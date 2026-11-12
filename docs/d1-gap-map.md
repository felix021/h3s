# 从 CPU1 release 边界到 D1 事件循环：前缀衔接与缺口地图（路 BC 只读调研）

2026-11-13。本页是路 BC（sim-d1gap）的调研结论：钉死 p11_ready_watchdog 支线（127,239 release 边界）与 dualcore/irq_entry 主条件线（303,853）的机器状态衔接关系，列出从 release 边界到 D1（事件循环）的逐边界缺口清单，并给出按 ROI 排序的后续路线建议。**只读调研**：未改任何 tools/ 代码、未跑任何会改报告的 probe；分析只用了反汇编（本机脚本）与既有报告（analysis/local-checks，git 忽略）。路线建议≠实现决定，每路落地前仍走证据核验先行的三选一。

## 1. 前缀衔接现状（钉死）

**结论：三条线共用同一条 CPU0 真实启动前缀，在 127,239 处已逐字段互证；「从 127,239 继续跑会发生什么」就是主条件线已经跑过的那段——不存在两条独立机器状态，不需要新的衔接机制。**

- 支线（probe_mmu `p11_ready_watchdog`）、platform（`cpu1_release_boundary`）、dualcore/主条件线（probe_dualcore / probe_irq_entry，经 `platform_case`→`architecture_case`→clock startup 栈）各自从应用入口冷启动跑**同一份真实固件指令流**。路 AY 已把支线与 platform 在 127,239（PC 0x60660da、kind `cpu1_release_not_implemented`、checked_executing_fetches 254480、debug 十键、MPU 四槽、cpu1_entry f4ca1200、cpu1_ready 00、task_create 里程碑 0x6035356 r0=0）逐字段互证；dualcore/主条件线的 `dual_case` 就是 `platform_case(machine=DualCoreMachine(...))`（tools/probe_dualcore.py:34），同一机制、同一参数族，只是把 release 处的单核诚实停换成 DualCoreMachine 的 release 实现。
- **CPU0 侧前缀是真实的，不是合成的**：从入口到 release 的 127,239 步全部由真实指令执行推出；显式 fixture 只覆盖硬件输入（port/reset/debug/MPU 等虚拟输入字），从未合成 CPU0 寄存器/内存状态。release 后 CPU0 继续真实执行（os_start→调度等待，dualcore_first_task 场景里 CPU0 到 136,403 步）。
- 两条 harness 的投影栈是**独立建造**的（支线用 probe_mmu 自己的 handoff profile + l1p_idle/icu_idle 等细粒度投影；主条件线用 architecture_case 的 cache_status_probe 等同覆盖集），这正是互证的价值：两套独立栈推出逐字节相同的前缀。

### 「从 127,239 继续跑」逐步对照（dualcore 域已走过的每一步）

| 步骤 | 性质 | 停点/锚点 | 显式 fixture/假设（未验证项） |
|---|---|---|---|
| release RMW `0x60660da \|=1`（0xffe8c0 CPU1 PMU_CON0） | 真实指令 | DualCoreMachine 由此创建 CPU1 | release→reset 因果、模式、栈 bank（AGENTS 钉死的未验证条件） |
| CPU1 从 PC0 起 25 条 ROM 指令 | 真实执行 | 0xa120→0xa144（核号）→0xc5fe（读向量 0x19fef8=0x12caf4）→0xc602 CALL（RETS=0xc604） | 初始 reset-PC=0、初始 ICFG 完整字 0（显式 fixture） |
| QCY trampoline 3 条 + 0x12cb06 RTI | 真实执行 | USP/SP=0x124840/RETI=0x12cad4 | 一次性 bootstrap RTI 投影（锚点全匹配才生效）；post-RTI SSP 与 gates8/9 未知 |
| ICU 命令 0xffec10=0xca000010 | 真实执行 | 14 次 busy 读后 CON=0x80000001 | 64 全局步完成（显式步数 fixture，非测量延迟） |
| CPU1 ready 写 0x606602a→0x1265f5=1、0x6066034→0x19ff54=1 | 真实执行 | 固件自己写 ready，无注入 | — |
| 调度选 TCB 0x127e10→帧 VA 0x401bb0/backing 0x19f630、12B 特殊帧恢复 | 真实执行 | RETI 0x12c57e/RETS 0/PSR 0、R0 pdata=0 | CPU1 栈区 0x123840..0x124840 为 harness region |
| pop.rmask 恢复 R1–R15 | 抽象运输 | 147,263 strict（读未初始化 0x19f640 停）/147,268 abstract | UnknownWord 逐字节来源运输；不含数值消费放行 |
| 0x606612e STI 后 0x6066130 task RTI | 反事实假设 | `first_task_return_hypothesis='open_gate8'` | hardware_mode_certified=false（probe_task_returns 五停点钉死） |
| CPU1 idle 0x12c5ca | 投影 | idle_wait_projection（核停发指令直到真实交付唤醒） | IDLE 硅语义未测量 |
| irq127 调度交付（149,660→153,430） | 投影 | 向量槽读真实 RAM、RETI 装载、pending 交付时清除、入口银行（USP=被中断任务栈/SSP=最后显式）、对称一次性 IRQ RTI | 全部显式假设（AGENTS/scheduler frontier 条目） |
| irq125 同步交付（155,127）→CRC→bankB→power_init 链（→303,853） | 投影+真实执行混合 | 主条件线「已实现」清单（路 G..AZ，约 25 个 opt-in 边界） | 每路各自标注 |

也就是说：**调研背景里问的「dualcore 场景的输入前缀是真实的还是合成的」答案是——CPU0 前缀真实（同支线），CPU1 侧是「真实执行 + 五件显式 fixture」（reset-PC0、初始 ICFG0、bootstrap RTI、ICU 64 步、栈区映射），全部已钉死在 dualcore-startup.md 的未验证条件表里。**

## 2. 127,239→D1 缺口清单

### 2.1 当前前沿机器状态（303,853 @ 0x6067c6e，p11_heap_experimental_config 报告实测）

- CPU0：292,689 步，PC 0x6067c6e（app_core 0x60120e2 call 0x6067cb4 的 P33 窗 walker 内、第二轮表 item[0] 的 P3_AWKUP 次路径），SP 0x400b74；CPU1：11,181 步，PC 0x12c5ca（idle 边界，defer 4096 耗尽、尚未执行 idle 指令）。stalls 2,155、launches 1、kick_rmw_writes 21、软件 IRQ commands 20/acks 10、交付 1+7+2。
- **Timer0**：con 0x1401（STD12M 计数中）、cnt 8,268、prd 119,999 → **首次到期 ≈ 341,097 步**（+37,244；与路 AH「启动后 40,000 步」推算一致：301,097+40,000）。
- **CPU0 core tick**：con 1、cnt 625,452、prd 1,199,999 → **首次溢出 ≈ 447,490 步**（+143,637）；CPU1 core tick cnt 640,960/prd 479,999,999（≈1.2 亿步外，任何预算不可达，事件时钟下也永远排在 CPU0 之后）。

### 2.2 未走边界（303,853→D1，按链上顺序）

| # | 边界 | 停点预测（PC/kind） | 所需投影/假设 | 与既有场景重叠度 | 风险 |
|---|---|---|---|---|---|
| B1 | P3_AWKUP 族读回（FLT0..2 写+EN/P_IE/N_IE/LEVEL/INSEL/P_PND/N_PND/P_CPND/N_CPND RMW） | 0x6067c6e 放行后次路径内下一未映射访问；族访问面 0xf2c180..0xf2c1a0、0xf2c200..0xf2c20c | codex 已定调：具名零值 fixture（opt-in、只覆盖首次读、不扩展全族）+写侧记录 | P33 族三先例（p33_analog/p33_window/port_sel）同域 | 读回复位值无 SDK 类型证据（p33_sfr.h 纯地址宏）；0x6067cac 读 P_IE 被消费 |
| B2 | P3_LS_* 六清零（0xf2c340..354）、[0xf2a638]=0、[0xf2a628]&=0xFFFFFF7F、walker 清尾 4 字节 0xFF | walker 0x6067cb4 返回前 | write-only 整字记录（lp_timer SYS_DIV 同族）+0xf2a628 掩码集扩展（路 AO 先例）；AV 投影已覆盖清尾地址 | 高（全部证据已钉死，路 AZ 条目） | 低 |
| B3 | P33 尾部两函数 0x6067d6a/0x6067d7a（`[0x129d7c]=0x6064d0c`/`[0x129d80]=0x6064d24` 装回调指针） | 无停点预期（RAM 全局写，区已映射） | 无 | 高 | 低 |
| B4 | app_core 资源 getter 链（0x6001230/0x601160e 重复对、ROM 0x1fd80）、0x6065d86 向量槽安装（0x19fe00+irq*4，区已映射）、任务创建 0x6066c26×2+0x6067242+ROM 0x12cc40 | 每个新资源/向量/任务一处；任务创建触发调度 IPI 往返（repeat 已开） | 多数走既有机制；新资源条目按 flash_image 只读区 | 高（task create/IPI 往返/向量写全部有先例） | 中（数量未知，可能夹带新 MMIO） |
| B5 | 函数表 walker×5 段（0x60121c0..0x6012254 `call.r` 间接调用） | 表目标为 flash 已知字节，执行应可行；新 MMIO 视表内容 | 无新机制 | 中 | 中 |
| B6 | app_core 尾部大段：0x6064162/0x6064f18/0x6064c8c/0x6064efc/0x606859c/0x6067dda、音频域 0x606dcc6/0x606dc14/0x606dd16/0x606dcd6、OS 域 0x6066a0a/0x60669a6/0x60669f0/0x6066a1a、ROM pend/post 0x12b55c/0x12b5b0 消息循环 | **边界数量未知**——本段是 D1 前最大的不确定段 | 逐边界证据先行；BT/音频外设若被触碰需新 fixture 族 | 低-中（0x606dxxx 域从未到达） | **高**（外部依赖芯片域可能需要大 fixture；0x601249c/0x60124ae 是 power-off 死等分支（call 0x60682a4 清理表 walker 后 goto 自循环），到达即 init 失败金丝雀，建议挂 watch） |
| B7 | **Timer0 IRQ4 交付** @≈341,097 | advance() 里 `interrupt_delivery_not_implemented`（若 IRQ4 合格；不合格则 pending 静置不阻塞） | 向量槽 0x19fe10=0x12ece8（真实 RAM 已写）；handler 28B 软件保存帧形状 gpio-irq 域已钉（docs/gpio-irq.md「clear pending and restore software context」）；W1C bit14 清 pending 已建模；对称 RTI 复用 | 中-高（deliver_core_tick/deliver_software_irq 两套机制可抄） | **已解决（路 BE）**：运行态 write trace 证实驱动安装 request_irq 在 ~110,768 步以 priority 1 真实使能 IRQ4（0xffe100=0x30000→0x33b55，此后无清零）——到期必交付；机制已就位（timer0_irq_delivery_requests+timer0_irq_entry/banks/rti，probe 场景已完成真实 handler 字节往返） |
| B8 | **CPU0 core tick IRQ3 溢出** @≈447,490 | `core_tick_overflow_delivery_unresolved`（主场景栈未开 core tick 交付时） | probe_core_tick 已有 deliver_core_tick/ACK/帧锚（CORE_TICK_FRAMES 0x60661a4/0x60661e2、向量槽 0x19fe0c），**只差接进 irq_entry 主场景栈**（machine.core_tick_entry+core_tick_delivery_requests） | 高（机制全有，纯接线+真实前缀首验） | 低；注意与 B6 的先后（init 链若短于 143k 步则先到全核 IDLE） |
| B9 | **全核 IDLE→事件时钟真实首验** | CPU0 进 idle（调度器无可运行任务）后 `all_cores_idle_waiting`（默认）或事件时钟推进 | `event_clock_projection`（需 idle_wait_projection；契约 docs/event-clock.md） | 高（8 单测+合成双核端到端已有；真实前缀首验是 C2 退出条件：计数→pending→IRQ3/IRQ4→真实 handler/ACK→RTOS 时间/就绪变化→任务恢复） | 中（首个真实 tick 唤醒的银行假设叠加；Timer0 pending 若合格但交付未实现会在推进中严格停——B7 必须先行） |
| B10 | **D1 验收**：事件循环自我维持（连续定时事件+至少一个输入事件分发、可检查状态变化、能处理下一事件） | 无单一停点；验收场景设计 | B1..B9 全部 | 低（新验收框架） | 中 |

### 2.3 横切风险（AGENTS 已列假设的对照）

- **CPU1 GPR 未知**：已由 UnknownWord 运输覆盖（147,268 起），后续消费者仍严格停——D1 链上若事件循环任务读这些槽会在精确位置显形，机制已备。
- **SSP/ICFG 模式**：入口银行假设+一次性 RTI（silicon uncertified）叠加适用；事件时钟契约第 5 节明确「唤醒走既有交付投影，其各自假设叠加适用」。
- **RTI 未认证**：task RTI/IRQ RTI 全部是反事实/一次性投影（hardware_mode_certified=false 恒定）。
- **p11_init 侧依赖**（CON2/PMU_STA/P2M_CLK）：主 init 分支跳过、本前缀未调用；若 app_core 尾部某分支再调 p11_init，p11 ready 投影（支线已验证）需接进主线场景栈——到达时观察。
- **堆信息/erase 态 fixture**：derived-not-measured 措辞维持；路 BA 测量会话与 D1 解耦（条件 fixture 已在用，不阻塞）。

## 3. 两条线的会师点结论

**会师已经发生，就在 127,239。** 支线的价值是以独立投影栈把「真实启动前缀→release 边界」复核到逐字段一致（路 AY 闭合）；主条件线本身就是「从该边界继续跑的那台机器」（DualCoreMachine 跨 release，147,263→303,853 全在这台机器上）。因此：

- 「条件线的任务创建/pmu_task 与支线的 task_create/os_start 是同一台机器的两段吗？」——**是**。支线在 127,239 前缀内钉死的 0x6035356 task_create 里程碑（主 init 建 app_core 任务）与主线后续的 os_start→调度→app_core 入口 0x6012028→power_init 内 0x6067a0c（pmu_task）/0x6067ae0（trim 任务）/0x6067b12（第二任务）是同一执行的连续段。步数坐标一致：127,239 就是主线总步数轴上的同一点（CPU1 未发射，总步数=CPU0 步数）。
- 排序建议：**单线推进主条件线**；支线栈不再派独立前缀（其 check_local --rom 回归保留为独立对照）；主场景 crc_bankb 289,849 维持为「不开 heap fixture」负例钉子。未来新投影优先做进主条件线场景栈，支线只在需要独立复核某个前缀段时启用。

## 4. ROI 排序路线图（BD..BJ；每路=一个边界族+证据先行三选一）

| 路 | 一句话（停点/框架/依赖） | ROI 理由 |
|---|---|---|
| BD（BB 收尾+P3_LS 尾段） | 完成 P3_AWKUP 具名零值 fixture（BB 在途）→ 按 AZ 已钉证据放行 P3_LS_*/0xf2a638/0xf2a628 掩码集+清尾，walker 全返回；依赖 BB | 当前停点，证据最全，一步出 walker |
| BE（Timer0 IRQ4 交付） | 在 ~341,097 必撞前实现 deliver_timer0_irq：向量槽真实读+0x12ece8 28B 帧锚+W1C ack+对称 RTI（抄 core_tick 机制）；依赖无（可与 BD 并行开发、BD 后接进主栈） | **已完成（2026-11-13，基线 f7878d0）**：机制就位且 probe 场景用真实 handler 字节跑通完整往返（交付→临界区→W1C→空回调派发→帧弹出→RTI 回到被中断点→同一诚实停）；IRQ4 运行态合格已证实（~110,768 步真实使能）；基线不含 BD，合并 main（c6f81d9+）后需融合场景参数区 |
| BF（core tick 交付接线） | 把 probe_core_tick 既有 core_tick_entry/RTI 机制接进 irq_entry 主场景栈（纯接线），在 ~447,490 前就位；依赖无 | 机制全有纯接线；C2/D1 的时间驱动主干 |
| BG（app_core 尾段推进） | B4/B5（资源 getter/向量安装/任务创建/函数表 walker）逐边界推进，预计以同族投影为主；依赖 BD | 到消息循环前的必经段，重叠度高 |
| BH（音频/OS 域大段） | B6 未知边界群（0x606dxxx 等）逐个证据先行；可能吸收多路；watch 0x601249c/0x60124ae power-off 金丝雀；依赖 BG | D1 前最大不确定段，尽早探明边界数量本身就是产出 |
| BI（全核 IDLE+事件时钟首验） | B9：真实前缀叠 event_clock_projection，观察首个真实 tick 唤醒→handler/ACK→任务恢复（C2 退出条件）；依赖 BE/BF/BH | D1 的机制收口；契约/单测已备 |
| BJ（D1 验收+L2 触发） | B10：事件循环自我维持验收场景（连续 N 定时事件+输入事件分发+可检查状态变化），通过即启动 L2 三镜像差分（analyze_image_trace 直接复用）；依赖 BI | 目标里程碑 |

不排进本梯（与 D1 解耦）：堆信息真机测量会话（路 BA，用户决策）、EQ 钩子站点消费验证（随 L2 显形）。

## 5. 证据锚点

- 衔接互证：HANDOFF「已实现」路 AY 条目；tools/probe_dualcore.py:23-48（dual_case→platform_case+DualCoreMachine）；tools/probe_irq_entry.py:635（irq_case→dual_case）；tools/probe_platform_startup.py:57（cpu1_release_boundary）；docs/dualcore-startup.md 未验证条件表。
- 机器状态@303,853：analysis/local-checks/irq-entry.json（p11_heap_experimental_config 场景；timer0 cnt 8268/prd 119999、cpu0 tick cnt 625452/prd 1199999、cpu1 tick cnt 640960/prd 479999999、kick_rmw_writes 21）。
- Timer0/IRQ4：br28_model.Timer0（pending 读回 bit15、W1C 0x4000）；advance() 的 `interrupt_delivery_not_implemented`（br28_model.py 顶层层）；向量槽 0x19fe10=0x12ece8（路 AU 快照+analyze_startup_chain anchors）；handler 28B 软件帧（AGENTS/现前沿条目、docs/gpio-irq.md）。
- core tick 交付机制：tools/probe_core_tick.py（CORE_TICK_FRAMES、core_tick_entry/banks/rti、「first overflow ~450k unreachable」自述）。
- 事件时钟：docs/event-clock.md 契约全文+第 4/5 节（Timer0 非唤醒事件、假设叠加）。
- app_core 尾部结构：本机反汇编（0x60120e2 call 0x6067cb4→0x6067d6a/0x6067d7a→资源 getter→0x6065d86 向量安装→任务创建→函数表 walker→0x606dxxx→pend/post 消息循环→0x601249c/0x60124ae power-off 死等）。

## 6. 不确定处（如实标注）

- 步数预测（341,097/447,490）假设 Timer0/tick 的 progress 不被后续固件写暂停或重配（CON/CNT/PRD 再写会移动预测点）；到达前每路按实际报告重钉。
- ~~IRQ4 与 IRQ3 的资格（enable/IPMASK/优先级）在当前前缀是否已配置未知~~ 已解决（路 BE，2026-11-13）：IRQ4 由驱动安装 request_irq 在 ~110,768 步以 priority 1（maskable）真实使能且此后无清零（write trace：0xffe100=0x30000，字后长成 0x33b55）；IPMASK 运行态在 0/6 间随临界区升降（临界区内 priority≤5 被屏蔽，退出即恢复）。IRQ3 的资格仍以到达时实测为准（B8）。
- B6 段的边界数量与是否有 BT/音频外设硬依赖（芯片应答类）离线不可判——这是 BH 路要量化的对象。
- p11_init 是否会在 app_core 尾部被再次调用未反汇编穷举。
