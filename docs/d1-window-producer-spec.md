# D1 窗口生产者实施规格（PR-2）：d1_window 键面契约落地 + emit 纪律 + 三参数接线

2026-11-16 22:05 路 PR2SPEC/sim-pr2spec（纯设计文档路，基线 main `7d9c144`；分支对基线
零 tools/ 改动）。本文是 [s19-batch3-taskbook.md](s19-batch3-taskbook.md) §1.2/§1.3/§1.3b
（PR-2 规格）的**实施级展开**：把冻结契约（[d1-health-checks.md](d1-health-checks.md) §2）
与消费侧真实代码（`tools/check_d1_acceptance.py`，下称 checker）逐键对齐后，给出
PR-2 实施路「拿到即可写代码」的落点——每条含**实现位置建议 + 键面 JSON 形态 +
验收断言**。凡未钉死的值一律标 **[待 PR-2 实测]**，不臆造。

## 0. 定位、依据与红线

- **PR-2 范围**（任务书 §1.7）：三参数 + registry 三条 + 场景行 + 观察件 + 报告键 +
  窗口机制 + H8 生产者自检 + 正式验收入口 + **d1_1..d1_4 checker 适配**（§6）+
  CNT 假设标注机制 + D1-4 主样本观察面。**前置=闸门②**（前沿到 S19/park）。
- **依据材料**：任务书 §1.2/§1.3/§1.3b/§1.4；[d1-scene-blueprint.md](d1-scene-blueprint.md)
  §1-§4（§12 刷新注记后口径）；[d1-health-checks.md](d1-health-checks.md)（冻结契约）；
  [d1-first-run-runbook.md](d1-first-run-runbook.md)（RBKREV 修订版 §1/§2/§4）；
  checker 与 `tools/frontier_registry.py` 真实代码；[s9-keyscan-timer.md](s9-keyscan-timer.md)
  / [s8-adc-scan-timer.md](s8-adc-scan-timer.md)（sys_timer 源形态）。
- **红线复述**：键名/语义与冻结契约零分叉（变更先改契约文档再改代码）；观察件纯只读
  旁路（不动计步/预算/IRQ 交付顺序——任务书 §1.2 第 4 条）；全部新开关 opt-in 默认关、
  legacy 场景报告逐字节不变（stash 对照）；P_park/CNT 快照值/门控值/id=2 节点全部实读，
  不预锁 40,000/300,000/4×10⁵/2×10⁵；首次过线唯一证据=Q1 全量重放。
- **契约优先级**：本文与 d1-health-checks §2 冲突时以后者为准并回报主协调（任务书
  红线 2 同款）。本文新增的是**实现落点**，不是新契约——除 §2.7 三处 checker 适配点
  （属任务书 §1.2 第 7 条已授权的「d1_1..d1_4 按 d1_window 键契约适配」的具体化）。

## 1. 数据流总览与三个架构决策

```
场景行 d1_acceptance_window（probe_irq_entry.main 场景表尾）
  │ scenario_flags('p11_heap_experimental') 实取（含 B2G 后的 cfg523_named_fixture=True）
  │ + event_clock=True + d1_acceptance=True（+d1_window_steps/d1_post_inject 可选）
  ▼
irq_case(...) ──构造──▶ D1WindowObserver（建议新文件 tools/d1_window_observer.py）
  │                        │ 观察器随 dual_case→explore 传入（probe_boot 只加挂点）
  ▼                        ▼
explore 主循环（probe_boot）：每步/每 stall/每交付 → observer.note_*()
  │  窗口计数器耗尽 → stop={'kind':'window_complete',...}
  ▼
报告键三层：
  ① machine.*/peripherals.* 观察键（「路 CH 需求」落地：watchpoint_hits/
  │   idle_wait_park_log/event_clock_engagements/message_chain_observations/
  │   jiffies_observations/task_switch_observations/expiration_model_steps/
  │   w1c_acknowledgements/eligibility_note）—— d1_1..d1_4 的现成数据面
  ② d1_window 树（post_stop 汇总或 observer.finalize() 注入）—— d1_5 数据面
  │   + D1-1..D1-4 的镜像子树（蓝图 §4.3）
  ③ verdicts = run_all(场景行) 结果回填（单源住在 checker，不分叉）
  ▼
正式验收入口：probe 跑完调 check_d1_acceptance.run_all(scenario_row)，
  d1_gate != 'pass' → 非零退出码（任务书 §1.2 第 7 条）
```

**决策 A（双数据面）**：d1_1..d1_4 继续消费 machine/peripherals 观察键（checker 的
missing 文案即键名清单，逐键落地后零重写即可判定），d1_5 消费 `d1_window.health`。
不把 d1_1..d1_4 整体改读 d1_window 树——那会破坏 runbook §4.1「两处场景名不同是
预期」（旧场景 `p11_heap_experimental_config` 的 park 判定继续用 machine 既有键）。
checker 侧只需 §6 的三处小适配。

**决策 B（观察器独立文件）**：观察/窗口逻辑集中新文件（建议
`tools/d1_window_observer.py`，类 `D1WindowObserver`），`probe_boot.explore` 只加
三个挂点（构造传参 / 每步回调 / 停点后 finalize）——最小化对禁区文件
`probe_boot.py` 的侵入，也缩小与在途路（JLEQ 等）的批冲突面。观察器不 import
checker（验收入口在 probe_irq_entry 侧调 run_all，观察器只产数据）。

**决策 C（观察件 vs 行为件的分界）**：观察器全部钩子只读；唯一行为件=**窗口耗尽停**
（`d1_window_steps` 耗尽 → `stop={'kind':'window_complete'}`）与 H8 自检的 **N 自动
延长**（§5）——两者都只在 `d1_acceptance=True` 的场景激活，默认关=legacy 逐字节
不变（红线 4 允许）。

## 2. d1_window 报告键完整 schema（逐键：形态 / 实现位置 / 验收断言）

总形态=蓝图 §4.3 示例树 ∪ d1-health-checks §2 冻结契约 ∪
`tools/test_d1_acceptance.py::d1_health_scenario()`（PR-1/H2SRC 合成样本=契约的
可执行实例，键名/嵌套以其为准）。下表把实现落点补齐；JSON 形态仅列非显然字段。

### 2.1 顶层骨架与双轴步数

```python
'd1_window': {
  'park': {...},                      # §2.2
  'core_tick_state': {...},           # §2.2（assumption 键位=顶层优先，checker 两处兼容）
  'loop_head_first_step': N, 'loop_head_count': N,
  'loop_head_count_after_drain': N,   # 期望 0（注入后回升，蓝图 §2.1）
  's18_first_pass': [...],            # 全程口径（S18 post 在 park 前发生，§3.3）
  'empty_queue_pend': {...}, 'dispatch': {...}, 'canaries': {...},
  'timer0_cycles': [...], 'core_tick': {...},
  'd1_4': {..., 'verdict': 'PASS|NOT_RUN|FAIL', 'no_sample_reason': '...'},
  'health': {...},                    # §2.4/§2.5，窗口口径 [W_park, W_park+N)
  'd1_model_steps_total': N,          # 全局模型步=hardware.advance() 总调用数（含事件时钟段）
  'd1_window_instruction_steps': N,   # 窗口内执行指令数=completed 末值−W_park 时 completed
  'window_blocked_events': [],        # 期望空；窗口内任何非 window_complete 停点记于此
  'verdicts': {'D1_1': ..., 'D1_2': ..., 'D1_3': ..., 'D1_4': ..., 'D1_5': ...},
  'projection': 'D1 window verdicts are conditional on the delivery/event-clock projection '
                'chain (idle_wait/event_clock/timer0/core_tick/…; hardware_mode_certified'
                '=false 不升级)',
}
```

- **实现位置**：`verdicts` 与 `d1_4.verdict` 由正式验收入口（§6.4）调
  `check_d1_acceptance.run_all()` 后回填，probe 侧不自行判定（单源纪律，任务书
  §1.2 第 7 条）。其余子树由观察器在运行中累计、停点后 `finalize()` 组装，
  经 explore 的 post_stop 观测通道（`result.update(post_stop_observations)`，
  probe_boot.py L2140 先例）注入顶层 `d1_window` 键。
- **`d1_model_steps_total` 的计数源**：`hardware.advance()` 是唯一模型步源（主循环
  每步一次 + `br28_machine.advance_event_clock` 段内每虚拟步一次，br28_model.py
  L7060 起）。建议在 `Hardware.advance` 入口加一行无条件内部计数
  `self.advance_calls += 1`（**不进 hardware.report()**——legacy 报告零变化），
  观察器停点后读出。窗口计数 = `advance_calls − W_park 时快照`。
  备选（避免碰 br28_model）：主循环步数（completed + machine.stalls）+
  `machine.event_clock_advances` 拼合——不精确（stall 分支 advance 计入方式），
  仅在 br28_model 冲突面不可接受时退此下策并如实标注口径 [待 PR-2 实测核对差值]。
- **验收断言**：probe check() 最小形状断言（任务书 §1.2 第 7 条）：顶层键齐全、
  `park.event_clock_engaged` 为真、`stop.kind=='window_complete'`（或
  `window_blocked_events` 非空时的 Blocked 记录）、`canaries` 两计数=0。

### 2.2 park 子树与 W_park 锚定（RBKREV 修正口径）

```python
'park': {
  'model_step': N, 'instruction_step': N,        # 双轴并记（红线：两轴不得互填换算）
  'cores': {'0': {'pc': '0x12c5ca', 'via': 'scheduler_idle_switch'},
            '1': {'pc': '0x12c5ca'}},
  'event_clock_engaged': True,
  'timer0_state':    {'con': '0x…', 'cnt': N, 'prd': N, 'period_closed_form': N},
  'core_tick_state': {'cnt': N, 'prd': N, 'deadline_closed_form': N,
                      'assumption': 'core_tick_cnt_held_snapshot:'
                                    'assumed-not-measured (NOT PRD-1)'},
  'queue_count': 0},                              # 互证字段：h[TCB+6]（实现可选，见下）
},
'core_tick_state': {同 park.core_tick_state},     # 顶层重复一份（键位=顶层优先，§1.4）
```

- **W_park 判据（核心）**：runbook §1.1（RBKREV）修正口径——W_park=「**S19 空队列
  pend 链完成后的那次事件时钟接管**」，不是全程第一次 `event_clock_engaged`
  （boot 期 os_time_dly 途中停泊已多次 engage，B3 实测 13,429,113 虚拟步）。机械
  判据（主）：观察器 watchpoint **0x6064cf6（pend 写面首 PC）首达**置
  `pend_write_seen=True`，此后的**首次** `event_clock_engaged` 即 W_park——时间序
  保证：boot 停泊全部发生在 S19 pend 写面之前（BA #15 前的停泊均无 pend 写面）。
  互证（实现可选，进 `park.queue_count`）：engage 事件时读任务表
  `[0x126c5c]`（cnum=0）→TCB→`h[TCB+6]`==0（空队列）+ engage 事件自带
  `idle_cores==[0,1]`。
- **实现位置**：观察器 `note_step()`（PC=0x6064cf6 计数）+
  `note_event()`（捕获 machine `event_clock_engaged` 事件，machine.record_event 的
  旁路钩子或事件流轮询）。落定瞬间快照：`machine.event_clock_advances`、
  explore `completed`、两核 `cpu.pc`、`hardware.timer.{con,cnt,prd}`、
  core tick `{cnt,prd}`（+内部 progress 现算 deadline）。
- **`period_closed_form` 公式**（与 checker `_timer0_expected_period_steps` 同源，
  DIVISORS 查表不自推导）：
  `per_step = 3 if (con>>10)&15==5 else 4`；
  `counts = DIVISORS[(con>>4)&15] * (prd+1)`；
  `period = counts // per_step`（非整除 → None + H2 pending，不猜）。
- **`deadline_closed_form`**（core tick 首溢出相对步数）=
  `ceil((prd+1-cnt)/progress)`（event-clock §2 条 3 的 S=ceil 公式；progress=每模型步
  core tick 计数，机器内部值，B3 口径 4）。CNT 起点携带冻结假设——assumption 键
  原文照抄 B3 registry 措辞（任务书 §1.0 空位表），**顶层+park 下两处都放**（顶层
  优先，PR-1 已两处兼容；缺失 → H2 core_tick 子项 pending，checker L1465-1474）。
- **验收断言**：probe check()：`park.model_step` 为 int 且
  `d1_model_steps_total ≥ park.model_step`；`timer0_state.period_closed_form` 非 None
  （None=停点配置异常，如实停归因）；`assumption` 键含 `'assumed-not-measured'`。
  checker 侧：H2 的 core_tick 分支消费 assumption（L1465-1474）；H8 消费
  `per_source[timer0].period`。

### 2.3 窗口步数 N 与 P_park 实读（任务书 §1.3 的落地）

- **N 的默认公式**（窗口起算时现算，观察器在 W_park 落定点执行）：
  `N = max(S_tick + 160_000, 200_000)`，其中 `S_tick = park.core_tick_state.deadline_closed_form`。
- **`d1_window_steps` 显式值覆盖**（值开关语义，`is not None` 判激活）：显式 N′ 替换
  公式值，**且不参与 H8 自动延长**（用户意志；覆盖不足 → checker H8
  insufficient_window_coverage → pending → 验收入口非零退出，修 N 重跑——runbook
  §4.1 verdict 分流）。
- **H8 生产者自检**（§5 详述）：公式 N 落定后现算 `N // P_park >= 3`
  （P_park=timer0 period_closed_form）；不足则自动延长 `N = max(N, 3*P_park)`
  （任务书 §1.2 第 7a 条「延长优先」）并如实以延长后的 N 报告
  （`health.window.steps`=实际生效值——契约无「原始/生效」双键，不发明）。
- **两轴纪律**（RBKREV P0）：N=**模型步轴**（含事件时钟快进段）；场景 budget=
  **指令步轴**（`completed_steps`，快进段不耗）——窗口机制不碰 budget 推导，
  靠 §2.1 的 `advance_calls` 计数停表。落档三数分列（执行预算/completed_steps/
  模型步），不互换冒充（runbook §2.1）。
- **验收断言**：checker H8（L1895-1925）：`window.steps`（缺则 end−start）与
  `per_source[timer0].period` 折算 `N//P_park>=3`；反例形状已由
  `D1_5H8WindowCoverageTests` 8 例钉死。

### 2.4 health.per_source：四周期源 + 非周期源（H2 数据面，窗口口径）

条目形态（冻结契约 §2 + S9 §2③/S8 §5 形态）：

```python
{'source': 'timer0',   # 周期源,kind 缺省='irq'
 'core': 0,
 'period': 40000,               # park 态实读=period_closed_form(§2.2 公式);非整除=None→H2 pending
 'expirations': N, 'deliveries': N, 'completions': N,   # 三分计数,窗口内
 'upper_bound': None,           # 可选互证键;填则必须==checker 自算 ceil(N/P)+1,不符=矛盾 FAIL
 'first_deadline': N|None,      # 窗口内首期限全局模型步
 'pending_at_window_start': False},   # W_park 时该源 pending 已置位(相位容差)
{'source': 'core_tick', 'core': 0, 'period': 300000, ...},   # period=PRD 读回/progress 实读,承载 CNT 冻结假设
{'source': 'keyscan', 'kind': 'sys_timer', 'core': 0,
 'period': 400000,              # =10×P_timer0;tick 数 10=静态(ctx[+16],S9 §1.1),P_timer0 实读
 'expirations': N, 'deliveries': N, 'completions': N, ...},  # 三分坍缩同数(同步回调)
{'source': 'adc_scan', 'kind': 'sys_timer', 'core': 0,
 'period': 200000,              # =5×P_timer0;tick 数 5=立即数硬编码(S8 §1.1),P_timer0 实读
 ...},
{'source': 'irq124', 'period': None, 'expirations': 0, 'deliveries': 0, ...},  # 非周期源:触达才入表,预期 0
```

- **period 实读来源（关键口径）**：`timer0`=park 态 con/prd 闭式（§2.2）；
  `core_tick`=PRD 读回（`core_tick_prd_readback` 恒等链，1199999）÷ progress——
  **不承载**冻结假设的是 PRD 读回，承载的是 CNT 起点（§1.4 第 4 条两代读回不混写）；
  `keyscan/adc_scan`=**静态 tick 数（10/5）× P_timer0 实读**（S9 §2②/S8 §2 红线：
  tick 数是静态事实、P_timer0 现算不预锁，随 BY 三态重编缩放）。
  **park 态快轮链表 0x126DF0 实读**（S8 §4 运行实读判据：walk 链表按回调字段
  0x6001944/0x60349d2 定位节点、读周期字段）作为 PR-BI 观察轮的**互证手段**，
  不进 per_source 键（契约无此字段，不发明）；[待 PR-2 实测：节点字段精确偏移
  与 walk 的可行性——节点在 RAM，park 态直读无副作用]。
- **三分计数采集点**（全窗口差分：W_park 落定时快照基线，停点时差分）：
  - `timer0`：expirations=`peripherals.timer0.expirations` 差分（既有全程计数器）；
    deliveries=`machine.timer0_irq_entry_projection.deliveries` 过滤
    `model_step > W_park`（**deliveries[].model_step 为路 CH 需求新观察**，挂
    br28_machine 交付记录处）；completions=`timer0_irq_rti` 事件过滤窗口内。
  - `core_tick`：expirations=`core_tick_configuration[0].expiration_model_steps`
    过滤窗口（**路 CH 需求新观察**，挂 br28_model core tick 模型）；deliveries=
    `core_tick_entry_projection.deliveries` 过滤；completions=ACK
    `acknowledgements` 差分（既有键）。
  - `keyscan/adc_scan`（sys_timer，坍缩同数）：锚=轮询 **0x6066de0** 的火回调点
    **0x6066e32/0x6066e7c** + 回调入口/返回（键扫 0x6001944 / adc_scan 0x60349d2）
    ——watchpoint 观察件计数（D1S9 §4.3/S8SCAN §6.4 观察件纪律，只读旁路）。
    expirations=到期判定命中（火回调点）、deliveries=回调入口、completions=回调
    返回，同步执行下三者同数（如实各自计数，坍缩是结果不是预设）。
- **first_deadline / pending_at_window_start**：timer0/core_tick 从机器内部状态
  （W_park 快照时的 pending 位与闭式期限）取；sys_timer 源从链表节点的到期字段
  [+20] 与 tick 计数 [0x1268CC] 换算 [待 PR-2 实测：换算公式按节点实读形状定，
  拿不准填 None——first_deadline=None 合法（契约 N|None），pending 容差项退 0]。
- **emit 纪律**（§3 详述）：timer0/core_tick/adc_scan 无条件入表；keyscan 挂
  cfg523 容器实状态；非周期源触达才入表。
- **验收断言**：checker `_h2_source_bounds`（L1397-1499）：每周期源
  `deliveries <= ceil(N/P)+1`、三分一致（交付≤到期+相位容差、完成≤交付）、
  `upper_bound` 可选互证；`MANDATORY_SOURCES=('adc_scan',)` 缺条目=pending
  （L1492-1498）；keyscan 缺条目不 pending；core_tick 条目另查 assumption 键。
  反例已由 `D1_5H2SysTimerSourcesTests` 8 例钉死。

### 2.5 health 其余子树（窗口口径，冻结契约 §2 全文照用）

实现位置统一=观察器运行中累计 + finalize 组装；每子树的 checker 判定分支：

| 子树 | 采集点 | checker 判定（函数:行） |
|---|---|---|
| `window.{model_step_start,model_step_end,steps,complete}` | 观察器（W_park/N/停点） | `_health_window_steps` L1242；H8 L1895；右边界条款=`complete:False` → pending 面 |
| `timer0_expiries` + `timer0_expiry_steps` | 窗口内到期步号（差分或过滤） | `_h1_timer0_events` L1312：≥3+间隔恒=period+与 `d1_window.timer0_cycles` 同数互证 |
| `event_reposts[]` + `queue_balance` | post/入队/消费观察（窗口口径**注意**：S18 预置 post 在 park 前，正常 D1 窗口内 reposts 可为空=H3 vacuous pass L1513；窗口内注入变体才有条目） | `_h3_reposts` L1504：posts≤2（natural）/3（injected）、消费=入队、queue_balance 互证 |
| `per_core['0'/'1'].{wake_count,rewait_count,wake_reasons,timeline,last_open_wake}` | machine 事件流（core_idle_wait_entered/交付唤醒）过滤窗口；timeline 每转换记 {model_step,kind,pc,irq,nesting} | `_h4_wake_rewait` L1588（判据面=CPU0）；`_h7_timelines` L1825（步号严格递增+合法转移 HEALTH_TIMELINE_NEXT） |
| `ready_tasks[]` | **停点** TCB 扫描 state==ready（`[]=扫描过且空=vacuous pass`，键缺=未扫描→pending）；`assertion_face` 生产者可填（app_core 消费链判定），缺时 checker 按 entry==0x6012028 兜底 | `_h5_ready_tasks` L1668：断言面 schedulings==0=FAIL；其余观察级 |
| `queue.{count_final,qlimit,capacity,cursor_trace,cursor_monotonic}` | 停点读 `h[TCB+6]`/环参数；cursor_trace 窗口内入队/出队记 {step,op,seq,physical}（**逻辑序号**，物理回绕合法） | `_h6_queue` L1731：count_final==0 或 ≤qlimit；序号严格递增+physical==seq mod capacity |

- `wake_reasons` 枚举不预锁（键扫/sys_timer 唤醒类条目自然容纳，任务书 §1.3b
  第 2 条）；只存计数+关联 id（砍单项，任务书 §1.0b 第 9 条）。
- [待 PR-2 实测：TCB 扫描的 ready 判定偏移（state 字段地址）与 qlimit/capacity
  的实读来源——BA §6.2 给了 TCB 面（+12/+14/+6），state 偏移按停点机器实测定；
  拿不准的字段填 None（qlimit=None 且 count_final>0 → H6 pending，不猜）]。

### 2.6 D1-1..D1-4 观察子树（d1_window 镜像）+ machine/peripherals 观察键

蓝图 §2.1-§2.4 各表已逐键定义（`empty_queue_pend/dispatch/canaries/timer0_cycles/
core_tick/d1_4`），实现=观察器从下列**主数据面**（路 CH 需求键，d1_1..d4 checker
直接消费）聚合镜像：

| 路径 CH 需求键（machine/peripherals 面） | 挂接文件 | 消费者（checker 函数） |
|---|---|---|
| `machine.watchpoint_hits[pc_hex]`（计数 dict） | 观察器自维护（probe_boot 每步回调 note_step 计数；不依赖 diagnostics.watch_pcs——那是 milestones 形状） | d1_1（S19 头/get-event/K1/K2）、d1_3（时间片 0x60653ae） |
| `machine.idle_wait_park_log[]`（不截断 park/唤醒序列，条目含 model_step/pc/core） | br28_machine（record_event 旁路并行累积，非禁区） | d1_1/d1_2/bi_a1/bi_a2 窗口切分 |
| `machine.event_clock_engagements[]`（每次 engage：model_step + timer0{con,cnt,prd,remainder,source_remainder} + core_ticks['0']{con,cnt,prd,progress} + first_segment_advances + inter_segment_execution） | br28_machine.advance_event_clock 入口 | d1_3 闭式、bi_a1/bi_a2 守恒（余量显式不给默认值） |
| `machine.message_chain_observations{samples[],s18_preset_samples,verdict,window_covered,last_dispatch_step}` | 观察器（全程口径，§3.3） | d1_1/d1_2（post_dispatch）/d1_4（CHAIN_STAGES 十站三值 True/False/None） |
| `machine.jiffies_observations[]`（0x125004 写序 before/after + delivery_index） | br28_machine（core tick handler 帧内写观察） | d1_3 jiffies+1 |
| `machine.task_switch_observations[]` | br28_machine（调度切换点） | d1_3/bi_a5 观察项 |
| `peripherals.timer0.{expiration_model_steps,w1c_acknowledgements}` | br28_model（Timer0 模型 report 扩展，**仅 D1 场景带出**——观察器读取后由 d1_window 树镜像，hardware.report 不加键保 legacy；两案二选一，见风险 §8.4） | d1_2 逐周期四查 |
| `peripherals.core_tick_configuration[0].expiration_model_steps` | 同上 | d1_3 溢出步号 |
| `core_tick deliveries[].{model_step,eligibility_note}` | br28_machine 交付记录（eligibility_note=结构化 {ipmask,priority,eligible:true}，自由文本不算） | d1_3 资格面 |

- 既有键（零新观察）：`timer0_irq_entry_projection.deliveries`（core/irq/vector/
  handler/reti_from）、`core_tick_entry_projection.deliveries`、
  `irq_delivery_deferral_projection`、`peripherals.timer0.{con,cnt,prd,expirations}`、
  `core_tick_configuration[0].acknowledgements`、`machine.events`（32 条滑窗+
  `events_truncated`）、`event_clock_projection`。
- `d1_window.timer0_cycles[i]`：从 deliveries+rti 事件+w1c 计数组装（每周期
  `expiry_model_step/gap_from_previous/delivered/acked/w1c_count/rti_pc/re_parked/
  tick_count_delta/tick_ms_delta/sys_timer_polled/delivery_delay_steps`——
  tick 效果=[0x1268CC]/[0x1268D0] 差分，观察器在交付帧前后读 RAM）。
- `d1_window.core_tick`：`deadline_closed_form`（park 态闭式）/
  `actual_overflow_model_step`（**窗口内**首次溢出步号，见 §6 适配点三）/
  `ack_count`（窗口内 ACK 差分）/`jiffies_before/after`（首窗口内交付绑定）/
  `timeslice_call{executed,returned}`/`task_switch`。
- **验收断言**：d1_1..d1_4 各函数的 missing 文案即键清单（REQUIRED_OBSERVATIONS
  L242-323）——PR-2 完成后 `--scenario d1_acceptance_window` 跑 Q1 报告，这些
  missing 全部消失、判定进入 pass/fail 实质分支。

### 2.7 窗口切分锚点：消费侧现状与 RBKREV 口径的偏差（checker 适配点，任务书 §1.2 第 7 条授权）

**发现的偏差**：checker 现状的窗口切分锚=`idle_wait_park_log` **首条** park 的
model_step（d1_2 L504-507、d1_1 window_parks L393-396、bi_a1 L907、bi_a2 L1012
同款）与 d1_3 的首个带快照 engagement（L685-688）。全程跑 D1 场景时**首条 park=
boot 期 os_time_dly 途中停泊**（B3 实测多次，远早于 S19）——「窗口内到期 ≥3」等
判据会被 boot 前缀的 Timer0 到期（B3 计 3401 次）污染而轻易满足，判据失效；d1_3
的闭式比对会锚到 boot 首次 engage 的 core tick 快照（非 park 态）。

**适配（只动 D1 系列，BI 系列语义本就是「首次停泊-唤醒往返」，不改）**：
`scenario['d1_window']` 在场（即 D1 场景行）时——

1. d1_1/d1_2 的 `park_boundary` ← `d1_window.park.model_step`（W_park）；
   键缺时回退现状（首条 park）。
2. d1_3 的 `snap_entry` ← engagements 中 `model_step` 最大且 ≤ 窗口观察值者
   （=W_park 的接管）；`expiry_steps0[0]` ← 窗口内首个 `> W_park` 的溢出步号
   （闭式=deadline_closed_form，与 park 快照同一冻结假设）。
3. bi_a1/bi_a2/bi_a3/bi_a4/bi_a5：零改动。

适配属 PR-2 的 checker 半边（`check_d1_acceptance.py` 非禁区，任务书 §1.2 第 7 条
「d1_1..d1_4 函数按 d1_window 键契约适配」的具体化）；单测钉「合成样本带 boot 停泊
+d1_window.park 时，d1_2 不再吃 boot 到期」（§7）。

## 3. emit 纪律实现点（H2SRC 留下的生产者义务）

runbook §5.1 第 4 条（H2SRC 更新后）：「cfg523 开+缺键扫 → pending」的严格反例由
**生产者 emit 纪律**承担——checker 无法分辨「cfg523 关」与「缺观测」（d1_window
键面不携带 cfg523 状态）。落地：

### 3.1 keyscan：cfg523 开 → 条目必生成

- **判定源**：`machine.cfg523_installed_at`（br28_machine 属性；报告键
  `cfg523_named_fixture_projection.installed_at` 同源，B2G 落地——解析器 0x60010b4
  首次执行时置 `{'pc','object_bytes'}`，未到=None）。**用运行时证据，不用场景
  flag**：flag=True 只证明 fixture 武装（conditional 栈自动开），installed_at 才
  证明解析器真实执行、S9 注册真实发生。正常 D1 场景（跑到 S19）解析器必已过
  ——installed_at 非 None ⇒ keyscan 条目必 emit；恰好实现「cfg523 开必 emit」。
- **挂接位置**：观察器在 **W_park 落定点初始化 per_source 条目集**（一次性）：
  `timer0/core_tick/adc_scan` 无条件 append；`keyscan` 当且仅当
  `machine.cfg523_installed_at is not None` append。若 flag=True 而
  installed_at=None（世界异常），不 emit 且在 `d1_window` 顶层记一行如实现注记
  （不进 health 键面；[待 PR-2 实测：是否需要专用注记键——默认不加，异常世界
  本就到不了 park，如实停已由 Blocked 兜住]）。
- **反例单测**（§7）：合成场景 `cfg523_installed_at` 非 None + 生产者逻辑单测钉
  「keyscan 条目必在」；反向（None → 不 emit）同钉。

### 3.2 adc_scan：无条件必 emit（init walker 直线段注册）

- **依据**：S8 段（0x60121a2..0x60121ae）是 init walker 直线段、无分支，boot 期
  一次性走完（远早于 D1 窗口）——节点必挂快轮 0x126DF0、每 5 tick 重触发
  （S8SCAN §4 静态判据）。D1 窗口=boot 完成后开窗 ⇒ 条目必在；缺条目=观测缺口
  → checker pending（`MANDATORY_SOURCES`，L1492-1498）。
- **挂接位置**：同 §3.1 的 W_park 初始化块，无条件 append（不挂 cfg523 条件栈，
  S8SCAN §5.2 第 2 条）。计数锚=§2.4 的 watchpoint（0x6066e32/0x6066e7c +
  0x60349d2 入口/返回）。

### 3.3 D1-4 固件自然事件 (18,0)/(20,2) 的采集点（全程口径）

- **口径区分（易错点）**：主样本 (18,0) 发生在 **park 前首批迭代**（蓝图 §2.4），
  因此 `message_chain_observations` 与 `d1_window.d1_4` 子树=**全程观察**（从
  boot），**不**限窗口；`health.event_reposts/queue_balance` 才是窗口口径
  （正常窗口内 reposts 为空=H3 vacuous pass；只有注入变体/调度新事件才有条目）。
  两口径并存是契约本意（d1-health-checks §2「event_reposts 窗口口径」vs 蓝图
  §2.4「park 前首批迭代」），报告内不加换算。
- **采集锚（CHAIN_STAGES 十站，BZ/BA 锚点）**：post 0x60123aa/0x60123b2→
  0x6000cba(0x60a1d11,0x10000C,{18,0}|{20,2})；入队 0x6064978→0x606487c（slot
  头字 0x10000C03、`h[TCB+6]` 0→1）；get-event 0x6064c8c 返 13（0x60123f0 消费）；
  `[sp]` id 读（=0x10000C）；bit20 0x60123f6；特路 0x60108fe（子码 18 直通/20→
  0x126F3C 链表 id=2）；表 2 walk（6 条，0x600434a/0x60252c6 不认领返回 0）→
  `[0x126f4c]` 动态过滤链（期望 0=跳过）→ 表 1 walk（54 条，[39] 命中）；
  handler 0x6023962（0x6023972 子码门→0x60239b2→0x60237d8→门控
  b[0x125B54]/b[0x127184]——运行值=观测项 R4，关=短闭合返 0）；返回→回环→
  空队列 pend 再执行。观察=PC watchpoint + 关键 RAM 值采样，全程累计。
- **samples[] 条目**：`{event:'(18,0)', origin:'firmware-natural', 十站三值
  True/False/None, id_read_value, producer_pc, first_post_step}`；
  `verdict`：`queue_empty_no_source`（回调表 [0x126F6C] 非空+队列空+无样本=未覆盖
  NOT_RUN）vs `chain_broken`（post 成功却丢失=FAIL，附 broken_evidence）。
- **d1_window.d1_4.verdict** 由 run_all 回填（§2.1）；probe 不自判。

### 3.4 观察件纪律（红线）

只读旁路：不改计步位置、不耗 budget、不动 IRQ 交付顺序与同刻扫描序、不写 guest
可见状态；watchpoint 命中间的 RAM 采样用 `mem.ram` 直读（模型簿记读先例）。
报告公共字段与序列化顺序不动（任务书 §1.2 第 4 条）。

## 4. probe 三参数、registry、场景行与 budget（任务书 §1.2 第 1-3/6 条）

### 4.1 irq_case 三参数（接线照 event_clock 先例，probe_irq_entry L1320 旁）

```python
d1_acceptance=False,       # 场景开关:观察器构造+窗口机制+正式验收入口
d1_window_steps=None,      # 值开关:None=默认公式 §2.3;int=显式覆盖(不参与 H8 延长)
d1_post_inject=None,       # 值开关:默认关;'s18_18'=PR-4 注入变体(PR-2 只接线占位)
```

- 激活判定一律 `is not None`（值开关，参谋结论①）；`d1_window_steps` 非 None 时
  require 为正 int；`d1_post_inject` 非 None 时 require `=='s18_18'` 且 PR-2 阶段
  `raise Blocked('d1_post_inject_pending_pr4')`（诚实停先例——行为实现归 PR-4，
  任务书 §1.7；PR-4 开工时替换为直调 0x6000cba(18,0) 的 key_chain_direct_call
  形态+显式合成假设标注）。
- `d1_acceptance` 开时 require：`event_clock and timer0_irq4_delivery and
  core_tick_irq3_delivery and repeat`（与 registry requires 一致，运行时守卫照
  key_chain_scan 先例写 require）。

### 4.2 frontier_registry 三条 probe_only（蓝图 §4.2 草案原样）

```python
Feature('d1_acceptance', 'probe_only',
        ('event_clock', 'timer0_irq4_delivery', 'core_tick_irq3_delivery', 'repeat'),
        False, None,
        'D1 验收观察窗场景(park 后 N 模型步,断言 D1-1..5,含 H8 生产者自检与'
        '正式验收入口;d1-acceptance §1;d1-scene-blueprint;d1-window-producer-spec)'),
Feature('d1_window_steps', 'probe_only', ('d1_acceptance',), True, None,
        'D1 观察窗模型步预算(默认=max(S_tick+160k,200k),H8 自检不足自动延长;'
        '显式值覆盖且不延长)'),
Feature('d1_post_inject', 'probe_only', ('d1_acceptance',), True, None,
        "park 后事件注入变体('s18_18' 直调 0x6000cba,显式合成假设,默认关;"
        '行为实现归 PR-4)'),
```

- probe_only 引 conditional 成员=闭包校验允许（key_chain_scan 先例，validate 只禁
  共享栈依赖 probe_only）。**不改** `SCENARIO_LAYERS/PROBE_SCENARIO_NAMES/
  FRONTIERS`（D1 是 probe 场景变体，非共享栈场景、不重钉前沿——任务书 §1.2
  第 6 条）。
- `tools/test_image_trace_diff.py` 调 `validate()` 自动钉参数一致性（irq_case 有参
  数而 FEATURES 无记录=单测红）。

### 4.3 场景行（main() 场景表末尾，参谋结论①(a)）

```python
irq_case(module,app,header,rom,'d1_acceptance_window',
         **scenario_flags('p11_heap_experimental',sys.modules[__name__]),
         event_clock=True,d1_acceptance=True,flash_image_bytes=image),
```

- flag 集=**当前 main 条件栈实取**（构造器+实取，不手抄；B2G 后自动含
  `cfg523_named_fixture=True`/`p2m_mailbox_ack=True`，JLEQ 合并后含其新开关——
  免手抄重核，任务书 §1.2 第 3 条）。**表尾追加**（key_chain_matrix 行之后）。
- **运行集合决策 (ii)**（任务书倾向且加强）：`main()` 加 CLI 旗标
  `--with-d1-window`（默认 False）——场景列表构造按旗标 append D1 行；常规
  `check_local` 全量不含 D1 段（单臂 ~18min，O7E 外推；过线后日常回归用
  assert_preflight+PR-D0 快照验证轮替代）。D1 验收跑=显式旗标+正式验收入口。
  旗标关闭时 D1 行完全不构造（连场景行都不进列表——比「进表但跳过」更干净，
  报告 JSON 无新增键）。

### 4.4 budget 推导（指令步轴）

`irq_case` 的 budget 推导链（L1453-1515）追加一条：

```python
if d1_acceptance: budget=41_000_000   # park 上界 39M(模型步) + N 上界 460k + 余量;
                                       # park>39M 或 N 加长时按实上调(实测为准)
```

- 依据：runbook §2.1——现行执行上限 40M 已由 `jl_adc_start_config` 层推到；D1 场景
  窗口执行另需 N 上界（S_tick≤300k+160k=460k）+ 余量，取 41M 起步。
  **快进段不耗 budget**（指令步轴），40M 模型步 park 的指令步实耗远小——budget
  破耗即 instruction_budget 停=窗口未完成的诚实信号。**[待 PR-2 实测：park 实际
  completed_steps 落定后按实收紧；破耗即调，不预设]**。
- D1 场景的 stop 期望=`window_complete`（模型步计数停，§5）；budget 停出现在
  park 前=前沿未到/预算不足，`window_blocked_events`/pending 语义兜住。

### 4.5 正式验收入口（任务书 §1.2 第 7 条，参谋结论①(c)）

D1 场景行跑完（含 `finalize`）后，probe_irq_entry 侧：

```python
from check_d1_acceptance import run_all
result=run_all(d1_scenario_row)      # d1_scenario_row=报告 scenarios 里的 D1 行
d1_scenario_row['d1_window']['verdicts']={aid:st for st in ...}   # 回填(§2.1)
# main() 退出:d1_gate != 'pass' → sys.exit(1)(字段缺失/截断/窗口未完成=证据不足,同非零)
```

- 断言逻辑单源住在 checker；probe 只加调用与退出码。BI-A* 不进 gate（BI 首验
  独立）。「pending≠pass」语义：insufficient（H8）与键缺都非零退出——修 N 重跑/
  补观察，不是 DUT 失败（d1-health-checks §1 末段）。
- 入口只在 `--with-d1-window` 旗标路线上激活（legacy 路径零调用）。

## 5. H8：输入键、生产者自检与窗口停（任务点 4）

- **checker 输入**（零新增生产键，PR2H8 冻结）：`health.window.steps`（缺则
  `model_step_end−model_step_start`，`_health_window_steps` L1242）+
  `health.per_source[timer0].period`（`_window_cycle_coverage` L1260——按
  source=='timer0' 取首个 int period）。两者都由 §2.2/§2.3 的实读链产生：
  steps←观察器 N（公式/H8 延长/显式覆盖的**实际生效值**）；period←park 态
  `period_closed_form`。
- **生产者自检**（W_park 落定后、窗口起算前，观察器内）：
  1. 算 P_park（§2.2 公式）；None → 不猜，N 按公式下限 200k 走，checker 侧
     H2/H8 对 period 缺=pending（不预锁 40,000 兜底，PR1 冻结语义）。
  2. N_effective = d1_window_steps（显式）否则 max(S_tick+160k,200k)。
  3. **公式 N 且 P_park 实读可用时**：`N_effective // P_park < 3` → 自动延长
     `N_effective = max(N_effective, 3*P_park)`（任务书 §1.2 第 7a 条延长优先；
     BY 三态 20k/16k/4k 与反例 P=70k 全被覆盖——PR2H8 反例 8 例的机检面在
     checker，生产者自检与其同一公式，双保险）。显式 N 不延长（§2.3）。
  4. `health.window.steps` 报 N_effective（契约无原始/生效双键，不发明；延长
     事实由 `window.steps` 值与公式值的差可推——验收记录落档时如实写）。
- **窗口停的机械实现**：观察器每步检查 `advance_calls − W_park_snapshot >=
  N_effective` → 置 `stop={'kind':'window_complete','note':'d1 window exhausted:
  N model steps after W_park','window':{'model_step_start':...,'steps':N}}` 并跳出
  主循环；**事件时钟快进段**（advance_event_clock 内部循环，br28_machine
  L374-397）一次可走数万步——机器侧 horizon 截断：观察器经
  machine_options 注入剩余上限回调（或 advance_event_clock 每步后检查注入的
  停判），到界 `raise Blocked('d1_window_complete',...)` 由 explore 转 stop
  （Blocked→stop 的既有通道，probe_boot L2037）。两处停都归一为
  `stop['kind']='window_complete'`（任务书 §1.2 第 6 条 `stop_kind=
  'window_complete'`+起止计数+实际覆盖长度+两核终态不压成单 PC）。
- **`window_blocked_events`**：窗口已开（W_park 落定）而 stop 非window_complete
  （Blocked/unknown kind）→ 观察器把 stop 记入该列表（D1-2 FAIL 面，蓝图 §2.2
  「旧 kind 也不算过」）；窗口未开的停=前沿未到，正常 pending 语义。
- **验收断言**：checker H8（L1895-1925）三态；probe check() 钉
  `stop.kind=='window_complete'` 或 blocked 非空二选一。

## 6. checker 侧适配点汇总（PR-2 内的 check_d1_acceptance.py 小改）

非禁区文件（任务书红线 1 明示不受限）。共四处，全部服务「D1 场景行上
d1_1..d1_4 判定口径=RBKREV 窗口」：

1. **窗口锚点**（§2.7）：d1_1 `window_parks`/d1_2 `park_boundary`/d1_3
   `snap_entry`+首溢出选择——`d1_window` 在场时用 `d1_window.park.model_step`
   与窗口内过滤；键缺回退现状。BI-A1..A5 零改动。
2. **`window_complete` 合法终点**：d1_2 `no_unknown_stops_in_window`（L630-642）
   的豁免集加 `'window_complete'`（条件同 all_cores_idle_waiting：≥3 窗口内
   周期后自然收尾）；instruction_budget 豁免维持（park 前耗尽=未完成，pending）。
3. **H8/insufficient 的 gate 传播**：零改动（PR2H8 已实现 `d1_gate` pending 态）。
4. **正式入口回填**（§4.5）：run_all 已有，无需改 checker。

单测：每适配点至少 PASS/FAIL 各一形状（§7）。

## 7. 验收清单（PR-2 完成的机检判据）

**单测红绿**（`python3 tools/check_local.py --units` 全绿为退出条件）：

| 测试 | 内容 | 红/绿判据 |
|---|---|---|
| `tools/test_d1_acceptance.py` 既有 86 例 | 适配后全量回归 | 零红（适配不破坏 PR-1/PR2H8/H2SRC 冻结语义） |
| 新增：窗口锚点切分 | 合成样本带 boot 停泊（park_log 首条=boot 停泊）+`d1_window.park.model_step`=S19 park+boot 段 3 次到期 | 未适配形状下 d1_2 会误计 boot 到期（演示判据失效）→ 适配后 d1_2 窗口内到期=真窗口值 |
| 新增：window_complete 豁免 | stop=window_complete+≥3 周期 | no_unknown_stops=pass；<3 周期时依旧红（由 H1/H8 接管） |
| 新增：d1_3 窗口锚 | boot 首溢出早于 W_park 的形状 | 闭式比对锚 W_park 快照+窗口内首溢出；boot 溢出不参与 |
| 新增：emit 纪律 | 观察器单测（合成 machine 形状）：cfg523_installed_at 非 None→keyscan 必在；None→不在；adc_scan 恒在 | 条目集逐形状断言（「cfg523 开+缺键扫→pending」反例的机械保证） |
| 新增：N 公式+H8 延长 | S_tick/period 闭式合成值；P=70k 形状自动延长；显式 N 不延长 | N_effective 逐形状断言 |
| `tools/test_image_trace_diff.py` | registry validate()（三条新 probe_only 闭包+参数一致性） | 零红 |
| 新文件配套单测（建议 `tools/test_d1_window_observer.py`） | 观察器纯逻辑（键面组装/差分/两轴） | 进 units 层 |

**checker 判定路径**（Q1 报告，`--with-d1-window` 跑出）：

```
python3 tools/check_d1_acceptance.py --report analysis/local-checks/irq-entry.json \
    --scenario d1_acceptance_window --json
```

D1-1..D1-5 各 status 进入实质分支（missing 清零）；`d1_gate` 非 pass 即非零退出
（正式入口同判据）。**注意**：PR-2 合并时首验数据未必已有（闸门②刚过、PR-BI
并行）——合并判据=合成样本单测绿+（若 park 已到）真跑一轮的 missing 清零；
五断言全 PASS 属 PR-3（任务书 §1.7）。

**对照与预检**：

- legacy 逐字节不变：stash 对照法（B1 先例）——旧 15+场景各自报告记录逐字节相同
  （新行不构造，整份汇总 JSON 亦不变）；观察器/计数器默认关零字节差。
- probe check() 断言面先 `python3 tools/assert_preflight.py <既有报告 json>` 秒级
  离线预检（CHK 工具，任务书 §1.2 第 8 条）再上全量。
- parity：D1 场景不进 analyze_image_trace 场景集（蓝图 §4.5）——镜像判据/
  freshness 钉子零变更预期；crc_bankb/p11_heap 两场景报告任何字段级变化先解释
  再动钉子。

**反例自证**（AZ 惯例，至少一路实测红）：cfg523 开+人为移除 keyscan emit →
观察器单测红；窗口缩短缺周期 → D1-2/H1 红；H8 余量破形状（P=70k@N=200k 显式）→
insufficient 非 pass。

## 8. 实施风险与冲突面

1. **五文件批冲突（JLEQ 在途）**：JLEQ（JL_EQ CON0 写放行）预计改
   `br28_model.py`（容器类）/`probe_irq_entry.py`（接线+budget）/`
   frontier_registry.py`（conditional 条目+FRONTIERS 重钉）/`analyze_image_trace.py`。
   PR-2 同文件不同区：budget 推导链（同串 if 尾部——**文本冲突热点**）、
   FEATURES 尾部 probe_only 段（冲突面小）、main() 场景表尾（小）。**时序**：
   PR-2 开工时 JLEQ 应已合并（任务前提）；rebase 时预算冲突取两边 max 语义手工合。
   观察器主体在新文件+`br28_machine.py`（非五文件禁区）——侵入面已最小化（决策 B）。
2. **registry 接线**：三条 probe_only 的 requires 引 conditional 成员合法
   （key_chain_scan 先例）；漏登记/漏参数由 validate() 单测钉死。**勿动**
   SCENARIO_LAYERS/FRONTIERS（D1 不重钉前沿——重钉归前沿推进路，任务书 §1.2
   第 6 条；S19MAP §4.5 纪律维持）。
3. **freshness 钉子**：`FRONTIERS['p11_heap_experimental']` 的 completed_steps=
   指令步轴（RBKREV P0），PR-2 不碰；D1 场景的模型步计数（advance_calls）与它
   不同轴，报告内并存不互填（§2.3 两轴纪律）。
4. **br28_model.py 的一行计数器**（§2.1 advance_calls）：无条件内部计数、
   不进 report——零 legacy 报告影响；但该文件是 JLEQ 高频区，若批冲突不可接受，
   退拼合计数方案（§2.1 备选）并把口径差异如实标注。
   `peripherals.timer0.{expiration_model_steps,w1c_acknowledgements}` 若挂
   hardware.report() 会改所有场景报告（破逐字节不变）——**必须**由观察器在
   D1 场景读模型内部状态后只进 `d1_window` 镜像（或 report() 条件带出，
   二选一在实施时按 br28_model 改动最小原则定，倾向前者）。
5. **快照双闸**：PR-2 改仿真源（probe_boot/br28_machine/br28_model）→ 既有快照
   代码指纹失效拒载。时序上 PR-D0 在 PR-3 过线后（任务书 §1.7）——PR-2 期间无
   park canonical 快照可破；PR-4 复用快照起跑的前提=PR-2 之后重新捕获（快照
   双闸机械保证，无需额外动作）。
6. **运行成本**：D1 场景单臂 ~18min（O7E 外推全程+窗口）；运行集合决策 (ii)
   挡在常规全量外；`--jobs` 并行（PAR3）可给 PR-3 验收跑提速（报告逐字节相同）。
7. **观察件的 events 滑窗**：`machine.events` 32 条滑窗截断（record_event
   L160-162）——观察器**不能**依赖 events 判定窗口内行为（rti/唤醒计数须自累计
   或读 park_log/engagements 等不截断新键）；`events_truncated` 语义照 checker
   现状（截断时降级 missing 不误判，d1_1 L439-446 先例）。
8. **d1_post_inject 占位的诚实性**：PR-2 交付的 Blocked('d1_post_inject_pending_
   pr4') 必须在 registry summary 与参数注释里写明（防误用变体跑验收）。

## 9. 红线核对（本路自查）

- 零 tools/ 改动、零 HANDOFF 改动（git 状态可查）；纯新增本文档。
- 键面与消费侧逐键对齐：§2 各表的「checker 判定」列全部给 checker 函数:行号；
  未发明消费侧不认的键（新增观察键=machine/peripherals 面的「路 CH 需求」清单
  原名，REQUIRED_OBSERVATIONS L242-323 逐条对应）。
- 不臆造：P_park/CNT/门控值/id=2/链表节点偏移/TCB state 偏移/tick 换算全部标
  [待 PR-2 实测] 或给实读公式；合成样本不计入过线证据。
- 与冻结契约冲突处理：§2.7 三处适配点属任务书 §1.2 第 7 条授权范围的具体化，
  不改 d1-health-checks §2 键名/语义；若主协调裁决不同（如改走 d1_window 单
  数据面重写 d1_1..d1_4），适配点重写、生产者键面不受影响。
- `check_local --units` 绿（文档路 trivially）。
