# D1 第一轮执行手册：park → PR-BI → PR-D0 → PR-3 → 失败回退（路 RUNBOOK）

2026-11-16 路 RUNBOOK（sim-runbook，纯文档路）。本手册把批 3 任务书
[docs/s19-batch3-taskbook.md](s19-batch3-taskbook.md)（PR-BI/PR-D0/PR-3/PR-2 段）串成
一份**带具体命令的执行手册**：前沿路照着跑 park 到达判定与 HANDOFF 更新，主协调照此
核对 PR-BI 预算、PR-D0 快照、PR-3 verdict 刷写与失败回退。所有依据随行标注
（任务书 § / 蓝图 § / 快照文档 / 健康检查契约 §），未钉死值一律标「运行观测项」，
不臆造。

**适用范围与前置**：本手册假设 RAND 路（JL_RAND 种子读放行）已合并、P2M+cfg523 已随
B2R 提层生效、前沿正从当前停点跑向 S19/park。开工闸门=任务书 §1.0「前沿/park 形态」
行（FLS 放行 + B2R 重落地 + 推进跑过 S16-S18 到 park）。

**红线 5 总纲（贯穿每一步）**：真实启动全程（Q1）是首次过线唯一合法形态
（任务书 §2.1）。四条执行含义：①场景行单次从 boot 起跑、budget 一次给足；②调试迭代
不改进入形态（窗口内 FAIL 归因→修复→重跑仍是全程 Q1）；③观察件只读、唯一合成件是
D1-4 注入变体（默认关）；④park 快照只能用于 Q2 复核，Q2 结果不得写成首次过线证据。
下文每一步都标出它在本步的具体约束。

---

## 1. park 到达判定

### 1.1 判据（什么算「到了 park」）

park = S19 消息循环空队列后两核进入 idle 等待、事件时钟首次接管。到达形态按
S19MAP 终局图 §2 表 #15 + 蓝图 §1.1/§2.1：

- CPU0 走到 S19 消息循环头（`0x60123e6`）→ get-event 成功码消费 → 分发表两表
  walk（表 2 六条 → 动态过滤链 → 表 1 五十四条）→ 空队列 pend 写面（`0x6064cf6` 起）
  → 经调度器切 idle 任务（`0x12c57e` → `0x12c5ca`）。
- CPU1 早已停泊在 `0x12c5ca`（boot 期就位）。
- 两核都进 idle 后事件时钟首次接管 = `event_clock_engaged` 首次为真 → 记 **W_park**
  （模型步 + 指令步双轴并记）。

**停点形态决策**（蓝图 §4.5，归前沿推进路定死，不在 D1 路）：二选一——
(a) S19 循环头首达的指令步（freshness 钉 `0x60123e6`）；(b) event_clock 开启后首个
全核 IDLE 前最后一个指令停点。要求：重钉值在 D1 场景 flag 集下可复现，
`freshness_fields` 保留 `(kind, address)`。

**诚实区分**（任务书 §1.0 空位表 + S19MAP §0）：音频 init 的 os_time_dly 途中停泊
已真实发生，但那不是 S19 终态 park。终态 park 判定只看上面这条链是否走完
（空队列 pend 写面执行 + 两核 idle + event_clock_engaged）。

### 1.2 前沿路怎么确认

1. 前沿推进路的放行跑完成后，看 `probe_irq_entry` 报告里
   `p11_heap_experimental_config` 场景的 stop 记录：
   - 停点 kind 不再是 unmapped_memory/unmapped_or_readonly_write 这类「新边界」；
   - 停点在 S19 循环/pend/两核 park 锚点上（或按 §1.1 停点形态决策 (a)/(b) 落定）。
2. 用 check_d1_acceptance 的 D1-1 断言做机器核验（离线，不跑仿真）：

   ```bash
   python3 tools/check_d1_acceptance.py --report analysis/local-checks/irq-entry.json \
       --scenario p11_heap_experimental_config
   ```

   D1-1（真实进入消息循环并阻塞）从 pending 转 pass，即机器可证的 park 到达；若
   仍 pending（键缺/窗口未开）说明还没到，不冒充。

### 1.3 HANDOFF 主表怎么更新

前沿推进路（RAND/B2R/放行跑）合并时更新三处，都在同一 PR 内：

1. **registry 单点重钉**（`tools/frontier_registry.py` 的 `FRONTIERS`）——这是
   freshness 钉子的单一数据源，只改这一处，probe require / analyze freshness /
   test 期望字面值全部自动跟随：

   ```python
   'p11_heap_experimental': {
       'completed_steps': <park 实测模型步>,
       'stop_pc': '0x60123e6',          # 或 §1.1 (b) 的最后一个指令停点
       'stop': {'kind': ..., 'access': ..., 'address': ..., 'size': ...,
                'reference_sdk_register': None},
       'freshness_fields': ('kind', 'address'),
   },
   ```

2. **HANDOFF「当前前沿」表**（`HANDOFF.md`）：更新 `p11_heap_experimental_config` 行
   的「步数 / 停止点 / kind」三列，停点描述写清「S19 空队列 pend 后两核 park」形态
   （不是单 PC，蓝图 §1.2 第 6 条完成记录同口径）。
3. **HANDOFF 条目段落**（表下加一段 `**N 步含义（本次推进，路 XX…）**`）：
   按既有格式记录停点解剖 + 计数 +「不是全核 IDLE 时间停 / 已是 park」的终态定性 +
   前 N 场景报告逐字节不变（stash 对照）结论。

D1 路（PR-BI/PR-2/PR-3）**不为 D1 重钉旧前沿**（任务书 §1.2 第 6 条：重钉归前沿推进路，
D1 场景单列结果行）。

---

## 2. PR-BI 预算：D1 场景 budget 一次给足

### 2.1 预算组成与取值

任务书 §1.1 第 1 条：budget = **全程 park + 窗口 N + 余量**，一次给足、不设「从中间
热启动」参数（红线 5 第 1 条）。

- **全程 park** ≈ 3.7–3.9×10⁷ 模型步（S19MAP §2 预算；从当前 28.76M 再 +9–10M，
  S16+S17 事件时钟 ~8M 占大头）。
- **窗口 N**（任务书 §1.3 默认公式，PR-2 窗口机制起算时现算）：
  `N = max(S_tick(W_park) + 160,000, 200,000)`。
  - `S_tick` = 从 park 态 core tick 的 CNT/PRD 推出的首溢出闭式期限（event-clock
    §2 条 3 的 S=ceil 公式），≤ 300,000（core tick PRD≈120 万、每模型步 4 tick →
    周期 300,000）。
  - 160,000 = 按 Timer0 周期形状值 40,000 预留 4 个周期（D1-2 要 3 个 + 1 余量）。
  - 所以 N ∈ [200,000, 460,000]。
- **H8 自检**（任务书 §1.2 第 7a 条 + d1-health-checks §1 H8）：N 落定后按实读
  P_park 折算 `N // P_park >= 3`；不足则延长 N 或报「观察窗不足」不判过。P_park
  形状值 40,000，但 BY 实测 delay 会按调用重编 PRD（周期三态 20,000/16,000/4,000，
  反例 P=70,000 已钉）——160,000 余量可能被打破，park 实读值出来前不预设结论。
- **余量**：540,000 左右（park 上界到 40M 的缝隙）。

**落地值**：`40,000,000`。计算：39,000,000（park 上界）+ 460,000（N 上界）+ 540,000
（余量）。当前代码里 `p11_heap_experimental_config` 的 budget 已经由 `jl_adc_start_config`
层推到 40M（`tools/probe_irq_entry.py` irq_case 的 budget 推导链），park 预算沿用同值；
若 park 实测更靠后（>39M）或 N 因 cfg523 S9 键扫观察加长，按实上调（如 41M），
「实测为准、不预设结论」同 P_park 红线。

### 2.2 具体命令

probe_irq_entry 没有 per-scenario 的 `--budget`/`--scenario` CLI——budget 是 irq_case
内的代码值（按 flag 推导，见 `tools/probe_irq_entry.py` irq_case 的 budget 链）。所以
PR-BI 的「给足预算」动作 = 在 D1 场景行（PR-2 落地的 `d1_acceptance_window`）写
`budget=40_000_000`（或 park 实测上界 + N + 余量）；PR-BI 首验阶段（场景骨架未接线前）
直接复用现有条件栈场景，确认其 budget ≥ 40M 即可。

跑的命令（跑全部场景，含条件栈场景）：

```bash
python3 tools/probe_irq_entry.py --rom <ROM> \
    --report analysis/local-checks/irq-entry.json
```

`<ROM>` 为私有 ROM 文件；firmware 默认取仓内
`firmware/QCY_H3S_7018F_1T2_release_note_V1.1.8_011.ufw`（`--firmware` 可换）。

跑完出 BI 首验结果（任务书 §1.1 第 2 条，A1-A5 先观察后升级）：

```bash
python3 tools/check_d1_acceptance.py --report analysis/local-checks/irq-entry.json \
    --scenario p11_heap_experimental_config --json
```

PR-2 接线后正式验收跑（任务书 §1.2 第 7 条）调 `run_all` 传播 `d1_gate`（非 pass 即
非零退出码）；PR-BI 阶段只观察 A1-A5，不触发 d1_gate（BI-A* 不进 D1 gate）。

### 2.3 红线 5 约束（本步）

- 首验跑是全程 Q1（boot 起跑到 park），单臂 ~18min（O7E 实测外推），一轮可承受、无
  快照依赖——**不得**用任何已有快照起跑（红线 5 第 1 条：不设中间热启动）。
- A1-A5 首轮只观察（任务书 §1.1 第 2 条）；观察一轮无异常后才升级硬断言，升级依据
  记 HANDOFF。
- 事件时钟首个消费者口径核验（任务书 §1.1 第 4 条）：W2[0] 3×os_time_dly(2)=6 tick，
  `virtual_model_steps_advanced` 应首次非零；仍为零则停下归因（park 判定或停泊形状
  与预期不符），不要继续。

---

## 3. PR-D0：park 停点 canonical 快照捕获

任务书 §1.6 第 4 条 / §1.7 PR-D0 行：park 点 canonical 快照**过线后**生成，只作回归/
复核（Q2）加速，不作过线证据。工具 = SNAPX（快照续跑，[docs/snapshot-resume.md](snapshot-resume.md)）。

### 3.1 捕获轮命令

```bash
python3 tools/analyze_image_trace.py --rom <ROM> \
    --scenario p11_heap_experimental \
    --image official=<官方 ufw> --image tested_b79=<b79 ufw> \
    --capture-snapshots analysis/snapshots \
    --report analysis/local-checks/capture-round.json
```

每镜像照常全量重放到自然停点（freshness 钉子照常把关），停点边界把该镜像世界
（CPU×2/内存/外设/机器/账本，M1–M7）捕获到 `analysis/snapshots/<scenario>-<label>.bin`。
三镜像（官方/b79/候选）各一份快照，互不可替换。`--jobs 3`（PAR3）可三臂并行，约
3.03x（条件三镜像 68m33s → 22m39s）。

### 3.2 验证轮命令

```bash
python3 tools/analyze_image_trace.py --rom <ROM> \
    --scenario p11_heap_experimental \
    --image official=<官方 ufw> --image tested_b79=<b79 ufw> \
    --resume-from analysis/snapshots \
    --report analysis/local-checks/verify-round.json
```

每臂 restore 起跑，主循环从停点指令重新 decode（预算停点不补调 advance）。resumed 轮
核验停点面：freshness 停点钉子、每对 `runs`/`parity`、`final_report_diffs`；轨迹面
字段（`trace_lengths`/`first_divergence`）只反映 restore 之后的步，看顶层
`snapshot_round` 与 pair 的 `resumed: true` 标记区分。

### 3.3 等价性验证（resume 三档 vs 全量）

方法（快照文档「等价性证据」段 + SNAP 先例）：capture 于停点 → restore+advance 三档
预算 vs 全量重放到同停点，报告**逐字节相等**；另加 0 步自反检查。测试入口
`tools/test_world_snapshot.py` ArtifactTierFrontierWorld（env 门控 `H3S_SNAPSHOT_ROM`，
`H3S_SNAPSHOT_CAPTURE_N`/`H3S_SNAPSHOT_WORK` 可调）。登记到
`evidence/snapshot-artifact.json` 的 canonical 行（含三档行）。

### 3.4 快照双闸与红线 5（本步）

- 快照双闸 = 代码指纹 + 适配器形状：任何仿真源/场景开关改动 → restore 拒载 → 强制
  重新捕获（=一次全量重放）。这与红线 5「行为变化类改动必须全量重放」同构
  （任务书 §2.1 第 4 条）。
- `--resume-from` 与 `--self-check`/`--candidate-self-check` 互斥（那些断言钉的是全
  前缀逐步事实，只能全量重放）。
- 快照文件含 RAM 镜像，红线不进 git（放 `analysis/`，gitignored）；仓库只进摘要行。
- **本步红线 5 约束**：PR-D0 在 PR-3 过线之后才做；快照轮结果只作 Q2 复跑，验收记录
  start_state 栏只写「真实启动全程（Q1）+ park 双轴步数」，绝不写快照 resume。

---

## 4. PR-3：五条验收记录 verdict 刷写

任务书 §1.6：五条记录从 PR3P 预填底稿出发
（`docs/acceptance-samples/d1/`：D1-LOOP-01/TMR-02/TCK-03/DSP-04/HLT-05），PR-3 只刷
verdict + 证据 + 实测值，不再从零起草。过线口径：五条全 PASS（D1-4 无样本=NOT_RUN=
未完成，不冒充）+ 条件性通过标注。

### 4.1 每条断言锚点怎么取实测值（三件套）

1. **assert_preflight 离线预检**（改断言/核对锚点前先跑，秒级，LPV 4×35min 教训
   固化的工具；任务书 §1.2 第 8 条）：

   ```bash
   python3 tools/assert_preflight.py analysis/local-checks/irq-entry.json
   ```

   三类报告（probe_boot / probe_irq_entry / analyze_image_trace）断言面零仿真离线预检；
   绿了再上全量 verify。`check_local --preflight` 会对标准报告集跑一遍（--allow-missing）。
2. **check_d1_acceptance 机检**（D1-1..D1-5 + H1-H8 实质判定，checker 独占）：

   ```bash
   python3 tools/check_d1_acceptance.py --report analysis/local-checks/irq-entry.json \
       --scenario p11_heap_experimental_config --json
   ```

   输出里 `D1-1..D1-5` 各自 status（pass/pending/fail）+ `d1_gate`。每条验收记录的
   verdict 直接取自对应断言的 status：LOOP-01 ← D1-1、TMR-02 ← D1-2、TCK-03 ← D1-3、
   DSP-04 ← D1-4、HLT-05 ← D1-5（H1-H8 机检 + 否定式总闸）。
3. **Q1 全量重放是唯一过线证据**：上面机检消费的报告必须是全程 Q1 跑出来的
   （§2.2 的 probe_irq_entry 全量运行），不是快照 resume 轮。

### 4.2 证据契约字段怎么填

每条记录九栏 + 证据字段（`tools/acceptance_record.py` 的 `COLUMNS` 九栏）：
固件身份 / 起始状态 / 输入与时间表 / 观察边界 / 断言 / 超时预算 / 模型假设 / 结果 /
未覆盖项，另列证据等级。

- **result**：NOT_RUN → PASS（或诚实 FAIL）。条件性通过 = `evidence_level: "conditional"`
  且必须附 `evidence_conditions`（显式条件列表）。
- **PASS+conditional 交叉校验**（acceptance_record 内置）：`evidence_conditions` 逐条
  必须出现在 `model_assumptions` 栏，否则 `--validate` 红。
- **CNT 假设贯穿三处**（任务书 §1.4 硬性要求，TCK-03 必核）：报告键
  `d1_window.core_tick_state.assumption`（顶层优先，park 下兼容）+ check_d1_acceptance
  的 d1_3 判定输出 + TCK-03 的 `model_assumptions` 栏。原文照抄
  "under the explicit 'CNT frozen after overflow' assumption (conditional,
  assumed-not-measured) — NOT PRD-1"。缺失 = 断言形状不完整（pending 非 pass）。
- **PRD 读回 ≠ CNT 读回**（§1.4 第 4 条）：PRD 读回（`core_tick_prd_readback`）不承载
  冻结假设，CNT 读回（`core_tick_cnt_held_snapshot`）承载，两者不得混写。
- **起始状态栏**（红线 5）：只能写「真实启动全程（Q1）+ park 双轴步数」。

刷写与校验命令（蓝图 §6 + acceptance_record CLI）：

```bash
python3 tools/acceptance_record.py --add docs/acceptance-samples/d1/D1-LOOP-01.json
# ... 逐条五条
python3 tools/acceptance_record.py --validate docs/acceptance-samples/d1
```

`--add` 校验通过才落盘；落档后重生成 README 汇总表（会整页覆写，头部需手工补回，
见 acceptance-samples/d1/README.md）。

### 4.3 红线 5 约束（本步）

- 验收跑 = 首次过线必须全量重放（Q1）；此后复跑/回归用 PR-D0 快照验证轮 +
  assert_preflight 预检，Q2 记录单列、不覆盖 Q1（任务书 §1.6 第 3 条）。
- DSP-04 无样本时 NOT_RUN（「未覆盖，D1 完整验收未完成」），**不得**当成 PASS
  （§1.6 第 3 条）。注入变体 D1-DSP-04b 归 PR-4 单独，不在本批。

---

## 5. 失败回退：窗口内 FAIL 的归因流程

### 5.1 归因流程（遇到 FAIL 先别急着改）

1. **看 FAIL 证据契约**（d1-health-checks §3 七规则共用结构）：每条 FAIL 的 check 附
   `evidence` 字段，形状：

   ```
   {rule, expected, observed,
    first_violation:{model_step, core, pc},   # 聚合计数键无步号时 None+note
    source_or_task, around:[前条, 违规, 后条],  # 只存首违；轨迹类规则才有
    worst:{...}, <rule 特化键>,
    window_end:{complete, model_step_start, model_step_end, evidence_complete}}
   ```

   先定位 `first_violation`（模型步/core/PC）与 `observed`，判断是**观测未到**
   （pending，键缺/右边界未闭合）还是**真违约**（fail，有数据但违反条款）。pending ≠
   fail ≠ pass，三态不要混写。
2. **pending 面先排除「窗口伪证」**：H8 折算覆盖 <3 → insufficient_window_coverage →
   D1-5=pending、d1_gate=pending（总闸不抵消）——这是窗口配置问题，修 N 重跑（仍是
   全程 Q1），不是 DUT 失败（d1-health-checks §1 H8 + §5）。
3. **真 fail 面按七规则归因**：
   - H1/H2 三分计数（到期/交付/完成）+ 上界；H2 core_tick 的 CNT 假设标注缺 = pending
     非 pass（先补标注再谈）。
   - H3 重发/重复消费 + queue_balance。
   - H4 未闭合唤醒区间（last_pc/in_flight_irq/nesting_level）。
   - H5 分两级：断言面任务饿死 = FAIL；非断言面未调度 = 观察级不 FAIL。
   - H6 逻辑序号回退 / 物理指针≠序号 mod 容量 / 超限。
   - H7 状态转换流断裂（步号倒流/非法转移/CPU0 有唤醒而时间线空）。
4. **cfg523 联动**（任务书 §1.3b）：若窗口内出现 S9 键扫 10-tick sys_timer 源，H2
   per_source 必须含它；缺源 = H2 pending 非 pass。

### 5.2 红线 5「调试迭代不改进入形态」（本步核心）

- 窗口内 FAIL → 归因 → 修复 → **重跑仍是全程 Q1**（boot 起跑，budget 一次给足）。
  修的是模型/断言 bug，不是缩短窗口、不是换成快照热启动、不是注入事件把断言填绿。
- 修复后重跑若行为变化（动了仿真源/场景开关），park 快照因双闸自动拒载、强制重新
  捕获——这本身就是「行为变化必须全量重放」的机械执行点（任务书 §2.1 第 4 条）。
- 归因记录里诚实标注 FAIL vs BLOCKED（模型缺件）vs 窗口配置不足三者的区别，不要
  把模型缺件写成 DUT 违约、也不要为过线放宽判据（红线 2/3：蓝图原文不删改、不发明
  硬件状态）。

---

## 6. 自验与收尾

- 每步退出条件对照任务书 §3 自验清单（本手册各节已逐条映射）。
- 全程自验：`python3 tools/check_local.py --units` 绿（本手册为纯文档，trivially 绿；
  验收命令同任务书要求）。
- 汇报带 HH:MM:SS 时间戳 + commit hash + 五断言 verdict 表 + Q1/Q2 轮次分列
  （任务书 §4 汇报格式）；commit 不加 Co-Authored-By，不 push，完成后由主协调复核。

**遗留**：本手册只覆盖 D1 第一轮执行路径；L2-a（三镜像差分进 D1 窗口）为过线后独立
一步，判据按事件计数等价（任务书 §1.2 第 8 条），不进本手册；PR-4 注入变体另立。
