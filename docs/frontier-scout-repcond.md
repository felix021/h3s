# 前沿侦察 REPCOND：REP×体内条件块停点（42,231,273 @ 0x60736c8）

路 REPCONDSCOUT / sim-repcondsout（基线 main 8b4de5d=SEGIMPL4 合并点，worktree
`~/worktrees/h3s/sim-repcondsout`，静态侦察 + codex 参谋，零 tools/ 改动，不 merge 不 push）。
2026-11-19 21:13 开工 / 21:24-21:29 codex 参谋（gpt-6.2-astra）/ 收口。
停点=路 SEGIMPL4 实测交接（evidence/sim-routes/segimpl4.json：
kind=`rep_multi_block_body_not_straight_line`，`ifs.gt.rr` @ 0x60736cc）。
证据 JSON：`evidence/sim-routes/frontier-scout-repcond.json`。

## 0. 停点与一句话结论

- 停点 `0x60736c8` = 寄存器计数 rep 体含条件块：`rep 16 r0 { ld.w r4=[r1+0];
  ifs.gt.rr(r4>r2){r4=r2}; ifs.lt.rr(r4<r3){r4=r3}; st.w.pin [r1++=4]=r4 }` +
  体后死分支 `bgts.ri (r0>0) goto -22`——**24 位音频样本原地钳位拷贝环**
  （r2=0x7FFFFF/r3=0xFF800000，钳到 [-8388608,+8388607]，计数 r0=[r8+8]>>2）。
- 上站路由笔记有**三处误导**（权威反汇编 + codex 复核确认，§1）：
  `rep 34 r0`→实为 `rep 16 r0`；`r3=0xFFC0C0C0`→实为 `r3=0xFF800000`；
  `r0=[r8+8]>>2 masked 0x7FFFFF`→**计数没有 mask**，0x7FFFFF 是装入 r2 的钳位上界。
  后两处旧值还被复制进 `probe_irq_entry.py` 注释与断言说明文字（9211/9263 行）。
- **hw 捕获里 rep×体内条件块的组合证据 = 无**（§2）：24 条 rep 捕获体一律是直行
  `r7 += 1`，条件块只作为独立指令捕获，从未进 rep 体。A 选项缺证据，排除。
- 三选一裁决（codex 与独立判断一致）：**B 拆解执行逐轮，但收窄形状**——把 rep
  当作「逐轮直行、条件块交给既有块机制」的组合投影，conditional 层独立开关、
  默认关，标 derived-not-measured；收窄到「寄存器 rep + 非嵌套 ifs.gt/lt.rr +
  t=1/e=0 + 块内 mov.rr + 无条件尾指令」这个可验证子集，不泛化到任意 else/嵌套
  块/块内并行对（§5/§6）。

## 1. 停点反汇编（仓内 pi32v2isa 权威解码，可复核）

`rep` 条目 `isa/pi32v2.toml:2471`（status=confirmed，sources=oracle+hardware）：
encoding `00000011nnnnrrrr`，n 字段 scale=2 add=2 → 体长 = 2·(n+1) 字节。停点
`0x60736c8` 字节 `70 03` → n=7、r=0 → **`rep 16 r0`**（16 字节体，非「34」）。

```
0x60736b0  ld.w.l    r0 = [r8+8]      ; 计数字节数
0x60736b4  ld.w.l    r1 = [r8+16]     ; 样本缓冲指针
0x60736b8  lsr.rri   r0 = r0 >> 2     ; 字节数 → 字数（逻辑右移，无 mask）
0x60736ba  li.u32    r2 = 8388607     ; 0x7FFFFF = 24 位有符号 max
0x60736c0  li.u32    r3 = 4286578688  ; 0xFF800000 = -8388608 = 24 位有符号 min
0x60736c6  li.u8     r4 = 0
0x60736c8  rep 16 r0 {                ; 计数寄存器 r0，体长 16 字节
0x60736ca    ld.w     r4 = [r1+0]     ; 读一个 32 位样本字
0x60736cc    ifs.gt.rr ifs (r4 > r2) {; 有符号比较（4 字节，t=0,e=0 → then 1 条 else 0 条）
0x60736d0      mov.rr  r4 = r2        ;   r4 = 0x7FFFFF（钳上）
             }
0x60736d2    ifs.lt.rr ifs (r4 < r3) {; 有符号比较（t=0,e=0）
0x60736d6      mov.rr  r4 = r3        ;   r4 = 0xFF800000（钳下）
             }
0x60736d8    st.w.pin [r1++=4] = r4   ; 写回同指针并后增 4（原地钳位）
           }
0x60736da  bgts.ri   ifs (r0 > 0) goto -22  ; 死分支（rep 已把 r0 减到 0，不跳）
0x60736de  ld.w.l    r0 = [r8+4]
...
```

体 16 字节 = ld.w(2) + ifs.gt.rr(4+then 2) + ifs.lt.rr(4+then 2) + st.w.pin(2)。
两个开启器原始字段都是 t=0,e=0（then 1 条、else 0 条）。函数 0x607367c
（push.rets_rl）入参 r0→r5、r1→r8（描述符）、r2→r6；描述符字段
[r8+4]=标志（0x6073698 beqz 判空）、[r8+8]=计数字节、[r8+16]=缓冲指针。
[r8+8] 由 helper 0x6073650（归一化循环 + `st.w [r0+8]=r7`）写入，运行时值。

## 2. hw 证据挖掘结论：无 rep×条件块组合捕获

对 `tools/reference-cache/pi32v2-isa/hw/` 全量扫（所有 json/jsonl，含
fm1-2026-10-24 及 v2/v3/v4/v5/app/app2/app3/app4、followup-*）：

- **rep 捕获共 24 条**（12 `rep` + 12 `rep.i`），全在
  `fm1-2026-10-24-v3/results.jsonl` 的 `batch-block-000`，无一例外。
- **体一律是直行 `r7 += 1`**（`gen.py:293-302`：
  `[rep word] + [add.ri.u5 r7,1]×body + [goto hw_exit0]`，body∈{1,2,3}）。
  `v3.py:23-32` 的 rep 模型只算净差 `Δr7 = count × (body_bytes/2)`，无逐轮回读。
- hw 捕获把 rep 当**单个整体指令**执行：芯片内部跑完体 N 轮，只在出口回读
  r0..r15 的净效果（count 寄存器→0、r7+=N），**不回放每轮、不呈现循环回边、
  不呈现体内部条件跳过**（`runner.S` 聚合读回）。
- 条件块（if/ifs/iff）只作为**独立指令记录**捕获：v3 主 results.jsonl 实有
  **50 个**条件开启器 id 各 12 条，其中 6 个 `iff.ne.rr.1..6` 无匹配模型、
  44 个有模型（`v3.py:59` 排除规则）——**从未出现在任何 rep 体内**。
- 寄存器 `rep` 的 12 条记录只覆盖 **2/4 字节体**、计数∈{1,2,3,5,7}；6 字节体
  出现在 `rep.i`（2/4/6 字节）。本站 16 字节体 + 计数 128 超出 hw 捕获的
  体长/计数范围，但未超 ISA 文档（2..32 字节）与既有 gate 已放行的范围
  （ROM 0x417e `rep 22 r5` 22 字节体、gate 计数上界 10000，见 §3）。

**结论：REP×体内控制流（条件块）的硬件组合捕获证据 = 无**。rep 语义（字节计数
循环、跑计数寄存器值次、递减到 0，1..7 已测、0 未测）与块语义（条件成立执行
then、否则 else，无 flags）是**分别**验证的，组合从未上过硅捕获。

## 3. rep 模型面现状（probe_boot.py multi-block gate）

- `immediate_rep_multi_block` 开关下通用多指令 rep gate（`probe_boot.py:2317-2437`）：
  - rep.i：count=立即数 n（1..32）；rep：count=`self.R[register]`（真实寄存器值），
    未知→`rep_register_count_unknown`、0→`unverified_zero_count_rep`（除非 opt-in
    `zero_count_rep_skip`）、>10000→超界停。
  - 体校验（2356-2419）：从体首逐条解码必须**恰好覆盖 body_bytes**，每条须
    `not inner.block`（非 if/ifs/iff 块开启器）且 `branch_field is None`（非分支）
    且非 rep/goto/call/callns/rts/pop.pc/tbb/tbh/lock/idle/cli/sti/... 且有模型 →
    否则 `rep_multi_block_body_not_straight_line` 拒绝。本站：体第 2 条
    `ifs.gt.rr`（block=True）即被拒。
  - 通过校验后 repeat 机制（2432-2436 + 793-801）：体逐条真实执行，PC 到体末
    回卷、计数寄存器递减，到 0 退出——**这就是逐轮拆解执行**，体指令走常规
    解码+模型路径。
  - 块机制（upstream `sim.py:145-168` + `238-248`）：`after_then`/`skip` 已正确处理
    if/ifs/iff 块（条件成立执行 then、否则 skip else），这是独立于 rep 的、
    已被 v3 hw 验证的机制。`probe_boot.py` 的 `finish_exact`（688-691）也联动
    `after_then` 递减。
- 已放行 **826** 次寄存器计数 rep（baseline `register_rep_multi_block_projection`
  =826，`evidence/p11_heap_adapter_count_baseline.json`），全是直行体或已放行的
  并行对体（ROM 0x417e 22 字节 memcpy 体、ROM 0xc70c DCU 并行对体）；本站是
  **第一个条件体**撞上 multi-block gate 被拒。
- 既有单测 `test_boot_bus.py:260-273`（`test_multi_instruction_rep_block_rejects_
  control_flow_body`）**显式钉死**「rep 体含条件块开启器（0x12cb12 的
  `if.eq.ri.modimm`）→ 诚实拒」——B 方案落地时此负例语义要随新开关分叉
  （关=保持拒，开=新窄形状才放行），不能静默改旧断言。

## 4. 钳位环运行时形状与前沿预判

codex 用 segimpl4 路的 ownbase4 快照重放到原停点（全程只读），钉死运行时值：

- **计数 r0 = 128**（=[r8+8]>>2=512 字节/4）、**源指针 r1 = 0x41d30c**
  （=[r8+16]）。按当前 MMU 映射逐字核 `[0x41d30c, 0x41d50c)`：**128 个对齐字
  全部可读、全为零**——首条 `ld.w` 不会撞 unmapped/uninitialized。
- 全零输入下每轮两个条件（r4>0x7FFFFF、r4<0xFF800000）**都假**，两个 `mov.rr`
  then 部分零次执行 → 前沿只覆盖「两条件都不取」路径；**上钳/下钳取真路径
  必须靠隔离单测**覆盖。
- 放行后每轮执行 4 条指令（ld.w + 2 个 ifs 开启器 + st.w.pin），整环
  **1（rep 头）+ 128×4 = 513 条本核指令**，然后 `bgts.ri`（r0=0 死分支不跳）
  + 1 步进 0x60736de 下游。每轮 r1 后增 4，出口 r1=0x41d50c、r0=0。
- **下一个停点预判**：源缓冲已知（全零），钳位环内无墙；停点应在环后下游
  （0x60736de 起第二相拷贝/处理 + 0x60736fc call），或下游既知的
  copex site16/site24（0x6072bb8/0x6072d1a）、二次 fn_config RMW——具体由实跑
  决定（地址序≠执行序，静态预判受表派发盲区限制，与 segimpl4 墙序偏差同族）。

## 5. 三选一裁决（codex B + 独立判断一致）

| 选项 | 裁决 | 依据 |
|---|---|---|
| A 放行 rep×条件块（若 hw 有证据） | **排除** | §2 无组合捕获证据；按红线「无硬件捕获证据不臆造」不能放行「rep×条件块」当作一个已验交互 |
| B 拆解执行逐轮（收窄形状） | **推荐** | 组合投影=两个已独立 hw 验证事实（rep 字节计数循环 + 块条件选臂）的组合，体指令全有模型，执行侧（repeat+after_then/skip）已能跑；conditional 层独立开关默认关，标 derived-not-measured |
| C 诚实停边界收窄 | 备选 | 前沿停在 42,231,273 原地；钳位环在 app 音频路径，其后 copex site16/24、二次 RMW 等下游全被这一个停点堵住；成本=前沿静止直到另案 |

**B 的组合性审查（codex 逐点 + 本路复核）**：

- 块 skip 是否漏掉 rep 回卷：**本站模型内无风险，硅上未知**。rep 按每次
  step_body 后 `pc==end` 判一轮结束（`probe_boot.py:793`）；本站两条件假时跳到
  0x60736d2/0x60736d8（体内），所有路径最后都执行 0x60736d8 的 st.w.pin 顺序到
  end=0x60736da——不需假设「条件跳过直接命中 rep 末端也算一轮」。芯片是否允许
  两控制态同时生效，捕获无回答。
- 尾存储时 after_then 残留：**本站无风险**。两开启器 t=0/e=0（then 1 条），假时
  不建立 after_then、真时建立 [1,0] 随 mov.rr 清掉；尾存储入口恒见 None。一般
  嵌套块有风险（上游 after_then 单槽）。
- 计数寄存器递减时点：**本站不敏感，泛化有风险**。体内不读不写 r0；现有实现
  每次到体末同时减 r0 与 remaining，末轮 r0=0 后 bgts.ri 自然不跳。若体内读
  r0 会见到未测中间值、写 r0 会让两计数分离——收窄形状里应拒绝体内引用 r0。
- 源读写是否成下一堵墙：**本次入口无缺页/未初始化风险**（512 字节已逐字核）。
  每轮真实 ld.w/有符号比较/st.w.pin 照常，保留同值写事件与指针后增。
- 两条件各自实测是否足以证整环：**仍有风险**。全零输入下两 mov 恒不执行，
  最终 RAM 看不出真假路径实现错误——须验 PC 命中/写事件/计数，不能只比最终
  缓冲（见 §6 单测）。

## 6. 实施形态（落地路执行，本路只出方案）

新增独立子开关（conditional 层，默认关），收窄到**可验证子集**：寄存器 `rep`、
非嵌套 `ifs.gt.rr`/`ifs.lt.rr`、`t=1/e=0`、块内 `mov.rr`、条件块后有无条件尾
指令、体内不引用计数寄存器、块终点严格早于 rep 终点。不用任意 else、条件块在
rep 尾部、块内并行对——这些都保持拒绝。

- **体校验 walk 块感知化**（改 `probe_boot.py:2356-2419` 的 walk）：REP 用字节
  界，块 t/e 用解码后的逻辑指令数；遇允许的开启器→查模型与操作数→解码并校验
  它声明的 1 条 mov→按实际指令宽度推进；守「块终点严格早于 rep 终点、全体恰好
  覆盖 body_bytes」；校验通过才建立 repeat。新开关关时保留原拒绝点（旧直行体/
  并行对体继续走旧路径，旧负例单测语义不变）。
- **正例**：真实 16 字节体，多轮喂 0x00800000（钳上）/0xff7fffff（钳下）/0/
  0x007fffff/0xff800000（保持），断言两 mov 各命中、每轮恰一次原址写、r1 每轮
  +4、flags 保持、r0 终 0、bgts.ri 不跳；另测单轮退出核对尾存储入口
  `after_then is None` 与终 `repeat is None`。
- **负例分清失败时点**：关开关/块越过体界/块恰在体末/嵌套块/块内分支或调用/
  块内并行对/体内引用计数寄存器→REP 入口拒绝、零部分提交；另造第二样本缺字节
  输入→第一轮写入保留、第二轮停 ld.w、该轮指针与计数不前进。
- **三臂**：跨代码 ON（上站快照恢复，记首 rep 入口/体内 PC 命中/尾存储次数/
  出口寄存器/IRQ 延后/实际新停点；全零入口预期 ld.w+2 ifs+st.w.pin 各 128 次、
  两 mov 各 0 次、r1=0x41d50c）；ownbase ON（新代码自建快照，两 ON 臂逐键一致）；
  ownbase OFF 因果负例（只关新开关，逐键复现 42,231,273 @ 0x60736c8 +
  `rep_multi_block_body_not_straight_line`/`ifs.gt.rr@0x60736cc` + 既有 826 计数；
  主线 289,849 @ 0x60679ba 不动）。合并前做从零回放。
- **证据卡措辞（写入）**：本路 conditional pass / derived-not-measured。FM-1 分别
  捕获直行加法体 REP 的出口净效果与独立有符号条件块的选臂效果；对固件 0x60736c8
  的非嵌套形状，模型假设每轮执行所选条件臂、在共同尾存储后消费一次 REP 计数；
  组合形状、128 轮中间状态与 IRQ 可见时点均无硅上捕获；当前入口 128 样本全零，
  上下钳取真路径由隔离单测覆盖。

## 7. 诚实边界与开放项

- AC791N（FM-1）≠ BR28：rep 与块的 hw 捕获只证 ISA 层；BR28 的组合行为无真机
  数据——模型保真度天花板即此，标 derived-not-measured。
- hw 只测过 rep 计数∈{1,2,3,5,7}（0 未测）、体长 2/4/6 字节；本站计数 128 +
  16 字节体是既有 gate 已接受的同一外推（22 字节 ROM rep 先例、计数上界 10000），
  非本路新增外推——本路新增的只有「体含条件块」。
- 全零入口只覆盖两条件都假的路径，钳位取真路径靠单测，不上前沿实测。
- 组合投影的硅上未知（如实记）：条件跳过与 rep 回卷两控制态同时生效是否成立、
  体内读/写计数寄存器的中间值、逐条推进虚拟时钟时的 IRQ 可见时点——都没有捕获
  证据；收窄形状通过「体内不引用计数寄存器 + 无条件尾 + 块终点早于 rep 终点」
  规避前两个，IRQ 延后沿用既有 deferral 机制但不声称硅验证。
- 上站路由笔记三处误导（rep 34 r0 / r3=0xFFC0C0C0 / masked 0x7FFFFF）已勘正
  （§1）；其中后两处已复制进 `probe_irq_entry.py` 注释/断言说明（9211/9263 行），
  落地路应一并勘正文字（不碰行为断言值）。

## 8. codex 参谋记录

gpt-6.2-astra，2026-11-19 21:24-21:29，
`~/tmp/codex/202611192124-repcond/`（prompt.md/final.md/log.md）。
裁决原文要点：「选 B：在独立、默认关闭的条件投影下，放行本站这种『块内只有一条
mov、两条路径都在无条件尾存储前汇合』的寄存器 REP，沿用现有机制逐轮执行。硬件
捕获支持两个组成部分，组合行为仍须标 derived-not-measured。」codex 用 ownbase4
快照重放到原停点并核 128 字源缓冲（r0=128、r1=0x41d30c、全零），三处勘误与
组合性五点审查（§5）本路逐条复核成立；「44 个开启器→实为 50 个 id（6 个无模型）」
「寄存器 rep 只覆盖 2/4 字节体」两处本路复核采纳。本路与 codex 无分歧。
