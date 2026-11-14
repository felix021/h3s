# USB Audio 域建模地图：设备栈全景 + 枚举边界 + 三选一预演（sim-usbaudio，路 CT，只读预研）

2026-11-14。基线 worktree `336e848`（路 AT 合并态）。本页服务路 AT 冻结的 **D2 有线模式
扩展用例（预期 BLOCKED：USB 设备控制器/主机枚举未建模）**——把这块 BLOCKED 画成精确的
解锁地图：设备栈全景、寄存器域逐站点钉死、「主机枚举未建模」的确切边界清单、三选一建模
方案预演、以及主前沿何时撞上 USB 域。照 [docs/wlaud-init-map.md](wlaud-init-map.md)（路 AG）
的模板；有线模式固件证据底座=[docs/wired-game-mode.md](wired-game-mode.md)（路 AT）。

**只读静态**：未改 tools/ 任何代码、未跑 probe、未连真机。证据源=官方 app.bin（sha
`18f3d573…`，加载基址 0x06000100）的 vendor 线性反汇编（路 AG 校准的同族工件；**call 目标
公式=指令尾+操作数字节位移**，本路四次独立锚点复验：0x6006f3c→0x600693a、0x6007424→
0x6006fce、0x6007030→0x60669a6、0x6007412→0x6064978，全部落在函数序言上）+ 既有 executor
报告（[模型] 事实）+ br28.h（sha256 `111e29ea…`）+ 参考 SDK 701n（commit `67328f4…`，
not-exact-QCY-source，[SDK] 仅作命名/结构参照）。一次性扫描/反汇编脚本在仓外 scratch
（路 AG 先例，不入库）。全部静态结论 **derived-not-measured**。

证据强度：**[静态]**=本路反汇编钉死；**[SDK]**=参考 SDK 源钉结构；**[模型]**=既有 executor
报告/模型源钉死；**[文档]**=既有 docs/HANDOFF 钉死；**[推断]**=上述链推出；**[未定位]**=如实标注。

> 路号说明：任务文写「路 AV」，但本基线 HANDOFF 中 AV 已被 sim-p33window 占用（字母已用到
> CS），按字母序顺延记 **CT**，分支/worktree 名 sim-usbaudio 不变。

## 0. 一句话结论

USB 设备面分三层：**配置/引脚层**（JL_USB CON0/CON1 整字写 + JL_USB_IO/IOMAP RMW）已被
路 AW 的 `port_initialization_projection` 放行（写记录+种子 0，[模型]）；**状态/传输层**
（CON0 读回线态、SIE 0xfd4a00 族、EP CSR/FIFO、IRQ28/29）零建模——这层全部活在那三段代码
里：usb_stack 任务体 0x6035840、OTG 消费者 0x600741c、SIE ISR 0x600693a；而这三段**全部在
调度器任务分发墙之后**——所以 **D1 剩余阶梯不会被 USB 阻塞**，主前沿撞上 USB 域的时间点=
D1 验收（BJ）之后的 D2 事件域，与路 AT 冻结裁定一致。解锁推荐**方案 (b) 钉住**（状态字节
fixture+读回投影跳过枚举），最小主机仿真器 (a) 留 D3/专项。

## 1. USB 设备栈全景（四问之一）

### 1.1 任务与事件面 [静态]

- **usb_stack 任务**：任务体 **0x6035840**（`li r1=0x6035840` @0x6007028；os_task_create 调用
  @0x6007030→0x60669a6）；栈深=b[usb 对象+1870]，句柄存 [usb 对象+752]（@0x6007036）。
  usb 对象基址 **0x1265C0**（`li r5=1205696` 多处互证，0x6006fd0/0x60070ac/0x600693c 同值）。
- **usb_init 0x6006fce**（全 app 唯一调用方=0x6007424 [静态]）：
  1. 复制 **12B 配置字**（`rep` 3 迭代 ld/st 对 @0x6006fdc/de）**0x1248A4 → 0x126D08..
     0x126D13**（对象+1864..1875）；
  2. 清零对象+3008 起任务队列 36B（`rep` 9 迭代 @0x6006fe8，=0x127180）；
  3. **门=b[0x126D08] bit0**（@0x6006fec..0x6006ff4）：清零 → b[0x127190]=**7**（模式字节=
     禁用，@0x6006ff8..0x6007002）且不建任务；置位 → 0x60070aa（usb 设备对象分配：[对象+184]/
     [+188] 两槽 malloc + 半字初始化族，纯 RAM）→ 0x6064162（队列创建，纯 RAM）→ 复读门
     （@0x6007016..0x600701e，再为 0 则跳过）→ task_create。
- **任务体 0x6035840 主循环** [静态]：入口查 b[对象+1864]==1 与 b[对象+3041] 的初始化门 →
  队列 pend → **读 [0xfd1800] CON0 bit0**（@0x6035878..0x6035880，设备使能位）→ 读
  b[对象+1869]（类配置字节）→ **模式字节 b[0x127190] & 0xF 的 tbh 分派 0..6**（@0x6035894..
  0x60358b2；>6 出循环；7=禁用不进循环）→ 各 case 带**有界重试**（`ifs (r2<=9)` 十次环
  @0x6035a12、计数器 b[对象+3028/3029/3030/3040]）与 **50 单位退避定时器**（@0x6035a02/
  0x6035a30，call 0x6066xxx 超时族）；环内还查 **CON0 bit13**（@0x6035a3a，`r0&8192`）；
  40 次重试耗尽后**直接改写模式字节** `(b[0x127190]&0xF0)|6`（@0x6035a72..0x6035a80）。
- **线态读取 helper 组**（模式机轮询消费 [静态]）：
  - 0x60357b4：`return CON0 bit16`；0x60357c2：`return CON0 bit17`（DP/DM 线态位族
    [推断]，位号钉死）；
  - 0x6035804：`return (CON0 & 0x820)==0`（线态空闲/SE0 判定；路 AT 的 &0x820 复证）；
  - 0x60357d0：**CON0=0x1005 整字写** + 条件（b[对象+50]&0x10）**USB_IO 0xfd6000 接管**
    （|=8、&=0xFFFFFD5F、|=0x5000，@0x60357e8..0x6035800）——「调试串口让位→脚位切回
    USB」的任务侧实体；
  - SIE POWER（0xfd4a04）写基址 @0x60358a8（case 体内按模式改写：soft-connect/reset/resume
    位族 [SDK MUSB 惯例推断]，具体位组合 [未定位]）。
- **OTG 消费者 0x600741c** [静态]：入口 `li r0=0x1248A4` → call 0x6006fce（usb_init，见上）→
  消息泵（pend @0x600744a，期望返回 13+魔数 0x100000）→ tbb 分派消息类型 0..3 →
  'OTG'(0x4F544700)+'s'(slave)+IN(1) case：usb 释放链（call 0x60073de：CON0&=~0x800）+
  **CON0 &= ~1、CON1=0**（@0x60074a0..0x60074a4）+ USB_IO RMW（|=0xC、&=~0xF0 掩码族
  @0x60074b2..0x60074c4）+ b[对象+760]=0。注册点=**0x6012316 `li r0=0x600741c`**（名字键控
  config/事件注册族 walker 0x6012028.. 内，call @0x601231c 注册；该 walker 同时维护 staging
  0x1249C0 族并在 0x6012324 置 b[0x1249F6]=1）。**0x600741c 与 0x6035840 是两个实体**：前者
  挂在 OTG 事件源上做 init/释放，后者是 os 任务——SDK usb_task.c 的 usb_task()/usb_stack_init()
  同构拆分 [SDK]。
- **插入事件**：129（上电初始化）/130/131（插拔）由电源/充电初始化尾段投递
  （0x6017f62/0x601805a 族，[文档] 路 AT；本路未重验）。

### 1.2 驱动分层与中断 [静态]

- **usb_slave_init 链**（设备上线后由任务模式机触达）：0x6007300..0x600733e——[对象+764]=
  设备对象、b[对象+636]=2 → call usb_g_sie_init（0x600731e→0x600714a）→ SIE 初始化块
  （0x6007324..0x6007342）：**[0xfd4a2c INTRUSBE]=0**、两笔 call（FADDR/DEVCTL 置 -1 族）、
  **[0xfd4a04 POWER]=0x40**（@0x600733a；soft-connect enable，[SDK] MUSB POWER bit6 同构）→
  **CON0 |= 0x800**（@0x6007342，设备模式位）。
- **usb_g_sie_init 0x600714a** [静态]：USB_IO |= 0x200、|= 0x100、&= ~0x1000、|= 0x2000
  （@0x6007150..0x600715c）→ CON0 &= 0xFFFFBFBB、**CON1=0**、CON0 |= 1（@0x6007164..
  0x6007172）。
- **DP/DM 引脚复用**：驱动侧接管=0x60065c8（USB_IO |= 0x5000、|=4、&=~0x100、&=~0x10、
  &=~0x40、|=8，@0x60065c8..0x60065e2；函数 0x60065c6，caller 0x600639e 驱动 init 族）；
  UART 侧让位=FI_UART0_RX 写 0x2f=USBDP_IN（0x6001342→0xfd5c90，[文档] 路 AT）；RAM 库
  0x60c67f6 族是第三处（CON0&=~1 + [0xff5c08] FI 系列回写，串口/USB 切换收尾族 [推断]）。
- **中断注册（枚举的入口）** [静态]：0x60073a2..0x60073c0 连续两笔
  `request_irq(29, prio3, 0x6006f38)` 与 `request_irq(28, prio3, 0x60067fc)`（IRQ 号/优先级/
  处理器三实参+handler 指针，call 0x6065c26 族——IRQ 交付机制域）。0x6006f38=三push+call
  0x600693a+rti 的蹦床（表字 @0x60073a4）；**0x60067fc=第二个 USB 中断蹦床（SOF/OTG 检测
  族 [推断]，28/29 与 SOF/CTRL 的对应 [未定位]）**。
- **SIE ISR 0x600693a**（IRQ29 蹦床 callee）[静态]：整块读 SIE 中断区
  （[0xfd4a08 INTRTX1]/[0xfd4a0c INTRTX2]/[0xfd4a10 INTRRX1]/[0xfd4a14 INTRRX2]/
  [0xfd4a18 INTRUSB] 与使能区 [0xfd4a1c..0xfd4a30] 的与/或掩码族，@0x6006944..0x600697c）→
  - **INTRUSB bit2=总线复位**（@0x6006980）：状态字节=2、**FADDR [0xfd4a00]=0**
    （@0x600699a）、回调环（设备对象 handler 槽 15..20，@0x6006a2..0x6006b0）；
  - **TX bit0=EP0/SETUP**（@0x6006da）：EP0 状态机 0x6006a32..——阶段字节 b[dev+1]+tbb 分派
    （SETUP/DATA/STATUS 三段 [SDK] MUSB CSR0 同构 [推断]），标准请求走描述符发送 helper
    （call @0x6006abe→0x600491a 族），类请求走设备对象槽回调；
  - EP1..4 TX/RX 位→handler 表（对象+2352/+2332 槽）逐端点分派（@0x6006f0..0x6006a28）。
- **充电域 OTG 检测** [静态]：0x601b15c（USB_IO bit[3:2] RMW insert，@0x601b15c..0x601b16e，
  caller 0x601b070）与 0x601b318（**读 CON0 & 0x200000**，@0x601b318..0x601b324，caller
  0x601b196）——充电状态机轮询 DP 的检测面（插 charger vs PC 的判别 [推断]）。
- **低功耗面** [静态]：快照对 0x60c846c（CON0→[对象+664]、USB_IO→[对象+668]）与 0x60c8498
  （恢复回写）；0x6069cd2（USB_IO=r0 恢复族）；**port-init 里的 0x6068a7c..0x6068a84（CON0/
  CON1=r7 写，即路 AW 钉的 USB-disable 0x6068a82/a4，r7 在 init 上下文=0）已属已放行域**
  （[模型]，见 §4）。

### 1.3 JL_USB 寄存器域全图（51 站点逐个钉）[静态+SDK]

vendor 反汇编全量 li 扫描（含 0xfd1800/0xfd4a00/0xfd4b10..0xfd4e40/0xfd6000 五族），共
**51 处基址/寄存器字面量**；另有一批经**基址+索引算术**到达（表尾注明）。SDK 命名=br28.h
`JL_USB_TypeDef`/`JL_USB_SIE_TypeDef`/`JL_USB_EP0/EP_TypeDef`/`JL_USB_IO_TypeDef`（全部
字段 __RW 除注明）。

**A. JL_USB 0xfd1800（端点 FIFO/DMA 面）——27 站点：**

| 寄存器 | 偏移 | 站点 | 形状（消费点钉死） |
|---|---|---|---|
| CON0 | +0 | 0x600471a,0x60047d8,0x600661a,0x60067f0,0x60068b6,0x60068ec,0x6006f02,0x6006f52,0x6007164,0x600733c,0x60073e0,0x6007438,0x601b318,0x60357b4,0x60357c2,0x60357d0,0x6035804,0x6035878,0x6068a7c,0x60c67f6,0x60c8448,0x60c846c,0x60c8498（23 处，路 AT 的 23 基址引用数复证） | 写：\|=1<<(ep+23)（EP delay 使能位族 @0x6004720）、&=~(1<<(n+19))（@0x60047de）、\|=r0（@0x6006620）、\|=0x1000（@0x60067f6）、读&2（@0x60068f4，TX delay 就绪门）、\|=0x80000（@0x6006f08，模式 case）、整字 0x1005（@0x60357da）、\|=0x800（@0x6007342 设备模式）、&=~0x800（@0x60073e6 释放）、&=~1/整字 r7（@0x60074a0/0x6068a82/0x60c67fc/0x60c849e）、&=0xFFFFBFBB+\|=1（@0x600716a/72，sie init）；读：bit16（0x60357b4）、bit17（0x60357c2）、(x&0x820)==0（0x6035804）、bit0（0x603587e）、bit13（0x6035a3a）、&0x200000（0x601b31e）、快照读（0x60c8472） |
| CON1 | +4 | 0x6007164,0x6068a7c,0x60074a0,0x60c8448 | 整字写 0（sie init @0x600716e、port-init @0x6068a84、释放 @0x60074a4、off 族 @0x60c844c）——纯配置面 |
| EP0_CNT | +8（索引 [0xfd1808+ep*4]=EP0..4_CNT） | 0x6004b16 | `[r4+r1<<2]=r2`（@0x6004b1c）：按端点写传输计数 |
| EP0_ADR | +0x1c | 0x60045d0,0x600466e | `[r1+0]=r2`（@0x60045d6/0x6004674）：EP0 DMA 缓冲地址设置 |
| EP1_TADR | +0x20（索引 [+ep*4]=EP1..4_TADR） | 0x6004bbe | `[r2+r1<<2]=r8`（@0x6004bc4）：TX DMA 地址环 |
| TXDLY_CON | +0x40（索引 [+ep*4]） | 0x600470e | `[r0+r4<<2]=r6`（@0x6004714）+ 写 2/0x900041（@0x60068f8/0x6006906，CON0 bit1 门内）：ISO 传输延迟/长度族 |
| （EP1..4_RADR/RLEN/MTX/MRX 族） | +0x28..0x60 | 无字面量站点 | 全部经 EP1_TADR 基址+偏移或运行时算术到达 [未逐个钉] |

**B. JL_USB_SIE 0xfd4a00（协议引擎，MUSB 同构 [SDK]）——8 站点 + 算术面：**

| 寄存器 | 地址 | 站点 | 形状 |
|---|---|---|---|
| FADDR | 0xfd4a00 | 0x600693e | ISR 基址：复位 case 写 0（@0x600699a），其余经 +0..+0x38 偏移读整块中断区（§1.2） |
| POWER | 0xfd4a04 | 0x6007324,0x60358a8 | 写 0x40（slave init @0x600733a）；任务模式机 case 读写基址 |
| INTRTX1E | 0xfd4a1c | 0x60045f4,0x6004610 | 置位/清位（16 位跨 [0xfd4a1c]/[0xfd4a20] 两字节寄存器拼接，@0x60045fa..0x600460e） |
| INTRRX1E | 0xfd4a24 | 0x600469a,0x60046b6 | 同上置位/清位对 |
| FRAME1 | 0xfd4a30 | 0x60067de | 读 [0xfd4a30]/[0xfd4a34] 拼 11 位 SOF 帧号 getter（@0x60067e4..ee，usb_get_frame） |
| （INTRTX/RX/USB、DEVCTL、EP0 CSR0 0xfd4a14、COUNT0 0xfd4a2c、EP1..4 CSR 族 0xfd4b10..0xfd4e3c） | — | 无字面量站点 | 全经 FADDR 基址+0x100*ep+偏移算术到达（ISR/EP0 机/端点配置族消费）[静态：访问形状钉，位语义未逐个钉] |

**C. JL_USB_IO 0xfd6000（DP/DM 引脚面）——15 站点：**

| 站点群 | 形状 |
|---|---|
| 0x60065c8,0x600714a,0x60357e8,0x6006f52,0x60074b2,0x6084f1e,0x6068a8e 族 | RMW：\|=0x5000（上拉族）、\|=0x200/0x100/0x2000（sie init）、\|=8/&=0xFFFFFD5F（任务接管）、|=0xC/&=~0xF0 族（释放）、|=4/\|=0x100（OTG sof check）、位域 insert |
| 0x6084ec8,0x6084f9a,0x6005004,0x608506e,0x60850d8 | 2 位字段 insert/读取 helper 族（bit[3:2]/[9:8]/[11:10]/[5:4]/[7:6]——DP/DM 上下拉选择 [推断]），gpio 式 API |
| 0x601b15c | bit[3:2] RMW insert（充电检测 DP 控制） |
| 0x60682bc（读 [0xfd6014]=IOMAP_CON0）,0x6068928（bit0 读+P33 混合）,0x60697da,0x6069cd2,0x60c847e | 低功耗快照/恢复 + DP 唤醒面；**0xfd6014 读已按 port 投影放行**（[模型]，路 AW 修正 BO 的锚点即此） |
| 0x60357e8 | 任务接管（见 CON0 行） |

**D. EP3 区未名寄存器组 0xfd4d50**：站点 0x600520a（`r13=0xfd4d50`，@0x6005218..读
[r13] bit0 + [r13+8]/[r13+12] 拼 16 位计数）——EP3 页 +0x40，br28.h 无名；消费函数
0x60051fb 做采样率算术（[obj+184]→[r0+16]/1000、h[r0+24]>>3 等）+ 从 staging
**[0x1249E4]** 取活动配置——**ISO 数据泵/异步反馈面**（pcspk 下行数据的 FIFO 计数族
[推断]）。

### 1.4 描述符与端点布局 [静态：镜像字节]

- **设备描述符 @0x60a22f2**：`12 01 10 01 00 00 00 40 | 54 36 | 55 41 | 00 01 | 01 02 03 | 01`
  ——USB 1.1 全速、EP0 64B、**VID 0x3654 / PID 0x4155('UA')**、iMfg 1/iProd 2/iSer 3、
  1 配置。**实测真机身份=3654:4a55（[文档] usb-recovery.md）≠ 静态模板 0x4155** → 运行时
  改 PID 的代码 **[未定位]**（全 bin 无 `54 36 55 4a` 字节序、无 0x4a55 li）。
- **字符串**：iProduct **"QCY H3S"** @0x60a212b（bLen 0x10 UTF-16，引用 0x6006686/0x6006ce0
  驱动簇）；iManufacturer "Jieli Technology" @0x60a2870（bLen 0x22；引用 [未定位]）。
- **UAC 模板片段**（拼装式，SDK descriptor.c/uac1.c 的 desc_config 回调同构 [SDK]）：
  - AC 族 @0x60a1c30..0x60a1c68：接口头（class 01 sub 01）+ AC HEADER（wTotalLength
    0x28/0x27 两变体）+ 输入终端（0x0301 与 **0x0201=麦克风**）+ FEATURE_UNIT；
  - **spk AS 族 @0x60a2fa0..0x60a300f**：alt0（0 EP）/alt1（**16bit**，2ch，44.1/48/
    **96k** 三频点，**EP 0x03 OUT**，iso，wMaxPacketSize **388**）/alt2（**24bit** 2ch，
    同 EP）——96k 与音频域 SR=96000 默认互证；
  - **mic AS 族 @0x60a2ae0..0x60a2b1a**：alt0/alt1（1ch 2B 16bit，**16k**/44.1k/48k，
    **EP 0x83 IN**，iso，wMaxPacketSize **96**）。
  - **模板→代码的引用通道 [未定位]**：全 bin（字+li 双扫）**没有任何指针指向 0x60a2ae0/
    0x60a2fa0 模板区**——拼装代码在 RAM 库（0x60c8xxx）或经偏移表间接寻址，静态未钉。
- **端点布局（从描述符+驱动面互证）**：EP0 控制 64B；**EP3 双向 ISO**（OUT=pcspk 下行
  388B/帧，IN=pc_mic 上行 96B/帧）；EP0_ADR/EPx_TADR/RADR（§1.3A）= FIFO DMA 地址面，
  ep0 缓冲 64B 分配+五端点 raddr 设置=0x6004aee/0x6004b94 族（caller 0x6004a0c/0x6004adc，
  [SDK] usb_device_init 的 usb_alloc_ep_dmabuffer 同构）。HID 面：0x60a2ed8 起 usage-page
    状字节块疑似 HID 报告描述符（复合设备含 HID，[文档] usb-recovery.md 身份行互证），
  归类 [未钉死]。

## 2. 「主机枚举未建模」的精确边界（BLOCKED 的确切内容）

**仿真里走到哪会停**：现有模型对 USB 面的全部放行=写记录/引脚 RMW 容器（§4），因此——

1. **任务分发墙（先撞）**：usb_stack 任务体/OTG 消费者/ISR 三段全在调度器之后。当前前沿
   3,137,573 停在 initcall #4 内部，#5/#6 纯 RAM [文档 路 AG]；走到任务分发需先过 D1 阶梯
   （BF core-tick→BG/BH 电源尾→BI 全核 IDLE→BJ 验收）——**这墙与 USB 无关**。
2. **CON0 读回墙（任务首跑即撞）**：任务体第一圈 `[0xfd1800]` 读（0x603587e）——现模型
   无 USB 状态字：读回语义（bit0 设备使能、bit3/bit13、bit16/17 线态、0x820 掩码）零建模，
   未映射读即停。OTG 消费者 0x60074a0 的 RMW 读同族。
3. **SIE/EP 墙（上线链撞）**：usb_slave_init 写 POWER=0x40/INTRUSBE/FADDR（写侧可按
   记录放行）→ 但**没有任何模型会制造 SETUP**：IRQ29 永不触发、INTRTX1/INTRUSB 永远无源、
   EP0 CSR0（0xfd4a14）握手永不推进、FRAME 计数不动、0xfd4d50 FIFO 计数无数据——**枚举
   握手需要主机侧输入，这正是 BLOCKED 的实体**。

**主机侧最小行为集**（方案 (a) 需要建模的全部；按 USB 1.1 全速设备枚举时序）：

| # | 主机行为 | 设备侧消费点（固件锚） | 模型面 |
|---|---|---|---|
| 1 | 总线复位（SE0>10ms） | ISR INTRUSB bit2 case（0x6006980）：FADDR=0、状态=2、回调环 | IRQ29 交付+INTRUSB 置位 |
| 2 | SET_ADDRESS(n)：SETUP 8B→IN 空 STATUS | EP0 机（0x6006a32..）CSR0 握手、FADDR 写 | EP0 FIFO 注入+CSR0 位推进 |
| 3 | GET_DESCRIPTOR{dev,cfg,str,lang} | 描述符发送 helper（0x600491a 族）+ 模板拼装（**拼装通道本身 [未定位]**） | EP0 TX 数据泵 |
| 4 | SET_CONFIGURATION(1) | usb_device_mode→类注册→端点 CSR 配置（EP3 双向 ISO） | TXCSR/RXCSR/RXTYPE 写路径 |
| 5 | SET_INTERFACE(alt1)（音频流开） | uac spk/mic alt 切换 | 同上 |
| 6 | SOF（1ms 帧起点） | FRAME1/2 getter（0x60067de）+ IRQ28 handler（0x60067fc）+ OTG sof check（0x6006f52） | 帧计数器/IRQ28 |
| 7 | ISO OUT 令牌（spk 数据入） | EP3 RX FIFO→0xfd4d50 计数→ISO 泵（0x60051fb 族）→uac_speaker→pcspk 节点 | FIFO 数据注入 |
| 8 | ISO IN 令牌（mic 数据出） | pc_mic 节点→EP3 TX FIFO | FIFO 抽取 |
| 9 | suspend/resume | INTRUSB SUSPEND 位、低功耗快照对（0x60c846c/98） | 可选（充电交互用） |

## 3. 建模方案候选（三选一预演）

| | (a) 最小主机仿真器 | (b) 钉住（跳过枚举直进「已配置」） | (c) 具名 fixture（端点缓冲预填） |
|---|---|---|---|
| 建模什么 | JL_USB+SIE 设备模型+剧本化主机：上电→IRQ29 复位→按序注入 SETUP（SET_ADDRESS/GET_DESCRIPTOR×N/SET_CONFIG/SET_INTERFACE）→SOF 计数→可选 ISO 静音/正弦流 | usb 对象状态字节 fixture（模式字节 0x127190、b[对象+1869] 类位、b[对象+3024..3045] 计数器族）+ CON0/POWER/FIFO 计数的**读回投影**（bit0=1 设备在线、线态位、0xfd4d50=「有数据」） | 预填 EP FIFO/计数让音频泵跑——**不解决到达问题**，只是 (a)/(b) 之后的数据面补件 |
| 固件哪段真跑 | 全链：ISR→EP0 机→描述符拼装→类注册→管线启动 | 任务模式机+OTG 消费者+pipeline-PCAudio 切换+pcspk/pc_mic 节点 | ISO 泵+效果链 |
| 风险 | ①描述符拼装通道 [未定位]，可能揭出新未知域；②CSR0/INTR 位语义是 MUSB 惯例推断，无实测；③面大（9 行为×握手细节） | ①「已配置」状态字节的精确取值集 [未定位]（模式字节 0..6 case 体未逐个钉）；②枚举路径零验证 | 同左，且单用卡在到达前 |

**推荐 (b) 为主**，(c) 作为其数据面附件；**(a) 列 D3/专项**。理由（ROI）：D2 有线用例
（路 AT 冻结）的断言对象是「**插拔→事件→路由切换→旧流收尾→返回**」——路由面，不是 USB
协议正确性；注入面=「事件 130/131 投递到 usb_stack 队列」（纯 RAM 写，现有模型即可做）+
模式机走通所需的读回钉子（路 AL/AM「读回=最后写值+显式初始字」同族先例，逐位具名假设）。
(a) 的收益（真协议路径+描述符拼装验证）属另一个验收维度，且被 §2 表 #3 的 [未定位] 卡着，
先做 (b) 还能反过来用真跑把模式字节 case 体钉死，为 (a) 铺路。

## 4. 与主前沿的关系（何时撞上 USB 域）

**已放行的 USB 面（[模型] executor 报告+路 AW）**：
- `port_initialization_projection` 的 **usb_disable_writes=[0xfd1800,0xfd1804]**（port init
  0x6068a82/a4 两笔整字写 0——本路 0x6068a7c li 站点即此，全部场景 149,660 步前已发生）；
- **USB_IO 0xfd6000**：初始 0 fixture+RMW 容器（`initial_usb_io_word=0`，未知位保留
  `usb_io_unknown_bits_preserved`）+ **IOMAP_CON0 0xfd6014** 读消费已按已知 0 放行；
  `usb_controller_status_transfer_or_pin_electrical_model=false`、`iomap_configuration_only=true`
  ——**即：配置/引脚层已过，状态/传输层零建模**（与 §2 的墙精确对齐）。
- SIE 域 0xfd4a00 族、EP CSR/FIFO、CON0 读侧：已运行前缀**零访问**（报告 registers 键+
  全部场景 writes 交叉验证 [模型]）。

**主线还会不会碰 USB（initcall 剩余项）**：#5 0x60614a2 / #6 0x60009e8 纯 RAM [文档 路 AG]；
initcall 表尾之后=调度器/任务分发域。USB 的三个代码体（任务 0x6035840、消费者 0x600741c、
ISR 0x600693a）+全部 SFR 站点都在任务分发墙之后——**主线（initcall walk 及其前后 init）
不再触碰 USB 状态面**。

**相遇点评估**：D1 剩余阶梯（BF..BJ）不被 USB 阻塞；BJ 之后事件域起跑，**第一次 USB 状态
访问=usb_stack 任务体首圈的 [0xfd1800] 读（0x603587e）或 OTG 消费者消息泵的 CON0 RMW 读
（0x60074a0）**；第一次 SIE 域访问=usb_slave_init 写序列（POWER=0x40 等，写侧可先按记录
放行）与 request_irq(28/29)（RAM 写，BE 机制域）。即：**主前沿撞 USB 的时点=D1 验收后的
D2 事件域开工处**，与路 AT「D2 扩展用例」冻结裁定互锁成立，无需为 USB 调整 D1 计划。

## 5. 对路 AT 文档的三处勘误（地址重算，偏移不变）[静态]

路 AT（docs/wired-game-mode.md §1.2）的偏移数对、**绝对地址有三处与 vendor li 行不符**：

| 项 | 路 AT 写 | 本路重算（依据） |
|---|---|---|
| 创建门 b[QCY+1864] | 0x1268D8 | **0x126D08**（`li r5=0x1265C0`+`add 0x748` @0x6006fd0/d6；0x1268D8 疑为数位错置） |
| 模式字节 b[QCY+3024] | 0x126D60 | **0x127190**（0x1265C0+0xBD0，@0x6035894/0x6006ff8 两写点） |
| usb 子配置源 | 0x1249F4 起 12B | **0x1248A4 起 12B**（`li r0=1198244` @0x600741e + rep 3 迭代；0x1249C0..0x1249FF 是**另一块** 0xFF 填充 staging，rep 16×4B=64B @0x6034f04——128B 之说按 vendor 单 store 体为 64B [未定：若 store 体为并行对则 128B]） |

另：路 AT「上电默认 0xFF=使能」的**默认值来源**应落在 0x1248A4 块的初始化处（0xFF 填的是
0x1249C0 staging 块），0x1248A4 块的填充者 **[未定位]**——门语义（bit0=0→模式 7 禁用）不变，
默认值的证据链比路 AT 表述弱一档，如实降级。

## 6. 未钉死处（诚实清单）

1. 描述符模板→代码的引用通道（拼装代码在 RAM 库还是偏移表）；设备 PID 0x4155→实测 0x4a55
   的运行时改写点。
2. 模式机 case 0..6 的逐 case 行为（SIE POWER 各 case 的位组合）；IRQ28/29 与 SOF/CTRL 的
   名字对应；EP0 CSR0 握手的位语义（MUSB 惯例推断，无实测）。
3. 「已配置」状态字节的精确取值集（方案 (b) 实施时需先钉）；0x1248A4 块的默认值填充者。
4. EP1..4 RADR/RLEN/MTX/MRX 族的算术访问面（无字面量站点）；0xfd4d50 计数语义。
5. usb-recovery.md 的 ROM loader USB 面（bootrom 层）与本页运行时栈的关系未逐层对账
   （FD07 读回通道复用 JL_USB FIFO 的程度）。

## 7. 证据锚点索引（复核用）

| 结论 | 锚点 |
|---|---|
| call 目标公式（vendor 操作数=真位移） | 0x6006f3c→0x600693a、0x6007424→0x6006fce、0x6007030→0x60669a6、0x6007412→0x6064978（均落函数序言 `[--sp]=…`） |
| usb_init 12B 复制+门+禁用 | 0x6006fce..0x6007002（li 0x1265C0/rep 3/位门/b[0x127190]=7） |
| 任务创建链 | 0x60070aa（对象槽）→0x6007010→0x6064162（队列）→0x6007028 li 0x6035840→0x6007030→0x60669a6（task_create）→0x6007036 存句柄 |
| 任务体模式机 | 0x6035840..0x6035a90（tbh 0x60358b2 前的 and 0xF/bgt 6；重试环 0x6035a12..；CON0 bit13 0x6035a3a；模式改写 0x6035a72..80） |
| 线态 helper 组 | 0x60357b4（bit16）/0x60357c2（bit17）/0x6035804（&0x820）/0x60357d0（=0x1005+USB_IO 接管） |
| OTG 消费者+注册 | 0x600741c 体 0x600741c..0x60074d4；注册 0x6012316/0x601231c；'OTG'/'s'/IN case 0x6007466..0x60074c4 |
| IRQ28/29 注册 | 0x60073a2（29,0x6006f38）/0x60073b4（28,0x60067fc）；蹦床 rti 0x6006f46 |
| SIE ISR 结构 | 0x600693a..0x6006b00（INTR 掩码族 0x6006944..97c；复位 case 0x6006980..9c6 含 FADDR=0 @0x600699a；EP0 bit0 门 0x6006da；EP0 机 0x6006a32..） |
| slave/sie init | 0x600714a..0x6007172（sie）/0x6007324..0x6007342（POWER=0x40、CON0\|=0x800） |
| EP 原语族 | 0x60045d0/0x600466e（EP0_ADR）、0x60045f4/0x6004610（INTRTX1E）、0x600469a/0x60046b6（INTRRX1E）、0x600470e..0x6004720（TXDLY+CON0 ep 位）、0x6004b16（EP_CNT）、0x6004bbe（TADR 环）、0x60067de（FRAME） |
| 描述符字节 | dev@0x60a22f2、iProd@0x60a212b、iMfg@0x60a2870、AC@0x60a1c30..68、spk AS@0x60a2fa0..0x60a300f、mic AS@0x60a2ae0..0x60a2b1a、疑似 HID@0x60a2ed8 |
| ISO 泵/0xfd4d50 | 0x60051fb..0x600523e（r13=0xfd4d50 读族+采样率算术+staging [0x1249E4]） |
| 充电域 OTG | 0x601b15c（USB_IO 位域）/0x601b318（CON0&0x200000） |
| 低功耗快照对 | 0x60c846c/0x60c8498；恢复 0x6069cd2 |
| 已放行 port 面 | executor 报告 `port_initialization_projection`（usb_disable_writes/initial_usb_io_word=0/IOMAP 已知 0/usb_controller_status_transfer_or_pin_electrical_model=false）；写点 0x6068a82/a4（路 AW） |
| SDK 结构 | br28.h L713-740（JL_USB）、L1108-1177（SIE/EP0/EP1..4）、L1211-1220（USB_IO）；701n usb_task.c/usb_device.c/descriptor.c/uac1.c（结构参照） |
