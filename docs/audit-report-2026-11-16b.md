# 文档对账审计报告（路 AUDIT3 / sim-audit3，2026-11-16）

基线 main `75bcd20`（H2SRC 合并点）。任务承接 AUDIT2（`1cc166d`，
[audit-report-2026-11-16.md](audit-report-2026-11-16.md)）先例：AUDIT2 之后 main 又合并
约 15 路（SNAPX/PAR3/ASS/FLS/B2G/L1FIX/D1S9/S15W/BPSYNC/BPFX/S8SCAN/D2BID/CMDFIX/
S8ERR/RUNBOOK/OFF8/OFF8TAIL/PARKPRE/RAND/RBKREV/H2SRC），本路做一次全面对账。
**本路零 tools/ 改动**（文档 2 处 + HANDOFF 1 处小修；probe 侧五文件与
world_snapshot/test_world_snapshot 全程只读——JLEQ/M3FIX 两路在途禁区）。

## 1. 对账范围

| 项 | 数字 |
| --- | --- |
| 审计窗口 | AUDIT2 基线 `1cc166d`..`75bcd20`，重点=RAND/PARKPRE/OFF8TAIL/OFF8/H2SRC/S15W/BZERR 系勘误与 rand-to-park-map/s19-endgame-map §8/d1-first-run-runbook 修订 |
| registry 实际 | **88 个开关**（base 19 / conditional 59 / probe_only 10）+ **63 条依赖边** + 条件栈 **7 个**值 fixture；`validate()` 通过。演进：B2G（`feb10ca`）时点 87=19-58-10/62 边 → RAND（`367c348`）加 `jl_rand_seed` → 88；H2SRC 未动 registry |
| 前沿契约 | `p11_heap_experimental`＝29,215,313 @ 0x6072b92 写 0xfe1500（size 4，值 0）；`crc_bankb`＝289,849 @ 0x60679ba 读 0xf27f4b——三处（HANDOFF/FRONTIERS/analyze 派生）逐字段一致 ✓ |
| 锚点复算 | **30 个**（RAND 6 / PARKPRE 8 / OFF8+OFF8TAIL 7 / H2SRC 3 / RUNBOOK 2 / S15W 4 / BZERR 2 + 地址算术脚本复算），官方 app.bin sha `18f3d573…`（脚本断言）+ 钉死 pi32v2-isa codec @`3608ed19`（git rev 验证），call 目标全部 end+位移脚本复算（零手算） |
| HANDOFF 条目查重 | 「已实现」250 条按首 40 字符查重：**1 组重复**（AO/sim-keyaction，已修） |
| 自验 | `python3 tools/check_local.py --units` → **EXIT=0**（含 git diff --check） |

## 2. 逐项结果

### 2.1 registry 开关计数三处同步（5 子项：3 对上 / 1 修复 / 1 登记）

- **代码实际**：`len(FEATURES)=88`，分层 19-59-10，依赖边 63，条件栈 value_switch 恰 7
  （heap_info_bytes/lrct_trim_result/fd31_adc_res_named_values/jl_adda_daa_con1_initial_fixture/
  p33_canary_first_read_words/ancif_official_resources/jl_rand_seed），`validate()` import 即过 ✓。
- **docs/frontier-registry.md**：88 条/19/59/10/63 边全对上（RAND 落地时已同步）✓；
  但「守卫」节「条件栈非布尔开关恰为**三**个具名 fixture」是 FLS 前旧计数——**已修为七个**
  （AUDIT3 勘误）；另 L29 `requires` 与 `value_switch` 两个子条目被挤进同一行（markdown
  渲染坏）——**已拆行**。
- **HANDOFF**：RAND 条目「registry conditional 59 开关」✓；AF 条目（57 条时点）/B2G 条目
  （87 条时点）为带时点的路叙事，不属漂移 ✓。
- **登记**：`tools/test_image_trace_diff.py:572` 注释「the six named data fixtures」实为七个
  ——该文件在禁区清单内（在途路正改），本路只读，移交后续路顺手修。

### 2.2 前沿钉点三处一致（5 子项全对上）

| 位置 | 值 | 一致 |
| --- | --- | --- |
| HANDOFF 标题（第 1 行） | 前沿 289,849 步 + 条件前沿 29,215,313 | ✓ |
| HANDOFF 前沿表 p11 行（第 18 行） | 29,215,313 @ 0x6072b92 unmapped_or_readonly_write 写 0xfe1500（JL_EQ->CON0，路 RAND） | ✓ |
| `frontier_registry.FRONTIERS['p11_heap_experimental']` | completed_steps=29215313 / stop_pc=0x6072b92 / stop={write,0xfe1500,size 4,value_bytes 00000000} | ✓ |
| analyze freshness | `frontier_registry.freshness()` 单源派生（analyze_image_trace.py:123/334），结构一致 | ✓ |
| crc_bankb 行 | 289,849 @ 0x60679ba 读 0xf27f4b（HANDOFF 表与 FRONTIERS 逐字段同） | ✓ |

### 2.3 PC/地址锚点复算（30/30 一致）

方法：codec 经 `tools/pi32v2_asm.py` 装载（git rev==`3608ed19bc…` 钉死断言）+ 官方 app.bin
（sha256 断言 `18f3d573…`，load base 0x06000100）。call 目标=指令尾+位移脚本复算——
**codec.decode 返回的 ln 是字节数**（call.l=4/call.xl=6），先经 AUDIT2 存档锚
`0x602f444→0x6072628`（JLA 分发器直调）校准复算器后全程使用。复算脚本 `~/tmp/audit3/`
（不进仓）。

**RAND（6）**——HANDOFF RAND 条目 + rand-to-park-map §1.1：

- R1-R4 四个 rand_bytes 调用点 `0x6011c64/0x6011c92/0x6048f8c/0x604bcd2` call→
  **0x601196a**（rand_bytes 入口）逐一命中 ✓（长度 6/6/16/24 的消费面声明与
  「调用点 4 个（非 3）」codex 勘误一致）。
- R5 `0x6011c48` call→**ROM 0x1fd7c**（memcmp）✓。
- R6 rand_bytes 体逐条：0x601196a `[--sp]={rets,r13-r4}` / 0x6011970 li r0=0xfd3b00 /
  0x6011976 `r10=[r0+0]`（停点）/ r8=16807、r7=0x7fffffff（Lehmer MINSTD）/
  0x601199e `r0=r10>>15` / 0x60119a2 `h[sp+2]=r0` ✓；函数界 [0x601196a,0x60119ca) ✓。

**PARKPRE（8）**——rand-to-park-map §1.2/§1.3/§7：

- P1 `0x6072b92 [r0+0]=r5`（停点指令形）✓；P2 `0x6072bae [r0+28]=r1`（FLT_ADR）✓；
  P4 `0x6072b78 r1=[r4+r6<<2]`（r4=0x1265c0、r6=815→0x12727c，脚本复算）✓；
  P5 `0x6072b90 r0=[r1+16]` ✓；P6 `0x607bf40 call.xl`→**0x6072b24**（EQ init）✓；
  P7 W2 表 [0x60C3250,0x60C327C) 11 项 u32 逐一命中（含 [8]=0x607bf2e @0x60C3270）✓；
  P8 `0x6072b68 if (r0!=0)`（CON0 写臂门 bnez）✓。
- P3 数据锚：app.bin `.data` **0x60c3748 u32=0x00fe1500**（全 app 唯一出现，「零 li 常量、
  li 枚举免疫」声明成立）✓。

**OFF8/OFF8TAIL（7）**——d2b-framework-task §3.1 两轮勘误注记：

- O1 tbb 表内联 9 字节 @0x60869ce=`05 11 9c 9c 2b 3d 72 24 77` ✓，cmd4/5/8/10/11/12 六个
  分支目标=表址+字节×2 脚本复算逐一命中（0x60869d8/0x60869f0/**0x6086a24**/0x6086ab2/
  0x6086a16/0x6086abc）✓。
- O1' `0x6086a24 r1=b[r7+10]`（r7=obj+8→b[obj+18]）——OFF8 off-by-8 勘正的代码实证 ✓。
- O2 `0x6086a40 call`→**0x6064d72**（cmd8 自 post，OFF8TAIL 勘正值）✓；O5 `0x6086aaa
  call`→0x6064d72（cmd9 自 post，「0x6086aaa 实为 cmd9」勘误互证）✓。
- O3 `0x6075a08` 入口 `[--sp]={rets,r6-r4}`（槽值登记函数）✓。
- O4 OFF8TAIL 常量勘正：`0x608698c r12=1205432`（=0x1264B8）/`0x608699e r14=1199232`
  （=0x124C80）✓；互证 `0x6086b98 r0=[r14+36]`→[0x124CA4] 与 0x124C80+36 脚本复算一致 ✓。
- O4' `0x6086b20 r8=r6+16`（walker obj+16 基址，OFF8 互证注记）✓。

**H2SRC（3）**——d1-health-checks 周期源表/s8/s9 文档：

- H1 `0x6001944` keyscan 回调入口 prologue ✓；H2 `0x60349d2` adc_scan 回调入口 prologue ✓。
- H3 火回调链：`0x6066e32 r10=[r5+12]`（回调参数装载）+ `0x6066e7c call r6`（间接调用
  本体，前有 0x6066e78 r6==0 判空）——「火回调点 0x6066e32/0x6066e7c」两锚形状吻合 ✓。

**RUNBOOK（2）**：RB1 `0x60123e6`（S19 循环头/freshness 钉）可解码 ✓；RB2 `0x6064cf6`
（空队列 pend 写面「起」）可解码 ✓；全文无滞后前沿字面值，占位符纪律（指令步轴，
§1.2/§2 分轴注记）正确 ✓。

**S15W（4）**：`0x606787a r6=1219024`（=0x1299d0）/`0x60679f6 r0=1`/`0x60679f8 b[r6+40]=r0`
（写者，+40→0x1299f8 脚本复算）✓；读门 `0x606749e r0=1219024`+`0x60674a4 b[r0+40]` ✓；
邮箱 flash 镜像 0x060c6456↔RAM 0x12cc7e（delta 0x5f997d8 脚本复算）、首停位
0x060c646c=`b[r0+0]=r1`（运行时 r0=0xF27F43）✓。

**BZERR（2）**：`0x6066c22 call`→**0x6066ba6**（caller 声明）✓；thunk 0x607…`0x6066c34
call`→**0x6066a7e**（add 体，「thunk 目标实为 0x6066a7e 非 0x6066ba6」勘误）✓。

**地址算术（脚本）**：0x1265c0+815×4=0x12727c、0x1265c0+0x4FC=0x126abc（S19MAP 勘误门）、
0x1299d0+40=0x1299f8 ✓。

### 2.4 文档内部一致性（8 子项：5 对上 / 1 修复 / 1 登记 / 1 修复）

- **d1-health-checks 周期源表 vs checker**：四源表（timer0/core_tick=irq、keyscan/adc_scan=
  sys_timer）与 `check_d1_acceptance.py` 的 `PERIODIC_SOURCES` 四源、`SYS_TIMER_SOURCES`
  注记组、`MANDATORY_SOURCES=('adc_scan',)`（缺条目 pending）、keyscan 条件性不 pending
  逐项一致 ✓；H2 行（§1）的「P 实读不预锁 4×10⁵/2×10⁵」与表内标称口径相容 ✓；
  回调锚 0x6001944/0x60349d2 与 §2 表/代码注释一致 ✓。
- **s19-batch3-taskbook**：PR 总表 PR-2 行已被 H2SRC 正确更新（checker 半边已落地+出处）✓；
  §1.3b dated 更新在位 ✓；**自验清单 0b 行「78 例全绿」未跟 H2SRC 的 78→86——已修**
  （实际 `grep -c "def test"`=86）。
- **s19-endgame-map §8 混轴注记 vs 正文**：注记引用的 28,764,184（§0/§7 运行读数）、
  13,429,113（§7）、park ≈3.7-3.9×10⁷（§0.3）全部在正文且口径（completed_steps 指令步轴
  vs virtual_model_steps_advanced）表述与 registry/HANDOFF 同轴 ✓。
- **HANDOFF 条目查重**：250 条首 40 字符查重发现 **AO/sim-keyaction 条目两份**（仅差
  「本次，」3 字符；`840ae41` 引入、早于 AUDIT2 基线——AUDIT2 的 md5 全等/列表前缀查重
  口径未覆盖）——**已删第二份**（与 AUDIT2 P1#2 同类处置）。
- **snapshot-design/snapshot-resume vs world_snapshot 现状**（登记不修，M3FIX 在途）：
  br28_model.py:3790 `JLAdcStartConfigRegisters`（属性 `jl_adc_start`，:5909）未出现在
  snapshot-design M3/M4 表与 world_snapshot M3_OBJECT_SLOTS/M4 槽位——即 HANDOFF RAND
  条目已记录的「预存在 M3 适配器缺口，条件场景快照捕获尚不可用」。M3FIX 正改此区，
  其落地时应同步设计文档表；本路不动。
- **frontier-dependency-table/lookahead**：均为带时点基线（3,137,573 时点）的静态预研
  叙事，无全局计数可漂 ✓。
- **d1-first-run-runbook（RBKREV 修订后）**：见 §2.3 RUNBOOK ✓。
- **registry 单测计数叙事**：RAND 条目「test_image_trace_diff 非布尔开关集 6→7」✓、
  H2SRC 条目「78→86」✓（与实际 seven/86 一致）。

## 3. 修复清单（3 文件 4 处，均带 AUDIT3 勘误注记或等价可溯）

| # | 位置 | 前 | 后 |
| --- | --- | --- | --- |
| 1 | docs/frontier-registry.md 守卫节 | 「条件栈非布尔开关恰为三个具名 fixture」 | 七个（AUDIT3 勘误注记；tools 侧同名注释登记移交） |
| 2 | docs/frontier-registry.md 数据结构节 | `requires` 行尾粘连 `value_switch` 子条目（渲染坏） | 拆为两行 |
| 3 | docs/s19-batch3-taskbook.md 自验清单 0b 行 | 「78 例全绿」 | 86 例（AUDIT3 勘误：H2SRC 8 例未跟） |
| 4 | HANDOFF.md | AO/sim-keyaction 条目重复两份（差「本次，」） | 删第二份（AUDIT2 P1#2 同类，其查重口径漏网） |

## 4. 移交清单（登记不修）

1. **tools/test_image_trace_diff.py:572 注释**「the six named data fixtures」→ 实际七个
   （断言集已是七元）。禁区文件（在途路正改），下路顺手修。
2. **snapshot-design.md M3/M4 表未覆盖 JLAdcStartConfigRegisters**（及 ASS 后新投影对象
   的归属分层）——M3FIX 在途正改 world_snapshot 此区，落地路应同步设计文档并消
   HANDOFF RAND 条目的「条件场景快照捕获尚不可用」注记。
3. （观察，非漂移）snapshot-resume.md「advance tier=前沿推进 checklist 第五步」而
   frontier_registry.py 头部注释仍是四步——SNAPX 记录的「届时追加」延迟项，下一条
   前沿路执行时落地。

## 5. 流程观察（供主协调裁决，非本路实施）

- registry 计数注释第三轮漂移（「三个」fixture 计数在 FLS/RAND 后未跟）：AUDIT2 §8 已
  建议「.md 计数改弱引用」，本处再证——docs/frontier-registry.md 的 fixture 个数叙述
  可改为「以 `extra_flags(probe_irq_entry)` 非布尔项为准」消除同步点。
- HANDOFF 合并冲突残留（AO 重复）说明 AUDIT2 建议的「同首部 40 字符 lint」仍未落地，
  且需把「仅差 本次，/列表前缀」的近似重复纳入口径。

## 6. 自验

- `python3 tools/check_local.py --units > /tmp/x.log 2>&1; echo EXIT=$?` → **EXIT=0**
  （skipped=2 为既有 env 门控跳过；含 git diff --check）。
- 环境注记：worktree 初次跑 units 因缺三样钉死缓存而红（tools/reference-cache 空目录、
  firmware/*.ufw 缺、jl-misctools 子模块未初始化）——照 BX/CE 先例软链主仓缓存 +
  本地 clone 子模块（pin `0a5b12db`）后绿；均为 gitignored/子模块路径，不进提交。
- 本路零 tools/ 改动、零 acceptance 记录改动、禁区文件零触碰。
