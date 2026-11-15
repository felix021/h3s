# L2-a 三镜像差分机器判定规格（路 L2AC）：等 own-step 检查点 + 三层清单

2026-11-15，路 L2AC（sim-l2ac，基线 main `2e642bc`，纯文档+acceptance 判据字段，零
tools/ 改动）。任务：把 [eq-divergence-profile.md](eq-divergence-profile.md)（路 EQDIV）
证明可用的「事件计数等价」判据，落成 L2-a（D1/D2 场景三镜像差分，
[d1-acceptance.md](d1-acceptance.md) §3.4 的宿主规格）的**可执行规格**——每条判据给出
机器判定式，不留「视情况」。A1（[eq-parity-rerun.md](eq-parity-rerun.md)）已证明零控制流
分歧判据在 447k 后机制性必破（相位彩票），EQDIV 证明等 own-step 对齐下事件面三层结构
（守恒/彩票/比例）；本规格=两者的合并 + [candidate-image-cotest.md](candidate-image-cotest.md)
（路 BT）四源白名单的归层衔接。

证据强度沿用：**[实测]** = A1/EQDIV/BT 等已合并文档与报告的实测数字（锚点随行）；
**[设计]** = 本规格新增工程决策（判定式系数/检查点密度等），实施路首跑校准。

**修订 2026-11-15（同日晚），路 L2AR（sim-l2ar，基线 main `52c4965`，纯文档零 tools/
改动）**：§3.3 预算式双口径化（Σ|δ| 主判据+|Δstep| 保守筛）+ DIF/R4C 首跑实测校准例
回填 + 适用线清单补 `abstract_task_stack_transport`（消 R2/R4-COT-01 两笔挂账，改动处
随行标注）。

**补算修订 2026-11-15（同日深夜），路 COEF（sim-coef，基线 main `0bffaa0`，纯文档+记录
复核零 tools/ 改动）**：§3.3 DIF/R4C 校准表升级为 16 差键全清单正式预算（15 白名单键
coeffs 工具存档值经重算脚本全量复现零差；`abstract_task_stack_transport` 按存档 json 数据
同式机器重算补正式预算，[设计代入] 消账）+ R4-COT-01 断言面按新判定序复核（L2AR 遗留
实施账前两笔清账，改动处随行标注）。

## 0. 一句话结论

判据按窗口分两段：**C0（首次 core-tick 溢出交付，own-step ≈447k）之前**零控制流分歧
判据原样适用；**C0 之后**（对有步轴偏移的镜像对）切换为「**等 own-step 检查点 + 三层
清单**」：第一层守恒类必须逐项相等（12 项，含封闭世界式的 adapter 闭包）、第二层相位
彩票白名单按闭式/有界吸收（6 项判定式）、第三层等待域按预算式吸收（双口径：Σ|δ|
主判据+|Δstep| 保守筛，L2AR 修订；超主判据预算即 unexplained=回归）。BT 四源白名单的归层：A=彩票成因+特有键吸收、B=守恒类预登记例外表、
C=第二/三层、D=正交的改道预声明层。

## 1. 适用范围与两窗切换判据

**适用镜像对**：{official, tested_b79, eq_fence_repair} 全部三对（L2-a 主用），以及候选
{l1_slotram, r4_keyhook} 与 official 的对（BT 的 A/B 双世界，同框架适用——两者都是
config.dat 追加型候选，与 EQ 同为 +458 步轴偏移族 [实测：cotest §1.4]）。

**C0 的机器定义**：C0(X) = 镜像 X 的 IRQ3 handler 入口 `0x60661a4` 首次取指的 own-step
（首次 core-tick 溢出交付步号）。[实测：A1 §4——b79 447,512 / EQ 447,490]。

**切换判据（逐对，机器可执行）**：δ₁ = |C0(A) − C0(B)|。

- **δ₁ = 0**：该对在 C0 后**维持零控制流分歧判据**（d1-acceptance §3.4 原判据），每
  core-tick 周期检查点复核 δ_k=0（第 k 次交付步号差）；一旦某 δ_k≠0，该对即刻切换
  三层清单模式。机制：步轴零差 → CNT 相位零差 → 同一开奖。[实测：official↔b79
  零控制流分歧至 447,490（AGENTS 三镜像 parity 句）；447k 后为运行观测项——该对无
  config.dat 追加差（b79 为原位字节改，非追加），步轴零差是机制推导+前缀实测。]
- **δ₁ > 0**：该对从 C0 起**切换三层清单模式**（§2-§3）。[实测：b79↔EQ δ₁=22，此后
  交付恒错位 22 步、+458 lag 重合永久破坏（A1 realigned=false）；控制流分歧在 C0 后
  是相位彩票的预期产物，不再是回归信号。]

**候选对的 δ₁**：official↔l1/r4 与 b79↔EQ 同族（+458 执行轴差→CNT 相位 +1832→交付
相位差），δ₁ 预期同为 22 量级——**运行观测项**（l1 表尾跨 4KiB 对齐桶的派生族是否改变
C0 前的执行轴形状，BT §1.4 已点名），实施路首跑实测归档后进第二层预算。

## 2. 等 own-step 检查点：操作定义

**own-step** = 镜像自身模型步计数。**等 own-step 对齐** = 各镜像取自身步数恰为同一数值
c 时的事件面做比较，**不做**世界位置（lag）对齐。机制前提 [实测：EQDIV §2 开头]：
core-tick CNT/Timer0 计数器挂在自身步进上，等 own-step 即等计数器轴——+458 执行轴差在
此视角下不存在（它只表现为交付相位差 δ，进第二层）。

**检查点集（机器枚举，逐对）**：

```
C = {C0(R)} ∪ {O_k(R) : k ∈ G} ∪ {S_min}
```

- **基准镜像 R**：场景前沿钉点所属镜像（`p11_heap_experimental`=tested_b79；BT 候选对
  =official）。检查点数值取基准侧 own-step。
- **S_min** = min(各镜像停点)——检查点不越过任一镜像的诚实停点；停点不同的对，公共段
  [0, S_min] 用检查点模式，残段 (S_min, S_max] 用 §3.4 跨停点闭式。
- **O_k(R)** = R 的第 k 次 core-tick 溢出交付步号；**G（抽样密度）**：必含 {首次, 末次
  （≤S_min 的最大 k）}，中间按 [设计] 至少每 4 个周期取一点（首跑可加密到逐周期）。
  检查点取在溢出交付后 ≥1 步（避开交付瞬间的在途状态）。
- 最低要求：C 至少含 {C0(R), S_min} 两点（EQDIV 全部结论就是在单检查点 3,747,585 上
  验证的 [实测：EQDIV §2-§4]）。

**事件面获取（三种实现，机器等价，实施路择一）**：

1. **受控停机**：`instruction_budget=c` 的预算停点逐检查点捕获（先例：SNAP artifact
   tier N=300,000/400,000 捕获 [文档：snapshot-design §5.7]）；多检查点 = 多次重放或
   快照恢复续跑（SNAP 恢复链已绿）。
2. **全轨迹切片**：单次运行记录逐步事件流，按步号切片取事件面（EQDIV scan 方式，
   4.5M 步实测可行；19M 步量级需实施路评估驻留）。
3. **报告键增补**：把检查点事件面直接做成运行时报告键（d1-acceptance §3.4 已有同类
   「报告侧增补项」条款先例）。

**每检查点必须产出的观测量**（判定式的输入，缺键=实施路报告增补项）：

| 观测量 | 用途（判定式编号） |
|---|---|
| per-core 步数 {CPU0, CPU1} | T1-1 |
| `instruction_adapters` 全键计数 | T1-6（封闭世界闭包） |
| 写集累计地址集（双向 only-集） | T1-4 |
| core-tick：溢出/交付/ACK/held-pending/deferral 计数 + 逐次交付步号序列（δ 序列=Σ\|δ_k\|/δ_max 输入，L2AR） | T1-2/T2-1/T2-2/T2-4/T3 |
| Timer0：交付计数 + 逐次交付步号序列 + PRD 窗记录 | T2-3/T3 |
| jiffies（RAM 0x124ca4）值 | T2-2 |
| 停点 RAM dump：data 区 0x124840..0x125320 + lrc_trim_result 块 0x129D88..0x129DEC + §2.5 快照锚区 | T1-7/8/9、T2-6 |
| CRC：轮次/结果读/喂入字节 | T1-3、特有键吸收 |
| 功能域页直方图（64KiB 桶） | T1-5 |
| 事件时钟推进量（停泊/唤醒） | T1-10 |
| abstract_write/abstract_read 计数 | T1-11 |

## 3. 三层清单（逐项判定式）

### 3.1 第一层：必须相等（守恒类）——12 项

对检查点 c，两镜像（或对基准做差）逐项判定 `X_A(c) == X_B(c)`，任一不等=FAIL（无白
名单、无预算）。全部条目均有 EQDIV 等 own-step 3,747,585 实测等值锚点：

| # | 判定项 | 判定式 | 实测锚点 [EQDIV] |
|---|---|---|---|
| T1-1 | CPU1 per-core 步数 | `cpu1_steps_A == cpu1_steps_B` | 11,251=11,251（§2.7；分叉全在 CPU0） |
| T1-2 | core-tick 溢出计数 | `N_over_A == N_over_B` | 12=12，且溢出周期序列两轴逐项相同（300,005×8、300,003×2、300,005×3）（§2.1） |
| T1-3 | CRC 轮次（seed writes） | `N_rounds_A == N_rounds_B` | 32=32（§2.4） |
| T1-4 | 写集累计地址集 | `addrset_A == addrset_B`（双向 only-∅） | 42,123=42,123，终态与等 own-step 累计均零差（§2.5） |
| T1-5 | 一次性功能域页计数 | 15 页逐页 `count_A == count_B`（页清单=0x4000/0x6010000/0x12a000/0x6064000/0x6060000/0x6007000/0x6074000/0x6075000/0x6013000/0x6084000/0x6011000/0x6085000/0x6071000/0x1f000/0x601d000） | 逐页相等（§2.6，如 0x4000 21,255=21,255） |
| T1-6 | 启动域 adapter 计数（封闭世界闭包） | `instruction_adapters` 全键中，凡**未列入** §3.2/§3.3 线清单（预登记）的键必须相等；即「默认相等，白名单吸收」 | 45 键相等（§2.3：cpu1_control_rmw 28=28、sfc_clock_gate_rmw 401=401、software_irq_rti 12=12、GPIO/P33/mem_pwr/ROM REP 族）；27 个差键全部落入 §3.2/§3.3 清单 |
| T1-7 | 结构快照逐字节 | boot_info、ram_code(18,644B)、efuse 4 页、MMU PTE 锚、TCB 帧、CPU1 入口/ready、os_running、cache_lock、current_tcbs、p11 各格、ADC 条目——逐字节相同 | §2.5 memory_snapshots 复核逐字节同 |
| T1-8 | data 区字节级 | 0x124840..0x125320（2,784B）除 0x124ca4 外逐字节同 | 恰 1 字节差=0x124ca4（§2.5） |
| T1-9 | lrc 块字节级 | 0x129D88..0x129DEC（0x64B）除 0x129D8C 外逐字节同 | 恰 1 字节差=0x129D8C（§2.5） |
| T1-10 | 停泊/唤醒计数 | `event_clock.virtual_model_steps_advanced_A == _B` | 0=0（§2.3 直接报告键） |
| T1-11 | abstract 读写计数 | `abstract_write_A == _B ∧ abstract_read_A == _B` | 1,108=1,108、9=9（§2.5） |
| T1-12 | 交付自洽核账（逐镜像内部） | `N_del + N_pending == N_over`（core-tick）∧ `N_ack == N_del`（一比一）∧ `jiffies == 基值 + N_ack` | §2.1：jiffies=10+ACK 双侧字节级+寄存器级双证 |

### 3.2 第二层：相位彩票白名单（有界吸收）——6 条判定式

| # | 项 | 判定式（机器形式） | 实测锚点 |
|---|---|---|---|
| T2-1 | core-tick 交付差 | `N_del_A − N_del_B == N_pending_B − N_pending_A`（与 T1-12 联立等价于「溢出守恒+pending 吸收」） | 11+1held vs 12：交付差 1=pending 差 1（§2.1/§4） |
| T2-2 | ACK/jiffies | `ΔN_ack == ΔN_del`（ACK 一比一）∧ `Δjiffies == ΔN_ack`（0x124ca4 值差恰等于 ACK 数差） | jiffies 21/22（等 own-step）、21/24（跨停点）均=基值+ACK（§2.1/§2.5） |
| T2-3 | Timer0 交付差界 | `\|N_T0del_A − N_T0del_B\| ≤ ⌈δ_max/T_min⌉`；δ_max=区间 [c_prev, c] 内**同序号**交付步号差的最大值（两侧交付步号序列按下标配对，取 max\|差\|）；T_min=区间内两侧并集的最小交付间隔 | δ=22、T_min=4,000（尾段）→ 界 1；实测 362 vs 361 过（§2.2/§3.1）。混合重排期（~3.06M）δ 可达 13,368 → 界 ⌈13,368/4,000⌉=4，仍闭式可判（§2.2 相位结构） |
| T2-4 | deferral（在飞窗 held 重请求） | 如实报、无界吸收；**下游把关=T2-3**（deferral 漂移必然改变交付计数，超界即 FAIL——回归不能无限藏进 deferral） | 168 vs 10,077（§2.3，等待域相位结构差，无闭式） |
| T2-5 | LRC 帧计数格 0x129D8C | 等own-step 检查点：`\|Δ值\| ≤ ⌈δ_max/T_win⌉`（T_win=区间内最小延时窗长，机器现算） | 等own-step 同值 12=12；跨停点 12/15（+3=多跑窗数，§2.5/§4）；窗长 15,864..19,864（§2.6）→ 界 ⌈22/15,864⌉=1 |
| T2-6 | RAM 终值差集 | trace 可见终值差地址集 ⊆ **预登记簿记/栈清单**（0x1237bc..0x123834 栈、0x1268cc/0x1268f8/0x1272b8 bss 簿记、0x19e2cd..0x19e2df、0x19f7xx/0x19fb30 TCB 邻区）；清单外任何新地址=unexplained；值不设界（时间派生暂存，残余风险见 §6.3） | 等 own-step 31 格全落清单（§2.5/§4）；候选对追加源 B 写面进例外表（§4） |

### 3.3 第三层：等待域比例吸收——预算式

**适用线（预登记清单，封闭）**：调度器读族、存储适配器族（exact_full_word_store 等）、
`cpu0_shared_lock_ownership`、`irq_repeat_return_hypothesis`、ICFG 可见性族、Timer0
handler 帧、core-tick handler 帧、**任务栈帧运输 `abstract_task_stack_transport`
（L2AR 补录）**。出处 [实测：EQDIV §2.3 的 27 差键全部落此清单或 §3.2；§3 结论 3
「步数线性伸缩」]；补录键出处 [实测：DIF/R4C 首跑重测 16 差键（本节数字见下）——
`abstract_task_stack_transport` 为 unknown_task_gprs 窗内任务帧 push/pop 寄存器列表
族的栈搬运计数（调度器任务帧 GPR 存取，含 UnknownWord 搬运），当前 flag 集下三偏移对
差 −10（104,965 vs 104,955）而 EQDIV 27 差键未含之（A1 时点 flag 集下两侧相等），
属等待域重排族（任务帧 push/pop 随重排次数消耗）；原清单未名列=DIF/R4C 字面缺口挂账，
按 T1-6「默认相等，白名单吸收」的字面口径该键曾是层 1 违规、两路按「同构于已归档
基线」处理，本修订显式入列消账]。

**预算式（每线 L，双口径并列；L2AR 2026-11-15 按 DIF/R4C 首跑实测修订——原单口径
|Δstep| 分母在跨停点比较下把 4 键误报超界，两路挂账承接）**：

```
主判据（Σ|δ| 口径）：      |Δcount_L| ≤ E_L  = 2·⌈Σ|δ_k| × ρ_L⌉ + ⌈δ_max/T_min⌉ + 1
快速保守筛（|Δstep| 口径）：|Δcount_L| ≤ E_L⁰ = 2·⌈|Δstep| × ρ_L⌉ + ⌈δ_max/T_min⌉ + 1
```

- **Σ|δ_k|（主判据分母）**：区间 [c_prev, c] 内**同序号**交付步号差 δ_k 的绝对值之和
  （配对同 §3.2 T2-3，即 δ 序列的分段幅度和）；检查点模式与跨停点模式同式。语义=等待
  域预算按**逐窗消耗累计**的真实度量——每个等待窗的重排不论方向都真实消耗一次预算。
- **|Δstep|（保守筛分母）**：端到端步轴差——检查点模式=δ_max（等 own-step 下相位错位
  是唯一步轴差）；跨停点模式=净停点差 |S_A − S_B|（原单口径定义，原样保留）。语义=
  端到端相位差的粗上界：方向相反的段在端到端差里正负抵消，系统性低估等待消耗。
- **判定序（机器可执行）**：先算保守筛 E_L⁰——过即 PASS（三角不等式 |Δstep| ≤ Σ|δ_k|
  ⇒ E_L⁰ ≤ E_L，筛过蕴含主判据过，免算 Σ|δ|）；**保守筛超界 ≠ FAIL**，必须切主判据
  E_L 复判，超 E_L 才 unexplained=FAIL。
- **何时必须切 Σ|δ|**：保守筛超界时必切；机制上「等待消耗≈净步轴差线性伸缩」的假设在
  deferral 重排族活跃时失效（T2-4 的 held 重请求把等待变重执行、重执行含配置写序列，
  与步数比例伸缩解耦——DIF/R4C 实测 deferral 差 9,923（2,458 vs 12,381）下
  exact_full_word_store 34/102 与 EQDIV 同族 2,701/773,969 比例差 100 倍即实证），故
  跨停点比较推荐直接算 Σ|δ|。
- **ρ_L**：基准镜像 [0, c] 区间该线密度（计数/步数），**比较前**从基准轨迹导出（先声
  明后比较，非事后拟合——d1-acceptance §7 白名单先声明原则的承接）。
- **T_min**：该线关联交付线的区间最小交付间隔（core-tick 300,005 族/Timer0 按 §3.2
  T2-3 同源值）。相位项 ⌈δ_max/T_min⌉ 两口径同用 δ_max，不随分母切换。
- **系数 2 与 +1** [设计]：量级预算语义（EQDIV §3.3「可作量级换算，不是精确闭式」）；
  校准依据=EQDIV 实测残差/纯预算=402/377≈1.07（调度器读跨停点），×2 留窗口间突发方差，
  +1 为单事件仲裁余项。超 E_L=unexplained=FAIL。
- **弃选方案（L2AR 如实归档）**：DIF 首跑注记的另一条修订路「deferral 下游配置写划归
  T2-4 交付界把关延伸」不采纳——T2-4 的下游把关语义=交付计数界（T2-3），配置写重执行
  不是交付事件，划入会稀释 T2-4 判定式语义；双口径已闭环。

**EQDIV 实测代入校准表**（实施路首跑后按新 flag 集重算补全）：

| 线 | 模式 | 实测差 | 预算 E_L（代入） | 结果 |
|---|---|---|---|---|
| 调度器读 | 等 own-step | −3 | 2·⌈22×4.870e-4⌉+⌈22/4,000⌉+1 = 2+1+1 = **4** | 过（余 1） |
| 调度器读 | 跨停点 | +402 | 2·⌈773,969×4.870e-4⌉+1+1 = 754+2 = **756** | 过 |
| Timer0 handler 帧 | 跨停点 | +161（723→884） | 2·⌈773,969×1.929e-4⌉+1+1 = 300+2 = **302** | 过 |
| 存储适配器/锁/irq_repeat/ICFG 族 | 跨停点 | +2,701/+213/+808/+690 等 | EQDIV 未归档基准绝对值，ρ_L 实施路首跑补 | 待补 |

EQDIV 各行按 |Δstep| 代入（当时单口径）；Σ|δ| ≥ |Δstep| ⇒ 主判据界只宽不紧，故该表
结论在双口径下原样成立（L2AR 注）。

**DIF/R4C 首跑重测代入校准表——16 差键全清单正式预算（L2AR 回填、COEF 2026-11-15
正式化；当前 flag 集，条件档 official↔r4_keyhook，停点 21,798,257/21,798,155 @
0x606b68e，基线 main `364d5bd`；与 R2-COT-01（official↔l1_slotram，DIF 档
21,798,206）16 差键同集合同差值（2 键绝对值随前沿 +51 整体平移、差值不变——R4C
记录），b79↔EQ 对同构。口径输入：Σ|δ_k|=58,354、δ_max=1,446、净停点差=102、相位项
⌈δ_max/T_min⌉=⌈1,446/300,005⌉=1（core-tick 族 T_min）；ρ_L=基侧计数/基侧步数。
数字来源：15 白名单键=coeffs 工具存档值（`analysis/local-checks/r4c/r4c-coeffs.json`）；
abstract 键=COEF 按存档 json 数据（基侧计数/步数+δ 读数）同式机器重算的正式值
（gitignored 运行产物，数字以 R4-COT-01/R2-COT-01 记录与 HANDOFF 承载）**：

| 线 | 实测差 | 保守筛 E⁰（\|Δstep\|=102） | δ_max 代入（1,446） | 主判据 E（Σ\|δ\|=58,354） | 结果 |
|---|---|---|---|---|---|
| exact_full_word_store | 34 | 4 ✗ | 20 ✗ | 718 ✓ | 主判据过（切主判据） |
| abstract_task_stack_transport | 10（104,965 vs 104,955） | 4 ✗ | 16 ✓ | 564 ✓（56 倍余量） | 主判据过（切主判据） |
| exact_postincrement_store | 32 | 4 ✗ | 10 ✗ | 262 ✓ | 主判据过（切主判据） |
| exact_subword_store | 6 | 4 ✗ | 4 ✗ | 66 ✓ | 主判据过（切主判据） |
| partial_icfg_known_lowbyte | 5 | 4 ✗ | 4 ✗ | 6 ✓（余 1，最紧） | 主判据过（切主判据） |
| per_core_visible_cnum | 2 | 4 ✓ | 10 ✓ | 292 ✓ | 保守筛即过 |
| irq_repeat_return_hypothesis | 4 | 4 ✓（恰界） | 8 ✓ | 222 ✓ | 保守筛即过（恰界） |
| partial_icfg_bit9_only | 2 | 4 ✓ | 8 ✓ | 176 ✓ | 保守筛即过 |
| partial_icfg_read_or_gate_write | 3 | 4 ✓ | 6 ✓ | 114 ✓ | 保守筛即过 |
| partial_icfg_pure_bit_mask | 2 | 4 ✓ | 6 ✓ | 112 ✓ | 保守筛即过 |
| mmio_rmw_always_writes | 1 | 4 ✓ | 4 ✓ | 80 ✓ | 保守筛即过 |
| cpu0_shared_lock_ownership | 2 | 4 ✓ | 4 ✓ | 56 ✓ | 保守筛即过 |
| timer0_irq_handler_frame | 2 | 4 ✓ | 4 ✓ | 42 ✓ | 保守筛即过 |
| conditional_timer0_irq_rti | 1 | 4 ✓ | 4 ✓ | 22 ✓ | 保守筛即过 |
| immediate_rep_multi_block_projection | 1 | 4 ✓ | 4 ✓ | 6 ✓（余 5） | 保守筛即过 |
| parallel_byte_store_old_registers | 1 | 4 ✓ | 4 ✓ | 6 ✓（余 5） | 保守筛即过 |

- 判定序实跑结果（COEF 复算）：16 键保守筛 11 键即过（E⁰=4；irq_repeat 恰界）、
  5 键超筛切主判据全过（E=718/564/262/66/6）、主判据超界 0——判定序「筛过即 PASS、
  筛超界必切主判据」全 16 键机器执行无歧义。
- R2 侧独立复核（R2CHK 2026-11-16，镜像 COEF 手法）：按 DIF 档基侧步数
  （official@21,798,206）以精确分数 ρ_L 重算 16 键三口径 E 值与本表逐键同值（与 R4C
  基侧 51 步差→ρ 第 6-7 位差被 ceil 吸收），15 白名单键对 difline-coeffs.json
  存档 E 值复现零差、abstract_task_stack_transport 同式补算同值（E=564）——
  R2-COT-01 断言面按新判定序复核（PASS/conditional 维持）直接引用本表。
- 前 5 行中 4 键（exact_full_word_store/exact_postincrement_store/exact_subword_store/
  partial_icfg_known_lowbyte）保守筛与 δ_max 端到端口径**双超界=误报案例**（机制=
  deferral 重排差 9,923 把等待变重执行，见上「何时必须切」），主判据全过——双口径修订
  的直接校准证据（先例：EQDIV 实测数字代回校准全过）；abstract_task_stack_transport
  保守筛超（10>4）而 δ_max 代入即过（16≥10），同切主判据过。
- abstract_task_stack_transport 行=入列后正式预算（COEF 补算，消 L2AR [设计代入]
  挂账）：DIF/R4C 跑时 coeffs 工具白名单未含该键（r4c-coeffs
  `adapter_keys_off_whitelist` 如实记录），正式数字按存档 json 的基侧计数
  104,965/步数 21,798,257+δ 读数以同式机器重算——E⁰=4/E_dmax=16/E=564，与 L2AR
  [设计代入] 估算（4/16/≈564，ρ_L≈4.82e-3 同式）逐位一致；重算脚本对 15 白名单键
  存档 E 值（三口径×3 偏移对）全量复现零差、difline-coeffs.json 双源交叉 E 值逐键
  相同（ρ 第 6-7 位差被 ceil 吸收）——场景无需重跑（预算全部输入已存档 json）；coeffs
  工具白名单同步属 tools 路未动。
- 16 差键清单全文钉死（三偏移对同集合；L2AR）：abstract_task_stack_transport、
  conditional_timer0_irq_rti、cpu0_shared_lock_ownership、exact_full_word_store、
  exact_postincrement_store、exact_subword_store、immediate_rep_multi_block_projection、
  irq_repeat_return_hypothesis、mmio_rmw_always_writes、parallel_byte_store_old_registers、
  partial_icfg_bit9_only、partial_icfg_known_lowbyte、partial_icfg_pure_bit_mask、
  partial_icfg_read_or_gate_write、per_core_visible_cnum、timer0_irq_handler_frame——
  全部落入本节适用线清单或 §3.2（R4-COT-01 字面缺口挂账消账）。

**首跑重测（2026-11-15 路 DIF/sim-difline，条件栈 21,798,206 @ 0x606b5ee，基线 main b592ecc）**
——§6.2 预告的重测已执行，三镜像+候选各一次全前缀观察跑（全数据
`analysis/local-checks/difline/difline-coeffs.json`，gitignored 运行产物）：

| 对 | δ₁ | δ_max（core-tick δ 序列） | 尾段锁定 | 停点差 | 备注 |
|---|---|---|---|---|---|
| official↔tested_b79 | **0** | **0**（116 次交付逐项同号同值） | — | 0 | δ₁=0 对「逐周期复核 δ_k=0」首次全量实测兑现；72 adapter 键全等，唯一差=CRC 结果读+milestones（源 A 特有键） |
| tested_b79↔eq_fence_repair | **22** | **1,446**（EQDIV 时 13,368） | +102 | −102 | EQ 停点 21,798,104（越过 A1 时点 4,521,554——DAA_CON0 已放行后跟随到条件前沿） |
| official↔l1_slotram | **22** | **1,446** | +102 | −102 | 与 b79↔EQ 逐档同构（δ 直方图逐档相同、l1↔EQ adapter 72 键逐键相等）——两条 +458 追加型候选的彩票轨迹一致，三层清单基线合一 |

- δ 直方图（两偏移对同）：{22×11, 308×31, 1446×10, 877/878×30, 102×24, 658×2, 385×4, 125×2, 1342×1, 883×1}；Σ|δ_k|=58,354。
- 层 3 预算式口径发现（如实归档）：字面口径 |Δstep|=净停点差 102 下 **4 存储适配器键超界**
  （exact_full_word_store 34>4 / exact_postincrement_store 32>4 / exact_subword_store 6>4 /
  partial_icfg_known_lowbyte 5>4）——机制=deferral 重排差 9,923 次（2,458 vs 12,381）把等待
  变成重执行（重执行含配置写序列），与步数比例伸缩解耦（EQDIV 同族 2,701/773,969 与本窗口
  34/102 比例差 100 倍即实证）；δ_max 口径（1,446）同 4 键超；**Σ|δ_k| 口径（58,354）16 键全过**。
  修订已执行（L2AR 2026-11-15）：本节预算式双口径化（Σ|δ| 主判据+|Δstep| 保守筛，见上），
  本条 4 键超界按主判据复判全过（DIF/R4C 代入校准表在上）；「deferral 下游配置写划 T2-4」
  备选弃选（理由见 §3.3 弃选方案）。
- 层 1 守恒（三对全过）：CPU1 11,251=11,251、CRC 轮次 32=32、data 区/lrc 块终值差集空、
  写集页直方图差仅 0x12/0x19 栈/TCB 域页（层 3 管辖）、交付自洽核账 116+0==116 ∧
  jiffies==10+116（终值 126 双侧同值，写入步号差 102）；层 2 全过（**Timer0 T2-3 机器算**：
  Timer0 自身 δ_max=23,322/T_min=774→界 31，实测交付差 1≤31 ✅——official 侧 Timer0 序列
  经 official↔b79 观察面全等传递替代自 b79，已在 coeffs JSON 显式标注；core-tick δ_max=1,446
  是 Timer0 相位差的 1/16——Timer0 在 4,000 步节奏下的相位差积累更大，T2-3 判定式对两线
  各自的 δ 序列取值；pending 双侧停点全 False、deliveries reti_from 延时环三 PC 交替=彩票预期）。

### 3.4 跨停点模式附加闭式（比较=各自终态报告时）

- **core-tick 交付差**：`|ΔN_del| ≤ ⌈|S_A−S_B| / 300,005⌉ + |ΔN_pending|`（停点间溢出数
  闭式）。[实测：EQDIV §3 结论 2——差 3=EQ 多跑 774k≈2.58 个周期内交付的第 12/13/14 次]
- **jiffies**：`Δjiffies == ΔN_ack == ΔN_del`（闭式链）。[实测：21 vs 24 差 3]
- **写集地址集**：仍必须相等（T1-4 不受停点差影响——脚印守恒）。[实测：终态 42,123=42,123]
- 其余项按 §3.1-§3.3 同式（§3.3 双口径，L2AR：保守筛 |Δstep|=净停点差，主判据 Σ|δ_k|
  同式；跨停点比较推荐直接算主判据）。

## 4. 与 BT 四源白名单的衔接（candidate-image-cotest §2.3 的归层）

| BT 源 | 新框架归层 | 判定变化 |
|---|---|---|
| **A 构造差** | C0 前=已知族签名段判据不变（fence 寄存器差/config.dat +458 段/表尾派生族/CRC 引擎结果差）；C0 后=**δ 的成因项**（+458 步轴差→CNT 相位 +1832→交付相位差 δ₁）+**特有键吸收**（CRC 结果读 +1/喂入字节 +30=多枚举条目数据面残留 [实测：EQDIV §2.4]） | 特有键进 §3.2 白名单（构造差下游键预登记）；δ 序列本身是 T2-3 预算的输入而非违规 |
| **B 载荷写集** | **第一层的预登记例外表** | 候选对：T1-4 地址集差 ⊆ B 表；T1-7/8/9 快照差 ⊆ B 写面；B 触发的 adapter 线差 ⊆ B 线清单。例外表机器生成钉进单测（BT §2.3 原文承接） |
| **C 步数派生** | **第二层**（jiffies/CNT/PRD/Timer0 余数/交付计数）+**第三层**（等待域计数） | jiffies 主计数格=**0x124ca4**（EQDIV RAM dump 钉正；BT 原稿 [0x125004] 是 BQ 缓存读记法——EQDIV dump 中该址双侧同值 203）。原「源 C 流入控制流即升级 unexplained」条款**适用窗收窄到 C0 前**：C0 后控制流分歧是相位彩票预期产物（事件轴与执行轴解耦，A1 §4），升级判据改为「流入 own-step 事件计数面：超 §3.2 界或破 §3.1 守恒即 unexplained」 |
| **D 改道差** | **正交层**（预期行为差预声明，不属三层） | 改道集的额外步数并入各预算式的 \|Δstep\|；其固件写面并入 B 例外表；行为断言（观察点）独立判定（BT §2.3 层 3 原文承接） |

## 5. L2-a/BT 用例判定行的落地（本路同步修正的清单）

1. **R2-COT-01 / R4-COT-01（公共前缀 parity）**：crc_bankb 档（289,849 < C0）四口径
   不变；p11_heap_experimental 档（条件前沿 19,139,239 > C0）——official↔l1/r4 是
   +458 偏移对，C0 后切本规格 §2-§3；「停点 parity」判据改为「各自诚实停 + 停点差按
   §3.4 跨停点闭式核账」；「reconverge」判据的适用段=[0, C0)。
2. **R2/R4-COT-02..06**：前缀段「逐步 parity」的适用段=[0, C0)；[C0, 注入点)=三层清单
   基线（其差集由 COT-01 归档）；注入段事件对齐不变（BT §2.3 原本就不按 step zip）。
3. **R2-COT-04 / R4-COT-04（等价负例）**：「零行为差」改为「**零增量差**」——观察窗
   终点的事件面差集 ⊆ 注入起点已归档的 C0 后基线差集（三层清单内），新增任何差类=回归。
   理由：偏移对在 C0 后本就存在三层清单内的基线差，逐字节全同不再可达。
4. **parity 面板**：A1-PAR-01（FAIL）/E1-PAR-01（PASS）为历史运行记录不动；L2-a 实施
   路的运行记录按本规格出，id 按 parity 面板规则续编。
5. 顺手校正（非 A1/EQDIV 因素、BU 已合并的规格修正回填）：R2/R4-COT-01 档案内
   PLAIN_LENGTHS 0xE5200/0xE524E → 0xE31E0/0xE322E（cotest §1.2「工具就绪」段的
   实测修正），条件前沿 3,747,585 → 19,139,239（main 2e642bc 条件栈现状）。

## 6. 诚实边界

1. **判定式系数（×2/+1）与检查点抽样密度 G 是工程决策 [设计]**，校准自 EQDIV 实测
   （§3.3 代入表）；非硅语义，实施路首跑后按新 flag 集重钉数值。首跑（DIF/R4C）已按
   新 flag 集重钉并触发 §3.3 预算式双口径修订（L2AR）——「形式不变」承诺按「原式
   原样保留为保守筛、主判据同构换分母」兑现；系数本身（×2/+1）未动。
2. **EQDIV 数字=A1 时点 flag 集**（b79 停 3,747,585；当前条件栈已到 19,139,239，
   `core_tick_cnt_held_snapshot`/`jl_adda_analog_family` 后置）——实施路在当前栈上
   首跑须重测 δ₁/δ_max/ρ_L/K 系；EQ 侧新停点未知（旧 4,521,554 的 DAA_CON0 已被
   `jl_adda_analog_family` 放行）。
3. **31 格簿记/栈值不设界**（T2-6）：栈暂存里的真回归只能靠写集地址守恒（T1-4）+
   一次性域守恒（T1-5）+adapter 闭包（T1-6）间接抓——残余风险如实标注。
4. **official↔b79 过 C0 零分歧=机制推导**（零步轴差→同开奖）+447,490 前实测，C0 后
   运行观测项；若实测 δ_k≠0（意外相位源），该对按 §1 切换三层清单，不是回归。
5. **时序等价不可作判据**（A1 钉死 22 步交付错位；EQDIV §5.3）；跨线交错序（IRQ3×IRQ4）
   只可作弱判据且须逐检查点核对（EQDIV §5.4、E1-PAR-01 uncovered 第 2 条挂账）。
6. **等 own-step 的 Timer0 到期数**（`timer0.expirations` 终态才报）=E1-PAR-01
   uncovered 第 1 条挂账，实施路报告增项。
7. 本规格是**模型轨迹差分**的回归判定口径（conditional/derived-not-measured），不构成
   镜像行为等价证明；候选镜像 UNTESTED 状态与互斥不变量不动。

## 7. 证据锚点

- A1：[eq-parity-rerun.md](eq-parity-rerun.md) §2（双侧停点）/§4（相位彩票机制、交付
  错位 22 步）/§6（L2-a 设计约束）/§7（判据①②命中）。
- EQDIV：[eq-divergence-profile.md](eq-divergence-profile.md) §2（等 own-step 事件表全
  部实测数字）/§3（三类结构定性）/§4（差集白名单候选）/§5（判据建议原文）。
- 验收记录：[E1-PAR-01](acceptance-samples/parity/E1-PAR-01.json)（PASS/conditional，
  等值键/差键/计数格全套）；[A1-PAR-01](acceptance-samples/parity/A1-PAR-01.json)（FAIL
  记录，判据①②命中证据）；[R4-COT-01](acceptance-samples/cot/R4-COT-01.json)/
  [R2-COT-01](acceptance-samples/cot/R2-COT-01.json)（DIF/R4C 首跑，§3.3 双口径校准
  例与 16 差键清单的数字承载，L2AR；R4-COT-01 断言面已按双口径判定序复核、16 键
  正式预算表回填本节，COEF）。
- 宿主与衔接：[d1-acceptance.md](d1-acceptance.md) §3.4（L2-a 原始规格——其预测 2 升级
  条款已被 A1 命中、预测 3 停点 parity 已破，本规格是其 447k 后段的可执行替代）；
  [candidate-image-cotest.md](candidate-image-cotest.md) §1.4/§2.3（四源白名单，归层见
  本文 §4）。
