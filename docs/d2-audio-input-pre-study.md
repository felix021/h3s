# D2AUD：D2 音频输入面预研——park 世界输入状态 + 注入设计对齐按键先例（路 D2AUD / sim-d2aud，只读预研）

2026-11-17 02:36-03:5x。基线 worktree `ff9fdf5`（分支 sim-d2aud）。用户判定「音频输入验证需要
补上」：按键输入已有 ADC 分压注入→真固件消抖/手势机的全链先例（路 S/AJ），音频输入则
从未有过**数据流验证**（推进前缀只走过「开→配→关」的配置面，§6.2——零采样、零中断
交付、零消费）。本页把音频输入的固件路径钉死并给出注入设计，照
[docs/sim-m1-pre-study.md](sim-m1-pre-study.md) 的 SIMPRE 模式：逐节结论+每个地址/函数给出
处+不发明+未钉项如实标注。

**本页与既有文档的分工**（避免重复劳动，先说清楚）：

| 既有文档 | 覆盖 | 本页关系 |
|---|---|---|
| [docs/audio-input-paths.md](audio-input-paths.md)（路 AE/AL/AP/AR） | 输入硬件面/采集链/消费链的**静态全图**（mic 布局、IRQ68、三消费者开链、三 blob、三方占用表） | §1-§3 汇总引用，不重推 |
| [docs/wlaud-init-map.md](wlaud-init-map.md)（路 AG） | boot 期音频 init 0x602ea68 全图（G6 门/条件族） | §6 引用+运行时复核 |
| [docs/d2b-injection-blueprint.md](d2b-injection-blueprint.md)（路 AW） | 注入实施蓝图（写于前沿 3,137,573 时代） | §4/§5/§7 对齐按键先例复核+按 park 时代更新 |
| [docs/peripheral-input-simulation.md](peripheral-input-simulation.md) §7/§8（路 S/AJ） | 按键「RES 分压→真固件全链」先例 | §4 注入层级对齐的基准 |
| [docs/acceptance-samples/d2b/](acceptance-samples/d2b/README.md)（路 BI 面板） | D2-b 验收记录：**配置面 7 条 PASS**（CFG-01..07，静态实证半边）+**注入面 8 条 NOT_RUN**（INJ-01..06/SW-01/02，等执行载体） | §5 判据对齐面板条目；本路直接闭合 INJ-01/02 的两个等待项（前沿释放已发生、分发器 pop 锚已钉） |

**本路增量**（既有文档没有的）：

1. **[运行·本路] park 停点世界的音频输入状态面全读**（§6）——用只读复现跑
   p11_heap_experimental_config 到 29,601,125 停点，读 RAM/SFR/向量槽，回答「boot 后输入
   开了没有」（蓝图 §5-R8 的动态复核项就此闭合），并带出 **mic_type 运行时勘误**
   （[1,2,0,3] 非 AL 静态的 [1,2,0,0]）与 **hdl+79 语义勘误**；
2. **[静态·本路] IRQ68 分发器帧锚补全**（§4.4）——蓝图 §2.4 留空的 pop/RTI 锚钉死；
3. **[静态·本路] audio_adc_irq_handler 0x6072628 全形状 + 三处布局勘误**（§4.4）——
   入口临界区对 0x12b55c/0x12b5b0（RAM-code）、链表节点布局、尾部三写；
   **hdl+79=单字节活跃通道计数**（非「每通道引用计数数组」）、**output-handler 链表头=
   hdl+0/+4**（非「hdl+92」）、挂链/摘链/遍历三函数互证，含实施墙清单；
4. **注入层级与按键先例的对齐分析**（§4.2）+ 实施切分按「前置已发生/待做」重排（§7）。

**只读纪律**：零 tools/ 改动（worktree git 状态可查）；probe 五文件未动；运行复现用
主仓 tools（reference-cache）+ 一次性脚本 `~/tmp/d2aud/`（不进库）；反汇编复用
`~/tmp/ag-wlaudmap/ag_dis.py`（app.bin sha `18f3d573…`，load base 0x06000100，路 AE 同源）。

证据强度：**[静态·本路]** = 本路反汇编钉死；**[静态]** = AE/AL/AP/AR/AG 既有静态结论
（本页引用）；**[文档]** = docs/HANDOFF 已合并条目；**[运行·本路]** = 本路 park 停点
复现观察（模型行为，derived-not-measured）；**[推断]** = 链路推出；**[未钉]** = 如实标注。

## 0. 一句话结论（六问六答）

1. **输入硬件面**：JL_AUDIO 模拟 ADC 单 DMA 多路复用，最多 4 路模拟 mic（A_MIC0..3），
   样本交织写入 ADC_ADR 指向的环形缓冲（**LEN 是样本数/半字单位非字节数**——set_buffer
   实写 `LEN=uextra(buf_num×len,1,16)`、`PNS=len>>1`，窗口字节长=LEN×ch×ssize，AE §2.1
   「len/2 截断」同义），每 PNS 样本置 ADC_CON(0xFE0538) bit7，IRQ68（全固件唯一音频 DMA
   中断，prio2，handler=分发器 0x602f406）交付；数字 mic 接口（ALNK0/PLNK）零注册零
   **li 直接引用**（变址访问不排除，AE §1.2 原口径），未用。GPADC 慢速通道（VBAT/按键
   分压）与音频 mic 完全分离 [静态，audio-input-paths §0/§1/§2.1]。
2. **采集链**：三个开链函数共享同一 audio_adc_hdl（QCY+4596=0x1277B4）——通话
   0x60200b6(flags)（同步、无 pend，[静态·本路] 入口复核）、辅听 jlstream START
   cmd 0x40001→0x603293e、ANC audio_anc_run 0x601c8de（不自开数字通道，搭便车）；数据搬运
   = IRQ68→分发器 0x602f406→0x6072628：data=ADR+SRP×ch×ssize，遍历 output-handler
   环形链（表头=hdl+0/+4，§4.4 勘误 3）回调 (priv, data, PNS×ssize)，尾部 SRN=PNS/
   PNS 重装/CON|=0x40 [静态，AE §2.2]。
3. **消费链**：ANC（JL_ANC 硬件滤波块 0xFE0400，IRQ67）、通话 aec 前端（handler
   0x6031D5A）、辅听 mic_effect（handler 0x6032e80→jlstream pipeline）。通道占用（按运行时
   mic_type=[1,2,0,3] 勘误后推导，§3 表）：ch0 三方共用（mux A_MIC1/LFF）、ch1..ch3 各有
   ANC 角色+通话组（「ch2 无开启者」的旧说法不成立——flags bit1 开组 2）。
   **M1 写环已覆盖的是 ANC 控制面（cmd-0x17 setter→apply→ctx+33/VM key16），数据面
   （采集启动）从未触达** [文档，SIMB1E]。
4. **注入设计**：推荐**按键先例同构的两层组合**——开链=停点后直调（S 路 key_chain_
   direct_call 形态），数据=寄存器/硬件级注入（DMA 缓冲写+bit7 pending 投影+IRQ68 真实
   交付，Timer0 先例）——即蓝图方案 (a)+P1 直调开链。分发器之下全固件真码，合成件只有
   「样本字节+完成时序」两件，与按键的「RES 档位值」同一深度（§4）。
5. **验收判据草案**：正弦/特征样本注入→五寄存器写记录+handler 链逐块回调+DSP 前样本
   逐字节回读+SRN/PNS 重装轨迹；负例=静音/不置 pending 时零交付零回调（§5）。
6. **与 park 稳态的关系**：park 世界里音频输入**稳态关闭但基础面全部就绪**——IRQ68 已
   注册（向量槽=真分发器 0x602f406）、audio_adc_hdl/anc_hdl/esco_adc blob/大缓冲全部落盘、
   输入侧**写侧**投影容器（ADC_CON/五字/ADA 九字/MIC_CTL 四字）已在条件栈放行；且推进
   前缀里「开→配→关」一个完整生命周期已被证明可执行（set_buffer 五写真实跑过一轮，
   路 JLA）。稳态零通道（hdl+79=0）、零 handler、零采样流。启动路径=**停点后直调开链**
   （S 路先例），不需要 resume 推进；**交付段（IRQ68→分发器→handler）的读侧/SRN/pending
   仍待模型件**（§6.3，五字容器当前读即停）。（§6，[运行·本路]）

## 1. 输入硬件面（预研问题 1）

全部为既有静态结论，本页汇总引用（逐字段出处见 audio-input-paths §1.1/§1.2/§2.1/§2.3）：

- **模拟 mic 前端**（JL_ADDA）：ADA_CON1(0xFE0A24) 每通道 3 使能位段、ADA_CON4..7
  (0xFE0A30..3C) 每通道配置、ADA_CON8(0xFE0A40) 4×5bit PGA 增益、偏置/LDO 在 ADA_CON0/
  ADDA_CON0/1（0xFE0A20/48/4C），上电后轮询 GPADC 通道 0x20000 电压 ≥1500 等偏置建立。
- **数字通道**（JL_AUDIO ADC 组）：ADC_CON(0xFE0538) bits0-3 SR 码/bit5 数字使能/bit4
  使能门/bit7 pending/bit6 清 pending；每通道 3bit 输入源选择在 MIC_CTL1(0xFE0908)，
  mic 时钟 MIC_CTL0(0xFE0904) bit0，JL_ASS CLK_CON(0xFE0900) 时钟使能串。
- **DMA 七字**：ADR 0xFE053C（环形缓冲基址）/LEN 0xFE0540/PNS 0xFE0544（中断周期）/
  HWP 0xFE0548（硬件写指针，软件写 0 复位）/SRP 0xFE054C（软件读指针，帧单位）/
  SRN 0xFE0550（读通知）。**每字段 4 字节槽位**（含声明 __u16 的）。
- **中断**：IRQ68（hwi.h 身份 JL_AUDIO，DAC+ADC 合并），注册点 0x602ec28（boot initcall
  0x602ea68 内），向量槽 0x19FE00+68×4=**0x19FF10**，handler=**0x602f406**（分发器，
  DAC/ADC 双侧 pending-门-分发），prio2。IRQ69/70（数字 mic 接口）零注册。
- **硬件上限**：并发音频输入 ≤4 路模拟 mic；H3S 实际 mic_type 运行时值 **[A_MIC1,A_MIC2,
  A_MIC0,A_MIC3]**（park 复现勘误 AL 静态的 [1,2,0,0]，§6.3——四角色落四路物理输入、
  无共用；「3mic_coeff.bin」的 3mic 语义需按此重审 [未钉]）。

## 2. 采集链（预研问题 2）

**谁启动**（三个开链，全部汇到共享 audio_adc_hdl）[静态，AP §1.7③/AR §1.8③/AL §2.2b]：

| 消费者 | 开链入口 | 直调可跑性 |
|---|---|---|
| 通话 esco/aec | **0x60200b6(flags)**：flags=0x5 → slot{1,0}→组{0,1}（ch0+ch1）；同步（zalloc→配置解析→逐通道 adc_mic_open→set_buffer→挂链→启动→返回 1），**入口无 pend**[静态·本路 0x60200b6..0x6020100 复核：zalloc 0x12e16e + 失败 canary 0x12cc40 + flags&0x10 才走的 0x601f856/0x601dab2] | 可直调（S 路先例形态） |
| 辅听 mic_effect | jlstream_start → 源节点 ioctl **0x603293e(node, 0x40001, …)** START 命令：取名 "MicEff"→40B blob→en_map=0x1 只开 ch0→set_buffer(buf,512,2)→挂 handler 0x6032e80→启动 | 需先 descriptor walk 直调 0x6074938(0x9c2d, 0xD06D) 建节点（d2c_effect_pipeline/hearing-aid L1 先例） |
| ANC | **audio_anc_run 0x601c8de(mode, r1)**：不自开 ADA 数字通道（搭 esco/共享通道便车），链内停-重配-启（0x606e9e8(0)→0x606ebe0→0x606e9e8(1)）+ JL_ANC 硬件块配置 + 系数装载 | 可直调；**实参已指令级钉死 [静态·本路]**：hdl 自取 [QCY+500]，两参=(mode, 指针)，esco 分发器恒以 `(2 或 1, QCY+8164=0x1285A4)` 调（0x6020cae/0x6020cc2 装载窗口）；boot 期只调 0x601c82e mic_management（0x602f2e2），不跑完整 run |

**数据怎么搬运**（IRQ68 中断层）[静态，AE §2.2 + 本路 §4.4 补全]：

```
DMA：帧=channel_num×(2|4)B 交织写 ADR 指向的环形窗（字节长=LEN×ch×ssize，LEN 样本单位），
  硬件推进 HWP，每 PNS 样本置 CON bit7
IRQ68 → 分发器 0x602f406：
  读 [0xFE0500] DAC 侧 bit7 → |=0x40 清 → bit5 门 → call 0x6072470（DAC handler）
  读 [0xFE0538] ADC 侧 bit7 → |=0x40 清 → bit5+bit4 门 → call 0x6072628
0x6072628（audio_adc_irq_handler）[静态·本路 全形状钉死，§4.4]：
  入口：r5 = b[hdl+90]≠0 ? 4 : 2（16/32bit 宽度选择→ssize）；call 0x12b55c（临界区 enter）
  data = ADC_ADR + SRP × ch × ssize，ch = b[hdl+79]（活跃通道计数，§4.4 勘误）
  遍历 output-handler 链（环形，哨兵=hdl）：call [node+12]([node+8], data, len)
  尾部：SRN=PNS、PNS 重装、CON|=0x40、call 0x12b5b0（临界区 exit）
```

**缓冲在哪**：环形缓冲基址=ADC_ADR（0xFE053C），由开链的 set_buffer 0x606dee4 五写
（ADR/LEN/PNS/HWP=0/SRP）配置；缓冲本体=开链时 zalloc 的堆块（esco=通道数×0x600 交织大
缓冲+每通道 512B；mic_effect=256<<1×…，[obj+72]/[priv+80] 或共享槽 [QCY+3504]）。写入者
=硬件 DMA（仿真侧=注入模型）。

## 3. 消费链（预研问题 3）

采到的数据给谁（output-handler 链上谁注册了谁拿帧指针）[静态，AE §1.3+AR §1.8④]：

| 消费者 | handler | 数据去向 |
|---|---|---|
| **ANC**（重点） | 无独立 output handler 候选①：共享 hdl+0/+4 环形链；候选②：JL_ANC 块自有 DMA（64KB buf [param+64]） | JL_ANC 硬件滤波块（0xFE0400..0xFE04C0，IRQ67 prio0 handler 0x6073d7e）；滤波在硬件，软件只配置+取状态 |
| 通话 aec 前端 | [obj+40]=**0x6031D5A** | aec/通话编码参考（下游编码不在注入场景验收内） |
| 辅听 mic_effect | [priv+76]=**0x6032e80**（前 10 帧丢弃门 b[priv+27]++≤9） | jlstream pipeline（0x6074080 帧分配→memcpy 进 frame+32→投递节点→cmd5 时间戳/水位消息进 'jlstream' 队列） |

**ANC 状态面 vs 数据面**：cmd-0x17 布局（[docs/cmd-0x17-0x1d-layout.md](cmd-0x17-0x1d-layout.md)）
钉的三字节状态面（b[0x12666B..D]：模式/场景/通透）与 M1 写环已验证的 apply 链
（0x6025D6A→0x6012BE2/0x600DCEE→ANC ctx [0x1265C0+500]+33/VM key16）都是**配置面**；
数据面=本页 §2 的采集链+JL_ANC 块，从未触达 [文档，SIMB1E：写环只到 setter 直存+apply
落点，audio_anc_run 的停-重配-启链未跑]。

**通道占用表**（**按运行时 mic_type=[1,2,0,3]**（§6.3 勘误）×esco en_map=0x0B×MicEff
en_map=0x1 推导——mux 映射=「通道 i 的模拟输入选 mic_type[i]」（AL §1.6④），mp 值为按
mic_management 算法代入、非实读 [推断]；静态版见 AR §1.8④）：

| ADA 通道 | 模拟输入（MIC_CTL1 adc{i}_sel） | ANC 角色（mp[mic_type[i]]） | 通话（en_map 组） | 辅听 | 裁决 |
|---|---|---|---|---|---|
| ch0 | **A_MIC1**（mic_type[0]=1） | mp[1]，type=LFF | 组 0（aec flags bit2→slot0） | bit0=1 | **三方共用**（mult_flag=1） |
| ch1 | **A_MIC2**（mic_type[1]=2） | mp[2]，type=LFB | 组 1（flags bit0→slot1） | — | ANC+通话（mp[2] mult=0，bit2=0） |
| ch2 | **A_MIC0**（mic_type[2]=0） | mp[0]，type=RFF | 组 2（**flags bit1→slot2——有开启者**，见注） | — | ANC+通话（mult_flag=1） |
| ch3 | **A_MIC3**（mic_type[3]=3，运行时勘误） | mp[3]，type=RFB | 组 3（flags bit3→slot3） | — | ANC+通话（mult_flag=1） |

注：**蓝图 §5-R4「ch2 无开启者」不成立**（本路勘误）——AP §1.7③ 已明写
`flags=0x3 → 组{1,2}`（aec flags bit1 经 0x6020064 重映射 slot2 强制使能，0x6020214..0x6020224
装载窗口），首选 fixture flags=0x5 不开组 2 只是**本场景的取值选择**；「各通话 case 的
真实 flags」仍是运行观测项（蓝图 R6），但「无开启者」的说法就此撤销。

## 4. 注入设计（预研问题 4）

### 4.1 按键先例的深度基准

路 S/AJ 的按键验证（peripheral-input-simulation §7/§8）：停点世界 post_stop 直调
key_chain_direct_call——真 adc_add_sample_ch 注册+真采样事务（setter 0x6011666 写复用域+CON、
PND 轮询投影过、RES fixture 真实读、清 PND，127 步/次）+真扫描回调/去抖/手势/post，
**唯一合成输入=RES 分压档位值**（fixture dict 一行）。音频输入对齐这个深度=「唯一合成
输入=样本字节」。

### 4.2 注入层级选项（可行性/证据/风险）

| 层级 | 内容 | 可行性 | 证据 | 风险 |
|---|---|---|---|---|
| **(a) 寄存器/硬件级**（推荐，与按键同深度） | ①预填交织帧进 ADR 环形窗当前窗口（字节长=LEN×ch×ssize）；②ADC_CON bit7 pending 读回投影；③IRQ68 真实交付（向量槽 0x19FF10 已被固件 request_irq 写入；Timer0 IRQ4 交付先例镜像）；④HWP 推进/单发不推进 | **高**——分发器→0x6072628→output-handler 链→消费者全固件真码；两个合成件都有 SDK 钉死语义+既有投影先例（fd31_adc_con_readback 同族、BE 路 Timer0 交付） | AE §3 推荐+蓝图 §1.2 时序+**本路 §4.4 帧锚/handler 形状补全** | 0x6072628 入口 call 0x12b55c=**os_enter_critical**（cli+IPMASK 提级+嵌套计数，RAM-code 经 app 0x060c4d34 装载映射解码，§4.4——无 pend 无轮询）；PNS/LEN 语义按 set_buffer 实写形状填（样本数单位） |
| (b) 直调采集回调 | 直调 0x6072628（合成 hdl 入参+预填缓冲+三寄存器读投影） | 中——跳过分发器的 pending/清位逻辑与真实中断入口（少验一段）；hdl 是堆对象依赖开链先跑 | AE §3 候选 (b) | 同 (a) 的 0x12b55c 墙；省掉的恰好是「DMA 完成→中断」语义面 |
| (c) 直调源节点 output handler | 合成 (priv, buf, len) 直调 0x6032e80/0x6031D5A | 低——完全绕开 ADC 簿记（SRP/PNS/SRN 不动），需要 pipeline 先建好才有节点对象 | AE §3 候选 (c) | 「输入路径闭环」验收含金量最低；只在前两级被卡死时兜底 |

**推荐 (a)**，理由对齐按键先例：注入面之下（DMA 写+pending 位）只有两个合成件，之上
从分发器到消费者全部真实执行——与按键「RES 档位值之上全真码」同一深度；且 IRQ68 交付
机制有 Timer0/软件 IRQ 两代先例。(b) 保留为 (a) 的调试捷径，(c) 兜底。

### 4.3 开链的注入世界线（与蓝图 §1.1 的对齐）

- **boot 级打开（hdl/IRQ68/anc_hdl/blob）**：蓝图写作前置的 P1-a「建模放行+前沿自然
  推进」**已经发生**——路 AN/AS 放行 ADC reset 三开关后 initcall 尾块全过，前沿已从
  3,137,573 推进到 park 29,601,125 [文档，HANDOFF]。§6 运行时复核其落盘状态。
- **场景级打开**：停点后直调（S 路 post_stop 形态；M1 世界= sim_world_driver.direct_call
  同一机制已服役于 SIMB1E 读/写环）。
- **数据注入**：模型完成事件（produce_block）+IRQ68 真实交付（resume/受控循环的
  IrqDeliveryRequest 分发——sim_world_driver._dispatch_delivery 现有 irq3/4 专用分支+
  software 通用分支，IRQ68 需镜像 Timer0 加专用分支 deliver_audio_dma_irq，蓝图 §2.3）。

### 4.4 [静态·本路] 分发器帧锚与 handler 全形状（蓝图 §2.4 空格补全）

反汇编复现：`python3 ~/tmp/ag-wlaudmap/ag_dis.py 0x602f406 0x602f460`（codec 钉死
pi32v2-isa；常量脚本复算 16647424=0xfe0500、1213160=0x1282e8）。

```
IRQ68 分发器 0x602f406（完整指令级）：
0x602f406  [--sp] = {psr, rets, reti}        ← 特殊帧 push（frame12_push）
0x602f408  [--sp] = {r4-r0}                  ← 寄存器组 push
0x602f40c  r4 = 0xFE0500（JL_AUDIO 基址）
0x602f412  r0 = [r4+0]                       ← 读 DAC_CON
0x602f414  if ((r0 & 128) == 0) goto 0x602f42e   ← DAC bit7 pending 门（无 pending 跳 ADC 侧）
0x602f418  [r4+0] |= 0x40                    ← DAC 清 pending
0x602f41c  r0 = [r4+0]
0x602f41e  if ((r0 & 0x20) != 0) {           ← DAC bit5 使能门
0x602f422    r0 = 0x1282E8                   ← DAC 侧对象指针（立即数）
0x602f428    call 0x6072470 }                ← DAC handler（入口同样 call 0x12b55c 临界区）
0x602f42e  r0 = [r4+56]                      ← 读 ADC_CON 0xFE0538
0x602f430  if ((r0 & 128) == 0) goto 0x602f44a   ← ADC bit7 pending 门
0x602f434  [r4+56] |= 0x40                   ← ADC 清 pending
0x602f438  r0 = [r4+56]
0x602f43a  if ((r0 & 32) == 0) goto 0x602f44a    ← bit5 数字使能门
0x602f43e  r0 = [r4+56]
0x602f440  if ((r0 & 0x10) != 0) {           ← bit4 使能门（三门的最后一门）
0x602f444    call 0x6072628 }                ← audio_adc_irq_handler
0x602f44a  {r4-r0} = [sp++]                  ← 寄存器组 pop（frame12_pop 配套）
0x602f44e  {psr, rets, reti} = [sp++]        ← 特殊帧 pop
0x602f450  csync
0x602f452  rti                               ← RTI 尾（与 AE §6 记录一致）
```

**帧锚常量（实施可直接落）**：`AUDIO_IRQ_FRAMES={0x602f406:'frame12_push',
0x602f44e:'frame12_pop'}`（pop 锚=**特殊帧恢复 pop.srl m=41**——probe_boot 的 frame12_pop
形状校验钉死该指令形态；0x602f44a 的 pop.rmask 是通用寄存器组恢复、不进帧锚表，
对照 TIMER0_IRQ_FRAMES={0x12ece8, 0x12ed08} 同形）；`AUDIO_IRQ_RTI_TAIL=0x602f452`。
（本路首版曾把 pop 锚写在 0x602f44a 并称间距 2 字节——经 codex 复核勘误：锚在 0x602f44e、
pop.rmask 与 pop.srl 相距 4 字节。）

**audio_adc_irq_handler 0x6072628 全形状**（`ag_dis.py 0x6072628 0x6072710`）：

```
入口：
0x6072628  [--sp] = {rets, r10-r4}
0x607262a  r0 = b[QCY+4686]   （= b[hdl+90]，QCY=0x1265C0=1205696）
0x6072638  r5 = 2
0x607263a  if (r0 == 0) 跳过 r5=4            ← b[hdl+90]=宽度选择布尔：0→ssize=2，非0→4
                                              （DWARF 的 bit_width 在 +108，+90 是 QCY 构建的
                                              等效布尔 [推断]；r5=ssize 本身由消费点钉死）
0x607263e  call 0x12b55c                      ← 临界区 enter（RAM-code，与 0x601bd3a/0x6072470
                                              三处同款使用互证；exit 配对 0x12b5b0 在尾部）
0x6072644  r0 = b[QCY+4675]   （= b[hdl+79]，活跃通道计数——见下方勘误）
0x607264c  r10 = 0xFE0538；r9 = [r10+12]（PNS）；r1 = [r10+4]（ADR）；r2 = [r10+20]（SRP）
0x607265e  r3 = b[QCY+4651]（= b[hdl+55]，位宽模式字节）
0x6072666  r0 *= r5（×ssize）；0x6072668  r0 *= r2（×SRP）；0x607266e  r4 = r0 + r1
                                              ← data = ADR + SRP × ch × ssize
0x6072670  按位宽模式分派 0x60724d8（16bit 交织子帧选择，r2=0/1 通道索引）
链表交付（环形，哨兵=hdl 基址）：
0x60726dc    r1 = r4（data）；r0 = [r7+8]（priv）；r2 = r5（len=ssize×PNS）；r3 = [r7+12]
0x60726e4    call r3                          ← handler(priv, data, len)
0x60726e6    r7 = [r7+0]；if (r7 != hdl) 继续循环
             ← 节点布局 {next@+0, …, priv@+8, handler@+12}
尾部：
0x60726ec  [r10+24] = r8（SRN=PNS）；[r10+12] = r8（PNS 重装）；[r10+0] |= 0x40（CON 清位）
0x60726f8  call 0x12b5b0                      ← 临界区 exit（配对闭合）
0x60726fe  {pc, r10-r4} = [sp++]              ← 被 call 的普通返回（非 rti——rti 在分发器）
```

与 AE §2.2 的「data = ADC_ADR + SRP × channel_num × ssize」「尾部 SRN=PNS、PNS 重装、
CON|=0x40」逐字段一致；本路补：帧锚、临界区对、链表节点布局、b[hdl+90]→ssize 选择器、
0x6072470（DAC handler）与 0x601bd3a（ANC 图门）对 0x12b55c/0x12b5b0 的同款使用。

**勘误（对 audio-input-paths §1.7②/§1.8③ 与蓝图 §0 表）**：hdl+79 的身份不是
「+79[ch] 每通道引用计数数组」，而是**单字节活跃通道计数/通道表游标**——
0x606b4d0（adc_mic_open）在互斥内做三件：`b[hdl+78] |= bit`（使能位图）、
`b[hdl + b[hdl+79] + 48] = cfg高nibble`（通道表按游标追加）、**`b[hdl+79] += 1`**
（0x606b554..0x606b55a），另写每通道 4B cfg 槽 hdl+52+ch×4（0x606b576..0x606b582）；
0x606f8b6（adc_mic_close）对每个关闭的使能位 **`b[hdl+79] -= 1`**（0x606f8d4..0x606f8da），
**归零才 call 0x606f3c4（digital_close）**（0x606f8e8..0x606f8ee，与 AP §1.7③ 的断电
语义一致但计数形式是单字节不是数组）[静态·本路，`ag_dis.py 0x606b4d0 0x606b5b0` /
`0x606f8b6 0x606f940`]。**对注入断言的直接含义**：通道数=b[hdl+79] 单字节直读；
帧内交织序按 hdl+44/+48 通道表（open 追加序）。AR §5.7 的「b[hdl+79]≥1」下界断言
不受影响；蓝图 §4.1 的 `refcounts:{'0':1,'1':1}` 键应改读单计数+使能位图。

**勘误 3（output-handler 链表头位置）**：链表头=**hdl 自身的 +0（next）/+4（prev）**
双向环形（哨兵=hdl），不是 AE/AP/AR 记的「hdl+92 链」——挂链函数 **0x606df3a(r0=hdl,
r1=node)**（AP §1.7③ 记的参数顺序同错，一并勘误）在临界区内做尾部插入
（[hdl+4]=node、[node+0]=hdl、[node+4]=旧尾、[旧尾+0]=node，0x606df54..0x606df5c；
调用点形状 0x6020334..0x602033c：r0=QCY+4596、r1=obj+16），0x6072628 遍历从 [hdl+0]
起到回到 hdl 为止（0x60726d2/0x60726e6/0x60726e8）[静态·本路]；运行时互证：park 世界
空链=hdl+0/+4 自指（§6.1）而 hdl+92 处是值 3（其它字段）。节点布局
{next@+0, prev@+4, priv@+8, handler@+12}。

**实施墙清单（0x12b55c 相关，本路首版误判、经 codex 复核+装载映射解码勘误）**：
0x12b55c 是 RAM-code（boot-handoff.md:54：官方 app 0x060c3e18 的 18644B 复制到
0x12a640..0x12ef14）——**字节就在 app.bin 里**（本路首版用 ROM 窗口解码全空即断「不在
静态镜像」是错的：该区不在 ROM 在 RAM）。按映射（0x12b55c→app 0x060c4d34）解码：
`cli` → `IPMASK[cnum<<11 + 0xFFE1A8] = 6`（提中断屏蔽级）→ 每核嵌套计数 [0x1264F0+cnum×4]+1
→ csync——即 **os_enter_critical（cli+提级+计数），无 pend 无轮询**，IRQ 语境无死等风险；
exit 配对 0x12b5b0 恢复。CF 路当年「停在 0x12b56c idle 轮询首读 IPMASK」的口径按此
重审（0x12b56c 是**写** IPMASK 槽的 `st.w [r0+r2<<2]`，非轮询读——CF 语境疑为变址写
未建模时代的停点记录）。0xffe1a8 的访问面在 D1 事件循环过线时已放行 [文档]。

**对 §5/§7 的连带修正已应用**：交付段验收不再要求经过 DAC 清位分支；模型件（步 1）
为 b2 硬前置。

## 5. 验收判据草案（预研问题 5）

沿用蓝图 §3/§4 的 b1-b4 框架（三场景：esco 首场景/mic_effect/ANC），本页按「注入正弦/
直流样本→可观测变化」重述+负例：

- **D2AUD-b1 输入链打开（直调开链）**：直调 0x60200b6(0x5)（esco）后真实执行——
  blob 解析→zalloc→逐通道 adc_mic_open（hdl+78|=bit、hdl+79++）→ADA_CON8/CON4-7 写→
  set_buffer **五写**（观测=0xFE053C/40/44/48/4C 写记录，HWP=0/SRP=0）→handler 挂
  hdl+0/+4 环形链→CON bit5+bit4 置位。通道数=b[hdl+79]（期望 2）+hdl+78 位图互证。
- **D2AUD-b2 DMA 交付（单发）**：预填 N=irq_pns 帧交织样本（特征值：帧内各通道不同
  常数，如 ch0=0x1234/ch1=0x5678，或蓝图公式 sample(ch,k,n)=((ch+1)<<8)|((k&0xF)<<4)|(n&0xF)
  ——兼具正弦类的可区分性与直流类的确定性）→置 bit7→IRQ68 真实交付→分发器 ADC 侧
  必经 PC 覆盖（0x602f42e 读/0x602f430 pending 门/0x602f434 清位/0x602f43a+0x602f440
  双使能门/0x602f444 call handler）——**0x602f418（DAC 清位）不在 ADC 单发覆盖集**：
  DAC pending 未置时真实代码在 0x602f414 跳过它（DAC 侧覆盖只在单独置 DAC pending 的
  变体里断言）→0x6072628 执行→尾部 SRN=PNS 重装+CON|=0x40。
- **D2AUD-b3 消费闭环**：output handler 逐块被调（esco：0x6031D5A 调用次数=交付块数；
  mic_effect：0x6032e80 前 10 块入口即返+第 11 块起 memcpy 真跑）；**DSP 前样本逐字节
  回读=注入交织序**（data 指针处）；辅听侧 'jlstream' 队列出现 cmd5 时间戳/水位消息并被
  框架任务消费（M1-c 的 resume+watch 0x6064c0e 同款机制可复用于「注入后事件被消费」验证）。
- **D2AUD-b4 连续性**：两整圈环形绕回+块号字段 k 单调（旧帧重复消费即 FAIL）。
- **负例（静音/不触发）**：①预填全零样本（静音）且正常交付→handler 被调但样本字节=0、
  下游无本场景事件面变化（ANC 状态字节/增益寄存器不变）；②不置 bit7（无 pending）→
  零交付零回调（deliveries 空）；③CON bit5 或 bit4 未置（门关）→分发器读了 pending 但
  不进 handler——三门各一个 masked 变体（timer0_irq4_masked_probe 先例）。
- **诚实收口**：样本=显式合成 fixture（具名、报告标注 synthetic-not-measured）；DMA 完成
  时序=投影（b2/b4 结论带「DMA 完成行为投影下通过」前缀）；ANC 场景只证「数据抵达 ANC
  输入边界+配置链真实执行」，不宣称降噪效果（JL_ANC 滤波是硬件块）。

**与 d2b 验收面板的对应**（[docs/acceptance-samples/d2b/](acceptance-samples/d2b/README.md)
8 条 NOT_RUN 的注入面，本页判据=其执行半边的预研依据）：b1=INJ-03/04/05 的打开链断言、
b2=INJ-01（模型件）+INJ-02（IRQ68 交付）、b3=INJ-03..05 消费闭环、b4+负例=INJ-06、
配置切换=SW-01/02；面板「注入面共同依赖地图」的四行等待项中，「BF 前沿释放」已完成
（§6.2）、「前置静态钉死四件」已由本路闭合（§7 步 0）——剩余等待项只有「AW 蓝图实施」
（§7 步 1-2）与 R1-R9 观测项（场景自然回答）。

## 6. 与 park 稳态的关系（预研问题 6）[运行·本路]

**复现方法**：一次性脚本 `~/tmp/d2aud/run_park4.py`（wlaud-init-map §0.1 同款：import 主仓
tools/probe_irq_entry，scenario_flags('p11_heap_experimental') 原样 kwargs 调 irq_case，
monkeypatch dual_case 注入 post_stop；只读，零工具改动）——全量重放 ~14min 至停点
**29,601,125 @ 0x606955c（读 0xfd1104 GPCNT NUM，停点 CPU0）**，post_stop 以 CPU0 视图
（machine.cores[0].mem）读音频输入状态面，结果存 `~/tmp/d2aud/park_state4.json`。

### 6.1 RAM 面观察（停点时刻）

| 观察项 | 地址 | 运行时值 [运行·本路] | 静态预期对照 |
|---|---|---|---|
| esco_adc blob 拷贝区 | QCY+3460..3507 | **en_map=0x0000000B** + 4×desc `01 01 01 01 00 0e 08 00 00` + 计数 3 + 大缓冲指针 0x0041858C | **前 40B 与 stream.bin 镜像 blob（AP §1.7①）逐字节一致**（后 8B 是计数/pad/缓冲指针，非 blob 内容）——boot initcall 0x602eb86→0x6014162 解析器真实跑过（含使能位计数→b[QCY+3500]=3、大缓冲 zalloc→[QCY+3504]） |
| ADC reset/init guard | b[QCY+4673] | **0x04** | AN 路记「reset 完成置 1」——现为 4，状态字非布尔，如实记录（不影响 hdl 落盘事实） |
| hdl 槽 | [0x126A90] | **0x001277B4** | AP 勘误后地址精确落盘 ✓ |
| hdl 链表 | hdl+0/+4 | **0x1277B4 自指双向（空链）** | 无任何 output handler 挂着（消费者全未激活） |
| hdl 通道面 | hdl+78/+79 | **+78=0x01（仅 bit0）、+79=0** | +79=0=通道全关；+78 bit0 残留的写点候选=0x606de62 通道登记族或 open/close 的 mult 位保留（AP §1.7② 机制「ch0 注册位保留给 ANC」），本路未逐一区分 [未钉] |
| hdl 其余 | hdl+8=0x124CB9（全局 SR 域）、+44..47 通道表 `00 01 03 ff`、+92=3 | 混合状态 | 字段精确归属留实施 world_prep 按需核 |
| anc_hdl 槽 | [QCY+500] | **0x0041921C（非零）** | AL §1.6② zalloc(572) 落盘 ✓ |
| anc param | anc_hdl+32 起 | lff/lfb/rff/rfb_en=**全 1**、dut=7、fade_lvl=1、**mode=1（OFF，param+1=anc_hdl+33；本路首版读 param+0 误记 0，codex 复核勘误）** | 与 AL param_init 静态值逐字段一致 ✓ |
| **mic_type** | anc_hdl+45..48 | **[1, 2, 0, 3]** | **与 AL 静态 [1,2,0,0] 不符——勘误，见 6.3**（mic_param 运行值未实读——dump 偏移误用 anc_hdl+292，实为 anc_hdl+324=param+292，留实施 world_prep 补） |
| esco 运行对象 | [QCY+620] | 0 | 通话未开（稳态）✓ |
| 场景 mic 标志 | **b[0x12657C/0x126580/0x126582/0x126584/0x126588]**（绝对地址；wlaud 图 §2.3 的「QCY+468..」相对偏移系笔误，codex 反汇编 0x602ea88..0x602eb0a 复核） | **0/1/0/0/1**（a2dp、DC74 置位；**mic_effect=0**——本路首版按错偏移误读为 1，勘误） | 五标志 OR 非零→audio_adc_init 已跑 ✓ |
| config-89 合并槽 | QCY+524..539 | 全 0 | 擦除态世界 config 89 缺失，未合并 ✓（AP §1.7① 形状预期） |
| ANC 图门条件 | [QCY+464] | **0x00419D70（非零）** | 0x601bd3a 第一条件成立；但 0xfe0400 族零写记录（下表）=图门未走到底 |
| IRQ67 向量槽 | 0x19FF0C | **uninitialized（未注册）** | ANC run 未执行 ✓（boot 只跑 mic_management 0x602f2e2） |
| **IRQ68 向量槽** | 0x19FF10 | **0x0602F406** | 与 AE 静态锚（注册点 0x602ec28）**精确一致** ✓ |

### 6.2 SFR/投影面观察

park 世界的 hardware 上已存在这些音频输入侧投影容器（推进前缀逐路放行的累积）：
`jl_audio_adc_reset_records`（0xFE0538 ADC_CON）、**`jl_audio_adc_dma`（五字 0xFE053C/40/44/
48/4C 写记录，路 JLA）**、`jl_adda_ada`（ADA 九字 0xFE0A20..40）、`jl_adda_family`（模拟族）、
`jl_adda_daa_con1`、`jl_adc_start`（MIC_CTL0/1+MBG_CON+ADC_CON1 四字，路 ASS）、`p11_lpvad`
[运行·本路枚举]。**0xFE0400（JL_ANC）零投影容器**——ANC 硬件块族从未被放行（图门/ANC run
未执行的自证）。

推进前缀里的关键执行证据（**全部 [文档]**=HANDOFF 路线链条目，非本路 dump 直接给出——
本路 JSON 只存了投影对象 repr，无写计数/当前值；终态与历史的衔接是「终态+hdl+79=0 +
HANDOFF 记录的放行序列」的合成判断）：**音频输入 pipeline 在前缀里完整开过一轮**——
ADA 上电（ADA 写 12→57→71 随多轮重配）、**set_buffer 五写真实执行**（21,800,325 @
0x606df1e，ADR 值 0x00419264=真实指令结果，路 JLA 放行五字写记录）、MIC_CTL0/1 模拟
mux 配置（路 ASS 四字容器，MIC_CTL1 家族终值 0xff8）、PGA 切换（CV 0x33/0x73）——随后
**关闭**（终态 hdl+79=0 [运行·本路]）。「开→配→关」生命周期的**执行证据归 HANDOFF**
（各停点即写指令本身），本路终态与其吻合；具体哪个调用者开的（§6.3 存疑项）未回溯。

### 6.3 裁决与勘误

**问题 6 的答案（分层）**：

| 层 | park 稳态状态 | 依据 |
|---|---|---|
| 中断注册 | **IRQ68 已注册**（向量槽=真分发器） | 6.1 |
| 软件对象 | **audio_adc_hdl/anc_hdl/blob/大缓冲全部就绪** | 6.1 |
| 数字通道 | **关**（hdl+79=0、无 handler 挂链） | 6.1 |
| DMA 五寄存器 | **有历史写值**（set_buffer 真实执行过一轮后随关闭失效）；稳态无在途 DMA | 6.2 |
| ANC 数据面 | **未启动**（IRQ67 未注册、JL_ANC 族零写） | 6.1/6.2 |
| 采样数据流 | **零**（无 pending、无交付、无消费者） | 分发器从未被调用（JLA 注记） |

**启动路径**：直调开链（S 路先例）——不需要 resume 推进；**开链写侧零新放行**（要写的
寄存器族全部已有容器，6.2），但**交付段有已知未建模面**：五字容器（路 JLA）是纯
write-only（读直接抛 jl_audio_adc_dma_read_not_modeled）、SRN 0xFE0550 留在白名单外、
ADC_CON 无 pending 投影、JL_ANC 族无容器——而 0x6072628 恰恰要**读** ADR/PNS/SRP 三字+
**写** SRN，分发器要**读** CON bit7。即蓝图的模型件（pending 投影+五字读+SRN 写+
deliver_audio_dma_irq）是交付段的硬前置，不是「可后置的完善件」（本路首版表述过强，
经 codex 复核收窄）。若走「让固件自己重启输入」的事件驱动路径（BT esco 事件等）才
需要 D1 域继续推进，注入场景不依赖它。

**勘误 1（mic_type 运行时值）**：AL §1.6② 静态判 mic_type=[A_MIC1,A_MIC2,A_MIC0,
**A_MIC0**]（[+16] 无存储指令→zalloc 默认 0）；运行时实值 **[1,2,0,**3**]**——
mic_type[3]=A_MIC3，即 **RFB 角色落在 A_MIC3，右耳 FF/FB 不共用 A_MIC0**。存在 AL 未钉
的第二个写点（非 0x602f0d0 族）[未钉·写点待溯源]。**对通道占用表的修正**（§3 表按
mic_type=[1,2,0,3]×en_map=0x0B 推导，mp 运行值未实读）：mic_management 求值后
mp[0..3] 全部 en=1（i=0→mp[1]、i=1→mp[2]、i=2→mp[0]、i=3→mp[3]），
mult_flag={mp[0]=1, mp[1]=1, mp[2]=0, mp[3]=1}——ch0/ch2/ch3 通话复用、ch1 ANC 独占
（按 §3 表的通道行）。「3mic」解释需按此重审（AL §1.6⑤ 的互证链条部分失效；四个
mic_type 软件输入编号≠四个物理元件的实证——接线在 config 59 平台表，公开工件外）。
蓝图 §5-R4 的「ch2 无开启者」**不成立**（§3 表注：flags bit1→slot2 强制使能组 2，
AP §1.7③ 原文与 codex 反汇编双证）；各通话 case 真实 flags 取值仍是观测项（R6）。

**勘误 2（hdl+79 语义）**：见 §4.4——单字节活跃通道计数，非「每通道引用计数数组」。

**AP §1.7⑤「公开镜像不走 boot mode-2 校准块」的存疑**：6.2 的「开→配→关」完整周期
+多轮重配形状与校准块（0x602ec7c..0x602ee94 开→自校准→关）吻合，但三门判据
（b[QCY+3464]==2）静态读 blob=1 应不开——两者矛盾未决 [未钉]；候选解释=三门结构判读
有误（G6/==2 的嵌套关系）或该周期由 21.8M 时点的其他开链执行。**对注入设计无影响**
（无论谁开的，重开都走直调开链）。

## 7. 实施切分建议（预研充分的部分可直接实施）

蓝图 §6 的 6-8 天计划按 park 时代重排——**P1-a 已发生、开链寄存器族已全部放行（§6.2）、
帧锚/实参已钉（§4.4/§2）、直调+resume 基础设施已服役（SIMB1E）**，剩余量比蓝图估的更小：

| 步 | 内容 | 前置状态（本路更新） | 量 |
|---|---|---|---|
| 0 | ~~静态钉死四件~~ | **四件全部已有**：分发器帧锚/RTI（§4.4）、ioctl 0x603293e(node,cmd,…) 入口 ABI（§2 表内）、audio_anc_run 0x601c8de(mode, 0x1285A4) 实参（§2）、request_irq 实参（AE §1.2 prio2/0x602f406 运行时落盘 §6.1） | 0 |
| 1 | 模型件**剩余半边**：JlAudioAdcDma 的 pending 位投影+produce_block+HWP 推进+machine deliver_audio_dma_irq+dispatch 分支+registry 条目 | **写记录半边已存在**（jl_audio_adc_dma 五字容器，路 JLA；ADC_CON/ADA/MIC_CTL 容器全在，§6.2）——蓝图 §2.1 的「新类」实为「扩类」 | 0.5-1 天 |
| 2 | esco 首场景（开链直调+注入循环+b1/b2） | 开链入口无 pend [静态·本路 §2]；开链**写侧**零新放行 [运行·本路 §6.2]；0x12b55c=cli/lockset 族（§4.4 勘误后无 pend 风险）；**依赖步 1**（交付段读侧/SRN/pending 是硬前置，非可后置） | 1-1.5 天 |
| 3 | 断言机读化（check_audio_dma_scene+b3）+面板 INJ 条目升格 | 蓝图 §4.2 骨架；面板 NOT_RUN→逐条 PASS/FAIL | 0.5-1 天 |
| 4 | mic_effect+ANC 场景（R1/R2/R4/R5 观测） | 蓝图 §3.2/§3.3；**R4 按 §6.3 勘误后占用表重算** | 1.5-2 天 |
| 5 | b4 连续性+配置切换变体 | 蓝图 §3.4 | 1 天 |
| 6 | 收尾（HANDOFF/registry/masked 反事实） | — | 0.5 天 |

另一条可选加速线：**在 M1 世界（sim_world_boot.build_world+sim_world_driver）上做注入
原型**——直调/resume/watch/快照基础设施已服役（SIMB1E 读/写环验证过），先拿「**开链
（b1）**」的证据包（写侧全放行，零模型件依赖）；「单发交付+样本回读」（b2 及以后）仍以
步 1 模型件为前置，不可跳过（§6.3 收窄后口径）。取舍：M1 世界是世界服务器形态（长驻），
probe 场景形态（一次性回归）才是 frontier_registry 的正式收口——原型与正式化两步走。

## 8. 未钉项清单（如实标注）

1. **[未钉·静态] mic_type[3]=3 的第二写点**——AL §1.6② 只钉 0x602f0d0 族（[+16] 无存储），
   运行时实值 3 说明另有写点（boot 后续链/配置装载），溯源归实施路 ANC 场景顺带。
2. **[未钉·静态] park 世界 hdl+78 bit0 残留的写点归属**（0x606de62 登记族 vs open/close
   mult 位保留，§6.1）——不阻塞注入（world_prep 读回后按需补齐）。
3. **[未钉] 「开→配→关」周期的执行者**（§6.3 勘误存疑：AP 的三门判读 vs 运行时形状）——
   21.8M 时点的 set_buffer 调用语境未回溯；对注入无影响（重走直调开链）。
4. **[未钉·运行观测] 0x12b55c 临界区 enter 在 IRQ 语境的行为**——快速路径还是轮询；
   IPMASK 读已建模，预期可过，首跑实证。
5. **[未钉·运行观测] mic_effect 开链的模式字节运行值**（决定 SR 三态 16000/全局/44100 与
   是否用共享静态 nodecfg QCY+3452）——蓝图 R1。
6. **[未钉·运行观测] event 18（0x60057e6(18,name)）注册者**——决定取名+blob 主路径 vs
   兜底块——蓝图 R2。
7. **[未钉·运行观测] ANC 消费路径两候选**（共享 hdl+0/+4 链 vs JL_ANC 自有 DMA）与 ch2
   （A_MIC2/LFB）数字通道开启者——蓝图 R4/R5，ANC 场景首个要回答的问题（§6.3 勘误后
   占用表已变，张力本身维持）。
8. **[未钉·静态] DAC handler 0x6072470 的对象指针 0x1282E8 的结构身份**（立即数钉死，
   字段未解——输出侧域，不阻塞输入场景）。
9. **[未钉·静态] 0x60724d8（16bit 交织子帧选择 helper）内部布局**——影响 data 指针在
   交织帧内的精确落点断言，实施步 2 首跑时按 coverage 钉。
10. esco flags 各通话 case 真实取值、ANCIF/3mic_coeff 系数文件内部格式（§6.3 勘误后
    「3mic」语义待重审）、DMA 总线行为/缓存一致性——蓝图 §5「仍开放」清单沿用。

## 9. 复现

```
# 反汇编（app.bin sha 18f3d573…，load base 0x06000100，codec=主仓钉死 pi32v2-isa）：
python3 ~/tmp/ag-wlaudmap/ag_dis.py 0x602f406 0x602f460    # IRQ68 分发器全body（§4.4）
python3 ~/tmp/ag-wlaudmap/ag_dis.py 0x6072628 0x6072710    # audio_adc_irq_handler 全body
python3 ~/tmp/ag-wlaudmap/ag_dis.py 0x6072470 0x6072480    # DAC handler 入口（0x12b55c 同款）
python3 ~/tmp/ag-wlaudmap/ag_dis.py 0x601bd3a 0x601bd90    # ANC 图门（0x12b55c/0x12b5b0 对）
python3 ~/tmp/ag-wlaudmap/ag_dis.py 0x60200b6 0x6020100    # esco 开链入口（无 pend 复核）
python3 ~/tmp/d2aud/rom_dis.py 0x12b55c 0x12b5a0           # ROM 窗口解码全空（RAM-code 区不在
                                                          #   ROM——正确解码经 app 0x060c4d34 装载映射，见 §4.4）
# park 停点状态面（§6，全量重放 ~14min）：
python3 ~/tmp/d2aud/run_park4.py   # 输出 ~/tmp/d2aud/park_state4.json；尾行汇总 print 因
                                   #   r['stop_pc'] 为字符串抛 TypeError（dump 已落盘、脚本收尾
                                   #   打印失败，不影响数据——本路如实注明）
#   （快照 capture 不可用：world_snapshot M3 adapter 缺 JlEqInitWriteRecords 捕获支持，
#    SnapshotError missing-state guard——JLEQ 后快照机制待扩展，本路如实记录不动 tools/）
# 常量复核（脚本，禁手算）：16647424=0xfe0500、1213160=0x1282e8、1205696=0x1265c0、
#   0x19FE00+68*4=0x19FF10、QCY+4596=0x1277B4、QCY+4686-4596=90（hdl+90 宽度布尔）、
#   0x1265C0+8164=0x1285A4（audio_anc_run 第二参）、0x606b4d0 内 hdl+76 基（+78 使能/+79 计数）
```
