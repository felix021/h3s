# 文档对账审计报告（路 AUDIT2 / sim-audit2，2026-11-16）

基线 main `1cc166d`（PR2 合并点）。任务承接 AUDIT（678eebf，
[audit-report-2026-11-15.md](audit-report-2026-11-15.md)）先例：一致性对账 + PC/地址/立即数
锚点抽样复算 + 时态/归因抽查。**本路改文档与 tools 纯文本（注释/生成器措辞），零行为改动、
零 acceptance 记录改动**（dashboard 再生成仅措辞行变化，`--check` 76 条一致维持）。

## 1. 对账范围

| 项 | 数字 |
| --- | --- |
| 审计窗口 | 678eebf..1cc166d 共 68 提交 26 路合并（重点=O6/WIND/LPV/PROF3/L2AR/REUSE/COEF/JLA/CHK/W3/R2CHK/PR2/R3；DT/ADA/ANCHOR 等上轮审计基线后的路一并纳入） |
| registry 实际 | **82 个开关**（base 19 / conditional 54 / probe_only 9）+ 58 条依赖边 + 条件栈 5 个值 fixture + 2 个已撤回名 |
| 前沿契约 | `p11_heap_experimental`＝21,800,455 @ 0x606e044 读 0xfe0904（size 4）；`crc_bankb`＝289,849 @ 0x60679ba 读 0xf27f4b——与 HANDOFF 主表逐字段一致 ✓ |
| acceptance | 面板 76 条＝实际 76 个 JSON（d2a 18/r234 15/d2b 15/cot 11/d3 8/parity 2/d1 5+上级散置 2），`--check` 通过 |
| 锚点复算 | 20 个（JLA 域 10 / LPV 2 / ADA 4 / DT 1 / ANCHOR 修正 3），官方 app.bin sha `18f3d573…` + 钉死 pi32v2-isa codec @`3608ed1`（与 data/dependencies.json pin 一致），call 目标全部脚本复算 |
| 实现抽查 | 5 处（H8/W3-1/W3-2/CHK/REUSE），全部与文档声明一致 |

## 2. 发现清单（P0 误导 / P1 过时 / P2 笔误措辞）

**P0：0 处**（未发现「把未验证说成已验证」或反向误导技术结论的段落）。

### P1（过时值仍在现在时语境 / 结构性重复）——7 处，全部已修（§4）

| # | 位置 | 前 | 后 |
| --- | --- | --- | --- |
| 1 | `docs/frontier-registry.md` 数据结构节 | 76 条/50/7/52 边（AUDIT 时点） | 82 条/54/9/58 边（AUDIT2 时点；AUDIT §2.2 同款滞后复发，DT/ADA/LPV/JLA 六开关并入后未同步） |
| 2 | `HANDOFF.md`「已实现」 | **LPV 条目逐字节重复两份**（行 96/107，md5 同）；WIND/O6 各两份仅差列表前缀（105/109、101/111）——PROF3/LPV/WIND 三次合并的冲突残留（blame：52c4965/4d65d12/0181573） | 删 3 条重复；另 R2CHK/LPV/COEF/L2CHK 四条裸 `*` 前缀非列表项（渲染为续文）规范为 `- **` |
| 3 | `docs/acceptance-samples/d1/README.md:17` | 「本目录未进 dashboard PANELS 六目录清单……收编归专门路」 | 已被 2f5df87 收编为第 7 目录，声明失实——改为「已收编（commit 2f5df87）」 |
| 4 | `docs/d2-audio-first-scene.md:180` | 「新前沿 21,798,257 @ 0x606b68e」（现在时，ADA 后又推进两路） | 「路 ADA 时点前沿……当前钉点以 frontier_registry 为准」（AUDIT 修 #4 同款） |
| 5 | `docs/usb-audio-modeling.md:187` | 「当前前沿 3,137,573 停在 initcall #4」（现在时，落后 26 路；上轮 AUDIT 漏网） | 「路 CT 时点（2026-11-14，基线 336e848）前沿……现已远远越过」（AUDIT 修 #7 同款） |
| 6 | `docs/id2-creator-prestudy.md:23` | 「当前前沿 21,798,206 …早已越过」 | 「路 ID2 时点（main dd6b89f）前沿……」（结论对更新前沿单调成立，保留原值+时点化） |
| 7 | `docs/id2-creator-prestudy.md:191-192` | 「当前前沿 21,798,206 的世界公共前缀覆盖…」 | 同上时点化 |

### P2（措辞/口径/工具文本滞后）——3 处全部处结（2 处已修 + 1 处 TAIL 裁决注记）

| # | 位置 | 说明 | 处置 |
| --- | --- | --- | --- |
| 8 | `tools/acceptance_dashboard.py`×4+`test_acceptance_dashboard.py`×2+生成页×2 | 「六目录」实为 **7 目录**（2f5df87 收编 d1 时只加了 PANELS 行，措辞未同步；「六目录 74」内部自相矛盾——74=7 目录之和） | 已修为「七目录」+再生成（--check 维持） |
| 9 | `tools/frontier_registry.py` 注释 | 「conditional…39 个开关」「probe_only…6 个开关」——实际 54/9（上轮 AUDIT 时点即已滞后 50/7，本轮 DT/ADA 探针并入再漂） | 已修 54/9（纯注释，validate() 不涉） |
| 10 | registry summary+probe 注释+HANDOFF ADA 条目 | 「7 个直接 li 基址站点(**9 实例**)」——7 站点独立复现精确吻合（见 §5-A16 附）；「9 实例」按同一静态 li 口径复现=**7 条指令**，疑为动态执行次数口径（如 0x606b5b0 站点执行两次），口径未注明 | **已裁决（TAIL 2026-11-16，据 ADA 路原始数据）**：9=ADA 路 scan3 脚本对**全 JL_ADDA 块**（0xfe0a00..0xfe0a4c 每字）直接 li 枚举的字级计数「words with direct li: 9」（~/tmp/ada-scan3.log，时点 10:19 先于提交）——有直接 li 的基址字恰 9：00/04/08/0c/20/24/30/40/48（含 ADA 子族窗外 CV/B3 域 5 字）；**既非站点实例、也非动态执行数**，原「9 站点实例」系计数单位误标；窗内 li 指令站点=7（本审计复现吻合）。HANDOFF ADA 条目已补口径注记；tools 内四处同文（frontier_registry summary/br28_model 报告文本/probe 注释×2）**保持原文**——它们是快照代码指纹文件（world_snapshot FINGERPRINT_FILES），纯文本改动即作废全部现存快照（重捕获=一次全量重放），措辞误读风险由本裁决+HANDOFF 注记覆盖 |

### 2.1 边界判定为「历史快照/自洽，不改」（记录备查）

- perf-budget-analysis §9-§12：全部停点数字（21,798,206/257、21,800,325 @ 0x606df1e）均带
  路基线锚定（O6 双基线/REUSE/W3 基线 0bffaa0），属运行记录快照 ✓。
- l2a-differential-criteria §3.3「停点 21,798,257/21,798,155 @ 0x606b68e，基线 main 364d5bd」
  ——L2AR/COEF 正式化时点自钉 ✓；R4-COT-01/R2-COT-01 记录、candidate-image-cotest R4C 节
  同为路锚定记录 ✓。
- candidate-image-cotest 的 l1/r4 app 切片 sha（`0536a9c7…`/`e4ee706f…`）：analysis/ 属
  gitignored 运行产物，本 worktree 不可复算；DIF 路标注「构建产物复跑实测」，留原样。
- d2a/d1 面板 README「共 20 条/共 7 条」＝其生成口径含上级散置 2 条，与自身表格自洽 ✓
  （d1 表格含上级 2 条 PASS，非错报）。
- AUDIT 报告（2026-11-15）自身「官方 app.bin（sha 65cafc7f…）」系 UFW/app.bin sha 混写
  （65cafc7f=UFW，app.bin=18f3d573）——已冻结的历史报告，只记录不改。
- id2-creator-prestudy 其余「当前前沿」为无数字限定语（「W1[3] 树内」），随前沿推进仍真，不动。

## 3. 一致性对账结论（未发现问题的面）

- **HANDOFF 主表 vs registry**：前沿行步数/停点/读地址/size 逐字段一致；步含义段链条算术
  全对（DT 19,139,239→ADA 21,798,206（+2,658,967）→LPV 21,798,257（+51）→JLA 21,800,325
  （+2,068）→21,800,455（+130））。
- **交叉引用**：l2a §3.3 双口径数字（Σ|δ_k|=58,354/δ_max=1,446/净停点差=102/E⁰=4/
  E_dmax=16/E=564/16 键=11 保守筛+5 主判据）在 l2a/COEF/R2CHK/HANDOFF/cot 记录间逐数一致；
  其中 Σ|δ_k|=58,354 由 §3.3 自身 δ 直方图（{22×11,308×31,1446×10,877/878×30,102×24,658×2,
  385×4,125×2,1342×1,883×1}）独立重加**精确复现**。
- **codec/镜像 pin**：pi32v2-isa @3608ed19 与 data/dependencies.json 一致；新文档 app.bin
  sha `18f3d573…`、UFW sha `65cafc7f…` 引用无混写（上轮 AUDIT 报告自身的混写除外，见 §2.1）。
- **开关名拼写**：82 开关+2 撤回名在新文档侧零拼错（LPV/JLA/ADA/DT 新开关名逐字核对）。
- **d2-audio-first-scene 新增两格**：DT/ADA 放行描述与 registry summary 一致（0xfe0a48/4c
  归 B3 五字容器的布局勘误口径在 probe 注释与本表相容）。

## 4. 直接修复清单（2 个提交）

- `5327080`（docs）：HANDOFF 三重复条目删除+四前缀规范；frontier-registry.md 计数；
  d2-audio-first-scene/usb-audio-modeling/id2-creator-prestudy（×2）时点化；d1 README
  dashboard 收编注记。
- `1a49bb0`（tools 纯文本）：dashboard 六→七目录（生成器+测试 docstring+再生成页面）；
  frontier_registry.py 两处栈计数注释。

## 5. PC/地址/立即数锚点复算（20/20 一致）

方法：官方 app.bin（sha `18f3d573…`，load base 0x06000100）+ 钉死 codec 逐条解码；call 目标
一律 `指令尾+位移` 脚本复算（ANCHOR 路规则，零手算）。复算脚本 `~/tmp/audit2/`（不进仓）。

**JLA 域（10）**——HANDOFF JLA 条目+registry `jl_audio_adc_dma_writes` summary：

- A1 `0x606dee4`＝set_buffer 入口（`[--sp]={rets,r8-r4}`）✓
- A2 `0x606df1e`＝停点并行对 `mul.rr.p`（`r2*=r1 #`）+store 半 ✓
- A3 五写同基本块 `0x606df20..30`：`[r4+4/8/12/16/20]`（r4=0xFE0538）＝ADR/LEN/PNS/HWP/SRP
  五字 0xfe053c/40/44/48/4c 逐一命中 ✓（值形状 PNS=r1>>1、HWP=SRP=r3=0 与「值全真实指令
  结果」相容）
- A4 `0x606e038`＝`[r5+0]=0x7F` 纯写，r5=0xFE0534（DAC_COP）✓；邻字 `0x606e03c [r5+36]
  &= 0xFFFFFFEF`＝0xfe0558 bit4 清位 RMW（AS/AK 容器描述吻合）✓
- A5 `0x606e044`＝`[r0+0] |= 0x2`（rmw.or.modimm，读 0xfe0904=JL_ASS+4）✓
- A6 `0x606e048`＝`[r0+0] &= 0xFFFFFFFE`（&=~0x1）✓
- A7 `0x606e050` 起 `[r0+4]`（0xfe0908）insert 族，实测见 p:9/l:3 与 p:6/l:3（bits 9..11/6..8，
  与「bits0..2/3..5/6..8/9..11」声明的族形状一致）✓
- A8 `0x606e040 r0=r5+976`（0xFE0534+0x3D0=0xFE0904 算术+指令双重吻合，「间接构造地址无
  直接 li 站点」声明成立——全 app li 扫描 0xfe0904 零命中）✓
- A9 handler 区间恰 `[0x6072628,0x6072700)`（尾 `0x60726fe {pc,r10-r4}=[sp++]`）；窗口内
  `[r10+12]/[r10+4]/[r10+20]` 三读（PNS/ADR/SRP）+`0x60726f0 [r10+12]=r8` PNS 一写 ✓
  （「恰 9 条访问」完备性：另两个 0xfe0538 li 站点 0x606f3e4 仅触 +0、0x60719b8 的 li 被
  并行对立即覆写——直接 li 面恰=set_buffer 五写+handler 三读一写 ✓）
- A10 `0x602f444 call 274910 → 0x6072628`（IRQ68 分发器直调）✓

**LPV（2）+ADA（4）**：

- A11 `0x606b688`＝`r1 = 15905796`（=0xF2B404，全 app 该窗唯一 li）✓
- A12 `0x606b68e`＝`[r1+0] |= 0x10000000`（bit28 置位 RMW）✓
- A13 `0x606b4d0`＝ADA 配置函数入口（`[--sp]={rets,r15-r4}`）✓
- A14 `0x606b5ee`＝`[r6+0] &= 0xFFFFFFDF`（bit5 清位），r6 在 `0x606b5b0 li 16648736`
  （=0xFE0A20）装载 ✓；邻窗 `[r6+40]/[r6+44]`＝0xfe0a48/4c 由 B3 旧五字容器覆盖（与
  「新开关分立不扩 B3 WORDS」自洽，无漏放行）✓
- A15 `0x606c3a8`＝函数尾 `{pc,r15-r4}=[sp++]`（区间 0x606b4d0..0x606c3a8 精确闭区间）✓
- A16 全 app 逐 2 字节独立解码 li 扫描：0xfe0a44 **零直接 li**（零触达声明成立）；ADA 窗
  直接 li 恰 7 站点 {0x606b5b0,0x606c3b0,0x606c448,0x606de9c,0x606ee9a,0x606f438,0x606f4fc}
  ——「7 个直接 li 基址站点」精确复现（「9 实例」已裁决=全块字级计数，见 §2 P2#10）✓

**DT（1）**：A17 `0x606ab92` tbb 表 12 字节 {06,08,06,08,1a,1a,1a,1a,10,12,10,12}——等价类
{0,2}/{1,3}/{4..7}/{8,10}/{9,11} 与「0/2→TM0低 1/3→TM1低 4..7→rts 8/10→TM0高 9/11→TM1高」
逐一吻合 ✓

**ANCHOR 修正复核（3）**：A18 `0x0602547e call 267654(=0x41586) → 0x6066a0a` ✓；A19
`0x06025466→0x600c2aa`/`0x0602546e→0x6016f34`/`0x06025490→0x600bbd8` ✓；A20 anc_next 五
引用 `0x06018bf0/0x06018ce8/0x60235de/0x0602548c/0x606e576` 全部 call→`0x6018b60` ✓
（另 d2a-loop-taskbook「0x0609e250 首字节 07 01 01」逐字节复现 ✓）

**W3/PR2 无 PC 锚点**：W3 为性能路（数字为运行测量，见 §6 抽查）；PR2 的 H8 锚点为立即数
（PRD 三态/P 值），在单测面验证（§6）。

## 6. 「实现符合描述」抽查（5/5 一致）

1. **H8（PR2）**：`check_d1_acceptance.py` `_window_cycle_coverage`＝`N//P_park` 向下取整、
   `<3`→pending `insufficient_window_coverage`、P_park/窗口 N 键缺=pending 不猜（无 40,000
   兜底）；H1 拦截＝「到期<3 且折算可证<3」才转 pending、覆盖足额照旧 FAIL；gate＝H1-H7 全
   PASS 而覆盖<3 时 `d1_gate`=pending 非 pass；证据契约 rule='H8'/first_violation 锚 W_park
   且 pc=None/{coverage,p_park,n,required_n_min} 特化键——与 d1-health-checks §1/§3/§4 逐点
   吻合；单测恰 8 例（`D1_5H8WindowCoverageTests`）与文档「8 反例」吻合，PRD 三态
   ((59999,20000),(47999,16000),(11999,4000)) 与文档同值。
2. **W3-2 memo 上限**：`_SIGNATURE_MEMO_CAP = 1<<16`、达上限先全清再插入、值 `sys.intern`
   （§12.2「条目上限 1<<16 超限全清」逐字落地）。
3. **W3-1 批量化+钉子**：`TraceSpool._staging`（BytesIO）单次 `file.write`；盘上形状钉死测试
   `test_trace_spool_chunk_format_is_row_pickle_concatenation` 与截断矩阵测试在位；
   `test_image_trace_diff.py` 21 项＝「17→21」声明。
4. **CHK**：`assert_preflight.py` CLI 形态（多报告位置参数+--kind/--first-failure/--json）与
   frontier-registry.md「推进类改动的预检纪律」示例一致；`test_assert_preflight.py` 16 项。
5. **REUSE RunPool**：每 (镜像,场景,预算) 恰执行一次、后续消费者复用 (result,spool)、spool
   由 close() 统一释放——与 perf-budget §10 描述一致。

## 7. 时态/归因抽查

新段落纪律良好：PR2「checker+单测先行，实跑数据等 D1 场景跑时自然消费」、W3「行为零变化
=验收」、COEF「[设计代入] 消账/机器重算」、H8「pending 拒绝给结论」措辞与证据等级标注
（conditional/derived-not-measured）贯穿；未发现「derived 说成 measured」或现在时断言未验证
事实。发现的时态问题全部是旧值滞后类（§2 P1#4-7，已修）。

## 8. 流程建议（供主协调者裁决，非本路实施）

- registry 计数注释已两轮漂移（39→50→54 未跟、.md 76→82 未跟）：建议新开关 PR 的 checklist
  （frontier_registry.py 头部四步）加第 5 步「同步 frontier-registry.md 计数」，或把 .md
  计数改为「以 `len(FEATURES)` 为准」弱引用，消除人工同步点。
- HANDOFF 合并冲突残留（三路条目重复）说明「已实现」列表在多路并行合并时无守卫：可在
  check_local 加一条轻量 lint（同首部 40 字符的条目行不得出现两次），成本一行。

## 9. 自验

`python3 tools/check_local.py --units > /tmp/x.log 2>&1; echo EXIT=$?` → **EXIT=0**；
`acceptance_dashboard.py --check` → 面板与 76 条记录一致。未改任何 acceptance JSON。
