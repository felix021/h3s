# 交接：整机启动仿真推进中（阶段 D 进行，前沿 183,486 步）

2026-11-11 恢复工作。阶段 A/B/C1 完成，C2（tick 真实触发）与 D 进行中。停止时请更新本文件。

## 当前前沿

七级停止阶梯（`tools/probe_irq_entry.py`，逐步 opt-in 叠加，全部回归通过）：

| 场景 | 步数 | 停止点 | kind |
|---|---|---|---|
| irq127_request_strict | 149,660 | 0x12b608 | software_irq_delivery_not_implemented |
| irq127_entry_minimal | 149,679 | 0x12ad68 | unknown_core_special_register (usp) |
| irq127_scheduler_entry | 151,500 | 0x12c5ca | unsupported idle（对核 IDLE 边界）|
| irq127_appcore_roundtrip | 153,430 | 0x12b66a | unmodeled_unmaskable_irq irq125 |
| irq125_sync_roundtrip | 155,127 | 0x6000f44 | unmapped_or_readonly_write 0xfd3504（CRC 投影关闭时的边界）|
| crc_engine_roundtrip | 176,288 | 0x416c | unmapped_memory 读 0x60fdfe0（bank B 诚实停钉子：不开 fixture 的负例）|
| **crc_bankb_erased_fixture** | **183,486** | **0x6060a00** | **unmapped_memory 读 0x61ddfe3（bank B 第二数据窗 flash 偏移 0x1e0000 头字节边界，新前沿）** |

**175,396 步含义**：CRC wrapper（见 155,127 旧含义）之后，flash 资源分区表扫描（70 个 rep.i 体，162,447 旧前沿）、SFC 时钟重配 +0x04=3（162,615）、qcy_boot_info 未知字节运输（162,646）、SFC 窗口组 write-only 记录（162,898）、两核 ICU region 命令（163,075 旧前沿）之后，VM init 链尾部的 **flushinv_dcache**（0x1fdb0→0xc71a→0xc6f4）三件套建模走通：寄存器计数 `rep r1,4`（推广 `immediate_rep_multi_block`，计数来自真实寄存器、计数寄存器按 FM-1 run3 证据递减到 0、体后编译器生成的 `bnez r1` 自然不跳）+ 并行对体（`add.rrr.p` r0+=32 + `st.w [r2]=旧 r0`，按 `exact_parallel_stores` 的「B 读 A 之前旧寄存器」硬件 run3 语义执行，首写 [0xff05cc]=**0x061d7fe0=行地址**，一行）+ **0xff05cc/0xff05c8 DCU CMO 口**（SDK csfr.h JL_L1P 的 CMO[19]/CMO[18]，__WO 只写；opt-in `dcu_cmo_projection` write-only 序列记录）。尾部 csync+ssync 走通（ssync 按 run4「无可见效果」模型，adapter 只在真实执行到的前缀计数），sfc_drop_cache 返回后 VM init 链继续 12,321 步：ROM 拷贝族 0x4156（字节头/字主体/字节尾，寄存器计数 rep+bnez）×4 次、CRC 引擎再用 11 次（合计 12 次往返 360 字节）、CPU0 又 pend irq125 到 CPU1 4 次（合计交付 1+6 次、commands 14/acks 7），随后撞上 ROM 0x4176 字节尾循环 `rep r2,4` count=0（该次拷贝 len&3==0）——**零跳过假设已推广**（opt-in `zero_count_rep_skip`，见「已实现」），跳过两次（两处都是 0x4176 尾）后链继续 892 步：ROM 拷贝族再加 2 个非零寄存器计数 rep（合计 7）、CRC 引擎再用 2 次（合计 14 次往返 420 字节），最后停在 ROM 0x416c（memcpy 出口 0x1fd74→0x413c 的字主体首条 `ld.w.pin`）读 **0x60fdfe0**——源指针由 app 经 ROM 解析，恰为 flash 偏移 **0x100000**（1MiB；app flash 偏移探测表 0x060A0FA4 `{0,0x1000,0x10000,0x80000,0x100000}` 第 5 项，CPU 可见地址=0x06000000+偏移-0x2020 头）。按实测物理布局（docs/usb-recovery.md），0x0ee000 起是 **bank B 应用区**，0x100000 落在 bank B 内；官方 UFW 的 flash.bin 只含引导区+bank A（0..0xE6000），**bank B 字节不在任何公开工件里**——不臆造，诚实停（unmapped_memory）。crc_engine_roundtrip 场景从此钉住这个诚实停（176,288）作为不开 fixture 的负例。

**183,117 步含义（crc_bankb_erased_fixture）**：消费者反汇编（地址级分支图见「关键技术事实」探测表条目）确认 0x60fdfe0 的读是 app `find_partition('otp_cfg')`（0x6010f32）主循环第 5 项的 32 字节头读取、CRC 不匹配分支良性（跳下一项、无副作用）之后，opt-in `erased_bankb_fixture` 把这 32 字节映射为 0xFF 只读（显式「未验证擦除态 fixture」，非已知出厂状态；范围严格限实测读取窗，其余 bank B 继续严格 unmapped）。真实代码自己算 CRC：硬件投影输出 0x75f8（=黄金软件算法 CRC16(30×0xFF)），与 0xFF 头自带期望值 0xffff 在 0x6010f80 比较，不匹配 → r4=5 → 探测表耗尽 → 0x6010fe6 return 1 → 调用者 free 临时对象、清 OTP 全局槽 827/828、VM init 返回值被 0x601205a 忽略（**失败分支良性，是擦除态下的预期主路径**）。链继续 6,829 步：CRC 引擎再用 5 次（15..19 次往返，fed 420→570 字节，新增值 0x75f8/0xc190/0xf271/0xb758/0x5ddd）、CPU0 又 pend irq125 2 次（commands 16/acks 8，交付 1+7 次）、ROM 拷贝族 rep.i 体 80→103、零计数跳过 2→3、寄存器计数 rep 7→10，停在 app 0x6060a74 `d[sp]=r1_r0`（qcy 消费函数 0x6060a4a 内）——**上游 store 模型的簿记读边界**，已解决（见下段与「已实现」exact 双字条目）。

**183,486 步含义（本次推进，+369）**：`st.d.sp.l d[sp]=r1_r0` 是全 8 字节覆盖写（实测上游 m2 模型最终 mem 效果恰为 {addr:R[d], addr+4:R[d+1]}，合并前读旧 word 只是模型内部簿记、对结果无影响），exact_doubleword_stores 扩展后两半直写、旧 unknown 字节被覆盖清除（正确字节语义，非 unknown 传播放松；默认关该 gate 时同指令仍严格停 upstream_store_bookkeeping_read，单测钉死）。链再走 369 步：qcy 消费函数 0x6060a4a 完成首次调用——0x60609ac 在 app 表 0x60a211c 找到描述符（r4=0x41838c 堆结构）、0x6060a74 双字清零栈上 8 字节结构、0x600ad2e 按描述符填充、0x6060a82 把结果 **0x061ddfe0=window(flash 偏移 0x1e0000)** 缓存进全局 0x126544（0x1e0000=VM 区 0x1da000+0x6000，落在 bank B 的 VM 尾与 TON 0x1e1000 之间）；随后同函数尾调 0x60609ec（u16 对表 0x60a1e8c walker），命中项后 `r0=[r4+4]+[0x126544]`=0x061ddfe0、0x6060a00 `ld.bu b[r0+3]` 读 **0x61ddfe3**——bank B 偏移 0x1e0003 的头字节，在 32 字节 fixture 窗口（0x60fdfe0..0x60fe000）之外、公开工件之外，诚实停 unmapped_memory，下一个独立决策点。

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
- **rep 拷贝体未知字节运输**（opt-in `rep_copy_unknown_transport`，需 `immediate_rep_multi_block`）：多指令 rep.i 体内的 `ld.w.pin` 装载若跨未写 RAM 字节，构造 UnknownWord（逐字节来源=源物理地址，经 MMU translate），配套 `st.w.pin` 走 `write_abstract_word` 按来源落盘（未知字节保持未知、目标必须是 RAM region）；全已知字照走常规严格路径。语义依据（见「关键技术事实」boot_info 条目）：0x600a352 rep 4 4 拷贝的源 bytes 1-3/8-11 是结构 padding/保留字段，app/ROM 均无写入者，硅上是残留 RAM 垃圾——模型只运输来源、不臆造数值，任何后续消费者仍严格停。负例：不开开关时同指令停在体首装载 uninitialized_memory（=旧 162,615 前沿形状，单测钉死）。默认关闭，legacy 报告不变。
- **SFC 主块窗口组 +0x18/+0x20/+0x24**（本次，挂既有 `sfc_read_bus` 开关，主/副 SFC 共用）：VM 区 flash 窗口配置，write-only 记录。形状只接受 app 反汇编观测到的：`+0x20/+0x24` 整字写记最后值+次数、`+0x18` 只接受 `rmw.or.modimm`（唯一观测 0x600a37e `|=2`，RMW 不经读回、PartialWord 按位记录、probe_boot 分发 `sfc_window_enable_rmw`）；读回与形状外写（整字写 +0x18 等）一律 Blocked。窗口语义（+0x24=窗口基址、+0x20=0x7ffffff=flash 顶界限、+0x18 bit1=使能）是从两个写入序列的同构性推断的显式假设，SDK 头文件无名、ROM 零引用、无任何读回——bit 语义未硅验证。`check_flash_access` 判定**不变**（固件配置窗口前就大量直访 0x06000000 区，窗口组不是直访门；report `flash_access_gate_unchanged` 标注假设）。单测 test_br28_model：观测形状/拒绝形状/routing/默认关。
- **cpu1_control 释放位（bit0）判定修正**（本次，bug 修复非投影）：kick RMW `|=8`（0x12b732）把 cpu1_control 记为 9，而 advance() 的 CPU1 交付资格判定此前用整字 `==1`——kick 后已释放的 CPU1 被误判未释放，第二次 pend 被静默吞掉（kick 产生「负调度效应」，与「kick 只写记录、无调度效应」的投影声明矛盾）。修正为 bit0 判定（advance() 的 software IRQ/timer pending 两处 + hold-after-release 防御路径）。钉死单测：test_cpu1_release_bit_survives_kick_bit_record（释放+kick+第二次 pend 后 advance 必须检查 CPU1 pending）。
- **真实第二次软件 IRQ 交付观察完成**（crc_engine_roundtrip 叠加 `repeat_irq_delivery`）：窗口组放行后 CPU0 在 162,646..162,898 前缀内第二次 pend irq125 到 CPU1（commands 4→6、acks 3），repeat 投影把停泊的 CPU1 第二次唤醒，handler 0x60662c8 处理完回同一停泊 0x12c5ca——暂停点1（HANDOFF 旧「四个待处理暂停点」第 1 条）的真实观察在此前沿闭合。
- **两核 ICU region 寄存器组**（opt-in `icu_region_projection`，需 `icu_initialization_projection`）：0xffe400/0xffec00 的 +0x14/+0x18 write-only 记录（每核记最后值+次数），+0x10 命令口接受 0xca000020/0x40（ROM 0xc726 按 len>=0x1000 在 0xc730/0xc736 两条谓词 li 里二选一，实测 len=0x20 选 0x40），与 0xca000010 同走既有 CON bit30 + 显式步数完成投影；+0x14/+0x18 读回一律 Blocked（固件全量扫描无读回）。命令低字节语义（0x10/0x20/0x40 对应 SDK cache.h Icu*Region 族哪个操作/粒度）无命名证据，report 显式标注未验证。默认关，legacy 报告不变；单测 test_br28_model（观测形状/拒绝形状/两核路由/默认关/独立开关参数校验）。**后续加强**：SDK csfr.h `JL_TypeDef_q32DSP_ICU` 的 CMD_CON/CMD_BEG/CMD_END 恰在 +0x10/+0x14/+0x18——上一里程碑从 ROM 形状推断的 region 寄存器偏移与 SDK 结构体命名一致。
- **DCU CMO 逐地址维护口 + 寄存器计数 rep 并行对体 + ssync（本次，三合一）**：
  - **`dcu_cmo_projection`**（br28_model.DcuCmoProjection，独立开关默认关）：0xff05c8/0xff05cc write-only 序列记录（每口记值序列+次数，>64 按首 32+末 32 截断），读回一律 Blocked（`dcu_cmo_readback_not_modeled`）。SDK 锚点：csfr.h 把 JL_L1P（0xff0500，共享 L1P cache）类型化为 `JL_TypeDef_q32DSP_DCU`，其 **CMO[32]**（+0x80..+0xff）标注 **__WO 只写**；0xff05c8=CMO[18]（flush_dcache 0xc780 用）、0xff05cc=CMO[19]（flushinv_dcache 0xc71a 用），逐 index 语义 SDK 未命名（report 显式标注），32 字节行步长按循环体推断未硅验证，cache 内容/一致性不模拟。固件全量字节扫描：两个口各只有 ROM wrapper 一处 li.u32 引用（0xc784/0xc71e），app 零引用、无任何读回。
  - **寄存器计数 rep 推广**（`immediate_rep_multi_block` 扩展，adapter `register_rep_multi_block_projection`）：接受 `rep {r} {n}`（operands：r=计数寄存器、n=体字节数=raw*2+2），计数=真实寄存器值——非 int（UnknownWord/IcfgValue）停 `rep_register_count_unknown`、count==0 停 `unverified_zero_count_rep`（GAPS.md 未测）、>10000 停（防失控）。计数寄存器随每轮回卷递减到 0（既有 repeat 机制 `register` 字段；FM-1 run3 硬件证据「体执行次数=计数寄存器值，寄存器 ends at 0」，与 ROM 编译形状「体后 bnez r计数控回 rep」两种硬件解释的可观测结果一致，bnez 自然不跳）。**体接受并行对**：`.p` 指令+第二半按一条计字节，要求 `exact_parallel_stores` 已支持的形状（纯 ALU 半 li./mov./add./sub./and./or./xor./div. 或 ext.b0.u + st.w 族第二半），否则停新 kind `rep_multi_block_body_parallel_pair_unsupported`；执行期由该 gate 按「B 读 A 之前旧寄存器」（run3）精确执行——失效循环里 st.w 存的是**递增前的 r0（行地址）**，修正了旧 HANDOFF「首次 [0xff05cc]=0x061d8000」的误记。rep.i 路径字节不变。
  - **ssync 无效果模型**（adapter `ssync_noop`，报告键只在真实执行到的前缀出现）：上游只有 run4 runner 上下文 trace 模型（effect() 拒绝）；按 FM-1 run4 捕获「flags 0/15 下无任何可见寄存器/标志/sp/rets/scratch 变化」以与 csync（k2 模型仅保 flags）同形状的无效果模型执行，顺序/屏障语义显式不建模。首个出现点 0xc716（flushinv_dcache 尾）。
  - 前沿 163,075→175,396（+12,321），前 6 场景报告逐字节不变；单测 test_br28_model（write-only 记录/截断/读回拒绝/默认关路由）+ test_boot_bus（ROM 0xc70c-0xc712 精确编码钉只读区：3 轮循环的旧寄存器存储序列/r0 递增/r1 归零、不开 gate 诚实停 rep、并行对 gate 未开停形状拒绝、count=0 停、ssync 无效果模型）。
- **零计数 rep 跳过假设推广**（opt-in `zero_count_rep_skip`，需 `immediate_rep_multi_block`）：memset 先例 `rom_zero_rep_skip`（probe_boot 0x4242/0x424c，count=0 时 PC 跳过体、计数寄存器不动、adapter `rom_zero_rep_skip_hypothesis`）推广到 multi-block gate 的寄存器计数形式——count=0 时体一次也不执行、PC 前进到体后、计数寄存器保持 0（FM-1 run3「递减到 0」在 0 次执行下的自然延伸），体形状仍须先通过与非零计数完全相同的直线体校验；报告以 adapter `zero_count_skip_is_assumption` 计数、不声称硅验证（count=0 本身 BR28 未测量，GAPS.md「Not tried: rep with a count of 0」；FM-1 run3 只捕获非零计数）。开关默认关，不开仍诚实停 `unverified_zero_count_rep`，legacy 报告不变。前沿 175,396→176,288（+892），前 6 场景字节不变；单测 test_boot_bus（跳过/计数寄存器不变/体零内存效果/默认关由 dcu_rep_zero 子例钉死）。
- **bank B 头 0xFF fixture + 消费者分支图**（opt-in `erased_bankb_fixture`）：先反汇编 CRC 消费者（选项 4），钉死 `find_partition('otp_cfg')` 0x6010f32 的地址级分支图（见「关键技术事实」）——CRC 期望值=头自带前 2 字节、比较在 0x6010f80、不匹配分支良性（跳下一项，5 项全败 return 1，调用者只 free+清 OTP 槽，VM init 返回值被 0x601205a 忽略），据此落地显式环境假设：把本前缀实测的唯一 bank B 读取窗（探测表第 5 项头 32 字节，CPU 0x60fdfe0..0x60fe000）映射为 0xFF 只读。措辞为「**unverified erased-bank fixture**，非已知出厂状态」（report `erased_bankb_fixture_assumption` 全文标注）；真实代码自己算 CRC（0x75f8）自己走不匹配分支（擦除态下的预期主路径），不强制任何结果；范围外 bank B 继续严格 unmapped，单测钉界（紧邻区外/默认无 fixture 都停 unmapped、写拒绝、黄金 CRC 算术 0x75f8∉{0,0xffff}）。crc_engine_roundtrip 场景不动（继续钉 176,288 诚实停），新场景 crc_bankb_erased_fixture 推进前沿 176,288→183,117（+6,829）。
- **st.d.sp.l 精确双字存储（本次，扩展现有 `exact_doubleword_stores`，非新开关）**：`d[sp]=r1_r0`（qcy 消费函数 0x6060a4a 内 0x6060a74，operands {r:源偶寄存器,n:sp 偏移}）此前走上游 m2 store 模型，Writes.put 合并写前读旧 word（实测该模型对 8 字节双字的最终 mem 效果恰为 {addr:R[d], addr+4:R[d+1]}——旧 word 只是合并簿记与「未变化字丢弃」优化，对结果无影响），sp 落未初始化区时把簿记读当错误停（kind=upstream_store_bookkeeping_read）。决策选 **方案 a（st.d.l 先例的形状扩展）而非 b（放宽簿记读）**：双字存储是全 8 字节覆盖写，旧值语义上无关，两半都是真实寄存器值（UnknownWord 源仍按 consumed 严格停）、地址 4 对齐即整字覆盖——直接 `mem.write` 两半、不读旧值，旧 unknown 字节被清除（**正确字节语义，不是 unknown 传播放松**）；方案 b 要动共享的 m2/适配层读路径，会影响所有 store 形状（部分字存储/rmw/testset 的旧值读是真实语义），无先例、改动面大。gate 复用 st.d.l 既有开关（默认关、形状外/源未知仍严格停），adapter 计数复用 `exact_doubleword_store`（本前缀 10→13：0x6060a74 一处 + 后续链两处）。前 7 场景报告逐字节不变（probe_irq_entry 断言 + diff 钉死），单测 test_boot_bus：sp 形状覆盖写清除旧 unknown 且邻居保持 unknown、默认关严格停 upstream_store_bookkeeping_read。前沿 183,117→183,486（+369）。
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
- **可重复中断交付**（sim-redeliver）：暂停点 1 最小闭环。新 opt-in 开关 `repeat_irq_delivery`（默认关，legacy 5 场景字节不变）：打开后同核「无在途 handler（active_context 非 software_irq_handler/core_tick_handler）即可交付」，handler RTI 返回后允许再次交付；在途仍拒绝（不嵌套）；向量槽/银行假设每次完整重走，pending 只能由固件真实重新 pend（0xffe1a0/0xffe9a0 |=，全部 pend 走 0x60644a6 通用 IPI 请求函数）。单测覆盖 交付→RTI→再交付、在途拒绝、默认关一次性。新场景 irq125_repeat_inert 钉住 repeat 惰性。~~真实第二次交付在其当时前沿（154,263）内不可达~~ **已闭合（162,898 前沿）**：SFC 窗口组放行后 CPU0 第二次 pend irq125 到 CPU1（见「已实现」窗口组与释放位修正两条），交付还需要 cpu1_control 释放位 bit0 修正（kick 位记录不再污染交付资格）。

## codex (gpt-6-astra) 参谋结论（2026-11-11 二次，已核实）

阶段划分修正：
- **C1**（IPI 调度进入应用）：条件模型下完成。
- **C2**（时间驱动恢复）：未完成。退出条件=实际计数产生 pending→IRQ3→真实 handler/ACK→RTOS 时间或就绪状态变化→任务恢复并继续进展。机制+单测不能替代。
- **D1**（控制服务就绪）：初始化依赖闭合；真实队列/分派接收输入；产生可检查状态变化；随后还能处理下一事件及定时事件。可启动 L2 差分。
- **D2**（音频服务就绪）：真实软件创建/启动/停止/重建音频链。为 L3 提供环境。

"完整仿真"边界（诚实定义）：**指定 UFW/初态/场景集合的整机软件功能仿真；硬件用列明契约的模型，未认证语义保持条件结论。** 软件（CPU0/1 指令、ROM、调度器、handler、锁队列、资源解析、EQ 钩子）真实执行；硬件（Flash 总线、计时器、中断控制器、DMA、GPIO、ADC/DAC 端、蓝牙外部环境）功能模型；P11 可用接口等价模型（不声称验证 P11 软件）；射频/功耗/模拟/声压不能离线认证。

三镜像 EQ 差分可提前到 L2（事件循环+EQ 控制面），不必先跑通蓝牙/PCM。L3 才需 PCM 证据。

## 四个待处理暂停点（codex 指出，按优先级）

1. **一次性交付限制**：~~software IRQ / tick 都要求"该核从未交付过"~~ **已解决（机制层 + 真实观察）**：`repeat_irq_delivery` opt-in 开关实现「返回后可重新交付」最小闭环（无在途 handler 即可交付、不嵌套、全量重校验、pending 只认真实 pend）；**真实第二次交付已在 162,898 前沿观察到**（SFC 窗口组建模后 CPU0 第二次 pend irq125 到 CPU1，见「已实现」），交付时还修掉了 cpu1_control kick 位污染释放判定的 bug。
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
- 路 J（sim-bootinfo，已合并）：qcy_boot_info 交接读调查完成（byte0=app 启动拷贝例程写的 vm.align；bytes 1-3/8-11 无写入者）+ rep 拷贝体未知字节运输投影，旧前沿 162,646。
- 路 K（sim-sfc-win，已合并 89de49f）：SFC 主块窗口组 0xfe0218/+0x20/+0x24 write-only 建模 + cpu1_control 释放位修正 + 真实第二次交付观察完成，旧前沿 162,898。
- 路 L（sim-icu，已合并）：两核 ICU region 寄存器组建模（opt-in `icu_region_projection`）完成，旧前沿 163,075。
- 路 M（sim-dcu，已合并 bcaa4a8）：DCU CMO 口 0xff05c8/0xff05cc + 寄存器计数 rep 并行对体 + ssync 三合一完成，旧前沿 175,396（ROM 0x4176 拷贝尾循环 count=0 rep，零计数语义决策点）。
- 路 N（sim-zerorep）：零计数 rep 跳过假设从 memset 两处先例推广到 multi-block gate（opt-in `zero_count_rep_skip`）完成，旧前沿 176,288（ROM 0x416c 读 bank B flash 0x60fdfe0，公开工件不含该字节，数据可得性边界）。
- 路 O（sim-bankb）：bank B 数据边界处理完成——先反汇编消费者（find_partition 分支图，失败分支良性），再落地 opt-in `erased_bankb_fixture`（32 字节 0xFF 显式环境假设），前沿 183,117。
- 路 P（sim-store，本次）：st.d.sp.l 精确双字存储（上游 store 簿记读决策，方案 a 形状扩展）完成，前沿 183,486（0x6060a00 读 bank B 第二数据窗 0x1e0000 头字节 0x61ddfe3，诚实停 unmapped）。

## 镜像资源级映射（sim-resmap，2026-11-11 完成）

`tools/analyze_image_resources.py`：单命令枚举一个 UFW 的全部资源——外层 UFW 目录（13 条）+ flash.bin 双备份区顶层 JLFS（9 条）+ 目录条目内嵌套子项（9 项：app.bin/VM/PRCT/BTIF/ANCIF/ANCIF1/anc_ext.bin/TON/TON1 分区描述），逐项 CRC 校验 + 内容 SHA256；多镜像按名字对齐 diff（差异资源给出字节游程摘要，不输出内容）。全部 CRC 失败/越界/未知 flags/重名直接报错。已接 check_local 默认检查（无参数自检：官方镜像清单与钉死摘要逐项一致，app.bin 摘要与 probe_boot.APP_SHA 互证）。用法：`python3 tools/analyze_image_resources.py --image path=label ... --report out.json`；`--expected-missing label=sha256` 如实记录不可用镜像。

**三镜像结论**：
- 官方 ↔ 已测试 b79：JLFS 仅 2/9 资源不同——app_area_head（整块 9B：表头/嵌套 CRC 8B + app.bin 内 **0x9e410 单字节** ANC 键循环表，与 AGENTS 钉死事实一致）与 stream.bin（41B：表头 CRC 4B + 音量参数区 0x18f8..0x1954，即 Vol_SysTone/Vol_SysKTone 邻域）；其余 7 资源（含 p11_code.bin、scene.bin、anc_ext.bin、3mic_coeff.bin、cfg_tool.bin、config.dat、分区目录）逐字节相同。外层 13 条仅 flash.bin 不同，TON/TON1 提示音分区字节级相同（-10dB 改动全部落在 flash.bin 内部）。
- **EQ 修复候选（67ba97ef）三镜像 diff 已闭合（2026-11-11 晚，用户提供 artifacts）**：候选 .ufw 实测 SHA256 与钉死值一致，位于仓库外本机目录 `artifacts/eq-fence-repair-offline-20261110/`（含 patch.elf/patch.asm/patch.map/patch.nm/build-report/verification.json 完整证据链，patch.elf 到位使 check_eq_repair 的确定性重建路径可用）。形状校验自动触发并通过：相对 b79 差异严格限于 app_area_head（41B：8B CRC 元数据 + app.bin 内 6 处站点游程，其中 0x34e9a..0x34e9d 正是 fence 上限立即数 06034e98→060e31c5 的 3 字节中段差异）+ config.dat 3B（index 翻转）+ 追加 eq_toggle.bin（337B 载荷，SHA256 f4e9501e…）；`unexpected_diff_found=false`。flash.bin 422B 差异（0x2000 头 CRC + app 内站点 + 0xe5073..0xe51e5 EQ 表区大块 + 边界小改）与钉死的 5+1 站点 + 337B payload 预期一致。
- L2 三镜像差分的数据基础：除 app_area_head/stream.bin 外全部资源可三镜像共享加载；候选差异全部集中在 app.bin 内容 + 追加 eq_toggle.bin。
- 对比报告：`analysis/image-resources.json`（本机 analysis/，git 忽略，不含路径/私有信息）。

## 下一步（按 codex 建议）

1. ~~零计数 rep 语义决策~~ 完成（opt-in `zero_count_rep_skip`）。~~bank B flash 读决策~~ 完成（2026-11-12：opt-in `erased_bankb_fixture` 32 字节 0xFF 显式假设，前沿 183,117）。~~0x6060a74 上游 store 簿记读~~ 完成（2026-11-12：`exact_doubleword_stores` 扩展到 st.d.sp.l，全 8 字节覆盖写两半直写不读旧 word，方案 a 见「已实现」；前沿 183,486）。**新决策点：bank B 第二数据窗 0x1e0000**——qcy 消费函数把 window(0x1e0000)=0x061ddfe0 缓存进全局 0x126544 后，0x60609ec 表查找命中项在 0x6060a00 读头字节 0x61ddfe3；该窗在 32 字节 fixture 窗口之外、公开工件之外（bank B VM 尾与 TON 之间），需要先反汇编消费者钉死分支图再决定是否扩 fixture（同 find_partition 先例）。若日后拿到 bank B 真实字节的合法数据源，可按只读 fixture 替换 0xFF 对照。
2. ~~补齐每个镜像的资源映射~~ 完成：三镜像（官方/b79/EQ 候选）资源级 diff 已闭合，见上节「镜像资源级映射」；rep.i 落地后 flash_image() 只读区可复用于三镜像加载。
3. 事件时间推进（暂停点 2 事件时钟；暂停点 1 机制+真实观察已落，162,898 前缀内 CPU1 两次 irq125 交付都发生在停泊唤醒路径）。
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
- **flash 偏移探测表 0x060A0FA4 与 bank B 读消费者分支图（本次钉死，app 反汇编 + watch trace 逐分支验证）**：app 函数 **0x6010f32 = find_partition(name)**（本前缀实参 'otp_cfg'@0x60a25a8，r0=出参结构 0x41838c/r1=名字串），主循环从 0x060A0FA4 处 5 项表 `{0,0x1000,0x10000,0x80000,0x100000}`（flash 偏移）逐项探测「应用区头」：
  | PC | 指令 | 语义/分支 |
  |---|---|---|
  | 0x6010f5c | `ifs (r4>4) goto 134`→0x6010fe6 | 候选耗尽 → r1=2、r0=1 → **return 1（失败）** |
  | 0x6010f60 | `r5=[0x060A0FA4+r4<<2]` | 下一候选偏移 |
  | 0x6010f6c | `call 0x6007c60(buf=sp+32,0,32,r5)` | 读 32B 头（memset OS 设备 0x126DC0 缓冲 [0x127E44] 界限检查、缓冲 0x1275D0 → ROM 解析 CPU 指针=0x06000000+偏移-0x2020 → memcpy 出口 0x1fd74→0x413c/0x416c 字主体 → 拷进 MMU 虚拟栈缓冲，本前缀 sp≈0x400a80） |
  | 0x6010f74 | `call 0x6000f0c(sp+34,30)` | **CRC16 头数据 30 字节**（软/硬件路径由 icfg gate 选，本前缀全走硬件引擎投影；期望值=头自带前 2 字节，`h[头].l`） |
  | 0x6010f78 | `if (crc==0) goto 0x6010f5a` | CRC==0 → 视为无效 → 下一项 |
  | 0x6010f80 | `if (期望!=crc) goto 0x6010f5a` | **CRC 不匹配 → 良性跳过（无副作用）** |
  | 0x6010f84..0x6010f8e | 匹配后进内层 slot 循环（≤128 slot，逐 32B slot 头同构 CRC+0x6010fb8 call 0x1fd82 名字匹配） | 找到 → 0x6010fc2 拷 slot 头+序号 → return 0；slot CRC 不匹配 → r1=1 → 0x6010fe8 → **return 0**（表尾即止） |
  实测（176,288 前缀 trace）：前 4 项（偏移 0/0x1000/0x10000/0x80000，均在官方 flash.bin 内）CRC 分别 0xd97f/0xd97f/0x33c6/0xaaa8 vs 期望 0x2000/0x2000/0x8a6e/0xe880 全不匹配（偏移 0 与 0x1000 解析到同一数据）；第 5 项 0x100000 落在 **bank B 应用区**（实测物理布局 docs/usb-recovery.md：bank B boot-info 0x0ee000、密文 0x0ee020 起 930,245B；VM 0x1da000、TON/TON1 0x1e1000/0x1ed000、ANCIF 0x1fe000），**bank B 字节不在官方 UFW 或任何公开工件里**（外层目录只有 flash.bin 一份镜像，bank B 由 updater 写入）——不开 fixture 诚实停 unmapped。**调用者分支图**：VM init（0x6011000）内 0x60111f8 调用 find_partition，0x6011210 `if (r0==0)` 分流——成功 0x6011240 把 [obj+8]/[obj+12] 拷进全局槽 827/828（0x126560+0xCEC/0xCF0=0x12724C/0x127250，OTP 配置缓存）后 free；失败 0x6011212 free 36B 临时对象（ROM export 0x12e18e）+ 清全局槽 827/828=0；两路径在 0x6011232 汇合继续 `call 0x600a4d6(vm_saddr,1)`（VM 区地址换算，与 otp 无关），VM init 返回值在平台 init 链 0x601205a 处**被完全忽略**（后续是 0x0C326DC8/0x0C326D5C 两个函数指针表遍历）——**失败分支良性**。0xFF 擦除态 fixture（32 字节）下的实测分支：CRC=0x75f8（=黄金软件算法 CRC16(30×0xFF)）≠ 期望 0xffff ≠ 0 → 0x6010f80 不匹配 → r4=5 → 0x6010fe6 return 1 → 清 OTP 槽 → 链继续（擦除态下失败分支就是主路径）。
- **qcy 消费函数 0x6060a4a 与全局 0x126544（本次钉死，反汇编+动态 watch 三方一致）**：首次调用链——0x60609ac 在 app 表 0x60a211c 找描述符（返回堆结构 r4=0x41838c）、`st.d.sp.l d[sp]=r1_r0`（0x6060a74，operands {r:0,n:0}，全 8 字节覆盖写）清零栈上结构、call 0x600ad2e 填充、0x6060a82 `st.w [0x126544]=填充结果`——结果即 **0x061ddfe0=window(flash 偏移 0x1e0000)**（0x06000000+0x1e0000-0x2020；0x1e0000=VM 0x1da000+0x6000，bank B 的 VM 尾与 TON 0x1e1000 之间，字节不在任何公开工件）；app 内 0x126544 只有 0x60609f6/0x6060a4e 两处 li 引用（都是读），初始化写只有 0x6060a82 一处（运行时 watch 钉死；另有一处 app 早期 0x6000132 的 bss 清零写 0）。随后同函数调 0x60609ec：0x60609be 起 u16 对表 walker（表 0x60a1e8c，h[r1] 匹配累加偏移），命中后 `r0=[r4+4]+[0x126544]`、0x6060a00 `ld.bu b[r0+3]` 读该窗头字节（本前缀 [r4+4]=0 → 0x61ddfe3）。上游 m2 对 st.d.sp.l 的 mem 效果实测恰为 {addr:R[d],addr+4:R[d+1]}（Writes 合并读纯簿记），是 exact_doubleword_stores 扩展的语义依据。
- IPMASK 物理地址 0xFFE168+cnum*0x800；模型常量 0xffe1a8 是另一路径，都更新 IRQController.ipmask。
- 0xFFE200 = EMU_CON（debug control）兼软件 IRQ 源使能位（bits0-3）。
- 调度 IPI 公式 IRQ 127-cnum（两核 handler 同为 0x12AD2A）；同步 IPI IRQ125（CPU1 handler 0x60662c8）。
- CPU1 irq125 handler 尾部形状（app 反汇编钉死）：0x606633a pop.rmask {r4-r0}、0x606633e pop.srl m=41 {psr,rets,reti}、0x6066340 csync、0x6066342 rti——与入口 0x60662c8 push.srl m=41 / 0x60662ca push.rmask m=31 完全对称；RTI 返回被中断的 idle（reti_from_frame=0x12c5ca）。
- **硬件 CRC 引擎 0xfd3500（本次钉死）**：+0=数据口（写一个字节馈入一次，st.w 载荷≤0xff）、+4=种子/结果口（写种子 ≤0xffff，csync 后读回当前 CRC）。**算法参数有黄金证据**：app 软件 CRC-16 半字节表在 0x060a860c（16 项，entry[i]=i·0x1021，GF(2) 乘法）→ 多项式 **0x1021**（x^16+x^12+x^5+1，CRC-16-CCITT/XMODEM 族）、**MSB-first 左移**、**初值=所写种子**、无输出异或/反射；软件实现 0x6000ed6（每字节两个半字节查表 `crc=(crc<<4)^tab[(crc>>12)^nib]`）。wrapper 0x6000f0c 路径选择：icfg.b0 gate 位非零或 0x12cb08（24 字位图检查）返回非零 → 软件路径（r2=0 初值）；否则硬件路径。两路径共用初值 0 = 固件的等价性设计意图。硬件路径：call 0x6064538 取 OS 设备（r6=0x126dc0）→ 种子写 → 馈入循环 → csync → **0x6000f52 配对指令** `r4=[r0+4]∥r0=r6`（4 字节复合，读结果+恢复 r0，watch 证实跳过 0x6000f54）→ call 0x606472a 释放（返回 r0=读数）→ 返回低 16 位。ROM 0xaa1e 同一引擎做 USB transform keystream：种子 0xffff、喂 0xff×2、循环喂 15 字节密钥表（0x1f619）、读 [r2+4] 与数据 XOR。
- IRQ125 = OS_SYNC_SOFT_IRQ，优先级 6 = GIEMASK 类（SDK hwi.h + hwi.c.o IR 双重印证）。
- app_core 帧 0x19f7b0：16B 特殊帧（PC 0x6012028）+ 60B GPR 未初始化（真实语义）。
- SFC 挂起路径 0x12b890（先 baud=0 后 disable）vs 使能路径 0x12e1e8（先 disable 后 baud）。
- **SFC 主块 0xfe0200 访问全图（本次钉死，app+ROM 线性扫描解码 + 操作数常量比对穷举）**：+0（CON）/+4（BAUD）固件两侧大量使用（已建模）。**窗口组 +0x18/+0x20/+0x24 只有 app 两个引用点、ROM 零引用、SDK 头文件无名**：①0x600a36e..0x600a37e（SFC 配置分发，命令 0x40045601 的 VM 分支）：`r1=0xfe0218`、`[r1+12]=call 0x12bdc6(vm_saddr)` 返回 0x061d7fe0（VM flash 窗口地址=0x06000000+0x1da000-0x2020）、`[r1+8]=0x7ffffff`（=0x08000000-1 flash 直访区顶）、`[r1+0]|=2`；②SFC 上下文恢复 0x60c5bc0 尾 0x60c5bfa..0x60c5c0a：`r1=0xfe0220`、`[r1+4]=call 0x60c55a0(保存值)`、`[r1+0]=0x7ffffff`，**不重写 +0x18**（与「bit1 是窗口使能位」自洽但未硅验证）。0x60c55a0 是 app 侧「flash 偏移→CPU 可见地址」换算（全局模式字节 0x1265cd==1 → +0x4000000 走 MMU，否则直访换算），与 RAM 函数 0x12bdc6 同功能。读写结论：**全部是写，无任何读回**——窗口组 write-only。+0x20 两次观测值都是 0x7ffffff；CPU 在窗口配置前就大量直访 0x06000000..0x08000000（CON bit0 使能即可），窗口组不是直访门（模型判定不变的显式假设）。0x60c5b9c/0x60c5bc0 上下文保存/恢复只碰 0xfd1c00/0xfd1d00 时钟门，窗口恢复值来自软件保存区（0x126500+1082*4），不是硬件读回。
- **两核 ICU/DCU cache 控制访问全图（本次钉死，ROM 反汇编 + export 名映射 + 163,075 动态记录三方一致）**：ROM export→实现：`IcuInitial` 0x1fdbc→0xbae0（cli→testset 0x19fd78 抢锁→0xbafc 命令 0xca000010→0xbaca 轮询 CON bit30→0xbadc 释放锁→sti→CON|=1，CPU1 bootstrap 已建模路径）、`IcuWaitIdle` 0x1fdb4→0xc792（轮询两核 CON **bit31**，r1=0x3ff900、地址=r1*4+core<<11，`bgts.ri r3,-1` 按符号判 bit31）、`sfc_drop_cache` 0x1fdcc→0xc726（签名 (beg,len) 同 SDK cache.h Icu*Region 族；每核 cli→testset 0x19fd78→`[+0x14]`=begin、`[+0x18]`=end-1→`[+0x10]`=命令字→call 0xbac0（csync+轮询本核 CON bit30，与 IcuInitial 同一条 ld.w.idxs@0xbaca）→call 0xbad4（释放锁）→sti，r3+=0x800 换核循环 2 次；实测实参 beg=0x061d7fe0/len=0x20，即 32 字节窗口 [0x061d7fe0,0x061d8000)，命令字选 **0xca000040**）、`flush_dcache` 0x1fdac→0xc780（r2=0xff05c8 调 0xc6f4 同族循环）、`flushinv_dcache` 0x1fdb0→0xc78c→0xc71a（r2=0xff05cc 调 0xc6f4）、`DcuWaitIdle` 0x1fdb8→0xbb20（共享 L1P CON 0xff0500 bit31）、`DcuInitial` 0x1fdc0→0xbb30。命令字族 0xca0000{10,20,40}：低字节 bit4/5/6 单位热命令选择；0xc72c `if.ge.ri r1>=0x1000` 在 0xc730(0x20)/0xc736(0x40) 两条谓词 li 里二选一，**实测 len=0x20 选 0x40**（len<0x1000→0x40，另一分支方向未测）；0x10=cache 使能路径、0x20/0x40=region 操作，对应 SDK cache.h IcuLockRegion/IcuUnlockRegion/IcuPfetchRegion/IcuFlushinvRegion 的哪个操作**无命名证据**。+0x14/+0x18 region 数据寄存器 write-only（固件全量扫描无读回）；0xc6f4 DCU 失效循环：迭代数 r1=((len+(beg&0x1f))>>5)+(末址未对齐?1:0)，体=add.rrr.p（r0+=0x20）+st.w（[r2]=r0）并行对（并行对两侧按上游模型语义用旧寄存器值），指令是**寄存器计数 `rep r1,4`**——与 rep.i（立即数计数）不同形式。**本次补充钉死**：①SDK csfr.h 把 0xff0500 命名为 **JL_L1P**（csfr_base 0xff0000+0x500，共享 L1P cache），类型化 `JL_TypeDef_q32DSP_DCU`：CON+0（bit31 空闲=DcuWaitIdle 轮询位）、CMD_CON[4]/CMD_BEG[4]/CMD_END[4] +0x40/+0x50/+0x60、**CMO[32] +0x80..+0xff 标注 __WO 只写**——0xff05c8=CMO[18]、0xff05cc=CMO[19]，逐 index 操作语义 SDK 未命名，由 ROM wrapper 区分；②ICU 结构体 q32DSP_ICU 的 CMD_CON/CMD_BEG/CMD_END 恰在 +0x10/+0x14/+0x18，与上一里程碑从 ROM 形状推断的 region 寄存器偏移一致；③寄存器计数 rep 语义有 FM-1 run3 硬件证据（hw/fm1-2026-10-24-v3：体执行次数=计数寄存器值、寄存器递减到 0；并行对 B 读 A 之前旧寄存器）——失效循环每轮存**递增前的 r0（行地址）**，实测首写 [0xff05cc]=0x061d7fe0（旧记录 0x061d8000 有误）；ROM 编译形状在体后跟 `bnez r计数控,回 rep`，与「rep 自回卷」解释可观测等价；④count=0 无硬件捕获（GAPS.md「Not tried」），ROM 拷贝族 0x4156（字节头/字主体/字节尾三段 rep+bnez）尾部真实出现 count=0（0x4176，本前缀两次）——已按 memset 先例推广的 opt-in `zero_count_rep_skip` 假设跳过（证据等级=假设，FM-1 run3 非零证据的延伸，硅未验证）。
- **SFC 时钟块 0xfd1c00 访问全图（本次钉死，app 11 处 + ROM 10 处反汇编穷举）**：+0 只有 RMW（bits 0/3/8/9/10/12/14，各挂起/恢复/串行读路径）、整字存三处（ROM 0xc18e=0x20、ROM 0xc0f0=0、app 0x60c5bda 上下文恢复）、bit15 PLL-lock 轮询读（ROM 0xade8、app 0x60c5210）与 app 0x60c5b9c 上下文保存读（同时读 +0 和 +256=0xfd1d00——另一个时钟门，恢复函数 0x60c5bc0 整字写回两者）；+4 只有两处整字写（app 0x6010b88=3、ROM 0xc198=flash 配置字 bits[8:16]），零读回；+8 分频整字写+读回；+0xc/+0x10 仅 ROM 0xae84/0xae86 整字写。SDK 头文件整块无名，全部位语义以反汇编形状为准、未硅验证。
- core tick PRD=1199999（CPU0）/479999999（CPU1），非"PRD=1 高频"（那是独立叶子 0x6066134 未在当前前缀调用）。
- per-core q32DSP SFR bank：0xffe000+core*0x800；PMU_CON0=+0xc0（0xffe0c0/0xffe8c0，SDK 无位定义）、ILAT_SET=+0x1a0、ILAT_CLR=+0x1a4。固件用 `(1-cnum)<<11+0xffe0c0` 算对核 PMU_CON0。
- 跨核同步邮箱（0x1265c0 结构）：+54=命令字节（CPU0 写、CPU1 handler 0x60662c8 读）、+55=标志字节——bit1=发送路径 ack（CPU1 清，CPU0 0x12b66e 等）、bit0=CPU0 应答等待门（CPU0 0x12b716 清、CPU1 idle 醒后检查）、bit2=响应完成（CPU1 0x6066336 清、CPU0 0x12b73c kick 循环等）。

## 资料和硬件边界（不变）

- 历史失败 r2（c8dae523…）禁止重刷；修复候选（67ba97ef…）未验证未刷。
- ROM 公开 Release asset（QCY_H3S_BR28_ROM_000000-020000.bin）。
- RAM 观测候选仍 nondeployable；检查全部本机执行，不用 GitHub CI；发布需用户确认。
