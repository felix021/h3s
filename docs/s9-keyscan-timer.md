# S9 键扫 sys_timer 的周期源形态：静态钉死（路 D1S9 / sim-d1s9，只读）

2026-11-16。基线 main `1e930be`。纯静态只读路：零 tools/ 改动，产出=本文（周期源形态
结论 + 对 H2 per_source 契约的影响建议 + 是否需要新键）+ HANDOFF 条目。

对象：批 3 任务书 [s19-batch3-taskbook.md](s19-batch3-taskbook.md) §1.3b 留的两个待定
之一——cfg523 落地激活的 **S9 键扫 10-tick sys_timer** 在 `d1_window.health.per_source`
里该是什么条目形态（独立源 vs timer0 衍生计数），以及每周期步数 P≈4×10⁵ 的静态依据。

方法与证据强度：反汇编用 official app.bin（sha256 `18f3d573…`，load base 0x06000100）
的线性反汇编（`disassemble_app.py` 同款），call 目标按 end-relative 程序化解析（AGENTS
不变量 5，脚本 `~/tmp/d1s9/verify_chain.py` 复算，禁手算）。**[静态]** = 本路指令锚定
复算；**[文档]** = 已合并 docs/HANDOFF 条目（出处随行）。全部结论 derived-not-measured。

## 0. 一句话结论

S9 键扫定时器**不是独立硬件定时器，也不是新中断源**——它是挂在 **Timer0 tick 回调里的
软件定时器（sys_timer）**，时间基=Timer0 的 10ms tick，到期检查发生在 Timer0 IRQ4 交付
帧内部（tick 回调 → sys_timer 轮询），键扫回调 0x6001944 同步执行、零新增 IRQ/向量/交付
帧。因此它在 H2 per_source 里应是 **timer0 衍生帧（derived-from-timer0 的 sys_timer 条目）**，
不是独立 IRQ 键；周期 = 10 × P_timer0（10 tick），静态形状值 = 10 × 40,000 = **400,000
模型步**（4×10⁵），运行实读为准（同 P_park 红线）。

## 1. 静态链：从 cfg523 生效点到键扫回调

### 1.1 S9 注册段（键扫 sys_timer 的产生）[静态+文档]

app_core init walker 段 S9（`0x60121ce..0x6012212`，appcore-tail-map 表）遍历注册表
0x60C0E20（16B `{enable=1, ctx=0x124888, read_fn=0x6000E94, init=0x6001866}`），对每条：

1. `call [表+12]` = S9 iokey init `0x6001866`：本路复算钉死其两条关键 call——
   `0x6001868 → 0x60010b4`（config-523 解析器，唯一调用者）、`0x600187e → 0x6001230`
   （adc_add_sample_ch，键通道注册）。健康路径返 0（0x60018da），擦除世界（无 fixture）
   在 `0x6001876` 返 −22、零注册 [文档：BH cfg523-fixture-design §1.2/§1.3]。
2. 返 0 后 walker 注册扫描定时器（0x60121f8 起，并行指令对）：`r1 = 0x6001944`（回调）、
   `r0 = [ctx]`、`r2 = [ctx+16] = 10`（周期，ctx @0x124888 的 .data 初值 @flash 0x60C3380
   +16 = `0a 00 00 00`，本路字节级复算=10）、`r3 = 0`，然后
   `0x6012202 → 0x6066c26`（**sys_timer_add 的 4 字节 thunk**，本路复算确认），
   thunk `0x6066c34 → 0x6066a7e`（真正的 sys_timer_add；BZ 曾记 thunk 转 0x6066ba6，
   本路按 end-relative 复算为 0x6066a7e——0x6066ba6 是按 id 查找/删除的另一函数，见 §5）。

即：**键扫注册形态 = sys_timer_add(回调=0x6001944, 参数=注册表项指针 0x60C0E20, 周期=10 tick)**，
与 BZ 已钉的 sys_timer 注册形态同构 [文档：d1-s18-consumption §2.3]。

### 1.2 sys_timer 的时基与到期链（"timer0 衍生"的机制依据）[静态+文档]

sys_timer 节点挂在快/慢轮双链表（头 0x126DF0/0x126DF8），节点布局
`{next@0, prev@4, 回调@8, 到期时刻@20, 参数/标志@24, flags@28}` [文档：BZ §2.3]。到期与
回调的驱动链全部在 Timer0 交付帧内：

1. Timer0（IRQ4，时钟源 5=STD12M，PRD+1=120000 tick=10ms [文档：AH/probe_irq_entry]）
   到期 → Timer0 handler 0x12ece8 → 回调表 [0x126F6C] 的 tick 回调 0x603622C
   （tick_timer_init 0x6066d86 写入，本路复算 `0x6066d86 r0=r11+2476 → 0x126F6C`）。
2. tick 回调 0x603622C 每 Timer0 周期执行：tick 计数 [0x1268CC]+1、累计 ms [0x1268D0]+10，
   然后 `0x603623e → 0x6066de0`（**sys_timer 轮询**，本路复算确认）。
3. sys_timer 轮询 0x6066de0 遍历快/慢轮，到期判定 `[+20] ≤ 当前 tick 计数` → 摘链
   （周期标志 bit25 重挂）→ `call [+8](arg=[+24]&0xFFFFFF)`。本路复算确认火回调点：
   `0x6066e32 r10=[r5+12]`、`0x6066e36 r6=[r5+8]`、`0x6066e7a r0=r10`、`0x6066e7c call r6`。

结论：**键扫回调的每一次触发都在 Timer0 IRQ4 交付帧内同步发生**——没有第二个定时器、
没有新的中断号/向量槽/交付请求。这就是 §0 "timer0 衍生" 的全部机制含义。

### 1.3 键扫回调本体（窗口内的行为形状）[文档]

0x6001944（扫描回调）→ read_fn 0x6000E94（读 [0x125320] 指针槽 → 阈值表 20 级定档 →
键 id 表）→ 去抖/手势状态机 → 有按键才 post 事件 0x10000C。**idle（无键）世界读回 255、
零手势、零 post、零唤醒**——D1 窗口内（无按键）它是 Timer0 tick 链内每 10 tick 一次的
良性背景回调 [文档：Q §2.2/BH §3.2]。

## 2. 三个问题的静态答案

### ① 独立定时器还是 Timer0 派生交付源？——**都不是，是 Timer0 派生（软件定时器）**

| 判据 | 键扫 sys_timer | 判定 |
|---|---|---|
| 独立硬件计数器/时钟源 | 无——是 sys_timer 链表里的软件节点，计数器=到期时刻 [+20] 对 tick 计数做比较 | 非独立定时器 |
| 新中断号/向量槽/交付帧 | 无——回调由 sys_timer 轮询 0x6066de0 在 tick 回调内 `call [+8]` 同步触发 | 非独立交付源 |
| 时间基 | Timer0 的 10ms tick（tick 计数 [0x1268CC]） | **Timer0 派生** |
| 到期检查位置 | Timer0 tick 回调 0x603622C → sys_timer 轮询 0x6066de0 | Timer0 交付帧内 |

一句话：它是 **Timer0 派生的 sys_timer 回调源**（任务书 §1.3b 括号里 "sys_timer 到期挂
在 Timer0 tick handler 之下" 的精确展开：挂在 tick 回调 0x603622C 的 sys_timer 轮询之下），
与 BH 的既有定性一致（"Timer0/core-tick 之外无新 IRQ 源，回调在 tick 链内"）。

### ② 每周期步数 P≈4×10⁵ 的静态依据

- Timer0 周期 = 10ms = **40,000 模型步**（常量链：STD12M 每模型步 3 计数，PRD+1=120000
  tick / 3 = 40000；`STEPS_PER_MS=4000`、`TIMER0_STEPS_PER_10MS=40000` [文档：INV/
  test_falsification §AZ-7、check_d1_acceptance "120000/3=40,000 是推导值不是魔数"]）。
- 键扫周期 = **10 tick**（ctx[+16]=10，§1.1 字节级复算）。
- 故 **P = 10 × 40,000 = 400,000 模型步 = 4×10⁵**。

红线同 P_park：**运行实读为准，不预锁**。tick 数是静态的（10），但 Timer0 周期可被
os_time_dly 按场景重编（BY 实测 PRD 三态），所以 P_keyscan = 10 × period_closed_form(timer0)
随 P_timer0 缩放；4×10⁵ 只是 Timer0=40,000 形状值下的标称。与 §3 的实读建议一致。

### ③ H2 per_source 条目形态——**timer0 衍生帧，非独立键**

当前契约（d1-health-checks §2）per_source 是"每个 IRQ 源一条"，checker 侧
`PERIODIC_SOURCES=('timer0','core_tick')`（check_d1_acceptance.py:71）只对这两源套
`⌈N/P⌉+1` 上界，其余源一律 `deliveries==0`。键扫**不是 IRQ 源**，但**是周期源**，直塞
成独立 IRQ 键会把"交付"语义搞混（它没有 IRQ 交付），塞成非周期源会被 checker 误 FAIL
（键扫 deliveries 恒 >0）。正确形态=**新增一条标为 timer0 衍生/sys_timer 的周期源条目**：

```
'source': 'keyscan',
'kind':   'sys_timer',           # 新增判别字段:irq(默认) vs sys_timer
'derived_from': 'timer0',        # 时间基=Timer0 tick(可选,kind 已含此义时可省)
'period': 10 * P_timer0,         # park 态实读;tick 数 10 是静态事实,P_timer0 现算不预锁
'expirations': N,                # sys_timer 到期(倒计时归零/到期判定命中)次数
'deliveries':   N,               # = 回调 0x6001944 被同步调用次数(无 IRQ 交付,与 expirations 同数)
'completions':  N,               # = 回调返回次数(= deliveries,同步执行到返回)
```

三分计数语义对 sys_timer 源坍缩为同数（到期即同步调用即同步返回），但保留三键以复用
H2 的一致性检查（交付≤到期+相位容差、完成≤交付）与证据契约。`⌈N/P⌉+1` 公式不变。

## 3. 对 H2 per_source 契约的影响建议（按实施序）

1. **契约先行（d1-health-checks.md §2）**：per_source 的 `source` 枚举加 `'keyscan'`；
   补一句 sys_timer 衍生源的计数语义（deliveries=同步回调调用非 IRQ 交付、period=N_ticks
   × P_timer0、expirations=倒计时归零）；加 `kind`（`'irq'` 默认 / `'sys_timer'`）判别字段。
2. **checker 侧**（PR-2 生产者落地时，非本路）：`PERIODIC_SOURCES` 加 `'keyscan'`（并把
   上界分支按 `kind=='sys_timer'` 识别衍生源，避免当独立 IRQ 处理）；H1/H4/H7 的
   wake_reasons 枚举要能容纳"键扫/ sys_timer 唤醒"字样（任务书 §1.3b 第 2 条）。
3. **生产者侧**：cfg523 开关开启且 S9 注册成功时才 emit 该条目（条件栈实取自适应，不
   硬编码"必有键扫源"——任务书 §1.3b 第 4 条）；无 cfg523 世界不出现该条目，H2 对不
   存在源不 pending（与 N2 零触达同款：触达才入表）。
4. **窗口覆盖**：默认 N=max(S_tick+160k,200k) 对 P=400,000 覆盖 <1 个完整键扫周期——
   但 H8 只折算 Timer0 的 P_park 不受影响；是否拉长观察键扫周期归 PR-BI 观察轮数据定
   （任务书 §1.3b 第 3 条，不预设）。

**是否需要新键：需要。** 最小集=①per_source 新增 `'keyscan'` 条目 ②`kind`（或等价
derived 标记）判别字段 ③checker `PERIODIC_SOURCES` 同步。均属"先改 d1-health-checks.md
契约再改代码"范畴，本路只读不落地。

## 4. 遗留与相关

1. **BZ 地址勘误**：d1-s18-consumption §2.3 "thunk 0x6066c26 `call 0x6066ba6`"按
   end-relative 复算应为 `call 0x6066a7e`（0x6066a7e 才是分配节点+写回调/到期/挂轮的
   add 体；0x6066ba6 是另一族函数——扫快/慢轮按节点 id [+28] 查表并改写 [+24]，
   非 add）。不影响 BZ 结论（sys_timer 注册形态与节点布局），仅地址标签需勘误。
2. **兄弟源 adc_scan（S8，5 tick）**：S8 段（0x60121ae）用同一 thunk 注册 adc_scan
   0x60349d2 为 5-tick sys_timer 采样器 [文档：BH §1.4-3/AY]。它与键扫同属"窗口内
   Timer0 衍生周期源"这一类；BZ §2.3 在 689,402 采样点只见两个 tick=115 节点（trim/GPCNT，
   D1 窗口外到期），adc_scan 当时不在快轮采样里——是否/何时挂轮、是否窗口内周期触发，
   归 **PR-BI 首验按 park 态快轮链表实读** 定（per_source 生产按实取自适应，不预写
   "必有 adc_scan/键扫"）。本文结论（timer0 衍生帧形态）对 adc_scan 同样成立。
3. **衍生源的计数采集点**：键扫"到期/调用/返回"的观测锚=sys_timer 轮询 0x6066de0 的
   火回调点（0x6066e32/0x6066e7c）+ 回调 0x6001944 入口/返回；这些是 Timer0 交付帧内
   的观察件，PR-2 生产者实现时复用 §1.2 观察件（只读旁路）纪律，不动计步/交付顺序。
