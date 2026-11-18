# H3S Android 查询与固定候选OTA工具

独立调试客户端，包名 `cn.felix021.h3squery`。通过QCY BLE服务调用官方JL OTA SDK；查询和实际升级分层。当前源码版本0.8.0-morefunc（含 0.7.2-eqfix 的 EQ type 修复；launcher=WebView 壳四 tab：耳机控制/助听/更多功能/USB，原生查询页降级为工具入口），保留已实测的协议、传输和固定候选门槛，目标地址由构建生成。

当前只接受已验证的 ANC 固件 SHA256 `b79cb3a6f8702eb9e1d12eaf181a968f6b5848a66c051b2c792c39aa8b42fa1b`、长度1326848，输入H3S版本280。EQ r2 实机出现运行异常，r1/r2均拒绝。记录见 [EQ 实验故障](../../docs/eq-toggle.md)。当前恢复还依赖可用设备连接；不能把工具准备完毕当作已恢复。

`firmware` Release 仍是已验证的 ANC 两态包，当前源码恢复为固定此摘要。实际手机先装回此前已验证的0.4/code6 APK，避免在排查期间更改传输逻辑。此限制不放宽为任意文件／版本。

## 构建

从仓库根目录执行：

```bash
python tools/bootstrap.py --android
bash tools/android-query/test.sh
export ANDROID_SDK_ROOT=/path/to/android-sdk
bash tools/android-query/build.sh
```

默认不需要任何地址：App 扫描 BLE 广播按**厂商特定 AD**（公司 ID 0x521C + 载荷 ≥24B + 型号
0x417C，照 QCY 官方 App 先例）自动发现耳机再连接，广播名匹配保留为辅（路 BLEDISC2，
2026-11-19——H3S 广播实测无 Local Name，按名发现的前提不成立；根因见
docs/android-ble-connection-diagnosis-20261118.md）。可选环境变量：`H3S_BLE_ADDRESS`
（注入则作为扫描时的优先地址，扫到即连）、`H3S_BLE_NAME`（辅助名字匹配，默认 "QCY H3S"）、
`H3S_BLE_NAME_MATCH`（prefix|exact，默认 prefix）。
环境要求：JDK、Python、unzip/zip、Android platform35、build-tools35.0.0。`test.sh`只需JDK和Python，不访问蓝牙。

两份依赖下载到被忽略的vendor/，均按固定摘要检查：

| 文件 | SHA256 |
| --- | --- |
| jl_bt_ota.aar（1.10.0/10932） | 7b0671e4f98b39537ed1a0701fcd0aecd16c17eebc43468fa58f17af0e84ee15 |
| androidx-core-1.7.0.aar | aaf6734226fff923784f92f65d78a2984dbf17534138855c5ce2038f18656e0b |

AndroidX Core是SDK权限检查的实际依赖，不能省略。配置只写入 `build/generated/.../DeviceConfig.java`（地址留空=扫描发现模式）；注入了地址的APK含所配目标地址，公开分享自己的APK前要考虑这一点。本仓库不附带原测试APK和签名私钥。

构建运行 tests/ 下全部 JVM 测试（QueryPolicy/UpgradeGate/BleDiscovery/BlePacketizer/EqFrame/KeyRemapFrame/
KeyStore/MoreFunc/H3SBridge/H3sTransport/TransportContract/SimulationGattLink/SimulationUsbLink/UsbScsi/H3sUsbBridge），再编译、打包、签名和检查APK。开始构建即移除旧APK，避免失败后误用旧产物。调试签名在 `build/debug.keystore` 创建；同一设备覆盖安装需要保留自己的签名，不要把它提交Git。输出 `build/h3s-query.apk`。

## 安装和只读检查

```bash
export ANDROID_SERIAL=YOUR_ADB_SERIAL
adb -s "$ANDROID_SERIAL" install -r tools/android-query/build/h3s-query.apk
# 桌面图标 / launcher 直接打开 WebView 壳（耳机 + USB 双 tab）
adb -s "$ANDROID_SERIAL" shell monkey -p cn.felix021.h3squery \
  -c android.intent.category.LAUNCHER 1
# 原生查询/升级工具页（非导出，adb 显式启动照旧）
adb -s "$ANDROID_SERIAL" shell am start \
  -n cn.felix021.h3squery/.MainActivity
```

打开App并授予“附近设备”权限。点击“连接并查询”，核对型号、版本和QCY电量。地址编辑框允许查询其他地址，但升级服务仍固定使用构建配置和H3S身份门槛。

普通查询允许的完整帧如下，其他写入拒绝：

| opcode | 参数 |
| --- | --- |
| 03 | FFFFFFFF00，读取设备信息 |
| D4 | 空参数，MD5读取；本机能力不支持时SDK不自动请求 |
| FF | FE012F、FE0114、FE0110、FE0117，只读电量／电源／睡眠／ANC；更多功能只读追加了 FE011F（断连关机）/FE0119（提示音语言）/FE0123（LDAC）/FE0124（双设备）/FE012E（音频模式），docs/morefunc-protocol.md §2 |
| C1 | 00000400，只读通用ANC列表属性 |
| E1 | 空参数，升级检查字段 |
| E2 | 仅00，且需先确认E1 offset/len0 |

## EQ 写命令组帧（字节布局钉死；EQ 下发通道与 ANC 同路，曲线生效待 0.7.2 复测）

`EqFrame` 按路 H 钉死的字节布局（[eq-cmd-0x22-layout.md](../../docs/eq-cmd-0x22-layout.md)）把「EQ 类型 + master gain + 段列表」组帧成 RCSP 自定义命令 0x22：

- 帧：`FE DC BA | C0 | FF | len_BE16 | SN | 22 | data_len | data | EF`
- data = 3B 头 + 每段 7B；头[0]=EQ 类型(u8)、头[1..2]=master gain(i16 小端 ÷100 得 dB)；每段 = freq(u16 小端，Hz 直存) + q(i16 ÷100) + gain(i16 ÷100 得 dB) + iir_type(u8，0..4，255=默认 band_pass)。段数上限 10。
- **EQ 类型字节写入语义（路 EQFIX 2026-11-19 钉死，docs/eq-cmd-0x22-layout.md §2.3）**：
  type=1 = 固件装载自定义槽 MusicEqBt[0]、**载荷曲线被忽略**；type=10 = 空间音效；
  type ∉ {1,10}（含 `EqFrame.TYPE_APPLY=2`）才解码载荷并应用。0.7.1 及之前页面发
  type=1 是真机「EQ 发送无效果」（连接/电量/ANC 全通）的根因；0.7.2 起预设与自定义
  全部改发 type=2，空间音效预设保持 type=10。
- 全部整数入参：master gain/每段 gain 用 centidB(=dB×100)、q 用 ×100、freq 用 Hz，避免浮点误差。

```java
byte[] frame = EqFrame.buildEqCommand(eqType, masterGainCentidB, bands); // 完整 RCSP 命令帧
byte[] data  = EqFrame.buildEqData(eqType, masterGainCentidB, bands);    // 仅 data=3B+7N，无 RCSP 包装
EqFrame.Parsed p = EqFrame.parseEqCommand(frame);                        // 反解析，往返自检
```

`EqBand(freq, q, gain, iirType)` 是段值类型；越界（段数>10、iir∉{0..4,255}、freq 超 u16、q/gain/master 超 i16）抛 `IllegalArgumentException`。

`EqWritePolicy.permitted(data, armed)` 是写策略骨架：只在 armed 时放行形状完全匹配的 0x22 setter 帧（opcode 0xFF + cmdid 0x22 + data_len%7==3），不开任意字节写总开关；未 armed 退回 QueryPolicy 只读。

诚实标注：组帧已由 `EqFrameTest` 单测覆盖（往返一致 + 路 H 文档字节对照 + 段数/取值校验 + 写策略放行/拒绝），真机下发未验证——不连真机、不刷机，传输接线见「传输接线骨架」与路 AU 适配层（真机下发待授权联调）。

## WebView 壳 + JS bridge（R3 独立 app 壳层，纯离线）

R3 独立 app 按用户指定架构实现为「HTML5 WebView 页面 + Android 原生壳」：页面（UI）在
`assets/index.html` 迭代，壳（`WebShellActivity`）封装组帧与传输接线，经 JS bridge（注入名
`window.h3s`）暴露给页面。bridge 注入 transport 时是「校验 + 组帧（SN 自管理）+ EqWritePolicy
放行 + 串行发送 + 事件回推」；未注入（无参构造）退化为纯组帧「校验 + 组帧 + hex 回显」——
旧测试与离线演示行为不变。真机下发需授权联调（路 AU 已把传输层接到壳，连接由页面显式触发）。

入口：**launcher=WebShellActivity**（桌面图标直达，耳机/USB 双 tab 全 WebView，原生零 UI）；
MainActivity 原生查询页降级为非导出工具入口（升级卡按钮 / adb 显式 am start / PROBE 流程不变）。

bridge 方法（`H3SBridge.java`，全部 `@JavascriptInterface`）：

| 方法 | 行为 | 说明 |
| --- | --- | --- |
| `armWrite(boolean)` / `isArmed()` | 写门控 | 对应 `EqWritePolicy` 的 armed；未武装时一切写方法返回错误 JSON |
| `writeEq(eqType, masterGainCentidB, bandsJson)` | 组 FF/0x22 EQ 写帧 | 内部调 `EqFrame.buildEqCommand`；bandsJson 每段 `{freq(Hz), q(x100), gain(centidB), iirType}`，q 缺省 100（Q1.0）、iirType 缺省 255（未指定） |
| `setAnc(mode, scene, strength)` | 组 FF/0x17 ANC 帧 | data=`mode scene strength`（降噪 1,1,2 / 正常 2,0,0 / 通透 3,1,4）；mode 只收 1..3 |
| `setAudioMode(mode)` | 组 FF/0x2E 音频模式帧 | mode 1=音乐/3=游戏(低延迟)/4=电影（docs/wired-game-mode.md §2.1：固件 cmd 46 setter 只读载荷首字节；data_len=1 [假设] 联调对照）；EqWritePolicy 白名单同域收紧 {1,3,4} |
| `setPromptVolume(level)` | 组 FF/0x1d 提示音音量帧 | 值只收 3/6/8；data=`value 00`（路 P 反汇编钉死，固件只读 data[0]、第二字节忽略不钳） |
| `readStatus(cmdId)` | 组只读状态查询帧 | cmdId∈{0x2f 电量, 0x17 ANC, 0x14 电源, 0x10 睡眠}；帧=QueryPolicy §battery 形状（body=`SN FE 01 cmd`），回包按 SN 匹配走 response 事件；只读不需武装 |
| `setKeyRemap(eventId, action)` | 组按键重映射帧（默认 wire=批量同步 0xC0） | **2026-11-19 MOREFUNC 钉死**：固件按键注册在批量同步表 cmdid 2（0x0600fae0 case 0），item=`{len=4, cmdid=2, key,gesture,action}`；此前默认的 ADV type2/0x2b 在固件注册表无对应（实发必无回执，即真机「发送未确认」的根因），两条旧候选保留作对照（`setKeyRemapAdv`/`setKeyRemapCustom`）；eventId 布局 0x{逻辑键}0{连击数}（769/1281/1793..1796，长按低字节 5 [假设]），action 0..7 或 11（ANC） |
| `readKeyRemapTable()` | 从设备读按键映射全表（0xC1 mask 00 00 00 04） | 回 45B=15×{key,gesture,action}（固件读 handler 0x060254f2）；回执前缀形状 [未钉] 宽容解析（`MoreFuncFrame.parseBatchReadPayload`） |
| `getKeyRemap()` | 读按键映射（本地工作副本） | `{"ok":true,"map":{"769":6,...}}`，初始=控制面板 defaultFun、set/设备读回后更新；长按条目 -1=未知 |
| `setSleepTimer(minutes)` / `setAutoOff(minutes)` | 定时/断连关机（FF/0x14 / FF/0x1f） | 面板档位 5/10/30/60 分钟或 0xFFFF 不关机（0=立即关机拒绝）；分钟 LE16；固件 setter 静默无回执，UI 发送后回读确认 |
| `setToneLanguage(chinese)` | 提示音语言（FF/0x19） | data=4B key："JL00"=英文/"JL01"=中文（固件 0x609e284 两项 BE 键表反解） |
| `setLdac(on)` / `setDualDevice(on)` | LDAC/双设备开关（FF/0x23 / FF/0x24） | data=[01/00]；两功能互斥+切换重启（固件 0x0602262e/0x06022670 互斥链反解）；双设备上限=固件编译死 2 台 |
| `setDeviceName(name)` | 给设备取名（批量同步 cmdid 1） | ≤20B（面板 maxLength；固件上限 32B NUL 补齐） |
| `restoreDefaults()` / `findBuds()` | 恢复出厂 / 找耳机响声（opcode 0xC5） | body=SN+子命令：01=跑两表全部恢复默认回调（危险，UI confirm）；02=响两声提示音 [推断] |
| `clearPairing()` | 清除配对记录（FF/0x03，实验性） | 固件 setter 无视 data 投内部事件 594→BT 控制 [推断]；UI 强警示 |
| `setHearingCfg(hex)` / `getHearingCfg()` | key30 写/读（FF/0x30 写形态帧） | 8B 值不透明；写回执 `30 01 status`、读回执 `30 08`+8B 经 response 事件 |
| `setHearingEq(segsJson)` / `getHearingEq()` | key31 十段 EQ 写/读 | 写 data=[31,88B]（encodeEqValue 协议门），读 data=[31] 回执 `30 58`+88B（parseEqReadPayload→decodeEqValue 解码链）；EqWritePolicy 白名单放行 key31 的 89B 写/1B 读精确形状（值不透明，key30 先例同款） |
| `invalidateHearingCfg(hex)` / `invalidateHearingEq(hex)` | 双 key 软回退（H5-DEF-01，§4d） | 只接受「可解码的真实协议原值」，Java 侧自行把魔数首字节 48→00、其余字节原样发（页面无法借这条入口发任意字节）；原值备份=调用方先 get 后存档 |

返回统一 JSON：成功 `{"ok":true,"bytesHex":"fedcba..."}`（小写连续 hex）；失败
`{"ok":false,"error":"..."}`（坏 JSON、字段缺失/非数值、取值越界、未武装等都有独立错误信息）。
未匹配 SN 的响应帧以 `device_message` 事件透出（迟到响应/主动通知，页面按前缀启发式解析）。

**页面（`assets/index.html`，单文件内联 CSS/JS、无外部 CDN）**：暗色主题、卡片式、四 tab——

- **耳机 tab**：连接卡（状态/MTU/电量槽位——连接就绪自动查电量，只读 0x2f）、写入门控开关、
  ANC 三态、音频模式（音乐/游戏·低延迟/电影，标注「低延迟=效果重配，帧长未钉」）、EQ 卡
  （预设 chips=QCY 面板原始 7 预设+自定义，含 −17dB 等 ±8 外调音值；曲线 + 一行横排 10 段，
  每段可调中心频率 20–20000 Hz 与增益 −12..+12 dB +master gain；点选锁定防滚动误触；
  **0.7.2/路 EQFIX**：①下发 type 字节 1→2 修复「发送无效果」（§EQ 写命令组帧）；
  ②自定义滑杆状态 localStorage 自动记忆/恢复；③「我的预设」——当前自定义可存为命名
  预设，列表加载/重命名/删除，同名单名覆盖，localStorage 持久化，上限 50 组）、
  按键自定义（3 键×5 手势矩阵，长按列虚线标注 [假设]；wire 单选默认批量同步 0xC0，可切
  ADV type2/0x2b 对照；「从设备读取」发 0xC1 回 45B 真值灌网格）、提示音
  音量三档、固件升级卡（跳 MainActivity 升级工具+固定候选说明）。
- **更多功能 tab**（MOREFUNC，2026-11-19）：定时关机/断连关机（档位选择+发送后自动回读确认）、
  提示音语言（英文/中文）、LDAC/双设备（互斥校验+重启警示）、给设备取名（≤20B）、找耳机响声、
  恢复默认设置/清除配对（危险区 confirm，后者标实验性）、声道平衡与佩戴检测调研结论卡
  （声道平衡=官方面板占位无命令绑定、固件无 L/R 平衡 key——不放假滑杆）。全部选择
  localStorage 记忆（`h3s.morefunc.*`），连接就绪自动回读 9 个只读 cmdid 对齐设备真值。
- **助听 tab**：见下节「助听 tab」——每频段增益/上限（BLE 留桩）+ 测试音/样本播放（h3sha 桥）。
- **USB tab**：见下节。在无 bridge 的普通浏览器打开会显示降级提示；调用与事件日志抽屉
  常驻（最近 60 条）。

诚实标注：

- 组帧/门控已由 `H3SBridgeTest` 单测覆盖（JVM 纯逻辑：JSON 解析、与 `EqFrame` 逐字节对拍、
  错误路径、武装门控、与 `EqWritePolicy` 一致性、setAudioMode/readStatus 字节对照、
  device_message 事件）。`WebShellActivity` 本身不测：它依赖 android.jar/WebView 运行时，
  纯 JVM 无法实例化，且只有「加载 asset + 注入 bridge」的壳逻辑，无业务分支。
- 真机通信：BLE 传输接线见下一节（路 Z/AU，联调待授权）；EQ 候选 UNTESTED 红线不变。
- 本机无 Android SDK 完整打包条件时，可用
  `javac -cp $ANDROID_HOME/platforms/android-35/android.jar src/.../H3SBridge.java src/.../EqFrame.java src/.../WebShellActivity.java`
  单独验证新增类的 Android 侧编译（本仓库开发机已验证通过；`test.sh` 只需 JDK+Python）。

## 助听 tab（R3：每频段增益/上限 + 测试音样本）

WebView 壳第三 tab，页面在 `assets/index.html`，原生侧只有一个 tone 播放桥 `h3sha`
（`H3sToneBridge.java`）。分两块：

### 每频段调节（BLE 命令留桩）

十个频段**横排**（每频段一纵列，窄屏可横向滚动，与 EQ tab 的 band-grid 同模式）。每列自上而下：
**上限数值**（默认 110，对应固件 Drc1Eff 原始末点 110.3 量级；点击数字弹窗输入修改，
范围 60–120）→ **纵向增益条**（±80 dB，App 滑杆范围；路 CN 已证 EQ float 无 clamp，
39 dB 放行，±80 只是 UI 范围）→ 增益读数 → 频点。增益条沿用防误触机制：
`touch-action: pan-y` + 点选锁定（未锁定时纵向滚动照常，点选后独占拖动；双击归零、方向键微调）。
10 段频率默认与 EQ 自定义一致（31..16k）。参数结构字段名与
[docs/hearing-aid-eq-feasibility.md](../../docs/hearing-aid-eq-feasibility.md) 对齐：

- `index / iir_type / freq / gain / q` = §1.2 `eq_seg_info`；`global_gain / seg_num / seg` = `eq_tool`；
- 每频段上限 `out_threshold` = §2 `wdrc_struct.threshold[].out_threshold` 语义（输出上限）。

**全带 MPO 聚合（L2 落地，[docs/hearing-aid-l2-mpo.md](../../docs/hearing-aid-l2-mpo.md)）**：
固件 `Drc1Eff` 是单带 WDRC，做不了每频独立限幅；页面把每频上限聚合为
`M = min(band.out_threshold)`，卡片下方「全带 MPO」汇总行实时显示生效值（弹窗/复位即时刷新）。
`buildHearingAidPayload()`（页面 `window.h3sHearingAidPayload`）输出 `mpo`（聚合值 +
三点水平尾段曲线 `(0.3,0.3)/(M,M)/(X,M)`，与 `--hearing-aid-mpo` 写进镜像的形状一致）
和 `band_out_threshold`（每频请求原样保留，L3 multiband 的用户输入存档）。

**诚实标注**：per-band 限幅在当前固件里无 1:1 落点（需 L3 分频+多 WDRC+merge 拓扑）；
M 是数字域 dB 刻度、非标定 SPL；attack/TWOPOINT 平滑下稳态上限成立≠瞬时峰值保护；
±80 dB 只是 App 草稿范围，越界实际效果未验。

**key30/key31 通道与成功判据（H5-PRE 缺口表修正）**：保存全带 MPO（key30）/保存十段 EQ
（key31）按钮按**四态**走：排队（`{ok,sent,seq}`）→ 发送（send_failed/桥拒绝）→ 回执
（挂精确判据：成功=载荷逐字节 `30 01 00`；`30 01 01`=set_config 失败；旧「返回串不以 ERR
开头」判成功会误报，已废）→ 读回（「读取两 key 原始值」按钮显示 `30 08`+8B / `30 58`+88B
原始 hex 并按协议门解码，非协议值如实标注）。「软回退 key30/key31（坏魔数，§4d）」按钮
先读后写：仅魔数首字节 48→00、其余字节原样（写入前弹窗显示确切 hex）；原值 hex 留档页面
副本。回执成功 ≠ 曲线已应用/已回退生效（固件候选 UNTESTED 红线不变）。

### 测试音与样本（h3sha 桥）

**二级选择**：先选类型（手动单频 / 语音 / 音乐 / 自然），再列该类型下的具体样本，每项可播/停。

- **手动单频**：频率**预设下拉**（125/250/500/1k/2k/3k/4k/6k/8k/12k/16k 听测常用频点）
  + **任意频率数字输入**（20–20000 Hz，输入框为准，预设变化跟随高亮）+ 音量滑杆；
  AudioTrack MODE_STATIC 生成整周期正弦、`setLoopPoints` 无缝循环直到 `stop()`。
- **样本**（`assets/samples/{speech,music,nature}/` 二级目录，16 kHz 单声道 PCM 16-bit，
  共 12 段约 2.9 MB）：
  - 语音 5 段：edge-tts 构建期生成的静态 WAV——en-US 男女声（日常对话/新闻）、en-GB 女声
    （数字报数）、zh-CN 男女声（日常对话/数字报数）。**不集成运行时 TTS 引擎**（端侧模型分发
    方案参考过 ~/code/podic-dictionary/tts，但本工具只需几段静态样本，静态生成是明确取舍）；
    edge-tts 服务条款允许个人使用其合成音频。
  - 音乐 3 段：小提琴（巴赫 BWV 1001 柔板、BWV 1004 阿勒芒德）×2 + 钢琴（肖邦夜曲
    Op.37 No.1），裁 9.3 s 片段，全部 CC0（Wikimedia Commons）。
  - 自然 4 场景：海浪（砾石滩）/ 雨（阵雨）/ 风（微风鸟鸣）/ 溪流（流水），各 6.2 s，
    全部 Public domain（Wikimedia Commons / PDSounds）。
  - 统一处理：去首尾静音 → loudnorm −20 LUFS（响度可比）→ 淡入淡出；每段来源 URL +
    license + 文本/时长记录在 `assets/samples/manifest.json`，与页面 `HA_SAMPLES` 同步维护。
    仅收 CC0/Public Domain，CC-BY 需署名的一律避开。
- **扬声器路由**：`AudioTrack.Builder.setPreferredDevice(TYPE_BUILTIN_SPEAKER)`——这是「首选
  设备」提示（android.jar 无 `MODIFY_AUDIO_ROUTING` 权限要求），用于绕开 BLE A2DP 的默认路由
  （否则耳机拾音→放大→再进同一副耳机会形成回环）。
- bridge 方法：`playTone(freqHz, volume)` / `playSample(id, loop)`（id 形如
  `speech/en_f_daily`，白名单按 assets 目录动态列举） / `stop()` / `info()`，
  返回 `{ok, playing, speaker, ...}`；`info()` 供页面 800ms 轮询（播放自然结束 + 扬声器状态）。

**诚实标注**：

- 语音样本是 edge-tts 合成人声（非真人录音），可用于试听频响变化，不等同真人言语可懂度测试。
- **真机 BT 连着时 `setPreferredDevice` 是否完全强制路由到内置扬声器未验证**——AVD 无真实
  BT，只能验 bridge 不崩、参数合法；路由行为、样本实际听感都要真机联调确认。

## USB tab（R3 USB 侧骨架，链路未验证）

WebView 壳的第二 tab，注入第二个 bridge `h3susb`（`H3sUsbBridge.java`）+ 原生 USB 层
（`AndroidUsbLink.java`，唯一接触 android.hardware.usb 的文件）。分层与 BLE 侧同构：

```text
页面（USB tab）
   ▼ h3susb.*（@JavascriptInterface）
H3sUsbBridge（枚举/授权/开盒/相位门/事件回推；结果经 window.onH3sUsbEvent）
   ▼ Link 接口（JVM 可桩测）
AndroidUsbLink（UsbManager 枚举+requestPermission+claimInterface+bulkTransfer；
               进入 ROM 的厂商控制传输 83/00/fc/fe len8→len0）
   ▼ UsbScsi.Channel（bulk OUT 0x01 / IN 0x81，实际以描述符为准）
UsbScsi（CBW/CSW 交换 + 最小命令集，与 tools/usb-recovery/protocol.py 逐字节同形）
```

- **命令白名单**（UsbScsi 编码入口即查）：INQUIRY / FB06（ROM 写 RAM）/ FB08（loader 跳转）/
  FC0A（在线查询）/ FD05（Flash 扇区读）/ FD07（内存窗读，≤128B）。**擦除/编程/复位命令
  不存在编码入口**——刷机路径在代码层就没有，不是 UI 挡一挡。
- **相位门**（照 read_rom.py）：FD07/FD05 只在 loader 就绪（FB06×46 + FB08 + FC0A 报 SPI NOR
  全过）后放行；loader 摘要钉死 `ab1b77eb…`（构建时从上游 MIT 仓库按摘要下载进
  `assets/loader/`，git 忽略；缺网构建允许缺失，届时读操作明确报错）。
- **只读操作**：INQUIRY 身份确认（BR28/UBOOT1.00）、读 ROM 窗（128 KiB FD07×1024 双读一致）、
  全片备份（2 MiB FD05×4096 双读一致）；结果落 app 私有 `files/usb/`，双读不一致即放弃不重试。
- **进入 ROM 模式**：对普通 QCY USB（3654:4a55）先能力读（须 8×FF）再一次性 wLength=0 请求
  （docs/usb-recovery.md：以重扫出现 4c4a:3442 身份为准，不重发）。
- **刷机区**：UI 存在但硬门控——解锁开关+红线文案（候选镜像 UNTESTED 不刷机）+ 候选列表
  空态；解锁后也不出现刷机按钮（无写命令）。
- 单测：`UsbScsiTest`（CBW/CDB/CSW 与 Python 逐字节对拍、CRC-16/XMODEM 向量、FB06 OUT
  相位、坏 CSW/短读/白名单拒绝）+ `H3sUsbBridgeTest`（桩 ROM 设备全流程：open/INQUIRY、
  loader 摘要门+相位门、双读落盘、enterRom 一次性+能力门、拔出复位）。

**诚实标注**：手机 OTG 连耳机进 ROM 模式的物理链路**未验证**——`info()` 恒报
`physicalLinkVerified:false`，页面常驻「OTG 链路未验证」标签；真机枚举/claim/端点/时序
待联调（页面上的枚举本身已在真机验证可用：能列出 USB 设备并标记身份）。

## 传输接线骨架（路 Z / sim-h3stransport，离线端到端已单测）

bridge 到 BLE 的传输层骨架。R3 命令面（组帧+白名单+壳）此前全部就绪、唯一缺的就是这段接线；
本路补齐后整条链「JS 参数 → 组帧 → 写策略 → 串行队列 → 分片 → 收响应 → SN 匹配 → 回调 JS」
**离线可端到端单测**；Android BLE 适配层已实现（路 AU，见下），真机联调只剩「授权后装壳实测」。

```text
H3SBridge（JS 入口，SN/seq 管理、EqWritePolicy 放行检查）
   │ write(frame)                       ┌─ 未注入 transport（默认构造）：纯组帧返回 hex（旧行为不变）
   ▼                                    └─ 已注入：组帧(SN)→策略→排队
H3sTransport（接口：connect/disconnect/write/onFrame/onMtuChanged）
   ▼
SerialBleTransport（通用实现，AGENTS 不变量 6 在这层）
   - 帧级串行：一次一条帧，等「写完所有片 +（flags=C0 时）响应帧到达或超时」再发下一条
   - 片级串行：BlePacketizer 按 MTU-3 分片，一次一片，等片写回调、片间隔 >=10ms
   - 接收：RcspDeframer 按 length+EF 重组（跨 notify 分片/一次多帧），响应帧释放当前帧
   ▼
LoopbackTransport（回环桩=「当耳机」：收片重组→逐字节可对照→生成 status=0 响应→走同一 notify 重组路径送回）
```

单测 `H3sTransportTest`（纯 JVM，进 test.sh）：端到端逐字节对照（Loopback 收到的帧 ==
`EqFrame` 直组）、按键重映射双 wire 端到端（ADV type2 默认/0x2b，Loopback 对 C2 写也回
status=0 响应——响应载荷回显同属 [未钉] 桩形状）、跨片重组（MTU 压小到 27）、帧级串行
（pause/release 响应，第二条帧在第一条响应前零发送）、未武装/未连接拒绝、SN u8 回绕
（254→255→0）、响应超时、未注入 transport 的向后兼容、deframer 单元（跨片/粘连/垃圾前缀）。

真机适配层（路 AU，2026-11-14）落地为三个文件：`AndroidBleTransport`（连接状态机+写路径粘合，
纯 JVM）、`AndroidGattLink`（android.bluetooth 薄粘合，唯一接触平台的文件）、
`TransportContractTest`（接口级契约：同一套断言跑 Loopback 与 AndroidBleTransport+假 Link——
分片序列=BlePacketizer、通知重组、串行顺序、错误传播、重连后 listener 存活、连接五段式/MTU
三变体/扫描三变体/重试/写看门狗、定时值 10s/30s/5s/10ms/5s、bridge connect/disconnect 控制）。
状态见下节清单（1-8 ✅，9-10 待授权联调）。

JS 侧异步约定（**seq + 事件回调**，不是把 JS 函数传进 Java——`@JavascriptInterface` 拿不到
可调用的 JS function，页面唯一可靠回推方式是壳 `evaluateJavascript` 调页面全局处理器，
这就是 WebView 生态的 promise id 模式）：

```js
const r = JSON.parse(h3s.writeEq(2, -400, bandsJson));  // {ok,sent,seq,sn,bytesHex}
window.onH3sEvent = e => {                               // 壳经 ResultSink 推回
  // {"event":"response","seq":r.seq,"ok":true,"status":0,"sn":1,"payloadHex":"..."}
  // {"event":"send_failed","seq":..,"error":"响应超时 5000ms"} / {"event":"mtu",...} / {"event":"disconnected",...}
};
```

诚实标注：**未连真机、未刷机**。Loopback 的 setter 响应载荷（cmdid|n|data 回显）是桩自定
形状——status+SN 起始位置是实测钉死的（docs/protocol.md），写路径 setter 响应的载荷部分
[未钉]，真机联调时以实机帧为准；EQ 候选 UNTESTED 红线不变。

### 真机适配清单（AndroidBleTransport，路 AU 已实现 1-8 项，9-10 待授权联调）

`AndroidBleTransport extends SerialBleTransport` 已实现（2026-11-14，路 AU / sim-bletransport）：
替换的只有「连接生命周期」与「doWriteChunk」，串行/分片/重组全走既有通用代码。android.bluetooth
全部接触面收敛在 `AndroidBleTransport.Link` 接口后面——状态机是纯 JVM 代码（假 Link 可离线单测，
`TransportContractTest` 与 Loopback 同断言对拍），真机粘合在 `AndroidGattLink`（照
MainActivity/UpgradeService 先例，本机按 android.jar 编译检查过，真机行为待联调）。

1. ✅ **connect()**：发现扫描（路 BLEDISC2，2026-11-19：**厂商特定 AD 主识别**——0xFF 结构含
   公司 ID 0x521C（报告未钉字节序，小端/大端镜像都认）+ 载荷 ≥24B（实测 H3S 恒 24，截断拒绝）
   + 载荷头两字节大端=型号 0x417C（同公司异型号拒绝），照 QCY 官方 App 按厂商数据筛选的先例；
   H3S 广播实测无 Local Name（5/5 历史目标广播 getName()=null），名字匹配降为辅助。0x03 服务
   列表含 0xFEE8 是辅助信号（加分不否决，只进候选日志）。注入地址候选扫到即优先选定，匹配候选
   首现后 settle 2s 取信号最强者（多台同型号）；12s 超时=明确失败，不再直连兜底死等）→
   `connectGatt(autoConnect=false, TRANSPORT_LE)` 到发现选定的地址 → `discoverServices` →
   校验 A002/1/2 与写特征属性（缺 A002 服务/特征=按连接失败处理——防同厂误连的最后一道防线；
   属性含 WRITE 用
   WRITE_TYPE_DEFAULT、只含 WRITE_NO_RESPONSE 用 NO_RESPONSE——H3S 实测属性 4）→
   `setCharacteristicNotification + CCCD ENABLE_NOTIFICATION_VALUE` → **等 CCCD 成功 +
   `onMtuChanged` 实际值双标志**（`requestMtu(517)` 以回调为准；MTU 协商失败不致命，按当前值
   就绪只增片数）→ `markReady(实际MTU)`。连接初始化失败重试（默认 2 次总尝试、退避 1s，沿用
   已选定地址），30s 整体看门狗。连接过程经 `Progress` 推「扫描中/发现/连接中」状态（页面
   conn 事件），终态走 mtu/disconnected 事件。
2. ✅ **doWriteChunk(chunk)**：`setWriteType + setValue + writeCharacteristic`；`status==0` →
   `chunkWritten(true)`，`status!=0` → `chunkWritten(false, "GATT status N")`（只终结当前帧，
   串行队列试下一条）；写请求同步被拒同样立即片失败；写回调 5s 缺失**断链**（照 UpgradeService
   writeTimeout 先例，不静默丢片）。
3. ✅ **notify 入口**：`onCharacteristicChanged`（NOTIFY uuid，新旧两个重载）→ `deviceNotify(bytes.clone())`。
4. ✅ **调度**：`schedule/scheduleTimeout` 覆盖为注入的 `Scheduler`（Android=`mainScheduler()`
   Handler.postDelayed；JVM 测试可注入假调度器或用默认手动队列）。所有定时任务带相位/世代自检。
5. ✅ **主线程**：平台回调统一 `main.post` 翻译到 Link.Callbacks；transport 的
   connect/disconnect/write 入口先跳主线程再执行（@JavascriptInterface 跑在 WebView JS 线程，
   不跳会与主线程回调并发改串行队列）。
6. ✅ **断开**：READY 后 `onConnectionStateChange` 断开 → `markNotReady(reason)`（未完成帧按
   失败回调，不自动重连，页面可再 connect）。附带修复：`SerialBleTransport.markNotReady` 不再清
   listener——原来断一次线 H3SBridge 就永久收不到事件，重连后链路才完整（契约测试钉死）。
7. ✅ **壳接线**：`WebShellActivity` 构造 `AndroidGattLink` + `AndroidBleTransport`（共享
   `BleDiscoveryPolicy`，日志接 Log.i）注入 `H3SBridge`，事件经 `evaluateJavascript` 推
   `window.onH3sEvent`；连接过程状态（扫描中/发现/连接中）经 `Progress` → `H3SBridge.emitConnectState`
   推 conn 事件，页面连接卡片实时显示流程文案；页面加连接卡片（连接/断开/MTU 状态）与事件回显
   （旧 index/keyremap 两页已并为单页双 tab）。
   连接由页面显式 `h3s.connect()` 触发，不自动连接。
8. ✅ **权限与目标**：`WebShellActivity` 进入即请求 `BLUETOOTH_SCAN/CONNECT`（照 MainActivity
   先例）；目标=按厂商 AD（0x521C/0x417C/≥24B，主识别）或广播名（辅助）发现的耳机
   （`BleDiscoveryPolicy`：GattLink 前置过滤不上报无关广播；只有被 transport 选定的地址才会被
   connectGatt，不开任意设备连接），连接后再以 A002 服务/特征核对身份（缺=失败重试/终态）。
   注入的 `H3S_BLE_ADDRESS` 作为优先地址（扫到即连）。终态通知按连接会话计
   （`SerialBleTransport.beginConnectSession`）：连续两次连接尝试失败各自报一次终态，
   会话内重复断线事件仍去重——2026-11-19 修复此前第二次失败被 `disconnectReported` 吞掉、
   页面永卡「扫描中」的 bug（诊断报告 00:29:34 复现两次）。
9. ⬜ **联调顺序**：先只读查询对拍（SN 匹配 + deframe 与 docs/protocol.md 实测帧对照）→
   单条 ANC 切换（响应最小）→ 提示音音量 → EQ 整组；EQ 候选 UNTESTED 不变。
10. ⬜ **联调时对照项**：真机 setter 响应载荷形状（替换 Loopback 的回显假设）、EQ 写是否需要
    先发预设装载、非档位音量值的实际行为（docs/cmd-0x17-0x1d-layout.md §6 未钉清单）。

## 实际升级

先完整阅读 [OTA实现与边界](../../docs/ota.md)。本例另有 [USB救砖实测](../../docs/usb-recovery.md)，其完整备份与限定恢复流程不由这个Android OTA客户端执行；双备份能力报告本身不能替代恢复步骤。

1. 核对当前固定候选的完整SHA256；当前源码对应已验证的 ANC 包。
2. 把**当前固定候选**复制到App私有 `files/candidate.ufw`。文件选择器的“检查本地uFW”只检查文件，不会把任意选择文件设为升级输入。
3. 点击“准备本次候选升级（不写入）”，等PREPARED，检查设备身份、版本、电量和MTU。
4. 点击“查看并确认本次刷写”，审阅摘要与修改说明，在两分钟内确认。服务再次读取设备后才开始实际OTA。
5. 传输期间保持连接，不更新APK、不重启手机、不重复开始。等待终态。
6. 成功后重新连接确认正常状态，实际验证声音与 ANC 模式键；版本号仍280，不能只看版本号。

复制候选的ADB示例：

```bash
adb -s "$ANDROID_SERIAL" shell -T run-as cn.felix021.h3squery \
  dd of=files/candidate.ufw bs=65536 \
  < firmware/H3S_0.1.1.8_ANC_ON_TRANSPARENT.ufw
```

界面确认不要求root。私有UpgradeService不导出；PROBE没有确认刷写operation。历史调试曾通过root向私有服务发送当次nonce，公开操作说明优先使用可见确认界面，不提供任意包／任意字节发送接口。

## ADB触发查询与准备

显式Intent可在已有权限下执行相同操作，不绕过或修改锁屏认证：

```bash
adb -s "$ANDROID_SERIAL" shell am start \
  -n cn.felix021.h3squery/.MainActivity \
  -a cn.felix021.h3squery.PROBE --es operation connect
```

每一步等前一步完成再切换operation：

| operation | 行为 |
| --- | --- |
| connect /disconnect | 连接并查询／释放查询连接 |
| flag /eligibility | E1／当前支持分支的E2 |
| power-info /sleep-info /anc-info | 固定只读QCY命令 |
| anc-cycle-info | 读取通用type10属性，不设置mask |
| negotiate-mtu | 空闲连接请求MTU协商，实际以回调为准 |
| inspect-cached | 核对私有固定候选大小与摘要 |
| prepare-upgrade | 准备，尚未刷写 |
| upgrade-state | 读取升级状态 |
| cancel-preparation | 取消未开始的准备，撤销nonce |
| ota-loopback | 4096字节内存模拟SDK流程，无真实GATT传输 |

循环调用am start可能把查询Activity带到前台，不能一边这样高频轮询一边假设用户可以无干扰操作QCY App页面。物理耳机键测试不依赖手机页面。

## 监控与独立核对

```bash
python tools/android-query/monitor_flash.py \
  --serial "$ANDROID_SERIAL" --output private/my-flash
python tools/android-query/pull_logs.py \
  --serial "$ANDROID_SERIAL" --output private/raw
python tools/android-query/verify_flash_log.py \
  private/my-flash/upgrade-TIMESTAMP.jsonl \
  firmware/H3S_0.1.1.8_ANC_ON_TRANSPARENT.ufw \
  --original firmware/QCY_H3S_7018F_1T2_release_note_V1.1.8_011.ufw
```

当前默认expected-sha256是ANC发布包。核对历史包时要显式 `--expected-sha256`，不能把不同包的日志混用。核对器重组RX帧，匹配每个E5的SN／offset／length，逐字节比较响应，并检查全部差分位置覆盖、E6/E7及待回复请求。

按键观察（先连接查询，且无OTA在运行）：

```bash
python tools/android-query/watch_anc.py \
  --serial "$ANDROID_SERIAL" --seconds 45 --output private/key-observation.json
```

它只读当前模式，不替用户切模式。操作物理键时每次间隔约4秒，避免轮询漏掉快速中间状态。SIGINT／SIGTERM会停止观察并保存已收集结果。观察到模式变化本身不能独立证明是哪种用户操作造成的，要结合实际测试过程。

## 日志和限制

日志位于私有 `files/query-*.jsonl` / `upgrade-*.jsonl`。`run-as`拉取不要求root，但需要这个debuggable客户端。原始设备信息可能含authKey、地址和广播，必须保留在private/等被忽略目录；公开前人工检查摘要。

前台服务不自动恢复授权、不自动重刷。准备超时、nonce过期、目标不匹配或设备不满足当前支持范围时，应读状态与原因；不要随意放宽代码门槛或调高固件版本来“试一试”。
