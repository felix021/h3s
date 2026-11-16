# 验收证据索引（simulation 路线级，路 EVIDENCE/sim-evidence）

目的=codex 对账审查 P1 收口：**验收证据随提交交付**。M1/AVD/M3/D2/JLEQ 各路的原始
证据（JSON 记录、server log、截图）散落在各路 worktree 的 `analysis/`（gitignored，
刻意——原始记录留本地，AGENTS「Device actions and publication」红线）与临时目录。
本索引+`evidence/sim-routes/` 脱敏摘要+`docs/acceptance-samples/screenshots/` 精选
截图=入库形态：**摘要级证据（verdict+关键数值+复现命令+本地原始路径）**，不是原始数据。

与既有档案的关系：`docs/acceptance-samples/`（§8 九栏验收记录，d1-acceptance §8 体系，
`tools/acceptance_record.py --validate` 机检）覆盖 D1/D2/R2-R4 用例级验收；本索引覆盖
**路线级联调/世界级判定**证据（M1 直调与 AVD 联调、M3 刷机全环、D2 场景、JLEQ park），
verdict 词汇沿用各路执行器四态（PASS/FAIL/BLOCKED/PARTIAL，test_sim_m1_verdicts.py
退出码 0/3/4/5）。两套互补，不互相改写。

## 索引

| 路线 | 日期 | verdict | 关键数值 | 摘要（入库） | 本地原始记录（gitignored） |
|---|---|---|---|---|---|
| JLEQ park 判定 | 2026-11-17 | park 稳态 **PASS**；新前沿钉点 | 前沿 29,601,125 步 @ 0x606955c（读 0xfd1104 GPCNT NUM）；喂狗 11 事务全闭合；事件钟 14,953,935 虚拟步；CPU0 parked 1791(Timer0)+57(core-tick)+4(sw)/CPU1 16(sw)；parity official↔b79 零 delta | [evidence/sim-routes/jleq-park.json](../evidence/sim-routes/jleq-park.json) | `~/worktrees/h3s/sim-jleq/analysis/local-checks/`（2026-11-17 全套最新 probe 报告=权威副本） |
| M1 三级验收（直调） | 2026-11-17 | 总 **BLOCKED**（M1-a 5/5 PASS；M1-b PARTIAL；M1-c BLOCKED） | 世界=park 快照 29,601,125；EQ B1 空指针墙 0x60642ec 写 0x20；C0 撞前沿墙本身；C7 复役三连 PASS；P2 动态门全落值、读环 40.7ms | [evidence/sim-routes/m1-verdicts.json](../evidence/sim-routes/m1-verdicts.json) | `~/worktrees/h3s/sim-simb1f/analysis/local-checks/sim-m1-verdicts.json` + `sim-m1-p2-dynamic.json` |
| M1 AVD E2E | 2026-11-17 | 5 场景全 **PASS** | 读环三命令真固件回执直显 UI；ANC 写→读回 1703010000；EQ 66B 写→镜像 inspect 9/9 段逐字段相等；world_dead 1s 重建+重连复役 | [evidence/sim-routes/m1-avd-e2e.json](../evidence/sim-routes/m1-avd-e2e.json) | `~/worktrees/h3s/sim-simb1e/analysis/local-checks/`（JSON+server log+截图 7 张） |
| M3 AVD E2E 全环 | 2026-11-17 | **PASS**（退出码 0） | 三扇区计划 0x0/0x1000/0x1f8000；全片复读 SHA==S_target（05ec4242…）；fb01=3/fb04=24/journal_len=28/rejected=0；磁盘资产全程==S0 | [evidence/sim-routes/m3-avd-e2e.json](../evidence/sim-routes/m3-avd-e2e.json) | `~/tmp/simb3a/`（e2e-flow.log/plan.json/fixture report/截图） |
| D2 音频注入 b1/b2 | 2026-11-17 | b1 **PASS** 6/6；b2 **PASS** 7/7；门关负例 **PASS** 8/8 | 开链 6,407,583 步（handler 0x6033d5a 挂链）；交付窗 131 步分发器十站全覆盖；样本回读 1024B 逐字节相等；前沿零位移 | [evidence/sim-routes/d2-audio-scene.json](../evidence/sim-routes/d2-audio-scene.json) | `~/worktrees/h3s/sim-d2ai/analysis/local-checks/d2ai/scene.json` |

截图档案：[acceptance-samples/screenshots/](acceptance-samples/screenshots/)（M1 AVD 8 张
+M3 AVD 1 张，逐张隐私核验说明与 **12 号截图标注勘误**见其 README）。

## 主仓 analysis/ 报告代际核实（codex「主仓旧报告」项，2026-11-17 核实）

- `analysis/` 整目录被 `.gitignore`（第 5 行 `/analysis/`）排除——**仓库里不存在旧报告**，
  「主仓报告误导」只可能发生在本地工作树层面。
- 主仓 `~/code/headsets/h3s/analysis/local-checks/` 现存一套 2026-11-14 22:2x-22:54 的
  probe 报告（JLEQ 及其后全部合并**之前**的代际）；无任何 M1/M3/verdict 类记录
  （grep 无 verdict/fail 字段文件）。
- **权威最新副本**=`~/worktrees/h3s/sim-jleq/analysis/local-checks/`（2026-11-17
  01:42-02:00，JLEQ 路终态全套；JLEQ 已并 main 5e65791，报告口径与合并码一致）。
- 处置：**未覆写主仓 analysis/ 文件**——主工作树有在途路线正在使用（functions.json
  2026-11-17 06:26 仍在更新），覆写有干扰在途证据比对的风险，而 gitignored 目录刷新
  对仓库零收益。以本索引指路代替搬文件：需要最新报告时读 sim-jleq worktree 副本，
  或在主仓重跑 `python3 tools/probe_irq_entry.py --rom <ROM>` 再生成。

## 入库 / 留本地原则（本次执行口径）

- **入库**：脱敏摘要 JSON（verdict/关键数值/复现命令/raw_local 指路）、精选截图
  （逐张人工核验：无真机数据、无私有路径/主机名/IP——10.0.2.2 为 AVD 标准回环别名）、
  勘误说明。
- **留本地（gitignored）**：raw server log、world-server.log、全量 verdict/watch/world_diff
  明细、fixture 二进制（flash-2mib.bin/target.bin）、ROM 窗口文件、含设备序列号/进程号
  的过程记录。摘要里的本地路径用 `~/` 相对写法（与 HANDOFF 既有惯例一致）。
- 入库前逐文件 grep 私有模式（felix021 主机段/内网 IP/设备序列号/PKT 号）：摘要与截图
  全部通过；原始 sim-b1e-avd-e2e.json 含一台他人真机序列号与本地 ROM 路径，故只入库
  脱敏摘要、原文留本地。
