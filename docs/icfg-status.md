# ICFG：固定来源、反例与首任务 RTI 的证据边界

[analyze_icfg_status.py](../tools/analyze_icfg_status.py) 校验固定的 AC791N 测量记录、BR28 SDK 头文件及可选的私有 ROM，生成[精简证据](../evidence/icfg-status.json)。这是供部分已知 ICFG 模型审查使用的静态来源检查；它不执行 CPU，不认证 QCY 初态、通用 RTI、完整双核启动或 EQ 修复，也没有设备操作或 CI 操作。

## 来源与复现

测量与解码来源固定为 [pi32v2-isa，3608ed19bc21324f8ecfe79cc0018d05e43d9563](https://github.com/jvitkauskas/pi32v2-isa/tree/3608ed19bc21324f8ecfe79cc0018d05e43d9563)。工具使用现有 `ROOT`、`PIN` 和 `upstream()`：检查缓存的 Git revision 与已跟踪文件，再逐个核对 14 个原始文件的长度和 SHA-256。它还对选定的 31 条 case／测量记录做规范化 JSON 摘要校验，随后检查数值、批次身份、采样顺序与派生关系。记录定位使用从 1 开始的 JSONL 行号或从 0 开始的 JSON 数组路径；规范化方式记在证据中。

BR28 SDK 固定为 `67328f4741c69993c88e504c67377b196c724f97`。两个原始头文件也固定长度和摘要，并核对 [dependencies.json](../data/dependencies.json) 的相同条目。新增的 [debug.h](https://gitlab.zh-jieli.com/ai_test/701n/-/blob/67328f4741c69993c88e504c67377b196c724f97/SDK/interface/utils/debug.h) 是 7,285 字节，SHA-256 为 `f9d03b0b251f0913fcb858dbf97f599f09d77fdda7bb36fcd81bb3c63e81041e`。该 SDK 不是 QCY 的确切源码。

在仓库根目录运行；首次准备 ISA 缓存可调用既有固定版本下载入口：

```sh
PYTHONPATH=tools python3 -c 'from probe_pi32v2 import upstream; upstream(fetch=True)'
python3 tools/fetch_boot_reference.py --hardware
python3 tools/analyze_icfg_status.py --report analysis/icfg-status.json
```

`--report` 必填。可另加 `--rom "$PRIVATE_ROM"`，输入必须是既有固定 SHA-256 的 128 KiB ROM；工具不获取 ROM。公开证据包含这一可选检查的结果，但不含输入路径、ROM 字节或完整反汇编。不提供 ROM 时，仅 `rom_reset_startup` 变为未检查，其他结论相同。写报告前完成全部验证，并拒绝覆盖输入文件、输入的符号／硬链接及工具自身。

## 原始记录能约束什么

| 来源 | 校验到的事实 | 推论边界 |
| --- | --- | --- |
| app2 controls 5、6；results 第 45、48 行 | 直接写 `ICFG=0x200`，或先写 0 再执行 STI，采样均为 `0x033f0600`。采样先于后面的 `ICFG=0x300` 开中断写入；采样前计数为 0 | 普通 whole-word RW 会预测 `0x200`，与记录不符。这些写法约束状态／原因字段的保留，不给出任意写入的完整 writable mask |
| app3 run `0x3002` 的 9 组 nesting | 低字节对应活动优先级 bitmap；发生两层嵌套的样本同时保留外层与子层优先级位，即使外层已经写过 gate word `0x300` | 低字节不是单一层数，也不能靠 gate 写入假装清除活动上下文 |
| app3 run `0x3001` 的两组返回尾部 | 低字段从 `0x0208` 变为 `0x0700`，高字段在这两组端点保持 | 中间包含恢复 ICFG、RETI、PSR、GPR、RETS，CSYNC 和 RTI，再进行采样；不能把全部变化独立归因于 RTI |
| v4 results 第 291 行 | ROM runner 内，RTI 前后 ICFG 均为 `0x01090700`，其中 gate8 原本已开 | 不能推出所有 RTI 都保留整个 ICFG，也不能确定原 gate8 关闭时的结果 |
| v5 results 第 273、294 行 | 实际 syscall 入口的 ICFG 为 `0x01090200`，低字节为 0 | `ICFG & 0xff == 0` 不能单独证明 thread mode |
| app4 run `0x4005` 的 9 组样本及当次 peer 源码快照 | 栈端点可重复，但 RTI 后先执行 CLI 和 `ICFG=0`，才采样 USP／SSP／SP | 没有立即返回后的 ICFG 样本，不能补足首任务 gate 或 bank 规则 |

SDK 的 `cpu_in_irq()` 使用低字节；未启用 unmask 支持时，`cpu_irq_disabled()` 检查 `(ICFG & 0x300) != 0x300`。`debug.h` 的 `traceSUPER_MODE()` 实际为空宏；bit10 的 ASSERT 位于注释中。这是 mode 相关线索，不是执行中的断言，也不规定极性、转换或栈别名。

可选 ROM 检查只验证 5 个固定 reset 指令锚点，以及 `0xc606..0xc636` 的线性 helper 中没有直接 ICFG 写指令。reset 路径在写入不同 SSP／SP 和 USP 后包含 RTI，未构造普通 IRQ 软件帧；静态字节不能给出执行后的 gate、mode、栈 bank 回写或首任务返回许可。

## 两种 gate8 规则为何仍无法区分

报告构造两个数学函数：每次返回都开 gate8；仅活动返回上下文开 gate8，否则保留旧值。二者都能给出这里选取的 gate8 端点：v4 的旧值已经为 1，app3 活动返回尾部为 1，app4 的后续显式清零把二者差异掩盖。对于“旧 gate8 为 0、没有已观察到的普通 IRQ 入口”的首任务场景，二者分别给出 1 和 0。

这只是有限端点上的非唯一性例子。两个函数都不是完整 CPU 模型，未证明满足所有测量，更未证明满足所有硅片行为。app3 的整个软件返回尾部也没有隔离 gate8 改变的原因。不能任选一个函数作为通用 RTI 实现。

## 对部分已知模型的审查要求

以下是依据上述约束提出的模型设计要求，并非已经认证的硬件规则：

- 分别记录 ICFG 的值、known mask、来源，以及真实入口／返回上下文知识；显式初始 fixture 不等于实测 reset 值。
- 只有实际同核 read／AND `~0x300`／write 表达式才能据其来源清除 gates8／9、保留其他位的已知程度；猜测的 literal word 是另一类操作。若支持 literal gate-write 投影，应单独显式开启，保留其 AC791N 来源和不完整 writable-mask 限制。
- CLI／STI、带寄存器形式与消费者只应消费所需位；与当前决策无关的未知位不应被补零。任意其他位写入、status-one-write 和保留位仍需独立证据。
- 已知关闭的 gate、禁用的 source 或不足的 priority 可以排除投递；条件都通过后仍必需的未知位应停止。不能从调用者写入的低字节或猜测 bit10 生成真实 IRQ-entry token。

首任务 RTI 仍缺立即返回后的 gate8、bit10／mode、SP／USP／SSP 回写，以及隐藏返回许可的创建／消费契约。需要区分这些行为的观测必须在 CLI 或另一次 ICFG 写入前采样，且入口历史与栈 bank 可区分；这里没有请求或执行新的硬件实验。部分 ICFG 模型可以独立推进，[首任务严格边界](dualcore-startup.md)不能因此取消。

## 本次离线验证

普通 Python 和 `python3 -O` 均完成正例；带／不带 ROM 的报告只有 ROM 检查字段不同，带 ROM 的生成结果与公开证据一致。独立内存变异逐一破坏 16 个原始输入、31 条选定记录，全部拒绝；错误 ROM 长度及摘要、4 种输入／自身覆盖目标和缺少 `--report` 也均拒绝。工具没有可被 `-O` 关闭的 `assert` 校验。此处只报告该静态工具的检查结果，不代表运行完整回归、CI 或硬件验证。
