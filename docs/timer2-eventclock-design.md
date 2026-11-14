# TIMER2 完成投影 + 事件时钟接入设计：域全图 + bit15 完成语义 + 唤醒件 + registry 草案（路 BK / sim-timer2，只读设计）

2026-11-14。基线 worktree `54e738c`（路 BH 合并点）。本页是 S19 冲刺五预案之 **P4**
（BA §5.2 / d1-scene-blueprint §5 BA #6 行）的**实施规格书**：把「TIMER2 完成投影+
事件时钟推进」从 BA 的一行预案落成实施路拿到即可开工的设计——JL_TIMER2 域全图
（寄存器族 SDK 名义/0x12cd26 全 26 指令/调用面）、bit15 完成投影语义（三选一裁决+
显式假设文本）、**忙等环可达性问题**（本路核心新发现：现有 idle_wait 投影下
poll-idle 环的读指令永不可达，完成投影必须带窄域唤醒件）、事件时钟接入
（1ms≈4000 步换算链+horizon 并入+层属裁决）、与 D1 蓝图（D1-3）的衔接核对、
实施规格（开关/模型改动/测试清单，照 BH §4/§5 模式）。**本路是设计文档，不是
实施**：未改 tools/ 任何代码、未跑任何改动场景（本文件为 docs/ 新增，git 状态可查）。

方法与证据强度：反汇编用仓库钉死的 pi32v2-isa codec（`probe_pi32v2.upstream()` +
`probe_boot.application()`，official app.bin sha `18f3d573…`/app sha `65cafc7f…`，
load base 0x06000100；RAM-code 从 flash 源窗按 RAM 基址 0x12a640↔0x060c3e18 重解码），
一次性脚本在 `~/tmp`（不进仓）；SDK 参考头在 reference-cache（sha 与
data/dependencies.json 钉死值同源）；ROM 孪生用已授权 Release 资产复核。
**[静态·本路]** = 本路指令锚定反汇编（新增钉死）；**[SDK]** = 参考头（gptimer_hw.h/
csfr.h/hwi.h，非精确 QCY 源）；**[文档]** = 已合并 docs/HANDOFF 条目（出处随行）；
**[模型]** = br28_model/br28_machine/probe_boot 源码阅读；**[推断]** = 上述链推出。
全部静态结论 **derived-not-measured**。

## 0. 一句话结论

**推荐 W1 方案「真计数器 + 窄域完成唤醒」**：JL_TIMER2 0xfd0600 族按 Timer0 同形状
建模（写记录+LRC 源计数器+PRD 跨越置 pending+CON 读回 bit15），忙等环的退出不靠
IRQ 交付而靠 **kick 先例的「锚定 PC 完成唤醒」**（停泊核 PC==0x12cd76 且 pending
置位 → idle 按完成计步、pc 前移 2 字节、无向量/无 RETI/无 ICFG 变化）。**本路核心
新发现有两个**：①现有 idle_wait 投影的唤醒语义（交付→handler→RTI 回被中断 PC=idle
指令地址→**再停泊**）使 0x12cd26 的 `idle→读 CON→测 bit15` 环**永远到不了读指令**
——任何「靠既有 Timer0/core-tick 唤醒后读回 bit15」的设计（BA §5.2 P4 行的字面
读法）在这个投影层上不成立，必须带唤醒件；②BA 记的特殊写地址 0xFFF130 系笔误，
实为 **(cnum<<11)+0xFFE1B0**（r2 立即数 0x3FF86C，raw `6c f8 3f`）——按 SDK csfr.h
布局该槽=**IWKUP_NUM**（IPMASK+8，紧跟 GIEMASK），写值 6=**IRQ_TIME2_IDX**（hwi.h），
「idle 前写本核唤醒中断号=TIMER2」的读法与上下文逐点互证。1ms≈4000 步的换算=
PRD 200 LRC tick × 80 RC16M 参考 tick/tick ÷ 4 参考 tick/步（Timer0 同一全局模型步轴），
闭环精确非估算。开关 `timer2_delay_projection` 进 conditional（requires
idle_wait+event_clock），与 event_clock 开关（BG §4.2 草案）同批或其后落地。

## 1. TIMER2 域全图（四问之一）

### 1.1 寄存器族与 CON 位布局 [SDK+静态·本路+文档]

| 地址 | SDK 名（出处） | 属性 | 本路径固件用法 [静态·本路] |
|---|---|---|---|
| 0xfd0600 | **JL_TIMER2->CON**（br28.h L651-665，ls_base+map_adr(0x06,0)，BO §N4 钉） | __RW | 整字写 0x4000（清 pending/停表）→ 整字写 0xC01（启动）→ **轮询读 bit15** → 退出写 `读值\|=0x4000`（W1C）→ 整字写 0（停） |
| 0xfd0604 | JL_TIMER2->CNT | __RW | 整字写 1（每次调用重写） |
| 0xfd0608 | JL_TIMER2->PRD | __RW | 整字写 `[0x129D88]×us/1e6`（us=1000、均值 200000 → **200**） |
| (cnum<<11)+0xFFE1B0 | **IWKUP_NUM**（csfr.h JL_TypeDef_q32DSP index 0x6c；本路具名，见 §1.2） | __RW | 整字写 6（idle 前、每调用一次） |

CON 位布局＝JL_TIMER_TypeDef 家族（gptimer_hw.h L51-86，Timer0 同族、路 AH 已三方
钉死该族编码）：**bits0-1 mode**（1=TIMER_MODE 计数、0=停）、**bits4-7 div**
（DIVISORS 表索引，0=÷1）、**bits10-13 src**（**3=LRC**、4=RC16M、5=STD12M）、
**bit14 PND_CLR**（W1C 写 1 清 pending）、**bit15 PND**（读位，Timer0 模型
`read` 的 `con|(pending<<15)` 同义）。本路径写值逐位展开：

- `0x4000` = 仅 PND_CLR（清 pending，mode=0 停表态）——Timer0 模型写路径
  `if value&0x4000:pending=False` 的同一语义 [模型]。
- `0xC01` = TIMER_MODE(1) | CLK_SRC_LRC(3<<10) | DIV_1(0<<4)：**LRC 源、不分频、
  计数**——「LRC 是字段 3」已有路 AH「切校准 LRC」语境反证互证 [文档：HANDOFF 路 AH]。

### 1.2 特殊写 0xFFE1B0：BA 笔误修正 + IWKUP_NUM 具名读法 [静态·本路+SDK]

反汇编（0x12cd62..0x12cd72，§1.3）：`r0=cnum; r0<<=11; r2=0x3FF86C; r3=6;
[r0+r2<<2]=r3` → 地址 = (cnum<<11)+0xFFE1B0。**BA §2.3/W4 行/§9.4 记的 0xFFF130
系转写笔误**（0xFFF130>>2=0x3FFC4C≠raw 立即数 0x3FF86C；0xFFE1B0>>2=0x3FF86C ✓）。

具名链（全部 [SDK] 推断级，但多点互证）：

1. csfr.h：q32DSP_sfr_base=0xff0000+0xe000=**0xFFE000**，JL_TypeDef_q32DSP 布局
   index 0x6a=IPMASK、0x6b=GIEMASK、0x6c=**IWKUP_NUM** → 0xFFE000+0x6c×4=0xFFE1B0。
2. 模型既有事实互证 [模型]：canonical IPMASK=0xffe1a8（=SDK 布局 0x6a×4+0xFFE000，
   br28_model.py:30）与 banked IPMASK=0xffe168（handler/idle-loop 实际寻址，:31）并存，
   per-core 步距 0x800 与本写法的 cnum<<11 一致——两种 IPMASK 视图下 0xFFE1B0 都落在
   IPMASK+8（GIEMASK 后一槽）。
3. **值互证**：写值 6 = hwi.h `IRQ_TIME2_IDX=6`（TIMER2 的 IRQ 线号）；写时机在
   `idle` 之前、写者就是即将靠 TIMER2 完成退出的忙等函数——「把本核 idle 唤醒
   中断号设为 TIMER2」的读法自洽。

诚实边界：SDK 头是参考材料非精确 QCY 源（IPMASK 双址即教训）；IWKUP_NUM 的硅
效果（写它是否真是 idle 唤醒选择器、与 ICU 使能的关系）**未测量**。建模取
**write-only 位记录**（值+计数，无读回、无行为效果），语义标注引本节。

### 1.3 0x12cd26(us)——TIMER2 延时 helper 全 26 指令 [静态·本路]（BA §2.3 的逐指令钉死）

RAM-code（flash 源 0x060c64fe，函数体 0x12cd26..0x12cd89，下一函数 0x12cd8a 起）：

```
0x12cd26: [--sp] = {rets, r4}              ; 序
0x12cd28: r1 = 0x129D88                    ; LRC 频率 20 项均值（路 AF/AL 域，实测 200000）
0x12cd2e: r2 = [r1+0]                      ; r2 = LRC 频率 Hz
0x12cd30: r1 = 0xFD0600                    ; JL_TIMER2 基址
0x12cd36: [r1+0] = 0x4000                  ; CON = PND_CLR（清 pending，停表态）
0x12cd3a: [r1+4] = 0x1                     ; CNT = 1
0x12cd3e: r3 = r2 * r0                     ; r3 = 频率 × us
0x12cd42: r0 = 1000000
0x12cd48: r0 = r3 / r0 (u)                 ; r0 = 频率×us/1e6 = PRD
0x12cd4c: r4 = 3000000
0x12cd52: r2 = 2                           ; 下限保护：PRD 最小 2
0x12cd54: if (r3 >= r4) r2 = r0            ; 频率×us ≥ 3e6（200kHz 下 us≥15）才用计算值
0x12cd5a: r0 = 0xC01                       ; TIMER_MODE|CLK_SRC_LRC|DIV_1
0x12cd5e: [r1+8] = r2                      ; PRD = r2（us=1000 → 200）
0x12cd60: [r1+0] = r0                      ; CON = 0xC01 启动
0x12cd62: r0 = cnum; r0 <<= 11; r2 = 0x3FF86C; r3 = 6
0x12cd70: [r0+r2<<2] = r3                  ; IWKUP_NUM（0xFFE1B0+cnum×0x800）= 6
0x12cd74: goto 2                           ; → 0x12cd76（环入口，首迭代先 idle 后读）
0x12cd76: idle                             ; ★忙等环头（锚点）
0x12cd78: r0 = [r1+0]                      ; 读 TIMER2 CON
0x12cd7a: r2 = r0.l (s)                    ; 低 16 位符号扩展 → bit15 落进符号位
0x12cd7c: ifs (r2 >= 0) goto -10           ; bit15==0 → 回 0x12cd76 再 idle（无界）
0x12cd80: r0 |= 1<<14                      ; bit15==1 退出：W1C 位置位（0x8C01→0xCC01）
0x12cd82: [r1+0] = r0                      ; 写 CON（清 pending；mode 位保留）
0x12cd84: [r1+0] = 0                       ; CON = 0 停表
0x12cd88..89: pop {rets, r4}               ; 返回（其后真实代码继续）
```

要点（与 AL 的 LRCT bit7 轮询同 idiom）：**等的位置=CON bit15（PND），方向=置位
退出，无界**（无轮询预算）；`ext.l.s`+`ifs(r2>=0)` 是「测 bit15」的符号位编码
（AL 路「死等清零」方向误读的先例已在 LRCT 处修正，此处方向无疑）。忙等环在
K1/K2 金丝雀树与条件 0x12cc40 分支的旧记录（BP §1.1「健康路径不可达」）已被 BA
§2.2 修正为「W4[4] 健康路径必经」[文档：BA]。

ROM 孪生 0xa778（本路用 Release 资产复核）[静态·本路]：同族编程习语
（`r4=0xFD0600; [r4]=0x4000; [r4+4]=0; …; [r4+8]=r0; [r4]|=0x31`），但**源/分频
不同**（0x4031：src=1=OSC、div=3=÷64、PRD=频率/64000×10）且**启动后立即返回**
（无 idle/poll——等待在调用侧）——家族习语互证、语义面（PND15/W1C14/整字编程
序列）不动，不外推其源配置。

### 1.4 调用面：0x60685fe 与全部 TIMER2 事务 [静态·本路+文档]

0x60685fe(trim, arg1)（体 0x60685fe..0x6068660，本路逐指令复核=BA §2.2，一处掩码
勘误见 §9）：PVDD1_AUTO(0xf2c110) RMW（并行对 B 半读旧值，trim 钳 15、
`(trim<<4)|trim` 塞低字节）→ **call 0x12cd26(1000)**（0x6068622，6 字节 call，
目标=0x6068628−99858690=0x12cd26 ✓）→ PVDD0_AUTO(0xf2c10c) 三 RMW（
&=0xFF1F|0x60 → &=0x1FEF|0x10 → insert umin(trim,arg1) bits4..11）→
P3_ANA_CON1(0xf2c244) &=0xFF7F。

调用点恰两处（call 偏移均验算 ✓）：

| 调用点 | 实参 | 所在路径 |
|---|---|---|
| 0x6068778 | (13, 0) | 慢路汇聚点 0x6068770 之后（trim 环 mode==3 与缺省段 G-b 都到达） |
| 0x60687f6 | (min(b[+2]+3,15), b[+3]) | 应用段 0x60687e0 内（两路共用） |

即 **0x60685fe 慢路恰 2 次、快路（VM99 校准过）恰 1 次**（BA「每路径 1-2 次」
精确化）；每次恰 1 个 TIMER2 事务（0x60685fe 内单次 call）。当前擦除世界=慢路
→ **W4[4] 一轮 = 2 个 TIMER2 事务**。其余调用者（K1/K2 金丝雀树+条件 0x12cc40）
健康世界不可达 [文档：BP/BA]；ROM 孪生不在路径 [文档：BO]。

## 2. bit15 完成投影设计（四问之二）

### 2.1 设计空间与关键机制发现（本路核心）

**发现（[模型]，本路新钉）**：现有 idle_wait 投影的唤醒闭环是「交付→handler→
RTI 装载**被中断 PC**（=idle 指令地址）→运行环再解码 idle→**再停泊**」
（deliver_* 的 `interrupted=cpu.pc; cpu.reti=interrupted`，br28_machine.py:456
邻域；BQ 单测 `reti_from==parked idle pc` 与 BI A1「重新 core_idle_wait_entered」
同款钉死）。对 0x12cd26 的 `idle→读→测` 环这意味着：**靠任何既有交付（Timer0
IRQ4/core tick IRQ3）唤醒后，核回到 0x12cd76 再停泊，0x12cd78 的 CON 读永不可达，
环永不退出**。该语义对 idle 任务公园（0x12c5ca，无环体依赖）与 os_time_dly（唤醒
发生在 handler 内的调度切换）无害，但对**外设完成轮询环**是结构性死角——TIMER2
是模型里第一个（也是当前唯一健康路径上的）此类消费者。

三选一裁决：

| 方案 | 内容 | 裁决 |
|---|---|---|
| **W1（推荐）** | 真计数器（LRC 源）+ pending 置位 + CON 读回 bit15 + **窄域完成唤醒**（kick 先例：停泊核 PC==0x12cd76 且 pending → idle 按完成计步、pc+=2、无向量/RETI/ICFG） | **采用**。计数语义与 Timer0 同构（不发明新外设行为）；唤醒件有 kick 直接先例（`wake_cpu1_control_kick`：「该 RMW 让 idle 按完成计步并前进到下一条指令，不发明中断向量、不改 held 位」——逐句同款）；投影面最窄（一个锚点 PC+一个模型内状态位） |
| W2 | TIMER2 到期经 IRQ6 交付（IrqDeliveryRequest(core,6)+向量槽 0x19fe18+入口三件套） | **否决（暂）**。IRQ6 的 ICU 使能状态零证据（ICU 配置字由固件 irq_enable 写入，本路径无观测）；IWKUP_NUM=6 写是机制**佐证**但不足以撑全套入口假设（银行/PSR/帧）。若实施路 trace 显示 IRQ6 nibble 使能可升级（§7-R1），升级面=W1 的唤醒件替换为交付件，计数器/读回零改动 |
| W3 | 无状态首读即完成（BY fd31_adc_pnd_completion 先例：读时合成 bit） | **否决**。(i) 丢弃 PRD 计数语义——4000 步的「延时」塌缩成 ~0 步，D1 预算/事件时钟推进量全部失真，恰与 BA P4 预案「事件时钟推进到 PRD 期」相反；(ii) **解决不了 idle 不可达**（首读仍要核先醒——还是要唤醒件），故 W3 只是 W1 减掉计数器的劣化子集。BY 选无状态是因为 ADC 转换无时钟模型可挂；TIMER2 恰恰有（LRC 源+事件时钟） |
| （对照） | 具名 fixture（PRD 完成时刻造值） | BA 原话已否：「PRD 完成时刻不是缺输入而是缺时钟模型」 |

**为什么不是「写命令后 N 步完成」的字面定时器**：投影不发明硬件时序——「完成
时刻」由模型计数器按事件时钟推进真实到达（N=PRD×20 步是**推导结果**不是预设
延时）；投影只声明「计数跨越 PRD 时 pending 置位」，这正是 Timer0 已验证的语义
（[模型] `advance` 的 `periods>=1 → pending=True`）。

### 2.2 投影语义全文（显式假设文本，实施路原样进报告键）

`timer2_delay_projection` 开启时：

1. **写记录**：CON/CNT/PRD 整字写全记录（值+次数+PC 形状照 lp_timer stores 先例）；
   写校验按 SDK 字段位图——mode bits0-1 ∈{0,1}（捕获/PWM 拒绝，照 Timer0
   `unsupported_timer_mode` 纪律）、src bits10-13 ==3（LRC；非 LRC 拒绝
   `unsupported_timer_clock_source`——本路径唯一观测值 0xC01）、div bits4-7 任意
   （查 DIVISORS）、bit14 写 1 清 pending（含复合字 0xCC01：mask 校验放行 W1C 位，
   存储 `value&~0xC000`）、bit15 写忽略。未开启时 0xfd0600 族读/写照旧
   unmapped/readonly 诚实停（当前 0x12cd36 停点形状不变）。
2. **计数器**：LRC 源名义 **200000 Hz**（derived-not-measured：AL 恒等式
   7.68e9/38400=200000，即固件自己的名义常数；真实 trim 率未测量）。每模型步
   source_ticks=4（RC16M 参考系）→ LRC 进账 4/80 tick，**分数余量累积**
   （`total=lrc_remainder+ticks; lrc_ticks,lrc_remainder=divmod(total,80)`，
   Timer0 STD12M 的 3/4 换算同构）；再过 DIVISORS[div] 分频（本路径恒 ÷1）；
   `cnt` 跨越 `prd+1` 时 `pending=True`（`expirations+=periods`，Timer0 同式）。
   **不读 RAM [0x129D88]**——固件已把它的频率信念编进 PRD 写值，模型按写下的
   PRD 计数（自洽性见 §3.1）。
3. **读回**：CON=存储配置字 |（pending？0x8000:0）；CNT=当前计数值；PRD=存储值
   （Timer0 `read` 同式；SDK __RW 三字读回有定义，lp_timer CON 读回先例同族）。
4. **完成唤醒**（窄域，显式假设）：当 pending 置位**且**某停泊核 PC==0x12cd76
   （指令锚定；helper 环头，全 app 唯一 [静态·本路]）时，该核按 kick 同款唤醒：
   `idle_wait_cores` 摘除、`pc+=2`（消费 idle 指令）、`steps+=1`、事件
   `core_woken_by_timer2_completion`（旗标 `wake_source_unverified=True`、
   `assumed_mechanism='timer2 pending breaks idle; kick precedent; no vector/RETI/
   ICFG change'`）。**硅机制大概率是 IRQ6+IWKUP_NUM（§1.2），未测量**；本投影
   不建 IRQ 交付、不写 ICU、不改 IPMASK。触发点两处：advance 内置位时（事件
   时钟逐步推进中/单核运行每步推进中）与 enter_idle_wait 停泊时 pending 已置位
   （0 步立即醒——覆盖「handler 执行期间跨过到期点」的窗口）。多个锚点停泊核
   逐一唤醒（实践 ≤1：W4[4] 只在 CPU0 跑）。
5. **退出序列零干预**：bit15=1 读回后的 W1C 写（0xCC01）、停表写（CON=0）、
   pop 返回全部真实代码执行，投影不介入。
6. **不建模清单**：TIMER2 IRQ6 交付与 IWKUP_NUM 写的硬件效果（记录 only）、
   硅 idle 指令语义（idle_wait 措辞不变）、LRC 真实频率与抖动、同刻仲裁
   （扫描序可复现性优先，契约 §3 措辞不变）。

### 2.3 与 lp_timer/ADC CON 先例的对比（为什么 TIMER2 不抄它们）

| 先例 | 形状 | 与 TIMER2 的差异 |
|---|---|---|
| lp_timer CON 读回（AL） | 读回=最后写值（SDK __RW 依据） | TIMER2 的 CON 读回含**动态位** bit15（计数器状态），纯最后写值会让忙等环死锁——必须叠计数器 |
| lrct_trim_done（AE） | 读时强制 bit7=1（无状态完成位，PLL-lock bit15 先例） | LRCT 无时钟模型可挂（trim 计数是硅测量）故读时合成；TIMER2 有 LRC 源+事件时钟，读时合成会丢 4000 步语义（=W3 的否决理由） |
| fd31_adc_pnd_completion（BY） | 读时合成 PND bit7（无状态）+RES 具名档 | ADC 转换时间无模型；且其 poll 环**无 idle**（80 轮纯忙等，核在跑），无「idle 不可达」问题——TIMER2 的环有 idle，问题域不同 |
| Timer0（BE/BQ） | 真计数器+闭式+交付请求 | TIMER2 抄计数器/闭式形状；**不抄交付**（IRQ6 无 ICU 证据），唤醒改 kick 型 |

## 3. 事件时钟设计（四问之三）

### 3.1 「1ms≈4000 步」换算链（依据=Timer0 的同一全局模型步轴）

- 固件算术 [静态·本路]：PRD = [0x129D88]×us/1e6；实测均值 200000 → us=1000 时
  PRD=200、CNT=1 → 到期需 N=prd+1−cnt=**200 个 LRC tick**。
- 模型换算 [模型]：1 LRC tick = 16MHz/200kHz = **80 个 RC16M 参考 tick**；
  每模型步 4 参考 tick（`source_ticks_per_step=4`，Timer0/core tick 同轴）→
  1 LRC tick = 20 模型步 → 200 tick = **恰 4000 模型步**（闭式，非估算；BA §2.3
  的「1 LRC tick=60 STD12M 步」是 STD12M-tick 口径：60×200=12000 STD12M tick
  =4000 步，同一数）。
- **非独立基准**：不引入第二个时间轴；TIMER2 的推进就是 `hardware.advance()`
  的每步调用（事件时钟段逐模型步、运行段每指令后——与 Timer0 完全同一条路），
  仅在 advance 内多累一个分数余量计数器。
- 自洽性注记：若真实 trim 率≠名义 200kHz，固件 PRD 与真实 tick 率**同比缩放**
  （延时真时间=us 不变），模型步数=PRD×20 随 PRD 变——投影按写下的 PRD 计数，
  不需要知道真实率；名义率只出现在「步数↔物理时间」的换算注里（该换算本来就
  不声称校准，契约 §1「步数与计数器值是仅有的时间量纲」不变）。

### 3.2 事件时钟接入：horizon 并入 + 同刻序 + 无事件诚实停（不重复造）

- **timer2_wake_horizon 并入同一 min()**（br28_machine.advance_event_clock，BQ
  的 G1 同款第三族）：pending 已置位且有锚点停泊核 = **1**（下一步即完成唤醒）；
  在计数 = 闭式 `S=ceil((80·M−lrc_remainder)/4)`，`M=DIVISORS[div]·(prd+1−cnt)−
  remainder`（观测形状 div=1/CNT=1/PRD=200 → S=(80×200−0)/4=**4000 精确**）；
  不在计数或无锚点停泊核 = None。闭式只作推进上限，实际置位仍由 advance() 逐步
  触发（Timer0/core tick 同构，契约 §2 措辞延伸到第三族）。
- **为什么必须进 min()**：完成唤醒现在是真唤醒事件——「无 core tick、无 Timer0、
  仅 TIMER2 在计数」的世界若不并入，会被误判「无事件」诚实停（BQ 修 G1 时对
  Timer0 犯过的同一类错，不再犯第三次）。
- **同刻序**：advance 扫描序定为 core tick（IRQ3，核号序）→ Timer0（IRQ4）→
  **TIMER2 完成唤醒** → 软件 pending（核号/IRQ 号序）。位置=Timer0 后（交付类
  事件优先于非交付唤醒，可复现性优先非硅声称）；单测钉死。同一虚拟步内先扫描
  到的事件先触发、同步内未扫描计数器不推进（契约 §3 原文适用）。
- **无事件诚实停语义保持**：TIMER2 在计数但**无锚点停泊核**（没人在等它）→ 不是
  事件——正确（pending 置位本身不唤醒任何人）；全核 IDLE 期间被屏蔽/无锚点/无
  其他事件 → `all_cores_idle_waiting`（note 扩一句「no anchored TIMER2 poll」）。
- **与 Timer0/core tick 的关系（不重复造）**：复用 advance_event_clock 逐模型步
  推进循环、IrqDeliveryRequest 分发闭包（dispatch_delivery）、wake horizon 的
  min() 框架、event_clock_advances 计数与 G4 horizon 构成报告键；**新代码只有**
  Timer2Delay 计数器类 + horizon 第三项 + 完成唤醒件。D1-2/H2 的交付计数面零新增
  （完成唤醒不产 IrqDeliveryRequest）。

### 3.3 registry 层属与开关设计

层属裁决：**conditional，不是 probe_only**。依据（与 BH §4.1 的 cfg523 同构）：

1. **W4[4] 在共享前沿路径上**：P1/P2（PVDD 容器）放行后，p11_heap 条件场景不带本
   投影将停在 0x12cd36 unmapped 写；再放行写记录则停在忙等环首次全核 IDLE——
   前沿要过 W4[4] 到 BA #7+，本开关必须在条件栈。
2. **事件时钟开关必须先行/同批**：不开 event_clock，写记录放行后第一次 idle 即
   `all_cores_idle_waiting`（合法停但 W4[4] 过不去）。事件时钟进条件栈的论证
   BG §4.2 已给（S16 os_time_dly）；本路加码一个更早的消费者序列：**条件路径上
   第一个事件时钟消费者是 W2[0] 的 3×os_time_dly(2)（BA #2 行已记「6 tick 事件
   时钟」），TIMER2（#6）是第一个「外设完成门控」的全核 IDLE**——event_clock
   开关（BG 草案）与本开关同 PR 或先行落地。
3. probe_only 只能服务停点后场景（key_chain 先例）；D1 入口必须真实启动
   （d1-acceptance §7）。

```python
# probe_irq_entry.irq_case 新参数（默认关）:
timer2_delay_projection=False,   # 开启时:JL_TIMER2 0xfd0600 族写记录+LRC 源计数器
                                 # +CON 读回 bit15+0xFFE1B0(IWKUP_NUM) 写记录
                                 # +锚定 PC 完成唤醒(0x12cd76,kick 先例)
                                 # +timer2_wake_horizon 并入事件时钟 min()

# frontier_registry.FEATURES 追加（BG 的 event_clock 条目落地后）:
Feature('timer2_delay_projection', 'conditional', ('idle_wait', 'event_clock'),
        False, None,
        'JL_TIMER2 延时域投影(W4[4] 0x60685fe→0x12cd26 忙等):0xfd0600 族写记录'
        '+LRC 源名义 200kHz 计数器(PRD 跨越置 pending)+CON 读回 bit15+完成唤醒'
        '(kick 先例,锚 0x12cd76,无 IRQ6 交付——ICU 使能无证据)+IWKUP_NUM '
        '0xFFE1B0 写记录;1ms=200 LRC tick=4000 模型步闭式;路 BK 设计'),
```

requires=('idle_wait','event_clock') 的理由：完成唤醒摘除停泊核需 idle_wait 投影
在场；计数器到期需事件时钟推进（全核 IDLE 时唯一推进路径）。无需
timer0_irq4_delivery/core_tick_irq3_delivery/repeat（唤醒自足；Timer0/core-tick
交错只是良性能量）。无数值 fixture → 非值开关。

### 3.4 报告键草案（默认关不出现，legacy 逐字节不变）

```python
'timer2': {'con': …, 'cnt': …, 'prd': …, 'pending': bool, 'expirations': N,
           'lrc_nominal_hz': 200000, 'writes': N, 'con_reads': N,
           'transactions': [{'prd': 200, 'cnt_start': 1, 'start_model_step': N,
                             'expiry_model_step': N, 'closed_form_steps': 4000,
                             'poll_wakes': [{'core': 0, 'pc': '0x12cd76',
                                             'idle_completion_is_assumption': True}]}],
           'iwkup_writes': [{'core': 0, 'address': '0xffe1b0', 'value': 6}],
           'readback_semantics': 'CON bit15 = modeled-pending projection (PND family, '
                                 'Timer0-precedent); completion timing = event-clock '
                                 'counted, not measured; wake is kick-precedent, '
                                 'wake_source_unverified'}
```

## 4. 与 D1 蓝图衔接核对（BG D1-3 依赖关系）

逐条核对（BG §2.3/§4.3/§5 对照）：

| D1 面 | TIMER2 落地后的影响 | 结论 |
|---|---|---|
| **D1-3 core tick 首溢出闭式** | 闭式输入= park 态 core tick CNT/PRD/progress，与 TIMER2 无耦合；W4[4] 退出时固件真实写 CON=0（0x12cd84）→ **park 态 TIMER2 恒停表** → horizon=None 不进 min() → S_tick 数值不变 | **零影响** |
| D1-2 周期守恒/H2 同源重发上界 | TIMER2 完成唤醒不产 IrqDeliveryRequest → 交付计数面零新增；D1 窗口内 TIMER2 零事件（停表）→ P_park 间隔与 H2 上界公式不变 | 零影响 |
| 事件时钟 min() 构成 | 多第三族；D1 窗口内恒 None（停表）——BI G4/`park.*` 报告键不比对构成清单，兼容 | 兼容（新增键 opt-in） |
| D1 验收记录 model_assumptions | BE §6 底稿已预留「BA 五预案 flag（…TIMER2 完成…）」占位——落名 `timer2_delay_projection` 即可 | 落名 |
| BI 首验 A1-A5 | A1「从实际 park 状态算闭式与 advances 互证」不受影响；BI 若在本路前跑形状不变，本路后 horizon 键多一可选源 | 兼容 |
| 快照（BD 设计） | park 快照含 TIMER2 停表态（con=0）；恢复等价绿不受影响 | 零影响 |

**唯一方向性依赖**（BG §5 前置表的行序保持）：TIMER2（BA #6）在 W2[0]（#2）与
S16（#12）之间——三者共同要求 event_clock 已进条件栈；本路把 event_clock 的
「首个消费者」证据从 S16（BG 论证）前移到 W2[0]/W4[4]（BA 表行序），不改变
BG 的层属结论，只强化它。

## 5. 实施规格（开工规格，照 BH §4/§5 模式）

### 5.1 模型改动（tools/br28_model.py）

| 项 | 内容 |
|---|---|
| 常量 | `TIMER2=0xfd0600`；`IWKUP_NUM_BASE=0xFFE1B0`（per-core +cnum×0x800）；`LRC_HZ_NOMINAL=200000`（derived 注释引 AL 恒等式） |
| 新类 `Timer2Delay` | 照 Timer0 形状：`con/cnt/prd/pending/lrc_remainder/remainder/expirations`；`write(offset,value)`（§2.2-1 校验）、`read(offset)`（§2.2-3）、`advance(source_ticks)`（两级分数：`total=lrc_rem+ticks; lrc,rem=divmod(total,80)` → divisor → `periods,cnt=divmod(cnt+ticks,prd+1)`）、`steps_to_next_expiration()`（§3.2 闭式）；**无 delivery_in_flight**（无交付） |
| BR28Model | 构造参数 `timer2_delay_projection=False` → `self.timer2=Timer2Delay()`（None=关闭，照 fd31 系列惯例）；`handles`/read/write/rmw 分发并入 0xfd0600 族与 0xFFE1B0（写记录+计数，读回照 §2.2）；`advance()` 在 Timer0 段后挂 `self.timer2.advance(self.source_ticks_per_step)`；`timer2_wake_horizon(icfg)` 包装（icfg 形参仅为与 timer0_wake_horizon 对称，TIMER2 无资格面）；`report()['timer2']`（§3.4） |
| 红线 | 默认关=全部改动不可达，legacy 报告逐字节不变；不触碰 Timer0/IRQ4/IRQ3/软件 pending 任何路径 |

### 5.2 机器改动（tools/br28_machine.py）

| 项 | 内容 |
|---|---|
| `wake_timer2_poll(cpu)` | kick 先例逐句对齐：require 停泊核 `decode(pc).id=='idle'` 且 pc==0x12cd76（锚字面量+注释引 §1.3）；摘除 idle_wait_cores、pc+=2、steps+=1、record `core_woken_by_timer2_completion`（`idle_completion_is_assumption`/`wake_source_unverified` 旗标照 kick 事件） |
| `advance_event_clock` | min() 并入 `timer2_wake_horizon`（None 跳过；=1 时单步探查同软件 pending 先例）；逐步 `hardware.advance()` 后检查「pending 新置位+锚点停泊核」→ 完成唤醒并**退出推进循环**（唤醒后核在跑，事件时钟退出——与 IrqDeliveryRequest 同一出口纪律） |
| `enter_idle_wait` | 停泊即查：pending 已置位且 pc==锚 → 0 步立即唤醒（不进推进循环；防「handler 执行期间已到期」死等） |
| 运行环（probe_boot.py） | 交付后的再停泊路径天然走 enter_idle_wait 的新检查，无需改 dispatch；单核停泊+另一核在跑的通用检查挂 `after_step`（现状 W4[4] 世界不会出现，留通用性一行即可，不扩语义） |
| 红线 | 全部在 timer2_delay_projection+idle_wait 开启路径内；`event_clock_engaged`/`core_idle_wait_entered` 等既有事件形状不变 |

### 5.3 场景接线（tools/probe_irq_entry.py + tools/frontier_registry.py）

`irq_case` 加参数（§3.3 草案）→ `machine_options['timer2_delay_projection']=True`
+ hardware options 同名；registry `Feature` 条目（§3.3）；**与 W4[4] PVDD 容器
（预案 P1/P2/P5）同簇不同开关、可同 PR**（BA §5.2 打包建议「两开关」的 TIMER2
半边）；前沿重钉（0x12cd36 → W4[4] 其后停点）与实施路同 PR（条件开关落地纪律，
BH §4.4 同款）；budget 影响见 §5.5。

### 5.4 测试清单

单测（tools/test_br28_model.py 新 Timer2Tests + EventClockTests 增例；照 BQ 的
7 例风格）：

1. 写路径：CON=0x4000/CNT=1/PRD=200/CON=0xC01 序列被接受并记录；非 LRC 源
   （如 0x101=RC16M src4）拒绝 `unsupported_timer_clock_source`；mode=2/3 拒绝；
   0xCC01 写清 pending 且存储 con=0xC01。
2. 计数精确性：LRC 分数累积——恰 20 步/tick、第 4000 步 pending 置位（闭式
   `steps_to_next_expiration` 与逐步 advance 从中途状态（含 lrc_remainder≠0）
   互证，Timer0 闭式测试同款）；div≠1 形状一组。
3. 读回：pending 前 CON 读=0xC01、pending 后=0x8C01；CNT/PRD 读回。
4. **完成唤醒端到端（W1 核心）**：合成双核——锚点停泊+TIMER2 计数、无 core tick
   无 Timer0 → `event_clock_engaged` 后恰 +4000 步
   `core_woken_by_timer2_completion`、核 pc==0x12cd78、无 IrqDeliveryRequest。
5. **交错反例**：Timer0 到期早于 TIMER2（park 后 +1000 步到期）→ 先 IRQ4 交付
   →handler→RTI 回 0x12cd76 再停泊 → TIMER2 到期（在 handler 执行期间跨过）→
   停泊即查 0 步唤醒 → 环退出；全程交付恰 1 次、唤醒恰 1 次。
6. 无事件诚实停：TIMER2 计数但无锚点停泊核（核停在别处）→ `all_cores_idle_waiting`
   note 含 TIMER2 说明；默认关逐字节 legacy（0x12cd36 unmapped 写停点形状）。
7. IWKUP 写记录：值 6/计数；读回保持严格停（不发明）。

probe 场景行（实施路）：p11_heap 条件栈 +timer2（+event_clock+idle_wait 前置），
post_stop 观察 `timer2.transactions`：**2 事务（慢路）/1 事务（快路 fixture 世界）、
每事务 closed_form_steps=4000、poll_wakes≥1、CON 末态=0、W1C 恰每事务一次**；
负例=关 event_clock 只开写记录 → 停点=all_cores_idle_waiting@0x12cd76（文档化
形状，非 FAIL）。

### 5.5 落地纪律与预算

- 同 PR：Feature+常量+模型/机器/接线+前沿重钉+单测（条件开关既有纪律）；
  event_clock 开关（BG 草案/G3）先行或同 PR。
- 模型步预算：W4[4] 慢路 = trim 环自旋 ~10⁵（0x12e29c 纯自旋，BA §2.4，非本路）+
  **2×4000 步 TIMER2 推进** + 交错 handler（每 40k 步一次 Timer0 交付，~2-5 次）；
  前沿重钉行的 budget 按 BA #6 行「10⁴–10⁵」+8×10³ 微调。
- 验证：`check_local.py --units` 绿 + legacy 报告 stash 对照逐字节不变（默认关）。

## 6. HANDOFF 条目草案（合并时贴入「已实现」节，照格式）[设计]

> - **TIMER2 完成投影+事件时钟接入设计（本次 2026-11-14 11:27，路 BK/sim-timer2，
>   基线 54e738c；只读设计——零 tools/ 改动、零场景运行，主交付
>   [docs/timer2-eventclock-design.md](docs/timer2-eventclock-design.md)）**：
>   BA §5.2 P4 预案的开工规格。①**域全图**：JL_TIMER2=0xfd0600 族（CON/CNT/PRD，
>   br28.h L651-665）CON 位布局=Timer0 家族（mode1/div/src3=LRC/W1C14/PND15）；
>   0x12cd26 全 26 指令钉死（PRD=[0x129D88]×us/1e6 下限 2；**忙等环
>   0x12cd76 idle→0x12cd78 读→0x12cd7c 测 bit15 无界**；退出 W1C+CON=0）；调用面
>   =0x60685fe 两调用点（慢路 2 事务/快路 1）；ROM 孪生 0xa778 复核（同族习语、
>   OSC/÷64 源不同、启动即返）。②**两个新钉**：(a) BA 的 0xFFF130 系笔误，实为
>   **(cnum<<11)+0xFFE1B0**，SDK csfr.h 该槽=**IWKUP_NUM**（IPMASK+8）、写值
>   6=IRQ_TIME2_IDX 与 idle 前写互证——建模 write-only 记录；(b) **idle_wait
>   投影下 poll-idle 环的读指令永不可达**（交付→RTI 回 idle PC→再停泊）——BA P4
>   行「靠 Timer0 唤醒后读回 bit15」的字面读法不成立。③**设计 W1**：真计数器
>   （LRC 名义 200kHz 分数累积，Timer0 STD12M 同构）+pending 置位+CON 读回
>   bit15+**窄域完成唤醒**（kick 先例：锚 0x12cd76、pc+=2、无向量/RETI/ICFG；
>   硅机制疑 IRQ6+IWKUP_NUM 但 ICU 无证据故不建交付，trace 显示 IRQ6 使能可升级）；
>   否决 W3 首读即完成（丢 4000 步语义且解决不了 idle 不可达）。④**事件时钟**：
>   1ms=200 LRC tick=恰 4000 模型步（同一全局步轴，80 参考 tick/LRC tick÷4/步
>   闭式）；timer2_wake_horizon 并入 min()（仅 TIMER2 计数的世界不再误停）；
>   同刻序=Timer0 后、软件 pending 前；W4[4] 退出 CON=0→D1 窗口零 TIMER2 事件、
>   D1-3 闭式零影响。⑤**registry**：`timer2_delay_projection` 进 conditional
>   （requires idle_wait+event_clock；事件时钟首个消费者前移证据=W2[0] os_time_dly
>   BA#2，TIMER2=#6 是首个外设完成门控全核 IDLE）；实施规格=模型/机器/接线/7 单测
>   +场景行清单。未改 tools/、未跑改动场景；修正 BA 三处（0xFFF130→0xFFE1B0、
>   0x6068630 掩码 0xFEFF→0xFF1F、「每路径 1-2 次」→慢 2/快 1）。

## 7. 未钉死处（如实标注）

1. **R1=IRQ6 ICU 使能状态**：W4[4] 时刻 0xffe100 配置字 nibble 6 是否使能——
   运行观测项；若使能且合格，W1 唤醒件可升级为真交付（向量槽 0x19fe18 内容/
   入口三件套另需证据），计数器/读回/闭式零改动。
2. **IWKUP_NUM 语义**：SDK 布局具名+值互证是 [SDK推断] 级；硅效果（是否真是
   idle 唤醒选择器、与 ICU/GIEMASK 关系）未测量；0xFFE1B0 在实际硅布局中的对齐
   按「canonical IPMASK 0xFFE1A8+8」读法（banked 0xFFE168 视图下同槽）。
3. **LRC 真实频率**：名义 200000（AL 恒等式推导）；trim 后逐片差异未测量——模型
   按写下的 PRD 计数故自洽（§3.1），「步数=物理时间」换算永远标 derived。
4. **快路事务数**：VM99 校准世界（快路）只有 1 个 TIMER2 事务；若未来造 VM=7
   fixture，场景期望值按 1 调整（§1.4）。
5. **同刻序位置**：TIMER2 完成唤醒排在 Timer0 后是 [设计] 决策（可复现性优先），
   无硅证据；单测钉死后如需调整是显式变更。
6. **0x12e29c 自旋延时（trim 环内）**：纯自旋无 SFR（BA §2.4），其步数耗量
   ~10⁴/轮属前沿推进路的 budget 项，不属本投影面。
7. **单核停泊+另一核在跑的 TIMER2 到期**：W4[4] 世界不会出现（CPU1 已停泊）；
   §5.2 的 after_step 通用检查是防御性接线，语义面（唤醒停泊核是否被允许与运行核
   并发）未验证——如首跑撞上按实记录。
8. **W2[0] os_time_dly 的唤醒机制归属**（core tick vs Timer0 tick 谁减
   h[TCB+6]）：BI/BF 域，本文只引用 BA #2 行的顺序事实（事件时钟首个消费者），
   不展开。

## 8. 对既有记录的修正清单

| 记录处 | 原记载 | 本路修正 |
|---|---|---|
| BA §2.3/§3.1 W4 行/§5.2 P4/§9.4 | 特殊写 `[(cnum<<11)+0xFFF130]=6`「无名特殊写，疑 ICFG 槽族」 | **`(cnum<<11)+0xFFE1B0`**（r2=0x3FF86C，raw `6c f8 3f`，0xFFF130>>2≠0x3FF86C）；具名读法 IWKUP_NUM=6=IRQ_TIME2_IDX（[SDK推断]，仍非硅测量） |
| BA §2.2/§7 | 0x6068630 `[0xf2c10c]&=0xFEFF\|0x60` | 掩码实为 **0xFF1F**（codec 直读 65311；净效果差=bit7 保留与否，旧值 bit7 未观测，两读法本路径结果同——按 0xFF1F 更正记录） |
| BA §2.2 | 0x60685fe「每路径 1-2 次」 | 精确化：**慢路恰 2（0x6068778(13,0)+0x60687f6）、快路恰 1（0x60687f6）**（call 偏移验算） |
| BA §2.3 | helper 体「0x12cd2a..0x12cd7e」 | 全函数 **0x12cd26..0x12cd89**（含退出 W1C/停表/pop；下一函数 0x12cd8a） |
| BA §2.3「1 LRC tick=60 STD12M 步」 | STD12M-tick 口径 | 补模型步口径：60 STD12M tick=80 参考 tick=**20 模型步**/LRC tick（两口径同一数，避免实施时误乘） |

## 9. 红线核对（本路自查）

- 设计不实施：未改 tools/ 任何文件、未跑任何改动场景（本文件为 docs/ 新增，
  git 状态可查）；
- 静态结论全部 derived-not-measured；投影语义（§2.2 六条）为显式假设文本，
  实施路原样进报告键；不发明硬件时序（完成时刻=事件时钟计数到达，非预设延时；
  唤醒件=kick 先例的窄域投影并旗标 wake_source_unverified）；
- 锚点全部标出处（本路反汇编/BA/BO/BP/BG/BH/event-clock/HANDOFF 路 AH/AL/BQ/
  SDK 头 sha 钉死值）；未钉死值（IRQ6 使能、IWKUP 硅效果、LRC 真率、同刻序）
  标运行观测项/[SDK推断]/[设计]；
- 提交不含 Co-Authored-By、不含私有路径/私有工件名；`check_local.py --units` 绿
  （文档路 trivially，已跑）。
