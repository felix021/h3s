# S19 批 3 任务书：D1 场景接线（probe_only 三参数 + H1-H8 机检 + 五断言首验）

2026-11-14 路 D1PREP/sim-d1prep 初版（基线 main `217858a`，22:33 按 main `48a49d0`
校准——路 B3「CNT held 快照+ACK 四查+JL_ADDA 模拟族」与路 A1「b79↔EQ parity 重跑
FAIL」已合并）。**2026-11-16 路 B3T/sim-b3t 全面刷新（基线 main `cc10784`）**：此后
合并的七路证据全部并入——S19MAP 终局图（`979a99c`，[s19-endgame-map.md](s19-endgame-map.md)：
park≈3.7-3.9×10⁷ 总步、最少 2 路计划 3 路、3 必裁决、AY 19 SFR 欠账零、W4[4] 预解决、
AG 图 0x606c54e 漏判修正）；PR-1 H1-H7 机检完成（`fafaffd`）+PR2H8 窗口相容性自检
半前移（`1cc166d`）；CHK 断言离线预检器 `assert_preflight.py`（`be9c6bb`）；SNAPX
快照续跑+PAR3 三臂并行（`a2f0b13`）；O7E 事件时钟性能销案（`cc10784`）；R4SNAP
差分线钉值机检化（`546b73c`）。本页是 S19 冲刺批 3（=路线梯 BJ「D1 验收」的场景
接线批）的任务书：把 [d1-scene-blueprint.md](d1-scene-blueprint.md)（路 BG 蓝图 +
§11 前置刷新）落成实施路拿到即可开工的分解单。**开工闸门**：①~~批 2 后续路（任务
卡称 BR=路 B3）合并~~**已过**（48a49d0）；②前沿到达 S19/park 形态（蓝图 §1 的前提）
——**判据按 S19MAP 新口径**（§1.0 空位表末行）：旧口径的 DAC_TM0 已被 DT 放行越过
（19,139,239→21,798,206），音频域此后又吃掉 ~6.96M 步（JLA/LPV/ADA/ASS 连环放行，
AG 图漏判修正为其根因），当前停点 **28,764,184 @ 0x606c5ba 读 0x61fbfe0**（W1[3]
尾段 flash 顶资源取数，FLS 在途）——闸门②到达=FLS 窗口放行+批 2 重落地（B2R 在途，
p2m 邮箱+cfg523，实现都在历史 `0b875ed`）两路完成、推进跑过 S16-S18 到 **park
≈3.7-3.9×10⁷ 模型步**（S19MAP §2 预算；注意**途中停泊已真实发生≠S19 终态 park**，
B3 实测的停泊是音频 init 的 OS 等待族形态）。闸门②未过时，本任务书中标「等前沿」
的空位不得填、probe 侧改动不得开工（§2 红线）；~~check_d1_acceptance.py 侧的
H1-H7 机检与单测不受闸门限制，可先行~~**该先行项已全部完成**（PR-1+PR2H8，§1.0b）。

## 0. 背景与弹药清单

### 0.1 冲刺脉络（批 0b→3）

批 0b（CW，已合并）：event_clock 进条件栈 + DAC_VL0。批 1（B1，已合并）：W4[4]
四件 + DAC DMA 尾段勘误，前沿到 3,747,578。批 2（BQ PRD 一案✅ + B3 两案✅，均
已合并 48a49d0）：PRD 读回 +7 步到 3,747,585；B3 放 CNT held 快照
（`core_tick_cnt_held_snapshot`）+JL_ADDA 模拟族（4,521,083），前沿冲到
**19,139,239 @ 0x606abbe（DAC_TM0 读）**——事件时钟首次真实推进 13,429,113 虚拟步、
core tick 溢出/ACK 12→108、jiffies 终值 118；电池段两案（P2M 邮箱/cfg523）已实现
但零消费者**撤回**（实现保留 sim-s19b3 历史 0b875ed，音频域过界后按到达顺序落地）。
**批 3 = 本任务书（初版口径）**：前沿过 DAC_TM0 及音频数字域整块、到 S19/park 后，
把 D1 验收观察窗场景接成可跑、可机检、可落档（cc10784 口径见下段与 §1.0 空位表）。

**48a49d0→cc10784 的战场变化（S19MAP §5 修正清单+七路新证据）**：DAC_TM0 由 DT
放行（半字 field setter 容器，19,139,239→21,798,206），此后 JLA/LPV/ADA/ASS 连环
推进到 **28,764,184**——音频域实跑合计 ~25.6M 步、10+ 个放行，远超 BA/BM 静态预测
（~0.4M），根因=AG 图把 0x606c54e 误判「全 RAM」漏掉间接指针（S19MAP §5 修正）；
**AY 19 新 SFR 清单欠账为零**（主路径 10 个已全放行、N2 9 个健康路径零触达），
真正剩的是三个非 SFR 必裁决：flash 顶窗口（FLS 在途）+P2M 邮箱（B2R）+cfg523
（B2R）；W4[4] 被批 1 三开关**预解决**（零裁决）。与此同时批 3 的免闸门准备件已
全部落地：PR-1（H1-H7 机检，`fafaffd`）、PR2H8（H8 半前移，`1cc166d`）、PR3P
（五条 NOT_RUN 底稿预填，`3a1a692`）、CHK（断言离线预检器，`be9c6bb`）、SNAPX+PAR3
（快照续跑+三臂并行，`a2f0b13`）、O7E（事件时钟性能销案，单臂到 park ~18min）、
R4SNAP（差分线钉值机检，`546b73c`）。

### 0.2 弹药盘点（已在 main cc10784 的，批 3 直接用；初版基线 217858a 时期各行保留原注记）

| 弹药 | 状态 | 出处 |
|---|---|---|
| `event_clock` probe 参数 + registry conditional 条目 | ✅ 已接线（CW；probe_irq_entry 行 1130/2031、frontier_registry 行 159） | HANDOFF 路 CW |
| Timer0 交付三件 `timer0_irq4_delivery(entry/banks/rti)` | ✅ conditional 层早就在 | HANDOFF 路 BE |
| core tick 交付三件 `core_tick_irq3_delivery` | ✅ conditional 层 | HANDOFF 路 BF |
| `repeat_irq_delivery`（第二次 Timer0 交付必需） | ✅ base 栈 | HANDOFF 路 F |
| TIMER2 投影 + 闭式 horizon 并入 min() | ✅（B1；4000 步闭式单测钉死） | HANDOFF 路 B1 |
| 事件时钟闭式/资格预检/停泊步共用分发（G1/G2） | ✅（BQ/sim-evclockfix） | d1-idle-event-clock §3.2 |
| 闭式常量 `STEPS_PER_MS=4000` / `TIMER0_STEPS_PER_10MS=40000` | ✅ 命名常量+四层断言 | 路 INV（falsification-checks §4） |
| core-tick CNT held 快照 `core_tick_cnt_held_snapshot`（+敏感性探针 probe_only） | ✅（B3，48a49d0；敏感性对照已测=0 vs PRD-1 全同，仅停 PC 漂移/sub-tick 差≤9ms） | HANDOFF 路 B3 |
| 事件时钟真实推进+停泊-唤醒往返+108 次 core tick 交付/ACK 实测 | ✅ 运行数据已在（B3 前缀报告） | HANDOFF 路 B3 条目⑥ |
| D1-1..D1-4 + BI A1-A5 断言函数 | ✅ `tools/check_d1_acceptance.py`（`ASSERTIONS` 注册表挂全；`--report` CLI 形态） | 路 CH |
| `test_d1_acceptance.py` 单测 | ✅ 已进 `check_local --units` | 路 CH |
| 验收记录器 `tools/acceptance_record.py`（九栏+证据等级） | ✅ | 路 AX |
| 快照框架 `world_snapshot.py` + M1/M4 适配器 | ✅（可选加速件；SNAPX 后已含 19M canonical 后全部四容器适配器+验证轮入口，见下「快照续跑」行） | 路 BJ/BN + sim-snapm4 分支（M4）；SNAPX 补齐四容器（DT/ADA/LPV/JLA） |
| **D1-5 / H1-H8 机检** | ✅ **已完成**——`d1_5_health` 挂 `ASSERTIONS`+否定式总闸 `d1_gate`（PR-1，`fafaffd`，单测 34→70）；H8 窗口相容性自检+H1 联动拦截+总闸不抵消（PR2H8，`1cc166d`，单测 70→78）；键契约冻结在 [d1-health-checks.md](d1-health-checks.md) | 路 PR-1/PR2；§1.0b |
| **probe 三参数 `d1_acceptance`/`d1_window_steps`/`d1_post_inject`** | ❌ 无（grep 零命中）——PR-2 生产者侧，等闸门② | 本任务书 §1.2，等前沿 |
| **场景行 `d1_acceptance_window` + `d1_window.*` 报告键** | ❌ 无——PR-2 生产者侧，等闸门②（消费侧 d1_1..d1_5+BI A1-A5 已全部就绪并按 §2 契约对表） | 本任务书 §1.2，等前沿 |
| **断言离线预检器 `tools/assert_preflight.py`**（CHK，`be9c6bb`） | ✅ 三类报告（probe_boot/probe_irq_entry/analyze_image_trace）断言面零仿真离线预检，秒级（LPV 212 断言面教训固化）；`check_local --preflight` 可选档 | 路 CHK；§3 自验/各 PR 引用 |
| **快照续跑 `--capture-snapshots`/`--resume-from`**（SNAPX，`a2f0b13`） | ✅ 21.8M 条件三臂 68m33s→验证轮 2.2s；**首次过线仍必须全量重放**（红线 5/§2.1；`--self-check`/`--candidate-self-check` 拒 resume）；行为改动=双闸拒载强制重捕获 | 路 SNAPX；[snapshot-resume.md](snapshot-resume.md)；PR-D0/PR-3/PR-4 用 |
| **三臂进程并行 `--jobs N`**（PAR3，`a2f0b13`） | ✅ 3.03x（68m33s→22m39s），报告逐字节相同，比对仍单进程 | 路 PAR3；PR-D0/L2-a 复跑用 |
| **事件时钟性能口径**（O7E，`cc10784`） | ✅ 实测 EC 段 2.95% CPU（8M 虚拟步≈13.4s），**全程到 park 单臂外推 ~18min**；PROF3 7-17% 上界纠偏 | 路 O7E；§1.2 运行集合决策/§1.1 预算 |
| **差分线钉值机检**（R4SNAP，`546b73c`） | ✅ r4_keyhook 两档（crc+conditional）candidate-self-check 独立钉值（条件档结构锚单源式跟随前沿）；4 篡改负例全红 | 路 R4SNAP；§1.2 第 8 条/§1.6 第 3 条 |
| **D1 验收记录底稿**（PR3P，`3a1a692`） | ✅ `docs/acceptance-samples/d1/` 五条九栏 NOT_RUN 预填+面板 | 路 PR3P；§1.6 |
| **S19 终局图**（S19MAP，`979a99c`） | ✅ [s19-endgame-map.md](s19-endgame-map.md)：17 行停点表+park 预算+三问裁决 | 路 S19MAP；本任务书闸门②判据来源 |

### 0.3 与蓝图 §8 工作量表的差量（现状修订，实施路以本表为准）

蓝图 §8 写于批 0b 之前，其步骤 1「BI G3：event_clock 进 irq_case」**已被 CW 提前
完成**（§0.2 第一行）。批 3 的步骤 1 相应修订为「首验运行 + A1-A5 从 pending 升硬
断言」（§1.1），不再有接线工作。**cc10784 刷新**：蓝图 §8 步骤 5（D1-5 H1-H7 机检
+总闸，估 0.5-1 天）已由路 PR-1+PR2H8 完成（含 REVIEW 盲区 #5 的 H8 增补）；步骤 6
的验收记录底稿已由路 PR3P 预填——批 3 剩余工作量集中在步骤 2-4（PR-2 生产者侧，
等闸门②）与步骤 6 的 verdict 刷新（PR-3），合计估 2.5-4 个工作日（蓝图原估 3.5-5
含已完成项）。

### 0.4 前置 P 表刷新（cc10784 三态；蓝图 §11 表（48a49d0 口径）不改动，本表为续篇）

| P# | 三态（cc10784） | 变化要点（vs 蓝图 §11） |
|---|---|---|
| P-1 DAA_CON1 | ✅ 绿 | 不变 |
| P-2 W1[3] 尾段+DAC 簇 | **在途**（挡路者再换防） | DAC_TM0 已放行（DT，19.1M→21.8M）；JLA/LPV/ADA/ASS 连环推进到 28,764,184；**新挡路者=flash 顶窗口读**（S19MAP 表 #0，FLS 在途；同族 5 事务一窗口覆盖，窗口方案优先于逐指针 fixture） |
| P-3 BA#1-3 | ✅ 绿 | 3.2M→28.76M 仅音频域停点链，判定维持 |
| P-4 W4[4] 簇 | ✅ 绿（**预解决**） | 批 1 三开关+RES 行已落地，S19MAP 表 #9 确认零裁决；剩首跑形状（v 值走向/量测分支必触，有界+敏感性探针，风险=实现 bug 非裁决缺口） |
| P-5/P-6 W4[5]/S11-S14 | 在途 | AY 对账后新 SFR 欠账零（N2 9 个健康路径零触达推迟 D1 后）；BA「通过」判定维持 |
| P-7 P2M 邮箱 | **在途（B2R 接手）** | S19MAP 表 #11：首停 0x12CC94、BM §3 设计全备、实现 `0b875ed` 重落地；邮箱门 b[0x1299f8] 写者未钉（实测 1，FLS 放行跑顺手复测） |
| P-8 S16 事件时钟 | ✅ 绿（机制） | 事件时钟 13,429,113 虚拟步实测冻结中（S16 起再启动，~2×10⁶ 步）；O7E 性能销案：EC 段 2.95% CPU，不构成路钟瓶颈 |
| P-9 cfg523 | **在途（B2R 接手）** | BH 方案 (a) 设计全备+RES 行 0x10001=4 已落地；**落地激活 S9 键扫 10-tick sys_timer（§1.3b 联动）**；三处合成值面=最大不确定性之一（S19MAP §3.2#2） |
| P-10 S17 budget | 在途 | budget 按 park 预算 3.7-3.9×10⁷ 口径复核（S19MAP §2：S16+S17 ~8M 占 80%+ 模型步） |
| P-11 S18 特路 | 在途（**风险销项**） | ID2 已钉：id=2 节点=编译期 DATA 段静态节点、B1[0] init pre-tail 挂链、零运行时依赖，(20,2) 消费链零停点零回调——存在性缺口关闭，仍为运行观测项不升必经 |
| P-12 S19/park | 在途（=闸门②本体） | S19MAP 表 #15：S19 循环→空队列 pend→两核 park 纯 RAM/OS 零 unmapped；btstack 创建（#16）非 park 前置、D1 不需要 BT |
| P-13 BI 前置 | ✅ 绿（checker 侧） | A1-A5+D1-5 H1-H8 全在 check_d1_acceptance（CH+PR-1+PR2H8）；首验数据=等 park 样本（B3 前缀数据+P-12 后窗口样本） |
| P-14 D1 前置 | 设计就绪+**checker/底稿全绿** | 本任务书（B3T 刷新版）；生产者侧=PR-2 等闸门② |
| P-15 快照 | ✅ 绿（工具面） | SNAPX 后捕获/恢复/验证轮入口全在 main（19M/21.8M canonical+等价性三层）；**park canonical 快照待过线后生成（PR-D0）**；非过线必要条件维持 |

统计（cc10784）：**绿 7**（P-1/P-3/P-4/P-8/P-13/P-14 checker 侧/P-15 工具面）/
**在途 8**（P-2=FLS、P-7/P-9=B2R、P-5/P-6/P-10/P-12=同链后续段与闸门②本体、
P-11=风险已销项仍留运行观测）/ **硬缺口 0**。

## 1. 任务分解

### 1.0 步骤 0：开工闸门核对 + BR 校准空位表（合并后第一件事）

B3 已合并（48a49d0），本表 CNT/ADDA/电池段三组已转正；剩余空位=前沿/park 形态组
（**闸门②过后再动 probe 侧**；本任务书正文凡「等前沿」处都指向这张表）：

| 空位 | 值（48a49d0 已合并转正 / 待前沿） | 校准动作 |
|---|---|---|
| CNT 读开关名 | `core_tick_cnt_held_snapshot`（conditional，依赖 `core_tick_prd_readback`）——**已合并转正** | §1.4 引用名即此（48a49d0 registry） |
| CNT 假设原文 | 「显式『溢出后 CNT 冻结』假设 assumed-not-measured（模型 artefact：溢出后 advance 不再推进、cnt 停在 divmod 余数；硬件倾向回绕继续计数但无 BR28 证据不声明）；非 PRD-1/非周期末值/非合成默认；读无副作用」——B3 报告措辞含英文原文 "under the explicit 'CNT frozen after overflow' assumption (conditional, assumed-not-measured) — NOT PRD-1" | D1-3 假设标注（§1.4）的 model_assumptions 文案照抄 B3 registry/报告版 |
| CNT 敏感性探针 | `core_tick_cnt_snapshot_probe_value`（probe_only）——**已合并**且对照已测：0 vs 周期末值 PRD-1 两分叉全同（core tick 52/52 交付/ACK、Timer0 1663、同 budget 停点 kind），仅停 PC 同 helper 族内漂移+sub-tick 相位差 624（≤9ms）只进 OS 时间换算 | D1-3 断言在两分叉下行为差异已实测（HANDOFF B3 条目③）——任务书不再留「若保留」分支，标注义务=如实引用该对照 |
| ADDA 族开关名 | `jl_adda_analog_family`（五字容器）——**已合并转正**（94 写全服务） | 场景行 `scenario_flags('p11_heap_experimental')` 的 flag 集=48a49d0 条件栈实取（B3 后含 ADDA 族、不含电池段两撤回开关） |
| 电池段开关名 | ~~`p2m_mailbox_ack` / `cfg523_named_fixture`~~ **撤回→B2R 重落地在途**（已实现+单测但零消费者，按 BQ 先例不进条件栈；实现保留 0b875ed；S19MAP 定调批 2 一路打包 p2m+cfg523 重落地+负例单测，负例钉子=邮箱关停 0x12CC94 / cfg523 关→S17 K2 自旋） | 开关名/地址以 B2R 落地为准；**B2R 合并前**批 3 场景行 flag 集**不含**这两个开关，合并后按条件栈实取重核（§1.2 第 3 条「构造器+实取」本就免手抄） |
| 前沿/park 形态 | 前沿 **28,764,184 @ 0x606c5ba 读 0x61fbfe0**（W1[3] 尾段音频效果配置装载的 flash 顶资源取数，S19MAP §1 解剖）；途中停泊已真实发生但 S19 终态 park 未到 | **闸门②=S19MAP 新口径**：FLS（flash 顶窗口放行，建议窗口方案并进 erased_bankb 族擦除窗+miss 侧有界性观察）→B2R（p2m+cfg523 重落地）→推进跑过 S16-S18 到 **park ≈3.7-3.9×10⁷ 模型步**（最少 2 路计划 3 路，S19MAP §0/§3.3）；到达前批 3 probe 侧挂起，免闸门件已全部完成（PR-1/PR2H8/PR3P） |
| BI 首验观察口径 | B3 已实测「BI 的 A1 形状部分显形」（事件时钟 13,429,113 虚拟步/停泊-唤醒/108 次溢出交付 ACK/jiffies 118）；S19MAP 补：该推进为 boot 期 OS 等待族，事件时钟当前冻结、**W2[0] 3×os_time_dly(2)（6 tick）才是 p11_heap 场景首个 init 路消费者**（表 #6），S16（5×dly(10)）/S17（~8M）在其后 | CW 的「A1-A5 硬断言属批 3 只观察」→PR-BI 执行升级；A1/A2 的窗口形态判据仍需 S19 后 park 样本 |

### 1.0b 步骤 0b（PR-1，不受闸门限制）：D1-5 / H1-H7 机检 —— ✅ 已完成（2026-11-16 刷新）

**完成状态（路 PR-1 `fafaffd` + 路 PR2H8 `1cc166d`）**：`d1_5_health` 已挂
`ASSERTIONS`（D1-1..D1-5+BI-A1..A5 共 10 条）+`run_all` 否定式总闸键 `d1_gate`；
下述第 1-9 条口径全部落地，其中第 6/7/8 条的实现修正（H6 逻辑序号/H5 两级受检/
FAIL 证据契约）按本任务书 §5 参谋结论②执行；单测 34→70（PR-1）→78（PR2H8）。
**键契约已冻结在 [d1-health-checks.md](d1-health-checks.md)**（PR-2 生产者按其对齐
生产，键名/语义变更须先改该文再改代码）。REVIEW 盲区 #5 裁决由 PR2H8 半前移为
**H8 观察窗相容性自检**（`h8_window_cycle_coverage`：折算 `N//P_park<3`→
insufficient_window_coverage 拒绝给结论+H1 联动拦截+总闸不抵消；**零新增生产键**，
折算输入复用 `health.window.steps`+`per_source[timer0].period`）——生产者侧义务=
park 落定后按实读 P_park 自检覆盖（<3 延长 N 或报「观察窗不足」），同一公式
`N // P_park >= 3`（§1.2 第 7a 条）。以下原文保留为规格口径存档。

**放 `tools/check_d1_acceptance.py` + `tools/test_d1_acceptance.py`**（两文件不在
前沿路占用文件清单；check_local units 层已挂 `test_d1_acceptance.py`）：

1. 新函数 `d1_5_health(scenario)`，挂进 `ASSERTIONS` 为 `('D1-5', d1_5_health)`
   （注册表现在止于 BI-A5，行 1156）。
2. H1-H7 规则照蓝图 §3 表逐条实现，数据面=蓝图 §4.3 的 `d1_window.health.*` 报告键
   **形状**（报告键本体要等 §1.2 的 probe 侧；先按蓝图键名契约写断言+用合成
   scenario 样本做单测——`test_d1_acceptance.py` 已有合成样本先例，见其
   `bi_a3`/`bi_a4` 的构造方式）。**合成样本单测只证明 checker 有分辨力，不计入
   D1 首次过线证据**（§5 参谋结论③）。
3. 上界公式的口径：H2 的 Timer0 P 与 core tick P 一律从 `health.per_source` 的
   `period` 实读字段取（蓝图 §3「park 态实读为准，不预锁 40,000/300,000」）；
   core tick 的 period 源头是 PRD 读回（BQ：1199999 恒等最近真实写入）——**该值
   依赖链上的 CNT 冻结假设按 §1.4 标注**。上界计算记录首次期限/窗口起点 pending
   状态（相位容差；`⌈N/P⌉+1` 的 +1 即此意图，报告键补 `first_deadline`/`pending_at_window_start`）。
4. 否定式总闸：D1-1..4 全 PASS 而 H1-H7 任一 FAIL → `run_all` 汇总 D1 仍 FAIL
   （蓝图 §3 原文；实现为 d1_5 的 fail 态 + 汇总层不抵消）。
5. 单测：每条规则至少 PASS/FAIL 各一形状 + 「H 任一 FAIL 时总闸红」一例 +
   「报告键缺失=pending 非 pass」（check_d1_acceptance 的 pending 语义先例：
   停点前场景不算 FAIL 也不算 PASS）+ 边界样本（截断/字段缺失≠零事件≠PASS）。
6. **H6 勘误（参谋结论②，对蓝图 §3 H6 行的实现修正）**：`cursor_monotonic` 不检查
   物理指针单调——环形队列指针正常按容量取模回绕。实现为：逻辑入队/出队**序号**
   单调 + 物理指针=序号 mod 容量的一致式。蓝图 §3 原文不改动，本条为实现口径。
7. **H5 优先级语义（参谋结论②）**：固定优先级调度器无公平性保证，「所有 ready
   任务窗口内必获调度」过强。受检集分两级：D1 断言面任务（app_core 消费链相关）
   必须调度（FAIL 级）；其余就绪任务未调度=如实记录（观察级，不 FAIL）。等待
   时长字段（ready 起点/最长连续等待/首次最近调度）在键里保留。
8. **FAIL 证据契约（参谋结论②，七规则共用结构）**：每条 FAIL 附
   `{rule, expected, observed, first_violation:{模型步, core, PC}, 相关 source/task,
   违反前后状态转换片段, 窗口末尾状态+证据完整性}`；计数/峰值/最长等待在线累计，
   片段只存首次违规+最严重违规。H4 的未闭合唤醒区间必须含最后 PC/在途 IRQ 及
   嵌套层级；H2 风暴含 per source+core 的到期/交付/完成三分计数。窗口右边界条款：
   窗口末尾刚唤醒/刚 ready 的对象标「尚未到期」，需要完整样本的验收项要求足量
   完整样本，不默认 PASS。
9. 可收敛项（参谋结论②砍单，实施时不做）：不存逐指令 timeline（存状态转换流）；
   `scheduled` 布尔由调度次数推导不单独存；`entry` 进任务元数据表一次、运行记录
   引用任务 id（若观察到 TCB 复用再加实例代次）；`wake_reasons` 存计数+关联 id
   不存全列表。

### 1.1 步骤 1（PR-BI，等前沿=闸门②）：BI 首验（真实前缀首跑 A1-A5，先观察后硬断言）

**定位（参谋结论③）**：独立 PR，观察性质——**观察失败不阻塞场景骨架（§1.2）的
开发与合并，但阻塞依赖该性质的 D1 验收**；已有证据支撑的 A1-A5 不因观察阶段统一
降级（观察≠放松，是记录充分再升硬）。

0. **数据基础（cc10784 已备）**：B3 前缀已实测事件时钟推进 13,429,113 虚拟步、
   停泊-唤醒往返、core tick 溢出 12..108 全交付/ACK、jiffies 终值 118、Timer0 到期
   372→3401（HANDOFF B3 条目⑥）——A1 的「停泊-唤醒往返」形状与 A2 的「周期计数」
   原料已有；S19MAP 补齐消费者序列：当前前缀事件时钟**冻结**在 13,429,113（boot 期
   纯 CPU0 顺序段），**W2[0] 3×os_time_dly(2)（6 tick）=p11_heap 场景首个 init 路
   消费者**（S19MAP 表 #6，CW 注记「本前缀惰性」由它兑现）、S16 5×dly(10)≈2×10⁶、
   S17 环 ≈6×10⁶+151 邮箱事务（健康侧）在其后。PR-BI 先整理这批现有报告数据，窗口
   形态判据（A1 闭式精确到期/A2 周期守恒的窗口版）再等 S19 后 park 样本。
1. 场景=当前条件栈 flag 集（B2R 合并后按实取重核）+ `event_clock=True`，budget
   **全程一次给足**：park ≈3.7-3.9×10⁷ 模型步（S19MAP §2）+窗口 N+余量（§2.1
   第 1 条 Q1 形态；旧文「首个全核 IDLE 点+80,000 步」是途中停泊口径，park 后由
   §1.3 窗口公式接手）。成本：O7E 实测外推**单臂全程到 park ~18min**（事件时钟段
   仅 2.95% CPU，45min 线余量扩大）——首验一轮可承受，无快照依赖（首次过线必须
   全量重放，§2.1）。
2. 跑真实前缀到 park，`check_d1_acceptance.py --report … --scenario <首验场景>` 出
   A1-A5 结果；**首轮全部只观察**（CW 口径「硬断言属批 3 只观察」）——把 A1（闭式
   精确到期）、A2（周期守恒）、A3（无事件零推进）、A4（期限缺口）、A5(a)（E1 往返）
   的实测值 vs 判据逐条记录，不符处先归因（观察项还是断言错）。
3. 观察一轮无异常后，把 A1-A5 从「pending/观察」升为硬断言（改 `test_d1_acceptance`
   的期望+check 函数的 fail 条件），并在 HANDOFF 条目记录升级依据。**A5(b)（任务
   恢复全证）不在此步**——那是 D1-3 的观察项（蓝图 §2.3「任务切换=观察项非硬断言」）。
4. 事件时钟首个消费者的实测口径：W2[0] `3×os_time_dly(2)`（CW 定调）——首验时
   `virtual_model_steps_advanced` 应首次非零；若仍为零，说明 park 判定或停泊形状
   与预期不符，停下归因（这是 A1 的前置观察）。

### 1.2 步骤 2（PR-2，等前沿=闸门②）：D1 场景骨架（probe_only 三参数 + 场景行 + 报告键 + 正式验收入口）

**状态（cc10784）**：**checker 半边已落地**——PR2H8（`1cc166d`）把窗口相容性自检
H8 做进 check_d1_acceptance（含 H1 联动拦截+总闸不抵消，8 反例），加上 PR-1 的
H1-H7，消费侧对 `d1_window` 键契约（[d1-health-checks.md](d1-health-checks.md)）
已全部就绪；**生产者侧（下列 1-8 条）整体等闸门②**，落地时按冻结契约对表生产、
键名/语义变更先改契约文档再改代码。

接线方式（蓝图 §4.1/§4.2/§4.5 的落地细节）：

1. **probe 参数**（`tools/probe_irq_entry.py` 的 `irq_case` 签名）：
   `d1_acceptance=False`（场景开关）、`d1_window_steps=None`（值开关，None=默认公式
   §1.3）、`d1_post_inject=None`（值开关，默认关）。接线形态照 `event_clock`
   参数的 CW 先例（machine_options 只在场景行打开）。**值开关激活判定用
   `is not None` 不用 truthy**（参谋结论①：避免 0/空串意外激活依赖）。
2. **registry 条目**（`tools/frontier_registry.py`）三条 probe_only Feature
   （蓝图 §4.2 草案原样）：`d1_acceptance` requires=('event_clock',
   'timer0_irq4_delivery','core_tick_irq3_delivery','repeat')——`key_chain_scan`
   先例允许 probe_only 引条件栈成员（闭包校验两向过）；`d1_window_steps`/
   `d1_post_inject` 各 requires=('d1_acceptance',)。
3. **场景行**：`irq_case(..., name='d1_acceptance_window', **scenario_flags(
   'p11_heap_experimental'), event_clock=True, d1_acceptance=True, budget=<park
   指令步+窗口执行预算>)`（`key_chain_scan_direct` 行先例）。flag 集=**当前 main
   的** p11_heap 条件栈全量（经 `scenario_flags()` 构造器+实取，不手抄开关表、
   不改公共条件栈字典——参谋结论①；48a49d0 时的「不含电池段两撤回开关」按 B2R
   合并后实取自动重核）。**场景行加在场景表
   末尾**（15 行现有场景之后；参谋结论①(a)：无语义理由提前，若顺序影响正确性
   说明有共享可变状态要修的是那个）。
   **运行集合决策（参谋结论①「开关默认关≠场景默认不运行」，先例核实后明确）**：
   本仓场景表惯例=15 场景每次全量都构造都跑，opt-in 的含义是**场景内行为差异**由
   开关控制（`key_chain_scan_direct` 的直调段靠 post_stop 钩子默认 None 保持
   legacy 不变，场景本体每次跑到停点）。D1 场景全程 **≈3.7-3.9×10⁷ 模型步+窗口**
   （S19MAP 口径；旧文 1.2×10⁷ 是 BM 旧预算），单臂 ~18min（O7E 实测外推），若进
   常规全量会把 `check_local` 默认层显著拉长——PR-2 落地时二选一并记录：
   (i) 接受成本，场景进表全跑（stash 对照面简单：旧 15 行逐字节不变+新行新增）；
   (ii) 显式选择才纳入运行集合（probe CLI 加场景过滤旗标，常规回归不含 D1 窗口
   段；D1 验收跑=显式旗标+正式验收入口 §1.2 第 7 条）。**倾向 (ii) 维持且加强**
   （18min×每次全量远超旧「分钟级」论证；且过线后日常回归有更便宜的替代——
   assert_preflight 断言面秒级离线预检+PR-D0 park 快照验证轮，常规层无需每轮
   重跑 D1 场景），但 (i)(ii) 都必须 legacy 对照绿。
4. **观察件**（蓝图 §2 公共机制，进 probe 运行循环）：
   - 具名锚点 PC 计数器+首达步（coverage 面，`key_chain` 先例）：S19 循环头
     0x60123e6、get-event 0x60123ea/0x6064c8c、特路 0x60108fe、pend 写面
     0x6064cf6..0x6064d06、让出链 0x6064188/0x60642bc/0x6064340、金丝雀
     K1 0x601249c/K2 0x60124ae、handler 0x6023962、tick 回调 0x603622C、
     core-tick handler 0x60661a4、时间片 0x60653AE、Timer0 RTI 尾锚 0x12ed0c
     （全部锚点出处=蓝图 §0 速查表，不新增锚点）；
   - per-core 状态时间线（entered_idle_wait/woken/running 序列，H7 数据面）；
   - 全局模型步计数器（advance() 总调用数含事件时钟段）=`d1_model_steps_total`；
   - park 检测：`event_clock_engaged` 首次 → 记 W_park（模型步+指令步双轴）→
     窗口步计数器从 0 起算。
   - 观察件是纯只读旁路（不改计步位置/预算消耗/IRQ 交付顺序——参谋结论①(d)
     耦合清单项）；报告公共字段与序列化顺序不动。
5. **报告键**：蓝图 §4.3 的 `d1_window` 树全量（park/s18_first_pass/
   empty_queue_pend/dispatch/canaries/timer0_cycles/core_tick/d1_4/health/
   双轴步数/window_blocked_events/verdicts/projection 措辞）。
6. **窗口机制**：`d1_window_steps` 耗尽=停+`verdicts` 汇总（超时=FAIL 语义，
   蓝图 §1.3「NOT_RUN 只给 D1-4 无样本」）；窗口内任何 Blocked →
   `window_blocked_events` 记录（D1-2 FAIL，蓝图 §2.2「旧 kind 也不算过」）。
   **D1 场景的成功终点=观察窗完整结束**（不是 park——park 是窗口起点）；场景行的
   完成记录含 `stop_kind='window_complete'`+起止计数+实际覆盖长度+两核终态
   （不压成单 PC——参谋结论①(b)）。`FRONTIERS['p11_heap_experimental']` 的重钉
   **归前沿推进路**（批 3 不为 D1 重钉旧前沿——参谋结论①(b)；S19MAP §4.5 重钉
   纪律维持：每路过线后单点重钉、park 形态停点在批 2 放行跑同一 PR 定死），D1
   场景单列结果行。
7. **probe check() 只做最小形状断言**（报告键齐全、park 形状、窗口完成标记、
   canaries=0）；**五断言+H1-H8 的实质机检全部归 check_d1_acceptance.py**
   （§1.0b 已备 d1_5+H8；d1_1..d1_4 函数按 `d1_window` 键契约适配）——分工理由见
   §5 参谋结论①。**正式验收入口**（参谋结论①(c)，防「probe 自检绿而实质验收
   未执行」空档）：D1 场景行跑完时 probe 侧调用 `check_d1_acceptance.run_all(
   <d1_window 场景记录>)` 并传播其 FAIL 为非零退出码——断言逻辑仍单源住在
   check_d1_acceptance.py（不与 probe check() 分叉），probe 只加调用与退出码。
   「字段缺失/截断/窗口未完成=证据不足」按非零处理，不当零事件、不当 PASS
   （H8 insufficient=pending 非 pass，同样非零退出——修 N 重跑，不是 DUT 失败）。
7a. **H8 生产者侧义务（PR2H8 剩余半边，随窗口机制落地）**：park 检测落定后按
   实读 P_park 自检窗口覆盖 `N // P_park >= 3`，不足则延长 N（`d1_window_steps`
   公式内自调）或报「观察窗不足」不判过——与 checker 侧同一公式（d1-health-checks
   §5 对接面），生产者无需为此新增任何键。
8. **parity 影响自验**（参谋结论①(d) 耦合清单）：PR-2 合并前核 analyze_image_trace
   的 self-check 无需变更（新场景默认关+不加进其场景集）；若 crc_bankb/
   p11_heap 两场景报告有任何字段级变化，先解释差异再动钉子。「逐字节不变」的
   对照对象=旧场景各自的报告记录（整份汇总 JSON 因新增场景行必然不同）。
   **断言面改动先离线预检（CHK 新工具，替代「改断言→烧全量才知道红」旧流程）**：
   probe check()/check_self 断言值调整时，先 `python3 tools/assert_preflight.py
   <既有报告 json>` 秒级核对（LPV 4×35min 全量教训固化的工具），绿了再上全量
   verify；`--preflight` 档在 check_local 已可挂标准报告集。
   **镜像判据口径（48a49d0 路 A1 结论）**：b79↔EQ 的 +458 lag 重合在 447k（首
   core-tick 溢出交付轴）后被交付相位差永久破坏——**跨 447k 的「零控制流分歧」
   判据机制性不可用，须卡窗或改事件计数等价判据；EQ 镜像可用前沿=447,490**
   （HANDOFF 路 A1 条目）。D1 场景单镜像运行不受影响；L2-a（过线后独立步）设计
   必须按事件计数等价判据，禁用零分歧判据。**差分线现状（R4SNAP，`546b73c`）**：
   r4_keyhook 两档 candidate-self-check 已有独立钉值机检（条件档结构锚单源式跟
   随前沿推进自动跟随），4 篡改负例全红——D1 过线后 L2-a 若引入 r4 臂可直接复用
   该机制；遗留一处：l1 条件档 czd 字面停在 DT 前沿时点（21,798,206−19,123,794），
   当前前沿下跑 l1 条件档将红，归 DIF/l1 线重钉（PR-2 不碰）。

### 1.3 步骤 3：观察窗 N 的公式与 P_park 实读的实施点 [等前沿]

- **N 的默认公式**（蓝图 §1.3）：`N = S_tick(W_park) + 160,000`，下限 200,000。
  S_tick=从 park 态 core tick 的 CNT/PRD 推出的首溢出闭式期限
  （event-clock §2 条 3 的 S=ceil 公式）。实施点=窗口起算时（park 检测落定后）
  在 probe 侧现算：读 park 态 `core_tick_state.{cnt,prd}` → 闭式 → N 取
  max(公式值, 200,000)；`d1_window_steps` 显式给值时覆盖公式（值开关语义）。
- **P_park 实读**：park 态 `timer0_state.{con,cnt,prd}` → `period_closed_form`
  用 INV 常量链（STD12M 3/4 + divisor + PRD+1-CNT）现算；**不预锁 40,000**
  （BY 实测 delay 函数按调用重编 PRD：59999→47999→11999 三态都出现过，park 末态
  =运行观测项 R1）。D1-2 判据=「相邻到期间隔恒=P_park」+如 P_park≠40,000 如实
  报告（蓝图 §1.3 口径；BI A2「配置不变时」条款照抄）。
- **H2 上界同步**：`⌈N/P_i⌉+1` 的 P_i 用同一实读源（§1.0b 第 3 条）。
- **N 落定后即跑 H8 自检**（§1.2 第 7a 条）：`N // P_park >= 3`，不足延长 N 或报
  观察窗不足——160,000 余量是按 P=40,000 形状值预留的，BY 三态重编下余量可破
  （PR2H8 反例 P=70,000@N=200,000 已钉），park 实读值出来前不预设结论。

### 1.3b 联动注意事项：cfg523 → S9 键扫 10-tick sys_timer → H2 上界/D1 窗口期望值 [B2R 在途，PR-2 实施必读]

S19MAP §4.3/表 #5 定调（B2R 落地耦合，2026-11-16 增）：**cfg523 落地会激活 S9
键扫 10-tick sys_timer**——config 523 解析成功后 S9 注册键扫描定时器（含两处 IO
配置调用+adc_scan 键通道轮选的新执行面，BH §0），它成为 D1 窗口内继 Timer0/
core tick/软件 IRQ124-127 之后的**新周期性行为源**。换算：1 tick=10ms=4×10⁴ 模型步
（INV 常量链），10-tick≈**4×10⁵ 模型步**（运行实读为准，不预锁——同 P_park 红线）。
对 PR-2/PR-3 的四点影响：

1. **H2 的 per_source 必须含它**：缺这个源=键扫交付无上界锚（per_source 缺项=
   H2 pending 非 pass，同「键缺≠零事件」语义）。上界公式 `⌈N/P⌉+1` 本身对任意
   P 成立（+1 即相位容差），无需改公式；P 实读。其在 per_source 里的条目形态
   （独立源 vs timer0 衍生计数——sys_timer 到期挂在 Timer0 tick handler 的
   S8 检查之下）由 PR-2 生产者按实测定型，**若需新增键先改
   [d1-health-checks.md](d1-health-checks.md) 契约再改代码**。
   〔2026-11-16 更新（路 H2SRC，基线 main `3a64ec2`）：形态已静态钉死并落地——
   D1S9/S8SCAN 定为 timer0 衍生 sys_timer 条目（`kind='sys_timer'`、period=
   10×/5×P_timer0、三分计数坍缩同数），d1-health-checks §2 已加 `kind` 字段+
   周期源表，checker `PERIODIC_SOURCES` 同步四源+8 反例单测；「先改契约再改代码」
   已完成 checker 半边，PR-2 只剩生产者 emit（adc_scan 无条件必 emit、keyscan 挂
   cfg523 条件栈）。缺源语义随之精化：**keyscan 缺条目不 pending**（cfg523 条件性，
   触达才入表——D1S9 §3.3 对本条「缺项=pending」的修正）；**adc_scan 缺条目=
   pending**（init walker 直线段无条件注册，S8SCAN §5.2）。〕
2. **d1_window 期望值**：健康报告的周期源清单、`wake_reasons` 计数（键扫唤醒/
   sys_timer 唤醒会出现非 timer0 源）都要能容纳它；H4/H7 的 wake_reasons 求和=
   wake_count 判据自然覆盖。
3. **窗口覆盖**：默认公式 N=max(S_tick+160k, 200k) 对 P≈4×10⁵ 覆盖不足 1 个完整
   键扫周期——**H8 只折算 Timer0 的 P_park，不受影响**；但若验收想观察 ≥1 个
   键扫周期，需显式加长 `d1_window_steps` 并如实报告覆盖数（右边界条款：
   「尚未到期」不默认 PASS，不因覆盖不足判 FAIL——窗口配置问题≠DUT 违约）。
   是否加长=PR-BI 观察轮数据定，不预设。
4. **条件性**：该源只在 cfg523 开（B2R 落地且场景 flag 含它）后存在——PR-2 生产者
   按条件栈实取 flag 集自适应（场景行构造本就 `scenario_flags()` 实取），不硬编码
   「必有键扫源」；B2R 合并前跑 D1 场景则该源不存在，per_source 相应少一条，
   H2 对不存在源不 pending（与 N2 零触达同款：触达才入表）。

### 1.4 步骤 4：D1-3 时间断言的 CNT 假设标注（硬性要求，**标注机制随 PR-2 落地、PR-3 确认贯穿落档**——参谋结论③）[开关已转正，标注实施等前沿]

**codex CNT 定调**（已随路 B3 合并转正，48a49d0 registry/报告措辞）：「显式
『溢出后 CNT 冻结』假设 **assumed-not-measured**（模型 artefact：溢出后 advance 不再
推进、cnt 停在 divmod 余数；硬件倾向回绕继续计数但无 BR28 证据不声明），非 PRD-1/
非周期末值/非合成默认；该场景快照=0（第 12 次溢出对齐余数恰 0）；读无副作用」——
报告英文原文 "under the explicit 'CNT frozen after overflow' assumption
(conditional, assumed-not-measured) — NOT PRD-1"（HANDOFF 路 B3 条目）。B3 已为
D1-3 定锚：check_d1_acceptance 的闭式 `ceil((prd+1-cnt)/progress)` 从 park 快照取
cnt，**携带该冻结假设**（只记录不代写）。

执行要求：

1. **D1-3 的一切时间/顺序/期限断言必须带该假设**，包括：闭式期限 S_tick 的
   CNT 起点（park 态 CNT 读值=冻结假设的产物）；`core_tick.actual_overflow_model_step`
   与 `deadline_closed_form` 的比对；BQ 条目③钉死的「PRD→r3 用途=sub-tick 换算
   分母（CNT*1000/r0）」路径上一切由 CNT 推出的中间值；H2 的 core tick 周期上界。
2. **不能因「进入循环/窗口运转」解除**：窗口内 core tick 溢出-交付-ACK 的行为
   由 BF 每步重检机制保证（measured 路径），但 **park 态快照的 CNT 起点永远继承
   冻结假设**——时间断言的假设标注是断言的组成部分，不是场景的阶段性状态。
   实现为：`d1_window.core_tick_state.assumption='core_tick_cnt_held_snapshot:'
   'assumed-not-measured (NOT PRD-1)'`（开关名已转正），且 check_d1_acceptance 的
   d1_3 fail/pass 判定输出里带该字段；缺失=断言形状不完整（pending 非 pass）。
   **checker 侧已落地（PR-1）**：H2 的 core tick 周期上界承载该假设，assumption
   键位两处兼容（d1_window 顶层优先 vs 蓝图 §4.3 示例树 park 下），PR-2 落地建议
   顶层（d1-health-checks §2 冻结契约）。
3. **验收记录**：D1-TCK-03 的 `model_assumptions` 栏必含该假设原文（acceptance_record
   的 conditional 校验会要求 PASS 条件出现在模型假设栏——§1.6 步骤 6 落档时核）。
4. **区分两代读回**：PRD 读回（BQ，`core_tick_prd_readback`）=回放最近真实写入
   1199999，恒等证据链，**不承载冻结假设**；CNT 读回（BR）=冻结快照，承载。两者
   在报告键与假设标注里不得混写。

### 1.5 步骤 5：D1-4 主样本与注入变体（主样本进 PR-3；注入变体=PR-4 单独）[等前沿]

- 主样本=固件自然产生的 (18,0)，全链站点表=蓝图 §2.4（post 实参→入队→get-event
  13→id→特路→表 2/过滤链/表 1 walk→handler 0x6023962→返回→回 pend）。
  **「不能只以 handler PC 出现过算通过」**（codex 原话）——每站点要执行证据。
- (20,2)=观测项非必经；id=2 节点存在性风险已销项（路 ID2：节点=编译期 DATA 段
  静态初值、B1[0] init pre-tail 挂链、零运行时依赖，(20,2) 消费链零停点零回调
  ——「缺失」分支不存在；是否真实到达消费链仍为运行观测，BZ 定性维持：非 D1-4
  FAIL 面）。
- 注入变体 `d1_post_inject='s18_18'` 默认关；开=窗口内首个 Timer0 唤醒交付完成、
  核回 idle 循环运行态时直调 0x6000cba(18,0)（key_chain_direct_call 先例：哨兵
  0x100000+保存/恢复 pc/sp/rets/flags/R0-R15+不调 advance），**显式合成假设全文
  标注**；idle 循环是否查询就绪队列未钉死——直调后调度器不切回=记录 BLOCKED
  （调度机制缺件）而非 FAIL；不进过线必要条件。

### 1.6 步骤 6：过线落档与收尾（PR-3）

1. 验收记录 D1-LOOP-01/TMR-02/TCK-03/DSP-04/HLT-05（+可选 DSP-04b 注入变体）
   **从 PR3P 预填底稿出发**（`docs/acceptance-samples/d1/` 五条九栏 NOT_RUN 已
   预填，断言锚点全引蓝图/任务书/PR1 契约原文——PR-3 只刷 verdict+证据+实测值，
   不再从零起草）；`acceptance_record.py --validate` 绿（conditional 必附条件、
   PASS 条件须出现在 model_assumptions 栏——含 §1.4 的 CNT 假设）。
2. HANDOFF 条目：蓝图 §9 草案为底，补实测值（P_park/S_tick/R1-R7 已答项）。
3. 过线口径：五条全 PASS（D1-4 无样本=NOT_RUN=未完成，不冒充）+条件性通过标注。
   **验收跑=首次过线必须全量重放（Q1，红线 5）**；此后复跑/回归用 PR-D0 快照
   验证轮（`--resume-from`，秒级）+assert_preflight 断言面预检，Q2 记录单列不覆盖
   Q1。L2-a（三镜像差分进 D1 窗口）为过线后独立一步，不在批 3——差分线钉值机检
   （R4SNAP r4 两档）+`--jobs 3` 并行（PAR3 3.03x）+快照续跑三件新工具届时全部
   可用（L2-a 判据按 §1.2 第 8 条事件计数等价口径）。
4. park 点 canonical 快照生成归 **PR-D0**（P-15 消费者③升格，见 §1.7 表）——
   `--capture-snapshots` 捕获轮（一代码态一次，~18min 级）；等价三层
   （byte-identical）由工具链自带，**仍不得用作过线证据**（过线证据只能是 Q1
   全量重放；快照=过线后的回归/复核加速件）。

### 1.7 PR 切分总表（参谋结论③采纳后的批 3 全貌；cc10784 刷新状态列）

| PR | 内容 | 闸门 | 完成标准 | 状态（cc10784） |
|---|---|---|---|---|
| **PR-1**（=§1.0b） | check_d1_acceptance.py 侧 d1_5_health（H1-H7）+FAIL 证据契约+单测 | **无**（不碰 probe/模型文件） | 每条规则独立 PASS/FAIL 判定正确；缺字段/截断/边界样本非零退出；总闸红一例；`--units` 绿 | ✅ **已完成**（`fafaffd`；PR2H8 `1cc166d` 增补 H8+8 反例，单测 78） |
| **PR-BI**（=§1.1） | 真实启动 BI 首验（A1-A5 先观察后升级硬断言；B3 已备大半数据+S19MAP 消费者序列） | 闸门②（FLS+B2R+推进跑到 park；数据整理部分可先行） | A1-A5 实测记录+升级依据落 HANDOFF；观察失败不阻塞 PR-2 合并但阻塞 D1 验收 | 等前沿（首验跑 ~18min/轮，O7E） |
| **PR-2**（=§1.2+§1.3+§1.3b 联动+§1.4 标注机制+§1.5 主样本接线） | 三参数+registry+场景行+观察件+报告键+窗口机制+**H8 生产者自检（§1.2 第 7a 条）**+**正式验收入口**+D1-1..4 断言适配 `d1_window` 键+CNT 假设标注机制+D1-4 主样本的观察面 | 闸门② | 生产者与 checker 契约对上（d1-health-checks §2 冻结契约）；旧场景逐字节不变；parity 钉子零变更（或有解释的差异，断言面先 assert_preflight 离线预检）；CNT 假设字段贯穿报告键+断言输出 | **checker 半边已落地**（PR2H8=H8 自检；H2SRC=H2 周期源 keyscan/adc_scan：§2 周期源表+`kind` 字段+checker `PERIODIC_SOURCES` 四源+缺源语义+8 反例单测，RBKREV P1 #5 缺口消账）；生产者侧整体等闸门② |
| **PR-3**（=§1.6） | 完整主样本验收跑满窗口+五断言 verdict+验收记录落档（PR3P 底稿刷 verdict）+HANDOFF | PR-BI/PR-2 全绿（PR-1 已绿） | 五断言全 PASS（或诚实 FAIL 归因）；Q1 全量重放为唯一过线证据；`acceptance_record --validate` 绿；CNT 假设确认贯穿落档 | 等前沿；工作量较原估降（底稿已预填） |
| **PR-D0**（新，过线后，原 §1.6 第 4 条「可选快照」升格） | park/D1-entry canonical 快照捕获（`--capture-snapshots`，一代码态一次）+验证轮登记+（可选）三镜像捕获 `--jobs 3` | PR-3 过线后 | 捕获轮报告+等价性证据（工具链三层自带）+`evidence/snapshot-artifact.json` 登记；快照只作回归/复核（Q2），不作过线证据 | 等过线；工具全部就位（SNAPX/PAR3） |
| **PR-4**（可选，=§1.5 注入变体） | `d1_post_inject='s18_18'` 变体实现+BLOCKED/PASS 裁决记录 | PR-3 后 | 默认关；显式合成假设全文标注；不进过线必要条件 | 等前沿；注入若不改指纹文件可复用 PR-D0 快照起跑（注入点在 park 后=快照边界后），改动即重捕获（快照双闸机械保证）——实施时如实验证，不预承诺 |

切分原则（参谋结论③）：场景接线（生产者）与断言适配（消费者）**同一 PR**（同一份
接口契约，拆开易版本错配）；BI 首验独立（观察性质，失败不阻塞骨架）；注入变体最后
（主样本=固件自然产生的 (18,0)，**不依赖**注入路径——参谋对「主样本需注入触发」的
担忧在本蓝图不成立，主样本在 park 前首批迭代自然发生，蓝图 §2.4）。PR-D0 独立成路
的理由=过线后才有 park 点、且快照捕获与 verdict 落档互不阻塞（REVIEW 排程「D1 过线
后=D1-entry 快照捕获（分钟级）」维持）。

## 2. 红线

1. **前沿路五文件批 3 实施期禁改**（当前占用者=FLS〔flash 顶窗口〕与 B2R〔批 2
   重落地〕两在途路，闸门②到达前持续占用）：`tools/analyze_image_trace.py`、
   `tools/br28_model.py`、`tools/probe_boot.py`、`tools/probe_irq_entry.py`、
   `tools/frontier_registry.py`。批 3 的 probe/registry 改动（§1.2）在闸门②
   （FLS+B2R 完成+推进跑到 park）后、且与当期前沿路协调占用后开工；
   `check_d1_acceptance.py`/`test_d1_acceptance.py`/
   `acceptance_record.py`/`assert_preflight.py`/新测试文件不受限（§1.0b 先行项
   已完成，后续 checker 契约微调同不受限）。
2. **蓝图原文不删改**：d1-scene-blueprint.md 只增补（§11 已按此办）；本任务书与
   蓝图冲突处以蓝图为准并回报主协调。
3. **不发明硬件状态**：P_park、CNT 快照值、门控值 b[0x125B54]/b[0x127184]、
   (20,2) id=2 节点、idle 循环调度面全部按实读/观察项处理；引用先例必须给路号/
   文档锚点（本任务书锚点=蓝图 §0 速查表）。
4. **默认关**：全部新开关 opt-in；legacy 场景报告逐字节不变（stash 对照法，
   B1 先例）。
5. **真实启动全程（Q1）是首次过线唯一合法形态**（§2.1 细则）。
6. 不合并 main；完成后汇报由主协调复核。commit 不加 coauthor 行。
7. 每步 `check_local.py --units` 绿；不跑 GitHub CI。

### 2.1 「真实启动全程=首次过线唯一合法形态」的执行含义（红线 5 细则）

依据：d1-acceptance §7「不能靠手工构造最终 OS 状态」、§8 起始状态栏、蓝图 §1.1
Q1/Q2/Q3 取舍表。执行含义四条：

1. **场景行单次运行从 boot 起跑**：p11_heap 条件栈自然推进到 park 再进窗口；
   budget 一次给足（全程 ≈3.7-3.9×10⁷ 模型步 S19MAP 口径〔旧 1.2×10⁷ 为 BM
   旧预算〕+ 窗口 N + 余量），不设「从中间热启动」参数。
2. **调试迭代不改进入形态**：窗口内 FAIL 归因→修复→重跑仍是全程 Q1；park 快照
   （若已生成）只能用于「回归/复核」（Q2），且 Q2 结果**不得写成首次过线证据**
   ——验收记录 start_state 栏只能是「真实启动全程（Q1）+park 双轴步数」。
3. **观察件不是注入件**：§1.2 的锚点计数器/per-core 时间线/模型步计数器全是只读
   观察件，零注入；唯一合成件=D1-4 注入变体（默认关+显式合成假设+不进必要条件）。
4. **过线后**才允许 Q2 加速复跑（快照等价绿为前提，蓝图 §1.1「Q2 为 Q1 的加速
   复跑形态」），且复跑记录单列、不覆盖 Q1 记录。**Q2 的工具形态=PR-D0 捕获的
   park 快照+`--resume-from` 验证轮**（SNAPX；`--self-check`/
   `--candidate-self-check` 拒 resume——断言钉的是全前缀逐步事实，全前缀验证
   只能全量重放）；SNAPX 的快照双闸（代码指纹+适配器形状）与红线 5 同构：
   任何仿真源/场景开关改动=拒载=强制重捕获（一次全量重放），恰是「行为变化类
   改动必须全量重放」的机械执行点。

## 3. 自验清单（每步退出条件）

| 步骤 | 自验 |
|---|---|
| 0b（H1-H8） | ✅ 已完成：`test_d1_acceptance.py` 78 例全绿（每规则 PASS/FAIL 形状+总闸+pending 语义+H8 三态 PRD 边界/余量破/拦截不放宽）；`--units` 绿（PR-1+PR2H8） |
| 1（BI 首验） | A1-A5 实测记录落档（先观察）；升级硬断言后单测同步；事件时钟首个消费者实测口径核（§1.1 第 0/4 条：W2[0] 6 tick） |
| 2（骨架） | 场景行跑通；`d1_window` 报告键蓝图 §4.3+d1-health-checks §2 冻结契约全出；legacy 逐字节不变（stash 对照）；registry 闭包校验过；probe check() 断言面先 `assert_preflight.py` 离线预检（秒级）再上全量 verify |
| 3（窗口公式） | P_park/S_tick 实测 vs 闭式差 ≤1 步（BP §6 口径）或如实记录偏差归因；P_park≠40000 时 D1-2 判据按实读周期核对通过；**H8 自检过（N//P_park≥3，不足延长 N）**；cfg523 开时 S9 键扫源入 per_source（§1.3b；checker 侧上界/缺源判定已落地——H2SRC，adc_scan 同入） |
| 4（D1-3） | 闭式=实溢出（±1 步）；jiffies +1；ACK=1；时间片返回；**CNT 假设字段在报告键+断言输出+验收记录三处齐全** |
| 5（D1-4） | 全链站点逐个执行证据（对照蓝图 §2.4 表）；(20,2) 观测如实（ID2 销项后仍观测「是否到达」，不测「是否存在」）；注入变体默认关验证 |
| 6（落档） | `acceptance_record --validate` 绿；HANDOFF 条目；五断言 verdict 表；Q1 全量重放证据链单列（Q2 快照轮不冒充） |
| D0（快照，过线后） | 捕获轮报告+等价证据+artifact 登记；验证轮 resume 报告带 snapshot_round/resumed 标记 |
| 反例自证（任一步） | 至少一个 FAIL 路径实测红（AZ/BU 假阳性反例惯例）：如窗口缩短缺周期→D1-2 红；H5 构造就绪未调度任务→红；CNT 假设字段删除→d1_3 pending（H8 反例 8 例已随 PR2H8 落地） |

## 4. 汇报格式（实施路向主协调）

每步进展带 HH:MM:SS 时间戳。完成时给：commit hash；五断言+H1-H8 verdict 表；
A1-A5 首验结果（观察轮+升级轮）；P_park/S_tick 实测 vs 闭式；legacy 对照结论；
已答运行观测项清单（蓝图 §7 R1-R7 哪些有答案）；**轮次标注**（Q1 全量重放/Q2
快照验证轮分列，Q2 不冒充过线证据）；遗留与归因；阻塞点（如有）。

## 5. codex 参谋结论（gpt-6-astra read-only，2026-11-14；采纳/不采纳逐条）

调用：`codex exec -m gpt-6-astra -s read-only`（沙箱无法读仓库文件，基于本任务书
摘要判断——其建议中凡依赖「未核实实现耦合」的分支，已在下表标注落地前核对项）。
原始输出存 `~/tmp/codex-batch3.log`（本机，不入库）。

**问题①（场景接线与 self-check/parity）**

| 建议 | 采纳 | 落地 |
|---|---|---|
| (a) 场景行放 `key_chain_scan_direct` 后（表末尾），无语义理由提前 | ✅ | §1.2 第 3 条（表末 15 行之后） |
| (a) 「开关默认关≠场景默认不运行」——显式选 D1 才纳入运行集合 | ✅（**核实后修正落地方式**：本仓先例是场景进表全跑+行为差异由开关控制，非「场景默认不跑」——codex 指出的成本问题真实存在，需新加场景过滤旗标或接受成本） | §1.2 第 3 条运行集合决策（倾向 CLI 过滤方案 (ii)） |
| (a) 条件栈用构造器+不可变覆盖，不手抄/不改公共字典 | ✅（即 `scenario_flags()` 惯例） | §1.2 第 3 条 |
| (a) 值开关激活判定勿用 truthy | ✅ | §1.2 第 1 条（`is not None`） |
| (b) 批 3 不为 D1 重钉旧 `p11_heap_experimental` 前沿；D1 单列结果行；成功终点=窗口完整结束（非 park） | ✅（与蓝图 §4.5「重钉归前沿推进路」一致并更明确） | §1.2 第 6 条；`p11_heap` 重钉归 BR 收尾路 |
| (b) D1 完成记录 `stop_kind='window_complete'`+起止+覆盖长度+两核终态（不压单 PC） | ✅ | §1.2 第 6 条 |
| (c) 同意 probe check() 最小形状 / check_d1_acceptance 独占实质机检 | ✅ | §1.2 第 7 条 |
| (c) **正式 D1 运行必须「生成报告→独立 checker→传播退出码」，单测进 units 不够**；证据不足≠PASS≠零事件 | ✅（本任务书原案缺口，实质补强） | §1.2 第 7 条：D1 场景行跑完调 `run_all` 传播 FAIL |
| (d) 新场景不应迫使 parity 重钉；给隐性耦合清单（共享栈/requires 展开/关闭态 event_clock/观察器动计步或交付顺序/全局计数器泄漏/序列化顺序/按表位置选场景） | ✅（作为 PR-2 自验清单） | §1.2 第 8 条 |
| (d) 「逐字节不变」对照对象=旧场景各自记录，非整份汇总 JSON | ✅（与 stash 对照法一致，措辞采纳） | §1.2 第 8 条 |

**问题②（H1-H7 定位粒度）**

| 建议 | 采纳 | 落地 |
|---|---|---|
| FAIL 共用结构（rule/expected/observed/first_violation{模型步,core,PC}/相关对象/状态转换片段/证据完整性） | ✅ | §1.0b 第 8 条 |
| 在线累计+只存首违/最严重片段（不巨量日志） | ✅ | §1.0b 第 8 条 |
| H4 未闭合唤醒区间补最后 PC/在途 IRQ/嵌套层级 | ✅ | §1.0b 第 8 条 |
| H2 风暴 per source+core 三分计数（到期/交付/完成）+首次超界状态快照 | ✅ | §1.0b 第 8 条 |
| H5：固定优先级无公平性，「所有 ready 必获调度」过强→分受检级（断言面任务 FAIL 级/其余观察级）+等待时长字段 | ✅（对蓝图 §3 H5 的实现修正） | §1.0b 第 7 条 |
| H6：`cursor_monotonic` 不检物理指针（环形正常回绕）→逻辑序号单调+取模一致式 | ✅（对蓝图 §3 H6 的勘误，**实施必守**） | §1.0b 第 6 条 |
| 窗口右边界：末尾刚唤醒/ready 标「尚未到期」，不默认 PASS | ✅ | §1.0b 第 8 条 |
| 计数上界考虑相位（记首次期限/起点 pending） | ✅（蓝图公式 +1 即相位容差，补记录字段） | §1.0b 第 3 条 |
| 模型步与事件时钟跳跃分别记录、换算依据入报告 | ✅（部分：不新加第三轴——蓝图双轴已把快进段计入模型步轴；换算依据=INV 常量链记入报告） | §1.3 |
| 砍：逐指令 timeline 不存/`scheduled` 由调度次数推导/`entry` 进元数据表+任务 id（TCB 复用再加代次）/`wake_reasons` 存计数+关联 id | ✅ | §1.0b 第 9 条 |
| 无限重发补事件关联 ID/父事件/生产者 PC | 部分（方向采纳，粒度从简） | D1 世界窗口内事件源封闭（S18 预置+Timer0 回调+时间片唤醒，蓝图 §2.4），首个窗口的事件基数个位数——**先记 producer PC+事件 id**，关联 ID/父事件机制若观察轮显示重发歧义再加（不为一次性代码做抽象） |

**问题③（PR 切分）**

| 建议 | 采纳 | 落地 |
|---|---|---|
| 拆出 PR-BI（观察失败不阻塞骨架合并、但阻塞 D1 验收；已有证据的 A1-A5 不统一降级） | ✅ | §1.7 总表 |
| 场景接线与报告消费适配同 PR（防生产者/消费者错配） | ✅ | PR-2 范围 |
| CNT 假设标注从 PR-3 前移 PR-2（运行结果语义；进入循环不改变证据等级） | ✅（与 §1.4「不能因进入循环解除」同构） | §1.4 标题时点 |
| 注入变体单独 PR-4；「若主样本需注入触发则最小注入路径须随 PR-2/3」 | ✅结论/**前提不成立**：本蓝图主样本=固件自然 (18,0)（park 前首批迭代，蓝图 §2.4），不依赖注入路径——故注入变体整体后置 PR-4，无前置义务 | §1.7 切分原则 |
| 合成样本单测不计入 D1 首次过线证据 | ✅（与红线 3「不发明」同向） | §1.0b 第 2 条 |
| 分钟级成本不值得快照首次验收；代码未变执行/观测语义不重跑主样本 | ✅（与红线 5/§2.1 一致） | §2.1 第 4 条 |
| PR-1 先冻结最小字段含义与计数口径 | ✅ | §1.0b（H1-H7 键契约=蓝图 §4.3+§1.0b 修正项即冻结口径） |

**不采纳/修正项汇总**：仅两处——②的「事件关联 ID/父事件」全套机制（过度设计，
见上表「部分」行）与③的「主样本需注入触发」前提（与蓝图事实不符）。其余全部采纳。
