"""Evidence-scoped BR28 register models. No device access or analog simulation.

Sources and remaining hypotheses: docs/hardware-contracts.md. Unknown reads,
capture/PWM modes, reset stimuli and actual CPU interrupt delivery still stop.
"""
from collections import Counter
import operator

from probe_boot import Blocked, IrqDeliveryRequest

TIMER0=0xfd0400
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
    def __init__(self,initial_con=None,progress=None):
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

    def read(self,offset):
        if self.unresolved_overflow:raise Blocked('core_tick_overflow_requires_resolution')
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
                if self.prd in (0,0xffffffff):raise Blocked('core_tick_period_edge_unverified',period=hex(self.prd))
                if self.cnt>self.prd:raise Blocked('core_tick_count_exceeds_period')
            self.con=value&1
            if value&64:self.pending=False
        elif offset in (4,8):
            if self.con is not None and self.con&1:raise Blocked('active_core_tick_reconfiguration_unverified')
            if offset==4:self.cnt=value
            else:self.prd=value
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
        return {'con':self.con,'cnt':self.cnt,'prd':self.prd,'pending':self.pending,'writes':self.writes,
                'virtual_source_ticks_per_step':self.progress,'expirations':self.expirations,
                'counter_projection_enabled':self.progress is not None,'irq_delivery_simulated':self.acknowledgements>0,'initial_control_fixture':self.initial_con,
                'acknowledgements':self.acknowledgements,'physical_initial_state_measured':False,'pending_bit7_readback_proven':False,
                'unresolved_overflow':self.unresolved_overflow,
                'counter_projection':'PRD+1 from SDK formula; no inferred core-held clock gating or physical phase'}


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
    def __init__(self,readback_projection=False):
        self.bits=PartialWord();self.pending=None;self.disable_writes=0;self.clear_writes=0
        # con_readback_projection(opt-in,需 gpcnt_cleanup_projection,路 CG):
        # GPCNT_CON(0xfd1100,SDK br28.h JL_GPCNTTypeDef CON __RW/NUM __RO)读回
        # =固件自己已执行写出的位粒度已知状态(lp_timer/fd31 CON「读回=最后
        # 写值」先例的位粒度变体——本前缀 CON 只有 disable/clear 两个位 RMW,
        # 无整字写,故返回部分值对象而非整数)。未知位消费诚实停,绝不臆造。
        self.readback_projection=readback_projection;self.con_reads=0

    def rmw(self,kind,operand):
        if kind=='and' and operand&0xffffffff==0xfffffffe:
            self.bits.apply('and',operand);self.disable_writes+=1
        elif kind=='or' and operand==64:
            if not self.bits.known&1 or self.bits.value&1:raise Blocked('gpcnt_disable_state_unknown')
            self.pending=False;self.bits.apply('and',~128);self.clear_writes+=1
        else:raise Blocked('gpcnt_counting_or_configuration_not_modeled',operation=kind,operand=hex(operand&0xffffffff))

    def read(self):
        if self.readback_projection and self.bits.known:
            # 读回=固件自己写出的已知位(bit0=0 由 app 的 &=0xFFFFFFFE 强制,
            # bit7=0 由 |=0x40 后的立即清投影)。本前缀唯一读者=任务 0x6069538
            # 的 bit0 使能判定(beq.and.ri,AND 掩码把其余位强制为已知 0 → 单
            # bit 谓词可判);任何未知位消费经 GpcntConValue 诚实停。
            self.con_reads+=1
            return GpcntConValue(self.bits.value,self.bits.known)
        raise Blocked('unknown_gpcnt_configuration_bits',known_mask=hex(self.bits.known))

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

    def report(self):
        return {'con':self.con,'key_phase':self.key_phase,'commands':self.commands,'feeds':self.feeds,
                'command_values':self.command_values,'command_values_truncated':self.commands>len(self.command_values),
                'elapsed_nominal_units':self.elapsed,'fraction_remainder':self.remainder,'expired':self.expired,
                'assumptions':{'nominal_units_per_model_step':self.progress,
                    'period_units':'1 << time_code, from SDK query table; hardware calibration unverified',
                    'counter_phase':'fresh enable/disable reset elapsed; feed rearms full period; explicit projection, silicon phase unmeasured',
                    'counting_before_first_explicit_CON_initialization_modeled':False,
                    'bit16_readback':'configuration projection omits command bit; silicon readback unmeasured',
                    'key_policy':'one CON command per observed KEY pair; hardware key lifetime unverified',
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
                 power_init_config_rmw=False,power_init_rstcon_mask_sets=False,
                 power_init_wkup_dly_rmw=False,
                 lp_timer_init_projection=False,lrct_trim_done_projection=False,
                 lrct_trim_result_value=None,lp_timer_con_readback_projection=False,
                 watchdog_projection=False,watchdog_progress=None,efuse_read_projection=False,efuse_words=None,
                 oscillator_configuration_projection=False,oscillator_status_word=None,timer_lsb_ticks_per_step=None,
                 gpcnt_cleanup_projection=False,gpcnt_con_readback=False,core_tick_configuration=False,
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
                 p33_canary_projection=False,p33_canary_first_read_words=None,
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
        self.gpcnt=GPCNTCleanup(readback_projection=gpcnt_con_readback) if gpcnt_cleanup_projection else None
        if initial_core_tick_controls is not None and len(initial_core_tick_controls)!=2:raise ValueError('need two explicit core tick controls')
        if core_tick_progress is not None and len(core_tick_progress)!=2:raise ValueError('need explicit progress for two core ticks')
        self.core_ticks=[CoreTickStopped(value,rate) for value,rate in zip(initial_core_tick_controls or (None,None),
                       core_tick_progress or (None,None))] if core_tick_configuration else None
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
            (self.p33_awkup is not None and self.p33_awkup.handles(address)) or
            (self.p3_ls is not None and self.p3_ls.handles(address)) or
            (self.fd31_adc_con is not None and (self.fd31_adc_con.handles(address)
                                                or self.fd31_adc_con.handles_res(address))) or
            (self.p33_analog_tail_projection and address==P3_ANA_READ) or
            (self.p33_canary is not None and self.p33_canary.handles(address)) or
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
        if self.jl_audio_adc_reset_records is not None and self.jl_audio_adc_reset_records.handles(address):
            self.counts['reads']+=1
            return self.jl_audio_adc_reset_records.read(address)
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
        # 金丝雀树/低功耗域(路 BV):P3_VLVD_CON/P3_PCNT_CON 只有显式 fixture
        # 覆盖的首次读返回具名字,其余读(未覆盖/写后/重复)一律严格停
        # (路 BB 同款;显式 kind 便于诊断)。
        if self.p33_canary is not None and self.p33_canary.handles(address):
            self.counts['reads']+=1
            return self.p33_canary.read(address)
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
        if self.handles_gpcnt(address):raise Blocked('gpcnt_full_control_store_not_modeled')
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
        st.b 半),需要独立的字节粒度入口——只路由到 p33_window 投影,其他
        地址一律 False(字节写到未建模地址继续走普通路径诚实停)。
        """
        return self.p33_window is not None and self.p33_window.handles(address)

    def write_byte(self,address,value):
        """字节写落进 P33 窗无名寄存器族的 write-only 记录(不经读回)。"""
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
        """
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
        if self.p33_awkup is not None:result['p33_awkup_projection']=self.p33_awkup.report()
        if self.p3_ls is not None:result['p3_ls_projection']=self.p3_ls.report()
        if self.fd31_adc_con is not None:result['fd31_adc_con_projection']=self.fd31_adc_con.report()
        if self.p33_canary is not None:result['p33_canary_projection']=self.p33_canary.report()
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
