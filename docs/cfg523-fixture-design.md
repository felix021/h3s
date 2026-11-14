# config-523 具名 fixture 设计：全图 + 三选一预演 + S17 消费面钉死 + registry 草案（路 BH / sim-cfg523，只读设计）

2026-11-14。基线 worktree `7a45e67`（路 BG 合并点）。本页是 S19 冲刺五预案之 P7
（BA §5.2 / d1-scene-blueprint §5 BA #13 行）的**实施规格书**：把「config-523 具名
fixture」从 BA 的一行预案落成实施路拿到即可开工的设计——config-523 全图（存储后端/
解析器/三读者）、fixture 三选一预演与推荐、S17 健康环的逐字段消费面、开关+registry
条目草案、与 D1 蓝图（P-13）与 S/AJ 键矩阵 fixture 的统一。**本路是设计文档，不是
实施**：未改 tools/ 任何代码、未跑任何改动场景（本文件为 docs/ 新增，git 状态可查）。

方法与证据强度：反汇编用仓库钉死的 pi32v2-isa codec（`probe_pi32v2.upstream()` +
`probe_boot.application()`，official app.bin sha `65cafc7f…`/app sha `18f3d573…`，
load base 0x06000100），一次性脚本在 `~/tmp`（不进仓）。**[静态·本路]** = 本路指令
锚定反汇编（新增钉死）；**[文档]** = 已合并 docs/HANDOFF 条目（出处随行）；**[推断]**
= 上述链推出；**[疑]** = 未闭合。全部静态结论 **derived-not-measured**；fixture 值
全部 **synthetic-not-measured**（真实 config 523 在 bank B 记录区，公开工件外）。

## 0. 一句话结论

**推荐方案 (a)「直接填产物对象」作为 D1 启用件**：在解析器 0x60010b4 首次执行时
（PC 锚定安装，绕过 boot 期 BSS 清零覆盖）把 70 字节具名对象写进 0x1274F4——解析器
自己的幂等早退路径（`b[+0]≠0 → 直接返回对象指针`，0x060010c2 [静态·本路]）会让
S9 iokey init 拿到「已解析」世界，**get_config(523)/记录区全链根本不跑**；S17 健康门
天然过（walker 读键通道队列项的**初始值 1**，1≤9 [静态·本路]），K2 金丝雀归零；
唯一的新执行面 = S9 注册段两处 IO 配置调用 + adc_scan 可能轮选键通道启动转换
（需配套 RES 行 0x10001=≤9 的「释放态」档，防诚实停）。**与 S/AJ 的统一点**：三路
fixture 填的是**同一个对象**（0x1274F4+[0x125320]，同一 +10 阈值表/+50 键 id 表），
D1 直接采用 AJ 三键矩阵的表值；**唯一必须修正的 AJ 语义 = 释放态代表值**——AJ 的
400 是停点后场景的自由选择，任何穿过 S17 的世界要求键通道原始读数 ≤9
（接地分压稳态），400 会让 S17 首轮即 K2。

## 1. config-523 全图（四问之一）

### 1.1 存储侧：523 不在 VM，在「记录区只读后端」（AB 未见过它）[文档+静态·本路]

AB 的 key 枚举（vm-key-roundtrip，VM 后端 key 1..99，record `0x60c3300`）**从未见过
config 523**——get_config 分派器 `0x06000f88` 按 key 区间选 4 个后端（注册表
`0x60c32c8..0x60c3338`，每条 28B {+0 init/+4 match/+8 get/+12 set}，路 CR 钉死；
本路从 flash 数据直读复核 [静态·本路]）：

| 后端 record | init | match | get | set | key 区间 | 介质 |
|---|---|---|---|---|---|---|
| 0x60c32c8 | 0x6060a4a | 0x6048810 | 0x6060ae2 | 0x6060b38 | 100..511、701+ | btif 窗/cfg_tool |
| 0x60c3300（VM） | 0x6060f9a | 0x6061304 | 0x6060ea0 | 0x6060efe | 1..99（key−1<256） | Flash VM 区 0x1da000 |
| **0x60c331c（记录区）** | **0x6060d7c** | **0x6060e54（`r0=1;rts` 恒命中=兜底末位）** | **0x6060e58** | **0（只读）** | **512..700 → 523 在此** | **bank B 记录区 0x1e0200** |

- **物理位置**：bank B flash 0x1e0200，CPU 直映可见 `0x61de1e0`（=0x06000000+0x1e0200
  −0x2020 头换算，与 btif 窗 0x61ddfe0 同窗 +0x200）。真实字节不在任何公开工件
  （官方 UFW 只含引导区+bank A）[文档：路 S/CR]。
- **已建模面**：擦除世界该区 8 字节头窗 = `ERASED_BANKB_RECORD_RANGE=(0x61de1e0,8)`
  （erased_bankb 第四窗，probe_irq_entry 钉死）：init 0x6060d7c 解析首条目头
  `{u32 len; u32{b0 crc, bits20+ size}}`，len 范围检查（(len−1)>2047 拒绝 @0x6060e0e）、
  size≤800、数据 CRC=0x6000f0c（与 VM 记录同一软件 CRC，表 0x60A85CC，AB §2.1）——
  擦除态 len=0xFFFFFFFF 被拒，条目有效位 b[0x126568] 保持 0，按 index 找条目的
  walker 0x60112c8 被挡在门外 [文档：erased_bankb 注释]。即：**boot 期记录区后端的
  init 已在真实跑，只是擦除态走拒绝分支**。
- **未钉面（(b) 方案的真实重量）**：条目链的 **id/key 编码**（512..700 的 key 如何
  映射到条目——get 0x6060e58 先 call「按 key 找条目」再 call「拷数据」两级，两级
  函数体未逐指令钉）、游标步进/终止符格式。实施 (b) 前需一轮补钉。

### 1.2 解析器 0x60010b4：输入/输出/校验/返回语义 [静态·本路]（Q §2.4 布局的逐指令复核+补钉）

```
0x60010b4 (config-523 解析器; 唯一调用者=S9 iokey init 0x6001868)
 ├─ r4 = QCY(0x1265C0)+3892 = 0x1274F4（对象槽）
 ├─ 0x060010c2 r0 = b[r4+0]（enable）≠0 → ★幂等早退：r0=r4 直接返回对象指针
 │   （不读 config、不开栈缓冲——本路新钉；fixture (a) 的支点）
 ├─ [解析路径] sp −=192；buf=sp+80（112B 读缓冲），scratch=sp+0（40×{u16 阈值,u16 原序}）
 │   0x060010d4 call get_config(523, buf, 112) → r5=返回长度
 │   r5<1 → 返回 0（擦除世界：记录区后端拒绝 → 长度<1 → 0）
 ├─ 头字段：b[+1]=查表(buf[1]|buf[2]<<8 → 63 项 io16 表 @0x60A9AA0，miss=255)
 │          b[+2]=buf[3]；w[+4]=查表(b[+1] → 16 项 mux 表 @0x60A858C) = 0x100000|mux 号
 │          b[+8]=buf[10]、b[+9]=buf[11]
 │   0x06001114 w[+4]==255 → 返回 0（通道字无效哨兵）
 ├─ 条目数 r10 = 20−(0x70−r5)/5（记录长度短 → 条目少；满 112B → 20 条）
 ├─ 记录 i（5B @ buf[12+5i..16+5i]：{+0 保留, +1/+2 键 id16, +3/+4 阈值 u16}）
 │   → scratch[i] = {阈值 u16, 原序 i}；0x06001160..a0 冒泡排序（升序，id 随序换位）
 ├─ 插值（0x060011c4..0x06001222）：S=buf[7]|buf[8]<<8、B=buf[5]|buf[6]<<8：
 │   thr_out[i] = (S·thr[i]/(B+thr[i]) + S·thr[i+1]/(B+thr[i+1]))/2（末项次项=S）
 │   → h[对象+10+i·2]（+10 阈值表）；键 id 字节 → b[对象+50+i]（+50 键 id 表）
 │   ★注意：对象里的阈值是**插值后的值**，不是记录原值——(b) 方案合成记录字节时
 │     要按此算术反推，不能直填目标阈值
 └─ 0x06001226 b[+0]=1（enable；★+0 无任何「键数」读者——Q §2.4 的「enable/键数」
     双标签里「键数」半边无指令证据，S/AJ 填 3 也能过只因为消费面只判 ≠0）
     → 返回 r4=0x1274F4
```

**产物对象布局（70B，0x1274F4..0x127539；三读者消费矩阵见 §1.3）**：
`+0` enable(b)/`+1` io 描述 A(b)/`+2` 极性(b)/`+4` 键通道字(w)/`+8` 第三段门(b)/
`+9` 第三段参数(b)/`+10` 20 级 u16 阈值表(升序，>1022 哨兵域)/`+50` 20 项键 id 字节表。

### 1.3 三个读者（含 S9/S17 两消费者；字段级矩阵）[静态·本路+文档]

| 读者 | 入口 | 取对象方式 | 读哪些字段 | 时机 |
|---|---|---|---|---|
| **S9 iokey init** | 0x6001866（注册表 0x60C0E20 {1,ctx 0x124888,fn 0x6000E94,init 0x6001866} 的 init 槽；app_core init walker 0x60121ce 调） | 调解析器取返回值，**并把返回值存进 [0x125320] 指针槽**（0x06001874） | +0（=0→返 −22）、+4（→adc_add_sample_ch 0x6001230 注册键通道）、+1/+2（IO 口配置：call 0x6085094 写 PORT DIE [base+16]，**port 高半字节=0 时该调用自身早退** 0x06085096；call 0x6001456 通道 32 复用）、+8（≠0 才走第三段 call）、+9 | boot init walker 段（W1[4]/0x6011a08 之后、W2 之前，AY 表 #7） |
| **S17 电池通道就绪环** | 0x6012458..0x601248c | **QCY 直接偏移**（li 3892/974，不经 [0x125320]） | **只读 +0 和 +4**（每迭代各一次，§3） | app_core 任务体尾段（S16 之后） |
| **read_key 键扫描** | 0x6000e94（S9 成功后注册的 10 tick sys_timer 回调 0x6001944 经注册表项调） | **[0x125320] 指针槽**（li 1200928 @0x06000e96） | +0（门）、+4（经 walker 0x6000e74）、+10..+49（阈值表线性定档，>1022 哨兵）、+50..+69（键 id 字节） | boot 后每 10 tick（D1 窗口内持续） |

**S9 返回值语义**：解析器返 0 → −22（0x06001876→0x060018d2）；+0=0 → 返 1
（0x0600187a→0x060018d6，防御路径）；健康 → 返 0，walker 才注册 10 tick 键扫
sys_timer（AY 表 #7「init 返回 0 才走 sys_timer_add(cb=0x6001944, [0x124898]=10)」）。
**注意：擦除世界的 −22 至今是静态结论**——当前条件前沿停在 W1[3] 音频树内
（3,170,362 @ 0x606b426），S9 尚未执行过；fixture 世界的 S9 全注册段同样未执行过
（见 §2.4 新执行面清单）。

### 1.4 ADC 队列侧的三个钉子（S17 健康性的机制基础）[静态·本路]

1. **adc_add_sample_ch 0x6001230**：找第一个空闲槽（key==−1），写 key=通道字、
   **value=1**（h[item+8]=1 @0x06001258）、**period=0**（h[item+14]=0 @0x06001264）、
   mode=0（非 0x10006 通道 @0x0600126e）。S 路实测：键通道落 **slot 3**（BY 三通道
   占 0/1/2）[文档：S §7.1]。
2. **walker 0x6000e74**：扫 0x127D0C+16i 的 key 字段（10 项，i≤9），命中返回
   **h[item+8] 低 16 位**；**未命中返回 1**（0x06000e8c）。
3. **adc_scan 0x60349d2/0x60348a2**（S8 注册的 5 tick 采样器，当前世界已活 [文档：AY]）：
   入口双门 = `[QCY+6124]=[0x127D44]≠−1 → 整轮跳过`（0x060349de）+ CON bit4=EN → 跳过
   （0x060349ee）；找到期项的游标**轮转**（从 b[0x124C85] 续扫）；**period==0 的占用项
   不跳过而是立即返回其 index**（0x060348cc→0x060348e6 返回，不推进 next_due）→
   调用方读该项 key 调 setter(key,1)（0x06034a12）**启动一次转换**。
   - **[疑] 0x127D44 的身份**：按 BU 队列布局恰为 item3+12（mode|period 字）——擦除
     世界 item3 空闲（memset 0xFF）→ 值 −1 → 门通过；键通道注册后该字变 0。它是
     「独立忙锁恰好压在 item3+12 上」还是「就是布局字」，本路未钉（两种读法对 D1
     断言面都无害，见 §3.4；首跑观测项 R2）。

## 2. fixture 方案三选一预演（四问之二）

### 2.1 (a) 直接填产物对象（S 路先例的 through-boot 版）——**推荐**

| 项 | 设计 |
|---|---|
| 内容 | 模块常量 `CFG523_OBJECT_BYTES`（70B，§5.1 字节级草案）：enable=1、io 描述全 0（+1 高半字节 port=0 → DIE 调用早退；+8=0 → 第三段跳过）、w[+4]=0x00100001（mux 1：boot 侧已占用 mux 0/4/5/6——BY 三通道+W4[4] trim 的 0x10005——**1 空闲**，S/AJ 同选）、+10/+50 = **AJ 三键矩阵表原样**（§5.1） |
| 安装时机 | **PC 锚定首装**：解析器入口 0x60010b4 首次执行时（执行前）写入 70B。理由：对象槽在 app RAM（.bss 域，boot 期 16,077 步 RAM 初始化会清零/覆写 [文档：probe_boot_handoff]），机器构建期预填会被擦；S9 段在 init walker（远晚于 BSS 初始化、远早于一切消费者），解析器入口是「对象唯一写者的正门」，且**幂等早退路径让解析器自己消费这次填充**（0x060010c2 读到 enable=1 → 直接返回 0x1274F4，get_config/记录区全链不跑） |
| 指针槽 | **不填**——iokey init 0x06001874 用真实代码写 [0x125320]=0x1274F4（S 路停点后场景要手工填指针槽，是因为它没重跑 iokey init；D1 世界 iokey init 真实执行） |
| S17 过环机制 | walker(0x10001) 命中 slot 3 → 返回**初始值 1**（1≤9 健康）；即使键通道因 adc_scan 轮选被采样，IRQ39 不交付 → 值不回写仍 1；若未来加 IRQ39 交付 → 值=RES(0x10001)（配套 RES 行取 ≤9，仍健康）。**三分支全过环** |
| 配套件 | RES 具名档**加一行 0x10001=4**（释放态接地分压代表值，synthetic-not-measured）——仅防「adc_scan 轮选键通道启动转换后 RES 读撞 `fd31_adc_res_channel_not_modeled` 诚实停」（br28_model 按「通道字不在 dict → Blocked」钉死），不参与 S17 判定（walker 读的是队列存储值）。合并方式=场景接线在 cfg523 开关开启时传 `{**FD31_ADC_RES_NAMED_VALUES, 0x10001: CFG523_KEY_CHANNEL_RES_IDLE}`，**不动 BY 共享常量本身** |
| 新执行面 | 见 §2.4（S9 注册段两处 IO 调用是全模型史首次执行——fixture 世界自己的新边界清单） |
| 诚实度 | 唯一合成件=70B 对象+1 行 RES；解析早退/注册/扫描/S17 全真实代码。等级 synthetic-not-measured（真实值在 bank B 记录区，公开工件外） |

### 2.2 (b) 记录区字节 fixture（让解析器真跑）——更真但更重，D1 不需要

任务文原话「伪造 VM 记录」需先修正域：**523 不走 VM 后端**（§1.1），(b) 的正确对象
是 **bank B 记录区 0x1e0200 的条目字节**（把 erased_bankb 第四窗从「8B 擦除头」扩成
「合法 key-523 条目链」）。要求与代价：

1. 补钉未钉面：条目 id/key 编码（get 0x6060e58 两级调用链 0x6060e66/0x06034a12 族）、
   游标步进/终止符——一轮静态pass，边界不封顶；
2. 合法性构造：头 {len≤2047、size≤800、CRC(0x6000f0c) 对数据} + init 0x6060d7c 的
   整链解析（擦除世界从未走过的分支）+ 后续条目终止符；
3. 内容反推：112B 记录是**插值前**值——要得到目标阈值表需按 §1.2 插值算术反解，
   且 b[+1]/w[+4] 要经两张公开查表（0x60A9AA0/0x60A858C）正向命中（io16 原始值需
   从表里挑真值）；
4. 收益：记录区 walk→CRC→get→解析→插值→组装**全链真实执行**——这是「523 解析链
   可跑」的证据价值（AB 之于 VM 的同款价值），但 **D1 五断言零依赖**（D1 只需要
   S17 过环+S19 park）。

**裁决：(b) 不是 D1 的前置，是独立的证据升级路**（将来某路可做，产出
`cfg523_record_bytes` 类 fixture；与本方案 (a) 的关系=同一契约的两个实现层，见 (c)）。

### 2.3 (c) 混合——**本质上是 (a) 的推荐形态 + (b) 的升级预留**

关键统一定义：**fixture 的契约 = 对象字节**（0x1274F4 的 70B——D1 世界只依赖它）。
(a) 直接交付对象；(b) 将来通过真实解析交付**同一个**对象。设计上把对象字节定义成
单一数据源（`CFG523_OBJECT_BYTES`），(b) 落地时以其「解析产物应等于具名对象」为
验收断言——两层实现、一份契约、D1 断言面零改动。**推荐 = (a) 先行（P7 实施路），
(b) 留作后续证据升级路。**

### 2.4 fixture 世界唤醒的新执行面（实施路的 watch 清单）[静态·本路]

擦除世界 S9 在 0x06001876 即返 −22，**以下分支全模型史零执行**，fixture 开启后首跑：

| # | 面 | 判定 |
|---|---|---|
| N1 | adc_add_sample_ch(0x10001)（0x0600187e） | 纯 RAM 队列写（S 路停点后直调已实测 41 步）——安全 |
| N2 | IO 配置 call① 0x6085094（DIE [base+16] RMW） | **port=0 → 自身早退**（0x06085096 `if (r5==0) goto`），fixture 取 b[+1]=0 → 零触达；若未来选非零 port：GPIO_BASES 0xfd5xxx 族 RMW 需 gpio_model（当前 p11_heap 机器未开）——设计上避开 |
| N3 | IO 配置 call② 0x6001456（通道 32 复用） | mask=1<<pin 恒非零不可跳过；写面未逐指令钉（疑 P3_PORT_SEL/P11 域）——**首跑观测项 R1**：若撞停点，按 p33_port_sel/容器族三选一，不属本 fixture 语义面 |
| N4 | 第三段（b[+8]≠0 时 call 416516-目标） | fixture 取 b[+8]=0 → 跳过，零触达 |
| N5 | adc_scan 轮选键通道启动转换（§1.4-3） | RES 行 0x10001=4 配套后无新停点；启动与否/频度=首跑观测项 R2（含 [0x127D44] 门身份） |

## 3. S17 健康环消费面钉死（四问之三）

### 3.1 环体逐指令 [静态·本路+文档：BA §4.5 复核]

```
0x06012458 r4=0（迭代计数）；r5=3892、r6=974（QCY 偏移）
循环头 0x06012462：call 0x600a590（喂狗——P6 面，b[0x1299f8]=1 时走 P11 邮箱
                   [0xF2A618] 写+[0xF2A61C] bit12 ack，P2M_CLK fixture 域，独立于本路）
         0x06012466 r0=1; call 0x6064efc(1)（os_time_dly 1 tick——事件时钟）
         0x0601246e r0 = b[QCY+3892]        ← ★对象 +0；==0 → goto 0x60124A4 = K2
         0x06012474 r0 = [QCY+974<<2]       ← ★对象 +4（通道字）
         0x06012478 call 0x6000e74          ← walker；0x0601247c if (r0 > 9) → K2
         0x06012480 r4+=1; r4≤150 → 回循环头
耗尽（151 次迭代）→ 0x06012486 b[QCY+4456]=1 → 0x601248c 直跳 S18
K1=0x601248e..a2（关机+自旋）；K2=0x60124a4..b4（b[QCY+4457]=1+cleanup+自旋）
```

**健康条件 = walker(通道字) ≤ 9**（fall-through 方向与指令锚点双重钉死；BA §4.5
「>9 → K2」正确，BU §3.5「值须 >9」/Q §2.4「要求读数>9」两处行文均系笔误，按本路
指令方向修正）。**物理语义**（[推断]）：键通道原始读数 ≤9 = 分压网络接地稳态 =
「无键按下」——S17 是**键线上电稳定门**（150 重试 ≈1.5s 等键线落到释放电平；真机
开机按住键 → 读数数百 → K2 拒启）。BU 的「NTC 启动门」标签按 Q §2.4 已降级，本路
按 ≤9 方向再收紧为「无键稳定门」。

### 3.2 fixture 世界里这环读什么、读几次

| 观察项 | 每迭代 | 151 迭代合计 | 期望值 |
|---|---|---|---|
| b[0x1274F4]（+0 enable）@0x0601246e | 1 读 | 151 读 | **1**（≠0，K2 分支不取） |
| w[0x1274F8]（+4 通道字）@0x06012474 | 1 读 | 151 读 | **0x00100001** |
| walker 0x6000e74(0x10001) | 1 次（≤10 项比较） | 151 次 | 命中 slot 3 → h[item+8]=**1**（初始值，无人回写）→ 1≤9 健康 |
| 喂狗 0x600a590 | 1 次 | 151 次 | P6 域（P2M_CLK ack fixture），非本路 |
| os_time_dly(1) | 1 tick | **151 tick ≈ 6.04M 模型步**（Timer0 10ms tick=40,000 步口径） | BA #13b 已按此记 budget |
| （窗口内并行）10 tick 键扫回调 0x6001944 | — | ≈15 次触发 | 每次经 [0x125320]→read_key：+0 门→walker(1)→阈值表首项 500≥1 → 档 0 → 键 id 表[0]=**255**（无键，零手势、零 post）——D1-5 H 系规则的良性背景活动 |

**不被 S17 读的字段**：阈值表/键 id 表（只有键扫读）、+1/+2/+8/+9（只有 S9 读一次）。
**与 S9 消费面的异同**：S17 只读 +0/+4 且走 QCY 直接偏移；S9 读 +0/+1/+2/+4/+8/+9
且经解析器返回值+[0x125320] 指针槽；read_key 读 +0/+4/+10../+50.. 且只认指针槽。
三者共享同一对象实体——**「S 路/AJ 的阈值表就是 config 523 的产物、S17 读同一个
对象吗」的答案：是，逐字段同源**（Q §2.4「两个消费者共享一个分档器对象」的反汇编
事实，本路按字段级矩阵细化）。

### 3.3 与 AJ 释放值的张力（统一性关键修正）

AJ 矩阵的释放态代表读数 **400** 是停点后场景的自由选择（其世界不跑 S17）。任何
**穿过 S17** 的世界，键通道原始读数必须 ≤9（§3.1）——400 落在任何「值会被回写进
队列项」的分支（IRQ39 交付/未来采样场景）都会让 S17 首轮 K2。**统一定论**：
- bin 0（≤500→255）的**语义**不变：释放=档 0；
- bin 0 的**代表值**按世界分岔：停点后场景（S/AJ）维持 400；through-boot 世界
  （D1/D2 窗口内）取 **≤9**（接地稳态，本设计取 RES 行 4、队列初始值 1 同样满足）。
- 按下态代表读数（600/780/950）与阈值分界（500/720/850）**原样沿用**——S17 在
  boot 期已过，窗口期按键交互不受 ≤9 约束。

### 3.4 键通道「boot 期不被回写」的机制钉子与残余不确定

钉死（[静态·本路]）：(i) adc_add_sample_ch 初始 value=1/period=0/mode=0；
(ii) walker 未命中返 1；(iii) 队列值唯一回写者=IRQ39 handler（EMA/直存）与显式
采样调用者——boot 顺序路径两者皆无（IRQ39 零交付 [文档：AY]；boot 直接采样环只
打 0x10000/0x10006/0x10004 [文档：BU]）。残余（如实标注）：
- **R2**：adc_scan 是否/多久轮选到键通道（period==0 立即返回语义 §1.4-3 + [0x127D44]
  门身份 + CON EN 锁存三者交织）——首跑观测；任何分支下 S17 判定不依赖它
  （ walker 读存储值恒 1），RES 行只消诚实停；
- **R3**：若实施路复测喂狗门 b[0x1299f8]=0（写者未钉，BA §9.2），P6 停点自动消失，
  S17 环更轻——与本 fixture 无耦合。

## 4. 开关 + registry 条目草案（四问之四）

### 4.1 层属裁决：**conditional，不是 probe_only**

理由（按第一性）：
1. **S17 在共享前沿路径上**：P1..P6 放行后，p11_heap 条件场景不带本 fixture 将落进
   K2 自旋（0x60124b4 `goto −2` 无限环+每轮喂狗），预算耗尽不是合法停点记录——
   registry 的前沿契约（stop kind/address freshness）无法钉。**前沿要重钉到
   S19/park（d1-scene-blueprint §4.5 的既定计划），本 fixture 必须在条件栈里**。
   这与 event_clock 进 conditional 的论证同构（BE §4.2：S16 os_time_dly 在共享
   前沿路径上）。
2. BE §1.2 已把「五预案新开关」分配给条件栈（「conditional 栈（含五预案新开关 +
   event_clock）」）——本条目是 P7 的那个开关。
3. probe_only 只能服务停点后场景（key_chain 先例）；D1 入口必须是真实启动
   （d1-acceptance §7「不能靠手工构造最终 OS 状态」），probe_only 够不着。
4. 条件栈放合成值 fixture 的先例充分：erased_bankb（base 层）、
   fd31_adc_res_named_values/lrct_trim_result/heap_info_bytes（conditional 层）。

### 4.2 registry 条目草案（照 frontier_registry「加新开关」四步的第 1/2 步）

```python
# probe_irq_entry.irq_case 新参数（默认关）:
cfg523_named_fixture=False,   # 开启时:解析器 0x60010b4 首次执行前安装
                              # CFG523_OBJECT_BYTES(70B@0x1274F4),并向 fd31 RES
                              # dict 合并 {0x10001: CFG523_KEY_CHANNEL_RES_IDLE}

# frontier_registry.FEATURES 追加:
Feature('cfg523_named_fixture', 'conditional', (), True, 'CFG523_OBJECT_BYTES',
        'config-523 分档器产物对象具名 fixture(70B 填 0x1274F4;解析器 0x60010b4 '
        '首次执行时 PC 锚定安装→幂等早退返回对象,S9 注册/S17 健康门/S19 后键扫'
        '三读者共用;配套 RES 行 0x10001=释放态档;synthetic-not-measured——真实 '
        'config 523 在 bank B 记录区 0x1e0200,公开工件外;路 BH 设计)'),
```

requires=() 的理由：对象填充本身零模型依赖；RES 行合并复用条件栈既有 fd31 两开关
（闭包已在栈内，无需重复声明）。value_attr 解析要求 `CFG523_OBJECT_BYTES` 与
`CFG523_KEY_CHANNEL_RES_IDLE` 常量与 Feature 同 PR 落在 probe_irq_entry
（conditional 层 value 开关的 registry 既有纪律）。

### 4.3 check 骨架（实施路落码参考，AW §4.2 风格）[设计]

```python
def check_cfg523_fixture_world(row):
    f = row['cfg523']
    require(f['installed_at_parser_entry'] is not None, 'fixture must install at first 0x60010b4')
    require(f['parser_early_return'] == '0x1274f4', 'parser idempotent early-return must fire')
    require(f['pointer_slot'] == '0x125320:0x1274f4', 'iokey init must store the pointer slot for real')
    q = f['queue_item']
    require(q == {'slot': 3, 'key': '0x10001', 'value': 1, 'mode': 0, 'period': 0},
            'adc_add_sample_ch must register the key channel at the first free slot')
    require(f['s17']['iterations'] == 151 and f['s17']['k2_taken'] is False
            and f['s17']['exit_flag_qcy4456'] == 1, 'S17 must run the full healthy ring into S18')
    require(f['read_key_idle'] == 255, 'in-window key scan must classify idle as no-key')
# 负例(单测级,照 AZ 反例纪律):
#   1) 无开关世界:P7 前置全放行后 S17 首迭代 K2(b[QCY+4457]==1)——钉「fixture 是唯一通过途径」
#   2) 敌意通道字(0x10006):walker 返 VBAT float 低 16 位(>9)→K2——钉「门真在读队列」
```

### 4.4 落地纪律（与前沿重钉同舟）

条件层开关会被 p11_heap 场景自动开启（`extra_flags` 全 True），而本开关在 P1..P6
落地前是惰性的（当前停点 0x606b426 在 W1[3]，S9 未到）——**但 registry 的
value_attr 立即解析常量、场景 flag dict 进报告**，惰性落地会破坏「legacy 报告逐字节
不变」。故按既有纪律：**Feature 条目+常量+irq_case 参数+前沿重钉（S17→S18 穿越后的
新停点）与 P7 实施路同一 PR 落地**（此前每个条件开关的落地方式均如此）；本设计路
零落地。预算：S9 段 +10²..10³ 步（解析早退+注册 41 步+两 IO 调用+sys_timer add）；
S17 环 151 tick≈6M 模型步已由 BA #13b 记账，无新增。

## 5. 与 D1 蓝图（P-13/BA #13）及 D2-a 的衔接（四问之五）

### 5.1 fixture 参数（字节级草案，全部 synthetic-not-measured）

```
CFG523_OBJECT_BYTES（70B @ 0x1274F4，LE）:
  +0x00: 01                        # enable（解析器成功路径写的值；无键数读者）
  +0x01: 00 00 00                  # io 描述 A/极性/padding（port=0→DIE 调用早退）
  +0x04: 01 00 10 00               # w=0x00100001（键通道字，mux 1；boot 用 0/4/5/6）
  +0x08: 00 00                     # 第三段门=0/参数（iokey init 第三段跳过）
  +0x0A: AJ 阈值表 20×u16: 01F4 02D0 0352 03FE 03FE ×16
         (=500/720/850/1022/1022…,升序,与 AJ §8.1 逐字相同)
  +0x32: AJ 键 id 表 20×B: FF 0A 0B 0C 00 ×16
         (=255/10/11/12/0…,键 id 分配=AJ §8.1 的 BW 掩码×App 表推导对应)
CFG523_KEY_CHANNEL_RES_IDLE = 4    # RES 行 0x10001 释放态档(接地分压代表值;
                                   # boot 期判定不依赖它——仅消 fd31 诚实停;区间 0..9
                                   # 内任意值同效,取中段远离边界)
```

电池相关字段：**无**。config-523 对象不含任何电池域字段；S15 低电门阈值
h[0x127734]=QCY+4468 属电池配置域（0x6011a08 默认链，config 20 缺省 3200mV
[文档：BU §3.3]），与本 fixture 无交集——S15/K1 的通过条件（battery_mv≥3200，
RES 推导 3780/4004mV [文档：BU/BA]）不因本 fixture 改变。

### 5.2 D1 场景的接入点

- **BA #13/P-13 行的补全**：BE 蓝图 §5 该行预案=「config-523 具名 fixture
  （0x6010b4 组装面或 VM 523 具名 112B）」——本设计落定为**组装面对象填充**
  （(a)），并把行内「VM 523」措辞修正为「记录区后端」（§1.1：523 不在 VM key 空间）。
- **D1-1 金丝雀**：`canaries.k2_0x60124ae` 期望 0 的前提=本 fixture 在场（无 fixture
  世界 K2 必触发=负例对照组，§4.3）；D1 验收记录 `model_assumptions` 栏 BE §6 已
  预留「BA 五预案 flag（…config-523 fixture）」——落名 `cfg523_named_fixture`。
- **D1-5 H 系**：窗口内键扫回调（≈每 10 tick 一次 read_key=255 幻零事件）是良性的
  第 4 类周期活动（Timer0/core-tick 之外无新 IRQ 源，回调在 tick 链内），H2 上界
  公式不受影响；R2/R3 观测项按实记录不阻塞。
- **D2-a 识别面**：boot 窗口内按键事件=无（idle 255）；识别/手势覆盖仍是 S/AJ 停点
  后场景的职责（表值已统一，见 §3.3）；将来若做「D1 世界内按键交互」（RES 档切换+
  真 tick），直接复用 `CFG523_OBJECT_BYTES` 表+`CFG523_KEY_CHANNEL_RES_IDLE` 之上的
  按下档（600/780/950，AJ 值）——同一对象、同一分档器、同一 handler 链。
- **(b) 升级预留**：`cfg523_record_bytes`（记录区条目 fixture）落地时以「解析产物 ==
  CFG523_OBJECT_BYTES」为验收断言（§2.3 契约式混合），D1 断言面零改动。

## 6. HANDOFF 条目草案（合并时贴入「已实现」节，照格式）[设计]

> - **路 BH（sim-cfg523，本次合并，只读设计）**：config-523 具名 fixture 实施规格书，
>   主交付 [docs/cfg523-fixture-design.md](docs/cfg523-fixture-design.md)。①**全图**：
>   523 走记录区只读后端（record 0x60c331c，key 512..700 兜底位；物理=bank B
>   0x1e0200/CPU 0x61de1e0=erased_bankb 第四窗；AB 的 VM 枚举从未见过它），解析器
>   0x60010b4 输入输出逐指令钉死（112B 记录={12B 头+20×5B 条目}→插值+排序→70B
>   对象；**幂等早退 0x060010c2：b[+0]≠0 直接返回对象、get_config 不跑**）；三读者
>   字段级矩阵（S9 iokey init 读 +0/1/2/4/8/9 并真写 [0x125320]；S17 只读 +0/+4
>   走 QCY 直偏移；read_key 经指针槽读 +0/4/10../+50..）。②**方案推荐 (a)**：70B
>   具名对象 `CFG523_OBJECT_BYTES`（AJ 三键表原样+enable=1+io 全 0+通道字 0x10001
>   /mux1 boot 空闲）在解析器入口 PC 锚定首装（BSS 清零覆盖→不能预填），解析器
>   早退自消费；配套 RES 行 0x10001=4（释放态接地分压，防 adc_scan 轮选启动转换
>   撞 fd31_adc_res_channel_not_modeled）。(b) 记录区字节 fixture=更真更重的独立
>   证据升级路（条目 id 编码/游标链未钉），D1 零依赖；(c)=以对象字节为契约的两层
>   实现。③**S17 消费面**：健康条件=walker(通道字)≤9（BU/Q 两处「值须>9」行文笔误
>   修正）；151 迭代×(+0 读+通道读+walker)+151 tick≈6M 步；walker 读队列存储值恒 1
>   （adc_add_sample_ch 初始 value=1/period=0、IRQ39 零交付、boot 采样环不打 mux1
>   ——三分支全过环）；窗口内键扫回调 read_key=255 良性。④**AJ 统一修正**：释放态
>   代表读数分世界——停点后场景维持 400，through-boot 世界须 ≤9（400 穿 S17 必
>   K2）；bin 语义/按下档/阈值分界不变。⑤registry：`cfg523_named_fixture` 进
>   conditional（probe_only 够不着真实启动入口；无 fixture 则 P6 后前沿落 K2 自旋
>   无法重钉——与 event_clock 同构论证），与 P7 实施路+前沿重钉同 PR 落地；check
>   骨架+两负例（无开关→K2/敌意通道字 0x10006→K2）。未改 tools/、未跑改动场景。

## 7. 未钉死处（如实标注）

1. **记录区条目 id/key 编码与游标链**（(b) 的前置）：get 0x6060e58 两级调用
   （找条目/拷数据）未逐指令钉；本路只钉了 8B 头格式（erased_bankb 第四窗注释）。
2. **R1=0x6001456（IO 配置 call②）写面**：mask 恒非零不可跳过；疑 P3_PORT_SEL/
   P11 域，未钉——fixture 世界首跑观测，若撞停点按容器族三选一（非本 fixture
   语义面）。
3. **R2=adc_scan 轮选行为**：period==0 立即返回语义（0x060348cc→0x060348e6）+
   [0x127D44] 门身份（item3+12 布局字 vs 独立忙锁）+ CON EN 锁存三者交织——启动
   频度首跑观测；任何分支 S17 判定不变（§3.4）。
4. **0x6085094 call① 的非零 port 分支写面**（GPIO_BASES RMW 需 gpio_model）：设计
   已避开（b[+1]=0），未验证非零路径。
5. **真实 config 523 内容**（通道字/阈值/键 id/io 表）：bank B 记录区，公开工件外
   ——一切值 synthetic-not-measured；将来真机 FD07 读记录区窗可升级（路 BU §6 同款
   通道，独立决策）。
6. **S9/S17 的 −22/K2 至今零动态观测**（当前前沿在 W1[3]，S9 未执行过）——本设计
   全部 S9/S17 行为为静态结论，P7 实施路首跑即动态复核。

## 8. 红线核对（本路自查）

- 设计不实施：未改 tools/ 任何文件、未跑任何改动场景（git 状态可查）；
- 锚点全部标出处；静态结论 derived-not-measured；fixture 值 synthetic-not-measured；
- 对既有记录的修正（BU §3.5/Q §2.4 的「>9」行文、BE §5 BA #13 的「VM 523」措辞、
  Q §2.4「enable/键数」双标签）均附指令锚点，不改动原文档（实施路落地时按
  仓内勘误惯例处理）；
- 提交不含 Co-Authored-By、不含私有路径/私有工件名；`check_local.py --units` 绿
  （文档路 trivially，已跑）。
