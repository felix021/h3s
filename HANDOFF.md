# 交接：整机启动仿真推进中（调度闭环已贯通，app_core 已进入）

2026-11-11 恢复工作。阶段 A（基线）+ 阶段 C 主体（IRQ127 调度闭环 → app_core）已完成。停止时请更新本文件。

## 当前状态（按时间序，均已在 worktree `sim-baseline` 提交）

- **基线已重建**（`921c2d5`）：k2 副作用修正后完整回归通过；13 组外设场景与 MMU 边界、双核 147,263/147,268、partial-ICFG 边界与 `ab9f4e1` 一致。
- **返回对照固化**（`f4394dd`，`probe_task_returns.py`）：5 个反事实停止点全部复现（147,270/147,404/149,660×2/149,661）。
- **IRQ127 入口**（`f4394dd`）：双 opt-in（`software_irq_delivery_requests`+`software_irq_entry`）交付；向量槽 `0x19fffc`、RETI 装载、pending 清除均为显式假设；负例单测固定。
- **handler 头部 + 真调度器**（`734c60b`）：PSR 小帧（m=32）与 76B 上下文帧（m=41）锚定执行；`0xFFE200` 源使能位跟踪；真实 IPMASK 地址 `0xFFE168+cnum*0x800`（idle 循环 `0x12b56c` 与 handler `0x12ad5c` 一致）接入 IRQ 控制器；银行读指令 `mov.sp_usp`/`mov.usp_sp` 实现；入口银行假设（USP=中断任务栈、SSP=last_explicit_ssp）下 handler 存 76B 帧入真实 TCB 槽并调用真调度器 `0x12ADEC`，151,500 步停对核 IDLE。dualcore/partial-ICFG 边界不变。
- **调度闭环贯通**（`469e579`，最新）：opt-in IDLE 等待投影（核停发指令直到真实交付唤醒；全核等待→`all_cores_idle_waiting` 停止）；交付泛化到每核 IPI（IRQ 127-cnum，CPU1 槽 `0x19fff8`）；调度器自身的任务恢复 `pop.rmask` 装载任务帧 GPR（未初始化槽→UnknownWord，同首任务语义）；对称一次性 IRQ RTI 投影（PC=RETI、SP=USP，银行回写不认证）。**完整回路**：IPI 交付 → handler → 真调度器 → 任务切换 → RTI 进入真实 app_core 入口 `0x6012028` → 任务执行并 pend CPU1（IRQ125，优先级 6）→ **153,430 步停在 CPU1 侧 unmaskable 软件 IRQ（下一真实边界）**。
- **报告差异**：执行器类报告 vs `ab9f4e1` 的差异均为元数据；`evidence/` 执行器类 JSON 待下一里程碑重新策划。

## 当前停止点与下一层

153,430 步：`unmodeled_unmaskable_irq` core=1 irq=125 priority=6 source=software_irq。CPU0 的 app_core 任务执行中 SET 了 CPU1 的 IRQ125（`rmw.or.bitr` @ `0x12b666`），而优先级 6 在模型里是 unmaskable（`IPMASK 0..7` 中 6/7 不可屏蔽）——需要弄清 IRQ125 的真实语义（config nibble 优先级 6 的实际含义、BR28 是否真有不可屏蔽软件 IRQ）后扩展模型或显式假设。这是下一个真实边界，不是死路。

## 下一步（恢复时按序）

1. IRQ125/优先级 6 语义：读 `analyze_qcy_irq.py` 的 config nibble 布局与 SDK 参考，判定 6/7 是否真的不可屏蔽、CPU1 侧交付需要什么；可能只是模型的 `priority>=6` 假设过严（`br28_model.py` 的 `unmodeled_unmaskable_irq` 抛出条件）。
2. CPU1 侧 IRQ126/125 交付 + CPU1 handler 跑通（对称复用本层全部投影；CPU1 银行 fixture=`last_explicit_ssp 0x19fd94`）。
3. core tick IRQ3（`core_tick_overflow_delivery_unresolved`；handler `0x60661A4`、ACK `0xFFE0EC+bank` bit6、CPU0 PRD=1 高频溢出）——codex 阶段 C 后半的"tick 唤醒"。
4. 阶段 D：app_core 任务推进（0x6012028 之后的初始化链条：资源映射、外设、P11、蓝牙音频）。
5. 阶段 E：三镜像 EQ 差分（L0-L3）。

## 关键技术事实（本日新钉）

- IPMASK 物理地址 `0xFFE168+cnum*0x800`；模型常量 `IPMASK=0xffe1a8` 是 setup_arch 侧另一路径，两者都更新 `IRQController.ipmask`。
- `0xFFE200` = `EMU_CON`（debug control）兼软件 IRQ 源使能位（bits0-3）；handler 入口 `&=~8` 出口 `|=8`，模型分位跟踪、不影响 eligible。
- 调度 IPI 公式 `IRQ 127-cnum`；两核 handler 同为 `0x12AD2A`（固件注册）。
- app_core 帧 `0x19f7b0`：16B 特殊帧已知（PC `0x6012028`）、60B GPR 未初始化（真实语义，非仿真缺陷）。
- IDLE 等待投影：`CoreStall('core_idle_waiting')` + `select()` 跳过等待核；唤醒仅来自真实交付。

## 资料和硬件边界（不变）

- 历史失败 r2（`c8dae523…`）禁止重刷；修复候选（`67ba97ef…`）未验证未刷。
- ROM 公开 Release asset（`QCY_H3S_BR28_ROM_000000-020000.bin`）。
- RAM 观测候选仍 nondeployable；检查全部本机执行，不用 GitHub CI；发布需用户确认。

## codex (gpt-6-astra) 调研路线（已核实采纳）

阶段划分（每阶段退出条件见 codex 报告 `~/tmp/codex/202611111059-h3s-ufw-boot-sim/final.md`，关键断言已核实）：

| 阶段 | 内容 | 状态 |
| --- | --- | --- |
| A | 恢复可信基线（回归、入口审计、测试接入） | **完成** |
| B | 返回契约（bootstrap/首任务/普通 IRQ 返回分开描述；独立前端 + 负例 + 来源审查） | **前端完成**（5 场景固化+断言；来源审查见 `docs/icfg-status.md` 反例边界） |
| C | 最小调度闭环（IRQ127 入口 → handler → ACK → 调度 → 返回；之后 core tick IRQ3） | **第一层完成**（入口+2条handler指令）；第二层：push.srl 特殊帧、0xFFE200 源使能 RMW、IPMASK `0xFFE168` 映射、sp=usp/ssp 栈银行 |
| D | 应用启动（资源映射、外设、IDLE 唤醒、P11 接口、蓝牙音频初始化） | 未开始 |
| E | 三镜像 EQ 差分（b79/r2/修复候选；L0-L3 四层验收） | 未开始 |

关键裁决：保留现有严格执行器，不迁 QEMU；RTI 用互斥 opt-in 假设（方案 B），拒绝从 b79 反推 gate8 必开；P11 先建主核读写依赖清单再决定投入。

## IRQ127 subagent 调研要点（已核实关键断言）

- SET 写入点 `0x60644a6`（调度器按核 pend IPI 循环，前缀内唯一一次 RMW）。
- 向量表 `0x19fe00`+`irq*4`；IRQ127 槽 `0x19fffc`、handler `0x12AD2A`（注册调用 `0x6066100`：`r0=0x7f-cnum, r2=0x12AD2A, call 0x6065d86`）。CPU1 同路径写 IRQ126 槽 `0x19fff8`。
- handler ABI：入口小帧（cli → push r2-r0+psr → `[0xFFE200+bank]&=~8` → `[0x1261D0+cnum*4]=1` → IPMASK=7）→ 切换 76B RTOS 帧 → `sp=usp/ssp` 银行切换 → 调真调度器 `0x12ADEC` → 恢复 → 出口（`|=8`、IPMASK=0、sti、rti）。
- 缺口分类：G2 RETI 装载 / G5 pending 清除 / G9 无硬件压栈 = 显式假设（第一层已带标注）；G3 SP bank / G4 ICFG 入口效应 / G6 `0xFFE200` 模型冲突 / G8 通用 RTI = 第二层或必须 strict；G12 core tick IRQ3（`0xFFE0EC` ACK bit6、PRD=1 高频溢出）是闭环的下一个硬点。

## 下一步（恢复时按序）

1. 第二层：push.srl 特殊帧投影（含 PSR push 清低位 flags 语义，参照首任务帧）、`0xFFE200` bit3 源使能 RMW 投影（与 debug control 白名单的冲突要显式拆分）、IPMASK `0xFFE168` 地址映射、`sp=usp/ssp` 栈银行（SSP 用 `last_explicit_ssp` 显式 fixture 或保持 strict）。
2. 第二层目标：跑完 handler 头部到 `sp=usp`，进入真调度器 `0x12ADEC`，看到实际 TCB 选择——那之后按 codex 里程碑定义（IRQ127 驱动调度、连续进入 app_core、tick 唤醒恢复任务）。
3. core tick IRQ3：`core_tick_overflow_delivery_unresolved` → 入口契约复用本层机制。
4. 阶段 D 前置：`candidate_view` 按镜像映射完整资源（当前只映射 app+payload）。

## 资料和硬件边界（不变）

- 历史失败 r2（`c8dae523…`）禁止重刷；修复候选（`67ba97ef…`）未验证未刷。
- ROM 已作为公开 Release asset 发布（`QCY_H3S_BR28_ROM_000000-020000.bin`，SHA256 `7cae0a02…`），可用 `gh release download firmware --pattern 'QCY_H3S_BR28_ROM*'` 获取后跑 `--rom` 回归。
- RAM 观测候选仍 nondeployable；不进入任务或文档流程。
- 整片 Flash、完整 RAM/轨迹、私人路径不进公共仓库；本仓检查全部本机执行，不用 GitHub CI。
