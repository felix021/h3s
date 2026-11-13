# hearing-aid L1 接驳前置证据包（路 I / sim-l1wire，只读调研）

2026-11-13。本页是 CJ §6「L1 接驳」的三件前置证据：①hearing_aid/mic_effect 开关接口调用面
穷举（CJ §5 未知 #3）；②mic_effect 代码全链入程度；③ANC 与 mic_effect 资源互斥。**只读**：
未改 tools/ 任何代码、未跑改机器状态 probe；反汇编用仓库钉死 codec（pi32v2-isa @3608ed1，
reference-cache）+ 官方 UFW 公开字节（app.bin sha `18f3d573…`，加载基址 0x06000100）；SDK
用 CJ 已拉取的 pin 67328f4 源码（mic_effect.c / jlstream_event_handler.c / scene_switch.c /
audio_anc.c）。一次性脚本在本地 tmp（~/tmp/hearaid-i），不进库；私有 ROM 路径/字节不进本文。

证据强度：**[静态]** = 指令锚定反汇编（本路全 app 线性解码 ~28 万条）；**[SDK]** = 参考 SDK
源；**[镜像]** = 官方镜像字节；**[文档]** = 既有 docs/HANDOFF 钉死条目；**[推断]** = 上述链推出。

## 0. 一句话结论

**CJ「疑似未接线」升级为「完全未接线，且激活代码整段不在固件里」**：mic_effect pipeline 的
「定义层」（uuid 0x9c2d 注册 + 场景配置读取 + 任务名字符串）已编译进 QCY app，但「激活层」
（`mic_effect_player_create` / `hearing_aid_player_open`：调 get_pipeline_uuid("mic_effect") →
pipeline_parse(0x9c2d) → set_scene(HEARING_AID) → add_thread("mic_effect1/2") → start）**整段
被链接器裁掉，不存在于固件**。因此 **L1 不是「把通透档改接到一个现成函数的一次跳转」，而是
「用汇编器新写激活载荷 + 在通透档插一次接驳」**。ANC 与 mic_effect 共享 mic-ADC→DAC 音频通路，
互斥是硬约束。

## 1. 前置①：开关接口调用面穷举 → 完全未接线

### 1.1 锚点复核（CJ 钉的地址，本路全部复核）[静态]

- `get_pipeline_uuid`（jlstream 事件处理函数）入口 = **0x060057e6**（20 个调用点，全部 call）。
  内部「mic_effect」分支：0x06005982 `call clock_alloc`（r1=24000000=24MHz）+ 0x06005986
  `li.u32 r4=0x9c2d` → 返回 0x9c2d。与 SDK `jlstream_event_handler.c` 的
  `get_pipeline_uuid()` 逐条对上（`clock_alloc("mic_effect", 24MHz); return PIPELINE_UUID_MIC_EFFECT`，
  gate 是 `TCFG_MIC_EFFECT_ENABLE || TCFG_AUDIO_HEARING_AID_ENABLE`）[静态+SDK]。
- 该函数的字符串表基址 r7=0x060a18c0，「mic_effect」串在 r7+1314=**0x060a1de2**（16 个 pipeline
  名：a2dp/ring/tone/esco/linein/pc_spk/pc_mic/spk_eq/MusicEq/a2dp_dec/ai_voice/dev_flow/MusicDrc/
  EnergyDet/**mic_effect**/PitchSpeed——QCY 比 SDK 多加了 spk_eq/MusicEq/MusicDrc/EnergyDet/PitchSpeed
  等效果 pipeline 名）[静态]。
- `jlstream_pipeline_parse`（uuid 查找器 0x0607452e 的宿主）入口 = **0x06074938**（10 个调用点）
  [静态]。

### 1.2 穷举结果：没有任何调用方激活 mic_effect [静态]

**穷举手段**：全 app 线性解码（基址 0x06000100 起，约 28 万条指令），建 call 目标反查表 + 全部
li.u32 立即数表 + 原始 4 字节字扫描，三重交叉。

- **get_pipeline_uuid 的 20 个调用点，应用层只传 6 个 pipeline 名**：pc_spk（0x060a1afa）、tone
  （0x060a1990）、a2dp（0x060a1986）、esco（0x060a1995）、ring（0x060a198b）、pc_mic（0x060a1b01）；
  其余 8 个在 jlstream 框架区（0x0607xxxx，框架自注册）。**没有任何调用点传「mic_effect」**。
- **pipeline_parse 的 10 个调用点，第二参数（节点 uuid）是 0x768A/0xF975/0xD06D/0xA09F/0x6963/
  0x8458/0xCADC/0x8346/0xD186/0xD06D，没有一个用 pipeline uuid 0x9c2d**；且这些调用点所属函数的
  get_pipeline_uuid 名只有 tone/a2dp/esco/ring/pc_spk/pc_mic 六种。
- **0x9c2d 常量在 app 里只有 2 处 li.u32**：0x06005986（get_pipeline_uuid 的返回值）+ 0x0602ead4
  （见 §2.2 的场景配置读取）。16 位立即数（li.s16 等）无一等于 0x9c2d。SDK `mic_effect.c` 的
  `jlstream_pipeline_parse_by_node_name(uuid, "IIS0_RX1")` 的「IIS0_RX1」字符串在 app 里**不存在**。
- **「mic_effect1」/「mic_effect2」字符串（0x0609c5f4/0x0609c600）只被任务表引用**（§2.3），
  无任何代码 li.u32 引用——`jlstream_add_thread(stream, "mic_effect1")` 这一调用点不存在。

### 1.3 键/App 入口侧：同样没有 [静态+文档]

- ANC 键循环 0x06018b60（first-match 表 0x0609e510）→ apply_mode 0x060189aa → 模式 dispatch
  0x0600db3c（把内部模式写进 ANC 句柄 `[0x1265c0+500]` 的 `b[r3+5]`）。0x0600db3c 的 17 个调用
  点、0x060189aa 的 2 个调用点，全部只进 ANC，没有一处进 mic_effect/hearing_aid。
- App 命令 0x17 直接设置路径 0x0600dcee 只把 QCY 模式分派为内部 ON2/OFF1/TRANS3（anc-cycle.md
  已钉），不进 mic_effect。
- SDK 的 `hearing_aid_switch()`（开关接口）依赖 `hearing_aid_player_open/close`，而后者依赖
  mic_effect_player_create——激活链整体缺失，所以开关接口本身也缺失。

**结论（前置①）**：`hearing_aid_player_open` / `mic_effect_player_open` / `MIC_EFX_DHA` /
`STREAM_SCENE_HEARING_AID` 在 QCY app 里**没有任何现成按键/App 命令入口，且承载这些接口的函数
本体不在固件里**。缺的不是「一次函数调用接线」，是**整条启动链**（见 §4）。

## 2. 前置②：mic_effect 代码全链入程度 → 定义层在、激活层被裁掉

### 2.1 已链入的部分（定义层）[静态+SDK]

1. **pipeline uuid 定义**：get_pipeline_uuid 的「mic_effect」分支完整存在（§1.1），返回 0x9c2d 并
   `clock_alloc("mic_effect", 24MHz)`——对应 `TCFG_MIC_EFFECT_ENABLE||TCFG_AUDIO_HEARING_AID_ENABLE`
   在 QCY 构建里为真。
2. **场景配置读取**（见 §2.2）：init 期函数 0x0602ea68 读取含 0x9c2d 在内的 5 个 pipeline 配置。
3. **任务名字符串 + 任务注册表**（见 §2.3）：「mic_effect1」「mic_effect2」串在、任务表在。

### 2.2 0x0602ea68 是什么（CJ「uuid 缓存」锚点的真相）[静态]

CJ 把「0x9c2d 在 0x0602ead6」标成「pipeline uuid 缓存到全局字节」。本路全函数反汇编（0x0602ea68
起，约 1.4KB）核实：它是 **ANC/音频场景初始化函数**（在 init 函数指针表 0x060c32b0，由场景管理器
0x060121b4 的 `call r0` 循环在音频 init 期调用），做的事是——用 5 个 pipeline uuid 作 key，逐个读
配置字节进全局：

| 调用点 | key（r0） | 对应 pipeline（SDK） | 写入全局 |
|---|---|---|---|
| 0x0602ea82 | 0x7674 | PIPELINE_UUID_TONE_NORMAL | [0x12657c] |
| 0x0602eaa0 | 0xd96f | PIPELINE_UUID_A2DP | [0x126580] |
| 0x0602eabe | 0xba11 | PIPELINE_UUID_ESCO | [0x126582] |
| 0x0602eadc | **0x9c2d** | **PIPELINE_UUID_MIC_EFFECT** | [0x126584] |
| 0x0602eaf8 | 0xdc8d | PIPELINE_UUID_PC_AUDIO | [0x126588] |

读取用 0x060755b2（jlstream 框架的 16 位 uuid 查表函数，也被框架 0x06076bbe 调用）。**0x0602ea68
只读配置、不 create player**——它的调用面是 0x060755b2×5 + get_config 0x06000f88×5 + 若干硬件
init，没有 get_pipeline_uuid/pipeline_parse/add_thread。所以「uuid 缓存」的真实语义是「init 期把
mic_effect 的场景配置字节读进全局 0x126584」，不是「pipeline 激活」。

### 2.3 任务指针表（CJ §6「从 0x609d93c/0x609d948 拿任务入口」的修正）[静态]

- 0x0609d93c / 0x0609d948 存的**不是任务入口地址，是任务名字符串指针**：0x0609d93c→0x0609c5f4
  （"mic_effect1"）、0x0609d948→0x0609c600（"mic_effect2"）。
- 该表是静态任务注册数组 `{name_ptr, flags, extra}` 12B 一条，flags 低字节=优先级（mic_effect1/2=
  0x03000006 即 prio 6 + stack 0x30000；CVP_RefTask=0x01000004 即 prio 4 + stack 0x10000），
  extra=q_size（mic_effect1/2=0，多数任务=0x80）。**表里没有任何代码入口指针**。
- **整段任务表（0x0609d880..0x0609d960）在 app 里 0 处引用**（原始 4 字节字全扫 + 全部 li.u32 全扫
  双零）——它是死数据（连 CVP_RefTask/iis_play_task 同名条目也无代码引用；ANC 实际任务名是 audio_anc.c
  的 "anc"，`task_create(anc_task,NULL,"anc")`，与 "CVP_RefTask" 不是一回事 [SDK]）。
- SDK 里 mic_effect 的「任务」不是独立 task_create，而是 `jlstream_add_thread(stream,"mic_effect1/2")`
  在运行时创建的 jlstream 处理线程——该调用点不存在，所以**固件里没有可反汇编的「mic_effect 任务体
  入口」**。

### 2.4 结论（前置②）[静态]

「从任务指针表拿任务入口反汇编」这条路走不通——表里没有入口、也没有调用链。mic_effect 的真实状态是
**半链入**：

| 层 | 状态 | 证据 |
|---|---|---|
| pipeline uuid 定义（get_pipeline_uuid "mic_effect" 分支 + 24MHz clock） | **在** | §1.1 |
| 场景配置读取（0x0602ea68 读 0x9c2d 配置） | **在** | §2.2 |
| 任务名字符串 + 任务注册表 | **在（死数据）** | §2.3 |
| mic_effect_player_create / hearing_aid_player_open（激活链） | **不在** | §1.2 |

**不是「只有 uuid 常量/字符串、代码被裁」的最小残留，而是「定义+配置在、激活在外的中间态」**——
说明 QCY 构建开了 mic_effect/hearing_aid 的**框架宏**（所以 uuid/配置/任务名进了），但没有任何应用
代码调用 player_create，链接器把激活函数整段 GC 掉了。

## 3. 前置③：ANC 与 mic_effect 资源互斥清单

### 3.1 各自占用的资源 [SDK+静态]

| 资源 | ANC（通透档） | mic_effect（辅听） | 冲突？ |
|---|---|---|---|
| 时钟 | clock_refurbish「模块时钟消耗 96M」[SDK audio_anc.c:644]；app 内 96MHz 立即数仅 0x06005bd6 | clock_alloc("mic_effect", 24MHz) @0x06005982 | 不同时钟域，但切时钟会停 CPU/DAC 缓存（SDK linein 注释）[推断] |
| 音频输入 | MIC/ADC（audio_anc_adc_ch_set，adc_ch 由 mic_en_map 决定）[SDK] | ADC 源（NODE_UUID_ADC 0xD06D / IIS0_RX1）[SDK] | **同源冲突** |
| 音频输出 | DAC（ANC FF→DAC） | DAC（jlstream→DacEff；hearing_aid_player_open 先 DAC_NOISEGATE_OFF、close 后 DAC_NOISEGATE_ON）[SDK] | **同输出冲突 + 共享 noisgate** |
| 任务优先级 | ANC 任务 prio 4（"anc"/CVP_RefTask 档） | mic_effect1/2 prio 6 | ANC 更高优先 [静态] |
| 内存 | — | 2×0x30000 栈 + 可搬移代码区（TCFG_CODE_RUN_RAM_MIC_EFF_CODE）[SDK] | 需 RAM 预算 |
| coexist 策略 | — | SDK coexist 表：HEARING_AID 与 A2DP/TONE/ESCO/RING 两两互斥（TCFG_AUDIO_DHA_AND_*_COEXIST 决定）[SDK jlstream_event_handler.c:57-88] | 与音乐/提示音/通话互斥 |

### 3.2 接驳时要注意的互斥点（清单）[推断，基于上表]

1. **mic-ADC 输入同源**：通透（ANC FF feedthrough）和辅听（mic_effect ADC 链）都想独占同一路 mic
   输入。切到辅听前必须先退出 ANC 通透，否则两路同时抢 ADC。
2. **DAC 输出同目标 + 噪声门**：两者都往 DAC 送。`hearing_aid_player_open` 调 `DAC_NOISEGATE_OFF`、
   close 调 `DAC_NOISEGATE_ON`——噪声门是共享资源，ANC 通路对 noisgate 的假设会被辅听开关破坏
   （或反之），接驳必须配对开/关。
3. **时钟切换代价**：ANC 96MHz vs mic_effect 24MHz，`clock_alloc`/`clock_refurbish` 之间切换会
   stop CPU / 影响 DAC 缓存（SDK linein 注释明示 120M 的理由），接驳时序要把时钟切换的毛刺算进去。
4. **coexist 策略表**：SDK 把 HEARING_AID 与 A2DP/TONE/ESCO/RING 设为互斥场景对；QCY 固件里这套
   策略是否编译、行为是否一致未核（本路未查 QCY 的 coexist 表），接驳前需确认「放歌时按通透键进
   辅听」不会触发未预期的场景抢占/静音。
5. **任务优先级**：ANC 任务 prio 4 高于 mic_effect prio 6；若两者短时共存（切换窗口），ANC 会抢占，
   需确保接驳路径先 close ANC 再 open 辅听（或反之），不留双开窗口。
6. **内存**：mic_effect 需要 2×192KB 栈 + 可搬移代码区，接驳 payload 若用追加+fence 模式（BT 先例）
   要确认不挤占 mic_effect 自己需要的 RAM/代码搬移槽。

**结论（前置③）**：ANC 通透与 mic_effect 辅听**不可共存**，是「同一 mic→DAC 通路上的两个消费者」。
接驳语义应是「通透键 = 先关 ANC 通透、再开辅听」的**替换**，不是叠加。

## 4. L1 最小改动形状（建议，不实施）

### 4.1 修正后的形状：一次跳转不够，是「新写激活载荷 + 一次接驳」

- **载荷（新代码，用路 G 汇编器写）**：在固件里重写 `mic_effect_player_create(MIC_EFX_DHA)` 的
  精简版。参照物是现成的 tone 播放器 0x0600d9be（get_pipeline_uuid→pipeline_parse→set_scene→
  set_node_param→add_thread→set_callback→start 的完整序列），换 uuid/场景/线程名即可。可复用的
  jlstream 框架 API 入口（本路从 tone 播放器反查钉死）：
  - `get_pipeline_uuid` = 0x060057e6（r0=事件 id 15，r1="mic_effect" 串 0x060a1de2）
  - `jlstream_pipeline_parse` = 0x06074938（r0=pipeline uuid，r1=节点 uuid 0x768A/0xD06D）
  - set_scene/set_node_param/add_thread/set_callback/start = 0x06074a5e / 0x06074e64 / 0x06074e78 /
    **0x06074ec6（add_thread）** / 0x06074ece / **0x06074ffe（start）**（与 SDK mic_effect.c 的调用序
    一一对应；set_scene 的 HEARING_AID 场景枚举值、player 结构体字段布局需再核 SDK 头 + 参考播放器）。
  - 载荷量级估计：tone 播放器 create 序列约 30~40 条指令，mic_effect 版（加 DAC_NOISEGATE_OFF、
    MIC_EFX_DHA 分支、两个 add_thread）约 **40~60 条指令 ≈ 150~300 字节**（混用 2/4/6 字节指令），
    外加 BT 五钩子式的追加+fence/重定位。
- **接驳（改一处跳转）**：在 ANC 键循环的 TRANS 分支把通透档改接到新载荷。候选接线点：
  - 模式 dispatch 0x0600db3c 的 `r5==3`（TRANS）分支（0x0600db92/0x0600dbbc 一带），把「写 ANC 通透
    模式」替换/前插为「调新 hearing_aid_open 载荷」；
  - 或 apply_mode 0x060189aa 的 TRANS 档；
  - 同步处理 App 命令 0x17 直接设置路径 0x0600dcee（否则 App 选通透与按键通透语义不一致）。
- **必须配套**：接驳载荷入口先关 ANC 通透（§3.2 互斥清单 1/2），再做 noisgate 配对。

### 4.2 诚实标注（与 CJ §6 的差异）

CJ §6 假设「把通透档改接到 hearing_aid_player_open」隐含「该函数已在固件里」。本路钉死：**该函数
不在固件里**，所以 L1 的工作量 =「重写 mic_effect_player_create（可精简）+ 接驳 + ANC/辅听切换配套」，
比「一次跳转」多一整个激活载荷。若只想做最小验证，可先只证明「调用 get_pipeline_uuid(0x060057e6) +
pipeline_parse(0x06074938) + start(0x06074ffe) 能拉起 0x9c2d pipeline」（载荷最小子集），再补
set_scene/add_thread/场景值。

## 5. 证据锚点表（本路钉死）

- get_pipeline_uuid 入口 0x060057e6（20 调用点）；"mic_effect"→clock_alloc 0x06005982（24MHz）+
  li.u32 0x9c2d @0x06005986；字符串表基 0x060a18c0、"mic_effect"@0x060a1de2（+1314）。
- jlstream_pipeline_parse 0x06074938（uuid 查找器 0x0607452e 宿主，10 调用点）；add_thread 0x06074ec6、
  start 0x06074ffe、set_callback 0x06074ece（tone 播放器 0x0600d9be 反查）。
- 0x9c2d 常量全 app 仅 2 处 li.u32：0x06005986 / 0x0602ead4（场景配置 init 0x0602ea68；init 函数指针表
  0x060c32b0，表被 0x060121b4 的 call.r 循环调用）。
- 任务名字符串：mic_effect1@0x0609c5f4 / mic_effect2@0x0609c600 / CVP_RefTask@0x0609c5e8 /
  iis_play_task@0x0609c5ce；任务注册表 0x0609d880..0x0609d960 全 0 引用（mic_effect1/2 记录在
  0x0609d93c/0x0609d948，存的是串指针非入口）。
- ANC：键循环 0x06018b60 → apply_mode 0x060189aa → 模式 dispatch 0x0600db3c（b[r3+5]=mode，句柄
  [0x1265c0+500]）；App 0x17 直接设置 0x0600dcee。
- 参考 SDK（pin 67328f4）：mic_effect.c（hearing_aid_player_open/mic_effect_player_create(MIC_EFX_DHA)/
  add_thread("mic_effect1/2")/DAC_NOISEGATE_OFF）、jlstream_event_handler.c（PIPELINE_UUID_MIC_EFFECT
  =0x9C2D、TONE 0x7674/A2DP 0xD96F/ESCO 0xBA11/PC_AUDIO 0xDC8D、coexist 表）、audio_anc.c
  （clock_refurbish 96M、task_create("anc")、adc_ch）。
