# D1 第一轮执行卡片（路 D1STAGE，2026-11-16）

主协调照卡逐步执行 D1 第一轮：park 确认 → PR-BI → PR-2 → Q1 全量重放 → PR-3 刷写 →
PR-D0 快照，失败走卡 6 回退。本文只做**合成不发明**：所有命令/代码/判据随行标注出处
（runbook 节 / PR2SPEC 节 / 任务书节 / 文件:行号），材料间矛盾集中在文末「矛盾清单」
并列待主协调裁决，不自行仲裁。时长标注分两口径：仓内文档推算（注明节号）与主协调
给定估算（注明「主协调估算」，材料内未见推算过程）。

**输入材料**：[d1-first-run-runbook.md](d1-first-run-runbook.md)（RBKREV 修订版，下称
runbook）；[d1-window-producer-spec.md](d1-window-producer-spec.md)（下称 PR2SPEC）；
[s19-batch3-taskbook.md](s19-batch3-taskbook.md) §1.1-§1.7（下称任务书）；
[acceptance-samples/d1/README.md](acceptance-samples/d1/README.md)（PR3P2 刷新版）；
[snapshot-resume.md](snapshot-resume.md)；`tools/d1_window_observer.py` 尾部
PR2_HOOKUP 注记（L774-792）。

**红线 5 总纲（每卡都适用，runbook 开篇）**：真实启动全程（Q1）是首次过线唯一合法
形态——①场景行单次从 boot 起跑、budget 一次给足；②调试迭代不改进入形态（窗口内
FAIL 归因→修复→重跑仍是全程 Q1）；③观察件只读、唯一合成件是 D1-4 注入变体（默认
关）；④park 快照只用于 Q2 复核，Q2 结果不得写成首次过线证据。

**当前代码态两条事实（写卡时核实，影响卡 2 工作量）**：

- 观察器半边**已在库**：`tools/d1_window_observer.py`（792 行，commit 597b5c5 路
  PR2OBS）+ `tools/test_d1_window_observer.py`。PR-2 实施路剩 probe 侧挂接，不必重写
  观察器。
- checker 半边**已在库**：PR2CHK 已落 d1_1/d1_2/d1_3 窗口换锚 + window_complete 豁免
  （HANDOFF 条目 99，单测 86→99）。PR2SPEC §6 的适配点 1-2 已完成、3-4 零改动确认。

## 卡片依赖表（文字版）

```
卡0 park确认(JLEQ报告) ──=闸门②──┬──▶ 卡1 PR-BI(观察,可先行) ──┐
                                 └──▶ 卡2 PR-2挂接+实施 ────────┤
                                                              ▼
                                              卡3 Q1首次过线全量重放(五断言+d1_gate)
                                                              │
                              卡6 失败回退 ◀── FAIL/pending ──┤pass
                                                              ▼
                                              卡4 PR-3 verdict刷写(五条记录)
                                                              │过线
                                                              ▼
                                              卡5 PR-D0 park快照(只作Q2)
```

卡 1 与卡 2 并行（任务书 §1.1：BI 观察失败不阻塞 PR-2 骨架合并，但阻塞 D1 验收）。
卡 3 需卡 1+卡 2 全绿（任务书 §1.7 PR-3 行闸门）。卡 5 在卡 4 过线之后（runbook §3.4）。

---

## 卡 0：park 确认（闸门②）

| 栏 | 内容 |
|---|---|
| 前置条件 | JLEQ 前沿路放行跑完成（advance7 已跑到 49,996,037 停在 instruction_budget 耗尽，穿过 park 预算点 37-39M 零新停点——主协调通报）。 |
| park 证据（JLEQ 报告里看什么） | 判定口径=runbook §1.1。三条：① **两核状态**：CPU0 走完 S19 消息循环链（循环头 `0x60123e6` → get-event 成功码消费 → 分发两表 walk → 空队列 pend 写面 `0x6064cf6` 起 → 调度器切 idle `0x12c57e`→`0x12c5ca`）；CPU1 停泊 `0x12c5ca`；② **事件时钟接管**：两核都 idle 后 event_clock 接管记 W_park（模型步+指令步双轴并记；注意这是「S19 空队列 pend 链完成后的那次接管」，**不是**全程首次 engaged——boot 期 os_time_dly 停泊已多次 engage，B3 实测 13.4M 虚拟步，runbook §1.1 诚实区分）；③ **无 unknown stop**：停点 kind 不再是 unmapped_memory / unmapped_or_readonly_write 类「新边界」——advance7 的 stop kind = instruction_budget（预算耗尽），满足本条。 |
| 机械核验命令 | 离线不跑仿真（runbook §1.2 第 2 条）：`python3 tools/check_d1_acceptance.py --report <JLEQ 报告 json 路径> --scenario p11_heap_experimental_config`。D1-1（真实进入消息循环并阻塞）从 pending 转 pass = 机器可证的 park 到达。 |
| 验收判据 | D1-1 pass + 上面三条证据齐全。**证据不全的回退**：D1-1 仍 pending（键缺/窗口未开）说明还没到 park，不冒充（runbook §1.2）；回退动作=前沿路归因（停点形状 vs §1.1 停点形态决策 (a)/(b) 哪个没满足），必要时前沿路再跑，D1 路不接管归因。 |
| 分工红线 | registry 单点重钉 + HANDOFF 三处更新（runbook §1.3）**归前沿推进路**，D1 路不为 D1 重钉旧前沿（任务书 §1.2 第 6 条）。本卡只做核对。 |
| 预计时长 | 分钟级（离线 checker，秒级 + 人工读报告）。 |
| 红线提醒 | 「首次 engaged」不能直接当 W_park；「音频 init 途中停泊」不是 S19 终态 park（runbook §1.1 诚实区分）。 |

## 卡 1：PR-BI——预算给足 + BI 首验（A1-A5 先观察）

| 栏 | 内容 |
|---|---|
| 前置条件 | 卡 0 park 确认（闸门②）。 |
| 改哪行（给足预算） | budget 是 `tools/probe_irq_entry.py` irq_case 内的代码值（按 flag 推导，无 per-scenario CLI；runbook §2.2）。现状：`if jl_adc_start_config:budget=40000000`（budget 推导链，约 L1515 附近，指令步轴）。PR-BI 首验阶段（场景骨架未接线前）**直接复用现有条件栈场景，确认 budget ≥ 40M 即可**；PR-2 接线后在 D1 场景行写 `budget=40_000_000` 或 park 实测上界 + N + 余量（runbook §2.2）。⚠ JLEQ 实测 50M 指令预算耗尽于 49,996,037——40M 是否够窗口 N 待 park 实测指令步落点，见文末矛盾 #1。 |
| 命令 | 跑全部场景（runbook §2.2）：`python3 tools/probe_irq_entry.py --rom <ROM> --report analysis/local-checks/irq-entry.json`（`<ROM>` 为私有 ROM 占位符；firmware 默认取仓内 `firmware/QCY_H3S_7018F_1T2_release_note_V1.1.8_011.ufw`）。出 BI 结果：`python3 tools/check_d1_acceptance.py --report analysis/local-checks/irq-entry.json --scenario p11_heap_experimental_config --json`。 |
| 验收判据 | 任务书 §1.1 第 2-4 条：A1（闭式精确到期）/A2（周期守恒）/A3（无事件零推进）/A4（期限缺口）/A5(a)（E1 往返）实测值 vs 判据逐条记录，不符先归因（观察项还是断言错）；**首轮只观察不触发 d1_gate**（BI-A* 不进 D1 gate）。事件时钟首个消费者口径：W2[0] 3×os_time_dly(2)=6 tick，`virtual_model_steps_advanced` 应首次非零，仍为零则停下归因不要继续（runbook §2.3）。任务书 §1.2 第 7 条的 run_all/d1_gate 传播口径是 **PR-2 接线后**的正式验收（见卡 2/卡 3），BI 阶段不适用——两口径并存是预期。观察一轮无异常后升硬断言，升级依据记 HANDOFF（任务书 §1.1 第 3 条；PR-BI 完成标准=任务书 §1.7 表）。 |
| 预计时长 | 主协调估算 0.5h（人工整理+机检）；含一轮首验跑则加单臂全程时长（见卡 3 推算）。 |
| 红线提醒 | 首验跑是全程 Q1（boot 起跑到 park），**不得用任何已有快照起跑**（runbook §2.3）。 |

## 卡 2：PR-2——挂接 + 实施（probe 侧接线）

| 栏 | 内容 |
|---|---|
| 前置条件 | 卡 0（闸门②）。观察器半边（PR2OBS 597b5c5）与 checker 半边（PR2CHK）已在库，本卡=剩余生产者接线：probe 三参数 + registry 三条 + 场景行 + `--with-d1-window` 旗标 + budget 追加 + 正式验收入口 + PR2_HOOKUP 挂接 + 单测。范围=PR2SPEC §0。 |
| 代码（可抄） | **① probe 三参数**（`tools/probe_irq_entry.py` irq_case 签名，`event_clock=False` 在 L1320，新参数加同款；PR2SPEC §4.1）：`d1_acceptance=False, d1_window_steps=None, d1_post_inject=None`。激活判定一律 `is not None`；`d1_post_inject` 非 None 时 require `=='s18_18'` 且 PR-2 阶段 `raise Blocked('d1_post_inject_pending_pr4')`；`d1_acceptance` 开时 require `event_clock and timer0_irq4_delivery and core_tick_irq3_delivery and repeat`。**② registry 三条 probe_only**（`tools/frontier_registry.py` FEATURES 尾部，PR2SPEC §4.2 原样）：`Feature('d1_acceptance','probe_only',('event_clock','timer0_irq4_delivery','core_tick_irq3_delivery','repeat'),False,None,'D1 验收观察窗场景(park 后 N 模型步,断言 D1-1..5,含 H8 生产者自检与正式验收入口;d1-acceptance §1;d1-scene-blueprint;d1-window-producer-spec)')`；`Feature('d1_window_steps','probe_only',('d1_acceptance',),True,None,'D1 观察窗模型步预算(默认=max(S_tick+160k,200k),H8 自检不足自动延长;显式值覆盖且不延长)')`；`Feature('d1_post_inject','probe_only',('d1_acceptance',),True,None,"park 后事件注入变体('s18_18' 直调 0x6000cba,显式合成假设,默认关;行为实现归 PR-4)")`。勿动 SCENARIO_LAYERS/PROBE_SCENARIO_NAMES/FRONTIERS。**③ 场景行**（main() 场景表末尾、key_chain_matrix 行之后；PR2SPEC §4.3）：`irq_case(module,app,header,rom,'d1_acceptance_window',**scenario_flags('p11_heap_experimental',sys.modules[__name__]),event_clock=True,d1_acceptance=True,flash_image_bytes=image)`。**④ `--with-d1-window` 旗标**（PR2SPEC §4.3 运行集合决策 (ii)）：main() 加 CLI 旗标默认 False，场景列表构造按旗标 append D1 行；旗标关时 D1 行完全不构造。**⑤ budget 追加**（irq_case budget 推导链尾部；PR2SPEC §4.4）：`if d1_acceptance: budget=41_000_000`（park 上界 39M 模型步 + N 上界 460k + 余量；park 实测落定后按实收紧/上调——见矛盾 #1）。**⑥ 正式验收入口**（PR2SPEC §4.5）：`from check_d1_acceptance import run_all`；D1 场景行跑完（含 finalize）后 `result=run_all(d1_scenario_row)`、回填 `d1_scenario_row['d1_window']['verdicts']`；main() 退出时 `d1_gate != 'pass'` → `sys.exit(1)`。**⑦ PR2_HOOKUP 挂接**（`tools/d1_window_observer.py` L774-792 注记原样，probe_boot.explore 三处）：1) 构造传参（irq_case d1_acceptance=True 时，dual_case 构造后、explore 调用前，detach 在 finally）：`observer=D1WindowObserver(machine,hardware,d1_window_steps=d1_window_steps)`；2) 主循环尾（trace.append 之后、stop 检查之前，每圈）：`if observer is not None: observer.note_step(c,int(row['pc'],16),completed,stalled='stall' in row)` + `if observer.window_stop_request is not None: stop=observer.window_stop_request;break`；3) post_stop 回调（场景行传 post_stop=）：`def post_stop(machine,hardware,stopped_cpu): face=observer.machine_face(); return {'d1_window':observer.finalize(stop),'machine':{**machine.report(),**face}}`（post_stop 注入通道先例=probe_boot.py L2076-2078/L2140-2141；Blocked→stop 通道=L2035 附近）。 |
| 红/绿单测清单 | PR2SPEC §7 表全量：① `tools/test_d1_acceptance.py` 既有 99 例回归零红；② 新增窗口锚点切分（boot 停泊+d1_window.park=S19 park 形状，适配后 d1_2 不吃 boot 到期）；③ 新增 window_complete 豁免（≥3 周期 pass、<3 仍红）；④ 新增 d1_3 窗口锚（boot 首溢出早于 W_park 形状）；⑤ 新增 emit 纪律（cfg523_installed_at 非 None→keyscan 必在、None→不在、adc_scan 恒在）；⑥ 新增 N 公式+H8 延长（P=70k 自动延长、显式 N 不延长）；⑦ `tools/test_image_trace_diff.py` validate()（三条 probe_only 闭包+参数一致性）零红；⑧ 观察器配套单测（`tools/test_d1_window_observer.py` 已在库，随挂接补对表用例）。退出条件=`python3 tools/check_local.py --units` 全绿。合并判据=合成样本单测绿+（park 已到）真跑一轮 missing 清零；五断言全 PASS 属 PR-3（PR2SPEC §7 末段）。 |
| 对照与预检 | legacy 逐字节不变（stash 对照法：旧场景各自报告记录逐字节相同）；probe check() 断言面先 `python3 tools/assert_preflight.py <既有报告 json>` 秒级预检再上全量（PR2SPEC §7）。反例自证至少一路实测红（AZ 惯例）。 |
| 预计时长 | 主协调估算 2-4h。 |
| 红线提醒 | 观察件纯只读旁路（不改计步/预算/IRQ 交付顺序/序列化顺序）；新开关默认关、legacy 逐字节不变；P_park/CNT/门控值全部实读不预锁（PR2SPEC §0 红线）。D1 场景不进 analyze_image_trace 场景集（parity 钉子零变更预期）。 |

## 卡 3：Q1 首次过线全量重放（五断言）

| 栏 | 内容 |
|---|---|
| 前置条件 | 卡 1（BI 观察+升级依据落 HANDOFF）+ 卡 2（PR-2 合并、`--with-d1-window` 可用）全绿；PR-1 已绿（任务书 §1.7 PR-3 行闸门）。 |
| 命令 | `python3 tools/probe_irq_entry.py --rom <ROM> --report analysis/local-checks/irq-entry.json --with-d1-window`（`--with-d1-window` 由卡 2 落地；Q1 报告由 probe_irq_entry 产出，五断言机检消费该报告）。⚠ 任务简报写「--rom --jobs 3」——probe_irq_entry CLI **无 --jobs**（只有 --rom/--firmware/--repaired-candidate/--failed-r2/--report，`tools/probe_irq_entry.py` L6185-6188）；`--jobs` 属 analyze_image_trace（L1739，PAR3 三臂并行，用于卡 5 捕获轮/镜像跑）。见文末矛盾 #2，以主协调裁决为准。 |
| 判过口径 | 跑完先离线机检（runbook §4.1 第 2 条）：`python3 tools/check_d1_acceptance.py --report analysis/local-checks/irq-entry.json --scenario d1_acceptance_window --json`。**场景名必须是 `d1_acceptance_window`**（`d1_window.*` 报告键只有它产出；旧条件场景名跑 D1-2..D1-5 全部 pending 永远到不了五条 PASS；park 判定仍用 `p11_heap_experimental_config`——两处场景名不同是预期，不要「统一」，runbook §4.1）。判过=D1-1..D1-5 各 status=pass + `d1_gate`=pass。**判活以输出里的 status/d1_gate 字段为准，checker CLI 退出码恒 0**（仅场景名缺失=2；runbook §4.1）；「非 pass 即非零退出码」是 probe 正式验收入口（卡 2 ⑥）的义务。 |
| 预计时长（写清推算） | 主协调给定口径：50M 预算 × 26.3k 步/s ÷ = 50,000,000/26,300 ≈ 1,901s ≈ **32 分钟单臂**；「jobs 3 双镜像 ≈ 40-70 分钟」（推算基础=主协调给定的吞吐与预算，仓内文档未见该组数字）。仓内文档口径：O7E 实测外推**单臂全程到 park ~18min**（事件时钟段仅 2.95% CPU；任务书 §1.1 第 1 条/runbook §2.3），预算口径 3.7-3.9×10⁷ 模型步。两口径预算轴不同（50M 指令步 vs 39M 模型步），并列待实测校准（矛盾 #3）。 |
| 红线提醒 | **首次过线必须全量重放（Q1，boot 起跑 budget 一次给足），不得用任何快照起跑**（runbook §2.3/§4.3；任务书 §2.1 第 1 条）。机检消费的报告必须是全程 Q1 跑出来的，不是快照 resume 轮（runbook §4.1 第 3 条）。窗口内 FAIL 走卡 6，修完重跑仍是全程 Q1。 |

## 卡 4：PR-3——五条 verdict 刷写

| 栏 | 内容 |
|---|---|
| 前置条件 | 卡 3 d1_gate=pass（或诚实 FAIL 归因后落档）。 |
| 命令（五条） | 从 PR3P 预填底稿出发（`docs/acceptance-samples/d1/` 五条九栏 NOT_RUN 已预填，PR-3 只刷 verdict+证据+实测值，任务书 §1.6 第 1 条）：逐条 `python3 tools/acceptance_record.py --add docs/acceptance-samples/d1/D1-LOOP-01.json --dir docs/acceptance-samples/d1`（TMR-02/TCK-03/DSP-04/HLT-05 同款各一条）；然后 `python3 tools/acceptance_record.py --validate docs/acceptance-samples/d1`。**`--add` 必须带 `--dir docs/acceptance-samples/d1`**——不带会写进 git 忽略的 analysis/local-checks/acceptance/（DEFAULT_DIR），git 跟踪的 docs 五条仍 NOT_RUN 而 validate 照样绿——落档后必查五条 result 已实刷，不能拿 validate 绿当五条 PASS（runbook §4.2）。落档后重生成 README 汇总表（会整页覆写、头部手工补回）：`python3 tools/acceptance_record.py --summary --dir docs/acceptance-samples --dir docs/acceptance-samples/d1 --output docs/acceptance-samples/d1/README.md`（README 刷新口径段）。 |
| verdict 四态映射 | 记录 `result` 只接受 PASS/FAIL/BLOCKED/NOT_RUN（大写；小写 status 直接填会被拒）。LOOP-01←D1-1、TMR-02←D1-2、TCK-03←D1-3、DSP-04←D1-4、HLT-05←D1-5。**pass→PASS、fail→FAIL**；pending 按原因分流：D1-4 确认无样本→NOT_RUN、模型缺件→BLOCKED（必附 `blocked_missing`）、窗口配置不足（H8 insufficient）→**不是 verdict**（修 N 重跑，runbook §4.1/README 判据口径段）。 |
| 判活 | status/d1_gate 字段非退出码（同卡 3）。过线口径=五条全 PASS（D1-4 无样本=NOT_RUN=未完成，不冒充）+条件性通过标注（任务书 §1.6 第 3 条）。 |
| 证据契约注意 | ① 起始状态栏只写「真实启动全程（Q1）+ park 双轴步数」（红线 5）；② CNT 假设贯穿三处：报告键 `d1_window.core_tick_state.assumption`（顶层优先）+ checker 判定输出（H2/d1_5 的 core_tick 上界分支）+ TCK-03 `model_assumptions` 栏，原文 "under the explicit 'CNT frozen after overflow' assumption (conditional, assumed-not-measured) — NOT PRD-1"；③ PRD 读回≠CNT 读回不混写；④ conditional 必附 `evidence_conditions` 且逐条出现在 `model_assumptions` 栏（acceptance_record 内置校验）（runbook §4.2 全段）。 |
| 预计时长 | 主协调口径未给；机械刷写+校验分钟级，加一轮验收跑（若需重跑）见卡 3。 |
| 红线提醒 | DSP-04 无样本时 NOT_RUN 不得当 PASS；注入变体 D1-DSP-04b 归 PR-4 不在本批（runbook §4.3）。 |

## 卡 5：PR-D0——park 快照（PR-3 过线后）

| 栏 | 内容 |
|---|---|
| 前置条件 | 卡 4 过线（任务书 §1.7 PR-D0 闸门=PR-3 过线后；runbook §3.4）。 |
| capture 命令 | `python3 tools/analyze_image_trace.py --rom <ROM> --scenario p11_heap_experimental --image official=<官方 ufw> --image tested_b79=<b79 ufw> --capture-snapshots analysis/snapshots --report analysis/local-checks/capture-round.json`（runbook §3.1；双镜像，候选第三臂可选，需要时补 `--image <label>=<路径>`（label ∈ {eq_fence_repair,l1_slotram,r4_keyhook}）并加 `--jobs 3`——PAR3 三臂并行约 3.03x，条件三镜像 68m33s→22m39s）。每镜像照常全量重放到自然停点，停点边界把世界（M1-M7）捕获到 `analysis/snapshots/<scenario>-<label>.bin`；每镜像一份互不可替换。 |
| resume 验证 | `python3 tools/analyze_image_trace.py --rom <ROM> --scenario p11_heap_experimental --image official=<官方 ufw> --image tested_b79=<b79 ufw> --resume-from analysis/snapshots --report analysis/local-checks/verify-round.json`（runbook §3.2）。**8 秒复现=主协调给定估算**（21.8M 时代实测验证轮 2.2-2.6s，snapshot-resume.md 成本对照表；50M 量级按停点步数比例外推 ≈8s——仓内文档无 50M 实测，待实测校准）。resumed 轮核验停点面：freshness 停点钉子、每对 runs/parity、final_report_diffs；轨迹面字段只反映 restore 之后的步，看顶层 `snapshot_round` 与 pair 的 `resumed:true` 标记（snapshot-resume.md §2）。 |
| 验收判据 | 捕获轮报告 + 等价性证据（capture 于停点→restore+advance 三档预算 vs 全量重放逐字节相等+0 步自反；测试入口 `tools/test_world_snapshot.py` ArtifactTierFrontierWorld，env 门控 `H3S_SNAPSHOT_ROM`）+ 登记到 `evidence/snapshot-artifact.json` canonical 行（runbook §3.3；任务书 §1.7 PR-D0 完成标准）。 |
| 预计时长 | 捕获轮 ~18min/镜像量级（任务书 §1.6 第 4 条，O7E 口径；50M 口径见矛盾 #3）；验证轮秒级（≈8s 主协调估算）。 |
| 红线提醒 | **快照只作 Q2 复跑/回归加速，不作过线证据**；验收记录 start_state 栏绝不写快照 resume（runbook §3.4）。快照文件含 RAM 镜像不进 git（放 analysis/，gitignored）。`--resume-from` 与 `--self-check`/`--candidate-self-check` 互斥。PR-2 改过仿真源 → 既有快照指纹拒载、须重新捕获（双闸机械保证，这正是「行为变化必须全量重放」的执行点）。 |

## 卡 6：失败回退（窗口内 FAIL 的归因流程）

| 栏 | 内容 |
|---|---|
| 前置条件 | 卡 3/卡 4 出现 FAIL 或异常 pending。 |
| 归因流程（runbook §5.1 浓缩） | ① **看 FAIL 证据契约**：每条 FAIL 的 check 附 `evidence={rule,expected,observed,first_violation{model_step,core,pc},source_or_task,around,worst,window_end}`（适用面=H1-H7；D1-1..D1-4 的 checks 是 `{name,status,fail,detail,missing}` 形状，读 detail/missing）。先定位 first_violation，判断**观测未到**（pending：键缺/右边界未闭合）还是**真违约**（fail：有数据但违反条款）——pending≠fail≠pass 三态不混写。② **排除窗口伪证**：H8 折算覆盖 <3 → insufficient_window_coverage → D1-5=pending、d1_gate=pending（前提=H1-H7 无真 FAIL；同窗口有真 FAIL 则 gate=fail 优先）——窗口配置问题修 N 重跑，不是 DUT 失败。③ **真 fail 按七规则归因**：H1/H2 三分计数+上界（H2 core_tick 的 CNT 假设标注缺=pending，先补标注）；H3 重发/重复消费+queue_balance；H4 未闭合唤醒区间（last_pc/in_flight_irq/nesting_level）；H5 两级（断言面任务饿死=FAIL、非断言面未调度=观察级）；H6 逻辑序号回退/物理指针≠序号 mod 容量/超限；H7 状态转换流断裂。④ **cfg523 联动**：checker PERIODIC_SOURCES 四源（timer0/core_tick/keyscan/adc_scan）已落地（H2SRC）；adc_scan 缺条目=pending、keyscan 缺条目不 pending（cfg523 条件性；「cfg523 开必 emit keyscan」由生产者 emit 纪律承担，判定源=machine.cfg523_installed_at）。 |
| 修复后重跑 | **仍是全程 Q1**（boot 起跑，budget 一次给足）——修的是模型/断言 bug，不是缩短窗口、不是换快照热启动、不是注入事件把断言填绿（runbook §5.2）。动仿真源/场景开关 → park 快照双闸自动拒载强制重捕获。 |
| 验收判据 | 归因记录诚实标注 FAIL vs BLOCKED（模型缺件）vs 窗口配置不足三者区别；不把模型缺件写成 DUT 违约、不为过线放宽判据（红线 2/3）。 |
| 预计时长 | 视归因对象（分钟级读证据→小时级修复+重跑一轮）。 |
| 红线提醒 | 同红线 5 总纲②：调试迭代不改进入形态。 |

---

## 矛盾清单（并列列出，待主协调裁决）

1. **D1 场景 budget 取值**：runbook §2.1 落地值 `40,000,000`（=39M+460k+540k 保守合成）vs PR2SPEC §4.4 建议 `41_000_000` 起步（同为「按实上调」条款，两文档不互斥但数字不同）。**新材料**：JLEQ advance7 实测 instruction_budget=50M 耗尽于 49,996,037（穿 37-39M park 预算点零新停点）——park 的指令步落点未钉，若 park 指令步接近 49M 则 40/41M 均不够窗口 N，budget 需按 park 实测指令步+N+余量重定（可能 ≥50M）。另注意：runbook §2.1「40M 真实余量远大于 540k（快进段不耗 budget）」的推断只覆盖快进段；park 后 idle 循环每步照耗 instruction budget，JLEQ 50M 耗尽与该推断的口径边界需主协调按报告实值裁决。
2. **卡 3 命令 `--jobs 3`**：任务简报给「--rom --jobs 3」，但 probe_irq_entry CLI 无 --jobs（L6185-6188）；--jobs 属 analyze_image_trace（L1739）。五断言 Q1 报告由 probe_irq_entry 产出。若意图是镜像并行或 PR-D0 捕获轮提速，应落在卡 5 的 `--capture-snapshots ... --jobs 3`，不落在卡 3。
3. **时长推算口径**：主协调给定「50M 预算×26.3k 步/s 单臂≈32 分钟、jobs 3 双镜像≈40-70 分钟」（吞吐与预算由主协调给定，仓内文档未见）vs 仓内 O7E 外推「单臂全程到 park ~18min」（3.7-3.9×10⁷ 模型步口径）。预算轴不同（指令步 vs 模型步）且 JLEQ 实测吞吐未入文档，实测为准。
4. **「8 秒复现」**：主协调给定；仓内实测=21.8M 停点验证轮 2.2-2.6s（snapshot-resume.md），50M 量级为外推，非实测。
