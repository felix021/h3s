# 交接：整机启动仿真推进中（阶段 D 进行，前沿 154,242 步）

2026-11-11 恢复工作。阶段 A/B/C1 完成，C2（tick 真实触发）与 D 进行中。停止时请更新本文件。

## 当前前沿

五级停止阶梯（`tools/probe_irq_entry.py`，逐步 opt-in 叠加，全部回归通过）：

| 场景 | 步数 | 停止点 | kind |
|---|---|---|---|
| irq127_request_strict | 149,660 | 0x12b608 | software_irq_delivery_not_implemented |
| irq127_entry_minimal | 149,679 | 0x12ad68 | unknown_core_special_register (usp) |
| irq127_scheduler_entry | 151,500 | 0x12c5ca | unsupported idle（对核 IDLE 边界）|
| irq127_appcore_roundtrip | 153,430 | 0x12b66a | unmodeled_unmaskable_irq irq125 |
| **irq125_sync_roundtrip** | **154,242** | **0x12b732** | **unsupported_cpu1_release_transition** |

**154,242 步含义**：CPU0 的 app_core 发跨核同步 IPI（IRQ125）→ CPU1 应答 → CPU0 继续 → SFC 挂起序列（0x12b916→0x12b890：GPIO RMW 按位、baud=0、disable、0xfd1c00 时钟门、第二 SFC 0xfe0100 复位态跳过）→ GPIO port D OUT 读回按位精化（GpioOutValue 偏字）→ SFC 恢复（0xfd1c00 |=8、&=~0x1000）→ +8 分频寄存器写 3、读回 0xFF（分频/数据寄存器语义）→ 0xfd1c00 bit15 PLL-lock 读回为 1（显式投影）→ 停在 `[0xffe8c0] |= 0x8`（对已释放 CPU1 的 CPU1 控制 RMW，未建模）。

## 已实现（按序，均 opt-in 投影，默认关闭 legacy 报告不变）

- **基线重建**（921c2d5）：k2 副作用修正 + probe_hardware/gpio_irq 全字存储 + probe_pi32v2 architectural_k2。
- **返回对照**（f4394dd）：probe_task_returns.py 5 反事实停止点。
- **IRQ127 调度闭环**（f4394dd→469e579）：每核 IPI 交付、向量槽读取、RETI 装载、pending 清除（software）/handler ACK（tick）、入口银行假设、IDLE 等待投影、对称 IRQ RTI 投影。
- **IRQ125 同步握手**（6ac1d8c）：unmaskable IRQ（6/7=GIEMASK 类）投递投影；交付目标按请求核解析。
- **SFC 挂起序列**（8e356df）：使能态写 baud=0 放行；非零速率切换仍拒绝。
- **GPIO RMW 按位精化**（420c874）：and/or 逐位确定，XOR/ADD/SUB 仍保守。
- **SFC 时钟门 + 第二 SFC**（8770401）：0xfd1c00 时钟门投影（只记录置/清、读回严格 unknown）、第二 SFC 0xfe0100（复位态 CON=0 显式假设）。
- **GPIO OUT 读回按位**（8e5df35）：GpioOutValue 偏字类型，and/or 位操作传递、全字消费（算术/比较/存 RAM）严格停；未写位 unknown 不臆造。
- **SFC 时钟 +8 / PLL lock 读回**（本次）：0xfd1c08 分频寄存器可写可读（读回=最后写入值，仅在固件写过后返回，不臆造复位值）；0xfd1c00 bit15 读回显式投影为 PLL 已锁定（0x12ba38 轮询证据）。
- **入口原子性**（cb109b3）：deliver 先完整校验银行假设再提交状态，失败不留部分变化。
- **core tick 机制**（303e388）：deliver_core_tick/return_core_tick + ACK rmw + 停摆策略 + 单测。**首次溢出 ~450k 步 > 当前 153k，未真实触发——C2 未完成。**

## codex (gpt-6-astra) 参谋结论（2026-11-11 二次，已核实）

阶段划分修正：
- **C1**（IPI 调度进入应用）：条件模型下完成。
- **C2**（时间驱动恢复）：未完成。退出条件=实际计数产生 pending→IRQ3→真实 handler/ACK→RTOS 时间或就绪状态变化→任务恢复并继续进展。机制+单测不能替代。
- **D1**（控制服务就绪）：初始化依赖闭合；真实队列/分派接收输入；产生可检查状态变化；随后还能处理下一事件及定时事件。可启动 L2 差分。
- **D2**（音频服务就绪）：真实软件创建/启动/停止/重建音频链。为 L3 提供环境。

"完整仿真"边界（诚实定义）：**指定 UFW/初态/场景集合的整机软件功能仿真；硬件用列明契约的模型，未认证语义保持条件结论。** 软件（CPU0/1 指令、ROM、调度器、handler、锁队列、资源解析、EQ 钩子）真实执行；硬件（Flash 总线、计时器、中断控制器、DMA、GPIO、ADC/DAC 端、蓝牙外部环境）功能模型；P11 可用接口等价模型（不声称验证 P11 软件）；射频/功耗/模拟/声压不能离线认证。

三镜像 EQ 差分可提前到 L2（事件循环+EQ 控制面），不必先跑通蓝牙/PCM。L3 才需 PCM 证据。

## 四个待处理暂停点（codex 指出，按优先级）

1. **一次性交付限制**：software IRQ / tick 都要求"该核从未交付过"，下一次事件被拒（br28_machine.py deliver 的 require）。扩展重复交付需验证完成状态、活动 handler、银行恢复。
2. **全核 IDLE 后时间停**：当前立即 `all_cores_idle_waiting` 停止。持续服务需独立事件时钟，时钟契约允许时推进到下个事件。
3. **入口失败原子性**：银行假设路径先改 PC/RETI、移除等待状态再查 SSP/任务 SP；检查失败可能留部分状态。应先完整校验再提交。
4. **文档/场景名落后证据**：HANDOFF 旧边界、tick 探针仍停 IRQ125。建议每里程碑绑定提交+场景+报告+退出条件+剩余假设。

## 当前并行任务

- 路 A（本 worktree sim-sfc-clk）：SFC 时钟 +8 分频 / PLL-lock 读回已完成，前沿推进到 154,242（0x12b732）。
- 路 B（subagent）：P11 主核读写依赖清单（判断 P11 硬/软前提）——只读调研。
- codex 参谋已出（见上）。

## 下一步（按 codex 建议）

1. 继续 SFC 挂起-恢复：0x12b732 的对核控制 RMW（`[0xffe8c0] |= 0x8`）在 CPU1 已释放后的语义未建模（当前 `unsupported_cpu1_release_transition`）；随后还有对核暂停/释放的完整周期。
2. 补齐每个镜像的资源映射（candidate_view 目前只返回 app+337B payload）。
3. 补可重复中断交付 + 事件时间推进（解决暂停点 1/2）。
4. 按实际启动依赖补电源 0xfd3100、trim、ROM 调用、P11 接口。
5. 到达事件循环 → D1（可启动 L2 三镜像差分）→ D2（音频流）。

## 关键技术事实（已钉）

- IPMASK 物理地址 0xFFE168+cnum*0x800；模型常量 0xffe1a8 是另一路径，都更新 IRQController.ipmask。
- 0xFFE200 = EMU_CON（debug control）兼软件 IRQ 源使能位（bits0-3）。
- 调度 IPI 公式 IRQ 127-cnum（两核 handler 同为 0x12AD2A）；同步 IPI IRQ125（CPU1 handler 0x60662c8）。
- IRQ125 = OS_SYNC_SOFT_IRQ，优先级 6 = GIEMASK 类（SDK hwi.h + hwi.c.o IR 双重印证）。
- app_core 帧 0x19f7b0：16B 特殊帧（PC 0x6012028）+ 60B GPR 未初始化（真实语义）。
- SFC 挂起路径 0x12b890（先 baud=0 后 disable）vs 使能路径 0x12e1e8（先 disable 后 baud）。
- core tick PRD=1199999（CPU0）/479999999（CPU1），非"PRD=1 高频"（那是独立叶子 0x6066134 未在当前前缀调用）。

## 资料和硬件边界（不变）

- 历史失败 r2（c8dae523…）禁止重刷；修复候选（67ba97ef…）未验证未刷。
- ROM 公开 Release asset（QCY_H3S_BR28_ROM_000000-020000.bin）。
- RAM 观测候选仍 nondeployable；检查全部本机执行，不用 GitHub CI；发布需用户确认。
