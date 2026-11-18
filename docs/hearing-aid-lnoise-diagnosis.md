# 真机左耳噪声离线诊断（路 LNOISE / sim-lnoise）

2026-11-18 22:18–23:0x。**触发**：HAL5 合一候选（merge 628de93，sha
`9ad0f088…`，MPO=95）当晚刷入真机，双击多功能键开辅听后：**右耳正常（听到放大的
环境声）、左耳严重噪声**——mic_effect(0x9c2d) 链在真机的第一个运行时反馈。本路纯
离线诊断（反汇编官方 app.bin `18f3d573…` + 官方 stream.bin `19c9deeb…` + HAL5 产物
逐字节对照），不改 tools/；一次性脚本在 ~/tmp/lnoise。codex 参谋
`~/tmp/codex/202611182244-lnoise/final.md`（gpt-6-astra，已合并其三处误读修正）。

证据强度沿用仓约定：[静态]=指令锚定反汇编；[镜像]=stream.bin/公开字节直接解析；
[SDK]=参考 SDK/IR；[文档]=既有 docs；[推断]=以上推出。

## 0. 一句话结论

**HAL5 的镜像改动排除为主因**（stream.bin 全差集=恰 9B 对称 WDRC 拐点；追加段不碰
音频数据路径）。左耳噪声最可能是 **0x9c2d 这条从未被官方激活过的 pipeline 自身的
输出侧缺陷**——首选假设 A′「链上某处声道数/位宽/帧布局不一致，导致 DAC 交织帧的左
声道槽拿到的不是有效样本」（如某节点每帧只写一个槽而写指针按双声道推进），次选
B「db3c(1,1) 异步关 ANC 与辅听 start 的竞态」。鉴别实验①②（见 §6）可在 2 分钟内
把 B 分出去。注意：本路撤销了此前口头分析中「3 个 DacEff 抢 DAC」的读法——图尾
经节点复用后**只有一个 DAC 输出节点**。

## 1. 任务④：硬件形态（先关掉 TWS 假设）

H3S = QCY **头戴单机**（JD 商品页「头戴式 7 麦 56dB ANC」；docs/audio-input-paths
§0/§1.5 已钉），左右耳 = 同一 BR28 SoC 的 DAC 左右输出 + DAA 模拟通道，**无 TWS
跨耳路由**。任务④ TWS 分支就此关闭。

## 2. 任务③：HAL5 vs 官方 stream.bin 全差集 → 排除为主因

[镜像] HAL5 构建产物 stream.bin（`~/tmp/hal5-probe/full95b/`，构建器随候选写出）与
官方基线逐字节 diff：**全文件恰 9 字节不同**，全部落在 Drc1Eff 76B blob
（绝对偏移 @0x18a9，见 §8 勘误）的三个 float：

| 偏移 | 官方 | HAL5 | 字段 |
|---|---|---|---|
| 0x18b9 | 9a 99 b4 42 (90.3f) | 00 00 be 42 (95.0f) | th[1].in |
| 0x18bd | 9a 99 b4 42 (90.3f) | 00 00 be 42 (95.0f) | th[1].out |
| 0x18c5 | 9a 99 dc 42 (110.3f) | 00 00 be 42 (95.0f) | th[2].out |

th[0]=(0.3,0.3) 与 th[2].in=110.3 不变（0x18c1 处 110.3f 原样）。即 MPO 曲线
(0.3,0.3)/(95,95)/(110.3,95)——末段斜率 0、限幅更保守（95<110.3）。

**为何排除**：Drc1Eff 是 0x9c2d 图唯一 WDRC（§5 记录核对）；TWOPOINT（mode=1）
是立体声双采样（第二采样复用第一采样增益，L2 §1 IR 证据）→ 同一曲线对称作用于
整帧，无左右分叉；限幅值变小只会更安静，不产生噪声。HAL5 其余 diff（内容账
916B）= 追加段载荷代码 + 8B 接驳窗 + fence 3B + CRC/封装，全部不在官方音频数据
路径上（open/close/toggle 桩在 L1KEY/L4 时已是同形状并经独立审计）。codex 保留
意见：E「完全不可能参与」说得过满——MPO 不是全链声压保证、对称参数不消除既有
不对称数据——但作为**单侧噪声的主因**置信度很低。

## 3. 任务①：输入侧——单 mic，且落在左耳 LFF mic 上 [静态+镜像]

- MicEff 40B blob（stream.bin @0x1da4）：**en_map=0x1 → 只开 ADA 数字通道 ch0**
  （audio-input-paths §1.8① 既有钉死，本路复核）。开链（0x6032c0c 逐 en_map 位）
  不写 MIC_CTL1——ch0 的模拟 mux 用现值。
- MIC_CTL1(0xFE0908) adc0_sel 的最后写者：ANC 重配链 0x606ec24→0x606c8aa 写
  adc0_sel = mic_type[0] = **A_MIC1 = LFF = 左耳前馈 mic**（audio-input-paths
  §1.6④：mic_type=[A_MIC1,A_MIC2,A_MIC0,A_MIC0]；mic_management mp[1]=LFF）。
- 用户操作序：开过 ANC（双击后听到「关闭降噪」提示音 = db3c(1,1) 走了 OFF 请求
  = 此前 ANC 在跑）→ MIC_CTL1 已被 ANC 写成上表 → **辅听采集的物理 mic 是左耳
  LFF**。boot 平台表（config 59，公开工件外）的默认 mux 值未知，但只要 ANC 开过
  一次即被覆盖。
- 含义：**这条链从设计上就是单 mic 单声道输入，无任何双耳平衡处理**——但单 mic
  坏数据会同时进两只耳朵（若输出对称复制），单独解释不了「右耳正常左耳噪声」的
  空间分离（codex 同判：D 假设降级，除非与输出不对称/ANC 残留组合）。

## 4. 任务②：输出侧全图钉死（本路新增大头）

### 4.1 0x9c2d 图 26 边全解 + 节点类名还原 [镜像+静态]

边表 8B/边（src{uuid,variant,port}+dst），经路 CP/probe_pi32v2 L1566 注释与 codex
复核确认。节点类名从 ops 适配器注册表（0x60a8dd0 起，每条 24B {process, name_ptr,
uuid16, op0_bind, op1_ioctl, op2_release}，**实际延展到 0x60a90d8+，既有文档的
[0xa8dd0,0xa9070) 只是子集**）还原：

```
MicEff(ADC) → NoiseGateEff → VolEff → Eq1Eff → Drc1Eff → FshiftEff →
HspressEff → VchangerEff → sound_splitter(0xd911/2)
   ├─ p0 → plate_reverb(0x5101/1) → ChannelMerge491(0xbf8e/1) ─┐
   ├─ p2 → echo(0x98a4/1) ────────────────────────────────────┼→ 3BandMerge(0x1b9d/2)
   ├─ p4 → Eq4Eff(0x737b/8) ──────────────────────────────────┘        │
   └─ p1/p3 → 空节点(0x0000)（未连接端口声明）                          ↓
                          Eq5Eff(0x737b/9) → bit_width_convert(0x7de5/7) → DacEff(0xdccd/9)
```

**关键修正（codex，采信）**：尾部 `3BandMerge→Eq5Eff→bitconvert→DacEff` 三段边
各重复 3 次（(0x1b9d,2,p0)→(0x737b,9,p0)×3 等），而 0x60746e6 建节点时**按
(uuid,subid) 查找、命中即复用**（路 O 仿真第 12 边停在复用搜索 0x60746cc 同一机
制）→ **图尾只有一个 3BandMerge/Eq5Eff/bit_width_convert/DacEff 节点**，三条支路
（混响/回声/EQ4）汇入同一个合并节点。「3 个 DacEff 抢 DAC」的读法撤销。

对照正常场景（本路五图全解析）：tone 图 = MIXER1→DAC1 单输出；esco 图 =
bitconvert(0x7de5/2)→DAC3 单输出；**只有 mic_effect 图是三支路汇聚结构**——这条
拓扑从未在官方固件上运行过（L1-wiring 钉死激活层被链接器裁掉），其正确性无任何
官方运行证据。

### 4.2 DacEff 配置 blob = 纯默认值 [镜像+静态+SDK]

DAC1/2/3/6/DacEff 五个 §5 记录共享 6B blob `32 00 08 00 00 00` = {u16 50, u16 8,
u8 0, u8 0}。codex 对照参考库结构还原为 **{delay_time=50, protect_time=8,
write_mode=0, dev_properties=0}**，且恰等于固件无 blob 时的默认分支
（0x607665c：h=50、h=8、b=0）——**不是声道/格式配置**。声道/位宽/帧布局靠运行时
fmt 协商（0x60765d2 tbb 分派 → 0x6075772 按 (uuid,variant) 查 §5；ADC 侧 SR 三态
16000/全局/44100 由 cmd 0x20006 模式字节驱动，模式字节运行值未钉——audio-input-
paths §1.8⑤(b) 既有开放项）。0x60765c2 的 b[ctx+24]=255 是 reference_network
（同步参考网络）字段非「广播」；0x607666c 的 255 匹配链表遍历是 DAC **状态回调**
分发非 PCM 广播（codex 对照参考库，两处 255 之前被串读）。

### 4.3 DAC 硬件面 [静态+SDK]

JL_AUDIO（br28.h + AE §2.1）只有**单组 DAC DMA**（DAC_ADR/LEN/PNS/HRP 各一）→
数字输出是交织帧（帧=ch×ssize），L/R 由帧内槽位承载，模拟侧 DAA_CON0..4 + ADDA
族（0x606abf0 模拟 init 的 b[ctx+21]=通道数、ctx+40..43 每通道配置；0x6071e74 启
动族写 DAC_CON/DMA 五寄存器）。0x60729xx 域是 DAC 输出设备的环形缓冲管理，样本
拷贝循环（0x6072984）按 stride=协商通道数做交织/解交织拷贝（16/32bit 双路径）。

## 5. 假设排序（合并 codex 后）

| # | 假设 | 判断 |
|---|---|---|
| 1 | **A′ 数据通路声道/位宽/帧布局不一致**（广义 A：不止 DMA 末端，覆盖声道展开点、ChannelMerge/3BandMerge、位宽转换、DAC fmt 协商） | 最符合「稳定的左右分离」。codex 关键洞察：mono 误当 stereo 通常得 L[n]=x[2n]/R[n]=x[2n+1]（两耳都坏），**「右好左坏」需要更具体的机制**：每帧只写一个槽而写指针按双声道推进；声道展开后左路无有效来源；某槽步长/字宽错；或左右独立状态只有左路未初始化 |
| 2 | **B ANC 退出竞态**：db3c(1,1) 是请求（L1KEY §5.2 已钉「返回≠anc_task 硬件退出完成」），open 不等完成即 start；ANC 的 JL_ANC 模拟注入（DAA_CON2 族，0x606e212/0x606e228 位写）与 MIC_CTL1/ADA 增益的重配是异步发生的，且 ANC 左右耳滤波独立（LFF/LFB vs RFF/RFB 共用）——残留/重配可能单侧表现 | 若故障依赖「首次开启」或先前 ANC 状态则升到首位 |
| 3 | **F 效果支路反馈/状态异常**（新补充）：单 mic 后链内可产生多声道；plate_reverb/echo 支路或啸叫抑制（HspressEff/FshiftEff 移频族）在真实声学闭环下自激，可能单侧 | 与佩戴耦合实验⑤区分 |
| 4 | C 多 DAC 句柄互抢 | **降级**（节点复用证据） |
| 5 | D 纯输入侧（mic 坏） | 单独解释不了空间分离；与 1/2 组合仍可能 |
| 6 | E HAL5 9B WDRC 参数 | 很低（对称+更保守；见 §2 保留意见） |

## 6. 真机鉴别实验（不刷机、耳朵+按键，按鉴别力排序）

全部低音量短时观察，噪声一出现就关：

| # | 操作 | 区分 |
|---|---|---|
| ① | 首开出现噪声→关辅听→等提示音/过渡结束再等 ~5 秒→**再开一次** | 首开坏、二次好 → B/首次初始化；两次同样坏 → A′/F。（注意：二次 open 仍会调 db3c，OFF→OFF 是否触发重配需观察提示音有无） |
| ② | 留意噪声出现时刻：第一声就有 / 与关 ANC 提示音重叠 / 提示音结束后才出现；重启后再比 | 先正常后突然坏 → 异步重配（B）；一开始就固定坏 → 格式/状态问题（A′） |
| ③ | 关辅听后听普通提示音/音乐/ANC 通透是否正常（再重启对比） | 关后普通声音仍左侧异常 → 共享 DAC/模拟资源受损（B 的重配破坏面）；普通路径正常 → 辅听局部数据问题（A′） |
| ④ | 辅听运行中只降音量，分别记录右耳环境声与左耳噪声变化 | 左噪声不随音量变 → 噪声在音量控制点之后/独立模拟路径；两耳一起变 → 共同数字路径 |
| ⑤ | 短时改变佩戴耦合（耳罩离耳一点，勿吹/敲 mic） | 噪声随耦合剧烈变化、有起振阈值 → 反馈自激（F）；固定一侧性质不变 → A′/B |

补一个低成本旁证：**换一个安静 vs 嘈杂环境重试**——左耳噪声内容随环境声变化
→ 含 mic 成分（A′ 的左槽读到错位 mic 数据/F）；与环境无关的白噪/数码噪 → 未初
始化缓冲/配置（A′ 的空槽/B）。

## 7. 修复方向（确认后）

- **A′ 确认后**：先离线钉死 MicEff→…→DacEff 每站的 fmt（声道数/字宽/交织布
  局/有效长度）与实际输出字节——0x60767ae 起已有位宽与声道模式协商逻辑，不能假
  定框架没做转换；用递增序列/全零/脉冲做离线输入、输出缓冲预填哨兵、覆盖两次环
  形绕回与部分写入，定位第一处错位。若确缺 mono→双声道转换：在**第一个要求双声
  道的消费者之前**接一个有明确契约的转换（输入 N 个 mono 样本→输出 N 帧
  L=x/R=x，同步更新格式声明/长度/消费偏移，16/32bit 分别处理）；修复落点优先
  （a）改含义已钉死的参数 blob，（b）追加段接框架可安装节点/回调入口；不能把参
  数修改包装成拓扑修复。
- **B 确认后**：open 载荷改为「请求退出 ANC → 拿到完成证据 → start」；鉴别阶段可
  先用固定延时候选（如 db3c 后延时 N×100ms 再 start，N 可调做二分），正式修复等
  实际完成事件/已证状态位，且不得阻塞执行退出的线程。
- **回滚决策参考**：本次真机操作用户未写过 key30/key31 → HAL5 的 apply_mpo/
  apply_eq 在 open 后**双双静默跳过**（L4 桩源码注释自门控语义，key 缺失即跳）——
  即 **HAL5 此次真机行为 ≈ L1KEY-only + 9B 烘焙差**。刷 L1KEY-only（无 MPO/EQ）
  若复现同样左耳噪声（预期如此）→ 链本身问题实锤、HAL5 无需回滚（问题不在这
  9B）；若 L1KEY-only 正常而 HAL5 坏 → 立刻回滚 HAL5，此时唯一音频面差异就是
  §2 的 9B 与 L2 烘焙装载路径本身（th[1] 90.3→95 对增益分段的影响在极端电平下
  理论上可改变 DRC 行为，但对称性使它仍解释不了单侧——需重新审）。

## 8. 勘误与新增锚点（供既有文档修订）

- **§5 记录 off 字段 = stream.bin 绝对偏移**（blob 区 0x10a5..0x1dcc 按记录
  off+size **完美衔接**自证：Drc1Eff@0x18a9→0x18F5 Vol_SysTone→…→0x1da4
  MicEff→0x1dcc）。audio-input-paths §1.7①/§1.8① 的「绝对=0x871+off」公式对
  esco_adc/MicEff 侥幸得到正确地址（两处恰有同形数据），对 Drc1Eff 不成立
  （0x871+0x18a9=0x211A 非 wdrc）；hearing-aid-l2-mpo §2 的「@0x1038」同源坐标误
  ——**Drc1Eff blob 实际 @0x18a9**，HAL5 的 9B diff 落点（+0x10/+0x14/+0x1c）
  与 th[1].in/th[1].out/th[2].out 一一对上。各文档结论不受影响（消费代码按查表
  命中取 blob，不依赖文档坐标）。
- ops 适配器注册表范围：0x60a8dd0 起至少到 0x60a90d8+（28 条口径是子集）；本路
  还原 0xdccd='dac'、0xd911='sound_splitter'、0x7de5='bit_width_convert'、
  0x1b9d='3band_merge'、0x98a4='echo'、0x5101='plate_reverb' 的 {process,
  op0/op1/op2}。
- 0x9c2d 图 26 边（@0x2d4）+ 五图（tone/a2dp/esco/pc/mic_effect）边表全解脚本在
  ~/tmp/lnoise/graph.py（8B/边口径）。

## 9. 开放项（下一步）

1. **fmt 协商运行值未钉**：ADC 节点模式字节（cmd 0x20006）实际值 → SR/ch 三态走
   哪支；DacEff 侧协商后的 ch/位宽——这是 A′ 的核心未知量，需要仿真前沿推进到
   fmt nego 或真机日志。
2. parse 仿真证据边界：路 O 只建出 11 节点、停在第 12 边复用搜索——完整图对象数
   与数据消费未证明（codex 同点提醒）。
3. ANC 关闭完成信号锚点（B 修复需要）：anc_task 硬件退出后有无可查询状态位。
4. DAA_CON2(0xFE0A08) 位段与左右耳 DAC 模拟通道的对应布局（0x606abf0 与
   0x606e212/0x606e228 的位集不同段）未逐位解。

## 10. 证据锚点速查

- HAL5 diff：~/tmp/hal5-probe/full95b/stream.bin vs ~/tmp/ar-miceffcfg/stream.bin
  （9B@0x18b9/0x18bd/0x18c5）；HAL5 ufw sha `9ad0f088…` 复核一致。
- 图/名字表/§5 记录：stream.bin @0x3A8 名字表 53×21B、@0x881 §5 记录 65 条、
  图头 @0x4/0x80/0x1d4/0x248/0x2d0（tone/a2dp/esco/pc/mic_effect）。
- 节点类 ops：0x60a8ea8(dac)/0x60a8f50(sound_splitter)/0x60a90d0(bit_width_
  convert)/0x60a90a0(3band_merge)/0x60a9070(echo)/0x60a9058(plate_reverb)。
- DAC 侧：bind 0x60765b0（ctx+24=255=reference_network）、ioctl 0x60765d2、fmt
  nego 0x6075772、模拟 init 0x606abf0、启动族 0x6071e74、缓冲管理 0x60729xx
  （交织拷贝 0x6072984）、设备注册 0x6073106。
- 输入侧：MicEff blob @0x1da4 en_map=0x1；MIC_CTL1 mux 0x606c8aa（adc0_sel=
  mic_type[0]=A_MIC1=LFF）；ANC 重配调用点 0x606ec24。
- codex 参谋：~/tmp/codex/202611182244-lnoise/{prompt,final,log}.md + 其反汇编
  摘录 ~/tmp/lnoise-review/app-audio-anchors.txt。
