# H3S Android 查询与固定候选OTA工具

独立调试客户端，包名 `cn.felix021.h3squery`。通过QCY BLE服务调用官方JL OTA SDK；查询和实际升级分层。当前源码版本0.5.1-restore-anc，保留已实测的协议、传输和固定候选门槛，目标地址由构建生成。

当前只接受已验证的 ANC 固件 SHA256 `b79cb3a6f8702eb9e1d12eaf181a968f6b5848a66c051b2c792c39aa8b42fa1b`、长度1326848，输入H3S版本280。EQ r2 实机出现运行异常，r1/r2均拒绝。记录见 [EQ 实验故障](../../docs/eq-toggle.md)。当前恢复还依赖可用设备连接；不能把工具准备完毕当作已恢复。

`firmware` Release 仍是已验证的 ANC 两态包，当前源码恢复为固定此摘要。实际手机先装回此前已验证的0.4/code6 APK，避免在排查期间更改传输逻辑。此限制不放宽为任意文件／版本。

## 构建

从仓库根目录执行：

```bash
python tools/bootstrap.py --android
bash tools/android-query/test.sh
export ANDROID_SDK_ROOT=/path/to/android-sdk
export H3S_BLE_ADDRESS=AA:BB:CC:DD:EE:FF
bash tools/android-query/build.sh
```

填自己的BLE地址，不是A2DP经典地址。环境要求：JDK、Python、unzip/zip、Android platform35、build-tools35.0.0。`test.sh`只需JDK和Python，使用虚构地址，不访问蓝牙。

两份依赖下载到被忽略的vendor/，均按固定摘要检查：

| 文件 | SHA256 |
| --- | --- |
| jl_bt_ota.aar（1.10.0/10932） | 7b0671e4f98b39537ed1a0701fcd0aecd16c17eebc43468fa58f17af0e84ee15 |
| androidx-core-1.7.0.aar | aaf6734226fff923784f92f65d78a2984dbf17534138855c5ce2038f18656e0b |

AndroidX Core是SDK权限检查的实际依赖，不能省略。地址只写入 `build/generated/.../DeviceConfig.java`；APK含所配目标地址，公开分享自己的APK前要考虑这一点。本仓库不附带原测试APK和签名私钥。

构建运行QueryPolicyTest、UpgradeGateTest、BlePacketizerTest，再编译、打包、签名和检查APK。开始构建即移除旧APK，避免失败后误用旧产物。调试签名在 `build/debug.keystore` 创建；同一设备覆盖安装需要保留自己的签名，不要把它提交Git。输出 `build/h3s-query.apk`。

## 安装和只读检查

```bash
export ANDROID_SERIAL=YOUR_ADB_SERIAL
adb -s "$ANDROID_SERIAL" install -r tools/android-query/build/h3s-query.apk
adb -s "$ANDROID_SERIAL" shell am start \
  -n cn.felix021.h3squery/.MainActivity
```

打开App并授予“附近设备”权限。点击“连接并查询”，核对型号、版本和QCY电量。地址编辑框允许查询其他地址，但升级服务仍固定使用构建配置和H3S身份门槛。

普通查询允许的完整帧如下，其他写入拒绝：

| opcode | 参数 |
| --- | --- |
| 03 | FFFFFFFF00，读取设备信息 |
| D4 | 空参数，MD5读取；本机能力不支持时SDK不自动请求 |
| FF | FE012F、FE0114、FE0110、FE0117，只读电量／电源／睡眠／ANC |
| C1 | 00000400，只读通用ANC列表属性 |
| E1 | 空参数，升级检查字段 |
| E2 | 仅00，且需先确认E1 offset/len0 |

## EQ 写命令组帧（纯离线，真机下发未验证）

`EqFrame` 按路 H 钉死的字节布局（[eq-cmd-0x22-layout.md](../../docs/eq-cmd-0x22-layout.md)）把「EQ 类型 + master gain + 段列表」组帧成 RCSP 自定义命令 0x22：

- 帧：`FE DC BA | C0 | FF | len_BE16 | SN | 22 | data_len | data | EF`
- data = 3B 头 + 每段 7B；头[0]=EQ 类型(u8，1=自定义/10=空间音效)、头[1..2]=master gain(i16 小端 ÷100 得 dB)；每段 = freq(u16 小端，Hz 直存) + q(i16 ÷100) + gain(i16 ÷100 得 dB) + iir_type(u8，0..4，255=默认 band_pass)。段数上限 10。
- 全部整数入参：master gain/每段 gain 用 centidB(=dB×100)、q 用 ×100、freq 用 Hz，避免浮点误差。

```java
byte[] frame = EqFrame.buildEqCommand(eqType, masterGainCentidB, bands); // 完整 RCSP 命令帧
byte[] data  = EqFrame.buildEqData(eqType, masterGainCentidB, bands);    // 仅 data=3B+7N，无 RCSP 包装
EqFrame.Parsed p = EqFrame.parseEqCommand(frame);                        // 反解析，往返自检
```

`EqBand(freq, q, gain, iirType)` 是段值类型；越界（段数>10、iir∉{0..4,255}、freq 超 u16、q/gain/master 超 i16）抛 `IllegalArgumentException`。

`EqWritePolicy.permitted(data, armed)` 是写策略骨架：只在 armed 时放行形状完全匹配的 0x22 setter 帧（opcode 0xFF + cmdid 0x22 + data_len%7==3），不开任意字节写总开关；未 armed 退回 QueryPolicy 只读。

诚实标注：组帧已由 `EqFrameTest` 单测覆盖（往返一致 + 路 H 文档字节对照 + 段数/取值校验 + 写策略放行/拒绝），真机下发未验证——不连真机、不刷机，传输层接线留待授权联调。

## WebView 壳 + JS bridge（R3 独立 app 壳层，纯离线）

R3 独立 app 按用户指定架构实现为「HTML5 WebView 页面 + Android 原生壳」：页面（UI）在
`assets/index.html` 迭代，壳（`WebShellActivity`）封装组帧能力，经 JS bridge（注入名
`window.h3s`）暴露给页面。**bridge 是纯组帧层**：只做「校验 + 组帧 + hex 回显」，不接真
蓝牙/USB——所有方法返回字节 hex 字符串用于前端调试，真机下发需要授权联调时再接传输层。

入口：MainActivity 的「打开 WebView 壳（离线组帧演示）」按钮，或
`adb shell am start -n cn.felix021.h3squery/.WebShellActivity`（不导出，无桌面图标）。

bridge 方法（`H3SBridge.java`，全部 `@JavascriptInterface`）：

| 方法 | 行为 | 说明 |
| --- | --- | --- |
| `armWrite(boolean)` / `isArmed()` | 写门控 | 对应 `EqWritePolicy` 的 armed；未武装时一切写方法返回错误 JSON |
| `writeEq(eqType, masterGainCentidB, bandsJson)` | 组 FF/0x22 EQ 写帧 | 内部调 `EqFrame.buildEqCommand`；bandsJson 每段 `{freq(Hz), q(x100), gain(centidB), iirType}`，q 缺省 100（Q1.0）、iirType 缺省 255（未指定） |
| `setAnc(mode, scene, strength)` | 组 FF/0x17 ANC 帧 | data=`mode scene strength`（降噪 1,1,2 / 正常 2,0,0 / 通透 3,1,4）；mode 只收 1..3 |
| `setPromptVolume(level)` | 组 FF/0x1d 提示音音量帧 | 值只收 3/6/8；data=`value 00`（路 P 反汇编钉死，固件只读 data[0]、第二字节忽略不钳） |
| `setKeyRemap(eventId, action)` | 组按键重映射帧（默认 wire=ADV type2） | 走 `KeyRemapFrame.buildAdvType2Command`（opcode C2+type2 mask+三元组，帧形状 [假设]）；eventId 布局 0x{逻辑键}0{连击数}（769/1281/1793..1796，长按低字节 5 [假设]），action 0..7 或 11（ANC） |
| `setKeyRemapCustom(eventId, action)` | 组按键重映射帧（候选 wire=自定义 0x2b） | 走 `KeyRemapFrame.buildCustomCommand`（FF/0x2b+3B 三元组）；字段域同上，两条 wire 真机抓包定案（docs/app-protocol.md §2.5） |
| `getKeyRemap()` | 读按键映射（本地工作副本） | `{"ok":true,"map":{"769":6,...}}`，初始=控制面板 defaultFun、set 成功后更新；非设备读回（type2 查询响应形状未钉），长按条目 -1=未知 |

返回统一 JSON：成功 `{"ok":true,"bytesHex":"fedcba..."}`（小写连续 hex）；失败
`{"ok":false,"error":"..."}`（坏 JSON、字段缺失/非数值、取值越界、未武装等都有独立错误信息）。
演示页面含 10 段 EQ 滑块（默认频点取 QCY 面板 32..16k，可手改；gain ±12 dB——QCY App 钳
±8 是面板层限制，固件接收层不钳，超出范围的听感自负）、master gain、ANC 三态、武装开关与
hex 回显；在无 bridge 的普通浏览器打开会显示降级提示条。按键重映射独立页 `keyremap.html`
（index.html 有入口）：三键（音量-/音量+/多功能）× 单击~四击+长按 [未钉] 网格，每格下拉选
动作（0..7 中文名+11 ANC）、wire 单选（默认 ADV type2 / 0x2b）、`getKeyRemap` 回填与逐格
写入，hex 回显同款。

诚实标注：

- 组帧/门控已由 `H3SBridgeTest` 单测覆盖（JVM 纯逻辑：JSON 解析、与 `EqFrame` 逐字节对拍、
  错误路径、武装门控、与 `EqWritePolicy` 一致性）。`WebShellActivity` 本身不测：它依赖
  android.jar/WebView 运行时，纯 JVM 无法实例化，且只有「加载 asset + 注入 bridge」两行壳
  逻辑，无业务分支。
- **真机通信未接**：bridge 不连接耳机；`EqWritePolicy` 已放行 0x22/0x17/0x1d 三条 setter
  形状（路 P）+ 按键重映射两条（0x2b 三元组 / C2 type2，路 AM，字段域从严），但传输层在本路
  （路 Z）之前只到组帧。传输接线骨架见下一节。
- 页面是 demo 级（内联 CSS/JS、无框架），不是最终产品 UI。
- 本机无 Android SDK 完整打包条件时，可用
  `javac -cp $ANDROID_HOME/platforms/android-35/android.jar src/.../H3SBridge.java src/.../EqFrame.java src/.../WebShellActivity.java`
  单独验证新增类的 Android 侧编译（本仓库开发机已验证通过；`test.sh` 只需 JDK+Python）。

## 传输接线骨架（路 Z / sim-h3stransport，离线端到端已单测）

bridge 到 BLE 的传输层骨架。R3 命令面（组帧+白名单+壳）此前全部就绪、唯一缺的就是这段接线；
本路补齐后整条链「JS 参数 → 组帧 → 写策略 → 串行队列 → 分片 → 收响应 → SN 匹配 → 回调 JS」
**离线可端到端单测**，真机联调只剩「把回环桩换成真 Android BLE 适配层 + 用户授权」。

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

### 真机适配清单（AndroidBleTransport，照单换——联调授权后实现）

实现 `AndroidBleTransport extends SerialBleTransport`，替换的只有「连接假动作」与
「doWriteChunk」两个点，串行/分片/重组全走既有通用代码：

1. **connect()**：`BluetoothDevice.connectGatt(context, false, callback, TRANSPORT_LE)`
   （UUID 常量照 `MainActivity.SERVICE/WRITE/NOTIFY/CCCD`：A002/1/2/2902）；`onServicesDiscovered`
   找 A002/1/2，校验 write 特征属性含 `PROPERTY_WRITE_NO_RESPONSE`；`setCharacteristicNotification(rx, true)`
   → 写 CCCD `ENABLE_NOTIFICATION_VALUE` → **等 CCCD 回调成功 + `onMtuChanged` 实际值**
   （`requestMtu(517)`，但以回调为准，不能假定 517/247）→ 全到位才 `markReady(实际MTU)`
   （连接顺序照 docs/app-protocol.md §1；UpgradeService 是完整先例）。
2. **doWriteChunk(chunk)**：`tx.setWriteType(WRITE_TYPE_NO_RESPONSE)` + `setValue(chunk)` +
   `gatt.writeCharacteristic(tx)`；`onCharacteristicWrite(status==0)` → `chunkWritten(true, null)`，
   失败 → `chunkWritten(false, "GATT status N")`；写回调 5s 缺失直接断链（照 UpgradeService 的
   writeTimeout 先例，不静默丢片）。
3. **notify 入口**：`onCharacteristicChanged`（NOTIFY uuid）→ `deviceNotify(bytes.clone())`
   （后续重组/匹配全在通用层）。
4. **调度**：覆盖 `schedule(delayMs, r)` 为 main `Handler.postDelayed(r, delayMs)`、
   `scheduleTimeout(r)` 为 `postDelayed(r, 5000)`（默认手动队列是 JVM 单测用的）。
5. **主线程**：GATT 调用与回调都过主线程（照 UpgradeService 的 `main.post` 模式）。
6. **断开**：`onConnectionStateChange` 断开 → `markNotReady(reason)`（未完成帧自动按失败回调）。
7. **壳接线**：`WebShellActivity` 改为 `new H3SBridge(new AndroidBleTransport(...), json ->
   web.evaluateJavascript("window.onH3sEvent&&onH3sEvent(" + json + ")", null))`，页面加
   `onH3sEvent` 处理器与连接状态显示。
8. **权限与目标**：`BLUETOOTH_CONNECT/SCAN` 运行时权限；目标地址仍走构建注入的
   `DeviceConfig.ADDRESS` 门槛（照 UpgradeService「禁止连接未确认设备」先例，不开任意设备连接）。
9. **联调顺序**：先只读查询对拍（SN 匹配 + deframe 与 docs/protocol.md 实测帧对照）→
   单条 ANC 切换（响应最小）→ 提示音音量 → EQ 整组；EQ 候选 UNTESTED 不变。
10. **联调时对照项**：真机 setter 响应载荷形状（替换 Loopback 的回显假设）、EQ 写是否需要
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
