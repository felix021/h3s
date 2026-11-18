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
