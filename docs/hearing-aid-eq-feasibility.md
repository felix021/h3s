# 助听器弱化版 EQ（R2）可行性证据包（路 CJ / sim-hearaid，只读调研）

2026-11-13。本页服务 R2 需求「更大范围参数化 EQ 作为弱化版助听器，支持限制最大音量
（例 1kHz 增益 39dB、整机最大输出限 ~100dB SPL）」，并按用户 13:50 补充「EQ 必须与
通透模式（transparency）搭配——助听器放大的是环境声，即通透模式的 mic→DAC 通路」重新
聚焦：**通透通路结构 → 该通路上的 EQ 插槽 → WDRC 参数化程度 → 音量限制实现层 →
L0/L1/L2 路径分层**。**只读**：未改 tools/ 任何代码、未跑改机器状态的 probe；反汇编用
仓库钉死 codec（pi32v2-isa @3608ed1，reference-cache）+ 官方 UFW 公开字节；参考 SDK
用 bootstrap 钉死清单（media.a 及 67328f4 源码，参考材料非精确 QCY 源）；一次性脚本在
本地 tmp 不进库。私有 ROM 路径/字节不进本文。结论基于官方镜像（b79 仅键表 1 字节差，
与本路无关）。

证据强度：**[静态]** = 指令锚定反汇编/资源字节解析（本路）；**[SDK]** = 参考 SDK 源/
IR；**[镜像]** = 三镜像/资源字节比对；**[文档]** = 既有 docs/HANDOFF 钉死条目；**[推断]**
= 上述链推出。

## 0. 一句话结论

QCY 固件里「通透」和「辅听」是**两条独立通路**：ANC 键循环的「通透」是 **ANC 硬件
通路**（CVP_RefTask + ANCIF/ANCIF1 系数 + ANCGAIN 增益），**没有 EQ/WDRC 插槽**；而
SDK 已内置、QCY 已编译进的「辅听/hearing_aid」走 **jlstream 的 pipeline-mic_effect
效果链**（ADC→EQ×3→DRC→LLNS→DAC），**天然具备「宽增益多段 EQ + WDRC 输出限幅 + 音量」
的助听器标准结构**，参数区已在 stream.bin §5 逐字节钉死。R2 的合理落点是：**L0 改
stream.bin §5 的辅听链参数（零代码）**，配一个 **L1 的模式接驳**（把 ANC 键的通透档
改接到 hearing_aid_player_open）；「100dB SPL」在固件里无标定，只能做**数字域绝对上限**
（DRC 输出阈 + DAC_VL0 数字音量）。这是调研不是实施承诺。

## 1. 现有效果链的参数化程度

### 1.1 ops 表（28 条效果适配器，BT 钉死、本路复核）[静态]

`0x60a8ed0` 起 28 条 24B 记录 `{op0, op1, op2, name_ptr, uuid16, flags16}`（本路全量
解码）。与助听器直接相关的节点类型**全部已在册**：

| name | uuid | op1（控制入口） | 助听器角色 |
|---|---|---|---|
| eq | 0x737b | 0x6078594 | 多段频响整形 |
| wdrc | 0xdefe | 0x607898e | 多通道宽动态范围压缩/限幅 |
| dynamic_eq | 0x87a0 | 0x6078d0a | 动态均衡 |
| spk_eq | 0x3be6 | — | 扬声器专用 EQ |
| data_saturation | 0x6b29 | 0x607a418 | **数字域硬限幅候选**（saturation 语义） |
| auto_mute | 0x86b9 | 0x607a2a2 | 自动静音 |
| howling_supress | 0xc482 | — | 啸叫抑制 |
| volume(74e3)/DAC(dccd) | — | — | 音量/输出 |

框架侧：uuid 查找器 `0x607452e`（遍历 24B 记录比 `h[+16]`=uuid）、节点构造器
`0x6076b7a`（28B 注册表 `[0x60a917c,0x60a9240)` 7 条 plug 匹配后 zalloc+create）——L2
「新效果节点注册」的机制已钉（见 §6 L2）。

### 1.2 stream.bin 结构（公开字节全解析）[静态]

9 节 `{u16 type, u18 len}`（type 0..8,10；9351B）。两节承载参数：

- **§2 节点实例名目录**（53 条 21B 记录 `{0x13, pad, uuid16, subid, name[16]}`）：eq×11
  （MusicEqBt/EQ12-14/EscoUlEq/EscoDlEq/EQ11/EQ47/Eq4Eff/Eq5Eff/Eq1Eff）、wdrc×4
  （DRC7/8/9/Drc1Eff）、volume×9（含 VolEff）、DAC×5（含 DacEff）、d06d（ADC 源）等。
- **§5 参数区**（16B 头 + 32B 记录数组 + 参数 blob）：记录 `{u8 flag, u16 uuid, u8
  subid, u8 mode, u16 name_hash(djb2 h=h*33+c &0xFFFF), u16 f5, u8 cfg_index, u32
  offset, u16 size, resv[16]}`（node_cfg_format 布局逐字段经 IR `%struct.node_cfg_format`
  + `jlstream_read_form_node_info_base` 复核；名字经 **djb2 哈希**引用，哈希表见 §7）。
  同一节点多套配置用 cfg_index 区分（MusicEqBt 有 cfg0-3 四套）。

**EQ 参数格式**（eq_tool，本路钉死）[静态+SDK]：`{float global_gain, i32 seg_num,
eq_seg_info seg[10]}`，每段 `{u16 index, u16 iir_type, i32 freq(Hz), float gain(dB),
float q}`，共 168B。SDK `eq_config.c` 的 `eq_seg_info`/`eq_tool` 与 IR
`%struct.eq_seg_info = {i16,i16,i32,float,float}`/`%struct.eq_tool = {float,i32,[0 x
eq_seg_info]}` 完全互证。iir_type 枚举：0=high_pass/1=low_pass/2=band_pass/3=high_shelf/
4=low_shelf（eq_config.h 注释）。**增益表示是 float**；SDK 注释标 `gain(-18~18)`（见 §5.1
关于 39dB 的讨论）。

**WDRC 参数格式**（本路钉死，见 §2）。**音量格式**（volume_cfg，docs/stream-volume.md
已钉）：`<BHiBihH>` = bypass/最大档/最小 dB/表模式/最大 dB/当前档/表字节数，16B。

### 1.3 定点格式说明 [静态]

EQ/WDRC 参数区是 **float**（IEEE754 单精度，节数据里直接 `struct.unpack('<f')` 解码出
-4.00/25.00/0.70 等合理值）——stream.bin 是**在线调音工具生成的参数文件**，浮点。
运行时节点（eq_node_hdl/wdrc_node_hdl，见 IR）把它换成定点 IIR/增益系数（`spectrum_eq`
/`audio_eq` 域），但**参数区改动只需写浮点**。name_hash 用 djb2 而非字符串，改节点名
需同步重算哈希（本路不改名即可规避）。

### 1.4 「EQ 预设」参数来源（0x60059ce 路径 + config.dat）[文档+静态]

0x60059ce 是 `0x60057e6`（EQ 参数源选择 fn，20 处调用）内读 `QCY[42]` 源字节的点
（eq-payload-semantics 已钉）。源=1（自定义）走自定义应用循环 0x602510e、源=2（预设）
走预设装载 0x6025030（含 QCY[43]=预设 index）。**预设曲线数据在 stream.bin §5
MusicEqBt cfg0-3（4×168B），不在 config.dat**——config.dat（352B，本路复核）是 JCRT
板级配置（ver_info/reset_io/power_io/link_key…），与 EQ 曲线无关。所以「改预设曲线」=
改 stream.bin §5 MusicEqBt 相应 cfg 块。

## 2. WDRC 节点深挖（参数化程度）

**结论：WDRC 节点参数化充分，「1kHz +39dB + 输出限 100dB」就是一组参数而非新代码
（在辅听链上）。**

`%struct.wdrc_struct`（sw_drc.h [SDK] 与 IR `%struct.wdrc_struct` 互证）：
`{u16 attack, u16 release, threshold_group threshold[6]{float in_threshold, float
out_threshold}, float inputgain, float outputgain, float rms_time, u8 threshold_num,
u8 rms_time, u8 algorithm(0=PEAK/1=RMS), u8 mode(0=PERPOINT/1=TWOPOINT), u16 holdtime,
u8 reserved[2]}`，外加 `i32 bypass` 头，共 **76B**。语义 [SDK sw_drc.h 注释]：
`inputgain` 输入整体 dB（正放大负缩小）、`outputgain` 输出整体 dB、`threshold[].in/
out` 是**压缩拐点对（in=输入电平 dB、out=映射输出电平 dB）**——out_threshold 直接就是
**输出电平上限**（限幅阈值），threshold_num 控制有效拐点数。

QCY 实际参数（本路解码 [静态]）：

| 节点 | 位置 | attack/release | 拐点 in→out (dB) | mode/algo |
|---|---|---|---|---|
| DRC7/8/9（媒体） | §5 0xf54/0xfa0/0xfec | 5/200 | 见下 | TWOPOINT/PEAK |
| **Drc1Eff（辅听）** | §5 0x1038 | 100/200 | (0.3,90.3),(110.3,110.3)… | TWOPOINT/PEAK, tnum=3 |

Drc1Eff 拐点：(0.3→0.3)、(90.3→90.3)、(110.3→110.3)——**线性区到 110dB 后拐平**，
正是「输出限幅 ~110dB（数字域）」的现成形状，改 out_threshold 即可调限幅点。inputgain/
outputgain 均可设大 dB。**多通道压缩**（multiband_drc）SDK 也有（audio_multiband_drc.c/
multiband_drc_node.c），但 QCY ops 表 28 条里**无 multiband_drc 节点**（wdrc 是单带），
需多带则走 L2（§6）。

## 3. 通透模式的音频通路结构（本次重点，两条通路分野）

### 3.1 ANC 键循环的「通透」= ANC 硬件通路（无 EQ 插槽）[静态+SDK+文档]

- 键循环 `0x60118b60`（first-match 表 0x0609e510，anc-cycle.md 已钉）→ `0x600189aa`
  模式应用 → 内部模式写进 ANC 句柄（`[0x1265c0+500]` 指针，模式字节/增益字段见 §7）。
  TRANS（内部 3）= 通透。
- ANC 引擎是**独立任务**（字符串 `CVP_RefTask` [静态]，SDK audio_anc.c 的 anc_task），
  系数来自 **ANCIF/ANCIF1（flash+0x1fe000/0x1fe100）+ anc_ext.bin（62KB）+ ANCCOEF01/
  ANCGAIN01** 资源名（本路钉死引用点 0x606c57e/0x606e722/0x606e85e）。[SDK audio_anc.c]
  通透 = `ANC_TRANSPARENCY` 模式，前馈 FF 滤波器 feedthrough，增益 `l_transgain/
  r_transgain/trans_alogm`。
- **该通路是 ANC 核心（anc_core.c.o/audio_anc.c）处理，不经过 jlstream 效果链**：ANC 的
  频响整形由 FF/FB IIR 滤波器系数决定（等价于「固定 EQ」），输出增益由 transgain 控制，
  **没有 WDRC/limiter 节点**。改通透频响=改 ANC 系数文件（L0 参数，但格式是 ANC 核心
  专用、本路未解）。

### 3.2 mic_effect「辅听」= jlstream 效果链（EQ+DRC+音量齐备）[静态+SDK]

- SDK `mic_effect.c` 内置 `hearing_aid_*`（辅听）接口：`hearing_aid_player_open()` →
  `mic_effect_player_create(MIC_EFX_DHA)` → `jlstream_pipeline_parse(uuid=0x9c2d,
  NODE_UUID_ADC)` → `jlstream_set_scene(STREAM_SCENE_HEARING_AID)` → 线程
  `mic_effect1`/`mic_effect2`。[SDK] jlstream_event_handler.c：`get_pipeline_uuid
  ("mic_effect")` 返回 `PIPELINE_UUID_MIC_EFFECT=0x9C2D` 并 `clock_alloc("mic_effect",
  24MHz)`。
- **QCY 固件编译进了这套** [静态]：app 内 `mic_effect1`/`mic_effect2` 任务名（0x609c5f4/
  0x609c600，任务指针表 0x609d93c/0x609d948）、`mic_effect`/`CVP_RefTask` 字符串、
  0x9c2d 立即数出现在 `0x60057e6`（正是 event handler 的 get_pipeline_uuid：24MHz
  clock_alloc + r4=0x9c2d）与 `0x602ead6`（pipeline uuid 缓存到全局字节）——**辅听
  pipeline 在 QCY app 里已初始化可用**。
- pipeline-mic_effect 的图（SDK `麦克风音效.x6flow`，JSON 图定义 [SDK]）与 QCY
  stream.bin §2/§5 的 Eff 节点**对应**：`ADC(MicEff, 0xd06d) → NoiseGate(0xb7c4) →
  播放同步(0x9b3b) → VolEff(0x74e3) → Eq1Eff/Eq4Eff/Eq5Eff(0x737b, 共享参数 @0xeac) →
  HowlingSuppress(0xc482) → Drc1Eff(0xdefe, @0x1038) → LLNS → DacEff(0xdccd)`。
  每条 ADC 输入一路（x6flow 有 6 路 ADC 链）。

**结论**：用户要的「放大环境声的通透 + 宽增益 EQ + 输出限幅」正是 mic_effect 辅听链的
原生能力；ANC 键循环的「通透」是另一条无 EQ 插槽的硬件通路。两者接驳点见 §6。

## 4. 音量限制实现层（数字域 vs 模拟域）

输出链（BO 已钉 + 本路复核）[文档+静态]：**DAC_VL0(0xfe051c) 数字音量 → DAA_CON1
(0xfe0a04) 3 位模拟增益 → 扬声器**。

- **数字域**：DAC_VL0 双通道半字对，setter `0x606db36` 钳位到配置槽 `[0x1265c0+
  1883*4]=0x1272ac` 上限（即数字音量的软件上限）；另有 getter/同步 walker/静音四消费者
  （读回=最后写值投影已由 BO 倾向）。**WDRC out_threshold + data_saturation(0x6b29) +
  DAC_VL0 三者共同构成数字域绝对上限链**。
- **模拟域**：DAA_CON1 3 位增益，4 档码表 `{0x00,0x04,0x06,0x07}`（flash 0x060a8608），
  RMW `0x606b40e`。档位粗（4 档），不是限幅机制，只影响整体模拟增益步进。
- **「100dB SPL」的换算链条**：DAC 满幅数字码 → DAC 模拟电压 → 功放/耳机 → SPL 的
  **声学标定在固件里不存在**（无任何 DAC full-scale→dB SPL 常量/表；[推断]）。因此
  固件侧无法表达「100dB SPL」绝对声压，**保守策略 = 数字域绝对上限**（DRC out_threshold
  设一个 dB 天花板 + DAC_VL0 上限），真机一次声压测量才能把数字 dB 校准到 SPL（与
  AGENTS「不猜测量」口径一致）。

## 5. 关键未知与风险（如实标注）

1. **EQ 段增益上限**：SDK 注释 `gain(-18~18)`，但表示是 float、上限未算法侧验证。用户
   要的 39dB 超出注释范围。替代路径：`WDRC inputgain`（可大 dB 宽带放大，SDK 注释「配 2
   表示总体增加 2dB」）做宽带增益 + EQ 做 1kHz 整形 + WDRC out_threshold 限幅——39dB 放
   到 inputgain/global_gain 而非单段 EQ。**增益上限需在算法侧（spectrum_eq/audio_eq IR）
   或真机验证**，本路不承诺。
2. **ANC 系数格式未解**（ANCIF/ANCIF1/anc_ext.bin 的 FF/FB 系数、ANCGAIN 的 trans gain
   布局）：若走「改 ANC 通透系数」路线（§6 L0 变体），需单独逆向 anc_db/ANCCOEF 格式，
   本路未投入。
3. **hearing_aid 开关在 app 里的可达性**：pipeline uuid 已初始化（§3.2），但
   `hearing_aid_player_open`/`mic_effect_player_open` 是否有现成按键/App 入口、还是完全
   未接线，本路未穷举调用面（§6 L1 的前置）。ANC 键循环和 App 命令 0x17 都只进 ANC
   模式（0x600db3c），不进 mic_effect。
4. **stream.bin 重打包**：参数区改 float 后需重算 §5 记录 offset/size 与 JLFS 资源 CRC
   （build_quiet_candidate 已有 volume 节点先例，可扩展），流程风险低但非零。
5. pipeline-mic_effect 在 QCY 的图拓扑与 SDK x6flow 是否逐节点一致：stream.bin 图节（§0/
   图拓扑）本路未逐节点解析（BX 同口径），Eff 节点清单以 §2/§5 为准。

## 6. 实现路径分层（L0/L1/L2）

### L0 纯参数（零代码）

- **主方案（辅听链参数区）**：改 stream.bin §5——Eq1Eff/4/5（0xeac，10 段 freq/gain/q +
  global_gain）、Drc1Eff（0x1038，inputgain/outputgain/out_threshold 限幅）、VolEff
  （0x14d7 音量）。参数区定位与格式已逐字节钉死（§1.2/§2），float 直接改。
  **前置**：确认辅听模式有进入方式（否则改了参数无用户入口——与 L1 接驳捆绑）；stream.bin
  重打包+CRC 流程（build_quiet_candidate 扩展）。**风险**：EQ 增益上限（§5.1）、
  SPL 无法绝对标定（§4）。
- **变体（ANC 通透系数）**：改 ANCIF/ANCIF1 的 TRANS 档 FF 系数 + ANCGAIN trans gain，
  在 ANC 通透通路上做固定频响整形。**前置**：逆向 ANC 系数格式（未做）。**风险**：无
  limiter、格式未知，本路不推荐为首选。

### L1 载荷扩展（需 R1 汇编器）

- **模式接驳**：把 ANC 键循环（0x60118b60 表 / 0x600db3c 模式 dispatch）的 TRANS 档（或
  新增一档）改接到 `hearing_aid_player_open()`（mic_effect pipeline），实现「通透键 =
  助听器放大」的语义。复用 EQ 载荷的追加+fence 模式（eq-payload-semantics）。
  **前置**：R1 汇编器；穷举 hearing_aid/mic_effect 开关接口的调用面；确认 mic_effect 代码
  全链入（pipeline uuid 已在，代码段链入程度待核）。**风险**：模式切换路径含 App 命令
  0x17 直接设置（0x600dcee），要同步处理；ANC 与 mic_effect 互斥/资源（24MHz clock）需
  核实。

### L2 完整自定义（新节点/新 pipeline）

- ops 表 28 条追加新节点（如 multiband_drc 或独立 limiter），或新增 pipeline。
  框架注册机制已钉（uuid 查找 0x607452e + 构造 0x6076b7a + plug 表 7 条）。
  **前置**：R1 汇编器 + 新 DSP 载荷（无可复用 QCY 参考实现，SDK 仅 IR 参考）。
  **风险**：最高；DSP 定点实现、内存/实时性无硅验证，本路不推荐短期走此路。

**ROI 排序**：L0 主方案（辅听链参数）→ L1 接驳 → 真机 SPL 校准 → （可选）多带 WDRC=L2。

## 7. 证据锚点与哈希表

- stream.bin §5 解析：name_hash = djb2 `h=(h*33+c)&0xFFFFFFFF` 取低 16 位（IR
  `_JBHash` 复核）。Eff 族哈希：Eq1Eff=dabd/Eq4Eff=7fe0/Eq5Eff=0c41/Drc1Eff=63e0/
  VolEff=b6c7/DacEff=94fe/MicEff=c6af——与 §5 记录逐条对上（§1 表）。
- 参数 blob 解码：eq_tool@0xeac（global_gain=0、10 段 25..21599Hz 全 0dB/q=0.7 平坦
  默认）、MusicEqBt cfg0@0x834（global_gain=-4，freq 30/170/300/700/1200/2800/5000/
  10000/11000/21599，gain -17..+3dB）、Drc1Eff@0x1038（bypass=0 attack=100 release=200
  tnum=3 mode=TWOPOINT algo=PEAK，拐点 (0.3→0.3)/(90.3→90.3)/(110.3→110.3)）、
  VolEff@0x14d7（`<BHiBihH>`：bypass=0 max=16 min=-45 mode=1 cur=5）。官方镜像
  stream.bin sha 19c9deeb…（analyze_image_resources 既有一致）。
- ops 表 28 条全量解码（§1.1 表）；uuid 查找 0x607452e/构造 0x6076b7a/plug 表
  [0x60a917c,0x60a9240)（eq-payload-semantics BT 已钉，本路复核一致）。
- ANC 通路：CVP_RefTask 任务名 0x609c5e8、ANCCOEF01/ANCGAIN01/ANCEXT02 资源名
  0x60a1dce/dd8/cc9、ANCCOEF01 引用点 0x606c57e/0x606e722、flash/app/ANCIF 引用
  0x606e85e；模式写 ANC 句柄 0x600db3c（[0x1265c0+500] 句柄，模式字节 struct 偏移见
  0x600db74 `b[r3+5]=r5`/0x600dc34 `b[r0+5]=r6`）。audio_anc.c（bootstrap 钉死 SHA
  e1552b0a…）：ANC_TRANSPARENCY/trans_alogm/l_transgain 语义。
- mic_effect 通路：任务名 mic_effect1/2 0x609c5f4/0x609c600 + 任务指针表 0x609d93c/
  0x609d948；0x9c2d 在 0x60057e6（get_pipeline_uuid，24MHz clock_alloc）与 0x602ead6
  （pipeline uuid 缓存）；SDK mic_effect.c（hearing_aid_player_open/MIC_EFX_DHA/
  STREAM_SCENE_HEARING_AID）、jlstream_event_handler.c（PIPELINE_UUID_MIC_EFFECT=
  0x9C2D、clock_alloc("mic_effect",24MHz)）、麦克风音效.x6flow（JSON 图，68 节点 6 路
  ADC 链）。SDK 拉取用 bootstrap --reference-sdk/--reference-binaries（钉死 67328f4）。
- 音量/模拟增益：DAC_VL0/DAA_CON1 消费者链见 docs/appcore-tail-sfr-semantics.md §3（BO）。
- 既有钉子：docs/eq-payload-semantics.md（BT，ops 表/uuid/0x60059ce 源字节/0x607898e
  subcommand-2）、docs/d2-audio-first-scene.md（BX，六具名场景/§5 流水线名）、
  docs/stream-volume.md（volume_cfg 布局/§3 0x6c94）、docs/anc-cycle.md（键循环 first-
  match + 内部 ON2/TRANS3 编号）、HANDOFF 路 BT/BX/BO/BN 条目。

## 8. 对后续路的建议（建议≠决定）

- 下一步最小验证 = **L0 主方案可行性演示**：取官方 stream.bin，把 Drc1Eff 的
  out_threshold 与 VolEff/Eq1Eff 参数改一组值，重算 CRC 打包，确认 build_quiet_candidate
  能字节级复现「参数区变化 + CRC 更新」（不刷机）。这验证 L0 的工程闭环，不涉及新代码。
- **L1 接驳**建议作为独立调研路：穷举 hearing_aid 开关接口调用面 + ANC/mic_effect 资源
  互斥，产出「接驳点 + 载荷形状」证据包（复用 BT 五钩子方法）。
- SPL 标定：真机一次（FD07 读 + 声压计）才能把数字 dB 对到 SPL，属于测量授权域，
  不在本路声明范围。
