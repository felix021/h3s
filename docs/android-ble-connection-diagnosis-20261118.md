# H3S 工具 BLE 控制信道连接失败排查

2026-11-18。在同一部 Android 手机上，QCY H3S 的经典蓝牙音频已连接。H3S 工具扫描了构建时指定的 BLE 地址尾号 `00:9F`，随后直连超时。官方 QCY App 在同一部手机上成功连接到尾号 `27:A1` 的 BLE 控制地址并读取电量。两次操作针对的是不同的 BLE 地址，故障定位到 APK 内置的目标配置。

## 排查对象

| 项目 | 核对结果 |
| --- | --- |
| 源码检查点 | GPU 项目 `~/code/headsets/h3s`，`main` 的 `a8fc237` |
| 手机安装包 | `cn.felix021.h3squery`，`0.6.0-r3-webview`，版本码 `10` |
| APK | 手机上下载目录的 `h3s-query.apk`，与 GPU 的 `tools/android-query/build/h3s-query.apk` 内容一致 |
| APK SHA256 | `286850604baa979e55b802b6a201609b6c8f73aec44c71edd4601d7e114f0a3d` |
| 蓝牙权限 | 手机主用户已授予 `BLUETOOTH_CONNECT` 和 `BLUETOOTH_SCAN` |

源码检查点用于定位当前项目里的连接路径。APK 以哈希区分，现存产物未记录可独立核验的构建提交。

## 手机日志与对照

| 对象 | 地址尾号 | 观察 |
| --- | --- | --- |
| 系统音频链路 | `BB:78` | QCY H3S 的经典蓝牙 ACL 已连接 |
| H3S 工具 | `00:9F` | `dumpsys bluetooth_manager` 记录四次按地址过滤的扫描，每次约 10 秒，均零命中。每次扫描后都注册了 GATT client，约 30 秒后注销。最后一次记录 `CONNECTION_ACCEPT_TIMEOUT (0x10)`，页面显示「连接/初始化超过 30000ms」 |
| 官方 QCY App | `27:A1` | 手机日志记录 `BT_TRANSPORT_LE` 连接成功、`GATT_CH_OPEN`，连接由 `com.qcymall.earphonesetup` 持有。官方 App 状态页显示耳机电量 100%。这个地址尾号也与此前成功查询时的记录一致 |

耳机能维持音频连接，也能正常提供 BLE 控制信道。在 H3S 工具这四次尝试中，连接停在 GATT 建链阶段，未进入服务发现、通知订阅或上层协议握手。现有证据能定位到目标地址填错，无法确定旧 APK 当初从哪里取得了 `00:9F`。

## 构建配置与验证办法

1. [Android 构建说明](../tools/android-query/README.md)要求通过 `H3S_BLE_ADDRESS` 注入目标耳机的 BLE 地址。生成器把它写入构建目录中的 `DeviceConfig.ADDRESS`。
2. [WebShellActivity](../tools/android-query/src/cn/felix021/h3squery/WebShellActivity.java) 把这个常量传给 [AndroidGattLink](../tools/android-query/src/cn/felix021/h3squery/AndroidGattLink.java)。后者先用同一个地址做精确扫描过滤，再用 `TRANSPORT_LE` 直连。广播未命中时，[AndroidBleTransport](../tools/android-query/src/cn/felix021/h3squery/AndroidBleTransport.java) 仍然会尝试直连该地址。
3. 重打包时，从手机的实际成功 BLE 会话取完整地址，用私有环境变量 `H3S_BLE_ADDRESS` 注入，按构建说明运行 `bash tools/android-query/build.sh`。继续使用原签名可覆盖安装。验收用 WebView 连接并查询电量，同时核对 GATT 服务和通知已就绪。报告保留地址尾号供对照。

本次完成了诊断和官方 App 对照。修正配置后的 H3S 工具尚未构建或验证。

## 后续（2026-11-18 深夜，路 BLEDISC）

上节第 3 条「取正确地址经 `H3S_BLE_ADDRESS` 注入」的修复方案已被更好的方案取代：App 改为
扫描 BLE 广播按名字（"QCY H3S"）自动发现耳机地址后连接（即本页观察到的官方 App 做法），
地址不再要求人工提供；`H3S_BLE_ADDRESS` 降级为可选优先地址（注入且扫到时优先用，兼容注入
构建）。扫描 12s 未发现或连接失败均给出明确错误文案，不再无限「连接中」。实现见
`tools/android-query/src/cn/felix021/h3squery/BleDiscoveryPolicy.java` 与该 README「真机适配
清单」第 1/8 条；离线单测全绿，真机验证待做。

## 2026-11-19 广播扫描版复测

广播扫描已经在手机上运行。失败发生在**收到扫描结果之后、选定 BLE 候选之前**。这版只接受优先地址或 `QCY H3S` 名称，而本机历史抓到的 H3S 广播没有 Local Name。本节更正上一节「按名扫描等同于官方 App」的推断。

### 实测结果

手机端 `0.6.0-r3-webview`（版本码 10）、下载目录 `h3s-query (1).apk` 和 GPU 构建产物的 SHA256 相同，均为 `327e068b9d62b545772a78ca0724fe8fc437fc31f48476370237638948053686`。安装时间为 11-19 00:20:17（手机 UTC+8）。对照广播发现改动 `c17c66b`，合入点 `3c5280f`。版本名与上一版相同，以哈希区分。

| 手机时间（UTC+8） | 条件 | 实际结果 |
| --- | --- | --- |
| 00:20:25 | 用户更新后第一次扫描 | 无地址过滤，11.996 秒，Android 记录 1,038 条结果。页面报 12 秒未发现 `QCY H3S` |
| 00:22:38 | 官方 QCY App 仍持有尾号 `27:A1` 的 BLE GATT | 12.000 秒，1,083 条结果，未注册新的 H3S 工具 GATT client |
| 00:28:53 | 退出官方 QCY App，确认 `27:A1` 的 BLE ACL 已断开，再冷启动 H3S 工具 | 11.999 秒，1,156 条结果。原生日志从「发现扫描」直接到 00:29:05「连接失败」。页面显示同一超时，经典蓝牙音频仍连接 |

上表是扫描回调总数，可以包含重复广播。它证明扫描权限和平台扫描有效，但无法从总数判断这三个窗口是否收到目标广播。第三轮复现说明，仅释放官方 App 的 BLE 连接不足以解决问题。这三轮都未进入服务发现、通知订阅或控制协议握手。

原生日志还确认该 APK 仍注入了上一版的错误地址尾号 `00:9F`。在扫描改动里，这个值是可选优先地址，只在扫到相同地址时采用。其他广播仍会进入按名匹配。因此它保留了错误配置，但并没有把 Android 的扫描范围锁回旧地址。参见 [AndroidGattLink](../tools/android-query/src/cn/felix021/h3squery/AndroidGattLink.java) 和 [BleDiscoveryPolicy](../tools/android-query/src/cn/felix021/h3squery/BleDiscoveryPolicy.java)。

### 广播识别

- 对 11-09 至 11-10 的私有扫描记录按此前成功连接的 BLE 地址筛选，得到五条目标记录。五条的 `BluetoothDevice.getName()` 都记录为 `null`，AD 结构均无 Shortened Local Name（`0x08`）或 Complete Local Name（`0x09`）。
- 五条都包含厂商特定 AD（`0xFF`）：公司 ID 为 `0x521C`，去掉两字节公司 ID 后有 24 字节厂商载荷，开头两字节大端为 `0x417C`，对得上 H3S 的型号 ID。另有 16 位服务列表 AD（`0x03`）包含 `0xFEE8`。这是广播字段。连接后仍要验证本项目使用的 GATT 服务 `A002` 和收发特征。
- 官方 QCY App 的本地反编译参考中，普通设备扫描会用公司 ID 筛选厂商数据，收到长度至少 20 字节的该类数据后构造 `Devicebind`，并把回调里的 BLE 地址保存进去。官方 App 能连通的事实也可能来自已保存的映射，不能据此断言这一轮官方 App 重新按广播发现了耳机。
- 独立扫描探针安装被手机系统的未知来源确认界面拦下，本次取消安装。当前固件这几个窗口内的逐包结构、`neverForLocation` 是否影响目标包交付，尚未实测。文中广播字段来自上述历史记录，未保存完整设备地址或原始广播。

当前源码在 `AndroidGattLink.scanName()` 读取广播名，空时读系统缓存名。两者都空且地址不匹配时，`onScanResult()` 会在候选上报之前丢掉这类包。对五条历史目标记录，这个丢弃条件确定成立。后续应用改动可以用 `0x521C` 厂商数据和 `0x417C` 型号一起识别候选，校验长度，从回调取得 BLE 地址，再用 GATT 服务与特征验证身份。已确认过的地址可以作为重连输入。无名目标广播、同公司不同型号、截断载荷和多个同型号候选应各有覆盖。仅延长扫描时间无法让无名包通过名称匹配。

### 重复失败的界面状态

另一次未重启进程的复测中，扫描结束后页面仍显示「扫描中」。源码中每次开始发现都会推送 `scanning`，超时经 `failConnect()` 调 `markNotReady()`。后者在第一次失败后把 `disconnectReported` 置为真，只在 `markReady()` 恢复为假。若两次尝试中间都没连成功，第二次的原生失败会被去重，WebView 收不到终态。修复时应保证每次新的连接尝试都有一次终态通知，并保留同一次尝试内的重复事件去重。验收应连续触发两次扫描失败，检查两次均在 12 秒后结束页面的「扫描中」。参见 [AndroidBleTransport](../tools/android-query/src/cn/felix021/h3squery/AndroidBleTransport.java) 和 [SerialBleTransport](../tools/android-query/src/cn/felix021/h3squery/SerialBleTransport.java)。

同一进程的第二次失败也已明确复现。00:29:34.830 原生日志再次记录「扫描 12000ms 未发现」终态，同一轮的页面仍显示「连接中／扫描中」，电量未知。这把实际扫描失败与页面未收到终态区分开了。
