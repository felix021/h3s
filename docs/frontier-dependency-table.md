# 剩余执行依赖表：前沿→S19 的函数级路障清单（路 AY / sim-deptable，只读预研）

2026-11-14。本页回答 codex 全局复盘点名的「最值钱补充」：**到 init 表尾还剩哪些 initcall、
每个已返回的 initcall 留下了什么异步工作（任务/定时器/队列消费者/IRQ 向量）、S19 消息循环
被谁跑、无 BT 连接状态能否进 S19**。全部结论 [静态]（本路反汇编）或 [文档]（既有
docs/HANDOFF 钉死条目），运行状态栏以**已合并的前沿报告 3,137,573 @ 0x60719c0**
（p11_heap_experimental_config，路 AN）为准——本路不跑任何改场景的 probe，AS 路
（ADC reset 写族放行）在途期间以该报告为基线。**全部 derived-not-measured**。

**方法**：官方 app.bin（sha `18f3d573…`，load base `0x06000100`）+ 钉死 codec
pi32v2-isa @ `3608ed1`（克隆于本地 tmp，不进仓）；一次性脚本在 `~/tmp/`（不进仓）；
零 `tools/` 改动、零 probe 运行（check_local --units 只作回归护栏）。函数体界定=首次
返回形指令（`rts`/`pc=[sp…]`/`{pc,…}=[sp++]`），逐 call 目标按指令尾+偏移精确计算；
并行对（`.p` 后缀 `#`）按 FM-1 run3 语义（B 半读 A 半旧寄存器）读参数。

## 0. 摘要（四个问题各一句话）

1. **initcall 全表**：app_core 尾段共 **4 段指针表 walker + 1 段 struct 表 walker =
   23 个函数指针 + 1 个 struct 项**；其中 **W1[0..2] 已返回、W1[3]（0x602ea68 音频
   init）在途**（前沿停在其调用树内 0x60719c0），剩余 **W1[4]（纯桩）→ 0x6011a08 →
   S9（键扫 sys_timer 注册，条件=config 523 在场）→ W2（11 项，含 jlstream 任务
   创建）→ W3（1 项）→
   W4（6 项，含 VLVD/低功耗域）→ S11..S19**。
2. **异步工作清单**：当前世界已存活的消费者=**app_core（运行中）+ idle×2 + pmu_task +
   Timer0 tick 链（IRQ4→0x603622C→sys_timer 轮询）+ IRQ39（ADC 队列生产者）+
   sys_timer 0x60349d2（周期 ADC 采样启动器）**；W1[3] 之后将新增 **IRQ68（音频帧
   DMA）、'anc' 任务、jlstream 任务、键扫 sys_timer（10 tick，**条件=config 523 在场**，
   擦除态跳过）、dev_mg 任务、（条件）usb_stack 任务、（充电时）0x6033F36 去抖
   sys_timer**——BT 三任务**全部不在 init 序列**（见 §3）。
3. **S19 判定**：S19 由 **app_core 任务体自身**线性执行到达（不是别的任务跑它），
   前置只有「init walker 走完 + 电池/充电检查健康（K1/K2 金丝雀不触发）」；
   **无 BT 连接、无 BT 任务、无控制器 ready 握手也能进 S19 并空转**——btstack 的
   创建点在 S19 分发表 handler 0x60160fc（event **0x100004**）内，即 BT 是 S19
   **启动后的事件驱动分支**，A2DP/ACL 更是连接后触发。
4. **阻塞链排序**（codex 五级映射后见 §4.3）：音频 init 完成条件（AS 放行族）→
   W2/W4 尾段 initcall（多数纯 RAM，两个新边界簇：VLVD N4 族、W2[8] IRQ91 注册
   依赖 RAM-code 执行）→ S9/S11 定时器与任务（IPI 往返）→ 电池/充电条件段
   （N2 条件族 + K1/K2 金丝雀）→ S18/S19（纯 RAM+OS 队列）→（S19 之后才是 BT）。

## 1. initcall 全表（walker 结构本路重新钉死）

### 1.1 walker 循环的精确形状 [静态，本路新钉]

app_core 尾段 `0x601219c..0x6012254` 共 **4 个指针表 walker + 1 个 struct 表
walker**，逐条 `r0=[r4++=4]; call r0` 无条件调用（0x601222c 段同形）：

| walker | 循环站点 | 表界 [静态] | 项数 |
|---|---|---|---|
| W1 | 0x60121b4..0x60121c6 | `[0x60C32A4, 0x60C32B8)` | 5 |
| — | 0x60121ca `call 0x6011a08`（非 walker，config 101 装载） | — | — |
| S9-struct | 0x60121ce..0x6012212 | `[0x60C0E20, 0x60C0E30)` 16B 项 | 1 |
| W2 | 0x6012216..0x6012228 | `[0x60C3250, 0x60C327C)` | 11 |
| W3 | 0x601222c..0x601223e | `[0x60C32B8, 0x60C32BC)` | 1 |
| W4 | 0x6012242..0x6012254 | `[0x60C328C, 0x60C32A4)` | 6 |

表内容逐字 dump 与 app.bin 字节互证（与 tail-map §1 一致）。**修正 CI（d2b-framework-task
§2.1）一处转写错误**：CI 记的「0x60C3298 单条、0x60C327C..0x60C3294 七条」实为
`0x60C32B8 单条（W3）`与`[0x60C328C,0x60C32A4) 六条（W4）`；CI 的 W2 十一项指针
列表本身正确。执行顺序 **W1 → 0x6011a08 → S9 → W2 → W3 → W4**（tail-map 的
「W1(5)+W2(11)+W3(1)+W4(6)」计数正确但未给顺序；AGENTS 的「W1[1]」编号=本表 W1 下标）。

### 1.2 全表（状态以已合并前沿报告 3,137,573 为准）

**pre-tail（已执行，非本表重点，列作上下文）**：A1 平台 init 表 `0x60c32c8..0x60c3338`
4×28B（路 U 钉：cfg_tool/btif 存储后端族）；B1 `0x60c327c..0x60c328c` 4 指针
（`0x60001b6/0x6004954/0x60124ba/0x60759ae`）；power_init（0x6067876，路 W 及其后全链）。

| # | 表:下标 | 函数 | 功能 [证据] | 状态 | 返回后遗留的异步工作 |
|---|---|---|---|---|---|
| — | S7 | 0x6065d86 | `request_irq(39, prio1, 0x60348e8)`：IRQ39=ADC 队列生产者（EMA+周期推送 0x127D08，路 Q）[静态+文档] | **已执行**（W1 walker 前） | IRQ39 向量：pend=ADC 转换完成；post=ADC 队列 |
| — | S8 | 0x6066c26 | `sys_timer_add(cb=0x60349d2, 5 tick, arg=0)`：周期 ADC 采样启动器（cb 内 0xfd3100 CON + 0x6011666 ADC 控制族）[静态；0x6066c26=sys_timer_add 按 BZ §2.3] | **已执行** | 5 tick 周期定时器（tick=10ms [文档：BZ]） |
| 1 | W1[0] | 0x6012e3e | 充电检测（SDK check_charge_state 同形 20 轮，路 CB）+ 0x6068152 时钟模式切换（路 CL）+ 0x6065c3a 调度助手；本世界无充电（p3_ana_read_ldoin_low）→ **0x6033F36 去抖 sys_timer 未注册**，超时侧写 b[0x125BF4]=2 | **已返回**（3,070,786..3,073,918 前缀内，CG/CL/CQ 域） | 充电在场时才有 2 tick 去抖定时器（本世界无） |
| 2 | W1[1] | 0x601321e | 薄包装 → 0x6013198(cfg 表 0x609d9c4)；树内调度助手 0x6065c3a（CG/CL 放行点）[静态+文档] | **已返回** | 无直接；调用链内 0x6069538 等属 pre-tail 定时器 |
| 3 | W1[2] | 0x6013246 | 系统配置头段（b[QCY+2972]==0 → get_config(528/532)）；**执行树含 uart_init 事务 0x6034xxx + OMAP/IMAP pinmux**（前沿序列 3,071,380..3,073,918 的路 F/M/R/V/AA 停点族；精确归属本项 [推断]，停点序列为证） | **已返回** | UART0 初始化完成（CON/BAUD/CON1 写族+读回投影）；无任务/定时器 |
| 4 | W1[3] | **0x602ea68** | **boot 期音频框架 init**（698 指令/87 call，AG 图全图）：场景 walker×5→audio_adc_init（**当前停点 0x60719c0 在其体内并行对首写 0xfe0538**）→DAC init SFR 簇（AK 域已放行）→**IRQ68 注册 0x602ec28**→框架尾块（zalloc 572B ANC ctx 等）→**'anc' 任务创建 0x602f30a** | **在途**（前沿 3,137,573 停在树内） | 见 §2：IRQ68（音频帧 DMA，需 D2-b 注入或真音频才触发）、'anc' 任务、音频框架队列/链表对象 |
| 5 | W1[4] | 0x60614a2 | **纯桩**：`r0=0; rts` [静态，本路钉死] | 未到 | 无 |
| 6 | — | 0x6011a08 | config 101（32B）装载到 QCY 0x1248C0 区，缺失走 ROM memset 缺省 [静态] | 未到 | 无 |
| 7 | S9 | struct walker | 项 {flag=1, pdata=0x124888, fn=0x6000e94, init=0x6001866}；init=**0x6001866=config 523 解析器链**（call 0x60010b4，失败返 -22）；**init 返回 0 才走** `sys_timer_add(cb=0x6001944, [0x124898]=10 tick, arg=项指针)`——**10 tick 键扫/去抖定时器**（cb 0x6001944 经项内 fn 0x6000e94=分档查询，路 Q 全链）。**擦除态世界 config 523 缺失→init 返 -22→定时器不注册**（与路 Q「键链整体旁路」互证）[静态；tail-map「task create fn 0x6000e94」系误读，本路按 BZ 口径修正] | 未到 | 键扫 sys_timer（10 tick；**条件=config 523 在场**） |
| 8 | W2[0] | 0x6004360 | GPIO 上电时序（0xfd5208 族 RMW|=0x10/&=~0x10 对×多组+ROM delay）+ 3 次电压采样（0x6064efc(2) 标志等待有界 3 轮、阈值 1010）→ 写 h[0x12489C] bit13 [静态] | 未到 | 无任务/定时器；依赖 GPADC 事务（fd31 域已建模） |
| 9 | W2[1] | 0x60074e6 | **纯桩**：`r0=0; rts` [静态，本路钉死] | 未到 | 无 |
| 10 | W2[2] | 0x601043a | `call 0x60c28c8`（flash 尾段代码，未命名）+ 挂回调 0x601039a 进槽 [0x1265C0+286·4]=0x126A38；cb 按参数码 0x40000B/0x400009/缺省三分支从 0x60a2134 描述符表取 16B 交 0x60c287c [静态] | 未到 | 回调槽注册（消费者=flash 尾段命令域） |
| 11 | W2[3] | 0x6027658 | **纯桩**：`r0=0; rts`（ANC init 不在此处）[静态，本路钉死] | 未到 | 无 |
| 12 | W2[4] | 0x602f5a8 | 挂回调 0x601d834（uart 域）进 QCY+5132 槽（0x1279CC）[静态] | 未到 | UART 回调槽注册 |
| 13 | W2[5] | 0x603346e | pc_spk（扬声器）框架对象：链表自指 init + q_init [静态] | 未到 | 队列对象（纯 RAM） |
| 14 | W2[6] | 0x6033678 | q_init [静态] | 未到 | 队列对象 |
| 15 | W2[7] | 0x603371e | q_init + 'flash/EXT_RESERVED/' 资源域引用 [静态] | 未到 | 队列对象 |
| 16 | W2[8] | 0x607bf2e | `call 0x6072b24`（**request_irq(91, prio1, handler=0x12ec02)**——编号/句柄来自配置表 0x124C40[0]，handler 在 RAM-code 窗）+ q_init [静态] | 未到 | **IRQ91 向量**（RAM-code handler；pend 来源未钉——音频/codec 域 [推断]） |
| 17 | W2[9] | **0x60759ee** | **jlstream 任务创建**：`task_create_byname(0x6086980, 0, 'jlstream')`（CI §2.1 钉死；无静态调用者，唯一数据引用=本表项）[静态+文档] | 未到 | **'jlstream' 框架主任务**（消息循环，pend 自己 taskq） |
| 18 | W2[10] | 0x607f8be | 空双向链表头 init（0x1266CC 自指）+ROM memset [静态] | 未到 | 无 |
| 19 | W3[0] | 0x60009e8 | q_init(0x126D70) + 0x6000978(0x609c4f4, 24000000)：命名资源注册表 init（strcmp+zalloc+注册链）[静态] | 未到 | 资源注册表对象 |
| 20 | W4[0] | 0x600ec2e | `0x600ebb6(0x124a12)`：VM 配置块注册（版本/魔数校验+装载；邻域 get/set 走 config 键 46/101 族）[静态] | 未到 | 配置块状态对象 ×4（W4[0..3]） |
| 21 | W4[1] | 0x600ecc6 | `0x600ebb6(0x124a6b)` 同上 [静态] | 未到 | 同上 |
| 22 | W4[2] | 0x600ed30 | `0x600ebb6(0x124a75)` 同上 [静态] | 未到 | 同上 |
| 23 | W4[3] | 0x600edf8 | `0x600ebb6(0x124aae)` 同上 [静态] | 未到 | 同上 |
| 24 | W4[4] | 0x6034c80 | `call 0x6068662` + `call 0x60688e6`：**低电压检测/低功耗域 init**——VM 读 0x6000f88、**0xf2c244（P3_VLVD_CON）RMW（=tail-map N4 家族首位）**、P33 f2c10c 族、0x12cd26（TIMER2 忙等 idle，BP §1 钉「健康路径不可达」）、M2P 0xf27f92、0x6068844 链（VM 写 0x6001f12+pend 0x12b55c+0xfd0020 RMW）[静态] | 未到 | **新边界簇**（N4/N3 域 SFR），无任务 |
| 25 | W4[5] | 0x6066f90 | `0x606784a(0x609efa0)`：PMU 回调注册（zalloc 16B 节点挂链 0x129A5C；cb=0x609efa0→0x609f09c，域未逐层命名）[静态] | 未到 | PMU 回调节点 |

W4 紧邻的 'DBG_EQ' 字符串（0x60a1b08）与 sys_timer_add×2/request_irq×3 属
0x6034c92 **之后的相邻函数**（在线调音/DBG 通道域），**不在 initcall 体内**——此前
线性扫描易把它们误记到 W4[4] 头上，本路按函数体界定排除。

「到 init 表尾」剩余项 = 上表 #4 尾段、#5..#25 全部（18 个实质条目 + 3 个纯桩），随后 S11
（q_init/q_create + **dev_mg 任务创建** 0x6012288 `task_create_byname(0x6000d6c, 0,
'dev_mg')`）→ S12（VM 读）→ S13（充电条件分支，N2）→ S14（**门 b[QCY+628]**：非零
→ `task_create_byname(0x600741c, 0, 'usb_stack')`（0x601231c）+b[0x1249f6]=1+直跳
S18；零 → S15..S17 电池路径）→ S18（post (18,0)/(20,2)）→ **S19**。

> **与 CT 路（usb-audio-modeling）的口径并存**：CT 记「usb_stack 任务体=0x6035840
> （os_task_create 实参 @0x6007028 复证）、0x600741c=挂 OTG 事件源的消费者（注册
> @0x6012316）」；本路 S14 站点指令锚定为 `0x6067242(entry=0x600741c, pdata=0,
> name='usb_stack' @0x60a1d38)`（0x6067242=task_create_byname 有 CI 里程碑互证）。
> 两静态口径并存，同名双实体的关系（如 0x6035840 是否惰性建、S14 先建后查重拒绝）
> 属运行时观测项——**对 S19 判定零影响**（两侧都直达 S18，usb_stack 域整体在
> CT 的「调度器分发墙后」结论内）。

## 2. 异步工作清单（codex 点名的漏项面）

### 2.1 任务（39 条注册表 @0x609d780 全量重读，字段布局 {name,prio,flags,stack,qlimit}）

**本路修正 CI §1.2 一处计数**：注册表共 **39 条**（CI 记 38；哨兵 0 @0x609d954 一致，
最后一条 'mic_effect2' @0x609d93c 被漏）。按创建点归属分四类：

**(a) 已存在（当前前沿世界，[文档] milestones 4 次 0x6065f9e 帧写入互证）**

| 任务 | 入口 | 创建点 | pend 什么 | 谁 post |
|---|---|---|---|---|
| app_core | 0x6012028 | 0x6035350 启动链 | S19 起 pend 自己 taskq（0x6064c8c(sp,16)） | 0x6000cba（硬编码 id 0x10000C）等 |
| idle×2 | 0x12c57e | 0x60652c8 per-core | 永久 idle（0x12c5ca park） | 调度器 |
| pmu_task | 0x6068f0c | 0x6067a0c（power_init 内，路 X） | 自己 taskq（get_event+pend；'DBG_REV' 域） | 电源域事件 |

**(b) init 序列将创建（全部在当前前沿之后）**

| 任务 | 入口 | 创建点（init 序内位置） | pend 什么 | 谁 post |
|---|---|---|---|---|
| **anc** | 0x602d3a0 | **W1[3] 尾块 0x602f30a**（name='anc' @r8+70，r8=0x60a18c0 本路钉死；AG 图只记「设备表 open」） | 自己 taskq（get_event+os_flag_wait 0x6064efc） | 0x6064d72('anc',…)——按键 cmd 11 链 0x602544e→0x6018b60→…（路 AO）与 ANC 命令域 0x602770a |
| **jlstream** | 0x6086980 | **W2[9] 0x60759ee** | 自己 taskq（buf 8 字；cmd 4..12 tbb 9 分支） | 0x6075414（stream-create 请求=cmd5，CK 实测）、0x6074192（框架内部事件）、0x6086aaa/0x6086b8a（自队列） |
| **dev_mg** | 0x6000d6c | **S11 0x6012288** | 自己 taskq（'sdfile' 域+get_event） | 设备管理事件（插拔/USB） |
| usb_stack | 0x600741c | **S14 0x601231c，条件=b[QCY+628]≠0（USB 在位门）** | 自己 taskq（get_event+pend） | USB 设备事件 |
| jlstream_N (≤8) | 0x6086c8c | 0x6074db8（stream create 内惰性 ensure，CI §2.2） | 100-tick sys_timer+事件位图 | 框架数据推进 |
| dac | 0x6076b4c | 0x6075276（首次 DAC 打开事件，CI §2.3） | 音频缓冲水位 | 音频框架 |

**(c) S19 之后才创建（分发表 handler 域——btctrler/btencry/btstack 全在此类）**

| 任务 | 创建点 | 载体（handler/任务） |
|---|---|---|
| **btstack** | **0x60162de**（entry 0x605934e，name='btstack' @0x60a1bc8） | handler **0x60160fc（表1[13]，filter=1，event 0x100004）** 体内 [静态，本路钉死] |
| btctrler/btencry 等 | 0x6015a10 / 0x6016346 / 0x601ffbe / 0x6020ff4 等 | 均在 0x6015xxx..0x6020xxx handler/任务域 [静态，CI §2.4 站点表+本路复核] |
| update（升级） | 0x60c28b8（RAM-code 窗） | 升级命令域 |

**(d) 注册表其余（update/tws_ota/aec/audio_vad/ui/rcsp/CVP_RefTask/mic_effect1/2 等）**：
全部为按需/事件驱动创建（registry flags=1 组 CI §8.1 已注「不随 init 建」），init 序列
零触。

### 2.2 sys_timer（tick=10ms，Timer0 驱动，[文档] BZ 全钉）

| 定时器 | 回调 | 周期/到期 | 注册点 | 状态 |
|---|---|---|---|---|
| tick 链 | 0x603622C（计数+累计+轮询） | 每 tick | tick_timer_init 0x6066c78（~110.8k 步） | **活着**（IRQ4→0x12ece8→0x12ecd4→cb） |
| LRC trim | 0x606750c | 115 tick 一次性 | power_init（路 AF 误记「任务创建」，BZ 修正） | **已到期摘链** |
| GPCNT 使能判定 | 0x6069538 | 115 tick 一次性 | power_init（同上） | **已到期摘链**（601,880 停点即其体内） |
| ADC 采样启动器 | 0x60349d2 | **5 tick 周期** | S8 0x60121ae | **活着**（W1 walker 前注册） |
| 键扫/去抖 | 0x6001944→0x6000e94 | **10 tick 周期**（[0x124898]=10） | S9 0x601202 | **未注册**（W1[3] 之后；且**条件=config 523 解析成功**——擦除态世界 init 0x6001866 返 -22 跳过注册，与路 Q 键链旁路互证） |
| 充电去抖 | 0x6033F36 | 2 tick | W1[0] 充电在场时 | 本世界**未注册**（无充电 fixture） |

### 2.3 IRQ 向量（init 序列涉及的）

| IRQ | handler | 注册点 | 状态 | 触发来源 |
|---|---|---|---|---|
| 4 (Timer0) | 0x12ece8 | tick_timer_init（BE 路） | 活着 | STD12M 1ms×10 模型周期 |
| 3 (core tick) | core tick 三件套 | BF 路 | 活着（IPMASK 优先级门） | 4×步进周期 |
| 39 (ADC) | 0x60348e8 | S7 0x601219c | **活着，零交付** | fd31 ADC 转换完成（采样事务经 BY 的 PND 读时投影完成、不经中断；IRQ39 交付接线未见 [文档]） |
| 68 (音频帧) | 0x602f406 | **W1[3] 0x602ec28（未到）** | 未注册 | ADC/DAC DMA 帧（D2-b 注入域） |
| 91 | 0x12ec02（RAM-code） | **W2[8]→0x6072b24（未到）** | 未注册 | 未钉 [推断]音频/codec 域 |
| 67 (ANC) | — | 0x606ebe0 ANC 条件族 | 本世界不可达 | ANC 图门打开时 |

### 2.4 队列/事件面

- **S18 预置事件**：(18,0)/(20,2) 以 id 0x10000C 入 app_core taskq（BZ 全钉：(18,0)
  由表1[39] 0x6023962 认领、(20,2) 走特路 sys_timer 查 0x126F3C 链表 id=2——运行时
  观测项）。
- **分发表**：表1 54 条 + 表2 6 条 = 60 条 {filter,event_id,handler}（本路全量 dump 与
  BR 计数一致；表2[5]=按键 handler 0x60252c6、表1[43]=0x6023962、表1[47]=tone 0x6023a0a
  互证）。**事件生产者→handler 是 S19 的全部工作**；60 条 handler 是「init 返回后的
  异步消费者」主体。
- **0x100004 家族**（8 个 handler，含 btstack 创建者 0x60160fc）：全 app 唯一生产站
  **0x601670c**（`post_evt_name(queue='app_core' 0x60a1d11, 0x100004, 3 字载荷)`，
  **直接落进 S19 的队列**），位于 0x60166xx 函数（handler 域）内 [静态，本路扫描]
  ——即 0x100004 本身也要先有事件进 S19 才被生产出来（开机提示音完成/应用启动链
  [推断]）。

## 3. S19 依赖判定

### 3.1 S19 由谁跑、怎么进 [静态+文档]

S19（0x60123e6 循环）是 **app_core 任务体 0x6012028..0x60124b4 的线性尾段**——不是
独立任务，也不需要任何别的任务先跑：OS 调度器选中 app_core（已发生，当前前沿就在
app_core 帧内）后**一路顺序执行**（S1..S18 全程无主动让出点；0x6064efc 等待为有界
自旋）。因此「进 S19」=「S2..S18 顺序路径走完」，前置闭包为：

1. W1..W4+S9 initcall 全部返回（§1 表 #4..#25）；
2. S13 充电分支（b[QCY+3464]==2 才走 N2 簇——条件性）；
3. S14 门（b[QCY+628]）选 usb_stack 侧或电池侧，两**都能**到 S18；
4. S15..S17 电池/ADC 检查健康（K1/K2 金丝雀不触发=方向正确信号，tail-map §4）；
5. S18 两次 post（纯 RAM+OS 队列）。

### 3.2 裁决：无 BT 连接状态能否进 S19 —— **能**

- **顺序路径 S11..S18 零 BT 引用**：边界簇只有 N2（电源框架 0x6067f84）/N4
  （P3_VLVD 0xf2c044 族）/K1/K2（电池金丝雀）——全是充电/电池域 [文档 tail-map+本路复核]。
- **BT 任务不在 init 序列**：btstack 创建点钉死在 S19 分发 handler **0x60160fc
  （表1[13]，event 0x100004）** 体内（0x60160fc..0x6016370 无中间返回形指令，0x60162de
  在其中）；btctrler/btencry 等其余创建点（0x6015a10/0x6016346/0x601ffbe/0x6020ff4，
  CI §2.4 站点表+本路复核）也全部位于 0x6015xxx..0x6020xxx 的 handler/任务域——
  init walker 表与 S11..S18 顺序路径零 BT 引用。
- **BT 控制器 ready 握手更靠后**：在 btstack 任务/0x100004 事件链（BZ §1.5 深层
  0x601a5xx「BT/提示类」域）内，即 S19 已在运转之后。
- **A2DP/ACL 拆分**（codex 问题 2 裁决）：两者都是**连接后触发**——启动必须项里既无
  A2DP 也无 ACL；空闲态 S19（空队列 pend+两核 park）不需要任何 BT 状态。

推论：D1 验收（进 S19+空队列阻塞+两核 park）**不需要建模 BT**；BT 域属于 S19 之后的
事件驱动分支（D2+/协议域）。

## 4. 依赖表主体

### 4.1 主表（前沿→S19；状态=已返回/在途/未启动；等待来源=该事务停下时等什么）

| 函数/事务 | 状态 | 后继依赖 | 等待来源 | 风险 |
|---|---|---|---|---|
| W1[3] 0x602ea68 内 0x60719c0 写 0xfe0538（ADC 节点 reset 并行对首写） | **在途（=当前前沿）** | AS 路（ADC reset 写族）放行后：函数尾块 DAC init（AK 域已放行）→IRQ68 注册→'anc' 创建→返回 | unmapped 写（模型门） | 低（AS 在途） |
| W1[3] 尾块（zalloc 572B ANC ctx、0x6067242 'anc' 创建、get_config 59/61/183、0x601bd3a ANC 图门=条件） | 未启动 | 帧池/堆 RAM；'anc' 创建触发 IPI 往返（既有机制） | 无（RAM）+IPI | 中：ANC 图门若打开撞 JL_ANC 0xfe0400 族（AG §3.3b，未钉） |
| W1[4] 0x60614a2（桩）→0x6011a08（config 101） | 未启动 | 无 | 无 | 低 |
| S9 键扫 sys_timer 注册（**条件=config 523 在场**；擦除态世界 init 返 -22 零注册直接过） | 未启动 | config 523 解析（VM 域）；在场才挂 sys_timer 轮询链 | 无 | 低 |
| W2[0] GPIO 时序+3×电压采样 | 未启动 | GPADC 事务（fd31 域）；0x6064efc 有界等待 | OS 标志（有界 3 轮） | 中：等待轮数×ADC 事务步数 |
| W2[2]/W2[4] 回调槽安装 | 未启动 | 无 | 无 | 低 |
| W2[5..7]/W2[10]/W3[0]/W4[0..3] 队列/链表/配置块 init | 未启动 | 无 | 无 | 低（纯 RAM/ROM 工具） |
| W2[8] IRQ91 注册（handler=RAM-code 0x12ec02） | 未启动 | RAM-code 窗执行（已映射）；触发源未钉 | 无（注册即 RAM 写） | 低（注册本身）；触发面未钉 |
| **W2[9] jlstream 任务创建** | 未启动 | TCB/帧池/名字注册；调度器 IPI | 无 | 低（既有机制，CI/CK 已直调验证过同链） |
| **W4[4] VLVD/低功耗域** | 未启动 | **N4 簇 0xf2c244 RMW（首位）**+f2c10c 族+0x12cd26 TIMER2 忙等（BP 钉健康路径不可达） | N4 读回语义（BV 域红线邻域） | **高：S19 前最可能的新停点族** |
| W4[5] PMU 回调注册 | 未启动 | zalloc+挂链 | 无 | 低 |
| S11 dev_mg 任务创建（+条件 0x12cc40=N4 金丝雀，仅创建失败分支） | 未启动 | task_create_byname 链 | 无 | 低（失败分支才触 N4） |
| S13 充电分支（b[QCY+3464]==2） | 未启动 | N2 簇（0x6067f84：0xfd0200 读+8 写only）——**条件性**，本世界值未钉 | 条件分支值 | 中：若走进 N2 需 BH-a 预案 |
| S14 门 b[QCY+628]（usb_stack vs 电池路径） | 未启动 | 门值写者未钉 [推断]充电检测链 | 条件分支值 | 中：两侧都能到 S18，但 usb_stack 侧多一个任务创建 |
| S15..S17 电池/ADC 检查（11×11 循环、≤150 重试） | 未启动 | 0x6000e74 电池读（GPADC）；N2 传递族 | 有界轮询 | 中：K1/K2 金丝雀=方向错误信号（tail-map §4：健康投影下不可达） |
| S18 两次 post | 未启动 | OS 队列（BZ 全钉） | 无 | 低 |
| **S19 消息循环** | 未启动 | §3.1 闭包全部走完 | **空队列 pend（B9 idle 入口）→两核 park→事件时钟** | 低（纯 RAM+OS；BI/BJ 域） |
| （S19 后）0x100004 事件→0x60160fc→**btstack 创建**→BT 控制器握手→A2DP/ACL | 未启动 | §2.4 事件链（0x601670c 生产 0x100004） | 事件+BT 硬件 | 高（BT 域整体未建模，但**非 S19 前置**） |

### 4.2 已返回 initcall 的异步遗留汇总（codex 漏项面收口）

已返回的 initcall（pre-tail + W1[0..2] + S7/S8）遗留：Timer0 tick 链、两个已到期的一次
性 sys_timer（已摘链）、活着的 5 tick ADC 启动器、IRQ39 向量（活、零交付）、pmu_task、
充电检测写入的 RAM 标志（b[0x125BF4]=2，W1[0] 已写；h[0x12489C] 属 W2[0] 未到）。
**唯一会在未来显形的是 IRQ39 交付与 5 tick 定时器到期**——二者都依赖 fd31 ADC 域
继续运转（已建模），BI/BJ 窗口内它们是 Timer0 周期面的一部分。

### 4.3 阻塞链排序（codex 五级的锚点修正版）

1. **音频 init 完成条件** = AS 放行 0xfe0538 写族 → W1[3] 返回（尾块全 RAM+已放行域，
   唯一条件族 ANC 图门未钉）。
2. **异步 init+事件调度** = W1[4]/0x6011a08/S9/W2（含 jlstream/IRQ91）/W3/W4（含
   VLVD N4 簇——本级新边界）+S11 dev_mg。
3. **双核唤醒** = 任务创建的 IPI 往返（既有机制；CPU1 已 park 0x12c5ca，11,216 步
   不变）+S19 空队列后 CPU0 也 park（BI 域）。
4. **BT 控制器 ready 握手** = S19 分发 0x100004→0x60160fc→btstack 创建→握手
   （**全部在 S19 之后**，codex 排序中它挡的是「后续配置」不是 S19）。
5. **后续配置** = D2 音频流/EQ/协议域。

### 4.4 AS 放行后预期路径预演（[静态]+[文档] 推演，非实测）

0x60719c0 写族放行 →（AG 图 §2.3 序）0x60719a6 其余纯 RAM → 0x602eb86 框架节点
walker → 0x6071bca DAC init SFR 簇（**AK 域 audio_dac_init_writes 已覆盖，零新边界**）
→ get_config(58/88) G4 跳过 → **IRQ68 注册（RAM）** → G5 跳过 → G6 跳过（esco 块
不执行）→ 尾块（zalloc 族+config 59/61/183+0x6067242 'anc' 创建+结构填充）→ 返回 →
W1[4] 桩 → 0x6011a08 → S9（擦除态世界=config 523 解析返 -22、零注册直接过）→
**W2 十一项**（jlstream 创建+IRQ91+队列族）→ W3 →
**W4（VLVD 0xf2c244=预期下一新停点族，N4 语义决策）** → S11 → S13/S14/S15..S17
（条件/金丝雀域）→ S18 → **S19**。预期新停点序列（置信度递减）：①W4[4] 0xf2c244
RMW 读（N4 三选一）；②S13 N2 簇（若门开）；③W2[8] RAM-code handler 域（仅触发时）；
④S15..S17 的 N2 传递族。到 S19 无必须的输入类 fixture（电池/充电检查消费的是已建模
GPADC 域）。

## 5. 对既有记录的修正清单

| 记录处 | 原记载 | 本路修正 |
|---|---|---|
| d2b-framework-task §2.1 | 第三/四段表「0x60C3298 单条、0x60C327C..0x60C3294 七条」 | `[0x60C32B8,0x60C32BC)` 单条（W3）、`[0x60C328C,0x60C32A4)` 六条（W4）；顺序 W1→S9→W2→W3→W4 |
| d2b-framework-task §1.2 | 38 条任务注册表 | **39 条**（'mic_effect2' 漏计；哨兵地址不变） |
| appcore-tail-map §2 S9 | 「struct walker: init+**task create** fn 0x6000e94 prio 10」 | S9=**sys_timer_add(cb=0x6001944, 10 tick)** 键扫定时器注册（0x6066c26 thunk；BZ 口径的 site-specific 落实）；fn 0x6000e94=定时器到达后的分档查询目标 |
| appcore-tail-map §2 S8 | 「task create 0x6066c26 (name 0x60349d2)」 | S8=**sys_timer_add(cb=0x60349d2, 5 tick)**：周期 ADC 采样启动器（cb 内触 0xfd3100 CON 写+0x6011666 控制族） |
| wlaud-init-map §2.2/AGENTS | initcall 表「6 项」混计 W1+W3 | W1=5 项（0x60614a2 为桩），W3=0x60009e8 单项另表 |
| wlaud-init-map §2.3 尾块 | 「0x6067242 设备表 open（未名）」 | 0x602f30a=**task_create_byname(entry=0x602d3a0, name='anc')**——'anc' 任务（AO 路 ANC ctx 0x602eeb2 的消费者本体） |

## 6. 未钉死处（如实标注）

1. **W1[2] 与 uart_init 事务的精确归属**：3,071,380..3,073,918 停点序列证明 uart/pinmux
   事务在 W1[1] 之后、W1[3] 之前执行完；按 walker 顺序归于 W1[2] 执行树 [推断]，未逐
   call 复核。
2. **S13/S14 门字写者**（b[QCY+3464]/b[QCY+628]）：字面量零引用（经基址+偏移访问），
   静态溯源止于「充电/USB 在位检测链 [推断]」（CB 路 LDO5V/LVCMP 比较器族是最近候选）。
3. **IRQ91 触发源**：注册形状钉死（0x124C40[0] 表驱动），硬件语义未命名。
4. **0x100004 的完整生产链**：唯一 post 站 0x601670c 已钉，其上游（开机提示音完成链
   [推断]）未逐层展开。
5. **ANC 图门**（0x601bd3a 三条件）运行时真值：AG §7.3 已标，本路维持——若门开，
   W1[3] 尾块撞 JL_ANC 0xfe0400 族。
6. W2[2] 的 0x60c28c8/0x60c287c、W4[5] 的 0x609efa0 深层语义未逐层命名（登记为 init
   序内执行即可，无边界风险迹象）。
7. **S14 usb_stack 双口径**（见 §1.2 尾注）：本路指令锚定 vs CT 路任务体归属，
   同名双实体关系待运行时观测；S19 判定不受影响。

## 7. 证据锚点

- walker 循环四段+S9 struct 段：0x60121b4..0x6012254 逐指令（codec pin 3608ed1）；
  表界 li 立即数 0x60C32A4/0x60C32B8/0x60C3250/0x60C327C/0x60C32B8/0x60C32BC/
  0x60C328C/0x60C32A4。
- 表内容 dump：0x60c32a4..0x60c32bc / 0x60c3250..0x60c327c / 0x60c328c..0x60c32a4 /
  0x60c0e20..0x60c0e30（app.bin 原字节）。
- 任务注册表 39 条：0x609d780..0x609d954（12B 记录逐字段）。
- S8/S9 sys_timer：0x60121ae（call 0x6066c26(0,0x60349d2,5,0)）/0x6012202
  （并行对 cb=0x6001944、r2=[0x124898]=10）；0x6066c26→0x6066ba6 按 BZ。
- 'anc' 创建：0x602ec62 r8=0x60a18c0、0x602f2fe r2=r8+70→0x60a1906='anc'、
  0x602f302 r0=0x602d3a0、0x602f30a call 0x6067242。
- usb_stack：S14 门 0x6001cb0=「return b[0x1265C0+628]」；0x6012316 r0=0x600741c、
  name 0x60a1d38='usb_stack'（CI §2.4 的 0x601231c 站点复核）。
- btstack：表1[13] {filter=1, 0x100004, 0x60160fc}（0x60a8a00 区 dump）；函数体
  0x60160fc..0x6016370 内 0x60162d0 r2=name 0x60a1bc8='btstack'、0x60162d4 r0=0x605934e、
  0x60162de call 0x6067242。
- 0x100004 生产站：0x60166f6 r1=0x100004、0x6016702 r0=0x60a1d11('app_core')、
  0x601670c call 0x6064978（全 app 线性 li-扫描唯一命中）。
- W4[4] VLVD：0x6034c80→{0x6068662（0xf2c244 RMW 站 0x6068662 域）/0x60688e6→
  0x6068844}；IRQ91：0x6072b24..0x6072b50（request_irq，表 0x124C40[0]）。
- 分发表 60 条：0x60a89e0..0x60a8c68 / 0x60a8c68..0x60a8cb0（12B {filter,id,handler}）。
