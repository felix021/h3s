# 交接：整机启动仿真推进中（阶段 D 进行，前沿 154,263 步）

2026-11-11 恢复工作。阶段 A/B/C1 完成，C2（tick 真实触发）与 D 进行中。停止时请更新本文件。

## 当前前沿

五级停止阶梯（`tools/probe_irq_entry.py`，逐步 opt-in 叠加，全部回归通过）：

| 场景 | 步数 | 停止点 | kind |
|---|---|---|---|
| irq127_request_strict | 149,660 | 0x12b608 | software_irq_delivery_not_implemented |
| irq127_entry_minimal | 149,679 | 0x12ad68 | unknown_core_special_register (usp) |
| irq127_scheduler_entry | 151,500 | 0x12c5ca | unsupported idle（对核 IDLE 边界）|
| irq127_appcore_roundtrip | 153,430 | 0x12b66a | unmodeled_unmaskable_irq irq125 |
| **irq125_sync_roundtrip** | **154,263** | **0x606633e** | **multicore_special_frame_unmodeled (pop.srl)** |

**154,263 步含义**：CPU0 的 app_core 发跨核同步 IPI（IRQ125）→ CPU1 应答 → CPU0 继续 → SFC 挂起序列（0x12b916→0x12b890：GPIO RMW 按位、baud=0、disable、0xfd1c00 时钟门、第二 SFC 0xfe0100 复位态跳过）→ GPIO port D OUT 读回按位精化（GpioOutValue 偏字）→ SFC 恢复（0xfd1c00 |=8、&=~0x1000）→ +8 分频寄存器写 3、读回 0xFF（分频/数据寄存器语义）→ 0xfd1c00 bit15 PLL-lock 读回为 1（显式投影）→ CPU0 ack 等待函数（0x12b6de）清 flag(0x1265f7) bit0 后进入轮询：3 次 `[0xffe8c0] |= 0x8` kick（0x12b732）→ kick 唤醒挂在自身 irq125 handler 内 idle（0x6066318）的 CPU1 → CPU1 检查 bit0 已清、清 bit2（0x6066336/38）退出循环 → CPU0 循环退出 → 停在 CPU1 handler 尾部 `{psr,rets,reti}` 特殊帧弹出（0x606633e pop.srl，multicore 特殊帧未建模）。

## 已实现（按序，均 opt-in 投影，默认关闭 legacy 报告不变）

- **基线重建**（921c2d5）：k2 副作用修正 + probe_hardware/gpio_irq 全字存储 + probe_pi32v2 architectural_k2。
- **返回对照**（f4394dd）：probe_task_returns.py 5 反事实停止点。
- **IRQ127 调度闭环**（f4394dd→469e579）：每核 IPI 交付、向量槽读取、RETI 装载、pending 清除（software）/handler ACK（tick）、入口银行假设、IDLE 等待投影、对称 IRQ RTI 投影。
- **IRQ125 同步握手**（6ac1d8c）：unmaskable IRQ（6/7=GIEMASK 类）投递投影；交付目标按请求核解析。
- **SFC 挂起序列**（8e356df）：使能态写 baud=0 放行；非零速率切换仍拒绝。
- **GPIO RMW 按位精化**（420c874）：and/or 逐位确定，XOR/ADD/SUB 仍保守。
- **SFC 时钟门 + 第二 SFC**（8770401）：0xfd1c00 时钟门投影（只记录置/清、读回严格 unknown）、第二 SFC 0xfe0100（复位态 CON=0 显式假设）。
- **GPIO OUT 读回按位**（8e5df35）：GpioOutValue 偏字类型，and/or 位操作传递、全字消费（算术/比较/存 RAM）严格停；未写位 unknown 不臆造。
- **SFC 时钟 +8 / PLL lock 读回**（c3f399e）：0xfd1c08 分频寄存器可写可读（读回=最后写入值，仅在固件写过后返回，不臆造复位值）；0xfd1c00 bit15 读回显式投影为 PLL 已锁定（0x12ba38 轮询证据）。
- **CPU1 PMU_CON0 RMW + kick 唤醒**（本次）：0xffe8c0=CPU1 PMU_CON0（csfr.h q32DSP(1)+0x30*4；0xffe0c0 同位为 CPU0）。含 bit0 的 RMW 与严格写路径等价（实际释放指令 0x60660da 就是 `|=1`）；释放后 bit0 不参与的 or 置位记为 kick 位（0x12b732/0x12c71c 双证据，只置位不回读不清除）。machine 侧 `cpu1_control_kick_wake` 投影：kick 让挂在 idle 的 CPU1 按完成计步并唤醒——「bit3 就是唤醒源」是从握手协议推断的显式假设（SDK 无 PMU_CON0 位定义，硅语义未验证，已写入 report 假设）。
- **入口原子性**（cb109b3）：deliver 先完整校验银行假设再提交状态，失败不留部分变化。
- **core tick 机制**（303e388）：deliver_core_tick/return_core_tick + ACK rmw + 停摆策略 + 单测。**首次溢出 ~450k 步 > 当前 153k，未真实触发——C2 未完成。**
- **可重复中断交付**（本次）：暂停点 1 最小闭环。新 opt-in 开关 `repeat_irq_delivery`（默认关，legacy 5 场景字节不变）：打开后同核「无在途 handler（active_context 非 software_irq_handler/core_tick_handler）即可交付」，handler RTI 返回后允许再次交付；在途仍拒绝（不嵌套）；向量槽/银行假设每次完整重走，pending 只能由固件真实重新 pend（0xffe1a0/0xffe9a0 |=，全部 pend 走 0x60644a6 通用 IPI 请求函数）。单测覆盖 交付→RTI→再交付、在途拒绝、默认关一次性。新场景 irq125_repeat_inert 钉住：repeat 开启后前沿 154,263 完全不动。**真实第二次交付在前沿内不可达（实测）：可达前缀内每核只 pend 一次（commands=4：两核各 pend+clear，CPU1 handler 入口 0x60662de 自清 pending）；CPU0 tick 在 per-core step 136,569 使能（PRD=1199999），其后 gate8/9 开放窗口总跨度 ~4.4k 步 < 最小溢出间距 4688 步（progress 上限 256），两次 tick 交付数学上不可能。第二次交付需等 0x606633e CPU1 handler 尾建模后（app_core 握手完成后的下一次跨核请求）。**

## codex (gpt-6-astra) 参谋结论（2026-11-11 二次，已核实）

阶段划分修正：
- **C1**（IPI 调度进入应用）：条件模型下完成。
- **C2**（时间驱动恢复）：未完成。退出条件=实际计数产生 pending→IRQ3→真实 handler/ACK→RTOS 时间或就绪状态变化→任务恢复并继续进展。机制+单测不能替代。
- **D1**（控制服务就绪）：初始化依赖闭合；真实队列/分派接收输入；产生可检查状态变化；随后还能处理下一事件及定时事件。可启动 L2 差分。
- **D2**（音频服务就绪）：真实软件创建/启动/停止/重建音频链。为 L3 提供环境。

"完整仿真"边界（诚实定义）：**指定 UFW/初态/场景集合的整机软件功能仿真；硬件用列明契约的模型，未认证语义保持条件结论。** 软件（CPU0/1 指令、ROM、调度器、handler、锁队列、资源解析、EQ 钩子）真实执行；硬件（Flash 总线、计时器、中断控制器、DMA、GPIO、ADC/DAC 端、蓝牙外部环境）功能模型；P11 可用接口等价模型（不声称验证 P11 软件）；射频/功耗/模拟/声压不能离线认证。

三镜像 EQ 差分可提前到 L2（事件循环+EQ 控制面），不必先跑通蓝牙/PCM。L3 才需 PCM 证据。

## 四个待处理暂停点（codex 指出，按优先级）

1. **一次性交付限制**：~~software IRQ / tick 都要求"该核从未交付过"~~ **已解决（机制层）**：`repeat_irq_delivery` opt-in 开关实现「返回后可重新交付」最小闭环（无在途 handler 即可交付、不嵌套、全量重校验、pending 只认真实 pend），单测+前沿不变场景齐备。**真实第二次交付观察未完成**：前沿内固件每核只 pend 一次、tick 双溢出窗口不够（见「已实现」末条），待 0x606633e 建模后在 app_core 下一次跨核请求处观察。
2. **全核 IDLE 后时间停**：当前立即 `all_cores_idle_waiting` 停止。持续服务需独立事件时钟，时钟契约允许时推进到下个事件。
3. **入口失败原子性**：银行假设路径先改 PC/RETI、移除等待状态再查 SSP/任务 SP；检查失败可能留部分状态。应先完整校验再提交。（cb109b3 已修：先完整校验再提交。）
4. **文档/场景名落后证据**：HANDOFF 旧边界、tick 探针仍停 IRQ125。建议每里程碑绑定提交+场景+报告+退出条件+剩余假设。

## P11 主核依赖清单（2026-11-11 路调研，全部反汇编证实）

主核侧两条死循环阻塞（无超时、无计数器）：

1. **p11_init** `0x60688ec`（调用点 `0x6035180`，先于 app_core）：向 P11_UART0.CON2 `0xf2af10` 写序列 {0xff,0xee,0xab,0x0e,0x55}（`0x60688f8–608`），然后死等 **PMU_STA `0xfd3e04` bit0 置位**（`0x606890c/e`）。
2. **power_init** `0x6067876`（app_core `0x6012028` 内 `0x60120dc` 唯一调用点）：置 **PWR_CON `0xf2a400` bit5**（LP_PWR_INIT_FLAG，`0x60679a0` `|=0x20`）后死等其清零（`0x60679ae/b0`）。

关键陷阱：`0xf2a400` 在 app 内**不是字面量**（全字节扫描 0 命中），由 `r7=0xf2c04c` 减 7244 算出（`0x6067918`）——按字面量搜必漏。

power_init 其余操作（只写不阻塞，可投影为可写寄存器）：释放 q32s PMU_CON `0xf2a0c0` bit0（`0x60679a4/a8`）、CLK_CON0 `0xf2a414`=1、SYS_DIV `0xf2a410`=0、P2M_CLK `0xf2a618`=8、p11_code.bin 公共段拷贝到 `0xf20080`（`0x6067988–96`）。堆信息 4 字节 `0xf27f4a..4d`（heap begin/size，P11 RAM 顶端 P2M 消息区 +0xa）：主核读一次不阻塞（`0x60679b4/ba/be`），值由 P11 init 写入（P11 代码 `0xa1c` 引用，具体数值未证实）。

P11 侧交叉印证：q32s `0xafa` `bitclr r2,0x5` 清 bit5 **已证实且无条件执行**（`0xac0` 片段）；但「主核释放 → q32s 跑 init → 清 bit5」的时序/可达性是推断（q32s 解码 `0xa86` 有缺口）。ROM 只读 PWR_CON **bit7**（`0x17648`，LP_PWR_RST_FLAG，POR=1 走已初始化路径），不碰 bit5。

**最小接口结论**：(a) 必须建模为 P11 响应——PWR_CON bit5 清除、PMU_STA bit0 置位（主核死等）；(b) 可投影复位固定值——PWR_CON bit7=1（POR 0x80）、堆信息 4 字节、纯写寄存器们；(c) 需仿真/其他证据才能定——堆信息具体数值、清 bit5 时序、PMU_STA bit0 置位机制。

## 当前并行任务

- 路 A（sim-sfc-clk，c3f399e 已合并推送）：SFC 时钟 +8 分频 / PLL-lock 读回完成。
- 路 B（只读调研）：P11 主核读写依赖清单完成，结论见上节。
- 路 C（sim-cpu1-ctl，本次）：CPU1 PMU_CON0 kick RMW + 唤醒投影完成，前沿 154,263（0x606633e）。
- 下一路：0x606633e CPU1 irq125 handler 尾部特殊帧弹出（pop.srl 的 psr/rets/reti 恢复 + handler RTI）。

## 下一步（按 codex 建议）

1. CPU1 irq125 handler 退出：0x606633e 的 `{psr,rets,reti}=[sp++]`（multicore 特殊帧弹出）与其后的 RTI（现有 return_software_irq 只认 CPU0 handler 尾 0x12ade8，需按 active_context 推广）；随后是对核暂停/释放完整周期（0x12c6e6 备用启动函数：读回 bit0 守卫的 RMW 释放 `|=1` + 等待 ready 的 kick 循环 0x12c71c，均未在当前前缀触发）。
2. 补齐每个镜像的资源映射（candidate_view 目前只返回 app+337B payload）。
3. 补可重复中断交付 + 事件时间推进（暂停点 1 机制已落，见上；剩暂停点 2 事件时钟，以及前沿推进后补真实第二次交付观察）。
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
- per-core q32DSP SFR bank：0xffe000+core*0x800；PMU_CON0=+0xc0（0xffe0c0/0xffe8c0，SDK 无位定义）、ILAT_SET=+0x1a0、ILAT_CLR=+0x1a4。固件用 `(1-cnum)<<11+0xffe0c0` 算对核 PMU_CON0。
- 跨核同步邮箱（0x1265c0 结构）：+54=命令字节（CPU0 写、CPU1 handler 0x60662c8 读）、+55=标志字节——bit1=发送路径 ack（CPU1 清，CPU0 0x12b66e 等）、bit0=CPU0 应答等待门（CPU0 0x12b716 清、CPU1 idle 醒后检查）、bit2=响应完成（CPU1 0x6066336 清、CPU0 0x12b73c kick 循环等）。

## 资料和硬件边界（不变）

- 历史失败 r2（c8dae523…）禁止重刷；修复候选（67ba97ef…）未验证未刷。
- ROM 公开 Release asset（QCY_H3S_BR28_ROM_000000-020000.bin）。
- RAM 观测候选仍 nondeployable；检查全部本机执行，不用 GitHub CI；发布需用户确认。
