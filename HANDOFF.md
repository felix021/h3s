# 交接：整机启动仿真推进中（IRQ127 调度闭环前置）

2026-11-11 恢复工作。阶段 A（重建可信基线）已完成，当前推进 codex 调研路线的后续阶段。停止时请更新本文件。

## 当前状态

- **基线已重建**：`921c2d5` 通过完整本机回归（真实 ROM + 新接入的 `test_architectural_models.py` / `test_task_returns.py`）。
- **修复内容**：`probe_hardware` / `probe_gpio_irq` 为 ROM delay helper（`st.w.sp` @ `0xc2ba`/`0xc2c6`）启用既有的 exact 全字存储路径，绕开上游对未写栈字节的合并读；`probe_pi32v2` 的 cpu 工厂应用 `architectural_k2`，消除测试框架副作用。
- **边界核对**：13 组外设场景与 MMU 边界（warm 73436 / cold 74474 @ `0xff0404`）、双核 147,263/147,268 步、partial-ICFG 147,268 步边界均与 `ab9f4e1` 一致；r2 五入口仍 BLOCKED。
- **报告差异**：执行器类报告 vs `ab9f4e1` 的差异均为元数据（k2 修正记录、适配器计数桶改名、开关字段），已在 AGENTS.md 说明；`evidence/` 的执行器类 JSON 尚未按新基线重新策划（待下一里程碑一起做）。

## codex (gpt-6-astra) 调研路线（已核实采纳）

阶段划分（每阶段退出条件见 codex 报告 `~/tmp/codex/202611111059-h3s-ufw-boot-sim/final.md`，已核实关键断言）：

| 阶段 | 内容 | 状态 |
| --- | --- | --- |
| A | 恢复可信基线（回归、入口审计、测试接入） | **完成** |
| B | 返回契约（bootstrap/首任务/普通 IRQ 返回分开描述；独立前端 + 负例 + 来源审查） | 进行中 |
| C | 最小调度闭环（真实 IRQ127 pending `0x8` → 向量 → handler → ACK → 调度 → 返回；之后 core tick IRQ3） | 前置调研中 |
| D | 应用启动（资源映射、外设、IDLE 唤醒、P11 接口、蓝牙音频初始化） | 未开始 |
| E | 三镜像 EQ 差分（b79/r2/修复候选；L0-L3 四层验收） | 未开始 |

关键裁决：保留现有严格执行器，不迁 QEMU；RTI 用互斥 opt-in 假设（方案 B），拒绝从 b79 反推 gate8 必开；P11 先建主核读写依赖清单再决定投入。

## 进行中的调研

- subagent 审计 1（已完成）：`probe_pi32v2` 独立入口确未应用 `architectural_k2` 修正，但 5 个 r2 REJECTED 结论不依赖 k2 栈语义（k2 调用次数为 0，拒绝发生在 decode 围栏）→ 已落地修复并验证。
- subagent 调研 2（进行中）：IRQ127 闭环的已知事实/缺口清单、向量槽位核对（4B 槽候选 `0x19fffc`，须核对实际注册索引）、pending 来源与 gate 链。

## 下一步（恢复时按序）

1. 等 IRQ127 调研结论，核实后实现受限入口契约（codex 建议 `probe_irq_entry.py` / `test_irq_entry.py`，未写向量、未知 SSP、关闭门、未知 pending、嵌套未支持均必须停下）。
2. IRQ127 之后补 core tick IRQ3（`core_tick_overflow_delivery_unresolved` 边界）。
3. 阶段 B 剩余：任务返回对照的独立前端（`probe_task_returns.py`）与来源审查。
4. 里程碑：真实 IRQ127 驱动调度、连续进入 app_core、tick 唤醒恢复任务——这是 codex 建议的下一个可交付里程碑。

## 资料和硬件边界（不变）

- 历史失败 r2（`c8dae523…`）禁止重刷；修复候选（`67ba97ef…`）未验证未刷。
- ROM 已作为公开 Release asset 发布（`QCY_H3S_BR28_ROM_000000-020000.bin`，SHA256 `7cae0a02…`），可用 `gh release download firmware --pattern 'QCY_H3S_BR28_ROM*'` 获取后跑 `--rom` 回归。
- RAM 观测候选仍 nondeployable；不进入任务或文档流程。
- 整片 Flash、完整 RAM/轨迹、私人路径不进公共仓库；本仓检查全部本机执行，不用 GitHub CI。
