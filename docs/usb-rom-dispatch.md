# 固定 BR28 ROM 的 USB 分派与 RAM 访问边界

[analyze_usb_rom.py](../tools/analyze_usb_rom.py) 对已取得的私有 128 KiB ROM 做限定静态检查，并离线执行两组 FB08 尾部指令。它核对 **9 个窗口摘要、62 个指令锚点**；两组测试分别完成 **23 条模型指令**，停在尚未执行的 RAM 入口。精简结果见 [usb-rom-dispatch.json](../evidence/usb-rom-dispatch.json)。

这次核对纠正了一项不能直接套用的上游描述：**这份 ROM 在 FB06 完成路径清除 MSC hook；FB08 尾部保留 hook。** 同名 USB 命令可能先被 RAM loader 接管，不能只凭 INQUIRY 身份判断实际执行哪条路径。

没有连接设备、执行自写 payload 或运行 CI。此工具不能批准新的 RAM 上传、调用或固件操作，`deployable=false` 保留。

## 固定输入与复现

ROM 必须为 131,072 字节，SHA-256 为 `7cae0a02e3ab3755c1e392f03c89222dd701065ec9bd3ec8ac109a7b3f40f793`。取得过程见[实机 ROM 窗口读取](rom-read.md)；原始内容不公开，工具不下载或获取 ROM。

解码器与指令模型固定为 [pi32v2-isa 3608ed19bc21324f8ecfe79cc0018d05e43d9563](https://github.com/jvitkauskas/pi32v2-isa/tree/3608ed19bc21324f8ecfe79cc0018d05e43d9563)，通过现有 `upstream()` 校验 Git revision 和已跟踪文件。首次准备该源码缓存可以调用：

```sh
PYTHONPATH=tools python3 -c 'from probe_pi32v2 import upstream; upstream(fetch=True)'
python3 tools/analyze_usb_rom.py \
  --rom "$PRIVATE_ROM" \
  --report analysis/usb-rom-dispatch.json
```

`--rom`、`--report` 均必填。工具先校验完整 ROM 摘要，再核对各窗口摘要、选定指令身份／操作数／分支目标，最后执行两组隔离测试。全部通过后才写报告；拒绝覆盖输入 ROM、工具自身及其符号／硬链接。校验使用显式异常，`python3 -O` 不会关闭它们。

公开报告只包含摘要、选定指令位置和身份、有限派生字段及测试端点，不含私有路径、ROM 字节、完整反汇编或执行 trace。窗口 hash 和固定输入验证说明分析对象一致，不证明完整 USB 会话或硬件行为正确。

## hook 决定命令由谁处理

实际 ROM 在 `0x4938` 读取 `0x181428` 的 hook 指针；非零时在 `0x4942` 先调用 hook。若返回值非零，`0x4944` 跳过 ROM 的命令处理。因此下表描述的是 **hook 没有消费命令时**的 ROM 路径。

| 命令 | 固定 ROM 路径 | 能确定的范围 |
| --- | --- | --- |
| FB06 | `0x4982` 调用接收包装 `0x4720`，`0x498e` 调用原地变换 `0xaa1e`，随后 `0x4994` 将 hook 槽写为 0 | 清 hook 位于这一正常完成路径；本工具未执行 USB 接收、CRC 接受或失败路径 |
| FB08 | `0x4956→0x4a70`，建立 arglist，`0x4aba` CALL 命令给定地址 | 该尾部不清 hook；入口 R0 是参数结构地址，不是整数参数本身 |
| FD07 | `0x49ce` 变换源区域，`0x49e0` 调用发送，`0x49ec` 再次变换 | 正常返回路径试图恢复源 RAM；没有证明传输中断、超时或异常时一定恢复 |

固定上游 [USB loader v2 协议](https://github.com/kagaimiq/jl-uboot-tool/blob/adb3f18889e88ac512ce0a3c4d8cc3d3cb30696a/docs/usb-loader-v2.md#jump-to-memory) 曾概括为每次 FB08 清 hook。这一点与此处取得的实际 ROM 路径不同，不能作为这颗芯片的执行前提。

既有[恢复流程](usb-recovery.md)及[ROM 读取流程](rom-read.md)使用摘要固定的 BR28 RAM loader。loader 的 hook 可以消费命令，ROM 尾部未必执行；调用一次落回 ROM 的 FB08，也不能据此推断后续 FD07 已切换为 ROM 的编码语义。本工具只核对 ROM，不下载或重新认证 loader。此前 RAM loader 的成功读取记录及其阶段约束保持原义，不能换算成原始 ROM 任意 RAM／SFR 读取的实测。

## FB08 的参数与返回值

实际 CALL 前，R0 指向 `0x18142c`，其五个 word 为：

| 偏移 | 内容 |
| --- | --- |
| `+0` | 发送函数指针 `0x4738` |
| `+4` | 接收函数指针 `0x4720` |
| `+8` | hook 指针槽地址 `0x181428` |
| `+12` | 命令中的 16 位参数 |
| `+16` | 0 |

CALL 的返回地址为 `0x4abc`。该处第一条指令将 **R0 覆盖为 `0xfb`**，随后构造 16B 响应，并在 `0x4acc` 调用发送函数。因此不能把“写一个 getter，让它把特殊寄存器放入 R0 后 RTS”当作可直接经 FB08 取得结果的方法；普通函数返回值没有被这样转发。响应在调用返回后构造，也不表示全部特殊寄存器、栈银行或中断配置都已恢复。

两组隔离测试直接从 `0x4a70` 开始，hook 初值分别为 0 和 `0x121cfe`，参数为 `0xa5a5`。第二组明确假设前面的 hook 调用已返回 0；测试不执行该 callback，不证明安装该 hook 后一定会落入 ROM 路径。

测试执行真实指令直至 CALL 将 PC 设置为 `0x120000`，立即停止，**不取指或执行那个 RAM 地址**。每组仅发生五次 arglist word 写入，hook 原值不变。R4／R7／R8 与工作区是显式合成输入，其余寄存器初值也不是实测状态。内存拒绝未初始化读取、ROM 写入、非对齐／非 word 写入及 arglist 之外的任何写入；没有替换 CALL、跳过未知指令或注入 payload 返回。

## FD07 原地变换与 USB 中转缓冲区

`0xaa1e` 的代码操作 CRC 引擎 `0xfd3500`，结合 `0x1f619` 的表逐字节 XOR 并写回源位置。这与固定上游 [BR28 MengLi quirk](https://github.com/kagaimiq/jl-uboot-tool/blob/adb3f18889e88ac512ce0a3c4d8cc3d3cb30696a/data/chips.yaml) 的解释相符；本工具没有模拟 CRC 引擎或认证编码算法的硅片行为。

这份 ROM 也不是完全不经缓冲区的直接发送：初始化代码设置 USB buffer 为 `0x181040`、`0x181080`、`0x1810c0`。发送函数经 `0x181444` 获取中转缓冲区，在 `0x4788` 调用 `0x413c`，每块最多复制 64B；接收函数经 `0x181454` 获取缓冲区，在 `0x46d4` 调用相同复制函数。64B 是内部发送分块，**不是获准的 FD07 请求上限**，也不改变此前 RAM loader 128B 分块的实测约束。

源 RAM 仍然先被原地变换；缓冲区存在不消除这一副作用。ROM／只读区域／SFR 不具备普通可写 RAM 的行为，不能因此扩大既有读工具的窗口或阶段白名单。发送函数未正常返回时，后面的还原调用是否执行也没有保证。

## RAM 可用性和返回安全仍未证明

确认 USB 的几个缓冲地址，没有证明其他 DMA、CPU1 或缓存不访问拟用 RAM。自写探针曾考虑的 `0x120000..0x121a00` 区域，覆盖正常 QCY 应用的 [MMU 表 `0x120000..0x122800`](mmu-startup.md) 的一部分。此前固定 loader 曾成功使用这个地址，只是该特定流程的兼容性证据，不能独立证明新 payload 的窗口所有权。

正常应用进入 ROM 所需的复位路径、各域复位值、CPU1 hold、MMU 失效、缓存一致性及执行保护范围仍需分别约束；USB 身份不足以确定这些状态。自写 RTI 探针还需要证明 mode、栈银行及中断配置的保存恢复，采样完成 magic 和命令响应都不能代替恢复验证。当前静态结果不批准任何自写 payload 的现场执行，也不补足[首任务 RTI 的严格边界](dualcore-startup.md)。

本工具的普通／`-O` 正例输出一致；90 个负例涵盖错误 ROM、9 个窗口与 62 个锚点变异、严格内存拒绝、跳出／早停、输出覆盖及必填参数，全部在 `-O` 下拒绝。这是独立工具验证，不代表又完成一轮整套回归、硬件测试或完整 ABI 认证。
