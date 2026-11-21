# gosim 整圈搬移设计（M1 架构定稿 + 骨架）

2026-11-22。基线 worktree `9779c42`（main HEAD，gosim 批量已接进 explore）。本页是
「H3S 固件仿真执行器完整迁移到 golang」6 里程碑中 M1 的第二交付物：世界状态结构、
执行圈语义、边界交接协议、advance 链移植清单与诚实卡点。Go 侧骨架落在
`tools/gosim/world.go`（纯结构，恒 YIELD，不执行）。

最终目标（背景）：Go 持有完整世界状态（双核 CPU + 外设模型 + 严格内存 + 账本），
批内零 Python 回调；Python 只做场景构造 / fixture / 报告 / 逐字节对拍。提速是副产品，
先正确后提速。

## 0. 现状（读码确认）

- Go 侧 `tools/gosim/`：`core.go`（指令核）、`decode_table.go` + `decode_index.go`
  （预解码表 + M1-1 首字索引）、`models*.go`（移植的模型约 3000 行）、`segment.go`
  （RunSegment：stage-1 顺序 ALU + stage-2 分支，单核、具体整数、不可变取指窗口）、
  `mem.go`（玩具内存：读未写页返零）。
- Python 侧执行器：`tools/br28_model.py`（13763 行完整外设模型）、`tools/probe_boot.py`
  （explore 执行循环 + 严格内存 `Memory`）、`tools/br28_machine.py`（after_step / advance
  机器侧推进 + 双核 select + 交付投影）。
- 仍在 Python 的边界（红线）：访存、抽象值（UnknownWord / IcfgValue）、双核 select、
  IRQ 交付、after_step / advance / trace、严格内存语义（unmapped vs uninitialized 区分
  + 首个缺失字节）、观察钩子。
- 接进 explore 的引擎：`tools/gosim_batch_engine.py`（BatchEngine：预判段头命中预条件
  切 Go 跑段，边界交回 Python）。当前只替代「单核核内 decode+effect+apply」，其余照旧逐步。

## 1. 世界状态结构（谁持有什么）

Go 侧 `world.go` 已搭最小骨架（`World` / `Core` / `Periph` / `Ledger` / `LoopStep` /
`LoopResult`）。最终 Go 持有的完整世界状态：

| 域 | 内容 | M1 状态 |
| --- | --- | --- |
| 双核 CPU | R[16] / flags / sp(sp_known) / rets / pc / icfg(icfg_known) / steps / repeat / after_then / seq | 骨架已定义 `Core`，缺 sp_known/icfg_known 之外的特殊寄存器（ssp/usp/reti/irq_psr）、执行上下文位（bootstrap_context/active_context/opaque_source） |
| 严格内存 | region 列表 + ROM/app/readonly/handoff 镜像 + MMIO 路由 + 字节级 ram（None=未初始化）+ icfg_spills/unknown_origins | **未移植**。mem.go 是玩具（无 region、无 unmapped/uninitialized 区分、无 MMIO、读未写返零）。移植是 M2 硬前置 |
| 外设模型 | Timer0 / core_ticks / watchdog / ICU / Timer2 / ADC-DMA / SRC-SYNC / software-IRQ + 各投影开关 | **未移植**。`Periph` 接口占位，见 §4 清单 |
| 账本 | trace 行 / memory events / milestones / adapters / rom_steps / event_counts / completed_steps | `Ledger` 骨架已定义（Trace/Events/Milestones/CompletedSteps），缺 adapters/rom_steps/event_counts 细分 |

Python 保留（不回迁）：场景构造、fixture、报告组装、观察钩子（d1_window_observer /
gosim_observer）、抽象值、IRQ 交付投影（deliver_*，M4 前）。

**内存是 M2 的硬前置**：整圈搬移只有等 Go 内存与 Python `Memory` 逐字节同语义（含
`uninitialized_memory` 的 `first_missing` 字节、`unmapped_*`、MMIO `handles` 路由、ROM
只读写拒绝）后才能进行，否则任何一次访存都可能伪造零值（红线 1）。现状 mem.go 读未写
页返零，正是红线禁止的行为。

## 2. 执行圈语义（一次完整循环尝试）

`probe_boot.explore` 内层 `for _ in range(budget)` 的一次尝试（读码 3013–3143）：

```
1  select       machine.select()：双核选择（含 idle_wait 排除、defer_core1、kick/timer2 兜底）
2  fetch 守卫   stop_before_pc / ROM_RETURN 停点检查 + observer.begin 预判
3  decode       c.decode(c.pc) → insn,raw,size；debug.check_fetch；observer.note_insn
4  execute      idle→enter_idle_wait(CoreStall)；否则 c.step()（decode+effect+apply 核内）
5  after_step   machine.after_step(c, before_pc)：cfg523 fixture / boot anchors /
               first-task 假设 / note_sp_write / D1 观察器喂步 / core_icfg 刷新
6  advance      hardware.advance(c.icfg)：外设逐模型步进 + 定时器到期 → IrqDeliveryRequest / Blocked
7  trace        row 组装（pc/core/instruction_id/bytes/next_pc）+ trace.append(截 64)
```

异常通道（5 个）：CoreStall（停泊）、Blocked（诚实停，stop=details）、
IrqDeliveryRequest（dispatch_delivery 交付投影）、RuntimeError/ValueError/KeyError/
IndexError（upstream_simulator_failure）、观察器 window_stop（Blocked 归一）。

「能静态证明无副作用的简单配置」= 单核 + 具体整数 + 无 repeat/after_then + 取指落在
不可变窗口 + **advance/after_step 在本尝试可证明为空操作**。advance 空操作的可静态判定
本身依赖外设状态（哪些 timer 在计数、有无 pending），所以整圈第一刀的「execute」阶段
复用的是现有 segment 契约（stage-1/2 已逐字节验证），新增的是对「after_step/advance
空操作」的判定——这个判定在 M1 里还没有任何 Go 模型可依据，故 M1 整圈恒 YIELD。

## 3. 边界交接协议（两个出口）

```
YIELD_BEFORE_ATTEMPT  本尝试未开始即交回 Python：循环级预条件无法证明
                      （双核、非具体整数、observer 激活、repeat/after_then、
                       取指窗口不可变不成立、advance 非空操作无法证明）。
                      世界状态零改动，已提交前缀为空。
                      语义 = 现有 BatchEngine 的「预判不中，走慢路径」上提一层。

STOP                  Go 已执行一段前缀，中途撞上无法建模的 hazard
                      （访存 / 抽象值 / IRQ / 双核交接 / 观察钩子 / 复杂 advance），
                      返回已提交前缀的逐步观测 + hazard pc + 原因，Python 从 hazard 处
                      续跑。语义 = 现有 segment 的 BatchUnsafe/BatchFetch 等诚实停。
```

两出口与现有 `segment.go` 停码的关系：现有 BatchOK/Unsafe/NoInstr/NoModel/Fetch 是
「execute 阶段」的 STOP 出口；YIELD_BEFORE_ATTEMPT 是新增的循环级出口，对应
BatchEngine.run_segment 之前的预判（`_batch_safe` + concrete + single-core + immutable
+ 无 repeat/after_then）。整圈搬移把这些预判收敛进 Go 的 `World.RunLoop`，Python 侧
BatchEngine 退化成「把预判结果翻译成一次 RunLoop 调用」。

红线约束（不可破）：诚实停 / 抽象值 / 报告确定性三性质逐字节对拍。任何一步不确定就
YIELD/STOP 交回，不伪造值、不跳过 hazard。crc_bankb 的 report sha256 + trace 逐条签名
与 Python 参考逐字节一致（`gosim_measure.py --self-check-batch` 现成 harness）。

## 4. advance 链移植清单（br28_model.py 模型）

`hardware.advance(icfg)`（br28_model.py:12480）是外设逐模型步进编排器，M1 需要移植的
叶子模型与其规模（行号实测）：

| 模型 / 方法 | 位置 | 规模 | 说明 |
| --- | --- | --- | --- |
| `IRQController.eligible` | 10047 | 25 行 | IRQ 优先级 + IPMASK + ICFG nibble 资格判定，交付核心 |
| `Timer0.advance` | 9839 | ~40 行 | 分频计数 + pending 置位 |
| `Timer2Delay.advance` | 9987 | ~170 行 | LRC 1/80 分数累积，只置 pending 不抛交付 |
| `CoreTickStopped.advance` | 1114 | ~164 行 | core tick 计数/溢出/pending/在途 |
| `WatchdogProjection.advance` | 1517 | ~119 行 | WDT 计数（feed 侧效应在写模型，非 advance） |
| `IcuInitializationProjection.advance` | 871 | ~97 行 | ICU 命令倒计数步进 |
| `JLSrcSyncDelivery.advance` | 7536 | ~139 行 | SRC_SYNC 完成步进 → IRQ89 |
| `SoftwareIrqCommands` | 9549 | ~116 行 | 软件 IRQ pending 字（读/写 + pending） |
| `BR28Model.advance` 编排器 | 12480 | ~150 行 | 把上面各 advance + 6 个交付扫描（IRQ3/4/68/89/software）串起来，抛 IrqDeliveryRequest/Blocked |

合计约 1000 行叶子模型 + 150 行编排器。**关键简化**：Go 的 advance 只需检测
「pending 且 eligible」并抛 IrqReq 交回 Python 执行交付投影（deliver_* 在
br28_machine.py，各 60–120 行，M4 前留在 Python），不需要移植交付本身。这样红线
（IRQ 交付里的 CPU 态改写 / 假设）不进入 Go，直到有独立对拍证据。

**移植顺序（依赖驱动）**：M2 严格内存 → M3 Timer0 + IRQController（单核无 IRQ 交付，
先让「无 timer 在计数」路径能静态判定 advance 空操作）→ M4 core_ticks + watchdog +
ICU + Timer2 + software-IRQ（覆盖「有计数但不抛交付」）→ M5 SRC-SYNC/ADC-DMA 交付检测
→ M6 双核 select + 交付投影。

## 5. 严格内存移植（mem.go → 对齐 Python Memory）

Python `Memory`（probe_boot.py:389–528）语义逐条：region 列表（bootstrap_stack /
hypothesized_boot_argument_object / handoff regions）+ ROM/app/readonly/handoff 镜像 +
MMIO `handles` 路由 + PLL fixture + region 内字节级 ram（`None`=未初始化，
首缺失字节 `first_missing` 抛 `uninitialized_memory`）+ 无 region 抛
`unmapped_memory`/`unmapped_instruction_fetch`（fetch）+ ROM 写抛
`unmapped_or_readonly_write` + icfg_spills overlay + unknown_origins（抽象值溯源）。

Go mem.go 现状与差距：仅 page 稀疏映射 + 读未写返零。移植需补：region 表、ROM/app/
readonly/handoff 镜像、MMIO 路由钩子、未初始化字节哨兵、抽象值字节、写拒绝与事件。
预估 300–400 行 + 与 Python 的逐字节对拍测试（用 test_consistency 现有窗口法扩展）。

## 6. 里程碑切分（M2–M6，M1 之后）

- M2 严格内存移植 + 逐字节对拍（红线 1 的载体）。
- M3 Timer0 + IRQController 移植，Go 侧 advance 能对「无 timer 计数」静态判空，整圈
  execute 阶段（复用 segment 契约）+ 空 advance 首次真正在 Go 内闭合。
- M4 core_ticks / watchdog / ICU / Timer2 / software-IRQ 移植，覆盖「有计数不抛交付」。
- M5 SRC-SYNC / ADC-DMA 交付检测（抛 IrqReq 交回，交付仍在 Python）。
- M6 双核 select + 交付投影回迁，Python 只剩场景/报告/对拍。

## 7. 卡点（诚实报告）

1. **M1 整圈第一刀无法在 Go 内真正闭合**：advance/after_step 空操作判定依赖外设状态，
   而 M1 没有任何 Go 外设模型，任何「闭合整圈」的实现都必然伪造 advance/after_step
   语义，违反红线 1。故 M1 交付 = 骨架 + 本设计 + 清单，`World.RunLoop` 恒
   YIELD_BEFORE_ATTEMPT（诚实，不执行）。
2. **严格内存是硬前置**：mem.go 玩具语义与 Python `Memory` 不等价，读未写返零正是
   红线禁止的伪造。必须先过 M2。
3. **双核 select 高度纠缠**：br28_machine.select 含 idle_wait 排除、defer_core1、
   kick/timer2 兜底等 ~20 行特判，且与 idle_wait_cores 集合状态耦合；回迁须按探索
   顺序逐分支对拍，风险高，放 M6。
4. **after_step 是「钩子丛林」**：cfg523 fixture / boot anchors / first-task 假设 /
   note_sp_write / D1 观察器喂步，每个都是数据驱动的 PC 锚定特判；整圈闭合前须先有
   静态「本 PC 不触发任何钩子」的判定，或这些钩子继续留在 Python 每步回调（批内零
   回调的目标到 M6 才完整达成）。
5. **IRQ 交付投影不回迁（M1–M5）**：deliver_* 含 CPU 态改写与显式假设（kick/
   timer2/audio-dma/src-sync 的 wake_source_unverified 等），在独立对拍证据前留在
   Python 是最安全的边界。

## 8. 验收（与现有红线同口径）

- 字节一致：`gosim_measure.py --self-check-batch`（crc_bankb report sha256 + trace
  逐条签名与 Python 参考一致），以及 `test_batch_consistency.py` / `test_consistency.py`。
- 提速冷缓存实测：`go test -bench BenchmarkDecodeOneCold`（无 pc 缓存），M1-1 已交付
  索引前后对比（hit 637→171ns，miss 928→1.57ns）。
