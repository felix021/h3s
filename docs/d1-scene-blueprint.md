# D1 验收窗口场景蓝图：d1_acceptance 的场景骨架、五断言观察点与 registry 草案（路 BE / sim-d1scene）

2026-11-14 路 BE（sim-d1scene，worktree 基线 `93e3659`=路 BA 合并点；前沿现状引用主仓
main `adb996e`：条件前沿 **3,170,362 步 @ 0x606b426 读 0xfe0a0c（DAA_CON1，首个硬件反馈
依赖，AS 放行 ADC reset 写族后的新停点，codex 参谋中）**。**字母说明**：任务文指定「路
BE」与早期 sim-timer0irq 的 BE（Timer0 IRQ4 交付，HANDOFF「已实现」）重名——照 CT 先例
（任务文标 AV 与仓内冲突→合并时顺延），合并时由主会话定夺是否顺延字母，本文以 BE 自称）。

本页把 [d1-acceptance.md](d1-acceptance.md) §1 的 D1-1..D5 五断言 + BA 的 S19 触达点预标
（[s19-preboundaries.md](s19-preboundaries.md) §6.2）+ BZ 的 S18 消费链全图
（[d1-s18-consumption.md](d1-s18-consumption.md)）+ BI 的事件时钟断言
（[d1-idle-event-clock.md](d1-idle-event-clock.md) §4）落成**实施路拿到即可开工**的场景
规格书：场景骨架与观察窗定义、五断言逐条观察点表（锚点/报告键/PASS-FAIL 判据）、D1-5
健康度的机器可检查规则、registry 条目与场景参数草案、前置依赖清单（BA 16 行预测表逐条
挂接）、验收记录模板（D1-01..05）、HANDOFF 条目。**本路是设计文档，不是实施**：未改
tools/ 任何代码、未跑任何改动场景；所有规格以已合并文档为据（出处随行标注），未钉死的
值一律标「运行观测项」，不臆造。

证据强度沿用各源文档口径（[静态]=指令锚定反汇编、[trace]=既有报告实测、[文档]=已合并
docs/HANDOFF 条目、[推断]=上述链推出）；本文新增的工程决策（场景名/开关名/报告键名/
判据数值）标 **[设计]**——实施路落地时按运行观测复核，发现不符即改蓝图再动手。

## 0. 前提与锚点速查（全表出处）

| 事实 | 值/锚点 | 出处 |
|---|---|---|
| D1 定义与五断言原文 | D1=启动完成并进入稳定调度；D1-1 消息循环阻塞 / D1-2 Timer0 连续运转 / D1-3 core tick 效果 / D1-4 消费闭环 / D1-5 窗口健康度；codex 反例原样保留在各断言条 | d1-acceptance §1 [文档] |
| 时间口径 | 周期断言用驱动 Timer0 的同一全局模型步轴（含事件时钟快进段），指令步数另列只作执行预算 | d1-acceptance §1（codex）[文档] |
| S19 循环结构 | 循环头 0x60123e6；`0x60123ea call 0x6064c8c(sp,16)`；`0x60123f0 if r0!=13` 重调；`0x60123f6` bit20 过滤；`0x60123fa` ==0x10000c 特路 `0x60108fe`；循环永不退出 | d1-acceptance §2.1 + BA §6.2 [静态] |
| 分发顺序（精确） | ①表 2（0x60a8c68..0x60a8cb0，6 条 12B 记录，含按键 handler 0x060252c6 记录[5]）→ ②RAM 动态过滤链 `[0x126f4c]`（BA 实测 0=跳过；非 0 时 call [r5+12]→[其+24] 且表 1 filter 参与比对）→ ③表 1（0x60a89e0..0x60a8c68，54 条）；一条事件可命中多 handler（返回 0 续走） | BA §6.2 + BZ §1.4 [静态+采样] |
| handler 调用形状 | `0x601241a-1e` 并行对：A 半 `r0=sp+4`（载荷指针）‖B 半 `r1=[rec+8]`，`call r1`；非 0 返回=本事件分发结束回循环头 | BA §6.2 + BZ §1.4 [静态] |
| app_core taskq 面 | 任务表 `[0x126c5c+cnum<<2]`（cnum=0）→TCB；队列头/尾 `h[TCB+12]/h[TCB+14]`、计数 `h[TCB+6]`；TCB 地址=运行时值（停点机器可读） | BA §6.2 [静态] |
| **空队列 pend 写面** | `0x6064cf6..0x6064d06`：`TCB+48/b+1 \|=0x30`、`TCB+49`、`h[TCB+6]=r12`；让出链 `0x6064188/0x60642bc/0x6064340` | BA §6.2 [静态] |
| S18 预置事件 | `0x60123aa/0x60123b2 call 0x6000cba(18,0)/(20,2)` → `0x6064978('app_core'@0x60a1d11, 0x10000C, 2 字载荷)`；**无 id 变换**（0x6000cba 硬编码 0x10000C，18/20=载荷子码）；入队 slot 头字 0x10000C03 | BZ §1.1/§1.2 [静态] |
| (18,0) 消费链 | 特路子码 18 直通 → 表 2 无认领 → 表 1[39] **0x6023962** 认领（0x6023972 子码门 → 0x60239b2 call 0x60237d8 → 门控 `b[0x125B54]`/`b[0x127184]`，关=短闭合返回 0） | BZ §1.3/§1.5 [静态+采样] |
| (20,2) 消费链 | 特路子码 20 → 查 0x126F3C 链表 id=2 调节点回调；**id=2 节点存在性=运行观测项**（缺失则 0x6010946 `b[0]` 读撞 unmapped 诚实停，独立边界非链路不通） | BZ §1.3 [静态+采样] |
| Timer0 回调表 | `[0x126F6C]` 恒非空（~110.5k 起=0x603622C，tick_timer_init 0x6066d86 写）；tick 回调每周期：`[0x1268CC]`+1、`[0x1268D0]+10ms`、sys_timer 轮询 0x6066de0 | BZ §2 [静态+采样] |
| Timer0 交付机制 | pending→IRQ4 合格→IrqDeliveryRequest(0,4)→`timer0_irq_entry/banks/rti` 三件套→handler 0x12ece8 真实字节→W1C bit14（\|=0x4000）清 pending→RTI 尾锚 0x12ed0c 回实际 RETI | HANDOFF 路 BE [文档] |
| core tick 交付机制 | `core_tick_irq3_delivery`+机器三件；handler 0x60661a4 帧；ACK \|=0x40；**软件 jiffies [0x125004] mod 2³² 恰 +1**；时间片调度 call **0x60653AE** 真实执行并返回 | d1-acceptance §1 D1-3 + HANDOFF 路 BF [文档] |
| 事件时钟契约 | 全核 IDLE 逐模型步 advance() 到最近已建模事件；Timer0 期限闭式 `S=ceil((4·M−source_remainder)/12)`、`M=divisor·(PRD+1-CNT)−remainder`；同刻序=advance 扫描序；masked pending 不唤醒；无事件诚实停 all_cores_idle_waiting | event-clock.md §2 [文档] |
| BI 断言 A1-A5 | 首事件按闭式精确到期/周期 40,000 模型步守恒/无事件零推进/期限缺口反例/C2 两级退出 | d1-idle-event-clock §4 [文档] |
| park 路径与终态 | CPU1 已停泊 0x12c5ca；CPU0 经调度器切 idle 任务 0x12c57e→0x12c5ca；park 时刻 IPMASK=0、IRQ4 合格；`event_clock_engaged` | d1-idle-event-clock §1.2/§1.4 [静态+推断] |
| 金丝雀负例 | K1 `0x601249c`/K2 `0x60124ae` 阈值/旗标静态零（BA 修正：K1 阈值=h[0x127734]、K2 触发=世界缺件）；到达即方向错误信号 | d1-acceptance §1 D1-1 + tail-map §4 + BA §4.6 [文档] |
| 停点预测 16 行+五预案 | BA §5.1 表 #0..#15（AS 放行终点→S19 完整序列）；§5.2 三选一：P1/P2/P5 PVDD 容器、P4 TIMER2 完成投影、P6 P2M_CLK ack fixture、P7 config-523 fixture | BA §5 [静态+动态复现] |
| 直调/注入先例 | post_stop 回调拿活世界+key_chain_direct_call 受控步进（哨兵 0x100000、保存/恢复 pc/sp/rets/flags/R0-R15、不调 advance） | peripheral-input-simulation §7 [文档] |
| 快照设计（执行形态选项） | D1-entry canonical 快照@park 点→窗口重放秒-分钟级；advance-tier 等价绿才有效 | snapshot-design.md §4.3 [文档] |
| 验收记录工具 | `tools/acceptance_record.py`：九栏+证据等级，id 正则 `^[A-Z]\d{1,2}(-[a-z])?(-[A-Z][A-Z0-9]{1,5})?-\d{2}$` | d1-acceptance §8/§8.1 [文档] |

## 1. 场景骨架：世界线、场景栈与观察窗

### 1.1 进入路径取舍 [设计]

| 维度 | 路径 Q1：真实启动全程（推荐） | 路径 Q2：快照 resume（BD 落地后升级） | 路径 Q3：直调合成进入（否决） |
|---|---|---|---|
| 做法 | p11_heap 条件栈（BA 五预案全落地后的新 flag 集）自然跑到 park，观察窗从 W_park 起算 | 从 park 点的 D1-entry canonical 快照恢复（snapshot-design §4.3 消费者③），窗口重放 | 手工构造 S19 世界状态后起跑 |
| 依据 | d1-acceptance §7/§8：**D1 正式验收必须从真实启动进入**（「起始状态」栏记真实启动），§7 明文「不能靠手工构造最终 OS 状态」 | §8 允许「可追溯快照」；快照等价由 BD §3 对照证明背书 | 违反 §7 |
| 成本 | ~3.4M+ 指令步/次（≈3-4 分钟） | 秒级恢复+窗口步 | — |
| 采用 | **D1 首次过线必须走 Q1**（九栏「起始状态」=真实启动）；过线后生成 park 快照，回归/复核走 Q2（等价绿为前提） | Q2 为 Q1 的加速复跑形态 | 否决 |

与 AW 蓝图（d2b-injection-blueprint §1.1 P1/P2）的差别点如实说明：D2-b 场景可以停点后直调
开链（断言面是开链以下），D1 场景**没有这个自由度**——D1 的断言对象（进入 S19/park/
稳定调度）本身就是启动路径的产物。唯一合成件是 D1-4 的 park 后注入变体（§2.4，opt-in
默认关，标注显式合成假设）。

### 1.2 场景栈与开关层叠 [设计]

```
p11_heap_experimental_config 条件栈（BA 五预案落地后的新 flag 集，前沿=S19/park）
 ├─ base 栈 19 开关（照旧，registry base_flags()）
 ├─ conditional 栈（含五预案新开关 + event_clock [设计：S16 的 os_time_dly 等待
 │   已要求事件时钟进条件栈——BA §5.1 #12 行明确「事件时钟」列；见 §5 前置表）
 ├─ Timer0 交付三件 timer0_irq4_delivery（已在 conditional）
 ├─ core tick 交付 core_tick_irq3_delivery（已在 conditional）
 └─ D1 增量（probe_only，本蓝图新增）：
     d1_acceptance=True      # 观察窗场景开关（BI G3 接线的超集）
     d1_window_steps=N       # 观察窗模型步预算（值开关）
     d1_post_inject=None     # 可选 's18_18'：park 后注入 (18,0) 第二样本（值开关，默认关）
```

**BI 与 D1 场景的关系**：BI（全核 IDLE+事件时钟首验，路线梯 BI）的 G3 场景接线
（event_clock 进 irq_case+真实前缀首验，BP §3.2）是本场景的直接前置——BI 场景=同一 flag
集+观察窗下限（≥80,000 模型步=两个 Timer0 周期）；D1 场景=BI 场景拉长窗口+BJ 断言包
（D1-1..5 机读化）。实施顺序：BI 先落（A1/A2/A3 断言），D1 场景在其上叠 D1-3/4/5。

### 1.3 观察窗定义与预算（时间口径）[设计]

- **W_park**：两核均在 idle_wait（`event_clock_engaged` 首次）的全局模型步时刻；同时记
  指令步口径的对应值（两轴并记，d1-acceptance §1 时间口径）。
- **观察窗 = [W_park, W_park+N)**，N 默认 **S_tick(W_park)+160,000**，下限 200,000：
  - 3×Timer0 周期（≥120,000，D1-2）；
  - core tick 首溢出闭式期限 S_tick（从 park 态 CNT/PRD 推出，≤300,000，D1-3）+其交付
    执行+交付后再覆盖 ≥1 个完整 Timer0 周期（d1-acceptance §1 D1-2「分发之后」条款的
    D1-3 版适用）。
- **窗口步数只按全局模型步计**（含事件时钟快进段）；窗口内执行指令数另列
  （`d1_window_instruction_steps`）——不冒充时间（§8 记录格式同口径）。
- **超时=FAIL**：N 耗尽而断言未齐=FAIL（NOT_RUN 语义只给 D1-4 无样本的诚实收口，§2.4），
  不是重跑理由（d1-acceptance §8 超时预算条款）。
- **周期常数的诚实口径** [设计]：40,000 步=Timer0「10ms tick 模式」（PRD=119,999、
  STD12M、divisor=1）的形状值；BY 实测 delay 函数会按调用重编 PRD，park 时的实际配置以
  停点读回为准——**D1-2 判据用「从 park 态推出的实际周期 P_park」**（期望形状=P_park=
  40,000；若实读不同，按实际周期核对「间隔恒定」并如实报告，BI A2 的「配置不变时」
  条款照抄）。

## 2. 五断言观察点表（锚点/报告键/判据）

观察件的公共机制 [设计]：probe 运行循环对一组**具名锚点 PC** 记执行计数+首达步数
（coverage 面，key_chain 场景先例）；事件流（`core_idle_wait_entered`/
`core_woken_by_delivery`/`timer0_irq_entry`/`*_rti` 等）已有 events 记录；全局模型步计数器
（advance() 总调用数，含事件时钟段）为新增报告键 `d1_model_steps_total`（BI G4 的 horizon
构成键在此推广为窗口全程）。

### 2.1 D1-1 真实进入消息循环并阻塞

| 观察点 | 锚点/来源 | 报告键草案（`d1_window.*`） |
|---|---|---|
| S19 循环头首达 | PC 0x60123e6（BA §6.2） | `loop_head_first_step` |
| get-event 调用+成功码消费 | 0x60123ea call 0x6064c8c；0x60123f0 `r0!=13` 重调（13=取得事件） | `get_event_calls`, `get_event_returns_13` |
| S18 首批消费如实记录 | (18,0)→表1[39] 0x6023962；(20,2)→特路 sys_timer 0x126F3C id=2（BZ §1.5/§1.3） | `s18_first_pass[0/1].{subcode,handler_hits,return}` |
| bit20 过滤/特路 | 0x60123f6 / 0x60123fa→0x60108fe | `dispatch.filter_bit20_hits`, `dispatch.special_path_taken` |
| 分发顺序 | 表 2（0x60a8c68 起 6 条）→[0x126f4c]（期望 0=跳过）→表 1（0x60a89e0 起 54 条）（BA §6.2） | `dispatch.table2_records_walked`, `dispatch.dynamic_filter_ptr`, `dispatch.table1_records_walked` |
| **空队列 pend 写面** | 0x6064cf6..0x6064d06（TCB+48/b+1\|=0x30、TCB+49、h[TCB+6]=r12）+让出链 0x6064188/0x60642bc/0x6064340（BA §6.2） | `empty_queue_pend.{write_face_pcs,yield_chain_pcs,tcb,queue_head,queue_count}` |
| pend 不返回 | 循环头 0x60123e6 在空队列后不再到达（直至新事件） | `loop_head_count_after_drain`（期望=0 或=注入后回升） |
| 两核终态 | CPU1 0x12c5ca；CPU0 0x12c57e→0x12c5ca（idle 切换）；`event_clock_engaged`（BA §6.2+BP §1.4） | `park.{core0,core1}.{pc,via}`, `park.event_clock_engaged`, `park.model_step` |
| 金丝雀负例 | K1 0x601249c / K2 0x60124ae | `canaries.{k1,k2}`（期望 0/0） |

**PASS 判据** [设计]：①循环头首达+S18 首批消费链每个站点有执行证据；②空队列后 pend
写面四写+让出链执行，循环头停达；③两核各有代码依据的状态（park 是预期形态非断言形状
——d1-acceptance §1 D1-1 codex 条款；若一核持续跑应用任务同样按实记录判定）；④
`event_clock_engaged` 成立；⑤K1/K2 计数=0。
**FAIL 判据**：任一站点缺失；pend 写面执行后循环头继续到达且队列计数>0（pend 假阻塞）；
K1/K2>0（方向错误信号，BA §4.6：fixture 世界的语义=世界缺件暴露，FAIL 并停机归因）。

### 2.2 D1-2 Timer0 连续运转与交错稳定（窗口内 ≥3 次完整周期）

| 观察点 | 锚点/来源 | 报告键草案 |
|---|---|---|
| 到期 | Timer0 pending/expirations（模型报告；BI E1） | `timer0_cycles[i].{expiry_model_step}` |
| 交付 | IrqDeliveryRequest(0,4)→`timer0_irq_entry`→handler 0x12ece8 真实字节（HANDOFF 路 BE） | `timer0_cycles[i].{delivered,handler_entry_pc,frames}` |
| ACK | W1C bit14（\|=0x4000）恰一次、delivery_in_flight 复位 | `timer0_cycles[i].{acked,w1c_count}` |
| RTI | 尾锚 0x12ed0c→实际保存的 RETI→重新 `core_idle_wait_entered`（BI A1） | `timer0_cycles[i].{rti_pc,reti_value,re_parked}` |
| 周期间隔 | 相邻到期模型步差=P_park（§1.3 口径；期望 40,000）（BI A2） | `timer0_cycles[i].gap_from_previous` |
| tick 回调真实效果 | 0x603622C：[0x1268CC]+1、[0x1268D0]+10、sys_timer 轮询（BZ §2.2/2.3） | `timer0_cycles[i].{tick_count_delta,tick_ms_delta,sys_timer_polled}` |
| 交付延迟另记 | 溢出落 IPMASK 临界区时经 pending 重检延后（BF 语义），不要求交付间隔=P | `timer0_cycles[i].delivery_delay_steps` |
| 无未知停点 | 窗口内零 Blocked（**旧 kind 再阻塞也不通过**，d1-accept §1 D1-2 codex 条款） | `window_blocked_events`（期望空） |
| 分发后再覆盖 | 窗口内若发生消息分发（注入变体/D1-3 后调度的事件），其后 ≥1 完整周期 | `post_dispatch_cycle_covered` |

**PASS**：≥3 周期逐周期四查（到期/交付/ACK/返回）全过、间隔恒=P_park、tick 回调效果核对、
窗口零 Blocked。**FAIL**：任一周期缺件/重复（漏失=少一次完整往返、重复=同周期双交付）、
间隔漂移（配置未变而 gap≠P）、任何 Blocked。

### 2.3 D1-3 core tick 的实际效果

| 观察点 | 锚点/来源 | 报告键草案 |
|---|---|---|
| 首溢出期限 | 从 park 态闭式 S=ceil 公式（event-clock §2 条 3；BI A1 同款「不预锁常数」） | `core_tick.deadline_closed_form`, `core_tick.actual_overflow_model_step` |
| 交付资格 | 按当时 IRQ 资格与优先级（pending 期间逐步重检，BF；park 时 IPMASK=0 合格——BP §1.2） | `core_tick.{eligible_at_overflow,delivery_delay_steps}` |
| handler | 0x60661a4 帧真实执行（HANDOFF 路 BF） | `core_tick.handler_entry_pc` |
| ACK | \|=0x40 恰一次 | `core_tick.ack_count` |
| jiffies | [0x125004] mod 2³² 恰 +1（路 BF 反汇编钉死） | `core_tick.jiffies_before/after` |
| 时间片调度 | call 0x60653AE 真实执行并返回 | `core_tick.timeslice_call.{executed,returned}` |
| 任务切换 | 观察项非硬断言：若触发切换，按实际恢复上下文记录（TCB/帧），不强求回原任务（d1-accept §1 D1-3 codex 条款） | `core_tick.task_switch.{occurred,from_tcb,to_tcb}` |

**PASS**：期限=闭式（差 0，允许 ±1 步取整差并如实记——BP §6 口径）、交付/handler/ACK/
jiffies+1/时间片调用返回全过。**FAIL**：期限偏离闭式（配置已冻结无解释）、ACK≠1、
jiffies 增量≠1、0x60653AE 未返回。

### 2.4 D1-4 消息消费与分发闭环

**主样本=固件自然产生的 (18,0)**（BZ §3 建议 1 原样承接；发生在 park 前首批迭代，D1-1
允许）：

| 环节 | 锚点 | 报告键草案（`d1_window.d1_4.*`） |
|---|---|---|
| post 实参 | 0x60123a6→0x6000cba(0x60a1d11, 0x10000C, {18,0}) | `post.{call_pc,args}` |
| 入队表示 | 0x6064978→0x606487c，环 slot 头字 0x10000C03，队列计数 h[TCB+6] 0→1 | `enqueue.{slot_header_word,queue_count_before/after}` |
| get-event 返回 13 | 0x6064c8c→0x60649b0 解包 | `get_event.return_code` |
| [sp] 实际 id | buf[0]=0x10000C（无变换，BZ §1.2） | `event_id_read` |
| bit20→特路 | 0x60123f6 过→0x60123fa 特路 0x60108fe 子码 18 直通 | `special_path.{taken,subcode}` |
| 按真实查找顺序命中 | 表 2 walk（0x600434a/0x60252c6 不认领 18，返回 0）→[0x126f4c]=0 跳过→表 1 walk→**表 1[39] 命中**（12 字节记录 {filter,id,handler}） | `walk.{table2_hits,filter_ptr,table1_hit_record}` |
| handler 真实字节执行 | 0x6023962（0x6023972 子码门→0x6023976/0x60239b2→call 0x60237d8→门控 b[0x125B54]→b[0x127184]） | `handler.{entry_pc,executed_steps,gates}` |
| 返回→回环→再 pend | 返回 0→续 walk→回循环头→空队列 pend 写面再执行 | `handler.return_value`, `re_pend_after_consume` |

**PASS**：全链每站点有证据+回 pend（**不能只以 handler PC 出现过算通过**，codex 反例
原样保留）。**诚实收口**（d1-accept §1 D1-4 codex 条款照抄）：D1-1..3 过+D1-4 无样本=
「未覆盖，D1 完整验收未完成」（NOT_RUN）；区分**回调表为空**（链路通无事件源——本世界
已静态排除：[0x126F6C] 恒非空，BZ §2）与**链路不通**（post 成功却在变换/入队/接收处
丢失——须定位，FAIL）。
**(20,2)**=第二样本观测项非必经（特路 sys_timer 路径；id=2 链表节点存在性=BZ 运行观测项，
缺失时 0x6010946 停点**归前沿推进路**处理而非 D1-4 FAIL）。

**注入变体 `d1_post_inject='s18_18'`**（opt-in 默认关）[设计]：在窗口内首个 Timer0 唤醒
交付完成、核回到 idle 循环运行态时，以 key_chain_direct_call 先例直调 0x6000cba(18,0)
（哨兵返回+上下文保存恢复；执行时机记入报告；**显式合成假设**全文标注）。观察目标=
「队列空→非空→app_core 被调度→循环头再达→消费全链→回 pend→再 park」的 park 后闭环
（D1-5「必要任务可唤醒」的加强样本）。**机制风险如实标注**：idle 循环（0x12c5c6 call
0x12b5b0→0x12c5ca idle→0x12c5cc call−160→0x12c5ce 判）是否查询就绪队列切回 app_core
**未钉死（运行观测项）**——直调后若调度器不切回，记录 BLOCKED（调度机制缺件）而非
FAIL；此变体不进默认场景、不进 D1 过线必要条件。

### 2.5 D1-5 观察窗口健康度 → §3 专节

## 3. D1-5 窗口健康度的机器可检查设计

codex 定义（d1-acceptance §1 D1-5 原文）：无中断风暴、无同一事件无限重发、无任务永久
饿死；「应发生的定时事件确实发生、必要任务可唤醒、处理后重新等待；仅 PC 稳定或两核
IDLE 不够」。机检规则 [设计]，全部在窗口报告键上可判定：

| # | 规则 | 机检判据（PASS） | FAIL 信号 | 报告键 |
|---|---|---|---|---|
| H1 | 定时事件确实发生 | 窗口内 Timer0 到期 ≥3 次（与 D1-2 同数互证） | 到期 <3 或周期漂移 | `health.timer0_expiries` |
| H2 | 同源重发有界 | 每源交付数 ≤ ⌈N/P_i⌉+1：Timer0 P=40,000；core tick P=300,000（park 态实读为准）；软件 IRQ124-127 预期 0 | 超上界=风暴/无限重发 | `health.per_source.{source,period,deliveries,upper_bound}` |
| H3 | 无同事件无限重发 | 同一 (事件 id,子码) 的入队次数 ≤ 上界（S18 预置=2；注入变体=+1）；消费次数=入队次数（无重复消费） | 消费>入队（重复分发）或入队无界增长 | `health.event_reposts`, `health.queue_balance` |
| H4 | 必要任务可唤醒 | 窗口内 CPU0 被 Timer0/core tick 唤醒 ≥1 次且**每次唤醒处理后重新** `core_idle_wait_entered`（唤醒→处理→回等待的循环可数） | 唤醒后不回等待（卡运行态）；零唤醒 | `health.per_core.{wake_count,rewait_count,wake_reasons}` |
| H5 | 无任务饿死 | 除 idle 外就绪任务集 R（停点 TCB 扫描 state==ready）：R=∅ 时 vacuous PASS 并记录；R≠∅ 时每个 r∈R 窗口内 ≥1 次调度（PC 进入其任务体） | 有就绪任务全程未调度 | `health.ready_tasks.{tcb,entry,scheduled}` |
| H6 | 队列积压有界+消费指针前进 | 窗口末 h[TCB+6] 回 0（全消费）或 ≤ qlimit；环游标单调前进无异常回卷 | 积压无界增长；游标回卷旧 slot 未消费即覆写 | `health.queue.{count_final,cursor_monotonic}` |
| H7 | 状态变迁可追溯 | 每核状态时间线（entered_idle_wait/woken/running 序列）完整记录于报告（机器可复查，非仅终态快照） | 时间线断链/矛盾 | `health.per_core.timeline` |

**否定式总闸**：D1-1..4 逐项形状达标但 H1-H7 任一 FAIL → D1 仍失败（d1-accept §1 D1-5
原文）。H2/H3 的上界公式按 §1.3 的 P_park 实读口径同步替换（不预锁 40,000）。

## 4. registry 条目草案 + 场景参数 + check 骨架

### 4.1 probe_irq_entry.irq_case 新参数（照 frontier-registry「加新开关」四步的第 1 步）[设计]

```python
event_clock=False,        # machine_options['event_clock_projection']=True（BI G3 接线；
                          #   需 idle_wait；S16 os_time_dly 等待已要求它进条件栈,见 §5）
d1_acceptance=False,      # D1 观察窗场景（probe_only 行 'd1_acceptance_window'）
d1_window_steps=None,     # 观察窗模型步预算;None=默认公式 §1.3（值开关）
d1_post_inject=None,      # 's18_18'=park 后注入 (18,0) 第二样本（值开关,默认关）
```

### 4.2 frontier_registry.FEATURES 条目（第 2 步）[设计]

```python
Feature('event_clock', 'conditional', ('idle_wait',), False, None,
        '全核 IDLE 事件时钟推进(advance_event_clock;开启后 S16/S17 tick 等待可跨,'
        'park 后持续运转;默认关=全核 IDLE 立即诚实停)'),
Feature('d1_acceptance', 'probe_only',
        ('event_clock', 'timer0_irq4_delivery', 'core_tick_irq3_delivery', 'repeat'), False, None,
        'D1 验收观察窗场景(park 后 N 模型步,断言 D1-1..5,d1-acceptance §1;'
        'd1-scene-blueprint)'),
Feature('d1_window_steps', 'probe_only', ('d1_acceptance',), True, None,
        'D1 观察窗模型步预算(默认=S_tick+160k,下限 200k)'),
Feature('d1_post_inject', 'probe_only', ('d1_acceptance',), True, None,
        "park 后事件注入变体('s18_18' 直调 0x6000cba,显式合成假设,默认关)"),
```

层属说明：event_clock 进 conditional（而非 probe_only）的依据=BA §5.1 #12 行——**S16 的
5×os_time_dly(10) 在共享前沿路径上**，事件时钟不开则条件前沿停在 S16 的首次全核 IDLE，
到不了 S19；probe_only 的 key_chain_scan 先例允许 requires 引条件栈成员（闭包校验两向过）。
`repeat` 在 base 栈（第二次 Timer0 交付必需）。

### 4.3 报告键全集（场景行 `d1_acceptance_window` 的 post_stop 观测块）[设计]

```python
'd1_window': {
  'park': {'model_step': N, 'instruction_step': N,
           'cores': {'0': {'pc': '0x12c5ca', 'via': 'scheduler_idle_switch'},
                     '1': {'pc': '0x12c5ca'}},
           'event_clock_engaged': True,
           'timer0_state': {'con': '0x…', 'cnt': N, 'prd': N, 'period_closed_form': N},
           'core_tick_state': {'cnt': N, 'prd': N, 'deadline_closed_form': N}},
  'loop_head_first_step': N, 'loop_head_count': N,
  's18_first_pass': [ {'subcode': 18, 'handler_hits': ['0x6023962'], 'return': 0},
                      {'subcode': 20, 'special_path': 'sys_timer_0x126F3C_id2',
                       'handler_hits': [], 'observed': '…'} ],
  'empty_queue_pend': {'write_face_pcs': ['0x6064cf6', '0x6064d06'],
                       'yield_chain_pcs': ['0x6064188', '0x60642bc', '0x6064340'],
                       'tcb': '0x…', 'queue_head': '0x…', 'queue_count': 0},
  'dispatch': {'table2_records_walked': 6, 'dynamic_filter_ptr': 0,
               'table1_records_walked': 54, 'hit_records': [{'table': 1, 'index': 39,
               'record': ['0xff', '0x10000c', '0x6023962']}]},
  'canaries': {'k1_0x601249c': 0, 'k2_0x60124ae': 0},
  'timer0_cycles': [ {'index': i, 'expiry_model_step': N, 'gap_from_previous': 40000,
                      'delivered': True, 'handler_entry_pc': '0x12ece8', 'acked': True,
                      'w1c_count': 1, 'rti_pc': '0x12ed0c', 'reti_value': '0x…',
                      're_parked': True, 'tick_count_delta': 1, 'tick_ms_delta': 10,
                      'delivery_delay_steps': 0} ],
  'core_tick': {'deadline_closed_form': N, 'actual_overflow_model_step': N,
                'handler_entry_pc': '0x60661a4', 'ack_count': 1,
                'jiffies_before': N, 'jiffies_after': N + 1,
                'timeslice_call': {'executed': True, 'returned': True},
                'task_switch': {'occurred': False}},
  'd1_4': { … §2.4 表的键… , 'verdict': 'PASS|NOT_RUN|FAIL',
            'no_sample_reason': '…（NOT_RUN 时必填）'},
  'health': { … §3 表的键… , 'rules': {'H1': 'PASS', …} },
  'd1_model_steps_total': N, 'd1_window_instruction_steps': N,
  'window_blocked_events': [],
  'verdicts': {'D1_1': 'PASS', 'D1_2': 'PASS', 'D1_3': 'PASS', 'D1_4': 'PASS',
               'D1_5': 'PASS'},
  'projection': 'D1 window verdicts are conditional on the delivery/event-clock projection '
                'chain (idle_wait/event_clock/timer0/core_tick/…; hardware_mode_certified'
                '=false 不升级)',
}
```

### 4.4 check() 断言骨架（进 probe_irq_entry.check()，照 AW §4.2 风格）[设计]

```python
def check_d1_window(row):
    a = row['d1_window']
    # D1-1
    require(a['loop_head_first_step'] is not None, 'S19 loop head must be reached')
    pend = a['empty_queue_pend']
    require(pend['write_face_pcs'] == ['0x6064cf6', '0x6064d06'] and pend['queue_count'] == 0,
            'empty-queue pend must run the BA-pinned write face on a drained queue')
    require(a['park']['event_clock_engaged'] and a['canaries']['k1_0x601249c'] == 0
            and a['canaries']['k2_0x60124ae'] == 0, 'park + canary negatives')
    # D1-2: ≥3 cycles, 逐周期四查, 间隔=P_park
    cycles = a['timer0_cycles']
    P = a['park']['timer0_state']['period_closed_form']
    require(len(cycles) >= 3, 'need >=3 full Timer0 cycles in-window')
    for i, c in enumerate(cycles):
        require(c['delivered'] and c['acked'] and c['w1c_count'] == 1 and c['re_parked'],
                f'cycle {i}: expiry/delivery/ack/return each exactly once')
        require(c['tick_count_delta'] == 1 and c['tick_ms_delta'] == 10,
                f'cycle {i}: tick callback must really advance the counters')
        if i: require(c['gap_from_previous'] == P, f'cycle {i}: gap must equal P_park')
    require(not a['window_blocked_events'], 'no unknown stop may appear in-window')
    # D1-3
    ct = a['core_tick']
    require(ct['actual_overflow_model_step'] == ct['deadline_closed_form']
            or abs(ct['actual_overflow_model_step'] - ct['deadline_closed_form']) <= 1,
            'core tick first overflow must match the closed form from the parked state')
    require(ct['ack_count'] == 1 and ct['jiffies_after'] - ct['jiffies_before'] == 1
            and ct['timeslice_call']['returned'], 'core tick must have real effects')
    # D1-4: 全链站点核对（NOT_RUN 诚实收口,不算 PASS）
    require(a['d1_4']['verdict'] == 'PASS', 'D1-4 NOT_RUN is not PASS (honest close-out only)')
    # D1-5: H1..H7 逐条
    for rule, verdict in a['health']['rules'].items():
        require(verdict == 'PASS', f'D1-5 rule {rule} must PASS (negation gate)')
    # 总闸
    require(all(v == 'PASS' for v in a['verdicts'].values()), 'D1 needs all five')
```

（骨架字面值——P 的比较、H2 上界、10 帧/tick 常数——实施时按运行观测复核后钉死进单测；
「NOT_RUN 不算 PASS」承接 d1-acceptance §6.4/§8 语义。）

### 4.5 前沿重钉与场景行接线（第 3/4 步）[设计]

- 条件前沿推进到 S19 后：`FRONTIERS['p11_heap_experimental']` 重钉为新停点。**停点形态
  决策留给前沿推进路**（两候选：a. S19 循环头首达的指令步（freshness 钉 0x60123e6）；
  b. event_clock 开启后首个全核 IDLE 前的最后一个指令停点）——本蓝图只要求：重钉值
  在 D1 场景 flag 集下可复现、`freshness_fields` 保留 (kind,address)。
- 场景行：`irq_case(..., name='d1_acceptance_window', **scenario_flags(
  'p11_heap_experimental'), event_clock=True, d1_acceptance=True, budget=<park 指令步
  +窗口执行预算>)`（key_chain_scan_direct 行先例）。
- D1 场景不进 analyze_image_trace 的三镜像差分场景集（L2-a 转录是 BJ 过线后另一步，
  d1-acceptance §3.4 已有设计；本蓝图不重复）。

## 5. 前置依赖清单（BA 停点预测表 16 行逐条挂接 + 当前停点 + BI）

D1 场景的启动世界=「前沿推进到 S19」全部落地之后。按 BA §5.1 表（16 行）+ 其后的实际
演进（AS 路→DAA_CON1）排列：

| 阶段 | 停点/事项 | 判定/预案 | 状态 | 出处 |
|---|---|---|---|---|
| P-1（新增，BA 表外） | **当前停点：3,170,362 @ 0x606b426 读 0xfe0a0c DAA_CON1**（W1[3] 音频 init 内首个「硬件反馈依赖」：固件写后读回等模拟域反馈） | 三选一归在途路（codex 参谋中；候选=AW 蓝图 §2.2 的 ADA/ADDA 写记录+读回容器族） | **在途** | main `c626fc0`（AS）；AW 蓝图 §2.2 |
| BA #0 | W1[3] 尾段+DAC 簇（AK 域✓）+IRQ68 注册+'anc' 任务创建 | P-1 放行后通过（纯 RAM+已放行域；ANC 图门 0x601bd3a 运行值=残余风险，AY §6.5） | 待 P-1 | BA §5.1 #0 |
| BA #1-3 | W1[4] 桩→config 101→S9（config 523 擦除世界 init 返 −22 零注册）→W2（jlstream/IRQ91/队列族）→W3→W4[0..3] | 通过（纯 RAM/VM/IPI；fd31✓；6 tick 事件时钟） | 待 #0 | BA §5.1 #1-3 |
| BA #4-8 | **W4[4] PVDD/VLVD 簇停点①-⑤**：0xf2c274 P3_ANA_CON13→0xf2c244 P3_ANA_CON1→**TIMER2 0xfd0600 族**（0x12cd26 延时，健康路径必经）→0xf2c10c P3_PVDD0_AUTO→条件 0xf2c044 P3_VLVD_CON | 预案①**PVDD 容器**（三地址并入 P33_ANA_CON_TAIL 式容器单开关）+②**TIMER2 完成投影**（write-only 记录+bit15 完成投影+事件时钟推进 1ms≈4,000 步+0xFFF130 位记录，本簇最重新投影件）+③**canary 接线**（BV 的 p33_canary_projection 接线+首读具名值） | 待 #1-3 | BA §5.1 #4-8, §5.2 P1/P2/P5/P4/P5 |
| BA #9 | W4[5]/S11/S12（pmu 回调/dev_mg 任务/VM 读） | 通过 | 待 #4-8 | BA §5.1 #9 |
| BA #10 | S13/S14 门（实测双 0→电池路径，零 SFR） | 通过 | 待 #9 | BA §5.1 #10, §4.1/§4.7 |
| BA #11 | **S15 喂狗停点⑥：[0xF2A618]=0x400 P2M_CLK 写+0xF2A61C bit12 无界 ack 轮询**（门 b[0x1299f8] 实测 1） | 预案④**P2M_CLK ack 完成具名 fixture**（「P11 已消费消息」显式假设；先复测 b[0x1299f8]，若运行值 0 停点自动消失） | 待 #10 | BA §5.1 #11, §5.2 P6, §4.3 |
| BA #12 | S16：5×os_time_dly(10)（**~2×10⁶ 模型步事件时钟**） | 通过，零 SFR——**要求 event_clock 已进条件栈（§4.2 层属依据）** | 待 #11+event_clock | BA §5.1 #12, §4.4 |
| BA #13 | **S17→K2**：b[0x1274f4] 恒 0（config-523 缺失）首迭代即 K2 | 预案⑤**config-523 具名 fixture**（0x6010b4 组装面或 VM 523 具名 112B；擦除世界到不了 S19 的唯一电池侧替代=充电世界 LDO5V fixture，世界假设更大不推荐） | 待 #12 | BA §5.1 #13, §5.2 P7, §4.6, §9.7 |
| BA #13b（补充 [推断]） | P7 fixture 世界的 S17 健康环=151×(1 tick 等待+喂狗+读通道)≈**151 tick≈6×10⁶ 模型步事件时钟**+每轮数百指令步——前沿推进路的 budget 按此调整（BA 表 #13 行的「+4×10⁴」仅覆盖 K2 世界首迭代） | budget 调整项 | 待 #13 | BA §4.5/§4.6 推算 |
| BA #14 | S18 两次 post（纯 RAM）+**S18→S19 执行段潜在独立停点**：(20,2) 特路 0x6010946 `b[0]` 读（0x126F3C 链表 id=2 缺失时）与 (18,0) 深层 0x601a5xx 域（门控开时） | post 通过；两个潜在停点归前沿推进路三选一（BZ 定性：独立边界非链路不通/属 BT 提示域 D2+，如实记录收口），**非 D1 断言面** | 待 #13 | BA §5.1 #14；BZ §1.3/§3.1 |
| BA #15 | S19 循环+空队列 pend→两核 park（BI/BJ 域=本场景） | 本蓝图覆盖 | 待 #14 | BA §5.1 #15 |
| BI 前置 | G1/G2 已落地（路 BQ：闭式并入 min()+停泊步共用分发）；**G3 场景接线未实施**（event_clock 进 irq_case+真实前缀首验 A1-A5） | 本蓝图 §4.1 采纳为 event_clock 参数；BI 首验先于 D1 场景 | 待 #15 | BP §3.2 |
| D1 前置 | 本蓝图→实施路（路线梯 BJ：D1 验收） | 设计完成 | 本文 | d1-gap-map 路线梯 |
| 执行加速（可选） | BD 快照（main `adb996e` 设计）：D1-entry canonical 快照@park | §1.1 Q2；非过线必要条件 | 设计完成 | snapshot-design §4.3 |

## 6. 验收记录模板（D1 过线时按 §8 落档）

用例 id（合 acceptance_record 正则，覆盖面缩写：LOOP/TMR/TCK/DSP/HLT）：

| id | 断言引用 | 观察边界 | 超时预算（模型步轴） | 结果枚举 |
|---|---|---|---|---|
| **D1-LOOP-01** | d1-acceptance §1 D1-1（本蓝图 §2.1） | 循环头首达→park（`event_clock_engaged`） | boot 段+park（窗口前） | PASS/FAIL |
| **D1-TMR-02** | §1 D1-2（§2.2） | [W_park, W_park+N) 内 ≥3 Timer0 周期 | N（§1.3） | PASS/FAIL |
| **D1-TCK-03** | §1 D1-3（§2.3） | park→core tick 首交付+时间片调用返回 | ≤S_tick+80,000 | PASS/FAIL |
| **D1-DSP-04** | §1 D1-4（§2.4） | (18,0) 全链（首批迭代） | 消费段+回 pend | PASS/**NOT_RUN**（无样本诚实收口）/FAIL |
| **D1-HLT-05** | §1 D1-5（§3 H1-H7） | 整个观察窗 | N | PASS/FAIL（否定式总闸） |

九栏公共底稿（五条共用，逐条只改断言/边界/预算）[设计]：

```json
{
  "case_id": "D1-LOOP-01",
  "firmware_identity": "official 65cafc7f…（d1-acceptance §3.1 身份钉死；三镜像另记）",
  "start_state": "真实启动全程（Q1）；park 指令步/模型步=<d1_window.park>",
  "inputs_and_schedule": "无外部输入（D1 排除面=P33/WKUP/USB/按键）；时间表=观察窗模型步轴",
  "observation_boundary": "<本条边界列>",
  "assertion": "d1-acceptance §1 D1-n（d1-scene-blueprint §2.n 观察点表）",
  "timeout_budget": "<模型步预算>；超时=FAIL 非重跑",
  "model_assumptions": ["idle_wait_projection", "event_clock_projection",
    "timer0_irq4_delivery(entry/banks/rti)", "core_tick_irq3_delivery",
    "repeat_irq_delivery", "BA 五预案 flag（PVDD 容器/TIMER2 完成/VLVD canary/P2M_CLK ack/config-523 fixture）",
    "注入变体仅 D1-DSP-04b 且默认关"],
  "result": "PASS",
  "evidence_level": "conditional",
  "evidence_conditions": ["全部结论投影等级（hardware_mode_certified=false 不升级）",
    "入口银行/PSR 高位/RTI 装载等交付假设叠加适用（event-clock §5）"],
  "uncovered_items": ["硅时序/同刻仲裁", "IDLE 指令硅语义（idle_wait 措辞）",
    "外部输入域（P33/WKUP/USB/按键）", "模拟前端/DMA 总线/声学面", "(20,2) id=2 节点存在性(观测项)",
    "D1-4 深层 0x601a5xx 域(BT/提示,D2+)"]
}
```

落档命令（d1-acceptance §8.1 工具，运行态副本 `analysis/local-checks/acceptance/`）：

```
python3 tools/acceptance_record.py --add - < D1-LOOP-01.json   # 逐条；五条 + 可选 D1-DSP-04b(注入变体)
python3 tools/acceptance_record.py --validate                  # 校验（conditional 必附条件、PASS 条件须出现在模型假设栏）
```

**过线口径**：五条全 PASS（含 D1-DSP-04；NOT_RUN=D1 完整验收未完成）+ 条件性通过标注
（证据等级=conditional，投影链全文在模型假设栏）。L2-a（D1 窗口三镜像差分）为过线后
独立一步（d1-acceptance §3.4），不进本模板。

## 7. 风险与开放项（运行观测项清单）

**会被 D1 场景自然回答的**：

| # | 观测项 | 哪个键回答 |
|---|---|---|
| R1 | park 时 Timer0 实际配置（PRD 被 delay 函数重编后的末态；周期=40,000 还是别的） | `park.timer0_state.period_closed_form` |
| R2 | park 时 core tick 剩余期限（boot 期间多次溢出/交付后的末态） | `park.core_tick_state.deadline_closed_form` |
| R3 | (20,2) 特路 0x126F3C 链表 id=2 节点存在性 | `s18_first_pass[1].observed` |
| R4 | (18,0) 两级门控 b[0x125B54]/b[0x127184] 运行值（开=深层 0x601a5xx 域进窗） | `d1_4.handler.gates` |
| R5 | D1-3 时间片调度是否触发任务切换、切到谁 | `core_tick.task_switch` |
| R6 | idle 循环是否查询就绪队列（注入变体的 BLOCKED/PASS 裁决） | 注入变体 `post_inject.*` |
| R7 | 快轮 sys_timer 两节点（trim/GPCNT，tick=115）在 park 后窗口是否到期、回调动作 | `health.*`（窗口拉长变体） |

**仍开放的（D1 场景不回答）**：硅时序/中断仲裁/IDLE 硅语义（契约 §5 措辞不变）；外部
输入域整体（BP §2 排除表）；硬件反馈依赖的真机对照（D3 域）；(18,0) 深层域与 BT 域
（D2+）。

**工程风险**：
- **前沿重钉连锁**：event_clock 进条件栈后「停点」语义变化（park 后持续运转）——§4.5
  的停点形态决策若选 b，freshness 钉子的字段集要与实施路对齐（本蓝图不越权定死）。
- **S17 健康环 6M 模型步**（§5 BA #13b）：事件时钟逐模型步推进（无批量跳步，契约选择）
  → 单次世界构建时长可能从 ~3 分钟涨到 ~6-8 分钟；缓解=BD 快照（§1.1 Q2）或实施路评估
  事件时钟段的跳步上限（独立决策，不属本蓝图）。
- **窗口内 Blocked 的归因歧义**：D1-2 要求零 Blocked，但 D1-3 时间片调度可能把执行带进
  未建模域（任务体跑新 SFR）——处理：那是 FAIL（「无未知停点」条款，旧 kind 也不算过）
  + 记录归因（归 D2+ 域的新边界），修复后重跑；不允许为了过线缩短窗口。
- check 字面值（P_park、10ms tick 常数、H2 上界）实施时按实读复核再钉单测（§4.4 注）。

## 8. 实施工作量分解（BJ 路拿到即开工）

| 步 | 内容 | 可验证产出 | 量 |
|---|---|---|---|
| 0 | 前置（独立路，不属本蓝图）：P-1 DAA_CON1 三选一→BA 五预案→前沿到 S19/park+重钉 | 新条件前沿复现 | 各独立路 |
| 1 | BI G3：event_clock 进 irq_case+BI 首验场景（A1-A5） | BI 场景行+A1/A2 PASS | 0.5 天 |
| 2 | D1 场景骨架：d1_acceptance/d1_window_steps 参数+观察件（锚点 PC 计数、per-core 时间线、全局模型步计数器）+场景行 | 场景行跑通，报告键 §4.3 全出 | 1 天 |
| 3 | D1-1/D1-2 断言机读化+check 骨架落码 | check_d1_window 前半绿 | 0.5-1 天 |
| 4 | D1-3（闭式核对+jiffies/时间片）+D1-4（全链站点+NOT_RUN 收口） | 中半绿 | 0.5-1 天 |
| 5 | D1-5 H1-H7 机检+总闸 | check 全绿（或诚实 FAIL 归因） | 0.5-1 天 |
| 6 | 验收记录 D1-LOOP-01..D1-HLT-05 落档+HANDOFF/前沿表更新+（可选）park 快照与注入变体 | acceptance_record --validate 绿 | 0.5 天 |

合计 ≈ 3.5-5 个工作日（不含步骤 0 前沿推进）。每步验证=`check_local --units`+legacy
报告逐字节不变（新开关默认关）。

## 9. HANDOFF 条目草案（合并时贴入「当前并行任务」，照格式）[设计]

> - **路 BE（sim-d1scene，本次合并，只读设计）**：D1 验收窗口场景蓝图——把 d1-acceptance
>   §1 五断言落成 BJ 实施规格：场景 `d1_acceptance_window`（p11_heap 条件栈+event_clock+
>   Timer0/core-tick 交付，probe_only 三参数 d1_acceptance/d1_window_steps/d1_post_inject）、
>   观察窗=W_park 起 S_tick+160k 模型步（周期按 park 态实读 P_park 核对不预锁 40,000）、
>   五断言观察点全表（D1-1 空队列 pend 写面 0x6064cf6..06+让出链；D1-2 逐周期四查+tick
>   回调 [0x1268CC]/[0x1268D0]；D1-3 闭式期限+jiffies [0x125004]+时间片 0x60653AE；
>   D1-4 主样本 (18,0) 全链 BZ 锚点+注入变体默认关；D1-5 机检 H1-H7 含否定式总闸）、
>   registry 条目（event_clock 进 conditional——S16 os_time_dly 已要求）、前置依赖=BA
>   16 行表+DAA_CON1 决策+BI G3（G1/G2 已由 BQ 落）、验收记录 D1-LOOP-01..D1-HLT-05 九栏
>   底稿。主交付 [docs/d1-scene-blueprint.md](docs/d1-scene-blueprint.md)。未改 tools/、
>   未跑改动场景；字母 BE 与 sim-timer0irq 重名，合并时可顺延（CT 先例）。

## 10. 红线核对（本路自查）

- 设计不实施：未改 tools/ 任何文件、未跑任何改动场景（本文件为 docs/ 新增，git 状态可查）；
- 锚点全部标出处（BA/AY/BZ/BP/BQ/BR/d1-acceptance/event-clock/tail-map/snapshot-design/
  HANDOFF 路 BE/BF/main 提交）；未钉死值（P_park、门控值、id=2 节点、idle 循环调度面、
  S17 健康环步数）标「运行观测项/[推断]」；
- codex 反例/原话原样保留（「不能只以 handler PC 出现过算通过」「仅 PC 稳定或两核 IDLE
  不够」「旧 kind 再阻塞也不通过」「终态不要求两核同形」）；
- 提交不含 Co-Authored-By、不含私有路径/私有工件名；`check_local.py --units` 绿
  （文档路 trivially，已跑）。
