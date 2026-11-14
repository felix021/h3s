# S19 冲刺剩余三预案实施规格：PVDD 容器 / VLVD canary 接线 / P2M 邮箱 ack + 五预案总装（路 BM / sim-s19spec，只读设计）

2026-11-14。基线 worktree `762cfb2`（路 BK 合并点；条件前沿 **3,170,362 步 @ 0x606b426
读 0xfe0a04 DAA_CON1**，P-1 在途）。本页是 S19 冲刺五预案中**剩余三案**的实施规格书：
①PVDD 容器（BA §5.2 P1/P2）、②VLVD canary 接线（P5）、③S15 P2M 邮箱 ack（P6），
加上 ④五预案总装（含 BH 已设计的 P7 与 BK 已设计的 P4 的实施顺序/开关层叠/停点串联/
打包建议/重钉纪律）。**本路是设计文档，不是实施**：未改 tools/ 任何代码、未跑任何改动
场景（本文件为 docs/ 新增，git 状态可查）。

**字母说明**：任务文指定「路 BM」与已合并的 sim-romtool 路 BM（read_rom
`--p11-heap-probe`，HANDOFF「当前并行任务」节）重名——照 CT 先例，合并时由主会话
顺延字母，本文以 BM 自称（BG/BE 先例同款处理）。

方法与证据强度：反汇编用仓库钉死的 pi32v2-isa codec（`probe_pi32v2.upstream()` +
`probe_boot.application()`，official UFW sha `65cafc7f…`/app sha `18f3d573…`，load
base 0x06000100；RAM-code 从 flash 源窗 0x060c3e18↔RAM 0x12a640 重解码，call 目标
按指令尾+偏移验算）；SDK 头在 reference-cache 701n-boot（commit `67328f4`，与
data/dependencies.json 钉死值同源：p33_sfr.h/p11_sfr.h/p11_app.h）；一次性脚本在
`~/tmp/bm-s19spec/`（不进仓）。**[静态·本路]** = 本路指令锚定反汇编（新增钉死）；
**[文档]** = 已合并 docs/HANDOFF 条目（出处随行）；**[SDK]** = 参考头；**[模型]** =
br28_model/probe_irq_entry 源码阅读；**[推断]** = 上述链推出。全部静态结论
**derived-not-measured**；一切 fixture 值 **synthetic-not-measured**。

## 0. 一句话结论

**三案全部可以按既有先例落成「一容器+一接线+一 fixture」，零新机制**：①PVDD 容器
= AM 容器同款（P3_ANA_CON13/CON1/P3_PVDD0_AUTO 三地址合成初始字 0+读回=当前值，
新开关 `jl_pvdd_trim_writes`）——但 **BA 记的「trim 环采样 0x10005 fd31✓」不成立**：
0x10005 不在 FD31_ADC_RES_NAMED_VALUES 里，必须随容器同 PR 扩一行 RES，且常量 fixture
值使 trim 环恒走满 15 轮后落缺省段（环内早退需要非常量值，不造）；②canary 接线 =
BV 已备而未接线的 `p33_canary_projection` 落进 conditional（0xf2c044 首读具名字 0），
**当前 fixture 世界 canary 必触非条件**（0x10006=500 → 0x12c29a 门 v4=2000≤3699 →
量测分支必走）；③P2M 邮箱 ack = p11_lpwr 同款请求-响应投影（观测到 kick
`[0xF2A618]=0x400` 后 P2M_INT_PND bit12 读回置 1+P2M+3 回声字节=M2P+7 值，即时、
不建延迟），**首停点是 0x12CC94 的 b[0xF27F43]=0xFF 字节写而非 BA 记的 0x12cc9e
邮箱写**（P2M 区未映射，字节写在前；且 BA/AG 的字节地址 0xF27F03/0xF27F47 系 +0x40
转写滑移，实为 **P2M+3=0xF27F43/M2P+7=0xF27F87**，ack 轮询地址实为 **0xF2A60C**
（AG 对 BA 错），0xF2A618 的 SDK 真名是 **M2P_INT_SET** 而非 P2M_CLK）。总装建议
**分三批**（W4[4] 簇批 → 电池段批 → D1 场景批），一次放行预期前沿从 3.17M 推到
S19/park ≈ **1.2×10⁷ 模型步**（其中 S16+S17 事件时钟 ~8×10⁶ 占大头）。

## 1. PVDD 容器规格（预案①，BA §5.2 P1/P2 行的实施规格书）

### 1.1 逐地址表（写值/形状/读侧风险；全部 [静态·本路] 逐指令钉死，BA §3.1 互证）

三个新地址 + 已建模邻域。P33_ACCESS(x)=0xf2c000+4x（p33_sfr.h L8 活动定义，本路验算
0x95×4+0xf2c000=0xf2c254 ✓）。

| 地址 | SDK 名（p33_sfr.h） | 本路径全部访问点 [静态·本路] | 形状 | 读侧风险 |
|---|---|---|---|---|
| **0xf2c274** | **P3_ANA_CON13**（P33_ACCESS(0x9d)，L143） | ①0x60686fc `[r8+48]&=0xFF0`（复合 and.nmodimm）；②0x6068700..0c 显式 ld/st RMW（读→&0x1FEF→\|0x10→写回）；③环体 0x6068732..3c（读→&0xFF0→\|r6→写回）每轮；④0x6068764 `&=0x1FEF`（复合） | 复合 RMW×2 + 显式 ld/st RMW×2（读值真实参与写回） | ld/st 读需容器读回；无整字写、无独立读点 |
| **0xf2c244** | **P3_ANA_CON1**（P33_ACCESS(0x91)，L131；BV 已钉、AY 抄名笔误由 BA §8 修正维持） | ①0x6068710..1c 显式 ld/st RMW（读→&0xFF7→\|8→写回）；②0x606876c `&=0xFF7`（复合）；③0x606865c `[r0+0]&=r1`（r1=0xFF7F，**and.r 寄存器操作数**形状，0x60685fe 尾） | ld/st RMW×1 + 复合×2 | ld/st 读需容器读回；其余读者（0x6067a38..6c 被 icfg 位图跳过的块、K 树 cleanup）不在健康路径 [文档：AY/BA] |
| **0xf2c10c** | **P3_PVDD0_AUTO**（P33_ACCESS(0x43)，L78） | 0x60685fe 内三连：①0x6068628..36（读→**&0xFF1F**（BK 勘误值，codec 直读 65311）→\|0x60→写回）；②0x6068638..42（读→&0x1FEF→\|0x10→写回）；③0x6068644..54（读→>>4→insert(umin(trim,arg1), p:4, l:8)→写回） | 全显式 ld/st RMW | 读值参与移位/插入，必须真读回；无整字写 |
| 0xf2c110 | P3_PVDD1_AUTO（0x44，L79） | 0x6068610..20 并行对（B 半读旧值）+`(trim<<4)\|trim` 塞低字节 | **VOLTAGE_CONFIG 已建模**（§0.1 实测有状态）[文档：BA §0.1] | 无新面 |
| 0xf2c254 | P3_ANA_CON5（0x95，L135） | 0x60687d4（&7 读）、0x60685d0（±1 比较读）、0x12c29a 入口读、扫描 RMW 族（§2）、0x6068808..20（&0x3FD7→&0x7F8→\|b[+5]→写回） | **P33_ANALOG_CONFIG 已建模** [文档：AM 路] | 无新面 |
| 0xf27f92 | M2P 消息字节（M2P+0x12） | 0x60687ea 写 b[+2]+0x60687ec 读回 | AA 域消息 RAM [文档：BA W6] | 无新面 |

**读回语义证据等级**：p33_sfr.h 只有地址宏、无 `__RW/__RO/__WO` 标注（与 p11_sfr.h
不同，AM 路已钉）——容器读回=当前值不是 SDK 类型证据，是 VOLTAGE_CONFIG
（P3_ANA_CON6）/P33_ANALOG_CONFIG（CON2/CON5）/p33_analog_tail（CON0/7/8/9/10）一脉
的**既有先例语义**（合成初始字 0+读回=后写值，不臆造硅模拟状态）。本容器与它们的
唯一差别=地址列表，零新机制。

### 1.2 容器语义 + 开关 + 依赖边

- **常量**：`JL_PVDD_TRIM_CONFIG=(0xf2c274,0xf2c244,0xf2c10c)`（不并入
  P33_ANALOG_CONFIG/P33_ANA_CON_TAIL 既有常量——那会改「旧容器开而本开关关」世界
  的报告形状，BV/BN 两次先例的同一纪律）。
- **语义**：开关开启时三地址 seed 进 additional_configuration（合成初始字 0）+
  registers（读回=当前值），机器侧零新分发（BV tail 容器的落地形态，
  「AM 容器同款……路 BN ANA_CON4 先例」）；复合 RMW（and.nmodimm/and.r）走既有
  通用 mmio 位记录路径；显式 ld/st 走 registers 读回。三个 and.nmodimm/and.r/ld-st
  观测形状各进一条单测。
- **开关名**：`jl_pvdd_trim_writes`（**不取任务文候选名 jl_pvdd_vlvd_trim_writes**，
  理由：0xf2c044（VLVD_CON）不在本容器——它走 canary 开关、读回语义是首读具名
  fixture 而非容器读回，混名会误导两套读回语义的差别）。
- **依赖边**：`requires=('p11_power_init',)`（P33 域家族一致；无 fd31 依赖——RES 行
  是值常量切换不是新开关，见 §1.3）。

```python
# probe_irq_entry.irq_case 新参数（默认关）:
jl_pvdd_trim_writes=False,   # P3_ANA_CON13/CON1/P3_PVDD0_AUTO 容器(合成初始字0+
                              # 读回=当前值,VOLTAGE_CONFIG 同族);W4[4] trim 域

# frontier_registry.FEATURES 追加:
Feature('jl_pvdd_trim_writes', 'conditional', ('p11_power_init',), False, None,
        'W4[4] PVDD/trim 域容器:P3_ANA_CON13(0xf2c274)/P3_ANA_CON1(0xf2c244)/'
        'P3_PVDD0_AUTO(0xf2c10c) 合成初始字 0+读回=当前值(VOLTAGE_CONFIG/'
        'P33_ANALOG_CONFIG 同族,p33_sfr.h 纯地址宏无类型标注不臆造模拟态);'
        '路 BM 设计'),
```

### 1.3 trim 环放行预期（含 BA「fd31✓」勘误与 RES 行扩展）

**勘误（本路新钉）**：BA §2.4/§5.1 #6 行记 trim 环采样「0x601174c(0x10005) fs 采样，
fd31✓」——**不成立**。fd31 的 RES 具名档按通道字索引（0x10000\|P3_ANA_CON4[4:1]
复用域，BY 钉），现行 `FD31_ADC_RES_NAMED_VALUES={0x10000:400,0x10006:500,0x10004:400}`
（probe_irq_entry.py:538）**不含 0x10005**——trim 环每轮采样会撞
`fd31_adc_res_channel_not_modeled` 诚实停。必须随本容器同 PR 扩一行。

- **0x601174c 的真实重量** [静态·本路]：它是 12×2 次**真采样事务**（每轮 call
  0x60116d0 两次：r0=0x10000 参考通道 + 目标通道，含 CON 写+轮询 1000 轮+RES 读，
  BY 域），不是缓存表读（缓存读者是 0x6011dd2，S15 才用）。
- **RES 行值与环行为的耦合**：环出口=`ifs(v2>800)` 有符号早退（0x6068752），v2=0x10005
  采样值。**常量 fixture 下环恒走端点**：值≤800 → 走满 15 轮（r6=r7=15）；值>800 →
  首轮即退（r7=1）。两种端点都出 b[+1]∉[3,10]（15>10、1<3）→ **缺省段 0x60687c8**
  （b=8/11，b[+4/5]=CON5&7）→ 应用段。「中途健康退出」（r7∈[3,10]）需要逐轮递增值
  表=对硅 PVDD 阶梯响应过拟合，**不造**（三选一里的 (c) 否决项，同 BA P4 行对 PRD
  完成时刻的否决逻辑）。推荐值 **0x10005=400**（与 WBG04/VTEMP 同带，走满 15 轮，
  环行为=有界确定）。
- **落值方式**：新常量 `FD31_ADC_RES_S19_VALUES={**FD31_ADC_RES_NAMED_VALUES,
  0x10005:400, 0x10001:4}`（0x10001=BH 的键通道释放态行——BH 原设计是 cfg523 开启时
  场景内合并，统一进本常量更简单且无害：该行只在 cfg523 世界被读，缺 cfg523 时
  是惰性字典项，还顺带覆盖 BH §1.4 的 R2「adc_scan 轮选键通道」防御面），p11_heap
  场景行改传新常量+registry `fd31_adc_res_named_values` 的 `value_attr` 同 PR 改指
  新常量名（flag 字典进报告与前沿重钉同 PR，BH §4.4 纪律）；BY 自己的场景继续用
  旧常量 → legacy 逐字节不变。
- **环步数预算**：15 轮 ×（2×0x12e29c(2000) 自旋 + 24 次采样事务 ×~10³ 步）≈
  **4-6×10⁵ 模型步**（BA §2.4「全环 ≈10⁵」低估约 3×——采样事务单价 BY 实测
  ~10³ 步/次 [文档：BN/BY]）。W4[4] 全簇（含头部 24 事务+sampler2×24+0x12c29a
  24+两助手 10+TIMER2 2×4000）≈ **5×10⁵ 步量级**。
- **环后的收敛块不构成重试风暴** [静态·本路]：0x6068846..74 的 b[+6] 计数重试块只在
  充电态查询（0x6067dda(14/8/21/22)）非零时进入（0x6068844 goto −102→应用段的
  反侧），本世界实测全 0 [文档：BA §0.1] → 零执行；即便进入也有 b[+6]≤255 上界。

## 2. VLVD canary 接线规格（预案③，BA §5.2 P5 行的实施规格书）

### 2.1 触达面：量测分支是健康路径必经，且当前 fixture 世界必走

0x12c29a（flash 母本 0x060c5a72，0x60685fe 之后的第三个调用点 0x60687a4）[静态·本路]：

```
入口: r10=0xF2C254; r5=[r10](CON5 读,已建模✓); r6=r5&0xF
      0x10006 采样(0x601174c 12×2 事务,fd31✓) → r0<<2 = v4
G-v:  if (v4 > 3699) 快路过：b[+5]=b[+4]=0x80|(CON5&0xF) → 出口（不触 0xf2c044）
量测分支(v4≤3699):
      r9=r10−528 = 0xF2C044; r8=[r9]           ← ★P3_VLVD_CON 首读（0x060c5aae，
                                                    RAM 0x12c2d6，BA 锚互证）
      [r9]&=0xFFFFFFFB（清 bit2）; 0x12e29c(100) 自旋; [r9]&=0xFFFFFFFE（清 bit0）
      两助手建 8 档表：0x12c20e(sp+32)/0x12c178(sp+0)——每助手开头
      [0xF2C044]=0 整字写 + CON5 扫描族 RMW（&=0x3FD7|0x38、&=0x7F8、
      insert(p:3,l:8) 档位域）+ 每档 0x12e29c(215) 自旋 + 0x10005 单发采样
      argmin×2 → b[+5]/b[+4]
      if (r8 & 1) [r9]|=0x1（bit0 使能回写，消费首读值）
      0x12e29c(3); [r9]|=0x4（bit2 回写）; CON5 恢复（&0x7F8|r5&7）→ 返回
```

- **首触达判定**：0xf2c044 的全部 app 读者（cleanup 0x60682c4/K 树 0x12cc40/
  0x12ceb8 族/DVFS 0x60c6750 族）都不在健康 boot 路径 [文档：BV/BA]——**本读就是
  健康世界的首读**，且先于该地址一切写（canary 首读 fixture 的适用条件严格满足）。
- **当前世界必走（非条件）**：v4=0x10006 具名值<<2；现行 fixture 0x10006=500 →
  v4=2000≤3699 → **量测分支必走、canary 首读必触**。BA §3.1 W8 行的「条件停点」
  按现行值收敛为必经；快路（v4>3699）只在 0x10006≥925 的未来世界出现（§4.3 表
  引用此不变量）。

### 2.2 canary 机制的应用：「触发即停」报警的定义

既有 canary 件盘点：(a) **P33CanaryWatchRegisters**（路 BV，模型已备未接线）——
写侧三观测形状记录（整字 st.w/or.modimm/and.r/and.modimm）+读侧**首读具名 fixture、
未覆盖/写后/重复读一律严格停**（kind：canary_first_read_fixture_absent /
canary_readback_after_write_not_modeled / canary_repeat_read_not_modeled）；(b) K1/K2
金丝雀 PC（0x601249c/0x60124ae，check_d1_acceptance 的 canary_k*_zero_exec）；
(c) 诚实停负例钉子先例（crc_engine_roundtrip 的 bank B 负例）——canary 的本质=
**正常不可达/只此一形，越形即停并归因，不兜底**。

本接线后「触发即停」报警清单（全部是模型既有严格停或场景 check，不新增机制）：

| # | 报警 | 形态 | 归因 |
|---|---|---|---|
| A1 | 0xf2c044 首读无 fixture | Blocked `canary_first_read_fixture_absent` | 世界缺件（本接线后=开关未开的负例对照） |
| A2 | 0xf2c044 写后再读 / 重复读 | Blocked `canary_readback_after_write_not_modeled` / `canary_repeat_read_not_modeled` | 越形访问=世界假设错或方向错（如真跑进 K 树/DVFS 域） |
| A3 | 0xf2c124（P3_PCNT_CON）任何访问 | 同上一族（无 fixture 条目 → A1 形状） | 健康路径零触达 [文档：BV]；触达=进了失败臂 |
| A4 | 写形状越白名单（如 and.nmodimm） | Blocked `canary_rmw_form_not_modeled`（probe_boot 分发） | 未观测形状，不发明 |
| A5 | K1/K2 PC 执行 | D1 场景 check FAIL（BE 蓝图 §2.1 canaries） | 世界缺件暴露（BA §4.6：K2=config 523 缺失） |

**接线到哪个场景检查**：p11_heap 条件场景行 post_stop 断言——
`p33_canary.report().first_read_fixture['0xf2c044'].reads_served==1`、零 A1-A4 类
Blocked、`0xf2c124` 零访问；D1 窗口（BJ）保留 A5。负例=开关关的世界停在
0x12c2d6 unmapped 读（文档化形状）。

### 2.3 首读值 + 开关 + registry

- **首读值 `0xf2c044: 0`**（synthetic-not-measured）：消费位只有 bit0（0x060c5b2e
  `if((r8&1)==0)`，置位侧才回写 |=1）；取 0 =「VLVD 比较器未使能的 boot 初态」——
  与「W4[4] 正是 VLVD 的初始化者、bit0/bit2 都由本函数自己写」自洽；高位无读者
  不发明。0xfd3604 LRCT 先例同款「具名探路值」证据等级。
- BV 的模型参数 `p33_canary_projection`/`p33_canary_first_read_words` 已在
  br28_model（:3555/:3926）——本接线只做 probe_irq_entry 场景参数+registry 条目
  （BV 当时「纯模型+单测，不改 probe_irq_entry 场景区」的另一半）。

```python
# irq_case 新参数（默认关）:
p33_canary=False,                  # 接线 BV 既有 P33CanaryWatchRegisters
p33_canary_first_read_words=None,  # 值开关:{0xf2c044:0} 具名首读字

# registry:
Feature('p33_canary', 'conditional', ('p11_power_init',), False, None,
        'P3_VLVD_CON(0xf2c044)/P3_PCNT_CON(0xf2c124) 金丝雀写记录+首读具名 fixture'
        '(BV 备而未接线;W4[4] 0x12c29a 量测分支首读 0xf2c044——BA §2.2 修正后健康'
        '路径必经;触发即停=未覆盖/写后/重复读/越形写全严格停;路 BM 接线)'),
Feature('p33_canary_first_read_words', 'conditional', ('p33_canary',), True,
        'P33_CANARY_FIRST_READ_WORDS',
        '金丝雀首读具名字 fixture({0xf2c044:0}=VLVD 未使能 boot 初态,'
        'synthetic-not-measured)'),
```

依赖边 `p11_power_init`：与 p33 族其余成员一致；**不依赖** jl_pvdd_trim_writes
（0x12c29a 在 0x60685fe 之后执行，但两开关语义正交——canary 不需要容器，容器不
需要 canary；分开落地时先容器后 canary 只是执行顺序，不是 require）。

## 3. P2M 邮箱 ack 规格（预案④，BA §5.2 P6 行的实施规格书）

### 3.1 0x12cc7e 全 16 指令 + 邮箱机制钉死（含三处勘误）

[静态·本路] flash 母本 0x060c6456..0x060c6492（RAM 0x12cc7e..0x12ccba）：

```
0x12cc7e: [--sp]={rets,r4}; r4=r0            ; r0=消息字节（S15/S17 喂狗传 8）
0x12cc82: call 0x12b55c                       ; pend：cli + IPMASK((cnum<<11)+
          ;   0xFFE1A8)=6 + 每核计数器[0x1265F0+4cnum]++ + 条件 sti + lockset
          ;   （OS 临界区助手；CPU1 停泊路径已执行过 0x12b5b0，既有机器面）
0x12cc8c: r1=0xFF; r0=0xF27F43                ; ★P2M+3（P2M 消息区回声槽）
0x12cc94: b[0xF27F43]=0xFF                    ; ★清回声槽（P2M 区未映射→首停点）
0x12cc96: r1=r0+0x44; b[0xF27F87]=r4          ; ★M2P+7=M2P 消息区（已映射窗✓）写消息
0x12cc9e: r1=0xF2A608; [r1+0x10]=0x400        ; ★[0xF2A618]=M2P_INT_SET=bit10：kick P11
0x12cca2: csync
0x12cca4: r2=[r1+4]                           ; ★读 [0xF2A60C]=P2M_INT_PND
0x12cca6: if ((r2&0x1000)==0) goto csync      ; ★无界轮询 bit12（ack pending）
0x12ccaa: r2=b[0xF27F43](u)                   ; ★读回声槽
0x12ccac: if (r2!=r4) goto csync              ; ★回声字节≠消息→回轮询（无界）
0x12ccb0: [r1+0]=0x1000                       ; ★[0xF2A608]=P2M_INT_CLR=bit12：清 ack
0x12ccb4: call 0x12b5b0                       ; post：计数器--、IPMASK 复位、lockclr
0x12ccb8: 返回
```

**勘误三件（对既有记录）**：

1. **消息字节地址**：BA §4.3/AG §4#7 记 b[0xF27F03]/b[0xF27F47]——raw 宽立即数
   `43 7f f2 00`（0x060c6462）钉死实为 **b[0xF27F43]=P2M_MESSAGE_ACCESS(3)** 与
   **b[0xF27F87]=M2P_MESSAGE_ACCESS(7)**（+0x40 转写滑移；P2M 区=0xF27F40..80、
   M2P 区=0xF27F80..FE0，p11_app.h L40-51 布局）。
2. **ack 轮询地址**：BA §4.3/§5.1 #11 记 [0xF2A61C]——实为 **[0xF2A60C]=P2M_INT_PND**
   （基址 r1=0xF2A608、poll [r1+4]；AG §4#7 的 0xF2A60C 正确）。
3. **寄存器命名**：模型注释/AY/BG 记 0xf2a618 为「P2M_CLK」——按 p11_sfr.h
   P11_SYSTEM_TypeDef（基 0xf2a600=P11_SYS_CON1 0xf2a628/MEM_PWR_CON 0xf2a62c 双锚
   验算）**0xf2a618=M2P_INT_SET（+0x18）**，P2M_CLK_CON0 是 0xf2a610（+0x10），
   p11_app.h L110-127 宏名互证（P11_M2P_INT_SET/P11_P2M_INT_CLR/P11_P2M_INT_PND，
   PND 行 SDK 自己标了 `//?`）。「P2M_CLK ack」应改称「P11 邮箱 M2P_INT_SET kick →
   P2M_INT_PND bit12 ack」。

**首停点修正**：BA #11 行记 0x12cc9e（[0xF2A618] 写）——P2M 区未映射
（probe_irq_entry.py:438-441 注释明言「若后续前缀出现 P2M 写,再同语义扩窗」），
**b[0xF27F43]=0xFF 字节写（0x12CC94）先撞 unmapped_or_readonly_write**。

**调用面全扫** [静态·本路，线性 call-target 扫描]：0x12cc7e 全 app 5 个调用点——
0x600a59c（S15/S17 喂狗，arg=8，健康路径唯一）；0x6068f98/0x606909a/0x60690f0/
0x60691d8（pmu_task/电源框架域——pmu_task 无事件时 pend 不跑，零执行）；喂狗函数
0x600a590 全 app 5 个调用点——0x600a5b4（S15 包装）、0x6011d64（**S12**，门
h[QCY+344]==0x5A22 才执行，实测世界产品字未钉、BA 归「通过」需补此条件）、
0x6012462（S17 环头）、0x6017d32（handler 域 S19 后）、0x602edda（**W1[3] 尾 esco
块内，G6=0 整块不执行** [文档：AG §4#2]）。另 0x6069062 `[0xF2A618]=0x800`（pmu
框架）与 0x602f464 `=0x80`+b[0xF27F88]=0x57（IRQ68 handler 域）是同 idiom 的其他
kick 位——均不在健康 boot 前缀，登记不建模。

### 3.2 ack 投影方案（三选一裁决）

| 方案 | 内容 | 裁决 |
|---|---|---|
| **(a) 请求-响应投影（推荐）** | 观测到 kick（对 0xF2A618 恰 0x400 整字写）后：①P2M_INT_PND 读回=0x1000（bit12 置位）；②P2M+3（0xF27F43）读回=**M2P+7（0xF27F87）当前存储值**（回声=P11 把消息字节写回 P2M 槽）；③对 0xF2A608 恰 0x1000 整字写记录并清 ack（后续 PND 读回 0）。模型自有状态（P2mMailboxAck 类），0xF27F43 读写双收口进模型（写记录、读按武装态服务） | **采用**。p11_lpwr_response_projection 逐句同款（「仅在观察到真实请求后投影响应、延迟不建模、其余位不发明」）；即时 ack（首次 poll 读即见 bit12）→ 零自旋；不建延迟（lpwr 先例 response_latency_modeled=False） |
| (b) N 步延迟 ack | kick 后 N 模型步才置位 | 否决：延迟无任何证据；且 poll 环纯忙等无 idle（csync+ld+test），延迟只能靠轮询计数凑，发明时序 |
| (c) 对核真实回 | q32s 侧真执行消费+回写 | 否决：本仓不执行 q32s（q32s-static 解码缺口钉死 [文档：p11-domain]）；「P11 已消费」只能作显式假设 |

**武装态状态机**（防跨事务串音）：写 0xF27F43（0xFF 清槽）→ 解除武装（读回=存储
值）；kick 0x400 → 武装（PND bit12=1、回声生效）；CLR 0x1000 → PND 清位（回声保持
到下次清槽——固件每事务都先清槽，无越窗读）。轮询无超时问题：ack 未武装时
0xF2A60C 读=unmapped 诚实停（世界错即停，不兜底自旋）；武装后首读即 1 →
`goto −8` 环零迭代。**喂狗门 b[0x1299f8] 运行值复核仍前置**（BA §9.2：写者未钉、
现测 1；若 S15 时刻归 0，邮箱路径消失、本投影惰性零事务=负例对照）。

### 3.3 开关 + registry + 报告键

```python
# irq_case 新参数（默认关）:
p2m_mailbox_ack=False,   # P11 邮箱 ack 投影:kick [0xF2A618]=0x400 后
                          # P2M_INT_PND(0xF2A60C) bit12 读回 1+P2M+3(0xF27F43)
                          # 回声=M2P+7(0xF27F87)值;P2M_INT_CLR(0xF2A608)=0x1000 清位

Feature('p2m_mailbox_ack', 'conditional', ('p11_power_init',), False, None,
        'P11 邮箱 ack 完成投影(W4 后 S15/S17 喂狗 0x12cc7e:M2P_INT_SET kick 0x400→'
        'P2M_INT_PND bit12 置位+P2M+3 回声字节;lpwr 请求-响应同款,即时无延迟,'
        'synthetic-not-measured;路 BM 设计;M2P+7 字节在既有 M2P 窗内)'),
```

报告键草案：`'p2m_mailbox': {'kicks': N, 'transactions': [{arg, poll_reads,
echo_byte, clr_writes}], 'gate_byte_0x1299f8_last': …, 'assumption': 'P11 consumed
the message (explicit named assumption); latency not modeled; no other P2M_INT_PND
bits invented'}`。场景行 post_stop：事务数=S15 11+S17 151=162（fixture 世界，
门=1 时）、每事务 poll_reads==1、echo_byte==8；负例=开关关停在 0x12CC94
unmapped 写。单测：kick 前读 PND=unmapped；kick 后首读 1、CLR 后 0；清槽解除回声；
非 0x400 kick 值（0x800/0x80 域）只记录不武装。

**与 P11 邮箱既有模型的衔接**：M2P 窗（0xF27F80..FE0）机器侧 harness 区不动
（0xF27F87 写/读已工作）；P2M 区**只收口 0xF27F43 一个字节进模型**（其余 P2M 字节
继续未映射——0xf27f4a..4d 的 heap fixture 走 hardware.read_bytes 既不被影响，
未来前缀若读写其他 P2M 字节再扩窗，probe_irq_entry 既有注释的既定哲学）；0xF2A618/
0xF2A60C/0xF2A608 三字全部模型收口（写记录/读投影），与 P11WindowRegisters 的
「写记录+读回拒绝」族并立但不并入（那族是 power_init 窗，本族带响应语义）。

## 4. S19 冲刺总装规格（五预案实施顺序 + 层叠 + 停点串联 + 打包 + 重钉纪律）

### 4.1 五预案清单与状态

| 预案 | 内容 | 设计 | 实施 |
|---|---|---|---|
| ① PVDD 容器 | 0xf2c274/0xf2c244/0xf2c10c 容器 + RES 行 0x10005 | **本文 §1** | 未 |
| ② TIMER2 完成投影 | 0xfd0600 族计数器+bit15+锚定唤醒 0x12cd76 | BK 全设计 | 未 |
| ③ VLVD canary 接线 | p33_canary 落 conditional+首读 0 | **本文 §2** | 未 |
| ④ P2M 邮箱 ack | kick→PND bit12+回声字节 | **本文 §3** | 未 |
| ⑤ config-523 fixture | 70B 对象 PC 锚定首装 | BH 全设计 | 未 |
| （前置 P-1） | DAA_CON1 0xfe0a04 读回容器（AW §2.2 族） | 在途路 | 在途 |
| （前置 event_clock） | 事件时钟进 conditional+BI 首验 | BG §4.2/BP G3 | 未 |

### 4.2 实施顺序与开关层叠图

```
p11_heap_experimental_config 条件栈（现状 = base 19 + conditional 32）
 │
 ├─ [批 0a·在途] P-1：DAA_CON1 读回容器（ADD A 写记录+读回族,AW §2.2）
 │    → W1[3] 尾段过（DAC 簇 AK✓ + IRQ68 注册 RAM + 'anc' 创建 IPI）
 │    → W1[4] 桩/0x6011a08/S9(-22)/W2×11/W3/W4[0..3] 全过（BA #1-3）
 │    → 新前沿 = W4[4] 停点① 0x60686fc
 ├─ [批 0b·前置] event_clock 进 conditional（BG §4.2 草案）+ BI G3 首验 A1-A5
 │    （W2[0] 的 3×os_time_dly(2)=6 tick 是第一个事件时钟消费者——BA #2/BK §3.3；
 │      不开则批 1 放行后停在 W2[0] 首次全核 IDLE）
 ├─ [批 1·W4[4] 簇批,一 PR] ① jl_pvdd_trim_writes + ③ p33_canary(+首读值)
 │    + ② timer2_delay_projection（BK §3.3 条目）+ FD31_ADC_RES_S19_VALUES
 │    （值常量切换：+0x10005=400，并 BH 的 +0x10001=4）
 │    → 新前沿 = S15 喂狗首停 0x12CC94（若 S12 产品字=0x5A22 则更早,运行观测）
 ├─ [批 2·电池段批,一 PR] ④ p2m_mailbox_ack + ⑤ cfg523_named_fixture（BH §4.2）
 │    → 新前沿 = S18 (20,2) 特路 0x6010946（若 id=2 节点缺失,BA #14 残余,独立
 │      三选一）或 S19 park 形态（BE §4.5 两候选,归本批实施路定夺）
 └─ [批 3·D1 场景批] BI 首验正式跑 + d1_acceptance/d1_window_steps/d1_post_inject
      （BJ 按 BE 蓝图 §8 分解,不属本规格）
```

顺序依据：执行序（W4[4] 在前、电池段在后）×依赖序（event_clock 是 ②④⑤ 共同
前置：TIMER2 计数/S16/S17 tick 等待都靠它）×归因隔离（批 1/批 2 是两个独立失败
域，各自带负例）。批 1 内四件同 PR 的理由：都服务同一段（W4[4]），前沿只重钉一次
（0x60686fc→0x12CC94），且 RES 值常量切换必须与容器同落（否则环内即停）。

### 4.3 停点序列串联表（从当前前沿到 S19 的完整预演）

「判定」= 假设前序停点按本表预案放行；步数量级=模型步（事件时钟段含快进）。

| # | 段/站点 | PC | kind/门 | 预案 | 量级 | 出处 |
|---|---|---|---|---|---|---|
| 0 | **当前前沿** | 0x606b426 | 读 0xfe0a04 DAA_CON1 | P-1 在途（ADD A 容器） | — | HANDOFF |
| 0b | W1[3] 尾+W1[4]+S9+W2+W3+W4[0..3] | — | DAC 簇 AK✓/纯 RAM/IPI/6 tick 事件时钟 | 通过（**批 0b 前置**） | 10⁴-10⁵ | BA #0-3 |
| 1 | W4[4] 停点① | 0x60686fc | [0xf2c274]&=0xFF0 复合 RMW（unmapped） | ①容器 | +10² | BA #4/本文 §1.1 |
| 2 | W4[4] 停点② | 0x6068710 | [0xf2c244] 显式读 | ①容器 | +个位 | BA #5 |
| 3 | trim 环 15 轮 | 0x6068732 环体 | 0x10005 采样（**RES 行缺=BA 勘误**）+0x12e29c 自旋 | ①RES 行 0x10005=400 | 4-6×10⁵ | 本文 §1.3 |
| 4 | W4[4] 停点③ | 0x12cd36 | [0xfd0600]=0x4000 写 | ②TIMER2（2 事务×4000 步+交错 handler） | +10⁴ | BA #6/BK |
| 5 | W4[4] 停点④ | 0x6068628 | [0xf2c10c] RMW | ①容器 | +10² | BA #7 |
| 6 | W4[4] 停点⑤ | 0x12c2d6 | 读 0xf2c044 首读（**必触非条件**，v4=2000≤3699） | ③canary 首读 0 | +10³-10⁴ | 本文 §2.1 |
| 7 | W4[4] 尾 0x60688e6 | — | pend/post（IPMASK/lock 既有面）+计数器 0→1 跳过 CLK_CON2 | 通过 | +10² | BA §2.1/本文 §3.1 |
| 8 | W4[5]/S11/S12 | — | PMU 回调/dev_mg/VM（S12 产品字≠0x5A22 跳过喂狗=运行观测） | 通过（S12 喂狗若走→批 2 提前触） | 10³-10⁴ | BA #9/本文 §3.1 |
| 9 | S13/S14 门 | 0x60122b6/0x6012300 | b[0x127368]==2?/b[0x126834]?（实测双 0） | 通过，电池路径 | +10² | BA #10 |
| 10 | **S15 喂狗首停** | **0x12CC94** | b[0xF27F43]=0xFF 写（**首停点勘误**；门 b[0x1299f8] 复测前置） | ④邮箱 ack | 10³ | 本文 §3.1 |
| 11 | S15 11 轮环 | 0x6012330 | 每轮邮箱事务+缓存读（阈值 0 恒正常侧，K1 不可达） | ④（11 事务） | 10⁴ | BA #11 |
| 12 | S16 | — | 5×os_time_dly(10) | event_clock | ~2×10⁶ | BA #12 |
| 13 | S17 151 迭代 | 0x6012462 | 每迭代喂狗+1 tick；无 fixture 首迭代即 K2 | ⑤cfg523（BH） | ~6×10⁶+151 事务 | BA #13/#13b/BH §3 |
| 14 | S18 两 post+特路 | 0x60123aa/b2 | (20,2) 0x126F3C id=2 节点存在性（0x6010946 潜在独立停点=运行观测） | post 通过；特路停点归前沿路 | +10²-10³ | BA #14/BZ |
| 15 | **S19 循环→park** | 0x60123e6→0x6064cf6 | 空队列 pend 写面+让出链+两核 0x12c5ca | BI/BJ 域（BE 蓝图） | +10³ | BA #15/§6.2 |

**全快照预算**：3.17M +（#0b-#9）≈0.4M +（#1-#7）≈0.6M +（#10-#12）≈2.1M +
（#13）≈6.1M + 尾段 ≈ **~1.2×10⁷ 模型步/次全程**（S16+S17 事件时钟 ~8×10⁶ 占
2/3；BI 契约逐模型步推进、无跳步——BE §7 已记 ~6-8 分钟风险，全程≈4×当前场景
时长；缓解=批 2 过线后落 BD park 快照，D1 回归走 Q2）。

### 4.4 一次打包 vs 分批：**分批（0b/1/2/3 四批）**

- **一次打包（8 开关+2 值常量一 PR）**：优点=前沿只重钉一次、少三轮回归；缺点=
  ①失败归因混杂——任何一段撞意外停点都要在 1.2×10⁷ 步世界里定位（S17 的 6M
  事件时钟段尤其）；②review 面太大（三设计+BH+BK 五文档一次落码）；③负例对照
  粒度粗（每开关的「关=诚实停」形状无法逐个在前沿报告中显形）。
- **分批（推荐）**：每批=「设计文档齐备的那个域」一次落地+一次重钉+一次全绿；
  批 1（W4[4] 簇）/批 2（电池段）是天然故障域边界；批 0b 是共享前置单独落（BI
  首验本来就要先跑，BE §1.2 既定顺序）；批 3 归 BJ 既有蓝图。代价=多两轮
  check_local 全量（每轮 ~分钟级，可接受）。
- 无论分批/一次：每开关都带「默认关 legacy 逐字节不变」单测（AZ 反例纪律），
  registry 条目与 irq_case 参数同 PR（validate() 闭包校验钉死）。

### 4.5 全量绿门槛 + 重钉纪律（每批通用）

1. `python3 tools/check_local.py --units` 绿（+有私有 ROM 时 `--rom` 全绿）；
2. legacy（全部新开关默认关）报告 stash 对照逐字节不变；BY 场景（旧 RES 常量）
   逐字节不变；
3. 前沿重钉：`FRONTIERS['p11_heap_experimental']` 改新停点（probe require/analyze
   freshness/test 期望自动跟随，registry 单点）；freshness_fields 维持
   (kind,address)；批 2 的停点形态决策（S19 循环头 vs park 前最后指令停点）按
   BE §4.5 留给实施路，但**必须在同 PR 定死**（可复现性）；
4. 三镜像 parity 延伸：每批过线后 official↔b79 零散度复核（EQ ±常数/reconverge
   既有机制）；批 2 过线=S19 park 首次三镜像到达；
5. HANDOFF 前沿表+「已实现」条目人工更新（registry 不管叙事）；验收记录不入
   （D1 五条归 BJ 过线时落档，BE §6 模板）；
6. 负例钉子随批落单测：批 1=容器关停 0x60686fc/canary 关停 0x12c2d6/TIMER2 关停
   0x12cd36（+BK §5.4 的 7 单测）；批 2=邮箱关停 0x12CC94/cfg523 关→K2（BH §4.3
   两负例）。

## 5. HANDOFF 条目草案（合并时贴入「已实现」节，照格式）[设计]

> - **路 BM（sim-s19spec，本次合并，只读设计；字母与 sim-romtool 路 BM 重名，合并
>   时顺延）**：S19 冲刺剩余三预案（PVDD 容器/VLVD canary/P2M 邮箱 ack）+五预案
>   总装规格书，主交付 [docs/s19-final-specs.md](docs/s19-final-specs.md)。①**PVDD
>   容器**：P3_ANA_CON13(0xf2c274)/P3_ANA_CON1(0xf2c244)/P3_PVDD0_AUTO(0xf2c10c)
>   逐地址访问点/形状钉死（复合 RMW+显式 ld/st 混合，读值真实参与写回）→ AM 容器
>   同款（合成 0+读回=当前值）新开关 `jl_pvdd_trim_writes`；**勘误 BA「trim 环采样
>   fd31✓」——0x10005 不在 RES 具名档，须同 PR 扩 `FD31_ADC_RES_S19_VALUES`
>   （+0x10005=400）**；常量值使环恒走满 15 轮落缺省段（中途退出需逐轮值表=过拟合
>   不造），W4[4] 全簇预算修订 ≈5×10⁵ 步。②**canary 接线**：0x12c29a 量测分支
>   全钉（首读 0xf2c044@0x12c2d6 先于一切写；两助手 [0xf2c044]=0 整字+CON5 8 档
>   扫描+0x10005 单发采样）；**当前 fixture 世界必触非条件**（0x10006=500→
>   v4=2000≤3699）；BV 的 p33_canary 落 conditional（`p33_canary`+
>   `p33_canary_first_read_words={0xf2c044:0}`），触发即停报警 A1-A5 清单+场景
>   check 接线。③**P2M 邮箱 ack**：0x12cc7e 全 16 指令钉死；**三勘误**——消息字节
>   实为 **b[0xF27F43](P2M+3)/b[0xF27F87](M2P+7)**（BA/AG 的 0xF27F03/47 系 +0x40
>   滑移）、ack 轮询实为 **[0xF2A60C]=P2M_INT_PND**（AG 对 BA 错）、**0xF2A618 真名
>   M2P_INT_SET**（「P2M_CLK」系模型注释误名，P2M_CLK_CON0=0xf2a610）；首停点
>   **0x12CC94 字节写**（非 BA 记的 0x12cc9e）；投影=kick 0x400→PND bit12+P2M+3
>   回声=M2P+7 值（lpwr 请求-响应同款，即时无延迟），调用面 5+5 点全扫（S12
>   产品字门/esco G6=0/pmu_task/IRQ68 域不在健康前缀）。④**总装**：分四批
>   （event_clock 前置→W4[4] 簇批→电池段批→D1 场景批），开关层叠图+16 行停点
>   串联表（当前 3,170,362→S19 park ≈1.2×10⁷ 模型步，S16+S17 事件时钟 ~8×10⁶），
>   全量绿门槛+逐批重钉纪律。未改 tools/、未跑改动场景。

## 6. 对既有记录的修正清单

| 记录处 | 原记载 | 本路修正 |
|---|---|---|
| BA §4.3/§5.1 #11 + AG §4#7 | 喂狗邮箱写 b[0xF27F03]/b[0xF27F47] | **b[0xF27F43]=P2M+3（清回声槽）/b[0xF27F87]=M2P+7（消息字节）**（raw `43 7f f2 00` @0x060c6462；+0x40 转写滑移） |
| BA §4.3/§5.1 #11 | ack 轮询 [0xF2A61C] bit12 | **[0xF2A60C]=P2M_INT_PND**（基址 r1=0xF2A608、poll [r1+4]；AG §4#7 的 0xF2A60C 正确） |
| BA §4.3/§5.1 #11 停点⑥ | 首停=0x12cc9e [0xF2A618]=0x400 写 | 首停=**0x12CC94 b[0xF27F43]=0xFF 字节写**（P2M 区未映射，先于 kick） |
| br28_model.py:1250 邻域注释 + AY §4.1/BE §5 用语 | 0xf2a618=「P2M_CLK」 | **M2P_INT_SET**（p11_sfr.h P11_SYSTEM_TypeDef +0x18；P2M_CLK_CON0=0xf2a610；p11_app.h L124 宏互证） |
| BA §2.4/§5.1 #6 | trim 环 0x10005 采样「fd31✓」 | 0x10005 **不在** FD31_ADC_RES_NAMED_VALUES——撞 fd31_adc_res_channel_not_modeled，需 RES 行扩展（§1.3） |
| BA §3.2/§5.1 #4-8 | G-a 走向「运行时才定」/停点⑤「条件」 | 现行 fixture（0x10006=500）下静态可定：v=945∈[925,1250]→**trim 路**；v4=2000≤3699→**量测分支必走**（快路需 0x10006≥925） |
| BA §2.4 | trim 环「全环 ≈10⁵ 步量级」 | 修订 ≈4-6×10⁵（0x601174c=12×2 真采样事务×24 次/轮×15 轮，~10³ 步/事务，BY 实测单价） |
| BA §2.1 W4[4] 尾 | 「pend 0x12b55c+…+post 0x12b5b0…纯 RAM」 | pend/post 写 canonical IPMASK `(cnum<<11)+0xFFE1A8`（cli/sti/lockset 族）——**非纯 RAM**，但是 CPU1 停泊路径既有执行面，非新边界（本路全 26 指令钉死） |
| BA #9 行 | S12 归「通过（RAM+IPI）」 | S12 含喂狗调用 0x6011d64，**门 h[QCY+344]==0x5A22**（产品字）才执行——运行观测项；0x5A22 侧会提前触 P6 |

（BA §8 已修的 0xf2c244=P3_ANA_CON1/0x12cd26 健康路径必经/S17 直跳 S18 等不再重复；
BK §8 对 BA 的 0xFFF130→0xFFE1B0、0xFF1F 掩码、慢 2/快 1 三修正维持。）

## 7. 未钉死处（如实标注）

1. **b[0x1299f8]（喂狗邮箱门）写者与 S15 时刻值**：全 app 无 0x1299f0 字面量
   （本路扫描），写者经 QCY 基址+计算偏移不可静态钉；BA §0.1 实测 1（W1[3] 内）。
   实施路复测 S15 时刻值；若 0，P6 投影惰性（负例对照）。
2. **S12 产品字 h[0x126818] 运行值**：决定 S12 是否提前触邮箱（0x5A22 才触）；
   擦除世界大概率 0（config 缺省域 [推断]），未证。
3. **W4[4] 首 v 值的运行复核**：v=945 的推导链（mean=500×756/400）依赖 12×2 事务
   全部命中具名档+换算因子 [0x126864]>>27 的 5 位符号解码（BA §0.1 口径）——
   首跑核对 G-a 走向与预期一致，不符即回勘误。
4. **(20,2) 特路 0x126F3C 链表 id=2 节点存在性**（BA #14/BZ 运行观测项维持）：
   缺失则 0x6010946 `b[0]` 读是 S18 段独立新停点，归前沿路三选一，不属五预案。
5. **0xF2A60C PND 其余位/读后自清语义**：SDK 自标 `//?`；本投影只服务 bit12、
   CLR 显式清位，其余位恒 0 不发明——硅语义未证。
6. **pmu_task/IRQ68 域的邮箱 kick（0x800/0x80）与消息字节 0xF27F88**：不在健康
   boot 前缀，未建模；D2-b 注入域若触达按本规格同 idiom 扩展。
7. **trim 环内 0x601174c 的 0x10000 参考通道事务**：每轮 24 次事务中一半打
   0x10000（已有档 400）——事务形状与 BY 域一致 [推断]，首跑以
   fd31 报告的事务计数核对（预计 W4[4] 全簇 ≈440 次）。
8. **批 2 后的前沿停点形态**（S19 循环头 vs park）：BE §4.5 两候选，归实施路
   定夺，本规格只要求同 PR 钉死可复现。

## 8. 红线核对（本路自查）

- 设计不实施：未改 tools/ 任何文件、未跑任何改动场景（本文件为 docs/ 新增，
  git 状态可查）；
- 静态结论全部 derived-not-measured；fixture 值（RES 行 0x10005=400、canary 首读
  0、邮箱回声语义）全部 synthetic-not-measured 且逐条给证据等级；投影假设
  （§3.2 即时 ack/无延迟/不发明其余位）为显式假设文本，实施路原样进报告键；
- 锚点全部标出处（本路反汇编 raw 字节/call 尾偏移验算；BA/BK/BH/AY/AG/BE/SDK 头
  命名链）；未钉死值标运行观测项/[推断]；
- 对既有记录的修正（§6 九条）均附指令锚点，不改动原文档（实施路落地时按仓内
  勘误惯例处理）；
- 提交不含 Co-Authored-By、不含私有路径/私有工件名；`check_local.py --units` 绿
  （文档路 trivially，已跑）。
