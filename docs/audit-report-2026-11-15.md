# 文档对账审计报告（路 AUDIT / sim-audit，2026-11-15）

基线 main `5bceedb`（路 L2AC 合并点之后）。任务：把「代码在动、文档易滞后」的漂移一次性对账清掉——
开关名（frontier_registry Feature id）、前沿停点（步数@PC）、抽查 PC/地址三类硬标识。
**本路只改文档，零 tools/ 改动、零 acceptance 记录改动**（故 acceptance_dashboard 无需再生成）。

## 1. 对账范围

| 项 | 数字 |
| --- | --- |
| registry 开关全量 | **76 个**（base 19 / conditional 50 / probe_only 7）+ 2 个已撤回名（`p2m_mailbox_ack`/`cfg523_named_fixture`） |
| 扫描文档 | `docs/*.md` 84 个 + `HANDOFF.md` + `docs/acceptance-samples/**/*.json` 70 个 |
| 前沿契约单点 | `crc_bankb`＝289,849 @ 0x60679ba（读 0xf27f4b）；`p11_heap_experimental`＝**19,139,239 @ 0x606abbe**（读 0xfe0524） |
| PC/地址抽查 | 5 个文档 20 个样本（candidate-image-cotest 3 / key-action-map 5 / audio-input-paths 6 / l2a-differential-criteria 1 / s19-batch3-taskbook 5），官方 app.bin（sha `65cafc7f…`）+ 钉死 pi32v2-isa codec 逐条解码 |

**范围约束**：`docs/d2a-loop-taskbook.md`（D2APREP 在写）、`docs/d1-health-checks.md` 与
`docs/s19-batch3-taskbook.md`（刚冻结）只读不改；`docs/acceptance-samples/d2a/` 按派单方
补充约束只读（本轮未发现 d2a 记录内有开关名/前沿引用错误）。历史叙述（路条目、设计书自钉
基线快照、「当时推进到 X」）一律不动。

## 2. 发现清单（按类）

### 2.1 前沿数字滞后（声称当前、值已过期）——7 处，全部已修

| # | 位置 | 前 | 后 |
| --- | --- | --- | --- |
| 1 | `HANDOFF.md:1` 标题 | 条件前沿 3,202,148 | 19,139,239（CW 时代值滞后两路） |
| 2 | `docs/candidate-image-cotest.md:42` | p11_heap 档「条件前沿 3,747,585」 | 19,139,239+「以 frontier_registry 为准」（同文档 §9 校准节 166 行已是新值——内部不一致） |
| 3 | `docs/d1-scene-blueprint.md:512` P-1 行 | 「现为 3,747,585」 | 「现为 19,139,239」（同文档 496 行校准节已是新值——内部不一致） |
| 4 | `docs/d2b-injection-blueprint.md:25` | 「当前条件前沿 3,137,573」 | 去现在时化：「路 AW 基线 336e848 时点快照，当前钉点以 frontier_registry 为准」（数值保留为设计时基线） |
| 5 | `docs/d1-gap-map.md:33` §2.1 节标题 | 「当前前沿机器状态（303,853…）」 | 「路 BC 调研时点 2026-11-13：303,853…；当前钉点见 frontier_registry」 |
| 6 | `docs/event-clock.md:51` | 「当前前沿（289,849/300,187）尚未到达任何全核 IDLE 点」（已失实：条件线早已越过全核 IDLE） | 改为时点化叙述+兑现记录（CW 进条件栈、B3 实测事件时钟推进 13,429,113 虚拟步、C2 退出条件已满足） |
| 7 | `docs/falsification-checks.md:23` | 「支撑当前前沿 3,137,573」 | 「支撑（路 AZ 挑样时点 2026-11-14）前沿 3,137,573」 |

### 2.2 registry 机制文档自身计数滞后——1 处（4 个数字），已修

`docs/frontier-registry.md`「数据结构」节：写 62 条/conditional 37/probe_only 6/37 依赖边/
三个数据 fixture，实际 **76 条/50/7/52 边/五个值 fixture**（新增 `jl_adda_daa_con1_initial_fixture`/
`p33_canary_first_read_words`）。已按实际值改写并标注对账时点。

### 2.3 撤回开关引用语境缺失——2 处，已补状态注记

`p2m_mailbox_ack`/`cfg523_named_fixture` 全库 13 处引用逐查：d1-scene-blueprint P-7/P-9 行、
s19-batch3（冻结）、HANDOFF 均自带撤回语境 ✓；但两份设计书无任何状态标记，读者会按
「拿到即可开工」误操作：

| # | 位置 | 修复 |
| --- | --- | --- |
| 8 | `docs/cfg523-fixture-design.md` 头部 | 补状态注记：开关已 B3 实现+单测后撤回（零消费者先例），不在 registry，实现保留 `0b875ed`，「拿到即可开工」前提未成立 |
| 9 | `docs/s19-final-specs.md` 头部 | 补状态注记：批 2 电池段两开关同上撤回；批 0b/1/3 不受影响 |

### 2.4 开关名缩写/不精确引用——1 处，已修

| # | 位置 | 前 | 后 |
| --- | --- | --- | --- |
| 10 | `docs/d1-scene-blueprint.md:512` | 「`config_readback` 族」 | 精确开关名 `jl_adda_daa_con1_config_readback`（「两开关」行内两个名字都应是 registry 精确名） |

**拼写错：0 处**。76 个开关名+2 个撤回名在文档侧未发现任何拼错/已改名残留
（`erased_bankb`、`fd31_adc_*`、`core_tick_*` 等高频引用全部逐字正确）。
21 个开关零文档引用（`banks`/`entry`/`rti`/`dcu_cmo` 等）——非错误，不处理。

### 2.5 「另一层真名」不算错（核对后放行，防误伤）

文档里大量 `*_projection`/`*_writes`/`*_records` 形态标识符经与代码核对是
br28_model/br28_machine 的**真实机器层参数名**或 probe 真实场景/机制名，非开关名漂移：
`dcu_cmo_projection`、`event_clock_projection`、`idle_wait_projection`、`p33_canary_projection`、
`p33_analog_tail_projection`、`jl_clock_dcck_projection`、`timer0_irq_entry`、`timer0_irq_delivery_requests`、
`zero_count_rep_skip`（probe_boot 诊断键）、`key_chain_scan_direct`（probe 场景名）、
`key_chain_direct_call`、`exact_doubleword_stores`、`immediate_rep_multi_block` 等。
判别标准：以「开关/opt-in/flag」身份引用且指向 frontier_registry 开关时必须精确等于 Feature id；
描述机器层参数/机制/停因 kind 时用代码真名不算错。

### 2.6 PC/地址抽查结果（20 样本：18 一致 / 1 分歧涉 2 行 / 1 待定性）

**一致（18）**，含多个强验证：

- `0x606ab92` getter：`bgt.ri if (r1>11)` + `tbb [r1]`——与「tbb selector 0..11」逐字吻合；
  且 getter 内 `li.u32 r2=0xFE0524`，停点 `0x606abbe` 处 `ld.w r1=[r2+0]`＝读 0xfe0524 ✓（cotest）
- `0x06018ce8 call→0x06018b60`、`0x060254aa call→0x060251fc`（key-action-map 校准锚点，call
  尾相对位移精确复算一致）；case 11 入口 `0x0602544e` 处 `li.s16 r1=444; ld.bu.idx [r7+r1]`＝
  「QCY+444 门」✓；case 1 `0x06025396` 第三条 call 目标复算＝`0x06018bf4`（发布 music_pp）✓
- `0x606de92` 增益 setter 入口即 `ifs.ge.ri.s12 ifs (r2>=19)`——「cap 19」逐字吻合（audio-input-paths）；
  `0x606b4d0`/`0x606c3aa`/`0x606f8b6`/`0x6032924`/`0x6014298` 入口/形状全部相符
  （0x6014298 处低 nibble 清位+打包序列与 desc 打包声称吻合）
- `0x60661a4`：`push.srl {psr,rets,reti}`+`push.rl {r3-r0}`+`mov r0=cnum`＝IRQ handler 入口
  （l2a C0 机器定义、s19-batch3 同址引用跨文档一致）✓
- `0x60124ae` K2 金丝雀：`call.xl` 后 `goto.s goto -2` 自旋——「无 fixture 世界 K2 必自旋」吻合 ✓
- s19-batch3 其余（0x6023962 handler 载荷比较、0x6064c8c get-event、0x6064cf6 pend 写面
  `or 0x30`、0x6064340 让出链读 0x1265C0+52）形状全部相符 ✓

**分歧（1，未修，归「无法判定」）**：key-action-map.md:16 校准锚点「`0x0602548c call→0x06018b60`」
与 audio-input-paths.md:587「BT 命令: 0x60235de / 0x602548c 调 anc_next」。钉死 codec 对官方
镜像解码：**两处 call 实际目标均为 0x06018b40**（0x0602548c：`call -51504`，0x06025490-0xC950
＝0x06018B40；0x60235de 同）——同一行内另两个锚点（0x06018ce8/0x060254aa）精确复算一致，
排除了方法误差；从 case 11 入口 0x0602544e 正向走指令流确认 0x0602548c 恰为指令边界，排除了
未对齐误解码。`0x06018b40` 不是跳板（叶子代码，写 [r5+172..173]），其身份（可能是 anc_next
事件发布/状态设置助手；anc_next 字符串在 0x0609CF4F 字符串池中、无邻近指针表）本轮未定。
**不改**的原因：需路线级重分析确定 0x06018b40 身份后才能安全改锚点（盲改两个方向都可能引入
新错）；两文档均非冻结，留给接手路。附带：key-action-map:128「QCY+444 门→0x0600bbd8/0x06018b60」
中 0x06025458 的 call 复算目标为 0x0600bb98（差 0x40），属同一未决链，一并记录。

### 2.7 边界判定为「历史快照、不改」（记录备查）

- 路线书/设计书头部自钉基线值：frontier-lookahead（3,073,402@0x6001342，基线 64a7e6b）、
  frontier-dependency-table（3,137,573，「AS 在途期间以该报告为基线」自限）、
  s19-final-specs（3,170,362，基线 762cfb2）、d1-scene-blueprint:4（3,170,362，引用 main adb996e）、
  eq-parity-rerun/eq-divergence-profile（3,747,585@0x12b0e8，路 A1/EQDIV 运行记录自钉停点）、
  adc-res-consumer/appcore-tail-sfr-semantics（路叙事「路 BS 后/新前沿」）——均有基线 commit
  或路号锚定，属时点快照。
- d3-preparation:38「parity 钉到当前前沿」＝机制描述（自动重钉），无数值，非滞后。
- l2a:182「条件前沿 19,139,239>C0」为当前值 ✓。

## 3. 修复清单（10 处，全部为本报告 §2.1–2.4）

HANDOFF.md 标题；docs/candidate-image-cotest.md；docs/d1-scene-blueprint.md（2 处同行）；
docs/d2b-injection-blueprint.md；docs/d1-gap-map.md；docs/event-clock.md；docs/falsification-checks.md；
docs/cfg523-fixture-design.md（状态注记）；docs/s19-final-specs.md（状态注记）；docs/frontier-registry.md（计数）。

## 4. 未修+原因

| 项 | 原因 |
| --- | --- |
| key-action-map:16 锚点 0x0602548c 目标、audio-input-paths:587 anc_next 声称、key-action-map:128 0x0600bbd8 链 | §2.6：0x06018b40 身份未定，需重分析后安全修（无法判定） |
| d1-health-checks.md / s19-batch3-taskbook.md / d2a-loop-taskbook.md / acceptance-samples/d2a 内疑点 | 冻结/他路在写（本轮 s19-batch3 抽查 5 样本未发现错误，d2a 无发现） |
| 21 个零引用开关 | 非错误（registry↔probe 一致性由 test_image_trace_diff 钉死，文档不欠账） |

## 5. 自验

`python3 tools/check_local.py --units` 退出码 0（纯文档改动，units 全绿）。未改 acceptance
记录，acceptance_dashboard 无需再生成/`--check`。
