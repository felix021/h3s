# 独立 App 对接协议调研（路 CS / sim-appproto，只读预研）

2026-11-13。本页服务 R3「独立 app 对接新功能（EQ 调节/按键重映射等）」：把独立 app 需要对接的
BLE 服务/特征、EQ 调节/ANC 切换/音量/按键重映射各自走哪个通道与命令格式、以及用户观察到的
「QCY App EQ 调节最大 +8/-8」这个限制到底落在哪一层，逐项钉清楚。**只读**：未改 tools/ 任何
代码、未跑改机器状态的 probe、未连接真机；证据来自仓库既有 docs/data（协议笔记、控制面板 JSON、
App 反编译摘要、EQ 载荷语义）、路 CN/CJ 的算法侧结论与 ANDROID-query 客户端源码。私有 ROM/
APK/会话字节不进本文。

证据强度：**[静态]** = 仓库既有反汇编锚定/控制面板 JSON 字节；**[文档]** = 既有 docs/HANDOFF
钉死条目；**[SDK]** = 钉死参考 SDK（参考材料非精确 QCY 源）；**[推断]** = 上述链推出；**[未钉]**
= 仓库内无证据、如实标注。

## 0. 一句话结论

独立 app 只需要复刻**一条 RCSP 命令通道**（QCY 服务 `0000a002` 的 write `00000001` + notify
`00000002`，MTU 247→GATT 有效载荷 244B）即可覆盖 EQ/ANC/音量/按键全部读写，命令都走 JL RCSP
的 `opcode=0xFF` 自定义命令 + 少量标准 opcode。EQ 的「+8/-8」是** (a) App UI/控制面板层限制**
（`maxdb:8`/`mindb:8`，可下发的面板 JSON），**不是固件能力**——算法侧 gain 是 float、无 clamp
（路 CN），同一面板里的预设曲线就带 -17/-14/-2.5 等超出 ±8 的值并经同一条命令下发，说明传输/
固件接收不按 ±8 钳。独立 app 直接发大值即可，真正约束在输出端限幅（路 CN §3）。

## 1. BLE 对接面（GATT）

[文档：docs/protocol.md] + [静态：tools/android-query MainActivity] 三方一致：

| 项目 | 值 |
| --- | --- |
| Service | `0000a002-0000-1000-8000-00805f9b34fb` |
| Write 特征 | `00000001-0000-1000-8000-00805f9b34fb`（实测属性 4 = WRITE_NO_RESPONSE） |
| Notify 特征 | `00000002-0000-1000-8000-00805f9b34fb`（实测属性 16） |
| CCCD | `00002902-0000-1000-8000-00805f9b34fb` |
| ATT MTU | 实测 247（GATT 有效载荷 = MTU−3 = 244 字节） |

连接顺序：discoverServices → 找到 A002/1/2 → `setCharacteristicNotification(rx,true)` →
写 CCCD `ENABLE_NOTIFICATION_VALUE` → 等 CCCD 回调成功 + 捕获 `onMtuChanged` 实际值后才初始化
SDK（Android 可能主动回报 MTU，不能只在自己调 `requestMtu(517)` 后接受回调，也不能只看请求了
517 就假定 MTU=517）。Write 用 `WRITE_TYPE_NO_RESPONSE`（属性 4）。

[静态] 控制面板 JSON（`data/controlpanel_qcyh3s_16764.json`）里每个功能还带一个 `character`
字段：EQ 面板=`0000000B`、恢复默认/清除配对=`0000000A`、LDAC/双设备=`00001001`——**这些 16 位
后缀的 GATT 级含义 [未钉]**（是独立 BLE 特征、还是 JL「功能特征」选择子，仓库无证据）。但
[文档：docs/app-analysis.md] 明确 ANC 设置走 `sendCustomCMD → JL RCSP SDK → QCY A002/1 BLE
写入`，[静态：docs/eq-toggle.md] 明确 EQ 设置 `setEQWtihQFG2()` 用自定义命令 `0x22`——即
**EQ/ANC 都作为 opcode=0xFF 自定义命令经 A002/1 下发**；android-query 客户端也只连 A002/1/2
就能完成全部查询。独立 app 先按「全部走 A002/1」实现即可，若真机 EQ 写入在 A002/1 上无响应，
再排查 0x0B 特征（低优先级）。

## 2. RCSP 帧与命令

### 2.1 帧格式 [文档：docs/protocol.md]

```text
FE DC BA | flags | opcode | length_BE16 | body[length] | EF
```

固定开销 8 字节。flags：`C0`=命令需响应、`80`=命令/通知不要求响应、`00`=响应。命令 body 通常以
**SN**（1 字节序号）开始；响应 body 以 **status、SN** 开始。length 是 body 长度；接收可能跨
通知分片、也可能一次多帧，必须按 length 与尾字节 `EF` 重新组帧。

### 2.2 EQ 调节（写） [静态：docs/eq-toggle.md + controlpanel]

- 通道：opcode `0xFF` 自定义命令，cmdid `0x22`（App 方法 `setEQWtihQFG2()`）。
- 固件注册点：`app.bin+0xa9a78`——getter `0x0602580a`、setter `0x0602583c`、应用当前参数例程
  `0x06025118`；配置保存 key 42。
- 载荷格式：**3 字节头 + 每段 7 字节**，至少含 EQ 类型、master gain，以及各段的频率/增益/Q/
  IIR 类型。[未钉] 各字段的精确字节宽度/字节序未在仓库内完整钉死——需 vendor objdump 重解
  `0x0602583c` 才能逐字节钉死（见 §4.4）。
- 控制面板给出滑块/曲线参数（type=2「音乐均衡器」，`count=10` 段，默认 freq
  `32,64,125,250,500,1000,2000,4000,8000,16000`，`q`/`iitType` 默认全 2=band_pass，
  `mastergain:-4`，`volumelevel:4`，`basedb:0`，`maxdb:8`/`mindb:8`）。预设 6 条（默认/流行/
  重低音/摇滚/柔和/古典）+ 空间音效(id 10)；预设曲线值含 -17/-14/-2.5 等超出 ±8 的值。
- 预设选择**下发整组参数**，不是「预设编号布尔开关」；固件侧 `0x06024fe6("MusicEqBt", index)`
  是「按 index 装载配置项」，不是 `enable(name,bool)`。

### 2.3 ANC 模式切换（写） [文档：docs/app-analysis.md + docs/protocol.md]

- 通道：opcode `0xFF` 自定义命令，cmdid `0x17`（App `setANCSenseMode(mode,scene,strength)`）。
- 载荷：`17 03 mode scene strength`（mode/scene/strength 各 1 字节）。
- QCY 线协议编号（勿与固件内部 OFF1/ON2/TRANS3 混淆）：

```text
默认降噪 17 03 01 01 02
默认通透 17 03 03 01 04
正常模式 17 03 02 00 00
```

- 只读查询：`FE 01 17` → 返回 `17 03 xx xx xx`。
- 固件注册点：`app.bin+0xa9ae8`，getter `0x06025c84`、setter `0x06025ca4`、回调 `0x06025d6a`
  （保存 key16/len3 → 应用 `0x0600dcee`：QCY mode1→内部 ON2、2→OFF1、3→TRANS3）。

### 2.4 音量控制 [未钉/文档]

- **媒体/播放音量**：仓库内**没有** BLE RCSP 音量命令的证据。候选路径：(a) 经典蓝牙 AVRCP
  绝对音量（A2DP 域，不在 BLE RCSP 里）；(b) JL RCSP 标准音量 opcode（本机未观测到）。[未钉]，
  独立 app 若需媒体音量建议先按 AVRCP 实现（standard Android），或用 BLE 音量命令做真机对照
  验证。
- **提示音音量**（设备设置，非媒体音量）：opcode `0xFF` cmdid `0x1d`(29)，值 3/6/8=最小/中等/
  最大（[静态：controlpanel]）。写命令帧形状已由路 P 反汇编钉死：data=`value 占位`（第 1 字节是
  值、第 2 字节固件忽略、len>=2、值不钳），查询响应=`1d 02 value ??`——见
  [docs/cmd-0x17-0x1d-layout.md](cmd-0x17-0x1d-layout.md)。
- 固件侧数字音量硬件 = `DAC_VL0`(0xfe051c)（setter 钳位到配置槽上限 `[0x1265c0+1883*4]`）+
  stream.bin volume 节点参数（[文档：docs/appcore-tail-sfr-semantics.md / stream-volume.md]）——
  这些是「改固件参数」域，不是 BLE 命令域。

### 2.5 按键重映射 [静态：docs/eq-toggle.md + controlpanel]

- App：`setKeyFunction()` 把 eventID 拆成 key+gesture → `JLDeviceImpl.setBtnClickFunction()`。
- 两种传输实现（[未钉] H3S 实际选哪个分支）：
  1. 标准 ADV 类型 2 三字节 `[key, gesture, action]`；
  2. 新设备自定义命令 `0x2b`（gesture/action 组合）。**[2026-11-14 路 AO 反证] 固件 App
     重映射命令注册表只有 cmdid 1 组（读/写/恢复默认三件套，0x60a9b00 族），无 0x2b 组——
     候选 B 实际不可达，定案候选只剩 ADV type2（KeyRemapFrame 的 0x2b 分支保留作对照，
     默认仍 ADV type2，见 docs/key-action-map.md §注册表）。
- 控制面板「耳机按键功能自定义」（type 14）：eventID 769(音量-)/1281(音量+)/1793-1796(多功能
  单击/双击/三击/四击)，动作 cmd 0..7（0=无作用 1=播放/暂停 2=上一曲 3=下一曲 4=语音助手
  5=音量+ 6=音量- 7=模式切换）。多功能键双击默认 cmd=7。
- 固件处理 `0x060252c6`（按键映射 handler），`0x06025382` 按动作编号跳转；动作 11 走 ANC
  `0x06018b60`。**没有可绑定的隐藏 EQ 开关编号**（动作 9 返回已处理但无 EQ 调用）——要「按键
  切 EQ」必须改固件（这正是 r2 实验做的事），独立 app 不能只靠发命令实现。

**App 侧实现状态**（路 AM/sim-keyremap-ui，`tools/android-query`）：

- 两条 wire 候选**都已实现**（`KeyRemapFrame.java`，单测覆盖往返+边界）：
  - 候选 A（默认）「ADV 类型 2」：opcode `0xC2` + 4B 大端 type2 mask（`00 00 00 04`）+ 三元组
    `[key, gesture, action]`。**帧形状为 [假设]**——C1 读（opcode+SN+4B mask）已实测钉死
    （§2.6），写对偶的 opcode 值（C1+1）与「mask 后跟值」的扩展未钉，真机抓一次
    setKeyFunction 实包即可定案；
  - 候选 B「自定义 `0x2b`」：opcode 0xFF cmdid `0x2b`，data=三元组（「gesture/action 组合」
    的字节序按 eventID 拆解结果直存 [未钉]）。
- 字段域（两端互证）：key=逻辑键 {3=音量-, 5=音量+, 7=多功能}（eventID 布局 0x{逻辑键}0{连击数}
  ×固件 BW handler 掩码 0x09050307，docs/d1-acceptance.md §6.1）；gesture=连击数 1..4
  （面板钉死；固件默认表 [7,2,7] 的 2 与双击 eventID 低字节互证），**5=长按 [假设]**——App
  面板无长按条目，但固件配置 key 48 是 15 个 3B 三元组映射表（3 键×5 手势恰好 15，
  docs/config-key-map.md）支持 5 手势槽存在；action=0..7（面板）+11（ANC，固件侧）。
- bridge：`H3SBridge.setKeyRemap(eventId, action)`（默认 ADV type2）/`setKeyRemapCustom`（0x2b）/
  `getKeyRemap()`（本地工作副本，初始=面板 defaultFun，非设备读回——type2 查询响应形状未钉）；
  `EqWritePolicy` 白名单相应扩 0x2b 三元组与 C2 type2 两条 setter 形状（字段域从严校验）。
  UI 在 `assets/keyremap.html`（三键×五手势网格），`index.html` 有入口。真机下发未验证
  （红线不变：不连真机；两条 wire 并存放行，抓包定案前默认 ADV type2）。

### 2.6 只读查询（已实测，[文档：docs/protocol.md]）

| opcode | 用途 |
| --- | --- |
| `03` + `FF FF FF FF 00` | 设备信息（固件版本/双备份/VID7250/PID16764 等 LTV 属性） |
| `D4` | MD5 查询（本机能力不支持则 SDK 不自动请求） |
| `FF` + `FE 01 2F/14/10/17` | 电量/定时关机/睡眠/ANC 只读 |
| `C1` + `00000400` | 通用 ADV 只读（type10 ANC 列表属性，H3S 返回 status0 但无该属性） |
| `E1`/`E2` | 升级检查字段/是否允许升级（offset=0/len=0 分支） |

## 3. +8/-8 限制定位

用户观察「QCY App EQ 最大范围 +8/-8」。三选一：**(a) App UI 滑块硬编码** / (b) App 发送时
数值钳位 / (c) 协议层/固件接收层钳位。逐项：

1. **(a) 成立 [静态]**：`data/controlpanel_qcyh3s_16764.json` 的音乐均衡器面板对象里
   `"mindb": 8`、`"maxdb": 8`（`basedb:0`、`count:10`）——这就是 App 滑块的下/上界（min −8 /
   max +8），且是**服务器可下发的控制面板配置**，属 App UI/配置层。
2. **(c 的反面证据，[推断])**：同一面板的 6 条预设曲线（`sys_eq`）含超出 ±8 的段增益值
   （默认 −17、古典 −14、摇滚 −2.5 等），而预设选择**下发整组参数**走同一条命令 0x22
   （[静态：eq-toggle.md]）——若固件接收层按 ±8 钳，这些预设会被改坏、与 QCY 调音意图矛盾。
   这是「±8 不是协议/固件接收层钳位」的强间接证据（非真机测量）。
3. **(c 的算法侧，已证无钳 [文档：路 CN eq-gain-limit.md])**：gain 字段 float、全链 IR 无
   min/max/clamp（唯一钳位是 global_gain 下限 −90dB，仅 Q1.15 数据通路防哑音），真实上限由
   `exp_float` 输入钳 [-32,31] 决定 ≈ +269dB（10^(db/20)）/ +538dB（段增益 10^(g/40)）。
4. **(b)/(c 的 QCY 命令接收器，[未钉])**：`0x0602583c`（0x22 setter）是否在复制参数时钳
   gain，仓库内无该函数反汇编，未直接验证；由预设 −17 值能正常下发推断其**不钳 ±8**。

**结论**：+8/-8 是 **(a) App UI/控制面板层限制**；固件算法侧无钳（路 CN 已证），QCY 命令接收
层无 ±8 钳的强间接证据（预设 −17 值）。独立 app 直接发 >8 的 gain 即可；真正约束在输出端
（WDRC out_threshold + data_saturation + DAC_VL0 限幅，路 CN §3），要防削顶而不是防「协议拒收」。

## 4. 独立 app 可行性

### 4.1 现有 android-query 可复用面 [静态]

`tools/android-query/` 是现成的独立查询/OTA 客户端（包名 `cn.felix021.h3squery`），已实现并
实测的全部传输层可复用：

- 连接/discover/CCCD/MTU 捕获/单写串行队列（`MainActivity.gattCallback`/`pump`）。
- 帧分片 `BlePacketizer.split(frame, mtu)`（payload=MTU−3）。
- JL OTA SDK `jl_bt_ota.aar` 1.10.0/10932 的 `CustomCmd`/`CustomParam`（opcode 0xFF 自定义
  命令收发，`QueryManager.sendDataToDevice` 覆写做分片+串行）。
- `QueryPolicy.permitted()`：**当前只放行只读帧**（03/D4/FF 只读/C1/E1/E2），所有 setter 形状
  （0x17 ANC set、0x22 EQ set、0x2b key set）都拒绝。

**结论**：**扩展现有客户端即可，不必新建工程**。要动的是「写策略」而非传输层——新增一个
`WritePolicy`（类比既有 `UpgradePolicy` 的 armed 状态），只放行**形状完全匹配的 setter 帧**
（0x17/0x22/0x2b/0x1d），不要开「任意字节写」总开关（AGENTS 红线：不添加任意 packet 写入
入口）。私有 `UpgradeService` 已示范「armed 才放行特定形状」的写法。

### 4.2 最小命令集（独立 app 要复刻的写命令）

| 功能 | 通道 | 载荷 | 状态 |
| --- | --- | --- | --- |
| EQ 写 | FF/`0x22` | 3B 头 + 每段 7B（EQ 类型/master gain/freq/gain/Q/iir） | 字段字节布局 [未钉]，需重解 0x0602583c |
| ANC 切 | FF/`0x17` | `17 03 mode scene strength` | 已钉 [文档]；路 P 反汇编核对一致（data=后 3 字节） |
| 按键重映射 | FF/`0x2b` 或 ADV type2 | gesture/action 组合 或 `[key,gesture,action]` | H3S 用哪个分支 [未钉]；App 侧两条 wire 组帧已实现（§2.5 实现状态，默认 ADV type2） |
| 提示音音量 | FF/`0x1d` | data=`value 占位`（len=2，值 3/6/8） | 帧形状已钉 [静态：docs/cmd-0x17-0x1d-layout.md 路P] |
| 媒体音量 | AVRCP(经典BT) 或 BLE 音量命令 | — | [未钉]，仓库无证据 |

只读查询（电量/ANC/设备信息）已实测，照 §2.6 抄即可。

### 4.3 MTU/ATT 分包约束 [文档：docs/ota.md + AGENTS 不变量 6]

- ATT MTU=247 ⇒ GATT 有效载荷 244B。522 字节 SDK 帧分 3 片（244+244+34）。
- 同一时刻只有**一个** GATT 写入，等 `onCharacteristicWrite` 成功回调后再发下一片，片间间隔
  **≥10ms**（实测服务要求）。
- 写回调超时/写请求被拒/队列过大/状态失效立即终止，不静默丢包/并发写。
- 接收按 RCSP length+尾字节 `EF` 重组帧（可能跨 notify 分片）。

### 4.4 待补齐 / 未钉清单（如实标注）

1. **0x22 EQ 载荷精确字节布局**：仓库只钉到「3B 头 + 每段 7B + 字段名」，未钉各字段字节宽/
   序。补齐方法 = 在构建机用 vendor objdump 反汇编 `0x0602583c`（setter）读参数解析形状
   （`tools/disassemble_app.py --elf-only` + 构建机 objdump，AGENTS 已有该流程）。
2. **按键重映射 H3S 走 ADV type2 还是 0x2b**：需真机抓一次 setKeyFunction 的实际 BLE 包
   （红线：不连接真机——本路不做，标 [未钉] 留给后续授权会话）。
3. **媒体音量命令**：仓库无证据，先按 AVRCP 或真机对照 BLE 音量命令。
4. **`character` 字段（0x0B/0x0A/0x1001）GATT 级含义**：先按全部走 A002/1 实现，异常再查。
5. **EQ/ANC 写的响应验证**：只读查询已有 status/SN 回包格式；setter 的响应与「设备已应用」
   边界 [未钉]（App 侧有距上次设置时间的过滤，不直接等于设备已应用——[文档：app-analysis.md]）。

## 5. 证据锚点

- BLE 服务/特征/MTU：docs/protocol.md（实测 A002/1/2/CCCD、MTU 247、帧格式）；tools/android-query/
  MainActivity.java（SERVICE/WRITE/NOTIFY/CCCD UUID 常量、分片 pump、QueryPolicy）。
- EQ 写 0x22：docs/eq-toggle.md（setEQWtihQFG2/0x22、注册点 app.bin+0xa9a78、getter 0x0602580a/
  setter 0x0602583c/应用 0x06025118、3B+7B/段、key42、预设下发整组参数）；data/controlpanel_
  qcyh3s_16764.json（EQ 面板 type2/maxdb8/mindb8/count10/freq/q/iitType/mastergain/6 预设+空间
  音效）。
- ANC 写 0x17：docs/app-analysis.md（setANCSenseMode→sendCustomCMD→A002/1；默认/通透/正常三值；
  注册点 app.bin+0xa9ae8；内部编号映射）；docs/protocol.md（FE 01 17 查询）。
- 音量：docs/appcore-tail-sfr-semantics.md（DAC_VL0 0xfe051c 数字音量+钳位槽）、docs/stream-
  volume.md（volume_cfg 16B 布局、提示音音量节点）、controlpanel（提示音音量 cmdid 0x1d 值
  3/6/8）。
- 按键重映射：docs/eq-toggle.md（setKeyFunction/setBtnClickFunction、ADV type2 vs 0x2b 未定、
  固件 0x060252c6/0x06025382、动作 11→ANC）、controlpanel（eventID 769/1281/1793-1796、cmd 0-7）。
- +8/-8：controlpanel（mindb/maxdb=8、预设 −17/-14/-2.5）、docs/eq-gain-limit.md（路 CN：float
  无钳、唯一 −90dB 下限、exp_float 上限 +269/+538dB）。
- 复用面：tools/android-query/README.md（构建/权限/门槛）、docs/ota.md（MTU 247/244B 分片/单写
  串行/10ms 间隔）、AGENTS.md 技术不变量 6（MTU247=GATT244、等写回调、10ms 间隔、522B=3 片）。
- 验证：`python3 tools/check_local.py --units` exit 0（本路自验）。
