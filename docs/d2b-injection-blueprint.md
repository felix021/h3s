# D2-b 音频注入实施蓝图：DMA 预填 + PND/IRQ68 交付的落地方径（路 AW / sim-d2bdesign）

2026-11-14 08:15 路 AW（sim-d2bdesign，基线 336e848）。把 d1-acceptance §6.2 的 D2-b 验收
框架（三场景配置表 + b1-b4 断言 + codex 数据设计）与 audio-input-paths 的 AE/AL/AP/AR 四路
实证落成**实施路拿到即可开工**的规格书：注入点精确落点、模型/机器/场景三层改动清单（函数
级锚点+工作量）、三场景注入序列与断言锚、b1-b4 机器可读断言形式、风险与开放项、分步工作量。
**本路是设计文档，不是实施**：未改 tools/ 任何代码、未跑任何改动场景；所有规格以已合并的
四路静态实证为据（出处随行标注），未钉死的值一律标「运行观测项」，不臆造。

证据强度沿用各源文档口径（[静态]=指令锚定反汇编、[镜像]=公开资源字节、[SDK]=参考 SDK、
[文档]=已合并 docs/HANDOFF 条目、[推断]=上述链推出）；本文新增的工程决策（注入点、开关
层属、断言键名）标 **[设计]**——实施路落地时按运行观测复核，发现不符即改蓝图再动手。

## 0. 前提与锚点速查（全表出处：audio-input-paths §2.1/§2.2/§6）

| 事实 | 值/锚点 | 出处 |
|---|---|---|
| 音频输入 DMA | JL_AUDIO ADC 组 0xFE0538..0xFE0550 单 DMA 多路复用，帧=ch×(2\|4)B 交织写 [ADC_ADR,+ADC_LEN) | AE §0/§2.1 [静态+SDK] |
| ADC 寄存器七字 | CON 0xFE0538（bit7 PND/bit6 清+重启/bit5 数字使能/bit4 使能门/bits0-3 SR 码）、ADR 0xFE053C、LEN 0xFE0540、PNS 0xFE0544、HWP 0xFE0548、SRP 0xFE054C、SRN 0xFE0550 | AE §2.1 [静态+SDK] |
| 唯一音频 DMA 中断 | IRQ68（prio2，注册 0x602ec28；向量槽 0x19FE00+68×4=0x19FF10），handler=分发器 0x602f406（真实入口 push {psr,rets,reti}+r4-r0；RTI 收尾 0x602f452） | AE §1.2/§2.2/§6 [静态] |
| 分发器序 | 读 [0xFE0500] DAC 侧（0x602f412）→ 读 [0xFE0538]（0x602f42e）→ bit7 判（0x602f430）→ \|=0x40 清（0x602f434）→ bit5+bit4 门（0x602f440）→ call 0x6072628 | AE §2.2/§2.3 [静态] |
| audio_adc_irq_handler | 0x6072628：data=ADR+SRP×ch×ssize（0x6072656..0x60726e6）→ 遍历 output-handler 链（hdl+92）→ 尾部 SRN=PNS、PNS 重装、CON\|=0x40 | AE §2.2 [静态+SDK] |
| set_buffer 五写 | 0x606dee4..0x606df38（ADR/LEN/PNS/HWP=0/SRP；bit4 查重读 CON） | AE §2.1/§6 [静态] |
| 共享 audio_adc_hdl | QCY+4596=0x1277B4（AP 勘误后基址；+78 通道使能位、+79[ch] 每通道引用计数、+92 output-handler 链头=0x127810、hdl 建立者 audio_adc_init 0x60719a6..ee、guard b[QCY+4673]） | AE §1.3/§6 + AP 勘误 [静态] |
| 当前条件前沿 | **3,137,573 步停 0x60719c0，unmapped_or_readonly_write 写 0xfe0538=0**——世界停在 boot ADC initcall（0x602ea68→audio_adc_init）内部、ADC 寄存器族第一写（路 AK 白名单明确留下的条件族首位） | frontier_registry.FRONTIERS + HANDOFF [文档] |
| 三场景开链锚 | 辅听=mic_effect 图 0x9c2d/源节点 0xD06D（plug 表 0x60a917c 行 2：create 0x6032924/ioctl 0x603293e/close 0x6032d74；START cmd 0x40001 开采集）；通话=0x60200b6(flags)（关 0x601e436）；ANC=audio_anc_run 0x601c8de（停-重配-启 0x606e9e8(0)→0x606ebe0→0x606e9e8(1)，系数重读 0x606e7c2） | AR §1.8③/AP §1.7③/AL §2.2b [静态] |
| 三 blob | esco_adc 40B @stream.bin 0x1BFD（en_map=0x0B）；MicEff 40B @0x1DA4（en_map=0x1，增益档 0x0a）；ADC13 共享 esco blob；名字表 53×21B @0x3A8 | AP §1.7①/AR §1.8①② [镜像+静态] |
| 三方通道占用 | ch0=A_MIC0 三方共用（ANC RFF/RFB+通话组0+辅听）；ch1=A_MIC1 ANC LFF+通话组1；ch2=A_MIC2 ANC LFB 专用（**已知 en_map=0x0B 不含 bit2——见 §5-R4 张力**）；ch3=A_MIC3 esco 专用 | AR §1.8④ [静态] |
| output handler | 辅听 0x6032e80（前 10 帧丢弃门 b[priv+27]++≤9；模式 1 时 0x602f5fa 门+0x6032da6 解交织）；通话 aec [obj+40]=0x6031D5A；ANC=共享 hdl（消费路径两候选，§5-R5） | AR §1.8③/AP §1.7③/AE §2.2 [静态] |
| S 路直调先例 | post_stop(machine,hardware,cpu) 回调+key_chain_direct_call 受控步进（哨兵 0x100000、保存/恢复 pc/sp/rets/flags/R0-R15、不调 hardware.advance） | peripheral-input-simulation §7 [文档+实测] |
| Timer0 交付先例 | Timer0.pending+IRQ4 合格 → advance() 抛 IrqDeliveryRequest(core,irq=4) → probe_boot.dispatch_delivery → machine.deliver_timer0_irq（向量槽 0x19fe00+4×4、RETI=被中断 PC、active_context、in-flight 防重入）→ 真实 handler W1C \|=0x4000 清 pending → return_timer0_irq（RTI 尾锚） | HANDOFF 路 BE + br28_machine [文档] |

## 1. 注入点选择：方案 (a) 的精确落点 [设计]

d1-acceptance §6.2 已定方案 (a)（DMA 缓冲预填+PND/IRQ68 交付），AE §3 的三选一理由不再
重复。本节回答剩下的一个问题：**注入发生在哪条世界线上**——直调开链（S 路先例）还是等
主前沿自然到达。结论先行：

> **三层分解：boot 级打开走「建模放行+前沿自然推进」，场景级打开走「停点后直调」（S 路
> 先例），数据注入走「模型完成事件+真实 IRQ68 交付」（Timer0 先例）。不采用「等主前沿
> 自然到达场景级打开」。**

### 1.1 两路径取舍

| 维度 | 路径 P1：停点后直调开链（推荐） | 路径 P2：等主前沿自然到达开链 |
|---|---|---|
| 到达成本 | 0——当前世界已停在三场景共用的 boot ADC initcall 门口（0x60719c0 写 0xFE0538）；场景开链入口（0x60200b6/0x603293e/0x601c8de）全部是函数入口，直调即真实执行 | 需先过 D1 全链：boot 完成→S19 消息循环→App/BT 事件（esco 开链由 BT esco 事件 0x60212bc 驱动、辅听由 App/框架激活、ANC 由按键/BT 命令 post "anc" 任务）——每一步都是独立的外部输入注入问题，且 D1 本身未过线 |
| 覆盖面 | 打开链自开链函数起到 pipeline/硬件寄存器的全部固件码；上游「谁触发开链」的事件面不在覆盖内（诚实标注，同 S 路 §7.4 的 S19 分发边界） | 理论上连触发事件面一起覆盖 |
| 先例 | S/AJ 键链（post_stop 直调）、CK/CP probe_pi32v2 场景（直调 0x6074938/0x6075e22） | 无 |
| 结论 | **采纳**：与 D1 解耦、与既有机制零新发明、开链以下全部真实代码——恰是 b1「打开链=场景前置」要的面 | **不采纳为 D2-b 前置**；其价值归 D1 过线后的自然回归场景（P4 回归位，§6 第 6 步） |

P1 内部还有一个次级选择：**boot 级打开（audio_adc_init 完成+IRQ68 注册+anc_hdl 建立）怎么
拿到**。两个子路径：

- **P1-a（推荐）：模型放行+前沿自然推进**。把 JL_AUDIO ADC 族（0xFE0538..0xFE0550）与
  ADA/ADDA 模拟族（0xFE0A00..0xFE0A4C）建成 opt-in 写记录/读回投影（§2.1/§2.2），世界自然
  走完 boot ADC initcall 的剩余段——IRQ68 注册（0x602ec28 真实 request_irq 写向量槽+ICFG）、
  anc_hdl=zalloc(572)@0x602eeb2→[QCY+500]、mic_management 0x601c82e 全部真实执行，停在下
  一个未建模边界（在哪是运行观测项）。收益：①顺带回答 AL/AP 留的动态复核项
  （mic_type b[anc_hdl+45..48]={1,2,0,0}、blob 拷贝区 b[QCY+3460..3507] 形状、b[QCY+4673]=1，
  audio-input-paths §5.6/§5.7）；②场景的 world_prep 面最小化。
- **P1-b（回退）：停在当前 3,137,573 前沿，场景内直调补齐**——直调 0x60719a6
  （audio_adc_init，guard b[QCY+4673] 未置位则真实重跑）+ 直调 request_irq 0x6065d86 以
  0x602ec1c..0x602ec28 的真实实参形状注册 IRQ68（实参表实施第一步反汇编钉死）。适用：若
  P1-a 推进后新前沿停在场景用不到的远处，或前沿重钉成本高时的调试捷径。

**场景停点约定 [设计]**：三场景均在「P1-a 推进后的新条件前沿」post_stop 直调（世界状态
先读后补，缺什么直调补什么，全部记进报告 world_prep 键）；若实施时 P1-a 的新前沿意外
停在 initcall 中段且补齐面大于 P1-b，允许整场景退回 3,137,573 旧前沿+P1-b 补齐（显式记录
选择）。

### 1.2 数据注入的精确时序（方案 (a) 落地约定，承接 d1-acceptance §6.2）

每个注入块 k 的完整时序（模型侧两个合成件之上全真实代码）：

```
①场景调 model.produce_block(k)：按固件已配置的 ADR/LEN/PNS/HWP 计算当前窗口
   [ADR+HWP .. ]（帧单位换算按 §2.1 语义），把 block k 的交织样本写进该窗口
   ——「每次完成事件前填当前窗口，不一次性预填全部内存」（d1-acceptance 原约定）
②模型推进 HWP 过块边界、置 ADC_CON bit7 pending
③advance() 扫描（Timer0 同位）：pending 且 IRQ68 合格（真实 ICFG nibble+IPMASK，
   IRQController.eligible(68)）且非 in-flight → 抛 IrqDeliveryRequest(core=0, irq=68)
④probe_boot.dispatch_delivery → machine.deliver_audio_dma_irq（§2.3）→ 向量槽
   0x19FF10 读回真实 handler 0x602f406 → 分发器→0x6072628→output-handler 链全真实执行
⑤handler 尾部 CON|=0x40（模型 W1C：清 bit7、解除 in-flight）→ RTI 回被中断上下文
⑥场景断言：读回 SRN/PNS/SRP/HWP+消费侧观测点（§3 各场景）
```

「DMA 完成行为投影下通过」标注：②③的完成时序是投影（DMA 总线行为、缓存一致性不建模），
b2/b4 的一切通过结论带此前缀（d1-acceptance §6.2 措辞承接）。

## 2. 模型改动清单（函数级锚点+工作量）

### 2.1 br28_model.py：JL_AUDIO ADC DMA 模型（新类，conditional 层）

- **加什么**：新类 `JlAudioAdcDma`（建议放 JLAudioDacInitWriteRecords 类旁，行 ~2200）：
  - 状态：七字写记录（values/writes/cores，同 JLAudioDacInitWriteRecords.stores 形状）；
    `pending`（bit7）、`delivery_in_flight`（Timer0 同款防重入簿记）、`hwp`（模型维护的硬件
    写指针，软件写 0 复位——0x606df2e [静态]）、`produced_blocks` 计数。
  - `handles/read/write`：写=记录；读回=最后写值合成 pending 位（CON：`last |
    (0x80 if pending)`；其余六字=最后写值；无写时显式合成初始 0 并计数，路 AM 先例）。
    CON 的 RMW（SR 写 0x606dee0、\|=0x40 清 0x602f434/handler 尾、\|=0x800 ANC）走通用
    MMIO 读侧=投影值。**\|=0x40 = W1C 语义：清 bit7 + 清 delivery_in_flight**（分发器与
    handler 尾两处都做，真实代码自己清两次也无害——第二次 RMW 于已清值上平凡）。
  - `produce_block(samples_bytes)`（场景面 API）：①②两步。样本由场景按 §3 数据公式生成
    传入；窗口/换算由模型按记录的 ADR/LEN/PNS/HWP 自算——**场景不手写地址**，防止场景
    与固件配置漂移。
  - `advance()` 扫描块（BR28Model.advance 内 Timer0 块后，行 ~4766 附近）：pending 且
    eligible(68) 且非 in-flight → `delivery_in_flight=True; raise IrqDeliveryRequest(core=0,
    irq=68)`。核选择 [设计]：IRQ68 注册于 CPU0 语境（boot initcall 在 CPU0），交付目标
    core=0；若运行观测显示固件把 IRQ68 配到 CPU1，按真实 ICFG 改。
  - `report()`：写记录+pending/in_flight 现值+produced_blocks+假设叙事（DMA 完成投影）。
- **接线**：BR28Model.__init__ 加参 `jl_audio_adc_dma=False`；handles/read/write 路由；
  report 段。**注意**：0xFE0538 当前是「条件族首位」严格停——本开关开启即推进共享条件前沿
  （见 §2.6 前沿重钉）。
- **工作量**：0.5-1 天（镜像 Fd31AdcConRegisters+Timer0，无新机制）。

### 2.2 br28_model.py：ADA/ADDA 模拟族与 JL_ASS 扩展（新类，conditional 层）

- **加什么**：`JlAddaWriteRecords`（0xFE0A00 DAA_CON0、0xFE0A0C、ADA_CON0..9
  0xFE0A20..0xFE0A48、ADDA_CON0/1 0xFE0A4C/0xFE0A40 邻域——精确白名单按 AG §3.3(a) 的
  esco 块站点表 ≈25 个 SFR 站点圈定）：整字写记录+位 RMW 记录+读回=最后写值（无写合成
  0）。覆盖面证据：esco 开链内 0x606de92 写 ADA_CON8(0xFE0A40)、0x606c3aa 写
  ADA_CON4..7(0xFE0A30/34/38/3C) bit0、0x606b4d0 ADA 上电、JL_ASS [0xfe0900]|=0x200、
  偏置电阻 [0xfe0924] insert RMW（JL_ASS+0x24，SDK 无名）[静态，AG §3.3(a)]。
- **JL_ASS MIC_CTL0/1（0xFE0904/0xFE0908）**：AK 白名单只有 CLK_CON 0xfe0900 三字——
  **不动 JLAudioDacInitWriteRecords.ADDRESSES**（改它动 legacy 报告的 whitelist 叙事），
  新开独立小类 `JlAssMicCtlRegisters`（同款三字先例）。消费者：ANC mux 0x606c8aa
  （adc{i}_sel 位段 p:15/21/12/18）[静态，AL §1.6④]。
- **GPADC 0x20000 偏置电压**：若 P1-a 推进撞上偏置建立轮询（0x602edc4..0x602ede8，有界
  ≤19 次 [静态，AG §4.2#2]），按 BY 具名值先例给 0x20000 通道加具名 fixture ≥1500
  （`fd31_adc_res_named_values` dict 扩一行——场景自己的 dict，BY legacy dict 不动，S 路
  KEY_CHAIN_RES_NAMED_VALUES 同款做法）。
- **工作量**：0.5 天（三件全是既有容器模板复刻）。

### 2.3 br28_machine.py + probe_boot.py：IRQ68 向量交付机制（Timer0 镜像）

- **deliver_audio_dma_irq(core)**（镜像 deliver_timer0_irq 行 532-586）：守卫三件
  （opt-in 开关 audio_irq_entry、指令边界、在途 handler 拒绝嵌套）→ 向量槽
  0x19fe00+68×4=0x19ff10 读 handler（真实 request_irq 已写 0x602f406；若未注册则
  Blocked('vector_slot_uninitialized') 诚实停）→ RETI=被中断 PC、irq_psr=低 4 位
  flags、active_context='audio_dma_irq_handler'、事件记录 `audio_dma_irq_entry`
  （delivery_clears_pending=False，pending 由真实 \|=0x40 清）。
- **return_audio_dma_irq(cpu)**（镜像 return_timer0_irq）：RTI 尾锚（§2.4 帧锚表）+
  active_context 清零 + 事件 `audio_dma_irq_rti`。
- **machine 选项三件**：`audio_irq_entry` / `audio_irq_entry_banks` / `audio_irq_rti`
  （镜像 timer0 三件，banks 为显式银行假设）。
- **probe_boot.dispatch_delivery**（行 1777-1821）：加 `elif request.details['irq']==68:`
  分支 → `machine.deliver_audio_dma_irq(...)`（与 irq==3/4 分支同形，含在途 handler 挂起
  语义——挂起时清模型侧 delivery_in_flight 让 RTI 后重新请求）。
- **工作量**：0.5 天（逐行镜像，无新语义）。

### 2.4 帧锚与 RTI 尾锚（probe_irq_entry.py 常量，实施第一步反汇编钉死）

```python
# 分发器 0x602f406 真实中断入口 = push {psr,rets,reti}+r4-r0（AE §2.2）:
# 特殊帧=push.srl（12B {reti,rets,psr}，TIMER0_IRQ_FRAMES 同形），r4-r0 走通用
# 抽象栈运输;pop 锚与 RTI 尾按 §6 锚点表 0x602f406..0x602f452 区间反汇编钉死。
AUDIO_IRQ_FRAMES={0x602f406:'frame12_push', 0x______:'frame12_pop'}   # pop PC 待钉
AUDIO_IRQ_RTI_TAIL=0x602f452                                        # RTI 收尾(AE §6)
```

经 diagnostics `audio_irq_frames` 并入 probe_boot 行 802-806 的 frames 合并（timer0_frames
同款三行）。**工作量**：反汇编 0.5 天（含 §2.5 的 ioctl/audio_anc_run 实参表一起钉）。

### 2.5 probe_irq_entry.py：场景骨架（三场景一个入口，变体参数选择）

- **irq_case 新参数**：`audio_dma_scene=None`（值开关：'mic_effect'|'esco'|'anc'——同
  key_chain_scan 的 post_stop 单回调模式，三变体互斥 require）。wiring 守卫：
  `require(jl_audio_adc_dma and audio_irq68_delivery ...)`。
- **场景函数 `make_audio_dma_post_stop(app, scene)`**（镜像 make_key_chain_post_stop）：
  1. world_prep：读 guard b[QCY+4673]/向量槽 0x19FF10/[QCY+500]，缺则直调补齐（§1.1 P1-b
     清单），全部记录；
  2. 开链直调（§3 各场景序列）；
  3. 注入循环：per block 调 `hardware.jl_audio_adc.produce_block(...)` → 受控步进循环
     （key_chain_direct_call 扩展：**捕获 IrqDeliveryRequest 转发 dispatch_delivery 等价
     逻辑**——直调段本身仍不调 hardware.advance，pending 的推进由 produce_block 显式触发，
     每块一次交付，交付后继续步进到 RTI 返回被中断停点）；
  4. 观测采集（§4 键名）返回 dict。
- **直调循环的交付扩展 [设计]**：key_chain_direct_call 现形状（哨兵返回、无中断）够用——
  IRQ68 交付不是异步打断，是场景显式 produce_block 后的同步事件；受控循环里在
  `machine.after_step` 后调一次 `hardware.advance(cpu.icfg)` 捕获 IrqDeliveryRequest 即可
  （比 S 路多的唯一一步；「直调段中断冻结」假设改述为「除场景显式产生的 IRQ68 外无中断」，
  Timer0/core-tick 的 pending 冻结语义不变）。
- **main() 场景行**：三行 `irq_case(..., 'audio_dma_injection_mic_effect'/'_esco'/'_anc',
  audio_dma_scene='mic_effect'/...)`，条件栈全开（key_chain_scan_direct 行 4432-4458 同款）。
- **工作量**：骨架+单场景 1-1.5 天；另两场景各 +0.5-1 天（ANC 因新模型族 +1 天内）。

### 2.6 frontier_registry.py 条目草案（照 registry checklist 四步）

```python
# 1) probe_irq_entry.irq_case 参数: jl_audio_adc_dma=False, audio_irq68_delivery=False,
#    ada_analog_writes=False, jl_anc_writes=False, audio_dma_scene=None (probe_only 值开关)
# 2) FEATURES 加条:
Feature('jl_audio_adc_dma', 'conditional', ('audio_dac_init_writes',), False, None,
        'JL_AUDIO ADC 族(0xFE0538..50)写记录+读回+DMA 完成投影(bit7 pending+IRQ68 交付请求)'),
Feature('ada_analog_writes', 'conditional', ('jl_audio_adc_dma',), False, None,
        'ADA/ADDA 模拟族(0xFE0A00..0xFE0A4C)写记录+读回=最后写值(esco 开链条件族,AG §3.3a)'),
Feature('audio_irq68_delivery', 'conditional', ('jl_audio_adc_dma',), False, None,
        'IRQ68 向量交付机制(进入投影+一次性 RTI,Timer0 先例镜像)'),
Feature('jl_anc_writes', 'conditional', ('ada_analog_writes',), False, None,
        'JL_ANC 族(0xFE0400)+JL_ASS MIC_CTL0/1 写记录(ANC 重配链条件族,AG §3.3b)'),
Feature('audio_dma_scene', 'probe_only', ('jl_audio_adc_dma', 'audio_irq68_delivery',
                                          'ada_analog_writes'), True, None,
        '停点后音频 DMA 注入场景(变体 mic_effect/esco/anc,D2-b b1-b4 断言,d1-acceptance §6.2)'),
# 3) jl_audio_adc_dma 开启即推进条件前沿: FRONTIERS['p11_heap_experimental'] 按新停点重钉
#    (新停点=运行观测项,首次开启跑批后钉;probe require/analyze freshness/test 期望自动跟随)
# 4) HANDOFF 前沿表+「已实现」条目人工更新
```

层属说明 [设计]：jl_anc_writes 先按 conditional 草案（共享栈若自然到达 ANC 图门 0x601bd3a
即需要它；若推进后新前沿在到达前停住，可降 probe_only——registry validate 的依赖闭包两向
都过）。三个共享栈新开关全部默认 False，legacy 报告不变；analyze/test 期望自动跟随 registry。

### 2.7 check_local 验证面

文档路本行 trivially 绿；实施路每步的验证=check() 断言（§4）+legacy 报告逐字节不变
（key_chain 两场景+全部既有场景）+`check_local.py --units`（registry validate 与 AST 对账
单测自动覆盖新参数/新条目一致性）。

## 3. 三场景注入序列规格

通用约定：数据公式（codex 数据设计落地 [设计]，16-bit 形；32-bit 位宽时低位字段加宽，
位宽本身是运行观测项）：

```
sample(ch, k, n) = ((ch+1)<<8) | ((k&0xF)<<4) | (n&0xF)
  ch=通道号(0..3)  k=块号  n=块内样本序(mod 16)
  低幅值: ≤0x3FF(16bit 有符号 ~3% FS);通道可区分: 高字节 01/02/03;
  随块变化: bits7-4;样本序可追踪: bits3-0(检「旧帧重复消费」的锚)
```

交付块数 [设计]：N = max(12, 2×⌈环形总长/PNS⌋+2)——12 保住辅听前 10 帧丢弃门后仍有消费
样本；两整圈保 b4。块参数（PNS/LEN/ADR）全部从开链后的寄存器记录读回，不预设。

### 3.1 场景 A：通话（esco）——**建议首场景**（开链最短：单次直调）

| 步 | 内容 | 锚点/期望 | 断言类 |
|---|---|---|---|
| A0 | world_prep（§2.5.1） | hdl guard=1、向量槽 0x19FF10=0x602f406、IRQ68 ICFG nibble 有效 | 前置 |
| A1 | 直调 `0x60200b6(flags)` | flags 取值 [设计]：**首选 0x5**（bit0+bit2→slot{1,0}→组{0,1}=ch0+ch1，与 ANC 共用的两路——三方占用表的 ch0 共用在断言里可见）；候选 0x3（组{1,2}）。**flags 各通话 case 运行值未钉死（AP §1.7③ 残余），两值都合法，取值显式记录为场景 fixture** | b1 |
| A2 | 开链真实执行链 | 0x6014162 解析 "esco_adc"→blob 拷 QCY+3460；zalloc(76)→[QCY+620]；逐 slot 0x60142be→0x606b4d0(hdl+79++、ADA_CON8/4-7 写)→0x606de92；每通道 512B [obj+60+4k]；大缓冲 ch×0x600 [obj+72]；SR=16000 写 0x606dee0（RMW 读 CON=投影值）；set_buffer 0x606dee4 **五写**；挂 handler 0x606df3a→[obj+40]=0x6031D5A 进 hdl+92 链；0x606df66 启动 | b1 |
| A3 | 读回五寄存器+hdl 面 | ADR/LEN/PNS/HWP=0/SRP 记录非空；CON bit5+bit4 置位；hdl+78 含组位、hdl+79[ch]≥1；帧长公式参数 ch=位计数（期望 2）、ssize 按位宽（2\|4） | b1 |
| A4 | 注入 N 块（2ch 交织序 [c0,c1,c0,c1,…]，公式 sample(0,k,n)/sample(1,k,n)） | §1.2 时序；每块记录窗口/首 8 字节/交付/handler 尾部 SRN=PNS 重装 | b2 |
| A5 | 消费观测 | 0x6072628 的 data=ADR+SRP×2×ssize 落在环形内；output-handler 链逐个回调：**0x6031D5A 调用次数=N**，入口 (priv,data,irq_pns×ssize) 的 data 指针处读回字节=注入交织序（DSP 前精确样本）；aec 对象内部读指针/处理计数前进（字段偏移实施时从 0x6031D5A 反汇编钉，观测不预设） | b3 |
| A6 | 两整圈+负例 | HWP 绕 [ADR,+LEN) 两圈；块号字段 k 单调（旧帧重复消费即 FAIL）；链上无其他 handler 被调（未启用消费者不被调） | b4 |
| 边界 | aec 下游（编码/回声参考信号）不在本场景——「参考信号来源另行说明」照 d1-acceptance 表；b3 终点=帧抵达 aec 前端入口+其簿记前进 | 诚实边界 | — |

### 3.2 场景 B：辅听（mic_effect）

| 步 | 内容 | 锚点/期望 | 断言类 |
|---|---|---|---|
| B0 | world_prep 同 A0 | 同 | 前置 |
| B1 | 开链：直调 `0x6074938(0x9c2d, 0xD06D)`（CP d2c_effect_pipeline 先例换 (scene,plug) 参）→ 管道建出（源节点 variant 3 唯一 ADC 节点，AR §1.8③ 图勘误）→ **直调源节点 ioctl `0x603293e(node, 0x40001, …)`**（=jlstream_start 0x6074ffe 内部对流激活发的 START 命令；绕过的 jlstream_start 外围状态面显式标注）。ioctl 实参表实施第一步钉 | plug 表 0x60a917c 行 2；START 开链=0x6032ac8..0x6032d4a：nodecfg（zalloc 48 或模式 1 静态 QCY+3452——**模式字节运行值观测**）→0x6075686 取名 "MicEff"+blob（event 18 主路径 vs 0x6032b40 兜底——**注册者观测**）→en_map=0x1 只开 ch0→0x606b4d0/0x606de92(档 0x0a)/0x606c3aa→SR 0x606dee0（三态观测）→set_buffer(buf,**512**,**2**)→挂 [priv+76]=0x6032e80→0x606df66 | b1 |
| B2 | 读回 | 五写记录（buf_num=2 与 esco 的 3 不同——布局差异本身就是断言点）；hdl+78 bit0、hdl+79[0]≥1；SR 码记录（回答三态）；nodecfg b[nodecfg+0]=1、h[nodecfg+8]=0x1（AR §5.7 动态复核接口） | b1 |
| B3 | 注入 N 块（**1ch**，公式 sample(0,k,n)；N≥12 因前 10 帧丢弃门 b[priv+27]++≤9） | §1.2 时序 | b2 |
| B4 | 消费观测 | 0x6032e80 调用次数=N；前 10 次入口即返（门实测）；第 11 块起：0x6074080 帧分配→（模式 1 时 0x6032da6 解交织——布局观测）→memcpy 进 frame+32 的字节=注入序（DSP 前精确样本）；投递节点后的下游提交面：jlstream 帧/水位（cmd5 时间戳族，CI/CK 钉的形状）——具体提交 PC 实施时按 coverage 钉，断言以「帧对象+32 字节+水位/时间戳字段变化」为观察面 | b3 |
| B5 | 两整圈+负例 | 同 A6（单通道下 k 单调即充分） | b4 |
| 边界 | 辅听检测/算法结果不在验收（「有足够证据时再验收检测结果」照 d1-acceptance 表） | 诚实边界 | — |

### 3.3 场景 C：ANC（配 jl_anc_writes 模型族）

| 步 | 内容 | 锚点/期望 | 断言类 |
|---|---|---|---|
| C0 | world_prep 扩展 | [QCY+500] anc_hdl 非零（P1-a 下 boot initcall 已建；否则直调 0x602eeb2 段不可行——anc_hdl 建立在 initcall 内，回退直调整段 0x602ea68） | 前置 |
| C1 | **co-opener**：先直调 `0x60200b6(0x5)` 开 ch0+ch1 | ANC 不独立开 ADA 数字通道（AL §1.6⑤：adc_ch 0x602f258 只声明搭便车，hdl+79 计数只由 adc_mic_open 增）——ANC 场景的通道集=运行时读回 hdl+78（**不臆造**；已知形状下=ch0+ch1，ch2 无开启者——§5-R4） | 前置 |
| C2 | 直调 `audio_anc_run 0x601c8de(mode=on, …)`（实参两形记录 AL §2.2b/§1.7③，实施钉死后统一） | 链内真实执行：mic_management 0x601c82e（mic_type [1,2,0,0]→mic_param en/type/mult_flag）→PGA 0x606ee98（ADA_CON8 insert+ADC_CON\|=0x800 RMW）→停-重配-启 0x606e9e8(0)→0x606ebe0（request_irq(67) 真实、JL_ASS CLK_CON 串、MIC_CTL1 重 mux 0x606c8aa(param+13)、JL_ANC 滤波配置 0x606cc36/0x606ce02/0x606d9c0、64KB DMA 缓冲 zalloc [param+64]）→0x606e9e8(1)→系数重读 0x606e7c2（flash/res/3mic_coeff.bin 公开资源，CRC 过） | b1 |
| C3 | 读回 | JL_ANC/ADA/JL_ASS 写记录非空；MIC_CTL1 位段=mic_type 映射（adc0_sel=1/adc1_sel=2/adc2_sel=0/adc3_sel=0 的 insert 形状）；IRQ67 向量槽有效；hdl 面（C1 读回值） | b1 |
| C4 | 注入 N 块（通道集=hdl+78 实读位，期望 {0,1}；交织序按实读通道号升序，公式按 ch） | §1.2 时序 | b2 |
| C5 | 消费观测——**ANC 输入边界两候选，运行观测裁决**：①共享 hdl+92 链上出现 ANC 侧 handler（0x6072628 链表遍历记录逐 handler 记账）；②JL_ANC 块自有 DMA（64KB buf/[param+64] 或 0xFE0400 域 DMA 地址寄存器）指向共享环形或自采。断言形式：记录两候选的实际状态，**抵达「实开通道对应的已确认输入边界」即过**（最小测试表原文），未抵达的候选如实记 NOT_RUN | b3 |
| C6 | 模式切换变体（配置切换场景复用此链，§3.4）+两整圈 | 同 A6 | b4 |
| 边界 | **JL_ANC 滤波是硬件块——软件只配置寄存器；本场景证明「数据抵达 ANC 输入边界+配置链真实执行」，不宣称降噪效果/滤波正确**（d1-acceptance §6.2 codex 原话的场景适配：「若 ANC 某段由硬件处理…不能借软件 handler 被调用宣称音频闭环」）；IRQ67 handler 0x6073d7e 的服务是附加观测非断言 | 诚实边界 | — |

### 3.4 第四场景（配置切换）规格简表

选 ANC on→off→trans 重配为代表（d1-acceptance 表「任一实际存在的重配」）：

1. 在场景 C 世界上：注入数块（布局 L1）→ 直调 anc_next 0x6018b60（或直接 os_taskq_post
   "anc" msg 171——post 0x6064d72 真实 API）→ **但 anc_task 0x602d3a0 是任务体，直调需合成
   任务上下文，回退：直调 audio_anc_run 链本身（C2）**——重配序列
   0x606e9e8(0)→0x606ebe0→0x606e9e8(1) 真实重跑 [设计取舍：跳过 anc_task 分发面，标注]。
2. 重配后继续注入（布局 L2，通道集/帧长按重配后寄存器读回）：断言「旧配置停止、新配置
   生效、无旧帧按新布局解释」——L1/L2 用不同 k 基（块号字段不同），L2 窗口内出现 L1 块号
   字段即 FAIL。
3. esco 关链让位变体（可选）：0x601e436 关→mult 通道位保留（0x606f8f6..f932 剔除逻辑真实
   执行）→hdl+79 计数不归零→辅听/ANC 中断源不断。断言=引用计数轨迹。

## 4. b1-b4 断言的机器可读形式

### 4.1 报告键名草案（场景行 `audio_dma_injection_*` 的 post_stop 观测块）

```python
'audio_dma_injection': {
  'scene': 'esco',                              # 场景名
  'world_prep': {'hdl_guard': 1, 'irq68_vector': {'slot':'0x19ff10','handler':'0x602f406',
                  'icfg_nibble':'<hex>'}, 'anc_hdl_slot': '…', 'completions':[ … ]},
  'open_chain': {'direct_calls': [{'pc':'0x60200b6','args':[hex(flags)],'steps':N}],
                  'set_buffer_writes': {'0xfe053c':'0x…','0xfe0540':'0x…','0xfe0544':'0x…',
                                        '0xfe0548':0,'0xfe054c':0},
                  'con_gate': {'bit5':True,'bit4':True,'sr_code':'…'},
                  'hdl': {'enable_bits':'0x…','refcounts':{'0':1,'1':1}},
                  'output_handlers': [{'priv':'0x…','handler':'0x6031d5a'}],
                  'en_map_readback':'0x…', 'notes': { … 场景特有观测(SR 三态/event18/模式字节) }},
  'blocks': [ {'index':k,'window':['0x…','0x…'],'first_bytes':'…','formula_tag':f'c{ch}k{k}',
                'hwp_before':…,'hwp_after':…,'pending_set':True,'delivered':True,
                'acked':True,'srn_reload':'0x…'} ],
  'deliveries': [ {'index':k,'vector':'0x19ff10','handler':'0x602f406','reti_from':'0x…',
                    'dispatcher_pcs':['0x602f412','0x602f42e','0x602f430','0x602f434',
                                      '0x602f440','0x6072628'],
                    'consumer_calls':[{'handler':'0x6031d5a','data':'0x…','len':N,
                                       'first_bytes':'…'}]} ],
  'wraparound': {'hwp_wraps':2,'blocks_total':N,'stale_frame_negative':True},
  'b1_layout': {'verdict':'PASS|FAIL|NOT_RUN','evidence':[ …键引用 ]},
  'b2_dma_irq': {'verdict':…,'evidence':[ … ]},
  'b3_consumption': {'verdict':…,'evidence':[ … ]},
  'b4_continuity': {'verdict':…,'evidence':[ … ]},
  'projection': 'DMA-completion projection (d1-acceptance §6.2): bus/cache behavior unmodeled; '
                'b2/b4 verdicts are conditional on the modeled completion timing',
}
```

### 4.2 check() 断言代码骨架（进 probe_irq_entry.check()，key_chain 断言块后）

```python
# ——路 AW/D2-b:音频 DMA 注入场景断言(三变体共用,场景参数化)——
def check_audio_dma_scene(row, scene, *, consumer_handler, min_channels):
    a = row['audio_dma_injection']
    require(a['scene'] == scene, 'scene mismatch')
    # 前置:世界自检(world_prep 必须有 IRQ68 真实注册的证据,不许合成向量)
    v = a['world_prep']['irq68_vector']
    require(v['slot'] == '0x19ff10' and v['handler'] == '0x602f406',
            'IRQ68 must be delivered via the firmware-registered vector slot')
    # b1:五写+使能门+handler 挂链+通道数(按位计数,非预设)
    sb = a['open_chain']['set_buffer_writes']
    require(set(sb) == {'0xfe053c','0xfe0540','0xfe0544','0xfe0548','0xfe054c'}
            and sb['0xfe0548'] == 0 and sb['0xfe054c'] == 0,
            'set_buffer 0x606dee4 must write ADR/LEN/PNS with HWP=0/SRP=0')
    require(a['open_chain']['con_gate'] == {'bit5': True, 'bit4': True},
            'ADC_CON bit5+bit4 enable gate must be set by the real open chain')
    handlers = {h['handler'] for h in a['open_chain']['output_handlers']}
    require(consumer_handler in handlers, f'{consumer_handler} must be on the hdl+92 chain')
    channels = bin(int(a['open_chain']['hdl']['enable_bits'], 16)).count('1')
    require(channels >= min_channels, 'channel count must come from hdl+78 bit count')
    # b2:每块 pending→真实 IRQ68→|=0x40 清→SRN/PNS 重装(逐块四查,无漏失无重复)
    for blk, d in zip(a['blocks'], a['deliveries']):
        require(blk['pending_set'] and blk['delivered'] and blk['acked'],
                f'block {blk["index"]}: pending/delivery/ack must each happen exactly once')
        require(d['handler'] == '0x602f406' and '0x6072628' in d['dispatcher_pcs'],
                'delivery must run the real dispatcher and irq handler bytes')
        require(blk['srn_reload'] == a['open_chain']['set_buffer_writes']['0xfe0544'],
                'handler tail must reload SRN=PNS')
    # b3:消费侧 handler 逐块被调+DSP 前精确样本(交织序逐字节)
    calls = [c for d in a['deliveries'] for c in d['consumer_calls']
             if c['handler'] == consumer_handler]
    require(len(calls) >= (len(a['blocks']) - (10 if scene == 'mic_effect' else 0)),
            'consumer must be called per delivered block past its own gate')
    for c, blk in zip(calls, a['blocks'][10:] if scene == 'mic_effect' else a['blocks']):
        require(c['first_bytes'] == blk['first_bytes'],
                'DSP-前样本必须逐字节等于注入交织序(布局/串路/旧帧检测面)')
    # b4:两整圈+旧帧负例
    require(a['wraparound']['hwp_wraps'] >= 2 and a['wraparound']['stale_frame_negative'],
            'b4 requires two full ring wraparounds with no stale-frame reuse')
    for key in ('b1_layout', 'b2_dma_irq', 'b3_consumption', 'b4_continuity'):
        require(a[key]['verdict'] == 'PASS', f'{key} must PASS (NOT_RUN is not PASS)')

check_audio_dma_scene(rows['audio_dma_injection_esco'], 'esco',
                      consumer_handler='0x6031d5a', min_channels=2)
check_audio_dma_scene(rows['audio_dma_injection_mic_effect'], 'mic_effect',
                      consumer_handler='0x6032e80', min_channels=1)
check_audio_dma_scene(rows['audio_dma_injection_anc'], 'anc',
                      consumer_handler='<C5 裁决的 handler 或输入边界记录键>', min_channels=1)
```

（骨架断言的字面值——SRN 比较、10 帧门、通道数——在实施时按运行观测复核后钉死进单测；
「NOT_RUN 不算 PASS」承接 d1-acceptance §6.4 BLOCKED 语义。）

## 5. 风险与边界：运行观测项清单（哪些被注入场景自然回答、哪些仍开放）

**会被注入场景自然回答的**（每项的观测点=场景报告键）：

| # | 观测项 | 哪个场景/哪个键回答 |
|---|---|---|
| R1 | SR 三态（GET_FMT 0x603297e..96 按模式字节取 16000/全局 h[0x124CB0]/44100；SET_SR 0x20003 可覆盖） | B2 的 sr_code+模式字节记录（AR §1.8⑤(b)） |
| R2 | event 18 注册者（0x60057e6(18,name) 主路径 0x607565c vs 兜底 0x6032b40 哪条跑） | B1 开链 coverage（AR §1.8⑤(c)） |
| R3 | 0x6032da6 解交织布局（仅模式字节==1 时执行） | B4——**条件性**：模式字节≠1 则本场景不回答，维持开放（AR §1.8⑤(d)） |
| R4 | **ANC 通道集张力**：已知 en_map=0x0B（bits 0,1,3）不含 bit2——A_MIC2（LFB 左耳反馈）在已知所有 opener 形状下**无开启者**（esco flags 任一取值都不开组 2、辅听只开 ch0）。两假设：①LFB 由 JL_ANC 块自有 DMA 采（64KB buf 的真实用途）；②存在未钉的 opener 取值。 | C1/C5：hdl+78 实读+C5 两候选裁决——这是 ANC 场景**首个**要回答的问题 |
| R5 | ANC 消费路径（共享 hdl+92 output-handler vs JL_ANC 自有 DMA） | C5 两候选记账 |
| R6 | esco aec flags 运行值（0x3/0x5/高 nibble 哪个真实） | 场景 fixture 显式合成（A1 记录）；**真实 case 值仍开放**（AP §5.5④），场景不冒充回答 |
| R7 | LEN/PNS/HWP/SRP 的窗口换算细节（buf_num=2/3 差异、SRP 是否被 handler 写） | 首场景 A3/A5 的寄存器轨迹+0x6072628 内 SRP 访问记录 |
| R8 | AL/AP 动态复核项（mic_type 运行值、blob 拷贝区形状、b[QCY+4673]） | P1-a 前沿自然推进即回答（audio-input-paths §5.6/§5.7 接口） |
| R9 | ADA/ADDA/JL_ANC/JL_ASS 条件族的实际写序列形状 | 各场景 open_chain 写记录（AG §3.3 表的动态版） |

**仍开放的（注入场景不回答）**：

- DMA 总线行为/缓存一致性/真实完成时序——一切 b2/b4 结论维持「DMA 完成行为投影下通过」；
- 模拟前端/声学效果/ANC 降噪正确性（§3.3 边界，D3 比较面另立）；
- 上游触发面（BT esco 事件/App 激活/按键→anc_task post→分发）——直调开链绕过，归 D1
  过线后的自然回归（§1.1 P4）；
- 真实 ADC 档位/增益/偏置的硅值（fixture 显式合成，synthetic-not-measured 措辞承接）；
- flags 各通话 case 真实取值、模式字节真实值（场景只提供合成 fixture 下的行为证据）；
- ANCIF/系数文件内部分组格式（hearing-aid §5.2 口径不变；3mic_coeff.bin 只做 CRC 过的
  资源读取，不解内容）。

**工程风险**：

- 前沿重钉的连锁：jl_audio_adc_dma 进 conditional 栈即推进共享前沿，新停点未知——若新
  前沿停在场景不需要的深处（如 tone/DAC 域），frontier 重钉+budget 调整是独立一步（registry
  checklist §62-71 已有流程）；**若推进后共享场景预算/时长超限，可把 ada_analog_writes 降
  probe_only 并回退 P1-b**（两向都过 validate 闭包）。
- 直调段的 IRQ68 交付是新组合（S 路受控循环+advance 捕获）：首次实施若撞
  active_context/icfg 未初始化类严格停，按 Timer0 场景当年同类问题对位处理（守卫报错即
  诚实停，不放宽）。
- 0x6072628 的 output-handler 链遍历对「链上有多个 handler」的场景（通话+辅听共开）——
  b3 断言按「注册了谁谁被调」记账（d1-acceptance 三消费者核对原则：帧计数按订阅关系核对，
  不简单要求相等）。

## 6. 工作量分解（实施路拆步与可验证产出）

| 步 | 内容 | 可验证产出 | 量 |
|---|---|---|---|
| 0 | 静态钉死四件：分发器帧锚/RTI 尾（§2.4）、ioctl 0x603293e 与 jlstream_start 实参、audio_anc_run 实参约定、request_irq 0x602ec1c..28 实参表 | 锚点表（本蓝图 §2.4/§3 的空格填上） | 0.5 天 |
| 1 | 模型件：JlAudioAdcDma+ADA 族+（JlAss 扩展）+machine 交付三件+dispatch 分支+registry 条目+前沿重钉跑批 | 新条件前沿复现（check_local --rom 绿）+Timer0 式最小交付冒烟（手工 produce_block→delivery 记录） | 1-1.5 天 |
| 2 | 单场景 esco：骨架+开链+注入循环+b1/b2 | 场景行跑通，b1/b2=PASS（投影措辞） | 1-1.5 天 |
| 3 | 断言机读化：报告键全量+check_audio_dma_scene 落码+b3（esco） | check() 断言进 CI，legacy 报告逐字节不变 | 0.5-1 天 |
| 4 | 场景 mic_effect（B1 开链+10 帧门+SR 三态/event18 观测）与场景 ANC（jl_anc_writes 族+C1 co-opener+C5 两候选） | 两场景 b1-b3=PASS；R1/R2/R4/R5 观测入档 | 1.5-2 天 |
| 5 | b4 连续性（两整圈+旧帧负例，三场景）+配置切换变体（§3.4） | b4=PASS×3+切换场景「无旧帧按新布局解释」 | 1 天 |
| 6 | 收尾：HANDOFF/frontier 表、可选 masked 反事实对照（清 IRQ68 nibble 惰性变体，timer0_masked 先例）、D1 过线后的自然回归挂账 | check_local --units 绿+全量 --rom 绿 | 0.5 天 |

合计 ≈ 6-8 个工作日（不含前沿推进若撞新条件族的追加建模——每族半天级，AG §3.3 表已
预告三族的站点清单）。

## 7. 红线核对（本路自查）

- 未改 tools/ 任何文件、未跑改动场景（本文件为 docs/ 新增，git 状态可查）；
- 规格全部出自已合并四路实证（AE/AL/AP/AR/AG/AK+BE/S/AJ/CK/CP 先例），出处随行；未钉死
  值（flags 真值、模式字节、新前沿停点、SRP 写者、ANC 消费路径、ch2 张力）标「运行观测
  项/条件性」；
- 提交不含 Co-Authored-By、不含私有路径/私有工件名（本地脚本按惯例留本地 tmp 不进库）；
  `check_local.py --units` 绿（文档路 trivially，已跑）。
