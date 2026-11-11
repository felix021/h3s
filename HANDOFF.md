# 交接：整机启动仿真推进中（阶段 D 进行，前沿 162,646 步）

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
| **crc_engine_roundtrip** | **162,646** | **0x600a374** | **unmapped_or_readonly_write 0xfe0224（SFC 主块 +0x24 未建模，新边界）** |

**162,646 步含义**：CRC wrapper（见 155,127/155,389 旧含义，此处不赘述）之后，CPU0 进入 flash 资源分区表扫描（app 0x6010aea 起）：`r4=表项指针`（RAM 表 0x126560+idx*4，实为 0x06000000 起的 flash 头），`0x6010b14 rep 4 32`（**4 字节体** `ld.bu.pin r2=b[r1++=1]` + `st.b.pin b[r0++=1]=r2` 重复 **32 次**，即 32 字节 memcpy——旧 HANDOFF「32 字节多指令体含 call」读反了编码：rep.i 语法 `rep {b} {n}` 中 b=体字节数（raw×2+2）、n=次数（raw+1），call 在体之外）把 32 字节表项头拷到栈，再 call 0x6010a64 名字匹配器（内含四个 `rep 4 8` 同形状 8 字节拷贝块）比对 app.bin/PRCT/CODE/BTIF/AUTO/ANCIF/ANCIF1 等名字。整条扫描 70 个 rep.i 多指令体全部逐条真实执行（opt-in `immediate_rep_multi_block`）+ 按需挂完整 sfc 明文只读区（0x06000000 头 0x100 资源表 + app 结束后的资源区，默认只映射 ENTRY 起 app 切片）。扫描完成后固件重配 SFC 时钟：0x6010b72-7a 对 0xfd1c00 三次 RMW（已建模时钟门路径），0x6010b88 `st.w [0xfd1c04]=3`（只写记录，162,615 旧前沿）——随后 VM init（0x6011000）把扫描到的 VM 分区参数写入 qcy_boot_info 0x122800 的 word1/word3（`[r8+4]=vm_saddr=0x1da000`、`[r8+12]=vm_size=0x4000`），并 call 0x600a2aa SFC 配置分发函数（命令 0x40045601），其中 0x600a352 `rep 4 4` 把 boot_info 头 16 字节拷进 r9+4304 结构：**bytes 1-3/8-11 永远无人写**（boot.h 布局见「关键技术事实」：vm_info 的 C 对齐 padding 与 vm_res 保留字段；app 6 处 0x122800 引用全为读/字段拷贝、ROM 无引用），真机上是残留 RAM 垃圾。opt-in `rep_copy_unknown_transport`（本次）让拷贝体按字节来源运输未知（不臆造数值），消费路径只用 vm_saddr（已知），call 0x12C326 算出 VM 区 flash 窗口地址 0x061d7fe0（=0x06000000+0x1da000-0x2020），随后 0x600a374 `st.w [r1+12]=r0`（r1=0xfe0218）写 **0xfe0224** 停——SFC 主块 0xfe0200 的 +0x18/+0x20/+0x24 偏移未建模（后两条是 `[r1+8]=0x7fffffff`、`[r1+0]|=2`），是下一个外设边界。旧 HANDOFF 猜的 r1=0xfe0208（+8/+0x10/+0x14）有误，实际基址 0xfe0218。

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
- **SFC 时钟 +4 只写记录**（5543138，仍挂在 `sfc_clock_gate_projection` 开关下）：0xfd1c04 整字写只记录最后值+次数，读回一律 Blocked。证据（app+ROM 全量反汇编）：app 仅 0x6010b88 `st.w=3`（一次性重配，0x12762c 标志守卫），ROM 仅 0xc198 `st.w=uextra(flash配置字,p:8,l:8)`（前置 `[0xfd1c00]=0x20` 整字存）；两侧都无任何读回，复位值/读回语义无观察，SDK 头文件无位定义——写 3 的含义（时钟源选择/分频字段）显式标注未证实。+0xc/+0x10 仅 ROM 0xae84/0xae86 整字写（`[r4+12]=r3`/`[r4+16]=r2`，串行读时钟配置），app 不触碰，暂不建模。
- **rep 拷贝体未知字节运输**（本次，opt-in `rep_copy_unknown_transport`，需 `immediate_rep_multi_block`）：多指令 rep.i 体内的 `ld.w.pin` 装载若跨未写 RAM 字节，构造 UnknownWord（逐字节来源=源物理地址，经 MMU translate），配套 `st.w.pin` 走 `write_abstract_word` 按来源落盘（未知字节保持未知、目标必须是 RAM region）；全已知字照走常规严格路径。语义依据（见「关键技术事实」boot_info 条目）：0x600a352 rep 4 4 拷贝的源 bytes 1-3/8-11 是结构 padding/保留字段，app/ROM 均无写入者，硅上是残留 RAM 垃圾——模型只运输来源、不臆造数值，任何后续消费者仍严格停。负例：不开开关时同指令停在体首装载 uninitialized_memory（=旧 162,615 前沿形状，单测钉死）。默认关闭，legacy 报告不变。
- **CPU1 PMU_CON0 RMW + kick 唤醒**（前置）：0xffe8c0=CPU1 PMU_CON0（csfr.h q32DSP(1)+0x30*4；0xffe0c0 同位为 CPU0）。含 bit0 的 RMW 与严格写路径等价（实际释放指令 0x60660da 就是 `|=1`）；释放后 bit0 不参与的 or 置位记为 kick 位（0x12b732/0x12c71c 双证据，只置位不回读不清除）。machine 侧 `cpu1_control_kick_wake` 投影：kick 让挂在 idle 的 CPU1 按完成计步并唤醒——「bit3 就是唤醒源」是从握手协议推断的显式假设（SDK 无 PMU_CON0 位定义，硅语义未验证，已写入 report 假设）。
- **CPU1 irq125 handler 尾部帧弹出 + 对称 RTI**（cb109b3 前一提交）：probe_irq_entry HANDLER_PSR_FRAMES 增加 `0x606633e:'frame12_pop'`（pop.srl m=41，从真实栈弹出 {psr,rets,reti}，字节来源是入口 0x60662c8 frame12_push 写入的交付状态，不臆造）；`br28_machine.SOFTWARE_IRQ_RTI_TAILS=(0x12ade8,0x6066342)` 把对称 RTI 投影（PC=RETI、SP=USP、bank 回写/ICFG 效果仍是假设）推广到 CPU1 的 irq125 handler 尾 0x6066342。RTI 后 CPU1 回到被中断的 idle（0x12c5ca，reti_from_frame=0x12c5ca）并重新停泊。注意：`defer_core1_first_idle` 的 4096 上限在此返回后被重新触发耗尽（CPU1 返回 0x12c5ca 未停泊期间 CPU0 连续发射）——确定性、不改状态，报告 deferred_core1_turns=4096。
- **硬件 CRC 引擎投影**（1ea2f59，opt-in `hardware_crc_projection`）：`br28_model.HardwareCrcEngine`。访问形状只接受 ROM 0xaa1e 与 app 0x6000f2c 真实用过的：写 +4 种子（≤0xffff）、写 +0 馈一个字节（st.w 载荷≤0xff）、读 +4 结果；复位态未知（未写种子读/馈入停止）、+0 读、超宽载荷、其他偏移一律 Blocked。算法参数按 app 软件 CRC-16 表推导（多项式 0x1021、MSB-first、初值=种子、无输出异或）——功能等价投影，硬件位序/字节序硅上未验证（report 显式记录）。同步模型无 busy 位（固件写数据后 csync 再读）。单测 test_br28_model 对照 app 表逐位/半字节实现 + CRC-16/XMODEM 标准校验值 0x31C3。
- **rep.i 多指令体重复**（fa1a762，opt-in `immediate_rep_multi_block`，probe_boot 通用兜底）：通用方案——主循环 repeat 消费本就按「PC 到达体末尾即完成一轮」回卷（probe_boot step() 393 行起），天然支持区间内多条指令顺序执行；放开的只是单 store 限制。新 gate 放在所有针对性 rep 特例（单 store 体、update_repeat、irq_repeat_return）之后兜底：逐条解码必须恰好覆盖 op['b'] 字节，每条都是有上游 m2 模型的非控制流指令（拒绝分支/goto/call/gotoss、if/iff/ifs 开启器、tbb/tbh、并行对 .p、嵌套 rep、rts/pop.pc/rti 等运行时指令——REP 与体内控制流的交互无硬件捕获证据，不臆造；新 Blocked kind `rep_multi_block_body_not_straight_line`/`rep_multi_block_body_unaligned`）。体指令走常规解码执行：ld.bu.pin 走上游 m2 模型（严格读），st.b.pin 走既有 exact_postincrement_store 适配器（字节粒度写，可写未初始化 RAM）。配套 `probe_boot.flash_image()`：按需把 0x06000000 起完整 sfc 明文作为 handoff 只读区传入（头 0x100 资源分区表 + app 后资源区），不改默认映射。crc_engine_roundtrip 启用后 155,389→162,447（+70 个 rep.i 体），前 6 场景字节不变。单测 test_boot_bus：真实 0x6010b14 块内存效果=语义展开（65 步、r0/r1 各 +32、32 字节快照全中）、开关关=诚实停 rep.i、控制流体（0x12cb12 rep 24 2 含 if.eq.ri.modimm）诚实拒绝。
- **入口原子性**（cb109b3）：deliver 先完整校验银行假设再提交状态，失败不留部分变化。
- **core tick 机制**（303e388）：deliver_core_tick/return_core_tick + ACK rmw + 停摆策略 + 单测。**首次溢出 ~450k 步 > 当前 153k，未真实触发——C2 未完成。**
- **P11 最小条件响应投影**（sim-p11-proj）：两个位分开建模、分开标注证据等级，互不假定因果。
  - **PWR_CON bit5 请求-响应**（`p11_lpwr_response_projection`，需 reset_history 投影）：只在观察到主核真实的 `rmw.or.modimm [0xf2a400] |= 0x20`（power_init 0x60679a0）之后，该寄存器读回把 bit5 投影为 0（「P11 已处理请求」显式假设）；其余位保持复位快照（POR bit7=0x80）原样；请求之前不臆造；夹带其他位的 RMW / and 形式 / 整字 store 仍严格拒绝；bit6 清标志命令语义不变，清标志后读回仍是 unknown。合并写回不落在严格复位标志路径。UART CON2 写入不制造任何 ready。
  - **PMU_STA bit0 就绪响应**（`p11_pmu_ready_projection`，需 p11_access_probe + 显式基线 fixture 0/1）：复用 P11AccessProbe 的 CON2 序列门控（序列完成后才有意义），在基线 fixture 字之上仅强制 bit0=1；与固定外部状态 fixture（None/0/1）机制并存且独立开关，report 里 `pmu_ready_response_projection` 单独标注「置位机制未证实，证据等级低于 bit5」。不提供基线时读回仍严格 unknown。
  - **堆信息 4 字节显式 fixture**（`p11_heap_info_bytes`，调用方传入 4 字节）：0xf27f4a..4d（P2M 消息区 +0xa，P11 init 写入 heap begin/size，值未证实）按字节粒度读回 fixture；未提供时走普通存储路径严格停（unmapped/uninitialized）。字节粒度经 `BR28Model.read_bytes` 新钩子进 `Memory.read`，不影响字对齐外设分发。
  - 验证：PMU_STA bit0 在真实 p11_init 前缀验证（`p11_ready_response` 场景越过 0x606890c 轮询，83,094 步停在 0x600a57c 0xf2a804，与全字 status=1 场景逐字段一致）；PWR_CON bit5 **尚未在真实前缀观察到**（app_core 路径前沿未到 power_init），以真实指令最小场景（test_boot_bus：0x60679a0 请求 RMW 严格/投影对照、0x60679ae/b0 轮询仅在请求后退出、0x60679ba/be 堆装载 fixture/严格停）+ 模型单测验证。
- **可重复中断交付**（sim-redeliver）：暂停点 1 最小闭环。新 opt-in 开关 `repeat_irq_delivery`（默认关，legacy 5 场景字节不变）：打开后同核「无在途 handler（active_context 非 software_irq_handler/core_tick_handler）即可交付」，handler RTI 返回后允许再次交付；在途仍拒绝（不嵌套）；向量槽/银行假设每次完整重走，pending 只能由固件真实重新 pend（0xffe1a0/0xffe9a0 |=，全部 pend 走 0x60644a6 通用 IPI 请求函数）。单测覆盖 交付→RTI→再交付、在途拒绝、默认关一次性。新场景 irq125_repeat_inert 钉住 repeat 惰性。**真实第二次交付在其当时前沿（154,263）内不可达（实测）：可达前缀内每核只 pend 一次（commands=4：两核各 pend+clear，CPU1 handler 入口 0x60662de 自清 pending）；CPU0 tick 使能后 gate8/9 开放窗口总跨度 ~4.4k 步 < 最小溢出间距 4688 步，两次 tick 交付数学上不可能。第二次交付待 0x606633e handler 尾建模（已落地）后前沿推进观察。**

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

### P11 条件响应投影：显式假设表（2026-11-11 已实现，两个位分开标注）

| 投影 | 规则 | 证据等级 | 延迟/因果 |
|---|---|---|---|
| PWR_CON bit5（LP_PWR_INIT_FLAG） | 仅在观察到主核真实 `rmw.or.modimm [0xf2a400] \|=0x20` 后读回投影 0；其余位保持复位快照；请求前/夹带其他位/and 形式/整字 store 严格 | **局部软件证据**：q32s 0xafa `bitclr r2,0x5` 无条件执行已证实；「主核置位→P11 清位」时序/可达性是推断（q32s 解码 0xa86 有缺口），硅时序未验证 | 响应延迟未建模（假设立即处理）；与 PMU_STA bit0 无因果假定 |
| PMU_STA bit0（就绪） | CON2 请求序列完成后读回=基线 fixture 字 \|1（仅强制 bit0，不发明其他位）；序列外读回仍按门控/fixture 严格 | **弱于 bit5**：只有主核轮询证据（0x606890c/e），P11 侧置位机制无任何软件证据 | 同上，且不假定由 CON2 序列「导致」（只记录条件顺序） |
| 堆信息 4 字节 0xf27f4a..4d | 调用方显式 4 字节 fixture 按字节读回；未提供严格停 | 值完全未知，不臆造具体数值；P11 代码 0xa1c 引用但数值未证实 | — |

开关：`p11_lpwr_response_projection`（需 reset_history 投影提供基线字）、`p11_pmu_ready_projection`（需 p11_access_probe + p11_status 基线）、`p11_heap_info_bytes`。全部默认关闭，legacy 场景字节不变。

**P11 剩余缺口**（推进到 power_init 内部时仍会依次停）：q32s PMU_CON 0xf2a0c0 释放 RMW/bit0（0x60679a4/a8，读回未知）、CLK_CON0 0xf2a414=1、SYS_DIV 0xf2a410=0、P2M_CLK 0xf2a618=8（纯写寄存器，可投影为可写）、p11_code.bin 公共段拷贝到 0xf20080（需 P11 RAM 可写 region）；p11_status_one/ready 前缀的下一停点是 0x600a57c 写 WDT_KEY 0xf2a804（需 watchdog 投影）。这些均未建模，留给下一路。

## 当前并行任务

- 路 A（sim-sfc-clk，c3f399e 已合并推送）：SFC 时钟 +8 分频 / PLL-lock 读回完成。
- 路 B（只读调研）：P11 主核读写依赖清单完成，结论见上节。
- 路 C（sim-cpu1-ctl，已合并）：CPU1 PMU_CON0 kick RMW + 唤醒投影完成。
- 路 D（sim-frame-pop，已合并 96faa99）：CPU1 irq125 handler 尾部 pop.srl 帧弹出 + 对称 RTI 完成，前沿 155,127（0x6000f44）。
- 路 E（sim-p11-proj，已合并 d2eb16b）：P11 最小条件响应投影完成（上表）；主核两条死循环的建模前提已解除，但 app_core 前缀尚未推进到 power_init，bit5 投影仅有真实指令最小场景+单测验证。
- 路 F（sim-redeliver，已合并 f842ed4）：repeat_irq_delivery 最小闭环；repeat_inert 场景在 155,127 前沿实测惰性。
- 路 G（sim-crc，已合并 1ea2f59）：硬件 CRC 引擎 0xfd3500/0xfd3504 建模完成（CRC-16/XMODEM 功能等价，黄金软件参数在断言中重算），旧前沿 155,389。
- 路 H（sim-repi，已合并 fa1a762）：rep.i 多指令体重复（通用直线体 gate，opt-in）+ flash 资源区只读映射完成，旧前沿 162,447（0x6010b88 写 0xfd1c04）。
- 路 I（sim-sfc-clk2，5543138）：SFC 时钟 0xfd1c04 只写记录完成，旧前沿 162,615（0x600a354 读半初始化 qcy_boot_info）。
- 路 J（sim-bootinfo，本次）：qcy_boot_info 交接读调查完成（byte0=app 启动拷贝例程写的 vm.align；bytes 1-3/8-11 无写入者）+ rep 拷贝体未知字节运输投影，前沿 162,646（0x600a374 写 0xfe0224，SFC 主块 +0x24 未建模）。

## 镜像资源级映射（sim-resmap，2026-11-11 完成）

`tools/analyze_image_resources.py`：单命令枚举一个 UFW 的全部资源——外层 UFW 目录（13 条）+ flash.bin 双备份区顶层 JLFS（9 条）+ 目录条目内嵌套子项（9 项：app.bin/VM/PRCT/BTIF/ANCIF/ANCIF1/anc_ext.bin/TON/TON1 分区描述），逐项 CRC 校验 + 内容 SHA256；多镜像按名字对齐 diff（差异资源给出字节游程摘要，不输出内容）。全部 CRC 失败/越界/未知 flags/重名直接报错。已接 check_local 默认检查（无参数自检：官方镜像清单与钉死摘要逐项一致，app.bin 摘要与 probe_boot.APP_SHA 互证）。用法：`python3 tools/analyze_image_resources.py --image path=label ... --report out.json`；`--expected-missing label=sha256` 如实记录不可用镜像。

**三镜像结论**：
- 官方 ↔ 已测试 b79：JLFS 仅 2/9 资源不同——app_area_head（整块 9B：表头/嵌套 CRC 8B + app.bin 内 **0x9e410 单字节** ANC 键循环表，与 AGENTS 钉死事实一致）与 stream.bin（41B：表头 CRC 4B + 音量参数区 0x18f8..0x1954，即 Vol_SysTone/Vol_SysKTone 邻域）；其余 7 资源（含 p11_code.bin、scene.bin、anc_ext.bin、3mic_coeff.bin、cfg_tool.bin、config.dat、分区目录）逐字节相同。外层 13 条仅 flash.bin 不同，TON/TON1 提示音分区字节级相同（-10dB 改动全部落在 flash.bin 内部）。
- **EQ 修复候选（67ba97ef）三镜像 diff 已闭合（2026-11-11 晚，用户提供 artifacts）**：候选 .ufw 实测 SHA256 与钉死值一致，位于仓库外本机目录 `artifacts/eq-fence-repair-offline-20261110/`（含 patch.elf/patch.asm/patch.map/patch.nm/build-report/verification.json 完整证据链，patch.elf 到位使 check_eq_repair 的确定性重建路径可用）。形状校验自动触发并通过：相对 b79 差异严格限于 app_area_head（41B：8B CRC 元数据 + app.bin 内 6 处站点游程，其中 0x34e9a..0x34e9d 正是 fence 上限立即数 06034e98→060e31c5 的 3 字节中段差异）+ config.dat 3B（index 翻转）+ 追加 eq_toggle.bin（337B 载荷，SHA256 f4e9501e…）；`unexpected_diff_found=false`。flash.bin 422B 差异（0x2000 头 CRC + app 内站点 + 0xe5073..0xe51e5 EQ 表区大块 + 边界小改）与钉死的 5+1 站点 + 337B payload 预期一致。
- L2 三镜像差分的数据基础：除 app_area_head/stream.bin 外全部资源可三镜像共享加载；候选差异全部集中在 app.bin 内容 + 追加 eq_toggle.bin。
- 对比报告：`analysis/image-resources.json`（本机 analysis/，git 忽略，不含路径/私有信息）。

## 下一步（按 codex 建议）

1. **SFC 主块 0xfe0200 高偏移写**（新前沿 162,646：0x600a374 `st.w [r1+12]=r0` 写 0xfe0224=r1(0xfe0218)+12，值 0x061d7fe0=VM 区 flash 窗口地址；随后 `[r1+8]=0x7fffffff`→0xfe0220、`[r1+0]|=2`→0xfe0218 RMW）：现有 SFCReadBus 只建模 CON(+0)/BAUD(+4)，+0x18/+0x20/+0x24 语义未知（像是 SFC 间接/DMA 窗口配置）。建模前先反汇编 app+ROM 穷举这三个偏移的全部访问形状（同 0xfd1c00 块的做法）。qcy_boot_info 交接读已闭合（见「关键技术事实」boot_info 条目与 `rep_copy_unknown_transport`）。
2. ~~补齐每个镜像的资源映射~~ 完成：三镜像（官方/b79/EQ 候选）资源级 diff 已闭合，见上节「镜像资源级映射」；rep.i 落地后 flash_image() 只读区可复用于三镜像加载。
3. 事件时间推进（暂停点 2 事件时钟；暂停点 1 机制已落，前沿推进后补真实第二次交付观察）。
4. 按实际启动依赖补电源 0xfd3100、trim、ROM 调用；P11 接口剩 power_init 内部的纯写寄存器与 0xf20080 拷贝（见上节剩余缺口）。
5. 到达事件循环 → D1（可启动 L2 三镜像差分）→ D2（音频流）。

## 关键技术事实（已钉）

- **qcy_boot_info 40B 结构布局（本次钉死，参考 SDK boot.h + app 反汇编 + 162,646 步动态写记录三方一致）**：0x122800..0x122828 是 SDK `BOOT_INFO`（`interface/system/boot.h`），字段/写入者全图：
  | 偏移 | 字段 | 写入者 |
  |---|---|---|
  | 0 | vm.align=0x10 | app 启动拷贝例程 0x60664ee `b[r1+0]=fs_info[13]`（flash_head.align，非 FlashSize）|
  | 1-3 | vm_info C 对齐 padding | **无任何写入者**（残留 RAM）|
  | 4-7 | vm.vm_saddr=0x1da000 | VM init 0x6011102（资源表扫描结果）|
  | 8-11 | vm.vm_res（保留，注释 reverse_bytes）| **无任何写入者** |
  | 12-15 | vm.vm_size=0x4000 | VM init 0x6011108 |
  | 16-23 | sfc{0x2020,0x06000000} | 0x60664e2 `d[r1+16]=handoff+4..12`（BOOT_DEVICE_INFO.sfc）|
  | 24-27 | flash_size=0x1ff000 | 0x60664ea `[r1+24]=fs_info+8`（flash_head.FlashSize）|
  | 28-31 | chip_id+trim | 0x60664f2/f6（handoff+12..16）|
  | 32-39 | bt_mac_addr[8] | 0x60664fc（handoff+16..24）|
  写入侧结论：**ROM 完全不碰 0x122800**（字节级常量搜索 0 命中；页表 memset 0x120000..0x122800 恰好止于其前）；app 6 处引用（0x6000f9c/0x6011016/0x6060a8a/0x6065e76/0x60664dc/0x60c1b14）中只有 0x60664dc（拷贝例程）与 0x6011016（VM init）两处写，其余是读（getter 0x6065e76 返回 sfc_base_addr、0x60c1b14 读 vm_saddr、0x6060aa8 读 vm.align 选 flash 参数、0x6000fa6 读 chip_id）。真机上该结构由 app 自己从 r0 交接对象（ROM 写的 BOOT_DEVICE_INFO，模型里是 0x19fd00 typed fixture）拷贝填充——「ROM/更新器直接写 0x122800」的旧猜测不成立。消费函数 0x600a2aa（SFC 配置分发，命令 0x40045601）只消费 vm_saddr：0x600a352 rep 4 4 拷头 16B、`d[r6+16]`/`[r6+24]` 拷 sfc/flash_size 到 r9+4304 结构，随后只用 dest+24（=vm_saddr）call 0x12C326 算 flash 窗口地址（CPU 可见地址=sfc 基 0x06000000+flash 偏移-0x2020 头），padding/vm_res 拷过去即死。
- **rep.i 编码（本次钉死）**：语法 `rep {b} {n} {`，解码后 b=体字节数（raw×2+2，即体必为偶数字节）、n=重复次数（raw+1，5 位字段最大 32）；固件用法全是小体多次（rep 4 32=4 字节体×32 次、rep 4 8=4 字节体×8 次），体=ld.bu.pin+st.b.pin 字节 memcpy。上游 v3 硬件模型按「次数×整个体」计总效果，与逐条真实执行一致；体内控制流的 REP 语义无硬件捕获证据，多指令体 gate 一律拒绝。0x12cb12 rep 24 2（24 字节体含 if 块）仍由 irq_repeat_return 特例单独处理。
- **flash 资源布局（本次钉死）**：sfc 明文 0x06000000 起——头 0x100 字节=资源分区表（首 0x20 超级头：magic 0x937d3328、0x06000100 入口指针；其后 32 字节定长项、尾 16 字节名字段：app.bin/PRCT/CODE/BTIF/AUTO/ANCIF/ANCIF1，项内含 flash 偏移/crc 字段，精确结构待与 extract_ufw_resources 的 jlfw.yaml 对照）；0x06000100 起=app 切片（842654 字节，至 0x060CDA26）；其后=资源区（ANCIF 等，含 0x060e3074 r2 payload）。启动扫描：RAM 表 0x126560+idx*4 给出 flash 指针，0x6010b14 rep 4 32 拷 32 字节表项头到栈、call 0x6010a64 名字匹配（大小写不敏感，内含 4 个 rep 4 8 拷 8 字节）。
- IPMASK 物理地址 0xFFE168+cnum*0x800；模型常量 0xffe1a8 是另一路径，都更新 IRQController.ipmask。
- 0xFFE200 = EMU_CON（debug control）兼软件 IRQ 源使能位（bits0-3）。
- 调度 IPI 公式 IRQ 127-cnum（两核 handler 同为 0x12AD2A）；同步 IPI IRQ125（CPU1 handler 0x60662c8）。
- CPU1 irq125 handler 尾部形状（app 反汇编钉死）：0x606633a pop.rmask {r4-r0}、0x606633e pop.srl m=41 {psr,rets,reti}、0x6066340 csync、0x6066342 rti——与入口 0x60662c8 push.srl m=41 / 0x60662ca push.rmask m=31 完全对称；RTI 返回被中断的 idle（reti_from_frame=0x12c5ca）。
- **硬件 CRC 引擎 0xfd3500（本次钉死）**：+0=数据口（写一个字节馈入一次，st.w 载荷≤0xff）、+4=种子/结果口（写种子 ≤0xffff，csync 后读回当前 CRC）。**算法参数有黄金证据**：app 软件 CRC-16 半字节表在 0x060a860c（16 项，entry[i]=i·0x1021，GF(2) 乘法）→ 多项式 **0x1021**（x^16+x^12+x^5+1，CRC-16-CCITT/XMODEM 族）、**MSB-first 左移**、**初值=所写种子**、无输出异或/反射；软件实现 0x6000ed6（每字节两个半字节查表 `crc=(crc<<4)^tab[(crc>>12)^nib]`）。wrapper 0x6000f0c 路径选择：icfg.b0 gate 位非零或 0x12cb08（24 字位图检查）返回非零 → 软件路径（r2=0 初值）；否则硬件路径。两路径共用初值 0 = 固件的等价性设计意图。硬件路径：call 0x6064538 取 OS 设备（r6=0x126dc0）→ 种子写 → 馈入循环 → csync → **0x6000f52 配对指令** `r4=[r0+4]∥r0=r6`（4 字节复合，读结果+恢复 r0，watch 证实跳过 0x6000f54）→ call 0x606472a 释放（返回 r0=读数）→ 返回低 16 位。ROM 0xaa1e 同一引擎做 USB transform keystream：种子 0xffff、喂 0xff×2、循环喂 15 字节密钥表（0x1f619）、读 [r2+4] 与数据 XOR。
- IRQ125 = OS_SYNC_SOFT_IRQ，优先级 6 = GIEMASK 类（SDK hwi.h + hwi.c.o IR 双重印证）。
- app_core 帧 0x19f7b0：16B 特殊帧（PC 0x6012028）+ 60B GPR 未初始化（真实语义）。
- SFC 挂起路径 0x12b890（先 baud=0 后 disable）vs 使能路径 0x12e1e8（先 disable 后 baud）。
- **SFC 时钟块 0xfd1c00 访问全图（本次钉死，app 11 处 + ROM 10 处反汇编穷举）**：+0 只有 RMW（bits 0/3/8/9/10/12/14，各挂起/恢复/串行读路径）、整字存三处（ROM 0xc18e=0x20、ROM 0xc0f0=0、app 0x60c5bda 上下文恢复）、bit15 PLL-lock 轮询读（ROM 0xade8、app 0x60c5210）与 app 0x60c5b9c 上下文保存读（同时读 +0 和 +256=0xfd1d00——另一个时钟门，恢复函数 0x60c5bc0 整字写回两者）；+4 只有两处整字写（app 0x6010b88=3、ROM 0xc198=flash 配置字 bits[8:16]），零读回；+8 分频整字写+读回；+0xc/+0x10 仅 ROM 0xae84/0xae86 整字写。SDK 头文件整块无名，全部位语义以反汇编形状为准、未硅验证。
- core tick PRD=1199999（CPU0）/479999999（CPU1），非"PRD=1 高频"（那是独立叶子 0x6066134 未在当前前缀调用）。
- per-core q32DSP SFR bank：0xffe000+core*0x800；PMU_CON0=+0xc0（0xffe0c0/0xffe8c0，SDK 无位定义）、ILAT_SET=+0x1a0、ILAT_CLR=+0x1a4。固件用 `(1-cnum)<<11+0xffe0c0` 算对核 PMU_CON0。
- 跨核同步邮箱（0x1265c0 结构）：+54=命令字节（CPU0 写、CPU1 handler 0x60662c8 读）、+55=标志字节——bit1=发送路径 ack（CPU1 清，CPU0 0x12b66e 等）、bit0=CPU0 应答等待门（CPU0 0x12b716 清、CPU1 idle 醒后检查）、bit2=响应完成（CPU1 0x6066336 清、CPU0 0x12b73c kick 循环等）。

## 资料和硬件边界（不变）

- 历史失败 r2（c8dae523…）禁止重刷；修复候选（67ba97ef…）未验证未刷。
- ROM 公开 Release asset（QCY_H3S_BR28_ROM_000000-020000.bin）。
- RAM 观测候选仍 nondeployable；检查全部本机执行，不用 GitHub CI；发布需用户确认。
