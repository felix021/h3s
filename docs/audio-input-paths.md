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
worktree 基线 b3d7e13。**路 AL（sim-anccfg，2026-11-14）增补 §1.6/§2.2b 并勘误 §1.3/§5：
mic_type 偏移、枚举值、写点、通道映射与 ANC 模式切换链全部静态钉死**（脚本在本地
~/tmp/al-anccfg，复用 AE 的 pi32v2-isa 钉死 codec 与同一 app.bin）。
**路 AP（sim-escocfg，2026-11-14）增补 §1.7 并勘误 §1.3/§5/§6：通话 esco_adc 40B blob
定位于公开 stream.bin、mult 决策表与通话链全图静态钉死**（脚本在本地 ~/tmp/ap-escocfg，
同一 codec/app.bin + 公开 stream.bin）。

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
无 IRQ 注册、无直接寄存器引用）——最可信分配见 §1.5 并由 §1.6 修正：**ANC 实际 3 个物理
mic 输入**（mic_type=[A_MIC1,A_MIC2,A_MIC0,A_MIC0]，4 滤波角色、右耳 FF/FB 共用 A_MIC0，
与 3mic_coeff.bin 互证）+ 2 通话 + 其余（VAD/辅听），通话与 ANC 按 SDK 复用机制共享通道。
GPADC 慢速通道（0xFD3100 CON/0xFD3104 RES，VBAT/VTEMP/
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
| **通话 esco/aec 前端** | **0x60200b6**（调用者 0x60212bc，BT esco 域）：4 slot 使能+增益数组驱动，每通道 zalloc 512B（[obj+60..]）+ 交织大缓冲 = 通道数×0x600（[obj+72]），SR=16000、set_buffer(大缓冲,512,3)，output handler 注册 [obj+40]=0x6031D5A | 4 slot 框架 [静态]；另有 esco ADC 变体 0x60142f0..0x6014460（**4×9B mic 描述符** @QCY+3464+9k、mask 硬编码 2 只开 desc1，b[QCY+3464]==2 门控，调用者 0x60221e2/0x6042384）——全链已由路 AP §1.7 钉死（esco_adc 40B blob @stream.bin 0x1BFD、en_map=0xB、mult 决策表、开/关链 0x60200b6/0x601e436） |
| **ANC（不经 jlstream）** | SDK audio_anc.c：`anc_hdl->param.adc_set_buffs_cb = audio_adc_fixed_digital_set_buffs; audio_anc_adc_ch_set()`（L1068-1070）→ **复用同一 audio_adc_hdl**；ANC 滤波在 JL_ANC 硬件块（0xFE0400..0xFE04C0，anc_core 驱动） | audio_anc_t（[DWARF] 全布局提取）：**mic_type[4]@+104bits=字节+13，角色=LFF/LFB/RFF/RFB**（SDK audio_anc.c L936-939：mic_type[0..3]=ANCL_FF/FB、ANCR_FF/FB_MIC），4 个使能位 lff/lfb/rff/rfb_en@+56..80bits=字节+7..10（AL 勘误：DWARF 偏移为位；H3S 实值见 §1.6） |
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
2 通话 + 2 其他——**ANC 实际 mic 数已由路 AL 钉死 = 3 个模拟输入**（§1.6：mic_type
=[A_MIC1, A_MIC2, A_MIC0, A_MIC0]，4 滤波角色落在 3 个物理输入上，RFF/RFB 共用 A_MIC0；
上表「ANC 前馈/反馈每耳 1 = 4」的 [推断] 就此修正）。

### 1.6 ANC 采集配置实证：mic_type 写点、枚举与通道映射（路 AL / sim-anccfg，2026-11-14 静态）

本节回答 §5.1 第 1 条（mic_type[4] 常量写点未定位）与 d1-acceptance §6.2 D2-b「ANC 采集」
场景的实证缺口。全部 [静态]（反汇编锚定，官方 app.bin sha `18f3d573…` 真字节；一次性脚本在
本地 tmp，不进库），derived-not-measured。**勘误**：本页此前把 DWARF 的 mic_type 偏移
104（**位**）当成字节——audio_anc_t 内 mic_type 实际在**字节偏移 +13**（§1.3 的
「mic_type[4]@+104」应读作 param+13..16），这也是 §5.1 扫描 b[rX+104..107] 零命中的原因。

**① 枚举值钉死 [DWARF]**：media.a `anc_core.c.o` 的 DWARF 枚举（earphone_sdk
`interface/media/cpu/br28/asm/anc.h` line 112）：`A_MIC0=0、A_MIC1=1、A_MIC2=2、
A_MIC3=3、D_MIC0..3=4..7、MIC_NULL=255`。固件侧独立互证：0x606c8aa（MIC_CTL1 源选择）
逐字节做 `v != 255 ? v : 0` 转换——255=未用的语义在 QCY 构建里同样成立。

**② anc_hdl 与 mic_type 常量写点 [静态]**：QCY 把 SDK 的 `anc_init()` 整体内联进 boot 期
initcall **0x602ea68**（AE §1.3 钉过的 ADC 子系统初始化）尾部：

- `anc_hdl = zalloc(572)` @ **0x602eeb2**（调用 0x12e16e），指针存 **[0x126954]**
  （=QCY 基 0x126760+500；`audio_anc_run` 等经 `[0x126760+500]` 取回）；
- param 基 = anc_hdl+32（audio_anc_t 内嵌偏移）；param_init 内联于 0x602f092..0x602f1f8，
  逐字段与 SDK `audio_anc_param_init` 对齐（online_busy=0@+11、dut=7@+17、
  fade_time_lvl=1@+12、debug_sel=0@+6、lff/lfb/rff/rfb_en=1@+7..10、fade_gain=16384、
  ch=3=L|R@B+144、mode=1=OFF）；
- **mic_type[4] = param+13..16 = anc_hdl+45..48**，常量存储在 **0x602f0d0/0x602f0d2/
  0x602f0d6**：`[+13]=1、[+14]=2、[+15]=0`；**[+16] 无存储指令**（原始字节核对
  0x602f0c4..0x602f0ec），保持 zalloc 默认 **0**。即
  **mic_type = [A_MIC1, A_MIC2, A_MIC0, A_MIC0]**：LFF←输入1、LFB←输入2、
  RFF←输入0、RFB←输入0（RFF/RFB 同一模拟输入；rff_en/rfb_en 均=1，ch=L|R=3）。

**③ 消费点一：mic_management [静态]**：**0x601c82e = audio_anc_mic_management**
（init 0x602f2e2 与 audio_anc_run 0x601c908 都以 param 调它）：循环 i=0..3 读
`b[param+13+i]` 依次 `==0/==1/==2/==3` 命中 **mic_param[0..3]**（基 param+292，8 字节步长
{en,type,gain,mult_flag,mic_p[4]}），置 `en=1、type=i`（角色号），并在
`b[QCY+3460]`（esco mic_en_map）对应位为 1 时置 **mult_flag=1**（通话复用标志，
SDK 同形）。按 [1,2,0,0] 求值：mp[1]={en=1,type=0(LFF)}、mp[2]={en=1,type=1(LFB)}、
mp[0] 被 i=2(RFF) 与 i=3(RFB) 先后命中 → **type 终值 3(RFB)**、mp[3](A_MIC3) en=0。

**④ 消费点二：MIC_CTL1 模拟输入选择 [静态+SDK]**：**0x606c8aa = SDK
`anc_core_mic_sel_io_init(mic_type)`**（media.a IR 逐位对齐：255→0 后
`adc0_sel=mic_type[0]&7`@bit15-17、`adc1_sel=mic_type[1]`@bit21-23、
`adc2_sel=mic_type[2]`@bit12-14、`adc3_sel=mic_type[3]`@bit18-20，写 MIC_CTL1 0xFE0908；
AE §1.1 的 p:15/21/12/18 形状即此函数）。ANC 重配链 0x606ebe0 以 **param+13（mic_type）**
调它（0x606ec24）——即 **ADC 通道 i 的模拟 mux 选择 mic_type[i] 号物理输入**：ch0 mux=输入1
（LFF 角色的 mic）、ch1 mux=输入2（LFB 的）、ch2 mux=输入0（R 侧）、ch3 mux=输入0（同 R 侧）。
（角色↔ADC 通道的最终对应还经 JL_ANC 块内部接线/滤波配置 0x606cc36/0x606d9c0，本轮未逐位
解，按 SDK 语义通道 i 承载角色 i 的数据 [SDK+静态]。）另有通用平台路径 0x606da50
（boot 0x600cd64 与 esco 0x60204c0 调用，arg=*([QCY+480]+12) 平台 mic 配置、config 59
1367B）也走同一函数——ADA 通道→物理引脚的默认表在 flash 配置区，公开工件外 [静态]。

**⑤ ANC mic 数裁决**：模拟输入只用 **A_MIC0/1/2 三个**（A_MIC3 全程未用；D_MIC 无引用
承 §1.2），4 个滤波角色（LFF/LFB/RFF/RFB 全开）落在 3 个物理 mic 上，右耳 FF/FB 共用
A_MIC0 一个元件。**「3mic_coeff.bin」的 3mic = ANC 物理 mic 数 = 3，与代码互证
[静态+镜像]**。§1.5 的 4+2+1 方案就「ANC=4」被否定；修正为 **3(ANC)+2(通话)+其余**，
通话/VAD 与 ANC 的通道复用与否仍由运行时 mult_flag/mic_en_map 决定 [静态机制，值未测]。
通道争用关系：ANC 不独立使能 ADA 数字通道——`audio_anc_adc_ch_set` 同形代码（0x602f258，
写 B+51=param+19=adc_ch）按 **b[QCY+3460]（通话 mic_en_map）** 置位、全零才默认 1，即
ANC 采样搭 esco/共享 audio_adc_hdl 已开通道的便车（与 SDK 语义一致）。

**⑥ ANC 增益/数字侧 [静态]**：`audio_anc_run`（0x601c8de）以 **B+324（=param+292，
mic_param[4]）** 调 **0x606ee98**：按 en 门把 mic_param[k].gain（+2，8 字节步长）逐个
insert 进 **ADA_CON8（0xFE0A40）4×5bit PGA 槽**（bit0/5/10/15 起），尾部
**ADC_CON（0xFE0538）|=0x800**。采样率/位宽：ANC 帧承共享 DMA 交织布局（§2.2），
ANC 专用 SR/滤波速率在 JL_ANC 块配置（0x606d9c0 param 域）与系数文件内，本轮未钉死。
（路 AP 补：QCY 构建的 0x606ee98 只收 mic_param 单参——SDK `audio_anc_mic_gain` 的
mult_gain_en 参数被裁掉，mult_flag 不再门控增益，见 §1.7②。）

## 1.7 通话（esco）采集配置实证：esco_adc 40B blob、mult 决策表与通话链全图（路 AP / sim-escocfg，2026-11-14 静态）

本节关闭 §5.5 的「esco_adc 48B 配置 blob 未定位」与「通话/ANC 复用关系未钉」两条余缺，
并勘误 §1.3 通话行两处。全部 [静态/镜像]，derived-not-measured；一次性脚本在本地
~/tmp/ap-escocfg（复用 AE 的 pi32v2-isa 钉死 codec 与同一官方 app.bin sha `18f3d573…`、
公开 stream.bin 9351B）。

**① esco_adc 配置 blob：就在公开 stream.bin 里，40 字节 [镜像+静态]**

- 查找名 = **"esco_adc"**（app.bin 字符串 @0x609D27E）。解析器 **0x6014162**（boot
  initcall 0x602eb86、通话开链 0x60143aa、aec 前端 0x602010c/0x602011a、esco 编码侧
  0x601fe4a 五处调用）先调
  **0x60750aa(mode=0, name, cfg_index=0, out8B)** 查注册表、成功再 **0x60751da(out8B,
  QCY+3460)** 整块拷贝。
- 注册表机制钉死：0x60750aa 内 fopen **"flash/res/stream.bin"**（0x6074234，VFS mount
  表 0x60C329C，4B 头非 "JS" 魔数即 plain 模式——本文件头 `00 00 a0 03`）；按 4B 节头
  {u16 type, u16 len} 扫描（b[1]&3 提供长度 18bit 进位），**type=5 即 §5 参数区**（载荷
  0x0871..0x21C2）；跳 16B 头后逐条比对 32B 记录：flag@+0（255=末尾）、mode@+4、
  **name_hash=djb2(name)&0xFFFF@+5**、cfg_index@+9；命中输出 (addr=§5 载荷基+off@+10
  (u32), size@+14 (u16))。§5 载荷基 = 0x871（经 Drc1Eff@0x1038 解出
  {bypass=0, attack=100, release=200} 与 hearing-aid §7 钉死值一致复核）。
- **命中记录 [镜像]**：stream.bin §5 记录 @0x0D41（第 38 条）= {flag=3, uuid=**0xD06D**,
  subid=2, mode=0, cfg=0, name_hash=**0xa996**=djb2("esco_adc")&0xFFFF, **off=0x138C,
  size=40**} → blob 在 stream.bin 绝对偏移 **0x871+0x138C=0x1BFD**，**40 字节**。
  §5.5 的「48B blob 公开件内未定位、疑在 flash EXT_RESERVED」就此关闭：AE 说的 48B 是
  QCY 侧 RAM 接收区（QCY+3460..3507 = en_map(4)+desc(4×9)+计数(1)+pad(3)+缓冲指针(4)，
  后 8B 由代码写，不来自 blob），blob 本体 40B 就在公开 stream.bin。
- **blob 内容**（拷到 QCY+3460..3499）：

| 偏移 | 字段 | 值 | 消费点 |
|---|---|---|---|
| +0..3 | mic_en_map word | **0x0000000B** | bit k = ADA 通道/组 k 使能（bit0/1/3）；读者见 ② |
| +4+9k (k=0..3) | desc_k（QCY+3464+9k） | 四份**完全相同** `{01 01 01 01 00 0e 08 00 00}` | 0x60142be 打包打开（③） |
| desc+0 | 类型/模式字节 | 01 | 三个 ==2 门（⑤）；打包进 hdl 每通道 cfg 低 nibble（0x606b4d0 内 `packed[3]&0xF != 2` 分支） |
| desc+1 | ADA_CON8 增益档 | 01 | 0x606de92(obj, 1<<k, gain)：ADA_CON8(0xFE0A40) 5bit 槽，**cap 19** |
| desc+2 | ADA_CON4..7 bit0 | 01 | 0x606c3aa(1<<k, v)：每通道寄存器(0xFE0A30/34/38/3C) bit0 = (v!=0) |
| desc+3 | cfg 字节 | 01 | 打包 packed[0] → adc_mic_open 存 hdl 每通道 cfg |
| desc+4 | 处理门 | 00 | 非 0 则跳过 desc+7/+8 的 16bit 处理（0x60142fc） |
| desc+5 | **mic 偏置档** | **0x0e(14)** | boot 校准回写目标（⑤）；config-89 合并目标（下） |
| desc+6 | cfg 高 nibble | 08 | packed[3]>>4 → hdl+48+ch（type!=2 时） |
| desc+7/+8 | u16 | 0 | 非 0 且 desc+4==0 时经 0x6001052→0x6085126(x>>4, 1<<(x&0xF))（语义未解） |

- config-89 合并 [静态]：0x6014162 内 0x60141d6..0x60141fa 把 QCY+524..527（值）按
  QCY+528..531（使能）逐槽写 **desc_k+5（QCY+3469+9k，偏置档）**——config 89 存在时
  覆盖 blob 的 0x0e（boot 0x602eb2a 读 config 89，成功则 b[QCY+528]=1）。
- **同族 40B 模板 {u32 en_map; mic_desc[4]}** [镜像]：uuid 0xD06D 共 3 条记录——
  "esco_adc"(a996, subid2) 与**未还原名**(a631, subid13) 共享同一 blob（同 off 0x138C）；
  **"MicEff"**(c6af, subid3, djb2 复核) 独立 blob @abs 0x1DA4：**en_map=0x1（辅听单路，
  与 D2-b「辅听/VAD 采集」行互证）**、desc+1=0x0a(10) 增益档、desc+4={0x10,0x02,0x04,0x08}
  位掩码族。第三处复用 = mic_effect 节点侧：0x6032b58/0x6032b64 以节点注册名查同一注册表
  拷到 nodecfg+8（同构合并 0x6032ba0，AE §1.3 已钉的 config-89 槽位）。

**② mic_en_map 写入者/读者全清单 + mult_flag 决策表 [静态]**

- **写入者**：QCY+3464..3468（desc+0..+4）唯一写入 = 上述 blob 拷贝
  （0x601419a→0x60751da）；desc+5（偏置）另有 boot 校准回写 0x602ee00/0x602ee10（⑤）与
  config-89 合并 0x60141f2。en_map 字（QCY+3460..3463）除 blob 拷贝外还有一个**条件恢复
  点**：0x6016be0（调用点 0x6016c2a/0x601dd96/0x601dda8/0x601ddb0，esco/aec 域）在
  [QCY+616] 的 28B 缓存对象存在且其 +3 字节为 0 时，把 b[obj+2]（boot 期 0x602f338 由
  getter 0x6020042 读入的 en_map 缓存）写回 [QCY+3460] 字——运行时只会「复位回 blob 值」
  形状，不是自由改写 [静态]。
- 读者（非穷尽）：mic_management 0x601c82e（mult_flag 置位）、ANC adc_ch 0x602f258、
  boot esco 块 0x602ec5a、aec 前端 getter 0x6020042、config-resolve 循环 0x60141b8
  （按位 0x606de62 把通道号登进 hdl+44 通道表，hdl+88 计数）、0x600c2c6（模式分发器）、
  0x602f708 等。
- **mic_management（0x601c82e）全文**：i=0..3 读 mic_type[i]=b[param+13+i]，值 v∈{0..3}
  → mic_param[v]（param+292+8v，{en,type,gain,mult_flag,mic_p[4]}，SDK
  `audio_adc_mic_mana_t` [DWARF]）置 en=1/type=i，且 **en_map bit v=1 时 mult_flag=1**；
  0x601c7fe(param,v,i) 另填 gain：源 = b[param+{139,140,240,241}[i]]（按**角色 i** 选
  LFF/LFB→+139/+140、RFF/RFB→+240/+241 的增益配置字节），写 mic_param[v].gain(+2)
  （param_init 未给这些字节赋值，默认 0 [静态]）。
- **通道分配决策表**（代入 H3S 实值：mic_type=[A_MIC1,A_MIC2,A_MIC0,A_MIC0]（§1.6）×
  en_map=0x0B（①））：

| ADA 通道 | ANC 角色（mic_param） | mult_flag | esco 组（en_map bit） | 裁决 |
|---|---|---|---|---|
| ch0 (A_MIC0) | mp[0]，type=RFB（i=2/3 先后命中，右耳 FF/FB 共用） | **1** | 组 0 | **通话/ANC 共用** |
| ch1 (A_MIC1) | mp[1]，type=LFF | **1** | 组 1 | **通话/ANC 共用** |
| ch2 (A_MIC2) | mp[2]，type=LFB | 0 | — | ANC 专用 |
| ch3 (A_MIC3) | en=0（不参与 ANC） | — | 组 3 | esco 专用 |

- **mult_flag 的消费面与 SDK 差异** [SDK+静态]：SDK `audio_anc_mic_gain(mic_param,
  mult_gain_en)`（media.a IR）在 mult_gain_en=1 时只给 mult_flag=1 的 mic 施益；**QCY
  构建的 0x606ee98 只收单参**（两调用点 0x601c90e/0x6022932），无条件给全部 en=1 的 mic
  施益 → **增益路径不读 mult_flag（增益不打折）**；但 mult_flag 并非死数据：**关链清位
  路径读它**——0x606c3e8（调用点 0x600dc24，ANC 模式写入族 0x600db3c 内，以
  param+292/adc_ch 注册）置 b[QCY+1228]=1 并存 [QCY+310<<2]=[QCY+1240]=mic_param 后，
  0x606f8b6 尾部（0x606f8f6..0x606f932）读 mic_param[0..3].mult_flag，把 mult 通道位从
  待清的 hdl 通道使能位中**剔除**再调 0x606f432——即 esco 关闭时 mult 通道的注册位保留
  （ANC 继续占用），非 mult 通道被清。实际执行复用的是另外两个机制：(a) **ANC adc_ch** 0x602f258——en_map bit0/1/2 任一 → 置 anc_hdl+51（=param+19）
  位，bit3 再置一位，全零默认 1：ANC 声明「搭便车」的数字通道位集合；(b) **共享
  audio_adc_hdl（内嵌结构 @QCY+4596=0x1277B4）的每通道引用计数 b[hdl+79]**：
  0x606b4d0 adc_mic_open 每路 +1、0x606f8b6 adc_mic_close 每路 −1，**归零才 0x606f3c4
  digital_close**（ADC_CON&=~0x20+ADA 位段清）——esco 开/关决定硬件通道生命周期，ANC
  只声明不计数。
- 通话↔ANC 联动点 [静态]：esco 事件分发器（0x6020d14 域，tbh 13 case @0x602126a）内直接
  调 audio_anc_run(QCY+8164, **1/2**)（0x6020cb0/0x6020cc6）——通话状态机驱动 ANC off/on。

**③ 通话采集链全图（开→采→关）[静态]**

```
BT esco 事件 → 分发器 0x6020d14 域（case 内 r5 = flags）
开: 0x60200b6(flags)（调用点 0x60212bc）
  ├ [QCY+620] 已有 → 0x601e436 先关（重开）
  ├ zalloc(76) → [QCY+620]；h[obj+12]=flags；flags&0x10 → 0x601f856(str,16000,0)+0x601dab2(3,0)
  ├ 0x6014162 配置解析（lookup "esco_adc"→blob→QCY+3460；config-89 合并；
  │   使能位数计数 → b[QCY+3500]；大缓冲 zalloc → [QCY+3504]）
  ├ en_map=b[QCY+3460]（getter 0x6020042）；chid[k]=b[QCY+3465+9k]=desc_k+1（增益档）
  ├ slot 使能 = en_map 位（仅 flags 高 nibble 非零时默认填充 0x6020138..156）∪ flags
  │   bit0/1/2/3 → 0x6020064(idx=1/2/0/3, mask) 强制使能（chid 空 → 取
  │   b[[QCY+500]anc_hdl+326+8k]=mic_param[k].gain 兜底）
  ├ 逐使能 slot k：b[[QCY+620]+2] 通道计数++ → 0x60142be(aecobj+32, 1<<k)：
  │     desc_k 打包（0x6014298）→ adc_mic_open 0x606b4d0(obj,bit,hdl,packed4B，hdl+79++)
  │     + ADA_CON8 档 0x606de92 + ADA_CON4..7 bit0 0x606c3aa
  │   → 0x606de92(aecobj+32, 1<<k, chid[k])
  ├ 每通道 zalloc(512) → [obj+60+4k]；大缓冲 zalloc(count×0x600) → [obj+72]
  ├ 0x606dee0(obj+32, 16000) → set_buffer 0x606dee4([obj+72], 512, 3) → [obj+28]=hdl
  │   → 挂 output-handler 链 0x606df3a(obj+16, hdl)（hdl+92 链表）→ 0x606df66 启动；返回 1
采: IRQ68 → 分发器 0x602f406 → audio_adc_irq_handler 0x6072628 → output handler
    [obj+40]=0x6031D5A（AE 钉）→ aec；slot 增益另有运行时名二查（0x607550c 取 16B 名 →
    0x60750aa → 0x60751da 拷 3B 到 QCY+6496，经 0x6020052 log2 → 32/64/128 档）
关: 0x601e436（调用点 0x60200c8 重开前 / 0x6021144 关闭 case）
  ├ flags&0x10 → 0x601e24e 子关
  ├ 0x606f8b6(obj+32)：每使能位 hdl+79−− → **归零才 0x606f3c4 digital_close**；
  │   0x606f432 清通道位时**剔除 mult_flag 通道位**（b[QCY+1228] 门 +
  │   [QCY+1240]=mic_param，见②——mult 通道注册位保留给 ANC）
  ├ 0x606f954(hdl, obj+16) 摘链 → 逐通道 free 512B → 大缓冲仅清 [obj+72] 指针（本函数
  │   无 free 调用 [静态]）→ free obj → [QCY+620]=0
```
停-启形状对照 ANC 三段式（§2.2b：0x606e9e8(0)→0x606ebe0→0x606e9e8(1) 同一 handle
重配）：通话侧是**对象生命周期式**（建对象→挂链→计数→拆链→计数→归零才断电），两域经
共享 hdl+79 计数器耦合：通话先开则 ANC 搭通道便车；通话全关且计数归零 → ADC 数字侧整体
下电（ANC 若在跑将失采——静态可见耦合点，运行表现未测 [推断]）。
通话「2ch」的构成 [静态形状+推断]：en_map=0x0B 提供组 {0,1,3}；aec 前端 flags bit0/1/2/3
经 0x6020064 重映射到 **slot {1,2,0,3}**（bit0→slot1、bit1→slot2、bit2→slot0、bit3→
slot3），且 flags 高 nibble 为 0 时 en_map 默认填充被跳过（0x6020138）——实际开的组集合
由 flags 运行值决定：flags=0x3 → 组 {1,2}（ch1+ch2）、flags=0x5 → 组 {0,1}（ch0+ch1，
即与 ANC 共用的两路）；flags 含高 nibble 位（如 &0x10 ENC 位）时 en_map 全集默认使能，
可开到 3-4 路。flags 各通话 case 的实际取值未逐 case 展开，待 D2-b 运行观测。

**④ esco ADC 变体 0x6014364（§1.3「esco 变体」行的定位修正）[静态]**

一次性 guard **b[QCY+4788]**（只置 1，静态扫描无清零点）→ 0x6013b10(4)/0x606dcd6/
0x606dd96（QCY+7464）→ zalloc(2128) → [QCY+504] → 0x6014162 → **0x60142be(obj+20,
mask=2 硬编码)**——只开 **desc1 一个 mic 组**（与 aec 前端按 en_map/flags 多路不同）→
set_buffer(**obj+76**, 512, **2**)（0x60143ce `add.u6_hi` 编码，与 boot 块 0x602ed1c
的 `add.u5_hi`(+44) 是两条不同指令，非笔误）→ 0x606df3a/0x606df66（0x6064efc(500) 重试
环）。调用者
0x60221e2（BT 通话事件状态机）与 0x6042384（表驱动 dispatcher，门 [QCY+373<<2]）——
「进入通话」的另一条打开路径；无对应关闭函数（[推断] 开一次即驻留）。

**⑤ b[QCY+3464]（=desc0+0）==2 三门与 boot 校准块 [静态]**

- 三处 ==2 门：0x60122b6（app-init，initcall 表 walker 后）、0x602ec76（boot ADC
  initcall 的 esco 块入口）、0x601fe5a（esco 编码侧函数，==2 → 0x601e91a(sample_rate)
  子初始化）。**公开镜像 blob 值 = 1 → 三门全不开**（mode 2 疑为出厂/校准配置档
  [推断]）。
- boot 的 mode-2 块（0x602ec7c..0x602ee94）=「开→自校准→关」：zalloc(20)@QCY+508
  （b[+2]/b[+3] = desc0+5/desc1+5 初值；r11 = h[0x60A180+2300+config91 值×2] 换算表）→
  开 1068B 对象 @QCY+516（0x60142be(obj+16, en_map) + set_buffer(obj+44,512,2) + 启动）→
  模拟侧上电（0xFD5008 端口族 5 字 + DAA_CON0(0xFE0A00)|=0x200）→ **偏置自校准环
  0x602edba..0x602ede8**：按表 b[0x60A180+2876+i] 逐档把偏置值 insert 进 ADA_CON1
  （0xFE0A24）bit6..12，每档延时 10ms 并经 0x6011dd2 轮询 GPADC 通道 0x20000 电压
  ≥1500，超 18 档退出 → **回写校准结果 b[QCY+3469]/b[QCY+3478] = 偏置档**（gain=
  电压×8196/r11 → h[0x124C92/0x124C94]（=h[0x124C80+18/+20]，r9=0x124C80@0x602eb4a））→
  0x6001f12(89, QCY+3469, 1) 把校准偏置**写回
  config 89** 持久化 → 关 1068B 对象（0x602ee36..0x602ee5c = 0x606f8b6/0x606f954/free，
  §2.2b 引用的「通话侧关」即此块，属 boot 校准而非运行时通话关断——运行时关断见 ③
  0x601e436）。**公开镜像（mode=1）不走此块**：desc+5 偏置保持 blob 值 0x0e，无 boot
  校准、无 config-89 回写。

**勘误（本路修正 §1.3/§6 既有条目）**：(1) §1.3 通话行「组内通道 id 在描述符内」——
desc+1 实为 **ADA_CON8 增益档**（0x606de92 消费，cap 19；0x606c3aa 写的是 ADA_CON4..7
bit0，不是 ADA_CON8——§1.1 该句一并修正），通道号即 en_map 位号本身；(2) §5.5
「48B blob/EXT_RESERVED」如上关闭；(3) 绝对地址笔误修正：audio_adc_hdl = QCY+4596 =
**0x1277B4**（§1.3 原文 0x1267BC 差 0x1000）；anc_hdl 指针槽 [QCY+500] = **0x1267B4**
（§1.6 原文 0x126954 同源笔误，基址实为 0x1265C0=立即数 1205696）；相对偏移与调用点
锚点两路结论不受影响。

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

### 2.2b ANC on/off/通透的模式切换与重配代码点（路 AL，D2-b「配置切换」场景锚点）[静态]

「配置切换」场景（d1-acceptance §6.2）需要的「旧配置停止→新配置生效」代码点，ANC 域全链
[静态]（内部模式枚举 off=1/on=2/trans=3，与 evidence/anc-cycle 实测互证；param_init 的
`mode_enable|=0xE` 即 1..3 三模式位）：

```
入口（三源）：
  按键: anc_next 0x6018b60（循环表 0x609e510=[1,3,2] off→trans→on；键 handler 0x6018bf0/0x6018ce8）
  BT 命令: 0x60235de / 0x602548c 调 anc_next；ANC 工具链 0x606e576
    ↓ os_taskq_post_msg("anc", msg)（0x6064d72；键/tone 侧封装 0x600db3c，msg 161..186 见下）
anc_task = 0x602d3a0（task_create 于 0x602f30a，名 "anc" 0x60a1906）：
  pend 0x6064c8c（16 字节 msg）→ 取 [msg+4]，tbh 跳表 @0x602d3de 分发 msg 161..186
  （161→0x602d4b4、163→0x602d4d6、167→0x602d4f6、170→0x602d4fe、171/172→0x602d416、
   182→0x602d51c、183→0x602d57e、184→0x602d5c0、185→0x602d5c8；未列出=回环忽略）
  msg 171/172（模式切换）@0x602d416：按 data=b[msg+8]（0..15）子跳表 @0x602d432 选
  模式×提示音组合处理器（0x602d454/0x602d72e/0x602d77e/…）
    ↓ 模式应用
audio_anc_run = 0x601c8de(mode, r1)：
  b[anc_hdl+170] 增益 → 0x606b440；mic_management 0x601c82e(param)（§1.6③，重算
  mic_param en/type/mult_flag）；ANC PGA → 0x606ee98(mic_param)（ADA_CON8 4×5bit +
  ADC_CON|=0x800）；非 off(≠1) 时播模式提示音 0x600bcbe(h[B+10])；
  【停止→重配→重启核心】0x606ee26(param, 0)：
    0x606e9e8(0)（JL_ANC CON 0xFE0400 bit1 清 = 停）
    → 0x606ebe0(param) = anc_open/重配主体：
        重注册 IRQ67(0x606ec02, prio0, handler 0x6073d7e)
        → JL_ASS 时钟 0x606c844(param)
        → MIC_CTL1 模拟 mux 0x606c8aa(param+13 = mic_type)（§1.6④）
        → JL_ANC 滤波块 0x606cc36/0x606ce02/0x606d9c0(param)
          （按 ch=3 的 L/R 位、lff/lfb_en、mode∈{2,4,5,3,0xA1} 分支配滤波器）
        → 64KB DMA 缓冲 zalloc（[param+64]、0x606ecc6/0x606ed46）
    → 0x606e9e8(1)（JL_ANC bit1 置 = 启）
  → 0x606e7c2(param, r1, 0)：按 b[QCY+1276] 门重读系数文件（"flash/res/3mic_coeff.bin"）
```

配套的**采集侧重配**（谁在切换时动 ADA/DMA）：esco 开 0x6014364..0x60143f6（描述符→
0x606b4d0/0x606de92/0x606c3aa→set_buffer 五写）与关 0x602ee36..0x602ee5c（free esco 对象）
在模式进出通话时跑；boot 期同链在 0x602ec7c..0x602ed44。ANC 自身不重开 ADA 数字通道
（§1.6⑤）。D2-b「配置切换」断言的可观测点即上述 0x606e9e8(0)→0x606ebe0→0x606e9e8(1)
序列 + set_buffer 五写（AE §2.1）+ IRQ67 重注册。

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

1. ~~**ANC mic_type[4] 的常量值**~~ **已由路 AL 钉死（§1.6）**：写点 0x602f0d0（[+16] zalloc
   默认 0），值 [A_MIC1, A_MIC2, A_MIC0, A_MIC0]，枚举 A_MIC0..3=0..3/MIC_NULL=255。
   原「b[rX+104..107] 零命中」系把 DWARF 位偏移当字节偏移（勘误见 §1.6）。残余未决：
   mic_type[3] 的 0 是「源码即省略第 4 赋值」还是「赋了 A_MIC0 被优化」无法从字节区分，
   运行效果等同（RFF/RFB 同输入）。
2. **3mic_coeff.bin / ANCIF(0x1fe000) 布局未解**：系数文件内容是 float 流，分组格式需
   anc_db 消费语义或真机读取；「3mic」语义已由 §1.6 代码侧互证（=3 物理 mic）。ANCIF
   分区在公开工件外（hearing-aid §5.2 口径不变）。
3. **ADA_CON1 位段→物理 mic 引脚（ain_sel）映射**与 config 89 默认 blob（@0x60a25e0 25B）
   的逐字段语义：只有消费代码形状，无默认值解析。路 AL 补充：平台侧 ada 通道→引脚默认表
   = config 59（1367B，经 *([QCY+480]+12) 的 +13 数组喂 0x606c8aa），同样在公开工件外。
4. **DMIC 未用结论的强度**：零 IRQ69/70 注册 + 零 0xFE06xx/0xFE07xx li 引用，但不排除
   变址访问；如后续场景撞上 PLNK 寄存器按本路地址表处理。
5. ~~esco 2 mic 描述符与 ANC 3 输入的复用关系；esco_adc 48B blob 未定位~~ **已由路 AP
   钉死（§1.7）**：blob 在公开 stream.bin §5 @abs 0x1BFD（40B，查找名 "esco_adc"），
   en_map=0x0B、desc 4×9B；复用决策表（ch0/ch1 共用、ch2 ANC 专用、ch3 esco 专用）+
   mult_flag 在 QCY 构建只写不读的结论见 §1.7②。残余未决：①b[QCY+3464]==2 的模式语义
   仅有三门+boot 校准块的形状（§1.7⑤），「出厂/校准档」是 [推断]；②desc+7/+8 的 16bit
   字段经 0x6001052→0x6085126(x>>4, 1<<(x&0xF)) 的消费语义未解（blob 值 0，公开镜像
   不触发）；③subid13 记录的查找名（hash a631）未还原；④aec 前端 flags 参数在各通话
   case 下的具体取值（0x60212bc 上游 r5 来源链）未逐 case 展开——通话 2ch=flags 0x3 是
   按位运算形状的 [推断]，运行值待 D2-b 场景观测。
6. 0x602ea68 所在 initcall 段（0x60c32a4..0x60c32b4）的 walker 边界：CI 钉的三段表不含它，
   该段执行面按 initcall 区属性 [推断]（boot 期）；如需精确触发序号是 D2-b1 场景观测项。
   路 AL 动态复核接口：ANC init 若已跑，`anc_hdl=*([QCY+500])`（=*(0x1267B4)，§1.7 勘误
   后的绝对地址），mic_type=b[anc_hdl+45..48]
   应为 {1,2,0,0}——供后续场景在 initcall 完成后的 frontier 快照读取互证（本轮未跑仿真，
   静态结论 derived-not-measured）。路 AP 补充动态复核接口：boot initcall 0x602ea68 完成后
   b[0x127344..0x127373]（=QCY+3460..3507）应为 {0B 00 00 00 | 01×4 00 0e 08 00 00 ×4}（blob
   拷贝 + config-89 合并后的形状，mode=1 不校准）；b[0x1265C0+4673]=1（audio_adc 已 init）。

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
| esco 前端（16k、每通道 512B、交织 ch×0x600、handler 0x6031D5A） | 0x60200b6..0x6020340（调用者 0x60212bc）；esco 变体 0x60142f0..0x6014460（4×9B 描述符 QCY+3464+9k，[AP] §1.7 全图） |
| mic_effect 图 3×ADC 源节点 [镜像] | stream.bin offset 0x2d0（`6d d0 03 00`）；config 89 → QCY+524..531（值/使能各 4 字节，0x602eb2a、0x6032ba0 按 9B 槽步长填 +17/+26/+35/+44） |
| ANC 复用共享 hdl + mic_type[4] 角色 | SDK audio_anc.c L936-939/L1068-1070/L2481-2517（reference-cache 副本）；audio_anc_t 布局 [DWARF]（mic_type@+104**bits**=字节+13、coeff 指针族 +608..+1024、mic_param@+2464） |
| **[AL] mic_type 常量写点**（=[A_MIC1,A_MIC2,A_MIC0,A_MIC0]） | 0x602f0d0/d2/d6（[+16] 无存储=zalloc 0）；anc_hdl=zalloc(572)@0x602eeb2→[0x126954]；param 基=anc_hdl+32 |
| **[AL] mic 枚举** A_MIC0..3=0..3、D_MIC0..3=4..7、MIC_NULL=255 | anc_core.c.o DWARF（earphone_sdk asm/anc.h:112）；固件互证 0x606c8aa 的 `!=255→0` |
| **[AL] audio_anc_mic_management**（mic_type→mic_param，mult_flag） | 0x601c82e（读 b[param+13+i]、==0..3→mp[param+292+8k]，en/type/gain/mult_flag） |
| **[AL] anc_core_mic_sel_io_init**（MIC_CTL1 adc{i}_sel=mic_type[i]） | 0x606c8aa（p:15/21/12/18）；ANC 调用点 0x606ec24（arg=param+13）；平台路径 0x606da50（boot 0x600cd64、esco 0x60204c0，arg=*([QCY+480]+12)） |
| **[AL] ANC PGA/数字使能** | 0x606ee98(mic_param=B+324)：ADA_CON8 4×5bit insert + ADC_CON\|=0x800 |
| **[AL] ANC adc_ch（数字通道位）按通话 mic_en_map** | 0x602f258..0x602f2a4（b[QCY+3460] 位测试→B+51=param+19；全零默认 1） |
| **[AP] esco_adc blob**（40B @stream.bin abs 0x1BFD，en_map=0x0B、4×同构 desc） | 查找名 "esco_adc"@0x609D27E；§5 记录 @0x0D41（uuid 0xD06D, subid2, hash a996, off 0x138C, size 40）；解析器 0x6014162（调用点 0x602eb86/0x60143aa/0x602010c/0x602011a）；注册表 fopen "flash/res/stream.bin" 0x6074234 + djb2 查找 0x60750aa + 拷贝 0x60751da；§5 载荷基 0x871（Drc1Eff@0x1038 复核）；同族 MicEff blob @0x1DA4（en_map=0x1） |
| **[AP] mic_en_map/desc 写入面** | desc+0..+4 唯一写 = blob 拷贝 0x601419a；en_map 字另有条件恢复点 0x6016be0（缓存 [QCY+616]+2 → [QCY+3460]，boot 0x602f338 填缓存）；desc+5 = 校准回写 0x602ee00/10 + config-89 合并 0x60141f2；读者（非穷尽）0x601c82e/0x602f258/0x602ec5a/0x6020042/0x60141b8/0x600c2c6/0x602f708 |
| **[AP] mic_management 多路复用决策** | 0x601c82e（mult_flag=mic_en_map bit v；gain 填充 0x601c7fe）；QCY 0x606ee98 单参化（SDK audio_anc_mic_gain 的 mult_gain_en 被裁，增益路径不读 mult_flag）；mult_flag 真实消费在关链清位 0x606f8f6..0x606f932（mult 通道位从 0x606f432 清位掩码剔除，注册位保留给 ANC；注册 = 0x606c3e8 ← 0x600dc24 ANC 模式写入族）；实际复用=ANC adc_ch 0x602f258 + hdl+79 每通道引用计数（0x606b4d0 +1 / 0x606f8b6 −1 / 归零 0x606f3c4 digital_close） |
| **[AP] 通话 aec 前端开/关链** | 开 0x60200b6（0x6014162→slot 使能 0x6020064→逐路 0x60142be+0x606de92→512B×ch+ch×0x600→SR16000/set_buffer(,512,3)→挂链 0x606df3a→0x606df66）；关 0x601e436（调用点 0x60200c8/0x6021144）；分发器 0x6020d14 域 tbh@0x602126a（含 audio_anc_run(1/2) 联动 0x6020cb0/0x6020cc6） |
| **[AP] esco ADC 变体（一次性，mask=2 只开 desc1）** | 0x6014364（guard b[QCY+4788] 无清零；zalloc 2128→[QCY+504]；set_buffer(obj+76,512,2)@0x60143ce）；调用者 0x60221e2/0x6042384 |
| **[AP] desc0+0==2 三门 + boot 偏置自校准块** | 门 0x60122b6/0x602ec76/0x601fe5a（公开镜像 blob=1 全不开）；校准块 0x602ec7c..0x602ee94（偏置表 0x60A180+2876、ADA_CON1 bit6..12、GPADC 0x20000≥1500、回写 0x602ee00/0x602ee10、config-89 持久化 0x6001f12(89,QCY+3469,1)）；关块 0x602ee36..0x602ee5c |
| **[AP] QCY 基址勘误** | QCY=0x1265C0（立即数 1205696）；audio_adc_hdl=QCY+4596=**0x1277B4**（原 0x1267BC 差 0x1000）；anc_hdl 槽 [QCY+500]=**0x1267B4**（原 0x126954 同源笔误） |
| JL_ANC=0xFE0400 / JL_ASS=0xFE0900 / ADC 组=0xFE0538.. | br28.h（sha `111e29ea…` 与 dependencies 钉死值一致）；anc_core.c.o IR 37×0xFE0400 |
| 3mic_coeff.bin 资源名 + config 默认内嵌 | 字符串 @0x60a2603；默认 blob @0x60a25e0（构造器 0x6004510/0x6004566） |
| 采样率表/SR 码、帧=ch×(2|4)B、handler 尾部 SRN/PNS/CON|=0x40 | SDK adc_core.c/audio_adc.c IR（media.a pinned SHA，`2d 9c` 场景读取器 0x60755b2 互证） |

参考文件身份：app.bin sha `18f3d573…`（官方 UFW 65cafc7f 逐层解包，JLFS/嵌套 CRC 全过）；
media.a = 主仓 tools/reference-cache 既有 pin；br28.h / audio_anc.h / audio_anc_coeff.h /
audio_link.h / audio_pdm.h / audio_anc_common.h / board_ac701n_demo_cfg.h 按 SDK commit
`67328f4` 下载（audio_anc.h 与 reference-cache 缓存副本 sha 相同 `169ddc88…`）；私有
earphone_sdk 的 asm/anc.h / asm/adc.h 不在公开仓库（仅 DWARF 文件名引用），布局以 [DWARF]
提取为准。
