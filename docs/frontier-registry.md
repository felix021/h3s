# frontier registry（前沿场景开关与停点的单一数据源）

`tools/frontier_registry.py`，2026-11-14 路 AF/sim-registry 引入。纯数据 + 最小访问函数，
不 import 任何工具模块。

## 为什么存在

前沿推进时，同一个事实此前要在四处人工同步，漏改已多次发生：

| 同步点 | 此前的形态 |
| --- | --- |
| `tools/probe_irq_entry.py` | 场景调用的大段开关字面值 + 前沿 require 断言（步数/停点 PC 字面值） |
| `tools/analyze_image_trace.py` | `BASE_SCENARIO_FLAGS`/`CONDITIONAL_EXTRA_FLAGS` 字典 + freshness 钉子（completed_steps/stop_pc）+ 自检 require |
| `tools/test_image_trace_diff.py` | freshness 期望值字面值（重复一份）+ AST 对 probe 调用点的钉法 |
| `HANDOFF.md` 前沿表 | 人工叙事（**保持人工，不进 registry**） |

现在前三处全部从 registry 派生：加一个开关或推进前沿，字面值只写一次。

## 数据结构

- `FEATURES`：每个 opt-in 开关一条记录（59 条），字段：
  - `switch`＝`probe_irq_entry.irq_case` 的场景参数名（开关名，即 feature id）；
  - `stack`＝`base`（crc_bankb 共享栈，19 个）/`conditional`（p11_heap 条件栈增量，35 个）/
    `probe_only`（只出现在 probe 自己的场景变体，5 个：探路 fixture、反事实掩码对照、键链直调）；
  - `requires`＝层叠依赖开关名（33 条依赖边，与 irq_case 内 require 守卫一致）；
  - `value_switch`/`value_attr`＝非布尔开关标记；条件栈里的三个数据 fixture
    （`heap_info_bytes`/`lrct_trim_result`/`fd31_adc_res_named_values`）只存 probe_irq_entry
    模块常量**名**，由调用方传命名空间解析——数值绝不复制进 registry；
  - `summary`＝一句话描述（证据全文在 probe_irq_entry 注释与 HANDOFF）。
- `FRONTIERS`：两个前沿契约单点——`crc_bankb`（289,849 @ 0x60679ba 读 0xf27f4b）与
  `p11_heap_experimental`（当前条件前沿，完整 stop 记录 + 步数 + freshness 检查字段子集）。

## 三处派生关系

- `probe_irq_entry.main()`：`crc_bankb_erased_fixture` 与 `p11_heap_experimental_config`
  两个共享栈场景的开关集＝`scenario_flags(...)`；`check()` 里两个前沿 require 的
  步数/停点字面值＝`frontier(...)`（断言叙事保持手写）。其余场景（legacy 八场景的
  嵌套前缀、probe 专用探路变体）保持手写，不进共享栈。
- `analyze_image_trace`：`BASE_SCENARIO_FLAGS`/`CONDITIONAL_EXTRA_FLAGS`＝registry 派生；
  两个 `freshness` 钉子与 `check_self*` 的前沿 require 字面值＝registry 派生。
- `test_image_trace_diff`：freshness 期望＝registry 读；行为断言（首分歧/分歧段/报告差异）
  保持人工——**测试不全生成**。

## 红线（勿破）

- registry 只管「开关/依赖/freshness 契约」，**不给寄存器值**：寄存器建模留在
  `br28_model`；fixture 数值按 probe_irq_entry 常量名引用（`value_attr`），不复制。
- 开关间的运行时校验（`irq_case` 内 `require` 层叠守卫）**留在 probe**；registry 的
  依赖表用于场景集闭包校验与文档，不代替运行时校验。
- 新东西默认关：registry 只是登记处，所有开关默认值不变（False/None）。

## 加新 opt-in 开关时只改哪几处

1. `probe_irq_entry.irq_case`：加参数（默认 False/None）+ wiring（保持注释与
   `require` 守卫）。
2. `frontier_registry.FEATURES`：加一条（`stack` 选层，`requires` 按层叠关系填；
   非布尔开关标 `value_switch`，条件栈值 fixture 填 `value_attr` 常量名）。
3. 若开关只进某个 probe 场景变体（不进两个共享栈）：`stack='probe_only'`，
   probe 场景调用点照旧手写传值。

analyze 的 flag 字典、freshness 钉子、test 期望自动跟随，无需再改。

## 推进前沿（新停点）时只改哪几处

1. `frontier_registry.FRONTIERS['p11_heap_experimental']`：按新停点重钉
   （completed_steps/stop_pc/完整 stop 记录/freshness_fields）。
2. `HANDOFF.md` 前沿表 + 「已实现」条目（人工叙事，保持人工）。

probe 的前沿 require、analyze 的 freshness 与 `check_self_conditional`、test 的期望
全部自动跟随；`analyze_image_trace` 场景注释里的前沿推进史叙事随路条目顺带更新。
条件前沿步数超过 `SCENARIOS['p11_heap_experimental']['default_budget']` 时另调
budget（该值留在 analyze，属运行配置）。

## 守卫（drift 怎么被拦住）

- `frontier_registry.validate()`（模块 import 时即执行 + 单测调用）：开关名唯一、
  依赖存在、共享栈依赖闭包（栈内开关的依赖也必须在栈内）、base 层无依赖、
  场景/前沿键对齐、value_attr 只出现在条件栈。
- `test_image_trace_diff.test_flag_sets_derive_from_the_frontier_registry`：
  registry 全量开关 ↔ `irq_case` 签名逐一对账（漏登记或登记已删参数都会失败）、
  条件栈非布尔开关恰为三个具名 fixture 且 `is` 同一模块常量、base 全布尔。
- 真实性由全量检查闭环：probe 前沿 require 与 analyze freshness 在
  `check_local.py --rom` 下用真仿真复现停点，单测不重复断言数值。
