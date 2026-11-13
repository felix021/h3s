# D2 音频域首场景形状预研（路 BX / sim-d2audio，只读）

2026-11-13。本页是 D2（音频流）的首场景预研：把「D1 事件循环 → 音频流启动」的入口图、
离线可喂性、音频域 SFR 缺口、验收断言草案一次性钉死，为「效果节点状态对比 = EQ 修复
验证最后一块」（[docs/eq-payload-semantics.md](eq-payload-semantics.md) §4，路 BT）铺路。
**只读**：未改 tools/ 任何代码、未跑改报告的 probe；反汇编用钉死 codec
（pi32v2-isa @3608ed1，经 worktree reference-cache 软链复用主仓钉死缓存）、官方 UFW
公开字节 + pinned 参考 SDK（bootstrap --reference-sdk/--reference-binaries 校验下载）。
一次性脚本放本地 tmp 不进库。结论基于 main 574e4d7。

证据强度：**[静态]** = 指令锚定反汇编/资源字节解析（本路）；**[SDK]** = 参考 SDK
media.a LLVM IR（参考材料，非精确 QCY 源）；**[文档]** = 既有 docs/HANDOFF 钉死条目；
**[推断]** = 上述链推出。

## 1. 音频域入口图（D1 事件循环 → 音频流启动）

### 1.1 顶层面：两张分发表里的音频入口 [静态]

D1 消息循环（S19 0x60123e6，docs/d1-acceptance.md §2.1）按 12B 记录
{filter, event_id, handler} 分发。全量解码两表（表 1 = 0x60a89e0 54 条、
表 2 = 0x60a8c68 6 条），音频域相关入口：

| 入口 | 事件 | handler | 音频角色 |
|---|---|---|---|
| 表1[4] | 0x100006 | 0x6012864 | 音频任务层：payload 字 39..46 tbb 子分发（含开/关/模式）；子 39 分支过 tone 表（§1.2） |
| 表1[5] | 0x100006 | 0x6012c1a | 模式切换：payload+0 tbb，0x6068152（P3_ANA_CON0/7-10 RMW，BO N3 同函数）在两个 case 内 |
| 表1[37] | 0x100009 | 0x60207a2 | BT 事件大树：payload b[..]>30/129..160 tbh 31 路子分发（a2dp/esco 开流的候选上游） |
| 表1[38] | 0x100008 | 0x602213a | BT 域 handler（未展开） |
| 表1[47] | 0x10000c | **0x6023a0a** | **开机音触发**（§1.2 钉死） |
| 表1[43] | 0x10000c | 0x6023962 | 音频通道状态机：payload 112/109/110/18 → 0x60237d8(36831/28759/40402/36798, 0) |
| 表2[5] | 0x10000c | 0x60252c6 | 按键 handler（BT 路已钉，EQ 钩子 0x6025364 所在 fn） |

事件 id 与 payload 子命令的生产者（raw 18/20 的 id 变换、0x70/0x6D 的 poster）仍是
BI/BJ 观测项（docs/d1-acceptance.md §2.3 口径不变）。

### 1.2 tone 场景入口链（开机音，全静态钉死）[静态]

```
handler[47] 0x6023a0a (event 0x10000c):
  payload[0]==112(0x70) 且 b[QCY+4451]≠0        # QCY=0x1265c0；4451=byte 偏移
  → 0x600c2aa()                                   # tone 表 getter：QCY+348 语言字
                                                    #   !=1 → zh 表 0x60a379c；==1 → +224(en) 0x60a387c
  → [表+40] = entry 10 = 'TON1/tone_en/power_on.*' # 双语指针表各 55 槽（本路全量解码）
  → 0x6017cc0(pattern, cb=0x60239D6)
      → 0x600d95e(pattern)   # 按通配名解析 tone 文件（资源 FS）
      → [entry+120]=0/[+124]=cb, 0x600da64         # 挂 tone 队列（头 0x124a24，设备互斥 0x6064538）
  完成回调 0x60239D6（fn 0x60239c0 内）：b[QCY+4447]|b[0x1249BE+9] 全零且序号==21
      → 再放 pairing 音（[表+48]）→ 0x6017878 → 0x60107a8   # power_on → pairing 链
tone 消费端（引擎 0x600d9xx..0x600daxx，状态结构 0x1249BE）：
  → 框架流控制 API（app→框架域 98 条直接调用边的一部分）：
    0x6074ec6 / 0x6074a5e / 0x6074e64 / 0x6074ffe / 0x6074204（tone 引擎调用簇）
```

tone 资源**全部在公开 UFW 外层条目里** [静态]：TON（zh，47024B）/TON1（en，66368B），
kind=50 SFC 编码，解码后 = 32B 目录项 {hcrc,dcrc,start,size,flags,last,name[16]} 索引的
tone 文件集——TON1 含 `power_on.wts`(2913B)、`power_off.wts`、`pairing.wts`、
`ring.aac`、`anc_on/off/trans.wts` 等 17 个文件，逐条 CRC 全过。**.wts = WTS 编码、
.aac = AAC**（扩展名即codec）。资源名经 'TON1/tone_en/<name>.*' 通配匹配（0x600d95e）。

### 1.3 框架层（jlstream）结构 [静态+SDK]

- **配置文件**：'flash/res/stream.bin'（加载器 0x6074234：zalloc 2060B、资源 get
  0x6007ae8、缓存 [buf+2056]、头 4B 校验 'J''S'+版本@buf+2048）；另有
  'flash/res/scene.bin'（app 层 0x60183f8 加载，全局 0x125368）。两文件都在公开
  JLFS 资源区 [静态：analyze_image_resources 既有 SHA]。
- **stream.bin 结构**（公开字节直接解析，本路全量钉死）[静态]：9 节
  {u16 节type, u16 len}（type=0..8,10；§3 对齐 docs/stream-volume.md 旧表）。
  **§2 = 节点实例名目录**（53 条 21B 记录 {标记0x13, uuid16, subid, name[]}）：
  eq(0x737b)×11 实例：**MusicEqBt(sub1)**/EQ12/13/14/EscoUlEq/EscoDlEq/EQ11/EQ47/
  Eq4Eff/Eq5Eff/Eq1Eff；wdrc(0xdefe)×4：DRC7/8/9/Drc1Eff；volume(0x74e3)×9：
  **Vol_SysTone/SysKTone/SysRing**/LinMusic/**BtmMusic**/BtcCall/PcspkMusic/BtdMusic/
  VolEff；DAC(0xdccd)×5：DAC1/2/3/6/DacEff。**§5 含流水线名**：
  `pipeline-tone-normal`、`pipeline-a2dp`、`pipeline-CVP`、`pipeline-PCAudio(USB Audio)`、
  `pipeline-a2dp_dut`、`pipeline-mic_effect`——六个具名场景 [静态]。
  节点→流水线图的拓扑留在图节内，由固件自己的 parser（[SDK] jlstream_file_parse.c：
  read_pipeline_data 按 section type==pipeline uuid 匹配）运行时解析，**不需要仿真侧重写
  parser**。
- **ops 表 0x60a8ed0 = 框架节点适配器注册表**（28 条 {op0=bind,op1=ioctl,op2=release,
  name,uuid16,flags}，BT 已钉；decoder=0x1a85 与 [SDK] decoder_node.c 的
  `decoder_node_adapter` uuid=6789=0x1A85 互证）。**7 条 28B 注册表 0x60a917c = 源/
  codec plug 表**（{u16 key, u16 flags, create@+4, ...}，key 即 stream.bin 图里可直接
  引用的源节点 uuid：0xd06d='esco_adc/ADC13/MicEff' [静态]；create 在 0x6031xxx..0x6033xxx
  = DSP 解码库 + 0x607a6fe 族 convert）。构造器 0x6076b7a 恰是 'decoder' 适配器的 op0
  （按 codec key 匹配 plug、zalloc 48B、call create）——BT 的两层表述与此一致并细化。
- **任务与队列**：框架 post 0x6074192 → OS 队列 API 0x6064d72(**队列名 'jlstream'**,
  0x609d3bb；Q_FULL=21 时 0x6064efc(2) 重试 ≤10 次)[静态]；[SDK] jlstream_task.c：
  `stream_task` 体 = `os_taskq_pend` → ret 13 → `stream_msg_handler` → 逐 stream 状态机
  ——与 app 消息循环同款 pend-13 惯用法。**框架任务在 app 里的创建点未静态钉死**
  （[SDK] jlstream_task_create 在 .initcall.bank.text 段；候选=S10 的 W1-W4 init walker
  或首个流水线打开时的惰性创建；一处运行时观测项，见 §5）。另有 0x6086980 = 第二个
  消息循环任务（0x6064c8c(sp+24,8) pend-13 + tbb cmd 4..12，亦向 'jlstream' 队列 post），
  按 [SDK] audio_event_manager.c 对位归类为音频事件管理任务 [推断]。
- **节点参数消息**（BT 已钉，本路补形状）[静态]：0x607538a 打包 {…, uuid16, name[16],
  param[44]} 后以 **cmd=11** post 进 'jlstream' 队列；框架消费 → 目标节点 op1。对
  eq/wdrc 族即 0x607898e 的 subcommand 分发：**subcommand 2 → 0x6078a2e
  `b[node+35]=value`**；值 0 且句柄空 → 0x60787ac 重开（内含钩子站点 0x6078846，
  见 §4）。
- **0x60787ac（open-config）内部序** [静态]：0x60787d8 call **0x60057e6**（EQ 源选择
  fn，钩子 0x60059ce 在其内）→ 0x60750aa/0x60751da 读 stream.bin 节点配置 →
  0x6078846（原始 `ctx[35]=file_cfg[4]`；EQ 镜像=关+名字匹配则强制 1）。
  **效果节点 open 路径天然覆盖 5 钩子中的 2 个**（0x60059ce + 0x6078846）。

### 1.4 驱动层与硬件面（DAC/ADDA 输出路径）[静态+SDK]

DAC/ADDA 驱动函数（0x606axxx..0x6073xxx）**没有直接调用者**——经运行时注册的
ops 表间接到达（数据段无静态指针槽，0x606e9f8 `b[r1+4]=r0` 式动态注册）[静态]。
因此静态调用图在此截止是常态，仿真侧由真实执行自然解析。关键锚（BO §3 已钉的
不重复）：DAA_CON0 上/下电位对 0x6071d80/0x607384c、DAA_CON1 3 位 RMW 0x606b40e、
DAC_VL0 getter/setter/walker/静音 0x606daf0/0x606db36/0x606db74/0x606dd16。
[SDK] audio_dac_io.c 的 JL_AUDIO 寄存器面 = 0xfe0500/04/08/0C/10/14/18/38
（DAC DMA buf/len/cnt + CON）。S7 的向量安装（irq 39 → 0x60348e8）按位置归音频
IRQ 候选 [推断]（docs/appcore-tail-map.md §2 S7；交付后首次 DMA 数据流才需要）。

### 1.5 最小音频启动序列（tone 首场景，函数级）[静态]

按执行序（框架任务/队列消费在 D1 后的任务上下文里）：

1. 事件 0x10000c/payload 0x70 → 0x6023a0a（门 b[QCY+4451]）。
2. 0x600c2aa → [tone 表+40] → 0x6017cc0(pattern, cb) → 0x600d95e/0x600da64 入队
   0x124a24。
3. tone 引擎出队 → 框架 open（0x6074ec6 族）→ 0x6074234 载 stream.bin（资源 get，
   公开字节）→ 场景匹配 'pipeline-tone-normal'。
4. 节点创建 0x607471e（uuid→适配器 0x607452e）：decoder bind 0x6076b7a（WTS plug
   create 0x6031xxx 族）+ Vol_SysTone(0x74e3) + DAC 节点 + 图内其余节点。
5. 效果/参数配置：stream.bin §5 参数 + cmd-11 参数消息（eq/wdrc 族走 0x607898e →
   node+35 初值 = 文件值；0x60787ac 顺带执行 0x60059ce/0x6078846 两站点）。
6. DAC 打开：DAA_CON0 电位对 → DAA_CON1 增益 RMW → DAC_VL0 写 → JL_AUDIO
   0xfe0500 CON/DMA 寄存器序列。
7. 数据流：tone 文件（公开 .wts 字节）→ WTS 解码 → 效果链 → DAC DMA 缓冲。
   IRQ39 交付 + DMA 状态轮询（0xfe0538 bit4，§3）属数据流阶段。

音乐场景（EQ 断言用）= 同框架、场景 'pipeline-a2dp'、节点 MusicEqBt/EQ12-14/
Vol_BtmMusic/DAC2；入口候选 = BT 事件 handler 0x60207a2 子分发或表1[4] 0x6012864
（事件 0x100006），其 payload 语义与前置 BT 状态门是场景设计项（§2）。

## 2. 离线可喂性评估

**结论：tone 首场景完全可离线驱动，全部输入是公开工件字节；音乐场景（EQ 节点状态
对比）需要一条合成事件 + 若干 QCY 状态门，无需真实蓝牙栈数据即可到达「节点创建+
ctx[35] 初值写入」层；再往下的 PCM 数据流才需要合成/省略决策。**

1. **tone 场景输入域** [静态]：事件（0x10000c/0x70）或直接调用 0x6017cc0(pattern)
   皆可；tone 数据 = TON/TON1 公开资源（.wts/.aac 真实字节）；stream.bin/scene.bin
   公开。无任何 BT 依赖。**风险**：0x600d95e 的资源解析走 flash FS（VM/资源分区机制
   已在前缀走通 [文档：路 S/T/U]）；tone 引擎的设备互斥 0x6064538 与「当前在播」
   检查 0x600d984 需空闲初态（bss 零即满足）。
2. **音乐场景输入域** [静态+推断]：MusicEqBt 节点的创建与 ctx[35] 初值只依赖
   pipeline-a2dp 打开 + 参数消息消费——**不需要空中 BT 数据**。触发=合成事件注入
   （经固件自身 post API，L2-b P9 同款显式 fixture）或直调 0x6017cc0 族的等价
   音乐开流入口。需要核对的状态门：0x60207a2 子分发前的 0x6008298/0x6001ef6/
   0x60107a8 检查、QCY 结构的模式/连接位（如 b[QCY+...]）——**注入前先把这些门
   逐个枚举**（场景设计的一部分，BT 对 P9 的口径不变：不承诺一次注入覆盖多站）。
   a2dp 解码器的真实输入（SBC/AAC 帧）在「节点状态对比」断言层**不需要**。
3. **效果节点状态对比的最小观测集** [静态]：
   - 站点执行覆盖：0x607471e（建节点）、0x6076b7a（decoder bind）、0x60787ac 入口、
     **0x6078846（钩子，EQ 镜像必显形）**、0x6078a2e（subcommand-2 写点）、
     0x607538a/0x6074192（消息打包/post 的返回值——BT 钉的「入队成功≠命中入口」）。
   - RAM：node ctx 是堆分配（0x12e16e 族），**按写集观测**（0x60787ac 全函数写集 +
     0x6078a2e 单点）而非固定地址；对照对象 = MusicEqBt/EQ12..14（eq）与
     DRC7/8/9（wdrc，应不受影响的旁证组）+ 0x1265EA（EQ 状态字节，BT 已钉）。
   - 报告侧：三镜像（official/b79/EQ 候选）同场景差分直接复用 analyze_image_trace
     的逐步签名/parity 机制 [文档：路 Y+AJ]。
4. **不可离线项**（诚实收口）：真实 DAC 输出波形、模拟增益的声学效果、BT 空口时序
   ——不在离线验证声明范围内；DMA 数据流阶段（IRQ39、0xfe0538 轮询）如需跑通，
   按合成数据+完成投影处理（§3 缺口表 G3/G5）。

## 3. SFR/投影缺口清单（音频首场景会碰的硬件面）

对照现有投影（tail-map 的 26 已建模 + 26 同族 + 19 新增清单 [文档：路 BH/BO]），
音频域新增面按地址族列出（站点数=app 内 li 直接引用数，另有基址+变址访问；
R/W 形状按站点指令分类 [静态]）：

| 族 | 地址（新增，未在 BO 音频簇清单内） | 站点 | 形状 | 建议先例 |
|---|---|---|---|---|
| **JL_AUDIO DAC/DMA** 0xfe0500 | 0xfe0504(DMA 指针/计数读×6)、0xfe0508、0xfe0524、0xfe0534、**0xfe0538(bit4 完成轮询×4)** | 15(基址)+15 | RMW+读回消费 | 0xfe051c DAC_VL0 按 BO「读回=最后写值」；**0xfe0538 轮询需完成投影**（lrct_trim_done 先例）或 IRQ 驱动 |
| **JL_ADDA** 0xfe0a00 | 0xfe0a0c(3 位 RMW)、0xfe0a20/24/30/40、**0xfe0a48/4c** | 8(基址)+11 | RMW 为主 | 同族扩展 BO 容器；**0xfe0a48/4c 出现在主路径 ADC 采样 setter tbb case（0x60116b0：[0xfe0a4c]\|=2、[0xfe0a48]&=0xFFFFF000）**——不纯是 D2 面，BG 后 ADC 通道切换可能先碰 [静态] |
| **0xfe0400 族**（SDK 头未缓存，疑音频时钟/gate 域） | 0xfe0400/04/0c/20/28/44c/460/46c/4a8/4b0/4d4/4ec | 27 | 位 RMW+状态位读 | 配置 RMW 记录（0xf2c054 先例）+ 读回位语义逐个钉 |
| **0xfe0900 族** | 0xfe0900/0xfe0908 | 5 | 读+RMW | 同上，先证据后放行 |
| **音频时钟 0xfd19xx** | 0xfd1900/04/0c/20/28/50（0xfd1924=LRCT 已建模；0xfd1928 被 DAC 域 0x606b670 引用；0xfd1920 被 decoder plug 域 0x60351cc 引用） | 16 | 未逐个分类 | [SDK] aud_clock_manager.c 对位；到达时逐地址三选一 |
| TIMER2 0xfd0600 | （BO N4 已钉：延时 helper 0x12cd26 CON 轮询） | — | idle 忙等 | 音频模拟稳定延时可能复用；维持 BO 钉住建议 |
| 0xfe0218 | （SFC +0x18，已建模族内） | 1 | — | 无缺口 |

另：**JL_SBC 硬件加速器疑点消解** [静态]——[SDK] sbc_hwaccel.c 引用 0xfd1100 达 30
处，但 app 侧 0xfd1100 仅 2 站（0x6069514/0x606953a）且 br28_model 已按 GPCNT_CON
钉死（clock_startup 证据）——**app 的 SBC 解码不走 0xfd11xx 硬件加速面**（或该对象
未链入），不新增缺口。0xfd1800（19 站，0x6004xxx-0x6007xxx BT/baseband 域）归 BT
域后续调研，不属音频首场景。

**缺口量级结论**：音频首场景的硬件面 ≈ 0xfe04xx(12)+0xfe05xx(≥8)+0xfe09xx(2)+
0xfe0axx(≥9)+0xfd19xx(≥6) ≈ **37+ 个地址、5 个族**，其中真正的语义决策点只有
两个族：读回消费闭环（0xfe0538 轮询、0xfe04xx 状态位）与 ADDA 初值 fixture
（BO §5 已给「读回=最后写值+首读具名 fixture」组合先例）。其余同族 write-only
记录可打包放行。

## 4. D2 验收断言草案（首场景怎么算跑通）

前提：D1 四断言已过（BJ）；场景 = tone 首场景（离线全公开数据）。断言全部建立在
真实前缀执行上，投影/fixture 显式 opt-in，结论维持投影等级：

- **D2-A 流水线构建**：注入 power-on 音事件（或直调 0x6017cc0）后，真实执行链
  完成：tone 入队（0x124a24 非空、互斥往返）→ 框架 open API 至少一次 →
  stream.bin 资源 get 成功 + 'J''S' 头校验 → 节点创建 0x607471e ≥1 次且
  decoder(0x1a85) 经 0x6076b7a 绑定 WTS plug（plug create 真实执行）。观测 =
  上述 PC 执行覆盖 + 0x6074192 post 返回值（BT 口径：入队成功才算）。
- **D2-B 效果节点状态（EQ 验证的 D2 半场）**：音乐场景（pipeline-a2dp 合成事件）
  下，MusicEqBt 节点创建后框架消费其参数消息：**EQ 关态（0x1265EA bit7=1）→
  0x6078a2e 写 node+35==1（EQ 镜像）vs b79 写文件配置值**；wdrc 组（DRC7/8/9）
  与非目标 eq 实例（EscoDlEq 等）的 +35 不因镜像差异翻转；0x6078846/0x60059ce
  两站点执行覆盖各 ≥1（0x6078846 仅 EQ 镜像执行——三镜像差分点）。
- **D2-C DAC 输出路径激活（数据全 synthetic）**：DAA_CON0 上电位对、DAA_CON1
  3 位 RMW、DAC_VL0 写、JL_AUDIO CON+DMA 寄存器序列全部以 write-only 记录/读回
  =最后写值投影走通；0xfe0538 轮询以显式完成投影（或 IRQ39 交付）收口；**不要求
  真实音频数据**——解码输入为公开 tone 字节，DAC DMA 缓冲内容只记录不校验声学。
- **D2-D 消息闭环与诚实收口**：'jlstream' 队列 ≥1 条消息被框架任务真实消费
  （pend-13 往返）；框架任务创建点被观测记录（§5 不确定处①）；双击 toggle 的
  两条 cmd-11 消息（BW 控制面场景的输出）在本场景被消费 → node+35 翻转 +
  0x60787ac 重开路径执行——即 EQ 验证链的最后一块（控制面+节点面会师）。

## 5. 不确定处（如实标注）

1. **框架任务创建点未静态钉死**（init walker 惰性 vs 首开创建）：一处运行时观测
   （D2-A 顺带记录）。0x6086980 第二消息循环的角色（音频事件管理）是 [SDK] 对位
   [推断]。
2. **音乐场景注入的精确 payload 与状态门清单未逐门枚举**（0x60207a2 tbh 31 路、
   QCY 连接/模式位）：场景设计项，本路只钉了入口候选；BT 对 P9 的「不承诺一次
   覆盖」口径沿用。
3. **'eq' op1（0x6078594）vs 'wdrc' op1（0x607898e）谁消费 0x737b cmd-11 消息**
   ——BT §5 已标的不确定，本路无新证据；断言 D2-B 的观测点（0x6078a2e 写点）
   不受影响（谁到达都会经过它或等价点，届时以执行记录为准）。
4. 0xfe0400/0xfe0900 族的 SDK 名与硬件身份未定（参考头缓存里无 JL_AUDIO 域头文件；
   BO 引用的 br28.h L162-194 不在 pinned 缓存内）；三选一落地前需按实读行为命名。
5. stream.bin 图节（§0/图拓扑）未逐节点解析——不阻塞场景（固件自己解析），但
   「tone 场景含哪些效果节点」的精确清单要等 D2-A 运行时记录；本路只钉了实例名目录。
6. 步数/触达预测沿用 tail-map 限制（CON/PRD 再写会移动期限）不变；音频 IRQ39 的
   中断号-设备对应是 [推断]（S7 安装锚+位置，未逐寄存器证明）。

## 6. 证据锚点

- 分发表/ops 表/plug 表/tone 表全量解码：本路一次性脚本（codec pi32v2-isa @3608ed1，
  主仓 reference-cache 经软链复用；脚本在本地 tmp，不进库）。关键新锚：
  handler[47] 0x6023a0a payload 0x70 分支、tone 表 getter 0x600c2aa（QCY+348 语言，
  zh 0x60a379c/en 0x60a387c，各 55 槽）、播放链 0x6017cc0→0x600d95e→0x600da64
  （队列 0x124a24）、框架 post 0x6074192（队列名 'jlstream'@0x609d3bb）、
  stream.bin 加载器 0x6074234、构造器 0x6076b7a=decoder op0、cmd-11 打包 0x607538a、
  open-config 0x60787ac 内部序（0x60057e6→0x60750aa/0x60751da→0x6078846）。
- 公开工件字节：firmware/ 两个 UFW（fetch_firmware 校验 SHA）；TON/TON1 目录解析
  （sfc 解码 + 逐条 CRC16，17 文件清单含 power_on.wts 2913B）；stream.bin/scene.bin
  经 analyze_image_resources.walk_jlfs 提取（SHA 与 analysis/image-resources.json 一致，
  official stream.bin=19c9deeb…、b79=6c1f1950…——b79↔official 的 stream.bin 差异
  即 AGENTS 不变量 4 的音量参数区，L2-b 既有 P 项，不新增）。
- 参考 SDK：tools/reference-cache/media.a（bootstrap.py pinned SHA 6d1c5ed5…）llvm-dis：
  jlstream.c/jlstream_task.c/jlstream_file_parse.c/tone_file.c/decoder_node.c/
  audio_dac_io.c/sbc_hwaccel.c；节点适配器 uuid（decoder 0x1a85）、源 plug 形状
  （source_node_plug {uuid16,flags,init/fread/fseek/ioctl/release}）、
  stream_task 的 pend-13 惯用法、audio_dac_io 的 JL_AUDIO 寄存器偏移（0xfe0500/
  04/08/0C/10/14/18/38）。
- SFR 站点清单：app 全量格式化缓存扫描（li 直接引用，35 个 0xfe0xxx 地址 + 分类；
  0xfd1xxx 扫描消解 SBC 疑点）。既有模型覆盖对照：tools/br28_model.py
  （SFC 0xfe0200/0xfe0218、GPCNT 0xfd1100、LRCT 0xfd1924 等）。
- 历史钉子：docs/eq-payload-semantics.md（BT：ops 表/uuid/消息/subcommand-2/断言形状）、
  docs/appcore-tail-sfr-semantics.md（BO：DAC_VL0/DAA_CON1 消费者链与三选一倾向）、
  docs/d1-acceptance.md（BR：分发表结构/事件循环/L2 切分）、docs/appcore-tail-map.md
  （BH：S1-S19 段图）、docs/stream-volume.md（stream.bin §3 表与音量节点）。
