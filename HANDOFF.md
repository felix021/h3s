# 交接：整机启动仿真推进中（阶段 D 进行，前沿 155,389 步）

2026-11-11 恢复工作。阶段 A/B/C1 完成，C2（tick 真实触发）与 D 进行中。停止时请更新本文件。

## 当前前沿

六级停止阶梯（`tools/probe_irq_entry.py`，逐步 opt-in 叠加，全部回归通过）：

| 场景 | 步数 | 停止点 | kind |
|---|---|---|---|
| irq127_request_strict | 149,660 | 0x12b608 | software_irq_delivery_not_implemented |
| irq127_entry_minimal | 149,679 | 0x12ad68 | unknown_core_special_register (usp) |
| irq127_scheduler_entry | 151,500 | 0x12c5ca | unsupported idle（对核 IDLE 边界）|
| irq127_appcore_roundtrip | 153,430 | 0x12b66a | unmodeled_unmaskable_irq irq125 |
| irq125_sync_roundtrip | 155,127 | 0x6000f44 | unmapped_or_readonly_write 0xfd3504（CRC 投影关闭时的边界）|
| **crc_engine_roundtrip** | **155,389** | **0x6010b14** | **unsupported_runtime_instruction rep.i（32 字节块重复，ISA 能力边界）** |

**155,389 步含义**：在 155,127 的 CRC 种子写之前，wrapper（app 0x6000f0c）先经 0x6064538 获取 OS 设备（r6=0x126dc0）；CRC 投影放行种子写 `[0xfd3504]=0` 后，逐字节馈入 0x400b22 起 30 字节（`[0xfd3500]=b`，st.w 载荷≤0xff）、csync（0x6000f50）、0x6000f52 **配对指令** `r4=[r0+4]∥r0=r6`（读回 CRC 结果 0xd97f，4 字节复合指令，watch 证实 PC 跳过 0x6000f54）、call 0x606472a 释放设备（返回值 r0=读数）、返回 CRC 低 16 位。CPU0 继续启动到 0x6010b14 `rep 4 32 {`：32 字节多指令块重复，现有 immediate_rep_projection 只支持单 store 体，模拟器无通用块 repeat——诚实 ISA 边界。

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
- **CPU1 PMU_CON0 RMW + kick 唤醒**（前置）：0xffe8c0=CPU1 PMU_CON0（csfr.h q32DSP(1)+0x30*4；0xffe0c0 同位为 CPU0）。含 bit0 的 RMW 与严格写路径等价（实际释放指令 0x60660da 就是 `|=1`）；释放后 bit0 不参与的 or 置位记为 kick 位（0x12b732/0x12c71c 双证据，只置位不回读不清除）。machine 侧 `cpu1_control_kick_wake` 投影：kick 让挂在 idle 的 CPU1 按完成计步并唤醒——「bit3 就是唤醒源」是从握手协议推断的显式假设（SDK 无 PMU_CON0 位定义，硅语义未验证，已写入 report 假设）。
- **CPU1 irq125 handler 尾部帧弹出 + 对称 RTI**（cb109b3 前一提交）：probe_irq_entry HANDLER_PSR_FRAMES 增加 `0x606633e:'frame12_pop'`（pop.srl m=41，从真实栈弹出 {psr,rets,reti}，字节来源是入口 0x60662c8 frame12_push 写入的交付状态，不臆造）；`br28_machine.SOFTWARE_IRQ_RTI_TAILS=(0x12ade8,0x6066342)` 把对称 RTI 投影（PC=RETI、SP=USP、bank 回写/ICFG 效果仍是假设）推广到 CPU1 的 irq125 handler 尾 0x6066342。RTI 后 CPU1 回到被中断的 idle（0x12c5ca，reti_from_frame=0x12c5ca）并重新停泊。注意：`defer_core1_first_idle` 的 4096 上限在此返回后被重新触发耗尽（CPU1 返回 0x12c5ca 未停泊期间 CPU0 连续发射）——确定性、不改状态，报告 deferred_core1_turns=4096。
- **硬件 CRC 引擎投影**（本次，opt-in `hardware_crc_projection`）：`br28_model.HardwareCrcEngine`。访问形状只接受 ROM 0xaa1e 与 app 0x6000f2c 真实用过的：写 +4 种子（≤0xffff）、写 +0 馈一个字节（st.w 载荷≤0xff）、读 +4 结果；复位态未知（未写种子读/馈入停止）、+0 读、超宽载荷、其他偏移一律 Blocked。算法参数按 app 软件 CRC-16 表推导（多项式 0x1021、MSB-first、初值=种子、无输出异或）——功能等价投影，硬件位序/字节序硅上未验证（report 显式记录）。同步模型无 busy 位（固件写数据后 csync 再读）。单测 test_br28_model 对照 app 表逐位/半字节实现 + CRC-16/XMODEM 标准校验值 0x31C3。
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
- 路 C（sim-cpu1-ctl，已合并）：CPU1 PMU_CON0 kick RMW + 唤醒投影完成。
- 路 D（sim-frame-pop，已合并 96faa99）：CPU1 irq125 handler 尾部 pop.srl 帧弹出 + 对称 RTI 完成，前沿 155,127（0x6000f44）。
- 路 E（sim-crc，本次）：硬件 CRC 引擎 0xfd3500/0xfd3504 建模完成，前沿 155,389（0x6010b14）。

## 下一步（按 codex 建议）

1. **rep.i 块重复指令**（新前沿 0x6010b14 `rep 4 32 {`）：32 字节多指令体（含 load/store/call），现有 immediate_rep_projection 只支持单 store 体；需要通用块 repeat 执行或针对该形状的显式投影。块内 0x6010b20/0x6010b28/0x6010b30 的 call 目标（0x6010a5e 附近）像字符串/表处理原语，值得先反汇编确认块体语义再决定投影形状。
2. 补齐每个镜像的资源映射（candidate_view 目前只返回 app+337B payload）。
3. 补可重复中断交付 + 事件时间推进（解决暂停点 1/2）。
4. 按实际启动依赖补电源 0xfd3100、trim、ROM 调用、P11 接口。
5. 到达事件循环 → D1（可启动 L2 三镜像差分）→ D2（音频流）。

## 关键技术事实（已钉）

- IPMASK 物理地址 0xFFE168+cnum*0x800；模型常量 0xffe1a8 是另一路径，都更新 IRQController.ipmask。
- 0xFFE200 = EMU_CON（debug control）兼软件 IRQ 源使能位（bits0-3）。
- 调度 IPI 公式 IRQ 127-cnum（两核 handler 同为 0x12AD2A）；同步 IPI IRQ125（CPU1 handler 0x60662c8）。
- CPU1 irq125 handler 尾部形状（app 反汇编钉死）：0x606633a pop.rmask {r4-r0}、0x606633e pop.srl m=41 {psr,rets,reti}、0x6066340 csync、0x6066342 rti——与入口 0x60662c8 push.srl m=41 / 0x60662ca push.rmask m=31 完全对称；RTI 返回被中断的 idle（reti_from_frame=0x12c5ca）。
- **硬件 CRC 引擎 0xfd3500（本次钉死）**：+0=数据口（写一个字节馈入一次，st.w 载荷≤0xff）、+4=种子/结果口（写种子 ≤0xffff，csync 后读回当前 CRC）。**算法参数有黄金证据**：app 软件 CRC-16 半字节表在 0x060a860c（16 项，entry[i]=i·0x1021，GF(2) 乘法）→ 多项式 **0x1021**（x^16+x^12+x^5+1，CRC-16-CCITT/XMODEM 族）、**MSB-first 左移**、**初值=所写种子**、无输出异或/反射；软件实现 0x6000ed6（每字节两个半字节查表 `crc=(crc<<4)^tab[(crc>>12)^nib]`）。wrapper 0x6000f0c 路径选择：icfg.b0 gate 位非零或 0x12cb08（24 字位图检查）返回非零 → 软件路径（r2=0 初值）；否则硬件路径。两路径共用初值 0 = 固件的等价性设计意图。硬件路径：call 0x6064538 取 OS 设备（r6=0x126dc0）→ 种子写 → 馈入循环 → csync → **0x6000f52 配对指令** `r4=[r0+4]∥r0=r6`（4 字节复合，读结果+恢复 r0，watch 证实跳过 0x6000f54）→ call 0x606472a 释放（返回 r0=读数）→ 返回低 16 位。ROM 0xaa1e 同一引擎做 USB transform keystream：种子 0xffff、喂 0xff×2、循环喂 15 字节密钥表（0x1f619）、读 [r2+4] 与数据 XOR。
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
