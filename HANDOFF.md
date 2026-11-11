# 交接：整机启动仿真推进中（IRQ127 已入 handler）

2026-11-11 恢复工作。阶段 A（重建可信基线）与阶段 C 第一层（IRQ127 入口交付）已完成。停止时请更新本文件。

## 当前状态

- **基线已重建**：`921c2d5` 通过完整本机回归（真实 ROM + 新接入的 `test_architectural_models.py` / `test_task_returns.py`）。
- **修复内容**：`probe_hardware` / `probe_gpio_irq` 为 ROM delay helper（`st.w.sp` @ `0xc2ba`/`0xc2c6`）启用既有的 exact 全字存储路径，绕开上游对未写栈字节的合并读；`probe_pi32v2` 的 cpu 工厂应用 `architectural_k2`，消除测试框架副作用。
- **边界核对**：13 组外设场景与 MMU 边界（warm 73436 / cold 74474 @ `0xff0404`）、双核 147,263/147,268 步、partial-ICFG 147,268 步边界均与 `ab9f4e1` 一致；r2 五入口仍 BLOCKED。
- **返回对照固化**（`probe_task_returns.py`，已接入 check_local）：暂停前 HANDOFF 表格的 5 个停止点在重建后的基线上全部复现——147,270（`unknown_core_stack_pointer`）/ 147,404（CPU1 真实 `idle` @ `0x12c5ca`）/ 149,660×2（IRQ127 未知门 / 待交付）/ 149,661（两核 IDLE 边界）。全部保持反事实假设标注。
- **IRQ127 第一层入口**（`probe_irq_entry.py` + `test_irq_entry.py`，已接入 check_local）：`software_irq_delivery_requests` + `software_irq_entry` 双 opt-in 投影。实际调度 IPI（`0x60644a6` SET 的 pending `0x8`）在 CPU0 `sti` 后请求交付；向量槽 `0x19fffc`（`0x19fe00+irq*4`，app 字节 `0x6065d90` 证明）读出固件注册的 handler `0x12AD2A`；RETI 装载中断 PC `0x12b608` 为显式假设；交付清 pending 为显式假设（`acknowledge` 单独计数）。handler 真实执行 `cli` + `push.rmask`（16B GPR 帧），149,662 步停在 `push.srl` 特殊帧（`multicore_special_frame_unmodeled`）——第一层的自然严格边界。负例：槽未写原子停（`vector_slot_uninitialized`）、非 127 IRQ 拒绝、双次交付拒绝、入口非 opt-in 拒绝。
- **报告差异**：执行器类报告 vs `ab9f4e1` 的差异均为元数据（k2 修正记录、适配器计数桶改名、开关字段），已在 AGENTS.md 说明；`evidence/` 的执行器类 JSON 尚未按新基线重新策划（待下一里程碑一起做）。

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
