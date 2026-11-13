# W1[0] 之后到两核 park 的剩余边界证据包（路 CC / sim-clkfamily，只读预研）

2026-11-13。本页服务 CB（P3_ANA_READ 读侧）之后的 1–2 路：把 447,383 停点之后
app_core 尾段剩余的新边界逐地址钉死（SDK 归属、访问形状、投影状态、三选一倾向），
并给出 BY 世界（PND 折叠 + RES 具名档位）下 S13–S18 条件段的可达性结论与 park 前
逐边界预测清单。**只读**：未改 tools/ 任何代码、未跑任何 probe（analysis/ 报告只读；
反汇编用主仓钉死 codec，与探针同源；SDK 参考证据来自既有 reference-cache 与
本地 SDK 对象 IR dump）。

证据强度：**[静态]** = 指令锚定反汇编（本路，li 基址正向重解 + call 目标按
branch_field×scale 精确计算）；**[SDK]** = 参考 SDK 头/驱动 IR（参考材料非精确
QCY 源）；**[采样]** = analysis/local-checks/irq-entry.json BY 世界报告实测；
**[文档]** = 既有 docs/HANDOFF 钉死条目。结论基于 main `7fcd45e`。

## 1. JL 时钟族 0xfd0008/0c/10：归属、访问面、既有模型核对、三选一

### 1.1 SDK 归属 [SDK]

br28.h：`JL_CLOCK_BASE = ls_base + map_adr(0x00,0x01) = 0xfd0004`，
`JL_CLOCK_TypeDef` 字段全部 `__RW`：

| 地址 | SDK 名 | 属性 |
|---|---|---|
| 0xfd0004 | PWR_CON | __RW |
| **0xfd0008** | **DCCK_CON0** | __RW |
| **0xfd000c** | **DCCK_CON1** | __RW |
| **0xfd0010** | **DCCK_CON2** | __RW |
| 0xfd0014 | SYS_DIV | __RW |
| 0xfd0018/1c/20/24 | CLK_CON0/1/2/3 | __RW |

0xfd0000 本身是 `JL_RST->RST_SRC`（另一结构）。BO §1 表漏记了 JL_CLOCK 基址
推导（只列了 CHIP_ID 等），本节补齐。

**SDK 驱动侧读回证据 [SDK]**：power_clock.c IR（对象缓存里的
`hw_clock_enter_deepsleep`/`hw_clock_exit_deepsleep`）把 27 个时钟 SFR 整组
save/restore 到 `clk_sfr[27]`：CLK_CON0..3、SYS_DIV、PWR_CON、**DCCK_CON0/1/2**、
PLL0×7、PLL1×7、WLA_CON8/9/10/16——即 SDK 自己把 DCCK 三字当**真实可读回、可
恢复**的寄存器（deep-sleep 域，不在本前缀）。clock.c 本体不碰 DCCK（只 dump
SYS_DIV/CLK_CON）。

### 1.2 0x6068152 全访问面 [静态]

mode 切换函数 0x6068152..0x606829c，入口双门：`b[0x129A30]`（上次 mode，
BSS 起 0，函数头成功路径会写它）== 入参 → 整体跳过；入参 ∉{0,1} → 跳过。
**时钟族只在 mode 0/1 且与上次不同时触达**（见 §4 预测分支）。调用面 [静态]：
全 app 直接调用 4 处——0x601318e（**W1[0] 尾，唯一顺序路径调用**）、
0x6012c44/0x6012c50（音频任务层 0x6012bfe 内，D1 后）、0x60211d0。

函数体时钟族访问（执行序）[静态]：

| 站点 | 地址 | 形状 |
|---|---|---|
| 0x606817a | 0xfd0008 | 原子 `rmw.or.modimm \|=0x4` |
| 0x606817e–86 | 0xfd0008 | 显式 ld/st RMW：`&0xFFFFFFE7 \| 8`（bits3–5←1） |
| 0x6068188 | 0xfd0008 | 原子 `&=0xFFFFFF3F`（bits6–7←0） |
| 0x606818c–96 | 0xfd0008 | 显式 ld/st RMW：`&0xFFFFF0FF \| 0xE00`（bits8–11←0xE） |
| 0x6068198 | 0xfd0008 | 原子 `&=0xFFFFCFFF`（bits12–15←0） |
| 0x606819c–a4 | 0xfd0008 | 显式 ld/st RMW：`(old&0xFFFF) \| 0x46000000`（高半←0x4600） |
| 0x60681a6–b2 | 0xfd000c | 显式 ld/st RMW：`&0xFFFF0000 \| 0x100`（低半←0x0100） |
| 0x60681b4–bc | 0xfd000c | 显式 ld/st RMW：`(old&0xFFFF) \| 0xFF000000`（高半←0xFF00） |
| 0x60681be | 0xfd0010 | 整字写 `=0x10000`（唯一访问） |
| 0x60681ca | 0xfd0008 | 原子 `&=0xFFFFFFFC`（清 bits0–1，时钟族收尾） |

要点：**显式 ld.w 真读参与写回**（0xfd0008×3 组、0xfd000c×2 组）——不是纯
write-only 记录能覆盖的形状；同时原子 RMW（rmw.or.modimm/rmw.and.nmodimm）也在场。
读写形状与 BV 尾段容器（P3_ANA_CON0/7..10 的 ld/st RMW+复合 RMW）完全同族。
非时钟族的其余访问面全部已建模：0xfd1920 WLA_CON8 `\|=0x8000000`（OSC 族既有
[采样]：BY 世界末值 0x3e0c4191）、P3_ANA_CON0/CON7/8/9/10 配置 RMW（BV
`p33_analog_tail_projection` 容器）、call 0x12b55c/0x12b5b0（OS 临界区）、
call 0x12cd04（纯软件倒计数延时循环，b[0x129A51] 乘子，无 SFR）[静态]。

**全 app/ROM 字面量扫描 [静态]**：0xfd0008 恰 1 处 li（0x6068176，即本函数）；
0xfd000c/0xfd0010 零字面量（经 [r0+4]/[r0+8] 派生）。条件侧 0x6067f84 的
0x6068042..56 两访问实为 **0xfd0018**（CLK_CON0 `\|=1`）与 **0xfd001c**
（CLK_CON1 bits8–9 RMW，BV §8 修正一）——都落在基模型既有 registers 槽（见
1.3），且该树本世界不可达（§2）。

### 1.3 既有模型核对：JL_CLOCK 块只有 DCCK 三字是洞 [采样+文档]

BY 世界（p11_heap_experimental_config） peripherals.registers 末值：

| 地址 | 模型状态 | 前缀末值 |
|---|---|---|
| 0xfd0000 RST_SRC | reset_reason_inputs（复位历史域） | — |
| 0xfd0004 PWR_CON | RESET_FLAGS（复位历史域） | — |
| **0xfd0008/0c/10 DCCK_CON0/1/2** | **unmapped——首次访问诚实停** | — |
| 0xfd0014 SYS_DIV | `clock_divider_projection`（additional_configuration） | 0x100 |
| 0xfd0018 CLK_CON0 | 基模型 registers 槽（CLOCK0） | 0x220 |
| 0xfd001c CLK_CON1 | 同上（CLOCK0+4） | 0x645 |
| 0xfd0020 CLK_CON2 | 同上（CLOCK0+8） | 0x0 |

即早期时钟初始化（0x6000244..0x600071c 域，~110k 步前已执行）已把 SYS_DIV/
CLK_CON0/1/2 写进模型；AH 的 TIMER0 STD12M（0xfd0400 TIMER0 自己的模型）与
OSC 0xfd1920 族（oscillator_configuration_projection）也都是既有。**JL_CLOCK
块内唯一未建模的就是 DCCK_CON0/1/2 三字**，tail-map 把它们归进「26 个
family-adjacent」的 SYS-div 族桶（"0xfd0008/000c/0010/001c"），但从未有投影
接住它们。

### 1.4 三选一倾向：投影（AM 容器同款「合成初始字 0 + 读回=当前值」）

- **倾向 (a) 投影**：DCCK 三字进 `additional_configuration` + `registers` seed 0
  （新开关，如 `jl_clock_dcck_projection`，默认关 legacy 不变）。理由：
  ①纯数字配置寄存器，非模拟量——无 RES/P3_ANA_READ 式红线（红线是「模拟测量值
  无可推导来源」，这里读回值完全由固件自己前序 RMW 决定）；②SDK 头 __RW +
  deep-sleep save/restore 证明真实可读回 [SDK]；③唯一 app 消费者 0x6068152 的
  显式读全部立刻被掩码+OR 消化（seed 0 下确定性的位域配置序列），无独立状态
  消费；④先例密：AM（P3_ANA_CON2/5）、BV 尾段容器、BN 的 ANA_CON4 联动 seed
  全是同一语义。原子 RMW 走 probe_boot 通用 mmio 路径（读 seed 值→算→写回，
  `mmio_rmw_always_writes`），显式 ld/st 走正常读写——**机器侧零新分发**。
- **不倾向 (b) 钉住**：那是给模拟量红线的；DCCK 是配置面，钉住会把一个可推导
  的确定序列变成死停。
- **不倾向 (c) 具名 fixture**：复位值没有推导链，但也不需要——配置序列只依赖
  「写什么读什么」，合成 0 已是本仓对该族的标准显式假设（三层身份标注照做）。

一个如实标注的残余未知：DCCK 真实复位值未知（seed 0 是显式合成，非硅值）；
若真机日后测量可得，证据等级可升，不影响本前缀正确性（读回全被掩码消化）。

## 2. BY 世界 S13–S18 条件段可达性

输入假设（BY 世界，全部 [采样]/[文档]）：PND 完成投影折叠 80 次 caller A 轮询；
RES 具名档位 400/500/400（通道识别 P3_ANA_CON4[4:1]）→ 项0 u16=400、项1/项2
float 945.0/756.0（runtime K=756）→ **battery_mv = 4×945 = 3780mV**（mode=1
float 直读路径，BU §5 [疑]点已实测关闭）；config 99/523 缺失（充电/NTC 配置
不加载）。

| 段 | 门 | BY 世界走向 | 证据 |
|---|---|---|---|
| S13 0x60122aa | `b[r8+3464]`（0x127348，writer=事件机制 0x606487c）==2 → 充电查询 | **跳过**（==2 分支不进：启动前缀无充电事件，BSS 起 0；0x6067dda(12)/0x6067f84 充电树不触达） | [静态]+[文档] |
| S14 0x60122f6 | `0x6001cb0()`（=W1[0] 的 b[r8+628] 标志）非零 → 创建任务 0x600741c + goto S18 | **取决于 CB 的 P3_ANA_READ bit1**（见 §4 两分支）；两臂都无新 SFR | [静态] |
| S15 0x601232e | 11 轮电池门：`0x6011e20()`（纯 RAM 读）≥ h[0x127734] | **通过**：阈值=3200（编译默认，0x6011c1a 写入；BU 已修正 BH 的「静态零」），3780≥3200 每轮过 → **0 连败，K1 不可达** | [静态]+[文档]+[采样] |
| S16 0x601235a | `0x606859c()` = 0x6067dda(18)（RAM 位图查询）门控 10 次标志等待 0x6064efc(10) | 两臂（使能/禁用）都到 S18，无新 SFR；**使能臂是 park 前第一个自然的 pend-唤醒周期（BI 首验素材）** | [静态] |
| S17 0x6012458 | NTC 启动门：`b[0x1274F4]`（enable）非零才进 150 重试 | **跳过**：config 523 缺失 → enable=0 → **K2 不可达**（含第三出口 0x6012486 恢复分支） | [文档]（BU §3.5） |
| S18 0x60123a6 | post(18,0)/post(20,2) + b[r8+632]=1 | RAM only（BZ 已钉消费链：(18,0) handler 0x6023962 两级门控短闭合；(20,2) sys_timer 特路） | [文档]（BZ） |
| 充电窗 0x60686b2 | `getter(0x10006)<<2 − 3700 > 1300`（[3700,5000] 窗） | **不触达**：config 99 缺失在前置 0x606867c `if(r0!=7)` 跳过（3780 本身在窗内，双重不达） | [文档]（BU §3.3b） |
| CHIP_ID 0xfd0200 三读者 | 0x6068076（充电树内）/0x6069ab0（音频 init 域）/0x6024210（info getter） | **全部不达**：充电树§2 不可达；后两者全 app 直接调用+指针引用双扫描零命中（只可能间接到达，D1 后再议） | [静态] |
| 金丝雀树 0x12cc40 | S11 任务创建失败（ret!=0）才调用 | **跳过**：BY 世界任务创建均成功（ret==0 → beqz 跳过 0x6012290） | [静态]+[采样] |

**金丝雀结论复核**：BH 的「阈值静态零」表述已被 BU 修正（阈值=3200 编译内置）；
新世界结论不变质——K1 需要 11 连败 battery_mv<3200，BY 世界常量 3780 → 0 连败；
K2 整段被 NTC enable=0 旁路。watch-only 维持。

## 3. park 前逐边界预测清单（447,383 → 两核 park）

W1[0] 停点后的完整序列 [静态]（★=新边界，✓=已备投影，～=既有机制无需投影）：

| # | 地址/站点 | 内容 | 投影状态 |
|---|---|---|---|
| 0 | 0x6012f26 读 0xf2c064 | P3_ANA_READ 轮询（≤10 轮×1ms，**等 bit2 清零**；bit2=0 常量值一轮即出） | **CB 在途** |
| 1 | 0x6012f40..0x6012fb2 | 0xfd5708 PORTP 组第二块五位 RMW（cli/sti 包裹） | ✓ gpio 投影 |
| 2 | **★ 0x6012fc0 ld.w [0xf2c288]** | **P3_L5V_CON1**（P33_ACCESS(0xa2)）显式 ld/st RMW——**CB 之后第一个新停点**（BO/tail-map 双双漏记：r0=r5+544 派生基址，walker 常量传播丢弃） | 待做（§3.1） |
| 3 | **★ 0x6012fd6 ld.w [0xf2c284]** | **P3_L5V_CON0**（0xa1）显式 ld/st RMW（与 RAM 配置字节 b[r6+256+4/5] 合成） | 待做（§3.1） |
| 4 | 0x6012fe6..0x60130fc | P3_CHG_CON0（0xf2c068）RMW 族 | ✓ BV 容器 |
| 5 | 0x601305a..0x601310e | P3_CHG_CON1（0xf2c06c）ld/st RMW | ✓ BV 容器 |
| 6 | **★ 0x6013094 ld.w [0xf2c070]** | **P3_CHG_CON2**（P33_ACCESS(0x1c)，[r5+12]）显式 ld/st RMW——BV 模型注释误记为 [r5+8]（CHG_CON1），实为 CHG_CON2，容器未含 | 待做（§3.1） |
| 7 | 0x601311c/0x6013122 再读 0xf2c064 | P3_ANA_READ **bit1/bit2** 测试（bit1=充电在位门控任务创建参数；bit2 复测） | CB 语义顺带覆盖 |
| 8 | 0x6013170 call 0x6066c26(0x6033f36,0,2,1) | 任务创建（仅 bit1/bit2 命中臂）＋ h[0x125BF8]=ret；两臂都有 0x6013182 `0x6001cb0()` RAM getter | ～ IPI 机制 |
| 9 | **★ 0x606817a rmw.or [0xfd0008]** | **DCCK_CON0** 首访问 = 0x6068152 mode 体入口（**仅 mode∈{0,1} 且 ≠b[0x129A30] 时到达**，见 §4） | 待做（§1.4） |
| 10 | 0x60681a6/0x60681be | DCCK_CON1（ld/st×2）/DCCK_CON2（整字 0x10000） | 待做（同开关） |
| 11 | 0x60681c6 `\|=0x8000000` | WLA_CON8 OSC 族 | ✓ OSC 投影 |
| 12 | 0x60681d4..0x606828a | P3_ANA_CON0/CON7/8/9/10 配置 RMW + 0x12cd04 软件延时 | ✓ BV 容器 |
| 13 | W1[1..4]（0x601321e/0x6013246/0x602ea68/0x60614a2）、W2×11、W3、W4×6 | 22 个 walker 函数**零直接 SFR 访问**（路 BH accesses 全量核对） | ～ 无边界 |
| 14 | S11 0x601228e | 任务创建 0x6067242 成功（ret==0）→ **跳过 0x12cc40**（失败臂才进金丝雀树：P3_VLVD_CON/P3_PCNT_SET0 fixture 已备 + 0xf2c280 P3_PR_PWR 未备——不可达，不动） | ✓/watch |
| 15 | S12–S17 | §2 全部条件段（BY 世界：S13 跳、S14 按 CB 分支、S15 过、S16 两臂无新 SFR、S17 跳） | ～ |
| 16 | S18 0x60123a6 | 两条预置事件 post | ～ RAM |
| 17 | S19 0x60123e6 | 消息循环：先消费 (18,0)/(20,2)（BZ 钉死短闭合），随后 pend 空队列 | ～ D1 域 |
| 18 | park | CPU0 app_core pend 空队列 / CPU1 12c5ca idle → 全核 IDLE → **BI**（event_clock 真实前缀首验：Timer0 往返即可，G3 场景接线归 BI） | BI 域 |

### 3.1 新发现的三个 P33 地址（本路增量，BO/tail-map 均漏记）

| 地址 | SDK 名（p33_sfr.h，仅地址宏） | 访问面 [静态] |
|---|---|---|
| 0xf2c284 | P3_L5V_CON0（0xa1） | W1[0] 独占（0x6012fd6 ld / 0x6012fde st）；ROM 0xac20 侧 `\|=0x4`（5V 域，低功耗路径，不在前缀） |
| 0xf2c288 | P3_L5V_CON1（0xa2） | W1[0] 独占（0x6012fc0 ld / 0x6012fce st） |
| 0xf2c070 | P3_CHG_CON2（0x1c） | W1[0] 独占（0x6013094 ld / 0x601309e st，位测试臂内） |

三者与 BV 尾段容器七地址**同函数、同族、同形状**（P33 模拟配置 RMW）。
落地建议：扩 `P33_TAIL_ANALOG_CONFIG` 常量 7→10 地址（同一 `p33_analog_tail_projection`
开关）。BV 当年不把新地址并进 AM 常量是为了保「AM 开而 BV 关」世界报告形状；
本容器自己从未被任何场景报告跑过（BV 是纯模型+单测、前沿未动），扩它只改
开关开世界，无逐字节 parity 负担——但决定归实施路（CD），照抄 BV 的保守路线
（兄弟开关）也可。

### 3.2 时钟族不动硬期限

0x6068152 只写 DCCK（外设数字时钟域使能/分频）与 WLA_CON8，不碰 TIMER0
（0xfd0400）/core tick（0xffe0ec）的 CON/PRD——**Timer0 到期与 core tick 溢出
期限不受本段影响**（d1-gap-map「CON/PRD 再写会移期限」的告警不适用于 DCCK）。

## 4. 对 CB 之后的实施路线建议（1–2 路）

**路 CD（P33 尾段三地址 + JL 时钟族，一次打包）**：CB 落地后前沿撞 0x6012fc0
（L5V_CON1）——三地址与时钟族 DCCK 是同一次「W1[0] 剩余面收尾」，建议一路做完：

1. P33 三地址：§3.1 容器扩展（或兄弟开关），AM 语义（合成 0+读回当前值）。
2. DCCK 三字：§1.4 新开关 `jl_clock_dcck_projection`，additional_configuration +
   registers seed 0，机器侧零新分发（原子 RMW 走通用 mmio 路径）。
3. 两个预测分支（CB 的 P3_ANA_READ 值决定，实施路只需接住不选边）：
   - **bit1=0（电池供电、无充电器，与 RES 档位的设备静置状态一致）**：20×1ms
     延时 → b[0x125BF4]=2 → 标志=0 → S14 跳过任务创建 → S15 电池门（3780 过）→
     mode=1 → **0x6068152 体执行 → DCCK 全序触达**。
   - **bit1=1（充电在位）**：任务 0x6033f36 创建 → 标志=1 → mode=0 → 若
     b[0x129A30]=0（BSS 初值，无前置写者证据）→ **0x6068152 体整体跳过，DCCK
     不可达**，前沿直落 W1[1..4]（无边界）→ S11。
   - 观测点：0x606815e（`beq.rr` 去抖门）。b[0x129A30] 的前置写者无法静态排除
     （0x1299D0 电源结构 26 处引用），首跑即知。
4. 单测照 BV 口径（形状/开关关停/世界隔离），回归 `check_local.py --rom --jobs 6`。

**路 BI 接力**：CD 之后顺序路径再无新 SFR 边界（§3 清单 13–18 全为既有机制/
RAM/D1 域）——前沿将自然滑到 S19 消息循环 pend 空队列，即 **BI 的入口条件**
（event_clock_projection 真实前缀首验 + G3 场景接线，Timer0 往返即可）。S16
使能臂的 0x6064efc(10) 等待是 park 前第一个自然 pend-唤醒周期，BI 可顺带观测。

**无需参谋的理由**：两个三选一都有密先例（AM/BV 容器语义）+ SDK __RW 证据 +
唯一消费者形状钉死，不存在势均力敌的对抗性决策；唯一真未知（DCCK 复位值）
不改变方案选择。

## 5. 不确定处（如实标注）

- **b[0x129A30] 前置写者**：0x1299D0 电源结构 26 处 li 引用（0x6067xxx/
  0x60c6xxx RAM 拷贝族），未逐一反汇编排除 +96 字节写；决定 DCCK 是否触达
  （§4.3 观测点）。[静态扫描局限]
- **S13 标志 b[0x127348] 的运行时值**：writer=0x606487c（事件机制），静态
  BSS=0，但事件域完整调用面未穷举；首跑即验。[静态扫描局限]
- **S16 feature-18 位图值**：[0x125C1C] bit18 运行时值未采样；两臂都无新 SFR，
  只影响 park 前是否出现 pend-唤醒周期。[运行时观测项]
- 线性 xref 扫描（call 目标）在对齐漂移处有伪迹风险：本文所有关键边都用
  「从已知函数入口正向重解」复核过；「零调用者」结论只排除直接调用与 4 字节
  指针引用，不排除其他间接表。[方法局限，沿 BO §7 口径]
- 步数预测沿 tail-map 限制：CB 折叠策略会改变 W1[0] 内延时步数，本清单只钉
  顺序与地址，不预测具体步数。
