# parity 面板（三镜像/双镜像轨迹差分证据，路 A1 起 2026-11-14）

本目录收「镜像间模型轨迹差分」类验收记录（进 git）。与 d3 面板（真机一致性）相区分：
本面板的观察面在模型内（analyze_image_trace 逐步签名+reconvergence+停点 parity），
按 d1-acceptance §3.4 的口径属 L2 系证据（同模型内回归+「本窗口未消费差异」的动态
证据，不是模型正确性证明）。

## 记录

| id | 结果 | 一句话 |
|---|---|---|
| E1-PAR-01 | PASS | EQ 分叉后世界事件剖面（A1 uncovered 第二项，L2-a 设计输入）：等 own-step 事件面高度守恒（字节级世界差仅 jiffies 0x124ca4=21/24 与 LRC 帧计数 0x129D8C=12/15 两格），交付层归相位彩票（差=pending 数/相位余数，|ΔN|≤⌈相位/周期⌉），等待域随步数线性——事件计数等价判据可用，须按等 own-step 检查点对齐；详 [docs/eq-divergence-profile.md](../../eq-divergence-profile.md) |
| A1-PAR-01 | FAIL | b79↔EQ 条件前沿 parity 重跑（BK 304,494→3.75M 前沿补齐）：+458 lag 重合保持到 b79 447,032 ⟷ EQ 447,490 后被 core-tick 首溢出交付相位差（22 步 vs 458 步错位）永久打断——判据①②命中（停点 parity 破坏+派生相位差被 IRQ 交付消费）、③未命中（差异区零取指）；详 [docs/eq-parity-rerun.md](../../eq-parity-rerun.md) |

## FAIL 的读法

A1-PAR-01 的 FAIL 是**预注册失败判据的命中**（d3-preparation §1.1），不是流程事故：
A1 的目的就是把 BK 欠的 +3.44M 步重跑补上、让前沿推进后的分歧结构暴露出来。它同时
是 B1（L2-a 三镜像差分）的设计输入：447k（首次 core-tick 溢出交付轴）之后「零控制流
分歧」判据在机制上不可用。

- 复核合法性：`python3 tools/acceptance_record.py --validate docs/acceptance-samples/parity`
- 全局汇总：[../acceptance-dashboard.md](../acceptance-dashboard.md)（六目录一页面板；记录改动后 `python3 tools/acceptance_dashboard.py` 再生成，`check_local --units` 校验一致性）
- 运行报告：忽略区 `analysis/local-checks/`（不进 git）
