# 实机读取 CPU ROM 地址窗口

2026-11-10，在已恢复正常的 QCY H3S 上取得 **CPU 地址 `0x000000..0x020000`（右端不含）的 128 KiB 内容**。两次完整读取逐字节相同，固定参考 SDK 的 **162 个具名函数导出入口**全部解码为 pi32v2 `goto.l`，目标均位于该窗口。用户随后反馈：“耳机启动后正常”。

这是芯片内部 ROM 地址空间的读取，和此前取得的 **2 MiB 外置 Flash 备份**是两份不同材料；OTA UFW 也不包含这份 CPU ROM。我们没有证明该窗口就是硅片上全部 Mask ROM 的物理范围，也没有确定其他映射或硬件批次。

公开结果：[evidence/rom-read.json](../evidence/rom-read.json)。原始 ROM、逐命令日志和完整设备备份留在私有目录，未提交 Git，也未添加到 `firmware` Release；现有固件附件和 tag 没有改变。

## 文件身份与代码核对

每份窗口文件长 `131072` 字节，SHA256：

```text
7cae0a02e3ab3755c1e392f03c89222dd701065ec9bd3ec8ac109a7b3f40f793
```

| SDK 导出名 | 导出入口 | 真实函数目标 | 初步检查 |
| --- | --- | --- | --- |
| `memcpy` | `0x1fd74` | `0x413c` | 寄存器保存、重叠判断、内存复制指令 |
| `memset` | `0x1fd80` | `0x4226` | 长度和对齐判断、填充指令 |
| `mask_init` | `0x1fde8` | `0x4590` | 保存参数，涉及 `0x19fd68` 附近 RAM 初始化 |
| `_pll_init` | `0x1fe14` | `0xc2ce` | 访问 `0xfd4600` 起 PLL0 寄存器 |

导出名来自固定参考 SDK `67328f4741c69993c88e504c67377b196c724f97` 的 `maskrom_stubs.ld`，文件 SHA256 为 `331e973cbd71d5f9add4b77a796036f88e8dad93f940b70bb82398b7748a4547`。该链接文件还包含数据表、RAM 地址和大小常量；162 指的是窗口末端具名函数入口，不是文件中所有符号。完整映射列在公开 JSON。

`goto.l` 编码为两个小端 16 位字：`1110101011pppppp ssssssssssssssss`。将 `ps` 视为有符号 22 位数，乘 2，再加**指令末尾地址**得到目标。编码和相对基准可核对固定 [pi32v2 ISA 表](https://github.com/jvitkauskas/pi32v2-isa/blob/3608ed19bc21324f8ecfe79cc0018d05e43d9563/isa/pi32v2.toml)。这些匹配支持“取得有效 ROM 代码”的判断，不证明整个 SDK 和 QCY 工程完全相同。

## 实际读取流程

1. 从正常 QCY USB 身份 `3654:4a55` 开始。EP0 请求 `bmRequestType=0x83, bRequest=0, wValue=0xfc, wIndex=0xfe`，长度 8 返回全 `ff`；同请求长度 0 切换模式。只发一次切换，再等待 `4c4a:3442`。切换时曾出现 errno71，必须以重新枚举和身份校验为准。
2. 校验 INQUIRY 的 `BR28 / UBOOT1.00`。使用与救砖相同的固定 BR28 loader，23552B，SHA256 `ab1b77eb73385aaa78ab769253b2af7ef63d0ade46f391bd7424cc598a40267c`。`FB06` 按 512B 上传到 RAM `0x120000`，逐块 CRC；`FB08` 从该入口以参数 1 运行；`FC0A` 确认 SPI NOR 类型 3。
3. 在 **RAM loader** 中使用 `FD07` 读取 CPU 地址：参数为 4B 大端地址 + 2B 大端长度，返回对应内存字节。它与 `FD05` 的 Flash 偏移读取不同。先对导出区重复小块探测，再用 **128B** 分块取得窗口两遍，比较字节和 SHA256。
4. 对照参考导出表，核对 162 个跳转与目标；抽查真实函数指令。断开 USB，手动启动耳机；用户确认启动后正常。

成功会话的命令日志汇总：INQUIRY 1 次，`FB06` 46 次，`FB08` 1 次，`FC0A` 1 次，`FD07` 2054 次。实际脚本先双读高段 `0x4000..0x20000`，再双读低段并合并，中间另有小块探测；因此不是单纯 2048 次全窗口分块。**Flash 擦除／编程和 OTP 写入均为 0 次。** 本轮没有再做整片 Flash 前后比较；正常应用重启也可能保存 VM，不能据此宣称物理 Flash 每字节未变。

### 512B 分块失败的纠正

前两次探测分别在地址 `0`、`0x4000` 请求 512B，均导致 USB 端点关闭、设备回应用模式；同一 `0x4000` 地址读 32B 成功。随后改成 128B 后，**包括地址 0 在内**的整个窗口两遍读取成功。因此不能把最初失败解释为地址 0 不可读。

上游工具的 `mem_io_max=256`，与 Flash 的 512B 默认值不同。我们实际确认 32B、160B 探测和 128B 连续读取可用；**未实测 256B，不知道精确缓冲上限**。不要沿用 Flash 分块或自动增大读取长度。512B 的 RAM loader 上传成功与 512B 的内存读取失败并不矛盾，它们是不同命令。

上游 [Read memory 协议说明](https://github.com/kagaimiq/jl-uboot-tool/blob/adb3f18889e88ac512ce0a3c4d8cc3d3cb30696a/docs/usb-loader-v2.md#read-memory) 还说明部分原始 UBOOT1 的内存读会对源位置执行 MengLi 原地变换。这里使用已加载的 RAM reader，不直接对原始 UBOOT1 的 RAM／SFR 做任意读取。

后续[固定 ROM 的离线分派核对](usb-rom-dispatch.md)确认：这份 ROM 在 FB06 完成路径清 hook，FB08 尾部保留 hook；ROM 的 FD07 对源 RAM 原地变换、经 USB 中转缓冲区发送后再变换。这些结论不改变上面的 RAM loader 实测流程，也不能据一次 FB08 或 INQUIRY 身份推断后续命令已切回 ROM 语义。

## 整理后的工具

[read_rom.py](../tools/usb-recovery/read_rom.py) 是成功会话后整理的独立入口：固定 loader 摘要、顺序和入口；只有确认 loader 后才允许 `FD07`，窗口限 `0..0x20000`，每次最多 128B。Flash 读取、擦写、OTP、任意跳转和重启命令均拒绝。没有扩大原救砖工具的通用命令白名单。

要求 64 位 Linux／rooted Android、usbfs 权限、USB 数据线及**刚接入的正常 QCY USB**。已有 BR28 ROM／loader 身份会被拒绝，因为 INQUIRY 无法可靠区分两者；每次会话必须重新建立正常 USB 起点。以下是有意操作设备时的命令，不属于本机检查：

```bash
python3 tools/usb-recovery/h3s_usb.py fetch-loader \
  --output analysis/usb-loader/br28loader.bin
sudo python3 tools/usb-recovery/read_rom.py \
  --loader analysis/usb-loader/br28loader.bin \
  --workdir analysis/private-rom-NEW
```

输出目录必须新建，目录权限 0700、原始文件 0600。若 sudo 生成的文件需要普通用户分析，只对本次私有目录调整所有者。读取失败立即停止，不自动重试、刷写或恢复。完成后拔 USB，手动重启，检查设备；不要为了测试工具整理而再次操作已经恢复正常的耳机。

**公开入口已通过离线约束测试，但整理后没有再次实机运行。** 成功实测来自此前的私有分阶段脚本；两者共用已有 USB 传输、loader 和协议原语，公开入口改为两遍顺序读取整个窗口，并增加了固定范围及阶段限制。不能将此前实测当作新入口的逐行实机验证。

[validate_rom.py](../tools/usb-recovery/validate_rom.py) 完全离线，不打开 USB：

```bash
python3 tools/usb-recovery/validate_rom.py \
  --first /private/path/rom-000000-020000.pass1.bin \
  --second /private/path/rom-000000-020000.pass2.bin \
  --stubs /private/path/maskrom_stubs.ld > analysis/rom-validation.json
python3 tools/usb-recovery/test_rom.py
python3 tools/check_local.py
```

本次私有原始读取已通过公开校验器，162 个映射与会话摘要一致。默认本机检查只运行假传输测试，不要求私有 ROM，不连接硬件，不运行 GitHub CI。

## 对模拟工作的影响

此前报告中的“缺少 ROM 字节”是当时的状态；现在 `_pll_init`、`mask_init` 的真实代码已取得，可以沿它们继续分析。后续已完成[真实ROM离线执行](rom-execution.md)：可选只读映射、冷PLL路径新边界，以及隔离函数回放。**没有完成整机启动。** 原探针默认不提供私有ROM，仍保留旧报告和原停止位置。

下一步可以在明确的 ROM 映射和 RAM 初态下离线回放，遇到未知寄存器继续停止。PLL 锁定、控制地址 `0xf2c11c`、IPMASK／异常／中断、双核与真实交接／校准状态仍需建模。取得代码让我们能看到它要求哪些硬件行为，不会自动补齐这些行为；也不构成新固件可刷的批准依据。
