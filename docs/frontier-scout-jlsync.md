# 前沿侦察 JLSYNC：JL_SYNC (audio_sync) 块停点（42,061,357 @ 0x60befe6）

路 SYNCSOUT / sim-syncscout（基线 main 345b08c，worktree
`~/worktrees/h3s/sim-syncscout`，静态侦察，零 tools/ 改动，不跑条件线）。
2026-11-19 00:40 开工 / 01:03 材料收口 / 01:06-01:13 codex 参谋
（gpt-6-astra）/ 01:24 勘误核实后定稿（§8）。停点=路 SRCSYNC 实测交接
（evidence/sim-routes/srcsync.json next_stop：`unmapped_or_readonly_write
0xfe2500=JL_SYNC CON 整字写 0`）。反汇编用仓外既有
`~/code/headsets/archive/h3s-all/h3s/analysis/anc-cycle-20261109/app-linear.asm`
（app.bin sha256 18f3d573…，与 disassemble_app.py 钉死源一致；li 立即数
显示为十进制）；一次性脚本内联 python（li/call/字面量扫描），不进仓。
证据 JSON：`evidence/sim-routes/frontier-scout-jlsync.json`。

## 0. 停点与一句话结论

- 停点指令 `0x60befe6: 81 60 [r0+0] = r1`（前邻 0x60befde `li r0=16655616`
  =0xfe2500、0x60befe4 `r1 = 0`），落在 **audio sync 对象创建器 0x60befae**
  体内（zalloc 56B → 字段 init → probe → **停点 CON=0** → BT clock timer
  配置 → request_irq(88) → fn_config → 链表插入），IPMASK=6 临界区内。
- 停点是 **JL_SYNC 块首个被当前链触达的字**：probe 0x6073502（sync 对象
  链表头 0x12528c：空→1/非空→0）返回 1（链表空）走完整初始化臂；若返回
  0 整个 SFR 段被跳过（0x60befdc `if (r0==0) goto 48` 直达链表插入）。
- **13 成员逐字分类（§3）**：creator→fn_config 链上最终触达**四字**——CON
  （停点整字写 0 + fn_config RMW 内部读→整字写）+ CON1/BT_OFFSET/MIN_SLOT
  （fn_config 纯写）——与 SRCSYNC 三字容器同族形状。但**本次续跑（跨过
  停点后）只执行 CON 一写**：fn_config 在 request_irq 之后（§6 墙序），
  WO 三字本次零执行。RO 状态字（DASPACE/OFFEND/OFFSUM/PCMNUM/BTTIME）
  与 OUTSR/DABUF 读者不在当前执行前缀（§4），不需要 fixture。
- 三选一裁决（codex 定稿，§5/§8）：**收窄版 (a) 四字配置容器**——CON
  **无合成初值**（首个访问就是写；首写前读拒绝）、写后读回=最后写值、
  RMW 内部读走通用 mmio 路径；WO 三字纯写记录读拒绝；可选消费者 PC 守卫
  拒绝 IRQ/getter 的分支读点。放行后下一停点预判 **42,061,359 @ 0x60befee
  写 0xF00130=0x2F4**（BT clock timer 配置，未建模，+2 步，§6）。

## 1. 块定义与全 app 访问面总扫描

SDK 头 `br28.h`（sha 111e29ea…，424-442 行）：JL_SYNC =
hs_base(0xfe0000)+map_adr(0x25,0x00) = **0xfe2500**，13 成员：

| 偏移 | 成员 | 属性 | | 偏移 | 成员 | 属性 |
|---|---|---|---|---|---|---|
| +0x00 | CON | __RW | | +0x1c | CON1 | __WO |
| +0x04 | DABUF | __RW | | +0x20 | CON2 | __RW |
| +0x08 | DASPACE | __RO | | +0x24 | BT_OFFSET | __WO |
| +0x0c | OFFEND | __RO | | +0x28 | MIN_SLOT | __WO |
| +0x10 | OFFSUM | __RO | | +0x2c | OUTSR | __RW |
| +0x14 | PCMNUM | __RO | | +0x30 | INSR | __WO |
| +0x18 | BTTIME | __RO | | | | |

IRQ：`IRQ_SYNC_IDX=88`（reference-cache 701n-boot hwi.h:68；与
IRQ_SRC_SYNC_IDX=89 相邻，恰对应 SFR 块 0x25/0x26 相邻）。

总扫描（li 十进制立即数 grep + app.bin 逐字节偏移 4 字节小端字面量全找 +
函数族通读；codex 独立复核 384 条反汇编记录与 app.bin 原始字节一致）：

- **li 站点恰 12 处，全部在 0x60beeee-0x60bf1f0**（audio_sync 库函数族）：
  CON×8（0x60beeee/0x60bef52/0x60befde/0x60bf05c/0x60bf0be/0x60bf128/
  0x60bf192/0x60bf1f0）、DASPACE @0x60bf174、OFFEND @0x60bf16a、
  BTTIME @0x60bf1b2/0x60bf1bc。
- **app 零 .data/.rodata 地址字面量**：`[0xfe2500,0xfe2600)` 全部 13 个
  成员地址的 32 位值在 app.bin 的命中恰为上述 12 个 li 的立即数字段，
  无 hw_res 表（与 JL_SRC_SYNC 的 .data hw_res 表 0x60a29d4 形成对照——
  JL_SYNC 纯 li 装载驱动）。
- **DABUF(+4) 无 li 但经基址寄存器读**：IRQ handler 0x60bf26c `r0=[r5+4]`
  （r5=0x60bf1f0 装载的 CON 基址）——li 扫描必须辅以函数体通读才不漏。
- OFFSUM(+0x10)/PCMNUM(+0x14)/INSR(+0x30) 在 12 个基址装载点与可见访问
  链中零访问（不能排除动态计算地址/跨函数传参/其他执行域，主核 app
  线性窗完备性边界照抄 LPV/ADA 措辞）；CON2(+0x20) 零 SFR 访问
  （0x60bf18e `r0=[r0+20]` 是 RAM 对象 getter：r0=0x607f620 传入的节点
  对象指针，20 是十进制=对象偏移 0x14，非 CON2 的 0x20）。

## 2. audio_sync 库函数族逐个审计（窗口 0x60bee98-0x60bf2c8）

全部通读；临界区调用对（call -100219xxx/-1002196xx 系）= enter/exit
critical（IPMASK）。**库窗口起点是 0x60bee98**（链表扫描函数）与
0x60beed0（对象 +14 setter）——0x60beee6 只是本次 SFR 审计窗口的起点，
非完整库边界（codex 勘误，本路复核成立）。

- **0x60beee6 fn_clear_b22**：enter crit → `CON &= 0xFFBFFFFF`（清 bit22，
  与 SRCSYNC CON1 段尾 `&=0xFFBFFFFF` 同款位）→ exit。RMW 内部读只进
  写回组装。
- **0x60bef00 fn_register(obj,tag)**：b[obj+3]=tag → enter crit → 走链表
  查重（比较对象/tag）→ **call fn_clear_b22（0x60bef2c call -72）——
  不是纯链表插入**（本路初稿误，codex 勘误成立）；真正的链表插入是
  creator 0x60bf01c call 0x60735d8（`[r2+4]=r0; [r0]=r2; [r0+4]=r1;
  [r1]=r0` 双向挂链）。
- **0x60bef46 fn_config(obj)**：probe≠0 臂内四字全触达——
  `BT_OFFSET(+0x24)=0`（0x60bef58 `[r0+36]=0x0`，36=0x24）；`CON1(+0x1c)
  = h[obj+10]<<16`（0x60bef5c-60，预期值 200<<16=0x00C80000）；
  `MIN_SLOT(+0x28)=h[obj+12]`（0x60bef96，预期 80）；**CON 读-改-写**：
  `r1=[CON] & 0xE9DFC000`（保留位）与对象字段组装——`r1|=0x06210000`/
  `r2=(r5&0xF9DEFFFF)|0x06200000` 两版本，`if (r4!=1)`（r4=b[obj+4]
  **对象字节非 CON 值**）二选一 → 整字写 CON（0x60bef9e）。**CON 旧值
  只进写回组装，不决定分支/地址/循环/返回值**（codex 逐指令复核同
  结论）；旧值影响保留位，非死读。尾部 `b[obj+6]==0` 条件臂写
  `0xF00000 |= 0x400`（modem 窗，§6）。
- **0x60befae creator（停点函数）**：zalloc 56B（失败返 NULL）→ b[obj+6]=
  参数 → enter crit → 字段 init（h[+10]=200/h[+12]=80/h[+8]=512/h[+4]=512）
  → **probe 0x6073502**（`r1=0x12528c; r2=[r1]; r0=1; if (r2!=r1) r0=0`
  ——链表空判定）→ 成功臂：**0x60befe6 CON=0（停点）** → 0x60befe8
  `li r0=15728944`（**0xF00130**）→ `[0xF00130]=0x2F4`、`[0xF00140]=1754`
  （BT clock timer 配置，§6）→ `li r2=0x60bf1e4`、r0=88、r1=2 → call
  0x6065d86（request_irq(88,prio=2,flags=0,handler)，§6 静态收窄）→
  call fn_config（0x60bf00c 近调用）→ 链表插入（0x60bf01c call 0x60735d8）
  → **0x60bf030 回调安装**（`[obj+32]=r1; [obj+36]=0x607f5f6`——getter
  分发域函数 0x607f5e2 的中段入口，见 §4）→ exit crit → 返回对象。
  （0x60bf030-0x60bf03a 在线性流上紧邻 creator 尾部 rts 后 fall-through
  「看似死代码」，实为 0x607e50e 直接调用的活函数——本路初稿误标死
  代码，codex 勘误成立。）
- **0x60bf03c fn_stop / 0x60bf088 fn_pause / 0x60bf0f4 fn_resume**：
  对象状态机（b[obj+0]=0/1/2）+ CON RMW/整字写×N（内部读均只进写回
  组装；fn_stop `CON|=0x100000; CON&=0xF81FFFFF` 后调 88-call
  〔0x60bf072→0x6066012，IRQ88 注销/使能族，方向 [推断]〕；fn_pause/
  fn_resume 各有一处链表走查（同类对象存在则只改状态不写 SFR）；
  fn_resume 内对象回调 `call r2`）。
- **0x60bf16a-0x60bf1bc getter×5**（RO 状态消费面）：
  - get_OFFEND（0x60bf16a）：纯读返回。
  - get_DASPACE（0x60bf174）：`r1=[+8]; if (r1!=0) 返回 utof(再读) else
    返回 float 272.0`（**0x43880000**——本路初稿误写 256.0f/0x43800000，
    codex 勘误成立）——**读值进分支**。
  - get_SLOT_MIX（0x60bf192）：`r2=[+8](DASPACE); if (r2==0) 返回 0`——
    **DASPACE 读值进分支**；非零时 `uextra(CON,p:28,l:1)` vs `b[r0+3]`
    （对象槽号）**比较进分支**：相等返回 `OUTSR(+0x2c)`，否则返回 RAM
    对象[+16]。
  - get_BTTIME（0x60bf1b2）：纯读返回。
  - get_BTTIME_calc（0x60bf1bc）：`BTTIME + 0xF00144/0xF00148 两字段拼算`
  → 返回（§6 timer 读面）。
- **0x60bf1e4 IRQ handler（IRQ_SYNC_IDX=88）**：完整读面在此——
  `r0=[CON]; CON|=0x100000; if (r0 & 0x80000)`（**bit19 读值进分支**：
  已置→直接对象扫描，未置→0xF00144/48 读算偏差 abs<3 判断）；
  `CON &= 0xFF7FFFFF`（清 bit23）；链表扫描对象：`uextra(CON,p:28,l:1)`
  vs `b[obj+3]`（**bit28 进分支**）；匹配对象：`OUTSR 读→写 RAM 对象
  [+16]`、`DABUF 读−h[obj+14]→写 RAM 对象[+20]`（**数据面消费**）、
  CON bit0-13 RMW（插对象 h[+8]）；对象回调 `call r2`（回调槽=obj[+36]
  ——creator 装的正是 getter 分发域入口，§4）；rti。

## 3. 13 成员逐字分类（访问面/读写形状/读值去向/链上状态）

| 成员 | 偏移 | 全 app 访问点 | 读值去向 | 当前链 |
|---|---|---|---|---|
| **CON** | +0 | 写：creator 0x60befe6 整字写 0（停点）、fn_config 0x60bef9e 整字写、fn_clear_b22 0x60beef4 RMW、fn_stop 0x60bf062/66 RMW、fn_pause 0x60bf0d2/ea 写、fn_resume 0x60bf13c/142 写+RMW、IRQ 0x60bf1f8/24a/28a。读：fn_config 0x60bef62、fn_pause 0x60bf0c4/d8、fn_resume 0x60bf12e（RMW 内部读）、IRQ 0x60bf1f6/**25e/276**、get_SLOT_MIX 0x60bf19c | RMW 内部读→写回组装（保留位 0xE9DFC000/0xF00000FF 等合并）；IRQ/get_SLOT_MIX 读→**分支**（bit19、bit28 vs 槽号） | **触达：本次续跑唯一执行字（一写零读）**；fn_config RMW 须过 §6 三道墙后才执行 |
| DABUF | +4 | 读：IRQ 0x60bf26c | →算术→**写 RAM 对象[+20]** | handler 未触发 |
| DASPACE | +8 | 读：get_DASPACE 0x60bf176、get_SLOT_MIX 0x60bf198 | **读值进分支**（r1!=0 / r2==0） | getter 未到达 |
| OFFEND | +c | 读：get_OFFEND 0x60bf170 | 返回值 | getter 未到达 |
| OFFSUM | +10 | 窗口内零访问 | — | — |
| PCMNUM | +14 | 窗口内零访问 | — | — |
| BTTIME | +18 | 读：get_BTTIME 0x60bf1b8、get_BTTIME_calc 0x60bf1c2 | 返回值/算术→返回值 | getter 未到达 |
| **CON1** | +1c | 写：fn_config 0x60bef60 | 纯写 | fn_config 内（本次不执行） |
| CON2 | +20 | 零 SFR 访问（0x60bf18e 系 RAM 对象 getter，偏移 0x14） | — | — |
| **BT_OFFSET** | +24 | 写：fn_config 0x60bef58 整字写 0 | 纯写 | fn_config 内（本次不执行） |
| **MIN_SLOT** | +28 | 写：fn_config 0x60bef96 | 纯写 | fn_config 内（本次不执行） |
| OUTSR | +2c | 读：get_SLOT_MIX 0x60bf1a8（条件臂）、IRQ 0x60bf268 | 返回值 / 写 RAM 对象[+16] | 均未到达 |
| INSR | +30 | 窗口内零访问 | — | — |

**结论**：读值进分支的成员（CON bit19/bit28、DASPACE）读者只有 ioctl
getter（分发函数 0x607f5e2 的 `b[r4+1]==4` 臂）与 IRQ handler——两者
不在当前执行前缀（§4）；容器放行的运行读只剩 RMW 内部读，判据与
SRCSYNC AM 族一致（读值只进写回组装）。

## 4. 调用链与回调结构（call 目标=指令末尾+有符号字节偏移，python 换算；codex 独立扫描同果）

`audio_sync 库 ← audio_sync 节点 adapter（0x607e376-0x607f7xx）← stream
框架`：

- **creator 唯一直接调用者 0x607e4f8**，在函数 0x607e488 内（节点 open：
  zalloc 204B 配置节点 → rep 逐字拷贝〔非显式 memcpy，codex 勘误〕→
  检查 → `r0=(b[cfgr+4]==2)` → creator → [node+116+40]=obj → NULL 检查 →
  fn_register（0x607e504）→ 0x607e50e **call 0x60bf030 回调安装** →
  注册回调 → 返回）；0x607e488 唯一调用者 0x6077f9e（stream 框架）。
- fn_register/fn_config/fn_stop/fn_pause/fn_resume/fn_clear_b22 直接
  调用点集中 **0x607e380-0x607ea8e**（adapter 域：open/close/pause/
  resume 桩）；fn_config 另有库内 0x60bf00c（creator）。
- getter×7 直接调用点集中 **0x607f604-0x607f696**（分发函数 0x607f5e2：
  入口先验 cmd `r5==1`，再 `b[node+1]==4` 臂逐个调 get_OFFEND/
  get_DASPACE/对象 getter/get_SLOT_MIX/get_BTTIME×2/get_BTTIME_calc
  ——sync 状态 ioctl 查询接口）。
- **回调槽结构（codex 发现，本路复核修正其地址）**：creator 成功路径
  经 0x607e50e 调 0x60bf030，把 **0x607f5f6**（0x607f5e2 分发函数的中段
  入口；codex 引文写作 0x607f5e2，li 实际值 101184994=0x607f5f6，差
  0x14——跳过参数装载的 thunk 入口）装进 sync 对象 [+36] 回调槽。
  fn_resume 与 IRQ handler 的 `call r2` 都可能经此槽进入 getter 分发域。
  **因此「getter 零调用者」只是直接调用意义上的：结构上经回调槽
  可达**；当前执行前缀未到达是运行事实（SRCSYNC 实测交接），静态
  不能替代运行排除。
- 全 app 无这些库函数的函数指针字面量（唯一 li 引用=creator 0x60beff8
  `li r2=0x60bf1e4` 注册 IRQ handler；0x60bf030 内的 0x607f5f6 是第二处
  函数地址 li，随回调安装走 RAM 槽不进 SFR）。

**旧档勘误（rand-to-park-map.md:141）**：「JL_SYNC 0xfe2500/0c/18 |
.data 0x60bee74.. | 读者 0x607aafa/0x607ae22/0x607b10e（DSP handler 域）」
两处不成立（codex 复核佐证）：① 0x60bee74 是 **.text**——它是 DSP
handler 0x607ae28 装载的函数指针表（表项 0x60738e4/0x60739f6/0x6073a9c…），
li 立即数字节被旧档误当 .data 字面量；JL_SYNC 全 app 零 .data 字面量
（§1）。② 三个地址是 DSP 数据搬运 handler 入口，不直接读 JL_SYNC；
真正的 JL_SYNC 消费者是 0x607e376-0x607f7xx 的 audio_sync 节点 adapter
（本节调用链钉死）——但「不直接读」不能扩大为「无间接依赖」（DSP
域经表调用与 adapter 域无共享路径的证据仅限本路扫描窗口）。

## 5. 三选一预案（codex 参谋定稿：收窄版 a）

| 方案 | 裁决 | 依据 |
|---|---|---|
| **(a) jl_sync_config_words 四字容器（收窄版）** | **采用** | 四字（CON/CON1/BT_OFFSET/MIN_SLOT）配置事务有静态依据（§2/§3）。**与 SRCSYNC 的关键差异：CON 不设合成初值**——当前链首个访问就是 creator 整字写 0，无初值读需求；首写前读拒绝（未写字读=停，BATCHA 11 字拒读先例），写后读回=最后写值，RMW 内部读走 probe_boot 通用 mmio 路径（mem.read→hardware.read→mem.write→hardware.write）。WO 三字纯写记录读一律拒绝（含 RMW 内部读）。可选强化：消费者 PC 守卫——IRQ 读点 0x60bf1f6/0x60bf25e 与 getter 读点 0x60bf19c 的 CON 读在消费前拒绝（它们拿 CON 决定控制流），pause/resume/stop 到达时另行纳入 |
| (a+) 四字+0xF00130/40 timer 写记录 | **退回（原案地址集错误）** | 初稿的 0xF00144/54 写、0xF00158 读**全部错误**（实际写口 0xF00130/0xF00140、读口 0xF00144/0xF00148，§6）；纠正后仍须另证写入副作用（timer 启动？）如何处理，「BT clock timer 配置」语义认证不足——下路单独放行 |
| (b) RO 状态字具名 fixture（GPCNT NUM/DAC_TM0 先例） | 当前不引入 | 第一障碍是写配置，无状态测量读需求；DASPACE/CON 状态位/DABUF/OUTSR 到真实读停点再议（且 CON 状态位属 (a) 的 PC 守卫范围） |
| (c) 停 | 否 | CON 整字写 0 是明确的条件记录推进；不确定性足以用范围限制表达 |

**本次续跑预期账本**（静态预测，未运行验证）：仅开 (a) → **CON 一次写
0、零读；WO 三字零写零读**（fn_config 未到达）；停 **42,061,359 @
0x60befee 写 0xF00130=0x2F4**。四字一次建好的理由：下路跨过 0xF001xx
后 fn_config 无需再改模型；执行断言按一字收口，不宣称整个配置事务
已跑通。

**负例矩阵（codex 12 项，落地路照建）**：①开关关=旧停点步数/PC/地址/
宽度/值逐键复现；②地址换算错位——0xF00130/40/44/48 各访问均不承接
（将来扩展仅接受独立批准地址，不按连续区间映射）；③隐式补零——CON
未写先读/快照恢复丢写历史→严格停；④其余 9 成员（尤其 DABUF/DASPACE/
OUTSR/CON2）读写不被吞；⑤WO 三字显式读与 RMW 内部读均拒绝（写过也
拒绝）；⑥读回进状态分支——IRQ 两读点+SLOT_MIX 读点+未知 PC+另一核
→消费前停；⑦子字/错位/跨成员访问拒绝且不部分提交（并行指令原子性）；
⑧配置分支完备——链表空/非空、b[obj+4]∈{1,≠1}、b[obj+6]∈{0,≠0}，路径
不因 CON 值改变；⑨保留位精确——非零哨兵值跑真实配置段，两组装分支
按掩码 0xE9DFC000/0xF9DEFFFF/0x06210000 精确核值；⑩整字写不制造读
依赖（记账无读）；⑪无状态副作用合成——写配置后 IRQ88/DASPACE/OUTSR/
BTTIME 无 pending/计时增长/DMA 进度；⑫快照写前/写后/RMW 前后保存恢复
一致（值、已写标志、计数、守卫）。

## 6. 下一停点预判与 BT clock timer 域材料（0xF00130 系，供下路）

放行 (a) 后的执行序与墙序（creator 剩余体逐指令）：

1. 0x60befe6 st 完成（+1 步）→ 0x60befe8 `li r0=0xF00130`（+1 步）
2. **`0x60befee [r0+0] = 0x2F4`（st.w.modimm，值 756）← 下一停点
   42,061,359**（距当前停点 +2 步；0xF00130 未建模——br28_model.py 无
   0xf0 域模型：P11WindowRegisters=0xf2a000 系、SoftwareIrqCommands=
   0xffe1a0 系、0xF00000 modem 窗未建模）
3. `0x60beff6 [r0+16] = 1754`（0x6DA，**0xF00140**）——第二道墙
4. **request_irq(88,2,0,0x60bf1e4)**（0x6065d86，静态收窄 codex 完成、
   本路复核）：向量表 RAM `[0x1A0060+88*4=0x1A01C0]=handler`（纯 RAM
   写；codex 引文「0x19ff60」系其基址口误，有效地址 0x1A01C0 本路
   复核）；**0xffe12c 读改写**（ICU IRQ 配置：清 bit16-19 段后写
   `((2<<1)&0xE)|1=5`——使能+优先级 2）。0xffe12c 在 br28_model.py 的
   IRQ 配置分发范围内但**旧配置字是否已知决定 RMW 读是否停——静态
   无解，运行验证**——第三道墙候选
5. fn_config 四字（容器内）+ 条件臂 `b[obj+6]==0 → 0xF00000 |= 0x400`
   （modem 窗 CON，BTMAP 已划入 BT 域影子读窗口——若触发即新停点；
   b[obj+6]=创建参数=(b[cfgr+4]==2)，运行时值未观测）
6. 链表插入/回调安装/exit crit/返回 → adapter 0x607e4fe 继续走

**0xF00130 域档案（供下路）**：写面恰两字（0xF00130=0x2F4、0xF00140=
1754，全 app 唯一站点=creator 0x60befe8/0x60beff6）；读面 0xF00144/
0xF00148（get_BTTIME_calc 0x60bf1c4 起 `r2=[r1+4]; r1=[r1+0]`、IRQ
handler 0x60bf216 起同两字）——形状=**BT clock 定时器**（配置字+计数
读回；与 BTTIME/MIN_SLOT/slot 对齐语义吻合，derived-not-measured）；
语义无 SDK 证据（公开 SDK 三源无 audio_sync 驱动源，闭源库），固件
驱动反汇编即第一手证据。0xF00000|=0x400 单点（fn_config/fn_resume
条件臂，modem 窗，BTMAP §影子读同址）。

eq 设计族 copex 站点（0x6072bb8 mode16/0x6072d1a mode24）仍在更下游
未到达（SRCSYNC 预判维持）。

## 7. 诚实边界与开放项

- 「读值只进写回组装」是**静态**审计结论（窗口内全部 CON 读点通读）：
  不证明运行时无新消费者；IRQ88 触发/ioctl getter 激活/其他核访问须按
  新停点重审（LPV/ADA 措辞）。codex 建议：落地后补 getter/IRQ handler
  的 watchpoint 命中计数，区分「未到达/守卫拒绝/已执行」。
- **「getter 不在当前前缀」与「IRQ88 不触发」是运行事实引用（SRCSYNC
  实测交接）**，非本路独立重放；注册 IRQ88≠证明硬件无源（仿真无 JL_SYNC
  源模型与「硅上无活动」是两回事，USB IRQ29 同款边界）。
- probe 语义（链表空检查）、creator 参数（=(b[cfgr+4]==2)）、回调目标
  0x607f5f6 的 thunk 结构从反汇编形状归纳，derived；b[cfgr+4]/b[obj+6]
  运行时值未观测（fn_config 的 0xF00000|=0x400 条件臂走向运行时才知）。
- fn_stop 尾 88-call（0x60bf072→0x6066012）注销/使能方向 [推断]；未深挖。
- 下一停点 **42,061,359 @ 0x60befee** 为静态预测（步数口径=completed_
  steps），未续跑测得；0xffe12c RMW 是否停取决于运行时旧配置字。
- OFFSUM/PCMNUM/INSR「零访问」限主核 app 线性窗（§1）；本路未运行任何
  场景（红线），步数/停点形状均引用 SRCSYNC 实测交接。

## 8. codex 参谋记录

gpt-6-astra，2026-11-19 01:06-01:13，
`~/tmp/codex/202611190113-jlsync-scout/`（briefing.md/final.md/log.md；
仅只读命令，未改文件未跑仿真；独立复核 384 条反汇编记录与 app.bin
字节一致）。裁决要点：「选收窄版 (a)。不接受原样 (a+)；当前不需要
(b)，也没有必要整路停在 (c)。」三个实质修正（本路逐条 python 复核
成立）：①**下一停点地址/步数勘误**——创建器 timer 写口是 0xF00130/
0xF00140（非初稿 0xF00144/54），读口 0xF00144/48（非 0xF00158），
下一停点 +2 步非 +4 步；②**fn_register 非纯链表插入**（真插入=
creator→0x60735d8）+ 0x60bf030 回调安装活代码（初稿误标死代码）；
③**CON 取消合成初值**（首访问即写，首写前读拒绝比 SRCSYNC 式零初值
更诚实）。另纠正初稿三处数字（272.0f、0x06210000、0xF9DEFFFF）。
codex 自身两处引文本路复核修正：回调目标实为 0x607f5f6（其引文
0x607f5e2 差 0x14）；request_irq 向量槽有效地址 0x1A01C0（其引文
0x19ff60 系基址误），0xffe12c ICU 配置结论不受影响。负例矩阵 12 项
（§5）与预期账本（本次续跑仅 CON 一写零读）均出自其定稿。
