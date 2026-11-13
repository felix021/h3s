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
without the fixture. Later routes advanced the conditional frontier through
the WKUP_DLY/PORT_SEL/P3_AWKUP/P3_LS/P11_SYS_CON1 boundaries to 304,036
(see HANDOFF route AZ..BD entries); route BG adds the opt-in
fd31_adc_con_projection: the B4-opening "power helper" 0x60115b4 is really
an ADC control function (0xfd3100 = JL_ADC->CON in the reference br28.h
that anchored JL_LRCT; first value 0x20000, a rotated modimm, not 0x200),
its six whole-word writes are recorded write-only, and the chain then
honestly stops at the first CON readback 0x60116e6 inside the 32-round
sampling loop's caller 0x60116d0 (CON bit4 gate / bit7 busy poll and the
analog RES 0xfd3104 have no derivable values) at 304,317.
Route BN then released the CON read side (opt-in fd31_adc_con_readback:
last-written-value projection, the lp_timer CON family) and the chain ran
the full ADC sampling transaction: bit4=0 skip, OS-post veneer, setter
CON=0x46105d, and the 1000-round busy-wait that crossed three STD12M
expirations before hitting the CPU0 core-tick first overflow at 447,490.
Route BS (this change) wires core_tick_irq3_delivery into the scenario so
that overflow no longer stops the run — and the measured reality is that
the WHOLE ADC transaction executes inside ONE OS critical section: the
0x12b55c post veneer's cli + [0xffe1a8]=6 idiom raises IPMASK to 6 before
the setter writes and no IPMASK store occurs again through the entire poll
(the write window holds zero stores between the setter's CON writes and
the stop). The overflow therefore pends under the route-BF per-step
eligibility recheck (gates 0x300 open, IRQ3 nibble enabled, but priority 1
< IPMASK 6 — cli masks, never drops), the poll runs its FULL 1000-round
budget, six more STD12M expirations pile the same way (343252..663252,
nine total), and the chain stops honestly at the RES read 0x6011704
(fd31_adc_res_not_modeled, 0xfd3104 = JL_ADC->RES, __RO analog value with
no derivable source — the red line) at 689,402. Both the core-tick IRQ3
and the Timer0 IRQ4 pendings stay armed past the boundary: their first
eligibility moment (the critical-section sti) lies in the not-yet-executed
suffix, so nothing is delivered in this prefix.

Route BY (this change) closes the RES boundary with two more opt-ins and
reshapes the frontier. fd31_adc_pnd_completion (SDK gpadc_hw_v10.h
GPADC_CON_PND=7 + the gpadc_v10.c adc_wait_pnd/adc_get_res driver shapes;
lrct_trim_done precedent) is a STATELESS read-time completion projection:
guest loads of CON see bit7 set once the last write has EN=1, while the
atomic compound RMW keeps synthesizing from the raw word (recorded stores
identical to the no-PND world). fd31_adc_res_named_values
({0x10000:400,0x10006:500,0x10004:400}, derived-not-measured, the BU
route's option (a) with the channel identified by the P3_ANA_CON4[4:1]
analog-mux field instead of the RAM slot — the model never touches guest
RAM and the mux field IS what the hardware converts) serves the RES reads.
The 80 caller-A polls fold to first-read exits, the whole sampling
transaction completes in real arithmetic (item0 mean 400, floats
945.0/756.0 with runtime K=756, mode halfword 1 — resolving the BU
mode=0xFFFF doubt: the getter path DOES write mode=1, so the battery gate
takes the float-direct path 4x945=3780>=3200), and the Timer0 schedule
resets (the timer-based delay 0x12e256 reprograms PRD per call): the
first ELIGIBLE expiry at 336,337 (IPMASK=0) triggers the route-BE
timer0_irq4_delivery layer — fourteen real delivery round trips interrupt
the delay wait loop, each W1C-acked and RTI'd back. The core tick never
overflows in the route-BY world (the stop sits ~107 steps before the
path-independent 447,490 landmark). With the route-BV p33_analog_tail
projection wired, the W1[0] sequence ran the AWKUP clear helper and the
P3_CHG_CON0 RMWs and stopped honestly at the P3_ANA_READ analog poll
(p3_ana_read_not_modeled, 0xf2c064) at 447,383 — the route-BY frontier.

Route CB (this change) resolves that read with evidence: the pinned-SDK
charge_hw.h names P3_ANA_READ bit1=LVCMP_DET (ldoin>vbat comparator) and
bit2=LDO5V_DET (ldoin>0.6V detect), charge.c pins the thresholds in
comments, and the QCY W1[0] 20-round detect loop matches the SDK
check_charge_state() line by line — so the read side is NOT an analog
magnitude (no (a)-style projection possible: zero writers, no completion
semantics) but two digitalized comparator status bits. The opt-in
p3_ana_read_ldoin_low fixture (derived-not-measured) serves the constant
word 0 for EVERY read under the explicit battery-boot-no-external-supply
scenario anchor (ldoin ~0V: both comparators read 0, consistent with the
route-BY RES fixture world; derived_mask 0x6, other bits explicitly
zero-filled not derived, detector-validity is an explicit assumption).
Releasing the first read surfaces three things: the W1[0] computed-
addressing config family (P3_L5V_CON0/CON1 at r5+0x220 and P3_CHG_CON2
at r5+12 — literal scans never see them) joins the tail AM container;
the ~447,490 core-tick overflow delivers for real and, while its handler
runs, Timer0 requests land in-flight — the new irq_delivery_deferral
projection HOLDS them (never drops: the dispatch layer clears the model
in-flight bookkeeping so the next advance() re-requests after the RTI,
the same held-not-dropped philosophy as the route-BF masked pendings; IRQ
nesting stays unmodeled); and the OS-tick cascade (software IRQ127 through
the scheduler) reaches 0x6069538, whose GPCNT_CON 0xfd1100 read (bit0
enable gate + bit7 poll + the count at +4) stopped honestly with unknown
configuration bits at 601,880 (route CB).

The route-CG gpcnt_con_readback projection (read side = three-choice (a),
bit-granular last-firmware-write on the br28.h __RW CON anchor) answers
that read with exactly the bits the firmware itself forced (bit0=0 from
its own disable+clear pair), so the real code takes the skip branch: the
task returns without the NUM read or the bit7 poll, nine dispatches in a
row across the timeout window. The W1[0] twenty-round timeout path then
executes for real (41 fixture-served reads, no charge-debounce task), the
core tick delivers eight more overflows (nine total, 138 deferrals) and
Timer0 grows to 286 deliveries, until the world enters the mode-switch
function 0x6068152 (r0==1 branch). Route CL releases the JL clock family
DCCK read side (jl_clock_dcck projection, route-AM container semantics) so
the mode-switch body runs for real; the W1[1] walker then calls the
scheduler interrupt-context helper 0x6065c3a. Route CO releases the
active-priority low-byte test at 0x6065c4c (`if ((r0 & 0xFF) != 0)`,
bne.and.modimm.ri.l i=0xFF) via the partial_icfg_masked_branch handler: the
empty-active-priority fixture makes the low byte known 0, so value&0xFF=0
and the branch is not-taken; the helper then calls 0x12cb08 (RTOS critical
query -> 1), skips the error path, re-enters the critical section via
0x60659ee->0x12b55c (IPMASK 0->6), and stops at 3,070,955 @ 0x6065ccc
`st.w.sp.l [sp+8]=r9` on the upstream store model merge read of the
uninitialized local 0x19e2d4 (the st.d.sp.l / parallel-halfword-store
family, next three-choice).

The trailing timer0_irq4_delivery_probe / timer0_irq4_masked_probe scenarios
(route BE) layer the Timer0 IRQ4 delivery machinery on the full
p11_heap_experimental_config stack before the ~341,097-step first-expiry
hard deadline: the model raises IrqDeliveryRequest(core, irq=4) instead of
interrupt_delivery_not_implemented when (and only when) the unchanged
eligibility chain accepts the pending IRQ4 (per-core ICFG gates 8/9 +
IRQ_CONFIG word0 nibble + priority vs IPMASK), at most once per pending
episode (delivery_in_flight, cleared by the real handler W1C
rmw.or.modimm [0xfd0400] |= 0x4000 at 0x12ecf8 which Timer0.write already
models); the machine reads the REAL vector slot 0x19fe10 (app-written,
value 0x12ece8 = the QCY Timer ISR table entry 0, 28B software frame, not
the SDK 76B hwi.S veneer) and enters with the same explicit entry-bank and
one-shot symmetric-RTI assumptions as the software-IRQ/core-tick entries
(RTI tail anchor 0x12ed0c). The event clock is NOT enabled here (route BI).
Both scenarios use the explicit probe fixture timer0_irq4_expiry_probe_steps
to pull the first STD12M expiry to 200 model steps after the real start
write (counter seed only, labeled synthetic). A route-BE write trace
resolved the runtime eligibility question the gap map left open: the
driver-install request_irq loop enables IRQ4 (nibble 0x3, priority 1,
maskable) at ~step 110768 (0xffe100=0x30000, later 0x33b55) and nothing
clears it through the frontier, so timer0_irq4_delivery_probe injects NO
eligibility fixture — its delivery round trip (scheduler-wait interrupt ->
0x12ece8 real bytes: critical enter, W1C ack with the pending bit15
readback visible in the RMW word, empty callback dispatch, frame pops,
csync, RTI tail) runs entirely on real state; timer0_irq4_masked_probe is
the counterfactual control that clears the IRQ4 nibble ("not installed"
world) and pins the inert branch: the expiry only sets pending, no
request, no stop, same honest P3_LS frontier. Every delivery conclusion is
a counterfactual projection (entry banks/PSR high bits/bank writeback
uncertified), not a silicon certification.

The trailing core_tick_irq3_delivery_probe / core_tick_irq3_masked_probe
scenarios (route BF) layer the CPU0 core-tick (IRQ3) delivery machinery on
the same full p11_heap_experimental_config stack before the ~447,490-step
first-overflow hard deadline. A route-BF disassembly pinned the handler
chain end to end: tick_timer_init 0x60661e8 first stops the tick
(st.w.prer [0xffe0ec+core*0x800]=0 at 0x60661fe), registers the handler via
request_irq (0x6065d86) with irq=3/priority=1/handler=0x60661a4 (writing
the REAL vector slot 0x19fe0c and the IRQ3 nibble bits12-15 of
IRQ_CONFIG word0 = 0x3), then writes CNT=0/PRD=clk-derived and starts
(CON=1 at 0x606622e) — so by the start write the whole eligibility chain
is already real firmware state, and the probe injects no eligibility
fixture. The handler 0x60661a4 is a 28-byte software frame like the Timer0
one (push.srl {psr,rets,reti} + push.rl {r3-r0}), ACKs with the real
rmw.or.modimm [0xffe0ec+(cnum<<11)] |= 0x40 the model already implements
(CoreTickStopped.ack_rmw; only CPU0 also bumps the software tick counter
[0x125004] and calls the timeslice scheduler), pops both frames, csync,
rti at the exact tail 0x60661e6 the machine projection requires. One real
semantic find: the model previously judged eligibility ONLY at the
overflow instant, so an overflow inside a critical section (IPMASK raised
above the tick priority) was silently missed forever — route BF aligns it
with the Timer0 precedent: while pending, every step re-checks the
unchanged eligibility chain and raises IrqDeliveryRequest(core, irq=3)
when it passes (delivery_in_flight prevents duplicates until the handler
ACK). The expiry counter seed is an explicitly labeled synthetic probe
fixture; with the seed near the frontier the probe demonstrates the full
round trip (real handler bytes, ACK, RTI back to the interrupted code)
and then returns to the SAME honest fd31-ADC-readback frontier.
core_tick_irq3_masked_probe is the counterfactual control clearing the
IRQ3 nibble: the overflow only sets pending, nothing is requested or
delivered. The event clock is NOT enabled here (route BI). Every delivery
conclusion is a counterfactual projection (entry banks/PSR high
bits/bank writeback uncertified), not a silicon certification.

The trailing key_chain_scan_direct scenario (route S/sim-keychain) sinks
the key input simulation from the route-BW handler-entry injection down to
the ADC divider-level injection on the SAME full p11_heap_experimental_
config stack (the route-M frontier world, 3,072,236 @ 0x60012de). A new
post_stop hook on explore (default None keeps every legacy run
byte-identical) lets the scenario direct-call real firmware functions on
the live stopped machine: a synthetic config-523 sensor object, a REAL
adc_add_sample_ch registration of the key channel, the REAL sampling
transaction 0x60116D0 per phase switch (setter mux write + CON write +
PND-poll projection + the RES named-fixture read), and one 0x6001944 scan
callback per beat with the registry-entry pointer (its real sys_timer
arg). Everything from the divider query 0x6000E94 through debounce, the
gesture state machine and the post call 0x6000cba is real firmware code;
the ONLY synthetic input is the RES divider level. Observed posts: double
click 0x10a04 (gesture 4 = click-count 2+2, field-by-field matching the
route-BW synthetic event word 0x10000|(10<<8)|4), long press 0x10a01
(gesture 1 at the 75-beat threshold), single click 0x10a00 (gesture 0).
The S19 dispatch and handler consumption are the honest boundary (the
app_core message-loop consumer is outside the stopped world; route BW
already executed that handler from its entry). Full derivation, per-beat
debounce states and the honest-boundary list live in
docs/peripheral-input-simulation.md section 7.
"""
import argparse
import json
import struct
from pathlib import Path

from br28_unknown import word
from probe_dualcore import dual_case
from probe_task_returns import EMPTY_PRIORITIES
from probe_arch_startup import candidate_view, REPAIRED_SHA, FAILED_SHA
from probe_boot import application, flash_image, check_rom, ROM_SHA, OFFICIAL_SHA, APP_SHA, Blocked, ENTRY
from probe_boot_handoff import package_header
from probe_pi32v2 import ROOT, PIN, upstream, require

HANDLER_PSR_FRAMES={0x12ad30:'psr_push',0x12ad60:'psr_pop',0x12adae:'psr_push',0x12adde:'psr_pop',
                    0x12ad6e:'frame12_push',0x12ada0:'frame12_pop',
                    0x60662c8:'frame12_push',0x606633e:'frame12_pop'}

# 路 BE:Timer0 IRQ4 handler 0x12ece8 的 12B 特殊帧锚(gpio-irq 域六 handler
# 反汇编钉死:入口 push.srl {psr,rets,reti}、尾部 pop.srl 后 csync;rti;
# 28B 软件帧的另外 16B 是普通 push.rl {r3-r0},走既有 abstract 栈运输)。
TIMER0_IRQ_FRAMES={0x12ece8:'frame12_push',0x12ed08:'frame12_pop'}

# 路 BF:core tick IRQ3 handler 0x60661a4 的 12B 特殊帧锚(本路证据核验,
# 真实 app 反汇编三方互证:tick_timer_init 0x60661e8 的 0x6066204
# li.u32 r2=101081508=0x60661a4 经 request_irq(0x6065d86)写进向量槽
# 0x19fe00+3*4=0x19fe0c;handler 形状=push.srl {psr,rets,reti}(12B)+
# push.rl {r3-r0}(16B,28B 软件帧,同 IRQ4 handler 族)+cnum 分支+
# rmw.or.modimm [0xffe0ec+(cnum<<11)] |= 0x40 ACK+CPU0 分支
# rmw.add.s12 [0x125004]+=1(软件 tick 计数)+call 时间片调度+pop.rl+
# pop.srl@0x60661e2+csync+rti@0x60661e6——与 br28_machine.return_core_tick
# 的尾锚 0x60661e6、probe_core_tick.CORE_TICK_FRAMES 同值(此处本地定义
# 避免 probe_core_tick->probe_irq_entry 的循环 import)。
CORE_TICK_IRQ_FRAMES={0x60661a4:'frame12_push',0x60661e2:'frame12_pop'}

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

# P3_AWKUP_P_IE(0xf2c184)首次读的具名零值 fixture(路 BB;合成假设,非硅测量)。
# 证据与适用场景(全部 2026-11-13 路 BB 反汇编+运行态快照复核钉死):
# - 本 boot 前缀对 P3_AWKUP_P_IE 的唯一一次读是 walker 0x6067cb4 第二轮表
#   item[0](cfg=0xE2,通道 ch=2,w4=0)在 0x6067cac 的显式 ld.w——该寄存器
#   本次 boot 从未被写过,读的是复位值;读值 R 被消费(0x6067cae or 后
#   0x6067cb0 写回 W=R|(1<<2))。items[1]/[2](cfg=0xE4/0xE5,快照钉死 w4=2)
#   走纯 RMW 分支不读 P_IE——本前缀恰好一次读、首次读。
# - 独立分发函数 0x60691f4(push.srl 中断入口)的 N_IE/P_IE 消费型读
#   (0x60692a6/0x60692b4)不在本前缀(AWKUP 中断从未交付);N_IE 因此
#   不需要也不提供 fixture(整字 0 覆盖只给 P_IE 一个寄存器,不扩展到族)。
# - 零值是**显式探索样例**,不是「复位后 IE 全关」的硅事实声明:SDK
#   p33_sfr.h 只有地址宏、无类型标注、无位定义,复位值无任何直接证据。
#   整字 0(而非「IE 位全 0」)覆盖未知位与高位。
# - 依赖传播(静态位运算 M/K 分析):读值 R 进入 W=(R|0x4)——bit2 被固件
#   强制置 1(无依赖),bits{0,1,3..31}=M&~K 携带本 fixture 假设;零值下
#   写回 0x4,后续 items[1]/[2] 固件 RMW |=2/|=1 → 记录末值 0x7,其中
#   bits3..31 完全由 fixture 携带。fixture 只覆盖「首次读返回字」这一个
#   初始条件假设:写过之后与首次之后的读一律严格停(不立「普通存储式
#   读回」假设),未覆盖寄存器(N_IE 等)的读严格停。
P33_AWKUP_P_IE_FIRST_READ_ZERO={0xf2c184:0}

# JL_ADC->RES(0xfd3104,__RO)的具名档位 fixture(路 BY/sim-adcresfix,
# derived-not-measured;与 heap_info/trim_result 同族的显式数据 fixture)。
# 候选值推导链(docs/adc-res-consumer.md §6,路 BU 只读预研全图+三选一):
# - **值域硬约束[钉]**:RES 消费低 16 位、物理量程 0..1023(10 位;
#   0x60117a6 r1=1023、0x6000ebc 温度表哨兵 >1022);常量样本下 32/12 轮
#   截尾均值恰等于常量本身(自洽)。
# - **WBG04(0x10000)=400**:十位域内非零;32 轮循环(0x6012148)的
#   (sum-min-max)/30 → h[0x127D10]=400——vbg 基线自洽。
# - **VBAT_4(0x10006)=500**:物理锚 4×(500×K/400)=4×floor(K×1.25),
#   K=801±3d(divider 0x601171a 的 VBG trim 标称,SDK ADC_VBG_CENTER=800
#   互证)→ K=801 时 ≈4004mV=标称 4.0V 锂电池。电池门(0x601232e,阈值
#   h[0x127734] 编译内置默认 3200mV(0x6011c1a),11 连败→0x601248e 关机)
#   两条路径都过关:mode==1 float 直读 4004≥3200;静态链实际路径
#   (VBAT/VTEMP 项 mode 保持 memset 残留 0xFFFF≠1,[疑]点见文档 §5)走
#   divider 兜底 4×(0x4000×K/400)≥69,304(K∈[756,846] 全域)≫3200。
# - **VTEMP(0x10004)=400**:条件前沿无消费者(NTC enable=0 旁路),仅
#   「域内非零」约束——证据最弱的一项,显式标注。
# - **禁区[钉]**:低值(如全 0)→battery_mv=0→11 连败→关机死循环——任意
#   值非行为等价,具名常量+门核验是最低成本可辩护选择。
# - **通道识别**:RES 读时刻 P3_ANA_CON4(0xf2c250)[4:1] 复用域(setter
#   0x6011666 case1 → 0x6067d8a 把 (通道字&0xFFFF)<<1 写进该域,反汇编
#   钉死)=硬件实际转换的通道;通道字=0x10000|域值。
# - **三层身份**(codex 参谋采纳):通道识别有推导链;原始码是显式
#   synthetic fixture(电压比例不能唯一反推原始码);电压是该 fixture 下
#   固件自己算出的结果。真机 FD07 一次 RES 读(路 BA:通道可读)可把
#   WBG04 码值升级为 measured 并反解 Vref(会话仍 deferred/需授权)。
FD31_ADC_RES_NAMED_VALUES={0x10000:400,0x10006:500,0x10004:400}

# ---------------------------------------------------------------------------
# 路 S/sim-keychain:按键输入全链真实执行(直调扫描回调)的具名 fixture。
# 全链(路 Q docs/peripheral-input-simulation.md §2 钉死):ADC 队列项 value →
# 分档查询 0x6000E94(20 级阈值表)→ 扫描回调 0x6001944(去抖+手势状态机:
# 稳定窗口 ctx+9=2 拍、按下阈值 ctx+10=75 拍、释放稳定 ctx+12=20 拍、
# 长按重复 ctx+11=90 档)→ post 事件 0x6000cba(载荷 0x10000|(key<<8)|gesture)
# → app_core 队列。本场景在 p11_heap_experimental_config 世界停点后:
#   ①安装分档器 fixture ②直调 0x6001230 真实注册键通道队列项
#   ③直调 0x60116d0 真实跑一次采样事务(setter 复用域写+CON 写+PND 轮询
#   投影+RES fixture 读+清 PND) ④直调 0x6001944(0x60C0E20 注册表项)按拍
#   驱动去抖/手势/POST——这些全部固件真实代码;唯一合成输入=RES 档位值。
# 所有值 derived-not-measured(真实 config 523/键通道字在 bank B 记录区,
# 公开工件外,擦除态世界整体旁路是路 Q 钉死的现状)。
# ---------------------------------------------------------------------------
# 键通道字:0x10001(P3_ANA_CON4[4:1] 复用域值 1,BY 三通道 0/4/6 未占用)。
# 合成选择(真实键通道的域值在 config 523,公开工件外);通道字形状约束
# 来自 setter 0x6011666:(通道字>>16)<=5 走 PMU 模拟路径、(通道字&0xFFFF)
# 即复用域值(0x6067d8a 写 (ch&0xFFFF)<<1 进域)。
KEY_CHAIN_CHANNEL_WORD=0x10001
# 分档器阈值表/档位值:两档方案(路 Q §3 fixture 1 的建议形状)。分压档位
# 值从阈值表反推:阈值[0]=500 是 fixture 自定的无键/按下分界;释放态读数
# 400(≤500→键 255 无键),按下态读数 700(>500 且 ≤1022→键 id 表[1]=10)。
# 哨兵 1023(>1022)占满剩余 18 槽(表长 20 由 read_key 循环 `if (r0>19)` 钉死,
# 阈值取址对象+10+2i、键 id 取址对象+50+i)。键 id=10 对路 BW 已验的
# handler 0x60252c6 逻辑键映射(key10→7)。全部显式 synthetic,非测量。
KEY_CHAIN_THRESHOLD_RELEASE=500
KEY_CHAIN_RES_RELEASE=400     # 释放态分压读数(≤500 → 无键)
KEY_CHAIN_RES_PRESSED=700     # 按下态分压读数(501..1022 → 键 10)
KEY_CHAIN_KEY_ID=10
KEY_CHAIN_SENSOR_OBJECT=0x1274F4   # 分档器对象(解析器 0x60010be r9+3892 钉死)
KEY_CHAIN_OBJECT_POINTER=0x125320  # 对象指针槽(read_key 0x6000e96 li 1200928 钉死;
                                   # 路 Q 记 0x125720 是笔误,以指令立即数为准)
KEY_CHAIN_REGISTRY_ENTRY=0x60C0E20 # sys_timer 注册表项(flash 16B:enable=1/
                                   # ctx=0x124888/read_fn=0x6000E94/init=0x6001866,
                                   # init walker 0x60121f4-0x6012202 把它作为回调
                                   # arg 注册,扫描回调 [r0+4]=ctx/[r0+8]=read_fn)
KEY_CHAIN_ADC_QUEUE_VALUE_BASE=0x127D10  # 队列 value 字段基址(0x6001230/0x60348e8/
                                         # 0x6000e74 三处独立钉死,16B 项步进)
# 0x124C80 结构(0x60669f6/0x6001a2c/0x6034912 的 li 1199232 反汇编钉死;路 Q 记
# 0x124B30 是十进制换算笔误,以指令立即数为准——1199232=0x124C80):b[+0]=手势
# 状态机跟踪键、b[+5]=ADC 队列当前采样项索引(IRQ39)、[+36]=tick 计数
# (0x60669f6 读 ×10=ms,手势时长差的时间源)。
KEY_CHAIN_GESTURE_STRUCT=0x124C80
KEY_CHAIN_SYS_TICK=KEY_CHAIN_GESTURE_STRUCT+36
# r7=0x1265C0(0x6001954 li 1205696):+2 日志门、+3 按键活动计数、+25 组合键
# 标志、+26 点击计数(0x6001b08 的 n+2 编码源)。
KEY_CHAIN_CLICK_COUNT=0x1265C0+26
KEY_CHAIN_TICKS_PER_SCAN=1     # 显式假设:一拍=一个 sys_timer tick(扫描周期
                               # ctx[+16]=10ms,tick 单位 10ms,故 +1/拍)

def key_chain_sensor_object_bytes():
    """config 523 分档器对象的显式合成内容(read_key 消费面布局,70 字节)。"""
    obj=bytearray(70)
    obj[0]=1                                     # enable/键数(read_key 的 b[obj+0]==0 旁路门)
    struct.pack_into('<I',obj,4,KEY_CHAIN_CHANNEL_WORD)  # 键通道字(read_key [r5+4]→adc_queue_get)
    thresholds=[KEY_CHAIN_THRESHOLD_RELEASE,1022]+[1023]*18
    for i,threshold in enumerate(thresholds):
        struct.pack_into('<H',obj,10+2*i,threshold)     # 20 级 u16 阈值表(升序,>1022 哨兵)
    obj[50:70]=bytes([255,KEY_CHAIN_KEY_ID]+[0]*18)     # 20 项键 id 字节表
    return bytes(obj)

# 键链场景的 RES fixture=BY 三通道(世界前缀的既有采样事务依赖)+键通道两档。
# BY 场景本身继续用自己的 FD31_ADC_RES_NAMED_VALUES(legacy 报告不变);本表
# 只服务键链场景,且场景在采样阶段通过 hardware.fd31_adc.res_named_values
# 切换键通道的按下/释放档值(每次切换显式记录)。
KEY_CHAIN_RES_NAMED_VALUES={**FD31_ADC_RES_NAMED_VALUES,
                            KEY_CHAIN_CHANNEL_WORD:KEY_CHAIN_RES_PRESSED}

# 直调返回哨兵:0x100000 落在 ROM(0..0x20000)与 flash(0x06000100..)之间的
# 未映射空洞,直调循环在 decode 之前检查 pc==哨兵,从不真的取指。
KEY_CHAIN_DIRECT_SENTINEL=0x00100000

def key_chain_direct_call(cpu,machine,pc,args,*,max_steps=200_000,watch=()):
    """在停点世界的 CPU0 上直调一个真实固件函数,返回 (r0, steps, watch_hits)。

    受控步进循环=explore 主循环的最小子集:decode→step→after_step(簿记),
    不调 hardware.advance(外设时间冻结→中断交付冻结,显式假设:直调段在
    无中断窗口内执行;Timer0/core-tick 的 pending 在前缀停点本就挂着,
    冻结只是不推进,不发明也不丢弃)。入口/出口保存恢复 pc/sp/rets/flags/
    R0-R15,世界状态除被调函数自己的存储副作用外逐字段复原。
    """
    saved={'pc':cpu.pc,'sp':cpu.sp,'rets':cpu.rets,'flags':cpu.flags,'R':list(cpu.R)}
    cpu.pc=pc
    for i,value in enumerate(args):cpu.R[i]=value
    cpu.rets=KEY_CHAIN_DIRECT_SENTINEL
    steps=0;watch_hits=[]
    try:
        while cpu.pc!=KEY_CHAIN_DIRECT_SENTINEL:
            if cpu.pc in watch:
                watch_hits.append({'pc':hex(cpu.pc),'args':[word(cpu.R[i]) for i in range(4)]})
            insn,raw,size=cpu.decode(cpu.pc)
            before=cpu.pc
            cpu.step();steps+=1
            machine.after_step(cpu,before)
            if steps>max_steps:
                raise Blocked('key_chain_direct_call_budget',pc=hex(pc),steps=steps)
        result=cpu.R[0] if isinstance(cpu.R[0],int) else None
    finally:
        cpu.pc=saved['pc'];cpu.sp=saved['sp'];cpu.rets=saved['rets']
        cpu.flags=saved['flags'];cpu.R=saved['R']
    return result,steps,watch_hits


def make_key_chain_post_stop(app):
    """构造键链直调场景的 post_stop 回调(路 S/sim-keychain)。

    在 p11_heap_experimental_config 世界的停点后,于活机器 CPU0 上按序执行:
      ① 分档器 fixture 安装(0x1274F4 对象 + 0x125320 指针,derived-not-measured)
      ② 直调 read_key 0x6000E94 —— 分档层独立断言(无键项时 adc_queue_get
         未命中返回 1,1<=500 → 键 255)
      ③ 直调 adc_add_sample_ch 0x6001230(键通道字) —— 真实代码注册队列项
      ④ 采样切换 = 直调 0x60116d0(键通道字) —— 完整采样事务(setter 复用域写
         +CON 写+PND 轮询投影+RES fixture 读+清 PND),返回值即 RES 档位
      ⑤ 队列项 value ← 采样返回值(显式假设:EMA mode=1 的 0.2/0.8 一阶收敛
         稳态=RES 常量档位,跳过瞬态;IRQ39 handler 0x60348e8 的 EMA 写回段
         依赖中断帧上下文,不在任务态直调)
      ⑥ 按拍直调扫描回调 0x6001944(0x60C0E20) —— 去抖/手势/post 全真实;
         每拍前 [0x124B54] sys_timer tick +1(一拍=一个 tick=10ms,显式假设)
    三个手势序列:双击(短按×2 → post 手势 4 = 点击计数 2+2)、长按按下确认
    (按住 75 拍 → post 手势 1)、单击(短按×1 → post 手势 0)。post 观测在
    调用点 0x6001b5e(r0/r1 已装载)与返回口 0x6001b62(返回值)各记一次。
    """
    ctx_blob=app[0x60C3380-ENTRY:0x60C3380-ENTRY+24]   # ctx 真实初值(flash .data)
    sequence_log=[]

    def read_ram(cpu,address,size):
        data=cpu.mem.read(address,size)
        return int.from_bytes(data,'little')

    def write_ram(cpu,address,data):
        cpu.mem.write(address,data)

    def sample(cpu,machine,hardware,res_value,label):
        # 切 RES fixture 档位 → 真实采样事务 → 队列项 value ← 采样返回值
        hardware.fd31_adc_con.res_named_values[KEY_CHAIN_CHANNEL_WORD]=res_value
        value,steps,_=key_chain_direct_call(cpu,machine,0x60116D0,[KEY_CHAIN_CHANNEL_WORD])
        entry={'phase':label,'res_fixture':res_value,'sampled_value':value,'steps':steps}
        if value!=res_value:
            raise Blocked('key_chain_sample_mismatch',**entry)
        # 找键通道队列项(注册后 key 字段==通道字),写 value(EMA 稳态假设)
        slot=None
        for idx in range(11):
            if read_ram(cpu,0x127D08+16*idx+4,4)==KEY_CHAIN_CHANNEL_WORD:
                slot=idx;break
        if slot is None:
            raise Blocked('key_chain_queue_slot_missing',**entry)
        write_ram(cpu,KEY_CHAIN_ADC_QUEUE_VALUE_BASE+16*slot,struct.pack('<H',value))
        entry['queue_slot']=slot
        sequence_log.append(entry)
        return value

    beat_states=[]

    def scan_beat(cpu,machine,tick):
        write_ram(cpu,KEY_CHAIN_SYS_TICK,struct.pack('<I',tick))
        _,steps,posts=key_chain_direct_call(cpu,machine,0x6001944,[KEY_CHAIN_REGISTRY_ENTRY],
                                            watch=(0x6001b5e,0x6001b62))
        beat_states.append({
            'confirmed_level':read_ram(cpu,0x124888+0,1),   # b[ctx+0] 确认电平
            'last_level':read_ram(cpu,0x124888+1,1),         # b[ctx+1] 上次电平
            'change_count':read_ram(cpu,0x124888+2,1),       # b[ctx+2] 变化计数
            'release_count':read_ram(cpu,0x124888+3,1),      # b[ctx+3] 释放计数
            'press_count':read_ram(cpu,0x124888+4,1),        # b[ctx+4] 按下计数
            'hold_timer':read_ram(cpu,0x124888+6,2),         # h[ctx+6] 长按计时
            'click_count':read_ram(cpu,KEY_CHAIN_CLICK_COUNT,1),   # b[0x1265DA] 点击计数
            'tracked_key':read_ram(cpu,KEY_CHAIN_GESTURE_STRUCT,1),# b[0x124C80] 跟踪键
            'post_payloads':[p['args'][0] for p in posts if p['pc']==hex(0x6001b5e)]})
        return steps,posts

    def reset_scan_state(cpu):
        # 序列间重置:ctx 用 flash 真实初值;跟踪键/点击计数显式设「无跟踪」
        # 初值(255/0,手势状态机的空档语义;世界停点现值属 sys_timer 域,
        # 不假设——观测后显式重置,标注 synthetic)。
        write_ram(cpu,0x124888,ctx_blob)
        write_ram(cpu,KEY_CHAIN_GESTURE_STRUCT,bytes([255]))
        write_ram(cpu,KEY_CHAIN_CLICK_COUNT,bytes([0]))
        beat_states.append({'sequence_reset':True})

    def post_stop(machine,hardware,cpu):
        if machine is None or hardware is None:
            raise Blocked('key_chain_requires_dualcore_world')
        if cpu.core_id!=0:
            raise Blocked('key_chain_stopped_on_cpu1')
        stopped_pc=cpu.pc
        # ① 分档器 fixture(derived-not-measured,布局=0x60010b4 解析器组装面/
        # 0x6000E94 消费面反汇编钉死)
        write_ram(cpu,KEY_CHAIN_SENSOR_OBJECT,key_chain_sensor_object_bytes())
        write_ram(cpu,KEY_CHAIN_OBJECT_POINTER,struct.pack('<I',KEY_CHAIN_SENSOR_OBJECT))
        # ② 分档层独立断言:键通道未注册 → 队列未命中返回 1 → 1<=500 → 255
        key_before,_,_=key_chain_direct_call(cpu,machine,0x6000E94,[])
        # ③ 真实注册(adc_add_sample_ch;队列空槽 key==-1 处落位)
        _,reg_steps,_=key_chain_direct_call(cpu,machine,0x6001230,[KEY_CHAIN_CHANNEL_WORD])
        key_idle,_,_=key_chain_direct_call(cpu,machine,0x6000E94,[])
        # ④⑤⑥ 三序列
        tick0=read_ram(cpu,KEY_CHAIN_SYS_TICK,4)

        # —— 序列 A:双击(短按 9 拍 ×2,间隔 11 拍,释放稳定 21 拍) ——
        reset_scan_state(cpu)
        posts=[]
        tick=tick0
        sample(cpu,machine,hardware,KEY_CHAIN_RES_PRESSED,'a_press_1')
        for _ in range(13):
            tick+=KEY_CHAIN_TICKS_PER_SCAN;scan_beat(cpu,machine,tick)
        sample(cpu,machine,hardware,KEY_CHAIN_RES_RELEASE,'a_release_1')
        for _ in range(11):
            tick+=KEY_CHAIN_TICKS_PER_SCAN;scan_beat(cpu,machine,tick)
        clicks_after_first=read_ram(cpu,KEY_CHAIN_CLICK_COUNT,1)
        sample(cpu,machine,hardware,KEY_CHAIN_RES_PRESSED,'a_press_2')
        for _ in range(13):
            tick+=KEY_CHAIN_TICKS_PER_SCAN;scan_beat(cpu,machine,tick)
        sample(cpu,machine,hardware,KEY_CHAIN_RES_RELEASE,'a_release_2')
        for _ in range(25):
            tick+=KEY_CHAIN_TICKS_PER_SCAN
            _,beat_posts=scan_beat(cpu,machine,tick)
            posts.extend(beat_posts)
        clicks_after_second=read_ram(cpu,KEY_CHAIN_CLICK_COUNT,1)
        tracked_key=read_ram(cpu,KEY_CHAIN_GESTURE_STRUCT,1)

        # —— 序列 B:长按按下确认(按住 78 拍 → 第 75 拍 post 手势 1) ——
        reset_scan_state(cpu)
        posts_b=[]
        sample(cpu,machine,hardware,KEY_CHAIN_RES_PRESSED,'b_press')
        for _ in range(80):
            tick+=KEY_CHAIN_TICKS_PER_SCAN
            _,beat_posts=scan_beat(cpu,machine,tick)
            posts_b.extend(beat_posts)

        # —— 序列 C:单击(短按 9 拍 → post 手势 0) ——
        reset_scan_state(cpu)
        posts_c=[]
        sample(cpu,machine,hardware,KEY_CHAIN_RES_PRESSED,'c_press')
        for _ in range(13):
            tick+=KEY_CHAIN_TICKS_PER_SCAN;scan_beat(cpu,machine,tick)
        sample(cpu,machine,hardware,KEY_CHAIN_RES_RELEASE,'c_release')
        for _ in range(25):
            tick+=KEY_CHAIN_TICKS_PER_SCAN
            _,beat_posts=scan_beat(cpu,machine,tick)
            posts_c.extend(beat_posts)

        def call_args(post_rows):
            # post 调用点 0x6001b5e 的 r0=载荷字 0x10000|(key<<8)|gesture
            return [{'at':p['pc'],'payload':p['args'][0],'second':p['args'][1]} for p in post_rows if p['pc']==hex(0x6001b5e)]

        observations={
            'key_chain_direct_scan':{
                'stopped_world_pc':hex(stopped_pc),
                'sensor_object_fixture':{'address':hex(KEY_CHAIN_SENSOR_OBJECT),
                                         'pointer_slot':hex(KEY_CHAIN_OBJECT_POINTER),
                                         'channel_word':hex(KEY_CHAIN_CHANNEL_WORD),
                                         'thresholds':[KEY_CHAIN_THRESHOLD_RELEASE,1022]+[1023]*18,
                                         'key_ids':[255,KEY_CHAIN_KEY_ID]+[0]*18,
                                         'classification':'synthetic-not-measured (real config 523 lives in '
                                                         'the bank-B record area, outside public artifacts; erased-state '
                                                         'world leaves the key chain fully bypassed — route Q)'},
                'read_key_before_registration':key_before,
                'adc_add_sample_ch_steps':reg_steps,
                'read_key_after_registration_idle':key_idle,
                'double_click':{'click_count_after_first_release':clicks_after_first,
                                'click_count_after_second_release':clicks_after_second,
                                'tracked_key_after_sequence':tracked_key,
                                'post_calls':call_args(posts)},
                'long_press':{'post_calls':call_args(posts_b)},
                'single_click':{'post_calls':call_args(posts_c)},
                'res_fixture_switches':sequence_log,
                'beat_states':beat_states,
                'scan_beats_total':13+11+13+25+80+13+25,
            }}
        return observations
    return post_stop

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
             mem_pwr_con=False,p33_window=False,p33_port_sel=False,p33_awkup=False,
             p3_ls=False,p11_syscon1_masks=False,
             fd31_adc_con=False,fd31_adc_con_readback=False,
             fd31_adc_pnd_completion=False,fd31_adc_res_named_values=None,
             p33_analog_tail=False,p3_ana_read_ldoin_low=False,gpcnt_con_readback=False,
             jl_clock_dcck=False,
             jl_uart_con=False,
             jl_uart_init_writes=False,
             jl_omap_writes=False,
             subword_stores=False,
             word_store_sp_l=False,
             timer0_irq4_delivery=False,timer0_irq4_probe_steps=None,timer0_irq4_masked=False,
             core_tick_irq3_delivery=False,core_tick_irq3_probe_steps=None,core_tick_irq3_masked=False,
             key_chain_scan=None,
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
    # word_store_sp_l:opt-in 的长位移 sp 相对整字存储 st.w.sp.l(exact_parallel_
    # stores 独立子开关 exact_word_store_sp_l)。证据形状 app 0x6065ccc
    # `st.w.sp.l [sp+8]=r9`(scheduler 中断上下文 helper 0x6065c3a 尾部):全 4
    # 字节覆盖写、旧值无关,上游存储模型写前合并读旧 word(模型内部簿记读、
    # 非 guest 装载),未初始化局部 0x19e2d4 上假停。该形状在 legacy 前缀
    # (289,849 之前)已真实执行 3 次(走上游模型、目标内存已初始化、正常完成),
    # 并入主开关会改 legacy 报告的 adapter 计数,故单独 opt-in。默认关闭,
    # legacy 报告不变。
    if word_store_sp_l:extra['exact_word_store_sp_l']=True
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
        # fd31 ADC CON 读回投影(路 BN)把前沿拉进轮询 helper 0x601163a 的
        # 1000 轮忙等(投影世界不发明 ADC 完成,bit7 恒不置位;每轮 call RAM
        # 延时 0x12e256 ≈386 步 → ~400k 步),途中跨过 Timer0 STD12M 首次
        # 到期(~341,097)及其后 ~11 次到期(每 40,000 步一次,场景必须叠加
        # 已合并的 timer0_irq4_delivery 机制,否则停在交付边界)——预算放宽
        # 到 800k 覆盖 RES 读停点(~710k)。
        if fd31_adc_con_readback:budget=800000
        # p3_ana_read_ldoin_low(路 CB):fixture 0 下 W1[0] 循环2 全预算跑完
        # (20 轮 × delay(1000µs)=每轮 ~120,000 模型步(120MHz 倒数循环
        # 0x12e282..86,3 指令/迭代 × 40,000 迭代)≈2.4M 步,途中 Timer0
        # STD12M 到期 ~60 次、core tick 溢出若干次——交付层就位)——预算
        # 放宽到 4M 覆盖超时路径+失败码+尾段下一停点。
        if p3_ana_read_ldoin_low:budget=4000000
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
    # p33_window:opt-in 的 P33 窗「无名寄存器族」write-only 字节记录(路 AV)。
    # 触发点=302,581 停点:post-power_init 的 P33 窗配置函数 0x6067b66(walker
    # 0x6067cb4 的 12 项表 0x0609E964→RAM 指针,首项 index=1/cfg=0x11/port=1/
    # bit=1/w4=1≠0→pair 路径)内 0x6067be4 `uextra.rrii.p∥st.b b[r6+8]=r0`
    # (r6=0xf2c940)对 [0xf2c948]=P33_ACCESS(0x252)的字节写——上游 pair 存储模型
    # 写前合并读旧 word(模型内部簿记读非 guest 装载)撞 unmapped。证据核验:
    # SDK p33_sfr.h 命名止于 0xd5(P3_LS_PLL),族内 0x150-0x153/0x250-0x253/
    # 0x258-0x259/0x267/0x268 全无名无类型标注无位定义(路 AZ 修正:清尾 0x6067d32
    # 实际写 0xF2C9A0=P33_ACCESS(0x268),AV 误记 0x280);app 字面量恰 3 处+
    # 寄存器算术、ROM 零引用;三个引用函数(0x6067b66/0x6067464/walker 清尾
    # 0x6067d26..38)的访问形状全是单字节写、零读回、零 RMW、零整字写——
    # 故按 lp_timer SYS_DIV/CLK_CON0 整字写记录同族做 write-only 字节序列
    # 记录(非 p33_analog_config 的合成读回——那族固件真实读回,本族从不)。
    # 字节写路由:probe_boot Memory.write 的一字节钩子(handles_byte_write,
    # 绕过「handles 只接受字访问」的字粒度限制);读回一律严格停。
    # subword_stores:probe_boot 既有 exact_subword_stores 诊断(路 AU 在
    # probe_mmu 支线同款接线)——普通 st.b/st.b.l(非 pair)直写 1 字节不走
    # 上游合并读,放行函数尾 0x6067c36 等对本族的普通字节写。uextra.rrii
    # A 半的 pair 扩展挂在既有 byte_pair 开关内(全 app 12 处站点在既有前缀
    # 零执行,不改任何既有报告;见 probe_boot 主 gate 注释)。
    if p33_window:
        require(p11_power_init,'the P33-window projection only layers on the p11_power_init scenario')
        options['p33_window_projection']=True
    # p33_port_sel:opt-in 的 P3_PORT_SEL0..11 整字写 write-only 记录(路 AZ)。
    # 触发点=302,620 停点:P33 窗配置函数 0x6067b66 尾部 0x6067c42
    # `st.w.idxs [0xf2c300+index<<2]=r0` 写 [0xf2c304]=P3_PORT_SEL1(SDK 有名
    # P3_PORT_SEL0..15=P33_ACCESS(0xc0..0xcf),p33_sfr.h 只有地址宏、无
    # __RW/__RO/__WO 类型标注;值 r0 是查表函数 0x60672C8(cfg) 的真实执行
    # 结果,cfg=0x11 实测 0x12)。证据核验:app 字面量 0 处、ROM 0 处,唯一
    # 写者是本函数(walker 两轮表循环 index 0..11 → 0xf2c300..0xf2c32c,boot
    # 表实测写 SEL1..4);唯一读是 0x0606745e 的 arch_startup opaque 死读
    # (r8=0xf2c120+480,调用者 0x0603502c/0x06035306 都立即杀死 R0,值从不
    # 消费)——无被消费读回,按 lp_timer SYS_DIV/CLK_CON0 整字写 write-only
    # 记录同族建模(全字覆盖写、旧值无关),读回一律严格停
    # port_sel_readback_not_modeled,RMW 无观测形状拒绝。注意(路 AZ 证据
    # 核验的新发现):walker 第二轮表(3 项,index 0..2)的 cfg 全是
    # 0xE2/0xE4/0xE5——全部走 P3_AWKUP 次路径(查表函数对 0xE2/0xE4/0xE5
    # 分别返回 2/1/0,作为 FLT 寄存器下标与通道位),其中 cfg=0xE2(w4=0)
    # 路径在 0x6067cac 有对 [0xF2C184]=P3_AWKUP_P_IE 的显式 ld.w 读回且被
    # 消费(读的是从未写过的复位值)——P3_AWKUP 族有真实读回需求,p33_sfr.h
    # 无类型证据,读回语义归下一路;本投影只放行 PORT_SEL 整字写,前沿停在
    # 次路径首个访问 0x6067c6e `st.w.idxs [0xF2C200+2<<2]=[0xF2C208]=
    # P3_AWKUP_FLT2`(SDK 有名 P3_AWKUP_FLT0..2=P33_ACCESS(0x80..0x82))。
    # P3_LS_*(0xf2c340..354 六寄存器清零)与 0xf2a628/638 在 P3_AWKUP 停点
    # 之后、本轮不可达,证据已钉死归后续路(见 HANDOFF 路 AZ 条目)。
    if p33_port_sel:
        require(p11_power_init,'the P3_PORT_SEL projection only layers on the p11_power_init scenario')
        options['p33_port_sel_projection']=True
    # p33_awkup:opt-in 的 P3_AWKUP 族「仅记录写访问」+ P3_AWKUP_P_IE 首次读
    # 具名 fixture(路 BB)。触发点=303,853 停点:walker 第二轮表 item[0]
    # (cfg=0xE2,ch=2,w4=0)0x6067c6e `st.w.idxs [0xF2C200+2<<2]=4` 写
    # P3_AWKUP_FLT2(SDK 有名 P3_AWKUP_FLT0..2=P33_ACCESS(0x80..0x82))。
    # 证据核验(路 BB 复核路 AZ):①SDK p33_sfr.h 把 P3_AWKUP_EN/P_IE/N_IE/
    # LEVEL/INSEL/P_PND/N_PND/P_CPND/N_CPND(0x60..0x68)与 FLT0..2(0x80..0x82)
    # 全命名,但只有地址宏、无 __RW/__RO/__WO、无位定义。②全量引用扫描
    # (app 字面量+基址算术、ROM 整块):FLT0..2 唯一写者是本次路径(整字写,
    # 值=w8 真实执行结果);EN/P_CPND/N_CPND 只观测到 rmw.or.bitr|=1<<ch
    # (次路径)与 modimm/nmodimm(清除助手 0x60685ae,不在本前缀);P_IE/N_IE
    # 除 RMW 外有显式消费型读回(本前缀只有 P_IE 一次:0x6067cac,从未写过,
    # 首次读;N_IE 的读在中断分发函数 0x60691f4,不在本前缀)。③LEVEL/INSEL
    # 零引用不建模;P_PND/N_PND 已归复位历史投影,不双重建模。④codex 规则
    # 落实:不发明默认读值——写侧仅记录(整字记录+位 RMW 记录),读侧只有
    # 显式具名 fixture(P33_AWKUP_P_IE_FIRST_READ_ZERO,整字 0、标注合成
    # 假设、只覆盖 P_IE 一个寄存器)覆盖的寄存器的首次读返回具名字;未覆盖
    # 读/写后读/重复读一律严格停;写回值对 fixture 的依赖(M&~K 位分析)
    # 在投影报告 writeback_dependency 全文记录。默认关:0xf2c208 写保持
    # unmapped_or_readonly_write(303,853 前沿形状)。
    if p33_awkup:
        require(p11_power_init,'the P3_AWKUP projection only layers on the p11_power_init scenario')
        options['p33_awkup_projection']=True
        options['p33_awkup_first_read_words']=dict(P33_AWKUP_P_IE_FIRST_READ_ZERO)
    # p3_ls:opt-in 的 P3_LS_P11+P3_LS_IO_DLY..PLL 整字写 write-only 记录(路 BD)。
    # 触发点=304,012 停点:walker 清尾 0x6067d40 li r0=0xF2C290 后并行对
    # 0x6067d48 `add.s14.rri.p r0+=176 ∥ st.w [r0]=0` 的 store 半(run3 旧值
    # 方向)写 [0xF2C290]=P3_LS_P11=0(SDK 有名 P33_ACCESS(0xa4),p33_sfr.h 只
    # 有地址宏、无 __RW/__RO/__WO、无字段位定义)。证据核验(路 BD 反汇编+
    # 全量引用扫描):①P3_LS 族访问面=P3_LS_P11(0xf2c290)+P3_LS_IO_DLY..
    # PLL(0xf2c340..354 六字,P33_ACCESS(0xd0..0xd5)),三组写者(walker 清尾
    # 0x6067d40/变体清零 0x6069cb0/低功耗 0x60c66f8)全部纯写(值 0/0/1、3)、
    # app+ROM 零读回观测——按 p33_port_sel/lp_timer SYS_DIV 同族整字写
    # write-only 记录放行(全字覆盖写、旧值无关),读回一律严格停
    # p3_ls_readback_not_modeled,RMW 无观测形状拒绝。②**0xf2a638 修正**:
    # 路 AZ/BB 条目记「[0xf2a638]=0 唯一写点 0x6067d62」是顺序语义(用加法后
    # 新 r0)静态读法;按 run3 旧值方向(BB 的 304,012 停点即其产物)该 store
    # 半写 [0xF2C350](P3_LS_PLL 第 3 次清零)。全量复核:0xf2a638 的 app
    # 字面量 0、ROM 0、全部可达基址+offset 访问形状逐点反汇编 0——全 app/
    # ROM 零访问,无需建模。③并行对 0x6067d5e∥d62(add.s14.rri.p n=8936 的
    # simm14 负值 -7448 → r0=0xF2A628)由既有 exact_parallel_stores gate 执行
    # (store 半旧值方向),A 半结果 r0=0xF2A628 交给下一条复合 RMW。
    if p3_ls:
        require(p11_power_init,'the P3_LS projection only layers on the p11_power_init scenario')
        options['p3_ls_projection']=True
    # p11_syscon1_masks:opt-in 的 P11_SYS_CON1(0xf2a628)多掩码访问放行(路 BD)。
    # 触发点=walker 清尾 0x6067d64 `rmw.and.nmodimm [0xF2A628] &= 0xFFFFFF7F`
    # (清 bit7;0x6067d5e 并行对 A 半从 0xF2C340-7448 算出 0xF2A628),与主
    # 复位初始化 0x6068c88 的 0xFFFFFFEF(清 bit4,RESET_CONFIG_MASKS 既有)
    # 是两个不同掩码——复位投影严格单掩码校验拒绝。证据核验结论:固件真实
    # 用这两个掩码清该寄存器不同位(app 变体函数 0x6069cc6 第三处同掩码
    # 0xFFFFFF7F,不在启动前缀);SDK p11_sfr.h P11_SYS_CON1 标 __RW(宏值==
    # volatile,R/W 区分只在注释)、无位字段定义,p11_app.h 用法宏
    # MSYS_IO_LATCH_DISABLE() 即 `P11_SYS_CON1 &= ~BIT(7)`(bit7 含义按宏名
    # 仅作参考)。选方案 (a) 掩码集扩展(路 AO 先例):extra_configuration_
    # mask_sets 把 0xFFFFFF7F 追加进 0xf2a628 的接受掩码集,每个掩码仍按位
    # 清(清掉的位已知 0、保留位未知、读回须全知),默认单掩码校验行为不变。
    # 默认关:0xf2a628 的 0xFFFFFF7F RMW 保持 reset_configuration_operation_
    # not_modeled 严格停。
    if p11_syscon1_masks:
        require(p11_power_init,'the P11_SYS_CON1 mask-set extension only layers on the p11_power_init scenario')
        options['p11_syscon1_mask_sets']=True
    # fd31_adc_con:opt-in 的 0xfd3100=JL_ADC->CON 整字写 write-only 记录(路 BG)。
    # 触发点=304,036 停点:app_core 尾段 0x60120f4/0x6012106 两次背靠背调
    # 「电源小函数」0x60115b4(实为 ADC 控制函数),每次三连整字写
    # **[0xfd3100]=0x20000 → 0x20040 → 0x40**(首值修正:路 BD 记 0x200,modimm
    # raw 0xB00 旋转展开是 0x20000,停点 value_bytes '00000200' 小端即 0x00020000)。
    # 证据核验(路 BG 反汇编+全量引用扫描):①命名修正——0xfd3100 在参考
    # SDK br28.h(路 AC JL_LRCT 同一命名锚点头)是 **JL_ADC_BASE=ls_base+
    # map_adr(0x31,0)**,JL_ADC_TypeDef{__RW CON;__RO RES;},+4=0xfd3104=RES
    # (只读转换结果);路 BD「JL PMU/SFR 段电源口、SDK 头无名」按此修正。
    # ②0x60115b4 全函数纯三写、零读回/RMW/轮询;全 app 恰 5 个 call 点
    # (0x60120f4/0x6012106/0x60116ee/0x6011706/0x6034900)。③全量引用面=
    # app 字面量恰 7 处、ROM 整块 0、0xfd3104 字面量 0:除电源函数外还有
    # **真实读回消费**——轮询 helper 0x601163a(≤1000 轮读 CON 等 bit7 置位/
    # bit4 清零)、caller A 0x60116d0(读 CON bit4 门控+读 RES 存软件槽)、
    # caller C/D 0x60348e8/0x60349xx(bit7/bit4 门控+byte0 消费)、setter
    # 0x6011666 尾(整字写+|=0x40)。④**读回就在启动链上**:放行 6 写后
    # app_core 尾段经 0x60115cc+3 组 getter(纯软件)进入 32 次采样循环
    # 0x6012148..0x601215e(每轮 call caller A,min/max/sum、末尾 /30 平均),
    # caller A 首个 fd31 访问=0x60116e6 `ld.w` 读 CON。建模:CON 写侧
    # p33_port_sel/P3_LS 同族 write-only 整字记录(值全真实指令计算);
    # CON 读回(bit4/bit7 状态位)与 RES(模拟测量)无可推导值,不发明
    # fixture,读回一律严格停 fd31_adc_con_readback_not_modeled;RMW 拒绝
    # (fd31_adc_con_rmw_not_modeled,该寄存器有真实读回消费者);RES 零写
    # 不覆盖(读保持泛 unmapped)。默认关:0xfd3100 写保持
    # unmapped_or_readonly_write(304,036 前沿形状)。
    if fd31_adc_con:
        require(p11_power_init,'the fd31 ADC CON projection only layers on the p11_power_init scenario')
        options['fd31_adc_con_projection']=True
    # fd31_adc_con_readback:opt-in 的 0xfd3100=JL_ADC->CON 读回=最后写值投影
    # (路 BN/sim-adc-read,lp_timer_con_readback 路 AL 同族)。SDK 证据:参考
    # br28.h 的 JL_ADC_TypeDef{__RW CON; __RO RES;}(该头 __RW/__WO/__RO 标注
    # 有区分度:JL_LRCT CON __WO vs NUM __RW、ADC CON __RW vs RES __RO),CON
    # 可回读是 SDK 参考语义;读回=最后写值与 SFC 时钟 +8(0xfd1c08)/lp_timer
    # CON(0xf2a908)同族,只在固件写过至少一次后返回、从不臆造复位值。消费
    # 分支(反汇编钉死,全文见 br28_model.Fd31AdcConRegisters 类注释):304,317
    # 停点 0x60116e6 的读由 caller A 0x60116d0 的 bit4 门控 0x60116e8 消费——
    # 末写值 0x40 的 bit4=0 → 跳过轮询 helper+控制函数,直接 RAM veneer
    # 0x12b55c+setter 0x6011666;setter(通道字 0x10000,r2'=r0>>16=1)走 tbb
    # case1 → call 0x6067d8a 对 0xf2c250=P3_ANA_CON4(p33_sfr.h:134 有名)做
    # 6 次读-改-写(路 AM P3_ANA_CON2/CON5 同族,联动 seed 合成初始字 0)→
    # 尾部 [0xfd3100]=r4 整字写 + |=0x40 复合 RMW(走通用 mmio 合成路径,
    # lp_timer CON2 同款)→ 轮询 helper(bit7 置位退出在投影世界取决于写值位,
    # 不发明 ADC 完成事件)→ 读 [0xfd3104] RES = **新停点 fd31_adc_res_
    # not_modeled**(RES 是模拟测量值,红线:绝不发明 0/满值 fixture;真机
    # FD07 测量或带推导链的具名 fixture 是唯一继续途径)。默认关:CON 读回
    # 保持 fd31_adc_con_readback_not_modeled(304,317 前沿形状不变)、
    # 0xf2c250 保持 unmapped。
    if fd31_adc_con_readback:
        require(fd31_adc_con,'the fd31 ADC CON readback only layers on the fd31 ADC CON projection')
        options['fd31_adc_con_readback']=True
    # fd31_adc_pnd_completion(路 BY/sim-adcresfix):CON(bit7=PND,SDK gpadc_hw_v10.h
    # GPADC_CON_PND=7)的无状态读时完成投影——最后写值 EN(bit4)=1 时 guest ld
    # 读回=最后写值|0x80;原子复合 RMW 的内部读从裸最后写值合成(边界抽象只钉
    # 死到 rmw.* 指令形状,probe_boot 专用分发)。证据链:SDK 驱动 gpadc_v10.c
    # 的 adc_wait_pnd(轮询 bit7,≤1000×udelay(1) 超时)+adc_get_res(RES 有效性
    # 钉在 bit7);QCY helper 0x601163a 同形(~386 步/轮×1000);**显式假设=启动写
    # 后固件首次状态读前转换已完成**(非时序模型)——全预算轮询=80 次×~96ms≈
    # 7.7s 纯轮询(1 步=250ns 按 Timer0 10ms=40,000 步),与实测 ~1s 级启动
    # 不相容。先例:lrct_trim_done(bit7)/SFC PLL-lock(bit15)。默认关:读回
    # 语义与路 BN 逐字节一致(轮询走满预算,前沿 689,402 形状不变)。
    if fd31_adc_pnd_completion:
        require(fd31_adc_con_readback,
                'the fd31 ADC PND completion only layers on the CON readback projection')
        options['fd31_adc_pnd_completion']=True
    # fd31_adc_res_named_values(路 BY):RES(0xfd3104,__RO 模拟测量值)的具名
    # 档位 fixture(derived-not-measured,推导链全文见 FD31_ADC_RES_NAMED_VALUES
    # 常量注释与 docs/adc-res-consumer.md §6)。通道识别=RES 读时刻
    # P3_ANA_CON4[4:1] 复用域(setter case1 → 0x6067d8a 反汇编钉死),非 RAM 槽
    # ——外设模型不接触 guest RAM,且硬件转换的就是复用器选中的通道;一致性
    # 门=最后 CON 写 EN=1 + CH_SEL=0。fixture 叠在 PND 完成投影上(RES 只在
    # 转换完成后持有有效样本,SDK adc_get_res 同语义)。默认关:RES 读保持
    # fd31_adc_res_not_modeled 严格停。
    if fd31_adc_res_named_values is not None:
        require(fd31_adc_pnd_completion,
                'the fd31 ADC RES named values only layer on the PND completion projection')
        options['fd31_adc_res_named_values']=dict(fd31_adc_res_named_values)
        # ADC 通道队列快照(项0.value 字 + 项1/项2 的 key/value/mode/period
        # 12 字节窗):fixture 的下游算术逐字节钉死——项0.value=32 轮 WBG04
        # 截尾均值(u16=400)、项1/项2.value=12 轮双采 divider 归一化后固件
        # 自己 utof 的 float 字、mode 半字实测=0x0001(BU §5 [疑]点显形:
        # getter 存 float 同路径写 mode=1,静态扫描漏看——adc_get_voltage
        # 走 float 直读分支)。只挂实验配置场景(不进共享 p11_snapshots,
        # crc_bankb 报告字节不变)。
        p11_snapshots=tuple(p11_snapshots)+(('adc_item0_value',0x127D10,4),
                                            ('adc_item1_entry',0x127D1C,12),
                                            ('adc_item2_entry',0x127D2C,12))
    # p33_analog_tail(路 BV/sim-tailproj,模型已备、本路接线):W1[0] 边界序列的
    # P3_ANA_CON0/7/8/9/10 + P3_CHG_CON0/CON1 AM 容器扩展(合成初始字 0+读回=
    # 当前值);P3_ANA_READ(0xf2c064)读回钉住 p3_ana_read_not_modeled(RES 红线
    # 同款)——RES 闭合后前沿撞上的第一个真 unknown。默认关,legacy 不变。
    if p33_analog_tail:
        options['p33_analog_tail_projection']=True
    # p3_ana_read_ldoin_low(路 CB/sim-anaread):P3_ANA_READ 读侧三选一按证据
    # 落地=方案 (c) 具名常量 live-input fixture(derived-not-measured)。证据
    # 链(全文见 docs/appcore-tail-sfr-semantics.md §9 与模型参数注释):
    # ①SDK charge_hw.h(pinned 67328f4)逐位语义 bit1=LVCMP_DET(ldoin vs vbat
    # 比较器)/bit2=LDO5V_DET(ldoin 检测)/bit0=CHARGE_FULL_FLAG;②charge.c
    # 驱动注释钉阈值(bit1=1⟺ldoin>vbat、bit2=1⟺ldoin>0.6V),QCY W1[0]=
    # 0x6012e3e 的 20 轮检测循环与 SDK check_charge_state() 逐行同形(反汇编
    # 逐指令钉死:20 轮、先测 bit1(&2)再测 bit2(&4)、任一置位→成功路径
    # (b[dev+628]=1+条件创建充电去抖任务 0x6033F36),全 0→delay(1000µs);
    # 超时→b[0x125BF4]=2 失败码);③循环1(0x6012f14..30)=do{delay;r4=
    # LDO5V_DET&--r4}while(r4),执行语义=首读 bit2 定 1/2 轮 delay;④全 app
    # 恰 5 处 li 读者(0x601201a/0x6012b58/0x6012e6e/0x6033f38/0x6035f54)只
    # 单 bit 消费 bit1/bit2,零写者。场景锚=电池启动+无外部供电(ldoin≈0V,
    # 与 BY 的 RES fixture 同一物理世界)→两比较器读 0。**形状=常量**(codex
    # 参谋采纳:轮询 41 次读,首读 fixture 只会把边界挪到第二读);derived_
    # mask=0b110,其余位显式填零非推导。默认关:读回保持 p3_ana_read_
    # not_modeled 严格停(447,383 前沿形状不变),legacy 报告逐字节不变。
    if p3_ana_read_ldoin_low:
        require(p33_analog_tail,
                'the P3_ANA_READ ldoin-low fixture only layers on the p33 analog tail projection')
        options['p3_ana_read_ldoin_low_fixture']=True
    # gpcnt_con_readback(路 CG,读侧三选一=方案 (a) 投影):GPCNT_CON(0xfd1100,
    # SDK br28.h JL_GPCNTTypeDef CON __RW/NUM __RO,无专用驱动头/位定义)读回
    # =固件自己已执行写出的位粒度已知状态。证据链:①全 app+ROM 字面量扫描
    # 恰 2 处 li 0xfd1100(cleanup 体 0x6069514+任务 0x606953a),ROM 零引用;
    # ②本前缀唯一执行过的写=disable &=0xFFFFFFFE(bit0→0)+clear |=0x40(位7
    # 立即清投影)——任何其他 RMW 形状/整字写模型都 Blocked,到 601,880 读
    # 时 mask=0x81;③唯一读者=任务 0x6069538:0x6069540 beq.and.ri 测 bit0
    # (使能位),==0 → 跳 0x60695fa 函数尾直接返回(零副作用,无 NUM 读无
    # bit7 轮询)——与「app 自己早期(~108k OS/tick init 域)关断了计数器」
    # 的现实一致。方案 (b) 钉住放弃(有 BN __RW 读回同族先例+完整推导链);
    # 方案 (c) fixture 不适用(未知位无值域证据,零填=发明)。读返回部分值
    # 对象 GpcntConValue:单 bit AND 谓词可判(掩码外位 AND 强制为已知 0),
    # 未知位任何消费诚实停(GpioOutValue/ICFG partial 同族)。默认关:读回
    # 保持 unknown_gpcnt_configuration_bits 严格停(601,880 前沿形状不变)。
    if gpcnt_con_readback:
        options['gpcnt_con_readback']=True
    # jl_clock_dcck(路 CL/sim-dcck,读侧三选一=方案 (a) 投影):JL_CLOCK->DCCK_CON0/1/2
    # (0xfd0008/0c/10)按路 AM 容器同款「显式合成初始字 0 + 读回=当前寄存器值」
    # 建模。证据链:①br28.h JL_CLOCK_TypeDef 全字段 __RW(可读回);②SDK
    # power_clock.c 的 deep-sleep save/restore 把三字列入可读回/可恢复清单
    # (真实读回消费者=deep-sleep 域,不在本前缀);③全 app+ROM 0xfd0008 恰 1 处
    # li(0x6068176,mode 切换函数 0x6068152),0xfd000c/0xfd0010 经 [r0+4]/[r0+8]
    # 派生;④唯一消费者 0x6068152 的显式 ld 读值全部立即被掩码+OR 消化(seed 0
    # 下确定性的位域配置序列),无独立状态消费;⑤原子 RMW/显式 ld/st RMW/整字写
    # 全走既有通用 mmio 路径(机器侧零新分发)。纯数字配置、无模拟量红线。默认
    # 关:三字保持 unmapped(3,070,626 前沿形状不变),legacy 报告逐字节不变。
    if jl_clock_dcck:
        options['jl_clock_dcck_projection']=True
    # jl_uart_con(路 F/sim-fd2000,读侧三选一=方案 (a) 投影):JL_UART0/1/2 的
    # CON0|CON1 整字(0xfd2000/0xfd2100/0xfd2200)按路 AM/DCCK 容器同款「显式
    # 合成初始字 0 + 读回=当前寄存器值」建模。证据链(全文见 br28_model 模型
    # 参数注释):①br28.h `JL_UART0_BASE=ls_base+map_adr(0x20,0)`=0xfd2000(命名,
    # 非「SDK 参考头未命名」——路 CQ 记录有误,本路修正),JL_UART_TypeDef
    # CON0/CON1 标 __RW;②读辅助函数 0x601d198 = SDK uart_v1.c `uart_is_idle`
    # (cpu.a/uart_v1.c.o LLVM bitcode 逐字节同形:switch{0:*(0xfd2000),1:*(0xfd2100),
    # 2:*(0xfd2200)};return (word&1)^1)——读值立即被 bit0 掩码测试消化(空闲
    # 判定,非独立状态消费);③bit0 是硬件 busy/空闲状态位,固件从不写 bit0
    # (SDK init 写 CON0=0x3400/putbyte |=0x2000/各 RMW 无一处置位),合成 0 表
    # 示「空闲」=derived-not-measured 显式假设(本前缀未发起 UART 发送),非硅
    # 测量/非复位值声明;④写侧(uart_init 的 CON0=0x3400/|=0x3 等 __RW 配置写)
    # 走通用 registers 路径,机器侧零新分发。默认关:三字保持 unmapped
    # (3,071,380 @ 0x601d1ac 前沿形状不变),legacy 报告逐字节不变。
    if jl_uart_con:
        options['jl_uart_con_projection']=True
    # jl_uart_init_writes(路 M/sim-uartwr,UART 写侧三选一=方案 (a1) 一次性打包
    # 初始化写白名单):JL_UART 初始化配置字(CON1+4/BAUD+8/OTCNT+0x10/RXSADR+0x1c/
    # RXEADR+0x20/RXCNT+0x24/CON2+0x2c,三基址对称)按 lp_timer SYS_DIV/P3_PORT_SEL
    # 的 write-only 整字记录先例放行(st.w 族全字覆盖写、旧值无关,记录地址+值+
    # 宽度;值全为真实指令结果)。**布局钉死(修正路 F 的「0xfd2004=BAUD|BUF」
    # 命名)**:实际 SDK 构建对象 uart_v1.c.o bitcode 声明 JL_UART_TypeDef={i32 x12}
    # (成员 k 在 +4k),QCY 固件写序列逐点互证(uart_init 内联 0x6034466/0x603446c/
    # 0x6034472 写字 0/1/11、uart_set_baudrate 0x601d286 写字 2=BAUD 分频值、
    # uart_refresh_otcnt 0x601d1f2 写字 4=OTCNT、RX 三连写 0x6034668..cc 写字
    # 7/8/9);公开 br28.h 的 u16/u8 紧凑布局与该构建对象不一致(BR31 等系列
    # 公开头确有紧凑布局,是布局分歧非笔误;以编译对象+固件字节为准)——
    # 0xfd2004 是 CON1 字,真 BAUD 写在 0xfd2008。停止边界(诚实停):白名单字
    # 读回/RMW 严格停;TXADR/TXCNT/BUF/HRXCNT 不进白名单(TX 路径字段,TXCNT 写
    # 或写后轮询 CON0 完成位=「写已启动硬件活动」信号,codex 风险点);运行效果
    # (波特率/RX/TX DMA/UART IRQ)不建模——只记录「初始化配置写已执行」。
    # 默认关:这些字保持 unmapped(3,071,984 @ 0x603446a 前沿形状不变),legacy
    # 报告逐字节不变。
    if jl_uart_init_writes:
        options['jl_uart_init_write_records']=True
    # jl_omap_writes(路 R/sim-gpioface,GPIO 引脚复用写侧):0xfd58xx =
    # **JL_OMAP**(SDK io_omap.h `JL_OMAP_BASE=ls_base+map_adr(0x58,0)`=0xfd5800,
    # **SDK 有名**——修正路 M 的「公开 br28.h 无名」记录:br28.h 确实不名它,
    # 但 io_omap.h(被 br28.h include)整组命名)——GPIO 交叉开关的逐引脚输出
    # 功能选择寄存器组。**识别三方交叉钉死**:①SDK 驱动表 gpio_hw.c.o 的
    # gpio_out_regs={0xfd5800,0xfd5840,0xfd5870,0,0,0xfd5894,0xfd58c0}(A/B/C/
    # D/E/G/P,D/E 无交叉输出)与 QCY 固件表 0x609EA9C 逐字节一致,gpio_in_regs
    # {1,17,29,0,0,38,49}与 0x609EAB8 一致;②gpio 224/225 = gpio_hw.h
    # IO_PORT_DP/DM(PORTUSB=14),固件解析函数 0x600127a(`if(r4==224)r0=
    # 16603320`)与 SDK gpio2crossbar_outreg 的 224→0xfd58b8/225→0xfd58bc 特例
    # 双侧同值;③停点值 0x9f = io_omap.h `FO_UART0_TX=((39<<2)|BIT(1)|BIT(0))`
    # = 159,与 gpio_func.c.o sw.bb `gpio_set_fun_output_port(add,159,1,1)`
    # (fn=1=PORT_FUNC_UART0_TX)互证——本写就是 uart_init 的 TX 引脚复用
    # (TX=USB DP 脚)。**布局**:4 字步进(SDK getelementptr i32+固件
    # (gpio&0xF)*4,B 基址=A+16*4 直接证明);io_omap.h 的 u8 紧凑成员偏移与
    # 驱动表不一致(同路 M 布局分歧),以编译对象+固件为准。白名单=49 字
    # (A16/B12/C9/G9/USBDP/USBDM/PP0,成员数=gpio_hw.h 端口掩码),P3_PORT_SEL
    # 的 write-only 整字记录先例:st.w 全字覆盖写、值=真实指令结果(FO_* 常量
    # 由 case 体真实装载);读回/RMW 严格停;交叉开关布线/输出电平/IRQ 不建模
    # ——只记录「引脚复用配置写已执行」,不声称 UART 通信已验证。默认关:
    # 0xfd58xx 保持 unmapped(3,072,236 @ 0x60012de 前沿形状不变),legacy
    # 报告不变。
    if jl_omap_writes:
        require(jl_uart_init_writes,
                'the JL_OMAP write records only layer on the UART init whitelist prefix')
        options['jl_omap_write_records']=True
    # timer0_irq4_*(路 BE,独立成段便于并行路线合并):Timer0 pending→IRQ4 交付
    # 机制就位(在 ~341,097 硬期限前)。机制全部抄 core_tick/软件 IRQ 两先例:
    # 模型 advance() 在资格合格时抛 IrqDeliveryRequest(core,irq=4)(每个
    # pending 片至多一次,in-flight 由真实 handler 的 W1C 清),机器侧最小
    # 进入投影读真实向量槽 0x19fe00+4*4,入口银行假设+对称一次性 RTI(尾锚
    # 0x12ed0c=gpio-irq 域六 handler 反汇编钉死的 Timer0 handler 0x12ece8 尾)。
    # 事件时钟不启用(BI 环节)。探路 fixture timer0_irq4_probe_steps 把首次
    # 到期拉近到 STD12M 启动写之后 N 步(计数 seed,显式合成假设)。运行态
    # trace 已证实前缀里 IRQ4 被驱动安装循环在 ~110,768 步真实使能(priority
    # 1,maskable,此后无清零)——所以 delivery_probe 不注入任何资格 fixture,
    # 就是真实资格的完整交付往返;timer0_irq4_masked 是反事实对照(清 IRQ4
    # nibble 的「未安装」世界),演示惰性分支:到期只置 pending、不请求不停。
    if timer0_irq4_delivery:
        require(p11_power_init and timer0_std12m,
                'the Timer0 IRQ4 delivery only layers on the STD12M counting prefix')
        machine_options['timer0_irq_entry']=True
        machine_options['timer0_irq_entry_banks']=True
        machine_options['timer0_irq_rti']=True
        options['timer0_irq_delivery_requests']=True
        extra['timer0_irq_frames']=dict(TIMER0_IRQ_FRAMES)
    if timer0_irq4_probe_steps is not None:
        require(timer0_irq4_delivery,'the Timer0 IRQ4 probe steps require the delivery layer')
        options['timer0_irq4_expiry_probe_steps']=timer0_irq4_probe_steps
    if timer0_irq4_masked:
        require(timer0_irq4_probe_steps is not None,
                'the Timer0 IRQ4 masked probe requires the expiry probe (it arms at the same start write)')
        options['timer0_irq4_masked_probe']=True
    # core_tick_irq3_*(路 BF,独立成段便于并行路线合并):CPU0 core tick(IRQ3)
    # 首次溢出交付机制接进主条件场景栈(在 ~447,490 步硬期限前就位)。机制
    # 全部复用 probe_core_tick/br28_machine 既有件(probe_core_tick 的场景栈
    # 停在 153,430 的 CPU1 unmaskable-IPI 边界,本路把它接到 304,317 前沿的
    # p11_heap_experimental_config 全栈):模型 advance() 在溢出且资格合格时抛
    # IrqDeliveryRequest(core,irq=3)(unresolved_overflow 防重入——counter hold
    # 到 handler 的 |=0x40 ACK,与 Timer0 的 delivery_in_flight 周期重检不同,
    # 溢出时刻不合格则保持 pending 等待、不发明重试),机器侧最小进入投影读
    # 真实向量槽 0x19fe00+3*4=0x19fe0c(运行态由 tick_timer_init 0x60661e8 的
    # request_irq 写入 0x60661a4),入口银行假设+对称一次性 RTI(尾锚
    # 0x60661e6=handler rti,反汇编钉死)。资格链无新 fixture:IRQ3 nibble
    # (bits12-15,priority 1)由同一 request_irq 在启动写之前真实置位。
    # 探路 fixture core_tick_irq3_probe_steps 把首次溢出拉近到启动写之后
    # N 步(计数 seed,显式合成假设);masked 是反事实对照(清 IRQ3 nibble)。
    # 事件时钟不启用(BI 环节)。
    if core_tick_irq3_delivery:
        machine_options['core_tick_entry']=True
        machine_options['core_tick_entry_banks']=True
        machine_options['core_tick_rti']=True
        options['core_tick_delivery_requests']=True
        extra['core_tick_frames']=dict(CORE_TICK_IRQ_FRAMES)
    if core_tick_irq3_probe_steps is not None:
        require(core_tick_irq3_delivery,'the core tick IRQ3 probe steps require the delivery layer')
        options['core_tick_irq3_expiry_probe_steps']=core_tick_irq3_probe_steps
    if core_tick_irq3_masked:
        require(core_tick_irq3_probe_steps is not None,
                'the core tick IRQ3 masked probe requires the expiry probe (it arms at the same start write)')
        options['core_tick_irq3_masked_probe']=True
    if subword_stores:extra['exact_subword_stores']=True
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
    # key_chain_scan(路 S/sim-keychain,opt-in 默认关):停点后直调键链
    # (分档查询→注册→采样事务→扫描回调→手势→post),全链固件真实执行,
    # 唯一合成输入=RES 档位值。依赖 BY 的 ADC 投影栈(CON 读回+PND 完成
    # +RES fixture);RES fixture 由场景传入含键通道行的独立 dict
    # (BY 场景自己的 dict 不变,legacy 报告逐字节不变)。
    post_stop=None
    if key_chain_scan is not None:
        require(p11_power_init and fd31_adc_pnd_completion and fd31_adc_res_named_values is not None,
                'the key-chain direct-scan scenario requires the full BY ADC projection stack')
        require(fd31_adc_res_named_values.get(KEY_CHAIN_CHANNEL_WORD) is not None,
                'the key-chain RES fixture must cover the key channel word')
        post_stop=make_key_chain_post_stop(app)
    return dual_case(module,app,header,rom,name,abstract=True,readonly=readonly,
                     machine_options=machine_options,options=options,budget=budget,
                     extra_diagnostics=extra or None,extra_regions=p11_regions,
                     extra_snapshots=p11_snapshots,post_stop=post_stop)


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
    # 0xF2C940(=P33_ACCESS(0x250),SDK 无名)、0x6067b78 r9=r6+32=0xF2C960。路 AV
    # 按「write-only 字节序列记录」放行无名族:p33_window_projection 覆盖 12 个
    # 字节地址(0x150-0x153/0x250-0x253/0x258-0x259/0x267/0x268,反汇编钉死访问
    # 形状全是单字节写、零读回/零 RMW/零整字写;SDK 命名止于 0xd5),0x6067be4
    # `uextra.rrii.p∥st.b` 走 byte gate(uextra.rrii 是无符号位域提取、效果只有
    # regs+flags,与 ext.b0.u 同族;全 app 12 处该形状站点在既有前缀零执行,故
    # 挂既有 byte_pair 开关不加新开关)对 [0xf2c948] 直写 0x02(旧 r0=1<<index
    # 低字节,run3 方向),A 半 uextra 得 (1<<index)>>8;0x6067c36 普通 st.b 经
    # exact_subword_stores 诊断(路 AU 先例)直写 [0xf2c94c]=0x00;0x6067c38..c3a
    # call 0x60672C8(cfg→port_sel 纯查表,uextra+tbb+名字表循环)返回 0x12。
    # 停点 0x6067c42 `st.w.idxs [0xf2c300+r5<<2]=r0` 写 [0xf2c304]=
    # P3_PORT_SEL1(SDK 有名 P3_PORT_SEL0..15=P33_ACCESS(0xc0..0xcf),字面量
    # 0xf2c300 有 arch_startup 的 opaque 死读先例)整字写 0x12——路 AZ 按
    # lp_timer SYS_DIV/CLK_CON0 整字写 write-only 记录同族放行(p33_port_sel
    # 投影,st.w.idxs 全字覆盖写、旧值无关;唯一读是 0x0606745e 的 opaque
    # 死读,值从不消费)。放行后 walker 0x6067cb4 两轮表全跑:第一轮 4 个
    # 有效项(表 0x0609E964 槽 1..4→RAM item 0x124A2c/38/44/50,cfg=
    # 0x11/0xE0/0x05/0x15;快照窗实测 idx=4 走 0x6067be4 pair 置位路径,
    # 即其 w4≠0——40 字节快照只覆盖到 cfg/b1)各写 PORT_SEL:
    # [0xf2c304]=0x12/[0xf2c308]=0x2f/[0xf2c30c]=0x6/[0xf2c310]=0x16(值=
    # 查表函数 0x60672C8 真实执行结果),idx=3 走 w4==0 清位路径写
    # [0xf2c548]=0xF7/[0xf2c54c]=0xFF;第二轮表(0x0609E994 3 槽→RAM
    # 0x124A5C/68/74)**cfg 全是 0xE2/0xE4/0xE5——全部走 P3_AWKUP 次路径**
    # (查表函数对 0xE2/0xE4/0xE5 返回 2/1/0,作 FLT 下标与通道位;次路径
    # 不写 PORT_SEL)。item[0](cfg=0xE2,通道 2)在 0x6067c6e
    # `st.w.idxs [0xF2C200+r0<<2]=r1` 写 [0xF2C208]=P3_AWKUP_FLT2(SDK 有名
    # P3_AWKUP_FLT0..2=P33_ACCESS(0x80..0x82))=w8=4——路 AZ 到此诚实停,
    # P3_AWKUP 族归路 BB;P3_LS 族+0xf2a628 掩码集归路 BD(sim-plsclear)。
    # **路 BD 落地**:opt-in p3_ls_projection(P3_LS_P11+P3_LS_IO_DLY..PLL 的
    # write-only 整字记录,p33_port_sel 同族)+ p11_syscon1_masks(0xf2a628
    # 掩码集扩展 {0xFFFFFFEF,0xFFFFFF7F},路 AO 先例)。反汇编钉死 walker 清尾
    # 全序列(0x6067d40..d68):并行对 0x6067d48 store 半旧 r0 → [0xF2C290]=0
    # (首清零)、8 条独立 st.w → [0xF2C340..354]=0(六 SDK 名+2 重复)、并行对
    # 0x6067d5e add.s14.rri.p r0+=-7448(字段 8936 的 simm14 负值)∥st.w
    # [旧r0+16]=0 → **[0xF2C350]=0(P3_LS_PLL 第 3 次,非 0xF2A638——路 AZ
    # 「[0xf2a638]=0 唯一写点 0x6067d62」是顺序语义静态读法,本路修正:
    # 0xf2a638 全 app/ROM 零访问,不建模)**、0x6067d64 复合 RMW
    # [0xF2A628]&=0xFFFFFF7F(清 bit7,p11_app.h MSYS_IO_LATCH_DISABLE() 用法宏
    # 同形)。放行后 walker 全返回,B3 装回调两小函数(0x6067d6a/d7a,RAM 全局
    # 写,无停点)如路线梯预期走通,停在 B4 资源 getter 链开头的电源小函数
    # 0x60115b4:`st.w.modimm [0xfd3100]=0x200`——JL PMU/SFR 段(fd3xxx)电源
    # 口,SDK 头无名(只在 HANDOFF「下一步」预告过),归下一路证据核验。
    # TIMER0 精确计数:cnt 8745+24×3=8817(路 BD 新前缀 24 步均匀 3 tick/步),
    # con 0x1401、expirations 10 不变;无新调度往返(commands 20/acks 10、
    # 交付 1+7+2 不变;CRC 30 次往返 900 字节不变——新前缀 24 步全在 walker
    # 清尾/P3_LS 清零/SYS_CON1 RMW/装回调内,不经 CRC/跨核同步)。
    # (历史:路 BD 前沿 304,036 曾停在这里,「电源口/SDK 无名/0x200」三条记录
    # 已由路 BG 修正——见下一段。)
    # **路 BG 落地**:opt-in fd31_adc_con_projection(0xfd3100=JL_ADC->CON 的
    # write-only 整字记录,p33_port_sel/P3_LS 同族)。证据核验修正路 BD 三条
    # 记录:①**命名**——参考 SDK br28.h(路 AC JL_LRCT 同一命名锚点头)
    # `JL_ADC_BASE=ls_base+map_adr(0x31,0)`、`JL_ADC_TypeDef{__RW CON;__RO RES;}`,
    # 0xfd3100=ADC CON、0xfd3104=ADC RES(只读转换结果)——「JL PMU/SFR 段电源
    # 口、SDK 头无名」不成立,「电源小函数」实为 ADC 控制函数;②**首写值**——
    # 0x60115ba `st.w.modimm {m:0xB00}` 的 modimm 旋转展开是 0x20000(304,036
    # 停点 value_bytes '00000200' 小端即 0x00020000),三连写=0x20000→0x20040
    # →0x40;③**读回消费就在启动链上**——0x60120f4/0x6012106 两次背靠背调用
    # (6 条纯写)后,app_core 尾段经 ROM 0x1fd80→0x4226 的 176 字节缓冲
    # memset、0x60115cc+3 组 getter(0x6001230/0x601160e,纯软件表)进入
    # **32 次采样循环 0x6012148..0x601215e**(每轮 call 0x60116d0,读 CON 测
    # bit4 门控、读 [0xfd3104] RES 存软件槽,收集 min/max/sum、末尾
    # (sum-min-max)/30 平均滤波),caller A 首个 fd31 访问=0x60116e6 `ld.w`
    # 读 CON。放行 6 写后(+281 步,全真实执行:memset 单指令 rep 体走上游
    # 原生机制、无新 adapter;不经 CRC/跨核同步)停在 0x60116e6——CON 读回
    # (bit4 门控/bit7 忙等轮询 0x601163a)与 RES(模拟测量)无可推导值,
    # 不发明 fixture,读回严格停 fd31_adc_con_readback_not_modeled。
    # TIMER0 精确计数:cnt 8817+281×3=9660(均匀 3 tick/步),con 0x1401、
    # expirations 10 不变;commands 20/acks 10、交付 1+7+2、CRC 30 次往返
    # 900 字节、全部 adapter 计数不变(新前缀是 memset+软件表+6 条 MMIO 写)。
    # (历史:路 BG 前沿 304,317 曾停在这里——CON 读回边界已由路 BN 放行,
    # 见下一段。)
    # **路 BN 落地**:opt-in fd31_adc_con_readback(CON 读回=最后写值投影,
    # lp_timer CON 路 AL 同族;SDK 证据=br28.h JL_ADC_TypeDef{__RW CON;__RO RES;},
    # 该头 __RW/__WO/__RO 标注有区分度)。逐分支推演(反汇编钉死+实测兑现):
    # ①0x60116e6 读 CON → 投影返回末写值 0x40;②0x60116e8 bit4 门控=0x40&0x10
    # =0 → **跳转成立**,跳过轮询 helper+控制函数,直接 RAM veneer 0x12b55c
    # (VM init 前缀 169,439 已真实走过的 OS post 链);③setter 0x6011666
    # (通道字 0x10000,r2'=r0>>16=1)走 tbb case1(0x6011698:r0=r0.l(u)=0)→
    # call 0x6067d8a 对 **0xf2c250=P3_ANA_CON4**(p33_sfr.h:134 有名)做 5 次
    # 读-改-写(实测末值 0xe1=bit0|bit5|bit6|bit7,与 0x6067d8a 的读&0x1FE1/
    # 置 bit5/bit6/bit7/bit0=1 序列精确自洽;联动 seed 合成初始字 0,路 AM
    # P3_ANA_CON2/CON5 同族语义)→ 汇合尾部 [0xfd3100]=0x46105d(=表字节
    # b[0x124A90] 低 3 位 0x5 | 0x1058 | r0<<2&0x1C0000=0x40000 | 0x420000,
    # 全真实指令计算)+ 复合 RMW |=0x40(0x46105d bit6 已置,合成值不变——
    # mmio_rmw_always_writes 通用路径,lp_timer CON2 同款);④轮询 helper
    # 0x601163a 进入忙等:读 CON=0x46105d → byte0=0x5d 符号扩展非负(bit7=0
    # 不退出)、bit4=1(0x1058 贡献)不退出 → call RAM 延时 0x12e256(nop/
    # 递减/bnez 倒数循环,实测 ~386 步/轮)→ r4 从 -1000 递增,**投影不发明
    # ADC 完成事件**(真实硬件转换完成会置 bit7 提前退出);⑤忙等 ~143k 步
    # 期间 Timer0 STD12M 三次到期(343,252/383,252/423,252,40,000 步精确
    # 间隔)——三次全部落在延时函数上下文(模型 core_icfg gate_demand=0,
    # IRQ4 priority-1 maskable 不合格),pending 挂起零交付(delivery_probe
    # 场景在等待点上下文的交付成功对照,上下文相关性自洽);⑥**447,490 步
    # CPU0 core tick(irq3)首次溢出**:路 BS 把 core_tick_irq3_delivery 接进
    # 本场景后不再停——实测溢出时 gates 0x300 开、nibble 使能,但 **IPMASK=6**
    # (priority 1 < 6 被 masked):整个 ADC 采样事务(OS post veneer 0x12b55c
    # 的 cli+ [0xffe1a8]=6 起,经 setter/1000 轮轮询/RES 读)在**同一个 OS
    # 临界区**内执行——写窗口证据:447,490..689,402 全程零写入(最后写入是
    # setter 的 0xfd3100 双写,其前最后一次 IPMASK 写=6,延时循环零 store),
    # BF 的 pending 期间逐步重检机制恰好把溢出挂起放行(cli 只屏蔽不丢弃);
    # ⑦轮询 1000 轮全预算跑完(~385 步/轮,途中 STD12M 再到期 6 次:463,252/
    # 503,252/543,252/583,252/623,252/663,252,全在临界区内不合格,pending
    # 挂起零交付),helper 返回、caller A 继续到 0x6011704 读 [0xfd3100+4]
    # =0xfd3104 RES —— 路 BS 前沿 689,402 曾诚实停在这里(fd31_adc_res_
    # not_modeled,RES=__RO 模拟测量值,红线绝不发明 0/满值)。
    # **路 BY 落地(sim-adcresfix,本前沿)**:两个新 opt-in 把 RES 边界闭合——
    # ①`fd31_adc_pnd_completion`(PND bit7 无状态读时完成投影,证据=SDK
    # gpadc_hw_v10.h GPADC_CON_PND=7 + gpadc_v10.c 的 adc_wait_pnd/adc_get_res
    # 把 RES 有效性钉在 bit7;显式假设=启动写后固件首次状态读前转换已完成,
    # 全预算轮询 80×~96ms≈7.7s 与实测 ~1s 级启动不相容):80 次 caller A
    # 轮询全部折叠为首读退出——**CON 写记录与无 PND 世界逐值相同**(原子复合
    # RMW 内部读从裸最后写值合成,|=0x40 记录值不变 0x46105d),con_reads
    # 2002→240(80 轮×(1 门控+1 RMW+1 helper 首 read)),pnd_forced_reads=80;
    # ②`fd31_adc_res_named_values`(BU 预研方案 (a):WBG04=400/VBAT_4=500/
    # VTEMP=400,通道识别=P3_ANA_CON4[4:1] 复用域而非 RAM 槽——外设模型不
    # 接触 guest RAM 且复用域就是硬件实际转换的通道;一致性门=最后 CON 写
    # EN=1+CH_SEL=0):served {0x10000:56(32 轮+12×2 getter),0x10004:12,
    # 0x10006:12}。**采样事务全链真实执行**:项0.value=32 轮截尾均值 u16=400、
    # 项1/项2 =固件自己 utof 的 float 945.0/756.0(runtime K=756=801−3×15,
    # divider 真实算术)——**BU §5 [疑]点实测显形:mode 半字=0x0001≠0xFFFF,
    # 存在静态漏看的 mode=1 写者**(getter 存 float 的同路径),adc_get_voltage
    # 走 float 直读分支 → battery_mv=4×945=3780≥3200 过电池门(两条路径都过:
    # 兜底 4×(0x4000×756/400)=124,108)。**前沿换形状**:PND 折叠把 80 次轮询
    # 的 ~31M 步预算塌缩,Timer0 的到期时刻全部重排(延时函数 0x12e256 实测
    # 每次 call 重编 TIMER0 PRD:delay(1000µs)→prd=11999)——首个合格到期
    # (IPMASK=0)落在 336,337 @ 0x12e286,叠路 BE 的 `timer0_irq4_delivery`
    # 后**14 次真实 IRQ4 交付往返**(reti_from 全在延时等待循环 0x12e282..86、
    # handler W1C 真实清 pending、RTI 回被中断点);CPU0 core tick cnt 到
    # 1,199,572(prd 1,199,999)未溢出——路 BY 停点 447,383 恰在旧 447,490 地标前
    # ~107 步(计数器位置路径无关,自洽)。尾段 W1[0](BV 投影接线
    # `p33_analog_tail`):0x60685ae AWKUP 清除助手(RMW 记录 +1×3)→ 0x6012e74/
    # 0x6012e7c P3_CHG_CON0 &=0x1FE/&=0x3FD(AM 容器,seed 0→末值 0)→ 轮询
    # 首读 0x6012f22(pair 基址,装载半在 0x6012f26)读 [0xf2c064]=P3_ANA_READ
    # ——路 BY 曾诚实停在 p3_ana_read_not_modeled。
    # **路 CB 落地(sim-anaread,本前沿)**:`p3_ana_read_ldoin_low`(读侧三选一
    # =方案 (c) 具名常量 live-input fixture,derived-not-measured:SDK
    # charge_hw.h 逐位语义 bit1=LVCMP_DET(ldoin>vbat)/bit2=LDO5V_DET(ldoin>
    # 0.6V)+charge.c 阈值注释+场景锚=电池启动无外部供电 ldoin≈0V → 两比较器
    # 读 0;W1[0] 循环2 与 SDK check_charge_state() 逐行同形;常量服务全部读,
    # derived_mask=0b110、其余位显式填零非推导)放行首读。连锁显形:①W1[0] 的
    # r5+0x220/r5+0xC 计算寻址面(字面量扫描不可见)——P3_L5V_CON0/CON1
    # (0xf2c284/0xf2c288)与 P3_CHG_CON2(0xf2c070,CHARGE_DET_EN/VOL 充电
    # 检测比较器使能)并入 tail AM 容器;②~447,490 core tick 首溢出→IRQ3 交付
    # (handler 0x60661a4),其执行期间 Timer0 到期合格——**交付延期机制**
    # (irq_delivery_deferral,挂起不丢弃:分发层清模型 in-flight 簿记,handler
    # RTI 后下个 advance 以同一 pending 片重新请求;与 BF masked-pending 逐步
    # 重检同族,IRQ 嵌套仍不建模)接住 14 次延期;③IRQ4 交付 31 次(30 次
    # RTI 完成,1 次在途),reti_from 集中在延时等待循环 0x12e282..86 与 OS
    # veneer 0x12b7b6;④OS tick 级联(软件 IRQ127 经调度器)把世界带进
    # 0x6069538 函数读 [0xfd1100]=GPCNT_CON(bit0 使能判定+bit7 轮询+计数读,
    # 上游 &=~1/|=0x40 是 gpcnt_cleanup 已建模形状)。**路 CG 落地(sim-gpcnt,
    # 本前沿)**:GPCNT 读侧三选一=方案 (a) 投影 `gpcnt_con_readback`——读回
    # =固件自己已执行写出的位粒度已知状态(br28.h CON __RW;全 app+ROM 唯一
    # 写者=disable+clear 位 RMW 对;唯一读者=任务 0x6069538 的 beq.and.ri
    # bit0 使能判定,AND 掩码外位强制为已知 0 → 单 bit 谓词可判,未知位任何
    # 消费诚实停)。bit0=0(app 自己 ~108k 关断)→ 真实跳过分支(0x60695fa
    # 函数尾直接返回,无 NUM 读无 bit7 轮询)。放行后连锁:①GPCNT 任务在 9
    # 次 core tick 级联里被调度 9 次、每次读 CON 走跳过分支(gpcnt_con_
    # readback_load=9);②W1[0] 循环2 20 轮 delay(每轮 ~120k 步)全预算跑完,
    # fixture 服务全部 41 读(循环1 1+循环2 20×2),两比较器恒 0 → 20 轮
    # 超时路径真实执行(不创建充电去抖任务);③core tick 又溢出交付 8 次
    # (合计 9)、Timer0 OS 1ms tick 交付至 286 次、LRC 周期链推到第 10 帧;
    # ④超时后世界进 mode 切换函数 0x6068152(r0==1 分支),路 CC 预研钉死的
    # JL 时钟族首访问 [0xfd0008]=DCCK_CON0。**路 CL 落地(sim-dcck,本前沿)**:
    # DCCK 读侧三选一=方案 (a) 投影 `jl_clock_dcck_projection`(路 AM 容器同款
    # 「显式合成初始字 0 + 读回=当前寄存器值」;br28.h JL_CLOCK_TypeDef 全字段
    # __RW + SDK power_clock.c deep-sleep save/restore 双证据;唯一消费者
    # 0x6068152 的显式读值全部被掩码+OR 消化,seed 0 下确定性位域配置)。放行
    # 后 mode 切换函数体全部真实执行:DCCK_CON0 七步位域配置(末值 0x46000e0c)、
    # DCCK_CON1 两步(末值 0xff000100)、DCCK_CON2 整字写 0x10000、WLA_CON8
    # |=0x8000000(OSC 族既有)、P3_ANA_CON0/7/8/9/10 配置 RMW(BV 容器)、
    # call 0x12cd04 软件延时、OS 临界区退出(0x12b5b0)。W1[0] 返回(0x6013196),
    # 路 CO:0x6065c4c 的 `if ((r0&0xFF)!=0)`(bne.and.modimm.ri.l,i=0xFF)是
    # active-priority 低字节掩码测试。empty-active-priority fixture(initial
    # mask 0x3ff/value 0,gates 由 CLI/STI 与 gate 写推到 0x300)下低字节 bits0-7
    # 已知为 0,`r0&0xFF=0x300&0xFF=0`、分支不跳——与既有 ext.b0.u 处理器同款
    # 「已知低字节按需消费」,opt-in `partial_icfg_masked_branch` 处理器放行
    # (低字节未知时仍经 .concrete() 诚实停 partial_icfg_value_consumed)。放行后
    # 0x6065c3a 判定「非 IRQ 上下文(低字节 0)」→ call 0x12cb08(RTOS 临界查询,
    # 返 1)→ r8=1 跳过错误路径 → call 0x60659ee→0x12b55c OS post veneer 进临界
    # 区(IPMASK 0→6)→ 停 0x6065ccc `st.w.sp.l [sp+8]=r9` 的 upstream 存储模型
    # 簿记合并读未初始化局部 0x19e2d4(st.d.sp.l/mov.rr.p∥st.h 同族,归下一路)。
    # **新前沿 3,070,955 @ 0x6065ccc**。**路 CQ(本次)**：0x6065ccc
    # `st.w.sp.l [sp+8]=r9` 是长位移 sp 相对整字存储，全 4 字节覆盖写、旧值
    # 无关（与 st.w.sp/st.d.sp.l 同论证），三选一选方案 (a) 独立子开关
    # `exact_word_store_sp_l`（该形状在 legacy 前缀已真实执行 3 次，并入主开关
    # 会改 legacy 报告 adapter 计数，故照半字先例单独 opt-in）放行——不走上游
    # 存储模型的写前合并读（模型内部簿记读、非 guest 装载）。放行后 scheduler
    # helper 0x6065c3a 尾部继续（0x6065cd0 add.r_sp_i/0x6065cd2 call 位域
    # 配置助手，[sp+4]=1/[sp+8]=r9 两个局部落盘），返回 W1[1] walker（0x6013198）
    # 后续，最终停在 **0x601d1ac `ld.w r0=[r2+0]` 读 0xfd2000**（r2=li.u32
    # 16588800=0xfd2000，r1==0 分支；r1==1/2 分别读 0xfd2100/0xfd2200——
    # 一个 switch 枚举 0/1/2 的读辅助函数 0x601d198，其 r1 来自 0x601d18e
    # `call.xl -99550550` 的返回值）——ls_base 0xfd0000 + map_adr(0x20,0)。
    # **命名修正（路 F/sim-fd2000）**：0xfd2000/0xfd2100/0xfd2200 = JL_UART0/1/2
    # 基址（br28.h `JL_UART0_BASE=ls_base+map_adr(0x20,0)`，非「SDK 参考头未
    # 命名」——路 CQ 记录有误），读辅助函数 0x601d198 = SDK uart_v1.c
    # `uart_is_idle(uart_num)`（cpu.a/uart_v1.c.o LLVM bitcode 逐字节同形：
    # switch{0:*(0xfd2000),1:*(0xfd2100),2:*(0xfd2200)};return (word&1)^1）。
    # 读侧三选一=方案 (a) 投影 `jl_uart_con_projection`（路 AM/DCCK 容器同款
    # 「显式合成初始字 0 + 读回=当前寄存器值」；br28.h CON0/CON1 __RW；bit0
    # 是硬件 busy/空闲状态位、固件从不写——合成 0=空闲，derived-not-measured
    # 非硅测量/非复位值声明）。放行后 uart_is_idle(0) 读 0xfd2000 得 0→bit0=0
    # →返回 1（空闲）→ uart_init 真实执行（CON0=0x3400 整字写/|=0x3 等 __RW
    # 配置写走通用 registers 路径），停 **0x603446a `[r2+4]=r3` 写 0xfd2004**
    # （=JL_UART.BAUD|BUF，BAUD 标 __WO——UART 写侧是下一路三选一）。**新前沿
    # 3,071,984 @ 0x603446a**（unmapped_or_readonly_write 写 0xfd2004）。
    # **路 M(sim-uartwr，本次)**：UART 写侧三选一按 codex 定调选方案 (a1) 一次性
    # 打包初始化写白名单 `jl_uart_init_write_records`。**布局钉死（修正路 F 的
    # 「0xfd2004=BAUD|BUF」命名）**：实际 SDK 构建对象 uart_v1.c.o bitcode 声明
    # `JL_UART_TypeDef = {i32 x12}`——12 成员各占一个 32 位字（成员 k 在 +4k:
    # CON0=0/CON1=4/BAUD=8/BUF=0xc/OTCNT=0x10/TXADR=0x14/TXCNT=0x18/RXSADR=0x1c/
    # RXEADR=0x20/RXCNT=0x24/HRXCNT=0x28/CON2=0x2c），QCY 固件写序列逐点互证
    # （uart_init 内联体 0x6034466 `[r2+0]=0x3400`/0x603446c `[r2+4]=0`/
    # 0x6034472 `[r2+44]=0` 写字 0/1/11；uart_set_baudrate 0x601d286 `[r7+8]=r0`
    # 写字 2=BAUD 分频值；uart_refresh_otcnt 0x601d1f2 `[r2+16]=r3` 写字 4=
    # OTCNT（CON0 bit6 门控）；RX 三连写 0x6034668..cc `[r6+28/32/36]` 写字
    # 7/8/9=RXSADR/RXEADR/RXCNT）；公开 br28.h 的 u16/u8 紧凑布局（BAUD u16@+4）
    # 与该构建对象不一致（BR31 等系列公开头确有紧凑布局，是布局分歧非笔误，
    # 以编译对象+固件字节为准）——**0xfd2004 是 CON1 字、真 BAUD 写在
    # 0xfd2008**。白名单=初始化序列实际出现的纯配置整字写 CON1/BAUD/OTCNT/
    # RXSADR/RXEADR/RXCNT/CON2（三基址对称），lp_timer SYS_DIV/P3_PORT_SEL 的
    # write-only 记录先例；TXADR/TXCNT/BUF/HRXCNT 不进白名单（TX 路径字段，
    # TXCNT 写=启动 TX 硬件活动的信号，codex 风险点，出现即诚实停）；读回/
    # RMW 严格停；运行效果（波特率/RX/TX DMA/UART IRQ）不建模。放行后
    # CON1=0/CON2=0 两写落地（BAUD/OTCNT/RX 三连写在 GPIO 停点之后、本前缀
    # 未到达），uart_init 继续 TX/RX 引脚配置，gpio_set_mode(0x6085126)/
    # gpio_set_function(0x6001456) 的助手链 0x60012c2 在 0x60012de `[r3+0]=r4`
    # 写 **0xfd58b8=0x9f**——0xfd58xx GPIO 端口组寄存器（GPIO_BASES 0x50..0x55/
    # 0x57 未覆盖、公开 br28.h 无名）——UART 引脚复用 GPIO 面，归下一路三选
    # 一。**新前沿 3,072,236 @ 0x60012de**（unmapped_or_readonly_write 写
    # 0xfd58b8）。
    # 路 R(sim-gpioface):jl_omap_write_records 放行 0x60012de 的 0xfd58b8=
    # 0x9f(=FO_UART0_TX,识别见 jl_omap 断言)后,uart_init 的 TX 复用链完成,
    # 第 297 次 Timer0 到期在 GPIO 助手临界区退出后**真实交付**(287 次,见
    # t0 断言),RX 复用链 gpio_set_function(PORTUSB,PIN0,PORT_FUNC_UART0_RX)
    # → gpio_set_fun_input_port 的助手 0x600132e 在 0x6001342 `[r4+0]=r3` 整字
    # 写 **0xfd5c90=0x2f**——0xfd5c90 = JL_IMAP->FI_UART0_RX(io_imap.h
    # `JL_IMAP_BASE=ls_base+map_adr(0x5c,0)`=0xfd5c00,FI_UART0_RX 是第 36 个
    # 成员、实际布局 4 字步进=0xfd5c90,PFI_UART0_RX 常量 16604304 互证),值
    # 0x2f=47=USBDP_IN(DP 脚的输入通道号,gpio2crossbar_inport(224)=47 与
    # 固件助手 0x60012e8 双侧同值)——UART0 RX 与 TX 同用 USB DP 脚(单线
    # UART)。JL_IMAP 写侧归下一路三选一。**新前沿 3,073,402 @ 0x6001342**
    # (unmapped_or_readonly_write 写 0xfd5c90)。
    require(r['stop_core']==0 and r['stop_pc']=='0x6001342' and
            r['stop']=={'kind':'unmapped_or_readonly_write','access':'write',
                        'address':'0xfd5c90','size':4,'value_bytes':'2f000000'},
            'the heap-experimental scenario must stop at 0x6001342 writing 0xfd5c90 after the '
            'route-R jl_omap_write_records release of the 0xfd58b8 JL_OMAP pin-mux write: the '
            'TX mux chain completes, the in-flight 297th Timer0 expiration is DELIVERED after '
            'the critical-section exit, and the RX mux chain (gpio_set_function '
            'PORT_FUNC_UART0_RX -> gpio_set_fun_input_port helper 0x600132e) reaches a '
            'whole-word write of 0xfd5c90 = JL_IMAP->FI_UART0_RX (io_imap.h '
            'map_adr(0x5c,0)=0xfd5c00, member 36 at the compiled 4-byte stride, the '
            'PFI_UART0_RX constant 16604304) with value 0x2f=47=USBDP_IN — the DP-pin input '
            'channel of gpio2crossbar_inport(224); UART0 RX shares the USB DP pin with TX '
            '(single-wire UART), and the JL_IMAP write side is the next three-choice')
    require(r['completed_steps']==3073402,'the heap experimental config changed its step count')
    dcck=r['peripherals']['registers']
    require(dcck['0xfd0008']=='0x46000e0c' and dcck['0xfd000c']=='0xff000100' and
            dcck['0xfd0010']=='0x10000',
            'the jl_clock_dcck projection must leave the three DCCK words at their deterministic '
            'field-configuration values (DCCK_CON0 0x46000e0c from |=0x4 -> &=0xFFFFFFE7|8 -> '
            '&=0xFFFFFF3F -> &=0xFFFFF0FF|0xE00 -> &=0xFFFFCFFF -> (old&0xFFFF)|0x46000000 -> '
            '&=0xFFFFFFFC; DCCK_CON1 0xff000100 from &=0xFFFF0000|0x100 -> (old&0xFFFF)|0xFF000000; '
            'DCCK_CON2 the sole whole-word write 0x10000)')
    # 路 F(sim-fd2000):jl_uart_con_projection 的读回投影把 0x601d1ac 的
    # uart_is_idle(0) 读放行(合成初始字 0=空闲、bit0=0),随后 uart_init 真实
    # 执行 CON0=0x3400 整字写(通用 __RW registers 路径);UART0 CON 字末值
    # 0x3400、UART1/2 CON 字保持合成 0(本前缀只 init UART0)。
    uart_assumption=r['peripherals']['assumptions'].get('jl_uart_con_projection')
    require(uart_assumption is not None and 'JL_UART' in uart_assumption and
            'uart_is_idle' in uart_assumption and 'derived-not-measured' in uart_assumption,
            'the route-F jl_uart_con_projection assumption must record the JL_UART naming, the '
            'uart_is_idle bit0-test consumption and the derived-not-measured idle seeding')
    require(dcck['0xfd2000']=='0x3400' and dcck['0xfd2100']=='0x0' and dcck['0xfd2200']=='0x0',
            'the jl_uart_con_projection must leave UART0 CON0|CON1 at the uart_init-written '
            '0x3400 and UART1/UART2 CON words at the untouched synthetic 0 (only UART0 is '
            'initialized in this prefix)')
    # 路 M(sim-uartwr):初始化写白名单的记录断言——本前缀实际落地的两条写是
    # uart_init 的 CON1=0(0xfd2004,3,071,984 停点放行)与 CON2=0(0xfd202c,
    # 紧随其后的 [r2+44]=r3);BAUD/OTCNT/RXSADR/RXEADR/RXCNT 在白名单里但
    # 位于 GPIO 停点之后、本前缀未执行(静态序列钉死:uart_set_baudrate
    # 0x601d286/refresh_otcnt 0x601d1f2/RX 三连写 0x6034668..cc)。假设键必须
    # 记录布局钉死(CON1 命名修正)与 derived-not-measured。
    jl_init=r['peripherals']['jl_uart_init_write_records']
    require(jl_init['stores']=={'0xfd2004':{'values':['0x0'],'writes':1},
                                '0xfd202c':{'values':['0x0'],'writes':1}} and
            jl_init['word_writes']==2,
            'the route-M jl_uart_init_write_records must record exactly the two executed '
            'uart_init config writes (CON1/+4=0 released from the 3,071,984 stop and the '
            'immediately following CON2/+0x2c=0); the BAUD/OTCNT/RXSADR/RXEADR/RXCNT words '
            'are whitelisted but sit after the GPIO stop and must not appear yet')
    jl_init_assumption=r['peripherals']['assumptions'].get('jl_uart_init_write_records')
    require(jl_init_assumption is not None and 'CON1 word' in jl_init_assumption and
            'derived-not-measured' in jl_init_assumption and
            'NOT verified UART transmission/reception' in jl_init_assumption,
            'the route-M jl_uart_init_write_records assumption must record the {i32 x12} '
            'layout pin-down (0xfd2004 = CON1, route-F naming corrected), the '
            'derived-not-measured write records and the no-UART-traffic scope limit')
    # 路 R(sim-gpioface):JL_OMAP 白名单的记录断言——本前缀恰一条写:
    # 0x60012de 的 0xfd58b8=0x9f(core 0)。识别三方交叉(SDK 驱动表 gpio_out_regs
    # 与固件表 0x609EA9C 逐字节一致、gpio 224/225=IO_PORT_DP/DM 双侧特例
    # 0xfd58b8/0xfd58bc、值 0x9f=io_omap.h FO_UART0_TX=((39<<2)|BIT(1)|BIT(0)))
    # 必须进 assumption;49 字白名单(成员数=gpio_hw.h 端口掩码)钉死;
    # USBDM(0xfd58bc)本前缀零写(RX 与 TX 同用 DP 脚)。
    omap=r['peripherals']['jl_omap_write_records']
    require(omap['stores']=={'0xfd58b8':{'values':['0x9f'],'writes':1,'cores':[0]}} and
            omap['word_writes']==1 and len(omap['addresses'])==49 and
            omap['write_only'] and omap['readback_not_modeled'] and
            omap['addresses'][0]=='0xfd5800' and omap['addresses'][-1]=='0xfd58c0',
            'the route-R jl_omap_write_records must record exactly the one executed JL_OMAP '
            'pin-mux write (0x60012de, USBDP_OUT 0xfd58b8 = FO_UART0_TX 0x9f, core 0) over '
            'the declared 49-word bank PA0_OUT..PP0_OUT (0xfd5800..0xfd58c0, driver 4-byte '
            'stride); USBDM_OUT 0xfd58bc is not written in this prefix (UART0 RX shares the '
            'DP pin)')
    omap_assumption=r['peripherals']['assumptions'].get('jl_omap_write_records')
    require(omap_assumption is not None and 'gpio_out_regs' in omap_assumption and
            '0x609EA9C' in omap_assumption and 'FO_UART0_TX' in omap_assumption and
            'derived-not-measured' in omap_assumption and
            'NOT verified UART transmission/reception or USB function' in omap_assumption,
            'the route-R jl_omap_write_records assumption must record the three-way '
            'identification (SDK gpio_out_regs table == firmware table 0x609EA9C, the '
            'IO_PORT_DP/DM specials, FO_UART0_TX value), the derived-not-measured write '
            'records and the no-traffic scope limit')
    fd31=r['peripherals']['fd31_adc_con_projection']
    require(fd31['stores']=={'0xfd3100':{'values':['0x40','0x46105d','0x46105d','0x20000',
                                                  '0x20040','0x40','0x46105d','0x46105d',
                                                  '0x20000','0x20040','0x40','0x46105d',
                                                  '0x46105d','0x20000','0x20040','0x40'],
                                  'writes':406}} and
            fd31['word_writes']==406 and
            not fd31['write_only'] and not fd31['readback_not_modeled'] and
            fd31['addresses']==['0xfd3100'] and
            'NOT 0x200' in fd31['observed_shapes'] and 'JL_ADC' in fd31['sdk_naming'] and
            fd31['con_readback_projection']['reads']==258 and
            fd31['con_readback_projection']['address']=='0xfd3100' and
            fd31['con_readback_projection']['res_boundary']['address']=='0xfd3104' and
            fd31['pnd_completion_projection']['forced_reads']==80 and
            fd31['pnd_completion_projection']['bit']=='0x80' and
            fd31['pnd_completion_projection']['not_a_timing_model'] and
            fd31['res_named_values_fixture']['served']=={'0x10000':56,'0x10004':12,'0x10006':12} and
            fd31['res_named_values_fixture']['values']=={'0x10000':400,'0x10004':400,'0x10006':500} and
            fd31['res_named_values_fixture']['derived_not_measured'],
            'the fd31 ADC CON projection must record 406 word writes (six control-function stores '
            'plus the 80 caller-A rounds x 5: setter 0x46105d, compound |=0x40 whose synthesized '
            'value is UNCHANGED because the route-BY raw-RMW synthesis does not observe the '
            'projected bit7, then the control function 0x20000/0x20040/0x40), 258 projected reads '
            '(80 rounds x (bit4 gate + RMW-internal + first poll read — every poll folded by the '
            'PND completion projection, forced_reads=80 — plus the reads of the route-CB/CG '
            'OS-tick cascade prefix) and the RES fixture serving 56+12+12 '
            'reads keyed by the analog-mux field')
    # P3_ANA_CON4(0xf2c250):末值 0xe9=最后 getter(VTEMP)的 mux 域(0xE1|域4;
    # WBG04 轮=0xE1(bit0|bit5|bit6|bit7)、VBAT_4 轮=0xED(域6)、VTEMP 轮=0xE9
    # (域 4)——bit5-7 使能位在首轮后保持,域值随通道切换,与 0x6067d8a 的
    # idx==0(|0x20/0x40/0x80 五连)与 idx!=0((读&0x1FE1)|(idx<<1) 两形状互证)。
    require(r['peripherals']['registers']['0xf2c250']=='0xe9',
            'P3_ANA_CON4 must end at 0xe9: the WBG04 rounds settle 0xe1 (mux 0 + enable bits '
            '0x20/0x40/0x80|1 from the idx==0 helper shape) and the final VTEMP getter round '
            'writes mux field 4 (0x6067d8a nonzero-index shape: ([reg]&0x1FE1)|(4<<1))')
    # 路 CB 尾段:tail 容器十个地址全部有真实访问记录——路 BV 的 P3_CHG_CON0
    # 双 RMW(0x6012e74/0x6012e7c,seed 0→0)之外,fixture 放行后的新前缀真实
    # 执行 W1[0] 的充电配置族:CHG_CON0 末值 0xC(0x6012fe6 &=0x7FB/0x60130b0
    # &=0xFF7/条件写回链)、CHG_CON1 末值 0x96(0x6013094..0x601310e 字段写)、
    # CHG_CON2 末值 0x10(0x6013094 pair 尾写回 |=配置字 后续 &=0xFF7 族——
    # CHARGE_DET_EN/VOL 使能链)、L5V_CON0/1 末值 0x1/0x3(0x6012fc0/0x6012fd6
    # r5+0x220 计算寻址配置 RMW)。路 CL:DCCK 投影放行后 mode 切换函数
    # 0x6068152 体真实执行,把 P3_ANA_CON0/7/8/9/10 从 seed 0 写成确定性配置
    # (CON7=0x49/CON8=0x66/CON9=0x6e/CON10=0x31/CON0=0x8,见 0x60681d4..828a
    # 反汇编逐位与末值精确互证)。
    tail={k:v for k,v in r['peripherals']['registers'].items()
          if k in ('0xf2c068','0xf2c06c','0xf2c070','0xf2c240','0xf2c25c','0xf2c260',
                   '0xf2c264','0xf2c268','0xf2c284','0xf2c288')}
    require(tail=={'0xf2c068':'0xc','0xf2c06c':'0x96','0xf2c070':'0x10','0xf2c240':'0x8',
                   '0xf2c25c':'0x49','0xf2c260':'0x66','0xf2c264':'0x6e','0xf2c268':'0x31',
                   '0xf2c284':'0x1','0xf2c288':'0x3'},
            'the route-BV/CB/CL tail container must end at the real configuration: the '
            'fixture-released prefix executes the W1[0] charger family (CHG_CON0 0xc, CHG_CON1 '
            '0x96, CHG_CON2 0x10, L5V_CON0/1 0x1/0x3 via the r5+0x220 computed addressing) and, '
            'after the route-CL jl_clock_dcck projection releases the mode-switch function '
            '0x6068152, the P3_ANA_CON0/7/8/9/10 config RMWs write 0x8/0x49/0x66/0x6e/0x31 '
            'deterministically from the synthesized zero seeds')
    # 路 CG:P3_ANA_READ fixture 报告——41 次读全部常量服务 0(循环1 首读 1+
    # 循环2 全部 20 轮各 2 读,超时路径全预算跑完),四键分离标注(derived_
    # mask/other_bits/duration/evidence_status)。
    ana=r['peripherals']['p3_ana_read_ldoin_low_fixture']
    require(ana['address']=='0xf2c064' and ana['reads_served']==41 and
            ana['kind']=='constant_external_input_fixture' and
            (ana['value'],ana['derived_mask'],ana['evidence_status'])==
            ('0x0','0x6','derived-not-measured') and
            ana['duration'].startswith('constant_for_this_scenario') and
            ana['other_bits'].startswith('explicitly_zero_filled_not_derived') and
            ana['writes_rejected'] and 'LVCMP_DET' in ana['derived_bits']['bit1'] and
            'LDO5V_DET' in ana['derived_bits']['bit2'],
            'the P3_ANA_READ ldoin-low fixture must serve forty-one constant-zero reads (loop-1 '
            'first read plus all twenty loop-2 double-read rounds through the full timeout path '
            'released by the route-CG GPCNT readback) with the four-way labeling: derived mask '
            '0x6 only, other bits explicitly zero-filled not derived, constant for this '
            'scenario, derived-not-measured')
    # ADC 队列快照:fixture 下游算术逐字节钉死(项0 32 轮截尾均值 u16=400、
    # 项1/2 固件自己 utof 的 float 945.0=0x446C4000/756.0=0x443D0000,runtime
    # K=756;mode 半字=1 —— BU §5 [疑]点实测显形,存在静态漏看的 mode=1 写者,
    # adc_get_voltage 走 float 直读,battery_mv=4×945=3780≥3200 过门)。
    snap=r['memory_snapshots']
    require(snap['adc_item0_value']['bytes_with_unknowns']=='9001ffff' and
            snap['adc_item0_value']['initialized_bytes']==4 and
            snap['adc_item1_entry']['bytes_with_unknowns']=='0600010000406c4401003200' and
            snap['adc_item1_entry']['initialized_bytes']==12 and
            snap['adc_item2_entry']['bytes_with_unknowns']=='0400010000003d4401003200' and
            snap['adc_item2_entry']['initialized_bytes']==12,
            'the ADC queue snapshots must pin item0.value=400 (u16, the 32-round trimmed mean '
            'of the constant WBG04 fixture sample), item1 (VBAT_4) key/value/mode/period = '
            '0x10006 / float 945.0 (0x446C4000, firmware-computed 500*756/400) / mode 1 / 50 '
            'and item2 (VTEMP) = 0x10004 / float 756.0 (0x443D0000, 400*756/400) / 1 / 50 — '
            'the mode halfword 1 (not the memset residue 0xFFFF) resolves the BU mode doubt by '
            'execution: a mode=1 writer exists on the getter store path')
    # 路 CG:Timer0 IRQ4 真实交付往返 ×286(路 CB 31 次+新前缀 255 次——
    # W1[0] 循环2 全部 20 轮 delay 与超时后尾段的 OS 1ms tick 持续到期交付;
    # reti_from 仍集中在延时等待循环 0x12e282..86 与 OS veneer 0x12b7b6);
    # 286 次 RTI 全部完成(572 帧=每次交付 2 个特殊帧锚)。core tick 溢出
    # 交付合计 9 次(首溢出~447,490→IRQ3 handler 0x60661a4 向量槽 0x19fe0c
    # →W1C ACK;后续 8 次溢出随 OS tick 级联继续交付,GPcnt 任务每次调度
    # 一个来回);期间 Timer0 到期合格的请求被延期机制挂起合计 138 次(全部
    # deferred_while=core_tick_handler),handler RTI 后重新交付。
    # 路 R(sim-gpioface):第 287 次交付=停点前在途的第 297 次到期(route M 记录
    # 的 pending=True、ICFG 0x100 首例)——GPIO 助手临界区退出后资格链放行,
    # reti_from=0x12b7b6(OS veneer 上下文,交付打断的是 TX 复用链里的 OS 调用),
    # handler 真实 W1C 清 pending(停点 pending=False)。
    t0=r['machine']['timer0_irq_entry_projection']
    require(len(t0['deliveries'])==287 and
            all((d['core'],d['irq'],d['vector_address'],d['handler'])==(0,4,'0x19fe10','0x12ece8')
                for d in t0['deliveries']) and
            all(d['reti_from'] in ('0x12e282','0x12e284','0x12e286','0x12b7b6')
                for d in t0['deliveries']) and
            sum(1 for d in t0['deliveries'] if d['reti_from']=='0x12b7b6')==2 and
            t0['deliveries'][-1]['reti_from']=='0x12b7b6' and
            all(d['pending_cleared_by_handler_ack'] and not d['delivery_clears_pending']
                for d in t0['deliveries']) and
            r['instruction_adapters'].get('timer0_irq_handler_frame')==574 and
            r['instruction_adapters'].get('conditional_timer0_irq_rti')==287,
            '287 real Timer0 IRQ4 delivery round trips (thirty-one through the route-CB stop '
            'plus the OS 1ms tick of the full W1[0] twenty-round timeout window and the tail '
            'released by the GPCNT readback, then the route-R delivery of the previously '
            'in-flight 297th expiration: the GPIO-helper critical section exits, the pending '
            'episode becomes eligible and interrupts the OS veneer context 0x12b7b6 inside '
            'the continued TX mux chain), each reading the real vector slot 0x19fe10 and '
            'clearing pending only via the real handler W1C; 287 symmetric RTIs complete')
    ct=r['machine']['core_tick_entry_projection']
    require(len(ct['deliveries'])==9 and
            (ct['deliveries'][0]['core'],ct['deliveries'][0]['vector_address'],
             ct['deliveries'][0]['handler'],ct['deliveries'][0]['reti_from'])==
            (0,'0x19fe0c','0x60661a4','0x12b7b6') and
            all(not d['delivery_clears_pending'] for d in ct['deliveries']) and
            r['instruction_adapters'].get('core_tick_handler_frame')==18 and
            r['instruction_adapters'].get('conditional_core_tick_rti')==9 and
            r['instruction_adapters'].get('core_tick_ack_rmw')==9,
            'the CPU0 core tick must deliver its first overflow (the ~447,490 landmark) through '
            'the real IRQ3 vector slot 0x19fe0c to handler 0x60661a4, interrupting the OS '
            'veneer context 0x12b7b6, with pending cleared only by the handler ack; the '
            'route-CG window adds eight more overflows (nine deliveries, nine acks, nine '
            'symmetric RTIs — the OS tick cascade the GPCNT task rides)')
    dfr=r['machine']['irq_delivery_deferral_projection']
    require(dfr['count']==138 and
            all(e['irq']==4 and e['deferred_while']=='core_tick_handler'
                for e in dfr['events']),
            '138 Timer0 delivery requests must be HELD (not dropped) while a core-tick '
            'handler runs, each re-requested by the next advance() after the handler RTI — '
            'the route-CB deferral projection, same held-not-dropped philosophy as the '
            'route-BF masked-pending re-checks; IRQ nesting stays unmodeled')
    # 路 CG:GPCNT 读回投影——disable+clear 各 1 次(与 clock 族共享的早期
    # OS/tick init 前缀)、9 次读全部位粒度部分值服务(mask 0x81/value 0,
    # bit0=0 由 app 自己的 &=0xFFFFFFFE 强制),未知位零消费(报告键仅在
    # 投影开启时出现,clock 场景字节不变)。
    gp=r['peripherals']['gpcnt_cleanup_projection']
    require(gp['known_mask']=='0x81' and gp['known_value']=='0x0' and
            gp['disable_writes']==gp['clear_writes']==1 and
            gp['con_readback_projection']['reads']==9 and
            gp['con_readback_projection']['address']=='0xfd1100' and
            gp['con_readback_projection']['known_mask']=='0x81' and
            not gp['con_readback_projection']['count_readback_proven'] and
            gp['con_readback_projection']['not_a_timing_model'] and
            r['instruction_adapters'].get('gpcnt_con_readback_load')==9,
            'the GPCNT CON readback projection must serve nine partial-word reads (one per '
            'task 0x6069538 dispatch across the nine OS-tick cascades), each answering only '
            'the bit0 enable gate from the bits the firmware itself wrote (disable+clear once '
            'each in the shared early prefix); the NUM count and bit7 poll are never reached '
            '(the real skip branch), unknown bits are never fabricated and the count readback '
            'stays unproven')
    reset_config=r['peripherals']['reset_history_projection']['configuration']
    require(reset_config['0xf2c054']=={'known_mask':'0xffffc0e0','known_value':'0x0'} and
            r['peripherals']['reset_history_projection']['accepted_configuration_mask_sets']==
            {'0xf2a628':['0xffffffef','0xffffff7f'],'0xf2c054':['0x3fdf','0xff7f','0x7fbf']},
            'the P3_RST_CON0 configuration must record the bitwise union of the three observed '
            'masks (0x3fdf clears bit5, 0xff7f clears bit7, 0x7fbf clears bit6) and P11_SYS_CON1 '
            'the union of its two observed masks (0xffffffef clears bit4 from the reset-init path, '
            '0xffffff7f clears bit7 from the walker tail) as the accepted mask sets')
    require(reset_config['0xf2a628']=={'known_mask':'0x90','known_value':'0x0'},
            'the P11_SYS_CON1 configuration must record exactly bit4|bit7 (0x90) cleared by the '
            'two observed masks, every other bit stays unknown')
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
    p33win=r['peripherals']['p33_window_projection']
    require(p33win['byte_writes']==28 and
            p33win['stores']=={'0xf2c548':{'values':['0xf7'],'writes':1},
                               '0xf2c54c':{'values':['0xff'],'writes':1},
                               '0xf2c940':{'values':['0x2','0x4','0x8','0x10'],'writes':4},
                               '0xf2c944':{'values':['0x0','0x0','0x0','0x0'],'writes':4},
                               '0xf2c948':{'values':['0x2','0x4','0x10'],'writes':3},
                               '0xf2c94c':{'values':['0x0','0x0','0x0'],'writes':3},
                               '0xf2c960':{'values':['0x2','0x4','0x8','0x10','0xff'],'writes':5},
                               '0xf2c964':{'values':['0x0','0x0','0x0','0x0','0xff'],'writes':5},
                               '0xf2c99c':{'values':['0xff'],'writes':1},
                               '0xf2c9a0':{'values':['0xff'],'writes':1}} and
            p33win['write_only'] and p33win['readback_not_modeled'] and
            p33win['addresses']==[hex(a) for a in (0xf2c540,0xf2c544,0xf2c548,0xf2c54c,
                0xf2c940,0xf2c944,0xf2c948,0xf2c94c,0xf2c960,0xf2c964,0xf2c99c,0xf2c9a0)],
            'the P33 unnamed-window projection must record exactly the 28 executed byte '
            'stores (four first-loop items: set paths [0xf2c948]/[0xf2c94c] for indexes '
            '1/2/4, clear path [0xf2c548]=0xF7/[0xf2c54c]=0xFF for index 3, the four '
            'tail stores per item, plus the four walker-tail 0xFF bitmap clears at '
            '0x6067d2e..d38 into [0xF2C99C]/[0xF2C9A0]/[0xF2C960]/[0xF2C964])')
    port_sel=r['peripherals']['p33_port_sel_projection']
    require(port_sel['word_writes']==4 and
            port_sel['stores']=={'0xf2c304':{'values':['0x12'],'writes':1},
                                 '0xf2c308':{'values':['0x2f'],'writes':1},
                                 '0xf2c30c':{'values':['0x6'],'writes':1},
                                 '0xf2c310':{'values':['0x16'],'writes':1}} and
            port_sel['write_only'] and port_sel['readback_not_modeled'] and
            len(port_sel['addresses'])==12,
            'the P3_PORT_SEL projection must record exactly the four first-loop full-word '
            'stores (values are real executed 0x60672C8 lookup results for cfg '
            '0x11/0xE0/0x05/0x15) and stay write-only')
    awkup=r['peripherals']['p33_awkup_projection']
    # 路 BY:W1[0] 入口的独立清除助手 0x60685ae([0xf2c19c](P_CPND)|=4、
    # [0xf2c1a0](N_CPND)|=4、[0xf2c180](EN)&=~4——BV §8 修正二钉死的形状)
    # 再添三条位记录;P_IE 首读 fixture 仍只服务一次。
    require(awkup['stores']=={'0xf2c184':{'values':['0x4'],'writes':1},
                              '0xf2c200':{'values':['0x5'],'writes':1},
                              '0xf2c204':{'values':['0x5'],'writes':1},
                              '0xf2c208':{'values':['0x4'],'writes':1}} and
            awkup['rmw_records']=={'0xf2c180':{'known_mask':'0x7','known_value':'0x3','writes':4},
                                   '0xf2c184':{'known_mask':'0x3','known_value':'0x3','writes':2},
                                   '0xf2c188':{'known_mask':'0x7','known_value':'0x3','writes':3},
                                   '0xf2c19c':{'known_mask':'0x7','known_value':'0x7','writes':4},
                                   '0xf2c1a0':{'known_mask':'0x7','known_value':'0x7','writes':4}} and
            awkup['first_read_fixture']=={'0xf2c184':{'word':'0x0','reads_served':1}} and
            awkup['fixture_is_synthetic_assumption'] and
            awkup['records_write_accesses_only'] and
            'every recorded P_IE bit except {0,1,2} depends on the fixture' in awkup['writeback_dependency'],
            'the P3_AWKUP projection must record the three walker FLT stores (w8 4/5/5 are real '
            'executed values), the P_IE consume write-back 0x4 (fixture 0 | 1<<2), the fourteen '
            'bit RMWs (EN/P_CPND/N_CPND |=1<<ch three times each, item[0] N_IE&=~4, items[1]/[2] '
            'P_IE/N_IE |=1<<ch) plus the three W1[0] clear-helper RMWs (0x60685ae: EN&=~4 with '
            'bit2 already clear keeps known_value 3, P_CPND/N_CPND |=4), and exactly one served '
            'first read of P3_AWKUP_P_IE')
    p3ls=r['peripherals']['p3_ls_projection']
    require(p3ls['stores']=={'0xf2c290':{'values':['0x0'],'writes':1},
                             '0xf2c340':{'values':['0x0'],'writes':1},
                             '0xf2c344':{'values':['0x0','0x0'],'writes':2},
                             '0xf2c348':{'values':['0x0'],'writes':1},
                             '0xf2c34c':{'values':['0x0'],'writes':1},
                             '0xf2c350':{'values':['0x0','0x0'],'writes':2},
                             '0xf2c354':{'values':['0x0','0x0'],'writes':2}} and
            p3ls['word_writes']==10 and
            p3ls['write_only'] and p3ls['readback_not_modeled'] and
            p3ls['addresses']==[hex(a) for a in (0xf2c290,0xf2c340,0xf2c344,0xf2c348,
                                                 0xf2c34c,0xf2c350,0xf2c354)] and
            'NOT 0xF2A638' in p3ls['observed_shapes'],
            'the P3_LS projection must record exactly the ten executed zero stores of the walker '
            'tail: the pair-half clear of P3_LS_P11 at 0xf2c290, the eight straight st.w clears '
            'of P3_LS_IO_DLY..PLL (with the two 0xf2c344/0xf2c354 repeats), and the second pair '
            'half 0x6067d62 landing on OLD r0 -> [0xf2c350] (third PLL clear, not 0xf2a638 — '
            'route AZ\'s sequential-semantics reading corrected by route BD: 0xf2a638 has zero '
            'app+ROM references)')
    timer=r['peripherals']['timer0']
    # 路 CG 精确形状:Timer0 持续在延时函数的 OS tick 上运转(每次 call 重编
    # PRD;停点前最后一段是 3ms 窗 PRD=35999),到期合计 296(10 legacy+286
    # 交付/ACK);停点时刻 cnt=31,179、pending False(最后一次 handler W1C
    # 清掉)。路 CL:mode 切换函数 0x6068152 体 +160 步真实执行(纯软件倒计数
    # 延时无 SFR、不重编 PRD),cnt 再 +480(=160×3 tick)到 31,659。路 CO:
    # masked 分支放行后 scheduler helper 再 +170 步(=169 模型步+停点步),cnt
    # 再 +510(=170×3 tick)到 32,169;无新到期(expirations 仍 296)。路 CQ:
    # st.w.sp.l 放行后 +426 步(helper 尾段+W1[1] walker 后续),cnt 再
    # +1278(=426×3 tick)到 33,447;仍无新到期。路 F:jl_uart_con_projection 放行
    # uart_is_idle(0) 读后 uart_init 真实执行 +604 步,cnt 再 +1812(=604×3 tick)
    # 到 35,259;仍无新到期(expirations 仍 296)。路 M:白名单放行 CON1/CON2 两写
    # 后 +252 步,cnt 再 +756(=252×3 tick)——35,259+756=36,015 越过 36,000 周期
    # 边界:**第 297 次到期在停点时刻挂起 pending=True、cnt 重装到 15**(停点在
    # GPIO 写上、交付往返未发生,与 286 次已交付/ACK 的到期不同——首次「到期
    # 在途」状态,下一路放行 GPIO 后由资格链决定交付)。
    # 路 R:放行 0xfd58b8 后 +1,166 步,cnt 15+1,166×3=3,513(<36,000 无新到期,
    # expirations 仍 297);**在途的第 297 次到期已交付+ACK**(pending False)——
    # 临界区退出→bit9 恢复→资格链放行,交付只走既有 IRQ 资格判断,GPIO 写不
    # 参与交付(codex 风险点核验)。
    require(timer=={'con':0x1401,'cnt':3513,'prd':35999,'pending':False,'expirations':297},
            'TIMER0 must stay on source 5 with the delay-function tick schedule (each delay '
            'call reprograms PRD; the last window is the 3ms one -> PRD 35999), 297 '
            'expirations total (10 legacy + 287 delivered/acked), pending FALSE at the stop: '
            'the route-M stop left the 297th expiration pending-in-flight inside the GPIO '
            'critical section (ICFG 0x100, CLI bit9 clear), and the route-R release lets the '
            'chain run past the critical-section exit — the unlock restores bit9, the pending '
            'episode becomes eligible on the next advance() and the real handler W1C acks it '
            '(delivery #287, reti_from 0x12b7b6); the counter then walks 15+1166*3=3513 '
            'without reaching 36000, so no new expiration')
    # 路 CG:core tick 溢出交付+ACK 合计 9 次(首溢出~447,490+超时窗口内 8 次,
    # expirations 9/acknowledgements 9,cnt 重装后走到 892,276<prd 1,199,999,零
    # pending);IRQ3 handler 0x60661a4 的 W1C 真实执行,交付列表见上。CPU1
    # core tick 零溢出。路 CL:+160 步后 cnt 再 +640(=160×4 tick)到 892,916;
    # 路 CO:scheduler helper 再 +170 步(=169+停点步)、cnt 再 +680(=170×4 tick)
    # 到 893,596;停点进入 0x6065c3a 内 OS 临界区(0x60659ee→0x12b55c),IPMASK
    # 由 0 回到 6。路 CQ:st.w.sp.l 放行后 +426 步、cnt 再 +1704(=426×4 tick)
    # 到 895,300;停点 0x601d1ac 已退出临界区,IPMASK 回 0。路 F:uart_init
    # 真实执行 +604 步、cnt 再 +2416(=604×4 tick)到 897,716;IPMASK 仍 0。
    # 路 M:白名单放行 CON1/CON2 两写后 +252 步、cnt 再 +1008(=252×4 tick)到
    # 898,724;IPMASK 仍 0,无新溢出(交付/ACK 仍 9 次)。
    # 路 R:+1,166 步、cnt 再 +4,664(=1,166×4 tick)到 903,388;IPMASK 仍 0;
    # CPU0 partial ICFG 在新停点又是 0x100——新停点 0x6001342 同样位于 gpio_
    # set_fun_input_port 助手的 0x600133c 上锁临界区内(0x100↔0x300 随临界区
    # 进出翻转;两次停点恰好都停在锁内纯属序列形状,交付已在两锁之间的
    # 窗口发生)。
    tick0=r['peripherals']['core_tick_configuration'][0]
    require(tick0['pending'] is False and tick0['expirations']==9 and
            tick0['unresolved_overflow'] is False and tick0['acknowledgements']==9 and
            tick0['cnt']==903388 and
            tick0['irq_delivery_simulated'] and
            r['machine']['core_tick_entry_projection']['enabled'] and
            r['peripherals']['core_tick_configuration'][1]['expirations']==0 and
            r['peripherals']['ipmask']==0 and
            r['machine']['cores']['0']['partial_icfg']['known_value']=='0x100',
            'the CPU0 core tick must deliver and ack nine overflows (the ~447,490 landmark '
            'plus the eight of the timeout window; rearmed counter at 903388 < prd 1199999, '
            'no pending at the stop, +1166 route-R steps x 4 ticks); IPMASK=0 (the mux chain '
            'never re-enters the OS critical sections), the CPU1 core tick never overflows, '
            'and CPU0 partial ICFG is 0x100 at the NEW stop too: the 0x6001342 JL_IMAP write '
            'sits inside the gpio_set_fun_input_port helper lock (0x600133c -> 0x12b746) so '
            'CLI cleared bit9 again — bit9 flips 0x100<->0x300 with the critical sections '
            'and the Timer0 delivery already happened in the window between the two locks '
            '(known gates stay 0x300 on CPU1)')
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
    # LRC trim 结果块(0x129D88..0x129DEC):平均 [0x129D88]/计数字节(0x129D8C=10
    # (0x0a)、0x129D8E=1——路 CG:GPCNT 放行后的 9 次 OS tick 级联里 LRC 周期
    # 链推到第 10 帧,历史数组继续添 200000 帧)/第一个任务句柄半字 [0x129D94]=1
    # (0x6067aec h[0x129D94]=r0 已执行,任务创建 SDK 调用返回句柄 1;旧记录写成
    # 0x129D90 系地址笔误,本路按实际快照修正)/raw [0x129D98]/20 项历史数组
    # ——探路值的全部数值侧效应逐字节钉死(200000=0x00030D40),全窗 0x64 字节
    # 另以 SHA 钉死(窗口 >40 字节无逐字节串)。
    head=r['memory_snapshots']['lrc_trim_result_head']
    require(head['bytes_with_unknowns']=='400d0300'+'0a000100'+'00000000'+'01000000'+'400d0300'*6 and
            head['initialized_bytes']==40,
            'the trim-result head must pin avg 200000, counter bytes 10/1 (the nine route-CG '
            'OS-tick cascades advance the LRC period counter to the tenth frame), the '
            'first-task handle halfword 1 at 0x129D94, raw 200000 and the seeded history '
            'head of 200000')
    block=r['memory_snapshots']['lrc_trim_result_block']
    require(block['initialized_bytes']==0x64 and
            block['sha256']=='9d1a1a617df8bdf3f5ccf9576b14c521e5c8872292fb806bfdde56e0c5bf3c99',
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
    # |=1)= 9;路 CB +5(计数字节 1→2 那轮)→14;路 CG 的 9 次 OS tick 级联
    # 里 LRC 周期链每轮再走 4 条位 RMW(14→54),计数字节推到 10、trim 读 4→20
    # /fixture 读 2→10;lrc_trim_result_simulated 因显式探路值翻 True(报告
    # 语义如实)。
    require(lp['lrct_con']=={'address':'0xfd3600','known_mask':'0x7f','known_value':'0x41',
                             'rmw_writes':54} and
            lp['write_only'] and not lp['readback_observed'] and
            not lp['bit_semantics_or_completion_verified'] and lp['lrc_trim_result_simulated'],
            'the JL_LRCT bit records or the explicit assumption flags differ')
    trim=lp['lrct_trim_done_projection']
    require(trim['address']=='0xfd3600' and trim['trim_done_bit']=='0x80' and
            trim['reads']==20 and trim['projected_word']=='0xc1' and trim['known_mask']=='0x7f' and
            not trim['completion_timing_modeled'] and
            trim['trim_result_register']['address']=='0xfd3604',
            'the trim-done readback projection differs from the observed poll reads '
            '(0x6067514/0x606751a plus the eighteen reads of the route-CB/CG LRC-period rounds)')
    fixture=lp['lrct_trim_result_fixture']
    require(fixture['address']=='0xfd3604' and fixture['value']=='0x9600' and
            fixture['reads']==10 and fixture['value_is_derived_not_measured'] and
            '7,680,000,000/38,400 = 200,000' in fixture['derivation'] and
            'not a silicon measurement' in fixture['derivation'],
            'the trim-result probe fixture record must pin 38400, its ten served reads (the '
            'route-BY read plus the nine LRC-period rounds through the route-CG stop) and the '
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
            [(0,127,'0x12ad2a','0x12b608')]*2+
            [(1,125,'0x60662c8','0x12c5ca')],
            'the pmu_task creation must round-trip the scheduler with two more CPU0 irq127 '
            'deliveries (the trim-task creation at 0x6067ae0 adds none: its path stops inside '
            'the timer bring-up before any scheduler IPI); the route-CO scheduler helper prefix '
            'adds one more CPU1 irq125 sync-IPI delivery at the tail')
    # 路 BY 注:pmu_task 入口的 RTI 事件(pc_after 0x6068f0c/sp 0x401ffc,
    # ~300k 步)在路 BY 分歧点(首个 caller A 的轮询退出,~305k)之前——该
    # 前缀与 689,402 地标世界逐字节相同,证据钉在路 BS 检查里。本场景的
    # events 窗口(前 32+后 32,防 RAM/代码镜像的裁剪)现在装着 14 次 IRQ4
    # 往返的尾部事件,中段的 software_irq_rti 事件滑出窗口:改钉不裁剪的
    # 持久记录(交付列表已钉 + RTI 适配器计数 10)与裁剪标志本身。路 CO 的
    # 新增 irq125 是 CPU1 同步 IPI(handler 0x60662c8、repeat 投影),不新增
    # CPU0 irq127 RTI 往返,conditional_software_irq_rti 仍 10。路 CQ:st.w.sp.l
    # 放行后 helper 尾段+W1[1] walker 后续 +426 步,路 CO 停点时 CPU1 最后一次
    # irq125 交付的 handler RTI 尚未执行完,本路补完——conditional_software_
    # irq_rti 10→11(交付列表仍 11 次不变,software_irq_commands 22/11 不变)。
    require(r['machine']['events_truncated'] is True and
            r['instruction_adapters'].get('conditional_software_irq_rti')==11,
            'the software IRQ RTI count must reach eleven (the pmu_task-entry RTI event lies '
            'in the byte-identical pre-305k prefix pinned at the 689,402 landmark; the trimmed '
            '32+32 events window holds the fourteen IRQ4 round trips; the route-CO CPU1 sync '
            'IPI is a repeat_irq_delivery round whose handler RTI was still in flight at the '
            'route-CO stop and completes during the route-CQ helper-tail/W1[1]-walker steps, '
            'with no new software IRQ command bookkeeping)')
    require(r['peripherals']['software_irq_commands']['commands']==22 and
            r['peripherals']['software_irq_commands']['acknowledgements']==11,
            'the two scheduler IPI round trips plus the route-CO scheduler-helper sync IPI '
            'must be the only new software IRQ bookkeeping')
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
    # 100→103(路 AQ +2)→116(路 CB +3:L5V/CHG 配置表的字节装载 pair)→140
    # (路 CG +24:超时窗口 20 轮 delay 的 L5V/CHG 配置字节 pair 与 W1[0] 循环
    # 簿记)、LRCT 位 RMW 14→54(路 CG:LRC 周期链 9 轮)、reset 配置 RMW 8 不变。
    require(r['instruction_adapters'].get('exact_doubleword_store')==27 and
            r['instruction_adapters'].get('immediate_rep_multi_block_projection')==165 and
            r['instruction_adapters'].get('zero_count_skip_is_assumption')==3 and
            r['instruction_adapters'].get('register_rep_multi_block_projection')==26 and
            r['instruction_adapters'].get('rep_copy_unknown_transport_load')==9 and
            r['instruction_adapters'].get('rep_copy_unknown_transport_store')==9 and
            r['instruction_adapters'].get('parallel_halfword_store_old_registers')==4 and
            r['instruction_adapters'].get('parallel_insert_load_old_registers_is_assumption')==8 and
            r['instruction_adapters'].get('parallel_byte_store_old_registers')==143 and
            r['instruction_adapters'].get('lp_timer_bit_rmw')==54 and
            r['instruction_adapters'].get('reset_unknown_preserving_configuration_rmw')==8 and
            r['instruction_adapters'].get('mem_pwr_con_rmw')==4 and
            r['instruction_adapters'].get('p33_awkup_bit_rmw')==17 and
            r['instruction_adapters'].get('proven_dead_mem_pwr_con_read')==1 and
            # 路 CG 新前缀(W1[0] 循环2 全 20 轮超时窗口+9 次 OS tick 级联+286
            # 次 IRQ4 交付+GPCNT 任务 9 次调度往返):任务栈帧运输 3987→10697、
            # 共享锁 580→1221、整字存储 5775→14122、mmio RMW 563→1482、icfg
            # bit9 1600→3724、cnum 2361→5770(OS tick/调度器/中断往返的真实执行);
            # 路 CL:mode 切换函数 0x6068152 体 +160 步真实执行(DCCK 位域配置+
            # WLA_CON8/P3_ANA_CON0/7/8/9/10 配置+软件延时+临界区退出)与 W1[1]
            # scheduler helper 帧——并行字节 140→141、任务栈帧运输 10697→10703、
            # 共享锁 1221→1222、整字存储 14122→14144、mmio RMW 1482→1492、icfg
            # bit9 3724→3726、cnum 5770→5772。路 CO:scheduler helper 判定前置
            # 段真实执行(masked 分支 1 次、0x12cb08 临界查询、0x12b55c 临界区
            # 进出 cli/sti、0x60659ee/0x6065ab8 包装)——新增 partial_icfg_masked_
            # branch=1,任务栈帧运输 10703→10706、共享锁 1222→1223、整字存储
            # 14144→14150、icfg bit9 3726→3730、cnum 5772→5781(mmio RMW 1492 不变)。
            # 路 CQ:+425 步(scheduler helper 0x6065c3a 尾段 + W1[1] walker 后续到
            # 0x601d1ac)——任务栈帧运输 10706→10721、共享锁 1223→1224、整字存储
            # 14150→14168(6 个 st.w.sp.l 转 exact +425 步内新整字存储)、icfg bit9
            # 3730→3734、cnum 5781→5788(mmio RMW 1492 与 masked 分支 1 不变)。
            # 路 F:jl_uart_con_projection 放行 uart_is_idle(0) 读后 uart_init 真实
            # 执行 +604 步(CON0 写/|0x3 RMW/gpio 配置/互斥量+信号量创建/context
            # 填充/并行对字节存储等)——任务栈帧运输 10721→10739、共享锁 1224→
            # 1226、整字存储 14168→14192、mmio RMW 1492→1496、icfg bit9 3734→
            # 3746、cnum 5788→5804(半字 pair 3→4、字节 pair 141→143、双字 26→27)。
            # 路 M:jl_uart_init_write_records 放行 CON1/CON2 两写后 uart_init 继续
            # +252 步(CON2 写、tx/rx 引脚判定、gpio_set_mode 进入引脚复用链)——
            # 任务栈帧运输 10739→10753、整字存储 14192→14202、icfg bit9 3746→
            # 3751、cnum 5804→5809(共享锁 1226、mmio RMW 1496、masked 分支 1、
            # 半字 pair 4、字节 pair 143、双字 27 全不变——新前缀无新临界区/
            # MMIO RMW/并行对存储)。
            # 路 R(sim-gpioface):jl_omap_write_records 放行 0xfd58b8 写后
            # +1,166 步(TX 复用链完成+第 297 次到期的交付往返+RX 复用链到
            # JL_IMAP 停点)——任务栈帧运输 10753→10805、共享锁 1226→1228
            # (TX/RX 复用各一组 0x12b746/0x12b772)、整字存储 14202→14254、
            # mmio RMW 1496→1497(RX 复用的 0xfd6000 RMW)、icfg bit9 3751→
            # 3769(临界区进出+交付往返)、cnum 5809→5832(masked 分支 1、
            # 半字 pair 4、字节 pair 143、双字 27 全不变)。
            r['instruction_adapters'].get('abstract_task_stack_transport')==10805 and
            r['instruction_adapters'].get('cpu0_shared_lock_ownership')==1228 and
            r['instruction_adapters'].get('exact_full_word_store')==14254 and
            r['instruction_adapters'].get('mmio_rmw_always_writes')==1497 and
            r['instruction_adapters'].get('partial_icfg_bit9_only')==3769 and
            r['instruction_adapters'].get('partial_icfg_masked_branch')==1 and
            r['instruction_adapters'].get('per_core_visible_cnum')==5832,
            'the heap-experimental prefix adapters differ from the observed conditional frontier')
    # 路 BE:Timer0 IRQ4 交付/惰性两分支探针(探路 fixture 只拉近到期计数,
    # 交付分支不注入任何资格 fixture——运行态 trace 已证实前缀里驱动安装
    # 循环在 ~110,768 步把 IRQ4 以 priority 1 真实使能且此后无清零)。
    d=rows['timer0_irq4_delivery_probe']
    require(d['completed_steps']==304857 and d['stop_core']==0 and d['stop_pc']=='0x60116e6' and
            d['stop']=={'kind':'fd31_adc_con_readback_not_modeled','address':'0xfd3100'},
            'the Timer0 IRQ4 delivery probe must return to the SAME honest fd31-ADC-readback '
            'frontier after the full delivery round trip (+540 steps of round-trip overhead '
            'over the 304,317 baseline)')
    deliveries=d['machine']['timer0_irq_entry_projection']['deliveries']
    require(len(deliveries)==1 and
            (deliveries[0]['core'],deliveries[0]['irq'],deliveries[0]['vector_address'],
             deliveries[0]['handler'],deliveries[0]['reti_from'])==(0,4,'0x19fe10','0x12ece8','0x12b608') and
            not deliveries[0]['delivery_clears_pending'] and
            deliveries[0]['pending_cleared_by_handler_ack'],
            'the Timer0 IRQ4 delivery must read the REAL vector slot 0x19fe10 (app-written '
            '0x12ece8) and interrupt the scheduler wait 0x12b608 without clearing pending')
    rti=[e for e in d['machine']['events'] if e.get('kind')=='timer0_irq_rti']
    require(len(rti)==1 and rti[0]['pc_after']=='0x12b608' and rti[0]['sp_after']=='0x400b38' and
            rti[0]['reti_from_frame']=='0x12b608' and rti[0]['bank_writeback_is_assumption'],
            'the symmetric one-shot RTI must return to the interrupted scheduler wait with '
            'SP=USP from the really popped frame')
    require(d['peripherals']['timer0']['pending'] is False and
            d['peripherals']['timer0']['expirations']==11,
            'the real handler W1C (rmw.or.modimm [0xfd0400] |= 0x4000, the 0xd401 RMW word '
            'with the pending bit15 readback visible) must clear pending and leave exactly '
            'the probe expiry counted')
    require(d['instruction_adapters'].get('timer0_irq_handler_frame')==2,
            'the handler must execute both 12B special-frame anchors (push.srl at 0x12ece8, '
            'pop.srl at 0x12ed08) with the 16B push.rl/pop.rl frame on the ordinary transport')
    m=rows['timer0_irq4_masked_probe']
    # 路 BY 注:主场景(p11_heap)在路 BY 后停在 447,383(P3_ANA_READ,折叠
    # 世界);masked probe 不开 fd31 读回/PND/fixture——它保持 304,317 的
    # CON 读回诚实停(与 delivery_probe 的 304,857 同族);停点按字面值钉,
    # 不引用主场景。
    require(m['completed_steps']==304317 and
            m['stop']=={'kind':'fd31_adc_con_readback_not_modeled','address':'0xfd3100'} and
            m['stop_pc']=='0x60116e6' and
            m['machine']['timer0_irq_entry_projection']['deliveries']==[] and
            m['peripherals']['timer0']['pending'] is True and
            m['peripherals']['timer0']['expirations']==11 and
            m['peripherals']['assumptions']['timer0_irq4_probe']['irq4_nibble_cleared_counterfactual'],
            'the masked counterfactual (IRQ4 nibble cleared) must stay inert: the expiry only '
            'sets pending, nothing is requested or delivered, and the run keeps the exact '
            'honest fd31-ADC-CON-readback frontier at 304317 (the probe scenarios do not opt '
            'into the route-BN readback projection)')
    report['timer0_irq4_delivery_projection']=(
        'route BE: the Timer0 IRQ4 delivery machinery is in place before the ~341,097-step '
        'first-expiry hard deadline. The eligibility chain is UNCHANGED (per-core ICFG gates '
        '8/9 + IRQ_CONFIG word0 nibble + priority vs IPMASK); a route-BE write trace resolved '
        'the open eligibility question: the driver-install request_irq loop enables IRQ4 '
        '(nibble 0x3, priority 1, maskable) at ~step 110768 (0xffe100=0x30000, word later '
        '0x33b55, no clear through the frontier), so the first real STD12M expiry WILL be '
        'eligible and deliver. The model raises IrqDeliveryRequest(core,irq=4) at most once '
        'per pending episode (delivery_in_flight, cleared by the real handler W1C), the '
        'machine reads the real vector slot 0x19fe10 (handler 0x12ece8, the QCY Timer ISR '
        'table entry 0, 28B software frame, NOT the SDK 76B hwi.S veneer), and the probe '
        'scenario executes the REAL handler bytes end to end: critical enter/exit (the '
        'IPMASK 6/0 pairs), the W1C ack whose RMW word 0xd401 shows the pending bit15 '
        'readback, the empty callback dispatch (callback table 0x126f6c slot 0 is zero in '
        'this prefix), both frame anchors, csync, and the symmetric RTI tail 0x12ed0c back '
        'to the interrupted scheduler wait. The expiry counter seed (200 model steps after '
        'the real STD12M start write) is an explicitly labeled synthetic probe fixture; the '
        'entry banks, entry PSR high bits, RETI load, bank writeback and ICFG effects all '
        'remain UNCERTIFIED counterfactual assumptions (hardware_mode_certified false), and '
        'the event clock stays disabled (route BI). The masked probe clears the IRQ4 nibble '
        'as a counterfactual "not installed" world and pins the inert branch on the same '
        'prefix: pending set, no request, no stop, identical honest frontier')
    # 路 BF:CPU0 core tick(IRQ3)交付/惰性两分支探针(在 ~447,490 步硬期限前把
    # 交付机制接进主条件场景栈)。资格链无新 fixture——tick_timer_init 的
    # request_irq 在启动写之前就把向量槽 0x19fe0c=0x60661a4 与 IRQ3 nibble
    # (priority 1)写好;溢出点 seed 到前沿前 ~800 步(启动写后 156,400 模型
    # 步,显式合成假设)。溢出瞬间实测落在临界区内(IPMASK=6):本路把模型
    # 对齐 Timer0 先例——pending 期间每步重检资格,退出临界区即交付。
    d=rows['core_tick_irq3_delivery_probe']
    require(d['completed_steps']==305180 and d['stop_core']==0 and d['stop_pc']=='0x60116e6' and
            d['stop']=={'kind':'fd31_adc_con_readback_not_modeled','address':'0xfd3100'},
            'the core tick IRQ3 delivery probe must return to the SAME honest fd31-ADC-readback '
            'frontier after the full delivery round trip (+863 steps of round-trip overhead '
            'over the 304,317 baseline)')
    deliveries=d['machine']['core_tick_entry_projection']['deliveries']
    require(len(deliveries)==1 and
            (deliveries[0]['core'],deliveries[0]['irq'],deliveries[0]['vector_address'],
             deliveries[0]['handler'],deliveries[0]['reti_from'])==(0,3,'0x19fe0c','0x60661a4','0x12b608') and
            not deliveries[0]['delivery_clears_pending'] and
            deliveries[0]['pending_cleared_by_handler_ack'],
            'the core tick IRQ3 delivery must read the REAL vector slot 0x19fe0c (firmware-'
            'registered handler 0x60661a4) and interrupt the scheduler wait 0x12b608 without '
            'clearing pending')
    rti=[e for e in d['machine']['events'] if e.get('kind')=='core_tick_rti']
    require(len(rti)==1 and rti[0]['pc_after']=='0x12b608' and rti[0]['sp_after']=='0x400b88' and
            rti[0]['reti_from_frame']=='0x12b608' and rti[0]['bank_writeback_is_assumption'],
            'the symmetric one-shot RTI must return to the interrupted scheduler wait with '
            'SP=USP from the really popped frame')
    tick=d['peripherals']['core_tick_configuration'][0]
    require(tick['pending'] is False and tick['expirations']==1 and
            tick['acknowledgements']==1 and tick['irq_delivery_simulated'],
            'the real handler ACK (rmw.or.modimm [0xffe0ec] |= 0x40) must clear pending '
            'exactly once and the counter must resume after it')
    require(d['peripherals']['core_tick_configuration'][1]['expirations']==0,
            "CPU1's tick (PRD=479999999) must never overflow in this prefix")
    require(d['instruction_adapters'].get('core_tick_handler_frame')==2,
            'the handler must execute both 12B special-frame anchors (push.srl at 0x60661a4, '
            'pop.srl at 0x60661e2) with the 16B push.rl/pop.rl frame on the ordinary transport')
    m=rows['core_tick_irq3_masked_probe']
    require(m['completed_steps']==304317 and
            m['stop']=={'kind': 'fd31_adc_con_readback_not_modeled', 'address': '0xfd3100'} and
            m['stop_pc']=='0x60116e6' and
            m['machine']['core_tick_entry_projection']['deliveries']==[] and
            m['peripherals']['core_tick_configuration'][0]['pending'] is True and
            m['peripherals']['core_tick_configuration'][0]['expirations']==1 and
            m['peripherals']['assumptions']['core_tick_irq3_probe']['irq3_nibble_cleared_counterfactual'],
            'the masked counterfactual (IRQ3 nibble cleared) must stay inert: the overflow '
            'only sets pending, nothing is requested or delivered, and the run keeps the '
            'pre-ADC-readback honest frontier (304,317, pinned as a literal because the '
            'base scenario itself advanced past the overflow to the 689,402 RES boundary '
            'with the ADC readback and route-BS delivery wiring on)')
    # ——路 S/sim-keychain:按键全链直调场景断言——
    k=rows['key_chain_scan_direct']
    require(k['stop_core']==0 and k['stop_pc']=='0x60012de' and
            k['stop']['kind']=='unmapped_or_readonly_write' and k['stop'].get('address')=='0xfd58b8',
            'the key-chain scenario must keep the exact route-M frontier world '
            '(3,072,236 @ 0x60012de, UART-pin GPIO face) before the direct calls')
    kc=k['key_chain_direct_scan']
    require(kc['read_key_before_registration']==255 and kc['read_key_after_registration_idle']==255,
            'read_key 0x6000E94 must return 255 (no key) both before registration (queue miss '
            'returns 1, 1<=500 -> key-id table[0]=255) and after it with the initial value=1')
    require(all(s['sampled_value']==s['res_fixture'] for s in kc['res_fixture_switches']) and
            len(kc['res_fixture_switches'])==7,
            'all seven real sampling transactions (0x60116D0: setter mux-field write + CON '
            'write + PND poll projection + RES fixture read + clear) must return exactly the '
            'fixture divider level')
    dc=kc['double_click']
    require(dc['click_count_after_first_release']==1 and
            dc['click_count_after_second_release']==0 and
            dc['tracked_key_after_sequence']==255 and
            [p['payload'] for p in dc['post_calls']]==[hex(0x10000|(10<<8)|4)],
            'double click: click count 1 after the first release edge, consumed+cleared by the '
            '20-beat release-stable terminator (27 path), tracked key back to 255, and exactly '
            'one post 0x10a04 = 0x10000|(key 10<<8)|gesture 4 (click count 2+2 — the n+2 '
            'multi-click encoding, field-by-field cross-verified with the route-BW synthetic '
            'event word)')
    require([p['payload'] for p in kc['long_press']['post_calls']]==[hex(0x10000|(10<<8)|1)],
            'long press: exactly one post with gesture 1 (press confirmed) at the 75-beat '
            'press threshold (the 15-beat HOLD-repeat tier at 90 needs 90 beats; 78 stay below)')
    require([p['payload'] for p in kc['single_click']['post_calls']]==[hex(0x10000|(10<<8)|0)],
            'single short click: exactly one post with gesture 0 (single click n==1 skips the '
            'n+2 encoding — correcting route Q\'s vague "single=1" note)')
    beats=[b for b in kc['beat_states'] if 'sequence_reset' not in b]
    require(len(beats)==kc['scan_beats_total'] and
            all('change_count' in b for b in beats),
            'every scan beat must carry the real debounce state read back from ctx RAM')
    press=[b for b in beats[:13]]
    require(beats[0]['change_count']==0 and beats[2]['change_count']==2 and
            beats[3]['press_count']==1 and beats[12]['press_count']==10,
            'debounce must advance per beat by real code: change window ctx+9=2, press-edge '
            'beat sets press_count=1, +1 per stable beat')
    report['key_chain_direct_scan_projection']=(
        'route S/sim-keychain: opt-in scenario key_chain_scan_direct (default OFF keeps every '
        'legacy report byte-identical). After the route-M frontier world (3,072,236 @ '
        '0x60012de) a post_stop callback drives the LIVE machine: a synthetic config-523 '
        'sensor object (0x1274F4, parser-product layout; pointer slot 0x125320 — route Q\'s '
        '0x125720 was a decimal-transcription slip corrected here) is installed, the key '
        'channel 0x10001 (mux field 1, unused by the BY three) is REALLY registered by '
        'direct-calling adc_add_sample_ch 0x6001230 (slot 3), each press/release phase REALLY '
        'runs the full sampling transaction 0x60116D0 (setter P3_ANA_CON4 mux write + CON '
        'write + PND-poll projection + the RES named fixture read + clear; 127 model steps, '
        'return value exactly the divider level), the queue item value is written to the EMA '
        'steady state (explicit assumption: the 0.2/0.8 first-order convergence steady state '
        'equals the constant RES level; the transient lives in the IRQ39 handler whose tail '
        'rti forbids task-context direct calls), and the scan callback 0x6001944 is direct-'
        'called once per beat with the registry entry pointer (its sys_timer arg, route-Q '
        'pinned) after advancing the 0x124C80-struct tick by one per beat (one beat = one '
        '10ms tick, explicit assumption). EVERYTHING from the divider query through debounce, '
        'the gesture state machine and the post call is real firmware code; the ONLY '
        'synthetic input is the RES level. Observed: double click posts 0x10a04 (gesture 4 = '
        'click-count 2 + 2 — field-by-field matching the route-BW synthetic event word '
        '0x10000|(10<<8)|4, so BW\'s injected payload now has producer-side runtime '
        'evidence), long press posts 0x10a01 (gesture 1 at the 75-beat threshold), single '
        'click posts 0x10a00 (gesture 0 — single click skips the n+2 encoding; correcting '
        'route Q\'s vague single=1). Two more route-Q address slips corrected by instruction '
        'immediates: the shared 1199232 base is 0x124C80 (not 0x124B30; tracked key +0, ADC '
        'scan index +5, tick counter +36) and the 1205696 base is 0x1265C0 (not 0x1265A0; '
        'click count +26 = 0x1265DA). HONEST BOUNDARIES: the S19 dispatch and handler '
        '0x60252c6 are NOT reached (the app_core message-loop consumer is outside the stop '
        'world; that belongs to the D1 event-loop scenarios) — but route BW already executed '
        'that handler from its entry with the field-matching payload, closing the loop from '
        'both ends; the post enqueue return/landing is observed at the call site, not the '
        'ring-buffer bytes; interrupts are FROZEN during the direct calls (no hardware.'
        'advance: the pending Timer0 IRQ4 / core-tick IRQ3 at the stop are held, not '
        'dropped); every conclusion stays conditional pass / derived-not-measured')
    report['core_tick_irq3_delivery_projection']=(
        'route BF: the CPU0 core tick (IRQ3) delivery machinery is wired into the main '
        'conditional scenario stack before the ~447,490-step first-overflow hard deadline '
        '(cnt 625452 / prd 1199999 at 304,317). Route-BF disassembly pinned the handler '
        'chain: tick_timer_init 0x60661e8 stops the tick, registers via request_irq '
        '(0x6065d86, irq=3 priority=1 handler=0x60661a4 — writing the REAL vector slot '
        '0x19fe0c and IRQ_CONFIG word0 bits12-15 = 0x3), then writes CNT=0/PRD and starts '
        '(CON=1 at 0x606622e); the handler 0x60661a4 is a 28B software frame like the '
        'Timer0 one (push.srl {psr,rets,reti} + push.rl {r3-r0}), ACKs with the real '
        'rmw.or.modimm [0xffe0ec+(cnum<<11)] |= 0x40 that CoreTickStopped.ack_rmw models, '
        'on CPU0 also bumps the software tick counter [0x125004] and calls the timeslice '
        'scheduler, pops both frames, csync, rti at the exact tail 0x60661e6. One real '
        'model fix surfaced by the probe: eligibility was previously judged ONLY at the '
        'overflow instant, so an overflow inside a critical section (the seeded overflow '
        'measurably lands at IPMASK=6) was silently missed forever; route BF aligns it '
        'with the Timer0 precedent — while pending, every step re-checks the UNCHANGED '
        'eligibility chain (per-core ICFG gates 8/9 + nibble enable + priority vs IPMASK) '
        'and raises IrqDeliveryRequest(core,irq=3) when it passes (delivery_in_flight '
        'until the ACK). The probe delivery round trip runs entirely on real state '
        '(scheduler-wait interrupt 0x12b608, real handler bytes, ACK clearing pending '
        'exactly once, frame anchors, csync, RTI back to 0x12b608/SP=USP 0x400b88) and '
        'then returns to the SAME honest fd31-ADC-readback frontier at 305,180 (+863 '
        'round-trip steps). The expiry counter seed (156,400 model steps after the real '
        'start write) is an explicitly labeled synthetic probe fixture; the entry banks, '
        'entry PSR high bits, RETI load, bank writeback and ICFG effects all remain '
        'UNCERTIFIED counterfactual assumptions (hardware_mode_certified false), and the '
        'event clock stays disabled (route BI). The masked probe clears the IRQ3 nibble '
        'as a counterfactual "not installed" world and pins the inert branch on the same '
        'prefix: pending set, no request, no stop, identical honest frontier')
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
    report['p33_awkup_first_read_fixture_assumption']=(
        'the p11_heap_experimental_config scenario opts into p33_awkup_projection: the P3_AWKUP '
        'family (SDK p33_sfr.h names P3_AWKUP_EN/P_IE/N_IE/LEVEL/INSEL/P_PND/N_PND/P_CPND/N_CPND '
        '= P33_ACCESS(0x60..0x68) and P3_AWKUP_FLT0..2 = P33_ACCESS(0x80..0x82), address macros '
        'only, no __RW/__RO/__WO annotations, no bit fields) is modeled as RECORDS-WRITE-ACCESSES '
        'ONLY: FLT0..2 and P3_AWKUP_P_IE full-word stores are recorded as executed values, and '
        'EN/P_IE/N_IE/P_CPND/N_CPND bit RMWs are recorded as PartialWord bit operations without '
        'readback (same family as mem_pwr_con). NO default read value exists for any register: '
        'the ONLY read semantics is the explicitly opted-in named fixture '
        'P33_AWKUP_P_IE_FIRST_READ_ZERO = {0xf2c184: 0}, a SYNTHETIC ZERO WORD with NO direct '
        'reset-value evidence, covering the FIRST read of P3_AWKUP_P_IE alone (never extended to '
        'N_IE or the rest of the family; the whole word 0 covers unknown bits and high bits and '
        'is not a claim that reset leaves all IE bits off). The boot prefix reads P3_AWKUP_P_IE '
        'exactly once: walker second-round item[0] (cfg=0xE2, channel 2, w4=0, source-item '
        'snapshot at 0x124A5C pins w4/w8 for all three items) executes ld.w r2=[0xF2C184] at '
        '0x6067cac and consumes it into the write-back W=R|(1<<2) at 0x6067cb0 — bit2 is forced '
        'by firmware, bits {0,1,3..31} (M&~K) carry the fixture word, so the recorded P_IE '
        'write-back 0x4 and every later recorded P_IE bit above bit2 DEPEND ON THIS ASSUMPTION. '
        'A first read is NOT an all-time readback: any repeat read, any post-write read and any '
        'read of an uncovered register strictly stops (awkup_repeat_read_not_modeled / '
        'awkup_readback_after_write_not_modeled / awkup_first_read_fixture_absent); no ordinary '
        'storage-style readback is assumed. LEVEL/INSEL have zero app+ROM references and are not '
        'modeled; P_PND/N_PND stay in the reset-history projection (POR/no-wake explicit '
        'synthetic 0). No CPND clear mechanism is inferred from the register name. With the '
        'family let through, the three walker sub-path items run fully (FLT2=4/FLT1=5/FLT0=5 '
        'real executed w8 values, 14 bit RMWs, one served fixture read) and the conditional '
        'frontier advances 159 steps to 304,012 at 0x6067d48: the walker-tail P3_LS cleanup '
        'opens with the parallel pair add.s14.rri.p r0+=176 || st.w [r0]=0 whose store half '
        'carries the OLD r0 (FM-1 run3 captured direction), so the first clear lands on '
        '[0xF2C290]=P3_LS_P11 (SDK-named P33_ACCESS(0xa4)) and only the following eight stores '
        'hit 0xf2c340..354 — a correction to route AZ\'s static reading that placed all nine '
        'clears in 0xf2c340..354; the P3_LS family, 0xf2a638=0 and [0xf2a628]&=0xFFFFFF7F were '
        'let through by route BD (see p3_ls_projection_assumption / p11_syscon1_mask_set_assumption)')
    report['p3_ls_projection_assumption']=(
        'opt-in p3_ls_projection (route BD): write-only word-sequence record of P3_LS_P11 '
        '(0xf2c290 = P33_ACCESS(0xa4)) and P3_LS_IO_DLY/IO_ROM/ADC/AUDIO/RF/PLL (0xf2c340..354 '
        '= P33_ACCESS(0xd0..0xd5)) — a simulator capability, NOT a register WO-property claim. '
        'Evidence: p33_sfr.h carries address macros only (no __RW/__RO/__WO, no bit fields); '
        'the full app+ROM reference scan finds three writer groups (walker tail 0x6067d40, '
        'variant clear 0x6069cb0, low-power 0x60c66f8) and ZERO readback observations, so '
        'stores record real executed values (0/0/1,3) and every read strictly stops '
        '(p3_ls_readback_not_modeled) while RMW is rejected (p3_ls_rmw_not_modeled, no observed '
        'shape). The walker tail runs ten zero stores: the pair half 0x6067d48 on OLD r0 -> '
        '[0xF2C290], eight straight st.w -> [0xF2C340..354] (two repeats), and the second pair '
        'half 0x6067d62 on OLD r0+16 -> [0xF2C350] (third PLL clear). CORRECTION to route AZ: '
        '"[0xf2a638]=0, unique write point 0x6067d62" was a sequential-semantics static reading; '
        'under the run3 old-register direction (which this repo implements and which produced the '
        '304,012 stop) that store half hits [0xF2C350], and 0xf2a638 has ZERO app+ROM references '
        '(literal scan + every reachable base+offset shape disassembled) — it is not modeled at '
        'all (also unnamed: offset 14 sits past the 12-word P11_SYSTEM_TypeDef). No '
        'level-shifter electrical effect is modeled.')
    report['p11_syscon1_mask_set_assumption']=(
        'opt-in p11_syscon1_mask_sets (route BD): the reset-history configuration gate accepts a '
        'second AND mask for P11_SYS_CON1 (0xf2a628). The walker tail computes r0=0xF2A628 via '
        'the pair 0x6067d5e add.s14.rri.p r0+=-7448 (simm14 of raw field 8936) and clears bit7 '
        'with the composite RMW 0x6067d64 rmw.and.nmodimm [r0]&=0xFFFFFF7F; the variant clear '
        'function uses the same mask at 0x6069cc6 (not in this prefix); the reset-init path '
        '0x6068c88 clears bit4 with 0xFFFFFFEF (the pre-existing RESET_CONFIG_MASKS entry). '
        'Firmware really uses two different masks on different bits of this register. SDK '
        'p11_sfr.h marks P11_SYS_CON1 __RW (macro value == volatile; the R/W split is comments '
        'only) with no bit-field definitions; the p11_app.h usage macro MSYS_IO_LATCH_DISABLE() '
        'is `P11_SYS_CON1 &= ~BIT(7)` — the macro NAME is a reference for bit7 only, no silicon '
        'semantics claimed. Each mask still clears bitwise (cleared bits known 0, kept bits '
        'unknown, readback requires a fully known word); the default single-mask validation is '
        'unchanged when the switch is off.')
    report['fd31_adc_con_projection_assumption']=(
        'opt-in fd31_adc_con_projection (route BG): write-only word-sequence record of '
        '0xfd3100 = JL_ADC->CON — a simulator capability, NOT a register WO-property claim '
        '(the reference br28.h marks CON __RW). NAMING CORRECTION to route BD: 0xfd3100 is '
        'not an unnamed "JL PMU/SFR power port"; the same reference header that anchored '
        'JL_LRCT (ls_base+map_adr(0x36,0)) names JL_ADC at ls_base+map_adr(0x31,0) with '
        '{__RW CON; __RO RES} (RES = 0xfd3104, a read-only conversion result), no bit-field '
        'definitions. VALUE CORRECTION to route BD: the control function 0x60115b4 writes '
        '0x20000 -> 0x20040 -> 0x40 (the first st.w.modimm raw 0xB00 is a rotated modimm '
        'expanding to 0x20000, not 0x200; the 304,036 stop bytes 00000200 are little-endian '
        '0x00020000). The full app+ROM reference scan finds exactly seven li.u32 0xfd3100 '
        'literal sites and zero 0xfd3104 literals; besides the pure-write control function '
        'the family has REAL readback consumers on the boot path: the 32-round sampling '
        'loop 0x6012148..0x601215e calls 0x60116d0 whose first fd31 access is the CON read '
        'at 0x60116e6 gating on bit4 (plus the busy poll helper 0x601163a waiting on bit7 '
        'and the RES read stored into a software slot for a (sum-min-max)/30 average), '
        'callers 0x60348e8/0x60349xx gate on bit7/bit4, and the setter 0x6011666 tail adds '
        'rmw.or |=0x40 past the frontier. CON status bits have no SDK definitions and RES '
        'is an analog measurement, so no readback value is invented: reads strictly stop '
        '(fd31_adc_con_readback_not_modeled), RMW is rejected (fd31_adc_con_rmw_not_modeled) '
        'and RES 0xfd3104 (never written by the firmware) stays unmodeled. The conditional '
        'frontier advances 281 steps to 304,317 at 0x60116e6 with ZERO adapter/CRC/'
        'cross-core changes (the 176-byte buffer memset runs its single-instruction REP '
        'bodies through the upstream native mechanism).')
    report['fd31_adc_con_readback_assumption']=(
        'opt-in fd31_adc_con_readback (route BN, layered on fd31_adc_con_projection): CON reads '
        'return the LAST FIRMWARE-WRITTEN VALUE, the lp_timer CON (route AL) / SFC clock +8 '
        '(0xfd1c08) precedent family. SDK evidence: the reference br28.h marks JL_ADC_TypeDef '
        'CON __RW and RES __RO, and that annotation is meaningful in this header (JL_LRCT CON '
        'is __WO while its NUM is __RW), so a CON read-back is defined; only after at least one '
        'firmware write, never a fabricated reset value. Consumer branch walk, disassembled and '
        'then EXECUTED: the 0x60116e6 gate reads 0x40 whose bit4=0, so the sampling-loop caller '
        '0x60116d0 skips the poll helper and control function, runs the RAM OS-post veneer '
        '0x12b55c (the same chain the VM-init prefix executed at 169,439) and the ADC setter '
        '0x6011666; with the sampling channel word 0x10000 (r0>>16=1) the setter tbb takes '
        'case 1 (0x6011698: r0=r0.l(u)=0) which CALLS 0x6067d8a — five read-modify-writes of '
        'P3_ANA_CON4 0xf2c250 (p33_sfr.h:134 named address macro, no type annotations; '
        'read-ld.w/st.w shape identical to route AM\'s P3_ANA_CON2/CON5) — then writes '
        '[0xfd3100]=0x46105d (table-byte b[0x124A90] low bits 0x5 | 0x1058 | 0x40000 | '
        '0x420000, all real instruction results) plus the compound |=0x40 (value unchanged, '
        'bit6 already set) through the generic mmio RMW path. P3_ANA_CON4 is seeded with the '
        'synthesized initial word 0 ONLY when this switch is on (route-AM semantics: read '
        'before write = 0, after = last write; the observed final value 0xe1 follows exactly '
        'from the disassembled mask chain). The poll helper then BUSY-WAITS: the projected '
        'CON word 0x46105d has bit7=0 and bit4=1, so neither exit fires and the helper runs '
        'its full <=1000-round budget (~386 steps/round through the RAM delay 0x12e256) — THE '
        'PROJECTION NEVER INVENTS AN ADC COMPLETION; on real hardware the conversion sets bit7 '
        'and the poll exits early, so the completion timing and status-bit transitions are NOT '
        'modeled and every step past 304,317 stays conditional pass / derived-not-measured. '
        'Inside the busy-wait Timer0 expires three times on STD12M (343252/383252/423252, '
        'exactly 40000 steps apart) but all three land in the delay-loop context where IRQ4 '
        '(priority-1 maskable) is not eligible (core_icfg gate demand 0): pending stays set '
        'with zero deliveries and zero stops — contrast the delivery-probe scenario whose '
        'expiry in the scheduler-wait context delivers fine, the context sensitivity is the '
        'existing eligibility chain, unchanged. The run stops at 447,490 on the CPU0 core-tick '
        '(irq3) FIRST OVERFLOW — core_tick_overflow_delivery_unresolved at 0x12e284 inside the '
        'delay countdown loop — which is route BF\'s wiring domain, not crossed here. The RES '
        'read at 0x6011704 (fd31_adc_res_not_modeled, the disassembled NEXT stop after the '
        '1000 poll rounds, ~690k) lies beyond that deadline; RES is a live analog measurement '
        'with no derivable source — NO 0/full-scale fixture is ever invented, a live FD07 '
        'measurement session (route BA evaluated the channel readable) or a named fixture '
        'with a real derivation chain are the only ways on. Default off: the CON read keeps '
        'the honest 304,317 fd31_adc_con_readback_not_modeled stop and 0xf2c250 stays unmapped')
    report['p3_ana_read_ldoin_low_assumption']=(
        'opt-in p3_ana_read_ldoin_low + the W1[0] computed-addressing container extension + the '
        'irq_delivery_deferral projection (route CB/sim-anaread, layered on p33_analog_tail; '
        'default OFF keeps the route-BY 447,383 p3_ana_read_not_modeled world byte-identical). '
        'P3_ANA_READ 0xf2c064 evidence: pinned-SDK charge_hw.h defines bit1=LVCMP_DET (ldoin vs '
        'vbat comparator) / bit2=LDO5V_DET (ldoin>0.6V detect) / bit0=CHARGE_FULL_FLAG, '
        'charge.c comments pin the thresholds, and the QCY W1[0] 20-round detect loop matches '
        'SDK check_charge_state() line by line — two digitalized comparator status bits, zero '
        'writers, no completion semantics (no projection possible). The fixture serves the '
        'CONSTANT word 0 for every read under the explicit scenario anchor "battery boot with '
        'no external supply, ldoin ~0V" (consistent with the route-BY RES fixture world: '
        'battery present at 3780mV, charge input unpowered; the charge-window gate skip is '
        'software path evidence, NOT an input claim). derived_mask=0x6 only; other bits '
        'explicitly zero-filled not derived (the five known app readers only consume bits 1/2; '
        'indirect base+offset paths not exhaustively excluded — any future consumer of other '
        'bits must re-review); duration=constant_for_this_scenario (no plug/unplug/debounce '
        'dynamics); detector-validity-and-stability at read time is an explicit assumption '
        '(W1[0] really configures L5V_CON0/1 + CHG_CON0/1/CON2 right before polling). One live '
        'FD07 read of 0xf2c064 could upgrade the bit values to measured. Container extension: '
        'P3_L5V_CON0/CON1 (0xf2c284/0xf2c288, r5+0x220 computed addressing) and P3_CHG_CON2 '
        '(0xf2c070, r5+12) join the tail AM family — literal scans never see them. Deferral: a '
        'delivery request landing while the target core runs an in-flight handler is HELD, not '
        'dropped — the dispatch layer clears the model in-flight bookkeeping so the next '
        'advance() re-requests the same pending episode after the RTI (route-BF masked-pending '
        're-check family); IRQ nesting stays unmodeled. Route CG closes the GPCNT read side '
        'with the gpcnt_con_readback projection (three-choice (a): bit-granular '
        'last-firmware-write on the br28.h __RW CON anchor; only the firmware-executed '
        'disable+clear bits are known, a GpcntConValue answers only AND tests whose mask covers '
        'every unknown bit, mirroring GpioOutValue/ICFG partial values): the task 0x6069538 '
        'bit0 gate reads 0 (the counter the firmware itself disabled) and takes the real skip '
        'branch nine dispatches in a row; the W1[0] 20-round timeout path executes (41 '
        'fixture-served reads, failure branch, no charge task); core tick delivers nine '
        'overflows total (138 held deferrals) and Timer0 reaches 286 deliveries. Route CL closes '
        'the JL clock family DCCK read side with the jl_clock_dcck projection (three-choice (a): '
        'route-AM container "explicit synthetic initial word 0 + readback=current register '
        'value"; br28.h marks the whole JL_CLOCK_TypeDef __RW and SDK power_clock.c deep-sleep '
        'save/restore lists all three DCCK words as readable; the sole consumer 0x6068152 masks '
        'every read immediately so the seeded 0 drives a deterministic field configuration): the '
        'mode-switch body runs for real (DCCK_CON0 0x46000e0c / DCCK_CON1 0xff000100 / '
        'DCCK_CON2 0x10000, WLA_CON8 |=0x8000000, P3_ANA_CON0/7/8/9/10 config, software delay), '
        'W1[0] returns, and the W1[1] walker calls the scheduler interrupt-context helper '
        '0x6065c3a. Route CO closes the active-priority low-byte test at 0x6065c4c with a '
        'partial_icfg_masked_branch handler (three-choice (a): bne.and.modimm.ri.l/'
        'beq.and.modimm.ri.l with i=0xFF is the low-byte mask test; the empty-active-priority '
        'fixture makes bits 0-7 known 0, so value&0xFF=0 is fully determined and the branch is '
        'taken/not-taken mechanically, mirroring the existing ext.b0.u partial_icfg_known_lowbyte '
        'handler; an unknown low byte still stops honestly through mask("and",i).concrete()). '
        'The helper then reads low byte 0 (not in IRQ), calls 0x12cb08 (RTOS critical query -> '
        '1), keeps r8=1 (error path skipped), and the 0x60659ee->0x12b55c OS-post veneer '
        're-enters the critical section (IPMASK 0->6) before 0x6065ccc `st.w.sp.l [sp+8]=r9` '
        'hits the upstream store model merge read of the uninitialized local 0x19e2d4. Route CQ '
        'closes that with the exact_word_store_sp_l sub-gate (three-choice (a): the long '
        'sp-relative word store overwrites all four bytes so the old word is irrelevant, '
        'mirroring st.w.sp/st.d.sp.l; the shape already executes three times in the legacy '
        'prefix so it is opt-in on its own sub-switch, not folded into exact_parallel_stores): '
        'the scheduler helper 0x6065c3a writes its two locals ([sp+4]=1, [sp+8]=r9) and returns '
        'to the W1[1] walker. '
        'New frontier: 3,071,380 @ 0x601d1ac unmapped_memory read of 0xfd2000 (the 0x601d198 '
        'switch reader r1==0 branch; r1==1/2 read 0xfd2100/0xfd2200, an unnamed ls_base+'
        'map_adr(0x20,0) SFR group, the next three-choice)')
    report['fd31_adc_res_pnd_assumption']=(
        'opt-in fd31_adc_pnd_completion + fd31_adc_res_named_values (route BY/sim-adcresfix, '
        'layered on fd31_adc_con_readback; default OFF keeps the route-BS 689,402 '
        'fd31_adc_res_not_modeled world byte-identical). LAYER 1 — PND completion: a STATELESS '
        'read-time projection; a guest load of CON observes bit7 (GPADC_CON_PND=7, '
        'gpadc_hw_v10.h) set whenever the last firmware write has EN(bit4)=1. Evidence: the SDK '
        'driver gpadc_hw_v10.c polls bit7 with a 1000x udelay(1) timeout (adc_wait_pnd) and '
        'gates RES validity on it (adc_get_res); the QCY helper 0x601163a is the same shape with '
        'a ~386-step RAM delay, and a full-budget poll per caller round (~96ms) x 80 rounds '
        'would put ~7.7s of pure polling on a ~1s-class boot — so on real hardware the polls '
        'exit on PND. THE COMPLETION TIMING IS NOT MODELED: the explicit assumption is only '
        '"the conversion completes before the firmware first status read after the start write" '
        '(lrct_trim_done bit7 / SFC PLL-lock bit15 precedent shape). The atomic compound RMW '
        'shapes synthesize from the RAW last-written word (their internal read does not observe '
        'the projected bit — a bounded abstraction pinned to the rmw.* instruction shapes; '
        'explicit guest ld sequences keep normal read semantics), so the recorded CON stores '
        'are IDENTICAL to the no-PND world. LAYER 2 — RES named values '
        '{0x10000:400, 0x10006:500, 0x10004:400}: EXPLICIT SYNTHETIC INPUTS, derived-not-'
        'measured (the voltage ratio does not uniquely determine raw codes). Channel '
        'identification IS derived: the setter case-1 path writes (channel_word&0xFFFF)<<1 '
        'into P3_ANA_CON4[4:1] before starting the conversion, so the mux field at RES-read '
        'time identifies the sampled channel (consistency gates: last CON write EN=1 and '
        'CH_SEL=0; anything else stops honestly fd31_adc_res_channel_not_modeled). '
        'Derivation: WBG04=400 (10-bit nonzero, constant-sample self-consistency -> '
        'h[0x127D10]=400); VBAT_4=500 (4x(500xK/400) ~= 4004mV nominal 4.0V cell, K=801+/-3d; '
        'BOTH battery-gate paths pass — the executed run takes the float-direct path '
        '4x945=3780>=3200 with runtime K=756, and the divider fallback 4x(0x4000x756/400) '
        '=124108 also passes); VTEMP=400 (no consumer on this frontier, in-domain nonzero '
        'only — weakest chain, labeled). FORBIDDEN REGION: low values trip the 3200mV battery '
        'gate into the 0x601248e shutdown — values are not behavior-equivalent. Downstream '
        'effects are FIRMWARE-COMPUTED under this fixture (item0 mean 400 u16, floats '
        '945.0/756.0, mode halfword 1 — the executed getter path DOES write mode=1, '
        'resolving the BU mode=0xFFFF doubt) and every conclusion stays conditional pass. '
        'One live FD07 read of 0xfd3104 (route BA evaluated the channel readable) would '
        'upgrade the WBG04 code to measured')
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
                              mem_pwr_con=True,p33_window=True,p33_port_sel=True,
                              p33_awkup=True,p3_ls=True,p11_syscon1_masks=True,
                              fd31_adc_con=True,fd31_adc_con_readback=True,
                              fd31_adc_pnd_completion=True,
                              fd31_adc_res_named_values=FD31_ADC_RES_NAMED_VALUES,
                              p33_analog_tail=True,
                              p3_ana_read_ldoin_low=True,
                              gpcnt_con_readback=True,
                              jl_clock_dcck=True,
                              jl_uart_con=True,
                              jl_uart_init_writes=True,
                              jl_omap_writes=True,
                              subword_stores=True,word_store_sp_l=True,
                              timer0_irq4_delivery=True,
                              core_tick_irq3_delivery=True,
                              flash_image_bytes=image),
                     irq_case(module,app,header,rom,'key_chain_scan_direct',
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
                              mem_pwr_con=True,p33_window=True,p33_port_sel=True,
                              p33_awkup=True,p3_ls=True,p11_syscon1_masks=True,
                              fd31_adc_con=True,fd31_adc_con_readback=True,
                              fd31_adc_pnd_completion=True,
                              fd31_adc_res_named_values=KEY_CHAIN_RES_NAMED_VALUES,
                              p33_analog_tail=True,
                              p3_ana_read_ldoin_low=True,
                              gpcnt_con_readback=True,
                              jl_clock_dcck=True,
                              jl_uart_con=True,
                              jl_uart_init_writes=True,
                              subword_stores=True,word_store_sp_l=True,
                              timer0_irq4_delivery=True,
                              core_tick_irq3_delivery=True,
                              key_chain_scan=True,
                              flash_image_bytes=image),
                     irq_case(module,app,header,rom,'timer0_irq4_delivery_probe',
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
                              mem_pwr_con=True,p33_window=True,p33_port_sel=True,
                              p33_awkup=True,p3_ls=True,p11_syscon1_masks=True,fd31_adc_con=True,subword_stores=True,
                              timer0_irq4_delivery=True,timer0_irq4_probe_steps=200,
                              flash_image_bytes=image),
                     irq_case(module,app,header,rom,'timer0_irq4_masked_probe',
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
                              mem_pwr_con=True,p33_window=True,p33_port_sel=True,
                              p33_awkup=True,p3_ls=True,p11_syscon1_masks=True,fd31_adc_con=True,subword_stores=True,
                              timer0_irq4_delivery=True,timer0_irq4_probe_steps=200,
                              timer0_irq4_masked=True,
                              flash_image_bytes=image),
                     irq_case(module,app,header,rom,'core_tick_irq3_delivery_probe',
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
                              mem_pwr_con=True,p33_window=True,p33_port_sel=True,
                              p33_awkup=True,p3_ls=True,p11_syscon1_masks=True,fd31_adc_con=True,subword_stores=True,
                              core_tick_irq3_delivery=True,core_tick_irq3_probe_steps=156400,
                              flash_image_bytes=image),
                     irq_case(module,app,header,rom,'core_tick_irq3_masked_probe',
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
                              mem_pwr_con=True,p33_window=True,p33_port_sel=True,
                              p33_awkup=True,p3_ls=True,p11_syscon1_masks=True,fd31_adc_con=True,subword_stores=True,
                              core_tick_irq3_delivery=True,core_tick_irq3_probe_steps=156400,
                              core_tick_irq3_masked=True,
                              flash_image_bytes=image)]}
    for name,path,digest in [('repaired_candidate',args.repaired_candidate,REPAIRED_SHA),('archived_failed_r2',args.failed_r2,FAILED_SHA)]:
        if path is None:continue
        candidate,readonly=candidate_view(path,digest,args.firmware)
        r=irq_case(module,candidate,header,rom,name,entry=True,readonly=readonly);r['ufw_sha256']=digest
        report.setdefault('candidate_prefixes',[]).append(r)
    check(report,args.firmware);args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(report,indent=2)+'\n')


if __name__=='__main__':main()
