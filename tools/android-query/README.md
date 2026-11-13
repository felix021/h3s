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
