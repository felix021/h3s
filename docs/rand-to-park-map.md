# RAND→park 停点预测：JL_RAND 放行后的最后一段（路 sim-parkpre，只读静态预研）

2026-11-16 17:5x。基线 main `667cc22`（worktree `sim-parkpre`，分支 `sim-parkpre`）。本页是
S19MAP（docs/s19-endgame-map.md）的续篇：前沿已到 **28,890,702 @ 0x6011976 读 0xfd3b00**
（JL_RAND R64L 随机种子首读，RAND 路在途放行），放行后到 park 还剩哪些停点、是否还有未建模
地址族，一张表回答。**只读**：未改 tools/、未改 HANDOFF 前沿表、未跑任何改动场景；一次性脚本在
`~/tmp/parkpre/`（不进仓）。

**方法与证据强度**：反汇编=仓库钉死 pi32v2-isa codec（pin `3608ed1`）+ 官方 app.bin
（sha `18f3d573…`，load base 0x06000100），沿用 S19MAP 三件套（calls.tsv 全量调用边 17,586
条、fentries.pkl 函数边界 4840 入口、li 枚举）。新增两条：**全 app 4 字节对齐 SFR 常量扫描**
（`0xfe0000..0xfe2fff` / `0xfdxxxx` / `0xf2xxxx` / `0x12xxxx`，35 个 hs 基址）与
**walker 表逐项直接调用闭包扫描**（W1/W2/W3/W4 + config-init，130 函数）。
**[静态·本路]** = 本路新钉；**[文档]** = 已合并条目（S19MAP/AG/AY/BA/BM/BH）；
**[运行·RAND路]** = RAND 路运行臂读数（~/tmp/rand-advance-result.json / rand-arm-*.json，
三臂种子 0/0x13579bdf/0x12345678 全部 29,215,313 步停同一 PC）；**[推断]** = 上述链推出。
全部 derived-not-measured。

## 0. 一句话结论（三问各一句）

1. **还剩几个停点**：放行 JL_RAND 后**还剩 1 个**——`0x6072b92 st.w [0xfe1500]=0`
   （JL_EQ CON0 写，值 0），RAND 路三臂已实测同停（29,215,313 步，种子无关）；该停点
   所在函数 `0x6072b24`（EQ 初始化）还连写第二寄存器 `0x6072bae → 0xfe151c`（FLT_ADR）。
   JL_EQ 族（0xfe1500..0xfe151c，8 个 __RW 字）建模一次覆盖，**之后按静态预测一次推进到
   park**（S19MAP #6-#15 全段已映射/已落地）。
2. **ANC 图门是否提前触**：**不触**。ANC 图门函数 `0x601bd3a` 的调用者只有
   `0x601d066`/`0x6026b18`（两处 handler 域），ANC 访问器 `0x606ee26` 的 4 个调用者
   （`0x601bdbe` 在图门内、`0x601c92a`、`0x602d56a`、`0x602d5ea`）全在 handler 域；
   boot 路径（W1→cfginit→S9→W2→W3→W4→S11-S19）闭包内**无任何 0xfe0400 族站点**，
   S19MAP #3 判定在 JL_RAND 之后仍成立。
3. **必做裁决有无遗漏**：**有且仅有一处遗漏 = JL_EQ 族**。S19MAP #7 把 W2[8]（`0x607bf2e`）
   归入「RAM+IPI 否」，漏判其 EQ 初始化子树对 JL_EQ 的写——根因是 **JL_EQ 基址 0xfe1500
   不在任何 li 常量里**（全 app 仅 1 处出现，在 .data 表 `0x60c3748`，经 RAM 描述符
   `[0x12727C]+16` 间接装载，静态 li 扫描免疫）。其余数据驱动族（JL_SRC 0xfe1600、
   JL_SRC_SYNC 0xfe2600、JL_SYNC 0xfe2500、JL_WL 0xfe2100、JL_WL_AUD 0xfe2200）全在
   app/音频/DSP handler 域（读者 `0x603a880`/`0x607aafa` 等不在 boot 闭包内），ANC/ADDA
   族维持「条件不可达」。

## 1. 当前停点 → 下一停点解剖（0x6011976 读 0xfd3b00 → 0x6072b92 写 0xfe1500）[静态·本路 + 运行·RAND路]

### 1.1 JL_RAND 消费面复核（沿 rand-pause.md）

`rand_bytes` [0x601196a,0x60119ca) 逐条反汇编复核：`0x6011970 li r0=0xfd3b00` →
`0x6011976 ld.w [r0]`（种子首读=停点）→ `0x601197a/197e li r8=16807, r7=0x7fffffff`
→ `0x6011988..199a` 64 位乘/除模（Lehmer MINSTD `state=(state*16807) mod (2^31-1)`）
→ `0x601199e lsr r0=r10>>15` 出 16 位 → `0x60119a2 st.h [sp+2]` → `0x60119b6` ROM memcpy
逐 1-2 字节。种子每次调用读一次，与 rand-pause.md 全对。

**boot 路径上两次调用**（RAND 路 watch 计数 `0x601196a=2, 0x6011976=2`）：`0x6011c64`
（MAC 首生成，`0x6011c3a syscfg_read(102,sp+10,6)` 得擦除态 → `0x6011c48 memcmp` 六 FF →
`0x6011c64 rand_bytes(6)` → `0x6011c6e syscfg_write(102)`）+ `0x6011c92`（`0x6011c86
memcpy` 失败臂重生成）。两臂输出只进数据路径（`0x6011ca8..caa` 拷进 `r8+520` 全局 MAC、
`0x6011cb8` 打日志、`0x6011cc2` 读 id 25/26），本函数内零值敏感分支——与 rand-pause.md
「只进数据路径」一致。

### 1.2 下一停点 = JL_EQ CON0（0xfe1500）写

**运行定位（RAND 路三臂一致）[运行·RAND路]**：zero/sensA/advance 三臂（种子
0/0x13579bdf/0x12345678）全部 `completed_steps=29,215,313`，停 `stop_pc=0x6072b92`，
`kind=unmapped_or_readonly_write, access=write, address=0xfe1500, size=4, value=0`。
`rand_stop_registers r0=0xfe1500 r5=0 r6=0x32f(=815) r4=0x1265c0`。

**静态链 [静态·本路]**：

```
walker 主驱动 0x6012242 W4 表循环前，W2 表 [0x60C3250,0x60C327C) 11 项
 └─ W2[8] = [0x60C3270] = 0x607bf2e
     └─ 0x607bf40 call.xl 0x6072b24        ← EQ 初始化
         └─ 0x6072b78 r1=[0x1265c0+815<<2]=[0x12727C]   ← EQ 硬件描述符指针
         └─ 0x6072b90 r0=[r1+16]                          ← 描述符 +16 槽 = 0xfe1500
         └─ 0x6072b92 st.w [r0+0]=r5                      ★停（r5=0，CON0 清零）
         └─ 0x6072bae st.w [r0+28]=r1                     ← FLT_ADR(0xfe151c)=分配缓冲指针
             （0x6072baa r1=[r0+8]=缓冲，0x6072bac r0=[r0+16]=0xfe1500 重载）
```

`0x6072b24` 体的完整流：读 `b[0x1265c0+3264]` 选 EQ 实例（×24 进 `0x124c40` 表）→
`0x6072b50 call 0x6065d86`（request_irq，RAM/IPI）→ 初始化 `0x127268` 实例链表 →
`0x6072b64 b[r1+3]=20` → `0x6072b6e call 0x6065c3a`（分配 640B）→ `0x6072b86/0x6072b9c
call 0x1fd80`（ROM memset）→ **两处 JL_EQ 写**（CON0=0、FLT_ADR=缓冲）。boot 路径上
**只写不读**，且 CON0=0 是「EQ 清零/旁路」的无害初值，FLT_ADR=RAM 指针（`0x12xxxx`，
模型自有分配器自然产生）。

### 1.3 JL_EQ 是「数据驱动」地址族（本路新钉，S19MAP 漏判根因）

全 app 4 字节对齐扫描：`0xfe1500` 在 app.bin 中**仅 1 处**出现 = `.data` 段 `0x60c3748`
（EQ 硬件描述符模板，随 .data 拷贝进 RAM），**零 li 常量**。对比：JL_ANC 0xfe0400 有 7 个
li、JL_AUDIO 0xfe0500 有 15 个 li——所以 S19MAP 的 li 枚举法能兜住 ANC/AUDIO 族，唯独
漏了 EQ（以及同样数据驱动的 SRC 0xfe1600/SRC_SYNC 0xfe2600/SYNC 0xfe2500，见 §3.3）。
机制与 FLS 的 flash 顶间接指针同型（S19MAP §1 归属修正），是「间接指针静态 walker 免疫」
的第二次应验。

## 2. JL_RAND → park 预测表（29.2M → ~37-39M）

「裁决」= 需要新路做三选一决策；「难度」低=套先例/写白名单族、中=窗口/fixture 设计已备。

| # | 段/站点 | PC / 地址 | 初分类 | 裁决? | 难度 | 步数区间 | 先行证据 |
|---|---|---|---|---|---|---|---|
| 0 | **JL_RAND 种子首读（当前）** | 0x6011976 读 0xfd3b00 | ls 域 R64L，__RO 熵源 | **是（RAND 在途）** | 中（具名 fixture，值=0x12345678/0x13579bdf 二选一，零臂诊断） | 放行后 +3.2×10⁵ | rand-pause.md + codex 定调 |
| 1 | config-init 其余尾段 | 0x6011c64/0x6011c92 之后至 0x6011d1c 返回 | RAM/VM（syscfg_read/write、id 25/26 日志） | 否 | — | +10²-10³ | [静态·本路] §1.1；RAND 路 watch 见两处 rand_bytes 均已过 |
| 2 | S9 表项 + W2[0..7] | 0x6000e94/0x6001866；0x6004360(GPIO+电压+6 tick 事件钟)/0x60074e6/0x601043a/0x6027658/0x602f5a8/0x603346e/0x6033678/0x603371e | RAM/GPIO/事件时钟（全建模） | 否 | — | +2-3×10⁵ | [文档 S19MAP #6-#7]；本路 walker 闭包扫描零未建模 SFR li；三臂实测 JL_RAND→JL_EQ 全程 +3.2×10⁵（含 #1-#3） |
| 3 | **W2[8] EQ 初始化（JL_EQ 族）** | 0x6072b92 写 0xfe1500=0；0x6072bae 写 0xfe151c=缓冲指针 | hs 域 JL_EQ（0xfe1500..0xfe151c，8 个 __RW 字，数据驱动） | **是（本路新钉）** | 低-中（RW SFR 族白名单，套 JL_AUDIO/JL_ANC 先例；只写不读、写值无害） | +10² | [运行·RAND路] 三臂同停；[静态·本路] §1.2 |
| 4 | W2[9]/W2[10]/W3[0] | 0x60759ee(jlstream)/0x607f8be/0x60009e8 | RAM+IPI | 否 | — | +10⁴-10⁵ | [文档 S19MAP #7] |
| 5 | W4[0..5] | 0x600ec2e/0x600ecc6/0x600ed30/0x600edf8/0x6034c80(VLVD/PVDD/trim)/0x6066f90 | RAM/VM；W4[4] 三开关已落地（0xfd5008 GPIO/0xf2c244/0xf27f92/0xf2c10c 全建模） | 否（预解决） | — | +4-6×10⁵ | [文档 S19MAP #8-#10] |
| 6 | S15 喂狗邮箱 → S16 充电/事件钟 → S17 电池环 | 0x12CC94；cfg523@0x1274f4 | P2M 邮箱 + cfg523 fixture | **已落地（B2R+B2G）** | — | +8×10⁶ | [文档 S19MAP #11-#13] |
| 7 | S18 → S19 消息循环 → park | 0x60123aa/b2；(20,2) 特路 | RAM/OS 队列 | **已落地（ID2）** | — | +10²-10³ | [文档 S19MAP #14-#15] |

**park 预算**：29.2M +（#2）+（#4-#5）≈ 1-1.5M +（#6）≈ 8M + 尾段 ≈ **+9-10×10⁶ →
park ≈ 3.8-3.9×10⁷ 模型步**（与 S19MAP §0 的 3.7-3.9×10⁷ 一致；JL_EQ 在 W2[8]，
落在 S19MAP 事件钟 ~8M 之前，不改变总量级）。

## 3. 三问详解

### 3.1 问①：JL_RAND 后下一个停点 = JL_EQ CON0（详见 §1.2）

下一个停点是**写**而非读：`0x6072b92 st.w [0xfe1500]=0`（`unmapped_or_readonly_write`，
JL_EQ CON0，值 0）。放行后 LCG 出数、BT MAC 生成（syscfg_write 102）走通，config-init
返回 → S9 → W2[0..7]（全建模）→ W2[8] EQ 初始化首个 SFR 写即停。三臂种子不同而停点/步数
全同，说明「boot 前缀对种子值不敏感」的敏感性对照预期成立（码出点见 §1.1，只进数据路径）。

### 3.2 问②：ANC 图门在 JL_RAND 后仍不触 [静态·本路]

- ANC 图门函数 `0x601bd3a`：`0x601bd42 r0=[0x1265c0+464]` → `0x601bd4a beqz`（[QCY+464]==0
  即整段跳过）。其调用者**仅** `0x601d066`、`0x6026b18`——两处都读/写 `[0x1265c0+464/496]`
  ANC 配置区，属 handler 域（S19MAP §2 #3 所记 `0x601d006/0x60269a8` 之下）。
- ANC 访问器 `0x606ee26`（JL_ANC 0xfe0400 族）调用者 4 处：`0x601bdbe`（图门体内）、
  `0x601c92a`、`0x602d56a`、`0x602d5ea`——全部 handler 域。
- 反证：walker 直接调用闭包（130 函数）内**零 0xfe0400 族站点**；且 RAND 路三臂实测
  从 28.89M 推到 29.2M 停 JL_EQ，途中未停 ANC（[运行·RAND路] 旁证）。
- 结论：S19MAP #3「boot 路径上该门在 handler 域之下、W1[3] 内不触」在 JL_RAND 之后的
  W2/W3/W4 段**仍成立**；park 后事件链是否打开仍属 D2-b 挂账（S19MAP §6.3 维持）。

### 3.3 问③：必做裁决唯一遗漏 = JL_EQ；其余数据驱动族不在 boot 路径

全 app hs 基址（0xfe0000..0xfe2fff）35 个不同值，逐个对账 br28_model.py：

| 族 | 基址 | 出现形态 | 位置/读者 | boot 路径? |
|---|---|---|---|---|
| **JL_EQ** | 0xfe1500 | .data 1 处 `0x60c3748` | W2[8]→0x6072b24（`[0x12727c]+16`） | **是（本路新钉）** |
| JL_SRC | 0xfe1600 | .data `0x60a29e8` | 表 0x60a2894 读者 0x603a880（app 域） | 否 |
| JL_SRC_SYNC | 0xfe2600 | .data `0x60a29d4` | 同上表 | 否 |
| JL_SYNC | 0xfe2500/0c/18 | .data 0x60bee74.. | 读者 0x607aafa/0x607ae22/0x607b10e（DSP handler 域） | 否 |
| JL_WL / JL_WL_AUD | 0xfe2100/0xfe2200 | 代码 li | 0x6048114（BT 库）/0x60552e8/0x6069c28 | 否（post-park） |
| JL_ANC | 0xfe0400..4ff | 代码 li 7 处 | ANC 图门 handler 域（§3.2） | 否（条件不可达，模型已标） |
| JL_ADDA | 0xfe0a00/04/08/0c/40/48 | 代码 li | 0x6067f84（零 call 点）/0x606b34e/0x606e1d8/0x6073cc4 等 | 否（缺省分支/条件族，模型已标） |

**结论**：必做裁决已落地 FLS(#0)/B2R+B2G(#11/#13)/ID2(#14) 之外，**唯一新增必做 = JL_EQ
族**；ANC/ADDA 维持「0 或 1 条件项」且 boot 路径不触发，SRC/SRC_SYNC/SYNC/WL 族全部
app/post-park 域（不在 walker 闭包内，[静态·本路] 反证）。

## 4. 建议裁决方案（给前沿 RAND 路「推进 40M」阶段）

1. **JL_EQ 建模形态 = RW SFR 族白名单**（套 JL_AUDIO/JL_ASS 先例，非具名单字 fixture）：
   映射 `0xfe1500..0xfe151c` 8 个 __RW 字，默认 0。boot 路径只写不读（CON0=0、FLT_ADR=RAM
   指针），写值无害；负例=族关停时停 0x6072b92 不变。**不要**逐寄存器具名 fixture——EQ
   实例化在 app/音频域还有后续写面（`0x6072d48/0x6072e30/0x6072f24/0x6072fec` 等 handler
   域 0x6072bb2 调用族），族白名单一次覆盖。
2. **放行跑必带观察项**：RAND 路推进跑里顺带记录 `b[0x1265c0+3264]`（EQ 实例选择字节）
   与 `0x6072b66` 读的 `[r1+8]`（决定 CON0 写臂是否走到，0x6072b68 bnez 分支）——若实测
   `[r1+8]!=0` 则 CON0 写臂被跳、停点退化为 FLT_ADR 单写，负例/断言要按此分支写。
3. **park 后不追 SRC/SYNC/WL/ANC**：这些族全在 app/音频/DSP handler 域，park 前不触，
   维持 S19MAP §4.4「D1 验收只要 S19 空转+两核 park」。
4. **重钉纪律**：JL_EQ 放行过线后 FRONTIERS['p11_heap_experimental'] 单点重钉
   （从 0x6011976 到 0x6072b92），与 S19MAP §4.5 一致。

## 5. 对既有记录的修正清单

| 记录处 | 原记载 | 本路修正 |
|---|---|---|
| S19MAP §2 #7 | 「W2[1..10]/W3[0] = jlstream 创建…RAM+IPI，否」 | **W2[8]（0x607bf2e）漏判**：其 EQ 初始化子树写 JL_EQ 0xfe1500/0xfe151c（数据驱动地址，li 枚举免疫），需 +1 裁决（本表 #3）。W2[9]/W2[10]（0x60759ee/0x607f8be）复核仍 RAM+IPI |
| S19MAP §6.1 | 「0x61fbfe0 来源链=间接指针，FLS 第一课题」 | 同型第二例：JL_EQ 基址 0xfe1500 亦为 .data 间接指针（全 app 仅 0x60c3748 一处），li 枚举法对此类数据驱动 SFR 族系统性失明——建议后续预研一律加「4 字节对齐 SFR 常量全扫描」补 li 枚举 |
| rand-pause.md 消费面 | 「0xfd3b00 访问面 11 个 li 站点全纯读」 | 复核维持（本路复扫 11 站点=0x6011970/0x601b63a/0x601b6a6/0x6024646/0x6035784/0x6051ba6/0x6055f50/0x6057458/0x60582ba/0x60738a4/0x60c04e0）；其中 0x60738a4 在 DSP handler 域（非 boot 路径），boot 路径消费者=0x6011c64+0x6011c92 两处 |

## 6. 未钉死处（如实标注）

1. **「JL_EQ 之后一次到 park」= derived-not-measured**：运行证据只到 29,215,313（JL_EQ
   首写）。JL_EQ → park 的「无新停点」依据 = S19MAP #6-#15（[文档]）+ 本路 li/数据驱动族
   全扫描无未建模站点（[静态·本路]）；运行侧结论要等 RAND 路推进跑实测。
2. **间接构造地址残量**：与 ASS 先例一致，运行时算出的 SFR 指针不排除（li/数据表枚举免疫区
   内已尽）。EQ 实例选择（`b[0x1265c0+3264]`）若运行时取非 0 值，进 `0x124c40` 表其他实例
   槽，可能带出额外 SFR 基址——本路未逐槽展开 0x124c40 表内容。
3. **JL_EQ 写臂分支**：0x6072b68 `bnez` 若实测跳转，CON0 写臂被跳、只写 FLT_ADR（§4.2）。
4. **步数量级**：S16/S17 事件钟 ~8M 沿用 BA/BM tick 换算（1 tick=10ms=4×10⁴ 模型步），
   ±20%。
5. **函数边界近似**：同 S19MAP §6.6，本路全部关键边（§1.2 链）经窗口正向重解复核。

## 7. 证据锚点（复核用）

- 前沿停点：0x6011976 `ld.w [0xfd3b00]`（28,890,702 步），函数 rand_bytes
  [0x601196a,0x60119ca)。
- 下一停点链：W2 表 `[0x60C3270]=0x607bf2e` → `0x607bf40 call 0x6072b24` →
  `0x6072b78 r1=[0x12727c]`（=0x1265c0+815<<2）→ `0x6072b90 r0=[r1+16]=0xfe1500` →
  `0x6072b92 [r0+0]=0`（★停）→ `0x6072bae [r0+28]=缓冲指针`。
- walker 表全量（app.bin u32 dump）：W1[0x60C32A4,0x60C32B8)=
  {0x6012e3e,0x601321e,0x6013246,0x602ea68,0x60614a2}；S9 结构[0x60C0E20,0x60C0E30)=
  {1,0x124888,0x6000e94,0x6001866}；W2[0x60C3250,0x60C327C)=11 项
  {0x6004360,0x60074e6,0x601043a,0x6027658,0x602f5a8,0x603346e,0x6033678,0x603371e,
  **0x607bf2e**,0x60759ee,0x607f8be}；W3={0x60009e8}；W4[0x60C328C,0x60C32A4)=
  {0x600ec2e,0x600ecc6,0x600ed30,0x600edf8,0x6034c80,0x6066f90}。主驱动
  0x60121b4..0x6012254（W1 循环→0x60121ca call config-init→S9→W2 0x6012216→W3 0x601222c
  →W4 0x6012242）。
- JL_EQ 数据锚：app.bin `.data` 0x60c3748=0xfe1500（全 app 唯一）；br28.h 335-344 行
  `JL_EQ_TypeDef{CON0,CON1,CON2,CON3,DATAI_ADR,DATAO_ADR,DATA_LEN,FLT_ADR}` 全 __RW，
  `JL_EQ_BASE=hs_base(0xfe0000)+map_adr(0x15,0)=0xfe1500`。
- 运行读数（RAND 路）：zero/sensA/advance 三臂 `completed_steps=29,215,313`、
  `stop_pc=0x6072b92`、写 0xfe1500 值 0；watch `0x601196a=2/0x6011976=2/0x6011c64=1/
  0x6011c92=1/0x6048f8c=0/0x604bcd2=0`。
- 全 app hs 基址 35 个（4 字节对齐扫描），数据驱动族 5 个：0xfe1500(JL_EQ)、0xfe1600/
  0xfe2600(JL_SRC/SRC_SYNC)、0xfe2500/0c/18(JL_SYNC)，其中仅 JL_EQ 在 boot 闭包
  （130 函数）内。
