# Android OTA实现与实际刷写过程

## 采用的SDK和传输

客户端使用官方 `jl_bt_ota_V1.10.0_10932-release.aar`，固定来源提交和SHA256在 [依赖清单](../data/dependencies.json)。QCY自己的BLE OTA也封装官方BluetoothOTAManager，但使用QCY A002服务和自有GATT收发实现。

实际配置包括BLE priority0、关闭设备认证流程 `setUseAuthDevice(false)`、关闭JL服务器，候选文件在本地。关闭认证是对照QCY实现的配置，不是声称绕过了耳机原本要求的认证。

## 设备驱动的数据请求

| opcode | 方向／阶段 | 当前实现 |
| --- | --- | --- |
| E1 | 主机查询升级检查字段 | 返回文件offset/len；本机为0/0 |
| E2 | 主机查询是否允许升级 | 在E1=0/0时使用SDK同样的参数00；返回0是条件允许 |
| E3 | 主机请求进入升级 | 只在本次确认和即时复查后允许 |
| E4 | 退出升级相关命令 | SDK流程需要时受限放行，不提供任意入口 |
| E5 | 设备请求文件块，主机响应 | offset_BE32 + length_BE16；响应status/SN后跟精确文件切片 |
| E6 | 主机读取最终更新结果 | 成功结果为0；历史有1和0x8E两种失败 |
| E7 | 成功后重启 | 正常流程最后发出，发送队列必须完成 |
| E8 | 设备报告／协商更新内容大小 | SDK处理，限定帧形状 |

E5请求的body包含SN、4字节offset和2字节length，范围是**uFW文件偏移**，不是任意flash写地址。length0表示EOF。SDK可能重发同一序号或再次请求同一块；核对器必须按实际请求匹配，不能只按进度猜文件位置。

E2在当前0/0分支只发送00，不含候选文件；所以E2=0不能证明该文件的签名、版本、内部身份或最终CRC一定会被接受。真正的文件验证发生在后面的设备流程中。

## 查询层与写入层

`QueryPolicy` 只允许完整、形状匹配的只读帧，且在GATT分片前检查整帧。包括03全部属性、D4读MD5、FF的少数固定查询、C1的固定mask、E1和当前实现的E2分支。所有setter形状和E3～E8在普通查询中拒绝。

`UpgradePolicy` 在未armed时复用只读门槛；armed后仅放行官方SDK本流程需要的请求／响应。不是“打开所有opcode”的总开关。

`MainActivity`承担查询、文件检查和可见确认。`UpgradeService`独立拥有GATT、SDK、文件字节、队列和状态，不依赖Activity是否前台或手机是否锁屏。它使用connectedDevice前台服务与有上限的wakelock，`START_NOT_STICKY`，进程重启不恢复授权。

## 升级门槛与单次确认

公开客户端固定接受：

- 新包SHA256 `b79cb3a6...`，长度1326848。
- H3S PID16764、VID/UID7250，构建时显式配置的目标BLE地址。
- 输入版本280（0.1.1.8）；不因为此次有过降级就允许所有旧／新版本。
- 双备份、无需bootloader、正常非强制升级模式、有效QCY电量。
- E1 offset/len0、E2=0、CCCD已订阅、实际ATT MTU至少247。

状态机：

```text
IDLE → PREPARING → PREPARED → RECHECKING → FLASHING → SUCCEEDED
                                                ↕
                                          RECONNECTING
任一错误 → FAILED
```

PREPARED时还没有进入升级。每次计划有独立nonce，最长2分钟；确认后nonce作废，服务再次查询设备、重新核对文件与条件，只有通过后才armed。旧nonce、过期nonce、进程重启、取消准备或终态都不能复用。

当前固定的输入版本和输出版本相同。**同版本更新成功后再读到280，不足以单独证明新逻辑启动。** 本次还结合最终结果、全部数据比对和用户按键反馈验收。

## MTU、分片与队列

旧传输每次硬编码20字节，即便实际MTU已247仍如此。改进后的规则：

1. 捕获实际onMtuChanged；CCCD和MTU都完成后才初始化SDK。
2. 若尚未获得足够MTU，按Android API请求517，但只以实际回调值计算payload。
3. payload=MTU-3。实测247时，522字节SDK帧分为244+244+34，共3片；旧方式需27片。
4. 同一时刻只有一个GATT写入，等待onCharacteristicWrite成功回调，再间隔至少10ms发送下一片。
5. 写回调超时、写请求拒绝、队列过大或状态失效立即终止，不静默丢包／并发写。

WRITE_NO_RESPONSE的回调说明Android传输层处理情况，不等于外设flash读回。更大MTU、10ms间隔和完整终态检查一起用于成功试验；不能把历史0x8E唯一归因于其中一个参数。

## 回连和最终收尾

只允许回连本次固定BLE地址，最多两次；不自动转SPP或连接未知地址。SDK成功后的预期断开与普通失败断开区别处理。SDK回调onStopOTA之后还要等最终发送队列排空，尤其E7不能仅因SDK回调已到就丢弃。

失败释放连接并取消armed，不自动重新刷写。应用取消的是尚未开始的准备，不能把已进入传输的“取消准备”按钮当作安全中断写入的方法。

## 如何认定成功

本次当前包的证据组合：

1. 设备E6返回0，随后E7响应，SDK成功。
2. 1834个非空E5响应和1个EOF独立比对正确，所有62个相对官方包的差分位置都被请求覆盖，没有待回复块。
3. 重连读到正常非强制升级状态，设备仍为预期0.1.1.8。
4. 用户实际使用模式键后确认达到预期。

SDK成功和传输相符都不等于独立flash内容读取。只读自动观察窗口只读到ANC_ON，未捕获完整按键轮转，文档没有将其包装成自动循环测试。

## 操作与原始证据

具体构建、安装和界面操作在 [Android工具README](../tools/android-query/README.md)。`monitor_flash.py`只监控已启动的升级，`verify_flash_log.py`只解析本地日志，不会重试或下发写入。历史四次传输的脱敏报告在 [evidence/ota](../evidence/ota/)，结果比较在 [验证记录](validation.md)。
