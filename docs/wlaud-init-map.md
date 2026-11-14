# 0x602ea68 init 函数全图：WL_AUD 写族 + 音频域推进前置地图（路 AG / sim-wlaudmap，只读预研）

2026-11-14。基线 worktree `d02b7d9`（路 AE 合并后）。本页服务下一前沿实施路：当前条件前沿
**3,079,241 步 @ 0x602ea72**（p11_heap_experimental_config，写 0xfe2200=JL_WL_AUD->CON0=1，
initcall 表第 4 项 0x602ea68 的首条 SFR 写）之后的完整静态地图——函数全图、写集枚举、
停点预测、读回/轮询风险、三选一预案，照 [docs/frontier-lookahead.md](frontier-lookahead.md)
（路 U）的模式；音频域寄存器语义沿用 [docs/audio-input-paths.md](audio-input-paths.md)
（路 AE）的钉死结果。

**只读**：未改 tools/ 任何代码、未新增/修改任何投影场景。反汇编用仓库钉死的 pi32v2-isa
codec（与 vendor 线性反汇编 ~/tmp/sim-wla-disasm/app-linear.asm 逐指令互证过关键站点）。
一次性脚本在 `~/tmp/ag-wlaudmap/`（不进仓）。另做了一次**现有场景的只读复现运行**
（§0.1，观察停点机器状态，零工具改动）。

证据强度：**[静态]** = 指令锚定反汇编（本路，H3S 固件真字节，加载基址 0x06000100，
app.bin sha `18f3d573…` 与路 AE 同源）；**[SDK]** = br28.h（sha256 `111e29ea…`，与
data/dependencies.json 钉死值一致）；**[模型]** = tools/probe_irq_entry.py / br28_model.py
源码阅读；**[动态复现]** = §0.1 的停点观察（仍是模型行为，非真机）。静态结论全部
derived-not-measured。

## 0.1 动态复现运行（现有场景，零改动，观察 only）

用临时驱动脚本 import `tools/probe_irq_entry.py`，以 main() 里 p11_heap_experimental_config
的**原样 kwargs** 调 `irq_case(...)`，仅在 explore 的 post_stop 回调里读机器状态（不改任何
投影/场景语义；该跑为一次性 scratch，报告不入库）。复现结果与 HANDOFF 逐字段一致：
**3,079,241 步 @ 0x602ea72，unmapped_or_readonly_write 0xfe2200=1**。

停点时刻的关键机器状态（[动态复现]，全部 derived-not-measured）：

| 观察项 | 值 | 含义 |
|---|---|---|
| word @0x127364（QCY+3460） | **0x00000000** | **G6 门 `(r10&3)==0` 成立 → esco mic 块整段跳过**（§2 G6） |
| b[0x127368]（QCY+3464） | 0x00 | 内层门 `==2` 也不成立（双保险） |
| b[0x127811]（QCY+4673） | 0x00 | audio_adc_init 的 guard 未置 → 函数体执行（RAM-only） |
| b[0x12a008] | 0x00 | 0x600a590 喂狗走 keys+CON 路径（不走 0x12cc7e 邮箱路径） |
| hardware.registers 里 0xfe0xxx/0xfe2xxx | **空** | 整个音频 SFR 域（fe0/fe2）零先验状态——本函数每个 fe0xxx 访问都是首触 |
| 0xfd1924（WLA_CON9/OSC_CONFIG） | 0xd400 | 已建模振荡器配置域当前值（§3 判定通过） |
| fd31 ADC：CON 写 406 次/读 258/PND 强制 80 | res_served {0x10000:56, 0x10006:12, 0x10004:12}；P3_ANA_CON4(0xf2c240)=0x8→mux=4 | GPADC 域已大量走过；CON 历史含 0x20000 通道事务（=mic 偏置通道字，此前世界已用同族 fixture 放行） |
| 0x1267BC hdl / 0x12657C 场景标志 / QCY+524 cfg | 全 0 | 本函数尚未写它们（符合"停在首指令"） |

注：复现运行从主仓 tools（HEAD `b24f967`）加载——worktree 缺 gitignored 的
reference-cache（模拟器缓存）；停点三字段（步数/PC/kind+值）与 worktree 基线记录完全
一致，场景栈等价由此互证。

## 1. 一句话结论

0x602ea68 是** boot 期音频框架 init**（698 条指令 / 87 个 call / 2282 字节，单出口），
WL_AUD 使能写在**头尾各一次**（entry 0x602ea72 与 DAC init 0x6071bca 内部 0x6071cbe），
IRQ68 注册在其间（0x602ec28）；**默认路径**（G6 门字=0，[动态复现]钉死）上从当前停点到
函数返回**只有 4 个新 SFR 寄存器、7 个站点**（JL_WL_AUD CON0 ×2 整字写、JL_AUDIO DAC_CON
×3、AUD_CON ×1、JL_ASS CLK_CON ×1 RMW），**无任何轮询/读回状态机**；其余 ~680 条指令
全 RAM/已建模域。建议**一次打包放行**（写记录 + RMW 读回=最后写值容器），预期前沿一次
滑过整个函数 + 后续 2 个 RAM initcall，落到更远的事件域边界。

## 2. 函数全图（四问之一）

### 2.1 规模与结构 [静态]

- **范围**：0x602ea68..0x602f351（入口 `[--sp]={rets,r13-r4}` + `sp+=-32`，出口 0x602f34e
  `sp+=32` + 0x602f350 `{pc,r13-r4}=[sp++]`；**单出口、无早退**）。
- **698 条指令、87 个 call、16 个条件/goto、5 个循环**（bias 有界轮询、memset 走走、
  ch_map 位循环 ×4、mic 槽循环 4×9B、列表 rep 初始化）。调用树深度 4 层
  （0x602ea68→0x601bd3a→0x606ee26→0x606ebe0→0x606c844）。
- 紧邻的 0x602f352 / 0x602f37c 是两个独立小函数（audio 关闭/暂停 helper，
  b[QCY+512/1428/1436] 门控），0x602f406 起是 IRQ68 分发器（AE §2 已钉）——都不在本次执行路径上
  （IRQ68 只有交付才进）。

### 2.2 initcall 上下文 [静态]

表 0x60c32a4..0x60c32b8（HANDOFF 钉的 6 项）：{0x6012e3e, 0x601321e, 0x6013246,
**0x602ea68**, 0x60614a2, 0x60009e8}。前三项已执行过；**后续两项 0x60614a2 / 0x60009e8
经扫描均纯 RAM**（zalloc/ROM memset/dev 层），表尾 0x60c32d8=0x00000000 终结。表区再往后
0x60c32bc..（0x6022598、0x602ea60→thunk 0x6017ce2、0x6034c92、0x6060a4a…）是否同表
续段属 AE §5.6 已记的 walker 边界未钉死项，本页不扩。

### 2.3 执行主干与门（按序）[静态+动态复现]

```
0x602ea68 entry: [--sp]={rets,r13-r4}; sp+=-32
0x602ea6c  r0=0xfe2200
0x602ea72  [0xfe2200]=1                      ← 当前停点（WL_AUD CON0 使能，先于一切音频初始化）
0x602ea76..0x602eb0a  5× 场景 mic 需求标志：0x60755b2(0x7674/0xD96F/0xB9D1/0x9C2D/0xDC74)
                      → b[QCY+468/472/474/476/480]（AE §1.3 钉的读取器；RAM/flash）
0x602eb0c  0x60119ca（config×4 + ROM memset；RAM）
0x602eb10  0x60225e6 →（内部 0x6011fd8 读 [0xf2c064]=P3_ANA_READ——场景旗标
           p3_ana_read_ldoin_low 已放行 [模型]）
G1 0x602eb1a  if (0x60225e6()==0) 跳过 config-89 预取（两侧均 RAM）
0x602eb26  get_config(89,sp,1)（==1 → b[QCY+528]=1）；get_config(91,sp+3,1)
G2 0x602eb50  if (ret==1) { b[QCY+41]=cfg&~3; b[QCY+532]=1 }   ; key 91 已占用（config-key-map）→ 走此侧
0x602eb78  b[0x1264D9]=…（mic 通道数字节）
0x602eb80  0x60719a6 audio_adc_init：guard b[QCY+4673]=0[动态复现]→体执行，
           纯 RAM（hdl 0x1267BC 初始化 + 槽 [QCY+308<<2] + 0x6064162）
           ※扫描器曾把 0x60719b8 的死 li 0xfe0538 误判为写——r3 随即被 hdl 指针覆盖，已勘误
0x602eb86  0x6014162（stream 框架节点 walker 0x60750aa/0x60751da；RAM）
0x602eb8a..0x602eb9c  输出 handler 链表初始化（rep 2 8）
0x602eb9e  0x6064162（dev 层）
G3 0x602eba4..0x602ebc0  5 场景标志 OR → b[0x1264A0+584]（仅 RAM 旗标，不门任何调用）
0x602ebce  [r9+588]=96000
0x602ebd8..0x602ebe2  0x6071bca(QCY+7464, QCY+508)   ← 音频框架/DAC init（★SFR 主簇，§3）
0x602ebe8  get_config(58,sp+11,1)（key 58 已占用）
0x602ebfe  get_config(88,sp+12,4)
G4 0x602ec02  if (ret!=4) { b[QCY+512]=1; call 0x6071d62 }   ; key 88=4B 已占用 → 返回 4 →
           **默认跳过 0x6071d62**（DAA_CON0/0xfe0a0c RMW 电源下链条不执行）
0x602ec1c..0x602ec28  request_irq(68, prio2, 0x602f406)   ← IRQ68 注册（RAM：向量槽
           0x19FF10←0x602f406 + IPMASK，BE 路 IRQ 机制域）
0x602ec2e  get_config(92,sp+4,6)
G5 0x602ec3e  if (ret==6) 跳过；else { 0x6071e74(默认 DAC 配置写族) + set_config(92) }
           ; key 92=6B 已占用 → 返回 6 → **默认跳过 0x6071e74**（DAC_LEN/ADR/HRP/PNS/CON 写族不执行）
G6 0x602ec5e  r10 = word[QCY+3460]（0x127364）
0x602ec68  if ((r10&3)==0) goto 0x602ee9a    ← **[动态复现] r10=0 → 整个 esco mic 块跳过**
           （块内还有内层门 b[QCY+3464]==2；块内容见 §3.3 条件族——ADA 上电/偏置轮询/
            0x60142be esco open/set_buffer 五写/GPIO 六连/喂狗）
0x602ee9e..0x602f34c  音频框架尾块（★纯 RAM 为主：见 §3.2；唯一条件 SFR 族 = 0x601bd3a
           的 ANC 图门，见 §3.3）
0x602f34e..0x602f350  epilogue，返回 initcall walker → 下一项 0x60614a2（RAM）
```

### 2.4 0x6071bca（音频框架/DAC init，★SFR 主簇）[静态]

体：ROM memset(296) → 对象/SR 默认填充（mode3 时 zalloc(128)，否则 8000Hz 默认）→
SR 表 [0x60A0800+idx]（r10 = idx≤5 ? 1 : 0）→ SFR 序列 → 0x6071ab8(0)（RAM 关闭旗标）→
0x6064162×3 → 时长预算计算 + 0x6065c3a（调度器助手，CG 域已建模）→ ROM memset。
SFR 序列（与 vendor 线性反汇编互证，§6 锚点）：

```
0x6071cb2 r0 = 0xfe0500 (JL_AUDIO 基址)
0x6071cb8 r1 = r0+0x1D00 = 0xfe2200
0x6071cbc [0xfe0500] = 0xfe2200          ; DAC_CON 整字写，值恰=WL_AUD 基址（位模式语义未钉死：
                                         ;  bits{9,13,17..23}；SDK 无字段名；疑 DAC 时钟/源选择族）
0x6071cbe [0xfe2200] = r10               ; WL_AUD CON0 第二次写（0/1，随 SR idx）
0x6071cc2 r1 = 0xfe0900 (JL_ASS 基址)
0x6071cc6 [0xfe0900] |= 0x1              ; JL_ASS CLK_CON 原子 RMW（音频子系统时钟位0）
0x6071cca r1 = 0xfd1924 (JL_WLA CON9 = OSC_CONFIG)
0x6071cd0..0x6071cd8  read; &0xFFF9FFFF; |0x20000; write   ; 2bit 字段→01（振荡器配置域）
0x6071cda [0xfd1924] |= 0x80000          ; 同域原子 RMW
0x6071cde r1 = 0xfd1924-0x1904 = 0xfd0020
0x6071ce2 [0xfd0020] &= 0xFF3FFFFF       ; JL_CLOCK CLK_CON2 原子 RMW
0x6071ce6 [0xfe0500] &= ~0x100           ; DAC_CON RMW
0x6071cea [0xfe0558] &= ~0x40            ; AUD_CON RMW（0xfe0558 = JL_AUDIO+0x58）
0x6071cee [0xfe0500] |= 0x100            ; DAC_CON RMW（bit8 开/关对）
```

## 3. 写集枚举 + 停点预测（四问之二）

> **路 AK 勘误（2026-11-14 实施后）**：本表对 0x602ea76..0x602ebb4 段的「通过/无停点」判定
> 只覆盖了 SFR 访问与 RAM 性，未覆盖 **st.d×MMU 页交互**——实施路放行 S0 后实测停在
> 0x6007af2 `st.d.sp.l d[sp+8]`（场景 walker 链 0x60755b2→0x6074234→0x6007ae8 查名
> helper 函数头）跨 128B MMU 页界 0x400afc（mmu_cross_page_access_not_split，全 boot
> 首例），S1..S5 本前缀未到达（详见 HANDOFF 路 AK 条目）。另 §5.1 的 S4 RMW 推导链
> 「0xfe2200→0xfe2000→0xfe2100」算术有误：0x2200 的 bit8 本为 0，实测链
> 0xfe2200→0xfe2200→0xfe2300。

> **路 AN 勘误补充（2026-11-14 跨页拆分实施后）**：§3.1 该段对 5× 场景 walker 的执行序
> 预期也漏了实际先经过的 **0x60719a6 ADC 节点 reset**（设备结构 0x1265C0 族，配置门
> b[0x1267E1]==0 分支）：跨页 st.d 拆分放行（opt-in `mmu_cross_page_split`，derived-
> not-measured）后实测停在 0x60719c0 并行对首写 **0xfe0538**（JL_AUDIO+0x38 ADC_CON，
> 路 AE 钉死命名）——本图 §5 写集**范围边界明确不进白名单的条件族首位**（esco/ADC 族），
> S1..S5 仍在其后未到达。停点本身诚实、归下一路三选一；本图对该段的执行序按实测修正。

> **路 AS 勘误+结果（2026-11-14 ADC reset 放行实施后）**：三处按实测修正——
> ①§2.3/AN 勘误里的门地址 **b[0x1267E1] 是手算笔误**：指令编码钉死 guard =
> b[0x1265C0+0x1241] = **b[0x127801]**（放行后动态互证：reset 完成置 1、hdl=0x1277B4、
> 槽 [0x126A90]←hdl 全落盘）；②§2.4 的 S1 解码 **「[0xfe0500]=0xfe2200 数值巧合」是
> 并行对旧寄存器误读**（与 0x60719a6 死 li 误判同族盲区）：0x6071cb8
> `add.s14.rri.p r1=r0+0x1D00` ∥ 0x6071cbc `st.w [r0+0]=r1` 的存储半读**旧 r1**（SR
> 表 walk 值，实测落盘 **0x0**），0xfe2200 是并行对首半算出的 **S2 写基址**；③§3.3(c)
> 把 0xfe0a00/0xfe0a0c 归入「缺省分支条件族」不误，但同族 **0xfe0a04（JL_ADDA->
> DAA_CON1，+0x04 __RW）实测在默认路径被读**（0x606b426，0x606b40e 配置 RMW insert
> 函数经 veneer 0x606db6c 调入；读→旧值>>3 入 bits3..31+flash 表 0x060A8548 位段→写回）
> ——**音频尾段首个非掩码平凡 RMW 读**（读值实质参与写回、本世界该字零写）= 首个真实
> 硬件反馈依赖。路 AS 放行（`jl_audio_adc_reset_writes` 域三开关）后 S1..S5 全落地、
> initcall 尾块全过（Timer0 一次到期落 ROM rep 体内、指令中途延期先例新设、块尾交付），
> **新前沿 3,170,362 @ 0x606b426**（unmapped_memory 读 0xfe0a04，归下一路三选一）。

### 3.1 默认路径（G4/G5/G6 全走缺省/跳过侧，[动态复现]钉死 G6）主路径 SFR 站点全表

从当前停点起，按执行序（"判定"列 = 假设前一停点按 §5 预案放行后的模型行为）：

| # | 站点 | 地址 | SDK 名 [SDK] | 形状/值 | 模型现状 | 判定 |
|---|---|---|---|---|---|---|
| S0 | 0x602ea72 | 0xfe2200 | **JL_WL_AUD->CON0**（0x22 区段**单成员** __RW，br28.h L412-418） | 整字写 1 | unmapped | **当前停点** |
| — | 0x602ea76..0x602ebb4 | — | 场景标志/config/audio_adc_init/框架 walker | RAM+flash+已建模域（0xf2c064 读在 p3_ana_read_ldoin_low 域） | 通过 | 无停点 |
| S1 | 0x6071cbc | 0xfe0500 | **JL_AUDIO->DAC_CON**（__RW，编译布局 4 字槽，AE §2.1） | 整字写 **0xfe2200** | unmapped | **预期停点 1** |
| S2 | 0x6071cbe | 0xfe2200 | JL_WL_AUD->CON0 | 整字写 r10(0/1) | S0 域 | S0 放行即过 |
| S3 | 0x6071cc6 | 0xfe0900 | **JL_ASS->CLK_CON**（__RW，JL_ASS 三成员之首） | 原子 RMW \|=1（**内部读**） | unmapped | **预期停点 2** |
| — | 0x6071cd0..0x6071cda | 0xfd1924 | JL_WLA->WLA_CON9（OSC_CONFIG[1]） | RMW &0xFFF9FFFF \|0x20000；\|=0x80000 | oscillator_configuration_projection（[动态复现] 当前值 0xd400） | **通过** |
| — | 0x6071ce2 | 0xfd0020 | JL_CLOCK->CLK_CON2（基模型 registers 槽，初值 0） | 原子 RMW &=0xFF3FFFFF | registers | **通过** |
| S4 | 0x6071ce6 | 0xfe0500 | DAC_CON | 原子 RMW &=~0x100（内部读） | S1 域若只做写记录→读停 | **预期停点 3** |
| S5 | 0x6071cea | 0xfe0558 | **JL_AUDIO->AUD_CON**（+0x58 __RW） | 原子 RMW &=~0x40（内部读，**无先前写**） | unmapped | **预期停点 4** |
| — | 0x6071cee | 0xfe0500 | DAC_CON | 原子 RMW \|=0x100 | S4 放行即过 | 通过 |
| — | 0x6071cf4..0x6071d60 | — | 0x6071ab8(0)/0x6064162×3/0x6065c3a/ROM memset | RAM+已建模（GPCNT/DCCK/ICFG 消费链同前） | 通过 | 无停点 |
| — | 0x602ec28 | 0x19FF10/IPMASK | IRQ68 注册（prio2, handler 0x602f406） | RAM 写 | BE 机制 | 通过（零 pending 零交付，同 IRQ12 先例） |
| — | 0x602ec3e | — | G5 跳过（config 92 存在） | — | — | 0x6071e74 不执行 |
| — | 0x602ec68 | — | **G6 跳过（r10=0 [动态复现]）**→0x602ee9a | — | — | esco 块不执行 |
| — | 0x602ee9e..0x602f34c | — | 框架尾块（zalloc 572/60/400/212/960/28/52/64、get_config 59/61/183、0x6072306/0x6072312/0x6072316/0x607232c/0x607234e、0x601c958、memset 走走 0x601d15a、0x607542a、0x601bdc8 族、0x600c092、0x601c82e、0x606a92c、0x6067242、0x6020042、结构默认大填充） | 全 RAM（已逐 callee 扫描，§6） | 通过 | 无停点（唯一例外=§3.3 ANC 图门） |
| 尾 | 0x602f34e | — | 返回；后续 initcall 0x60614a2/0x60009e8 均 RAM | — | 通过 | 函数结束，前沿进入 initcall 表尾之后的域 |

步数量级：S0→S1 约 **10³–10⁴ 步**（5×场景表 walker + config×4 + 描述符 walk + ROM memset
296 字）；S1→S3/S4/S5 各 **+个位数指令**（同一函数体内连续站点）；S5→函数返回约
**10³–10⁴ 步**（框架尾块 + config 59 的 1367B 读 + memset 走走）。即：**放行 4 个停点后
整个函数一次通过，下一个真边界在 initcall 表尾之后**（事件域/D1 域，运行时才定形状）。

**写集计数**：WL_AUD 族 **2** 写（本函数直接）；音频域 0xfe05xx/0xfe09xx **5** 站点
（DAC_CON×3、AUD_CON×1、CLK_CON×1）；顺手经过的已建模域 3 族（0xfd1924×2、0xfd0020×1、
P3_ANA_READ×1）；其余 80+ call 站点纯 RAM。条件族（§3.3）另计 ~40 站点。

### 3.2 框架尾块为何纯 RAM（逐 callee 核过）[静态]

0x6072306（4×半字拷贝）、0x6072312（[obj+32]=handler，1 条）、0x6072316/0x607232c（链表
init+zalloc）、0x607234e（**注意：不是** DAC 读族——它只 0x6064162+b[QCY+1276]=1；
真正的 DAC 缓冲读族在 0x607236c，其 caller=0x607249c/0x6073258，**不在本函数调用面内**，
tone/PCM 推送才触达）、0x601c958（0x6007ae8/0x600ae2e/ROM/0x6000f0c）、0x601d15a
（memset+cache 同步族；0x601d198 起的 uart_is_idle 是相邻函数不是它）、0x607542a、
0x601bdc8（0x606c54e/0x606e92a/0x600c406/0x600db3c 全 RAM；**0x601bd3a 见 §3.3**）、
0x600c092、0x601c82e、0x606a92c、0x6067242（设备表 open：ROM strcmp 0x1fd84 +
0x60650a0 族；**失败分支** 0x60671d8→0x12cc40 canary——见 §4）、0x6020042、zalloc 族
（0x12e16e/0x12e160/0x12e190）、get_config/set_config（VM 域）。

### 3.3 条件族（本世界默认不可达，供后续场景对号入座）

**(a) esco mic 块**（G6 `(word@0x127364&3)!=0` 且 `b[QCY+3464]==2`；[动态复现] 两者均假）：
GPIO 六连 RMW（0xfd5008 族，gpio 容器 ✓ 已覆盖）、JL_ASS [0xfe0900] \|=0x200 /
&=~0x200、偏置电阻 [0xfe0924]（JL_ASS+0x24，**SDK 无名**）insert RMW ×≤19、
GPADC 通道 on/off（0x6001230/0x6001cc0：RAM 表 + gpio 容器）、偏置电压轮询（§4.2）、
0x60142be esco open（→0x606de92 [0xfe0a40]=ADA_CON8 RMW×4、0x606c3aa PGA insert、
0x606b4d0 ADA 上电）、0x606dee0 SR 写（**读** [0xfe0538]=ADC_CON）、0x606dee4 set_buffer
五写（ADR/LEN/PNS/HWP=0/SRP；bit4 查重又读 ADC_CON）、0x606df3a CON 回写、喂狗
0x600a590。≈25 个 SFR 站点 + 2 个读回依赖。

**(b) ANC 图门**（0x601bd3a：[QCY+464]!=0 且图节点 h[r6]&0xF0==0xA0 且 b[r8+1]!=1——
运行时图配置决定，静态不可钉）：0x606ee26→0x606e9e8/0x606ea12（**JL_ANC->SYS
[0xfe0400] RMW 读+位段 insert 族**）、0x606ebe0（anc_core init：request_irq(67) +
0x606c844 JL_ASS CLK_CON 使能串 + 0x606c8aa MIC_CTL1 + 0x606aaf4/0x606a7dc DAC 族）。
≈15 个 SFR 站点。属 D2-b1 打开链/ANC 域的活，不归本路。

**(c) 缺省分支**（G4/G5 若 config 88/92 缺失才走；key-map 钉两 key 均占用 → 默认不走）：
0x6071d62（[0xfe0a00] DAA_CON0 \|=0x20000000/0x40000000 + [0xfe0a0c] DAA_CON3 RMW 链——
多个读回依赖）、0x6071e74（DAC_LEN/ADR/HRP/PNS 整字写 + DAC_CON \|=0x110 +
0x606ab0a DAC 模拟族：[0xfe0558] \|=0x100、[0xfe0534]=DAC_COP 0x7F、[0xfe0500] 读+RMW 族）。

## 4. 读回/轮询风险清单（四问之三）

| # | 风险点 | 形状 | 判定 |
|---|---|---|---|
| 1 | **WL_AUD CON0 读回** | app 全量 li 扫描：写 0x602ea72/0x6071cbe、RMW \|=1 @0x60552ec（WL 域上电路径）、**读仅 1 处** 0x6069a2e（`r0=[0xfe2200]; [r7+4]=r0` —— 电源管理**快照保存**族，同函数还存 [0xfe0900]）。**无任何循环读/ready 位轮询** | 无轮询风险；快照读属远期低功耗域（0x6069a28 族），到时再按读回=最后写值处理 |
| 2 | 偏置电压轮询 0x602edba..0x602ede8（esco 块内） | `if (r4>18) 退出` + `电压≤1499 再来`——**有界 ≤19 次**；且 0x6011dd2 读的是 **RAM 缓存表**（0x6000e74 表查找），不直接触 SFR；0x20000 通道的 GPADC 事务在 fd31 域已放行（[动态复现] CON 历史含 0x20000 事务、mux=4→档 400） | 无死循环风险；且本世界 G6=0 整块不执行 |
| 3 | ADA 校准/等待 | esco 块上电序列（0x606b5b0 族 ADDA RMW）只有 RMW 读依赖，无校准轮询循环（AE §1.1 的 0x20000 电压等待就是 #2 那个有界循环） | 条件族，读回投影可解 |
| 4 | DAC 缓冲读回族 0x607236c | 读 [0xfe0504/08/14/18]（ADR/LEN/HRP/SWP/SWN）算可用空间——**不在本函数调用面内**（caller 0x607249c/0x6073258，tone/PCM 推送域）；届时本世界这些寄存器可能**从未写过**，「读回=最后写值」无写可回 → 需要显式初始值决策（同 #5 AUD_CON 问题，量级更大：LEN/ADR 是缓冲参数，0 初始意味着"满"还是"空"要按消费代码分支核） | 远期（tone 域），提前挂账 |
| 5 | AUD_CON 0xfe0558 RMW 读（S5） | 本路径无先前写 | 「读回=最后写值+显式合成初始 0」（路 AM P3_ANA_CON 先例）；&~0x40 于 0 得 0，平凡 |
| 6 | 0x6067242 设备 open 失败 → 0x60671d8 → 0x12cc40 canary（P3_VLVD_CON 0xf2c044 读，路 BV 域；场景旗标**无** p33_canary 投影） | 失败分支才可达（open 历史恒成功） | 预期不可达；若撞上按路 U §3.2 同款处理（canary 首读 fixture） |
| 7 | 0x600a590 喂狗分支：flag≠0 侧 → 0x12cc7e(8) = **P11 邮箱 post**（母本 0x60c6456：写 M2P 槽 0xF27F03/0xF27F47、[0xF2A618]=0x400、**轮询 [0xF2A60C] bit12** —— ack 永假即死循环） | [动态复现] flag=0 → 走 keys+CON \|=0x10000 喂狗路径：0x600a568 先写双 KEY（0x048c159d/0x26ae37bf，与 Watchdog.keys 逐字一致）再 [0xf2a800]\|=0x10000，**与 Watchdog 模型语义完全匹配，预期通过**；且本函数的喂狗调用点在 esco 块内（本世界不执行） | 低风险；0x12cc7e 邮箱路径如日后触达按 P11 消息域单独评估 |
| 8 | 0x60225e6→0x6011fd8 读 [0xf2c064]（P3_ANA_READ，W1 红线） | 单读非循环 | 场景已带 p3_ana_read_ldoin_low=True → 通过 |

## 5. 三选一预案 + 放行策略建议（四问之四）

### 5.1 逐停点预案

| 停点 | (a) 写记录/读回投影 | (b) 钉住 | (c) 具名 fixture | 建议 |
|---|---|---|---|---|
| S0+S2 0xfe2200 整字写×2 | write-only 整字记录（omap/imap/uart_init_writes 先例）——SDK 名齐（单成员 __RW），本路径零读消费 | 卡死整个函数首指令，后续全到不了 | 不适用（不是缺输入值） | **(a)** |
| S1/S4 0xfe0500 DAC_CON（整字写+RMW×2） | 写记录 + **读回=最后写值**（lp_timer CON 路 AL / UART CON1 路_AA 同族）：RMW 链 0xfe2200→0xfe2000→0xfe2100 全可从记录推导，零发明 | 同上劣 | 不适用 | **(a)**（读回是 RMW 的必要面） |
| S5 0xfe0558 AUD_CON RMW | 同上容器 + **显式合成初始字 0**（路 AM 先例，报告里具名假设） | 同上劣 | 0 本身就是具名假设的一部分 | **(a)** |
| S3 0xfe0900 JL_ASS CLK_CON RMW | write-only 位记录（IMAP 路 V 先例：原子 RMW 挂记录域，读侧不发明）**或**并入上面的读回容器（初始 0，\|=1→1） | 同上劣 | 不适用 | **(a)**，两种挂法取一（建议并入同一容器少一个开关） |
| 条件族（esco/ANC/缺省分支） | — | — | — | 本世界不可达，不做；留 D2-b1 打开链/ANC 路/远期按同模板 |

一句话理由：4 个寄存器全部是 br28.h **__RW 配置面**（时钟门/源选择/使能位），无状态机、
无轮询、无 pending 语义；写记录+读回=最后写值是最小发明假设，且 S1 的整字写让 DAC_CON
的 RMW 读值可推导、AUD_CON/CLK_CON 只需显式 0 初始（三条先例各管一摊，全有据可依）。

### 5.2 「一次放行整个 0x602ea68」vs「逐停点推进」

**建议一次打包**（单开关覆盖 {0xfe2200, 0xfe0500, 0xfe0558, 0xfe0900} 四地址族：
整字写/原子 RMW→写记录；读→最后写值、无写时显式合成 0；照 uart 三段先例 M→F→AA 的
最终形态一次做齐）。理由：

1. 默认路径只有 4 停点且 S1..S5 在同一函数体内**相距个位数指令**——逐停点推进=4 次
   「跑几十步就停」的回合，每次都要全量回归 3M 步场景，ROI 明显低；
2. 函数其余 ~680 条指令 + 后续 2 个 initcall 全 RAM，打包后一次回归即可验证到 initcall
   表尾之后的**真边界**（事件域，形状运行时才定），前沿含金量最高；
3. 风险面小：4 地址全部无读消费点在本路径（唯一读点 0x6069a2e 在远期低功耗域）、
   无轮询（§4），投影行为=「记录+可推导读回」，比 GPIO 容器还保守。

保守项如实记录：①0xfe2200=1 与 DAC_CON=0xfe2200 的**硬件副作用**（时钟门真开）不
建模——写记录语义=忽略副作用，与 omap 先例同级假设，维持 derived-not-measured；
②AUD_CON/CLK_CON 的合成初始 0 是显式假设（reset 态未实测）；③若实施路更求稳，可拆
两批：第一批 {0xfe2200 写记录 + 0xfe0500 写+读回}（覆盖 S0..S4，含唯一整字写），第二批
{0xfe0558/0xfe0900 RMW}——两批也是可接受的次优解。

预期前沿（一次打包后）：**滑过整个 0x602ea68 + 0x60614a2 + 0x60009e8**，落在 initcall
walker 返回后的下一个未建模访问——形状由运行时决定（候选：事件循环/更多 initcall 续段/
低功耗快照域），届时按路 U §5 事件域候选表对号入座。

## 6. 证据锚点索引（复核用）

| 结论 | 锚点 |
|---|---|
| 函数体 698 指令/87 call/2282B，单出口 | 0x602ea68..0x602f351 线性解码（codec pin 3608ed1）；出口 0x602f34e/0x602f350 |
| WL_AUD CON0 写×2 | 0x602ea6c-72；0x6071cb8-cbe（vendor 反汇编互证 ~/tmp/sim-wla-disasm/app-linear.asm L159069-72） |
| DAC_CON=0xfe2200 整字写 + 后续 RMW 对 | 0x6071cbc/ce6/cee；AUD_CON 0x6071cea；JL_ASS 0x6071cc6 |
| WLA_CON9/CLK_CON2 已建模 | br28_model.py L60 OSC_CONFIG 含 0xfd1924；L13-25 CLOCK0/SYS_DIV/DCCK；[动态复现] 0xfd1924=0xd400 |
| IRQ68 注册（prio2, 0x602f406） | 0x602ec1c..0x602ec28；向量槽 0x19FF10（AE §6 同锚） |
| initcall 表 6+7 项 | 数据字 0x60c32a4..0x60c32b8（0x6012e3e/0x601321e/0x6013246/0x602ea68/0x60614a2/0x60009e8）；后续项 RAM 扫描 0x60614a2/0x60009e8 |
| G6 门字=0、flag=0、fe0/fe2 域零先验 | [动态复现] §0.1（irq_case 原样 kwargs 复现 3,079,241@0x602ea72 后 post_stop 观察） |
| esco 块/ANC 门/缺省分支站点族 | §3.3 各锚（0x602ec68/0x602ec76；0x601bd4a/0x601bd6c；0x602ec02/0x602ec3e） |
| audio_adc_init 纯 RAM（勘误） | 0x60719a6..0x60719ee（r3 被 hdl 指针覆盖，死 li 0xfe0538） |
| 0x607234e ≠ DAC 读族；0x607236c 才是（不在调用面） | 0x607234e..0x607236a 体；0x607237a..0x60723d8 读；caller xref 0x607249c/0x6073258 |
| WL_AUD 全 app 4 站点、无轮询 | li 扫描：0x602ea6c（写）、0x6071cbe（写）、0x60552ec（\|=1，WL 域上电）、0x6069a2e（读快照）+0x6069c26（基址变体→0xfe0900 恢复写） |
| 喂狗 keys 与 Watchdog.keys 一致 | 0x600a568-0x600a586（0x048c159d+0x22222222=0x26AE37BF 双写）；br28_model.py WatchdogProjection.keys |
| 0x12cc7e=P11 邮箱 post+ack 轮询 | 母本 0x60c6456..0x60c648c（[0xF2A618]=0x400、轮询 [0xF2A60C]bit12） |
| JL_ASS/JL_AUDIO/JL_ADDA/JL_WLA 结构名 | br28.h（sha 111e29ea…）：L285-289（ASS CLK_CON/MIC_CTL0/1）、L159-191（AUDIO 全字段）、L285-321 区段注释、L744-787（WLA_CON0..39） |
| config 58/59/61/88/89/91/92 已占用（决定 G2/G4/G5 走向） | docs/config-key-map.md key 表（88=4B、92=6B、89/91=1B、59=1367B、61=960B） |

## 7. 未钉死处（如实标注）

1. **DAC_CON=0xfe2200 的字段语义**：值恰等于 WL_AUD 基址是巧合还是源选择字段——SDK 头
   无字段名、media IR 无该常量，只有位模式（bits 9/13/17-23）。对仿真无影响（整字记录），
   对「WL_AUD 使能与 DAC 源选择的硬件耦合」这个理解问题保持开放。
2. G1（0x60225e6 返回值）、r10（SR idx≤5 与否）两个 RAM 分支的走向：两侧对 SFR 站点集
   **零影响**（只改 RAM 写值），实施路在报告里记录走向即可。
3. 0x601bd3a 的 ANC 图门三个条件的运行时真值（[QCY+464] 写点未回溯到）——若默认世界该门
   打开，S 站点表要加 §3.3(b) 族；实施路验证跑会直接揭示（停在 JL_ANC 0xfe0400 读）。
4. initcall 表 0x60c32bc.. 续段是否同 walker（AE §5.6 同款边界问题）。
5. 0xfe0924（JL_ASS+0x24）SDK 无名（JL_Ass_TypeDef 只有三成员）——条件族启用时命名按
   「JL_ASS+0x24 未名寄存器（偏置电阻位段 p:6 l:7 消费点钉死）」处理。
