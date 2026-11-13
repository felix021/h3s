# D2-b 前置：音频输入路径钉死——mic 布局、ADC DMA 数据流与注入方案（路 AE / sim-audioin，只读静态调研）

2026-11-14。本页回答 docs/d1-acceptance.md §6 D2-b 留下的「输入路径未钉死、需静态调研」：
mic 数量与布局（JD 商品页「头戴式 7 麦 56dB 主动降噪」固件侧求证）、从 mic 硬件采样到
jlstream pipeline 消费的完整链路（每环地址锚点）、三选一注入方案、D2-b 可测断言草案。
**只读**：未改 tools/ 任何代码、未跑任何 probe；反汇编用钉死 codec（pi32v2-isa @3608ed1，
主仓 tools 加载）+ 官方 app.bin 公开字节（sha `18f3d573…`，加载基址 0x06000100，本地提取自
官方 UFW 逐层解包，JLFS/CRC 全过）；SDK 参考 = pinned commit `67328f4` 的 media.a LLVM IR
（本路新提取 adc_core/audio_adc/audio_link/audio_dac_io/anc_core/anc_db 六对象 llvm-dis）+
按同 commit 下载的 br28.h（sha256 与 data/dependencies.json 钉死值 `111e29ea…` 一致）、
SDK/audio/cpu/br28/audio_anc.h（与主仓 reference-cache 缓存副本逐字节相同）、板级 demo 配置
board_ac701n_demo_cfg.h。一次性脚本放本地 tmp（~/tmp/ae-audioin），不进库。结论基于
worktree 基线 b3d7e13。

证据强度：**[静态]** = 指令锚定反汇编/数据表解码（本路，H3S 固件真字节）；**[SDK]** = 参考
SDK 源/IR 交叉佐证（非 QCY 精确源）；**[镜像]** = 公开资源字节直接解析；**[文档]** = 既有
docs/HANDOFF 钉死条目；**[DWARF]** = media.a 调试信息提取的结构体布局（SDK 侧权威布局）；
**[推断]** = 上述链推出。静态结论全部 derived-not-measured。

## 0. 一句话结论

H3S 的音频输入 = **JL_AUDIO 模拟 ADC 的单一 DMA 通道多路复用**：最多 4 路模拟 mic 的样本
交织写进同一个环形缓冲（JL_AUDIO ADC 寄存器组 0xFE0538..0xFE0550），每次中断（IRQ68，
全固件唯一音频 DMA 中断）由分发器把帧指针交给 `audio_adc_irq_handler`，后者按 output-handler
链表把 (buf, len) 回调交付给各消费者——ANC 核心（经共享 ADC + JL_ANC 硬件滤波块 0xFE0400）、
通话 aec 前端、mic_effect/pc_mic 的 **0xD06D 源节点（即 jlstream pipeline 的输入端）**。
JD「7 麦」**无法**映射为 7 个并发音频通道（硬件上限 4 路模拟 ADC；数字 mic 接口 ALNK0/PLNK
无 IRQ 注册、无直接寄存器引用）——最可信分配见 §1.5（4 ANC + 2 通话 + 1 VAD/辅听，通话与
ANC 按 SDK 复用机制共享通道）。GPADC 慢速通道（0xFD3100 CON/0xFD3104 RES，VBAT/VTEMP/
按键分压，路 BU/BY 域）与音频 mic 通路**完全分离**，D2-b 注入面是前者之外的后者。

## 1. mic 数量与布局（四问之一）

### 1.1 硬件面：4 路模拟 ADC 通道，每路独立模拟配置 [静态+SDK]

- **数字侧**（SDK adc_core.c `adc_core_digital_open`，IR 逐字段）：`adc_core_digital_t`
  {adc0..3_sel, adc0..3_dcc, adc_ch, mic_capless, bit_width, clk_sel, sample_rate}。采样率表
  `adc_sample_rates[11]`={48k,44.1k,32k,-,24k,22.05k,16k,-,12k,11.025k,8k}，SR 编码=
  索引+4 写 **ADC_CON(0xFE0538) bits0-3**；使能位 |=0x20/0x10/0x100/0x800、清 0x200；
  adc0 dcc bits12-15、adc1-3 dcc 在 **ADC_CON1(0xFE055C)** 半字节段；每通道 3bit 输入源
  选择（模拟 mux / DMIC）在 **MIC_CTL1(0xFE0908)**；mic 时钟 **MIC_CTL0(0xFE0904)** bit0。
- **模拟侧**（H3S 固件两段关闭序列逐位反推 + SDK audio_adc.c 对位）：**ADA_CON1
  (0xFE0A24)** 每通道 3 个使能位段——ch0={bit6,7,8}、ch1={bit9,10,11}、ch2={bit12,13,14}、
  ch3={bit15..17}（digital_close 尾段 0x606f442..0x606f4f8 逐通道清 [0xFE0A24] 位段 +
  每通道配置寄存器；mic_close 变体 0x606f4c0..0x606f53e 同族再清一遍）；每通道模拟配置在
  **ADA_CON4..7（0xFE0A30/34/38/3C）** 各一份（位 {0x8000,0x4000,0x10}）；**ADA_CON8
  (0xFE0A40) = 4×5bit 每通道 PGA 增益**（H3S 增益 setter 0x606c3aa：mic0 insert p:0 l:5、
  mic1 p:5，0x606c462/0x606c494）；mic 偏置/LDO 在 ADA_CON0/ADDA_CON0/ADDA_CON1
  （0xFE0A20/48/4C，上电序列 0x606b5b0 族 + 偏置电阻表 @0x609CBE4+2300，上电后轮询
  PMU 通道 0x20000 电压 ≥1500 等待偏置建立，0x602edc4..0x602ede8）。
- **结构布局证据 [DWARF]**：audio_adc.c 调试信息给出 `audio_adc_hdl`（channel_num@+840bit、
  max_adc_num@+848、digital_mic_open@+816、anc_mic_open@+824、adc_mic_open@+832、
  bit_width@+864）与 `adc_mic_ch`（adc/handler/sample_rate/bufs/buf_size/ch/gain/buf_num）
  ——**4 通道架构在驱动结构里写死**。

### 1.2 中断注册面：IRQ68 唯一音频 DMA 中断；数字 mic 接口未注册 [静态]

全固件 `request_irq`（0x6065d86）27 个调用点逐站提取实参，音频相关：

| IRQ | hwi.h 身份 | 优先级 | handler | 注册点 |
|---|---|---|---|---|
| **68** | **JL_AUDIO**（DAC+ADC 合并） | 2 | **0x602f406**（分发器，§2） | 0x602ec28（0x602ea68 内） |
| 67 | JL_ANC（硬件滤波块） | 0 | 0x6073d7e | 0x606da6a、0x606ec02（两处） |
| 48 | SBC 硬件加速 | 1 | 0x6073cc4 | 0x6073916 |
| 39 | GPADC（慢速通道） | 1 | 0x60348e8 | 0x601219c（路 BU 已钉） |

**IRQ69（ALINK0）/IRQ70（PDM_LINK）零注册**；且 0xFE0600..0xFE07FF（ALNK0/PLNK 寄存器组）
在全 app 零个 `li` 直接引用（本路扫描；变址访问不排除，[推断] 数字 mic 接口本固件未用，
mic 全走 JL_ADDA 模拟输入）。→ **并发音频输入上限 = 4 路模拟 mic**。

### 1.3 消费者面：三条开链 + ANC 共享，各自通道数 [静态]

| 消费者 | 打开链（函数锚点） | 通道数证据 |
|---|---|---|
| **mic_effect / pc_mic（jlstream 输入）** | 源节点 plug create **0x6032924**（plug 表 0x60a917c 条目[1] key=0xD06D）→ 读节点 cfg（ch_map@nodecfg+8）→ 逐 ch_map 位开通道（0x606de62/0x606b4d0/0x606de92/0x606c3aa，0x6032b78..0x6032c7c）→ SR 写 0x606dee0 → set_buffer 0x606dee4 | **stream.bin mic_effect(0x9c2d) 图描述符含 3 个 ADC(0xD06D) 源节点** [镜像，offset 0x2d0：`2d 9c d0 00 | 6d d0 03 00`——uuid 0x9c2d + ADC 节点计数 3，路 I 已钉本路复核]；mic 槽结构 4×9B（节点侧 +17/+26/+35/+44），**config id 89** 供给每槽 1 个配置字节+1 个使能字节（QCY+524..527 值/QCY+528..531 使能，0x602eb2a 读入、0x6032ba0 按 enable 门逐槽填） |
| **通话 esco/aec 前端** | **0x60200b6**（调用者 0x60212bc，BT esco 域）：4 slot 使能+增益数组驱动，每通道 zalloc 512B（[obj+60..]）+ 交织大缓冲 = 通道数×0x600（[obj+72]），SR=16000、set_buffer(大缓冲,512,3)，output handler 注册 [obj+40]=0x6031D5A | 4 slot 框架 [静态]；另有 esco ADC 变体 0x60142f0..0x6014460（**2 个 9B mic 描述符** @QCY+3469/QCY+3478，b[QCY+3464]==2 门控，调用者 0x60221e2/0x6042384） |
| **ANC（不经 jlstream）** | SDK audio_anc.c：`anc_hdl->param.adc_set_buffs_cb = audio_adc_fixed_digital_set_buffs; audio_anc_adc_ch_set()`（L1068-1070）→ **复用同一 audio_adc_hdl**；ANC 滤波在 JL_ANC 硬件块（0xFE0400..0xFE04C0，anc_core 驱动） | audio_anc_t（[DWARF] 全布局提取）：**mic_type[4]@+104，角色=LFF/LFB/RFF/RFB**（SDK audio_anc.c L936-939：mic_type[0..3]=ANCL_FF/FB、ANCR_FF/FB_MIC），4 个使能位 lff/lfb/rff/rfb_en@+56..80 |
| boot 期 ADC 子系统初始化 | **0x602ea68**（initcall 表区条目 @0x60c32b0，无线性调用者）：从 stream.bin 场景配置读 5 个「mic 需求」标志（场景 0x7674 tone/0xD96F a2dp/0xB9D1/0x9C2D mic_effect/0xDC74 → b[0x12657C..0x126588]，读取器 0x60755b2）→ OR 非零则 audio_adc_init（hdl=**QCY+4596=0x1267BC**，0x60719a6，注册槽 [QCY+308<<2]=[0x126A90]）→ 模拟上电 → **注册 IRQ68** | 5 场景标志 OR 门 [静态]；b[QCY+3460] bit0/bit1 两 mic 组使能、b[QCY+3469]/b[QCY+3478] 组内通道 id |

### 1.4 ANC 侧 mic 证据 [镜像+SDK]

- 公开 JLFS 顶层资源 **`3mic_coeff.bin`**（1060B，内容 = IIR float 系数流）——资源名字符串
  在 app.bin（0x60a2603，`flash/res/3mic_coeff.bin`），且是**config 默认值内嵌的 coeff 路径**
  （config 子项 0x21/0x22 构造器 0x6004510/0x6004566 族引用同区默认 blob @0x60a25e0）→
  QCY 自选的 ANC 系数文件名指向 **3-mic ANC 配置**（「3mic」具体=每耳 FF+FB 其中一耳少一路
  /或训练用 3 参考点——**布局未钉死**，见 §5）。
- ANCIF（256B）/ANCIF1（3840B）是 flash 地址 0x1fe000/0x1fe100 的**分区描述**，超出公开
  flash.bin（0..0xE6000）——内容不在公开工件里，ANC 参数区维持 hearing-aid §5.2 的「格式
  未解」口径。
- SDK 板级 demo（board_ac701n_demo_cfg.h）模板：`ANCL_FF_MIC=A_MIC0、ANCL_FB_MIC=A_MIC1、
  ANCR_FF/FB_MIC=MIC_NULL`、通话与 ANC 共用 FF MIC 时走 `TCFG_AUDIO_DYNAMIC_ADC_GAIN` +
  `audio_anc_mic_mult_flag_set`（复用标志）——头戴式双耳布局应取 R 侧非 NULL 的变体。

### 1.5 「7 麦」最可信分配方案（derived-not-measured，逐条标证据强度）

硬件硬约束：**并发音频通道 ≤4**（§1.1/1.2）；mic_effect 图 3 路 [镜像]；esco 描述符 2 路
[静态]；ANC 4 角色结构 [DWARF]+「3mic」资源名 [镜像]。据此：

| 用途 | 数量 | 通道 | 强度 |
|---|---|---|---|
| ANC 前馈（每耳 1） | 2 | LADC ch（FF 位置） | [推断]（4 角色结构 + 头戴式布局常识；FF 存在性由 ANCIF 训练参数族佐证） |
| ANC 反馈（每耳 1） | 2 | LADC ch（FB 位置） | [推断]（同上；hybrid ANC 为头戴式标配，56dB 宣传值指向 hybrid） |
| 通话双 mic（拾音+参考） | 2 | 与 ANC FF 复用（SDK mult_flag 机制）或独立通道分时 | [静态]（esco 2 描述符）+复用[推断] |
| VAD/辅听（mic_effect 第三路） | 1 | mic_effect 图第 3 个 ADC 节点 | [镜像]（3 节点计数）+用途[推断] |

**合计物理 mic 元件 ≈ 4（ANC）+2（通话，可能复用）+1（VAD/辅听）= 7 与 JD 宣传吻合**；
但因「通话/VAD 与 ANC 复用通道」（复用是 SDK 显式机制）或「7 为营销计数」，**7 个元件不必
7 条并发通道**。若「3mic_coeff」确指 ANC 仅 3 mic（如 R 侧 FB 缺省），则分配改为 3 ANC +
2 通话 + 2 其他——**ANC 实际 mic 数未钉死**（mic_type[4] 常量初始化点未定位，§5）。

## 2. 音频数据流路径：mic 硬件 → jlstream 消费（四问之二，每环锚点）

### 2.1 JL_AUDIO ADC 寄存器组（布局钉死：每字段 4 字节）[静态+SDK+文档]

TBAA（media.a IR，28×i32 槽）+ SDK 六站点 + BO 运行时 DAC_VL0=0xFE051C + H3S 分发器
[0x602f412 读 [0xFE0500]、0x602f42e 读 [r4+56]=0xFE0538] 互证：**JL_AUDIO 各字段一律 4 字节
槽位**（含声明为 __u16 的字段）。ADC 侧：

| 寄存器 | 地址 | 语义（消费代码锚点） |
|---|---|---|
| ADC_CON | 0xFE0538 | bit7=IRQ pending（分发器 0x602f430 判）、bit6=清 pending/重启（0x602f434、handler 尾部）、bit5=数字使能（0x606f3e4 关）、bit4=使能门（set_buffer 0x606def0 查重+分发器 0x602f440 双检）、bits0-3=SR 码 [SDK] |
| ADC_ADR | 0xFE053C | 环形缓冲地址（set_buffer 0x606df20 写；handler 0x6072656 读） |
| ADC_LEN | 0xFE0540 | 缓冲长度（样本数 u16，len/2 截断，0x606df26） |
| ADC_PNS | 0xFE0544 | 中断周期（样本数；handler 重装 0x60726xx 尾部） |
| ADC_HWP | 0xFE0548 | 硬件写指针（软件写 0 复位，0x606df2e；运行期 DMA 推进） |
| ADC_SRP | 0xFE054C | 软件读指针（**帧**单位；handler 计算 buf 起点） |
| ADC_SRN | 0xFE0550 | 读通知（handler 尾部回写 irq_pns）[SDK] |
| MBG_CON/AUD_CON/ADC_CON1/DCC_CON0-3 | 0xFE0554..0xFE056C | mic 偏置/杂项/每通道 dcc [SDK] |

### 2.2 完整链路（按执行序）

```
模拟 mic → ADA 前端（ADA_CON1 每通道使能 / ADA_CON4-7 每通道配置 / ADA_CON8 5bit PGA、
           偏置上电 0x606b5b0 族 + 0x20000 电压轮询 ≥1500，0x602edc4）
  → 数字通道（ADC_CON SR bits0-3 + 使能位、MIC_CTL1 每通道 3bit 输入源、MIC_CTL0 时钟、
             JL_ASS CLK_CON 0xFE0900 |=0x1/2/0x24/0x80/0x100/0x200，0x606c844）
  → DMA：样本交织帧（帧 = channel_num × (2|4) 字节，bit_width 决定）写入 [ADC_ADR, +ADC_LEN)
       硬件推进 ADC_HWP；每 PNS 样本置 ADC_CON bit7
  → IRQ68（向量槽 0x19FF10=0x19FE00+68×4，注册 0x602ec28）
  → 分发器 0x602f406【真实中断入口：push {psr,rets,reti}+r4-r0】
       DAC 侧：DAC_CON(0xFE0500) bit7 → |=0x40 清 → bit5 门 → 0x6072470（DAC handler）
       ADC 侧：ADC_CON bit7 → |=0x40 清 → bit5+bit4 门 → call 0x6072628
  → audio_adc_irq_handler 0x6072628：
       data = ADC_ADR + SRP × channel_num × ssize   （0x6072656..0x60726e6）
       遍历 output-handler 链表（hdl=0x1267BC+92 的 q_init 链，0x60719e4 建）
         → handler(priv, data, irq_pns × ssize)     （SDK 逐字对应）
       尾部：SRN=PNS、PNS 重装、ADC_CON |=0x40
  → 消费者（谁注册了 output handler 谁拿帧指针）：
       (a) mic_effect/pc_mic 源节点 0xD06D（plug create 0x6032924 注册）→ 写进 jlstream
           节点 → pipeline 推进（cmd5 时间戳/水位消息进 'jlstream' 队列，CI/CK 钉的形状）
       (b) 通话 aec 前端 0x60200b6 的 [obj+40]=0x6031D5A
       (c) ANC：共享同一 hdl（audio_anc.c L1068 adc_set_buffs_cb）→ JL_ANC 硬件滤波
           （0xFE0400..0xFE04C0，anc_core；IRQ67 handler 0x6073d7e）
```

**pc_mic 模板（0x60339a4）的输入侧** [静态+文档]：播放器序列（hearing-aid-l1-payload §1.1）
→ `pipeline_parse(uuid, 0xD06D)`（0x60339e6）→ descriptor walk（0x6074938，CP 已实装）
建出图内 3 个 ADC 源节点实例 → 每实例走 plug create 0x6032924：读 stream.bin 节点 cfg 的
ch_map + config 89 的 4×9B mic 描述符 → 开 ADA 通道 + set_buffer → 注册 output handler。
即 **pc_mic/mic_effect 的「mic-ADC→输出」= 0xD06D 源节点 + 共享 audio_adc_hdl 的标准链**，
与 esco/ANC 同一硬件 DMA、不同 output-handler。

### 2.3 对 BX §3 表的勘误（本路 br28.h 到位后的修正）[静态]

- **0xFE0400 族 = JL_ANC**（br28.h `map_adr(0x04,0x00)`；anc_core.c.o IR 37 处访问
  0xFE0400+ 各偏移 + IRQ67 注册 + 分发器同址）——BX 原标「疑音频时钟/gate 域（SDK 头未
  缓存）」应改 JL_ANC（ANC 硬件滤波块）。
- **0xFE0538 = ADC_CON**（BX 表内「0xfe0538 bit4 完成轮询×4」）——实际是分发器/set_buffer
  的 bit7 pending + bit5/bit4 门（§2.1）；4 个 li 站点（0x606dee6/0x606f3e4/0x60719b8/
  0x607264c）= set_buffer/digital_close/audio_adc_init/irq_handler 四函数基址。
- **0xFE0900 族 = JL_ASS**（CLK_CON/MIC_CTL0/MIC_CTL1）：音频子系统时钟 + mic 时钟/输入
  源选择——BX 表同族猜测就此命名。
- 0xFE0A20..0xFE0A4C = JL_ADDA ADA_CON0-9 + ADDA_CON0/1（模拟 mic 前端）；0x60116b0 的
  [0xFE0A4C]|=2/[0xFE0A48]&=0xFFFFF000（BX 已记）= GPADC 模拟复用对 ADDA 的越域触碰，
  与音频使能位不冲突（位段不同）。

## 3. 注入方案候选（四问之三，三选一框架）

| 候选 | 要建模什么 | 固件哪段代码消费它 | 风险 |
|---|---|---|---|
| **(a) DMA 缓冲预填 + PND/IRQ68 交付（推荐）** | ①预填交织帧样本进 [ADC_ADR..+LEN)（或单帧）；②ADC_CON bit7 pending 读回投影（置位=有帧）；③IRQ68 交付（向量 0x19FF10 ← 真实 request_irq 已写；用既有软件 IRQ 交付机制，BE 路 Timer0 IRQ4 先例）；④HWP 推进/或单发不推进 | 分发器 0x602f406（真实中断入口字节）→ 0x6072628（真实）→ output-handler 链（真实）→ 0xD06D 源节点 → jlstream——**全部固件真码** | 需 ADC_CON 读回投影（BN 路 fd31_adc_con_readback 同族先例）+ IRQ 资格/优先级模型（BF 域既有）；PNS/LEN 语义按 SDK 钉死值填，单发模式可先绕开周期推进 |
| (b) 直调 0x6072628（handler 级 fixture） | 合成 hdl 指针入参 + 预填缓冲 + 寄存器三读（PNS/ADR/SRP）投影 | 0x6072628 及以下全真实 | 跳过分发器的 pending/清位逻辑与真实中断入口（少验一段）；hdl 是堆对象（QCY+4596 初始化后的状态依赖 D2-b1 打开链先跑） |
| (c) 直调源节点 output handler（pipeline 输入级） | 合成 (priv, buf, len) 直调 0xD06D 节点注册的 handler | 仅 jlstream 侧 | 完全绕开 ADC 簿记（SRP/PNS/SRN 不动）；需要 D2-B/C 管道构建先就位才能有节点对象；「输入路径闭环」验收含金量最低 |

**推荐 (a)，一句话理由**：注入面之下（DMA 写+pending 位）只有两个合成件且都有 SDK 钉死
语义与既有投影先例，之上从分发器到 pipeline 全部是固件真实执行——恰好覆盖 D2-b 要观察的
「stream pipeline 消费采样值、DSP 链推进、缓冲/水位事件」整链，且复用 BE/BF 已验证的 IRQ
交付机制，ROI 最高。(b) 可作为 (a) 的调试捷径保留，(c) 只在 (a)(b) 被寄存器语义卡死时兜底。

前置依赖（与既有路线衔接）：D2-b1 打开链（mic_effect 激活 = 路 L 载荷先例 0x6074938
(0x9c2d, 0xD06D) descriptor walk——CP 的 d2c_effect_pipeline 机制换 (scene,plug) 参数即可；
或 pc_mic 播放器 0x60339a4 全静态立即数序列）。

## 4. D2-b 可测断言草案（四问之四，对齐 d1-acceptance §6 与 D2-a 风格）

前提：D1 四断言已过；场景 = opt-in 显式合成（默认关，legacy 报告不变）；全部结论维持投影
等级（hardware_mode_certified=false 不升级）；derived-not-measured 全文标注。

- **D2-b1 输入链打开**：直调 0x6074938(0x9c2d, 0xD06D)（或 pc_mic 序列 0x60339a4）后真实
  执行：plug create 0x6032924 ≥1 次 → 逐通道 0x606b4d0/0x606de92/0x606c3aa（ADA_CON1/
  ADA_CON8 写记录非空）→ set_buffer 0x606dee4 写 **ADC_ADR/ADC_LEN/ADC_PNS/HWP=0/SRP=0**
  五寄存器（观测=寄存器写序列 + 图内 3 个 ADC 源节点建出，CP 的节点建 PC 覆盖集）。
- **D2-b2 DMA 交付（单发）**：预填 N=irq_pns 帧后置 ADC_CON bit7 → IRQ68 真实交付 →
  0x602f406 分发器执行：读 0xFE0538 bit7=1 → |=0x40 清位 → bit5+bit4 门通过 → call
  0x6072628 → 帧指针=ADR+SRP×ch×ssize、output handler 逐个回调 → 尾部 SRN/PNS 重装 +
  CON|=0x40（观测=上述 PC 执行覆盖 + 五寄存器读回序列与 §2.1 语义一致）。
- **D2-b3 pipeline 消费闭环**：'jlstream' 队列在注入后出现并被框架任务真实消费 ≥1 条
  （cmd5 时间戳/sys_timer 形状，CI §9/CK 钉的消费链 PC），jlstream 子任务（0x6086c8c 体）
  或流状态机（0x6075ce0）执行 ≥1 步——即「stream pipeline 消费」的 OS 可观察面。
- **D2-b4 采样值穿透**：注入特征样本（帧内各通道不同常数，如 ch0=0x1234/ch1=0x5678）→
  在源节点 output handler 的入参 buf 处读回相同字节序列（交织序一致）；通道数=ch_map 位
  数。derived-not-measured：模拟器侧读回，不要求 DAC/声学输出。
- **诚实收口**：ADC 样本为显式合成 fixture（具名、版本管理、报告标注），不声明真实声学；
  若 D2-b1 打开链被未建模外设卡住（如 0x20000 偏置电压轮询需 GPADC 0x20000 通道 fixture，
  与 BU/BY 路具名值机制同族），如实记录卡点不静默降级；三镜像（official/b79/EQ 候选）同
  场景 parity 沿用 L2-a 机制（本路域无镜像差异点，预期零分歧）。

## 5. 未钉死处（如实标注）

1. **ANC mic_type[4] 的常量值**：SDK 侧是编译期内置（board cfg TCFG_*）；H3S 固件中该 4 字节
   写点未定位（anc_hdl 为堆对象、写点或经寄存器变址/初始化器，本路 b[rX+104..107] 形扫描
   零命中）。→ §1.5 的 ANC 4 mic 只能停在 [推断]；「3mic」确切布局未定。
2. **3mic_coeff.bin / ANCIF(0x1fe000) 布局未解**：系数文件内容是 float 流，分组格式需
   anc_db 消费语义或真机读取；ANCIF 分区在公开工件外（hearing-aid §5.2 口径不变）。
3. **ADA_CON1 位段→物理 mic 引脚（ain_sel）映射**与 config 89 默认 blob（@0x60a25e0 25B）
   的逐字段语义：只有消费代码形状，无默认值解析。
4. **DMIC 未用结论的强度**：零 IRQ69/70 注册 + 零 0xFE06xx/0xFE07xx li 引用，但不排除
   变址访问；如后续场景撞上 PLNK 寄存器按本路地址表处理。
5. esco 2 mic 描述符与 ANC 4 通道的复用关系（mult_flag 是运行时数据）；b[QCY+3464]==2 的
   模式语义（疑 ANC/dsp 模式）。
6. 0x602ea68 所在 initcall 段（0x60c32a4..0x60c32b4）的 walker 边界：CI 钉的三段表不含它，
   该段执行面按 initcall 区属性 [推断]（boot 期）；如需精确触发序号是 D2-b1 场景观测项。

## 6. 证据锚点索引（复核用）

| 结论 | 锚点 |
|---|---|
| IRQ68 注册（prio2, 0x602f406） | 0x602ec1c..0x602ec28；向量槽 0x19FF10 |
| 分发器（DAC/ADC 双侧 pending-门-分发） | 0x602f406..0x602f452（RTI 收尾） |
| audio_adc_irq_handler（帧指针/链表交付/尾部重装） | 0x6072628..0x60726e6；hdl 链头 0x1267BC+92 |
| set_buffer（ADR/LEN/PNS/HWP/SRP 五写+bit4 查重） | 0x606dee4..0x606df38 |
| digital_close（ADC_CON &=~0x20 + ADA 每通道位段清） | 0x606f3c4..0x606f4f8+（ch0..3 位段表见 §1.1） |
| 每通道 PGA 增益（ADA_CON8 4×5bit） | 0x606c3aa；insert p:0/p:5 @0x606c462/0x606c494 |
| mic 偏置上电 + 0x20000 电压轮询 ≥1500 | 0x606b5b0..0x606b63e；0x602edc4..0x602ede8 |
| JL_ASS CLK_CON 使能串 / MIC_CTL1 源选择 | 0x606c844..0x606c8a8；0x606c8aa..0x606c922（4×3bit p:15/21/12/18） |
| audio_adc_init（hdl=0x1267BC、槽 [0x126A90]） | 0x60719a6..0x60719ee；guard b[0x1265C0+4673] |
| boot 期 ADC initcall + 5 场景 mic 标志 | 0x602ea68（表项 @0x60c32b0）；标志 b[0x12657C/80/82/84/88]，读取器 0x60755b2 |
| mic_effect/pc_mic 源节点（0xD06D plug create） | plug 表 0x60a917c[1]；create 0x6032924；节点 cfg→ch_map 0x6032b40..0x6032c7c |
| esco 前端（16k、每通道 512B、交织 ch×0x600、handler 0x6031D5A） | 0x60200b6..0x6020340（调用者 0x60212bc）；esco 变体 0x60142f0..0x6014460（2×9B 描述符 QCY+3469/3478） |
| mic_effect 图 3×ADC 源节点 [镜像] | stream.bin offset 0x2d0（`6d d0 03 00`）；config 89 → QCY+524..531（值/使能各 4 字节，0x602eb2a、0x6032ba0 按 9B 槽步长填 +17/+26/+35/+44） |
| ANC 复用共享 hdl + mic_type[4] 角色 | SDK audio_anc.c L936-939/L1068-1070/L2481-2517（reference-cache 副本）；audio_anc_t 布局 [DWARF]（mic_type@+104、coeff 指针族 +608..+1024、mic_param@+2464） |
| JL_ANC=0xFE0400 / JL_ASS=0xFE0900 / ADC 组=0xFE0538.. | br28.h（sha `111e29ea…` 与 dependencies 钉死值一致）；anc_core.c.o IR 37×0xFE0400 |
| 3mic_coeff.bin 资源名 + config 默认内嵌 | 字符串 @0x60a2603；默认 blob @0x60a25e0（构造器 0x6004510/0x6004566） |
| 采样率表/SR 码、帧=ch×(2|4)B、handler 尾部 SRN/PNS/CON|=0x40 | SDK adc_core.c/audio_adc.c IR（media.a pinned SHA，`2d 9c` 场景读取器 0x60755b2 互证） |

参考文件身份：app.bin sha `18f3d573…`（官方 UFW 65cafc7f 逐层解包，JLFS/嵌套 CRC 全过）；
media.a = 主仓 tools/reference-cache 既有 pin；br28.h / audio_anc.h / audio_anc_coeff.h /
audio_link.h / audio_pdm.h / audio_anc_common.h / board_ac701n_demo_cfg.h 按 SDK commit
`67328f4` 下载（audio_anc.h 与 reference-cache 缓存副本 sha 相同 `169ddc88…`）；私有
earphone_sdk 的 asm/anc.h / asm/adc.h 不在公开仓库（仅 DWARF 文件名引用），布局以 [DWARF]
提取为准。
