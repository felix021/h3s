# D1-5 窗口健康度机检：H1-H8 判定规则与 d1_window.health 键契约（冻结）

2026-11-14 路 PR-1（sim-pr1，批 3 任务书 §1.0b；基线 main `ed241a4`）。本文冻结
两样东西：①`tools/check_d1_acceptance.py` 的 `d1_5_health`（H1-H8）判定规则口径；
②其数据面 `d1_window.health.*` 报告键的最小契约——**PR-2（probe 侧场景骨架，等
闸门②）按本文对齐生产；键名/语义变更须先改本文再改代码**。判据语义照
[d1-scene-blueprint.md](d1-scene-blueprint.md) §3 表 + [s19-batch3-taskbook.md](s19-batch3-taskbook.md)
§1.0b 修正项（H5 两级受检/H6 逻辑序号勘误/FAIL 证据契约），不放宽。

2026-11-16 路 PR-2（sim-pr2，半前移；基线 main `82ba8d6`）按 REVIEW 裁决
（[phase-review-d1-readiness.md](phase-review-d1-readiness.md) §3.1 盲区 #5）增补
**H8 观察窗相容性自检**：只新增判定规则（§1 表 H8 行 + H1 联动），**无新增生产键**
（折算输入全部复用 §2 既有键）；checker+单测先行落地，实跑数据等 D1 场景推进后
自然消费。

2026-11-16 路 H2SRC（sim-h2src；基线 main `3a64ec2`）按 D1S9 §3 / S8SCAN §5 的
建议形态增补 **H2 周期源 keyscan/adc_scan**（sys_timer/Timer0 衍生；RBKREV 复核
P1 #5 缺口消账）：§2 per_source 加 `kind` 判别字段+**周期源表**（四源：
kind/period/三分计数语义/emit 条件性）；checker `PERIODIC_SOURCES` 同步四源——
键扫/adc_scan 条目不再被按非周期源判上界 0 误报风暴；缺源语义分流：**adc_scan
（无条件注册）缺条目=pending、keyscan（cfg523 条件性）缺条目不 pending**。生产者
emit 仍归 PR-2（闸门②后），实跑数据等 D1 场景推进自然消费。

**证据边界（参谋结论③，任务书 §1.0b 第 2 条）**：当前 H1-H7 的单测全部用合成
scenario 样本（`tools/test_d1_acceptance.py` 的 `d1_health_scenario()`），只证明
checker 有分辨力，**不计入 D1 首次过线证据**；实跑数据要等前沿过 DAC_TM0、到达
S19/park 后由 PR-2 的观察件产出。

## 1. 判定规则（checker 侧口径）

三态沿用 check_d1_acceptance 既有契约：pass（全条款有数据且满足）/ pending
（观测未到、键缺、右边界未闭合——pending != pass）/ fail（有数据但违约）。
「窗口未开」（无 `d1_window` 且 park 判据不成立）时 D1-5 整体 pending early-return，
与 D1-1..4 同款；`d1_window` 键在本身即窗口已开的证据（D1 场景前提=真实启动全程）。

| # | 规则 | 输入键（health.*） | PASS 判据 | FAIL 信号（级别） | pending 语义 |
|---|---|---|---|---|---|
| H1 | 定时事件确实发生 | `timer0_expiries`(+`timer0_expiry_steps`) | 窗口内 Timer0 到期 ≥3；到期步号相邻间隔恒定且=per_source 实读 period；与 `d1_window.timer0_cycles` 同数互证 | 到期 <3；间隔漂移/倒流；互证矛盾（全 FAIL 级） | expiry_steps <2 个=间隔面证据不足；timer0_cycles 缺=不互证（不阻塞）；**到期 <3 且 H8 折算覆盖可证 <3=insufficient（pending）非 FAIL**——窗口伪证≠定时器违约（REVIEW §3.1 盲区 #5，2026-11-16 增） |
| H2 | 同源重发有界 | `per_source[]`+`window.steps` | 每周期源交付 ≤ ⌈N/P⌉+1（周期源=timer0/core_tick/keyscan/adc_scan，§2 周期源表；P=per_source.period **实读**，不预锁 40,000/300,000/4×10⁵/2×10⁵）；周期源另含 irq127（RTOS tick 调度器软件 IRQ，≤⌈N/P⌉+1、P=core tick 周期 40,000，无 timer 到期故 expirations=0 属正常形态）；非周期源（irq89 SRC 完成等事件驱动中断）交付 ≤ 小有界常数 4（一次/少数次合法事件非风暴，风暴=无界增长）；三分计数一致（交付≤到期+起点 pending 容差、完成≤交付；sys_timer 源三分坍缩同数，语义见 §2 表）；报告 `upper_bound`（可选）与 checker 自算一致 | 超上界=风暴；交付>到期（+容差）；完成>交付；upper_bound 不符（全 FAIL 级，证据含三分计数） | 周期源 period 缺=无法算上界；window.steps 缺；**core_tick 的 CNT 冻结假设标注缺=断言形状不完整**（§1.4）；**adc_scan 条目缺=观测缺口**（无条件注册必在，S8SCAN §5.2）；**keyscan 条目缺不 pending**（cfg523 条件性，触达才入表，D1S9 §3.3） |
| H3 | 无同事件无限重发 | `event_reposts[]`(+`queue_balance`) | 每 (event_id,子码) 入队 ≤2（S18 静态每子码恰 post 一次，上界含一次容差；注入条目 origin='injected' 上界=3）；消费=入队；queue_balance 与 reposts 汇总一致、出队≤入队 | 入队超上界；消费>入队（重复分发）；queue_balance 矛盾（FAIL 级） | 消费<入队=未闭合样本（右边界条款：`pending_at_window_end` 标注与否都 pending，需要完整样本不默认 PASS）；键缺≠零事件≠vacuous pass |
| H4 | 必要任务可唤醒 | `per_core['0']`（判据面=CPU0；CPU1 记录面） | 窗口内 CPU0 唤醒 ≥1 且末态重新 entered_idle_wait（嵌套唤醒共享一次回等待合法：rewait<wake 不违约）；wake_reasons 求和=wake_count | 零唤醒；唤醒后卡运行态（窗口 complete 且末态 woken/running）；计数矛盾（rewait>wake / reasons 求和≠wake）（FAIL 级，证据含未闭合唤醒区间：最后 PC/在途 IRQ/嵌套层级） | 窗口截断+末态非等待=未闭合区间（「尚未到期」不误 FAIL）；timeline 缺且 rewait<wake（嵌套/未闭合不可分） |
| H5 | 无任务饿死（两级受检） | `ready_tasks[]` | `[]`=扫描过且空→vacuous PASS 并记录；断言面任务（`assertion_face` 字段或 entry==0x6012028）调度 ≥1 | **断言面任务就绪零调度=FAIL 级**（固定优先级无公平性，但 app_core 消费链必须跑） | assertion_face/entry 都缺=无法分级；schedulings 缺 |
| — | 观察级（同 H5 键） | 同上 | 非断言面就绪任务未调度=**如实记录不 FAIL**（等待时长字段 ready_since_step/longest_wait/first_scheduled_step 保留在记录里） | — | — |
| H6 | 队列积压有界+游标前进 | `queue.{count_final,qlimit,capacity,cursor_trace}` | 窗口末 count_final=0（全消费）或 ≤qlimit；**逻辑**入队/出队**序号**严格递增；物理指针=序号 mod 容量（物理回绕合法——勘误：不检查物理指针单调）；`cursor_monotonic`（可选）与 checker 判定一致 | 超限；序号回退；取模不一致；空轨迹+count_final>0；预判定矛盾（FAIL 级） | qlimit 缺且 count_final>0；cursor_trace 缺=游标面证据不足 |
| H7 | 状态变迁可追溯 | `per_core[].timeline` | 每核状态转换流（entered_idle_wait/woken/running）机器可复查：步号严格递增、kind 合法、相邻转移合法（park→只能 woken；woken→running 或直接回等待；running→回等待或嵌套唤醒；连续同 kind=转换流断裂） | 步号倒流；非法 kind；非法转移；CPU0 计数有唤醒而时间线空=断链（FAIL 级，证据含首违前后片段） | timeline 键缺；CPU1 空时间线=静默核 vacuous pass（不 pending） |
| H8 | 观察窗相容性自检（REVIEW §3.1 盲区 #5 裁决；2026-11-16 增） | `window.steps` + `per_source[timer0].period`（**复用 H1/H2 既有键，无新增生产键**） | 按实读 P_park 折算窗口可覆盖完整周期数 `N // P_park >= 3`（最坏相位口径，等价 `N >= 3×P_park`；窗口公式 N=max(S_tick+160k,200k) 的 160k 余量按 P=40,000 形状值预留 3 周期，BY 实测 delay 会按场景重编 PRD 为 59999/47999/11999 三态，任意实读值下须重验） | —（**无 FAIL 级**：覆盖不足是窗口配置问题，不是 DUT 行为违约） | **insufficient_window_coverage**：折算 <3 → pending 拒绝给结论（不判过也不误判 FAIL）；同窗口 H1 的「到期 <3」联动转 pending；P_park/窗口 N 缺=pending 不猜（不预锁 40,000 兜底） |

**否定式总闸**（蓝图 §3 原文）：D1-1..4 全 PASS 而 H1-H8 任一 FAIL → D1 仍 FAIL。
实现=两处：①D1-5 断言自身的 fail 态；②`run_all` 新增 `d1_gate` 键（fail/pass/
pending，只聚合 D1-1..D1-5，BI-A* 不进 gate——BI 首验独立）。probe 侧正式验收
入口（PR-2 §1.2 第 7 条）调 `run_all` 时传播 `d1_gate`。**H8 的 insufficient 属
pending 面（拒绝给结论）**：H1-H7 全 PASS 而折算覆盖 <3 时 D1-5=pending →
`d1_gate`=pending（insufficient）非 pass——总闸不抵消（2026-11-16 增）；验收
入口「非 pass 即非零退出码」语义照旧，窗口配置问题修 N 重跑，不是 DUT 失败。

## 2. `d1_window.health` 最小契约（PR-2 生产者对齐）

窗口口径：`health` 子树全部计数/轨迹限定 **[W_park, W_park+N) 窗口内**（`event_reposts`
与 `queue_balance` 同口径以便互证）。所有键=观察件输出，checker 不信任何预判定
（唯一例外：`cursor_monotonic`/`upper_bound` 作为可选互证键，与 checker 自算不符
=证据矛盾 FAIL）。

```python
'd1_window': {
  'park': {'model_step': N, 'instruction_step': N, 'cores': {'0': {...}, '1': {...}},
           'event_clock_engaged': True,
           'timer0_state':    {'con', 'cnt', 'prd', 'period_closed_form'},   # 蓝图 §4.3
           'core_tick_state': {'cnt', 'prd', 'deadline_closed_form',
                               'assumption': 'core_tick_cnt_held_snapshot:'
                                             'assumed-not-measured (NOT PRD-1)'}},
           # ↑ assumption 键(§1.4 第 2 条)——H2 的 core tick 周期上界承载该假设;
           #   键位两处兼容:d1_window.core_tick_state(任务书 §1.4 原文,顶层优先)
           #   与 d1_window.park.core_tick_state(蓝图 §4.3 示例树)任一存在即可,
           #   PR-2 落地时建议顶层。缺失 → H2 core_tick 子项 pending 非 pass。
  'timer0_cycles': [...],          # D1-2 数据面;与 health.timer0_expiries 同数互证
  'health': {
    'window': {'model_step_start': N, 'model_step_end': N|None,   # W_park 与窗口末端
               'steps': N,            # 窗口长度 N(上界公式 ⌈N/P⌉+1 的 N)
               'complete': bool},     # window_complete;False → 右边界条款生效
    'timer0_expiries': N,             # H1:窗口内到期次数(≥3)
    'timer0_expiry_steps': [N,...],   # H1(建议):窗口内到期全局步号,间隔漂移判据
    'per_source': [                   # H2:每个源一条(timer0/core_tick/keyscan/adc_scan/irq124..127)
      {'source': 'timer0'|'core_tick'|'keyscan'|'adc_scan'|'irq124'|...,
       'kind': 'irq'|'sys_timer',     # 判别字段(缺省='irq';2026-11-16 H2SRC 增):
                                      # sys_timer=Timer0 tick 回调内同步触发的衍生源
                                      # (keyscan/adc_scan),无独立 IRQ 交付——注记性,
                                      # checker 按源名套上界,不以 kind 分流
       'core': N,
       'period': N|None,              # park 态实读周期;周期源必填;非周期源=None
       'expirations': N,              # 三分计数:到期(非周期源=0)
       'deliveries': N,               # 三分计数:交付
       'completions': N,              # 三分计数:完成(handler 执行完/ACK)
       'upper_bound': N|None,         # 可选互证键(checker 自算为准,不符=矛盾)
       'first_deadline': N|None,      # 窗口内首期限全局步号(相位容差记录)
       'pending_at_window_start': bool}],  # 窗口起点 pending 已置位(相位容差记录)
    'event_reposts': [                # H3:每 (event_id,subcode) 一条;窗口口径
      {'event_id': '0x10000c', 'subcode': N,
       'origin': 'firmware-natural'|'injected',
       'posts': N, 'consumed': N,
       'producer_pc': '0x...',        # 首 post 生产者 PC(任务书 §5 问题②「部分」行)
       'first_post_step': N|None,
       'pending_at_window_end': bool}],  # 窗口末仍未消费(右边界标注)
    'queue_balance': {'enqueued': N, 'dequeued': N},   # H3:与 reposts 同窗口口径
    'per_core': {                     # H4/H7:每核一字典(键 '0'/'1')
      '0': {'wake_count': N,
            'rewait_count': N,        # 重新 core_idle_wait_entered 次数
            'wake_reasons': {'timer0': N, 'core_tick': N, ...},  # 计数+关联 id
            'timeline': [             # H7:状态转换流(非逐指令,任务书 §1.0b 第 9 条)
              {'model_step': N, 'kind': 'entered_idle_wait'|'woken'|'running',
               'pc': '0x...', 'irq': N|None, 'nesting': N|None}],
            'last_open_wake': {'last_pc': '0x...', 'in_flight_irq': N|None,
                               'nesting_level': N|None}|None},   # 未闭合唤醒区间
      '1': {...}},
    'ready_tasks': [                  # H5:停点 TCB 扫描 state==ready;[]=扫描过且空
      {'tcb': '0x...', 'entry': '0x...',
       'assertion_face': bool|None,   # 分级:True=断言面(FAIL 级);None 时按 entry 匹配
       'schedulings': N,              # scheduled 布尔由此推导,不单独存(§1.0b 第 9 条)
       'ready_since_step': N|None,    # 等待时长字段(观察,保留)
       'longest_wait': N|None,
       'first_scheduled_step': N|None}],
    'queue': {                        # H6:app_core 消息环队列终态+游标轨迹
      'count_final': N, 'qlimit': N|None, 'capacity': N|None,
      'cursor_trace': [               # 游标转换流(可只存首/末若干条,须按时间序)
        {'step': N|None, 'op': 'enqueue'|'dequeue', 'seq': N, 'physical': N|None}],
      'cursor_monotonic': bool|None}, # 可选互证键(checker 自判为准,不符=矛盾)
  },
}
```

**H2 周期源表**（2026-11-16 路 H2SRC 增；keyscan/adc_scan 形态出处
[s9-keyscan-timer.md](s9-keyscan-timer.md) §2③ / [s8-adc-scan-timer.md](s8-adc-scan-timer.md)
§5——sys_timer 节点挂 Timer0 tick 回调内同步触发，无独立 IRQ 交付/向量/交付帧）：

| source | kind | period（park 实读） | 三分计数语义 | emit 条件性（生产者） |
|---|---|---|---|---|
| timer0 | irq | P_timer0 | 三分（IRQ 交付帧） | 无条件 |
| core_tick | irq | P_core_tick | 三分；上界承载 CNT 冻结假设标注（§1.4） | 无条件 |
| keyscan | sys_timer | 10×P_timer0（tick 数 10 静态；标称 4×10⁵） | **坍缩同数**：deliveries=同步回调调用（0x6001944）=expirations=到期判定命中=completions=回调返回；一致性检查（交付≤到期+相位容差、完成≤交付）照用 | **条件**：cfg523 解析成功才注册；缺条目**不** pending（触达才入表） |
| adc_scan | sys_timer | 5×P_timer0（tick 数 5=立即数；标称 2×10⁵） | 同上坍缩（回调 0x60349d2） | **无条件**（init walker 直线段必注册）；D1 窗口（boot 已完成）内缺条目=观测缺口 → **pending** |

上界公式 `⌈N/P⌉+1` 对四源同一（+1 即相位容差），P 一律 park 实读不预锁（P_timer0
被场景重编时 10×/5× 随之缩放）。sys_timer 回调在 Timer0 交付帧内同步执行、不产生
独立唤醒——H4 的 `wake_reasons` 求和=wake_count 判据对「键扫/sys_timer 唤醒」类
条目自然容纳（枚举不预锁，任务书 §1.3b 第 2 条）；计数采集锚=轮询 0x6066de0 火回调
点+回调入口/返回（D1S9 §4.3/S8SCAN §6.4 观察件纪律，只读旁路）。

砍单项（任务书 §1.0b 第 9 条，勿加回）：不存逐指令 timeline；`scheduled` 布尔由
调度次数推导；`entry` 只进任务元数据表（TCB 复用再加实例代次）；`wake_reasons`
只存计数+关联 id。

**H8 无新增生产键（2026-11-16 增）**：折算输入=`health.window.steps`（缺则
`model_step_end−model_step_start`）+`health.per_source[timer0].period`（park 态
实读，§2 既有键）——键缺=pending 不猜，与全表红线同款。窗口公式本身
（N=max(S_tick+160k,200k)）与 park 落定时的生产者侧自检（覆盖 <3 时延长 N 或报
「观察窗不足」不判过）归 PR-2 probe 侧正式落地，与本表 checker 判定同一公式
`N // P_park >= 3`。

## 3. FAIL 证据契约（七规则共用结构）

每条 FAIL 的 check 附 `evidence` 字段（任务书 §1.0b 第 8 条）：

```python
{'rule': 'H1'..'H7',
 'expected': '<期望值/判据>', 'observed': '<实际值>',
 'first_violation': {'model_step': N|None, 'core': N|None, 'pc': '0x...'|None},
                    # 聚合计数键无步号时为 None 三元组+note(证据完整性如实降级)
 'source_or_task': 'timer0@core0' / '(0x10000c,18)' / 'tcb 0x… entry 0x…' / 'queue',
 'around': [<前一条记录>, <违规条目>, <后一条记录>],   # 仅轨迹类规则;只存首违
 'worst': {...},                                        # 最严重违规(可选,单违时同首违)
 '<rule 特化>': H2: counts 三分计数/derived_upper_bound/first_deadline/
                     pending_at_window_start;H4: open_wake{last_pc,in_flight_irq,
                     nesting_level};H5: wait_fields;H6: seq_regressions/modulo_
                     mismatches/capacity;H7: violation 类别
 'window_end': {'complete': bool|None, 'model_step_start': N|None,
                'model_step_end': N|None, 'evidence_complete': bool}}
                # 窗口末尾状态+证据完整性(complete 字段缺失=evidence_complete False)
```

计数/峰值/最长等待在线累计在原始观测键里，证据片段只存首次违规+最严重违规。
H8 复用同一结构（`rule='H8'`，2026-11-16 增）：`first_violation` 锚窗口配置点
（model_step=W_park，core/pc=None+note——配置级条件无故障指令，如实降级）；
rule 特化键=`{coverage, p_park, n, required_n_min}`。

## 4. 反例自证（AZ 假阳性框架；单测钉死）

`tools/test_d1_acceptance.py` 新增 36 例（`D1_5*` 五个测试类），全部构造违约输入
验证机检必红/边界不误判：

- 每规则 FAIL 形状：H1 到期不足/间隔漂移/互证矛盾；H2 Timer0 风暴(deliveries=12>
  上界 6)/软件 IRQ 交付>0；H3 重发超界/重复消费；H4 零唤醒/卡运行态；H5 断言面
  饿死（含 assertion_face 字段缺、按 entry==0x6012028 兜底分级两形状）；H6 积压
  超限/逻辑序号回退/取模不一致；H7 步号倒流/连续双 park/计数有时间线空。
- 总闸红：`test_negation_gate_h_fail_keeps_d1_failed`——D1-1..4 全 PASS + H1 FAIL
  → `d1_gate='fail'`（fail 态不抵消）。
- pending 语义：键缺≠零事件≠PASS（event_reposts 删除不得 vacuous pass）；core tick
  period 缺不预锁 300,000；CNT 假设字段删除 → pending；窗口截断+末态 running=
  未闭合 pending 不误 FAIL；消费<入队=未闭合 pending。
- 勘误口径验证：H6 物理指针回绕（seq 6..9 → physical 6,7,0,1）合法=pass；H5 非
  断言面未调度=观察级 pass；H4 嵌套唤醒共享一次回等待=pass。
- 证据契约形状：H1 证据含 rule/expected/observed/first_violation{model_step,core,
  pc}/source_or_task/window_end.evidence_complete；H2 证据含三分计数+derived_
  upper_bound+first_deadline+pending_at_window_start；H4 卡运行态证据含 open_wake
  {last_pc,in_flight_irq,nesting_level}；H7 证据含首违前后片段。

**H8 反例自证**（2026-11-16 路 PR-2 增，`tools/test_d1_acceptance.py` 新增 8 例，
`D1_5H8WindowCoverageTests` 一类；REVIEW §3.1 盲区 #5 裁决的机检面）：

- 三态 PRD 各造窗口边界例：BY 实测三态（59999/47999/11999 → P=20,000/16,000/
  4,000）逐一验证「覆盖恰 3（N=3×P）=pass／恰 2（N=3×P−1）=拒绝给结论」。
- 余量破（盲区本体）：PRD 被重编出更大周期（P=70,000）而 N 停在公式下限
  200,000（3×P=210,000>N）→ insufficient_window_coverage、D1-5 零 FAIL、
  `d1_gate`=pending——到期 2 次是窗口伪证，H1 不再误判「定时事件没真发生」。
- P_park 未实读=pending 不猜：period 键缺不得回退 40,000 形状值折算（不猜
  红线照抄 H2 CNT 假设先例）；窗口 N 键缺（steps 与 end−start 两路都断）同款。
- 拦截不放宽（双向不猜）：覆盖足额（恰 3）时观测缺口就是真实缺口，H1 照旧
  FAIL——PR1 冻结语义只在「折算可证 <3」时让位。
- 总闸不抵消：H1-H7 全 PASS（幸运相位凑满 3 次到期）而折算 <3 → D1-5=pending、
  `d1_gate`≠pass。
- 证据契约形状：H8 拒绝结论的证据=七规则结构（rule/expected/observed/
  first_violation 锚 W_park 且 pc=None/source_or_task/window_end.evidence_
  complete/coverage+p_park+required_n_min 特化键）。

**H2 周期源补齐反例**（2026-11-16 路 H2SRC 增，`tools/test_d1_acceptance.py` 新增
8 例，`D1_5H2SysTimerSourcesTests` 一类；RBKREV 复核 P1 #5 的机检面）：

- 正例：keyscan/adc_scan 在场且计数自洽=pass（keyscan P=4×10⁵>N 窗口内 0 到期
  合法；adc_scan 三分同数坍缩）；`kind` 字段缺省（注记性）不 pending。
- 负例：**adc_scan 缺条目=pending**（无条件注册必在，键缺≠零事件）；**keyscan 缺
  条目=不 pending**（cfg523 条件性，D1S9 §3.3「触达才入表」）；超上界 FAIL
  （keyscan bound=1 下 deliveries=3；adc_scan bound=2 下 deliveries=4）；period
  键缺=pending（不预锁 4×10⁵/2×10⁵ 兜底）；completions>deliveries（同步回调
  不可能）按既有三分一致性 FAIL。

## 5. 与 PR-2 的对接面（等闸门②，本路不实施）

- 生产者按 §2 契约实现观察件（probe_irq_entry 场景 `d1_acceptance_window` 的
  post_stop 观测块）；`d1_5_health` 只消费 `scenario['d1_window']`，不读
  machine/peripherals 面板的重复信息（唯一例外=窗口未开时的 park_window 通用判据）。
- PR-2 §1.2 第 7 条正式验收入口调用 `run_all` 后传播 `d1_gate`（非 pass 即非零
  退出码）；「字段缺失/截断=证据不足」即本文 pending 语义，probe 侧不得当零事件。
- CNT 假设标注的键位决策（顶层 vs park 下）由 PR-2 定夺后可在本文 §2 更新——
  checker 已两处兼容，无需再改代码。
- H8（2026-11-16 增）：checker 侧已上线（半前移，本路 PR-2）；PR-2 窗口机制侧
  在 park 落定后按实读 P_park 自检覆盖（<3 延长 N 或报「观察窗不足」不判过），
  与本表 checker 判定同一公式 `N // P_park >= 3`；生产者无需为此新增任何键。
- H2 周期源（2026-11-16 路 H2SRC 增）：生产者按 §2 周期源表的 emit 条件性实现
  （adc_scan 无条件必 emit、keyscan 挂 cfg523 条件栈，不硬编码「必有键扫源」，
  D1S9 §3.3/S8SCAN §5.2），计数锚=轮询 0x6066de0 火回调点+回调入口/返回
  （D1S9 §4.3/S8SCAN §6.4，只读旁路）；checker 侧上界/缺源语义已就位，生产者
  照表 emit 即对上，无需再改 checker。
