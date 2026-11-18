# 前沿侦察 DACDMA：JL_AUDIO DAC DMA 指针读停点（41,965,883 @ 0x606e148）

路 DMASCOUT / sim-dmascout（基线 main 5cc4d12，worktree `~/worktrees/h3s/sim-dmascout`，
静态侦察，零 tools/ 改动，不跑条件线）。2026-11-18 13:44 开工 / 14:26-14:33 codex 参谋
（gpt-6-astra）/ 14:45 收口。停点=路 EVIMPL 实测（证据 evidence/sim-routes/evimpl.json
arm9 + analysis/local-checks/irq-entry.json p11_heap_experimental_config 场景）。
一次性脚本在 `~/tmp/dmascout-static/`、SDK IR 提取在 `~/tmp/dmascout-static/mediaao/`，
不进仓。

## 0. 停点与任务

- 前沿 **41,965,883 @ 0x606e148**：`ld.w r0=[r5+16]`（raw 字索引 `[r5+4]`，r5=0xfe0500
  =JL_AUDIO 基址）读 **0xfe0510**，kind=`jl_audio_dac_dma_read_not_modeled`——路 BM 的
  JL_AUDIO DAC DMA 四字 write-only 记录容器（0xfe0504/08/10/14）读侧 fence。
- EVIMPL 交接预判的两问——「0xfe0514 是谁（HWP/TWP/PNS？）」「DMA 等待环语义
  （谁推进 HRP、退出条件、环/块大小从哪读）」——本路静态钉死：
  **0xfe0514 = DAC_SWP（软件写指针）；这段代码不是轮询等待环**（§2.3 codec 级证据），
  停点函数 = SDK `audio_dac_set_sample_rate`（§2.1 三源互证）。

## 1. 寄存器布局钉死（三源互证）

**JL_AUDIO（0xfe0500）DAC 域全 32 位字槽布局**：

| 偏移 | 地址 | 成员 | 本路定性 |
|---|---|---|---|
| +0x00 | 0xfe0500 | DAC_CON | 使能/采样率低 4 位（AK 容器已建模） |
| +0x04 | 0xfe0504 | DAC_ADR | 环缓冲基址（固件写 0x18e180=60KB CW 缓冲） |
| +0x08 | 0xfe0508 | DAC_LEN | 环长/块参数（固件写 1；SDK power_on 按缓冲尺寸计算） |
| +0x0C | 0xfe050c | DAC_PNS | flush 臂写 1（本路新钉；**未映射**） |
| +0x10 | 0xfe0510 | **DAC_HRP** | 硬件读指针（软复位写 0；硬件消费时推进）——**停点字** |
| +0x14 | 0xfe0514 | **DAC_SWP** | 软件写指针——停点第二读字 |
| +0x18 | 0xfe0518 | DAC_SWN | 空间计算读（**从未被写**，未映射） |
| +0x1C | 0xfe051c | DAC_VL0 | CW 容器已建模 |
| +0x24/+0x28 | 0xfe0524/28 | DAC_TM0/TM1 | DT 容器已建模 |
| +0x34 | 0xfe0534 | DAC_COP | p3_ls 写记录已建模 |
| +0x38 起 | 0xfe0538.. | ADC 域 | 路 AE 钉（另域） |

三个独立来源互证「成员序×4 全 32 位」：

1. **[DWARF]** media.a `audio_dac_io.c.o` 调试信息里的 `JL_AUDIO_TypeDef`
   （源路径 `earphone_sdk/interface/driver/cpu/br28/asm/br28.h` 第 162-191 行，
   与本地缓存 br28.h 同段行号、**不同成员宽度**）：DAC_PNS@bit96/+0x0C、
   DAC_HRP@bit128/+0x10、DAC_SWP@bit160/+0x14、DAC_SWN@bit192/+0x18、
   DAC_VL0@bit224、DAC_TM0@bit288=+0x24、DAC_COP@bit416=+0x34——与模型全部
   既有地址常量一致。
2. **[SDK 头]** csfr.h 第 15-17 行 `#define __u8/__u16 unsigned int // …special
   for struct`——构建变体里 u16 标注实际是 32 位字槽（路 AG/br28_model.py:258
   注释已钉，本路独立复核）。
3. **[固件]** 全部访问为对齐 32 位：停点 `ld.w [r5+16]`/`[r5+20]`（raw 字索引
   +4/+5）、W1[3] 四整字写、flush 臂 `[r5+12]=1` 整字写。

**勘误 1（命名级）**：registry/模型/执行报告注释「四字=ADR/LEN/HRP/**PNS**」成员
序错一位——+0x14 是 **SWP**；PNS 在 +0x0c（0xfe050c，恰在 flush 臂、未映射）。
地址与已实现行为零影响，注释修正归实施路。

**勘误 2（文档级）**：本地缓存 br28.h 副本（md5 0314dd3c…，u16 混排）非固件构建
变体；构建真值=成员序×4。后续引用 SDK 名字一律以 DWARF/csfr.h 口径为准。

## 2. 停点函数钉死 = `audio_dac_set_sample_rate`（SDK dac_base.c，QCY 构建变体）

### 2.1 全形状（官方 app.bin sha 18f3d573…，vendor 线性反汇编 + 钉死 codec 复核）

```
0x606e106: push {rets,r9-r4}; r4=r0(采样率参数); r9=0x1265C0(dac_hdl 对象族基址)
0x606e110: r5=r9+7700(=0x1283D4); r1=0; r7=0; call 0x604538(r5)     ; mutex/log 族
0x606e120: r0=101317488=0x609FB70                                   ; ←采样率表
  for (r7=0; r7<=14; r7++) { r1=[0x609FB70+r7*4]; if (r1==r4) goto 0x606e142; }
0x606e136: call(r5); r4=-22(EINVAL); goto 0x606e1BC                  ; 未命中→返 -22
★0x606e142: r5=0xfe0500                                              ; 命中→DAC 段
0x606e148: r0=[r5+16]        ; ld.w DAC_HRP  ← 停点（首读）
0x606e14a: r1=[r5+20]        ; ld.w DAC_SWP
0x606e14c: if (r0==r1) goto 0x606e194                                ; beq.rr n=34
0x606e150: r0=[r5+16]; r1=[r5+20]                                    ; 第二次双读
0x606e154: if (r0==r1) goto 0x606e194                                ; beq.rr n=30
; —— 两检不等才到这（flush 臂，SDK wait_for_dac_stop 主体）：
0x606e158: call 0x12355C(ROM)         ; spin_lock/malloc+sem_create 族
0x606e15e: r8=r9+7472(=0x1282F0=dac->sem); call 0x605D2E(r8)
0x606e16a: [r5+12]=1                  ; DAC_PNS=1（arm stop IRQ）
0x606e16e/172: [r5+0]|=0x20; |=0x40   ; DAC_CON bit5/bit6
0x606e176: call 0x123530(ROM)         ; spin_unlock 族
0x606e17c-18a: r0=h[0x1265C0+7538](u16)/10+1                        ; delay_ms 超时
0x606e18e: call(r8, 超时)             ; os_sem_pend —— 等 IRQ39 投递的信号量
0x606e194: POST —— r6=r9+7508; [r6+20]=r4(记采样率); [r5+0]&=~0xF; [r5+0]|=r7(索引)
0x606e1a2: call 0x60474A(r6+192=r5)   ; audio_dac_read_reset_all（纯 RAM+mutex）
0x606e1ac: r1=[r6+0]; r4=0; if (r1==0) goto 出口; r0=[0x1265C0+1882*4]; call r1  ; 可选回调
0x606e1BC: r0=r4; pop; ret
```

三源互证：

- **SDK IR 逐段对位**（media.a dac_base.ll:647 `audio_dac_set_sample_rate`，内联
  `wait_for_dac_stop`+`set_sample_rate_base`）：15 项表搜索/未命中 -22/命中→HRP==
  SWP 检查→flush 臂 PNS=1+CON|=32/64+`os_sem_pend(sem, delay_ms/10+1)`→POST 段
  `sample_rate` 存回+CON 低 4 位 RMW+`audio_dac_read_reset_all`+可选
  `dac_set_samplerate_callback`——逐段对应。
- **采样率表逐字节一致**：0x609FB70 15 字 = SDK `sample_rate_tbl`
  [96000,88200,64000,0,48000,44100,32000,0,24000,22050,16000,0,12000,11025,8000]
  （含 3 个 0 槽位）。
- **codec 级钉死**（reference-cache pi32v2-isa，主仓 tools 加载）：
  0x606e148/14a = `ld.w {d:0/1, b:5, n:4/5}`（字索引×4=字节 +16/+20 → 0xfe0510/14）；
  0x606e14c/154 = `beq.rr {r:0, s:1, n:34/30}`（自指令尾按字计）→ 目标均为
  **0x606e194（前跳）**。

### 2.2 「等待环」勘误（勘误 3）

**0x606e142..0x606e192 没有任何向后分支**——这不是 DMA 指针轮询等待环，而是
`wait_for_dac_stop()` 的**空闲守卫**：HRP==SWP（两次 volatile 读任一次相等）→
环空/DMA 空闲 → 直接跳 POST 配置；两次都不等 → 进 flush 臂请求数据排空，真正的
等待发生在 **os_sem_pend（等 DAC 停止中断投递的信号量，带 delay_ms/10+1 超时）**，
不在指针上自旋。EVIMPL/HANDOFF 的「beq.rr 指针对轮询=DMA 等待环」表述作废；
对实施路的直接影响：**相等臂一次比较即退出，不存在「环循环 N 次」的投影问题**。

### 2.3 硬件语义与「谁写 HRP / 环大小从哪读」

- **HRP**：软件可写（`dac_platform_power_on` 复位写 0，即 W1[3] 尾段 0x6071ea4/ea8
  的 [+0x14]=0、[+0x10]=0，顺序=SDK IR 的 SWP 先 HRP 后）；硬件在 DMA 消费环数据时
  推进。**SWP**：纯软件（生产者推送数据后前进）。相等=空环。
- **退出条件**：任一次双读相等（前跳 POST）。
- **环几何不读硬件**：ADR/LEN 都是固件自己写的（0x18e180 / 1）；可用空间由
  0x607236c 现读 ADR/LEN/SWP/SWN 计算（`r2=(LEN+0xFFFF-SWN)&0xFFFF; …` 环回绕算术），
  该函数在本前缀**从未执行**（它读 0xfe0514，执行了早就在此停）。
- **停点世界机器事实**（analysis/local-checks/irq-entry.json，p11 场景）：
  四字**恰各 1 写**（0x6071e92..ea8，之后 3800 万步零新写零读）：
  ADR=0x18e180、LEN=1、**HRP=0、SWP=0**。

## 3. 消费链与镜像同构

### 3.1 相等臂出口（本前缀实际会走的臂）

POST 0x606e194→0x606e1BC：dac_hdl 族 RAM 字段写（0x1265C0+7508 段）→
DAC_CON[3:0]=采样率索引 RMW（**AK 容器地址级服务**，读回=最后写值）→
`audio_dac_read_reset_all`（SDK IR 证纯 RAM+os_mutex，无 SFR）→ 可选回调
`[r6+0]`（开放项，§7）→ 返 0。**SFR 面不再触未建模读**（回调目标除外，见 §7）。

调用者两处（全 app call 扫描）：0x601442c（`r0=16000` 字面量，app 初始化族）与
0x6076942（jlstream DAC 节点速率协商段）。停点证据无 GPR 转储，不可分辨是哪个
（两者载荷都在表内、返回后消费链同形；不构成预案分叉）。

### 3.2 official↔b79 镜像同构（字节级）

b79 app.bin（ait.load_b79 解密取 plain，sha b79cb3a6… 与 manifest 钉死值一致）与
official 全镜像**恰 1 字节差**：0x609e510（ANC 键循环表，AGENTS 钉死预期）。本域
六区域逐字节相同：set_rate 函数（0x606e100..）、采样率表（0x609fb60..）、
power_on 尾段（0x6071e70..）、空间函数（0x6072360..）、两调用者段
（0x6076800../0x6014400..）。stream.bin 41B 音量参数差只在值层面且不在本段读面
（若后续路径读到，parity 逐步签名机制会显形）。**结构同构=代码+表逐字节相同**；
动态前缀一致性仍按纪律由 official↔b79 对照臂分别核对（codex 口径）。

### 3.3 下一墙预判（按分支，不排全局顺序——codex 框架）

| 臂 | 首个候选墙 |
|---|---|
| 本路放行（两字 HRP/SWP 读回）后走 POST 返回 | 调用者续段 RAM/已建模族 → 再往后首撞 0xfe04xx 时钟门族或解码器资源域（均未建模，d2 §3 清单） |
| 空间计算 0x607236c（tone/PCM 推送域，D2） | **两字放行口径下首读 0xfe0508(LEN) 即停**；若四字全放行则首读未写过的 0xfe0518(SWN) 停——SWN 无写记录，读回语义无值可回，天然诚实停 |
| flush 臂（HRP≠SWP 才进，本前缀不进） | 依序：ROM 锁/分配服务 → PNS 写 0xfe050c（未映射）→ os_sem_pend 等 IRQ39（不建模=挂起/超时） |

## 4. 与既有音频域模型的边界关系

| 既有模型 | 关系 |
|---|---|
| JL_AUDIO DAC_INIT_WORDS（AK：DAC_CON/AUD_CON/ASS CLK_CON 写记录+读回） | POST 段 DAC_CON RMW 由它服务；无冲突 |
| DAC_DMA 四字 write-only（BM） | **本停点就是它的读侧 fence**；预案只动读侧两字 |
| DAC_VL0（CW）、DAC_COP（p3_ls）、DAC_TM0/TM1（DT）、jl_adda_analog_family（AM）、ADC DMA 五字（0xfe053c..54c 写记录） | 地址不重叠，全在上/下游配置链，互不牵动 |
| d2-audio-first-scene §3 缺口表 | 「0xfe0538(bit4 完成轮询)」一行按旧 u16 布局命名，实际 0xfe0538=ADC_CON（DWARF）；该表到达时按本路布局表重核 |

**勘误 4（BM 边界注）**：BM/registry「D2 的 DMA 指针/计数读属 tone/PCM 推送域
（D1 后）」的边界划分漏了一类更早的读者——**DAC 节点开放路径的 set_sample_rate
空闲守卫**（41.96M 到达）。实施路放行时同步修正该注释。

## 5. 三选一裁决（codex 定稿：a 收窄版）

**裁决：选 a，但只批准「HRP(0xfe0510)/SWP(0xfe0514) 两字、对齐 32 位、读回=最后
写值」；不批准四字容器无条件扩展；不采信「相等臂=硅真相」的强表述。** b（DMA
引擎推进+IRQ39+信号量投影）留到数据相确实需要时另立项；c（诚实停）只在守卫无法
成立时执行。

实施要求（codex 意见书要点，全文见 `~/tmp/codex/202611181426-dmascout/final.md`）：

1. **开关**：新条件开关（建议名 `jl_audio_dac_dma_idle_readback`），默认关，显式
   依赖 `jl_audio_dac_dma_writes`（缺依赖报配置错误）。
2. **读语义**：被读字有写记录 → 返回最后写值（原值，不改 PC/不构造相等/不跳指令）；
   **任一被读字零写 → 维持原严格停**（不补零、不从另一指针复制）。ADR/LEN 本次
   不动（无消费需求，保原行为）。
3. **守卫面**：绑定「已核验镜像+场景+初始化阶段」；读位置限定到已核验的
   set_sample_rate 指针比较段（0x606e148/14a/150/152 的形状）；「初始化阶段」由
   **事件历史**维护，不是逐读检查 HRP==SWP==0（否则「曾经运行过又回到零」会被
   误判首空闲）。失效事件：SWN/PNS 触达、DAC 启停/控制位变化、另一核写、
   任何未预期重写（**同值重写也要审查写事件**）、快照恢复（写来源+阶段+失效位
   一并恢复）。
4. **报告面**：每字成功读次数+最后写值及来源 PC/步数、首末读 PC/核/步数、拒绝
   次数及守卫失败原因、当前阶段与失效事件。
5. **措辞**（证据分级）：「返回值直接来自固件写记录，零合成字节」成立；但放行
   隐含新增硬件假设「HRP 自软写 0 后硬件未推进」——登记为**显式初始化空闲假设**，
   不声明与 BR28 硅行为等价（证据缺口：仿真无 IRQ39 不能反证硅上无 DMA 活动；
   SWN 未写≠复位值为 0；LEN=1 配置待解释；控制位序列对硬件静止的影响未审）。
   若纪律要求硅等价证明才放行，则本证据只够选 c——按本仓 derived-not-measured
   口径走 a。
6. **负例臂**（不满足即停/拒绝）：开关关=原停点原 kind 逐字复现；零写读停；
   宽度/对齐不符停；读位置未核验停；注入 HRP/SWP 不等不得强制相等；「先推进后
   回零」不得重新获得放行资格；数据提交/控制变化后再读 HRP 停。
7. **双核审计口径**：零写/零读断言按「两核+地址别名+全部写路径」合并审计后才能
   写进守卫依据（本路 core1 步数冻结 11,496 是旁证，实施路补逐字核）。
8. SDK 对应关系是「段级语义对位」，不写「逐指令一致」（SDK 67328f4 的
   wait_for_dac_stop 单检，QCY 构建双检——以目标镜像解码为准）。

不选 b：本前缀不进 flush 臂、无数据消费；b 需要 DMA 引擎+IRQ39+信号量整套投影
（=d2 场景 G3/G5 数据相），复杂度独立立项。不选 c：a 的两字读回零合成字节、
有 lp_timer/UART CON1/DAC_VL0 同族先例（先例只提供实现方式，不自动赋予硬件
语义——codex 口径，见措辞分级）。

## 6. 实施验收清单（建议 10 臂，仿 EVSCOUT）

1. off 臂：开关关从零复现 41,965,883 @ 0x606e148 逐字（含 counters）。
2. on 臂快照续跑（CKPT 先例）+ 从零全量对照：resume≡from-0 逐键。
3. on 臂记录真实第一停点（预期 POST→调用者续段；预算耗尽单列）。
4. 读计数/守卫拒绝计数进 executor 报告并与断言互锁。
5. 零写负例：模型级单测直读未写字（构造新世界）→ 严格停原 kind。
6. 不等负例：模型级写 HRP≠SWP 后读 → 不构造相等，flush 臂按真停点停
   （PNS 写 0xfe050c unmapped 停即预期墙）。
7. 阶段失效负例：模拟 SWN/PNS 触达后读 HRP → 拒绝。
8. 快照一致性臂：on 世界快照→恢复→续跑与直跑逐键（写来源/阶段随快照走）。
9. official↔b79 对照臂扩到新共同前沿（结构同构已字节级钉死，动态对照照跑）。
10. 实施后 `--rom` 全量回归 EXIT=0 + assert_preflight 全绿 + registry/FRESHNESS
    重钉 + BM 注释勘误（§4 勘误 4）落盘。

## 7. 诚实边界与开放项

1. **POST 可选回调 `[r6+0]` 未证空**：dac_set_samplerate_callback 等价槽在
   0x1265C0+7508；静态无法证明 41.96M 时为 NULL。若非空，call r1 执行的是 app
   代码（非 SFR 读），风险有界但属新域——实施路 on 臂首停点自然收口（codex：
   「回调为空或目标链已核验」二择一才能维持『POST 不触未建模 SFR』结论）。
2. **调用者二选一未定**（0x601442c/0x6076942）：无 GPR 转储；不影响判定与预案。
3. **LEN=1 语义未解释**：固件写 1（SDK 按 buf/2/channels 计算处）；60KB 缓冲与
   有效 DMA 长度的关系、长度单位/回绕未证。只登记观察值，不解释。
4. **0x12355C/0x123530 ROM 调用身份**：按 SDK IR 对位推断为锁/分配/延时族
   （ROM 0x120000-0x140000 延迟 helper 域），未逐指令反汇编（flush 臂不进，
   非本路必需）。
5. 双核历史审计（§5.7）与动态 parity（§3.2）留实施路。
6. 静态分析未执行固件；「停点时会走相等臂」依赖写记录机器事实（四字恰各 1 写、
   HRP=SWP=0）而非运行时 GPR。

## 8. codex 参谋记录

- gpt-6-astra，2026-11-18 14:26-14:33，`~/tmp/codex/202611181426-dmascout/`
  （prompt.md/final.md，43,679 tokens）。
- 裁决=a 收窄版（两字+事件历史守卫+假设分级措辞），另采纳：PNS 地址笔误更正
  （brief 中 0xfe0510c → **0xfe050c**）、下一墙按分支预测不给全局顺序、
  「回调目标证明」缺口、双检以目标镜像解码为准、先例不自动赋予硬件语义、
  六区域同构≠动态前缀一致、10 臂验收清单框架。全部并入 §5/§6/§7。
