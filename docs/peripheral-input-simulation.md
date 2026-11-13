# 外设输入模拟：现状体系 + 按键物理链路 + 下沉清单 + 音频流可行性（路 Q / sim-gpioin，只读预研）

2026-11-13。本页回答用户问题「目前的仿真只是让模拟器跑着吗？外设的输入怎么模拟呢？」的
输入侧：把现有四种输入模拟机制体系化，把**按键从物理层到 handler 的完整链路**逐指令钉死
（本路核心增量，含一个方向性修正：H3S 按键不走 GPIO 边沿中断，走 ADC 电阻分压 + 软件扫描），
给出「把按键模拟下沉到硬件输入事件」的补齐清单，并评估「喂 PCM 数据过效果链」的可行性。
**只读**：未改 tools/ 任何代码、未跑改报告/改机器状态的 probe、不连真机不刷机。反汇编用
official app.bin（sha256 `18f3d573…`，`disassemble_app.py` 同款线性反汇编，vendor objdump
产物放本地忽略目录）；call 目标按 end-relative 程序化解析（AGENTS 不变量 5）；flash 表字节
直接从公开 app.bin 解析；RAM/.data 内容按 probe_boot_handoff 钉死的 blob 区间推导。
结论基于 main `ae8102a`。

证据强度：**[静态]** = 指令锚定反汇编/表解码（本路）；**[文档]** = 既有 docs/HANDOFF 钉死条目；
**[SDK]** = 参考 SDK 交叉佐证（非 QCY 精确源）；**[推断]** = 上述链推出；**[疑]** = 未闭合。

---

## 0. 摘要（三句话）

1. **现状**：仿真里外设输入已经有一套四层机制（读回投影 / 具名 fixture / 事件注入 /
   中断交付），覆盖 UART/ADC/Timer/按键 handler/tone 场景共六类实例——输入不是没做，
   是「按场景 opt-in、逐边界放行」地在做。
2. **按键物理链路（本路钉死）**：H3S 按键是 **ADC 电阻分压矩阵**（不是 GPIO 边沿中断）——
   硬件源头就是路 BY 已经建好的 `fd31_adc_res_named_values` fixture 所服务的 JL_ADC；全链 =
   ADC 队列项 value → 周期 sys_timer 扫描回调（10ms）→ 分档查询（20 级阈值表）→ 软件去抖
   计数 → 手势状态机（单击/双击/多击/长按 1/3/5/8/10s 档）→ post 事件 0x10000C → 分发表 →
   按键 handler 0x60252c6。链路每一环都已反汇编钉死，**从 RES 采样值到 handler 的分发链是齐的**。
3. **下沉路径**：把按键模拟从「handler 入口注入」（路 BW 先例）下沉到「ADC 采样值注入」，
   复用 BY 的 RES fixture 机制加三件东西（按键通道的 RES 档位、config 523 分档器 fixture、
   IRQ39 ADC 完成中断交付），中间的扫描/去抖/手势/事件/handler **全部由固件真实代码跑**；
   音频数据流（喂 PCM 过 eq/wdrc）卡在 copex DSP 指令语义（无数值模型），但「节点状态对比」
   类断言不需要数据流，维持 D2 现状即可。

---

## 1. 现状体系：外设输入模拟的四种机制

现有 harness（probe_pi32v2 / probe_irq_entry / probe_boot 族）里，「外设输入」按注入深度分四层。
每层都遵守同一纪律：**opt-in 显式开关、值带推导链、derived-not-measured 标注、legacy 报告
逐字节不变**。

| 机制 | 一句话定义 | 适用外设 | 已覆盖实例（路） | 证据等级约定 |
|---|---|---|---|---|
| **读回投影** | 寄存器读返回「固件自己写过的状态」（最后写值/合成初始字/位粒度已知位），不发明硬件没写的值 | 有状态寄存器：CON/状态位 | JL_UART0 CON bit0 空闲位（路 F `jl_uart_con_projection`，合成 0=空闲）；JL_ADC CON 读回=最后写值+PND bit7 完成投影（路 BN/BY）；lp_timer CON / LRC trim 完成（路 AL/AE）；GPCNT/DCCK 位粒度读回（路 CG/CL） | derived-not-measured：读回值来自固件自己的写序列或显式完成假设，非硅测量 |
| **具名 fixture** | 对「模拟量/硅测量值/外部数据」这类无可推导来源的输入，提供显式命名的常量输入，值必须带物理/结构推导链 | 模拟测量值、外部数据、flash 缺失数据 | **ADC RES 400/500/400**（路 BY `fd31_adc_res_named_values`，WBG04/VBAT_4/VTEMP，通道识别=P3_ANA_CON4[4:1]）；lrct_trim_result=38400（路 AG，整除自洽）；bank B 擦除态四窗（路 S/U 等）；SDTAP CON=0（路 AW） | derived-not-measured + 显式 provenance；禁区（如 RES 红线）曾要求「唯一继续途径=真机测量或带推导链 fixture」 |
| **事件注入** | 从固件自己的 API/队列边界注入合成事件对象，让分发链真实跑 | 应用层输入：按键/tone/命令 | **EQ 双击 handler 注入**（路 BW `--event-injection` 五场景：合成事件字 `0x10000|(10<<8)|4` 从 handler 0x60252c6 入口注入，三镜像控制面对比）；**tone 场景**（路 CA/CF：开机音事件/直调 0x6017cc0 → stream.bin 加载→框架 post/pend）；cmd-11 参数消息（路 CM/CP 直调 0x6075e22） | synthetic-not-measured：事件对象是合成的，但注入点之后的执行是真实固件代码 |
| **中断交付** | 硬件到期/完成事件按真实资格链（ICFG gates、nibble 使能、优先级 vs IPMASK）抛 IrqDeliveryRequest，进入真实向量槽 handler | 周期/完成中断 | **Timer0 IRQ4**（路 BE：向量槽 0x19fe10、W1C ack、RTI 回点，14 次真实往返 in BY 世界）；**core tick IRQ3**（路 BF/BS：临界区挂起+每步重检+交付）；软件 IRQ127/IPI（probe_irq_entry 七级阶梯） | counterfactual（hardware_mode_certified=false：入口银行/PSR 高位等未认证），但 handler 字节、W1C、RTI 尾锚全部真实执行 |

四层的共同上游是「机器状态投影」基础设施（BR28Model 的 opt-in 寄存器族 + DualCoreMachine
的交付机制）。一个输入从浅到深可以逐层下沉：BW 的按键 handler 注入（第 3 层）下沉到本页
§3 的 ADC 采样值注入（第 2 层+第 4 层组合），就是这套体系的典型用法。

---

## 2. 按键物理链路钉死（本路核心增量）

### 2.1 方向性修正：按键不走 GPIO，走 ADC 电阻分压 [静态]

任务原设「把按键模拟下沉到 GPIO 电平事件」。反汇编结论：**H3S 按键不接 GPIO 边沿中断**。
证据链（全部 [静态]，指令锚定）：

1. **读键电平的函数 0x6000E94 是 ADC 分档查询**：读全局 `[0x125720]`（分档器对象指针）→
   `b[对象+0]`（使能/键数）非零 → `call 0x6000E74`（ADC 队列项按通道字取 value 低 16 位，
   队列 @0x127D08 起 11 项×16B，与路 BU 钉的 ADC 通道队列同一结构）→ 对象 +10 起的
   **20 级 u16 阈值表**线性定档（`>1022` 为哨兵=10 位 ADC 满量程界）→ 返回对象 +50 起的
   **20 项档位/键 id 字节表**对应项；查不到返回 255（无键）。
2. **全 app 没有 GPIO 边沿中断安装点服务按键**：request_irq（0x6065d86）全 27 个调用点
   逐个核过 irq 号（0/1/2/3/29/28/39/41/67/68/76/78/79/82/124/125/127-cnum/32），没有一个是
   PORT 中断；GPIO 驱动族（0x6084E40-0x6085210：mask/方向/输出/DIE/上下拉 [base+0..+28]）
   只有配置函数，没有 pending/边沿状态寄存器访问。
3. **iokey 安装函数 0x6001866 把键通道注册进 ADC 队列**：`call 0x6001230`（adc_add_sample_ch，
   路 BU 同一函数）注册键通道字，随后配置两个 IO 的通道复用（见 §2.6）。
4. docs/gpio-irq.md 记录的「启动早期 PORTB bit1 被配置为输入+DIE/PU/PD/DIEH」是 P11 侧
   io 初始化（P11_PORT_DIGITAL=PB_DIE/PB_DIEH，br28_model 注释）的默认动作，不是按键
   专属配置 [文档+静态]；H3S 按键的具体分压引脚配置在产线配置里（§2.5），公开工件不含。

### 2.2 完整链路（七层，全部 [静态] 钉死）

```
[硬件] JL_ADC（CON 0xfd3100 / RES 0xfd3104）按通道采样，10 位原始值
   │  ←—— 路 BY 的 RES fixture / PND 完成投影就服务这里
   ▼
[ADC 队列] 0x127D08 起 11 项×16B {next_due, key=通道字, value, mode, period}
   │  生产者①：IRQ39（JL_ADC 转换完成中断，handler 0x60348e8，
   │            EMA 平滑后写项 value——app_core 0x601219c 安装，28B 中断帧）
   │  生产者②：周期回调 adc_scan 0x60349d2（0x60348a2 找到期项→setter 启动下一转换，
   │            app_core 0x60121a4 注册）——采样循环自我维持
   ▼
[分档查询] read_key 0x6000E94：adc_queue_get(键通道字) → 20 级阈值表 → 键 id（255=无键）
   ▼
[扫描回调] 0x6001944（sys_timer 周期 10ms，app_core init walker 0x60121ce-0x6012212
   │        经注册表 0x60C0E20 {enable=1, ctx=0x124888, read_fn=0x6000E94,
   │        init=0x6001866} 安装；回调 ctx 即 .data 静态描述符）
   │  软件去抖：电平稳定时按下计数 b[ctx+4]++、长按计时 h[ctx+6]++；
   │  释放计数/变化计数 b[ctx+2..3]；阈值在 ctx（按下 0x4B/释放 0x5A 拍，[推断] 时间单位）
   ▼
[手势状态机] 0x60019xx-0x6001b64：
   │  单击/双击/多击（点击计数 n → 手势码 n+2，≥8 归 27=HOLD）；
   │  长按档位（时长差×10=ms：1000/3000/5000/8000/10000ms → 手势码 11..14）
   │  按下=1、释放=3；先过回调链表 @0x60C0E30（{+4 arg,+8 cb} 逐项调用，
   │  返回非 0 截断），再 post 事件
   ▼
[事件 post] 0x6001b5e → 0x6000cba：事件 id=0x10000C、队列名 0x60A1D11（app_core）、
   │        载荷 {0x10000|(key<<8)|gesture, 0}——key 字段=键 id（BW 实测 10..13）、
   │        gesture=手势码（双击=4）
   ▼
[分发] S19 消息循环 get-event → 特路 0x60108fe → 分发表 walk（先表 2 后表 1）
   │     → 表 2 记录[5] {0xff, 0x10000c, handler 0x60252c6}（路 BZ/BT 已钉）
   ▼
[按键 handler] 0x60252c6（BW 先例的注入点）：
         bit16 处理门 → gesture tbb 分类 → 模式查询 0x6008170（==5 保持纯类别，
         否则 |0x80）→ key 字段 10..13 经位掩码 0x09050307 映射逻辑键 {7,3,5,9}
         （key10→7）→ 映射表 15 项 (key,gesture,action) @QCY+3404 → action 分发
         （tbb 11 路：含 ANC 键链 0x6018b60 族、EQ 钩子站点 0x6025364）
```

从 RES 采样值到 handler 的分发链**是齐的**：每一环都有指令级锚点（§6），没有「未知函数」
环节。

### 2.3 各环节关键证据（逐层展开）

**ADC 队列与 IRQ39** [静态+文档]：队列结构/注册（WBG04/VBAT_4/VTEMP 三通道启动注册）路 BU
已钉（docs/adc-res-consumer.md §1-2）。本路补钉：IRQ39 handler 0x60348e8 读 CON bit7（PND）
后按项 mode 做 EMA（mode==1 float 域 0.2/0.8 加权；key==0x10000 整数域）写回项 value——与
BU §3.6 一致。**docs/d2-audio-first-scene.md §1.4 曾把 IRQ39/0x60348e8 归「音频 IRQ 候选」
[推断]，本路按 handler 体反汇编修正：它是 JL_ADC 完成中断，不属音频域**（BX 自己也标了
[推断]，此处升级为 [静态] 并改判）。

**扫描回调与注册表** [静态]：app_core init walker 段 0x60121ce-0x6012212 遍历注册表
0x60C0E20（16B：`01 00 00 00 | 88 48 12 00 | 94 0e 00 06 | 66 18 00 06` = enable=1 /
ctx=0x124888 / read_fn=0x6000E94 / init=0x6001866），先 `call [表+12]`（iokey init）再以
ctx[+16]=10 为周期注册扫描回调 0x6001944（sys_timer_add 0x6066ba6 族）。ctx @0x124888 是
.data blob 内容（flash 0x60C3380，48B 见 §6）：`FF 00 00 00 00 00 00 00 01 02 4B 5A 14 00
00 00 0A 00 00 00 …`——初始电平 0xFF（无键）、键数 1、组合键值 2、按下/释放阈值 75/90、
长按常数 20、扫描周期 10。

**去抖与手势** [静态]：扫描回调每拍比较「上次电平 b[ctx+0]」与 read_fn 返回值：稳定则按下
计数 +1、达到 b[ctx+10] 触发按下事件（post 手势 1）；电平变化走变化/释放计数（b[ctx+2..3]，
阈值 b[ctx+11]）。手势状态机（0x6001a00-0x6001b64）以时间戳差（`[0x124B30+36]` 系统计数，
差值×10=ms [推断：单位换算由指令形状推出，未硅验证]）分长按档；多击以点击计数+2 编码；
HOLD=27。事件在「确认手势」时 post（0x6001b5e），post 前先走回调链表 0x60C0E30
（当前 .data 内容为 0，即空表——**扩展按键行为的现成挂点**）。

**handler 消费** [静态+文档]：0x60252c6 的解码细节（bit16 门、gesture tbb、0x09050307 逻辑
键映射、15 项 action 表 @QCY+3404）路 BW 已钉并在五场景里真实执行过；本路补上游：**事件
载荷的 key 字段就是分档器的键 id 字节**（0x6001b1a `r8 = b[r10+0]` 取当前键 id →
0x6001b54 `r1 = r8 << 8` 装进事件字），BW 的「key 字段 10..13」即真实键 id 表取值域。
BW 合成事件字 `0x10000|(10<<8)|4` 与本路生产者编码逐字段吻合（互证）。

### 2.4 按键配置：config 523 分档器（擦除态世界缺失）[静态+文档+疑]

分档器对象（[0x125720] 指向，实体在 0x1274F4 缓存槽）由 **get_config(key 523)** 解析填充
（解析器 0x60010b4：读 112 字节配置→组装 {enable/键数, 通道字, 阈值表×20, 档位表×20}）。
key 523 落在 512..700 区间 → 记录区只读后端（路 CR 的后端注册表）→ **真实内容在 bank B
window 记录区（公开工件外）**。推论：

- **擦除态/仿真世界**（erased_bankb fixture）：config 523 缺失 → 分档器 enable=0 →
  iokey init `if (b[r4+0]==0) goto 90` 不安装 → read_key 恒返 255 → **按键链整体旁路**。
  这与「全新芯片无产线配置」自洽，也解释了为什么现有前缀里按键链从未显形。
- **真实耳机**：产线写入 config 523（键通道字+阈值表+键 id 表）。
- **与路 BU 的关系修正**：BU §3.4/3.5 把 0x6000E94 和 0x6012458 门都标成「NTC 温度档」。
  本路证明 0x6000E94 的身份是**键扫描 read_fn**（sys_timer 注册表直证）+分档器查询函数；
  0x6012458 的启动等待门（分档器 enable 非 0 时要求读数>9，150 次重试失败关机）读的是同一
  对象——它的「NTC」标签应降级为「分档器上电稳定门」（可能是 VTEMP 复用同一分档器机制，
  也可能就是按键通道的稳定性检查；通道字内容不在公开工件，[疑] 如实标注）。
  **两个消费者共享一个 config 523 分档器对象是两边反汇编的共同事实**。

### 2.5 「GPIO 引脚」那部分：IO 通道复用配置 [静态]

iokey init（0x6001882-0x60018ca）确实配置了两个物理 IO：每键记录 `{b[+1]=port<<4|pin,
b[+2]=极性}` → `gpio_mask(1<<pin)` → 通道 16/17 二选一（b[+2]==0→17，否则 16）调
0x6085210（上下拉 [base+24]/[base+28]）；第二个 IO 用通道 32。结合 BR28 SDK 的 IO 通道
复用模型（IO_CH：物理 IO 可映射到编号通道供外设采样），这两个 IO 是**按键分压网络的
输入脚/使能脚**，按键电平变化经分压后进入 ADC 通道，不产生 GPIO 中断。具体 port/pin
在 config 523 键表里（公开工件外，[疑]）。启动早期 PB1 的配置（gpio-irq.md）与按键的
关系未证实，不作声明。

### 2.6 对「输入模拟」的直接含义

现有 BW 场景（handler 入口注入）验证的是「从 handler 开始的链」。本路钉死后，可以下沉
**两级**：

1. **事件级**：在 post 边界（0x6000cba 调用点或队列 0x60A1D11）注入合成载荷
   `{0x10000|(key<<8)|gesture, 0}`——让 S19 分发+特路+表 walk+handler 全链真实跑
   （BW 的载荷形状已与本路生产者编码互证，注入值有生产者侧依据）。
2. **采样值级**：给分档器通道的 ADC 队列项 value 注入「按键按下时的分压读数」——让
   read_key/去抖/手势/post/handler **全部固件代码真实执行**，唯一合成输入是 RES 值。
   详见 §3。

---

## 3. 下沉清单：从「handler 注入」到「ADC 采样值注入」要补什么

目标：在一个已到 app_core init walker 之后的世界（p11_heap_experimental_config 前缀，
BY 世界 447,383+）里，注入一次「按键按下→双击」的完整物理输入，断言链尾 handler 0x60252c6
收到正确 (key,gesture) 并走 action 分发。逐项清单（按依赖序）：

| # | 要补的东西 | 现状与依据 | 量级 |
|---|---|---|---|
| 1 | **config 523 分档器 fixture**：显式合成 {enable=1, 键通道字, 20 级阈值表, 键 id 表}，按 BY/BB 先例具名+版本管理 | 解析器 0x60010b4/安装器 0x6001866 已钉死布局；擦除态世界该 key 缺失是当前旁路根因。阈值表内容可自造（如两档：无键 0..500=键255、按下 501..1022=键10），键 id=10 对 BW 已验的 handler 映射 | 小：一个具名 fixture（值合成、标注 synthetic-not-measured） |
| 2 | **按键通道的 ADC 队列项**：让 adc_add_sample_ch(键通道字) 真实执行（iokey init 会自己做）+ 队列项 value 可控 | 队列结构/注册函数路 BU 已钉；BY 世界 ADC 队列已有三项（WBG04/VBAT_4/VTEMP）在跑（RES fixture 服务中），键通道项按同一机制 | 小：真实代码自动做（开 fixture 1 后 0x6001866 自己注册） |
| 3 | **RES fixture 扩按键通道**：`fd31_adc_res_named_values` 从三通道扩到「三通道+键通道按下档/释放档」，通道识别已用 P3_ANA_CON4[4:1] 复用域（BY 机制现成） | BY 的 RES fixture/PND 完成投影直接可扩；键通道字由 fixture 1 决定 | 小：fixture 表加行+一个「按下状态切换」开关（按步数或显式两段） |
| 4 | **IRQ39 交付机制**（可选但推荐）：ADC 完成中断的真实交付（向量槽 0x19fe00+39*4 由 0x601219c 的 request_irq 写入、handler 0x60348e8 28B 帧已钉），机制完全抄 Timer0 IRQ4（路 BE 三件套：entry/banks/rti + delivery_in_flight 防重） | BE/BF 的交付机制是现成模板；IRQ39 优先级 1（0x6012196 装 r1=1） | 中：一条新交付路径（模式化复制，非新机制） |
| 5 | **scan 周期的 sys_timer 真实到期**：扫描回调 0x6001944 由 sys_timer 驱动，需要 D1 事件循环的 tick/到期机制（BZ 已钉 tick 回调 0x603622C→sys_timer 轮询 0x6066de0；BI/BJ 在途） | **这是最大的前置**：BY 世界停点在 W1[0] 轮询（p3_ana_read_not_modeled），ADC 队列采样循环（adc_scan 周期回调）尚在未执行段；没有 tick 就没有扫描 | 大：依赖 D1（BI/BJ 路线梯），非本清单可独立完成 |
| 6 | （替代 5 的捷径）**直调扫描回调**：不动 sys_timer，在 harness 里按拍直调 0x6001944(注册表项指针)（CK/CM 直调先例），配合 fixture 3 的值切换模拟「按下 N 拍→释放」 | 去抖阈值 75/90 拍 [推断] 可先用小值试真值（如拍数未过门再调），或按 ctx fixture 调小阈值——ctx 在 .data，可用 BW 的 RAM fixture 机制改 | 小：harness 侧循环+断言 |

**推荐路径（ROI 最大）**：1+2+3+6（直调捷径）先做——四件都是 fixture/harness 级，不动
模型机制，就能拿到「RES 值→去抖→手势→post→分发→handler→action」全真实链的首个证据包；
4（IRQ39 交付）和 5（真 tick）归 D1 之后的正式化。**需要真实跑的中间函数**（下沉后不再
stub/注入的）：0x6000E74（队列取值）、0x6000E94（分档）、0x6001944 全族（去抖+手势+
post 前回调链表）、0x6000cba/0x606487C（post/入队）、S19 分发段、0x60252c6（BW 场景已
真实跑过，同一函数）。

**寄存器/状态清单**（与 §2.2 对照）：JL_ADC CON（0xfd3100，BY 投影现成）、RES（0xfd3104，
fixture 扩通道）、P3_ANA_CON4（0xf2c250，通道复用域，BN 联动 seed 现成）、ADC 队列
（0x127D08 RAM，真实代码维护）、分档器对象（0x1274F4/0x125720 RAM，fixture 1 填）、
ctx @0x124888（.data blob 自带，无需动）。**GPIO 端口寄存器一个都不用碰**——这是本路
修正的直接收益：下沉目标是 ADC，不是 GPIO。

---

## 4. 音频数据流模拟可行性（短节）

**问题**：D2 场景（CA/CF/CK/CM/CP）目前到「节点创建+参数消息消费+node+35 写」为止；
「喂 PCM 采样过效果链（eq/wdrc 滤波）」在现 harness 上行不行？

**结论：节点状态类断言不需要数据流（维持现状）；真数据流卡在两块前置，其中 copex 是硬的。**

1. **效果节点的参数装载/状态写已经真实跑通**（CP 场景：descriptor walk 建 20 节点、cmd-11
   命中真节点、node+35 三镜像差异走真实链）。「EQ 关态 ctx[35]=1 vs 文件值」这类断言
   （D2-B/EQ 验证的核心）在 open-config 路径上完成，**不依赖 PCM** [文档：路 CP]。
2. **系数计算已经停在 copex**：bypass=0 的重开路径在 open-config 写完 ctx[35] 后诚实停在
   **0x606a900 `copex(r3_r2)(22)`**（mode 22）[文档：路 CP+本路复核]。这个小函数
   （0x606a8fc-0x606a906：`r2=r0; r3=0; r3_r2=copex(r3_r2)(22); [r1]=r2`）是浮点标量
   变换 helper，属 eq/wdrc 系数计算族（CJ/CN 路钉过 exp_float/db2mag 用 fmin/fmax+copex）。
   **copex 是 DSP 标量浮点协处理器指令**：pi32v2-isa 已收录该指令并有 fm1 硬件捕获
   （mode 22 有 6 个采样 case），但 **isa notes 明言「No numerical or lookup-table model」**
   ——364+ case 采样后模式语义仍无数值模型（部分模式有方向性结论，mode 22 在「upper mask
   不变」采样集里，输出公式未解）。模拟器现状：未建模即诚实停 [文档+SDK-isa]。
   → **喂 PCM 过 eq/wdrc 的硬前置 = copex 至少一个模式的数值语义**（系数计算+滤波循环
   都要用）。可行升级途径：fm1 硬件记录已在库（reference-cache/pi32v2-isa/hw），若日后
   语义有突破（新分析或新捕获），harness 侧无障碍；在此之前数据流场景诚实标注不可达。
3. **数据泵侧**（假设 copex 解决后）：WTS/AAC 解码 plug（0x6031xxx DSP 库）、DAC DMA
   （JL_AUDIO 0xfe0500 族 + 0xfe0538 bit4 完成轮询 + IRQ39 之外的音频 DMA 中断）、
   jlstream 数据通路任务——BX §3 已列 ≈37+ 地址/5 族的 SFR 缺口（其中 0xfe0538 轮询和
   0xfe04xx 状态位是语义决策点，先例齐备：完成投影 lrct_trim_done 同款）。这些是「量」
   不是「质」，逐地址三选一即可 [文档：路 BX]。
4. **最小可行路径建议**：如果目标是「效果链数字域输出对比」（三镜像 EQ 行为差的最终
   显形），更便宜的路是**离线喂数给单个效果节点**——D2-C 场景已有真节点+ctx，直调节点
   op（0x607898e 族）的 process 入口、喂合成 PCM 块、dump 输出缓冲；但这同样先过 copex。
   若 copex 无解，退一步的替代断言：**系数内存对比**（open-config 后 dump 节点系数区，
   三镜像差只在钩子路径上显形）——不需要跑滤波，能证明「配置面行为差」，不能证明
   「滤波输出差」。

---

## 5. 不确定处（如实标注）

- **config 523 内容**（通道字/阈值表/键 id 表/键表 port-pin）不在公开工件（bank B 记录区），
  §3 fixture 1 的阈值表是自造值；真实键 id↔逻辑键映射（10..13→{7,3,5,9}）来自 handler 侧
  掩码，与分档器档位表的关系未运行时互证（下沉场景落地时可互证）。
- **时间单位**：ctx 阈值 75/90 拍、扫描周期 10、手势时长差×10=ms——拍/tick 的精确换算
  （10ms vs 100ms）未硅验证 [推断]；直调捷径（§3 #6）落地时按行为校准。
- **0x6012458 门的 NTC 归属**（§2.4）：分档器共享语境下的「温度 vs 按键稳定性」未定论，
  不影响按键链结论。
- **IRQ39 归属修正**（§2.3）只否定「音频候选」标签；0x60348e8 handler 内 0x6034900 处的
  call -144208（错误路径）未展开。
- copex mode 22 的输出公式未解（isa notes 全文为准）；「系数计算全用 mode 22」未逐 call
  核实（只核了 0x606a900 一处+CN 路的 exp_float 族记载）。
- 反汇编为线性扫描（data 区内偶有伪指令），本文所有锚点均以函数边界/调用目标交叉验证过；
  个别未展开的分支（如 0x60018b6-0x60018ca 的第三段配置 call 416516）只用于布局推断。

## 6. 证据锚点

- 线性反汇编：official app.bin sha256 `18f3d573…`（disassemble_app.py 产物，本地忽略目录）；
  call 目标 end-relative 程序化解析（全 306k 行，call 20060 处）。关键新锚：
  **0x6000E94**（read_key/分档查询）、**0x6000E74**（ADC 队列按通道字取 value，队列基
  0x127D10）、**0x6001944**（扫描回调/去抖状态机）、**0x6001A00-0x6001B64**（手势状态机+
  post）、**0x6001B5E**（post 0x10000|(key<<8)|gesture）、**0x60C0E20**（sys_timer 注册表
  16B）、**0x124888**（ctx，.data blob @flash 0x60C3380）、**0x6001866**（iokey init，注册
  ADC 通道+IO 通道复用）、**0x60010B4**（config 523 解析器，缓存槽 0x1274F4）、
  **0x60348E8**（IRQ39 handler，读 0xfd3100 bit7+EMA）、**0x6084E40-0x6085210**（GPIO
  驱动族：mask/基址表 @0x60852E0/方向+8/输出+12/DIE+16/PU+24/PD+28）、**0x60252C6**（BW
  已钉，本路补生产者侧编码）。
- flash 表字节（直接解析公开 app.bin）：0x60C0E20（16B）、0x60C32A4..0x60C32B8（init
  函数表 5 指针）、0x60C0E30（事件前回调链表头，.data 当前空表：起点==终点）、.data blob @0x60C3338 起。
- request_irq 全调用点扫描：27 处，irq 号逐点提取（r0/r1/r2 装载窗口）。
- 历史钉子：docs/adc-res-consumer.md（BU：队列结构/IRQ39/EMA/0x6012458 门）、
  docs/eq-payload-semantics.md（BT：handler 域/0x09050307）、HANDOFF 路 BW（事件注入五
  场景+载荷形状）、路 BY（RES fixture/PND 投影）、路 BZ（S19 分发/Timer0 回调表）、
  docs/d2-audio-first-scene.md（BX：音频 SFR 缺口）、docs/d2b-framework-task.md（CI：init
  walker）、pi32v2-isa isa/pi32v2.toml copex 条目+hw/fm1-2026-10-24* 硬件捕获。
- 一次性脚本（call 图解析/irq 站点提取/flash 表 dump）放本地 tmp 不进库；私有 ROM 未用
  （0x60348e8 等全在 app flash 区）；结论基于 main `ae8102a`，只读未改任何 tools/ 代码。
