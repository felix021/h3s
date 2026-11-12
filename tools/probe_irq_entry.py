#!/usr/bin/env python3
"""Minimal IRQ127 software-interrupt entry on the real startup prefix.

The scheduler IPI (pending 0x8 set by the actual QCY code at 0x60644a6)
reaches an opt-in CPU entry: RETI loads the interrupted PC and execution
continues at the vector slot written by the firmware. Entry banks, RETI
load and pending acknowledgement are explicit assumptions; generic IRQ
entry, IDLE wake and hardware frame writes stay unmodeled. The trailing
irq125_repeat_inert scenario pins that the opt-in repeat_irq_delivery
projection (handler return re-enables delivery) stays inert on this prefix
until the firmware actually pends a second time. No flash approval.

The trailing crc_bankb_erased_fixture scenario layers an explicit unverified
erased-bank fixture (four evidence-bounded windows of 0xFF: the 32-byte header
read of the probe-table entry 5, the 20-byte window-table structure the qcy
consumer enumerates at flash offset 0x1e0000, the full declared VM region
0x2000 bytes at flash offset 0x1da000, and the 8-byte first-record header at
window offset +0x200 the cfg-tool/btif storage backend init 0x6060d7c parses)
on top of crc_engine_roundtrip: the consumers really compute the header CRC /
parse the erased headers / blank-scan the VM halves / range-check the erased
record header and select their benign branches; crc_engine_roundtrip itself
keeps stopping honestly on the unmapped bank-B read. The VM window is declared
structurally (not incrementally) because its consumer is a data-dependent
byte-by-byte blank scan over each whole half — a partial window would just
move the unmapped stop one byte later. After the blank scan confirms an
all-erased VM, the chain meets the misaligned-source ROM memcpy variant
(insert.rrii.p + ld.bu.pin compose pairs), runs it through the opt-in
exact_parallel_insert_load projection, then the record-header init rejects
the erased 8-byte header (only its first 4 bytes are read). The halfword
pair store that used to stop there (mov.rr.p with a halfword-store second
half, an upstream model-internal merge read of uninitialized stack RAM) now
runs through the extended exact_parallel_stores gate as a byte-granular
partial write (two target bytes written, same-word neighbours untouched), so
the chain really enters app_core's power_init. The opt-in p11_power_init layer
then walks the whole P11 dependency list of power_init: the 0xf2c05c
configuration RMW (sole firmware-wide reference, recorded like the 0xf2c054
precedent without any read-back), the q32s PMU_CON 0xf2a0c0 release RMW and
the P11_CLOCK SYS_DIV/CLK_CON0 word stores (write-only records), the real
"flash/res/p11_code.bin" resolution and its 12112-byte common-segment copy
into the P11 RAM window 0xf20080 (snapshot cross-checked byte-exact against
the CRC-valid resource), the PWR_CON bit5 request and its post-flag-clear
poll exit through the p11_lpwr_response projection (the real-prefix test of
the P11 request/response hypothesis), and finally stops honestly at the
first heap-info byte read 0xf27f4b (P2M message area +0xa, value written by
P11 init and unproven): the explicit p11_heap_info_bytes fixture is the only
way past this boundary.

The trailing p11_heap_experimental_config scenario layers the STATICALLY
DERIVED heap-info candidate (P11_HEAP_INFO_DERIVED: begin 0x3d0c / size
0x39f4, link-time immediates in the p11_code.bin common segment, see the
derivation chain next to the constant) on top of crc_bankb_erased_fixture
through that explicit fixture. The value is derived, not measured: the q32s
init path is statically decoded (raw wide-immediate bytes cross-checked),
never executed here, and every conclusion past 289,849 in this scenario is a
CONDITIONAL pass that must not be reported as verified. With the candidate
bytes the chain consumes them for real (0x60679ba/be first read assembles
begin 0x3d0c, r0=0xf20000|begin=0xf23d0c, the region call 0x60114ca
protects [0xf20000, heap begin), the four bytes are re-read at 0x60679ee..f4,
then the pmu_task creation 0x6067242 really round-trips the scheduler: CPU0
takes two more irq127 deliveries and RTIs into the pmu_task entry 0x6068f0c
before power_init resumes). The next boundary is the first main-core write
into the P11 M2P message area (the pair at 0x6067a74, SDK lp_ipc.h
M2P_RVD2PVDD_EN, executed value 0) whose pair-store word-merge read needs the
M2P window: the scenario maps the SDK-declared M2P area 0xF27F80..0xF27FE0
(p11_app.h M2P_MESSAGE_RAM_BEGIN..+0x60) as ordinary writable message RAM —
write real, read back the last value, which the chain itself exercises
(0x60674b8 writes the LRC period byte at M2P+0 and reads it back three times
to derive the 50us/200us/600us timer bytes) — plus the opt-in byte-store
parallel-pair sub-gate (exact_parallel_byte_stores, same pattern as the
halfword precedent) and the opt-in lp_timer_init_projection write-only
records for the LP-timer init tail (P11_LPTMR0 CON0/CON1/CON2 0xf2a900..08
word stores, the unnamed PMU-family 0xf2c04c/0xf2c050 word stores, and the
JL_LRCT CON 0xfd3600 bit RMWs, all write-only). The LRC-trim boundary that
used to stop at 300,236 (the CON readback poll) is now crossed by the opt-in
lrct_trim_done readback projection (bit7 projected as trim-complete, the
SFC PLL-lock bit15 pattern): the disassembled poll shape is
read CON -> beq.and.ri bit0 (not enabled -> skip) -> re-read CON ->
beq.and.ri bit7 branching BACK while bit7 is clear ("branch if
(r & (1<<n)) == 0", hardware-confirmed ISA), so the firmware waits for
bit7 to be SET, and the projection returns the recorded RMW bits
(bit0 enable=1, bit6=1) with bit7 forced to 1 — the poll really exits on
its first iteration. The trim-result register JL_LRCT.NUM at 0x6067520
(0xfd3604, SDK __RW) is a silicon measurement with no measured value, so its
read stays strictly blocked by default (300,240); the scenario then feeds the
EXPLICIT PROBE VALUE 38400 (LRCT_TRIM_RESULT_PROBE, derived-not-measured:
the consumer divides the firmware constant 7,680,000,000 = 0x1_C9C38000 by
NUM to get the LRC frequency in Hz, and 7,680,000,000/38,400 = 200,000, the
firmware's own nominal constant, so the probe is the identity count of a
perfectly nominal 200 kHz LRC). The chain then really runs the consumer
math (quotient/first-run history fill/average into 0x129D88, trim re-arm),
the second LRC period write (same M2P bytes 05/0a/28/78 re-derived from the
"measured" 200000 Hz) and enters the first task creation at 0x6067ae0
(entry 0x606750c = the trim function itself, priority 10); inside the
task-create path a timer bring-up wrapper (0x6000898 with r0=0xa) configures
TIMER0 and the start RMW at 0x600087c merges CON to 0x1401 — clock source
field 5, named STD12M (standard 12 MHz) from the pinned SDK gptimer_hw.h
macro, the SDK driver IR (src<<10|div<<4, std_clk==12 -> field 5) and the
app's own conversion/divider tables plus PRD=119999 closing the 10 ms
period; the opt-in timer0_std12m_source lets it count at 3/4 of the RC16M
reference tick (12 MHz nominal, not a per-chip measurement). The chain
then really executes the first task-create return (handle halfword
h[0x129D94]=1) and enters the second task creation at 0x6067b12 (entry
0x6069538); after its create call returns, the else branch (gate byte
b[r6+98] neither 0 nor 1) calls 0x6067668 with r0=2 and the LP-timer
configuration RMW on P11_LPTMR0 CON2 (0xf2a908) hits the lp_timer
write-only boundary at the first read, stopping honestly at 301,540; the
opt-in lp_timer_con_readback_projection (SDK p11_sfr.h marks the CONs __RW)
returns the last-written value so that RMW runs through. The following
call 0x60677a4 (the power_init P33 analog-config routine) then reads
P3_ANA_CON5 (0xf2c254) for its 0x3FC7 clear RMW: the opt-in
p33_analog_config_projection covers P3_ANA_CON2/P3_ANA_CON5 with the same
explicit synthetic initial word 0 as the VOLTAGE_CONFIG precedent (the
p33_sfr.h header is address macros only, no __RW/__RO/__WO annotation),
and the chain next reads P3_RST_CON0 (0xf2c054) with two and-masks
(0xFF7F at 0x60677fa clearing bit7, 0x7FBF at 0x60677fe clearing bit6)
different from the main reset-init mask 0x3fdf (clears bit5): the opt-in
power_init_rstcon_mask_sets extends the reset projection's accepted mask
set for 0xf2c054 to the three observed masks (applied bitwise, unknown-
preserving, no SDK bit definition invented), and the chain then stops
honestly at 301,651 on the P3_WKUP_DLY (0xf2c114) &=0x7F8 RMW — an
independent, not-yet-modeled P33 register, the next decision; power_init's
return still lies beyond. No new scheduler round trips occur.
The crc_bankb_erased_fixture frontier itself stays pinned at 289,849
without the fixture.
"""
import argparse
import json
from pathlib import Path

from probe_dualcore import dual_case
from probe_task_returns import EMPTY_PRIORITIES
from probe_arch_startup import candidate_view, REPAIRED_SHA, FAILED_SHA
from probe_boot import application, flash_image, check_rom, ROM_SHA, OFFICIAL_SHA, APP_SHA
from probe_boot_handoff import package_header
from probe_pi32v2 import ROOT, PIN, upstream, require

HANDLER_PSR_FRAMES={0x12ad30:'psr_push',0x12ad60:'psr_pop',0x12adae:'psr_push',0x12adde:'psr_pop',
                    0x12ad6e:'frame12_push',0x12ada0:'frame12_pop',
                    0x60662c8:'frame12_push',0x606633e:'frame12_pop'}

# bank B 头 fixture 的显式范围:find_partition 探测表 0x060A0FA4 第 5 项
# (偏移 0x100000)的 32 字节头读取窗(CPU=0x06000000+偏移-0x2020 头)。
# 只覆盖消费者分析实测的读取范围;见 irq_case 的 erased_bankb 注释。
ERASED_BANKB_HEADER_RANGE=(0x60fdfe0,32)

# bank B 第二数据窗 fixture 的显式范围:qcy 消费函数 0x6060a4a 缓存进全局
# 0x126544 的 window(0x061ddfe0=0x06000000+0x1e0000-0x2020,VM 尾与 TON
# 0x1e1000 之间)上,u16 对表 walker(0x60609be,表 0x60a1e8a,app flash 公开
# 字节)枚举的全部条目结构范围:两项 {id=0x66,size=6}/{id=0x6e,size=6},
# 每项 on-flash 形状=4 字节头+size 字节数据,全长 (4+6)*2=20 字节——范围
# 由 app 自己的表声明,不是任意拍的;见 irq_case 的 erased_bankb 注释。
ERASED_BANKB_WINDOW_RANGE=(0x61ddfe0,20)

# bank B 第三数据窗 fixture 的显式范围:VM 区全长 0x2000 字节(CPU 可见
# 0x61d7fe0..0x61d9fe0=0x06000000+0x1da000-0x2020 起 0x2000)。范围是结构声明
# 的:qcy_boot_info 自己的 vm 字段(vm_saddr=0x1da000、vm_size=0x4000)、GET
# 消费者 0x6060f9a 自己的 clamp(>=8192→8192)与 4K 对齐切半算法([0x129070+0]=
# 0x1da000、[+0xC]=0x1db000、[+0x10]=0x1dc000,每半 0x1000)、空白检查自己传入
# 的 len(0x600a79e 每半 len=0x1000)——不是任意拍的。消费者分支图(反汇编+
# trace 钉死):0x6061094/0x606109e 先读两半 magic 各 4 字节,0xFFFFFFFF(擦除态)
# →0x6061106→word2≠0xFF5A5A5A→0x6061120 call 0x600a7b6:0x600a78a 逐字节扫
# 每半 len 字节检查全 0xFF(实测 0x600a78c 执行 8192 次、读 0x61d7fe0..0x61d9fdf,
# 遇非 0xFF 立即中止)——数据依赖的结构性全扫描,逐窗扩 fixture 只会把 unmapped
# 停点向后挪一个字节,故按结构声明范围一次性映射(同第二窗「表结构=擦除态」
# 语义)。全空白分支良性:两处格式化写 call(0x600a7da/0x600a7f6)只在半区部分
# 写入且头无效时执行、本前缀 trace 零执行;随后设置 VM 空状态、从 app flash 表
# 0x60a9344 重建位图、return 0。见 irq_case 的 erased_bankb 注释。
ERASED_BANKB_VM_RANGE=(0x61d7fe0,0x2000)

# bank B 第四数据窗 fixture 的显式范围:window(0x61ddfe0)+0x200 处的记录区
# 首条目头 8 字节(0x61de1e0..0x61de1e7=0x06000000+0x1e0200-0x2020 起 8 字节)。
# 消费者是 cfg_tool/btif 存储后端 ops 表(28 字节记录,0x60c32c8 起,成员含
# 0x6060a4a/0x6060c3c/0x6060f9a/0x6060d7c)的 init 成员 0x6060d7c——平台 init
# 尾部 0x601206c 表遍历的最后一项:thunk("flash/res/cfg_tool.bin") 与
# thunk("flash/app/btif") 各取描述符,把状态块写到全局 0x12655C(+0 有效=1、
# +4/+8 cfg_tool 内容界、+12 条目有效位清 0、+16 游标=window+0x200、+20 长度),
# 然后解析 window+0x200 的 8 字节首条目头 {u32 len;u32{b0 crc,bits20+ size}}:
# 0x6060e0e 对 len 做无符号范围检查((len-1)>2047 拒绝)、0x6060e2a 对
# size 做 >800 检查、都过了才 call 0x6000f0c 对数据区算 CRC 并与头 b0 比较,
# 匹配才置 +12 条目有效位=1 并推进游标。0xFF 擦除态下(len=0xffffffff,
# (len-1)=0xfffffffe 无符号 >2047)真实代码只读头 4 字节就在 0x6060e0e 拒绝、
# 返回 0(动态 trace 钉死:bytes 4-7 不读、CRC call 不执行、无 flash 写)——
# +12 有效位保持 0,把按 index 找条目的 walker 0x60112c8(入口判 b[0x126568]
# !=1 即跳过)挡在门外。fixture 覆盖消费者自己解析的完整 8 字节头结构
# (同第二窗「表结构=擦除态」语义:按结构整体声明,避免把一个头切成一半已知
# 一半未知),不扩 +0x208 之后的数据区/后续条目——擦除态分支到不了,无任何
# 全记录区扫描证据(记录区其余消费者:0x60112c8 被 +12=0 门控、0x6060c6a/
# 0x600a412 的 walk 基址=getter(103)+OTP 槽 827(本前缀 find_partition 失败
# 已清 0,0x600a412 入口判槽 828==0 直接返回 -250)、表区写路径 0x6060b80 与
# VM 窗口 0x6060f9a 各自独立)。见 irq_case 的 erased_bankb 注释。
ERASED_BANKB_RECORD_RANGE=(0x61de1e0,8)

# power_init 的 p11_code.bin 公共段拷贝目的窗:主核把 P11 公共代码段拷进 P11 RAM
# (0x6067990..0x6067996 字节循环,destination li 0xf20080)。范围由资源自己声明:
# p11_code.bin(analyze_p11 钉死的 12248 字节真实资源,CRC 全过)BANKCB 头 entry0
# {address=0x80(p11 本地地址,主核映射 0xf20080), bytes=12112}——即
# 0xf20080..0xf20080+12112=0xf22FD0。拷贝源是同一资源在 bank A flash 的已知
# 字节(flash_image 只读区已映射);模型只提供可写目的窗(scoped harness region,
# 与 CORE1_STACK 同类),P11 RAM 其余部分(含 P2M 消息区 0xf27f4a 堆信息)不映射
# ——堆信息读仍按 p11_heap_info_bytes 显式 fixture/严格停语义。
P11_COMMON_CODE_WINDOW=(0xf20080,0xf20080+12112,'p11_common_code_window')

# P11 RAM M2P 消息区(主核→P11 方向)的可写窗。范围由 SDK p11_app.h 自己声明:
# P11_RAM_END(0xF28000)-P11_POWEROFF_RAM_SIZE(0x14+0xc=0x20)=0xF27FE0(poweroff
# 区),再减 M2P_MESSAGE_SIZE(0x60)=M2P_MESSAGE_RAM_BEGIN=0xF27F80;lp_ipc.h 逐
# 字节命名锚点(M2P_LRC_PRD=+0x00、M2P_LRC_TMR_50/200/600us=+0x02..0x04、…
# M2P_RVD2PVDD_EN=+0x56、M2P_USER_* +0x58..0x5f)。语义=普通共享消息 RAM,不是
# 外设寄存器:主核写真实生效、读回最后值——power_init 0x6067a74 首写
# b[0xF27FD6](M2P_RVD2PVDD_EN,实测落盘 0x00:静态看 0x6067a6c `r4=1` 在被
# icfg 位图跳过的块内,实际路径 r4=0),0x60674b8(写 LRC 周期字节)随后
# 自己回读 b[0xF27F80] 三次推导定时器分频字节,「写后读回」由真实代码消费。P2M 区
# (0xF27F40..0xF27F80,P11→主核,含堆信息 4 字节)保持不映射:本前缀固件不写
# P2M(堆信息仍按 p11_heap_info_bytes 显式 fixture 读);若后续前缀出现 P2M
# 写,再同语义扩窗。
P11_M2P_MESSAGE_WINDOW=(0xF27F80,0xF27FE0,'p11_m2p_message_area')

# 堆信息 4 字节的静态推导候选(P2M 消息区 +0xa,0xf27f4a..4d)。推导链(全部
# 公开工件可复算,本注释只记录结论;「静态推导」不等于「硅测量」):
# ①p11_code.bin 资源(analyze_p11 钉死、CRC 全过)公共段 entry0 12112 字节里,
#   P11 本地地址 0xa12 处 `mov r0,#0x3d0c`(原始字节 c0ff 0c3d0000,wide 立即数
#   LE32 独立验证)+0xa18 uxth +0xa1a `mov r5,#0x7f4a`(c5ff 4a7f0000)+0xa20
#   `sb r0,[r5]`(低字节 0x0c→0x7f4a)+0xa22 lsr 8+0xa24 `sb r0,[r5+1]`(0x3d
#   →0x7f4b);随后 0xa26 `mov r0,#0x39f4`(c0ff f4390000)+0xa2e `sb`(0xf4
#   →0x7f4c)+0xa30 lsr 8+0xa32 `sb`(0x39→0x7f4d)——堆 begin/size 是 P11
#   代码里的链接期立即数,不是运行时计算。
# ②SDK 固定头对照:lp_ipc.h P2M_P11_HEAP_BEGIN_ADDR_L/H=P2M_MESSAGE_ACCESS
#   (0x00A/0x00B)、P2M_P11_HEAP_SIZE_L/H=(0x00C/0x00D);p11_app.h
#   P11_HEAP_BEGIN=P11_RAM_BASE+(H<<8|L)、P11_HEAP_SIZE=(H<<8|L)——字节序
#   消费语义与此处 sb 序列逐字节一致。
# ③独立算术互证:p11_app.h 布局 P2M_MESSAGE_RAM_BEGIN=P11_RAM_END(0xF28000)
#   -0x20(poweroff)-0x60(M2P)-0x40(P2M)=0xF27F40(+0xa=0xF27F4A,与
#   P11_HEAP_INFO 一致);heap 末=P11_RAM_BASE|0x3d0c+0x39f4=0xF27700,恰好
#   等于 SDK 布局的 VAD_DVAD_CONFIG_BEGIN(0xF28000-0x20-0x60-0x40-0x20
#   -0x780(VAD cbuf)-0x50(AVAD cfg)-0x50(DVAD cfg)=0xF27700)——heap size
#   精确取到 SDK 主核侧下一个结构区的起点,size 值多一层布局级印证。
# 证据等级:值是推导而非测量(P11 是否真执行该 init 路径依赖 q32s 控制流,
# 解码存在已知缺口,本仓库不执行 q32s);只作为显式实验配置使用,经此场景
# 产生的全部推进都是条件通过(derived-not-measured),不算已验证。
P11_HEAP_INFO_DERIVED=bytes.fromhex('0c3df439')  # begin=0x3d0c size=0x39f4

# JL_LRCT NUM(0xfd3604,trim 结果计数)的显式探路值(derived-not-measured)。
# 推导链(trim 函数后半 0x606750c..0x6067600 反汇编 + 固件自己的换算常数):
# ①0x6067520 r2=NUM;0x6067526 r2==0 → CON|=1 重启 trim 直接返回——NUM 非零
#   是链继续的必要条件(r2==0 时 [0x129D88] 保持 bss 0,0x6067ac8 第二轮
#   LRC 写 1000000/0 除零,链必坏)。②0x6067532 读设备结构字节 b[0x1265C0+692]
#   (运行态实测已初始化,值 1)选择分子:0x606753e r0=0xC9C38000(十进制
#   3385032704,原始字节 00 80 c3 c9;旧记录「0xC9C9C9C0」是十进制转写笔误)
#   +0x6067544 r1=1 → 64 位分子 0x1_C9C38000=7,680,000,000(7.68e9,整数
#   设计常数)。③0x6067546 64/32 无符号除法 r2=7.68e9/NUM 得换算频率写
#   [0x129D98];历史维护:b[0x129D8E](power_init 0x6067aa6 已写 1)非零 →
#   r0=20、entry[0]==0(首次运行,bss 零)→ 0x606758e 填充路径把 20 项全部
#   填成商,平均=商 → [0x129D88];b[0x129D8C] 0→1;0x60675e0 call 0x60674ec
#   (r0=1)重启 trim(CON &=~1/|=0x40/|=1)。④0x6067ac8 r0=[0x129D88] 调
#   0x60674b8 第二轮 LRC 写:prd=1000000/r0——[0x129D88] 的单位就是 LRC
#   频率 Hz,与第一轮(0x6067ab2 r0=200000 标称值 → prd=5,M2P 05/0a/28/78)
#   同单位。⑤探路值 NUM=38400(0x9600):7,680,000,000/38,400=200,000 整除
#   精确——「标称 200kHz LRC 在该换算常数下的恒等计数」,商恰等于固件自己
#   的标称立即数 200000,第二轮 LRC 写重新推导出与第一轮完全相同的字节
#   (prd=5、10/40/120),下游算术完全自洽。**探路值非硅测量**:真实 NUM
#   是逐片硅测量值,这里只保证数值侧效应落在固件自己的标称常数上,不构成
#   对硅行为的任何声称;未提供该参数时 NUM 读严格停(300,240 前沿形状)。
LRCT_TRIM_RESULT_PROBE=38400  # 7,680,000,000/200,000 = 38,400 = 0x9600

# 0xfd1c00 在 SFC 挂起/恢复周期里按位的 RMW 记录；前三条来自挂起尾与恢复头，
# 中间的 0x4000 是 0x12ba38 轮询返回后的 PLL 状态位写入，末尾两条是恢复尾的清位。
SFC_RESUME_OPERATIONS=(
    {'kind':'or','operand':'0x1'},{'kind':'or','operand':'0x8'},
    {'kind':'and','operand':'0xffffefff'},{'kind':'or','operand':'0x4000'},
    {'kind':'and','operand':'0xfffffff7'},{'kind':'or','operand':'0x8'},
    {'kind':'and','operand':'0xffffefff'},{'kind':'or','operand':'0x4000'},
    {'kind':'and','operand':'0xfffffff7'},{'kind':'or','operand':'0x8'},
    {'kind':'and','operand':'0xffffefff'},{'kind':'or','operand':'0x4000'},
    {'kind':'and','operand':'0xfffffff7'},{'kind':'or','operand':'0x8'},
    {'kind':'and','operand':'0xffffefff'},{'kind':'or','operand':'0x4000'},
    {'kind':'and','operand':'0xfffffff7'},{'kind':'or','operand':'0x1000'},
    *([{'kind':'or','operand':'0x4000'}]*32),
    {'kind':'and','operand':'0xfffff3ff'},{'kind':'and','operand':'0xfffffffe'})


def irq_case(module,app,header,rom,name,*,entry=False,banks=False,idle_wait=False,rti=False,
             unmaskable=False,sfc_suspend=False,gpio_out_readback=False,kick_wake=False,
             repeat=False,hardware_crc=False,rep_block=False,rep_unknown_transport=False,
             icu_region=False,dcu_cmo=False,zero_rep_skip=False,erased_bankb=False,
             insert_load_pair=False,halfword_pair=False,p11_power_init=False,
             heap_info_bytes=None,byte_pair=False,lp_timer_writes=False,lrct_trim_done=False,
             lrct_trim_result=None,timer0_std12m=False,lp_timer_con_readback=False,
             p33_analog_config=False,power_init_rstcon_masks=False,
             power_init_wkup_dly=False,
             mem_pwr_con=False,
             flash_image_bytes=None,readonly=()):
    machine_options={**EMPTY_PRIORITIES,'first_task_return_hypothesis':'open_gate8',
                     'defer_core1_first_idle':True}
    if entry:machine_options['software_irq_entry']=True
    if banks:machine_options['software_irq_entry_banks']=True
    if idle_wait:machine_options['idle_wait_projection']=True
    if rti:machine_options['software_irq_rti']=True
    if gpio_out_readback:machine_options['gpio_out_readback']=True
    if kick_wake:machine_options['cpu1_control_kick_wake']=True
    if repeat:machine_options['repeat_irq_delivery']=True
    options={'initial_software_irq_pending':(0,0)}
    if entry:options['software_irq_delivery_requests']=True
    if unmaskable:options['unmaskable_irq_projection']=True
    if hardware_crc:options['hardware_crc_projection']=True
    # icu_region:opt-in 的两核 ICU region 寄存器组(0xffe400/0xffec00 的
    # +0x14/+0x18/+0x10)。ROM 0xc726 把 VM flash 窗口配进两核 ICU:每核
    # [+0x14]=begin、[+0x18]=end-1(write-only 记录)、[+0x10]=命令字
    # 0xca000020/0x40(与 0xca000010 同族,复用 CON bit30 完成投影)。默认关。
    if icu_region:options['icu_region_projection']=True
    # dcu_cmo:opt-in 的 DCU CMO 逐地址 cache 维护口(0xff05c8/0xff05cc,
    # SDK csfr.h JL_L1P 的 CMO[18]/CMO[19],__WO 只写)。sfc_drop_cache 两核
    # region 命令完成后尾调 flushinv_dcache(0xc71a,r2=0xff05cc):0xc6f4 把
    # (beg,len) 折算成 32 字节行数,寄存器计数 `rep r1,4` 体=add.rrr.p(r0+=32)
    # +st.w [r2]=旧 r0 并行对,每行写一次口——write-only 记录,读回一律拒绝。
    # 默认关。
    if dcu_cmo:options['dcu_cmo_projection']=True
    # rep_block:opt-in 的多指令 rep.i 体执行(0x6010b14 rep 4 32 字节拷贝,
    # 及其 callee 0x6010a64 内的四个 rep 4 8)。默认关闭,legacy 报告不变。
    # rep_unknown_transport:opt-in 的 rep 拷贝体未知字节运输。字形式(0x600a352
    # rep 4 4 从 qcy_boot_info 0x122800 拷 16 字节,其中 bytes 1-3/8-11 是
    # boot.h 里 vm_info 的 C 对齐 padding 与 vm_res 保留字段,app/ROM 均无写
    # 入者,真机为残留 RAM 垃圾)与字节形式(0x600a302 rep 4 16 从设备结构
    # 0x1265C0+0x10E4 拷回同一 16 字节块——SET 路径 rep 4 4 拷入的缓存,同一
    # 批 padding/vm_res 未知字节;ld.bu.pin 零扩展构造、st.b.pin 只写低字节)
    # 都按字节来源运输未知,不臆造数值。默认关闭。
    extra={'software_irq_frames':dict(HANDLER_PSR_FRAMES)} if entry else {}
    if rep_block:extra['immediate_rep_multi_block']=True
    if rep_unknown_transport:extra['rep_copy_unknown_transport']=True
    # zero_rep_skip:opt-in 的零计数 rep 跳过假设(multi-block gate 的寄存器
    # 计数形式 count=0 → 体一次也不执行、PC 前进到体后、计数寄存器保持 0)。
    # memset 先例 rom_zero_rep_skip(0x4242/0x424c)的推广:FM-1 run3 只捕获非零
    # 计数「体执行次数=计数寄存器值,寄存器递减到 0」,count=0 本身 BR28 未测
    # 量(GAPS.md「Not tried: rep with a count of 0」),报告以
    # zero_count_skip_is_assumption 计数、不声称硅验证。默认关闭。
    if zero_rep_skip:extra['zero_count_rep_skip']=True
    # insert_load_pair:opt-in 的「装载+组装」并行对执行(ROM memcpy 0x417e
    # 不对齐源组装变体:rep r5,22 体=4 次 ld.bu.pin 逐字节取+insert.rrii.p
    # ∥ld.bu.pin 并行对组装字(bits 8/16/24)+st.w.pin 存整字)。寄存器交叉
    # 方向「A 半(insert)读 B 半(ld)写之前的旧寄存器」与 FM-1 run3 捕获的
    # exact_parallel_stores 方向(B 读 A 之前旧值)相反、无硬件捕获证据,按
    # 对称旧值假设执行,adapter 以 _is_assumption 后缀显式标注。默认关闭。
    if insert_load_pair:extra['exact_parallel_insert_load']=True
    # halfword_pair:opt-in 的并行对半字存储第二半(exact_parallel_stores 的
    # 独立子开关 exact_parallel_halfword_stores)。证据形状 app 0x6011370
    # mov.rr.p∥st.h h[r4+0]=r5(记录解析 helper 0x6011366 的出参清零序列):
    # 上游 pair 存储模型写前合并读旧 word(模型内部簿记读,非 guest 装载),
    # 未初始化 RAM 上假停;gate 内按字节粒度直写 2 个目标字节,同字邻字节
    # (含 unknown)保持原状——正确字节语义,非 unknown 传播放松。该形状在
    # legacy 前缀(149,660 之前)已出现 2 次(走上游模型、内存已初始化、
    # 正常执行),并入主开关会改既有报告的 adapter 计数,故单独 opt-in。
    # 默认关闭,legacy 报告不变。
    if halfword_pair:extra['exact_parallel_halfword_stores']=True
    # byte_pair:opt-in 的并行对字节存储第二半(exact_parallel_stores 的独立
    # 子开关 exact_parallel_byte_stores,同半字先例单独 opt-in)。证据形状
    # app 0x6067a74 add.s14.rri.p∥st.b b[r0+0]=r4(power_init 主核对 P11
    # M2P 消息区的首写):字节存储单字节全覆盖、旧值完全无关,直写 1 个
    # 目标字节;不走上游 pair 存储模型(其写前合并读旧 word 是模型簿记读,
    # 未初始化/未映射 RAM 上会假停)。默认关闭,legacy 报告不变。
    if byte_pair:extra['exact_parallel_byte_stores']=True
    # mem_pwr_con 的死读放行需要 probe_boot 的 opaque 死读门诊断标志
    # (0x6067844 ld.w 读回 MEM_PWR_CON,值被下一指令覆盖,从不消费)。
    if mem_pwr_con:extra['mem_pwr_con_projection']=True
    # p11_power_init:power_init(0x6067876)内部的 P11 依赖链(opt-in,默认关,legacy
    # 报告不变)。注意 reset_history_projection 本来就由 platform_case 默认打开
    # (POR_HISTORY 全量快照,含 PWR_CON 0xf2a400:0x80),这里不得重复设置——
    # reset_history_words 会被 options.update 整体替换,只给部分字会破坏前缀里
    # 复位原因初始化(0x6068c80 读 0xfd0004)等已有读回。本开关只加四件:
    # ①p11_lpwr_response_projection——主核 |=0x20 请求(0x60679a0)后 PWR_CON 读回
    #   bit5=0(q32s 0xafa bitclr 已证实的最小响应投影,路 E),死等轮询
    #   (0x60679ae/b0)因此退出。
    # ②p11_window_projection——q32s PMU_CON 0xf2a0c0 释放 RMW |=1(0x60679a8,
    #   write-only 位记录)与 P11_CLOCK.SYS_DIV 0xf2a410=0 / CLK_CON0 0xf2a414=1
    #   整字写(write-only 记录)。
    # ③P11 RAM 公共段拷贝目的窗(P11_COMMON_CODE_WINDOW):范围=p11_code.bin 资源
    #   BANKCB 头 entry0 自己声明的 (address=0x80→主核 0xf20080,bytes=12112)
    #   (analyze_p11 钉死、CRC 校验的真实资源字节);拷贝源是同一资源在 bank A
    #   flash 的已知字节(flash_image 已映射),模型只提供可写目的窗,不发明内容。
    # ④power_init_config_rmw——0xf2c05c 配置 RMW(0x60678f4 `rmw.and.r &=0x3FD`,
    #   app/ROM 全量唯一引用、从不回读)按 0xf2c054 先例进 reset 投影的配置表
    #   (extra masks,opt-in;默认三地址表与 legacy 报告不变)。
    p11_regions=()
    p11_snapshots=()
    budget=250000
    if p11_power_init:
        options['p11_lpwr_response_projection']=True
        options['p11_window_projection']=True
        options['power_init_config_rmw']=True
        # M2P 消息区随 p11_power_init 一起挂为可写 harness region(范围=SDK
        # p11_app.h 自己声明的 M2P_MESSAGE_RAM_BEGIN..+0x60,普通存储语义,
        # 见 P11_M2P_MESSAGE_WINDOW 注释)。crc_bankb 前缀(289,849)在首个
        # M2P 访问之前就停,该场景报告不受影响。
        p11_regions=(P11_COMMON_CODE_WINDOW,P11_M2P_MESSAGE_WINDOW)
        # 拷贝目的窗的快照:check() 用它与 p11_code.bin 资源 entry0 的真实字节
        # (analyze_p11 的 CRC/SHA 钉死值)互证拷贝逐字节正确。
        p11_snapshots=(('p11_common_code',P11_COMMON_CODE_WINDOW[0],12112),)
        # p11_code.bin 公共段拷贝循环(0x6067990..96,每字节 4 条指令 ×12112 字节
        # ≈48k 步)超出默认 250k 预算,前沿场景放宽到 320k。
        budget=320000
    # heap_info_bytes:显式实验配置(P11_HEAP_INFO_DERIVED 静态推导候选)。只与
    # p11_power_init 叠加使用;报告把 fixture 标注为 derived-not-measured,
    # 场景结论一律条件通过,不算已验证(模型侧 read_bytes 只按字节粒度回放,
    # 不影响任何其他读者)。
    if heap_info_bytes is not None:
        require(p11_power_init,'heap-info fixture only layers on the p11_power_init scenario')
        options['p11_heap_info_bytes']=bytes(heap_info_bytes)
        # M2P 消息区两组钉子字节:头 8 字节(LRC 周期/定时器字节,0x60674b8
        # 写并三次回读 b[+0] 推导:prd=1000000/200000=5、50/5=10、200/5=40、
        # 600/5=120)与 0xF27FD4..D7(首写 M2P_RVD2PVDD_EN 后的字;实测落盘
        # 0x00——静态读起来 0x6067a6c `r4=1` 在被 icfg 位图跳过的
        # 0x6067a38..6c 块内,实际路径的 r4 是 0,写 0=清除)。快照只挂进
        # 实验配置场景(堆 fixture 放行后链才写 M2P);挂在共享的
        # p11_snapshots 里会给 crc_bankb 报告加键、破坏其字节不变。
        p11_snapshots=tuple(p11_snapshots)+(('p11_m2p_lrc',P11_M2P_MESSAGE_WINDOW[0],8),
                                            ('p11_m2p_rvd2pvdd',0xF27FD4,4))
    # lp_timer_writes:opt-in 的 power_init 尾部低功耗定时器初始化写记录
    # (lp_timer_init_projection:0x6067a7a 起 [0xf2a900]=0x5C/[+4]=0x53/[+8]=0
    # P11_LPTMR0 CON0/CON1/CON2 整字写、[0xf2c04c]=0x21/[0xf2c050]=0x41 PMU
    # 配置族整字写、0xfd3600 JL_LRCT CON 位 RMW——全部 write-only 记录,读回
    # 一律严格拒绝)。独立开关而非并入 p11_window_projection(后者报告键在
    # crc_bankb 场景已钉死);默认关,legacy 报告不变。
    if lp_timer_writes:
        require(p11_power_init,'LP-timer write records only layer on the p11_power_init scenario')
        options['lp_timer_init_projection']=True
    # lrct_trim_done:opt-in 的 JL_LRCT CON 读回「trim 已完成」条件投影(bit7
    # 置 1,SFC PLL-lock bit15 先例同模式)。轮询形状(反汇编钉死):0x6067514
    # 读 CON→0x6067516 beq.and.ri 测 bit0(==0 跳过 trim 直接返回)→0x606751a
    # 再读→0x606751c beq.and.ri 测 bit7——ISA 语义是「(r&(1<<n))==0 时跳转」、
    # 目标回 0x606751a,即固件等 bit7 置位(busy/完成标志置 1)才退出,随后
    # 0x6067520 ld.w [r3+1*4](n 字段 scale=4)读 NUM(0xfd3604,SDK __RW)取
    # trim 结果计数——硅测量值无任何证据,读回一律严格停(新前沿)。投影只
    # 约束 CON 读回:已记录 RMW 已知值(bit0=1 使能/bit6=1)+ bit7 强制 1,
    # mask 外的位不臆造。默认关,legacy 报告不变。
    if lrct_trim_done:
        require(lp_timer_writes,'the LRCT trim-done projection only layers on the LP-timer write records')
        options['lrct_trim_done_projection']=True
    # lrct_trim_result:JL_LRCT NUM(0xfd3604,trim 结果计数,硅测量值)的显式
    # 探路值(LRCT_TRIM_RESULT_PROBE 推导链见常量注释;与 heap_info_bytes
    # 同族的 opt-in 数据 fixture)。只与 lrct_trim_done 叠加(NUM 读只有
    # trim-done 投影放行轮询后才真实到达);未提供时 NUM 读严格停语义
    # 不变(300,240 前沿形状)。挂 LRC 结果块快照(40 字节头窗逐字节 + 0x64
    # 全窗 SHA;avg/计数字节/任务句柄/raw/20 项历史数组),只挂进实验场景,
    # 不进共享 p11_snapshots(否则 crc_bankb 报告加键、破坏字节不变)。
    if lrct_trim_result is not None:
        require(lrct_trim_done,'the LRCT trim-result probe value only layers on the trim-done projection')
        options['lrct_trim_result_value']=lrct_trim_result
        p11_snapshots=tuple(p11_snapshots)+(('lrc_trim_result_head',0x129D88,40),
                                            ('lrc_trim_result_block',0x129D88,0x64))
    # timer0_std12m(路 AH):TIMER0 时钟源字段 5 的命名+速率放行。三方证据
    # (SDK gptimer_hw.h 宏 GPTIMER_CLK_SRC_STD12M=0b0101<<10、SDK 驱动
    # gptimer_hw_v2.c bitcode IR 的 src<<10|div<<4 与 std_clk==12→字段 5/
    # 12MHz、app 自己的换算表 0x60A2B5C[src5]=12MHz+0x600079A 原始字段 RMW+
    # PRD=12*10000/1-1=119999=10ms 闭环)见 br28_model.Timer0 类注释;source 5
    # 是 STD12M(标准 12MHz),不是 LRC(那是字段 3)。计数到期事件从此有
    # 时基依据:12MHz 相对 RC16M 参考系 3/4 速率,PRD+1=120000 tick=10ms。
    # 12MHz 是固件名义换算速率,非逐片硅测量;到期后的 IRQ4 交付路径仍未
    # 实现,pending 可见性(读回 bit15/中断判定)按既有语义显形。默认关:
    # source 5 写保持 unsupported_timer_clock_source 严格停(301,097 形状)。
    if timer0_std12m:
        options['timer0_std12m_source']=True
    # lp_timer_con_readback:opt-in 的 P11_LPTMR0 CON 读回=最后写值投影(路 AL)。
    # 证据:SDK p11_sfr.h 把 P11_LPTMR_TypeDef 的 CON0/CON1/CON2/PRD/RSC 都标
    # __RW(仅 CNT __RO)——即这些配置字可读回,读回=最后写值与 SFC 时钟 +8
    # (0xfd1c08,divider_readback_is_last_write)先例同族;只返回固件已写过的
    # 最后值,从不臆造复位值。触发点:0x6067668 LP 定时器配置函数(0x6067b32
    # 以 r0=2 调入)的 0x6067672 ld.w 读 CON2 + 0x606767e rmw.and.nmodimm 复合
    # RMW 再读,形状反汇编钉死(清 CLK_SEL bits0-3、|=3、写回、清 CLK_DIV
    # bits4-7)。默认关:CON 读回保持 Blocked(301,540 前沿形状)。
    if lp_timer_con_readback:
        require(lp_timer_writes,'the LP-timer CON readback only layers on the LP-timer write records')
        options['lp_timer_con_readback_projection']=True
    # p33_analog_config:opt-in 的 P33 模拟控制族读回投影(路 AM)。触发点
    # =power_init 的 P33 配置函数 0x60677a4(0x6067b36 调用)内 0x60677b0
    # `rmw.and.r [0xf2c254]&=0x3FC7` 读 P3_ANA_CON5(0xf2c254=P33_ACCESS(0x95))
    # ——p33_sfr.h 只是地址宏、无 __RW/__RO/__WO 类型标注(与 p11_sfr.h 的
    # P11_LPTMR_TypeDef 不同),读回语义本身无 SDK 证据;但同族 P3_ANA_CON6
    # (0xf2c258)已在 VOLTAGE_CONFIG(本链 architecture_case 默认开启)按「显式
    # 合成初始字 0 + 读回=当前寄存器值」建模(clock_voltage_configuration
    # 变体先例)。故按同一先例把该函数读写的另两个模拟控制族成员 P3_ANA_CON2
    # (0xf2c248)/P3_ANA_CON5(0xf2c254)纳入 opt-in 的
    # p33_analog_config_projection(显式合成初始字 0、写后读回=最后写值、
    # 不臆造硅模拟状态)。独立开关而非并入 VOLTAGE_CONFIG:后者在本链
    # architecture_case 默认展开,并入会给所有经该链的场景报告加键、破坏
    # 逐字节不变。默认关:0xf2c254 读保持 unmapped_memory(301,615 前沿形状)。
    if p33_analog_config:
        require(p11_power_init,'the P33 analog-config readback only layers on the p11_power_init scenario')
        options['p33_analog_config_projection']=True
    # power_init_rstcon_masks:opt-in 的 0xf2c054(P3_RST_CON0)多掩码访问放行(路 AO)。
    # 触发点=0x60677a4 的 P33 配置函数内 0x60677fa `rmw.and.r [0xf2c054]&=0xFF7F`
    # (清 bit7)与 0x60677fe `&=0x7FBF`(清 bit6),与主复位初始化 0x6068c80 的
    # 0x3fdf(清 bit5)是三个不同掩码——复位投影严格单掩码校验拒绝。证据核验结论:
    # 固件真实用这三个掩码清该寄存器的不同位(SDK p33_sfr.h 对 P3_RST_CON0 只有
    # 地址宏、无字段位定义,power_reset.h 只有枚举复位源,bit6/bit7 含义无 SDK
    # 证据,只记录真实观测掩码、不发明未观测位)。选方案 (a) 掩码集扩展:
    # extra_configuration_mask_sets 把 0xFF7F/0x7FBF 追加进 0xf2c054 的接受掩码集,
    # 每个掩码仍按位清(清掉的位已知 0、保留位未知、读回须全知),默认单掩码校验
    # 行为不变。默认关:0xf2c054 的 0xFF7F 读保持 reset_configuration_operation_
    # not_modeled 严格停(301,636 前沿形状)。
    if power_init_rstcon_masks:
        require(p11_power_init,'the P3_RST_CON0 mask-set extension only layers on the p11_power_init scenario')
        options['power_init_rstcon_mask_sets']=True
    # power_init_wkup_dly:opt-in 的 P3_WKUP_DLY(0xf2c114)配置 RMW 放行(路 AQ)。
    # 触发点=0x60677a4 的 P33 配置函数内 0x6067824 `rmw.and.nmodimm [0xf2c114]&=0x7F8`
    # (r1 由 0x606781e `r1=r1-308` 从 P3_ANA_CON2 基址 0xf2c248 算出 0xf2c114=
    # P33_ACCESS(0x45))。AND 掩码 0x7F8=bits3-10 置位:&=0x7F8 保留 bits3-10(掩码
    # 置位位,8 位 delay 字段,保持未知)、清 bits0-2 与 bits11-31(掩码清零位,已知 0)。
    # 掩码 0x7F8 是反汇编钉死的真实观测;SDK p33_sfr.h 只有地址宏、无字段位定义,
    # 「wakeup delay 字段」只是寄存器名+固件真实保留这些位,不声称硅语义。按
    # RESET_CONFIG_MASKS「配置 RMW 不经读回」先例进 reset 投影的配置表(extra
    # masks,opt-in;清掉的位已知 0、保留位未知、读回仍须全知)。注意:0xf2c114 在
    # app 里还有第二处引用——独立函数 0x6067f84(app.bin 0x67e84,power-off/低功耗
    # 例程)0x6067f98 直接 li 0xf2c114 后 0x6067f9e 显式读回 `r=(r&0x7F8)|0x7` 再
    # 写回(保留 bits3-10、置 bits0-2=0x7)——该读回不属于「配置 RMW 不经读回」,本
    # 投影不放行,未来到达时诚实停 unknown_reset_configuration_bits,归后续路。
    # 默认关:0xf2c114 读保持 unmapped_memory(301,651 前沿形状)。
    if power_init_wkup_dly:
        require(p11_power_init,'the P3_WKUP_DLY config RMW only layers on the p11_power_init scenario')
        options['power_init_wkup_dly_rmw']=True
    # mem_pwr_con:opt-in 的 P11_SYSTEM.MEM_PWR_CON(0xf2a62c)位 RMW 放行(路 AS)。
    # 触发点=位域配置助手 0x60676ec(0x606783a call 调入)内四次 `rmw.or.r
    # [0xf2a62c]|=r0`(置位内存电源位,0x6067730/52/74/96)。证据核验结论:
    # SDK p11_sfr.h 标 __RW(但 __RW 宏值 == __WO == volatile,R/W 区分只在注释)、
    # p11_app.h 有 bits0-5 字段位定义(MEM_PWR_CPU_CON/RAM0_3/RAM4_5/RAM6_7/
    # RAM8_9/PERIPH_CON);app 字面量 3 处、ROM 0 处——helper 四次 OR-RMW(置位)、
    # 调用者 0x60677a4 0x6067844 一次 ld.w 读回(死读,值被下一指令 0x6067846
    # 覆盖)、独立 power-off 例程 0x6067e82 多次 AND-RMW(清位)。无整字写,读回
    # 值从不消费——故不选 lp_timer 的「读回=最后写值」(OR/AND 部分字 RMW 没有
    # 「最后整字写值」可回读),选 write-only 位记录(PartialWord,与 cpu1_control
    # kick/q32s PMU_CON |=1/LRCT 位 RMW 同族);死读由 opaque 死读门(证明值从不
    # 消费)单独放行,不臆造读回值。默认关:0xf2a62c 读保持 unmapped_memory
    # (301,676 前沿形状)。
    if mem_pwr_con:
        require(p11_power_init,'the MEM_PWR_CON RMW only layers on the p11_power_init scenario')
        options['mem_pwr_con_projection']=True
    # 资源表扫描从 flash 基址 0x06000000 读 app 映射(ENTRY 之下、app 结束
    # 之后)之外的已知真实字节:按需挂完整 sfc 明文只读区,同样不影响默认映射。
    if flash_image_bytes is not None:readonly=((0x06000000,flash_image_bytes),)+tuple(readonly)
    # erased_bankb:opt-in 的「未验证擦除态 bank B」显式 fixture,三个独立
    # 证据钉死的窗。窗 1(2026-11-12 反汇编+trace):app find_partition
    # (0x6010f32) 主循环从探测表 0x060A0FA4 第 5 项(flash 偏移 0x100000)
    # 读 32 字节头——CPU 可见 0x60fdfe0=0x06000000+0x100000-0x2020。
    # CRC16(头 bytes 2..31)与头前 2 字节期望值在 0x6010f80 比较,不匹配→
    # 0x6010f5a 良性跳到下一项;5 项全败→0x6010fe6 return 1;调用者只是
    # free 临时对象并清 OTP 全局槽 827/828,VM init 返回值被 0x601205a
    # 调用者忽略——失败分支良性。0xFF 擦除态下期望 CRC=0xffff、真实计算值
    # =0x75f8,必然不匹配,由真实代码自己选择分支。窗 2(本次反汇编+trace):
    # qcy 消费函数 0x6060a4a 把 window(flash 0x1e0000)缓存进全局 0x126544
    # 后,循环 0x6060ad0 从同一张 u16 对表 0x60a1e8a(id==0 终止)取 id、
    # call 0x60609ec:walker 0x60609be 命中项(offset=Σ前项(4+size))后读
    # window+offset 起 4 头字节,LE32==0xFFFFFFFF→返回 -248(擦除态),
    # 调用者 0x6060a9a 对 -248 直接跳下一项、零副作用(错误处理 call 只在
    # -245/-243/成功路径,trace 零执行)。本表两项 {0x66,6}/{0x6e,6},擦除
    # 态实际读取只有两段头各 4 字节(offset 0/0xa),fixture 覆盖表声明的
    # 完整结构范围 20 字节。窗 3(本次反汇编+trace):VM 配置 GET 消费者
    # 0x6060f9a 读两半 magic 各 4 字节后,0xFFFFFFFF→0x600a7b6 空白检查
    # 逐字节扫过声明 VM 区全长(两半各 0x1000,实测 0x600a78c 共 8192 次、
    # 范围 0x61d7fe0..0x61d9fdf,遇非 0xFF 即止)——数据依赖的结构性全扫描,
    # 逐窗扩 fixture 只会把 unmapped 停点后挪一字节,故按结构声明范围一次
    # 性映射 0x2000 字节(范围依据见 ERASED_BANKB_VM_RANGE 注释:boot_info
    # vm 字段+消费者切半算法+空白检查 len,均固件自己的声明);全空白分支
    # 良性(格式化写 call 零执行、空 VM 状态、位图重建自 app flash 表、
    # return 0),由真实代码自己扫描自己选分支。窗 4(本次反汇编+trace):
    # cfg_tool/btif 存储后端 init 0x6060d7c 解析 window+0x200 的 8 字节首
    # 条目头 {u32 len;u32{b0 crc,bits20+ size}},0xFF 擦除态下 (len-1)=
    # 0xfffffffe 无符号 >2047,真实代码只读头 4 字节就在 0x6060e0e 拒绝、
    # 返回 0(bytes 4-7 不读、CRC 不算、无 flash 写,trace 钉死),条目有效
    # 位 b[0x126568] 保持 0 把按 index 找条目的 walker 0x60112c8 挡在门外
    # ——fixture 覆盖消费者自己解析的完整 8 字节头结构(范围依据见
    # ERASED_BANKB_RECORD_RANGE 注释),不扩数据区/后续条目。四个窗都是
    # 显式环境假设(unverified erased-bank fixture),不是已知出厂状态;
    # 范围外 bank B(含 TON 0x1e1000)继续严格 unmapped。
    if erased_bankb:
        base,length=ERASED_BANKB_HEADER_RANGE
        wbase,wlength=ERASED_BANKB_WINDOW_RANGE
        vbase,vlength=ERASED_BANKB_VM_RANGE
        rbase,rlength=ERASED_BANKB_RECORD_RANGE
        readonly=tuple(readonly)+((base,b'\xff'*length),(wbase,b'\xff'*wlength),
                                  (vbase,b'\xff'*vlength),(rbase,b'\xff'*rlength))
    if sfc_suspend:
        # The SFC suspend tail sets 0xfd1c00|=1 (write-only RMW projection) and
        # then the wrapper reads the second SFC 0xfe0100 CON. The second SFC is
        # never referenced by the ROM (byte search) and the H3S boots from a
        # single 2MiB flash on 0xfe0200, so 0xfe0100 stays at its reset state;
        # CON=0 is an explicit, unmeasured reset assumption, not a default.
        options['sfc_clock_gate_projection']=True
        options['secondary_sfc_read_bus']=True
        options['initial_secondary_sfc_control']=0
    return dual_case(module,app,header,rom,name,abstract=True,readonly=readonly,
                     machine_options=machine_options,options=options,budget=budget,
                     extra_diagnostics=extra or None,extra_regions=p11_regions,
                     extra_snapshots=p11_snapshots)


def check_delivery(r,*,before_rti=True):
    m=r['machine'];projection=m['software_irq_entry_projection']
    require(len(projection['deliveries'])==1,'scheduler IPI was not delivered exactly once')
    d=projection['deliveries'][0]
    require(d['core']==0 and d['irq']==127 and d['vector_address']=='0x19fffc' and
            d['handler']=='0x12ad2a' and d['reti_load_is_assumption'] and
            d['entry_sp_bank_or_icfg_effects_certified'] is False,'unexpected delivery record')
    if before_rti:
        require(m['cores']['0']['reti']==d['reti_from'],'RETI did not keep the interrupted PC')
    return d


def check(report,firmware):
    rows={r['name']:r for r in report['scenarios']}
    r=rows['irq127_request_strict']
    require(r['completed_steps']==149660 and r['stop_core']==0 and r['stop_pc']=='0x12b608' and
            r['stop']=={'kind':'software_irq_delivery_not_implemented','core':0,'irq':127},
            'strict path must still stop before the unimplemented entry')
    r=rows['irq127_entry_minimal']
    d=check_delivery(r)
    pending=r['peripherals']['software_irq_commands']['pending']['0']
    require(pending=={'known_mask':'0xf','known_value':'0x0'} and
            r['peripherals']['software_irq_commands']['acknowledgements']==1,
            'pending bit was not acknowledged exactly once by the delivery assumption')
    source=r['peripherals']['software_irq_commands']['source_enable']['0']
    require(source=={'known_mask':'0x8','known_value':'0x0'} and
            r['peripherals']['software_irq_commands']['source_commands']>0,
            'handler did not mask its own software-IRQ source at entry')
    require(r['peripherals']['ipmask']==7,'handler did not raise the real IPMASK to 7')
    require(r['stop_pc']=='0x12ad68' and
            r['stop']['kind']=='unknown_core_special_register' and r['stop']['register']=='usp',
            'handler without the bank assumption must stop at the unknown USP')
    adapters=r['instruction_adapters']
    require(adapters.get('software_irq_handler_psr_frame')==2 and
            adapters.get('software_irq_source_enable_rmw',0)>0,
            'entry small-frame push/pop or source masking was skipped')
    executed=[(row['pc'],row.get('instruction_id')) for row in r['last_instructions']]
    require(('0x12ad5c','st.w.modimm') in executed and ('0x12ad66','csync') in executed,
            'handler head instructions were not really executed')
    r=rows['irq127_scheduler_entry']
    d=check_delivery(r)
    require(r['machine']['software_irq_entry_projection']['entry_bank_assumption'] and
            d['entry_banks'] and not d['entry_banks']['silicon_bank_switch_certified'],
            'scheduler entry lacks the explicit bank assumption record')
    adapters=r['instruction_adapters']
    require(adapters.get('multicore_visible_sp_from_usp')==1 and
            adapters.get('software_irq_handler_context_frame')==1 and
            adapters.get('multicore_visible_sp_from_ssp',0)>=1,
            'handler bank switch or 76-byte context frame was skipped')
    require(r['completed_steps']>150000 and r['stop_core']==1 and r['stop_pc']=='0x12c5ca' and
            r['stop']['kind']=='unsupported_runtime_instruction' and
            r['stop']['instruction_id']=='idle',
            'the real scheduler must run and the stop must be the peer-core IDLE boundary')
    require(r['completed_steps']>=151000,'scheduler body 0x12adec did not execute')
    r=rows['irq127_appcore_roundtrip']
    d=check_delivery(r,before_rti=False)
    events=r['machine']['events']
    rti=[e for e in events if e['kind']=='software_irq_rti']
    require(len(rti)==1 and rti[0]['core']==0 and rti[0]['pc_after']=='0x6012028' and
            rti[0]['reti_from_frame']=='0x6012028' and rti[0]['bank_writeback_is_assumption'],
            'the restored task did not enter the real app_core entry through RTI')
    restored=[e for e in events if e['kind']=='scheduler_task_gprs_restored']
    require(restored and restored[0]['unknown_registers']==list(range(1,16)),
            'scheduler GPR restore lost the unknown task slots')
    require(any(e['kind']=='core_idle_wait_entered' for e in events),
            'peer core never reached its IDLE wait under the projection')
    require(r['stop']['kind']=='unmodeled_unmaskable_irq' and r['stop']['core']==1 and
            r['stop']['source']=='software_irq',
            'round trip must stop at the next real boundary: the CPU1 unmaskable software IRQ')
    require(r['completed_steps']>=153000,'app_core task did not execute after RTI')
    r=rows['irq125_sync_roundtrip']
    deliveries=r['machine']['software_irq_entry_projection']['deliveries']
    require([(d['core'],d['irq'],d['handler']) for d in deliveries]==
            [(0,127,'0x12ad2a'),(1,125,'0x60662c8')],
            'the peer sync IPI was not delivered to the real CPU1 handler')
    sync=deliveries[1]
    require(sync['vector_address']=='0x19fff4' and sync['entry_banks'] and
            not sync['entry_banks']['silicon_bank_switch_certified'],'CPU1 sync delivery record differs')
    require(r['peripherals']['software_irq_commands']['pending']['1']=={'known_mask':'0xf','known_value':'0x0'},
            'CPU1 sync pending was not acknowledged by its real handler')
    gpio=r['peripherals']['gpio'].get('0xfd5308')
    require(gpio=={'known_value':'0x60','known_mask':'0x68'},
            'the register-AND must clear the known bit3 and leave the masked-out bit0 unknown')
    out=r['peripherals']['gpio'].get('0xfd5300')
    require(out=={'known_value':'0x8','known_mask':'0x8'},
            'the SFC resume must re-set the known bit3 on GPIO OUT without fabricating its neighbours')
    adapters=r['instruction_adapters']
    require(adapters.get('gpio_out_readback_load')==12 and
            adapters.get('gpio_out_readback_bit_mask')==4 and
            adapters.get('gpio_out_readback_register_copy')==1 and
            adapters.get('gpio_out_readback_writeback')==2,
            'the SFC GPIO OUT read-back path was not exercised through its exact load/mask/copy/write-back shape')
    # The CPU1 handler tail pops its real {psr,rets,reti} frame (0x606633e,
    # app disassembly: pop.rmask r4-r0 / pop.srl m=41 / csync / rti) and the
    # symmetric RTI returns the core to its interrupted idle instruction.
    require(adapters.get('conditional_software_irq_rti')==2 and
            adapters.get('software_irq_handler_context_frame')==4,
            'the CPU1 handler tail frame pop and symmetric RTI were skipped')
    rti=[e for e in r['machine']['events'] if e['kind']=='software_irq_rti']
    require(len(rti)==2 and rti[1]['core']==1 and rti[1]['pc_after']=='0x12c5ca' and
            rti[1]['reti_from_frame']=='0x12c5ca' and rti[1]['sp_after']=='0x401bf8' and
            rti[1]['bank_writeback_is_assumption'],
            'the CPU1 sync handler did not return to its interrupted idle task through RTI')
    events=r['machine']['events']
    parks=[i for i,e in enumerate(events) if e['kind']=='core_idle_wait_entered' and
           e['core']==1 and e['pc']=='0x12c5ca']
    require(len(parks)==2 and parks[1]>events.index(rti[1]),
            'the returned CPU1 idle task did not re-park after its RTI (first park + re-park)')
    require(r['stop_core']==0 and r['stop_pc']=='0x6000f44' and
            r['stop']=={'kind':'unmapped_or_readonly_write','access':'write','address':'0xfd3504',
                        'size':4,'value_bytes':'00000000'},
            'after the peer return the stop must be CPU0 reaching the CRC engine control write')
    require(r['completed_steps']==155127,'the CPU1 handler return path changed its step count')
    held=r['peripherals']['cpu1_held_projection']
    require(held['current_control']==9 and held['kick_rmw_writes']==3,
            'the CPU1 PMU_CON0 kick bit was not recorded as exactly three |=8 retries')
    require(r['instruction_adapters'].get('cpu1_control_rmw')==4,
            'the release RMW plus three kick RMWs must be the only CPU1 control RMWs')
    wakes=[e for e in r['machine']['events'] if e.get('kind')=='core_woken_by_cpu1_control_kick']
    require(len(wakes)==1 and wakes[0]['pc_after']=='0x606631a' and
            wakes[0]['idle_completion_is_assumption'] and wakes[0]['wake_source_unverified'],
            'the kick wake of the parked peer core must stay an explicit assumption')
    require('cpu1_control_kick_bit3' in r['peripherals']['assumptions'],
            'the PMU_CON0 bit3 kick assumption must be recorded explicitly')
    gate=r['peripherals']['sfc_clock_gate_projection']
    require(gate['operations']==list(SFC_RESUME_OPERATIONS),
            'the SFC suspend/resume clock-gate writes changed')
    require(gate['divider']=='0xff' and gate['divider_writes']==36 and gate['reads']==36 and
            gate['readback_modeled'] is True and gate['known_value']=='0x5000' and
            gate['known_mask']=='0x5c09',
            'the SFC +8 divider write/read-back or the PLL-lock bit15 projection differs')
    require(r['peripherals']['sfc_read_bus_secondary']['con']==0 and
            r['peripherals']['sfc_read_bus_secondary']['writes']==0 and
            r['peripherals']['sfc_read_bus_secondary']['initial_control_fixture']==0,
            'the second SFC (0xfe0100) reset-state fixture or write count differs')
    r=rows['irq125_repeat_inert']
    # 暂停点1(repeat_irq_delivery)的实证边界:新投影打开后,前沿前缀必须完全
    # 不变(该场景不启用 hardware_crc,停在 155,127 的 CRC 引擎种子写)。前缀内
    # 固件对每核只 pend 过一次(commands=4:两核各 pend+clear,CPU1 handler 入口
    # 自清),第二次交付从未被请求;投影保持惰性,直到固件真实地再次 pend。
    require(r['completed_steps']==155127 and r['stop_core']==0 and r['stop_pc']=='0x6000f44' and
            r['stop']=={'kind':'unmapped_or_readonly_write','access':'write','address':'0xfd3504',
                        'size':4,'value_bytes':'00000000'},
            'the repeat delivery projection must not move the frontier stop')
    require(r['machine']['repeat_irq_delivery_projection']['enabled'] and
            not r['machine']['repeat_irq_delivery_projection']['nesting_implemented'] and
            r['machine']['repeat_irq_delivery_projection']['pending_rearmed_only_by_real_firmware_command'],
            'the repeat delivery projection is missing from the machine report')
    deliveries=r['machine']['software_irq_entry_projection']['deliveries']
    require([(d['core'],d['irq']) for d in deliveries]==[(0,127),(1,125)],
            'enabling repeat delivery changed the delivery set before the frontier')
    require(r['peripherals']['software_irq_commands']['commands']==4 and
            r['peripherals']['software_irq_commands']['acknowledgements']==2,
            'the firmware issued unexpected extra software IRQ commands before the frontier')
    r=rows['crc_engine_roundtrip']
    deliveries=r['machine']['software_irq_entry_projection']['deliveries']
    require([(d['core'],d['irq'],d['handler']) for d in deliveries[:2]]==
            [(0,127,'0x12ad2a'),(1,125,'0x60662c8')],
            'the CRC scenario must reuse the exact scheduler/sync deliveries')
    crc=r['peripherals']['hardware_crc_projection']
    # The wrapper (app 0x6000f2c) writes seed 0, feeds the 30 bytes at 0x400b22
    # one st.w each, csyncs and reads the result once. The expected value is
    # recomputed here from the app software CRC-16 parameters (poly 0x1021,
    # MSB-first, init=seed, no xorout) so the projection is checked against the
    # golden software algorithm, not against its own implementation.
    fed=bytes.fromhex('60e0e0c183070f3e5c983142a468f0e1c3872e7cd8b163e6ecd9b367cf9f')
    reference=0
    for byte in fed:
        reference^=byte<<8
        for _ in range(8):
            reference=((reference<<1)^0x1021)&0xffff if reference&0x8000 else (reference<<1)&0xffff
    require(crc['seed_writes'][0]=='0x0' and crc['result_reads'][0]==hex(reference) and
            crc['fed_bytes']>=len(fed),
            'the hardware CRC projection did not reproduce the software-table result for the actual wrapper input')
    # sfc_drop_cache 之后 VM init 链继续:同一 CRC 引擎再用 13 次(每次种子 0、
    # 喂 30 字节、读一次结果,共 14 次往返 420 字节),首轮黄金校验不变。
    require(len(crc['seed_writes'])==14 and crc['fed_bytes']==420 and len(crc['result_reads'])==14,
            'the extended VM-init prefix changed the hardware CRC usage shape')
    # 0x6010b14 rep 4 32(4 字节体 ld.bu.pin+st.b.pin × 32,flash 资源表头部
    # 32 字节拷贝)及其后整条资源表扫描(含 callee 0x6010a64 内四个 rep 4 8)
    # 全部按多指令体真实执行:70 个 rep.i 体、155,389→162,447 步;扫描完成后
    # 的一次性 SFC 时钟重配(0x12762c 标志守卫)写 0xfd1c04=3(+4 只写记录)。
    # 之后 0x600a352 rep 4 4 从 qcy_boot_info 0x122800 拷 16 字节头:bytes
    # 1-3/8-11 无任何写入者(boot.h vm_info 的 C padding 与 vm_res),按
    # rep_copy_unknown_transport 运输未知字节;消费函数 0x600a2aa 只用
    # vm_saddr(word 4-7,VM init 0x6011102 写入的 0x1da000),call 0x12bdc6
    # 算出 VM 区窗口地址 0x061d7fe0(=0x06000000+0x1da000-0x2020)。随后
    # 0x600a36e..0x600a37e 的 SFC 窗口组写入(r1=0xfe0218:[+12]=0x061d7fe0、
    # [+8]=0x7ffffff、[0]|=2)按 write-only 记录走完(sfc_read_bus 扩展),
    # call 0x12c030 后 CPU0 真实地第二次 pend irq125 到 CPU1(repeat 投影
    # +cpu1_control bit0 释放位修正),第二次交付把 CPU1 从停泊唤醒、
    # handler 处理完回停泊。之后 VM init 链 call ROM export sfc_drop_cache
    # (0x1fdcc→0xc726,实参 beg=0x061d7fe0/len=0x20):把 [0x061d7fe0,
    # 0x061d8000) 配进两核 ICU(每核 cli→testset 0x19fd78 抢锁→[+0x14]=begin、
    # [+0x18]=end-1(write-only 记录)→[+0x10]=0xca000040(0xc72c if.ge len>=
    # 0x1000 在 0xc730/0xc736 两条谓词 li 里二选一,实测 len=0x20 选 0x40)→
    # call 0xbac0 等 CON bit30 完成(与 0xca000010 使能路径同一条轮询指令)→
    # call 0xbad4 释放锁),r3+=0x800 换核重复 2 次。region 命令完成后尾调
    # flushinv_dcache(0x1fdb0→0xc71a→0xc6f4):0xff05cc 逐地址失效循环,体是
    # 寄存器计数 `rep r1,4`(add.rrr.p+st.w 并行对)。三件套建模后走通:
    # ①register_rep_multi_block(immediate_rep_multi_block 推广到寄存器计数
    # rep,计数来自真实寄存器值,计数寄存器按 FM-1 run3 证据递减到 0,体后
    # 编译器生成的 bnez r1 自然不跳);②并行对体按 exact_parallel_stores 的
    # 旧寄存器语义执行(存储半读 A 之前的旧 r0,首写 [0xff05cc]=0x061d7fe0
    # =行地址,r0 递增 32);③dcu_cmo_projection 把 0xff05cc 写入记为
    # write-only 序列(SDK csfr.h JL_L1P 的 CMO[19],__WO;flush_dcache 用的
    # 0xff05c8=CMO[18] 一并建模,本前缀未出现)。flushinv_dcache 尾部
    # csync+ssync 走通(ssync 按 run4「无可见效果」模型),返回 sfc_drop_cache、
    # VM init 链继续 12k 步后撞上 ROM 0x4176 字节拷贝尾循环 `rep r2,4`(该次
    # 拷贝 len&3==0,count=0):opt-in zero_count_rep_skip(multi-block gate 的
    # 零跳过假设,memset 先例 rom_zero_rep_skip/0x4242、0x424c 的推广——体一
    # 次也不执行、PC 前进到体后、计数寄存器保持 0,FM-1 run3「递减到 0」在
    # 0 次执行下的自然延伸;count=0 本身 BR28 未测量,报告以
    # zero_count_skip_is_assumption 计数、不声称硅验证)。跳过两次(0x4176 尾
    # ×2)后链继续 892 步(ROM 拷贝族再加 2 个非零寄存器计数 rep、CRC 引擎
    # 再用 2 次 12→14),停在 ROM 0x416c(0x1fd74→0x413c memcpy 字主体首条
    # ld.w.pin)读 0x60fdfe0——源指针由 app 经 ROM 解析,恰为 flash 偏移
    # 0x100000(1MiB,app flash 偏移探测表 0x060A0FA4 {0,0x1000,0x10000,
    # 0x80000,0x100000} 的第 5 项;CPU 可见地址=0x06000000+偏移-0x2020 头)。
    # 按实测物理布局(docs/usb-recovery.md),0x0ee000 起是 bank B 应用区,
    # 0x100000 落在 bank B 内;官方 UFW 的 flash.bin 只含引导区+bank A
    # (0..0xE6000),bank B 字节不在任何公开工件里——不臆造,诚实停。
    require(r['stop_core']==0 and r['stop_pc']=='0x416c' and
            r['stop']=={'kind':'unmapped_memory','access':'read','address':'0x60fdfe0',
                        'size':4,'reference_sdk_register':None},
            'after the two zero-count REP skips the stop must be the genuine bank-B flash read')
    require(r['completed_steps']==176288,'the zero-count REP skip changed its step count')
    require(r['instruction_adapters'].get('zero_count_skip_is_assumption')==2,
            'the zero-count REP skip assumption must fire exactly twice (both 0x4176 tails) '
            'and be reported as an explicit assumption')
    dcu=r['peripherals']['dcu_cmo_projection']
    require(dcu['ports']['0xff05cc']=={'values':['0x61d7fe0'],'writes':1} and
            dcu['ports']['0xff05c8']=={'values':[],'writes':0},
            'the DCU invalidate port did not record exactly the one observed line address')
    require(dcu['write_only'] and not dcu['readback_observed'] and
            not dcu['port_index_semantics_verified'] and
            not dcu['cache_contents_or_coherence_simulated'],
            'the DCU CMO projection report is missing its explicit assumption record')
    require(r['instruction_adapters'].get('register_rep_multi_block_projection')==7,
            'the register-count REP projection must cover exactly the DCU loop (0xc70c) plus the '
            'six nonzero ROM memcpy counters (0x416a word bodies and 0x4176 byte tails) in the extended prefix')
    gate=r['peripherals']['sfc_clock_gate_projection']
    require(gate['select']=='0x3' and gate['select_writes']==1 and
            gate['select_write_only'] is True and gate['select_readback_observed'] is False,
            'the SFC clock +4 select write-only record differs')
    # SFC 主块窗口组(0xfe0218/+0x20/+0x24)write-only 记录:初始配置序列
    # 各写一次,+0x18 只有一次 |=2(or 置位,不经读回)。
    window=r['peripherals']['sfc_read_bus']['window_registers']
    require(window['base']=='0x61d7fe0' and window['base_writes']==1 and
            window['limit']=='0x7ffffff' and window['limit_writes']==1 and
            window['control_operations']==[{'kind':'or','operand':'0x2'}] and
            window['control_known_mask']=='0x2' and
            window['write_only'] is True and window['readback_observed'] is False,
            'the SFC window register record differs')
    require(r['instruction_adapters'].get('sfc_window_enable_rmw')==1,
            'the SFC window control RMW was not executed exactly once')
    # 两核 ICU region 组(opt-in icu_region_projection):sfc_drop_cache 把
    # [0x061d7fe0,0x061d8000) 配进两核——+0x14/+0x18 各一写(write-only 记录),
    # +0x10 命令 0xca000040 与 0xca000010(bootstrap 使能)同走 CON bit30
    # 完成投影(64 步 fixture),两核各完成一次,busy 读 14→74。
    icu=r['peripherals']['icu_initialization_projection']
    region=icu['region_projection']
    require(icu['commands']==[{'address':'0xffec10','value':'0xca000010'},
                              {'address':'0xffe410','value':'0xca000040'},
                              {'address':'0xffec10','value':'0xca000040'}] and
            icu['completed_commands']==3 and icu['busy_reads']==74 and not icu['pending_cores'],
            'the two-core ICU region commands did not run through the shared completion projection')
    require(region['registers']['0xffe400']=={'0x14':{'value':'0x61d7fe0','writes':1},
                                              '0x18':{'value':'0x61d7fff','writes':1}} and
            region['registers']['0xffec00']=={'0x14':{'value':'0x61d7fe0','writes':1},
                                              '0x18':{'value':'0x61d7fff','writes':1}},
            'the ICU region begin/end records differ from the observed sfc_drop_cache arguments')
    require(region['write_only'] and not region['readback_observed'] and
            not region['command_low_byte_semantics_verified'] and
            region['same_busy_machinery_as_command4'],
            'the ICU region projection report is missing its explicit assumption record')
    # 窗口组放行后 CPU0 第二次 pend irq125(commands 4→6),第二次交付把
    # 停泊的 CPU1 再唤醒一次:暂停点1 的真实第二次交付在 162,898 前沿被观察
    # 到。DCU 失效循环走通后 VM init 链继续与 CPU1 同步:又 pend 4 次(合计
    # commands 14/acks 7,交付 1+6 次,CPU1 每次回同一停泊 0x12c5ca)。
    deliveries=r['machine']['software_irq_entry_projection']['deliveries']
    require([(d['core'],d['irq'],d['handler'],d['reti_from']) for d in deliveries]==
            [(0,127,'0x12ad2a','0x12b608')]+
            [(1,125,'0x60662c8','0x12c5ca')]*6,
            'the repeated cross-core deliveries to the parked CPU1 did not occur through the repeat projection')
    require(r['peripherals']['software_irq_commands']['commands']==14 and
            r['peripherals']['software_irq_commands']['acknowledgements']==7,
            'the VM-init sync requests changed the software IRQ command bookkeeping')
    # 70 个体来自资源表扫描,第 71 个是 0x600a352 rep 4 4(拷 16 字节 boot_info
    # 头);其中第 1/3 轮(word0 含 padding、word2 是 vm_res)走未知字节运输,
    # 第 2/4 轮(vm_saddr/vm_size 全已知)走常规严格读。DCU 失效循环走通后
    # VM init 链的 ROM 拷贝族再加 9 个 rep.i 体(71→80)。
    require(r['instruction_adapters'].get('immediate_rep_multi_block_projection')==80,
            'the flash resource-table scan must execute its rep.i blocks through the multi-block projection')
    require(r['instruction_adapters'].get('rep_copy_unknown_transport_load')==2 and
            r['instruction_adapters'].get('rep_copy_unknown_transport_store')==2,
            'the boot-info padding/reserved words were not transported exactly twice')
    r=rows['crc_bankb_erased_fixture']
    # bank B 头 0xFF fixture(显式环境假设,unverified erased-bank fixture,非已知
    # 出厂状态;范围=探测表第 5 项的 32 字节头读取窗,其余 bank B 严格 unmapped)。
    # 消费者分支图(app 反汇编+trace 钉死,报告/HANDOFF 有全文):find_partition
    # 0x6010f32 主循环 5 项逐项读 32B 头→crc16(头 bytes 2..31)→0x6010f80 与头前
    # 2 字节期望值比较,不匹配→0x6010f5a 良性跳下一项;0xFF 头期望 0xffff、真实
    # 计算值 0x75f8(下面对黄金软件算法重算),必然不匹配→r4=5→0x6010f5c ifs
    # (r4>4)→0x6010fe6 return 1;调用者 VM init(0x6011210)free 临时对象、清
    # OTP 全局槽 827/828 后与成功路径汇合,VM init 返回值被 0x601205a 忽略——
    # 失败分支良性,由真实代码自己计算 CRC 并选择分支(不强制任何结果)。
    crc_ff=r['peripherals']['hardware_crc_projection']
    reference_ff=0
    for byte in b'\xff'*30:
        reference_ff^=byte<<8
        for _ in range(8):
            reference_ff=((reference_ff<<1)^0x1021)&0xffff if reference_ff&0x8000 else (reference_ff<<1)&0xffff
    require(reference_ff==0x75f8 and reference_ff not in (0,0xffff),
            'the golden CRC of an erased 0xFF header must stay a non-matching value')
    # 前 14 次往返与 crc_engine_roundtrip 完全相同(fixture 不改变既有前缀),
    # 第 15 次(result_reads[14])是 0xFF 头的真实计算值,后 4 次是探测失败后
    # VM init 链继续运行时的真实 CRC 使用——失败分支确实继续执行的证据。
    crc_base=rows['crc_engine_roundtrip']['peripherals']['hardware_crc_projection']
    require(crc_ff['seed_writes'][:14]==crc_base['seed_writes'] and
            crc_ff['result_reads'][:14]==crc_base['result_reads'] and
            crc_ff['fed_bytes']==crc_base['fed_bytes']+480,
            'the erased fixture must not change the first fourteen CRC round trips')
    require(crc_ff['result_reads'][14]=='0x75f8' and len(crc_ff['seed_writes'])==30 and
            crc_ff['fed_bytes']==900 and len(crc_ff['result_reads'])==30,
            'the fifth probe entry must really CRC the 0xFF header bytes and the chain must continue')
    require(crc_ff['result_reads'][15:26]==['0xc190','0xf271','0xb758','0x5ddd',
                                          '0xa1cd','0x9946','0xd1cf','0xc190','0xf271','0xb758','0x5ddd'],
            'the post-probe VM-init CRC usages changed after the fixture took the mismatch branch')
    # 簿记读边界已解决(st.d.sp.l 双字全 8 字节覆盖写,exact_doubleword_stores
    # 两半直写);第二数据窗的表 walker(上一路钉死:有限小窗两项、-248 跳过
    # 零副作用)与 qcy_bss 16 字节 VM 配置块(SET 路径已写入、7 未知字节与
    # boot-info padding/vm_res 同源,GET 消费者只读已知字段,字节形状 unknown
    # 运输放行)都走通后,旧前沿 184,113 停在 ROM 0x4178 字节尾读 bank B VM
    # 窗口基址 0x61d7fe0。本次(路 S):VM 区消费结构先反汇编+trace 钉死——
    # 0x6060f9a 读两半 magic 各 4 字节(0x6061094/0x606109e,flash 0x1da000/
    # 0x1db000)后 magic switch(0x60610a4..0x60610c2:0x5A5A5A5A/0xFFFF5A5A/
    # 0xFFFFFF56/0xFFFFFFFF/0xFF5A5A5A),擦除态 0xFFFFFFFF→0x6061106→
    # half-2 magic≠0xFF5A5A5A→0x6061120 call 0x600a7b6 空白检查:0x600a79e
    # 每半换算 CPU 地址后 0x600a78a 逐字节扫 len=0x1000 检查全 0xFF(实测
    # 0x600a78c 执行 8192 次、读 0x61d7fe0..0x61d9fdf,遇非 0xFF 立即中止)
    # ——数据依赖的结构性全扫描,逐窗扩 fixture 只会把 unmapped 停点后挪
    # 一字节,故按结构声明范围一次性映射 VM 区全长 0x2000 字节(范围=
    # boot_info vm 字段声明+消费者 4K 对齐切半算法+空白检查 len,全部是
    # 固件自己的结构声明;同第二窗「表结构=擦除态」语义)。全空白分支良性
    # (trace 钉死):两处格式化写 call(0x600a7da/0x600a7f6,只在半区部分
    # 写入且头无效时执行)零执行、清状态位、0x6061274 清理路径设 VM 空状态、
    # 读 sfc 头 2 字节(0x06000000,flash image 已知字节)与缓存版本不等→
    # 从 app flash 表 0x60a9344 重建位图、return 0——空白 VM 就是「全新
    # 芯片」的良性初始化路径,由真实代码自己逐字节扫描自己选分支。放行后
    # 链继续 44,137 步(寄存器计数 rep 11→16、双字存储 15→19、单存 rep
    # 13→16、irq_repeat_return 146→160;CRC 19 次往返 570 字节、IRQ commands
    # 16/acks 8、交付 1+7 全不变——空白扫描是纯字节比较,不经硬件 CRC、
    # 无跨核同步),旧前沿 228,250 停在 ROM 0x417e——memcpy 族的「不对齐源
    # 组装」变体。路 T(本次):opt-in exact_parallel_insert_load 按对称旧值
    # 假设执行 insert.rrii.p∥ld.bu.pin(A 半 insert 读 B 半 ld 写之前的旧
    # 寄存器——与 FM-1 run3 捕获的 exact_parallel_stores 方向相反,无硬件
    # 捕获证据;厂商 memcpy 的软件流水只有在旧值语义下才能把字节按序组装,
    # 未知源字节经 rep_copy_unknown_transport 零扩展运输、按字节来源组装
    # 传播)。放行后链继续 7,692 步(compose 对执行 4 次=2 轮 rep×2 对、
    # 寄存器计数 rep 16→21、rep.i 体 104→143、双字存储 19→21、
    # irq_repeat_return 160→202;CRC 19→26 次往返 780 字节、IRQ commands
    # 16/acks 8、交付 1+7 全不变——新增前缀是 app 侧重扫描与 memcpy,无新
    # 跨核同步),停在 app 0x6060df4 `ld.bu b[r0+3]` 读 0x61de1e3——
    # 0x6060dd6 call 0x60609ac(描述符 thunk,与 0x6060a4a 表 walker 同族)
    # 后 r0=[desc+8]+512=window(0x61ddfe0)+0x200=0x61de1e0,首个头字节读
    # 在 +3——bank B flash 偏移 0x1e0203,在第二窗 20 字节 fixture
    # (0x1e0000..0x1e0014)之外、仍在 0x1e0000 window 区内(VM 尾与 TON
    # 0x1e1000 之间)——旧决策点:window+0x200 结构区的消费者分支图
    # 与 fixture 范围声明(235,942)。路 U(本次):静态全图+动态 trace 钉死
    # window 区(0x1e0000..0x1e1000)不是被系统性枚举——消费者=cfg_tool/btif
    # 存储后端 ops 表(0x60c32c8 起 28 字节记录)的成员,init 0x6060d7c 是
    # 平台 init 表遍历(0x601206c)最后一项:双 thunk("flash/res/cfg_tool.bin"
    # +"flash/app/btif")后解析 window+0x200 的 8 字节首条目头
    # {u32 len;u32{b0 crc,bits20+ size}};0xFF 擦除态下 (len-1)=0xfffffffe
    # 无符号 >2047,真实代码只读头 4 字节就在 0x6060e0e 拒绝返回 0(bytes
    # 4-7 不读、CRC 不算、无 flash 写),条目有效位 b[0x126568] 保持 0 把
    # walker 0x60112c8(入口判 !=1 即跳)挡住;其余记录区消费者 0x6060c6a/
    # 0x600a412 的 walk 基址=getter(103)+OTP 槽 827(本前缀已清 0,槽 828
    # ==0 直接返回 -250)、表区写路径 0x6060b80 未调用——故 fixture 只按
    # 结构声明映射完整 8 字节头(第四窗,同 opt-in erased_bankb),不扩
    # 记录区其余 0xE00 字节。放行后链继续 473 步(仅双字存储 21→22;rep/
    # 运输/compose/CRC 26 次往返 780 字节/IRQ commands 16/acks 8/交付 1+7
    # 全不变——记录头校验是 4 字节读+分支,无新跨核同步),停在 app
    # 0x6011370 `mov.rr.p`(A 半 r6=r0 ∥ B 半 h[r4+0]=r5,0x6011366 记录
    # 解析 helper 入口把出参半字清零)写虚拟栈 0x400b96(物理 0x19f794,
    # 未初始化)——上游 pair 存储模型的簿记读边界(uninitialized_memory),
    # 与 183,486 的 st.d.sp.l 同类(半字部分写、邻字节真保持旧值,决策归
    # 下一路),非 bank B 数据可得性问题(236,415,旧前沿)。路 V:
    # exact_parallel_stores gate 扩展接受 B 半 st.h(独立子开关
    # exact_parallel_halfword_stores——该形状在 legacy 前缀已出现 2 次,
    # 并入主开关会改既有报告;证据形状 app 0x6011370 mov.rr.p∥st.h
    # h[r4+0]=r5):半字是部分写,只覆盖 2 个目标字节、同字邻字节保持原状
    # (含 unknown)——不能像 st.d 双字全 8 字节覆盖写那样断言「旧值无关」,
    # 按字节粒度直写(寄存器值全已知写 2 个真实字节;含 unknown 时走
    # write_abstract_byte 按字节来源运输,与 rep_copy_unknown_transport
    # 同语义)。放行后记录解析 helper 完成、平台 init 表走完、app_core 进入
    # power_init(0x6067876)。路 W(本次,p11_power_init opt-in):
    # ①0xf2c05c 配置 RMW(0x60678f4 `rmw.and.r &=0x3FD`,r7=0xf2c04c 与
    # PWR_CON 同一基址;app/ROM 全量唯一引用、从不回读)按 0xf2c054 先例进
    # reset 投影配置表(extra masks,opt-in);②call 0x60674aa 读已初始化
    # 数据字 [0x12521C] bit1=0(qcy_initialized_data 区真实 flash 字节)→
    # 非 wake 路径;守卫 b[0x1299F0](bss 清零区)也读 0→首boot 路径;
    # ③CLK_CON0 0xf2a414=1、SYS_DIV 0xf2a410=0(write-only 记录,p11_window
    # 投影);④"flash/res/p11_code.bin" 资源解析真实走通(get 0x6007ae8+
    # fill 0x600ae2e,CRC 再用 4 次 26→30),rep 4 16 拷描述符到 0x1251F8,
    # [r12+8]==128(BANKCB entry0 address 字段)过,0x6067990 字节循环把
    # 12112 字节公共段从 bank A flash 拷到 P11 RAM 0xf20080(≈48k 步,快照
    # SHA256 与 analyze_p11 entry0 真实载荷逐字节互证);⑤PWR_CON |=0x20
    # 请求(路 E 投影记录)、q32s PMU_CON 0xf2a0c0 |=1 释放(write-only 位
    # 记录)、csync、PWR_CON 轮询——主 init 早已发过 bit6 清标志命令
    # (0x6068cf2,复位投影记为 flag-clear),轮询读回按 lpwr 投影的
    # post_flag_clear_read(快照&~bit7,bit5=0)第一轮就退出:PWR_CON bit5
    # 请求-响应投影在真实前缀检验通过(requests=1、poll read=1)。
    # 链停在 0x60679ba `ld.bu.l r8=b[r4+1]` 读 0xf27f4b——堆信息 4 字节
    # (P2M 消息区 +0xa,P11 init 写入、值未证实)的第一读:heap fixture
    # (p11_heap_info_bytes)未提供,严格停 unmapped_memory(289,849,
    # 新前沿——需要显式 heap fixture 参数才能继续的诚实停点)。
    require(r['stop_core']==0 and r['stop_pc']=='0x60679ba' and
            r['stop']['kind']=='unmapped_memory' and r['stop']['address']=='0xf27f4b',
            'the P11 power-init frontier must stop at the first heap-info byte read 0xf27f4b')
    require(r['completed_steps']==289849,'the erased fixture scenario changed its step count')
    # P11 公共段拷贝逐字节互证:目的窗快照 SHA256 == p11_code.bin 资源 entry0
    # 真实载荷(analyze_p11 钉死值,资源 CRC 全过)。
    from analyze_p11 import analyze as analyze_p11_resource
    p11_entry0=analyze_p11_resource(firmware)['bankcb_entries'][0]
    snap=r['memory_snapshots']['p11_common_code']
    require(snap['initialized_bytes']==snap['size']==12112 and snap['sha256']==p11_entry0['data_sha256'],
            'the P11 common-code copy did not land the exact p11_code.bin entry0 bytes')
    # PWR_CON bit5 请求-响应(路 E)真实前缀检验:恰好一次请求 RMW、恰好一次
    # 清标志后轮询读回、轮询第一轮退出(PC 越过 0x60679b0 到 0x60679b4)。
    require(r['peripherals']['p11_low_power_response']['requests_observed']==1 and
            r['peripherals']['counts'].get('p11_lpwr_post_clear_poll_reads')==1,
            'the PWR_CON bit5 request/response projection did not pass the real-prefix poll exactly once')
    window=r['peripherals']['p11_window_projection']
    require(window['q32s_pm_con']['rmw_writes']==1 and window['q32s_pm_con']['known_mask']=='0x1' and
            window['stores']['0xf2a410']=={'values':['0x0'],'writes':1} and
            window['stores']['0xf2a414']=={'values':['0x1'],'writes':1},
            'the q32s release RMW or the P11 clock register records differ')
    require(r['peripherals']['reset_history_projection']['configuration']['0xf2c05c']==
            {'known_mask':'0xfffffc02','known_value':'0x0'},
            'the 0xf2c05c configuration RMW must record exactly the cleared bit1+bits10+ as known zero')
    require(r['instruction_adapters'].get('parallel_halfword_store_old_registers')==3,
            'the halfword pair-store extension must fire exactly three times in the extended prefix')
    # compose 对(装载+组装)假设执行:legacy 前缀内 2 轮 rep×2 对=4 次;P11
    # 资源解析后再 +4 次(新的 2 轮),adapter 名带 _is_assumption 显式标注
    # 旧值方向无硬件捕获证据。
    require(r['instruction_adapters'].get('parallel_insert_load_old_registers_is_assumption')==8,
            'the insert/load parallel pair must execute exactly eight times (four REP rounds) '
            'through the old-register assumption adapter')
    require(r['instruction_adapters'].get('exact_doubleword_store')==25,
            'the doubleword stores of the extended prefix must run through the exact gate')
    require(r['peripherals']['software_irq_commands']['commands']==16 and
            r['peripherals']['software_irq_commands']['acknowledgements']==8,
            'the extended VM-init sync requests changed the software IRQ bookkeeping')
    deliveries=r['machine']['software_irq_entry_projection']['deliveries']
    require([(d['core'],d['irq'],d['handler'],d['reti_from']) for d in deliveries]==
            [(0,127,'0x12ad2a','0x12b608')]+[(1,125,'0x60662c8','0x12c5ca')]*7,
            'the extra cross-core delivery after the probe mismatch differs from the repeat projection')
    # ROM 拷贝族/CPU0-CPU1 同步在 power_init 放行后不变:rep.i 体 143→165
    # (+22:P11 资源 get/fill 链的 rep 4 16/rep 4 4 拷贝)、零计数跳过 3 不变、
    # 寄存器计数 rep 21→26(+5)、双字存储 22→25、unknown 运输 9/9 不变(compose
    # 对源是 flash 已知字节)。CRC 26→30 次往返 900 字节(+4 次:P11 资源头
    # 校验,末值 0x239e);IRQ commands 16/acks 8、交付 1+7 全不变——P11 资源
    # 拷贝不经硬件 CRC 以外的跨核同步,公共段拷贝是纯 CPU0 字节循环。
    require(r['instruction_adapters'].get('immediate_rep_multi_block_projection')==165 and
            r['instruction_adapters'].get('zero_count_skip_is_assumption')==3 and
            r['instruction_adapters'].get('register_rep_multi_block_projection')==26 and
            r['instruction_adapters'].get('rep_copy_unknown_transport_load')==9 and
            r['instruction_adapters'].get('rep_copy_unknown_transport_store')==9,
            'the fixture-scenario REP projections differ from the observed prefix')
    require(crc_ff['result_reads'][26:]==['0xa1cd','0x9946','0xd1cf','0x239e'],
            'the P11 resource resolution must add exactly four CRC rounds ending 0x239e')
    r=rows['p11_heap_experimental_config']
    # 堆信息 4 字节的显式实验配置(P11_HEAP_INFO_DERIVED 静态推导候选,derived-
    # not-measured):本场景的一切结论都是条件通过,不构成对堆值或 P11 执行的验证。
    # 传入后链真实消费:0x60679ba/be 首读(begin L/H 组装 r5=0x3d0c,0x60679d8 并行
    # 对 r0=r5|0xf20000=0xf23d0c,watch 钉死)、call 0x60114ca(3,0xf20000,0xf23d0b)
    # (P11 RAM protect 区注册,SDK p11_app.h P11_RAM_PROTECT_END=P11_HEAP_BEGIN 语义)、
    # 0x60679ee..f4 四字节再读(r0 依次 0x3d/0x0c/0x39/0xf4)、"pmu_task" 创建
    # (call 0x6067242,r2=名字 0x6083a6a,r0=入口 0x6068f0c)。pmu_task 创建触发
    # 真实多任务往返:CPU0 又收两次 irq127 调度交付(reti_from 都是调度等待点
    # 0x12b608),第一次 RTI 进 pmu_task 入口 0x6068f0c(独立栈 sp=0x401ffc,时间片
    # 执行到 sp=0x401f3c 后让出),第二次 RTI 回调度器、切回 app_core,power_init
    # 从任务创建调用返回处继续。其后 0x6067a32 icfg 位图检查(b[r6+846]=0)跳过
    # 0x6067a38..6c(0xfd5108 RMW 族与 0xf2c278/0xf2c244 都在被跳过的块里,本前缀
    # 不触及),0x6067a74 并行对(A 半 r0=r7-0x174c=0xf2a900、B 半 st.b
    # [0xf27fd6]=r4)是主核对 P11 RAM M2P 消息区的首写(SDK lp_ipc.h 命名锚点
    # M2P_RVD2PVDD_EN,M2P+0x56)——实测落盘 0x00:静态看 0x6067a6c `r4=1` 在被
    # 跳过的块里,实际路径的 r4 是 0(写 0=清除)。本次(路 AA)三件放行:
    # ①M2P 区(0xF27F80..0xF27FE0,SDK p11_app.h 自己声明的范围)映射为普通
    # 可写消息 RAM(写真实生效、读回最后值;0x60674b8 写 M2P+0 的 LRC 周期字节
    # 后自己回读三次推导 50/200/600us 定时器字节——「写后读回」由真实代码消费,
    # 快照钉死 prd=5、10、40、120);②exact_parallel_byte_stores 子开关(同半字
    # 先例):字节存储单字节全覆盖、旧值完全无关,直写 1 字节;③lp_timer_init_
    # projection(独立开关,write-only 记录):P11_LPTMR0 CON0/1/2=0x5C/0x53/0
    # (r7-0x174c 算出,SDK p11_sfr.h P11_LPTMR0_BASE=0xf2a000+map_adr(0x09,0))、
    # PMU 配置族 0xf2c04c=0x21/0xf2c050=0x41(SDK 无名)、JL_LRCT CON 0xfd3600
    # 四次位 RMW(SDK br28.h ls_base+map_adr(0x36,0),CON __WO;0x6067aae
    # &=0xFFFFFFC1 清 bits0-5、0x60674fc &=~1、0x6067500 |=0x40、0x6067506 |=1,
    # 按位记录不经读回)。路 AC 前沿 300,236 曾停在 0x6067514 读 CON(trim 使能/
    # busy 轮询读回无证据)。
    # LRC trim 完成投影(lrct_trim_done,路 AD):0x6067514/0x606751a 两读 CON 都回
    # 投影字 0xC1(bit7 完成投影置 1 + RMW 已知 bit0=1/bit6=1),0x6067516 bit0=1
    # 不跳、0x606751c bit7=1 不跳——轮询第一轮真实退出(固件语义:beq.and.ri 在
    # bit7==0 时回跳 0x606751a,等的是置位不是清零;旧记录「busy 死等清零」按
    # 编码修正)。路 AE 曾停在 0x6067520 读 NUM(0xfd3604,SDK __RW trim 结果
    # 计数)——硅测量值无任何推导证据。本次(路 AF)按堆信息 fixture 先例把
    # NUM 做成显式探路值参数(lrct_trim_result,推导链见 LRCT_TRIM_RESULT_PROBE
    # 常量注释):NUM=38400 → 0x6067526 非零不跳 → 0x6067532 读设备结构门控字节
    # b[0x126874](运行态实测已初始化=1)→ 64 位分子 0x1_C9C38000=7.68e9 →
    # 0x6067546 64/32 无符号除法,商=200000(=固件自己的标称 LRC 频率立即数)
    # → [0x129D98]=raw、首次经填充路径 20 项历史全部=商、平均=[0x129D88]=
    # 200000、b[0x129D8C] 0→1 → 0x60675e0 call 0x60674ec(r0=1)重启 trim(CON
    # &=~1/|=0x40/|=1,RMW 4→9)→ 0x6067ac8 第二轮 LRC 写以 [0x129D88]=200000
    # 重推 prd=5/M2P 05/0a/28/78(与第一轮同值,数值自洽)→ **第一个任务创建**
    # 0x6067ae0(call 0x6066c26→0x6066a7e,入口 0x606750c=trim 函数,优先级
    # 10,watch 钉死)——任务创建路径内部调定时器 wrapper 0x6000898(r0=0xa)
    # → 0x60008c0 定时器配置 → 0x60008c6 启动 → 定时器启动函数 0x6000828 内
    # 0x600087c `rmw.or.modimm [r3+0]|=1`:TIMER0 0xfd0400 CON 0x1400|=1 →
    # 0x1401(mode=1 启动、时钟源字段 bits10-13=5)。路 AG 在此诚实停
    # (unsupported_timer_clock_source)。路 AH 按三方证据命名放行:source 5
    # =STD12M(标准 12MHz,SDK gptimer_hw.h 宏 GPTIMER_CLK_SRC_STD12M=
    # 0b0101<<10、SDK 驱动 gptimer_hw_v2.c bitcode IR 的 src<<10|div<<4 与
    # std_clk==12→字段 5、app 自己的换算表 0x60A2B5C[src5]=12MHz+0x600079A
    # 原始字段 RMW+PRD=12*10000/1-1=119999=10ms 精确闭环;LRC 是字段 3,
    # 「trim 后切校准 LRC」的语境推断被否定),opt-in timer0_std12m_source
    # 起计数:12MHz 相对 RC16M 参考系 3/4 速率、PRD+1=120000 tick 到期。
    # **第二个任务创建 0x6067b12(入口 0x6069538)已进入**:SDK 调用返回后
    # 句柄存 0x6067b22 h[0x129D9C]=r0,读 b[r6+98] 门控字节(运行态非 0/1)
    # 走 else 分支 0x6067b30 `r0=2` → 0x6067b32 call 0x6067668(LP 定时器
    # 配置函数,body 0x6067668..0x60676ea):0x606766c `r0=0xF2A908`(P11_LPTMR0
    # CON2)→ 0x6067672 ld.w 读 CON2 → 0x6067674 `&=~0xF` 清 CLK_SEL bits0-3 →
    # 0x6067678 `|=3` → 0x606767c st.w 写回 → 0x606767e `rmw.and.nmodimm
    # [r0]&=~0xF0` 清 CLK_DIV bits4-7。路 AL 按 SDK p11_sfr.h 证据(该结构
    # CON0/CON1/CON2/PRD/RSC 都标 __RW,仅 CNT __RO)实现 opt-in 的
    # lp_timer_con_readback_projection:CON 读回=最后写值(SFC 时钟 +8 先例
    # 同族,只在固件已写过至少一次后返回,不臆造复位值)——CON2 读回 0、复合
    # RMW 读回 3,RMW 真实走通,CON2 末态=3(CLK_SEL=3/CLK_DIV=0)。返回后
    # 0x6067b36 紧接 call 0x60677a4(P33 模拟配置函数,body 到 0x6067848)。
    # 路 AM 按同族先例放行 P3_ANA_CON5 读回:0x60677aa r1=0xF2C248(P3_ANA_CON2
    # 基址)→ 0x60677b0 `rmw.and.r [r1+12]&=0x3FC7` 读 P3_ANA_CON5(0xf2c254=
    # P33_ACCESS(0x95))、0x60677b8 再读、0x60677ce 写回;p33_sfr.h 只是地址宏、
    # 无 __RW/__RO/__WO 类型标注(与 p11_sfr.h 的 P11_LPTMR_TypeDef 不同),但
    # 同族 P3_ANA_CON6(0xf2c258)已在 VOLTAGE_CONFIG 按「显式合成初始字 0+读回
    # =当前寄存器值」建模,opt-in p33_analog_config_projection 把该函数读写的
    # P3_ANA_CON2(0xf2c248)/P3_ANA_CON5(0xf2c254)按同一先例覆盖(合成 0、
    # 不臆造硅模拟状态)。放行后函数继续:P3_ANA_CON6 三次读改(VOLTAGE_CONFIG)、
    # 0x60677f4/f8 并行对(A 半 r2=r1-500=0xf2c054、B 半写 P3_ANA_CON6 旧值)后
    # **0x60677fa `rmw.and.r [r2+0]&=0xFF7F` 读 P3_RST_CON0(0xf2c054=P33_ACCESS
    # (0x15))撞 reset_configuration_operation_not_modeled 诚实停**——0xf2c054
    # 在 RESET_CONFIG_MASKS 里记录的 and 掩码是 0x3fdf(主复位初始化路径),而
    # power_init 的 P33 配置函数用 0xFF7F/0x7FBF 两个**不同**掩码清 bit7/bit6,
    # 复位投影的严格掩码校验拒绝——0xf2c054 的多掩码访问是新独立决策点,归
    # 下一路。power_init 返回(0x60120e0)仍在停点之后;第一个任务句柄半字
    # 0x6067aec h[0x129D94]=1 已执行(旧记录「0x129D90」系地址笔误,实测目标
    # 是 0x129D94)。路 AO(掩码集扩展)放行 0xf2c054 的两个不同掩码后,函数继续:
    # 0x60677fa `rmw.and.r [0xf2c054]&=0xFF7F`(清 bit7)与 0x60677fe `&=0x7FBF`
    # (清 bit6)按掩码集放行(与主复位初始化 0x6068c80 的 0x3fdf 清 bit5 按位合并:
    # 0xf2c054 末态 known_mask=0xffffc0e0=bit5/6/7/14/15/16-31 已知 0);随后
    # P3_ANA_CON2(0xf2c248)三次读改(p33_analog_config 已覆盖,末态 0x70=bits4/5/6)、
    # 0x606781e/22 并行对(add∥st.w)写回。路 AQ(P3_WKUP_DLY 配置 RMW)放行
    # 0x6067824 `rmw.and.nmodimm [0xf2c114]&=0x7F8`(掩码 bits3-10 置位:保留 bits3-10、
    # 清 bits0-2 与 bits11-31,掩码反汇编钉死)后,函数续跑:0x6067828 r1=63→
    # 0x606782a r4=0xF27F8C(M2P 字节)→0x6067830/34
    # 并行对(r1=844∥b[0xf27f8c]=0x3f)→0x6067836 r0=b[0x129D1C]→0x606783a
    # `call 0x60676ec` 进入位域配置助手(0x60676ec..0x6067a2):0x60676ee
    # r1=0xF27F8C→0x60676f4 读 b[0xf27f8c]→0x60676f6 `r2&=~r0`→0x60676fa/fe
    # 并行对(r2=r0&1∥b[0xf27f8c]=r2)→… 把 r0(实参=b[0x129D1C])的 bits0-5 按
    # 位域组装进 r0→0x606772a r8=0xF2A62C(P11_SYSTEM.MEM_PWR_CON)→**路 AS 放行
    # 0x6067730/52/74/96 四次 `rmw.or.r [0xf2a62c]|=r0`(write-only 位记录,
    # 实参 b[0x129D1C]=0,四个 OR 操作数全 0,known_mask 0x0)+ 0x6067844 `ld.w
    # r0=[0xf2a62c]` 死读(opaque 放行,值被 0x6067846 ld.bu 覆盖)**——helper 返回、
    # 0x60677a4 返回、power_init 尾部(0x6067b3a..b64)执行、**power_init 在
    # 0x6067b64 返回(caller app_core 0x60120dc → 0x60120e2)**,链继续进布局在
    # power_init 之后的 P33 窗配置函数 0x6067b66(0x6067ce6 处调用,rets=0x6067cea):
    # 0x6067b6e r8=0xF2C548(=P33_ACCESS(0x152),SDK 无名)、0x6067b74 r6=r8+1016=
    # 0xF2C940(=P33_ACCESS(0x250),SDK 无名)、0x6067b78 r9=r6+32=0xF2C960。停点
    # 0x6067be4 `uextra.rrii.p ∥ st.b b[r6+8]=r0`(A 半 uextra.rrii 非纯 ALU,
    # 落入上游 pair 模型)对 [0xf2c948]=P33_ACCESS(0x252)(SDK 无名)做 4 字节
    # 合并读撞 unmapped_memory 诚实停——P33 窗「无名寄存器族」(0x150/0x250/0x252)
    # + 新 pair 形状的独立决策点,归下一路。TIMER0 精确计数:1484 步 × 3 tick/步
    # = cnt 4452(见下方断言),con 0x1401,expirations 10 不变;无新调度往返
    # (交付 1+7+2、commands 20/acks 10 不变)。
    require(r['stop_core']==0 and r['stop_pc']=='0x6067be4' and
            r['stop']=={'kind':'unmapped_memory','access':'read','address':'0xf2c948','size':4,
                        'reference_sdk_register':None},
            'the heap-experimental scenario must stop at the P33-window unnamed register '
            '(0xf2c948=P33_ACCESS(0x252)) read after MEM_PWR_CON and its dead readback '
            'are let through and power_init returns')
    require(r['completed_steps']==302581,'the heap experimental config changed its step count')
    reset_config=r['peripherals']['reset_history_projection']['configuration']
    require(reset_config['0xf2c054']=={'known_mask':'0xffffc0e0','known_value':'0x0'} and
            r['peripherals']['reset_history_projection']['accepted_configuration_mask_sets']==
            {'0xf2c054':['0x3fdf','0xff7f','0x7fbf']},
            'the P3_RST_CON0 configuration must record the bitwise union of the three observed '
            'masks (0x3fdf clears bit5, 0xff7f clears bit7, 0x7fbf clears bit6) as the accepted mask set')
    require(reset_config['0xf2c114']=={'known_mask':'0xfffff807','known_value':'0x0'},
            'the P3_WKUP_DLY configuration must record bits 0-2 and 11-31 cleared by the '
            'observed &=0x7F8 mask (known_mask 0xfffff807; bits 3-10 stay unknown)')
    mem_pwr=r['peripherals']['mem_pwr_con_projection']
    require(mem_pwr['known_mask']=='0x0' and mem_pwr['known_value']=='0x0' and
            mem_pwr['rmw_writes']==4 and mem_pwr['dead_reads']==1 and
            mem_pwr['write_only'] and mem_pwr['readback_observed'] and
            not mem_pwr['readback_value_consumed'],
            'the MEM_PWR_CON projection must record four OR-RMWs whose operands are all zero '
            '(the argument b[0x129D1C]=0 in this path) and one dead readback')
    timer=r['peripherals']['timer0']
    require(timer=={'con':0x1401,'cnt':4452,'prd':119999,'pending':False,'expirations':10},
            'TIMER0 must be running on source 5 with exactly 1484 steps * 3 ticks counted')
    require(r['peripherals']['p11_heap_info_fixture']['bytes']=='0c3df439',
            'the heap fixture must carry the derived candidate bytes 0c3df439')
    # M2P 消息区钉子字节:LRC 周期/定时器字节(05/0a/28/78=5/10/40/120,其余
    # 未写保持 unknown)与 RVD2PVDD_EN 字(实测 0x00)。第二轮 LRC 写(0x6067ac8,
    # 以探路值换算出的 200000 Hz)重推出与第一轮完全相同的字节——数值自洽的
    # 直接证据。
    require(r['memory_snapshots']['p11_m2p_lrc']['bytes_with_unknowns']=='05??0a2878??????' and
            r['memory_snapshots']['p11_m2p_lrc']['initialized_bytes']==4 and
            r['memory_snapshots']['p11_m2p_rvd2pvdd']['bytes_with_unknowns']=='????00??' and
            r['memory_snapshots']['p11_m2p_rvd2pvdd']['initialized_bytes']==1,
            'the M2P message-window bytes differ from the SDK-anchored LRC derivation and the '
            'executed M2P_RVD2PVDD_EN write')
    # LRC trim 结果块(0x129D88..0x129DEC):平均 [0x129D88]/计数字节(0x129D8C=1、
    # 0x129D8E=1)/第一个任务句柄半字 [0x129D94]=1(0x6067aec h[0x129D94]=r0
    # 已执行,任务创建 SDK 调用返回句柄 1;旧记录写成 0x129D90 系地址笔误,
    # 本路按实际快照修正)/raw [0x129D98]/20 项历史数组——探路值的全部数值
    # 侧效应逐字节钉死(200000=0x00030D40),全窗 0x64 字节另以 SHA 钉死
    # (窗口 >40 字节无逐字节串)。
    head=r['memory_snapshots']['lrc_trim_result_head']
    require(head['bytes_with_unknowns']=='400d0300'+'01000100'+'00000000'+'01000000'+'400d0300'*6 and
            head['initialized_bytes']==40,
            'the trim-result head must pin avg 200000, counter bytes 1/1, the first-task '
            'handle halfword 1 at 0x129D94, raw 200000 and the seeded history head of 200000')
    block=r['memory_snapshots']['lrc_trim_result_block']
    require(block['initialized_bytes']==0x64 and
            block['sha256']=='34684ecfe176785c838f443b98e79d99058ae2debd7fadf8af413174032e3adc',
            'the full trim-result block (raw + the twenty-entry history) must match the '
            'probe-value side effects byte-exactly')
    lp=r['peripherals']['lp_timer_init_projection']
    require(lp['stores']=={'0xf2a900':{'values':['0x5c'],'writes':1},
                           '0xf2a904':{'values':['0x53'],'writes':1},
                           '0xf2a908':{'values':['0x0','0x3','0x3'],'writes':3},
                           '0xf2c04c':{'values':['0x21'],'writes':1},
                           '0xf2c050':{'values':['0x41'],'writes':1}},
            'the LP-timer init records differ from the observed power_init tail '
            '(CON2 gains the two RMW writes of the LP-timer config function: explicit st.w=3 '
            'and the compound rmw.and.nmodimm writing 3 back)')
    # LRCT CON 位 RMW:初始化 4 次(0x6067aae + helper r0=1 三条)+ trim 函数
    # 内 stop-helper 2 次(&=~1/|=0x40)+ 末尾 restart-helper 3 次(&=~1/|=0x40/
    # |=1)= 9;lrc_trim_result_simulated 因显式探路值翻 True(报告语义如实)。
    require(lp['lrct_con']=={'address':'0xfd3600','known_mask':'0x7f','known_value':'0x41',
                             'rmw_writes':9} and
            lp['write_only'] and not lp['readback_observed'] and
            not lp['bit_semantics_or_completion_verified'] and lp['lrc_trim_result_simulated'],
            'the JL_LRCT bit records or the explicit assumption flags differ')
    trim=lp['lrct_trim_done_projection']
    require(trim['address']=='0xfd3600' and trim['trim_done_bit']=='0x80' and
            trim['reads']==2 and trim['projected_word']=='0xc1' and trim['known_mask']=='0x7f' and
            not trim['completion_timing_modeled'] and
            trim['trim_result_register']['address']=='0xfd3604',
            'the trim-done readback projection differs from the observed two poll reads '
            '(0x6067514 and 0x606751a)')
    fixture=lp['lrct_trim_result_fixture']
    require(fixture['address']=='0xfd3604' and fixture['value']=='0x9600' and
            fixture['reads']==1 and fixture['value_is_derived_not_measured'] and
            '7,680,000,000/38,400 = 200,000' in fixture['derivation'] and
            'not a silicon measurement' in fixture['derivation'],
            'the trim-result probe fixture record must pin 38400, one read and the '
            'derived-not-measured derivation chain')
    con=lp['lp_timer_con_readback_projection']
    require(con['addresses']==['0xf2a900','0xf2a904','0xf2a908'] and con['reads']==2 and
            con['readback_semantics'].startswith('last firmware-written value') and
            '0x6067672' in con['evidence'] and '0x606767e' in con['evidence'],
            'the LP-timer CON readback projection must cover CON0/CON1/CON2 with two reads '
            '(0x6067672 ld.w + 0x606767e rmw.and.nmodimm) and pin the SDK __RW evidence')
    deliveries=r['machine']['software_irq_entry_projection']['deliveries']
    require([(d['core'],d['irq'],d['handler'],d['reti_from']) for d in deliveries]==
            [(0,127,'0x12ad2a','0x12b608')]+[(1,125,'0x60662c8','0x12c5ca')]*7+
            [(0,127,'0x12ad2a','0x12b608')]*2,
            'the pmu_task creation must round-trip the scheduler with two more CPU0 irq127 '
            'deliveries (the trim-task creation at 0x6067ae0 adds none: its path stops inside '
            'the timer bring-up before any scheduler IPI)')
    rti=[e for e in r['machine']['events'] if e.get('kind')=='software_irq_rti' and e.get('core')==0]
    require(any(e['pc_after']=='0x6068f0c' and e['sp_after']=='0x401ffc' for e in rti),
            'the scheduler must really RTI into the pmu_task entry with its own stack')
    require(r['peripherals']['software_irq_commands']['commands']==20 and
            r['peripherals']['software_irq_commands']['acknowledgements']==10,
            'the two scheduler IPI round trips must be the only new software IRQ bookkeeping')
    # 新前缀不经硬件 CRC 引擎、不动 P11 资源链:CRC 30 次往返 900 字节与
    # crc_bankb_erased_fixture 完全一致。
    crc_heap=r['peripherals']['hardware_crc_projection']
    require(crc_heap['seed_writes']==crc_ff['seed_writes'] and
            crc_heap['result_reads']==crc_ff['result_reads'] and
            crc_heap['fed_bytes']==crc_ff['fed_bytes'],
            'the heap fixture must not change any hardware CRC usage')
    require(r['peripherals']['p11_low_power_response']['requests_observed']==1 and
            r['peripherals']['counts'].get('p11_lpwr_post_clear_poll_reads')==1,
            'the PWR_CON bit5 request/poll projection must stay at its single observed round')
    # adapter 计数:与 300,240 前沿一致的全部投影不变;并行对字节存储
    # 100→101(+1:第二轮 LRC 写 0x60674b8 的 M2P 字节写,LRC 快照值不变)→103(路 AQ
    # 新前缀 +2:0x6067830/34 与 0x60676fa/fe 两处 M2P 字节 pair 存储)、LRCT 位
    # RMW 4→9(+5:trim 函数内 stop-helper 2 + restart-helper 3)、reset 配置 RMW
    # 6→7(路 AQ +1:0x6067824 的 0xf2c114 &=0x7F8)。
    require(r['instruction_adapters'].get('exact_doubleword_store')==26 and
            r['instruction_adapters'].get('immediate_rep_multi_block_projection')==165 and
            r['instruction_adapters'].get('zero_count_skip_is_assumption')==3 and
            r['instruction_adapters'].get('register_rep_multi_block_projection')==26 and
            r['instruction_adapters'].get('rep_copy_unknown_transport_load')==9 and
            r['instruction_adapters'].get('rep_copy_unknown_transport_store')==9 and
            r['instruction_adapters'].get('parallel_halfword_store_old_registers')==3 and
            r['instruction_adapters'].get('parallel_insert_load_old_registers_is_assumption')==8 and
            r['instruction_adapters'].get('parallel_byte_store_old_registers')==104 and
            r['instruction_adapters'].get('lp_timer_bit_rmw')==9 and
            r['instruction_adapters'].get('reset_unknown_preserving_configuration_rmw')==7 and
            r['instruction_adapters'].get('mem_pwr_con_rmw')==4 and
            r['instruction_adapters'].get('proven_dead_mem_pwr_con_read')==1,
            'the heap-experimental prefix adapters differ from the observed conditional frontier')
    report['heap_info_value_is_derived_not_measured']=(
        'the p11_heap_experimental_config scenario feeds the four heap-info bytes 0c3df439 '
        '(begin=0x3d0c, size=0x39f4) as an EXPLICIT EXPERIMENTAL CONFIG derived statically from '
        'the p11_code.bin common segment (link-time wide-immediate movs at P11-local 0xa12 '
        '(0x3d0c) and 0xa26 (0x39f4), each followed by the byte-store sequence into 0x7f4a..0x7f4d '
        'whose pointer immediate 0x7f4a sits at 0xa1a; raw bytes c0ff0c3d0000 / c5ff4a7f0000 / '
        'c0fff4390000 cross-checked directly, never executed: no q32s execution exists in this '
        'repository). Consumer semantics match the pinned SDK headers (lp_ipc.h P2M heap offsets '
        '0x00a..0x00d, p11_app.h P11_HEAP_BEGIN=P11_RAM_BASE+(H<<8|L) / P11_HEAP_SIZE=(H<<8|L)), '
        'and the size value has an independent layout-level cross-check: heap end 0xf27700 equals '
        'the SDK-declared VAD_DVAD_CONFIG_BEGIN (0xF28000-0x20-0x60-0x40-0x20-0x780-0x50-0x50). '
        'The value is nevertheless DERIVED, NOT MEASURED: whether the q32s core really executes '
        'this init path at boot is unproven (known decode gaps), so every step past 289,849 in '
        'that scenario (including the pmu_task scheduler round trip, the M2P message-area writes, '
        'the 300,240 LRCT NUM boundary, the trim-result probe consumption, the 301,097 '
        'TIMER0-clock-source stop and the 301,540 STD12M pass-through to the LP-timer '
        'CON2 readback boundary) '
        'is a CONDITIONAL PASS that must not be reported as verified; without the fixture the '
        'crc_bankb_erased_fixture frontier stays pinned at 289,849 on the unmapped 0xf27f4b read')
    report['p11_m2p_message_window_assumption']=(
        'the p11 scenarios with p11_power_init map the SDK-declared P11 M2P message area '
        '(p11_app.h M2P_MESSAGE_RAM_BEGIN 0xF27F80 .. +M2P_MESSAGE_SIZE 0x60, i.e. '
        '0xF27F80..0xF27FE0) as ORDINARY WRITABLE MESSAGE RAM: this is shared SRAM, not a '
        'peripheral register bank — main-core writes really land and read back the last value, '
        'which the firmware itself exercises (0x60674b8 writes the LRC period byte M2P_LRC_PRD '
        'at +0 and reads it back three times to derive the 50/200/600us timer bytes at +2/+3/+4; '
        'snapshot pins 05/0a/28/78). The first write is the pair at 0x6067a74 whose B half stores '
        'b[0xF27FD6] (lp_ipc.h M2P_RVD2PVDD_EN, M2P+0x56) with the EXECUTED value 0x00 — the '
        'statically visible r4=1 at 0x6067a6c sits inside the icfg-bitmap-skipped block '
        '0x6067a38..6c, so the executed path stores the pre-branch r4=0 (a clear, not an enable). '
        'The P2M area (0xF27F40..0xF27F80) stays UNMAPPED: nothing writes it in this prefix and '
        'the heap-info bytes keep the explicit p11_heap_info_bytes fixture semantics; if a later '
        'prefix writes P2M the window extends with the same RAM semantics. '
        'The lp_timer_init_projection write-only records (P11_LPTMR0 CON0/CON1/CON2 word stores, '
        'the unnamed PMU-family 0xf2c04c/0xf2c050 word stores, JL_LRCT CON 0xfd3600 bit RMWs) '
        'never fabricate readback by themselves: with only that switch the LRC-trim CON poll at '
        '0x6067514 stays the honest stop (the recorded 300,236 frontier shape); the conditional '
        'scenario additionally opts into lrct_trim_done_projection (see '
        'lrct_trim_done_projection_assumption) to cross it')
    report['lrct_trim_done_projection_assumption']=(
        'the p11_heap_experimental_config scenario opts into lrct_trim_done_projection: reads of '
        'JL_LRCT CON 0xfd3600 return the recorded RMW known bits (bit0 enable=1, bit6=1, mask 0x7f) '
        'with bit7 FORCED to 1, an explicit "trim complete" condition projection in the SFC '
        'PLL-lock bit15 pattern (0x12ba38 precedent). Evidence level: the poll SHAPE is disassembled '
        'and machine-verified (0x6067514 ld.w CON; 0x6067516 beq.and.ri bit0 — clear means not '
        'enabled, skip the whole trim; 0x606751a re-read; 0x606751c beq.and.ri bit7 whose '
        'hardware-confirmed semantics "branch if (r & (1<<n)) == 0" sends it BACK to 0x606751a '
        'while bit7 is clear, i.e. the firmware WAITS FOR bit7 TO BE SET — the earlier "busy, '
        'wait for clear" note was wrong and is corrected by the encoding), but the bit LAYOUT '
        'beyond that inference has no evidence: SDK marks CON __WO with no readback definition, '
        'no silicon value was measured, and the completion timing is not modeled (the projection '
        'reads back complete immediately, so the poll exits on its first iteration). Bits outside '
        'the RMW mask are not fabricated (the poll only consumes bit0/bit7). The very next '
        'instruction reads JL_LRCT.NUM 0xfd3604 (SDK JL_LRCT_TypeDef {CON __WO; NUM __RW}, the '
        'trim-result count, [r3+4] after the poll; app references the LRCT block only via three '
        'li sites of 0xfd3600 and the ROM nowhere): that count is a silicon measurement with NO '
        'measured value, so its read stays strictly blocked (lrct_trim_result_not_modeled) '
        'without the explicit probe value — the conditional scenario additionally feeds the '
        'derived arithmetic probe 38400 (see lrct_trim_result_probe_assumption) to walk the '
        'consumer chain; the consumer math it feeds is disassembled, the value itself is not a '
        'silicon measurement')
    report['lrct_trim_result_probe_assumption']=(
        'the p11_heap_experimental_config scenario feeds lrct_trim_result_value=38400 (0x9600) '
        'as an EXPLICIT PROBE VALUE for JL_LRCT.NUM, in the p11_heap_info_bytes fixture family: '
        'the strict stop (lrct_trim_result_not_modeled) stays the default for every other '
        'configuration. Derivation chain: the trim function 0x606750c, after the done-poll, '
        'reads NUM into r2 (0x6067520), retries on zero (0x6067526), selects the 64-bit '
        'numerator 0x1_C9C38000 = 7,680,000,000 via the device-struct gate byte at 0x126874 '
        '(executed value 1; the immediate raw bytes at 0x606753e are 00 80 c3 c9 = 0xC9C38000 '
        '— earlier notes writing 0xC9C9C9C0 transcribed the decimal 3385032704 wrongly), '
        'divides it by NUM (0x6067546, 64/32 unsigned) to get the converted LRC frequency in Hz, '
        'stores it at 0x129D98, seeds the twenty-entry history on the first run (count byte '
        '0x129D8E=1 was set by power_init at 0x6067aa6) and writes the average to 0x129D88, '
        'which 0x6067ac8 feeds to the second LRC period write (prd=1000000/freq — the first '
        'write used the nominal constant r0=200000 giving prd=5 and M2P bytes 05/0a/28/78). '
        '7,680,000,000/38,400 = 200,000 EXACTLY, the firmware\'s own nominal LRC constant, so '
        'the probe is the identity count of a perfectly nominal 200 kHz LRC and every downstream '
        'numeric side effect lands on the firmware\'s own constants (avg=raw=history=200000, '
        'M2P bytes unchanged). It is DERIVED ARITHMETIC, NOT A SILICON MEASUREMENT: the real NUM '
        'is a per-chip trim measurement, and passing this fixture only walks the disassembled '
        'consumer chain')
    report['timer0_std12m_source_assumption']=(
        'the p11_heap_experimental_config scenario opts into timer0_std12m_source: TIMER0 CON '
        'clock-source field 5 is NAMED STD12M (standard 12 MHz) on three independent pieces of '
        'evidence. (1) The pinned SDK header gptimer_hw.h (hardware_reference_files, sha256 '
        'e8e3e1c1…) defines GPTIMER_CLK_SRC_STD12M=0b0101<<10 alongside LRC=0b0011<<10 and '
        'RC16M=0b0100<<10 — source 4 matching the already-validated 16 MHz reference cross-'
        'checks the encoding. (2) The SDK driver gptimer_hw_v2.c (LLVM bitcode in cpu.a, '
        'disassembled IR) maps lib_gptimer_src_std_clk==12 -> register source field 5 with '
        'frequency select 12 MHz, assembles CON as src<<10 | div<<4, and its timer_div_table '
        'is the log2 of the app divider values. (3) The app itself: conversion table '
        '0x60A2B5C[src]=MHz has ONLY src5=12 nonzero; the divider table 0x60A2B9C matches the '
        'SDK GPTIMER_CLK_DIV_* macro order item for item; the config function 0x600079A raw-'
        'field RMWs (clear 0xFFFFC3FF, src<<10, div<<4, bit14 PND clear, CNT=0) and PRD=12*'
        '10000/1-1=119999 close the 10 ms period against the wrapper 0x6000898 r0*1000us '
        'demand — so the earlier "switches to the calibrated LRC" guess is REFUTED (LRC is '
        'field 3). Timing: 12 MHz is the firmware\'s own nominal conversion rate, NOT a '
        'per-chip silicon measurement; STD12M\'s physical origin is unverified. Counting '
        'runs at 3/4 of the RC16M(16 MHz) reference tick (fractional remainder accumulated '
        'per step), PRD+1=120000 ticks per 10 ms expiration, W1C bit14 and pending bit15 '
        'keep the existing evidence-scoped contracts; the first expiration lies 40000 model '
        'steps after the start RMW and Timer0 IRQ4 delivery remains unimplemented, so the '
        'pending visibility surfaces through the existing semantics. The strict stop '
        '(unsupported_timer_clock_source, the 301,097 frontier shape) stays the default; '
        'with the source let through, the conditional frontier advances 443 steps to 301,540 '
        'at 0x6067672: the second task creation 0x6067b12 (entry 0x6069538) has returned from '
        'its SDK create call (handle stored 0x6067b22 h[0x129D9C]), and its else branch '
        '(gate byte b[r6+98] neither 0 nor 1) calls 0x6067668 with r0=2, whose LP-timer '
        'configuration RMW on P11_LPTMR0 CON2 (0xf2a908: read -> clear bits0-3 -> |=3 -> '
        'write -> &=0xFFFFFF0F) hits the lp_timer_init_projection write-only boundary at '
        'the first read — lp_timer readback semantics is the NEXT independent decision; '
        'power_init\'s return (0x60120e0) still lies beyond the stop')
    report['p3_rstcon_mask_set_assumption']=(
        'the p11_heap_experimental_config scenario opts into power_init_rstcon_mask_sets: the '
        'P3_RST_CON0 (0xf2c054) configuration register is written with THREE different and-masks '
        'across the real firmware — 0x3fdf in the main reset-init path (0x6068c80 @ 0x6068c94, '
        'clears bit5), and 0xFF7F (0x60677fa, clears bit7) plus 0x7FBF (0x60677fe, clears bit6) in '
        'the power_init P33 analog-config routine 0x60677a4. The reset projection\'s strict '
        'single-mask check rejected the two power_init masks, so this scenario opts into an '
        'accepted-mask-set for 0xf2c054: each observed mask is applied bitwise through the same '
        'unknown-preserving PartialWord semantics (bits the mask clears become known 0, preserved '
        'bits stay unknown, readback still requires a fully known word), so the union of the three '
        'cleared bit sets is recorded bitwise (known_mask 0xffffc0e0: bits 5/6/7/14/15/16..31). '
        'No SDK field definition exists for P3_RST_CON0 bit6/bit7 — p33_sfr.h only has the '
        'P33_ACCESS(0x15) address macro and power_reset.h only enumerates reset reasons — so the '
        'extension records ONLY the truly observed masks and invents no unobserved bits; the '
        'register NAME (P3 reset control) and the firmware really clearing bits 6/7 are the whole '
        'evidence, not a claim about their silicon meaning. The strict single-mask behavior stays '
        'the default for every other configuration (and for 0xf2c054 without the opt-in), and the '
        'legacy reports are byte-identical. With the masks let through the P33 config routine '
        'reaches its P3_WKUP_DLY (0xf2c114) &=0x7F8 RMW, which is the separate '
        'power_init_wkup_dly_rmw opt-in described next')
    report['p3_wkup_dly_config_rmw_assumption']=(
        'the p11_heap_experimental_config scenario opts into power_init_wkup_dly_rmw: the '
        'P3_WKUP_DLY (0xf2c114=P33_ACCESS(0x45)) configuration register is written by the '
        'power_init P33 config routine 0x60677a4 with one compound rmw.and.nmodimm &=0x7F8 '
        '(0x6067824; the base r1=0xf2c248 P3_ANA_CON2 is decremented by 308=77*4 at 0x606781e). '
        'The mask 0x7F8 has bits 3-10 set, so the AND preserves bits 3-10 and clears bits 0-2 '
        'and 11-31. The mask 0x7F8 is the truly observed immediate; SDK p33_sfr.h only has the '
        'P33_ACCESS(0x45) address macro and defines no field bits, so "wakeup delay field" is '
        'only the register NAME plus the firmware really preserving bits 3-10, not a claim about '
        'silicon meaning. It is applied through the reset projection\'s unknown-preserving '
        'PartialWord semantics (cleared bits 0-2/11-31 known 0, preserved bits 3-10 stay unknown, '
        'readback still requires a fully known word). NOTE: 0xf2c114 is '
        'referenced a SECOND time in the app — a separate power-off/low-power routine 0x6067f84 '
        '(app.bin 0x67e84) loads 0xf2c114 directly at 0x6067f98 and reads it back at 0x6067f9e '
        'with r=(r&0x7F8)|0x7 before writing it back — so that readback is NOT modeled by this '
        'config-RMW projection: if the frontier ever reaches 0x6067f9e it stops honestly on '
        'unknown_reset_configuration_bits (a separate, future decision). With the &=0x7F8 let '
        'through the routine next calls the bit-field helper 0x60676ec, whose rmw.or.r '
        '[0xf2a62c]|=r0 reads P11_SYSTEM.MEM_PWR_CON (0xf2a62c, SDK p11_sfr.h __RW) — that '
        'OR-shaped access is a SEPARATE P11 SYSTEM-domain stop, now handled by the '
        'mem_pwr_con_projection route (see mem_pwr_con_rmw_assumption), not by this WKUP_DLY '
        'projection')
    report['mem_pwr_con_rmw_assumption']=(
        'the p11_heap_experimental_config scenario opts into mem_pwr_con_projection: the '
        'P11_SYSTEM.MEM_PWR_CON (0xf2a62c = P11_SYSTEM_BASE map_adr(0x06,0) + 11*4) register is '
        'written by the bit-field helper 0x60676ec (called from 0x606783a) with four compound '
        'rmw.or.r [0xf2a62c]|=r0 (0x6067730/52/74/96, each followed by a call to the RAM routine '
        '0x12e29c). SDK p11_sfr.h marks MEM_PWR_CON __RW in P11_SYSTEM_TypeDef, but __RW and __WO '
        'are the SAME macro (volatile — the R/W distinction is comment-only), and p11_app.h names '
        'the field bits 0-5 (MEM_PWR_CPU_CON/RAM0_3/RAM4_5/RAM6_7/RAM8_9/PERIPH_CON). The full '
        'app reference scan (3 literal sites, 0 in the ROM) shows the register is only ever touched '
        'by OR-RMW (set, helper) and AND-RMW (clear, the separate power-off routine 0x6067e82 '
        'rmw.and.invr &=~r0) with NO full-word store and no consumed readback — the one readback at '
        '0x6067844 ld.w r0=[0xf2a62c] (right after the helper returns) is overwritten by the very '
        'next instruction 0x6067846 ld.bu r0=b[r4], so its value is never consumed. There is '
        'therefore no "last full-word write" to read back, which is why this route chose '
        'write-only bit recording (PartialWord, like the cpu1_control kick bit / q32s PMU_CON |=1 / '
        'JL_LRCT bit RMW precedents) over the lp_timer "readback=last write" projection: readback '
        'stays strictly blocked (mem_pwr_con_readback_not_modeled) and the dead read is instead '
        'passed by the probe\'s opaque dead-read gate (R0 stays None until the 0x6067846 overwrite; '
        'no value is fabricated). In THIS boot path the argument byte b[0x129D1C] is 0, so all four '
        'OR operands are zero and the recorded known_mask is 0x0 — the write-only record captures '
        'the four RMWs without inventing any set bit. No memory-power effect is modeled. With the '
        'RMW and the dead read let through the conditional frontier advances 905 steps to 302,581 '
        'at 0x6067be4: power_init returns at 0x6067b64 (caller app_core 0x60120dc -> 0x60120e2), '
        'and a post-power_init P33-window config function 0x6067b66 (called from 0x6067ce6) then '
        'stores a byte to [0xf2c948] = P33_ACCESS(0x252) via the pair uextra.rrii.p || st.b; the '
        'A half uextra.rrii is not a "pure ALU" form so the pair falls to the upstream pair model, '
        'whose 4-byte merge read of 0xf2c948 stops honestly on unmapped_memory. 0xf2c948 '
        '(and the base 0xf2c548=P33_ACCESS(0x152) / 0xf2c940=P33_ACCESS(0x250) block) are '
        'UNNAMED in the SDK p33_sfr.h (which names only P33_ACCESS(0x00..0xd5)), so this is the '
        'next independent decision point — a P33-window unnamed register family plus a new pair '
        'shape')
    report['irq127_scheduler_loop_entered']=True
    report['app_core_entered_after_irq_rti']=True
    report['dual_core_sync_handshake_executed']=True
    report['hardware_crc_engine_modeled']=True
    report['erased_bankb_fixture_assumption']=(
        'unverified erased-bank fixture (NOT a known factory state), four evidence-bounded windows: '
        '(1) the 32-byte bank-B header read at 0x60fdfe0 (flash offset 0x100000, probe-table entry 5) '
        'is mapped read-only to 0xFF; the consumer find_partition 0x6010f32 really computes CRC-16 over '
        'the 30 header data bytes (golden value 0x75f8, recomputed in the assertion from the app software '
        'parameters) and compares it at 0x6010f80 against the header word 0xffff, so the benign mismatch '
        'branch (0x6010f5a next entry -> table exhausted -> return 1 -> caller frees the temp object and '
        'clears the OTP slots 827/828, the VM-init return value ignored at 0x601205a) is selected by real '
        'code, never forced. (2) The 20-byte window-table structure at 0x61ddfe0 (flash offset 0x1e0000, '
        'the window the qcy consumer 0x6060a4a caches into the global 0x126544) is mapped read-only to '
        '0xFF over the full on-flash extent declared by the app u16-pair table 0x60a1e8a (two entries '
        '{id=0x66,size=6}/{id=0x6e,size=6}, each 4-byte header + payload); with erased headers the '
        'header check 0x60609ec reads only the two 4-byte headers (LE32 0xffffffff -> return -248) and '
        'the caller loop 0x6060a9a skips to the next entry with zero side effects, again selected by '
        'real code. (3) The full declared VM region 0x2000 bytes at 0x61d7fe0 (flash offset 0x1da000, '
        'the two 0x1000 halves the VM-config consumer 0x6060f9a derives from the boot-info vm fields '
        'after the 8192 clamp) is mapped read-only to 0xFF over its whole structural extent: the '
        'erased magic words 0xffffffff route to the blank check 0x600a7b6 whose byte-by-byte scan '
        '(0x600a78a, len 0x1000 per half, observed 8192 loads over 0x61d7fe0..0x61d9fdf, aborting '
        'at the first non-FF byte) confirms an all-erased VM, skips both format-write calls, sets up '
        'the empty-VM state, rebuilds the bitmap from the app-flash table 0x60a9344 and returns 0 '
        'again selected by real code; the window is declared structurally, not incrementally, '
        'because the scan is data-dependent — a partial window would only move the unmapped stop '
        'one byte later. (4) The 8-byte first-record header at 0x61de1e0 (window+0x200, flash offset '
        '0x1e0200) of the record area the cfg-tool/btif storage backend initializes: its init member '
        '0x6060d7c (the last platform-init table call at 0x601206c) parses the header as '
        '{u32 len; u32{b0 crc, bits20+ size}}; with erased bytes len=0xffffffff and the unsigned '
        'range check (len-1)>2047 at 0x6060e0e rejects after reading only the first 4 header bytes '
        '(observed in the trace: bytes 4-7 unread, no CRC call, no flash write, return 0), leaving '
        'the entry-valid byte b[0x126568] at 0 which gates the find-by-index walker 0x60112c8 — '
        'the fixture covers the complete 8-byte header structure the consumer itself parses '
        '(same structure-unit semantics as window 2), not the remaining record area, for which no '
        'erased-state read exists (the other record-area consumers walk from getter(103)+OTP slot '
        '827 and the OTP slot 828==0 gate returns -250 immediately in this prefix). Every other '
        'bank-B address (including the TON window at flash 0x1e1000 and the record payload area '
        'beyond the 8-byte header at window+0x208) stays strictly unmapped; the '
        'crc_engine_roundtrip scenario without the fixture still stops honestly at 176,288 on the '
        'unmapped 0x60fdfe0 read')
    report['insert_load_parallel_pair_assumption']=(
        'opt-in exact_parallel_insert_load: the compose pair of the misaligned-source ROM memcpy '
        '(0x417e, rep r5,22: four ld.bu.pin byte loads + insert.rrii.p||ld.bu.pin pairs assembling '
        'bits 8/16/24 + one plain insert.rrii + st.w.pin) is executed with BOTH halves reading the '
        'pre-pair register file: the A half (insert) reads the register the B half (ld.bu.pin) '
        'writes in the same pair. FM-1 run3 hardware capture only established the opposite '
        'direction (exact_parallel_stores: the second half reads the register as it was before the '
        'first half); the A-reads-what-B-writes direction has NO hardware capture evidence and is '
        'a symmetry assumption — the only semantics under which the vendor compiler software '
        'pipeline assembles the bytes in order (a new-value read would skip/duplicate source '
        'bytes and memcpy would be wrong). Reported through the '
        'parallel_insert_load_old_registers_is_assumption adapter; unknown source bytes are '
        'transported (zero-extended UnknownWord, rep_copy_unknown_transport) and assembled '
        'byte-granularly with their physical origins; non-byte-aligned insert fields over '
        'unknown bytes still stop honestly')
    for r in report.get('candidate_prefixes',[]):
        d=r['machine']['software_irq_entry_projection']['deliveries']
        require(len(d)==1 and d[0]['vector_address']=='0x19fffc','candidate delivery differs')
        require(r['stop']['kind']=='unknown_core_special_register' and r['stop'].get('register')=='usp',
                'candidate handler left the modeled region unexpectedly')
    report['irq127_entry_checks_passed']=True
    report['generic_irq_entry_implemented']=False
    report['repeat_delivery_second_observation']=('observed at the 162898 frontier: after the SFC window '
        'writes are recorded, CPU0 really pends irq125 to CPU1 a second time (software IRQ commands '
        '4 to 6) and the repeat delivery projection wakes the parked CPU1 once more; the handler '
        'returns to the same idle park 0x12c5ca. Delivering it also required the cpu1_control '
        'release-bit (bit0) fix: the kick bit3 record (control word 9) must not revoke the released '
        'core delivery eligibility that the advance() pending checks gate on')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--rom',type=Path,required=True)
    p.add_argument('--firmware',type=Path,default=ROOT/'firmware/QCY_H3S_7018F_1T2_release_note_V1.1.8_011.ufw')
    p.add_argument('--repaired-candidate',type=Path);p.add_argument('--failed-r2',type=Path)
    p.add_argument('--report',type=Path,required=True);args=p.parse_args()
    rom=args.rom.read_bytes();check_rom(rom);module=upstream();app=application(args.firmware);header=package_header(args.firmware)
    image=flash_image(args.firmware)
    report={'firmware_sha256':OFFICIAL_SHA,'application_sha256':APP_SHA,'rom_sha256':ROM_SHA,'simulator_commit':PIN,
        'device_accessed':False,'whole_h3s_boot_simulated':False,'candidate_whole_boot_validated':False,
        'suitable_for_flash_approval_alone':False,
        'vector_slot_formula_evidence':'app bytes [r1+r0<<2]=r2 at 0x6065d90 after li 0x19FE00; caller 0x6066100 passes irq=0x7f-cnum handler=0x12AD2A',
        'scenarios':[irq_case(module,app,header,rom,'irq127_request_strict'),
                     irq_case(module,app,header,rom,'irq127_entry_minimal',entry=True),
                     irq_case(module,app,header,rom,'irq127_scheduler_entry',entry=True,banks=True),
                     irq_case(module,app,header,rom,'irq127_appcore_roundtrip',
                              entry=True,banks=True,idle_wait=True,rti=True),
                     irq_case(module,app,header,rom,'irq125_sync_roundtrip',
                              entry=True,banks=True,idle_wait=True,rti=True,unmaskable=True,
                              sfc_suspend=True,gpio_out_readback=True,kick_wake=True),
                     irq_case(module,app,header,rom,'irq125_repeat_inert',
                              entry=True,banks=True,idle_wait=True,rti=True,unmaskable=True,
                              sfc_suspend=True,gpio_out_readback=True,kick_wake=True,repeat=True),
                     irq_case(module,app,header,rom,'crc_engine_roundtrip',
                              entry=True,banks=True,idle_wait=True,rti=True,unmaskable=True,
                              sfc_suspend=True,gpio_out_readback=True,kick_wake=True,hardware_crc=True,
                              rep_block=True,rep_unknown_transport=True,repeat=True,icu_region=True,
                              dcu_cmo=True,zero_rep_skip=True,flash_image_bytes=image),
                     irq_case(module,app,header,rom,'crc_bankb_erased_fixture',
                              entry=True,banks=True,idle_wait=True,rti=True,unmaskable=True,
                              sfc_suspend=True,gpio_out_readback=True,kick_wake=True,hardware_crc=True,
                              rep_block=True,rep_unknown_transport=True,repeat=True,icu_region=True,
                              dcu_cmo=True,zero_rep_skip=True,erased_bankb=True,insert_load_pair=True,
                              halfword_pair=True,p11_power_init=True,flash_image_bytes=image),
                     irq_case(module,app,header,rom,'p11_heap_experimental_config',
                              entry=True,banks=True,idle_wait=True,rti=True,unmaskable=True,
                              sfc_suspend=True,gpio_out_readback=True,kick_wake=True,hardware_crc=True,
                              rep_block=True,rep_unknown_transport=True,repeat=True,icu_region=True,
                              dcu_cmo=True,zero_rep_skip=True,erased_bankb=True,insert_load_pair=True,
                              halfword_pair=True,p11_power_init=True,
                              heap_info_bytes=P11_HEAP_INFO_DERIVED,byte_pair=True,lp_timer_writes=True,
                              lrct_trim_done=True,lrct_trim_result=LRCT_TRIM_RESULT_PROBE,
                              timer0_std12m=True,lp_timer_con_readback=True,
                              p33_analog_config=True,power_init_rstcon_masks=True,
                              power_init_wkup_dly=True,
                              mem_pwr_con=True,
                              flash_image_bytes=image)]}
    for name,path,digest in [('repaired_candidate',args.repaired_candidate,REPAIRED_SHA),('archived_failed_r2',args.failed_r2,FAILED_SHA)]:
        if path is None:continue
        candidate,readonly=candidate_view(path,digest,args.firmware)
        r=irq_case(module,candidate,header,rom,name,entry=True,readonly=readonly);r['ufw_sha256']=digest
        report.setdefault('candidate_prefixes',[]).append(r)
    check(report,args.firmware);args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(report,indent=2)+'\n')


if __name__=='__main__':main()
