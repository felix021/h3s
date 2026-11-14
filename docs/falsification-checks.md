# 反例验证（路 AZ/sim-falsify，2026-11-14）

codex 全局 ROI 复盘第 3 路「独立反例验证」的落地：**故意破坏一个被依赖的假设，
看测试是否诚实失败**。反例「仍通过」= 原测试假阳性（断言没有真的依赖被破坏的机制），
记入盲区清单。本文件是方法论 + 每反例结果 + 盲区清单；工具为
`tools/test_falsification.py`（快速层已接进 `check_local.py --units`）。

两层设计：

- **快速单测层**（`python3 tools/test_falsification.py`，秒级，进 `--units`）：
  接线守卫反例、Timer0 计数守恒/停止纪律、跨页拆分哨兵、键链常量前置守卫、
  post_stop 外设时钟冻结、事件时钟 post_stop 冻结不变式（AZ-6，盲区 B-1
  收口）、闭式换算常量断言（AZ-7，盲区 B-2 收口）、场景级快照反例（AZ-8，
  盲区 B-3 收口：快照恢复世界→值级破坏→短程 resume，断言面必须红）。除
  AZ-8 需要公开固件/模拟器缓存（`--fetch-inputs`/`--fetch-simulator` 一次
  下载，无私有 ROM）外不需要资产。
- **慢场景层**（`python3 tools/test_falsification.py --scenario-evidence --rom <ROM>`，
  分钟级，**不进** check_local）：用被破坏的开关复跑大场景，证明前沿通过
  genuinely 依赖该投影。基线 3a86487 的实测结果在 §3。

## 1. 反例清单（挑了哪几个、为什么）

按「高风险投影 = (a) 支撑当前前沿 3,137,573 的条件投影；(b) 支撑 D2 按键
验收链的投影；(c) 破坏后可能静默改变路径而非报错的机制」挑选：

| # | 反例 | 破坏方式 | 高风险理由 |
|---|------|---------|-----------|
| AZ-1 | 场景开关依赖错置（BN/AA/AL/S/Timer0 探针族） | 撤掉层叠依赖/值 fixture 关键行 | 前沿场景 60+ 开关靠 require 守卫保证组合合法性；守卫若漏，破坏会静默改变世界 |
| AZ-2 | Timer0 计数守恒 + 停止纪律（BE/BF 族） | 账本互证 + 停止边界重放 | 「停止后事件还在增长」原型：计数若在停后/重试时幻影增长，事件时钟与到期断言全部失真 |
| AZ-3 | 跨页拆分哨兵（AN） | 页表字节哨兵 + 全六哨兵 + 页内/读侧对照 | 拆分是 derived-not-measured 假设；越界/回环/假拆分/读侧过扩都会静默写错物理内存 |
| AZ-4 | 键链常量前置守卫（S/AJ） | 档位带不变式 | D2-a 验收的通过判据（三个手势 post）只在档位值落在分档带内时才可推导 |
| AZ-5 | post_stop 外设时钟冻结（S 直调机制） | 直调前后计数快照 | 「停止后事件不增长」的直调版：直调循环若推了外设时间，键链拍数与时序断言失真 |
| AZ-6 | 事件时钟 post_stop 冻结不变式（盲区 B-1 收口；前置=CW 落地 event_clock） | 合成双核四种 stop 形状（交付请求逃逸/诚实停/严格停传播/TIMER2 唤醒返回）后反复观察+完整 report() 生成 | 事件时钟落地后，「stop 后事件时钟不再前进」从 AZ-5 的 Timer0 直调版升级为机器层不变式；观察/查询路径若隐式走表会静默失真全部时序断言 |
| AZ-7 | 闭式换算常量断言（盲区 B-2 收口；BK 闭式） | 命名常量推导链逐原子钉死 + 模型行为互证 + 双链汇合 | 「1ms=恰 4000 模型步」的两条独立换算链（LRC/TIMER2、STD12M/Timer0）散落字面值改一处就漂；常量化后任何原子漂移在 units 层直接红 |
| AZ-8 | 场景级快照反例（盲区 B-3 收口；SNAP 快照恢复） | 从捕获点恢复世界→对快照做**值级**篡改（形状闸自洽重 derive 通过）→短程 resume：Timer0 pending 位翻转/cnt 偏置、counts 簿记、寄存器容器记录值、软件 IRQ pending 位移除、M7 账本记录、CPU sp_known、静态 fixture 值（守卫面） | 「快照恢复路径不能洗掉破坏」：若恢复用静态重建覆盖状态、或报告不从恢复世界读，值级破坏会静默消失——SNAP 等价测试就成空真。B-3 挂账的「场景级反例 3 分钟」由此降到秒级进 units |
| R1 | RES fixture 撤掉（BY，场景级） | 条件前沿开关集去掉 `fd31_adc_res_named_values` | 前沿 3.1M 与键链采样都压在 RES 具名值上；撤掉后仍前进 = 假阳性 |
| R2 | Timer0 IRQ4 交付撤掉（BE，场景级） | 条件前沿开关集去掉 `timer0_irq4_delivery` | 「屏蔽必要 IRQ 是否仍然前进」原型直接场景版 |
| R3 | 键链手势输入破坏（S，场景级） | 阈值 500→750，按下档 700 改出分档带 | 「改一个手势拍数断言应抓住」：等价的输入侧破坏（拍数字面量在闭包内，见盲区 B-2） |

未单列的候选：CON1 读回「无写时读回」（路 AA）——模型级负例已由
`test_br28_model.py::test_jl_uart_con1_readback_projection_is_last_write_value`
钉死（从未写过的 UART1/2 CON1 停、其余白名单字停、CON0 不在本容器），本路只在
AZ-1 补接线守卫反例，不重复。IRQ68（音频 DMA 完成，D2-b 蓝图）尚未实现，
无从反证——列入盲区 B-4。

## 2. 快速层结果（全部进 `check_local --units`，2026-11-14 全绿 26 例 + B-3 收口 9 例 = 35 例；AZ-8 为 2026-11-15 路 B3R）

**AZ-1 接线守卫反例族（10 反例 + 1 对照）——被抓住。**
对每个撤掉依赖的开关组合（fd31 CON 读回/PND 完成/RES 具名值三层、键链场景
fixture 缺键通道行、键链+矩阵同开、Timer0 交付撤 STD12M、掩码探针撤到期探针、
lp_timer CON 读回撤写记录、UART CON1 读回撤 init 白名单、堆信息撤 power_init），
`irq_case` 都在**进入仿真之前**以精确的 require 文案拒绝。假 module（任何属性
访问即爆炸）证明拒绝路径零仿真器/镜像接触。对照组（banks 无 entry）越过全部
接线守卫、深到 `DualCoreMachine` 构造器才报错——证明整组不是「永远拒绝」的空真。

**AZ-2 Timer0 守恒 + 停止纪律——被抓住（两条新钉死）。**
①三级守恒律（12/16 换算 → 分频 → 计数 telescoping 的独立账本）与 `advance()`
在 source5/div1/prd119999 多周期（含 W1C 片间）与 source4+分频两个形状下逐步
互证，无幻影增长/泄漏。②in-flight 交付请求期间每模型步恰消耗 3 个 STD12M 计数
（显式消耗、可归因）；严格世界的 `interrupt_delivery_not_implemented` 停止
**确定但非幂等**——重试 advance 会在再次抛出前继续走表（每步 3 计数），见盲区 B-1。

**AZ-3 跨页拆分哨兵增补——被抓住（四条新钉死）。**
①拆分存储**零页表污染**：两枚 PTE 与前后邻接表项逐字节不变（翻译只读不写）。
②第二页 PTE 无效时**全部**哨兵不污染（既有测试只断言 6 个中的 3 个，本路补满，
证明被拒的第二半没有任何越界/回环副作用）。③页内 8 字节**不走拆分**（适配器
`exact_doubleword_store` 而非 split；两半连续落在单次翻译内）——无假拆分。
④跨页**读** ld.d 两半各取各自翻译页的值（上游 m2 语义读侧本就是两个独立 4 字节
事务；「按第一页连续读」会拿到 0xcccccccc，实测拿到各自页值）——拆分世界读写对称。

**AZ-4 键链常量前置守卫——被抓住。**
释放档 400 ≤ 阈值 500 < 按下档 700 ≤ 1022、键通道 fixture 行=按下档、键链
dict 是 BY 三通道的超集不改写、分档阈值表升序 + 1022/1023 哨兵尾。任何把档位
改出带的编辑在 3 分钟场景跑之前就失败。

**AZ-5 post_stop 外设时钟冻结——被抓住。**
真模拟器 + 已走表 Timer0（source5）上直调 `key_chain_direct_call`（rts 窗口）：
cnt/两级分数余数/到期数/模型步数**全部不变**，CPU 状态逐字段复原；随后显式
advance 一步计数即 +3（冻结断言有分辨力，非空真）。post_stop 直调段不推进
外设时间是**实测属性**，不只是注释里的假设。

**AZ-6 事件时钟 post_stop 冻结不变式——四种 stop 形状全冻结 + 破坏被抓住
（盲区 B-1 收口，路 INV/sim-invariants，2026-11-14；前置=CW 落地 event_clock、
docs/event-clock.md 契约）。**
合成双核（probe_event_clock 的 ParkedCpu 桩，不需要 ROM/模拟器）覆盖事件时钟
全部可观测计数面（推进量/虚拟模型步/core tick/Timer0/TIMER2 两级余数/软件
pending/事件簿记）：**S1** 交付请求逃逸（enter_idle_wait 抛 IrqDeliveryRequest
回到 harness）；**S2** 无事件诚实停（all_cores_idle_waiting，零推进）；
**S3** 严格停传播（Timer0 合格到期、交付投影关，Blocked 原样传播）；
**S4** TIMER2 完成唤醒正常返回（事件时钟恰推进闭式 4000 步=STEPS_PER_MS，
B-1 与 B-2 在此汇合）。每种形状停点后 3 轮「计数快照+完整 report() 生成」
逐项不变；显式 advance 一步必动计数（分辨力）。两条入口裁决：
①泊核入口（enter_idle_wait）对已泊核短路立即返回，停后重试零推进；
②事件时钟入口（advance_event_clock）的 horizon 家族显式排除 unresolved_overflow
的 core tick 与已在途的 Timer0 pending 片，捕获请求后重入=诚实停零烧表
（唯一新增是 event_clock_engaged 事件簿记 +1）——烧表风险只在模型层直调
`hardware.advance` 的 harness 路径（AZ-2 钉死）。反例自证（破坏→红，外部
monkeypatch 复核）：观察路径隐式走表（report 生成时顺手 advance）在 S1/S2
都被检出。

**AZ-7 闭式换算常量——双链汇合 + 模型互证 + 漂移被抓住（盲区 B-2 收口；
闭式=路 BK §3.2 钉死，docs/timer2-eventclock-design.md）。**
提两个命名常量：`STEPS_PER_MS=200*80//4=4000`（LRC/TIMER2 链：1ms=200 LRC
tick × 80 RC16M 参考 tick/LRC tick ÷ 4 参考 tick/模型步）与
`TIMER0_STEPS_PER_10MS=120000//3=40000`（STD12M/Timer0 链：prd=119999 的
整周期）。断言四层：①原子钉死（LRC_HZ_NOMINAL=200000、
LRC_REF_TICKS_PER_LRC_TICK=80、STD12M_TICKS_PER_REF_TICK=(3,4)、
source_ticks_per_step=4 与实现侧逐项绑定）；②两条独立链在同一全局模型步轴
上汇合（40000==10×4000）；③模型互证（Timer2Delay 1ms 形状闭式 horizon=
常量、逐 advance 第 4000 步恰首次 pending；Timer0 整周期恰 40000 步一次到期
且回绕位置守恒）；④反例自证：LRC 原子 80→40（「频率漂一倍」）时闭式立刻
偏离常量、STD12M 原子 (3,4)→(1,4) 时周期互证红。设计要点：常量用**字面
原子**推导而非 import 实现常量——import 顺序不同时快照式引用会让常量与
实现原子同步漂移（断言恒真、漏检；自证实测发现后修正）。只提这两条已被
闭式证明的普适换算；瞬态推导值（horizon 36,780 步，依赖 cnt）不提常量。

**AZ-8 场景级快照反例——值级破坏全被抓住 + 守卫面拒载 + 对照组等价绿
（盲区 B-3 收口，路 B3R/sim-b3r，2026-11-15；前置=SNAP 快照捕获/恢复
全链路 + advance tier 等价）。**
世界=公开资产 typed_memory_replay 16,077 步（SNAP fast tier 同款）挂带
Timer0 走表（con=0x1001 source4/div1 每步恰 4 参考计数、prd=0xFFFFFFF
全程不回绕）+软件 IRQ pending（word0 bit3）的外设模型——外设域访问只有
PLL0.CON0 读，`initial_registers={PLL0:1}` 与 `explore(pll=1)` 假设同值，
前缀与原世界同停点（unverified_zero_count_rep@16,077）。中点 8,000 捕获，
对快照做**值级**篡改（形状闸/manifest 对 body 形状重 derive 全部自洽——
测的是恢复链路携带的值，不是格式闸，那是 test_world_snapshot DriftGates
的域），短程 resume 后断言面：①等价对照（纯 json.dumps，插入序可见，无
diff 白名单）②报告键精确红点（peripherals.timer0 即 check_d1_acceptance
的 `_timer0_state` 数据源）③恢复侧静态守卫。八类破坏+对照组（units 9 例）结果：

| # | 破坏（值级，形状自洽） | 红点（实测） | 被抓住 |
|---|----------------------|-------------|--------|
| ① | Timer0 pending 位翻转 False→True | peripherals.timer0.pending（=D1-2 断言数据源 `_timer0_state`）；icfg gates8/9 未开故 resume 照常到终点，破坏存活到报告 | 是 |
| ② | Timer0 cnt +4（一个模型步的参考计数量） | peripherals.timer0.cnt 终点恰偏 4——恢复世界继续走表、篡改按步传播（BQ 闭式换算/事件时钟 horizon 全压在 cnt 上） | 是 |
| ③ | counts 簿记一条（completed_model_steps +1，虚拟时间轴） | peripherals.counts.completed_model_steps 偏 1 | 是 |
| ④ | 通用寄存器投影容器一条记录值（PLL0 读回记录 1→2） | peripherals.registers['0xfd4600'] | 是 |
| ⑤ | 软件 IRQ pending 位移除（word0 value 0x8→0x0，known 掩码不动） | peripherals.software_irq_commands.pending['0'].known_value | 是 |
| ⑥ | M7 循环账本一条记录值（memory events 首条 bytes 翻一位） | memory_events[0].bytes | 是 |
| ⑦ | M1 CPU 状态位 sp_known True→False | last_registers.sp 变 unknown（hex/unknown 由该位分派） | 是 |
| ⑧ | 静态 fixture 值篡改（initial_registers PLL 假设 1→2） | 恢复侧 check_static 拒载：SnapshotError 'static configuration mismatch on [initial_registers]'（守卫面，不是报告面） | 是 |
| 对照 | 未破坏恢复续跑 vs 完整直跑 | 逐字节相等（AZ-1 先例：证明「抓住」不是「任何恢复都红」的空真） | — |

诚实边界（如实记录）：typed_memory_replay 是单核无 machine 世界（explore
的 CPU 形状没有 machine.select() 需要的 icfg_known 属性），**M5 机器交付
簿记（timer0_irq_deliveries）在该世界不可承载**——该类破坏在慢层 R4
（ROM 门控 irq_case 世界）覆盖；M3.timer.delivery_in_flight 不在报告键里，
值级篡改对其不可观测，同在 R4 用 M5 形态覆盖。「前缀分歧」型反例
（R1/R2/R3 撤开关）快照不适用（快照世界 ≡ 原开关集的原前缀，
docs/snapshot-design.md §4.1），慢层保留。

## 3. 慢场景层结果（基线 3a86487，2026-11-14 09:08-09:11 实测 R1-R3；R4 为 2026-11-15 路 B3R 补，全部被抓住）

复现：`python3 tools/test_falsification.py --scenario-evidence --rom <private ROM>`
（输出 JSON 落 `analysis/local-checks/falsification-scenarios.json`；对照组=同一
开关集不破坏时的绿 probe 运行，前沿 3,137,573 @ 0x60719c0）。

| # | 破坏 | 实测停点 | 对照 | 结论 |
|---|------|---------|------|------|
| R1 | 条件前沿撤 `fd31_adc_res_named_values` | **304,406 @ 0x6011704**，`fd31_adc_res_not_modeled` 读 0xfd3104（caller A 0x60116d0 的首个 RES 读） | 3,137,573 | **被抓住**：撤掉 RES 具名值，前沿退回 30 万级的 RES 诚实停；3.1M 的通过真的依赖该 fixture |
| R2 | 条件前沿撤 `timer0_irq4_delivery` | **336,337 @ 0x12e286**（RAM 延时环内），`interrupt_delivery_not_implemented`（core0, irq4） | 3,137,573 | **被抓住**：屏蔽 Timer0 交付，首次合格 pending（延时上下文里）即严格停，场景不前进 |
| R3 | 键链场景阈值 500→750（按下档 700 改出带） | 世界照常到 3,072,236 @ 0x60012de；采样事务 7 次全过（sampled==fixture，传输层完好）；**observed posts = ∅** | 期望 posts {0x10a00, 0x10a01, 0x10a04} | **被抓住**：断言链对输入敏感——输入侧破坏使三个手势 post 全灭，probe 的 require 会失败 |
| R4 | 条件前沿 flag 全栈 N=400,000 预算停点快照（completed 397,845，timer0_irq_deliveries 5 条非空）篡改首条交付簿记 reti_from 0x12e286→0x12e280（形状自洽），恢复续跑 X=200 | resume 398,045 @ 0x12e286（budget 停）；报告 machine.timer0_irq_entry_projection.deliveries[0].reti_from=0x12e280 | 对照组未篡改 resume vs full（预算=捕获预算+X）逐字节相等 | **被抓住**（2026-11-15 实测）：交付簿记（one-shot 投影「已交付」判定源）的值级破坏存活恢复链路并出现在 machine 报告键——恢复路径不能洗掉/重建它 |

R2 附注：BN 时代「STD12M 到期在延时上下文惰性」的记录是在**交付探针开启**的
世界里（交付真实发生所以无停点）；本反例证明交付机制撤掉后同一到期点就是
严格停——「惰性」的正确解读是「交付已发生」，不是「不合格」。

## 4. 盲区清单（漏网的与没法测的，按优先级）

- **B-1（已收口为不变式，2026-11-14 路 INV）模型 advance 停止非幂等**：
  `BR28Model.advance()` 先消费本步计数再抛交付/严格停——停止后重试 advance
  的 harness 会**静默**烧掉定时器计数（AZ-2 钉死模型层确定性）。原补法设想
  是「事件时钟落地时做成模型不变式」；CW 落地 event_clock 后实际裁决（AZ-6
  实测）：烧表风险**只**存在于模型层直调 `hardware.advance` 的 harness 路径
  ——机器层泊核入口（enter_idle_wait）已泊即短路、事件时钟入口
  （advance_event_clock）的 horizon 家族显式排除溢出态/in-flight 计数器，
  两种停止形状的重入都是诚实停零烧表。B-1 由「已知边界记录」升级为
  **AZ-6 常态不变式**（四种 stop 形状冻结 + 入口裁决 + 破坏自证，破坏即红），
  AZ-2 继续钉住模型层直调路径。
- **B-2（已收口为常量断言，2026-11-14 路 INV）闭式换算字面量不可参数化破坏**：
  「1ms=恰 4000 模型步」此前散落为字面值（BK 设计文档、test_br28_machine
  观测钉子、机器报告文案）。AZ-7 提为命名常量 `STEPS_PER_MS`/
  `TIMER0_STEPS_PER_10MS` 并把推导链逐原子钉死+模型互证+双链汇合；任何
  原子漂移（LRC 频率、参考 tick 比、每步源 tick）在 units 层直接红。
  键链拍数（13/11/25/80）本身仍是闭包字面量——R3 的输入侧等价破坏演示
  继续有效；拍数属固件行为参数非闭式换算，不强行常量化（ROI 裁决）。
- **B-3（已收口为场景级快照反例，2026-11-15 路 B3R）场景级反例不进 --units**：
  原挂账原因=单次世界构建 ~3 分钟（3M 步 @ ~17k 步/s），R1/R2/R3 只能按需
  手跑。SNAP 落地 probe_boot.explore(snapshot=) 捕获/恢复（+advance tier
  逐字节等价）后，「停点后破坏」类反例降到秒级：AZ-8 在公开资产 16,077 步
  真世界（快照中点 8,000→恢复→短程 resume）上以**值级**篡改八类
  （Timer0 pending/cnt、counts 簿记、寄存器容器记录值、软件 IRQ pending 位、
  M7 账本、CPU 状态位、静态 fixture）+对照组进 `--units`（+9 例，~5s），
  证明「快照恢复路径不能洗掉破坏」；M5 机器交付簿记破坏在慢层 R4
  （ROM 门控）覆盖。「前缀分歧」型反例（R1/R2/R3 撤开关）快照**不适用**
  （快照世界 ≡ 原开关集的原前缀，SNAP 设计 §4.1），慢层保留——这是定义
  边界不是缺陷。
- **B-4 IRQ68（音频 DMA 完成）无从反证**：D2-b 蓝图未实现。实现时按本路模板
  补「不发完成事件 → 音频场景停」反例（AZ-1 的守卫族 + R1 场景式各一条）。
- **B-5 世界级「停后事件不增长」A/B 对照未做**：AZ-5 覆盖直调段冻结（机制级），
  但「同一世界开/关 post_stop 的计数 A/B 对照」（证明报告里的到期/交付计数
  在 post_stop 段零增长）没跑（需两次 3M 世界，~6 分钟，ROI 低——直调冻结
  已由 AZ-5 机制级证明）。快照落地后成本降为一次 3M 捕获+两次恢复段，
  ROI 边际改善但仍非零成本，保持挂账。

## 5. 结论

8 个反例（5 快 + 3 慢）**全部被抓住**：当前投影族的负例/接线守卫/断言链没有
发现假阳性。三条当年钉死的已知边界中，**B-1/B-2 已分别收口为 AZ-6/AZ-7
常态不变式**（2026-11-14 路 INV，前置=CW 落地 event_clock + BK 钉死闭式）：
事件时钟 post_stop 冻结（四种 stop 形状 + 入口裁决 + 破坏自证）与闭式换算
常量（双链汇合 + 模型互证 + 漂移自证）；**B-3 已收口为 AZ-8 场景级快照反例**
（2026-11-15 路 B3R，前置=SNAP 快照捕获/恢复+advance tier 等价）：快照恢复
世界的值级破坏（八类+守卫面+对照组）进 `--units`（35 例），M5 交付簿记
破坏在慢层 R4；B-4/B-5 保持挂账（IRQ68 实现/世界级 A/B 对照，见上）。
这组反例进 `check_local --units` 后成为常驻回归——未来任何让「破坏仍通过」
的改动（守卫弱化、断言放宽、投影偷读、观察路径隐式走表、换算原子漂移、
快照恢复洗掉状态）会直接红灯。
