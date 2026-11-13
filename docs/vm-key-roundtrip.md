# key 30 离线读写演示（R4 落地，路 AB/sim-keydemo）

函数级仿真验证「自定义 key 的离线读写」：在 probe_pi32v2 的 tone-scene 同款 harness 里，**直调真实的 get_config/set_config 分派器**（`0x06000f88`/`0x06001f12`），让 VM 后端（match `0x06061304`/get `0x06060ea0`/set `0x06060efe`）自己跑完整条读写链路。opt-in 场景 `--vm-key-scene`，默认关，legacy 报告逐字节不变（已对照）。

一句话结论：**空白 VM 区上 vm_init 真实格式化 → set_config(30, 8B) 真实走完「分配/读旧值/CRC/写窗口/更新偏移表/维护位图/提交 key-255 头」全链 → 同机 get_config(30) 读回逐字节一致；「重启世界」（新机器 + 真实 vm_init 重扫记录链）里 get_config(16)/get_config(30) 都把写入值原样读回——运行时持久化的固件侧读写面成立。flash 写侧（SFC 擦写）在「半区写满触发磨损均衡换区」时真实推进到 0x12cc40 RAM 函数体，诚实停在第一个未建模的 SFC/PMU 寄存器读（0xf2c128+4）。**

> 基线 main `1975ff8`（分支 sim-keydemo）。运行：`python3 tools/probe_pi32v2.py --vm-key-scene [--official-ufw …]`。所有结论为 conditional pass / derived-not-measured，非硅测量；不刷机、不连真机。

---

## 1. 场景设计（三个世界）

| 世界 | VM 区窗口内容 | 做什么 | 断言 |
| --- | --- | --- | --- |
| A 空白 | 0x2000 字节 0xFF（擦除态 fixture） | 直调 vm_init `0x06060f9a` → get(16)（期望空）→ set(16,[1,1,2],3) → set(30,8B) → get(16)/get(30) → CRC 破坏负例 → len 超限负例 | init 返回 0、半区描述字 {0x1da000,0x1000}/{0x1db000,0x1000}、magic `5a5a5aff`、记录落点、同机读回==写入、负例返回 -252/-254 |
| B 重启 | 世界 A 真实执行产生的窗口字节（derived-by-real-firmware-execution） | 新机器重新直调 vm_init（真实解析记录链）→ get(16)/get(30) | init 走记录链+CRC 校验+偏移表重建；**16/30 都原样读回且本机从未写过** |
| C SFC 边界 | 空白 | set(31, 0xFCC B) 把半区 0 恰好填满 → 再 set(30,1) | 真实磨损均衡换区（活记录拷入半区 1、key-255 头搬移、magic/选择字节写）→ flash_sync `0x0600a70a` → RAM `0x12cc40` 真实执行 → 停在 0x12cc48 读 `0xf2c128+4`（未建模 PMU/SFC MMIO） |

对照组按任务要求钉在**已占用 key 16（降噪模式 3B）**：世界 B 里 get_config(16) 返回 3 字节 `01 01 02`（ANC 默认 mode/scene/strength），本机没有任何写操作——证明 harness 真实在跑 VM 后端的「boot 重扫→按 key 取记录→CRC 校验→拷出」而不是空转。

b79 与 official 双镜像结果完全一致（窗口字节、停点 0x12cc48、全部断言）。

## 2. 运行中钉死的新事实（此前 CR/K 未钉或钉错）

1. **VM 记录格式**：`{u8 crc, u8 key, u16le (len<<4 | 低4位标志)} + data[len]`。crc 是 **data 的软件 CRC16 低位字节**（`0x6000f0c`→软件实现 `0x6000ed6`，查表于 0x60A85CC；读侧 `0x601045a` 校验「crc(data)&0xFF == 记录首字节」，不符返回 -252）。实测样例：key-30 记录 `a4 1e 80 00 | 57 30 de ad be ef 21 00`。
2. **半区头**：vm_init 格式化空白半区时写 4 字节 magic `5a 5a 5a ff`（=u32 LE 0xFF5A5A5A ✓ 与 CR 钉死值一致），记录紧跟在 magic 后（+4）。
3. **key→偏移表**：`0x129088` 起 256×u16，存「记录在半区内的偏移」（写核心尾 `0x60106ee` 更新；读侧据此定位记录）。半区描述字在 `0x129070`/`0x12907c`（各 12B 条目 {base, size}，base 存 flash 地址）。追加位置 `0x1292a0`，半区选择位=b[0x1292A6] bit1，有效标志 b[0x1272dc]。**VM 位图实为 `0x1272BC` 起 8 个字**（=0x1265C0+3324，[0x1272BC,0x1272DC) 恰好在有效标志前结束；CR 文档的 0x1272ac 偏低 0x10）。
4. **key-255 = VM 缓存状态快照（40B）**：每次 set_config 后递归 vm_set(255, 0x1272B8, 40) 提交；内容含 sfc 窗口版本字（本次实测 `a7 89`=公开镜像 flash 头 2 字节）与位图快照。重启时 vm_init 从该记录恢复缓存状态。
5. **窗口界字是隐形门**：写路径 `0x6007cf4` 用 `[0x127694]`（写）/`[0x127698]`（读）做范围检查，界字=boot 期 SET 路径按 `pow2(窗口描述字节-16)<<16` 算出（2MiB flash → 0x200000）。**界字为 0 时所有窗口写静默失败（错误码 66）而调用方仍报成功——幻影写陷阱**，本场景钉住该行为并给出界字 fixture。
6. **写核心的「同值短路」**：写前先分配缓冲读旧值（`0x601045a`），旧值存在且与新值全等时跳过写入直接返回成功（`0x1fd7c` 比较调用）。本场景 key 30 为新 key，该路径不触发。
7. **len≤4095 / key≤255** 由 vm_set 自身校验（越限返回 -254），负例实测 set_config(31,len=4096)→-254。

## 3. 诚实边界（不吹的部分）

- **flash 持久化本身未仿真**：VM 区窗口是 harness RAM（经真实直映换算映到 0x061da000..0x061dc000，可写）。SFC 擦/写硬件路径不建模——世界 C 里真实代码推进到 `0x12cc40`（RAM 驻留 SFC 函数）后**诚实停**在第一个未建模 MMIO 读（`0xf2c128+4`，PMU/SFC 族），擦写时序、掉电窗口、双区切换的 flash 侧效果全部不声称。
- **「重启」= 新机器 + 真实 vm_init 重扫记录链**（解析+CRC+偏移表重建+key-255 头恢复），不是掉电周期仿真。持久化语义成立的前提是「窗口内容会落到 flash」——这正是上一条不建模的部分。
- **OS 单任务桩**：分配器（bump）、互斥锁 0x6064538/0x606474a、VM 锁 0x12b55c/0x12b5b0、RTOS 查询 0x12cb08（返回 1 恰好让 CRC 走纯软件实现——记录 CRC 是真实计算代码）。ROM memcpy/memset 族与 RAM 字节端口写 0x12be48 为字节拷贝桩（真实体走 ICU 临界区读 0xffe8c0，未建模；ABI 反汇编钉死：读本地字节推入窗口）。
- **具名 fixture**（全部 derived-not-measured，见场景 `fixtures` 键）：VM 配置块 0x1276a4 {align=0x10, saddr=0x1da000, size=0x4000}（路 S 实测字段）；窗口界字 0x200000（2MiB flash 按固件 pow2 公式推导）；直映换算全局量 [0x1276B4]=0/[0x1276B8]=0x06000000（boot 直映态，路 S「固件大量直访 0x06000000 区」）；公开镜像 flash 头 4 字节（真实公开字节）；擦除态 VM 区（无公开工件携带设备 VM 字节，路 S erased_bankb 先例）。
- 结论等级：conditional pass——函数级执行证据，非整机 boot、非硅测量。

## 4. App 侧对接路径（「App 写 key 30 → 固件读 key 30」最小方案，只设计不实施)

前置结论（路 K）：key 30 在 1..99 的 54 个空 key 里（27..36 整段空），新 key **只需约定 key 号+字节格式，无需注册**（VM match `key-1<256` 全命中、set 仅校验 len≤4095）。

**App 侧**（复用 android-query 既有资产，路 CS/P/Z 已备齐）：
1. 仿 0x17/0x22 注册一条**新自定义命令号**（RCSP 帧 `FE DC BA|C0|FF|cmdid|len_BE16|body|EF`，经 A002/1 写、MTU247/244B 分片、帧级+片级串行——SerialBleTransport 骨架已单测）。body 形状自定（建议 `key(1B) + len(1B) + data` 或直接固定 key 30 的专用形状）。
2. `EqWritePolicy` 白名单加该 cmdid 的形状校验（armed 门控不变，不开任意字节写）。

**固件侧**（将来实施，走 R2 L1 同款「追加段+接驳」模式）：
1. 用 pi32v2 汇编器（路 G）把一个 setter 载荷汇编进追加段（照 mic_effect 先例，含 fence 扩界+JLFS/CRC 链）；
2. 载荷体内直调 `set_config(30, buf, len)`（`0x06001f12`，本场景已验证该入口在 VM 后端上完整可用）；开机加载照 key 16 模式：`get_config(30,…)` + 「读回长度≠期望→落默认」回退；
3. 接驳点候选：RCSP 命令注册表（`[0x060a9b78,0x060a9d1c)` 15 项×28B）追加一条或复用空位（该表在 app 数据区，追加段方案需评估改表 vs 载荷内自查命令号）。

**本场景贡献的证据**：固件侧「写 30→读回 30→重启重扫仍在」的读写面已经离线跑通；剩下的是命令接驳（R3 传输骨架已就绪）与真正的 flash 落盘（未建模，见 §3）。

## 5. 复现

```
python3 tools/fetch_firmware.py --variant current
python3 tools/probe_pi32v2.py --vm-key-scene            # b79
python3 tools/probe_pi32v2.py --vm-key-scene \
    --official-ufw firmware/QCY_H3S_7018F_1T2_release_note_V1.1.8_011.ufw
```

相关键址速查：get/set 分派器 `0x06000f88`/`0x06001f12`；vm_init `0x06060f9a`；写核心 `0x06010534`；记录读取 `0x601045a`；flash_sync `0x0600a70a`→RAM `0x12cc40`；窗口拷贝原语 读`0x6007ce4`/写`0x6007cf4`。
