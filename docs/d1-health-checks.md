# D1-5 窗口健康度机检：H1-H7 判定规则与 d1_window.health 键契约（冻结）

2026-11-14 路 PR-1（sim-pr1，批 3 任务书 §1.0b；基线 main `ed241a4`）。本文冻结
两样东西：①`tools/check_d1_acceptance.py` 的 `d1_5_health`（H1-H7）判定规则口径；
②其数据面 `d1_window.health.*` 报告键的最小契约——**PR-2（probe 侧场景骨架，等
闸门②）按本文对齐生产；键名/语义变更须先改本文再改代码**。判据语义照
[d1-scene-blueprint.md](d1-scene-blueprint.md) §3 表 + [s19-batch3-taskbook.md](s19-batch3-taskbook.md)
§1.0b 修正项（H5 两级受检/H6 逻辑序号勘误/FAIL 证据契约），不放宽。

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
| H1 | 定时事件确实发生 | `timer0_expiries`(+`timer0_expiry_steps`) | 窗口内 Timer0 到期 ≥3；到期步号相邻间隔恒定且=per_source 实读 period；与 `d1_window.timer0_cycles` 同数互证 | 到期 <3；间隔漂移/倒流；互证矛盾（全 FAIL 级） | expiry_steps <2 个=间隔面证据不足；timer0_cycles 缺=不互证（不阻塞） |
| H2 | 同源重发有界 | `per_source[]`+`window.steps` | 每周期源交付 ≤ ⌈N/P⌉+1（P=per_source.period **实读**，不预锁 40,000/300,000）；非周期源（软件 IRQ124-127）交付=0；三分计数一致（交付≤到期+起点 pending 容差、完成≤交付）；报告 `upper_bound`（可选）与 checker 自算一致 | 超上界=风暴；交付>到期（+容差）；完成>交付；upper_bound 不符（全 FAIL 级，证据含三分计数） | 周期源 period 缺=无法算上界；window.steps 缺；**core_tick 的 CNT 冻结假设标注缺=断言形状不完整**（§1.4） |
| H3 | 无同事件无限重发 | `event_reposts[]`(+`queue_balance`) | 每 (event_id,子码) 入队 ≤2（S18 静态每子码恰 post 一次，上界含一次容差；注入条目 origin='injected' 上界=3）；消费=入队；queue_balance 与 reposts 汇总一致、出队≤入队 | 入队超上界；消费>入队（重复分发）；queue_balance 矛盾（FAIL 级） | 消费<入队=未闭合样本（右边界条款：`pending_at_window_end` 标注与否都 pending，需要完整样本不默认 PASS）；键缺≠零事件≠vacuous pass |
| H4 | 必要任务可唤醒 | `per_core['0']`（判据面=CPU0；CPU1 记录面） | 窗口内 CPU0 唤醒 ≥1 且末态重新 entered_idle_wait（嵌套唤醒共享一次回等待合法：rewait<wake 不违约）；wake_reasons 求和=wake_count | 零唤醒；唤醒后卡运行态（窗口 complete 且末态 woken/running）；计数矛盾（rewait>wake / reasons 求和≠wake）（FAIL 级，证据含未闭合唤醒区间：最后 PC/在途 IRQ/嵌套层级） | 窗口截断+末态非等待=未闭合区间（「尚未到期」不误 FAIL）；timeline 缺且 rewait<wake（嵌套/未闭合不可分） |
| H5 | 无任务饿死（两级受检） | `ready_tasks[]` | `[]`=扫描过且空→vacuous PASS 并记录；断言面任务（`assertion_face` 字段或 entry==0x6012028）调度 ≥1 | **断言面任务就绪零调度=FAIL 级**（固定优先级无公平性，但 app_core 消费链必须跑） | assertion_face/entry 都缺=无法分级；schedulings 缺 |
| — | 观察级（同 H5 键） | 同上 | 非断言面就绪任务未调度=**如实记录不 FAIL**（等待时长字段 ready_since_step/longest_wait/first_scheduled_step 保留在记录里） | — | — |
| H6 | 队列积压有界+游标前进 | `queue.{count_final,qlimit,capacity,cursor_trace}` | 窗口末 count_final=0（全消费）或 ≤qlimit；**逻辑**入队/出队**序号**严格递增；物理指针=序号 mod 容量（物理回绕合法——勘误：不检查物理指针单调）；`cursor_monotonic`（可选）与 checker 判定一致 | 超限；序号回退；取模不一致；空轨迹+count_final>0；预判定矛盾（FAIL 级） | qlimit 缺且 count_final>0；cursor_trace 缺=游标面证据不足 |
| H7 | 状态变迁可追溯 | `per_core[].timeline` | 每核状态转换流（entered_idle_wait/woken/running）机器可复查：步号严格递增、kind 合法、相邻转移合法（park→只能 woken；woken→running 或直接回等待；running→回等待或嵌套唤醒；连续同 kind=转换流断裂） | 步号倒流；非法 kind；非法转移；CPU0 计数有唤醒而时间线空=断链（FAIL 级，证据含首违前后片段） | timeline 键缺；CPU1 空时间线=静默核 vacuous pass（不 pending） |

**否定式总闸**（蓝图 §3 原文）：D1-1..4 全 PASS 而 H1-H7 任一 FAIL → D1 仍 FAIL。
实现=两处：①D1-5 断言自身的 fail 态；②`run_all` 新增 `d1_gate` 键（fail/pass/
pending，只聚合 D1-1..D1-5，BI-A* 不进 gate——BI 首验独立）。probe 侧正式验收
入口（PR-2 §1.2 第 7 条）调 `run_all` 时传播 `d1_gate`。

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
    'per_source': [                   # H2:每个 IRQ 源一条(timer0/core_tick/irq124..127)
      {'source': 'timer0'|'core_tick'|'irq124'|...,
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

砍单项（任务书 §1.0b 第 9 条，勿加回）：不存逐指令 timeline；`scheduled` 布尔由
调度次数推导；`entry` 只进任务元数据表（TCB 复用再加实例代次）；`wake_reasons`
只存计数+关联 id。

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

## 5. 与 PR-2 的对接面（等闸门②，本路不实施）

- 生产者按 §2 契约实现观察件（probe_irq_entry 场景 `d1_acceptance_window` 的
  post_stop 观测块）；`d1_5_health` 只消费 `scenario['d1_window']`，不读
  machine/peripherals 面板的重复信息（唯一例外=窗口未开时的 park_window 通用判据）。
- PR-2 §1.2 第 7 条正式验收入口调用 `run_all` 后传播 `d1_gate`（非 pass 即非零
  退出码）；「字段缺失/截断=证据不足」即本文 pending 语义，probe 侧不得当零事件。
- CNT 假设标注的键位决策（顶层 vs park 下）由 PR-2 定夺后可在本文 §2 更新——
  checker 已两处兼容，无需再改代码。
