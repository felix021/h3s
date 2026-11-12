# 全核 IDLE + 事件时钟首验（BI）证据包（路 BP / sim-idlemap，只读预研）

2026-11-13。本页是路线梯 **BI**（全核 IDLE+事件时钟的真实前缀首验，d1-gap-map B9）的实施设计预研：
IDLE 指令语义盘点、IDLE 后的事件源清单、事件时钟最小改动方案草图、验收断言草案。**只读**：未改
tools/ 任何代码、未跑任何会改报告的 probe；证据来自反汇编（本机脚本，钉死 codec）、
`analysis/local-checks/irq-entry.json`（p11_heap_experimental_config @304,317）、
`analysis/local-checks/event-clock.json` 与既有单测/probe。结论基于 main `3f4aca1`（d08175e 谱系）
时代的 br28_model.py / br28_machine.py / probe_irq_entry.py / probe_boot.py；路 BF/BN 正在改
同一批文件，实施路落地前按当时代码复核锚点行号。

证据强度标注：**[静态]** = 指令锚定反汇编；**[trace]** = 304,317 前缀实测；
**[模型]** = 既有模型/单测钉死的行为；**[推断]** = 由上述证据链推出、未单独实测。

## 1. IDLE 指令语义盘点

### 1.1 pi32v2 编码与 idle 站点

编码 `01 00`（2 字节），codec 显示 `idle`。健康路径上指令锚定的站点共两个 + 两个边缘站点：

| 站点 | 上下文 | 证据 |
|---|---|---|
| **0x12c5ca**（RAM code，flash 源 0x060c5da2=load 表 RAM_CODE 基 0x060c3e18+0x1f8a） | **idle 任务体内的 idle**。任务入口 0x12c57e（=CPU1 首任务特殊帧 RETI，dualcore 域钉死）；循环体 …→0x12c5c6 `call 0x12b5b0`→0x12c5ca `idle`→0x12c5cc `call -160`→0x12c5ce `if (r0<5)` 回跳。**两核共用同一份代码**：CPU1 首任务即 idle 任务；CPU0 无可运行任务时调度器同样切到它——probe_task_returns 钉死 `both-cores IDLE@12c5ca`（149,661） | [静态]+[模型] |
| **0x6066318**（flash） | CPU1 irq125 同步 handler 0x60662c8 内的**驻留 idle**：清 b[0x1265f7] bit1 后 `idle`，醒后轮询 b[0x1265f7] bit0（0x606631a/e），不满足回跳再 idle。唤醒源=CPU0 对 0xffe8c0 的 kick RMW（`cpu1_control_kick_wake` 投影，kick_rmw_writes 21 [trace]） | [静态]+[模型] |
| 0x12cd26（RAM code，flash 源 0x60c64fe） | TIMER2 延时 helper 的**忙等 idle**（`idle`→轮询 JL_TIMER2 CON bit15 完成）；仅在 K1/K2 金丝雀树+条件 0x12cc40 可达，健康路径不可达（appcore-tail-map §4、tail-sfr-semantics N4） | [静态] |
| ROM 0xa778 邻域 | TIMER2 helper 的 ROM 孪生（同 `li 0xfd0600` 起手），ROM 侧唯一引用 | [静态] |

### 1.2 进 idle 前的资格状态（谁能在 park 时叫醒它）

idle 任务每圈先 call **0x12b5b0**（OS 解嵌套 helper）：递减两级调度嵌套计数，归零路径在
0x12b5f6 把 **0 写进 banked IPMASK（0xffe168+cnum*0x800，br28_model.py:19 钉死该寻址，
idle loop 0x12b56c 与 handler 0x12ad5c 同一寻址）**再返回，随后才执行 idle [静态]。即：
**park 时刻 IPMASK=0（全开）**，配合 ICFG gates 0x300（两核 core_icfg=768 [trace]）与
IRQ4 nibble=enable+priority1（~110,768 步驱动安装后无清零，路 BE），
**IRQ4 在 park 时合格** [推断，静态+trace 合证；BE 的 delivery probe 304,857 已在运行中证实同一资格链]。
反向佐证：进入调度临界区才写 IPMASK=6（0x12b56c 存 6），退出路径写 0——与「IPMASK 运行态 0/6
随临界区升降」的 trace 观测一致。

### 1.3 模型现有处理（进/停/醒）

- **进**：probe_boot.py 运行循环（~1683）`insn.id=='idle'` 且 `idle_wait_projection` 开 →
  `machine.enter_idle_wait(cpu)`：加入 `idle_wait_cores`、记 `core_idle_wait_entered`；
  `select()`（br28_machine.py:156）此后跳过 parked 核。idle 指令本身不执行（`c.step()` 跳过），
  首次交付按「被中断上下文=idle 指令地址」装载 RETI [模型]。
- **停**：第二个核也 park 时——默认（`event_clock_projection` 关）抛 `Blocked('all_cores_idle_waiting')`
  诚实停 [模型]；开事件时钟则进入 `advance_event_clock`（§3）。
- **醒**：三条既有路径，全部从 `idle_wait_cores` 摘除并记 `core_woken_by_delivery`/kick 事件：
  ①软件 IRQ/IRQ125 交付（`deliver_software_irq`）；②core tick IRQ3 交付（`deliver_core_tick`，
  主场景栈尚未接=BF 的活）；③Timer0 IRQ4 交付（`deliver_timer0_irq`，路 BE，主场景栈同样未接——
  现只有 timer0_irq4_delivery_probe/masked 两场景）；④kick RMW（单核驻留场景，需 CPU0 在跑，
  不属全核 IDLE 域）[模型]。
- 单核 park 期间另一个核照常轮转，每条指令后照常 `hardware.advance()`——「每步轮询」只在
  全核 IDLE 时被事件时钟替代（§3）。

### 1.4 两核各自何时进 IDLE（304,317 前缀 + 静态段推断）

- **CPU1：已经 park**。p11_heap 场景恰有一次 `core_idle_wait_entered core=1 pc=0x12c5ca` [trace]；
  等价机器形状的 probe_task_returns 把该边界钉在 147,404。其后被 irq127/irq125 往返唤醒干活
  （CRC/bankB/power_init 同步段，其间在 0x6066318 驻留过、被 kick 唤醒），最终再 park 于
  0x12c5ca；304,317 时 CPU1 停在 11,181 步、PC 0x12c5ca [trace]。
- **CPU0：尚未 park**，停在 app_core 尾段 S3/S5 的 ADC 采样循环（0x60116e6 读 0xfd3100）。
  它进 idle 的路径=尾段 S3..S19 走完、S19 消息循环 `0x6064c8c(sp,16)` 在空队列上 pend
  （tail-map S19：pend-on-empty 即 B9 idle 入口条件）→ 调度器无可运行任务 → 切 idle 任务 →
  0x12c5ca [静态，appcore-tail-map]。**步距未知**：S3..S19 含 BG-a/b/c + BH 的全部边界
  （tail-map 量化：71 SFR 地址中 19 个新增，顺序段以同族投影为主），量级估计数百~数千步
  （多数段 RAM-only，任务创建往返每段 ~1k 步级）→ 首个全核 IDLE 点**大概率落在
  ~305k..312k，早于 Timer0 首次到期 341,097（指令轴）** [推断]；实施路到达后按实测重钉。
  注意 0x6064efc(N) 等待与 K1/K2 金丝雀是 watch-only 反方向信号，不构成 idle 入口。

## 2. 事件源清单（IDLE 后什么能唤醒机器）

「自主事件」=不需要任何核在跑就能发生的事件。全核 IDLE 期间固件冻结（只有核在跑才能写
IRQ config/IPMASK/pending），故资格面静态化，事件集封闭：

| # | 事件 | 期限（自 304,317 实测计数器外推） | 证据 |
|---|---|---|---|
| E1 | **Timer0 周期到期→IRQ4**（priority 1，park 时合格 §1.2） | con 0x1401（STD12M 源，div=1）/cnt 9660/prd 119999 → **+36,780 模型步到期，此后每 40,000 模型步一次**（每步恰 3 计数：304,012→304,036 的 cnt 8745→8817=+24×3 钉死）。注意**周期是 40,000 步不是 120,000**（120,000 是计数不是步）。指令轴首到期 ≈341,097（=已发布硬期限）；模型步轴（计数器真轴，含 2,155 次 stall 步差）≈343,252 | [trace]+[模型] |
| E2 | **CPU0 core tick 溢出→IRQ3** | cnt 627308/prd 1199999/每步 4 → **+143,173 模型步**（指令轴 ≈447,490=BF 硬期限；模型轴 ≈449,645）；此后每 300,000 模型步。交付机制全有、主场景栈未接（BF） | [trace]+[模型] |
| E3 | CPU1 core tick 溢出→IRQ3 | cnt 642816/prd 479999999 → **+119,589,296 模型步**，任何预算不可达，事件时钟下也永远排在 E1/E2 之后 | [trace] |
| E4 | **软件 pending（IRQ124-127，含 IRQ125 IPI）**：静态，不自发 | 304,317 时两核 pending 字全已知 0x0 [trace] → idle 入口无此类事件；若 park 前已置位且合格，事件时钟一次探查步即请求交付（契约 §2） | [trace]+[模型] |
| E5 | CPU1 kick（0xffe8c0 RMW） | 需 CPU0 在跑，**不是全核 IDLE 事件**（单核驻留域） | [模型] |

**除 E1-E4 外没有别的自主事件源**，逐项排除（=契约 §1「不发明新事件源」的实证面）：

- **WDT**：watchdog 投影到期抛 `watchdog_expired_delivery_not_implemented` 诚实停——是停不是唤醒，
  且主场景栈未开其标称时钟（AGENTS：硅 key lifetime/源时钟/reset 交付未验证）[模型]。
- **LP 定时器（P11 LPTMR0）**：只有 write-only 初始化记录与 CON 读回投影，无计数器模型 [模型]。
- **TIMER2**：无计数器模型；其 idle 忙等 helper 在金丝雀树（健康路径不可达）[静态]。
- **P33/WKUP（P3_AWKUP 族）**：外部唤醒在硅上是低功耗唤醒源，但模型只有写记录+一次性首读
  fixture，无输入电平模型、无 IRQ 线接到 advance() [模型]。
- **USB/GPIO/BT/音频**：无输入模型；GPIO IRQ 域只用于孤立 handler 研究，未接成自主事件 [模型]。
- **RTC/LRCT**：trim 投影是读回语义，非计数事件 [模型]。

## 3. 事件时钟最小改动方案草图（不实施）

### 3.1 现状（已实现的部分，BI 不需要重做）

`docs/event-clock.md` 契约 + `br28_machine.py::advance_event_clock`（225-254）+ 8 个单测 +
合成双核 probe 已就位：全核 IDLE 时**逐模型步**调 `hardware.advance()`（无批量跳步，可复现性
优先）到最近已建模事件；途中 IrqDeliveryRequest 原样抛出走既有交付投影、Blocked 原样传播、
无可唤醒事件诚实停 `all_cores_idle_waiting`；默认关=legacy 逐字节不变（单测钉死）。
「用下一个最近事件驱动步进」已成立——运行循环在全核 IDLE 时整段停在 `advance_event_clock`
内部，不再逐指令轮询；逐模型步推进是契约选择不是缺口。

### 3.2 缺口与钩子点（按优先级）

> **实施状态（2026-11-13 路 BQ/sim-evclockfix）**：G1+G2 已实施（闭式/资格
> 预检/停泊步共用分发+7 个新单测，见 HANDOFF「已实现」路 BQ 条目）；
> G3（场景接线）仍归 BI。本节保留为原预研证据。

**G1（必改）：期限计算漏 Timer0。** `core_tick_wake_horizon`（190-206）只算两个 core tick；
Timer0 到期在推进途中会经 advance() 正确抛 IrqDeliveryRequest（BE 机制），但「没有任何
core tick 在计数且无 pending」时判「无事件」诚实停，**漏掉唯一事件=Timer0 到期的场景**
（例：固件在 idle 前关掉 CPU0 tick CON bit0——当前无证据会发生，但模型允许）。修法（codex
参谋 2026-11-13 选 (a)）：**精确闭式并入同一 min()**——把 Timer0.advance 的两级分数余量
（STD12M 3/4 + divisor）解成步数：需 N=prd+1-cnt 个 post-divisor 计数 → 需源域计数
M=divisor*N-remainder → 步数 S=ceil((4*M-source_remainder)/12)（source_ticks_per_step=4、
比率 3/4；本前缀 divisor=1、各余量 0 时 S=(120000-9660)/3=36,780 精确）。闭式只作推进上限，
实际到期/交付仍由 advance() 逐步触发（与 core tick 现状同构）。前置资格检查（config 冻结
所以查一次即可）：Timer0 在计数且 ∃核 eligible(4) 才算事件；被屏蔽的 Timer0 pending 不是
事件（同契约 §4 masked 规则）。legacy 不变：全部改动在 `event_clock_projection` 开启的
路径内，默认关零字节差。

**G2（必改）：首停泊步的交付请求逃逸。** probe_boot.py 运行循环的 CoreStall 处理器
（~1695-1699）里 `hardware.advance()` 只捕 Blocked；若某核**首次执行 idle 的那一步**恰好
撞上交付请求（如 Timer0 恰在此步到期且合格），IrqDeliveryRequest 从嵌套 try 逃出整个
分发结构 → probe 崩溃而非诚实报告。概率低（每核一次的单步窗口）但确定性场景可构造。
修法：stall 路径的 advance 与主路径共用同一条交付分发（或把请求 re-raise 进主 except 链）。

**G3（接线清单，BI 场景参数）**：真实前缀首验场景 = p11_heap_experimental_config 栈 +
`timer0_irq_delivery_requests` + `timer0_irq_entry/banks/rti`（BE 三件） +
`event_clock_projection`；BF 落地后加 `core_tick_delivery_requests`+`core_tick_entry/banks/rti`。
前置依赖：BE/BF/BG/BH（路线梯顺序）；预算须覆盖首个全核 IDLE 点 + ≥80,000 模型步
（两个 Timer0 周期）。`repeat_irq_delivery` 已在主场景栈（第二次到期交付必需）。

**G4（观察项，不改码）**：`event_clock_advances` 报告键已有；建议 BI 报告补记每次
engage 时的 horizon 构成（哪个事件源、S 值），便于验收断言 A1/A2 核对——纯报告侧、
默认关不影响 legacy。

### 3.3 假设与证据强度汇总

| 假设 | 强度 |
|---|---|
| IDLE 硅语义=核停发指令直到真实交付（idle_wait_projection 措辞） | 未测量假设（契约 §5 原文不变） |
| park 时 IPMASK=0/IRQ4 合格 | [推断]（§1.2 静态链+trace 运行态佐证；非 idle 时刻实测） |
| Timer0 每步恰 3 计数、40,000 步周期 | [trace]（+24×3 钉死）+[模型]（DIVISORS[0]=1、STD12M 3/4） |
| 步数预测 341,097/447,490 可被后续 CON/CNT/PRD 写移动 | 既有 caveat 不变（d1-gap-map §6） |
| 唤醒走既有交付投影，入口银行/PSR 高位/RTI 等假设叠加适用 | [模型]（契约 §5） |

## 4. 验收断言草案（BI「首验」怎么算过）

codex 参谋（2026-11-13）校订后的五条。前四条可在 BE 接线后单独验证；A5 的 b 段依赖 BF。

- **A1 首个事件=按期限公式精确到期的 Timer0 IRQ4**：两核 park 后 `event_clock_engaged`；
  首个唤醒=IrqDeliveryRequest(core=0, irq=4)，`event_clock_advances` 与**从实际 park 状态
  （cnt/余量）算出的 S=ceil 公式精确一致**（不预锁 341,097——中间执行会消耗计数）；
  交付后 handler 0x12ece8 真实字节执行、W1C bit14 清 pending 且 `delivery_in_flight` 复位、
  RTI 回到**实际保存的 RETI**（idle 指令地址）并重新 `core_idle_wait_entered`。
- **A2 周期与推进量守恒**：配置不变时相邻两次 Timer0 到期间隔**恰 40,000 模型步**（全局模型步
  轴）；每个 idle 停留段的 `event_clock_advances` = 该段 horizon 减去 handler/RTI/重新 park
  执行期间消耗的步数——无重复计时、到可交付事件立即停推进（不检查「每步整数 CNT 前进」，
  分数余量累积可合法不动整数）。
- **A3 无事件零推进诚实停**：全部计数器不在计数且无已置位 pending → 零推进 +
  `all_cores_idle_waiting`（note: no modeled event can fire）；**masked overflow unresolved
  保留其独立 Blocked 原因**，不与停表混同。
- **A4 期限缺口反例（G1 的直接验收）**：仅 Timer0 在计数且 IRQ4 合格、两个 core tick 都不计数、
  软件 pending 全零 → 仍按闭式期限唤醒（现有 8 单测之外补此形状）。
- **A5 C2 退出条件分两级**：**(a) BI 首验=E1 往返**——真实 tick 唤醒→handler/ACK→恢复被中断
  上下文→回 IDLE，即 A1+A2 通过即算 BI 过；**(b) 「任务恢复」全证=E2 驱动**——core tick IRQ3 →
  真实 RTOS tick handler（0x60661a4 帧）→调度路径→任务上下文恢复并执行（观察到实际 TCB/帧
  切换才算「任务切换」）。Timer0 空回调往返不证明 RTOS tick 推进/调度决策；全部结论保持投影
  等级（hardware_mode_certified=false 不升级）。

## 5. 与 BJ（D1 验收）的衔接

BI 过 = 事件循环的最小形态（定时事件唤醒+回 IDLE 自我重复）在真实前缀上成立。BJ 的
「连续 N 定时事件+至少一个输入事件分发+可检查状态变化」在 BI 之上加：消息循环 0x60123e6
的分发表消费（81+9 槽）、以及一个非定时输入事件（当前模型里唯一候选=固件自身 pend 的
软件 IRQ 链——外部输入 P33/WKUP/USB 无输入模型，见 §2 排除表；这是 BJ 设计时要面对的
边界，不在本页展开）。

## 6. 不确定处（如实标注）

- 首个全核 IDLE 点的步数（~305k..312k 为估计区间，§1.4）；BG/BH 落地后重钉。
- park 时 IPMASK=0 是静态推断：若真实固件在 idle 前留有未解嵌套（计数非零路径不写 IPMASK=0），
  IRQ4 资格变化会在 A1 的资格检查处显形（交付不来→A3 停表或 masked 分支），不是隐藏失败。
- Timer0 报告不含 remainder/source_remainder，闭式修法须读模型内部状态而非报告值（±1 步级
  差异可能；A1 用公式与 advances 互证而非与预钉常数比对）。
- 0x12b56c 邻域两 helper（0x12b552/0x12b5b0）的完整角色（调度进入/退出侧）未逐指令命名，
  本页只引用与 IPMASK/嵌套计数相关的形状；如需全名归属归后续静态路。
- 本页所有「主场景栈未接」的判断基于 3f4aca1；BF/BN 合并后接线清单（G3）需按新 main 复核。
