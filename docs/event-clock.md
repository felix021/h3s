# 事件时钟最小契约（暂停点 2）

2026-11-12。codex 第 4 次参谋点名「现在定契约：虚拟时间推进条件、同刻事件顺序、中断屏蔽与唤醒」；本页是该最小契约的全文与实现锚点。此前全核 IDLE 时仿真立即停（`all_cores_idle_waiting`），时间在仿真里「停死」，持续服务（D1）无从谈起。本契约只定最小规则，具体外设按需实现。

工具：[br28_machine.py](../tools/br28_machine.py)（`advance_event_clock`）、[probe_event_clock.py](../tools/probe_event_clock.py)、[test_br28_machine.py](../tools/test_br28_machine.py)。开关 `event_clock_projection` **默认关闭**，关闭时行为与 legacy 逐字节一致（全核 IDLE 立即诚实停）。

## 契约全文

### 1. 虚拟时间的定义

- **唯一时钟源 = 已建模定时器的计数状态**：两个 per-core core tick 计数器（`CoreTickStopped`，周期 = PRD+1，SDK `GPTIMER_SYS_SET_PRD(x)` 写 x-1 的公式）以及已建模的共享 Timer0 计数。虚拟时间的一次「推进」就是一次与运行循环完全相同的 `hardware.advance()` 模型步（每步各计数器按其 `progress` 计数）。
- **不声称任何真实时间换算**：无 ns/µs 折算、无周期校准（报告既有键 `timer_progress_is_virtual_not_cycle_calibrated` 继续适用）。「步数」与「计数器值」是仅有的时间量纲。
- **不发明新事件源**：事件时钟只消费上述计数器的既有状态（con/cnt/prd/progress/pending），不为「让机器继续跑」而虚构外设到期。

### 2. 推进条件（全核 IDLE 时）

- 触发时机：所有已启动核都在 `idle_wait_cores` 中（最后一个核执行 IDLE 指令进入等待的瞬间），且 `event_clock_projection` 开启。
- 推进目标：**最近的已建模事件**，即
  1. 某个在计数的 core tick 的下一次溢出（`ceil((PRD+1-CNT)/progress)` 步，与每步 `CNT+=progress` 的计数语义精确一致）；或
  2. 已置位（或未知值）的软件 pending 位（IRq124–127）——若其合格，会在一次探查步内立即请求交付。
- 推进方式：逐模型步调用 `hardware.advance()`，与运行循环单核 IDLE 时的推进路径**完全相同**，无批量跳步、无特殊时间路径。途中三种出口：
  - `IrqDeliveryRequest` → 原样抛给既有交付投影（`deliver_core_tick`/`deliver_software_irq`）唤醒目标核，事件时钟退出；
  - `Blocked` → 原样传播（严格诚实边界在它的精确位置显形，例如 Timer0 IRQ4 交付未实现、CPU1 tick 保持态未知）；
  - 走到期限仍无交付（最近的溢出被屏蔽，计数器停在溢出态等 handler ACK）→ 继续找下一事件；**没有任何可唤醒事件时诚实停** `all_cores_idle_waiting`（note 注明 no modeled event can fire）。
- 默认（开关关闭）：维持原行为——全核 IDLE 立即诚实停，legacy 报告字节不变。

### 3. 同刻事件顺序

- **固定顺序 = `advance()` 的扫描序**：先两个核的 core tick 溢出（IRQ3，核号升序），再软件 pending（核号升序、IRQ 号升序）。同一虚拟步内先扫描到的事件先请求交付；交付请求抛出时同一步内尚未扫描到的计数器不推进（下一虚拟步再走）。
- **理由 = 可复现性优先，不是硅时序声称**：真实芯片同刻中断的仲裁/流水未建模、未测量；这里只需要任何固定全序保证逐位可复现（两核同刻溢出时低核号先醒，已在单测与 probe 钉死）。

### 4. 屏蔽与唤醒

- **只有「有资格的 IRQ」能唤醒等待核**：资格 = 既有 `IRQController.eligible` 语义（IRQ config 使能位 + 优先级 ≥ IPMASK（6/7 类走 unmaskable 投影）+ gates8/9 开）。
- **被屏蔽的 pending 不唤醒**：pending 位保持置位、不交付；时间仍推进到下一事件（单测钉死：屏蔽的 irq125 pending 与合格的 tick 溢出并存时，只有 tick 唤醒）。
- **被屏蔽的 tick 溢出不唤醒**：溢出置 pending 后计数器停在溢出态（`unresolved_overflow`，等 handler 真实 ACK），时间继续推进到下一事件。
- 全核 IDLE 期间屏蔽状态视为冻结：只有固件写（需要核在跑）才能改 IRQ config/IPMASK/ICFG。
- Timer0（IRQ4）在默认下**不是唤醒事件**：交付投影未开时，推进中若它到期且合格，按既有严格停（`interrupt_delivery_not_implemented`）诚实显形。路 BE（2026-11-13）起 `timer0_irq_delivery_requests`+`timer0_irq_entry` 可 opt-in：合格到期经同一条 `advance()` 路径抛 `IrqDeliveryRequest(core,irq=4)`，由运行循环既有分发处理（入口/W1C ack/RTI 假设与本投影叠加）；本契约的其余规则不变。

### 5. 显式假设与不声称

- 硅时序、中断进入延迟、同刻仲裁：**未建模**。
- IDLE 指令本身的语义仍是未测量假设（既有 `idle_wait_projection` 措辞不变：核停发指令直到真实交付唤醒）。
- 唤醒走既有交付投影，其各自假设（入口银行/PSR 高位/帧写入模式等）叠加适用，事件时钟不新增放宽。
- 时间推进本身是 opt-in 投影：`event_clock_projection` 需 `idle_wait_projection`，默认关。
- 已知边界：无 `repeat_irq_delivery` 时每核 tick 交付仍是一次性——第二次溢出的交付请求会在一次性校验处停（如实显形，不静默）。

## 与 D1 验收的关系

D1（控制服务就绪）要求「服务可响应且事件能唤醒处理，单纯进入循环不够」。本契约给出机制层的最小规则与合成双核端到端验证（probe 与单测）；**真实固件前缀撞上全核 IDLE 后的启用验证是剩余工作**——当前前沿（289,849/300,187）尚未到达任何全核 IDLE 点。届时叠加 `event_clock_projection` 观察首次真实 tick 溢出唤醒（C2 的退出条件）。

## 证据与复现

| 行为 | 锚点 |
| --- | --- |
| 推进到最近溢出且只唤醒合格核 | `test_br28_machine.py::EventClockTests::test_advances_exactly_to_the_nearest_tick_overflow_and_wakes_only_the_eligible_core` |
| 同刻两核按核号顺序唤醒 | 同上 `test_same_tick_overflows_wake_in_core_order` |
| 屏蔽 pending 不唤醒、时间走向下一事件 | 同上 `test_masked_pending_never_wakes_and_time_moves_to_the_next_tick_event` |
| 屏蔽 pending 单独存在也不是事件（一步探查后诚实停） | 同上 `test_masked_pending_alone_is_not_an_event_and_stops_honestly` |
| 无任何事件零推进诚实停 | 同上 `test_stops_honestly_with_zero_advances_when_nothing_can_fire` |
| 默认关 = legacy 停点形状逐字节不变 | 同上 `test_default_off_keeps_the_exact_legacy_stop_shape` |
| 合成双核端到端（enter_idle_wait → 事件时钟 → 既有交付唤醒） | `tools/probe_event_clock.py`（synthetic_two_core_only，如实标注非真实前缀） |
