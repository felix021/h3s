# 前沿→D1 剩余执行依赖表（路 DEPMAP / sim-depmap，只读分析）

2026-11-17。本页是 codex 全局复盘点名的「最值钱补充」的**续篇**：以当前条件场景
`p11_heap_experimental_config` 前沿 **41,847,303 @ 0x6019458**（stop=`unmapped_memory`
读 **0xfd610c**，JL_PLED 域 slot3 RMW 内部读）为起点，列出到 **D1 正式过线**（五
verdict LOOP/TMR/TCK/DSP/HLT 全 PASS）之前剩余的每一处未放行 gate，并逐行给
「函数/地址 | 当前是否已执行 | 后继任务 | 等待来源」。**只读**：未改任何 tools/ 代码、
未跑任何改场景的 probe；全部结论 [文档]（既有 docs/HANDOFF 钉死条目）、[静态]（反汇编
证据已合并于所引文档）或 [codex 参谋]（本路 codex gpt-6-astra 只读评审，结论全文见
`~/tmp/codex/202611171526-depmap/final.md`），运行状态以已合并前沿报告 41,847,303 为准。

**先读 [docs/frontier-dependency-table.md](frontier-dependency-table.md)（路 AY，2026-11-14）**
——那是「initcall 全表 + 异步工作清单 + S19 无 BT 裁决」的函数级路障清单，本页不再重复
它的 initcall 枚举，只接续「S19 之后」这一小段。

## 0. 摘要

1. **前沿已过 S19 消息循环**：STOREG3 放行独立后增整字存储 `st.w.pin.l` 直写后，
   WDFEED 前沿 41,828,530 @ 0x60649d0（get-event 解包循环写 buf[0]）被穿，世界 +18,773
   步走完 S18 事件消费链（解包缓冲填满→分发表 walk→handler 0x6023962 执行→OS 调度
   返回），进入 **LED 驱动 helper 0x6019448**，停在 **0x6019458
   `rmw.and.nmodimm [0xfd610c] &= 0xFFFFFFDF`** 的架构性 RMW 内部读（真读旧值、非簿记读）。
2. **0x6019448 是短 helper、不是独立任务**（[codex 参谋] 重新解码钉死）：prologue
   `li r0=41→delay(41)→li r0=0xfd610c` 后 0x6019458 清位、下一条 0x601945c 即弹栈返回，
   有 4 个直接调用点 **0x6019bcc / 0x6019d50 / 0x601a0c8 / 0x601a666**。不能从「调度返回后
   进入这里」推断存在独立 LED 任务，也不能推断它返回后 CPU0 就 idle。
3. **剩余 gate 是一堵硬墙 + 多项观察件缺口 + 未知数量的运行态后续**：
   - **G1（主，未放行）**：**JL_PLED 块**的读侧语义（前沿撞 CON3 RMW 读；CON0 的
     save-restore 消费紧随其后）。三选一裁定见 §3。
   - **G2（纯前瞻）**：helper 返回后，真实 TCB/队列/调用者清查——剩余 ready 任务
     （jlstream/dac/anc/pmu_task/dev_mg/usb_stack）哪些真实运行、触碰哪些未建模 SFR，
     离线不可判、必须放行跑显形。
   - **G3（观察件缺口，非 SFR）**：不止 H5 的 `ready_tasks`——DSP-04 消息链观察锚、
     多消息关联、TCK `task_switch_observations`、队列 capacity/qlimit 占位都还缺。
4. **D1 五 verdict 各自卡点**（详见 §2）：LOOP/TMR/DSP/HLT 直接或间接卡在 **G1** +
   **G3**；TCK 的「期限/交付/ACK/jiffies」机制已全，只被 G1 的未知停点 + G3 的
   `task_switch_observations` 缺键连带卡住。

## 1. 前沿定位（已证 + 静态已核）

| 项 | 值 | 证据 |
|---|---|---|
| 前沿 | 41,847,303 @ 0x6019458 | HANDOFF 前沿表（STOREG3 重钉，本 worktree 表仍在 41,828,530）+ registry |
| stop | `unmapped_memory` 读 **0xfd610c**，RMW `&=0xFFFFFFDF` 内部读 | HANDOFF 路 STOREG3 条目 |
| 0xfd610c 归属 | **JL_PLED->CON3**（br28.h L1249-1272：`JL_PLED_TypeDef` 第 4 个成员 `__RW CON3`；基址 0xfd6100=CON0） | SDK 头核对；CON0=0xfd6100 的 save-restore 消费者 0x60194a4/0x60194b4 由路 BO 钉死 |
| 0x6019448 形状 | **短 helper**：`li r0=41→delay(41)→li r0=0xfd610c→0x6019458 rmw.and→0x601945c pop 返回`；4 调用点 0x6019bcc/0x6019d50/0x601a0c8/0x601a666 | [codex 参谋] 重新解码官方 app 钉死（本页采纳，修正 STOREG3「LED 驱动 0x6019448」的叙事粒度） |
| 到达路径 | S18 消费链走通 +18,773 步 → OS 调度返回 0x6066016 → （某 ready 任务/调用链）→ 0x6019448 → 0x6019458 | HANDOFF 路 STOREG3 条目 |
| 纯 CPU0 段互证 | 事件钟 82,545,158 冻结 / parked 唤醒 12791+257+176 冻结 / per-core1 11,496 不变 / 喂狗 162 事务冻结 / wdt elapsed 100→104 / ipmask 6→0（出临界区） | HANDOFF 路 STOREG3 条目 |

**注意（诚实边界）**：0x6019458 之后的下一条 0x601945c 即返回——helper 体本身只有
「delay + 一次 PLED RMW + 返回」三件事。它的 4 个调用点各属哪个任务/执行链、返回后
CPU0 是否直接 idle，**[未钉]**，是 G2 的核心未知（§3）。

## 2. D1 五 verdict 卡点

| verdict | 判据要点（d1-acceptance §1 + acceptance-samples/d1/README） | 当前卡在哪 | 解锁 gate |
|---|---|---|---|
| **LOOP-01** (D1-1) | ①S19 循环头 0x60123e6 首达 + S18 首批消费链每站点有执行证据；②空队列后 pend 写面四写 + 让出链执行；③两核各有代码依据的状态；④event_clock_engaged；⑤K1/K2=0 | ①已达成（前沿在循环头之后）；②③④待 CPU0 重新 park（当前在 helper 内）；⑤K1/K2 零执行（健康） | **G1**（PLED 放行→helper 返回→调用链收尾→CPU0 park） |
| **TMR-02** (D1-2) | ≥3 周期逐周期四查全过；**窗口内零 Blocked（无未知停点）** | Timer0 IRQ4 交付机制已全；卡在「窗口内零未知停点」——G1 的 PLED 读就是未知停点 | **G1** |
| **TCK-03** (D1-3) | core tick 闭式期限差 0 + 交付/handler/ACK 恰一次 + jiffies +1 + timeslice call 执行并返回 | 机制已全（core_tick_irq3_delivery）；闭式期限 PASS 已实测；卡在 G1 未知停点 + **`machine.task_switch_observations` 未生产**（G3） | **G1** + **G3** |
| **DSP-04** (D1-4) | 固件自然 (18,0) 全链：post→入队→get-event→分发表→handler 0x6023962 执行→返回 0→回 pend | (18,0) 链已走通（STOREG3 的 +18,773 步）；但**观察器 `id_read/table_record_hit/handler_returned` 无锚、`id_read_value` 恒 None**（G3），且多消息关联 `_ChainTracker` 只存一个 open_sample、S18 连 post 两条会串——断言面凑不齐 | **G1**（间接）+ **G3** |
| **HLT-05** (D1-5) | H1..H8 | H1/H2/H3/H4/H6/H7 机制面已就绪；H1 在 park 稳态锚下应过；**H5 `ready_tasks` 未生产 + 队列 capacity/qlimit/physical 占位**（G3）；窗口被 G1 截断 | **G1** + **G3** |

**结论**：五 verdict 没有一个是「SFR 机制还没实现」——真正缺的是 **G1（PLED 块读侧
建模）** + **G3（多处观察件缺键）**。G1 是前沿 gate，G3 是观察件侧缺口（非前沿、
非 SFR），且 G3 缺口比此前只记 H5 一条要**多得多**（codex 参谋点名「只剩 PLED+H5 低估了
当前代码缺口」）。

## 3. 剩余 gate 依赖表（核心交付）

每一行=一处「前沿即将撞到、或 D1 依赖的未放行 gate」。

| # | 函数/地址 | 当前是否已返回/已执行 | 后继任务 | 等待来源（什么外部完成信号 / 什么未建模 SFR / 什么 IRQ 交付） |
|---|---|---|---|---|
| **G1** | **JL_PLED 读侧**：前沿 0x6019458 读 **0xfd610c=CON3**（`old & 0xffffffdf`）+ 0x60194a4/0x60194b4 读 **0xfd6100=CON0**（save-restore：保存旧字→判旧 bit0→决定恢复使能）+ 0x6069b0e 读 CON0 bit0 走另一分支 | **未放行**（前沿停在其首个访问 CON3 RMW 内部读；真读旧值、非簿记读） | 三选一（codex 参谋裁定，见下）后放行；放行后 helper 0x601945c 返回 → 4 个调用点之一收尾 → 顺真实 TCB/队列继续（G2） | 未建模 SFR 读 0xfd610c；**读侧三选一**：①写记录+读回=最后写值 **不足**（首个 RMW 尚无已知写值、无旧字可给）；②整块合成初始 0 **不推荐**（CON0 初值控制分支，零值会替场景选「此前未使能」）；③**首读具名 fixture + 后续配置读回承接真实写值（推荐）**——把不可推导初始态显式暴露，首次初始化后由真实 RMW/整字写维护。零只作 **synthetic-not-measured 探路值**、不称复位值；敏感性臂至少查 CON3 保留位 + CON0 bit0 两态首分叉 |
| G1-边界 | **PLED 计数/同步面**：0xfd613c=CNT_RD（`__RO`）、0xfd6144=CNT_SYNC（`__WO`）、0xfd6148=CNT_DEC（计数作用未证） | **未放行（不预先建模）** | **不要把 0xfd6100..0xfd6148 整体做成 RW latch**；先只按真实消费者覆盖配置地址，计数/同步面若到达再另立 gate | 运行态显形（LED 域是否真读 CNT_RD/写 CNT_SYNC/CNT_DEC 未证） |
| **G2** | **helper 返回后的异步执行清查**：4 调用点 0x6019bcc/0x6019d50/0x601a0c8/0x601a666 各属哪个执行链 + 剩余 ready 任务 jlstream(0x6086980)/jlstream_N·dac(0x6086c8c/0x6076b4c，惰性)/anc(0x602d3a0)/pmu_task(0x6068f0c)/dev_mg(0x6000d6c)/usb_stack(0x600741c，条件) | **未放行（纯前瞻）**——G1 放行前不可达，触达与否由运行态显形 | **先观察再建模**：每次调度切换记录任务入口/TCB/队列计数/唤醒原因/首次新 SFR 消费；只处理真实到达的消费者。**优先盯音频流（jlstream）再盯电源恢复（pmu_task）**——jlstream 队列若有 stream 消息会进设备/效果节点并可能继续创建任务；pmu_task 链上有 0x6069ab6 读 CHIP_ID(0xfd0200，无 fixture)+0x6069b0e 读 PLED bit0 | 运行态显形；候选 SFR 面已列（音频 0xfe0500 15 站点 / 0xfe0a00 8 站点 / ANC 0xfe0400 / CHIP_ID 0xfd0200），但这些是否在「两核 park 前」触达未证。**已知「有容器但消费者语义未认证」的风险**：0x606a70e 读 0xfe0900 后查 0x380 位域、0x606b352 消费 DAA_CON2 0xfe0a08 bit1——不 Blocked ≠ 已获语义支持 |
| **G3** | **D1 观察件缺口**（d1_window_observer / check_d1_acceptance）：DSP-04 的 `id_read/table_record_hit/handler_returned` 无锚、`id_read_value` 恒 None（应绑 0x60123f4/0x601244e/0x6012450→0x6064cf6）；`_ChainTracker` 只存一个 open_sample、S18 连 post 两条会串；TCK 的 `task_switch_observations` 未生产；HLT 的 `ready_tasks` + 队列 capacity/qlimit/physical 占位 | **未生产（pending，非前沿 gate）** | 补齐观察件（归 d1_acceptance 观察开关，零新增 SFR 投影）；可与 G1 并行、且应在 PLED 推进前准备好避免昂贵全量重跑后仍 pending | 观察器 note 钩子（TCB 扫描/队列轨迹/消息链锚点）；与 G1 解耦 |

## 4. 已放行 gate 上下文（本前沿之前，简要）

为便于对照「哪些已放行、哪些还欠着」，从 registry 101 开关里摘出**本前沿链上关键
已放行项**（完整清单见 tools/frontier_registry.py，叙事见 HANDOFF「已实现」）：

- **watchdog feed**（`wdt_mailbox_feed`，路 WDFEED）：喂狗邮箱事务闭合→P11 侧 WDT
  feed 副作用，越过 32,737,356 伪到期点。
- **st.w.pin.l 独立后增整字存储直写**（`exact_postincrement_word_stores`，路 STOREG3）：
  独立指令（非并行对）整字覆盖写直写，越过 41,828,530 簿记读停点。
- **S18 事件消费链**：get-event 解包（0x60649b0/0x60649d0）、分发表 walk、handler
  0x6023962 执行——全走既有 gate（半字 pair / compose pair / rep 体 / OS 调度）。
- **Timer0 IRQ4 交付**（`timer0_irq4_delivery`，路 BE）、**core tick IRQ3 交付**
  （`core_tick_irq3_delivery`，路 BF/BS）、**event_clock**（路 AB/BQ/CW）——D1 窗口的
  时间驱动主干已全，只等世界干净跑到 park 稳态。
- **N2 电源框架簇（SD0/SPI1/SPI2/IIC/PLED/CHIP_ID 等）**：**未放行**——这是 G1 的
  归属簇；此前判「健康路径零触达（S13 门=0、0x6067f84 零直接 call）」是基于 boot 主
  路径，现在 PLED 经 LED helper（任务/事件域）被触达，该判断需按「到达顺序」更新。

## 5. 诚实边界

- **已证（有 hard assertion）**：前沿 41,847,303 @ 0x6019458 的 stop 字段（kind/address/
  size/PC）由 probe require 断言 + analyze freshness 钉子 + test_image_trace_diff 字面值
  三处派生自 registry 单点，逐字段机器核对；STOREG3 的 +18,773 步纯 CPU0 段互证
  （事件钟/parked/喂狗/per-core1/ipmask 全冻结或精确 +1）是放行跑实测。
- **条件通过（derived-not-measured）**：0x610c=JL_PLED CON3 的 SDK 头归属（br28.h
  参考头，非精确 QCY 固件源）；CON0 save-restore「真消费旧值」的路 BO 静态结论；
  D1 五 verdict 的「机制已就绪」判断（timer0/core_tick/event_clock 已接线，但尚未在
  park 稳态窗口真实跑满 ≥3 周期）。
- **未放行（纯前瞻）**：G1 的 PLED 三选一（codex 已给推荐，但未实施）；G2 的「helper
  返回后还有哪些任务/SFR」完全未证（运行态才显形）；「PLED 放行后 CPU0 直接 park、
  无更多 SFR gate」是**乐观前提**，未经放行跑验证——不得当作已证。**codex 参谋明示：
  「不能预先承诺 PLED 放行后直接 park」。**

## 6. codex 参谋排序结论（gpt-6-astra 只读，全文 `~/tmp/codex/202611171526-depmap/final.md`）

按「新增假设最少 + 输出最明确 + 距 D1 收益最大」排序，前 3 个建议推进顺序：

1. **先放行 PLED，选「首读具名 fixture + 后续配置读回承接真实写值」**（唯一已撞的硬墙；
   新增假设中等）。关键限定：不要按 SDK 结构把 0xfd6100..0xfd6148 整体做成 RW latch——
   CNT_RD 是 RO、CNT_SYNC 是 WO、CNT_DEC 计数作用未证，先只覆盖配置地址；零只作
   synthetic-not-measured 探路值、不称复位值。
2. **紧接着补观察件**（缺口不止 H5 的 `ready_tasks`；零新增硬件假设）。DSP-04 消息链
   观察锚（id_read/table_record_hit/handler_returned）、多消息关联 `_ChainTracker`、
   TCK `task_switch_observations`、队列 capacity/qlimit 占位——都归已有 `d1_acceptance`
   开关，不新增 SFR 投影，且可在 PLED 推进前备好。
3. **PLED 返回后按真实 TCB/队列/调用者清查异步执行，先盯音频流（jlstream）再盯电源
   恢复（pmu_task）**（纯前瞻、最大运行风险）。静态清单只能给候选集、不能给实际 ready
   集与执行顺序；每次调度切换记录任务入口/TCB/队列计数/唤醒原因/首次新 SFR 消费，只
   处理真实到达的消费者。

另两条（收尾）：④收齐 S18 两事件实际去向（(20,2) id=2 节点存在性、(18,0) 深层门控
0x125b54/0x127184 实际分支、串到 0x6064cf6 pend + K1/K2 未执行）再认定 S19 阻塞闭环；
⑤把正式观察窗绑定到已建立的 S19/app_core 消费链（不要在 boot 中途 park、不要随新
pend 反复重锚）。最终五项全 PASS 仍应标 **条件通过 / derived-not-measured**，不升级为
真机启动认证。

## 7. 不确定处（如实标注）

1. **G2 的真实规模**：helper 返回→两核 park 之间，调度器跑几个 ready 任务、触碰几个
   未建模 SFR——离线不可判，G1 放行跑是唯一测量手段。
2. **0x6019448 的 4 个调用点归属**：0x6019bcc/0x6019d50/0x601a0c8/0x601a666 各属哪个
   任务/执行链未钉（[codex 参谋] 只钉到「短 helper + 4 调用点」这一层）。
3. **0xfd610c 的完整访问面**：并行路正在探；本页只钉「前沿这一条 RMW + 路 BO 的 CON0
   save-restore 面」，CON1/CON2/CNT_RD/CNT_SYNC/CNT_DEC 是否被读、被谁读未钉。
4. **D1 窗口触发口径**：d1_acceptance 是 probe_only 场景（park 后 N 模型步），观察器
   当前在任意 0x6064cf6 后开窗、新 pend 又重锚——codex 参谋点名需把正式起点绑定到
   S19/app_core 消费链，本页未逐字段复核该接线。
5. **版本边界**：本 worktree 的 registry FRONTIERS 仍是 WDFEED 41,828,530；41,847,303
   @ 0x6019458 作为 STOREG3 在途前沿采纳，其 hard assertion 未在本分支独立核验。
