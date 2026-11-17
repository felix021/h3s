"""Evidence-scoped BR28 register models. No device access or analog simulation.

Sources and remaining hypotheses: docs/hardware-contracts.md. Unknown reads,
capture/PWM modes, reset stimuli and actual CPU interrupt delivery still stop.
"""
from collections import Counter
import operator

from probe_boot import Blocked, IrqDeliveryRequest

TIMER0=0xfd0400
# 路 BK/S19 批 1:JL_TIMER2(br28.h ls_base+map_adr(0x06,0),CON/CNT/PRD 与
# Timer0 同族 JL_TIMER_TypeDef)。W4[4] 0x60685fe→0x12cd26 延时 helper 的
# 忙等域:整字写 0x4000(清 pending 停表态)/CNT=1/PRD=[0x129D88]×us/1e6
# (us=1000→200 LRC tick)/CON=0xC01(LRC 源 src3、div=1、mode1)启动,退出
# 写 0xCC01(bit14 W1C)与 0(停表)——全 26 指令反汇编见 docs/timer2-
# eventclock-design.md §1.3。
TIMER2=0xfd0600
# 特殊写 (cnum<<11)+0xFFE1B0(raw 立即数 0x3FF86C<<2;路 BK §1.2 修正 BA 的
# 0xFFF130 笔误):SDK csfr.h JL_TypeDef_q32DSP index 0x6c=IWKUP_NUM(IPMASK+8,
# GIEMASK 后一槽),写值 6=hwi.h IRQ_TIME2_IDX,写时机在 idle 前——「本核
# idle 唤醒中断号设为 TIMER2」读法多点互证,硅效果未测量,建模 write-only
# 位记录。canonical IPMASK=0xffe1a8+0x8 与 banked 0xffe168 视图同槽。
IWKUP_NUM_BASE=0xFFE1B0
IWKUP_NUM_ADDRESSES=(IWKUP_NUM_BASE,IWKUP_NUM_BASE+0x800)  # per-core +cnum*0x800
# LRC 名义频率(derived-not-measured):AL 恒等式 7.68e9/38400=200000,即固件
# 自己的名义常数([0x129D88] 均值,trim 结果未测量)。16MHz RC16M 参考系下
# 1 LRC tick=80 参考 tick;每模型步 4 参考 tick → 1 LRC tick=20 模型步、
# 1ms(200 LRC tick)=恰 4000 模型步(闭式,docs/timer2-eventclock-design.md §3.1)。
LRC_HZ_NOMINAL=200000
LRC_REF_TICKS_PER_LRC_TICK=16_000_000//LRC_HZ_NOMINAL  # =80
# 路 BM §1(S19 批 1):W4[4] PVDD/trim 域容器三地址(p33_sfr.h 纯地址宏,
# 无 __RW/__RO/__WO 标注——读回语义取 VOLTAGE_CONFIG/P33_ANALOG_CONFIG/
# p33_analog_tail 一脉的既有先例,不臆造硅模拟状态)。P3_ANA_CON13/
# P3_ANA_CON1/P3_PVDD0_AUTO;P3_PVDD1_AUTO(0xf2c110)已由 VOLTAGE_CONFIG
# 建模、P3_ANA_CON5(0xf2c254)已由 p33_analog_config 建模,不并入本常量
# (BV/BN 两次先例的同一纪律:并入会改「旧容器开而本开关关」世界的报告形状)。
JL_PVDD_TRIM_CONFIG=(0xf2c274,0xf2c244,0xf2c10c)
# 路 BM §4.3 行 0b 勘误/S19 批 1 ④:JL_AUDIO->DAC_COP(0xfe0534=JL_AUDIO
# 基址 0xfe0500+0x34,br28.h 成员 13 __WO,DAC chopper/泵配置字)。全 app
# 恰两处访问、全部纯写 0x7F:W1[3] 尾段输出级门控 0x606ab2c([r5+52],
# r5=0xfe0500)与音频域 0x606e038(li 0xfe0534 直存,esco/ANC 邻域函数);
# 零读回观测(D2 场景表的 0xfe0534 行属 D1 后音频任务域)——按消费面判定
# =write-only 整字记录(路 CV 分类先例:__WO 裁剪操作字,无读侧不立容器)。
JL_AUDIO_DAC_COP=0xfe0534
# 路 BM §4.3 行 0b 勘误之二/S19 批 1 ④续:W1[3] 尾段 DAC 启动函数 0x6071e74
# 在 call 0x606ab0a(输出级门控)返回后的四个 DAC DMA 配置整字写(0x6071e92
# 起,[r10+k],r10=0xfe0500):[+8]=1、[+4]=0x18e180(60KB DMA 缓冲首址,与
# 0x6071d50 memset 目的同址互证)、[+0x14]=0、[+0x10]=0——成员名按路 AG
# wlaud-init-map「DAC_LEN/ADR/HRP/PNS 整字写」的布局链([SDK推断]级命名,
# br28.h JL_AUDIO_TypeDef 全 __RW 家族);探路运行实证**恰四写、零读回、
# 零 RMW**(RMW 面 0x6071eb8 的 [r10+0]|=0x110 是 DAC_CON,AK 容器服务)。
# D2 场景表对 0xfe0504/08 的「DMA 指针/计数读×6」消费面属 tone/PCM 推送域
# (0x607236c caller 0x607249c/0x6073258,D1 后)。
JL_AUDIO_DAC_DMA_WORDS=(0xfe0504,0xfe0508,0xfe0510,0xfe0514)
# 路 JLA:JL_AUDIO ADC 通道缓冲配置五字(set_buffer 0x606dee4 五写,
# [r4+k] r4=0xfe0538=ADC_CON):[+4]=ADR(缓冲首址)、[+8]=LEN(buf_num*len>>1
# 的 uextra 16 位)、[+0xc]=PNS(len>>1)、[+0x10]=HWP=0、[+0x14]=SRP=0
# (docs/d2b-injection-blueprint.md 预案的五写;成员名 SDK 推断级,br28.h
# JL_AUDIO_TypeDef 全 __RW 家族、csfr.h __u16→32 位字槽,布局只定位字)。
# 全 app 访问面(逐 2 字节独立解码 li 枚举+站点线性窗数据流解析,codex 复跑
# 互证):五字恰 9 条已识别访问=set_buffer 五条连续整字写(纯写、旧值无关、
# 值=真实指令结果)+audio_adc_irq_handler [0x6072628,0x6072700)(IRQ68 分发
# 器 0x602f444 直调)读 ADR/PNS/SRP 三条+写 PNS 一条——后者当前前缀不可达
# (IRQ68 交付未建模),读的是运行期 DMA 指针状态,属 D2-b 后域留严格停。
# 0xfe0550(handler 尾写的 +0x18 SRN 读通知字)零当前前缀触达,不进白名单。
JL_AUDIO_ADC_DMA_WORDS=(0xfe053c,0xfe0540,0xfe0544,0xfe0548,0xfe054c)
# 路 D2AI/sim-d2ai:ADC DMA 读通知字(handler 尾 [r10+24]=r8 的 0xfe0550,
# JL_AUDIO 基址 0xfe0500+0x50=成员 SRN)。五字容器交付模式下并入白名单
# (路 JLA 裁决「不收 SRN」是 write-only 半边的边界;读侧/交付半边=本路
# 扩类后的新事务,PRE §6.3 钉为交付段硬前置之一)。
JL_AUDIO_ADC_SRN=0xfe0550
# 路 JLEQ/sim-jleq:EQ 初始化函数 0x6072b24 的两处整字写——JL_EQ->CON0
# (0xfe1500=JL_EQ 基址 0xfe1500+0)与 JL_EQ->FLT_ADR(0xfe151c=+0x1c)。
# br28.h `JL_EQ_TypeDef {__RW __u32 CON0..CON3, DATAI_ADR, DATAO_ADR,
# DATA_LEN, FLT_ADR}` 全 __RW、8 成员 0xfe1500..0xfe151c;boot 路径只写
# 不读(0x6072b92 [CON0]=r5=0、0x6072bae [FLT_ADR]=分配缓冲指针),两写
# 都是整字 st.w、无 RMW、无条件执行(codex 勘误:0x6072b68 bnez 只跳过
# 分配+首次 memset,目标 0x6072b90,CON0/FLT_ADR 两写仍必达)。按 JLA
# write-only 记录先例:恰两字、零读回、零合成初值,读侧一律严格停。
JL_EQ_INIT_WORDS=(0xfe1500,0xfe151c)
# 路 ASS:ADC 启动函数 0x606df66 的未建模配置块四字——JL_ASS->MIC_CTL0
# (0xfe0904=JL_ASS 基址 0xfe0900+4,br28.h JL_ASS_TypeDef 三成员 CLK_CON/
# MIC_CTL0/MIC_CTL1 之二)、JL_ASS->MIC_CTL1(0xfe0908,之三)、JL_AUDIO->
# MBG_CON(0xfe0554=JL_AUDIO+0x54)、JL_AUDIO->ADC_CON1(0xfe055c=+0x5c;
# 路容器注释钉的成员名,[SDK 推断]级,csfr.h __u16→32 位字槽,全 __RW
# 标注非硅读回证据)。21,800,455 停点 0x606e044 `[r0+0]|=0x2` 内部读
# 0xfe0904——地址 r0=r5+976 构造自 r5=0xfe0534(li @0x606e032),全 app
# 无直接 li 站点(间接构造地址的首个停点);基址+偏移传播解析法扫描
# (~/tmp/ass-resolve.py,356 li 锚点全 GPR 常量跟踪)证实四字访问面
# 全部为配置 RMW/insert 写回组装形状,读值零分支消费(B3/DT/ADA/LPV
# 判据),0xfe090c/0xfe0910/0xfe0560 零触达,0xfe0550 SRN 归 JLA 边界。
JL_ADC_START_CONFIG_WORDS=(0xfe0904,0xfe0908,0xfe0554,0xfe055c)
PLL0=0xfd4600
CLOCK0=0xfd0018
SYS_DIV=0xfd0014
# JL 时钟族(0xfd0004 起 JL_CLOCK_TypeDef,br28.h 全字段 __RW)里唯一未建模的
# 三个数字时钟配置寄存器:DCCK_CON0/1/2。0xfd0004 PWR_CON=RESET_FLAGS、
# 0xfd0000 RST_SRC=RESET_REASON_INPUTS(复位历史域);SYS_DIV/CLK_CON0/1/2 是
# clock_divider_projection 与基模型 registers 槽。全 app+ROM 0xfd0008 恰 1 处
# li(0x6068176,mode 切换函数 0x6068152),0xfd000c/0xfd0010 经 [r0+4]/[r0+8]
# 派生;SDK power_clock.c 的 deep-sleep save/restore 把三字列入可读回/可恢复
# 清单。访问形状=原子 RMW×3 + 显式 ld/st RMW×3 组(DCCK0)、显式 ld/st RMW×2
# (DCCK1)、整字写 0x10000(DCCK2)——见 docs/tail-clk-boundary.md §1.2。
DCCK_CON0=0xfd0008
DCCK_CON1=0xfd000c
DCCK_CON2=0xfd0010
JL_CLOCK_DCCK=(DCCK_CON0,DCCK_CON1,DCCK_CON2)
PINR1=0xf2c11c
PINR0=0xf2c120
IRQ_CONFIG=0xffe100
IPMASK=0xffe1a8
IRQ_IPMASK_BANKED=0xffe168  # handler/idle-loop addressing: base+cnum*0x800 (0x12b56c, 0x12ad5c)
DIVISORS=(1,4,16,64,2,8,32,128,256,1024,4096,16384,512,2048,8192,32768)
GPIO_BASES=(0xfd5000,0xfd5100,0xfd5200,0xfd5300,0xfd5400,0xfd5500,0xfd5700)
MMU_BASE=0xff0400
P11_UART_CON2=0xf2af10
PMU_STA=0xfd3e04
PWR_CON=0xf2a400  # PMU PWR_CON; bit5 LP_PWR_INIT_FLAG (main-core request), bit7 POR flag
LP_PWR_INIT_FLAG=0x20
LP_RST_FLAG=0x80  # PWR_CON bit7 LP_PWR_RST_FLAG(POR=1;主 init 0x6068cf2 bit6 命令清除)
Q32S_PMU_CON=0xf2a0c0  # P11 侧 q32s 核的 PMU_CON(p11_sfr 窗口;主核 power_init |=1 释放)
P11_SYS_DIV=0xf2a410    # SDK p11_sfr.h P11_CLOCK.SYS_DIV
P11_CLK_CON0=0xf2a414   # SDK p11_sfr.h P11_CLOCK.CLK_CON0
P11_HEAP_INFO=0xf27f4a  # P11 RAM P2M message area +0xa: 4B heap begin/size written by P11 init (value unproven)
P11_MEM_PWR_CON=0xf2a62c  # SDK p11_sfr.h P11_SYSTEM.MEM_PWR_CON = P11_SYSTEM_BASE(map_adr(0x06,0)=0xf2a600)+11*4
P11_LPTMR0_CON0=0xf2a900  # SDK p11_sfr.h P11_LPTMR0_BASE = p11_sfr_base(0xf2a000)+map_adr(0x09,0x00)
P11_LPTMR0_CON1=0xf2a904  # 结构 CON0/CON1/CON2/PRD/RSC/CNT(__RO);CNT 在本前缀无访问观测
P11_LPTMR0_CON2=0xf2a908
# P11_LPTMR0 CON 三字在 SDK p11_sfr.h P11_LPTMR_TypeDef 里都是 __RW(可读回),
# 与 PMU 配置族 0xf2c04c/0xf2c050(SDK 无名)不同族——读回投影只覆盖这三个。
LPTMR_CON_ADDRESSES=(P11_LPTMR0_CON0,P11_LPTMR0_CON1,P11_LPTMR0_CON2)
POWER_INIT_PMU_CFG0=0xf2c04c  # power_init 尾部 PMU 配置族整字写(SDK 无名;与 0xf2c048/0xf2c054/0xf2c05c 同族)
POWER_INIT_PMU_CFG1=0xf2c050
LRCT_CON=0xfd3600  # SDK br28.h JL_LRCT = ls_base(0xfd0000)+map_adr(0x36,0x00);CON __WO,NUM __RW
LRCT_NUM=0xfd3604  # SDK br28.h JL_LRCT_TypeDef 第二个字 NUM(__RW);trim 结果计数,固件唯一访问=0x6067520 读
WDT_CON=0xf2a800
WDT_KEY=WDT_CON+4
EFUSE_CON0=0xf2c2c0
EFUSE_CON1=EFUSE_CON0+4
EFUSE_RDAT=EFUSE_CON0+8
OSC_CONFIG=(0xfd1920,0xfd1924,0xfd1928,0xfd1940,0xfd1954)  # WLA_CON8/9/10/16/21
OSC_STATUS=0xfd1978  # WLA_CON30, RO; sampled by actual QCY calibration
GPCNT_CON=0xfd1100
GPCNT_NUM=0xfd1104  # SDK br28.h JL_GPCNT_TypeDef 第二字 NUM(__RO 计数值);全 app+ROM 唯一读者=0x606955c
# 路 F(sim-fd2000):0xfd2000/0xfd2100/0xfd2200 = JL_UART0/1/2 基址
# (br28.h `JL_UART0_BASE = ls_base(0xfd0000) + map_adr(0x20,0x00)` = 0xfd2000
# =16588800,与 0x601d198 读辅助函数的 li 字面量逐位一致;JL_UART1_BASE/
# JL_UART2_BASE = map_adr(0x21,0)/(0x22,0) = 0xfd2100/0xfd2200)。读辅助函数
# 0x601d198 = SDK uart_v1.c 的 `uart_is_idle(uart_num)`(cpu.a/uart_v1.c.o
# LLVM bitcode 逐字节同形):switch{0:*(0xfd2000),1:*(0xfd2100),2:*(0xfd2200)}
# ;return (word&1)^1 —— 即 bit0 位测试(bit0=0→空闲)。JL_UART_TypeDef 里
# CON0/CON1 都标 __RW;bit0 是硬件「busy/空闲」状态位(固件从不写 bit0:
# SDK 全部 CON0 写=init 0x3400/putbyte |=0x2000/各 RMW,无一处置位 bit0),
# 读回=合成初始字 0 + 当前寄存器值(AM 容器同款先例)。三个地址是整字
# CON0|CON1 复合字(ld.w 整字读)。
JL_UART_CON_WORDS=(0xfd2000,0xfd2100,0xfd2200)
# 路 M(sim-uartwr):UART 初始化写白名单的逐字地址集。**布局钉死(修正路 F 的
# 「BAUD|BUF」命名)**:实际 SDK 构建对象(cpu.a/uart_v1.c.o 的 LLVM bitcode,
# 路 F 已证 uart_is_idle 与固件逐字节同形)把 JL_UART_TypeDef 声明为
# `{i32 x12}`——12 个成员每个占一个 32 位字,成员 k 在偏移 +4k;实际 QCY
# 固件反汇编逐点互证:uart_init 内联体 0x6034466 `[r2+0]=0x3400`(CON0,字0)、
# 0x603446c `[r2+4]=0`(CON1,字1)、0x6034472 `[r2+44]=0`(CON2,字11=+0x2C)、
# uart_set_baudrate 0x601d286 `[r7+8]=r0`(BAUD 分频值,字2)、uart_refresh_otcnt
# 0x601d1f2 `[r2+16]=r3`(OTCNT,字4)、uart_dma_init/启动包装 0x6034668..cc
# `[r6+28/32/36]`(RXSADR/RXEADR/RXCNT,字7/8/9)。**公开 br28.h 的 u16/u8
# 紧凑布局(CON0 u16@0/CON1 u16@2/BAUD u16@4/BUF u8@6/…/CON2 u16@0x22)与
# 该构建对象/固件的真实访问布局不一致**(BR31 等其它系列公开头确有紧凑布局,
# 故这不是笔误而是布局分歧;以实际编译对象+固件字节为准),0xfd2004 不是
# 「BAUD|BUF」而是 **CON1 字**——真正的 BAUD 写在 0xfd2008。白名单只收
# 本 boot 初始化序列里实际出现的纯配置写:CON1(+4)/BAUD(+8)/OTCNT(+0x10)/
# RXSADR(+0x1c)/RXEADR(+0x20)/RXCNT(+0x24)/CON2(+0x2c),三个 UART 基址对称;
# TXADR(+0x14)/TXCNT(+0x18)/BUF(+0xc)不在初始化序列(发送路径字段,TXCNT
# 写=启动 TX 硬件活动,codex 风险点)不进白名单;HRXCNT(+0x28,__RO)保持严格。
JL_UART_INIT_WORD_OFFSETS=(0x4,0x8,0x10,0x1c,0x20,0x24,0x2c)
JL_UART_INIT_WORDS=tuple(base+off for base in JL_UART_CON_WORDS
                         for off in JL_UART_INIT_WORD_OFFSETS)
# 路 R(sim-gpioface):0xfd58xx = **JL_OMAP**(SDK io_omap.h `JL_OMAP_BASE =
# ls_base + map_adr(0x58, 0x00)` = 0xfd5800,组 0x58)——GPIO 交叉开关
# (crossbar)的**逐引脚输出功能选择寄存器组**。识别链(三方交叉,全部公开
# 工件+钉定参考 SDK commit 67328f4 可复算):
# - **SDK 头命名**:io_omap.h 的 JL_OMAP_TypeDef 按成员序声明 PA0..PA15_OUT/
#   PB0..PB11_OUT/PC0..PC8_OUT/PG0..PG8_OUT/USBDP_OUT/USBDM_OUT/PP0_OUT
#   (49 个 __RW __u8 成员);io_omap.h 同时定义 FO_* 输出功能常量
#   (`FO_UART0_TX = ((39<<2)|BIT(1)|BIT(0))` = 159 = 0x9f——正是停点观测值)。
# - **SDK 驱动表与 QCY 固件表逐字节互证**:gpio_hw.c.o(cpu.a,LLVM bitcode)
#   的 `gpio_out_regs = {0xfd5800,0xfd5840,0xfd5870,0,0,0xfd5894,0xfd58c0}`
#   (A/B/C/D/E/G/P 七组,D/E 无交叉开关输出寄存器=0)与 QCY app.bin 表
#   0x609EA9C 的 7 个字**完全一致**;`gpio_in_regs = {1,17,29,0,0,38,49}`与
#   0x609EAB8 一致。SDK `gpio2crossbar_outreg` 对 gpio>208 特例 224→
#   0xfd58b8/225→0xfd58bc,与固件解析函数 0x600127a(`if (r4==224) r0=
#   16603320` 等)同值。gpio_hw.h:IO_PORT_DP=IO_GROUP_NUM*14+0=224、
#   IO_PORT_DM=225(PORTUSB=14,gpio 编号=port*16+pin),USB DP/DM 是普通
#   gpio_set_function 可用的「14 组引脚」。
# - **布局钉死(4 字步进,非 io_omap.h 的 u8 紧凑偏移)**:SDK 驱动与 QCY
#   固件都用 `gpio_out_regs[port] + pin*4` 的字寻径(SDK `getelementptr i32`,
#   固件 0x60012bc `r0=r0<<2`),B 基址 0xfd5840=A 基址+16*4 直接证明字步进;
#   io_omap.h 的 u8 成员表只在**成员序与个数**(49 个)上与真实布局对应,
#   其 u8 偏移(PB0 在 A+16 字节处)与驱动表不一致,以编译对象+固件表为准
#   (与路 M 的 JL_UART 布局分歧同款处理)。白名单=49 个 4 字对齐字:
#   A 0xfd5800..0xfd583c(16)、B 0xfd5840..0xfd586c(12)、C 0xfd5870..0xfd5890
#   (9)、G 0xfd5894..0xfd58b4(9)、USBDP 0xfd58b8、USBDM 0xfd58bc、PP0
#   0xfd58c0(引脚数=io_omap.h 成员数=gpio_hw.h 端口掩码 IO_PA_MASK 0xffff/
#   IO_PB_MASK 0x0fff/IO_PC_MASK 0x01ff/IO_PG_MASK 0x1ff/IO_PP_MASK 0x0001/
#   IO_PORT_USB_MASK 0x03,四方一致)。0xfd58c4 之后与各组未用槽一律不在
#   白名单(不给邻接地址补零)。
# 停点证据:uart_init(QCY 0x60343e8)TX/RX 引脚配置链 gpio_set_function
# (0x6001456,fn=1=PORT_FUNC_UART0_TX)的助手 0x60012c2 在 0x60012de
# `[r3+0]=r4` 整字写 0xfd58b8=0x9f——0x9f 即 FO_UART0_TX(bits[7:2]=功能号
# 39、bit1/bit0=dir/data 由外设控制的控制位,SDK gpio_func.c.o sw.bb
# `gpio_set_fun_output_port(add, 159, 1, 1)` 与固件助手 arg3!=0 不清位的
# 形状互证);值由真实指令装载(tbh case fn=1 体 0x600151e `r1 = 159` 的
# 编译字面量,SDK 同源常量),非模型硬编码。fn=2(UART0_RX)的 case 体
# 0x6001522 `r1 = 16604304`(=0xfd5c90=PFI_UART0_RX)同表互证。
JL_OMAP_OUT_PIN_TABLE=((0xfd5800,16),(0xfd5840,12),(0xfd5870,9),(0xfd5894,9))
JL_OMAP_USB_WORDS=(0xfd58b8,0xfd58bc)  # USBDP_OUT / USBDM_OUT(gpio 224/225 特例)
JL_OMAP_OUT_WORDS=tuple(base+4*pin for base,pins in JL_OMAP_OUT_PIN_TABLE
                        for pin in range(pins))+JL_OMAP_USB_WORDS+(0xfd58c0,)
# 路 V(sim-imapwr):0xfd5c00..0xfd5cf8 = **JL_IMAP**(SDK io_imap.h
# `JL_IMAP_BASE = ls_base + map_adr(0x5c, 0x00)`,组 0x5c)——GPIO 交叉开关的
# **外设输入功能选择寄存器组**(每个外设输入信号选一个输入通道/引脚)。
# 识别链(三方交叉,全部公开工件+钉定参考 SDK commit 67328f4 可复算):
# - **SDK 头命名**:io_imap.h 的 JL_IMAP_TypeDef 按成员序声明 FI_GP_ICH0..
#   FI_GP_ICH13/FI_SPI0_*/FI_SPI1_*/FI_SPI2_*/FI_SD0_*/FI_IIC_*/FI_UART0_RX/
#   FI_UART1_RX/FI_UART1_CTS/FI_UART2_RX/FI_TDM_*/FI_RDEC*/FI_ALNK0_*/
#   FI_PLNK_*/FI_CHAIN_*(63 个 __RW __u8 成员,另有 FI_TOTAL 计数哨兵——
#   不是成员地址);io_imap.h 同时定义输入通道常量 PA0_IN=1..PP0_IN=49,
#   **USBDP_IN=47**(停点观测值 0x2f)。
# - **布局=4 字步进是 SDK 的显式设计,非分歧**:csfr.h(pinned)`#define __u8
#   unsigned int  // u8 to u32 special for struct`——SFR 结构体成员一律按
#   32 位字编译,gpio_hw.c.o(cpu.a,LLVM bitcode)的 PFI_* 枚举(=gpio_hw.h
#   `((u32)(&(JL_IMAP->FI_*)))` 的编译常量)逐成员 16604160..16604408
#   (=0xfd5c00+成员序×4)连续 4 字步进,63 个成员与 io_imap.h 成员表一一
#   对应;gpio_set_fun_input_port 体 `store i32 %call, ptr %0, align 4`
#   整字写。路 R 对 io_omap.h「u8 偏移与驱动表不一致」按 csfr.h 同源解释。
# - **SDK 驱动与 QCY 固件双侧互证**:gpio2crossbar_inport(gpio_hw.c.o)
#   gpio>208 特例 224→47(USBDP_IN)/225→48(USBDM_IN),否则通道号=
#   gpio_in_regs[port]+pin(通道号表 {1,17,29,0,0,38,49},D/E 组无输入);
#   QCY 固件 twin 0x60012e8(`if(r4==224) r0=47`/`if(r4==225) r1=48`,
#   表 0x609EAB8 与 gpio_in_regs 逐字节一致,`r0 += gpio&0xF` 无移位=
#   通道号非地址);gpio_set_fun_input_port 固件 twin 0x600132e:入口
#   r4=r1(pfun=PFI_* 常量)→call 0x60012e8→临界区→**0x6001342 `[r4+0]=r3`
#   整字写**(路 R 停点)→临界区退出;gpio_set_function 分发器 case 体
#   0x6001522 `r1 = 16604304`(=0xfd5c90=PFI_UART0_RX)是 pfun 实参字面量。
# - **访问面(全镜像扫描)**:app.bin 字面量扫描 0xfd5c00..0xfd5cff 共
#   37 处,全部落在 63 字成员组内(0xfd5c00..0xfd5cc0 的 PFI_* case 体/
#   GP_ICH 位图走查 0x60013e0 系/0x60196d2 系索引寻径),直接访问全部是
#   整字 st.w(复用写+disable 清零 0x601968c `[r0+0]=0x0`),**零读回**;
#   ROM 0xa884 单处 0xfd5c04 字面量是把常量存进 RAM(指针变量),非 IMAP
#   访问。白名单=63 字(成员数=io_imap.h 成员表=PFI_* 枚举数);
#   0xfd5cfc 之后不在白名单(不给邻接地址补零)。
JL_IMAP_IN_WORDS=tuple(0xfd5c00+4*member for member in range(63))
# 路 AK(sim-audioinit):0x602ea68 boot 期音频框架 init 的两个已审查写域
# (函数全图/写集枚举/停点序列见 docs/wlaud-init-map.md,路 AG 只读预研;
# SDK 名字/偏移按钉死参考 br28.h sha 111e29ea… 逐条复核:JL_WL_AUD=
# hs_base+map_adr(0x22,0)=0xfe2200 单成员 __RW CON0;JL_AUDIO=hs_base+
# map_adr(0x05,0)=0xfe0500,csfr.h `#define __u16 unsigned int` 显式 32 位
# 字槽 → 成员序×4 偏移,ADC_CON@+0x38=0xfe0538(路 AE 已钉)、AUD_CON@+0x58
# =0xfe0558;JL_ASS=hs_base+map_adr(0x09,0)=0xfe0900 三成员之首 CLK_CON)。
# 默认路径(G6 门字 word@0x127364=0 动态复现钉死→esco 块整段跳过;config
# 88/92 已占用→缺省分支不走)只有这 4 个寄存器 7 个站点:
# - JL_WL_AUD CON0 整字写 ×2(0x602ea72 entry st.w.modimm=1 与 0x6071cbe
#   st.w.l=r10,DAC init 尾);app 全量 li 扫描读仅低功耗快照 0x6069a2e 一处
#   (不在本 boot 前缀),无 ready 位轮询;
# - DAC_CON 整字写(0x6071cbc,路 AS 勘误:并行对 0x6071cb8 `r1=r0+0x1D00`
#   ∥ `[r0+0]=r1` 的存储半,落盘=旧 r1=SR walk 值,实测 0x0;路 AG 的
#   「=0xfe2200 数值巧合」是并行对旧寄存器误读——0xfe2200 是 S2 写基址)
#   + RMW 对 0x6071ce6 &=~0x100/0x6071cee |=0x100(bit8 开/关);
# - AUD_CON RMW 0x6071cea &=~0x40(本路径无先前写→RMW 内部读合成 0);
# - JL_ASS CLK_CON 原子 RMW 0x6071cc6 |=1(音频子系统时钟位0,同样无先前写)。
# 条件族(esco mic 块 0xfe0a40/0xfe0924/0xfe0538、ANC 图门 0xfe0400、缺省
# 分支 0xfe0a00/0xfe0a0c/DAC_LEN 写族)本世界不可达,不进白名单、保持
# unmapped 诚实停。
JL_WL_AUD_CON0=0xfe2200
JL_AUDIO_DAC_CON=0xfe0500
JL_AUDIO_AUD_CON=0xfe0558
JL_AUDIO_ADC_CON=0xfe0538
JL_ASS_CLK_CON=0xfe0900
JL_AUDIO_DAC_INIT_WORDS=(JL_AUDIO_DAC_CON,JL_AUDIO_AUD_CON,JL_ASS_CLK_CON)
# JL_ADDA->DAA_CON1(br28.h `JL_ADDA_BASE=hs_base+map_adr(0x0a,0)`=0xfe0a00,
# +0x04 __RW,DAC 侧模拟增益字;+0x20 起才是路 AE 钉的 ADA_CON 族,布局互证)。
JL_ADDA_DAA_CON1=0xfe0a04
# JL_AUDIO->DAC_VL0(br28.h JL_AUDIO_TypeDef 全字段 __RW,csfr.h `#define __u16
# unsigned int`→32 位字槽,DAC_VL0@+0x1c=成员 7;路 BO 钉死四个消费者
# getter/setter/同步 walker/静音判零,全配置面消费=「配置类」分类依据)。
JL_AUDIO_DAC_VL0=0xfe051c
CORE_TICK_BASES=(0xffe0ec,0xffe8ec)
CPU1_CONTROL=0xffe8c0
VOLTAGE_CONFIG=(0xf2c110,0xf2c258,0xf2c26c,0xf2c270,0xff0000)
# P33 模拟控制族里 power_init 的 P33 配置函数(0x60677a4)真实读写的、又不被
# VOLTAGE_CONFIG 覆盖的两个寄存器:P3_ANA_CON2(0xf2c248=P33_ACCESS(0x92))与
# P3_ANA_CON5(0xf2c254=P33_ACCESS(0x95))。p33_sfr.h 只是地址宏、无 __RW/__RO/
# __WO 类型标注(与 p11_sfr.h 的 P11_LPTMR_TypeDef 不同),读回语义本身没有 SDK
# 证据;但同族 P3_ANA_CON6(0xf2c258)已在 VOLTAGE_CONFIG 里按「显式合成初始字
# 0 + 读回=当前寄存器值(后写值)」建模(clock_voltage_configuration 变体先例),
# 这两个寄存器按同一先例、同一显式合成初始字 0 处理,不臆造硅模拟状态。
P33_ANALOG_CONFIG=(0xf2c248,0xf2c254)
# 路 BV(sim-tailproj,2026-11-13):app_core 尾段 W1[0](S10 第一个函数
# 0x6012e3e..0x6013196)与 mode 切换函数 0x6068152 的 P33 模拟配置族,按
# AM 容器(P33_ANALOG_CONFIG/P3_ANA_CON4 联动 seed)同一先例一次打包的
# opt-in 扩展面。逐地址证据(指令锚定反汇编+全 app/ROM 字面量扫描,复核
# 修正路 BO 证据包 docs/appcore-tail-sfr-semantics.md 的两处基址误记,见
# 各类注释与该文档 §8 修正清单):
# - P3_ANA_CON0/7/8/9/10(=P33_ACCESS(0x90)/(0x97..0x9a),p33_sfr.h L130/
#   L137-140 有名、纯地址宏):mode 切换函数 0x6068152 内 r2=0xf2c240
#   (0x60681ce li)对 [r2+0x1c/0x20/0x24/0x28/0x0] 做显式 ld.w 读→掩码→
#   st.w 写回的配置 RMW(0x60681d4..0x606828a,读值真实参与写回值,与
#   P3_ANA_CON2/CON5/CON4 消费形状同族)+ 复合 and RMW(0x60681f2/0x60681fa
#   [r2+0x1c]&=0x7FB/&=0x3FD、0x6068254/0x6068270 [r2+0x28]&=0xFF7F/&=r4);
#   0x606827e..0x606828a 对 CON0 的 &0x3FD3|8(pair 尾 st.w)。条件侧
#   0x6067e16/0x6067e34 同族(ld/st &0x7FB|4 + rmw.and.r &=0xFF7)。BO 的
#   CON0/7/8/9/10 归属核对成立。
# - P3_CHG_CON0/CON1(=P33_ACCESS(0x1a)/(0x1b),p33_sfr.h L39/L40 有名):
#   **修正 BO「W1[0] 对 0xfd5f08/0C/10/14/20 五个主路径 RMW」**——0x6012e9c
#   的 li 字面量是 **0xfd5708**(0x57 组 PORTP,GPIO_BASES 已覆盖,固件
#   一位 RMW 走既有 gpio 投影),0xfd5f00 族全 app/ROM 字面量 0 处、基址
#   算术亦无命中,不建模。W1[0] 的 P33 面是 r5=0xf2c064(0x6012e6e li):
#   [r5+4]=P3_CHG_CON0 的复合 and RMW 0x6012e74(&=0x1FE)/0x6012e7c
#   (&=0x3FD)+ 0x60130b0/0x60130b8 显式 ld/st RMW + 0x60130fc 等整字写,
#   [r5+8]=P3_CHG_CON1 的 0x6013094/0x601309e/0x60130ea..0x601310e 显式
#   ld/st RMW;独立充电函数 0x6004298 同族(0x600429e [0xf2c068]&=0x1FE +
#   0x60042b2 [r5+8]|=0x8)。
# - P3_ANA_READ(=P33_ACCESS(0x19),p33_sfr.h L38):W1[0] 轮询读 0x6012f26
#   (循环1 首读 bit2 定 1/2 轮 delay)与位测试 0x60131c/0x6013122(bit1/
#   bit2 门控 20 轮充电检测循环的成败路径——成功创建充电去抖任务 0x6033F36,
#   超时写失败码 b[0x125BF4]=2);app 全量恰 5 处 li(0x601201a/0x6012b58/
#   0x6012e6e/0x6033f38/0x6035f54),全部只单 bit 消费 bit1/bit2,零写者。
#   路 CB 证据(pinned SDK commit 67328f4):charge_hw.h 给出逐位语义——
#   bit1=LVCMP_DET(ldoin vs vbat 比较器)、bit2=LDO5V_DET(ldoin 检测)、
#   bit0=CHARGE_FULL_FLAG;charge.c 注释钉阈值(bit1=1⟺ldoin>vbat、
#   bit2=1⟺ldoin>0.6V)。默认(无 fixture)读回显式钉住
#   p3_ana_read_not_modeled(RES 红线同款);opt-in
#   p3_ana_read_ldoin_low_fixture(路 CB)在「无外部供电(ldoin≈0V,电池
#   启动)」场景锚下常量服务 0(derived mask=0b110,其余位显式填零非推导,
#   详见 report 子键)。
P33_ANA_CON_TAIL=(0xf2c240,0xf2c25c,0xf2c260,0xf2c264,0xf2c268)
P3_ANA_READ=0xf2c064
P3_CHG_CON0=0xf2c068
P3_CHG_CON1=0xf2c06c
# 路 CB:P3_L5V_CON0/CON1(=P33_ACCESS(0xa1)/(0xa2),p33_sfr.h L149/L150 有名,
# charge_hw.h 位宏 L5V_LOAD_EN/L5V_IO_MODE/L5V_RES_DET_S_SEL)。W1[0] 以
# r5(0xf2c064)+0x220 计算寻址访问(0x6012fb8 add.s14.rri r0=r5+544 →
# 0x6012fc0 ld [r0+4]/0x6012fd6 ld [r0+0] 显式 ld/st 配置 RMW,值=配置表
# 字节真实计算,旧值真实参与写回)——字面量扫描只有 0x606928c 一处
# (AWKUP 中断分发 0x60691f4 域,D1 后才可达),主路径全靠基址算术,故
# BV 轮的字面量扫描没看见这对。AM 容器同族语义(合成初始字 0+读回=
# 当前值)。fixture 放行 P3_ANA_READ 首读后 449,751 @ 0x6012fbc 撞上。
P3_L5V_CON0=0xf2c284
P3_L5V_CON1=0xf2c288
# 路 CB:P3_CHG_CON2(=P33_ACCESS(0x1c),p33_sfr.h L41 有名;charge_hw.h 位宏
# CHARGE_DET_VOL(bits1-2 充电检测阈值选择)/CHARGE_DET_EN(bit0)/
# CHARGE_FULL_mA_SEL(bits4-6))。W1[0] 以 [r5+12] 计算寻址做显式 ld/st 配置
# RMW(0x6013094 ld [r5+12] → 0x6013096 &=0x7FFF → 0x6013098 |=r2 →
# 0x601309a 写回;正好是「使能充电检测比较器」的配置,与紧随其后的
# P3_ANA_READ bit1/bit2 轮询自洽)。449,799 @ 0x6013090 撞上,AM 容器同族。
P3_CHG_CON2=0xf2c070
P33_TAIL_ANALOG_CONFIG=P33_ANA_CON_TAIL+(P3_CHG_CON0,P3_CHG_CON1,P3_CHG_CON2,
                                         P3_L5V_CON0,P3_L5V_CON1)
PLL1_CON3=0xfd470c
CACHE_STATUS_REGISTERS=(0xff0500,0xffe400,0xffec00)
ICU_BASES=(0xffe400,0xffec00)
# ICU region 组:两核各 +0x14(begin)/+0x18(end-1) 数据寄存器与 +0x10 命令口。
# ROM 0xc726 按 len>=0x1000 在两个 region 命令字里二选一;完成等待复用 CON bit30
# (0xbac0 轮询),与已建模的 0xca000010(ICU 使能路径 0xbae0)同族。
ICU_REGION_OFFSETS=(0x14,0x18)
ICU_REGION_COMMANDS=(0xca000020,0xca000040)
# SDK csfr.h:JL_L1P(0xff0500=csfr_base+0x500,共享 L1P cache)类型化为
# JL_TypeDef_q32DSP_DCU,其 CMO[32](+0x80..+0xff,逐地址 cache 维护口)标注
# __WO 只写。0xff05c8/0xff05cc=CMO[18]/CMO[19]:逐 index 操作语义 SDK 未命名,
# 由 ROM export wrapper 区分(flush_dcache 用 0xff05c8、flushinv_dcache 用 0xff05cc)。
DCU_CMO_FLUSH=0xff05c8
DCU_CMO_FLUSHINV=0xff05cc
DCU_CMO_PORTS=(DCU_CMO_FLUSH,DCU_CMO_FLUSHINV)
# JL_L1P(0xff0500)CON 本身:bit31=空闲,DcuWaitIdle(0x1fdb8->0xbb20)轮询位。
# 与 CMO 口(+0x80..+0xff)不同,CON 是读侧状态字;DcuInitial(0x1fdc0->0xbb30)
# 会写 bit0(使能),但 p11_ready_watchdog 支线前缀未调用 DcuInitial。
L1P_CON=0xff0500
CORE_PMU_CON1=(0xffe0c4,0xffe8c4)
SFC_BASE=0xfe0200
SFC_SECONDARY_BASE=0xfe0100
SFC_CLOCK_GATE=0xfd1c00  # RMW'd by the SFC suspend tail; register name not in the reference SDK headers
CRC_ENGINE=0xfd3500  # 硬件 CRC 引擎;寄存器名不在 reference SDK 头文件,语义由 ROM 0xaa1e 与 app 0x6000f2c 的真实用法钉死
SDTAP_CON=0xff0100
PSRAM_CON0=0xfe0300
IOMAP_CON0=0xfd6014
USB_IO_CON0=0xfd6000
USB_DISABLE_REGISTERS=(0xfd1800,0xfd1804)
PORT_INPUT_REGISTERS=(SDTAP_CON,PSRAM_CON0)
P11_PORT_DIGITAL=(0xf2b21c,0xf2b220)  # PB_DIE / PB_DIEH, initialized by p11_io_init
DEBUG_BANK_STRIDE=0x800
EMU_CON=0xffe200
DEBUG_OFFSETS=(0x1c0,0x200,0x208,0x20c,0x210,0x214,0x218,0x21c,0x220,0x224,0x228,0x22c)


class DebugExecutionProjection:
    """Actual configuration shadows plus explicit half-open PC-range policy.

    Not silicon endpoint/alignment certification, MPU or exception delivery.
    Never manufacture status, inherit CPU0 state into CPU1, or check data reads.
    """
    def __init__(self,initial_controls=None):
        if initial_controls is not None and (len(initial_controls)!=2 or any(v not in (None,0) for v in initial_controls)):
            raise ValueError('need two unknown or explicitly disabled initial debug controls')
        self.initial_controls=tuple(initial_controls or (None,None));self.registers={}
        for core,value in enumerate(self.initial_controls):
            if value is not None:self.registers[EMU_CON+core*DEBUG_BANK_STRIDE]=value
        self.checks=0;self.violation=None

    def handles(self,address):
        return any(address==0xffe000+core*DEBUG_BANK_STRIDE+offset for core in (0,1) for offset in DEBUG_OFFSETS)

    def require_live(self):
        if self.violation is not None:raise Blocked('pc_guard_violation_requires_delivery')

    def read(self,address):
        self.require_live()
        if address not in self.registers:raise Blocked('unknown_debug_configuration',address=hex(address))
        return self.registers[address]

    def write(self,address,value):
        self.require_live()
        offset=(address-0xffe000)%DEBUG_BANK_STRIDE
        if offset==0x200 and value not in (0,0xe0000007,0xe000000f,0xe000001f):
            raise Blocked('debug_control_mode_not_modeled',value=hex(value))
        if offset==0x1c0 and value!=1:raise Blocked('etm_control_mode_not_modeled',value=hex(value))
        self.registers[address]=value

    def check_fetch(self,pc,size,core=0):
        self.require_live()
        if core not in (0,1) or size<1:raise ValueError('invalid execution guard request')
        self.checks+=1;base=0xffe000+core*DEBUG_BANK_STRIDE
        if base+0x200 not in self.registers:raise Blocked('unknown_initial_pc_guard',core=core)
        if not self.registers[base+0x200]&16:return
        if 0x400000<=pc<0x800000:raise Blocked('pc_guard_virtual_comparison_unverified',pc=hex(pc))
        ranges=[]
        for index in range(3):
            high=base+0x218+8*index;low=high+4
            if low not in self.registers or high not in self.registers:
                raise Blocked('pc_guard_range_unknown',core=core,index=index)
            begin,end=self.registers[low],self.registers[high]
            if end<begin:raise Blocked('invalid_pc_guard_range',core=core,index=index)
            ranges.append((begin,end))
        if not any(begin<=pc and pc+size<=end for begin,end in ranges):
            self.violation={'core':core,'pc':hex(pc),'size':size,
                            'ranges':[[hex(a),hex(b)] for a,b in ranges]}
            raise Blocked('projected_pc_range_violation',**self.violation)

    def report(self):
        return {'registers':{hex(a):hex(v) for a,v in sorted(self.registers.items())},
                'initial_control_fixtures':self.initial_controls,'checked_executing_fetches':self.checks,
                'violation':self.violation,'policy':'union of three half-open numeric PC ranges; full instruction extent',
                'endpoint_alignment_readback_silicon_verified':False,'virtual_pc_comparison_modeled':False,
                'stack_limit_or_mpu_enforced':False,'exception_status_entry_reset_delivery_modeled':False,
                'other_emu_etm_effects_modeled':False,'cpu_banks_independent':True}


class MPUConfigurationProjection:
    """Scoped eight-slot configuration and explicit E7 write-gate projection.

    Opposite polling helpers both write E7, supporting a toggle protocol.
    Readback upper bits/ack latency and actual access enforcement are unverified.
    """
    WREN=0xff03fc

    def __init__(self,initial_wren=None):
        if initial_wren not in (None,0,1):raise ValueError('MPU gate must be unknown or explicit0/1')
        self.initial_wren=initial_wren;self.wren=initial_wren;self.registers={};self.key_writes=0

    def handles(self,address):
        return address==self.WREN or any(base<=address<base+32 and address%4==0
                                        for base in (0xff0300,0xff0340,0xff0380,0xff03c0))

    def read(self,address):
        if address==self.WREN:
            if self.wren is None:raise Blocked('unknown_initial_mpu_write_gate')
            return self.wren
        if address not in self.registers:raise Blocked('unknown_mpu_slot_configuration',address=hex(address))
        return self.registers[address]

    def write(self,address,value):
        if address==self.WREN:
            if value!=0xe7:raise Blocked('mpu_write_gate_command_not_modeled',value=hex(value))
            if self.wren is None:raise Blocked('unknown_initial_mpu_write_gate')
            self.wren^=1;self.key_writes+=1
        else:
            if self.wren!=1:raise Blocked('mpu_configuration_requires_known_open_write_gate')
            self.registers[address]=value

    def report(self):
        return {'registers':{hex(a):hex(v) for a,v in sorted(self.registers.items())},
                'write_gate':self.wren,'initial_write_gate_fixture':self.initial_wren,'key_writes':self.key_writes,
                'gate_projection':'E7 toggles bit0; upper readback bits0; synchronous acknowledgment',
                'write_gate_latency_readback_silicon_verified':False,'cpu_dma_access_permissions_enforced':False,
                'unknown_unwritten_slots_preserved':True}


SFC_WINDOW_OFFSETS=(0x18,0x20,0x24)  # VM flash 窗口组:SDK 头文件无名,语义由 app 反汇编钉死(见 SFCReadBus)
SFC_PRIMARY_WINDOW_ADDRESSES=(SFC_BASE,SFC_BASE+4)+tuple(SFC_BASE+o for o in SFC_WINDOW_OFFSETS)
SFC_SECONDARY_WINDOW_ADDRESSES=(SFC_SECONDARY_BASE,SFC_SECONDARY_BASE+4)+tuple(SFC_SECONDARY_BASE+o for o in SFC_WINDOW_OFFSETS)


class SFCReadBus:
    """Functional decoded-Flash read bus, with real enable/configuration gates.

    Transactions are synchronous. No SPI waveform/cache coherence/timing claim.

    窗口组(+0x18/+0x20/+0x24)是 VM 区 flash 窗口配置,write-only 记录:
    app 全量反汇编只有两个引用点,ROM 零引用(reference SDK 头文件亦无该块命名)——
      - 0x600a36e 起的 VM 窗口设置序列(r1=0xfe0218):[r1+12]=0x061d7fe0
        (=0x06000000+vm_saddr-0x2020,call 0x12bdc6 算出的 VM 区 flash 窗口地址)、
        [r1+8]=0x7ffffff、[r1+0]|=2(唯一一处 +0x18 访问,只置 bit1);
      - SFC 上下文恢复函数 0x60c5bc0 尾部(r1=0xfe0220):[r1+4]=call 0x60c55a0 结果、
        [r1+0]=0x7ffffff,不重写 +0x18(与「bit1 是窗口使能位」自洽但未硅验证)。
    两侧都没有任何读回,复位值/读回语义无观察——读一律停止,写只记录。
    +0x20 的两次观测值都是 0x7ffffff(=flash 直访区顶 0x08000000-1),像窗口
    界限/掩码;但 CPU 在窗口配置前就大量直访 0x06000000..0x08000000(CON bit0
    使能即可),所以窗口组不建模为 flash 访问门,check_flash_access 判定不变。
    """
    def __init__(self,initial_con=None,base=SFC_BASE):
        if initial_con is not None and (not isinstance(initial_con,int) or initial_con&~0xf00001):
            raise ValueError('unsupported initial SFC mode')
        if base not in (SFC_BASE,SFC_SECONDARY_BASE):raise ValueError('unsupported SFC base')
        self.initial_con=initial_con;self.con=initial_con;self.baud=None;self.writes=0;self.base=base
        # 窗口组:+0x18 只观测过 rmw.or(置位,不经过读回);+0x20/+0x24 只观测过整字写
        self.window_control=PartialWord();self.window_control_operations=[]
        self.window_limit=None;self.window_limit_writes=0
        self.window_base=None;self.window_base_writes=0

    def check_flash_access(self,address):
        if not 0x06000000<=address<0x08000000:return
        if self.con is None:raise Blocked('unknown_sfc_enable_state')
        if not self.con&1:raise Blocked('sfc_disabled_flash_access',address=hex(address))

    def window_rmw(self,operand):
        # 唯一观测形状:0x600a37e `rmw.or.modimm [0xfe0218]|=2`。RMW 不经过读回,
        # 按位记录置位;未写位保持 unknown,任何整字消费仍走严格路径。
        self.window_control.apply('or',operand)
        self.window_control_operations.append(('or',operand&0xffffffff))

    def read(self,address):
        if address-self.base in SFC_WINDOW_OFFSETS:
            # 固件从不读窗口组(app/ROM 全量反汇编),读回语义无证据,一律诚实停。
            raise Blocked('sfc_window_readback_not_observed',address=hex(address))
        value=self.con if address==self.base else self.baud
        if value is None:raise Blocked('unknown_sfc_configuration',address=hex(address))
        return value  # bit31 busy=0: no queued transaction in this synchronous model

    def write(self,address,value):
        offset=address-self.base
        if offset in SFC_WINDOW_OFFSETS:
            if offset==0x18:
                # 整字写无观测(固件只做 |=2 置位),形状外一律拒绝
                raise Blocked('sfc_window_control_full_store_not_modeled',address=hex(address),value=hex(value))
            if offset==0x20:self.window_limit=value;self.window_limit_writes+=1
            else:self.window_base=value;self.window_base_writes+=1
        elif address==self.base:
            if value&~0xf00001:raise Blocked('sfc_mode_not_modeled',value=hex(value))
            self.con=value
        elif address==self.base+4:
            if self.con is None:raise Blocked('unknown_sfc_enable_state')
            # The suspend path (0x12b890/0x12b970) writes baud=0 while enabled
            # to stop the clock and only then clears CON bit0; a nonzero rate
            # change while enabled stays an unverified sequence.
            if self.con&1 and value!=0:raise Blocked('sfc_baud_change_requires_observed_disable')
            if value>255:raise Blocked('unsupported_sfc_baud',value=value)
            self.baud=value
        else:raise Blocked('sfc_register_not_modeled',address=hex(address))
        self.writes+=1

    def report(self):
        return {'base':hex(self.base),'initial_control_fixture':self.initial_con,'con':self.con,'baud':self.baud,'writes':self.writes,
                'busy_projection':'zero outstanding transactions; synchronous decoded-Flash memory',
                'flash_fetch_and_data_access_require_enable':True,'spi_or_cache_timing_simulated':False,
                'window_registers':{'control_address':hex(self.base+0x18),
                    'control_known_value':hex(self.window_control.value),
                    'control_known_mask':hex(self.window_control.known),
                    'control_operations':[{'kind':kind,'operand':hex(operand)}
                                          for kind,operand in self.window_control_operations],
                    'limit_address':hex(self.base+0x20),
                    'limit':None if self.window_limit is None else hex(self.window_limit),
                    'limit_writes':self.window_limit_writes,
                    'base_address':hex(self.base+0x24),
                    'base':None if self.window_base is None else hex(self.window_base),
                    'base_writes':self.window_base_writes,
                    'write_only':True,'readback_observed':False,'bit_semantics_verified':False,
                    'evidence':('app 0x600a36e..0x600a37e (r1=0xfe0218: [+12]=VM flash window 0x061d7fe0, '
                        '[+8]=0x7ffffff, [0]|=2) and the SFC context-restore tail 0x60c5bfa..0x60c5c0a '
                        '(r1=0xfe0220: [+4]=window calc, [0]=0x7ffffff, control not rewritten); '
                        'the ROM references +0x18/+0x20/+0x24 nowhere; reference SDK headers name none of them'),
                    'flash_access_gate_unchanged':('assumption: the 0x06000000..0x08000000 direct-access gate stays '
                        'CON bit0 only; the firmware fetches/reads flash through that window long before these '
                        'registers are configured, so they are not modeled as an access gate')}}


class SfcClockGateProjection:
    """RMW projection for 0xfd1c00 plus the +4 select record and +8 divider read-back.

    The SFC suspend tail (0x12b910) sets bit0; the resume helper 0x12ba4c RMWs
    bit3 (|=8) and bit12 (&=~0x1000), full-stores the divider value into
    [0xfd1c08] at 0x12ba5c, then polls [0xfd1c00] bit15 at 0x12ba38..0x12ba46.
    bit15 read-back is an explicit PLL-locked condition projection (not a
    measured reset value); the +8 read-back returns only the last
    firmware-written divider, never a fabricated reset value.

    0xfd1c04(+4) 只写不读:app 全部 11 处 0xfd1c00 块访问里仅 0x6010b88
    `st.w [r0+4]=3`(资源表扫描后的一次性 SFC 时钟重配,0x12762c 标志守卫),
    ROM 仅 0xc198 `st.w [r5+4]=uextra(config,p:8,l:8)`(flash 配置字 bits[8:16]
    整字装载,前置 [0xfd1c00]=0x20 整字存)。两侧都没有任何读回,复位值未知、
    读回语义无证据——读一律停止,写只记录最后值。reference SDK 头文件无该块
    定义,写 3 的位含义(时钟源选择/分频字段)未证实。
    """
    PLL_LOCK_BIT=0x8000

    def __init__(self):
        self.bits=PartialWord();self.operations=[]
        self.select=None;self.select_writes=0
        self.divider=None;self.divider_writes=0;self.reads=0

    def rmw(self,kind,operand):
        if kind not in ('or','and'):raise Blocked('sfc_clock_gate_rmw_form_not_modeled',operation=kind)
        self.bits.apply(kind,operand)
        self.operations.append((kind,operand&0xffffffff))

    def read(self,address):
        if address==SFC_CLOCK_GATE:
            self.reads+=1
            # 0x12ba38 轮询 [0xfd1c00] 的 bit15，等它置位后再 |=0x4000 返回。
            # 该位读回为 1 是显式「PLL 已锁定」条件投影，不是测得复位值。
            # 其余位沿用已记录 RMW 的已知值；本路径轮询只消费 bit15，未写入位不臆造。
            return (self.bits.value|self.PLL_LOCK_BIT)&0xffffffff
        if address==SFC_CLOCK_GATE+4:
            # 固件从不读 +4(app/ROM 全量反汇编证据),读回语义无观察,一律诚实停。
            raise Blocked('sfc_clock_select_readback_not_observed',address=hex(address))
        if address==SFC_CLOCK_GATE+8:
            if self.divider is None:raise Blocked('unknown_sfc_clock_divider',address=hex(address))
            return self.divider
        raise Blocked('sfc_clock_register_read_not_modeled',address=hex(address))

    def write_select(self,value):
        self.select=value&0xffffffff;self.select_writes+=1

    def write_divider(self,value):
        self.divider=value&0xffffffff;self.divider_writes+=1

    def report(self):
        return {'known_value':hex(self.bits.value),'known_mask':hex(self.bits.known),
                'operations':[{'kind':k,'operand':hex(v)} for k,v in self.operations],
                'address':hex(SFC_CLOCK_GATE),'identity':'register name not in reference SDK headers;待确认',
                'readback_modeled':True,
                'readback_projection':'bit15 PLL-lock condition projected as 1 (0x12ba38 poll); not a measured reset value',
                'select_address':hex(SFC_CLOCK_GATE+4),
                'select':None if self.select is None else hex(self.select),
                'select_write_only':True,'select_writes':self.select_writes,
                'select_readback_observed':False,
                'select_bit_semantics_verified':False,
                'select_evidence':('app 0x6010b88 st.w [0xfd1c04]=3 (one-time SFC clock reconfig); '
                    'ROM 0xc198 st.w [0xfd1c04]=uextra(flash config,p:8,l:8); no read-back anywhere'),
                'divider_address':hex(SFC_CLOCK_GATE+8),
                'divider':None if self.divider is None else hex(self.divider),
                'divider_readback_is_last_write':True,'divider_writes':self.divider_writes,
                'reads':self.reads,'reset_value_measured':False}


class HardwareCrcEngine:
    """opt-in 硬件 CRC 引擎功能等价投影(0xfd3500 块)。

    访问形状只接受固件真实用过的两种,ROM 0xaa1e(USB transform)与 app 0x6000f2c
    (CRC wrapper 硬件路径)用法一致:写 +4 装载种子(st.w,载荷<=16 位)、写 +0 馈入
    一个数据单位(st.w,载荷<=0xff,即一个字节)、读 +4 取当前 CRC。固件在写完数据后
    csync 再读结果(ROM 0xaa46、app 0x6000f50),同步模型无需 busy 位。复位状态
    未知:未写种子就读或馈入一律停止,不臆造复位种子。

    算法参数按 app 的软件 CRC-16 半字节表实现推导(表在 app 0x060a860c,16 项,
    entry[i]=i*0x1021,GF(2) 乘法):多项式 0x1021(x^16+x^12+x^5+1)、MSB-first 左移、
    初值=所写种子、无输出异或/反射。app wrapper 0x6000f0c 在软件/硬件两条路径间
    选择且共用同一初值 0(软件路径 r2=0 ↔ 硬件路径种子 0),固件设计意图即两者功能
    等价;本投影按软件参数实现,硬件位序/字节序若有差异以软件等价为准,silicon 未验证。
    """

    POLYNOMIAL=0x1021

    def __init__(self):
        self.crc=None;self.seed_writes=[];self.feeds=0;self.result_reads=[]

    def handles(self,address):
        return address in (CRC_ENGINE,CRC_ENGINE+4)

    def _feed(self,crc,byte):
        crc^=byte<<8
        for _ in range(8):
            crc=((crc<<1)^self.POLYNOMIAL)&0xffff if crc&0x8000 else (crc<<1)&0xffff
        return crc

    def read(self,address):
        if address==CRC_ENGINE:raise Blocked('crc_data_port_read_not_modeled',address=hex(address))
        if self.crc is None:raise Blocked('unknown_crc_engine_reset_state',address=hex(address))
        self.result_reads.append(self.crc)
        return self.crc

    def write(self,address,value):
        if address==CRC_ENGINE+4:
            if value>0xffff:raise Blocked('crc_seed_width_not_modeled',value=hex(value))
            self.crc=value;self.seed_writes.append(value)
            if len(self.seed_writes)>32:self.seed_writes.pop(16)
            return
        if address!=CRC_ENGINE:raise Blocked('crc_register_not_modeled',address=hex(address))
        if value>0xff:raise Blocked('crc_feed_width_not_modeled',value=hex(value))
        if self.crc is None:raise Blocked('crc_feed_before_seed_unknown')
        self.crc=self._feed(self.crc,value);self.feeds+=1

    def report(self):
        return {'address':hex(CRC_ENGINE),
                'algorithm':{'polynomial':hex(self.POLYNOMIAL),
                    'bit_order':'MSB-first (left shift)','initial_value':'the written seed',
                    'xor_out':None,'reflected':False},
                'algorithm_evidence':('app software CRC-16 nibble table at 0x060a860c (16 entries, '
                    'entry[i]=i*0x1021 extracted from app bytes); wrapper 0x6000f0c selects between '
                    'software and hardware paths with the shared initial value 0'),
                'access_shapes':{'0xfd3500':'write one byte (st.w payload <=0xff) feeds one unit',
                                 '0xfd3504':'write seed (<=0xffff); read returns the current CRC'},
                'seed_writes':[hex(v) for v in self.seed_writes],'fed_bytes':self.feeds,
                'result_reads':[hex(v) for v in self.result_reads],
                'reset_value_measured':False,
                'hardware_bit_or_byte_order_silicon_verified':False,
                'synchronous_no_busy_bit':'firmware csyncs after the last feed (ROM 0xaa46, app 0x6000f50) before reading +4',
                'functional_equivalence_projection':('results follow the app software CRC-16 table; if the hardware '
                    'engine differs in bit/byte order the software equivalence is the contract, silicon unverified')}


class IcuInitializationProjection:
    """Only observed command4 and enable bit; explicit global-step completion.

    Bit30 is command busy. Bit31 stays an independent external status fixture.
    No cache contents, physical latency, instruction-cache coherence or DMA.

    region_projection(opt-in,默认关):两核 ICU 的 region 寄存器组 +0x14/+0x18。
    ROM 0xc726 把 VM flash 窗口配进两核 ICU(每核 cli→testset 0x19fd78 抢锁→
    [+0x14]=begin、[+0x18]=end-1、[+0x10]=命令字→call 0xbac0 等 CON bit30 完成→
    call 0xbad4 释放锁→sti,r3+=0x800 换核循环 2 次)。命令字 0xca000020/0xca000040
    按 len>=0x1000 二选一(0xc72c if.ge + 谓词 li 对),与既有 0xca000010 同走
    +0x10 命令口、同用 CON bit30 完成等待(0xbaca 轮询锚点与 0xbae0 使能路径同一
    条指令),是同族命令。+0x14/+0x18 数据寄存器固件只写不读(ROM 该序列与 app
    全量扫描均无读回),write-only 记录;命令低字节的语义(0x10/0x20/0x40 对应
    SDK cache.h Icu*Region 家族的哪个操作)无命名证据,不臆造。
    """
    def __init__(self,initial_words=None,command_steps=None,region_projection=False):
        if command_steps is not None and (not isinstance(command_steps,int) or not 0<=command_steps<=10000):
            raise ValueError('invalid explicit ICU command step count')
        self.initial=dict(initial_words or {});self.control={b:self.initial.get(b) for b in ICU_BASES}
        if any(v is not None and (not isinstance(v,int) or v&~0xc0000001) for v in self.control.values()):
            raise ValueError('ICU initial configuration outside scoped bits')
        self.region_projection=region_projection
        self.region={b:{offset:None for offset in ICU_REGION_OFFSETS} for b in ICU_BASES}
        self.region_writes={b:{offset:0 for offset in ICU_REGION_OFFSETS} for b in ICU_BASES}
        self.command_steps=command_steps;self.pending={};self.commands=[];self.completed=0;self.busy_reads=0

    def handles(self,address):
        offsets=(0,0x10)+ICU_REGION_OFFSETS if self.region_projection else (0,0x10)
        return any(address-b in offsets for b in ICU_BASES)

    def base(self,address):return ICU_BASES[0] if address<ICU_BASES[1] else ICU_BASES[1]

    def peek(self,address):
        base=self.base(address)
        if address-base in ICU_REGION_OFFSETS:
            raise Blocked('icu_region_readback_not_modeled',address=hex(address))
        if address!=base:raise Blocked('icu_command_readback_not_modeled',address=hex(address))
        if self.control[base] is None:raise Blocked('unknown_initial_icu_control',address=hex(address))
        return self.control[base]|(0x40000000 if base in self.pending else 0)

    def read(self,address):
        value=self.peek(address)
        if value&0x40000000:self.busy_reads+=1
        return value

    def write(self,address,value):
        base=self.base(address)
        offset=address-base
        if offset in ICU_REGION_OFFSETS:
            if not self.region_projection:raise Blocked('icu_region_write_not_modeled',address=hex(address))
            self.region[base][offset]=value;self.region_writes[base][offset]+=1
        elif address==base+16:
            allowed=(0xca000010,)+ICU_REGION_COMMANDS if self.region_projection else (0xca000010,)
            if value not in allowed:raise Blocked('icu_command_not_modeled',value=hex(value))
            if self.control[base] is None:raise Blocked('unknown_initial_icu_control',address=hex(base))
            if self.peek(base)&0x40000000:raise Blocked('icu_prior_command_state_unresolved',address=hex(base))
            if self.command_steps is None:raise Blocked('icu_command_progress_unknown',address=hex(address))
            self.pending[base]=self.command_steps
            self.commands.append({'address':hex(address),'value':hex(value)})
        else:
            old=self.peek(base)
            if base in self.pending:raise Blocked('icu_enable_before_command_completed')
            if value!=(old|1):raise Blocked('icu_control_write_not_modeled',address=hex(address),value=hex(value))
            self.control[base]=old|1

    def advance(self):
        if self.command_steps==0:return  # explicit stopped completion clock
        for base in tuple(self.pending):
            self.pending[base]-=1
            if self.pending[base]<=0:del self.pending[base];self.completed+=1

    def report(self):
        result={'initial_word_fixtures':{hex(a):hex(v) for a,v in self.initial.items()},
                'con':{hex(a):None if v is None else hex(v) for a,v in self.control.items()},
                'command_steps_fixture':self.command_steps,'pending_cores':[ICU_BASES.index(b) for b in self.pending],
                'commands':self.commands,'completed_commands':self.completed,'busy_reads':self.busy_reads,
                'bit31_is_independent_external_status':True,'physical_latency_or_command4_semantics_certified':False,
                'cache_contents_or_coherence_simulated':False}
        if self.region_projection:
            result['region_projection']={
                'registers':{hex(b):{hex(o):{'value':None if self.region[b][o] is None else hex(self.region[b][o]),
                                              'writes':self.region_writes[b][o]} for o in ICU_REGION_OFFSETS}
                             for b in ICU_BASES},
                'commands_accepted':[hex(v) for v in ICU_REGION_COMMANDS],
                'observed_sequence':('ROM 0xc726 (export sfc_drop_cache, SDK cache.h (beg,len) family): '
                                     'per core cli, testset 0x19fd78 lock, [+0x14]=begin, [+0x18]=end-1, '
                                     '[+0x10]=0xca000020/0x40 chosen by the if.ge len>=0x1000 predicated '
                                     'li pair (observed run: len=0x20 selects 0xca000040, other branch '
                                     'untested), call 0xbac0 polls CON bit30 (ld.w.idxs at 0xbaca, same '
                                     'poll as the 0xca000010 enable path), call 0xbad4 releases the lock'),
                'write_only':True,'readback_observed':False,
                'command_low_byte_semantics_verified':False,
                'same_busy_machinery_as_command4':True}
        return result


class DcuCmoProjection:
    """共享 L1P(JL_L1P,0xff0500)的 CMO 逐地址 cache 维护口 write-only 记录。

    SDK csfr.h 把 JL_L1P 类型化为 JL_TypeDef_q32DSP_DCU:CON(+0,bit31=空闲,
    DcuWaitIdle 0xbb20 轮询、cache.h WAIT_DCACHE_IDLE 同位)之外,CMO[32]
    (+0x80..+0xff)标注 __WO 只写;cache.h 只声明 flush_dcache/flushinv_dcache
    两个函数。ROM export 映射:flush_dcache 0x1fdac→0xc780(r2=0xff05c8)、
    flushinv_dcache 0x1fdb0→0xc78c→0xc71a(r2=0xff05cc),二者都调同一循环
    0xc6f4:按 (beg,len) 折算 32 字节行数(寄存器计数 rep),体=add.rrr.p
    (r0+=32)+st.w [r2]=旧 r0 并行对,每行把行地址写一次口。固件全量字节扫描:
    两个口各只有 ROM wrapper 一处 li.u32 引用,无任何读回。模型只记录写入
    序列,不模拟 cache 内容/一致性;读回一律拒绝。哪个 index 是 flush、哪个
    是 flush+失效只有 wrapper 名字映射证据,行粒度(32 字节)按循环步长推断,
    硅上未验证。
    """
    def __init__(self):
        self.values={address:[] for address in DCU_CMO_PORTS}
        self.writes={address:0 for address in DCU_CMO_PORTS}
    def handles(self,address):
        return address in self.values
    def write(self,address,value):
        values=self.values[address];values.append(value);self.writes[address]+=1
        if len(values)>64:values.pop(32)  # 同 record_write:保留首 32+末 32
    def read(self,address):
        raise Blocked('dcu_cmo_readback_not_modeled',address=hex(address))
    def report(self):
        return {'ports':{hex(address):{'values':[hex(v) for v in values],'writes':self.writes[address]}
                         for address,values in self.values.items()},
                'values_truncated':any(self.writes[a]>len(v) for a,v in self.values.items()),
                'sdk_csfr_h_typing':'JL_L1P (0xff0500) typed JL_TypeDef_q32DSP_DCU; CMO[32] at +0x80 marked __WO',
                'port_index_semantics_verified':False,
                'line_stride_32_from_loop_body':True,'line_granularity_silicon_verified':False,
                'write_only':True,'readback_observed':False,
                'cache_contents_or_coherence_simulated':False}


class L1PIdleProjection:
    """JL_L1P CON (0xff0500, shared L1P cache) idle-bit read fixture.

    The only access reached in the p11_ready_watchdog branch line is the ROM
    DcuWaitIdle (0x1fdb8 -> 0xbb20) poll: csync, li r0=0xff0500, ld.w r1=[r0],
    bgts.ri r1,-1 (loop back while bit31==0, exit once bit31==1). No cache
    maintenance (DCU CMO write) or DcuInitial CON write precedes it in that
    prefix, so the idle bit is the natural no-operation-in-flight read, NOT a
    maintenance-completion claim. The word is an explicit synthesized fixture:
    bit31=1 (idle) is the only bit the poll consumes; the other 31 bits are
    supplied as 0 for the numeric comparison to proceed but are NOT claimed as
    silicon values (silicon reset value and bit semantics unverified). Writes
    to CON are not observed in this prefix and stay blocked.
    """
    def __init__(self, idle_word=0x80000000):
        if not isinstance(idle_word, int) or not 0 <= idle_word <= 0xffffffff:
            raise ValueError('JL_L1P CON idle fixture must be unsigned32')
        self.idle_word = idle_word

    def handles(self, address):
        return address == L1P_CON

    def read(self, address):
        return self.idle_word

    def write(self, address, value):
        raise Blocked('l1p_con_write_not_modeled', address=hex(address), value=hex(value))

    def report(self):
        return {'idle_word_fixture': hex(self.idle_word),
                'bit31_idle_polled_by': 'ROM DcuWaitIdle 0x1fdb8 -> 0xbb20',
                'idle_is_no_operation_in_flight_not_maintenance_completion': True,
                'other_bits_claimed_as_silicon': False,
                'silicon_reset_value_verified': False,
                'con_write_modeled': False,
                'cache_contents_or_coherence_simulated': False}


class IcuIdleProjection:
    """Two-core ICU CON (0xffe400/0xffec00) idle-bit read fixture.

    The only access reached in the p11_ready_watchdog branch line is the ROM
    IcuWaitIdle (0x1fdb4 -> 0xc792) poll: entry li r0=0 (core), li r1=0x3ff900
    (=0xffe400>>2), goto the core-advance check, then per core csync,
    r2 = r0<<11, ld.w.idxs r3=[r2+r1<<2] (address = 0xffe400 + core<<11),
    bgts.ri r3,-1 (loop back on the same core while bit31==0, advance once
    bit31==1), rts once both cores read idle. SDK cache.h pins bit31 as the
    icache-idle bit: WAIT_ICACHE_IDLE = csync; while(!(q32DSP_icu(core_num())
    ->CON & BIT(31))). bit30 (command busy) is NOT SDK-named; it is inferred
    from the ROM command wait helper 0xbac0 (if (r2 & 0x40000000)!=0 goto -8)
    shared by IcuInitial (0xbae0) and sfc_drop_cache (0xc726), and is
    deliberately NOT modeled here -- this projection only serves the bit31
    idle poll, leaving command busy / completion timing / enable-bit writes to
    honest stops. The word is an explicit synthesized fixture: bit31=1 (idle)
    is the only bit the poll consumes; the other 31 bits are supplied as 0 for
    the numeric comparison to proceed but are NOT claimed as silicon values
    (silicon reset value and bit semantics unverified). CON writes are not
    observed in this prefix and stay blocked.
    """
    def __init__(self, idle_word=0x80000000):
        if not isinstance(idle_word, int) or not 0 <= idle_word <= 0xffffffff:
            raise ValueError('ICU CON idle fixture must be unsigned32')
        self.idle_word = idle_word

    def handles(self, address):
        return address in ICU_BASES

    def read(self, address):
        return self.idle_word

    def write(self, address, value):
        raise Blocked('icu_con_write_not_modeled', address=hex(address), value=hex(value))

    def report(self):
        return {'idle_word_fixture': hex(self.idle_word),
                'bit31_idle_polled_by': 'ROM IcuWaitIdle 0x1fdb4 -> 0xc792 (both cores)',
                'sdk_bit31_evidence': 'cache.h WAIT_ICACHE_IDLE: csync; while(!(q32DSP_icu(core_num())->CON & BIT(31)))',
                'bit30_command_busy_not_modeled': True,
                'idle_is_no_operation_in_flight_not_maintenance_completion': True,
                'other_bits_claimed_as_silicon': False,
                'silicon_reset_value_verified': False,
                'con_write_modeled': False,
                'cache_contents_or_coherence_simulated': False}


class CoreTickStopped:
    """Optional nominal counter; real IRQ entry and pending readback stay strict."""
    def __init__(self,initial_con=None,progress=None,prd_readback=False,cnt_held_snapshot=False,
                 freerun_edge_period=False):
        if initial_con not in (None,0):raise ValueError('only unknown or explicitly disabled core tick supported')
        if progress is not None and (not isinstance(progress,int) or not 0<=progress<=256):raise ValueError('invalid explicit core tick progress')
        self.initial_con=initial_con
        self.con=initial_con;self.cnt=self.prd=None;self.pending=None if initial_con is None else False;self.writes=0
        self.progress=progress;self.expirations=0
        self.unresolved_overflow=False;self.acknowledgements=0
        # 路 BF:交付请求在途标志(抛 IrqDeliveryRequest 至 handler ACK 之间
        # 不重复抛;Timer0.delivery_in_flight 同旨)。ACK(rmw |=0x40)清 pending
        # 时一并解除,计数器随之恢复推进。
        self.delivery_in_flight=False
        # 路 BQ(一案 core_tick_prd_readback):PRD 最近真实写入记录——读回
        # 投影的证据链。prd_write 只在 write(offset==8) 更新;运行中重编程
        # 本就被 active_core_tick_reconfiguration_unverified 挡住,故本模型里
        # prd_write 与 prd 恒等(单测钉死)。缺失(None)时读侧继续严格停,
        # 不给默认值;整字写不存在部分未知形状(显式声明,非放松)。
        self.prd_readback=prd_readback;self.prd_write=None
        # 路 B3(S19 批 2 续一案 core_tick_cnt_held_snapshot):held 快照开关。
        # 模型现状:溢出后 advance() 不再推进(unresolved 期间重检资格而非
        # 计数),cnt 停在溢出时刻 divmod 的余数(本场景对齐恰 0)——「溢出后
        # CNT 冻结」是模型的显式假设(assumed-not-measured),不是硬件声明;
        # 硬件倾向「溢出自动回绕继续计数」但无 BR28 证据,codex 定调维持模型
        # 现状+显式标注。开关只把 unresolved 窗内 offset 4(CNT)读放行为
        # 「返回模型实际存储的溢出后快照」——不是 PRD-1、不是周期末值、不是
        # 合成 0:值就是 self.cnt(冻结的余数)。读无副作用(不清 pending、
        # 不重装载、不动在途/ACK/jiffies);CON(offset 0)同窗保持原严格停。
        self.cnt_held_snapshot=cnt_held_snapshot
        # 敏感性探针值(probe_only,CV 双探针先例):非 None 时 unresolved 窗内
        # CNT 读返回该探针值(0 vs 周期末值 PRD-1 两分叉对照用),主开关世界
        # 恒 None。只影响读值,不动任何状态。
        self.cnt_snapshot_probe_value=None
        # 路 JLEQ 续二:PRD=0xFFFFFFFF 边沿周期使能放行(29,229,183 停点
        # 0x6065834=tick 重启函数 0x6065806 尾 `st.w.modimm [r1+0]=0x1`:固件
        # 先收割 CNT 折算 µs 存 [0x1265C8],再 CON=0/CNT=0/PRD=0xFFFFFFFF/
        # CON=1——core tick 切「自由运行 µs 计数器、溢出关断」形态)。默认关:
        # 使能仍停 core_tick_period_edge_unverified;开启后恰放行 prd==
        # 0xFFFFFFFF 一格(prd==0 无证据恒拒),计数走既有 divmod(prd+1)
        # 公式不变(2^32 周期内零到期),读回/ACK/pending 语义全不动。
        self.freerun_edge_period=freerun_edge_period

    def read(self,offset):
        # 路 BQ(三查之二):异常由 read() 头部的通用 overflow guard 抛出
        # (覆盖 CON/CNT/PRD 三个 offset,先于 offset 分派)——开关只把
        # offset 8(PRD)且 prd_write 有值的一格放行为「回放最近真实写入」,
        # 其余(CON/CNT)在 unresolved 窗内保持原严格停,语义与停点名逐字
        # 不变;读无副作用(不清 pending、不重装载 CNT、不动在途/ACK/相位)。
        # 路 B3 续:cnt_held_snapshot 把 offset 4(CNT)在 unresolved 窗内放行
        # 为「返回实际存储快照」(同一 guard 的第二格放行,假设文本见
        # report() 的 cnt_held_snapshot_projection 键)。
        if self.unresolved_overflow and not (
                (self.prd_readback and offset==8 and self.prd_write is not None) or
                (self.cnt_held_snapshot and offset==4 and self.cnt is not None)):
            raise Blocked('core_tick_overflow_requires_resolution')
        if self.unresolved_overflow and self.cnt_held_snapshot and offset==4:
            # 敏感性探针(probe_only)优先返回探针值;正常世界返回冻结快照。
            if self.cnt_snapshot_probe_value is not None:
                return self.cnt_snapshot_probe_value
            return self.cnt
        if offset==8 and self.unresolved_overflow and self.prd_readback:
            return self.prd_write
        value={0:self.con,4:self.cnt,8:self.prd}[offset]
        if value is None or (offset==0 and self.pending is None):raise Blocked('unknown_core_tick_state')
        if offset==0 and self.pending:raise Blocked('core_tick_pending_readback_unverified')
        return value

    def write(self,offset,value):
        if self.unresolved_overflow:raise Blocked('core_tick_overflow_requires_resolution')
        if offset==0:
            if value&~0x41:raise Blocked('core_tick_control_not_modeled',value=hex(value))
            if value&1:
                if self.progress is None:raise Blocked('core_tick_enable_not_modeled',value=hex(value))
                if self.cnt is None or self.prd is None or self.pending is None:raise Blocked('unknown_core_tick_state')
                # 路 JLEQ 续二:边沿周期使能——恰放行 prd==0xFFFFFFFF(prd==0
                # 恒拒):模型公式 period=prd+1=2^32 在任何受建模视界内零到期
                # (2^30 模型步),与「溢出关断的自由运行计数器」固件意图一致;
                # 硅上最大周期比较器行为未测量,显式假设只在开关开启时声明。
                if self.prd==0 or (self.prd==0xffffffff and not self.freerun_edge_period):
                    raise Blocked('core_tick_period_edge_unverified',period=hex(self.prd))
                if self.cnt>self.prd:raise Blocked('core_tick_count_exceeds_period')
            self.con=value&1
            if value&64:self.pending=False
        elif offset in (4,8):
            if self.con is not None and self.con&1:raise Blocked('active_core_tick_reconfiguration_unverified')
            if offset==4:self.cnt=value
            else:self.prd=value;self.prd_write=value
        else:raise ValueError('invalid core tick offset')
        self.writes+=1

    def advance(self):
        if self.unresolved_overflow:raise Blocked('core_tick_overflow_requires_resolution')
        if self.con is None or not self.con&1:return
        periods,self.cnt=divmod(self.cnt+self.progress,self.prd+1)
        if periods:self.pending=True;self.expirations+=periods;self.unresolved_overflow=True

    def ack_rmw(self,kind,operand):
        # The handler acknowledges by |=0x40 (bit6 W1C clear-pending) on CON.
        # The RMW readback bypasses the pending-readback stop; only the exact
        # ACK shape is allowed, and only for a genuinely delivered overflow.
        if kind!='or' or operand!=0x40:raise Blocked('core_tick_rmw_not_modeled',operation=kind,operand=hex(operand&0xffffffff))
        if self.pending is None:raise Blocked('unknown_core_tick_state')
        if not self.pending:raise Blocked('core_tick_ack_without_pending')
        self.pending=False;self.unresolved_overflow=False;self.acknowledgements+=1
        self.delivery_in_flight=False

    def report(self):
        row={'con':self.con,'cnt':self.cnt,'prd':self.prd,'pending':self.pending,'writes':self.writes,
             'virtual_source_ticks_per_step':self.progress,'expirations':self.expirations,
             'counter_projection_enabled':self.progress is not None,'irq_delivery_simulated':self.acknowledgements>0,'initial_control_fixture':self.initial_con,
             'acknowledgements':self.acknowledgements,'physical_initial_state_measured':False,'pending_bit7_readback_proven':False,
             'unresolved_overflow':self.unresolved_overflow,
             'counter_projection':'PRD+1 from SDK formula; no inferred core-held clock gating or physical phase'}
        # 路 BQ:读回投影键只在开关开启时出现(legacy 报告逐字节不变)。
        if self.prd_readback:
            row['prd_readback_projection']={'enabled':True,
                'prd_last_write':self.prd_write,
                'returns':'the last real firmware PRD store (tick_timer_init); no synthesized default',
                'covers_reads_during':'unresolved_overflow only (offset 8/PRD alone)',
                'con_cnt_reads_and_counter_phase_unchanged':True,
                'prd_write_equals_live_prd':self.prd_write==self.prd,
                'silicon_readback_behavior_measured':False}
        # 路 B3(S19 批 2 续):held 快照键只在开关开启时出现;假设文本按 codex
        # 原文措辞——「在显式『溢出后 CNT 冻结』假设下读取模型保存的溢出后
        # 快照 0」conditional/assumed-not-measured;不是 PRD-1、不代硬件声明
        # 回绕行为。
        if self.cnt_held_snapshot:
            row['cnt_held_snapshot_projection']={'enabled':True,
                'stored_snapshot':self.cnt,
                'returns':'the stored post-overflow snapshot under the explicit '
                          '"CNT frozen after overflow" assumption (conditional, '
                          'assumed-not-measured) — NOT PRD-1, not a period-end value, '
                          'not a synthesized default; the freeze is a model artefact '
                          '(advance stops re-counting while the overflow waits out the '
                          'IPMASK mask), not a silicon claim',
                'covers_reads_during':'unresolved_overflow only (offset 4/CNT alone)',
                'con_prd_reads_unchanged_prd_via_bq_case':True,
                'read_side_effects':'none — reading neither ACKs, clears pending, '
                                    'reloads CNT, resets the timing phase, nor touches '
                                    'jiffies/delivery_in_flight (unit-pinned)',
                'silicon_wraparound_behavior_measured':False,
                'held_window_progress_lost_if_frozen':True}
            if self.cnt_snapshot_probe_value is not None:
                row['cnt_held_snapshot_projection']['sensitivity_probe_value']=self.cnt_snapshot_probe_value
        # 路 JLEQ 续二:freerun 边沿周期键只在开关开启时出现(legacy 报告
        # 逐字节不变);假设文本=「PRD=0xFFFFFFFF 使能按模型公式 period=prd+1
        # =2^32 计数、任何受建模视界内零到期」——硅上最大周期比较器行为
        # 未测量,assumed-not-measured。
        if self.freerun_edge_period:
            row['freerun_edge_period_projection']={'enabled':True,
                'accepts':'exactly the PRD==0xffffffff enable (the firmware tick-restart shape '
                          'CON=0 -> CNT=0 -> PRD=0xffffffff -> CON=1, 0x6065830..34); PRD==0 '
                          'stays refused',
                'period_arithmetic':'unchanged model formula period=prd+1=2**32 — zero expirations '
                                    'within any modeled horizon (~2**30 model steps at progress '
                                    '4/step), matching the firmware free-run us-counter intent '
                                    '(the restart harvests CNT/(clock/1e6) into [0x1265c8] first)',
                'assumption':'assumed-not-measured — silicon max-period comparator/wraparound '
                             'behavior at PRD==0xffffffff is unmeasured; readback/ACK/pending '
                             'semantics unchanged'}
        return row


class GpcntConValue:
    """Partial GPCNT CON word returned by the opt-in read-back projection.

    Only bits the guest's own RMW writes made known are known (GpioOutValue
    partial-latch family). A bitwise AND with a mask that covers every unknown
    bit yields a plain int — the AND forces every masked-off bit to a known
    zero — so single-bit predicates (beq.and.ri / bne.and.ri) decide on known
    bits alone; any other consumption stops instead of fabricating bits.
    """
    __slots__=('value','known')

    def __init__(self,value,known):
        self.known=known&0xffffffff;self.value=value&self.known

    def __and__(self,mask):
        mask&=0xffffffff
        if (self.known|(~mask&0xffffffff))==0xffffffff:return self.value&mask
        self.consumed()

    def __rand__(self,mask):return self.__and__(mask)

    def consumed(self,*args,**kwargs):
        raise Blocked('gpcnt_con_readback_consumed',known_mask=hex(self.known),
                      known_value=hex(self.value))

    def concrete(self):
        if self.known!=0xffffffff:self.consumed()
        return self.value

    __int__=__index__=concrete
    def __float__(self):return float(self.concrete())
    def __bool__(self):return bool(self.concrete())
    def __hash__(self):return hash(self.concrete())
    def __neg__(self):return -self.concrete()
    def __pos__(self):return self.concrete()
    def __invert__(self):return ~self.concrete()

    def __repr__(self):return 'GpcntConValue'


def _gpcnt_con_binary(operation,reverse=False):
    def apply(self,other):
        lhs=self.concrete();rhs=other.concrete() if isinstance(other,GpcntConValue) else other
        return operation(rhs,lhs) if reverse else operation(lhs,rhs)
    return apply


for _name,_operation in (('eq',operator.eq),('ne',operator.ne),('lt',operator.lt),('le',operator.le),
        ('gt',operator.gt),('ge',operator.ge),('or',operator.or_),('xor',operator.xor),
        ('add',operator.add),('sub',operator.sub),('mul',operator.mul),('lshift',operator.lshift),
        ('rshift',operator.rshift),('truediv',operator.truediv),('floordiv',operator.floordiv),
        ('mod',operator.mod),('pow',operator.pow)):
    setattr(GpcntConValue,'__'+_name+'__',_gpcnt_con_binary(_operation))
    if _name not in ('eq','ne','lt','le','gt','ge'):
        setattr(GpcntConValue,'__r'+_name+'__',_gpcnt_con_binary(_operation,True))


class GPCNTCleanup:
    """Only the observed disable/clear path; unknown neighbouring bits stay so."""
    def __init__(self,readback_projection=False,config_words=False,completion_bit=False,num_value=None):
        self.bits=PartialWord();self.pending=None;self.disable_writes=0;self.clear_writes=0
        # con_readback_projection(opt-in,需 gpcnt_cleanup_projection,路 CG):
        # GPCNT_CON(0xfd1100,SDK br28.h JL_GPCNTTypeDef CON __RW/NUM __RO)读回
        # =固件自己已执行写出的位粒度已知状态(lp_timer/fd31 CON「读回=最后
        # 写值」先例的位粒度变体——本前缀 CON 只有 disable/clear 两个位 RMW,
        # 无整字写,故返回部分值对象而非整数)。未知位消费诚实停,绝不臆造。
        self.readback_projection=readback_projection;self.con_reads=0
        # config_words(opt-in,需 con_readback 投影,路 JLEQ 续五):W4[4] VLVD
        # 域的 GPCNT 重配置面——29,544,781 停点 0x60688b6(ld.w→and/or 组装
        # →st.w 重配置链,函数 0x60688xx,W4[4] walker 0x6034c8e 直调)消费
        # 未知配置位(bits12-15/1-3/8-11/28-31 时钟选择·分频字段)。开启后:
        # ①读回=全字整数(已知位照实、未知配置位显式合成初始 0,AM 容器族
        # synthetic-zero 先例,derived-not-measured 非复位值声明);②整字写
        # 记录(全字已知落 PartialWord);③接受 |=0x1 使能形状(计数开始,
        # 计数语义不建模)。NUM(0xfd1104,__RO 计数值)仍严格停(不经本类)。
        self.config_words=config_words;self.config_reads=0;self.whole_writes=0;self.enable_writes=0
        # completion_bit(opt-in,需 config_words,路 JLEQ 续七):S17 稳态测量
        # 任务 0x6069538 的完成位轮询——0x6069540 bit0 使能门(W4[4] 重配置
        # 已 |=1 使能 → 实测走轮询臂)后 0x6069546/48 `beq.and.ri (r0&128)
        # ==0 回跳`等 bit7 置位(等的是置 1 不是清零,方向按编码)。投影:
        # 配置字读回 OR 0x80——「测量在第一次轮询读时已完成」的条件投影
        # (lrct_trim_done bit7 强制置 1/SFC PLL-lock bit15 同模式;bit7 布局
        # 由轮询方向推断,SDK __RW 无状态位语义,非硅测量;延迟不建模)。
        # |=0x40 清 pending 投影照旧把存储位清 0(读侧强制与存储无关);
        # NUM 读(紧随其后的 0x606955c)不经本类、保持严格停=下一前沿。
        self.completion_bit=completion_bit;self.completion_reads=0
        # num_value(路 GPCNUM,需 completion_bit):JL_GPCNT->NUM(0xfd1104,
        # __RO 计数值)的显式探路值(derived-not-measured,LRCT trim_result
        # 同款数据 fixture)。0x606955c 读值进 491520000/NUM*1000+20 槽滑动
        # 平均(消费面=低功耗域漂移补偿数据路径,验证臂内无值分支);探路值
        # =假设标称 32768 Hz 睡眠钟的恒等计数 15000(491520000=32768×15000,
        # 32768 系 assumed 非推导,推导链见 probe 常量注释)。非零下界:消费面
        # 0x606957a 无零检查直接除 NUM,0 值会触发除零,拒绝。未提供时 NUM
        # 读保持 unmapped 严格停。
        if num_value is not None and not completion_bit:
            raise ValueError('the GPCNT NUM fixture requires the completion-bit projection')
        if num_value is not None and (not isinstance(num_value,int)
                                      or not 1<=num_value<=0xffffffff):
            raise ValueError('GPCNT NUM fixture must be a nonzero unsigned32 count')
        self.num_value=num_value;self.num_reads=0

    def rmw(self,kind,operand):
        if kind=='and' and operand&0xffffffff==0xfffffffe:
            self.bits.apply('and',operand);self.disable_writes+=1
        elif kind=='or' and operand==64:
            if not self.bits.known&1 or self.bits.value&1:raise Blocked('gpcnt_disable_state_unknown')
            self.pending=False;self.bits.apply('and',~128);self.clear_writes+=1
        elif kind=='or' and operand==1 and self.config_words:
            self.bits.apply('or',operand);self.enable_writes+=1
        else:raise Blocked('gpcnt_counting_or_configuration_not_modeled',operation=kind,operand=hex(operand&0xffffffff))

    def read(self):
        if self.config_words and self.readback_projection:
            # 配置字读回:全字整数——已知位照实(重配置写后全字已知),未知
            # 配置位显式合成初始 0(AM 族先例;非复位值声明);completion
            # 投影开启时 bit7 读侧强制置 1(存储位不变,清 pending 照旧)。
            self.config_reads+=1
            if self.completion_bit:
                self.completion_reads+=1
                return self.bits.value|0x80
            return self.bits.value
        if self.readback_projection and self.bits.known:
            # 读回=固件自己写出的已知位(bit0=0 由 app 的 &=0xFFFFFFFE 强制,
            # bit7=0 由 |=0x40 后的立即清投影)。本前缀唯一读者=任务 0x6069538
            # 的 bit0 使能判定(beq.and.ri,AND 掩码把其余位强制为已知 0 → 单
            # bit 谓词可判);任何未知位消费经 GpcntConValue 诚实停。
            self.con_reads+=1
            return GpcntConValue(self.bits.value,self.bits.known)
        raise Blocked('unknown_gpcnt_configuration_bits',known_mask=hex(self.bits.known))

    def write(self,value):
        """配置字整字写(opt-in,路 JLEQ 续五):全字覆盖、全字已知落记录。"""
        if not self.config_words:raise ValueError('GPCNT config-word stores require the config opt-in')
        if not isinstance(value,int) or not 0<=value<=0xffffffff:raise ValueError('unsigned32 word expected')
        self.bits=PartialWord(value,0xffffffff);self.whole_writes+=1

    def read_num(self):
        """NUM(0xfd1104,__RO 计数值)读(路 GPCNUM,需显式探路值)。

        硅测量值,默认不臆造数值(unmapped 严格停);显式探路值
        (num_value,derived-not-measured)按 LRCT trim_result fixture 先例
        放行,每次读回同一常量并计数(真实硬件逐次有抖动,20 槽滑动平均
        消化——常量=标称理想化,与 LRCT 38400 服务 20 次读同款)。
        """
        if self.num_value is None:raise Blocked('gpcnt_num_not_modeled',address=hex(GPCNT_NUM))
        self.num_reads+=1
        return self.num_value

    def report(self):
        result={'known_value':hex(self.bits.value),'known_mask':hex(self.bits.known),'pending':self.pending,
                'disable_writes':self.disable_writes,'clear_writes':self.clear_writes,
                'pending_clear_is_immediate_projection':True,
                'counting_or_prior_bootloader_state_simulated':False,'command_bit6_readback_proven':False}
        if self.readback_projection:
            # 仅投影开启时加键:clock 场景族的 gpcnt 子报告字节不变。
            result['con_readback_projection']={
                'address':hex(GPCNT_CON),'reads':self.con_reads,
                'known_mask':hex(self.bits.known),'known_value':hex(self.bits.value),
                'readback_semantics':('bit-granular last-firmware-write state; br28.h marks '
                    'JL_GPCNT CON __RW, and the only executed writers in the whole app+ROM are the '
                    'disable (&=0xFFFFFFFE -> bit0=0) and clear (|=0x40, immediate pending-clear '
                    'projection -> bit7=0) RMW pair, so the readback returns exactly the bits the '
                    'guest itself forced; unknown bits are never fabricated — a GpcntConValue only '
                    'answers bitwise-AND tests whose mask covers every unknown bit (the AND forces '
                    'masked-off bits to zero), mirroring the GpioOutValue partial-latch transport '
                    'and the ICFG partial-value doctrine (partial numeric consumers stop)'),
                'first_consumer':('task 0x6069538 @ 0x6069540: beq.and.ri bit0 enable gate; bit0=0 '
                    '(disabled by the firmware\'s own early cleanup) takes the real skip branch to '
                    'the epilogue at 0x60695fa — no NUM read, no bit7 poll, matching the disabled '
                    'counter the firmware itself left behind'),
                'count_readback_proven':False,'not_a_timing_model':True}
        if self.config_words:
            result['config_words_projection']={
                'reads':self.config_reads,'whole_writes':self.whole_writes,
                'enable_writes':self.enable_writes,
                'readback_semantics':('full-word integer — known bits as recorded, never-written '
                    'configuration bits serve the explicit synthetic initial 0 (route-AM '
                    'synthetic-zero precedent; derived-not-measured, NOT a reset-value claim); '
                    'after the first whole-word config store the word is fully known'),
                'accepted_shapes':('the W4[4] VLVD-domain reconfig chain (ld.w -> and/or '
                    'compose -> st.w, 0x60688a4..d4: clock-select bits28-31=0xB, bits12-15=0x4, '
                    'bits1-3=0xC, bits8-11=0x6) plus the |=0x40 clear and the |=0x1 enable '
                    '(0x60688d6/da) — enable starts the counter, counting semantics are NOT '
                    'modeled; NUM 0xfd1104 (__RO count) stays strictly stopped')}
        if self.completion_bit:
            result['completion_bit_projection']={
                'reads':self.completion_reads,
                'readback_semantics':('config-word readback with bit7 forced set — the steady-state '
                    'measurement task 0x6069538 polls CON at 0x6069546/48 (beq.and.ri loops while '
                    'bit7==0, i.e. waits for SET) after the 0x6069540 bit0 enable gate takes the '
                    'poll arm (the W4[4] reconfig chain enabled the counter); projection = the '
                    'measurement is already complete at the first poll read, latency not modeled '
                    '(route lrct_trim_done bit7 / SFC PLL-lock bit15 same shape); the bit7 layout '
                    'is inferred from the poll direction, the SDK __RW label carries no status '
                    'semantics and this is NOT a silicon measurement'),
                'stored_bit7_untouched':True,
                'num_register_served':self.num_value is not None,
                'next_boundary':('the NUM read at 0x606955c ([0xfd1104], __RO measured count) '
                    'stays strictly stopped without the explicit fixture — the measured-value '
                    'three-way decision of route GPCNUM; with the fixture the run continues '
                    'past twenty served conversions to the watchdog-expiry frontier '
                    '(the value feeds 491520000/NUM*1000 and a 20-slot sliding average)')}
        if self.num_value is not None:
            result['num_register_fixture']={
                'address':hex(GPCNT_NUM),'reads':self.num_reads,
                'value':hex(self.num_value),
                'fixture_semantics':('named probe value, derived-not-measured (route-GPCNUM '
                    'three-choice (a), LRCT trim_result 38400 same shape): the firmware converts '
                    'NUM into a frequency via 491520000/NUM*1000 (task 0x6069538 at 0x606957a/80) '
                    'and slides it through the 20-slot average to [0x12A3D0]; the constant 15000 '
                    'is the identity count of an ASSUMED nominal 32768 Hz sleep clock '
                    '(491520000 = 32768*15000 exactly — arithmetic self-consistency, NOT an '
                    'independent derivation: unlike the LRCT precedent, where the firmware '
                    'itself writes the 200000 nominal elsewhere, no firmware-side nominal for '
                    'this measurement exists in evidence; documented alternatives: 32000 Hz '
                    '(count 15360) and 160000 Hz (count 3072, where the 4476/7188 consumer '
                    'coefficients fall out exactly) — codex-review candidates, none exclusive); '
                    'every read serves the same constant (real hardware jitters per conversion; '
                    'the 20-slot average exists to absorb exactly that); the fixture is scoped '
                    'to this world\'s CON configuration (GSS=4/GTS=6/CSS=6, measured 0x460C '
                    'prefix) — a future reconfiguration needs re-examination'),
                'consumer_sensitivity':('data-only within the verified arms: the quotient feeds '
                    '[0x12A3E0] ring slots, '
                    '[0x12A3D0] average and the low-power drift consumers 0x60675e4/0x6067668 '
                    '(4476/7188 coefficients, P11 mailbox + P33 LP-domain payloads); the '
                    'numerator gate byte '
                    'b[0x126874] (the same device-struct +692 flag the LRCT trim reads) is 1 at '
                    'the stop, so the quotient is live; a different plausible NUM '
                    '(e.g. 15360 for a 32000 Hz nominal) changes only RAM/mailbox DATA — the '
                    'in-world branches (0x6069584 ring[0]==0 init-vs-shift, the fill counter '
                    'compare) do not test the quotient for any nonzero fixture that keeps '
                    '491520000/NUM*1000 and the 20-term sum inside u32 (15000: 32768000 and '
                    '655360000, both far in range); the ring-replacement path is dead in this '
                    'world (the counter stays disabled after the first conversion), so the '
                    'constant never exercises the shift-out leg — noted, not simulated'),
                'zero_is_refused':True,
                'counting_semantics_modeled':False}
        return result


class EfuseReadProjection:
    """Exact CPU0 read transaction shape with explicitly supplied OTP inputs.

    No programming commands, read latency or inferred device calibration.
    Upper read bits are an explicit fixture to test the guest low-byte mask.
    """
    def __init__(self,words=None,read_upper_bits=0xa5a50000):
        if words is not None and (len(words)!=4 or any(not isinstance(w,int) or not 0<=w<=0xffffffff for w in words)):
            raise ValueError('need four explicit unsigned32 EFUSE words')
        if not 0<=read_upper_bits<=0xffffffff or read_upper_bits&255:raise ValueError('invalid EFUSE upper-bit fixture')
        self.words=None if words is None else tuple(words);self.upper=read_upper_bits
        self.page=None;self.byte=0;self.active=False;self.read_done=False;self.reads=0

    def read(self,address):
        if address!=EFUSE_RDAT:raise Blocked('efuse_control_read_not_modeled',address=hex(address))
        if not self.active or self.read_done:raise Blocked('efuse_read_outside_selected_byte')
        if self.words is None:raise Blocked('unknown_efuse_input',page=self.page,byte=self.byte)
        value=(self.words[self.page]>>(8*self.byte))&255
        self.read_done=True;self.reads+=1
        return self.upper|value

    def write(self,address,value):
        if address==EFUSE_CON0:
            if self.active or value&~0xc0:raise Blocked('efuse_page_select_not_modeled',value=hex(value))
            self.page=value>>6;self.byte=0;return
        if address!=EFUSE_CON1:raise Blocked('efuse_data_is_readonly')
        if value==0:
            if not self.active or not self.read_done:raise Blocked('efuse_deassert_before_completed_read')
            self.active=self.read_done=False;self.byte+=1;return
        if self.page is None or self.active or self.byte>=4 or value!=0x82|(self.byte<<2):
            raise Blocked('unsupported_efuse_command',value=hex(value))
        self.active=True;self.read_done=False

    def report(self):
        return {'words_fixture':None if self.words is None else [hex(w) for w in self.words],
                'read_upper_bits_fixture':hex(self.upper),'selected_page':self.page,'selected_byte':self.byte,
                'active':self.active,'completed_data_reads':self.reads,'device_otp_measured':False,
                'physical_read_latency_or_programming_modeled':False}


class WatchdogProjection:
    """SDK command projection with an optional, explicit nominal timeout clock.

    One CON command per observed KEY pair is a constrained software contract,
    not a claim about silicon unlock lifetime. Enabling requires an explicit
    clock; counting before the first CON initialization is outside this model.
    Expiry stops execution; hardware reset/exception delivery is not invented.
    """
    keys=(0x048c159d,0x26ae37bf)

    def __init__(self,progress=None):
        if progress is not None:
            if len(progress)!=2 or not all(isinstance(x,int) for x in progress) or progress[0]<0 or progress[1]<1:
                raise ValueError('watchdog progress needs explicit nonnegative numerator/positive denominator')
            progress=tuple(progress)
        self.progress=progress;self.con=None;self.key_phase=0
        self.elapsed=0;self.remainder=0;self.commands=0;self.feeds=0;self.expired=False
        self.command_values=[]
        # 路 WDFEED:邮箱喂狗事务闭合的 P11 侧 feed 副作用计数(投影,见
        # mailbox_feed)。与 feeds 分开计:feeds 里既有主核直接 CON bit16 命令
        # (观测形状),也有本投影合成的 P11 侧副作用。
        self.mailbox_feeds=0

    def read(self,address):
        if self.expired:raise Blocked('watchdog_requires_reset_after_expiry')
        if address!=WDT_CON:raise Blocked('watchdog_key_read_not_modeled')
        if self.con is None:raise Blocked('uninitialized_watchdog_control')
        return self.con

    def write(self,address,value):
        if self.expired:raise Blocked('watchdog_requires_reset_after_expiry')
        if address==WDT_KEY:
            if self.key_phase>=2 or value!=self.keys[self.key_phase]:
                raise Blocked('watchdog_key_sequence_not_modeled',value=hex(value),phase=self.key_phase)
            self.key_phase+=1;return
        if address!=WDT_CON:raise ValueError('not a watchdog register')
        if self.key_phase!=2:raise Blocked('watchdog_command_without_observed_key_pair')
        if value&~0x100ff or (value&31)>17:raise Blocked('unsupported_watchdog_control',value=hex(value))
        if value&32 and value&0xc0:raise Blocked('watchdog_assert_irq_mode_not_modeled',value=hex(value))
        if value&32 and self.progress is None:
            raise Blocked('watchdog_timeout_clock_unknown',value=hex(value))
        if self.con is not None and self.con&32 and value&32 and (value&31)!=(self.con&31):
            raise Blocked('active_watchdog_period_change_not_modeled')
        if value&0x10000:self.elapsed=self.remainder=0;self.feeds+=1
        if not value&32 or self.con is None or not self.con&32:
            self.elapsed=self.remainder=0
        self.con=value&~0x10000;self.key_phase=0;self.commands+=1
        self.command_values.append(value)
        if len(self.command_values)>32:self.command_values.pop(16)

    def advance(self):
        if self.expired:raise Blocked('watchdog_requires_reset_after_expiry')
        if self.con is None or not self.con&32:return
        ticks,self.remainder=divmod(self.remainder+self.progress[0],self.progress[1])
        self.elapsed+=ticks
        if self.elapsed>=1<<(self.con&31):
            self.expired=True
            raise Blocked('watchdog_expired_delivery_not_implemented',control=hex(self.con),
                          elapsed_nominal_units=self.elapsed)

    def mailbox_feed(self):
        """路 WDFEED:喂狗邮箱事务闭合(P11 回声匹配+固件 CLR 清 ack)时,
        P11 侧 WDT feed 副作用投影(计数器重装)。

        证据链(全部静态/运行态,无 q32s 执行):
        - 主核喂狗函数 0x12cc7e(16 指令反汇编钉死,docs/s19-final-specs.md
          §3.1)只走邮箱(kick 0x400/消息字节 8/回声比较 0x12ccac/CLR
          0x12ccb0),成功条件=回声匹配后清 ack——本投影的提交点与固件
          自己的成功判据重合;本前缀 WDT 接口主核 command_values=
          [0,0,0,14,46,0x1002E] 恰一次直接 feed(init 内),此后零直接写;
        - 官方 p11_code.bin(12248B,BANKCB CRC 全过)公共代码段 3 处 32 位
          字面量 0x048c159d(p11_local 0x2e2/0x8c4/0x20cc)=WDT 双 KEY 第
          一个——P11 固件含 WDT 解锁序列(字面量存在≠消息 8 分派链已证);
        - 真机 park 稳态长时间存活(用户设备待机不重启)支持稳态存在
          保活机制(不排除周期自主喂狗/停钟等其他控制路径)。
        假设(显式,synthetic-not-measured):P11 成功处理消息 8 会重装本
        模型中的 WDT;q32s 未执行,消息处理器到 WDT 写入的完整路径、实
        际副作用时刻与延迟均未验证;ack 成功沿用既有邮箱投影。**提交点
        =事务闭合(CLR)是本投影选定的记账时刻,非已证明的物理喂狗时刻**
        (真实 P11 可能在写回声之前已经喂狗;对 16384 单位名义周期,事务
        长度量级的时刻差不建模)。计数语义与主核 CON bit16 feed 写一致:
        elapsed/remainder 同时清(相位重置仍属 counter_phase 既有假设);
        feeds 为总计数(主核直接+本投影),mailbox_feeds 单列仅计本投影,
        不伪造 KEY/CON 总线写、不消耗主核 key_phase/commands。已 expired
        的看门狗不允许补喂复活(与 read/write/advance 同判)。
        """
        if self.expired:raise Blocked('watchdog_requires_reset_after_expiry')
        self.elapsed=0;self.remainder=0
        self.feeds+=1;self.mailbox_feeds+=1

    def report(self):
        return {'con':self.con,'key_phase':self.key_phase,'commands':self.commands,'feeds':self.feeds,
                'mailbox_feed_side_effects':self.mailbox_feeds,
                'command_values':self.command_values,'command_values_truncated':self.commands>len(self.command_values),
                'elapsed_nominal_units':self.elapsed,'fraction_remainder':self.remainder,'expired':self.expired,
                'assumptions':{'nominal_units_per_model_step':self.progress,
                    'period_units':'1 << time_code, from SDK query table; hardware calibration unverified',
                    'counter_phase':'fresh enable/disable reset elapsed; feed rearms full period; explicit projection, silicon phase unmeasured',
                    'counting_before_first_explicit_CON_initialization_modeled':False,
                    'bit16_readback':'configuration projection omits command bit; silicon readback unmeasured',
                    'key_policy':'one CON command per observed KEY pair; hardware key lifetime unverified',
                    'mailbox_feed_side_effect':('route WDFEED, opt-in: a successfully closed feed-dog '
                        'mailbox transaction (kick-latched message byte 8, observed ack, matching echo, '
                        'then the firmware CLR) reloads the modeled WDT; counted separately in '
                        'mailbox_feed_side_effects while feeds stays the total (direct + projected). '
                        'Evidence: the feed-dog function 0x12cc7e only touches the mailbox and its own '
                        'success test IS the echo match (0x12ccac) followed by the CLR (0x12ccb0); the '
                        'main core wrote the WDT interface exactly once after init (0x1002E) on this '
                        'prefix; the official p11_code.bin common segment contains the WDT unlock key '
                        '0x048c159d three times (p11_local 0x2e2/0x8c4/0x20cc); real devices survive '
                        'park steady state far longer than the nominal 1<<14-unit period. EXPLICIT '
                        'ASSUMPTION: the P11 message-8 handler reloads the WDT — the dispatch chain to '
                        'the WDT write, the physical feed moment and its latency are NOT verified (no '
                        'q32s execution exists here; the CLR commit point is the projection bookkeeping '
                        'instant, not a proven physical feed time; ack success itself rides the existing '
                        'mailbox projection). Conditional pass, derived-not-measured'),
                    'expiry_reset_or_irq_delivery_simulated':False}}


class P11AccessProbe:
    """Explicit external-status counterfactual, NOT a P11 response model.

    CPU0 source establishes the five writes and bit0 poll, not their causality.
    A supplied status is fixed from the beginning; writes never manufacture it.
    The opt-in ready_response projection is separate: after the observed CON2
    request sequence it forces bit0 to 1 on top of the explicit base fixture
    word (no other bit is invented), with its own weaker evidence label.
    """
    sequence=(0xff,0xee,0xab,0x0e,0x55)

    def __init__(self,status=None,ready_response=False):
        if status not in (None,0,1):raise ValueError('P11 status fixture must be unknown,0 or1')
        if ready_response and status is None:
            raise ValueError('PMU ready response projection requires an explicit base status fixture')
        self.status=status;self.ready_response=ready_response
        self.phase=0;self.sequences=0;self.polls=0

    def write(self,value):
        if value!=self.sequence[self.phase]:raise Blocked('unmodeled_p11_access_write',value=hex(value))
        self.phase+=1
        if self.phase==5:self.phase=0;self.sequences+=1

    def read(self):
        if self.phase or not self.sequences:raise Blocked('p11_status_read_outside_known_sequence')
        self.polls+=1
        if self.status is None:raise Blocked('unknown_external_pmu_status',address=hex(PMU_STA))
        return (self.status|1) if self.ready_response else self.status

    def report(self):
        result={'fixed_external_status_hypothesis':self.status,'completed_sequences':self.sequences,
                'phase':self.phase,'status_polls':self.polls,'response_causality_or_timing_modeled':False,
                'q32s_executed':False}
        if self.ready_response:
            # 独立标注:置位机制没有任何 P11 侧软件证据,证据等级低于 PWR_CON bit5。
            result['pmu_ready_response_projection']={
                'trigger':'PMU_STA read after the completed CON2 request sequence (p11_init poll 0x606890c)',
                'readback':'base fixture word with bit0 forced to 1; no other bit invented',
                'evidence_level':'weaker than the PWR_CON bit5 response: only the main-core poll is proven, the P11-side bit0 set mechanism is unproven',
                'bit0_set_mechanism_proven':False,'response_latency_modeled':False,
                'causal_link_to_pwr_con_bit5_assumed':False}
        return result


class P11LowPowerResponse:
    """PWR_CON(0xf2a400) bit5(LP_PWR_INIT_FLAG)最小请求-响应投影。

    主核 power_init(0x60679a0,rmw.or.modimm |=0x20)请求 P11 低功耗初始化后死等该位
    清零(0x60679ae/b0)。P11 侧 q32s 0xafa `bitclr r2,0x5` 无条件执行已证实,但「主核
    置位→P11 清位」的时序/可达性是推断。投影规则:仅在观察到主核真实的 |=0x20 RMW
    之后,PWR_CON 读回把 bit5 投影为 0(显式假设:P11 已处理请求,延迟未建模);其余位
    保持复位快照原样(POR bit7 等),不发明其他效应,也不与 PMU_STA bit0 假定因果。
    """

    def __init__(self):
        self.requests=0

    def handles_rmw(self,address,kind,operand):
        # 只匹配已证实的请求形式:对 PWR_CON 的 or 0x20;其他形式保持严格路径
        return (address==PWR_CON and kind=='or' and
                operand is not None and operand&0xffffffff==LP_PWR_INIT_FLAG)

    def request(self):
        self.requests+=1

    def read(self,address,value):
        # 请求之后 bit5 读回 0;请求之前不臆造(快照原样返回,包括 POR bit7)
        if address==PWR_CON and self.requests:
            return value&~LP_PWR_INIT_FLAG&0xffffffff
        return value

    def post_flag_clear_read(self,address,snapshot):
        """清标志后、已观察到请求时的 PWR_CON 读回(真实前缀检验路径)。

        真实顺序(本前缀反汇编钉死):主 init 复位原因处理 0x6068c80 先读 PWR_CON
        bit7(0x6068ce4..e8)、置位才发 bit6 命令清标志(0x6068cf2 `|=0x40`,
        复位投影按 flag-clear 记录、此后整字读回严格停),app_core power_init 再
        |=0x20 请求(0x60679a0)并死等 bit5 清零(0x60679ae/b0)。本方法把该轮询
        读回从「清标志后 unknown」中放出来:bit7 按观察到的「查 bit7→发 bit6」
        配对推定已清 0(W1C 目的),其余位保持 POR 快照,bit5 由请求-响应投影清
        0——清标志后的整字状态无硅测量,仅在 lpwr 投影开启且已观察到真实请求的
        读回上使用;其他读者、其他 RESET_FLAGS 寄存器仍严格停。
        """
        if address!=PWR_CON or not self.requests:
            raise Blocked('p11_lpwr_readback_requires_request',address=hex(address))
        return (snapshot&~LP_RST_FLAG&~LP_PWR_INIT_FLAG)&0xffffffff

    def report(self):
        return {'requests_observed':self.requests,
                'trigger':'exact main-core rmw.or.modimm |=0x20 on PWR_CON (power_init 0x60679a0)',
                'readback_after_request':'bit5=0 (P11 processed the request); other bits unchanged from the reset snapshot',
                'post_flag_clear_readback':'after the main-init bit6 flag-clear (0x6068cf2) the poll read returns snapshot&~bit7 with bit5=0; bit7-cleared is inferred from the observed check-bit7-then-clear pair, not silicon-measured',
                'evidence_level':'q32s 0xafa bitclr bit5 executes unconditionally (local software evidence); main-core release timing and q32s reachability inferred, silicon unverified',
                'response_latency_modeled':False,'causal_link_to_pmu_sta_bit0_assumed':False,
                'other_bits_or_registers_modeled':False}


class P11WindowRegisters:
    """主核写进 P11(q32s)SFR 窗口的纯写寄存器记录(opt-in,读回一律拒绝)。

    SDK p11_sfr.h:p11_sfr_base=0xf2a000,P11_CLOCK 组(P11_CLOCK_BASE+0x400)的
    PWR_CON/SYS_DIV(+0x10)/CLK_CON0(+0x14)等;0xf2a0c0 是 P11 侧 q32s 核自己的
    PMU_CON(HANDOFF P11 清单:主核 power_init 0x60679a4/a8 `|=1` 释放 q32s)。
    固件全量扫描证据(app 线性反汇编,字节级常量搜索):
    - 0xf2a0c0(=0xf2a400-832,由 r7=0xf2c04c 基址寄存器算出,字面量搜索必漏):
      全 app+ROM 仅 power_init 0x60679a8 一处 `rmw.or.modimm [r0+0] |= 0x1`,
      无任何读回、无整字写、无其他 RMW 形式。
    - 0xf2a410 SYS_DIV:仅 0x6067920 `[r4+16]=0`(整字写 0)。
    - 0xf2a414 CLK_CON0:仅 0x606791c `[r4+20]=1`(整字写 1)。
    (0xf2a618 P2M_CLK 的两处写——0x6069062 `=0x800`(p11_init 0x60688ec 族
    init 内)与 0x602f464 `=0x80`(0x602f408 低功耗路径)——本前缀都未执行,
    不建模。)
    模型只做 write-only 记录:整字写记值+次数;0xf2a0c0 只接受已观察的 |=1
    (PartialWord 按位记录,不经读回,与 cpu1_control kick 位记录同语义)。全部
    读回严格拒绝(固件从不读,复位值/读回语义无任何证据);释放/时钟效果(是否
    真的启动 q32s、分频含义)不模拟——q32s 侧响应在 p11_lpwr_response_projection
    单独建模,两者不假定因果。
    """
    RELEASE_RMW_OPERAND=1

    def __init__(self):
        self.control=PartialWord()          # 0xf2a0c0 q32s PMU_CON 位记录
        self.release_rmw_writes=0
        self.stores={address:{'values':[],'writes':0} for address in (P11_SYS_DIV,P11_CLK_CON0)}

    def handles(self,address):
        return address in self.stores

    def handles_release_rmw(self,address):
        return address==Q32S_PMU_CON

    def release_rmw(self,address,kind,operand):
        if not self.handles_release_rmw(address):raise ValueError('not the modeled q32s PMU_CON')
        if kind!='or' or operand is None or operand&0xffffffff!=self.RELEASE_RMW_OPERAND:
            raise Blocked('p11_window_release_rmw_form_not_modeled',address=hex(address),
                          operation=kind,operand=None if operand is None else hex(operand&0xffffffff))
        self.control.apply('or',operand&0xffffffff);self.release_rmw_writes+=1

    def write(self,address,value):
        if address not in self.stores:raise ValueError('not a modeled P11 window register')
        entry=self.stores[address];entry['values'].append(value);entry['writes']+=1
        if len(entry['values'])>64:entry['values'].pop(0)

    def read(self,address):
        raise Blocked('p11_window_register_readback_not_modeled',address=hex(address))

    def report(self):
        return {'q32s_pm_con':{'address':hex(Q32S_PMU_CON),'known_mask':hex(self.control.known),
                               'known_value':hex(self.control.value),'rmw_writes':self.release_rmw_writes,
                               'release_effect_simulated':False,
                               'observed_shape':'power_init 0x60679a8 rmw.or.modimm |=0x1 (sole firmware-wide reference)'},
                'stores':{hex(address):{'values':[hex(v) for v in entry['values']],'writes':entry['writes']}
                          for address,entry in self.stores.items()},
                'sdk_p11_sfr_h':'p11_sfr_base 0xf2a000; P11_CLOCK.SYS_DIV=+0x10 CLK_CON0=+0x14; 0xf2a0c0=q32s PMU_CON (HANDOFF P11 checklist)',
                'write_only':True,'readback_observed':False,
                'reset_values_or_bit_semantics_verified':False,
                'causal_link_to_pwr_con_bit5_response_assumed':False}


class P2mMailboxAck:
    """P11 邮箱 ack 完成投影(BM §3 规格;首版路 B3/S19 批 2 续 0b875ed,
    本版 B2R 重落地——容器/语义逐字保留,挂载点按当前模型形态重排)。

    喂狗函数 0x12cc7e(全 16 指令反汇编钉死,flash 母本 0x060c6456)的事务序列:
      清回声槽 b[P2M+3]=0xFF → 写消息字节 b[M2P+7]=arg → kick [M2P_INT_SET]
      =0x400(bit10) → 无界轮询 [P2M_INT_PND] bit12 → 读回声槽 b[P2M+3] 比对
      → [P2M_INT_CLR]=0x1000 清 ack。
    地址勘误(BM §3.1 三勘误):P2M+3=0xF27F43/M2P+7=0xF27F87(BA/AG 的
    0xF27F03/47 系 +0x40 转写滑移);PND=0xF2A60C(非 0xF2A61C);
    **0xF2A618=M2P_INT_SET**(p11_sfr.h P11_SYSTEM_TypeDef +0x18;旧注释
    「P2M_CLK」系误名,P2M_CLK_CON0=0xf2a610)。
    投影语义(lpwr 请求-响应同款,即时无延迟,synthetic-not-measured):
      - kick 恰 0x400 → 武装:PND 读回 0x1000(bit12)、回声字节=M2P+7 当前
        存储值(P11 把消息写回 P2M 槽的显式假设;消息字节由机器侧对 harness
        RAM 写的被动镜像提供,不拦截真实存储);
      - 清槽写(任意字节值,观测 0xFF)→ 回声解除(读回=槽内存储值);
      - CLR 恰 0x1000 → PND 清位(回声保持到下次清槽——固件每事务先清槽);
      - 未武装的 PND 读=Blocked(世界错即停,不兜底自旋);非 0x400 的 kick
        值(0x800/0x80 域,pmu 框架/IRQ68 handler,不在健康 boot 前缀)只
        记录不武装。
    P11 真实消费/延迟/其余 PND 位不发明(SDK 自己对 PND 行标 //?)。
    消费时序(B2G 提层注):S15 邮箱首停 0x12CC94 在当前前沿停点(28,890,702
    随机种子读)之后——conditional 层条件场景自动开,当前前缀零触达(提层
    实测停点/步数不变)。
    """

    ECHO_SLOT=0xF27F43       # P2M+3 回声槽(字节读写双收口)
    MESSAGE_BYTE=0xF27F87    # M2P+7 消息字节(harness RAM,被动镜像)
    INT_SET=0xF2A618         # M2P_INT_SET(kick 整字写)
    INT_PND=0xF2A60C         # P2M_INT_PND(ack pending 整字读)
    INT_CLR=0xF2A608         # P2M_INT_CLR(清 ack 整字写)
    KICK_VALUE=0x400
    ACK_BIT=0x1000
    CLR_VALUE=0x1000
    # 路 WDFEED:喂狗事务的消息字节(0x12cc7e 写 M2P+7 的观测值;本前缀
    # 0x400 kick 的唯一消息)。P11 对该消息的处理含 WDT feed——是推断
    # (q32s 未执行),证据链见 WatchdogProjection.mailbox_feed 注释。
    FEED_MESSAGE=8

    def __init__(self):
        self.pnd_armed=False       # kick 置位、CLR 清位:PND 读的服务门
        self.echo_armed=False      # kick 置位、清槽写解除:回声读的服务门
        self.kicks=0;self.other_kick_values={}
        self.clr_writes=0;self.clear_slot_writes=0
        self.pnd_reads=0;self.echo_reads=0
        self.message_mirror=None   # M2P+7 最近值(机器侧被动镜像)
        self.stored_slot=None      # 清槽写落进槽里的字节(观测 0xFF)
        self.transactions=[];self.current=None

    def note_message_byte(self,value):
        """机器侧对 M2P+7 harness RAM 写的被动通知(值照常落 RAM,不拦截)。"""
        self.message_mirror=value

    def kick(self,value):
        if value==self.KICK_VALUE:
            self.kicks+=1;self.pnd_armed=True;self.echo_armed=True
            self.current={'arg':self.message_mirror,'poll_reads':0,
                          'echo_byte':None,'clr_writes':0}
        else:
            self.other_kick_values[hex(value)]=self.other_kick_values.get(hex(value),0)+1

    def clear_slot(self,value):
        self.clear_slot_writes+=1;self.stored_slot=value;self.echo_armed=False

    def clear_ack(self,value):
        self.clr_writes+=1
        if value==self.CLR_VALUE:
            self.pnd_armed=False
            if self.current is not None:
                self.current['clr_writes']+=1
                self.transactions.append(self.current);self.current=None
        else:
            raise Blocked('p2m_mailbox_clr_form_not_modeled',value=hex(value))

    def pnd_read(self):
        if not self.pnd_armed:
            raise Blocked('p2m_mailbox_pnd_read_without_kick',address=hex(self.INT_PND))
        self.pnd_reads+=1
        if self.current is not None:self.current['poll_reads']+=1
        return self.ACK_BIT

    def echo_read(self):
        """字节粒度回声读(read_bytes 路由):武装态=M2P+7 镜像,否则=槽存储值。"""
        self.echo_reads+=1
        if self.echo_armed and self.message_mirror is not None:
            if self.current is not None:self.current['echo_byte']=self.message_mirror
            return self.message_mirror
        if self.stored_slot is not None:
            return self.stored_slot
        raise Blocked('p2m_mailbox_echo_slot_never_written',address=hex(self.ECHO_SLOT))

    def report(self):
        return {'kicks':self.kicks,
                'transactions':list(self.transactions),
                'other_kick_values':dict(self.other_kick_values),
                'clear_slot_writes':self.clear_slot_writes,
                'pnd_reads':self.pnd_reads,'echo_reads':self.echo_reads,
                'addresses':{'echo_slot':hex(self.ECHO_SLOT),'message_byte':hex(self.MESSAGE_BYTE),
                             'int_set':hex(self.INT_SET),'int_pnd':hex(self.INT_PND),
                             'int_clr':hex(self.INT_CLR)},
                'disassembly_anchor':'feed-dog 0x12cc7e (flash master 0x060c6456); kick [0xF2A618]=0x400, '
                                     'poll [0xF2A60C] bit12, echo b[0xF27F43], clr [0xF2A608]=0x1000',
                'assumption':'P11 consumed the message and wrote it back to the P2M slot '
                             '(explicit named assumption, synthetic-not-measured); latency not '
                             'modeled (first poll read sees bit12); no other P2M_INT_PND bits invented',
                'latency_modeled':False,'silicon_ack_semantics_measured':False}


class LpTimerInitRegisters:
    """power_init 尾部低功耗定时器初始化链的写记录 + opt-in 的 LRCT 完成读回投影。

    反汇编钉死的三个地址族(全部由基址寄存器算出,字面量搜索必漏):
    - P11_LPTMR0 CON0/CON1/CON2(0xf2a900/04/08,r7(0xf2c04c)-0x174c):
      0x6067a7a..a84 整字写 0x5C/0x53/0。SDK p11_sfr.h P11_LPTMR0_BASE=
      p11_sfr_base+map_adr(0x09,0x00)=0xf2a900,结构 CON0/CON1/CON2/PRD/
      RSC/CNT;值与 p11_app.h LP_TMR0_* 位宏自洽(0x5C=清 P11 侧 wkup/to
      标志并开 IE、0x53=清主核侧标志并开 kick-start、CON2=0 时钟源/分频)。
      本前缀(运行到 0x6067514 为止)写后无人回读;CNT(+0x14,__RO)无访问
      观测——地址族全部由基址寄存器算出,字面量扫描不能穷举,读回一律
      严格拒绝兜底(真实到达时诚实停)。
    - PMU 配置族 0xf2c04c/0xf2c050:0x6067a86/a8a 整字写 0x21/0x41。SDK 头
      无名(主核 PMU 配置族,与复位快照 0xf2c048、0xf2c054/0xf2c05c 配置族
      同一块)。0xf2c04c 之后会被 pmu_task 回读(0x6068fa0/fa4 字节读
      b[r7+0/1] 组装 P11 heap 基址)——那在该任务收到消息的时间片里,不在
      本前缀;模型对读回一律严格拒绝,真实到达时诚实停。
    - JL_LRCT CON(0xfd3600,SDK br28.h ls_base+map_adr(0x36,0x00),CON 标注
      __WO):0x6067aae `rmw.and.nmodimm &=0xFFFFFFC1`、0x60674fc &=~1、
      0x6067500/0x6067506 |=0x40/=0x1(app 全量恰三处 li 引用 0xfd3600、
      ROM 整块零引用)。RMW 按位记录(PartialWord,清位/置位不经读回,与
      gpio RMW、q32s PMU_CON 位记录同语义)。
      trim 等待函数 0x606750c 的轮询形状(反汇编钉死,注意 ld.w 的 n 字段
      scale=4):0x6067514 `ld.w r0=[r3+0]` 读 CON;0x6067516 `beq.and.ri`
      测 bit0(bit0==0 → 跳 0x60675e2 直接返回,trim 未使能);0x606751a
      再读 CON、0x606751c `beq.and.ri` 测 bit7——ISA 语义(硬件 12/12 确认)
      是「(r&(1<<n))==0 时跳转」,目标回 0x606751a,即**固件等 bit7 置位**
      (busy/完成标志置 1 才退出;旧记录「死等清零」方向反了,按编码修正);
      退出后 0x6067520 `ld.w r2=[r3+1*4]` 读 NUM(0xfd3604)取 trim 结果。
      opt-in trim_done_projection(lrct_trim_done_projection,需本投影开启):
      CON 读回=已记录 RMW 已知值(bit0=1 使能、bit6=1)但 **bit7 强制置 1**
      ——「trim 已完成」的条件投影(与 SFC PLL-lock bit15 先例同模式),
      措辞明示:busy 位布局由轮询行为推断,SDK __WO 无读回定义,非硅测量;
      RMW mask 外的位不臆造(返回 int 未写位为 0,轮询只消费 bit0/bit7)。
      未开启时 CON 读回保持 Blocked(300,236 前沿形状)。
      NUM 读回默认一律严格停:trim 计数是硅测量值,无任何测量证据。
      唯一放行途径是显式参数 lrct_trim_result_value(需 trim-done 投影,
      与堆信息 4 字节 p11_heap_info_bytes fixture 同族的 opt-in 数据
      fixture):调用方传入探路值后 NUM 读回该值并计数;探路值选择由
      调用方的推导链负责(固件消费路径:0x6067526 r2==0 → 重启重试;
      0x606753e/44 64 位分子 0x1_C9C38000=7.68e9(注意 0x606753e 立即数
      原始字节 00 80 c3 c9=0xC9C38000,十进制 3385032704)、0x6067546
      64/32 无符号除法 r2=7.68e9/NUM 得换算频率写 [0x129D98],首次经
      20 项历史填充平均后写 [0x129D88],0x6067ac8 以它为 Hz 调 LRC 周期
      写——7.68e9/38400=200000 整除精确,即标称 200kHz 的恒等计数),
      值本身 derived-not-measured、不模拟 LRC 校准效果(频率)。
    """

    TRIM_DONE_BIT=0x80

    def __init__(self,*,trim_done_projection=False,trim_result_value=None,con_readback_projection=False):
        self.stores={address:{'values':[],'writes':0} for address in
                     (P11_LPTMR0_CON0,P11_LPTMR0_CON1,P11_LPTMR0_CON2,
                      POWER_INIT_PMU_CFG0,POWER_INIT_PMU_CFG1)}
        self.lrct=PartialWord();self.lrct_rmws=0
        self.trim_done_projection=trim_done_projection;self.lrct_con_reads=0
        self.trim_result_value=trim_result_value;self.lrct_num_reads=0
        # con_readback_projection(opt-in,需 lp_timer_init_projection):P11_LPTMR0
        # CON0/CON1/CON2 的读回=最后写值投影。SDK p11_sfr.h 把 CON0/CON1/CON2/
        # PRD/RSC 都标 __RW(只有 CNT 是 __RO),即这些配置字是可回读的——读回
        # =最后写值与 SFC 时钟 +8(0xfd1c08,divider_readback_is_last_write)
        # 同族先例一致;只在固件已写过至少一次后返回最后写值,从不臆造复位值。
        # 默认关:CON 读回保持 Blocked(lp_timer_register_readback_not_modeled,
        # 301,540 前沿形状)。PMU 配置族 0xf2c04c/0xf2c050 不在投影范围(SDK
        # 无名,读回语义无证据,pmu_task 后的回读是独立问题)。
        self.con_readback_projection=con_readback_projection;self.con_reads=0

    def handles(self,address):
        return address in self.stores

    def handles_lRCT(self,address):
        return address==LRCT_CON

    def handles_lrct_num(self,address):
        # NUM 只路由读(诚实停);RMW/整字写由 dispatch 层分别拒绝,不经此处
        return address==LRCT_NUM

    def write(self,address,value):
        if address not in self.stores:raise ValueError('not a modeled LP-timer init register')
        entry=self.stores[address];entry['values'].append(value);entry['writes']+=1
        if len(entry['values'])>64:entry['values'].pop(0)

    def rmw(self,address,kind,operand):
        if not self.handles_lRCT(address):raise ValueError('not the modeled LRCT CON')
        if kind not in ('and','or'):raise Blocked('lp_timer_rmw_form_not_modeled',address=hex(address),
                                                  operation=kind)
        self.lrct.apply(kind,operand&0xffffffff);self.lrct_rmws+=1

    def read(self,address):
        if address in LPTMR_CON_ADDRESSES and self.con_readback_projection:
            # 读回=最后写值(SFC 时钟 +8 先例):P11_LPTMR0 CON 在 SDK p11_sfr.h
            # 标 __RW,可读回;只在固件已写过至少一次后返回最后写值,从不臆造
            # 复位值。本前缀唯一触发点=0x6067668 的 LP 定时器配置 RMW
            # (0x6067672 读 CON2、0x606767e 复合 rmw.and.nmodimm 再读)。
            entry=self.stores[address]
            if entry['writes']>0:
                self.con_reads+=1
                return entry['values'][-1]
        if address==LRCT_CON and self.trim_done_projection:
            # 0x606751a..0x606751c 轮询在 bit7==0 时回跳重读——固件等 bit7
            # 置位才退出并读 NUM。该位读回为 1 是显式「trim 已完成」条件投影
            # (SFC PLL-lock bit15 先例同模式),不是测得复位值;其余位沿用已
            # 记录 RMW 的已知值(bit0 使能=1、bit6=1),mask 外的位不臆造。
            self.lrct_con_reads+=1
            return (self.lrct.value|self.TRIM_DONE_BIT)&0xffffffff
        if address==LRCT_NUM:
            # trim 结果计数(SDK __RW NUM):硅测量值,默认不臆造数值,严格停。
            # 显式探路值(trim_result_value,derived-not-measured)按堆信息
            # fixture 先例放行,读回并计数;未提供时严格停语义不变。
            if self.trim_result_value is not None:
                self.lrct_num_reads+=1
                return self.trim_result_value
            raise Blocked('lrct_trim_result_not_modeled',address=hex(address))
        raise Blocked('lp_timer_register_readback_not_modeled',address=hex(address))

    def report(self):
        result={'stores':{hex(address):{'values':[hex(v) for v in entry['values']],'writes':entry['writes']}
                          for address,entry in self.stores.items()},
                'lrct_con':{'address':hex(LRCT_CON),'known_mask':hex(self.lrct.known),
                            'known_value':hex(self.lrct.value),'rmw_writes':self.lrct_rmws},
                'sdk_anchors':'p11_sfr.h P11_LPTMR0_BASE=0xf2a000+map_adr(0x09,0); br28.h JL_LRCT=0xfd0000+map_adr(0x36,0) CON __WO NUM __RW; 0xf2c04c family unnamed in SDK headers',
                'write_only':True,'readback_observed':False,
                'bit_semantics_or_completion_verified':False,
                'lrc_trim_result_simulated':self.trim_result_value is not None}
        if self.trim_done_projection:
            result['lrct_trim_done_projection']={
                'address':hex(LRCT_CON),'trim_done_bit':hex(self.TRIM_DONE_BIT),
                'reads':self.lrct_con_reads,
                'projected_word':hex((self.lrct.value|self.TRIM_DONE_BIT)&0xffffffff),
                'known_mask':hex(self.lrct.known),
                'readback_projection':('bit7 projected as trim-complete; condition projected, '
                    'not a measured reset value. The poll at 0x606751a..0x606751c (beq.and.ri, '
                    'hardware-confirmed semantics "branch if (r & (1<<n)) == 0") loops back while '
                    'bit7 is CLEAR and falls through to read NUM at 0xfd3604 when bit7 is SET, so '
                    'the firmware waits for bit7 to be set; the busy-bit LAYOUT is inferred from '
                    'the poll behavior only — SDK marks CON __WO with no readback definition'),
                'completion_timing_modeled':False,
                'trim_result_register':{'address':hex(LRCT_NUM),'sdk_name':'JL_LRCT.NUM (__RW)',
                    'sole_firmware_access':'0x6067520 ld.w [r3+4] read after the poll exits',
                    'value_evidence':('none (silicon measurement); reads stay strictly blocked'
                        if self.trim_result_value is None else
                        'explicit probe value (derived-not-measured), see trim_result_fixture')}}
        if self.con_readback_projection:
            result['lp_timer_con_readback_projection']={
                'addresses':[hex(a) for a in LPTMR_CON_ADDRESSES],
                'reads':self.con_reads,
                'readback_semantics':('last firmware-written value; P11_LPTMR0 CON0/CON1/CON2 are '
                    '__RW in SDK p11_sfr.h so a read-back is defined, and returning the last write '
                    'matches the SFC clock +8 (0xfd1c08) divider precedent — only after at least '
                    'one firmware write, never a fabricated reset value'),
                'evidence':('0x6067668 LP-timer config function (called from 0x6067b32 with r0=2): '
                    '0x606766c li.u32 r0=0xF2A908 (P11_LPTMR0 CON2); 0x6067672 ld.w r1=[r0] reads '
                    'CON2; 0x6067674 and.nmodimm r1&=~0xF (clear CLK_SEL bits0-3); 0x6067678 '
                    'or.modimm r1|=3; 0x606767c st.w [r0]=r1 (write CON2=3); 0x606767e '
                    'rmw.and.nmodimm [r0]&=~0xF0 (clear CLK_DIV bits4-7). SDK p11_app.h: '
                    'LP_TMR0_CLK_SEL=SFR(CON2,0,4) bits0-3, LP_TMR0_CLK_DIV=SFR(CON2,4,4) bits4-7. '
                    'PMU config 0xf2c04c/0xf2c050 are NOT in this projection (SDK-unnamed, readback '
                    'semantics unproven; their later pmu_task byte read is a separate decision)')}
        if self.trim_result_value is not None:
            result['lrct_trim_result_fixture']={
                'address':hex(LRCT_NUM),'value':hex(self.trim_result_value),
                'reads':self.lrct_num_reads,
                'value_is_derived_not_measured':True,
                'derivation':('probe value: the consumer divides the firmware constant '
                    '0x1_C9C38000 (7,680,000,000 = r1=1:r0=0xC9C38000 at 0x606753e/0x6067544; '
                    'the immediate raw bytes are 00 80 c3 c9 — earlier notes writing 0xC9C9C9C0 '
                    'transcribed the decimal 3385032704 wrongly) by NUM to get the LRC frequency '
                    'in Hz ([0x129D98] raw, [0x129D88] 20-entry average, consumed at 0x6067ac8 as '
                    '1000000/freq); 7,680,000,000/38,400 = 200,000 exactly, the firmware\'s own '
                    'nominal LRC constant (first LRC write 0x6067ab2 r0=200000, prd=5), so the '
                    'probe value is the identity count of a perfectly nominal 200 kHz LRC. It is '
                    'a DERIVED ARITHMETIC PROBE, not a silicon measurement; the real NUM is a '
                    'per-chip trim measurement and the LRC calibration effect is not simulated')}
        return result


class MemPwrConRegisters:
    """P11_SYSTEM.MEM_PWR_CON(0xf2a62c)位 RMW 的 write-only 位记录(opt-in)。

    SDK p11_sfr.h 把 MEM_PWR_CON 标 __RW(P11_SYSTEM_TypeDef offset 11),但 __RW
    与 __WO 是同一个宏(都是 volatile)、R/W 区分只在注释;p11_app.h 有字段位定义
    (bits0-5:MEM_PWR_CPU_CON/RAM0_3/RAM4_5/RAM6_7/RAM8_9/PERIPH_CON,六个内存
    电源域)。固件访问(app 字面量 3 处、ROM 0 处,字节扫描钉死):
    - 0x606772c(位域配置助手 0x60676ec):四次 `rmw.or.r [0xf2a62c]|=r0`
      (置位内存电源位,0x6067730/52/74/96,每次之间 call RAM 0x12e29c)。
    - 0x6067840(调用者 0x60677a4,紧接 helper call 之后):`li r0=0xf2a62c` 后
      0x6067844 `ld.w r0=[r0]` 读回,值被下一指令 0x6067846 `ld.bu r0=b[r4]`
      立即覆盖——死读、从不消费,归 probe_boot 的 opaque 死读门放行,不臆造读回值。
    - 0x6067e82(独立 power-off 例程):多次 `rmw.and.invr [0xf2a62c]&=~r0`
      (清位,电源关路径)。
    无任何整字写。OR/AND 都是部分字 RMW、读回值(死读)从不消费——故按
    cpu1_control kick 位 / q32s PMU_CON |=1 / LRCT 位 RMW 同族,write-only 位
    记录(PartialWord,不经读回),读回一律严格停;死读由 probe_boot 的 opaque
    死读门(证明值从不消费)单独放行,不臆造读回值。
    """
    def __init__(self):
        self.bits=PartialWord()
        self.rmw_writes=0
        self.dead_reads=0

    def handles(self,address):
        return address==P11_MEM_PWR_CON

    def rmw(self,kind,operand):
        if kind not in ('or','and'):
            raise Blocked('mem_pwr_con_rmw_form_not_modeled',address=hex(P11_MEM_PWR_CON),operation=kind)
        self.bits.apply(kind,operand&0xffffffff)
        self.rmw_writes+=1

    def observe_dead_read(self):
        self.dead_reads+=1

    def read(self,address):
        raise Blocked('mem_pwr_con_readback_not_modeled',address=hex(address))

    def report(self):
        return {'address':hex(P11_MEM_PWR_CON),
                'known_mask':hex(self.bits.known),'known_value':hex(self.bits.value),
                'rmw_writes':self.rmw_writes,'dead_reads':self.dead_reads,
                'sdk_anchors':'p11_sfr.h P11_SYSTEM.MEM_PWR_CON = P11_SYSTEM_BASE+11*4 (__RW macro == __WO == volatile, R/W distinction comment-only); p11_app.h MEM_PWR_CPU_CON/RAM0_3/RAM4_5/RAM6_7/RAM8_9/PERIPH_CON bits0-5',
                'write_only':True,'readback_observed':True,'readback_value_consumed':False,
                'observed_shapes':('0x60676ec helper 4x rmw.or.r |=r0 (set memory-power bits); '
                                   '0x6067e82 power-off routine rmw.and.invr &=~r0 (clear); '
                                   '0x6067844 ld.w read is dead (overwritten by 0x6067846 ld.bu)')}


class P33WindowByteRegisters:
    """P33 窗「无名寄存器族」的 write-only 字节序列记录(opt-in)。

    证据(2026-11-12 路 AV 反汇编+动态 watch 钉死):
    - SDK p33_sfr.h(sha256 adde010f…,与 data/dependencies.json 一致)的 P33 命名
      止于 P3_LS_PLL=P33_ACCESS(0xd5);本族偏移 0x150-0x153/0x250-0x253/
      0x258-0x259/0x267/0x268 全部超过命名上限、无 __RW/__RO/__WO 类型标注、
      无字段位定义(整个 p33_sfr.h 都是纯地址宏)——SDK 无名即不声称硅语义。
    - app 字面量恰 3 处(0x6067b70/0x6067470/0x6067d28),ROM 整块 0 处;其余
      引用全部经基址寄存器算术到达(r8=0xF2C548/r6=0xF2C940/r9=0xF2C960 及
      ±常数偏移),字面量扫描不能穷举。
    - 固件访问形状(三个引用函数逐一反汇编钉死):全部是**单字节写**
      (st.b/st.b.l/pair 的 st.b 半),没有任何读回、RMW 或整字写观测。
      * P33 窗配置函数 0x6067b66(walker 0x6067cb4 的 12 项表 0x0609E964,
        每项 12 字节{cfg,b1,w4,w8}在 RAM、cfg 低 nibble=bit/高 nibble=port):
        表项 w4!=0 时 pair 0x6067be4 `uextra.rrii.p∥st.b b[r6+8]=r0` 写
        [0xF2C948]=(1<<index)&0xFF(302,581 停点)+0x6067c36 写 [0xF2C94C];
        w4==0 时同两处形状写 [0xF2C548]/[0xF2C54C](清位路径 ~(1<<index));
        函数尾无条件 0x6067c54/c56 写 [0xF2C940]/[0xF2C944]、0x6067c4a/c50
        写 [0xF2C960]/[0xF2C964](全部 (1<<index) 低/高字节)。
      * 独立函数 0x6067464:[0xF2C960]=4/[0xF2C964]=0(st.b.l r2+32/+36),
        再按参数 r4 分支写 [0xF2C940]=4/[0xF2C944]=0(r4!=0)或
        [0xF2C540]=251/[0xF2C544]=255(r4==0,st.b)。
      * walker 完成后的清尾 0x6067d26..d38(r1=0xF2C960):[0xF2C99C]=0xFF
        (st.b.l r1+60)、[0xF2C9A0]=0xFF(+64)、[0xF2C960]/[0xF2C964]=0xFF
        ——16 位位图寄存器对全部置位。(路 AZ 修正:0x6067d32 的 st.b.l
        原始字段 n=64、r1=0xF2C960 → 实际字节地址 0xF2C9A0=P33_ACCESS(0x268);
        路 AV 记录的 0xf2ca00(=0xF2C960+160)是十进制/十六进制换算笔误,
        按原始字段解码修正。)
      位图语义(index 0..11 各一位、低/高字节相邻两个字节地址构成 16 位对)
      是从写入值形状归纳的描述,不是 SDK 字段定义;不发明未观测位、不建模
      寄存器对固件的电气效果。
    - 建模选择:与 lp_timer SYS_DIV/CLK_CON0 整字写记录同族的 write-only
      记录,但按字节粒度(观测形状只有字节写);读回一律严格停
      (p33_window_readback_not_modeled),RMW 无观测形状一律拒绝,
      整字写不路由到本投影(handles() 不含这族,4 字节写走普通路径诚实停)。
    """
    ADDRESSES=(0xf2c540,0xf2c544,0xf2c548,0xf2c54c,
               0xf2c940,0xf2c944,0xf2c948,0xf2c94c,
               0xf2c960,0xf2c964,0xf2c99c,0xf2c9a0)

    def __init__(self):
        self.stores={address:{'values':[],'writes':0} for address in self.ADDRESSES}
        self.byte_writes=0

    def handles(self,address):
        return address in self.stores

    def write_byte(self,address,value):
        if address not in self.stores:raise ValueError('not a modeled P33 window byte register')
        if not isinstance(value,int) or not 0<=value<=0xff:raise ValueError('byte value expected')
        entry=self.stores[address];entry['values'].append(value);entry['writes']+=1
        self.byte_writes+=1

    def read(self,address):
        raise Blocked('p33_window_readback_not_modeled',address=hex(address))

    def rmw(self,kind,operand):
        raise Blocked('p33_window_rmw_not_modeled',address=hex(self.ADDRESSES[0]),operation=kind)

    def report(self):
        return {'addresses':[hex(a) for a in self.ADDRESSES],
                'byte_writes':self.byte_writes,
                'stores':{hex(a):{'values':[hex(v) for v in e['values'][-16:]],
                                  'writes':e['writes']}
                          for a,e in sorted(self.stores.items()) if e['writes']},
                'sdk_naming_limit':'p33_sfr.h names end at P3_LS_PLL=P33_ACCESS(0xd5); '
                                   'offsets 0x150-0x153/0x250-0x253/0x258-0x259/0x267/0x268 '
                                   'are unnamed (address macros only, no __RW/__RO/__WO, no bit fields)',
                'write_only':True,'readback_not_modeled':True,
                'observed_shapes':('0x6067b66 P33-window init: pair uextra.rrii.p||st.b [0xF2C948] '
                                   '(w4!=0) / [0xF2C548] clear path (w4==0), st.b [0xF2C94C]/[0xF2C54C], '
                                   'tail st.b/st.b.l [0xF2C940]/[0xF2C944]/[0xF2C960]/[0xF2C964]; '
                                   '0x6067464 st.b.l/st.b [0xF2C960]=4/[0xF2C964]=0/[0xF2C940]=4/'
                                   '[0xF2C944]=0/[0xF2C540]=251/[0xF2C544]=255; '
                                   'walker tail 0x6067d2e..d38 st.b.l 0xFF into [0xF2C99C]/[0xF2C9A0]/'
                                   '[0xF2C960]/[0xF2C964]; byte stores only, no readback, no RMW, '
                                   'no full-word store observed'),
                'assumption':('write-only byte-sequence record; per-bitmap semantics (12 indices, '
                              'low/high byte pairs) inferred from written-value shapes, not an SDK '
                              'field definition; no silicon readback or electrical effect modeled')}


class P33PortSelRegisters:
    """P3_PORT_SEL0..11(0xf2c300..0xf2c32c)的 write-only 整字记录(opt-in,路 AZ)。

    证据(2026-11-12 路 AZ 反汇编+全量引用扫描钉死):
    - SDK p33_sfr.h 把 P3_PORT_SEL0..15 全部命名为 P33_ACCESS(0xc0..0xcf)
      (P3_PORT_SEL0=0xf2c300);与整个 p33_sfr.h 一样只有地址宏、无
      __RW/__RO/__WO 类型标注、无字段位定义——寄存器名与固件真实写形状
      是全部证据,不声称硅语义。
    - app 字面量 0 处、ROM 整块 0 处(0x00f2c300..0x00f2c30c 全部扫描);
      唯一写者是 post-power_init 的 P33 窗配置函数 0x6067b66(walker
      0x6067cb4 两轮表循环调入):函数尾 0x6067c3e `r1=0xF2C300` +
      0x6067c42 `st.w.idxs [r1+r5<<2]=r0`,r5=表 index(第一轮 0..11、
      第二轮 0..2),值 r0=查表函数 0x60672C8(cfg) 的真实执行结果
      (cfg=0x11 实测 0x12)。访问面=index 0..11 → 0xf2c300..0xf2c32c
      共 12 个字;本 boot 表实测写 SEL1..4(第一轮 index 1..4,cfg=
      0x11/0xE0/0x05/0x15),第二轮 index 0..2 走 P3_AWKUP 次路径
      (cfg=0xE2/0xE4/0xE5,不写 PORT_SEL)。
    - 读回证据:app+ROM 全量唯一读是 0x0606745e `ld.w.l r0=[r8+120]`
      (r8=0xf2c120,n 缩放后=+480 → 0xf2c300=P3_PORT_SEL0)——arch_startup
      的 opaque 死读先例(R0=None 直到调用方覆盖,调用者 0x0603502c/
      0x06035306 已钉死都会立即杀死 R0),值从不被消费。没有任何被消费
      的读回观测——读回一律严格停(port_sel_readback_not_modeled),
      显式 kind 优先于泛 unmapped;死读由 probe 侧 opaque 死读门单独
      放行(不经本投影)。
    - RMW 无任何观测形状,一律拒绝(port_sel_rmw_not_modeled)。
    - 建模选择:与 lp_timer SYS_DIV/CLK_CON0(路 W)整字写 write-only
      记录同族——st.w.idxs 全字覆盖写、旧值无关(与 st.d.sp.l「旧值
      无关」同论证),按字粒度记录写值序列;不为无人消费的读发明值
      (与 p33_analog_config 的合成读回不同族——那族固件真实读回)。
    """
    ADDRESSES=tuple(0xf2c300+4*i for i in range(12))

    def __init__(self):
        self.stores={address:{'values':[],'writes':0} for address in self.ADDRESSES}
        self.word_writes=0

    def handles(self,address):
        return address in self.stores

    def write(self,address,value):
        if address not in self.stores:raise ValueError('not a modeled P3_PORT_SEL register')
        if not isinstance(value,int) or not 0<=value<=0xffffffff:raise ValueError('unsigned32 word expected')
        entry=self.stores[address];entry['values'].append(value);entry['writes']+=1
        self.word_writes+=1

    def read(self,address):
        raise Blocked('port_sel_readback_not_modeled',address=hex(address))

    def rmw(self,kind,operand):
        raise Blocked('port_sel_rmw_not_modeled',address=hex(self.ADDRESSES[0]),operation=kind)

    def report(self):
        return {'addresses':[hex(a) for a in self.ADDRESSES],
                'word_writes':self.word_writes,
                'stores':{hex(a):{'values':[hex(v) for v in e['values'][-16:]],
                                  'writes':e['writes']}
                          for a,e in sorted(self.stores.items()) if e['writes']},
                'sdk_naming':'P3_PORT_SEL0..15 = P33_ACCESS(0xc0..0xcf) in p33_sfr.h '
                             '(address macros only, no __RW/__RO/__WO, no bit fields); '
                             'modeled face is index 0..11 = 0xf2c300..0xf2c32c (the two '
                             'walker-table loops of 0x6067cb4)',
                'write_only':True,'readback_not_modeled':True,
                'observed_shapes':('0x6067b66 tail 0x6067c3e/c42: r1=0xF2C300 then '
                                   'st.w.idxs [r1+index<<2]=lookup(cfg) (full-word store, old value '
                                   'irrelevant); boot table writes SEL1..4 via indexes 1..4; the only '
                                   'observed read is the arch_startup proven-dead read of P3_PORT_SEL0 '
                                   'at 0x0606745e (opaque gate, R0 never consumed); no RMW observed'),
                'assumption':('write-only word-sequence record; the stored value is the real '
                              'executed lookup result, never invented; no silicon readback or '
                              'pin-mux electrical effect modeled')}


class JLUartInitWriteRecords:
    """JL_UART 初始化配置字的 write-only 整字记录(opt-in,路 M/sim-uartwr)。

    证据(2026-11-13 路 M 反汇编+SDK bitcode 交叉钉死):
    - **布局钉死(修正路 F 的「0xfd2004=JL_UART.BAUD|BUF」命名)**:实际 SDK
      构建对象 uart_v1.c.o 的 bitcode 声明 `%struct.JL_UART_TypeDef = type
      {i32 x12}`——12 个成员各占一个 32 位字(成员 k 在 +4k:CON0=0/CON1=4/
      BAUD=8/BUF=0xc/OTCNT=0x10/TXADR=0x14/TXCNT=0x18/RXSADR=0x1c/RXEADR=0x20/
      RXCNT=0x24/HRXCNT=0x28/CON2=0x2c);实际 QCY 固件写序列逐点互证
      (uart_init 内联体 0x6034466/0x603446c/0x6034472 写字 0/1/11、
      uart_set_baudrate 0x601d286 写字 2=分频值、uart_refresh_otcnt 0x601d1f2
      写字 4、RX 三连写 0x6034668..cc 写字 7/8/9)。公开 br28.h 的 u16/u8
      紧凑布局与该构建对象不一致(BR31 等其它系列公开头确有紧凑布局,是
      布局分歧非笔误;以实际编译对象+固件字节为准):0xfd2004 是 **CON1 字**,
      不是「BAUD|BUF」;真正的 BAUD 分频写在 0xfd2008。路 F 的读侧投影
      (jl_uart_con_projection,0xfd2000 合成 0+读回=当前值)不受影响——
      两种布局在字 0 上一致。
    - **写形状**:全部是 st.w 族全字覆盖写(旧值无关),值全为真实指令
      计算结果(CON1=0/CON2=0 来自 uart_init 清零、BAUD=分频除法结果、
      OTCNT=clk/1e6*阈值、RXSADR/RXEADR/RXCNT=DMA 环形缓冲地址/大小),
      无一处发明值。
    - **读回/RMW**:白名单字零读回观测(uart_dump 不在本 boot 前缀),读回
      一律严格停(显式 kind 优先泛 unmapped);RMW 无观测形状,经读侧严格停。
      字 0(CON0)不在本容器:CON0 的读/写/RMW 走路 F 的 registers 投影。
      **路 AA 例外(opt-in con1_readback,需 jl_uart_init_write_records)**:
      CON1(+4,三基址对称)升级为「写记录+读回=最后写值」——lp_timer CON
      (路 AL)/fd31 ADC CON(路 BN)同族先例。首个真实消费者=uart_fifo_reset
      的复合 RMW 0x60340d8 `rmw.and.nmodimm [r0+4]&=0xFFFFFFEF`(SDK uart_v1.c
      序列:临界区内 CON1|=16(bit4=RX DMA 重启位)→写 RXSADR/RXEADR/RXCNT→
      CON1&=-17 清 bit4;读回是真实消费,读值参与写回)。RMW 经 probe_boot
      通用 mmio 路径执行:内部读=投影最后写值、按位合成、**写回照常落记录**
      (完整 RMW 路径,不只读)。本事务 CON1 已被 uart_init 0x603446c 整字写 0,
      读 0、写回 0&0xFFFFFFEF=0,平凡零发明。**条件假设(codex 风险点)**:
      br28.h __RW 只证明 SDK 认为可读回,不能证明无硬件自更新/自清零位——
      「读回=软件配置值」是 derived-not-measured 的条件假设;若真硬件会在
      CON1 其它位自更新,投影会失真(如实标注)。只扩 CON1:BAUD/OTCNT/
      RXSADR/RXEADR/RXCNT/CON2 的读回仍严格停(它们也无读回观测,无消费者,
      不为无人消费的读发明值);从未写过(如 UART1/2)仍诚实停,不臆造复位值。
    - **运行效果不建模**:白名单只记录「初始化配置写已执行」——不推导波特
      率、不模拟 RX/TX DMA 数据流、不发明 UART IRQ(RX 活动只会在固件读
      HRXCNT/轮询 CON0 状态位时显形,那些读一律严格停)。
    - TXADR(+0x14)/TXCNT(+0x18)/BUF(+0xc)不进白名单:发送路径字段不在
      初始化序列;TXCNT 写=启动 TX 硬件活动的信号(JieLi 官方示例「写
      TXADR/TXCNT 后等 CON0 完成位」),出现即诚实停。HRXCNT(+0x28,__RO)
      保持严格。
    """
    CON1_ADDRESSES=tuple(base+0x4 for base in JL_UART_CON_WORDS)

    def __init__(self,*,con1_readback=False):
        self.stores={address:{'values':[],'writes':0} for address in JL_UART_INIT_WORDS}
        self.word_writes=0
        # con1_readback(opt-in,路 AA/sim-con1rb):CON1 读回=最后写值投影,详见
        # 类注释「路 AA 例外」段;默认关,读回保持 3,073,918 前沿形状。
        self.con1_readback=con1_readback;self.con1_reads=0

    def handles(self,address):
        return address in self.stores

    def write(self,address,value):
        if address not in self.stores:raise ValueError('not a modeled JL_UART init word')
        if not isinstance(value,int) or not 0<=value<=0xffffffff:raise ValueError('unsigned32 word expected')
        entry=self.stores[address];entry['values'].append(value);entry['writes']+=1
        self.word_writes+=1

    def read(self,address):
        # 路 AA:CON1 读回=最后写值(只在固件已写过至少一次后返回,lp_timer
        # CON 路 AL 同款「从未写过仍诚实停」);其余白名单字读回一律严格停。
        if self.con1_readback and address in self.CON1_ADDRESSES:
            entry=self.stores[address]
            if entry['writes']>0:
                self.con1_reads+=1
                return entry['values'][-1]
        raise Blocked('jl_uart_init_word_readback_not_modeled',address=hex(address))

    def report(self):
        result={'addresses':[hex(a) for a in JL_UART_INIT_WORDS],
                'word_writes':self.word_writes,
                'stores':{hex(a):{'values':[hex(v) for v in e['values'][-16:]],
                                  'writes':e['writes']}
                          for a,e in sorted(self.stores.items()) if e['writes']},
                'sdk_naming':('compiled uart_v1.c.o layout {i32 x12}: CON0=+0/CON1=+4/BAUD=+8/'
                              'BUF=+0xc/OTCNT=+0x10/TXADR=+0x14/TXCNT=+0x18/RXSADR=+0x1c/'
                              'RXEADR=+0x20/RXCNT=+0x24/HRXCNT=+0x28/CON2=+0x2c per UART '
                              '(0xfd2000/0xfd2100/0xfd2200); the public br28.h packed u16/u8 '
                              'offsets disagree with this build and are NOT used — 0xfd2004 is '
                              'the CON1 word, not BAUD|BUF (route-F naming corrected here)'),
                'write_only':not self.con1_readback,
                'readback_not_modeled':not self.con1_readback,
                'whitelisted_words':('CON1 +4 / BAUD +8 / OTCNT +0x10 / RXSADR +0x1c / '
                                     'RXEADR +0x20 / RXCNT +0x24 / CON2 +0x2c (the words the '
                                     'boot uart_init+uart_set_baudrate+uart_dma_init sequence '
                                     'actually writes); TXADR/TXCNT/BUF/HRXCNT NOT whitelisted'),
                'observed_shapes':('all full-word st.w-family stores (old value irrelevant): '
                                   '0x603446c [r2+4]=r3 (CON1=0, pair B half), 0x6034472 '
                                   '[r2+44]=r3 (CON2=0), 0x601d286 [r7+8]=r0 (BAUD divider), '
                                   '0x601d1f2 [r2+16]=r3 (OTCNT, gated on CON0 bit6), '
                                   '0x6034668..cc [r6+28/32/36] (RXSADR/RXEADR/RXCNT); '
                                   'the first real readback consumer is the uart_fifo_reset '
                                   'compound RMW 0x60340d8 rmw.and.nmodimm [r0+4]&=0xFFFFFFEF '
                                   '(bit4 = the RX-DMA restart bit clear; the read value feeds '
                                   'the write-back, so it is a genuine consumption)'),
                'assumption':('write-only word-sequence record of pure configuration writes '
                              '(derived-not-measured); stored values are real executed '
                              'instruction results, never invented; no baud-rate runtime '
                              'effect, no RX/TX DMA data flow and no UART IRQ is modeled — '
                              'this releases the initialization config writes only, it does '
                              'NOT verify UART transmission/reception' +
                              ('' if not self.con1_readback else
                               '; with the opt-in CON1 readback projection CON1 reads return '
                               'the last firmware-written value — see con1_readback_projection'))}
        if self.con1_readback:
            result['con1_readback_projection']={
                'addresses':[hex(a) for a in self.CON1_ADDRESSES],
                'reads':self.con1_reads,
                'readback_semantics':('last firmware-written value (route AA); the reference '
                    'br28.h marks JL_UART_TypeDef CON1 __RW, so a read-back is defined, and '
                    'returning the last write matches the lp_timer CON (route AL, 0xf2a908) '
                    'and fd31 ADC CON (route BN, 0xfd3100) precedents — only after at least '
                    'one firmware write, never a fabricated reset value; CON0 keeps its own '
                    'route-F projection and is deliberately NOT given this semantics here'),
                'conditional_assumption':('the __RW annotation proves the SDK believes CON1 '
                    'reads back, NOT that every bit mirrors software: hardware-updated or '
                    'self-clearing bits outside bit4 (if any exist) would read back '
                    'differently on silicon. The projection is the explicit hypothesis '
                    '"readback == the software-configured value" — derived-not-measured, '
                    'conditional pass; the first consumer is the uart_fifo_reset compound '
                    'RMW at 0x60340d8, and in this prefix CON1 was whole-word written 0 by '
                    'uart_init (0x603446c) before the first read, so the projected RMW reads '
                    '0 and writes back 0&0xFFFFFFEF=0 — a trivially zero-invention release'),
                'rmw_path':('the uart_fifo_reset compound RMW runs the FULL generic MMIO '
                    'path (probe_boot mem.read -> bit-AND -> mem.write): the internal read '
                    'is served by this projection and the write-back lands in the write '
                    'records as a real transaction (CON1 gains a second recorded write of '
                    'the computed value); it is never short-circuited'),
                'other_words_stay_strict':('BAUD/OTCNT/RXSADR/RXEADR/RXCNT/CON2 keep the '
                    'strict jl_uart_init_word_readback_not_modeled stop (no readback '
                    'consumer observed, so no read value is invented for them)')}
        return result


class JLOmapWriteRecords:
    """JL_OMAP 逐引脚输出功能选择字的 write-only 整字记录(opt-in,路 R/sim-gpioface)。

    识别与证据(2026-11-13 路 R 反汇编+钉定参考 SDK 交叉钉死,全文见
    JL_OMAP_OUT_WORDS 常量注释):
    - 0xfd5800..0xfd58c0 = SDK io_omap.h 的 JL_OMAP(map_adr(0x58,0)=0xfd5800,
      **SDK 有名**,非反推):GPIO 交叉开关的逐引脚输出功能选择寄存器组,
      49 个字(A16/B12/C9/G9/USBDP/USBDM/PP0,成员数与 gpio_hw.h 端口掩码
      四方一致),4 字步进(SDK gpio_out_regs 表+QCY 固件表 0x609EA9C 逐字节
      互证;io_omap.h 的 u8 紧凑偏移与驱动表不一致,以编译对象+固件为准)。
    - 0xfd58b8/0xfd58bc = USBDP_OUT/USBDM_OUT:gpio 224/225(IO_PORT_DP/DM,
      PORTUSB=14)在固件解析函数 0x600127a 与 SDK gpio2crossbar_outreg 双侧
      同值特例;本前缀的首写来自 uart_init 的 TX 引脚复用
      gpio_set_function(PORTUSB,PIN0,PORT_FUNC_UART0_TX) → 写 0x9f=
      FO_UART0_TX((39<<2)|BIT(1)|BIT(0),io_omap.h 常量+gpio_func.c.o sw.bb
      `gpio_set_fun_output_port(add,159,1,1)` 双互证)。
    - **写形状**:st.w 族全字覆盖写(固件助手 0x60012de `[r3+0]=r4`,SDK
      store i32),值全为真实指令结果(固件 case 体的 FO_* 常量装载/SDK 调用
      参数),无一处发明值;RMW/半字/字节形状无观测,一律不经本容器。
    - **读回/RMW 严格停**:本 boot 前缀零读回观测(SDK disable 路径
      gpio_disable_fun_output_port 的 load+&~3+store 不在本前缀),读回显式
      kind `jl_omap_word_readback_not_modeled`;RMW 经读侧同样严格停。
    - **运行效果不建模**:只记录「引脚复用配置写已执行」——不推导交叉开关
      的信号布线、不模拟 OMAP 输出电平、不发明任何 IRQ;UART 收发/USB 功能
      一律未验证(与路 M 白名单同措辞)。core 由模型侧 current_core 记录;
      写 PC/步数由 probe 断言钉死(模型写路径无 PC 上下文,同全部写记录先例)。
    """
    def __init__(self):
        self.stores={address:{'values':[],'writes':0,'cores':[]} for address in JL_OMAP_OUT_WORDS}
        self.word_writes=0

    def handles(self,address):
        return address in self.stores

    def write(self,address,value,core):
        if address not in self.stores:raise ValueError('not a modeled JL_OMAP word')
        if not isinstance(value,int) or not 0<=value<=0xffffffff:raise ValueError('unsigned32 word expected')
        entry=self.stores[address];entry['values'].append(value);entry['writes']+=1
        entry['cores'].append(core);self.word_writes+=1

    def read(self,address):
        raise Blocked('jl_omap_word_readback_not_modeled',address=hex(address))

    def report(self):
        return {'addresses':[hex(a) for a in JL_OMAP_OUT_WORDS],
                'word_writes':self.word_writes,
                'stores':{hex(a):{'values':[hex(v) for v in e['values'][-16:]],
                                  'writes':e['writes'],'cores':e['cores'][-16:]}
                          for a,e in sorted(self.stores.items()) if e['writes']},
                'sdk_naming':('io_omap.h JL_OMAP = ls_base+map_adr(0x58,0) = 0xfd5800, per-pin '
                              'output-function select words of the GPIO crossbar: PA0..PA15_OUT/'
                              'PB0..PB11_OUT/PC0..PC8_OUT/PG0..PG8_OUT/USBDP_OUT/USBDM_OUT/PP0_OUT '
                              '(49 words, 4-byte stride); the u8-packed member offsets in io_omap.h '
                              'disagree with the compiled driver table and are NOT used — the SDK '
                              'gpio_out_regs table {0xfd5800,0xfd5840,0xfd5870,0,0,0xfd5894,'
                              '0xfd58c0} matches the QCY firmware table at 0x609EA9C byte-for-byte; '
                              '0xfd58b8/0xfd58bc are the USBDP/USBDM specials of gpio2crossbar_'
                              'outreg(gpio 224/225), and the observed first value 0x9f is '
                              'FO_UART0_TX=((39<<2)|BIT(1)|BIT(0)) from io_omap.h'),
                'write_only':True,'readback_not_modeled':True,
                'whitelisted_words':('the 49 per-pin OUT-select words the firmware gpio helper '
                                     '(0x600127a resolver: table base + pin*4, gpio 224/225 '
                                     'special-cased) can address — exactly the io_omap.h member '
                                     'list at the driver 4-byte stride; adjacent words beyond the '
                                     'declared pins (e.g. 0xfd58c4+, unused D/E groups) are NOT '
                                     'covered and stay unmapped'),
                'observed_shapes':('full-word st.w-family stores (old value irrelevant): the QCY '
                                   'helper 0x60012c2 body 0x60012de `[r3+0]=r4` inside the '
                                   '0x12b746/0x12b772 spinlock critical section (the compiled '
                                   'gpio_set_fun_output_port), writing FO_* constants computed by '
                                   'real instructions'),
                'assumption':('write-only word-sequence record of pin-mux configuration writes '
                              '(derived-not-measured); stored values are real executed '
                              'instruction results, never invented; no crossbar signal routing, '
                              'no output-level effect, no IRQ and no UART/USB traffic is modeled '
                              '— this releases the pin-mux configuration writes only, it does '
                              'NOT verify UART transmission/reception or USB function')}


class JLImapWriteRecords:
    """JL_IMAP 外设输入功能选择字的 write-only 整字记录(opt-in,路 V/sim-imapwr)。

    识别与证据(2026-11-13 路 V 反汇编+钉定参考 SDK 交叉钉死,全文见
    JL_IMAP_IN_WORDS 常量注释):
    - 0xfd5c00..0xfd5cf8 = SDK io_imap.h 的 JL_IMAP(map_adr(0x5c,0)=0xfd5c00,
      **SDK 有名**):GPIO 交叉开关的外设输入功能选择寄存器组,63 个字
      (FI_GP_ICH0..FI_CHAIN_RST,成员数=io_imap.h 成员表=gpio_hw.c.o 的
      PFI_* 枚举数),4 字步进是 csfr.h `__u8→unsigned int` 的显式编译布局
      (PFI_* 枚举 16604160..16604408 连续 4 字步进逐成员互证)。
    - 0xfd5c90 = FI_UART0_RX(成员 36):本前缀首写来自 uart_init 的 RX 引脚
      复用 gpio_set_function(PORTUSB,PIN0,PORT_FUNC_UART0_RX) → 固件 twin
      0x600132e 的 0x6001342 `[r4+0]=r3` 整字写 0x2f=47=USBDP_IN(io_imap.h
      输入通道常量;gpio2crossbar_inport(224)=47 与固件 resolver 0x60012e8
      双侧同值)——UART0 RX 与 TX 同用 USB DP 脚(单线 UART)。
    - **写形状**:st.w 族全字覆盖写(固件助手 0x6001342 `[r4+0]=r3`,SDK
      gpio_set_fun_input_port `store i32, align 4`,均在 unmask 临界区内),
      值全为真实指令结果(resolver 真实计算/PFI 常量由 case 体真实装载),
      无一处发明值;app 全镜像访问面扫描零读回(disable 路径 0x601968c
      `[r0+0]=0x0` 也是纯整字写),RMW 无观测、一律不经本容器。
    - **读回/RMW 严格停**:读回显式 kind `jl_imap_word_readback_not_modeled`;
      RMW 经读侧同样严格停。
    - **运行效果不建模**:只记录「输入复用配置写已执行」——不推导交叉开关
      的信号布线、不模拟输入电平/采样、不发明任何 IRQ;UART 收发/USB 功能
      一律未验证(与路 M/R 白名单同措辞)。core 由模型侧 current_core 记录;
      写 PC/步数由 probe 断言钉死(模型写路径无 PC 上下文,同全部写记录先例)。
    """
    def __init__(self):
        self.stores={address:{'values':[],'writes':0,'cores':[]} for address in JL_IMAP_IN_WORDS}
        self.word_writes=0

    def handles(self,address):
        return address in self.stores

    def write(self,address,value,core):
        if address not in self.stores:raise ValueError('not a modeled JL_IMAP word')
        if not isinstance(value,int) or not 0<=value<=0xffffffff:raise ValueError('unsigned32 word expected')
        entry=self.stores[address];entry['values'].append(value);entry['writes']+=1
        entry['cores'].append(core);self.word_writes+=1

    def read(self,address):
        raise Blocked('jl_imap_word_readback_not_modeled',address=hex(address))

    def report(self):
        return {'addresses':[hex(a) for a in JL_IMAP_IN_WORDS],
                'word_writes':self.word_writes,
                'stores':{hex(a):{'values':[hex(v) for v in e['values'][-16:]],
                                  'writes':e['writes'],'cores':e['cores'][-16:]}
                          for a,e in sorted(self.stores.items()) if e['writes']},
                'sdk_naming':('io_imap.h JL_IMAP = ls_base+map_adr(0x5c,0) = 0xfd5c00, the '
                              'peripheral-input function select words of the GPIO crossbar: '
                              'FI_GP_ICH0..FI_CHAIN_RST (63 words, 4-byte stride — the csfr.h '
                              '`__u8 -> unsigned int` SFR layout, confirmed member-by-member by '
                              'the gpio_hw.c.o PFI_* enumerators 16604160..16604408); FI_TOTAL '
                              'is a count sentinel, not a member. 0xfd5c90 is FI_UART0_RX '
                              '(member 36, PFI_UART0_RX=16604304) and the observed first value '
                              '0x2f=47 is USBDP_IN from io_imap.h — gpio2crossbar_inport(224)=47 '
                              'on both sides (SDK gpio_hw.c.o and firmware resolver 0x60012e8, '
                              'whose channel-number table at 0x609EAB8 matches gpio_in_regs '
                              '{1,17,29,0,0,38,49} byte-for-byte)'),
                'write_only':True,'readback_not_modeled':True,
                'whitelisted_words':('the 63 declared member words 0xfd5c00..0xfd5cf8 the '
                                     'firmware can address (gpio_set_fun_input_port twin '
                                     '0x600132e writes the PFI_* constant the dispatcher case '
                                     'bodies load, e.g. 0x6001522 r1=16604304; the disable '
                                     'helper 0x601968c zero-fills through the same pointers); '
                                     'adjacent words past FI_CHAIN_RST (0xfd5cfc+) are NOT '
                                     'covered and stay unmapped'),
                'observed_shapes':('full-word st.w-family stores (old value irrelevant): the QCY '
                                   'helper 0x600132e body 0x6001342 `[r4+0]=r3` inside the '
                                   'unmask critical section (the compiled gpio_set_fun_input_'
                                   'port, `store i32, align 4`), writing channel numbers '
                                   'computed by real instructions (resolver 0x60012e8)'),
                'assumption':('write-only word-sequence record of input-mux configuration writes '
                              '(derived-not-measured); stored values are real executed '
                              'instruction results, never invented; no crossbar signal routing, '
                              'no input-level effect, no IRQ and no UART/USB traffic is modeled '
                              '— this releases the input-mux configuration writes only, it does '
                              'NOT verify UART transmission/reception or USB function')}


class JLWlAudWriteRecords:
    """JL_WL_AUD->CON0(0xfe2200)的整字写记录 + 读回=最后写值(opt-in,路 AK)。

    识别与证据(2026-11-14 路 AG 反汇编预研 docs/wlaud-init-map.md + 本路复核):
    - **SDK 命名**:br28.h(sha 111e29ea…)`JL_WL_AUD_BASE = hs_base +
      map_adr(0x22, 0x00)` = 0xfe2200,JL_WL_AUD_TypeDef 单成员 `__RW __u32
      CON0`——无线音频(wireless-audio)子系统使能字,SDK 头有名、__RW 可回读。
    - **写形状**:全字覆盖写 ×2(旧值无关)——0x602ea68 函数头 0x602ea72
      `st.w.modimm [0xfe2200]=1`(WL_AUD 使能,先于一切音频初始化;当前
      3,079,241 前沿停点)与 DAC init 0x6071bca 内 0x6071cbe `st.w.l
      [r1]=r10`(SR idx≤5 时 1、否则 0,头尾各一次)。值全为真实指令结果,
      无一处发明值。
    - **读面**:app 全量 li 扫描(路 AG)读仅 1 处 0x6069a2e
      (`r0=[0xfe2200]; [r7+4]=r0`,电源管理快照保存族,同函数还存 [0xfe0900])
      ——不在本 boot 前缀;无任何 ready 位轮询/循环读。读回语义照 uart CON1
      最终形态(路 M→F→AA):写过返回最后写值;从未写过显式合成初始字 0
      (路 AM 先例,derived-not-measured 显式假设,非复位值声明)。本路径
      0xfe2200 在任何读之前必已写过(函数头即写),合成 0 是未被消费的兜底
      语义,报告计数分开记录。
    - **运行效果不建模**:时钟门真开/无线音频活动/IRQ 一律不建模——只记录
      「WL_AUD 使能配置写已执行」,不扩展为「音频/无线功能已验证」。
    - RMW 经 probe_boot 通用 mmio 路径完整执行(内部读=投影值、按位合成、
      写回照常落记录);本路径无 RMW 观测(app 唯一 RMW 0x60552ec |=1 在
      WL 域上电路径,不在本前缀)。
    """
    ADDRESS=JL_WL_AUD_CON0

    def __init__(self):
        self.stores={JL_WL_AUD_CON0:{'values':[],'writes':0,'cores':[]}}
        self.word_writes=0
        self.reads_after_write=0;self.synthesized_initial_reads=0

    def handles(self,address):
        return address==JL_WL_AUD_CON0

    def write(self,address,value,core):
        if address!=JL_WL_AUD_CON0:raise ValueError('not the modeled JL_WL_AUD CON0')
        if not isinstance(value,int) or not 0<=value<=0xffffffff:raise ValueError('unsigned32 word expected')
        entry=self.stores[address];entry['values'].append(value);entry['writes']+=1
        entry['cores'].append(core);self.word_writes+=1

    def read(self,address):
        if address!=JL_WL_AUD_CON0:raise ValueError('not the modeled JL_WL_AUD CON0')
        entry=self.stores[address]
        if entry['writes']>0:
            self.reads_after_write+=1
            return entry['values'][-1]
        # 从未写过:显式合成初始字 0(路 AM 先例)——derived-not-measured 的
        # 显式假设,不是复位值声明;本路径该分支无消费者(见类注释「读面」)。
        self.synthesized_initial_reads+=1
        return 0

    def report(self):
        return {'addresses':[hex(JL_WL_AUD_CON0)],
                'word_writes':self.word_writes,
                'stores':{hex(a):{'values':[hex(v) for v in e['values'][-16:]],
                                  'writes':e['writes'],'cores':e['cores'][-16:]}
                          for a,e in sorted(self.stores.items()) if e['writes']},
                'sdk_naming':('br28.h JL_WL_AUD = hs_base + map_adr(0x22, 0x00) = 0xfe2200, '
                              'single-member JL_WL_AUD_TypeDef {__RW __u32 CON0} — the '
                              'wireless-audio subsystem enable word, SDK-named and __RW'),
                'whitelisted_words':('exactly the one JL_WL_AUD->CON0 word written twice on '
                                     'the default path of the boot audio-framework init '
                                     '0x602ea68 (0x602ea72 entry st.w.modimm=1 and 0x6071cbe '
                                     'st.w.l=r10 inside the DAC-init cluster 0x6071bca); the '
                                     'sole app read point is the power-management snapshot '
                                     'family at 0x6069a2e (not in this boot prefix)'),
                'observed_shapes':('full-word st.w-family stores (old value irrelevant): '
                                   '0x602ea72 st.w.modimm [0xfe2200]=1 (the 3,079,241 frontier '
                                   'store) and 0x6071cbe st.w.l [r1]=r10 (SR-index dependent '
                                   '0/1); no RMW on this path (the only app RMW 0x60552ec |=1 '
                                   'is on the WL power-up path, outside this prefix)'),
                'readback_projection':{
                    'addresses':[hex(JL_WL_AUD_CON0)],
                    'reads':self.reads_after_write,
                    'synthesized_initial_reads':self.synthesized_initial_reads,
                    'readback_semantics':('last firmware-written value (route-AA uart-CON1 '
                        'final form); before any firmware write the explicit synthetic '
                        'initial word 0 (route-AM precedent) is served — derived-not-measured, '
                        'NOT a reset-value claim; on this path 0xfe2200 is always written at '
                        'the function head before any possible read, so the synthetic-zero '
                        'branch has no consumer in this prefix'),
                    'conditional_assumption':('the __RW annotation proves the SDK believes '
                        'CON0 reads back, NOT that every bit mirrors software — hardware-'
                        'updated or self-clearing bits (if any exist) would read back '
                        'differently on silicon; the sole app reader (0x6069a2e low-power '
                        'snapshot family) is outside this prefix, so even the last-write '
                        'branch is a latent semantics here'),
                    'no_poll_risk':('the full-app literal scan (route AG) found zero ready-'
                        'bit polls or loop reads of 0xfe2200 — the single read site is the '
                        'one-shot power-management snapshot store')},
                'assumption':('write-only word record plus last-write readback of the WL_AUD '
                              'enable word (derived-not-measured); stored values are real '
                              'executed instruction results, never invented; the clock-gate '
                              'hardware side effect of writing CON0 is NOT modeled — this '
                              'records that the enable configuration write executed, it does '
                              'NOT verify any audio or wireless-audio function')}


class JLAudioDacInitWriteRecords:
    """音频 DAC init 写域(JL_AUDIO DAC_CON/AUD_CON + JL_ASS CLK_CON)的写记录+读回(opt-in,路 AK)。

    识别与证据(2026-11-14 路 AG 反汇编预研 docs/wlaud-init-map.md §2.4 + 本路复核):
    - **SDK 命名与布局**:br28.h `JL_AUDIO_BASE = hs_base + map_adr(0x05, 0x00)`
      = 0xfe0500(JL_AUDIO_TypeDef 全字段 __RW;csfr.h `#define __u16 unsigned
      int` → 成员序×4 字槽,DAC_CON@+0、AUD_CON@+0x58=0xfe0558,ADC_CON@+0x38
      =0xfe0538 与路 AE 钉死互证);`JL_ASS_BASE = hs_base + map_adr(0x09, 0x00)`
      = 0xfe0900(JL_ASS_TypeDef 三成员 CLK_CON/MIC_CTL0/MIC_CTL1 之首, __RW)。
    - **写形状(0x6071bca DAC init SFR 主簇,按执行序)**:
      * 0x6071cbc `st.w [0xfe0500]=…`——**路 AS 勘误**:0x6071cb8
        `add.s14.rri.p r1=r0+0x1D00` ∥ 0x6071cbc `st.w [r0+0]=r1` 是并行对,
        存储半读**旧 r1**(SR 表 walk 留下的值;路 AS 实测执行值 0x0);路 AG
        「[0xfe0500]=0xfe2200 数值巧合」是并行对旧寄存器误读(与 0x60719a6
        死 li 误判同族盲区)——0xfe2200 是并行对首半算出的 **S2 写基址**
        (0x6071cbe `[0xfe2200]=r10` 才用它),从不是 DAC_CON 的落盘值;
      * 0x6071cc6 `rmw.or.modimm [0xfe0900]|=1`(JL_ASS CLK_CON,音频子系统
        时钟位 0;无先前写);
      * 0x6071ce6 `rmw.and.nmodimm [0xfe0500]&=~0x100` 与 0x6071cee
        `|=0x100`(DAC_CON bit8 开/关 RMW 对;读侧=已记录整字写值可推导);
      * 0x6071cea `rmw.and.nmodimm [0xfe0558]&=~0x40`(AUD_CON;本路径无
        先前写,RMW 内部读由显式合成初始字 0 服务,&~0x40 于 0 得 0,平凡)。
      值全为真实指令结果(probe_boot 通用 mmio 路径完整执行 RMW:内部读=
      投影值、按位合成、写回照常落记录),无一处发明值。
    - **读回语义**:照 uart CON1 最终形态(路 M→F→AA)一次做齐——写过返回
      最后写值;无写时显式合成初始字 0(路 AM 先例)并在报告计数分开标注
      derived-not-measured(JL_ASS CLK_CON 与 AUD_CON 的 RMW 内部读即此情形,
      是本投影的必要面:0|1=1、0&~0x40=0)。
    - **范围边界**:白名单恰三字 {0xfe0500,0xfe0558,0xfe0900};条件族
      (esco mic 块 0xfe0a40 ADA_CON8/0xfe0924 偏置/0xfe0538 ADC_CON 读、
      ANC 图门 0xfe0400 JL_ANC 族、缺省分支 0xfe0a00 DAA_CON0/0xfe0a0c/
      DAC_LEN 写族)本世界默认不可达(G6 门字=0 动态复现钉死;config 88/92
      已占用),不进白名单、保持 unmapped 诚实停——真到达时按路 AG §3.3
      条件族表对号入座,不顺带放行。
    - **运行效果不建模**:DAC 时钟门/模拟通路/DMA/IRQ68 事件一律不建模
      (IRQ68 注册 0x602ec28 只是向量槽 RAM 写)——只记录「音频 DAC 初始化
      配置写已执行」,不扩展为「音频输出已验证」。
    """
    ADDRESSES=JL_AUDIO_DAC_INIT_WORDS

    def __init__(self):
        self.stores={address:{'values':[],'writes':0,'cores':[]} for address in self.ADDRESSES}
        self.word_writes=0
        self.reads_after_write=0;self.synthesized_initial_reads=0

    def handles(self,address):
        return address in self.stores

    def write(self,address,value,core):
        if address not in self.stores:raise ValueError('not a modeled audio DAC-init word')
        if not isinstance(value,int) or not 0<=value<=0xffffffff:raise ValueError('unsigned32 word expected')
        entry=self.stores[address];entry['values'].append(value);entry['writes']+=1
        entry['cores'].append(core);self.word_writes+=1

    def read(self,address):
        if address not in self.stores:raise ValueError('not a modeled audio DAC-init word')
        entry=self.stores[address]
        if entry['writes']>0:
            self.reads_after_write+=1
            return entry['values'][-1]
        # 无先前写:显式合成初始字 0(路 AM 先例)。derived-not-measured 显式
        # 假设、非复位值声明;本路径的消费点=JL_ASS CLK_CON/AUD_CON 的原子
        # RMW 内部读(S3/S5,rmw 经 probe_boot 通用路径真实读侧)。
        self.synthesized_initial_reads+=1
        return 0

    def report(self):
        return {'addresses':[hex(a) for a in self.ADDRESSES],
                'word_writes':self.word_writes,
                'stores':{hex(a):{'values':[hex(v) for v in e['values'][-16:]],
                                  'writes':e['writes'],'cores':e['cores'][-16:]}
                          for a,e in sorted(self.stores.items()) if e['writes']},
                'sdk_naming':('br28.h JL_AUDIO = hs_base + map_adr(0x05, 0x00) = 0xfe0500 '
                              '(all-__RW JL_AUDIO_TypeDef; the csfr.h `__u16 -> unsigned int` '
                              '32-bit slot layout puts DAC_CON at +0 and AUD_CON at +0x58 = '
                              '0xfe0558, cross-checked by ADC_CON at +0x38 = 0xfe0538 pinned '
                              'by route AE) and JL_ASS = hs_base + map_adr(0x09, 0x00) = '
                              '0xfe0900 (JL_ASS_TypeDef CLK_CON, first of three members)'),
                'whitelisted_words':('exactly the three words of the audited default-path '
                                     'write set inside the DAC-init cluster 0x6071bca: '
                                     'JL_AUDIO->DAC_CON (0xfe0500, one whole-word store + the '
                                     'bit8 clear/set RMW pair), JL_AUDIO->AUD_CON (0xfe0558, '
                                     'one &=~0x40 RMW) and JL_ASS->CLK_CON (0xfe0900, one '
                                     '|=1 RMW); the conditional families (esco-mic block '
                                     '0xfe0a40/0xfe0924/0xfe0538, the ANC gate 0xfe0400, the '
                                     'missing-config branches 0xfe0a00/0xfe0a0c/DAC_LEN) are '
                                     'NOT whitelisted and stay unmapped'),
                'observed_shapes':('whole-word st.w [r0+0] store at 0x6071cbc — the SECOND '
                                   'slot of the parallel pair 0x6071cb8 `add.s14.rri.p '
                                   'r1=r0+0x1D00` ∥ `st.w [r0+0]=r1`, reading the OLD r1 '
                                   '(the SR-table walk value; executed 0x0 on this path — '
                                   'route-AS correction of the AG-map "0xfe2200 numeric '
                                   'coincidence" parallel-pair misread: 0xfe2200 is the '
                                   'computed S2 store BASE, never the DAC_CON value) — plus '
                                   'atomic RMWs rmw.or.modimm/rmw.and.nmodimm at 0x6071cc6/'
                                   '0x6071ce6/0x6071cea/0x6071cee'),
                'readback_projection':{
                    'addresses':[hex(a) for a in self.ADDRESSES],
                    'reads':self.reads_after_write,
                    'synthesized_initial_reads':self.synthesized_initial_reads,
                    'readback_semantics':('last firmware-written value (route-AA uart-CON1 '
                        'final form); before any firmware write the explicit synthetic '
                        'initial word 0 (route-AM precedent) is served — derived-not-'
                        'measured, NOT a reset-value claim. The RMWs run the FULL generic '
                        'MMIO path (probe_boot mem.read -> bit-compose -> mem.write): the '
                        'write-backs land in the records as real transactions, never '
                        'short-circuited. Consumers on this path: the JL_ASS CLK_CON |=1 '
                        'and AUD_CON &=~0x40 internal reads are served by the synthetic 0 '
                        '(0|1=1, 0&~0x40=0 — trivial), the DAC_CON RMW pair reads the '
                        'recorded whole-word (route-AS executed shape: the SR-walk 0x0) — '
                        'bit8 already clear, so &~0x100 writes it back unchanged and the '
                        '|0x100 tail lands 0x100 (fully derivable from the record, zero '
                        'invention; the AG-map "0xfe2200 numeric coincidence" and its '
                        '0xfe2200->0xfe2300 RMW chain were parallel-pair misreads, '
                        'corrected by the route-AS dynamic record)'),
                    'conditional_assumption':('__RW proves the SDK believes these words read '
                        'back, NOT that every bit mirrors software: hardware-updated or '
                        'self-clearing bits (if any exist) would read differently on '
                        'silicon; the synthetic initial 0 is an explicit assumption (reset '
                        'state unmeasured), consumed only by RMW read sides on this path')},
                'assumption':('write records plus last-write readback (synthetic initial 0 '
                              'before the first write) of three audited audio DAC-init '
                              'configuration words (derived-not-measured); stored values are '
                              'real executed instruction results, never invented; no DAC '
                              'clock gating, analog path, DMA or IRQ68 event is modeled '
                              '(the IRQ68 registration at 0x602ec28 is a plain vector-slot '
                              'RAM write) — this releases the initialization configuration '
                              'writes only, it does NOT verify any audio output')}


class JLAudioAdcResetWriteRecords:
    """JL_AUDIO->ADC_CON(0xfe0538)reset 写记录 + 读回=最后写值(opt-in,路 AS)。

    识别与证据(2026-11-14 路 AS 反汇编钉死,repo codec pin 3608ed1 与 vendor
    线性反汇编逐指令互证):
    - **SDK 命名与位语义**:br28.h `JL_AUDIO_BASE+0x38`=0xfe0538=ADC_CON
      (__RW,csfr.h `__u16→unsigned int` 32 位字槽;与路 AE 钉死的
      DAC_CON@+0/AUD_CON@+0x58 同一布局互证)。位语义路 AE 全 app 锚定:
      bit7=IRQ pending(每 PNS 样本硬件置位;分发器 0x602f430 读、仅 IRQ68
      交付后进入)、bit6=清 pending/重启(handler 尾)、bit5=数字使能
      (0x606f3e4 关)、bit4=使能门(set_buffer 0x606def0 查重+分发器 0x602f440
      双检)、bits0-3=SR 码。**写 0 = 把整个配置面清零的 reset 写**。
    - **写形状(唯一站点,0x60719a6 函数内并行对)**:0x60719a6(boot 音频
      initcall 0x602ea68 于 0x602eb80 调入的 ADC 节点 reset;设备结构基址
      0x1265C0 由 li.u32 装载)——guard 字节 b[0x1265C0+4673]=b[0x127801]
      读(0x60719b2,本世界=0)→ 0x60719b8 `li.u32 r3=0xfe0538` →
      0x60719be `li.u8 r4=0` → **0x60719c0 并行对 `add.s14.rri.p r3=r1+4596`
      ∥ 0x60719c4 `st.w [r3+0]=r4`**:第二半用**旧 r3**(0xfe0538)执行
      存储、值=旧 r4=0——即 [ADC_CON]=0 的 reset 写;第一半同步把 r3 更新
      为 hdl 指针(0x1265C0+4596=0x1277B4)供随后的 RAM 初始化(rep 24×
      st.w.pin 清 96B、[hdl+44]=0xFFFFFFFF、链表头 [hdl]=hdl/[hdl+4]=hdl、
      [hdl+8]=调用者实参、槽 [0x1265C0+308<<2]=hdl、call 0x6064162 dev 层),
      尾部 0x60719d6 把 guard 字节置 1。**旧寄存器并行语义=FM-1 run3 硬件
      证据/exact_parallel_stores 机器适配器既有机制**(「B 读 A 之前旧寄存器」
      ——本写就是该形状的首个 SFR 站点;3,137,573 停点即此对首写)。
      (注:路 AN 叙事里的门地址 b[0x1267E1] 是手算笔误,指令编码钉死
      0x1265C0+0x1241=0x127801,本路勘误。)
    - **读面(本前缀零读)**:路 AE 全 app ADC_CON 消费者——bit7 分发器
      0x602f430/bit4 set_buffer 0x606def0 都在 IRQ68 交付/esco 打开链上
      (本世界 G6 门字=0 动态钉死,esco 块不执行;IRQ68 零交付),SR 写
      0x606dee0 的读也在 esco 族——**默认路径无任何读回/轮询**。
    - **读回语义**:照 uart CON1 最终形态(路 M→F→AA)——写过返回最后
      写值;无写时显式合成初始字 0(路 AM 先例)。本前缀无消费者,读回是
      与域一/域二一致的容器完整面。**条件假设(derived-not-measured)**:
      __RW 不能证明位镜像软件——硅上 bit7 由硬件按 DMA 样本置位,投影返回
      软件值(bit7=0=无帧就绪)是「无 ADC 硬件活动」的诚实显形:固件若轮询
      bit7 会如实永远读 0(路 BN fd31 CON 轮询同款先例),不发明完成。
    - **范围边界**:白名单恰一字 0xfe0538;ADC 域其余寄存器(ADC_CON1
      0xfe055c/MBG_CON 0xfe0554/MIC_CTL0/1 0xfe0904/08/ADA_CON* 0xfe0a20..)
      与 AK 域二条件族保持 unmapped 诚实停。**运行效果不建模**(ADC 时钟/
      模拟通路/DMA/IRQ68 事件)——只记录「ADC 节点 reset 配置写已执行」,
      不扩展为「音频输入已验证」。
    """
    ADDRESS=JL_AUDIO_ADC_CON

    def __init__(self):
        self.stores={JL_AUDIO_ADC_CON:{'values':[],'writes':0,'cores':[]}}
        self.word_writes=0
        self.reads_after_write=0;self.synthesized_initial_reads=0

    def handles(self,address):
        return address==JL_AUDIO_ADC_CON

    def write(self,address,value,core):
        if address!=JL_AUDIO_ADC_CON:raise ValueError('not the modeled JL_AUDIO ADC_CON')
        if not isinstance(value,int) or not 0<=value<=0xffffffff:raise ValueError('unsigned32 word expected')
        entry=self.stores[address];entry['values'].append(value);entry['writes']+=1
        entry['cores'].append(core);self.word_writes+=1

    def read(self,address):
        if address!=JL_AUDIO_ADC_CON:raise ValueError('not the modeled JL_AUDIO ADC_CON')
        entry=self.stores[address]
        if entry['writes']>0:
            self.reads_after_write+=1
            return entry['values'][-1]
        # 从未写过:显式合成初始字 0(路 AM 先例)——derived-not-measured 的
        # 显式假设,不是复位值声明;本前缀该分支无消费者(类注释「读面」)。
        self.synthesized_initial_reads+=1
        return 0

    def report(self):
        return {'addresses':[hex(JL_AUDIO_ADC_CON)],
                'word_writes':self.word_writes,
                'stores':{hex(a):{'values':[hex(v) for v in e['values'][-16:]],
                                  'writes':e['writes'],'cores':e['cores'][-16:]}
                          for a,e in sorted(self.stores.items()) if e['writes']},
                'sdk_naming':('br28.h JL_AUDIO = hs_base + map_adr(0x05, 0x00) = 0xfe0500, '
                              'ADC_CON at +0x38 = 0xfe0538 (all-__RW JL_AUDIO_TypeDef, the '
                              'csfr.h `__u16 -> unsigned int` 32-bit slot layout cross-checked '
                              'by route AE: DAC_CON@+0, AUD_CON@+0x58); AE pinned the bit '
                              'semantics — bit7 IRQ pending (hardware-set per PNS sample), '
                              'bit6 clear/restart, bit5 digital enable, bit4 enable gate, '
                              'bits0-3 sample-rate code — writing 0 resets the whole config face'),
                'whitelisted_words':('exactly the one JL_AUDIO->ADC_CON word written by the ADC-node '
                                     'reset function 0x60719a6 (called from the boot audio initcall '
                                     '0x602ea68 at 0x602eb80 on the config-gate b[0x127801]==0 '
                                     'branch; the gate byte is device-struct 0x1265C0+4673, set to '
                                     '1 at 0x60719d6 after the node init completes)'),
                'observed_shapes':('one whole-word store, executed as the SECOND slot of the '
                                   'parallel pair at 0x60719c0: `add.s14.rri.p r3 = r1+4596` ∥ '
                                   '`st.w [r3+0] = r4` — the store reads the OLD r3 (0xfe0538 '
                                   'loaded by li.u32 at 0x60719b8) with old r4 = 0 (li.u8 at '
                                   '0x60719be), i.e. the pair writes [ADC_CON]=0 while its first '
                                   'slot computes the device-struct handle pointer; the '
                                   'old-register parallel semantics is the FM-1 run3 hardware '
                                   'evidence already implemented by the exact_parallel_stores '
                                   'machine adapter (this is its first SFR site). The route-AN '
                                   'narrative address b[0x1267E1] for the gate was a hand-'
                                   'arithmetic slip — the instruction encoding pins '
                                   '0x1265C0+0x1241 = 0x127801'),
                'readback_projection':{
                    'addresses':[hex(JL_AUDIO_ADC_CON)],
                    'reads':self.reads_after_write,
                    'synthesized_initial_reads':self.synthesized_initial_reads,
                    'readback_semantics':('last firmware-written value (route-AA uart-CON1 '
                        'final form); before any firmware write the explicit synthetic '
                        'initial word 0 (route-AM precedent) is served — derived-not-measured, '
                        'NOT a reset-value claim; this prefix has ZERO reads (every app '
                        'ADC_CON consumer per route AE — the bit7 dispatcher 0x602f430, the '
                        'bit4 set_buffer double-check 0x606def0, the SR-write read 0x606dee0 — '
                        'lives on the IRQ68-delivery/esco-open paths, both outside this '
                        'world), so both branches are latent semantics here'),
                    'conditional_assumption':('__RW proves the SDK believes ADC_CON reads back, '
                        'NOT that every bit mirrors software: on silicon bit7 (IRQ pending) is '
                        'SET BY HARDWARE per DMA sample — the projection returning the software '
                        'value (bit7=0, no frame ready) is the honest manifestation of no ADC '
                        'hardware activity: a firmware poll of bit7 would spin its full budget '
                        'rather than be handed an invented completion (route-BN fd31 CON '
                        'poll-budget precedent), never a fabricated pending'),
                    'no_poll_risk':('no ready-bit poll or loop read of 0xfe0538 exists on the '
                        'default boot path (route-AE consumer inventory); the reset write is '
                        'unconditional inside the guarded body')},
                'assumption':('write-only word record plus last-write readback of the JL_AUDIO '
                              'ADC_CON reset word (derived-not-measured); the stored 0 is the '
                              'real executed instruction result (old r4 of the parallel pair), '
                              'never invented; no ADC clock gating, analog front end, DMA or '
                              'IRQ68 event is modeled — this records that the ADC-node reset '
                              'configuration write executed, it does NOT verify any audio '
                              'input')}


class JLAddaDaaCon1Registers:
    """JL_ADDA->DAA_CON1(0xfe0a04)的「显式初值 fixture + 配置位读回」两开关分立容器(opt-in,路 CV)。

    背景:3,170,362 停点 0x606b426 是 0x606b40e 配置 RMW insert 函数对
    DAA_CON1 的首读——音频 init 尾段第一个非掩码平凡 RMW 读(读值实质参与
    写回),且本世界该字零写=读的是「DAC 模拟块寄存器内容」这个硬件域
    状态,投影世界无从推导(**首个真实硬件反馈依赖**)。按「首次读取时的
    未知状态」处理(措辞红线:不称「零写历史」——ROM/他核/模拟域复位仍
    可能影响该字)。

    识别与证据(2026-11-14 路 CV 反汇编钉死,repo codec pin 3608ed1;
    消费者先例=路 BO docs/appcore-tail-sfr-semantics.md §3):
    - **RMW 函数 0x606b40e 全链**:`[--sp]={rets,r4}` → `if (r1>=3) r1=3`
      (增益档位钳 3)→ r3=b[0x060A8608+r1](**4 档增益码表实测字节
      {0x00,0x04,0x06,0x07}**;勘误:probe 叙事曾记 0x060A8548,指令编码
      实为 0x060A8608,与路 BO 一致)→ 0x606b426 `r2=[0xfe0a04]` 首读 →
      r0 bit0 块:`r4=r2>>3; r2=r3; r2<=insert(r4,p:3,l:29)`(bits3..31
      保留旧值、bits0..2 换表码)→ r0 bit1 块:`r2<=insert(r3,p:4,l:3)`
      (bits4..6 换表码)→ **0x606b43c `[r1+0]=r2 写回在两个条件块之外**
      (未选通道时写回=旧值原样回写,总线写仍发生)→ `{pc,r4}=[sp++]` 返回。
      函数不改 r0/r1(返回后 caller 可复用)。
    - **veneer 0x606db6c 返回链**(反汇编钉死):`[--sp]=rets; call 0x606b40e;
      pc=[sp++]`——是**真调用**不是跳转:0x606b40e 的 rts 回到 0x606db72
      (运行态实测 rets=0x606db72),再由 `pc=[sp++]` 弹出**原始调用者的**
      返回地址(该地址躺在 fixer 帧下方的栈上,不能把 0x606db72 当返回地址)。
    - **本前缀唯一到达的调用点=0x6071b70**(DAC init 驱动内,与 S1..S5 同
      函数域):`r0=0xFF(r1=0)`(双通道全选、档位 0)经 veneer 直调 fixer
      ——运行态实测 r0=0xFF、r3=table[0]=0x0。另一调用点 0x606dc00(音量
      同步 walker 0x606db74 的纠偏分支,r0=单通道位)与 0x606b45c/0x6071d92
      两处直调不在本前缀。
    - **两开关分立**(codex 定调):
      * `jl_adda_daa_con1_initial_fixture`——**显式输入初始 word,无隐式零
        默认**。fixture 记录名称/来源/适用场景(报告 fixture 子键);本前缀
        用敏感性探针对 {0x0, 0xfffffff8}(保留位全零/全一,标
        sensitivity-probe-not-plausible-state,**是探针不是合理状态**)。
        写前读一律返回该字(它就是假设的初始状态,一次性供给)。
      * `jl_adda_daa_con1_config_readback`——已核定配置位维护当前值:
        真实写入后更新、后续读用当前状态(最后写值;__RW 先例=uart CON1
        路 AA/lp_timer CON 路 AL/ADC_CON 路 AS 同族,但**本容器无「合成
        初始 0」分支**——初值只来自显式 fixture,这正是与域一/二/三容器的
        分野)。
    - **停原边界契约**:只开读回、无初值、未写入时的读仍按原边界停
      (unmapped_memory 读 0xfe0a04,kind/字段与 3,170,362 停点逐字段一致);
      初值开、读回关时写过之后的读同样停原边界(未立读回假设)。
    - **范围边界**:白名单恰一字 0xfe0a04;JL_ADDA 块其余
      (DAA_CON0 0xfe0a00/DAA_CON2 0xfe0a0c/ADA_CON 族 0xfe0a20..)与
      DAC_VL0 0xfe051c 保持 unmapped——**不一次放行整个 ADDA 读族**,后续
      读逐个分类(配置/状态/测量)另立证据。运行效果(DAC 模拟通路/偏置/
      音量)不建模——只记录「增益配置 RMW 已执行」,不扩展为「音频输出
      已验证」。
    """

    ADDRESS=JL_ADDA_DAA_CON1
    # 原边界停记录字段(停原边界契约用;与 probe_boot unmapped_memory 形状一致)。
    BOUNDARY_DETAILS={'access':'read','address':hex(JL_ADDA_DAA_CON1),
                      'size':4,'reference_sdk_register':None}

    def __init__(self,initial_word=None,config_readback=False):
        self.initial_word=initial_word
        self.config_readback=config_readback
        self.stores={JL_ADDA_DAA_CON1:{'values':[],'writes':0,'cores':[]}}
        self.word_writes=0
        self.fixture_reads=0;self.reads_after_write=0

    def handles(self,address):
        return address==JL_ADDA_DAA_CON1

    def _boundary_stop(self):
        # 停原边界:kind/字段与默认世界的 unmapped_memory 停点逐字段一致
        #(「只开读回无初值未写入仍停原边界」的契约形状)。
        raise Blocked('unmapped_memory',**dict(self.BOUNDARY_DETAILS))

    def write(self,address,value,core):
        if address!=JL_ADDA_DAA_CON1:raise ValueError('not the modeled JL_ADDA DAA_CON1')
        if not isinstance(value,int) or not 0<=value<=0xffffffff:raise ValueError('unsigned32 word expected')
        entry=self.stores[address];entry['values'].append(value);entry['writes']+=1
        entry['cores'].append(core);self.word_writes+=1

    def read(self,address):
        if address!=JL_ADDA_DAA_CON1:raise ValueError('not the modeled JL_ADDA DAA_CON1')
        entry=self.stores[address]
        if entry['writes']>0:
            if not self.config_readback:
                # 写后读但读回开关未开:不立「普通存储式读回」假设,停原边界。
                self._boundary_stop()
            self.reads_after_write+=1
            return entry['values'][-1]
        if self.initial_word is None:
            # 无初值 fixture:首次读取时的未知状态不可推导,停原边界
            #(codex:只开读回无初值未写入时仍停原边界)。
            self._boundary_stop()
        self.fixture_reads+=1
        return self.initial_word

    def report(self):
        return {'addresses':[hex(JL_ADDA_DAA_CON1)],
                'word_writes':self.word_writes,
                'stores':{hex(a):{'values':[hex(v) for v in e['values'][-16:]],
                                  'writes':e['writes'],'cores':e['cores'][-16:]}
                          for a,e in sorted(self.stores.items()) if e['writes']},
                'sdk_naming':('br28.h JL_ADDA = hs_base + map_adr(0x0a, 0x00) = 0xfe0a00, '
                              'DAA_CON1 at +0x04 (__RW, the DAC-side analog gain word; the '
                              'ADA_CON family pinned by route AE starts at +0x20 — layout '
                              'cross-checked); route BO pinned the consumer map: RMW 0x606b40e '
                              'inserts the 3-bit gain codes {0,4,6,7} from flash table '
                              '0x060a8608 (probe narrative previously wrote 0x060A8548 — '
                              'the instruction encoding pins 0x060A8608)'),
                'whitelisted_words':('exactly the one JL_ADDA->DAA_CON1 word; the rest of the '
                                     'JL_ADDA block (DAA_CON0 0xfe0a00 / DAA_CON2 0xfe0a0c / '
                                     'the ADA_CON family 0xfe0a20..) stays outside THIS '
                                     'container — the ADDA read family is NOT released '
                                     'wholesale, later reads get classified per address '
                                     '(config/status/measurement); the route-B3 sibling '
                                     'switch jl_adda_analog_family serves five OTHER family '
                                     'words with a mutually exclusive address set'),
                'observed_shapes':('plain ld.w read at 0x606b426 plus plain st.w write-back at '
                                   '0x606b43c inside the config-RMW insert function 0x606b40e '
                                   '(the write-back sits OUTSIDE both selector blocks — with '
                                   'no channel selected the OLD value is written back verbatim, '
                                   'the bus write still happens); reached through the REAL-CALL '
                                   'veneer 0x606db6c ([--sp]=rets; call 0x606b40e; pc=[sp++]) — '
                                   'the fixer returns to 0x606db72 and the veneer then returns '
                                   'to the original caller, whose return address lives on the '
                                   'stack below the fixer frame (runtime-verified rets='
                                   '0x606db72); the only call site on this prefix is the '
                                   'DAC-init driver 0x6071b70 (r0=0xFF both channels, gain '
                                   'step 0), runtime-verified'),
                'fixture':(None if self.initial_word is None else {
                    'address':hex(JL_ADDA_DAA_CON1),
                    'initial_word':hex(self.initial_word),
                    'label':'sensitivity-probe-not-plausible-state',
                    'name':('sensitivity probe pair member {0x0, 0xfffffff8} '
                            '(reserved bits all-zero / all-one)'),
                    'source':('route-CV initial-word sensitivity probes forked from the same '
                              '3,170,362 stop; NOT a plausible hardware state, NOT a '
                              'reset-value claim — the first-read unknown state has no '
                              'derivable source (ROM/other-core/analog-domain reset may all '
                              'still influence the word)'),
                    'scenario':('p11_heap_experimental_config conditional frontier advance; '
                                'the one-shot route-CV sensitivity fork ran the same stack '
                                'with the all-ones probe word (results pinned in '
                                'docs/adda-daa-con1.md §3)')}),
                'readback_projection':{
                    'addresses':[hex(JL_ADDA_DAA_CON1)],
                    'fixture_reads':self.fixture_reads,
                    'reads_after_write':self.reads_after_write,
                    'readback_semantics':('two-switch separation (codex): the initial-fixture '
                        'switch supplies an EXPLICIT initial word (no implicit zero default); '
                        'the config-readback switch serves reads AFTER real firmware writes '
                        'with the last written word (config bits keep their current value). '
                        'With readback alone, a never-written read still stops at the ORIGINAL '
                        'boundary (unmapped_memory read 0xfe0a04, identical fields); with the '
                        'fixture alone, a read after a write also stops at the original '
                        'boundary (no readback assumption); unlike the route-AK/AS containers '
                        'there is NO synthetic-initial-zero branch — derived-not-measured, '
                        'NOT a reset-value claim'),
                    'no_poll_risk':('the RMW at 0x606b40e is not a poll (single read, single '
                        'write-back, no loop); the volume-sync walker 0x606db74 does '
                        'read-compare-correct DAA_CON1 fields but is outside this prefix '
                        '(audio-task domain)'),
                    'preserved_bits_note':('bits 3 and 7..31 of the written-back word are '
                        'carried from the INITIAL state (the RMW only replaces bits0..2 and '
                        'bits4..6 with the table code) — the initial-word dependency is NOT '
                        'erased by the write; it persists in the register state and only '
                        'disappears from observations if no consumer reads those bits '
                        '(see the route-CV sensitivity table)')},
                'assumption':('explicit initial-word fixture plus config-bit last-write readback '
                              'of JL_ADDA->DAA_CON1 (derived-not-measured, '
                              'sensitivity-probe-not-plausible-state); stored values are real '
                              'executed instruction results, never invented; no DAC analog '
                              'path, bias or audio output is modeled — this records that the '
                              'gain configuration RMW executed, it does NOT verify any audio '
                              'output')}


class JLAddaAnalogFamilyRegisters:
    """JL_ADDA 模拟族五字容器(合成初始 0+读回=当前值,opt-in,路 B3/S19 批 2 续)。

    背景:4,521,083 停点 0x606ac42——音频 DAC 模拟 init 函数 0x606abf0..0x606b20e
    (对象驱动,r13=配置对象,内部两次 delay(10) 经 0x606abe6 veneer 调 RAM 延时
    0x12e290 族)首条 `[0xfe0a00] &= 0xFFFFDFFF`(DAA_CON0 bit13 清位 RMW)的
    内部读撞 unmapped。函数全量反汇编钉死触址恰五字(r7=0xfe0a00 基址,off 按
    br28.h JL_ADDA_TypeDef 全 __u32 布局命名):
      +0x00 0xfe0a00 DAA_CON0 (__RW):68 处 RMW + 多处 ld→mask/or→st 纯配置组装
      +0x08 0xfe0a08 DAA_CON2 (__RW):132 处 RMW
      +0x0c 0xfe0a0c DAA_CON3 (__RW):35 处 RMW
      +0x48 0xfe0a48 ADDA_CON1(__RW):34 处 RMW
      +0x4c 0xfe0a4c (结构外,SDK 无名):10 处 RMW
    (+0x04 DAA_CON1 不进本容器——路 CV 两开关分立容器已覆盖,白名单互斥。)
    分类判定(路 BO §5 三选一倾向行「DAA_CON0/2:写侧整字/RMW 记录」+本路
    消费面复核):读值只进写回组装(load→mask/or→store 或 RMW),函数内零
    分支消费读值(分支全部测试配置输入 r0/r4/r6/r12)——AM 容器族语义
    (合成初始字 0+读回=当前值),derived-not-measured 非复位值声明;
    敏感性分叉(ones 字)照 CW DAC_VL0 先例。
    已知限制(如实标注):0x606b352 读 DAA_CON2 &2 与期望比较是状态位读回
    消费者(路 BO 家族面),在本前缀之外的音频任务域——本容器的读回=当前值
    语义届时对它服务的是合成配置位而非硅状态位,bit1 硅语义仍未证(推迟,
    BO 既定结论不变)。
    """

    # 白名单恰五字(r7 基址 0xfe0a00 的静态全函数枚举,见类注释)。
    WORDS=(0xfe0a00,0xfe0a08,0xfe0a0c,0xfe0a48,0xfe0a4c)
    SDK_NAMES={0xfe0a00:'JL_ADDA->DAA_CON0',0xfe0a08:'JL_ADDA->DAA_CON2',
               0xfe0a0c:'JL_ADDA->DAA_CON3',0xfe0a48:'JL_ADDA->ADDA_CON1',
               0xfe0a4c:'JL_ADDA+0x4c (past struct, unnamed)'}
    SYNTHETIC_INITIAL_WORD=0

    def __init__(self):
        self.stores={address:{'values':[],'writes':0} for address in self.WORDS}
        self.word_writes=0
        self.reads_after_write=0;self.synthesized_initial_reads=0

    def handles(self,address):
        return address in self.stores

    def write(self,address,value):
        if address not in self.stores:raise ValueError('not a modeled JL_ADDA analog family word')
        if not isinstance(value,int) or not 0<=value<=0xffffffff:raise ValueError('unsigned32 word expected')
        entry=self.stores[address];entry['values'].append(value);entry['writes']+=1
        self.word_writes+=1

    def read(self,address):
        if address not in self.stores:raise ValueError('not a modeled JL_ADDA analog family word')
        entry=self.stores[address]
        if entry['writes']>0:
            self.reads_after_write+=1
            return entry['values'][-1]
        # 从未写过:显式合成初始字 0(AM 容器族先例;读值只进写回组装不进
        # 分支=初值不参与观察路径,敏感性分叉 ones 字互证)。
        self.synthesized_initial_reads+=1
        return self.SYNTHETIC_INITIAL_WORD

    def report(self):
        return {'addresses':[hex(a) for a in self.WORDS],
                'word_writes':self.word_writes,
                'stores':{hex(a):{'values':[hex(v) for v in e['values'][-8:]],
                                  'writes':e['writes']}
                          for a,e in sorted(self.stores.items()) if e['writes']},
                'sdk_naming':('br28.h JL_ADDA = hs_base + map_adr(0x0a,0x00) = 0xfe0a00, '
                              'JL_ADDA_TypeDef all-__u32 words (DAA_CON0/1/2/3/4, '
                              'RESERVED5/6 __RO, DAA_CON7 __RO, ADA_CON0..9, ADDA_CON0/1); '
                              'route-B0 §3 family face lists 8 li users of the block; '
                              'this container whitelists exactly the five words the '
                              'analog-init function 0x606abf0..0x606b20e touches'),
                'whitelisted_words':('exactly {0xfe0a00 DAA_CON0, 0xfe0a08 DAA_CON2, '
                                     '0xfe0a0c DAA_CON3, 0xfe0a48 ADDA_CON1, 0xfe0a4c '
                                     'unnamed}; +0x04 DAA_CON1 stays with the route-CV '
                                     'two-switch container (mutually exclusive whitelist); '
                                     'the rest of the JL_ADDA block stays unmapped'),
                'observed_shapes':('function-granularity config RMW chains inside the '
                                   'analog init 0x606abf0 (rmw.and/rmw.or immediate and '
                                   'register-operand forms, plus ld.w→mask/or→st.w '
                                   'compositions); every read value only composes a '
                                   'write-back, no branch inside the function consumes a '
                                   'loaded value (branch inputs are the config object '
                                   'bytes r0/r4/r6/r12); first stop was the DAA_CON0 '
                                   'bit13 clear at 0x606ac42 after a delay(10) veneer'),
                'readback_projection':{
                    'addresses':[hex(a) for a in self.WORDS],
                    'reads':self.reads_after_write,
                    'synthesized_initial_reads':self.synthesized_initial_reads,
                    'readback_semantics':('last firmware-written value (route-AA/AL family); '
                        'before any firmware write the explicit synthetic initial word 0 '
                        '(route-AM precedent) — derived-not-measured, NOT a reset-value '
                        'claim; no separate initial-fixture switch (the read values only '
                        'compose write-backs, no branch — the route-CW classification)'),
                    'known_limitation':('0x606b352 reads DAA_CON2 &2 and compares with an '
                        'expected value (route-B0 family face) — a status-bit readback '
                        'consumer in the audio-task domain OUTSIDE this prefix; when that '
                        'domain arrives this container would serve its synthesized config '
                        'word, the silicon bit1 semantics stay unverified (route-B0 '
                        'deferral unchanged)')},
                'assumption':('write record plus last-write readback with an explicit '
                              'synthetic initial 0 for the five JL_ADDA analog-family '
                              'words (derived-not-measured); stored values are real '
                              'executed instruction results, never invented; no DAC '
                              'analog path, bias or audio output is modeled — this '
                              'records that the analog configuration RMWs executed, it '
                              'does NOT verify any audio output')}


class JLAudioDacTmRegisters:
    """JL_AUDIO DAC_TM0/TM1(0xfe0524/0xfe0528)半字 field setter 容器(合成初始 0
    +读回=当前值,opt-in,路 DT/S19 冲刺批 2 续二;codex 定调四点全落)。

    背景:19,139,239 停点 0x606abbe——DAC 启动函数 0x6071e74 尾段(两次 delay
    经 0x606abe6 veneer 后)0x60720c2 `r1=b[0x1265C0+69]`、0x60720c6 r0=-600、
    0x60720ca call 0x606ab92——该函数(B3 交接暂称 getter)全量反汇编是
    **DAC_TM0/TM1 的半字 field SETTER(RMW)**:入口 `if(r1>11) goto rts`+
    `r2=0xFE0524`+`tbb [r1]`(表 06 08 06 08 1a 1a 1a 1a 10 12 10 12)12 路——
    selector 0/2→0x606abaa 写 TM0 低半字(读[r2+0]&0xFFFF0000 保高半,
    r0 低 16 位进低半)、1/3→0x606abae 写 TM1 低半字、4..7→0x606abd2 裸 rts
    (无读无写)、8/10→0x606abbe 写 TM0 高半字(读[r2+0] 取 .l(u) 保低半,
    (r0&0xFFFF)<<16 进高半)、9/11→0x606abc2 写 TM1 高半字。停点即
    selector 8/10 handler 的 RMW 内部读。**读值数据流:只进写回组装
    (保留的旧半字与新半字 OR 后写回同地址),函数内零分支消费读值**
    (分支只测入口 selector r1>11)。

    访问面(全 app 逐 2 字节位置独立解码扫描+15 个 li 0xfe0500 基址站点
    ±80 指令偏移核查):0xfe0524 唯一 li 在 setter 内部 0x606ab96,0xfe0528
    无 li;setter 全 app 恰 10 个调用点全在音频驱动层(0x606b30e/b318 配置
    半字、0x606b322/b32c 固定 -600、0x6071e08 2048+0x6071e52 31 次递减
    ramp、0x60720ca/20d6 本前缀固定 -600 两条、0x6072212/2256 缓冲长度/2
    派生)——**固件从不「读」TM0/TM1 当运行态,唯一读形态就是 setter 的
    RMW 内部读**,B3 交接里「getter/硬件维护时间标记被读」的预判撤销。

    分类(codex gpt-6-astra 定调,四点采纳):①确认 setter/RMW,撤销「运行态
    getter」分类;DAC_TM 命名保留原名,硬件语义(time mark?单位?有符号?)
    不展开不解释——软件写 -600 不证明硬件按有符号数解释。②容器族语义
    (合成初始 0+读回=当前值),但**初值来源具名登记**:本例保留半字持久
    携带进写回字=CV 性质的初值依赖,与 CW(旧值位被清)不同;因全 app 无
    setter 外读者(CW 判据:读值不进观察路径),不单设 fixture 开关,
    synthetic_zero 初值假设随容器开关绑定、报告明示。③恰白名单 setter
    静态地址集 {0xfe0524,0xfe0528}(报告分列 modeled/touched);只服务对齐
    32 位字访问(setter 本身 ld.w/st.w);其余 0xfe05xx 严格 unmapped
    (BO 全块 D1 后另立证据包不变)。**新读者重审边界**:同一地址出现
    setter 外读者必须重新分类,否则容器会悄悄放过新语义。④敏感性探针
    (probe_only):TM0 初值一次性 0 vs 0xffffffff 分叉(只注初值,后续读回
    真实写回值),断言两臂写回 0xFDA8_0000/0xFDA8_FFFF(异或 0xFFFF)+执行
    轨迹全同。
    """

    # 白名单恰两字(setter 0x606ab92 静态全函数地址集;selector 4..7 空操作
    # 不触寄存器,>11 早退同)。
    WORDS=(0xfe0524,0xfe0528)
    SDK_NAMES={0xfe0524:'JL_AUDIO->DAC_TM0',0xfe0528:'JL_AUDIO->DAC_TM1'}
    SYNTHETIC_INITIAL_WORD=0

    def __init__(self,tm0_probe_initial=None):
        self.stores={address:{'values':[],'writes':0} for address in self.WORDS}
        self.word_writes=0
        self.reads_after_write=0;self.synthesized_initial_reads=0
        # probe_only 敏感性探针:只替换 TM0(0xfe0524)的初值一次(后续读回
        # 真实写回值,非每读强制返回探针常数);TM1 不扰动保归因干净。
        self.tm0_probe_initial=tm0_probe_initial

    def handles(self,address):
        return address in self.stores

    def _initial_word(self,address):
        if address==0xfe0524 and self.tm0_probe_initial is not None:
            return self.tm0_probe_initial
        return self.SYNTHETIC_INITIAL_WORD

    def write(self,address,value):
        if address not in self.stores:raise ValueError('not a modeled JL_AUDIO DAC_TM word')
        if not isinstance(value,int) or not 0<=value<=0xffffffff:raise ValueError('unsigned32 word expected')
        entry=self.stores[address];entry['values'].append(value);entry['writes']+=1
        self.word_writes+=1

    def read(self,address):
        if address not in self.stores:raise ValueError('not a modeled JL_AUDIO DAC_TM word')
        entry=self.stores[address]
        if entry['writes']>0:
            self.reads_after_write+=1
            return entry['values'][-1]
        # 从未写过:显式合成初始字 0(初值来源具名 synthetic_zero——本例保留
        # 半字持久携带进写回=CV 性质初值依赖,全 app 无 setter 外读者,故不
        # 单设 fixture 开关;derived-not-measured 非复位值声明)。
        self.synthesized_initial_reads+=1
        return self._initial_word(address)

    def report(self):
        touched={hex(a):{'values':[hex(v) for v in e['values'][-8:]],
                         'writes':e['writes']}
                 for a,e in sorted(self.stores.items()) if e['writes']}
        proj={
            'addresses':[hex(a) for a in self.WORDS],
            'modeled_vs_touched':('modeled exactly {0xfe0524 DAC_TM0, 0xfe0528 '
                                  'DAC_TM1} (the setter 0x606ab92 static address '
                                  'set); touched this prefix: '
                                  +((', '.join(sorted(touched)) or 'none'))),
            'word_writes':self.word_writes,
            'stores':touched,
            'sdk_naming':('br28.h JL_AUDIO_TypeDef +0x24 DAC_TM0 / +0x28 DAC_TM1, '
                          'both __RW __u32 (reference SDK header, not exact QCY '
                          'source); the TM ("time mark"?) hardware semantics, units '
                          'and signedness stay UNVERIFIED — the container models '
                          'only the observed software half-word field-setter shape'),
            'whitelisted_words':('exactly {0xfe0524, 0xfe0528} — the full static '
                                 'address set of the half-word field setter '
                                 '0x606ab92 (12-way tbb: 0/2 TM0.low, 1/3 TM1.low, '
                                 '8/10 TM0.high, 9/11 TM1.high, 4..7 no-op rts); '
                                 'only aligned 32-bit word accesses are served '
                                 '(the setter itself uses ld.w/st.w); the rest of '
                                 'the JL_AUDIO block stays unmapped (route-BO '
                                 'whole-block D1-later evidence package unchanged)'),
            'observed_shapes':('RMW chains inside the setter 0x606ab92 (read word, '
                               'mask-keep the other half, insert (r0&0xFFFF)<<shift, '
                               'store back to the same word); every read value only '
                               'composes a write-back, the sole branch tests the '
                               'entry selector r1>11; all 10 app call sites feed '
                               'config halfwords, fixed -600 values, a 31-step '
                               'ramp and buffer-length-derived halves; the app '
                               'never reads DAC_TM0/TM1 as live hardware state — '
                               'the only read form is the setter RMW internal read'),
            'readback_projection':{
                'addresses':[hex(a) for a in self.WORDS],
                'reads':self.reads_after_write,
                'synthesized_initial_reads':self.synthesized_initial_reads,
                'initial_value_origin':('synthetic_zero — an explicit experimental '
                    'assumption registered with this container switch (codex note: '
                    'the preserved half-word persists into the write-back, which is '
                    'CV-like initial-value dependence; the app has no reader outside '
                    'the setter RMW, so the route-CW observed-path classification '
                    'applies and no separate initial-fixture switch is created)'),
                'readback_semantics':('last firmware-written value; before any '
                    'firmware write the explicit synthetic initial word 0 — '
                    'derived-not-measured, NOT a reset-value claim; no hardware '
                    'self-update, time-mark counting or DAC DMA evolution is '
                    'modeled (an explicitly enabled software register-storage '
                    'projection; the zero initial word is an experimental '
                    'assumption, readback comes from the most recent write inside '
                    'the projection; it does not claim to reproduce the silicon '
                    'reset value or any hardware-autonomous update)'),
                'known_limitation':('any future reader of 0xfe0524/0xfe0528 outside '
                    'the setter 0x606ab92 (audio-task domain, D2) must be '
                    're-audited before this container may serve it — the container '
                    'would then answer with synthesized config bits, not silicon '
                    'state; DAC_DB0/DB1 (+0x2c/0x30) and the rest of the JL_AUDIO '
                    'block stay unmapped and get their own per-access '
                    'classification when reached'),
            },
        }
        if self.tm0_probe_initial is not None:
            proj['sensitivity_probe']={
                'tm0_probe_initial':hex(self.tm0_probe_initial),
                'label':'sensitivity-probe-not-plausible-state',
                'shape':('one-shot initial-word substitution for DAC_TM0 only '
                         '(subsequent reads return the real written value); TM1 '
                         'stays at synthetic 0 in both arms to keep attribution '
                         'clean'),
            }
        return proj


class JLAddaAdaFamilyRegisters:
    """JL_ADDA ADA 子族九字容器(合成初始 0+读回=当前值,opt-in,路 ADA/S19
    冲刺批 2 续三)。

    背景:21,798,206 停点 0x606b5ee——ADA 配置函数 0x606b4d0..0x606c3a8
    (8 路 tbb selector:入口 r1∈1..8(r10=r1-1,>7 早退,2/4/5/6 直接 pop),
    公共体先做 RAM 对象初始化,然后 if(dev_ptr!=0){li r6=0xFE0A20;ADA 大串
    配置},尾部二级 tbh 8 路分发 0xfd2018/0xfd2518 块——本前缀外)内部
    `[r6+0] &= 0xFFFFFFDF`(ADA_CON0 bit5 清位 RMW)的内部读撞 unmapped。
    函数内 r6 唯一真赋值即 0x606b5b0 li(tbb 表区 0x606b4ec..f0 的"写 r6"
    是表字节伪解码)。

    静态全图(全 app 逐 2 字节位置独立解码扫描枚举 li 常量+每站点线性窗口
    数据流核查,方法照 DT):ADA 子族窗 0xfe0a20..0xfe0a44 的全部触达 =
    7 个基址站点(9 站点实例),全部配置 RMW/组装形状、读值零分支消费:
      * li r6=0xFE0A20 @0x606b5b0(上述函数):+0/+4/+8/+12/+16/+20/+24/
        +28/+32 → 九字;309 处原子 RMW(rmw.and.nmodimm/rmw.or.modimm)
        +51 处 ld.w,51 条读值消费链逐一核查全部 and/or/insert/lsl/lsr
        →st.w 写回组装,零分支零 call 参数消费(分支全部测试配置输入);
        另 +40/+44=0xfe0a48/4c 是路 B3 容器白名单字(DT 段 +11 RMW 即本
        函数写,由 B3 容器服务,与本容器地址集互斥)。
      * li r0=0xFE0A20 @0x606f4fc(close 族 0x606f4c0..0x606f598):纯清位
        rmw.and 序列(0xfe0a20/24/30/34/38/3c),零装载。
      * li r2=0xFE0A24 @0x606f438(上一 close 函数):纯清位 RMW
        (0xfe0a24/30/34/38/3c),零装载。
      * li r1=0xFE0A30 @0x606c3b0(4 通道 field setter 0x606c3b0..e6):
        if((chan&N)!=0){ld→and.invbit→or→st},分支只测通道掩码。
      * li r9=0xFE0A40 @0x606c448(调度层 0x606c3e8 函数内,call 上述
        配置函数后):ld→insert→st 读回组装。
      * li r3=0xFE0A40 @0x606de9c(增益 field setter):if((chan&N)!=0)
        {ld→lsr>>5/insert→st}。
      * li r1=0xFE0A40 @0x606ee9a(配置对象 setter):beqz(配置字节)
        门控 {ld→mask→insert→st}×4。
    0xfe0a44(ADA_CON9)全 app 零触达(li 无、任何基址偏移无)→不进白名单,
    留 unmapped,新读者重审。0xfe0a00 基址 8 站点(B0 族面)无一触
    +0x20..+0x44(B3 五字域访问与 B3 记录一致)。全部对齐 32 位字访问,
    无子字/错位形状。

    分类(照 B3/DT 判据,读值只进写回组装零分支):AM 容器族——合成初始
    0+读回=当前值,derived-not-measured 非复位值声明。开关分立而非扩展
    jl_adda_analog_family 的 WORDS:直接扩五字容器会静默改变已开场景的
    服务面,违反「新开关默认关 legacy 逐字节不变」红线;两容器分治同一
    JL_ADDA 块不同字域(B3 五字+本九字+CV 一字),地址集互斥由白名单
    天然保证。known_limitation 照 B3 复刻:close 族纯清位届时服务的是容器
    存储的合成配置字(非硅状态位),硅复位/运行态语义未证。
    """

    # 白名单恰九字(已证实触达的静态地址集;0xfe0a44 零触达不进)。
    WORDS=(0xfe0a20,0xfe0a24,0xfe0a28,0xfe0a2c,0xfe0a30,
           0xfe0a34,0xfe0a38,0xfe0a3c,0xfe0a40)
    SDK_NAMES={0xfe0a20:'JL_ADDA->ADA_CON0',0xfe0a24:'JL_ADDA->ADA_CON1',
               0xfe0a28:'JL_ADDA->ADA_CON2',0xfe0a2c:'JL_ADDA->ADA_CON3',
               0xfe0a30:'JL_ADDA->ADA_CON4',0xfe0a34:'JL_ADDA->ADA_CON5',
               0xfe0a38:'JL_ADDA->ADA_CON6',0xfe0a3c:'JL_ADDA->ADA_CON7',
               0xfe0a40:'JL_ADDA->ADA_CON8'}
    SYNTHETIC_INITIAL_WORD=0

    def __init__(self,ada_probe_initial=None):
        self.stores={address:{'values':[],'writes':0} for address in self.WORDS}
        self.word_writes=0
        self.reads_after_write=0;self.synthesized_initial_reads=0
        # probe_only 敏感性探针:只替换 ADA_CON0(0xfe0a20)的初值一次
        # (后续读回真实写回值,非每读强制常数);其余八字不扰动保归因干净。
        self.ada_probe_initial=ada_probe_initial

    def handles(self,address):
        return address in self.stores

    def _initial_word(self,address):
        if address==0xfe0a20 and self.ada_probe_initial is not None:
            return self.ada_probe_initial
        return self.SYNTHETIC_INITIAL_WORD

    def write(self,address,value):
        if address not in self.stores:raise ValueError('not a modeled JL_ADDA ADA-family word')
        if not isinstance(value,int) or not 0<=value<=0xffffffff:raise ValueError('unsigned32 word expected')
        entry=self.stores[address];entry['values'].append(value);entry['writes']+=1
        self.word_writes+=1

    def read(self,address):
        if address not in self.stores:raise ValueError('not a modeled JL_ADDA ADA-family word')
        entry=self.stores[address]
        if entry['writes']>0:
            self.reads_after_write+=1
            return entry['values'][-1]
        # 从未写过:显式合成初始字 0(AM 容器族先例;读值只进写回组装不进
        # 分支=初值不参与观察路径,敏感性探针 ones 分叉互证)。
        self.synthesized_initial_reads+=1
        return self._initial_word(address)

    def report(self):
        touched={hex(a):{'values':[hex(v) for v in e['values'][-8:]],
                         'writes':e['writes']}
                 for a,e in sorted(self.stores.items()) if e['writes']}
        proj={
            'addresses':[hex(a) for a in self.WORDS],
            'modeled_vs_touched':('modeled exactly the nine statically-verified ADA '
                                  'family words 0xfe0a20..0xfe0a40 (0xfe0a44 ADA_CON9 '
                                  'has zero app access sites and stays unmapped); '
                                  'touched this prefix: '
                                  +((', '.join(sorted(touched)) or 'none'))),
            'word_writes':self.word_writes,
            'stores':touched,
            'sdk_naming':('br28.h JL_ADDA_TypeDef +0x20..+0x40 = ADA_CON0..ADA_CON8 '
                          '(all-__u32 per the reference SDK header, not exact QCY '
                          'source — the type layout locates the words, it is NOT '
                          'evidence of plain-RW/reset-zero/side-effect-free silicon); '
                          'cross-check: reference audio_anc.c reads JL_ADDA->ADA_CON8 '
                          'as four 5-bit mic gains (bits 0/5/10/15), matching the '
                          '0x606de9c/0x606ee9a gain field setters on 0xfe0a40. '
                          'Erratum registered here (codex note): the route-B3 report '
                          'text labels 0xfe0a48 "ADDA_CON1" and 0xfe0a4c "unnamed"; '
                          'per the same type layout they are ADDA_CON0/ADDA_CON1 — '
                          'the B3 text stays byte-pinned for legacy reports and its '
                          'served address set is unchanged'),
            'whitelisted_words':('exactly the nine words with proven app access '
                                 '{0xfe0a20 ADA_CON0, 0xfe0a24 ADA_CON1, 0xfe0a28 '
                                 'ADA_CON2, 0xfe0a2c ADA_CON3, 0xfe0a30 ADA_CON4, '
                                 '0xfe0a34 ADA_CON5, 0xfe0a38 ADA_CON6, 0xfe0a3c '
                                 'ADA_CON7, 0xfe0a40 ADA_CON8}; only aligned 32-bit '
                                 'word accesses are served (sub-word/misaligned '
                                 'strictly unmapped); 0xfe0a44 ADA_CON9 has zero '
                                 'identified access sites and stays unmapped; '
                                 '0xfe0a48/0xfe0a4c stay with the route-B3 five-word '
                                 'container and 0xfe0a04 with the route-CV container '
                                 '(mutually exclusive whitelists). Re-audit boundary: '
                                 'the address whitelist only blocks NEW addresses — '
                                 'a NEW READ CONSUMER of an already-whitelisted word '
                                 '(status polling, branch, call argument) must be '
                                 're-classified before this container may serve it'),
            'access_face_completeness':('the face below is "the seven identified '
                                 'direct-li base-register sites (nine site '
                                 'instances) plus their offset accesses" — a '
                                 'constant-by-constant independent decode sweep; '
                                 'INDIRECT sources (constant-pool loads, computed '
                                 'addresses, pointers stored in RAM, cross-function '
                                 'argument passing) are NOT exhausted by this scan '
                                 'and remain an explicit boundary (codex note)'),
            'observed_shapes':('atomic config RMW chains (rmw.and.nmodimm/rmw.or.modimm) '
                               'plus ld.w→mask/insert→st.w compositions inside the '
                               'ADA config function 0x606b4d0..0x606c3a8 (309 RMW + 51 '
                               'loads, every load\'s value only composes a write-back), '
                               'pure clear-bit RMW sequences in the close family '
                               '(0x606f4c0/0x606f438), and channel-masked field '
                               'setters (0x606c3b0/0x606de9c/0x606ee9a) whose branches '
                               'test only config inputs (channel mask, config object '
                               'bytes); first stop was the ADA_CON0 bit5 clear at '
                               '0x606b5ee'),
            'readback_projection':{
                'addresses':[hex(a) for a in self.WORDS],
                'reads':self.reads_after_write,
                'synthesized_initial_reads':self.synthesized_initial_reads,
                'initial_value_origin':('synthetic_zero — an explicit experimental '
                    'assumption registered with this container switch (route-AM/CW '
                    'precedent; every read value only composes a write-back, no branch '
                    'consumes a loaded value, so the initial word does not enter any '
                    'observed path)'),
                'readback_semantics':('last firmware-written value; before any '
                    'firmware write the explicit synthetic initial word 0 — '
                    'derived-not-measured, NOT a reset-value claim; no hardware '
                    'self-update (ADC analog state, bias, calibration or audio '
                    'input path) is modeled — an explicitly enabled software '
                    'register-storage projection; it records that the ADA config '
                    'RMWs executed, it does NOT verify any audio input; read and '
                    'write (including the atomic RMW internal read) share ONE '
                    'container state — no re-zeroing on function entry'),
                'close_semantics':('the close/channel-off paths (0x606f4c0/0x606f438) '
                    'clear bits in the container\'s synthesized config word; that '
                    'does NOT mean a real analog channel is off, that hardware state '
                    'was cleared, or that any timing completed (codex wording)'),
                'known_limitation':('the audited sites\' read values only compose '
                    'config write-backs; the real registers\' reset values, writable '
                    'masks and read/write side effects are unverified, and the scan '
                    'does not prove the absence of other status consumers on these '
                    'words'),
            },
        }
        if self.ada_probe_initial is not None:
            proj['sensitivity_probe']={
                'ada_con0_probe_initial':hex(self.ada_probe_initial),
                'label':'sensitivity-probe-not-plausible-state',
                'shape':('one-shot initial-word substitution for ADA_CON0 only '
                         '(subsequent reads return the real written value); the '
                         'other eight words stay at synthetic 0 in both arms to '
                         'keep attribution clean'),
            }
        return proj


class P11LPVADConfigRegisters:
    """P11_SFR LPVAD VAD_ACON0 单字容器(合成初始 0+读回=当前值,opt-in,路 LPV)。

    背景:21,798,257 停点 0x606b68e——ADA 配置函数 0x606b4d0..0x606c3a8 顺序段
    0x606b5f2..0x606b68c 走完后,`li.u32 r1=0xF2B404`(0x606b688,全 app 唯一
    站点)之后首条 `rmw.or.modimm [r1+0] |= 0x10000000`(bit28 置位原子 RMW)
    的内部读撞 unmapped。0xf2b404 = P11_SFR LPVAD 组 p11_sfr_base+
    map_adr(0x14,0x01) 的 VAD_ACON0(参考 SDK p11_sfr.h P11_LPVAD_TypeDef
    第二字,低功耗语音活动检测;组基址 0xf2b400,13 字布局见下)。

    静态访问面(全 app 逐 2 字节位置独立解码扫描枚举 li 常量+数据区 4 字节
    字面值扫描+邻组站点数据流核查,方法照 ADA/DT):
      * 0xf2b400..0xf2b434 窗(每字枚举)唯一 li 站点 = 0x606b688
        `li.u32 r1=0xF2B404`,其后唯一访问即停点 RMW 本身(r1 随后被写覆盖,
        停点后顺序流 0x606b692 起全部是 ADA 窗/RAM,再无 0xf2b4xx 触达)。
      * 数据区无独立常量池字面值(全 app 扫描唯一命中=li 指令自身立即数字
        节 0x606b68a);0xf2b000..0xf2b5ff 整块 li 家族恰 2 站点,另一站点
        0x6068c20(li r1=0xF2B21C,他组)仅 st.w 两写即返回,差 0x1e4 不触达。
      * 读值数据流:窗内全 app 触达只有停点这一条原子 RMW——内部读进零
        GPR、零分支、零 call 参数;窗内无任何 ld.w 显式读(比 lp_timer 的
        ld→组装→st 消费链更弱,连显式读链都没有)。注意(codex 钉子):
        「零消费者」只限定为已识别的主核软件消费者——RMW 本身消费旧值
        (new=old|0x10000000),合成初始 0 下容器结果 0x10000000,真实执行
        则保留其余 31 位并进入写事务,潜在消费者含外设与 q32s,不能判死读。

    P11_LPVAD_TypeDef 布局(参考 SDK p11_sfr.h,sha256 与 data/dependencies.json
    一致):13 字全标 __RW __u32(VAD_CON +0/VAD_ACON0 +4 停点字/VAD_ACON1 +8/
    AVAD_CON +c/AVAD_DATA +10/DVAD_CON0 +14/DVAD_CON1 +18/DMA_BADR +1c/
    DMA_LEN +20/DMA_HPTR +24/DMA_SPTR +28/DMA_SPN +2c/DMA_SHN +30)——但
    __RW 宏值==__WO==volatile,R/W 区分只在注释,头标注不是硅读回证据
    (路 AL lp_timer 同款措辞);布局只定位字,不授权整组行为。

    分类(照 B3/DT/ADA 判据+codex 定调):AM 式合成配置族单字容器——
    合成初始 0+读回=当前值,「初始 0」与「读回=容器当前值」均为显式模型
    假设(synthetic-not-measured,非硅复位值/非静态推导结果)。白名单恰
    一字(已证实触达);其余 12 字零触达不进、留 unmapped 新读者重审;
    同址新读者(新 ld.w/新访问 PC/新 RMW 掩码/子字错位/其他核)亦须重审
    ——地址白名单只拦新地址,拦不住同址新增消费者。**新开关分立而非扩
    p11_window_projection 的白名单**:直接扩已开开关服务面=改契约(ADA
    codex 钉子同构),且 p11_window 是「纯写记录,读回一律 Blocked」族,
    配置读回语义与之族冲突;lp_timer_con_readback 的「未写过严格停」在
    首访即读的停点形状下无解(停点自身就是首次访问)。依赖边挂
    jl_adda_ada_family=当前执行路径先后关系(ADA 放行后链才到达停点),
    非 LPVAD 在硬件上依赖 ADDA(codex 措辞)。

    codex 备案的更弱替代(不采纳,记录假设强度区别):只记录「bit28 被置
    位」、其余 31 位保留未知、普通读继续阻断——假设强度更弱,但需要新
    的抽象传播机制(容器返回 UnknownWord 而非 int),违反最小改动原则;
    本容器沿用全字合成 0 的 AM 家族形态,两者假设强度差异在此明文保留。
    """

    # 白名单恰一字(已证实触达的静态地址集;其余 12 字零触达不进)。
    WORDS=(0xf2b404,)
    SDK_NAMES={0xf2b404:'P11_LPVAD->VAD_ACON0'}
    SYNTHETIC_INITIAL_WORD=0

    def __init__(self):
        self.stores={address:{'values':[],'writes':0} for address in self.WORDS}
        self.word_writes=0
        self.reads_after_write=0;self.synthesized_initial_reads=0

    def handles(self,address):
        return address in self.stores

    def write(self,address,value):
        if address not in self.stores:raise ValueError('not a modeled P11 LPVAD config word')
        if not isinstance(value,int) or not 0<=value<=0xffffffff:raise ValueError('unsigned32 word expected')
        entry=self.stores[address];entry['values'].append(value);entry['writes']+=1
        self.word_writes+=1

    def read(self,address):
        if address not in self.stores:raise ValueError('not a modeled P11 LPVAD config word')
        entry=self.stores[address]
        if entry['writes']>0:
            self.reads_after_write+=1
            return entry['values'][-1]
        # 从未写过:显式合成初始字 0(AM 容器族先例;读值只进 RMW 写回不进
        # 分支=初值不参与观察路径)。
        self.synthesized_initial_reads+=1
        return self.SYNTHETIC_INITIAL_WORD

    def report(self):
        touched={hex(a):{'values':[hex(v) for v in e['values'][-8:]],
                         'writes':e['writes']}
                 for a,e in sorted(self.stores.items()) if e['writes']}
        proj={
            'addresses':[hex(a) for a in self.WORDS],
            'modeled_vs_touched':('modeled exactly the one statically-verified LPVAD '
                                  'word 0xf2b404 (VAD_ACON0); the other twelve '
                                  'P11_LPVAD struct words have zero identified app '
                                  'access sites and stay unmapped); touched this '
                                  'prefix: '+((', '.join(sorted(touched)) or 'none'))),
            'word_writes':self.word_writes,
            'reads_after_write':self.reads_after_write,
            'synthesized_initial_reads':self.synthesized_initial_reads,
            'stores':touched,
            'sdk_naming':('reference SDK p11_sfr.h P11_LPVAD_TypeDef, group base '
                          'p11_sfr_base+map_adr(0x14,0x00)=0xf2b400; 13 all-__u32 '
                          'members VAD_CON/VAD_ACON0(stop word,+4)/VAD_ACON1/'
                          'AVAD_CON/AVAD_DATA/DVAD_CON0/DVAD_CON1/DMA_BADR/'
                          'DMA_LEN/DMA_HPTR/DMA_SPTR/DMA_SPN/DMA_SHN — the __RW '
                          'macro value equals __WO (volatile), the R/W distinction '
                          'is comment-only, so the header labels are NOT silicon '
                          'readback evidence; the type layout locates the word, it '
                          'is NOT evidence of plain-RW/reset-zero/side-effect-free '
                          'silicon'),
            'whitelisted_words':('exactly the one word with proven app access '
                                 '{0xf2b404 VAD_ACON0}; only aligned 32-bit word '
                                 'accesses are served (sub-word/misaligned stay '
                                 'unmapped); a NEW READ CONSUMER of this same '
                                 'address (a new ld.w, a new access PC, a new RMW '
                                 'mask, another core) must be re-audited before '
                                 'this container may serve it — the address '
                                 'whitelist only gates new addresses (codex)'),
            'cross_domain_limitations':('this projection only records the main '
                                 'core\'s synthesized config-word update of '
                                 'VAD_ACON0 at the identified atomic-RMW site; '
                                 'the synthetic initial 0 and the '
                                 'current-container-value readback are explicit '
                                 'model assumptions, not the silicon reset value '
                                 'or the actual readback behavior; the projection '
                                 'does NOT model q32s access to this word, LPVAD '
                                 'detection, cross-domain visibility/handshake, '
                                 'or read/write side effects; the main core '
                                 'passing this stop does NOT mean the real LPVAD '
                                 'is configured or running (codex wording). '
                                 'Known gaps kept open: another core may read/'
                                 'write the same word; bit28\'s actual role is '
                                 'unknown (config/trigger/other); atomicity and '
                                 'visibility ordering across main core/q32s/'
                                 'peripherals are unknown; the write may itself '
                                 'have effects — absence of an identified '
                                 'main-core reader does not prove the omission '
                                 'harmless'),
            'access_face_completeness':('the sweep covered direct immediate '
                                 'sites in the specified window, word-literal '
                                 'scans of the specified form, and dataflow '
                                 'checks of the identified neighbor-group '
                                 'sites; RAM-pointer loads, cross-function '
                                 'argument passing and arithmetic address '
                                 'construction are NOT exhausted; coverage is '
                                 'limited to the scanned main-core app and does '
                                 'NOT cover ROM, q32s or other access agents; '
                                 'finding no other direct site does NOT elevate '
                                 'to "the only access in the system" (codex '
                                 'wording)'),
            'model_assumptions':('synthetic_initial_word=0 and readback=current '
                                 'container value are explicit model assumptions '
                                 '(AM-family form, minimal-change choice); a '
                                 'WEAKER alternative — record only "bit28 set", '
                                 'keep the other 31 bits unknown, keep plain '
                                 'reads blocked — was considered and NOT taken '
                                 '(it would need a new unknown-word propagation '
                                 'path through the container); the assumption-'
                                 'strength difference is registered here '
                                 '(codex)'),
            'known_limitation':('the single audited access composes the RMW '
                                'write-back only; the real register\'s reset '
                                'value, writable mask and side effects are '
                                'unverified, and the scan does not prove the '
                                'absence of other consumers (peripherals, q32s) '
                                'of the written word'),
        }
        return proj


class JLAudioDacVl0Registers:
    """JL_AUDIO->DAC_VL0(0xfe051c)数字音量字的「写记录+读回」容器(opt-in,路 CW/S19 批 0b;任务卡称「路 BO」与既有 BO/sim-audiosfr 撞号,照 CT/CV 先例顺延记 CW)。

    背景:3,170,379 停点 0x606db4c 是 DAC_VL0 setter 0x606db36 内并行对
    `umin.rrr.p r1=umin(r4,r1) ∥ r3=[r2+0]` 第二半对 0xfe051c 的首读(本世界
    该字零写)。**下一停点分类=配置**(路 CV 裁定):br28.h __RW+路 BO 消费者
    全图(getter/setter/同步 walker/静音判零门控)全是配置面消费——对照
    DAA_CON2 bit1=状态位、CHIP_ID=身份。

    识别与证据(路 BO docs/appcore-tail-sfr-semantics.md §3 消费者全图 +
    本路反汇编复核,repo codec pin 3608ed1):
    - **setter 0x606db36 全函数**(运行态实测互证:r0=0xFF/r1=0/r2=0xfe051c/
      r3=0x1265C0/r4=0x3a98):`[--sp]={rets,r4}` → `r2=1883;r3=0x1265C0;
      r4=[r3+r2<<2]`(音量上限=设备结构槽 **[0x1265C0+1883*4]=[0x12832C]**,
      运行态值 0x3a98;**勘误:CV/BO 叙事记 [0x1272AC] 是算术笔误**——
      0x1265C0+0xCEC=0x1272AC 需下标 827,指令编码 li.s16 n=1883 钉死
      1883*4=0x1D6C→0x12832C)→ `r2=0xfe051c` → **0x606db4c 并行对
      `r1=umin(上限,新值) ∥ r3=[r2+0]` 首读** → 0x606db52 `if((r0&1)!=0)`
      t=2(t 钉死:无条件体恰 {r4=r3>>16;r3=r1},0x606db5a insert 是谓词尾)
      → 谓词 insert `r3<=insert(r4,p:16,l:16)`(低半字=钳后新值,高半字=
      读值保留)→ 0x606db5e `if((r0&2)!=0)` t=1 → `r3=r3.l(u)` + 谓词
      insert `r3<=insert(r1,p:16,l:16)`(高半字=钳后新值,低半字携带)→
      **0x606db68 `[r2+0]=r3` 写回在两个选择块之外**(未选通道=组装值原样
      写回,总线写仍发生)→ pop 返回。半字布局=低 16 位通道 0/高 16 位通道 1
      (getter 0x606daf0 按参数位选半字读回,互证)。
    - **读值参与度(本路核心判定,决定初值处理)**:读值 r3 只进两个 insert
      组装,**不进任何分支**(分支只测 r0 通道位);且本前缀唯一调用点
      0x6071b76 实参 r0=0xFF(双通道全选)/r1=0——两块都走时 `r3=r3.l(u)`
      把第一块的读值携带清零,**写回恒 0x0 与初值逐位无关**(读值只存在于
      两块之间的瞬态 r3)。按批 0b 任务判据「只影响数值不影响分支→合成 0+
      敏感性探针」:无写时的首读由**显式合成初始字 0**服务(路 AM 先例,
      AK 域一/二/三容器同族分支,derived-not-measured,非复位值声明),
      不立 CV 式显式 fixture 开关(DAA_CON1 用 fixture 是因为其保留位
      bits3,7..31 被写持久携带、读值实质进寄存器态)。
    - **敏感性探针(一次性分叉,ones 字 0xffffffff)**:与合成 0 对照——
      首读 0xffffffff 时第一块瞬态 r3=0xffff0000 仍被 `r3=r3.l(u)` 清零,
      写回同为 0x0、同步数与后续停点逐字段一致=观察路径对初值不敏感
      (结果钉进路 CW 叙事与单测)。「仿真顺利返回不能证明初值正确」。
    - **读回语义**:真实写入后读回=最后写值(uart CON1 路 AA/lp_timer CON
      路 AL/AK 域容器同族);本前缀 setter 之后无 0xfe051c 读(同步 walker
      0x606db74/静音 0x606dd16/getter 0x606daf0 都在音频任务域,D1 后)。
    - **范围边界**:白名单恰一字 0xfe051c;JL_AUDIO 块其余(DAC_LEN/almk/
      DMA 域 15 个 li 站点)保持 unmapped,不一次放行音频数字域(路 BO
      §5「D1 后另立证据包」)。运行效果(DAC 数字音量/模拟通路/输出)不
      建模——只记录「音量配置 RMW 已执行」。
    """

    ADDRESS=JL_AUDIO_DAC_VL0
    # 合成初始字(显式常量,报告分开计数;敏感性分叉一次性改类属性跑 ones 臂)。
    SYNTHETIC_INITIAL_WORD=0

    def __init__(self):
        self.stores={JL_AUDIO_DAC_VL0:{'values':[],'writes':0,'cores':[]}}
        self.word_writes=0
        self.reads_after_write=0;self.synthesized_initial_reads=0

    def handles(self,address):
        return address==JL_AUDIO_DAC_VL0

    def write(self,address,value,core):
        if address!=JL_AUDIO_DAC_VL0:raise ValueError('not the modeled JL_AUDIO DAC_VL0')
        if not isinstance(value,int) or not 0<=value<=0xffffffff:raise ValueError('unsigned32 word expected')
        entry=self.stores[address];entry['values'].append(value);entry['writes']+=1
        entry['cores'].append(core);self.word_writes+=1

    def read(self,address):
        if address!=JL_AUDIO_DAC_VL0:raise ValueError('not the modeled JL_AUDIO DAC_VL0')
        entry=self.stores[address]
        if entry['writes']>0:
            self.reads_after_write+=1
            return entry['values'][-1]
        # 从未写过:显式合成初始字 0(路 AM 先例;读值只进 insert 不进分支、
        # 双通道写回恒 0x0=初值不参与观察路径,敏感性分叉 ones 字互证)——
        # derived-not-measured 显式假设,不是复位值声明。
        self.synthesized_initial_reads+=1
        return self.SYNTHETIC_INITIAL_WORD

    def report(self):
        return {'addresses':[hex(JL_AUDIO_DAC_VL0)],
                'word_writes':self.word_writes,
                'stores':{hex(a):{'values':[hex(v) for v in e['values'][-16:]],
                                  'writes':e['writes'],'cores':e['cores'][-16:]}
                          for a,e in sorted(self.stores.items()) if e['writes']},
                'sdk_naming':('br28.h JL_AUDIO = hs_base + map_adr(0x05, 0x00) = 0xfe0500, '
                              'all-__RW JL_AUDIO_TypeDef with 32-bit word slots (csfr.h '
                              '__u16 widening), DAC_VL0 at +0x1c = member 7; route BO pinned '
                              'the four consumers (getter 0x606daf0 / setter 0x606db36 / '
                              'volume-sync walker 0x606db74 / mute zero-gate 0x606dd16) — '
                              'all config-plane consumption, the CONFIG classification; the '
                              'volume cap comes from device-struct slot [0x1265C0+1883*4] = '
                              '0x12832C (runtime 0x3a98; the route-CV/BO narrative 0x1272AC '
                              'was an arithmetic slip — li.s16 pins index 1883)'),
                'whitelisted_words':('exactly the one JL_AUDIO->DAC_VL0 word; the rest of the '
                                     'JL_AUDIO digital block (DAC_LEN/almk/DMA — the 15 li '
                                     'sites of route BO) stays unmapped, the audio digital '
                                     'domain is NOT released wholesale (route BO §5: a '
                                     'separate evidence package after D1)'),
                'observed_shapes':('function-granularity RMW inside the setter 0x606db36: the '
                                   'second slot of the parallel pair at 0x606db4c '
                                   '(`umin.rrr.p r1=umin(r4,r1) ∥ ld.w r3=[r2+0]`) is the '
                                   'old-value load; two channel-select blocks (if t=2: '
                                   '{r4=r3>>16; r3=r1} + predicated insert bits16..31; '
                                   'if t=1: {r3=r3.l(u)} + predicated insert bits16..31) '
                                   'compose the write-back which sits OUTSIDE both blocks '
                                   'at 0x606db68; with r0=0xFF (both channels, the only '
                                   'call site 0x6071b76 on this prefix, r1=0) the '
                                   'r3=r3.l(u) of the second block erases every bit the '
                                   'first block carried from the READ — the written word '
                                   'is 0x0 regardless of the initial state, the read value '
                                   'lives only in the transient r3 between the blocks and '
                                   'never reaches a branch'),
                'readback_projection':{
                    'addresses':[hex(JL_AUDIO_DAC_VL0)],
                    'reads':self.reads_after_write,
                    'synthesized_initial_reads':self.synthesized_initial_reads,
                    'readback_semantics':('last firmware-written value (route-AA uart-CON1 '
                        'final form); before any firmware write the explicit synthetic '
                        'initial word 0 (route-AM precedent, the route-AK domain-one/two/'
                        'three container family) is served — derived-not-measured, NOT a '
                        'reset-value claim; unlike route-CV DAA_CON1 there is NO separate '
                        'initial-fixture switch: the read value only composes the insert '
                        'write-back (no branch), and on this prefix the both-channels call '
                        'overwrites it entirely, so the synthetic-zero branch carries no '
                        'observation weight'),
                    'sensitivity_probe':('one-shot fork with the all-ones word 0xffffffff: '
                        'first-block transient r3=0xffff0000 is still cleared by '
                        'r3=r3.l(u), the write-back stays 0x0 and the stop/step shape is '
                        'identical — the observation path is initial-word-insensitive '
                        '(route-CW fork, results pinned in the route narrative and the '
                        'unit test); a clean return does NOT prove the initial word right'),
                    'no_poll_risk':('the setter is not a poll (single read, single '
                        'write-back, no loop); the sync walker 0x606db74 does '
                        'read-compare-correct on DAC_VL0 halfwords but lives in the '
                        'audio-task domain outside this prefix')},
                'assumption':('write record plus last-write readback with an explicit '
                              'synthetic initial 0 before the first write of JL_AUDIO->'
                              'DAC_VL0 (derived-not-measured); stored values are real '
                              'executed instruction results, never invented; no DAC digital '
                              'volume effect, analog path or audio output is modeled — this '
                              'records that the volume configuration RMW executed, it does '
                              'NOT verify any audio output')}


class JLAudioDacCopRegisters:
    """JL_AUDIO->DAC_COP(0xfe0534)写侧 write-only 整字记录(opt-in,路 BM/S19 批 1 ④)。

    背景:3,202,148 停点 0x606ab2c(S19 批 0b/路 CW 放行 DAC_VL0 +31,769 步后的
    下一停点)是 W1[3] 尾段输出级门控函数 0x606ab0a 的 `st.w.modimm [r5+52]=0x7F`
    (r5=JL_AUDIO 基址 0xfe0500,+0x34=成员 13 **DAC_COP**,br28.h 标 **__WO**,
    DAC chopper/泵配置字)。写侧三选一按消费面判定(路 CV 分类先例):
    - **全 app 恰两处访问、全部纯写 0x7F、零读回**:①本停点 0x606ab2c(AK 容器
      服务 [r5+88] AUD_CON|=0x100 与 [r5+0] DAC_CON 五连 RMW 之后的尾写,
      本路 codec 复核:0x606ab0e li r5=0xfe0500→0x606ab14..28 六条原子 RMW→
      0x606ab2c st.w.modimm {n:13,m:127});②0x606e032 `li r5=0xfe0534` 直存
      +0x606e038 st.w.modimm=0x7F(esco/ANC 邻域函数,音频任务域、本前缀
      不可达)。D2 场景表(docs/d2-audio-first-scene.md §3)把 0xfe0534 列进
      「RMW+读回消费」族——那是 D1 后音频任务域的消费面,到达时再逐地址
      分类(路 BO §5「D1 后另立证据包」同一纪律)。
    - **裁决**:__WO 裁剪操作寄存器+本前缀无读侧 → write-only 整字记录
      (p3_ls/jl_uart_init_writes 同族:全字覆盖写、旧值无关、值=真实指令
      结果);读回/RMW 一律显式严格停(显式 kind 优先于泛 unmapped 便于
      诊断)。运行效果(chopper/泵电路)不建模。
    - 白名单恰一字 0xfe0534;JL_AUDIO 数字域其余成员维持 unmapped(路 CW
      DAC_VL0 白名单边界不变)。
    """

    ADDRESS=JL_AUDIO_DAC_COP

    def __init__(self):
        self.stores={JL_AUDIO_DAC_COP:{'values':[],'writes':0,'cores':[]}}
        self.word_writes=0

    def handles(self,address):
        return address==JL_AUDIO_DAC_COP

    def write(self,address,value,core):
        if address!=JL_AUDIO_DAC_COP:raise ValueError('not the modeled JL_AUDIO DAC_COP')
        if not isinstance(value,int) or not 0<=value<=0xffffffff:raise ValueError('unsigned32 word expected')
        entry=self.stores[address];entry['values'].append(value);entry['writes']+=1
        entry['cores'].append(core);self.word_writes+=1

    def read(self,address):
        raise Blocked('jl_audio_dac_cop_read_not_modeled',address=hex(JL_AUDIO_DAC_COP))

    def report(self):
        return {'addresses':[hex(JL_AUDIO_DAC_COP)],
                'word_writes':self.word_writes,
                'stores':{hex(a):{'values':[hex(v) for v in e['values'][-16:]],
                                  'writes':e['writes'],'cores':e['cores'][-16:]}
                          for a,e in sorted(self.stores.items()) if e['writes']},
                'sdk_naming':('JL_AUDIO = hs_base + map_adr(0x05, 0x00) = 0xfe0500 (br28.h all-__RW '
                              'JL_AUDIO_TypeDef), DAC_COP at +0x34 = member 13 marked __WO (the DAC '
                              'chopper/pump configuration word)'),
                'whitelisted_words':('exactly the one JL_AUDIO->DAC_COP word; the rest of the JL_AUDIO '
                                     'block keeps its route-CW boundaries (the digital domain stays '
                                     'unmapped, a separate evidence package after D1)'),
                'observed_shapes':('whole-word stores of 0x7F only: 0x606ab2c st.w.modimm [r5+52] '
                                   '(the W1[3]-tail DAC-output gate function 0x606ab0a, r5=0xfe0500, '
                                   'right after the AUD_CON|=0x100 and five DAC_CON RMWs served by the '
                                   'route-AK container) and 0x606e038 st.w.modimm [r5+0] (li '
                                   '0xfe0534 direct store in the ADC start function 0x606df66, '
                                   'reached by the route-JLA segment right after the set_buffer '
                                   'five writes); zero readback sites in the whole '
                                   'app (the D2-scene RMW/read classification of 0xfe0534 belongs to '
                                   'the post-D1 audio-task domain)'),
                'write_only':True,'readback_not_modeled':True,
                'assumption':('write-only word record (a simulator capability; the br28.h __WO mark '
                              'plus the all-write/no-read consumption face motivate it, not a '
                              'measured WO property claim); stored values are real executed results '
                              '(0x7F), never invented; no chopper/pump circuit or audio output is '
                              'modeled — this records that the DAC output-stage gate store executed')}


class JLAudioDacDmaWriteRecords:
    """JL_AUDIO DAC DMA 配置四字(0xfe0504/08/10/14)写侧 write-only 整字记录
    (opt-in,路 BM §4.3 行 0b 勘误之二/S19 批 1 ④续)。

    背景:批 1 放行 DAC_COP(3,202,148 停点)后 +35 步撞 0x6071e92 写
    0xfe0508=1——W1[3] 尾段 DAC 启动函数 0x6071e74 在 call 0x606ab0a(输出级
    门控,路 CW 停点所在函数)返回后的四个整字写(BM §4.3 行 0b「W1[3] 尾段
    …通过」的第二次勘误:AK/CV/CW 白名单之外该尾段还有 DMA 配置四字)。

    证据(本路 codec 复核 + 一次性探路运行实证,探路脚本不进仓):
    - 写序列 0x6071e92..0x6071ea8:`[r10+8]=r4(1)`、`[r10+4]=r0`([r11+48+52]
      装载的 DMA 缓冲首址 0x18e180——与 0x6071d50 ROM memset 目的 0x18e180
      同址互证,CW 路 60KB 缓冲清零的消费者)、`[r10+20]=0`、`[r10+16]=0`,
      随后 `[r10+0]|=0x110`(DAC_CON,AK 容器服务)。
    - 探路运行(bl整个 hs 音频域写放行的分叉世界)实证该前缀上这四字
      **恰四写、零读回、零 RMW**——D2 场景表(docs/d2-audio-first-scene.md
      §3)的「0xfe0504 DMA 指针/计数读×6」消费面属 tone/PCM 推送域
      (0x607236c,caller 0x607249c/0x6073258,D1 后),到达时再逐地址分类
      (路 BO §5「D1 后另立证据包」同一纪律)。
    - 裁决:纯配置整字写(全字覆盖、旧值无关、值=真实指令结果)→
      write-only 记录(p3_ls/DAC_COP 同族);读回显式严格停(显式 kind
      优先于泛 unmapped);DMA 传输/IRQ39/音频数据流一律不建模。
    """

    ADDRESSES=JL_AUDIO_DAC_DMA_WORDS

    def __init__(self):
        self.stores={address:{'values':[],'writes':0,'cores':[]} for address in self.ADDRESSES}
        self.word_writes=0

    def handles(self,address):
        return address in self.stores

    def write(self,address,value,core):
        if address not in self.stores:raise ValueError('not a modeled JL_AUDIO DAC DMA word')
        if not isinstance(value,int) or not 0<=value<=0xffffffff:raise ValueError('unsigned32 word expected')
        entry=self.stores[address];entry['values'].append(value);entry['writes']+=1
        entry['cores'].append(core);self.word_writes+=1

    def read(self,address):
        raise Blocked('jl_audio_dac_dma_read_not_modeled',address=hex(address))

    def report(self):
        return {'addresses':[hex(a) for a in self.ADDRESSES],
                'word_writes':self.word_writes,
                'stores':{hex(a):{'values':[hex(v) for v in e['values'][-16:]],
                                  'writes':e['writes'],'cores':e['cores'][-16:]}
                          for a,e in sorted(self.stores.items()) if e['writes']},
                'sdk_naming':('JL_AUDIO = hs_base + map_adr(0x05, 0x00) = 0xfe0500 (br28.h all-__RW '
                              'JL_AUDIO_TypeDef); the four words at +0x04/+0x08/+0x10/+0x14 are the '
                              'DAC DMA configuration block — member names per the route-AG '
                              'wlaud-init-map layout chain (ADR/LEN/HRP/PNS whole-word writes, '
                              '[SDK-inferred] naming level)'),
                'whitelisted_words':('exactly the four DAC DMA words of the W1[3]-tail start '
                                     'function 0x6071e74; the D2-scene DMA pointer/count reads of '
                                     '0xfe0504/08 belong to the post-D1 tone/PCM push domain and '
                                     'stay strictly stopped'),
                'observed_shapes':('whole-word stores only, right after the output-stage gate call '
                                   'returns: 0x6071e92 [r10+8]=1, 0x6071e9e [r10+4]=0x18e180 (the '
                                   '60KB DMA buffer base, same address as the 0x6071d50 ROM memset '
                                   'destination — the route-CW buffer-clear consumer), 0x6071ea4 '
                                   '[r10+20]=0, 0x6071ea8 [r10+16]=0; the following [r10+0]|=0x110 '
                                   'is DAC_CON served by the route-AK container; a pathfinding run '
                                   'with the whole hs audio write family released proved exactly '
                                   'four writes, ZERO reads and ZERO RMWs on these words before '
                                   'the next boundary'),
                'write_only':True,'readback_not_modeled':True,
                'assumption':('write-only word records (values are real executed results, never '
                              'invented; the zero-read consumption face on this prefix motivates '
                              'the record, not a measured WO property claim); no DMA transfer, '
                              'no IRQ39 event and no audio data flow is modeled — this does NOT '
                              'verify any audio output')}


class JLAudioAdcDmaWriteRecords:
    """JL_AUDIO ADC 通道缓冲配置五字(0xfe053c/40/44/48/4c)写侧 write-only
    整字记录(opt-in,路 JLA)。

    背景:21,800,325 停点 0x606df1e——set_buffer 0x606dee4(函数
    [0x606dee4,0x606df3a),call-xref 边界)并行对 mul.rr.p 的第二半
    store `[r4+4]=r0`(r4=0xfe0538)写 0xfe053c=ADR(值=真实指令结果),
    是 D2-b 注入蓝图预案的 set_buffer 五写 {+0x3c ADR/+0x40 LEN/
    +0x44 PNS/+0x48 HWP=0/+0x4c SRP=0} 之首。五写同基本块连续
    (0x606df20/26/2a/2e/30,中间只有 uextra/lsr 算术),写完一条 RAM
    写(缓冲指针槽 [0x1265c0+1170*4])即返回——AK 先例(wl_aud/audio_dac_init
    纯写集一次打包)的「整族一次打包」候选直接成立;放行后仍由真实指令
    逐条执行,不合成原子事务。

    静态访问面(全 app 逐 2 字节独立解码枚举 li 常量+每站点线性窗数据流
    解析+数据区 4 字节字面值扫描,方法照 ADA/DT/LPV;codex 独立复跑互证):
      * 0xfe0500..0xfe0560 窗全 app li 基址站点 33 个(全部装载
        0xfe0500/04/08/1c/24/34/38,无直接装载五字地址的站点);五字访问
        解析(寄存器存活跟踪)恰 9 条——set_buffer 五写 + audio_adc_
        irq_handler [0x6072628,0x6072700) 的 PNS/ADR/SRP 三读与 PNS 一写
        (唯一直接调用者=IRQ68 分发器 0x602f444;读的是运行期 DMA 指针
        状态,算缓冲位置后 call r3 挂 handler 链、写 SRN 0xfe0550 与 PNS、
        ADC_CON|=0x40 清完成)。
      * 数据区 4 字节字面值:窗内值 33 命中全部=li.u32 站点 [site+2,site+6)
        立即数字段,零独立常量池字面值(路内脚本首版分类坐标混用误标,
        codex 复核后以精确立即数字段判定为准)。
      * handler 的三读一写当前前缀不可达:IRQ68 交付未建模,分发器从未
        被调用;「当前未到达」不升级为「所有未来路径不可达」(codex 措辞)。
      * 间接来源(RAM 指针装载/跨函数传参/运算构造地址)未穷尽;覆盖限
        本次扫描的主核 app,不覆盖 ROM/q32s/其他访问主体。

    裁决(codex 定调,gpt-6-astra):(a) write-only 记录恰五字——与 BM 路
    DAC DMA 四字先例完全同构(同 struct 相邻成员、同 set_buffer 形状、
    同「读属后域留严格停」);拒绝 (b) 合成读回容器的最充分理由=当前推进
    不需要任何读回,而增加读回会提前承诺尚未建立的消费语义(handler 读
    PNS/ADR/SRP,HWP 是硬件写指针、SRP 推进机制不能凭名断定,「读回=最后
    写值」对运行期 DMA 语义不足);0xfe0550(handler 尾的 SRN 读通知字)
    属另一段消费事务不收。动态证据等级:停点报告证明首写到达并被拒,
    后四写与返回是静态顺序流证据,放行重跑后才报告五写完成与实际值。
    """

    ADDRESSES=JL_AUDIO_ADC_DMA_WORDS

    def __init__(self,delivery=False):
        # 路 D2AI(sim-d2ai,预研 docs/d2-audio-input-pre-study.md §7 步 1):
        # 交付半边扩类(opt-in delivery)——五字读回投影(最后写值/无写合成 0,
        # HWP=模型写指针)+SRN 0xfe0550 并入白名单+pending/delivery_in_flight
        # 簿记+produce_block(DMA 完成事件:按 ADR/LEN/PNS/HWP 计算当前窗口
        # 写入交织样本块、HWP 推进、置 ADC_CON bit7 pending)。默认关:行为与
        # 路 JLA write-only 半边逐字节一致(读仍 jl_audio_adc_dma_read_not_
        # modeled 严格停、SRN 保持 unmapped)。
        self.delivery=delivery
        self.stores={address:{'values':[],'writes':0,'cores':[]} for address in self.ADDRESSES}
        if delivery:self.stores[JL_AUDIO_ADC_SRN]={'values':[],'writes':0,'cores':[]}
        self.word_writes=0
        self.pending=False             # ADC_CON bit7 投影源(硬件 DMA 完成置位)
        self.delivery_in_flight=False  # 每 pending 片至多抛一次 IrqDeliveryRequest
        self.hwp=0                     # 模型侧硬件写指针累计样本数(单调;环形
                                       # 位置=hwp%LEN——路 D2AJ 绕回投影,读回/
                                       # 窗口都按环内位置折算)
        self.blocks_produced=0
        self.con_w1c_acks=0            # |=0x40 W1C 清 pending 的真实执行次数
        self.reads_served=0;self.synthesized_reads=0

    def handles(self,address):
        return address in self.stores

    def write(self,address,value,core):
        if address not in self.stores:raise ValueError('not a modeled JL_AUDIO ADC DMA word')
        if not isinstance(value,int) or not 0<=value<=0xffffffff:raise ValueError('unsigned32 word expected')
        entry=self.stores[address];entry['values'].append(value);entry['writes']+=1
        entry['cores'].append(core);self.word_writes+=1
        if self.delivery and address==0xfe0548:
            # HWP 软件写复位(0x606df2e,set_buffer 第四写):模型侧写指针跟随
            # 写值重置(INJ-01「hwp 软件写 0 复位」;此后推进只归 produce_block)。
            self.hwp=value

    def read(self,address):
        if not self.delivery:
            raise Blocked('jl_audio_adc_dma_read_not_modeled',address=hex(address))
        if address not in self.stores:raise ValueError('not a modeled JL_AUDIO ADC DMA word')
        self.reads_served+=1
        entry=self.stores[address]
        if address==0xfe0548:
            # HWP 是硬件写指针:读回=模型侧当前环内位置(软件复位值+
            # produce_block 推进后对 LEN 取模——环形缓冲的指针形状),
            # 不是「最后写值」——运行期 DMA 语义(PRE §6.3:读回=最后写值
            # 对 HWP 不足)。app 静态面无人读 HWP(audio handler 只读
            # PNS/ADR/SRP),本投影只服务容器完整性/场景观察。
            length=self.stores[0xfe0540]['values'][-1] if self.stores[0xfe0540]['writes'] else 0
            return self.hwp%length if length>0 else self.hwp
        if address==0xfe054c:
            # SRP=硬件域读指针(路 D2AJ b3/b4):app 侧唯一读点=audio handler
            # 0x607265a(算 data=ADR+SRP×ch×ssize),无人写(除 set_buffer 复位
            # 0)——推进只属硬件域。投影=「最近完成块的环内起点」=(hwp-PNS)
            # mod LEN(交付时刻读,produce_block 已推进 hwp)。b2 实测互证:
            # block1 交付时 SRP 读 0、data=ADR+0。若该语义与硅不符,场景的
            # 逐字节回读断言会 FAIL(可证伪设计),不静默。
            if self.blocks_produced:
                length=self._recorded(0xfe0540,'LEN');pns=self._recorded(0xfe0544,'PNS')
                return (self.hwp-pns)%length
            # 未完成任何块:走通用「最后写值/合成 0」容器面(set_buffer 写 0)。
        if entry['writes']>0:return entry['values'][-1]
        # 无写显式合成 0(路 AM 先例,derived-not-measured;本路径在开链
        # set_buffer 之后才会被读,该分支是容器完整面)。
        self.synthesized_reads+=1
        return 0

    def note_con_write(self,value):
        """ADC_CON(0xfe0538)整字/RMW 写回的 W1C 联动(delivery 模式)。

        分发器 0x602f434 与 handler 尾 0x60726ec 的 `|=0x40`(经通用 mmio 路径
        读投影值再写回)是真实清 pending 写:bit6 置位的写清 pending+解除
        delivery_in_flight(两处真实清两次无害,INJ-01)。bit7 是硬件域
        (pending 投影在读侧 OR,存储值不保留)——本函数不动 stores 记录。
        """
        if not self.delivery:return
        if value&0x40:
            self.pending=False;self.delivery_in_flight=False;self.con_w1c_acks+=1

    def produce_block(self,mem,block,*,channels,sample_bytes):
        """DMA 完成事件投影:把一个 PNS 样本块的交织字节写进当前窗口。

        唯一合成输入=样本字节本身(按键先例的「RES 档位值」同一深度,PRE
        §4.2 方案 a);窗口位置/块长全部按固件 set_buffer 写下的 ADR/LEN/PNS
        与模型 HWP 推导,不发明地址。步序(INJ-01):①按记录的 ADR/LEN/PNS/
        HWP 计算当前窗口写块 ②HWP 推进过块边界+置 pending(bit7)。
        channels=b[hdl+79](活跃通道计数,单字节直读)、sample_bytes=2 或 4
        (b[hdl+90] 宽度布尔)——两者由调用方从真实 RAM 读出传入。
        """
        if not self.delivery:raise ValueError('produce_block requires the delivery projection')
        if not isinstance(channels,int) or channels<1:raise ValueError('channel count must be a positive int')
        if sample_bytes not in (2,4):raise ValueError('sample width must be 2 or 4 bytes')
        adr=self._recorded(0xfe053c,'ADR');length=self._recorded(0xfe0540,'LEN');pns=self._recorded(0xfe0544,'PNS')
        if length<=0 or pns<=0:
            raise Blocked('jl_audio_adc_dma_window_unconfigured',ADR=hex(adr),LEN=length,PNS=pns)
        expected=pns*channels*sample_bytes
        if len(block)!=expected:
            raise ValueError('block must be exactly PNS*channels*sample_bytes bytes '
                             '(%d*%d*%d=%d), got %d'%(pns,channels,sample_bytes,expected,len(block)))
        span=length*channels*sample_bytes
        # 环形窗口(路 D2AJ 绕回投影):hwp 单调累计,环内位置=hwp%LEN;
        # PNS 整除 LEN 时块界恰好对齐环尾(越界即回 0),否则跨环尾的块
        # 保持诚实停(硅上跨块搬运形状未测,不发明)。
        offset=(self.hwp%length)*channels*sample_bytes
        if offset+expected>span:
            raise Blocked('jl_audio_adc_dma_ring_straddle_not_modeled',
                          ring_pos_samples=self.hwp%length,
                          window_offset=hex(offset),span_bytes=hex(span),block_bytes=hex(expected))
        window=adr+offset
        # MMU 逐页分块写(大缓冲在虚拟堆 0x41xxxx,128B 页界跨界整块写会被
        # mmu_cross_page_access_not_split 拒绝;按页内长度切片=逐访问翻译的
        # DMA 总线形状,与固件逐指令写同一翻译面)。
        data=bytes(block)
        cursor=window
        while data:
            chunk=len(data) if (cursor&127)+len(data)<=128 else 128-(cursor&127)
            mem.write(cursor,data[:chunk])
            cursor+=chunk;data=data[chunk:]
        # HWP 单调累计(环内位置/圈数由 hwp%LEN 与 hwp//LEN 派生,不加状态
        # 字段——快照 M4 面零变化);对齐绕回不静默折返数据,只折返写位置。
        self.hwp=self.hwp+pns
        self.pending=True;self.blocks_produced+=1
        return {'window':hex(window),'block_bytes':expected,'pns_samples':pns,
                'channels':channels,'sample_bytes':sample_bytes,
                'hwp_after':self.hwp,'ring_pos_after':self.hwp%length,
                'ring_wraps':self.hwp//length,'pending':True}

    def _recorded(self,address,name):
        entry=self.stores.get(address)
        if entry is None or entry['writes']==0:
            raise Blocked('jl_audio_adc_dma_word_unwritten',word=name,address=hex(address))
        return entry['values'][-1]

    def report(self):
        result={'addresses':[hex(a) for a in self.ADDRESSES],
                'word_writes':self.word_writes,
                'stores':{hex(a):{'values':[hex(v) for v in e['values'][-16:]],
                                  'writes':e['writes'],'cores':e['cores'][-16:]}
                          for a,e in sorted(self.stores.items()) if e['writes']},
                'sdk_naming':('JL_AUDIO = hs_base + map_adr(0x05, 0x00) = 0xfe0500 (br28.h all-__RW '
                              'JL_AUDIO_TypeDef, csfr.h __u16 -> 32-bit word slots); the five words '
                              'at +0x3c/+0x40/+0x44/+0x48/+0x4c are the ADC channel-buffer DMA '
                              'configuration block written by set_buffer 0x606dee4 — member names '
                              '(ADR/LEN/PNS/HWP/SRP) follow the D2-b injection blueprint and the '
                              'route-AG wlaud-init-map layout chain ([SDK-inferred] naming level; '
                              'the __RW labels locate the words, they are NOT silicon WO evidence '
                              'and NOT a claim that these words ignore reads on silicon)'),
                'whitelisted_words':('exactly the five set_buffer words {0xfe053c ADR, 0xfe0540 '
                                     'LEN, 0xfe0544 PNS, 0xfe0548 HWP, 0xfe054c SRP}; ADC_CON '
                                     '+0x38 stays with the route-AS container and the handler-'
                                     'tail SRN word 0xfe0550 (a different consumption '
                                     'transaction) is NOT included; only aligned 32-bit whole-'
                                     'word overwrite stores are served — sub-word, misaligned '
                                     'and cross-word accesses stay unmapped; a NEW consumer of '
                                     'any of these addresses (a new read PC, a new write PC, a '
                                     'new access shape, another core) must be re-audited before '
                                     'this container may serve it — the address whitelist only '
                                     'gates addresses, it does not stop a same-address new '
                                     'writer at runtime (codex wording)'),
                'observed_shapes':('five consecutive whole-word stores in one basic block of '
                                   'set_buffer 0x606dee4: 0x606df20 [r4+4]=r0 (ADR, the stop '
                                   'word, the store half of the parallel pair mul.rr.p at '
                                   '0x606df1e — the same parallel-store shape as 0x60719c0 '
                                   'recorded by routes AS/AN), 0x606df26 [r4+8]=r2 (LEN, '
                                   'uextra(buf_num*len,1,16)), 0x606df2a [r4+12]=r1 (PNS, '
                                   'len>>1), 0x606df2e [r4+16]=r3 and 0x606df30 [r4+20]=r3 '
                                   '(HWP=0, SRP=0); every value is a real instruction result, '
                                   'never invented; dynamic evidence at the stop = the FIRST '
                                   'write arrived and was refused, the remaining four plus the '
                                   'return path are static sequential-flow evidence — this '
                                   'release rerun reports the completed writes and their '
                                   'actual values; the words are executed one by one, NOT '
                                   'composed into one atomic transaction'),
                'write_only':True,'readback_not_modeled':True,
                'consumption_face':('the only other identified in-window accesses are the '
                                    'audio_adc_irq_handler [0x6072628,0x6072700) reads of '
                                    'PNS/ADR/SRP (three reads computing buffer positions before '
                                    'the output-handler chain call) plus its PNS/SRN 0xfe0550 '
                                    'writes — reached only through the IRQ68 dispatcher '
                                    '0x602f444, NOT reached on this prefix (IRQ68 delivery is '
                                    'not modeled); "not reached now" does NOT mean unreachable '
                                    'on all future paths (codex); those reads stay strictly '
                                    'stopped under the explicit kind and belong to the D2-b '
                                    'completion-projection domain'),
                'assumption':('write-only word records (values are real executed results, never '
                              'invented; the zero-read face on THIS prefix motivates the record, '
                              'it is NOT a measured hardware WO property claim); no DMA '
                              'transfer, no pointer advance, no SRN side effect, no pending '
                              'bit, no IRQ68 delivery and no cache coherency is modeled — this '
                              'does NOT verify any audio input, ADC capture or D2-b acceptance '
                              'scenario'),
                **({'delivery_projection':{
                    'enabled':True,
                    'words_served':[hex(a) for a in sorted(self.stores)],
                    'srn_word_added':hex(JL_AUDIO_ADC_SRN),
                    'read_semantics':('ADR/LEN/PNS/SRN = last firmware-written value (synthetic '
                                      'initial 0 when never written, route-AM precedent); HWP = '
                                      'the model-side hardware write pointer read back as its '
                                      'ring position (software write resets it at set_buffer '
                                      '0x606df2e, produce_block advances the monotonic count '
                                      'by PNS samples per block, read-back folds it mod LEN — '
                                      'route D2AJ); SRP = the hardware-domain read pointer '
                                      'projected as the freshest completed block origin '
                                      '((hwp-PNS) mod LEN at read time; the only app reader is '
                                      'audio handler 0x607265a computing data=ADR+SRP*ch*ssize; '
                                      'falsified by the per-block byte-equal scene assertions '
                                      'if wrong)'),
                    'pending_bit':'ADC_CON bit7 reads back set iff the model DMA completed a '
                                  'block and no real |=0x40 W1C has acknowledged it since',
                    'w1c_semantics':'any firmware write to ADC_CON with bit6 set clears pending '
                                    'and delivery_in_flight (dispatcher 0x602f434 and handler '
                                    'tail 0x60726ec both write it; twice is harmless)',
                    'produce_block_shape':('writes exactly PNS*channels*sample_bytes '
                                           'interleaved bytes at ADR+(hwp mod LEN)*channels*'
                                           'sample_bytes (the ONLY synthetic input is the sample '
                                           'bytes; window/length derive from the firmware-'
                                           'written ADR/LEN/PNS); advances the monotonic HWP '
                                           'count by PNS and sets pending — DMA completion '
                                           'timing is a projection; aligned ring wrap (PNS '
                                           'dividing LEN) folds the write position back to the '
                                           'ring start (route D2AJ, two-full-lap b4 evidence '
                                           'face), a straddling block stays honestly stopped '
                                           '(jl_audio_adc_dma_ring_straddle_not_modeled)'),
                    'state':{'pending':self.pending,'delivery_in_flight':self.delivery_in_flight,
                             'hwp_samples_monotonic':self.hwp,
                             'ring_pos_samples':(self.hwp%self.stores[0xfe0540]['values'][-1]
                                                 if self.stores[0xfe0540]['writes'] else self.hwp),
                             'ring_wraps':(self.hwp//self.stores[0xfe0540]['values'][-1]
                                           if self.stores[0xfe0540]['writes'] else 0),
                             'blocks_produced':self.blocks_produced,
                             'con_w1c_acks':self.con_w1c_acks,'reads_served':self.reads_served,
                             'synthesized_reads':self.synthesized_reads},
                    'synthetic_not_measured':True}} if self.delivery else {})}
        return result


class JlEqInitWriteRecords:
    """JL_EQ 初始化写域(JL_EQ->CON0 0xfe1500 / FLT_ADR 0xfe151c)写侧
    write-only 整字记录(opt-in,路 JLEQ/sim-jleq;codex gpt-6-astra 定调)。

    背景:29,215,313 停点 0x6072b92——EQ 初始化函数 [0x6072b24,0x6072bb2)
    (路 RAND 放行 JL_RAND 种子后到达)的 `st.w [r0+0]=r5` 写 0xfe1500
    =JL_EQ->CON0(值 0,unmapped_or_readonly_write,size 4)。同函数尾段
    0x6072bae `st.w [r0+28]=r1` 再写 0xfe151c=FLT_ADR(=分配缓冲指针)。
    JL_EQ 是数据驱动地址族(S19MAP #7 漏判根因):0xfe1500 在 app.bin 仅
    1 处出现=.data 段 0x60c3748(EQ 硬件描述符模板,随 .data 拷进 RAM
    0x124c40 实例表),零 li 常量——经实例指针 [0x12727C]+16 间接装载,
    li 枚举法免疫。

    函数流(反汇编复核,codex 独立复跑互证):0x6072b30 读 b[0x127280]
    (实例选择字节)→ ×24 进 0x124c40 实例表 → 0x6072b48 [0x12727C]=实例
    指针 → 0x6072b50 call request_irq(0x6065d86,RAM/IPI)→ 0x6072b64
    b[r1+3]=20 → 0x6072b66 ld r0=[实例+8] → 0x6072b68 bnez(目标
    0x6072b90,只跳过分配+首次 memset,**不跳过 CON0/FLT_ADR 两写**——
    codex 勘误) → 0x6072b6e call 分配 640B → 0x6072b86 call ROM memset
    (0x1fd80) → 0x6072b90 r0=[实例+16]=0xfe1500 → 0x6072b92
    [CON0]=r5=0 → 0x6072b9c 第二缓冲 memset → 0x6072bae [FLT_ADR]=
    [实例+8](分配缓冲指针)。boot 路径只写不读:两写都是整字 st.w、无
    RMW、无条件执行;值=真实指令结果(CON0=0、FLT_ADR=模型自有分配器
    自然产生的 RAM 指针)。

    裁决(codex 定调,gpt-6-astra):(c) write-only 记录恰两字——与 BM 路
    DAC DMA 四字/JLA 路 ADC DMA 五字先例完全同构(同 struct 相邻成员、
    同「当前推进零读、读属后域留严格停」);拒绝 (b) 全族八字读回容器的
    最充分理由=当前推进不需要任何读回,而读回+合成初值 0 会提前承诺
    尚未建立的消费语义(六个未触达字 0xfe1504..0xfe1518 零访问证据,
    不得凭 br28.h __RW 推成可读回;EQ→park 后 handler 域 0x6072bb2 调用
    族还会新写面,须按真实访问重审);拒绝 (a) 逐寄存器具名 fixture 的
    理由=两字是同一函数同一基本块形状的连续整字写,一个开关统一管理
    两字即可。__RW 是 SDK 布局定位,**不是**硅只写/无副作用证据,本容器
    也不宣称硅寄存器只写(codex 措辞)。

    覆盖边界:恰两字 {0xfe1500 CON0, 0xfe151c FLT_ADR};其余六字
    (CON1/CON2/CON3/DATAI_ADR/DATAO_ADR/DATA_LEN=0xfe1504..0xfe1518)
    与 0xfe1520 起(无已声明成员/属另一 SFR 组)零触达、保持 unmapped
    诚实停;只服务对齐 32 位整字覆盖写,子字/错位/跨界访问严格停;同址
    新读者/新写者/新 PC/新掩码/其他核须重审(地址白名单只拦新地址,
    运行时同址新写者由访问记录揭示,codex 措辞)。运行效果(均衡器处理/
    DMA/IRQ/滤波系数加载)一律不建模。
    """

    ADDRESSES=JL_EQ_INIT_WORDS
    SDK_NAMES={0xfe1500:'JL_EQ->CON0',0xfe151c:'JL_EQ->FLT_ADR'}

    def __init__(self):
        self.stores={address:{'values':[],'writes':0,'cores':[]} for address in self.ADDRESSES}
        self.word_writes=0
        self.read_attempts=0

    def handles(self,address):
        return address in self.stores

    def write(self,address,value,core):
        if address not in self.stores:raise ValueError('not a modeled JL_EQ init word')
        if not isinstance(value,int) or not 0<=value<=0xffffffff:raise ValueError('unsigned32 word expected')
        entry=self.stores[address];entry['values'].append(value);entry['writes']+=1
        entry['cores'].append(core);self.word_writes+=1

    def read(self,address):
        if address not in self.stores:raise ValueError('not a modeled JL_EQ init word')
        self.read_attempts+=1
        raise Blocked('jl_eq_init_read_not_modeled',address=hex(address))

    def report(self):
        return {'addresses':[hex(a) for a in self.ADDRESSES],
                'word_writes':self.word_writes,
                'read_attempts':self.read_attempts,
                'stores':{hex(a):{'values':[hex(v) for v in e['values'][-16:]],
                                  'writes':e['writes'],'cores':e['cores'][-16:]}
                          for a,e in sorted(self.stores.items()) if e['writes']},
                'sdk_naming':('br28.h JL_EQ = hs_base + map_adr(0x15, 0x00) = 0xfe1500 '
                              '(JL_EQ_TypeDef {__RW __u32 CON0..CON3, DATAI_ADR, DATAO_ADR, '
                              'DATA_LEN, FLT_ADR}); the two written words are CON0 at +0 and '
                              'FLT_ADR at +0x1c = 0xfe151c — the EQ init function 0x6072b24 '
                              'clears CON0 and stores the allocated filter buffer pointer into '
                              'FLT_ADR; the __RW labels locate the words, they are NOT silicon '
                              'WO evidence and NOT a claim that these words ignore reads on '
                              'silicon'),
                'whitelisted_words':('exactly the two EQ-init words {0xfe1500 CON0, 0xfe151c '
                                     'FLT_ADR} written by the EQ init function 0x6072b24; the '
                                     'other six declared members (CON1/CON2/CON3/DATAI_ADR/'
                                     'DATAO_ADR/DATA_LEN = 0xfe1504..0xfe1518) have zero '
                                     'identified boot-path access and stay unmapped, as does '
                                     '0xfe1520 onward (no declared member, next declared SRC '
                                     'group starts at 0xfe1600); only aligned 32-bit whole-'
                                     'word overwrite stores are served — sub-word, misaligned '
                                     'and cross-word accesses stay unmapped; a NEW consumer of '
                                     'either address (a new read PC, a new write PC, a new '
                                     'access shape, another core) must be re-audited before '
                                     'this container may serve it — the address whitelist only '
                                     'gates addresses, it does not stop a same-address new '
                                     'writer at runtime (codex wording)'),
                'observed_shapes':('two whole-word st.w stores in the EQ init function '
                                   '0x6072b24: 0x6072b92 [r0+0]=r5 (CON0=0, the stop word — '
                                   'r0=[instance+16]=0xfe1500, r5=0 from 0x6072b4e) and '
                                   '0x6072bae [r0+28]=r1 (FLT_ADR=[instance+8], the allocated '
                                   '640B filter-buffer pointer — r0 reloaded at 0x6072bac); '
                                   'both writes execute UNCONDITIONALLY — the 0x6072b68 bnez '
                                   'targets 0x6072b90 and skips only the allocation plus the '
                                   'first ROM memset (codex correction of the route-PARKPRE '
                                   'branch description); every value is a real instruction '
                                   'result, never invented; the FLT_ADR pointer is whatever the '
                                   'model allocator actually returned, NOT pre-claimed to be '
                                   '0x12xxxx'),
                'write_only':True,'readback_not_modeled':True,
                'consumption_face':('zero reads on this prefix (the boot path only writes CON0=0 '
                                    'and FLT_ADR=buffer-pointer); reads of these words would '
                                    'only arise in the EQ/audio handler domain (the 0x6072bb2 '
                                    'copex family or DSP filter streaming) beyond this frontier '
                                    '— "not read now" does NOT mean unreachable on all future '
                                    'paths (codex); such reads stay strictly stopped under the '
                                    'explicit kind and belong to a later EQ-consumption route'),
                'assumption':('write-only word records (values are real executed results, never '
                              'invented; the zero-read face on THIS prefix motivates the '
                              'record, it is NOT a measured hardware WO property claim and the '
                              'eight-member __RW layout is not evidence of readback semantics); '
                              'no EQ filtering, DMA, IRQ, coefficient loading or audio '
                              'processing is modeled — this releases the EQ initialization '
                              'configuration writes only, it does NOT verify any audio '
                              'processing or EQ function')}


class JLAdcStartConfigRegisters:
    """ADC 启动函数配置块四字容器(合成初始 0+读回=当前值,opt-in,路 ASS)。

    背景:21,800,455 停点 0x606e044——ADC 启动函数 [0x606df66,0x606e106)
    (采样率查表 0x609FBDC×11 → 配置 JL_AUDIO/JL_ASS 配置块)的
    `rmw.or.modimm [r0+0] |= 0x2` 内部读 0xfe0904=JL_ASS->MIC_CTL0 撞
    unmapped。停点地址 r0=r5+976 构造自 r5=0xfe0534(li @0x606e032,与
    DAC_COP 第二写点/邻窗同基址)——**间接构造地址的首个停点**,li 枚举
    法免疫(全 app 无 li 直接装载 0xfe0904);本路以基址+偏移传播解析法
    (~/tmp/ass-resolve.py:356 个 li 锚点装载 [0xfd0000,0xfe0a00) 常量,
    每锚点线性窗 500 条抽象解释跟踪全部 GPR 常量并做寄存器间传播,任何
    非恒等写杀值;[reg±imm] 基址已知即解析为绝对地址,含 b/h/d 子字与
    [rA+rB<<k] 变址形状)完成两窗(JL_ASS 0xfe0900..0xfe0910、JL_AUDIO
    尾 0xfe0550..0xfe0560)全 app 访问面扫描(存档 ~/tmp/ass-resolve.out)。

    静态访问面(两窗 46 个访问指令站点中归本容器的 29 个〔codex 口径:
    原子 RMW 一站点兼读写,ld 与 st 各计一站〕,其余为 AK/JLA/AS 既有
    容器字或零触达字;数据区 4 字节字面值扫描零独立命中——值域落在
    0xfe0500..0xfe0a00 的仅两处跨 call 指令编码的字节重叠,已人工排除,
    codex 独立逐 byte 复扫互证;「零独立字面值」不排除运行时算出后存入
    RAM 的指针来源,如实进完备性措辞):
      * 0xfe0904(MIC_CTL0):恰 2 个站点,全在本函数——0x606e044 |=0x2
        (停点,原子 RMW 内部读只进写回)、0x606e048 &=~0x1(同)。
      * 0xfe0908(MIC_CTL1):16 个站点(8 组 ld/st 对)两族——本函数
        0x606e050..0x606e07a 四组([r0+4] 源选择位段:前两组 ld→mask→
        insert(p:9/6,l:3)→st,后两组 ld→直接 insert(p:3/0,l:3)→st,
        值=param 字节);直接 li 0xFE0908 @0x606c8ca(函数
        [0x606c8aa,0x606c924),D2-b 蓝图 C2 链「MIC_CTL1 重 mux」,当前
        前缀不可达 NOT_RUN)四组 ld→mask→insert(p:15/21/12/18,l:3)→st。
        两族读值全部只进写回组装,分支只测 param 字节/配置输入;codex 另核
        家族 A 两个直接调用点 0x606da8e/0x606ec26——末次读值残留 r1 随即
        被 0x606c776(0) 路径在 0x606c804 清零,无残值消费。
      * 0xfe0554(MBG_CON):恰 5 条访问站点全在本函数——三条原子清位 RMW
        (0x606e0da/0de/0e2 &=~0x300/~0x40/~0x30)+ ld→&0xFFFFFFF0→|0x3→st
        (0x606e0e6..f0,保留高 28 位再置 bits0-1;门控 [sp]≠0=param 驱动,
        两支均零 SFR 读值分支消费)。
      * 0xfe055c(ADC_CON1):恰 6 个站点(3 组 ld/st 对)全在本函数——
        三组 ld→insert(p:0/4/8,l:4)→st。
      (本函数内 [r5+4]=0xfe0538 ADC_CON 的 insert/RMW 串含尾部 0x606e0f2/
      f6/fa |=0x20/0x10/0x100,全归 AS 容器已服务,不进本容器。)
      同窗其余:0xfe0900/0xfe0558 归 AK 容器(0xfe0900 共 13 站点:3 ld
        +1 st+9 RMW)(本容器不含,地址集互斥;
        解析另见 0xfe0900 的 11 条访问,含 3 处 ld.w 读者——0x606a70e
        就绪检查(读值进分支,`r0&0x380 != 0x380` 返布尔)、0x6069a38
        电源快照存 RAM、0x606c852 写回组装——到达时按 AK 读回语义服务,
        不属本路);0xfe0550 SRN 归 JLA 边界不收;0xfe090c/0xfe0910/
        0xfe0560 零触达不进白名单。
    完备性措辞(照 LPV/ADA 收紧):覆盖限本次扫描的主核 app 线性窗锚点
    法,不覆盖跨函数寄存器传递/RAM 指针装载(数据区字面值扫描零命中
    已证该来源为空)/ROM/q32s/其他访问主体;「未找到其他站点」≠「全
    系统唯一访问」。全部访问对齐 32 位字,窗内无子字/错位形状。

    分类(照 B3/DT/ADA/LPV 判据,读值只进写回组装零分支):AM 容器族——
    合成初始 0+读回=当前值,两项均为显式模型假设(synthetic-not-measured,
    非复位值非静态推导);RMW/insert 自身消费旧值(new=old|bit),潜在
    消费者含外设与 q32s,不能判死读(codex 钉子沿用)。开关分立
    jl_adc_start_config,依赖边挂 jl_audio_adc_dma_writes=当前条件场景
    推进顺序(JLA 放行后链才到达停点),非硬件依赖,模型类独立可用。
    known_limitation:容器存储的是合成配置字(非硅状态位),硅复位值/
    运行态语义未证;跨域限制照 LPV:只记录主核侧合成配置字更新,不模拟
    q32s 访问/ADC 捕获/跨域可见性/读写副作用——主核越过停点不表示真实
    MIC_CTL/MBG_CON/ADC_CON1 已生效或音频输入可用。
    """

    WORDS=JL_ADC_START_CONFIG_WORDS
    SDK_NAMES={0xfe0904:'JL_ASS->MIC_CTL0',0xfe0908:'JL_ASS->MIC_CTL1',
               0xfe0554:'JL_AUDIO->MBG_CON',0xfe055c:'JL_AUDIO->ADC_CON1'}
    SYNTHETIC_INITIAL_WORD=0

    def __init__(self,probe_initial=None):
        self.stores={address:{'values':[],'writes':0,'cores':[]} for address in self.WORDS}
        self.word_writes=0
        self.reads_after_write=0;self.synthesized_initial_reads=0
        # probe_only 敏感性探针(codex 建议,CV/ADA 先例):只替换 MIC_CTL0
        # (0xfe0904,停点字)的初值一次(后续读回真实写回值,非每读强制
        # 常数);其余三字不扰动保归因干净。零臂写回 0x2/ones 臂写回
        # 0xfffffffe——「只进写回」不等于初值无影响,未覆盖位会保留进
        # 写事务(codex 静态算术:四字在初值 0/0xffffffff 下写回终点分别
        # 为 0x2|0xfffffffe、0xff8|0xfffffff8、0|0xfffff000、0x3|0xfffffc03)。
        self.probe_initial=probe_initial

    def handles(self,address):
        return address in self.stores

    def _initial_word(self,address):
        if address==0xfe0904 and self.probe_initial is not None:
            return self.probe_initial
        return self.SYNTHETIC_INITIAL_WORD

    def write(self,address,value,core):
        if address not in self.stores:raise ValueError('not a modeled JL ADC-start config word')
        if not isinstance(value,int) or not 0<=value<=0xffffffff:raise ValueError('unsigned32 word expected')
        entry=self.stores[address];entry['values'].append(value);entry['writes']+=1
        entry['cores'].append(core);self.word_writes+=1

    def read(self,address):
        if address not in self.stores:raise ValueError('not a modeled JL ADC-start config word')
        entry=self.stores[address]
        if entry['writes']>0:
            self.reads_after_write+=1
            return entry['values'][-1]
        # 从未写过:显式合成初始字 0(AM 容器族先例;读值只进写回组装
        # 不进分支=初值不参与观察路径——但 RMW/insert 的未覆盖位会保留
        # 初值进写事务,单测 ones 探针互证(codex 钉子:仅测初值零会漏掉
        # RMW 读写不同容器状态的实现错误)。
        self.synthesized_initial_reads+=1
        return self._initial_word(address)

    def report(self):
        touched={hex(a):{'values':[hex(v) for v in e['values'][-16:]],
                         'writes':e['writes'],'cores':e['cores'][-16:]}
                 for a,e in sorted(self.stores.items()) if e['writes']}
        return {'addresses':[hex(a) for a in self.WORDS],
                'sdk_names':{hex(a):name for a,name in sorted(self.SDK_NAMES.items())},
                'word_writes':self.word_writes,
                'stores':touched,
                'readback_projection':{'reads':self.reads_after_write,
                                       'synthesized_initial_reads':self.synthesized_initial_reads,
                                       'readback':'last written value; a never-written word reads '
                                                  'back the explicit synthetic initial word 0 '
                                                  '(synthetic-not-measured, NOT a silicon reset '
                                                  'value and NOT statically derived)'},
                'modeled_vs_touched':('modeled exactly the four statically-verified ADC-start '
                                      'config words (0xfe0904 MIC_CTL0 / 0xfe0908 MIC_CTL1 in '
                                      'JL_ASS, 0xfe0554 MBG_CON / 0xfe055c ADC_CON1 in the '
                                      'JL_AUDIO tail); the zero-access words 0xfe090c/0xfe0910/'
                                      '0xfe0560 stay unmapped'),
                'whitelisted_words':('exactly the four words above; JL_ASS->CLK_CON 0xfe0900 and '
                                     'JL_AUDIO->AUD_CON 0xfe0558 stay with the route-AK container '
                                     'and the handler-tail SRN word 0xfe0550 stays unmapped '
                                     '(route-JLA boundary); only aligned 32-bit whole-word '
                                     'accesses are served — sub-word, misaligned and cross-word '
                                     'accesses stay unmapped; a NEW consumer of any of these '
                                     'addresses (a new read PC, a new write PC, a new RMW mask, '
                                     'another core) must be re-audited before this container may '
                                     'serve it — the address whitelist only gates addresses, it '
                                     'does not stop a same-address new consumer at runtime '
                                     '(codex wording)'),
                'observed_shapes':('the stop-shape family in the ADC start function 0x606df66: '
                                   '0x606e044 [r0+0]|=0x2 (the 21,800,455 stop itself, r0=r5+976 '
                                   'constructed from the 0xfe0534 base — the first stop point '
                                   'with an indirectly constructed address, immune to the li '
                                   'enumeration method), 0x606e048 [r0+0]&=~0x1, four '
                                   'ld→mask→insert(p:9/6/3/0,l:3)→st groups on [r0+4]=0xfe0908, '
                                   'three ld→insert(p:0/4/8,l:4)→st groups on 0xfe055c, three '
                                   'atomic bit-clear RMWs plus one ld→&0xF0→|0x3→st on 0xfe0554 '
                                   '(param-gated); plus the prefix-unreachable direct-li family '
                                   '0x606c8ca (D2-b C2-chain MIC_CTL1 re-mux, four ld→mask→'
                                   'insert(p:15/21/12/18,l:3)→st groups); every stored value is '
                                   'a real instruction result, never invented'),
                'access_face_completeness':('base+offset propagation scan (356 li anchor sites '
                                           'loading [0xfd0000,0xfe0a00) constants, per-anchor '
                                           '500-instruction linear-window abstract interpretation '
                                           'tracking ALL GPRs with register-to-register constant '
                                           'folding) + data-region 4-byte literal scan (zero '
                                           'independent literals reaching the windows); covers '
                                           'the main-core app linear windows only — cross-function '
                                           'register passing, ROM, q32s and other cores are NOT '
                                           'covered; "no other site found" does NOT mean "the '
                                           'only access in the system" (LPV/ADA wording)'),
                'model_assumptions':('synthetic initial word 0 and read-back=current-value are '
                                     'both explicit model assumptions (synthetic-not-measured, '
                                     'NOT silicon reset values, NOT statically derived); the '
                                     'RMW/insert instructions consume the old value themselves '
                                     '(new=old|bit / masked insert), so the reads are NOT dead '
                                     'even though no branch consumes them on the audited face — '
                                     'potential consumers include peripherals and q32s (codex '
                                     'nail, LPV precedent)'),
                'cross_domain_limitations':('the projection only records main-core-side synthetic '
                                            'config-word updates; it does NOT model q32s access, '
                                            'ADC capture, cross-domain visibility, handshakes or '
                                            'read/write side effects — the main core passing the '
                                            'stop does NOT mean the real MIC_CTL0/1, MBG_CON or '
                                            'ADC_CON1 are configured or that any audio input '
                                            'works (LPV codex wording)'),
                'sdk_naming':('JL_ASS = hs_base + map_adr(0x09, 0x00) = 0xfe0900 with the '
                              'all-__RW JL_ASS_TypeDef members CLK_CON/MIC_CTL0/MIC_CTL1 at '
                              '+0/+4/+8 (route-AK naming chain); JL_AUDIO tail members MBG_CON '
                              'at +0x54 = 0xfe0554 and ADC_CON1 at +0x5c = 0xfe055c follow the '
                              'route-AS container comment (br28.h all-__RW JL_AUDIO_TypeDef, '
                              'csfr.h __u16 -> 32-bit word slots; [SDK-inferred] naming level — '
                              'the __RW labels locate the words, they are NOT silicon read-back '
                              'evidence)'),
                'known_limitation':('the container stores synthetic config words, NOT silicon '
                                    'state; silicon reset values and runtime semantics of '
                                    'MIC_CTL0/1, MBG_CON and ADC_CON1 remain unverified'),
                'assumption':('config read-back container of the AM family (B3/DT/ADA/LPV/AK '
                              'precedents): reads return the last written value or the explicit '
                              'synthetic initial word 0; this does NOT verify any audio input, '
                              'ADC capture, clock enable or D2-b acceptance scenario')}


class P33AwkupRegisters:
    """P3_AWKUP 族 SDK 有名寄存器的「仅记录写访问」+ 单寄存器首次读具名 fixture(opt-in,路 BB)。

    证据(2026-11-13 路 BB 反汇编+运行态快照复核钉死):
    - SDK p33_sfr.h 把 P3_AWKUP_EN/P_IE/N_IE/LEVEL/INSEL/P_PND/N_PND/P_CPND/
      N_CPND=P33_ACCESS(0x60..0x68)、P3_AWKUP_FLT0..2=P33_ACCESS(0x80..0x82)
      全部命名;与整个 p33_sfr.h 一样只有地址宏、无 __RW/__RO/__WO 类型标注、
      无字段位定义——寄存器名与固件真实访问形状是全部证据。
    - app+ROM 全量引用扫描(字面量+基址算术双查)后本族的访问面:
      * walker 0x6067cb4 第二轮表 3 项(cfg=0xE2/0xE4/0xE5,运行态快照
        0x124A5C 钉死 w4/w8:0xE2→w4=0/w8=4,0xE4/0xE5→w4=2/w8=5)全部走
        0x6067b66 的 P3_AWKUP 次路径(0x6067c5a..cb2):FLT0..2 整字写
        `st.w.idxs [0xF2C200+ch<<2]=w8`(全字覆盖,旧值无关)、
        EN/P_CPND/N_CPND `rmw.or.bitr |=1<<ch`;w4==2 时 P_IE/N_IE
        `rmw.or.r |=1<<ch`(不经读);w4==1 时 N_IE |= 后 **P_IE 显式读回
        0x6067ca0 且消费**(写回 W=R&~(1<<ch):bit ch 强制 0,其余位携带读值);
        w4∉{1,2} 时 N_IE `rmw.and.invr &=~(1<<ch)` 后 **P_IE 显式读回
        0x6067cac 且消费**(写回 W=R|(1<<ch):bit ch 强制 1,其余位携带读值)。
        本 boot 前缀只有 item[0](cfg=0xE2,w4=0)走读回分支——P_IE 全前缀
        恰好一次读、且从未写过(读的是复位值);items[1]/[2] 走 w4==2 纯 RMW。
      * 独立清除助手 0x60685ae:P_CPND/N_CPND `rmw.or.modimm |=4`、EN
        `rmw.and.nmodimm &=0xFFFFFFFB`,纯 RMW 无显式读(不在本前缀)。
      * 独立分发函数 0x60691f4(`push.srl` 中断入口):0x60692a6/0x60692b4
        显式读 N_IE/P_IE 并按位循环派发(消费型读;AWKUP 中断从未交付,
        不在本前缀)。
      * LEVEL(0xf2c18c)/INSEL(0xf2c190):app+ROM 零引用,不建模。
      * P_PND/N_PND(0xf2c194/0xf2c198):已归复位历史投影(RESET_REASON_
        INPUTS,POR/no-wake 显式合成 0),本投影不重复建模。
    - 读回语义:SDK 无类型证据、无任何复位值证据——不为任何寄存器发明默认
      读值。每个寄存器单独经显式 fixture(p33_awkup_first_read_words,
      具名、版本管理、显式 opt-in)覆盖「首次读返回字」这一初始条件假设,
      未覆盖寄存器的读、写过之后的读、以及首次之后的重复读一律严格停
      (首次读不等于全时读回;不立「普通存储式读回」假设)。
    - 建模选择:写侧「仅记录写访问」(仿真器能力描述,不声明寄存器 WO 属性)
      ——FLT0..2/P_IE 整字写按字粒度记录(写值是真实执行结果,从不发明),
      EN/P_IE/N_IE/P_CPND/N_CPND 的 or/and 位 RMW 按 PartialWord 位记录
      (不经读回,与 mem_pwr_con/LRCT 位 RMW 同族);FLT 的 RMW 无观测形状
      拒绝;EN/N_IE/P_CPND/N_CPND 的整字写无观测形状拒绝。
    """
    EN,P_IE,N_IE=0xf2c180,0xf2c184,0xf2c188
    P_CPND,N_CPND=0xf2c19c,0xf2c1a0
    FLT0,FLT1,FLT2=0xf2c200,0xf2c204,0xf2c208
    # 整字写记录面:FLT0..2(walker st.w.idxs)+P_IE(0x6067cb0 消费型写回)。
    WORD_STORE_ADDRESSES=(FLT0,FLT1,FLT2,P_IE)
    # 位 RMW 记录面(全部有观测形状):EN/P_IE/N_IE/P_CPND/N_CPND。
    RMW_ADDRESSES=(EN,P_IE,N_IE,P_CPND,N_CPND)
    ADDRESSES=tuple(dict.fromkeys(WORD_STORE_ADDRESSES+RMW_ADDRESSES))
    # 复位历史投影持有的 pending 位地址(不双重建模,报告可追溯)。
    PENDING_IN_RESET_PROJECTION=(0xf2c194,0xf2c198)

    def __init__(self,first_read_words=None):
        self.first_read_words=dict(first_read_words or {})
        self.stores={address:{'values':[],'writes':0} for address in self.WORD_STORE_ADDRESSES}
        self.bits={address:PartialWord() for address in self.RMW_ADDRESSES}
        self.rmw_writes={address:0 for address in self.RMW_ADDRESSES}
        self.read_state={address:{'reads':0,'written':False,'fixture_reads':0}
                         for address in self.ADDRESSES}

    def handles(self,address):
        return address in self.read_state

    def write(self,address,value):
        """整字写记录:只接受有观测形状的 FLT0..2 与 P_IE,其余拒绝。"""
        if address not in self.stores:
            raise Blocked('awkup_word_store_not_modeled',address=hex(address),value=hex(value))
        if not isinstance(value,int) or not 0<=value<=0xffffffff:
            raise ValueError('unsigned32 word expected')
        entry=self.stores[address];entry['values'].append(value);entry['writes']+=1
        self.read_state[address]['written']=True

    def rmw(self,address,kind,operand):
        """位 RMW 记录(or 置位/and 清位,不经读回);FLT 的 RMW 无观测形状拒绝。"""
        if kind not in ('or','and'):raise ValueError("RMW kind must be 'or' or 'and'")
        if address not in self.bits:
            raise Blocked('awkup_rmw_not_modeled',address=hex(address),operation=kind)
        self.bits[address].apply(kind,operand&0xffffffff)
        self.rmw_writes[address]+=1
        self.read_state[address]['written']=True

    def read(self,address):
        state=self.read_state[address]
        if state['written']:
            raise Blocked('awkup_readback_after_write_not_modeled',address=hex(address))
        if state['reads']:
            raise Blocked('awkup_repeat_read_not_modeled',address=hex(address))
        if address not in self.first_read_words:
            raise Blocked('awkup_first_read_fixture_absent',address=hex(address))
        state['reads']+=1;state['fixture_reads']+=1
        return self.first_read_words[address]

    def report(self):
        stores={hex(a):{'values':[hex(v) for v in e['values'][-16:]],'writes':e['writes']}
                for a,e in sorted(self.stores.items()) if e['writes']}
        rmw={hex(a):{'known_mask':hex(self.bits[a].known),'known_value':hex(self.bits[a].value),
                     'writes':self.rmw_writes[a]}
             for a in self.RMW_ADDRESSES if self.rmw_writes[a]}
        fixture={hex(a):{'word':hex(w),'reads_served':self.read_state[a]['fixture_reads']}
                 for a,w in sorted(self.first_read_words.items())}
        return {'addresses':[hex(a) for a in self.ADDRESSES],
                'word_writes':sum(e['writes'] for e in self.stores.values()),
                'stores':stores,'rmw_records':rmw,
                'sdk_naming':('P3_AWKUP_EN/P_IE/N_IE/LEVEL/INSEL/P_PND/N_PND/P_CPND/N_CPND = '
                              'P33_ACCESS(0x60..0x68), P3_AWKUP_FLT0..2 = P33_ACCESS(0x80..0x82) '
                              'in p33_sfr.h (address macros only, no __RW/__RO/__WO, no bit fields)'),
                'not_modeled':('LEVEL 0xf2c18c / INSEL 0xf2c190 have zero app+ROM references; '
                               'P_PND 0xf2c194 / N_PND 0xf2c198 stay in the reset-history projection '
                               '(POR/no-wake explicit synthetic 0) and are not double-modeled here'),
                'first_read_fixture':fixture or None,
                'fixture_is_synthetic_assumption':True,
                'fixture_provenance':('synthetic zero word, NO direct reset-value evidence; named '
                                      'opt-in fixture (whole word 0 covers unknown bits and high bits; '
                                      'not a claim that reset leaves all IE bits off); per-register '
                                      'coverage only, never extended to N_IE or the rest of the family'),
                'first_read_semantics':('the fixture answers the FIRST read of a register only '
                                        '(never written, never read before); any repeat read and any '
                                        'post-write read strictly stops (awkup_repeat_read_not_modeled '
                                        '/ awkup_readback_after_write_not_modeled); registers without '
                                        'a fixture entry strictly stop (awkup_first_read_fixture_absent); '
                                        'no ordinary storage-style readback is assumed'),
                'writeback_dependency':('walker item[0] (cfg=0xE2, ch=2, w4=0) reads R=P3_AWKUP_P_IE at '
                                        '0x6067cac and writes back W=R|(1<<2) at 0x6067cb0: bit2 is forced '
                                        'to 1 by firmware, bits {0,1,3..31} (M&~K) carry the fixture word '
                                        'into the recorded P_IE value; the later firmware RMWs |=2/|=1 '
                                        '(items cfg=0xE4/0xE5) set bits1/0 — every recorded P_IE bit '
                                        'except {0,1,2} depends on the fixture assumption'),
                'records_write_accesses_only':True,
                'readback_not_modeled_except_first_read_fixture':True,
                'observed_shapes':('walker sub-path 0x6067c5a..0x6067cb2: st.w.idxs FLT0..2=w8 (full '
                                   'word, old value irrelevant); rmw.or.bitr EN/P_CPND/N_CPND|=1<<ch; '
                                   'w4==2: rmw.or.r P_IE/N_IE|=1<<ch (no read); w4==1: N_IE|= then explicit '
                                   'P_IE read 0x6067ca0, write-back R&~(1<<ch); w4 not in {1,2}: N_IE&=~ '
                                   '(rmw.and.invr) then explicit P_IE read 0x6067cac, write-back R|(1<<ch); '
                                   '0x60685ae helper: rmw.or.modimm P_CPND/N_CPND|=4, rmw.and.nmodimm '
                                   'EN&=0xFFFFFFFB (pure RMW, not in this prefix); IRQ dispatch 0x60691f4 '
                                   '(push.srl entry): explicit consumed N_IE/P_IE reads 0x60692a6/0x60692b4 '
                                   '(no AWKUP IRQ delivered in this prefix)'),
                'assumption':('write accesses are recorded only (a simulator capability, not a WO property '
                              'claim); no CPND clear mechanism is inferred from the register name; the '
                              'single first-read fixture word is an explicit synthetic assumption and the '
                              'recorded write-back value depends on it')}


class P3LsRegisters:
    """P3_LS_P11 + P3_LS_IO_DLY..PLL(0xf2c290/0xf2c340..354)的 write-only 整字记录(opt-in,路 BD)。

    证据(2026-11-13 路 BD 反汇编+全量引用扫描钉死;修正路 AZ/BB 条目里
    「[0xf2a638]=0 唯一写点 0x6067d62」的静态读法,见下):
    - SDK p33_sfr.h 把 P3_LS_P11 命名为 P33_ACCESS(0xa4)、P3_LS_IO_DLY/
      IO_ROM/ADC/AUDIO/RF/PLL 命名为 P33_ACCESS(0xd0..0xd5);与整个 p33_sfr.h
      一样只有地址宏、无 __RW/__RO/__WO 类型标注、无字段位定义——寄存器名与
      固件真实写形状是全部证据,不声称硅语义。
    - walker 清尾序列(app 0x6067d40..d68,反汇编钉死):
      0x6067d40 li.u32 r0=0xF2C290; 0x6067d46 li.u8 r1=0;
      0x6067d48 并行对 add.s14.rri.p r0+=176 ∥ st.w [r0+0]=r1 —— store 半按
      FM-1 run3「B 读 A 之前旧寄存器」方向携带旧 r0 → **[0xF2C290]=
      P3_LS_P11=0**(路 BB 已钉,304,012 停点即它);A 半后 r0=0xF2C340;
      随后 8 条独立 st.w(0x6067d4e..d5c,n=0..5,1,5 → +0..+0x14,+4,+0x14)
      写 [0xF2C340],[344],[348],[34C],[350],[354],[344],[354]=0(六地址
      SDK 有名 + 2 重复);
      0x6067d5e 并行对 add.s14.rri.p r0+=(-7448)(字段 n=8936 的 simm14 负值,
      8936-16384=-7448) ∥ st.w [r0+16]=r1 —— store 半按同一 run3 旧值方向
      携带旧 r0=0xF2C340 → **[0xF2C350]=0(P3_LS_PLL 第 3 次清零,非
      0xF2A638)**;A 半后 r0=0xF2A628,由下一条复合 RMW 消费。
    - **0xF2A638 修正**:路 AZ/BB 条目记「[0xf2a638]=0 整字写、全 app 唯一
      写点 0x6067d62」是顺序执行语义(用加法后的新 r0)的静态读法;按
      run3 硬件捕获方向(本仓库 exact_parallel_stores 已实现并在 304,012
      停点实际生效)该 store 半写 [0xF2C350]。全量复核(路 BD):0xF2A638
      的 app 字面量 0 处、ROM 整块 0 处、全部可达基址(0xF2A600/608/610/
      618/624/628)+offset 访问形状逐点反汇编后零命中——**0xF2A638 在整个
      app/ROM 里没有任何访问**,无需建模(SDK 侧它超出 P11_SYSTEM_TypeDef
      12 字结构体(offset 14),无名)。
    - P3_LS 族全量引用(app 字面量+基址算术、ROM 整块):三组写者、全部
      纯写、零读回——①walker 清尾(0x6067d40 基址 0xF2C290,写 0,本前缀);
      ②变体清零函数 0x6069cb0(基址字面量 0xF2C340:5 条 st.w 写 340..350
      =0 + 并行对 0x6069cc0/cc4 add.s14.rri.p r0=r1-7448 ∥ st.w [r1+20]=
      r0 → store 半旧 r1 → [0xF2C354]=0 + 复合 RMW [0xF2A628]&=0xFFFFFF7F,
      不在启动前缀);③低功耗路径 0x60c66f8(基址字面量 0xF2C340:6 条
      st.w =1、6 条 st.w =3,纯写)。ROM 零引用。没有任何 P3_LS 读回观测。
    - 建模选择:与 p33_port_sel(路 AZ)/lp_timer SYS_DIV/CLK_CON0(路 W)
      同族——全字覆盖写、旧值无关,按字粒度记录写值序列(写值是真实执行
      结果,从不发明);不为无人消费的读发明值,读回一律严格停
      (p3_ls_readback_not_modeled,显式 kind 优先于泛 unmapped);RMW 无
      任何观测形状,一律拒绝(p3_ls_rmw_not_modeled)。
    """
    P11=0xf2c290
    IO_DLY,IO_ROM,ADC,AUDIO,RF,PLL=0xf2c340,0xf2c344,0xf2c348,0xf2c34c,0xf2c350,0xf2c354
    ADDRESSES=(P11,IO_DLY,IO_ROM,ADC,AUDIO,RF,PLL)

    def __init__(self):
        self.stores={address:{'values':[],'writes':0} for address in self.ADDRESSES}
        self.word_writes=0

    def handles(self,address):
        return address in self.stores

    def write(self,address,value):
        if address not in self.stores:raise ValueError('not a modeled P3_LS register')
        if not isinstance(value,int) or not 0<=value<=0xffffffff:raise ValueError('unsigned32 word expected')
        entry=self.stores[address];entry['values'].append(value);entry['writes']+=1
        self.word_writes+=1

    def read(self,address):
        raise Blocked('p3_ls_readback_not_modeled',address=hex(address))

    def rmw(self,kind,operand):
        raise Blocked('p3_ls_rmw_not_modeled',address=hex(self.ADDRESSES[0]),operation=kind)

    def report(self):
        return {'addresses':[hex(a) for a in self.ADDRESSES],
                'word_writes':self.word_writes,
                'stores':{hex(a):{'values':[hex(v) for v in e['values'][-16:]],
                                  'writes':e['writes']}
                          for a,e in sorted(self.stores.items()) if e['writes']},
                'sdk_naming':('P3_LS_P11 = P33_ACCESS(0xa4), P3_LS_IO_DLY/IO_ROM/ADC/AUDIO/RF/PLL = '
                              'P33_ACCESS(0xd0..0xd5) in p33_sfr.h (address macros only, no '
                              '__RW/__RO/__WO, no bit fields)'),
                'write_only':True,'readback_not_modeled':True,
                'observed_shapes':('walker tail 0x6067d40..d68: li r0=0xF2C290, li r1=0, pair '
                                   '0x6067d48 add.s14.rri.p r0+=176 || st.w [old r0]=0 -> [0xF2C290], '
                                   'then eight st.w [0xF2C340..354]=0 (six SDK-named words, two '
                                   'repeats), pair 0x6067d5e add.s14.rri.p r0+=-7448 (simm14 of raw '
                                   '8936) || st.w [old r0+16]=0 -> [0xF2C350] (third PLL clear, NOT '
                                   '0xF2A638); variant 0x6069cb0: five st.w [0xF2C340..350]=0 + pair '
                                   '0x6069cc0||cc4 st.w [old r1+20]=0 -> [0xF2C354]; low-power '
                                   '0x60c66f8: six st.w [0xF2C340..354]=1 then six st.w =3; '
                                   'no readback anywhere (app+ROM)'),
                'assumption':('write-only word-sequence record (a simulator capability, not a WO '
                              'property claim); stored values are real executed results, never '
                              'invented; no level-shifter electrical effect modeled; 0xf2a638 has '
                              'zero app+ROM references (route AZ static reading corrected) and is '
                              'not modeled at all')}


class P33CanaryWatchRegisters:
    """金丝雀树/低功耗域 P3_VLVD_CON + P3_PCNT_CON 的「仅记录写访问」+ 首次读具名 fixture
    (opt-in,路 BV/sim-tailproj;BB 的 P3_AWKUP 先例同模式)。

    证据(2026-11-13 路 BV 指令锚定反汇编钉死;**修正路 BO 证据包 §N4 的地址
    归属**——BO 把 0x12cc56/0x12ced4/da 记成 P3_WKUP_CPND1/P3_ANA_CON1,实际
    基址是 0xf2c128/0xf2c124,BO 两侧各 +0x120 误记;tail-map 的原记录
    「0x12cc56 读 0xf2c044」反而成立,详见 docs/appcore-tail-sfr-semantics.md §8):
    - RAM 代码 flash 母本 0x60c6418(=运行态 0x12cc40,S11 条件任务创建失败分支)
      内 0x60c641a `li r0=0xf2c128`、0x60c6420 pair `r2=r0-0xE4` → **r2=0xf2c044**:
      0x60c6424 `ld.w r1=[r0]`(读 P3_PCNT_SET0 0xf2c128,已归复位配置掩码族)、
      r1&=0xFF7F、0x60c642e `ld.w r3=[r2]`(**读 P3_VLVD_CON,消费**:r3=r3<<5&0x80
      即 bit2→bit7 折进 PCNT_SET0 写值)、pair 尾 0x60c643c st.w [0xf2c128]=r1、
      0x60c643e `rmw.and.r [r2]&=0x7FB`(复合清位)。
    - RAM 代码 flash 母本 0x60c6690(=运行态 0x12ceb8,K1/K2 cleanup 邻域):
      0x60c6692 `li r1=0xf2c124`、0x60c6698 pair `r2=r1+0x40`=0xf2c164:
      st.w 0xFF → [0xf2c160]/[0xf2c164](P3_WKUP_CPND0/1,已归复位历史投影
      WAKE_CLEAR_TARGETS)与 [0xf2c19c]/[0xf2c1a0](P3_AWKUP P/N_CPND,路 BB
      面);0x60c66a2 pair `r0=r1-0xE0`=**0xf2c044**:0x60c66ac ld.w 读→&0x7FBF
      |0x40→st.w 写回(**显式 ld/st 配置 RMW,读值参与写回**);0x60c66b4 对
      **[r1]=0xf2c124=P3_PCNT_CON** 同形 ld/st RMW(&0x7FBF|0x40)。
    - 低功耗 DVFS RAM 母本:0x60c5952 `li r1=0xf2c044` + 0x60c5958
      `st.w.modimm [r1]=0`(VLVD 整字关断);0x60c6750 `li r8=0xf2c044` +
      0x60c6766 `rmw.and.modimm &=0x1FE`(同函数 [r8+0x14]=0xf2c058 P3_ANA_KEEP
      &=0xFF00 等,不在本投影面)。
    - P3_PCNT_CON 另一写者:AWKUP 中断分发函数 0x60691f4 内 0x6069232
      `li r1=0xf2c124` + 0x6069238 `rmw.or.modimm [r1]|=0x40`(纯复合置位,
      不经显式读;本前缀 AWKUP IRQ 从未交付,不在启动前缀)。
    - cleanup 0x60682a4 内 0x60682c4 **读回消费**(`li r1=0xf2c044; r1=[r1];
      r1<<3&8` 折进 [0xf2c0e0]=P3_SFLAG0 写值;另一源 [0xfd6014]=IOMAP_CON0
      >>14&4 已由 port 投影给已知 0——**修正 BO「[0xfd5f34]>>14&4」**:0x60682bc
      li r4=0xfd6000 → 0x60682c2 读 [r4+0x14]=0xfd6014,0xfd5f34 全 app/ROM
      零引用)。
    - SDK p33_sfr.h L30/L78 有名(P3_VLVD_CON=P33_ACCESS(0x11)、P3_PCNT_CON=
      P33_ACCESS(0x49)),纯地址宏、无 __RW/__RO/__WO 标注、无字段位定义。
    - 健康主路径不可达(S11 ret==0 条件 / K1/K2 金丝雀 / 低功耗 DVFS 域),
      维持 watch-only:投影只为「真到达时按形状处理」预置,不改变任何既有
      前沿(默认关)。
    - 读回语义:P3_VLVD_CON 的读是**真实消费**(bit0/bit2 折进其他寄存器写值),
      复位值无任何证据——按 BB 的 P3_AWKUP_P_IE 先例,每寄存器单独经显式
      fixture(p33_canary_first_read_words)覆盖「首次读返回字」,未覆盖/
      写过后/重复读一律严格停;P3_PCNT_CON 的 0x60c66b4 ld/st RMW 读值参与
      写回,同一首次读 fixture 语义服务。写侧「仅记录写访问」:整字写按值
      记录(st.w.modimm/st.w 写回形状),复合 RMW(or.modimm 置位/and.r/
      and.modimm 清位,全部有观测形状)按 PartialWord 位记录(不经读回)。
    """

    VLVD_CON=0xf2c044
    PCNT_CON=0xf2c124
    ADDRESSES=(VLVD_CON,PCNT_CON)

    def __init__(self,first_read_words=None):
        self.first_read_words=dict(first_read_words or {})
        self.stores={address:{'values':[],'writes':0} for address in self.ADDRESSES}
        self.bits={address:PartialWord() for address in self.ADDRESSES}
        self.rmw_writes={address:0 for address in self.ADDRESSES}
        self.read_state={address:{'reads':0,'written':False,'fixture_reads':0}
                         for address in self.ADDRESSES}

    def handles(self,address):
        return address in self.read_state

    def write(self,address,value):
        """整字写记录(DVFS st.w.modimm 关断 + 消费型写回 st.w 形状)。

        整字覆盖写使此前该地址的全部位记录失效(旧 PartialWord 的部分知识
        已被全字覆盖取代),位记录归零重新累计;stores 序列本身保留完整历史。
        """
        if address not in self.stores:
            raise Blocked('canary_word_store_not_modeled',address=hex(address),value=hex(value))
        if not isinstance(value,int) or not 0<=value<=0xffffffff:
            raise ValueError('unsigned32 word expected')
        entry=self.stores[address];entry['values'].append(value);entry['writes']+=1
        self.bits[address]=PartialWord()
        self.read_state[address]['written']=True

    def rmw(self,address,kind,operand):
        """复合 RMW 位记录(置/清位,不经读回;仅接受有观测形状的
        or.modimm / and.r / and.modimm 形式,由 probe_boot 分发;
        无 and.nmodimm 观测,不在白名单)。"""
        if kind not in ('or','and'):raise ValueError("RMW kind must be 'or' or 'and'")
        if address not in self.bits:
            raise Blocked('canary_rmw_not_modeled',address=hex(address),operation=kind)
        self.bits[address].apply(kind,operand&0xffffffff)
        self.rmw_writes[address]+=1
        self.read_state[address]['written']=True

    def read(self,address):
        state=self.read_state[address]
        if state['written']:
            raise Blocked('canary_readback_after_write_not_modeled',address=hex(address))
        if state['reads']:
            raise Blocked('canary_repeat_read_not_modeled',address=hex(address))
        if address not in self.first_read_words:
            raise Blocked('canary_first_read_fixture_absent',address=hex(address))
        state['reads']+=1;state['fixture_reads']+=1
        return self.first_read_words[address]

    def report(self):
        stores={hex(a):{'values':[hex(v) for v in e['values'][-16:]],'writes':e['writes']}
                for a,e in sorted(self.stores.items()) if e['writes']}
        rmw={hex(a):{'known_mask':hex(self.bits[a].known),'known_value':hex(self.bits[a].value),
                     'writes':self.rmw_writes[a]}
             for a in self.ADDRESSES if self.rmw_writes[a]}
        fixture={hex(a):{'word':hex(w),'reads_served':self.read_state[a]['fixture_reads']}
                 for a,w in sorted(self.first_read_words.items())}
        return {'addresses':[hex(a) for a in self.ADDRESSES],
                'word_writes':sum(e['writes'] for e in self.stores.values()),
                'stores':stores,'rmw_records':rmw,
                'sdk_naming':('P3_VLVD_CON = P33_ACCESS(0x11) (p33_sfr.h L30), P3_PCNT_CON = '
                              'P33_ACCESS(0x49) (p33_sfr.h L78); address macros only, no '
                              '__RW/__RO/__WO, no bit fields'),
                'route_bo_correction':('BO §N4 recorded 0x12cc56/0x12ced4/da against '
                                       'P3_WKUP_CPND1/P3_ANA_CON1 by misreading the base '
                                       'registers as 0xf2c248/0xf2c244; the actual li '
                                       'literals are 0xf2c128 (P3_PCNT_SET0) and 0xf2c124 '
                                       '(P3_PCNT_CON), and the folded bit source is '
                                       'P3_VLVD_CON 0xf2c044 — the tail-map original '
                                       'reading stands (see docs/appcore-tail-sfr-semantics.md §8)'),
                'first_read_fixture':fixture or None,
                'fixture_is_synthetic_assumption':True,
                'fixture_provenance':('named opt-in fixture per register; NO reset-value '
                                      'evidence (the canary tree is unreachable on the healthy '
                                      'path, so no runtime observation exists); first read only, '
                                      'never extended to repeat/post-write reads'),
                'first_read_semantics':('the fixture answers the FIRST read of a register only '
                                        '(never written, never read before); any repeat read and '
                                        'any post-write read strictly stops; registers without a '
                                        'fixture entry strictly stop; no ordinary storage-style '
                                        'readback is assumed'),
                'records_write_accesses_only':True,
                'readback_not_modeled_except_first_read_fixture':True,
                'observed_shapes':('0x60c6418 (=runtime 0x12cc40): ld.w [0xf2c044] at 0x60c642e '
                                   'consumed (bit2<<5&0x80 folded into the P3_PCNT_SET0 write '
                                   'value), rmw.and.r [0xf2c044]&=0x7FB at 0x60c643e; '
                                   '0x60c6690 (=runtime 0x12ceb8): ld/st RMW &0x7FBF|0x40 on '
                                   '0xf2c044 (0x60c66ac..b2) and 0xf2c124 (0x60c66b4..ba), '
                                   'st.w 0xFF wake clears to 0xf2c160/0xf2c164 (reset-history '
                                   'projection) and 0xf2c19c/0xf2c1a0 (P3_AWKUP projection); '
                                   'DVFS 0x60c5958 st.w.modimm [0xf2c044]=0 and 0x60c6766 '
                                   'rmw.and.modimm [0xf2c044]&=0x1FE; AWKUP dispatch 0x6069238 '
                                   'rmw.or.modimm [0xf2c124]|=0x40; cleanup 0x60682c4 consumes '
                                   'ld.w [0xf2c044] bit0<<3&8 into the P3_SFLAG0 write value'),
                'assumption':('write accesses are recorded only (a simulator capability, not a '
                              'WO property claim); the canary tree stays watch-only — healthy-path '
                              'unreachability is the standing judgement (tail-map §4), and reaching '
                              '0x601249c/0x60124ae remains a wrong-direction signal')}


class Fd31AdcConRegisters:
    """0xfd3100 = JL_ADC->CON 的 write-only 整字记录(opt-in,路 BG/sim-pwrdomain)。

    证据(2026-11-13 路 BG 反汇编+全量引用扫描钉死;修正路 BD 两条记录):
    - **命名修正(路 BD 记「JL PMU/SFR 段电源口、SDK 头无名」)**:0xfd3100 在
      参考 SDK br28.h(与路 AC JL_LRCT=ls_base+map_adr(0x36,0) 同一头文件、
      同一命名锚点家族)里是 **JL_ADC_BASE = ls_base(0xfd0000)+map_adr(0x31,0)**,
      `JL_ADC_TypeDef { __RW __u32 CON; __RO __u32 RES; }`——+0=CON(可读写)、
      +4=RES(只读转换结果)。结构体无任何位字段定义;名字是 SDK 参考命名锚点,
      不声称硅语义。HANDOFF「电源小函数 0x60115b4」实为 ADC 控制函数。
    - **首写值修正(路 BD 记「[0xfd3100]=0x200」)**:0x60115ba `st.w.modimm
      {m:0xB00}` 按 modimm 展开(codec 旋转立即数)是 **0x20000**;304,036 停点
      报告 value_bytes '00000200' 小端即 0x00020000。三连写序列=
      **0x20000 → 0x20040 → 0x40**。
    - 电源/控制函数 0x60115b4..0x60115ca 全函数:`li r0=0xfd3100;
      st.w.modimm [r0]=0x20000; li r1=0x20040; st.w [r0]=r1;
      st.w.modimm [r0]=0x40; rts`——纯三条整字写,零读回、零 RMW、零轮询。
      全 app 恰好 5 个 call 点:0x60120f4/0x6012106(app_core 尾段,两次背靠背,
      中间夹 ROM 0x1fd80→0x4226 的 176 字节缓冲 memset,纯 RAM)、0x60116ee/
      0x6011706(caller A 内)、0x6034900(caller C 内)。
    - 全量引用面(app 字面量恰好 7 处 li.u32 0xfd3100、ROM 整块 0 处、
      0xfd3104 字面量 0 处):①0x60115b6 电源函数(纯写);②0x6011642 轮询
      helper 0x601163a(csync 后 ≤1000 次循环读 CON,退出条件=byte0 bit7 置位
      (ext.b0.s<0)或 bit4 清零,轮询间 call ROM 0x12e256 延时——**真实读回+
      忙等消费**);③0x60116c4 setter 0x6011666 尾(各分支合并:整字写计算值
      r4 + `rmw.or.modimm |=0x40` 置 bit6);④0x60116e2 caller A 0x60116d0
      (ADC 采样取值:写通道槽 [0x127DAC](=0x1265C0+1531*4) 后 **读 CON 测
      bit4 门控**、bit4 置位才调轮询 helper+电源函数,**读 [r4+4]=0xfd3104
      RES** 把结果半字存进槽+4(st.h n=2、scale 2),经 ROM 0x12b5b0 post
      后 ld.hu 取回同址槽+4 作返回值);⑤
      0x6034908 caller C 0x60348e8(push.srl m=41 全帧:槽==0x3FF → 读 CON 测
      bit7 门控,否则调电源函数);⑥0x60349E8 caller D(槽==0x3FF → 读 CON、
      bit4 置位退出、byte0 消费(==10 检查));⑦0x6068084 power_init 尾段区域
      单写 CON=0x40(不在本前缀)。
    - **读回消费就在启动链上**:304,036 放行后的 app_core 尾段=两次电源函数
      (6 条纯写)→ 0x60115cc+3 组 getter(0x6001230/0x601160e,纯软件表)→
      **32 次采样循环 0x6012148..0x601215e**(每轮 call caller A,收集
      min/max/sum,末尾 (sum-min-max)/30 的 ADC 平均滤波)——caller A 首个
      fd31 访问就是 0x60116e6 `ld.w [r4]` 读 CON。
    - 建模选择:CON 写侧按 p33_port_sel/P3_LS/lp_timer SYS_DIV 同族 write-only
      整字记录(全字覆盖写、旧值无关、值全真实指令计算);**CON 读回(bit4
      门控/bit7 忙等)与 RES(模拟转换结果)是真实读回消费且无可推导值——
      RES 是模拟测量、CON 状态位无 SDK 位定义,不发明 fixture**,读回一律
      严格停(fd31_adc_con_readback_not_modeled,显式 kind 优先于泛 unmapped);
      RMW(setter 的 |=0x40,在前沿之后)拒绝(fd31_adc_con_rmw_not_modeled,
      p3_ls 先例:该寄存器有真实读回消费者,位记录也不能声称合并语义)。
      RES(0xfd3104)固件零写、投影不覆盖——读保持泛 unmapped 严格停。
    - **读回投影(路 BN/sim-adc-read,opt-in con_readback_projection,需
      fd31_adc_con_projection,默认关=304,317 前沿形状不变)**:CON 读回=最后
      写值(lp_timer CON 路 AL 同族)。SDK 证据:参考 br28.h 的
      JL_ADC_TypeDef{__RW CON; __RO RES;}——该头的 __RW/__WO/__RO 标注有区分度
      (JL_LRCT CON __WO vs NUM __RW、ADC CON __RW vs RES __RO),CON 可回读是
      SDK 参考语义;读回=最后写值与 SFC 时钟 +8/lp_timer CON 同族,只在固件
      写过至少一次后返回。消费者分支全图(反汇编钉死):
      - **caller A 0x60116d0**(32 次采样循环 0x6012148 每轮以 r0=0x10000 调入,
        循环计数 r6=32→0、min r4/max r5/sum r7、末尾 (sum-min-max)/30 平均):
        [0x127DAC]=通道字 → 读 CON → `(r0&16)==0` 即 **bit4==0 → goto 0x60116f2
        跳过 helper+控制函数**;bit4==1 → call 0x601163a(helper)+0x60115b4(控制
        函数,重写 CON 三连)→ 0x60116f2 call RAM veneer 0x12b55c(VM init 前缀
        已真实走过的 OS post 链)→ 读槽 → call setter 0x6011666(通道字 0x10000:
        r2'=r0>>16=1,tbb[1]→0x6011698 case1:r0=r0.l(u)=0 后 **call 0x6067d8a**
        ——对 **0xf2c250=P3_ANA_CON4**(p33_sfr.h:134 有名,P33_ACCESS(0x94),
        p33_sfr.h 纯地址宏无类型标注)做 6 次读-改-写(读&0x1FE1 写回、置 bit5/
        bit6/bit7、bit0=1,显式 ld.w 读+st.w 写,与路 AM P3_ANA_CON2/CON5 的
        消费形状完全同族)→ 汇合尾部 **[0xfd3100]=r4(整字写)+
        [0xfd3100]|=0x40(复合 RMW)**,r4=(表字节 b[0x124A90]&~7)|0x1058|0x420000
        → call helper 0x601163a(轮询)→ **读 [r4+4]=0xfd3104 RES**(r2,唯一
        读点)→ call 控制函数 → h[槽+4]=r2 半字存结果 → [槽]=0xFFFFFFFF →
        call 0x12b5b0(veneer)→ ld.hu 槽+4 作返回值。caller A 的 bit4 门控在
        投影下的走向:末写值 0x40 的 bit4=0 → 第一轮就跳过 helper+控制函数,
        直接 veneer+setter。
      - **轮询 helper 0x601163a**(do-while,r4=-1000 起):csync → 读 CON →
        r0.b0 符号扩展 `ifs (r0<0)` 即 **bit7 置位 → 直接返回**(完成);再读
        CON → `(r0&16)==0` 即 **bit4 清零 → 直接返回**(放弃);否则 call RAM
        延时 0x12e256、r4+=1、`r4!=0` 回卷——最多 1000 轮。投影世界里 setter
        写值的 bit7 取决于表字节 b[0x124A90] 的 bit7、bit4=1(0x1058 bit4)——
        helper 走满 1000 轮超时退出或按写值位直接退出,**投影不发明 ADC 完成
        事件**(真实硬件转换完成会置 bit7 提前退出;完成时序不建模,报告
        status_bits_not_modeled 全文标注)。
      - setter 尾部复合 RMW |=0x40 走 probe_boot 通用 mmio_rmw_always_writes
        路径(读=投影最后写值→合成→写=记录),与 lp_timer CON2 复合 RMW
        (0x606767e,路 AL)同一执行语义。
      - **RES(0xfd3104)=新显式停点 kind fd31_adc_res_not_modeled**(优先于泛
        unmapped 便于诊断):RES 是模拟测量值、无可推导来源(红线:绝不发明
        0/满值 fixture);真机 FD07 测量(路 BA 已评估通道可读)或带推导链的
        具名 fixture 是唯一继续途径。
      - **0xf2c250(P3_ANA_CON4)的放行**:挂 fd31_adc_con_readback 开关联动
        seed 进 additional_configuration(合成初始字 0,路 AM p33_analog_config
        同款语义:写后读回=后写值、写前读回=0)——不并进路 AM 的
        P33_ANALOG_CONFIG 常量(那会改变「p33_analog_config 开而本开关关」
        世界的报告形状);开关关时 0xf2c250 保持 unmapped(304,317 形状)。
    """
    CON=0xfd3100
    RES=0xfd3104
    # P3_ANA_CON4(p33_sfr.h:134 有名,P33_ACCESS(0x94)):ADC setter case1 的
    # 0x6067d8a 读改写目标,fd31_adc_con_readback 开关联动 seed(见类注释)。
    ANA_CON4=0xf2c250
    ADDRESSES=(CON,)

    def __init__(self,*,con_readback_projection=False,pnd_completion=False,res_named_values=None):
        self.stores={address:{'values':[],'writes':0} for address in self.ADDRESSES}
        self.word_writes=0
        # pnd_completion(opt-in,需 con_readback_projection,路 BY/sim-adcresfix):
        # CON(bit7=PND,SDK gpadc_hw_v10.h GPADC_CON_PND=7)的**无状态读时完成
        # 投影**——最后写值 EN(bit4)=1 时,guest ld 读回=最后写值|0x80;EN=0
        # 不强制(adc_close 的 0x40 写已 CPND 清+关使能)。证据链:SDK 驱动
        # gpadc_hw_v10.c 的 adc_wait_pnd 轮询 CON 低字节符号(bit7 置位→读 RES,
        # ≤1000 次 udelay(1) 超时)、adc_get_res 把 RES 有效性钉在 bit7 上;QCY
        # helper 0x601163a 同形(1000 轮 × RAM 延时 ~386 步)。**假设(显式、非
        # 时序模型)**:启动写之后固件的第一次状态读之前转换已完成——SDK 自己
        # 的超时预算=1ms(µs 级转换),而 QCY 全预算轮询=80 次×~96ms≈7.7s 纯
        # 轮询(1 步=250ns 按 Timer0 10ms=40,000 步换算),与实测 ~1s 级启动
        # 不相容→真机必然 PND 提前退出。**原子复合 RMW(rmw.or/and.* 指令形状)
        # 的内部读不观察投影 bit**(有边界的抽象:合成从裸最后写值出发,只钉死
        # 到这些指令形状;显式 guest ld→or→st 序列里的 ld 走正常读语义)。
        # 先例:lrct_trim_done(bit7 强制 1)/SFC PLL-lock(bit15)同模式。
        self.pnd_completion=pnd_completion;self.pnd_forced_reads=0
        # res_named_values(opt-in dict{通道字:常量},需 pnd_completion):RES 的
        # 具名档位 fixture(derived-not-measured,推导链见 report 子键与
        # docs/adc-res-consumer.md §6)。通道识别=RES 读时刻 P3_ANA_CON4(0xf2c250)
        # 的 [4:1] 模拟复用域(setter case1 → 0x6067d8a 把 (通道字&0xFFFF)<<1
        # 写进该域,反汇编钉死),通道字=0x10000|域值;另要求最后 CON 写 EN=1
        # (有已启动的转换)且 CH_SEL(bits8-11)=0(PMU 模拟路径)——RES 只有在
        # 完成后才持有有效样本(SDK adc_get_res 同语义),故 fixture 叠在
        # pnd_completion 之上。未覆盖通道/未知域显式严格停
        # fd31_adc_res_channel_not_modeled;RES 写仍拒绝。
        if res_named_values is not None:
            if not pnd_completion:
                raise ValueError('the fd31 ADC RES named values require the PND completion projection')
            if (not isinstance(res_named_values,dict) or not res_named_values or
                    any(not isinstance(ch,int) or not 0<=ch<=0xffffffff
                        or not isinstance(v,int) or not 0<=v<=1023
                        for ch,v in res_named_values.items())):
                raise ValueError('RES named values must map channel words to 10-bit codes')
        self.res_named_values=dict(res_named_values) if res_named_values else None
        self.res_served={}
        # con_readback_projection(opt-in,需 fd31_adc_con_projection,路 BN/sim-adc-read):
        # CON 读回=最后写值投影。SDK 证据与 lp_timer CON0/1/2(路 AL,p11_sfr.h
        # __RW)同族——参考 br28.h 把 JL_ADC_TypeDef 标成 {__RW CON; __RO RES;},
        # 且该头的 __RW/__WO/__RO 是有区分度的标注(JL_LRCT CON __WO vs NUM __RW、
        # ADC CON __RW vs RES __RO),即 SDK 作者认为 CON 是可回读的配置字。读回
        # =最后写值与 SFC 时钟 +8(0xfd1c08)/lp_timer CON(0xf2a908,路 AL)同族,
        # 只在固件已写过至少一次后返回、从不臆造复位值。
        # 消费者分支(反汇编钉死,见类注释):caller A 0x60116d0 的 0x60116e8
        # `(r0 & 16)==0` 用投影值判 bit4 门控——末写值 0x40 的 bit4=0 → 跳过
        # helper+控制函数;轮询 helper 0x601163a 的 bit7 置位退出在投影世界里
        # 永不触发(setter 写值 bit7=0),固件轮询 1000 轮超时退出是投影的自然
        # 后果——**投影不发明 ADC 完成事件**(真实硬件上转换完成会置 bit7,轮询
        # 提前退出;完成时序不建模,报告显式标注)。RES(__RO、模拟测量值)读回
        # 与 CON 状态位转换语义一样无可推导来源,保持严格停
        # fd31_adc_res_not_modeled,绝不发明 0/满值 fixture。
        self.con_readback_projection=con_readback_projection;self.con_reads=0

    def handles(self,address):
        return address in self.stores

    def handles_res(self,address):
        # RES 只路由读(显式 kind 优先于泛 unmapped,便于诊断);写不经 fd31
        # 投影(handles() 不含 RES),整字写保持泛 unmapped_or_readonly_write。
        return address==self.RES

    def write(self,address,value):
        if address not in self.stores:raise ValueError('not a modeled fd31 ADC CON register')
        if not isinstance(value,int) or not 0<=value<=0xffffffff:raise ValueError('unsigned32 word expected')
        entry=self.stores[address];entry['values'].append(value);entry['writes']+=1
        self.word_writes+=1

    def read(self,address,*,ana_con4=None):
        if address==self.RES:
            # RES(0xfd3104,SDK __RO)=ADC 转换结果,模拟测量值;固件侧唯一读点=
            # caller A 0x6011704 `ld.w [r4+4]`。默认(无 fixture)严格停
            # fd31_adc_res_not_modeled(红线:绝不发明 0/满值);真机测量通道
            # (FD07,路 BA)或带推导链的具名 fixture 是唯一继续途径。
            if self.res_named_values is not None:
                # 具名档位 fixture:按 P3_ANA_CON4[4:1] 复用域识别当前转换通道
                # (识别链有推导,反汇编钉死);前置一致性检查=最后 CON 写 EN=1
                # (已启动的转换,SDK adc_get_res 把 RES 有效性钉在 PND 上)且
                # CH_SEL(bits8-11)=0(PMU 模拟路径,非数字通道)——不满足或
                # 通道不在 fixture 表,显式严格停,不猜。
                if self.stores[self.CON]['writes']==0:
                    raise Blocked('fd31_adc_res_channel_not_modeled',address=hex(address),
                                  reason='no CON write observed')
                con_last=self.stores[self.CON]['values'][-1]
                if not con_last&0x10:
                    raise Blocked('fd31_adc_res_channel_not_modeled',address=hex(address),
                                  reason='last CON write has EN(bit4)=0: no started conversion')
                if (con_last>>8)&0xF:
                    raise Blocked('fd31_adc_res_channel_not_modeled',address=hex(address),
                                  reason='CON CH_SEL(bits8-11) nonzero: not the PMU analog path')
                if not isinstance(ana_con4,int):
                    raise Blocked('fd31_adc_res_channel_not_modeled',address=hex(address),
                                  reason='P3_ANA_CON4 analog mux word unknown')
                mux=(ana_con4>>1)&0xF;channel=0x10000|mux
                if channel not in self.res_named_values:
                    raise Blocked('fd31_adc_res_channel_not_modeled',address=hex(address),
                                  mux_field=hex(mux),channel_word=hex(channel),
                                  reason='channel not covered by the named fixture')
                self.res_served[channel]=self.res_served.get(channel,0)+1
                return self.res_named_values[channel]
            raise Blocked('fd31_adc_res_not_modeled',address=hex(address))
        if self.con_readback_projection and self.stores[self.CON]['writes']>0:
            self.con_reads+=1
            value=self.stores[self.CON]['values'][-1]
            if self.pnd_completion and value&0x10:
                # PND 完成投影(无状态、读时):EN=1 的最后写值上强制 bit7——
                # 完成时序不建模,假设=启动写后固件首次状态读前转换已完成
                # (证据链与原子 RMW 的边界抽象见 __init__ 注释)。
                value|=0x80;self.pnd_forced_reads+=1
            return value
        raise Blocked('fd31_adc_con_readback_not_modeled',address=hex(address))

    def rmw(self,kind,operand,*,count_read=False):
        if self.con_readback_projection and kind in ('or','and'):
            # 复合 RMW(rmw.or.modimm [0xfd3100]|=0x40,setter 0x60116ca)=内部
            # 读(投影=最后写值)+按位合成+写回记录,与 lp_timer CON2 复合 RMW
            # (0x606767e,路 AL)同一执行语义;实际执行路径:pnd_completion
            # 关闭时走 probe_boot 的通用 mmio_rmw_always_writes(mem.read→
            # mem.write,行为与 legacy 逐字节一致),开启时走 probe_boot 的
            # fd31 专用分发(内部读从裸最后写值合成——不观察投影 bit7,边界
            # 抽象只钉死到 rmw.* 指令形状)。count_read=True 由专用分发传入,
            # 保持与通用路径(mem.read 顺带计一次 con_reads)相同的读计数
            # 口径;模型 API/单测默认 False 不改既有计数。形状外拒绝。
            if self.stores[self.CON]['writes']==0:
                raise Blocked('fd31_adc_con_readback_not_modeled',address=hex(self.CON),operation=kind)
            if count_read:self.con_reads+=1
            operand&=0xffffffff
            value=self.stores[self.CON]['values'][-1]
            value=(value|operand) if kind=='or' else (value&operand)
            self.stores[self.CON]['values'].append(value);self.stores[self.CON]['writes']+=1
            self.word_writes+=1
            return
        raise Blocked('fd31_adc_con_rmw_not_modeled',address=hex(self.ADDRESSES[0]),operation=kind)

    def report(self):
        result={'addresses':[hex(a) for a in self.ADDRESSES],
                'word_writes':self.word_writes,
                'stores':{hex(a):{'values':[hex(v) for v in e['values'][-16:]],
                                  'writes':e['writes']}
                          for a,e in sorted(self.stores.items()) if e['writes']},
                'sdk_naming':('0xfd3100 = JL_ADC->CON (__RW), 0xfd3104 = JL_ADC->RES (__RO) in the '
                              'reference br28.h (ls_base+map_adr(0x31,0); same header family as the '
                              'JL_LRCT naming anchor; struct has NO bit-field definitions; naming '
                              'anchor only, not a silicon claim; route BD\'s power/PMU reading corrected)'),
                'write_only':not self.con_readback_projection,
                'readback_not_modeled':not self.con_readback_projection,
                'observed_shapes':('control function 0x60115b4: li r0=0xfd3100, st.w.modimm '
                                   '[r0]=0x20000 (modimm raw 0xB00 expands to 0x20000, NOT 0x200 '
                                   'as route BD recorded), li r1=0x20040, st.w [r0]=r1, '
                                   'st.w.modimm [r0]=0x40, rts; called back-to-back from the '
                                   'app-core tail 0x60120f4/0x6012106; readback consumers: poll '
                                   'helper 0x601163a (<=1000 rounds on CON bit7-set/bit4-clear), '
                                   'caller 0x60116d0 (CON bit4 gate + RES read into a software '
                                   'slot, 32-round sampling loop 0x6012148), callers 0x60348e8/'
                                   '0x60349xx (CON bit7/bit4 gates), setter 0x6011666 tail '
                                   '(whole-word store + rmw.or |=0x40, past the frontier); '
                                   'RES 0xfd3104 is never written by the firmware'),
                'assumption':('write-only word-sequence record of CON (a simulator capability, '
                              'not a WO property claim); stored values are real executed results, '
                              'never invented; no ADC conversion, analog input or status-bit '
                              'semantics modeled; CON readback (bit4 gate / bit7 busy poll) and '
                              'RES (an analog measurement) have no derivable values, so reads '
                              'stop honestly and no fixture is invented' +
                              ('' if not self.con_readback_projection else
                               '; with the opt-in readback projection CON reads return the last '
                               'firmware-written value — see con_readback_projection'))}
        if self.con_readback_projection:
            result['con_readback_projection']={
                'address':hex(self.CON),'reads':self.con_reads,
                'readback_semantics':('last firmware-written value; the reference br28.h marks '
                    'JL_ADC_TypeDef CON __RW (a meaningful annotation in this header: JL_LRCT '
                    'CON is __WO while its NUM is __RW, and ADC RES is __RO), so a CON read-back '
                    'is defined and returning the last write matches the SFC clock +8 '
                    '(0xfd1c08) and lp_timer CON (route AL, 0xf2a908) precedents — only after '
                    'at least one firmware write, never a fabricated reset value'),
                'status_bits_not_modeled':('the projection NEVER invents ADC completion: bit7 '
                    '(conversion-done, poll helper 0x601163a exits on it) and bit4 (gate at '
                    '0x60116e8) read back whatever the firmware last WROTE, so in the projected '
                    'world the poll helper runs its full <=1000-round budget and exits on the '
                    'counter (r4 from -1000 to 0) instead of an early completion exit; on real '
                    'hardware the ADC would set bit7 and the poll would exit early — the '
                    'completion TIMING and status-bit transition semantics are NOT modeled, '
                    'every step in this projection is conditional pass / derived-not-measured'
                    + ('' if not self.pnd_completion else
                       '; NOTE the separate opt-in pnd_completion_projection (this run) DOES '
                       'project bit7 set on reads — see its own sub-key')),
                'res_boundary':{'address':hex(self.RES),'sdk_name':'JL_ADC.RES (__RO)',
                    'sole_firmware_access':'0x6011704 ld.w [r4+4] (caller A, n-field scale 4)',
                    'value_evidence':('none — RES is the live analog conversion result; reads '
                        'stay strictly blocked (fd31_adc_res_not_modeled) and no 0/full-scale '
                        'fixture is ever invented; a live-device FD07 measurement session '
                        '(route BA evaluated the channel readable) or a named fixture with a '
                        'real derivation chain are the only ways on')}
            }
        if self.pnd_completion:
            # 路 BY:PND 完成投影子键(叠在 con_readback 之上)。无状态读时投影,
            # 非时序模型;原子 RMW 边界抽象只钉死到 rmw.* 指令形状。
            result['pnd_completion_projection']={
                'bit':'0x80','gate':'only when the last firmware CON write has EN (bit4) set',
                'forced_reads':self.pnd_forced_reads,
                'semantics':('stateless read-time projection: a guest load of CON observes '
                    'bit7 (GPADC_CON_PND, gpadc_hw_v10.h) set once a conversion is started; '
                    'the completion TIMING is not modeled — the explicit assumption is that '
                    'the conversion completes before the firmware FIRST status read after the '
                    'start write (SDK adc_wait_pnd polls bit7 with a 1000x udelay(1) timeout '
                    'budget; the QCY helper 0x601163a is the same shape with a ~386-step RAM '
                    'delay, and a full-budget poll per caller round would put ~96ms x 80 '
                    'rounds of pure polling on the boot path, incompatible with the observed '
                    '~1s-class boot); lrct_trim_done (bit7) and the SFC PLL-lock (bit15) '
                    'precedents'),
                'compound_rmw_boundary':('the atomic compound RMW shapes (rmw.or/and.* on CON) '
                    'synthesize from the RAW last-written word — their internal read does NOT '
                    'observe the projected bit (a bounded abstraction pinned to those '
                    'instruction shapes only; explicit guest ld sequences keep the normal '
                    'read semantics); no claim is made about write-back effects of bit7'),
                'not_a_timing_model':True}
        if self.res_named_values is not None:
            # 路 BY:RES 具名档位 fixture 子键。三层身份标注(codex 参谋采纳):
            # 通道识别有推导链;原始码是显式 synthetic fixture;电压是该 fixture
            # 下的计算结果——不是测量值。
            result['res_named_values_fixture']={
                'address':hex(self.RES),
                'values':{hex(ch):v for ch,v in sorted(self.res_named_values.items())},
                'served':{hex(ch):n for ch,n in sorted(self.res_served.items())},
                'channel_identification':('derived, disassembly-pinned: the setter case-1 path '
                    '(0x6011666 -> 0x6067d8a) writes (channel_word & 0xFFFF)<<1 into '
                    'P3_ANA_CON4[4:1] (0xf2c250) before starting the conversion, so at RES-read '
                    'time the mux field identifies the sampled channel (channel_word = '
                    '0x10000|field); consistency gates: last CON write must have EN=1 and '
                    'CH_SEL(bits8-11)=0 (PMU analog path) — anything else stops honestly'),
                'value_identity':{
                    'raw_codes':'explicit synthetic fixture inputs (physically constrained '
                        'placeholders, NOT silicon measurements and NOT uniquely derivable '
                        'from the voltage ratio)',
                    'channel_ids':'derived from the analog mux field (see above)',
                    'voltages':'computed results under this fixture (real firmware arithmetic), '
                        'conditional on the fixture values'},
                'derivation':('WBG04(0x10000)=400: nonzero inside the 10-bit field with '
                    'constant-sample self-consistency (h[0x127D10]=400); VBAT_4(0x10006)=500: '
                    'physical anchor 4*(500*K/400) = ~4004mV nominal 4.0V lithium cell with '
                    'K=801+/-3d — BOTH battery-gate paths pass (float-direct 4004>=3200, '
                    'divider-fallback 4*(0x4000*K/400)>=69304 for every K in [756,846]); '
                    'VTEMP(0x10004)=400: no consumer on this frontier, in-domain nonzero only; '
                    'see docs/adc-res-consumer.md section 6 for the full chain'),
                'forbidden_region':('low values are NOT behavior-equivalent: the battery gate '
                    '(app_core 0x601232e, compiled-in default threshold 3200mV at '
                    'h[0x127734]) shuts down (0x601248e) after 11 consecutive failures'),
                'requires_pnd_completion':('RES holds a valid sample only after a completed '
                    'conversion (SDK adc_get_res gates RES on CON bit7), so the fixture '
                    'layers on fd31_adc_pnd_completion'),
                'derived_not_measured':True,
                'evidence_upgrade':('one live FD07 read of 0xfd3104 (route BA evaluated the '
                    'channel readable; USB-session RES holds the last conversion) would '
                    'upgrade the WBG04 code to measured and recover Vref')}
        return result


class MMU:
    """Functional SDK page-table projection, no TLB/cache or bus timing model."""
    def __init__(self,initial_con=None):
        if initial_con not in (None,0):raise ValueError('only explicit disabled MMU initial state supported')
        self.con=initial_con;self.begin=self.end=None
        self.walks=0
        self.last_walk=None

    def read(self,address):
        value={MMU_BASE:self.con,MMU_BASE+4:self.begin,MMU_BASE+8:self.end}[address]
        if value is None:raise Blocked('uninitialized_mmu_register',address=hex(address))
        return value

    def write(self,address,value):
        if address==MMU_BASE:
            if value&~0x31:raise Blocked('unsupported_mmu_control',value=hex(value))
            if value&1 and (self.begin is None or self.end is None or
                            self.begin%2 or self.end<self.begin or (self.end-self.begin+1)%2):
                raise Blocked('invalid_mmu_table_bounds')
            self.con=value
        elif address==MMU_BASE+4:self.begin=value
        elif address==MMU_BASE+8:self.end=value
        else:raise ValueError('not an MMU register')

    def translate(self,address,size,read_physical):
        if not 0x400000<=address<0x800000:return address
        if self.con is None:raise Blocked('uninitialized_mmu_enable',address=hex(address))
        if not self.con&1:raise Blocked('virtual_access_with_mmu_disabled',address=hex(address))
        page=(address-0x400000)//128
        entry=self.begin+page*2
        if entry+1>self.end:raise Blocked('mmu_page_outside_table',address=hex(address))
        if (address&127)+size>128:raise Blocked('mmu_cross_page_access_not_split',address=hex(address),size=size)
        tag=int.from_bytes(read_physical(entry,2),'little')
        if not tag&0x8000:raise Blocked('mmu_invalid_page',address=hex(address),entry=hex(entry))
        self.walks+=1
        physical=((tag&0x7fff)<<7)+(address&127)
        self.last_walk={'virtual':hex(address),'physical':hex(physical),'entry_address':hex(entry),'tag':hex(tag)}
        return physical

    def report(self):
        return {'con':self.con,'table_begin':self.begin,'table_end_inclusive':self.end,'walks':self.walks,
                'translation':'128B pages;2B tags;valid bit15;absolute physical page number;virtual base0x400000',
                'initial_con_is_synthetic':True,'hardware_tlb_cache_timing_simulated':False,
                **({'last_walk':self.last_walk} if self.last_walk is not None else {})}


class PartialWord:
    """Exact bitwise register state; unknown bits are never returned as zero."""
    def __init__(self,value=0,known=0):
        self.known=known&0xffffffff;self.value=value&self.known

    def apply(self,kind,operand):
        operand&=0xffffffff
        if kind=='or':self.known|=operand;self.value|=operand
        elif kind=='and':self.known|=(~operand)&0xffffffff;self.value&=operand
        else:raise ValueError('unsupported partial-word operation')

    def read(self,address):
        if self.known!=0xffffffff:
            raise Blocked('uninitialized_gpio_bits',address=hex(address),known_mask=hex(self.known))
        return self.value


class GpioOutValue:
    """Partial GPIO OUT latch word returned by the opt-in read-back path.

    Only bits the guest already wrote are known; explicit and/or masks can make
    more bits known. Any full-word consumption stops instead of fabricating the
    missing bits. Scoped to the SFC suspend/resume read-modify-write on GPIO OUT.
    """
    __slots__=('address','value','known')

    def __init__(self,address,value,known):
        self.address=address;self.known=known&0xffffffff;self.value=value&self.known

    def mask(self,operation,operand):
        operand&=0xffffffff
        if operation=='and':
            return GpioOutValue(self.address,self.value&operand,self.known|(~operand&0xffffffff))
        if operation=='or':
            return GpioOutValue(self.address,self.value|operand,self.known|operand)
        raise Blocked('gpio_out_readback_bit_operation_unmodeled',address=hex(self.address),operation=operation)

    def consumed(self,*args,**kwargs):
        raise Blocked('gpio_out_readback_consumed',address=hex(self.address),
                      known_mask=hex(self.known),known_value=hex(self.value))

    def concrete(self):
        if self.known!=0xffffffff:self.consumed()
        return self.value

    __int__=__index__=concrete
    def __float__(self):return float(self.concrete())
    def __bool__(self):return bool(self.concrete())
    def __hash__(self):return hash(self.concrete())
    def __neg__(self):return -self.concrete()
    def __pos__(self):return self.concrete()
    def __invert__(self):return ~self.concrete()

    def __repr__(self):return 'GpioOutValue'


def _gpio_out_binary(operation,reverse=False):
    def apply(self,other):
        lhs=self.concrete();rhs=other.concrete() if isinstance(other,GpioOutValue) else other
        return operation(rhs,lhs) if reverse else operation(lhs,rhs)
    return apply


for _name,_operation in (('eq',operator.eq),('ne',operator.ne),('lt',operator.lt),('le',operator.le),
        ('gt',operator.gt),('ge',operator.ge),('and',operator.and_),('or',operator.or_),
        ('xor',operator.xor),('add',operator.add),('sub',operator.sub),('mul',operator.mul),
        ('lshift',operator.lshift),('rshift',operator.rshift),('truediv',operator.truediv),
        ('floordiv',operator.floordiv),('mod',operator.mod),('pow',operator.pow)):
    setattr(GpioOutValue,'__'+_name+'__',_gpio_out_binary(_operation))
    if _name not in ('eq','ne','lt','le','gt','ge'):
        setattr(GpioOutValue,'__r'+_name+'__',_gpio_out_binary(_operation,True))


class SoftwareIrqCommands:
    """Opt-in selected-bit SET/CLR contract; WO bus readback is unverified.

    Never returns a fabricated read word or silently initializes other pending.
    BR28 mapping124+n is cross-checked against actual QCY registration/ACK.
    """
    bases=(0xffe1a0,0xffe9a0)
    source_bases=(0xffe200,0xffea00)

    def __init__(self,initial_pending=None,initial_source_enable=None):
        if initial_pending is not None and (len(initial_pending)!=2 or any(v is not None and
                (not isinstance(v,int) or not 0<=v<=15) for v in initial_pending)):
            raise ValueError('software IRQ initial pending requires two explicit low-nibble or unknown fixtures')
        if initial_source_enable is not None and (len(initial_source_enable)!=2 or any(v is not None and
                (not isinstance(v,int) or not 0<=v<=15) for v in initial_source_enable)):
            raise ValueError('software IRQ source enable requires two explicit low-nibble or unknown fixtures')
        self.initial_pending=None if initial_pending is None else tuple(initial_pending)
        self.initial_source_enable=None if initial_source_enable is None else tuple(initial_source_enable)
        self.pending=[PartialWord() if v is None else PartialWord(v,15) for v in (self.initial_pending or (None,None))]
        self.source_enable=[PartialWord() if v is None else PartialWord(v,15) for v in (self.initial_source_enable or (None,None))]
        self.commands=0;self.acknowledgements=0;self.source_commands=0

    def handles(self,address):return any(address in (b,b+4) for b in self.bases)

    def handles_source(self,address):return address in self.source_bases

    def command(self,address,mask):
        if not self.handles(address):raise Blocked('software_irq_command_address_unmodeled',address=hex(address))
        if mask&~15:raise Blocked('software_irq_command_bits_unmodeled',address=hex(address),mask=hex(mask))
        core=0 if address<self.bases[1] else 1
        clear=address==self.bases[core]+4
        self.pending[core].apply('and' if clear else 'or',~mask if clear else mask)
        self.commands+=1

    def source_rmw(self,address,kind,operand):
        # Actual handler masks the source at entry ([base]&=~8) and re-arms it
        # at exit ([base]|=8). Bit meaning and delivery interaction are
        # recorded only; eligibility is unchanged in this projection.
        if not self.handles_source(address):raise Blocked('software_irq_source_address_unmodeled',address=hex(address))
        core=0 if address<self.source_bases[1] else 1
        if kind not in ('and','or') or operand&~0xffffffff:raise Blocked('software_irq_source_rmw_form_unmodeled',address=hex(address))
        self.source_enable[core].apply(kind,operand)
        self.source_commands+=1

    def acknowledge(self,core,bit):
        # Explicit assumption of the opt-in minimal entry: taking the vector
        # clears the pending bit. Which agent clears it on silicon is unknown.
        self.pending[core].apply('and',~(1<<bit)&0xffffffff)
        self.acknowledgements+=1

    def report(self):
        return {'pending':{str(i):{'known_mask':hex(w.known&15),'known_value':hex(w.value&15)} for i,w in enumerate(self.pending)},
                'commands':self.commands,'mapping':'BR28 software IRQ124+n, n=0..3; bank selects target core',
                'selected_bit_command_is_explicit_projection':True,'wo_readback_certified':False,
                'exception_delivery_simulated':False,'initial_pending_assumed_zero':self.initial_pending is not None and 0 in self.initial_pending,
                'acknowledgements':self.acknowledgements,'acknowledged_by_delivery_is_assumption':True,
                'source_enable':{str(i):{'known_mask':hex(w.known&15),'known_value':hex(w.value&15)} for i,w in enumerate(self.source_enable)},
                'source_enable_addresses':[hex(a) for a in self.source_bases],
                'source_rmw_affects_eligibility':False,'source_enable_semantics_inferred_from_handler_bytes':True,
                'source_commands':self.source_commands,
                **({'initial_pending_fixture':[None if v is None else hex(v) for v in self.initial_pending],
                    'pending_fixture_is_not_measured_reset_state':True} if self.initial_pending is not None else {})}


RESET_FLAGS=(0xfd0004,0xf2a400,0xf2c0c0)
RESET_REASON_INPUTS=(0xfd0000,0xf2a404,0xf2c048,0xf2a60c,
                    0xf2c158,0xf2c15c,0xf2c194,0xf2c198,0xf2c2a0,0xf2d2a0,0xf2d24c)
# 配置 RMW(unknown-preserving):每个地址只接受固件实际观察到的 and 掩码,清掉的
# 位变已知 0、保留位保持未知,读回仍须全知才给值。0xf2c05c 不在默认表里——它是
# app_core power_init(0x6067876)内部的访问,只有推进到该函数的前缀才会出现;按
# opt-in 经 extra_configuration_masks 提供(见 POWER_INIT_CONFIG_MASKS),默认关,
# legacy 场景的 reset 配置报告不变。
RESET_CONFIG_MASKS={0xf2a628:0xffffffef,0xf2c054:0x3fdf,0xf2c128:0xff00}
# power_init 0x60678f4 `rmw.and.r [r7+16] &= 0x3FD`(r7=0xf2c04c,与 0xf2c054 同一
# PMU 配置族,掩码清 bit1 与 bits10+):app+ROM 全量扫描的唯一一处引用,固件从不
# 回读该地址——无复位值证据,只按 0xf2c054 先例记录「配置 RMW 不经读回」。
POWER_INIT_CONFIG_MASKS={0xf2c05c:0x3fd}
# power_init 0x60677a4(P33 配置函数,0x6067b36 调用)对 P3_RST_CON0(0xf2c054)的
# 两个 and 掩码:0x60677fa `rmw.and.r [0xf2c054]&=0xFF7F`(清 bit7)与 0x60677fe
# `&=0x7FBF`(清 bit6),与主复位初始化路径 0x6068c80 @ 0x6068c94 的 0x3fdf(清
# bit5)不同——固件真实用三个不同掩码清该寄存器的不同位。SDK p33_sfr.h 对
# P3_RST_CON0 只有地址宏(P33_ACCESS(0x15))、无字段位定义,power_reset.h 只有
# 枚举复位源、无位定义:bit6/bit7 的具体含义无 SDK 证据,只按「固件真实清这些
# 位」的观测记录掩码集,不发明未观测位。opt-in 经 extra_configuration_mask_sets
# 把这两个追加进 0xf2c054 的接受掩码集(默认单掩码校验行为不变,legacy 报告不变)。
POWER_INIT_RSTCON_MASK_SETS={0xf2c054:(0xff7f,0x7fbf)}
# walker 清尾 0x6067d64 与变体清零函数 0x6069cc6 两处同掩码复合 RMW
# `[0xF2A628] &= 0xFFFFFF7F`(清 bit7;0x6067d5e 并行对 A 半 r0=0xF2C340-7448
# 算出 0xF2A628 后由 rmw.and.nmodimm m=128(invert → 打印掩码 0xFFFFFF7F)消费),
# 与主复位初始化路径 0x6068c80 @ 0x6068c88 的 0xFFFFFFEF(清 bit4,RESET_CONFIG_
# MASKS 既有条目)是两个不同掩码——固件真实用两个掩码清 P11_SYS_CON1 的不同位。
# SDK p11_sfr.h 把 P11_SYS_CON1 标 __RW(宏值==volatile,R/W 区分只在注释);
# p11_app.h 的用法宏 MSYS_IO_LATCH_DISABLE() 即 `P11_SYS_CON1 &= ~BIT(7)`(SDK
# 侧存在清 bit7 的用法宏,bit7 字段含义按宏名仅作参考、不声称硅语义)。SDK 头
# 无位字段定义,只按「固件真实清这些位」的观测记录掩码集、不发明未观测位。
# opt-in 经 extra_configuration_mask_sets 追加进 0xf2a628 的接受掩码集(默认
# 单掩码 0xFFFFFFEF 校验行为不变,legacy 报告不变)。
P11_SYSCON1_MASK_SETS={0xf2a628:(0xffffff7f,)}
# power_init 0x60677a4(P33 配置函数)内 0x6067824 `rmw.and.nmodimm [0xf2c114]&=0x7F8`
# (r1 由 0x606781e `r1=r1-308` 从 P3_ANA_CON2 基址 0xf2c248 算出 0xf2c114,即
# P33_ACCESS(0x45)=P3_WKUP_DLY)。AND 掩码 0x7F8=bits3-10 置位:&=0x7F8 保留
# bits3-10(掩码置位位,8 位 delay 字段,保持未知)、清 bits0-2 与 bits11-31(掩码
# 清零位,已知 0;known_mask=0xfffff807)。掩码 0x7F8 是反汇编钉死的真实观测;SDK
# p33_sfr.h 只有地址宏、无字段位定义,「wakeup delay 字段」只是寄存器名 + 固件真实
# 保留这些位,不声称硅语义。注意:0xf2c114 在 app 里还有第二处引用——独立函数
# 0x6067f84(app.bin 0x67e84,power-off/低功耗例程)0x6067f98 直接 li 0xf2c114 后
# 0x6067f9e 显式读回 `r=(r&0x7F8)|0x7` 再写回(保留 bits3-10、置 bits0-2=0x7)——该
# 读回不属于「配置 RMW 不经读回」,本投影不放行(读回仍须全知,未来到达时诚实停
# unknown_reset_configuration_bits),归后续路;本 opt-in 只放行 power_init 路径的
# &=0x7F8。默认关、legacy 报告不变。
POWER_INIT_WKUP_DLY_MASK={0xf2c114:0x7f8}
WAKE_CLEAR_TARGETS={0xf2a608:(0xf2a60c,0xffffffff),0xf2c160:(0xf2c158,0xff),
                    0xf2c164:(0xf2c15c,0xff)}
RTC_WAKE_CLEAR=0xf2d248


class ResetHistoryProjection:
    """Explicit reset/wake history snapshots, not inferred reset defaults.

    Flag-clear commands do not erase the reason snapshot read afterwards.
    Their post-command readback is unknown. No mailbox, IRQ or wake generation.
    """
    def __init__(self,words=None,extra_configuration_masks=None,extra_configuration_mask_sets=None):
        self.initial_words=dict(words or {});self.words=dict(self.initial_words)
        allowed=RESET_FLAGS+RESET_REASON_INPUTS
        if any(a not in allowed or not isinstance(v,int) or not 0<=v<=0xffffffff for a,v in self.words.items()):
            raise ValueError('invalid reset/wake snapshot fixture')
        masks=dict(RESET_CONFIG_MASKS)
        if extra_configuration_masks:
            if any(a in masks or not isinstance(m,int) or not 0<=m<=0xffffffff
                   for a,m in extra_configuration_masks.items()):
                raise ValueError('invalid extra reset configuration masks')
            masks.update(extra_configuration_masks)
        # 掩码集(opt-in):默认每地址只接受 RESET_CONFIG_MASKS 里的单个 and 掩码
        # (严格单掩码校验,legacy 行为逐位不变);extra_configuration_mask_sets 只在
        # 既有地址上追加固件真实观测到的额外掩码(0xf2c054 的 0xFF7F/0x7FBF)。接受
        # 集合只记录真实观测掩码、不发明未观测位;apply 仍按位清(清掉的位已知 0、
        # 保留位未知、读回须全知),不同掩码清掉的位由 PartialWord 自然按位合并。
        mask_sets={a:(m,) for a,m in masks.items()}
        if extra_configuration_mask_sets:
            if any(a not in masks or not isinstance(ms,(tuple,list,frozenset,set)) or not ms
                   or any(not isinstance(m,int) or not 0<=m<=0xffffffff for m in ms)
                   for a,ms in extra_configuration_mask_sets.items()):
                raise ValueError('invalid extra reset configuration mask sets')
            for a,ms in extra_configuration_mask_sets.items():
                mask_sets[a]=tuple(dict.fromkeys((masks[a],)+tuple(ms)))
        self.config_masks=masks
        self.config_mask_sets=mask_sets
        self.configuration={a:PartialWord() for a in masks}
        self.flag_clear_commands=set();self.clear_commands=[]

    def handles(self,address):
        return (address in RESET_FLAGS+RESET_REASON_INPUTS or address in self.configuration or
                address in WAKE_CLEAR_TARGETS or address==RTC_WAKE_CLEAR)

    def read(self,address):
        if address in self.configuration:
            word=self.configuration[address]
            if word.known!=0xffffffff:raise Blocked('unknown_reset_configuration_bits',address=hex(address),known_mask=hex(word.known))
            return word.value
        if address in WAKE_CLEAR_TARGETS or address==RTC_WAKE_CLEAR:
            raise Blocked('wake_clear_register_readback_not_modeled',address=hex(address))
        if address in self.flag_clear_commands:raise Blocked('reset_flag_post_clear_state_unknown',address=hex(address))
        if address not in self.words:raise Blocked('unknown_reset_or_wake_history',address=hex(address))
        return self.words[address]

    def rmw(self,address,kind,operand):
        if kind!='and' or operand&0xffffffff not in self.config_mask_sets[address]:
            raise Blocked('reset_configuration_operation_not_modeled',address=hex(address))
        self.configuration[address].apply(kind,operand)

    def write(self,address,value):
        if address in RESET_FLAGS:
            old=self.read(address)
            expected=(old&0x7fbf)|64 if address==0xf2c0c0 else old|64
            if value!=expected:raise Blocked('reset_flag_command_not_modeled',address=hex(address),value=hex(value))
            self.flag_clear_commands.add(address)
        elif address==RTC_WAKE_CLEAR:
            if value:raise Blocked('rtc_wake_clear_nonzero_mapping_unverified',value=hex(value))
            self.clear_commands.append({'address':hex(address),'value':hex(value)})
        elif address in WAKE_CLEAR_TARGETS:
            target,mask=WAKE_CLEAR_TARGETS[address]
            if value&~mask:raise Blocked('wake_clear_mask_not_modeled',address=hex(address),value=hex(value))
            if value:
                if target not in self.words:raise Blocked('unknown_wake_pending_before_clear',address=hex(target))
                self.words[target]&=~value&0xffffffff
            self.clear_commands.append({'address':hex(address),'value':hex(value)})
        else:raise Blocked('reset_history_snapshot_or_config_write_not_modeled',address=hex(address))

    def report(self):
        result={'initial_full_word_fixtures':{hex(a):hex(v) for a,v in self.initial_words.items()},
                'physical_history_measured':False,'flags_cleared':[hex(a) for a in sorted(self.flag_clear_commands)],
                'flag_post_clear_readback_modeled':False,
                'configuration':{hex(a):{'known_mask':hex(w.known),'known_value':hex(w.value)} for a,w in self.configuration.items()},
                'pending_clear_commands':self.clear_commands,
                'wake_mailbox_irq_generation_or_latency_simulated':False,
                'reason_snapshots_preserved_after_flag_clear':True}
        multi={hex(a):[hex(m) for m in ms] for a,ms in self.config_mask_sets.items() if len(ms)>1}
        if multi:result['accepted_configuration_mask_sets']=multi
        return result


class Timer0:
    # source 5 的命名与时基(路 AH,三方证据):SDK 钉死参考头 gptimer_hw.h
    # (sha256 e8e3e1c1…,data/dependencies.json hardware_reference_files)
    # GPTIMER_CLK_SRC_STD12M=0b0101<<10(source 4=RC16M 与既有 16MHz 参考
    # 验证互证);SDK 驱动 gptimer_hw_v2.c bitcode IR(lib_gptimer_src_std_clk
    # ==12 → 寄存器源字段 5、频率 select 12 MHz、CON 组合 src<<10|div<<4);
    # app 自己的换算表 0x60A2B5C[src]=MHz 只有 src5=12 非零、分频实际值表
    # 0x60A2B9C 按字段索引与 SDK GPTIMER_CLK_DIV_* 宏序列逐项一致,配置函数
    # 0x600079A 直接 RMW 原始字段(src<<10/div<<4/bit14=PND_CLR/CNT=0),
    # PRD=12*10000/1-1=119999 与 0x6000898 wrapper 的 r0*1000us 需求闭环。
    # 12 MHz 是固件自己的名义换算速率,非逐片硅测量;STD12M 的物理来源
    # (PLL 分支或其他)未验证。
    STD12M_SOURCE=5
    STD12M_TICKS_PER_REF_TICK=(3,4)  # 12MHz 相对 RC16M(16MHz) 参考系 tick 的 p/q

    def __init__(self,allowed_sources=(4,)):
        self.allowed_sources=tuple(allowed_sources)
        self.con=self.cnt=self.prd=None
        self.pending=False
        self.remainder=0
        self.source_remainder=0
        self.expirations=0
        # 路 BE:opt-in 交付请求的防重入簿记。core tick 的 unresolved_overflow
        # 让计数器停在溢出态等 ACK;Timer0 是周期计数器,pending 置位后 cnt
        # 继续走(真实周期定时器行为),故只用 delivery_in_flight 记录「本
        # pending 片已请求过交付」,W1C(bit14)清 pending 时一并清零、下个
        # 周期重新具备请求资格。这不是硅状态,是模型侧每个 pending 片至多
        # 抛一次 IrqDeliveryRequest 的显式投影。
        self.delivery_in_flight=False

    def read(self,offset):
        value={0:self.con,4:self.cnt,8:self.prd}.get(offset)
        if value is None:
            raise Blocked('uninitialized_peripheral',address=hex(TIMER0+offset))
        return value|(0x8000 if self.pending and offset==0 else 0)

    def write(self,offset,value):
        if offset==0:
            mode=value&3
            if value&~0xfcf3 or mode not in (0,1):
                raise Blocked('unsupported_timer_mode',value=hex(value))
            if mode and (value>>10)&15 not in self.allowed_sources:
                raise Blocked('unsupported_timer_clock_source',value=hex(value))
            if mode and (self.cnt is None or self.prd is None):
                raise Blocked('uninitialized_timer_count_or_period')
            if value&0x4000:self.pending=False;self.delivery_in_flight=False
            self.con=value&~0xc000
        elif offset==4:
            # CNT 重写=计数器复位,分频相位与源换算相位一并归零(对 legacy
            # source 4 无影响:source_remainder 恒 0)。
            self.cnt=value;self.remainder=0;self.source_remainder=0
        elif offset==8:
            self.prd=value
        else:
            raise Blocked('unsupported_timer_register',address=hex(TIMER0+offset))

    def advance(self,source_ticks):
        if not isinstance(source_ticks,int) or source_ticks<0:raise ValueError('negative/noninteger timer progress')
        if self.con is None or self.con&3==0:return
        if (self.con>>10)&15==self.STD12M_SOURCE:
            # source_ticks 语义是 RC16M(16MHz)参考系的 tick;STD12M 计数按
            # 12/16=3/4 换算,分数余数逐步累积(无每步取整误差)。legacy 下
            # source 5 写已被 allowed_sources 拒绝,该分支只在 opt-in 后可达。
            total=self.source_remainder+source_ticks*self.STD12M_TICKS_PER_REF_TICK[0]
            source_ticks,self.source_remainder=divmod(total,self.STD12M_TICKS_PER_REF_TICK[1])
        divisor=DIVISORS[(self.con>>4)&15]
        ticks,self.remainder=divmod(self.remainder+source_ticks,divisor)
        # SDK GPTIMER_SYS_SET_PRD(x) writes x-1: period is PRD+1 ticks.
        periods,self.cnt=divmod(self.cnt+ticks,self.prd+1)
        self.expirations+=periods
        if periods:self.pending=True

    def steps_to_next_expiration(self,source_ticks_per_step):
        """距下一次到期(periods>=1)的最少模型步数,精确闭式(路 BQ/事件时钟 G1)。

        与 advance() 的两级分数余数计数逐步互逆:溢出需 N=prd+1-cnt 个
        post-divisor tick,两级累除可整体 telescoping,故需源域累计
        M=divisor*N-remainder,再解 (source_remainder+num*S)//den>=M 得
        S=ceil((den*M-source_remainder)/num);num/den=每步源域进账速率
        (STD12M:每 source_ticks_per_step 个参考 tick 进 3 个 STD12M tick,
        即 num=3*source_ticks_per_step、den=4,真实前缀形状
        prd=119999/cnt=9660/div=1 时 S=(120000-9660)/3=36780;其余源无
        换算级,num=source_ticks_per_step、den=1)。不在计数或未初始化返回
        None。只作事件时钟的推进上限;实际到期/交付仍由 advance() 逐步触发。
        """
        if self.con is None or not self.con&3 or self.cnt is None or self.prd is None:return None
        if (self.con>>10)&15==self.STD12M_SOURCE:
            num=source_ticks_per_step*self.STD12M_TICKS_PER_REF_TICK[0]
            den=self.STD12M_TICKS_PER_REF_TICK[1]
        else:
            num,den=source_ticks_per_step,1
        needed=DIVISORS[(self.con>>4)&15]*(self.prd+1-self.cnt)-self.remainder
        return -(-(den*needed-self.source_remainder)//num)


class Timer2Delay:
    """JL_TIMER2(0xfd0600 族)延时域投影(opt-in,路 BK 全设计/S19 批 1 落地)。

    W4[4] PVDD/trim 域的 0x60685fe→0x12cd26(us) 延时 helper(慢路恰 2 事务、
    快路恰 1,BK §1.4 call 偏移验算):CON=0x4000 清 pending 停表态→CNT=1→
    PRD=[0x129D88]×us/1e6(下限 2)→CON=0xC01 启动(LRC 源 src=3<<10、div=0<<4、
    mode=1)→[(cnum<<11)+0xFFE1B0]=6(IWKUP_NUM,write-only 记录)→忙等环
    0x12cd76 idle→0x12cd78 读 CON→0x12cd7a ext.l.s+ifs(r2>=0) 测 bit15 符号位
    (置位退出、无界)→退出 r0|=1<<14(W1C)写回→CON=0 停表→pop。全 26 指令
    与 BA §2.3/BK §1.3 互证(本路用仓库 codec 从 flash 母本 0x060c64fe 复核)。

    设计裁决(路 BK §2.1,三选一):W1=真计数器+窄域完成唤醒。计数语义与
    Timer0 同构(mode/src/div 字段位图、W1C bit14、PND bit15、PRD+1 SDK 公式、
    分数余量累积——差别只有两级分数的第一级是 LRC 1/80 而非 STD12M 3/4);
    唤醒件=kick 先例的「锚定 PC 完成唤醒」(机器侧 wake_timer2_poll,锚
    0x12cd76),不建 IRQ6 交付(ICU 使能零证据,W2 否决)、不读时合成 bit15
    (W3 否决:丢 4000 步语义且解决不了 idle 不可达——BK §2.1 的核心发现:
    idle_wait 投影的交付→RTI 回被中断 PC=idle 地址→再停泊闭环使忙等环的
    读指令在无唤醒件时永不可达)。「完成时刻」由模型计数器按事件时钟推进
    真实到达(N=PRD×20 步是推导结果不是预设延时),投影只声明「计数跨越
    PRD 时 pending 置位」。

    - 不读 RAM [0x129D88]:固件已把频率信念编进 PRD 写值,模型按写下的 PRD
      计数(自洽性:BK §3.1——若真实 trim 率≠名义率,PRD 与真实 tick 率同比
      缩放,模型步数随 PRD 变,不需要知道真实率)。
    - LRC 名义 200000Hz(derived-not-measured,AL 恒等式);「步数=物理时间」
      换算永远标 derived。
    - 退出序列零干预:bit15=1 读回后的 W1C 写(0xCC01)、停表写(CON=0)、pop
      返回全部真实代码执行。
    - 不建模清单(BK §2.2-6):TIMER2 IRQ6 交付与 IWKUP_NUM 写的硬件效果
      (记录 only)、硅 idle 指令语义、LRC 真实频率与抖动、同刻仲裁。
    """

    LRC_SOURCE=3  # bits10-13 字段值 3=LRC(gptimer_hw.h GPTIMER_CLK_SRC 家族,路 AH「LRC 是字段 3」互证)

    def __init__(self):
        self.con=self.cnt=self.prd=None
        self.pending=False
        # 两级分数余量:第一级 RC16M 参考 tick→LRC tick(1/80),第二级
        # LRC tick→post-divisor tick(DIVISORS[div],本路径恒 ÷1)。
        self.lrc_remainder=0
        self.remainder=0
        self.expirations=0
        self.writes=0
        self.con_reads=0
        self.advance_calls=0  # advance() 调用计数=模型步(与 counts['completed_model_steps'] 同轴)
        self.iwkup_writes=[]  # [{'core':c,'address':hex,'value':v}] 有界列表
        # 事务簿记:CON 启动写(mode 0→1)开一条,advance 到期填 expiry;
        # poll_wakes 在机器侧事件里(machine.core_woken_by_timer2_completion)。
        self.transactions=[]

    def handles(self,address):
        return TIMER2<=address<=TIMER2+8

    def handles_iwkup(self,address):
        return address in IWKUP_NUM_ADDRESSES

    def write(self,address,value,source_ticks_per_step=4):
        offset=address-TIMER2
        if not isinstance(value,int) or not 0<=value<=0xffffffff:
            raise ValueError('unsigned32 word expected')
        self.writes+=1
        if offset==0:
            # 字段位图校验照 Timer0.write 纪律(BK §2.2-1):mode∈{0,1}(捕获/PWM
            # 拒绝)、src==3(本路径唯一观测值 0xC01 的 LRC 源;非 LRC 拒绝——
            # 换算链只建了 LRC)、div 任意(DIVISORS 查表)、bit14 写 1 清 pending
            # (含复合字 0xCC01:存储 value&~0xC000)、bit15 写忽略。
            mode=value&3
            if value&~0xfcf3 or mode not in (0,1):
                raise Blocked('unsupported_timer_mode',value=hex(value),timer='timer2')
            if mode and (value>>10)&15!=self.LRC_SOURCE:
                raise Blocked('unsupported_timer_clock_source',value=hex(value),timer='timer2')
            if mode and (self.cnt is None or self.prd is None):
                raise Blocked('uninitialized_timer_count_or_period',timer='timer2')
            was_counting=self.con is not None and bool(self.con&3)
            if value&0x4000:self.pending=False
            self.con=value&~0xc000
            if mode and not was_counting:
                # 启动写=新事务(闭式在启动时刻按当前状态计算,BK §3.4)。
                self.transactions.append({'prd':self.prd,'cnt_start':self.cnt,
                                          'start_model_step':self.advance_calls,
                                          'expiry_model_step':None,
                                          'closed_form_steps':self.steps_to_next_expiration(source_ticks_per_step)})
        elif offset==4:
            # CNT 重写=计数器复位,两级分数相位一并归零(Timer0.write 同款)。
            self.cnt=value;self.lrc_remainder=0;self.remainder=0
        elif offset==8:
            self.prd=value
        else:
            raise Blocked('unsupported_timer_register',address=hex(TIMER2+offset))

    def write_iwkup(self,address,value,core):
        """IWKUP_NUM write-only 位记录(值+计数,无读回、无行为效果)。"""
        if address not in IWKUP_NUM_ADDRESSES:raise ValueError('not a modeled IWKUP_NUM word')
        if not isinstance(value,int) or not 0<=value<=0xffffffff:
            raise ValueError('unsigned32 word expected')
        self.iwkup_writes.append({'core':core,'address':hex(address),'value':value})
        if len(self.iwkup_writes)>16:self.iwkup_writes.pop(0)

    def read(self,address):
        offset=address-TIMER2
        value={0:self.con,4:self.cnt,8:self.prd}.get(offset)
        if value is None:
            raise Blocked('uninitialized_peripheral',address=hex(TIMER2+offset))
        if offset==0:
            self.con_reads+=1
            return value|(0x8000 if self.pending else 0)
        return value

    def advance(self,source_ticks):
        if not isinstance(source_ticks,int) or source_ticks<0:
            raise ValueError('negative/noninteger timer progress')
        self.advance_calls+=1
        if self.con is None or self.con&3==0:return
        # 第一级分数:source_ticks 语义是 RC16M(16MHz)参考系 tick,LRC 计数按
        # 200k/16M=1/80 换算,分数余量逐步累积(Timer0 STD12M 的 3/4 换算同构)。
        total=self.lrc_remainder+source_ticks
        lrc_ticks,self.lrc_remainder=divmod(total,LRC_REF_TICKS_PER_LRC_TICK)
        divisor=DIVISORS[(self.con>>4)&15]
        ticks,self.remainder=divmod(self.remainder+lrc_ticks,divisor)
        # SDK GPTIMER_SYS_SET_PRD(x) writes x-1: period is PRD+1 ticks(Timer0 同式)。
        periods,self.cnt=divmod(self.cnt+ticks,self.prd+1)
        self.expirations+=periods
        if periods:
            self.pending=True
            if self.transactions and self.transactions[-1]['expiry_model_step'] is None:
                self.transactions[-1]['expiry_model_step']=self.advance_calls

    def steps_to_next_expiration(self,source_ticks_per_step):
        """距下一次到期(periods>=1)的最少模型步数,精确闭式(路 BK §3.2)。

        与 advance() 的两级分数计数逐步互逆:需 post-divisor 域 N=prd+1-cnt 个
        tick,即 LRC 域累计 M=divisor*N-remainder;再解 (lrc_remainder+4*S)/80>=M
        得 S=ceil((80*M-lrc_remainder)/4(每步源域进账))。观测形状 div=1/
        CNT=1/PRD=200 → S=(80×200-0)/4=4000 精确。不在计数或未初始化返回
        None。只作事件时钟的推进上限;实际置位仍由 advance() 逐步触发。
        """
        if self.con is None or not self.con&3 or self.cnt is None or self.prd is None:
            return None
        needed=DIVISORS[(self.con>>4)&15]*(self.prd+1-self.cnt)-self.remainder
        return -(-(LRC_REF_TICKS_PER_LRC_TICK*needed-self.lrc_remainder)//source_ticks_per_step)

    def report(self):
        return {'con':self.con,'cnt':self.cnt,'prd':self.prd,'pending':self.pending,
                'expirations':self.expirations,'lrc_nominal_hz':LRC_HZ_NOMINAL,
                'writes':self.writes,'con_reads':self.con_reads,
                'transactions':[{**t,'closed_form_steps':t['closed_form_steps']}
                                for t in self.transactions[-16:]],
                'iwkup_writes':list(self.iwkup_writes),
                'sdk_naming':('JL_TIMER2 = ls_base + map_adr(0x06, 0) = 0xfd0600 (br28.h L651-665, '
                              'CON/CNT/PRD of the JL_TIMER_TypeDef family — same bit layout as '
                              'Timer0: mode bits0-1, div bits4-7, src bits10-13, PND_CLR bit14 '
                              'W1C, PND bit15); IWKUP_NUM = (cnum<<11)+0xFFE1B0 (csfr.h '
                              'JL_TypeDef_q32DSP index 0x6c, IPMASK+8), value 6 = IRQ_TIME2_IDX'),
                'readback_semantics':('CON bit15 = modeled-pending projection (PND family, '
                                      'Timer0-precedent: con|(pending<<15)); completion timing = '
                                      'event-clock counted (1 LRC tick = 80 RC16M ref ticks = 20 '
                                      'model steps at 4 ref ticks/step; 1ms = PRD 200 = exactly '
                                      '4000 model steps, closed form), not measured; the wake is '
                                      'kick-precedent (machine-side anchored poll wake at '
                                      '0x12cd76), wake_source_unverified'),
                'assumption':('real counter semantics on the Timer0 family shape (no invented '
                              'peripheral behavior); LRC nominal 200000 Hz is derived-not-measured '
                              '(the model counts the written PRD, never reads RAM [0x129D88]); no '
                              'TIMER2 IRQ6 delivery and no IWKUP_NUM hardware effect is modeled '
                              '(write-only records) — the ICU enable state has zero evidence '
                              '(route BK R1); no silicon idle semantics claimed')}


class IRQController:
    def __init__(self,*,initial_config=None,initial_ipmask=None,unmaskable_irq_projection=False):
        self.config=[None]*32 if initial_config is None else list(initial_config)
        if len(self.config)!=32:raise ValueError('need32 explicit ICFG words')
        self.ipmask=initial_ipmask
        # SDK hwi: priorities 6/7 are the unmaskable/GIEMASK class, set only via
        # irq_unmask_set (+6/clamp 7), delivered independently of IPMASK. The
        # projection assumes GIEMASK enabled and the enable bit alone gates
        # delivery; GIEMASK reset value and rules are NOT documented.
        self.unmaskable_irq_projection=unmaskable_irq_projection

    def eligible(self,index,icfg):
        if icfg&0x300!=0x300:return False  # independent gates8/9 in pinned app2/app3 measurements
        value=self.config[index//8]
        if value is None:raise Blocked('uninitialized_irq_configuration',irq=index)
        nibble=(value>>((index%8)*4))&15
        if not nibble&1:return False
        if self.ipmask is None:raise Blocked('uninitialized_ipmask')
        priority=nibble>>1
        if priority>=6:
            if self.unmaskable_irq_projection:return True
            raise Blocked('unmodeled_unmaskable_irq',irq=index,priority=priority)
        return priority>=self.ipmask


class BR28Model:
    def __init__(self,*,source_ticks_per_step=4,timer0_std12m_source=False,initial_registers=None,
                 initial_irq_config=None,initial_ipmask=None,reset_inputs_inactive=False,
                 bus_lock_model=False,initial_lock_owner='unknown',gpio_model=False,
                 mmu_model=False,initial_mmu_con=None,p11_access_probe=False,p11_status=None,
                 p11_pmu_ready_projection=False,p11_lpwr_response_projection=False,
                 p11_heap_info_bytes=None,p11_window_projection=False,
                 p2m_mailbox_ack=False,wdt_mailbox_feed_side_effect=False,
                 power_init_config_rmw=False,power_init_rstcon_mask_sets=False,
                 power_init_wkup_dly_rmw=False,
                 lp_timer_init_projection=False,lrct_trim_done_projection=False,
                 lrct_trim_result_value=None,lp_timer_con_readback_projection=False,
                 watchdog_projection=False,watchdog_progress=None,efuse_read_projection=False,efuse_words=None,
                 oscillator_configuration_projection=False,oscillator_status_word=None,timer_lsb_ticks_per_step=None,
                 gpcnt_cleanup_projection=False,gpcnt_con_readback=False,gpcnt_con_config=False,gpcnt_con_completion=False,gpcnt_num_value=None,core_tick_configuration=False,
                 cpu1_held_projection=False,initial_cpu1_control=None,initial_core_tick_controls=None,
                 clock_divider_projection=False,voltage_configuration_projection=False,pll1_configuration_projection=False,
                 p33_analog_config_projection=False,
                 mem_pwr_con_projection=False,p33_window_projection=False,
                 p33_port_sel_projection=False,
                 p33_awkup_projection=False,p33_awkup_first_read_words=None,
                 p3_ls_projection=False,p11_syscon1_mask_sets=False,
                 fd31_adc_con_projection=False,fd31_adc_con_readback=False,
                 fd31_adc_pnd_completion=False,fd31_adc_res_named_values=None,
                 p33_analog_tail_projection=False,p3_ana_read_ldoin_low_fixture=False,
                 jl_clock_dcck_projection=False,
                 jl_uart_con_projection=False,
                 jl_uart_init_write_records=False,
                 jl_uart_con1_readback=False,
                 jl_omap_write_records=False,
                 jl_imap_write_records=False,
                 jl_wl_aud_write_records=False,
                 jl_audio_dac_init_write_records=False,
                 jl_audio_adc_reset_write_records=False,
                 jl_adda_daa_con1_initial_fixture=None,
                 jl_adda_daa_con1_config_readback=False,
                 jl_audio_dac_vl0_readback=False,
                 p33_canary_projection=False,p33_canary_first_read_words=None,
                 jl_pvdd_trim_writes=False,timer2_delay_projection=False,
                 jl_audio_dac_cop_writes=False,jl_audio_dac_dma_writes=False,
                 jl_audio_adc_dma_writes=False,
                 audio_adc_dma_delivery=False,audio_adc_dma_delivery_requests=False,
                 jl_eq_init_writes=False,
                 jl_adc_start_config=False,jl_adc_start_probe_value=None,
                 cache_status_probe=False,cache_status_words=None,sfc_read_bus=False,initial_sfc_control=None,
                 secondary_sfc_read_bus=False,initial_secondary_sfc_control=None,sfc_clock_gate_projection=False,
                 hardware_crc_projection=False,
                 core_tick_progress=None,core_tick_count_while_held=None,
                 core_tick_irq3_expiry_probe_steps=None,core_tick_irq3_masked_probe=False,
                 port_initialization_projection=False,port_input_words=None,initial_usb_io_word=None,
                 debug_execution_projection=False,initial_debug_controls=None,
                 reset_history_projection=False,reset_history_words=None,
                 mpu_configuration_projection=False,initial_mpu_write_gate=None,
                 icu_initialization_projection=False,icu_command_steps=None,icu_region_projection=False,
                 dcu_cmo_projection=False,l1p_idle_projection=False,l1p_idle_word=0x80000000,
                 icu_idle_projection=False,icu_idle_word=0x80000000,
                 software_irq_command_projection=False,
                 initial_software_irq_pending=None,software_irq_delivery_requests=False,
                 unmaskable_irq_projection=False,core_tick_delivery_requests=False,
                 core_tick_prd_readback=False,
                 core_tick_cnt_held_snapshot=False,
                 core_tick_freerun_edge_period=False,
                 core_tick_cnt_snapshot_probe_value=None,
                 jl_adda_analog_family=False,
                 jl_audio_dac_tm_readback=False,jl_audio_dac_tm_probe_value=None,
                 jl_adda_ada_family=False,jl_adda_ada_probe_value=None,
                 p11_lpvad_config=False,
                 timer0_irq_delivery_requests=False,
                 timer0_irq4_expiry_probe_steps=None,timer0_irq4_masked_probe=False):
        if not 0<=source_ticks_per_step<=256:raise ValueError('invalid virtual tick increment')
        if timer_lsb_ticks_per_step is not None and (not isinstance(timer_lsb_ticks_per_step,int) or not 0<=timer_lsb_ticks_per_step<=256):
            raise ValueError('invalid explicit LSB virtual tick rate')
        if oscillator_status_word is not None and (not isinstance(oscillator_status_word,int) or not 0<=oscillator_status_word<=0xffffffff):
            raise ValueError('oscillator status fixture must be unknown or unsigned32')
        self.timer_lsb_ticks_per_step=timer_lsb_ticks_per_step
        # timer0_std12m_source(opt-in):允许 CON 时钟源字段 5=STD12M(命名与
        # 3/4 速率换算的证据见 Timer0 类注释)。默认关:source 5 写保持
        # unsupported_timer_clock_source 严格停(301,097 前沿形状),legacy
        # 场景报告不变。
        timer_sources=(4,) if timer_lsb_ticks_per_step is None else (0,4)
        if timer0_std12m_source:timer_sources=timer_sources+(Timer0.STD12M_SOURCE,)
        self.timer=Timer0(timer_sources)
        self.irq=IRQController(initial_config=initial_irq_config,initial_ipmask=initial_ipmask,
                               unmaskable_irq_projection=unmaskable_irq_projection)
        self.multicore_enabled=False;self.current_core=0;self.irq1=None;self.core_icfg={}
        self.cpu1_release_handler=None;self.secondary_cpu_executed=False
        self.cpu1_control_kick_writes=0
        if initial_software_irq_pending is not None and not software_irq_command_projection:
            raise ValueError('software IRQ pending fixture requires explicit command projection')
        if software_irq_delivery_requests and not software_irq_command_projection:
            raise ValueError('software IRQ delivery requests require the command projection')
        self.software_irq_delivery_requests=software_irq_delivery_requests
        self.software_irq=SoftwareIrqCommands(initial_software_irq_pending) if software_irq_command_projection else None
        self.registers={}
        self.reset_inputs_inactive=reset_inputs_inactive
        self.initial_registers=dict(initial_registers or {})
        self.initial_irq_config=None if initial_irq_config is None else list(initial_irq_config)
        self.initial_ipmask=initial_ipmask
        self.source_ticks_per_step=source_ticks_per_step
        self.bus_lock_model=bus_lock_model
        self.lock_owner=initial_lock_owner
        self.initial_lock_owner=initial_lock_owner
        self.gpio_model=gpio_model
        self.gpio={}
        self.mmu=MMU(initial_mmu_con) if mmu_model else None
        if p11_pmu_ready_projection and not p11_access_probe:
            raise ValueError('PMU ready response projection requires the P11 access probe')
        self.p11=(P11AccessProbe(p11_status,p11_pmu_ready_projection) if p11_access_probe else None)
        # PWR_CON bit5 响应投影叠在复位快照读回之上,没有快照就没有可投影的基础字
        if p11_lpwr_response_projection and not reset_history_projection:
            raise ValueError('P11 low-power response projection requires the reset history snapshot')
        self.p11_lp=P11LowPowerResponse() if p11_lpwr_response_projection else None
        self.p11_win=P11WindowRegisters() if p11_window_projection else None
        # p2m_mailbox_ack(BM §3 规格;首版路 B3/S19 批 2 续 0b875ed,本版 B2R
        # 重落地):P11 邮箱 ack 完成投影,必须叠在 p11 窗口投影上(M2P 消息窗
        # 是 p11_power_init 的 harness 区,邮箱事务的消息字节从那里镜像)。
        # 默认关:0xF2A60C/0xF2A618/0xF2A608 与 0xF27F43 保持各自原语义
        # (整字访问 unmapped/字节写普通路径),legacy 报告逐字节不变。
        if p2m_mailbox_ack and not p11_window_projection:
            raise ValueError('the P2M mailbox ack projection requires the P11 window projection')
        self.p2m_mailbox=P2mMailboxAck() if p2m_mailbox_ack else None
        # wdt_mailbox_feed_side_effect(路 WDFEED,opt-in,需 p2m_mailbox_ack+
        # watchdog_projection):喂狗邮箱事务闭合(回声匹配+固件 CLR)时 P11 侧
        # WDT feed 副作用投影(计数器重装,WatchdogProjection.mailbox_feed 注
        # 释全证据链)。默认关:事务闭合不触碰看门狗——32,737,356 停点
        # (watchdog_expired_delivery_not_implemented)的钉死语义,legacy 报告
        # 逐字节不变。
        if wdt_mailbox_feed_side_effect and not p2m_mailbox_ack:
            raise ValueError('the WDT mailbox feed side effect requires the P2M mailbox ack projection')
        if wdt_mailbox_feed_side_effect and not watchdog_projection:
            raise ValueError('the WDT mailbox feed side effect requires the watchdog projection')
        self.wdt_mailbox_feed_side_effect=wdt_mailbox_feed_side_effect
        # lp_timer_init:power_init 尾部低功耗定时器初始化链(P11_LPTMR0 CON 写、
        # PMU 配置族 0xf2c04c/50 写、JL_LRCT CON 位 RMW)的 write-only 记录。
        # 独立开关而非并入 p11_window_projection:后者的报告键在 crc_bankb 场景
        # 已钉死,并表会加键破坏既有报告字节不变。
        # lrct_trim_done:LRCT CON 读回的「trim 已完成」条件投影(bit7 置 1,
        # SFC PLL-lock bit15 先例同模式;证据等级=轮询行为推断,非硅测量)。
        # 单独开关而非并入 lp_timer_init_projection:后者关着时读回一律 Blocked
        # 是 300,236 前沿的钉死语义(实验场景的 opt-in 叠加层),合并会让该
        # 中间前沿形状不可复现。
        if lrct_trim_done_projection and not lp_timer_init_projection:
            raise ValueError('the LRCT trim-done readback projection requires the LP-timer init projection')
        # lrct_trim_result_value:JL_LRCT NUM(0xfd3604,trim 结果计数,硅测量值)
        # 的显式探路值(derived-not-measured),与 p11_heap_info_bytes 同族的
        # opt-in 数据 fixture;必须叠在 trim-done 投影上(NUM 读只有轮询放行
        # 后才真实到达)。未提供时 NUM 读严格停语义不变(300,240 前沿形状)。
        if lrct_trim_result_value is not None:
            if not isinstance(lrct_trim_result_value,int) or not 0<=lrct_trim_result_value<=0xffffffff:
                raise ValueError('the LRCT trim-result value must be unsigned32')
            if not lrct_trim_done_projection:
                raise ValueError('the LRCT trim-result value requires the trim-done readback projection')
        # lp_timer_con_readback:opt-in 的 P11_LPTMR0 CON 读回=最后写值投影
        # (SDK p11_sfr.h 标 CON0/CON1/CON2 __RW;SFC 时钟 +8 先例同族)。必须
        # 叠在 write 记录投影上——没有 stores 就没有「最后写值」可回读。默认关:
        # CON 读回保持 Blocked(301,540 前沿形状)。
        if lp_timer_con_readback_projection and not lp_timer_init_projection:
            raise ValueError('the LP-timer CON readback projection requires the LP-timer init projection')
        self.lp_timer=(LpTimerInitRegisters(trim_done_projection=True,trim_result_value=lrct_trim_result_value,
                                            con_readback_projection=lp_timer_con_readback_projection)
                       if lrct_trim_done_projection
                       else LpTimerInitRegisters(con_readback_projection=lp_timer_con_readback_projection)) \
                      if lp_timer_init_projection else None
        if p11_heap_info_bytes is not None and (not isinstance(p11_heap_info_bytes,(bytes,bytearray))
                                                or len(p11_heap_info_bytes)!=4):
            raise ValueError('P11 heap info fixture must be exactly 4 bytes')
        self.p11_heap_info_bytes=(None if p11_heap_info_bytes is None else bytes(p11_heap_info_bytes))
        self.watchdog=WatchdogProjection(watchdog_progress) if watchdog_projection else None
        self.efuse=EfuseReadProjection(efuse_words) if efuse_read_projection else None
        self.oscillator_configuration_projection=oscillator_configuration_projection
        self.oscillator_status_word=oscillator_status_word
        # gpcnt_con_readback(路 CG,opt-in,需 gpcnt_cleanup_projection):
        # GPCNT_CON 读回=位粒度「最后固件写」投影(返回部分值对象)。必须叠在
        # cleanup 投影上——没有 disable/clear RMW 的位记录就没有可回读的已知
        # 位。默认关:读回保持 Blocked(unknown_gpcnt_configuration_bits,
        # 601,880 前沿形状)。
        if gpcnt_con_readback and not gpcnt_cleanup_projection:
            raise ValueError('the GPCNT CON readback projection requires the cleanup projection')
        # gpcnt_con_config(路 JLEQ 续五,opt-in,需 gpcnt_con_readback):W4[4]
        # VLVD 域 GPCNT 重配置面——全字读回(未知配置位合成初始 0)+整字写
        # 记录+|=0x1 使能形状;必须叠在读回投影上(ld.w 拦截在 readback_
        # projection 分发内)。
        if gpcnt_con_config and not gpcnt_con_readback:
            raise ValueError('the GPCNT config-word release requires the CON readback projection')
        # gpcnt_con_completion(路 JLEQ 续七,opt-in,需 gpcnt_con_config):
        # 稳态测量任务 0x6069538 的 bit7 完成位轮询投影(读侧强制置 1,
        # lrct_trim_done/SFC PLL-lock 同模式);NUM 读保持严格停。
        if gpcnt_con_completion and not gpcnt_con_config:
            raise ValueError('the GPCNT completion-bit projection requires the config-word release')
        # gpcnt_num_value(路 GPCNUM,opt-in,需 gpcnt_con_completion):NUM 读
        # (0xfd1104)只有在完成位投影放行轮询后才真实到达(依赖链按世界
        # 执行序);值校验在 GPCNTCleanup 内(非零下界:消费面 0x606957a
        # 无零检查直接除 NUM)。
        self.gpcnt=(GPCNTCleanup(readback_projection=gpcnt_con_readback,config_words=gpcnt_con_config,
                                 completion_bit=gpcnt_con_completion,num_value=gpcnt_num_value)
                    if gpcnt_cleanup_projection else None)
        if initial_core_tick_controls is not None and len(initial_core_tick_controls)!=2:raise ValueError('need two explicit core tick controls')
        if core_tick_progress is not None and len(core_tick_progress)!=2:raise ValueError('need explicit progress for two core ticks')
        # core_tick_prd_readback(路 BQ 一案,opt-in,默认关):unresolved 溢出
        # 窗内放行 PRD(offset 8)读=回放最近真实写入;必须叠在 core tick
        # 配置上(无配置无该停点形状)。CON/CNT 读与 CNT/pending/ACK/计时
        # 相位全不动;prd_write 缺失时读侧继续严格停(不给默认值)。
        if core_tick_prd_readback and not core_tick_configuration:
            raise ValueError('the core-tick PRD readback projection requires the core tick configuration')
        # core_tick_cnt_held_snapshot(路 B3/S19 批 2 续,opt-in,默认关):
        # unresolved 溢出窗内放行 CNT(offset 4)读=返回模型实际存储的溢出后
        # 快照(冻结余数;显式「溢出后 CNT 冻结」假设 assumed-not-measured,
        # 非 PRD-1/非周期末值)。同样必须叠在 core tick 配置上;读无副作用,
        # CON 读/pending/ACK/计时相位全不动。
        if core_tick_cnt_held_snapshot and not core_tick_configuration:
            raise ValueError('the core-tick CNT held-snapshot projection requires the core tick configuration')
        # core_tick_freerun_edge_period(路 JLEQ 续二,opt-in,默认关):放行
        # PRD==0xFFFFFFFF 边沿周期使能(固件 tick 重启形状);必须叠在 core
        # tick 配置上(无配置无该停点形状)。
        if core_tick_freerun_edge_period and not core_tick_configuration:
            raise ValueError('the core-tick freerun edge-period release requires the core tick configuration')
        # 敏感性探针值(probe_only,CV 双探针先例):非 None 时 unresolved 窗内
        # CNT 读返回该值(0 vs 周期末值 PRD-1 两分叉记录首次分叉),必须叠在
        # held 快照开关上(探针是快照读的替换值,不是独立投影)。
        if core_tick_cnt_snapshot_probe_value is not None:
            if not (isinstance(core_tick_cnt_snapshot_probe_value,int)
                    and 0<=core_tick_cnt_snapshot_probe_value<=0xffffffff):
                raise ValueError('the core-tick CNT snapshot probe value must be unsigned32')
            if not core_tick_cnt_held_snapshot:
                raise ValueError('the core-tick CNT snapshot probe value requires the held-snapshot projection')
        self.core_ticks=[CoreTickStopped(value,rate,prd_readback=core_tick_prd_readback,
                                         cnt_held_snapshot=core_tick_cnt_held_snapshot,
                                         freerun_edge_period=core_tick_freerun_edge_period) for value,rate in zip(initial_core_tick_controls or (None,None),
                       core_tick_progress or (None,None))] if core_tick_configuration else None
        if core_tick_configuration and core_tick_cnt_snapshot_probe_value is not None:
            self.core_ticks[0].cnt_snapshot_probe_value=core_tick_cnt_snapshot_probe_value
        # jl_adda_analog_family(路 B3/S19 批 2 续):JL_ADDA 模拟族五字容器
        # (0xfe0a00/08/0c/48/4c,函数 0x606abf0 静态全枚举白名单;+4 DAA_CON1
        # 不在白名单——路 CV 两开关分立容器独占,地址集互斥)。
        self.jl_adda_family=JLAddaAnalogFamilyRegisters() if jl_adda_analog_family else None
        # jl_audio_dac_tm_readback(路 DT/S19 批 2 续二):JL_AUDIO DAC_TM0/TM1
        # (0xfe0524/0xfe0528)半字 field setter 0x606ab92 容器——合成初始 0
        # +读回=当前值,初值来源 synthetic_zero 具名登记(类注释 codex 定调
        # 四点);probe_value 仅 probe 场景用(TM0 初值一次性替换,TM1 不扰动)。
        if jl_audio_dac_tm_readback:
            if jl_audio_dac_tm_probe_value is not None and (not isinstance(jl_audio_dac_tm_probe_value,int)
                    or not 0<=jl_audio_dac_tm_probe_value<=0xffffffff):
                raise ValueError('invalid JL_AUDIO DAC_TM probe initial word')
        elif jl_audio_dac_tm_probe_value is not None:
            raise ValueError('the JL_AUDIO DAC_TM probe value rides on the DAC_TM readback container')
        self.jl_audio_dac_tm=(JLAudioDacTmRegisters(tm0_probe_initial=jl_audio_dac_tm_probe_value)
                              if jl_audio_dac_tm_readback else None)
        # jl_adda_ada_family(路 ADA/S19 冲刺批 2 续三):JL_ADDA ADA 子族九字容器
        # (0xfe0a20..0xfe0a40 ADA_CON0..8;0xfe0a44 零触达不进白名单)——新开关
        # 分立而非扩展 B3 五字容器 WORDS(直接扩会静默改变已开场景服务面,
        # 违反「新开关默认关 legacy 逐字节不变」红线);probe_value 仅 probe
        # 场景用(ADA_CON0 初值一次性替换,其余八字不扰动保归因干净)。
        if jl_adda_ada_family:
            if jl_adda_ada_probe_value is not None and (not isinstance(jl_adda_ada_probe_value,int)
                    or not 0<=jl_adda_ada_probe_value<=0xffffffff):
                raise ValueError('invalid JL_ADDA ADA_CON0 probe initial word')
        elif jl_adda_ada_probe_value is not None:
            raise ValueError('the JL_ADDA ADA probe value rides on the ADA family container')
        self.jl_adda_ada=(JLAddaAdaFamilyRegisters(ada_probe_initial=jl_adda_ada_probe_value)
                          if jl_adda_ada_family else None)
        # p11_lpvad_config(路 LPV):P11_SFR LPVAD VAD_ACON0(0xf2b404)单字容器
        # ——新开关分立而非扩 p11_window 白名单(改已开开关服务面=改契约,且
        # 「纯写记录读回 Blocked」族与配置读回语义冲突;codex 定调)。
        self.p11_lpvad=(P11LPVADConfigRegisters()
                        if p11_lpvad_config else None)
        if core_tick_delivery_requests and not core_tick_configuration:
            raise ValueError('core tick delivery requests require the core tick configuration')
        self.core_tick_delivery_requests=core_tick_delivery_requests
        # 路 BE(opt-in,默认关):Timer0 pending→IRQ4 资格合格时向机器抛
        # IrqDeliveryRequest(core,irq=4) 而非 interrupt_delivery_not_implemented
        # 严格停。资格链不变(ICFG gates8/9 + config[0] nibble 使能位 + 优先级
        # 对 IPMASK,advance() 逐核扫描);每个 pending 片至多请求一次
        # (Timer0.delivery_in_flight,handler 的 W1C |=0x4000 清 pending 时
        # 一并清零),pending 不由交付清除。
        if not isinstance(timer0_irq_delivery_requests,bool):
            raise ValueError('Timer0 IRQ4 delivery requests are an opt-in boolean')
        self.timer0_irq_delivery_requests=timer0_irq_delivery_requests
        # 探路 fixture(显式假设,仅 probe 场景用):把 STD12M Timer0 的首次
        # 到期拉近到启动写之后 N 个模型步(每步 3 个 STD12M tick)。运行态
        # 已证实(路 BE trace):驱动安装循环在 ~110,768 步把 IRQ4 以
        # priority 1 真实使能(0xffe100=0x30000 起,word 后长成 0x33b55,
        # 此后到前沿无清零)——所以默认探针就是「真实资格」的交付分支;
        # timer0_irq4_masked_probe 是反事实对照(把 IRQ4 nibble 清零的
        # 「未安装」世界),演示惰性分支:到期只置 pending、不请求不停。
        if timer0_irq4_expiry_probe_steps is not None and (not isinstance(timer0_irq4_expiry_probe_steps,int)
                or not 1<=timer0_irq4_expiry_probe_steps<=100000):
            raise ValueError('invalid Timer0 IRQ4 expiry probe step count')
        self.timer0_irq4_expiry_probe_steps=timer0_irq4_expiry_probe_steps
        if not isinstance(timer0_irq4_masked_probe,bool):
            raise ValueError('Timer0 IRQ4 masked probe is an opt-in boolean')
        self.timer0_irq4_masked_probe=timer0_irq4_masked_probe
        self.timer0_irq4_probe_armed=False
        # 路 BF 探路 fixture(显式合成假设,仅 probe 场景用,默认关):把 CPU0
        # core tick(IRQ3)的首次溢出拉近到 tick_timer_init(0x60661e8)的启动
        # 写(0x606622e st.w [r5+0]=1,CON 0→1)之后 N 个模型步(每步 progress
        # 个 tick)。证据核验(本路,真实 app 反汇编三方互证):注册序
        # CON=0 停表→request_irq(3,1,0x60661a4,cnum)@0x6066210→CNT=0→PRD→
        # CON=1,即启动写时 IRQ3 的向量槽 0x19fe0c=0x60661a4 与 config[0]
        # 的 IRQ3 nibble(bits12-15,priority 1)已由真实前缀自己写好——探针
        # 不注入任何资格 fixture;core_tick_irq3_masked_probe 是反事实对照
        # (清 IRQ3 nibble 的「未安装」世界),演示惰性分支:溢出只置 pending、
        # 不请求不停(pending 期间 advance() 每步重检资格,Timer0 同款;
        # masked 世界资格恒不过,故到停点保持 pending)。
        if core_tick_irq3_expiry_probe_steps is not None and (
                not isinstance(core_tick_irq3_expiry_probe_steps,int)
                or not 1<=core_tick_irq3_expiry_probe_steps<=1000000):
            raise ValueError('invalid core tick IRQ3 expiry probe step count')
        self.core_tick_irq3_expiry_probe_steps=core_tick_irq3_expiry_probe_steps
        if not isinstance(core_tick_irq3_masked_probe,bool):
            raise ValueError('core tick IRQ3 masked probe is an opt-in boolean')
        self.core_tick_irq3_masked_probe=core_tick_irq3_masked_probe
        self.core_tick_irq3_probe_armed=False
        if core_tick_count_while_held not in (None,False,True):raise ValueError('invalid held-core clock hypothesis')
        self.core_tick_count_while_held=core_tick_count_while_held
        if initial_cpu1_control not in (None,0):raise ValueError('only unknown or explicitly held CPU1 fixture supported')
        self.cpu1_held_projection=cpu1_held_projection;self.initial_cpu1_control=initial_cpu1_control
        self.cpu1_control=initial_cpu1_control
        self.clock_divider_projection=clock_divider_projection
        self.voltage_configuration_projection=voltage_configuration_projection
        self.pll1_configuration_projection=pll1_configuration_projection
        self.p33_analog_config_projection=p33_analog_config_projection
        # mem_pwr_con:opt-in 的 P11_SYSTEM.MEM_PWR_CON(0xf2a62c)位 RMW 的
        # write-only 位记录(OR 置位/AND 清位,不经读回,与 cpu1_control kick
        # 位/q32s PMU_CON |=1/LRCT 位 RMW 同族)。SDK p11_sfr.h 标 __RW(宏值
        # == __WO)、p11_app.h 有 bits0-5 字段位定义;但固件只有 OR/AND 部分字
        # RMW、唯一读回(0x6067844)是死读(值被下一指令覆盖),没有「最后整字
        # 写值」可回读——故不选 lp_timer 的读回=最后写值投影,选 write-only。
        # 死读由 probe_boot 的 opaque 死读门(证明值从不消费)单独放行。默认关:
        # 0xf2a62c 读保持 unmapped_memory(301,676 前沿形状)。
        self.mem_pwr_con=MemPwrConRegisters() if mem_pwr_con_projection else None
        # p33_window(opt-in):P33 窗无名寄存器族的 write-only 字节序列记录
        # (证据与形状见 P33WindowByteRegisters 类注释;默认关,这族地址保持
        # unmapped,302,581 前沿形状不变)。
        self.p33_window=P33WindowByteRegisters() if p33_window_projection else None
        # p33_port_sel(opt-in):P3_PORT_SEL0..11 的 write-only 整字记录(路 AZ;
        # 证据与形状见 P33PortSelRegisters 类注释;默认关,0xf2c304 写保持
        # unmapped_or_readonly_write,302,620 前沿形状不变)。
        self.p33_port_sel=P33PortSelRegisters() if p33_port_sel_projection else None
        # p33_awkup(opt-in,路 BB):P3_AWKUP 族 SDK 有名寄存器的「仅记录写访问」
        # + 单寄存器首次读具名 fixture。写侧整字/RMW 记录;读侧只有显式
        # fixture 覆盖的寄存器的首次读返回具名字,其余读(未覆盖/写后/重复)
        # 一律严格停——首次读不等于全时读回,不发明复位值,不扩展到全族。
        # 默认关:0xf2c208 写保持 unmapped_or_readonly_write(303,853 前沿形状)。
        if p33_awkup_first_read_words:
            if not p33_awkup_projection:
                raise ValueError('P3_AWKUP first-read fixtures require the P3_AWKUP projection')
            if any(a not in P33AwkupRegisters.ADDRESSES or not isinstance(v,int)
                   or not 0<=v<=0xffffffff for a,v in p33_awkup_first_read_words.items()):
                raise ValueError('invalid P3_AWKUP first-read fixture')
        self.p33_awkup=(P33AwkupRegisters(p33_awkup_first_read_words)
                        if p33_awkup_projection else None)
        # p3_ls(opt-in,路 BD):P3_LS_P11+P3_LS_IO_DLY..PLL 的 write-only 整字
        # 记录(证据与形状见 P3LsRegisters 类注释;默认关,0xf2c290 写保持
        # unmapped_or_readonly_write,304,012 前沿形状不变)。0xf2a638 全
        # app/ROM 零访问(路 AZ 静态读法修正),不建模。
        self.p3_ls=P3LsRegisters() if p3_ls_projection else None
        # fd31_adc_con(opt-in,路 BG):0xfd3100=JL_ADC->CON 的 write-only 整字
        # 记录(证据与形状见 Fd31AdcConRegisters 类注释;命名修正——路 BD 记的
        # 「PMU/电源口」实为 ADC CON,参考 br28.h 同 JL_LRCT 锚点头)。默认关:
        # 0xfd3100 写保持 unmapped_or_readonly_write(304,036 前沿形状不变);
        # RES(0xfd3104)固件零写,投影不覆盖、读保持泛 unmapped。
        # fd31_adc_con_readback(路 BN):CON 读回=最后写值投影(lp_timer CON
        # 路 AL 同族)+ RES 显式停点 kind;0xf2c250(P3_ANA_CON4,setter case1
        # 调 0x6067d8a 读改写)联动 seed 进 additional_configuration(路 AM
        # p33_analog_config 同款合成初始字 0)——开关关时 0xf2c250 保持
        # unmapped、CON 读回保持 fd31_adc_con_readback_not_modeled(304,317
        # 前沿形状不变)。
        if fd31_adc_con_readback and not fd31_adc_con_projection:
            raise ValueError('the fd31 ADC CON readback projection requires the fd31 ADC CON projection')
        # fd31_adc_pnd_completion / fd31_adc_res_named_values(路 BY/sim-adcresfix,
        # 均默认关):PND(bit7)读时完成投影与 RES 具名档位 fixture,依赖链=
        # con_projection → con_readback → pnd_completion → res_named_values
        # (RES 只有在转换完成后才持有有效样本,SDK adc_get_res 同语义)。
        # 证据链/边界抽象(原子 RMW 内部读不观察投影 bit)见
        # Fd31AdcConRegisters 类注释与 report 子键。默认关:CON 读回/RES 读
        # 语义与路 BN 完全一致,legacy 报告逐字节不变。
        if fd31_adc_pnd_completion and not fd31_adc_con_readback:
            raise ValueError('the fd31 ADC PND completion projection requires the CON readback projection')
        self.fd31_adc_con=Fd31AdcConRegisters(con_readback_projection=fd31_adc_con_readback,
                                              pnd_completion=fd31_adc_pnd_completion,
                                              res_named_values=fd31_adc_res_named_values) \
                          if fd31_adc_con_projection else None
        # p33_analog_tail(opt-in,路 BV/sim-tailproj;路 CB 扩 L5V 对):W1[0]/
        # mode 切换函数的 P33 模拟配置族 AM 容器一次扩展——P3_ANA_CON0/7/8/9/10
        # (0xf2c240/0xf2c25c..268)+ P3_CHG_CON0/CON1(0xf2c068/0xf2c06c)+
        # P3_L5V_CON0/CON1(0xf2c284/0xf2c288,W1[0] 的 r5+0x220 计算寻址)+
        # P3_CHG_CON2(0xf2c070,[r5+12] 计算寻址,充电检测比较器使能/阈值)
        # 都是路 CB 加进来的计算寻址面(字面量扫描不可见)按路 AM
        # P33_ANALOG_CONFIG 同款语义(显式合成初始字 0 + 读回=当前寄存器值;
        # 整字写/显式 ld/st 配置 RMW/复合 RMW 全走既有通用寄存器路径,机器侧
        # 零新分发)。P3_ANA_READ(0xf2c064)是模拟量读(live input,轮询+位
        # 测试消费),读回显式钉住(p3_ana_read_not_modeled,RES 红线同款)、
        # 写显式拒绝——不并进容器 seed、不发明 0/满值。默认关:全部地址保持
        # unmapped,legacy 报告逐字节不变。证据与 BO 修正(0xfd5f 族不可复现、
        # W1[0] 的 RMW 块实为 0xfd5708 PORTP GPIO 面)见 P33_TAIL_ANALOG_CONFIG
        # 常量注释与 docs/appcore-tail-sfr-semantics.md §8。
        self.p33_analog_tail_projection=p33_analog_tail_projection
        # jl_clock_dcck_projection(opt-in,路 CL/sim-dcck):JL 时钟族 DCCK_CON0/1/2
        # (0xfd0008/0c/10)按路 AM 容器同款「显式合成初始字 0 + 读回=当前寄存器
        # 值」建模。br28.h JL_CLOCK_TypeDef 全字段 __RW,SDK power_clock.c 的
        # deep-sleep save/restore 把三字列入可读回/可恢复清单(双证据);全
        # app+ROM 唯一消费者=mode 切换函数 0x6068152,其显式 ld 读值全部立即
        # 被掩码+OR 消化(seed 0 下确定性的位域配置序列),无独立状态消费。
        # 原子 RMW(rmw.or.modimm/rmw.and.modimm)与显式 ld/st RMW、整字写全走
        # 既有通用 mmio/registers 路径(机器侧零新分发)。默认关:三字保持
        # unmapped(3,070,626 @ 0x606817a 前沿形状),legacy 报告逐字节不变。
        self.jl_clock_dcck_projection=jl_clock_dcck_projection
        # jl_uart_con_projection(opt-in,路 F/sim-fd2000):JL_UART0/1/2 的 CON0|CON1
        # 整字(0xfd2000/0xfd2100/0xfd2200)按路 AM/DCCK 同款「显式合成初始字 0 +
        # 读回=当前寄存器值」建模。证据链:①br28.h `JL_UART0_BASE=ls_base+
        # map_adr(0x20,0)` 命名、JL_UART_TypeDef CON0/CON1 标 __RW;②读辅助函数
        # 0x601d198 = SDK uart_v1.c `uart_is_idle`(cpu.a/uart_v1.c.o bitcode 逐字节
        # 同形,return (word&1)^1)——读值立即被 bit0 掩码测试消化(空闲判定);③
        # bit0 是硬件「busy/空闲」状态位,固件从不写 bit0(SDK 全部 CON0 写
        # =init 0x3400/putbyte |=0x2000/各 RMW 无一处置位)——合成 0 表示「空闲」
        # (本前缀未发起任何 UART 发送,SDK 驱动自己 init 写 0x3400 也保持 bit0
        # 清零),是 derived-not-measured 的显式假设,非硅测量/非复位值声明;④写侧
        # (uart_init 的 CON0=0x3400/|=0x3 等)是 __RW 配置写,走通用 registers 路径
        # (last-write 语义),机器侧零新分发。默认关:三字保持 unmapped(3,071,380
        # @ 0x601d1ac 前沿形状),legacy 报告逐字节不变。
        self.jl_uart_con_projection=jl_uart_con_projection
        # jl_uart_init_write_records(opt-in,路 M/sim-uartwr):JL_UART 初始化配置字
        # (CON1+4/BAUD+8/OTCNT+0x10/RXSADR+0x1c/RXEADR+0x20/RXCNT+0x24/CON2+0x2c,
        # 三基址对称)的 write-only 整字记录(p33_port_sel/lp_timer SYS_DIV 同族)。
        # 前置证据(布局钉死,修正路 F 的「0xfd2004=BAUD|BUF」命名):实际 SDK
        # 构建对象 uart_v1.c.o bitcode 声明 JL_UART_TypeDef={i32 x12}(成员 k 在
        # +4k),QCY 固件写序列逐点互证(0x6034466/0x603446c/0x6034472/0x601d286/
        # 0x601d1f2/0x6034668..cc);公开 br28.h 的 u16/u8 紧凑布局与该构建对象
        # 不一致(BR31 等系列确有紧凑布局,布局分歧非笔误)——0xfd2004 是 CON1
        # 字、真 BAUD 写在 0xfd2008。放行范围=boot uart_init+uart_set_baudrate+
        # uart_dma_init 序列实际出现的纯配置整字写(st.w 族全字覆盖、旧值无关、
        # 值全为真实指令结果);读回/RMW 一律严格停;TXADR/TXCNT/BUF/HRXCNT 不
        # 进白名单(TX 路径=「写已启动硬件活动」信号,codex 风险点,出现即诚实
        # 停);运行效果(波特率/RX/TX DMA/UART IRQ)不建模——只记录「初始化
        # 配置写已执行」,不扩展为「UART 收发已验证」。默认关:这些字保持
        # unmapped(3,071,984 @ 0x603446a 前沿形状),legacy 报告逐字节不变。
        # jl_uart_con1_readback(opt-in,路 AA/sim-con1rb,需 jl_uart_init_write_
        # records):把 CON1(+4,三基址对称)从纯写白名单升级为「写记录+读回=
        # 最后写值」(lp_timer CON 路 AL/fd31 ADC CON 路 BN 同族)。证据与条件
        # 假设全文见 JLUartInitWriteRecords 文档串「路 AA 例外」段:首个真实
        # 消费者=uart_fifo_reset 复合 RMW 0x60340d8(bit4=RX DMA 重启位清零,
        # 读值参与写回);本事务 CON1 已被写 0,读 0、写回 0,平凡零发明;RMW
        # 走 probe_boot 通用 mmio 路径完整执行(内部读=投影值、写回落记录)。
        # __RW 不能证明无硬件自更新位——「读回=软件配置值」是 derived-not-
        # measured 条件假设,报告 con1_readback_projection 子键全文标注。只扩
        # CON1(BAUD/OTCNT/RXSADR/RXEADR/RXCNT/CON2 读回仍严格停;CON0 读侧
        # 已有 jl_uart_con_projection,保持不动)。默认关:CON1 读回保持
        # Blocked(3,073,918 @ 0x60340d8 前沿形状),legacy 报告逐字节不变。
        if jl_uart_con1_readback and not jl_uart_init_write_records:
            raise ValueError('the JL_UART CON1 readback projection requires the init write records')
        self.jl_uart_init_records=(JLUartInitWriteRecords(con1_readback=jl_uart_con1_readback)
                                   if jl_uart_init_write_records else None)
        # 路 AK(sim-audioinit):0x602ea68 boot 期音频框架 init 的两个已审查写域
        # (函数全图/写集枚举见 docs/wlaud-init-map.md,路 AG 预研;写集默认关、
        # 关着时 4 地址保持 unmapped,3,079,241 @ 0x602ea72 前沿形状与 legacy
        # 报告逐字节不变):
        # - jl_wl_aud_write_records:JL_WL_AUD->CON0(0xfe2200,br28.h
        #   hs_base+map_adr(0x22,0) 单成员 __RW)整字写记录+读回=最后写值
        #   (uart CON1 路 M→F→AA 最终形态;无写时显式合成 0=路 AM 先例,
        #   derived-not-measured)。放行 0x602ea72 entry 写(当前停点)与
        #   0x6071cbe DAC init 尾写。
        # - jl_audio_dac_init_write_records(需上一开关层叠:执行序上 DAC init
        #   SFR 簇 0x6071cbc..ee 只在 entry 写放行后到达):JL_AUDIO DAC_CON
        #   (0xfe0500,整字写 0xfe2200+RMW 对 &=~0x100/|=0x100)/AUD_CON
        #   (0xfe0558,RMW &=~0x40)/JL_ASS CLK_CON(0xfe0900,原子 RMW|=1)
        #   三字写记录+读回=最后写值/无写合成 0。RMW 经 probe_boot 通用 mmio
        #   路径完整执行(内部读=投影值、写回落记录),机器侧零新分发。
        #   条件族(esco/ANC/缺省分支)不进白名单,保持 unmapped 诚实停。
        #   运行效果(时钟门/模拟通路/DMA/IRQ68 事件)一律不建模。
        if jl_audio_dac_init_write_records and not jl_wl_aud_write_records:
            raise ValueError('the audio DAC-init write records require the JL_WL_AUD write records '
                             '(the DAC-init SFR cluster is only reached after the entry store)')
        self.jl_wl_aud_records=JLWlAudWriteRecords() if jl_wl_aud_write_records else None
        self.jl_audio_dac_init_records=(JLAudioDacInitWriteRecords()
                                        if jl_audio_dac_init_write_records else None)
        # 路 AS(sim-adcreset):ADC 节点 reset 写域(域三,独立子开关而非并入
        # audio_dac_init_writes——AK 域二的边界文本/断言钉死「恰三字+条件族
        # 不进白名单」,且 ADC(输入侧)与 DAC(输出侧)分域命名;执行序上
        # 0x60719c0 reset 写在 S1..S5 DAC 簇之前,依赖边只挂 wl_aud 域一):
        # JL_AUDIO->ADC_CON(0xfe0538=+0x38,路 AE 钉死命名/bit7 pending 语义)
        # 单字写记录+读回=最后写值/无写合成 0。写形状=0x60719a6 ADC 节点
        # reset 函数体内并行对 0x60719c0 `r3=r1+4596 ∥ [r3+0]=r4` 第二半以
        # 旧 r3(0xfe0538)写 0(旧寄存器并行语义=FM-1 run3/exact_parallel_
        # stores 既有机制,本写是其首个 SFR 站点;3,137,573 停点即此)。
        # 本前缀零读(AE 全 app 消费者都在 IRQ68/esco 打开链上);读回投影是
        # 容器完整面,bit7 pending 在投影下恒 0=「无 ADC 硬件活动」的诚实
        # 显形,不发明完成(路 BN 轮询先例)。ADC 域其余寄存器保持 unmapped。
        if jl_audio_adc_reset_write_records and not jl_wl_aud_write_records:
            raise ValueError('the ADC-reset write records require the JL_WL_AUD write records '
                             '(the ADC node reset is only reached after the entry store)')
        self.jl_audio_adc_reset_records=(JLAudioAdcResetWriteRecords()
                                         if jl_audio_adc_reset_write_records else None)
        # 路 CV(sim-adda):JL_ADDA->DAA_CON1(0xfe0a04)两开关分立容器——
        # 显式初值 fixture(无隐式零默认,fixture 记录名称/来源/适用场景)+
        # 配置位读回(真实写入后更新、后续读用当前状态;无「合成初始 0」
        # 分支,这是与域一/二/三容器的分野)。停原边界契约:只开读回无初值
        # 未写入、或初值开读回关写后读,一律按 3,170,362 原边界停
        # (unmapped_memory 读 0xfe0a04 逐字段一致)。白名单恰一字,不放行
        # 整个 ADDA 读族(DAA_CON0/2、ADA_CON 族、DAC_VL0 保持 unmapped,
        # 后续读逐个分类)。执行序上 0x606b426 首读只在 AS 放行后的 initcall
        # 尾块到达,依赖边挂 ADC reset 写域;fixture 值=敏感性探针对
        # {0x0,0xfffffff8} 之一(sensitivity-probe-not-plausible-state)。
        if (jl_adda_daa_con1_initial_fixture is not None or jl_adda_daa_con1_config_readback) \
                and not jl_audio_adc_reset_write_records:
            raise ValueError('the JL_ADDA DAA_CON1 fixture/readback requires the ADC-reset '
                             'write records (the DAA_CON1 first read is only reached in the '
                             'initcall tail after the ADC-node reset release)')
        if jl_adda_daa_con1_initial_fixture is not None and (not isinstance(jl_adda_daa_con1_initial_fixture,int)
                                                             or not 0<=jl_adda_daa_con1_initial_fixture<=0xffffffff):
            raise ValueError('the JL_ADDA DAA_CON1 initial fixture must be an explicit unsigned32 word')
        self.jl_adda_daa_con1=(JLAddaDaaCon1Registers(initial_word=jl_adda_daa_con1_initial_fixture,
                                                      config_readback=jl_adda_daa_con1_config_readback)
                               if (jl_adda_daa_con1_initial_fixture is not None
                                   or jl_adda_daa_con1_config_readback) else None)
        # 路 CW(S19 批 0b/sim-s19b0):JL_AUDIO->DAC_VL0(0xfe051c)数字音量字
        # 「写记录+读回」容器——3,170,379 停点 0x606db4c(setter 0x606db36 并行对
        # 第二半的首读)按 CV 裁定的「配置」分类放行:读值只进 insert 组装不进
        # 分支,且本前缀唯一调用点 r0=0xFF 双通道下写回恒 0x0 与初值逐位无关
        # →无写时首读由显式合成初始字 0 服务(路 AM/AK 容器族,derived-not-
        # measured),不立 CV 式显式 fixture 开关(DAA_CON1 的保留位被写持久
        # 携带才需要 fixture);敏感性探针 ones 字一次性分叉钉死观察路径对
        # 初值不敏感(类注释全文)。执行序上 0x606db4c 只在 CV 放行 DAA_CON1
        # 首读(显式 fixture)后的同一 DAC-init 驱动内到达——依赖边挂 adda 域。
        # 白名单恰一字,JL_AUDIO 数字域其余(15 个 li 站点)保持 unmapped。
        if jl_audio_dac_vl0_readback and self.jl_adda_daa_con1 is None:
            raise ValueError('the JL_AUDIO DAC_VL0 readback requires the JL_ADDA DAA_CON1 '
                             'release (the DAC_VL0 setter call 0x6071b76 sits immediately '
                             'after the DAA_CON1 fixer call 0x6071b70 in the same DAC-init '
                             'driver)')
        self.jl_audio_dac_vl0=(JLAudioDacVl0Registers()
                               if jl_audio_dac_vl0_readback else None)
        # 路 R(sim-gpioface):JL_OMAP 逐引脚输出功能选择字(0xfd58xx)的
        # write-only 整字记录。识别链与 49 字白名单见 JL_OMAP_OUT_WORDS 常量
        # 注释;本前缀首写=uart_init TX 引脚复用 0x60012de 写 0xfd58b8=0x9f
        # (FO_UART0_TX)。默认关:0xfd58xx 保持 unmapped,legacy 报告不变。
        self.jl_omap_records=JLOmapWriteRecords() if jl_omap_write_records else None
        self.jl_imap_records=JLImapWriteRecords() if jl_imap_write_records else None
        # p3_ana_read_ldoin_low_fixture(opt-in,路 CB/sim-anaread,需 tail 投影):
        # P3_ANA_READ(0xf2c064)读侧三选一按证据落地=方案 (c) 具名常量 live-input
        # fixture。证据链(pinned SDK commit 67328f4,全文档见
        # docs/appcore-tail-sfr-semantics.md §9):①charge_hw.h 逐位语义——
        # bit1=LVCMP_DET(ldoin vs vbat 比较器)、bit2=LDO5V_DET(ldoin 检测)、
        # bit0=CHARGE_FULL_FLAG;②charge.c 驱动注释钉阈值:bit1=1⟺ldoin>vbat、
        # bit2=1⟺ldoin>0.6V(≈充电线/USB 在位),QCY W1[0]=0x6012e3e 的 20 轮
        # 检测循环与 SDK check_charge_state() 逐行同形(20 轮、LVCMP||LDO5V、
        # udelay(1000));③QCY app 全量 5 处 li 读者只单 bit 消费 bit1/bit2,
        # 零写者——无完成位语义、无 readback=末值可能(方案 (a) 不适用);
        # ④场景锚=电池启动+无外部供电(ldoin≈0V)→ 两比较器读 0(与路 BY 的
        # RES fixture 同一物理场景:电池在位供电 3780mV,充电输入引脚无输入;
        # 充电窗门 config 99 缺失被前置跳过,与 ldoin 无关、不作为证据)。
        # **形状=常量**(codex 参谋采纳):轮询语义要求 21+ 次重复读,首读
        # fixture 只会把边界挪到第二读;duration=本场景内持续不变的外部输入,
        # 不建模插拔/去抖时序。**三层标注**:derived_mask=0b110(只有 bit1/
        # bit2 有推导链),其余位显式填零非推导(当前已核验消费者不使用),
        # evidence_status=derived-not-measured;检测器有效且稳定是显式假设
        # (W1[0] 在轮询前刚配置 L5V_CON0/1 与 CHG_CON0/1,使能/阈值配置真实
        # 执行,但比较器输出时序不建模)。默认关:读回保持 p3_ana_read_
        # not_modeled 严格停(447,383 前沿形状不变),legacy 报告逐字节不变。
        if p3_ana_read_ldoin_low_fixture and not p33_analog_tail_projection:
            raise ValueError('the P3_ANA_READ ldoin-low fixture requires the p33 analog tail projection')
        self.p3_ana_read_ldoin_low_fixture=p3_ana_read_ldoin_low_fixture
        self.p3_ana_read_served=0
        # p33_canary(opt-in,路 BV):金丝雀树/低功耗域 P3_VLVD_CON(0xf2c044)+
        # P3_PCNT_CON(0xf2c124)的「仅记录写访问」+ 单寄存器首次读具名 fixture
        # (BB 的 P3_AWKUP 先例同模式;修正 BO §N4 的 +0x120 基址误记)。健康
        # 主路径不可达,维持 watch-only;默认关,legacy 不变。
        if p33_canary_first_read_words:
            if not p33_canary_projection:
                raise ValueError('P33 canary first-read fixtures require the canary projection')
            if any(a not in P33CanaryWatchRegisters.ADDRESSES or not isinstance(v,int)
                   or not 0<=v<=0xffffffff for a,v in p33_canary_first_read_words.items()):
                raise ValueError('invalid P33 canary first-read fixture')
        self.p33_canary=(P33CanaryWatchRegisters(p33_canary_first_read_words)
                         if p33_canary_projection else None)
        # 路 BM §1(S19 批 1 ①):W4[4] PVDD/trim 域容器——P3_ANA_CON13(0xf2c274)/
        # P3_ANA_CON1(0xf2c244)/P3_PVDD0_AUTO(0xf2c10c)按路 AM 容器同款「显式
        # 合成初始字 0+读回=当前寄存器值」seed(seed 语句在 additional_
        # configuration 初始化后的 seed 段,见下)。不并进 P33_ANALOG_CONFIG/
        # P33_TAIL_ANALOG_CONFIG 常量(那会改「旧容器开而本开关关」世界的报告
        # 形状,BV/BN 先例纪律)。p33_sfr.h 纯地址宏、无类型标注——读回语义取
        # VOLTAGE_CONFIG/P33_ANALOG_CONFIG 一脉先例,不臆造硅模拟状态;P3_PVDD1_
        # AUTO(0xf2c110,VOLTAGE_CONFIG 已建模)与 P3_ANA_CON5(0xf2c254,AM 已
        # 建模)是邻域既有面,不在本容器。默认关:三地址保持 unmapped
        # (3,202,148 后 W4[4] 停点① 0x60686fc 形状),legacy 逐字节不变。
        self.jl_pvdd_trim_enabled=jl_pvdd_trim_writes
        # 路 BK(S19 批 1 ②):JL_TIMER2 延时域投影——Timer2Delay 计数器类
        # (写记录+LRC 源名义 200kHz 分数累积+PRD 跨越置 pending+CON 读回
        # bit15+IWKUP_NUM 0xFFE1B0 write-only 记录;完成唤醒件在机器侧
        # wake_timer2_poll,事件时钟期限并入同一 min())。默认 None=关闭
        # (0x12cd36 unmapped 写停点形状,fd31 系列同款惯例)。
        self.timer2=Timer2Delay() if timer2_delay_projection else None
        # 路 BM §4.3 行 0b 勘误(S19 批 1 ④):JL_AUDIO DAC_COP(0xfe0534,
        # br28.h 成员 13 __WO)写侧 write-only 整字记录——全 app 恰两处纯写
        # 0x7F(W1[3] 尾 0x606ab2c+音频域 0x606e038)、零读回,按消费面判定
        # (路 CV 分类先例);D2 场景的 RMW/读面属 D1 后另立证据包。默认关:
        # 0xfe0534 保持 unmapped(3,202,148 前沿形状),legacy 逐字节不变。
        self.jl_audio_dac_cop=JLAudioDacCopRegisters() if jl_audio_dac_cop_writes else None
        # 路 BM §4.3 行 0b 勘误之二(S19 批 1 ④续):W1[3] 尾段 DAC 启动函数
        # 0x6071e74 的四个 DAC DMA 配置整字写(DAC_COP 放行后 +35 步的
        # 0x6071e92 写 0xfe0508=1 首)——恰四写零读回(探路实证),write-only
        # 记录(DAC_COP 同族);DMA 传输/IRQ39/数据流不建模。依赖边挂
        # jl_audio_dac_cop_writes(执行序 0x6071e92 在 0x606ab2c 之后)。
        # 默认关:四字保持 unmapped,legacy 逐字节不变。
        self.jl_audio_dac_dma=(JLAudioDacDmaWriteRecords() if jl_audio_dac_dma_writes else None)
        # 路 JLA:JL_AUDIO ADC 通道缓冲配置五字(0xfe053c/40/44/48/4c,set_buffer
        # 0x606dee4 五写)写侧 write-only 整字记录——恰五写零读(本前缀),与
        # BM DAC DMA 四字同族;audio_adc_irq_handler 的 ADR/PNS/SRP 读属 D2-b
        # 后域留严格停(显式 kind)。默认关:五字保持 unmapped
        # (21,800,325 前沿形状),legacy 逐字节不变。
        # 路 D2AI(sim-d2ai,预研 §7 步 1):ADC DMA 交付半边扩类(opt-in,
        # 默认关)——读回投影(五字+SRN)+pending 位+produce_block+advance()
        # IRQ68 扫描(见下)。前置:五字容器(write 侧)+ADC reset 写域
        # (ADC_CON bit7 投影叠加在读侧、W1C 联动挂在写侧)。默认关时行为与
        # 路 JLA 半边逐字节一致。
        if audio_adc_dma_delivery and not jl_audio_adc_dma_writes:
            raise ValueError('the ADC DMA delivery projection requires the five-word '
                             'write records (set_buffer must run first)')
        if audio_adc_dma_delivery and not jl_audio_adc_reset_write_records:
            raise ValueError('the ADC DMA delivery projection requires the ADC-reset '
                             'write records (ADC_CON 0xfe0538 must be served for the '
                             'pending-bit overlay)')
        if audio_adc_dma_delivery_requests and not audio_adc_dma_delivery:
            raise ValueError('the ADC DMA delivery requests require the delivery projection')
        self.audio_adc_dma_delivery_requests=audio_adc_dma_delivery_requests
        self.jl_audio_adc_dma=(JLAudioAdcDmaWriteRecords(delivery=audio_adc_dma_delivery)
                               if jl_audio_adc_dma_writes else None)
        # 路 JLEQ/sim-jleq:EQ 初始化函数 0x6072b24 两字(JL_EQ->CON0 0xfe1500
        # /FLT_ADR 0xfe151c)write-only 整字记录——29,215,313 停点 0x6072b92
        # (写 CON0=0)放行;boot 路径只写不读(两写无条件整字 st.w、无 RMW,
        # codex 勘误 0x6072b68 bnez 只跳分配不跳两写);六字 0xfe1504..0xfe1518
        # 与 0xfe1520 起零触达不进白名单。默认关:两字保持 unmapped
        # (29,215,313 前沿形状),legacy 逐字节不变。
        self.jl_eq_init=(JlEqInitWriteRecords() if jl_eq_init_writes else None)
        # 路 ASS:ADC 启动函数配置块四字(JL_ASS MIC_CTL0/1+JL_AUDIO MBG_CON/
        # ADC_CON1)合成配置读回容器——21,800,455 停点 0x606e044(间接构造
        # 地址首停点,r0=r5+976)放行;读值只进写回组装(B3/DT/ADA/LPV 判据);
        # 0xfe0900/0xfe0558 留 AK、0xfe0550 留 unmapped(JLA 边界)、
        # 0xfe090c/0xfe0910/0xfe0560 零触达不进。默认关:四字保持 unmapped
        # (21,800,455 前沿形状),legacy 逐字节不变。
        self.jl_adc_start=(JLAdcStartConfigRegisters(probe_initial=jl_adc_start_probe_value)
                           if jl_adc_start_config else None)
        self.cache_status_probe=cache_status_probe
        self.cache_status_words=dict(cache_status_words or {})
        if any(address not in CACHE_STATUS_REGISTERS or not isinstance(value,int) or not 0<=value<=0xffffffff
               for address,value in self.cache_status_words.items()):raise ValueError('invalid explicit cache status fixture')
        # ICU region 组投影复用同一命令完成机制(CON bit30 + 全局步数),必须挂在
        # ICU 初始化投影之下;默认关闭,legacy 行为不变(region 写仍是不支持的外设写)。
        if icu_region_projection and not icu_initialization_projection:
            raise ValueError('ICU region projection requires the ICU initialization projection')
        self.icu=IcuInitializationProjection({a:v for a,v in self.cache_status_words.items() if a in ICU_BASES},
                                             icu_command_steps,icu_region_projection) if icu_initialization_projection else None
        # DCU CMO 口(0xff05c8/0xff05cc)是独立 write-only 记录,不依赖 ICU/CON 机制。
        self.dcu=DcuCmoProjection() if dcu_cmo_projection else None
        # JL_L1P CON(0xff0500)空闲位读回独立 fixture,只服务 0xff0500、不碰两核 ICU
        # 状态字(0xffe400/0xffec00)——与 cache_status_probe(覆盖三状态字)分离。
        self.l1p=L1PIdleProjection(l1p_idle_word) if l1p_idle_projection else None
        # 两核 ICU CON(0xffe400/0xffec00)空闲位读回独立 fixture,只服务
        # IcuWaitIdle 的 bit31 轮询、不碰 bit30 命令忙/使能位/region 寄存器——
        # 与 icu_initialization_projection(命令+使能机制,bit30)和 cache_status_probe
        # (覆盖三状态字)分离。合成字 0x80000000(bit31=1=空闲),其余位不声称硅值;
        # CON 写无观测形状仍拒绝。
        self.icu_idle=IcuIdleProjection(icu_idle_word) if icu_idle_projection else None
        self.sfc=SFCReadBus(initial_sfc_control) if sfc_read_bus else None
        if secondary_sfc_read_bus and not sfc_read_bus:raise ValueError('secondary SFC requires the primary SFC read bus')
        self.sfc_secondary=SFCReadBus(initial_secondary_sfc_control,SFC_SECONDARY_BASE) if secondary_sfc_read_bus else None
        self.sfc_clock=SfcClockGateProjection() if sfc_clock_gate_projection else None
        self.crc_engine=HardwareCrcEngine() if hardware_crc_projection else None
        self.debug=DebugExecutionProjection(initial_debug_controls) if debug_execution_projection else None
        if power_init_config_rmw and not reset_history_projection:
            raise ValueError('power_init config RMW requires the reset history projection')
        if power_init_rstcon_mask_sets and not reset_history_projection:
            raise ValueError('power_init RST_CON mask sets require the reset history projection')
        if p11_syscon1_mask_sets and not reset_history_projection:
            raise ValueError('P11_SYS_CON1 mask sets require the reset history projection')
        if power_init_wkup_dly_rmw and not reset_history_projection:
            raise ValueError('power_init wakeup-delay config RMW requires the reset history projection')
        extra_config_masks=None
        if power_init_config_rmw:
            extra_config_masks=dict(POWER_INIT_CONFIG_MASKS)
        if power_init_wkup_dly_rmw:
            extra_config_masks=dict(extra_config_masks or {})
            extra_config_masks.update(POWER_INIT_WKUP_DLY_MASK)
        extra_mask_sets={}
        if power_init_rstcon_mask_sets:extra_mask_sets.update(POWER_INIT_RSTCON_MASK_SETS)
        if p11_syscon1_mask_sets:extra_mask_sets.update(P11_SYSCON1_MASK_SETS)
        self.reset=(ResetHistoryProjection(reset_history_words,
                    extra_config_masks,
                    extra_mask_sets or None)
                    if reset_history_projection else None)
        self.mpu=MPUConfigurationProjection(initial_mpu_write_gate) if mpu_configuration_projection else None
        self.port_initialization_projection=port_initialization_projection
        self.port_input_words=dict(port_input_words or {})
        if any(a not in PORT_INPUT_REGISTERS or not isinstance(v,int) or not 0<=v<=0xffffffff
               for a,v in self.port_input_words.items()):raise ValueError('invalid port input fixture')
        if self.port_input_words and not port_initialization_projection:raise ValueError('port fixtures require explicit projection')
        self.usb_disable_writes=[]
        if initial_usb_io_word is not None:
            if not port_initialization_projection or not gpio_model:raise ValueError('USB IO fixture requires explicit port/GPIO projections')
            if not isinstance(initial_usb_io_word,int) or not 0<=initial_usb_io_word<=0xffffffff:raise ValueError('invalid USB IO fixture')
            self.gpio[USB_IO_CON0]=PartialWord(initial_usb_io_word,0xffffffff)
        self.initial_usb_io_word=initial_usb_io_word
        self.additional_configuration=set()
        for enabled,addresses in ((oscillator_configuration_projection,OSC_CONFIG),
                                  (clock_divider_projection,(SYS_DIV,)),
                                  (voltage_configuration_projection,VOLTAGE_CONFIG),
                                  (pll1_configuration_projection,(PLL1_CON3,)),
                                  (p33_analog_config_projection,P33_ANALOG_CONFIG),
                                  (port_initialization_projection,(IOMAP_CON0,)+P11_PORT_DIGITAL)):
            if enabled:self.additional_configuration.update(addresses)
        # P33 模拟控制族读回投影的显式合成初始字 0:与 VOLTAGE_CONFIG 先例相同,
        # 「显式合成初始字、非推导的模拟状态」;写后读回=最后写值,写前读回=0。
        # 直接 seed 进 registers(而非要求调用方传 initial_registers),开关关时
        # 不 seed、不进 additional_configuration,legacy 场景报告逐字节不变。
        if p33_analog_config_projection:
            for address in P33_ANALOG_CONFIG:
                self.registers[address]=0
        # P3_ANA_CON4(0xf2c250,路 BN):ADC setter case1 调 0x6067d8a 对它做 6 次
        # 读-改-写(显式 ld.w 读+st.w 写,与 P3_ANA_CON2/CON5 消费形状同族)。
        # 挂 fd31_adc_con_readback 联动 seed(路 AM 同款合成初始字 0),不并进
        # P33_ANALOG_CONFIG 常量——那会改变「p33_analog_config 开而本开关关」
        # 世界的报告形状;开关关时 0xf2c250 保持 unmapped(304,317 前沿形状)。
        if fd31_adc_con_readback:
            self.additional_configuration.add(Fd31AdcConRegisters.ANA_CON4)
            self.registers[Fd31AdcConRegisters.ANA_CON4]=0
        # 路 BV(sim-tailproj):P33 模拟配置族尾段扩展(P3_ANA_CON0/7/8/9/10 +
        # P3_CHG_CON0/CON1)按路 AM 同款「显式合成初始字 0」seed 进 registers,
        # 并入 additional_configuration(整字写/显式 ld/st RMW/复合 RMW 全走既有
        # 通用路径,机器侧零新分发)。P3_ANA_READ(0xf2c064)不 seed——它不进
        # 容器而是显式钉住(见 read()/write());不并进 P33_ANALOG_CONFIG 常量,
        # 那会改变「p33_analog_config 开而本开关关」世界的报告形状(路 BN 先例)。
        # 开关关时七个地址保持 unmapped(W1[0] 前沿形状不变),legacy 报告不变。
        if p33_analog_tail_projection:
            self.additional_configuration.update(P33_TAIL_ANALOG_CONFIG)
            for address in P33_TAIL_ANALOG_CONFIG:
                self.registers[address]=0
        # 路 CL(sim-dcck):DCCK_CON0/1/2 按路 AM 同款「显式合成初始字 0」seed
        # 进 registers、并入 additional_configuration(原子 RMW/显式 ld/st RMW/
        # 整字写全走既有通用寄存器路径,机器侧零新分发)。开关关时三字保持
        # unmapped(3,070,626 前沿形状不变),legacy 报告逐字节不变。
        if jl_clock_dcck_projection:
            self.additional_configuration.update(JL_CLOCK_DCCK)
            for address in JL_CLOCK_DCCK:
                self.registers[address]=0
        # 路 F(sim-fd2000):JL_UART0/1/2 的 CON0|CON1 整字按 DCCK 同款 seed 0 进
        # registers、并入 additional_configuration(显式 ld 读回=当前值、整字写/
        # RMW 走通用 registers 路径)。开关关时三字保持 unmapped,legacy 不变。
        if jl_uart_con_projection:
            self.additional_configuration.update(JL_UART_CON_WORDS)
            for address in JL_UART_CON_WORDS:
                self.registers[address]=0
        # 路 BM §1(S19 批 1 ①):W4[4] PVDD/trim 域容器三地址 seed(语义全文见
        # 构造器 jl_pvdd_trim_writes 注释;复合 RMW/显式 ld/st RMW 全走既有通用
        # 路径,机器侧零新分发)。开关关时不 seed、不进 additional_configuration,
        # legacy 场景报告逐字节不变。
        if jl_pvdd_trim_writes:
            self.additional_configuration.update(JL_PVDD_TRIM_CONFIG)
            for address in JL_PVDD_TRIM_CONFIG:
                self.registers[address]=0
        self.counts=Counter();self.writes=[]
        for address,value in (initial_registers or {}).items():
            if address not in (CLOCK0,CLOCK0+4,CLOCK0+8,PLL0) and address not in self.additional_configuration:
                raise ValueError('unsupported initial register')
            if not isinstance(value,int) or not 0<=value<=0xffffffff:raise ValueError('initial register must be unsigned32')
            self.registers[address]=value

    def handles(self,address,size):
        return size==4 and address%4==0 and (self.handles_gpio(address) or
            (self.software_irq is not None and self.software_irq.handles(address)) or
            (self.icu is not None and self.icu.handles(address)) or
            (self.dcu is not None and self.dcu.handles(address)) or
            (self.l1p is not None and self.l1p.handles(address)) or
            (self.icu_idle is not None and self.icu_idle.handles(address)) or
            (self.multicore_enabled and (address==IPMASK+0x800 or IRQ_CONFIG+0x800<=address<IRQ_CONFIG+0x880)) or
            (self.multicore_enabled and address in CORE_PMU_CON1) or
            (self.debug is not None and self.debug.handles(address)) or
            (self.reset is not None and self.reset.handles(address)) or
            (self.mpu is not None and self.mpu.handles(address)) or
            (self.cache_status_probe and address in CACHE_STATUS_REGISTERS) or
            (self.sfc is not None and address in SFC_PRIMARY_WINDOW_ADDRESSES) or
            (self.sfc_secondary is not None and address in SFC_SECONDARY_WINDOW_ADDRESSES) or
            (self.crc_engine is not None and self.crc_engine.handles(address)) or
            self.handles_sfc_clock(address) or
            self.handles_sfc_clock_select(address) or
            self.handles_sfc_clock_divider(address) or
            address in self.additional_configuration or
            (self.port_initialization_projection and address in PORT_INPUT_REGISTERS+USB_DISABLE_REGISTERS) or
            (self.cpu1_held_projection and address==CPU1_CONTROL) or
            self.handles_core_tick(address) or
            self.handles_gpcnt(address) or
            self.handles_gpcnt_num(address) or
            (self.oscillator_configuration_projection and address==OSC_STATUS) or
            (self.efuse is not None and address in (EFUSE_CON0,EFUSE_CON1,EFUSE_RDAT)) or
            (self.watchdog is not None and address in (WDT_CON,WDT_KEY)) or
            (self.p11 is not None and address in (P11_UART_CON2,PMU_STA)) or
            (self.p11_win is not None and (self.p11_win.handles(address) or self.p11_win.handles_release_rmw(address))) or
            (self.lp_timer is not None and (self.lp_timer.handles(address) or self.lp_timer.handles_lRCT(address)
                                            or self.lp_timer.handles_lrct_num(address))) or
            (self.mem_pwr_con is not None and self.mem_pwr_con.handles(address)) or
            (self.p33_port_sel is not None and self.p33_port_sel.handles(address)) or
            (self.jl_uart_init_records is not None and self.jl_uart_init_records.handles(address)) or
            (self.jl_omap_records is not None and self.jl_omap_records.handles(address)) or
            (self.jl_imap_records is not None and self.jl_imap_records.handles(address)) or
            (self.jl_wl_aud_records is not None and self.jl_wl_aud_records.handles(address)) or
            (self.jl_audio_dac_init_records is not None and self.jl_audio_dac_init_records.handles(address)) or
            (self.jl_audio_adc_reset_records is not None and self.jl_audio_adc_reset_records.handles(address)) or
            (self.jl_adda_daa_con1 is not None and self.jl_adda_daa_con1.handles(address)) or
            (self.jl_audio_dac_vl0 is not None and self.jl_audio_dac_vl0.handles(address)) or
            (self.p33_awkup is not None and self.p33_awkup.handles(address)) or
            (self.p3_ls is not None and self.p3_ls.handles(address)) or
            (self.fd31_adc_con is not None and (self.fd31_adc_con.handles(address)
                                                or self.fd31_adc_con.handles_res(address))) or
            (self.p33_analog_tail_projection and address==P3_ANA_READ) or
            (self.p2m_mailbox is not None and address in (P2mMailboxAck.INT_SET,P2mMailboxAck.INT_PND,
                                                           P2mMailboxAck.INT_CLR)) or
            (self.jl_adda_family is not None and self.jl_adda_family.handles(address)) or
            (self.jl_audio_dac_tm is not None and self.jl_audio_dac_tm.handles(address)) or
            (self.jl_adda_ada is not None and self.jl_adda_ada.handles(address)) or
            (self.p11_lpvad is not None and self.p11_lpvad.handles(address)) or
            (self.p33_canary is not None and self.p33_canary.handles(address)) or
            (self.timer2 is not None and (self.timer2.handles(address)
                                          or self.timer2.handles_iwkup(address))) or
            (self.jl_audio_dac_cop is not None and self.jl_audio_dac_cop.handles(address)) or
            (self.jl_audio_dac_dma is not None and self.jl_audio_dac_dma.handles(address)) or
            (self.jl_audio_adc_dma is not None and self.jl_audio_adc_dma.handles(address)) or
            (self.jl_eq_init is not None and self.jl_eq_init.handles(address)) or
            (self.jl_adc_start is not None and self.jl_adc_start.handles(address)) or
            (self.mmu is not None and MMU_BASE<=address<=MMU_BASE+8) or address in (TIMER0,TIMER0+4,TIMER0+8,PINR1,PINR0,IPMASK,
            CLOCK0,CLOCK0+4,CLOCK0+8,PLL0,PLL0+4,PLL0+8,PLL0+12,PLL0+16) or
            IRQ_CONFIG<=address<IRQ_CONFIG+128)

    def handles_gpio(self,address):
        return self.gpio_model and address%4==0 and (any(base<=address<=base+32 for base in GPIO_BASES) or
            (self.port_initialization_projection and address==USB_IO_CON0))

    def handles_core_tick(self,address):
        return self.core_ticks is not None and any(base<=address<=base+8 for base in CORE_TICK_BASES)

    def core_tick(self,address):
        index=0 if address<CORE_TICK_BASES[1] else 1
        return self.core_ticks[index],address-CORE_TICK_BASES[index]

    def handles_gpcnt(self,address):
        return self.gpcnt is not None and address==GPCNT_CON

    def handles_gpcnt_num(self,address):
        # 路 GPCNUM:NUM(0xfd1104)只在显式探路值给出时接管——默认保持
        # unmapped 严格停(29,601,125 前沿形状);RMW/整字写不经此分发,
        # 落到普通路径按 unmapped_or_readonly_write 拒绝(__RO)。
        return (self.gpcnt is not None and address==GPCNT_NUM
                and self.gpcnt.num_value is not None)

    def handles_fd31_rmw_synthesis(self,address):
        # 路 BY:PND 完成投影开启时,fd31 CON 的原子复合 RMW 走专用分发(内部
        # 读从裸最后写值合成,不观察投影 bit7);关闭时返回 False,probe_boot
        # 通用 mmio 路径行为与 legacy 逐字节一致。
        return (self.fd31_adc_con is not None and self.fd31_adc_con.pnd_completion
                and self.fd31_adc_con.handles(address))

    def gpcnt_rmw(self,address,kind,operand):
        if not self.handles_gpcnt(address):raise ValueError('not modeled GPCNT CON')
        self.gpcnt.rmw(kind,operand);self.counts['reads']+=1
        self.record_write({'address':hex(address),'operation':kind,'operand':hex(operand&0xffffffff),
                           'known_value':hex(self.gpcnt.bits.value),'known_mask':hex(self.gpcnt.bits.known)})

    def handles_sfc_clock(self,address):
        return self.sfc_clock is not None and address==SFC_CLOCK_GATE

    def handles_sfc_window_rmw(self,address):
        # 只有 +0x18 有 RMW 观测(0x600a37e |=2);+0x20/+0x24 的 RMW 无观测形状,
        # 不在此分发,落到普通路径即因窗口读回无证据而停止。
        return ((self.sfc is not None and address==SFC_BASE+0x18) or
                (self.sfc_secondary is not None and address==SFC_SECONDARY_BASE+0x18))

    def sfc_window_rmw(self,address,operand):
        if not self.handles_sfc_window_rmw(address):raise ValueError('not a modeled SFC window control')
        bus=self.sfc if address==SFC_BASE+0x18 else self.sfc_secondary
        bus.window_rmw(operand);self.counts['reads']+=1
        self.record_write({'address':hex(address),'operation':'or','operand':hex(operand&0xffffffff),
                           'known_value':hex(bus.window_control.value),'known_mask':hex(bus.window_control.known)})

    def handles_sfc_clock_divider(self,address):
        return self.sfc_clock is not None and address==SFC_CLOCK_GATE+8

    def handles_sfc_clock_select(self,address):
        return self.sfc_clock is not None and address==SFC_CLOCK_GATE+4

    def sfc_clock_rmw(self,address,kind,operand):
        if not self.handles_sfc_clock(address):raise ValueError('not the modeled SFC clock gate')
        self.sfc_clock.rmw(kind,operand);self.counts['reads']+=1
        self.record_write({'address':hex(address),'operation':kind,'operand':hex(operand&0xffffffff),
                           'known_value':hex(self.sfc_clock.bits.value),'known_mask':hex(self.sfc_clock.bits.known)})

    def core_tick_rmw(self,address,kind,operand):
        if not self.handles_core_tick(address):raise ValueError('not a modeled core tick register')
        timer,offset=self.core_tick(address)
        if offset!=0:raise Blocked('core_tick_rmw_not_modeled',address=hex(address))
        timer.ack_rmw(kind,operand);self.counts['reads']+=1
        self.record_write({'address':hex(address),'operation':kind,'operand':hex(operand&0xffffffff),
                           'pending':timer.pending,'acknowledgements':timer.acknowledgements})

    def writable_gpio(self,address):
        if not self.handles_gpio(address):raise ValueError('not a GPIO register')
        if address%256==4:raise Blocked('gpio_input_is_readonly',address=hex(address))

    def gpio_rmw(self,address,kind,operand):
        self.writable_gpio(address)
        word=self.gpio.setdefault(address,PartialWord())
        word.apply(kind,operand)
        self.counts['reads']+=1
        self.record_write({'address':hex(address),'known_value':hex(word.value),'known_mask':hex(word.known)})

    def gpio_read(self,address):
        """Read a GPIO latch for the opt-in read-modify-write path.

        A fully unknown register stays a strict stop; a partially known one
        returns a GpioOutValue whose unknown bits may only be transported by
        and/or masks before a write-back. A fully known register is a plain int.
        """
        self.counts['reads']+=1
        word=self.gpio.setdefault(address,PartialWord())
        if word.known==0:
            raise Blocked('uninitialized_gpio_bits',address=hex(address),known_mask=hex(word.known))
        if word.known==0xffffffff:
            return word.value
        return GpioOutValue(address,word.value,word.known)

    def gpio_write_partial(self,address,partial):
        """Write a read-modified partial GPIO word back without fabricating zeros.

        Known bits overwrite the modeled latch; bits the guest never made known
        stay unknown rather than being forced to a reset value.
        """
        self.writable_gpio(address)
        word=self.gpio.setdefault(address,PartialWord())
        word.value=(word.value&~partial.known)|(partial.value&partial.known)
        word.known|=partial.known
        self.record_write({'address':hex(address),'known_value':hex(word.value),'known_mask':hex(word.known)})

    def observe_dead_port_selection_read(self,address):
        if address!=0xf2c300:raise ValueError('not P3_PORT_SEL0')
        self.counts['reads']+=1
        self.counts['opaque_port_selection_reads']+=1

    def observe_dead_pwm_read(self,address):
        if address not in (0xfd2964,0xfd2974,0xfd2984,0xfd2994):raise ValueError('not the four proven PWM config reads')
        self.counts['reads']+=1;self.counts['opaque_pwm_configuration_reads']+=1

    def read(self,address):
        self.counts['reads']+=1
        if self.software_irq is not None and self.software_irq.handles(address):
            raise Blocked('software_irq_wo_readback_unverified',address=hex(address))
        if self.icu is not None and self.icu.handles(address):return self.icu.read(address)
        if self.dcu is not None and self.dcu.handles(address):return self.dcu.read(address)
        if self.multicore_enabled and address in CORE_PMU_CON1:return self.core_power[address].read(address)
        if self.multicore_enabled and address==IPMASK+0x800:
            if self.irq1.ipmask is None:raise Blocked('unknown_cpu1_ipmask')
            return self.irq1.ipmask
        if self.multicore_enabled and IRQ_CONFIG+0x800<=address<IRQ_CONFIG+0x880:
            value=self.irq1.config[(address-IRQ_CONFIG-0x800)//4]
            if value is None:raise Blocked('unknown_cpu1_irq_configuration',address=hex(address))
            return value
        if self.debug is not None and self.debug.handles(address):return self.debug.read(address)
        # P2M_INT_PND(0xF2A60C)是同一物理寄存器的两段生命周期:kick 武装的
        # 喂狗事务窗口内由 p2m_mailbox 拥有(bit12=ack pending),事务外是
        # 复位/唤醒历史快照(NO_WAKE fixture,boot 前缀的复位原因读)。
        # conditional 场景两投影同开时,reset 分派在前会把武装态轮询也吃掉
        # (fixture bit12=0 → 0x12cca2 死自旋,路 JLEQ 续五实测)——武装态
        # 必须先于 reset 分派;p2m 关闭(默认)时本分支不存在,行为不变。
        if (self.p2m_mailbox is not None and address==P2mMailboxAck.INT_PND
                and self.p2m_mailbox.pnd_armed):
            self.counts['reads']+=1
            return self.p2m_mailbox.pnd_read()
        if self.reset is not None and self.reset.handles(address):
            # 清标志后的 PWR_CON 轮询读回(真实前缀:主 init 清标志→power_init
            # 请求→死等 bit5):仅 lpwr 投影开启且已观察到真实请求时按
            # post_flag_clear_read 放行;其余读者/寄存器保持严格停。
            if (self.p11_lp is not None and address==PWR_CON and self.p11_lp.requests
                    and address in self.reset.flag_clear_commands):
                snapshot=self.reset.initial_words.get(address)
                if snapshot is None:raise Blocked('unknown_reset_or_wake_history',address=hex(address))
                self.counts['p11_lpwr_post_clear_poll_reads']+=1
                return self.p11_lp.post_flag_clear_read(address,snapshot)
            value=self.reset.read(address)
            return self.p11_lp.read(address,value) if self.p11_lp is not None else value
        if self.mpu is not None and self.mpu.handles(address):return self.mpu.read(address)
        if self.port_initialization_projection and address in PORT_INPUT_REGISTERS:
            if address not in self.port_input_words:raise Blocked('unknown_port_input',address=hex(address))
            return self.port_input_words[address]
        if self.port_initialization_projection and address in USB_DISABLE_REGISTERS:
            raise Blocked('usb_controller_status_not_modeled',address=hex(address))
        if self.l1p is not None and self.l1p.handles(address):return self.l1p.read(address)
        if self.icu_idle is not None and self.icu_idle.handles(address):return self.icu_idle.read(address)
        if self.cache_status_probe and address in CACHE_STATUS_REGISTERS:
            if address not in self.cache_status_words:raise Blocked('unknown_cache_status_input',address=hex(address))
            return self.cache_status_words[address]
        if self.sfc is not None and address in SFC_PRIMARY_WINDOW_ADDRESSES:
            return self.sfc.read(address)
        if self.sfc_secondary is not None and address in SFC_SECONDARY_WINDOW_ADDRESSES:
            return self.sfc_secondary.read(address)
        if self.crc_engine is not None and self.crc_engine.handles(address):return self.crc_engine.read(address)
        if self.handles_sfc_clock(address) or self.handles_sfc_clock_select(address) or self.handles_sfc_clock_divider(address):return self.sfc_clock.read(address)
        if self.cpu1_held_projection and address==CPU1_CONTROL:
            if self.cpu1_control is None:raise Blocked('unknown_initial_cpu1_control')
            return self.cpu1_control
        if self.handles_core_tick(address):
            timer,offset=self.core_tick(address);return timer.read(offset)
        if self.handles_gpcnt(address):return self.gpcnt.read()
        if self.handles_gpcnt_num(address):return self.gpcnt.read_num()
        if self.oscillator_configuration_projection and address==OSC_STATUS:
            if self.oscillator_status_word is None:raise Blocked('unknown_oscillator_status_input')
            return self.oscillator_status_word
        if self.efuse is not None and address in (EFUSE_CON0,EFUSE_CON1,EFUSE_RDAT):return self.efuse.read(address)
        if self.watchdog is not None and address in (WDT_CON,WDT_KEY):return self.watchdog.read(address)
        if self.p11 is not None and address in (P11_UART_CON2,PMU_STA):
            if address!=PMU_STA:raise Blocked('unmodeled_p11_register_read',address=hex(address))
            return self.p11.read()
        if self.p11_win is not None and (self.p11_win.handles(address) or self.p11_win.handles_release_rmw(address)):
            return self.p11_win.read(address)
        if self.lp_timer is not None and (self.lp_timer.handles(address) or self.lp_timer.handles_lRCT(address)
                                          or self.lp_timer.handles_lrct_num(address)):
            return self.lp_timer.read(address)
        if self.mem_pwr_con is not None and self.mem_pwr_con.handles(address):
            return self.mem_pwr_con.read(address)
        # P33 窗无名寄存器族:固件访问全是字节写(反汇编钉死,零读回观测),
        # 读回一律严格停——显式 kind 优先于泛 unmapped,便于诊断。
        if self.p33_window is not None and self.p33_window.handles(address):
            self.counts['reads']+=1
            return self.p33_window.read(address)
        # P3_PORT_SEL0..11:整字写 write-only 记录,读回一律严格停(唯一观测
        # 读是 arch_startup 的 opaque 死读,由 probe 侧死读门放行、不经这里)。
        if self.p33_port_sel is not None and self.p33_port_sel.handles(address):
            self.counts['reads']+=1
            return self.p33_port_sel.read(address)
        # JL_UART 初始化配置字(路 M):整字写 write-only 记录,读回一律严格停
        # (白名单字零读回观测;字 0 CON0 的读走路 F 投影,不经这里)。路 AA
        # 例外:opt-in con1_readback 时 CON1 读回=最后写值(只在写过之后,从未
        # 写过仍诚实停);其余字读回保持严格停。
        if self.jl_uart_init_records is not None and self.jl_uart_init_records.handles(address):
            self.counts['reads']+=1
            return self.jl_uart_init_records.read(address)
        # JL_OMAP 逐引脚输出功能选择字(路 R):整字写 write-only 记录,读回
        # 一律严格停(本 boot 前缀零读回观测;SDK disable 路径的 load+store
        # 不在本前缀,出现即在读侧显形)。
        if self.jl_omap_records is not None and self.jl_omap_records.handles(address):
            self.counts['reads']+=1
            return self.jl_omap_records.read(address)
        # JL_IMAP 外设输入功能选择字(路 V):整字写 write-only 记录,读回一律
        # 严格停(全 app/ROM 镜像访问面扫描零读回;出现即在读侧显形)。
        if self.jl_imap_records is not None and self.jl_imap_records.handles(address):
            self.counts['reads']+=1
            return self.jl_imap_records.read(address)
        # JL_WL_AUD CON0(路 AK):读回=最后写值,无写时显式合成 0(路 AM 先例,
        # derived-not-measured);本路径 0xfe2200 在任何读前必已写过,唯一 app
        # 读点=低功耗快照 0x6069a2e 不在本前缀——无轮询风险。
        if self.jl_wl_aud_records is not None and self.jl_wl_aud_records.handles(address):
            self.counts['reads']+=1
            return self.jl_wl_aud_records.read(address)
        # 音频 DAC init 写域(路 AK):JL_AUDIO DAC_CON/AUD_CON + JL_ASS CLK_CON
        # 读回=最后写值,无写时显式合成 0(消费点=CLK_CON|1/AUD_CON&~0x40 的
        # RMW 内部读,rmw 经 probe_boot 通用 mmio 路径真实走读侧)。
        if self.jl_audio_dac_init_records is not None and self.jl_audio_dac_init_records.handles(address):
            self.counts['reads']+=1
            return self.jl_audio_dac_init_records.read(address)
        # ADC 节点 reset 写域(路 AS):ADC_CON 读回=最后写值,无写时显式合成 0
        # (路 AM 先例);本前缀零读(AE 消费者全在 IRQ68/esco 链上)——bit7
        # pending 恒 0=无 ADC 硬件活动的诚实显形,不发明完成。
        # 路 D2AI:交付投影开启时 bit7 叠加模型 pending(硬件域位:存储值一律
        # 掩掉,读值=软件值|pending 位)——分发器 0x602f430 的 pending 门由此
        # 读到真实投影(PRE §4.4)。
        if self.jl_audio_adc_reset_records is not None and self.jl_audio_adc_reset_records.handles(address):
            self.counts['reads']+=1
            value=self.jl_audio_adc_reset_records.read(address)
            dma=self.jl_audio_adc_dma
            if dma is not None and dma.delivery:
                value=(value&~0x80)|(0x80 if dma.pending else 0)
            return value
        # JL_ADDA DAA_CON1(路 CV):两开关分立——写前读=显式 fixture(无隐式
        # 零默认);写后读=配置位读回(最后写值,需读回开关)。只开读回无初值
        # 未写入、或初值开读回关写后读:停原边界(unmapped_memory 读
        # 0xfe0a04,与 3,170,362 停点逐字段一致——「首次读取时的未知状态」
        # 不可推导,不发明)。
        if self.jl_adda_daa_con1 is not None and self.jl_adda_daa_con1.handles(address):
            self.counts['reads']+=1
            return self.jl_adda_daa_con1.read(address)
        # JL_AUDIO DAC_VL0(路 CW/S19 批 0b):读回=最后写值,无写时显式合成初始
        # 字 0(路 AM 先例;读值只进 insert 不进分支、双通道调用下写回恒 0x0=
        # 初值不参与观察路径,敏感性分叉 ones 字互证——类注释全文)。
        if self.jl_audio_dac_vl0 is not None and self.jl_audio_dac_vl0.handles(address):
            self.counts['reads']+=1
            return self.jl_audio_dac_vl0.read(address)
        # P3_AWKUP 族:只有显式 fixture 覆盖的寄存器的首次读返回具名字,
        # 其余读(未覆盖/写后/重复)一律严格停(路 BB;显式 kind 便于诊断)。
        if self.p33_awkup is not None and self.p33_awkup.handles(address):
            self.counts['reads']+=1
            return self.p33_awkup.read(address)
        # P3_LS 族:整字写 write-only 记录,读回一律严格停(全 app/ROM 零
        # 读回观测;显式 kind 优先于泛 unmapped,便于诊断)。
        if self.p3_ls is not None and self.p3_ls.handles(address):
            self.counts['reads']+=1
            return self.p3_ls.read(address)
        # fd31 ADC CON(路 BG):整字写 write-only 记录,读回是真实消费(bit4
        # 门控/bit7 忙等/RES 模拟值无可推导来源),一律严格停(显式 kind 优先
        # 于泛 unmapped,便于诊断)。路 BN:opt-in 读回投影开启时 CON 返回最后
        # 写值(lp_timer 同族),RES(0xfd3104)无论投影开关一律显式严格停
        # fd31_adc_res_not_modeled(模拟测量值无可推导来源)。
        if self.fd31_adc_con is not None and (self.fd31_adc_con.handles(address)
                                              or self.fd31_adc_con.handles_res(address)):
            self.counts['reads']+=1
            # RES 具名 fixture 需要 RES 读时刻的 P3_ANA_CON4 模拟复用字来识别
            # 通道(联动 seed 只在 con_readback 开启时存在);CON 读忽略该参。
            return self.fd31_adc_con.read(address,
                                          ana_con4=self.registers.get(Fd31AdcConRegisters.ANA_CON4))
        # P3_ANA_READ(0xf2c064,路 BV/CB):W1[0] 两个轮询(循环1 首读 bit2 定
        # 1/2 轮 delay;循环2 20 轮 bit1|bit2 充电检测,成功创建充电去抖任务
        # 0x6033F36/超时写失败码 b[0x125BF4]=2)与三个 getter/handler 读者的
        # 真实读回消费。默认:模拟量无可推导来源——显式钉住(RES 红线同款;
        # 显式 kind 优先于泛 unmapped,便于诊断)。opt-in
        # p3_ana_read_ldoin_low_fixture(路 CB):「无外部供电(ldoin≈0V,电池
        # 启动)」场景锚下常量服务 0(bit1=LVCMP/bit2=LDO5V 按 charge_hw.h+
        # charge.c 阈值推导为 0;其余位显式填零非推导)——**常量 live input**
        # (轮询 21+ 次重复读都服务同一字,非首读限制;不建模插拔/去抖时序)。
        # 其余十个容器地址走下面的通用 registers 路径(AM 语义:合成初始字
        # 0/最后写值)。
        if self.p33_analog_tail_projection and address==P3_ANA_READ:
            if self.p3_ana_read_ldoin_low_fixture:
                self.counts['reads']+=1
                self.p3_ana_read_served+=1
                return 0
            raise Blocked('p3_ana_read_not_modeled',address=hex(address))
        # P2M_INT_PND(0xF2A60C,BM §3):kick 武装后的 ack pending 读回
        # (bit12);未武装读=Blocked(世界错即停,不兜底自旋)。
        if self.p2m_mailbox is not None and address==P2mMailboxAck.INT_PND:
            self.counts['reads']+=1
            return self.p2m_mailbox.pnd_read()
        # JL_ADDA 模拟族五字(路 B3):读回=当前值(最后写值/无写时合成初始 0)。
        if self.jl_adda_family is not None and self.jl_adda_family.handles(address):
            self.counts['reads']+=1
            return self.jl_adda_family.read(address)
        # JL_AUDIO DAC_TM0/TM1(路 DT):读回=当前值(最后写值/无写时合成初始 0,
        # 初值来源 synthetic_zero 具名登记);只服务对齐 32 位字,其余形状落
        # 严格 unmapped。
        if self.jl_audio_dac_tm is not None and self.jl_audio_dac_tm.handles(address):
            self.counts['reads']+=1
            return self.jl_audio_dac_tm.read(address)
        # JL_ADDA ADA 子族九字(路 ADA):读回=当前值(最后写值/无写时合成初始 0);
        # 只服务对齐 32 位字,子字/错位落严格 unmapped;读与原子 RMW 内部读共享
        # 同一份容器状态(不重置零)。
        if self.jl_adda_ada is not None and self.jl_adda_ada.handles(address):
            self.counts['reads']+=1
            return self.jl_adda_ada.read(address)
        # P11_SFR LPVAD VAD_ACON0(路 LPV):读回=当前值(最后写值/无写时合成
        # 初始 0);只服务对齐 32 位字,子字/错位落严格 unmapped;读与原子 RMW
        # 内部读共享同一份容器状态。
        if self.p11_lpvad is not None and self.p11_lpvad.handles(address):
            self.counts['reads']+=1
            return self.p11_lpvad.read(address)
        # 金丝雀树/低功耗域(路 BV):P3_VLVD_CON/P3_PCNT_CON 只有显式 fixture
        # 覆盖的首次读返回具名字,其余读(未覆盖/写后/重复)一律严格停
        # (路 BB 同款;显式 kind 便于诊断)。
        if self.p33_canary is not None and self.p33_canary.handles(address):
            self.counts['reads']+=1
            return self.p33_canary.read(address)
        # JL_TIMER2(路 BK/S19 批 1):CON/CNT/PRD 读回=存储值|pending bit15
        # (Timer0.read 同式);IWKUP_NUM(0xFFE1B0)write-only,读回一律严格停
        # (显式 kind 优先于泛 unmapped,便于诊断)。
        if self.timer2 is not None and self.timer2.handles(address):
            self.counts['reads']+=1
            return self.timer2.read(address)
        if self.timer2 is not None and self.timer2.handles_iwkup(address):
            raise Blocked('iwkup_num_readback_not_modeled',address=hex(address))
        # JL_AUDIO DAC_COP(路 BM/S19 批 1 ④):write-only 记录,读回显式严格停。
        if self.jl_audio_dac_cop is not None and self.jl_audio_dac_cop.handles(address):
            self.counts['reads']+=1
            return self.jl_audio_dac_cop.read(address)
        # JL_AUDIO DAC DMA 四字(路 BM/S19 批 1 ④续):write-only 记录,读回
        # 显式严格停(D2 场景的 DMA 指针/计数读属 tone/PCM 推送域,D1 后)。
        if self.jl_audio_dac_dma is not None and self.jl_audio_dac_dma.handles(address):
            self.counts['reads']+=1
            return self.jl_audio_dac_dma.read(address)
        # JL_AUDIO ADC DMA 五字(路 JLA):write-only 记录,读回显式严格停
        # (audio_adc_irq_handler 的 ADR/PNS/SRP 读属 D2-b 完成投影后域)。
        # 路 D2AI:交付投影开启时读回由容器服务(五字+SRN,见类注释);
        # SRN 0xfe0550 只在此模式进白名单。
        if self.jl_audio_adc_dma is not None and self.jl_audio_adc_dma.handles(address):
            self.counts['reads']+=1
            return self.jl_audio_adc_dma.read(address)
        # JL_EQ 初始化两字(路 JLEQ):write-only 记录,读回显式严格停
        # (EQ/音频 handler 域 0x6072bb2 copex 族的读面属后域;当前前缀零读)。
        if self.jl_eq_init is not None and self.jl_eq_init.handles(address):
            self.counts['reads']+=1
            return self.jl_eq_init.read(address)
        # ADC 启动配置块四字(路 ASS):合成配置读回(读回=当前值/显式合成
        # 初始 0,AM 容器族);停点 RMW 内部读与 insert 族显式 ld 均经此路径。
        if self.jl_adc_start is not None and self.jl_adc_start.handles(address):
            self.counts['reads']+=1
            return self.jl_adc_start.read(address)
        if self.mmu is not None and MMU_BASE<=address<=MMU_BASE+8:return self.mmu.read(address)
        if self.handles_gpio(address):return self.gpio.setdefault(address,PartialWord()).read(address)
        if TIMER0<=address<=TIMER0+8:return self.timer.read(address-TIMER0)
        if IRQ_CONFIG<=address<IRQ_CONFIG+128:value=self.irq.config[(address-IRQ_CONFIG)//4]
        elif address==IPMASK:value=self.irq.ipmask
        else:value=self.registers.get(address)
        if value is None:raise Blocked('uninitialized_peripheral',address=hex(address))
        return value

    def write(self,address,value):
        if not isinstance(value,int) or not 0<=value<=0xffffffff:raise ValueError('MMIO value must be unsigned32')
        if not self.handles(address,4):raise Blocked('unsupported_peripheral_write',address=hex(address))
        if self.software_irq is not None and self.software_irq.handles(address):
            raise Blocked('software_irq_full_store_contract_unmodeled',address=hex(address))
        if self.icu is not None and self.icu.handles(address):
            self.icu.write(address,value);self.record_write({'address':hex(address),'value':hex(value)})
            return
        if self.dcu is not None and self.dcu.handles(address):
            self.dcu.write(address,value);self.record_write({'address':hex(address),'value':hex(value)})
            return
        if self.multicore_enabled and address in CORE_PMU_CON1:raise Blocked('core_power_full_write_not_modeled',address=hex(address))
        if self.debug is not None and self.debug.handles(address):
            self.debug.write(address,value);self.record_write({'address':hex(address),'value':hex(value)})
            return
        # P2M_INT_CLR(0xF2A608)同 P2M_INT_PND 的双声明裁定:武装事务窗口内
        # CLR=0x1000 是喂狗清 ack(进 mailbox 投影,闭合事务);事务外的写保持
        # reset 投影的 wake-clear 语义不变。CLR 只清 live pending 位,复位快照
        # 的 bit12 本就是 0(NO_WAKE fixture),不需要回写快照。
        if (self.p2m_mailbox is not None and address==P2mMailboxAck.INT_CLR
                and self.p2m_mailbox.pnd_armed):
            # 路 WDFEED:喂狗事务闭合(这笔事务的回声已读且匹配 kick 时刻
            # 锁定的消息字节,现被 CLR 收尾)时,看门狗投影计一次 P11 侧
            # feed(计数器重装)。授权条件(codex 参谋定形):①开关显式
            # opt-in;②恰有一笔新闭合事务(武装态 CLR 分发已保证在途事务
            # 存在;事务外 CLR 走 reset 投影语义不触发);③该事务回声字节
            # 非空且==kick 时刻锁定的 arg(arg 在 kick 时快照,不受后续
            # 消息写入串账;回声不满足=该事务不满足本投影的授权条件,
            # 不授予 feed 副作用——不是「硬件没喂」的证明);④arg==8
            # (喂狗函数 0x12cc7e 的观测消息,本前缀 0x400 kick 的唯一值;
            # 其它消息的 P11 语义未知不喂)。每笔事务只提交一次(current
            # 在 CLR 后清空,重复 CLR 时 pnd_armed 已 False 不再进本分支)。
            # 副作用时序=事务闭合时刻=本投影选定的记账时刻(假设,见
            # WatchdogProjection.mailbox_feed 注释)。
            closed=len(self.p2m_mailbox.transactions)
            self.p2m_mailbox.clear_ack(value)
            if (self.wdt_mailbox_feed_side_effect
                    and len(self.p2m_mailbox.transactions)==closed+1):
                t=self.p2m_mailbox.transactions[-1]
                if (t['echo_byte'] is not None and t['echo_byte']==t['arg']
                        and t['arg']==P2mMailboxAck.FEED_MESSAGE):
                    self.watchdog.mailbox_feed()
            self.record_write({'address':hex(address),'value':hex(value)})
            return
        if self.reset is not None and self.reset.handles(address):
            self.reset.write(address,value);self.record_write({'address':hex(address),'value':hex(value)})
            return
        if self.mpu is not None and self.mpu.handles(address):
            self.mpu.write(address,value);self.record_write({'address':hex(address),'value':hex(value)})
            return
        if self.port_initialization_projection and address in PORT_INPUT_REGISTERS:
            raise Blocked('port_input_snapshot_write_not_modeled',address=hex(address))
        if self.port_initialization_projection and address in USB_DISABLE_REGISTERS:
            if value!=0:raise Blocked('usb_controller_enable_or_configuration_not_modeled',address=hex(address),value=hex(value))
            self.usb_disable_writes.append(address)
            self.record_write({'address':hex(address),'value':hex(value),'projection':'port initialization disable only'})
            return
        if self.l1p is not None and self.l1p.handles(address):self.l1p.write(address,value)
        if self.icu_idle is not None and self.icu_idle.handles(address):self.icu_idle.write(address,value)
        if self.cache_status_probe and address in CACHE_STATUS_REGISTERS:raise Blocked('cache_control_write_not_modeled',address=hex(address))
        if self.handles_gpcnt(address) and not (self.gpcnt is not None and self.gpcnt.config_words):
            raise Blocked('gpcnt_full_control_store_not_modeled')
        if self.oscillator_configuration_projection and address==OSC_STATUS:raise Blocked('oscillator_status_is_readonly')
        if self.sfc is not None and address in SFC_PRIMARY_WINDOW_ADDRESSES:
            self.sfc.write(address,value)
        elif self.sfc_secondary is not None and address in SFC_SECONDARY_WINDOW_ADDRESSES:
            self.sfc_secondary.write(address,value)
        elif self.crc_engine is not None and self.crc_engine.handles(address):self.crc_engine.write(address,value)
        elif self.handles_sfc_clock(address):raise Blocked('sfc_clock_gate_full_store_not_modeled',address=hex(address))
        elif self.handles_sfc_clock_select(address):self.sfc_clock.write_select(value)
        elif self.handles_sfc_clock_divider(address):self.sfc_clock.write_divider(value)
        elif self.cpu1_held_projection and address==CPU1_CONTROL:
            if value&1:
                if self.cpu1_release_handler is None:raise Blocked('cpu1_release_not_implemented',value=hex(value))
                if self.cpu1_control!=0:raise Blocked('unsupported_cpu1_release_transition')
                self.cpu1_release_handler(value);self.cpu1_control=value
            else:
                if value!=0:raise Blocked('cpu1_control_write_not_modeled',value=hex(value))
                # bit0 是释放位:kick 只写记录(值可达 9),不影响「已释放」判定
                if self.multicore_enabled and self.cpu1_control&1:raise Blocked('cpu1_hold_after_release_not_implemented')
                self.cpu1_control=0
        elif self.handles_core_tick(address):
            timer,offset=self.core_tick(address)
            if address==CORE_TICK_BASES[1] and value&1:
                if not self.cpu1_held_projection or self.cpu1_control is None:raise Blocked('core1_execution_state_unknown')
                if self.cpu1_control==0 and self.core_tick_count_while_held is None:raise Blocked('core1_tick_held_clock_unknown')
            was_running=timer.con is not None and bool(timer.con&1)
            timer.write(offset,value)
            self.core_tick_irq3_probe_on_start(address,value,was_running)
        elif self.efuse is not None and address in (EFUSE_CON0,EFUSE_CON1,EFUSE_RDAT):self.efuse.write(address,value)
        elif self.watchdog is not None and address in (WDT_CON,WDT_KEY):self.watchdog.write(address,value)
        elif self.p11 is not None and address in (P11_UART_CON2,PMU_STA):
            if address!=P11_UART_CON2:raise Blocked('pmu_status_is_not_a_configuration_latch')
            self.p11.write(value)
        elif self.p11_win is not None and self.p11_win.handles(address):
            self.p11_win.write(address,value)
        elif self.p11_win is not None and self.p11_win.handles_release_rmw(address):
            # 0xf2a0c0 全 app+ROM 只有 |=1 一种写形状;整字 store 无观测形状,拒绝
            raise Blocked('p11_window_release_full_store_not_modeled',address=hex(address),value=hex(value))
        elif self.lp_timer is not None and self.lp_timer.handles(address):
            self.lp_timer.write(address,value)
        elif self.lp_timer is not None and self.lp_timer.handles_lRCT(address):
            # 0xfd3600 只有位 RMW 观测;整字 store 无观测形状,拒绝
            raise Blocked('lp_timer_full_store_not_modeled',address=hex(address),value=hex(value))
        elif self.lp_timer is not None and self.lp_timer.handles_lrct_num(address):
            # NUM(0xfd3604,trim 结果计数,SDK __RW):固件唯一访问是轮询退出后
            # 0x6067520 的读;写形状无任何观测,拒绝
            raise Blocked('lrct_trim_result_write_not_modeled',address=hex(address),value=hex(value))
        elif self.mem_pwr_con is not None and self.mem_pwr_con.handles(address):
            # 0xf2a62c 只有 OR/AND 位 RMW 观测,无整字 store;整字写拒绝
            raise Blocked('mem_pwr_con_full_store_not_modeled',address=hex(address),value=hex(value))
        elif self.p33_port_sel is not None and self.p33_port_sel.handles(address):
            # P3_PORT_SEL0..11:st.w.idxs 全字覆盖写(旧值无关),write-only 记录
            self.p33_port_sel.write(address,value)
        elif self.jl_uart_init_records is not None and self.jl_uart_init_records.handles(address):
            # JL_UART 初始化配置字(路 M):st.w 族全字覆盖写(旧值无关),
            # write-only 记录;值=真实指令结果,运行效果不建模。
            self.jl_uart_init_records.write(address,value)
        elif self.jl_omap_records is not None and self.jl_omap_records.handles(address):
            # JL_OMAP 逐引脚输出功能选择字(路 R):st.w 族全字覆盖写(旧值
            # 无关),write-only 记录;值=真实指令结果(FO_* 常量由真实指令
            # 装载),交叉开关布线/输出电平/IRQ 一律不建模。
            self.jl_omap_records.write(address,value,self.current_core)
        elif self.jl_imap_records is not None and self.jl_imap_records.handles(address):
            # JL_IMAP 外设输入功能选择字(路 V):st.w 族全字覆盖写(旧值
            # 无关),write-only 记录;值=真实指令结果(resolver 真实计算通道
            # 号/PFI_* 常量由 case 体真实装载),交叉开关布线/输入电平/IRQ
            # 一律不建模。
            self.jl_imap_records.write(address,value,self.current_core)
        elif self.jl_wl_aud_records is not None and self.jl_wl_aud_records.handles(address):
            # JL_WL_AUD CON0(路 AK):st.w 族全字覆盖写(entry 0x602ea72 与
            # DAC init 尾 0x6071cbe 各一次),写记录;时钟门副作用/无线音频
            # 活动一律不建模。
            self.jl_wl_aud_records.write(address,value,self.current_core)
        elif self.jl_audio_dac_init_records is not None and self.jl_audio_dac_init_records.handles(address):
            # 音频 DAC init 写域(路 AK):整字写(DAC_CON 0xfe2200)与原子
            # RMW 写回(经 probe_boot 通用 mmio 路径的内部读=投影值)都落
            # 记录;DAC 时钟门/模拟通路/DMA/IRQ68 事件一律不建模。
            self.jl_audio_dac_init_records.write(address,value,self.current_core)
        elif self.jl_audio_adc_reset_records is not None and self.jl_audio_adc_reset_records.handles(address):
            # ADC 节点 reset 写域(路 AS):0x60719c0 并行对第二半以旧 r3 执行
            # 的 [ADC_CON]=0 整字写(全字覆盖、旧值无关,旧寄存器形状由机器
            # 侧 exact_parallel 机制保真),写记录;ADC 时钟/模拟前端/DMA/
            # IRQ68 事件一律不建模。
            self.jl_audio_adc_reset_records.write(address,value,self.current_core)
            # 路 D2AI:交付投影开启时联动 W1C——|=0x40(bit6)写清模型 pending
            # (分发器 0x602f434 与 handler 尾 0x60726ec 两处真实清位;两次无害)。
            if self.jl_audio_adc_dma is not None and self.jl_audio_adc_dma.delivery:
                self.jl_audio_adc_dma.note_con_write(value)
        elif self.jl_adda_daa_con1 is not None and self.jl_adda_daa_con1.handles(address):
            # JL_ADDA DAA_CON1(路 CV):0x606b43c 写回(在两个通道选择块之外,
            # 未选通道时旧值原样回写、总线写仍发生)整字写记录;值=真实指令
            # 结果;DAC 模拟通路/偏置/音量运行效果不建模。
            self.jl_adda_daa_con1.write(address,value,self.current_core)
        elif self.jl_adda_family is not None and self.jl_adda_family.handles(address):
            # JL_ADDA 模拟族五字(路 B3/S19 批 2 续):模拟 init 函数 0x606abf0
            # 的 RMW 写回/整字写记录(经通用 mmio 路径的内部读=容器当前值);
            # 模拟通路/偏置/音频输出不建模。
            self.jl_adda_family.write(address,value)
        elif self.p2m_mailbox is not None and address==P2mMailboxAck.INT_SET:
            # M2P_INT_SET kick(BM §3):恰 0x400 武装,其他 kick 值只记录。
            self.p2m_mailbox.kick(value)
            self.record_write({'address':hex(address),'value':hex(value),
                               'projection':'P11 mailbox M2P_INT_SET kick record'})
        elif self.p2m_mailbox is not None and address==P2mMailboxAck.INT_CLR:
            # P2M_INT_CLR(BM §3):恰 0x1000 清 ack 位并闭合事务。
            self.p2m_mailbox.clear_ack(value)
            self.record_write({'address':hex(address),'value':hex(value),
                               'projection':'P11 mailbox P2M_INT_CLR ack clear'})
        elif self.jl_audio_dac_tm is not None and self.jl_audio_dac_tm.handles(address):
            # JL_AUDIO DAC_TM0/TM1(路 DT/S19 批 2 续二):setter 0x606ab92 的
            # RMW 写回整字记录(经通用 mmio 路径的内部读=容器当前值);DAC
            # time-mark/DMA 运行效果不建模。
            self.jl_audio_dac_tm.write(address,value)
        elif self.jl_adda_ada is not None and self.jl_adda_ada.handles(address):
            # JL_ADDA ADA 子族九字(路 ADA/S19 冲刺批 2 续三):ADA 配置函数
            # 0x606b4d0/close 族/field setter 的 RMW 写回与整字写记录(经通用
            # mmio 路径的内部读=容器当前值);ADC 模拟通路/偏置/音频输入不建模。
            self.jl_adda_ada.write(address,value)
        elif self.p11_lpvad is not None and self.p11_lpvad.handles(address):
            # P11_SFR LPVAD VAD_ACON0(路 LPV):停点 0x606b68e 的 bit28 置位
            # RMW 写回与整字写记录(经通用 mmio 路径的内部读=容器当前值);
            # q32s 侧访问/LPVAD 检测/跨域副作用不建模(codex 跨域措辞)。
            self.p11_lpvad.write(address,value)
        elif self.jl_audio_dac_vl0 is not None and self.jl_audio_dac_vl0.handles(address):
            # JL_AUDIO DAC_VL0(路 CW/S19 批 0b):0x606db68 写回(在两个通道
            # 选择块之外,未选通道=组装值原样写回)整字写记录;值=真实指令
            # 结果(umin 钳制+半字 insert 的组装字);数字音量运行效果不建模。
            self.jl_audio_dac_vl0.write(address,value,self.current_core)
        elif self.p33_awkup is not None and self.p33_awkup.handles(address):
            # P3_AWKUP 族(路 BB):整字写只接受有观测形状的 FLT0..2(walker
            # st.w.idxs)与 P_IE(0x6067cb0 消费型写回),其余(EN/N_IE/
            # P_CPND/N_CPND 无整字写观测)由投影拒绝;写后读严格停。
            self.p33_awkup.write(address,value)
        elif self.p3_ls is not None and self.p3_ls.handles(address):
            # P3_LS 族(路 BD):st.w/并行对 st.w 半全字覆盖写(旧值无关,
            # 含 run3 旧值方向 pair),write-only 记录。
            self.p3_ls.write(address,value)
        elif self.fd31_adc_con is not None and self.fd31_adc_con.handles_res(address):
            # RES(0xfd3104,SDK __RO):固件零写观测、转换结果是模拟量——任何
            # 写形状一律显式拒绝(kind 优先于泛 unmapped_or_readonly_write)。
            raise Blocked('fd31_adc_res_write_not_modeled',address=hex(address),value=hex(value))
        elif self.fd31_adc_con is not None and self.fd31_adc_con.handles(address):
            # fd31 ADC CON(路 BG):st.w.modimm/st.w 全字覆盖写(旧值无关),
            # write-only 记录;值序列 0x20000/0x20040/0x40 全真实指令计算。
            self.fd31_adc_con.write(address,value)
        elif self.p33_analog_tail_projection and address==P3_ANA_READ:
            # P3_ANA_READ(0xf2c064,路 BV):固件零写观测(全部 5 处 li 都是读
            # 基址),模拟量读值——任何写形状一律显式拒绝(RES 写拒绝同款,
            # kind 优先于泛路径)。
            raise Blocked('p3_ana_read_write_not_modeled',address=hex(address),value=hex(value))
        elif self.p33_canary is not None and self.p33_canary.handles(address):
            # 金丝雀树/低功耗域(路 BV):DVFS st.w.modimm 关断与消费型写回
            # st.w 的整字记录(P_IE 写回先例同款);其余形状由投影拒绝。
            self.p33_canary.write(address,value)
        elif self.timer2 is not None and self.timer2.handles(address):
            # JL_TIMER2(路 BK/S19 批 1):CON/CNT/PRD 整字写(0x4000/1/200/
            # 0xC01 启动/0xCC01 W1C/0 停表,全 26 指令钉死),字段位图校验在
            # Timer2Delay.write;退出序列零干预(W1C/停表/pop 全真实执行)。
            self.timer2.write(address,value,self.source_ticks_per_step)
        elif self.timer2 is not None and self.timer2.handles_iwkup(address):
            # IWKUP_NUM(0xFFE1B0,[(cnum<<11)+base]=6):write-only 位记录,
            # 无读回、无行为效果(硅 idle 唤醒选择器语义未测量,路 BK §1.2)。
            self.timer2.write_iwkup(address,value,self.current_core)
        elif self.jl_audio_dac_cop is not None and self.jl_audio_dac_cop.handles(address):
            # JL_AUDIO DAC_COP(路 BM/S19 批 1 ④):st.w.modimm 0x7F 整字写
            # 记录(全字覆盖、旧值无关);chopper/泵电路效果不建模。
            self.jl_audio_dac_cop.write(address,value,self.current_core)
        elif self.jl_audio_dac_dma is not None and self.jl_audio_dac_dma.handles(address):
            # JL_AUDIO DAC DMA 四字(路 BM/S19 批 1 ④续):0x6071e92..ea8 四条
            # 整字写(LEN/ADR/HRP/PNS),write-only 记录;DMA 传输不建模。
            self.jl_audio_dac_dma.write(address,value,self.current_core)
        elif self.jl_audio_adc_dma is not None and self.jl_audio_adc_dma.handles(address):
            # JL_AUDIO ADC DMA 五字(路 JLA):set_buffer 0x606dee4 的五条
            # 连续整字写(ADR/LEN/PNS/HWP/SRP,首写=停点并行对 store 半),
            # write-only 记录;DMA 搬运/指针推进/IRQ68 不建模。
            self.jl_audio_adc_dma.write(address,value,self.current_core)
        elif self.jl_eq_init is not None and self.jl_eq_init.handles(address):
            # JL_EQ 初始化两字(路 JLEQ):EQ 初始化函数 0x6072b24 的 CON0=0
            # (0x6072b92)与 FLT_ADR=分配缓冲指针(0x6072bae)两条整字写,
            # write-only 记录;均衡器处理/DMA/滤波系数加载不建模。
            self.jl_eq_init.write(address,value,self.current_core)
        elif self.gpcnt is not None and self.gpcnt.config_words and address==GPCNT_CON:
            # GPCNT 配置字整字写(路 JLEQ 续五):W4[4] VLVD 域重配置链的
            # st.w 写回(全字覆盖、全字已知);计数语义不建模。默认关时
            # GPCNT_CON 整字写落通用 registers 路径(legacy 形状不变)。
            self.gpcnt.write(value)
        elif self.handles_gpcnt_num(address):
            # NUM(__RO)只读(路 GPCNUM):fixture 开启时显式拒绝整字写/
            # RMW 写路径,不经通用 registers 槽(本前缀零写者,合同面诚实)。
            raise Blocked('gpcnt_num_is_readonly',address=hex(address))
        elif self.jl_adc_start is not None and self.jl_adc_start.handles(address):
            # ADC 启动配置块四字(路 ASS):MIC_CTL0 RMW 写回/MIC_CTL1·
            # MBG_CON·ADC_CON1 insert 族写回与整字写;配置生效/音频输入
            # 不建模。
            self.jl_adc_start.write(address,value,self.current_core)
        elif self.mmu is not None and MMU_BASE<=address<=MMU_BASE+8:self.mmu.write(address,value)
        elif self.handles_gpio(address):
            self.writable_gpio(address);self.gpio[address]=PartialWord(value,0xffffffff)
        elif TIMER0<=address<=TIMER0+8:
            was_running=self.timer.con is not None and bool(self.timer.con&3)
            self.timer.write(address-TIMER0,value)
            self.timer0_irq4_probe_on_start(address,value,was_running)
        elif address==PINR1:
            if not self.reset_inputs_inactive:
                raise Blocked('unmodeled_pin_reset_input',address=hex(address),value=hex(value))
            if value&~0x7f or value>>4>4:raise Blocked('unsupported_pin_reset_configuration',value=hex(value))
            self.registers[address]=value
        elif address==PINR0:
            if value!=0:raise Blocked('pin0_reset_enable_not_modeled',value=hex(value))
            self.registers[address]=0  # SDK gpio_longpress_pin0_reset_config(time=0)
        elif self.multicore_enabled and address==IPMASK+0x800:
            if not 0<=value<=7:raise Blocked('unsupported_cpu1_ipmask',value=value)
            self.irq1.ipmask=value
        elif self.multicore_enabled and IRQ_CONFIG+0x800<=address<IRQ_CONFIG+0x880:
            self.irq1.config[(address-IRQ_CONFIG-0x800)//4]=value
        elif address==IPMASK:
            if not 0<=value<=7:raise Blocked('unsupported_ipmask',value=value)
            self.irq.ipmask=value
        elif address==IRQ_IPMASK_BANKED:
            # Same priority mask as IPMASK: the idle loop (0x12b56c) and the
            # IRQ127 handler (0x12ad5c/0x12adda) both address 0xFFE168+cnum*0x800.
            if not 0<=value<=7:raise Blocked('unsupported_ipmask',value=value)
            self.irq.ipmask=value
        elif self.multicore_enabled and address==IRQ_IPMASK_BANKED+0x800:
            if not 0<=value<=7:raise Blocked('unsupported_cpu1_ipmask',value=value)
            self.irq1.ipmask=value
        elif IRQ_CONFIG<=address<IRQ_CONFIG+128:
            self.irq.config[(address-IRQ_CONFIG)//4]=value
        else:self.registers[address]=value
        self.record_write({'address':hex(address),'value':hex(value)})

    def record_write(self,row):
        self.counts['writes']+=1
        self.writes.append(row)
        if len(self.writes)>64:self.writes.pop(32)

    def timer0_irq4_probe_on_start(self,address,value,was_running):
        """路 BE 探路 fixture:把 STD12M Timer0 首次到期拉近到启动写之后。

        只在真正的「计数启动写」(CON mode 位 0→1、source=STD12M、探针步数
        已设置)那一刻生效一次:cnt 改为 prd+1-3*steps(每模型步 3 个 STD12M
        tick,与 advance() 的 3/4 换算精确一致),可选把 IRQ4 使能+优先级 1
        nibble OR 进 config[0](前缀里固件的 mask-all 已把该字清零,真实
        request_irq 是否装过 Timer 向量无静态证据——这是探针合成假设,不是
        硅状态)。改写只动模型计数器/nibble,不改任何资格链代码。
        """
        if self.timer0_irq4_expiry_probe_steps is None:return
        if address!=TIMER0 or was_running or not value&1:return
        if (value>>10)&15!=Timer0.STD12M_SOURCE:
            # 早期前缀里固件合法地以 source 4 多次启停 Timer0(PLL/trim 路径,
            # 到期 10 次都在 IRQ4 不合格期):探针只盯 STD12M(0x1401)那一次
            # 启动写,其他启动写原样放行。
            return
        steps=self.timer0_irq4_expiry_probe_steps
        if self.timer.prd is None or self.timer.cnt is None or steps*3>self.timer.prd+1:
            raise ValueError('Timer0 IRQ4 expiry probe needs an initialized period large enough')
        self.timer.cnt=self.timer.prd+1-steps*3
        self.timer.remainder=0;self.timer.source_remainder=0
        self.timer0_irq4_probe_armed=True
        if self.timer0_irq4_masked_probe:
            self.irq.config[0]=(self.irq.config[0] or 0)&~0xf0000
        self.record_write({'address':hex(TIMER0+4),'value':hex(self.timer.cnt),
                           'projection':'Timer0 IRQ4 expiry probe fixture (counter seed, not a firmware write)'})

    def core_tick_irq3_probe_on_start(self,address,value,was_running):
        """路 BF 探路 fixture:把 CPU0 core tick(IRQ3)首次溢出拉近到启动写之后。

        只在 CPU0 真正的「计数启动写」(CON 0→1,即 tick_timer_init 0x606622e
        的 st.w [r5+0]=r7/r7=1)那一刻生效一次:cnt 改为 prd+1-progress*steps
        (progress=每模型步 tick 数,与 advance() 的计数精确一致),可选把
        config[0] 的 IRQ3 nibble(bits12-15)清零(masked 反事实对照——真实
        前缀的 request_irq 已在启动写之前把它置成 priority 1)。改写只动
        模型计数器/nibble,不改任何资格链代码;CPU1 的 tick(0xffe8ec)不
        触发(主前缀 CPU1 停泊 idle、其 tick CON 从未使能)。
        """
        if self.core_tick_irq3_expiry_probe_steps is None or self.core_tick_irq3_probe_armed:return
        if address!=CORE_TICK_BASES[0] or was_running or not value&1:return
        steps=self.core_tick_irq3_expiry_probe_steps
        timer=self.core_ticks[0]
        if timer.prd is None or timer.cnt is None:
            raise ValueError('core tick IRQ3 expiry probe needs an initialized counter/period')
        if steps*timer.progress>timer.prd+1:
            raise ValueError('core tick IRQ3 expiry probe step count exceeds the period')
        timer.cnt=timer.prd+1-steps*timer.progress
        self.core_tick_irq3_probe_armed=True
        if self.core_tick_irq3_masked_probe:
            self.irq.config[0]=(self.irq.config[0] or 0)&~0xf000
        self.record_write({'address':hex(CORE_TICK_BASES[0]+4),'value':hex(timer.cnt),
                           'projection':'core tick IRQ3 expiry probe fixture (counter seed, not a firmware write)'})

    def enable_multicore(self,irq1_config,ipmask1,release_handler):
        if self.multicore_enabled or not self.cpu1_held_projection:raise ValueError('multicore requires a fresh held-CPU1 model')
        self.irq1=IRQController(initial_config=irq1_config,initial_ipmask=ipmask1,
                                unmaskable_irq_projection=self.irq.unmaskable_irq_projection)
        self.core_power={a:PartialWord() for a in CORE_PMU_CON1}
        self.cpu1_release_handler=release_handler;self.multicore_enabled=True

    def core_power_rmw(self,address,kind,operand):
        if kind!='and' or operand&0xffffffff!=0xfffffeff:raise Blocked('core_power_operation_not_modeled',address=hex(address))
        word=self.core_power[address];word.apply(kind,operand)
        self.counts['reads']+=1
        self.record_write({'address':hex(address),'known_mask':hex(word.known),'known_value':hex(word.value)})

    def handles_cpu1_control_rmw(self,address):
        return self.cpu1_held_projection and address==CPU1_CONTROL

    def cpu1_control_rmw(self,address,kind,operand):
        """CPU1 PMU_CON0(0xffe8c0) 的 RMW(cpu1_held_projection opt-in)。

        含 bit0 的形式与通用 read+write 路径等价:合并结果交回严格 write(),释放/保持/
        停止语义逐字节不变(实际释放指令 0x60660da 就是 `rmw.or.modimm |= 0x1`)。
        释放后 bit0 不参与的 or 置位是新的 kick 位记录。执行证据:RAM_CODE 0x12b732
        `rmw.or.modimm [r0] |= 0x8`,r0=(1-cnum)<<11+0xffe0c0(core0 侧指向对核 PMU_CON0),
        位于 0x12b73c 回跳的等待对核 ack 轮询循环;0x12c71c 是同型的 boot 期等待 CPU1
        ready 循环。两处都只置位 bit3,固件从不回读、从不清除。reference SDK csfr.h 只定义
        了 PMU_CON0 的地址(q32DSP(n) SFR bank + 0x30*4),没有位定义,bit3 的硅语义未
        证实——模型侧只做只写位记录,不发明唤醒/调度效应;唤醒在 DualCoreMachine 的
        cpu1_control_kick_wake 投影里单独 opt-in。
        """
        if not self.handles_cpu1_control_rmw(address):raise ValueError('not the modeled CPU1 control register')
        if self.cpu1_control is None:raise Blocked('unknown_initial_cpu1_control')
        if kind not in ('or','and'):raise Blocked('cpu1_control_rmw_form_not_modeled',operation=kind)
        operand&=0xffffffff
        merged=(self.cpu1_control|operand) if kind=='or' else (self.cpu1_control&operand)
        merged&=0xffffffff
        self.counts['reads']+=1
        if kind=='or' and not operand&1:
            self.cpu1_control=merged;self.cpu1_control_kick_writes+=1
            self.record_write({'address':hex(address),'operation':kind,'operand':hex(operand),
                               'control':hex(merged),
                               'projection':'kick bit record only; no wake or scheduling effect modeled'})
            return
        self.write(address,merged)

    def handles_p11_lpwr_rmw(self,address):
        return self.p11_lp is not None and address==PWR_CON

    def p11_lp_form_matches(self,insn,op):
        """dispatch 侧的形式预判:只拦 |=0x20 请求;其他 RMW(如 bit6 清标志
        |=0x40)落回通用路径,保持既有严格复位标志语义。"""
        return (insn.id=='rmw.or.modimm' and op.get('m') is not None and
                op['m']&0xffffffff==LP_PWR_INIT_FLAG)

    def handles_p11_window_rmw(self,address):
        return self.p11_win is not None and self.p11_win.handles_release_rmw(address)

    def p11_window_rmw(self,address,kind,operand):
        """q32s PMU_CON(0xf2a0c0)释放 RMW 的 opt-in 拦截(|=1,唯一观测形状)。

        只记录置位(RMW 不经读回);释放效果不模拟——q32s 是否真的开始执行由
        p11_lpwr_response_projection 单独投影,两者不假定因果。
        """
        if not self.handles_p11_window_rmw(address):raise ValueError('not the modeled q32s PMU_CON')
        self.p11_win.release_rmw(address,kind,operand);self.counts['reads']+=1
        self.record_write({'address':hex(address),'operation':'or','operand':hex(P11WindowRegisters.RELEASE_RMW_OPERAND),
                           'known_value':hex(self.p11_win.control.value),'known_mask':hex(self.p11_win.control.known),
                           'projection':'write-only bit record; no release or scheduling effect modeled'})

    def handles_lp_timer_rmw(self,address):
        return self.lp_timer is not None and self.lp_timer.handles_lRCT(address)

    def lp_timer_rmw(self,address,kind,operand):
        """JL_LRCT CON(0xfd3600)位 RMW 的 opt-in 拦截(清位/置位,不经读回)。

        只按位记录(PartialWord),不臆造读回值;LRC trim 的使能/busy 位读回
        (0x6067514/0x606751c 轮询)与校准效果保持严格停。
        """
        if not self.handles_lp_timer_rmw(address):raise ValueError('not the modeled LRCT CON')
        self.lp_timer.rmw(address,kind,operand);self.counts['reads']+=1
        self.record_write({'address':hex(address),'operation':kind,'operand':hex(operand&0xffffffff),
                           'known_value':hex(self.lp_timer.lrct.value),'known_mask':hex(self.lp_timer.lrct.known),
                           'projection':'bitwise record only; no readback, trim completion or LRC value modeled'})

    def handles_byte_write(self,address):
        """单字节写的模型路由(probe_boot Memory.write 的字节钩子)。

        现有 hardware.handles(address,size) 只接受 size==4 且 4 对齐的字访问;
        P33 窗无名寄存器族的固件访问形状全是单字节写(st.b/st.b.l/pair 的
        st.b 半),需要独立的字节粒度入口——只路由到 p33_window 投影与
        P2M 邮箱回声槽(BM §3),其他地址一律 False(字节写到未建模地址
        继续走普通路径诚实停)。
        """
        return ((self.p33_window is not None and self.p33_window.handles(address)) or
                (self.p2m_mailbox is not None and address==P2mMailboxAck.ECHO_SLOT))

    def write_byte(self,address,value):
        """字节写:P33 窗 write-only 记录 / P2M 邮箱回声槽清槽(BM §3)。"""
        if self.p2m_mailbox is not None and address==P2mMailboxAck.ECHO_SLOT:
            self.p2m_mailbox.clear_slot(value)
            self.record_write({'address':hex(address),'value':hex(value),
                               'projection':'P11 mailbox P2M+3 echo-slot clear'})
            return
        if not self.handles_byte_write(address):raise ValueError('not a modeled P33 window byte register')
        self.p33_window.write_byte(address,value)
        self.record_write({'address':hex(address),'value':hex(value),
                           'projection':'P33 unnamed-window write-only byte record'})

    def handles_mem_pwr_rmw(self,address):
        return self.mem_pwr_con is not None and self.mem_pwr_con.handles(address)

    def mem_pwr_rmw(self,address,kind,operand):
        """P11_SYSTEM.MEM_PWR_CON(0xf2a62c)位 RMW 的 opt-in 拦截(置/清位,不经读回)。

        只按位记录(PartialWord),不臆造读回值;唯一读回(0x6067844 ld.w)是死读,
        由 probe_boot 的 opaque 死读门(证明值从不消费)单独放行。内存电源效果
        不模拟。
        """
        if not self.handles_mem_pwr_rmw(address):raise ValueError('not the modeled MEM_PWR_CON')
        self.mem_pwr_con.rmw(kind,operand);self.counts['reads']+=1
        self.record_write({'address':hex(address),'operation':kind,'operand':hex(operand&0xffffffff),
                           'known_value':hex(self.mem_pwr_con.bits.value),'known_mask':hex(self.mem_pwr_con.bits.known),
                           'projection':'write-only bit record; no memory-power effect modeled'})

    def observe_dead_mem_pwr_read(self):
        if self.mem_pwr_con is None:raise ValueError('MEM_PWR_CON projection is not enabled')
        self.mem_pwr_con.observe_dead_read();self.counts['reads']+=1
        self.counts['opaque_mem_pwr_con_reads']+=1

    def handles_p33_awkup_rmw(self,address):
        return self.p33_awkup is not None and self.p33_awkup.handles(address)

    def p33_awkup_rmw(self,address,kind,operand):
        """P3_AWKUP 族位 RMW 的 opt-in 拦截(置/清位,不经读回;路 BB)。

        只按位记录(PartialWord,与 mem_pwr_con/LRCT 位 RMW 同族),不臆造读回
        值;FLT 的 RMW 无观测形状由投影拒绝;CPND 的「写 1 清除 pending」机制
        不从寄存器名推断,只记录写访问本身。
        """
        if not self.handles_p33_awkup_rmw(address):raise ValueError('not a modeled P3_AWKUP register')
        self.p33_awkup.rmw(address,kind,operand);self.counts['reads']+=1
        bits=self.p33_awkup.bits[address] if address in self.p33_awkup.bits else None
        self.record_write({'address':hex(address),'operation':kind,'operand':hex(operand&0xffffffff),
                           **({} if bits is None else
                              {'known_value':hex(bits.value),'known_mask':hex(bits.known)}),
                           'projection':'P3_AWKUP write-access bit record; no readback, no CPND effect modeled'})

    def handles_p33_canary_rmw(self,address):
        return self.p33_canary is not None and self.p33_canary.handles(address)

    def p33_canary_rmw(self,address,kind,operand):
        """金丝雀树/低功耗域 P3_VLVD_CON/P3_PCNT_CON 位 RMW 的 opt-in 拦截
        (置/清位,不经读回;路 BV,P3_AWKUP 位记录同族)。

        只按位记录(PartialWord),不臆造读回值(读回只认首次读具名 fixture,
        经 read() 路径);观测形状=rmw.and.r(0x60c643e &=0x7FB)、
        rmw.and.modimm(0x60c6766 &=0x1FE)、rmw.or.modimm(0x6069238 |=0x40),
        形状外由 probe_boot 分发拒绝(canary_rmw_form_not_modeled)。
        """
        if not self.handles_p33_canary_rmw(address):raise ValueError('not a modeled P33 canary register')
        self.p33_canary.rmw(address,kind,operand);self.counts['reads']+=1
        bits=self.p33_canary.bits[address]
        self.record_write({'address':hex(address),'operation':kind,'operand':hex(operand&0xffffffff),
                           'known_value':hex(bits.value),'known_mask':hex(bits.known),
                           'projection':'P33 canary write-access bit record; no readback, '
                                       'no VLVD/PCNT electrical effect modeled'})

    def p11_lpwr_rmw(self,address,instruction_id,operand):
        """主核 PWR_CON |=0x20 请求 RMW 的 opt-in 拦截。

        只接受已证实的 rmw.or.modimm |=0x20 形式(0x60679a0);记录请求后指令完成,
        合并写回不落在严格复位标志路径上(该路径只允许 bit6 清标志命令)。其他形式
        保持原有严格语义(整字 store 或其他 RMW 仍被拒)。
        """
        if not self.handles_p11_lpwr_rmw(address):raise ValueError('not the modeled P11 low-power request register')
        kind='or' if instruction_id=='rmw.or.modimm' else instruction_id
        if not self.p11_lp.handles_rmw(address,kind,operand):
            raise Blocked('p11_lpwr_request_form_not_modeled',instruction_id=instruction_id,
                          operand=None if operand is None else hex(operand&0xffffffff))
        self.p11_lp.request();self.counts['reads']+=1
        self.record_write({'address':hex(address),'operation':'or','operand':hex(LP_PWR_INIT_FLAG),
                           'projection':'P11 processed the LP_PWR_INIT_FLAG request; bit5 reads back 0'})

    def read_bytes(self,address,size):
        """Byte-granular explicit P2M heap-info fixture reads; None keeps the strict stop.

        power_init 读取 0xf27f4a..4d(P11 init 写入的 heap begin/size,值未证实)。
        fixture 由调用方显式提供;未提供时返回 None,普通存储路径继续严格停。
        路 B3 增(0b875ed,B2R 重落地):P2M 邮箱回声槽(0xF27F43)的字节读按
        武装态服务(BM §3)。
        """
        if (self.p2m_mailbox is not None and size==1 and address==P2mMailboxAck.ECHO_SLOT):
            self.counts['reads']+=1
            return bytes((self.p2m_mailbox.echo_read(),))
        if (self.p11_heap_info_bytes is None or not isinstance(size,int) or size<1 or
                not P11_HEAP_INFO<=address or address+size>P11_HEAP_INFO+4):return None
        self.counts['reads']+=1
        offset=address-P11_HEAP_INFO
        return self.p11_heap_info_bytes[offset:offset+size]

    def software_irq_command(self,address,mask):
        self.software_irq.command(address,mask)
        self.counts['reads']+=1
        self.record_write({'address':hex(address),'selected_mask':hex(mask),'projection':'WO selected-bit command; no read value fabricated'})

    def software_irq_source_rmw(self,address,kind,operand):
        self.software_irq.source_rmw(address,kind,operand)
        self.counts['reads']+=1
        self.record_write({'address':hex(address),'operation':kind,'operand':hex(operand),
                           'projection':'source-enable bit tracking only; eligibility unchanged'})

    def lock(self,acquire):
        if self.lock_owner=='unknown':raise Blocked('uninitialized_shared_lock')
        actor=self.current_core if self.multicore_enabled else 0
        if acquire:
            if self.lock_owner is not None:
                if self.multicore_enabled and self.lock_owner!=actor:
                    from br28_machine import CoreStall
                    raise CoreStall('shared_lock_wait',owner=self.lock_owner)
                raise Blocked('shared_lock_contention_or_recursion',owner=self.lock_owner)
            self.lock_owner=actor
        else:
            if self.lock_owner!=actor:raise Blocked('shared_lock_release_by_nonowner',owner=self.lock_owner)
            self.lock_owner=None
        self.counts['lock_acquire' if acquire else 'lock_release']+=1

    def timer0_irq4_could_fire(self,icfg):
        """事件时钟资格预检(路 BQ/G1):Timer0 的 pending/到期是否可能在某核
        抛 IrqDeliveryRequest 或既有严格停。

        与 advance() 的 Timer0 扫描同构(逐核 controller+gate 需求,core1 需
        已释放),但绝不在这里抛:返回 True=至少一核合格,或资格无法判定
        (未初始化 config/ipmask、未知 gate 需求、未建模 unmaskable——这些
        情形下推进中的 advance() 会在到期处以同一严格停显形,按事件处理才
        不隐藏边界);False=所有可达核都确定不合格(契约 §4:被屏蔽的
        pending/到期都不唤醒,资格面在全核 IDLE 期间冻结,预检一次有效)。
        """
        controllers=(self.irq,self.irq1) if self.multicore_enabled else (self.irq,)
        for core,irq in enumerate(controllers):
            if core==1 and not self.cpu1_control&1:continue
            gate=self.core_icfg.get(core) if self.multicore_enabled else icfg
            try:
                if irq.eligible(4,gate if gate is not None else 0x300):return True
            except Blocked:
                return True  # 资格无法判定:按事件处理,advance() 到期处显形
        return False

    def timer0_wake_horizon(self,icfg):
        """Timer0 对事件时钟的期限贡献(路 BQ/G1,docs/d1-idle-event-clock.md §3.2)。

        返回距下一个 Timer0 唤醒事件的模型步数:pending 已置位且未在途=1
        (下一个 advance 步即请求交付/严格停);否则在计数=Timer0.steps_to_
        next_expiration 的精确闭式;其余(不在计数/IRQ4 全核确定屏蔽/pending
        片已在途等 ACK)=None,不构成事件。delivery_in_flight 的 pending 片
        已消费过请求、handler ACK 前不会再抛,计入期限会造成无限推进,故
        与 core tick 的 unresolved_overflow 同旨排除。
        """
        timer=self.timer
        if not self.timer0_irq4_could_fire(icfg):return None
        if timer.pending:return None if timer.delivery_in_flight else 1
        if timer.con is None or not timer.con&3:return None
        rate=self.timer_lsb_ticks_per_step if (timer.con>>10)&15==0 else self.source_ticks_per_step
        return timer.steps_to_next_expiration(rate)

    def timer2_wake_horizon(self):
        """TIMER2 完成唤醒对事件时钟的期限贡献(路 BK §3.2/G1 第三族)。

        返回距 TIMER2 完成唤醒的模型步数:pending 已置位=1(下一步即唤醒);
        在计数=Timer2Delay.steps_to_next_expiration 的精确闭式(观测形状
        div=1/CNT=1/PRD=200 → 恰 4000 步);不在计数/未初始化=None。与
        timer0_wake_horizon 的分野:无 icfg 资格面(完成唤醒不是 IRQ 交付,
        只需机器侧有锚点停泊核——该条件由机器侧包装检查),无在途排除
        (pending 置位后每一步都可唤醒,唤醒即消费)。闭式只作推进上限,
        实际置位仍由 advance() 逐步触发。
        """
        timer=self.timer2
        if timer is None:return None
        if timer.pending:return 1
        if timer.con is None or not timer.con&3 or timer.cnt is None or timer.prd is None:
            return None
        return timer.steps_to_next_expiration(self.source_ticks_per_step)

    def advance(self,icfg):
        self.counts['completed_model_steps']+=1
        if self.icu is not None:self.icu.advance()
        if self.watchdog is not None:self.watchdog.advance()
        if self.core_ticks is not None:
            if self.core_ticks[1].con is not None and self.core_ticks[1].con&1:
                if not self.cpu1_held_projection or self.cpu1_control is None:raise Blocked('core1_execution_state_unknown')
                if self.cpu1_control==0 and self.core_tick_count_while_held is None:raise Blocked('core1_tick_held_clock_unknown')
            for core,timer in enumerate(self.core_ticks):
                if core==1 and self.cpu1_held_projection and self.cpu1_control==0 and self.core_tick_count_while_held is False:continue
                if timer.unresolved_overflow:
                    # Opt-in delivery: the handler's ACK clears pending; until
                    # then the counter holds (no re-entrancy) and no second
                    # overflow is raised. The strict default keeps the terminal
                    # stop for any already-overflowed timer.
                    if not self.core_tick_delivery_requests:
                        raise Blocked('core_tick_overflow_requires_resolution')
                    # 路 BF:pending 期间每步重检资格(Timer0 同款先例——真实
                    # IRQ 控制器语义:cli/sti 临界区只屏蔽不丢弃,退出临界区
                    # 即交付;溢出瞬间 IPMASK>priority 不算不合格事件的终判)。
                    # 请求在途不重复抛(delivery_in_flight,ACK 一并解除)。
                    if timer.delivery_in_flight:continue
                    irq=self.irq if core==0 else self.irq1
                    gate=self.core_icfg.get(core) if self.multicore_enabled else None
                    try:eligible=irq.eligible(3,0x300 if gate is None else gate)
                    except Blocked as e:
                        raise Blocked('core_tick_delivery_blocked',core=core,irq=3,reason=e.details['kind']) from None
                    if eligible:
                        timer.delivery_in_flight=True
                        raise IrqDeliveryRequest(core=core,irq=3)
                    continue
                before=timer.expirations;timer.advance()
                if timer.expirations!=before:
                    if not self.core_tick_delivery_requests:
                        raise Blocked('core_tick_overflow_delivery_unresolved',core=core,irq=3,
                                      note='internal pending set; no guessed readback bit, wake or interrupt entry')
                    irq=self.irq if core==0 else self.irq1
                    gate=self.core_icfg.get(core) if self.multicore_enabled else None
                    try:eligible=irq.eligible(3,0x300 if gate is None else gate)
                    except Blocked as e:
                        raise Blocked('core_tick_delivery_blocked',core=core,irq=3,reason=e.details['kind']) from None
                    if eligible:
                        # 与 pending 重检分支同一在途纪律(Timer0 先例)。
                        timer.delivery_in_flight=True
                        raise IrqDeliveryRequest(core=core,irq=3)
        if self.timer.con is None or not self.timer.con&3:self.timer.advance(0)
        elif ((self.timer.con>>10)&15)==0:self.timer.advance(self.timer_lsb_ticks_per_step)
        else:self.timer.advance(self.source_ticks_per_step)
        # 路 BK/S19 批 1:TIMER2 与 Timer0 同一条推进路(每模型步 advance,事件
        # 时钟段逐虚拟步——advance_event_clock 调的就是本方法),LRC 1/80 分数
        # 累积在 Timer2Delay.advance 内部;置位 pending 不抛交付(IRQ6 无 ICU
        # 证据),唤醒由机器侧锚定轮询件消费。
        if self.timer2 is not None:self.timer2.advance(self.source_ticks_per_step)
        if self.timer.pending:
            if self.multicore_enabled:
                for core,irq in enumerate((self.irq,self.irq1)):
                    # bit0=已释放;kick 位(bit3,值可达 9)只是只写记录,不得改变交付资格
                    if core==1 and not self.cpu1_control&1:continue
                    gate=self.core_icfg.get(core)
                    if gate is None:
                        if irq.eligible(4,0x300):raise Blocked('pending_irq_with_unknown_core_icfg',core=core,irq=4)
                    elif irq.eligible(4,gate):
                        if not self.timer0_irq_delivery_requests:
                            raise Blocked('interrupt_delivery_not_implemented',core=core,irq=4)
                        # 路 BE:每个 pending 片只请求一次交付;handler 用真实
                        # W1C(rmw.or.modimm [0xfd0400] |= 0x4000,Timer0.write)
                        # 清 pending 并解除 in-flight,下个周期可再请求。
                        if not self.timer.delivery_in_flight:
                            self.timer.delivery_in_flight=True
                            raise IrqDeliveryRequest(core=core,irq=4)
            elif self.irq.eligible(4,icfg):
                if not self.timer0_irq_delivery_requests:
                    raise Blocked('interrupt_delivery_not_implemented',irq=4,
                                  note='Timer0 pending and enabled after priority filtering; CPU entry not bypassed')
                if not self.timer.delivery_in_flight:
                    self.timer.delivery_in_flight=True
                    raise IrqDeliveryRequest(core=0,irq=4)
        # 路 D2AI(sim-d2ai,预研 §7 步 1/INJ-01):ADC DMA pending 扫描——
        # Timer0 块后同位。IRQ68 是全固件唯一音频 DMA 中断,注册于 CPU0 语境
        # (boot initcall 0x602ec28 request_irq prio2,向量槽 0x19FF10 运行时
        # 落盘 0x602f406,PRE §6.1)——核选择按运行观测钉 CPU0;若固件未来
        # 把 IRQ68 配到 CPU1(ICFG nibble),新站点须重审后扩核。pending 且
        # 非 in-flight 时按真实 ICFG nibble+IPMASK 判资格,合格即抛
        # IrqDeliveryRequest(core=0,irq=68)(每 pending 片至多一次;真实
        # |=0x40 W1C 清 pending 时一并解除 in-flight)。
        dma=self.jl_audio_adc_dma
        if dma is not None and dma.delivery and dma.pending:
            gate=self.core_icfg.get(0) if self.multicore_enabled else icfg
            try:eligible=self.irq.eligible(68,0x300 if gate is None else gate)
            except Blocked as e:
                raise Blocked('audio_dma_irq_delivery_blocked',core=0,irq=68,
                              reason=e.details['kind']) from None
            if eligible:
                if not self.audio_adc_dma_delivery_requests:
                    raise Blocked('interrupt_delivery_not_implemented',core=0,irq=68,
                                  note='audio ADC DMA pending and eligible; CPU entry not '
                                       'bypassed — enable the audio ADC DMA delivery requests')
                if not dma.delivery_in_flight:
                    dma.delivery_in_flight=True
                    raise IrqDeliveryRequest(core=0,irq=68)
        if self.software_irq is not None:
            for core,word in enumerate(self.software_irq.pending):
                if core==1 and (not self.multicore_enabled or not self.cpu1_control&1):continue
                irq=self.irq if core==0 else self.irq1
                gate=self.core_icfg.get(core) if self.multicore_enabled else icfg
                for bit in range(4):
                    if word.known&(1<<bit) and not word.value&(1<<bit):continue
                    try:eligible=irq.eligible(124+bit,0x300 if gate is None else gate)
                    except Blocked as e:
                        if e.details['kind']=='unmodeled_unmaskable_irq' and self.multicore_enabled:
                            raise Blocked('unmodeled_unmaskable_irq',core=core,irq=124+bit,
                                          priority=e.details['priority'],source='software_irq',
                                          pending_known=bool(word.known&(1<<bit)),
                                          icfg_gate_demand='unknown' if gate is None else hex(gate)) from None
                        raise
                    if eligible:
                        if gate is None:raise Blocked('software_irq_with_unknown_icfg',core=core,irq=124+bit)
                        if self.software_irq_delivery_requests:
                            raise IrqDeliveryRequest(core=core,irq=124+bit)
                        kind='software_irq_delivery_not_implemented' if word.known&(1<<bit) else 'software_irq_pending_unknown'
                        raise Blocked(kind,core=core,irq=124+bit)

    def report(self):
        result={'registers':{hex(a):hex(v) for a,v in sorted(self.registers.items())},
                'ipmask':self.irq.ipmask,'irq_config_known_words':sum(x is not None for x in self.irq.config),
                'timer0':{'con':self.timer.con,'cnt':self.timer.cnt,'prd':self.timer.prd,
                          'pending':self.timer.pending,'expirations':self.timer.expirations},
                'counts':dict(self.counts),'writes':self.writes,
                'writes_truncated':self.counts['writes']>len(self.writes),'shared_lock_owner':self.lock_owner,
                'assumptions':{'source_ticks_per_model_step':self.source_ticks_per_step,
                    'initial_registers':{hex(a):hex(v) for a,v in sorted(self.initial_registers.items())},
                    'initial_irq_config':self.initial_irq_config,'initial_ipmask':self.initial_ipmask,
                    'shared_lock_model':self.bus_lock_model,'initial_lock_owner':self.initial_lock_owner,
                    'timer_progress_is_virtual_not_cycle_calibrated':True,
                    'external_reset_inputs_inactive':self.reset_inputs_inactive,
                    'analog_pll_lock_simulated':False,'cpu_interrupt_delivery_simulated':False}}
        if self.gpio_model:
            result['gpio']={hex(a):{'known_value':hex(w.value),'known_mask':hex(w.known)}
                            for a,w in sorted(self.gpio.items())}
            result['assumptions']['gpio']='exact bitwise configuration projection; external levels unknown; no GPIO IRQ/electrical model'
        if self.mmu is not None:result['mmu']=self.mmu.report()
        if self.p11 is not None:result['p11_access_counterfactual']=self.p11.report()
        if self.p11_lp is not None:result['p11_low_power_response']=self.p11_lp.report()
        if self.p11_win is not None:result['p11_window_projection']=self.p11_win.report()
        if self.p2m_mailbox is not None:result['p2m_mailbox']=self.p2m_mailbox.report()
        if self.lp_timer is not None:result['lp_timer_init_projection']=self.lp_timer.report()
        if self.mem_pwr_con is not None:result['mem_pwr_con_projection']=self.mem_pwr_con.report()
        if self.p33_window is not None:result['p33_window_projection']=self.p33_window.report()
        if self.p33_port_sel is not None:result['p33_port_sel_projection']=self.p33_port_sel.report()
        if self.jl_uart_init_records is not None:result['jl_uart_init_write_records']=self.jl_uart_init_records.report()
        if self.jl_omap_records is not None:result['jl_omap_write_records']=self.jl_omap_records.report()
        if self.jl_imap_records is not None:result['jl_imap_write_records']=self.jl_imap_records.report()
        if self.jl_wl_aud_records is not None:
            result['jl_wl_aud_write_records']=self.jl_wl_aud_records.report()
            result['assumptions']['jl_wl_aud_write_records']=(
                'JL_WL_AUD->CON0 (0xfe2200, br28.h hs_base+map_adr(0x22,0), single-member '
                '__RW): whole-word write records (entry 0x602ea72 =1 plus the DAC-init tail '
                '0x6071cbe =r10) with last-write readback and an explicit synthetic initial 0 '
                'before the first write (derived-not-measured, route-AM precedent, NOT a '
                'reset-value claim); the sole app read site (0x6069a2e power-management '
                'snapshot) is outside this prefix and there is no ready-bit poll anywhere; '
                'the clock-gate hardware effect is NOT modeled — this does NOT verify any '
                'audio or wireless-audio function')
        if self.jl_audio_dac_init_records is not None:
            result['jl_audio_dac_init_write_records']=self.jl_audio_dac_init_records.report()
            result['assumptions']['jl_audio_dac_init_write_records']=(
                'three audited audio DAC-init configuration words (JL_AUDIO->DAC_CON 0xfe0500 '
                '/ AUD_CON 0xfe0558, JL_ASS->CLK_CON 0xfe0900; all br28.h __RW): write '
                'records with last-write readback and an explicit synthetic initial 0 before '
                'the first write (derived-not-measured, route-AM precedent, NOT a '
                'reset-value claim — consumed by the CLK_CON |=1 / AUD_CON &=~0x40 RMW '
                'internal reads); RMWs run the full generic MMIO path with the write-backs '
                'recorded; the route-AS dynamic record corrects the AG-map parallel-pair '
                'misread of the 0x6071cbc store (the "0xfe2200 numeric coincidence" value '
                'was never the DAC_CON content — the pair stores the OLD r1, the SR-walk '
                'value 0x0; 0xfe2200 is the S2 base); '
                'the conditional families (esco-mic/ANC/missing-config) stay unmapped; no '
                'DAC clock gating, analog path, DMA or IRQ68 event is modeled — this does '
                'NOT verify any audio output')
        if self.jl_audio_adc_reset_records is not None:
            result['jl_audio_adc_reset_write_records']=self.jl_audio_adc_reset_records.report()
            result['assumptions']['jl_audio_adc_reset_write_records']=(
                'JL_AUDIO->ADC_CON (0xfe0538 = JL_AUDIO base +0x38, br28.h all-__RW, route-AE '
                'naming and bit semantics): whole-word write record of the ADC-node reset '
                '[ADC_CON]=0 plus last-write readback and an explicit synthetic initial 0 '
                'before the first write (derived-not-measured, route-AM precedent, NOT a '
                'reset-value claim); the one store on this path is the second slot of the '
                'parallel pair at 0x60719c0 (`add.s14.rri.p r3=r1+4596` ∥ `st.w [r3+0]=r4`) '
                'reading the OLD r3 = 0xfe0538 with r4 = 0 — the FM-1 run3 old-register '
                'parallel semantics already implemented by exact_parallel_stores (its first '
                'SFR site); zero reads on this prefix (every route-AE consumer lives on the '
                'IRQ68-delivery/esco-open paths); on silicon bit7 pending is hardware-set '
                'per DMA sample, the projection returning the software value is the honest '
                'no-ADC-activity manifestation, never an invented completion; the rest of '
                'the ADC register family stays unmapped; no ADC clock gating, analog front '
                'end, DMA or IRQ68 event is modeled — this does NOT verify any audio input')
        if self.jl_adda_daa_con1 is not None:
            result['jl_adda_daa_con1_registers']=self.jl_adda_daa_con1.report()
            result['assumptions']['jl_adda_daa_con1_registers']=(
                'JL_ADDA->DAA_CON1 (0xfe0a04, br28.h hs_base+map_adr(0x0a,0) +0x04 __RW, the '
                'DAC-side analog gain word): TWO SEPARATE switches — an explicit initial-word '
                'fixture (no implicit zero default; the served fixture word is labeled '
                'sensitivity-probe-not-plausible-state, derived-not-measured, NOT a '
                'reset-value claim — the first-read unknown state has no derivable source) '
                'plus config-bit last-write readback after real firmware writes; with '
                'readback alone a never-written read still stops at the ORIGINAL boundary '
                '(unmapped_memory read 0xfe0a04) and with the fixture alone a post-write read '
                'does too; the RMW at 0x606b40e preserves bits 3 and 7..31 from the initial '
                'state (only bits0..2 and bits4..6 take the table code), reached through the '
                'real-call veneer 0x606db6c from the DAC-init driver 0x6071b70 (r0=0xFF, '
                'gain step 0, runtime-verified); exactly the one word is whitelisted — the '
                'rest of the ADDA read family stays unmapped and later reads get classified '
                'per address; no DAC analog path or audio output is modeled — this does NOT '
                'verify any audio output')
        if self.jl_adda_family is not None:
            result['jl_adda_analog_family_registers']=self.jl_adda_family.report()
            result['assumptions']['jl_adda_analog_family_registers']=(
                'JL_ADDA analog family, five words (0xfe0a00 DAA_CON0 / 0xfe0a08 DAA_CON2 / '
                '0xfe0a0c DAA_CON3 / 0xfe0a48 ADDA_CON1 / 0xfe0a4c unnamed-past-struct; '
                'br28.h JL_ADDA_TypeDef all-__u32): write record plus last-write readback '
                'with an explicit synthetic initial 0 before the first write '
                '(derived-not-measured, route-AM/CW precedent, NOT a reset-value claim); '
                'whitelist = the exact static enumeration of the analog-init function '
                '0x606abf0..0x606b20e (68/132/35/34/10 RMW sites plus ld→mask/or→st '
                'compositions, first stop the DAA_CON0 bit13 clear at 0x606ac42); every '
                'read value only composes a write-back — no branch inside the function '
                'consumes a loaded value; +0x04 DAA_CON1 stays with the route-CV two-switch '
                'container (mutually exclusive address sets); the known status-bit consumer '
                '0x606b352 (DAA_CON2 &2 compare) lives in the audio-task domain outside this '
                'prefix — the container would serve its synthesized config word there, the '
                'silicon bit1 semantics stay unverified; no DAC analog path, bias or audio '
                'output is modeled — this does NOT verify any audio output')
        if self.jl_audio_dac_tm is not None:
            result['jl_audio_dac_tm_registers']=self.jl_audio_dac_tm.report()
            result['assumptions']['jl_audio_dac_tm_registers']=(
                'JL_AUDIO DAC_TM0/TM1 (0xfe0524/0xfe0528 = JL_AUDIO base +0x24/+0x28, '
                'br28.h both __RW __u32, route DT): write record plus last-write readback '
                'with an explicit synthetic initial 0 before the first write '
                '(derived-not-measured, route-AM/CW family); initial-value origin is the '
                'named synthetic_zero assumption registered with this switch — the '
                'preserved half-word persists into the write-back (CV-like initial-value '
                'dependence) but the app has no reader outside the setter RMW, so no '
                'separate initial-fixture switch (codex decision); whitelist = the exact '
                'static address set of the half-word field setter 0x606ab92 (12-way tbb, '
                'selectors 0/2 TM0.low 1/3 TM1.low 8/10 TM0.high 9/11 TM1.high, 4..7 '
                'no-op; all 10 app call sites in the audio driver layer feed config '
                'halfwords, fixed -600 values, a 31-step ramp and buffer-length-derived '
                'halves); the app never reads DAC_TM0/TM1 as live hardware state — the '
                'only read form is the setter RMW internal read composing the write-back, '
                'so the B3 handoff "getter / hardware-maintained time-mark read" '
                'classification is withdrawn; the TM hardware semantics/units stay '
                'UNVERIFIED and are not interpreted; any future reader outside the setter '
                'must be re-audited before this container may serve it; no DAC time-mark '
                'counting, DMA evolution or audio output is modeled — this does NOT '
                'verify any audio output')
        if self.jl_adda_ada is not None:
            result['jl_adda_ada_family_registers']=self.jl_adda_ada.report()
            result['assumptions']['jl_adda_ada_family_registers']=(
                'JL_ADDA ADA family, nine words (0xfe0a20..0xfe0a40 = ADA_CON0..ADA_CON8, '
                'br28.h JL_ADDA_TypeDef all-__u32; cross-check audio_anc.c reads ADA_CON8 '
                'as four 5-bit mic gains, route ADA): write record plus last-write readback '
                'with an explicit synthetic initial 0 before the first write '
                '(derived-not-measured, route-AM/CW family); every audited read value only '
                'composes a write-back (309 atomic RMW + 51 load→mask/insert→st sites in '
                'the ADA config function 0x606b4d0..0x606c3a8, pure clear-bit close '
                'sequences 0x606f4c0/0x606f438, channel-masked field setters '
                '0x606c3b0/0x606de9c/0x606ee9a — branches test only config inputs); the '
                'face is "seven identified direct-li base sites (nine instances)" from a '
                'constant-by-constant independent decode sweep — indirect sources '
                '(constant pools, computed addresses, RAM pointers, cross-function '
                'arguments) are not exhausted and stay an explicit boundary (codex note); '
                'whitelist = exactly the nine proven-access words; 0xfe0a44 ADA_CON9 has '
                'zero identified sites and stays unmapped; the address whitelist only '
                'blocks new addresses — a new READ consumer of a whitelisted word must be '
                're-classified before this container may serve it; read and the atomic '
                'RMW internal read share one container state (never re-zeroed); the '
                'close paths clear bits in the synthesized config word, which does NOT '
                'mean a real analog channel is off or any hardware state was cleared; '
                'the type layout is NOT evidence of plain-RW/reset-zero/side-effect-free '
                'silicon; erratum: the route-B3 report text labels 0xfe0a48 "ADDA_CON1" '
                'and 0xfe0a4c "unnamed" — per the same layout they are ADDA_CON0/'
                'ADDA_CON1, the B3 text stays byte-pinned and its served addresses are '
                'unchanged; no ADC analog path, bias, calibration or audio input is '
                'modeled — this does NOT verify any audio input')
        if self.p11_lpvad is not None:
            result['p11_lpvad_config_registers']=self.p11_lpvad.report()
            result['assumptions']['p11_lpvad_config_registers']=(
                'P11_SFR LPVAD VAD_ACON0 (0xf2b404 = p11_sfr_base+map_adr(0x14,0x01), '
                'reference p11_sfr.h P11_LPVAD_TypeDef second word, route LPV): write '
                'record plus current-value readback with an explicit synthetic initial 0 '
                'before the first write (AM-family form; both the initial 0 and the '
                'readback semantics are explicit model assumptions, NOT a reset value '
                'and NOT a static derivation — codex); the whole identified face is ONE '
                'atomic RMW (0x606b68e |= 0x10000000 via the single direct-li site '
                '0x606b688) whose internal read never reaches a GPR/branch/call '
                'argument — "zero consumers" is limited to identified main-core '
                'software consumers (the RMW itself consumes old, the other 31 bits '
                'enter the write transaction, peripherals/q32s may consume them); '
                'whitelist = exactly the one proven-access word, the other twelve '
                'struct words stay unmapped; the address whitelist only blocks new '
                'addresses — a new READ consumer of 0xf2b404 itself (new ld.w, new '
                'PC, new RMW mask, another core) must be re-audited (codex); '
                'independent switch, NOT a p11_window_projection extension (contract '
                'and family boundary, codex); the projection does NOT model q32s '
                'access to this word, LPVAD detection, cross-domain visibility or '
                'side effects — the main core passing the stop does NOT mean the '
                'real LPVAD is configured or running; the __RW header label is '
                'comment-only (==__WO==volatile) and is NOT silicon readback '
                'evidence; a weaker bit28-only record with unknown other bits was '
                'considered and not taken (assumption-strength difference '
                'registered in the container report)')
        if self.jl_audio_dac_vl0 is not None:
            result['jl_audio_dac_vl0_registers']=self.jl_audio_dac_vl0.report()
            result['assumptions']['jl_audio_dac_vl0_registers']=(
                'JL_AUDIO->DAC_VL0 (0xfe051c = JL_AUDIO base +0x1c, br28.h all-__RW, the '
                'digital-volume word, route-BO CONFIG classification — released by route CW): write record plus '
                'last-write readback; before the first firmware write the explicit '
                'synthetic initial word 0 (route-AM precedent) is served — '
                'derived-not-measured, NOT a reset-value claim; no separate initial-fixture '
                'switch (unlike route-CV DAA_CON1) because the read value only composes the '
                'insert write-back (no branch consumes it) and the only call site on this '
                'prefix (0x6071b76, r0=0xFF both channels, r1=0) overwrites every carried '
                'bit — the written word is 0x0 regardless of the initial state (the '
                'all-ones sensitivity fork stopped identically); the volume cap comes from '
                'the device-struct slot [0x1265C0+1883*4]=0x12832C (runtime 0x3a98; the '
                'route-CV/BO narrative 0x1272AC was an arithmetic slip); exactly the one '
                'word is whitelisted, the rest of the JL_AUDIO digital block stays '
                'unmapped; no digital-volume effect or audio output is modeled — this does '
                'NOT verify any audio output')
        if self.p33_awkup is not None:result['p33_awkup_projection']=self.p33_awkup.report()
        if self.p3_ls is not None:result['p3_ls_projection']=self.p3_ls.report()
        if self.fd31_adc_con is not None:result['fd31_adc_con_projection']=self.fd31_adc_con.report()
        if self.p33_canary is not None:result['p33_canary_projection']=self.p33_canary.report()
        # 路 BM §1(S19 批 1 ①):PVDD/trim 域容器报告——三地址合成初始字 0
        # seed+读回=当前值(AM 容器族),终值见 registers 键;运行效果(PVDD
        # 电压/trim 结果)不建模。
        if self.jl_pvdd_trim_enabled:
            result['assumptions']['jl_pvdd_trim_writes']=(
                'W4[4] PVDD/trim-domain container: P3_ANA_CON13 (0xf2c274 = P33_ACCESS(0x9d), '
                'p33_sfr.h L143), P3_ANA_CON1 (0xf2c244 = P33_ACCESS(0x91), L131) and '
                'P3_PVDD0_AUTO (0xf2c10c = P33_ACCESS(0x43), L78) seeded with the explicit '
                'synthetic initial word 0 and read back as the current register value '
                '(route-AM/VOLTAGE_CONFIG/P33_ANALOG_CONFIG precedent — p33_sfr.h has address '
                'macros only, no __RW/__RO/__WO, so this is the standing container semantics, '
                'NOT a reset-value or analog-state claim); all accesses on this path are '
                'composite RMWs (rmw.and.nmodimm [r8+48]&=0xFF0 family) and explicit ld/st '
                'read-modify-writes whose read values genuinely participate in the write-backs, '
                'served by the generic MMIO paths with zero new dispatch; the neighboring '
                'P3_PVDD1_AUTO (0xf2c110, VOLTAGE_CONFIG) and P3_ANA_CON5 (0xf2c254, route-AM) '
                'stay under their own switches; no PVDD voltage level, trim result or analog '
                'behavior is modeled — this releases the register face only')
        if self.timer2 is not None:
            result['timer2_delay_projection']=self.timer2.report()
            result['assumptions']['timer2_delay_projection']=(
                'JL_TIMER2 delay-domain projection (route-BK design): real counter on the '
                'Timer0 family shape (mode/src/div bitmap validation, W1C bit14, PND bit15 '
                'readback, PRD+1 formula, LRC fractional accumulation at the nominal '
                '200000 Hz — derived-not-measured, the model counts the written PRD and never '
                'reads RAM [0x129D88]); completion timing is event-clock counted (1 LRC tick '
                '= 80 RC16M reference ticks = 20 model steps at 4 reference ticks/step, so '
                'the observed 1ms shape PRD=200 expires after exactly 4000 model steps, '
                'closed form), never a preset delay; the machine-side anchored poll wake '
                '(0x12cd76, kick precedent) carries wake_source_unverified; no TIMER2 IRQ6 '
                'delivery is modeled (ICU enable state has zero evidence, route-BK R1) and '
                'the IWKUP_NUM (0xFFE1B0, value 6 = IRQ_TIME2_IDX) store is a write-only '
                'record with no hardware effect claimed')
        if self.jl_audio_dac_cop is not None:
            result['jl_audio_dac_cop_write_records']=self.jl_audio_dac_cop.report()
            result['assumptions']['jl_audio_dac_cop_write_records']=(
                'JL_AUDIO->DAC_COP (0xfe0534 = JL_AUDIO base +0x34, br28.h member 13 __WO): '
                'write-only whole-word record (exactly two all-write/no-read sites in the '
                'whole app: 0x606ab2c in the W1[3]-tail output-stage gate and 0x606e038 in '
                'the audio-domain function outside this prefix); reads and RMWs strictly '
                'stop — the D2-scene read/RMW classification of this word belongs to the '
                'post-D1 audio-task domain; no chopper/pump circuit or audio output is '
                'modeled — this does NOT verify any audio output')
        if self.jl_audio_dac_dma is not None:
            result['jl_audio_dac_dma_write_records']=self.jl_audio_dac_dma.report()
            result['assumptions']['jl_audio_dac_dma_write_records']=(
                'JL_AUDIO DAC DMA words (0xfe0504/0xfe0508/0xfe0510/0xfe0514 = base '
                '+0x04/+0x08/+0x10/+0x14, the ADR/LEN/HRP/PNS block per the route-AG '
                'wlaud-init-map layout chain): write-only whole-word records of the four '
                'stores in the W1[3]-tail DAC start function 0x6071e74 (0x6071e92..0x6071ea8, '
                'right after the output-stage gate call; ADR=0x18e180 is the 60KB DMA buffer '
                'cleared by the 0x6071d50 ROM memset, route-CW); a pathfinding run proved '
                'exactly four writes with ZERO reads/RMWs on this prefix — the D2-scene DMA '
                'pointer/count reads belong to the post-D1 tone/PCM push domain and stay '
                'strictly stopped; no DMA transfer, IRQ39 event or audio data flow is '
                'modeled — this does NOT verify any audio output')
        if self.jl_audio_adc_dma is not None:
            result['jl_audio_adc_dma_write_records']=self.jl_audio_adc_dma.report()
            delivery_on=self.jl_audio_adc_dma.delivery
            result['assumptions']['jl_audio_adc_dma_write_records']=(
                'JL_AUDIO ADC channel-buffer DMA words (0xfe053c/0xfe0540/0xfe0544/'
                '0xfe0548/0xfe054c = base +0x3c/+0x40/+0x44/+0x48/+0x4c, the ADR/LEN/'
                'PNS/HWP/SRP block written by set_buffer 0x606dee4, D2-b blueprint '
                'word 15 of the csfr.h 32-bit word slots): write-only whole-word '
                'records of the five consecutive stores in one basic block '
                '(0x606df20..0x606df30, the first being the store half of the '
                'parallel pair mul.rr.p at the 21,800,325 stop); values are real '
                'instruction results, never invented; the whole-app direct-li '
                'access sweep resolved exactly nine accesses — the five set_buffer '
                'writes plus the audio_adc_irq_handler [0x6072628,0x6072700) reads '
                'of PNS/ADR/SRP and its PNS write, reachable only through the IRQ68 '
                'dispatcher 0x602f444'
                + (' (route D2AI delivery projection ON: those reads are now served '
                   'by the container — ADR/LEN/PNS/SRN = last firmware write with a '
                   'synthetic initial 0, HWP = the model hardware write pointer; the '
                   'handler-tail SRN word 0xfe0550 IS whitelisted in this mode; the '
                   'ADC_CON bit7 pending overlay, |=0x40 W1C coupling, produce_block '
                   'DMA-completion projection and the advance() IRQ68 scan are all '
                   'opt-in on the same switch — DMA completion TIMING is a projection '
                   'and the only synthetic input is the sample bytes)' if delivery_on
                   else ' and NOT reached on this prefix (IRQ68 delivery '
                        'not modeled) — those reads stay strictly stopped and belong to the '
                        'D2-b completion-projection domain; the handler-tail SRN word '
                        '0xfe0550 is NOT whitelisted; the zero-read face on THIS prefix '
                        'motivates the record, it is NOT a measured hardware WO property '
                        'claim; no DMA transfer, pointer advance, SRN side effect, pending '
                        'bit, IRQ68 delivery or cache coherency is modeled — this does NOT '
                        'verify any audio input, ADC capture or D2-b acceptance scenario'))
        if self.jl_eq_init is not None:
            result['jl_eq_init_write_records']=self.jl_eq_init.report()
            result['assumptions']['jl_eq_init_write_records']=(
                'JL_EQ initialization words (0xfe1500 = JL_EQ->CON0 at hs_base+'
                'map_adr(0x15,0), br28.h JL_EQ_TypeDef {__RW __u32 CON0..CON3, '
                'DATAI_ADR, DATAO_ADR, DATA_LEN, FLT_ADR}; 0xfe151c = FLT_ADR at '
                '+0x1c, route JLEQ): write-only whole-word records of the two '
                'unconditional stores in the EQ init function 0x6072b24 — '
                '0x6072b92 [CON0]=0 (the 29,215,313 stop word, r5=0) and '
                '0x6072bae [FLT_ADR]=the actually-allocated 640B filter-buffer '
                'pointer (r0 reloaded from [instance+16]; the 0x6072b68 bnez '
                'targets 0x6072b90 and skips only the allocation plus the first '
                'memset — BOTH writes execute either way, codex correction of the '
                'route-PARKPRE branch description); values are real instruction '
                'results, never invented, and the FLT_ADR pointer is whatever the '
                'model allocator returned, not pre-claimed; the JL_EQ base is '
                'data-driven (0xfe1500 appears in app.bin only at .data 0x60c3748, '
                'the hardware-descriptor template copied to the RAM instance table '
                '0x124c40, loaded through the instance pointer slot [0x12727C]+16 '
                '— immune to li-constant sweeps, the route-PARKPRE root cause for '
                'the S19MAP #7 miss); reads of these words never happened on this '
                'prefix and stay strictly stopped under the explicit kind — the '
                'EQ/audio handler domain (0x6072bb2 copex family) and DSP '
                'coefficient streaming are future write faces that must be '
                're-audited as real accesses arrive; the other six declared '
                'members (CON1/CON2/CON3/DATAI_ADR/DATAO_ADR/DATA_LEN = '
                '0xfe1504..0xfe1518) and 0xfe1520 onward have zero identified '
                'access and stay unmapped; the __RW layout is NOT silicon '
                'readback/WO evidence; no EQ filtering, DMA, IRQ, coefficient '
                'loading or audio processing is modeled — this does NOT verify any '
                'EQ function or audio processing')
        if self.jl_adc_start is not None:
            result['jl_adc_start_config_registers']=self.jl_adc_start.report()
            result['assumptions']['jl_adc_start_config_registers']=(
                'ADC start-function config block (JL_ASS->MIC_CTL0 0xfe0904 / MIC_CTL1 '
                '0xfe0908 and JL_AUDIO->MBG_CON 0xfe0554 / ADC_CON1 0xfe055c, the '
                'unmodeled words of the ADC start function 0x606df66): synthetic-config '
                'read-back container — a never-written word reads back the explicit '
                'synthetic initial word 0, a written word reads back its current value '
                '(both synthetic-not-measured, NOT silicon reset values); the base+'
                'offset propagation sweep (356 li anchors, all-GPR constant tracking, '
                'register-to-register propagation — built for the 21,800,455 stop whose '
                'address r0=r5+976 has NO direct li site) resolved every in-window '
                'access to config RMW / insert write-back shapes with zero branch '
                'consumption; JL_ASS->CLK_CON 0xfe0900 and AUD_CON 0xfe0558 stay with '
                'the route-AK container and SRN 0xfe0550 stays unmapped; a new consumer '
                'of any whitelisted address (new PC, new RMW mask, another core) must '
                'be re-audited; no q32s access, ADC capture, clock enable or audio '
                'input is modeled — this does NOT verify any audio input or D2-b '
                'acceptance scenario')
        if self.p11_heap_info_bytes is not None:
            result['p11_heap_info_fixture']={'address':hex(P11_HEAP_INFO),
                'bytes':self.p11_heap_info_bytes.hex(),
                'source':'explicit caller fixture; P11 init writes heap begin/size '
                         '(P11 code mov r5,#0x7f4a at 0xa1a with wide immediates 0x3d0c/0x39f4 '
                         'at 0xa12/0xa26), value derived-not-measured'}
        if self.watchdog is not None:result['watchdog_projection']=self.watchdog.report()
        if self.efuse is not None:result['efuse_read_projection']=self.efuse.report()
        if self.gpcnt is not None:result['gpcnt_cleanup_projection']=self.gpcnt.report()
        if self.timer0_irq_delivery_requests:
            # 路 BE:交付请求开(默认关不产生任何报告键,legacy 逐字节不变)。
            result['assumptions']['timer0_irq_delivery_requests']={
                'enabled':True,
                'request_raised_once_per_pending_episode':True,
                'episode_gate':'Timer0.delivery_in_flight; cleared by the real W1C '
                               '(rmw.or.modimm [0xfd0400] |= 0x4000, bit14) modeled by Timer0.write',
                'delivery_does_not_clear_pending':True,
                'episode_in_flight_at_report':self.timer.delivery_in_flight,
                'eligibility_chain_unchanged':'ICFG gates8/9 + IRQ_CONFIG word0 nibble enable '
                                               '+ priority vs IPMASK; per-core advance() scan',
                'cpu_entry_is_a_counterfactual_projection':True}
        if self.timer0_irq4_probe_armed:
            result['assumptions']['timer0_irq4_probe']={
                'purpose':'pull the ~341097-step first Timer0 IRQ4 boundary inside the observed prefix',
                'counter_seed_at_std12m_start':self.timer0_irq4_expiry_probe_steps,
                'seed_ticks_per_model_step':3,
                'irq4_nibble_cleared_counterfactual':self.timer0_irq4_masked_probe,
                'real_prefix_irq4_eligibility':'enabled by the driver-install request_irq at ~step 110768 '
                                               '(0xffe100=0x30000, priority 1, maskable; no later clear)',
                'is_synthetic_probe_fixture_not_silicon_state':True}
        if self.core_tick_irq3_probe_armed:
            # 路 BF:CPU0 core tick 首次溢出 ≈447,490 步(cnt 625452/prd
            # 1199999 @304,317)前的交付机制探针。资格链(向量槽/nibble)由
            # 真实前缀的 tick_timer_init request_irq 自己建立,探针只 seed
            # 计数器。
            result['assumptions']['core_tick_irq3_probe']={
                'purpose':'pull the ~447490-step first CPU0 core tick (IRQ3) overflow '
                          'inside the observed prefix',
                'counter_seed_at_start_write':self.core_tick_irq3_expiry_probe_steps,
                'seed_ticks_per_model_step':self.core_ticks[0].progress,
                'irq3_nibble_cleared_counterfactual':self.core_tick_irq3_masked_probe,
                'real_prefix_irq3_eligibility':'registered by tick_timer_init request_irq '
                                               '(slot 0x19fe0c=0x60661a4, nibble priority 1) '
                                               'before the start write; no later clear',
                'is_synthetic_probe_fixture_not_silicon_state':True}
        if self.core_ticks is not None:
            result['core_tick_configuration']=[timer.report() for timer in self.core_ticks]
            result['assumptions']['core_tick_count_while_cpu1_held']=self.core_tick_count_while_held
            result['assumptions']['core_tick_delivery_requests']=self.core_tick_delivery_requests
        if self.cpu1_held_projection:
            result['cpu1_held_projection']={'initial_control_fixture':self.initial_cpu1_control,
                                            'current_control':self.cpu1_control,'secondary_cpu_executed':self.secondary_cpu_executed,
                                            'release_is_a_stop_not_ready_injection':not self.multicore_enabled,
                                            'kick_rmw_writes':self.cpu1_control_kick_writes}
            if self.cpu1_control_kick_writes:
                result['assumptions']['cpu1_control_kick_bit3']=(
                    'post-release |=0x8 on CPU1 PMU_CON0 (evidence 0x12b732 ack-wait poll loop '
                    'and 0x12c71c CPU1-ready wait) is recorded as a write-only kick bit; the '
                    'reference SDK csfr.h names no PMU_CON0 bits, so bit3 silicon semantics and '
                    'any wake effect are unverified; CPU0 code never reads back or clears it')
        if self.multicore_enabled:
            result['multicore']={'current_core':self.current_core,'cpu1_irq_words':self.irq1.config,
                'cpu1_ipmask':self.irq1.ipmask,'core_icfg':{str(k):v for k,v in self.core_icfg.items()},
                'shared_lock_ownership_is_per_core':True,'ready_flag_injected':False}
            result['multicore']['pmu_con1']={hex(a):{'known_mask':hex(w.known),'known_value':hex(w.value)}
                                              for a,w in self.core_power.items()}
        if self.clock_divider_projection:result['assumptions']['sys_div_configuration_projection']=True
        if self.voltage_configuration_projection:
            result['assumptions']['voltage_configuration_projection']='five scoped P33/CMNG configuration registers; analog voltage/ramp/settling and RAM wait-state timing not modeled'
        if self.p33_analog_config_projection:
            result['assumptions']['p33_analog_config_projection']='two P33 analog-control configuration registers (P3_ANA_CON2/P3_ANA_CON5); explicit synthetic initial word 0 (not an inferred analog state), readback=current register value; analog/voltage behavior not modeled'
        if self.p33_analog_tail_projection:
            # 路 BV:W1[0]/mode 切换 P33 模拟配置族尾段扩展(P3_ANA_CON0/7/8/9/10
            # + P3_CHG_CON0/CON1,AM 容器同款语义)+ P3_ANA_READ 显式钉住
            # (默认;路 CB opt-in fixture 例外,见 p3_ana_read_ldoin_low_fixture)。
            result['assumptions']['p33_analog_tail_projection']=(
                'ten P33 analog/charger configuration registers on the app-core tail '
                '(P3_ANA_CON0/7/8/9/10 = 0xf2c240/0xf2c25c..268, P3_CHG_CON0/CON1/CON2 = '
                '0xf2c068/0xf2c06c/0xf2c070 and P3_L5V_CON0/CON1 = 0xf2c284/0xf2c288 — the '
                'CHG_CON2/L5V trio is reached by the W1[0] base arithmetic r5+0xC/+0x220, so '
                'literal scans miss them, route CB); explicit synthetic initial word 0 (not an inferred '
                'analog state), readback=current register value (route-AM container '
                'precedent); the whole family is read-modify-written by the real '
                'mode-switch function 0x6068152 and the W1[0] function 0x6012e3e '
                'with reads participating in '
                'the written-back values; P3_ANA_READ 0xf2c064 is a live analog input '
                'polled/bit-tested by W1[0] and by default stays strictly blocked '
                '(p3_ana_read_not_modeled) — no 0/full-scale fixture is invented '
                'outside the explicit opt-in ldoin-low fixture; '
                'route-BO corrections: the W1[0] RMW block is the 0xfd5708 PORTP GPIO '
                'face (already modeled by the GPIO projection), the 0xfd5f word block '
                'has zero app+ROM references and is NOT modeled')
            if self.p3_ana_read_ldoin_low_fixture:
                # 路 CB(codex 参谋 gpt-6-astra 只读采纳):常量 live-input 具名
                # fixture,derived_mask/other_bits/duration/evidence_status 四键
                # 显式分离;场景假设与消费者覆盖面全文标注。
                result['p3_ana_read_ldoin_low_fixture']={
                    'address':'0xf2c064',
                    'kind':'constant_external_input_fixture',
                    'value':'0x0',
                    'derived_mask':'0x6',
                    'derived_bits':{'bit1':'LVCMP_DET (ldoin vs vbat comparator; '
                                            'charge.c: ldoin>vbat sets it) — 0 because '
                                            'ldoin ~0V < vbat in the battery-boot scenario',
                                    'bit2':'LDO5V_DET (ldoin detect; charge.c comment: '
                                           'ldoin < 0.6V unplug level clears it) — 0 because '
                                           'no external supply is attached'},
                    'other_bits':'explicitly_zero_filled_not_derived — the five known app '
                                 'readers (0x601201a/0x6012b58/0x6012e6e/0x6033f38/0x6035f54) '
                                 'only ever consume bit1/bit2; bit0 (CHARGE_FULL_FLAG) has zero '
                                 'app consumers; this is NOT a reset-value claim',
                    'duration':'constant_for_this_scenario — every read (the W1[0] polls read '
                               'it 41 times) serves the same word; plug/unplug events and '
                               'debounce dynamics are NOT modeled',
                    'evidence_status':'derived-not-measured',
                    'evidence_provenance':'SDK charge_hw.h bit macros + charge.c threshold '
                                          'comments (pinned commit 67328f4); the QCY W1[0] '
                                          '20-round detect loop matches SDK check_charge_state '
                                          'line by line; one live FD07 read of 0xf2c064 could '
                                          'upgrade bit values to measured',
                    'scenario_assumption':'battery boot with no external supply (ldoin ~0V), '
                                          'consistent with the route-BY RES fixture world '
                                          '(battery present at 3780mV, charge input pin '
                                          'unpowered); the detectors are assumed valid and '
                                          'stable at read time (W1[0] really configures '
                                          'L5V_CON0/1 and CHG_CON0/1 right before polling; '
                                          'comparator timing/hysteresis is not modeled)',
                    'consumer_coverage_note':'the five literal-reference readers are the only '
                                             'known consumers; indirect base+offset paths were '
                                             'not exhaustively excluded, any future consumer of '
                                             'bits outside 0x6 must re-review this fixture',
                    'reads_served':self.p3_ana_read_served,
                    'writes_rejected':True,
                    'default_off_behavior':'p3_ana_read_not_modeled strict stop (the 447,383 '
                                           'route-BV frontier shape)'}
        if self.jl_clock_dcck_projection:
            # 路 CL(sim-dcck):JL 时钟族 DCCK 三字按路 AM 容器同款「显式合成初始字
            # 0 + 读回=当前寄存器值」建模;纯数字时钟配置寄存器,非模拟量(无
            # RES/P3_ANA_READ 式红线)。SDK br28.h __RW + power_clock.c deep-sleep
            # save/restore 双证据;唯一 app 消费者 0x6068152 的显式读全部被掩码+
            # OR 消化,seed 0 下是确定性的位域配置序列。
            result['assumptions']['jl_clock_dcck_projection']=(
                'three JL_CLOCK digital clock-configuration registers '
                '(DCCK_CON0/1/2 = 0xfd0008/0xfd000c/0xfd0010); br28.h marks the whole '
                'JL_CLOCK_TypeDef __RW and the SDK power_clock.c deep-sleep save/restore '
                'lists all three as readable/restorable; explicit synthetic initial word 0 '
                '(not an inferred silicon reset value), readback=current register value '
                '(route-AM container precedent); digital clock enable/divider configuration '
                'only — clock generation/propagation is not modeled; the sole app consumer '
                '0x6068152 masks the read value immediately so the seeded 0 drives a '
                'deterministic field configuration')
        if self.jl_uart_con_projection:
            # 路 F(sim-fd2000):JL_UART CON0|CON1 整字读回=合成初始字 0(空闲)的
            # 读侧投影;bit0 是硬件 busy/空闲状态位(derived-not-measured 显式
            # 假设,非硅测量/非复位值声明)。
            result['assumptions']['jl_uart_con_projection']=(
                'three JL_UART CON0|CON1 words (UART0/1/2 = 0xfd2000/0xfd2100/'
                '0xfd2200); br28.h names JL_UART0_BASE=ls_base+map_adr(0x20,0) and '
                'marks CON0/CON1 __RW; the read helper 0x601d198 is the SDK uart_v1.c '
                'uart_is_idle() (cpu.a/uart_v1.c.o LLVM bitcode matches byte-for-byte: '
                'switch{0:*(0xfd2000),1:*(0xfd2100),2:*(0xfd2200)}; return (word&1)^1) '
                'so the read value is immediately consumed as a bit0 idle test; bit0 is a '
                'hardware busy/idle status bit the firmware never writes (the SDK never sets '
                'bit0: init writes CON0=0x3400, putbyte |=0x2000, other RMW forms all keep '
                'bit0 clear), so the seeded 0 means "idle" — an explicit synthetic initial '
                'word, derived-not-measured (no UART transmission has been initiated in this '
                'prefix), NOT an inferred silicon reset value and NOT a measured idle status; '
                'readback=current register value (route-AM/DCCK container precedent) and the '
                '__RW write side (uart_init CON0=0x3400/|=0x3 ...) is recorded via the generic '
                'last-write register path')
        if self.jl_uart_init_records is not None:
            # 路 M(sim-uartwr):UART 初始化配置写白名单。布局钉死 + 命名修正见
            # JLUartInitWriteRecords 文档串;此处把「写记录 derived-not-measured、
            # 运行效果不建模」写进 assumptions。
            result['assumptions']['jl_uart_init_write_records']=(
                'JL_UART init config words (CON1 +4 / BAUD +8 / OTCNT +0x10 / RXSADR +0x1c / '
                'RXEADR +0x20 / RXCNT +0x24 / CON2 +0x2c per UART base 0xfd2000/0xfd2100/'
                '0xfd2200) released as write-only whole-word records (lp_timer SYS_DIV / '
                'P3_PORT_SEL precedent). LAYOUT PINNED (route-F naming corrected): the compiled '
                'SDK object uart_v1.c.o declares JL_UART_TypeDef as {i32 x12} — one 32-bit word '
                'per named member at +4k — and the actual QCY firmware write sequence matches it '
                'point-for-point (0x6034466 CON0/+0, 0x603446c CON1/+4, 0x6034472 CON2/+0x2c, '
                '0x601d286 BAUD/+8, 0x601d1f2 OTCNT/+0x10, 0x6034668..cc RXSADR/RXEADR/RXCNT); '
                'the public br28.h packed u16/u8 offsets (BAUD u16 at +4) DISAGREE with this '
                'build and are not used — 0xfd2004 is the CON1 word, not BAUD|BUF, and the real '
                'BAUD divider write goes to 0xfd2008. All released writes are full-word '
                'st.w-family stores whose values are real executed instruction results '
                '(derived-not-measured); readback and RMW of these words stay strictly blocked; '
                'TXADR/TXCNT/BUF/HRXCNT are NOT whitelisted (a TXCNT write or CON0 status poll '
                'would be a started-hardware-activity signal and stops honestly); no baud-rate '
                'runtime effect, RX/TX DMA data flow or UART IRQ is modeled — this records '
                '"initialization config writes executed" only, NOT verified UART '
                'transmission/reception')
            if self.jl_uart_init_records.con1_readback:
                # 路 AA(sim-con1rb):CON1 读回=最后写值投影。条件假设(读回=
                # 软件配置值,derived-not-measured)与 RMW 完整路径写进 assumptions。
                result['assumptions']['jl_uart_con1_readback']=(
                    'JL_UART CON1 (+4 per UART base 0xfd2000/0xfd2100/0xfd2200) upgraded from '
                    'the route-M write-only whitelist to "write records + readback = last '
                    'firmware-written value" (lp_timer CON route-AL / fd31 ADC CON route-BN '
                    'precedent). FIRST REAL CONSUMER: the uart_fifo_reset compound RMW at '
                    '0x60340d8 rmw.and.nmodimm [r0+4]&=0xFFFFFFEF — bit4 is the RX-DMA '
                    'restart bit (SDK uart_v1.c: |=16 -> RXSADR/RXEADR/RXCNT -> &=-17 inside '
                    'the OS unmask critical section) and the read value feeds the write-back. '
                    'CONDITIONAL ASSUMPTION (codex risk point): br28.h __RW only proves the '
                    'SDK believes CON1 reads back; it cannot prove there are no '
                    'hardware-updated or self-clearing bits — "readback == the '
                    'software-configured value" is a derived-not-measured hypothesis, and '
                    'bits other than bit4 WOULD misread if silicon self-updates them. In this '
                    'prefix CON1 was whole-word written 0 by uart_init (0x603446c) before the '
                    'first read: the projected RMW reads 0 and writes back 0&0xFFFFFFEF=0 — '
                    'a trivially zero-invention release, and the RMW still runs the FULL '
                    'generic MMIO path (mem.read -> bit-AND -> mem.write, the write-back '
                    'lands in the records). Scope: ONLY CON1 — BAUD/OTCNT/RXSADR/RXEADR/'
                    'RXCNT/CON2 readback stays strictly blocked and CON0 keeps its own '
                    'route-F projection; never-written CON1 words (UART1/2) still stop '
                    'honestly; no UART IRQ, RX/TX DMA data flow or baud-rate runtime effect '
                    'is modeled — NOT verified UART transmission/reception')
        if self.jl_omap_records is not None:
            # 路 R(sim-gpioface):JL_OMAP 逐引脚输出功能选择字白名单。识别链
            # 见 JLOmapWriteRecords 文档串;此处把「写记录 derived-not-measured、
            # 运行效果不建模」写进 assumptions。
            result['assumptions']['jl_omap_write_records']=(
                'JL_OMAP per-pin output-function select words (ls_base+map_adr(0x58,0)='
                '0xfd5800, the SDK io_omap.h crossbar bank: PA0..15/B0..11/C0..8/G0..8/'
                'USBDP/USBDM/PP0, 49 words at the driver 4-byte stride) released as '
                'write-only whole-word records (lp_timer SYS_DIV / P3_PORT_SEL / route-M '
                'jl_uart_init_write_records precedent). IDENTIFICATION PINNED: the SDK driver '
                'table gpio_out_regs {0xfd5800,0xfd5840,0xfd5870,0,0,0xfd5894,0xfd58c0} '
                '(gpio_hw.c.o bitcode) matches the QCY firmware table at 0x609EA9C '
                'byte-for-byte, gpio 224/225 (IO_PORT_DP/DM, gpio_hw.h) special-case to '
                '0xfd58b8/0xfd58bc on BOTH sides (firmware resolver 0x600127a and SDK '
                'gpio2crossbar_outreg), and the observed first value 0x9f is FO_UART0_TX='
                '((39<<2)|BIT(1)|BIT(0)) from io_omap.h (uart_init TX pin mux). All released '
                'writes are full-word st.w-family stores whose values are real executed '
                'instruction results (derived-not-measured); readback and RMW of these words '
                'stay strictly blocked; no crossbar signal routing, output-level effect or IRQ '
                'is modeled — this records "pin-mux configuration writes executed" only, NOT '
                'verified UART transmission/reception or USB function')
        if self.jl_imap_records is not None:
            # 路 V(sim-imapwr):JL_IMAP 外设输入功能选择字白名单。识别链
            # 见 JLImapWriteRecords 文档串;此处把「写记录 derived-not-measured、
            # 运行效果不建模」写进 assumptions。
            result['assumptions']['jl_imap_write_records']=(
                'JL_IMAP peripheral-input function select words (ls_base+map_adr(0x5c,0)='
                '0xfd5c00, the SDK io_imap.h crossbar input bank: FI_GP_ICH0..FI_CHAIN_RST, '
                '63 words at the csfr.h __u8->unsigned-int 4-byte stride) released as '
                'write-only whole-word records (P3_PORT_SEL / route-M jl_uart_init_write_'
                'records / route-R jl_omap_write_records precedent). IDENTIFICATION PINNED: '
                'the gpio_hw.c.o PFI_* enumerators (gpio_hw.h `((u32)(&(JL_IMAP->FI_*)))`) '
                'walk 16604160..16604408 member-by-member at the 4-byte stride, and the '
                'input-channel resolver agrees on BOTH sides (SDK gpio2crossbar_inport: '
                'gpio 224->47 USBDP_IN with the gpio_in_regs {1,17,29,0,0,38,49} channel '
                'table; firmware twin 0x60012e8 with the byte-identical table at 0x609EAB8) '
                '— the observed first value 0x2f=47 is USBDP_IN from io_imap.h (uart_init RX '
                'pin mux: UART0 RX shares the USB DP pin with TX). The whole-image app scan '
                'shows only full-word stores through these words (mux write 0x6001342, '
                'disable-zero 0x601968c) and ZERO readback. All released writes are full-word '
                'st.w-family stores whose values are real executed instruction results '
                '(derived-not-measured); readback and RMW of these words stay strictly '
                'blocked; no crossbar signal routing, input-level effect or IRQ is modeled '
                '— this records "input-mux configuration writes executed" only, NOT '
                'verified UART transmission/reception or USB function')
        if self.pll1_configuration_projection:result['assumptions']['pll1_con3_configuration_projection']='RW config only; analog PLL1 not modeled'
        if self.cache_status_probe:
            result['assumptions']['cache_status_words_fixture']={hex(a):hex(v) for a,v in sorted(self.cache_status_words.items())}
            result['assumptions']['cache_contents_and_coherence_simulated']=False
        if self.sfc is not None:result['sfc_read_bus']=self.sfc.report()
        if self.sfc_secondary is not None:result['sfc_read_bus_secondary']=self.sfc_secondary.report()
        if self.crc_engine is not None:result['hardware_crc_projection']=self.crc_engine.report()
        if self.sfc_clock is not None:result['sfc_clock_gate_projection']=self.sfc_clock.report()
        if self.icu is not None:result['icu_initialization_projection']=self.icu.report()
        if self.dcu is not None:result['dcu_cmo_projection']=self.dcu.report()
        if self.l1p is not None:result['l1p_idle_projection']=self.l1p.report()
        if self.icu_idle is not None:result['icu_idle_projection']=self.icu_idle.report()
        if self.software_irq is not None:result['software_irq_commands']=self.software_irq.report()
        if self.debug is not None:result['debug_execution_projection']=self.debug.report()
        if self.reset is not None:result['reset_history_projection']=self.reset.report()
        if self.mpu is not None:result['mpu_configuration_projection']=self.mpu.report()
        if self.port_initialization_projection:
            result['port_initialization_projection']={
                'input_words_fixture':{hex(a):hex(v) for a,v in self.port_input_words.items()},
                'physical_initial_values_measured':False,
                'initial_usb_io_word_fixture':self.initial_usb_io_word,
                'usb_disable_writes':[hex(a) for a in self.usb_disable_writes],
                'usb_controller_status_transfer_or_pin_electrical_model':False,
                'iomap_configuration_only':True,'usb_io_unknown_bits_preserved':True}
        if self.counts.get('opaque_pwm_configuration_reads',0):
            result['assumptions']['pwm_configuration_reads']='R0 remains unknown through guarded loop/return until actual overwrite; no value fabricated'
        if self.counts.get('opaque_mem_pwr_con_reads',0):
            result['assumptions']['mem_pwr_con_read']=(
                'the sole firmware readback of MEM_PWR_CON (0x6067844 ld.w, right after the '
                'bit-field helper returns) is a proven dead read: its value is overwritten by the '
                'next instruction 0x6067846 ld.bu r0=b[r4] before any consumption, so R0 stays '
                'opaque until that overwrite and no read value is fabricated')
        if self.oscillator_configuration_projection:
            result['assumptions']['oscillator_configuration']='five scoped WLA RW configuration registers; analog oscillator/lock/settling and clock propagation not modeled'
            result['assumptions']['oscillator_status_word_fixture']=self.oscillator_status_word
        if self.timer_lsb_ticks_per_step is not None:
            result['assumptions']['timer_lsb_ticks_per_step']=self.timer_lsb_ticks_per_step
        if self.counts['opaque_port_selection_reads']:
            result['assumptions']['port_selection_read']='opaque R0 until validated caller overwrites it; value never supplied as zero'
        return result
