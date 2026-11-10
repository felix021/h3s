# USB 救砖：从失效的 EQ 实验恢复 ANC 两态版

2026-11-10 在一台 QCY H3S 上实际完成：BLE 无法连接、按键复位无响应时，利用仍可响应的 USB 控制入口进入 BR28 ROM，加载 RAM 程序，取得两份一致的完整 Flash 备份，再恢复故障分区。6 个扇区分别读回通过，最后整片 2 MiB 读回与目标完全一致。用户随后确认蓝牙回连和播放恢复；**模式键随后被报告无反应（具体按键与手势仍在核对），按键验收仍在排查，不能称所有功能已经恢复**。

恢复目标是 Release 中的 `H3S_0.1.1.8_ANC_ON_TRANSPARENT.ufw`：

```text
1326848 bytes
b79cb3a6f8702eb9e1d12eaf181a968f6b5848a66c051b2c792c39aa8b42fa1b
```

这是恢复了这一已知故障镜像的应用区，**不是通用全片烧录器**。没有验证完全不枚举 USB、ROM 损坏、另一硬件批次、未知分区或 Flash 芯片损坏的恢复方法。本次仍能响应 EP0，才具备软件进入 ROM 的条件。没有找到或验证硬件强制进下载模式的按键组合。

## 实测环境与文件保管

成功路径为有 root 的 Android 手机上的 64 位 Linux 用户空间，Python 3、标准库、`/dev/bus/usb`。本仓库工具直接调用 Linux usbfs ioctl，不依赖 PyUSB、BlueZ、ACE、QCY App 或 Android 重新编译。普通 Linux 电脑具有相同 usbfs ABI，但未在本次用另一台 Linux 电脑做实机回归。

Mac 上普通设备和 ROM 设备的配件授权都已允许；能读标准描述符，但 SCSITask 未暴露可用接口，libusb claim 失败，原生 IOUSBHost 也无法打开接口（直接 IOServiceOpen 返回 `0xe00002e2`）。因此本次改用安卓成功。不能把这个结果写成所有 Mac 都不能用，也不要重复把同一个接口错误归因于用户未点允许。

完整备份包含蓝牙身份、配对、VM 配置与校准信息。工具用新的工作目录和 `0600` 文件保管备份、计划、目标与日志；这些文件放在忽略目录 `analysis/`，不上传 GitHub。UFW 附件只有 942080B 的 `flash.bin`，**不是 2MiB 整片备份**，不能直接用它覆盖设备。

## 两种 USB 身份

| 状态 | VID:PID | 内容 |
| --- | --- | --- |
| 普通应用 | `3654:4a55` | QCY H3S，音频与 HID 复合设备 |
| ROM 下载 | `4c4a:3442` | `BR28UBOOT1.00`，MSC `08/06/50`，IN `81` / OUT `01`，64B 最大包 |

成功的标准 SCSI INQUIRY 返回厂商 `BR28`、产品 `UBOOT1.00`。仅看到普通 QCY USB 身份、一个 HID report 或蓝牙广播，都不等于已进入可写 Flash 的恢复通道。

普通应用可能反复掉线重枚举。手机上曾出现 `device descriptor read/64, error -71`，期间仍有短暂的 EP0 可用窗口。工具在 30 秒内观察目标身份、尝试只读能力查询，在确认后只发送一次进入请求；不循环发送重启或擦写命令。

## 进入 ROM 的准确控制请求

实际 QCY 机器码 `0x060066ce..0x06006710` 与参考 [JL701N user_setup.c](https://gitlab.zh-jieli.com/ai_test/701n/-/blob/67328f4741c69993c88e504c67377b196c724f97/SDK/apps/common/device/usb/device/user_setup.c) 相符：

| 字段 | 值 |
| --- | --- |
| bmRequestType | `83`，IN / standard / OTHER |
| bRequest | `00`，GET_STATUS |
| wValue / wIndex | `00fc` / `00fe` |
| wLength=8 | **只读能力检查**，实测返回 8 个 `ff` |
| wLength=0 | **进入下载模式并重启**，调用 `go_mask_usb_updata()` |

这里虽是标准请求编码，数值组合的含义是厂商实现约定。0 长度请求不是只读操作。重启可能使请求本身返回协议错误；本次 errno71 后确实出现了 ROM 身份。以新的 USB 身份及 INQUIRY 判断结果，不以控制传输是否正常返回为唯一依据，也不因此盲目重发。

## RAM loader 与 USB 协议

采用 [kagaimiq/jl-uboot-tool](https://github.com/kagaimiq/jl-uboot-tool/tree/adb3f18889e88ac512ce0a3c4d8cc3d3cb30696a) 的 BR28 loader 和协议资料。固定提交、MIT 归属和许可见 [THIRD_PARTY.md](../THIRD_PARTY.md) 与 [UPSTREAM_LICENSE](../tools/usb-recovery/UPSTREAM_LICENSE)。程序按摘要下载 loader，不把第三方二进制混入本项目自编源码。

```text
data/loaderblobs/usb/br28loader.bin
23552 bytes
ab1b77eb73385aaa78ab769253b2af7ef63d0ade46f391bd7424cc598a40267c
RAM address 0x120000; block 512B; argument 1 (SPI NOR)
```

该 blob 是 MengLi 预编码形式，BR28 ROM 的 memory-rw-mengli-crypt quirk 已核对，**按原文件分块上传，不再额外编码一次**。`FB06` 把它装进 RAM，`FB08` 跳转运行。这两步没有烧写 Flash，也没有写 OTP/chipkey。

USB 使用 MSC Bulk-Only Transport：31B CBW → 数据阶段 → 13B CSW，检查 `USBC/USBS` 签名、tag、residue 和 status。这里的 512B 协议块和 USB 64B 最大包是不同层，不要手工把 CBW 当作音频/HID 报告发出去。

厂商 CDB 为大端 2B opcode 加参数，总共补 `ff` 至 16B；地址和长度大端，数据 CRC16 为小端，算法 `binascii.crc_hqx(data,0)`。

| 指令 | 参数 | 用途 |
| --- | --- | --- |
| `FB06` | addr4 + len2 + `00` + crc16LE | ROM 写 RAM，随后 DATA OUT |
| `FB08` | addr4 + arg2 | 运行 RAM loader，16B 响应 |
| `FC0A` | 无 | 在线存储信息；实测 type3 / SPI NOR，ID `cd7015`，容量2MiB |
| `FD05` | addr4 + len2 | 读 Flash，本次每块512B |
| `FB01` | addr4 | **擦除单个4KiB扇区**，16B响应 |
| `FB04` | addr4 + len2 + `00` + crc16LE | **写 Flash**，随后 DATA OUT |

工具未实现整片擦除、64KiB块擦除或 OTP/chipkey 写入。参考库中存在某个指令不表示这一 loader 支持：本次 `FC03` 可选状态查询未成功并出现 stall，后续流程移除它，仅保留实际成功的命令。

读取结束并离开工具后，设备曾自行回应用模式；不能假定 RAM loader 会无限留驻。分析或计划完后，**断开并重新连接 USB，确保下一次从新进入的 ROM 开始**。已运行 RAM loader 的 INQUIRY 名称仍相同，不能凭产品名识别其运行阶段；不要对仍在运行的 RAM loader 再上传 ROM 编码 blob。

## 实际物理布局与恢复范围

这张表来自两份一致的整片读取，不能仅从 OTA 包推出来：

| 区域 | 地址 / 范围 | 故障时内容 |
| --- | --- | --- |
| 引导区 | `000000..001fff` | 保留 |
| bank A boot-info | `002000..00201f` | 序号2、有效 CRC |
| bank A 应用密文 | 从 `002020` 开始，929875B | **完整等于已验证 ANC 包的应用** |
| bank B boot-info | `0ee000..0ee01f` | 序号3、有效 CRC |
| bank B 应用密文 | 从 `0ee020` 开始，930245B | **完整等于故障 r2 的应用** |
| VM | `1da000..1ddfff` | 设备运行配置，本次变化发生于 `1db000` |
| BTIF 等保留区域 | 后续保留区 | 保留，不用 UFW 填充覆盖 |
| TON / TON1 | `1e1000` / `1ed000` | 保留 |
| ANCIF / ANCIF1 | `1fe000` / `1fe100` | 保留校准 |

两套应用使用各自从 0 开始的 SFC 相位；bank B 不能用绝对物理地址作为 SFC key 相位。两份 boot-info 的设备参数尾部相同，偏移 `+6` 在实机中分别为2和3。恢复保留 bank B 的3及设备参数，更新应用明文／密文 CRC、长度与头 CRC；不把 UFW 头中的保留／`ff` 字段直接覆盖到设备头。

恢复把 bank B 的应用换回已验证 ANC 包，移除末尾370B实验扩展。实际仅改变419B，但 NOR Flash 按4KiB扇区擦除，因此写入范围是以下6个扇区：

```text
0x0f3000  0x113000  0x166000  0x1d0000  0x1d1000
0x0ee000  # 最后提交包含 boot-info 的扇区
```

每次擦除都立即读回全 `ff`，每扇区写8块512B再读回对比；最后重新读完整2MiB，与预计镜像逐字节比对。bank A 和 `[0x1d2000,0x200000)` 所有字节均保留。**没有切换序号、尝试破坏坏分区头以“强制回滚”，或整片写入工厂包。**

## 可复用命令

工具源码在 [tools/usb-recovery](../tools/usb-recovery/)，Python 3.10+。以下 `analysis/rescue-session` 必须是新的、位于私有磁盘上的目录；运行前阅读本页的适用范围。`plan` 只处理离线文件；`backup` 进入 ROM／加载 RAM 后只读 Flash；只有 `restore --apply` 执行擦写。

```bash
# 取得公开的已验证 ANC 包，并下载固定 RAM loader
python3 tools/fetch_firmware.py --variant current
python3 tools/usb-recovery/h3s_usb.py fetch-loader \
  --output analysis/usb-loader/br28loader.bin

# 手机／Linux USB 主机上，耳机从新连接状态开始
sudo python3 tools/usb-recovery/h3s_usb.py backup \
  --loader analysis/usb-loader/br28loader.bin \
  --workdir analysis/rescue-session

# 两份整片备份必须一致，生成离线恢复计划，查看六扇区及全部保留范围
sudo python3 tools/usb-recovery/h3s_usb.py plan \
  --good-ufw firmware/H3S_0.1.1.8_ANC_ON_TRANSPARENT.ufw \
  --workdir analysis/rescue-session
sudo cat analysis/rescue-session/plan.json

# 断开并重新接上耳机 USB，确保旧 RAM loader 已退出。
# 这个明确的 restore --apply 命令才会执行 Flash 擦写。
sudo python3 tools/usb-recovery/h3s_usb.py restore --apply \
  --loader analysis/usb-loader/br28loader.bin \
  --good-ufw firmware/H3S_0.1.1.8_ANC_ON_TRANSPARENT.ufw \
  --workdir analysis/rescue-session
```

工具只接受本例 profile：A 应用已为 b79、B 应用为已知 r2 密文摘要、两头有效且序号2/3、2MiB布局和指定6扇区。其他现场会拒绝，不能通过放松摘要／改地址直接套用。本次公开整理后的 CLI 没有再次对耳机刷写；底层传输、备份与扇区流程来自上面的实际恢复，公开离线 planner 已用私有原始备份逐字节重现当次419B目标，另有脱机失败路径测试。

`restore` 还会现场重读两次整片并比较。曾有一次准备后原始摘要变化，写入门槛及时阻止，未擦除：两次进入 ROM 之间，故障应用在 VM 写了配置。最终只允许文档 VM 区变化，其他所有代码、资源和校准仍须匹配旧备份；保留**最新** VM，不用历史 VM 覆盖。这不是忽略摘要校验或容许不明差分。

恢复日志存在时不自动再来一次。任何擦除／写入／读回失败都停止；应先检查日志和已完成扇区，不得删除日志后无分析重刷。本工具没有实现断点续写，也未做断电故障注入验证。

## 写后验收

`FLASH_RESTORE_VERIFIED` 只证明读回与目标一致。随后拔 USB、按平时方式开机，检查正常连接、蓝牙播放、开关机与独立 ANC 按键，再读取 QCY 服务和版本。版本仍为 `V_0.1.1.8 / 280`，版本号不区分本项目两个同版本镜像。

本次已恢复 A2DP/HFP/BLE，PID16764、版本查询和电量查询成功，用户确认播放正常。模式键无反应的反馈仍未排除，因此验收记录保留该未完成项。不会把“救回连接”写成“EQ 功能实现”或“全部按键通过”。
