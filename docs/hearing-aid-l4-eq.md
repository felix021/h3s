# 助听 L4「EQ 十段热更」：BLE 参数通道固件侧消费链（路 HAL4 / sim-hal4）

2026-11-17。任务：App 下发的十段 EQ 曲线经 BLE 到固件后真正生效到 mic_effect
pipeline 的输入 EQ 节点 Eq1Eff——补上 hal3 留下的「逐频增益热更未实现」（
docs/hearing-aid-l3-mpo.md 诚实边界 §3）。本路产出**离线镜像 + 静态/字节层验证**，
不刷机、不连真机；候选 UNTESTED。前置：main f5fd5af（hal3 全链：key 30 MPO +
keyhook + key7 toggle + L1/L2 slotram 栈）。

## 0. 一句话结论

codex 参谋（gpt-6-astra，`~/tmp/codex/202611172318-hal4/final.md`）判：**采纳
key 31 新协议 + float32 直传 + 只发 Eq1Eff 十条 subcmd 7 段消息**；推翻主会话
两个关键错读（§1.1 返回极性、§1.2 三节点全发）。本路落地：

1. **固件侧**：新载荷 `apply_eq.S`（读 VM key 31 → 整包先验 → 10 条 EQ_SEG 44B
   段包 `jlstream_set_node_param(0x737b,"Eq1Eff",包,44)`，每条要求返回 1）+
   keyhook 扩展 key-31 读写分支 + toggle 桩 open 成功后 apply_mpo → apply_eq；
   构建器开关 `--hearing-aid-l4-eq`（隐含 hal3 全链）。
2. **App 侧**：KeyStoreFrame 88B 值协议编码（`encodeEqValue/decodeEqValue` +
   `buildEqWriteCommand`）+ bridge `setHearingEq(segsJson)` + 助听 tab 在 hal3
   保存按钮旁加「保存十段 EQ」按钮（index.html 最小改动，另一路 sim-haui 在
   大改助听 tab，避免撞车）。
3. **re-pin**：keyhook 字节变化使 r4-keyhook 候选家族作废重钉（1873027b… →
   2576df46…，同 hal3 勘误先例）；l1_slotram 候选不受影响。
4. **验证**：载荷/镜像两层单测（10+12 断言）+ KeyStoreFrameTest.eqCodec +
   android-query 11 套 + 独立校验器 + candidate_world_checks 全绿。

## 1. 消息通道证据（本路逐指令反汇编钉死，codex 复核修正）

### 1.1 eq ioctl 的 subcmd 7 = EQ_SEG（44B 段包）

eq ioctl 0x0607898e（注册表 eq@0x060a8f84，uuid 0x737b）的 0x2000F 分支
（0x06078a04）：subcmd = [param+0] - 2，tbh 表 @0x06078a1a（target = 表基 +
2×entry；key0=subcmd2→0x06078a2e 与路 BT 钉死一致，验证表语义）。
**subcmd 7 分支 @0x06078a9a**：

- param+4..20（16B）→ 栈上 eq_seg_info；param+20..44（24B）→ 栈上淡入结构；
- 要求 [ctx+16]（运行句柄，r6=ctx+16）与 [ctx+28]（参数缓存）非空；
- call 0x0607c6aa(run句柄, fade24, seg16, sext8(缓存+4 位域)) → 0x0607c5c4
  工作者：seg.index（h[r4+0]）×16 定位运行段数组槽，`rep` 整段 16B 复制；
  fade24 自带（+0/+1 写 run+116/117，0.1f 步进等），**不依赖 subcmd 8/11 先行
  初始化**（ctx+72 的分配属于 subcmd 11 @0x06078aee——主会话原把它错归
  subcmd 8，codex 纠正）。

### 1.2 两处生产者先例（消息形状逐字节一致）

| 先例 | 位置 | 序列 |
|---|---|---|
| QCY 自定义 EQ 应用循环（0x22 命令消费端） | 0x0602503a | 每名字（MusicEqBt/Eq0Media）：subcmd8{8,master,0,fade} → subcmd3{3,seg_num,fade} → 10×subcmd7{7,seg16,fade}，全 44B 经 0x0607538a(0x737b,name,sp,44)；8/3 后查 `if(r0==0) 跳下一名字`，段循环不查 |
| stream.bin 文件配置应用循环（节点 open 装载） | 0x06024f22 | 0x060751da 名字哈希解析（节点 open 0x0607882a 也用它）→ 同形状 subcmd8/3/7×n，不查返回值 |

fade 区常量（packet+20..44）：`{u8 10, u8 100, f32 0.1f, 余 0}`——两处先例
立即数逐字节相同，本路原样烘进段包模板。

### 1.3 返回值极性（codex 纠正主会话错读）

eq ioctl 入口 `r4 = 1`（0x06078a0c），尾部 0x06078ba4 读 **[r6+28] = ctx+44
状态字**（非 0 时覆盖 r4，如 open 失败 -2）——不是 ctx+28 参数缓存指针（那是
[r6+12]）。即：**常规 subcmd 7 返回 1**（=分发未报错，不证明段参数已在音频块
生效）；0x0607538a 另可透传 -12（分配失败）/-22（名字无节点）/21（队列满）。
本载荷**每条消息都要求 r0 == 1**，否则停止本轮（返回 2）——官方生产者的
`if(r0==0)` 查法（只查 0、只查前两条）不能建立这个门。

### 1.4 拓扑：只发 Eq1Eff（codex Q6 + 本路独立复核）

stream.bin §0（管线图节，928B）的边表区（@0x2d0 起 8B 记录
{u16 uuid,u8 subid,u8 port}×2 端点链）解出 mic_effect 主链（§2 名字目录对
subid 反查）：

```
ADC(MicEff) → NoiseGate → VolEff → Eq1Eff → Drc1Eff → Fshift →
HowlingSuppress → Vchanger → sound_splitter(d911) → [支路 …] → DacEff
支路含：3band_merge → Eq5Eff → 7de5 → DacEff；Eq4Eff → 3band_merge port2
```

关键边：0x2e8 VolEff→Eq1Eff、0x2ec Eq1Eff→Drc1Eff、0x33c 3band_merge→Eq5Eff、
0x37c splitter→Eq4Eff、0x384 Eq4Eff→3band_merge。**Eq1Eff 是 MPO（Drc1Eff）
上游的输入 EQ；Eq4Eff/Eq5Eff（§5 记录 subid 8/9，与 Eq1Eff 共享 blob @0xeac）
在 splitter 之后的输出支路（MPO 下游）**——同曲线全发会在限幅器之后重复整形、
改变 MPO 后输出电平（甚至把输出推过 MPO 上限）。共享 blob ≠ 三节点该一起更新
（各自 open 时独立分配参数缓存/运行状态）。**只发 Eq1Eff，10 条消息。**

### 1.5 官方 Eq1Eff blob（模板常量来源）

stream.bin §5 @0xeac 168B：global_gain=0、{u8 bypass=0, u8 seg_num=10} 位域
（+4..7 = 00 0a 00 00，此前文档的「大端 u16 段数」按位域勘误）、10 段全
iir=2（band_pass）、q=0.7f、freq=25/53/112/238/505/1070/2268/4807/10190/21599、
gain=0、index=0..9 顺序。段包模板的 iir/q 与此逐字节同源（单测对官方
stream.bin 交叉断言）。

## 2. key 31 值协议（App ↔ 固件对齐，88B）

```
+0..1  魔数 "HA"（0x48 0x41）      +2 版本 2      +3 保留 0
+4     段数，必须 == 10             +5..7 保留 0
+8+8i  段 i = { u16 freq LE(Hz), u16 pad(固件忽略), float32 gain LE(dB 位型) }
```

- **整包先验后才发第一条**（codex：不要第九段才发现非法值、前八段已生效）：
  固件侧头两字 == 0x00024148 / 0x0000000A（一次覆盖魔数/版本/保留/段数）+
  每段 freq ∈ [1, 21599]（官方 blob 观测域，非算法验证上界）+ 每段
  |gain 位型| ≤ 0x42200000（40.0f：正 float 位型单调，一个幅值上界同时挡
  超范围/NaN/±Inf，对称覆盖 ±40dB，零浮点运算）。App 侧同门先挡一道。
- **float32 位型直传**（App 预编码，固件纯位拷贝，沿用 apply_mpo 手法）；
  codex 备选 48B 纯增益协议（频点固化十点）更省但失去频点可调性，未采纳
  （88B 单帧 100B < MTU247，无压力）。
- 40dB 上限口径：覆盖 L0 演示的 +39dB；MPO（Drc1Eff）在 EQ 下游兜底输出，
  但救不了上游系数异常——NaN 必须拒。
- key 31 属 27..36 空 key 段（路 K），与 key 30 同类，VM set 仅校验 len≤4095。

## 3. 固件载荷（tools/hearing-aid/apply_eq.S，62 insns / 180B 代码 + 60B rodata）

```
apply_eq():
    [--sp] = {rets, r9-r4}; sp += -132; r4 = sp   # 帧 = 88B key buf + 44B 段包
    get_config(31, sp, 88) @0x06000f88            # r0 != 88 → 跳过（未配置）
    [sp+0] == 0x00024148; [sp+4] == 0x0000000A    # 头两字门
    十段循环：freq h 门 (0, 21599]；gain 幅值门 |bits| ≤ 0x42200000
    r9 = 0x0607538a; r5 = eq_name("Eq1Eff"); r8 = 0
seg 循环 ×10:
    模板 44B（eq_pkt rodata）→ 栈段包（11 字循环拷贝）
    h[包+4] = r8（index=i）; [包+8] = h[freq](零扩展); [包+12] = gain 位型
    jlstream_set_node_param(0x737b, "Eq1Eff", 包, 44)
    if (r0 != 1) → 停止（返回 2）
    r0 = 0（全部提交）/ 1（跳过）/ 2（中途停止）
```

**段包模板 44B**（rodata）：`{u32 7, u16 index(运行时写), u16 iir=2, i32 freq
(运行时写), f32 gain(运行时写), f32 q=0.7f, u8 10, u8 100, u16 0, f32 0.1f,
16B 0}`——iir/q 与官方 blob 逐字节同源，fade 常量与两处生产者先例同源。
名字 16B `"Eq1Eff\0"`+补零（框架按 NUL 截断 strcmp 匹配，非哈希）。

**接驳**（mic_effect_toggle_l4_hook.S，16 insns / 52B）：hal3 桩 open 分支
再加一条 `call apply_eq`——open 成功 → apply_mpo（key 30 MPO）→ apply_eq
（key 31 EQ），两载荷各自自门控互不依赖；close→open 每次重读重应用。段布局：
open(156) + close(64) + apply_mpo(128) + mpo rodata(92) + apply_eq(180) +
eq rodata(60) + 桩(52) + keyhook(142) = 874B；EQ_*_ADDR .equ 两遍定址
（MPO_* 同契约），fence 上界 0x060E33DE。

## 4. keyhook 扩展（tools/r4cmd/keystore_hook.S，54 insns / 142B）

按 data_len + data[0] 分路双 key（表）：

| 形状 | 动作 | 回执 |
|---|---|---|
| len=9, data[0]=30 | set_config(30, data+1, 8) | `30 01 status` |
| len=89, data[0]=31 | set_config(31, data+1, 88) | `30 01 status` |
| len=1, data[0]=30 | get_config(30, resp+2, 8) | `30 08 <8B>` |
| len=1, data[0]=31 | get_config(31, resp+2, 88) | `30 58 <88B>`（90B < 128B 缓冲） |

四个 API 锚点装载（set×2/get×2）各自紧跟 call r6；两个写分支都保持 hal3 的
resp+2 字节直写勘误形状。**字节作废口径**：keyhook 增量改变 r4-keyhook 候选
家族字节（1873027b…/630B → 2576df46…/639B；hal3 候选 88483a67… → 279c586a…）
——r4-keyhook 家族以本路产出为准（路 L3 勘误同款流程）；l1_slotram 候选
6f267a2e… 不含 keyhook，不受影响（实证构建字节不变）。

## 5. 构建与验证

- 开关 `--hearing-aid-l4-eq`（隐含 --hearing-aid-l3-mpo → --r4-keyhook 全链；
  与 --hearing-aid-l1-key7 互斥同一接驳窗）。候选 sha256
  `2b31e0203bfde1478cb11676334e6eba49e7a5d8af23932505a183fd9bfb7323`，
  diff 943B。独立校验器 verify_ufw_integrity.py 全过。
- 单测 `test_hearing_aid_l4_eq_payload.py`（10）：往返零 diff、API 锚点装载、
  常量集、整包先验全部先于首条发送、段内三 patch store 精确形状（index 半字
  /freq/gain）、模板对官方字节（含活 stream.bin 交叉：十段 iir=2/q=0.7f 逐段
  断言）、16B 名字域、.equ 双值布局不敏感、栈帧平衡、循环界=10 与三返回值。
- 单测 `test_hearing_aid_l4_eq_image.py`（12）：段几何（含交错 rodata 数据洞
  的 listing 数据条目）、open/close 前缀==独立汇编、decode 六旗、桩调用序
  [open→apply_mpo→apply_eq→close]、组合段内 key-31 分支形状、两组 rodata 字节、
  开关误用拒绝、legacy 四变体零回归、候选构建 + 差异区间 + 注册表/fence 窗口 +
  独立校验器。
- re-pin：candidate_world_checks / test_candidate_world_checks /
  analyze_image_trace 的 r4 候选 sha/plain_length(0xE3286)/diff(639)/app 切片
  sha/fence 标注，全链重测绿（含反面用例与 CLI 退出码）。
- App：KeyStoreFrameTest 增 eqCodec（编码字节形状/往返、门六拒、头破损拒、
  与 MPO 值互不误读、写帧 cmdid/data_len/key 字节、读回载荷）；test.sh 11 套
  全过；index.html 双 script 块 parse 冒烟过。

## 6. 诚实边界（必须随候选一起读）

1. **离线候选 UNTESTED，不刷机**。交付=载荷字节与 §1 证据一致 + 四层 CRC/
   JLFS 结构自洽 + 逐字节差异精确可控 + 单测/独立校验全绿。
2. **消费链零执行证据**：apply_eq → 0x0607538a → 框架队列 → eq ioctl
   subcmd 7 → 0x0607c5c4 → 段数组重写的完整路径未在任何仿真/真机跑过（主仿真
   前沿未到 RCSP/任务派发相位；0x0607538a 的同步等待在冻结单上下文会撞 EQPIPE
   同族墙）。r0==1 只证明「消息分发未报错」，不证明音频块已按新曲线滤波。
3. **十条消息非事务性**：段间可能跨音频块逐步生效（fade 0.1f 步进平滑），
   中途失败（返回≠1 停止）会留下部分应用的曲线；key 31 值仍在，下次 open
   重试全部十条。
4. **名字无节点的行为**：框架消费者写 -22 到 msg+20 并 signal（0x06075e3e/
   0x06086b00 链，正常调度必返回）；但等待 timeout=0（无限），流服务任务不
   跑/死锁时无界阻塞——open 成功路径下发消息依赖节点已活（open==0 前置），
   该前提由桩的 `if (r0 != 0) goto done` 保证到「open 返回 0」为止，节点内部
   状态机异常未覆盖。
5. **freq 上界 21599 = 官方 blob 观测域**，非算法验证的合法频带上限（采样率
   未证）；±40dB 增益门是产品口径非固件算法极限（SDK 注释 -18~18 未验证）。
6. **Eq4Eff/Eq5Eff 不发**：输出支路 EQ 维持 open 时的文件默认（全 0dB 平坦），
   其曲线热更未实现（如需，另起一路——注意它们在 MPO 下游的产品语义）。
7. cmdid 0x30 占用残余风险沿用 r4-command-hook §5.4；key 31 是当前镜像未占用
   编号，非厂商永久保留。
8. index.html 的十段 EQ 按钮与 sim-haui 的助听 tab 改版并存风险：本路改动
   限定在 hal3 按钮区 + 同区 JS，无样式/结构改动；合并时如冲突以 sim-haui
   的结构为准重放本按钮。

## 7. 复跑命令

```sh
python3 tools/build_quiet_candidate.py --attenuation-db 0 --hearing-aid-l4-eq \
    --output-dir <不存在的目录>
python3 tools/verify_ufw_integrity.py <产物>/H3S_0.1.1.8_tones_minus0dB_HEARING_AID_L4_EQ_OFFLINE_UNTESTED.ufw
python3 tools/test_hearing_aid_l4_eq_payload.py
python3 tools/test_hearing_aid_l4_eq_image.py
python3 tools/test_r4_keyhook_payload.py
python3 tools/test_candidate_world_checks.py
bash tools/android-query/test.sh
python3 tools/check_local.py --units
```

## 8. 证据锚点

- codex 参谋全文：`~/tmp/codex/202611172318-hal4/final.md`（不入库）；prompt
  同目录 prompt.txt，log.md。
- 消息链：eq ioctl 0x0607898e（tbh @0x06078a1a，subcmd7→0x06078a9a）；
  0x0607c6aa → 0x0607c5c4 → fade helper 0x0607c344（codex 纠正 0x0607c342 是
  上一函数尾）；生产者 0x0602503a（0x060250aa/ce/f8 三调用形）与 0x06024f22
  （0x06024f7a/9c/c6）；返回极性 r4=1 @0x06078a0c、尾部状态字 @0x06078ba4。
- 拓扑：stream.bin §0 边表 @0x2d0..0x3a4（8B 端点链记录）；§2 实例目录
  subid↔名字；§5 记录 subid 8/9/10 共享 blob @0xeac。
- 载荷：tools/hearing-aid/apply_eq.S + mic_effect_toggle_l4_hook.S +
  tools/r4cmd/keystore_hook.S（key-31 扩展）；单测
  tools/test_hearing_aid_l4_eq_payload.py / test_hearing_aid_l4_eq_image.py；
  构建 tools/hearing_aid_l1_image.py（EQ_*/L4_* 常量、_verify_eq/
  _verify_l4_toggle_stub、交错数据洞 listing）+ tools/build_quiet_candidate.py
  （--hearing-aid-l4-eq）。
- App：KeyStoreFrame.encodeEqValue/decodeEqValue/buildEqWriteCommand/
  parseEqReadPayload + H3SBridge.setHearingEq + tests/KeyStoreFrameTest.eqCodec
  + assets/index.html（haEncodeEqValue / haSaveEq 按钮）。
- 勘误：本页 §1.5 的 blob +4..7 位域口径（旧「大端 u16 段数」表述作废）；
  docs/hearing-aid-l3-mpo.md §1.1 的「subcmd 7 实为 EQ_SEG 44B 段包」维持，
  但其「16B 段+24B 淡入参数+priv/callback」中的第四参实为段数位域（本页
  §1.1）。
