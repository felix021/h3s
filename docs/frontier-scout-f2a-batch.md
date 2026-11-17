# 前沿侦察：0xf2a610 放行后的下批未建模 SFR 停点预判（路 SCOUT / sim-scout，只读静态调研）

2026-11-17。基线 main `2712dcb`（前沿 **41,848,382 @ 0x6019508** = helper
0x6019502 首条 `rmw.and.nmodimm [0xf2a610]` 的 RMW 内部读，`unmapped_memory`）。
本页是「攒批放行」的静态预研：预判 0xf2a610 放行后的下一批未建模 SFR 停点，
逐地址给 SDK 身份、读值去向（消费链审计）与三选一预案。**纯静态反汇编，未跑
任何仿真**——实际停点顺序以 sim-p2mclk 推进为准；本路不改 tools/ 任何代码。

**方法与证据等级。** 反汇编双层：①既有 vendor objdump 线性反汇编
（`~/tmp/h3s-eqcmd/app-linear.asm`，app.bin sha `18f3d573…`，load base
`0x06000100`，与本仓 `tools/disassemble_app.py` 产物同源）；②仓库钉死
pi32v2-isa codec（`probe_pi32v2` 同一 PIN 的 `codec.decode`/`check.operands`）
做流式重解——线性扫描在 tbb/tbh 跳表区会产生表数据伪指令（本路实测撞上一处，
见 §8 勘误②），流式重解以指令长度+表区间跳过来规避。call 边按「指令尾+偏移」
验算（与 s19-final-specs 同口径），用路 BO 已钉死的锚点（0x600a590 五调用点、
0x60116d0 五调用点）自校验通过。RAM-code 从 flash 源窗重解码
（RAM_CODE `0x12a640←0x060c3e18` 既有钉死表）。SDK 头交叉：br28.h（sha
`111e29ea…`）、p11_sfr.h / p11_app.h / io_omap.h / io_imap.h（pinned commit
`67328f4`，本路按 data/dependencies.json 的 sha 逐文件核验后取自参考仓）。
**[静态·本路]** = 本路指令锚定新增钉死；**[文档]** = 已合并 docs/HANDOFF 条目；
**[SDK]** = 参考头；**[在途]** = sim-p2mclk 路未合并工作。全部静态结论
derived-not-measured；一切 fixture 值 synthetic-not-measured。

## 0. 一句话结论

**0xf2a610 放行后的下一个未建模 SFR 停点几乎必然落在 JL_PLED 配置字族**
（CON1/CON2/BRI_×6/PRD_DIVL/DUTY0-3 共 **13 字**，全部在 LED 效果机
0x601a206 的紧后执行流里），**全部是「读值只进写回组装」的配置写/RMW 消费者**
——CON3 式容器一次批放行即可整族通过；批内静态面**没有已知的非 PLED 下一停点**
（CON0/CON3/JL_IMAP/USB_IO 等邻面全部已建模或免停认证，§7），放行后预期 LED
效果机全链走通、返回 S19 消息环。最可能的首停按分支条件排：**0x601a3ce 写
[0xfd6134]=0xFF（DUTY2）**＞**0x6019564 写 [0xfd6128]（PRD_DIVL）**＞
**0x60195f8 写 [0xfd6104]（CON1）**——三者由 LED 效果配置表的三个字节决定
（§2 分支树，静态不可解，运行观测项）。

## 1. 前沿定位与释放形状（含一处掩码勘误）

- **0xf2a610 = P11_SYSTEM->P2M_CLK_CON0**（p11_sfr.h `P11_SYSTEM_TypeDef`
  +0x10，基 0xf2a600；s19-final-specs §3.1 勘误③已钉名）[文档]。SDK 语义
  **`LED_CLK_SEL(x)`**（p11_app.h L117）：
  `P2M_CLK_CON0 = ((P2M_CLK_CON0 & ~0xe0) | x << 5)` —— helper 0x6019502 全文
  [静态·本路，codec 流式钉死]：

```
0x6019502: li.u32 r0 = 0xF2A610
0x6019508: rmw.and.nmodimm [r0+0] &= 0xFFFFFF1F    ; 清 bits5-7（=SDK &~0xe0）
0x601950c: rmw.or.modimm  [r0+0] |= 0x60           ; bits5-6=3 → LED_CLK_SEL(3)
0x6019510: li.u32 r0 = 0x12603C ; b[r0]=1          ; RAM 标志「PLED 时钟已配」
0x601951a: rts
```

- **勘误①（对 HANDOFF 前沿表/任务文）**：掩码是 **0xFFFFFF1F**（~0xE0），不是
  0xFFFFFFE0。「清 bits5-7」的文字正确；掩码数值是转写笔误（vendor objdump、
  钉定 codec、SDK 宏三方一致）。
- **全 app 唯一消费者**：0xf2a610 的 32 位字面量全 app 恰 1 处（0x6019502，
  本路线性全扫）[静态·本路]；无 GET_LED_CLK_SEL 读者、无计算寻址读者（邻字
  0xf2a608+0x10=0xf2a618 是喂狗/pmu 域，不落 0xf2a610）。读值去向：**只进
  old&mask|field 写回组装，零分支零轮询**——CON3 式单字容器的标准适用面。
- **释放形状（[在途] sim-p2mclk，codex 参谋 2026-11-17 已裁）**：选 (a)
  `P2MClkCon0Registers` 单字容器（合成初始 0 + RMW 内部读服务 + 两次写记录 +
  **显式读具名拒绝** `p2m_clk_con0_read_not_modeled`），开关
  `p2m_clk_con0_config_rmw`。本页预判以此落地为前提。

## 2. 释放后的立即执行流：LED 效果机 0x601a206 的分支树

helper 的活跃调用者是 **0x601a30c（函数 0x601a206）**——另一调用点 0x6019fda
（函数 0x6019d70）在其函数体内先调 helper 后调 LED 驱动，与「LED 驱动
0x60194a4 返回 +576 步到 0x6019508」的实测序矛盾，排除 [静态·本路]。0x601a206
是被 spawn 的**递归效果步进机**（0x601a1c2/0x601a1da 以 fn 指针 0x601a206 建
任务；函数尾 0x601a588 尾递归自身、0x601a56e 递归 0x601a590），从 S18 (18,0)
深层路径 0x60237ea→0x601a590→0x601a6d4 进入 [静态·本路 + 文档 d1-s18-destinations]。

helper 返回后的执行流（指令锚点全钉 [静态·本路]）：

```
0x601a310: b[0x1265F0] |= 1            ; RAM（QCY+48，"clk 已配"标志字节）
0x601a31c: goto → 0x601a386
0x601a386: r0 = 0
0x601a388: call 0x6019a8e              ; PLED 使能：读 b[0x12603C](=1)→读 CON0
                                       ; [0xfd6100]→&~0xF0|r1→写回——全已建模 ✓
0x601a38c: r3 = 0xFD6100               ; JL_PLED 基址
0x601a392: if ((r14 & 0xFF) == 0) goto 0x601a3e0   ; r14 = 效果配置字节 b[entry+9]
```

配置字节来自 RAM 效果表（0x1265C0 基指针索引，运行态值，静态不可解）。
三分支（byte5/6/7 = b[entry+8/9/10]，pack>>40/>>48/>>56 提取）：

| 路 | 条件 | 流向 | 路上首个未建模访问 |
|---|---|---|---|
| **A1** | byte6≠0 且状态字==2 | 0x601a398..c6 RAM 检查 → **0x601a3ce/d0/d2/d6/da** | **[0xfd6134]=0xFF 写 @0x601a3ce（DUTY2）** |
| **A2** | byte6≠0 且状态字≠2 | 0x601a45e pend → 数学 → **call 0x6019c5e**（呼吸建立） | **[0xfd6128] 写 @0x6019564（PRD_DIVL，0x6019552 内）** |
| **B1** | byte6==0 且 (byte5\|byte7)≠(0,1) | 0x601a4b2 → 子路 B1a（调 0x6019a86/0x601a1c2，全已建模）或 B1b（CON0 RMW×2 ✓ + 2×0x6019a1a→0x60196e8 恒 case32→0x6084eac，USB_IO 已建模 ✓） | **无（免停，§7）** |
| **B2** | (byte5,byte6,byte7)=(0,0,1) | 0x601a3f0..（可经 0x60194a4 ✓）→ **call 0x60195e2**（全字重配） | **[0xfd6104] 写 @0x60195f8（CON1）** |

B1 免停后走函数尾 tbb [r10]（效果步切换）：0x601a54e（门控+可递归 0x601a590）/
0x601a572（计数复位）/0x601a57e（spawn 0x601a1da）/0x601a588（**尾递归
0x601a206=下一步**）/0x601a58c（返回 0x601a590→S18 handler→S19 环）。即效果机
每步重走 §2 分支树，A1/A2/B2 三条首停路在步进序列中按配置数据先后出现——
**首个停点必属 JL_PLED 配置字族，具体哪字以运行态为准**。

步数量级：释放→A1 首停 ≈10-20 条指令（~10² 模型步）；→A2 首停 ≈40-60 条
（含 0x6019a8e×2 + 0x12d010 getter×2）；→B2 首停 ≈30 条。批放行后到
park/回环的总步数=效果表数据驱动，静态不可定。

## 3. 预判停点清单（核心交付，按预计撞点顺序）

「预计撞点顺序」= 同一路径内的指令序 × 三条分支路的首停排位；跨路由运行态
配置字节定（§2）。全部 **[静态·本路]** 逐指令钉死。

### 批 A：JL_PLED 配置字族（主批，13 字，强烈预期最先撞）

| # | 地址 | SDK 名（br28.h JL_PLED_TypeDef） | 本域全部访问点 | 形状 | 读值去向 | 三选一预案 |
|---|---|---|---|---|---|---|
| A-1 | **0xfd6134** | DUTY2（+0x34，__RW） | 写 0xFF @0x601a3ce；写 @0x60195d4（0x6019574 内） | 整字写 | **无读者**（全 app 零显式 load） | (a) CON3 式容器¹ |
| A-2 | **0xfd6138** | DUTY3（+0x38，__RW） | 写 0xFF @0x601a3d0；写 @0x60195ae（0x6019574 内） | 整字写 | 无读者 | (a) |
| A-3 | **0xfd6104** | CON1（+0x04，__RW） | RMW `&=~8`/`\|=0x80`/`\|=0x40` @0x601a3d2..da；显式 ld+写回 @0x6019588..ac、0x60195b6..be、0x60195c6..ce、0x60195d6..de（0x6019574 内）；ld+组装+写 @0x6019afa..06（0x6019af2 内）；整字写 @0x60195f8（0x60195e2 内） | RMW+ld/st+整字写混合 | **只进写回组装**（r5=[CON1]→\|=0x80→&=~0x60→写回；bit4/5/6 门控分支在**实参** r1/r2/r3 上，非读值） | (a) |
| A-4 | **0xfd6128** | PRD_DIVL（+0x28，__RW） | 写 @0x6019564（0x6019552 内，值=min(arg,4095) 低字节） | 整字写 | 无读者 | (a) |
| A-5 | **0xfd6110** | BRI_PRDL（+0x10，__RW） | 写 @0x6019ac2（0x6019ab0 内，基址 li 0x6019abc） | 整字写 | 无读者 | (a) |
| A-6 | **0xfd6114** | BRI_PRDH（+0x14） | 写 @0x6019ac6 | 整字写 | 无读者 | (a) |
| A-7 | **0xfd6118** | BRI_DUTY0L（+0x18） | 写 @0x6019aea | 整字写 | 无读者 | (a) |
| A-8 | **0xfd611c** | BRI_DUTY0H（+0x1c） | 写 @0x6019aee | 整字写 | 无读者 | (a) |
| A-9 | **0xfd6120** | BRI_DUTY1L（+0x20） | 写 @0x6019ad6 | 整字写 | 无读者 | (a) |
| A-10 | **0xfd6124** | BRI_DUTY1H（+0x24） | 写 @0x6019ada | 整字写 | 无读者 | (a) |
| A-11 | **0xfd6108** | CON2（+0x08，__RW） | 整字写 @0x60195fa（0x60195e2 内）；ld→insert(bits4-7)→写 @0x6019b0c..12（0x6019af2 内） | 整字写+ld/st | 只进写回组装（insert 字段来自实参 r1） | (a) |
| A-12 | **0xfd612c** | DUTY0（+0x2c，__RW） | 写 @0x60195b4（0x6019574 内，实参≠0 才写） | 整字写 | 无读者 | (a) |
| A-13 | **0xfd6130** | DUTY1（+0x30，__RW） | 写 @0x60195c4 | 整字写 | 无读者 | (a) |

¹ **(a) = CON3 式配置容器**（AM 族：合成初始字 0 + 读回=最后写值；RMW 内部读
服务、写记录；显式读可选「容器读回」或「具名拒绝」——本族 CON1/CON2 的显式
ld 读值全部只进写回组装，与 CON3 的 9 站点同证据类，**可开放读回**；BRI/
PRD/DUTY 全 app 零显式读者，读回语义惰性）。**(b) CON0 式具名整字初态
fixture：不需要**——13 字无任何分支/查表消费者（对照 CON0 的 bit0/bits4-7），
零值只作 synthetic-not-measured 探路值。(c) 逐字诚实停：否决——13 字同族同
证据类，逐字放行要多轮 4×10⁷ 步全量重钉，纯流程损耗。**建议一开关一批**
（如 `jl_pled_config_words`），whitelist 恰 13 字；**不并入** CON0/CON3 两个
既有开关（BV/BN「不并旧容器改旧世界报告形状」纪律），依赖
`jl_pled_con0_initial_fixture`（同域层叠）。

**批 A 不含**（维持 unmapped，到达再议）：CNT_RD 0xfd613c（__RO；全 app 零
读者——CON3 模型注释记的 0x6034b7a 显式读是 [基址+12]=CON3 不是 CNT_RD）、
CON4 0xfd6140、CNT_SYNC 0xfd6144（__WO）、CNT_DEC 0xfd6148——LED 域零触达
[静态·本路]；与 remaining-dependency-map G1-边界「不把 0xfd6100..0xfd6148
整体 RW latch」的 codex 定调一致。

### 批 B：JL_IOMC OCH/ICH 读族（条件远场，6 字，不在立即路径）

| # | 地址 | SDK 名（br28.h JL_IOMC，基 0xfd6014） | 访问点 | 读值去向 | 预案 |
|---|---|---|---|---|---|
| B-1..3 | **0xfd6018/1c/20** | OCH_CON0/1/2（输出通道映射，5 位×16 槽） | 读 @0x601966e（0x6019634 内，槽搜索循环） | **进比较** `if (r0 != r4)`（0x6019678）——匹配才清 RAM 位图+调写侧 | **CON0 式具名 fixture**（读值选行为：0=空映射表，比较恒假→「无占用槽」臂）或需 live 值；CON3 式**不适用**（读值进分支） |
| B-4..6 | **0xfd6020/24/28/2c** | ICH_CON0..3（输入通道映射，4 位×32 槽；固件按 0-7/8-15/16-23/24-31 取 0xfd6020..2c 四字） | 读 @0x60196c2（0x6019692 内） | **进索引+分支**：nibble=\[ICH]>>shift&0xF→`if (nibble>13) skip`→IMAP 清除地址 0xfd5c00+(nibble<<2)→RAM 位图 | 同上（0 值→nibble 0→清 FI_GP_ICH0 臂） |

**触达条件（为什么是远场）**：0x6019634/0x6019692 只被 0x60196e8（引脚卸载
switch）的非 33 号 case 调用 [静态·本路：tbh 表 72 案例逐一解出]。LED 路上
唯一调用者 0x6019a1a **硬编码 r2=33**（0x6019a28 `li.u8 r2,33`）→恒 case32
→0x601992e→call 0x6084eac（USB_IO，已建模）；其余调用者在 debug/emu 域
（0x60342e2/0x6034312/0x603437a/0x6034396）。另有 GPIO resolver
0x6001356/0x60013ce（OCH/ICH 槽位搜索-占用）经 0x600180a/0x6001820 调用——
boot 前缀已穿 uart_init 未停，说明该域读点未执行（输入侧走了 0x609EAB8 查表
臂 [文档：路 V]）。**若运行态意外触达，按 CON0 式 fixture 三选一，勿用容器**。

### 批 C：P11_SYSTEM 其余成员（G2 运行态远场，登记不建模）

全 app 字面量扫描 [静态·本路]：0xf2a604（P2M_INT_SET）/0xf2a61c（M2P_INT_CLR）/
0xf2a620（M2P_INT_PND）**零用户**；其余成员用户全不在 LED→S19 立即路径：

| 地址 | SDK 名 | 用户（函数域） | 状态 |
|---|---|---|---|
| 0xf2a600 | P2M_INT_IE | 0x60691fc（pmu_task 体）、RAM 0x60c684a（喂狗族） | pmu 事件驱动（G2）；喂狗族形状已被 p2m_mailbox_ack/wdt_mailbox_feed 域覆盖 |
| 0xf2a608/0c/618 | P2M_INT_CLR/PND、M2P_INT_SET | RAM 0x12cc7e 喂狗（**已建模** ✓）、0x6068c82/0x606905c（pmu_task kick 0x800）、0x602f45e（IRQ68 域 kick 0x80+b[0xF27F88]） | 喂狗面在跑；pmu/IRQ68 kick 到达时按 s19-final-specs §3.3 既有 idiom 登记 |
| 0xf2a62c | MEM_PWR_CON | 0x60676ec/0x60677a4（power_init，已执行）、0x6067e48（充电树 S13 门后） | 不在立即路径 |

## 4. 消费链审计摘录（批 A 关键站点，钉定 codec 流式重解）

**CON1 的「读值只进写回组装」证据**（0x6019574，DUTY 配置器；分支全在实参上）：

```
0x601957c: if (r0 == 255) goto …        ; 实参分支（duty 满值特例），非读值
0x6019584: [r4+0] &= ~0x8               ; CON1 RMW（内部读→写回）
0x6019588: r5 = [r4+0]                  ; CON1 显式读
0x601958a: r5 |= 0x80 ; … 0x60195aa: r5 &= ~0x60 ; 0x60195ac: [r4+0] = r5
                                        ; 读值仅经 或/与 后写回，零分支
0x60195b0: if (r1 != 0) { [r4+40]=r1 ; r0=[r4+0] ; r0|=0x10 } else { … &=~0x10 }
                                        ; 门控在实参 r1；读值只组装 bit4
```

**CON2 的 insert 消费**（0x6019af2）：`0x6019b0c r0=[r2+4]` → `insert r0,
r1, p:4, l:4`（字段来自实参 r1）→ `0x6019b12 [r2+4]=r0`——读值只承载保留位。

**DUTY2/DUTY3 满值写**（0x601a206 路 A1，紧跟状态字==2 判定）：

```
0x601a3c6: r0 = b[r5+0] (u)             ; 状态字（RAM）
0x601a3c8: if (r0 != 2) goto 0x601a45e  ; ≠2 走 A2 呼吸建立
0x601a3cc: r0 = 255
0x601a3ce: [r3+52] = r0                 ; ★[0xfd6134]=DUTY2=0xFF
0x601a3d0: [r3+56] = r0                 ; ★[0xfd6138]=DUTY3=0xFF
0x601a3d2: [r3+4] &= ~0x8               ; ★CON1 RMW（内部读）
0x601a3d6: [r3+4] |= 0x80 ; 0x601a3da: [r3+4] |= 0x40
0x601a3de: goto 效果尾 switch
```

**PRD_DIVL 首写**（0x6019552，呼吸分频设置；0x6019c5e 于 0x6019cc8 调入）：
`r0=min(arg,4095); r1=r0.b0; r2=0xFD610C; [r2+28]=r1`→**[0xfd6128]**，随后
CON3 `&=0xF`/`|=r0`（已建模 ✓）。

**BRI 六连写**（0x6019ab0 体）：`r3=0xFD6110`（0x6019abc）后
`[r3+0]/[r3+4]/[r3+16]/[r3+20]/[r3+8]/[r3+12]` 六个整字写（呼吸周期/占空高
低位），值全部来自实参计算，零读回。

## 5. 三选一裁决汇总与攒批建议

| 域 | (a) 容器 | (b) 具名 fixture | (c) live/诚实停 | 裁决 |
|---|---|---|---|---|
| 0xf2a610（在途，codex 已裁） | ✓ CON3 式单字（显式读拒绝） | 不需要 | 关=严格基线 | (a) [在途] |
| 批 A 13 字 | ✓ CON3 式一族一开关 | 不需要（无分支消费者） | 否决（流程损耗） | **(a) 一批 13 字** |
| 批 B OCH/ICH 6 字 | 不适用（读值进分支/索引） | ✓ 0=空表探路值 | 真需要映射真值时 | 远场到达再议，倾向 (b) |
| 批 C P11_SYSTEM 余员 | 喂狗族已建模；pmu/IRQ68 kick 到达按 idiom 扩 | — | — | 登记，G2 运行态 |

**攒批放行的直接输入**：批 A 一开关（13 字 whitelist + CON3 同族报告键），负例
=开关关停 0x601a3ce（或运行态首停字）；敏感性臂可选 CON1 bit7（save-restore
位）两态。前沿重钉一次到「批 A 放行后的下一停点」——静态面该停点已在 LED 域
之外（§7 免停面），大概率是回 S19 环后的 park/idle 观察面或 G2 任务域，归
sim-p2mclk 实测。

## 6. 与 prior work 交叉核对

| 记录处 | 已有内容 | 本路增量 |
|---|---|---|
| HANDOFF FD610C 条目②（codex 表） | 19 字 struct 全列「留 unmapped」 | 补上 13 字的**全访问点+读值去向+路径顺序**；确认 CNT_RD/CON4/CNT_SYNC/CNT_DEC 四字 LED 域零触达 |
| remaining-dependency-map G1/G1-边界/G2 | PLED 读侧三选一框架、「4 调用点归属未钉」、G2 前瞻 | 钉死活跃调用链 0x601a206←0x601a590←0x60237ea（S18 深层）；G2 候选 SFR 面补 P11_SYSTEM 余员明细 |
| appcore-tail-sfr-semantics §2-N2 要点3 | LED 驱动域 6 处 CON0 li | 扩展为全族 13 字 + IOMC 6 字 + 效果机调用图 |
| d1-s18-destinations / d1-s18-consumption | (18,0) 深层 0x601a5xx「未逐层命名」 | 补全 0x601a590..0x601a6da 门控+switch 与 0x601a206 效果机全图 |
| s19-final-specs §3.1 勘误③/§3.3 | 0xf2a610=P2M_CLK_CON0 真名、pmu/IRQ68 kick 登记 | LED_CLK_SEL(3) 语义锚（p11_app.h L117）+ 全 app 唯一消费者钉死 |
| s19-endgame-map N2 | 0xfd6100 记在电源框架清零写组 | 该组维持（充电树）；LED 域到达面是新路径（remaining-dependency-map §4 已预告） |
| [在途] sim-p2mclk + codex 202611172110 | 0xf2a610 容器设计 | 本页以其落地为前提，批 A 是其直接后继 |

## 7. 免停认证面（释放 0xf2a610 后不会停的，静态钉死）

- **CON0 0xfd6100 / CON3 0xfd610c**：两容器服务本域全部 20 站点（FD610C/
  FD610C-con0 已 merge；0x6019a8e/0x6019a60/0x6019a86/0x60195e2 头部/0x6019bb0/
  0x601a506..0a/0x601a086..0a0 全为 CON0/CON3 位操作）。
- **JL_IMAP 63 字**：0x60196e8 案例体的 `[rBase±off]=0` 整字清零写全部落在
  0xfd5c00..0xfd5cf8 白名单内（路 V 写记录）；**无 RMW/testset 消费**（§8 勘误②）。
- **USB_IO CON0 0xfd6000**：0x6084eac/0x6084f12/0x6084f7e/0x6084fe8/0x6085052/
  0x60850bc 的读/RMW/写由 port_initialization_projection 种子的全位已知
  PartialWord 服务（br28_model.py:7231）。
- **纯 RAM 面**：0x601a590 门控函数（0x1265C0 配置域）、S18 handler 链
  0x60237d8..0x6023b34（零 SFR 字面量）、0x12d010（=`[0x12A058]` 频率 getter）、
  0x6019b16/0x6019bc0 的数学段、pend/post 0x12b55c/0x12b5b0、任务创建
  0x6066a56/0x6066b88/0x6066c26 族、向量安装 0x6065d86（写 RAM 0x1A0080）。
- **IRQ41 注记**：0x6019d70 呼吸模式注册 IRQ41（0x601a094 `li r0,41`，handler
  参数 0x6034B06 域——内含 CON3 **显式读** 0x6034b7a，CON3 容器 read() 严格停）。
  模型无 IRQ41 源（LED 硬件不建模）→handler 零执行；若执行到=世界错向信号
  （canary 性质，不建模）。

## 8. 勘误（对既有记录与本路自查）

1. **HANDOFF 前沿表/FD610C-con0 条目**：「[0xf2a610]&=0xFFFFFFE0」→ 实际
   **0xFFFFFF1F**（~0xE0；objdump+codec+SDK 宏三方一致；「清 bits5-7」文字本就
   正确）。
2. **本路自查撤回**：vendor 线性反汇编把 0x6019774 tbh 跳表数据（表区
   0x6019776..0x6019806，72 案例半字）误译成指令——初扫曾得「0x60197a6
   testset b[0xfd5c38]（JL_IMAP RMW）」，流式重解确认**不存在** testset：真实
   case 体只有 mov/call/st.w/pop。JL_IMAP 维持路 V「读回/RMW 严格停」不变。
   （教训与 tail-map「对齐漂移伪迹」同款：跳表区必须流式跳过。）

## 9. 诚实边界与未知（如实标注）

1. **全部预判未经执行验证**——实际停点顺序以 sim-p2mclk 推进为准；A1/A2/B2
   三路由 LED 效果配置字节（RAM 表数据）决定，静态不可解，首停字=运行观测项。
2. **效果机步进深度未知**：尾递归/spawn 次数、何时返回 0x601a590/S19 环，由
   效果表数据驱动；「批 A 放行后 LED 域内无下一停点」是**静态面**结论——间接
   call（全 app 1189 处 `call rN`）与调度切换后的其他 ready 任务（G2：jlstream/
   pmu_task/dev_mg/usb_stack，remaining-dependency-map §3 已列）不在此保证内。
3. **BRI/PRD/DUTY 写值的硬件效果**（呼吸波形、亮度）不建模——容器只记录配置
   写已执行，同 CON3 的 known_limitation 措辞。
4. **批 B 的 ICH 四字边界**：固件按 0xfd6020..0xfd602c 取输入映射，而 br28.h
   结构体 ICH_CON0 名义基是 0xfd6024——差一字（固件通道 0-7 读的字按 SDK 名是
   OCH_CON2）。远场建模时以固件实际访问为准、名字按结构体偏移标注，未强行
   裁决。
5. **本路工作区残留**：一次性脚本与取回的 SDK 头在 `~/tmp/frontier-scout/`
   （不进仓）；本仓新增本文档与只读清单
   [evidence/sim-routes/frontier-scout-f2a-batch.json](../evidence/sim-routes/frontier-scout-f2a-batch.json)
   （停点表/免停面/勘误的机器可读版）。
