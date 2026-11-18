# 前沿侦察 COPEX：copex.ppi DSP 协处理器停点（42,037,363 @ 0x606a900）

路 COPEXSCOUT / sim-copexscout（基线 main 1b72d04，worktree
`~/worktrees/h3s/sim-copexscout`，静态侦察，零 tools/ 改动，不跑条件线）。
2026-11-18 18:23 开工 / 18:43-18:46 codex 参谋（gpt-6-astra）/ 19:01 收口。
停点=路 DMAIMPL 实测交接（evidence/sim-routes 与 HANDOFF：
kind=`upstream_simulator_failure: '0x606a900: copex.ppi has no model'`）。
一次性脚本在 `~/tmp/copexscout/`（scan2.py/chain.py/path.py，不进仓）、
SDK IR 用路 CJ 既有提取（`~/tmp/h3s-anaread/objref/all/math_fast_funtion.c.o.ll`）。
证据 JSON：`evidence/sim-routes/frontier-scout-copex.json`。

## 0. 停点与一句话结论

- 停点指令 `0x606a900: ff e1 16 22` = `r3_r2 = copex(r3_r2)(22)`，落在叶子函数
  0x606a8fc（exp 薄包装）。**mode 22 = e^x 已双源钉死**：SDK DSP 库
  `math_fast_funtion.c` 的 `exp_float` 就是「fmin/fmax 钳位后 copex(22)」，
  且 AC791N 硬件捕获 6 条中 4 条数值吻合 + e^1.0→0x402df853 一条有限互证
  （§2/§3）。
- 停点函数族 = **eq/wdrc 系数计算的 db2mag（10^(dB/20)）**：0x606a908 先算
  `(dB/20.0)·ln10` 再调 exp 叶子——正是 SDK `eq_db2mag`/`drc_db2mag` 的公式
  （路 CN 已从 IR 侧钉过同式）。调用链（静态 BFS）：
  **eq open-config 0x60787ac → 0x607b9ee → 0x606feba → 0x606a908(db2mag)
  → 0x606a8fc(exp) → 0x606a900 停**——与路 CP 实测的「bypass=0 重开路径
  停在系数计算」完全吻合。
- 这是**上游模拟器能力缺口**（sim.py 无模型即诚实停），不是固件墙；
  三选一裁决（codex 定稿）：**收窄版 (a) 本地 mode-22 指数近似模型**，
  conditional 层、默认关、域外专用停点，其余 7 个 app 模式另案审查（§5/§6）。

## 1. copex.ppi 指令钉死（ISA + 编码）

`isa/pi32v2.toml:17773`（status=confirmed，sources=oracle+compiler+hardware，
group=dsp）：

- **编码**：两字。word1=`1110000111111111`=0xE1FF 固定；word2=
  `rrr-sss-nnnnnnnn` = `(dst_pair_base/2)<<13 | (src_pair_base/2)<<10 | n(u8)`。
  停点字节 `ff e1 16 22`：dst=src=r3_r2（pair base 2），n=22。
  （校验：isa 表自带例 `ff e1 00 e0`→r15_r14 dst ✓）
- **操作数约定**：pair (lo,hi)。app 全部真码站点：单目模式（exp/ln/sqrt/sincos）
  参数在 **lo、hi 置零**；双目 atan2（mode 17）是 (y,x) 两实参进 pair
  （0x602807a 直接从数组取相邻两元素）。
- **状态契约**（364 条 AC791N 捕获的共识）：只写 dst pair 两个寄存器
  （`changed` 字段恰 2 项）、psr 低 4 位保持（flags_in==psr_out）。
  isa notes：mode=control&31；高位掩码 32/64/128/224 对已采样模式输出不变；
  bit4（值 16）在部分模式有作用。
- **并行孪生** `copex.ppi.p`（word1 bit12 → 0xF1FF，带 `#` 后缀）：app 内
  解码命中 2 处均为伪（§4），真码零站点。**注意**：上游 sim.py 的 `pair()`
  会把 `.p` 按基形派发（见 §6 勘误 2），注册基形模型即自动覆盖孪生。
- 上游模拟器处理：`tools/llvm/sim.py:101` `models.get(insn.id)` 为 None 即
  `raise "has no model"` → `sim_world_driver.py:599` 包装成
  `upstream_simulator_failure`。模型接口
  `model(R, flags, op) -> ({reg: val}, flags)`，`o['s']/o['r']` 为 pair 偶寄存器号
  （check.operands：pair→2*idx）。

## 2. SDK 侧语义：math_fast_funtion.c 的 copex 模式库（[SDK IR]）

参考 SDK（gitlab 701n @67328f4，路 CJ 钉死）`system.a/math_fast_funtion.c.o`
的 LLVM IR 逐函数提取内联汇编 `"$0 = copex($1) ($2)"` 的模式号——
**这是 JieLi 自己的协处理器 DSP 库，模式号即语义**：

| mode | SDK 函数 | 语义 | mode | SDK 函数 | 语义 |
|---|---|---|---|---|---|
| 0 | sin_fix+cos_fix | 定点 sincos 对 | 13/14 | float_to_int / int_to_float | f2i / i2f |
| 1 | angle_fix | 定点 atan2 | 16 | sin_float+cos_float | 浮点 sincos 对 |
| 2 | complex_abs_fix | 定点 hypot | 17 | angle_float | 浮点 atan2 |
| 3 | sinh_fix/cosh_fix | 定点 sinh/cosh | 18 | complex_abs_float | 浮点 hypot |
| 4 | atanh_fix | 定点 atanh | 19 | sinh/cosh_float | 浮点 |
| 5 | complex_dqdt_fix(+limit) | 定点 dq/dt | 20 | atanh_float | 浮点 |
| 6 | exp_fix | 定点 exp | **22** | **exp_float** | **浮点 e^x** |
| 7 | ln_fix/log10_fix | 定点 ln/log10 | 23 | ln_float/log10_float | 浮点 ln/log10 |
| 8 | root_fix | 定点 sqrt | 24 | root_float | 浮点 sqrt |
| 9/10 | mul_fix / div_fix | 定点乘/除 | 25/26 | mul_float / div_float | 浮点乘/除 |
| 11/12 | add_float / sub_float | 浮点加/减 | | | |

- 0..10 定点族与 16..26 浮点族**恰差 16**——与 isa notes「bit4 有作用」互证：
  bit4=浮点/定点选择位 [推断]。
- `exp_float`（IR 行 529-549）全形状：`fmin(31.f)/fmax(-32.f)` 钳位 →
  `zext i32→i64`（**上字清零**）→ `copex(22)` → `trunc i32`（**下字取出**）→
  bitcast 回 float。与固件 0x606a8fc 叶子 `r2=r0; r3=0; copex(r3_r2)(22);
  [r1]=r2` **逐指令同形**。
- app 的 mode-22 家族（全部 10 个真码站点均为 exp 包装）大多把钳位编译成
  硬件 fmax：0x6027b68/0x606600c 钳 **-32.0（0xC2000000，与 SDK 下钳常数
  一致）**、0x602f9a6/0x6094628 钳 4.0、三处钳位值调用方栈传；仅 0x602a90a
  与停点 0x606a900 两处是裸包装（依赖调用方有界输入）。

## 3. 硬件侧验证（AC791N 捕获，非 BR28——只证 ISA 层）

`hw/fm1-2026-10-24/*.jsonl` 共 364 条 copex 捕获，按模式重分析：

- **mode 22（6 条，全部有价值）**：
  - `e^0 → 1.0` ✓；极小输入（±1e-19/2.5e-29）→ 1.0 ✓ ×3；
  - `e^1.0 → 0x402df853(2.7182815075)`——**比 float32 正确舍入
    （0x402df854）低 1 ulp**：硬件是非正确舍入的近似实现（codex 修正：仅证
    非正确舍入，推不出多项式结构与全域误差包络；且此条源上字=1.0 非零、
    非 SDK 包装形状，互证有限）；
  - 域外（x=-1.58e29）→ 1.0（疑似默认/残留值）——SDK 钳位正是为规避此域，
    **不能当域外规则建模**。
  - dst 上字：5 条 0x1ffffff5、e^1.0 条 0x2b7e14f2——**非常数**
    （codex 修正本路初稿的「常数」误判），且 isa notes 的 mode-15 历史依赖
    未对 mode 22 排除。
- **mode 23（6 条中 3 条有价值）**：ln(极小)→host-ln±1ulp ✓、ln(0)→-inf ✓、
  ln(另一极小)→+22.87 域外异常。
- **无信息量**：mode 16 输出全 1.0、mode 17 输出≈src、mode 24 仅 src=0 吻合、
  mode 0 输出钉 (0x20000000,0)——探测输入不在有效域。与 isa notes
  「No numerical or lookup-table model」结论一致：**上游没建模型的原因是
  采样不足以钉公式，而不是指令没语义**；本路补上了 SDK 具名这一侧。

## 4. 全 app 站点扫描（codec 全对齐偏移解码）

钉死 codec（mcpu=r3）扫全部对齐偏移：104 处 copex 解码命中，手工核验邻域
剔 6 处伪解码 → **真码 98 处**：

- 伪解码 6 处：mode 67 ×4（0x60a5410/0x60a55d8/0x60a66e4/0x60a6a74，
  浮点常数表区，0x3f3f 位型邻域+no-decode 洞）、mode 72 ×1（0x601a7f4，
  `call -7170` 四字节指令中段错位）、mode 124 ×1（0x60845d6，邻域指令流
  不合理，疑数据 [疑]）。
- **真码模式分布**：`24:33（sqrt 浮点）、16:18（sincos 浮点）、17:14（atan2）、
  22:11（exp）、23:11（ln/log10）、0:9（sincos 定点）、6:1、7:1`——
  **全部 8 个模式都有 SDK 具名语义**。
- 覆盖率：只建 mode 22 → 11/98=11.2%（但含当前停点+eq/tone 的 dB 换算全链）；
  22+23+24 → 55/98=56.1%（eq/wdrc 系数设计域）；8 模式全建 → 100%。
- 域归属（按调用链钉死/[推断]标注）：
  - **eq/wdrc 系数域**（停点域）：db2mag(0x606a908) 25 调用点——eq 设计
    0x606feba/0x606ffbc/0x6072c92..0x6072fd0（9 处）、解码器库 0x6030f2c（7 处，
    WTS/AAC plug 0x60318e0 同库 [推断]）、tone 0x600bcbe（2 处）、app 杂项
    0x60118ea/0x6013b52/0x601e4e0（6 处）；eq 设计族内联 mode 24/16 站点
    （0x606a936、0x6072d16 等）在 22 之后。
  - **tone 振荡器域**：0x600c50c/0x600c586 mode 16（D2ANC 路已钉的 tone
    停点正是 0x600c50c）。
  - **通用 DSP 数学簇** [推断]：0x6027ff4-0x602ac78（33 站点，16/17/22/23/24
    混合）+ 0x60a9d2a/0x60a9d96 + 0x60be7aa。
  - **定点 sincos 簇**：0x6096418..0x60980dc + 0x607f790/0x60917ee mode 0
    成对使用（`>>5` Q 格式缩放）。
- 勘误（对 peripheral-input-simulation.md §5 的旧开放项）：「系数计算全用
  mode 22」不成立——mode 22 只覆盖 exp 换算，eq 设计族还有 16/24 内联站点；
  **解锁 22 后重开路径的下一停点大概率是 mode 24（sqrt）**（design 的
  omega/Q 计算），届时按同一预案扩展。

## 5. 三选一裁决（codex 定稿：收窄版 a）

| 方案 | 裁决 | 依据 |
|---|---|---|
| (a) 本地 copex 语义建模 | **推荐，限 mode 22、显式近似假设、conditional 默认关** | 保留输入→输出计算关系（非 NOP）；上游提交与源码不动；可限定范围/关闭/回归；k2 先例证明 adapter 机制可用（但 k2 的证据强度不自动转移给新模型） |
| (b) 具名 fixture | 主推进排除 | 对 db2mag 这类连续计算型读值喂常数=变相越过输入；仅适合精确捕获重放/反事实臂 |
| (c) fork/改上游 | 当前不推荐 | 不增加语义证据；改缓存破坏 pin 干净检查，fork 另起维护；将来证据成熟可上游贡献 |

- **NOP 红线判定**：按真实输入计算指数、写结果、保持已证 flags 行为、
  不支持处停机=「逐步补足模型」，不触红线；但证据等级必须标
  **conditional mathematical approximation / derived-not-measured**，
  不得称「已认证 architectural 模型」。mode 22 双源（SDK 具名+捕获吻合）；
  16/17/24 等单源 SDK **不自动够**——需逐模式审参数序/单位缩放/上下字含义/
  特殊值，另案处理，本路只裁 mode 22。
- **域外输入**：在任何架构状态提交前抛专用停点（codex 建议区分
  `dsp_mode_not_modeled` / `dsp_domain_boundary` / `dsp_operand_shape_not_modeled`）。
  契约限 `n==22`（**不用 n&31 推广**——硬件等价证据未覆盖控制高位组合）+
  源上字已知零 + 低字有限 float32 + 声明域内。模仿硬件输出 1.0（单条域外
  样本推不出规则）与 SDK 式自动钳位（把固件没做的运算塞进指令模型）均否。
- **精度交代**：低字用显式版本数学近似（host expf 或 float32(exp)，记录
  实现与舍入策略）；三镜像 parity 只证相对行为不证共同模型误差——加做
  ±1 ulp 扰动敏感性实验；AC791N≠BR28 caveat 全程标注。
- **dst 上字**：非常数、历史依赖未排除——codex 建议表示为带 COPEX 来源的
  「不透明结果」（允许搬运/保存恢复、数值消费即停）或维持边界停；不能写
  假常数。现状 br28_unknown.py 的 UnknownWord 强制物理内存来源、effect()
  拒绝抽象模型输出——两处须先适配才能走「不透明」路线。

## 6. 实施形态与勘误（codex 修正，本路逐条核实成立）

新建 `tools/br28_dsp_models.py` 暴露 `conditional_copex_exp22(models)`，
与 `architectural_k2` 同级合入；registry 走 conditional 层（先例
exact_doubleword_stores / mmu_cross_page_split）。codex 钉的六个坑：

1. **上字非常数**（followup-q2.jsonl:135-140 复核成立）——见 §5。
2. **`.p` 孪生自动走基形模型**（`tools/llvm/sim.py:117-121` pair() 以
   `insn.id[:-2]` 查 models）——「不注册 .p 保持诚实停」不成立；须在并行
   派发处显式拒绝未支持组合（覆盖 .p 作前半/后半两种位置，两半副作用
   均未提交时拒绝）。app 真码 .p 零站点，故此为完备性守卫不是当前风险。
3. **接入点**：整机前沿 CPU 在 `probe_boot.py:466-471` 构造并独立应用 k2
   （probe_pi32v2.py 另有 139/513/1127/2154 四处）——安装逻辑必须共享且
   全点位覆盖，否则 p11_heap_experimental 场景接不到模型。
4. **异常路由**：上游 effect() 把 ValueError/KeyError 转报「model needs the
   hardware run's context」（sim.py:109-110）——域边界须用不被该 except
   捕获的专用异常并接入独立 stop kind，防止误报 upstream_simulator_failure。
5. **冲突检查**：启用 adapter 时显式检查上游是否已提供 copex 模型，
   发现即**报冲突要求重审**（不静默让位/遮蔽——codex 与本路一致反对
   自动让位：静默改变模型含义比报错危险）。
6. **快照契约**：world_snapshot.py:58 FINGERPRINT_FILES 是显式枚举——
   新文件+开关+不透明结果序列化都要纳入，不会自动覆盖。

验收清单（落地路执行）：①先写 mode 22 契约+证据表（修正上字/多项式/ulp
三处表述）②上字表示与消费边界先行 ③整机接入+registry+冲突检查+.p 拒绝+
快照指纹 ④测试臂（正常/域边界/未知输入/他模式/.p 两位置/源目标重叠/上字
运输消费/冲突拒绝；失败零部分提交）⑤关开关复现 42,037,363 @ 0x606a900、
开开关记录每次输入输出与下一诚实停点；镜像 parity+敏感性+从零回归；
五条 archived r2 拒绝保留。

## 7. 诚实边界与开放项

- AC791N ≠ BR28：硬件捕获只证 ISA 层协处理器行为；BR28 的数值细节
  （舍入/全域误差）无真机数据——模型保真度天花板即此，标
  derived-not-measured。
- mode 22 上字的历史依赖未排除（isa notes mode 15 有前科）；app 全部
  mode-22 站点丢弃上字（`[r1]=r2` 只存下字），上字行为对 app 不可观测
  ——不透明表示可行，但消费边界要按 §6.1 接线。
- 停点世界的 db2mag 输入域（EQ 段增益 dB → x∈[-10.4,+4.5]）在有效域内是
  静态推断；运行时契约仍须域检查（codex：静态配置范围不能替代运行时检查）。
- 站点域标签中 math 簇/解码器域/wdrc 域为 [推断]（地址邻域+模式组合），
  未逐函数对 SDK；不影响停点链结论（eq 域链是静态 BFS 钉死的）。
- 下游预判（供下路）：解锁 mode 22 后，eq 重开路径下一停点预期 mode 24
  （sqrt）；tone 场景（D2ANC 停点 0x600c50c）需要 mode 16；数据相三重墙
  （LEN 首读/SWN/PNS）仍在更后。

## 8. codex 参谋记录

gpt-6-astra，2026-11-18 18:43-18:46，
`~/tmp/codex/202611181843-copexscout-adapter/`（prompt.md/final.md/log.md）。
裁决原文要点：「建议选收窄后的 (a)：本地、默认关闭、仅 mode 22 的指数函数
近似模型，归入 conditional。域外输入诚实停；其他模式另案审查。它可以支持
条件性前沿推进，但现有证据不足以称为 BR28 精确指令语义。」三处方案修正
（上字非常数/.p 派发/接入点）本路逐条到代码核实成立（§6）。本路可验收的
结果应是：「在显式指数近似假设下推进了多少步、遇到什么新边界。」
