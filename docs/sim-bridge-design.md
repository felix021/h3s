# SIMBRIDGE：AVD ↔ H3S 仿真环境联调 bridge 架构设计（路 SIMBRIDGE / sim-simbridge，只读设计）

2026-11-16 15:10-。基线 worktree `413202a`（分支 sim-simbridge）。本页服务 R3「独立 app 联调」
的仿真半边：**app（跑在 AVD）不连真耳机，经 bridge 连宿主机的 H3S 固件指令级仿真进程**，
跑通「app 命令 → 固件真实执行 → 回执 → app UI 更新」全闭环，以及 USB 侧的备份/恢复流。
任务原文：「avd 和仿真环境完成联调，构建必要的 bridge 完成蓝牙/usb 通信的模拟」。

**本路零 tools/ 生产改动**（设计定稿后另派实施路）；probe 五文件（analyze_image_trace/
br28_model/probe_boot/probe_irq_entry/frontier_registry）JLEQ 在途禁改，本设计全部方案
不要求触碰它们。证据强度沿用：**[静态]** = 反汇编钉死（引文档）；**[文档]** = 既有 docs/
HANDOFF 条目；**[仿真]** = 既有指令级仿真实证；**[设计]** = 本页新设计决策；**[未钉]** =
如实标注 + 归入 §5.0 预研清单/§6 风险表。

## 0. 一句话结论

app 侧新增两个 Link 实现（`SimulationGattLink` 实现 `AndroidBleTransport.Link`、
`SimulationUsbLink` 实现 `H3sUsbBridge.Link`——传输栈/业务/UI 零改动），经 **adb reverse**
（已实测，§4.5）的 localhost TCP 连到宿主机新文件 **bridge server**（`tools/sim_bridge_server.py`，
实施路新增）；server 在 `probe_boot.explore(..., post_stop=…)` 的停点后回调里跑 socket
命令循环（直调驱动器复刻路 S `key_chain_direct_call` 先例，外设时间冻结），把 app 的 RCSP
命令**直调到表 A 命令分发链的 setter/getter**，回执由 server 包装成 RCSP 响应帧回流。
**关键修正**：BLE 数据面的正确注入点**不是**任务假设的 0x6000cba/0x60252c6（那是 2 字载荷
的按键事件面），而是 RCSP 自定义命令分发器 **0x0600f85e + 表 A**（§3）。**同步闭环的现实
边界（codex 复核后修正）**：getter 链（如 ANC 读 `FE 01 17` → `17 03 …`）ABI 与回执形状
双钉、可同步全闭环；setter 链（0x17/0x1d）**恒返 0=写静默**（qcy-command17.asm:59 尾部
`r0 = 0`；0x1d 同）——真实固件的「写生效」走 **app_core 异步回调**（setter 内
post('app_core',0x300000,[回调 0x6025D6A,…])，qcy-command17.asm:34/59），park 世界的冻结
直调只完成「直存+投递」半程，全环要解冻续跑让事件泵消费（§5.1 分 M1-a/b/c 三级验收）。
USB 侧切在 `H3sUsbBridge.Link`，server 实现 MSC BOT 设备端对 FB06/FB08/FC0A/FD05（M2
只读）/FB01/FB04（M3 擦写，落本地镜像副本）——协议级仿真，非指令级（如实标注，§4.6）。

## 1. 需求与范围

| 项 | 内容 |
| --- | --- |
| 目标 | R3 app（android-query，WebShell WebView 壳）在 AVD 上连「仿真耳机」完成全链路联调 |
| BLE 面 | app 的 RCSP 命令（EQ 0x22 / ANC 0x17 / 提示音 0x1d / key30 0x30、只读查询）→ 仿真固件真实执行 → 回执帧 → app |
| USB 面 | app 的恢复/备份流（进 ROM → FB06 loader → FD05 读 / FB01+FB04 写）→ server 侧 flash 镜像文件 |
| 非目标 | BT modem/空口模型（BTMAP §4 方案 (b) 明确 R3 不需要）；BLE OTA E1-E8（列 M4 可选，§5.4）；真机 |

## 2. 总体架构（文字版）

```
┌─ AVD（仿真器内）──────────────┐      ┌─ 宿主机 ─────────────────────────────────────┐
│ android-query app (WebShell)  │      │                                              │
│  WebView UI (index.html)      │      │  bridge server（新文件 tools/sim_bridge_     │
│   ↓ h3s / h3susb JS bridge    │      │   server.py，实施路新增，probe 五文件零改动） │
│  H3SBridge / H3sUsbBridge     │      │   ┌────────────────────────────────────────┐ │
│   ↓ (业务/策略/UI 全复用)      │      │   │ socket 命令循环（JSON-lines）           │ │
│  AndroidBleTransport          │      │   │  ├ RCSP 解帧/组帧（FE DC BA…EF）        │ │
│   ├ Link = SimulationGattLink │ TCP  │   │  ├ 表 A 查表（镜像静态解析）            │ │
│  H3sUsbBridge.UsbScsi         │──────┼──▶│  ├ 直调驱动器（复刻 key_chain_direct_   │ │
│   └ Link = SimulationUsbLink  │adb   │   │  │  call：save/restore+哨兵 rets+受控步进)│ │
│  (127.0.0.1:7788)             │reverse│  │  └ USB MSC BOT 设备端（M2/M3，协议级）  │ │
└───────────────────────────────┘      │   └──────────────┬─────────────────────────┘ │
                                       │                  │ post_stop 回调（活机器）    │
                                       │  probe_boot.explore(...) 停点世界            │
                                       │   ├ 起跑：snapshot restore（~8s，M3FIX 实证）│
                                       │   │   或全量重放（~27min 量级，首轮用）      │
                                       │   ├ 直调：表 A getter（同步回执）/setter    │
                                       │   │  （直存+post，恒返 0=写静默）          │
                                       │   └ 解冻续跑：app_core 泵消费 post→apply 链 │
                                       └──────────────────────────────────────────────┘
```

数据面一句版（读路径，同步闭环）：app 写 RCSP 帧 → SimulationGattLink 分片走 socket →
server 重组帧 → 剥 SN 取命令体 → 查表 A 选 getter → 直调（RAM scratch 装参）→ 读返回
`n` 与载荷 → server 包 `status+SN+cmdid|n|payload` 成 RCSP 响应帧 → socket 回推 →
SimulationGattLink 注入 onCharacteristicChanged → SerialBleTransport.RcspDeframer 重组 →
H3SBridge 按 SN 匹配 → WebView UI 事件。写路径多一步异步：setter 直调只「直存+投递」，
生效与（若有）异步通知帧要 server 解冻续跑让 app_core 事件泵消费后才出现（§5.1-c）。

## 3. 关键修正：BLE 数据面注入点（对任务假设）

任务 brief 假设「app 命令（RCSP 帧）→命令桥 0x6000cba post 0x10000C→表2[5] 0x60252c6」。
逐字核对既有钉死结论后**必须修正**，两处：

1. **0x6000cba 装不下 RCSP 帧**。指令级钉死（d1-s18-consumption.md）：`0x6000cba` 是
   `post('app_core', 0x10000C, 2 字载荷)`——`[--sp]=rets; sp-=8; d[sp]=r1_r0`，实参就两个
   字（如 S18 预置 `{18,0}`、模块切换 `(20,1)`、手势 `0x10a04` 一类 packed 字）[静态]；
   其消费者语义（表1/表2 handler、模块切换分派）全是 2 字事件字，不存在帧缓冲传递
   [静态：BTMAP2 §8.1 事件泵钉死]——EQ 0x22 命令体 3+7×10=73B 无承载面（判定依据=
   消费者语义而非单纯字数，codex 修正）。
2. **0x60252c6 是按键事件 handler，不是 RCSP 命令 handler**。它是 S19 表2[5]
   `{0xff,0x10000C,0x60252c6}` 的按键/命令事件消费端（bt-stack-map.md §1.1），消费的是
   0x6000cba 投递的 2 字事件（键/手势码）[静态+仿真：路 BW/AJ 双击载荷逐字段互证]。

**RCSP 自定义命令的真实固件分发面 = 分发器 0x0600f85e + 表 A**（r4-command-hook.md §1.1
+ cmd-0x17-0x1d-layout.md §1，路 P/AQ 双向互证）[静态]：

- 表 A `[0x060a9b78, 0x060a9d1c)`，15 项 × 28B `{+0 init, +4 getter, +8 setter,
  +20 恢复默认, +24 cmdid}`；EQ=0x22（setter `0x0602583c`）、ANC=0x17（setter
  `0x06025ca4`/getter `0x06025c84`）、提示音=0x1d（setter `0x06025e82`）、key30=0x30
  （r4 候选镜像才有 setter，官方槽为空）。
- 命令体两形态：写 `cmdid | data_len | data`（分发器 0x600f932 取 `r3=b[body+1]`、
  `r2=body+2` 调 `setter(r0=响应缓冲, r1=128, r2=data, r3=data_len)`）；读 `FE 01 cmdid`
  （0x600f89c 调 `getter(buf+2, 126)`）。
- 响应包装 0x600f946：返回 n≥1 后，分发器在缓冲 `[0]=cmdid、[1]=n`，即设备回执数据段 =
  `cmdid | n | payload`；n<1 静默不回（0x600f942）。RCSP 层再包成
  `FE DC BA 00 FF len | status SN cmdid n payload EF`（protocol.md:40 实测响应帧形；status=00、
  SN 回显请求序号）。**实测有回执的只有 getter 路径**（`FE 01 17` 查询 ↔ `17 03 …` 响应，
  protocol.md:39-40 同一 TX/RX 对）；**0x17/0x1d setter 恒返 0=写静默**（qcy-command17.asm:59、
  cmd-0x17-0x1d §2.1），写的「生效确认」不走同步回执——真机侧可观察的写后帧是**异步通知**
  （如按键切模式的 0x2E 回执族，candidate-image-cotest.md §2.1 W-ANC 侧）。「setter 收到的
  响应缓冲基址与载荷起点」（resp / resp+2）现有证据互相矛盾（cmd-0x17 §1 vs r4-command-hook
  §2/keystore_hook.S 写读两路自相矛盾）**[未钉]**——因已检 setter 全部恒返 0，该 ABI 从未被
  执行检验；归 P1 预研（0x22 setter 返回值一并钉）。
- BTMAP §1.1 的「rcsp 任务→0x6000cba」是 rcsp 域向 app_core 的**控制面**事件桥
  （静态零直接 caller、经函数指针注册），与数据面分发器并存不冲突——本修正不影响
  BTMAP 其余结论。

由此注入点分三层，**M1 取 T1**：

| 层 | 注入方式 | 固件真实执行段 | 前置 | 取舍 |
| --- | --- | --- | --- | --- |
| **T1（M1 默认）** | server 查表 A（镜像静态解析）→ **直调匹配的 setter/getter** | getter：读参数 RAM+组回执（同步全闭环，如 0x6025c84 读 3B 返回 3）。setter：前置门链+参数直存+**异步投递 post**（0x17 asm 钉死：0x6008170(==5)/0x6000c6c/0x60065bc/0x6018994 四门 → 存 `b[0x1265C0+171..173]` → post('app_core',0x300000,[0x6025D6A,…]) → **恒返 0**）；全环需解冻续跑（§5.1-c） | 调用参数与表布局有静态依据；**停点世界可调用性待验证**（路 AB 的 set/get_config 实证是 probe_pi32v2 函数级带桩 harness——分配器/互斥锁/VM 锁/RTOS 查询皆桩，非停点世界直调，vm-key-roundtrip.md §3 诚实边界；不能外推）→ P2 预研 | 表 walk/响应包装由 server 复刻（Python 侧 ~20 行）；cmdid 匹配逻辑等价、非执行；setter 撞墙如实报 BLOCKED |
| T2（升级） | 直调分发器 0x0600f85e 本体 | T1 + 表 walk + 响应包装 + 异步投递尝试 | 入口 ABI（body 指针所在寄存器/上下文对象）与尾部 TX 走向**[未钉]**——candidate-image-cotest.md §2.2 R4-COT-02 列了上下文对象依赖（含命名任务 0x60A1D11），但该处**没有钉死入口完整 ABI 或尾部 TX 去向**；不能引用它证明尾部必然投递 rcsp 任务 | 预研后启用；**T2 也不自动解决 setter 内部的异步工作**（codex 指出） |
| T3（远期） | 喂 rcsp 任务 RX（槽回调面） | 全链含 rcsp 解码 | 槽表写入面 [未钉]（BTMAP §8.5：闭源库内计算地址写入）+ rcsp 任务创建（0x100003 注入）+ LE glue | 不做，如实标注 |

- **标准 opcode（03/D4/C1/E1/E2）不走表 A**（rcsp 库域处理，路径未钉）：M1 由 server
  Python 层桩应答（03 设备信息按 protocol.md 实测属性形状、E1/E2 按 ota.md 形状），显式
  标注 synthetic-not-firmware。
- **0x6000cba 的正确用途**（M1 不需要）：若后续要测「app 事件」而非「app 命令」（如模拟
  手机侧触发的模式切换事件族），它是 2 字事件的现成注入面（BW 先例）；BT 模块切换
  0x10000C(20,1)（BTMAP2 §8.1）在 bridge 架构下**不需要**——bridge 绕过 BT 域，rcsp/
  btstack/btctrler 任务不创建（BTMAP §4 方案 (b)：R3 不需要 modem 模型）。

## 4. 组件设计

### 4.1 bridge server（宿主机，新文件 `tools/sim_bridge_server.py` [设计]）

**长驻化形态——最小改动路径**：不改 probe 五文件，新文件 `import probe_boot` 后调
`explore(..., post_stop=serve_loop)`。`post_stop(machine, hardware, stopped_cpu)` 在主循环
停点后、报告组装前被调（probe_boot.py:98-116 docstring、:2076-2078 调用点；路 S 先例
「在活机器上做停点后直调类探测」）——**serve_loop 内跑 socket accept/handle 循环即得长驻
交互模式**：①停点世界长驻（世界=实施时点最新已过线停点；首轮全量重放 ~27min 量级
（M3FIX 双镜像捕获轮实测），之后用 `snapshot={'restore': …}` 起跑 ~8s [文档：
snapshot-resume.md，M3FIX canonical 轮实测]——restore 要求相同静态开关/代码指纹/场景
上下文匹配，快照不能替代世界构造，probe_boot.py:1871）；②接受外部命令注入（socket
JSON-lines）；③直调段用受控步进循环继续执行；④状态变化经 `world_inspect`/`world_diff`
消息返回。**不需要新「交互模式」引擎**——explore 的 post_stop 通道 + 直调驱动器就是交互
模式。

**启动草图与世界构造面（codex 复核补——「零 probe 改动」的诚实成本）**：
`explore` 是世界构造链的最底层；前沿场景的开关集/fixture 住在 `probe_irq_entry.irq_case`
的参数里，而 `irq_case` **没有通用 post_stop 透传**（签名无此参，内部固定 `post_stop=None`，
probe_irq_entry.py:1331/:2660 附近，仅支持两个既有键链回调）。因此 server 文件必须**自己
重建目标场景的世界构造**：照 `irq_case` 对 `explore` 的调用形状（scenario flags、handoff、
machine_options、fixture 传参）在新文件里复刻一份并注入 `post_stop=serve_loop`。零 probe
文件改动**技术上成立，但构造链复刻是持续维护成本**（前沿场景演化时 server 侧要跟）——
接线方式在实施路与 JLEQ 合并后定稿，备选=给 probe 提通用透传参（需等禁区解除）。启动
草图：`module=加载 app/rom → 构造 scenario flags/handoff → explore(module, app, …,
snapshot={'restore': …}, post_stop=serve_loop)`；停点恢复后首条命令前校验目标 CPU 与
停点上下文（世界横幅里带 stop_pc/steps）。

```python
# 形状（实施路落地，本页定契约）
def serve_loop(machine, hardware, cpu):        # explore 的 post_stop 回调
    server = SimBridgeServer(machine, hardware, cpu,
                             flash_image_path=..., listen=('127.0.0.1', 7788))
    return server.run()                        # 阻塞式命令循环；返回 dict 并进最终报告

# 直调驱动器：复刻 probe_irq_entry.py:846-876 key_chain_direct_call（~30 行，公共 API：
# cpu.decode/step、machine.after_step、cpu.mem.read/write）——不 import 禁改文件里的
# 场景函数，独立实现同语义：save pc/sp/rets/flags/R0-R15 → 设参 → rets=哨兵
# 0x100000 → 受控步进到哨兵 → finally 恢复。不调 hardware.advance（外设时间冻结，
# 路 S 显式假设：直调段在无中断窗口内执行）。在本设计下按 codex 建议加两条纪律：
def direct_call(cpu, machine, pc, args, *, max_steps=1_000_000, watch=()):
    # ① 成功返回（到哨兵）时先校验 SP 与入口平衡、栈未越过边界，再恢复上下文；
    # ② 限定 CPU0、已知 SP、非 IRQ 上下文、无在途 repeat/after_then；
    # ③ Blocked/预算耗尽 → 世界标记不可继续（见下「失败恢复」），finally 只恢复寄存器。
    ...
```

**注入/观察 API（server 内部）**：
- `inject_rcsp_read(cmdid) -> bytes|None`：直调 getter（r0=buf scratch, r1=126），返回值
  n（有符号校验：1≤n≤126 才接受）→ 回执数据段 `cmdid|n|buf[0..n)`。
- `inject_rcsp_write(cmdid, data) -> (stored: bool, posted: bool, n: int)`：直调表 A 该
  cmdid 的 setter（r0=resp scratch, r1=128, r2=data scratch, r3=len）；0x17/0x1d 预期恒返
  0（写静默）——观察面转为 ①参数直存 RAM（如 0x12666B..D）②post 落队（app_core 队列
  RAM 写断言）；若 setter 意外返 n≥1（0x22 待 P1），回执数据段按 P1 钉死的基址语义组。
- `resume_steps(n)`（M1-c）：解冻续跑——退出直调的冻结纪律，按 explore 主循环同款
  （含 hardware.advance 的正常步进）跑 N 步，让 app_core 事件泵消费注入期 post 的事件；
  观察面=watch PC（apply 链）+ world_diff + （若固件产生）异步通知帧出队 → `ble_notify`。
- `wrap_response(status, sn, payload) -> frame`：组 `FE DC BA 00 FF len status SN payload EF`
  （protocol.md:40 实测响应帧形；LoopbackTransport.wrapResponseBody 同形先例）。
- `world_inspect(addr, len)`（白名单地址表）/ `world_diff()`：RAM 直读 / 相对基线的写集差异
  （验收断言用，基线=停点后首帧快照，照 A/B 双世界差异纪律 candidate-image-cotest.md §2.3）。
- **失败恢复（codex 建议采纳）**：直调 Blocked/预算耗尽后世界可能半写（memcpy 一半、
  锁未释放）——**首版策略=世界标记不可继续，下一条命令前快照 restore 重建再接**；只恢复
  GPR 会把污染隐藏。重建成本 ~8s（快照）可接受。
- **scratch 选址 [设计+守卫]**：命令体 data ≤73B（3B 头+7B×10 段，eq-cmd-0x22-layout.md
  §7）、响应缓冲 ≤130B。放停点世界 bootstrap 栈区的死区（0x123000..STACK_TOP 内、当前 sp
  之上未被活帧引用的窗口）或其它实施时验证的已映射可写 RAM；守卫=选址后跑一次空直调，
  `world_diff` 必须=∅（除 scratch 自身）+ 深调用栈探针（直调一个已知深栈函数验证不越界），
  否则换址。「空直调 diff=∅」只证无静态引用，不证无动态引用——实施路保留换址余地。不碰
  [0x1265C0+1004]（rcsp 缓冲池域，任务未创建时状态未初始化）。
- **单线程纪律**：machine 非线程安全，世界执行（直调/续跑）全在 post_stop 线程内串行；
  连接层允许多路 accept 但**同一时刻只允许一个活动客户端**（其余立刻回 busy 断开）——
  不能靠 app 侧单 transport 的串行队列推导 server 无并发问题（codex）。
- **生命周期**：serve_loop 不返回则最终报告不组装（预期行为：server 常驻就没有场景报告）；
  优雅退出（SIGINT/quit 消息）时清理 socket、返回摘要 dict 进报告；异常退出跳过报告收尾，
  由 CLI 侧日志兜底。

**CLI**：`python3 tools/sim_bridge_server.py --listen 127.0.0.1:7788
--snapshot <dir|bin>（或 --replay 全量参数）--flash-image <2MiB raw fixture>（M2/M3）
--scratch <addr>`。

### 4.2 socket 协议（JSON-lines [设计]）

一行一条 JSON（UTF-8，`\n` 结尾），二进制一律 base64（`data` 字段）。吞吐非瓶颈
（命令帧几十字节、10ms 片间隔主导；M2 全片**双读**备份 ≈5.6MB+JSON 文本，localhost
adb reverse 远超所需——精确口径见本节末③）。选 JSON 而非二进制帧：调试透明、可手工
telnet 试、扩展免版本协商。

| 方向 | 消息 | 语义 |
| --- | --- | --- |
| app→server | `{"t":"hello","proto":1,"client":"sim-gatt"}` | 会话与身份（sim-gatt/sim-usb）；**未知 proto 版本一律拒绝**（hello_err 后断开） |
| server→app | `{"t":"hello_ok","world":{"stop_pc":"0x…","steps":N}}` | 世界横幅（验收记录用） |
| app→server | `{"t":"ble_write","data":b64}` | 一个 GATT 片（Link.writeChunk 入队后由后台写线程发出）——**片不带 SN**，SN 由 server 从重组出的完整 RCSP 帧提取 |
| server→app | `{"t":"chunk_ack"}` | 片写回执（→ onCharacteristicWrite(0)）；**收到即 ACK，再进 handler**——不隐式延迟 ACK 掩盖慢 handler（codex） |
| server→app | `{"t":"ble_notify","data":b64}` | 设备→app 帧（→ onCharacteristicChanged；server 整帧回推，app 侧 RcspDeframer 兼容任意分片） |
| app→server | `{"t":"inspect","addr":"0x…","len":N}` | world_inspect 白名单代理（验收断言/调试） |
| server→app | `{"t":"error","code":"blocked"/"budget"/"world_dead","detail":…}` | 直调 Blocked/预算耗尽/世界已标记不可继续——**如实上报，不臆造回执**；app 侧映射为可见的链路错误事件（不是静默超时） |
| （M2/M3） | `{"t":"usb_bulk_out","data":b64}` / `{"t":"usb_bulk_in","data":b64}` / `{"t":"usb_ctl",…}` / `{"t":"usb_scan",…}` | USB 面（§4.4）；数据相位与 CSW **各自独立一条消息**、长度精确（UsbScsi.exchange 拒绝数据相位短读且下一次必须恰为 13B CSW，UsbScsi.java:127-136） |

**执行契约（codex 复核补）**：①每连接独立解帧状态（字节流累积式 RCSP 解帧器——支持
`FE DC BA` 头跨片拆分、按 BE16 长度+EF 尾校验、假头后移一位重同步——SerialBleTransport.
RcspDeframer.java:225-240 同语义的 Python 版）；断链清空状态；单帧上限（4KB）+ 累积缓存
上限（64KB）+ 未完成请求数上限（16）+ 日志量上限——超限断开报错，防资源失控。②重复
请求不自动重放（app 侧 SerialBleTransport 帧级串行已保证不重发；server 不猜）。③JSON
吞吐按**双读备份**口径估算：M2 全片备份 = H3sUsbBridge 双读（H3sUsbBridge.java:354-360）
= 2×4096 次 FD05，纯 base64 数据 ≈5.6MB+JSON 开销——localhost 充裕，但验收以双读备份
实测耗时为准，不凭带宽推断。

### 4.3 SimulationGattLink（app 侧，新文件 [设计]）

`implements AndroidBleTransport.Link`（tools/android-query/src/cn/felix021/h3squery/
AndroidBleTransport.java:79-121 的 11 方法接口——**BLE 唯一接触面**；「换实现、上层零改动」
的两个先例：LoopbackTransport 换传输层（Z 路）、TransportContractTest 假 Link 换 Link 面）：

```java
/** 仿真设备 Link：把 GATT 面搬到 adb reverse 的 localhost TCP（bridge server）。
 *  线程约定照 AndroidGattLink：socket 读线程收消息，回调统一 post 主线程；
 *  写路径=有界发送队列+后台 IO 线程（writeChunk 在主线程被调，不得直写 socket，
 *  见下方「写路径线程与超时」）。不触 android.bluetooth（AVD 无 BT 也可用）。 */
public class SimulationGattLink implements AndroidBleTransport.Link {
    public SimulationGattLink(String host, int port);       // 127.0.0.1:7788
    // —— 连接期：本地合成相位事件（server 只做数据面；故障注入留 M1+ 扩展消息）——
    @Override public String notReadyReason();               // socket 未连且不可拨 → 原因；否则 null
    @Override public void startScan();                      // ~50ms 后 onScanResult（走真实扫描相位）
    @Override public void stopScan();
    @Override public void connectGatt();                    // 异步拨号 → 成功 onConnectionStateChange(0,CONNECTED)
                                                            //   失败 onConnectionStateChange(错,DISCONNECTED)
    @Override public boolean discoverServices();            // → onServicesDiscovered(0)（本地即时）
    @Override public boolean characteristicsPresent();      // true（A002/1/2 合成在场）
    @Override public int writeProperties();                 // PROPERTY_WRITE_NO_RESPONSE（照 H3S 实测属性 4）
    @Override public boolean enableNotifications();         // → onDescriptorWrite(0)
    @Override public boolean requestMtu(int mtu);           // → onMtuChanged(247,0)（照实测 247）
    // —— 数据面 ——
    @Override public boolean writeChunk(byte[] chunk, int writeType);  // 发 ble_write；false=socket 断
    @Override public void closeGatt();                      // 关 socket；幂等
    @Override public void setCallbacks(Link.Callbacks cb);
}
```

- 相位事件本地合成 vs server 驱动的取舍 [设计]：transport 五段式状态机的覆盖已由
  TransportContractTest 假 Link 保证，M1 不需要 server 参与连接期；连接期慢/故障注入
  （测重试/看门狗）留扩展（server 发 `{"t":"link_event",…}` 消息即可）。
- **写路径线程与超时（codex 复核修正）**：Link 动作由主线程调用（AndroidGattLink 线程
  约定），`writeChunk` **不得在主线程直写 socket**（阻塞=ANR）——仅入**有界发送队列**，
  由后台 IO 线程写 socket；接收 `chunk_ack` 后及时回调 onCharacteristicWrite。片写看门狗
  5s（AndroidBleTransport.CHUNK_WRITE_TIMEOUT_MS）与帧响应超时 5s
  （SerialBleTransport.RESPONSE_TIMEOUT_MS）是硬边界：**M1 前（P2）实测直调 handler 耗时**
  （受控步进 ~10⁵ 步/s 量级，EQ apply 预算 1M 步≈10s 级——**可能超 5s**）；超时处理二选一
  并在验收记录里写明：按超时判失败（诚实）或留可配置超时扩展位（app 侧改动另评）。不许
  隐式延迟 ACK 掩盖慢 handler。
- **error 可见性**：server 的 blocked/budget/world_dead 错误要映射成 app 可见的链路错误
  事件（transport markNotReady 或 bridge error 事件），不能让上层只看到普通超时。
- 接线点：WebShellActivity.java:66 一行换——**实施路应把局部类型改成 Link 接口注入**
  （现局部类型是具体 AndroidGattLink，USB 字段也是具体 AndroidUsbLink，codex）：仿真
  模式构造 SimulationGattLink/SimulationUsbLink 并**跳过 BT 权限请求流程**（onCreate 尾部
  requestPermissions，WebShellActivity.java:100 附近——AVD 上 BT 权限弹窗无意义）、按模式
  决定 USB detach 广播注册；默认仍真机 Link，不改变真机行为。
- manifest 增 `android.permission.INTERNET`（现无，AndroidManifest.xml:3-13 仅 BT/USB 族）；
  `uses-feature android.hardware.bluetooth_le required="true"`（:11）建议仿真构建置
  `required="false"`（AVDCHK 实测侧载不受阻，仅消歧义）。
- 单测先例：TransportContractTest 的假 Link 契约断言直接套用（同套断言跑
  AndroidBleTransport+SimulationGattLink+本地 echo 桩）。

### 4.4 SimulationUsbLink + server USB 设备端（M2/M3）

**切点评估——切在 Link，不切在 UsbScsi 层** [设计]：`H3sUsbBridge.Link`
（H3sUsbBridge.java:52-68，scanJson/hasPermission/requestPermission/open/close/channel/
recoveryCapability/enterRomRequest/setCallbacks 八方法——**USB 唯一接触面**）。理由：
H3sUsbBridge 的全部业务（loader 装载序列 FB06×46+FB08+FC0A 校验、备份流、状态机、
FileSaver）与 UsbScsi 的 CBW/CSW 编解码全部零改动复用；换的只是物理层（bulk 端点→
socket）。若切 UsbScsi 层（转发 SCSI 命令对象）反而要复刻 Channel 语义且丢掉
「app 组的字节与 server 解的字节互拍」这层免费回归。

```java
public class SimulationUsbLink implements H3sUsbBridge.Link {
    public SimulationUsbLink(String host, int port);
    @Override public String scanJson();          // server usb_scan → 当前身份（normal 3654:4a55 / rom 4c4a:3442）
    @Override public boolean hasPermission(String key);       // true（仿真设备无授权面）
    @Override public void requestPermission(String key);     // 即时 onPermission(key,true)
    @Override public boolean open(String key);               // socket 会话（usb_open）→ true
    @Override public void close();
    @Override public UsbScsi.Channel channel();              // bulkOut/bulkIn ↔ usb_bulk_out/in（b64）
    @Override public byte[] recoveryCapability();            // usb_ctl(83/00/fcfe/8) → 8×ff（usb-recovery.md 实测值）
    @Override public boolean enterRomRequest();              // usb_ctl(len0) → server 翻状态 normal→rom 并"重枚举"（断会话）
    @Override public void setCallbacks(H3sUsbBridge.Callbacks cb);
}
```

- Channel 读语义：`bulkIn(max)` 允许短读（UsbScsi.java:33-34），link 侧维护接收缓冲，
  server 每条 `usb_bulk_in` 消息入缓冲，`bulkIn(max)` 阻塞取（超时 5s 照 AndroidUsbLink
  TIMEOUT_MS）。**但 UsbScsi.exchange 要求数据相位精确 size 字节、随后恰一条 13B CSW**
  （UsbScsi.java:127-136 拒绝短读）——server 的 BOT 层必须把「数据相位」与「CSW」作为
  两次独立精确长度的 bulk_in 发出（codex）；正常路径不做短读，短读只用于显式故障注入。
- **server 侧 MSC BOT 设备端（协议级）**：显式 CBW→数据→CSW 状态机（区分 socket 分片
  与 USB transfer 边界）；31B CBW（`USBC`|tag|size|flags|lun|cdb_len|cdb）→ 按 CDB 前两
  字节 opcode 分派（usb-recovery.md §RAM loader 与 USB 协议 + tools/usb-recovery/
  protocol.py 同源知识）→ 数据相位 → 13B CSW（`USBS`|tag|residue0|status0）；tag 逐命令
  校验。**设备身份与 loader 状态是 server 进程级状态**——跨 socket 重连保留（「进 ROM 后
  断开再扫描」必须仍见 ROM 身份，codex），server 退出才复位。
- **flash 后端输入（codex 修正）**：UFW 容器 ≠ 2MiB 物理镜像。输入 = **固定 2MiB raw
  flash fixture**：从公开 UFW 合成时显式列地址映射（应用密文区/boot-info 按位填入）+
  缺省区内容来源（无公开工件区=擦除态 ff，erased_bankb 先例）+ 合成标记；不接受「直接
  拿 UFW 当镜像」的口径。

| opcode | server 行为（M2） | M3 增量 |
| --- | --- | --- |
| 0x12 INQUIRY | 36B，厂商 `BR28` 产品 `UBOOT1.00`（usb-recovery.md 实测） | — |
| FB06 写 RAM | 校验 CRC16-XMODEM（`binascii.crc_hqx` 同算法），存 loader blob | — |
| FB08 跳转 | 置 loader-running 态，16B 回显响应 | — |
| FC0A 在线查询 | 16B，`fc0a03`+SPI NOR/2MiB（实测 type3） | — |
| FD05 读 Flash | 从 2MiB raw fixture 读 512B | — |
| FD07 读内存 | 从 ROM dump/镜像窗口读 ≤128B（rom-read.md 语义；app 备份流不依赖，可选） | — |
| FB01 擦除 | — | fixture 副本 4KiB 扇区置 ff |
| FB04 写 | — | fixture 副本写入+读回校验 |

### 4.5 AVD ↔ 宿主机通道 [设计+实测：路 AVDCHK 5/5 PASS]

- **adb reverse**（首选，已实测）：宿主机 `adb reverse tcp:7788 tcp:7788` 后，AVD 内 app 连
  `127.0.0.1:7788` 即达宿主机 server。AVDCHK 路已在本机 AVD 上双向实测通过（6/6），
  本节不再有可行性假设。
- **AVD 地基（AVDCHK 实测，直接复用）**：
  - 专用 AVD `h3s-app`（Android 16 / pixel_7 / x86_64 系统 + arm64 转译），headless 冷启
    ~21s：`emulator -avd h3s-app -no-window -no-audio -gpu swiftshader_indirect`；emulator
    二进制不在 PATH（本机 /usr/lib/android-sdk/emulator/）。
  - app（0.6.0-r3-webview）本机构建+安装验证过：build.sh 需 H3S_BLE_ADDRESS 占位 + vendor
    aar 从主仓复制；WebShellActivity 现为 exported=true 的 LAUNCHER——`am start` 直接拉起，
    无需 debug 入口。
  - 坑位：宿主机常见端口已被占（AVDCHK 撞过 18080）——server 端口选偏门值（本设计取
    7788）并把端口做成 CLI 参数；toybox `nc` 的 EOF 竞态只是 shell 验证工具问题，
    app 内 JS/Java 长连接不受影响。
  - 调试地基：app 跑起来后暴露 `webview_devtools_remote` unix socket（CDP 可直接调
    WebView 页面——M1 验收时看 UI 事件的旁路通道）。
  - 性能：headless+swiftshader+arm64 转译三层开销 janky ~25%，可交互，联调够用（不
    影响命令环语义）。
- 备选（不取）：adb forward（宿主→设备方向，需 app 侧监听，复杂）；虚拟网卡/组网
  （过度设计）。

### 4.6 诚实边界：USB 面是协议级仿真，不是指令级

M2/M3 的 server USB 设备端**不执行固件代码**——它按 usb-recovery.md/protocol.py 钉死的
协议字节形状应答，flash 后端是本地镜像文件。指令级 USB 设备仿真需要 USB 外设模型
（SETUP/CBW 到达→IRQ29 事件源——usb-audio-modeling.md 域的「无 SETUP→IRQ29 永无源」墙，
与 BT 协议墙同构，bt-stack-map.md §0）——不在本 bridge 范围。若未来要对拍真机 USB 行为
再立项。BLE 面则相反：**每条命令的 handler 段都是固件真实执行**（直调），只有帧封装/
表 walk（T1）和标准 opcode 桩是仿真合成——合成面逐项标注在 §3/§5。

## 5. 分期计划与验收判据

### 5.0 前置（M1 开工前补预研，~1-2天）

| # | 项 | 方法 | 产出 |
| --- | --- | --- | --- |
| P1 | 0x22 setter `0x0602583c` 的返回值与 setter 响应基址 ABI（resp/resp+2 两说冲突+写读路径矛盾，§3；已检 0x17/0x1d 恒返 0，该 ABI 从未被执行检验） | 反汇编 setter 尾部与分发器 0x600f932-0x600f954 的缓冲关系（eq-cmd-0x22-layout.md §8 工具链） | M1 的 0x22 回执组帧依据（或确认写静默）；**附带产出=r4-command-hook/keystore_hook 的回执偏移勘误线索（0x30 写回执 `30 01 00` 的状态位可能被包装覆写——UNTESTED 候选的设计级勘误，回报主协调）** |
| P2 | **停点世界直调可跑性**（修正「零未钉」：路 AB 是函数级带桩 harness 不可外推）：0x17 setter 四道前置门（0x6008170(==5)/0x6000c6c/0x60065bc/0x6018994，qcy-command17.asm）、0x17 getter（无门）、0x22 setter 链、EQ apply 链（0x06025118→EQ 引擎）逐个在目标停点世界上直调试跑 | server 骨架上跑 T1 直调试错（Blocked 即知墙位）；必要时按 RES named-value 先例补具名 fixture（**能力门 fixture 须显式标注 synthetic，不得称原链已验证**，codex） | 墙清单+fixture 需求+**直调 handler 实测耗时**（§4.3 超时判定依据）；watch PC 集合 |
| P2b | **异步消费面**：setter post 的 `('app_core', 0x300000, [回调,…])` 在事件泵 0x60123C0 的消费语义（0x300000 事件的分派路径——表1 walk？专用回调案？[未钉]）；resume_steps 解冻续跑可行性（外设时间解冻后 park 停点是否稳定推进） | 反汇编泵对 0x300000 的匹配+小步续跑实验 | M1-c 启用判据；apply 链/0x2E 异步通知可达性结论 |
| P3 | scratch 选址验证（§4.1 守卫+深栈探针） | 空直调+world_diff | 定址 |
| ~~P4~~ | ~~AVD 实测：adb reverse + INTERNET 权限 + uses-feature 侧载~~ | **已由路 AVDCHK 过线（5/5 PASS，§4.5）**：专用 AVD `h3s-app` + app 构建/安装 + adb reverse 双向实测全通；残余=实施时 INTERNET 权限随 SimulationGattLink 一并加（manifest 现无），uses-feature 侧载未构成障碍 | 通道可用性结论 ✅ |
| P5 | （T2 可选）分发器 0x0600f85e 入口 ABI + 尾部 TX 走向 | 反汇编+直调试 | T2 启用判据 |

### 5.1 M1：BLE 命令环通（三级 verdict，BLOCKED 不计「完整通过」，codex 复核重构）

改动面：app +SimulationGattLink +INTERNET 权限 +接线开关；宿主 +sim_bridge_server.py
（含直调驱动器、RCSP 解帧、表 A 查表、标准 opcode 桩）。

**M1-a 通道+读环（同步全闭环——首个可验收目标）**：
1. **连接环**：AVD app「连接」→ SimulationGattLink 五段式走到 READY(MTU 247)，
   UI 呈现已连接（transport 相位机真实跑，AndroidBleTransport 零改动）。
2. **ANC 读往返**：`FE DC BA C0 FF 00 04 | SN FE 01 17 | EF`（protocol.md:39 实测命令形）
   → server 直调 getter `0x6025c84`（无前置门，qcy-command17.asm 钉死：读 `b[0x12666B..D]`
   三字节、恒返 3）→ 回执 `FE DC BA 00 FF 00 07 | 00 SN 17 03 m s st | EF`（protocol.md:40
   实测响应形）→ app 收帧、SN 匹配、WebView 事件。**两个不同配置的读往返**（codex：避免
   「值没变也通过」假阳性——M1-b 写后值须不同于初值）。
3. verdict=**通道通过**。

**M1-b 写半程（setter 直调——「直存+投递」两级断言）**：
1. ANC 写 `FE DC BA C0 FF 00 06 | SN 17 03 01 01 02 | EF` → 直调 setter `0x6025ca4`：
   ①四道前置门真实执行（P2 过后）；②`world_inspect(0x12666B,3)` 返回 `01 01 02`
   （**地址勘误：0x1265C0+171=0x12666B**，qcy-command17.asm:6025cd0-6025ce0；cmd-0x17-0x1d
   §3 记的 0x1265EB 系转写错误，回报主协调）；③post 落队断言（app_core 队列 RAM 出现
   0x300000 事件记录）；④**setter 恒返 0=写静默，不合成写回执**（asm 钉死；app 侧写命令
   的超时行为按真机静默语义处理——写后无响应帧是否为 QCY app 预期，随 P1/P2b 结论定，
   若需要「写确认」UI 走 M1-c 的异步通知或只读查询确认）。
2. EQ 写（主目标）：合法 0x22 帧 → 直调 `0x0602583c`：len%7==3 过 → memcpy 断言
   （world_diff 配置区）→ apply `0x06025118` watch PC（**到达≠完成**，codex）——完整通过
   另需返回前栈平衡+无 Blocked+预期写集；**撞墙=verdict 停在半程**，不降级冒充全环。
3. verdict=**handler 半程通过**（各命令分别计）。

**M1-c 写全环（异步消费——P2b 过后启用）**：
1. resume_steps 解冻续跑：app_core 泵消费 M1-b 投递的 0x300000 → 回调 `0x6025D6A` →
   `0x6012be2`/`0x600dcee` apply 链 watch PC 触达 + `world_inspect` 状态迁移（0x1265C0+500
   模式字节族）+ set_config(16) 持久化触达；EQ 同构（apply→EQ 引擎配置 RAM/JL_EQ 寄存器
   写集）。
2. （若固件路径产生）0x2E 族异步通知帧 → `ble_notify` → app UI 事件。
3. verdict=**完整命令通过**。
4. **负例（两个入口，codex）**：①app 侧：畸形 EQ 帧（len%7≠3）被 EqWritePolicy 形状
   校验拦截（生产路径不动）；②server 侧测试入口：绕过 app 策略直发畸形命令 → 固件
   setter 拒绝路径、无假回执。
5. **稳定性**：连续 ≥10 条命令（写读混合）世界不崩——每命令后 SP 平衡校验+`world_diff`
   可归因；一次故意 Blocked 负例后世界标记不可继续→快照重建→命令恢复服务。

### 5.2 M2：USB 只读（FD05 备份环）

改动面：app +SimulationUsbLink；server +MSC BOT 设备端（只读命令集）。

**验收判据**：
1. app 扫描见 normal 身份 → recoveryCapability 得 8×ff → enterRomRequest → 重扫见
   ROM 身份（server 进程级状态机翻转换）——app 的进 ROM UX 全链真实走；断开重连后身份
   保持 ROM（codex：跨连接状态保留）。
2. loader 装载序列：FB06×46（23552B/512B，sha 校验照 H3sUsbBridge 既有门）+ FB08 +
   FC0A（fc0a03）全过。
3. **FD05 全片双读备份**（H3sUsbBridge 备份本就双读比对，H3sUsbBridge.java:354-360）：
   2×4096 次 FD05，app 侧双读一致后落盘文件的 SHA256 == server 输入 2MiB fixture 的
   SHA256（逐字节等）；**实测耗时记录**（不凭带宽推断，codex）。
4. 断链负例：server 中途关连接 → app 报错不静默、不产半片文件（H3sUsbBridge 错误路径
   真实走）。

### 5.3 M3：刷机流（FB06 + 擦写协议）

**改动面（codex 复核展开——远不止扩两个 opcode）**：app 侧①UsbScsi 白名单扩 FB01/FB04
（armed 门控照 UpgradePolicy 先例）；②**恢复业务本体**——H3sUsbBridge 现只有装载/读取/
备份（UsbScsi.java:18-19 无 Flash 写入口），恢复流需新做：计划生成（目标 2MiB fixture
的扇区计划+保留范围）、armed 状态（单次确认+nonce，照 UpgradeService 先例）、恢复执行器
（擦前核验→擦→分块写→读回比对，h3s_usb.py:88/:162 真实工具的守卫形状）、进度/UI 事件、
错误中止与固定范围守卫（计划外地址一律拒）。server +擦写落 fixture 副本。仿真写能力由
**仿真构建/Link 能力位**限定（sim 构建才编入 FB01/FB04 编码入口），不只依赖 server 拒绝
越界（codex）。

**验收判据**：
1. app armed 后的计划擦写（6 扇区一类，usb-recovery.md §实际物理布局 口径）落到 server
   fixture 副本：每扇区擦后读回 ff、写后读回比对一致。
2. 保留区负例：计划外地址写被拒（app 侧能力位+server 双层），app 报错不静默。
3. 全片复读与**独立构造的预期镜像**逐字节一致（预期镜像由测试脚本离线算出，不能让
   server 同时生成结果和期望，codex）。
4. 中止负例：armed 过期/进程重启后 nonce 作废，不得复用（照 UpgradeService 状态机语义）。
5. （可选 M4）BLE OTA E1-E8：server 主动发 E5 请求、校验 app 的文件切片响应（ota.md
   协议；设备侧拉模型）——不阻塞 M3 验收。

### 5.4 里程碑依赖

M1 →（无依赖）→ M2 → M3（M2 的 BOT 基础上扩擦写）；M1 与 M2 可并行（不同 Link/不同
server 模块）。P1-P3 是 M1 前置（P4 已由路 AVDCHK 过线消解，§4.5）；M2 前置已清零。

## 6. 风险与未钉点（如实）

| # | 风险/未钉 | 影响 | 缓解 |
| --- | --- | --- | --- |
| R1 | **同步/异步链混淆（codex 最大风险 #1）**：把 setter 返回、异步应用、读响应混成一条同步链会得出假成功——0x17/0x1d setter 恒返 0（写静默），生效走 app_core 异步回调 | 错误的「全闭环」结论 | §5.1 三级 verdict 强制拆分；写静默不合成回执；全环必须 P2b 过后走解冻续跑 |
| R2 | **直调失败后世界污染（codex 最大风险 #2）**：Blocked/预算耗尽只恢复 GPR，半次 memcpy/未释放锁留在世界里 | 下一条命令吃到脏状态 | §4.1 失败恢复策略：世界标记不可继续→快照重建；SP 平衡校验 |
| R3 | **函数级证据外推（codex 最大风险 #3）**：路 AB 是带桩 harness，VM 链可跑性/锁/任务身份/持久化依赖在停点世界未验证 | M1 撞墙概率被低估 | P2 逐函数试跑；能力门 fixture 显式 synthetic 标注 |
| R4 | **EQ/ANC 应用链撞未建模外设**（0x17 四道前置门 0x6008170 族、apply 链外设面；JLEQ 路正在钉 0xfe1500 族） | M1-c 不可达 | P2/P2b 预研+具名 fixture 先例；世界版本跟前沿水涨船高 |
| R5 | **0x22 回执与 setter 响应基址 ABI [未钉]**（resp/resp+2 冲突；真机抓包缺） | M1 的 0x22 回执是形状假设；r4 候选回执偏移可能同样是错的 | P1 反汇编；标注 synthetic；真机抓包后回填；r4 勘误回报主协调 |
| R6 | **直调段中断冻结**（不调 hardware.advance，路 S 显式假设） | 与真机「命令处理会被中断打断」有时序差异；长命令若依赖定时器会挂 | M1-a/b 断言面无影响；M1-c 解冻续跑恢复时间轴；如实记录 |
| R7 | **片写/响应 5s 看门狗 vs 慢 handler**（EQ apply 1M 步预算可能 ~10s 级） | 正确仿真也超时断链 | P2 实测耗时；超时语义显式二选一（按超时判失败/可配置超时扩展），不隐式延迟 ACK |
| R8 | **多连接并发**（BLE+USB 两客户端、或两个调试连接同时进） | machine 非线程安全被并发触达 | §4.1 单活动客户端纪律，其余连接 busy 断开 |
| R9 | **长会话世界漂移**：多命令后 RAM 累计（SN、配置缓存、堆碎片） | 与「重启后的耳机」语义发散 | 第一版接受（真耳机长会话同样累计）；`world_reset`（快照 restore）扩展位 |
| R10 | **AVD 通道**——已由路 AVDCHK 实测消解（adb reverse 双向 6/6、app 安装/启动、headless 性能可交互，§4.5）；残余小项=INTERNET 权限新增 | 低 | AVDCHK 结论直接复用；端口避让常见占用值 |
| R11 | **server 进程故障域**：explore 主循环异常（直调 Blocked 之外）会杀整个 server | 会话丢失需重跑（快照 8s 可接受） | world 横幅+错误上报；不试图在 server 内吞未知异常 |
| R12 | **快照兼容**：restore 要求代码指纹/场景开关/捕获上下文匹配（probe_boot.py:1871）——实施路代码态变化后旧快照拒载 | server 起跑失败 | 拒载即重捕获（M3FIX 语义：适配器形状变化→重捕过闸）；预留全量重放回退 |
| R13 | **USB 协议级仿真的保真边界**（§4.6）：字节形状钉死但时序/stall 细节是合成 | M2/M3 测不出 USB 物理层问题（-71/重枚举抖动等） | 如实标注；app 侧真机联调清单另行保留（README 清单第 9/10 条口径） |
| R14 | scratch 选址踩活数据（空直调 diff=∅ 不证无动态引用） | 世界损坏 | P3 守卫+深栈探针+换址余地 |
| R15 | 表 A 查表由 server 复刻（T1 不执行表 walk）——cmdid 路由逻辑无固件执行证据 | 极低（表是静态数据，r4 §1.1 三 walker 机器走表已钉） | T2 预研后升级可消 |
| R16 | **世界构造链复刻的维护成本**（§4.1：irq_case 无 post_stop 透传，server 要跟前沿场景演化） | 实施路/后续前沿推进时 server 接线腐化 | 构造面做成显式配置（场景名+开关集）；JLEQ 合并后与主协调定夺是否给 probe 提通用透传参 |

## 7. 约束遵从与改动面总账

- **本路（设计）**：零 tools/ 改动、零 probe 运行、未连真机、HANDOFF 不动（条目草稿随
  报告交主协调）。probe 五文件零触碰。
- **实施路（M1）**：tools/ 新文件 ×2（app: SimulationGattLink.java+测试；宿主:
  sim_bridge_server.py+测试——含世界构造链复刻段）；app 既有文件改动 ×2
  （AndroidManifest +INTERNET、WebShellActivity 接线开关+接口类型化）；probe 五文件仍
  零改动（explore/post_stop/snapshot 均为既有公共参数，probe_boot.py:98-116）。
- **实施路（M2/M3）**：+SimulationUsbLink.java、server USB 模块（2MiB raw fixture 合成
  器）；**M3 改动面（codex 复核展开）**：UsbScsi 白名单扩 FB01/FB04（armed 门控+仿真
  构建能力位）、H3sUsbBridge 恢复业务本体（计划/armed/执行器/进度/中止/范围守卫，
  §5.3）——**各项均实施路单独评审，本页不预设放行**。
- 提交规范：不加 Co-Authored-By；文档内主机/端口示例用 127.0.0.1 占位。

## 8. 证据锚点索引（复核用）

| 结论 | 锚点 |
| --- | --- |
| Link 注入面（BLE/USB 唯一接触面） | AndroidBleTransport.java:79-121（Link 接口）；H3sUsbBridge.java:52-68；TransportContractTest 假 Link（Link 面替换先例）；LoopbackTransport.java（**传输层**替换先例，非 Link 面——codex 勘误）；WebShellActivity.java:66-67（接线点） |
| 0x6000cba=2 字载荷事件 post（消费者语义无帧面） | d1-s18-consumption.md:33-45（指令级）；d1-scene-blueprint.md:35（S18 预置）；BTMAP2 §8.1（事件泵钉死） |
| 0x60252c6=按键事件 handler（表2[5]） | bt-stack-map.md §1.1/§7 RCSP 桥行；peripheral-input-simulation.md §7.2-7.3 |
| 表 A/分发器/setter ABI/响应包装 | r4-command-hook.md §1.1/§2/§6；cmd-0x17-0x1d-layout.md §1（0x600f932/0x600f89c/0x600f946） |
| **0x17 setter 全链：四门+直存 0x12666B+异步 post+恒返 0；getter 无门恒返 3** | evidence/anc-cycle/qcy-command17.asm（:28 地址 0x1265C0+171、:34 post 0x6064978('app_core',0x300000,[0x6025D6A,…])、:59 `r0 = 0`；getter :6025c84-6025ca2）；**勘误**：cmd-0x17-0x1d §3 的 0x1265EB/0x6007fb0 系转写错误（本路核实） |
| **setter 响应基址 ABI 冲突（resp/resp+2）** | cmd-0x17-0x1d §1 vs r4-command-hook §2 表「resp 写在 +2」+ tools/r4cmd/keystore_hook.S:65-78（写 `[r4+0]` 注释说 resp[2]、读 `r1=r4+2`——自相矛盾，UNTESTED 候选设计级勘误线索） |
| EQ 0x22 布局与 setter | eq-cmd-0x22-layout.md（0x0602583c、apply 0x06025118、§7 字节公式）；0x22 返回值 [未钉]（P1） |
| RCSP 帧形/响应 status+SN（实测对=读请求） | docs/protocol.md:25-43（:39 TX `FE DC BA C0 FF 00 04 6F FE 01 17 EF` / :40 RX `FE DC BA 00 FF 00 07 00 6F 17 03 01 01 02 EF`——同一 TX/RX 对）；app-protocol.md §2.1 |
| GATT 面（A002/1/2/MTU247/属性4） | app-protocol.md §1（protocol.md+MainActivity 三方一致） |
| post_stop/snapshot 公共参数 | probe_boot.py:98-116（docstring）、:2076-2078（调用点）；irq_case 无通用透传（probe_irq_entry.py:1331 签名/2660 post_stop=None） |
| 直调驱动器先例 | probe_irq_entry.py:846-876（key_chain_direct_call：save/restore+哨兵 0x100000+受控步进+不调 advance；**无 SP 平衡校验**——本设计加）；peripheral-input-simulation.md §7 |
| set/get_config 函数级实证（**带桩 harness，不可外推停点世界**） | vm-key-roundtrip.md:3（probe_pi32v2 harness）/§3 诚实边界（分配器/互斥锁/VM 锁/RTOS 查询桩+flash 持久化未仿真）；candidate-image-cotest.md §2.2 R4-COT-02 世界前置 |
| 快照 restore ~8s / 全量重放 ~27min 量级 | snapshot-resume.md；HANDOFF M3FIX 条目（canonical 轮实测）；restore 匹配条件 probe_boot.py:1871 |
| 方案 (b)：R3 不需要 modem 模型 | bt-stack-map.md §0/§4 |
| USB 协议（CBW/CSW/FB06/FB08/FC0A/FD05/FB01/FB04、8×ff、BR28/UBOOT1.00） | usb-recovery.md（实测）；UsbScsi.java（字节级移植；:127-136 exchange 拒短读）；tools/usb-recovery/protocol.py（上游 MIT）；h3s_usb.py:88/:162（恢复守卫形状） |
| 备份=双读 2MiB | H3sUsbBridge.java:354-360（readFlash×2 比对后落盘） |
| BLE OTA E1-E8（M4 候选） | docs/ota.md（设备拉模型/形状） |
| A/B 差异纪律（world_diff 依据） | candidate-image-cotest.md §2.3 |
| AVD 地基/adb reverse 实测 | 路 AVDCHK（2026-11-16，5/5 PASS；详见 §4.5 与该路交付物） |

## 9. codex 参谋结论（gpt-6-astra，read-only，2026-11-16）

复核范围=注入点选型 T1/T2、post_stop 长驻化最小改动方案、adb reverse 通道、USB Link
切点、风险清单完整性（prompt/产出存 `~/tmp/simbridge/`，不进仓）。总体判断：「架构方向
成立，但当前稿不宜直接作为实施契约」——无 P0，P1×10 + P2×2。**逐条核实后全部采纳**
（关键证据独立复核通过：evidence/anc-cycle/qcy-command17.asm、keystore_hook.S:65-78、
vm-key-roundtrip.md:3/§3、probe_irq_entry.py:1331/2660、UsbScsi.java:127-136、
H3sUsbBridge.java:354-360、SerialBleTransport.java:225-240）。修订落点：

| codex 意见（编号照原报告） | 核实 | 采纳落点 |
| --- | --- | --- |
| 1 ANC 验收三错（地址 0x12666B、异步投递、写静默） | ✅ asm 逐行核 | §0/§3/§5.1 重构（M1-a/b/c 三级 verdict）；0x1265EB/0x6007fb0 勘误回报主协调 |
| 2 setter 响应基址写错+keystore_hook 内部矛盾 | ✅ .S 源码核 | §3 [未钉]+P1 扩展（r4 勘误线索） |
| 3 「零未钉项」不成立（路 AB=带桩 harness） | ✅ vm-key-roundtrip §3 核 | §3 T1 行/§5.0 P2/R3 改写 |
| 4 post_stop 可长驻但缺构造链 | ✅ irq_case 签名核 | §4.1 启动草图+R16 |
| 5 寄存器恢复≠世界恢复 | ✅ | §4.1 直调纪律①②③+失败恢复+R2 |
| 6 socket 执行契约（SN/解帧/单客户端） | ✅ | §4.2 执行契约段 |
| 7 主线程写 socket+5s 看门狗 | ✅ | §4.3 写路径线程段+R7 |
| 8 BOT 精确长度+2MiB fixture+状态保留 | ✅ | §4.4 修订+§5.2-3 |
| 9 M3 改动面低估+不重释红线 | ✅ | §5.3/§7 改写（原「红线原意」表述删除） |
| 10 验收假阳性（分级 verdict/双配置/两入口负例/独立预期镜像） | ✅ | §5.1/§5.2/§5.3 |
| 11 接线不止换类名（接口类型/BT 权限/adb -s） | ✅ | §4.3 接线点段 |
| 12 JSON 吞吐双读口径+资源上限 | ✅ | §4.2 ③ |
| T1/T2 取舍：T1 同意作受限入口、T2 暂缓；「两字装不下」论据改为消费者语义 | ✅ | §3 两处修订 |

codex 三大风险（同步/异步混淆、失败后世界污染、函数级证据外推）分别落 R1/R2/R3。
其最小路径建议（「先闭合一个真实 getter 往返+一个有明确返回语义的 setter 用例」）
= §5.1 M1-a 起步式。**未采纳项：无**（全部核实成立）。
