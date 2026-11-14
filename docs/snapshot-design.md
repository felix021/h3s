# 快照/恢复机制设计蓝图：世界状态全清单 + 一致性对照 + 三消费者接口（路 BD / sim-snapshot）

2026-11-14 路 BD（sim-snapshot，基线 87a0cc3）。动机（codex 全局 ROI 复盘 + 路 AZ 盲区
B-3）：条件前沿世界构建一次 = 3,137,573 步 ≈ 3 分钟（~17k 步/s），场景级实验/反例
（D2-b 注入变体、停点后反例、D1 断言重放）每次都要整世界重跑，进不了 `check_local
--units` 反馈环。快照/恢复可把「从停点继续实验」降到秒级——但 codex 警告原文照录：

> 快照必须包含双核、事件队列、外设及未知位状态，并与完整重放对照。只存 RAM/PC 的
> 快照容易制造另一套启动条件。

本文件按路 AW（sim-d2bdesign）模式写**实施路拿到即可开工**的设计蓝图：状态全清单
（§1）、快照点与格式（§2）、一致性对照的自动化（§3）、三消费者接口（§4）、风险与
分步工作量（§5）。**本路是设计文档，不是实施**：未改 tools/ 任何代码、未跑任何改动
场景；所有状态字段逐一读自基线 87a0cc3 的 tools/ 源码（标 [代码]），已合并文档事实标
[文档]，本文新增的工程决策标 **[设计]**——实施路落地时按运行观测复核。

# 0. 前提与锚点速查

## 0.1 世界构建链与活世界对象图

| 事实 | 锚点 | 出处 |
|---|---|---|
| 构建链 | `probe_irq_entry.irq_case`（开关表+require 守卫）→ `probe_dualcore.dual_case`（机器/参数组装）→ `probe_platform_startup.platform_case` → `probe_arch_startup.architecture_case` → `probe_clock_startup.startup`（handoff/诊断组装）→ `probe_boot.explore`（主循环） | [代码] |
| 活世界五元组 | 停点时刻的世界 = `machine`（DualCoreMachine）+ `hardware`（BR28Model）+ CPU0（explore 局部 `c`）+ CPU1（`machine.cores[1]`，与 CPU0 共享 `mem`）+ `mem`（explore 闭包 Memory，`cpu.mem` 同一对象） | [代码] explore+DualCoreMachine.attach/factory |
| 运行循环账本 | explore 闭包局部量：`events`（有界 64/32）、`adapters`（Counter）、`rom_steps`（Counter）、`event_counts`（Counter）、`milestones`（无界）、`trace`（有界 64）、`completed`、`stop`、`dispatch_delivery`（交付分发的纯函数，无独立状态） | [代码] explore 主循环 |
| 停点后访问先例 | `post_stop(machine,hardware,stopped_cpu)` 回调在主循环停点后、报告组装前拿到活世界（S 路键链直调、AW 蓝图注入循环都从这里进）；`key_chain_direct_call` 受控步进循环=主循环最小子集（不调 advance） | [代码+文档] probe_boot/probe_irq_entry |
| 循环步序 | 每迭代：`machine.select()` → decode → `c.step()`（Blocked 在变更前抛出）→ `machine.after_step` → `hardware.advance(c.icfg)`（IrqDeliveryRequest/Blocked 原样传播，交付经 `dispatch_delivery`） | [代码] explore |
| 世界重放速度 | 3,137,573 步 ≈ 3 分钟 @ ~17k 步/s | [文档] falsification-checks §4 B-3 |
| 前沿契约 | 条件前沿 3,137,573 @ 0x60719c0（unmapped_or_readonly_write 写 0xfe0538=0）；主前沿 289,849 @ 0x60679ba；开关集 19 base + 33 conditional + 6 probe_only | [文档] frontier_registry.FRONTIERS/FEATURES |
| 既有「对照」先例 | ① stash 对照法：改动前后各跑一次 probe、报告逐字节 diff（各路「前 8 场景报告逐字节不变」）；② AN 配置差异重放：撤开关看前沿是否真退（R1/R2）；③ BR L2 三镜像差分：official/b79/EQ 同场景全前缀重放 parity 到前沿 | [文档] HANDOFF 各路 |
| 报告确定性 | 报告 JSON 无时间戳/随机数；行为关键集合（cores/idle_wait_cores/first_task_calls/additional_configuration）全是 int 集合，int 哈希跨进程确定；报告内集合输出前排序 | [代码]（保险措施见 §3.4） |

## 0.2 术语约定 [设计]

- **快照（snapshot）**：停点时刻世界五元组+循环账本的完整可序列化副本。
- **恢复（restore）**：用与原世界相同的静态配置重建对象图，再把快照态逐属性覆盖上去，
  得到可继续步进的活世界。
- **等价（equivalence）**：同代码指纹下「恢复→跑 X」与「完整重放→跑 X」的报告逐字节
  相等 + 内存全景摘要相等（§3）。等价是恢复合法性的**唯一**证明，缺一字段即失败。

# 1. 状态全清单（codex 点名项逐条落位）

按状态载体分 9 个模块（M1–M9）。每条给「存/排除」判定：**存**=必须进快照；
**排除（可推导）**=纯 memoization 或由代码+静态配置重建，不进快照但排除理由必须
可论证；**静态配置**=不进快照、由场景参数重建（§2.3 指纹覆盖）。

## M1 双核 CPU 对象（probe_boot.CPU，CPU0+CPU1 两个实例）

上游基类（sim.py `class CPU`）+ explore 闭包子类的全部实例属性：

| 状态 | 字段 | 存/排除 | 说明 |
|---|---|---|---|
| PC/步数 | `pc`、`steps`、`seq` | 存 | seq=顺序下一条预判 |
| 通用寄存器 | `R[0..15]`：每槽是 int **或** 抽象值对象 | 存（含类型标签） | 抽象值四种：`UnknownWord`（bytes[4] 各 None/0-255 + origins[4] 各 None/物理地址）、`IcfgValue`（value/known/identity=(core,status_generation)）、`GpioOutValue`（address/value/known）、`GpcntConValue`（value/known）——origins/identity 是「未知位状态」的一部分，必须逐字段 round-trip [代码 br28_unknown/br28_icfg/br28_model] |
| 标志/栈 | `flags`、`sp`、`sp_known`、`ssp`、`usp`、`rets`、`reti`、`irq_psr` | 存 | usp/ssp 可为 None=未知，None 本身是状态 |
| ICFG | `icfg`、`icfg_known`、`icfg_unknown_mask`、`icfg_state`（IcfgState：core/value/known/**status_generation**/writes/bit9_updates/task_return_projections） | 存 | status_generation 参与 IcfgValue.identity 校验，错一代价是恢复后 ICFG 写入被拒 |
| 执行上下文 | `bootstrap_context`、`active_context`（None/'software_irq_handler'/'core_tick_handler'/'timer0_irq_handler'/first_task 投影名）、`opaque_source`、`repeat`（REP 体投影 dict：start/end/register/remaining/multi_block）、`after_then` | 存 | 在途中断/REP 状态=「事件队列」的 CPU 侧投影 |
| 银行/帧簿记 | `last_explicit_ssp`、`last_explicit_usp`、`unresolved_ssp_candidates`、`task_frame_load`、`task_frame_restore`、`task_gpr_frame`、`first_task_return_frame` | 存 | 缺任一=恢复后银行假设/首任务对照断裂 |
| 解码 memo | `cache`（pc→(insn,raw,size)）、`mutable_decode_bytes` | **排除（可推导）** | 语义透明论证 [代码 decode]：ROM/flash 区 `super().decode` 纯 memo；可变区每次 decode 先按当前映射重读字节校验、失配即弃缓存——空缓存恢复后首次 decode 重读重解码，结果必然相同；`mutable_decode_bytes` 只被缓存命中路径消费。排除两者必须**成对**（只排一个会改变校验路径） |
| 模型表 | `models`（k2 修正后的指令模型 dict）、`tab`（上游指令表） | 静态配置 | 由 upstream PIN + architectural_k2 重建；跨快照 diff 上游语义=指纹职责 |
| 共享引用 | `mem`、`core_id` | 存 core_id；mem 为世界级（M2） | CPU1 由 `machine.factory()` 重建后覆盖状态 |

## M2 共享内存（probe_boot.Memory，单实例、两核共享）

| 状态 | 字段 | 存/排除 | 说明 |
|---|---|---|---|
| 已初始化字节 | `ram`：dict 物理地址→字节值，**键缺席=未知**（不读零） | 存（原始字节段） | 「未知位状态」的 RAM 侧编码=缺席语义，序列化必须区分「未知字节」与「值为 0 的已知字节」（§2.4 格式） |
| 未知来源 | `unknown_origins`：dict 地址→物理来源地址 | 存 | 任务帧 GPR 槽/运输未知字节的 provenance；丢了它，恢复后 UnknownWord 的 origins 断链 |
| 部分溢出字 | `icfg_spills`：dict 字地址→IcfgValue | 存 | 部分已知 ICFG 溢出字（known/value），与 ram 字节互补 |
| 窗口表 | `regions` 元组、`operation` | 静态配置 / 瞬态 | regions 由场景链重建；operation 是单步内的读写标记，停点时刻恒为复位值 |
| 只读输入 | app 切片、rom、handoff readonly、flash 窗 | 静态配置 | 文件+digest 重建（public 资产+私有 ROM 路径不进快照，§5.4） |

RAM 窗与前沿时刻的已初始化量估计（§2.2 大小测算用）：

| 窗 | 范围 | 全大 | 3,137,573 时刻估计 |
|---|---|---|---|
| mmu_page_table | 0x120000..0x122800 | 10,240B | 全初始化（ROM memset 全 10240B [文档 mmu-startup]） |
| bootstrap_stack | 0x123000..0x123840 | 2,112B | 部分（bootstrap 深度+复位路径下探） |
| qcy_initialized_data | 0x124840..+0xae0 | 2,784B | 全（启动拷贝） |
| qcy_bss | 0x125320..+0x530c | 21,260B | 全（清零+运行写入） |
| qcy_ram_code | 0x12a640..+0x48d4 | 18,644B | 全（拷贝） |
| cpu1_stack | 0x123840..0x124840 | 4,096B | 部分；**未写 GPR 槽保持缺席**（60B 未知槽是状态） |
| irq_vector_table | 0x19fe00..0x1a0000 | 512B | 部分（已注册槽） |
| physical_heap | 0x134500..0x19fb80 | 440,448B | 小部分：分配位图/描述符+TCB+任务栈帧，估 20–60KB |
| P11 公共段窗 | 0xf20080..+12112 | 12,112B | 全（拷贝，SHA 与资源互证 [文档 路 W]） |
| P11 M2P 窗 | ~0x60 | 96B | 部分 |
| 零散（boot_info/handoff/update/mask_context/boot_args） | — | ~276B | 多数全/近全 |

## M3 外设模型顶层：定时器/中断子系统/杂项（BR28Model）

| 状态 | 字段 | 存/排除 | 说明 |
|---|---|---|---|
| Timer0 | `timer`：con/cnt/prd/pending/**remainder/source_remainder**（两级分数余数）/expirations/delivery_in_flight（allowed_sources 静态） | 存 | 「时钟/定时器」核心：分数余数丢了=恢复后到期步数漂移（BQ 闭式互证依赖它） |
| IRQ 控制器 ×2 | `irq`（CPU0）与 `irq1`（CPU1，multicore 后）：config[32]（None=未知槽）、ipmask（None=未知） | 存 | None 是状态；使能 nibble 全部来自固件真实写 |
| 软件 IRQ | `software_irq`：pending[2]（PartialWord）、source_enable[2]（PartialWord）、commands/acknowledgements/source_commands 等计数 | 存 | pending 的 known/value 是「未知位状态」 |
| core tick ×2 | `core_ticks[0/1]`：con/cnt/prd/pending/unresolved_overflow/expirations/acknowledgements/writes/delivery_in_flight（progress 静态） | 存 | unresolved_overflow=True 时计数器停在溢出态等 ACK——在途事件 |
| 通用寄存器投影 | `registers`：dict 地址→int（DCCK/UART CON/P33 容器等的合成字+写后值） | 存 | 从未写入的地址缺席=未知，天然不存 |
| GPIO | `gpio`：dict 地址→PartialWord(value,known) | 存 | known 位图是状态 |
| 共享锁 | `lock_owner`（'unknown'/None/0/1） | 存 | |
| 总线杂项 | `cpu1_control`（None=未知）、`cpu1_control_kick_writes`、`secondary_cpu_executed`、`current_core`、`core_icfg`、`multicore_enabled`、`usb_disable_writes`、`p3_ana_read_served` | 存 | 后四个是调度/路由簿记 |
| 计数与写日志 | `counts`（Counter：reads/writes/completed_model_steps/opaque_*_reads/p11_lpwr_post_clear_poll_reads…）、`writes`（有界 64） | 存 | 报告等价依赖；completed_model_steps=虚拟时间轴 |
| 探针 armed 位 | `timer0_irq4_probe_armed`、`core_tick_irq3_probe_armed` | 存 | 探路 fixture 的「已生效一次」标志=一次性事件状态 |
| 静态初值 | `initial_*` 系列、`source_ticks_per_step`、`timer_lsb_ticks_per_step`、各开关布尔、fixture dict（**含运行时可变的 `fd31_adc_con.res_named_values`——键链场景在采样阶段改它，属 M4**） | 静态配置 | 由场景参数重建 |

## M4 外设投影对象族（约 25 个类，按状态形状分六族）

全部对象是 BR28Model 属性；逐类读自 br28_model [代码]。**codex「访问过的 SFR 必存、
从未访问的可不存」在这里自动成立**：每族的状态就是「已观测访问」的投影（dict 记录/
位图/计数），从未访问的地址根本不进 dict。

| 族 | 类 | 状态字段（全部**存**） |
|---|---|---|
| 写记录族 | P11WindowRegisters、P33WindowByteRegisters、P33PortSelRegisters、P3LsRegisters、JLUartInitWriteRecords、JLOmapWriteRecords、JLImapWriteRecords、JLWlAudWriteRecords、JLAudioDacInitWriteRecords、LpTimerInitRegisters、Fd31AdcConRegisters | stores{地址:{values[],writes}}、整族计数（word_writes/byte_writes/rmw_writes…）；读回投影的辅助计数（con1_reads/lrct_con_reads/lrct_num_reads/reads_after_write/synthesized_initial_reads）与 lrct PartialWord、mem_pwr bits、control PartialWord |
| 位记录族 | MemPwrConRegisters、SfcClockGateProjection（bits+operations[]+select/divider+计数）、SFCReadBus ×2（con/baud/writes/window_control PartialWord+window_control_operations[]/window_limit/window_base+计数） | PartialWord(value,known)+操作序列+计数 |
| 一次性事件族 | P11LowPowerResponse（**requests**——请求-响应投影 armed 位）、P33AwkupRegisters / P33CanaryWatchRegisters（**read_state{reads,written,fixture_reads}**——首读 fixture 已消费位 + bits PartialWord + first_read_words 副本）、EfuseReadProjection（**page/byte/active/read_done/reads**——EFUSE 读事务进行态）、P11AccessProbe（访问计数） | armed/已消费位丢了=恢复后投影行为改变（首读 fixture 会再发一次） |
| 进位状态族 | HardwareCrcEngine（**crc 当前累加值**、seed_writes 有界 32、feeds、result_reads）、IcuInitializationProjection（control{base:word}、**pending{base:剩余步数}** 命令完成倒计时、commands[]、completed、busy_reads、region{base:{offset:word}}+region_writes）、DcuCmoProjection（记录）、L1PIdle/IcuIdle（无状态 fixture，只读回常量——排除，静态） | CRC 的 crc 值与 ICU 命令倒计时是「事件队列」的外设侧投影：事务中途恢复必须续算 |
| 复位历史族 | ResetHistoryProjection（initial_words 静态+**words{地址:PartialWord}** 运行态+**flag_clear_commands** 已观测清标志集+掩码集静态） | words 的 known/value 演进是状态 |
| 模型桩 | WatchdogProjection（progress 静态+advance 计数）、DebugExecutionProjection（registers{EMU_CON×2}+checks+violation）、MPUConfigurationProjection（wren+registers{槽字}+key_writes）、MMU（见 M6）、GpcntConValue/GPCNTCleanup（bits PartialWord+pending+disable/clear_writes+con_reads） | 全存（L1P/IcuIdle 除外） |

## M5 双核机器（DualCoreMachine）

| 状态 | 字段 | 存/排除 |
|---|---|---|
| 核表 | `cores`（0/1→CPU 对象，M1；CPU1 是否已 launch 由键存在性表达） | 存（存在性+内容） |
| 调度 | `last_core`、`idle_wait_cores`（int 集）、`stalls`、`launches`、`deferred_core1_turns`、`seen_cpu1_control_kicks` | 存 |
| 交付簿记 | `software_irq_deliveries[]`、`core_tick_deliveries[]`、`timer0_irq_deliveries[]`、`irq_delivery_deferrals`+`irq_delivery_deferral_events[]`（有界 64） | 存（one-shot 投影的「已交付」判定读它——丢了会二次交付） |
| 事件日志 | `events[]`（有界 32）+`event_count`（真实总数）、`boot_anchors[]`（有界 16）+`boot_anchor_count` | 存（有界列表+**越界总数**两件套） |
| 首任务对照 | `first_task_calls`、`first_task_returns`、`first_task_setup` | 存 |
| 事件时钟 | `event_clock_advances` | 存 |
| 静态 | 构造器全部 opt-in 布尔/fixture（bootstrap_rti、software_irq_entry…）、`factory`/`hardware` 引用 | 静态配置/重建接线 |

## M6 MMU（BR28Model.mmu）

`con`（None/0/1）、`begin`、`end`（页表窗）、`walks`、`last_walk`——全存 [代码 MMU 类]。
页表**内容**本身在 M2 的 ram（0x120000..0x122800 窗）；MMU 无 TLB 缓存状态（report 明言
无 cache/timing 模型）。

## M7 运行循环账本（explore 闭包）

`adapters`（Counter——任务卡点名的「adapter 前缀计数」即此：如
parallel_byte_store_old_registers 104→105 的那类计数）、`events[]`+`event_counts`
（有界+总数）、`rom_steps`、`milestones`（无界，watch_pcs/watch_ram 记录）、`trace[]`
（有界 64）、`completed`、`stop`、`use_architectural_models`/`architectural_model_names`
（排除：由 machine is not None 推导）。除排除项外**全存**——报告逐字节等价（§3）要求
它们跨恢复点连续。[设计] 捕获通道：post_stop 签名现状只给 (machine,hardware,cpu)，
实施路需把账本句柄暴露给捕获回调（向后兼容：machine 属性或第四个可选参数，默认不暴露
=legacy 行为不变）。

## M8 静态配置（不进快照、按指纹重建）

模块对象（upstream PIN）、app/flash/rom 字节（digest 校验）、handoff regions/seeds、
diagnostics（含全部 exact_* gate 布尔与 watch 表）、BR28Model/DualCoreMachine 构造参数
（开关集+fixture 值）、budget。重建方式=重走 irq_case→explore 构造链后 `budget=0`
式进入循环前覆盖状态（§2.5）。

## M9 清单外显式排除汇总（防「顺手多存/漏存」）

| 排除项 | 理由 |
|---|---|
| CPU.cache / mutable_decode_bytes | 语义透明 memo（M1 论证；成对排除） |
| CPU.models / CPU.tab | 代码指纹重建 |
| architectural_model_names | 由 use_architectural_models 推导 |
| L1PIdle/IcuIdle | 无状态常量读回 |
| handoff/app/rom/flash 字节 | 文件+digest |
| explore 的 dispatch_delivery | 纯函数，无状态 |

**[设计] 漂移绊网**：序列化器对每个对象类同时落一份「属性名清单（manifest）」；恢复时
先断言 manifest 与当前类的实际属性集相等，不等=该类状态形状变了（新增/删除字段），
拒绝恢复并报类名——把「模型演进后旧快照缺新状态」从静默错误变成显式失败（codex 漂移
风险的机制化回答）。

# 2. 快照点选择、大小与格式

## 2.1 快照点 [设计]

| 点 | 用途 | 成本/收益 |
|---|---|---|
| **canonical：条件前沿 3,137,573 @ 0x60719c0**（p11_heap_experimental_config 全栈） | 三消费者的共同起点：D2-b 注入（世界就停在这）、键链/反例的 post_stop 段、D1 验收续跑 | 生成一次 3 分钟；此后全部秒级 |
| 次级：主前沿 289,849（crc_bankb 栈） | restore 机制的第二个对照世界（更短前缀、同构造链），等价测试矩阵加一档 | 生成 ~17s；ROI 中，实施路可选 |
| 任意步停点 | `budget=N` 即得 instruction_budget 停点快照；等价测试的中间取样点（§3.2） | 按需 |
| 镜像变体（b79/EQ） | L2 三镜像差分若要快照化，每镜像一份；**首期不做**——L2 parity 本身就是全前缀重放纪律，快照化必须先过 §3 的 advance-tier 等价，避免快照把镜像差异吞掉 | 按需，挂账 |

多点历史的取舍：历史停点（149,660/176,288/…）只在等价测试矩阵用作低成本样本，不生成
常驻 artifact——常驻快照的维护面（每次模型演进再生成）随点数线性涨。

## 2.2 大小估算

RAM 已初始化字节 ≈ 70–115KB（M2 表逐窗合计：全初始化窗 65KB + 栈/堆/向量表部分
15–50KB）；外设投影+机器+账本 JSON 化 ≈ 50–150KB（stores 值序列小、milestones 可能数
千条）；CPU 寄存器/抽象值 <2KB。**合计 <0.5MB（全 JSON）或 ~0.15MB（RAM 段用字节流）**。
结论：无需压缩或增量设计，整文件读写即可。

## 2.3 指纹与版本 [设计]

快照 header 必含：`format_version`（本格式版本）；`code_fingerprint` = SHA256 over
（tools/probe_boot.py、br28_model.py、br28_machine.py、br28_icfg.py、br28_unknown.py、
probe_irq_entry.py、probe_dualcore.py、probe_platform_startup.py、probe_arch_startup.py、
probe_clock_startup.py、probe_boot_handoff.py、frontier_registry.py 的文件内容 + upstream
PIN + scenario_flags 序列化 + FRONTIERS 契约记录）；`capture_context`（场景名、
completed_steps、stop 记录、开关清单）。恢复时指纹不符=拒绝加载并提示再生成（3 分钟）。
属性 manifest（§1 M9）作第二道闸。**不试图做跨版本迁移**：模型演进=再生成，迁移器是
负 ROI（对照测试会立刻抓住任何迁移遗漏）。

## 2.4 序列化格式：自定义分层文件，**否决 pickle** [设计]

```
snapshot 文件 = header(JSON) + manifest(JSON) + body(类型标签 JSON) + ram(字节段)
```

- body 用通用反射 walker（从五元组+账本句柄出发，收集 (类名, 排序属性名, 值)），值域
  白名单：None/bool/int/str/bytes(hex)/list/dict + 五种抽象值对象的显式类型标签
  （UnknownWord/IcfgValue/GpioOutValue/GpcntConValue/PartialWord）。walker 遇到白名单外
  类型即抛错——新状态种类自动显形（与 manifest 互为冗余防线）。
- ram 段按 M2 窗口做 run-length：`(起始,长度,字节流)` 的已知段序列 + unknown_origins/
  icfg_spills 单独小节；窗内「已知段之间的洞」=未知字节，格式天然保缺席语义。
- 否决 pickle 的四条理由：① CPU 类定义在 explore 闭包内，跨进程 pickle 需要把闭包类
  注册进 sys.modules，脆弱且依赖实现细节；② 上游 sim 模块以动态名加载（'h3s_isa_sim'），
  pickle 绑定其类布局，上游 PIN 一动整批快照无声损坏；③ pickle 恢复对增删属性静默容忍
  ——恰好放走 codex 点名的漂移；④ 自定义格式可 diff、可审计、可手工检查（repo 的证据
  纪律需要「能看清快照里有什么」）。

## 2.5 捕获与恢复的接入形状 [设计]

- **捕获**：post_stop 钩子式（S 路先例）——场景传 `snapshot_capture=path`，explore 在
  停点后调用捕获器，产出文件+摘要（digest、completed_steps、stop、指纹）。
- **恢复**：explore 增加恢复入口（如 `snapshot_restore=path, resume_budget=K`）：完整
  走构造链（M8 重建）→ 循环开始前用快照覆盖五元组+账本 → 断言指纹/manifest → 进入
  正常主循环跑 K 步 → 照常组装报告（账本来自快照，报告天然与完整重放同构可比）。
  消费者也可在恢复后不进循环，直接拿活世界做 post_stop 式直调/注入（§4 ②）。
- **自反检查 #0**：恢复后 0 步立即再停——应重现原 stop 记录与原报告（世界停在变更前的
  干净边界 [代码 Blocked 语义]，同一条指令再次抛出同一停点）。这是成本最低的缺状态
  探测，进 fast tier。

# 3. 一致性验证设计（codex「与完整重放对照」的自动化）

## 3.1 等价定义

同进程、同代码指纹、同 PYTHONHASHSEED（保险，见 §3.4）下：

> 对多个前瞻步数 X：`full(X)`（场景完整重放到 X）与 `snap(N)+resume(X)`（N<X 快照、
> 恢复续跑到 X）产出——①报告 JSON 逐字节相等；②内存全景摘要相等（ram 段 SHA256 +
  unknown_origins/icfg_spills 规范序列化 SHA256）；③两世界对象属性 manifest 相等。

对照字段即报告全集：stop、completed_steps、per-core steps、machine.report()、
peripherals.report()（含 counts/writes/各投影子键）、last_instructions、
instruction_adapters、memory_event_counts、milestones。**不允许 diff 白名单**：任何字段
不等=恢复缺状态或恢复注入了多余状态，修清单而不是掩差异。（首次实施若发现报告专用的
派生字段确有不可比性，须逐字段论证并记录在本文件修订里，不得静默。）

## 3.2 三层测试 [设计]

| 层 | 内容 | 进哪 | 成本 |
|---|---|---|---|
| fast tier | ①自反检查 #0；②小世界往返（budget 2–20k 步的探针世界：快照于中点，恢复到终点，对照完整直跑）——专测捕获/恢复/manifest 机制本身 | `check_local --units`（新 test 文件，秒级，无需私有 ROM——小世界用公开资产可构造的停点） | 秒级 |
| artifact tier | 每生成一个 canonical 快照（3,137,573）：X 取 {+1 步, +10k 步, +100k 步} 三档跑 §3.1 全量对照；结果（PASS/FAIL+digest+指纹）落 evidence 摘要 JSON（只含摘要，无 RAM 字节） | 手动/慢层（仿 test_falsification --scenario-evidence 形态），分钟级、每快照一次 | 分钟级一次 |
| advance tier | 前沿推进 checklist（frontier_registry 注释四步）追加第五步：**新开关世界必须重跑一次 snapshot-resume vs full replay**——依据是 repo 的「默认关=legacy 前缀逐字节不变」纪律：新开关不改 N 步前的前缀，故旧快照可续用；但「新开关只影响停点之后」本身要用这次对照证明 | 前沿推进流程 | 分钟级一次 |

## 3.3 反例自检（AZ 模式嫁接）

等价测试自身按 falsification-checks 方法论加两条反例：①**故意删状态**（walker 跳过
某类，如 core_ticks 的 remainder）→ 等价测试必须红（证明对照有分辨力，非空真）；
②**故意注入偏差**（恢复后篡改一个 RAM 字节）→ 必须红。这两条进 fast tier。

## 3.4 确定性边界

行为关键集合全为 int 集（M5）；str 哈希随机化只影响报告输出的集合排序处——报告侧已有
sorted() 输出的字段保持，未排序处由「同进程对照」消除。跨进程使用（快照生成进程 ≠
恢复进程）时 PYTHONHASHSEED 钉 0。已知非确定性输入：无（无随机、无时间、无环境读）。
B-1 非幂等 advance（停止后重试 advance 烧定时器计数）：恢复语义=从循环头重新 decode，
不补调 advance，与完整重放在同一步序上对齐——等价测试的 X 档天然覆盖此边界。

# 4. 三消费者接口

## 4.1 消费者 ①：场景级反例秒级重放（AZ B-3）

**适用面（诚实边界）**：快照加速的是「**停点后**破坏」的反例——世界前缀（到
3,137,573 为止）不动，破坏发生在停点之后的行为面：

| 反例 | 破坏方式 | 接口 |
|---|---|---|
| 键链阈值改带（R3 型） | 恢复世界→改分档器 fixture 档值→跑 post_stop 扫描段→断言 posts 不可达 | `--from-snapshot` 跑 make_key_chain_post_stop 系 |
| RES 档位值破坏 | 恢复→篡改 res_named_values 键值→采样事务断 sample_mismatch | 同上 |
| IRQ68 完成抑制（AZ B-4，D2-b 落地后） | 恢复→注入循环里不发完成事件→断言音频场景诚实停 | AW 蓝图注入循环的反例变体 |
| 交付机制破坏（停点后段） | 恢复→post_stop 直调段内撤 Timer0/IRQ68 交付接线→断言停 | 同上 |

**不适用面**：前缀分歧型反例（R1 撤 RES fixture→前沿退 304,406；R2 撤 Timer0 交付→
336,337）——撤开关改变的是前缀本身，只能完整重放（3 分钟）。这不是快照的缺陷而是
定义：快照世界 ≡ 原开关集的原前缀。test_falsification 文档化两层的分工：慢层保留
（前缀依赖证明），`--from-snapshot` 层新增（停点后破坏，秒级，可频繁跑）。

## 4.2 消费者 ②：D2-b 注入实验的「回到停点」循环（AW 蓝图 P1-b 升级）

AW §1.1 的 P1-b（停在旧前沿+直调补齐）现状每变体重建 3M 世界。升级后：

```
snap = Snapshot.load(canonical)               # 3,137,573 停点
for variant in variants:                      # esco/辅听/ANC×配置切换×注入参数
    world = restore(snap)                     # 秒级；world=(machine,hardware,cpu0,cpu1,mem)
    world_prep_assert(world, expected)        # 变体隔离断言：注入前状态 digest 相等
    inject_and_deliver(world, variant)        # produce_block/IRQ68 交付/post_stop 直调面
    assert variant.b1_b4(world.report())
```

变体隔离断言是关键新增能力：注入实验结论的效力依赖「各变体起点相同」——现状只能靠
「同一进程顺序跑+每变体重放」；快照后起点相同是可断言的（digest 相等）。AW 蓝图的
P1-b 保持为快照落地前的回退路径；§2 落地后在蓝图修订里把 P1-b 改注「首选=快照恢复」。

## 4.3 消费者 ③：D1 验收断言的可重复重放（codex「确定性重放」门槛）

- D1 首次过线时，在 S19 park 点（或 D1 观察窗起点）生成 **D1-entry canonical 快照**。
- 之后每次 D1 断言重放（回归、模型演进后的复核）= 恢复 D1-entry 快照→重放观察窗→
  逐断言核对，秒-分钟级（观察窗本身 ~447k 步级的事现在是窗口内步数而非 3M+窗口）。
- **门槛**：快照态 D1 断言只在 §3 advance-tier 等价（含新开关世界）绿时有效；等价红
  =回退完整重放并修快照。codex 的「确定性重放」= 同代码指纹+同快照 digest→同报告，
  由 §3 机制交付，不靠口头保证。
- L2 三镜像差分（BR 谱系）与 D1 快照的关系：L2 保持全前缀重放（§2.1 挂账），直到三
  镜像各自快照都过 artifact-tier 等价，才允许镜像内快照化。

# 5. 风险与工作量

## 5.1 风险表

| 风险 | 缓解 |
|---|---|
| 快照漂移（模型演进旧快照失效） | 双闸：代码指纹（§2.3）+属性 manifest（§1 M9）；失效=拒绝+再生成（3 分钟/点）；不做迁移器 |
| 缺状态→「另一套启动条件」（codex 原警告） | 状态清单 M1–M7 全存+白名单外类型抛错；§3 等价对照是唯一合法性证明；§3.3 反例证明对照有分辨力 |
| B-1 非幂等 advance 破坏恢复点 | 恢复点只在干净停边界（Blocked 变更前抛出/budget 尽）；恢复不补调 advance；X 档对照覆盖 |
| 报告不可比字段 | 首期逐字节硬对照；发现不可比=显式论证+修订本文件，无静默白名单 |
| 快照内容泄密（RAM 镜像/私有 ROM 派生字节进 git） | 快照文件只落 analysis/（.gitignore 全目录）；进库的只有 evidence 摘要（digest+指纹+PASS/FAIL）；沿用「Never publish full RAM/event mirrors」红线 |
| 维护面膨胀 | 常驻 artifact 仅 canonical（+可选主前沿）；镜像/历史点按需；advance-tier 一步进前沿 checklist |
| 集合迭代序/哈希随机化 | 同进程对照为主；跨进程钉 PYTHONHASHSEED=0（§3.4） |

## 5.2 分步实施（每步独立可验收，均须 check_local --units 绿）

| 步 | 内容 | 产出 | 量 |
|---|---|---|---|
| S1 | 序列化器+捕获（walker/manifest/header/ram 段；post_stop 捕获钩子；自反检查 #0） | canonical 快照文件+digest；fast tier 自反绿 | 0.5–1 天 |
| S2 | 恢复+resume 入口+等价对照（fast tier 小世界往返+反例两条；artifact tier 三档 X） | 等价 PASS 记录入 evidence 摘要 | 1–1.5 天 |
| S3 | 消费者接线：②D2-b restore 循环（AW 实施时一并）、①falsification --from-snapshot 层、③D1-entry 快照（D1 过线时） | 各消费者秒级路径+文档挂账 | 每消费者 ~0.5 天 |

合计 ≈ 3–4 个工作日（不含 D2-b/D1 本体的既有工作量）。先只读快照+对照（S1/S2），
恢复（S2）与消费者（S3）分层推进——S1 单独就有价值（快照即世界全景审计文件）。

## 5.4 S1 模块半完成状态（2026-11-14 11:21 路 BJ/sim-snapmod，基线 dbfcc16）

S1 拆成两半推进（与 BF 前沿路并行不冲突）：**模块半已完成**（序列化框架+fast tier
自反，纯新增 `tools/world_snapshot.py` + `tools/test_world_snapshot.py`，不碰
probe_boot/probe_irq_entry/br28_model/br28_machine 等既有文件）；**捕获接线半未动**
（见下）。模块半能力清单：

| 能力 | 状态 | 锚点 |
|---|---|---|
| 分层格式编解码（magic+版本+header/manifest/body JSON + RAM 字节段二进制框架） | 就绪 | world_snapshot.WorldSnapshot.encode/from_bytes/save/load |
| 类型标签值编码（五种抽象值显式标签 + $pairs/$set/$counter 集合标签；白名单外类型抛错） | 就绪 | encode_value/decode_value；集合标签是对 §2.4 白名单的记录在案的扩展（M5/M7 的 set/Counter/int 键映射需类型精确往返） |
| RAM run-length 编解码（已知段合并排序、段间洞=未知字节、unknown_origins/icfg_spills 小节） | 就绪 | encode_ram_runs/decode_ram_runs/encode_ram_section |
| 漂移双闸：①代码指纹（§2.3 源文件 sha + upstream PIN + 格式版本 + 适配器形状）②manifest（从 body 派生的形状校验和 + restore 侧与当前适配器字段表对照），不匹配拒载并报类名 | 就绪 | code_fingerprint / derive_manifest / from_bytes / restore_world |
| 适配器注册表（M1–M9 各一个 capture_fn/restore_fn 注册点；M4/M8/M9 显式登记为「设计性缺席/接线半」） | 就绪 | register_module / MODULES |
| 小世界最小实现：M1 平面 CPU 桩（15 字段）、M2 三字典 Memory、M3 Timer0/双 core tick/双 IRQ 控制器/软件 IRQ/寄存器与 GPIO 投影/标量簿记、M5 机器交付簿记（20 标量+core 存在性）、M6 MMU、M7 循环账本 | 就绪 | tools/world_snapshot.py 各 _capture_m*/_restore_m* |
| 静态开关守卫（捕获时对未被捕获、非 fixture 的属性种类拒绝并报名字；恢复时静态视图不一致拒绝——「另一套启动条件」的机制化防漏） | 就绪 | encode_static_value/check_static；M4 投影族适配器落地（§5.5）后，守卫下沉到每投影对象一级（适配器外的属性种类仍显形拒载） |
| round-trip 自反（检查 #0 的模块半形态：恢复零步逐字段相等 + 重捕获字节同一） | 就绪 | assert_equal + test_world_snapshot.SmallWorldRoundTrip |
| fast tier 测试（22 例：小世界往返+逐字段比对+字节同一；反例两条——漏应用字段/注偏差必须红；指纹闸/manifest 闸/篡改拒载；run-length 边界含洞语义；白名单拒绝） | 就绪 | tools/test_world_snapshot.py，进 `check_local --units`（check_local 一行接线） |

已记录的格式决策偏离（模块 docstring 同步）：捕获时场景开关进 header.capture_context
而非指纹（load 侧重算指纹拿不到捕获时上下文），消费者经 restore_world(expected_context=)
拿到场景闸。

**捕获接线半剩什么**（BF 合并后的下一路）：
1. probe_boot explore 循环的捕获点（post_stop 式钩子，§2.5；账本句柄暴露，M7 的
   「向后兼容第四可选参数」方案）；
2. M1 完整字段清单（执行上下文/repeat/after_then/银行簿记/任务帧/IcfgState——最小
   15 字段清单已在注册表，扩展即改 M1_CPU_FIELDS，旧快照自然被双闸拒绝再生成）；
3. ~~M4 约 25 个投影族类的适配器~~ **已完成**（路 BL/sim-snapm4，2026-11-14：
   31 槽位/30 类/121 状态字段全覆盖，含一次性事件位与进位状态；见 §5.5）；
4. 小世界往返升级为「快照于中点、恢复到终点、对照完整直跑」的 fast tier 全量形态
   （§3.2；等价对照=恢复合法性证明仍是接线半的验收，本路不声称）；
5. canonical 快照生成 + artifact tier 三档 X 对照（§3.1/§3.2）。

## 5.5 M4 投影族适配器完成状态（2026-11-14 路 BL/sim-snapm4，基线 5b0f59d）

路 BJ 留下的接线路清单 ③ 前半：**M4 投影族适配器全量落地**（纯新增 world_snapshot
侧，不改 br28_model/probe_*）。真实世界的 M4 投影对象不再触发 M3 static guard 拒载。

| 能力 | 状态 | 锚点 |
|---|---|---|
| 31 个 BR28Model 槽位（30 类：写记录 12 + 位记录 3 + 一次性事件 5 + 进位状态 3 + 复位历史 1 + 模型桩 4 + 无状态 idle fixture 2；SFCReadBus 双槽）×121 个状态字段，逐槽 capture/restore | 就绪 | world_snapshot.M4_FAMILIES/_capture_m4/_restore_m4 |
| 每对象静态守卫：构造 fixture/开关（initial_*、投影开关布尔、first_read_words、ICU initial/command_steps、trim/RES fixture、config_masks…）记为 static、恢复时逐对象比对（M3 hardware 守卫的每对象版） | 就绪 | capture_static/check_static（M4 每槽位调用） |
| 开关一致性：槽位 None/非 None 双向拒载（快照有态而目标关、或反向） | 就绪 | _restore_m4 |
| 一次性事件位语义保持（lpwr requests armed、awkup/canary 首读 fixture 已消费、efuse 事务 page/byte/active/read_done） | 就绪，进 fast tier | test_world_snapshot.M4ProjectionFamilies.test_one_shot_event_bits_stay_consumed_after_restore |
| 进位状态精确恢复（CRC 累加值续算一致、ICU 命令倒计时 advance 同步） | 就绪，进 fast tier | …test_carry_state_resumes_exactly |
| 类型标签：无需新标签——全部状态值落在 BJ 白名单（五种抽象值 + $pairs/$set/$counter/$tuple）内；新增 encode_value_pairs（任意深度 int 键 dict 自动 $pairs 化，ICU region 嵌套映射用），capture_object 增加 encode= 注入点 | 就绪 | world_snapshot.encode_value_pairs |
| fd31 res_named_values 按设计归状态（键链场景运行时可变）而非 static | 就绪 | M4_FAMILIES fd31_adc_con 行 |
| 顺手修复 BJ encode_static_value $pairs 分支丢弃值的潜伏 bug（M3 静态从未踩中，M4 的 int 键 fixture 首次触发） | 就绪 | world_snapshot.encode_static_value |
| fast tier 新增 6 例（全族往返+字节同一/一次性位/进位/开关双向拒/漏字段必须红+漏恢复必须红/静态 fixture 拒）+1 例改写（M4 拒载测试改为「投影对象上适配器外新状态显形拒载」） | 就绪 | tools/test_world_snapshot.py（28 例，进 --units） |

剩余接线路清单：①explore 捕获点、②M1 完整清单、④小世界中点往返全量形态、
⑤canonical 快照+artifact tier（等价对照仍是恢复合法性的唯一证明，M4 适配器本身
只承诺字段级往返与行为语义保持）。

## 5.3 与既有机制的关系（不重复造轮子）

- post_stop 直调（S 路）/dispatch_delivery（BE）/事件时钟 advance 同循环（AB/BI）——
  恢复世界复用全部这些机制，快照层不新增任何执行语义；
- stash 对照法（各路报告逐字节不变检查）——§3 等价对照是它的自动化近亲（对照对象从
  「改动前后报告」变成「重放 vs 恢复续跑报告」）；
- frontier_registry 四步 checklist——advance-tier 等价作为第五步并入，不另立流程。

# 6. HANDOFF 条目（同步落档）

- 路 BL（sim-snapm4，2026-11-14，基线 5b0f59d）：**M4 投影族适配器全量**完成（BJ
  接线路清单 ③ 前半，纯新增）——主交付 tools/world_snapshot.py 的 M4_FAMILIES 表
  （31 槽位/30 类/121 状态字段：写记录 12、位记录 3、一次性事件 5、进位状态 3、
  复位历史 1、模型桩 4、无状态 idle fixture 2）+_capture_m4/_restore_m4+encode_value_pairs
  （任意深度 int 键 dict 自动 $pairs 化；无新增值类型标签）；每对象静态 fixture/开关
  守卫与槽位开关双向拒载；fd31 res_named_values 按设计归状态。顺手修 BJ
  encode_static_value $pairs 分支丢值潜伏 bug。fast tier 28 例（+6 新、1 改写：
  M4 拒载测试改为投影对象上适配器外新状态显形拒载），--units 绿。真实世界的 M4
  投影对象不再触发拒载；等价对照（恢复合法性）仍是接线半 ④⑤ 的验收。清单剩余：
  ①explore 捕获点、②M1 完整清单、④小世界中点往返全量、⑤canonical+artifact tier。
  前沿不变。
- 路 BJ（sim-snapmod，2026-11-14 11:21，基线 dbfcc16）：**S1 模块半**完成——序列化
  框架+适配器注册表+漂移双闸+fast tier 自反，主交付 tools/world_snapshot.py（新）+
  tools/test_world_snapshot.py（22 例，进 --units）+check_local 一行接线+本文件 §5.4
  状态清单。不碰 probe_boot/probe_irq_entry/br28_model/br28_machine；捕获接线（explore
  捕获点/M1 完整清单/M4 投影族/等价对照）留给 BF 合并后的下一路（§5.4 尾清单）。
  前沿不变。
- 路 BD（sim-snapshot，本次合并，设计路不改 tools/）：**快照/恢复机制设计蓝图**完成，
  主交付 [docs/snapshot-design.md](docs/snapshot-design.md)。动机=codex 复盘+AZ B-3
  （场景级实验 3 分钟/次进不了反馈环）。蓝图内容：①世界状态全清单 9 模块（双核 CPU
  抽象值/共享内存缺席语义/外设投影六族/机器交付簿记/循环账本 adapter 计数；解码缓存
  等可推导项显式排除+论证）；②快照点=条件前沿 3,137,573 canonical，大小 <0.5MB，
  自定义分层格式（否决 pickle：闭包类/上游动态模块耦合/静默漂移/不可审计）；③一致性
  对照三层（fast 自反+小世界往返进 --units；artifact 层每快照三档 X 逐字节对照完整
  重放；advance 层并入前沿 checklist 第五步）+反例自检（删状态/注偏差必须红）；
  ④三消费者接口（停点后反例秒级——前缀分歧型仍走慢层；D2-b restore 循环+变体隔离
  digest 断言；D1-entry 快照以等价绿为门槛）；⑤风险表（漂移双闸=代码指纹+属性
  manifest；快照文件仅落 analysis/ 不进 git）。实施分三步 ≈3–4 天，S1 只读快照先行。
  主前沿/条件前沿均不变。

# 7. 红线核对（本路自查）

- 未改 tools/ 任何文件（本文件为 docs/ 新增，git 状态可查）；
- 状态清单全部读自基线 87a0cc3 源码（[代码] 标注类/函数锚点）；既有机制（post_stop、
  dispatch_delivery、事件时钟、stash 对照、adapter 前缀计数、frontier_registry
  checklist）如实纳入设计未重复发明；
- 快照内容边界遵守「Never publish full RAM/event mirrors」：设计规定快照文件只在
  本地 analysis/，进库证据仅摘要 digest；
- 提交不含 Co-Authored-By、不含私有路径/私有工件名；
- `check_local.py --units` 绿（文档路，已跑）。
