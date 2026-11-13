# EQ 段增益 ±18dB 是算法硬 clamp 还是注释约定（路 CN / sim-eqgain，只读调研）

2026-11-13。本页只回答路 CJ [docs/hearing-aid-eq-feasibility.md] §5 第 1 条关键未知：EQ 参数
`gain` 字段（float，SDK 注释标 -18~18）换算成运行时系数时，算法里到底有没有 clamp 到
±18dB；顺带验证 WDRC inputgain/outputgain 有没有 clamp、上限多大；以及定点系数格式的真实
数值上限。**只读**：未改 tools/ 任何代码、未跑改机器状态的 probe。证据来源是仓库 bootstrap
钉死的参考 SDK（commit 67328f4）编译产物 IR（media.a / lib_eq_design_coeff.a / drc.a /
system.a 的 LLVM bitcode，用 llvm-dis 反出 .ll），加 SDK 源文件（gitlab raw 同 commit）。
**红线 caveat：SDK 是参考材料，不是精确 QCY 源**——QCY 固件编译进的是同款节点库（feasibility
§3.2 已钉 pipeline uuid/任务名等），但本页每个结论只对「参考 SDK 的该库实现」成立，QCY 镜像
是否打了 patch 需另行核对（目前无任何 patch 迹象）。

证据强度：**[IR]** = SDK 编译产物 LLVM IR（钉死 archive SHA）；**[SDK]** = SDK 源文件；
**[推断]** = 由上述推出。

## 0. 一句话结论

**±18dB 是 (b) 纯注释约定，算法里没有任何 clamp 到 ±18dB 的代码。** 从 float gain 到 biquad
系数到存储，全程无 `min/max/clamp/if(gain>18)`。EQ/WDRC 的增益系数是 **float32（非定点）**，
真实数值上限由 `exp_float` 内部的输入钳位 [-32, +31] 决定：全局增益/WDRC inputgain 约
**+269dB**、段增益（peaking/shelf 的 10^(g/40)）约 **+538dB** 才饱和。39dB 完全在范围内
（10^(39/20)=89.1、10^(39/40)=9.44）。算法里唯一的增益钳位是**下限 -90dB**（只在下文 §1.3
的数据通路 Qval==15 时把 global_gain 钳到 ≥-90dB，防哑音），方向与「39dB 上限」无关。

## 1. EQ gain 处理链（全程无 ±18 clamp）

float gain（dB）→ biquad 系数的完整链路，逐环节核查：

### 1.1 参数入口：只赋值，不 clamp [SDK]

- `SDK/audio/effect/eq_config.c:306` 的 doc 注释 `@param seg->gain:总增益(-18~18)` 是
  **注释约定**；同文件 `eq_mode_set_custom_seg`（~316 行）对 `struct eq_seg_info` 做
  `memcpy` 原样拷贝，`eq_mode_set_custom_info`（364 行）/`eq_mode_set_custom_param`（386 行）
  直接 `seg->gain = gain`。三者都没有范围判断。
- `SDK/interface/media/cpu/br28/asm/hw_eq.h:53` 定义 `struct eq_seg_info { i16 index,
  i16 iir_type, i32 freq, float gain, float q }`——**gain 是 float**，结构体注释里没有
  范围限制；`design_pe/ls/hs` 的 doc 注释只写 `gain:增益`（215/235 行附近），没有 -18~18。

### 1.2 jlstream 节点入口：唯一钳位是下限 -90dB [IR]

`media.a/eq_node.c.o`（jlstream 的 eq 节点适配器）解析 `%struct.eq_tool = { float, i32,
[0 x %struct.eq_seg_info] }`（`eq_node.c.o.ll:23`），把 global_gain/各段 gain 原样传给
audio_eq。全文件只有一处 gain 钳位（`eq_node.c.o.ll:973-980`，内联的 audio_eq_open 路径，
条件 `Qval==15`）：

```llvm
%cmp34.i = fcmp olt float %19, -9.000000e+01   ; global_gain < -90 ?
br i1 %cmp34.i, label %if.then36.i, ...         ; 是 → 钳到 -90
if.then36.i:
  store float -9.000000e+01, ptr %global_gain.i ; 下限钳位 -90dB
```

即：**只有 global_gain 的下限 -90dB 钳位，没有 +18dB（或任何正值）上限**。`Qval==15` 指
数据通路是 Q1.15（16 位 PCM），-90dB ≈ 2^-15 的数据分辨率底，低于它增益无意义（全零哑音），
所以固件钳到 -90dB 作静音下限。这与「+39dB 上限」正交。

### 1.3 系数换算：gain 直接进 biquad 公式，无 clamp [IR]

`media.a/audio_eq.c.o` 的 `eq_seg_design`（`audio_eq.c.o.ll:1450`）按 `iir_type` 分发：

| iir_type | 目标函数 | gain 用法 |
|---|---|---|
| 0/1 | design_hp / design_lp | 无 gain（只用 Q） |
| 2 | design_pe（带通/peaking） | gain 直接入公式 |
| 3/4 | design_hs / design_ls（搁架） | gain 直接入公式 |
| 5/6 | design_hs_Q / design_ls_Q | 同上 |
| 7/8 | butterworth_hp/lp_design | gain 被 `fptosi` 当**阶数**（非 dB） |

`eq_seg_design` 只做一件事：`%gain = load float` → 原样传进 `design_pe(freq, fs, gain, q,
coeff)`（sw.bb151/155/159 处），**没有对 gain 做任何 fcmp/select/clamp**。算完系数后调
`eq_stable_check`（`eq_design.o:414`）只判 |极点|≤1，不稳定就用 `eq_get_AllpassCoeff`
替换成全通（中性），这是**稳定性兜底，不是增益上限**——peaking/shelf 滤波器任意增益下极点
都在单位圆内，不会因此被拒。

实际 biquad 公式在 `lib_eq_design_coeff.a/eq_design.o`：

- `eq_db2mag`（`eq_design.o:63`）：`mag = exp_float(db * 0x3FBD791C60000000)`，其中
  `0x3FBD791C60000000` = ln(10)/20 ≈ 0.115129，即 **10^(db/20)**。无 clamp。
- `design_pe`（`eq_design.o:162`）：`A = exp_float(gain * 0.5 * ln10/20) = 10^(gain/40)`
  （peaking 的标准幅度），用它算 b0/b1/b2/a1/a2。无 clamp。
- `design_ls`（`eq_design.o:225`）/`design_hs`（`eq_design.o:322`）：同样 `A = 10^(gain/40)`。
  无 clamp。

### 1.4 系数存储：5 个 float32/节 = 20 字节，非定点 [IR]

- `media.a/audio_eq_fade.c.o` 的 `eq_lr_mem_update`（`audio_eq_fade.c.o.ll:1860`）：按
  `nsection * 20` 字节分配系数内存（`mul145 = mul i32 %add144, 20`），每节 20 字节 = 5 个
  float32（b0/b1/b2/a1/a2）。
- `media.a/hw_eq.c.o` 的 `audio_hw_coeff_reduction`（`hw_eq.c.o.ll:362`）：每节 `memcpy
  20 字节`，无 float→定点转换。
- `media.a/hw_eq.c.o` 的 `audio_hw_eq_ch_set_coeff`（`hw_eq.c.o.ll:1212`）：L_gain/R_gain/
  N_gain 逐个 `eq_db2mag` 后存 float，无 clamp。
- `hw_eq.c.o` 全文**没有任何 fptosi/sitofp**——硬件 EQ 加速器（JL_EQ）吃的是 float 系数，
  不是定点系数。

**所以「biquad 系数用几 bit 定点」这个前提不成立**：系数是 float32，不是定点。feasibility
§1.3 说的「运行时换成定点 IIR/增益系数」对 EQ 系数不成立（EQ 域是 float；Q1.15 是**数据**
通路格式，只影响 §1.2 的 -90dB 下限，不影响系数）。

### 1.5 真实上限：exp_float 的输入钳位 [-32, +31] [IR]

`system.a/math_fast_funtion.c.o` 的 `exp_float`（`math_fast_funtion.ll:529`）开头一段内联
汇编：

```llvm
$1 = 31.f ; $0 = fmin($1,$0)   ; x = min(31, x)
$1 = -32.f; $0 = fmax($1,$0)   ; x = max(-32, x)
; 再 copex（硬件 exp 协处理器指令）算 e^x
```

即 `exp_float` 把输入钳到 [-32, +31] 后做 e^x。据此：

- `eq_db2mag`/`drc_db2mag`（10^(db/20)）在 **|db| ≈ +269dB（正）/ -278dB（负）** 处饱和；
- `design_pe/ls/hs` 的 A=10^(gain/40) 在 **|gain| ≈ +538dB** 处饱和。

两者都远大于 18dB。39dB 时 10^(39/20)=89.1、10^(39/40)=9.44，都在 float32 正常范围内，
不会触发 exp 饱和，也不会触发稳定性兜底。

## 2. WDRC inputgain/outputgain 换算链（无 clamp）[IR]

- `media.a/audio_wdrc.c.o` 的 `wdrc_init`（`audio_wdrc.c.o.ll:632-642`）：`inputgain`/
  `outputgain` 按 **float 原始位模式**载入（`load i32`→`store i32` 进 `_DrcParam.
  InputGain/OutputGain`），没有任何范围判断。
- `drc.a/drc.o` 的 `drc_db2mag`（`drc.o.ll:28`）：`exp(db * ln10/20)` = 10^(db/20)，无 clamp。
- `drc.a/drc.o` 的 `DrcInit`（`drc.o.ll:991`）与 `DrcUpdate`（`drc.o.ll:1391`）：把
  `InputGain`（float dB）经 `drc_db2mag` 换成线性 float 存到内部状态 offset 88，**outputgain
  按原始 float 存 offset 92**（在别处应用）。无 clamp。

**WDRC inputgain/outputgain 上限**：和 EQ 一样走 `exp_float`，10^(db/20) 在 **~+269dB** 才
饱和；float 参数区里写几十 dB 完全没问题。CJ 说的「可大 dB」成立，上限不是 ±18 也不是小值。

## 3. 对 R2「1kHz +39dB」的结论与建议

- **39dB 不会被算法拦**：放 EQ 段增益（iir_type=2 peaking @1kHz）、或 WDRC inputgain、或
  global_gain 都行，float 系数域全程放行，无 ±18 clamp。
- **真正的约束在输出端，不在增益端**：39dB 单段 peaking 会把整段频带放大约 90 倍，数字输出
  必然削顶，除非被输出限幅链（WDRC out_threshold + data_saturation(0x6b29) + DAC_VL0
  上限，见 feasibility §4）压住。所以「39dB + 整机限 ~100dB SPL」在算法侧 = **把 39dB 放
  增益端 + 把 out_threshold 设成数字域天花板**，与 CJ 的 L0 主方案一致。
- **「100dB SPL」仍无固件标定**（feasibility §4 结论不变）：只能做数字域绝对上限，真机一次
  声压测量才能把数字 dB 对到 SPL。
- 建议 R2 落地时把 39dB 拆成「宽带增益（WDRC inputgain 或 global_gain）+ 1kHz 整形（EQ 段）」
  组合，而不是单段 peaking 全押 39dB——这样限幅更好控制，也和 CJ 的替代路径一致。

## 4. 证据锚点

- SDK archive（钉死 SHA）：media.a = 6d1c5ed5…、lib_eq_design_coeff.a、drc.a、system.a
  （commit 67328f4，`tools/bootstrap.py` 与 `data/dependencies.json` 钉死；本路额外拉取
  lib_eq_design_coeff.a/drc.a/system.a 到本地 tmp，不进库）。
- EQ gain 链：eq_config.c:306（注释）/~316（memcpy）/364/386（直接赋值）[SDK]；
  eq_node.c.o.ll:973-980（-90dB 下限）[IR]；audio_eq.c.o.ll:1450 eq_seg_design + 162
  design_pe + 225 design_ls + 322 design_hs + 63 eq_db2mag + 414 eq_stable_check [IR]；
  audio_eq_fade.c.o.ll:1860 eq_lr_mem_update（20B/节）[IR]；hw_eq.c.o.ll:362 coeff_reduction
  （memcpy 20B）+ 1212 set_coeff（eq_db2mag）+ 无 fptosi [IR]；math_fast_funtion.ll:529
  exp_float（[-32,31] 钳）[IR]。
- WDRC 链：audio_wdrc.c.o.ll:632-642（inputgain/outputgain 原样）[IR]；drc.o.ll:28
  drc_db2mag + 991 DrcInit + 1391 DrcUpdate [IR]。
- 验证：`python3 tools/check_local.py --units` exit 0（--fetch-inputs 首次拉取后本地通过）。
