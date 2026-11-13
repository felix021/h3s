# R4 App 命令接驳：keystore_hook 载荷 + 注册表空位接驳（路 AQ / sim-r4hook）

2026-11-14。R4「App 写配置 + 固件读配置」的固件侧实施：把一条 App 自定义命令接到
VM `get/set_config` 分派器上（key 30，8 字节值），做成离线候选镜像
`--r4-keyhook`（隐含 `--hearing-aid-l1-slotram`，仍与 EQ 修复候选互斥——同一追加点）。
**不刷机、不连真机；候选 UNTESTED；EQ 修复候选 67ba97ef 状态不变。**

基线 720dec5（worktree sim-r4hook）。前置：docs/vm-key-roundtrip.md（路 AB：key 30
读写往返全链离线实证）、docs/hearing-aid-l1-payload.md（R2 追加段/fence/CRC 链模式）、
docs/key-action-map.md + docs/cmd-0x17-0x1d-layout.md（注册表与分发器）、
docs/app-protocol.md + tools/android-query/（R3 组帧/白名单/bridge/Loopback 资产）。

证据强度沿用：**[静态]** = 本路反汇编钉死；**[镜像]** = 公开镜像字节直接读出；
**[文档]** = 既有 docs/HANDOFF 钉死条目；**[仿真]** = 路 AB 函数级仿真实证；
**[推断]** = 上述链推出；**[未钉]** = 无证据、如实标注。

## 0. 一句话结论

固件侧 setter 载荷 **27 条指令 / 78 字节、零重定位**（tools/r4cmd/keystore_hook.S），
按分发器写路径 ABI `(resp_buf, 128, data, len) → n` 接收命令，`data_len==9 && data[0]==30`
走 `set_config(30, data+1, 8)`、`data_len==1 && data[0]==30` 走 `get_config(30, resp+2, 8)`，
其它形状静默不回——读写在同一 handler 内按长度分路；接驳 = 把命令注册表 cmdid **0x30**
项的**空 setter 字**（0x060a9c7c，原值 0）填为载荷地址（**4 字节数据窗口**，写路径今日
为 no-op，纯增量；T/Y 的 8B 指令窗口替换不适用于数据表——等价安全检查 = 原字节为零 +
全 app 引用扫描零命中 + 消费面钉死）。候选 sha256
`8e9a47d0ec89982bd6f35a616d0e1d310562c1509d761746822e787bf5dcbf5d`，与官方镜像逐字节差异
**554 字节**、与 slotram 候选（906184b2）差异**恰 111 字节**（分类账见 §4）。App 侧
KeyStoreFrame/EqWritePolicy/H3SBridge.setHearingCfg/getHearingCfg + Loopback 端到端
单测全过。**未在真机/仿真跑过命令分发链，不构成「App 可读写 key 30」的运行验证声明。**

## 1. 注册表全案与空位调研（任务第 3 项）

### 1.1 两张表（本路重扫钉死，修正/细化既有文档）

官方 app.bin（sha 18f3d573…）在 0x060a9b24..0x060a9d1c 有**两张相邻但独立**的命令表：

**表 A：RCSP 自定义命令分发表** `[0x060a9b78, 0x060a9d1c)`，15 项 × **28B**，布局
`{+0 init, +4 getter, +8 setter, +20 恢复默认, +24 cmdid}` [文档:路 P + 本路复walk]。
消费者三处，全部只按表基/表尾走表（无逐项静态引用）：

| 消费者 | 走法 | 读哪个字段 |
|---|---|---|
| 分发器 **0x0600f85e**（opcode 0xFF 命令体） | 写：`body[0]=cmdid` 匹配 +24 → **0x0600f8b4 取 +8 setter，零值静默退出** → 异步投递尝试（0x0600f8b8..f92e，trampoline **0x060256aa** 以同一四参 `(resp,128,data,len)` 调起 [静态]）或同步 0x0600f932-0x0600f93e `call setter`；读：`FE 01 cmdid` 匹配 +24 → **0x0600f8a6 取 +4 getter**，`getter(buf+2, 126)`；响应包装 0x0600f946 `resp[0]=cmdid, resp[1]=n` | +8（写）/ +4（读） |
| 恢复默认 walker **0x0600f836** | [基, 尾) 步长 28，调 [+20] | +20 |
| 开机 init walker **0x06024df4** | 先表 C 后表 A，调 [+0] | +0 |

**表 C：批量同步表** `[0x060a9b24, 0x060a9b78)`，3 项 × 28B，布局
`{+0 cmdid, +4 装载, +8 读, +12 写, +24 恢复默认}`（**cmdid 在 +0，与表 A 的 +24 相反**）。
消费者：批量同步分发器 0x0600fae0（按命令位图 `1<<b[项+0]` 匹配后调 +8/+12）[静态]、
恢复 walker 0x0600f816（[基, 尾) 调 [+24]）、开机 walker 0x06024df4（调 [+4]）。

**对路 AO「cmdid 1 组」的修正 [静态，机器复核]**：key-action-map §3.1 把按键重映射
四件套 {0x060254ce 装载 / 0x060254f2 读 / 0x06025564 写 / 0x0602556c 恢复} 记为
「注册表 cmdid 1 组」。本路用三个 walker 的机器走表证据钉死：该四件套在**表 C 首项
0x060a9b24，其 cmdid 字节（+0）= 2**；「1」是**下一项（0x060a9b40）的 +0 字**，在
+24 当 cmdid 的错位读法里被误当成上一项的 cmdid——而 +24 实际是恢复默认**函数指针**
0x0602556c（walker 0x0600f816 调的就是它）。handler 四件套本身与 AO 钉死一致，仅
cmdid 归属修正为 **2（批量同步域）**。表 A 的 cmdid 1 项是另一个东西：setter
0x06026090 = `call 0x0600f856`（= 依次跑表 C/表 A 恢复 walker 的**恢复出厂**命令）。
AO 的负面结论不受影响：表 A/表 C 全部 cmdid 里**没有 0x2b（43）**——app-protocol §2.5
候选 B 的固件侧对应仍不存在，按键重映射 wire 定案仍待真机抓包（ADV type2 vs 批量
同步 2）。

### 1.2 表 A 十五项占用与「空位」结论

| cmdid | init | getter | setter | restore | 面板/文档出现 |
|---|---|---|---|---|---|
| 0x22 | 0x06025774 | 0x0602580a | 0x0602583c | 0x060258ea | EQ（App setEQWtihQFG2） |
| 0x14 | 0 | 0x0602597c | 0x06025a2a | 0x06025b40 | 面板（定时关机查询族） |
| 0x1f | 0x06025b5e | 0x06025b7e | 0x06025b98 | 0x06025b48 | 面板 |
| 0x2d | 0 | 0x06025c12 | 0x06025c26 | 0x06025c34 | 无（写 data[0] 单字节旗标） |
| 0x17 | 0x0600dddc | 0x06025c84 | 0x06025ca4 | 0x06025d32 | ANC（App setANCSenseMode） |
| 0x1d | 0x06025e2c | 0x06025e68 | 0x06025e82 | 0x06025eb0 | 提示音音量（3/6/8） |
| 0x19 | 0 | 0x06025ed8 | 0x06025f00 | 0x06025f7a | 面板 |
| 0x23 | 0 | 0x06025f8c | 0x06025fa0 | 0 | 面板 |
| **0x2f** | 0 | 0x06026004 | **0（空）** | 0 | **电量查询（protocol.md 实测）** |
| **0x30** | 0 | 0x06026060 | **0（空）** | 0 | 无（0x06007f0a 族查询） |
| 0x01 | 0 | **0（空）** | 0x06026090 | 0 | 恢复出厂（walker 驱动） |
| 0x03 | 0 | **0（空）** | 0x0602609a | 0 | 无（0x060232e6 族单值写） |
| 0x2e | 0x060260f0 | 0x06026120 | 0x06026134 | 0x06026176 | 面板（模式切换回执族） |
| 0x10 | 0x06026210 | 0x0602621e | 0x06026234 | 0x06026244 | 睡眠查询族 |
| 0x24 | 0 | 0x06026252 | 0x06026268 | 0 | 面板 |

**空位结论 [静态]**：

1. **没有任何 cmdid 空项**——15 项全部有 cmdid；表尾 0x060a9d1c 之后紧跟活字节
   （指令样式的数据），**扩表不可行**（需改 3 处表尾立即数 0x0600f874/0x0600f83e/
   0x06024e18 还要吃掉后续数据）。
2. **空的是四个函数指针槽**：0x2f/0x30 的 setter（写路径分发器对零值是「静默退出
   返回 -1」，不是错误——0x0600f8b6 `if (r0==0) goto 出口`）；0x01/0x03 的 getter
   （读路径 0x0600f8a8 同样零值跳过）。
3. **新命令号选择 = 复用 cmdid 0x30 的空 setter 槽**，理由：
   - 0x2f 是**电量查询**（protocol.md §2.6 实测在用，android-query QueryPolicy 白名单
     里有它）——动它的槽位风险高；
   - 0x30 面板无条目、协议笔记无记录、getter 是 0x06007f0a 族查询（充电/无线参数类
     [推断]），**写路径从未存在**——填 setter 是纯增量，读路径原样保留；
   - 0x01/0x03 的 getter 槽与其活 setter（恢复出厂/单值写）语义混搭，且 getter ABI
     （`(buf+2, 126)`）拿不到命令帧 data，做不了「写 key 30」的对称面。
   - 与 App 侧的约定（本路钉死）：**写 = `FF/0x30 data=[30, v0..v7]`（len 9）；读 =
     `FF/0x30 data=[30]`（len 1，同为写形态帧——载荷按 data_len 分路；`FE 01 30`
     真·只读查询走原 getter 0x06026060，不经载荷）**。
4. **接驳窗口类型**：注册表项是**数据**不是代码——T/Y 的「8B 指令窗整窗替换 + 分支
   落点扫描」不适用。等价安全检查本路全做 [静态]：①槽原字节恰为 `00 00 00 00`
   [镜像]；②全 app `li.u32` 立即数扫描 + 整个 decrypted flash 的裸 LE32 指针字扫描，
   **没有任何引用指向 0x060a9c74 项内**（唯一到达方式 = 三 walker 的表基遍历）；
   ③消费面钉死：读 +4、写 +8、恢复 +20、开机 +0——本路只写 +8。

## 2. 固件侧载荷：tools/r4cmd/keystore_hook.S

```
r4_keystore_hook(resp_buf=r0, cap=r1, data=r2, len=r3) → r0 = n（回执字节数）
    [--sp] = {rets, r6-r4}          ; r7=分发器的活表项指针（0x0600f946 调用后还读 b[r7+24]），不碰
    r4 = r0                         ; 保存 resp 指针
    if (r3 == 9) goto write         ; 写形状：key(1) + 值(8)
    if (r3 == 1) goto read          ; 读形状：key(1)
    goto silent                     ; 其它形状：返回 0（分发器 ifs(n<1) 跳过响应，
                                    ;   与 0x1d setter 对 len<2 同一行为）
write: r5 = b[data+0]; if (r5 != 30) goto silent
    r6 = 0x06001f12; r0 = 30; r1 = data+1; r2 = 8; call r6   ; set_config(30, data+1, 8)
    r5 = 0; if (r0 == 0) goto w_ok; r5 = 1                    ; status = 错误码归一为 0/1
w_ok: [r4+0] = r5                   ; 字存储把 status 落在 resp[2]（resp[0..1] 分发器覆写，
                                    ;   resp[3] 残留在 n+2 之外不会被发送）
    r0 = 1; goto ret                ; 回执 `30 01 status`
read:  r5 = b[data+0]; if (r5 != 30) goto silent
    r6 = 0x06000f88; r0 = 30; r1 = resp+2; r2 = 8; call r6    ; get_config(30, resp+2, 8)
    if (r0 != 8) goto silent        ; 无记录/长度不符：静默不回
    r0 = 8; goto ret                ; 回执 `30 08 v0..v7`
silent: r0 = 0
ret: {pc, r6-r4} = [sp++]
```

**27 insns / 78B / 零重定位**；仅有的绝对地址是两个 API 锚点（`li.u32 r6` + `call r6`
惯用法），段基址无关（单测钉死重汇编确定性 + 无其它宽常量）。依据表：

| 元素 | 值 | 依据 | 强度 |
|---|---|---|---|
| setter ABI `(resp,128,data,len)→n` | 分发器 0x0600f932-0x0600f93e | 路 P 反汇编 + 本路复walk + trampoline 0x060256aa 四参展开 | 静态+文档 |
| set_config / get_config 入口 | 0x06001f12 / 0x06000f88，`(key,buf,len)` | 路 AB 函数级仿真直调全链（写→同机读回→重启重扫仍在） | 仿真+文档 |
| key 30 无需注册 | VM match `key-1<256` 全命中，set 仅校验 len≤4095 | 路 K/AB | 文档 |
| 值长度 8B | 路 AB 往返实证长度（记录 `a4 1e 80 00|57 30 de ad be ef 21 00`） | 仿真+文档 |
| resp 写在 +2 | 分发器 0x0600f946-0x0600f954 调用后覆写 resp[0]=cmdid、resp[1]=n | 静态 |
| 源缓冲 data+1 直接传 set_config | 既有 setter 同款同步读（0x22 memcpy src=r2、0x1d/0x2d 读 b[r2]）；帧缓冲在（同步或异步 trampoline）调用期间有效是既有命令族的同一契约 | 静态+推断 |
| 静默不回 n=0 | 0x0600f942 `ifs (r0<1) goto` 跳过响应；0x1d len<2 同款 | 静态 |

## 3. 镜像实施：离线候选 `--r4-keyhook`

`build_quiet_candidate.py` 新开关 **`--r4-keyhook`**（默认关；隐含
`--hearing-aid-l1-slotram` 全链，与 EQ 修复候选互斥——同一追加点）。实施在
`hearing_aid_l1_image.py` 扩展（assemble_segment/apply_l1_patch/verify_l1_image 三处
加 r4_keyhook 参数），载荷源 `tools/r4cmd/keystore_hook.S` 单独可汇编。

### 3.1 段几何

| 项 | 值 |
|---|---|
| 组合段 | 442B = L1 slotram 段 364B（逐字节不变，构建时断言）+ keyhook 78B |
| r4_keystore_hook 入口 | **0x060E31E0**（= slotram 段末，偏移 364） |
| 追加文件 mic_effect.bin | 475B（头 32 + 0xff + 代码 442），flash [0xE5073, 0xE524E)，boot-info app_size 0xE524E |
| fence 上限 | 0x060E31E0（slotram 值）→ **0x060E322E**（新文件末端运行地址）；下限/使能位不动 |
| 注册表窗口 | **0x060a9c7c 4B：`00 00 00 00` → `e0 31 0e 06`**（LE 0x060E31E0）；项基 0x060a9c74、cmdid 字节 0x30 构建时断言 |

### 3.2 验证清单（全部通过 [实测]）

①载荷 27 条指令逐条 encode(decode)==原字节零 diff（独立单测 + 构建时逐条断言）；
②组合段前 364B == slotram 候选段逐字节（L1 部分零改动的机器证明）；③keyhook 尾 ==
独立汇编结果；④载荷指令形状逐条 pin（R4_INSN_IDS 表）+ 两 API 锚点各恰一次 +
`call r6` 前三条指令必是对应装载；⑤注册表槽原字节断言 + 最终镜像明文反解出
`LE32(0x060E31E0)` + cmdid 字节仍 0x30（verify_l1_image 新断言）；⑥CRC 链照 §9.3
全套重算，独立校验器 verify_ufw_integrity.py 全过（475B 条目 JLFS CRC + 双备份）；
⑦legacy：`--hearing-aid-l1-patch` 6c7247a5 / `--hearing-aid-l1-close` 5a5f2ef7 /
`--hearing-aid-l1-slotram` 906184b2 三候选本路重建逐字节一致；⑧`check_local --units`
exit 0（含新单测 11 tests）；android-query test.sh 8 套全过（含新 KeyStoreFrameTest）。

### 3.3 字节账

**vs 官方镜像：554 字节**（slotram 候选 472 + 82：注册表槽 4 + keyhook 密文 78）。
**vs slotram 候选（906184b2）：恰 111 字节**，分类：

| 区域 | 字节数 |
|---|---|
| UFW 外层（头 CRC 2 + 条目表 CRC 2 + flash 条目 CRC 2） | 6 |
| boot-info 32B 编码区（app_size 2 + 头/明文/密文 CRC） | 8 |
| app_area_head + app.bin 双层 JLFS CRC | 8 |
| config.dat 头 CRC（末文件标志位在 slotram 候选已清，不再差） | 2 |
| **注册表 setter 槽 0x060a9c7c** | **4** |
| 追加文件头（头 CRC 2 + 数据 CRC 2 + block_size 1B） | 5 |
| **keyhook 代码密文** | **78** |

全部差异逐字节分类断言（verify_l1_image 全镜像精确差异 + 手工复核差异区间清单），
无一例外字节。

## 4. App 侧（轻量：组帧 + 白名单 + bridge + Loopback）

- **KeyStoreFrame.java**（照 KeyRemapFrame 模式）：`buildWriteCommand(sn, byte[8])`
  → `FF/0x30 | 09 | 1E v0..v7`；`buildReadCommand(sn)` → `FF/0x30 | 01 | 1E`（同为
  写形态帧，见 §1.2 约定）；严格反解析（parseWriteCommand/parseReadCommand/
  parseWriteAck `30 01 status`/parseReadPayload `30 08 v…`）。
- **EqWritePolicy** 白名单加 cmdid 0x30 两形状（`len==9&&data[0]==30` /
  `len==1&&data[0]==30`；值 8 字节不透明——只钉形状不开任意写）；最小 body 门槛从
  n≥5 降到 n≥4（0x30 读形状 4B，其它 cmdid 的最小形状校验不受影响）。
- **H3SBridge**：`setHearingCfg(valueHex16)` / `getHearingCfg()`（armed 门控、SN 自
  管理、响应经 ResultSink 事件按 SN 匹配回推，payloadHex 由 KeyStoreFrame 解析）。
- **LoopbackTransport**：设备桩按**固件载荷的设计回执**应答 cmdid 0x30（写→`30 01 00`，
  读→`30 08 桩值`，桩值可设）——不是回显、也不是实测设备帧（写路径回执形状路 P 已标
  [未钉]，真机联调以实机为准）。
- **测试**：`tests/KeyStoreFrameTest.java`——组帧/反解析往返、文档字节对照、边界拒绝、
  回执解析、EqWritePolicy 放行/拒绝（含 0x17 回归）、**Loopback 端到端**（armed 写 →
  设备桩逐字节收帧 → `30 01 00` 回执 SN 匹配；读 → `30 08` 桩值解出 8 字节；未武装/
  非 hex/长度错拒绝），接入 test.sh（8 套全过）。

真机下发未验证（红线不连真机）；固件候选 UNTESTED 不刷机——App 侧生效前提 =
`--r4-keyhook` 候选真机刷入并验证（本仓库不做）。

## 5. 诚实边界（必须随候选一起读）

1. **离线候选 UNTESTED，不刷机**。交付=「载荷字节与 §1/§2 证据一致 + 四层 CRC/JLFS
   结构自洽 + 逐字节差异精确可控 + App 侧组帧/白名单/回环链路可跑」。
2. **命令分发链零执行证据**：载荷到分发器的完整路径（BLE → RCSP → 0x0600f85e →
   异步 trampoline/同步 call → set/get_config → VM 后端）未在任何仿真/真机跑过——
   主仿真前沿未推进到 RCSP/任务派发相位；路 AB 只实证了「直调 set/get_config 分派器
   的读写往返」，没实证「命令帧到达这个 handler」。
3. **异步路径的缓冲生命期**：分发器会先尝试把 setter 投递给命名任务（0x060A1D11 串）
   延迟调起；帧缓冲在延迟调用期间仍有效是既有命令族（0x17/0x22/0x1d）共用的同一
   契约，本载荷没有新增风险面，但也没有独立证据 [推断]。
4. **cmdid 0x30 占用的残余风险**：QCY App 今日不发 0x30 写（面板无条目、协议笔记无
   记录 [静态]），但若未来 QCY App 版本给 0x30 加写功能，会与本命令冲突（表现为
   QCY App 写 0x30 变成写 key 30）。0x30 的 getter 语义（0x06007f0a 族查询）本路
   只做了浅层反汇编，命名是 [推断]。
5. **「App 侧」语义**：android-query 是查询/实验客户端（包名 cn.felix021.h3squery），
   「App 写 key 30」指该客户端经 A002/1 的 RCSP 自定义命令通道；QCY 官方 App 不认识
   cmdid 0x30 的这个用法。
6. 表 C「批量同步域 cmdid 2=按键重映射」的修正是静态结论（三个 walker 机器走表 +
   批量分发器位图匹配反汇编），App 实发 wire（ADV type2 vs 批量同步 2）仍 [未钉]
   待真机抓包——不动既有「不连真机」红线。
7. 与 EQ 修复候选互斥（同一追加点）；EQ 候选 67ba97ef UNTESTED 不变。

## 6. 证据锚点表（本路新增钉死）

- 表 A：[0x060a9b78, 0x060a9d1c) 15×28B {+0 init,+4 get,+8 set,+20 restore,+24 cmdid}；
  分发器 0x0600f85e（写匹配 0x0600f88a→0x0600f8b4 取 +8、零值静默 0x0600f8b6；异步
  投递 0x0600f8b8..0x0600f92e，trampoline 0x060256aa = `call r5([r2],[r2+4],[r2+8],
  [r2+12])`；同步 0x0600f932-0x0600f93e `setter(resp,128,data,len)`；读 0x0600f89c-
  0x0600f8b0 `getter(buf+2,126)`；响应包装 0x0600f946）；恢复 walker 0x0600f836(+20)、
  开机 walker 0x06024df4(+0)。
- 表 C：[0x060a9b24, 0x060a9b78) 3×28B {+0 cmdid,+4 装载,+8 读,+12 写,+24 恢复}；
  批量同步分发器 0x0600fae0（位图 `1<<b[项+0]`，0x0600fbf0-fbfc）；cmdid 2=按键重映射
  {0x060254ce/0x060254f2/0x06025564/0x0602556c}、cmdid 1={0,0x0602557a,0x060255e4,
  0x06025626}（32B 字串配置 [推断]）、cmdid 12={0x0601194e,0x060260a8,0x060260bc,
  0x060260dc}；表 A cmdid 1 setter 0x06026090=恢复出厂（call 0x0600f856=双表恢复
  walker）。
- 表 A 空槽全集：setter 空 = 0x2f/0x30 项；getter 空 = 0x01/0x03 项；表尾后 0x060a9d1c
  起为活数据（扩表不可行）。0x060a9c74 项（cmdid 0x30）全 app li.u32 + 裸 LE32 引用
  扫描零命中。
- 载荷：tools/r4cmd/keystore_hook.S（27 insns/78B/0 relocs）；单测
  tools/test_r4_keyhook_payload.py（6 tests）+ tools/test_r4_keyhook_image.py
  （5 tests）。
- 候选：sha256 `8e9a47d0…`（554B vs 官方；111B vs slotram）；构建器开关
  `--r4-keyhook`；注册表窗口 0x060a9c7c `00000000`→`e0310e06`；fence 0x060E322E。
- App 侧：KeyStoreFrame.java（cmdid 0x30/key 30/8B 值）、EqWritePolicy（0x30 两形状）、
  H3SBridge.setHearingCfg/getHearingCfg、LoopbackTransport.keyStoreValue +
  buildDeviceResponse；tests/KeyStoreFrameTest.java。

## 7. 复现

```
python3 tools/fetch_firmware.py --variant current
python3 tools/build_quiet_candidate.py --attenuation-db 0 --r4-keyhook --output-dir <空目录>
python3 tools/verify_ufw_integrity.py <产物>
python3 tools/test_r4_keyhook_payload.py && python3 tools/test_r4_keyhook_image.py
bash tools/android-query/test.sh
python3 tools/check_local.py --units
```
