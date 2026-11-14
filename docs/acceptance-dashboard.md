# 验收记录全局面板(六目录一页汇总,路 PANEL/sim-panel)

数据=`docs/acceptance-samples/` 全部记录的只读汇总(不改记录;每条链接直达 JSON)。
记录改动后须再生成:`python3 tools/acceptance_dashboard.py`;
`check_local --units` 会校验本页与记录一致(test_acceptance_dashboard.py)。
数据截至(记录 recorded_at 最大值)=2026-11-15T00:36:00 ｜ 共 70 条(六目录 68+上级散置 2)。

## 1. 总览

| 目录 | 内容 | PASS | 其中条件性 | FAIL | BLOCKED | NOT_RUN | 共计 |
|---|---|---|---|---|---|---|---|
| [d2a](acceptance-samples/d2a/README.md) | D2-a 按键输入响应:识别×动作覆盖(路 BB/sim-d2arecords) | 8 | 8 | 0 | 1 | 8 | 17 |
| [r234](acceptance-samples/r234/README.md) | R2 辅听/R3 独立 app/R4 命令接驳(路 CU/sim-r234records) | 12 | 12 | 0 | 3 | 0 | 15 |
| [d2b](acceptance-samples/d2b/README.md) | D2-b 音频输入闭环:配置实证×注入预填(路 BI/sim-d2brecords) | 7 | 7 | 0 | 0 | 8 | 15 |
| [cot](acceptance-samples/cot/README.md) | 候选镜像联动用例(路 BT/sim-coimg,d1-acceptance §6.5) | 0 | 0 | 0 | 0 | 11 | 11 |
| [d3](acceptance-samples/d3/README.md) | D3 真机一致性:案头就绪底稿(路 BS/sim-d3prep) | 1 | 0 | 0 | 0 | 7 | 8 |
| [parity](acceptance-samples/parity/README.md) | 镜像间轨迹差分(路 A1/EQDIV,L2 系证据) | 1 | 1 | 1 | 0 | 0 | 2 |
| (上级散置) | 上级目录示例:D2-a-REC-01(属 d2a)/R4-VM-01(属 r234),§8.1 | 2 | 2 | 0 | 0 | 0 | 2 |
| **合计** |  | **31** | **30** | **1** | **4** | **34** | **70** |

BLOCKED=必选用例因模型缺失未过,不是弱化 PASS(§6.4);条件性=证据等级 conditional(断言在显式投影/fixture 下成立,§8)。

## 2. 待授权/依赖项汇总

分组映射取 r234 README「全局待授权项清单」与 d3 README「等待项地图」先例(逐组注明出处);组内结果实时读自记录——同一记录可挂多组(r234 先例同此),PASS 记录挂账=其真机半边等授权,不代表已生效。

### 2.1 刷机授权(8 条挂账)

性质:离线候选族(patch 6c7247a5/close 5a5f2ef7/slotram 906184b2/r4-keyhook 8e9a47d0)真机生效的共同前提;AGENTS 红线「No new device action is authorized」 ｜ 映射出处:r234 README「全局待授权项清单」第 1 行

| 记录 | 目录 | 结果 | 证据等级 |
|---|---|---|---|
| [R2-HEA-02](acceptance-samples/r234/R2-HEA-02.json) | r234 | PASS | conditional(3) |
| [R2-HEA-03](acceptance-samples/r234/R2-HEA-03.json) | r234 | PASS | conditional(3) |
| [R2-HEA-04](acceptance-samples/r234/R2-HEA-04.json) | r234 | PASS | conditional(2) |
| [R2-HEA-05](acceptance-samples/r234/R2-HEA-05.json) | r234 | PASS | conditional(2) |
| [R2-HEA-06](acceptance-samples/r234/R2-HEA-06.json) | r234 | BLOCKED | none |
| [R4-VM-02](acceptance-samples/r234/R4-VM-02.json) | r234 | PASS | conditional(3) |
| [R4-VM-03](acceptance-samples/r234/R4-VM-03.json) | r234 | PASS | conditional(2) |
| [R4-VM-05](acceptance-samples/r234/R4-VM-05.json) | r234 | BLOCKED | none |

### 2.2 真机 BLE 联调授权(9 条挂账)

性质:R3 全链(壳→transport→GATT)与 R4 App 侧端到端;android-query 真机适配清单第 9/10 条 ｜ 映射出处:r234 README 第 2 行+d3 README「等待项地图」BLE 联调行

| 记录 | 目录 | 结果 | 证据等级 |
|---|---|---|---|
| [R2-HEA-05](acceptance-samples/r234/R2-HEA-05.json) | r234 | PASS | conditional(2) |
| [R3-APP-01](acceptance-samples/r234/R3-APP-01.json) | r234 | PASS | conditional(4) |
| [R3-APP-02](acceptance-samples/r234/R3-APP-02.json) | r234 | PASS | conditional(2) |
| [R3-APP-03](acceptance-samples/r234/R3-APP-03.json) | r234 | PASS | conditional(2) |
| [R3-APP-04](acceptance-samples/r234/R3-APP-04.json) | r234 | PASS | conditional(2) |
| [R3-APP-05](acceptance-samples/r234/R3-APP-05.json) | r234 | BLOCKED | none |
| [R4-VM-04](acceptance-samples/r234/R4-VM-04.json) | r234 | PASS | conditional(2) |
| [R4-VM-05](acceptance-samples/r234/R4-VM-05.json) | r234 | BLOCKED | none |
| [D3-EVT-01](acceptance-samples/d3/D3-EVT-01.json) | d3 | NOT_RUN | none |

### 2.3 只读观测会话授权(FD07/FD05)(4 条挂账)

性质:D3 SFR/PRB 四条共同前提;工具就绪=read_rom.py --session-pack 会话打包 dry-run(路 SESS) ｜ 映射出处:d3 README「等待项地图」第 1 行

| 记录 | 目录 | 结果 | 证据等级 |
|---|---|---|---|
| [D3-PRB-01](acceptance-samples/d3/D3-PRB-01.json) | d3 | NOT_RUN | none |
| [D3-SFR-01](acceptance-samples/d3/D3-SFR-01.json) | d3 | NOT_RUN | none |
| [D3-SFR-02](acceptance-samples/d3/D3-SFR-02.json) | d3 | NOT_RUN | none |
| [D3-SFR-03](acceptance-samples/d3/D3-SFR-03.json) | d3 | NOT_RUN | none |

### 2.4 前沿推进(非授权类,模型件缺失)(20 条挂账)

性质:主仿真前沿/D1 事件循环/RCSP 相位与注入执行载体——模型件缺口,不是授权缺口 ｜ 映射出处:r234 README 第 3 行+d2a README(ACT-10 归 D1 事件循环)+d2b README「注入面共同依赖地图」前两行+cot README「其余按注入层级 L0-L3 挂前沿/D1/RCSP」

| 记录 | 目录 | 结果 | 证据等级 |
|---|---|---|---|
| [R2-HEA-06](acceptance-samples/r234/R2-HEA-06.json) | r234 | BLOCKED | none |
| [R4-VM-05](acceptance-samples/r234/R4-VM-05.json) | r234 | BLOCKED | none |
| [D2-a-ACT-10](acceptance-samples/d2a/D2-a-ACT-10.json) | d2a | BLOCKED | none |
| [D2-b-INJ-01](acceptance-samples/d2b/D2-b-INJ-01.json) | d2b | NOT_RUN | none |
| [D2-b-INJ-02](acceptance-samples/d2b/D2-b-INJ-02.json) | d2b | NOT_RUN | none |
| [D2-b-INJ-03](acceptance-samples/d2b/D2-b-INJ-03.json) | d2b | NOT_RUN | none |
| [D2-b-INJ-04](acceptance-samples/d2b/D2-b-INJ-04.json) | d2b | NOT_RUN | none |
| [D2-b-INJ-05](acceptance-samples/d2b/D2-b-INJ-05.json) | d2b | NOT_RUN | none |
| [D2-b-INJ-06](acceptance-samples/d2b/D2-b-INJ-06.json) | d2b | NOT_RUN | none |
| [D2-b-SW-01](acceptance-samples/d2b/D2-b-SW-01.json) | d2b | NOT_RUN | none |
| [D2-b-SW-02](acceptance-samples/d2b/D2-b-SW-02.json) | d2b | NOT_RUN | none |
| [R2-COT-02](acceptance-samples/cot/R2-COT-02.json) | cot | NOT_RUN | none |
| [R2-COT-03](acceptance-samples/cot/R2-COT-03.json) | cot | NOT_RUN | none |
| [R2-COT-04](acceptance-samples/cot/R2-COT-04.json) | cot | NOT_RUN | none |
| [R2-COT-05](acceptance-samples/cot/R2-COT-05.json) | cot | NOT_RUN | none |
| [R2-COT-06](acceptance-samples/cot/R2-COT-06.json) | cot | NOT_RUN | none |
| [R4-COT-02](acceptance-samples/cot/R4-COT-02.json) | cot | NOT_RUN | none |
| [R4-COT-03](acceptance-samples/cot/R4-COT-03.json) | cot | NOT_RUN | none |
| [R4-COT-04](acceptance-samples/cot/R4-COT-04.json) | cot | NOT_RUN | none |
| [R4-COT-05](acceptance-samples/cot/R4-COT-05.json) | cot | NOT_RUN | none |

### 2.5 真机观测手段(手段本身现不存在)(2 条挂账)

性质:事件日志面/音频校准手段——授权也解不了,须先造观测手段 ｜ 映射出处:d3 README「等待项地图」第 3/4 行

| 记录 | 目录 | 结果 | 证据等级 |
|---|---|---|---|
| [D3-TIM-01](acceptance-samples/d3/D3-TIM-01.json) | d3 | NOT_RUN | none |
| [D3-AUD-01](acceptance-samples/d3/D3-AUD-01.json) | d3 | NOT_RUN | none |

未入组等待项 10 条(依赖见各记录「未覆盖项」): [D2-a-ACT-01](acceptance-samples/d2a/D2-a-ACT-01.json), [D2-a-ACT-02](acceptance-samples/d2a/D2-a-ACT-02.json), [D2-a-ACT-03](acceptance-samples/d2a/D2-a-ACT-03.json), [D2-a-ACT-04](acceptance-samples/d2a/D2-a-ACT-04.json), [D2-a-ACT-05](acceptance-samples/d2a/D2-a-ACT-05.json), [D2-a-ACT-06](acceptance-samples/d2a/D2-a-ACT-06.json), [D2-a-ACT-08](acceptance-samples/d2a/D2-a-ACT-08.json), [D2-a-ACT-09](acceptance-samples/d2a/D2-a-ACT-09.json), [R2-COT-01](acceptance-samples/cot/R2-COT-01.json), [R4-COT-01](acceptance-samples/cot/R4-COT-01.json).

## 3. 关键 FAIL/BLOCKED(归因取自记录本身)

### FAIL(1 条)

| 记录 | 目录 | 归因(未覆盖项首条,截断) |
|---|---|---|
| [A1-PAR-01](acceptance-samples/parity/A1-PAR-01.json) | parity | 22 步首交付延迟的指令级仲裁轨迹（b79 447,490..447,512 的 IPMASK/重检路径逐指令展开）——需工具增报告能力或下一路接手 |

### BLOCKED(4 条,§6.4「没过」而非弱化 PASS)

| 记录 | 目录 | 归因(blocked_missing,截断) |
|---|---|---|
| [D2-a-ACT-10](acceptance-samples/d2a/D2-a-ACT-10.json) | d2a | post→S19 分发→handler 0x060252c6 的生产路径未接线：app_core 消息循环 get-event 消费不在停点世界可达段，直调分发函数需构造队列消费者任务上下文（路 S … |
| [R2-HEA-06](acceptance-samples/r234/R2-HEA-06.json) | r234 | 主仿真前沿未推进到 0x0600db3c（ANC 键循环 dispatch）相位——键循环两链/ctx getter 0x0600c2aa/apply 0x0600dae4/两条尾声的世界不可达，桩+… |
| [R3-APP-05](acceptance-samples/r234/R3-APP-05.json) | r234 | 真机 BLE 联调授权缺失——需用户当场显式授权的设备连接会话（红线：不连真机、不刷机），tools/android-query/README.md 真机适配清单第 9/10 条无法执行 |
| [R4-VM-05](acceptance-samples/r234/R4-VM-05.json) | r234 | 主仿真前沿未推进到 RCSP/任务派发相位——BLE 协议栈与命名任务 0x060A1D11 的命令投递世界不可达，分发器 0x0600f85e 对载荷（0x060E31E0）的实际调用没有执行载体（… |

### NOT_RUN(34 条,按目录聚合;等待说明=面板 README 指针)

| 目录 | NOT_RUN | 其中入组挂账 | 组外(依赖见记录「未覆盖项」) |
|---|---|---|---|
| d2a | 8 | 0 | 8 | 等执行载体(§6.1 等价类预填);ACT-10 BLOCKED 归 D1 事件循环——d2a README |
| d2b | 8 | 8 | 0 | 等 BF 前沿释放+AW 蓝图实施——d2b README「注入面共同依赖地图」 |
| cot | 11 | 9 | 2 | 按 L0-L3 前置链(COT-01 两条不依赖 D1 可立即跑)——cot README |
| d3 | 7 | 7 | 0 | 等授权或观测手段——d3 README「等待项地图」 |

## 4. 记录索引(逐条链接)

| 用例 id | 目录 | 结果 | 证据等级 | 记录时间 |
|---|---|---|---|---|
| [D2-a-ACT-01](acceptance-samples/d2a/D2-a-ACT-01.json) | d2a | NOT_RUN | none | 2026-11-14T09:30:00 |
| [D2-a-ACT-02](acceptance-samples/d2a/D2-a-ACT-02.json) | d2a | NOT_RUN | none | 2026-11-14T09:30:00 |
| [D2-a-ACT-03](acceptance-samples/d2a/D2-a-ACT-03.json) | d2a | NOT_RUN | none | 2026-11-14T09:30:00 |
| [D2-a-ACT-04](acceptance-samples/d2a/D2-a-ACT-04.json) | d2a | NOT_RUN | none | 2026-11-14T09:30:00 |
| [D2-a-ACT-05](acceptance-samples/d2a/D2-a-ACT-05.json) | d2a | NOT_RUN | none | 2026-11-14T09:30:00 |
| [D2-a-ACT-06](acceptance-samples/d2a/D2-a-ACT-06.json) | d2a | NOT_RUN | none | 2026-11-14T09:30:00 |
| [D2-a-ACT-07](acceptance-samples/d2a/D2-a-ACT-07.json) | d2a | PASS | conditional(3) | 2026-11-14T09:30:00 |
| [D2-a-ACT-08](acceptance-samples/d2a/D2-a-ACT-08.json) | d2a | NOT_RUN | none | 2026-11-14T09:30:00 |
| [D2-a-ACT-09](acceptance-samples/d2a/D2-a-ACT-09.json) | d2a | NOT_RUN | none | 2026-11-14T09:30:00 |
| [D2-a-ACT-10](acceptance-samples/d2a/D2-a-ACT-10.json) | d2a | BLOCKED | none | 2026-11-14T09:30:00 |
| [D2-a-REC-02](acceptance-samples/d2a/D2-a-REC-02.json) | d2a | PASS | conditional(2) | 2026-11-14T09:30:00 |
| [D2-a-REC-03](acceptance-samples/d2a/D2-a-REC-03.json) | d2a | PASS | conditional(3) | 2026-11-14T09:30:00 |
| [D2-a-REC-04](acceptance-samples/d2a/D2-a-REC-04.json) | d2a | PASS | conditional(2) | 2026-11-14T09:30:00 |
| [D2-a-REC-05](acceptance-samples/d2a/D2-a-REC-05.json) | d2a | PASS | conditional(2) | 2026-11-14T09:30:00 |
| [D2-a-REC-06](acceptance-samples/d2a/D2-a-REC-06.json) | d2a | PASS | conditional(2) | 2026-11-14T09:30:00 |
| [D2-a-REC-07](acceptance-samples/d2a/D2-a-REC-07.json) | d2a | PASS | conditional(2) | 2026-11-14T09:30:00 |
| [D2-a-REC-08](acceptance-samples/d2a/D2-a-REC-08.json) | d2a | PASS | conditional(2) | 2026-11-14T09:30:00 |
| [R2-HEA-01](acceptance-samples/r234/R2-HEA-01.json) | r234 | PASS | conditional(3) | 2026-11-14T09:55:00 |
| [R2-HEA-02](acceptance-samples/r234/R2-HEA-02.json) | r234 | PASS | conditional(3) | 2026-11-14T09:55:00 |
| [R2-HEA-03](acceptance-samples/r234/R2-HEA-03.json) | r234 | PASS | conditional(3) | 2026-11-14T09:55:00 |
| [R2-HEA-04](acceptance-samples/r234/R2-HEA-04.json) | r234 | PASS | conditional(2) | 2026-11-14T09:55:00 |
| [R2-HEA-05](acceptance-samples/r234/R2-HEA-05.json) | r234 | PASS | conditional(2) | 2026-11-14T09:55:00 |
| [R2-HEA-06](acceptance-samples/r234/R2-HEA-06.json) | r234 | BLOCKED | none | 2026-11-14T09:55:00 |
| [R3-APP-01](acceptance-samples/r234/R3-APP-01.json) | r234 | PASS | conditional(4) | 2026-11-14T09:55:00 |
| [R3-APP-02](acceptance-samples/r234/R3-APP-02.json) | r234 | PASS | conditional(2) | 2026-11-14T09:55:00 |
| [R3-APP-03](acceptance-samples/r234/R3-APP-03.json) | r234 | PASS | conditional(2) | 2026-11-14T09:55:00 |
| [R3-APP-04](acceptance-samples/r234/R3-APP-04.json) | r234 | PASS | conditional(2) | 2026-11-14T09:55:00 |
| [R3-APP-05](acceptance-samples/r234/R3-APP-05.json) | r234 | BLOCKED | none | 2026-11-14T09:55:00 |
| [R4-VM-02](acceptance-samples/r234/R4-VM-02.json) | r234 | PASS | conditional(3) | 2026-11-14T09:55:00 |
| [R4-VM-03](acceptance-samples/r234/R4-VM-03.json) | r234 | PASS | conditional(2) | 2026-11-14T09:55:00 |
| [R4-VM-04](acceptance-samples/r234/R4-VM-04.json) | r234 | PASS | conditional(2) | 2026-11-14T09:55:00 |
| [R4-VM-05](acceptance-samples/r234/R4-VM-05.json) | r234 | BLOCKED | none | 2026-11-14T09:55:00 |
| [D2-b-CFG-01](acceptance-samples/d2b/D2-b-CFG-01.json) | d2b | PASS | conditional(2) | 2026-11-14T10:40:00 |
| [D2-b-CFG-02](acceptance-samples/d2b/D2-b-CFG-02.json) | d2b | PASS | conditional(2) | 2026-11-14T10:40:00 |
| [D2-b-CFG-03](acceptance-samples/d2b/D2-b-CFG-03.json) | d2b | PASS | conditional(2) | 2026-11-14T10:40:00 |
| [D2-b-CFG-04](acceptance-samples/d2b/D2-b-CFG-04.json) | d2b | PASS | conditional(1) | 2026-11-14T10:40:00 |
| [D2-b-CFG-05](acceptance-samples/d2b/D2-b-CFG-05.json) | d2b | PASS | conditional(2) | 2026-11-14T10:40:00 |
| [D2-b-CFG-06](acceptance-samples/d2b/D2-b-CFG-06.json) | d2b | PASS | conditional(3) | 2026-11-14T10:40:00 |
| [D2-b-CFG-07](acceptance-samples/d2b/D2-b-CFG-07.json) | d2b | PASS | conditional(2) | 2026-11-14T10:40:00 |
| [D2-b-INJ-01](acceptance-samples/d2b/D2-b-INJ-01.json) | d2b | NOT_RUN | none | 2026-11-14T10:40:00 |
| [D2-b-INJ-02](acceptance-samples/d2b/D2-b-INJ-02.json) | d2b | NOT_RUN | none | 2026-11-14T10:40:00 |
| [D2-b-INJ-03](acceptance-samples/d2b/D2-b-INJ-03.json) | d2b | NOT_RUN | none | 2026-11-14T10:40:00 |
| [D2-b-INJ-04](acceptance-samples/d2b/D2-b-INJ-04.json) | d2b | NOT_RUN | none | 2026-11-14T10:40:00 |
| [D2-b-INJ-05](acceptance-samples/d2b/D2-b-INJ-05.json) | d2b | NOT_RUN | none | 2026-11-14T10:40:00 |
| [D2-b-INJ-06](acceptance-samples/d2b/D2-b-INJ-06.json) | d2b | NOT_RUN | none | 2026-11-14T10:40:00 |
| [D2-b-SW-01](acceptance-samples/d2b/D2-b-SW-01.json) | d2b | NOT_RUN | none | 2026-11-14T10:40:00 |
| [D2-b-SW-02](acceptance-samples/d2b/D2-b-SW-02.json) | d2b | NOT_RUN | none | 2026-11-14T10:40:00 |
| [R2-COT-01](acceptance-samples/cot/R2-COT-01.json) | cot | NOT_RUN | none | 2026-11-14T21:40:00 |
| [R2-COT-02](acceptance-samples/cot/R2-COT-02.json) | cot | NOT_RUN | none | 2026-11-14T21:40:00 |
| [R2-COT-03](acceptance-samples/cot/R2-COT-03.json) | cot | NOT_RUN | none | 2026-11-14T21:40:00 |
| [R2-COT-04](acceptance-samples/cot/R2-COT-04.json) | cot | NOT_RUN | none | 2026-11-14T21:40:00 |
| [R2-COT-05](acceptance-samples/cot/R2-COT-05.json) | cot | NOT_RUN | none | 2026-11-14T21:40:00 |
| [R2-COT-06](acceptance-samples/cot/R2-COT-06.json) | cot | NOT_RUN | none | 2026-11-14T21:40:00 |
| [R4-COT-01](acceptance-samples/cot/R4-COT-01.json) | cot | NOT_RUN | none | 2026-11-14T21:40:00 |
| [R4-COT-02](acceptance-samples/cot/R4-COT-02.json) | cot | NOT_RUN | none | 2026-11-14T21:40:00 |
| [R4-COT-03](acceptance-samples/cot/R4-COT-03.json) | cot | NOT_RUN | none | 2026-11-14T21:40:00 |
| [R4-COT-04](acceptance-samples/cot/R4-COT-04.json) | cot | NOT_RUN | none | 2026-11-14T21:40:00 |
| [R4-COT-05](acceptance-samples/cot/R4-COT-05.json) | cot | NOT_RUN | none | 2026-11-14T21:40:00 |
| [D3-AUD-01](acceptance-samples/d3/D3-AUD-01.json) | d3 | NOT_RUN | none | 2026-11-14T21:18:45 |
| [D3-EVT-01](acceptance-samples/d3/D3-EVT-01.json) | d3 | NOT_RUN | none | 2026-11-14T21:18:45 |
| [D3-PRB-01](acceptance-samples/d3/D3-PRB-01.json) | d3 | NOT_RUN | none | 2026-11-14T21:18:45 |
| [D3-PRB-02](acceptance-samples/d3/D3-PRB-02.json) | d3 | PASS | none | 2026-11-14T23:27:10 |
| [D3-SFR-01](acceptance-samples/d3/D3-SFR-01.json) | d3 | NOT_RUN | none | 2026-11-14T21:18:45 |
| [D3-SFR-02](acceptance-samples/d3/D3-SFR-02.json) | d3 | NOT_RUN | none | 2026-11-14T21:18:45 |
| [D3-SFR-03](acceptance-samples/d3/D3-SFR-03.json) | d3 | NOT_RUN | none | 2026-11-14T23:27:38 |
| [D3-TIM-01](acceptance-samples/d3/D3-TIM-01.json) | d3 | NOT_RUN | none | 2026-11-14T21:18:45 |
| [A1-PAR-01](acceptance-samples/parity/A1-PAR-01.json) | parity | FAIL | conditional(3) | 2026-11-14T22:23:21 |
| [E1-PAR-01](acceptance-samples/parity/E1-PAR-01.json) | parity | PASS | conditional(3) | 2026-11-15T00:36:00 |
| [D2-a-REC-01](acceptance-samples/D2-a-REC-01.json) | (上级散置) | PASS | conditional(2) | 2026-11-14T08:30:00 |
| [R4-VM-01](acceptance-samples/R4-VM-01.json) | (上级散置) | PASS | conditional(2) | 2026-11-14T08:30:00 |

- 记录格式与校验规则:docs/d1-acceptance.md §8/§8.1;各目录面板(含依赖地图):[d2a](acceptance-samples/d2a/README.md) [r234](acceptance-samples/r234/README.md) [d2b](acceptance-samples/d2b/README.md) [cot](acceptance-samples/cot/README.md) [d3](acceptance-samples/d3/README.md) [parity](acceptance-samples/parity/README.md)。
- 本页由 tools/acceptance_dashboard.py 生成(只读汇总,不手改;数字全部来自记录文件)。
