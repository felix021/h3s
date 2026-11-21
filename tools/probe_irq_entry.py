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
the P11 request/response hypothesis). The P2M heap descriptor 0xf27f4a..4d
(P2M message area +0xa, value written by P11 init and unproven) is mapped in
this scenario as a read-only region seeded with the statically derived
candidate (P11_HEAP_INFO_DERIVED, same family as the erased_bankb 0xFF
fixture; derived-not-measured), so the chain consumes the descriptor for
real (assembles begin 0x3d0c, protects [0xf20000, heap begin), re-reads the
four bytes), then the pmu_task creation round-trips the scheduler and the
fifth core-tick overflow (masked by the IPMASK 6 critical section) is left
pending/unresolved; the firmware reads the core-tick CNT register (0xffe0f0)
at 0x12b0c2, where the scenario honestly stops
(core_tick_overflow_requires_resolution). The explicit p11_heap_info_bytes
fixture (conditional) remains the p11_heap_experimental_config-only path;
it is not how this scenario crosses the descriptor read.

The trailing p11_heap_experimental_config scenario layers the STATICALLY
DERIVED heap-info candidate (P11_HEAP_INFO_DERIVED: begin 0x3d0c / size
0x39f4, link-time immediates in the p11_code.bin common segment, see the
derivation chain next to the constant) on top of crc_bankb_erased_fixture
through that explicit fixture. The value is derived, not measured: the q32s
init path is statically decoded (raw wide-immediate bytes cross-checked),
never executed here, and every conclusion past 299,063 (the masked core-tick
overflow CNT read where the base tier now honestly stops) in this scenario is
a CONDITIONAL pass that must not be reported as verified. With the candidate
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
The crc_bankb_erased_fixture frontier itself (with core_tick_irq3_delivery
in base) now advances to 299,063 @ 0x12b0c2 — the fifth (masked) core-tick
overflow CNT read, reached after the base tier maps the P2M heap descriptor
as a derived-candidate read-only region and consumes it for real — after
four real core-tick IRQ3 deliveries; without
the delivery switch (the explicit no-delivery negative) it still stops at
177,491 @ 0x12ba42. Later routes advanced the conditional frontier through
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

The key_chain_matrix_scan scenario (route AJ/sim-keymatrix) widens the same
mechanism from the single-key two-band fixture to the real three-key matrix
layout (multi-function / vol+ / vol- on ONE ADC divider channel, four
threshold bands + a 20-entry key-id table) and drives the gesture set the
d1-acceptance D2-a recognition coverage asks for: single clicks on the two
new key ids (0x10b00/0x10c00), triple and quad clicks (gestures 5/6 — the
n+2 encoding measured where it was only extrapolated), and a 390-beat hold
that measures the gesture-2 repeat rhythm (every 15 beats from beat 93), the
long-press tiers (1000ms -> gesture 10 at beat 183, 3000ms -> gesture 11 at
beat 378, correcting route Q's inferred 11..14 ladder), the explicit release
gesture 3 and the no-extra-single-click negative example. Every band value is
synthetic-not-measured (the real config 523 lives in the bank-B record
area); docs/peripheral-input-simulation.md section 8 has the full
derivation and the D2-a coverage table.
"""
import argparse
import binascii
import hashlib
import json
import struct
import sys
from pathlib import Path

from br28_model import P11_HEAP_INFO
from br28_unknown import word
from frontier_registry import PROBE_SCENARIO_NAMES, scenario_flags, frontier
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

# 路 D2AI(sim-d2ai,预研 docs/d2-audio-input-pre-study.md §4.4):IRQ68 分发器
# 0x602f406 的 12B 特殊帧锚——入口 0x602f406 push.srl {psr,rets,reti}+0x602f408
# push.rmask r4-r0(普通寄存器组走既有抽象栈运输);尾部 0x602f44a pop.rmask+
# 0x602f44e pop.srl(特殊帧恢复,本表 pop 锚)→0x602f450 csync→0x602f452 rti
# (RTI 尾锚=机器侧 AUDIO_IRQ_RTI_TAIL;与 TIMER0_IRQ_FRAMES 同形,经
# diagnostics audio_irq_frames 并入 probe_boot frames 合并)。
AUDIO_IRQ_FRAMES={0x602f406:'frame12_push',0x602f44e:'frame12_pop'}

# 路 SRCSYNC5(sim-srcsync5):IRQ89 SRC_SYNC 完成 handler 0x12ca24(RAM 拷贝,
# flash 母本 0x60c61fc)的 12B 特殊帧锚——入口 push.srl {psr,rets,reti}+
# push.rmask r6-r0(普通寄存器组走既有抽象栈运输);尾部 pop.rmask+pop.srl
# (特殊帧恢复,本表 pop 锚)→csync→rti(RTI 尾锚=机器侧 SRC_SYNC_IRQ_RTI_TAIL
# =0x12cad2,与 flash 母本 0x60c62a2 pop.rmask/0x60c62a6 pop.srl 的偏移 0xAE
# 折算,经 diagnostics src_sync_irq_frames 并入 probe_boot frames 合并)。
SRC_SYNC_IRQ_FRAMES={0x12ca24:'frame12_push',0x12cace:'frame12_pop'}

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

# 路 FLS:flash 顶 sdfile ANCIF/ANCIF1 文件头的映射窗与官方资源内容 fixture。
# 背景(2026-11-16 路 FLS 全图+运行时里程碑钉死):28,764,184 停点 0x606c5ba
# `ld.w r0=[r7+0]` 读 0x61fbfe0 = sdfile「app 分区」文件 ANCIF 的头首字。
# 完整链条:app 分区表在 flash 0x06000080(镜像内,32 字节项 {头CRC@0,virt@4,
# size@8,magic 0x8192@12,终止标记@14,名字@16},项 CRC16 由走查器 0x6010cec
# 逐项校验)恰三项:BTIF virt=0x1E0000 size=0x1000、ANCIF virt=0x1FE000
# size=0x100、ANCIF1 virt=0x1FE100 size=0xF00(其后擦除 padding);地址换算
# 0x6010c20 `x-[0x1265C0+820*4]+[0x1265C0+821*4]` 的运行时对=[0x127290]=
# 0x2020/[0x127294]=0x06000000(codex 以 boot-info 已知字节复跑 0x601101c..
# 0x6011024 三条真实指令独立解出;BTIF/ANCIF/ANCIF1 三点换算一致互证:
# CPU=virt-0x2020+0x06000000)——0x61ddfe0(BTIF,既有 ERASED_BANKB_WINDOW_
# RANGE 同族)/0x61fbfe0(ANCIF,停点)/0x61fc0e0(ANCIF1,mode0)。
# 「app/ANCIF」剩余路径无 '/',经 0x6010de6→0x6010e2e→0x6010d54→0x6010d78
# →0x6010c20 解析(不经 0x606075c 尾部 island 的 -32 路径——codex 勘误);
# 消费者=0x606c54e「读 ANC 配置资源」:mode1 开 "flash/app/ANCIF"(魔数
# 0x9881,期望头内名 r8="ANCGAIN01")、mode0 开 "flash/app/ANCIF1"(0x98C1,
# "ANCCOEF01");头 parse {word len@0(+1 无符号 >=2 才继续;0/0xFFFFFFFF
# 擦除回绕→0 走 free 路径 return 0),hword CRC@4,hword 魔数@6,hword
# 10+payload长@8,名字@10..19,payload@20},CRC 过 0x6000f0c 引擎校验,
# 成功 return r7+20=payload 指针。音频域 4 个调用点 3 处直接判空、1 处
# (0x60704fc)经 0x607008c 判空跳过 payload;全 app 共 11 个调用点形状
# 含默认数据替代/传参/直返,「无文件」不必然崩溃。
# 内容来源=官方 UFW 外层资源(docs/firmware-layout.md 外层清单条目
# ANCIF/ANCIF1,type 50,UFW 偏移 0x102120/0x102220,有效长 0x100/0xAC8,
# SFC 解扰相位=原始 UFW 绝对偏移):外层 CRC、内层 CRC(crc_hqx over
# [6,6+len-2))、魔数 0x9881/0x98C1、头内名 ANCGAIN01/ANCCOEF01 与消费者
# 期望逐项互证(codex 独立解扰校验+本路 tools/extract_ufw_resources.py
# 复跑双口径一致)。物理 0x1FE000/0x1FE100=设备尾部保留参数区(docs/
# usb-recovery.md 实测布局表「保留校准」)——真机该区可能含每机标定差异,
# 本 fixture 的显式假设=「目标分区携带官方 UFW 资源内容」
# (official-resource-content assumption,非设备实测值);场景一切结论
# 条件通过。ANCIF1 只映射有效长 0xAC8,不补齐到目录声明 size 0xF00
# (声明容量与有效内容是两回事,补齐=发明数据)。
SDFILE_FLASHTOP_ANCIF_RANGE=(0x61fbfe0,0x100)
SDFILE_FLASHTOP_ANCIF1_RANGE=(0x61fc0e0,0xac8)
# 逐字节内容(官方 UFW 提取;SHA256 ANCIF=885121fbe0dbdaa5bedc5713ba1987c8f
# d553b6596eb01df20c2523737d69a1d/ANCIF1=47bb7e25b035c79cf90998a45b299c2cb
# 8c4f8eadb86558f7fa1bc8c9e7b46b8;单测 test_irq_entry.py 与在场 UFW 重提取
# 互证):ANCIF(0x100 字节,payload 长 0xEC=ANCGAIN01 增益表)、ANCIF1
# (0xAC8 字节,payload 长 0xAB4=ANCCOEF01 系数表)。
ANCIF_OFFICIAL_RESOURCES=(
    bytes.fromhex(
        'fc0000003d588198f600414e434741494e303100127003010101000001ff0001'
        '0500000005000000ce4d003fa99ee33fc164ff3fc8ddca3fce4d003fa99ee33f'
        'c164ff3fc8ddca3f010000010000000000300004ff010004000070170004ff01'
        '00040000ff7f00040004000401640901d200a00f00fc00000000c0c001010101'
        '010000002c0114000000f04200007041cdcccc3e000000000000000000000000'
        '0000000000000000000000000000000000000000000000000000000000000000'
        '0000000000000000000000000000000000000000000000000000000000000000'
        '0000000000000000000000000000000000000000000000000000000000000000'
    ),
    bytes.fromhex(
        'c40a0000cd4dc198be0a414e43434f4546303100a40003000100100006000000'
        '82000a00820004001600860082001a000801040007000c0168000b0074010400'
        '1700780168001b00e00104000800e4014e000c0032020400180036024e001c00'
        '84020400090088024e000d00d60204001900da024e001d002803040004000024'
        '42a8c601410000803f020000ce422db21d3e0000803f020080d5430ad7814100'
        '00803f0200800e4460e5bdc1cdcccc3f0200c04f4448e14a41cdcc8c3f0200c0'
        '8d448b6c9fc00000803f0200f01845b0729cc00000803f020000904593188ec1'
        '0000803f0200681b46583960c10000803f030068a54523db79c10000803fce4d'
        '00bf040000184219042a410000803f020000bc421b2f5dbe0000803f020000e9'
        '43d7a3b8403333333f0200001944cdcc1cc13333f33f0200403444a4702f41cd'
        'cc8c3f0200e0a744b07268c10000803f0200502f452db2b5400000803f020040'
        '9745ac1c9ec10000803f02001c1d46dbf94ec10000803f030068a54523db79c1'
        '0000803fce4d00bf020000b0418b6c01c10000803f02000094421283403fcdcc'
        'cc3e020000154300003840cdcc4c3f020080c64352b88e3f0000803f0200b026'
        '45c976cac000000040020060c844000000c1000020400200608645000060c100'
        '0000400300402d4554e32bc10000803fa99ee3bf020000b0418b6c01c1000080'
        '3f02000094421283403fcdcccc3e020000154300003840cdcc4c3f020080c643'
        '52b88e3f0000803f0200b02645c976cac000000040020060c844000000c10000'
        '20400200608645000060c1000000400300402d4554e32bc10000803fa99ee3bf'
        '04000018428716c9bf0000803f020000544360e590409a99193f0200806b4435'
        '5e12c1cdcc4c3f0200a05d450c02e4c10000803f0300c04645000020c2000080'
        '3f020000c7430ad7d7c00000803fc8ddcabf04000014428fc299c00000803f02'
        '00004043ec5120409a99193f0200806b441d5a1ec1cdcc4c3f0200a05d450c02'
        'e4c10000803f0300c04645000020c20000803f020000de430c02fbc00000803f'
        'c8ddcabf040000fc4275930cc20000803f020080c443560e4dbf0000003f0200'
        '80af43d1220bc10000003f0200408644c5202e410000003f0200006545f628ac'
        'c0cdcc4c3f0300180c465a6490c10000803fc164ffbf040000fc4275930cc200'
        '00803f020080c443560e4dbf0000003f020080af43d1220bc10000003f020040'
        '8644c5202e410000003f0200006545f628acc0cdcc4c3f0300180c465a6490c1'
        '0000803fc164ffbf020010000600000082000a00820004001600860082001a00'
        '0801040007000c0168000b00740104001700780168001b00e00104000800e401'
        '4e000c0032020400180036024e001c0084020400090088024e000d00d6020400'
        '1900da024e001d00280304000400002442819501410000803f020000c0428fc2'
        '75bd0000803f020000d843964381410000803f0200400f44f0a7b8c1cdcccc3f'
        '020000494448e13c41cdcc8c3f0200e094440000d0c00000803f0200f018459a'
        '99a9c00000803f0200c89045d7a386c10000803f02000c18467b1456c1000080'
        '3f030068a5459a9979c10000803fce4d00bf040000144233331d410000803f02'
        '0000b8422db21dbe0000803f020000df43c976ae403333333f0200801644bc74'
        'f3c03333f33f0200802e44a4700d41cdcc8c3f0200e089442b8712c10000803f'
        '0200f018459a99a9c00000803f020070964579e96ac10000803f0200b41f4656'
        '0e33c10000803f030068a5458fc279c10000803fce4d00bf020000b041000000'
        'c10000803f0200007442dbf96640cdcccc3e020000a042b4c88240cdcc4c3f02'
        '00802c446210d83f0000803f0200003945643bf3c000000040020080e4440681'
        '17c1000000400200608645000060c10000004003002023451058d5c00000803f'
        'a99ee3bf020000b041000000c10000803f0200007442dbf96640cdcccc3e0200'
        '00a042b4c88240cdcc4c3f0200802c446210d83f0000803f0200003945643bf3'
        'c000000040020080e444068117c1000000400200608645000060c10000004003'
        '002023451058d5c00000803fa99ee3bf0300788e451d5affc10000803f040000'
        '40428b6c5dc10000803f020000c242bc749bc09a99193f020000764396438f40'
        '0000803f0200c01f441b2f89c19a99193f0200a03745c3f58cc10000803f0000'
        '803f0300788e451d5affc10000803f04000040428b6c5dc10000803f020000c2'
        '42bc749bc09a99193f020000764396438f400000803f0200c01f441b2f89c19a'
        '99193f0200a03745c3f58cc10000803f0000803f0300e8fe453bdf90c1000080'
        '3f0400003c4279e96ac10000803f0200009442819533400000803f0200006c43'
        '560ef1400000803f020080c54414ae77400000803f020000304433333b400000'
        '803f7824a1bf0300e8fe453bdf90c10000803f0400003c4279e96ac10000803f'
        '0200009442819533400000803f0200006c43560ef1400000803f020080c54414'
        'ae77400000803f020000304433333b400000803f7824a1bf0300100006000000'
        '82000a00820004000700860068000b00ee0004000900f2004e000d0040010400'
        '080044014e000c00920104001600960182001a001802040017001c0268001b00'
        '84020400190088024e001d00d60204001800da024e001c002803040004000024'
        '42560e03410000803f020000c442e3a5db3e0000803f020080d5435eba5d4100'
        '00803f02000011440681b7c13333f33f0200c045440ad73f41cdcc8c3f0200e0'
        '94440000d0c00000803f0200f018459a99a9c00000803f0200188945e17a68c1'
        '0000803f0200e018462db259c10000803f030068a5459a9979c10000803fce4d'
        '00bf020000b041000000c10000803f020000ba429a9941c0cdcccc3e02000056'
        '435a643b3fcdcc4c3f020000f243190456400000803f0200004b452db2c5c000'
        '000040020080f94489415ac1000000400200608645000060c100000040030060'
        '434523dbcdc00000803fa99ee3bf020000f04183c081c10000003f0200008642'
        '9643cb400000803f0200007c436de7bf400000003f0200004744643b7f400000'
        '803f0300508745b4c84ac10000803f0200408744000000000000803f000080bf'
        '040000d841ec5148c10000803f02000092423f35a6c00000803f02000048437d'
        '3f553f0000803f020000da43c976e6c09a99193f0200405044759366c1000080'
        '3f0300401945cff7f6c10000803f0000803f0400001842c52022410000803f02'
        '0000b6426891adbe0000803f020000d243fed494403333333f0200c01944508d'
        '05c13333f33f0200802e449eef2f41cdcc8c3f0200c07c4454e305c10000803f'
        '0200a015459cc4f4c00000803f0200709645871631c10000803f0200b41f46ba'
        '493ac10000803f030068a5459a9979c10000803fce4d00bf020000b041000000'
        'c10000803f020000ba429a9941c0cdcccc3e02000056435a643b3fcdcc4c3f02'
        '0000f243190456400000803f0200004b452db2c5c000000040020080f9448941'
        '5ac1000000400200608645000060c100000040030060434523dbcdc00000803f'
        'a99ee3bf020000f04183c081c10000003f02000086429643cb400000803f0200'
        '007c436de7bf400000003f0200004744643b7f400000803f0300508745b4c84a'
        'c10000803f0200408744000000000000803f000080bf040000d841ec5148c100'
        '00803f02000092423f35a6c00000803f02000048437d3f553f0000803f020000'
        'da43c976e6c09a99193f0200405044759366c10000803f0300401945cff7f6c1'
        '0000803f0000803f'
    ),
)

# 路 TONMAP(2026-11-18,docs/frontier-scout-vfs.md 全案;codex 裁决 (a)):TON/TON1
# 分区容器官方内容 fixture。41,858,013 停点 0x6010ca6=第二次 sdfile 分区目录走查器
# 0x6010c3c(恢复路径)首字节读 flash 0x61eafe0——TON1 容器头在固件 SFC 明文视图
# 中的位置:官方 flash 应用区 SFC 明文 0xCDCA0 的 EXT_RESERVED 分区条目表(boot 槽
# 823=0x060CDCA0 指向,镜像内,hcrc 全过)恰三项 {EXT_RESERVED addr=0x20/TON addr=
# 0x1E1000 len=0xC000/TON1 addr=0x1ED000 len=0x11000 last=1},分区条目 addr 字段是
# SFC 应用区相位坐标,固件统一换算 `addr-0x2020+0x06000000`(0x6010c20,运行时槽
# [0x127290]=0x2020/[0x127294]=0x06000000;与 FLS 路 ANCIF 停点 0x61fbfe0=换算
# (0x1FE000) 同族同律)得 CPU 映射地址——TON1 头 0x61EAFE0=本停点、TON 头
# 0x61DEFE0(勘误①:docs/frontier-scout-vfs.md §2 布局表/codex 表的 TON 头
# 0x61E0FE0 系坐标误算——xlate(0x1E1000)=0x1E1000-0x2020=0x1DEFE0,少减
# 0x1000;按 codex 表的 TON 窗右端 0x61EC790 会越过 TON 分区右界并与 TON1 头
# 0x61EAFE0 重叠 0x17B0,本路挂载范围守卫+单测窗口重叠检查抓出。正确布局
# 四方互证:BTIF 头 0x61DDFE0+0x1000=TON 头(条目表相邻)、TON 分区右端
# xlate(0x1E1000+0xC000)=0x61EAFE0=TON1 头、TON1 分区右端 xlate(0x1ED000+
# 0x11000)=0x61FBFE0=ANCIF 头(=既有 SDFILE_FLASHTOP_ANCIF_RANGE,FLS 路独立
# 钉死)、VM 窗/record 窗均在 TON 头之下——全保留区排列自洽)。内容=官方
# UFW 外层 type50 条目 TON(UFW 偏移 0xE6620)/TON1(0xF1DE0,
# firmware-layout.md 表记 990688=0xF1CE0 系笔误 0x100,SFC 解扰相位=UFW 绝对偏移,
# 外层/容器头/目录/文件数据 CRC 全过)。三方 SHA256 复算一致(本路 jl-misctools
# 提取==codex gpt-6-astra 独立复算==本仓内嵌字节),见 TON_OFFICIAL_SHA256。
# fixture 语义:两容器(官方提取、SHA 钉死)成对只读映射,窗口=容器有效全长
# (不按本次文件读取范围裁剪——目录/索引/其余 tone 文件同属一个资源对象,FLS
# 「有效长」先例);不映射 padded 尾、不补齐分区声明容量(TON 0xC000/TON1
# 0x11000),两窗间空隙(0x61EA790..0x61EAFE0,0x850)与 TON1 尾至 ANCIF 头
# (0x61FB320..0x61FBFE0,0xCC0)维持 unmapped(erased_bankb 先例同款边界)。
# 挂载自校验(ancif_
# official_resources 同款+SHA256/目录面):长度/完整 SHA256/容器头 32B JLFS 项
# (hcrc+addr=0x20+len=有效长+flags=3+name tone_zh/tone_en)/目录逐项 hcrc+文件
# 数据 dcrc/范围两两不重叠且不与既有窗(应用区 SFC 明文/erased_bankb 四窗/
# ANCIF 两窗)重叠,校验失败即拒——fixture 只认官方工件字节,零合成字节。
# 显式假设(codex 原文):本场景假设目标 TON/TON1 分区在固件 SFC 明文视图中
# 携带指定官方 UFW 提取的资源内容,并按当前 sdfile 地址换算映射。映射字节全部
# 来自官方工件;未假定分区剩余容量的内容,未验证该设备当前资源与官方内容一致
# (usb-recovery 实测布局 TON/TON1 标「保留」,真机字节未取)。执行结论为
# conditional / derived-not-measured,不构成真机播放验证;停点首字节读到的是
# 官方容器头第一字节 0x4E(hcrc=0x7e4e 低字节)。zh 语言臂(QCY+348==1 时)走
# TON/tone_zh,同批映射即可覆盖(codex:成对开放利于 zh/en 对照,不设两个开关)。
SDFILE_TON_RANGE=(0x61defe0,0xb7b0)
SDFILE_TON1_RANGE=(0x61eafe0,0x10340)
TON_OFFICIAL_SHA256=('6a511c89acfa4433fbab7d46f22bbbb62c8524cdd93cd72c9b5101f104ae52b5',
                     'f765091dadf26e5042cbfd94ad8570f71be4714e5fdfa98ed0bcf3f0b4c1fc66')
TON_OFFICIAL_PARTITION_PAIR=(
    bytes.fromhex(
        'a84821e820000000b0b7000003ff0000746f6e655f7a6800ffffffffffffffff'
        '43a19094400200001e01000002ff0000696e6465782e69647800ffffffffffff'
        '0b8fba5160030000a909000002ff0000706f7765725f6f6e2e77747300ffffff'
        '26a24364100d0000710b000002ff0000706f7765725f6f66662e77747300ffff'
        'bc47e06090180000ff10000002ff00006c6f775f706f7765722e77747300ffff'
        '378262c8902900001801000002ff0000766f6c5f6d61782e77747300ffffffff'
        '808b887bb02a0000cc0a000002ff000062745f636f6e6e2e77747300ffffffff'
        '9d01f42780350000560b000002ff000067616d655f6d6f64652e77747300ffff'
        '1ace939ce0400000650b000002ff00006d757369635f6d6f64652e77747300ff'
        'd717a172504c00009f04000002ff0000616e737765722e77747300ffffffffff'
        'c44bc9d8f05000001807000002ff000072656a6563742e77747300ffffffffff'
        '3a9d81da10580000860b000002ff000070616972696e672e77747300ffffffff'
        '4c6ad2b1a0630000340e000002ff0000616e635f6f6e2e77747300ffffffffff'
        'eadb5644e0710000500c000002ff0000616e635f6f66662e77747300ffffffff'
        '3c1251dc307e00002410000002ff0000616e635f7472616e732e77747300ffff'
        '3eca9c9b608e0000da08000002ff00006c696e6b6c6f73732e77747300ffffff'
        'cf8a2d83409700000512000002ff0000616e635f616461702e77747300ffffff'
        '96d4ac9250a900005d0e000002ff01006d6f7669655f6d6f64652e77747300ff'
        '54494458b0c5ffffffffffff1000000084551101706f7765725f6f6e2e777473'
        '00cf8f1202706f7765725f6f66662e7774730031c112036c6f775f706f776572'
        '2e77747300ad041004766f6c5f6d61782e777473006f3b100562745f636f6e6e'
        '2e77747300ff21120667616d655f6d6f64652e777473003e2613076d75736963'
        '5f6d6f64652e7774730093e60f08616e737765722e77747300513c0f0972656a'
        '6563742e7774730064b9100a70616972696e672e7774730069370f0b616e635f'
        '6f6e2e77747300d1b2100c616e635f6f66662e777473005f31120d616e635f74'
        '72616e732e77747300189c110e6c696e6b6c6f73732e777473006c9e110f616e'
        '635f616461702e77747300cf0713106d6f7669655f6d6f64652e77747300ffff'
        '2807861bc3bb9518e01d8002bf2ada65a0621fe678f96c383ce386844cfe7c2e'
        '2005d0a1ad8a4026800b188fbc86b755508bc2b5888e1ce37c22d62dd82afee2'
        'd8fa7826f3011ed4b979dfe245b525800b4acd89df41f8ec0fdba7a02bb8ead7'
        'c5ae79fa253abcdc89376149318653b8a321611028804a32d81ef922fb9f4445'
        '397e72a4569ee0f45d3c6cdb7a4952185bc83b29484f840b7d16d9e2c0278498'
        'f3e7722e24e49c1beb4ed62f60a90d99c9cb9f41da2841666e1bff2bfe03ecba'
        '324f7ed1f02784db6872964ac0c244fd742ad88e10d81541b4b7b9840b8f1e2a'
        '0d8fc9a4a0ffa7211167fc6d402796aec8fb20187a2d235dbf22a2cbdd5f5cd7'
        'b78a98b0c73ec56d1fba184a54753af2d13116e7e82697760af792ba9f59c78b'
        '87d922a8435ba5640c83fa84d75d00c66f3514ce847d0f8bdbcc798a38bc474f'
        'ae5983ac8dd5d33e4155a1fb12855059d678869aa5582d4f3b9509389f7faa8f'
        'ec33b5d8bdf4a244339682d36ed9330fb4c83afb0032bbf5125f98846c2231f5'
        '35bc210b697c723350fb06786158002908f18108ae6c068e4041e247597c3ab7'
        'cd4d49c3836aa26032b98b61d4d9f861beab3a7539da1a46b87ff57ab4a6e100'
        'db696e0f81cbb98d109105e452e805f23fe34fc8d7a6aa185045002ab988223e'
        '140fded24a45038d4420cf73c78524db9f73c2fca989ff566050f19ef7c5cb0d'
        '4f607feb992031b88499a15fdfb95cec82d92e88ffea4a82790135909bf565af'
        'c8dc2c32d48bff791ebae711e1ac12de2e4324965285ada131b7ce0e0fc69c3a'
        'f8b8306a5dd8397bfc8c4668e5a40695c532a48e9d95b72ff43a45a0ed444d4c'
        '0dbde2d031109a0abd8031b7d8d6adf53071cf1cce6b86ee25c76998358bff56'
        'bd8d5b87346656a482a1ff2f0c48d346714410d01830f746a328ef9030b77a04'
        'fc93d58e9dae88da8ea5190d9cfe6af3e89136f06afb4f377269d911071d2c15'
        'df80edc9f013b6443468e71db032b3287dcbe5ce32d71b7eca012fff2c5434f0'
        'ff2327957be99c256aa2c324ebd879aa072cccbf4c9fd71a6075639254be0e30'
        '31b0234f1eba6d6d76b53aa41a983c64c8dfc3db6ece026d2d2f967597be6007'
        '003540aa57f734ed867ae235f4910dca7c6830a5d0a238cfb4cf96a4d839bd32'
        'f47d1063229e5b8d60f17529142b12c45cae6df770fbe99e6803a59248941bc6'
        '60a99031bdb7dd78b20a74381fc6c62688141210bef942e0381f64381964f015'
        'ff14277dd9f5c5180d5307afd994db954a78540e42278d38ba9460cdcf90bef7'
        '8dc993c9fb23b86f9c1ae7ba663dce2f97b43cefdd0ace3f635cdbdbd4289205'
        'b2e7f72186dd96b544f94157c29cd364f86affb6571651f1d56d4e686d4fa29b'
        '8d96bc9a860027932d12b8daa45e980c2f66f80e62dcb8083bb17617149b019d'
        '1398f71833805dd010570915ad00278466554ce76edaec729e30daab9482e3c2'
        '3d93aca357b705c4fccd99634012c2d1bdf02923ddb82793cc84c6392df98b6d'
        'fc8927d39bd6ceab7b3842506f91075c8a164b49e706ab48289349ae3d6039b3'
        '276d739e00b7fc44df9f1325f4c73744d006b87da439b63cdffdcb03d14985da'
        '3ea829cc7c4c1a9bd868cc82dd23c143e48dafeb25b83f202eaeb68a2b8ddadc'
        'a3187a19eef5a2a88e9abcca32096db0f8ea25d48535da1dfa48d0b53e7d1f9c'
        '1553f9928188602fad039e8c7ecae0693569f6e11839a6e5654491eaa713c39f'
        'beb7ec3717ffc02ea6ebcde1514e1f236544fccb89bf802cad000b11c30c8c62'
        'd519d958623624f0749050d65fcc23e9eeb05b55bb08b80dd8e5e6e53eab62f2'
        'a7e59d222dad000b842308a423ac1e6c6c8acd21ab935bf9f5c595e16c38d20f'
        '06a2b6d2d69050e282c89e0d48ac1004ca582cac7499e3ca0b2f0341b76fb7c5'
        'fba06405317ec9d1828beca3840d1461fbfda21a5ef13a3e8e92ec9bcbe2c02b'
        'ab1ddae6a898f6cb9c39366b75f53950655b2f551b8e0e64248ae3a93e777542'
        'b43c8b1fa75343fcdd596a2fa9a58c9560b48c668c2d40f102f67f0f1b1121fb'
        'eb47edd50e232af9a0f5934a6005d0299a00c6346e55be80b8c5802ea94a6dec'
        '50871d5a5b2d157944a9052be4cce129f506e249c939aa9b7f105d5317b8061d'
        '4a23f1fe15431c17a5e031a75c58fdbdd4aee35be01f850f678db6993fbd58d0'
        '855ff366948ba764910e9352fbf41c2d1f36b86609003bd72ee0658032a62e1e'
        '487ce3be8fd5c4b4e4d2095b4b06e4759c5f3dbd71151846ef07a84e3b5f7cde'
        '8d85b4214a1dab4b55022d8eb3159032a4a802d6d63547b7b37eb2ea14083f5b'
        'dc4ad8af81a188bcae01b20f6e1a36e035a34d78e339d258b7d4b9379291f8c1'
        '1eb0329e3c651b2ce394decfff94fbe8d362369135f68ef3bd7364d4ccf7d537'
        'b3adc5a0209b557ca40b44bc87b989d338d4bf3c00278904b289437353d73c5a'
        '37eaa18ba4125efa097f8671017c3a29211513844508fc1ababb3eb07e258066'
        'ceb87a17c1feebb7a043248ee3278cb1f00143177da415559885f1ad5a047d74'
        '505cc02580340554d546a09a5780e9813eb93195fc645f222a72825416d295a6'
        'fad947b69832fcf6b42686349a0c0431751534fefad30f6c47f733b67cae17b6'
        'a968d676591dbbc75ad4505bfe6aad38288012772adb23870853986a58a1690f'
        'ba805f0c4501064a59b9026d7f9ed30511e138a8368c0ff93e288028caf43d14'
        '92f468f0ceae86de743140e3a35152da4373ec431047468271d0bba2d904d6c8'
        'd81c27800b0e7a0b5f3651d72692bbe177f983c994b1c45c4110984b49a0622b'
        'f70e142d51f496edcd7827802205dbdbd01b8f1c24e67a08d713f0a9555cedc1'
        'fd02d6142275d027c769963f8c913568722e26862a210457a51ddf973651c936'
        '347a00f3c5d67b567d068ca435194e95f3ceb100250fb467802780110368ec1a'
        'ecda95dd93f456fdf98fabd76612254f8f93f182878b76eaa7af5fdd6db432b5'
        '802286349a321fdfb0b6013773c2ef73b576b6f859c70972689f26bd3e58daac'
        '71392a0027862217724e5f7d8b3538d8eeac4ea2707a6ceaf414d4018c421fe5'
        '9fe50b75ad931342299b5f0026800b2671664932036591c623cdb98bb14fceb0'
        'ea3534f8194280c0db91bdf6b10d18148b182028800b0e7dfb0446d95a94e631'
        '1bf5c5b8df497e7e9e65e33d26b2937eb698e36647e1aca3cf63dec0248621f0'
        '8065e5afaa59ff53713972545d12511fae299a720a1d8c31c994c9efa1b6da1f'
        'cc28861f86529239b7b7049ead154eb2982e1212cd0c0589ca4133a19f2cf47f'
        '85b691f734ab1851c3a026801177da134658663dc0a224432b92005b755e183d'
        'cc2a8c2fa124ab597fdd1cb577ea32354013800a80ee6e4c9cdc1ed0577958f0'
        '8115dae07c07860691cab8658006800221722d4e06800221722d4e0680022172'
        '2d4e06800221722d4effffffffffffff280780090135eaf7281d8002b03a747c'
        '80932ab38d7b76b02a70b1175843f929f545c98c061a2026864eac0f094a8857'
        '1a0f34ed5855f2fb64f94a536880a8cfa3519c8f4fe570d82f7b53149a542586'
        '225a7994667248997ac76bb6c7f49ff0d97b2e17779e9166453c48a518c74977'
        '40f592182486223227186833b4a7e915afb0e32c6ec93c5e83dc4025505afbc2'
        'f65fbb4b8acfb9601023862217c65334868ccfda198434d47a9835953caced78'
        '0b07f9921488323220e1ac238027800b1868a6f73e488e14ad656b5d15ec867c'
        'f2a994548bdb37b9a7e09eefd9efd82fd71cb9b65025800b26728d139da1e056'
        '8f5474a84107d4c56d525e278e993aa0e1b61f0df34828dc21d280268622318c'
        '9905bd01283d2260929b98a2fcddd825661b43a222d63158df506e7ca7814413'
        '787e2486221a41596003cb6030a22aebfdc4e63691175d1af49e1f59bb7a6ca3'
        '50453e3c195840238622324afae18da649032387a6db449129821d4a770cf835'
        '2f56382c33a9b98f7508142886221caee3f85d2778be29091c40687179a6b711'
        '77cb210a45c2f4d1a4f64fed39dff0f12f121880248622184c3762c814412ab5'
        'af5e5233458d5527f43dd8afb5e96852a15695b79479086b3c23800b7a41ec61'
        '4a47b0c073abf5cb38e0f8b6ac143efdf4f23f8d10d2785f65fe566ef02883ff'
        'b5ad96ecf0cdc1b2966f7063e7c6bb18e0ef3d390cdf554c41decdadbc717687'
        'f32d58ce4de43db0a203b3f789c9d069460c70380f3eaa5170fcfdb32050b6b7'
        'c2f2f85a39e822a24508cf6162822818c5967562c92d8da704b40f7c12c4ab6d'
        '04b81cb233b6491f10357d43fab42d92da791e29ccbab43991aa2e924f126eb4'
        '497f12f33cb275f945e6591d62d181da941a9ffd18bcec0031b8c0a4e12c4e13'
        '9c294dab30a85712259d8c20116e9af79b77924c2a7813da890b19b59c5d128c'
        '5f42e33b028b4e73611b2db8bcdfeed90f74772deb7108b1b355996b171e112c'
        '1f12c89cbf303255d5c3410baff68c6afb879fb27085ee802fb88fa8a000cc82'
        '9fd1a840703de71121c1a996522bd330fc2896c501b55f1a556a3ce7612fe0e8'
        '8246d665df22567032b77f3968dfb77c57282f069febd2dda2174cea69bf2af2'
        'abc92e303a2cc9b2fda794650190a22d7aa442964d93c0e47cb2d333b4b960c7'
        '285445077edfbb95410cda1a8efbd03ada4eba15e9c1a7e1aa645d2ac882c28f'
        'e3f8baa82daa1038fbb609d35b0f9031a626e2907bb9080d7522804c3a99d6d2'
        'd098b978bc992be65d8eca9fdeb79f9f9c614f4d7fcbaf9abaa9f117b0c9b243'
        'b82da27603a184b15089d2ef82f7e13c403ecf2fe2a2834d5c6e6e43c78d4b28'
        'd741acf3421b28bec7fda6e370ec0031a2671e71836393ae328656d3bc6a97bd'
        'c18641a1f876f15cb2fc1a6f9f9de7fb6e209211cca82f6d6ed834876cc2beaa'
        '983ba1c5fbbd70db69a557b8ce1b9eddd05c20322342eac6452d78e95021af7f'
        '9c0ad7e937e9179bf88ba5f0c2f55f3c9dd6368c329be1eb20a98f5dcc2983d2'
        'd36dad59832fc7ce22e2148d9b055401ef2e040517139c20efce05db2e07a1e3'
        'bf406af362c5002783b01d15b77053684f9ac507f8bbfde83715cb2982a5a5a4'
        '57784986e729b87666a6e786417d802892c2d42bb5cdaf490c2aa9a412a83a69'
        '72baa1f4b15fa69dfc7e93eb1594e5448204ba9e9a9499f029846a666081bbe8'
        '170d0a0bfdac6b9e540dcec3b8a5b8774817f1f56d49d1cc66b674b15da95328'
        'f1802795406e2d0558daeb4337a553576a5e38aa531544469fb030ecd4d8f22e'
        '41fa6bb72de72e39b07c37b4d121b354a2044cda146564eed473b1ea2244f612'
        '4e4dad912120c4305b11d07f94f9fa0357df0f683482e374184aaf9667ccb882'
        'e00032b2233476fa8fa8bf1965d62d5741f52a5626a2f24c1a01e54afe394b08'
        'f22a95efa334cea11ee13aa9aac4dde44174d4bc002ab18b2b308f88ddcf97d1'
        '37d949bb98a6652afac9e6b80947e030528a1c9c6f7a48a88065b408cad16640'
        '28b070751c0531a97ebe60e11d0ccf81d9ee60455045a85c059e6c848baa81af'
        '9f663f9dbc2a6018401db0691ee98d3ebc6aaa1819cf922edf466913ba1bde3c'
        '1f9d30853ef56821aeb65ea1c183f2b9acb7cb91e7ab64682226f468cea9c142'
        '3d439c1e8d8bdf080024ae83d22b652ced9149de21f23fc78678b9499f213d1f'
        'f0a0dc86a1fbc08c124615ab3ae824acafed9dbb32029adc1eadb8939c393b18'
        '5a55f00f6de369381250959562921c5a20dd5f27ab55f83e6f4633cd0a8c4df0'
        '8689d0cec227d9cb6f7b7a192a8d49be2395d8af56d25cadcfc6802aaab42237'
        '3372f4560fd2bede845b5769d9e19a69bdf5714bd28228748128d600633ea2ec'
        'f051aac9a51830a648fd2b6e560a71dae27f74a9e0fc9b031edf81d09af0aa55'
        '40cf8d099fb6002d138272fad20c457e1731b9ec725d8033a4b0c56824c7cf0c'
        'c435c1d0c2762d8b4947d21ecdce3ec96d5dd51be214f357b84e03973801f74b'
        '593bf6f774a5d6e098e18033a206ef972807d10a0b5759cd2c19da4c7464621d'
        '73bac9b7b73dbed8a7b293515825779ae669a39841642c175ac21dc721c4e039'
        '9bf0c3e361bcb2ceaecbf0c7bc272408d1ecf1297528ecc1ab13a855fc1e7653'
        'e303a419002ab0d937004e3929aa1c83935ad44d2971de3fd027812f27a29192'
        '7dfda1af1d8358e8a1537c42cb34a1ce5b32a88967f9aa7d33c3b15d1538e8c6'
        '00288111848596d515dff650f8c2d709ce67bbfcf8d6f1591eb023529f008e53'
        '6a7e29bb593a7df5021027873a2173914a25304650863d86c6d74311d94a64b2'
        'de2c1e14dfed128ec4d8cc4a898d2d86006027804eb42a360f17edc8df839423'
        'e7090c9636e1412fd786d7dc372ae742ad24aa5be6e30c259bf028802f18c52b'
        '95a49b32ae65a4c8b18a27c9ce0f414e119886b5db4e8403480dcd5605d27599'
        '32aca027868554ba5a816ed2be0f80342f3610109140803f4a442d3df0a3c667'
        '9dbca76c3fee3b12df7fab28802742920a5dae26643838a0edb84af82fc3e120'
        '110e6ca5842002df74eb88f0bbbd862a833a078027802269f80cb783402f85de'
        'fe7bc87e9786a5b7e6be87ee3a45e746082d5701796b7a0d5eabc50026865172'
        '5ce91dd1f857ef3c4ef1f8caa9b7b31b05f44c34fdc8dc2f23b342521cf7d832'
        'f4ec2027862ff041cb2837b645c19dd5d0535d6cf75b990d5598acdfa7e09f5d'
        '789488f72f5dfa638e15c0298623d6828aecfaa45fecd03698e97e3872abc50b'
        'f27622baeff2a56c4c4b09a6b89b0c7cad7e706ef02886221e03d99dfc12909f'
        '06fc8a8df85cb0bf4d802fe439b807b613af3af038592eb97bec875c800c2780'
        '0ae9587754f7688a4891ed0c81c3111ea3932f1af8c3fb99798957ae6f262265'
        '272d4dfde3c027800b0e74e221d09fcaf19f58bd07335ca3493bcf608fd2fa4a'
        'f015307dd10c894a7d3bd1f9478025800b17355bdcf55642ba524412ccb9fd1f'
        '89e4ecfe0bb27c5bcbbf96e046e0608eab176fb82686219ae371da068c315431'
        'daac9bfddd8cff509600d150ee0aa832ab9eeef54044f923a71d8026861d997e'
        '868afdfc9e39b99957939d8b8b8f639e1457387891dd8723181bf1a9045ae07d'
        '975d28861f36828a6e750b9566c1700712740f1e2d2c369c1d57437268e5e3d2'
        '25220b6a87a2677ff8a90027862232df85edbd7525a5fac22825b34e88d2c475'
        '486f9da2fb8942310c04d5747ece3017bb590826800b03857ea9fc3e056449bf'
        'ee31ff63a8601e8e0d15dbf74945a68fd83ef425f6dbf1836c2027800a211a85'
        'c2aac2b155d555e40e337caf4605f155e66e4ddd041252bc17a8d06ee0a87feb'
        '958025800ae61cf5f3ed92c545f5498b330bf385f76b61899b3e53021c317245'
        '91045ec7dc670ad028861f3683ec98879ef3ee8b793bc11b88b771f56be8a2f4'
        'd53977c0162bae64190143bd0bb4cf930028800bb8a25f736072c26c43982809'
        'ddeff411c26c285447aadff267248000e7cfc4b331890fcc9bf40a8612a52f86'
        '4b8589a90006800221722d4e06800221722d4e06800221722d4e06800221722d'
        '4effffffffffffffffffffffffffffff280780090135eaf728198002b0e0295a'
        '9a90b906ece2c4209f16e19ce3a29ec484ca9022862f7391f1b64b2f24bb9206'
        'e3eccb886ea957ddf3c1f082d829c2f4502c0cc96ac4218011743a9bf784c589'
        'b0d4dac6a83443cfb903bf44718be80d4bb4d1fa681b2e00248636a06ed9b44e'
        'd36ff7ff495e893e4e2c87897e4c952be028a3f429ed63a2bd3fd37208248020'
        'df97e34828891d2cf35273d7c8f888be62d51b9477dfffa9ffc27dad13114ca2'
        '7568238533573e2bfc1e9eabdc1ccc515b708997f5401291ed0cc6bd3838d6ca'
        '14b391c486d02fb9491b7211c38e805bbfa0a5e09f61659af9a29c5d1450541c'
        '3b4879b2e7847c845bddec4d21cb0f02539cd028d72029b6a15eaaf108d084da'
        'd5008c6403cdac0baf88e636a245372a80faeea653ff04473a73920b3dc22000'
        '2bb60fe88349efbed5a63e3b23406ec7a625ba6c7112b912a59914dc0b41d76b'
        '1627df234bb822e6351a9b802bb7d50e55f57b0cd38670d72768a7452b0617c1'
        'abe6fb3622bcdf8a9c26d1136e0eb26a7a749fc7e87baee02ab909fdc1bb866f'
        'f3a0dfe621b2b70d1057086413fa5611490685f980c0329fd6a0e503986c029c'
        '15a0002db8d8f91396931c9a7cff45bd5bd2ed08f80bda10ccfd8bda4a95a686'
        'ebd0bc8c509a135c0178cf44b4b30502b02cb969358dacc6ce319e4a92a9970c'
        '6233c018f275e08c9491518203140ce169fdb8060195413a57254a1d1c802bb6'
        '936cfceee03de9758bc0e42ab3bbcb15f9b6fd7cc83154656d3f41dbbbafca16'
        '03b2643b0cecf6ac93a62db37af961184c7f3b386ca7879a27cb855f93a9ce57'
        '046998ff1e5ae45071dbcfc1c3ed60ba1209402f45e394d82db3cb020156aaff'
        '58ce8fc7c2e714891cd6aaf493a9600125d449a6d0753dd95e535748a60e076f'
        'e5097d18eb7c2dba83450b2d2e116387a73da8281c026e850b52fb8ed156c0b1'
        '1d9ee898f74fc56bf24f8ccb4ee5023bbb63a0002cb9ac97b32fb26b4f946cb4'
        '67bc02399c37564fffe054358d7a73672a95f78cbb9e3904d68821d0fda251bf'
        'd02bba8f3c22942b8ad5a30b3d37be11ebde0d9a4fb489e22519bfd79df25e67'
        '4c381f05c50dad9d16161e49002dbacf7aa4f0a97f8f690372616402718034dd'
        '18e7e90005f928aed0e67edde04337445d48d3f0275e830b56df4029bb3ed81e'
        'd6f8bc91dc13ef19c7381e9c0b3037146faafac3d32cbaa9a029c72e953adacc'
        '27c65e8f802dba38f910bc888260b70852608ef7fe885e6efbc06a2a42b0ef63'
        'f66455b3b5e750273cb5af39d7e58ff44d42882db7b9092fefb0ef1fb46df455'
        '660f4671dd7d727f02030819a874ea96b73111f968c302d077d63173b7199654'
        '3c2cb5ad59b3a4300d8e94ef07910c5b5ed11ba714522ff81f0667fe1529a2fb'
        '7fa47b759deef663caed1afd45702db311058c10000d3d741be13484140b5a14'
        '995f5d3a81d39a66d901cc432a248e403b1f9a40cf2fa5277f4237042bac5c1f'
        '0934e217b3f51604d700bf5bd872626a30da62fd89cf4076736d3b0eb15b7d64'
        '15a95035f0bde4062bad8fb47aba370406510e52c8b8fe8b547e978dd8491fbc'
        'ee6fb041805185a0a0e17aa6ea2d0922444509cc2ea33fe238b5439bf654d0e9'
        'f560d54ec4e815d7ec284f4785fb67de1b289a692ee764de99593e7f7bc32163'
        '822b582ebdaab0cd61c77a6e4d8260c59857bac21ecdc9bbf35a23e2074b03c8'
        '0644762eaee159becac172f4bd32da58aa0024804a5a5847f937e0a5114985fc'
        'f277329a2edae2f37faae6d66680295fc673ff52f903e021961c719525fb29e8'
        '59e5f54d9b1b1ad1a111d22354c46ed4e5da441fe58fb156482bb5972d60e5ea'
        '73f3b16a8fb2a959faf25cd86b664036b7ecfc2e0fafd35ab8ea85a638011cd8'
        '6c5c4bf8182caebb782bb3cc12cc83b9ee4e8cc11e23a855ff4582d6a85ac69b'
        '6f3113d55858a0f88643688f3b5cf6cac54c2cae29f244e52ecd4aec8dca315b'
        '70c1bd805d043a1982a81fb1dfab715850ab5653df4221b1a5d8841bb83aa42e'
        'ad0af51bf895cda665ff582b8dd33e15341813c0a81c009b0da589ad479a5e9b'
        'cbd14276488698848168282a620030ad08f94ca623e7613fb017759fc949736e'
        '4f90212b328f7569e08e2fd698abc1eb909a5810c3489a46e215a8839fb1b62c'
        'ad7f6cdc8c72761445ce0b882812d02d01afc13a98c0a6c1c5bf7bff9430b625'
        '59ac5f4dc4e4d98de6601b9827aebb782a863741f50a0b2972fff4a92328c8f2'
        'd686337c914647b1ca9b1c0dd05d3d2923f3343227acd08b9b122aa4675c1617'
        'aea0d7faddec475117f5c39593aa17854ba35ec6677b20c2c30f66402aaab443'
        '43472b9b2468fc21e5532622a7712f67971a5de78c1eb5d982416a8ff51edcbc'
        'e253d7916f6e702aa7f0b94c78fe916c5008fda016d3552ead245b57bc709ba6'
        '53f60c41f1930a31e53719e4500ece7c99e82ca93c63dcf0d31585cb8dc18771'
        '40a6ef509ecef1e9bbef6661a52259b90e198db5d8ab054bef9a81a7fe72e02d'
        'a5e201913727ae16a516090e133d68b803b53411e86f6fd27224a435abf2f272'
        'ff35fd51b194d43fcf5eb9fbc0339cddda863f36c5a26e22b9d7c0e364d5cda0'
        '86e21daecf32494a4fb3e84e40bce5848b3d454fad05bc2abf261e574a1115de'
        '202480754496a86ef4822fdb3eebdc2c941b8208fb3bbd3b255f1aa760c2b0d5'
        'bfa425f930002386adaf6fff17d55424401671d1c1c6664e2f57328510424301'
        '971e0618c5ff096e90202486a8c9e35d55ddeeed5f0232e7abb6105ee8a5beba'
        'efa6119ed8ced80b499742222617561f8681aad4e7747e44c7d80069eb736939'
        '8fef2988d40c6bfe68575fcdeca36823801dcbe691c8c22c93494d5aa1ad9504'
        'b4df0ca1067fae53b3a607eb277eb02329bba0238659ce33575841996a09d681'
        '97b02f042e0532ac7a9be20270a29f6d1161f56367359023801601f9a5f52518'
        '28a0e33c76ac4514964b4aca708f52a319a2ac936d46e843854df0248026fc3b'
        '386fb595874416a67c9361ed291c6de847c681d99368b209ad7005a44ccbf0a4'
        '22865bcb6b967861916b66b863e97edcf6566fa6948cf7804ae44215dd22f1b3'
        '7cb20022803158031e018a5f0c9c0a057b96fe40e627a5d97aae509c3a7b9c60'
        '2b3c7e744fc02482e06162c56a86e513f9577767b1530f665db752296c1937f4'
        'fc6ddc004140787e10a9602393ca88adfaf6d6e31a8a407360795e85092aa58a'
        'a0013679ec35d8b4c1fe4bb32496702285270c1cd6943f5931ea8f724af576cc'
        '1554ff181bfe97d9c2067d14f5540374ac4024963291ffd876959b8f6a62f398'
        'bcc72bcf24e1f6522f90a17cc2531cd700b80a573b397024853c0dd56ca5bcc9'
        '49b8d3ea20ee1eba38a2f762d09ecb211a1def0bb63e8c52501d618036b60a0f'
        '7ee460a16cd3f1066d8e40b3306f3ff31f503f5bad212169f5832c112f65e21a'
        'ddd378d7c1cfc88d1ae1012c346bc24c67aa002db816aededd04cc125e17bc97'
        'de3a600bc5f8244d4a50003add729eaa7dc2853ed9727b483424fa2308e87943'
        'e02cb6941d825391b1aa89a8a5ba7d3af64d6afab8190b3e1da6763b020db777'
        'a0d2c6eb4514c15339ba00fcbb202db1de5438e6d1a544a33ee07ad8b3d12fc9'
        '49ed547543989f6a9df7cdfc95e2ab41f3a0c6a085e552b4bf7b504c2cb0e381'
        '435fbe08e19da2e63b08e1ada6b756bb9aff6aec4b9ae2e1afd80998e82d0617'
        '76c84b73b050a82c4828ae1b7b496bba758773e10b392316e21e76d29a874443'
        '9b4caa8f8f9d012be44e46d5b3cfe8cc76c02baaa910d0d42e6ef2ca9ade0a8e'
        'e1dc90d0768cba5cf060bcb3605833d7bd02e616470aad28098eb115b6142da5'
        'e15ae1c0187c1377dce07ad50104c16083536329711e5d8b5f887f33b47f4e14'
        'fecf07ced63bb88c785ec01a24839bf565fe0a306fc3e2bf2faa55933aa7fb65'
        'b234dd14fae7c487fc82534749ef1df60024858f89169329c4911b53335d73ab'
        '19e2991387f0c5220107f1f42c90514bf3f1e49339802485b0a3be1c2e030834'
        'cf6f8f1c7fa8b6282310ae32ff16b05747a3b3e3fe499ef808f2f822857ce685'
        'ae888c0494e3a111bb4679d5cb6e0b7e67f0daf9a25c1edd87c1038545002496'
        '175de0949c32122bfc375c7a52a48eb2b63309b3cf9e596bcabe85ce0039387b'
        'b3dff22eb4b96f205986d472217eb06c7fd66b6be3c5659c7136f545354e0c6c'
        '50f897bd7bcbafe7e24c9e1e5bb7454a8bc62bab32dca2f4b233798a1aab6403'
        'cda38d1375f967c9f31c50cee26cba2e4c9416b2940b49e9b43b4cf17e202dad'
        '32a7bd52b919889b87684208857326f4e954888cb2b2ea7c2cd330ef35853310'
        '0b84e397c89ccd64bed6a90426ac72e0d542d234896a9f5800500135f943fb6b'
        '8c74331c2d27c5428ed30a727849e6295d79c02daab3b1c5d2967f1ebebf7b2e'
        'e739fa69415df73e09b84c920de5223779631268b3ee3d792ac21398b6e251a5'
        '002ca7534ab4a96c38705d4a9563e590111d89d8b5319d32926c85b5cb713eb5'
        '5eb45f2dcc06d957fdb44644358029a3a1a42d4147d0b53474a61519c6b41e80'
        'b720b9b6882345549286682587c51b80a5bdcc524fe812802caa8f9908fc176c'
        '2bbdd739f8efa676186fdde2a145c9647b40057ac33ac5c464ffae477fbd7f62'
        '37caefa80034aa32a9a663befb5fd2461a224f9483b38a23810ddeeffd497043'
        '6c13f12d010c04adcc125fbcfdb59834b0f8667acdd7bee63b5032ba24f19089'
        '7ab84d6f70050b71ab34c4e39b79a2d52d4aa02678465175914018205f01a194'
        '8b71ee58128a3d3dab56f4154032b1ee5d194b1c8788eb56e5c89bbb7a876072'
        'c988c30ce4cda7ccc1a20f83ce9111d9908cf6ce8f7f133470635ed66e047550'
        '2ab60a0d4b68343d8fe3d62bc2c472d64db3f526cfb0a496c4a8c3b78ced1b91'
        '42e8e145c50002ed0707802db64c2a6ddbeb74cbb6d2e95286156c50975a8b2e'
        '8a4c1dfa9d53475af7d22ad62bd8a5d7d7fbfd26cb164c3e602bb7f736bd8181'
        'cf09916677a058d55082cb81f7574c8d60cbf0b9fa88345e616430feafe4eb2a'
        'cf9ff7bd802bb9a75c29440aed897d82723812aefc6149c7654cb8362b97bccc'
        'bc52960c03e7ae0b474ba27efb9d4e17a032b8c5e8789c597cee8f54a5930846'
        'fc781a15613aff643a064eb40b3c33cffb9194805ea8d49316e6c3e5f7800364'
        '216ecb6030bac4c2f41fb480329d909fcaabae114c7981d2082e088f15b32c20'
        '2a1db66d9bc8f202a115f5d3b4daa5587bdbad19682cba792b791e8871e200bc'
        'f5a735e0772dd304b9a88d4baa509d4cd1cfeb51a6ee8676288c0e8a5769b556'
        'c2c02db94a3722b1eaa225770f512b12bdda2eacd5d002eb17e64a3ec0c11249'
        '3830ebfd28359211c6a7c4fda417dc202cb5b0ee0c345deeb5c2a79323576bd8'
        'a1e2ccbc7095a9e6abafd622c9617855c427f0aa73727b8bf6201feffa2dafc2'
        'b350b43a8c3498f0f6791a6a281a0ea66b1147a12dae49e1ebc138a9fadd5fa0'
        '6e76e8a3dfe74fb1effc002ca74168ae7982ae3e581f0b409b6f6840550a9b0b'
        '13f03250042524edfcd1c98d528418552bec1a223730e678329f7587016abd0d'
        'b0db6c151b9b3c5c2bbdb15807814bd7c2de3413204ce6ad13050fe831606980'
        'c727664626cfbcd541f09823890471e6085f10f037cef7acbb7eba389bbdce9e'
        'd780298be3d9840ab191835b9f993c2386f480a96197e4dbc749b4334f1ddee7'
        '6bae11c65a40bc107997b12049b4abfba14d682386abd71e460061e2d3260fb8'
        '3ed3ac36d1aee5cfb93ebb88eedfbab151191a92a19970238678c0c7ea93208d'
        'b7391ab0065224424e84ba3075bcb541bc7b67c2f42dfa04e9ea8020801dcd23'
        '036f789b5bd6d8ce445ca0b8359068bb52bbc6223b992f3ee5aa9d4023865d2c'
        '2db540ca87f8be8e000463b3e4fd7ad2fe257f66b942adc0c96fdbee5441d2a0'
        '208029e03896ee95d19e14eee438f306110198af767e273402b9579049380ef5'
        '300986172b0e114d00199007860691cab8658006800221722d4e06800221722d'
        '4e06800221722d4e06800221722d4eff583d811775677a6a4a6a8dee552cfaf7'
        'dabde76f4dd4f7955c79e512a3cead9d2634bff3d082da6456f6504be8a30a69'
        '2574fe2d1a9964a074b32e34dae6001e9e1e6917b9f5cc219eba3250b8405c53'
        '68b3387b165ff3e532e45250f5ac1c994f752feaae13901ef2ac27bf47e702d8'
        'c8dd3751a4b7afeaaf6a2c219948a216618d0fdd72d5eec03292f8125d9862d6'
        '6b5d501e81d760c4c775e2807823994f752fed7207adfc1e257cdfe4911c0bda'
        '34c5743b8d35ecf5b46a983f7f406b2e404c9accde94682705a50b2fad35f5da'
        'c80e7321a995156589c49118974f3b764a724339b80daa2fba2cce5b18d8bec3'
        '49caef114aa5378be4f8c5e6ad6c53bbd38a19294aa4807470c4ade97d000988'
        'ffc0210b766adec0ffffffffffffffff280780090135eaf7281a8002c621a142'
        '2127c2f99a99f00c4f9c2bae55438537973488b0258013909275b0f05ee44a6f'
        '6d5675e96290fdd96c9680d6c97775eb233602745c0cdb19f10020865aff67a9'
        '2aa7c1a1b3d9a6dc533d9c010852bccac6075cb9022dbd25fb4e802286347b91'
        '2b3b4e2b2b01bad6f0b4420edd3f21621ce5ddb670b1d6639b3c0af646802186'
        '34a5e643069164422ab28d1949ad666c56bce5d675c25807e862a38cd2f9e4b8'
        '24800b4634f18b5707c93b9d07461de6d3bf22e061cd887f70143a8a901e2ea9'
        'b0e443bde1238633cc77bd9d62f8659e77463c722a91c5891888824275f83efa'
        '17846a7767b7cfc300208634b7a6fdd775d9b94684f2cd89870f948c14947a0a'
        'da0b4440067926b56e382480110ff73a0aaaad5fb98528dcfbc25a779acead6e'
        '7bf8c9b18dcae733e83b94f54942b6278636c1b43571f4ace927d78b994ea6f0'
        '1dc2f0e49c43bf65168cc1bb1b7211269c0d9af5e68f002080110f4414a9c469'
        '03f83dd0374e555ded30ef483264cb84c5175e10fa051c1226800e3ceb7e7a23'
        '8640520732d6e4bc7f7222598231c5b5555ba5078e0f48389fe288021eaf0024'
        '801c412fd388b544edb4b2976df17a9a3b484d4f5c7a7a02e9b7b66d31d34d92'
        '4faec3202286327f2e20d05848679a9f02b9b4cedb7dfc40fa7cf82f8f9776a5'
        'b866acc204e950258622371ddf667f7af414147fc16558952ddf39531716ce8b'
        'd7653d841cc07d448002f061802386225c73a62359b700e6099abc726071330a'
        '3b2796f6c728f1667c3485c53aecefdfc8268622c26b8feb2a967fe9a9586c99'
        'e3e14ea45902f6bb745a65e97f50692fc779ad2bfe708f2025868b43a956df7f'
        'f4e0fc746b2284c6a04b6649b23eb25d7329ca75c911e5ab874756d8e4403c98'
        'd9ab63e96fd85a25d8b3113c79b20233958a2ffaa9dd8e6f03112260b6312d77'
        '8a5179e6ab5cc080cc01856849cd42940ecc9a12664e9b5428dcc034a6d32a2f'
        'a6255beb9623d6cbca68930cf018e00a3b803117797e862f67e10a8303a90846'
        '99cd75660c7255eba2d4b2523d5b2fc02cabd480fa2955ba095f7d3c4dc81d07'
        '34fc1a9cf5ff4dcf8672f37ca58a4226cd993cf16083d47472ce33d38b27aebd'
        'b0bd885dbbc570a8d7be273be5174f3f3f6ecdf475aa82c7f77a20a932ce3abe'
        '59bd0972cf29ae62a8dca5a3ad0bb0f52cd9794b0fda8c6b146f82ab72f8ab00'
        '0a71e350c1f47416e5d7a896284b4030ad03b43f62b9deed091f82b85bb578d6'
        '46793e20a5ef7181decb7f399a5b987e32bbecc69c2a0c73ecf139e24e45ab80'
        '2dad85f4fbe212b2be0558135abab2e3e23c1c20c5b2bdbec6bd7efed5c7c75e'
        '89f86e09b2ecbbe96f729a38fe402eb09fb3ecb8b1ca7f7a8af356c78f91d746'
        '5c056192d7a8d317d9a115efbca78a72c1b66ab992c3603fe8aa7602c02fb3ce'
        'ee0d2f7067fe7d1cf21dd87f715388e0f7c6979154ed4769125777e303c0ff39'
        'c3e8f1f954fd006b84125f38cb30b3e159c7a6637a6697c1093384c2955ea152'
        'ae290104308dfab6c9d6583166e1f1fac41ed67d5acb279e78cef7cc5c602fb7'
        '7f6c9065fe9ae9b8feb729433feb0acde04853533c61d220f4d7309f6d459499'
        '8c28233f3065d97ed175e5c902c02cb0157333f3480cf5e67d96aa41588f2a51'
        'cf9d862ce120d4070dcf3e71f47a61883878bdc5c64c224a22fb4a2eaeb6630b'
        '2a467f5e08911954af64773f99b1e9aa7f68a5c24e1bf83f5f6b31815551e09b'
        '04799754c6f4bed966ec30b73fe8bc9dcf29f32ba3d970b7f52aef42a45a7d7e'
        '8bb5e5cd22197f50c11937d168b765962e5e46b4b355daf91cd8e825b522ed83'
        '7e6f5702fe535e305fb3d807f90442cba0874901d38e6a4b3d39e5b821bcb916'
        '5c2db56055ce6b58ca7832f6ec929e10aa5b7d3be2e225c89d06d476d850dc66'
        '0810ff8c4ce1ccd66909bcae13e4c02db6f6c9e0c35af1a7a710ca457d4b7810'
        '1bb28346e33c7ada97d29fbb441f7552688acf0388746d62d31fc227a82ab7d9'
        '155c9e976896a22a5d2c95c8093d710ef7531e569363be9595b0f0712c060506'
        '8d5a08ab16a259c02bb7d8e0d2d54c6f5aab94e81a0188c72a6751df215f9560'
        'aa1c053dfdde7a91b0f3a3bb474b770166a0930a2ab78605496d723a038f4444'
        'd3a0ab9e24121e92ee7a5fbced37b0a7bc11fa3dcbfa24446792aee8c129002f'
        'b6b389a1b0aa1c3ff3fb11046544c8051381e233bc9e1bc4d5e6f3ddff6cac29'
        '03225f74a72cb7dc4d91562c64e0092fb3269c4b5df3a382272e5d31a3570c2f'
        'aaaed14ca6b287f49b38d02cc656ae73c1b53e5bf486f57d3bf7adee6e61c02e'
        'a78ef964fb5b4ebee685b5667974cb7c1e73a77a292e2ecca63041716a155729'
        '114f94b10c366ea8d9dbb0f644002ea63e2aff71aebf27d550d52e9bd161156b'
        '4ea46d62b485786f21939b2700cd5281e476a2ca6feb22a442ce2d6ac02ea824'
        '62cfeee8ace3bad273ef65356d36de5b3bbfb559040504cb5c754b760b0da1bb'
        '97235cfdd2134deef550c1d82683ed72437f63954858508104612b71d0b61821'
        'ede6edf74d565888d0ca249e681072427d410424918b58ca55f96468ef80aa8a'
        '8d182c4a9b0469d1f731bc1d1687a679b6da4e70ed1b6230268440eaa82bfecb'
        'e84dfb627d5d7a15447b95936a9ea56475f6cbe32871bc8d5770e39f5209c025'
        '9417ccce8c1e48054f7ad806ea80351753a68e0a99abb35c54a2f00b451da1bd'
        'b63267df403db5b0ff2011da44b77c7fddacf2f015a4b45cc7d36cc2c08c1b9b'
        'ea34bee10d01be69a87357f5ff20d0da9b2a0db8c7e4d96e615b6f8fc4da182b'
        '68ad8030b3c26db7448d47098c1a5543f7471af2e53ecb15b9062d9826e034a5'
        'b44d72292ad54c13a3618d1de6792ab154562b8030b06939e04c83a39c36c65b'
        'e9693962e81195f8754dbdf251f369bcc98b26f3d054ca1f8f305092d3321905'
        '63f79bbbc02fb2233490dd566327457dbcccce7144efc0f491c05fd43abbf7b3'
        '52c26e0fcacbde3d8dc2d8746f7041a92913556f002bb18cdfd94056b1e9a395'
        '654d40fdd4686d85d84b19aa610032556f274c318256d1bf91a670a7a991e6af'
        '402eb24f27489222431f1cd16d4a24b0c6985091131961e1fa0addccdaea2825'
        '331b30d03e785a91d10d4649d3c65fc22bb3ce78a0630786936532d7715c9bb0'
        '6a5c7c8878c7236253fbfbcd33927ec0d06d19359b5346cbbb21d2602bb6070d'
        'f05d4a215cb303f27c7d09fff78893068689828357d3af61af722436e85ada6c'
        '025fd2a5d7c638b030b62338e4f4fa9a2aa9b41ecf60cbb307371342bdae30da'
        'f43fc83d018c481c423277b0f22fa5fd55695dbaf83997cda030b695956ad8cf'
        'e8d469a7354f7c175407bf153b80504bd89e7db6ca84e3efa7ba276c7301d898'
        '5d7fec83449038edf5402cb4c5d5b271aa3dff7fe51a72f28f838f815150d623'
        '866a16810506657187d84f809d44594e8e5054890dcc5a30b1719555bdf0739b'
        'a23cbd46c91218500f61fb71d196c56d8598e3e4d970266ef94b2448653bcaa2'
        '803b9a3197492d4026837393cc3c487edbaefd11e5741d1f5733fb391c03a968'
        '03e9add6fc059ace6a89dabeae7600248b2e20380c7d321375752b8ca6ad717a'
        'a5119b0e2376849dd1b7b0d1916f453d24a1e95c268777cb4f1b80df86ddd5e8'
        '9567256d65576bda8629397191b85a43a186723c97d747304338302786c42db6'
        '644f2fab4a5d3b21caebbef3a24172218c377b182ae4652db99feeec365bd6cd'
        'cd530014862061f2e2162575392c29668d5fab382ff2358007860691cab86580'
        '06800221722d4e06800221722d4e06800221722d4e06800221722d4effffffff'
        '2807861bc3bb9518e01c8002cadaf92b4dafb041e1ee616bd49e4b1d263b07cb'
        '6ad63209390025866399c2000c0ec0f1eb903b65065800229399a3966cbeddaf'
        '3173aae89598cfd0e59708e023867e157a9d73627e27d08c172fefd73bdadaa0'
        'dbbeda4101bc4d94c7ea37e61160fcc02580239530aeddbe97077e5ab35c7f04'
        '9a6a28f391bd3b73d4c37ff5b2ca8946d7aa27b26a00258058ac83eca3f13d9a'
        '75d4a9171c4eb26ec5e7a7bb4e6d1dc1d3ee45cfb888820a414d3c2022870743'
        'daa5df2ccd38ef0c3157b4223224db21675db3d318f830cc44b1a72939151026'
        '805f512deed21a7f7e98ac4166d6843e21993fdd811caa3f230af3d274eacbd9'
        'e89ea0ea6dc03799f3dd50eb58e593606f6808e7eb7b948d081298c5e509eb2d'
        '3f97c324087b75951c09cc08ca7347fcae913f6a15f8c2d6dc21bd98ed4036a6'
        '9a8fda318d6630f98218127253ddbeb74183c61b09fb59aa96deb786650ed75d'
        '074bc0da749a453e0bca8d902eb4fc549b7fa2c0a42fb464cc9af44c8d492b4d'
        '41a85656ff20aea8360f1906619d57b10656a15c6d84e014f9739d187ac8368d'
        'f0b2eb00402fb8234a44769f5308faf6cdf20dd8356a8569d8a74b5133c80277'
        '90e14e9023e78691a55b2e4946e726fabe7c248fb02fba32c737efdf59acdd46'
        '9511251060f6deca1167e9465a48314682ebf7d77193e1371882d8dd946e6527'
        '23812752c82cb7b994f74b4b6eed12e11374053404fc8894986eb7b6ccc0f8bc'
        'e145e0228d90042cb0fe451d2bab1332c8282bb5da3fe68ce59a0a839b766c26'
        '9ae34dc8e81a765fc13bbae9eb34e974bd105ad2817efbba1adedb4c7b3c24b5'
        '03d26d58efc01ffdbb0422b2af24745a59503ceb9dff4de28c924db18359d746'
        'f44d8027b3b719741539eee6550d8341c39877901725eba5fe1d99dc3b971dcb'
        '4174c7d148387bd8038de02fb56183a049bc0150bf3ec72acb6f782cd60b7824'
        '223e9ddff8cb178db4ccee19decc508661dd59fa6bb9f6ea3f2cf031b4e48bdd'
        '0a99565bdf307c97f800911535e9514140f5e3fca7cbfa0ce9d7e3c3c2ede3d1'
        '2a8c9b1610c661aa3661f4b4482eb541c655f07bfa60ce116505bbcc454dba18'
        '10670b134642dc438153f8a3d8762baaa366c20d495b6d89f761eb0037b7f118'
        '9e7ff1e3901dc6449f1a785aa27d23878d9ebb0ebc5023ffc9b7d4a915722762'
        '37bcf0fdc0bb8d46c1a434a25adec1c83a0e6ede26856440a222c6f005f8e945'
        '19b7a5e89075062050a905a07286f017c6f5d68fbfbac97e5e5f4023853c6297'
        'fe17b3d85c5d9a9ea460d0af49af32cca5319ff1da1eae90c77f28ce57f28426'
        '9563acb467ef8345d4a67021f2f99877961c7883dbd669bdd4c228a97e8010b1'
        'b8caebfd93c026953ff6894ebc57016e04b124a293239ebbb11f182404639db9'
        '3e0cad7ff3b96a3dd2069898c0258505e9a64fb43318f1257d3d496bf75747ce'
        'f5f659746677661dbe2bf3bc6488f7c26082002bb6801db9668df75157a06070'
        '86abd3a8ed047f218db37f64d30a3b8faeff729d5b0d7dffaa52a9e62a267c2e'
        'b05cf5ba0b0b5861a70cef10daa0ac945e24179e8b155985445fa5092e2197bd'
        '3067ba909aacb3daa04440b92ca02eb0811ef4f6c5a6ee2f82c4b490bf9429a8'
        '1553a0e04b59b71a58da0669dfd71b5558e906b4dd61371d3d57e8dca230b689'
        '233139464237aa6fcc422c28c42e3db14b29b3ae9f217da3d84f5e795a9e4a29'
        '5c83559596a9eff3137f143fac802db15dd8c3a6efaa7bb82c7fe11fd82a47c3'
        '4fa05091fad8c994be65d3c8b10b9b2d9327a45725e0dffcab07ae802fa7c395'
        'c5628f6b28d5c2e3b2211843074c3717d3484e6824b1275b92508bb0ef19f3e4'
        '8d68cf90bd9de87f33875bf02cab32835313659c1fc0188901370817d3e6931c'
        '9277d262fd217e3bc4feaec3db8a39ee45a8ac4e9f23fb57802eabc56f9ae8fd'
        'a2846b2167fd3de54708e6d216431e2dfb26931cee646738d2eeb3f75be691b3'
        'a579c8aa8fc9f8c02ead4b4745e4019193e7e6174f18980b49e441c6c28db857'
        '87971a3b09e153ba557890d6f9efe80fc3524d16b6c2202fb1788bab441f9bb0'
        'a0e1652a4985b4bafec4e057811db55a914f03e4e68842de92e3421ab52d6a79'
        '4f953c3c742a902fb0305fa2958ac3acec83f7d77f2c0e723a22e7afd422fb99'
        '966a16e16d77fedfb408fe7092484cc8521f86924680802eae9ac72f17f0ed73'
        'db583d794b8e482ae520561b18bc8d166fd0b159849fe35ac79b0cf0768c8ce1'
        '5667984e99d030b23089c3f7bb750f9e7fa3e383180f3e0296049c3b99d30823'
        '61959e9c857dbb422fcb5c6b032c579c793233be29b4002eb733497e6c4d06cc'
        'c2fdef2a05afba27968904e2d051073c58ac2ea7fab37ee29aa04b65d4b99e41'
        '05186ecb2d4030b8ba1f2dcbd57d28601f4a2bab8d7ca0f65e662f0ae3570d84'
        'ff43fa563cbc7195aed7a126a7db84fe22ac6b8865070030b5af52838bc880b9'
        'ddec1a9941ef713372e84ad9dd254507703d31b90519970cbd8d82c37bc58d0f'
        'f55c196eebf149c02396f2c320bdba880145b3fd501e31eb5bc54039d5bd0dd4'
        '59a5ca9ccfa8594550b369002685939716cd6cb3ff3ca4aa8a1ce91218c678a5'
        '104bff983ede7ed65176fb30ac0a9310ddcd0026858b3dfd3c88195f894ee83a'
        '290bca305f8364c22c9fc2080caa2fff0f6aefda2ecca18e6c2025970faefef8'
        'ba0f80366b83a1cc29623d4a9c0df4b4f4d1ccece5f121773730b689ae4c32ac'
        '24858b6b528acb88921db70c3b1de7b6dcbedf4f9d076b8681692b5d2128192d'
        '30668cbcf026972443dc312c5e52d2771a1013ab46a897732b5f9ede61f6ac83'
        '8ccf3778b1cec7b7c17eb760249743ec4df77a05155082f921c45cfc1d551fa5'
        '6cbf2b4a1080086678b8ec3edb4b78bd00268595fe1262dd5f6e36ea429b2132'
        '0e01de21b2ad6f69997ed6502554fee369ad00c6725ada8032b85b9a2e24c450'
        '6f6c0e0a6dfcf9cccb81e2b48204ce981907045061b73a3d77679f68db215b5d'
        'dfdbcdd65da38fdf289b2c2fb606c25fbb41595763c167a19f45b37409557498'
        '9cec391eb0ce25447e2627a1859ae9d2ef0e989f28b71af5ac66882eb6233d81'
        '625856c717ecce3ba58a57e2f6e167364ad5d98b204d735f9a06ad3804f7ffcd'
        '3cacc7735c3797a7d41031b6ee025fc257ec26b1c6ba6a8a4fbe1f7d34323d24'
        '7cd9a5e00f2b293371282c7aeb5eb0d0cc1843e73084b5200a77685430b78fc7'
        '4b851f2b018b660b9a47615799d3b7d0d10cc6f71087e403fbf9b9ea8f37dd61'
        '64ea7d1572d8dffbb1133dc64032b93911a7698e29602e107004bd98538736bd'
        '3d9821eaaa936a5b4c4c6e0c528e2824966aad22ce28a9b991b32646b66207aa'
        '39af970778ceed243fee38cb7308c8f975d2460036f776c1aa48526221bac6e0'
        '0b4159b20b97e42553273eb8823e720e0d7936ab3f9dabf698e0268e04117c6e'
        'fdb6dcd60d99fdcbc8b1c0c1b436abcfa8a2a07ffa82a1ed9e5814104307da46'
        'c025815f125efe242de54f4b879964f4e44537e30567b150ca8cb4e52d10056e'
        '12443393d08120218073d5e2b9c38a5bf8f6ac1e0128f2bc3c3a2dea8a0fc556'
        'e7a0bc3d8fc3c5f3b02286abbe20c077fe4d7b323466ee178577e02dac06bf44'
        '40271aeeb68db74b529d818020803866b423a59cf52da67ba5230a0d72076860'
        '159c996e5c38b30b35bdf4204c268035b7c96857454de79d69e5da1b222ae34c'
        '9518575e9b7c85bcd9db249fd5fa411d1578e1982386aa8671bc1f0d6647985c'
        'febcaea44fa342535b4c5c1a153d640cb66e6baf8a86e2b0228678c197e3a6d8'
        '86169f0b21894e0e55ff149aa5deaffb21103d77db8c4d831a280024863c4e77'
        'a2b825ff703f394798d607c9ddea7034b0190fdc63eb9ca9e96ffc6c425e9e40'
        '23801d3c2324a5dc7ac611d6a3ae1c3ddffbc155b665089f897b8a4ae1b1b5c4'
        'e4c9a2e00d800909beb4c9e3316fecd8320f07860691cab8658006800221722d'
        '4e06800221722d4e06800221722d4e06800221722d4effffffffffffffffffff'
        '280780090135eaf7281b8608785bcf0644588480644d4457548799e7a50f9fa2'
        '17e9e7e35c25863c253425e7051d72205bcde889b52cb32ad5c4d4407be9f991'
        '0fced058bb80de3d3ee80022865d58160d3fff09cb27e9bf6f559179aa8bb001'
        '5043239adafc5d82aa10fd0f1f0024801dc56fb101061cacd71bf7603b16b0de'
        '781e6686c192877d48129061a4bb03e78f0a4e2286514119465391dc12edcf2c'
        '8729707cdfacc17ea8ba9f2f36554a2bf9100e14ba88258011056f88cda0f367'
        'b5c263e9b848cdc13cb4240a0bf1a7d7ba40efbbafa233355fc1648025801274'
        '72eddae5ba66b81fd1899a2168c6b1b73a600aa4d83a96ecf99d75785ad2c31b'
        '23a0258048469425feb09478e242fbfd852e74bbc080b8f47d7a0755ed5da2d7'
        'db9fcbfa9504bcd02480d411de54e928ed2fc45f3c02006038bb99e54f652e51'
        '9cb03b9f12a09740647c8c7ec03db0ecc1ba2841391f3bf0a7973f3ad60f1941'
        '72c6c7e6b053ba2a66c590df8ff401967306f1d83118586a1c53b9156622af8e'
        '3fe2f1336651611e2506982eb3d3b89e92923fc4bbe7a3023655a344650c0588'
        '363047e3cd97bbba1cc94bdfc8d855c0ff7cf90ce7a9019945b02dafd877614c'
        '6335991462a91a5549732b7e9556a6104dd33ad1085065b392d093078e812b3c'
        '1ea6d1dcfb6bb1c12faeec94807d0492921e3fe787095fcf6a41d3a3d474bd9b'
        '591937b572f6f3747dc877e6df4c125528eac940db392f602dae4e6ceee3501c'
        '2d4e46f7d0290359594fb1163f7b9902582ca16578eabcce1e9b73df3dbf7875'
        'b4a8b6b637802caab60fed4dd52d4756e3c924b78c5669f4e5d80389254f928a'
        'ea3ed6a12c63c9651668c1d5c3ef5f2fa7ec8029a9a3794f0fae6991863e0c13'
        '278d9eb92f51a12373137f80412e3317af8bbb6ac59a3e045804dbaa202aa973'
        '41edea4b43195b15e969922c9443a6065e35fff4440101c3d4c607d20fbbe7fb'
        '267c4dd999875f1829a99e471a915bfb1b25aa99272658b115171dd299ed84f4'
        '4d69c525b2eaa01d75f1817353ac3d0e97c02da9a59af084d118b357c36fbda7'
        '3c4bc4a43c6de03c2066642688912b95c8056c45bb9378b832b958da00aecc80'
        '2eab1a96bf6de58b4e274080a6eb1f4f47d4844362905017f852c0d456cd1606'
        '5d09e56a2e9b79e2afae1dd8c007ee2fabd4c34a893515254648f213f1fd3654'
        '6b630077e67cffa9767f0af3a44361f11aa6739b9f8c58c516b0c94e5423c02c'
        'b0fb4ad62f9c9064e19ff76d3182ddf2bec369a9c695358620e0fa77cfda3de8'
        'af45517c9ce48e3874721e802db33b05f9d064dfdc2f2498572773a4aacd8478'
        '619ac806e9358f88a0b26f4ac3044654597949aa1e1aebba3c602eafd55841ee'
        '2b85e70cff923ad4acc69c0471a64b510654c66eb8203296f7bb0aea0587d699'
        '46cb8bec27c18f9af02daf3a4b18c9775659a0c154a82aab43e1315118667643'
        '0ada85b63330e8a2d3262393a813a7b7405cc85305ddc02fb53ccada80ed46c2'
        'c7a3cdcf3512f1437932ca9e7f0c76c9868a3261aaed33b68f9c0c44ad35ea2d'
        'a7930c1084e7402eb8178b7cb4e90e2c581efabc7deeb0f9eebd33751237192b'
        '7752ff37e16fdfe8425870d6d382c01118901a5df7d82fb9e53b061e6eb5fc77'
        '50d83859e4c57390aa249a4b382a561b6ece3c645aba8d4ddd428e618d81d4d2'
        '9867bdca268032ba8a58ac474929f29a0848888bc32cfce2fb0c41000379f5de'
        '094456d9145661474ebd5bf6dbc22914b95b683b78712ed4102fb867f940e284'
        '6021a2c3ea949cb81513a3a6c753fe247d7a758cea8c0cf05505324d8899f289'
        '1985e86141e8a97b202fb6c9014281fa8ce21d7d0229038cbfb089c28d198295'
        '161be6972233677e88619a1d750e7e0f76e140a7179366b3002eb4afbaf4e381'
        'c867922e2a8478813229cd6524d7e5e84a65c06dd75fcdc055e58a9ba4387047'
        '7b020dd05b6ced3e2cacf0ad2278846ffb5ecc6e6d7f3fd3f4c838174966261b'
        '44e5e1df8c32c5267c99765d8ac87ab19658eaefc02caacada3ca1e9b286d2f8'
        '4b68a4389b1c0f6c6f1f61082e76332cf988300ceb04688370f54815a718559e'
        'c1162fad4564a2d07bc2c1db7a751630bac47ab96bdad3a8240d9019cb8dd726'
        '01da036703b55774cdb3b41f877d636e4a8036b4c33dac36a1607224060c3986'
        'a50c78336fb3dae77f391d2cc8f923a3e2412fcf59ef49e12eee4da59ef37931'
        'ef13cbf37156aa1de02cae730a1d73bd5d0a9d1845e306e8dd0661768b108c30'
        'c8ba3a521dbea60728af4fd2064e8df7e26a502f58072faeea4eba7f2bd4060f'
        'c082de311cbe7ed3e8d25cb9fed8dc60b90c866ae1c3cc2a821dc71965808a81'
        'b5a711442f002eb253ffa7bb80117474f89fe87560bb49792462c6325bfec6bc'
        '68fa8391637ed2a468f69d5a4f92d91ead3fa381282eb5412278ac2e479e9146'
        '84ab07806066a37b47d1cc834be40bef5a73326cf117c7f377e8663240830643'
        '209828402eb98f35a317cf3d9eba234172f15d6c01d7aa90926336985af89906'
        '5e890c4ead4831d4334b748aa6caaa1248f8c02fb93daccd08cfd04622a02476'
        'c06611a61189f92af0d35f5d92a079cfbbaa7f5361ed8425dbdc63296e9a5fd1'
        '0838002fb5732794642d9ff6fd69e4bcb47a77a2ca13d4f063e1f5d7bf84083f'
        'd4fc43190bd9324303ac4d049e227b5791ad902596bd6f19a3639c01c16ad36f'
        '8aae5fe7bd78aff0758905051acaa14cee10c2ccf0a21525a024858b6449e2d7'
        'c34751030b976d408ca6e19154c037987f7f225fabbb89032607c6dfb3402697'
        '10b8c44996dc8ff2f930fad44b091f93bdf5aa1951b816f31d0b6583d9af94b6'
        '47c74d3f002696bd791b87b4b5a3988a75ac5e5e7757ba21029acf5f4fb2cc40'
        'c9cf1c9b600223c86caecd8025970a5ee693cfedcb84bd87e2e433078efbfefd'
        '178a108a62bb03211883e68ed91e020f4e802497188ac1b50cd9246e357a96c7'
        '73c21f45db4e0a3b040e0066ca245ce0f795b494a108c0228599b083cf8117fa'
        '2318dbe74f2dce8a01b6d744d532744ec9bbdcbb11c15d63e4b830ba22590931'
        '7924818c56e30fd9fdf3c8ad07f0f12d52f3f5276e66f02bb83eaa74e970f87f'
        'f155460bede901cb162fa02db6f1532b32e6211b85bd47e772b7dbd7137a7771'
        'db4ebc254d41fb4d26b98072e0498f36283733755ca8ffa5402cb6f1b8f62e09'
        'ad48a8dab3880b90e2db809fc95982a05804a94a3ff5286a6d40bada9e9456ab'
        '789c5f43cd5429b606513b3c895edd157023d2d231e69a4d7fa662111924c5d9'
        '888d1f14cc6ef711ce273378c617aaf02db5d197b25e8d7ca0c77291fd7af4b6'
        '8da446ad4745b787ea5b6ce68dc0188cd9946c69d7b18b65461b857e3c8032b1'
        '7c0da0f2ccb486fbf83f3e7bab35d679ee198772009d9439b5357128e6c94c9e'
        '864fa5b81f59745fbac71ff2092445fe4033ac636404b9d66bd622b7ecc83205'
        'e33a609b73965e1fdabf402ab46633f501f79f4d7fdd84cbcae11f65bbd071f9'
        '18b4e61482268efa3cc08ffcfbfb1a914b83a14a03dbf16a0d39d51183ff70ac'
        'a782fa890c5b9e83a83fc40025823e5563d86bbe152fbc565a02ccf715881967'
        '5ec2fd2e2822fb807bec6f3c5f7ee7ce6c482480b5954901c5df2097726a0a76'
        'b8e30f84e0462f8bb4872ef85be1565853153fc873fc802480403fc45252bba9'
        '4287bbf5b153891b273fa53a0d69d1918a7594fbf8b80894ff2937242486833a'
        '904ea9780af5ea3bf2780c6a4d44a0d665ee471baed73810fa0dad4f2cc8ce9a'
        '3c20801dcf441b122c7e5321fec6b9bd704555af42548521ca42c73a266b18c3'
        'a04c23802a42fae5ab814ff2cd81b4ca43dda1e7bb8496b06e1a82eeac7aaed9'
        '8be977e3fa2423867e3eb5a12a98bdfabdfebf39e5431435087231b887231246'
        '3745c757f647f22d2db824865bcb25fb7580b26c011955dd360dd50ad89407c2'
        'bfcce28b7518a012b80983fd8bd78011800c4e23080fa50107339ca913c07964'
        '0007860691cab8658006800221722d4e06800221722d4e06800221722d4e0680'
        '0221722d4efffffffffffffffffffffff80f861bc3b5725f644f3fdae43246ab'
        '00118606c7c17137c0bf8bb6b009962ecf29e60f86068f7a096cb2c52b1b6a89'
        '75df4012860690a35c07ae4d2605e36448b490408d901386068c7587103b02cc'
        'e4ff6bf0c50d67376880138606882590189531aeb4a49bf48b87aa83b6901186'
        '06891e8236f6ac8d088dd19abd2bee6c0f86068f5bc63250c02a89510ff09500'
        '11860691282fba00c2071161ba04d0605c700d86068fa76a6b27af0988b7de76'
        '0e8606c6a25252ae439ee5a87c00c01086069000621c14fed1d0602715077b70'
        '3b8005d8d5be3aeb5a0b0c942552a7a99f7f7d422a5cfe3c4e523c0b191b880a'
        'eb63770250ea237b817a5d5b2f356e9f289d8391fc453a55ee0d82e34ea0af22'
        'ee3536d5176a3df33b370ecd035f92a940133a03174f91f7304757199459033a'
        '317063209e8b5d97949f66f77f86518c07ad987665b0edbafaf591fafc7fbe06'
        '401679a0ec4e9a2d4926003798ec50c3bc942e70fb1caee955f8712eb0fa9d46'
        'a2c428c3df4b537c8f60bfa22d42792939bbe82b1b96becf5077d80aa6a01a7e'
        '926a043198e25f75147af513fcef21fc61b8527742a8b860047deb3f19fdf2a4'
        '8d19a8558eda2ec727198eef37b16d4124a6f7ea683598e35dda982fc069f4ba'
        'b3dee04f8cb2b0c1e34de76f0e5208d3c1329752740f4b5f74553fc512e78975'
        '6cbc50c0025b609e6806de2d98e25f75147aeeea7cae4cde2c894c202fe72f84'
        '8cdbe8c6d50623ca4725f5dd8586d0cf0f5ae33eb11fcb7f103398e35dda982f'
        'c0583a81c58e14cadd8d7fd580fe42eed09565a6a9ca6771af4c1cd98a8e66f3'
        '3785d7c288e0e7ecf7a858fe402d98e25f75147af50c1dd142fb78728d57da4f'
        '037ea29c943431c0ad3216f86c2a3be626accf9bb319b3b297eee03598e25f75'
        '598ff0316a69181e8da096361e086840f7c02c1635b64a2920f8e4d509ca8bf8'
        'e4ebb3575da388a70f7ada1fc45f4ac3804c98f057565b47a6153a11eada0007'
        '0a19d6d37bdb31463d66b9f76018031b962e5094c9f81a50f94308d49a8b293b'
        'b7264e1804dee1006f35d65aa0b19290eab68a980db7ad763f76a01299c04798'
        'cfe3e28de601a3c0f0ceba5ccf1ff69c3a92273000b97853524f8fb9daba9caf'
        '6ac3dff2df74b95e9a1a44e487bb33f3e3debb340023568efeb7a7549522f9f8'
        '6e0dc1a1d70019860672ff8102895fc510e2c9f916a933263caf6102784d9d20'
        '1286068673c20994a0a76491c75800bcc76d400d8606867dd00a33e95a3a0719'
        'c011860686709f05c027dfca65ab112d76c05012860686827e52216465e335df'
        'ec1986045aa40e860686839640ee2515a50bac9e1c14860686731f0bb7908aa2'
        'e9e217214d4d72188b4010860686827da04964dda53a6ee637f3d81386068673'
        'bea2746f5e07c4689bedec829dedc00f8606867ab673c3ac3c100ae3607b8010'
        '86068677e60e97b3544e898c787935090f86068679631d10ea91ee27ad4dff10'
        '0f8606867df2403beea71be49b666ec01186068673c2e5dd2feba5ed2231a0bf'
        '5b4911860686884ce8fddb1e82fde96694775a60128606c64f5432fb8287104e'
        '8da03be2f219a015860687058539163dc83746e9f8eb24261551cb5b80118606'
        '883115576e267cc131ad50a00b42aefff80e861bc391e65c6bff3d66a6be1d60'
        '1186068f947f1d10b5b044661d6f159c79c00e86068fa61897eae8e2d9644a49'
        '391086066eae56c54071d3ab50baf5a3b0f80d86068f44dc1b4b07ef9b038480'
        '11860691c3a3cfc2a53ad8ad993f4e1f7fe0138606c7ca8e16a8a8fb3dab125e'
        '919a03b5b9801086068f93faf51bf044a439f93d147500108606891e7a22ed63'
        '225ea74d969991d80e86068fa8172ea9befce4ec9e9f00128606909d4e231a29'
        'f8ac16edc280113d5380118606895669ba0794df24b4913a6daeedc00e86068f'
        '8ddddcb95455f088b4434c118606ab603d7595c56254bb9814efa2acc0108606'
        '8f7ba86fee2a3ad936a04f96df400c86068f8f379bc7137b92d43011860688ac'
        '9edf893c2d80cf2eb3bbd0a0f01386068f49a50a81b1d5ef9c69932cec3d8560'
        '003e800755d7462152f46468a479020b95c9b1333542c2594f35ad68bdd97912'
        'c1fc8a549a6a03e05292097697de82e6371b26f4b540e4c0a1fdd4dab7024060'
        '4e9f66c6dd47bc9864735058570d15c84f66a626683d5f842f9eb8279ce844e6'
        '6753e3b8867c125f7aaedd3cf183f8431c98c60eb5226d21c7958fc60d0fd899'
        'b9bec73b12613864356c51a3d868683698ec55c0795fa6ccfff270448bdd5c99'
        'ae59d4c4e0bc0a8611662273d3cacbf28e5802e542afb93201d49b0e78e8e789'
        '6004ec23ef803398e35dda982fcc6eeb073335d08b114ca4e2b62ed499857efd'
        '6591e04f6905674b6cb070cd3fc50c2c99ce2900e877cc0d64404e990e9694e2'
        '2dda4dd56c4931b7cb632b7ac288076567e678723bbb6354972c99537e460e75'
        'd8220783d30c034b91623923f07f618f8702963aaae6e7de25d71ac652c8a9c5'
        'd92607cd1f46870ca04d98ed03d8b49abb6f6dd628a56f3b55dcff3707505916'
        '3e6591ebd64186824f5d3dbe8cfaaf275e275b6e32b2e1d2fa1a6c300c4a5877'
        '670c7ba174e2973f38e1f732354fab7dc02c60fcb35f404d9f66c08f7e205f4d'
        'a60e4df6e874c7c86c43d86cb34b41d3a23e34ab132057aa56cdad5e55f39fc3'
        'c5cc721fdc79229249c0710812d97eed757fcbb78e1bc429f6f58d22d461b508'
        'ac9efbfe003798d47f75146c624eae8c88b7d084e478936900c812d86111e1ef'
        'aaa7d41d1ee1d7234283b7cbdc08d2b17f2ecf90371310602ff6fdccb83198d5'
        '7dda982fcc6eeb07c731b44152d03bab29e6b5b8515f2a547860a47e1cbf9cac'
        'e4becc54e6627acf1157802995988a4b98e277565b47a1b5a3323466fa1d4ef1'
        '0c21fd72858eeeabbc2e1b2525aec63d6edffea031d4e4bb8497d640911ba59b'
        '4661ecbe2b306237b49a320e6574ddc7556f116d7ae3db148664e04998cf01bc'
        '1c2651093a8f68c2e431b278ff38678a4a2025c129f78da3483b0f576418c6a7'
        '4be9c90f204642c1aa0660124552fb90238ff3c86e0bce7cffb580cb88a148ab'
        '01f7bb365217860656f2222b635aa956cad4c895e9174eebe6d60a07b0168606'
        '86b9d00dea890096eb1ad2010a794932a7525fd0138606866db6aeaeea94aeb1'
        'ad75f323f1588dee1686068673e1a201a56ed7991efe7a983cb36c929b307c13'
        '8606867d647ace583db5c0a1f9681bbd2094001086068671c52cd3af258d1a8b'
        '467c6b041386068673cdd5f91a4a84474d18ac5981316d9810860686707b7605'
        'd9efecd6f42b5c9dbf1386068670867f6614333996893668e23ec16080108606'
        '8671a4a413f6463c50275f00ac6c0f860686739f1153ec64b3489a100ee01086'
        '06867300a671af1e77cb46ca1d7aa0128606867a5dea030ea830bbdd495c5780'
        'c4201286068671ccb90d60bed0b892acee3e4b0ae0108606867d9b2ea964232a'
        '022fef46d54012860686726d996be62dccf8577b961501c8481286068680ec2a'
        'edc7f1213b491cb2d77a78601186068673c27e1021a27e14800a6151d3000f86'
        '06867256e4fbfb46f00c2d922b62118606868d51a842e1b6315aa78cc7b4acc0'
        '118606867a99edde3b664f1a22e82dfa75d015860686822af765a2465df9dd81'
        '57285c94b9fe06c012860686822371bed0ce2af6ec37acbbef0e200f86068824'
        'a338508057f2990f89f8400d8606882e8ed67de2d14283b8f0118606883eb411'
        '55a718e5fdf3af823b6c00148606c69b83e24d6c52c07be8ea5bf2b07ca3f810'
        '0f86068844250bbb9f9cbe30bc0f9dc0118606897dcde7e7729c5bdffee1b2d0'
        '3fd012860688dd6cd8394ee2a71b45fa1cf49a4ac00f86068830d20d23abdb95'
        '3b9eb9614a13860688ea0f2aeb1fa6d0d2ed2efb9d2fc5a1c8118606c69f0f7e'
        'a5509d05d5c07a776c910011860688dd4c9ec8ac8ecde8c98db55472500f8606'
        '91c332c994ecbcdf132d9510801186068f7bb1c734b1b4f76226e44ed98d2012'
        '8606883e541841dd9bded999ed16a475c6800e860692aebe3407c1153ac8146a'
        'a5108606897b5e27e2784d53cc06a1a6ffdc1086068fab132686e932e32777af'
        '7433401186068f31cd048bea8d33bdb3dafb6c0fb012860691c391ea481b68e8'
        'e9adbbb6dd6343c0ffffffffffffffff2807861bc3bb9518e01e8002cac73cf3'
        '278ab0092e8c1d20d9897837736e264755a6d0acf41a35582380105040d95b8a'
        '8b307a95a4f3515f86eeaf99a00f1be97f0ebf771726bcab08858990248623db'
        '61f83f99f4d01b6eee14741a2620be913a41ec52f911c882246bd1f670a994e3'
        'c0218010cafa05df03e337f2bbe0cf5c01f1b2c418db49f95c949538641282dc'
        'e4f4b023863499ab7a62f967a2c41eaef6be32a34ad61c5404d355c7c6be0e87'
        'c78f0cea419a84248010c445b5fd3ba59cd9615a2dec013991542e33d39bc349'
        '3e7fc80e8122bc6e971ec0e02486349a3602255c7a62305e1730073fe38a9741'
        'fe755234d0299f31d88d9ea72938bcce0024863498af11ac59904a10bb7cc149'
        'e7372b12e910a5b6df81d15546fa5ca061cccb2df3b82480111870a938af4527'
        'ca235b0c1e52754ff08461569f947bba48cfd8b5986c65157c03cc228010ccb0'
        'e36787f4fca1aba9f3a21740f9451bb5d2a76e36c9270ea4951459e03c142286'
        '3608e4be380473a6dea06078974803b09ff48f52ecac8d2fe579e88182f47a03'
        'c82486360bbf726cc083f778151d634307d2eb09dce659f506f9b42c6f47df01'
        '29ead930a98820862f9eabf90192a52fe58b0dbb0fd38e4865857805506bbecb'
        '1141e73d3a64c0238633b9a4a2718fb9533cf9712dc271e0a3992c84fb7f0657'
        'e50e7fd8e20265cf2a02d11f8635f8f10065e9dde5a64eddbbd0545f0af31abb'
        'ddebf95b1be18f3c3436e8258634b8d912ebb41e77e7da22a48174794f7a9631'
        'd31f0b3a831be819fad880a9194ffcf28c238010cac27c3ee5ce6727b4d1fb17'
        'adfad3e2fe2b401099d7d84fdbb60e1a30a25a597e23800dc2fc3c67ab19299f'
        '5f24b984f938ce4ed874c46be4907ad96983b5ada3081c322323862232466e0e'
        'aaaa7e8ad8f6e37fb31b62b97a12508f9cebd33160e0b5c7927602b8c8248633'
        'bacffc504f719aea01aa5b9fe98cbfd1235972d96aeb485e3a088cadc9af6669'
        '088022862f748d4d09c62ccac463143dbad5a6c67343ac5e152f3d5c72ca90a7'
        'db7a3e192023800bafbfab5c5b1f3d278dbe648acab1ef1a41eca1ffcfeb1539'
        '2b7b0d2cc4d4e59d00238438fe752f136c95501085a87a93e4d70a34380d45f6'
        '02a36c585b1d349f4abfd795f224950c6aca0f8f364095e06caed87297933fd4'
        '3355cd4b1aec50a5528a4285564f3a684dc0258526230d3c9ad16a9a43fe632f'
        '02ec0f197a4b977a17defb2286013c5c6567e8eb90a245402496400a3a3639c2'
        '71cc1d42a3833375f81ecd49da1e31f2e08f23f239615c598a68012c6033ba31'
        '6c127dd82e7f9490b335761bffdaa7d14f37a0334130e6813a3624b4b458289f'
        '08650bfb88e0bd8957a508f964547c01212eb6e6c5c0a965fc870499a297ee88'
        '648dd375452298d684827f0f87d5ba8493eb642a3394e5de1068ef2585ff58f8'
        '2eb6f00eb6cdb7b2e2967186471630a7dadf092af962cfe3e8074320238863ad'
        '6ebfadd702fcaa0e20d1ad3dfe27902db6da120f1ebbaa81dd5b1bba32242150'
        '06463e841ecf94f876955d5c0743a36895945796a30ec234418c7e3ca02db689'
        'd8e154fd363f8ffcba275553ccf8214dcde557689d377483016e3a36c12162ec'
        '2fdd812727b21d77ad48182db0008eeb96ef23293a74252e2dd591eb9d00b5e6'
        '391e285faa7553dee4f50a37abe23ce9d4a1930d71cb83e1482cacacb610a724'
        '48891cdb6f21d4983ab01dfab0b3bcbd1deb5eb0fd3f961777d3f8edacc4491c'
        '00ccf978c00c2ea7193fafb5c1627a285f19d88c109927af0b2f2b295ffe23ad'
        'ea87cbbeadb85ffb5ab496cec10b266c8d966818102f9a7fc95ab4eadfa6bff2'
        '7d33488358bd846b87cf7a5c031dfc2a4b01fcf976febc2f9608b394cb57eb1b'
        '9559b9ca5823806dd1d19be3f2bec51cedd0dc3b505de0455c404e5897146954'
        '176e3f82ec7782a5a02585322cc47dee79e66474f5e0316872dc06a189b62ca0'
        '7b43eec7fab98510ff72a837517be82bb59d13bd579c1b61f31275ff39ea766b'
        'baf213903ceabaacdff2f4381bf541dba3dafa1bbab3adf864d6c02cb09e8bc5'
        '7103a22b4c07aa849e40fe42251347f78f931acaa9f78a9e992165d4f0a9f506'
        '4f6154572595e5402eb44c6076f2a2c349f0b97874506a9cb63d61e649270f3e'
        '39186c9bc417977bf8275edc1164df05e196490cd5bc8e2eb62298dbbccd7d4a'
        'f470c5dafbe5025710d8819a030ca01c483c779a2f799b52db0d12138ac27c3d'
        'a5da69f79b922db6c1c713b93fc550bfeaab48b0170257236e716009fad506a8'
        '19fc9cac666f918ae0d9340381165730f315a2802eb5d34d9dfe25bd87bf84f5'
        'ba948d970099a57b6a1632c1a228272922b2cc844f9c06efb2a2c2ede08d6321'
        '491cd82eb51f3e2bae6ffdc8e8071aa7e62dfc7b6127536cfdf874ce2681d32c'
        '0ee9484f3c23488ef35af90a70fb502aaac02db3cee51fefe81e7d8fe95e649a'
        'f5b4ca6f7d011927a94bebd01d7cb9659e4640a252723d9b8b76ac203ad5f900'
        '2eb4c8fea6afd07bbaac43819a3315c3a593634748584523de5f714a1832fe65'
        'eb35609faf576f2273ce3fc9ec4aa02482bb0aab655c56d936f2a354bff7c2b2'
        '93a1c4e8fa6c491eb6768bdf944c2eeeb18dede02581ad053a25dd0f7484b01c'
        '22dc2134f2bf00e792e84f3b43aef4305bc5d7ef5f92e3a5c3e82483f8f1d5d6'
        '89f261894ff6d1e677f1ee2ad73f79e65d8a37eab1f71afb11bd393be5edc024'
        '8585c5159f3b20e3692dc0fc47b7242e0fce5b7d851d9f1eef3c869e82a402db'
        '1ae39800258587b142741a16df1ffc7da406b4855c7419411b7f23d24ddf55d4'
        'b4d7889aadd88d64a44030b7654ac4b98b2350001478e6bf9c329e29984354a1'
        'f81356ebdc99bf8485c5c424fd563c04922ddc5eec361cb290ba002caebb15bd'
        'f35821e8ce1fb5051de88162395f1f703681717a378877f284900f7c1ac8c7a0'
        'e193ecb5d40311ca2bad01baeaf85b58b1261195dceab351725adaae16629ef0'
        'cf16d9c5d59f3257435891e8ac1a6fcadc5395602cac659828d16046110ca8fb'
        'b8ec4a1da5b49efebcf73519c3130d9911e6bc77e143cbd7c9b49ef51ff72d1e'
        'ea2eaa1b1e9890f1c8e1095d702f64e78cbee0351c8ad0f0b97fb44078dc56a9'
        'f439d116b5c6964066da759bc8eccb382eacfadeb0550414f01796fc70ad7928'
        '8b3938e7d52b84c394516aada1a3b87a0e199eb74765f8b77b4bd603d647492d'
        'acee1aa982e60a9f2e2726875d6e1aedfccf2fc4915c2646ed9912d1cc37fb07'
        'd6080a5e71f5bd2801f90781fa2ead012ce6cb97caa00233c93d189d67a13209'
        'd2baea40e7061683231fe87e67a1f9d123778a638e024981e994cc422aacb11b'
        'ad1894a03f9ea97efa1c690eee66596f6f18103d71d98a1c100cf458de140818'
        '5a3801f78d6f402daaf0851d8c5e433d40d99d5b078f8968779528a0a07c1fdf'
        '0981707c19c33f95286ff3cb5b1ddc5b79fa2c739c2ba77f76658f17bdb0411d'
        '538d867b0705becb7aae332c3618d2d845cafcbdab1e1dec38061be0c22218d5'
        '702ea3b62e173c21eb8a587e2799b00abf6137cd8107fb86e87de467e26b5903'
        '4fc882ebd6ccbb0d3d011bc4f2a90fa02da2070ed23b8d972d060f07c3e6d7bf'
        'a7c0c4eeb7a2ff145c256a3ce64c828916424c9e03e4882ebc391832af22339c'
        'ddfb84dd6a47f2e483b08b1e89b56abe81b50f76727e3b6a331edef7bbc1b41a'
        '1db64f317138088456e01c65b096f185c3e02387837de11583b410f7693d18af'
        'c8f62f8c87bc0f4b92ccde0b5e1857a982ec348554a02586f643f21a24efdc73'
        '69ba7bdcf7db2c65eda6172bf8b47ff6eb81e36162e4c447dcfeb4d42186a86d'
        'e5a745ba2d99728906194d109a2a4ee7ecb50b0769be73a06450c9b4018d2380'
        '2747c3a97e4a4e4cdbcb8f2442afe6a4c9cf7a112d6c656f1592597fa735b66c'
        '58a0228659ce331592554a9a989f07327e1b92c195951fc92d83c21eb7aeb47a'
        '7c5eeada8024863c4e90897bd4915e360b1eab99d6fa2f85d6c0a3c0f735d199'
        '0146a32316f8e799ff00208632d406188b503f0a47c04ef44cfecaa71a66448e'
        '0835ecd4a7705a03fce9200a860e279eb590dce5ccc0ffffffffffffffffffff'
        '280780090135eaf7281a8002bf528db437fdd297e244078c3c34d57d344f5e0d'
        'e401ae6026801174514538460e61f623b86854eb6887f8f6f4370b7d976dce56'
        '17f93561434450d2ff07b023801372e51d0e8524d187660d6a07801d9529fa1b'
        'c5eb1e659ac62e8aa4c31f0576ec802480158eb3bd6cfff9035883a7c57e7444'
        '4604a0ab073c1befd065d0398966893c5023190023865168e1c28333f1bcfb04'
        '320031cd9108e7e65278c16fcee5ad37f5584c9079b0c6ac2686349a37d2a80c'
        '9f197a0dfa5fcad0cba6a493a4d1e9aa299dae7008e88ed0ce1ec75942b29024'
        '8010bb3bd65c96a5239d50c184332b87658c1bef73cec74b190d6711e8549b02'
        '63ef947024865d21029a5eb764ee6e9d02be8c933c73089428410e431b0c51a8'
        'e577b88a7190c1f56022801d91f513f76ad9f029ef74b217ba333579e4d5f00b'
        'fd5eba59d54d0e6adf7352c021801a59ada198a58cf8907a8f8641e13b329562'
        'f1f3a1db6d03655c8b853e59e400258639166a9df1723cfda0ba3cc6b960d9ba'
        'c812952ff7ef5822daa1bbf38c20a7bddb20eb1e25803cd9154460aa76c69237'
        '010de66affdab41f487d2264c486bfbdd69145d2ac4a8c33d05026817d1308d1'
        'bdda01643468aac89412892264aa051777d8b0bc8ff06dd576b7eef5cab5ddf9'
        'b0258454eaf15b38098f08142e2563a79a46aed5790d62926289393a0a26ad0a'
        '6896d1bf4e28782695114c714f5ecfb107c3ab22cf552902a0639c994812b1e9'
        '2c1690f280996090d4f9ecde50c025858f793609db90442f3663fe5df5a7bb13'
        '42f5a4a3f74f4ca0842381dcc29217a8a6b459a02fbc3ea412f207f0d6d093a9'
        '114fc5bad65624ba533da7cb6734c0e5d52938308ff438366165edfd298dfed7'
        'bc86c8ae2ebb1f0e9a1f6fee2725934b459f934f97afd5d2401ff645517b349a'
        'c8fa926a87005bd0cf030d2f09ebf960bbdd002db992856f2cd00b854c146cfb'
        'd0ba1c67d4d8c2b8b169e23d51dfe1e931d0d6c0f53f62f0aaf9a94b2c681292'
        '802eb98b41b2a884f745dfc2fb706965fc83115bedd630ca0dac2203a55347b5'
        'eca836d282759b49070b0436390187802fb976bad691440777d8b5a75918da70'
        'b84abbf71adfa76a0c6fa39ba1753625477c3dff39a4958907a6105321da1608'
        '2fb7b280d51f6b4f2a58d44ebb426e6f62a5ea0df05bcde09924843640bc3ef3'
        'e9298635d508b41c4a43c32bfe4aa3e02eb4e509d4c42093d88d7fc2ea497984'
        '727197112e396f993438b8cea70c837cefa9360d6faa0ec7bee8ce9559046830'
        'b1a395c91aa6fbd64cfe0b19da24d5278c5e9b0a8a5bdd170d197b7b4c25d7c3'
        '4656c23fd769c2537aeafa2c473b7f0034a0bf5c7c1483878d1e6f8e46f2b68c'
        'ffaf2cdff3b55cdafb163a501c302e3f839cc3f889417f078d9f7ac35c53ce1a'
        '8506d492003abdf2bbf92463f3523a8564589b855d748e38028bead205eda213'
        '3dbfbd0e0b28f21ac78bebae0eae9ad00ee84e9312c10193e508f3fdf5861e00'
        '24836c798465aa5a21f7a5d1ab6890b3072a87290352d4d95e0041d76c3c3584'
        '0ae4fd883e258494d344bb40f1c2515427872171a48689b43a99b35b0ee0ad0c'
        'eae0d64ee392cb75c0e27524949c1e2507eb5bf5f77907928c548dab32538658'
        '7c609536087fefa3c9f7a2ad0dd703c02684f5e23d7ed05adf22da35c6e6f6b7'
        'ea371b58803d0bcec79f4ff1d663d221b7253b34411a802694c13c6ee4b98061'
        '0e68896c541ed398073e34fc93606bafbb971f284f2f8bfd6ea467fde52037b1'
        'cc881b2fafdc6ab6fbb89a54cfe4bab4c4af08b811be0fd580015bc95e305795'
        '5dd434bcdfbeaa9278d317ec6244b3400dc58bc2dd202fb24e7bb2a67dcf755c'
        '97035bc98eae5d528129b700f7b51758377e28c0242ded495553448ab2095082'
        '3de05783060036b1ac4583c6907a0053bd89b258a39623a14c774a1d2eee8484'
        'e47763128353eebd459c14bdc90404c755c51761dd74c442ba6eb2ba0031ac96'
        'd6f9149f2f4ff1c2fdb768a908b2b357e2218310170ce37665095ba5b219683e'
        '5ed65ade7b19159339f3d183a617c034a71a81edda325aa62cf7b2434b4f5854'
        '794d689991570d8f70b64a6d158076b325c8fd8c8c09d35c3907751877577529'
        'b1c7a8d82fa0f72fa3a3ddf8ebebc52ae87c940c211d8e1367272a61e5d5efe3'
        '71e020aa754b5b4025eb91078b511313ab6b5e00359afd7cd5bb3fd6a82f23b6'
        '73f7d0673ce049f22f58b7105bfa3e7b6215c1292a465a8af92e6d7e7ee9c1cf'
        '1edf72c2aefdfb9c2d002481d0a7af0d689c9d99ad10251711f28f0c8bdd6087'
        '932a8da05d574b1ba099d4d3c4a8402283b67857d983da976ca40e87683f3f1c'
        'b847b003aa55e5f279cba304d3ac5336ea3724841cf6a3c63f08d3587b61b719'
        '6d24d9e1b34de6ae51f6d68ec88e54ac3f618429dd0ed62584e10d41941dd2b7'
        'cd9efc92b69c2129f86cc8898519e89732a070680f287dd7f51543b9ca33b901'
        'a8fc59103188d3eccf1dcda64e4c744cb2e5fb658ee22e0aab6d9815878476e2'
        '4b80c842522335eb8d4ca82892fe08c0002fba83e61516d43d9698bfc7958dea'
        '59fb172f676adc96858f7073cfb0127d154e93ab03a534259f43cb172bc8ac6d'
        '002dba5ef2f27b2871c077ba9fbbd91440919f63c03d13ec64a2cf0f84b395a5'
        'dd478e81d2d2038b3df3085a72db9e2eb86136100ef2fa1684a905d94e75bbdd'
        '7a8a75f17c0cbfe7ee73db587d9db2caf15c85e1314dfa0e0e4c289bf3c02fb3'
        '6bf1bd4f8e562addb4ba853bfc47828788db3784ab5ca32ab4cc76a1d5923e8e'
        '0ddf2c7b59bf6b3ccb4925dd480030b1e70bf908e699141891de324fea019369'
        '87db26a94fedcb0b80b197bc788d70d4507eeb7df1ebbd7491099ed745b9002e'
        'afaa4b0c38e6adbae3c33a845fc618dbee09bbdf0051c67d7c122eb829dd43cd'
        'b3fde41412865ccf07882c903f282fa5e2ed7e56525afa18eef896931b9fc2c3'
        '61a12d0ccc7173a4e6ced289b21d935282742aa5a8cd696150979f7166bc2fa2'
        'fb2eca596fd63208eea789287d65e032d08c6cbf9d1b0ce8306974e719c6cb3b'
        'bd2b633e1772e7be5e94602cae782690824b7d9d99a13047c68966864fc9304f'
        'd75e82f5729d8e47a5cf614d968a55aac4831db4882691aa69effe2d2f844ccb'
        '93f112ceaac0a7318b779c1b81b471e0568c745c4b6b0dbc4573ba602584fe9b'
        '4a768f9124e40061c78f7edb48debc75ffc26589434681e3faaf96744bd0705b'
        '136130b991e6d7265b5f9f3fd6c0865655f887fc952f9897517479ebfce16527'
        '6256e0ba9ff3643dbc140c7aec9262c102dc3d2db9a21cdf5c51984379fbb0a0'
        'af8b978d308335a4b166532d88c06051af46882464627cea5fc269666aff6e61'
        '802bb956e14f43a4e0fe2adfdef81fef4c092e25f81929c30fb393fd22d362b1'
        '36e2b0daa5f06bdd00103dcb402eb99285dd33e4815ab09cd4c7f1d174675f48'
        'f9890c214ec5fd98e94d15033234b6a60b028b27cf5acc4593e266b030b7a7dd'
        '58109716cdd250f6d4ceb24ed994436d466ec5809d6a5b2130a19e7f2cb65b78'
        '5b4e674d5b0ec226398d159b002eb815d960127b1324bca1550ccbaf3f8164ec'
        '9ed7dfc546e798fe0bdafcfd1ecb94174e8767ff4accc2a0694c9d0030b7a543'
        '8b8d862c74ed5242599e38face7c990dfd2423e0113835a4ecd49479eeb8a50d'
        '8c00b80bc6867464ca3d3d64c02eb7aa410b9e9a23ee031c1eaf6b6f093385b0'
        '3602bd2f8b2a522388c6990e5ec48b1d6b8cf5552aa1a0a731858b602eb6d14b'
        'bca831549c2eb1bbeac5c5e45305f47f6d9b1b33805aebe5c0d87a6ef821a54c'
        '547311e45403cc057e063834b67ded160a8fd2539a435513e41112cb9c6edea0'
        '171cdad6326b6b3518174c11ea619b52ba410ac6e3566eba0f0695dfc0071183'
        '33adeb868f8772220b227b5f041fcf29246912c89ecf72b6650c4515fed84c58'
        'fbc2e052750a36b5888dcc86df193ef14e7971802482bd85bdd88b6da47fec7e'
        'b42d0515907404296ccac0b10bedfdefacc7ba5680f8874d00268bbbc50a42e9'
        'eb4a2b8167cf6d462f1c60ed24bc78f6e11a118fe4cbd0cbc34a6a7ff7fba350'
        '2581009a0ebfa48ecf2f88a30807321b585b52e7b3784e870a9eb754d22d1da9'
        'd196f498604c268069e448216bf67422f9745d14f7e39268d27bc9a8cfdd30ed'
        '316d4b69b516a065e40f0594c02686c7eabe30197f82aec87f6b6978aabbfe3e'
        '8e7354a4f405b5c9eafc0aaa4e3c3d37dad39d002486809cfa31c3bdbd6f32fa'
        'a4c30423eaaa46e1f0de6fa7a72da797345a27493a094bcbc0258015b5cb4108'
        'dedfa205dccdc8d692cd0ff0f4de8607cf12bb6b356dbc1b6392829721c30026'
        '8651745258b39fb54d72c9ffb5864b43ecdc10962f488d4d39bc5d6e00057b54'
        '80baaed50cd023862fb080f93ef2053738ffa0723da507a221f23bd09b089c83'
        'b8e804deee9d0c534440238634947261947b8ca04110daffd7ef0375cc41697d'
        '4908e04fd5bf125a1b9828deebb824862a7859a83166217b59e2197034753f01'
        'c8d8bc842796e6815f553963ce2352daecb1d8268635f75dbcfab0b66b92e2f7'
        '3a1ea5906875dd2cfbc142bcd809ba3b9a899e3e43fdef0b5a80248635f75e00'
        'ebbd1e7c1cc8904815d3611897e2b62f26286e445739b337a6eec3026f21be25'
        '86394a9e66b2c3fefea9c786176f9414c11a324c79afa697e070d76711018b9d'
        '9907fa938b2686574caebbccb53f9e111839272f8fe90896814b73745fa75854'
        '9e8826ceb6f37cd22f1cd0e026801d0ffab2d82345d195ce7aae59c91aa02eae'
        '888810b2a1aa46db4fc00f29003b8d0a36680021801174380f2c2f2521af8d4e'
        '1fce7b62af8eab7a0f07ce1ca3edcb89dbf21f27802480138d8bf4907049570d'
        'f6d72f1ab603a760d65e2f911d212421cfe86117a1d009f58c3022865bc351ee'
        '0069f1c0c01abf6b11904156ac9fd1ca2d2ef2be64922bb61189faf088128620'
        '618e1c91f97c602da6e63a2f72495b6007860691cab8658006800221722d4e06'
        '800221722d4e06800221722d4e06800221722d4effffffffffffffffffffffff'
        '2807861bc3bb9518e01a8002cacb15ba7a8a43fdf57f0d580671e31355282982'
        '6e29d16022867e0fabe90c9caae48930c4190dd5c42891266174285b23c260f7'
        '7ec98ef9598b701e8633cf132d9594778fff647ce715c3228cce77f35450d5e6'
        'c929794d21171e864276a0f8b8ff2417f5a6e1be87745af74ff21f49457b3818'
        '7c6a9a237e2086513f3986eed31c9b9290599a833d060547040b831b813ccdd0'
        '73e20bf288402280130887cb31c29c0314ae7c4e4fb363e9e4ced8351bd6b70b'
        'd24e7429996b8bf00022836bed9fddd2bf5dafdc416c0e114652e8a7c26b158e'
        '11aaee19ac5448ecd5f8814037b07a5bd1139fec674430a78c906015f1dc3ff4'
        'd2f655fd19d724b80b99ed61bcdd2d594b0deb95202180a8f684027d4eddca74'
        '633c8ae02cb654ec6f136d66ba5620a4917e1a003738b6e0388131a08ec68ff6'
        '0bfbc92a89361ea540f337228cd3d1bdb02ab99c982e626e9b8a7d3bafd6664c'
        '4c8da1a16b307f51bda79ecda1ad039fb66647302219764c3bae9c8928b9555c'
        'fb68d4c45df73bdd4b9bf4a5be4638a90ee728c7767ab923a27c942fdf17e063'
        '6dcc5231742ab88f18021db9d51f10bb7d79e0794932a4483d103b9b30cf64d6'
        'f139b3512967a8628bb433618e5ae8402cb7c9cd97b45617e159939b8268051d'
        '81d4fdc32a6ecd70db85111dbac9cbd333b308adb44f740fa27959a4fc2bb79a'
        'ad089c14f841dba178a36a1f0f0b822aeeb1a17aabc6d43eecd50062ba8a3c4f'
        'a6ac4a297c760035302ab306df7dd4f10281fbdb32a4a73b1f1d1d950c2a4daf'
        '98b3833f3e596e9e0084f20685109931767f380028a78bc5bbf64ed8ddc18595'
        'd54b35226fa23f6b2c60e305c4a54d1092514b390c61c370cc951e80d429a4b6'
        'b7c1180965f97f221d9808a3199a8318455cdc341e0d9a46fe56d34a63aec84a'
        'f896fdb8ebaa802ca4fc909bc346b8b699add235c247eb4bb91508d6d702e521'
        '8820fee17f97ee305ab12f796f44bfd6b7b1cb202ca348b54517d3d95f477894'
        'bf019f17437883621ce8a66aacbcb776eb16d762e343dc3eeaf99dce65fc289a'
        'a634a07dddb43c582176d4cb4538e1d27646b6196c613d12b370ded4bb5a0617'
        '992d949dc9cbc4880e21dc61e234afd4ad9cfd2eb4a832b4fcc80155223c3178'
        'd24cab2192f10c6d5585ad3efef40ec277a0c0b2eb60d3481b6127b9d00fa0a5'
        'be26604e028749f6002cb034dfbeb5558c0cd74f3e4a8aeb347f4004ab81a5a6'
        '1365330f8ddbadd57035ba0cda733f1b083609ab8aba2aad0af0bddda6f075b1'
        '2b8d6ca31a8232682a3b1f453076c4bda586004e27107453f9cd740f13a3131a'
        'e029acafe5819520a308933a492f726a9907ad0d2db70073458b54e2e4a83296'
        '2046d6a733a25b72b5d7702ba99e63503780fc9a6fa7c42b2c63ffb85e49d83c'
        '85e51e7d869cc17c113c8de3767348bf48f5a36d41f1b82aace38c6bedcfc667'
        'f979823443bf9eea067eaa011d58493c916221bd149de53ec0d9dadd4b96d3f7'
        '0c0931aab5cad23e0f3d2a3d25fcb497dec6ad83d4e4c80a5566c55f844cd0b7'
        '177327cb41df780bc73760018e7fa7a97a551e002cabe076b8b5757b18f2abc2'
        'c57c510d15609531ecc2526844bf67cf1c19f448093f06e3881d804d17c88f86'
        'a032a5d87a15dcbcabdfad9d7e238e8ba32001ca03ae528df1132c86c211fe73'
        '9b99e6910ce956db7fbf5a01a778f82612d7fea02381ff538f87dfbd18455246'
        'd5959700d68a67784211045f941282d8bfad7d1476c92baa22833d1fa04d2a0c'
        '2faf875749ef2c8c3bf3874940ad106c7228b26d8467ca0d358838238477bf9c'
        '1275ec5fd80a2d9d07e8e9405472fcb373ba02532adf7eb7256a1d2055b95039'
        'b6a89a02a5ec1d35986a71e853edaca6f8f9feb574f0d73e2b59b8e0447ef388'
        '30e9cf45bc36eee67210fc9d3e7bc13ce60a043a53f71229f22abad028eb0f38'
        '20b417733c50797d0ca4bbdd297c01570fab31c8a9ec68269e134a2836eee3f5'
        'fc2604d42cbbee27f8f0456552682f8bc1b9d3f65c82d7d1af8041bb9bcc4c3b'
        'a11096af741251cd4e31ee18e83c7eb6a029ba50a702677f523b1b3ba0e9dfbd'
        '574b4dff8fcd47ef4d61ceea36698dd8ce51286ed493f0c5e6f6002fb689f630'
        '35b8619739c2723081a4de842aa5509ebb62f1430597e96f42e1699968086bf9'
        '857ca788dbf9ad30192d002cb17fb9939c8322376f093b41146461cb408c3f56'
        '5a2a165050740293feb17a4dc579d3c806cb13dc3db8204a29b1e1c6a167e7e2'
        '1440844582bd22651930b5ab44ef54b2e304976391deb3757c50c00c146c3397'
        'c4882cae0e0ff2bf0300eb2213abee12dc2244c3c751e974365c164055fe1887'
        '2b1ef09111cec848f1a762950aece02ca21cf2506288b2bcabb347bd8e73c44f'
        '48921aed449d3a997fcb305f432178bab4a0782baa02c58e3a7dfbd8238250fb'
        'c469b56dc8ad67a733575f07b87b2b67663913e5c36ac9c6c22bd8d7bdc57e80'
        '22830cc584b64a230e3270ad594485d635317cf850ffe47cbb44387e8a04ddea'
        'ca7ee02293ca9c98967e7bdc48b51968574e5b525d2d17dabc66f92107b8b468'
        'b3907e77d24023854532b4b42c0d628e4276de2e19015d144c02a9a69799fef6'
        '5439f608e74e2f9ec76c30baa6eec491647167d6bae763e418dc752669e3fc6a'
        'a289f1a3b4d12ce94b30c72acb32eeabba62daefc918b3805320ce29bb3ec033'
        'e46171a1425f56325ee048d142a3777ce34119cdd0f5758729a45f2c1ad647bb'
        '5520c24b002bb953a0fc1dc1661af0a3f4b67c9fc761987aba4c1645fca0261b'
        'c5a32fc10c46b083b6e8ee197b68cc32002cb8fd196061dd025a6838a6a8d4ca'
        '1350d06999fe215e5c724e1993c48c1ec042749b531c79c68ebe03ed33202bb9'
        '97065451a8b1e8bd5d5b7ac521bf80ac3c30e37d076ea07b65850707b00a7311'
        'c28cd7ea810c9b86f0802bb8ba9c66be2a1cd4163c3a93711ff6e9d670ef4ee0'
        '4916b21b769f6e08ff755b44d07dca7e47640b5547be30b89125d15592b92c89'
        '1f0535f33fed98d16b274d255ab7ae9fbe3b6b4f87f918ed21af6dc62ca2345c'
        'af6f264a14a1002eb8f4ec0d9ce5bf418c6468993b2c7e09c37dc257d95859d6'
        '034936b8e067122efa041a32142f39fe23313961b5182eb687decef246281d85'
        'ff49433a331c4047f0234336768b3b9a4d516078ea615ffc799435bd4d222e2f'
        '98c71bac7430a78b89b47c91f1f3fd7a19ea21ede14ad804878a36e16e938305'
        '4f4e56102ebba8105072afe039a55a02ae23697f40002282bf1d6d395faa62de'
        '863b44f24d4f1f677c0e04291b56bc25a44604f2f0e7008658238aa35984b2f0'
        '08fb839319641a40cc602376f7c8c68c6fd6ae318ca045c6ba911ec5c8228714'
        'bea9e9685d0388890cd7aa7667ab2d0dd35de9cd9b22e780a38f32457dc20e70'
        '238029f942038f4327f08fdbfb58c5739752bc4517fff9cfce3ca49f8b6e0129'
        'bc1b7bbd24865d583ea7ad01821d21b054bef91030ce83d053bfceec7caf1dfd'
        '98d209a1accfacfe801b865d4675f6aff70ead0e4853ad01e1cd10cb673b5259'
        'fcbc35c3f721801e461bf0da2ca5b7b69fa8eb2751b9cf60a8b4b27bbbfc4f85'
        'd77c995f00fb1021865be9bb4a9c07c80c698fc1a97802e0e1a282263aebeca2'
        'b51f7ebf88754ed9d121801c8894d458d39a63b4151efe1a4916ae96834ef805'
        'aed4a063a70ca72d7d2dd823801c8b137901433fefb7afb83d28c2271d414b08'
        '2646532f5f58dbd7fb4a1f3b12ea782480204e620774a33cd92cdf77fa239787'
        '921100880e8a070cf8b6ef2b020c73fba4d17e80238687ade06d443825e248a1'
        '41fd4fc8fd93a1bc2f9e6c232b95b4e2e3a4cb6c9bcc7fb82080440f6b3c80eb'
        '5d275511061b5bb84bf00178051c0dcb1438e1c480dc689558228036fb761d4c'
        'c77f37ec33c20171540dd277174638d7c7b8d1efe70b79c7dabf5b50228039c5'
        '8e0210d0d42b602cee83aa9aba41c63c2c5168a989994a5810ca1452cc1faf23'
        '80347dfbc88225639560f4552efc09114cfe151cbfe6a3a95a6e7b9f428eea52'
        '2e5f0022803608cd6c2bb9d389b142829c16f94c9d2c82939b38ab95f862dd3c'
        'ea8592e77a342186dfbff18fec4f3090fd87a93b6911f4ac73c6edae61d74c18'
        '0e464b84a8b42f402186d153f953f4d860dd78b9fb36281b3dcf3935a2132dd9'
        '984cc01f1569450299801e86f8708da5b69d959e629f406dc109160b99a0bb49'
        '6115f189ebc4d36e802386a569c38a8de0c2741fa5cff62968c1c4bc7ba38076'
        '40e857067df6f9009e74a61f202086b1e7b9a86e94bb164de4a9f00e8a2c45ac'
        'bd6e65973696e89347d49e068632218035749b43f6437c255b593e483aad89a7'
        '225fdfc501b6d73242e935404404cd3c2807861bc3bb9518e0198002ce0ed3c9'
        '5c93bd89b5d902724d1ae44dd4ad5a04369df024801f40387a20073fa56adadd'
        '8ae8f327c8b5569f2eba3c4e981b7840241080be5d25a300238038e28ad5d055'
        '4f9ceae077d705d621adeda5c665b215bd380e88891cc580f2b0035c24806f5a'
        '07a5ba10d01afd852b02788bb2cb4bd9b9535f19250e45c9b53c0a595160400c'
        '802481a551873508f433a0e4667fb1c763c334dbb99473ce052b6bef604b885f'
        '47c48df300f82382de706cec706c701098d8463b9d3fe940ca43cd339ff2a861'
        '78d1b41ff04e67237cba22838fc0c74737ec1c42e9476a73917341001a6a4750'
        'a2c61f43d1bf4ca3615bc52d30249149b08a6ccb1dc342ae764b06cb86afa97b'
        '00870a7485b6c7c19ed92f0b6093ae73650037ada7796d9daf24188e0e500b13'
        '3e5ae0dd7991f27c261d6ef761d0a286f6b8a3c7f7a2866c19caa4414aa1c42c'
        'acd4a025490190c77bb82cbad07ae79286d958f7cc26a3ba4092ab278867e676'
        'aaa279194e36ea56450d4591f986b338424c2526a13df62abb6ee3fb10cfe158'
        'e51b57e67edb31c8ec5fd27dbadbf5db2ffe89c83ed5700d0e52f8839197ccdf'
        'f61025bb23212f0fc4dcad920fa6e4fd2326950a35f2d071b4484bce57c8d211'
        'd53067906013015027ba6ce1e77bbd5a7c7322f39ba424b84467401dc7926451'
        '95e6a5d6845df41f331da1573442289a26b94f38eb12a850170492ec9683ec63'
        'a9cacaed8961d75f25b654a1d16beac770d9aca83a55002cb778c8b60013ba91'
        '4f3cead1db4573bc45f2eca4e32c16eea75ea7adefd73b207ae199d0df2d74ab'
        'aa3b61e02baaabfaf00e8c4f8f877baa4b39173863db9553a00dcc620411467e'
        'db61e7695159f6f297780b227a8415802aa5226d8985964b97556528cdb5ca57'
        '63e5a6ed0efc6a6e9c2d07cb15a1bb33f361c1fd4bd12233bb7e4029a4aa99e8'
        '171e65e6fea86a395e4d399472c0d2297dbd3cca84f74a629ec39200a51ba1ce'
        'df388ed1212babf0fd4b659a1f179bda38dbb38242b51e98051ee6deadd75ac7'
        '32e0f13a2524bf0b781acf79b521c61ad433aec5be290f849f936a1cef5f6e0a'
        '86541ef0b757a8ab3cc38fe5e72a2ccd4a66f5c14e0c1dc7051a85c2531d1304'
        '1a3ba74740249382d377903e0168f2c236336b152d74b9310045f45713ba8a1f'
        'c7c2b93fc34d7bdf460024849548fd28c38faf6633dabb2fc408668ba1cfa852'
        '829bddf800d35e18977398908f540024948bfb42684a29b4af2a7eee660e741f'
        'aa92f6494f0f394e28499ef38cd8b8b8fc0fbb602fb4c7aa499c0ed7b7172501'
        'f367a11a95688ef745fc37cb53233b037273f71b2fb3cdbd97805897dc9e7130'
        '71ca36082cb704521dbf3045006d205776d2072791c16c2483921e8fe041b276'
        'cbbbfb7ae6495165d8273882d9f9b594702cb4f34b8d04f061e5f4bf4c114275'
        '23d971e30e8623b572f8c26cfcaaec7d01279ce14646211930dee7056cdc2bb1'
        'bc447b996fdb8839bc9076fdb41270c474e7f87515e5ffbfc1c069c866763c11'
        '179b75916f8a656057842cae60110c45df08f667403b97f77c82835712e32748'
        'ffe9616086dc3469a89917347b27b4e893edc27759f1c028a9714abb086ad309'
        '3057fc006088f1036b69b1862d30ba999ecef9c99b54076651b41c881edefa40'
        '28a7ce083dd5732b1c1658ffd136edc8834c519c3bc79710df7e6a7810d56f07'
        '8af5a271a7ee239f722ba7f69fda7844fafc7500a995ced3015ae3a2de9429e8'
        '56ed3efe1100652c6abaec7833707ed14c075480ec2aa96e5f10464eaa71652b'
        'ca8ddf03e43b0255f0a0699653473d0702d082db9c97276e817fdfc23e2fdd00'
        '2aa90b51b8778248ca36e66d7f188e3beeb64e5c438dac279d15b475e07a1bc8'
        '7a3866721e5818976607e42da797a823b2b6763066479387f77e364c71434674'
        'b5915aa7ac108d583c9c49d2b8effc4079598e5bdd193b56b037a85f11730ce0'
        '69de4264e0ec5e9e8ddb9b99b6cb1c372a935b4dfdaf0d8ab3a4b9be5975ee1d'
        'bd4e84cdbfba10ad884c69a52479184a8039aa32105864e65769e4fb7f740ee8'
        'f3da8d74cb82e1863852ccdb7717bd302d4a2fc57ac414c2471f796d8f400017'
        '9cf693e90ff5620b973f5036ae328ec2c0b10dce57a9391f135fc90c57ce6375'
        '414482dfe7ae8b7c08429413e55fc33f0c15a86fb80e7c95e72f4798ea9b846a'
        '83932bb29800c55c3e627dc3914cb66b3d825ef8712367bff29576d172a584dc'
        '2a773796b935c30d788a6f0048242cb3d63d46d25bdc0a26072a821844b2416f'
        '5866775dcf0f69a312130c3d7cf2a17f285dae51165b0e1d857a402db3cefa2e'
        '41b54e702dcf8c3ea0280fa36cb0b620f28a3ad8a3b9e9b834f5c3e00bc49523'
        '160612d5f6469102202bb3cf0c87ce28d5812e5efaa33e25b33e96a9e9a076dd'
        '2bdeef192fc128f302d22110dbc03403faaf9f5fb02cb4d1f8ea17e5f5c43705'
        '024b614a9c33a3a3c50521489dae1aa32651f8a62e787d44c437664141311ef4'
        '3b802db14eb1b2fcece1e4283ea2c406f64b21baa68e413cc0848c5a0cf16ac6'
        '30e68189d679043b172fde25cc5b71c027a94172b89727e303bb4e40ca5e4084'
        '52c1bdaec1ba53c7ddb12a47abe0e57b68a344bbcc8395802ba7c8105c0cbdc3'
        '56261fb1c07be272848d4f58a475a20dcb726d6b1a8cfb45743a4931eacfeee1'
        '33d7ba802ba7ce0d2016bcce964eac1f96a0087be85ea66efb920bcb22856bd5'
        '8f65a8e470d4c7b94a5cec793883fa082aaa181a9c005d5fe587a8632e686769'
        '4793c013514c510af4e90d8f3bd4af7df6de67d1e45a2d510352862caaabfb0b'
        '1e8182b8caeeaf4f6f0efb11be114b8f662ae1fcb4183c38b0457691d1cc6954'
        '11012b8555623b002aa7eb7425a2d4e09cd57f97da3c0f5c8881d1e56da2dae3'
        '2f78a0d5436359cb28c52d8b6c90f69445dc202da861820329dd5738870f5e53'
        '5eef252ca2ff362b5939a7d02cbe3f02fa03bd4a6f3f163a617081334728ceab'
        '0034ada8b78e1f8f9c09215c2a1ee0eb8ad0652ce0cd41a3be421ee32fd3492d'
        'd4c8a23294cf4acbb2ceff28876629ea2c9fc686332c32aca8fa54dc3adb633d'
        '03c8c6331683a623d7dd3a79ed18303f2a17106fd41eab370bf027416e3a76fd'
        '8990d34a9dcd2eae7234ab62f7b7fe3fa443f8fb4ac238d4da6c16d402ef1331'
        '7b1428b3bed17f69bfc3a7dcbf4fb87d9a9d9b9e7362b591fb31d78720f02caf'
        '3431e5ae44686a2f1c1cc00e2e713cb43ca6f6768a2d783db99a29dc03822eb9'
        'de3d73f4fecdc1a977de602db40d9e1211e8170a8bb531c4bb8c094d32619157'
        '24c88efa27efc9dccc821955ff3517b68ef83f2e8bf68e1e402bb8de225026c7'
        '0343b2ab8d9bc076a45f185f21c520fb9ef1a18fed8ef74abc5cd4b7fdfaefff'
        '56a2e1de802cbb5562dede54edaaad967ab557b030a17dc7bb6f4adcdf143f9b'
        'e599e95b931407f2c661099425bc3b401d2434b7728b1aca8760de3f6650ba8b'
        'ce0d677574331b846ec23a23cd7a60db57723d47a3c84a3a9d98b9dca15db9b8'
        'd0278cb5f1b1002cb90bcd82d33ef459b2723a2c2abb2afb15050c6ce297aa01'
        'bd043ea33ffc4f081e9ec8c12cbec67bb72c9a5e248596432c310088da1566e0'
        'd82f9eeb6fd59e08ccce7594fc86d1ddfe406db12e75f17400248576b950a56e'
        '14bf286192bed8cb5cd6c11f27fe70219bcd14de687a69d3854f414bfee02485'
        '21e0d0113dd0632b4f7857888f352126ce62579828e5f0712ad91bb7a702586a'
        'c5f4742496ec9945c9a2e8e5437e1195095bd292911dcdc78420e88dc043f542'
        '7d7c65a1fa9788002396e3796c811bca068547feb58ec9ef190b609c396fc9c5'
        'f9d1681df3b28a1b49d8791033ba332b82ae296741ce20bbd45e1f9ed7d7c1fc'
        'c7c1dfb7de986b1f5eba1f41719b5d6630f3b94a162b78cacbfa9ba3bd69c2e8'
        '29b6f1673ebc078e196905d99a5c71fdc90e523e3f7bc7c09243a6df8476f2c6'
        'fba385cdec82cfd3c7b62fb6edf5b9cae8a98d4b155eb56d8d8fa2a965f9d637'
        '0c2e6ab8890709382199599b04e29b38667fb0e03d193de921dd2cb79f7c9235'
        'b903332afd094867af175c4851d3df82820e2f43e681a2d75241223e0075690c'
        '2cfcc98803c08121842f7fb1ef6cc43674eeace72ac40a81fbc9380f88105ff6'
        '199f6265308124ed002481b10fcf895b49b81ae24bb001c04dc5796c238f06bb'
        '9b920a4fbe0ed5d92b5e9100d1802481172e172d25247c4ac8a3bff61516dd9d'
        '80e3aab8473bd3ba72e4fc7c968e5b441ca3002385af02cd703e958d7b86e787'
        '488201de1905a7f8d428bd19685b7647e7194e00facb802395c92b01c8ca056e'
        'b206c3e8e175d43c3794a89cb91e44b572cf5caade21d6f55d134023965a55c8'
        '310605ca93b0d7b5dae5e27d9bc2ec020e7852ac23b9ce45ebc9298450d55824'
        '974a3e6488efc9ee2837d473c5682ba9e0ced1cec405745df709a8c3f7a2440e'
        '8f9e302033bc6843492802a07931e68eee6323a0e650903379b1034907e3ee02'
        '9daf4c0868d4e2fe8a83842c5f161fc8051b88ddbbe19e662abbfdf491cac397'
        '80d9578917cac9c5cc80ad70dd3e4ca93b0b07d3d9fcb5f396a7ba1f311bbc5a'
        'fbef8827bbf720f54c3c09aba65abd64a6798dce8b0539bb293aa28bca46c455'
        'c679afc9456e7ca055ebc022bb3f827c7fa19f1026046aece9305ddcddf14f00'
        'f2d34da8faa632c00c00fbe65df827ba831da8ddb7c6e8235445ede61c932355'
        'ca7285f496a00c887652016eb16ddbf47dff24a7f0a029ba83e586daeb623f17'
        '30302b6f71e7a3a6ccb0c60bfc105aaf40d85ecfffdcc0eae938c9be465b5960'
        '28b99cf0f0bd072b5bf27365479169489194fb4f751e6ce4c0b77476b81e7b8a'
        '22d8e511f4230daaa029b9859fab669603fa38f502c71ca23a2372d9fa7ebd4d'
        '444a6b04d9a0d3df25445043c1a4c236e2ea2029b771c981baa5a06c3c87ed2d'
        '7b65eb34705150946ee840dc7b9b222732da9c525dac065afc23c48e0b2cb36f'
        '02e13226819ad9e29ef6ac36e27c3659cba7a32ecb3f61aa99e4eefc41f70438'
        '65d9731c50274bbdfcd02cb174ad23469929d0c22f3f73133eff8efe5659fd0a'
        'c949eee0dce3555d5b2f071ce764b14a5f817436056cf82da8d6b508e28fbd53'
        '99b5a54b24e8dba577b4f689d13d6811fdc515cb45b601cad1668b833eb443c7'
        'f4a739e6802481de00d87c2cadd87c1c62b8f3466f7a1d7e42dc497d141854fa'
        '6b9184a9f3138e642a002480b8c8813b268ac370df7de9ced2472f94ef1f2e88'
        'bc4e6fb302175c15c88f6aa6e0d400238714bf03c1804d4bb3f7ce654890e11a'
        'f3c45cfa15a992400db853cc96f8ff0cb13af023867e98441cefc45d64de31c0'
        '93cf001836361ef9ccd3d6bd34596728b602269561c9c0248022baac78e180f1'
        '54cef08e5fe3163a569338b491287392ca6c8ec0906d722df6e0573021865be9'
        '126f883f7593b04a5ed22addab65df6ad604923db15f52d682a55244c5f02280'
        '0fc7ec09098da8321d7174a1b06b685de98b1a3be72cf01ac3821455749e6433'
        '20238633b28424b167f926ec63949e34d8e62be341fff9c323f191c73efc640a'
        '070b89d8581f86324ba352b124d4b15f0dbad724afb7be86a0d30ea28153cb0b'
        'caabb5806020800b03e66051c40657e938dfd975ad90c4aa5bb1e5d49a57b058'
        '0c5a7500f500098609edc52b382563c407860691cab8658006800221722d4e06'
        '800221722d4e06800221722d4e06800221722d4effffffffffffffffffffffff'
        '2807861bc3bb9518e0198002cac8a146b21276ca8d36984057bcca2379b03677'
        '9422c0208635e259e343b0a6c65766ed9376cc07388bd80e207dedb25c2696c1'
        'afcea7c024863604a3d0c7e555f0f031a23edc1cf22a55c43ee0b68b3bf58746'
        'edc4b93cdf9b28fd0022863499ab333a7cbe0192aae86d47804bed9670771521'
        'b21d29fc120d85bdafc3f040248642f089c9c97d322ecaa2df4617ec22b9bbff'
        'd6f9c20b8b5d786794b61a4b20173e03302686b3e077de2bb96c47df6ce2c5ee'
        '84ca2561ea6b9945e20aab4f576012d6c748de07d96709402680d363e913835e'
        'f388fd0a3c6993913f4527883481589efdb3e44974928569421b94c68facf033'
        'a7f2aeade3becc15cef6ee94cc6146639a24c803e84b19f0e9806a107fca4300'
        'db10442316f20117103c08220b0659c55e0b382faa30e341843edc3304996870'
        'a4bb95ed3cd5abd82e2bc48c86f48f2ff08d8a24e8058307cf73964b9ccfa37b'
        'a6f40a2eacf6121d9ac0eda2ddf1d5cd53dcab0b7c8337aa422311bbe165c230'
        '59f5df99d2c3511b74d7da26e618a0f0c8402eacc77d6e8a154089f4e6370309'
        'aba983a03fe1eb0ac9a1b82816bc612a32f0fe6b0089b345ead9964b3aef30c2'
        '2b2eab2c4a7a6419dec6865ca48afb1cf50f036d0451ffcb8134d6202d2076aa'
        'fa3412a2eda1b5fde037df4590c2fd002fa997f174e999964dc22b8411404a06'
        'aba25cd865b4663246dbc98c188b818b91cad9f256b87c29caff86d633fc33e0'
        '30a9aa54d9984d79e57ba431306f2f0af0ecbe2c4daeca7cad2cf6354ceef7ef'
        'acef450d70fa3a802dd2b4ca292f8b86002baac02ff858f91afc7a077136b2cb'
        '933503f811910485ebe48eff36346ad2569b5dff522a513e645c87b9722eaaa3'
        'e05bca9074e69ef318d757f593c4ea6e4c2d7be3f3931e14ec67d8fe8c1c6731'
        '3cf8767f164c4d0d855487fc339c3ca645d72aaeb2bf29612318f8bd676ca87a'
        'c10ea474746ca0ccba0d5dd8488ad5f80e88ce30dbf82c8c6c8f644677fb8290'
        '2480b6625f2cd0178f68af5fae476a58f67d51f22b28720ecc9e1f1d2d1587e3'
        '5b2b4dfd04268046d5c34cae96c93055251bb1a7dc1c1064319f7bb4cdfe5ede'
        'c1249e98d13bd00ddb8e2ee02483cb84d5e7a48c6f358132355e58121981b966'
        '39f78f9a03dce4c4babd8bbbee2942d5c034b93ebfec75237b9fcad811560d46'
        '3c8ebd1a8a24c92a22d227531271f461ff04848be026c8f7c77ea28571b12896'
        '6a2d2c83d7802db7d1bb9d78b8bbbeb7b689ed0c16d495607a0ba564a83fb326'
        '2e16a8db57d5dc9d6f0cf1179057bf3cea5a3e802fb81778923766f1e21479ce'
        'f1f68910e3a78b631b9c72ee332e53417b173fca1829100707a2defaedb03158'
        '7b02c98c2fb8b923fa17254fa0d7c2abb05780a6603a7498f1f2deaaf73f4797'
        'b24846fb9e2544925d0f39e9893d923fa476768030b8f8fd75eab1f19e74e002'
        'd4273528fb4fd7f87434f6b130286d1df78877c973ec08da310b9a5ff1c899f6'
        'd41ac732002fb8900709c8ac58fefd0c903948131b69161c97438b63d1fd0c2e'
        '456f690d70add027772618e17f03d16f814b8bca6030b7a8531da9e1fd562fc9'
        '75607f6de016f5968365bff6f9ef96920b00279e46559c3ab39711c0b26156a2'
        'af11d35918802fb4b964349bc5497dc4aaa4b19e28f3e09d8b541e783cdb640e'
        '000624b8355dc3f4489cd7d8f1b4bbbfeffdaf12bde030a9434b323aed2396ee'
        '8bf1a82fba76fb1217ae2231bec43f179ab3d10bed4b8ec35db0d26c24e92def'
        '0ce7d4dceb7f002ea747283041704f777e4d95dc644157da337bfa33a3bafec7'
        'dc8c3c7ca319a616b73b6442df5bef78e9817eb082a02fa6d5924ec593a8a846'
        'c3a0e5a9b84a21596aff6a5a915eab45a8642a7600f2adbc8164eecb899fd39e'
        'ebcc457a865026849e781dafba2687ea64054f7d8589b0c151665e4666cef0aa'
        'a4ce645cbf511bfdc883fa2d802585100968952a4456f7ec0ef4eeb5825759a8'
        'a66f2f60f8cc5a2bd502c26e87def7ce5d468025854b3edbe48bb822a3c4c5c4'
        '647ad8af8990f69aac82f8ee1e1e5c57e15e5d95a379c173b025859e31bc2730'
        'ca383a8772bafcdaa3c6f4031697dde8593aa5fd8c742ffbdad1e2aad66ffe25'
        '978d2c9bafc4ff2834b27c6ab4a75253e26c77ed6bf1208e3e93f949caa2df59'
        '23566bb0662497c53e8bef66e5b9e35b03e21d4648d1e46a9c360c5569e7d25d'
        '54d6259e7b0c286d3ec037bc17e683db09a735f1d97424dd525df98303190cd9'
        'eefa5f216a4da36d103c5ae9f4785669a198c06cf8a26dc5a6094f5a87c48822'
        '1f542ebc083992446cd342cfba0bded20799068df450ecf9c1ef8d9cba85f7dc'
        '7056cf887d820f9897022268673c9264402ebbf41b93b5af6405e1ebb53c5f0d'
        '13db2600dfa5ac3c0f7c665888237008bb3d936c293b70a8ab40503805831190'
        '2ebb60faaa5f5064a8cda8a7ad54efde2db0352a4c0b1d0727d6b3c3a18db7b3'
        'fd348c5b695aa2043c036e99d9590825bb4132217827a257126287afb002ce50'
        '8df5f337985f7a20ca7a45bf4fffc46a4c52727d802aba68f0df9afa2bc0cb8c'
        '4faeffdf0ab3d373f0a18b545ea7fda504fe8a2990a2bad7c08743461ad99800'
        '2fb99279f7890d217b5765c2a3456d6188c78a86359ae831c27215cb7cebdb38'
        'ea68e8e2360bd9ed4598ddb928da11e029b8871eb864b78b6ab459e7786f9b7e'
        '42b2ed663997ac511ee338fdf75115671ce9a83ccfcd21863e442eb5d3132395'
        '4ab0bd7f5116aa37d6f3da974cd9917a516f2114576eb395d17b1e8110caa0f4'
        '787dcf9fb24e3fd3cc2eb4c42d8015d25053bdcd45112f8eda269d0428682b84'
        'fec10557c4ed28b8fb03c4f2de2934ad290ab164c351fb3830aaa67c308688db'
        'a287759a38f7726611385a977d5701ea6292e612e0f05e4311e8c4c314382594'
        '400aa04943584c34d8258f03196625577f7110ecee0d58b1a64a2d0081c14949'
        '7c3542ac54f4d0ecdf04be294f0500238aacb20f4591ef364c5c80714a198e65'
        'ac634255e42aa3391a9a5b75c82af2ee00b448218096a6c38f1968171b8dd2cc'
        '1ef013ec2565eff34dc55de22468f3e9b0999dc600088612a54192f8cf100680'
        '0221722d4e06800221722d4e06800221722d4e06800221722d4effffffffffff'
        '2807861bc3bb9518e01d8002c6177d30bb72338a87c95be9d18c887b7cc59601'
        'd771c787962248268029146bf92abbd8b86f46b25dffb3911b99e00bc2fc4085'
        'cc4519b72b0e2f0a4ffb4437c1002486573ee9ae72eae173a1944d6512d103a1'
        '544bfa48187d545f10152d4acf0dd39743009023865ae56003add8d12654afd4'
        '58e1a843e59ccc6c8f7eaf27511bd742a37358af9e244a24801a6812f33f27b5'
        '4c1e48a5babcaa82ee665626de2e18abf4d34f9fb9d0d40b41914ef024863910'
        '4fa83b07f69331fef65f82d12b3c1bc699cc0bc383e6dfedff0810c5f5e9ef89'
        '10268020fac6369125e529e328695bce89f36fc74f2ebeee617ff782a28e7cc5'
        '3a2625e54c85f03025828f9c8c30d6d83f4be657055ed188c8682afad1e28268'
        'd394bad8f34f133e88aa199b0d002683331d51c3f0737e3909dcd39b5830691a'
        'ddb963616300328c8f580b66ab62b2b7867ec91e6027846581f948c6a4b59ea0'
        '19ba75fe607a7fbd6152feecf9a4e8c05b57abda679fd42b26af173a802693a1'
        '77ddfb6f2f2ffbda41d06d9e6917be9830b1e743b0cb940cb1f43edaa549a097'
        'c48d8f6038b397d6ac57cf3c865172a77486e9b4fe71d55a47ff469fecf1e311'
        '86b72f6597e422cfabb25c1637edeff8ff38a76e03bdbdc8cab0cd144029b069'
        '4b0833d0105ed7fe6a37ac35b55fd182b0aedf8d3477bbd186ae17e23217121d'
        '4f13539c77144023aebdb57287ab2d92d1ba4006d2cf850471d23d5f1b4bce7a'
        '59454009d4af8bd07adf4027aee4a3063e06370d6c9a5ff937e1daa9a965992c'
        '94f60f60cdfe06a21cd194c2f7ad2542f4d9ff2cb0694b5172aeeaeaea608587'
        '2468eb88f6eb423c0a364b6e3ff55954f123003306bc45fd3a3504e11e8a0e00'
        '30b21bfd7707b4a01e73046172dc98ba092d1633bde346b4c66356ba26660cac'
        'ae2fab5d221b4d3ade1760cd217831ec0030b4682bc3f38b55018303edbcca84'
        '0c1321151862153ee90450f57bfeb7dec894ae89f7b17b151519f29d64904719'
        '6a402fb817e658a7e9d06246bbe3fb0950c7157a92d0f6c6aec56c73e37828a2'
        'beac0e81a05294f563b8d56d2bfa453d20c8248580e529bc3f675a898c9f1b17'
        'b43dae70ab2c4d39459c8f1ab4bbd5d24d3c9651eb10682685860131fc836f13'
        '9889ffd4e0b423f423e5927d7760e71ace91abe53c17970a619faca66c702496'
        'ec0d66b64d1bec6a5b89955c231ea2e2fd321ea1a001d23a2e5726fd078670ae'
        'c34b482496e37f2d09d189d07455cea7cdd0576a9e10e4a6a2973e4c3bbe4dc4'
        '474cc48e63926900269689e9e010b1b78e4ac7601e315f3043f4efac74e1f445'
        '142227ea094c4fd783f637f2f769e6268545d4ca5d7c1cdbc7cc683bf3304145'
        '8afa2557631dcf12f424e85b56122f7075d0ad628a8031b85835c7b9fe74392b'
        'e3918199e15ab49b12f27467c08c47e80dc3a6ae3e1398e13b550ae29c80c272'
        '91315d9720feafde2fb33f993aaabcc3c6ae5f4348458df66f6add10f85bc624'
        '8da94d391e43d11621fdf57055156c5dfe6c77302fbdce802fb2525ba8c03721'
        '7ebeeff134a69191b6959efb1e72d8da33c69bdda5b375fcdff981b986de391c'
        '1c486d6bd216694030b39c110569922957448c402d28e36f984ea86b919626d6'
        '70658210709c839a9ebea63b46517f2d3b81705913d3ce85e02fafaf1b3a5387'
        '67cb707fff3ac5cb90848a646801f21ada5e87a987f39708917bdb405fa6b738'
        'cb8346f91f7d31b8b630a9017ef7d3e222726291fcd3409f9dce9b2646cbaf61'
        '23b1ddaf7b5dcd2985d490b2022ac0bfd150815245c03a1ea8802fa7eb652035'
        'b165e3857b822564a6a7ad73b0f4b5d6a2a221758b59c8c1eec00eb94412b54a'
        'a0c7025ce7093d046ea030a7eb7437bf3d21ec6c5ab714abbc5c73241164c757'
        'fbde6237b5eacfdf31e88f5f75cf0813bc1edb8f2275679a5889a42faa20b5dd'
        '4324cde99109acd9f0002494d4a0365985b5616257890f1265a2e43df4cbe0a4'
        '00121b3e81a42e5e9928c030ad815d819236a1c721f3da98a7fdef50cd08c4b9'
        'f1a82db984e91d9b32ed203b50a79777c73c8196bb4b6c98d61ce54830b0a181'
        'acc76276a395524eaf6a652f9d8dd261090368165d23379b5d929ab4969064b6'
        'eb334b33006f90e3e9190f2e1c2fb55d58f99b6935751398389bc861b4dc8fbb'
        '8a686a2c820753d079d65e1ae502a297ccc5826a1eb4a2f023cada78b430b5ad'
        '13717ad3aac6ab301d038f4c071de3ddc8e18d7f94627483182f3747c2c0a7c4'
        'e82e23157821d5abf9f0b0ab35402cb3a52224b0d383620bd09c42645a9413ee'
        '0bbfaa584e5b66dbd3af8f2c75c4aef57975d199eb07565a1084c02fb02fd9d5'
        '113b65eccae7a80c716d46a180f8ae24eb225ba97ba5eb0ed4e4c69da44a5220'
        'c35b513e3c4be813861a2026ae7f6ca3eda9992df9bb602e37ed4869220fc626'
        '43bd1e84def2b74b9e2b959a0d0e135b15802caceb9bcfcd62b5bf6eb0e0cd4d'
        'a0867b9475e247a0788f4ff724464dd6804adcbd1b02cc326fb83af5c100002f'
        'b07c9896ebb59309f68f8aa2386f37a33b86fa035d03f88e28da3c18c7dd3f7b'
        'b6ff7511781a580daeb2a92f57078030b65288dafe03d3c0e3ab4739aa66cb58'
        '414ddb11b09a4273e55845f42c6d5042f3afdbe0bcc3932d99aaa9f5ebe17f00'
        '36b52d6ab1211b98e966d84fcf4c446a62161c92ae8131b461b0c4c5f4a4675f'
        '51a05abcb3cb1b3f3b600a0b73f7ce6bf477e62cda668022b67d803ef5876a98'
        '6bae6218253fa2f3b3c214ad0d2ba03779e37956695b0c6e36ba2683f87eca83'
        '5287d1063b25387efa76b2f5064ad6800e7222b710715e8190b917aba7a490b7'
        '0026843bb0873b8733ba5feee7c269ec4741c85688386af0c1dacebf7de61296'
        '79de2ed42c9a74102084aa213dfe95a8723f752251739020de08ed40ad0b010c'
        '710f381fbe81214e1d26858d3a2058b139f09bd7bc7039bb8443084ab0b480be'
        'e669ef9a01ef736e4488560c38f5d9d835bbf257e6bf25745a8b2fde0d507c8f'
        'd32cb09e30054e8d7fda72c8cbcb9e9ddbdd6d07cf2066d200f4064dbacb8dd6'
        'b118b6cd52c830ba305b1b484dfcc40ff8643638c4334eb37516328827acfd32'
        'd376e86b1a1c8d1338851818ce3690e726fbd85a3e0d982fb7a8340c8792fe62'
        'f014270b22f021c20fb04c2e38d4674e143565579ec1f45d2e33b106da0ec34e'
        'cb7d4112dad8412db6bf3f500a2fec6cbaedc02d51be5ba9ef6ac0190ffb5129'
        'cd206ff0ae782ae68051c6ba1da8dbf5cb9ec157582bb60a46ccd17755b6e954'
        'cf8775cd8fecc3d12ae07bde0479f59993ebe6fd3f75a04f3e7a50dea118a4c7'
        '8030b71034e4c3c4eabccf7d70307aa1cd99413d78d1404e5eed96c6da25a394'
        'de8e24148151f6caf7655f003bed034f467026859d0dcba2aa739e5eeb6a2316'
        '7e002f4ff7221d864e80d5df45b12334a647bbf62c79ea47a02384c8c7f3fcac'
        'f0fcf8f0acac37cd5ac23571c4d06bf06e01284200416bd7bbd95c9c00258de0'
        'a698cf1a3e1aea698a6d815db96cb7e425968f376f824a43203ffeff25729f88'
        'c7f60026811d6f000eaef3e7723b52095778a21a2698be99cf02269419fa1189'
        '1c2149593f6c96afeb8026858d6a12735cab8542d79c19cc3c1f2e752ff3d88a'
        'f146f84752454190513a912b06a5b24a802cbbe9031644b285fcfe99806de7d3'
        '5925a0bfadb28cf3c2812c472c6a3c3aeb25f678d6f6dac375d21695f84030b3'
        '13e48ce134794484ff338c490636d58114840f9f73a2dd3e942873db9e93ca88'
        '16aea3785744ee5bdd185f10748c402daa92f70843606b782d16ef8a86903218'
        '40bc6530555fe07fd4e0279604ac86e59d21b0dc48e639e2d545c9917828a3b2'
        '71df2c9c9cfce540df8b2c17cfba7c0046e3477f6461660d26871403f4e45c61'
        '1dfd028ed18024a28f0a98cf152c68e7f6222eb8210a37de7a0d9106aeb04337'
        'f348c5d9f1f150da92914830a6750f0a6bd8987121dcf757611e37cbc7bd592f'
        '2493fafb3b2a382208fb84faf7d91da5c2553494fef9fdfa052f31282ead415c'
        '8b938598496978b879d209b5da9a9ab9e4c13d798d78cdbf862b92f89fa71e14'
        'e1585bdc96b4abc4c4badc2fb21849f08b1666906e1b46d39e637d5ae96dcc0a'
        'b69879c2ec72fd67c6d851863f40c711b785893ac4181789d777ec2292c0339a'
        '16a8fc750edb7b428505181877f1c344cefffe9951a9f06c1d03ed93fec02684'
        '41f87bbe6fc3e15bbc770917e6e3d2c2ee3a262168e3675074fa5c00cbc15990'
        'b9cf2cd3002493153e882414af61e38d5896dc4a6bc12c5948b8fe028b9cd2fd'
        '5732d9484314379d5ae625932e2a3172c48c197c9775352ef8da2c747e795e8e'
        'e1c56795d677c1ce5e6f8cc1509d71c02484f9f51839888ee83cad0d05cddf0f'
        '8b0207e9ad3ae4ca57e0a0d6d7fea7f5e98de8b30033b4ca1f029eaacceec7a1'
        '0a01c28dc6e8df732cc5acd868399ff06cffad2f8bb099cfbd5129b91d8427b3'
        '73ce0c82a35298ed0029afb375ab5cbddb52beb54038a352d9f06ec70c6ecc1e'
        'd658997ef8749d837aab5b12387b11ca39046a25ad039bd1c2bd4edda2d083b2'
        '0a42f59b585f8bc0d57f128706641e0f3535044184811b708a26aca405d6f8dc'
        '96522db314fc705e548b138c547ca5392c101ab677344c35018aaf29fa8f7620'
        '26a90ea2c25d7fbf7410c6bdf45da42d41000b1c5c29572f97dd8c5b2e306faa'
        '16a22dfcd26ef62aa7f2b439649e83fb3c7f9c98a40ac9a18f5ca31c1facfb16'
        'd76caedf9208518414a172f09370af942f2025a7f2b0d55f77a9b3163d363ec7'
        '967097442561d2c76565134d09b861e8703335c75821fdca28a75eefd5eb1dfc'
        '05f8af9be8ca52bf866e5e4d40cd446aa1248981e8691963aacf6244e6ea5539'
        'fc2da798e57e0e1f1312cdf8a0101131e5161c5685e8b2698c006a417dc8ce44'
        '0593bb135b11b978a26b35627ba60030a6a10be2577dfa097ba7f068f7a305da'
        '91c53c2402a3d533074f4ce5081be076916a6954532e2592e804fbbc7a5b3340'
        '2ea5e06f1c3267368bd444e275f97ee8306fd362d7c8bd79062b332079438ef3'
        'c904ad630c5f0fb402e6784cb76e6c369f6e74ce11c6b3fe08a333e95135151e'
        '686d98b0843aebcc80d87466f13705d530c872e77ff5f78adf1b7e5baca9df9a'
        '95778c767fc025808154ad2fa3989c1faf80284ff38457b6292e28befca7833d'
        'c40bb4cebbdd442f259ad0802687692d391d8791c502dba09b4f04c62f2279ab'
        '64478977fe8d87f303777192f6b5aae367dd4e2586cfd9073feeb0248f15083b'
        '77f33e3be9be1449b861d2de79f128c31aac2b9f1d4af3b74026869f05bc948a'
        '68b01e89fa766a9a5bc25074adaae1e84518d6ef5dddc990cd08d4d327912900'
        '2186817b8b11136acb18ce0827a0cef1dc4463330cb86cbb3e1cfe7ffbf1a251'
        'b6e02786349a8fbbb71059d65548e62f5922c89f03cdb3a0b889e05aef0ff6e7'
        'bfc62dfb77457effbd2021865bf5bc23ca847b946fbd1aa428280e5d2b6bc7f6'
        '092893974439466b44a3a5a42486225f697df14867104d65d121173a8eb40536'
        '7354a534e2dd2e4871274220ffc58f83402580118744005d96b7648639db9747'
        '1be70c1916843edf1c9bd5a293588937a95808a35e9cf1238636080188c3fca2'
        '4489458be8d13a3b0fb7700b204c74a7f39dede5cfd5351a6b7ab02486349988'
        'ee494f791fd085048d3db00115da6c8d24837247c65654b359a799f1a78f784e'
        '268656beb9442b156d0a6bea58efaef5629da15ba76c212e063bc24ca9bf4925'
        'e0139b81cfb988228011804e5e1683876645e21ef90441d96328f3483693f238'
        '93b29126618f3f0c549826864298c0f6b580e7588860560808ad23005ea5f975'
        '8fcb8c86c09d0a90be4edc22b70cba8eb023801dcf5c67cdc0304e4cd5ef4ee0'
        '588450ba0befe449f67d292ae83233529919b6807026801a6a1224fd9f53bc74'
        '1ef7c922848e7fc79a255d872f8709c5e97ced5b6f63c967573a338024863c4e'
        '7166faf29c94230981b3f7cd7986033626a24c2e4e3a8f39e57a9471cfc40cf7'
        '742580118087697249afeca0ec29b9481c442d8b20e855de065ac55a27825cf2'
        'd756bcba94ed20258642d030a33006bc4042d264aa3c0833bab107b512f33478'
        'f76a393e1cce496d081826280b2380110f3a4ed6d55247ade840f5c3c8f75d26'
        '18ac4dfafbfd9f5b3c528c27d94d4a737826863497908c791d84e1abb8e4a303'
        '42b23145936abc126bf1153b0b03a2609165c7068ead5240258010c9e6881745'
        '7ab579d969ab936692e60e447a2a5fa0945ca27f3eb84351e15b569317a01b80'
        '0ecb392df2212a647fcc8b90a35a05377e2de60b7ef739785a9c07860691c23a'
        'b54006800221722d4e06800221722d4e06800221722d4e06800221722d4e0680'
        '0221722d4effffffffffffffffffffff280780090135eaf7281b8002bf52282f'
        'e414e64de60a1079d94c93a71d8fbed5d30243b380258633974ca61936f8299a'
        'b61041cf62b4d1513297022355734fb0e0c2651cca2bb64f5755c0258638ee7d'
        '1934c25953e697e7e407f081b9eb63abd5a858390abe95803ad3b8ebf6f79823'
        'c01f8635d1305340142820eda5e58059e6429c068c0a1a9366525b80d57aaed0'
        'aa24863c2604fe71c31f4d479c6d98641d9959f1443e1f17d1847a93be8fcb96'
        '624161cf7cd021801a560ff42100409081723e1aea7613750640e1eaa05e4fb7'
        '18cd8b48fc1f89d7248633957eb46f23afdd7d4fe698747665835fca4ae00c5f'
        'b76651b176cf1b0686be56ec00248633c4f1367a29d98c04bf22c1110f221d58'
        'f790a95b41e181f70df952d48aa145490420238633cdab228c869cc94fe33a26'
        '9bb214d51d7fd8058757a07edc49deb7eb1d52ced6d2268643498ed3d9f5ec70'
        '5545f30d6002f10dbc5f2f34904d21b2fdb71b242846b087eb5d304c002682e3'
        '4f083e013c075781071a5de12ca6863cf4d7dfb4ad23b180e77d4cbbee6c9367'
        '65f7158037b291b917f4f06aee928344c952ccbed7389f055c72639dba0634b3'
        '393db66e836d7a3909b97c588b7419c9da092442fe6493c99eeb29c22cb3d6c3'
        '5916f89017b0416743051e261217213879d6a7f00ca87208c8d087e605ed9195'
        '436cd6993bcd5eaaf02bb3cf216cb0dde1c2d4dad8e0c8dc58260c90dd6286ff'
        '0dbfb8bab2d8dc163cd7838ecbb1376fead48e8d6a2bb52303f3225a3c52d080'
        '616dd9fdbf3dbf9c01e0cde1d736bda0e65eb28f15834a60cd48577035d4b509'
        '002bb522c3ef35934de5fc4c580b7f2c03d28f352253fa541a0c56917d2705de'
        '9d744693a4abe7a3d12e1753402eb60618a4d68f587942f84d396dc749b27bb1'
        'e051e30a77dead48cf6030ba607e6f3437853499b9acd7c5178a7b9a2eb64832'
        '8b000080ea60b23e5f97dfd735001162a35d068396a58d8c09d9b0c1b639bb9c'
        '4ca680c556ccf74c23bc9c2fb6ba8bf3cc15f2f337fe1c76bd98aa4abadb9da2'
        '99357a8aa2888bcfb2bc1414443c3e3d2269e9bfb6c42b04febd582fb1c9dc8a'
        'defe65cc6f8fe912773a19dd4be6b8ff2ee947c3e0052154893d34457e789bed'
        '4e0065a7c76aeaf37340802fb009847d087f08e29cde7968e0bcd970320eb602'
        'dc3494b86918c06f685d655e9e08768aac9b51e56b0a08b7d9f22830ac72e564'
        'f18f9356158228ea3bee4a451ace742493aa2fd70ba6124f045c0e9bab28fca3'
        'c75708090c6bb0e75283ce0036a8f7abb7c218c616cbbe2afa899738c33c7c49'
        '690deac6ecaa730c582d2c0caae0259968df058c949192677a1cf78d35c73bf3'
        'f123803aa699502009d64923f0f77c28e0911b112e73430a84b39f5a51aa3d07'
        'bee44717d3fb972e99f8a4ad549e634b26d26d0e5ca08f347786896cb9fa3aa7'
        '5a9e4b386710a8bcb2204ab45e83cb7f2d62268d7ab21524df6e33c6b088067f'
        'e2c2676a2fa41f89e3d6b5091ae4a67506c902500ff2d515be3caa1c0c9556d7'
        'c1f03e0586053bce1ceab2cb2d47982ca80bd8be56f5601dceaf3e9f60719873'
        '486a223521aa32ffc3eb7d15576e6080845a6eade6002faf344daf7efe528828'
        'a77cee6adca8ed58e3eb198381ed1a158bb9bd07942164b43c0b608a1bdbb039'
        '2e27bcaee1a02eb2981e759283dff880ef1c1e98ca71961216dcc67378700a2b'
        'e2e5ebe49ce1371b958f3c35deea2d93b0c37563802fb55c07016c7cbbaaedd9'
        '0315f139c319d54fef8714a43bc1b4dc58191fbd083da482f925e2cf11011f47'
        'f0ba8374102cb32dcf18147fe342be5b6d484fefe1a067eb90710b80042dc163'
        '3e885033e471d3c72a4bf7ec89ff946b84402eb04b5510515f6247a6a7142785'
        '4a9132e99fa3b3a01d10e4c148f4aaee93c994c8bc37e877c7d7feee31524f0b'
        '802fae7db063845a0edae250b161bec41d17dacec50edb2472773b289ad68492'
        'e0ecda006d7ab6a953d39ee962fb4be0c02bacc9a1eb92724d611540ba66e817'
        'a510c890d9b77818f2ff1baff8c70e2a65d366415d6d38400dab01d80629ab4a'
        '9d9287cb7caa4d9cebb25f10392af0f0808e930afa282a0bc385e88b78ce7903'
        'd96e313eb1db002caafb8dc7509e8cfb17d50af59ac971eb8144ac9e7593c05d'
        '5aac3f3c6e660af4c7ff8aba9bd3bb23d57c3c0024a96297096b43f54bbb13f1'
        '0eee1bf53f64358dd8349ba12def5ba77827b3347844c0b06423a7b28c68e4a7'
        'c56f5c1b2c4274e2090e25c3327a66c5590016ff2e0782a5a1ea1afe602ba661'
        '2d77741f91950928797e9110c4142bd5c603e1f6a70fd0a491aec108418a6fca'
        '1daf90566a68851b3028a6a474c0005b92c1852535ca1854a74ed86a3e609889'
        '5c7b26feb766752dcd21f9522c69fc540c4c2db313e479662198ffea229616ab'
        '4e02252c5eff71a17927c23383afa233733882acc478c129ee46adca5d4a2870'
        '2fa985ac114a426c846c7775993f7fff3578d6d028c1864e38493afa2be70bb1'
        '17d2da9affa535bd7b6f6cdccd115aa830ae1e8c5f7be1895d06d3c6c7563da0'
        'c765a507a3d2f70b237c9db91619cee63514ec72fc1429e0575cc0304ab4b0e2'
        '002fae8bf32a9332c387afeb2c5352c7510e829445996145b7990288cba9f258'
        'fb762b2dd980699b756b3e29f8c26552502fb24fd00f4ca34712641a7001af09'
        '1f2053a3f637bfb769cadb9f4c2394fac09d3ebe3328c9fe339a5675cb2179ba'
        'f02fb623b352e3f648cf8f70ffd67b03389fe14d61282848174a2de5dddb74fb'
        '95eb719f54fed33fdce9416eddca9755002fb812e49b1f2a799afe187b112956'
        'cb443130ea51ad4792d2a2fc2003fe24549bd364fde7e86fd13e4f9c03eae5b5'
        '4c2fb881c37c26eea4301befb99b8d5c88c819a2b8abc3fa44760c2d8bac673d'
        'c6b8892db4424b0b45ea18739ec918dc802fb65676b25e3d24d3e9f086f86b38'
        'e0f1387152e47363eb40d624a531e290acff2afb3eded16c2a3fa517d16810be'
        '40268580a96ee0968c23c9bfcb617093794b8d9116d43fdb3f273f7f22f16e8d'
        'f38c14f3268544602296e6a9d3800ccbae3e0137bfe0fd5b7ddffe7673c62f65'
        '16c05d735f4c08ac7fa7802596339d1f2e62f21db0f6dcf00fd5c54436b93f83'
        '9547f6125b90e4a47af358e2373a76ac0e2596339d21c6648d9ef2467f9a6561'
        '5481184c558e791634623019b613609a386e5af64ac2d4258521de6ebf866f96'
        '62095e6844d0cbf1ad81b64399249643b73b4d21502d1e1235e045e740259689'
        'f62f7882e65235b2223b1ac24ee1a466c2faa392d43370aeb1ae53be7cddba9c'
        '026e9e25857ebe482271da8ca536d22e8670e2f6fc58f24b59c5fce7bd13a20b'
        'd38520ecda998ee35026859405d9e766a00d4402ff5e4c8be0b8cb740cd9ac52'
        '0b77d0a772b2157bd10dc6c5232d0d8031b858f4b8993aaf85292d86366f0021'
        '6e45640111501dbe07d5019311edf75df3eff550487ff80c92091268d5bfdc58'
        '0ea02db72f72f25da073e3e05d94ee76c7f9149a86f8a82bf8d7e7cc6542566c'
        'fa34e20c081c1ef537836f1946b62a742eb647a5667a6929bb88f93b7bb62c00'
        '023a7138c2edfb5db8b9eb677be8f4e419ffa790d9098c04b9881c83d254062e'
        'b72ee5bfdaec602d6f56ad12afdc4ec412d53b03f04ef7d4435e96ea4a3c66a3'
        'd7e4112cb4421f89ba972d5336ec2eb77d6be34661cef07cc78f26f54f50a974'
        '3e683f8c2122f9a2ce50d37186d12c6ac81a206804b106cbf3d9c31d9a32b606'
        '807fbc5367270dd56f5df9bc8a4e0d49be103b16e7bc268bee9b4ddbdd82cd01'
        '5eed4056106b7c7fd0b470d3728035242db5ce8a60ed1c901843940bdff5b6f5'
        'ce0b61fc958a22db58f4368429a26c7d3113bf06749b5a6c5e4911bf62c02fb3'
        '139bc36e6e62c2b2b2acebeb28a08199ed09268cd350f992162e0aa38496b748'
        'd26b88e91fcee38fcb0f51b57cb72fac3bfe585aa691d5e07fdae6976d1da6bd'
        'fe20e71d6715a8fb98cccdc10d05ea35100e710b6fcc2c4ee2d7c2f833342582'
        '91e19ab8ede7aeb7bfa80e12d5ba82984a355085f4ae857ee3e01a753a67be7e'
        '0fb860a025814442f13c14b69551e883653da0d8a2d6ba000688504d863c02a6'
        '7e5e5c71a8ab3868606c25807969a3aea269c8ac64b0449ac826bd7e38c07436'
        '24c31d83ae2a1a351da0000038fc5c0024865be7802b4428011b8e066077ef59'
        '1bec09296a1733076993a449c2d0d44e76cf5826c025865a4fb58d1479417d89'
        'f5bb1726ef6b3de818058200ba41b7643668acd1b7aaddeaa65140248641cf04'
        'd8005c35cc7c33526da56be77f0ad49c48980ae4f1fe51e30cb955f7ea90ea20'
        '23801c23a55ebf032e709ed87d746892dc7ff025d74b8f3672eb55809330f292'
        'e9713380238642d122f63a0ed78ed412ada905cf5cc8cd18cc87c1e8f16a5d6d'
        '471c2a7723c69d9424865b128ada2bbddcca5264858960d059c06d24a31e6a38'
        '9058d3a09e1a86e830f925cb80248633959a5ad58667ae463ff73ecfdf5ba2c6'
        'eef25c5079adfce4061f9cf786739fc6b0a82386349a8ee9504bc8f2dbbd6dd1'
        '26f2b113061b612cf3632cb944b4ec5d825e42884e3821801113362024c024a2'
        '78e60c69e2a231fc3111805eb6e51e6ca3deba70ab7981a02686324beb9f76c6'
        'c96fe32398940c7132f64eb2a27b3418a9daf4d98d46eabb704ba93bae9a8023'
        '800b1890d43815fcac568f18597356a752db50c87f81bd0dc7513bf31e685194'
        '5fa6fd26862319ef2f8d1d39aea81f8ab9decff343ed0bed3d7ed88733e34dfa'
        'ef0c78afea58e3b195c023801186327f5066503abca20c405455533d83be1e49'
        '0ee9ddbe7ca41ed81f08a270cd18238633ce653861a0c8379be5378fd532fba6'
        '51a5540b7a7e890db11b0d8676266b68e6a82586225a831b6ebde139b0a47309'
        'e2b4fb9916998461fbab0ce740bd55a757b44574aaaa72802686347b5861da01'
        '510d01e132d8b10642e4660d2e1b063a3d5c0b6d8270399172d0f2a725820020'
        '800d7c3b6d433bba00a05362f31aa4ffc593f188fab207ba6dfdac0e20f0c640'
        '08860e28649f4f1ea007860691cab8658006800221722d4e06800221722d4e06'
        '800221722d4e06800221722d4effffff'
    ),
    bytes.fromhex(
        '4e7e5a86200000004003010003ff0000746f6e655f656e00ffffffffffffffff'
        '96cfe627400200001e01000002ff0000696e6465782e69647800ffffffffffff'
        'c0f5c95f60030000610b000002ff0000706f7765725f6f6e2e77747300ffffff'
        'a87a11c0d00e0000d009000002ff0000706f7765725f6f66662e77747300ffff'
        'e665db81a0180000681a000002ff00006c6f775f706f7765722e77747300ffff'
        'f93d62c8103300001801000002ff0000766f6c5f6d61782e77747300ffffffff'
        '15fefba830340000cd08000002ff000062745f636f6e6e2e77747300ffffffff'
        '7d399295003d0000b322000002ff000072696e672e61616300ffffffffffffff'
        '6d8079c0c05f0000eb0a000002ff000067616d655f6d6f64652e77747300ffff'
        '43e6beb4b06a0000a20b000002ff00006d757369635f6d6f64652e77747300ff'
        'ca0056aa607600000f08000002ff000070616972696e672e77747300ffffffff'
        '413ca367707e0000da0a000002ff0000616e635f6f6e2e77747300ffffffffff'
        'e35901db50890000840b000002ff0000616e635f6f66662e77747300ffffffff'
        '76530583e09400005e0f000002ff0000616e635f7472616e732e77747300ffff'
        '14a02f7540a40000d638000002ff000061646170746976652e61616300ffffff'
        '27effc7820dd0000280c000002ff00006c696e6b6c6f73732e77747300ffffff'
        '28a706ff50e90000320e000002ff0000616e635f616461702e77747300ffffff'
        'f82ac28290f70000af0b000002ff01006d6f7669655f6d6f64652e77747300ff'
        '54494458b0c5ffffffffffff1000000084551101706f7765725f6f6e2e777473'
        '00cf8f1202706f7765725f6f66662e7774730031c112036c6f775f706f776572'
        '2e77747300ad041004766f6c5f6d61782e777473006f3b100562745f636f6e6e'
        '2e77747300a1ed0d0672696e672e61616300038f120767616d655f6d6f64652e'
        '77747300245f13086d757369635f6d6f64652e77747300c17610097061697269'
        '6e672e777473001c340f0a616e635f6f6e2e77747300d978100b616e635f6f66'
        '662e77747300a39f120c616e635f7472616e732e77747300d128110d61646170'
        '746976652e61616300189c110e6c696e6b6c6f73732e777473006c9e110f616e'
        '635f616461702e77747300cf0713106d6f7669655f6d6f64652e77747300ffff'
        '280780090135eaf7281a8002c60ec773aa51cf44ccc8f2acaf9aaab84574c118'
        '087b2794258623d7e9784776a59b3615d865a44094f2af48395331ec71d47b31'
        'ac22aae68ee6933e478021800f8ff5aa753e68eebbabac61c453a892de8d6792'
        '7f834b53128084058b7fd6702586223b294164e9b3cc9007151b7dc8564e757b'
        'b5d84da714c79514b1b67df0905f2f81501c278623d75af32d9764c7953ab941'
        'b91d6fbacffa6c1f8e35489259f837076abbca8e72f53468c46027813a0ed539'
        '8d88606cb7ec55d97b73366f9dd4338547b93ecf876cc7b4bd1c6fee14350c45'
        'f0b825950c9417e4bc002acff8984d89aed5366e60ef4c97338284741473249f'
        'eedaa828dec6f7862796a1bbbaeefc539a7ca90f27fe9132234fb63ba39763b3'
        '4a0526987eadf05ce6cd5b2df9458f202796f2206e7ff3d2f84801197608ef4c'
        'd4c1f1eb8e470d1d4b1f35163beb7d4ee3f40ffa8ce999402585b5b4354b3067'
        '93b3c88613bc9e6db716967f6a0157ee3df8b887b7bf803ee50c74158a402785'
        '94c0a9042f1ffd8b5c2c468647da77fbd135fd0d94fbf94a0ffda96e4feb6cfc'
        '140fe7ff935836bc3e3686d43946cf86913e0bdf5227086b296195fa99e283b9'
        'ecedc14dd295117b6abbc094894a5ca5f264b5ab93868ad441f952b1402fb957'
        '4026f760e4ee9cc4ff22f2f6eef186db91dc871affa0de19e242358e1ff53509'
        'ada8a575c49ef5fddd4b91aa7831b7878454059b79d6d0ece295fa2ca7869b32'
        '2111e97c423e087a1fa64e09c11a1a14f66b0eccd2d517415acfaa217583f030'
        'b699289eab2c9b6fd6eec7b4ae64f37ce4f0a70bd9eb9c45a512275136b30c4b'
        '8fb1c0d58bfcc71a4cd48abc5fbeeee830b3cd66703bd96402d8053d2df620c2'
        'f7227ea9d2b31bb2d784bd70cc94756f9e0e0b79769b7b63662058c15ac13dfc'
        'be2eb06797c9cf6f4914daaa9ad189693d5b65efa4a3cc19152e0c94588f1b3f'
        'd35b3009dcd5ba230081cd9a4c691fdd30b051e4f6d36baceabac18a5410eb5a'
        'cc67b150c89ba823d8bbd430f4b50604183b0cb84ac01dd17501e1a2ca058598'
        '0031b1cc976195431cc4679cf4934f2670497cd8f024e117fbacff96b1c4efce'
        'edb7199669cf9dfa7408915f98b3043a53c30030b0a98a556f33e62b64704cbe'
        'be02950a66695448db5ab4a4a36bd5d47538914026d5ad6af678c7de180ff02f'
        '7886e1702cb3b861aa8206cef56ffbaa3f11e1f13e0e056b9a05bf6a24ef3b20'
        '0e7e80c20db44e52842322a950c2b7aa0031b21a5c35114a34de8b28ba276d43'
        '24cca064e56fd38821d81f379358a2706464db2a492a73bace7b50faac77e477'
        '53ce0029b21ebd98f69095167dd530b9c97a38a827587964e8d431459cc5dcfb'
        'ad5e62d8cd85e7fd8ef8be98a42db2a150d7923fd8aa620dba2e382a34afb891'
        'ba4a3428658469da83d991c571e6becda62f0116364fdf8bcfb4402585138e66'
        'f937b5d082cb4733b02e20f8def9ca725122571058d007ea38c786267b4f44ee'
        '4f2584e9d39347ada08070f90bd5d2d5666ff1faaa127f337ca66c0241ad719d'
        '7cb68040e8d5202682655c5f7e5a047dbad2490225c3050f6a8bc7cc49fce473'
        'e46b402b6a95328e0e21f392c1e0268a3a98a722afdeff830163d3f8e427dc16'
        '0a42fd3f345ecb16f562fa59cab3fe448fd81092c8319901b7078c3885d0cc0e'
        '8afc842046a0561ea3aa85442bd7241814267318a43aa2cfbb6ab25a9feb6383'
        'c629e7214ebd00258546669495323e45b03c954502a0fd1bcb779d0efcd43fcd'
        '791848f8e9b357c4e002e687662685a21a2aa30f9d5fc8bcb5539975ca2e1fca'
        '7f9cad3b095b47938965f29879d5b80efefdb73c31b876910a406a5e77648923'
        '027941612eac2cde056c82b80b608a9c85177d6ec2e829b74aba875d35e5cea9'
        '848102588b0031b5bb76a3c9a0ee12a67b7c55187aca4b31212bc06a0bbad2a3'
        '77124023a33afdd319287e8edb05bc71369ca84b541ead802ab6017127465e2f'
        '2d32281114dd71bafdd75caf47ba9ad9cbb0df59611d1f75e1182fbc11b19a52'
        'f544de2db60932177b6f204a680113f6c98aeb4907015634950a9735e3618e72'
        'ca6db9832e756d377148a5ffa5f047ebec25b604e075c5885a0d3ddda6fbec94'
        'f8d4e8d6d2483ae1477931102f36dc9b3cc820b88d820027b609321b99d5ae04'
        '0139ae086834b1264ceec88c56370980ec4cc13408e9d9332a35ed40e5397828'
        'b60911018d219084457abd8e451e0322aea975732c960b56da2ba34b1d28bffc'
        'aad9032390bcf5002eb60aa9f15ab42ef715a1c055a3109181615f522eb0809d'
        '9a989b0657a71180218dd3e913611afb8351e9c549a96a2eb5da4b10189d3e3a'
        '891eeb262a5e0261b1c565a3295883df9c603ea66f775d0f64b28e47830de5c1'
        '54e6463a8c802eb513a2e043670279641b80d4500df7058539efb9e9a9057eaf'
        '53ff541578c828b0823ed91e9636150c01514177d231b604dc3d126c70bc5e68'
        '37d7ea9f0990c5c780b3cdade5aabd3c9cc9d85ca52635d73f41800c5997c4f9'
        'c51888980bd64030b6207554af78f72af084c7bb65fe8acbe3fa02761c2de234'
        'bc1b76f9bfa6f390bb142c9b2a7b27cdf84e250e5cbde88034b88914406ba4f1'
        'e81e32c5d76bb56a94fe184f1033ad4265fd2d5d5ecf9fc45ddb6948f55a881d'
        '403fab9b4d0abb08170717e98037b69390dfc06ff18b469d75d9a99463382222'
        '82002aa60ee720e982e5d83958862437c89933bedf65debb17d2f2e133616451'
        '675a17d44032b31580337c35e9a5cc9a4a92469f6ba48eb3cab3e3a99880ef97'
        'c8e6c384e597ce8d8c6a8eeec1313dc267d46ebc0caa0c0031ae43e9d0a3fefe'
        'e8d8a4212c50beb7b2b009901f323f946a98a5237ef49107fa526f2aadcbd101'
        '46c40825e6ee844fb65038a54fe4e58bbddca390222598e50aa88c5372090beb'
        'd3555714586f322a7fbb69edaa03a7e142921b995752041ff3122d623df7a6c0'
        '08bc4038a2eb63afc18271de6c0abe4ce1fd22b32e7e30400b5d399db9a9823a'
        'cf5c5904528ba56e90985e77fe6a11fb6ad7747a2d2f265d5ad1dc403ba0cf4e'
        '9d0b1c96de51d3f827e88d4d00de3dfc2cb6567bd4688e0ac5c0114f0a129e33'
        '4995eb7eb312808523baac3cb4cf9a348fc7439d6aa936002781b124a340a1f3'
        'c14bcb0a1ff255f18223f6afe96fad231688a432fcc9840f3d1c2fc91ed84000'
        '2780b95aed8acb9e3c38035639228b35ea7707916a28dd4dc7a89708a5dbbba1'
        '32682f6df794d8e027804eb7dfdc867ea49a1e534ed869707da4cd18c726027c'
        '924d618a724c6466aafdb52a76aa2de027865e3b01bd75b40257aef8c570cf45'
        '5535506acddcfa2e74d42043d22c7613d72ea4844d0c8e802586349a0b8200f6'
        'd52c8b6617f980f2c76fea3500cf4b72ef22ac6a7e23630c26c5f3a86a302580'
        '111de85edeed2c9f5527d996da96d8c91776256acc96e820e41760b5fff05a4b'
        '998c8614268633aea543dee67e8f698f2b07626caf1e55f1eb5c035d3fe6cef2'
        '3e989dac40a793639e3f00278633d60c7ce7c6ee66e871283ae870e71cf7ceca'
        '5f8c33e780020b934336422d1c54004a41ac8024801050159664987ae743ff85'
        '67fdc836ad57890a84d78d825fa9f392b6c48dfa5b92766426800b1865492473'
        '93b8e2454f72f33248fc2eede3d5a0be0e6670c60c1517ed008e8dedcb388026'
        '8622bc4b07908f373d18b4e65ee6b7c3727142f1eec0e7e067c471a92e131093'
        '87c4fdb3dd6024862ffb0c2b123bda8fb6e0d0bbd5d7dba0abd91fd45e1032c5'
        'bec9965f94557f3a7ca6f4278622616360e664f98f95790b936b6c1e39fc84c7'
        'b3f9ae50defb19744cbcd0e16b2e5bfd3b9700258623d773adab305adf759b2d'
        'e4385258a7394b4a1b8546d7ebb595100d60c12b73dec3f28021801063a1b20b'
        '08a7ece08a485e1f1d4cca6a90e83d6600c793dff022482a3266001a8620942d'
        'a6c7fe05838d397c5318d5593d29fcc03ea8f0f578fe07860691cab865800680'
        '0221722d4e06800221722d4e06800221722d4e06800221722d4e06800221722d'
        '4effffffffffffffffffffffffffffff280780090135eaf7281b8002bf30234c'
        'cfe2f241475ac3b8f0a892528255ba1a7984e293a027804ca3c649508ce6db2f'
        'a29cc20eb26d350a21dad620d81e32191b07c2d691143707c6f286a05023864e'
        '8c1f0a7cb79934b00d83246bcbae468c6d535b1da587fbaf2a38a41e09badeb4'
        '382386226107b2df813d5050091ab3a2a286bb6f39973b153dabbd991971e442'
        '6ad9c4868426800b1031529fe3636c5ffa360a12ef5ca3dae80bcc7f4f0c8aae'
        '1120ee2f061fa9a2cca88e422586231d6111d627c88b1b426c7371f75116ddd0'
        '826eed370b1bdcd6f9bac643aab570a872c025862ff920870d2c9d871ed060ff'
        'e608071e2c30e3d2c458cb1205b14fad9c828eb11ce3534026801281807a5d54'
        '62f6367992331e41fea39a2103980d69de217612fa6231646f73845b20b38026'
        '803159577887b17e5c3b1cb309b88f27c25540af9042f022866ec613187c4f39'
        '588ec8c34170269437e71825c9d3669aa3ca79d4db800a1277138b57d12bfa82'
        'c43001220944fb30ee2a8725562596bdcd5b25556e8a5fc304b744ec733e6396'
        '98e1ebd1157e43697853e0344ed846b5e966b026976ce48cfcd9db86f5e7144e'
        '631a2757e9335248ad4e5768cd8a0a3c459e07dacd55989a56f02685a6014f71'
        '9234bce50a065ac6eccaf061ca53b4339be7ccc1331d68c53f2ef18d199efb4e'
        '802785a7043a4dcd03a12015773c64b5219d232a68854e56042f18834b0344d2'
        '0e2779a52341cee30036bb0e7b26ec5d371de79abb0c87c534783f3bc5519582'
        '330560bb73ca5b4daceb70b795c4f1c1aec2b6647768b55508b0b53649775494'
        '30b85bad3343a0cd762ca827cba149d889838852ba33afd6a97a1c218ea9551e'
        'a4d2aa8a4a536030bcaa9942b1851d98182eb699d22b045eed2bc33e9b8ca662'
        '9c38ce94217e0b6f78925668d0053ff24e250fd8cd6551d7f1d461ef22eb04a0'
        '2fb521e9d6943731aed98db4199c4216a7da652bdfd2bfc7b592531aa8a031ac'
        '73b2dceb9274a10e7a68ebe90360e76030b55dfabaafb8841da57329fe85cbd7'
        '03fa51fc1b5082f5a16cc08a60cb904e157f88bbed8cd38fc16fbb181c18ccb3'
        '0031b51dc0f59f23f36c0d817dcf1f5139eb384073545638399ed6096eabce4f'
        'bc75f34f4a95288bf7cb42e0f176637cd9b3a42eb7dfbb8d8d5499695723fbce'
        'dc2d9d94028975d8a428b7ff8ff327427dde34d621824dc7f1dbe2a99b949223'
        '095834b785ce0ba00fcf00f175c00d3fdeb10d414fd1ed49d0b344332ff2c018'
        'bc4d4afe3e46967c2d40fa04d4e86746b7cb9d217a656030b60501afa9f0d883'
        '2cc43181d8e9a149b3eba5c28efcd93667fe63bfd005751e1196c98b49aba20c'
        '3cb06c50aae6cd8030b3cd324c1026f917d67a02caeb16656f0bb1cfafd0764b'
        '73b968cde0d8cc144c3f7e0bce3f591ff36da9f89494de668c3ab40572442a1a'
        '41855e365b081b9ad6819c1de6d60ce65b98e5d66f86f4bf6359cb6e1def921b'
        'f826fa997c12aaf1479f5359c9e72c688e1fdb4037b5c884a5822d12ea9dbde3'
        'c3d3855d17d0cac5c134f0f3bba21d95c7d961f0948f1c4269615b60bb95be36'
        '3652c43c449c7c19c3e43a0027845c0019609944fcd115998e5dbce025aca9d3'
        'ecf6d3ce6982ece391c764b4652052e21bad93f0268266fcc32598beac374331'
        'bf5dafe6e6ea93041e204791a4bff91c21f5aa92755d95578940c0278cce5372'
        '7d01702f6b17f261b09d021c182ff6c4f4a3ea797b3b0d2ee2423b9b18f34769'
        'c20726278094cb40926f81dc8930ab1a8f33f2467e59a6c5dd570f7091feaf0b'
        '6fb34d7cc8bd7abe336a80258035b6e99483f20a981fe7b4f08f7eab76d151d1'
        'f7f25c8e92e72bad867fd11c0c1a308a0c268288e7eae5b3d1324e5897ffc37b'
        '522bd5bf90cf794bb72e987608b9d91fe257b2d58638cd6024852982047c480c'
        '510a0143c025b453bb3b8e5f3850b7c4056480ca848a9b9cd0a73f33c035b94b'
        '2f17fefa0fb4d6533f5dd5ce33ce893ee7c4d6b7aa2d90fca17111d8d7f3bac9'
        '0d06a1ddd29f21761c2f9f14c6cc07d3afce4e31b6a1214607a54ebdae553f83'
        'f15c2640c428dc187f5232ce04db48ef7ed9dbc56fe95b5fbbe3c100bf5ca105'
        '2106b1ecec2db60ac93f8eb21615895f26466b1cb40653bc659ea0e74e112510'
        'f30087ebf738fad4cad59027ed54d5bce725da29b5eb7b8457a31a7fe73dff08'
        '0c38c529bcbd9c4c930abe3be106c2d307d59bd41018ff028d0a8e15002bb51e'
        '364c23ec4789190796910c5ff9844176062339e3e0527cdc7948e6847d2bfd6d'
        'd4d40b4cc545e1708026b523856c339eb0b47e3d618fa025b5a8719a93ea7794'
        '7e42515fae93da7a78d7e31c350fc0982eb521f8595f5e43c3e5142201d5e1b1'
        'a40850cf565b6ffd62e7d5c77621c9e296c1ac524bfb004c2068ce52317ee030'
        'b500909da0ad9b784b28c029b50da13e1d7c3416ed994ec17e20933aab877897'
        '19b40e1aac325b21f76ef4a764142cb931b50b2723821949974eb7e1f3b0b800'
        '71d4bf109e74ea1d3e0560e1b88aeecc0a46e59f3a231ad78b4bbac1aee4e5f3'
        'b4682db560210f3bcbe0f27d0ef680d9ea224ffc04de4a0f01ef4de238448c24'
        '08126c735887504e5cd6e9a8fcf0fb8020b51ddcfa2a541c40659162e6aa2eba'
        'ea510d0a34c3619122babcdc78030812002bb32a151e91bdfebfae21396a94dc'
        'f50d42976246f8c1e9bfa91a994196f15f455e1ec8f46ac724a1d9f76a3bafaa'
        '98e374959548c414166bdf0f7cb79e271a25e24bbf540ff8cb56fa6f8fdea97a'
        '2d595c66bb7d6c8f2181bd346daf2ac883d7cb5dad6e81f6c030ad411a274b53'
        '9c858b9b26995904ee7d8139c5db58cc568ea9ec648d04fbd47348ba5c508aa3'
        'bbbd213d5da5f674b2402784410724f21cc844431e94020100afeb85c1257cdb'
        'db89d3d8e37346949ea20e774c5c698f8e6026845c322b50628b4498b95e3e41'
        '4baa858146bad66b9ff43d3b6ae7ea79996a63d1659077bc1325906c99f52b8e'
        '93fc8485ed286f557240ef97ec432f7cd1d75892c5a27acc392098b2801d6e26'
        '9238854e32c172c540d98b87aaf5398316f1ffe9a8665b0209f534323e25dfda'
        '34c5a26291e02583ff000780df383fb38ddbbc993b73f18f9a26e4ff8d9934fa'
        '255637a3618607ef0e9fd9002781db1411b877c6ab8d07104789b88880f271c9'
        '4b6ebdc0369fdad602d5acbc18f166265c93b2402580b89573fc6edda888da80'
        '8f980a3f9746dec654b4be2458106fde87120f8ea3f1c8553040228053e6ca35'
        '49fe45d3938ef74b4d4d121f7a3262fb0e57b918329deda6e58b4349002786a4'
        'f934257ef9e6ee1225642a49cdbf74f817c44f0316a005ddda9b3b0f67a78967'
        'a5cf33a18025863c540cff58cd0054e83de404de4c84d06e92eb59f4ec2ae1b6'
        'e4537b1acd7bbbf7896024178625f0f536265d35f0111e244027a7f07f1226f8'
        '34e12007860691c22c57a006800221722d4e06800221722d4e06800221722d4e'
        '280780090135eaf7281986087892e019b469667803d2d181be4f467e4f0a10c4'
        '93a6b2248011743817b94019f7e4a9704eda2100846348cb38c66fb1914a29fc'
        'f72adfa87dcba8542486426f692c15b2ea5978e55d7ba9d164b391aabd0e01bd'
        '5e9f588ff28f45ca9caa41ca8020801a58f36bb2b8a23980dad6ebf48417fb73'
        '2c23f2920cd00e491290477da5c02486360a13f304a9a505b03d62157cc28238'
        'f419193319740a3e81ca397a1296056e8deba0258013a1673fbf3e7980554f34'
        '1d26862f33ede422db24fd79b888f8c647e17e0e9143956d00248633aee18eb7'
        'c72a08f47e1490329d3e3d385d84338cde75a6fe7d9c3a4e4c5593989c702086'
        '349a06e1290dfe913e6068f2bac8940c5a919343ee7baae245ebf8ca1b55481e'
        '80111dc2408c008c7a1169f01db584c029e204b3f51e739aca961b66024e2180'
        '158e5ee5f2e04db9c9ed6871f5f38362247bdfb3b8964a0c8d4734ba391bb0d4'
        '1e801d1de9f33da8e52d3455eb3363d924b358776442656c14b2a2c62ec1e024'
        '80111e0a6da4f957c906448f415af5d8530f46b91a1e01b820be293dd27b4681'
        '4928ab20238634a5df15f1a80aee470e6f4b17039055cffece2a748902a97c2e'
        '1e31ccc2151ca4002286391023385ee984f46702bfad2d41a12d45e3ddf8a1df'
        'd79456bee201071171ab0c25802f56ed90c8adadbc6c6e8d29d5350f71fdce7d'
        '73f9dbf95532aefbded2cd645760820b803498c9c910384c7b97a36f4a86074a'
        '0e6bd886ece9350f5eab805530ed74a52d602483356eda5d79266a7e58f6fec9'
        '5c66eb1901902f99ef3e7843fe0f5a6aa4679ddfdc11baafb02a18a8f696f5c3'
        '48f7e24d9c7e8c7b64769df109da7226b1bc4d91a83a35adb311d86c3b12a13c'
        'c3b86588fbfe9d8ff5957dd0093d2fc2f03404ba8eace24f8ce96ab7fba45724'
        '6d5f51a9af3f7e105a4622802dbac90e7ad3ca7e72601b7995c0c44466d95886'
        'dffe7d8f0aeabb803f5364f61a243eff3d88df62102e59388a9b2cbb42ab48fe'
        '68a1df5ba57a5b70e41b76dc079914510db139eac7211db709a6e2789f484c13'
        '7aac36161108d82dbaa429ca3f7e0f08034acba1490347d07569b0067b0457a1'
        '846c9ab9900b3b55b3b99e274ae0d7a2f82f303c602ebb21702d55a6f8b06a7a'
        '7c8d1ae9d88478eaac72d683acbf9ff6f16cecc50c8144707146874fc1e74e47'
        '79ca579c2eba89caa9912d425f16071b701036f1bb2453f8874e517c36cd0c2c'
        '2fa479bd2623da8436d2d91c9eb7017d9d0f0025b976ed6f4bb74bf47e2d48bb'
        'af84736dac4c56a73473b33a70fc42c6a14b1ce2cc158e24862eb980b6cfa3bd'
        '222648e583cdc03e53447f91812c4aafb7f1858a145c9098a14373894027ca0b'
        'c5cbcbd730d18fdc2db69160938d178380049d7d7f3e53aa519143a7ceed337e'
        'd6e8d30f08457cd16a6c5771bbdbb4e4165fbd8cd29e2ca490280c0d0b370944'
        '7999ae970a8141a98dcb12fd8a62260535cdfdc97f43d0736f31e36c2a234aac'
        '6492f82481a86963989e8e97995f9b4f55f22c1d8ea50e7f7aca5a5fd68215e1'
        'a36f4188a00281cc248528e3b60b8560f0a0f34224d4f4df611259e2024f9a30'
        'b17cca9bc445fbb10d35a57a7c38b5d188b3a124c88021ca182798ea2ea0ae0a'
        '9336a1427d9de70567d5dc0377bcccab6e4f8209a6acb64ea55b749a851ae871'
        'afe3385efcd82dafffac4185efa3cc5dbb23ae6b56886973eabc70c4856deeae'
        '1ef02953ca0b8f082d838d8461de7b4d6500abbc2cacf96fea3a13c669f83196'
        '7e184592b7a340969c08e1321e1aeb985b6f09579e5637a14881bc2605cef250'
        'a02daf34080dcb00491f83a9c7cdc031c72647b0d3771b907b939e3744fb9bd4'
        '2386b7bf9e08bdeacdf16aa7bd05002db53cd04429173c87430449a4035188e8'
        '6c7f6570e9eb0c5468c1adb2c3778d736afe7e00685074f5738e3d9ed82bb6f1'
        'a2e669f7cc909414af0a2050a50a57eea2de917bed254b357f6b002041bcb72a'
        'c630a2acee1b2242582bb5db8b9a851bcf4f32670d318001d42bd717254ed0ad'
        '23910f633d2b6a7b5cedd773034fc97d9b94963d002ab622a03b8777036d60e5'
        'ddd505dd4cdc3c60092cb623ec20f2797d8dc53a898b55a56a5abc70331b9830'
        '32b8662befbdb3835a106d565131fc562f115d9de604e3d6c3c66794e2aa4cb0'
        '9531542517c3e8ddbb217cb61240552f2dde7835b68bb5cadc93ca3c7cc63317'
        '884dfbfdc2169df06d2d4aa2bdc38a7c9f8760960015869ea0f332fe48d4d0c3'
        'a403d1acf3ac0d378033b01a74f9f8d360b024fc05308bcf0a286301808726a4'
        '8c4aca7fdb5716b258b5af2192fb409b0edd156e0b241b5442d53e37b82db1ad'
        '7f04adea9ba1c54ab28e7591a80e41a57cfece5f426bbd8ea55df12d8d147661'
        '32518fd8784d6f826578502aa7ce5807f4ab5912461573e5ba2802202977cdda'
        'fe2b5f896feaa76d6e31d96e3c4fe10c3f7a584278b02da673edf4ef9b278ae6'
        '23accf26f5f2ed0398eb0988c8eba1770d77fb36d66b9e3e06a257c15e39a69b'
        'b06827c02ca65a501c95f03a31a6ad9c9915b04cc6cde7ec12a89f9e81f314ec'
        '4ed4e3a27a873bba3833ab95d7440d49002ea9a9a0d0c8f1166f97492e9a6bfc'
        'f92c85af9e4d799cb86f57a728910b755dc8dd3b750a178cf6c15062b1d84180'
        '2cad14b9faddc25fd8ad883cefe9b726a0079b0cbd5cceeeb99155fa00539af7'
        'f3ad6d9b17035e01b2150e72a02eb65d476c112b1cc27bf277cd800ac4528477'
        '0dcb9e6611c0b0a216d6f91a432da44ec6916e418a4cc28dd0db0f7025b8bb88'
        '5edb355fd6b28ba02ee750d7c341d5706cbab29f973a798f041cc56160aeb14b'
        '7c402cb7d7b77078bbca82f83a419344519761eb2f9874f2d2f9dabbf0460afc'
        'bd9b6aeb489f37928e0ca2f3823f002db8bf78c4fc1a2d2655f7475071027ff6'
        '5fd2c58fc0290f9c4707036e0afc1f8e076a584e1c0b88363002e5e9802db8c5'
        'fe9afca0cc0eb3e316e8a0488f08559f518d056e341a185134f2a639b67df4a4'
        '4f9cc8fe219ebeefc7ed602cb9a7fc8160294d67c57e1729b845cf61d8a37fe5'
        '8f9df71ae11b6e70c654e8c3c254b5c0a807ce488fa3f7002fb954a0b5ee7d33'
        '24ad68a562d3c69304a24c3b14f880970b2341642cfec46440b31fa291cf3f14'
        'e3c27e1187850f002cb8906f3927e3ce668e65fb9a5050d6b2c4d0902524808f'
        '02e7e3a9d780ae873190f97a31bad594cd1227497629b7b9f1bc7dde4410c7e8'
        '8a43df597549ad119ae27610de861dc762dc04dc82b7ff818c1206eaad91e82b'
        'b7b891f750c74cf8f7b4aad9f9dc5535a7d59d6ce723d49c74bc6fa915a9dfe5'
        'dfc38f65db583e9e1c7a1c2eb93ffcde6f2caea2663350aee74bd85caa510ac0'
        'be8bd4051f5c904923b5b4a14c5ef4f68934d729c433697402002bb6885e93f8'
        '5f43e752df9402501b32509d1b88f8c5c7d72030ee32f167df768294e6e171c1'
        '42bfac2c55e12daffc0857cfc515b39edb78b056deae90cc1e167083f800cd0a'
        'a9da00cad49be04180988d5839d2ab524701df242daa96cf32f4bc1c87603ac9'
        '8e8620052b4800b7eb2793862fc50bf46ed723f2b47f62630acbb87a8b976b66'
        'db422ea5d917576646cd12574f6474b5834fd73d1adfd8cff45a949927614e74'
        '2b0894cabcef3e957909b2552176e1e14037a0e4496d56c253ba0b7b73cc822c'
        'bc5b92266a6249e42427011f952d05860b5d0dcc86af29a6bf5a6bb58f7aef8a'
        'd970694701793c5180228e1028a4dd3ba02effb396b9f597c776744339a78296'
        '4bac918fc4e76ebee6da14ac2481c40443b8b55347d7f7968063033087a2c77e'
        'c148dc8787e70e1fb3904d5e819673cbd024818000112febd685bbcffa3c0534'
        '3162c8de9b6ddbf3580679b76a6b5997f672933176c0238033894cede0b5c412'
        '85d655693f1441e8dc5a2ccbeb3a0660dceb9800d46c4c39029025801ac10974'
        '4f5af14cc2dd4af018b36a4eea429a44db104e27c94fdea5a9efa048612ac400'
        '2286349a65b72763f6cc8e277b5c3aec0ca6d5fd544711f68b70eeb235bd4a1e'
        '66f6b023800b0e85e458704793fb9b4190f6b0f8efb445615ce40594018f9306'
        '5c91802723217624862235e86132b9a00dd1204ae0e65c8c0a076c0fb82c386c'
        'fcc1c85203f7db349e5d8cb024800b188f60ac16604a940fefcba8305d79ce7a'
        '58342969474fbe756701e563b3f97b69c42586349a0cb8f559761cc5b2dd384f'
        '095c1957fc54316006774d8af5f2a70a5a49d60099e6c02286424fe9522b428c'
        '66ffa51784c5ed547a5958dbdecbe96847535ff1524e83e314a0258638eaf225'
        '6dc74f86f8fc675826fd5416242a563382b13136aec76b1d8ff0ed2f4ace28c0'
        '22865c167487d68ea9b63bbb78b36601839f7a75dbea479374eba24a437af4f1'
        '0e6be82580304d5cde01fcfad2f7024ea8728f9fca200421dac367dd331f3183'
        '68a8eec9f12c2a9a0021801d7ae94472531e0bcddf86815f0125333742ae9785'
        '51a22811b8a3ceebd1e1c0248016a21423f7f942fcb2227eb63c8579d404795c'
        'aa19031bcb1541b02a0be669687a9d4024843885ad5d81c197b2d935af50754c'
        'bae9a12fcee2f14a0a3adc23fd2b01e2c8b76471802492381f0035192b799118'
        '8e396ddc9907f5be5601c3c1c957bcffa615a969ecaed4b570d0248445b3576e'
        '9524a72b7c5711beff180f21d857eb0b2bd03d6dde32f299c03cdc5b0dcbe033'
        'b1cc22a5939c5690ea0468e89619809a8f5c0ab12aece6ba747a1743299a1dbe'
        'b854b6b67d6ac14137437c7506da931f1eb1882db6c025c18bbbe873ac7a2067'
        'ac0f1f4b29ffd79c03b476541d2c7239e2fd1b3cd0a22df1da45b73226ed7663'
        '0027b503299b6da44eec65db884ff19143123e1f2d32f0d7b8c29a6d691586fb'
        'fb8769e0619ac914f82bb3d61fa2317acb539f115e9b45eba77ee10e3e523e5c'
        '21e4763838f8e6d4fbbc5b19a98811f0ff6ee6d46029b522e3df3f8ce8974867'
        'cddd548214870cb3731a2e88fea097cde488b7cfcd5cde0f61d62aa7272d9a2a'
        'b39a64980632e8cadd0b9a9cfb7225276aac9e414ca42502cce271466078f3b9'
        '5f7ddd6b7691da185a402cb22358aabac800d38260fe84204f308e630936ee55'
        '8a9ae247a5f4399d548e6da1bc065effad00e3bd63ca002593a8aaccf54b31c2'
        'be3f67ae776737f650bc3ac37d38b2663b231df3b31517c917ce19c200258480'
        '72854291546f8428e8a32fbd9f61c616cf506dbc5bdd9340df33d11a289c5796'
        'b26b70249162fac0153dacf52230f366594076dc1d5bc5b1d99bead687bf6750'
        'c3e9d912dc164e002283189657f5fde89d50ba1a27f1c955c3ef4fe7d4749fbc'
        '157183e2a18265c37f22402481811ef7cdb26693f5cf0163743970e344ae7b7c'
        '3f8ae1f09af77e7bbafc436fdf2233002580d8f46b5e00b243db94bb0b033622'
        '019f848093cf8cee5997b5f83d0c08355b3f75044ed82380943ff31f62cc65cc'
        'ebaaec4d646de3650bf65ea8ff180cb934048aeca0edfcca52dc2480750368ff'
        'd995ed64289af4adb9653534e713da65453dda2740f485e4a51444c01d008024'
        '839cd5574f98b2aef1d943e488cee14554d66fd70f1efe781f52ecd95cbfc365'
        'b83f47982496a318822d0e64c840213101b7da039262e948a503f88a4dff77ba'
        '3c86779d4e95be96652496daab38ea9152841f1d8ce19a89531e0f61aaf2ca78'
        '465573dbc6f955ab2bcbeb72e4182495e0ebb591658083b5ea489023a0c78137'
        '683b6a28169859e12b8ce508da0f9382fa78d2258504fb8f65a4c424347dd479'
        '7c0d37c275e30b11f620a05783f782a307384967494ac26ac035baa6f4c6e10d'
        'a3a507f87cd174465f932e78bce23f2b8616701f373124a6c3f5fdffd5f8b952'
        '623def7169542aaff6abf0b568b8002dbb0ecd5511f5e32aedca11d5496f2af8'
        '5de33b0aedb8b654a0fdc3595b027bb9656be29a12aab991fac480de6028b867'
        '575799ecc42fbb1caaf821f1d8aa348eeb8804f0f63fe94e6c87a7c945add5ce'
        '0a77276bcc202eb7c936ee60ad2ceab00dee6cc89690cac1ec51227a3004f8a3'
        '7bb4c588f965ce89f47c23f6ffd9dae582b320de602eb79c2781def37038f912'
        '6af75d63b7aec3c852b1b2112b85b501d37886e09e69ed2be47acef4bc62ba8e'
        'a5d8cfe02eb3114504ddb5839a026f75b75a0437cbfef6408df663b2595c9f17'
        '4dba1c145eafe3161c80c2c7baa82c85ebff622fade4067831f932a130848d06'
        '94a28c37d070841c592ae809bf8d60bca3817192e6c360176e0c51df3efcafb4'
        '9b6000339e2ce96d01a38d264d25d6c99dabe3b00795da9a26226c7939c5cbdc'
        'a7ee34834df310ff1d8a798457d9a36ad960434c649da02d9af9f930dfa43df8'
        'c700cd9ab066d2fe51f5cc9981282633cb7165eb1908e57884a21c9b2410f7d7'
        '587580b1ae2584c97e0713eca0b457695a57c8636f11903431a283aecc2b2832'
        'fd14cc6963f01ec9af9f002484cde4b795d455f82bf3fe9d2dad763453e4faed'
        '27735c21dbf77ceb14c6855975fc1cb02283738477b0edecb544cf1910c8f698'
        '625b9051d5487009173e15c29d023e0b7fe6a025838c5a1528b695b517c10058'
        '6127b26615f86a810011d8050c0274dbce48f0c5fc03c318502ba8cfd8c3818e'
        '31c2b580c1e9729f8d072b31a046f0729f831ff2ecd34250d909f52b9a498422'
        '07758542602eadaee18f665c2fa056d54d770f89275866b14c5d522b752fd852'
        'd01e74b1262bc3828508949b7682b0b99e7422102ab98b40b446ab172d8c81b0'
        'd6e5dd7402e68a7636f9a8b3fa8d8f4d3ffbcd7710d3cd0bcf137bde8f5cc02b'
        'b98b2aa26eaa58ab2927ba172de06a7a44896d65073d85b103ffe0ff9c022ca8'
        'f9f34ccecccb8ca60bb8002cb9e4ae3fd637f5b99e3e149566ec31a248ea3dce'
        'f47df6c96883ba6c59f8da4a7206fbd49ac8a7336b333b1025ba59f23c9fc93f'
        'bcad171bc82ebf23d9a84af5e0af7ecaffa7ee9502e1339bc62fa17a8d082db9'
        '8b15f26c47a4eb4fd0bbcde18bb55b0d83dc8a7026233af2006093e4c5278959'
        '4c15dce190b52ab073b7cf0a2db9ad366caec7d5cf606fef207bc8cf2837a851'
        '95fe151871837acefc4bd763a2a933d049ee90d2af2f4e06d3a02ebbfe2279e1'
        '95b0e1c3241df1eb5c6f93398aea9e2c487716219d7d3e2cbf6a0076932e9113'
        'fac5df55e43cee82802dbb1ccd34be22da0c0e31107714eaf64e38c2c60d8b34'
        '7c98f63139c43b58460e3f4f869bfbfd19dbade53b66e02db9a6391d12723eaf'
        'adb9eec7b57328b50cdf16d8049a919155ce10234bac70b92979c5fe2dbbd904'
        'b031c22eb02cb9a6111f096c5f4b24a825f8bb19122d7b8eb58b3ab1a31eb257'
        'f4c8d8cf65dabe516f3db4b4af8da19577c02db941499d6d28a233652cc98192'
        '1dd144d3718b132402813a322ee24e255f6b2c47c5c7b58784abaf8e436b8fed'
        '29b695e0d16aeb574a5679dc5bcb9f84ed3520b3923499021ecc6537c28446b0'
        '682c22a3eac09f6663e42cb173e0bef91229e94dab5f8f9ac0a0710eba3dcf80'
        '375d72f68579e4e9ca53bd173399ba08922b3077d926222ea93d0f87564ef65b'
        '2021efc6dd696a6da045ba167b68c201b6803f645b9cb36c7779c3b0bbf0f2af'
        '47ce41c79b402ea4a656c31f809e293d4a041bdee655f7d8c5441c8eb61782f2'
        'd8b48f1c271a4745f2039e13c2afd64b0044c33be838a033e155eb2134037d1a'
        '0baeb709a9b96d48080bee7cb8ae77084317fe9843d1b9ee1857fe8fa3f12beb'
        '28f87dcd78473a0a32be99e93860349d419bac45457f47f21608d1950737b936'
        '309e7071dd07ad81081d0072eead8c41686a90113da07a2c2277fa1940ac8d14'
        '21e430248a448fce6a6be4c68aa05a1064b02ddd806859d58b6eb3c202a81b7f'
        '78905ea5ade323902488515c0be387870c145ce6a2c70bc9a3b31a1178b69493'
        '40b2ef942f7527e366af129400248059bd7ccc6d08bd46c2a918a9d6d9c43d6f'
        '397e5b7c502c575f2decbfc55c78afcddc00248027c47ca84d67174d73aaa65e'
        '401bdb90180c71c92b214db8ce696aa569b5c7184555082580290ff919b0486b'
        'a01c1afdf7aa70913aa62ab0a542edef5a59346e925210045343011b00258027'
        '2a0751b0e1f05d75681030fa06573f1874336c5c65dc20644c633128154b3256'
        '550c00218022594da5fe3c5d47a6e8aebc4f9b87ab8c34ef9b1dede58669736b'
        'e8b327428822862f9f7067ddbe885986f288e362810f01af1955088ed0e333ce'
        '7ae40fbe22add0dc258634c74741948c03f53dcaae30913d02d00190e630c0d8'
        'bbed85b6d4ca878dd014587fc2402286513a880f21ed3b8beca20a5909910d76'
        '5fbf537e7f9536868508c5aa4057c295b42586390b89c30579076136a30d0fba'
        'de8eff6f8d24b2799763a7f55660269eec11642c5a0d402380353ba3d8e0639d'
        '97abcad1d8bc5424199318c71ef9b7c65a4093d80bc99e348bfde8258034797f'
        'c04782f7fa5d706d846ddd0f37fa828dc5a5b32c556c644d82ffec8cb81fbe76'
        '6023862f9fdb215c72c273feb4371b28a9a03d3cf9eaac0ef278bdacbcfe0c1e'
        '879e6c8fb823865039da786875c0f761531d889b4ce5665a830fc8e7b7b38373'
        '01d2c62d5e010ae9b024800ba1195d01f7fee3b5961ea04158d2537b22101bc2'
        '809b299ca4ed56e4bc9b9b00a6002380155159fdc9e1007b661165f00661fab3'
        'fa29d9447b3b4f7b08f9137e657b5d59f2c024800b63a99c01fc9d488c6405fb'
        'd1d160440f07d7010cea432bac2ae6a8b0c494303c0c2d228635f8eb490f818b'
        '2eea0cbefea816077556100c64f497d567c697cae6468374d700228010bae962'
        '0e1715b68c987ab302a7bfb12fbe73deaae9943de2cf91c985175609a024865a'
        '4fafe10e5f4df0ed51074a433129ce9766dbb45fb0ca77b414e0816f2e43087b'
        '0f642480138d8af6d2b0284f6dc32abc6931fce889bc7f1a92bdbf6f4adac8f1'
        '8fda08a669c260248634c7d03b28a3accf8d028da4781954b814e1fcf9f1c796'
        'f42ab0ee730fa49591011a702186221cb344b852005e5ab855413b1fb0187a4e'
        '514706a026a19c63613185d63f6e2386347b92ca21ef0c39aa1810197ab9befc'
        'e8537da9d2b1f3af8f4649a5021bd6e4e2002280111b8fdc06665e7576b87954'
        '38776ceebc86f1cb9279b2c8e573c5a16866560b00218011056ee5541be5e07e'
        'ced8c3f684ff6d8c3aa4be31082ada581e0f5c63293d80238010cac5687e7ffd'
        'aa567cc52ae3c154fd530b75a47a9308215809c95b9677c261c6e02086327e27'
        'e91e4a1d3176f80592779175ad18ae6e65d40c2fb8acc241cf34908021801014'
        'ca4d48911a091ce26970daff9d69d2f54296b86a34f4c6f07dbda3e236200c86'
        '09d9d910bd2bd877f06e2c07860691cab8658006800221722d4e06800221722d'
        '4e06800221722d4effffffffffffffff583d811775677a6a4a6a8dee552cfaf7'
        'dabde76f4dd4f7955c79e512a3cead9d2634bff3d082da6456f6504be8a30a69'
        '2574fe2d1a9964a074b32e34dae6001e9e1e6917b9f5cc219eba3250b8405c53'
        '68b3387b165ff3e532e45250f5ac1c994f752feaae13901ef2ac27bf47e702d8'
        'c8dd3751a4b7afeaaf6a2c219948a216618d0fdd72d5eec03292f8125d9862d6'
        '6b5d501e81d760c4c775e2807823994f752fed7207adfc1e257cdfe4911c0bda'
        '34c5743b8d35ecf5b46a983f7f406b2e404c9accde94682705a50b2fad35f5da'
        'c80e7321a995156589c49118974f3b764a724339b80daa2fba2cce5b18d8bec3'
        '49caef114aa5378be4f8c5e6ad6c53bbd38a19294aa4807470c4ade97d000988'
        'ffc0210b766adec0ffffffffffffffff280780090135eaf7281b8002c60bce37'
        '31890be644c420360f2e750efa6e296ec56976202028800b626b84b0d2153082'
        '260c256b2df714e298dee6e2b3231031613361586e1f9055dab572cc27802486'
        '2f9f79d81e8bbdaaeac60cb99e6ce7a3b32a9aa24bb8111bf3e9b5bd87f82db7'
        'c9159027800b424f485f438153ee578fa6d54e8c637efb5df8d8ad6235b2b08e'
        'f9ba459affb070a8061b8028803cd6b3e465fa071224ab5186065c8c8b93412d'
        '81903e1c73839080e8f03e4a3a7978a9778eb2c02786c4863c9b7bd9a750b851'
        '70394b9c60fac4ea7b92c641d6f4430bbb686a28b781cb236c2cfdb028803a75'
        'e8c46f111f97297d8efaff000b7eddaa56b82c300a9aecc6a12bcde1a907c1e6'
        'ef70aafc802380332c5b8407d00e2354456337eeecefea2f3d3b00f01319d3a1'
        'f26235c58a4aaad870298503069d3e6dbebef36985e8c473eeb2d108fe96169e'
        '0d9e695e575a90962d14921226a9288af06e402995ffe5648319e1ccb76f585e'
        'da31f5f932363370eb21d513488ad12a15251d6389c08d807cbd76a2902884e1'
        '65c390fdb08830722d89246b3b37723f4b072399b4ada7baedccb641a4768ffa'
        '77d1e41b9dd233b59669e88428c5f0ff62c4f393dcb7eb0e353c9e4640ae7439'
        '8ba9e89d904196b3e8c1c6e1ced50f41bd9e700a0f9b6337b55033aaa9951ae2'
        '5e7e0ecd6876358cdf171f80cd08f03ca8bbf0f7c6376f856516c22a0bd0d9a6'
        'f452d11c91bf63acdf0d15c431002ea5f741f21f8d1671d89483458ccfa8466e'
        'f48782301e6df0418bea760f7610d90d419c509ba8d7eddd885896235831a40e'
        '1002f74c9fe81d5c8a82b2c432b1a090829636bdd55facf0a1d1194934091aec'
        '47f0d49e199acd1a805b12489761102ba22c0155e1b130b982285dd8e6893b44'
        '35208a1dd982aa8a2dfdd5399c61fc28f5391042b5f5073d0ff4d831b073d53f'
        'efaf978944d27f7fe2b696cab189fa20cffdd3fba1b0cd3bedd99d37fb611089'
        '04547aaca0b68b85f78e71140032b2b29ed10a3af9c04a9b28a1e25a44ecb5c0'
        'ca67d36c2b5587150622965c119ac346acee6b040cd29c27459d74f8556d8960'
        '32b8d7c4162b0c4d8d547a2717f8a157dd0666c0a4cc94273ce2bf15bc63ee05'
        'a5ca34016daff59282a19fc985348920b1748032b981bc8894df44a7ba0ca942'
        'c35206461822c804f741e2078d1c00dfa716f7b958a7b56d96e5cc9a19de1a3e'
        '533b1befe61031b98b48ccf86756568d99daacee1050f0679667e7aa781b40d3'
        'c6e563fc48a6d73c0fdc72c28443bdb31009e13fb607f13831b9337c3626a423'
        '351b640af35c4db3d49f86e5584860b6708718973ef3c40bea4a3f589b3a6379'
        'f766969cadfffbc7f17632a5d0a20a43cc88442929fa59dea35f01174989ef9b'
        '9d86c448b69cee0d9b894fee17eca269574f90c0c45ce23bcebd7a7170339bbe'
        '1194c613a985ec57ca5c18472ee68492f21843149c5875b2e148271e296f4f20'
        'fe67d895995998e06eae83e472e967501c2884eabba71231ceb3b392e0340306'
        '5c1d53c14e1266744f20d1c10705a6bd82044b8332be66801b3029849ba42f31'
        '05a8170bef126ecefb6c24b5568f45a5fb22a7241a75a5bd92e07c02457f5036'
        '454d22401482e8001779a628f14841714bdb0e189e85a29510278139736e229a'
        'e48d7042a5fe5a7897df26acca05170d3f106d72c69cc9cc474d4405cdfb305d'
        '3c298444ce844f1b3ae2eb3f78b1804156ae57b99c6470ec0e232a7985ca386e'
        '357ab13abbb2effb6e8b502593e7ebebd6e54e3cfb77cb19a02d89e31bc50f74'
        'd489cfe5ea7bbc12fae720a26c611fa1c027849c785a137842aff0c2f4cbc7c5'
        '48dd92c74e76473a9dd1c65b422cd20ff36e7e6116bb14f29038b50d52bd09c3'
        'b9b7399be5fd1b46ce3ec1ec06b98ff47e400287d89e6978e936ec059085ed38'
        '675f3c4e5e617408d1441f63e3ce7ac8740030b3ae0067054a5661eb4b656c4c'
        '3ffaf1d0a782b53c6c2e738614bab0e5db278acd70c8b02a348c4d6f06b368d8'
        'c5b7002db3d6758db261120dc674c91ae1366062e2ca15c8e35ed26e7b7709ea'
        '0663f302723bd9d080f581768b5a66d6002bb33e0bdafed69fd68c4b51f30cc5'
        'c608ae90e97d10980a6b3ad90fdd33ccfe0a07179fa37c605720abbaee29b223'
        '7e04b8663a698b09c59b2c6cfedf3103dca1eb25647fbc001d45a806ccbe1a65'
        'd73c25718198dc2ab01257c3af038ac3b6af2d38d3ce4c101b1516e4aff32750'
        'eb57479b138c3490cdac140d32837c34c7d432aee9a2e4afcd2b1554d468c761'
        'e39e110cb2cbdfa540ecc81ab9267c8d4a058a2c3f9603c604e1aaaadb2049d2'
        '1f5258b1c832b02847131aa614cf7cdb5c368a99868188c078a428f79fa1d907'
        '6b2aaeb91d0bc04a36272e5d59c395a179091ee1eb7b53602ca5d0a04b01839f'
        '8365e546fafc012302279f8d653099a69d09b9e22d89186e33684c2edb818d04'
        '11efd53810278073bbd4ecb1d4fac1e474c24e1f617c26767fc759458126edfc'
        '03a06a3b9dd482ced93a809640278437c619e7a5c85f0830d282ec29cd27bd9e'
        '09302592b70eb4f54585c46810a115bae2cecfc1942883179f4b2acdee0e3443'
        '725b96638182f43730e472a27560707fdd95ff21b4c4a0734727c70814802881'
        '83df28ae10808103060b70560bfe16c8e9a741234d20e39bfee9fd09b8b51403'
        '15467491edf3002987198d40264798d6474b6fc662f8a798bf984b2d7795b570'
        '3c05962fe26d4d9aee7990659009311e0023865990e7a915d10ffa61859a59c2'
        '3bebccb89b67031410a6b13370ca1cd200f069baa82780118624ecd27f7c4b2b'
        '45c6862e9cc0da78e816c4cb0409e31096cdc21e680172a1db518a6c1028862a'
        'a37576ec6c457b60b09b7fa97f29bff5b765fc0222462865915a38e81aaa13aa'
        'd98f13d69f60288622bc48916b73d7ae3fb35d84888b05207016123bd9aa9994'
        'afc68c8d3f5b0b620d1211dcef4e0825800f74151433c3fd18b34185f1731f66'
        '0df8155e18054dd8179b6b464c2702c7e56b6d92cc1f800ad83da58e04655908'
        'b8e73867a79e6842c796c147e573ceaf134f6d26800b86068960ba2d385137ac'
        'c006800221722d4e06800221722d4e06800221722d4e06800221722d4effffff'
        '5dc001b0984003007900f036816410f882e8f7af800000000000000000000000'
        '0000000000000000000000000000000000000000000000000000000000000000'
        '0000000000000000000000000000000000000000000000000000000000000000'
        '0000000000000000000000000000000000000000000000000000000000000000'
        '00705dc001b0984003007900f23680aae1b80ca2b228300aa402293437cd9cfa'
        'fad4dd6b13c26f519c3573c0dd812665d16db1be9faddf387cc57ddf9c74ae91'
        '3435a32885b84739f152906648cf1a7dc330b9f79d9ed68808edfbfcb85a34b9'
        'a5d74abe38c0029266001a850025722049bc0800000000000000000000000000'
        '000003805dc001b098400300790104569203a019f548606db8540294020d077f'
        '406bd021cf4fcf5fa2c53eaaab5730882499c9e1a45e6bd5e1f5ff8fed1b2d3f'
        '6af99442a942e52345aaed14727ec7fcdcd920827136c819e75f20c6db613a5a'
        '2292fb27b5b7109959d55fa202c21a1d745ea8804ca939c43600000000000000'
        '0000000000705dc001b0984003007901169db03f1683dbb102dd241c11b5071c'
        'f3aac06462d8df2a4d89100a37b6e9827d875443967312195b64219f9f79cc80'
        '03b87b0f4ed070dac4467c888994c048e6f8091d6f9e88d6838e4d4320db4d6d'
        '97346f1500000000000000000000000000000000000000000000000000000000'
        '00000000000000e05dc001b09840030079012cf6adaa7012080ca10700014020'
        '9641d33d53b212b0a1ccbcc39342442622980462da234b9292cce3c9508849c7'
        '24407dab353a945d2d2e961694a952847666003f237c222b0ef8abd3a2f57ed1'
        '98113184ff0e1bc4c00000000000000000000000000000000000000000000000'
        '000000000000000000385dc001b09840030079012436ad0a981889840351005c'
        '20e28000529aa882811a94ddd2e8cb638b001d68e3b1888fa59083818d0fb306'
        '3bbf64803c400e890e905beb8420604cc0a33e17d2e15fea0533111e1bf0b525'
        '6e15933cb3e9b99fb26f12000000000000000000000000000000000000000000'
        '0000000000000000000000e05dc001b09840030079012636ad0b00238c1c8ccc'
        'b8218914b29a1b05b31cfe08005ab0e96b85ae4b74ebc8646150a000fa3a401a'
        '02aa38fea9af303ba40a3d8cf2b8df43afdfc1b64048018c2e263357a7d5de3e'
        '0000000000000000000000000000000000000000000000000000000000000000'
        '00000000000000000000000001c05dc001b09840030079013a36a0d630f95564'
        'deb903990bd897755599765ccf9dcf26aa80979b4b0de9400000000000000000'
        '0000000000000000000000000000000000000000000000000000000000000000'
        '0000000000000000000000000000000000000000000000000000000000000000'
        '0000000000000000000000000000001c5dc001b09840030079013036ad70b0ba'
        'ea2ab7150c44a419da61380a2384864aa610996791b222563084c380a170a4b0'
        'f8d93f1c471cd27c9b5446612e9054a0e3ed37d839ea1f64f94deb6e9bb5a4dd'
        '6d24ef24b784999a4486824148911ac664ea2809941a65429a90de80fbc14000'
        '0000000000000000000000000000000000385dc001b09840030080014836ad6c'
        'b24b0818b99c414e72cab48c21a3ee119c23706cc76f9de6b857f43c1396f27c'
        '19563138c8267254e08b6e0c212c670f6ce52880550880550884550884b3318c'
        '91731bb29efce58ca76cd648fed21bed247a931f8277f82779eea817cf02a8f7'
        'f61b6f60da9332deaef4ecd2f97968cfa15de5a36f41b7210000e05dc001b098'
        '4003007c012636ad50f02b041c046eaad544114b0463a48b215daeae9c49edb5'
        '8e440822c7d0a5242512693a6c88689185d7896d72559abf5bc93c83637bd63f'
        'c79b23ef6349dab20aa9c19ae82a8499315549f87eecd4b85ae2f262836d1877'
        '3b57a9631546a5eb2c68d4e6dea5008ef4d534939134091856e7e061abffd1c0'
        '5dc001b0984003006f012836ad1047330804c201a8803010720006d2d562c502'
        '34902e4746794e7a0029864d80443cc882ac3f672542144a0ab4c2008d804a32'
        'f6383a364c3692e6cf74bc39f370e5fab85ca48e1bf0552568509cb48784222f'
        'd676f060000000000000000000000000000000000000000e5dc001b098400300'
        '7d012e36ad90446009020e01199783d603eaa004ce7f4e20a8042145b11395d4'
        '4e0c226a0540520d8041a4ea9ceebb4f2e430d3c850b0430b8c2189c01092fb3'
        '0d5efadfb1bbb706ccbd97bb2e86d2643573e0a0c069cf10c0fa4603e9181ed8'
        '046d805abf98683fb780510f1047e3e90c07a4603d2181f25b5c447cdf805dc0'
        '01b0984003007e012836ad70400c15880240809820e54c8abe3ba2aac052c115'
        '0c8b1c4501e34243392412fbc9a22262f98dd4024217e1b78fb29282527e21e7'
        'a47c1d8fc060f3f7dab5b7ebf66d64655b2745904e68e8b26a97929c8238b534'
        'ada71e4e2934a716cd4a9e2d5f68e3ec64ae4cf5d295d6e748c3520ae7a074d3'
        '0eef6f5b175dc001b09840030070012436ada048200586a3013841c0000002a5'
        '0ba0e2d51da59faed2912dde50313f3488a891daf8dc9e3ea68f621e10b698d5'
        'c69e283f00356312709ebf7c0e5b0230f86772459f38a8dfb10f652f0be3f290'
        '030cf29cebc92c33308fec86f0200000000000000000000000000000000e5dc0'
        '01b09840030079012636ad4c30230806c30120404e1073265680a8ba40f153c2'
        'b131c908d780c5de0d81f18400621b6e3a4b58ae5e427b68ac4d2a06f6c5c5c2'
        '52469bf37843e3c8092613911ce6a5935aad525747666f190000000000000000'
        '00000000000000000000000000000000000000000000000000000000000000e0'
        '5dc001b09840030079012836a0f644100d86636180dc80d54aaba082916a2068'
        '31e14359fa5b84e87227d07a7193904b37c6486253540ebd0688a1c1c2ae94fb'
        '4dcea2303cfb505addb5102ff325003ba22fb6348b95800144d19fd21a56315a'
        'ef78880000000000000000000000000000000000000000000000000000000000'
        '00075dc001b09840030079012836ad4a301b0c468301a8c1ca8cd6f329ed9729'
        '8b480aa4f8a85cd243fd1823745f2c264e7e7c9c4c8e030dc207b6d0daa4952f'
        '925e74c50ac6ea05759047c8b0dc7b721d91761980020429cfec96f190000000'
        '0000000000000000000000000000000000000000000000000000000000000000'
        '0000000e5dc001b09840030079012636ad4a301a8c16a1070000414e0149dcb3'
        '8abcf0edf7bc59b8328911108ee7724f483eb7487ea9ad577a3a79c79878675d'
        '61915feb2de62000000000000000000000000000000000000000000000000000'
        '0000000000000000000000000000000000000000000000000000000000000000'
        '00000000001c5dc001b09840030079012036ad8a484208048100b881acb55d04'
        'a5568a90a4941ae35144af1ebf94767a7b6285035aa73d56928859f0e92c4d0f'
        '5e3a63155a07ce6beecaf5eed277993992d41aa4cab3934a2bd496f8d4cc8124'
        '6b6df09dc8bdb388748d36cc95a99e0ab2e0fec2fa6d7343bab672cf7cae3aaf'
        'd3cd7a9b8145e0e05dc001b09840030079011e36ad4e66429402e206b7a73d60'
        '4aaa5dd51742250de2c6072db82f573e81460d6788ead158f904b8e5b99381cb'
        '020af4df73575f38f3552b5246e175481cfaace9e5ccbaf9c584cb5b980d4881'
        '7cb438e2345681501071c6800e86f28dfa7b1ab99bb431bd799b9e3fe70e2dc0'
        'bb5b38000000000000075dc001b09840030079013036ad30600b182e109ce56a'
        '948a58a2d240d108420132432708040b0ec4793549a2e4108d5a55984e75ba1a'
        '7109f82218fc410c04e22ea9ecb8989863d8624bb7904265d08899681933e630'
        '79bf553d1f2bf329ef9627396d9ec96ede160000000000000000000000000000'
        '0000000000000000000001c05dc001b09840030079013836ad40640834c20e4a'
        '4894059277128da02fd91a6b4cd59e7555fd464523c0cdc2320ccda5059cf085'
        '5f57de7600000000000000000000000000000000000000000000000000000000'
        '0000000000000000000000000000000000000000000000000000000000000000'
        '00000000000000000000000001c05dc001b09840030079012636ad04202b8986'
        '028100d82028083939bbf5f8f7f1c5dca5963975531d6ca30a38d7b1ec910bca'
        '90c944ea8478cbb846e2e96db37eb7ac9b5cb905c43690c89e514c3279e8d73e'
        '4d61827debbcf3a085e65f7c7f235bc440000000000000000000000000000000'
        '000000000000000000000000000000385dc001b09840030079012636ad0ae610'
        '0dc2027083865f7ebd8bb61632e4419c5f93dde8008de7a75b6f034912941239'
        '665aa127d99d4efac100022baff4fe8e08f6f72405c178e92ae91b6f9c3b77f1'
        '668cc1d9eae7269474270f6f91bc500000000000000000000000000000000000'
        '0000000000000000000000000000000003805dc001b09840030079012836ad0a'
        '301b9804c201a8404c1072377cb97556b2ad2070ce26ec2c00566dcd4c8177ff'
        '2a2ce1158df07afb60803067bbdb35f4746aae75a00784814712c6d98635cc7c'
        '74a004689223fff9b78d80000000000000000000000000000000000000000000'
        '00000000000000000000000000000000000000705dc001b09840030079012636'
        'ad0c402b0c06c1033841cac9ae41212c4364772d186168187c96435e64ec4a8b'
        '664cd15b715c78b011dc1ea22b5ea087aa0664af9186389a35d38a7d36ede500'
        '0000000000000000000000000000000000000000000000000000000000000000'
        '0000000000000000000000000000000000000000001c5dc001b0984003007901'
        '1c36ad12b6280d4401a0808c20b00000056b6315f55cd1f5d516dcf549654379'
        '1c4a11694f93bc304fa1642568e78ef6668f0381df86c2c272d3e25b6f63ed5c'
        'dbea41abfcce86bce700dadda18781f5eeb179f2d218ebfd309fa73c8a49597e'
        '2911092569b048ddc8f75cb0d400000000000000000001c05dc001b098400300'
        '79011e36ad0e76280586036080a020e4a00002161c3df3bbe660187d466ecac3'
        'fd07c422d2812673527d6e7daf24f47ff2309f5b01b830de397269cdfdff433a'
        'd348de9f4dad0067c341c9f8977d7a4bbf77e14eb984131b0931957cf641007d'
        '86b78400000000000000000000000000000000000000000000705dc001b09840'
        '030079011c56ada63622a0050107000329108292c2cf3ecf7be27b8bc67976d3'
        '47e13f8840062089a42770127e03393c0309bd04cc3ffcfa9f8be9b75b696df8'
        'd0d0d0a15fbffcf85009baca9c2fdf1772a7ae50f643863fc01f3ae9e8fd5204'
        'f4f18a580331e533befeb2d31a6f94d9216b3a66700000000000000e5dc001b0'
        '984003009501109d8ed07b6c3cbc1cde641999bdfc66d59fabc2a9628201091a'
        'fec025273e442426fd154d0a1b58448bd8b067ad0c4605696c028e04f4f28847'
        '884e116c7367c7ecf731a83a0a1c972cf3012f33f1323980909bd4c8c42569e2'
        'b21e61e424b07208fa06e904888f53c26082e8fcaa487b95ded34083d3968ea2'
        '33aaa4b7b3465f15546451d5fe6d0f5c3f748dee3ac35133fab85dc001b09840'
        '0300870124f6adaa462005860350806026305b26f8b97ae767280ba4bc50ac4d'
        '2130b8776fe63d954f7a36761123289418c4a7d42590d092e05c0897c3df8da4'
        '7def9923a0ba468c7bb49e931aad4f58b53d7b419cdd13180f1ace43e8924dc3'
        '8109ccd7dda09a39ff1b2b91fd35a00e40c528786e2f68d282b500b084d27b84'
        '85fdafa956001f1250385dc001b09840030059012636ad4ec61809020270839b'
        'e00001b694b1d42f0bcccfdd299cdc433d2c1f8125dab5bc82788f0f11d0d3fa'
        '68a0727c40039a13a111d8f0b0eebb2007020067f7e14809ecb2837e0852339e'
        'f1df8831b8d29acb53e4c3805dc001b0984003006f012836ad4b2013841ccd53'
        '9b05992f112a1562f13831b70e44d36000282334dfd691cd56a0f137b8a06feb'
        'bb00e45c9cfe1ae8f8f440b9cefe59019f7d7697c7ba51ef9bcac0003315038a'
        '9723776e6f0f0000000000000000000000000000000000000000000000000000'
        '000000e05dc001b09840030079013436ad4c3022043695052b2e83f3f217f030'
        '006c8e932640fd3fbb080cc40a0d0b90c3638358748656c0d146894f1d1fcb92'
        '59999431c9f79800000000000000000000000000000000000000000000000000'
        '0000000000000000000000000000000000000000000000000000000000000000'
        '0000000000705dc001b09840030079013036ad70b00b042acd732679f1032549'
        '49424c312812c93433e9c8c39646440cd0488c250e04cc722586464c8c053271'
        '0966b764786c99601f6ef4a83e91d9bc595dd4e4f838a4654e7d965e53452d96'
        '4c768a464cd829550e8b24a453e365903451d12750b3416f0000000000000000'
        '00000000000000e05dc001b0984003007c012e36ad948628515055b32280a850'
        '24b010a72f27d097a410ab2899a9928308856793896492e610b422736956d06b'
        '0824ea5221a08a43379d218739353b0617987d4f6b7bcd1fefae38fb248d3ce3'
        '1876896194275518ccd42478d2ac691f63de18bfc8255f5cdf478db8cbbda5cb'
        '5d5be91f45986c5ae895d949385dc001b09840030076013436ac346b181c821b'
        '60a6afc41ca394ae4c5f1b314cbe3532f6a732c1804b31a5218202414d9779f0'
        '0000000000000000000000000000000000000000000000000000000000000000'
        '0000000000000000000000000000000000000000000000000000000000000000'
        '0000000000000000000000075dc001b09840030079012a36ad6a481a91860240'
        '80a020e5718000bdc2d52c50203abc4dc5d2979ad226048329057f3fb49d6eae'
        '470f1788a005e1dbe7900443386401786d88d45d2d8bdc719f56200cfef55203'
        '7ecb26592a6291ad3dc9dfb420299ca700523d90bb7848000000000000000000'
        '00000000000000000000000000075dc001b09840030092012036ad82b81a0c02'
        'c107000affc028e1fe3dfefebaefd8127108612d934626cec090c1d4264b64ac'
        '42218aaa4e2628953884309149c7ac498a2118b95e21315623cd7644b8261486'
        '47064ddbc27cdfa293e3385212302465809c8664c81ffbf2ec1d575a3598d978'
        '345645eafa08df52ce05bad2ef7b590ce229d42c8c8e8ce1f50f441c0622f51a'
        'e737a25ba95eab1f805dc001b09840030060012c36ac307b230807013200d461'
        '16659b97ceb400a1cbb6167512174645b64e66305c281d4f6279be0baf214a0f'
        'b488bd565bbf8d4ef417fcb62de3fced2fd75ffeff17bd3cff3ea9ce000bed9c'
        'e33bc00000000000000000000000000000385dc001b09840030079012836a536'
        '35250c02c201c05460350831bd32db675c7345caab8c907e53a0adff0846efb7'
        'e23706cea0ae79010e3b4259a99b1332f46b99c60012814267e3273430c0081c'
        '4a23effa81373560f82a652407800e3107799f871f6f84263bba7c7c212441bc'
        '10000000000000000000000000000000000003805dc001b09840030079012436'
        'a8ca3b201282a401b9006a106000199722a07be9842657962d76531a80c5fa04'
        'f82a307e0843b9c2956855ab9b7182800febb1ab1dc4e7a388f772463d52dfba'
        'e7dd69f5b9d40029a549e21b356783a1170be9f6f4d806f0f000000000000000'
        '0000000000000000000000000000000000000000000e5dc001b0984003007901'
        '2636ad8658118c06e408000026cb8cb1a15452150792502556499457800caa90'
        'd1b30dbecb37aa709263c1d17cd68fbf043f19b2e62640cca7cfa647fecdfe9a'
        '77f71dbed4c000d84ebc22b5f81a346f18000000000000000000000000000000'
        '0000000000000000000000000000000000000000000000e05dc001b098400300'
        '9c012236adac200d8a08c3a0b0a02c200c0418ae1cdaab8f6ef28bdd5c338c4a'
        '90394601fbfee700311ea279002d6ca4282e8412c5f96c8fd64e332ee61d91cb'
        '3a558155926efa21253b14cb19343bcb7252f098dc673ad7f5c402a57ef7c669'
        'b1f01bc13aab517dd51a011ac61ab797cc30e898b85cd7752054e5308e913ebc'
        '3abb49e15ac2fbc48a18437213daac1b6634412d2232b03c60ad4673b85dc001'
        'b09840030073012436adc02b1391052501a841870a00a6b65d16a8032f2d0f07'
        'e90002d2f264d400dbe4f30e36d08448bd0c8f62c6cab310361c7162c76485b8'
        '880f160867bdf1043b75d3c3e24b57b1c135c6dadfecbe6e9d5ac50f0806beb7'
        '2be75c7a00257c6c22fa6c33976f8f0b0b5ebfbf47cf52858e5dc001b0984003'
        '0084012036adc6281c9d84a201284184dcd36d71baca4bca974ad5083521401a'
        '2d64e9a631761bfc00c683c8e1b5d4c2527d4e47ac714965ce1bf20a4159e564'
        '57978365edc9af855ebfdcf3525ae5d565151e9326ff705efe66fd66371c9df0'
        'ad4d317856bf17dfb85bf191b2744289450784e218119fdf7d328576178b866f'
        'afc3c3bc950e5dc001b0984003006c012236adae3a129405014130406610637d'
        '6da550baa5cc255d20434d03603be472cb7be986d000b472ace615262b2b71f2'
        '716abeca506898cc1c5bf16c5e9843b90c7ea31acc7bbf2e01fc7817fc137c39'
        'e94c051b3d1042774ea7d28faee4ed0d84b7349510741fa1f4020e5dc001b098'
        '40030079013036adb0641c082a8a95139b57aaf61f27de8124288c2113a41213'
        '2013831c81a4dd723069045d2494b6d8b32c796426309c18440f2898b264f658'
        '9205af40979cf0697434aa903f9ff10daf4b53b2146af90da1088228b5f69579'
        '1672ae6768747aceef61abb206940c88938932e46195463458f31b421e5dc001'
        'b0984003005e0138369cf630f91b843af03fb3aa98cec32e58f7e4a4719c809d'
        '353195bcc0000000000000000000000000000000000000000000000000000000'
        '0000000000000000000000000000000000000000000000000000000000000000'
        '000003805dc001b09840030079012436ad8648198d86026080a02021082c0000'
        'ca342723ff51a803868fa41ca62928eac8a2fcc840d1c8720f0492e2f3a8211d'
        '3ef2fe14503e37e865ed420eb97d0465d1e2bcb47d6a7d9a82ef3e33856deac1'
        '226c5ebb2892a3a901479384011822148dc85d74c37808000000000000000000'
        '0000000000075dc001b09840030079012436ad4a30230c02c401a0402e206b70'
        '605844a8a1daae93e99dc8eac4400cb113e10acec725a4e1243227d0851e8a8c'
        '6b8d4fb4e49b540c24d02555a420bbb95e04fe3b58877104cefd5d33b2cac550'
        '35bd9c9b6625301bc40000000000000000000000000000000000000000000000'
        '00000000000000385dc001b09840030079012836a8f636181184636180d42017'
        '08395094002202d2c074d510b3d94447ec9b940bae4375b49e2bab11c6c5dba2'
        '14b7cf283488dedaafbed9984fbef54361289958578986d4a6bae3c22f997165'
        '2a074f646f150000000000000000000000000000000000000000000000000000'
        '000000000000000000e05dc001b09840030079012836ad4ab4200dc60e155ae4'
        'c9c508128882451401d0e6d00735ba7b65a200fc891cae06c7c3d266e6bb66aa'
        '38312eb00e73dae3e3713d139c8069c9555bc54dd086633779d76d95e0cc0011'
        '7345a3da378b8000000000000000000000000000000000000000000000000000'
        '0000000000000000000000705dc001b09840030079012a36ad66561810c20630'
        '839515ad9e3868cd082fcbcb4ef2dc7c886a16bcf8ccdc65707202e32498b4c0'
        '6b23cc481349c29912d89888feb3c9923fcb6f2b000000000000000000000000'
        '0000000000000000000000000000000000000000000000000000000000000000'
        '00000000000000000000000000e05dc001b09840030079012036ad6c300b2548'
        '0291188164adf540029604a60d60900241e0d20a534dcfd3e84ea680cbd0c974'
        'f0589cd658643daa4ab9e18fbc20e37aca21c9ec5fd3cbd32b38eda68725a341'
        '975ec9ea34a29fe255096dce63a8082fd79aa0088e3b9c6fd31965bb32b6fb56'
        '468e98217003dd0338000000000000075dc001b09840030079012036ad4c2022'
        '9032ce39718ae6bcd1039d545656166a3c5805cde75210f1122ac191cfeb08e6'
        '69eec066d068d077076d33f0cab79a0000000000000000000000000000000000'
        '0000000000000000000000000000000000000000000000000000000000000000'
        '0000000000000000000000000000000000705dc001b09840030079010236ac21'
        'f684ac156debe000000000000000000000000000000000000000000000000000'
        '0000000000000000000000000000000000000000000000000000000000000000'
        '0000000000000000000000000000000000000000000000000000000000000000'
        '000000000000000000000000000000000000001c5dc001b0984003009000fa36'
        'ac2034516104620193711460000102012b0a6ba4b8be5469f0b5a3b8b98e8410'
        '6533acb1a5098201a8e2c0b695b4d68093d01094a3f896d9c8d98b113131e476'
        '1edac6093811f33165cc131a6008ffbd5f92775c71085f8eb261ec07a3b653a6'
        '53e56736a92532d0403a9e578c90b1ef8725f793d13f44ade445452609a4b27b'
        'ae4b4633e1cd6d4c12453517385dc001b09840030093010036a4ec4438bc88a1'
        '2120442030bd1902b294500424080fcaa405c4633bc51682adb519b180f53c8e'
        '339dc4d6c512e51bd3918b9ce57549f5a7152f58a0b874ad2fab4b433c944f8c'
        'f56cfaaf1fc52335b2da85cee6bec35346707e6e1e8f4ec42ecb611dc3f5df1a'
        '8b4a95a4d29c8968a0581d49c92bba7abdffce26a5fd54dce625c5e6af4482ba'
        '4395f5e5576449a6e05dc001b0984003008800f836a02892989c8a2101865a94'
        'cd185000a042506e97f6af000a568249e83aa8137ebc3f4b9ff624e4f14f386e'
        '4cd2a1e279b27ce7f25834d7f042501494239f7491f3cdbe3ee56d1ecca4b5ef'
        '57a749367d289780e6bdefd5056a9f9b1af6fed9622a58e873600b4ce2a884c1'
        '3320b0fa85425dd5426be18300c342a23b75b156249063d4540e5dc001b09840'
        '03007c00fe36922491800d089060506028a0b00150a77f34876e93b6d63fcbf0'
        '767c9ad4a16a0f7e5c0fcce9c0b661ff4a98ad96b6032a8b78ebf833c5efe9bf'
        '7c5b6d659795e142f10cab1641014b4eadd0ab5187f5afdb0ff05a861f6d7fd1'
        '284e6d79717c3aa1ef84fe2537988b6711af195b436db7d6d3572cd82570075d'
        'c001b0984003007d00f436804cc238c4842201ac2b20e68b66c004a440064793'
        '2290c7b86fc665a54c6464ae57902bb688f5b3a6c0ac7354c3c1b23024d77590'
        '4756b73f8aa5e0cd161aea8f4bd0eb79cdc2028a6a958100aed3a006f435fce9'
        '4b74a29f3ac30a28b9f0d2c291b30998dba8a5e6ce3d8764d9a5a7166fa4b2aa'
        '37e75e40705dc001b0984003007b00f636926370180066407200041107f437fb'
        '7f51e6d6adbb8ef9e771c8902b3b866221758d896da25f6b59f1bd90fd3c15ad'
        'a8ae380db9217b5cf75f95d5a65847896ca5e6ac48698da1df7a8e60ac235b75'
        '352b0c928b046b564e9fda38bf19b92521e62945b6c694c686cf9cb5d39b361b'
        '51b9182f2b59ac03805dc001b0984003007500f23698486640e806276e8ca50d'
        'c0390002287046cdf4e9d737355dc5231ad70c8eee0861100a915df63f7f4d70'
        'a7e6cd9fd16a565c3d6b46b62eadecd90511aa95a1816b5ab42df6c240c4c594'
        '784b95b527628c32c52a8e285caa1514bd9860b2b7caa4bb2f14fd2a883c95a3'
        '2afc28b98420705dc001b0984003007600fa36946c9260c80625ce6ad4cad4c5'
        '60a6058111078e02dab81577213848b022ab743f6d97a39f670be82f4fac08a2'
        'a856730309262a5c6c2cb842a5c5ece0963269122d0d1519ecf9bdeaa352adb0'
        '7aae9ad535268718f973d16a83129782c8de19a728d21ad3cbbebb298756838c'
        '4956453583805dc001b0984003007b00fa369468f1c00c6b0014392800944403'
        'c741e193f6698e75dcaf587714ae1cebad29a6bbc66b8ebd7a8e0d7ee6adce13'
        '724ad46414aaf3bf802f68b22d8f441443df68c70ffce1447f48188820bb1aca'
        '496568ed8774238e8a2f4c12665a3ee9be73577639678f0bd79fe57b72bd2d5e'
        '6fd5ae928d4896382c0e5dc001b0984003006000fa3692c340191166e0600ec0'
        '00858fb5f0a39f55d0e3fa0d2ec624375b26c8ae9248196b13437a392913c26b'
        'e26e0df74462ac0804170d81420dfe1415c8eb0deb49b00982379406202005c0'
        '1df55a28ecb7c2c0151977d45d3ea58575000effffffffffffffffffffffffff'
        '2807861bc3bb9518e01d8002bf536989d810169526ebbcc131e0506265e91314'
        '3704e87ae0edc02586605c07702880f5bd5bab2c55708b4bcada1b4a5b3649f9'
        'b6c8983da74292c8fcf8a8efe0258029cfdfc099b058d1a1cdb303d3a6e1584a'
        '7c82d6ac06a7c7536c53851d0d5bf1a0cd7fa32786637c81af395236fbc4ddc7'
        '59bdfc6e59163e2a290fcf9f5fb8169e69bdefba5127b5f5c8d24827802d0409'
        '7ea51ab504dc9d3dc870e43ca47852e379de82d8f430b36bedb70baf909dbe30'
        '14f420278288c774b31ced50ece575fc4f0c8f378c071580d271f32ea0f758dd'
        '48141ae9f28631c84914402784b966b77e01884524355b8dc177a6f1f13e80a2'
        '3c2ba8946cc3e9bb5e66af287725673be53c8c2db7a8bf9a7ac3ff2beefc1b6e'
        '40dfec2e435040431e0e88e8e14361564184a886c551a1f0ba48b07a7131727f'
        'a029b60a3c4004bf8c2ce74c99fe57cfeb53173434f81a3ddebdc5ba1baf2826'
        '45e8101b54fea395b604002fb6066e1a1701629801b2c587d782eb7ff8ccc944'
        '987e08fdcbb4f0fa7c1bec1d58724e8450c483a57ffc9cee31defc31b642dca7'
        'c6295bfad457405a27a7550c11c3ac31c91e8803cf2e840ae492efaa1a38cfb5'
        '0f425a5ecee1049d1c9054b7002ab517c9dc94da443998ff4afed943791fcc6c'
        '05ac05f6cb9dacbc7586532745ef5cbad8b7f4f6b7a52cfc28b3d60a5878fdd9'
        '6aad171749db012ad5e01dae411f21d612c06505c34ac693d0c7e7e92ab0ea1f'
        '902bb3d61b643e0cde6c52cfb9ba6b8469b72342de06e10258aeafb3600b031c'
        'de6c794d9d543fe508d7ddb39d2fb55ab8f8392929ccedf65aff3a535eb77b24'
        'eeb77e3542ac6942a0fc5955c8d222c8e8af965784b4c4e72b9930d5842bac32'
        'd238f66c73a875ad3d1c29cfe67de9796fefaa9236cbef9afc0d8e2ef4d93220'
        'c03f6bf8ef9a7ce7062ea52272db13a8e599c46ae9a9931783784b6047027d7e'
        '8209446d948751b1f38ac16cb789dbd933bc6d0a337c65882ca525b1415e8171'
        '0f50a47abef204fc0acc097d3a3fdb2e2a51c45b486b7d8f466c6e8f2e4b3275'
        '184fa8a6f22aa4fe8e615f099cd6b74a8e8c026d3e6d6094b337b55e695d1c56'
        'd9cad4ea6616e31398cdf9aa8fa6eb4c2ea3c0d68b4dcc065e602e1346711017'
        'a706199b3e956e728d8f5d2c22a07f1796baa548ff6a117d396b950c37ce602e'
        'a511c57833b20029bd05f80fcc03baba0a6755ba10b60fb6f3a17ec2d4f66f15'
        'c892e38492ce64bb63f105bb99902fa36c9c7a69693ca179c8a58c9d2a028f47'
        'd60ee996718fbde7451fa693981c5eb209d5a3d89d4147480aaa612453a031a2'
        'a297ccba500a4249296d6ffc9406298ffd3e7db4d868edc0451752d48d8c2386'
        '7e02d9fe24cb949f0dd1d9dc9334b5142da3c0b8718e7098cb33fc2d7bbc3916'
        'e0629adc093150fd78baf351510f84ec7f5fee5291b5fc38315c150a3cd02da3'
        'f3ac01e5a3125ffd9f39b432c3587c8217f91cf2bd6b67c25860367564b4c004'
        'a3c6a1cafe1a20f9db948c242eb8aef639087fb35572662b91b9c9fe4faaa693'
        'fd52db8cf1fdd485d07cf70259a1ce8049d0ded0f9d06cf04f6ec429b7bc8bad'
        'e164c3ddbd3fe02f9032aa3f7534c83bd892f44fbb98d94f504d0caaac16d9ba'
        '0ca73ab4002bb7d3593336e1de0d260f28763516a0529f7331cd01e9c53f59e7'
        '85006263efcf56f005946f2a74d4c1d08027b788b70d6de58dd619439f06faa6'
        '34d70bdaffe3ca73d8ee3a980c3f7f7cfb3ed109cc580264002bb6a01d8118e3'
        '9e33bf26a2e795da2138b455a9b991e3d3cd37a4c67ca3bffd91305a7c61ab5d'
        'e21908884030b4eb34d10b71ac0b2497e1ccebdff95c1de091a865614c6764e3'
        '857bd133e1efaf0e761a372cdeb74ef67ec6988039b430b1ab7d59a1fe40abff'
        '93225e94a055c66988000bcef5df7edb44584b36bfded3f3a7f5553e6a8234ba'
        '46cae4e8c5631831ac661f8526d75861723b639fedf7e2dd2deb2907bb15f897'
        '5bfab3067d71d438a1abe55c4f2d9a3edaf306a6890a79bec030a948572f512a'
        'cc28d2d0626ebb4ff4f412052a2a000a516fa39b10d8ad1015145af766e667ef'
        '54329394de55b3876a4831a73a0b9039ea9c7ca4400c276e67bc6131f8726617'
        '0ddf99d2689b0393026d25bc5a923ca355354217dbe3d96f03d9327030a91168'
        'cd3e1e46664e8f4dd45fdc5b6ab42f9bf1ea18d60fb6f87bd8c0d11feaf03847'
        '7ad45122510290f8d02f34eaff31a9a9521d531432c251c9e1fc9d3e1e0b4fc7'
        '8bb2bef506c48c52dc91f32107aa55ff5dbdc6320d2afe8b2abeeabb907eac3c'
        'ab9163a68e62d9de3b9f6fbc3e15868f876921c2c1d11a2a557a7ec09571882c'
        '8e738ac55abb494ef700cfd06b9cc3946d6cc602d9ad1179826935c030ade400'
        '2f61c287e3b0e0fbcb5fa48823aa5d6e68a54218b2df670a5035085386058bff'
        '749170f01e685637e5cfa9f5b4309af808b35a1416c6e0dd8667fe70c56415fb'
        '925a47dea8e90b1842d6591fb87f350c48fcb610320d6ffbcf42d628a40c2687'
        '165d681f104a40199c2c64efd76c4357113c32f6de21a8ca66bd2304ac69175c'
        '8540eec4482586d3274af6fbd0cb0c858bd9efcabd0144d63c1a131edf4e99f6'
        'd3f356b8d116d7cbfab2282780491fc06a84e1c17eb1ad21a657b1a44b56dae4'
        '268f71554dfb419d2ee11e51b7260e8387e3682783c8e3a0206d382c5660627a'
        '7467f38a29016f0356eda28e49c01edf5583e7fa83b6f62e380180268f02ca73'
        '040ddd7603ad0a50cb03c7b62b614c6f967613aa0fca77b944c5edbd01045bd1'
        '78ec2781db1365af004dc0f2d003308a33a87b71d08b6bd5c0f6ba4167caeeaa'
        'e52223421dc3a063de64258059b0f35904f1cad2c6122bebfe095960fabbb75e'
        '19dbf5769cf4f6e9c0be75cc6a972c4026871b844dd958110a60ca40fcf9020a'
        'fac830990f4a074ac56b2f12e5431cd7ed2471dbe40e32268052414b79329aec'
        '633499def1623b8b52fea1c9958da069d736f8c48c0e3f5276db51899de42780'
        '5bf3fe5320b403b823e174beb08d22109211e6c53ef9324fb17cdcef456d0753'
        '4f412c82a40025869ee7bb3ae61b0945371f0872da8e0df0e0d2286e115446d1'
        '6c53a4389b4144c62a3a420b26801e461d324d4059a4944281d5c62fbffe3be9'
        '7b9cc504dbf8a192c86b0350089a8a764f8e8023865bea62a4c8119b1f654202'
        'e7fd730c4e15d04c16295defc3648335c1cacd5af54c0027801a58204b7600db'
        '9b05386053204d72ae8693a7a5fa81ec923254249206c88947e96819695f5826'
        '801e3437b1160d3584373af873693880e45585df39085d2b775c3e8c681e8a67'
        '6b21edbbae802686349a3521c42034477cf690caca19bde50ec4e3e6c3d38047'
        '9df3ea7a9970094ef369c304482586429856789dd83a663e40f530c32cc061c4'
        '7daa10146420a6a59f19dd75565003c206d21e2686347ffcbff2f0620724f4f7'
        '9d58062ea07f3f339145612788a2b47cf38200d14dc4172f7de023801dc56fa9'
        '6dbb0d709859d0fda4211dea061b803e7f221cce66ab5a895b460b9658e42786'
        '39c04ff312c431b134644e6d2216dc43b25a560ad9060f5955266ee23aa3cadd'
        'cbf06a1ac31126804c59ccdf1a5d64470b3b4ff482c479c7c2e8ed6ba05cdfaa'
        '626867901cd0b9c1fe1fb98dde278651400fd7eae2a3b180b0cfdbf226a8bc7f'
        'c89b261fc4261abc08a07dc9ec79e985f0ee97ad00228634976e6bf3da243675'
        '41bad84a43aa4c235e05733f9c22ed707084f074077f9a6e2480117dea16ee2c'
        'd450186529868f5f4736375940f5b431d4071bd334ce09fb4c1d3bd900098612'
        'a551bd6e54b20007860691cab8658006800221722d4e06800221722d4e068002'
        '21722d4e06800221722d4effffffffff2807861bc3bb9518e01c8002cad8238f'
        '1199d2ac4acfa3e5535a2d54206e15fac05c89bd3f002086599dbed9f1e68b66'
        '7d24203f3a367e3e2217be743e908b3a4dfebd4a736318258012d5d370ebeafc'
        '9f7feb73e3921abfe19ac37665762b3283d4f3164487254ea7d991d680218678'
        '8fb357fbb7ec3faca07635253771837649aae768e7f4104df7c19a1a71c6f822'
        '865ae5e6eefc7d3619fb085a507a9750b6134d6b9679ba01a7c2d7fa8078e4e8'
        '0c8c20865b0a0ccea3b3bd538958c4a6cfff129f44476eb0614be2dfd18b64a2'
        '36b7a023801dcf890adfa2d99016f7e42322d9fddce52d8ffa697db24e9efe56'
        '9cc5242bafa0c821866214d7a9e1a4a9f48e263e374420079f08e62af94a4953'
        '728e981b8ab940be9836a1ac15e83789ee5e2bf22e32a9871ac6b4c911170157'
        '914fdeaa1681338714bfeced99ec80324be69a4dcb0e8b7eacf79f926f1999c0'
        '29a3e0363b6c6929b900166ca196c25d87369140f6219e3d0e42a05949bce5b6'
        'a5ec316b9f794d08e6882cb2c4884ac55de35f981edcf8eed6daea3b6f94cf6c'
        'c7cf6af5bc4256347e9f00f0d3f362be525312303c93402ab220f829e659a274'
        'fd3b1e84d5def185635c6cc2d3cdfab4a5065c4c9c1ea4d6a949f61045767b95'
        'e3102eb3d60eee4a29794721aab9538aff682601d73e3aaea5aaaa19db1bf0c8'
        '4f50970211c4d5581f1d8a2dd9b09800802bb51771a66216abc0b9c92f09410f'
        'c3701f4ba8a9a9b1ecb973d43deb1f9505fbe5f345d2fa52b6c7d4fd8028b502'
        'a41217d5c4f839f8ab075565a6651042f559e8e67afb28480d81f5b4ca25574a'
        '7208f09724132bb33c72a9ccd8758c3cd024b9863974c8c46f5b413ed2fb868b'
        '3a2ea8ed0c47df1a5905c24a50162a030c8029b0694d6d65a759964749916258'
        'a588604c80799ceaf0b246ddce871a2bf0cc5080cb36ff115e2828602db041e1'
        '4bd430382b8fecce670d7c3082560318269eefd239a93ae5dc4f751c29e5ca44'
        '18c2c72cae0b83bc5f002eb3e292b282d7406dc5d016cbdad2d04d816e85e8e6'
        '68a7acf45f4a1c646ee1583e668891f992ffee9a1025678b7031b1bd6ef7f17f'
        'aec3f62acc4185765c5757f76d6ab6468349a2715fcd2bc6425378a50fbf0165'
        '1023404d752d02bfb2b9202db297fd84ff095567049fe0491bfdc45007a329fa'
        'fe707f3f585023222fbe99326c3888b9f535176cf31448e27839b25d8e246521'
        'b8d957e51caa644da7769fc8759fd56a90a26f9e8e10736466a34dd567ed5d81'
        '812f0ae5596767da7e594595d698baba3b8b0031b69eb8c395d7803cb427e3c1'
        'b1a64645b8cbd5346b6383f2218054e503eae5fd95ad1cb20a039091981b8c03'
        'afb3632b202ab6c1d713b50043afbdbc9283357e4383efe23cf0afe880c521b0'
        '246c46a48529113d4035fc93d831e6102bb5ba2ffa11c6b563d4df16f952eae9'
        'cde0b641553c0c7523c1328f7b1fad69ca0e2127169f370af40ea4002db64d0c'
        'eed730fc98548f6289c77394adb830197b41345f9f6e4e05ae9d676c5b5dc9c8'
        '7d63353ad2fe6725e40e30b76ecf11f661196c5be7cbd5a93f56168b6dc04d1c'
        'e336cc803e0f20db0fa4f6d139314c3fe274bbdb046b26f83eef802ba8cbc0e2'
        'affae4264253625d9901e7a66c004afe2253acb8fe70322ecf1aa74b51f9c4c6'
        '3c281eb0ea572021814e2d01231ded86bf05a615a0a29648af2f25b2836acaff'
        'a1618d7e6d10b567e02481da0039b0189f02e432976bd9847bcb7951684712fc'
        'e4db43fd1d6a20ab9f914904b42023806f87315c1539fb70b4b3de2223507352'
        '2ec65e3da10364889ba058bd8550748d039c24804769c03b7cc84fbc1cff6389'
        'f21b7d093f5f45d4e4fa6b054dac0deff0bc0791bae7aa32aaab6b8ed8011dba'
        'c936d6c584ca1c274acb0d245684f24a9d4158f731f8391774564fbb2b3fbaa8'
        '2b63f7f59e6aef7dad6c2ea86ef712a3c25626f7981c88d0c0447df6a8041a94'
        'a738f6caf3fcb2aa8dc2c332eb3e81bd6ddb412a9f1634716027abd4fc0b1e24'
        'f6a0138e73536390f4be8abd718a06094cf80690a79d23ee8aee9c7769923d67'
        'e02db6f46a33e00dded2be14f8cc3c3fc733e7a276ee3973cf9973d0b8940f59'
        '1731fed86e49d481abd7a2d607c63424b6da489702a2c51d946a276560b4ab91'
        '30e5058dcc1b0ec9cd8deb344018e3ca30377c8025b6bc3440589be97946a7dc'
        '974851ff35bd23bdd1559c6c114a3329e342639de5f2fc037b902cb6ec1ad9b8'
        'fb0278917ca9faccdb438a4ab5a27c2e0723b43472b1d7b0320e508620084f10'
        'bc207c4cd43ab426b604dadfe4289d13cf008d4647b963b4df6e0dce7b407294'
        '3f094fae15859137f0f2fcb2588026b5d957a564af52145ae9e6336ca0e88195'
        'c6b33be333bc75e82454724e07a4e318daf4a36de02ab32e2bec72e5421a3730'
        'c921b9601f2509d367d4991c02cd5b5282db347fff602455f3368d834e7a6612'
        '28ae60b79fe1efd718de0319363099ccbd870862f9caed0f44533782a7c2aada'
        '73ad9cbdf6f8de0a6826acca9f647fd4130cb789917d269aa97a0d1161fbbcd5'
        '8b9e8e44115145cf996939c8f8680b7426ab163c6d94c849830aaca128ef6992'
        'b57577388525a6581f381003df29212dd6fd85acfe4c0e26a93e0226616825af'
        'f839b03f92740fbe18d445c33d6a730d207a4ddcc4e68451f00bbb1377202aa6'
        '59b3594078c081da9f841b5713e980ceb67a0e7adaf0d51e13411792716198bb'
        '0bf83e2e4ced4216cc2ea7f83389993201a4c9ead83887f76ee8af6c22968c78'
        'e54a529808832f43f5805560767a5be047190946c41435102dabd4b13cc186a5'
        '8483cf160a4bab8b9604786969575f40f0b9118965654c5998c4adc2872faf51'
        '208092c5e1e82eb1a3a731b410bfc8ad90dcb89bdf014504bd47b1184942faef'
        'a0a7c1aa3208cad10db36da37b16fb0559de0d43e02da5d0a183e4f25f66830d'
        '5bca85d18a0a749adb5f8677ffe19072a5ab4f7be500bfe2fcdecd8de781cf91'
        '4639302c9a0fba2a7c4a0baa10605920dae6e3b21d93778e2a86fe6921cf7a33'
        '647fd1684ebddc0b37b0abaccfffe55924808eb1d525932679a79c624a28bd28'
        '95256aa33b420d52ada8de0773582dcac1b02105282283aba6947f1b3690a17c'
        '7e23ca96df3cf3254814f048c284c58476dcb3ea0e793918238207002f39e914'
        'a9250c8cc88ff002dc79d5f5c1792dd917919545c4eefe40bd64d7002380591b'
        '1caac0dc72a107b343cd4007812cf7bc0bc210f7c7617b91c7578c29864cf028'
        '238028f1bc61185ce8e6011eb3e5e1031995adcf9fd25b008812d9ebcd44c824'
        '786ca0e021865bea8daa080fb50f83febb1796922625e73df69b62bd4f927581'
        '2aa41ba5499024801f5df1ed2193a2d0c79e43987b3bf89447ad44cf15b390ed'
        'f289cb197a0df51567b72823867891b8a58c20e2ce3e21236ef112407a8aede5'
        '3b010a66160922f0c00da8f810e940248633cbfe4b9aefbc7a5347aa89c4e547'
        'f28928f1dc1a290aa817a92fbf2bf43615b97b30248642d390953e9e12f6a697'
        '2285dfa4d5995bb68998135f6aa0e7c6b0deff42df6bca540022801dc575fe9b'
        'a9b210ea2f398ca57ec2a110626323235ca32418331e7b8553d437a1248633c4'
        '34a19efff45a18cc81461d45d004aa0e5eac0a649cd320f7d6d46bb02f469b00'
        'd12186347b69aab1638e69b444a320bb41246bccf5cb77ebffef489f8324f9bb'
        'b49a6a1f865bf138e5da7850ae20f9b030f514ac79db5f035bc0eccae2d9b50b'
        'fc836821865b13012aedeeb43bd17d341accc4063aa03d93b706ebbacac3660e'
        'c6ee95a8f02286360841eaf028793bd993b5e92fdb36830d860465666d4cd36e'
        '898a16b30a6766b02280117452edfb12a13898a5dba34a35fb9b883d813f0176'
        '9b4247e15eb0e965d55c8624864276bf7c54e257cb7f2dccd6f1eadb54b1f88a'
        '1d6b328b69fca3faa1b4e5bac1695e7c23801e46239d574b300b8f6aeebf85ed'
        'f478da3cac029bf718a9c7625827f00378fb371923865bcb7f0f52f828ce1371'
        'ac1644b07f7c7a06ef2b4c81ec59fae28a2f82c0bb233310248659d8369b05e1'
        '49ae534cf5502d011819313e4ff58edde832b6c931406dc4492f9a288014800e'
        'a5d6c8fed0f4a1aa864a1971ddb53f35d980ffffffffffffffffffffffffffff'
        '2808861bc3bb951954801f8002ce11ee98467a04ec3e30a4afa8586e5ea40129'
        '9ef0fd6c0a4ee4a78be625802b4e9464467c986374915433361858db27300417'
        'a727ff1260fd192758afaeeae687920027867e435f5f6dc23703e21847897118'
        '8b1c8c40736121c485629340c48d4f6bdde03603ecbece8023865d2bcb4004cb'
        '042a5a26f15b8a783ee85f1c9d6ebfd8c9528853a4617ecbc63e850827865bdb'
        '2b929ae78ca3d19d4542108d34ab7f99389a59fab7e248cf88cb0aeebb1e3411'
        '2ed54c0425801a6a0d645b49d941b44f083ccfbb92b5409a65a568703c347316'
        '314927a0b2a7ed13e618268020db9fbab0c5baf93f4bb041694fbbf47d09d12f'
        'ab17bb227bcc627c631bf882b1c3388e00288310b8a42ac2d2cba0c5dae832c4'
        '9dbb70cc4b4f2827a8f1580d2ea7476f753647b316781036fde026955368cbf5'
        'fab71a92d97a3e6e73df816112b66ebf76abe33a7b854d1d9400d47e12f985b7'
        '902795799acd4193f30cad66850b9cfa53b49996a1797ad07d06523fb396ba51'
        '06234466aba68649a627856fd0994e9e9016437ea537edd6925e5fea0d98a2a9'
        '8d6f954fd95bd282c1a4db094a200004d835bb461c1d080f8a1798e27e3cc2fd'
        '45acb5e5eacd3b2982f0a73248341b041ceadd3da143cfe6bb9ba409764a8962'
        '716baf6a3018f031bb755dad87f7dfe786fc4fda81f6ce43374315deebef4976'
        '8aac53c6357d0988b795ce9c0499200dce8e7d1a7c727c3b002fba6ce23b6092'
        '4967ed867deb4e5b2910f62bb7e4f813bfdd5d59669160859c3a468192e8ba4b'
        '48adcca7cfdca541c430b98840cc604a65ff6dc9905b50bd8e4a6b6b8b5dff0e'
        'd360604876e5531380be79ca8fc22bb634b50abacbc632cb5bcc2cba83e363e5'
        '922a3cc2d6d200ca69ba21650bdd7d2f8033268ff87b05673145ef479e90038b'
        '97f9e0725d45e02db9a5e17706bbe282356f66b5ce805cf4b2f0a84e9b6dc749'
        '2faad4c9986287c14a8e70da08ed8f7feec23ab9f02bb9555cd7d4008220749e'
        'f637b04df57c557e6133609c517b3b993ee468af8e7e3da6c08d1e1f658f51a4'
        'a430b77fcfa0b1da7e4e1e7b5a5a0f754a23f71617c30cc4e41aee14c8caa8c3'
        'f6f56ab8a35b68bffe1bd910e4e3845c6f7e2fb5acbf4330db0e609b71b8edbb'
        'caa8ddc1f577e88915baaf8fd862c4e71e1fed0bb64c3bf6e4f11821773f8aad'
        '090031affb6ff42c7fc052df56d496fcb720f2e40259383d64de8ec9f4afd6e6'
        '02bc894745cb4eb9393dde3a20e11090e371696032a9813aedb8b5578fb7f4c3'
        'dc5bb7e46b3991238d729509ba9bdc1dec9c7fd208062ad8f9e0565ab481d710'
        '823bc14d2858c032a9dfdd2ca0873940326e5531a5f123b51da105943288f5ab'
        '3f3a675601c9b9effd37ff115b8e84549c7d86e132aa7630160031b45400277d'
        '95746ee522fccf9d7092897d1db77c156bea69be2b7bcd7e0bbb35a4dc2a0f26'
        '0b1a54e75d711699be61824430ba8ad31a56a6d42f255c81b40032f06e4c863d'
        'ac05720872e18e765de84a1f3120abd619b7d5976776007205c28b9d8030b94f'
        'dbf8deeacc389c988ec2deb3b5642d3982fc0bfb78ba163b10545d7359f62308'
        '64cc4526b8b716c8bf5997e1f50030b7a1306160e980e16960ced483372c84a5'
        'bedf79b011041bc7c72f2440f43e977fb17170af4013ad41f40f3edacfb9ec31'
        'b651a81490e3f26e821972261feba0c919b6e8fa39f29156dd1ecbe04998264f'
        'b42e8c469e9aaa46f1bc19ebb01c7b988030b3146eee46546e285ecf3056ba26'
        '51f0ad3c754a465010c96ee921f9627cc353a25059731eab27ac5d63ebd2017a'
        '5dd02fb05e4b09b08d83c22076e452b7e63e6881462f52a2d8d469704bd95d8f'
        '8fd2f39f5396be3d9375d879590d3168d03d37ac3c13eab541ab96aea55d8350'
        'f0d8ba43e4fb4a889a885d0a4963cd0a9e0df899510c0363b15d7a68439b382d'
        'a62a3e862d40ca776ff832a60a25aa9188a96773f7375c08a7090acaf02c476d'
        'fbf02ad70cc188e614c8fd99cadedb27c83c087d38a51be7d7f110d9c030a510'
        '54b719eff385c77b4b942459d9c791872510b276022af952641760200b443023'
        '4d22fcebeccea150d3601c1c8fa032a4f516007d24bc7dac5804fdd347713068'
        '034f17e12e1460469760c7edea65783cc1b95f81d1afefc466179d887d2f7756'
        '0032a24d3381bf753d84a35be6cf361b5d2a4fa4f1db4cce6d03678b56f4d237'
        '42d0dd5a7333b358ac6bb70fad32a4fc1efc2a40319e43da584eb10d2692622b'
        '8c947ffa1bbf697b990f2a705427d869bc67012703313d74c8d1eb4f500bc867'
        'cd0b918d4c0026811c6f11061f2ff313772c864b0f32fc84bb3ddcb068d25530'
        '0ea7022d3d4083693a24bffae6268769c620de58bf261f2eacd245439af274d4'
        'ff47fd3c14b588405e4e6bb0cab5e53dd39de50028869fa51130b694e7211742'
        '138d18b03e40b1048c62c157534066cc4f31e6ced9ef0068e4c698248028801f'
        '5dfc6f337c72d54e53fc234bb4830097612b78e5f0e3f4fbaf5c3b05ee858dae'
        'a42a3f9ac58027801ac1085e1f71e37124e5160305cd95eaa17d4d066486bdd2'
        '30ed00b80a84bde07861b05f8f00278011c2548b8f98fa655ec26d12366973ff'
        '0baf28feeff34cfc691156dd3779bc56be4758085fa025800b2663ac221dc548'
        '49f58fcda59315443f771f0aa1c2ff647c2467a0e26ecfe3b4ce752026862261'
        '317800a130b06132a0ed908c42e2b95cedbd10f587b0dabef7bd7182e9c21bc8'
        '2e62381e800ae130ef880a9a93a00667924d25a7f7ac7a6f86d43e0988a1bbde'
        'ded608860690a1708244a007860691cab8658006800221722d4e06800221722d'
        '4e06800221722d4e06800221722d4eff280780090135eaf7281b8002c6241cce'
        '6de56cd302ef0b280285ffcf22fc9662621977fd8023865d552f9254c46a9b25'
        '9645f194415a8e46eb8565b5e212f5b03ba6ce38081435d800228026d001fd31'
        'c0e3dfcbd5477fcff39d32cae9970f795f9bae00d87570154243def22480248c'
        'a5e8b10fa6bf112b1e026d13fedb0c0687619df02b75a99cb1bc3903d24b42f8'
        'f433bc07ab24674e26f1b99b9dc66444b4cb2c854d21248b8b44dd84816b9a8c'
        '21adba9cdce2d4f1a4eaf0122517499d6e2d96a99033bc082578694537fba502'
        '12cb0eb71367f463ccb83825a0ac550353ed3a72182a8d77c126a315f0cd5940'
        '2f9a1a74c916dc1fe029ba52398d124793c845c16669af99301e4fa1dce25d97'
        'b505b5543018ce23f9a5009f6947105be41aa02cb864af80e2fe85bb1ea79d63'
        '142569ff9cdccfbda007c59d453353020ed2b61bd604fc297ea2f58d1c501ed0'
        '2bb7d52705ec5d8b6af05ee5e370d1809c8f14af41b4cbc8375489554c4c00c8'
        '02072767e4fe6279cc8d56e02bb5ba2576af98320e0a9e92376dec324d148dbf'
        'd49838ebe99a11b8aa565ae505b35bba5503b07370387d0c2cb522f85dc97520'
        '2d883cbc159c282722f72b1252c2be85a3ba2e05e51be434415dfc06c8b0d8d3'
        '7d7fb007602bb528dbf7078ac502186222bbbc09a0e0bcf8e2e894553e498773'
        '2c0c4b15215126360b9c8708f280d9b2782db730b573da6bdf9ea49c5100cbef'
        '75f80d486121a9bdead25f3f124e6bec099a5173d9144920c9783d7bafd4202c'
        'b794aac168f139e1c9fea13c369e34a39cfd64e7afed91ce02d5c5ced5aeb4fe'
        'b3dc005fcc8209d8e1022a302fbe66c2bb219c1a91dd488da3b89bb3c6676b64'
        '2126bb804fbdd9c7552b2fa40b233821a5f26d2ca9f8bc025e66bc6c1e831680'
        '26f36e39919a347e2839bb7f232896b0200bff9ad813557a0c9bc0248139962d'
        '2aa21e8fc44c15def21dc96f8978a8d01dbfef152450be6e55d4089bde915c00'
        '32ba32f85601513af8a192a82148f11ec23eac463d338c3aa13686319b311ae0'
        'd750f7175b95d4f271df2b81024237aac324802cb9068f04e38a91e94a86b05e'
        '44b2874f44db4905e078fd79d921f5c2d7812338d20bb00c64a8739d5ad3bd84'
        '2db6d1a811f70f8a24824187a8a56fffa2c58a3b29677fa3f6b1a435ab69e3f2'
        '05a1bbde190244857ce8c3adfeb22cb6ba560c23caab398c47056f8dbdafbb45'
        'cba32fd83b375766fd3aa8cbb0dd39639ffbfb822c043699eaf6002bb361b2f1'
        '56d3220ec696331fdd98756e2d7a5457808bd5cc76f0b1f24f3c34c5db6e5cd6'
        '08ae401fcf95112dacacc249f1211b6f7fccd70bcde8c18c2f3eee1aeb9a0d25'
        'fe84da62dc9d7d0fdd47cd2e5faea13377f9ebe5402da93d5f2ad1fe954f8201'
        '40d41572c9241d2a3c55252b6678c4ad45bdbd4c84413d20a8af29e35b4fbc34'
        '3820402aaa16e991bd1e7e147fc220d302e240f9233cd3f20487bd089e53a450'
        'a9da9da70cde6e38745f391e54002cad39040bb7d7ba3d32f90225ae7f7188bc'
        '3c160b3343222cc14a85ba23b9c12fe73447bc96ab3096c65c06e033ae3296eb'
        '3c0cb046c44838facd6bb50f3980f545c3b4e061e3761b78638876f1770d5d67'
        'ba589c0989c8a81ad5ed70a98c79602493a9f322f3df31493c93e3079b6ddc46'
        '5a88e7d6786642b7f4970a56c987289427c1bff822846850941d43ec6ec2ab50'
        '35b93cf6a01568834bb85be886a7ad2cf49f4821bef1202484830cde1eed9b00'
        '2ce10c5af1e5be47beb6d6a1f0663ee8dd0d745f17ecac72cf34790024846db3'
        'e2143d3b7138d59fdd80e449b29c5b2ce3efb7c2e2029a98fcdc0c74bc92eff4'
        '442393ccd5fe6195a1f37d7627292b6fb1dd69ff321b9074e5aa5844b0ba49cc'
        'fc87a9f5d82db37ad40db518bf04ac975b1b7a10937ccde2a063e44f309a658b'
        'ac3f9c241235abf8b0dc3188523c6cbd4744f828b18b2627670b0c78cc0ac9ae'
        'eb099a078b4c878d2a4cf0615ba0825fd029d271512cea4d68473a4c23ae62a2'
        'ce6df8a879d26eb77f9c17a20f2b962875220e88bb5b9002456fc78ddd2d91c0'
        '27ad16522ef5619fcd06b3371eef35ff470e228ebfbd349696ae0fe209bca995'
        '50ca0559b02ea7002db29fefc18f50b278581200f214c583eac0947c24bf7cd7'
        '9f712d3d0938b224e5d32fe3096654e5ca554d42a6b037b80d871cfd33347894'
        'fb03a0566331a2cc8234f862a573e2c0de8f205b0719238b7a402a02cdccf348'
        '4a5c08f794077bb5dd7d3d0e394025ac3143640a584685b80b6b59aa3535570a'
        '53804803c9fd2be7bea1ed2c29c66a884be892b02381dc0003a2cb5b8dd1ff88'
        'fc504c8f48e697df986e3ad46bd7ad511024aa0c381e0638248425e2e14f3735'
        '0f6829f34aaf54328643c0398b02b6405a4ef4b5d1ae3844f5c73a6884248504'
        'b6f5f83e0aa15b6191f2b318ef0f462ea8caffddeeb8417e1500ae9461f44384'
        '685c31b77a80a577136f951f610aac7359fc3966668d5b31d07ce9da074bf865'
        'e7b81d146c58921f5f31186890ec56aef7901c602db53b18e5bfbd7d80cfa616'
        '1fbe8016c5aafad4026fdb5bb7dff2be881230edc20b73ef0f084722b0ee428f'
        '8e002db503c51a60b79334c3e1fd04d44d2272fdfb4a805b1e50b78c36cb219d'
        'fde5862949313a3fae026143236a9c002cb3d41a5031505b26b6e90fce4e2f67'
        '7fd96dfb44c3ce27dc42b2fff55f9adc76f0d713e1d7b3c492ff51dcbb29b395'
        '4e2d9611fe06d8dd109a2c57a145b1bd6bad1a25b59a724f6ddbf9a88fdcd0c3'
        'd6cde2fae23ace29b06e86ce1139f4f5ef26eccfbb4f6c893b633dcad2f07bb4'
        'b53d210bd5705f7360a4ac04daf2808e282cb05007e67f528d3ea9ee63995663'
        'a07ed7c11574cd16b50a4924652c11d3790720d525e67c13487d88737d802db0'
        '9d0b7788e39244fae8c84c787de379922161acd7792372ef1a18b831c3169f2f'
        'd9599f23e5be3a17cf613d9c2cb295598a7f4e896be7705e783dc4d332c68f13'
        '2beacb33137e3e1c7a439122dc2f7a40519e58a635f9c62bfc2eb3cc3bc2e4fd'
        '02923f7160d316c9c45620a2fbacd478bd2740d2ebc8ed7636f76385a5491dae'
        '13dd0b66103c97c02db3cc32db34fc2a41fdcaf68134aa30c3d092190436b44d'
        'c1f15ad1da1ff2e154367102860b0df1e31fdd9677802db6f68501a8f6a53bb1'
        'd7dbc1039137098cdbf645fed1bf0ef911fed91ee5a45341a60ac400a7f65d7b'
        '5dffb1802ab77b8aa2917a8128237e372f78c6e2266c79aa5a60e2144c6a9e67'
        'd2694eaa9667e9eac3973a72ec0c602db4b9de846e3335f79de607b6000a0c9f'
        'cdfaafd47517dd8c207020bf245412cabb9f7a09b6b2309599f26c81b82da945'
        'f204c953e7e9d9a09251a912f3ec549af9caf6b4a1aa517aca75bdaa84fe8aef'
        'b337be21a7d154400862002aa7c3a7dc294d54a88fac0621abb74359a7524afb'
        'cd13497e48088bd7d4b544c8388dbfbb8f3815c70a802ba5f8c2390bccbe1d1e'
        'd9db29479c48e86856d93414fba703346cd4c104f5b9ccff6fd9939b997160be'
        '1e182da38c7f808695cd3b3bcb1b3951342dad8c0c5614f9e6abbd9373547b72'
        '2020544dd3c53fde39bdc27f742b30002481de6d9231571cced2da7dce70ac35'
        '2c8b3802d29c74276e5ee9a8c3d9096f93bebdb70023896cbf36a79e3f966cd0'
        'ce662000e4f4716c9b4237ae73bc7a7dece7b2cc657f095f102587423cb64dff'
        '2e814081fb4e9450c30f6e5e411ae1df8215b60951f2e769700458dc3b8d0022'
        '80406d3159a267ebe046d27edd769025acb9df5c5d94251326a8f74fb1a19cb3'
        'a3c00a860e29ac0bb4ba84abec07860691cab8658006800221722d4e06800221'
        '722d4e06800221722d4effffffffffff2807861bc3bb9518e01c8002c6202cad'
        '9190b3599bc089ae928bc3ffd1dad449557e3488c18026801608e4a1a94b2a1c'
        '0bdadc4dccd519b13f606166f9f821dc6f45bcb46570511dc826aae80825828a'
        '1cc4bb21b91c3005d975321c539665db757025ee71ecd0b4a5e95a4aa4959236'
        '3c4b5032bb463e56529784819feb632d0421bdb208beb4b07e80711a442768ec'
        '7a9a730b0da3f0b54e3faa319f3113958d2fdbfc380030baa7008b77547e2cd2'
        '7ff65862f90f45aaea167f555d255b6f991ceaf42a40217e913dd75956bcebf1'
        'dd8073da95e9202db8c02fa4d7e5ec1de6c02af3ca95477184a9740a7a4b228a'
        'fc444280e1022993fd6da5b530149f6fe69a862b102cb859097225aa85997d2b'
        '5735258faaf2eb5ecfe48e0c1b7f7e9b0ab04368df45e76f05c8f6e941cf9d4d'
        '0cc02fb60a1abe50c63716374ff0afa7df38a96d0884848a0718a342330f8aea'
        '3bfcb6887f216c2cdbd315888ee16174374028b5a5dc9d16341c2d984c3ead7e'
        '446b29896bf8d36b758c0e7d98798fe710e2cde13510c6c130d0c029b3d63d48'
        '5bc56d66aebfaf09a5e106cea9f7fbf986dd62c9881e4de364d107662b9a9a22'
        '3e0c2758e42ab2235214e92ace55dc9fde23810c83c00420d920f2e14e6e31b8'
        '1b1c2e87a8d54a447c4b6dde903dcc802fb734d032c846564b5a889a694475ed'
        'b347bf268a6d63a10b20155150b6d341581c072e43f4a1a611d6e6a0966ce600'
        '2284c5c0184bfc816e81ac8383ad40b448c89c79e3090437ea7f04fe83e5bbfc'
        'e6dcb0268c406a626045dee7023e99f5254cf3ae57e6e805b14fed8939602f73'
        '24443fb2e939d5addcc02680fabc0a3fadac6e16a397e1602de1d3f7e9363bb9'
        'eb50d76038921ab830b515998535f16c4026847aeaf8756a9f69fb644dfea496'
        '1479ac945307c6111587501f38aa8cd17418719753e1c0f933b8262eb4648fb7'
        '27717a2190aec1b4edbff58dc10a995837a3d755ca29aab362105fa64cd4ddea'
        '37f426a70161da3bdb9406002bb5b934100179c75f32b2e141edbb3f1c862a11'
        'a32202551dd902e9214b93f17bc36b2fc03f9b19ba78e18028b4cb461db6b5ea'
        'befe840c1f51679b6a4074153307876fab4b84664214ac5381fdb0f052a91339'
        '9a2ab174ad265ef2a2af0cecbd6da434dcf9a2ddd699ae5688e23aa1cd3d9b1f'
        'd11093da7f3049dbcaac25002faca40480e5323bf0e4ba2bd8fa8273b0687f21'
        '34be4f1e142418948b520c60080feb504696cb6d80f5d0fbc05c67802ca7cb45'
        '8b511115fd9a9ed657e446a3623450dd7254993b51d2a00f85953bfa652dcd8f'
        'fdce9ccb34ce74a5002aa78d92ade66aca1bf529a32c2c01caaf3674a58f3b6b'
        'ad7950f36b1da71dd4d8cbb27c3829ba0899f5b52fa9d5f6d7969a0e9b38b289'
        'c4e37e14a37340ad4224f79ae850ff024237276f0ee6c50e3ddbe0b1ccf9146a'
        'c3ed8b3037af33c4e9ccd4365f7b384fc9791dcb21bd29872584ebb5b7ee9bf6'
        'ab8c40d20807900a466ec4046ded27e334b8f42ac710fdf932b4a74037b1ec19'
        'aa85be94bc4c691c06f0387a8a393dbf660f261fe1eed6df8c7191d8c64f0845'
        'b5c79b526be361aa928e7e6ba81eff713be099dc2684ac01f93836c307f18258'
        '5a000aeb9d5b7b2079721fea4072cfa6c9d7c9c6751b89ca696f0025949c6ecc'
        '957e906f12862b49134456f82babb527b5ac88dd0fb864b266e2a77bee963a55'
        '802584df8689de1129389a138a61b00f8bc023be1f0c411010860772b7a31e7d'
        '33452ef2f4b8003cb2a14420970facb3906436aa14f92a02d68f7ce3726d764c'
        '8ded621f9adc4e2ea98164098abaf803d264e9d297b3f97c3764a1f108aea7bb'
        '0dbdd6002eb5ba046af79f0e6952829e128ac84fed6fe43803118e2902d77cc8'
        '95b7fb6cd5de614345e2fb197f33377b0c47b02cb4d1ee1ff394a0ff0d2e8aeb'
        '1cfe264238eff881ad61db1db1de9b5ee6b3fe92eaffffcfcac8443448c8b300'
        '27b2234c971d8f28f903c8a2cc38be4e2fbf87463f5533390170e0fa1e760110'
        'ff98d5159f6298d42fb29fc87585c9d7bc670e189c096c02f7095a4812aedb56'
        'd9f9ab413dcc1f3cd8229068f750c5b93ddc21d1b9ca100036b7991001a38363'
        'c7bf26d2465a24cbfb46163e9adadcc3d6e9f7d7909d321d8e715525ffa9ed79'
        '565609552e202a54f2488a634895b434adeec4e518e12c5f3d4b24e6ebc70145'
        '7d26e018d37446575c57ede48dd4219eed5e47791a3b0895ee62e95183a07daf'
        'b69ecc96258ccb14b12e9daa44f520e9ebe1ff5ad91e98aafdc01d92a19d617b'
        '442abfd002587b8057802680aa4b9fae6032be2c8b01588e52b238e898aa2050'
        'd3261dcb7e352132b4088dc55874e553a626822f670e11b5f4dd92fde13ad5f3'
        'ab6cbccbccb62cddc62301e5123ff85133032d01923c340036b0a1f58d88feb3'
        '4e453d33fb7d087422e49db3dd95a597661a92b3d12e69709b4968fd61df1ccd'
        '6472fe31b0324ad76fa3ce7edaa83c2cb4ba9eab7b5ce3867103ed026481c48f'
        'd860f64c10a5ec3c06f06cd9e940b7ac3350c929989389dc6e4dfe432fafd9b8'
        'a24ae8989987c01bae03f19819b36eb517cb40ac4984dbdb683b8b8a6c688383'
        '335d157ce1b21377fb16da002faee88778f3b457d605ea0c788d3fe8b365ed5a'
        '3d17da53f5b12f361a3a8e4a6444edbbe2e044438bbd126afe971b762ab0e3c1'
        '9e9cebe43c6ff6f5819e994810e14d4dd1a8794f8b82d618a8fe272d3065132b'
        '759af702f0ed8829b1c7aa48b2e739d92d70e21f7366e5fc0332a042528647cd'
        '94c3c671af8a8b05446ec8df7c42982e812bb07b78c9ffeb6f812a8a33affca2'
        '06a08cf77a8cb45a9d74c7a9cc0d47c5a6790e09efb406ed0b43ee32102cb1c2'
        '0dc8fb0416a0df02384b683af0966ec0781bd50b4cf09639f6d87c046e18b5df'
        '9f472c17415f5f8d288026b0615d0547ef11d88b32f14e4667dc61f9ad697a07'
        'cec4607c408a48cd38f8f40e77e12e3d8025b008af925de473fbdef006a7aba5'
        '1c354afef83fe2efb3a1ebb1ee42aee2f81298f3b5c8a02eac7255930b38960f'
        '3b9823ae082809e87c77b14e02bdd7e6cd1419b148fd498da14661f6d3d5d6ba'
        'f6f41797760b2eaa97bb180a8054a52540e96f58162c3a3129755af4e88611a0'
        '1a24fd2993dec0e38118507c025e0e8eb2e7b1a5a02683910991b73fc2047527'
        '3183a24c2d0acff78a46e30fd1da3d303167a6b7e23d7a6cbb65e61626845df1'
        '900f9df0c24ec058f1bdce80cb415bed276cbadb65a6eb7d72ddeee4a160bdcd'
        '636a202483b67929b40b1e84722b8357fbf0ee783156d6233b7114367a77a8d4'
        'd814d349d9ad01082590b92ebea0f894faa6af17631573462c3240883ebea7af'
        '03b7267f6d1fb87320ee149ee6c4269111281a371a19d5c9a8ddb5e28fb637a2'
        'd930ce4069d71d8a8ed27e8d435f4b0d7def606ec82683d27fa59a6a9e8822c2'
        '998e28ff0a65b827008d5e1ac8fef4f21fb2a4344c4c8f3b031ee88021815e45'
        '04e66e4828e964c1f13f908d0ebf8464dd16246defa3f4867b5c2311a9802380'
        '3fbfe5f21d6a09d3c7b89197dc97d15433ab6af5aa3596111316be885c25de01'
        'dec02480555265a7b0a291d82f3ff9f4151c52c26f3aa0b608639458b08497c2'
        '30e3aa3d796fc0268059ab5ceb0ead4308372832a9f390acbda0efffcb20183d'
        'b24b8d949b18b54cde77815e00c0268678845803a3b15a6bd3ffe165c6d18817'
        '9b4c23b9c2b597bdc5a6cb76686276c7ea921ea98023865171ac5327518ce1dd'
        'c435bb3da3d7ea66c5747e8d11e9d9c1ee68c6e52cb34857a824863c25729cbc'
        '64afe6502b8fa35eeb511dac23e5191a9e9566e241bd9335072a2c3d07782486'
        '2fa6018d2db5d2972c4c3e6aa70112f37c2adf70f4a7c418f56ac39585020e0a'
        'ffdf502386231d61b9cb86e345d26565295a58376645508f2f067b3cfa5c651c'
        'a730c56fec0f512580110edc321105d1672eba88afc022700457b8f8c50cb670'
        'ecc48befe9fab2e58da5b6d7e01f801017eba18efeab5c220f2226b4bb96a072'
        'd3edabc98e5224b76871035900098606c88c2a41729f8007860691cab8658006'
        '800221722d4e06800221722d4e06800221722d4effffffffffffffffffffffff'
        '2807861bc3bb9518e01c8002c6219b4f46627ec0634e60838243c5ff50807b3d'
        '8def88c34053238633af4faaed63f800fa325cc2995ea69ab126237b32742111'
        '3452c95121b12c0b7e802486348084102c367afdcb97d163bfacd36692554bc2'
        'd6676da221e96c376b8ac885d8828026802353b2eb2900027fab3b2cb75a11fa'
        'c3b9e794b94c67df515faf9a5ddac32c718a018d95002583d92edf046963c45e'
        'b65fe4e16dd3e331f7dd72fabaf2db85bd4246d29ccd589cd195a90023859e83'
        '65ca45b7b6c37b7639475f5a098ae7bee7324449357b19fbfc113ae7087f0e60'
        '30bc13e1a8a46f48fb64ad560604d47efb986c55fc23844b6454b949ad04bbea'
        '90d97a4c5465a4247039a6f4e6a4809f082dbacc896e2f680edd8e5d992fd6a6'
        '701df2ff99bbc4de2204ce571d58e10769d1b4e76e749109ced28e3e94cca02d'
        'b97101590ea4778721b7cd8f13873e822f55bc99346052ec493710eabfe4939e'
        '7221debeb0875c2c24321fcbb82db85664db07554decc18b7cdcf904c6a7ab6c'
        '40d1c9b483054f02958626d4e64bf56554015946cc62976073cfe02cb4bde210'
        'c0f337edf1de3bb220a32206bdccffc3134f948e29eec4d52f3cdf68658eab48'
        '088f24cd149de2b928aeb05ca36b5b2da1a12ce9d31d7afad2f2af0127bccb09'
        '456cbe83717b5adf2c4228c770b264290624ae24cf80fbca7532ea08e8477f77'
        'b2ae96a27e8c675988b0179af8d2e8f31ee69f0ed0782aab3599024c47e968a2'
        '1d5a23d443673e6b21e0bdece390f3a7b0db4b902f67b1e9ba60d32c76da5f01'
        '702baaef23dc6a36ea53f529327d765613aae6b22f49f374204cece9e887d563'
        '65d71f2ce22a90cbc4a51cb8d02ca744732948b8a1714262df2818a6770c24e8'
        '26c4536376cc08e5c48e45db76ae1ce3c691cc7feea9402534002da38d34fed3'
        'ccf3dd5242312a13bb9d200cd2061518633a7b9b5c8abc0e7bcd4ce4117eaaf3'
        'f9ce9005c8bba58037a2ea67372d7f9bea2760e0949d9817075554fd942d16e8'
        'd06809984460bf81e38c300253d3785b54ec57b7704de0b7f57dbddd6c714300'
        '33a0665cc7676ae64259404f4bc1b0de1908bbd10cdfd2850cb7423252f6145b'
        '1fbb8c491d28cd3304c6ae20445f39b1609b4e9037ba1ae1c24d7b3be2f519e0'
        'ce9078374c99ff39139767cc04f999f7745d84f352cb06fc4037ef82de9036bc'
        '1a7adeb5342ba85b15156b802cb3d60c94f05ced2087d077172f577aab682cd3'
        'f1802c8e1471bd03c15b4f1302078972db172430e5b66a4ba02eb461dc2975f6'
        '82a468857f5bd3cfd9e299b60c821545f50145743dcfcb2d4587a3efe2d712dd'
        '710a43ddac057d422eb62341895583e2410efc69f253e9a66277033752805dbd'
        'ec3a2115cfb890d25249ada3041341a21e5f89bbfa7fb727b6db44c65c17bbcf'
        'a5501e8990810e01cc7ed33d72a8a7b9aef15d87b3ab55ba9f1d429148c77029'
        'b6a061d21c4df078954089935838502197d21ef9c9a626f9ae447ca8bdd24482'
        'a62383487667d57dc02bb5b29f5647dc3c95fd2ff367abe159507c7a190ad96f'
        '6bed049107ad0af8e939b79fb077170d470c81ec082cb33a1438b8b15d34b097'
        '25a359e50bf70453250074bbd9e66612269aba117741216fe4bc1a87ccd96f26'
        'c8a028aaf8b6fe72a9cd7545d6c5bf598b62274f24d392cbcc08f1486b897e16'
        'c1449d3d8f538db1fc3c102aa7fce7711bac6024e3124957e9ec16604efe6c04'
        '4f3bbf593ab634771e37b1208d9f97c6552ddc2d05cc2eaaa9310b2f03fce370'
        '8d094eb1a5ee36043a99d243fc752853430fb5896775787caebab59f38c844c0'
        'ba4d4922402ea8969a13c70fb5c9e94eac8a91a16b839d87dab65ca992469330'
        '51b77ac54dc03d3795e9c241629d235e81dbba002db012440434459c62d581c1'
        'be8815f517c16fc6da138defb06253ba177e1818dede438014c90ffd107888b9'
        'ef202db40107f9d15cc8ecce48c648b230390319fe84a34d0c5f3fbfb211ea79'
        '9f2096d9ea48d090259ed85db04f82c02293a124d99872ce6fb0d796a5a678d2'
        'c8d7ece803fc0bf5704c147ae76cd6726e5244258471a69b301d941fc94d9a9a'
        'bb3a57b01e98c10edde3da8f27f151c3285f5304e83d2224ee2584b22b392099'
        'b56a7e546cc47d109305d38bda7617d717888dbec12d6c18c21028aef8f56425'
        '848bbeecbb8aee47905108e279824c68653a2a3e947a27c533c3ed4eead89cac'
        '6c9a703d2425843df70492cead344028209f9fea0fcb09f48b14525e613220fa'
        'e4b057bfffc05241bcf280258450d9510bf670ad6fb568af3c63908dc15d9278'
        'c524de8af63a5e8757b53299bbfd0f17802584bdc92c69f6321801ca6aabf673'
        'f76e3c0571caded5b1df3f26f0913fed0efef3d62f138030b8aa50709f28d386'
        '940b3faf76e76f62e2087904487b2184366bd59ff768d4d950dee9126aafe200'
        '8f4724f1ba9465962eb7d3f3ed1c01362aafca5d3c70b053d90cc08179af6d93'
        '4a59ade134d0738ee3150e6b0716466ad0ce64e83928102cb6f05109bf646e4b'
        'e7c4e22cc4681661a3e4570dedf98c8d028e01a47681c2b1a6c3eb0020012f97'
        '56b5f8802cb6ec22057c0faea54aed4ad81d5e64037620ede38579817bfe9765'
        'd9243964af82fffaba9e354d891eabcace28b5f90c93132fcf8b244e65a7b165'
        '949c7590829ea0fddbf951c9bfec6a5b1146ae8994d244d0528c29b60f45180b'
        '5daf5d5d17522028a3adaa8d13b680bdbd3383d254a70b6f34e8dc2690ab4fee'
        '64ab7fc82bb7072629ab0b189f8e66811859e7009ae90f8d24f24475624d1947'
        '1ebed44a1ba4f57a3fe43939089913c02db85ff0614f1c3e0209bf21f72682fb'
        '1cf80567254fe94586e56ce993b7871007d42a4e05c1acc818052b542e802dae'
        '2b883d4fd3998ad9928765ba275511bf93180a26cb7ca28fb91171412033c30e'
        '58c433c00d5daad2af01d5b035b31d04dc10fc70bb53fc34b27797c713d6a332'
        'b9f33fbf7a8b8cef49094ae4d6d6f9161cc83a3a14c81e041fadc7981f6808bd'
        'dd9836b35f2ebe4ae3e0332ba275545ad57267f393d0412ab6bc0dc99efaaa7e'
        'b4d69eb7d3643abf37eb6f64c8fc45fd60348f348f3aac25882cad1bbf4f669b'
        'cc47204a05d002b059764a7e14b5b2bb241702b317433992f92c96082cb4d4c5'
        '4c6641c0faae32b18fe29811e8084378e5a878b66337cb0e43245997e4b627de'
        'fc185d26d9161f0ce4d533dd02906aeade56106fbad492109c2db307bec65b51'
        '42593b6e1007e301630435334904fbfa5498b20790fffd4f67c82da7e8bd5bba'
        '18bf4d98c8d60022ab255f2b4bfea9ce5f94ef90090f66f0d700f53cd68fe6c8'
        'dbd9a159ae8561eaec5026ab47abfc7ae777084463df6ff6fba453068e578cb4'
        '46eb2579fecab76c84e0c94d3c658d6ca82fb10ea14b6809b7164aa34fa75534'
        '0133c27b5bd63f9a28d137017a024a2b5f0c8ddfd0d6b93f049e54668c7c0a8e'
        '602db3dc34e118baa4e971ea5f214803b19748f103514cf8fb548b27b00ff9de'
        '9b150af0e4166d238b1fb816e975722cb53bc46aad5337848df7b866e211d518'
        '99583df887b1ac811171342b1c040ac82592c1a0e9f429f1bda0ed802cb60aa6'
        '74a781acc4db46892c304d6a5237a2e75717b80389035f87ccccecf5d64399d6'
        'd4ae308b68d47ddaf82bb604e0783b47de8a25e816b7548144a250b9ad524747'
        'f74566e503c0ed221d1704804e286ba07046aca97029b604e06698bdb3767e83'
        '47e2884b43a6d21630c1c62031f6fd3a8e15d3a2d7b3ea70445fd68515eb0027'
        'b5b2ca01811e7ae8e9bbc1c6bb1d486146bcd9d4213af88f3e2d8dd97ca06e02'
        'ee2f67ac41a14027b3cc540b0944ba5d0104ba7b1e527b6b68cc82f88749cc4c'
        '2ec527efe5754c9a5734227ce7650c28b177b124e6275c8971d7624f387c86a6'
        'c3b2581471a5df0e69623af5d68231b9a18cf2bf184284282cae81a6f337d7cf'
        'd5a331e7564eb6d4f95733e02e7a2d10644ac41971c4f2095505b85d9315c788'
        'e836d52c0027ac662221bb05840dcb669fa52840e551bdceae4fa10056914473'
        '00896cc5fad634a67440b5de2027a90e7b12247a931f0defffc284e99e671a00'
        '28e26ca6db3b352bc3e7c49a3957af1c9d1b2cadf42faa231b0767369b3125bb'
        '9d6f2a94289eb5fedbe417819e2f09e47d466ba9ef5072ba8a6d2cdc764f422b'
        'd74004ee4038b29e6d13f0065c59fcd1cd7695dbaba601d09d5aacf8f2d2bce3'
        '0fb69bb5775b967bbaa28156d3bec8d254d77097071b068a75cf0417738028b4'
        'af002bf0a1c56d596fd75355a6dcdb3db26485e690dbd931521d0855a0da53ef'
        'a4160fddc542cc2481a557dd417e097dfd03b0390954826ca74c539a19beb9fd'
        '2542a33248b6a4be50a00dca2588ffd575763b51f53a9bac1c83f07ee55bf72f'
        'a4c3ebaf765a9a44e1779ec0d789061d7aee2480915c29fd3461e780683893dd'
        'b0faa51e3a60d2a2b14123969c472d369699fb13bc5844258377a17008da62ed'
        '136aa10b9cbf7f381b35ebadb6fb7c42345e1a451b49a1c0c10947cb8025815e'
        '450520f433c87eff3bf7e4325c9dfe5abccd53b192b68e8909ebadfebd5c132f'
        '5883002480592017120160b9e8cae72eb9a444c9ffeaaf7c055742832073f5ed'
        'ad5ff1f3b4b68980248034590a399870a2f05df682c3236a91f583c3dfe2b264'
        'e2daf8c0bd294d58ff5200dde02480d81245b47d5feeb124fb2cf8409b6539a1'
        '57575000b259eb4be0845dcaab50222a9550258011057c547b6106a02e123b87'
        'a431fef5904ffd70d99aa4dbdff9e6bafa276fb114e931c82586324dd6d0b742'
        'ce3579f3e10ea75a44019943359f28e8e6ac518564bd5650033ee7d73d462480'
        '0b9fc47e8446cf6cd579f20f205ad2e0721c36393346b39e0bdd4a991130440d'
        'dece2825863608e08fe5fa4f1c79d891d4b6a82917d03478d05a7ef4cc636ac6'
        '14ea3099a4d61f3a24248633af9dccea0353a007996db21791143bd79dc13d80'
        'fe13c4660be4370cd124572ec550208633af235147e10b748e6cbbb6a5951d88'
        '6598fe1b2d31be848c1ce5484d53802680110f79909cb7439cd70e76c351af75'
        '6210bf4f35878482d68aa4041854ff28889f83efe6002386225a832a12f48086'
        '714db3326831b829d50aaea9520e198dca7e4bd438f77cc87740258622c217be'
        '3bc9e43e589295ff347d2adca836ba528fe3dcb3198de674cede5844f807a89e'
        '20862f71315e9c0bf615092539d961fce8afa577875cc442b2d76642836b4be8'
        '3c2286221740c314447b698ff78af02ff5c3839bc2319ca7b148e11c766198d9'
        '0e04330024800b246551460b3ff4fbb32287aab00f5f037c9e3f0a94ea0bd23e'
        '66cf0691f889fb16e0248621c01e9ec9462805b36985ad5798b45aaf772e0a98'
        '06c98dc50158efc2138718e859b40b862061aba9da02632acad007860691cab8'
        '658006800221722d4e06800221722d4e06800221722d4e06800221722d4effff'
        '5dc001b0984003007900f83698603428e802220296cd6420000a04a04649666a'
        '1e1e900183fc47fa75087c83ef9c1fd1fe043fc4433e813968041f288a7df3ff'
        '88fcf304b41f39442998f93e60cef9fdf3c7ee26476ca5111697de5a36be34cc'
        '381bc40000000000000000000000000000000000000000000000000000000000'
        '00385dc001b0984003007c010a36a043c2600d08000640400410038744732eb6'
        'dc31b5650e2df033daf1569669869be66e8290bbb205eacd4b215ce084e8b622'
        'c68e6ccd28326572d839e106de2f0988061a562de2240481f4c20df01f53e895'
        '0a70b09b0e7172f271bdd308900b9530dcc59203cf19f87f6bc32c11981d4cec'
        '0496d2c31000705dc001b0984003007600fc36a848b1e80c029418a054060108'
        '02ee78a01f4213fe141a78cf43c70d69108877974be678adcb45ea97fcbe687d'
        '9a51b46f7bd3208c23a252986fe99a718dceeb9135d216d4ba65639a6de2f527'
        '9c02c4ef01ac81229864c54aa10b4e659aab284f42519fc42f0e2264c19c0000'
        '0000000003805dc001b09840030079010c369843f31900400215a00a60580b2c'
        '3f2e83ca334af33975912abbf2ada96c4d558c3ae2437d3f544fcf72b0ba9192'
        '40444a3522b46831ada9a0d04270ae8f08d8ada1166629dd3846975615dd69ce'
        '919275b616c85e05108ebd13fadcf2ecf754e970c7b86fb527db4b4304f20009'
        '48001900000000385dc001b098400300790108369c6921a80d62d82c02b00365'
        '810231fecc422cdbb6d6336568f4e25df92974a88cd2c3b7c701f5c8339d3ace'
        '4bd137df33351399485e4eedd2bd1c48905e2004c215179c86920a4f62ecf642'
        'd12344e375226a2fd1a295d477911b2fb1b84b7ea9fbab5a160dea06d0000000'
        '0000000000000000000e5dc001b0984003007900fa56a88921a00c028a282ab6'
        '08a0104121ab7a4a885709c54bf29d03d6f7f9c97a6ef39b45cf2c0d062f0d95'
        '0a8ec944dd571d2a55410597b949ce48c45046c3b2212517904e2705a77dea41'
        '541661205e0d627950e3568a56338cd6ccace3868aa3ebcbdd04e31524115a60'
        '01a4000000000000000003805dc001b0984003008a01209db03f00cd8737cd40'
        '57db2043ca801adab29ac00f840fda2526290c18c9e02990a38a9f6ed8d58852'
        '8c4d32b8e48995bfb793864441b5d1e915665ebb035fe231815a7e5e17eacb9c'
        '7fd2393d1179de774542510c16d49fb8f9892cbdc9fa14bd9b22c6cb32653916'
        '28d4f39cf5bd9c79c7c69c1b8f54d7b7e53535188403dc1859be4b5bcb17b75d'
        'c001b0984003008a0136f6ad642812a4ca8400a0414b5314dbf43f9f92b8edf7'
        '3430d41cf84206308e5e811cee80974dde91bd3327552585be4acb4951ae4c8a'
        'fb16085fd37bdc9ec968ab4c5ae70d8d4bbcdf07d901d8d05569330e361834b6'
        '00c77fd922b85cc4e7319d780d7b7aa83a27cae5e483377d1b45951ae146d165'
        '4f8c41413ad611b42f2c572e59de0a1059785dc001b09840030073013436ae6a'
        '2b0b880a6107b4eecc4ae2940cb50777126d027e34c791e4fa127079993c04ac'
        '23f8d77bc8b804994be709d35f5ad2d441ab27dfe9592fc0e84067156c8d2fc3'
        'd4ed485ad313163ba55581687cd6f032b7e66734c580f790ca7d1cbda57834f4'
        'd680b350aa5180020e9ef89ff21c5dc001b09840030075013436ae662b0b88c6'
        '023083d6e575337ab5ec46f4a014bd478c257b2e41b5c86338912c7b6c3fdde5'
        '8b58845773bb6a49d89fc0654c0d8c15830bccf2c11f0e0d2f2f33532b5fd16d'
        '842d60eee5368f8a5cf2f26a2856f75e002ed86f21b297c9460fc75443c0cadb'
        '030a016bf2f0807eb80fe5b85dc001b09840030076013836ae601b1005820250'
        '849e730329acbacbb0078205011f08ec49e6b724b8de4301a18016a0804610f1'
        'e40240bb291494c2cb83f5c247a544ae315eacb7420e35d273b8ec62be8b3817'
        'b565528549e18e1512180a67a8979a3c01cf23f4dd38a98d3a9c638cfc9ea66c'
        '7c5a32e3622fe5bdc170e05dc001b0984003006e013436add27b0a882ad56e54'
        '2b349b16de94323122e089f02dc91e69209f07e1376c8b9093116757f119dea5'
        'e8465365607ac935422330132975cb9225c2063c8e6bb37f69fea4954e312332'
        'ed0627f5eadf2eeb7f89f2d781963c9f926ce92418deff51ff232647c00ce58f'
        '2ae05dc001b0984003006f013436ad841b0b9ec2620aa5555959266b0ab0a187'
        '9335327c2e5caec22d836745776aa9549c280806ce76012911bc63a56ddbf7a4'
        '62ee372350a138efae49a50fd455bd77750d794723267dcf2aab35377e8e3a50'
        '011c50249a08002cf65bc00000000000000000000000000000385dc001b09840'
        '030079013436add280208424bc54a4c4ad6d506bb1e03907244363bc2500c443'
        '2ad68b98bd7aeb67e5099e975c913425bc7644c495a1b36850e0dc04eb4d721e'
        '5c3868c33fc9c3a158885bc72e342fbdd015a7973b7c6a803fb77fd3df8d2637'
        '860000000000000000000000000000000000000000000000000000705dc001b0'
        '9840030079013436adcc50158582076103578cba6f5513251c65841082e991e9'
        '6c23362108593c181a77d8f8fa510904cae52ced0f2c652ce917f9524429cf3c'
        'deb0c1772d0826ca3667e1b2a46e144be2237dd3e5bf514415877fad0e20f431'
        'dfeae624cf3cd5495540016cecf1f1f1f1e99aaae79aa94650000000000e5dc0'
        '01b09840030079013436adca78101d820e5e6499219262964414b123e289f23d'
        'c4a262711544ae3856b14bfd627023f2eda30ec1d15694797038ea1ddfd260a2'
        'a2b273daa1604478b7112740ecc54ecaaee0f54a83fcfc386403e3ff8a3928c9'
        '404a6850a055bd6a97cdaf782800000000000000000000000000000000000007'
        '5dc001b09840030079013836adce7b099424bf1f7df3a75d9c785d8145873e43'
        'a04b4394fdf6a9372c5040acd5eba41277dd411ae25f4196c4b6e13ade9d0319'
        '9883234b24a410303959da3068f3406eef6153d5479759a0004b6a0e68516020'
        'b00f666eb7868000000000000000000000000000000000000000000000000000'
        '00705dc001b09840030079013a36adca7b085d7a732152b5b5a0d061cea821b0'
        '17f858c24c7358fe0262270d530930c8dd3efeeb9b9b554a65c701a1cb41e01d'
        '42f022e64d040507311603cfb514f9babf2acd0072c7b69d034378d000000000'
        '0000000000000000000000000000000000000000000000000000000000000000'
        '000000075dc001b09840030079013e36adce7b085de671ea098d62c8683d574c'
        '9d723df3db39eec71609070516592135b1220b919a9da4491aa779f38f5df1d6'
        '6f2cab8062ab8d05d7f27089e62019ce1e400ef3674e601aca8b73fcb304de2e'
        '0000000000000000000000000000000000000000000000000000000000000000'
        '0000000001c05dc001b09840030079013836add4500d0802c10abcdf8555e497'
        '832e9741af3b909734d110e75bf27e2df2393d66524e8d6b54230c76e40a1a7e'
        'f394571b5dc02118d6725bdc1e2dd15ee773e9293092f8a359a55cd2ed2304dc'
        '4f12670ad2c61c84fa8540a281a0ecbb22f6ad1006514cbb8fa30e68f46ba68c'
        '15b28000000000705dc001b09840030079013636adca401c842eeab6a944be6f'
        '6abb101c9b1c871feee4b0ddd89726eba4a8c5ed097c2414cc1d56249f232121'
        '7a6bd8a4c97b8249b61c17c5ae3f7aea9403638ca35601012245d68c9cde4765'
        '7015f371f3bfa26c837888000000000000000000000000000000000000000000'
        '000000000000000000075dc001b09840030079013456adc8202c842eb30ce372'
        '64c582000900d92385e1341c1b1b7e432c420a1ccc52640922a266a1f84c7f0f'
        'f0ad4486021243f310498298a184acf1efa834275ee7478c93e467d53d9cbe8f'
        'f5784fe56f3378b0000000000000000000000000000000000000000000000000'
        '0000000000000000000000075dc001b0984003008a01249db2daa0eadaa0ead2'
        'b0eaf9280959b755c92803144e013a586a265100eb098c6460e189c27510be6b'
        '9942efa949a23f94cef65ddec75e6e24b9392592122209639a453388e32f52f2'
        'e0be032eff43d73a93af97571afe16fa9e4f2e49424c37aebb3757039c09c24d'
        '08db1035a036667cfee63c07542ab1d3f6ddb6e3034bfb1f6ef9302c3ed7705d'
        'c001b098400300b601469db2d4346740da4c740d42e4151663d81bb2fafdffb7'
        '7906977afd7fd4069d5fec0b5afeac3b7234af9fb0f96c1c538b4723abadd7d5'
        '314132fd04a6b28a9e86fdd1dafa649d08f22f8b3561b2fa1af46ba3ad985540'
        '1633ccc2b1f7675558f174538f4a06be255a82a4274a59a16d2e1cf4d4fa336c'
        '2427cfd1ab048ec8b77cf2a171320c21180982a916329583e7364b0e7509ee1e'
        '62708f2cceb3f36f02844d7b69600eb3ede4b04bc33e78275134b0b900e05dc0'
        '01b09840030086015ef6a169016c040801665b2340cb1825db83cfea1ca44a78'
        '28a3274226910b8d1923a9338d714dfe860f4d4aac39fe4ff47fa6fc00c699f4'
        '6aef0dd945eb2ed52e57cd8b409f98d4199e39579e9b04a8bd78defb41f40041'
        '84a53092a52890599e4b05d6b8253c8006bed821913aa2bf237888953c6fba7c'
        'f9089a9e03b252d12d18057f805dc001b098400300800162369a444530ad8205'
        '2f8efa0ab002c1cad6bbd27f2002f4700256f30cd287b001c1d08ad3a5d8dfdd'
        '3016a172325a149c2de098b90d391a2c5fbf7e5bc62250e539a0178cc4f766cd'
        'af0c214bcc2e5e70bd37aa8c53c60597062273e223648962c1327c4c504ee2be'
        '1ca27648928c3cb863d73f1691da7f2665f445273a785dc001b0984003008301'
        '4a36a120181b9509014130c9009003ce6f7561e6115d89a5870b6d7a11995bf7'
        'bff47fe2117d514c98a15feb5966f04d3fd7d6a784a92b85a2c72bf21c49aba4'
        'acdd46fef99da924610ad99ea6c495495bf76a68b0b18db64f4b52c735fdf037'
        '7f94d3ea4282a71a02cc130acc641db618d758c7c47b3983a1496077e57bc092'
        '0b805dc001b09840030077015c368034293da80342630400ebd9f16500a20363'
        'ad5a0a38cd66b30e8fd435cf01703a10742f384f2c6f95a58c78aeba6253dda7'
        'a3e702f36e0c5ac918e9f47bae9128270a4df46a06b9d68a60dc1a2d0c666fc2'
        '6fd33460155baa05ec47ddf8fd31f278aa5fdfd75ea4b8659e892949d298991f'
        'c9c05dc001b09840030044013236adc076085dad800054201111c3e4c8f807c8'
        '126d0a224ebce6d22837546754c4e518761f6aceb1a1fc6cbf93bf9e093838f7'
        'ca8956968c3f77174c22f475b333705dc001b09840030049012e36ade069085c'
        '035dc4f1c587759c8e3ac25ce3a410553b4d3f587f93337ece4930f70ca1624c'
        '068d22844ad53ebee1f70e47e20a0f8d3ad50450b3a50b6e85d6600020d5f924'
        '0e5dc001b09840030074012a36ade06a101d820e00012a084d773c8c7611e75d'
        '1c8da8e49a6cac0fa5e40813b088bef6dfb7f0fd0b8b102daa1a2ba783525288'
        '6591a8bf2b804ba48c1c08c62ee8afbf911a9d6cfec9d76314187039b366cb42'
        '2200b2ca74b3c7c7783000000000000000000000000000000000000000075dc0'
        '01b0984003008e016a369cf4743d098c630130812d06f5f573f7af1b1abcf6a0'
        '1e2bef40e3fe9f446156c984a2312e821c9596e4dccbef3d7c6725871bd5ef29'
        '8d27d13c4fac99dd0828406c54a7b934a20ed549710b522f2cf217c6b5f36e33'
        'ae49662be1dcc5e9f3646911db80823831cc1238ce085f0d3a571f8b740edfbf'
        '9b733d78493574ccf5edd12537a3bc8a686c0e605c5dc001b098400300640134'
        '36adcc202b842e978000138b41e4e25e2333a590f1565c85a158a4e4248c5e15'
        '444723baecf5c57923fa742407621d7bb37dc4e551fcc8d8a1c2d837e960fbba'
        '051d7a2dfb3949d3f2a52de0e000000000000000000000000000000000000000'
        '001c5dc001b09840030079013636adc41032842e92a66719bbbc99037b8fb028'
        '56ec223c9f338f5d598e6dce81ecfc850a78ef55afa957713643e075ccbde391'
        '2f84c2c742298f24800aa0b971e7f606f2400000000000000000000000000000'
        '0000000000000000000000000000000000000000000000000000000000000000'
        '0000000e5dc001b09840030079013036adc077085dcd75ae43537be017ae6ee5'
        '7e5a5b8e37d4ba6b278f895731e8dbbcd05ef922464ce29a1672faead84525a5'
        'a5a12b0c5b33b5a95be2ea7d181bca0000000000000000000000000000000000'
        '0000000000000000000000000000000000000000000000000000000000000000'
        '0000000000385dc001b09840030079013636add0500b082ed0c565d719795575'
        '56197165478027aecd10c8f0ec798999655677085321268080899305298633fa'
        '2a90fc285d1b82fda7436c3bdb347d9e3e6b8c145c774237558790fd8ebd7bd5'
        'a505f318a9383e762344735a94fd72be446de0c0000000000000000000000000'
        '000000000000001c5dc001b09840030079013236adca401a842ed67379adde2f'
        '35b011d9314027c7f744785f8908f66e8b83c9cbb2d83ffcc7b1b218af4959de'
        '9bafa501661deb21abc2955c4aea3655fcc426a4b1c578a77c23dd8ae803a639'
        'ebd90378b8000000000000000000000000000000000000000000000000000000'
        '000000000000000000075dc001b09840030079012e36adcc43205c0046e32c04'
        '8a4534c8ef8045f6886cb8012c79fade5765046b3e21053fbcb1eabc4e5807fa'
        'a8824360b0ed64e93b1ee478fb532bd540f55882f7792acb9516e2882017d415'
        '81ffa8de30000000000000000000000000000000000000000000000000000000'
        '0000000000000000000001c05dc001b09840030079012e56adca80b800badde2'
        '00202210153c9b89274004b4bbd2188550a6f6aead213924d24db091cf972850'
        '5a69a14930451f591a0eebc957ccd9ef58e689d8f06141107dbf1df6c5f2071e'
        '776000fcc378c800000000000000000000000000000000000000000000000000'
        '00000000000000000000000000075dc001b098400300c101329cdacc62612dad'
        '18a40da1e9501b290f030030cca596ff4fcf80386bef5fd4d01f704d6ff0bc0b'
        '1bb2aa1ea65b96d9f4d7b478172860449465d125b4532a16d9c46f6715931cb4'
        '53d3435719f47f0431cbe04961bcd889708cce0254d2e51cf69d04af305ace5a'
        '031d179c4c3aaeb018fb785a6a09d2804405b32d5de9208864882565b408db66'
        'cba529a0f164c913811cb47f03579cc5877b72c861dd99cabebf5f537b6fe39f'
        '846b4124878ee2423300270d3ebbf555c4d0ca1eab6fffcf5dc001b098400300'
        '470130f6ad503b101902171400605a30c8bf664b1b8223c221e56c712c2cc249'
        '54bcaec22442d9edef1b484ff4bd812f0a62d25cc506ac4cd3984a49cd70fe92'
        '364c9862c3d1c15c5dc001b09840030063013036ad801b081cc2172272ae3772'
        '8378917e0886f815240260168cdfaf8b304d8a900eaad44c4a53a032f2b0c6b0'
        '38d78e8e3c3ddb5b617f168b78c0000000000000000000000000000000000000'
        '00000000000000000000000000000000000000075dc001b09840030079012a36'
        'ad801b081d42171c77592d6cb19632a62c8e137644e6a94f2f832607eedc7e7a'
        '5735933b01414beede40b8566e6ebe3181d792e75043cce7f94adbcac0000000'
        '0000000000000000000000000000000000000000000000000000000000000000'
        '000000000000000000000000000000000000000000385dc001b0984003007901'
        '3036ae0018100584175b55ce70cae2a5140a3b21009701944ebe5c9f01c85150'
        '89cb610a9489c0839d15d63f31838977dfe6cfbcbfe6f7929ff19b15c94ebeda'
        '5fec072a22a58dafcac95d66adb021825dc6ce527028f256e8e4ae3bc3000000'
        '0000000000000000000000000000000000000000000000385dc001b098400300'
        '79013036ad5260e82ea656696d84727ba580d1062580c458c6a5f6e70cfd3f3b'
        '551bc95f5fdba0490e5840d3c5cee5694e834ff9fe9412d16f31000000000000'
        '0000000000000000000000000000000000000000000000000000000000000000'
        '00000000000000000000000000000000000000000000000000e05dc001b09840'
        '030079011c36ad304b0b14c61700aa5d6b90023d965a439c1c948ba4b8360eb1'
        'caff671e1bda89221d4a7f4a261178cc89f69df68a3e2cde3a36b65755c9490e'
        '7da90dbdd70d64f453384a4cf4934933cc01e68de30000000000000000000000'
        '0000000000000000000000000000000000000000000000000000001c5dc001b0'
        '9840030079012236adc1f003466ef596390642a24e440d6644f8cc04cd3bb34a'
        '7d87d2a34cd161e11c797b1f357233c89a949ccf25a62dddd84f02ef32000000'
        '0000000000000000000000000000000000000000000000000000000000000000'
        '0000000000000000000000000000000000000000000000000000000000e05dc0'
        '01b09840030089012236ad7052230c04c300b0c1ad4ea78e2a3239ad781c5743'
        '7d037bbb894f81458b263bb4ad1392ad108cdc06095a5e27c2d9a3eb6fbd456c'
        '5160c0a1804463d2def773deca2e473bd99527264dad2bec82743043cdbb2c1c'
        'db80216627368c39cd6e0b8d4d356e6c042e78d7aa99a30b3127110427a51cc1'
        '91c47c2da869eaf26ba241681606c31e5dc001b09840030069010836ad0c400c'
        '043e005d56e49039ae6245192814c94b592186ee0e2d7f49f5ee6a9ba36769d1'
        '473f0891cf115d4e4b7948000000000000000000000000000000000000000000'
        '0000000000000000000000000000000000000000000000000000000000000000'
        '00075dc001b0984003008300f836a848e0288c062323209820140810000005aa'
        '67480066bf93d77f520c220874368bc12e5866a130c26ec8542f4a1c213df740'
        '0463865c85466440792582ae56fc9df3630eb46cd9d9274122750148b9435de8'
        '8d6ee809d30fa1d756d212c0472e870c2e563c29b9d55ca1d91464cf6cf705ee'
        'bd55767961707f830dffaf4bd9c05dc001b0984003009600fa36ac6ca4514104'
        '2101802a50a060a00121812e59deccc02924d54c2bd173228d2b0174349901fa'
        '81c5abc73e973c7a6b30103c0ab5d2ebaf70296b74c30520eb5c816933008b9d'
        '0bfb602062c1aa393082222d53b057308a2550048b32d203cc05845d9af0a808'
        '2b0b78c07cf02db44dd7d3fcfa33f6e55b0a4ec0c29b314ab07e37b801f65e45'
        '8ca82e960c18bcec56c98a00e05dc001b0984003008800fc36a1486248a88c21'
        '0180051800c0081141ef57d649ce64ecbc245244b0103413dbb51d145c9a41e4'
        'd8b7a52b435a38644f31eb09231e864fe2d7c44d19af0782d70a6cd5897b764a'
        '134a73b27787bed1572ab4961793446a9652a398d01730ce8bb1621055a45075'
        '4b119de90180f185b11324c512c427e72a216e7ae8854c3009e54c70000e5dc0'
        '01b0984003007900fa368bc2722010400000000580066f251f35883b511c2820'
        '6d0ea616401173f0843ba58900c6195b0ab7cfe483c2761a53c9ed9a5227477a'
        '39e3849bada82bf6a21c340dd543a60c74b7c2e9553a515f0ddebac11e8e1f76'
        '41174e86f67f327313caaa4b0f1f86bbbdb065996c92db10932c9d944887dde0'
        '5dc001b0984003006600f23680a8c1c80d15628630c002084a0168ea35173877'
        '0cf9931c5fb7c006b4a69a7cdaeb861bf4110a488274275100a6d9d230330089'
        '1056199be81cc7cd794b223881698eb35a3c5f8fe1ae8a48dd0cc46b49256956'
        '0aeec73bcfbdf22ae6a9d4aa902fc05dc001b0984003006f00ec3680cc91b90c'
        '08f1d206cc1b01081103d24621720d5c2bb6d4471087562c13f2d7a7865ab9c9'
        '99aad44c5908cdb52549da536ed94b269765262252521a593a65e4b561b365f9'
        'a723205bdf837a06c32ffe6e849088baa55eab7a81c209eac3666078832d38fc'
        '10fb1d2bfb226e5dc001b0984003006f00ee3680c89248c80614a43602a80104'
        '0db1f919af10bd8868260e60115d9a665dcf9d58136689634ad37e30cad58662'
        'cdb17eb61192157da5c60d8a4ec70d5543d3f9ae149b1db7da0539a2202f249c'
        '9eb5b7db27538245c4ec148c9651d717a39ee866b9aba7d75a784a3f5d75385d'
        'c001b0984003006e00fc368ce8f1a00c7b0500506f00044003fc4ffbb7250c7c'
        'cfebcd23822e4e6e1a665dca5ee86f0387daaa86e97a11741f3158d50a852a22'
        'c9509253e08af02f6bf028d0164ba7112cf6aab94ae69b647494fc2f0b7e187b'
        '3a463b66cd74abaf34e7ad46a596270bec09278c46ce5dc001b0984003007900'
        'f8368784b1180c0000000084001b17dda2d7f631bc1eb8c6b9bd3a04bfa5d551'
        '26184af13d4e5ce91f2502d70302b25e0d5c561ce2514465a8043312dce9ee01'
        '642b6dd5e139e662e5c69e67d181203180df6af061923c59beb8acb823b97649'
        '4b5fb63bee811c0527e97366e7b72d99d43cbaa3eb40e0e05dc001b098400300'
        '7800f83687a4d0181028000400800057acab2aa3d6df28c08ae01cf7352d6292'
        'e3ab62b6619f6ef2d179aa6578f57235bedb6640398a11fe432d6fb4254a4bc8'
        '4a1a41a652b5a2e0932d8835a959777399a70f804f44810b96a3e8b90dd64cf5'
        '480aea742bd9b671741c42cc028c7bd99831619987786108f85dc001b0984003'
        '007600ec368088e199100873665a94a599802ac201ce1c570d53bd3567783075'
        '215892cf2b8ac5cb67b7f34bedf0bf956bbc37c52a876aaf9a75a997304eda28'
        '802b6b689e49e2aef71940d4ccaf27c7454adb89584f29e886416ea3b9791b7b'
        '4b4a0339089b455f83a031689c5f1547444621ea600d83805dc001b098400300'
        '7900fc3694c86250c086041f43be1ceb218036504411001e3de1a3c68d3c2838'
        '42699b53e5cfbdf15da4d1af35ab78bf01fae48c5142f7e1f408585e568b15e8'
        '2522722b8d2ee619cea872e6e0e4c128ac847bf0c90f1386c61288a54bcc3f65'
        'a9087082cf1fb88ad32cc78a78ef13fae0265eede45d788000385dc001b09840'
        '03007a00fa368d4c61b10c0879000d9bd180010803ffec7e05cbe0ed410a2f6b'
        'dfb8455932c51582a072c525e9dcbe69f9a14b09b733f694ede484a73aa4d565'
        'e100b53743eb59ca7e08a12555be95505d6f3d2f47ed321e9dbeaa4296c12bdd'
        '55f62d89c112b40f36f2e85a9aaea6d37d677d4403d79248cff42f41f85dc001'
        'b0984003007900f83687a4a41010000000440410014ddb3653177c3a6f0281d5'
        '54b93fd271a6b08553bb715c79105eae57c582bbe334fe0cd6e98f21c59b0410'
        'a771cddacb32f7bfccc7e5b4e9bcef22277f9e0b39bf0d8740f99d858599134d'
        'c2001be8f2bc0f538ec2b489cf95baecfa1e0b682658db187160565700c1c05d'
        'c001b0984003007800f23680e8e1691c08570aa8ad877a00440828de847ead87'
        'eec4a509b4aaa2232d6a54b0c0c621c0af69253445dce929cb5be2bd7a2643c1'
        '3c16bc89a4b46f51645b3ea14f32c61dc38d13f65783473a8703762869da0783'
        'd71d93d496927ac8824043b8a58e8cad99e5f78de6e897ec1878617b18000007'
        '5dc001b0984003007f00f836874473110d06040000020100480181388d9a12e5'
        '294746ed38666d6791addf267be88a96078ddc1f4fa476281c9a554fa1bd2aa3'
        'b19d1611909462cda3bd296b366af7394866d2d578f60e2c6fa42494843c18f7'
        '837a12125d9d9d8b5a1e437032e67770abdcf874dc326afe4d3cef16b5d8cac9'
        '439b852239e176385dc001b0984003007600f636808521711442030002801004'
        '008b565070761de1788c863c2545c15c5a41b9048052d04a0bebe445b324dfc2'
        'affd92ebcdf1dd978cfbb77f29242eca9db030f968dbe352edb40fab87643b9d'
        'a52edee5a2ce9fd67532114e0c83d59ec6c7aa9b40511842c9c814a92a6dac82'
        'e2527ddc1b12705dc001b0984003007600f43680c89258a08a22022d6a55532c'
        'c0c059000e5e181d42e684b38c942274192d2ce9ebd07ced79fb99f4846cbee5'
        '2b022535fa21312269050ee9d8449b8ef67ade4cccb732a0539dc139bc8290b7'
        '1ff3b4c4fd37729108b983680d2a12686ab3008124a864a8b57f97b846f123b8'
        '61c6000001c05dc001b0984003007d00f6368085500935040400000010081ae0'
        'b39f2bacd35574080fc973ab800f2ce27ad5379bfd265b6a3d8cad323753cfe1'
        '394b29a5c8182e4925047aee17e3a4d82d78052a6b091e9750464aab80ef1dd7'
        'db5214a9a610a9cf3fda162020ca1bdcba033fc294c180bcf857154ea86f9b0b'
        '899d74288da11e58ca4d95705dc001b0984003007a00ee368088c181144a034a'
        'a8994a500280010378dd2f5fd18fd06b4a5a1c85c814314df47f964d7dd694e3'
        'a2e5acebee77e2af525b85cecfeb2d8d7bb5e9ecbcd7420f1191a98770ea620e'
        '38c7c886081e6d8e928aa0762d9025c9b077dc2cc7449ca7782a8ca549196144'
        'd7d4479ded873c70cf91d535ad659c5dc001b0984003007e00f6368064c33941'
        '06040000000825240fd816b132dd4dd4a75597003ea79cc7702ec5541323432a'
        'a8051401f2744b1b470e91f65c24481c9bf0e71dac7f0aa3f4ace3cd36c36843'
        '15ba29a6f6e36d95d9adb3ad163057cf22a294284f08896da0bf654c354ad8c2'
        'c886e448b6b79ef5c828e71a1867c89d02e831b54fc05dc001b0984003006f01'
        '00368c8ca430d8067e01cf05281ded01282c007fa73d518b38ce27149d7a8daa'
        '82a9def4b6d06309cd53d066704b9df8bb8266016968ad9091905645a6922588'
        '8671214e00261c27c17b2774ab39ad3659227e8d3023d60277a56b620c68492a'
        'c6ddaa496ac60145c320000000075dc001b0984003007900f2368088d1c80c4d'
        'e8152b6a00040800012ed8ac7e577994855010ab790e0097a4490708d6f6afc5'
        '3459a2249fab784eb9f2fed9f56e3a4ef198b77e9310c85ab811cc4558d1973d'
        'effb6ece1254ee24b0d365a38690e651f5fca75bc253bb71d2bed5767de5c670'
        '85a27e88af3bd6f9c0000000000000385dc001b0984003007900e83680c8d1b0'
        '115cda0ee873c05008420de78515b1501fff6def67258bf8968acfb0134a3276'
        '9777555dccc8fc0a7856cf7c28685679cb4f4358e2b2e56fda304632e547b2e2'
        'e8df6c57bfef4ca7352704dd50c7dfd9596d41397d2eb76ebbdbd7c93ed6faa8'
        'c6938f6e2ff7a9df0e2cb5ca0000000001c05dc001b0984003007900ee368088'
        '9260c008a40730c8140c16101b81eeebe44f7bcaeafb937e5957c01faa88485f'
        '0245eb9afe6df45859c2649626ca22810208c8b2a915208a4936b27fee35270b'
        '79b7cf6423b64449ef99e1d9d1d29f45d79d9c6f0874a463f843aa6c5e8694ac'
        '969fbbedc7e3d6daabf4fe6198000000000003805dc001b0984003007d00f836'
        '8dc3841088063400d8000041001f37a28d7bd68be6a5811e3f6bf0a4ffe47047'
        '27e0d2258b19e64a7d251da114bed35a7fb22daddd57932e84e33b361d50e4b4'
        'a96e9ad9656dd3891fe9d3dc464fa4a787671406885a3c2ac3715ec91b5e70a9'
        '72fb88aac58678369acd85a93ab8e7a69b657f767677685197805dc001b09840'
        '03007500ee3680c8b240c4065454146ca0608419a0f281c9918be4368f5780fa'
        'fc4ef1635e833cb12d50355bef9e2ad33acb926778ef294484c6b50454d29119'
        '81494d6d162a2b392ab61d555d195b441d363b5e31cdbea7e55097b875d779e4'
        '58132085bcf86d4879ff1b5eed10956954f75771880003805dc001b098400300'
        '7a00f8368083e2400c8450005002c00e2f3be109fb714b1a34b53f7cbdb3e830'
        'f33ac2b08eb1f4bc4364a779a916e58eb7aabced5adb1df646587fd936490e84'
        'a442da6e5fafa91d29f236debfa78a2b37f1f3015af8b0a07d6da40b29126159'
        'fb0e6f202d39e7f1242b055a7940482bbd5c41ba260187480a7f805dc001b098'
        '4003007801803683ee1bd8800000000000000000000000000000000000000000'
        '0000000000000000000000000000000000000000000000000000000000000000'
        '0000000000000000000000000000000000000000000000000000000000000000'
        '000000000000000000000000000000000000000000000000000000385dc001b0'
        '984003009a0100368228b1220606444081039ef9bc45ac30bf8166c0ee9387f8'
        '8d32d7af60181934d37e7744fca424242424242426e84ddbb7786c1b23b365b6'
        '4a3227394fd2965440c0c0c0d0a50c10c10c10a10c897225c897225c8a7429d0'
        'a7429d06d86d86d86d86d86c82c82c82c87c8a28a3e9f479e79eefd2844488a2'
        '2988f3ce7508d60d42e85de9812f318cfc1eb3dc11c5b0818e06a1b44143805d'
        'c001b098400300a000f8369a848119214242018143979f1c5bb365301082163b'
        'cfa86aaa4cf9af93292f0090e27a658187ab451a05ad2a62483b27970a4e4115'
        '11b916477ba252bd357a3c3a25a44b4a0799ca43b58d24162cb325eb05207abe'
        'd39f6a1e060898752c860c4a86681021e8aadc9228d22905e269577ee6c0f262'
        '06ae3819ffccac1cf80eb537b2d37aa8d12cd168e16aadd0f699e7e2b41abf1c'
        'cdbc79562d0183805dc001b098400300900108569928f152048c2101a4d2c6f6'
        '2b4a860d8417422840923c2093ebb54d6f9a4ed09a443e85b3a1af5398520b8e'
        'a566c40256c7de9e7ab895c6016d26274ea7ed3210a98b7b7da48b2d357e4d1b'
        'e168c615fd771395b865d31cdc86b641d748a169e0eaab25a880e8c5f3fb2229'
        'ef8040c24d4aa96c943e6b8e568aa3af6f9143162e6d3b7d2fd2183a40f90813'
        '075dc001b0984003009d014a9d8c58782c3ca8cc55a06b6d42b30419fc93f0fb'
        '01fcecffe87ffa375edc800891ab58363dd9664f1ee9db37ca7fd8d7f679d004'
        '0e69900d6c8eaf6843dfc54aeaf8df5df9ad6b8707800ca53e54b0880b37cb44'
        '07bfbb98d44238f0921b681916a4912ec9152f5c9816e740da96a96a5c167c5a'
        '444a21b12cc53a0aa38a43a3c6b0e4c05bf8021e2ba1a6765163a6f5f983c485'
        '076209ed3902e05dc001b0984003007a0126f68216402d8403010358682c1040'
        'cd679e703a73efc2e5519121c860e8517d0094e1e042ffcbe5b8ba2cb498f203'
        '1dd19652ee2ff51219f17149480c14276c15d2e97acf5f5e24531283089eb380'
        '90f31888e41c462bc8b8e44c0c04bd66c359915890a64f41430e9c345132671f'
        '4e4886c6a41500132dfc5dc001b0984003005c0130368216303d840f616181ca'
        '4bd00a508a9ab8986113e042e7310628fff3131848c081c00730b280000a2c52'
        'b914995a435c896448c4304940b8a9352d463252886c0fc17b7655da3bb8a621'
        '2bcacaf380c79fb5548c1004cd01385dc001b0984003005d012c368216303206'
        'c2c103585840802aec1a0c8f7a991761497244cb7011a53c35a739358ebadd71'
        '756bf1e4253d591aa8520006145333faf499664b1c1e584b90429159e8b1389c'
        '9c892de5bb144ca4ecdb53f756d2bd0f2d42bc8e6e5dc001b098400300490130'
        '368216203a06c2e10358404a103b9f3537d03e518bbc2e0b16a6c0f91a8cc5ca'
        '147a2ec73b8cf9ab82f7f976b45400000600905bbc358e7e8419d3a8c7a8d31d'
        'bed8cddc42e8a75dc001b0984003005e0132368236120819836151016c2c1049'
        'aef89cf0373911d5258b2fb94701034e3659ec1efd2f1158530026ebae2c96bd'
        '9350ecb000300074497ecf58e17fc04180d3a930627a2acb1c84002a6de02000'
        '000000000000000000000000001c5dc001b098400300790128368216303306c3'
        '0250812c2416101d4d5699eefa80428c45a7919530961e25942ab1e59796ea28'
        '2ab7b92df493c347a2cd0576945d2affb81427a6fd0a337129116027973ece49'
        '560000000019476fd096bfc0e0016aa4499bb22169df2efebec8bf6b33bc3cb8'
        'b8a3727c3a506e80cec10542620000e05dc001b0984003007901263682163035'
        '8882c102d8584085679f1cfd78790cd09912887d82868b8ed78a60c03312cb75'
        '341a2d37daa296b19db6a429a394474db668c6d08b50c00c2cd3419c924c4aa2'
        '7292c84e0a60123c02c1c96231201a1cb60f76eedb84717aa9b6fb129de0a000'
        '00000000000000000000000000000000001c5dc001b098400300790126368216'
        '331012c40160816c24180821de97addfa7b010d320306a80b4dc7ce614610fe6'
        '5d1cd4ae5503ecd2dc979d88596f5d2d19d658c5ced729c57249a000c0001465'
        '3c1d12addff6382816cc26a784fe23d3f5622853be3090210d6b1e0932f78300'
        '00000000000000000000000000000000000000705dc001b09840030079012836'
        '8216303588030102d8504086efadebdde4162a05f71da44f4fd0eea5c5014820'
        'e8c43eecc3fe9786e07b195d8ff71a11e6b09b0fc08d9271c000000640824f2c'
        'b157a0570c024616ca473aa715f35fc061c8f1fd56621a9f7870000000000000'
        '000000000000000000000000000000000000000000075dc001b0984003007901'
        '2a3681e21b12080b6261812c20140b840ebce7abf4f8a2c5a8856d333e459468'
        '128a7e2a70001e860030ecd20ced9272a9aa6bbce478a02c019ee500a7644f81'
        '039dd23b0302b1a16005a42d03eee62691e03b896529519acf55f6256d39a2fd'
        '0dc3efda4d7d6d7f0b9a02b7adb26f4000000000000000075dc001b098400300'
        '79012a368216220819836161016c2016101d5cfd7a7b102f010bde73ad0a09a5'
        'fff12cabd5a656e6dfc9a4acaeeef571cc2810921dccb32b7e16c5242681a20c'
        '31f457262d62ee258343d0ac2b155ceace4db3236759dfb3bbed7d3b2690f040'
        '0500870002bebde060000000000000000000000000000000001c5dc001b09840'
        '0300a101203681f0200d08c403b140d4204a140542064bc4beb9aa6954123a38'
        '2f995fd1a7a5c9580b36ec33e4ffd9710eba7974435895fd3c9c5611d65f25e9'
        '8e317761f48edb0b87d1fb9bbe5d44e2fb8a772f0a4bc8ccb3b08374a780a728'
        '4af7eb1bd0907cd4bbb0006e2ebdaf4d267482f45de9a0aee5c32af3eda4bbd5'
        'c6a15d602c2d3b57166d72152d159a984c3339c5880408c8531fe7d79cc2b489'
        'c5ad6ae05dc001b0984003007401203681ec2b1188096261812c20140c040ee4'
        '71afb3e2fe78e81c1d8833787ff6d1bf440e6f5b5e88b3ffbcd6035d725b0281'
        '5347bca35921f650ccca064361048a64273470be7fff609a105100000294cd93'
        '06b6ac69ed128b2ca0e9cd25f1bf99243fc1c6a4703dd4329c805cb49c011301'
        'f85dc001b0984003006d012836821630358a0405b0a0a02071f1df0fb3af6000'
        '544fbf6ddab402a336f0c80f028c92e26689b3cb2c5dbb8f887164510e425b60'
        '6fc65bd9cfc745c15125a40000059452ef7b32c4a4464e2178053c7b65053b98'
        'e2b711e441f8ea4892833a56919338ca3a9403c0268dc05dc001b098400300c7'
        '01143681f6502d918403b201cf3cf3c69995f18c0a82a490533149764119d148'
        'abcb4439766ad23e26e86beb079359b24146b0e2747bc53edf5b26bb24808ef5'
        '950bfd1bc32edca40219f47b89dfe5f0fbed832f425c2eb44ca3acfb4be8f6e7'
        '79c1d91a22aef12033210030eb9e307ff1fe5477ed301193d0ee88fb2e810cb3'
        '3f712b03f0bbfa01c7ec5dc3a03a6fae92e93a5a22ecd063d7b6eea5edacfdd9'
        '9a63e7ff39224423977badf3b1dcffcf0fca0a8a77f8cdf2cd837f69814f1a30'
        '01a58c216e38375dc001b098400300c601163681f651100ec8e201d900ebebc5'
        '3c7d6b732bdb2607b0526254638ac9c027371c459dcc861291160a3b8cf1aea5'
        '04281912f50443375cb113d9b27a93c246148b1227591209f67ed68619a7c051'
        'e58602c5a6a031b267d3fd15fd869ef6b5e79246b4f51afd1b62e6020714d5f2'
        '0e91dd7671e4d293978323e4b758b7c8ac5da72326f84b0f3cd7c79bf9e79569'
        'acf5b4988eba0c51b676178d6c4feefc16bbbe698506ec69d87f5e5ceccd38c4'
        'f865e15f4615fded575ea8d3b218109839a635243dc05dc001b0984003007f01'
        '223681ea2b0b080b62612840961612040e4c938d8cd5023bf373feb7b132e125'
        'e1d5bd5dab5ada8460eccb57555974f22132e249e26040661a41c4943f6d527f'
        'df69bbc9673657b8c7977f6016000516855db54cfd05037aad506a3455906a03'
        'afa7dab43200024c23d5db32900315644c9e9bfff2d1ff42e9337bcf81fe5dc0'
        '01b098400300730122368216303589842101886c2a14101d63638e6c061056ef'
        '4acff96a98899851243c3b2781150464fbd2644966d6c1f738d34447147be4ae'
        '808a8441d02197b4402dec140d4c5887dd93d656650173bd5d03b1b2df4719ad'
        'defe5f6f0ae00fd6f5dccbca700fac26e4fa55232ef62f1dd3805dc001b09840'
        '03007a01203681f640358d84035080604e103a44ebb16a0580e7cc15ee45f4fa'
        '8731908b49259a2800a6a82e4ed6dd63b448466e74972e0516e8deaa7e462108'
        '9ac3919f351615f6b0c13d5463b90c0000840ba4b8fb71643404b7a0035d820a'
        '486e5d5e5890988b6b59d50d355d3f44b7a6f2ae5ade14411701ab73c05dc001'
        'b0984003008801183681f6502d8cc40350806830203af2f38e3b54d000055560'
        '3ed788ca29adf31810e62af92ce7403a21869881140d8dcaad791f2373544bb8'
        'd325603d07212d418b6385a24ed461d079c7c14b490455f0d4bd79e1021a5ac8'
        'a5cf6a19bb06a4482e766565fb1abc9a3e008965eb6bbc3591801e3abec5ae87'
        '4ff5b9b572af18e320e9eaa684ae5dc001b0984003007901183681f636101503'
        '6160a080961610840e4f11e7b8eb7940006568a325d06aec8449842510d4cee9'
        '2dc91151cf9bfe89bfc880c4b284f9ab2aaf595c15eac8ad1655fc3a198287d8'
        '7b0634b533c85bea0e3c9cdb16ccba1c9311615a6a6c842ca59524aa7400ab7c'
        '39d6e02006e4a0f760fff3db380567c05dc001b0984003006f01163682163035'
        '8d0205b0c0810ba5ef7e6816066f362754da3802464f49c11cee27a03447b4a8'
        'e2109ab088864b100ea37347cac29acca49023a28badb2588eacc005c84c6fc0'
        '7585bcab567412c941191f4178133858894895b6f14a559618807b4f6c03a9ce'
        '9484486c665546e05dc001b09840030086010c3681864b181983613180c43618'
        '11840261028da87329e695e45c0fe33dbd1d777dec8921cf83a79d8b6a4f0104'
        'dc3455c7193cd8d22aa7b625e1a7f70d2399170f47f8005d61a061830eef8ff1'
        '1cac1a15d555ee31d5c37d29403a4c7988bc728ee813651935346b005397099d'
        '4919e8af1fe2ff797f0f16ee01e77b27e278746f3d15785dc001b0984003009a'
        '01043685286a19a2c2c1335858424020000afe1a2f47bfb672fc1eb8c9dfb558'
        'f483b9dac2137f8173b21faa49266ce8c47b133ed23a3b3f143b236881801833'
        'cd5e339680c6bc51c1e575ad90e8214b0dabf65d11c5b7d7cb3f651002a7da94'
        '69678a42851e2b6151054cc4a2891943e027666222ea8000833ced6920154e2e'
        '43dbd710a5812596a58f0ce38717e4544249b65704fa4ef62b1e5dc001b09840'
        '03008e01083689f43345858226b0b04ca040000fca3a59df1ba584cae163ab2d'
        '2d2293e80160218963900840490389119a6caa82cd374af32a94d669c019bd9f'
        'dd71e5d2aac86ac7800d6cb37fe158a50bebfbfa446db8112a00096c0034a003'
        '4a6192b9452b02130c59090831925d4c66000e3a5bcf19a5a5cdbe6fb3758028'
        'a40d3e0488410a0a46f0a4a56aa2562e0e5dc001b0984003008300fe36923411'
        '3c884ee11380c0873cca8dec3cb6e438a328e17c0d9bf383388ee4fd8d4a735b'
        '9d39edc4fdf3e7a926f2957ec6c4602adab9d0aa59d2cc7271fe77b2e76bd612'
        'edfdf2c4793a03ba0a847310ec0001bf1d0fe6ffc71d1cd596af34e1b9af673f'
        '332e5cb1bcb74002a63bb745cd505d44f623746f78a67ebf7922770b705dc001'
        'b0984003007e01103685ec31440508036088d04050fb801cf643e1e39e17e79e'
        '72df7803f87f07f007d3de000ccccd4e347776ff087f03cd63f843283f898bf8'
        'b49ed7159659241597a20a69d766fd914b850293527c5c401ddebc305ebc6664'
        '0fe314bc41e20197f8426002b2daacb0f695e971c107f347538aef752eb5fe55'
        '0a17b9c05dc001b0984003006900fa368084c17908500458001880001019df0a'
        'feeb825e86a20d51f9603f3a6cadf1ae25843b4367e0a46fe03bfc0d8863cb8e'
        'e0a847982adc47af4ee23db6f1445af866642177be663c39159d14fc2e51394d'
        '103ab050f1e5a560fb4724106fc9af65b94379f6c5c0ffffffffffffffffffff'
        '2807861bc3bb9518e01b8002c621ea0e04261ca82141e2292bafd923b9910d81'
        '3f8966c110228632c738ced89fdbbc418d92c15d78309f849da911786ab8e370'
        'e8c1ba4b0aead40024800b6214b2de93bf3bed8db795b24c6de74d8c5e162e22'
        'd581cabdc2247d2163b9552d20228011107f1d774d0c43c338cba57e46852c84'
        '7e32679fbbbdf44f8635d7f327df42bc25801105130d496bf9e9f0ff7cf9f4cf'
        '32279b127c09d51eee32ac175ebd70bef27e904bbc00248011056f3a861d8c98'
        '192d1da0ad9e73a227ba65e12525ee528cdda16e83907bc1e90c4a1f862ffa23'
        '8c45b4da7ea94d51089f759241c79816363a5fac6dff28732f768025800b9f83'
        'c6db3f148b3765a8dc1fe2af3e784df478e04d30115abab8d448c7947ebba9ad'
        '532180111de3e4446fa223147df1dbf3197b4e93950eb73b6c200645871c1954'
        'b7f37c208634a1b1f7433be8346293223535ff77826caae38c5d1b1f4034a2d7'
        '8d5b2380218634a5e6141c220828ae9398c007d9ab7400640996d0858a156851'
        'e07b149b5940248634c022b3711dcc1a4b3322ca95d4a42c6cf2b2f426ac18bc'
        'b068f6f4d54b47f9e75778268014157792e6091491f68c6985bfc1a024358766'
        'b6f9ad5d3d6b4d31434e6879b23ce2a34b8827803dd9beb73fa90330033965a7'
        '22d97e8d4866d87b372bcb7cf46b4fa61455851ea530965452802684bfbc7cb6'
        'a96b3fc5fad57b99ffd46b2f52e77685c9429135458b81fc17bdab4cdfeb137c'
        'c032b77b9fa84923501865bd1db6858d4a55b5f76f1a737ad06f6960ec825096'
        '1d2f5aab02e2b82a77f5cf1b053b503b2f24b4002eb517d7402b48edb7597027'
        'f988008171810d87fa7720365c49743c236acacbb3b0f75a6379eefcbe8eae89'
        '977b282db5d9ac303f1a5150d939452464e8054691fd806631d1310c55403d64'
        '97e4f874d5982604c226d7a84d305af2742fb7099101fecf86a8541afe880ed9'
        'e28e82662cea87a9e8895ec5ad615262b8da445ae0534df5b4fb4424f6aa7525'
        '802684e237a36de57f616cf6b2e700365d1a25a74d52795227d1adfee44e0912'
        '4fc156115c4765102684a6250775f815f1ba939ef444095aee114168e1666a73'
        '322d8b4830990d51b90c5c8f7c79082693c68f204d7ce4a3fe5b69e63962554c'
        '942ec3bb79a7caefb135891bae87c6c3156dc727fc882684c743db33c465a9e7'
        '6816f75101de5a83783d171793dbcb3f4c4214611dce97df016b170c001b8261'
        '0007041a858b705940d3004b0d623e2f174f1182144a3361c826806dc4a99dcc'
        'a258e04c4e92d7b5b2d0c0294531a24910de5f94ea7ffda2c13124c5e62fcc00'
        '269411e6e5f9e90d68a24c00d9942535c10ede89445dadd758d11c6ae29f173a'
        'e67457551d048024846ef764615e61197335c26cd73b41764b6a435cd100d676'
        'ca968996e9258b9a991cc40033b5a11a84ed336af59d0c6d50128ade2c801acd'
        '58730e0c89b865a64fff60a8d0b5fe0b4a9829a3b90e3b9627ecf7fdc877867f'
        '2fafbec7e700ca1cd085f8c497a82cc0c1d8431303a44b6126ff5be151332c36'
        'c11d79fa519e0679d3c830c5a561f6402fa640f1beb187c4b588ac85e8778bbc'
        '5694ee79ca23f5521db9c134afde6ee32ca32ca439783868a35b2c857634d190'
        '26a3b69eb6772d756f857896e162cf32b3a17c64eecabc2408dd70c996cceece'
        '31353c88bdc05e27a291ad8ffac8423f44d499082fdba95918630dd9d44b6cda'
        'c1acc6de15b4f716ec0a95301b6de02ca294f0189e908a53ab67d7fd67cec0c7'
        '4ca6bc091308ace9d888c6c1f909713d1d0416b68afaa7147fd336a030af5b91'
        '299604d87d1290bd789b2d9041e38ed916e939de64b25650e8762acf1a05d7d9'
        'd817bd7774419b36afde1413602fb64982522612bb6c8731b28181885853edc7'
        '6667d83ad35526af32ed7ea186f44a974e99563d8057750bf7c189a37c2eb9e2'
        'd616be2eb813868eaad7716b43f527e400af794f74b36c4341f3ee938a2b0d18'
        'd95320666a275109fc6890c030ba713f10335900b9b31bca64f8dc26026701cf'
        'b80343c62a7cef27af9a82c9bc99050e4ab1372bdfb8fc90b9c6f2fa7030bb56'
        '09a9afbf5f7750cdb3240d4d246a5b7b65ec66648dfce3b884a306db24045132'
        '333afde7fa50ff5e3b322bd17b0030b852636cc2eb036d0445bb351c2ccfbdbb'
        'faeddc069ef1c8d438d1bee9a99ea4fe7cfe92359d1254e55e324e90ae068024'
        'a8cbc5ca9317b58c4ad48f77d92a4b4e2351b734b08500e991ef7b9b7843e9d6'
        '19c735af2dbe1c6276e0d13eb98b1d27e9b0fba602d6a5500b56f45258314128'
        '61009cdfce743361499d7008102e5ccd5bbc268475997ebf37087b4c37969e92'
        'f005989020ed02918602b7ca16cf70e92a399c17c9d3576e302382bb000c7402'
        '482aac6211e17c5d3305c88e061f45aa96c080f2880304f8b29537f540258067'
        '66c22989db98caf10d94f0dbcb1d9ebd695dc31f017ca18bdfda06b54946b1d7'
        '4f65e82484b3725e4607b15effe8cf100d9b6f5cf8fd7bbfa7a84cbcf571d331'
        'a67efcbc5ef28ace268493fc7efaa7633ca459414a8718185391fa7ff503d51a'
        '6a84f11a7910db969d2c104966c40027849af64ca872921419157f27cab78fe7'
        '2dc174a299b48b8e3db97df3eea2e675082a54543e170036b527f137e0a6ff83'
        '5146494eac4d4d6b1c3e2d07776e4ffb0b00e12e5e3daae75b860bafc00f4142'
        'e86f5be98f181923def2fbc5dbf82fb60a8fcc201f21bd8d1ef1c3373f23cc43'
        '3f9e3c9316cead37f1c44abefc88abaa941d9fc9e2d36251efa1aedaa7d82cb6'
        'c12021944d98c1423fdc43393c957a1d041369fb7ceef03e6198bec64e6fdafe'
        '823f499bf0c68899a694c02bb60a387232ca013357821eafe7030695b2b66f5c'
        '49b1fa1f39c567b94726e9029af4a7d1e47c0b97aa5dc02eb5d7fda9b4d7768a'
        'ec693ef16dbb654292a7329014dbcf1b7a99a24fbb08cb0e01a479d64a976f9c'
        '4f681a9c40f02cb5b1b269e56768a0e151e06c10eb3ab6de45252e602cedd80f'
        'd0bda3cefbc90c9fab3133ae1d197004e1d6002db296e3bba74a00e8161a52af'
        '00337a9e52dd7ffb2c5be06a4bd1217b2820112f79587700cc35547703fecaef'
        '0c2eb43cb5f888f3179c4746328b2f0baa557af923524b0639793819b5a85950'
        '9f32a9d56fbca2af5cd66173e8257e402aae7a6c627f3c9055d843ea67d63b74'
        '833ab2c3573d76efa2cbb82bf8d383e2141d7be51e09d6d22ae20030ac314161'
        '2053b63aebad52b065db24b1664ff23c4e902c7c05b1ae7a8b9e0c94de25ca46'
        '0576305b1063bd066f0f077825811ad4f55e3ecc96335aecb660c3153dede984'
        '3dba69acdd7a84dd9cb90e5e8ea6ebdf40402580603ab89af6cbbadc75680c29'
        '2d5ac4339195025b6fd1855fc022050d5cb88ced6f5bd5162582b594ee74f0c3'
        'dc399bd2153e361bd144192eac1641c0da656d68736a32f805aa478885f81a82'
        '3400083da2c3e241f74ae347335b6b233b27a2e0d51ddedd58268065de573a35'
        '330add8dc54c49644f05347d192482267c6f9b510603e668ee603d60daa8e128'
        '26869fb4b875864d72556aadfffe7b2943a7e6335ef3096ae1a0883cba249198'
        'c9b53bfd8dfd4025800f9efa2c9e71512382ebf4d5ea785a3d4172d197e6dfba'
        '16728064fda4723b62de15f3bc258633cdb15667217153f9c38a038788dd3bf9'
        'fbfe0cd3748762106178c54b9c0db41cb2210c24801186239cdf8af5f3c30f44'
        '9dd535eb5ceffebe04f472c350c57aced5f04d1fe172cbd023862a21a1a741af'
        'b5f840a50fbf8cb29d9b76150fd65397e940919ef4762135f4634780238623db'
        '63a441e80192c68cd6c5c5d025987345dd0e95d7c756e2f36999b41f0402f612'
        '228629f21153709a633cfbabe41cac5ab1f92f79acb5f8aec51cc2b84881a102'
        'ebf0a025862319140f16cc8f598e6bfc0f4ea1bf86d302ed32e32de2b24d65e0'
        '84587e9747a6edc39624862232e22d209ecc7eb5599bdb66fae6ec8e06e73bce'
        'a1f680fa3f1ac7460de738a12ec023862232e3655c97bfa70d51a28029232ec4'
        'be9aea4f1d0d656a73c856ce7d957358c4122286354657af61358312ae63f4fd'
        '0e5d345f30d35f6a97f61a142a69ba03cb0c5b6bdc25862235fc396fa9c6d187'
        'e4b0f8cc3beb014e43ced507ee697f0fc099211f203f7f691ce2582286226131'
        '73ad8170dd94902e0302ef21ace9e52bb53215145ee6d2d4e6e1cad959c02780'
        '0b17213179a58e1a5fd66528ea61e8a91aa61ac0f190715f37825e72ffebf4ec'
        'ec02d81a2180218621db35dfd3dbd7452aeb5085932be41676e1868b6003801b'
        'de205232f2ef3570ffffffffffffffff2807861bc3bb9518e01b8002c6241695'
        '0ed469ca423d0211b9670f2b4599c05f0cff25f8c02480110f5e1fb6f36a56fd'
        '0a111e16ae60240ed65fb20c32f895955272bc5dfdfbea31d000238635f6b76d'
        'a13ec66e413937bd34abe2cf614070fdf620670414d51d4a70e93466fa802380'
        '1180a19bfba356b8a970235a52b0b10d1310ff9c3f5e61ec161a588184b1063c'
        '8a7022864298c0b6d00409571b5e6d36d4ccdc8ccc324076a336b845c007206f'
        '6706e2580024801c31be1e4d7e7125f894b9a71ca3ae8a6e6983371846d3c90e'
        '4c2b983ad97800deca00218623d75b103efa0b835ea146f04dd450a6d3fd3b0e'
        'd8a8c99750debcab359825742580f6dfdf93a0abdc551062c49e631e021aac1e'
        '7befa5f378bedc8174b61884b1f0209d01d333b9ac53c3fe9d8e4efde097cfc4'
        'b8716ab5ac84b7d24a77ba09a03b5302cfa2bccd087d1ccb73f94eea773da38d'
        '04f85c83a5802fb856e2d84c601bffc47e794ce480880cf16a66959ece432b63'
        'b46ef94d16bd2520b322ab4c4c2549216db4169838802eb4af02f1b65f2d1722'
        '5451a7560aba8eb696b1f28943605fec67b55daf03c397edb90ac7a9594aa537'
        '1ad47f8c002ea35fbbf2a74eae60639437e6618e7a7fdc5240ad47127f10869f'
        'f1edaaf7e38fa7e08d93258e098c10b20c4b81002e9ce4f1c3c613463988543e'
        '07edaae336682838fec119cee68490af2ca2a2a1460870313270a30a3a30bc5f'
        '719f302ebdb1f44f79e762ac14d3bfd6deaf6bae1e91b1924d8549da3ae17c84'
        '5ea21ff7bfd6d518b3928e5ee7c3a522dc202bb73bb5935fcf5ea9c3dae6a45b'
        '54dd4dbd8771f68a669c09540a66e4d24393db912fe192614e03f36280c52eb8'
        'fd8c35c34f49aa724091b1fcb213e9c833fdbfbbab872e6278a7827b19fbb0e3'
        '2c7c67b0afb8c1b0524be3b8882cb8fc241bf76e45e87a051233fc7ded2de113'
        '407c2cd0b050d76db0819a0fee6070c307d6c1555415d35f4ba62ab9e0bfd8d9'
        '3f93fdb0c8004aabe98fb58b6577243a10cc7f685c7a4d4640a0e99a37cb5417'
        '55b76823002bb887838de23079b5602e76a409c5cacaf0e20386e45f488750ca'
        'f617b8f909921840fdfb540321478f03402db886d438c413d7ba65e36bb63088'
        '5cc427cde35ec992290138d06fa3885ef75360a8058a2e44ec797d8639b27c29'
        'af96c00dfaeef620e025cc11f8d28b85fc5938e34e7cab68b5a0b0064d6f5220'
        '82d77ca425926346002bbe3739a5e74d1aaf9b45f2ba0d27327bf926ed0cae4a'
        '60f3ab8133a205cadd7a43d8ed5ab33a112f1034e623ade400238f286233ed3c'
        'a5c84b29529abca1b01fa48d39650f0a9f71dd645c3aea84d02580f909e5f9b7'
        '53a1dd7838a10c26dd1ac690b452ebc1f9682562dfdfcf5863714bfb7e9a4025'
        '805697b0fabc7e8f7defdfcfb062591f43b7b58b10f01a82161a7c7284e3b302'
        '37d2f6918023846ddd3930aaa37152cc8a59bd0fecb230f2a103d55ddf4df4eb'
        'e5712f6e8361e8da402484395ff2f882425eadb4fcbc032466919889a083b9c5'
        '51b2565ed3670fd9692c4ae017e036af62765528fcf8cde86401c59e83d02fb9'
        '548b914b34776dadc6543bbd330ad9ebc5f085059f7076bd5654939bf61e485d'
        '8539521b082db3d649eb1aeb86f60a34b8f4ef22980006c4d40021493140e443'
        '3c48b008165e688975e71e32439abb981c8cf829b3d57e1c825cddd63db35111'
        'b91f2192e2afcdbadd4db31a32ccb19f9744ad2b488b427b93459a11202db405'
        '33b101fa91f66ee1433210654d6a197e3c2ad3dd65f7db51f8227b2fcef20219'
        'f5dfb56ecf1e4ba60e607828b50326059f20d5ac77f69626c9c14ac44591a640'
        'c21f8b1c046d341af4174dfb63193c6b23f7c2802eb692b278178e5438723a18'
        '7529129cdf58661a441dd9441f70475bca41b06e45d044b3be1b3c30032d8572'
        'e92edc31b103a160dce0deea31c41bf42ae64c6210983dccd896cbb23116275d'
        'ce5a2dcad07e44439478642d8ecfb21f94082b40402dbeeb42ccae6c6ee905b3'
        '79cd84dbbc88922e74292d8d7ea7baf9c874a6e2867eec0bbc8e058c1249f3d8'
        '6b07902583accf724f55c4308d81622a75a3d09f8b9e7928450903d9bc6db2a4'
        'c52ae3579d9b431e402282620e1350874d7cd575391e5c57adb61007e84be569'
        '46c1e80f418807a53484cb00248712c5f87f627f0d2527d1361535dc0d6ed954'
        'e3c387b8e5745ad77c093a839cfacfdd4025804f0cfec6e94006b7b9c510686f'
        'e25d4dfbf741063f421ddd9c2b2658a16e7d3ed9bd90e8248034a82fff1b6e9f'
        'e6b867cc4117923ad3c84eb3d79d4c2102f2095f62d337b9fd5fd60a258078ba'
        'f1b6706b9aeb5b8073d68e608d4c545827120b4154bde88666d2570232866938'
        'b78032b93fff0a55725ae0994521e832a6efebb06ebbad64e45400a58bbf47f6'
        '2a43ae6fcc32afbb0b64693eae8fef61ac80a526582db875bc23fac0d0039703'
        'c15afd31e195d93c7a3a4b627a05f12283fc40ebd103b61ea003b55bb0535e31'
        '3780222db7f4e3aea54c9a3b8de306844b312bb3c8c1441abebc7011587521f0'
        '8d926511f71f49797cec69dbe5ad6a4be22cb4f35776ba48d70def07062b10e9'
        'ec7d47a16e9a7ce85b9f7329bfd3d1de9fa9203136e5c5c8289fe64012002ab5'
        '22fc4958fc41454b003b126c0d211c13d7cd6eecf9782770b5e7b1b66c57a444'
        '487c19cd04c5525f4027b51f4a805e8fe568dfeb494eac6cb4d96b57d114a52d'
        '77252ece742d460e5c2652600e4f3d55982db51806d9011725ec50db147cbbca'
        'd8a1757ee4dace669c9a3ad874f634f2c51dc1e5895b5dea4c95b13e77e8102a'
        'baa223f7dbb2b16730df170313989f365f02f9d4130913c908eba9ad0af0b167'
        'dd492e926018347676d81f84e900185e0e5f1daf28014fa1c6be1f41a207c518'
        '39bc2fef137d2cd752f029a1fe710f6e4298ca298ba637719fc7a957e94e792d'
        '8ca26845a5bfd883933a894b350b2589c073462534ba56f5bed85991fc1667b5'
        'a231da118fa3982630ae634abe1f49360cfde37861d09c013a7ae1cb3d2adae3'
        'ab4b82b09b354fcc002cb77babaf9b46b6f6101ba5c817768897871847e8d2b6'
        '98d9da2bfaf27521032d3322bf50f9d1e607c99631302eb5369cb9778d9db635'
        'bce9de6daf543ec6bf4b18e18d9221fce3279a7c950b5740d44a6eb10f1a1b12'
        'f7944ada002eafaa5ca23826c6b65cc8864e6339b796114b5e90a1f8a91874e3'
        '084b1daa85d7102992f13b166f6c694a7e00078828a50b52cb233247ea0890ce'
        'fe9a00f594dd78d3e21d8a49d4827ce574a733ccd9c6f6e8e6544fca4029a522'
        '6ba506b6e9ae98009c2396b0b42e471f18ec08e20dc8b007eecd2e4485168653'
        '97b9906a2773002ca6ceb69d779f1ff8a4f81050ce4c6c63010e7c69c8461959'
        '603be92688383d445e61f58e63c98b5635cce2a42ead993493ceec56a4e1d732'
        '547c23769af84aa7d014a7dcf2ee10f0fcf40f44d6652eaefebc3f0be0900d74'
        '5feb50238476adbd21581be6bf19e84478805729b1c37591ef67b2926ff5091c'
        '5391959d075ca02493ec9c1993dd85d8868db613898b87ce1b565d403b6e45fa'
        '3464eb38b2f6dcfd216e7680249324c93c6f3456846a7de4a3e74e302f9f4f74'
        '371b8357da89f847c03bd0c5fa44082e8822846c82848f238d9e8e70178f196f'
        'fb87751e7958b631412450f751eb1be527e9d18024846b7e741768ea961de766'
        'a2dae4861769f13fa54b67f8f6573cf1f91415c6311244e24435af341eae93ef'
        '3321509c5b2b9b7154a574658d0508ca204cfa595e4ceb31d1e8be48ce758249'
        'c02586775887c90e21b1f6911971802db070a519703f34ab6317a722a91bd60b'
        '9d9464031036d473f71d8184b8ea43047d0f540e24df99006eebe040bc28b05b'
        'a1165990b48c244e03210e3dc3c67e1487832f94893bdd553c12e5bb0e588cb3'
        '05dedacb9c802eae62b559efc017bb60df9a2c0dce458c3d9edad91eb8907487'
        '2c6525be8ae9dc861d377dbe6e5cac00599c05f45e2bad0b2578d2ef9faed89d'
        '1cebafa80bac2aba5e381c67cab97cd3b2f4f66a6c324d733fced9734e09a482'
        '202eac7422c62f87889dd166ca2d7feae769685104c5311ac7aea1441f4a86d7'
        'be41a48aeefe35eaed9e39da8f340b802caafee7f1ea1bc34c43abd2a54ae19e'
        '23122c5c3282f3e29ea02d49c93567d6cb79b6f9d8a48af94e735931582ea9a5'
        'a57656ca8eb444b727dbde6a81265960fc1af6fc0191404a4894f61ed8521aba'
        '8a8a83b8c623f44cf44ecf402ba75aaff338e23a9d8ae571ef243b5a64748829'
        'a015615c459fecff7f3197e85c5d309d91ae826a8eb0cdf02ca66f8a482fa4df'
        '6c0a7f32dcabc1f1063552b7c7c98303113df4b5ceaef7ae704f58dd0601ad0e'
        'a94ba1f3602ea6481515ea56bf57ad70af68dc8dc5ec227474976c252a3a0501'
        'ac7cdb4f3c417f78eaa49a89e58e81983e054bbf2faa3e30487dbea386dbcc94'
        'b55ae7802a7f8f4e1058b53d3ca7be92f75e8a62f957f726332aad8c3965dee1'
        'b4ff744037a217ad80e9e2d015b5a75cd0087bcb0a599c94487452d91561cad6'
        '72c33ff6ba714946f5390dc4febe5439f19b75531eefa885199dbfb02481b764'
        '4ae2ffe952464740b37c4aaefaa8fd9cda5c4bbef0dc256841105a8a219c311f'
        'e125883484cbcf2c844ef708273c69841ce449e7d7c8d717d07f3b4db916ddb9'
        'c5f1717e69c0d02386cd3a10425bd31469240af35524a899259a74ffdeff1098'
        'f907edfa2210e7043f31c82386abda13b0f1b44548d971131f43c6d886cc1859'
        '83cd067f5571348cede7776a964af02486a5e9075e83d0b0a7c04632cab83777'
        'ecf1381828db1f2d4eb2cb66ed8f3368fd00660025869190c0e13571d016dfe5'
        '2fd858db376227c3891ce30830d966f5c2489dc95539dd66bcb824868174e784'
        '259134877f3fe141a641d34ad94f83fce67241056fc06a1b43a00fe2dd45301b'
        '801b63c9c624dd0d5a74430c3db4ccb94e93eedac05ea918a6b5d00986068c7c'
        '5da99111ca07860691cab8658006800221722d4e06800221722d4e0680022172'
        '2d4effffffffffffffffffffffffffff2807861bc3bb9518e01b8002bf5231d6'
        'dd1e6c3c7fbb6ecb15a49302192ff5309b673882b6248010c69a20a489446539'
        '2d21550816d065545ca99c4dccc6cab952f49b213c3f98c67060238015a359e5'
        'c27fbfa0f8bb9d1f4b9b5b0aaea34af8c24bb1111750c135a0b525d2bc002086'
        '33c72a26a1e0d4032e3ec0182d4f9ed1e3fe26761632e51b559ce9f2990e2020'
        '8633c30b2dd785dddf3c6836e95eb46543a8863fc035d91f7ae51c21a523de1c'
        '238010c9a33a269ef64a0a2b5f494e7d2c58edfdc97fe52bbbc6513f9446f4fe'
        '332932001f801d8d4e9bdfaca9539a5724ed3d9c64bc27872d60d02c68a89bde'
        '158416602286513f45c576c3f270e3c333815e2acead511dad91be14f6996507'
        'de8c666ee1ff98208633d33aa26aed93e4889692ce371517b27a39efb44faed8'
        '05ede365e3cb7ca021865ad61f47a0c2beb0c3b589f2fdeb80ec7306ff56f19c'
        '083115922e48787bda0020801d101548eb7ac95a7b65b1f113a5910a68d2437b'
        'd6db5a6f4440fa90099b7820865b1f1a954b587bcebe5f291977e79899e1e9cc'
        '76647d5310228ca720c75e6c1d8633beed3db754ed21c496a85b574959fb15e4'
        'f9dc3c51462850512a4022863a1a405dc365a1a91235bee24025e8beb3e3aa67'
        '5866a4c5e0c6f2d07b6994d8fe2480b6f80f82f1dc64b22dd7bcc6bdc37d8ce5'
        '89c8558ef76f59d2588234d0789a8b687300248057594a5edd8bdc0160a3b4b9'
        'cd68007415807e5b0df35901d34a71133424b3c9193f6033a97339884a0fb58f'
        '567822615e3f8325d1f22abb7fc96797ba9969a56e2f6cbe56f4db81372899d8'
        'cf2b8786e0fa5a2ac114002ba680dfc092b6be2cf012d8d36bedef486da96105'
        'fb7dcd813680bfd92e1a3a13a3d16ed48e9fa084f8e7002db1727ebd8e04a3e0'
        'f8a7f69d721b44e076379d3a223b49e7b4520fa2141842887a2d2865df6dacc5'
        '232547f7b42da9e69f17539c90e9e8277344ce65183449f2f345f20d5d5b11cb'
        '7b24d6ae407adcca1e5bf8b668a2a1e5487b982bb15f04636f4e89459bdc0000'
        '2b625aa8576244ed18b4e0c0c784ebdf4575cc6a22f0da7d1401778186828024'
        'a98096cec46c02cb0c7b9f3d2bd907a42b2027db55aaf2b4e8e154b393a336f3'
        '3608bee02aa97c77dce84111b3e078a009ffbc2ebeca1b08853f5b27bd09bcac'
        'a926d2a254bf8ee65a603d260b9ed82ca97612c8ae83b367222365916e4ae879'
        '3ed76c4833a68bc09c9f49121d915df097baf19e74da8afbd12599802daf3a16'
        'fe8fb64ca23efb0ccec121e62bf1eac55429bb8dd8d849d89886a42911dc1b5f'
        'ff32bf1209d908c469d02cb1f0fc192d2e6b0b7535d168861f1da23ed90bc056'
        '650134426e76a5a058e6aaf6ffb94dd10959bbb518f3402db2b016418aa8008a'
        '2f518db30ded06a8398478d31f83ff08d6a382dfc8b98c0ee761bb4bc246d038'
        'e5e6f274f434b7a9072b27943e494d4c8bde320057ee4da123b85e0d3787cf69'
        '7bc24b98d0f8ab7b5c819d2b9e9cc55c1ae3c32ac0f8b2f414d027b50402f73c'
        '306ccad65f4e35eb6086e4d6b72499572faa84dd616a75f291b0892279db7360'
        'b4402ab3d3b843cf3b0b62500f2f6c9bb27ce32e81f2f591e04c7ef0d6a5f707'
        '14e137e14a5f50ed1e6975da602db5031f4d80bb3935847f767fc727587da017'
        '64dc3df84010560fd0fe4bf62b3bc43ca0ad6d348a3f43b12e48202ab070998b'
        '902955c0c0cadcf99960592a9408595033ecdd6a986523c48751e6c12522780b'
        '498791364fb02ab3564682cf9927821b4068ea1c73b4710a0e5fbc1c775cc445'
        '3eaa226f88b74c2f142b53cf8abcce80122ca51fcaa65afde113534edec6b0bb'
        '53d555ff5f981871b9365ce64516fa363e35045031579f6a6eb102d367002da5'
        '89aafd931112cd4593cbfd3f3c8c019c6df3a7a0ee3a029174974811ce035020'
        '1c5d4c29268792c77150e7c02ca74f691cb86c67daf3cfdabaf3388e3db0af5d'
        '582a5e3a3d29678235e9168ff92aba68196e3ca5e6f7cf938028a653a703a41a'
        'a72085c115a7f1e4b36693ce15bb25311f52b8590152b100b638a18c3082c09f'
        '2fe42ca627ce3e10c03604ffb775841c1e1bdf516c41639f8d0bdc49d7ebbb14'
        'f1989d71bfce2f86a759735f154f862ba4fa46f04ecc1ee94ef79a7e970dd5b9'
        '91f96a4f04b3b36e155ad74c2fe0b490ebe6a958b9619d8a36d1402ca3d81f95'
        'b38e8c43534f3f73c26bb456a803a24989f83d1273053f18832b5ce7d820ea3c'
        'bad4c7a187d585d82dadaee5c2eca0727e2060e08d0dd5cf27433cd10b7a13c2'
        'f790298a1fa8468256e5e6b60edd82402b015a9a90002cb9a75ac9c1d72faad5'
        '0aaa8252273f2292d36605322b7bf12da2d9c0b117d170965b5ed11b80f28ee8'
        '63b9c82dbbed387bf96d97cc33ab40628c9cbbca20edad03dfb2987662b0b788'
        '36a66ac4771e29e8fb0b5f2a3b371c49782cbbebd3e52cde787fc0c6876a4665'
        'b2b7617afe29b1b037c269b0669a1a38f78022a85bccdd58b36d50e9ce002bbb'
        '18daaac8fb2c1f2f96d5e51e979ca34169630a4b62c63b7a55c1bf279fae52da'
        'f88f31b914bb1894cb202cb9a1e96bfccbea9f5072461e80c91ca5e2ac5a3695'
        '2e61e50f4c351e1fa79466b3b7d549920c513b257c77da2cb949cab8f48c83ed'
        'acaa5c2a93b6107545fc12a32937e1ff54bcb4be51f7f240be3813778fcf4236'
        'efcd950a2db5b18ee680f0b9708b932e53881f4704329b73f35526096f4d560b'
        '3647657a6af93ff9bde7276d33a2f33738922ab32502c0927ba0b8fe612bd0e3'
        '42dab663bf99f3c294febd54ba46f2085e33441372482b29dc3a6a55142bb18e'
        '18367f6d4cc1dabd24e5b0f094463c8257aadbb25dddcf44b53b0fa1049561ea'
        'aeb71398ebe83c0e402cae0495bd9af33a369de688abccd2c4b6da8916677210'
        'f60d9fca8d4c49c84d3e421d8c6cdfc5501f67b205cc2ca995aec2cd6d2957de'
        '29b05b15b9a59ac2b2e9e34a44208743286f39bd35996097a70485bfd4ed221e'
        '6f02302ca7cd6d756d7171c0339c9ebbafc22c66734b6619db5ac28b494f30e0'
        '6511f1cfbcda3b948bb0792b59039ca036a869740f8df4b5eaf04f937cf67550'
        '4c9425ad776d4bf766f1ed9684b7e0e5ff12191680bcb2256de96020d98c3933'
        '43916b2f1d41dc29adf6b047e0b9927deaf870c8af68ece86dacfd4d0e1b0138'
        '6ce3cca57516ab6a1194b7cead56f91aac2c9be5a0815f29bc9f3b0658d3aa10'
        '0f53f347ac43f4a5a2b5124d642fdf833f495ed17949d0a426ddaf9704802487'
        '4a1505d5b52980f742880976a4f8081c6078b6b6af5325f36578d7d1ec46fafc'
        '0e62d72282b5f40c12f286d13ff54e3f5d224f085977b7741ca815424297ec0c'
        '65610ecfd9282291c94e08d8c87f4543278afad22be232b5d2a9a47f09c08ed5'
        'aad4ec4b2c2c6799b2228b2ac17fdff16f31cafb9ed957120fc417290efed0cc'
        '96901e5bc09ff0758516c340238058e84bd8eb79cf4d1ac47eb2644c07beedfe'
        'b0c417cdb4b971f87b802035289485202486825a8f31908f305c6f03e3aaa615'
        '3906be1b99d2e1946acfd67fa1f83bab9a81dbf00020865ae45a5735c9321d31'
        '2272bedf9dd096c302566a23b032d99e1fb81a2fc20021864e8d07f7aadc7664'
        '5d286eb47d925b45cbcbce00c62f6e025276c6071e3b32b0248020f02ef6ad1e'
        '29f37101238dcf7f7273cb59c0dcc65fd6fd443761f6dc412a1d7c6680228027'
        '47b02ee412bc3c6d827393bbb36db683df0050dd1efee3e5f83d6d0c3e66e4a0'
        '24865b17472714056c4576d96812e53f1e6c7a7455ba351ebf7e52283e4fda1e'
        '14fe4adf4823801a578a80add6d685d33c081f31df2cc9b6b112fe46d6803473'
        '6456624a6f5e6cea702186324fe75f1b5dc599f21a8479968811aa805eac5499'
        '051a8f93eb85a45264be00248013847afd7551064a4e0cb8ba46e822338ae861'
        '65684c4154f4741f3931cf4317583a802486527c761b1f011f475e1c41b28910'
        'c5beb8a4a95a63e30c179d28194d23a94fc8ebefc023862a244c1654fc5d4449'
        '3e781c136671560815272f9051814fed879a1a8f0e8d0fd2cc23800b10022661'
        '96fcba68b0a6bf675c96e1a595804e5333c99ce316a24962f3e4962a28218634'
        '9a33b6002f2370835bf0a4b7580efdb1b3976e3c60cefa99d8a7528dc42808ff'
    ),
)

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
# (0xF27F40..0xF27F80,P11→主核,含堆信息 4 字节)除堆描述符外保持不映射:本前缀
# 固件不写 P2M,但会读 0xf27f4a..4d 堆描述符——base 层把该 4 字节按 readonly
# 区(种子=静态推导候选 P11_HEAP_INFO_DERIVED)挂载,条件场景仍走
# p11_heap_info_bytes 显式 fixture(见 irq_case p11_power_init 块);若后续前缀
# 出现 P2M 写,再同语义扩窗。
P11_M2P_MESSAGE_WINDOW=(0xF27F80,0xF27FE0,'p11_m2p_message_area')

# 路JLEQ：向量表顶(0x1a0000)以上固件静态上层缓冲窗(scoped harness region)。
# 证据：EQ 初始化 0x6072b24 的第二 memset 写 [实例+4]=0x1a0600(0x6072b94 装载,
# 29,215,328 停点 0x4244=ROM memset 字填充体首写)——0x1a0600 不是堆分配(堆
# 描述符 0x12953c 钉死 [0x134500,0x19fb80))，是 app 链接器静态放置：.data 描述
# 符模板 0x60c373c=0x001a0600、+0x60c3744=0x280(恰 640=EQ 缓冲尺寸)静态钉死。
# 全 app 4 字节对齐扫描：上层静态放置共 6 处 {0x1a0600(EQ,0x60c373c)/0x1a073e
# (0x60b1ad4)/0x1a0940(JL_SRC 描述符表 0x60a29e8)/0x1a0dc0(0x60a29cc)/
# 0x1a1104(jlstream 域 0x607a440)/0x1a13c0(.data 表 0x60be7d0)}，跨度
# [0x1a0600,0x1a13c0]；窗口取 [0x1a0600,0x1a4000)(已观测静态放置+余量至下一
# 16KB 边界，覆盖后续 SRC/jlstream 静态缓冲免反复扩窗)。语义=固件自有静态
# RAM(普通存储：写真生效、读回最后值、未写=uninitialized)，随 jl_eq_init_
# writes 开关挂载(P11_COMMON_CODE_WINDOW 随 p11_power_init 同款先例)——
# 硅上该区间全量是否 RAM 未测量，不是全芯片 RAM 声明；窗口外(含
# [0x1a0000,0x1a0600) 头部与 0x1a4000 起)保持 unmapped 诚实停。
APP_UPPER_STATIC_BUFFERS=(0x1a0600,0x1a4000,'app_upper_static_buffers')

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

# JL_GPCNT->NUM(0xfd1104,__RO 计数值)的具名探路值(路 GPCNUM,
# derived-not-measured;三选一 (a) 标称闭环,LRCT_TRIM_RESULT_PROBE 同款)。
# 推导链(2026-11-17 路反汇编+停点世界运行态观察 R3=1,codex 参谋采纳):
# ①消费面(任务 0x6069538 反汇编钉死):0x6069558 先读分子门字节
# b[0x1265C0+692]=b[0x126874](与 LRCT trim 0x6067532 读的是同一设备结构同
# 一字节,停点世界寄存器实测 R3=1——门开着),0x606955c 读
# NUM(全 app+ROM 唯一读者,无直接 li 经 [基址+4],ROM 零访问),0x606956e
# 分子 491,520,000 → 0x606957a `r2=r3/r2` 无零检查直接除 → ×1000 → 20 槽
# 滑动平均(首次 ring[0]==0 → 全 20 槽填商,平均=商)→ [0x12A3D0] → 消费者
# 0x60675e4/0x6067668 的 4476·Hz/160000 与 7188·Hz/160000 漂移系数(P11
# 邮箱+P33 低功耗配置;验证臂内纯数据面,商 32768000 与 20 项和 655360000
# 均在 u32 内)。
# ②换算常数恒等式:491,520,000 = 32768×15000 = 7,680,000×64 = 240,000×2048
# (与 LRCT 分子 7.68e9=7.68 MHz×1000 同族;硬件门/采样布线 BR28 无 SDK
# 参考——官方 GPCNT 文档(wl82)写门=32×2^GTS,该口径下等效参考=
# 491.52e6/2048=240 kHz,7.68 MHz×2^6 是多种分解之一,不构成硬件声称;
# 唯固件侧恒等式 NUM=491520000/f 可考)。
# ③标称频率=32768 Hz 睡眠钟(**assumed,非推导**——codex 参谋降档:LRCT
# 先例里固件自己在别处写 200000 标称,本处无固件侧标称常数可引):BT/RTC
# 生态标准;测量任务由 W4[4] VLVD/低功耗域 init 注册(sys_timer 115 tick≈
# 1.15s 周期)、消费面=P11/低功耗漂移补偿=慢漂移钟校准形态。已记录的候选
# (均不排他):32000 Hz(NUM=15360,无生态先例)、160000 Hz(NUM=3072,
# 消费者系数 4476/7188 在该频率下整数精确——codex 复核发现);200 kHz LRC
# 整数不除(2457.6,但量化误差不排除:NUM=2457→200048 Hz)且已由 JL_LRCT
# 独立测量(结构上两套机制同测一钟的可能性低,未证伪)。
# ④探路值 NUM=15000(0x3A98):491,520,000/15,000=32,768 整除精确,×1000=
# 32,768,000 mHz——「假设标称 32768 Hz 在该换算常数下的恒等计数」,商即
# 假设标称频率本身(算术自洽;LRCT 38400→200000 的「标称闭环」强度更高,
# 本处降一档=assumed-not-measured+推导计数)。**非硅测量**:真实 NUM 逐片
# 逐次有抖动(20 槽滑动平均正是为消化它),fixture 每次读回同一常量=标称
# 理想化(本世界首次转换后计数器保持禁用,ring 替换路径不执行,常量不
# 触发移位淘汰腿——如实标注);CON 位段命名(GSS/CSS/GTS)取自 wl82 家族
# SDK gpcnt.h 的 SFR 宏位宽(交叉家族证据,BR28 本尊无公开驱动头),CSS=6
# 的源清单不可考;fixture 有效域=本世界 CON 配置(GSS=4/GTS=6/CSS=6,
# 实测 0x460C 前缀),未来重配置需重审。未提供该参数时 NUM 读严格停
# (29,601,125 前沿形状);值下界 1(0 会除零)。
GPCNT_NUM_PROBE=15000  # 491,520,000/32,768 = 15,000 = 0x3A98(assumed 32768 Hz)

# JL_RAND->R64L(0xfd3b00)种子 fixture 常量(路 RAND/sim-rand)。熵源类停点:
# 参考 SDK br28.h `JL_RAND_TypeDef {__RO u32 R64L; __RO u32 R64H}`,cpu.h
# rand32() 直接读 R64L;熵机制(真随机/计数器/烧录值)在参考 SDK 无任何文档,
# 值不可推导——synthetic-not-measured。fixture=冻结一次合法抽样的 4 字节
# 常量(主臂 0x12345678,Lehmer MINSTD 首步 state=0x2a18e99e,6 字节 MAC 输出
# 31 54 23 93 fe eb 非退化);退化种子 0/0x7FFFFFFF/0xFFFFFFFE 都是 LCG 不动点
# (→全零输出,全零 MAC 会被消费者 0x6011a08 的 memcmp 判「仍空白」再生成),
# 主 fixture 避开。R64H(0xfd3b04)零直接常量且已审查路径零访问,不进容器。
JL_RAND_SEED_BYTES=(0x12345678).to_bytes(4,'little')

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

# 路 BM §1.3 勘误/S19 批 1:S19 条件栈的 RES 具名档扩展表。**勘误**:BA §2.4/
# §5.1 #6 行记 W4[4] trim 环采样「0x601174c(0x10005) fs 采样,fd31✓」不成立
# ——0x10005(通道字 0x10000|P3_ANA_CON4[4:1] 复用域值 5)不在 BY 的
# FD31_ADC_RES_NAMED_VALUES 里,trim 环每轮采样会撞 fd31_adc_res_channel_
# not_modeled 诚实停,必须随 PVDD 容器同 PR 扩行。两行新值(全部
# derived-not-measured,推导链全文见 docs/s19-final-specs.md §1.3):
# - **0x10005=400**:W4[4] trim 环/0x12c29a 量测分支的单发采样通道。值域约束
#   同 BY(十位域内非零、与 WBG04/VTEMP 同带);**常量 fixture 下 trim 环恒走
#   端点**:采样值 400≤800(环出口 `ifs (v2>800)` 有符号早退不触发)→走满
#   15 轮(r6=r7=15)→b[+1]=15∉[3,10]→缺省段 0x60687c8(b=8/11)→应用段
#   ——「中途健康退出」(r7∈[3,10])需逐轮递增值表=对硅 PVDD 阶梯响应过拟合,
#   不造(BA P4 行对 PRD 完成时刻的同款否决逻辑)。
# - **0x10001=4**:BH §1.4 的键通道释放态行(该行只在键通道真实注册的世界
#   被读,当前是惰性字典项,顺带覆盖 BH §1.4 的 R2「adc_scan 轮选键通道」
#   防御面;值=键分档器释放态读数 4 与 KEY_CHAIN 阈值表同源;19,139,239
#   停点实测 served 计数零消费——键通道注册(0x60010b4 解析/iokey init)
#   仍在停点之后未执行)。
# p11_heap 条件场景经 registry value_attr 改传本表(BM §1.3:flag 字典进报告
# 与前沿重钉同 PR);BY/键链场景继续用旧表(各自常量不动,legacy 逐字节不变)。
FD31_ADC_RES_S19_VALUES={**FD31_ADC_RES_NAMED_VALUES,0x10005:400,0x10001:4}

# 路 JLEQ 续三/S19 表再扩两行(29,258,205 停点 0x6011704=fd31_adc_res_
# channel_not_modeled 通道 0x1000d 放行):W4[4] PVDD/VLVD trim 域第二函数
# 0x6068662 的两处新通道采样(反汇编+SDK gpadc_hw.h 枚举钉死):
# - **0x1000d=AD_CH_PMU_TWVD=350**:0x606872c 装载、0x606874c 12 轮双通道
#   getter(每轮同采 WBG04 0x10000=400)→divider 0x601171a 归一化(值=
#   raw×K/vref,K=801±3d)→环出口 `bgts (r0>800)`(0x6068752,**比较换算值**,
#   与 BM 0x10005 环比较单发原始值不同形)。值约束:恒 ≤800×400/K 对全
#   K∈[756,846](350×846/400=740<800)→常量样本下环恒走满 15 轮到端点
#   (r6=r7=15∉[3,10]→缺省段 0x60687c8 b=8/11,与 BM 同一「端点策略」,
#   中途健康退出需逐轮阶梯=对硅响应过拟合不做);非零、十位域内(BY 同带)。
# - **0x1000e=AD_CH_PMU_PVDD=550**:0x6068786 装载、同 getter 单发,消费=
#   `trim=0xD-(v-950)/50`(0x6068792..9c,div.u 无符号→v<950 会回绕成巨值,
#   值约束 v=550×K/400∈[1039,1163]≥950 恒成立)→存 b[r4+2]/b[r4+0](端点
#   形状下纯数据路径——r7=15 使 0x60687b0 首查即跳缺省段,b[+2] 检查
#   (∈[7,13] 才调 apply)不再被走到;值仍取在良构带 9..12 内)。apply 路径
#   写 0xf2c10c/0xf2c110(P3_PVDD0/1_AUTO,jl_pvdd_trim_writes+VOLTAGE_CONFIG
#   已建模)。全部 derived-not-measured(BY「显式 synthetic fixture」同族);
#   BY/键链场景继续用旧表 FD31_ADC_RES_NAMED_VALUES 不变。
FD31_ADC_RES_S19_VALUES.update({0x1000d:350,0x1000e:550})

# 路 BM §2.3/S19 批 1 ③:VLVD canary 首读具名字 fixture。0x12c29a 量测分支
# (0x60687a4 调入)首读 P3_VLVD_CON(0xf2c044)@RAM 0x12c2d6——健康 boot
# 路径的**必经**首读(当前 fixture 世界非条件:0x10006=500→v4=2000≤3699
# →量测分支必走,BM §2.1),且先于该地址一切写(canary 首读 fixture 的适用
# 条件严格满足)。消费位只有 bit0(0x060c5b2e `if((r8&1)==0)`,置位侧才回写
# |=1)——取 0=「VLVD 比较器未使能的 boot 初态」,与「W4[4] 正是 VLVD 的
# 初始化者、bit0/bit2 都由本函数自己写」自洽;高位无读者不发明。
# synthetic-not-measured(0xfd3604 LRCT 先例同款「具名探路值」证据等级);
# 写后/重复读/未覆盖地址一律严格停(BV 的 canary 语义原样)。
P33_CANARY_FIRST_READ_WORDS={0xf2c044:0}

# 路 B2R/S19 批 2 重落地(0b875ed 原案,BH §5.1 字节级草案):config-523
# 分档器产物对象具名 fixture(70B @ 0x1274F4,PC 锚定首装——解析器
# 0x60010b4 首次执行前写入,幂等早退路径让解析器自己消费这次填充)。
# 全部 synthetic-not-measured(真实 config 523 在 bank B 记录区 0x1e0200,
# 公开工件外):
# +0x00 enable=1(解析器成功路径写的值;S17 门 b[+0]!=0 不取 K2);
# +0x01..03 io 描述全 0(+1 高半字节 port=0→DIE 调用早退);
# +0x04 通道字 w=0x00100001(mux 1:boot 侧已占用 mux 0/4/5/6——BY 三通道
#       +W4[4] trim 的 0x10005——1 空闲,S/AJ 同选);
# +0x08..09 第三段门=0(iokey init 第三段跳过);
# +0x0A 阈值表 20×u16=500/720/850/1022/1022×16(升序,AJ §8.1 原样);
# +0x32 键 id 表 20×B=255/10/11/12/0×16(BW 掩码×App 表推导对应)。
# 释放态 ≤9 修正(BH §3.3):through-boot 世界(D1/D2 窗口)键通道原始读数
# 必须 ≤9(S17 键线稳定门),配套 RES 行 0x10001=4 已并入
# FD31_ADC_RES_S19_VALUES(BM 批 1);S/AJ 停点后场景的释放代表值 400 维持
# 不变(其世界不跑 S17)——两个世界的 bin 0 语义相同(≤500→255 无键),
# 代表值按世界分岔。电池相关字段:无(S15 低电门阈值 h[0x127734] 属电池
# 配置域 config 20,与本 fixture 无交集)。
CFG523_OBJECT_BYTES=(
    bytes((0x01,0x00,0x00,0x00))                          # +0x00 enable, io 描述
    +(0x00100001).to_bytes(4,'little')                    # +0x04 通道字 mux 1
    +bytes((0x00,0x00))                                    # +0x08 第三段门/参数
    +b''.join(struct.pack('<H',v) for v in (500,720,850,1022)+(1022,)*16)   # +0x0A 阈值表
    +bytes((255,10,11,12))+bytes(16)                       # +0x32 键 id 表
)
assert len(CFG523_OBJECT_BYTES)==70
# 键通道 RES 释放态档(接地分压代表值,0..9 区间中段;S17 判定读队列存储值
# 不依赖它——仅消 adc_scan 轮选启动转换的 fd31 诚实停;BM 批 1 已并入 S19 值表)。
CFG523_KEY_CHANNEL_RES_IDLE=4

# JL_ADDA->DAA_CON1(0xfe0a04)首读的显式初始字探针值(路 CV/sim-adda,
# derived-not-measured;**sensitivity-probe-not-plausible-state——是探针,
# 不是合理状态**)。背景:3,170,362 停点 0x606b426 是配置 RMW insert 函数
# 0x606b40e(经真调用 veneer 0x606db6c 调入,唯一到达调用点=DAC init 驱动
# 0x6071b70,r0=0xFF/r1=0 运行态实测)对 DAA_CON1 的首读——**音频尾段首个
# 非掩码平凡 RMW 读**(读值实质参与写回:bits3..31 原样保留、bits0..2 与
# bits4..6 换 4 档增益码表 0x060A8608={0,4,6,7} 的码),且本世界该字零写
# ——读的是「DAC 模拟块寄存器内容」硬件域状态,投影世界无从推导(**首个
# 真实硬件反馈依赖**)。措辞红线:**「首次读取时的未知状态」**——ROM/
# 他核/模拟域复位仍可能影响该字,不称「零写历史」。
# 探针对(codex 敏感性对照定调):{0x0, 0xfffffff8}=保留位(bits3,7..31)
# 全零/全一两个极端。选择依据(见 docs/adda-daa-con1.md 敏感性表):两探针
# 分叉跑出**同一步数/同一写回形状/同一下一停点**——观察路径对初值不敏感
# (RMW 只把 bits0..6 写成表码 0,写回值差异 0x0 vs 0xffffff88 不进任何
# 分支);取 0x0(保留位全零侧)作为前沿 fixture 值。**「仿真顺利返回不能
# 证明初值正确」**——本 fixture 只让链继续,不构成对硅复位值的任何声称;
# 未提供该参数时首读按原边界停(unmapped_memory 读 0xfe0a04)。
JL_ADDA_DAA_CON1_INITIAL_PROBE=0x0

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

# JL_PLED->CON0(0xfd6100)具名整字初态 fixture(路 FD610C-con0,条件主臂;
# derived-not-measured,非硅复位值、非 SDK 推导)。0x60194b4 的 save-restore
# 首读(r6=[0xfd6100]→清 bit0→if(r6&1)恢复)读值 bit0 直接进分支,boot 前缀
# 在首读前从未写过 0xfd6100(写会 Blocked),故初值是明确选择的实验输入。
# codex 三选一(2026-11-17,gpt-6-astra)定调 (a):0=假分支(条件恢复不执行,
# LED 保持关),1=真分支(恢复执行,LED 保持开);两臂均无硬件证据,不因哪臂
# 跑得更远反推更真实。敏感性对照臂 0x00000001 不单独登记开关,直接用同一
# 参数传 1 即可。
JL_PLED_CON0_INITIAL_VALUE=0x00000000

# JL_IOMC 映射表七字(批 B/scout2 §4,docs/frontier-scout2-batchbc.md)具名
# fixture(条件主臂 0=空映射表;derived-not-measured,非硅复位值、非 SDK 推导)。
# 七字=OCH_CON0..2 0xfd6018/1c/20+ICH_CON0..3 0xfd6024/28/2c/30;CON0 0xfd6014
# 不在此——当前前沿世界已是活容器(platform_case 默认 seed IOMAP_CON0=0+
# port_initialization_projection 服务 0x6010b84 RMW 等)。OCH 读值进比较分支
# (0x601966e 槽搜索)、ICH 读值进分支+跨地址传播(0x60196c2 nibble 选 JL_IMAP
# 清零目标 0xfd5c00+(nibble<<2))——读者需要值,fixture 放行显式读。ICH=0 的
# 副作用=固件清 [0xfd5c00]+位图 bit0(路 V 63 字白名单内,幂等清零);OCH=0
# 恒不匹配零写副作用。批 B 触达面(引脚卸载 switch/pfun 分发器/RAM-code
# 低功耗域)全不在 LED→S19 立即路径=前缀零触达。敏感性对照臂直接用同一
# 参数传非 0 值(不单独登记开关)。
JL_IOMC_MAP_INITIAL_VALUE=0x00000000

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

    def post_stop(machine,hardware,cpu,stop=None):
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


# ---------------------------------------------------------------------------
# 路 AJ/sim-keymatrix:S 路单键两档 fixture 扩成三键矩阵(多功能/音量+/音量-)
# 的多手势场景。布局约束全部沿用 S 路钉死的 read_key 0x6000E94 消费面
# (+0 enable/键数(b)/+4 键通道字(w)/+10 20 级 u16 阈值表/+50 20 项键 id 表,
# >1022 哨兵)。三键共用同一 ADC 分压通道(键通道字 0x10001 不变——「电阻
# 分压矩阵」=不同键在同一分压网络上产生不同电压,§2.1 路 Q 钉死),各键分配
# 互不重叠的阈值区间,释放态(三键全松)同一档。**键 id 分配自洽依据[推导]**:
# BW 钉死 handler 0x6025334 位掩码 0x09050307 把 key 字段 10..13 映射逻辑键
# {7,3,5,9}(key10→7);App eventID=逻辑键<<8|(gesture+1)(app-protocol §2.5:
# 音量- 769=0x301/音量+ 1281=0x501/多功能 1793..1796=0x701..0x704)→
# key10=多功能(0x700)/key11=音量-(0x301)/key12=音量+(0x501)。**全部
# synthetic-not-measured**:真实 config 523(阈值表+键 id 表)与真实分压网络
# 读数都在 bank B 记录区/公开工件外,本表是显式合成(擦除态世界该链整体
# 旁路,路 Q 钉死)。
# ---------------------------------------------------------------------------
# 阈值表前四档(升序):释放(≤500→无键 255)/多功能(501..720→键 10)/
# 音量-(721..850→键 11)/音量+(851..1022→键 12),其余 16 槽 1023 哨兵
# (10 位 ADC 满量程界,S 路同款)。代表读数取各区间中段,与上下界的距离
# ≥50(实测最小 59)≫EMA 瞬态幅度(S 路稳态假设同款论证,单测钉死)。
KEY_CHAIN_MATRIX_THRESHOLDS=(500,720,850,1022)
KEY_CHAIN_MATRIX_KEY_IDS=(255,10,11,12)
KEY_CHAIN_MATRIX_RES_LEVELS={'multi':600,'vol_down':780,'vol_up':950}
KEY_CHAIN_MATRIX_RES_RELEASE=KEY_CHAIN_RES_RELEASE      # 400:释放态同一档(≤500→255)
KEY_CHAIN_MATRIX_KEY_BY_NAME={name:key_id for key_id,name
                              in ((10,'multi'),(11,'vol_down'),(12,'vol_up'))}
KEY_CHAIN_MATRIX_PRESS_BEATS=13    # 短按拍数(与 S 路双击/单击同款:有效稳定按压 ~10 拍,
                                    # 低于 75 拍按下确认阈值→纯点击)
KEY_CHAIN_MATRIX_GAP_BEATS=11      # 连击间隔拍数(S 路双击同款:低于 20 拍释放稳定终结)
KEY_CHAIN_MATRIX_TAIL_BEATS=25     # 末次释放稳定拍数(≥20 触发点击计数消费,S 路同款)
KEY_CHAIN_MATRIX_HOLD_BEATS=390    # 长按保持拍数(210 拍首轮实测:确认@78 拍、持续@93
                                    # +15k、1000ms 档@183 拍=确认+105;390 拍覆盖下一档
                                    # 3000ms 的首个重复槽 78+300=378)

def key_chain_matrix_sensor_object_bytes():
    """三键矩阵分档器对象的显式合成内容(read_key 消费面布局,70 字节)。"""
    obj=bytearray(70)
    obj[0]=3   # enable/键数:三键矩阵自洽值(read_key 消费面仅 b[obj+0]==0 旁路门,
               # S 路单键用 1 同理;iokey init 的逐键记录不在直调场景执行段)
    struct.pack_into('<I',obj,4,KEY_CHAIN_CHANNEL_WORD)
    for i,threshold in enumerate(list(KEY_CHAIN_MATRIX_THRESHOLDS)+[1023]*16):
        struct.pack_into('<H',obj,10+2*i,threshold)      # 20 级 u16 阈值表(升序)
    obj[50:70]=bytes(list(KEY_CHAIN_MATRIX_KEY_IDS)+[0]*16)  # 20 项键 id 字节表
    return bytes(obj)

# 矩阵场景的 RES fixture=BY 三通道+键通道(缺省=多功能键按下档);采样切换与
# S 路同机制(场景内改 hardware.fd31_adc_con.res_named_values 后直调采样事务)。
KEY_CHAIN_MATRIX_RES_NAMED_VALUES={**FD31_ADC_RES_NAMED_VALUES,
                                   KEY_CHAIN_CHANNEL_WORD:KEY_CHAIN_MATRIX_RES_LEVELS['multi']}

def make_key_chain_matrix_post_stop(app):
    """构造三键矩阵多手势直调场景的 post_stop 回调(路 AJ/sim-keymatrix)。

    与 S 路 make_key_chain_post_stop 同机制、独立实现(S 路函数与报告保持逐
    字节不变):同一 p11_heap_experimental_config 世界停点后,装三键矩阵分档器
    fixture → 真实注册键通道 → 每键一次「真实采样事务+分档查询」独立断言 →
    五个手势序列按拍直调扫描回调 0x6001944:
      ① 音量+ 单击(键 12) ② 音量- 单击(键 11)——动作覆盖的键 id 面;
      ③ 多功能键三击(手势 5=n+2) ④ 四击(手势 6)——识别覆盖的多击补全;
      ⑤ 多功能键长按保持 390 拍+释放 25 拍——长按档(1000/3000ms 两档,实测
        手势码 10/11)、长按持续 2 的重复节奏(93 拍起每 15 拍)、以及「长按
        释放是否多发一次单击」负例(实测:释放发显式手势 3,零 gesture 0)。
    post 观测同 S 路在调用点 0x6001b5e(r0=载荷字),另记所在拍号。
    """
    ctx_blob=app[0x60C3380-ENTRY:0x60C3380-ENTRY+24]
    sequence_log=[]

    def read_ram(cpu,address,size):
        return int.from_bytes(cpu.mem.read(address,size),'little')

    def write_ram(cpu,address,data):
        cpu.mem.write(address,data)

    def sample(cpu,machine,hardware,res_value,label):
        hardware.fd31_adc_con.res_named_values[KEY_CHAIN_CHANNEL_WORD]=res_value
        value,steps,_=key_chain_direct_call(cpu,machine,0x60116D0,[KEY_CHAIN_CHANNEL_WORD])
        entry={'phase':label,'res_fixture':res_value,'sampled_value':value,'steps':steps}
        if value!=res_value:
            raise Blocked('key_chain_matrix_sample_mismatch',**entry)
        slot=None
        for idx in range(11):
            if read_ram(cpu,0x127D08+16*idx+4,4)==KEY_CHAIN_CHANNEL_WORD:
                slot=idx;break
        if slot is None:
            raise Blocked('key_chain_matrix_queue_slot_missing',**entry)
        write_ram(cpu,KEY_CHAIN_ADC_QUEUE_VALUE_BASE+16*slot,struct.pack('<H',value))
        entry['queue_slot']=slot
        sequence_log.append(entry)
        return value

    beat_states=[]
    tick_cursor=[None]

    def scan_beat(cpu,machine,tick,seq,beat):
        write_ram(cpu,KEY_CHAIN_SYS_TICK,struct.pack('<I',tick))
        _,steps,posts=key_chain_direct_call(cpu,machine,0x6001944,[KEY_CHAIN_REGISTRY_ENTRY],
                                            watch=(0x6001b5e,0x6001b62))
        beat_states.append({
            'seq':seq,'beat':beat,
            'confirmed_level':read_ram(cpu,0x124888+0,1),
            'last_level':read_ram(cpu,0x124888+1,1),
            'change_count':read_ram(cpu,0x124888+2,1),
            'release_count':read_ram(cpu,0x124888+3,1),
            'press_count':read_ram(cpu,0x124888+4,1),
            'hold_timer':read_ram(cpu,0x124888+6,2),
            'click_count':read_ram(cpu,KEY_CHAIN_CLICK_COUNT,1),
            'tracked_key':read_ram(cpu,KEY_CHAIN_GESTURE_STRUCT,1),
            'post_payloads':[p['args'][0] for p in posts if p['pc']==hex(0x6001b5e)]})
        return steps,[p for p in posts if p['pc']==hex(0x6001b5e)]

    def reset_scan_state(cpu,seq):
        write_ram(cpu,0x124888,ctx_blob)
        write_ram(cpu,KEY_CHAIN_GESTURE_STRUCT,bytes([255]))
        write_ram(cpu,KEY_CHAIN_CLICK_COUNT,bytes([0]))
        beat_states.append({'sequence_reset':True,'seq':seq})

    def posts_of(seq):
        # 序列内全部 post(逐拍;一拍多 post 全收,不截断)
        return [{'beat':b['beat'],'payload':payload}
                for b in beat_states if b.get('seq')==seq
                for payload in b.get('post_payloads',[])]

    def click_sequence(cpu,machine,hardware,seq,name,clicks):
        """clicks 连击:短按 13 拍×clicks、间隔 11 拍、末次释放稳定 25 拍。"""
        reset_scan_state(cpu,seq)
        level=KEY_CHAIN_MATRIX_RES_LEVELS[name]
        beat=0
        for click in range(clicks):
            sample(cpu,machine,hardware,level,f'{seq}_press_{click+1}')
            for _ in range(KEY_CHAIN_MATRIX_PRESS_BEATS):
                beat+=1;scan_beat(cpu,machine,tick_cursor[0]+beat,seq,beat)
            sample(cpu,machine,hardware,KEY_CHAIN_MATRIX_RES_RELEASE,f'{seq}_release_{click+1}')
            tail=KEY_CHAIN_MATRIX_TAIL_BEATS if click==clicks-1 else KEY_CHAIN_MATRIX_GAP_BEATS
            for _ in range(tail):
                beat+=1;scan_beat(cpu,machine,tick_cursor[0]+beat,seq,beat)
        tick_cursor[0]+=beat
        return {'key_name':name,'key_id':KEY_CHAIN_MATRIX_KEY_BY_NAME[name],'clicks':clicks,
                'beats':beat,'post_calls':posts_of(seq)}

    def post_stop(machine,hardware,cpu,stop=None):
        if machine is None or hardware is None:
            raise Blocked('key_chain_matrix_requires_dualcore_world')
        if cpu.core_id!=0:
            raise Blocked('key_chain_matrix_stopped_on_cpu1')
        stopped_pc=cpu.pc
        # ① 三键矩阵分档器 fixture(synthetic-not-measured,布局=S 路钉死消费面)
        write_ram(cpu,KEY_CHAIN_SENSOR_OBJECT,key_chain_matrix_sensor_object_bytes())
        write_ram(cpu,KEY_CHAIN_OBJECT_POINTER,struct.pack('<I',KEY_CHAIN_SENSOR_OBJECT))
        # ② 分档层独立断言:键通道未注册 → 队列未命中返回 1 → 1≤500 → 键 255
        key_before,_,_=key_chain_direct_call(cpu,machine,0x6000E94,[])
        # ③ 真实注册(与 S 路同一通道字,队列 slot 3)
        _,reg_steps,_=key_chain_direct_call(cpu,machine,0x6001230,[KEY_CHAIN_CHANNEL_WORD])
        # ④ 每键一次真实采样事务+分档查询(识别覆盖的输入面:三键+释放态)
        classification={}
        for name,res_value in (('release',KEY_CHAIN_MATRIX_RES_RELEASE),
                               *KEY_CHAIN_MATRIX_RES_LEVELS.items()):
            sample(cpu,machine,hardware,res_value,f'classify_{name}')
            key_id,_,_=key_chain_direct_call(cpu,machine,0x6000E94,[])
            expected=255 if name=='release' else KEY_CHAIN_MATRIX_KEY_BY_NAME[name]
            if key_id!=expected:
                raise Blocked('key_chain_matrix_classification_mismatch',
                              name=name,res_fixture=res_value,read_key=key_id,expected=expected)
            classification[name]=key_id
        tick_cursor[0]=read_ram(cpu,KEY_CHAIN_SYS_TICK,4)
        # ⑤ 手势序列(拍计数跨序列连续,同 S 路)
        vol_up_single=click_sequence(cpu,machine,hardware,'vol_up_single','vol_up',1)
        vol_down_single=click_sequence(cpu,machine,hardware,'vol_down_single','vol_down',1)
        multi_triple=click_sequence(cpu,machine,hardware,'multi_triple_click','multi',3)
        multi_quad=click_sequence(cpu,machine,hardware,'multi_quad_click','multi',4)
        # ⑥ 长按保持+释放:长按档(1000/3000ms→手势 10/11,实测)/长按持续 2
        #   节奏/释放后多发单击负例(实测:释放发显式手势 3)
        seq='multi_long_hold'
        reset_scan_state(cpu,seq)
        beat=0
        sample(cpu,machine,hardware,KEY_CHAIN_MATRIX_RES_LEVELS['multi'],f'{seq}_press')
        for _ in range(KEY_CHAIN_MATRIX_HOLD_BEATS):
            beat+=1;scan_beat(cpu,machine,tick_cursor[0]+beat,seq,beat)
        sample(cpu,machine,hardware,KEY_CHAIN_MATRIX_RES_RELEASE,f'{seq}_release')
        release_beat=beat
        for _ in range(KEY_CHAIN_MATRIX_TAIL_BEATS):
            beat+=1;scan_beat(cpu,machine,tick_cursor[0]+beat,seq,beat)
        tick_cursor[0]+=beat
        multi_long_hold={'key_name':'multi','key_id':10,'beats':beat,'release_beat':release_beat,
                         'post_calls':posts_of(seq)}
        sequences={'vol_up_single':vol_up_single,'vol_down_single':vol_down_single,
                   'multi_triple_click':multi_triple,'multi_quad_click':multi_quad,
                   'multi_long_hold':multi_long_hold}
        beats_total=sum(s['beats'] for s in sequences.values())
        observations={
            'key_chain_matrix_scan':{
                'stopped_world_pc':hex(stopped_pc),
                'matrix_fixture':{'address':hex(KEY_CHAIN_SENSOR_OBJECT),
                                  'pointer_slot':hex(KEY_CHAIN_OBJECT_POINTER),
                                  'channel_word':hex(KEY_CHAIN_CHANNEL_WORD),
                                  'bands':[{'name':'release','key_id':255,'res_level':KEY_CHAIN_MATRIX_RES_RELEASE,
                                            'range':'<=500'},
                                           {'name':'multi','key_id':10,'res_level':KEY_CHAIN_MATRIX_RES_LEVELS['multi'],
                                            'range':'501..720'},
                                           {'name':'vol_down','key_id':11,'res_level':KEY_CHAIN_MATRIX_RES_LEVELS['vol_down'],
                                            'range':'721..850'},
                                           {'name':'vol_up','key_id':12,'res_level':KEY_CHAIN_MATRIX_RES_LEVELS['vol_up'],
                                            'range':'851..1022'}],
                                  'thresholds':list(KEY_CHAIN_MATRIX_THRESHOLDS)+[1023]*16,
                                  'key_ids':list(KEY_CHAIN_MATRIX_KEY_IDS)+[0]*16,
                                  'classification':'synthetic-not-measured (real config 523 lives in the '
                                                  'bank-B record area, outside public artifacts; band values '
                                                  'and key ids are an explicitly synthetic but self-consistent '
                                                  'matrix: non-overlapping press bands, one shared release '
                                                  'band, key ids follow the BW handler mask 0x09050307 '
                                                  'mapping 10..13->{7,3,5,9} and the App eventID layout '
                                                  '0x{logical}0{gesture+1})'},
                'read_key_before_registration':key_before,
                'adc_add_sample_ch_steps':reg_steps,
                'read_key_classification':classification,
                'sequences':sequences,
                'res_fixture_switches':sequence_log,
                'beat_states':beat_states,
                'scan_beats_total':beats_total,
            }}
        return observations
    return post_stop


def make_d1_window_post_stop():
    """构造 D1 验收观察窗场景的 post_stop 回调(路 D1RUN/PR-2;PR2_HOOKUP
    注记③)。观察器由机器侧 attach 构造并注册模块级——此处只取注册实例
    (同一 machine 校验),停点后组装 d1_window 全树+machine 面观察键,并在
    尾部 detach(下一场景 attach 另有防御性清注册)。stop 由 probe_boot
    停点后回调以 kwarg 传回(窗口停 vs unknown stop 的判别面)。"""
    import br28_machine
    def post_stop(machine,hardware,stopped_cpu,stop=None):
        observer=br28_machine.d1_window_observer
        if observer is None or observer.machine is not machine:
            return {'d1_window':{'window_blocked_events':[
                {'kind':'d1_observer_missing',
                  'note':'the D1 scenario ran without its observer registered'}]}}
        try:
            face=observer.machine_face()
            tree=observer.finalize(stop)
            # 到期步号注入 peripherals 面(checker 的 D1-2/D1-3 消费键;观察器
            # pending 边沿自累计——hardware.report() 全量重建后覆写,不丢模型键)。
            peripherals=hardware.report()
            expiry=observer.expiry_steps_faces()
            timer0=peripherals.get('timer0')
            if isinstance(timer0,dict):
                timer0['expiration_model_steps']=expiry['timer0']
                timer0['expiration_model_steps_source']=('d1_window_observer pending-edge '
                                                          'sampling (observer-side, model untouched)')
            ticks=peripherals.get('core_tick_configuration')
            if isinstance(ticks,list) and ticks and isinstance(ticks[0],dict):
                ticks[0]['expiration_model_steps']=expiry['core_tick']
                ticks[0]['expiration_model_steps_source']=('d1_window_observer pending-edge '
                                                            'sampling (observer-side, model untouched)')
            return {'d1_window':tree,
                    'machine':{**observer.project_delivery_faces(machine.report()),**face},
                    'peripherals':peripherals}
        finally:
            observer.detach()
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
             heap_info_bytes=None,byte_pair=False,mul_pair=False,lp_timer_writes=False,lrct_trim_done=False,
             lrct_trim_result=None,timer0_std12m=False,lp_timer_con_readback=False,
             p33_analog_config=False,power_init_rstcon_masks=False,
             power_init_wkup_dly=False,
             mem_pwr_con=False,p33_window=False,p33_port_sel=False,p33_awkup=False,
             p3_ls=False,p11_syscon1_masks=False,
             fd31_adc_con=False,fd31_adc_con_readback=False,
             fd31_adc_pnd_completion=False,fd31_adc_res_named_values=None,
             p33_analog_tail=False,p3_ana_read_ldoin_low=False,gpcnt_con_readback=False,
             gpcnt_con_config=False,gpcnt_con_completion=False,gpcnt_num_value=None,
             jl_clock_dcck=False,
             jl_uart_con=False,
             jl_uart_init_writes=False,
             jl_uart_con1_readback=False,
             jl_uart_tx_start_writes=False,
             jl_omap_writes=False,
             jl_imap_writes=False,
             wl_aud_con_writes=False,
             audio_dac_init_writes=False,
             jl_audio_adc_reset_writes=False,
             jl_adda_daa_con1_initial_fixture=None,
             jl_adda_daa_con1_config_readback=False,
             jl_audio_dac_vl0_readback=False,
             subword_stores=False,
             word_store_sp_l=False,
             ext_pair=False,
             postincrement_pair=False,
             postincrement_store_word=False,
             preincrement_store_subword=False,
             postincrement_store_subword=False,
             rep_conditional_body=False,
             mmu_cross_page_split=False,
             timer0_irq4_delivery=False,timer0_irq4_probe_steps=None,timer0_irq4_masked=False,
             core_tick_irq3_delivery=False,core_tick_irq3_probe_steps=None,core_tick_irq3_masked=False,
             core_tick_prd_readback=False,
             core_tick_cnt_held_snapshot=False,
             core_tick_freerun_edge_period=False,
             core_tick_cnt_snapshot_probe_value=None,
             jl_adda_analog_family=False,
             jl_audio_dac_tm_readback=False,jl_audio_dac_tm_probe_value=None,
             jl_adda_ada_family=False,jl_adda_ada_probe_value=None,
             jl_anc_writes=False,
             jl_clock_con3_readback=False,
             jl_pled_con3_readback=False,
             jl_pled_con0_initial_fixture=None,
             p2m_clk_con0_config_rmw=False,
             jl_iomc_map_fixture=None,
             jl_pled_config_words=False,
             jlstream_kick_param2_unknown=False,
             copex_exp22_model=False,copex_exp22_probe_ul_bias=None,
             p11_lpvad_config=False,
             event_clock=False,
             jl_audio_dac_cop_writes=False,
             jl_audio_dac_dma_writes=False,
             jl_audio_dac_dma_idle_readback=False,
             jl_audio_dac_dma_setup_discarded_reads=False,
             jl_audio_dac_dma_setup_discarded_sites=5,
             jl_audio_dac_dma_len_readback=False,
             jl_audio_dac_dma_pns_write=False,
             jl_audio_dac_dma_pcm_produce=False,
             p3_pcnt_set0_readback=False,
             p3_vlvd_bit2_writeback_readback=False,
             p3_pr_pwr_retained_bits=None,
             jl_audio_adc_dma_writes=False,
             audio_adc_dma_delivery=False,
             jl_adc_start_config=False,jl_adc_start_probe_value=None,
             jl_src_sync_config_words=False,jl_src_sync_probe_value=None,
             jl_src_sync_full_block=False,jl_src_sync_coef_batch_readback=False,
             src_sync_delivery=False,
             src_sync_cursor_progress=False,
             src_sync_cursor_sample_bytes=4,
             src_sync_cursor_consume=None,
             src_sync_cursor_produce=None,
             jl_sync_config_words=False,
             jl_btclk_timer_words=False,
             jl_fm_con_head_rmw=False,
             ancif_official_resources=None,
             ton_official_partition_pair=None,
             jl_rand_seed=None,
             jl_eq_init_writes=False,
             jl_pvdd_trim_writes=False,
             timer2_delay_projection=False,
             idle_wake_consumes_idle=False,
             p33_canary=False,p33_canary_first_read_words=None,
             p2m_mailbox_ack=False,
             wdt_mailbox_feed=False,
             cfg523_named_fixture=False,
             key_chain_scan=None,
             key_chain_matrix=None,
             post_stop=None,
             d1_acceptance=False,d1_window_steps=None,d1_post_inject=None,
             flash_image_bytes=None,readonly=(),
             snapshot=None):
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
    # 路 SEGIMPL2:本容器(不新开开关)扩到 uextra.rrii/insert.rrii 前缀×
    # st.h 第二半——形状与半字族同构(A 半纯寄存器+立即数位运算,效果只有
    # regs+flags;B 半半字部分写字节粒度直写)。证据形状 ROM 0x5ed0
    # `uextra.rrii.p r5=uextra(r5,p:4,l:1) ∥ st.h h[r3+2]=r6`(SEGIMPL 前沿
    # 42,172,454 停点:ROM 0x5e94-0x5ef2 字节→半字展开环每轮高半字的 B 半,
    # 上游 pair 模型写前合并读撞 SEGIMPL 放行后保留的未知字节 0x19e316);
    # ROM 0x11144=同族第二实例(同形展开环 0x11134..0x1114a)——放行的是
    # 形状非站点。静态面上界:app uextra∥st.h 6 站+insert∥st.h 3 站+ROM
    # 2 站,0..42,172,441 前缀零执行(crc_bankb 177,491 动态 watch 零命中
    # +off 臂逐键对照,唯一前缀内迁移=展开环迭代 0 已知字上的 0x5ed0,
    # PARSTOR 同款);st.w 族第二半×extract 前缀不在放行面(到达时诚实停)。
    if halfword_pair:extra['exact_parallel_halfword_stores']=True
    # byte_pair:opt-in 的并行对字节存储第二半(exact_parallel_stores 的独立
    # 子开关 exact_parallel_byte_stores,同半字先例单独 opt-in)。证据形状
    # app 0x6067a74 add.s14.rri.p∥st.b b[r0+0]=r4(power_init 主核对 P11
    # M2P 消息区的首写):字节存储单字节全覆盖、旧值完全无关,直写 1 个
    # 目标字节;不走上游 pair 存储模型(其写前合并读旧 word 是模型簿记读,
    # 未初始化/未映射 RAM 上会假停)。默认关闭,legacy 报告不变。
    if byte_pair:extra['exact_parallel_byte_stores']=True
    # mul_pair:opt-in 的并行对 mul.* 标量乘前缀(exact_parallel_stores 的独立
    # 子开关 exact_parallel_mul_prefixes,路 SEGIMPL3)。16 形=hw/fm1-2026-10-24-v3
    # 全部 mul 前缀捕获形,全部纯寄存器/立即数乘(标量模型效果只有 regs+flags,
    # 含乘累加形 acc=寄存器对;运行时 effect 纯度守卫兜底);B 半存储按 v3 旧
    # 寄存器语义(「B 读 A 之前旧寄存器」64/64 条∥st.w 捕获核对)走既有 word/
    # halfword/byte 直写路径(宽度子门分层:word 直接接受、st.h 要 halfword_pair、
    # st.b 要 byte_pair)。证据形状 ROM 0x6198 `mul.modimm.rri.p r0=r0*0x9 ∥
    # st.w.sp [sp+4]=r2`(SEGIMPL2 前沿 42,173,271 停点:B 半上游合并读撞未初始
    # 化帧槽 0x19e4bc——ROM 0x6170 函数序言帧口袋 [sp+4] 未写过,拒绝合成初始
    # 化,word 整字 4 字节全覆盖旧值语义无关直写恰覆盖)。同段 0x61aa
    # mul∥ld.w 走上游模型(B 半装载,不属本 gate)。独立子开关而非并入
    # pure-ALU 主清单的原因(halfword_pair/word_store_sp_l/CQ 先例同款):
    # mul 前缀×st.* 在已执行窗口(0..42,173,271)内恰 8 次真实执行/6 站
    # (快照段 0..41,884,941 恰 6 次=word 5〔0x601d1ee×2/0x601d282×2/
    # 0x606df1e×1〕+half 1〔0x6011be8〕;resume 段恰 2 次=word 1〔0x606fb8c〕
    # +half 1〔ROM 0x7604〕;从零 41.9M attempts watch 重放与自建 base 快照
    # 账本双口径互证,adapter 14,615/568 一致),全部走上游模型目标字已知
    # 正常完成——并入主 gate 会改 p11 条件场景报告的 adapter 计数与写事件
    # 形状,已执行形状独立 opt-in;crc_bankb 177,491 mul×st.* 动态 watch
    # 零命中,base 场景零变化。静态面上界(2 字节对齐,A 半按 16 形过滤):
    # app word 族 94/st.h 5/st.b 11 站+ROM word 族 24/st.h 1 站;madd.*/
    # msub.*=另族(v3 另有 48/40 条∥st.w 捕获,未纳入本路评审)与确无捕获
    # 的 mul.f.rrr 都不收(到达时同纪律评审)。
    if mul_pair:extra['exact_parallel_mul_prefixes']=True
    # word_store_sp_l:opt-in 的长位移 sp 相对整字存储 st.w.sp.l(exact_parallel_
    # stores 独立子开关 exact_word_store_sp_l)。证据形状 app 0x6065ccc
    # `st.w.sp.l [sp+8]=r9`(scheduler 中断上下文 helper 0x6065c3a 尾部):全 4
    # 字节覆盖写、旧值无关,上游存储模型写前合并读旧 word(模型内部簿记读、
    # 非 guest 装载),未初始化局部 0x19e2d4 上假停。该形状在 legacy 前缀
    # (177,491 之前)已真实执行 3 次(走上游模型、目标内存已初始化、正常完成),
    # 并入主开关会改 legacy 报告的 adapter 计数,故单独 opt-in。默认关闭,
    # legacy 报告不变。
    if word_store_sp_l:extra['exact_word_store_sp_l']=True
    # ext_pair:opt-in 的并行对 ext.* 传送族前缀(exact_parallel_stores 独立
    # 子开关 exact_parallel_ext_transfers,路 PARSTOR)。ext.b0.u/ext.b0.s/
    # ext.l.u/ext.l.s=寄存器低字节/低半字零/符号扩展,pure-ALU(效果只有
    # regs+flags,ISA confirmed、hw/fm1 硬件确认「result as stated, N/Z follow,
    # C/V kept」)。证据形状 app 0x600f95a `ext.l.u.p r2=r0.l (u) ∥
    # st.w.sp [sp+4]=r5`(BLE 读分发器 0x600f85e 帧内,SIMB1F 假停现场:停点
    # 世界 park 前沿的分发器 216B 帧落在从未写过的 handler 栈口袋,上游
    # store 模型写前合并读旧 word=模型簿记读非 guest 装载,未初始化 RAM 上
    # 假停)。第二半按 FM-1 run3 旧寄存器语义执行(「B 读 A 之前旧寄存器」,
    # SIMPRE §1.5 纪律)。静态面:全 app 233 个 ext.*.p∥st.* 站点;重捕获实测
    # 前沿 29,601,125@0x606955c 零位移,287 次前缀内 ext 对迁至本 gate(架构
    # 等值,adapter 4182→4184/42→327 只落在本 opt-in 条件场景),base 场景与
    # probe_dualcore 零变化。
    if ext_pair:extra['exact_parallel_ext_transfers']=True
    # postincrement_pair:opt-in 的 ext 传送族∥pi/pin 后增量存储对(exact_parallel_
    # stores 独立子开关 exact_parallel_postincrement_stores,依赖 ext_pair 的
    # exact_parallel_ext_transfers,路 DEBTCLEAR——PARSTOR 留待的 pi·pr 形 6 站点
    # 收口)。st.{w,h,b}.{pi,pin}(及 .l 长形)=存储到旧基址后基址+=增量(寄存器
    # 或立即),ISA confirmed 三源(hw/fm1-2026-10-24-v2);与 ext A 半组合按 FM-1
    # run3 旧寄存器纪律执行(存储地址/值都取旧寄存器),both-write 冲突 A 结果
    # 保留(upstream pair() 同规则;该冲突捕获的 B 半是装载,writeback 组合无
    # 直接硬件捕获,derived-not-measured)。静态面:全 app 233 个 ext.*.p∥st.*
    # 站点中 6 个本形(st.w.pin.l 0x6077628/0x60776c2、st.w.pi 0x609999a、
    # st.b.pi 0x60b5e66/0x60b6572、st.b.pin 0x60b61ba),宽度子门分层照旧
    # (word 无额外/half/byte 各自子开关);其他前缀(add/mul 等 118 站点)照旧
    # 落上游通用 pair 模型。默认关,legacy 报告不变。
    if postincrement_pair:extra['exact_parallel_postincrement_stores']=True
    # postincrement_store_word:opt-in 的独立(非并行对)后增量整字存储直写
    # (exact_postincrement_word_stores 子开关,路 STOREG3)。st.{w}.{pi,pin}
    # 及 .l 长形 4 形=存储到旧基址后基址+=增量(寄存器或立即),行为依据=hw/
    # fm1-2026-10-24-v2+followup5 硬件捕获 53/53(旧基址存储/旧寄存器取值/
    # 基址增量/flags 不动,含基址=数据、基址=增量别名样本)与 m2 模型同语义;
    # 整字 4 字节全覆盖旧值无关(st.d.sp.l/st.w.sp.l/word 家族 gate 同论证)
    # ——不走上游存储模型写前合并读(模型簿记读非 guest 装载,未初始化 RAM
    # 假停)。证据形状 0x60649d0 `st.w.pin.l [r9++=4]=r0`(WDFEED 前沿
    # 41,828,530 停点,0x60649b0 get-event 解包循环写 0x19f7a8 未初始化物理
    # 堆字)。静态面:独立 word 形全 app 1,045 站点;half/byte pi/pin 形与
    # st.d.pi/pin.l 不在本开关(到达时诚实停)。与并行 postincrement_pair
    # 开关无依赖关系(独立指令无 A 半);与 immediate_rep_projection 短形
    # gate 的次序关系见 probe_boot gate 注释(短形由旧 gate 先接,行为不变)。
    # 默认关,legacy 报告不变。
    if postincrement_store_word:extra['exact_postincrement_word_stores']=True
    # preincrement_store_subword:opt-in 的独立前增量(寄存器)半字/字节存储
    # 直写(exact_preincrement_subword_stores 子开关,路 SEGIMPL)。st.{h,b}.
    # prer=先基址+=增量寄存器、存到新基址、数据取旧寄存器值、flags 不动,
    # 行为依据=hw/fm1-2026-10-24-v2 followup5-m 硬件捕获 13/13(含基址=数据
    # 别名样本)与 m2 模型 memop prer 同语义;半字/字节部分写只写自己的
    # 字节、硅上不读包含字——上游存储模型 Writes.put 写前合并读旧 word 是
    # 模型簿记读非 guest 装载,未初始化 RAM 上假停(与 exact_subword_stores
    # 偏移形/exact_postincrement_word_stores word 族同一论证,字节粒度直写
    # 恰好初始化所写字节)。证据形状 ROM 0x5ea4 `st.h.prer h[++r3=r6]=r7`
    # (FMIMPL 前沿 42,172,441 停点,ROM 0x5e94-0x5ef2 字节→半字展开环,
    # MMU v0x404380 页→p0x19e300 物理堆展开区;同环 0x5ec6 st.b.prer 字节
    # 通道同族)。放行审计(拒绝合成初始化路径):base 快照 41,884,941 边界
    # RAM 镜像恰 0x19e314-0x19e31b 8 字节=已被未知值运输写覆盖(unknown_
    # origins 指向 0x19f7e8-0x19f7ef,值未知非从未写过);展开环对目的零
    # 装载、每轮低半+高半成对写=固件语义不消费旧值,直写恰清除所写字节的
    # 未知来源、邻字节保留(放行后 +13 步 0x5ed0 并行对 uextra.rrii.p∥st.h
    # 高半字合并读如实撞保留的未知字节——下一停点 42,172,454,归下路)。
    # 边界:pre 立即数形(app 16 h+41 b 站点,hw 各 13 条)与 hi.prer 形
    # (hw 13 条、全库零站点)未被触达不在开关;st.w/st.d.prer 整字前增量
    # 形(38+4 站点)与 st.{h,b}.{pi,pi.l,pin.l} 后增量部分字形(app 上千
    # 站点)照旧到达时诚实停;ld.*.prer 真读不在放行族。静态面上界:app
    # st.h.prer 6/st.b.prer 8 站点,ROM 7/2 站点。默认关,legacy 报告不变。
    if preincrement_store_subword:extra['exact_preincrement_subword_stores']=True
    # postincrement_store_subword:opt-in 的独立(非并行对 B 半)后增量半字/
    # 字节存储直写(exact_postincrement_subword_stores 子开关,路 SEGIMPL4)。
    # st.{h,b}.{pi,pin} 及 .l 长形 8 形=存储到旧基址后基址+=增量(寄存器或
    # 立即;长形立即已符号扩展并按宽度定标),数据取旧寄存器值、flags 不动
    # ——行为依据=hw/fm1-2026-10-24-v2 results.jsonl 独立形每形 12 条捕获
    # 逐条核对(唯一变化寄存器=基址、步长 0 时零变化、mem 读回字低 width
    # 字节=旧数据寄存器低位、psr 不动;半字步长全偶/字节步长任意/寄存器步
    # 长含 0 与负)与 m2 模型 memop 同语义,ISA confirmed 三源(并行对 B 半
    # 12 形容器 DEBTCLEAR 先例已收同形)。半字/字节部分写只写自己的字节、
    # 硅上不读包含字——上游存储模型 Writes.put 写前合并读旧 word 是模型簿记
    # 读非 guest 装载,未初始化 RAM 上假停(与 exact_subword_stores 偏移形/
    # prer 前增量形/word 族 STOREG3 同一论证)。证据形状 ROM 0x63c2
    # `st.h.pin.l h[r11++=2]=r0`(SEGIMPL3 前沿 42,173,750 停点:rep.i 4 16
    # 清零循环体第二轮第 2 个半字的合并读撞未初始化堆半字窗 0x19e100,r11
    # 虚拟 0x404400 经 MMU→物理 0x19e100;第一轮 16 次+第二轮第 1 个半字共
    # 17 次已真实执行走上游模型目标字节已知正常完成)。独立子开关而非并入
    # exact_subword_stores(mul_pair/word_store_sp_l/prer 先例:已执行形状
    # 独立 opt-in,并入会改 p11 条件场景报告 adapter 计数与写事件形状);
    # 零装载论证限于 rep 体(外层循环 0x6242 ld.w.idxs 实有装载照旧走上游
    # 模型)。静态面上界(2 字节对齐,含数据段假阳性):app st.h.pin.l 12/
    # st.h.pi.l 9/st.b.pin.l 39/st.b.pi.l 0 站,ROM 10/1/2/0 站;短形 st.h.pin/
    # st.b.pin 由 immediate_rep_projection 短形 gate 先接不迁移(gate 次序
    # 见 probe_boot 注释,STOREG3 同款)。与 postincrement_pair(并行对 B 半)
    # /postincrement_store_word(word 族)无依赖关系(独立指令无 A 半/无宽度
    # 交叠)。默认关,legacy 报告不变。
    if postincrement_store_subword:extra['exact_postincrement_subword_stores']=True
    # rep_conditional_body:opt-in 的寄存器计数 rep 体内非嵌套条件块拆解执行
    # (conditional 层子开关,路 REPCOND)。multi-block rep gate 的体校验 walk
    # 块感知化:只放行非嵌套 ifs.gt.rr/ifs.lt.rr、t=1/e=0、块内唯一 then 是一
    # 条有模型的 mov.rr、块终点严格早于 rep 终点(块后留无条件尾)、体内不引用
    # 计数寄存器(计数寄存器每轮由 rep 机制递减,体内读它见未测中间值、写它使
    # 两计数分离——一律拒绝)。证据形状 0x60736c8 24 位音频样本原地钳位拷贝环
    # `rep 16 r0 { ld.w r4=[r1+0]; ifs.gt.rr(r4>r2){r4=r2}; ifs.lt.rr(r4<r3)
    # {r4=r3}; st.w.pin [r1++=4]=r4 }`(r2=0x7FFFFF/r3=0xFF800000,计数 r0=
    # [r8+8]>>2 无 mask)。组合投影=两个已独立 hw 验证事实(rep 字节计数循环+
    # 块条件选臂)的组合,标 derived-not-measured:rep×体内控制流的组合行为、
    # 128 轮中间状态与 IRQ 可见时点均无硅上捕获,上下钳取真路径靠隔离单测。
    # 默认关(legacy 报告不变);开关关时体含条件块开启器照旧诚实拒
    # (rep_multi_block_body_not_straight_line)。
    if rep_conditional_body:extra['rep_conditional_body']=True
    # mmu_cross_page_split:opt-in 的跨 128B MMU 页界双字存储拆分(probe_boot
    # exact_doubleword_stores 独立子开关)。证据形状 app 0x6007af2
    # `st.d.sp.l d[sp+8]=r1_r0`(配置表查名 helper 0x6007ae8 函数头,app_core
    # 虚拟栈 sp+8=0x400afc 跨页界,全 boot 3M+ 首例)。三选一按 codex 参谋定调
    # 选方案 (a) 拆分:st.d 按两个独立 4 字节页内访问执行、每半各自 MMU 翻译
    # ——与上游 m2 模型的总线形状一致(st.d=两个独立 4 字节写、ld.d=两次独立
    # 4 字节读,AC791N 真机捕获拟合,含三例 128B 界跨越样本两字均正常落盘:
    # fm1-2026-10-24-v2 st.d.sp.l 0x1c220fc/0x1c22100、st.d.pre 0x1c29dfc/
    # 0x1c29e00、st.d.pi.l 0x1c29e7c/0x1c29e80;AC791N≠BR28,只证明 ISA 层无
    # 对齐陷阱、不证明 BR28 MMU 拆分语义)。现场映射审计(路 AN):两页 PTE
    # 0xb3c6/0xb3c5 物理倒序非连续(0x400afc→0x19e37c、0x400b00→0x19e280),
    # 拆分与「按第一页连续写」在本现场可区分;SP 审计通过(任务帧 VA 0x400bb0
    # 真实恢复→app_core 入口 0x6012028 sp=0x400ba8→3,352 次合法 push/pop/call
    # 到 0x400af4,无模型虚构跳变)。derived-not-measured:本开关验证的是
    # 「实现符合拆分假设」,不是「芯片采用该假设」。默认关,legacy 报告不变
    # (不开时同指令仍停 mmu_cross_page_access_not_split)。
    if mmu_cross_page_split:extra['mmu_cross_page_split']=True
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
    eq_regions=()
    p11_snapshots=()
    budget=250000
    if p11_power_init:
        options['p11_lpwr_response_projection']=True
        options['p11_window_projection']=True
        options['power_init_config_rmw']=True
        # M2P 消息区随 p11_power_init 一起挂为可写 harness region(范围=SDK
        # p11_app.h 自己声明的 M2P_MESSAGE_RAM_BEGIN..+0x60,普通存储语义,
        # 见 P11_M2P_MESSAGE_WINDOW 注释)。crc_bankb 前缀(177,491)在首个
        # M2P 访问之前就停,该场景报告不受影响。
        p11_regions=(P11_COMMON_CODE_WINDOW,P11_M2P_MESSAGE_WINDOW)
        # 拷贝目的窗的快照:check() 用它与 p11_code.bin 资源 entry0 的真实字节
        # (analyze_p11 的 CRC/SHA 钉死值)互证拷贝逐字节正确。
        p11_snapshots=(('p11_common_code',P11_COMMON_CODE_WINDOW[0],12112),)
        # p11_code.bin 公共段拷贝循环(0x6067990..96,每字节 4 条指令 ×12112 字节
        # ≈48k 步)超出默认 250k 预算,前沿场景放宽到 320k。
        budget=320000
        # base 层:P2M 堆描述符 0xf27f4a..4d 只读映射,种子=静态推导候选
        # (P11_HEAP_INFO_DERIVED:begin=0x3d0c/size=0x39f4,link-time 立即数,
        # derived-not-measured——与 erased_bankb 0xFF fixture 同级的显式环境
        # 假设)。只在该场景未显式传 heap_info_bytes 时挂载:P2M 是 P11→主核
        # 方向,本前缀固件只读不写,readonly 区(与 flash_image/erased_bankb
        # 同机制)按已知字节回放,不再停 unmapped_memory。条件场景仍走
        # p11_heap_info_bytes 显式 fixture(报告键 p11_heap_info_fixture 不变)。
        if heap_info_bytes is None:
            readonly=tuple(readonly)+((P11_HEAP_INFO,P11_HEAP_INFO_DERIVED),)
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
        # 路 BM §1.3/S19 批 1:W4[4] 簇放行后前沿滑过 trim 环(BM 修订预算
        # ≈4-6×10⁵:15 轮×(2×0x12e29c(2000) 自旋+24 次采样事务×~10³ 步,
        # BY 实测单价)——BA §2.4「全环 ≈10⁵」低估约 3×)+W2[0] 事件时钟
        # 6 tick+W1[4]/S9/W2..W4[0..3]+TIMER2 2×4000+尾段到 S15 邮箱首停
        # 0x12CC94,预算放宽到 5M(新前沿 ≈3.8M 量级,留事件时钟段余量)。
        if jl_pvdd_trim_writes:budget=5000000
        # 路 B3/S19 批 2 续:JL_ADDA 模拟族放行后链滑过 retry 环+溢出窗+
        # 模拟 init(4.5M)+OS 等待族(事件时钟首次真实推进 13.4M 虚拟步)到
        # 19,139,239 @ 0x606abbe,预算放宽到 20M 留余量。
        if jl_adda_analog_family:budget=20000000
        # 路 DT/S19 批 2 续二:DAC_TM0/TM1 setter 容器放行后链越过 19,139,239
        # 的两次 -600 半字写(实测 TM0=TM1=0xFDA80000,第二次调用 selector
        # ∈{9,11} 写 TM1 高半)继续走 DAC 启动尾段(zalloc(56)+分配对象填充+
        # 长度派生 TM 写)与后续 OS 等待族——20M 预算实测在 19,996,266 @
        # 0x12e286(忙等 µs 延时 helper)耗尽,预算放宽到 40M 让链走到下一个
        # 诚实边界。
        if jl_audio_dac_tm_readback:budget=40000000
        # 路 ADA/S19 冲刺批 2 续三:ADA 子族九字容器放行后链走过停点函数
        # 0x606b4d0 的 ADA 配置段(12 写)再 +51 步停在 0x606b68e 读
        # 0xf2b404(P11_LPVAD->VAD_ACON0 bit28 置位 RMW 内部读)——新前沿
        # 21,798,257,预算 40M 已充分(与 TM 层同值,余量 >18M)。
        if jl_adda_ada_family:budget=40000000
        # 路 LPV:LPVAD VAD_ACON0 单字容器放行后停点 RMW 执行,链继续走
        # 停点函数尾段(ADA 窗+RAM+二级 tbh 分发后的 case 体)——预算沿用
        # 40M(与 ADA 层同值;新停点由实跑取得后再核余量)。
        if p11_lpvad_config:budget=40000000
        # 路 JLA:ADC DMA 五字 write-only 记录放行后 set_buffer 五写完成,链
        # 继续走挂 handler+启动段与后续音频/OS 域——预算沿用 40M(与 LPV 层
        # 同值;新停点由实跑取得后再核余量)。
        if jl_audio_adc_dma_writes:budget=40000000
        # 路 ASS:ADC 启动配置块四字读回容器放行后停点 RMW/insert 族执行,
        # 链继续走启动函数尾段与后续音频/OS 域——预算沿用 40M(与 JLA 层
        # 同值;新停点由实跑取得后再核余量)。
        if jl_adc_start_config:budget=40000000
        # 路 JLEQ:EQ 初始化两字放行后,依 S19MAP #6-#15(全段已映射/已落地)
        # 静态预测一次推进到 park(S19MAP §0 预算 3.7-3.9×10⁷,事件钟段
        # ±20%→上界 ≈4.1×10⁷)——预算放宽到 50M 留余量(「一次到 park」是
        # derived-not-measured,新停点由实跑取得)。
        if jl_eq_init_writes:budget=50000000
        # 路 D1RUN/PR-2(PR2SPEC §4.4):D1 观察窗场景——JLEQ 实测停点指令步
        # 29,601,125,窗口=W_park+N(N 上界 3×P_park 量级≈几十万模型步,窗口内
        # 真实指令步远小于 N),指令轴终点 ≈29.7M;41M 覆盖停点+窗口+余量
        # (事件钟快进段虚拟步不耗指令预算)。park 实测落定后按实收紧。
        if d1_acceptance:budget=41000000
        # 路 BATCHA:批 A 13 字放行后 LED 全字重配 0x60195e2 全函数走完
        # (CON1/CON2 写+PRD writer+DUTY 配置器,18 笔配置写),效果机返回
        # S19 环继续任务安装/资源 VFS 域——+7,033 步实测停 41,858,013 @
        # 0x6010ca6(EXT_RESERVED/APP 分区 open 0x6010c3c 的 128B 目录条目
        # rep 拷贝,首字节读 flash 0x61eafe0=偏移 0x1EAFE0,OTA 镜像
        # 0x1442C0 之外,模型无字节)。预算放宽到 60M(>50M JLEQ 上界,
        # 余量 >18M)。
        if jl_pled_config_words:budget=60000000
        # 路 TONMAP:TON/TON1 官方容器放行后链穿过资源 VFS open(容器头 CRC
        # 复算 0x7e4e 与官方头字段全等+目录走查+文件 open)→新任务安装
        # (0x602d3a0/0x6086980/0x6000d6c 三任务)→事件循环,+51,493 步实测停
        # 41,909,506 @ 0x60869ba(任务 0x6086980 事件循环读输出参数第二字
        # [r10+8]=任务栈 0x18ca64 未初始化,physical_heap 诚实停)。预算沿用
        # 60M(余量 >18M)。
        if ton_official_partition_pair is not None:budget=60000000
        # 路 EVIMPL:jlstream 任务栈单点 4B 槽放行后首条消息(cmd 0)走完整
        # 事件分发,链进入音频 DAC DMA 运行域,+56,377 步实测停 41,965,883 @
        # 0x606e148(JL_AUDIO DAC DMA HRP 指针读 0xfe0510 未建模,DMA 指针
        # 轮询体 0x606e14a beq.rr 0xfe0510/0xfe0514 对读——探路 top3 第 3 名
        # 解码器/音频域)。预算沿用 60M(余量 >18M)。
        if jlstream_kick_param2_unknown:budget=60000000
        # 路 DMAIMPL:HRP/SWP 两字读回放行后空闲守卫双读相等直进 POST 段
        # (采样率记录+DAC_CON 低 4 位 RMW+audio_dac_read_reset_all 纯 RAM 段
        # +可选回调检查),链继续走调用者续段与音频/OS 域——新停点由实跑
        # 取得;预算沿用 60M(余量以实跑核)。
        if jl_audio_dac_dma_idle_readback:budget=60000000
        # 路 COPEXIMPL:mode-22 模型放行后 eq 系数链(db2mag→exp 叶子)真实
        # 执行,链继续走 eq/wdrc 设计域(侦察预判下一停点大概率 mode 24
        # sqrt 的内联站点)——新停点由实跑取得;预算沿用 60M(余量以实跑核)。
        if copex_exp22_model:budget=60000000
        # 路 SRCSYNC:JL_SRC_SYNC 配置事务三字放行后 eq open-config 尾段的
        # SRC_SYNC 重配函数 0x606fa7e 走完(bit22 开→memset→55 轮系数装载→
        # bit4 关→系数表拷贝→bit22 关),链继续走 eq 设计族/音频配置域——
        # 新停点由实跑取得;预算沿用 60M(余量以实跑核)。
        if jl_src_sync_config_words:budget=60000000
        # 路 SRCSYNC2:JL_SRC_SYNC 全块 11 新字放行后链继续走 eq 域 src_sync
        # 配置函数 B 的后续写/启动请求——新停点由实跑取得;预算沿用 60M
        # (余量以实跑核)。
        if jl_src_sync_full_block:budget=60000000
        # 路 SRCSYNC4:COEF 批次回读放行后 helper 走完 55 读+memcpy 切片+55
        # 回写,链继续走函数 B 的启动请求/后续 eq 域——新停点由实跑取得;
        # 预算沿用 60M(余量以实跑核)。
        if jl_src_sync_coef_batch_readback:budget=60000000
        # 路 SRCSYNC5:SRC_SYNC 完成+IRQ89 交付放行后 jlstream_N 的 3 tick
        # 等待被唤醒、状态变 2,链继续推进到下一个诚实边界——新停点由实跑
        # 取得;完成事件在启动写后 1 步即触发,预算沿用 60M(余量以实跑核)。
        if src_sync_delivery:budget=60000000
        # 路 SYNCIMPL:JL_SYNC 四字放行后 creator 0x60befae 的 CON 写停点
        # 被越过,链在 +2 步的 0x60befee(BT clock timer 0xF00130 写,未建模)
        # 停——新停点由实跑取得;预算沿用 60M(余量以实跑核)。
        if jl_sync_config_words:budget=60000000
        # 路 TIMERIMPL:BT clock timer 两写放行后链在 request_irq 族内
        # 停(ICU 0xffe12c RMW 或更下游,侦察 §6 三候选静态无解)——新停点
        # 由实跑取得;预算沿用 60M(余量以实跑核)。
        if jl_btclk_timer_words:budget=60000000
        # 路 FMIMPL:modem 窗 CON 头 RMW 放行后 fn_config 返回,链走链表
        # 插入/adapter 续走/表派发段(静态缺口)→节点 walker 第二次
        # fn_config 或 eq 设计族 copex 站点(mode16 0x6072bb8/mode24
        # 0x6072d1a,侦察 §6 预判下一停点 dsp_mode_not_modeled,先后运行时
        # 定)——新停点由实跑取得;预算沿用 60M(余量以实跑核)。
        # (40,40) core tick 时间基修正后 CPU1 4s 定时器以正确速率走表,
        # 双核喂狗循环把看门狗喂过两个完整 WDT 周期(16.384s×2),前缀不再
        # 停在看门狗到期前沿,而是走到 instruction_budget 前沿——预算
        # 60M→140M(约 2.14 个 WDT 周期,前沿 139,947,000 由实跑取得)。
        if jl_fm_con_head_rmw:budget=140000000
    # 快照接线路(sim-snapwire,默认 None=legacy 不变):snapshot['budget'] 覆盖
    # flag 推导的预算=「可配置步数停点」(instruction_budget 快照点,docs/
    # snapshot-design.md §2.1 任意步停点);capture/restore/expected_context 由
    # explore 消费。
    if snapshot is not None and snapshot.get('budget') is not None:
        require(isinstance(snapshot['budget'],int) and snapshot['budget']>=0,
                'snapshot budget override must be a non-negative step count (0 = pure '
                'restore for the zero-step self-check)')
        budget=snapshot['budget']
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
    # 路 GPCNUM:GPCNT 结果块快照(avg[0x12A3D0]/fill[+4]/valid[+6]/半字
    # [+0xc]/20 槽 ring[0x12A3E0..+0x50]),只挂进实验场景(与 LRC 快照同款,
    # 不进共享 p11_snapshots)——探路值的全部数值侧效应逐字节可钉。
    if gpcnt_num_value is not None:
        p11_snapshots=tuple(p11_snapshots)+(('gpcnt_result_head',0x12A3D0,0x60),)
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
    # gpcnt_con_config(路 JLEQ 续五,opt-in 默认关):29,544,781 停点
    # 0x60688b6——W4[4] VLVD 域 GPCNT 重配置链(函数 0x60688xx,W4[4]
    # walker 0x6034c8e 直调;ld.w 读 CON→and/or 组装时钟选择/分频字段
    # bits28-31/12-15/1-3/8-11→st.w 写回→|=0x40 清→|=0x1 使能)消费未知
    # 配置位诚实停(gpcnt_con_readback_consumed,known_mask=0x81)。开关
    # 放行:①读回=全字整数(未知配置位显式合成初始 0,AM synthetic-zero
    # 先例,derived-not-measured 非复位值);②整字写记录;③|=0x1 使能形状
    # (计数开始,计数语义不建模);NUM(0xfd1104 __RO 计数值)仍严格停。
    # 依赖边挂 gpcnt_con_readback(ld.w 拦截在 readback_projection 分发内,
    # 语义前置);执行序上停点在 JL_EQ/缓冲窗/freerun/RES 表/canary 形式
    # 全部放行后才到达(推进顺序,非硬件依赖)。
    if gpcnt_con_config:
        require(gpcnt_con_readback,
                'the GPCNT config-word release only layers on the CON readback '
                'projection (the ld.w interception lives in the readback dispatch)')
        options['gpcnt_con_config']=True
    # gpcnt_con_completion(路 JLEQ 续七,opt-in 默认关):49,996,010 步
    # 50M 预算耗尽停点 0x6069546——S17 稳态测量任务 0x6069538(task 栈
    # 0x418xxx,rets 链 0x6066e7e)在 bit0 使能门(W4[4] 重配置已 |=1)后
    # 0x6069546/48 无界轮询 CON bit7(beq.and.ri 等置位;喂狗/park/事件钟
    # 稳态在其之前已建立:11 次喂狗事务、CPU0 1791+57 次 park 唤醒)。开关
    # 放行:配置字读回 OR 0x80(「测量在第一次轮询读时已完成」条件投影,
    # lrct_trim_done bit7/SFC PLL-lock bit15 同模式;bit7 布局按轮询方向
    # 推断,非硅测量;延迟不建模);紧随其后的 NUM 读(0x606955c
    # [0xfd1104],值进 491520000/NUM*1000+20 槽滑动平均)保持严格停=下一
    # 前沿的测量值具名 fixture 三选一。依赖边挂 gpcnt_con_config(读侧
    # 强制位只在全字读回分发内);执行序上停点在测量任务首个转换之后。
    if gpcnt_con_completion:
        require(gpcnt_con_config,
                'the GPCNT completion-bit projection only layers on the '
                'config-word release (the forced bit lives in the full-word '
                'readback dispatch)')
        options['gpcnt_con_completion']=True
    # gpcnt_num_value(路 GPCNUM,opt-in 默认关,值开关):29,601,125 停点
    # 0x606955c——稳态测量任务 0x6069538 首次 GPCNT 转换 `ld.w r2=[r4+4]`
    # 读 0xfd1104=JL_GPCNT->NUM(br28.h __RO 计数值)的具名探路值
    # (derived-not-measured;LRCT trim_result 38400 同款三选一 (a) 标称闭环,
    # 推导链见 GPCNT_NUM_PROBE 常量注释)。值面:491520000/NUM*1000+20 槽
    # 滑动平均→[0x12A3D0]→低功耗漂移补偿(纯数据路径,无值分支);未提供
    # 时 NUM 读保持 unmapped 严格停(29,601,125 前沿形状)。依赖边挂
    # gpcnt_con_completion(NUM 读只有在完成位投影放行轮询后才真实到达)。
    if gpcnt_num_value is not None:
        require(gpcnt_con_completion,
                'the GPCNT NUM fixture only layers on the completion-bit '
                'projection (the NUM read is only reached after the poll release)')
        options['gpcnt_num_value']=gpcnt_num_value
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
    # jl_imap_writes(路 V/sim-imapwr,GPIO 引脚复用写侧·输入面):0xfd5c00..0xfd5cf8
    # = **JL_IMAP**(SDK io_imap.h `JL_IMAP_BASE = ls_base + map_adr(0x5c, 0x00)`,
    # **SDK 有名**)——GPIO 交叉开关的**外设输入功能选择寄存器组**(每个外设
    # 输入信号选一个输入通道)。**识别三方交叉钉死**:①io_imap.h 成员表
    # FI_GP_ICH0..FI_CHAIN_RST 共 63 个 __RW __u8 成员(另有 FI_TOTAL 计数
    # 哨兵),输入通道常量 USBDP_IN=47(停点观测值 0x2f);csfr.h
    # `#define __u8 unsigned int`——SFR 结构体成员一律按 32 位字编译,4 字
    # 步进是 SDK 显式设计,gpio_hw.c.o 的 PFI_* 枚举(=gpio_hw.h
    # `((u32)(&(JL_IMAP->FI_*)))`)逐成员 16604160..16604408=0xfd5c00+序×4
    # 连续 4 字步进互证,gpio_set_fun_input_port 体 `store i32, align 4` 整字
    # 写;②SDK gpio2crossbar_inport gpio>208 特例 224→47(USBDP_IN)/225→48,
    # 通道号表 gpio_in_regs={1,17,29,0,0,38,49} 与 QCY 固件表 0x609EAB8 逐
    # 字节一致,固件 twin resolver 0x60012e8(`if(r4==224) r0=47`)双侧同值;
    # ③停点 0x6001342 `[r4+0]=r3` 写 0xfd5c90=0x2f——0xfd5c90=PFI_UART0_RX
    # (固件分发器 case 体 0x6001522 `r1=16604304` 实参字面量互证),本写
    # 就是 uart_init 的 RX 引脚复用(UART0 RX 与 TX 同用 USB DP 脚,单线
    # UART)。**访问面**:app.bin 全镜像字面量扫描 37 处全落 63 字组内,
    # 直接访问全部整字 st.w(复用写+disable 清零 0x601968c),零读回;ROM
    # 单处 0xfd5c04 字面量是存常量进 RAM 非 IMAP 访问。白名单=63 字,
    # JL_OMAP 的 write-only 整字记录先例:st.w 全字覆盖写、值=真实指令结果
    # (resolver 真实计算通道号);读回/RMW 严格停;交叉开关布线/输入电平/
    # IRQ 不建模——只记录「输入复用配置写已执行」,不声称 UART 通信验证。
    # 默认关:0xfd5cxx 保持 unmapped(3,073,402 @ 0x6001342 前沿形状不变),
    # legacy 报告不变。
    if jl_imap_writes:
        require(jl_omap_writes,
                'the JL_IMAP write records only layer on the JL_OMAP pin-mux prefix '
                '(uart_init reaches the input mux after the output mux)')
        options['jl_imap_write_records']=True
    # wl_aud_con_writes(路 AK/sim-audioinit,音频 init 写侧域一):JL_WL_AUD->CON0
    # (0xfe2200)整字写记录+读回=最后写值(uart CON1 路 M→F→AA 最终形态;无写时
    # 显式合成 0=路 AM 先例,derived-not-measured)。**识别**:br28.h
    # `JL_WL_AUD_BASE=hs_base+map_adr(0x22,0)`=0xfe2200,单成员 __RW CON0
    # (SDK 头有名)。**写形状**:0x602ea68 entry 0x602ea72 `st.w.modimm
    # [0xfe2200]=1`(当前 3,079,241 停点;WL_AUD 使能,先于一切音频初始化)
    # 与 DAC init 0x6071bca 内 0x6071cbe `st.w.l=r10`(头尾各一次)。**读面**:
    # app 全量 li 扫描(路 AG)读仅低功耗快照 0x6069a2e 一处,不在本前缀;无
    # ready 位轮询。**范围**:仅此一字;条件族(esco/ANC/缺省分支)不进白名单。
    # 运行效果(时钟门/无线音频活动/IRQ)不建模。默认关:0xfe2200 保持
    # unmapped(3,079,241 @ 0x602ea72 前沿形状),legacy 报告逐字节不变。
    if wl_aud_con_writes:
        require(jl_uart_con1_readback,
                'the JL_WL_AUD write records only layer on the route-AA CON1 readback prefix '
                '(the init chain reaches 0x602ea72 only after uart_init completes)')
        options['jl_wl_aud_write_records']=True
    # audio_dac_init_writes(路 AK/sim-audioinit,音频 init 写侧域二,需
    # wl_aud_con_writes 层叠——执行序上 DAC init SFR 簇只在 entry 写放行后
    # 到达):音频 DAC init 写域三字(JL_AUDIO->DAC_CON 0xfe0500=br28.h
    # hs_base+map_adr(0x05,0) 全 __RW;AUD_CON 0xfe0558=+0x58(csfr.h
    # `__u16→unsigned int` 32 位字槽,ADC_CON@+0x38=0xfe0538 与路 AE 互证);
    # JL_ASS->CLK_CON 0xfe0900=hs_base+map_adr(0x09,0) 三成员之首 __RW)。
    # **写形状(0x6071bca SFR 主簇)**:0x6071cbc 整字写(**路 AS 勘误:该
    # 存储是并行对 0x6071cb8 `r1=r0+0x1D00` ∥ `[r0+0]=r1` 的旧 r1 半,实测
    # 落盘 0x0=SR walk 值;原叙事「=0xfe2200 数值巧合」是并行对旧寄存器
    # 误读——0xfe2200 是并行对首半算出的 S2 写基址,0x6071cbe 才用它**)
    # + RMW 对 0x6071ce6 &=~0x100/0x6071cee |=0x100
    # (DAC_CON bit8 开/关);0x6071cea `rmw.and.nmodimm [0xfe0558]&=~0x40`
    # (AUD_CON,无先前写);0x6071cc6 `rmw.or.modimm [0xfe0900]|=1`(JL_ASS
    # CLK_CON 音频子系统时钟位 0,无先前写)。**读回**:照 uart 最终形态一次
    # 做齐——写过返回最后写值;无写时显式合成 0 并标注 derived-not-measured
    # (路 AM 先例;S3/S5 的 RMW 内部读即此情形:0|1=1、0&~0x40=0,平凡);
    # RMW 经 probe_boot 通用 mmio 路径完整执行(内部读=投影值、按位合成、
    # 写回照常落记录,非捷径)。**范围**:恰三字;条件族(esco mic 块
    # 0xfe0a40/0xfe0924/0xfe0538、ANC 图门 0xfe0400、缺省分支 0xfe0a00/
    # 0xfe0a0c/DAC_LEN 写族)本世界不可达(G6 门字 word@0x127364=0 动态复现
    # 钉死;config 88/92 已占用),保持 unmapped 诚实停(**路 AS 修正:
    # 0xfe0538 的 reset 写由域三 `jl_audio_adc_reset_writes` 放行;缺省分支
    # 描述的 0xfe0a00/0xfe0a0c 族之外的 0xfe0a04 DAA_CON1 实测在默认路径
    # 被读——新前沿所在,归下一路**)。**运行效果不建模**
    # (DAC 时钟门/模拟通路/DMA/IRQ68 事件;IRQ68 注册 0x602ec28 只是向量槽
    # RAM 写)——只记录「音频 DAC 初始化配置写已执行」,不扩展为「音频输出
    # 已验证」。默认关:三字保持 unmapped,legacy 报告逐字节不变。
    if audio_dac_init_writes:
        require(wl_aud_con_writes,
                'the audio DAC-init write records only layer on the JL_WL_AUD entry-store '
                'prefix (the 0x6071bca SFR cluster is reached after the 0x602ea72 store)')
        options['jl_audio_dac_init_write_records']=True
    # jl_audio_adc_reset_writes(路 AS/sim-adcreset,音频 init 写侧域三):JL_AUDIO
    # ->ADC_CON(0xfe0538=+0x38,路 AE br28.h 钉死命名+位语义:bit7 IRQ pending
    # 硬件按 DMA 样本置位/bit6 清+重启/bit5 数字使能/bit4 使能门/bits0-3 SR 码)
    # 的 reset 写 [ADC_CON]=0 单字写记录+读回=最后写值(uart CON1 最终形态;无写
    # 时显式合成 0=路 AM 先例,derived-not-measured)。**写形状(路 AS 反汇编
    # 钉死,repo codec+vendor 互证)**:0x60719a6(boot 音频 initcall 0x602ea68
    # 于 0x602eb80 调入的 ADC 节点 reset,设备结构基址 0x1265C0)——guard 字节
    # b[0x1265C0+4673]=**b[0x127801]** 读(0x60719b2,本世界=0;路 AN 叙事的
    # b[0x1267E1] 是手算笔误,指令编码 0x1265C0+0x1241=0x127801,本路勘误)
    # → 0x60719b8 li r3=0xfe0538 → 0x60719be li r4=0 → **0x60719c0 并行对
    # `add.s14.rri.p r3=r1+4596` ∥ 0x60719c4 `st.w [r3+0]=r4`**:第二半以
    # **旧 r3**(0xfe0538)写 0=reset 写,第一半同步把 r3 更新为 hdl 指针
    # (0x1277B4)供随后 RAM 初始化(rep 24×st.w.pin 清 96B/链表头/槽
    # [0x1265C0+308<<2]=hdl/call 0x6064162),尾部 0x60719d6 guard 置 1——
    # **旧寄存器并行语义=FM-1 run3/exact_parallel_stores 既有机制,本写是其
    # 首个 SFR 站点**(3,137,573 停点即此对首写,机器侧零新分发)。**读面**
    # :本前缀零读——AE 全 app 消费者(bit7 分发器 0x602f430/bit4 set_buffer
    # 0x606def0 查重/SR 写读 0x606dee0)都在 IRQ68 交付/esco 打开链上(G6 门
    # 字=0 动态钉死,IRQ68 零交付),默认路径无轮询;投影下 bit7 恒 0=「无
    # ADC 硬件活动」的诚实显形,固件若轮询会如实跑满预算、不发明完成(路 BN
    # fd31 CON 先例)。**为何独立子开关而非并入 audio_dac_init_writes**:AK
    # 域二的边界文本/断言钉死「恰三字+条件族不进白名单」,扩它=改已合并且
    # 三处断言钉死的边界;且 ADC(输入侧 reset 事务,函数 0x60719a6)与 DAC
    # (输出侧 init 簇,函数 0x6071bca)分域命名(codex 分域原则);执行序上
    # reset 写在 S1..S5 之前,依赖边只挂 wl_aud 域一(只开域一+本开关的世界
    # 停在 S1,语义自洽)。**范围**:恰一字 0xfe0538;ADC 域其余(ADC_CON1
    # 0xfe055c/MBG_CON 0xfe0554/MIC_CTL0/1/ADA_CON*)与 AK 条件族保持
    # unmapped 诚实停。**运行效果不建模**(ADC 时钟/模拟前端/DMA/IRQ68)——
    # 只记录「ADC 节点 reset 配置写已执行」,不扩展为「音频输入已验证」。
    # 默认关:0xfe0538 保持 unmapped(3,137,573 @ 0x60719c0 前沿形状),legacy
    # 报告逐字节不变。
    if jl_audio_adc_reset_writes:
        require(wl_aud_con_writes,
                'the JL_AUDIO ADC-reset write records only layer on the JL_WL_AUD entry-store '
                'prefix (the ADC-node reset 0x60719a6 is called after the 0x602ea72 store)')
        options['jl_audio_adc_reset_write_records']=True
    # jl_adda_daa_con1 两开关(路 CV/sim-adda,DAA_CON1=0xfe0a04 首个硬件反馈
    # 依赖的处理,codex 四点定调落地):**分立**——
    # ①`jl_adda_daa_con1_initial_fixture`=显式输入初始 word(值=
    # JL_ADDA_DAA_CON1_INITIAL_PROBE 探针对 {0x0,0xfffffff8} 之一,
    # sensitivity-probe-not-plausible-state,fixture 记录名称/来源/适用场景,
    # **无隐式零默认**;写前读一律返回该字)。
    # ②`jl_adda_daa_con1_config_readback`=已核定配置位维护当前值(真实写入
    # 后更新、后续读用当前状态=最后写值;__RW 先例=uart CON1 路 AA/lp_timer
    # CON 路 AL 同族,但**容器无「合成初始 0」分支**——与 AK/AS 容器的分野,
    # 初值只来自显式 fixture)。
    # 停原边界契约:只开②无①未写入、或开①关②写后读,均按 3,170,362 原边界
    # 停(unmapped_memory 读 0xfe0a04 逐字段一致)。
    # 静态核对(路 CV 反汇编钉死,全文见 br28_model.JLAddaDaaCon1Registers
    # 类注释与 docs/adda-daa-con1.md):RMW 0x606b40e=钳档位(r1>=3→3)→
    # r3=b[0x060A8608+r1](4 档增益码表 {0,4,6,7};勘误:旧叙事 0x060A8548
    # 是笔误)→ 0x606b426 首读 → r0 bit0 块 bits0..2 换表码(bits3..31 保留
    # 旧值)→ r0 bit1 块 bits4..6 换表码 → **0x606b43c 写回在两条件块之外**
    # (未选通道=旧值原样回写,总线写仍发生)→ 返回(不改 r0/r1)。
    # **veneer 0x606db6c 是真调用不是跳转**:[--sp]=rets;call 0x606b40e;
    # pc=[sp++]——fixer 的 rts 回 0x606db72(运行态实测 rets=0x606db72),再
    # 弹出原始调用者返回地址(在 fixer 帧下方栈上,不能把 0x606db72 当返回
    # 地址)。本前缀唯一调用点=0x6071b70(r0=0xFF 双通道、档位 0;另一调用
    # 点=音量同步 walker 0x606db74 的纠偏分支 0x606dc00,r0=单通道位,不在
    # 本前缀;0x606b45c/0x6071d92 两处直调同样不在)。
    # **不一次放行整个 ADDA 读族**(codex 第 2 点):白名单恰一字 0xfe0a04;
    # DAA_CON0 0xfe0a00/DAA_CON2 0xfe0a0c/ADA_CON 族 0xfe0a20../DAC_VL0
    # 0xfe051c 保持 unmapped,后续读逐个分类(配置/状态/测量)。放行后下一
    # 停点=0x606db4c setter 0x606db36 读 DAC_VL0 0xfe051c(配置 RMW,数字
    # 音量,同法归下一路;状态/测量类将停住报告)。运行效果(DAC 模拟通路/
    # 偏置/音量)不建模。默认关:0xfe0a04 保持 unmapped(3,170,362 前沿形状),
    # legacy 报告逐字节不变。
    if jl_adda_daa_con1_initial_fixture is not None or jl_adda_daa_con1_config_readback:
        require(jl_audio_adc_reset_writes,
                'the JL_ADDA DAA_CON1 fixture/readback only layers on the ADC-reset release '
                '(the DAA_CON1 first read 0x606b426 sits in the initcall tail after 0x60719a6)')
        if jl_adda_daa_con1_initial_fixture is not None:
            options['jl_adda_daa_con1_initial_fixture']=jl_adda_daa_con1_initial_fixture
        if jl_adda_daa_con1_config_readback:
            options['jl_adda_daa_con1_config_readback']=True
    # jl_audio_dac_vl0_readback(路 CW/S19 批 0b/sim-s19b0):3,170,379 停点
    # 0x606db4c(setter 0x606db36 并行对第二半 `r3=[r2+0]` 对 0xfe051c 的首读)
    # 按 CV 裁定的「配置」分类放行——**读值参与度判定(本路核心)**:读值只进
    # 两个通道块的半字 insert 组装,不进任何分支(分支只测 r0 通道位);且本
    # 前缀唯一调用点 0x6071b76 实参 r0=0xFF(双通道全选)/r1=0——第二块的
    # `r3=r3.l(u)` 把第一块从读值携带的全部位清零,**写回恒 0x0 与初值逐位
    # 无关**(读值只存在于两块之间的瞬态 r3)。按批 0b 判据「只影响数值不
    # 影响分支→合成 0+敏感性探针」:容器=AK 域一/二/三同族(写记录+读回=
    # 最后写值+无写时显式合成初始字 0,路 AM 先例,derived-not-measured),
    # **不立 CV 式显式 fixture 开关**(DAA_CON1 需要fixture 是其保留位被写
    # 持久携带);敏感性探针=一次性 ones 字 0xffffffff 分叉(首块瞬态
    # 0xffff0000 仍被 r3=r3.l(u) 清零、写回同 0x0、同停点同步数=观察路径对
    # 初值不敏感,结果钉进路叙事与单测)。**cap 槽勘误**:上限装载
    # [0x1265C0+1883*4]=**0x12832C**(运行态 r4=0x3a98;CV/BO 叙事 0x1272AC
    # 是算术笔误——0x1272AC 需下标 827,li.s16 n=1883 钉死 1883*4=0x1D6C)。
    # 白名单恰一字 0xfe051c;JL_AUDIO 数字域(15 个 li 站点:DAC_LEN/almk/
    # DMA)保持 unmapped(路 BO §5:D1 后另立证据包)。运行效果(数字音量/
    # 模拟通路/输出)不建模。默认关:0xfe051c 保持 unmapped(3,170,379 前沿
    # 形状),legacy 报告逐字节不变。
    if jl_audio_dac_vl0_readback:
        require(jl_adda_daa_con1_initial_fixture is not None,
                'the JL_AUDIO DAC_VL0 readback only layers on the DAA_CON1 initial-fixture '
                'release (the setter call 0x6071b76 sits immediately after the DAA_CON1 '
                'fixer call 0x6071b70 — without the fixture the world still stops at '
                '0x606b426)')
        options['jl_audio_dac_vl0_readback']=True
    # jl_uart_con1_readback(路 AA/sim-con1rb,CON1 读回真决策点三选一=方案 (a)
    # 读回=最后写值投影):把 JL_UART CON1(+4,三基址对称)从路 M 的纯写白名单
    # 升级为「写记录+读回=最后写值」——lp_timer CON(路 AL)/fd31 ADC CON(路 BN)
    # 同族先例。**停点语义(SDK uart_v1.c uart_fifo_reset 序列钉死)**:0x60340d8
    # 复合 RMW `rmw.and.nmodimm [r0+4]&=0xFFFFFFEF` 的内部读是**真实消费**(读值
    # 参与写回);序列=临界区内 CON1|=16(bit4=RX DMA 重启位)→写 RXSADR/RXEADR/
    # RXCNT(RAM 装载的 RX DMA 地址/计数)→CON1&=-17(bit4 清零=重启)。**证据**:
    # ①br28.h JL_UART_TypeDef CON1 __RW(布局分歧不影响标注;该头标注有区分度);
    # ②路 M 白名单已记录 CON1=0 的整字写(0x603446c),最后写值已知 → RMW 读 0、
    # 写回 0&0xFFFFFFEF=0,行为平凡、零发明;③配置面 RMW,非状态轮询。**条件
    # 假设(codex 风险点,derived-not-measured)**:__RW 不能证明 CON1 无硬件
    # 自更新/自清零位——「读回=软件配置值」是显式假设,bit4 之外的位若真硬件
    # 会自更新则投影失真(报告 assumptions 全文标注)。**RMW 走完整路径**:内部
    # 读=投影值、按位合成、写回照常落写记录(probe_boot 通用 mmio 路径,非捷径)。
    # **只扩 CON1**:BAUD/OTCNT/RXSADR/RXEADR/RXCNT/CON2 读回仍严格停(无消费者,
    # 不发明值);CON0 读侧已有 jl_uart_con_projection 保持不动;从未写过(如
    # UART1/2)仍诚实停。默认关:CON1 读回保持 Blocked(3,073,918 @ 0x60340d8
    # 前沿形状),legacy 报告逐字节不变。
    if jl_uart_con1_readback:
        require(jl_uart_init_writes,
                'the JL_UART CON1 readback projection requires the route-M init write records')
        options['jl_uart_con1_readback']=True
    # jl_uart_tx_start_writes(路 UARTTX/sim-uarttx,UART TX 启动写白名单):
    # TXADR(+0x14)/TXCNT(+0x18)(三基址对称 0xfd2014/18、0xfd2114/18、
    # 0xfd2214/18)按路 M write-only 整字记录先例放行。**唯二静态站点**=
    # 发送函数 0x603413c 的 DMA 启动对 0x603425a `[r7+20]=r5`(TXADR=会话
    # 缓冲)/0x603425c `[r7+24]=r4`(TXCNT=帧构建器 0x6060254 的包络总长
    # {0x55,0xAA,len,payload...,checksum}=payload+4);全 app+ROM LE32 字面量
    # 扫描六地址零命中(访问全经基址寄存器相对寻径),**零静态读者**——读回/
    # RMW 显式严格停(jl_uart_tx_word_readback_not_modeled)。**完成语义钉死
    # (路 UARTTX park 世界探针)**:轮询档(b[obj+80]==0,实测档)同步发送
    # 路径 TXCNT 写后读 RAM 标志即返回(0x6034266→0x6034172),不等待任何
    # 完成位;启动前等待环 0x60341a6 轮询 CON0 bit0(uart_is_idle)但经
    # 0x12cb08 返 0 出口立即退出(0x6034200)——**不发明完成投影**(无读时
    # 投影位、无 advance 置位、无 IRQ12 交付件);完成的真实消费面=中断域
    # (UART IRQ12/13/14 handler 0x60354e0 TX-done 支,CON0 bit15+bit2 键控)
    # 与等待环 CON0 bit0,均不建模:CON0 保持路 F「读回=最后软件写值」
    # (bit0 停留软件终值,不模拟硬件清零)。依赖(执行序):TXADR/TXCNT 只
    # 在 CON0 bit0 读(路 F jl_uart_con)与 CON1|=0x10 RMW(路 M+路 AA)放行
    # 后到达。运行效果(TX DMA 数据流/IRQ12)不建模——只记录「TX 启动信号
    # 写已执行」,不扩展为「UART 发送已验证」。默认关:六字保持 unmapped
    # (TX relay 0x603425a 诚实停的 CALLIMPL 前沿形状),legacy 报告逐字节不变。
    if jl_uart_tx_start_writes:
        require(jl_uart_con,
                'the JL_UART TX start write records only layer on the route-F CON0 '
                'projection (the send path reads CON0 bit0 via uart_is_idle before '
                'reaching the TX start pair)')
        require(jl_uart_con1_readback,
                'the JL_UART TX start write records only layer on the route-AA CON1 '
                'readback prefix (the TX start sequence RMWs CON1 |= 0x10 at 0x60340f6 '
                'before TXADR/TXCNT)')
        options['jl_uart_tx_start_write_records']=True
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
    # audio_adc_dma_delivery(路 D2AI/sim-d2ai,预研 §7 步 1;INJ-01/02 执行
    # 载体):ADC DMA 五字容器交付半边——读回投影(五字+SRN 0xfe0550)+
    # ADC_CON bit7 pending 叠加/|=0x40 W1C 联动+produce_block(DMA 完成事件,
    # 唯一合成输入=样本字节)+advance() IRQ68 扫描(逐 pending 片至多一次
    # IrqDeliveryRequest)+机器侧最小进入/RTI 投影(嵌套仅限 timer0 在途且
    # 真实 ICFG 优先级严格更高)。前置:五字容器+ADC reset 写域(依赖边=
    # 场景推进顺序)。默认关:行为与路 JLA write-only 半边逐字节一致。
    if audio_adc_dma_delivery:
        require(jl_audio_adc_dma_writes and jl_audio_adc_reset_writes,
                'the audio ADC DMA delivery requires the five-word records and the '
                'ADC-reset write records')
        machine_options['audio_irq_entry']=True
        machine_options['audio_irq_rti']=True
        options['audio_adc_dma_delivery']=True
        options['audio_adc_dma_delivery_requests']=True
        extra['audio_irq_frames']=dict(AUDIO_IRQ_FRAMES)
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
    # core_tick_prd_readback(路 BQ/S19 冲刺批 2 一案,opt-in 默认关):3,747,578
    # 停点 0x12b0c2 的三查分类——①有效地址:r5=0xffe0f0(0x12b0bc li 立即数
    # 16769264)+4=0xffe0f4=CORE_TICK_BASES[0]+8=PRD,核 0(停点 stop_core=0,
    # helper 硬编码无 cnum 偏移),宽度 4(ld.w);②异常位置:CoreTickStopped
    # .read() 头部通用 overflow guard(先于 offset 分派,CON/CNT/PRD 同拦)
    # ——判定表第一行「guard 挡 PRD」;③PRD 最近真实写入=tick_timer_init
    # 0x60661e8 的 PRD store(0x606622a `[r5+8]=r0`,换算 [0x126900](=0x126640+704
    # 输入时钟全局)/1000*r4-1,值
    # 1199999),运行中重编程被 active_core_tick_reconfiguration_unverified
    # 挡住(3.75M 步零发生),prd 恒等最近写入。「恢复后自然交付」由既有
    # BF 机制+单测钉死(pending 期间每步重检,IPMASK 回落即抛请求——
    # test_pending_rechecks_eligibility_after_critical_section 与
    # core_tick_irq3_delivery_probe 探针场景)=只有读侧问题,故按 codex 定调
    # 一案先行:unresolved 窗内只放行 PRD 读=回放最近真实写入,缺失继续停,
    # 不改 CNT/pending/ACK/计时相位,不给默认值;第二案 held 重新仲裁仅在
    # 放行后复现「IPMASK 恢复后漏仲裁」才加。依赖边挂 core_tick_irq3_delivery
    # (无交付层的世界上首溢出即终态停,读侧 unresolved 窗不存在)。
    if core_tick_prd_readback:
        require(core_tick_irq3_delivery,
                'the core-tick PRD readback only layers on the IRQ3 delivery projection '
                '(the unresolved-overflow read window exists only while a held overflow '
                'waits out the IPMASK mask)')
        options['core_tick_prd_readback']=True
    # core_tick_cnt_held_snapshot(路 B3/S19 批 2 续一案,opt-in 默认关):3,747,585
    # 停点 0x12b0e8 的 CNT 读按 codex 定调落地——①同核同宽:helper 0x12b0ba 的
    # retry 环在核 0 上 ld.w 读 0xffe0f0=CORE_TICK_BASES[0]+4=CNT(宽 4);
    # ②明确 held 状态:第 12 次溢出 unresolved(屏蔽窗内 BF 每步重检,窗口
    # 从未结束);③模型现状=溢出后 advance 不再推进,cnt 冻结在溢出时刻
    # divmod 余数(该场景对齐恰 0)——「溢出后 CNT 冻结」是模型 artefact 的
    # 显式假设(assumed-not-measured;硬件倾向回绕继续计数但无 BR28 证据),
    # 开关只放行该一格读=返回实际存储快照,非 PRD-1/非周期末值/非合成默认;
    # ④读无副作用(不清 pending、不重装载、不动在途/ACK/jiffies,单测钉死),
    # CON(offset 0)同窗保持原严格停。依赖边挂 core_tick_prd_readback(无
    # PRD 放行的世界停在 0x12b0c2,CNT 停点不存在)。
    if core_tick_cnt_held_snapshot:
        require(core_tick_prd_readback,
                'the core-tick CNT held-snapshot projection only layers on the PRD '
                'readback release (the CNT read stop 0x12b0e8 exists only inside the '
                'retry loop the PRD release at 0x12b0c2 admits)')
        options['core_tick_cnt_held_snapshot']=True
    # core_tick_freerun_edge_period(路 JLEQ 续二,opt-in 默认关):29,229,183
    # 停点 0x6065834——tick 重启函数 0x6065806(被 [0x60004be) 于 0x600050c
    # 调用,r0=[0x1265C0+704] tick 时钟)尾段:若 CON&1 先读 CNT 折算 µs 存
    # [0x1265C8](收割逝时),再 CON=0→CNT=0→PRD=0xFFFFFFFF→CON=1——core
    # tick 切「自由运行 µs 计数器、溢出关断」形态,模型使能路径的边沿周期
    # 守卫(prd in (0,0xffffffff))拒之。开关恰放行 prd==0xFFFFFFFF 一格
    # (prd==0 恒拒),计数公式 period=prd+1=2^32 不变、任何受建模视界内零
    # 到期,读回/ACK/pending 语义全不动;硅上最大周期行为未测量
    # (assumed-not-measured)。依赖边挂 jl_eq_init_writes=场景推进顺序
    # (EQ 初始化放行后链才到达 tick 重启),非硬件依赖。
    if core_tick_freerun_edge_period:
        require(jl_eq_init_writes,
                'the core-tick freerun edge-period release only layers on the JL_EQ '
                'init write records (the tick-restart call 0x600050c is reached only '
                'after the EQ initialization completes — without it the world still '
                'stops at the 0x6065834 edge enable; the dependency encodes THIS '
                'execution order, not a hardware dependency)')
        options['core_tick_freerun_edge_period']=True
    # 敏感性探针值(probe_only,CV 双探针先例):0 vs 周期末值 PRD-1 两分叉,
    # 记录观察路径首次分叉;非 None 时 unresolved 窗内 CNT 读返回该值。
    if core_tick_cnt_snapshot_probe_value is not None:
        require(core_tick_cnt_held_snapshot,
                'the core-tick CNT snapshot sensitivity probe rides on the held-snapshot '
                'projection (it substitutes the snapshot value, it is not a standalone release)')
        options['core_tick_cnt_snapshot_probe_value']=core_tick_cnt_snapshot_probe_value
    # jl_adda_analog_family(路 B3/S19 批 2 续,opt-in 默认关):4,521,083 停点
    # 0x606ac42(音频 DAC 模拟 init 函数 0x606abf0 首条 DAA_CON0 bit13 清位
    # RMW 的内部读)按消费面判定=AM 容器族——静态全函数枚举白名单恰五字
    # (0xfe0a00/08/0c/48/4c,+4 DAA_CON1 由路 CV 容器独占),读值只进写回
    # 组装零分支消费,合成初始 0+读回=当前值,derived-not-measured。
    if jl_adda_analog_family:
        options['jl_adda_analog_family']=True
    # jl_audio_dac_tm_readback(路 DT/S19 批 2 续二,opt-in 默认关):19,139,239
    # 停点 0x606abbe——DAC 启动函数 0x6071e74 尾段 0x60720ca call 半字 field
    # setter 0x606ab92(12 路 tbb:0/2→TM0 低半、1/3→TM1 低半、4..7→裸 rts、
    # 8/10→TM0 高半〔停点 handler,读 [0xfe0524] 取低半保底〕、9/11→TM1 高半)
    # 的 RMW 内部读。读值只进写回组装零分支消费(分支只测入口 selector),
    # **全 app 无 setter 外读者**(逐 2 字节位置独立解码:0xfe0524 唯一 li 在
    # setter 内部 0x606ab96、0xfe0528 无 li、15 个 0xfe0500 基址站点无
    # +0x24/+0x28 访问;10 个调用点全在音频驱动层)→B3 交接「getter/硬件
    # 维护时间标记被读」预判撤销,按 CV/CW 判据归 AM 容器族:合成初始 0+
    # 读回=当前值(codex 定调:初值来源 synthetic_zero 具名登记——保留半字
    # 持久携带=CV 性质初值依赖,无 setter 外读者故不单设 fixture 开关;
    # 本轮不做 derived、不为将来预埋时间标记推导语义;恰白名单 setter 静态
    # 地址集 {0xfe0524,0xfe0528},只服务对齐 32 位字;新读者须重审)。
    # 依赖边挂 jl_adda_analog_family(执行序:模拟 init+OS 等待族放行后
    # DAC 启动尾段才到达;TM 语义 time mark/单位/有符号不解释,保留原名)。
    if jl_audio_dac_tm_readback:
        require(jl_adda_analog_family,
                'the JL_AUDIO DAC_TM readback container only layers on the JL_ADDA '
                'analog-family release (the DAC start tail 0x60720ca is reached only '
                'after the analog init and the OS-wait family — without it the world '
                'still stops at 0x606abbe)')
        options['jl_audio_dac_tm_readback']=True
    # 敏感性探针值(probe_only,CV 双探针先例):TM0(0xfe0524)初值一次性
    # 0xffffffff 替换(只注初值,后续读回真实写回值;TM1 不扰动保归因干净),
    # 断言两臂写回 0xFDA8_0000/0xFDA8_FFFF(异或 0xFFFF)+执行轨迹全同。
    if jl_audio_dac_tm_probe_value is not None:
        require(jl_audio_dac_tm_readback,
                'the JL_AUDIO DAC_TM sensitivity probe rides on the DAC_TM readback '
                'container (it substitutes the TM0 initial word once, it is not a '
                'standalone release)')
        options['jl_audio_dac_tm_probe_value']=jl_audio_dac_tm_probe_value
    # jl_adda_ada_family(路 ADA/S19 冲刺批 2 续三,opt-in 默认关):21,798,206
    # 停点 0x606b5ee——ADA 配置函数 0x606b4d0..0x606c3a8(8 路 tbb selector+
    # 二级 tbh)首条 ADA_CON0 bit5 清位 RMW 的内部读。静态全图(逐 2 字节
    # 独立解码枚举 li 常量+站点窗口数据流核查):ADA 子族窗 0xfe0a20..0xfe0a44
    # 全部触达=7 个直接 li 基址站点(9 实例)——配置函数 309 原子 RMW+51 ld
    # (读值全部 and/or/insert→st 组装零分支)、close 族 0x606f4c0/0x606f438
    # 纯清位零装载、field setter 0x606c3b0/0x606de9c/0x606ee9a 通道掩码门控
    # 组装——按 B3/DT 判据归 AM 容器族:合成初始 0+读回=当前值(读值只进
    # 写回组装,初值不进观察路径)。白名单恰九字(已证实触达;0xfe0a44
    # ADA_CON9 零触达留 unmapped,新读者重审——同址新读者亦须重审:地址白名
    # 单只拦新地址)。**新开关分立而非扩 jl_adda_analog_family 的 WORDS**:
    # 直接扩五字容器会静默改变已开场景服务面,违反「新开关默认关 legacy
    # 逐字节不变」红线(codex 定调第三点)。依赖边挂 jl_audio_dac_tm_readback
    # =当前执行路径先后关系(TM 放行后链才到达 0x606b5ee),非 ADA 在硬件
    # 上依赖 TM、亦非其他调用路径的必要条件(codex 措辞)。close 族清位届时
    # 服务容器存储的合成配置字——不表示真实模拟通道已关闭/硬件状态已清除/
    # 任何时序已完成。布局勘误(audio_anc.c 读 ADA_CON8 四通道 5 位 mic 增益
    # 与 0x606de9c/0x606ee9a 增益 setter 互证):0xfe0a48/0xfe0a4c 按类型布局
    # 是 ADDA_CON0/ADDA_CON1,B3 报告文本旧标注不动(legacy 字节钉死),勘误
    # 在本容器报告登记。
    if jl_adda_ada_family:
        require(jl_audio_dac_tm_readback,
                'the JL_ADDA ADA-family container only layers on the JL_AUDIO DAC_TM '
                'release (the ADA config function 0x606b4d0 call site 0x6072144 is '
                'reached in the DAC start tail after the TM writes — without the TM '
                'container the world still stops at 0x606abbe; the dependency encodes '
                'THIS execution order, not a hardware dependency)')
        options['jl_adda_ada_family']=True
    # jl_anc_writes(路 D2ANC,opt-in 默认关):JL_ANC 硬件滤波块 44 字容器
    # (0xfe0400..0xfe04f4;22 个 li 常量传播扫描字+22 个表驱动滤波族运行时
    # 变址墙钉字(0x606cfb2 域,索引来自滤波模式表);窗外零触达留 unmapped)。消费者=INJ-05 ANC 场景的 post_stop 直调段
    # (audio_anc_run 0x601c8de 停-重配-启链 0x606ee26→0x606e9e8→0x606ebe0+
    # 滤波配置族);boot 前缀零触达(D2AUD §6.2 运行时自证)=开启不动前沿。
    # 依赖边挂 audio_adc_dma_delivery=场景先后关系(音频注入世界就绪后 ANC
    # 场景才触达该块),非硬件依赖。
    if jl_anc_writes:
        require(audio_adc_dma_delivery,
                'the JL_ANC 44-word container only layers on the audio ADC DMA '
                'delivery release (the ANC scene that first touches the block is '
                'the post-stop direct-call world built on the audio injection '
                'stack; the dependency encodes THIS scenario order, not a '
                'hardware dependency)')
        options['jl_anc_writes']=True
    # jl_clock_con3_readback(路 D2ANC,opt-in 默认关):JL_CLOCK CLK_CON3(0xfd0024)
    # 单字容器——ANC 重配链 0x606c844 的 ADC 时钟选择字(全 app 10 站点,读值
    # 只进写回组装;0xfd0028 零触达留 unmapped)。boot 前缀零触达=开启不动
    # 前沿;依赖边挂 jl_anc_writes=场景先后关系(同一 ANC 场景的链内先后)。
    if jl_clock_con3_readback:
        require(jl_anc_writes,
                'the JL_CLOCK CLK_CON3 container only layers on the JL_ANC release '
                '(the sole consumer is the ANC reconfig chain 0x606c844 reached in '
                'the ANC scene; the dependency encodes THIS scenario order, not a '
                'hardware dependency)')
        options['jl_clock_con3_readback']=True
    # jl_pled_con3_readback(路 FD610C,opt-in 默认关):JL_PLED CON3(0xfd610c)
    # 单字容器——boot 前缀 LED 驱动 0x6019458 停点的配置 RMW 读(清 bit5,
    # 41,847,303 前沿)。CON3 全 6 站点均为原子 RMW 配置写、读值只进
    # old&mask/old|mask 写回组装、零分支零轮询(codex 定调:单字最小假设,
    # CON0 save-restore 位消费/其余 17 字留 unmapped 到各自到达)。无依赖
    # (停点在 boot 前缀,不叠任何其他场景)。
    if jl_pled_con3_readback:
        options['jl_pled_con3_readback']=True
    # jl_pled_con0_initial_fixture(路 FD610C-con0,opt-in 默认 None=关):JL_PLED
    # CON0(0xfd6100)单字具名初态容器——41,847,806 停点 0x60194b4 的
    # save-restore 首读(r6=[0xfd6100]→清 bit0→if(r6&1)恢复)。CON0 是分支
    # 消费者(bit0 恢复门控/bit1 分支/bits4-7 查表),不能照搬 CON3 合成 0;
    # codex 三选一(2026-11-17)定调 (a):具名整字初态 fixture,条件主臂
    # 0x00000000、敏感性对照臂 0x00000001。显式读/RMW/整字写共享同一状态,
    # 走通用 mmio 路径(零新分发)。无依赖(停点在 boot 前缀)。
    if jl_pled_con0_initial_fixture is not None:
        options['jl_pled_con0_initial_fixture']=jl_pled_con0_initial_fixture
    # p2m_clk_con0_config_rmw(路 P2MCLK,opt-in 默认关):P11_SYSTEM
    # P2M_CLK_CON0(0xf2a610)单字配置 RMW 容器——41,848,382 停点 0x6019508
    # (LED_CLK_SEL(3) helper 0x6019502 首条清 bits5-7 RMW,SDK p11_app.h L117
    # 宏逐字对应)的内部读。全 app 唯一引用=该 helper(指令级线性反汇编+
    # 字面值/偏移表/函数指针/ROM 全扫描),两条立即数 RMW 读值只进写回组装
    # 零分支零轮询,SDK GET_LED_CLK_SEL(L118 读值宏)无人引用。codex 三选一
    # (2026-11-17)定调 (a) CON3 式:合成初始 0+RMW 内部读回=当前值,显式读
    # 一律拒绝(零显式读者=能力最小化,不为假想读者开放),只服务已观测
    # rmw.and.nmodimm/rmw.or.modimm 两种形状。无依赖(停点在 boot 前缀,
    # 与邮箱 608/60c/618 地址集互斥不重叠)。
    if p2m_clk_con0_config_rmw:
        options['p2m_clk_con0_config_rmw']=True
    # jl_iomc_map_fixture(批 B JL_IOMC,opt-in 默认 None=关):映射表七字
    # (OCH_CON0..2 0xfd6018/1c/20+ICH_CON0..3 0xfd6024/28/2c/30)具名 fixture
    # 容器——首写前读=具名初态(条件主臂 JL_IOMC_MAP_INITIAL_VALUE=0=空映射
    # 表,scout2 §4 三选一 (b)),写后读=最后写值;显式读/RMW/整字写共享同一
    # 状态走通用 mmio 路径(读值进比较分支/分支+跨地址传播,read() 返回值
    # 而非拒绝,零新分发)。CON0 0xfd6014 不在此容器(已是活容器:platform_
    # case seed IOMAP_CON0=0)。ICH=0 的清 JL_IMAP 副作用是固件自然演进
    # (清 0xfd5c00 在路 V 白名单内幂等),模型不额外模拟。批 B 触达面全不
    # 在 LED→S19 立即路径=前缀零触达,开启不动前沿。无依赖(与批 A 容器/
    # 触达面零交集,独立开关独立批)。
    if jl_iomc_map_fixture is not None:
        options['jl_iomc_map_fixture']=jl_iomc_map_fixture
    # jl_pled_config_words(路 BATCHA,攒批,opt-in 默认关):JL_PLED 配置字族
    # 13 字批容器——41,850,980 停点 0x60195f8(LED 全字重配 0x60195e2 的
    # CON1 整字清零)起的 LED 配置链(同函数 CON2=0、PRD 分频 0x6019552、
    # DUTY 配置器 0x6019574、BRI 六连写 0x6019ab0、CON1/CON2 insert
    # 0x6019af2、效果机 A1 路 0x601a3ce..da)全部未建模配置字一次放行。
    # 消费链(落地前 vendor objdump 复核修正 scout「无读者」表述):
    # CON1/CON2 活路径显式读只进写回组装(分支全在实参)且先写后读→写后
    # 读回、首写前拒读(无合成初始字,写零按写次数计已写);BRI×6/
    # PRD_DIVL/DUTY×4 活路径零读,唯一读者在 IRQ41 handler 域(算术/分支)
    # →读一律拒绝+该域入口(0x6034b06/0x6034aba)执行前 canary。RMW 零新
    # 分发(CON1/CON2 走通用 mmio 路径,CON0 先例)。新开关分立不扩
    # CON0/CON3/P2MCLK 旧容器;无依赖(停点在 boot 前缀 LED 域内)。
    if jl_pled_config_words:
        options['jl_pled_config_words']=True
    # 敏感性探针值(probe_only):ADA_CON0(0xfe0a20)初值一次性 0xffffffff
    # 替换(只注初值,后续读回真实写回值;其余八字不扰动保归因干净)。
    # codex 钉子:断言非零合成初值经停点 RMW 只清 bit5——仅测初值零会漏掉
    # RMW 内部读/写不同容器状态的实现错误。
    if jl_adda_ada_probe_value is not None:
        require(jl_adda_ada_family,
                'the JL_ADDA ADA sensitivity probe rides on the ADA family container '
                '(it substitutes the ADA_CON0 initial word once, it is not a '
                'standalone release)')
        options['jl_adda_ada_probe_value']=jl_adda_ada_probe_value
    # p11_lpvad_config(路 LPV,opt-in 默认关):21,798,257 停点 0x606b68e——
    # ADA 配置函数 0x606b4d0 顺序段尾的 `li r1=0xF2B404`(0x606b688,全 app
    # 唯一站点)之后首条 `rmw.or.modimm [r1+0] |= 0x10000000`(bit28 置位原子
    # RMW)的内部读撞 unmapped。静态访问面(全 app 逐 2 字节独立解码+数据
    # 区字面值扫描+邻组站点数据流核查,方法照 ADA/DT):0xf2b400..0xf2b434
    # 窗唯一 li 站点即 0x606b688,唯一访问即停点本条(内部读零 GPR/零分支/
    # 零 call 参数;窗内无 ld.w 显式读);数据区无独立常量池字面值;邻组
    # 0xf2b21c 站点(0x6068c20)两写即返回不触达。按 B3/DT/ADA 判据+codex
    # 定调归 AM 式合成配置族单字容器:合成初始 0+读回=当前值(两项均为
    # 显式模型假设,非复位值非静态推导);白名单恰一字,其余 12 字零触达
    # 留 unmapped,同址新读者(新 ld/新 PC/新掩码/其他核)亦须重审(codex:
    # 地址白名单只拦新地址)。**新开关分立而非扩 p11_window_projection
    # 白名单**:改已开开关服务面=改契约+「纯写记录读回 Blocked」族与配置
    # 读回语义冲突(codex 定调);lp_timer_con_readback 的「未写过严格停」
    # 在首访即读形状下无解(停点自身即首次访问)。依赖边挂
    # jl_adda_ada_family=当前执行路径先后关系(ADA 放行后链才到达停点),
    # 非 LPVAD 在硬件上依赖 ADDA(codex 措辞)。跨域限制(codex 措辞):容器
    # 只记录主核侧合成配置字更新,不模拟 q32s 访问/LPVAD 检测/跨域可见性/
    # 读写副作用——主核越过停点不表示真实 LPVAD 已配置或运行;更弱的
    # 「只记 bit28 置位其余位未知」替代案未采纳(需新的未知字传播机制,
    # 违反最小改动;假设强度区别在容器报告登记)。
    if p11_lpvad_config:
        require(jl_adda_ada_family,
                'the P11 LPVAD config container only layers on the JL_ADDA ADA-family '
                'release (the stop 0x606b68e sits in the ADA config function 0x606b4d0 '
                'sequential tail reached only after the ADA container serves the window '
                '— without the ADA release the world still stops at 0x606b5ee; the '
                'dependency encodes THIS execution order, not a hardware dependency)')
        options['p11_lpvad_config']=True
    # jl_audio_adc_dma_writes(路 JLA,opt-in 默认关):21,800,325 停点
    # 0x606df1e——set_buffer 0x606dee4 并行对 mul.rr.p 第二半 store
    # `[r4+4]=r0`(r4=0xfe0538)写 0xfe053c=ADR,是 D2-b 注入蓝图预案五写
    # {+0x3c ADR/+0x40 LEN/+0x44 PNS/+0x48 HWP=0/+0x4c SRP=0} 之首;五写同
    # 基本块连续(0x606df20..30,中间只有 uextra/lsr 算术),写完一条 RAM 写
    # (缓冲指针槽)即返回——纯写集一次打包(AK 先例)。静态访问面(全 app
    # 逐 2 字节独立解码 li 枚举+站点线性窗数据流解析+数据区字面值扫描,codex
    # 独立复跑互证):0xfe0500..0xfe0560 窗 33 个 li 基址站点、五字访问解析恰
    # 9 条=set_buffer 五写+audio_adc_irq_handler [0x6072628,0x6072700)(IRQ68
    # 分发器 0x602f444 直调)读 ADR/PNS/SRP 三条+写 PNS 一条;数据区零独立
    # 字面值(33 命中全=li.u32 立即数字段);handler 当前前缀不可达(IRQ68
    # 交付未建模),读的是运行期 DMA 指针状态,属 D2-b 后域。codex 定调选
    # (a) write-only 记录恰五字(BM DAC DMA 四字同族先例);拒绝 (b) 合成读
    # 回的理由=当前推进不需要任何读回,加读回会提前承诺尚未建立的消费语义;
    # 0xfe0550(handler 尾 SRN 读通知字)属另一段消费事务不收;同址新写者/
    # 新读 PC/新形状/其他核须重审(地址白名单只拦地址);子字/错位不服务;
    # DMA 搬运/指针推进/SRN 副作用/pending/IRQ68/缓存一致性一律不建模。
    # 依赖边挂 p11_lpvad_config=当前条件场景在 LPV 放行后才到达 ADC 缓冲
    # 配置首写(codex 措辞:记录场景推进顺序,不表示 ADC DMA 硬件、寄存器
    # 写记录或所有 set_buffer 调用依赖 LPVAD;模型类独立可用)。默认关:
    # 五字保持 unmapped(21,800,325 前沿形状),legacy 逐字节不变。
    if jl_audio_adc_dma_writes:
        require(p11_lpvad_config,
                'the JL_AUDIO ADC DMA write records only layer on the P11 LPVAD release '
                '(the current conditional scenario reaches the first ADC buffer-config '
                'store only after the LPVAD container releases the 21,798,257 stop — '
                'without it the world still stops at 0x606b68e; the dependency records '
                'THIS scenario advance order, not a hardware dependency of the ADC DMA '
                'registers or of every set_buffer call on LPVAD (codex wording))')
        options['jl_audio_adc_dma_writes']=True
    # jl_adc_start_config(路 ASS,opt-in 默认关):21,800,455 停点 0x606e044
    # ——ADC 启动函数 0x606df66 的 `rmw.or.modimm [r0+0]|=0x2` 内部读
    # 0xfe0904=JL_ASS->MIC_CTL0;地址 r0=r5+976 构造自 r5=0xfe0534(li
    # @0x606e032),全 app 无直接 li 站点(间接构造地址的首个停点实证,
    # li 枚举法免疫)。基址+偏移传播解析法(~/tmp/ass-resolve.py:356 个
    # li 锚点装载 [0xfd0000,0xfe0a00) 常量,每锚点线性窗 500 条抽象解释
    # 跟踪全部 GPR 常量+寄存器间传播)扫描两窗(JL_ASS 0xfe0900..0xfe0910/
    # JL_AUDIO 尾 0xfe0550..0xfe0560)证实四字 {0xfe0904 MIC_CTL0/0xfe0908
    # MIC_CTL1/0xfe0554 MBG_CON/0xfe055c ADC_CON1} 访问面全部为配置 RMW/
    # insert 写回组装形状、读值零分支消费(B3/DT/ADA/LPV 判据;0xfe0908
    # 另有前缀不可达直接 li 族 0x606c8ca=D2-b C2 链重 mux,同形状);数据区
    # 4 字节字面值扫描零独立命中(值域内仅两处代码区指令字节重叠已排除)。
    # codex 定调:AM 式合成配置读回容器恰四字,合成初始 0+读回=当前值(两
    # 项显式模型假设);0xfe0900/0xfe0558 留 AK 容器(地址集互斥,含其
    # 0x606a70e 就绪检查分支读者等将来按 AK 语义服务)、SRN 0xfe0550 留
    # unmapped(JLA 边界)、0xfe090c/0xfe0910/0xfe0560 零触达不进;同址
    # 新读者(新 ld/新 RMW 掩码/新 PC/其他核)须重审(地址白名单只拦地址);
    # 子字/错位不服务;跨域限制:不模拟 q32s 访问/ADC 捕获/跨域可见性/
    # 读写副作用——主核越过停点不表示真实 MIC_CTL/MBG_CON/ADC_CON1 已
    # 生效或音频输入可用;完备性措辞:线性窗锚点法不覆盖跨函数寄存器传递
    # /RAM 指针装载(字面值扫描零命中已证该来源为空)/ROM/q32s/其他核。
    # 依赖边挂 jl_audio_adc_dma_writes=当前条件场景推进顺序(JLA 放行后
    # 链才到达停点)非硬件依赖(codex 措辞),模型类独立可用。默认关:四字
    # 保持 unmapped(21,800,455 前沿形状),legacy 逐字节不变。
    if jl_adc_start_config:
        require(jl_audio_adc_dma_writes,
                'the JL ADC-start config container only layers on the route-JLA release '
                '(the current conditional scenario reaches the ADC start function '
                '0x606df66 and its MIC_CTL0 RMW only after the five set_buffer stores '
                'complete — without the JLA release the world still stops at '
                '0x606df1e; the dependency records THIS scenario advance order, not a '
                'hardware dependency of the JL_ASS/JL_AUDIO config registers on the '
                'ADC DMA buffer words (codex wording))')
        options['jl_adc_start_config']=True
    # 敏感性探针值(probe_only,codex 建议,CV/ADA 先例):MIC_CTL0(0xfe0904)
    # 初值一次性 0xffffffff 替换;零臂写回 0x2/ones 臂写回 0xfffffffe。
    if jl_adc_start_probe_value is not None:
        require(jl_adc_start_config,
                'the JL ADC-start probe value only applies with the ADC-start config '
                'container enabled')
        options['jl_adc_start_probe_value']=jl_adc_start_probe_value
    # event_clock(路 CW/S19 批 0b/sim-s19b0,BG §4.2 草案落地):把机器侧
    # 事件时钟投影(docs/event-clock.md 最小契约,BQ 的 G1 min() 框架)接进
    # 条件栈——机器/模型零新代码,本参数只是 machine_options 接线。层属
    # conditional 的依据(BK §3.3/BM 总装图):条件路径上第一个事件时钟
    # 消费者是 W2[0] 的 3×os_time_dly(2)=6 tick 等待(BA #2 行「6 tick 事件
    # 时钟」),不开则批 0b 放行 DAC_VL0 后前沿停在 W2[0] 首次全核 IDLE
    # (all_cores_idle_waiting,合法停但 W1[3] 尾段/W1[4]/S9/W2/W3/W4[0..3]
    # 过不去)。TIMER2 本体投影(wake horizon 第三族)属批 1 的 BK 件,本
    # 批不做——现 min() 只有 core tick+Timer0 两族,W2[0] 的 tick 等待恰由
    # Timer0 交付推进。BI 首验(A1-A5 硬断言)属批 3;本批对 os_time_dly
    # 的形状**只观察不硬断言**(check() 记录 event_clock 报告键)。
    # 需 idle_wait(base 栈成员,机器侧守卫同款)。默认关:全核 IDLE 立即
    # 诚实停(legacy 形状逐字节不变)。**本前缀实测惰性**(virtual_model_
    # steps_advanced=0,新前沿 3,202,148 之前 CPU0 从未停泊、无全核 IDLE 点
    # ——首个消费者 W2[0] 在停点之后;批 1 放行 DAC_COP 后才可能有消费者,
    # 届时不开本开关的世界将停在首次全核 IDLE)。
    if event_clock:
        require(idle_wait,'the event clock projection requires the IDLE wait projection')
        machine_options['event_clock_projection']=True
    # jl_audio_dac_cop_writes(路 BM §4.3 行 0b 勘误/S19 批 1 ④):3,202,148
    # 停点 0x606ab2c(W1[3] 尾段输出级门控 0x606ab0a 的 `st.w.modimm [r5+52]
    # =0x7F`)写侧三选一按消费面判定——**全 app 恰两处访问、全部纯写 0x7F、
    # 零读回**(本停点+音频域 0x606e038;D2 场景表的 0xfe0534 RMW/读面属 D1
    # 后音频任务域),br28.h 成员 13 标 **__WO**(裁剪操作字)→write-only 整字
    # 记录(p3_ls/jl_uart_init_writes 同族),读回/RMW 显式严格停,运行效果
    # (chopper/泵电路)不建模。依赖边挂 jl_audio_dac_vl0_readback:0x606ab0a
    # 在 CW 放行 DAC_VL0 setter 后才到达(执行序同一 DAC-init 驱动链)。
    # 默认关:0xfe0534 保持 unmapped(3,202,148 前沿形状),legacy 逐字节不变。
    if jl_audio_dac_cop_writes:
        require(jl_audio_dac_vl0_readback,
                'the JL_AUDIO DAC_COP write record only layers on the DAC_VL0 release (the '
                'output-stage gate 0x606ab0a storing DAC_COP is reached right after the route-CW '
                'setter chain — without it the world still stops at 0x606ab2c)')
        options['jl_audio_dac_cop_writes']=True
    # jl_audio_dac_dma_writes(路 BM §4.3 行 0b 勘误之二/S19 批 1 ④续):DAC_COP
    # 放行后 +35 步的 0x6071e92 写 0xfe0508=1——W1[3] 尾段 DAC 启动函数
    # 0x6071e74 在输出级门控 call 返回后的四个 DAC DMA 配置整字写
    # (0x6071e92..ea8:[+8]=1/[+4]=0x18e180(60KB DMA 缓冲,与 0x6071d50 memset
    # 目的同址互证)/[+0x14]=0/[+0x10]=0)。探路运行实证恰四写零读回零 RMW
    # (随后 [r10+0]|=0x110 是 DAC_CON,AK 容器服务)→write-only 记录(DAC_COP
    # 同族);D2 场景的 DMA 指针/计数读面属 tone/PCM 推送域(D1 后)。依赖边
    # 挂 jl_audio_dac_cop_writes(执行序)。默认关:四字保持 unmapped
    # (0x6071e92 停点形状),legacy 逐字节不变。
    if jl_audio_dac_dma_writes:
        require(jl_audio_dac_cop_writes,
                'the JL_AUDIO DAC DMA write records only layer on the DAC_COP release (the '
                'four stores 0x6071e92..0x6071ea8 sit right after the output-stage gate call '
                'that stores DAC_COP — without it the world still stops at 0x606ab2c)')
        options['jl_audio_dac_dma_writes']=True
    # jl_audio_dac_dma_idle_readback(路 DMAIMPL,codex 裁 (a) 收窄版,
    # docs/frontier-scout-dacdma.md §5):HRP(0xfe0510)/SWP(0xfe0514)两字
    # 读回=最后写值——41,965,883 停点 0x606e148 是 audio_dac_set_sample_
    # rate 空闲守卫首读(勘误:非轮询等待环,两次 volatile 双读任一次相等
    # 即前跳 POST);停点世界四字恰各 1 写、HRP=SWP=0、首检即相等。放行
    # 只覆盖 0x606e148/14a/150/152 四个已核验读点(ld.w {d,b:5,n:4/5} 形状
    # +字节钉死),值=固件自己的写记录(零合成字节);零写/阶段失效/位置
    # 未核验/基址不符维持原严格停。「初始化阶段」由事件历史维护(SWN/PNS
    # 触达、DAC_CON 控制位变化、另一核写、未预期重写——同值也审查——均
    # 失效;写来源+阶段随 M4 快照恢复)。显式初始化空闲假设
    # (derived-not-measured):仿真无 IRQ39 不能反证硅上无 DMA 活动,不
    # 声明硅等价。依赖边挂 jl_audio_dac_dma_writes(读回答自四字写记录,
    # 缺依赖=配置错误)。默认关:读点停点形状与开关关世界逐字节一致。
    if jl_audio_dac_dma_idle_readback:
        require(jl_audio_dac_dma_writes,
                'the DAC DMA idle readback requires the four-word write records '
                '(the HRP/SWP readback answers from JLAudioDacDmaWriteRecords '
                'stores — without jl_audio_dac_dma_writes the world still stops at '
                'the 0x6071e92 unmapped store)')
        options['jl_audio_dac_dma_idle_readback']=True
    # jl_audio_dac_dma_setup_discarded_reads(路 DACDMAREAD,codex 202611211512
    # 方案,默认 False=关):DAC DMA 配置函数五死读放行——31,391,166 停点
    # 0x606b2b2..b2ba 依次读 ADR/LEN/HRP/SWP/SWN,读值均不参与比较/寻址/
    # 写回、下一条 0x606b2bc 由真实 r0=4 覆盖。放行=标为不可用于计算的未知
    # 值逐条退休(R0=None opaque),不是四字全地址通用回读、不是 DMA 推进模型;
    # SWN 只放行 0x606b2ba 这一次,其余 PC 读 SWN/任何写 SWN/PNS 访问仍走原
    # 路径。依赖边挂 jl_audio_dac_dma_writes(四字写记录是审计来源)。sites=4
    # 负例只放行四字配置读、钉住第五读。默认关:读点停点形状与开关关世界
    # 逐字节一致。
    if jl_audio_dac_dma_setup_discarded_reads:
        require(jl_audio_dac_dma_writes,
                'the DAC DMA setup discarded reads require the four-word write records '
                '(the released reads ride the ADR/LEN/HRP/SWP stores of '
                'JLAudioDacDmaWriteRecords — without jl_audio_dac_dma_writes the world '
                'still stops at the 0x6071e92 unmapped store)')
        require(jl_audio_dac_dma_setup_discarded_sites in (4, 5),
                'the DAC DMA setup discarded-sites probe takes exactly 4 or 5 '
                '(4 = release only the four-word config reads and pin the fifth SWN '
                'dead read, the negative arm; 5 = also release the SWN dead read)')
        options['jl_audio_dac_dma_setup_discarded_reads']=True
        if jl_audio_dac_dma_setup_discarded_sites != 5:
            options['jl_audio_dac_dma_setup_discarded_sites']=jl_audio_dac_dma_setup_discarded_sites
    # jl_audio_dac_dma_len_readback(路 DACDMALEN,codex 202611211512 方案 §3
    # 「活读到达时再扩」,默认 False=关):DAC DMA LEN 活读回读=最近一次整字写
    # 放行——31,391,419 停点 0x606ddc4(音频参数计算函数 0x606dd96 体内,
    # r8=0xfe0508)的钳位比较首读与 0x606ddcc then 臂再读。回读=四字写记录
    # 里 0xfe0508 最近一次整字写值(0x606b2b0 重写的实际 LEN,不是 0x6071e92
    # 的 LEN=1),零合成字节;无写来源/守卫不符→回落普通严格路径。依赖边挂
    # jl_audio_dac_dma_setup_discarded_reads(同一条配置函数链,五死读放行是
    # 到达 0x606ddc4 的前置)。默认关:读点停点形状与开关关世界逐字节一致。
    if jl_audio_dac_dma_len_readback:
        require(jl_audio_dac_dma_setup_discarded_reads,
                'the DAC DMA LEN live read requires the five-dead-read release '
                '(0x606ddc4 is reached only after 0x606b2b2..b2ba are released on the '
                'same config-function chain)')
        options['jl_audio_dac_dma_len_readback']=True
    # jl_audio_dac_dma_pns_write(路 DACDMAPNS,codex 202611211512 方案 §4 优先级
    # 1 实跑落地,默认 False=关):DAC DMA PNS(0xfe050c)写侧 write-only 整字记录
    # ——31,391,525 停点 0x606de56(st.w.l [r8+4]=r2,r8=0xfe0508,+4=0xfe050c=
    # PNS,值 0x60)放行。全 app 恰两处 PNS 纯整字写(0x606de56 与 0x6072496 阈值
    # 写)、零读回零 RMW(静态扫描),write-only 记录;读回显式严格停,不建模
    # 衰减语义/DMA 推进/PNS 运行时状态推进。依赖边挂 jl_audio_dac_dma_len_
    # readback(同一函数链,LEN 活读放行是到达 0x606de56 的前置)。默认关:PNS
    # 保持 unmapped(0x606de56 前沿形状),legacy 逐字节不变。
    if jl_audio_dac_dma_pns_write:
        require(jl_audio_dac_dma_len_readback,
                'the DAC DMA PNS write release rides on the LEN live-read world '
                '(0x606de56 is reached only after 0x606ddc4/cc are released on the '
                'same function chain)')
        options['jl_audio_dac_dma_pns_write']=True
    # jl_audio_dac_dma_pcm_produce(路 DACDMAPCM,codex 202611211512 方案 §4
    # 优先级 2,默认 False=关):DAC DMA PCM 数据相生产路径(0x607152c)读回+SWN
    # 提交记录——31,409,349 停点 0x6071560(LEN 活读算差,非 len_readback 两
    # 站点)放行。ADR/LEN/SWP 读回=四字写记录最近一次整字写值,SWN 读回=具名
    # 假设初值 0 后=0x6071782 提交写值,SWN 写 write-only 记录;不建模 SWN/HRP
    # 真实活动期推进量/DMA 数据流/IRQ68 消费。依赖边挂 jl_audio_dac_dma_pns_
    # write(同一函数链,PNS 写放行是到达 0x6071560 的前置)。默认关:SWN 保持
    # unmapped(0x6071560 前沿形状),legacy 逐字节不变。
    if jl_audio_dac_dma_pcm_produce:
        require(jl_audio_dac_dma_pns_write,
                'the DAC DMA PCM production-path release rides on the PNS write world '
                '(0x6071560 is reached only after the PNS store 0x606de56 of the same '
                'function chain is released)')
        options['jl_audio_dac_dma_pcm_produce']=True
    # p3_pcnt_set0_readback(路 DACDMAPCNT,默认 False=关):P3_PCNT_SET0(0xf2c128)
    # bit8-15 读回+ld/st RMW 写回记录——31,418,680 停点 0x12cc48(喂狗函数
    # 0x12cc7e 前身)放行。0x12cc40 读整字(bit8-15 携带到写回值、bit7 折入
    # VLVD bit2)、写回整字;bit8-15=软件复位标记暂存(复位历史 &=0xff00 保留
    # 位),固定 POR 下合成初始 0(derived-not-measured),读值零分支零比较;
    # 写回值=真实执行结果,仅记录不发明复位配置位运行时语义。依赖边挂
    # p33_canary(同函数链 VLVD 读放行是写回 bit7 折入的前置,本栈恒开)。
    # 默认关:0xf2c128 读保持严格停(31,418,680 前沿形状),legacy 逐字节不变。
    if p3_pcnt_set0_readback:
        require(p33_canary,
                'the P3_PCNT_SET0 readback rides on the P33 canary world '
                '(0x12cc40 folds the VLVD 0xf2c044 bit2 read into the 0xf2c128 '
                'write-back bit7; the canary first-read fixture is the fold source)')
        options['p3_pcnt_set0_readback']=True
    # p3_vlvd_bit2_writeback_readback(路 DACDMAPCNT 续,codex 202611212109
    # canary-readback,默认 False=关):P3_VLVD_CON(0xf2c044)bit2 写后读回=
    # 软件最后写入值——31,418,682 停点 0x12cc54/56 并行对 B 半读放行。取值
    # bits.known&4→stores[-1]&4→回落严格停,只给已核验消费链 0 或 4(掩码
    # 0x4);守卫=读点组首 0x12cc54+B 半 0x12cc56+有效地址 0xf2c044+指令字节。
    # 依赖边挂 p3_pcnt_set0_readback(同函数链 bit2 折入写回 bit7 的前置)。
    # 默认关:读点严格停(31,418,682 前沿形状),legacy 逐字节不变。
    if p3_vlvd_bit2_writeback_readback:
        require(p3_pcnt_set0_readback,
                'the P3_VLVD bit2 writeback readback rides on the P3_PCNT_SET0 readback '
                '(0x12cc54/56 is reached only after the 0x12cc48 PCNT_SET0 read is '
                'released on the same function chain)')
        options['p3_vlvd_bit2_writeback_readback']=True
    # p3_pr_pwr_retained_bits(路 DACDMAPCNT 续二,codex 202611212300,默认
    # None=关):P3_PR_PWR(0xf2c280)软复位读点 12 个保留位显式 fixture——
    # 31,418,688 停点 0x12cc6a 读 0xf2c280 放行,读值=显式传入保留位(source=
    # fixture,mask=0x3fcf),真实指令 &0x3fcf|0x30 后 0x12cc78 st.w.l 写回=
    # 软件请求 P33 软复位(停因 p33_soft_reset_requested,写值 (retained&0x3fcf)
    # |0x30)。value_switch 约定区分 None(关)与数值 0(开,保留位全零假设)。
    # 依赖边挂 p3_vlvd_bit2_writeback_readback(同函数链 0x12cc54 VLVD 读放行
    # 是到达 0x12cc6a 的前置)。默认关:0xf2c280 读保持 unmapped(31,418,688
    # 前沿形状),legacy 逐字节不变。
    if p3_pr_pwr_retained_bits is not None:
        require(p3_vlvd_bit2_writeback_readback,
                'the P3_PR_PWR retained-bits fixture rides on the P3_VLVD bit2 writeback '
                'readback (0x12cc6a is reached only after the 0x12cc54 VLVD read is '
                'released on the same function chain)')
        require(isinstance(p3_pr_pwr_retained_bits, int)
                and 0 <= p3_pr_pwr_retained_bits <= 0xffffffff
                and not (p3_pr_pwr_retained_bits & ~0x3fcf),
                'the P3_PR_PWR retained-bits fixture is an int whose bits are all '
                'within the 0x3fcf mask (the value is an explicit named assumption, '
                'not a reset-value claim)')
        options['p3_pr_pwr_retained_bits']=p3_pr_pwr_retained_bits
    # copex_exp22_model(路 COPEXIMPL,codex 裁 (a) 收窄版,默认 False=关):
    # copex.ppi mode-22 指数近似模型——42,037,363 停点 0x606a900(upstream
    # 『copex.ppi has no model』,音频管线重配段 exp 叶子 0x606a8fc 体内)
    # 的放行。mode 22=e^x 双源钉死(SDK exp_float 形状+AC791N 捕获);模型
    # 是 conditional mathematical approximation(下字 float32(double exp)
    # 非硅复刻,上字不透明消费即停),契约/证据/六坑对照全文见
    # br28_dsp_models 模块注释与 docs/frontier-scout-copex.md。拦截门在
    # probe_boot CPU.step_body(hardware.copex_exp22 槽),上游模型冲突检查
    # 在 CPU 构造点。依赖边挂 jl_audio_dac_dma_idle_readback=场景推进顺序
    # (DMAIMPL 放行后链才到达 copex 停点)非硬件依赖;默认关=拦截门不装,
    # legacy 报告逐字节不变。
    if copex_exp22_model:
        require(jl_audio_dac_dma_idle_readback,
                'the copex mode-22 release rides on the DMAIMPL idle-readback '
                'world (the chain reaches the copex stop at 0x606a900 only after '
                'the set_sample_rate idle guard passes)')
        options['copex_exp22_model']=True
        if copex_exp22_probe_ul_bias is not None:
            require(copex_exp22_probe_ul_bias in (-1, 1),
                    'the copex ul sensitivity probe takes exactly +1 or -1 '
                    '(sensitivity-probe-not-plausible-state, CV/ADA precedent)')
            options['copex_exp22_probe_ul_bias']=copex_exp22_probe_ul_bias
    # jl_src_sync_config_words(路 SRCSYNC,codex 20261118 下路指引「CON1 单字
    # 配置 RMW 容器」(AM/ADA/LPV 先例)的同函数事务扩展,默认 False=关):
    # JL_SRC_SYNC 配置事务三字——42,059,070 停点 0x606fbe4(rmw.or.modimm
    # [r0+4]|=0x400000 的内部读 0xfe2604=JL_SRC_SYNC CON1,br28.h src_sync 块
    # 16 成员 14 __RW+2 __WO 之 +4)放行。审计钉死:基址数据驱动(全 app 字
    # 面量恰 .data 0x60a29d4 一处、零 li,hw_res 表 0x60a29c8 唯一访问函数
    # =停点函数 0x606fa7e,`+4360` 全 app 唯一站点);函数全通读 SRC_SYNC
    # 访问面恰三字=CON1 RMW×2(|=0x400000 @0x606fbe4/ &=0xFFBFFFFF
    # @0x606fc2c)+CON0 RMW 恰 2 次(|=0x10 开门 @0x606fbfa/ &=0xFFFFFFEF
    # 关门 @0x606fc0a)+COEF 纯写×55(@0x606fc02 循环体,恒零;goto 偏移
    # 单位勘误=字节,|=0x10 不在循环内——主臂账本 CON0 2 写+COEF 55 写
    # 纠正首版 word×2 误读);读值只进写回组装零分支
    # 消费;其余 13 成员零触达不进白名单(不据 __RW 扩整块)。容器策略:
    # CON0/CON1 合成初值 0+读回=最后写值(合成假设非复位值,显式声明),
    # COEF 纯写记录读一律拒绝(零读观测);原子 RMW 内部读走通用 mmio 路径。
    # bit22/bit4 门控语义=derived(固件驱动形状归纳:bit22 配置事务总门、
    # bit4 系数装载门——公开 SDK 三源 grep 无 src_sync 驱动源,固件内置驱动
    # 即第一手证据,如实标注)。SDK 驱动用法/消费者审计/证据全文见
    # br28_model.py JLSrcSyncConfigRegisters 类注释。依赖边挂
    # copex_exp22_model=场景推进顺序(COPEXIMPL 放行后链才到达停点)非硬件
    # 依赖;默认关=三字保持 unmapped(42,059,070 停点形状),legacy 逐字节
    # 不变。
    if jl_src_sync_config_words:
        require(copex_exp22_model,
                'the JL_SRC_SYNC config container only layers on the copex mode-22 '
                'release (the chain reaches the SRC_SYNC reconfiguration function '
                '0x606fa7e and its CON1 RMW only after the eq coefficient design '
                'segment crosses the exp sites — without the COPEXIMPL release the '
                'world still stops at 0x606a900; the dependency records THIS scenario '
                'advance order, not a hardware dependency of the SRC_SYNC config '
                'registers on the DSP coprocessor model)')
        options['jl_src_sync_config_words']=True
        if jl_src_sync_probe_value is not None:
            require(jl_src_sync_probe_value==0xffffffff,
                    'the SRC_SYNC CON1 sensitivity probe takes exactly the all-ones '
                    'word (sensitivity-probe-not-plausible-state, CV/ADA/ASS precedent)')
            options['jl_src_sync_probe_value']=jl_src_sync_probe_value
    # jl_src_sync_full_block(路 SRCSYNC2,codex 202611200345 总裁决「收窄 A」
    # 决策点 3,默认 False=关):JL_SRC_SYNC 全块 11 新字容器——42,231,992
    # 停点 0x6070a9a(CON2 0xfe2608 整字写 0x00010002,函数 B 0x6070a30
    # 体内,在已放行 jl_src_sync_config_words 容器 CON0/CON1/COEF 之外)
    # 放行。函数 B 的 11 笔整字纯写:CON2/CON3/IDAT_ADR/IDAT_LEN/ODAT_ADR/
    # ODAT_LEN/FLTB_ADR/STOP_FLAG/INSR/OUTSR/PHASE;ODAT_ADR_START/END
    # (+0x24/+0x28)函数 B 不写留表外。策略=整字纯写记录+读一律拒绝
    # (jl_src_sync_full_block_read_not_modeled,首写前/写后/RMW 内部读都停)。
    # CON2 bit16 用途、bit3 启动含义保留为推断(derived)。CON0|=0x8 启动
    # 请求 @0x6070b1c 归原 CON0 容器(五元组 RMW 专用分发+写历史检查),
    # 不占新容器 11 纯写。依赖边挂 jl_src_sync_config_words(同址块的两段
    # 分属两容器;场景推进顺序非硬件依赖)。默认关=11 字保持 unmapped
    # (42,231,992 停点形状),legacy 逐字节不变。
    if jl_src_sync_full_block:
        require(jl_src_sync_config_words,
                'the JL_SRC_SYNC full-block container only layers on the '
                'jl_src_sync_config_words release (the CON0/CON1/COEF transaction '
                'container must already be on — the function-B base 0xfe2600 rides '
                'the same hw_res table and the startup RMW 0x6070b1c reads the CON0 '
                'old value from the original container; the dependency records THIS '
                'scenario advance order, not a hardware dependency)')
        options['jl_src_sync_full_block']=True
    # jl_src_sync_coef_batch_readback(路 SRCSYNC4,codex 202611200751 总裁决
    # 「改红线收窄」,默认 False=关):COEF 口「本次初始化后 55 字批次回读」——
    # 42,232,325 停点 0x60709e0(helper 0x607099c 的 ld.w r0=[r5+60] 读
    # 0xfe263c=COEF,jl_src_sync_coef_read_not_modeled 诚实拒)放行。不是通用
    # FIFO/破坏性弹出/写满丢最旧:记录重配驱动 0x606fa7e 在 0x606fc02 的 55
    # 笔对齐零写(介于 CON0 |=0x10 @0x606fbfa 与 CON0 &=~0x10 @0x606fc0a 之间,
    # CON1 bit22 全程置位)填满批次;helper 在 0x60709e0 顺序读回第 i 位
    # (i=0..54,ld.w [r5+60] 字节 50 6f 钉死,CPU0+CON0 bit4+CON1 bit22 开门)
    # 各返回该位置的值(非「永远最后写值」),成功读推进游标、第 56 次读读空
    # 即停;写回 0x6070a1c 按固件实际 55 个写值记账(读整批→memcpy 切片替换
    # [obj+168]→写回 55 字,通道切片修改保留);超量写(56th 填窗)/未知写 PC/
    # 他 PC 读/他核读/门未开/启动后重入一律改状态前停。假设须明列:55 笔
    # 初始化写覆盖下次开门可见的同一批 55 个位置(64 深 FIFO 首读也可能取
    # 旧字,故是显式假设非硅声明)。依赖边挂 jl_src_sync_config_words(同一
    # 容器;场景推进顺序非硬件依赖)。默认关=COEF 读仍拒(42,232,325 停点
    # 形状),legacy 逐字节不变。
    if jl_src_sync_coef_batch_readback:
        require(jl_src_sync_config_words and jl_src_sync_full_block,
                'the COEF batch read-back only layers on the jl_src_sync_config_words '
                'and jl_src_sync_full_block releases (the COEF batch answers from the '
                'CON0/CON1/COEF config-transaction container and the helper 0x607099c '
                'is only reached behind the full-block function B; the dependency '
                'records THIS scenario advance order, not a hardware dependency)')
        options['jl_src_sync_coef_batch_readback']=True
    # src_sync_delivery(路 SRCSYNC5,codex 202611201614 总裁决 §2「数字模型
    # 建到哪里」,默认 False=关):SRC_SYNC 完成事件 + IRQ89 交付——从真实
    # 启动写 CON0|=0x8 @0x6070b1c 开始,completion_steps 个模型步后完成
    # (显式时序假设 derived-not-measured,不从任务 3 tick 超时反推;
    # completion_steps=40000=1 OS tick 量级——1 tick=10ms、1ms≈4000 步,
    # 非硅测量:SRC 真实完成延迟取决于采样率与缓冲块,ODAT_LEN≈930 样本
    # 下 48kHz≈2 tick;取 1 tick 让 jlstream_N 真实睡眠一个 tick 周期、
    # OSSemPost 唤醒路径走到且 <3 tick 不总走超时,idle 喂狗循环恢复)置
    # IRQ89 pending;advance() 逐 pending 片至多一次 IrqDeliveryRequest(core=0,
    # irq=89);机器侧最小进入/一次性 RTI 投影镜像 audio IRQ68(向量槽
    # 0x19ff64→handler 0x12ca24 RAM 拷贝,flash 母本 0x60c61fc),handler 的
    # CON0|=0x40 W1C 清 pending;转换结果/连续数据流不建模(读回=最后写值、
    # 输入消耗=0、输出数据不合成,消费即 BLOCKED)。依赖边挂
    # jl_src_sync_config_words+jl_src_sync_full_block(启动写/ACK 归原 CON0
    # 容器,handler 九字读回归全块 readback 半边);默认关=启动写后无完成
    # 事件、无 IRQ89 pending,legacy 逐字节不变。
    if src_sync_delivery:
        require(jl_src_sync_config_words and jl_src_sync_full_block,
                'the SRC_SYNC delivery only layers on the jl_src_sync_config_words and '
                'jl_src_sync_full_block releases (the startup CON0|=0x8 and the handler '
                'ACK |=0x40 ride the config container; the handler progress readback of '
                'IDAT/ODAT/FLTB/STOP_FLAG/OUTSR rides the full-block readback half)')
        machine_options['src_sync_irq_entry']=True
        machine_options['src_sync_irq_entry_banks']=True
        machine_options['src_sync_irq_rti']=True
        options['jl_src_sync_delivery']=True
        options['jl_src_sync_delivery_requests']=True
        options['jl_src_sync_completion_steps']=40000
        extra['src_sync_irq_frames']=dict(SRC_SYNC_IRQ_FRAMES)
    # src_sync_cursor_progress(路 SRCSYNC6,codex 202611211240 方案,默认 False=关):
    # SRC 硬件游标进度投影——在 src_sync_delivery 的完成事件之上,给 IDAT_ADR/
    # IDAT_LEN/ODAT_ADR/ODAT_LEN 四字一个「完成即提交」的硬件结果:note_start()
    # 锁存本次描述符+配置+事务号,advance() 到期验证后原子提交,full_block read()
    # 返回已提交快照(ACK 后保留)。消耗量 c/产出量 p/样本宽 W 为具名假设
    # (derived-not-measured):C=CON2 低字节通道数、W 默认 4(tone 路径 32 位样本)、
    # c 默认一次消费全部剩余「跑到输入耗尽」、p 默认与消费量相同「1:1 速率」;
    # 真实每 completion 次数与 SRC 速率比需硅测确认。逐块守恒 0<=c<=L前/
    # 0<=p<=O前/L后=L前-c/A后-A前=c*C*W/无回绕;一次启动只准提交一次。依赖边挂
    # src_sync_delivery(完成事件里提交,快照走 full_block 读回半边);默认关=
    # 四游标字读回=最后写值(139,947,345 @ 0x6064220 零消费臂),legacy 逐字节不变。
    if src_sync_cursor_progress:
        require(src_sync_delivery,
                'the SRC cursor progress only layers on the src_sync_delivery release '
                '(the cursor commits inside the completion event and its snapshot rides '
                'the full-block readback half)')
        options['jl_src_sync_cursor_progress']=True
        if src_sync_cursor_sample_bytes != 4:
            options['jl_src_sync_cursor_sample_bytes']=src_sync_cursor_sample_bytes
        if src_sync_cursor_consume is not None:
            options['jl_src_sync_cursor_consume']=src_sync_cursor_consume
        if src_sync_cursor_produce is not None:
            options['jl_src_sync_cursor_produce']=src_sync_cursor_produce
    # jl_sync_config_words(路 SYNCIMPL,codex 20261119 收窄版 a,
    # docs/frontier-scout-jlsync.md §5,默认 False=关):JL_SYNC(audio_sync)
    # 四字容器——42,061,357 停点 0x60befe6(creator 0x60befae 体内 li 常量
    # CON 整字写 0,br28.h audio_sync 块 0x2500-0x25ff 13 成员之 +0 __RW)
    # 放行。侦察钉死(SYNCSOUT+codex 复核):基址 li 装载驱动(CON li 站点
    # 恰 12 处全在 0x60beeee-0x60bf1f0 audio_sync 库族,app 零 .data 地址
    # 字面量);creator→fn_config 链触达恰四字=CON(停点整字写 0+fn_config
    # 0x60bef62 读→0x60bef9e 读-改-写)+CON1/BT_OFFSET/MIN_SLOT(fn_config
    # 纯写,CON1=h[obj+10]<<16 预期 0x00C80000、BT_OFFSET=0、MIN_SLOT=
    # h[obj+12] 预期 80);本次续跑只执行 CON 一写零读(fn_config 在
    # request_irq/0xF001xx 三道墙后);CON 读值去向逐 PC 分类——写回组装
    # 读点(fn_clear_b22/fn_config/fn_stop/fn_pause/fn_resume/IRQ RMW 共
    # 11 处)只进写回,分支读点(IRQ 0x60bf1f6 bit19、0x60bf25c uextra
    # bit28 vs 槽号、getter 0x60bf19c)拿 CON 决定控制流。容器策略
    # (codex 裁):CON 无合成初值(首个访问就是写,首写前读拒绝 BATCHA
    # 先例)、写后读回=最后写值、RMW 内部读走通用 mmio 路径;WO 三字纯写
    # 记录读一律拒绝(含 RMW 内部读);消费者 PC/核守卫——分支读点/未审计
    # PC/另一核的 CON 读消费前停(PC 溯源=probe_boot step_body 每步刷新,
    # DMAIMPL current_pc 先例)。其余 9 成员(DABUF/DASPACE/OFFEND/OFFSUM/
    # PCMNUM/BTTIME/CON2/OUTSR/INSR)零触达不进白名单(不据 __RW/__WO
    # 扩整块);0xF00130/40/44/48 timer 域不在容器(将来独立批准,不按
    # 连续区间映射)。SDK 驱动用法/消费者审计/证据全文见 br28_model.py
    # JLSyncConfigWords 类注释。依赖边挂 jl_src_sync_config_words=场景
    # 推进顺序(SRCSYNC 放行后链才到达 creator)非硬件依赖;默认关=四字
    # 保持 unmapped(42,061,357 停点形状),legacy 逐字节不变。
    if jl_sync_config_words:
        require(jl_src_sync_config_words,
            'the JL_SYNC config container only layers on the JL_SRC_SYNC release '
            '(the chain reaches the audio sync object creator 0x60befae and its '
            'CON whole-word store only after the SRC_SYNC reconfiguration '
            'transaction crosses the 42,059,070 stop — without the SRCSYNC '
            'release the world still stops at 0x606fbe4; the dependency records '
            'THIS scenario advance order, not a hardware dependency of the '
            'JL_SYNC config registers on the SRC_SYNC block)')
        options['jl_sync_config_words']=True
    # jl_btclk_timer_words(路 TIMERIMPL,codex 20261119 三选一裁 B,
    # docs/frontier-scout-timer.md §5,默认 False=关):BT modem 窗尾
    # BT clock timer 恰两地址写记录容器 {0xF00130, 0xF00140}——
    # 42,061,359 停点 0x60befee(creator 0x60befae 体内 st.w.modimm
    # 整字写 0x2F4)与 +2 步 0x60beff6(写 0xF00140=1754)放行。侦察钉死
    # (TIMERSCOUT+codex 复核):块身份=btctrler 闭源库数字窗
    # 0xF00000..0xF0018F(非 SDK JL_TIMER0-5 0xfd0400-0xfd0900,SDK 头零
    # 定义);双轨 li 扫描(十进制+十六进制显示勘误)+app.bin/ROM/p11
    # LE32+函数体通读=两写是全 app 唯一写站点;链上恰两写,读口
    # 0xF00144/48(getter/IRQ88)与 400B 整窗 bulk 不在前缀。容器策略
    # (codex 裁 B):无合成初值、首写前读拒绝、写后读仍拒绝(记录值只进
    # 诊断账本)、RMW 内部读走通用 mmio 路径即拒、不推进计数/不触发
    # IRQ88/不改其他寄存器、邻接成员一概不承接(将来独立批准,不按连续
    # 区间映射)、写者 PC 守卫逐地址只认 0x60befee/0x60beff6(其余 PC 写
    # 相同常量也停)。副作用边界(codex 点名最弱一环)已答:两放行指令均
    # 纯写、与已建模域零交叠、未知硬件活动的观察面(0xF00144/48/整窗/
    # IRQ88)全部仍拒或无源,放行段后续分支无一消费这两字值。证据全文
    # 见 br28_model.py JLBtclkTimerWords 类注释。依赖边挂
    # jl_sync_config_words=场景推进顺序(JL_SYNC CON 写放行后链才到达
    # 两 timer 写)非硬件依赖;默认关=两字保持 unmapped(42,061,359 停点
    # 形状),legacy 逐字节不变。
    if jl_btclk_timer_words:
        require(jl_sync_config_words,
            'the BT clock timer two-word container only layers on the JL_SYNC '
            'release (the chain reaches the creator\'s BT clock timer stores '
            '0x60befee/0x60beff6 only after the JL_SYNC CON whole-word store at '
            '0x60befe6 crosses the 42,061,357 stop — without the SYNCIMPL '
            'release the world still stops at the CON store; the dependency '
            'records THIS scenario advance order, not a hardware dependency of '
            'the modem-window timer words on the JL_SYNC block)')
        options['jl_btclk_timer_words']=True
    # jl_fm_con_head_rmw(路 FMIMPL,codex 20261119 三选一裁 B,
    # docs/frontier-scout-fm00000.md §5,默认 False=关):modem 窗 CON 头
    # 0xF00000 恰一字 RMW 容器——42,061,478 停点 0x60befa8(fn_config
    # 0x60bef46 尾段 b[obj+6]==0 条件臂 `[r0+0]|=0x400` 的 RMW 内部读)
    # 放行。侦察钉死(FMSCOUT+codex 复核):读值无任何链上来源(本 RMW
    # 是前缀内对窗内头字的第一次访问,反证法——任何更早的头字读写都会把
    # 仿真停得更早)=硅初始态静态不可知,模型必须显式合成初值(与 JL_SYNC
    # CON「无合成初值」先例的关键差异:RMW 内部读是放行前提);fn_config
    # 有第二调用者(节点 start 0x607e46c 经 walker 0x6073514 派发,同 PC
    # 同 obj 同臂——放行后大概率第二次 RMW,账本区分 synthetic/last_write
    # 读来源分别记账);fn_resume 0x60bf14e 同款 RMW 不同 PC 链外、IRQ88
    # 0x60bf238 无源、btctrler 13 站域未启动,全拒。容器策略(codex 裁 B,
    # 五条修正照做):合成初值 0(显式假设 derived-not-measured 非硅复位
    # 值声明)+RMW 读拍返回当前容器值+授权=一条完整指令五元组(主核+
    # PC 0x60befa8+有效地址+对齐 32 位+rmw.or.modimm 立即数 0x400 全匹配,
    # 同 PC 的普通 ld/st/不同掩码不借用)+其余 PC/核消费前停+显式标量读
    # 一律拒绝+独立写一律拒绝(bulk 守卫在总线访问级,不只查 memcpy 调用
    # 点)+邻接成员(头段 +4..+FF/尾段/400B 整窗双向)一概不承接+无副作用
    # (不产生 IRQ88/不改其他寄存器)+复位清态+快照不扩大权限。证据全文见
    # br28_model.py JLFmConHeadRmw 类注释。依赖边挂 jl_btclk_timer_words=
    # 场景推进顺序(RMW 停点在 BT clock timer 两写与 request_irq 之后)
    # 非硬件依赖;默认关=0xF00000 保持 unmapped(42,061,478 停点形状),
    # legacy 逐字节不变。
    if jl_fm_con_head_rmw:
        require(jl_btclk_timer_words,
            'the FM CON-head RMW container only layers on the BT clock timer '
            'release (the chain reaches the fn_config conditional arm and its '
            '[0xF00000]|=0x400 RMW only after the creator\'s BT clock timer '
            'stores, request_irq(88) and the JL_SYNC four-word transaction '
            'cross — without the TIMERIMPL release the world still stops at '
            'the timer store; the dependency records THIS scenario advance '
            'order, not a hardware dependency of the modem-window head word '
            'on the timer words)')
        options['jl_fm_con_head_rmw']=True
    # jl_pvdd_trim_writes(路 BM §1/S19 批 1 ①):W4[4] PVDD/trim 域容器——
    # P3_ANA_CON13(0xf2c274)/P3_ANA_CON1(0xf2c244)/P3_PVDD0_AUTO(0xf2c10c)
    # 合成初始字 0+读回=当前值(AM 容器族;BM §1.1 逐地址访问点钉死:复合
    # RMW×2+显式 ld/st RMW×N,读值真实参与写回)。**RES 行扩展同 PR**(勘误
    # BA「trim 环采样 fd31✓」:0x10005 不在 RES 具名档——场景经 registry 改传
    # FD31_ADC_RES_S19_VALUES,否则环内即停,见该常量注释)。依赖边挂
    # p11_power_init(P33 域家族一致;执行序 W4[4] 在 power_init 放行链之后)。
    # 默认关:三地址保持 unmapped(W4[4] 停点① 0x60686fc 形状),legacy 不变。
    if jl_pvdd_trim_writes:
        require(p11_power_init,
                'the PVDD trim container only layers on the p11 power-init release '
                '(the W4[4] trim domain sits in the power-init initcall tail)')
        options['jl_pvdd_trim_writes']=True
    # timer2_delay_projection(路 BK 全设计/S19 批 1 ②):JL_TIMER2 0xfd0600 族
    # 写记录+LRC 源名义 200kHz 计数器(PRD 跨越置 pending)+CON 读回 bit15+
    # 0xFFE1B0(IWKUP_NUM)写记录+锚定 PC 完成唤醒(0x12cd76,kick 先例,
    # wake_source_unverified;无 IRQ6 交付——ICU 使能无证据)+timer2_wake_
    # horizon 并入事件时钟 min()(BK §3.2 第三族)。W4[4] 0x60685fe 慢路恰
    # 2 事务(0x6068778(13,0)+0x60687f6)、每事务 1ms=200 LRC tick=恰 4000
    # 模型步闭式。requires idle_wait(停泊/唤醒件)+event_clock(全核 IDLE
    # 期间唯一推进路径——W2[0] 的 6 tick 等待是更早的事件时钟消费者,BK §3.3)。
    # 默认关:0x12cd36 unmapped 写停点形状不变,legacy 逐字节不变。
    if timer2_delay_projection:
        require(idle_wait and event_clock,
                'the TIMER2 delay projection requires the IDLE wait and event clock projections '
                '(the anchored poll wake needs the parking machinery and the all-core IDLE '
                'advance path)')
        options['timer2_delay_projection']=True
        machine_options['timer2_delay_projection']=True
    # 路 WDTFEED2:停泊核被 IRQ 交付唤醒时消费 idle 指令(reti=pc+2)。
    # 默认关=旧语义(返回 idle 指令本身,核在同址再停泊,喂狗循环不执行)。
    # 只改机器侧返回地址,零硬件/模型改动;依赖 idle_wait(停泊机制)。
    if idle_wake_consumes_idle:
        require(idle_wait,
                'the idle-wake consume projection requires the IDLE wait projection '
                '(a parked core wake return address only exists under the parking machinery)')
        machine_options['idle_wake_consumes_idle']=True
    # p33_canary(+首读值)(路 BM §2/S19 批 1 ③):把 BV 备而未接线的
    # P33CanaryWatchRegisters 落进条件栈。W4[4] 的 0x12c29a 量测分支首读
    # 0xf2c044(0x12c2d6)是健康路径**必经**(当前 fixture 世界非条件:
    # 0x10006=500→v4=2000≤3699)且先于该地址一切写——首读具名 fixture
    # (P33_CANARY_FIRST_READ_WORDS={0xf2c044:0}=「VLVD 比较器未使能的
    # boot 初态」,synthetic-not-measured)适用条件严格满足。触发即停报警
    # A1-A4(未覆盖/写后再读/重复读/越形写)=模型既有严格停,场景 check
    # 钉「首读恰一次+0xf2c124 零访问」;A5(K1/K2 PC)保留 D1 窗口(BJ)。
    # 依赖边挂 p11_power_init(p33 族一致;canary 与容器语义正交不互依赖,
    # BM §2.3)。默认关:0x12c2d6 unmapped 读停点形状,legacy 逐字节不变。
    if p33_canary:
        require(p11_power_init,
                'the P33 canary projection only layers on the p11 power-init release '
                '(the W4[4] VLVD measurement call sits in the power-init initcall tail)')
        options['p33_canary_projection']=True
        if p33_canary_first_read_words is not None:
            options['p33_canary_first_read_words']=dict(p33_canary_first_read_words)
    # p2m_mailbox_ack(BM §3 规格;首版路 B3/S19 批 2 续 0b875ed,本版 B2R 重
    # 落地,B2G 提层到 conditional):S15/S17 喂狗 0x12cc7e 的 P11 邮箱事务(kick
    # [0xF2A618]=0x400→P2M_INT_PND bit12 置位+P2M+3 回声=M2P+7 值;CLR 0x1000
    # 清位)。依赖 p11_power_init(M2P 消息窗是其 harness 区)。消费时序:
    # S15 邮箱首停 0x12CC94 在当前前沿停点(28,890,702 随机种子读)之后——
    # conditional 层条件场景自动开,当前前缀容器零触达(提层实测停点/步数
    # 逐值不变,19,139,239 时 with/without 逐字节同停的 B3 记录延续)。
    if p2m_mailbox_ack:
        require(p11_power_init,
                'the P2M mailbox ack projection only layers on the p11_power_init scenario '
                '(the M2P message window is its harness region)')
        options['p2m_mailbox_ack']=True
    # wdt_mailbox_feed(路 WDFEED 三选一 (b),opt-in 默认关,需 p2m_mailbox_ack):
    # 喂狗邮箱事务闭合→P11 侧 WDT feed 副作用投影(计数器重装,模型侧
    # wdt_mailbox_feed_side_effect,全证据链与假设措辞见
    # WatchdogProjection.mailbox_feed 注释;codex 参谋定形=提交点取事务闭合
    # 〔回声匹配+CLR,与固件自己的成功判据重合〕、arg 以 kick 时刻锁存值为准、
    # 回声不满足只表示「不授予本投影的副作用」、已到期不补喂复活)。决策记录:
    # 32,737,356 @ 0x12c5ca 的 watchdog_expired_delivery_not_implemented 停点
    # 根因=模型只建模了喂狗事务的请求/ack 面、未建模 P11 的 feed 副作用——
    # 主核 init 后 WDT 接口恰一次直接 feed(command_values 末项 0x1002E),
    # 此后 16 笔闭合邮箱事务(counts 见 check)都不触达 WDT,计数器必然到期;
    # (a) 到期交付投影不采纳:到期在真硅=系统复位(SDK wdt.c.o;AGENTS「reset
    # delivery 未验证」),BR28 无看门狗向量槽,park 稳态真机长期存活证实稳态
    # 到期不发生——把伪到期做成交付=发明「看门狗=普通中断」(红线);(c) 不
    # 需要:喂狗节拍由真实代码驱动无漂移,名义时钟 (1,4000) 只定墙钟刻度、
    # 任何正速率下零喂狗必然到期,「修源头值」无的放矢。默认关:停点回
    # 32,737,356 逐字段一致(负例臂)。
    if wdt_mailbox_feed:
        require(p2m_mailbox_ack,
                'the WDT mailbox feed side effect only layers on the P2M mailbox ack '
                'projection (the feed transaction is what authorizes the reload)')
        options['wdt_mailbox_feed_side_effect']=True
    # cfg523_named_fixture(BH §4.2;首版路 B3/S19 批 2 续 0b875ed,本版 B2R
    # 重落地,B2G 提层到 conditional):70B 具名对象 PC 锚定首装(机器侧
    # after_step 钩子,解析器 0x60010b4 首次执行前写入 0x1274F4;BSS 初始化
    # 会清零故不能预填)。消费时序:解析器在 W1[4]/S9 域,当前前沿停点
    # (28,890,702 随机种子读,W1[3] 域)之后——conditional 层条件场景自动
    # 开,当前前缀安装钩子零触发(提层实测停点/步数逐值不变)。解析器到达
    # 后会激活 S9 键扫 10-tick sys_timer(D1 窗口多一个周期源,H2 上界与
    # d1_window 期望值要算进去;S19MAP §4.3 提醒)。
    if cfg523_named_fixture:
        machine_options['cfg523_object_bytes']=CFG523_OBJECT_BYTES
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
    # ancif_official_resources(路 FLS,默认 None=关):flash 顶 sdfile ANCIF/
    # ANCIF1 文件头映射窗=官方 UFW 外层资源内容(readonly;来源/换算链/消费者
    # 全图见 ANCIF_OFFICIAL_RESOURCES 常量注释)。挂载时自校验(长度/魔数/
    # 内层 CRC),校验失败即拒绝——fixture 只认官方工件字节,不发明数据;
    # 显式假设=目标分区携带官方资源内容(非设备实测),场景结论条件通过。
    # 依赖边挂 jl_adc_start_config=场景推进顺序(ASS 放行后链才到达停点),
    # 非硬件依赖;不触碰 erased_bankb 四窗(bank B 其余范围继续严格 unmapped:
    # ANCIF1 有效长 0xAC8 之外、0xF00 声明容量之内的区间照旧停)。
    if ancif_official_resources is not None:
        require(len(ancif_official_resources)==2,
                'the ANCIF fixture is the (ANCIF, ANCIF1) official-resource pair')
        ancif_blob,ancif1_blob=ancif_official_resources
        base,length=SDFILE_FLASHTOP_ANCIF_RANGE
        cbase,clength=SDFILE_FLASHTOP_ANCIF1_RANGE
        require(len(ancif_blob)==length and len(ancif1_blob)==clength,
                'official ANCIF resource lengths changed (expected 0x100/0xac8)')
        for blob,magic in ((ancif_blob,0x9881),(ancif1_blob,0x98c1)):
            w0,stored,h6=struct.unpack_from('<IHH',blob)
            require(h6==magic and 6+(w0-2)==len(blob),
                    'ANCIF fixture header shape changed (magic/length cover)')
            require(binascii.crc_hqx(blob[6:],0)==stored,
                    'ANCIF fixture inner CRC self-check failed')
        readonly=tuple(readonly)+((base,ancif_blob),(cbase,ancif1_blob))
    # ton_official_partition_pair(路 TONMAP,默认 None=关):TON/TON1 分区容器
    # 官方内容成对只读映射(窗口=容器有效全长;来源/坐标/布局全图见
    # TON_OFFICIAL_PARTITION_PAIR 常量注释与 docs/frontier-scout-vfs.md)。
    # 挂载自校验(ancif_official_resources 先例+SHA256/目录面,三方复算钉死):
    # 长度/完整 SHA256/容器头 32B JLFS 项(hcrc+addr+len+flags+name)/目录逐项
    # hcrc+文件数据 dcrc/范围不重叠——校验失败即拒绝,fixture 只认官方工件
    # 字节,零合成字节。显式假设(codex 原文,见常量注释):目标分区在 SFC 明文
    # 视图携带官方 UFW 提取内容,按当前 sdfile 地址换算映射;未验证真机一致,
    # 结论 conditional / derived-not-measured。依赖边挂 jl_pled_config_words=
    # 场景推进顺序(BATCHA 放行后链才到达 TON1 容器头停点),非硬件依赖。
    if ton_official_partition_pair is not None:
        require(len(ton_official_partition_pair)==2,
                'the TON fixture is the (TON, TON1) official-container pair')
        ton_blob,ton1_blob=ton_official_partition_pair
        ton_base,ton_length=SDFILE_TON_RANGE
        ton1_base,ton1_length=SDFILE_TON1_RANGE
        require(len(ton_blob)==ton_length and len(ton1_blob)==ton1_length,
                'official TON container lengths changed (expected 0xb7b0/0x10340)')
        for blob,(wbase,wlength),wsha,wname in (
                (ton_blob,(ton_base,ton_length),TON_OFFICIAL_SHA256[0],b'tone_zh'),
                (ton1_blob,(ton1_base,ton1_length),TON_OFFICIAL_SHA256[1],b'tone_en')):
            require(hashlib.sha256(blob).hexdigest()==wsha,
                    'TON fixture SHA256 self-check failed (bytes must be the exact '
                    'official UFW outer-layer container, never synthesized)')
            hcrc,dcrc,addr,clen,flags,_,last,raw=struct.unpack_from('<HHIIBBH16s',blob)
            require(addr==0x20 and clen==wlength and flags==3 and last==0 and
                    raw.split(b'\0')[0]==wname and
                    binascii.crc_hqx(blob[2:32],0)==hcrc,
                    'TON fixture container header shape changed '
                    '(hcrc/addr/len/flags/name)')
            offset=32
            while True:
                require(offset+32<=wlength,'TON fixture directory overruns container')
                ehcrc,edcrc,start,size,eflags,_,elast=struct.unpack_from('<HHIIBBH',blob,offset)
                require(binascii.crc_hqx(blob[offset+2:offset+32],0)==ehcrc and
                        eflags==2,'TON fixture directory entry hcrc/flags failed')
                require(start+size<=wlength,
                        'TON fixture file data window beyond container valid length')
                require(binascii.crc_hqx(blob[start:start+size],0)==edcrc,
                        'TON fixture file data CRC failed')
                offset+=32
                if elast:break
        # 范围守卫:两窗互不重叠、不吞并/不与既有窗(应用区 SFC 明文、
        # erased_bankb 四窗、ANCIF 两窗)重叠;两窗间与尾部空隙维持 unmapped。
        windows=[(wbase,wbase+wlength) for wbase,wlength in
                 ((ton_base,ton_length),(ton1_base,ton1_length),
                  SDFILE_FLASHTOP_ANCIF_RANGE,SDFILE_FLASHTOP_ANCIF1_RANGE,
                  ERASED_BANKB_HEADER_RANGE,ERASED_BANKB_WINDOW_RANGE,
                  ERASED_BANKB_VM_RANGE,ERASED_BANKB_RECORD_RANGE)]
        if flash_image_bytes is not None:
            windows.append((0x06000000,0x06000000+len(flash_image_bytes)))
        for i in range(len(windows)):
            for j in range(i+1,len(windows)):
                require(windows[i][1]<=windows[j][0] or windows[j][1]<=windows[i][0],
                        'TON fixture windows must not overlap an existing mapping')
        readonly=tuple(readonly)+((ton_base,ton_blob),(ton1_base,ton1_blob))
    # jlstream_kick_param2_unknown(路 EVIMPL,默认 False=关):jlstream 任务栈
    # 单点 4B 槽 unknown-word 放行——41,909,506 停点 0x60869ba(r6=[r10+8] 读
    # post('jlstream',1,{0}) kick 消息从不写的第二输出参数槽,任务栈物理
    # [0x18ca64,0x18ca68))。codex 裁决 (a)①:unknown-word 传播+单点槽+上下文
    # 守卫(五要素=任务实例/PC+编码 d0 ec a8 60/r10==sp+24+0xC00 栈窗/MMU 翻译
    # 恰为钉死物理窗;每次装载逐条重验,非地址豁免;值构造走 abstract_word
    # (task_frame=True) 不写零不标初始化;消费面 fail-closed 不变)。机制/
    # 证据全图见 JlstreamKickParam2Unknown 类注释与 docs/frontier-scout-
    # evqueue.md;消费负例(cmd∈[4,12] 最迟 0x60869c6 停)由定向单测钉死。
    # 依赖边挂 ton_official_partition_pair=场景推进顺序(TONMAP 放行后链才
    # 到达本停点),非硬件依赖;开关关=拦截门不装,41,909,506 逐键复现。
    if jlstream_kick_param2_unknown:
        require(ton_official_partition_pair is not None,
                'the jlstream kick param2 release rides on the TONMAP official-'
                'container world (the chain reaches the slot load only after the '
                'TON/TON1 release)')
        options['jlstream_kick_param2_unknown']=True
    # jl_rand_seed(路 RAND/sim-rand,默认 None=关):JL_RAND->R64L(0xfd3b00)熵源
    # 种子 fixture——readonly 单字映射,每次读同一冻结常量(policy=constant_on_
    # every_read)。熵机制无文档、值不可推导(synthetic-not-measured,非设备
    # 实测);R64H(0xfd3b04)零触达不进容器,0xfd3b04 起/跨界访问/写继续严格停。
    # 只要求 4 字节(bytes),不在此拒绝退化值——零臂(全零=LCG 不动点→全零
    # MAC)是敏感性对照的显式诊断输入,由调用方显式传值;主臂用
    # JL_RAND_SEED_BYTES=0x12345678。依赖边挂 ancif_official_resources=场景
    # 推进顺序(ANCIF 放行后链才到达停点),非硬件依赖。
    if jl_rand_seed is not None:
        require(isinstance(jl_rand_seed,(bytes,bytearray)) and len(jl_rand_seed)==4,
                'the JL_RAND seed fixture is a 4-byte little-endian R64L word')
        readonly=tuple(readonly)+((0xfd3b00,bytes(jl_rand_seed)),)
    # jl_eq_init_writes(路 JLEQ/sim-jleq,opt-in 默认关):EQ 初始化函数
    # 0x6072b24 两字(JL_EQ->CON0 0xfe1500/FLT_ADR 0xfe151c)write-only 整字
    # 记录——29,215,313 停点 0x6072b92(写 CON0=0)按消费面判定=JLA write-only
    # 记录先例(boot 路径只写不读:两写都是无条件整字 st.w、无 RMW;codex
    # 勘误 0x6072b68 bnez 目标=0x6072b90 只跳过分配+首次 memset,CON0/FLT_ADR
    # 两写必达)。codex gpt-6-astra 三选一定调:选 (c) 恰两字 write-only 记录,
    # 拒绝全族八字读回容器(当前推进零读,读回+合成初值会提前承诺未建立的
    # 消费语义;六字 0xfe1504..0xfe1518 零访问证据不得凭 __RW 推成可读回)。
    # 敏感性定调:两写纯 st.w 不读旧值,无 RMW 无读→初值零消费,不需要
    # ones 敏感性臂;验证=主臂(两写完成后 read_attempts 断言 0)+负例
    # (开关关仍停 29,215,313 @ 0x6072b92)。JL_EQ 是数据驱动地址族(基址
    # 0xfe1500 在 app.bin 仅 .data 0x60c3748 一处、零 li 常量,经 RAM 描述符
    # [0x12727C]+16 间接装载——li 枚举法免疫,S19MAP #7 漏判根因)。依赖边挂
    # jl_rand_seed=场景推进顺序(RAND 放行后链才到达停点),非硬件依赖。
    if jl_eq_init_writes:
        require(jl_rand_seed is not None,
                'the JL_EQ init write records only layer on the JL_RAND seed '
                'fixture (the EQ init call 0x607bf40 is reached only after the '
                'MAC regeneration passes — without the seed the world still '
                'stops at 29,215,313 @ 0x6072b92; the dependency encodes THIS '
                'execution order, not a hardware dependency)')
        options['jl_eq_init_writes']=True
        # EQ 静态上层缓冲窗随本开关挂载(P11_COMMON_CODE_WINDOW 随 p11_power_
        # init 同款先例):EQ 初始化第二 memset 写 [实例+4]=0x1a0600(app 链接器
        # 静态放置,.data 0x60c373c/0x60c3744 钉死 0x1a0600/size 0x280,堆描述符
        # 之外),不挂窗则 29,215,328 @ 0x4244 停在 unmapped_or_readonly_write;
        # 窗口范围/证据全文见 APP_UPPER_STATIC_BUFFERS 注释。
        eq_regions=(APP_UPPER_STATIC_BUFFERS,)
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
    # post_stop 透传(SIMB1F §5.2,probe 禁区解禁后的最小改):显式回调直通
    # dual_case,与键链场景互斥(一个场景一个 post_stop)。
    if post_stop is not None:
        require(key_chain_scan is None and key_chain_matrix is None,
                'explicit post_stop and the key-chain scenarios are exclusive (one post_stop)')
    if key_chain_scan is not None:
        require(p11_power_init and fd31_adc_pnd_completion and fd31_adc_res_named_values is not None,
                'the key-chain direct-scan scenario requires the full BY ADC projection stack')
        require(fd31_adc_res_named_values.get(KEY_CHAIN_CHANNEL_WORD) is not None,
                'the key-chain RES fixture must cover the key channel word')
        require(key_chain_matrix is None,
                'key_chain_scan and key_chain_matrix are separate scenarios (one post_stop)')
        post_stop=make_key_chain_post_stop(app)
    # key_chain_matrix(路 AJ/sim-keymatrix,opt-in 默认关):同一停点世界的三键
    # 矩阵多手势直调场景(S 路机制的矩阵扩展;S 路场景与报告保持不变)。依赖
    # 同一 BY ADC 投影栈;RES fixture 用矩阵自己的独立 dict(键通道缺省=多功
    # 能键按下档,采样切换在场景内)。
    if key_chain_matrix is not None:
        require(p11_power_init and fd31_adc_pnd_completion and fd31_adc_res_named_values is not None,
                'the key-chain matrix scenario requires the full BY ADC projection stack')
        require(fd31_adc_res_named_values.get(KEY_CHAIN_CHANNEL_WORD) is not None,
                'the key-chain matrix RES fixture must cover the key channel word')
        post_stop=make_key_chain_matrix_post_stop(app)
    # d1_acceptance(路 D1RUN/PR-2,PR2SPEC §4.1;registry probe_only 三条之
    # 开关):D1 验收观察窗场景——机器侧挂接=br28_machine d1_window 注册
    # (attach 构造观察器/after_step+note_stall 喂步/窗口耗尽停),probe 侧
    # 只传开关与 N;报告键组装走 post_stop 通道(stop kwarg 由 probe_boot
    # 停点后回调传回)。激活要求事件钟+Timer0/core-tick 交付+repeat 全开
    # (H1-H8 观察面的机制前提)。
    if d1_post_inject is not None:
        require(d1_post_inject=='s18_18',
                "the D1 post-inject variant only defines 's18_18' (direct 0x6000cba call)")
        require(d1_acceptance,'the D1 post-inject variant requires the D1 acceptance window')
        raise Blocked('d1_post_inject_pending_pr4',
                      note='explicit synthetic hypothesis; behavioral implementation belongs to PR-4')
    if d1_acceptance:
        require(event_clock and timer0_irq4_delivery and core_tick_irq3_delivery and repeat,
                'the D1 acceptance window requires the event clock, Timer0 IRQ4 and core-tick '
                'IRQ3 deliveries, and repeat delivery (H1-H8 observation prerequisites)')
        require(post_stop is None,'the D1 acceptance window owns its post_stop slot')
        machine_options['d1_window']=True
        if d1_window_steps is not None:machine_options['d1_window_steps']=d1_window_steps
        post_stop=make_d1_window_post_stop()
    return dual_case(module,app,header,rom,name,abstract=True,readonly=readonly,
                     machine_options=machine_options,options=options,budget=budget,
                     extra_diagnostics=extra or None,extra_regions=p11_regions+eq_regions,
                     extra_snapshots=p11_snapshots,post_stop=post_stop,snapshot=snapshot)


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


# 23 个 instruction_adapters 计数基线(条件场景 p11_heap_experimental_config;
# 历史注释写「22 键」,COPEXIMPL 加入 copex_exp22_model_execute 后未同步——
# 以 _ADAPTER_COUNT_KEYS 元组实际长度为准)。
# 推进前沿会漂移的 adapter 计数不再手工钉绝对值:期望值统一放在
# evidence/p11_heap_adapter_count_baseline.json,由 tools/capture_p11_heap_adapter_counts.py
# --recapture 重取。键集合写死在代码里(不能只遍历 JSON 现存键,防删键顺带删检查)。
_ADAPTER_COUNT_KEYS = ('exact_doubleword_store', 'exact_doubleword_store_cross_page_split',
                       'immediate_rep_multi_block_projection', 'zero_count_skip_is_assumption',
                       'register_rep_multi_block_projection', 'rep_copy_unknown_transport_load',
                       'rep_copy_unknown_transport_store', 'parallel_halfword_store_old_registers',
                       'parallel_insert_load_old_registers_is_assumption',
                       'parallel_byte_store_old_registers', 'lp_timer_bit_rmw',
                       'reset_unknown_preserving_configuration_rmw', 'mem_pwr_con_rmw',
                       'p33_awkup_bit_rmw', 'proven_dead_mem_pwr_con_read',
                       'abstract_task_stack_transport', 'cpu0_shared_lock_ownership',
                       'exact_full_word_store', 'mmio_rmw_always_writes',
                       'partial_icfg_bit9_only', 'partial_icfg_masked_branch',
                       'per_core_visible_cnum',
                       # 路 COPEXIMPL:mode-22 指数近似模型的执行计数
                       # (推进前沿会随 eq 系数链长度漂移,进基线钉值集合)。
                       'copex_exp22_model_execute',
                       # 路 SEGIMPL4:独立后增量半字/字节存储直写的执行计数
                       # (推进前沿会随清零环/拷贝环轮数漂移,进基线钉值集合)。
                       'exact_postincrement_subword_store')
_ADAPTER_COUNT_BASELINE = ROOT / 'evidence/p11_heap_adapter_count_baseline.json'


def _adapter_count_actual(adapters):
    """从场景 adapter 计数里取 22 个钉值键(缺失键记 None,交给比较判红)。"""
    return {key: adapters.get(key) for key in _ADAPTER_COUNT_KEYS}


def _adapter_count_baseline():
    """读 in-tree 22 键 adapter 计数基线(纯数据文件,不 import 任何工具模块)。"""
    return json.loads(_ADAPTER_COUNT_BASELINE.read_text())['counts']


def _adapter_count_mismatch(actual, expected):
    """逐键差异描述(空串=一致);require 消息用,比较本身保持纯计算。"""
    parts = []
    for key in _ADAPTER_COUNT_KEYS:
        a = actual.get(key)
        e = expected.get(key)
        if a == e:
            continue
        delta = f', delta {a - e:+d}' if isinstance(a, int) and isinstance(e, int) else ''
        parts.append(f'{key}={a!r} (expected {e!r}{delta})')
    return '; '.join(parts)


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
    # 第 15 次(result_reads[14])是 0xFF 头的真实计算值;后 15 次(16..30)是探测
    # 失败后 VM init 链继续运行时的真实 CRC 使用(跨越 core-tick 首溢出交付与
    # 其后的 power_init 公共段拷贝/堆信息消费)——失败分支确实继续执行的证据。
    crc_base=rows['crc_engine_roundtrip']['peripherals']['hardware_crc_projection']
    require(crc_ff['seed_writes'][:14]==crc_base['seed_writes'] and
            crc_ff['result_reads'][:14]==crc_base['result_reads'],
            'the erased fixture must not change the first fourteen CRC round trips')
    require(crc_ff['result_reads'][14]=='0x75f8' and len(crc_ff['seed_writes'])==30 and
            crc_ff['fed_bytes']==900 and len(crc_ff['result_reads'])==30,
            'the fifth probe entry must really CRC the 0xFF header bytes; the extended '
            'prefix (core-tick IRQ3 delivery + P11 power-init downstream) must carry '
            'fifteen further real CRC usages to the heap-info stop')
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
    # core_tick_irq3_delivery 已提入 base 层(本路):基座场景从 177,491 @
    # 0x12ba42(核心 tick IRQ3 首溢出 terminal 停点)推进到 299,063 @ 0x12b0c2
    # (堆描述符 readonly 放行后第 5 次溢出的 CNT 读点)。
    # CPU0 核心 tick 溢出共 4 次(40-tick 时基、PRD 1,199,999,周期 30,000
    # 模型步),每次按核仲裁取 IRQ3 向量槽 0x19fe0c=handler 0x60661a4、真实
    # 进入处理函数、`[0xffe0ec]|=0x40` ACK 一次(模型 core_tick_ack_rmw 只收
    # |=0x40 形状、不钉执行 PC——但 handler 由普通执行器逐步走 0x60661a4..
    # 0x60661e6,RTI 尾锚 0x60661e6 由 return_core_tick require 钉死,故 ACK
    # 必经 0x60661ca;此 PC 绑定=反汇编+尾锚的 derived-not-measured 结论)、
    # 0x60661e6 RTI 一次。4 次交付后 power_init 链走 P11 公共段拷贝/PWR_CON
    # 请求/堆信息消费(0xf27f4b 首读由 base 层 P2M 堆描述符 readonly 区放行,
    # 种子=静态推导候选 P11_HEAP_INFO_DERIVED)→pmu_task 创建 0x6067242
    # 往返调度器(再多一次 irq127 交付)→第 5 次 core tick 溢出落在 IPMASK=6
    # 临界区内被屏蔽、交付挂起(unresolved),固件在 0x12b0c2 读 core tick CNT
    # (r5=0xffe0f0)时诚实停(core_tick_overflow_requires_resolution;放行靠
    # conditional 的 prd_readback/cnt_held_snapshot,base 不取)。旧 293,277 @
    # 0x60679ba 读 0xf27f4b 即上一停点(堆信息首读)。
    # 前沿契约单点:停点/步数字面值从 frontier_registry 派生(推进前沿只改
    # registry,本处断言叙事保持手写)。
    crc_bankb_frontier=frontier('crc_bankb')
    require(r['stop_core']==0 and r['stop_pc']==crc_bankb_frontier['stop_pc'] and
            r['stop']['kind']==crc_bankb_frontier['stop']['kind'],
            'the base tier must carry the scenario past the P11 heap-info read to the '
            'fifth (masked) core-tick overflow CNT read, not stop terminal')
    require(r['completed_steps']==crc_bankb_frontier['completed_steps'],
            'the erased fixture scenario changed its step count')
    # CPU0 core tick:5 次到期、前 4 次交付并 |=0x40 ACK;第 5 次到期落在
    # IPMASK=6 临界区(pending=1、unresolved=1、ACK 仍 4)被 CNT 读点停住;
    # 40-tick 时基不变。
    tick=r['peripherals']['core_tick_configuration'][0]
    require(tick['con']==1 and tick['prd']==1199999 and tick['virtual_source_ticks_per_step']==40 and
            tick['expirations']==5 and tick['acknowledgements']==4 and
            tick['pending'] is True and tick['unresolved_overflow'] is True,
            'the CPU0 core tick must overflow five times, deliver/ACK the first four, '
            'and hold the fifth masked overflow (pending, unresolved) at the CNT read')
    ct=r['machine']['core_tick_entry_projection']
    require(ct['enabled'] is True and len(ct['deliveries'])==4 and
            all(d['core']==0 and d['irq']==3 and d['vector_address']=='0x19fe0c' and
                d['handler']=='0x60661a4' for d in ct['deliveries']),
            'every core-tick delivery must enter the real CPU0 IRQ3 handler via 0x19fe0c')
    first=ct['deliveries'][0]
    require(first['reti_from']=='0x12b608' and first['entry_banks'] and
            not first['entry_banks']['silicon_bank_switch_certified'] and
            first['pending_cleared_by_handler_ack'] is True and
            first['delivery_clears_pending'] is False,
            'the first delivery must be the masked-then-unmasked first overflow, keeping '
            'pending set for the handler |=0x40 ACK (not clearing it at entry)')
    adapters=r['instruction_adapters']
    require(adapters.get('core_tick_ack_rmw')==4 and adapters.get('conditional_core_tick_rti')==4 and
            adapters.get('core_tick_handler_frame')==8,
            'the four deliveries must each ACK once (|=0x40 RMW) and RTI once (0x60661e6), '
            'with the handler frame pushes/pops executed exactly twice per delivery')
    require(r['peripherals']['software_irq_commands']['commands']==17 and
            r['peripherals']['software_irq_commands']['acknowledgements']==9,
            'the extended VM-init sync requests plus the pmu_task scheduler round trip '
            'changed the software IRQ bookkeeping')
    deliveries=r['machine']['software_irq_entry_projection']['deliveries']
    require([(d['core'],d['irq'],d['handler'],d['reti_from']) for d in deliveries]==
            [(0,127,'0x12ad2a','0x12b608')]+[(1,125,'0x60662c8','0x12c5ca')]*7+
            [(0,127,'0x12ad2a','0x12b608')],
            'the extra cross-core delivery after the probe mismatch differs from the repeat projection')
    require(adapters.get('immediate_rep_multi_block_projection')==165 and
            adapters.get('zero_count_skip_is_assumption')==3 and
            adapters.get('register_rep_multi_block_projection')==26 and
            adapters.get('rep_copy_unknown_transport_load')==9 and
            adapters.get('rep_copy_unknown_transport_store')==9 and
            adapters.get('exact_doubleword_store')==26 and
            adapters.get('parallel_halfword_store_old_registers')==3,
            'the fixture-scenario REP projections differ from the observed prefix')
    # 显式关开关负例:core_tick_irq3_delivery=False 时必须逐字段复现旧前沿
    # 177,491 @ 0x12ba42(核心 tick IRQ3 首溢出 terminal、pending 置位、零 ACK)。
    neg=rows['crc_bankb_no_delivery_negative']
    require(neg['completed_steps']==177491 and neg['stop_core']==0 and neg['stop_pc']=='0x12ba42' and
            neg['stop']=={'kind':'core_tick_overflow_delivery_unresolved','core':0,'irq':3,
                          'note':'internal pending set; no guessed readback bit, wake or interrupt entry'},
            'the explicit no-delivery negative must still reproduce the strict 177,491 overflow stop')
    ntick=neg['peripherals']['core_tick_configuration'][0]
    require(ntick['pending'] is True and ntick['acknowledgements']==0 and
            ntick['unresolved_overflow'] is True and ntick['expirations']==1,
            'the negative arm must keep the terminal pending overflow (no delivery, no ACK)')
    require(neg['peripherals']['ipmask']==6,
            'the first overflow must sit behind the IPMASK 6 critical section (priority 1 masked)')
    require('core_tick_entry_projection' not in neg['machine'],
            'the negative arm must not enable the core-tick entry projection')
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
    # UART)。JL_IMAP 写侧归下一路三选一。前沿 3,073,402 @ 0x6001342
    # (unmapped_or_readonly_write 写 0xfd5c90)。
    # 路 V(sim-imapwr):jl_imap_write_records 放行 0x6001342 的 0xfd5c90=
    # 0x2f(=USBDP_IN,识别见 jl_imap 断言)后,RX 复用链完成,链继续 +516 步
    # (无新停点:BAUD/OTCNT/RX 三连写尚未到达),停在 **0x60340d8** 复合
    # RMW `[r0+4] &= 0xFFFFFFEF` 的内部读——**读 0xfd2004(JL_UART0->CON1)**,
    # kind `jl_uart_init_word_readback_not_modeled`(路 M 白名单的读侧守卫
    # 如设计显形)。SDK uart_v1.c.o `uart_fifo_reset` 序列钉死该位语义:
    # 临界区内 CON1 |= 16(bit4)→ 写 RXSADR/RXEADR/RXCNT(RAM 装载的 RX DMA
    # 地址/计数)→ CON1 &= -17(bit4 清零,RX DMA 重启)——**bit4 是 RX DMA
    # 重启位,该 RMW 读回是真实消费(读值参与写回)**,读回类真决策点,
    # 照 codex 红线诚实停,归下一路三选一(CON1 读回投影=lp_timer_con_
    # readback 先例「只在写过之后返回最后写值」是自然候选)。停点在
    # 0x60340cc 进入的 OS unmask 临界区内(IPMASK 0→6,非 CLI bit9 锁,
    # ICFG 保持 0x300——与前两个 GPIO 停点的 0x100 不同)。路 V 前沿
    # 3,073,918 @ 0x60340d8。
    # **路 AA 落地(sim-con1rb,本前沿)**:opt-in `jl_uart_con1_readback`
    # (CON1 读回=最后写值,lp_timer CON 路 AL/fd31 ADC CON 路 BN 同族;只在
    # 固件已写过至少一次后返回,从未写过仍诚实停;条件假设「读回=软件配置值」
    # derived-not-measured——__RW 不能证明无硬件自更新/自清零位,报告
    # assumptions 全文标注;只扩 CON1,BAUD/OTCNT/RXSADR/RXEADR/RXCNT/CON2
    # 读回仍严格停,CON0 读侧走路 F 投影不动)放行该读——RMW 走 probe_boot
    # 通用 mmio 完整路径:内部读 0(最后写值)、0&0xFFFFFFEF=0、**写回照常
    # 落写记录**(平凡零发明,非捷径)。放行后 **uart_init 事务全部收尾**
    # (与路 U 前瞻一致,零新停点):BAUD=0x270×2(0x603453c/0x60346a4 两次
    # baudrate,整字写白名单)、OTCNT=0x1e834(0x601d1c2)、RX 三连写
    # RXSADR=0x125bfc/RXEADR=0x125e1c/RXCNT=0x220(0x6034668..cc,环形缓冲
    # 544 字节)、CON0 |=0x68/|=0x4→末值 **0x346f**(路 F 容器,bit3/5/6
    # RX DMA/使能族+bit2 RX irq);[cfg+4]!=208 条件分支未进 PORTP 五连
    # (gpio 容器域,本前缀零 PORTP RMW);忙检查单线 UART 恒等返回。
    # **UART IRQ 使能观察(codex 要求,标记过滤的 instrumented 运行钉死)**:
    # 新前缀恰一条 ICU 使能写——事件/回调注册 0x603468e `call 0x6065d86`
    # (request 形状:0x6065d90 向量槽 [0x19FE00+irq*4]=handler、0x6065dbe
    # 优先级钳制 ≥5→5、nibble=(5<<1)|1=0xb)把 **IRQ12 以优先级 5 使能**
    # (0xffe104 word1 nibble4:0→0xb;handler=0x60355e0=UART0 IRQ veneer,
    # 表 0x60a1f1c 首项;IRQ 号来自配置字节 b[表+27],实测 12=UART0)。
    # **首次 pending 判定:IRQ12 使能后零 pending、零交付请求**——模型无
    # UART RX 输入源,ICU 使能只是资格登记,永不发明 UART 事件;新前缀交付
    # 仍全部是 Timer0/core tick/软件 IRQ 家族(287/9/11 全不变)。事务完成
    # 后世界进入**事件域**:app_core 尾 0x6011fee 的 init 调用链 0x60c32a4..
    # 0x60c32b8(`r0=[r4++=4]; call r0` 顺序调用 W1[0] 0x6012e3e/0x601321e/
    # 0x6013246 之后)第 4 项 **0x602ea68** 的首指令 `st.w.modimm
    # [0xfe2200]=1` 撞 unmapped——**0xfe2200 = br28.h JL_WL_AUD->CON0**
    # (hs_base+map_adr(0x22,0),单成员 __RW 结构,SDK 头有名),音频/无线
    # 域首个新边界:整字写投影(记录/读回/钉住三选一)是下一路独立决策点,
    # 不顺带套用最后写值(codex 红线)。**新前沿 3,079,241 @ 0x602ea72**
    # (unmapped_or_readonly_write 写 0xfe2200=1)。
    # **路 AK 落地(sim-audioinit,本前沿)**:opt-in `wl_aud_con_writes`+
    # `audio_dac_init_writes`(docs/wlaud-init-map.md 路 AG 预研的已审查写集,
    # 分域两开关:JL_WL_AUD CON0 单字域 + DAC init 三字域,后者层叠前者)放行
    # 0x602ea72 的 JL_WL_AUD CON0=1 整字写(写记录落地,+1,760 步全真实执行)后,
    # **实际停点与 AG 图预期不符、如实停住**:0x602ea68 头部场景 walker 段
    # (0x602ea76..0x602eb0a,AG 图 §3.1 判「通过/无停点」——该审计只覆盖 SFR
    # 访问与 RAM 性,未覆盖 st.d×MMU 页交互)内,0x602ea76→0x60755b2(magic
    # 0x7674)→0x60755c2 call 0x6074234→0x607425c call 0x6007ae8(配置表查名
    # helper:清 16B 局部缓冲+ROM 0x1fd98 表查找)的函数头 0x6007af2
    # `st.d.sp.l d[sp+8]=r1_r0` 在 **app_core 虚拟栈** sp+8=0x400afc 上跨越
    # 128B MMU 页界((addr&127)+8=132>128)——全 boot 3M+ 前缀首例跨页双字
    # 存储此前全部落在物理栈/页内),0x400afc..0x400b003 双页翻译未拆分,
    # mmu_cross_page_access_not_split 诚实停。**这是 ISA/MMU 存储模型边界,
    # 非外设边界**:BR28 硬件对跨页 st.d 的真实行为(拆两次访问/对齐异常/
    # 栈对齐使其实际不可达)无任何测量证据,归下一路三选一决策,本路不硬闯。
    # 因此 AG 图的 S1..S5(DAC init SFR 簇 0x6071cbc..ee)**本前缀未到达**
    # ——audio_dac_init 容器已登记但零写零读(报告如实),G6 门(0x602ec5e)
    # 与喂狗调用点(esco 块内)也未到达,零 watchdog 访问(0xf2a800 族零触,
    # 否则会先以 unmapped_or_readonly_write 显形)。**新前沿 3,081,001 @
    # 0x6007af2**(mmu_cross_page_access_not_split 0x400afc size 8)。
    # **路 AN 落地(sim-crosspage,本前沿)**:opt-in `mmu_cross_page_split`
    # (跨页双字存储拆两半各自 MMU 翻译,derived-not-measured,开关注释里有
    # 完整证据链:上游 m2 模型本就是两个独立 4 字节事务+AC791N 真机三例
    # 128B 界跨越样本+现场两页物理倒序可区分性+SP 审计)放行 0x6007af2 后,
    # 链继续 +56,572 步全真实执行:5× 场景表 walker(0x7674/0xD96F/0xB9D1/
    # 0x9C2D/0xDC74)逐个跑配置表查名 helper(跨页 st.d 拆分执行合计 5 次,
    # 页内双字 30→35)、每个 walker 6 轮硬件 CRC(全零种子,结果六值循环
    # 0xa1cd/0x9946/0xd1cf/0x239e/0x7dee/0x91a9 ×5,CRC 32→62 轮)、zalloc/
    # 配置 init/ROM memset(寄存器计数 rep 26→112、rep.i 体 165→320),随后
    # 0x60719a6 函数(设备结构 0x1265C0 族的 ADC 节点 reset:b[0x1267E1] 配置
    # 门==0 分支)体内并行对 0x60719c0 `r3=r1+4596 ∥ [r3+0]=r4` 以旧 r3=
    # 0xfe0538 写 0——**JL_AUDIO+0x38 ADC_CON(路 AE br28.h 钉死命名),路 AK
    # 范围边界明确不进白名单的条件族首位**,诚实停(unmapped_or_readonly_
    # write),归下一路三选一。S1..S5(0x6071cbc..ee)仍在其后未到达,audio
    # 容器继续零写零读。**新前沿 3,137,573 @ 0x60719c0**(unmapped_or_
    # readonly_write 0xfe0538=0)。
    # **路 AS 落地(sim-adcreset,本前沿)**:opt-in `jl_audio_adc_reset_writes`
    # (域三,独立子开关:AK 域二边界断言钉死「恰三字+条件族不进」,ADC 输入
    # 侧与 DAC 输出侧分域;依赖边只挂域一——执行序 0x60719a6 在 0x602ea72
    # 之后、S1..S5 之前)放行 0x60719c0 并行对旧 r3 写 [ADC_CON]=0 后,链
    # +32,789 步全真实执行:0x60719a6 ADC 节点 reset 完成(guard b[0x127801]
    # 置 1、hdl 0x1277B4 初始化落盘、槽 [0x126A90]←hdl,动态互证;**勘误:
    # 路 AN 叙事门地址 b[0x1267E1] 是手算笔误,指令编码钉死 0x1265C0+0x1241
    # =0x127801**)→ 0x6014162 walker(通道位图=G6 门字=0,per-channel
    # 0x606de62 ADA_CON8 打开链死代码不执行)→ handler 链表/G3/**S1..S5
    # DAC init SFR 簇全落地**(AK 域二容器 5 写:0xfe0500×3/0xfe0558×1/
    # 0xfe0900×1+RMW 内部读 4;**S1 值勘误:0x6071cbc 存储是并行对旧 r1 半,
    # 实测 0x0,AG 图「=0xfe2200 数值巧合」是并行对误读,0xfe2200 是 S2 写
    # 基址**;0x6071cbe 第二写落 wl_aud 容器)→ 0x602ea68 尾块+后续 initcall
    # 全过,期间 **Timer0 第 5 次到期(3,139,140)落在 ROM 0x4180 寄存器
    # rep 拷贝体内**——probe_boot 交付分发按路 CB 延期先例扩到指令中途态
    # (after_then/repeat 在途挂起不丢弃,硅上 rep/并行对是单条指令、IRQ 在
    # 指令边界识别;此前=未捕 ValueError 硬崩,现 30 次延期事件、其中一次
    # 在块尾交付 reti_from=0x4196=rep 块出口,延期-交付往返机制实证),停在
    # **0x606b426 读 0xfe0a04**——br28.h `JL_ADDA=hs_base+map_adr(0x0a,0)`
    # 的 **DAA_CON1(+0x04,__RW,DAC 侧模拟配置字;+0x20 起才是 AE 钉的
    # ADA_CON 族,布局互证)**,0x606b40e 配置 RMW insert 函数(读→旧值>>3
    # 入 bits3..31+flash 表 0x060A8548[chan≤3] 位段 insert→写回;动态调用
    # 链经尾调用 veneer 0x606db6c,rets=0x606db72)的首读。**这是音频 init
    # 尾段第一个非掩码平凡的 RMW 读**:读值实质参与写回(不同于 S3/S5 的
    # 0|1=1/0&~0x40=0 平凡合成),且本世界该字从未被固件写过——读的是
    # 「DAC 模拟块寄存器内容」这个硬件域状态,投影世界无从推导=**首个真实
    # 硬件反馈依赖**(本轮验收达成:已知音频初始化写序列 S0/ADC reset/
    # S1..S5 已全部执行完,前沿如约停在写序列之外的首个硬件内容依赖点)。
    # 非轮询(无循环读);三选一(具名 fixture/读回容器合成 0/钉住)归下一
    # 路,本路如实停。新前沿 3,170,362 @ 0x606b426(unmapped_memory 读
    # 0xfe0a04)。
    # **路 CV 落地(sim-adda,本前沿)**:DAA_CON1 首个硬件反馈依赖按 codex
    # 四点定调处理——**①两开关分立**(显式初值 fixture `jl_adda_daa_con1_
    # initial_fixture`〔值=JL_ADDA_DAA_CON1_INITIAL_PROBE 探针对 {0x0,
    # 0xfffffff8} 之一,无隐式零默认,报告 fixture 子键记名称/来源/适用场景
    # +label=sensitivity-probe-not-plausible-state〕+配置位读回
    # `jl_adda_daa_con1_config_readback`〔真实写入后更新、后续读用当前状态
    # =最后写值;无「合成初始 0」分支〕;只开读回无初值未写入、或初值开
    # 读回关写后读,均停原边界)。**②不一次放行整个 ADDA 读族**:白名单恰
    # 一字 0xfe0a04,DAA_CON0/2、ADA_CON 族、DAC_VL0 保持 unmapped,后续读
    # 逐个分类。**③静态核对**(全文见 docs/adda-daa-con1.md+容器类注释):
    # RMW 0x606b40e 完整链=钳档位→表 r3=b[**0x060A8608**+r1]({0,4,6,7};
    # **勘误:AS 叙事 0x060A8548 是转写笔误,指令编码+表字节实测互证**)→
    # 首读→bit0 块 bits0..2 换表码(bits3..31 保留)→bit1 块 bits4..6 换表码
    # →**写回在两条件块之外**(未选通道=旧值原样回写)→返回;**veneer
    # 0x606db6c 是真调用不是跳转**([--sp]=rets;call 0x606b40e;pc=[sp++]——
    # fixer rts 回 0x606db72 运行态实测,原始调用者返回地址在 fixer 帧下方
    # 栈上);本前缀唯一调用点=0x6071b70(r0=0xFF/r1=0,运行态 r0/r3/rets
    # 三方互证;walker 0x606dc00/直调 0x606b45c、0x6071d92 不在)。**④敏感
    # 性对照**:同停点分叉探针 0x0 与 0xfffffff8——首写回 0x0 vs
    # 0xffffff88(bits4..6 清位路径真实执行)、写回差异不进任何分支/消费,
    # 两侧同 +17 步同停点 0x606db4c=**观察路径对初值不敏感**(保留位 bits3,
    # 7..31 被写持久携带但无消费者);「仿真顺利返回不能证明初值正确」。
    # **放行推进(+17 步)**:fixture=0x0+读回开,RMW 写回落记录(恰一条
    # 0x0),fixer 经 veneer 返回 0x6071b74,0x6071b76 call setter 0x606db36
    # (DAC_VL0 数字音量,r0=0xFF/r1=0,体积上限 [0x1265C0+1883*4]=0x12832C
    # 运行态 0x3a98;**勘误:原记 [0x1272AC] 是算术笔误,路 CW 按指令编码
    # li.s16 n=1883 修正**),停在
    # **0x606db4c 读 0xfe051c=JL_AUDIO->DAC_VL0**——并行对第二半 `r3=[r2+0]`
    # 的 RMW 内部读(读值经 umin 钳制+半字 insert 组装进写回)。**下一停点
    # 分类=配置**(数字音量配置字,br28.h __RW;BO 消费者全图:getter/
    # setter/同步 walker/静音判零门控,全配置面消费——对照 DAA_CON2 bit1=
    # 状态位、CHIP_ID=身份,分类依据在 docs/adda-daa-con1.md §4):归下一路
    # 同法处理,本路如实停。**新前沿 3,170,379 @ 0x606db4c**(unmapped_memory
    # 读 0xfe051c)。
    # **路 CW 落地(S19 批 0b/sim-s19b0,本前沿)**:DAC_VL0 配置读按批 0b
    # 判据放行(读值只进 insert 不进分支、双通道调用写回恒 0x0→合成 0+
    # 敏感性探针,全文见 jl_audio_dac_vl0_readback 接线注释与容器类注释),
    # event_clock 进条件栈(BG §4.2 草案,机器件 BQ 已备,本路只接线)。
    # **放行推进(+31,769 步)**:setter 0x606db36 全链真实执行(合成 0 首读
    # ×1+写回 0x0×1+写后零读)→0x6071b7a call 0x6064162 dev 层全 RAM→DAC-init
    # 驱动继续:0x6071d16..d4e 从配置字算 DMA 缓冲长(rep 30 r7 体)、
    # **0x6071d50 call ROM memset(0x18e180,0,0xf000)**——ROM 0x4242 memset
    # 适配器的 count≤10000 护栏被首个大缓冲清零(0x3c00 字)撞上,护栏非
    # 硬件声称按观测形状抬到 100000(适配器三项语义假设零变化,legacy 前
    # 缔从未到达该调用点),15360 次 rep 字存储(exact_postincrement_store
    # +15,360)把 60KB 堆缓冲真实清零→rets=0x6071e8c 的调用者继续→
    # **0x606ab0a 函数**:AUD_CON(0xfe0558)|=0x100+DAC_CON(0xfe0500)五连
    # RMW(AK 容器服务内部读)后,**0x606ab2c `st.w.modimm [r5+52]=0x7F`
    # 写 0xfe0534=JL_AUDIO->DAC_COP(br28.h +0x34 成员 13 __WO,DAC chopper/
    # 泵配置字)撞 unmapped_or_readonly_write 诚实停**。这是 W1[3] 尾段
    # DAC 簇里 AK 白名单({DAC_CON,AUD_CON,CLK_CON}+AS 的 ADC_CON+CV 的
    # DAA_CON1+本路 DAC_VL0)之外的下一个成员——**写侧整字写、__WO 标注**,
    # 三选一(write-only 记录/钉住/fixture)归下一路,本路如实停。
    # **event_clock 观察(BI 首验观察项,只观察不硬断言)**:本前缀
    # virtual_model_steps_advanced=0——CPU0 全程运行从未停泊(事件窗口只有
    # CPU1 的停泊),**W2[0] 的首次全核 IDLE(os_time_dly)在新停点之后未
    # 到达**,开关对旧前缀惰性(接线就位,批 1 放行 DAC_COP 后才可能有
    # 消费者);敏感性探针 ones 字 0xffffffff 一次性分叉:同 +31,769 步、
    # 同停点 0x606ab2c 逐字段一致、DAC_VL0 写回同 0x0=观察路径对初值不
    # 敏感(第二块 r3=r3.l(u) 清掉第一块携带的全部读值位,静态预测兑现)。
    # 计数:CRC 32 轮 3041 字节不变;IRQ commands/acks 22/11→24/12(新前缀
    # 一次跨核同步);Timer0 到期 309→311/交付 rti 299→301(PRD 尾段重编
    # 59999→47999=12ms 窗);core tick 无新溢出。旧前沿 3,202,148 @
    # 0x606ab2c(unmapped_or_readonly_write 0xfe0534,值 0x7f)。
    # **路 B1 落地(S19 冲刺批 1/sim-s19b1,本前沿)**:W4[4] 簇四件+尾段
    # 二次勘误件同 PR——①PVDD 容器(jl_pvdd_trim_writes:P3_ANA_CON13/
    # P3_ANA_CON1/P3_PVDD0_AUTO 合成初始字 0+读回=当前值,AM 容器族语义)
    # ②TIMER2 W1 投影(timer2_delay_projection:Timer0 家族位图+LRC 分数
    # 计数+CON bit15 pending 读回+锚点 0x12cd76 完成唤醒 kick 先例+闭式
    # horizon 并入 min();单测钉死 1ms=恰 4000 模型步)③VLVD canary
    # (p33_canary:首读 fixture 0xf2c044=0,五条触发即停报警接线,写后/
    # 重复读全严格停)④DAC_COP 处置(jl_audio_dac_cop_writes:全 app 恰两
    # 写点零读点,按消费面判定=write-only 记录)⑤**W1[3] 尾段二次勘误件**
    # (jl_audio_dac_dma_writes:放行 DAC_COP 后 +35 步撞 0x6071e92 写
    # 0xfe0508=1——DAC 启动函数 0x6071e74 还有四个 DAC DMA 配置整字写
    # [r10+8]=1/[r10+4]=0x18e180/[r10+20]=0/[r10+16]=0,0x18e180 与路 CW
    # 的 60KB memset 目的地同址互证;探路实证恰四写零读回零 RMW=同 idiom
    # write-only 记录)。另 FD31 RES 行扩至 S19 值表(+0x10005=400、
    # +0x10001=4,BM 勘误后的显式 synthetic 标注)。
    # **放行推进(+545,430 步,全 CPU0)**:DAC_COP/DMA 四写放行后 DAC 启动
    # 尾段真实执行(AK 容器再服务八条 DAC_CON 写回 0x8.. 系列,见音频 init
    # 断言),随后代码进入 OS 延时等待族——Timer0 再越 61 次边界(到期
    # 311→372、交付/ACK 301→362,新交付 reti_from 全在延时等待循环
    # 0x12e282/84/86 三址;PRD 被最新 delay 重编回 1ms 窗 11999),期间
    # LP 定时器/LRC 周期链再走 2 帧(LRCT 位 RMW 54→64、trim 读 20→24、
    # fixture 读 10→12、计数字节 10→12)、GPCNT 任务再调度 2 次(读 9→11)、
    # fd31 CON 读 +3(259→262,RES 零新读)、core tick 第 11 次溢出交付
    # /ACK(10→11)后**第 12 次溢出落在 OS 临界区内(IPMASK 6 屏蔽 IRQ3
    # prio1)未交付未 ACK**,固件 OS 时间更新路径(Timer0 handler #362 的
    # tick 处理→包装 0x12b14c→内核定时器编程助手 0x12b0ba)随即在并行对
    # 第二半读 core-tick PRD——**0x12b0c2 `mov.rr.p r10=r2 # ld.w r3=[r5+4]`
    # (r5=0xffe0f0,读 0xffe0f4)**撞 unresolved_overflow 严格停(BF core-
    # tick 域既有边界,CoreTickStopped.read 拒绝溢出未决期间的一切访问)。
    # **BM §W4 预期分支未执行(如实记录)**:TIMER2 助手 0x12cd26 零写、
    # canary 树零访问(首读 fixture 零服务)、PVDD 三字零写(保持合成初始
    # 0)、RES 新行(0x10005/0x10001)零消费、邮箱轮询 0x12cc7e/0x12CC94 未
    # 到达、eclock 仍零推进(CPU0 全程未停泊,锚点停泊核从未出现)——批 1
    # 四件全部落地并由单测验证,但本前缀的真实执行没有到达它们的任何运行
    # 态消费者;世界在 BF core-tick 边界诚实停,归批 2 前置处理。
    # **路 BQ 落地(S19 冲刺批 2 一案,本前沿)**:3,747,578 的 core-tick PRD
    # 读边界按 codex 定调三查分类+一案先行——①r5+4=0xffe0f4=BASES[0]+8=
    # PRD/核 0/ld.w 宽 4;②异常由 CoreTickStopped.read() 头部通用 overflow
    # guard 抛(先于 offset 分派,判定表第一行「guard 挡 PRD」);③PRD 最近
    # 真实写入=tick_timer_init 0x606622c 的 [r5+8]=1199999(模型步 149,644,
    # 写日志钉死),此后零重编程(运行中 PRD 写被 active_core_tick_
    # reconfiguration_unverified 挡住,3.75M 步零发生)。「恢复后自然交付」
    # 由既有 BF 每步重检机制承担(溢出 1-11 全交付+屏蔽窗探针场景回归)
    # =只有读侧问题 → core_tick_prd_readback 落地:unresolved 窗内只放行
    # offset 8 读=回放 prd_write,缺失继续停,CON/CNT/pending/ACK/计时相位
    # 全不动;第二案 held 重新仲裁未加(放行后未复现「IPMASK 恢复后漏仲裁」
    # ——新停点仍在屏蔽窗内,窗口从未结束)。**放行推进 +7 步**:0x12b0c2
    # 并行对完整提交(A 半 r10=旧 r2=0、B 半 r3=1199999——并行对一致锚点
    # 运行态实证)→ 0x12b0d0 PRD>9 分支真走 → 0x12b0de r2==0 跳过 cli veneer
    # (arg2=0:调用者已在临界区内)→ 0x12b0e6 jiffies 缓存读 [0x125004] →
    # **0x12b0e8 `ld.w r1=[r5+0]` 读 CNT(0xffe0f0)撞同一通用 overflow
    # guard 严格停**——CNT 读回不在一案范围(codex:「不改 CNT」),归下
    # 一轮三选一;第 12 次溢出仍 held 未决未 ACK(expirations 12/acks 11、
    # pending True、cnt 0、IPMASK 6、ICFG 0x300 全不变)。
    # 路 B3(S19 批 2 续)推进叙事:3,747,585 的 CNT 读(0x12b0e8,retry 环
    # 第一半)由 core_tick_cnt_held_snapshot 放行——unresolved 窗内返回模型
    # 实际存储的溢出后冻结快照(该场景对齐溢出余数恰 0;显式「溢出后 CNT
    # 冻结」假设 assumed-not-measured,非 PRD-1/非周期末值;读无副作用)。
    # retry 环 0x12b0e6..0x12b0f6(jiffies/CNT/PRD/CNT/PRD/jiffies 快照一致性)
    # 首次全通过 → 单调检查 0x12b108 → *arg1=CNT*1000/(PRD/10) sub-tick →
    # 包装 0x12b14c 组合 ms;第 12 次溢出的屏蔽窗随后结束,溢出 12..108 全
    # 交付/ACK(108/108)。链继续 +773,498 步后撞 **4,521,083 @ 0x606ac42**
    # (音频 DAC 模拟 init 函数 0x606abf0 首条 DAA_CON0 bit13 清位 RMW 的内部
    # 读)→ jl_adda_analog_family 五字容器放行(静态全函数枚举白名单,合成
    # 初始 0+读回=当前值;94 写全服务)→ 链滑过模拟 init 尾段与 OS 等待族
    # (忙等 µs 延时+停泊 os_time_dly——事件时钟实测推进 13,429,113 虚拟步)
    # 后停在 **19,139,239 @ 0x606abbe**:DAC 启动函数 0x6071e74 尾段
    # 0x60720ca 调 getter 0x606ab92(tbb 按 selector 0..11 分发)首读
    # **0xfe0524=JL_AUDIO->DAC_TM0**(br28.h JL_AUDIO_TypeDef +0x24 __RW,
    # 硬件维护的 DAC 时间标记/缓冲状态)——音频数字域运行态首读(路 BO
    # §5「0xfe05xx 全块 D1 后另立证据包」分类的运行时显形),读值是 DAC
    # 启动后硬件域活动状态、投影世界无从推导,诚实停归下一路三选一。
    # 路 DT(S19 批 2 续二)jl_audio_dac_tm_readback 放行:全量反汇编证实
    # 0x606ab92 是 DAC_TM0/TM1 半字 field SETTER(RMW)非运行态 getter
    # (12 路 tbb:0/2→TM0 低半、1/3→TM1 低半、4..7→裸 rts、8/10→TM0 高半、
    # 9/11→TM1 高半;读值只进写回组装零分支;全 app 无 setter 外读者)→
    # B3 交接「getter/硬件时间标记被读」预判撤销,AM 容器族放行(合成初始
    # 0+读回=当前值,初值来源 synthetic_zero 具名登记;codex 四点定调)。
    # 放行后两次 -600 半字写实测 TM0=TM1=0xFDA80000(第二次 selector∈
    # {9,11} 写 TM1 高半,静态预测兑现),链继续 DAC 启动尾段(zalloc(56)+
    # 分配对象填充)+OS 等待族再 2,658,967 步,停在 **21,798,206 @
    # 0x606b5ee**:JL_ADDA 块 **0xfe0a20=ADA_CON0**(br28.h +0x20,DAA_CON4/
    # RESERVED5/6/DAA_CON7 之后第一个 ADA 配置字)`[r6+0]&=0xFFFFFFDF`
    # bit5 清位 RMW 内部读——音频 ADC init 簇(0x606b4xx..b6xx,邻字 +0x48
    # ADDA_CON1/+0x4c 无名已由 B3 容器服务)配置 RMW 族,归下一路(照 B3
    # 先例候选=容器白名单扩展,记录即停)。
    # 路 ADA(S19 批 2 续三)jl_adda_ada_family 放行:静态全图(逐 2 字节
    # 独立解码枚举 li 常量+站点窗口数据流核查)证实 ADA 子族窗
    # 0xfe0a20..0xfe0a44 的全部触达=7 个直接 li 基址站点(9 实例),全部
    # 配置 RMW/组装形状、读值零分支消费(停点函数 0x606b4d0..0x606c3a8
    # 内 309 原子 RMW+51 ld 逐一核查;close 族 0x606f4c0/0x606f438 纯清位
    # 零装载;field setter 0x606c3b0/0x606de9c/0x606ee9a 通道掩码门控组装;
    # 0xfe0a44 ADA_CON9 全 app 零触达;0xfe0a00 基址 8 站点无一触 ADA 窗)
    # →按 B3/DT 判据归 AM 容器族放行(合成初始 0+读回=当前值,恰九字
    # 白名单;codex 定调四点:分类成立但「全 app 完备性」措辞=已识别直接
    # li 站点而非穷尽间接来源;新开关分立不扩 B3 WORDS——直接扩会静默改
    # 变已开场景服务面;同址新读者须重审——地址白名单只拦新地址;
    # close 族清位服务合成配置字非硅状态;布局勘误 0xfe0a48/4c=ADDA_CON0/
    # ADDA_CON1〔audio_anc.c 读 ADA_CON8 四通道 5 位 mic 增益与 0x606de9c/
    # 0x606ee9a 增益 setter 互证〕,B3 文本 legacy 钉死不动、勘误在 ADA
    # 容器报告登记)。放行后停点 RMW 执行,链再 +51 步(DT 段的 0x606b5b6..
    # 5ea 已含 delay(20) 忙等;本段从 0x606b5f2 起)停在 **21,798,257 @
    # 0x606b68e**:`[0xf2b404] |= 0x10000000` RMW 内部读——**0xf2b404 =
    # P11_SFR LPVAD 组 map_adr(0x14,0x01) VAD_ACON0**(p11_sfr.h
    # __RW __u32,低功耗语音活动检测;0xf2b400 块全 app 唯一 li 站点即
    # 0x606b688 本处)——P11 域配置 RMW 族首读,bit28 置位形状与
    # p11_window 0xf2a0c0 `|=1` 同形,候选=lp_timer_con_readback 式配置
    # 读回投影或 p11_window write-only 位记录扩展(0xf2b404 不在现有
    # p11_window 白名单),归下一路三选一(记录即停)。
    # 路 LPV:LPVAD VAD_ACON0 单字容器(p11_lpvad_config)放行后停点 RMW 执行,
    # 链再 +2,068 步(AADA 窗尾段+二级 tbh 分发 case 体+RAM 对象链+缓冲配置
    # walk)停在 **21,800,325 @ 0x606df1e**:`mul.rr.p r2 *= r1` **并行对**
    # 第二半 store 写 0xfe053c(值 0x00419264=真实指令结果;路 AS/AN 记录的
    # 0x60719c0 并行对 store 半同族)——**0xfe053c = JL_AUDIO+0x3c ADC 通道
    # 缓冲 ADR 字**(csfr.h __u16→32 位字槽成员 15;路 AE 钉的 ADC_CON
    # +0x38 邻字;D2-b 注入蓝图 set_buffer 0x606dee4 五写 {+0x3c ADR/+0x40
    # LEN/+0x44 PNS/+0x48 HWP/+0x4c SRP} 之首)——JL_AUDIO 数字域写族
    # (AK 三字+AS ADC_CON+CV DAA_CON1+CW DAC_VL0+批 1 DAC_COP/DMA 族)白
    # 名单外下一成员,写侧整字写,三选一(write-only 记录/整族容器/钉住)归
    # 下一路(记录即停)。
    # 路 JLA:ADC DMA 五字 write-only 记录(jl_audio_adc_dma_writes)放行后
    # set_buffer 五写完成(ADR=0x419264 大缓冲址/LEN=0x100/PNS=0x80/HWP=
    # SRP=0,值全真实指令结果;首写=停点并行对 store 半本尊),链再 **+130
    # 步**(挂 handler 段+DAC_COP 第二写点 0x606e038 到达+ADC_CON 邻字
    # AUD_CON 0xfe0558 查重 bit4 清位 RMW——AS/AK 容器服务)停在
    # **21,800,455 @ 0x606e044**:启动函数 0x606df66 的 `rmw.or.modimm
    # [r0+0] |= 0x2` 内部读 **0xfe0904 = JL_ASS+4**(r0=r5+976 构造自
    # 0xfe0534 基址,**无直接 li 站点**——间接构造地址的首个停点实证);
    # 后续 0x606e048 &=~0x1 与 0x606e050 起 [r0+4]=0xfe0908 字段 insert 族
    # (bits0..2/3..5/6..8/9..11)同窗——JL_ASS 时钟/源选择 RMW 族首读,
    # 初分类=配置 RMW 读族候选(读值只进写回组装),三选一归下一路。
    # 路 ASS:ADC 启动配置块四字读回容器(jl_adc_start_config)放行后停点
    # RMW/insert 族执行(容器 9 写:MIC_CTL0 0x2/0x2、MIC_CTL1 终值 0xff8、
    # ADC_CON1 三写全 0;MBG_CON [sp] 门控关零写;3 合成初始读+6 写后读),
    # 链再 **+6,963,729 步**(音频 pipeline 多轮重配:AS ADC_CON 1→12 写、
    # B3 0xfe0a0c +23、ADA 0xfe0a24/30 +14、CV 0x33/0x73 PGA 切换、DT TM
    # ×4、CRC +8 轮 65405 字节、core tick 116→140、Timer0 3671→4401——
    # 事件时钟 13,429,113 冻结不变=纯 CPU0 顺序段,per-core {0:+6,964,483,
    # 1:0})停在 **28,764,184 @ 0x606c5ba**:`ld.w r0=[r7+0]` 读
    # **0x61fbfe0**(r7=[sp+8]=构造链 0x600B15C 族写入栈记录 +8 槽的指针;
    # 0x61fbfe0=0x6200000-0x4020=2MB flash 顶-0x4020,**在 SFC 明文区
    # 0xe6000 之外**——app 镜像外/固件文件外)——函数 [0x606c54e,
    # 0x606c628) 资源查找链(RAM 0x1265C0+0x4FC 配置门→锁对象 0x126E90→
    # 名字池 0x60A1DCE→按名开资源 call 0x6007ae8→虚方法 0x6060980 写栈记录)
    # 后首读,初分类=flash 顶参数/资源区指针读族候选(值来源=下一路
    # 第一课题,全图存档 ~/tmp/ass-fn-0x606c54e.txt),三选一归下一路。
    # 路 FLS:flash 顶 sdfile ANCIF/ANCIF1 官方资源内容 fixture
    # (ancif_official_resources)放行。值来源全图(本路第一课题,已解):
    # 停点 0x61fbfe0=sdfile「app 分区」文件 ANCIF 的头首字——app 分区表在
    # flash 0x06000080(镜像内,32 字节项 {头CRC@0,virt@4,size@8,magic
    # 0x8192@12,终止标记@14,名字@16})恰三项 BTIF/ANCIF/ANCIF1,换算对
    # [0x127290]=0x2020/[0x127294]=0x06000000(CPU=virt-0x2020+0x06000000,
    # BTIF 0x61ddfe0=既有 erased_bankb 窗 2 同族/ANCIF 0x61fbfe0/ANCIF1
    # 0x61fc0e0 三点互证);内容在官方 UFW 外层 type50 资源(UFW 偏移
    # 0x102120/0x102220,有效长 0x100/0xAC8;docs/usb-recovery.md 实测布局
    # 标该区「保留校准」=真机可能每机不同),外层/内层 CRC+魔数 0x9881/
    # 0x98C1+头内名 ANCGAIN01/ANCCOEF01 与消费者 parse 期望逐项互证——
    # codex 定调(gpt-6-astra,逐条核实+复跑互证+6 处勘误全采纳:换算链
    # 走 0x6010de6→0x6010d54→0x6010c20 不经 island -32;bge.ri 无符号仅
    # 0/0xFFFFFFFF 两哨兵同判;第四调用点经 0x607008c 判空;11 调用点
    # 形状各异不得概称全良性;0x6083a40 是路径串址非内容址)选修正 (c)
    # 官方内容具名 fixture(擦除对照 (a) 4 字节窗留作对照场景未实施)。
    # 放行后两文件 parse 全部真实走通(watch 钉死:mode1 首字 0xfc/魔数
    # 0x9881、mode0 首字 0xac4/魔数 0x98C1,0x606c5e6 parse 块两入、
    # 0x606c5c2 free 路径零执行=名字 strcmp+CRC 全过,调用者拿到 payload
    # 指针 0x61fbff4/0x61fc0f4),链再 **+126,518 步**(ANCGAIN/ANCCOEF
    # 系数真实进入 ANC 配置:音频域系数装载段+OS 等待族续)停在
    # **28,890,702 @ 0x6011976** JL_RAND->R64L 熵源种子首读(路 RAND
    # 三选一=具名 fixture 放行,见 jl_rand_seed 开关注释)。
    # 路 RAND/sim-rand:jl_rand_seed 具名 fixture(readonly 单字 0x12345678,
    # synthetic-not-measured)放行后,MAC 生成两处调用点(0x6011c64/0x6011c92,
    # rand_bytes 各读一次种子)真实走通(6 字节 MAC 31 54 23 93 fe eb 非退化;
    # 首写后重读仍=擦除 FF 哨兵→第二次再生成+再写),链再 **+324,611 步**
    # (ANC/EQ 音频域配置续)停在 **29,215,313 @ 0x6072b92**:
    # `st.w [r0+0]=r5` 写 **0xfe1500=JL_EQ->CON0**(hs_base+map_adr(0x15,0),
    # br28.h __RW,值 0=EQ 关闭配置)——初分类=音频域外设配置写族
    # (EQ 硬件块首写,0xfe1500=0,函数 [0x6072b24,0x6072bb0) 浮点系数段)。
    # 路 JLEQ 批量放行(本路,一批七件+两处 harness 修正):①jl_eq_init_
    # writes——EQ 初始化两字 write-only 记录(CON0=0+FLT_ADR=分配缓冲
    # 0x18cb80,codex 三选一选 (c) 两字容器非八字族白名单);②eq_regions
    # APP_UPPER_STATIC_BUFFERS 窗 [0x1a0600,0x1a4000)(29,215,328 @ 0x4244
    # memset 停点);③core_tick_freerun_edge_period(PRD=0xffffffff freerun
    # 边,29,229,183 @ 0x6065834);④fd31 RES 表 +0x1000d:350/+0x1000e:550
    # (TWVD/PVDD 具名通道值,29,258,205 @ 0x6011704);⑤p33 canary
    # rmw.and.nmodimm 形式(29,486,212 @ 0x12c2da);⑥gpcnt_con_config
    # (29,544,781 @ 0x60688b6);⑦p2m_mailbox PND/CLR 双声明裁定修正——
    # conditional 场景 reset_history 与 p2m_mailbox 同开时 reset 分派在前
    # 吃掉武装态轮询(0x12cca2 喂狗死自旋 20M 步,advance7 假「零停点」的
    # 根因),武装事务窗口内 mailbox 先行(事务外快照语义不变);⑧exact_
    # parallel_byte_stores 接受 insert.rrii A 半(0x6011f20 ∥ st.b 配置键
    # 表压缩循环,29,562,445 停点形状,uextra.rrii 同族);⑨gpcnt_con_
    # completion(49,996,010 @ 0x6069546 稳态测量任务 bit7 轮询,读侧
    # OR 0x80,lrct_trim_done 同模式)。放行后 **park 稳态建立**(0x12cca2
    # 首喂狗完成→S16/S17/S18/S19 段:11 次喂狗事务全闭合、事件钟
    # 14,953,935 虚拟步、CPU0 1791 次 Timer0+57 次 core-tick parked 唤醒
    # (0x12c5ca)、CPU1 16 次 parked 软中断唤醒、TIMER2 锚定轮询 3 次到期
    # 唤醒)——两核 idle+事件循环形态=park 判定证据;随后 S17 稳态测量
    # 任务 0x6069538(task 栈 0x418xxx)首次 GPCNT 转换:bit7 完成位投影
    # 放行后曾停 29,601,125 @ 0x606955c 读 0xfd1104=JL_GPCNT->NUM——路
    # GPCNUM 三选一 (a) 具名 fixture(assumed 标称 32768 Hz 恒等计数
    # 15000,codex 参谋采纳+降档,推导链见 GPCNT_NUM_PROBE 常量注释)放行
    # 后:测量任务转换持续被服务满 **20 次**(20 槽窗口填满,fill==20 触发
    # epilogue 的 h[0x12A3DC] 消费+0x6068880 重启链),park 稳态继续推进
    # 至看门狗 CON 0x2e(time_code=14,周期 16384 名义单位)到期——到期
    # 交付(硬件复位/异常)不发明,停在 **32,737,356 @ 0x12c5ca**
    # (parked 轮询头);喂狗邮箱此前 11 次事务全闭合,第 12 次未在到期前
    # 到来,喂狗节拍/到期交付归下一路。
    conditional_frontier=frontier('p11_heap_experimental')
    require(r['stop_core']==0 and r['stop_pc']==conditional_frontier['stop_pc'] and
            r['stop']==conditional_frontier['stop'],
            'the heap-experimental scenario must stop at 0x606e148 on the '
            'not-modeled JL_AUDIO DAC DMA register read 0xfe0510 — reached '
            'after the route-STOREG3 '
            'standalone postincrement word-store release '
            '(exact_postincrement_word_stores) executes the st.w.pin.l '
            'full-word store of the byte-unpack loop in 0x60649b0 directly '
            '(the route-WDFEED frontier 41,828,530 upstream bookkeeping read '
            'of the never-initialized physical-heap word 0x19f7a8 is crossed: '
            'a full-word store overwrites all four bytes, the old value '
            'cannot affect the result, the merge read is a model-internal '
            'bookkeeping read, not a guest load — the 183,486 st.d.sp.l and '
            '236,415 halfword-pair precedents of the same family), the '
            'unpacked S18 event buffer fills and the consumption chain walks '
            'on through the OS scheduler return (0x6066016) into the LED '
            'driver helper 0x6019448; the route-FD610C single-word JL_PLED '
            'CON3 container serves the 0x6019458 rmw.and.nmodimm '
            '[0xfd610c]&=0xFFFFFFDF RMW read as the explicit synthetic '
            'initial 0 (derived-not-measured, not a reset value), the helper '
            'returns, and +503 pure-CPU0 steps walk into the CON0 save-'
            'restore at 0x60194b4; the route-FD610C-con0 JL_PLED CON0 named '
            'whole-word initial fixture (conditional main arm 0x00000000, '
            'codex three-way (a), derived-not-measured — NOT silicon reset '
            'state) serves the 0x60194b4 r6=[0xfd6100] read as 0, the '
            '0x60194b6 clear-bit0 RMW writes back 0 (word_writes 1), the '
            '0x60194ca if(r6&1) restore is skipped on the false branch, the '
            'save-restore function returns, and +576 pure-CPU0 steps walk '
            'into the 0x6019502 helper whose first rmw.and.nmodimm '
            '[0xf2a610]&=0xFFFFFF1F RMW internal read (clear bits5-7; raw '
            'm=0xE0) is served by the route-P2MCLK P11_SYSTEM P2M_CLK_CON0 '
            'single-word config-RMW container (the helper is exactly SDK '
            'macro LED_CLK_SEL(3), p11_app.h L117; the audited static face '
            'is this one helper — two immediate RMWs whose read values feed '
            'only write-back assembly, zero branches/polls/table lookups, '
            'GET_LED_CLK_SEL referenced nowhere; codex three-way (a), CON3 '
            'shape: synthetic initial 0, RMW-internal readback = last write, '
            'explicit loads always refused, observed forms only — derived-'
            'not-measured): first leg 0&0xFFFFFF1F=0 writes back 0, second '
            'leg 0|0x60=0x60 writes back 0x60 (bits[7:5]=0b011), the RAM '
            'flag b[0x12603C]=1 is set, the helper returns to its one-shot '
            'LED-init caller (0x601a30c arm) which marks b[ctx+48]|=1 and walks '
            'the per-channel reconfig sequence into the 0x601a41c pend (IPMASK '
            '0->6, the OS critical-section mechanism, CLI bit9 untouched) that '
            'calls the LED reset helper 0x60195e2 at 0x601a422 INSIDE the pend '
            'window; its CON0 clears (0x60195ec &=~1, '
            '0x60195f0 &=~2) and the 0x60195f6 whole-word zero store are '
            'served by the route-FD610C-con0 container, then the 0x60195f8 '
            'st.w [0xfd6104]=0 (JL_PLED CON1) stops honestly on the '
            'unmapped-or-readonly word inside the still-open pend window — '
            'a JL_PLED word outside both released containers; the route-'
            'BATCHA batch release then serves that whole-word zero store '
            '(the same STOREG3 full-word family), the LED reset helper '
            'completes inside the pend window, the IPMASK falls back to 0 '
            'and the chain walks through the S19 ring into the resource-VFS '
            'open; the route-TONMAP release maps the official TON/TON1 '
            'containers read-only (three-way SHA agreement, container CRCs '
            'verify at mount) carrying the VFS open past the directory walk '
            'into the task installations (jlstream 0x6086980 among them) and '
            'the event-loop prefix, stopping at 41,909,506 @ 0x60869ba on '
            'the uninitialized read of the task message slot '
            '[0x18ca64,0x18ca68); and the route-EVIMPL release (this '
            'scenario, the codex five-element guard) passes that single 4B '
            'slot as an unknown-word built from the actual known bytes — '
            'the first jlstream message (cmd 0) dispatches through the '
            'event loop and the chain walks into the audio DAC DMA run '
            'domain, stopping at 41,965,883 @ 0x606e148 on the DAC DMA HRP '
            'pointer read 0xfe0510 — the decoder/audio domain, scout top3 '
            'rank 3; and the route-DMAIMPL release (this scenario, the '
            'codex (a) narrowed two-word readback) answers the four '
            'set_sample_rate idle-guard loads (0x606e148/14a, called four '
            'times over the segment) with the firmware\'s own recorded '
            'pointer-reset stores (zero synthesized bytes) — the volatile '
            'double read compares 0==0, takes the equal arm straight into '
            'the POST segment (sample-rate record store, the DAC_CON '
            '[3:0] RMW pair served by the route-AK container, '
            'audio_dac_read_reset_all pure RAM, empty callback slot), the '
            'chain walks on through the audio pipeline reconfiguration '
            '(+71,480 steps, per-core1 frozen at 11496) into the DSP '
            'coprocessor family and stops at 42,037,363 @ 0x606a900 on '
            'copex.ppi — an upstream unmodeled DSP instruction, the '
            'scout branch-1 "decoder/DSP resource domain" wall, NOT one '
            'of the three data-phase walls (LEN read/SWN 0xfe0518/PNS '
            '0xfe050c — the space function never ran, the guard audit '
            'shows zero SWN/PNS touches); and the route-COPEXIMPL release '
            '(this scenario, the codex (a) narrowed mode-22 exponential '
            'approximation) executes the exp leaf 0x606a8fc three times '
            '(two at the old stop site 0x606a900 with x=0xc05d0c55 ≈ '
            '(-30/20)·ln10 → 0x3d0186e2 ≈ 10^(-30/20), one at the second '
            'mode-22 site 0x606600c with x=0 → e^0=1.0 — the eq db2mag '
            'coefficient chain plus one clamp-wrapped wrapper call), the '
            'chain walks on through the eq open-config coefficient '
            'design (+21,707 steps, per-core1 still frozen at 11496, '
            'the three destination upper words discarded by the firmware '
            'before any read) and stops at 42,059,070 @ 0x606fbe4 on the '
            'unmapped read of 0xfe2604 — JL_SRC_SYNC CON1 (SDK br28.h '
            'src_sync block 0x2600-0x26ff), the rmw.or.modimm |= 0x400000 '
            'bit22 configuration RMW whose internal read precedes any '
            'write-back — an ordinary unmapped SFR boundary, NOT the '
            'scout\'s predicted next copex (the eq-design inline sites '
            '0x6072bb8 mode 16 / 0x6072d1a mode 24 sit further down the '
            'chain and stay unreached; codex 20261118: accept the honest '
            'stop, mode 24 was a prediction, not an acceptance target); and '
            'the route-SRCSYNC release (this scenario, the codex '
            'next-stop-classification container: CON0/CON1 synthetic-initial '
            'read-back plus the COEF write-only port, exactly the three '
            'audited words of the reconfiguration driver 0x606fa7e) serves '
            'the whole bit22 reconfiguration transaction — CON1 |=0x400000 '
            'opening, the hw_res RAM-buffer memset, CON0 bit4 opening the '
            'coefficient-load gate once, the 55-round COEF zero-store loop, '
            'CON0 bit4 closing, the flash coefficient-table copy, CON1 '
            '&=0xFFBFFFFF closing — and the chain walks on (+2,287 steps, '
            'per-core1 still frozen at 11496, container ledger 59 writes '
            'exactly as audited) into the audio sync object creator '
            '0x60befae and stops at 42,061,357 @ 0x60befe6 on the '
            'unmapped write of 0xfe2500 — JL_SYNC CON0 (SDK br28.h '
            '0x2500-0x25ff block), the li-constant full-word zero store '
            'inside an IPMASK-6 critical section after the creator\'s '
            'zalloc/field-init/probe call chain — the JL_SYNC write face, '
            'a fresh SFR boundary for the next route); and the route-SYNCIMPL '
            'release (this scenario, the codex narrowed-(a) four-word '
            'container: no-synthetic-initial CON read-back after the first '
            'write, three __WO write-only words refusing every read, and a '
            'consumer PC/core guard refusing the branch-consuming loads '
            'before consumption) serves the creator\'s whole-word zero store '
            'and the chain walks on (+2 steps, per-core1 still frozen at '
            '11496, container ledger exactly one CON write and zero reads — '
            'the scout\'s predicted ledger) to the BT clock timer '
            'configuration and stops at 42,061,359 @ 0x60befee on the '
            'unmapped write of 0xf00130 = 0x2F4 — the 0xF001xx BT-clock-'
            'timer domain (write ports 0xF00130/0xF00140, read ports '
            '0xF00144/0xF00148; the scout\'s wall-order prediction of +2 '
            'steps hit exactly), a fresh SFR boundary for the next route); '
            'and the route-TIMERIMPL release (this scenario, the codex '
            'decision-B two-address write-record container: no synthetic '
            'initial word, every read refused before and after writes, the '
            'RMW internal read refused at the read stage, and a per-address '
            'writer PC/core guard accepting only 0x60befee/0x60beff6 on '
            'core 0) serves both creator stores (ledger exactly two writes '
            '0xF00130=0x2F4 and 0xF00140=0x6DA, zero reads, zero guard '
            'refusals — the scout\'s predicted ledger) and the chain walks '
            'on (+119 steps, per-core1 still frozen at 11496) THROUGH '
            'request_irq(88,prio=2) — the ICU 0xffe12c RMW read is answered '
            'from the known initial-fixture word ([0]*32, probe_clock_'
            'startup.py:41): runtime read value 0x00003030, write-back '
            '0x00003035 (the irq88 nibble 0->5=prio2+enable; the word '
            'already carried irq89=3 from the SRC_SYNC registration earlier '
            'in the same creator window and irq91=3 from a pre-base '
            'registration) — the scout\'s statically-unsolvable candidate '
            'one RESOLVED at runtime as passing — and THROUGH the JL_SYNC '
            'fn_config transaction (the audited write-back read PC '
            '0x60bef62 served exactly once: BT_OFFSET=0, CON1=0x00C80000 '
            '(200<<16, the SYNCIMPL scout\'s predicted value), MIN_SLOT=80, '
            'the CON RMW writing back 0x06200050) to the fn_config '
            'conditional arm b[obj+6]==0 -> [0xF00000] |= 0x400, which '
            'stopped at 42,061,478 @ 0x60befa8 on the unmapped read of '
            '0xf00000 through the TIMERIMPL era (the modem-window CON head, '
            'scout candidate two — the arm IS taken at runtime) and became '
            'the route-FMIMPL five-tuple RMW release face (the chain now '
            'walks on past it to 42,172,441 — see the route-FMIMPL release '
            'record for the +110,963-step segment, the one-shot RMW ledger '
            'and the task-assembly wall order); and the route-SEGIMPL '
            'release (this scenario, exact_preincrement_subword_stores: '
            'the standalone register-preincrement st.h.prer/st.b.prer '
            'direct-write pair, hw 13/13 each with the base==data alias '
            'sample) serves the ROM byte-to-halfword expansion loop\'s '
            'preincrement stores — the 42,172,441 stop instruction itself '
            '(st.h.prer h[++r3=r6]=r7 writing the low half of the '
            'transported-unknown word 0x19e314, clearing exactly its two '
            'byte origins) and its byte-channel sibling 0x5ec6 — and +13 '
            'steps landed at 42,172,454 @ 0x5ed0 on the PARALLEL pair '
            'uextra.rrii.p||st.h whose halfword B-half merge read hit the '
            'retained unknown byte 0x19e316 (a raw uninitialized_memory '
            'stop: the pair records under the A-half uextra id, the '
            'bookkeeping-read rename does not apply), which the route-'
            'SEGIMPL2 release then crossed by joining the extract-prefix x '
            'st.h shape into the exact_parallel_halfword_stores container '
            '(+817 steps through the full five-iteration loop exit and the '
            'ROM record building to the 0x6198 mul||st.w.sp merge-read '
            'stop — see the route-SEGIMPL2 release record), which the '
            'route-SEGIMPL3 release in turn crossed by putting the '
            'mul-scalar-prefix x store-second-half shape on its own '
            'exact_parallel_mul_prefixes sub-switch (+479 steps to the '
            '0x63c2 rep-body st.h.pin.l merge-read stop — see the '
            'route-SEGIMPL3 release record)')
    require(r['completed_steps']==conditional_frontier['completed_steps'],
            'the heap experimental config changed its step count')
    # 路 EVIMPL(2026-11-18,docs/frontier-scout-evqueue.md 停点 41,909,506;
    # codex 裁决 (a)-1 五要素守卫):jlstream 任务栈单点 4B 槽 unknown-word
    # 放行。守卫链=①任务实例绑定:任务安装点 0x6065f9e(r0==0x6086980、
    # r4=TCB)观察到的当前实例(任务体入口 0x6086980 本身从不执行——OS
    # 预装初始 GPR 帧后首派直接进取事件循环 0x6064c8c,入口只是反汇编
    # 锚点,安装点才是运行时可观察事实);②PC+编码钉死:0x60869ba 且字节
    # d0 ec a8 60(ld.w.l r6=[r10+8],raw n=2 换算 +8);③r10==sp+24 且 sp
    # 落在 TCB 槢述的 0xC00 字节栈窗([0x402800,0x403400));④MMU 平移恰为
    # [0x18ca64,0x18ca68) 且 region 可写 RAM;⑤值=按实际已知字节构造的
    # UnknownWord(物理字节带源,不写零,槽保持未初始化)。每次装载全链
    # 重验,拒绝即回落普通严格停点;放行不持久、邻槽/其他 PC/其他实例/
    # 改映射/ROM/MMIO/取指一律无源权限;消费负例(cmd∈[4,12] 最迟
    # 0x60869c6 停)由定向单测钉死。段内运行时证据:首条消息 cmd=0、
    # buf_words ['00001000','00000000','????????','????????'] 把探路遗留
    # 开放项 n 的运行时闭包交出(buf[1] 已初始化 0 ⇒ n<2;buf[2] 未初始
    # 化 ⇒ n≥2 不成立 ⇒ n=1);unknown 在 0x6086b0c 整字覆写前活过一次
    # 异步 IRQ 落窗(window_async,foreign_steps 1),覆写后 r6=0x1264b8;
    # 六个候选 kick post 站点中只有 open_kick_api_0x6074ffe 执行(生产者
    # 甄别收窄,痕迹项保持 open 不设障)。
    evrel=r['peripherals']['jlstream_kick_param2_unknown_release']
    require(evrel['enabled'] and
            evrel['loads']=={'authorized':1,'unknown_word':1,'value_known':0} and
            evrel['cmd_histogram']=={'0x0':1} and
            evrel['task_installs']==1 and
            evrel['producer_executions']['open_kick_api_0x6074ffe']==1 and
            sum(evrel['producer_executions'].values())==1 and
            evrel['first_load']['cmd_register']=='0x0' and
            evrel['first_load']['physical']=='0x18ca64' and
            evrel['first_load']['region']=='physical_heap' and
            evrel['first_load']['tcb']=='0x41a390' and
            evrel['first_load']['stack_window']=='[0x402800,0x403400)' and
            evrel['first_load']['buf_words']==['00001000','00000000',
                                               '????????','????????'] and
            evrel['first_load']['value']=='unknown' and
            evrel['first_load']['r6_after_overwrite']=='0x1264b8' and
            evrel['first_load']['window_async'] is True and
            evrel['first_load']['window_foreign_steps']==1 and
            [t['pc'] for t in evrel['first_load']['trace_tail']]==
            ['0x60869be','0x60869c2','0x6086b06','0x6086b0c','0x6086b0e'] and
            [t['r6'] for t in evrel['first_load']['trace_tail']]==
            ['unknown']*4+['0x1264b8'] and
            evrel['guard']['load_pc']=='0x60869ba' and
            evrel['guard']['encoding']=='d0eca860' and
            evrel['guard']['slot_physical_window']=='[0x18ca64,0x18ca68)' and
            evrel['guard']['install_pc']=='0x6065f9e' and
            evrel['guard']['task_entry']=='0x6086980' and
            evrel['guard']['stack_bounds_read_per_load'] and
            evrel['guard']['revalidated_per_load'] and
            evrel['guard']['not_a_permanent_address_exemption'] and
            'zero executions' in evrel['guard']['task_entry_never_executes'] and
            'NOT measured on the device' in evrel['equivalence_wording'] and
            'producer_identification_open' in evrel and
            r['instruction_adapters'].get('jlstream_kick_param2_load')==1 and
            'never zero-filled' in
            r['peripherals']['assumptions']['jlstream_kick_param2_unknown_release'],
            'the route-EVIMPL single-slot unknown-word release record must pin '
            'the full runtime evidence: exactly ONE authorized load in the '
            'segment (the first jlstream message, cmd 0 — the cmd histogram '
            'has the single 0x0 key, zero consumption-negative arms here), '
            'the slot bytes 00001000/00000000/????/???? closing the scout '
            'open item n at runtime (buf[1] initialized zero, buf[2] '
            'uninitialized ⇒ n=1), the unknown value surviving one async '
            'IRQ landing in its live window (window_async, one foreign step) '
            'until the whole-word overwrite at 0x6086b0c answers r6=0x1264b8, '
            'the TCB 0x41a390 stack window [0x402800,0x403400) with '
            'r10==sp+24 geometry, and the guard contract (per-load '
            'revalidation, stack bounds read from the TCB every time, no '
            'permanent address exemption, the task body entry never '
            'executing — install-site binding is the observable fact); the '
            'producer identification stays an open runtime-traceability item '
            '(only the open_kick_api_0x6074ffe candidate executed; all six '
            'carry the identical {0} payload and it does not gate this '
            'release)')
    # 路 DMAIMPL(2026-11-18,docs/frontier-scout-dacdma.md 停点 41,965,883;
    # codex 裁 (a) 收窄版):HRP/SWP 两字读回=最后写值放行。守卫链=①读点
    # 限定四点 0x606e148/14a/150/152(ld.w {d,b:5,n:4/5} 形状+字节 50 64/
    # 51 65 钉死,r5==0xfe0500 有效地址恰为 0xfe0510/14);②零写拒绝(不补
    # 零、不从另一指针复制);③「初始化阶段」由事件历史维护(判定锚 v3=
    # HRP 复位写步号:DAC_CON 初始化轨迹 0x100→…→0x835e110 全在锚前/同窗
    # =基线,锚后仅尾段站点 0x6071eb8 免检,再后他站点低 4 位采样率索引面
    # 外位变化或 flush 臂位 0x20/0x40 即失效;SWN/PNS 触达、另一核写、未
    # 预期重写——同值也审查——写溯源缺失均失效);④拒绝回落普通严格路径
    # (kind 与开关关世界一致)。每次装载逐条重验,非地址豁免;守卫状态
    # (写来源+阶段+失效位)随 M4 快照恢复。措辞=显式初始化空闲假设
    # (derived-not-measured,非硅等价声明)。
    dmaidle=r['peripherals']['jl_audio_dac_dma_idle_readback']
    require(dmaidle['enabled'] and
            dmaidle['phase']=='initialization' and
            dmaidle['rejections']=={} and
            dmaidle['invalidation_events']==[] and
            dmaidle['reads']['0xfe0510']['reads_served']==4 and
            dmaidle['reads']['0xfe0514']['reads_served']==4 and
            dmaidle['reads']['0xfe0510']['first_read']['pc']=='0x606e148' and
            dmaidle['reads']['0xfe0514']['first_read']['pc']=='0x606e14a' and
            dmaidle['reads']['0xfe0510']['first_read']['core']==0 and
            dmaidle['reads']['0xfe0510']['last_written_value']==
            {'pc':'0x6071ea8','step':3275551,'core':0,'value':'0x0'} and
            dmaidle['reads']['0xfe0514']['last_written_value']==
            {'pc':'0x6071ea4','step':3275550,'core':0,'value':'0x0'} and
            dmaidle['write_audit']['totals']=={'0xfe0500':24,'0xfe0504':1,
                                               '0xfe0508':1,'0xfe050c':0,
                                               '0xfe0510':1,'0xfe0514':1,
                                               '0xfe0518':0} and
            dmaidle['guard']['revalidated_per_load'] and
            dmaidle['guard']['not_a_permanent_address_exemption'] and
            dmaidle['guard']['phase_by_event_history_not_per_read_pointer_check'] and
            dmaidle['guard']['same_value_rewrites_audited'] and
            'EXPLICIT INITIALIZATION-IDLE ASSUMPTION' in dmaidle['assumption'] and
            'NOT a silicon-equivalence claim' in dmaidle['assumption'] and
            'zero synthesized bytes' in dmaidle['readback_semantics'] and
            r['instruction_adapters'].get('jl_audio_dac_dma_idle_readback_load')==8 and
            'EXPLICIT INITIALIZATION-IDLE ASSUMPTION' in
            r['peripherals']['assumptions']['jl_audio_dac_dma_idle_readback'],
            'the route-DMAIMPL two-word readback record must pin the runtime '
            'evidence: FOUR served loads per word (set_sample_rate called four '
            'times over the segment, each first-pair compare 0==0 taking the '
            'equal arm — the second volatile pair 0x606e150/152 never executed), '
            'the write provenance answering them is the firmware\'s own '
            'pointer-reset stores at 0x6071ea8/0x6071ea4 (steps 3,204,978/977, '
            'core 0, value 0x0 — zero synthesized bytes), ZERO rejections and '
            'ZERO invalidation events over the whole segment (the event-history '
            'guard never refused: the DAC_CON anchor held — 24 audited writes '
            'with the trajectory entirely inside the initialization window plus '
            'the POST [3:0] RMW pairs, and SWN/PNS were never touched — the '
            'scout data-phase walls stay uncrossed), and the guard contract '
            '(per-load revalidation, event-history phase, no permanent address '
            'exemption, same-value rewrites audited) with the explicit '
            'initialization-idle assumption wording — derived-not-measured, '
            'not a silicon-equivalence claim')
    # 路 COPEXIMPL:mode-22 指数近似模型释放记录断言——三次执行/两站点/
    # 两输入形状/逐次样本账本(codex 20261118 措辞定稿:证据=已验证实现
    # 契约与当前前缀推进的条件数学近似,不证明 BR28 数值等价/其余 mode-22
    # 站点覆盖/完整 EQ 系数设计)。±1 ulp 敏感性=同停点同步数+已报告计数
    # 不变(不升级为「系数绝不参与分支」)。
    copexrel=r['peripherals']['copex_exp22_model']
    require(copexrel['executions']==3 and
            copexrel['modes']=={'22':3} and
            [(s['pc'],s['x_bits'],s['result_bits']) for s in copexrel['samples']]==
            [('0x606a900','0xc05d0c55','0x3d0186e2'),
             ('0x606600c','0x0','0x3f800000'),
             ('0x606a900','0xc05d0c55','0x3d0186e2')] and
            all(s['core']==0 for s in copexrel['samples']) and
            'CONDITIONAL MATHEMATICAL APPROXIMATION' in
            r['peripherals']['assumptions']['copex_exp22_model'] and
            'NOT a silicon replica' in
            r['peripherals']['assumptions']['copex_exp22_model'] and
            '1 ulp BELOW correct rounding' in
            r['peripherals']['assumptions']['copex_exp22_model'] and
            r['instruction_adapters'].get('copex_exp22_model_execute')==3,
            'the route-COPEXIMPL mode-22 exponential release record must pin the '
            'runtime evidence: exactly THREE executions over the segment at TWO '
            'sites (the old stop 0x606a900 twice with the db2mag shape '
            'x=0xc05d0c55 ≈ (-30/20)·ln10 → 0x3d0186e2 ≈ 10^(-30/20), and the '
            'clamp-wrapped wrapper site 0x606600c once with x=0 → e^0=1.0 '
            '0x3f800000), the per-execution sample ledger carrying every '
            'input/output, the three destination upper words discarded by the '
            'firmware before any read (zero copex_dst_upper_word_consumed '
            'stops, zero transport copies), the assumption wording pinning the '
            'approximation class (conditional mathematical approximation, '
            'derived-not-measured, AC791N e^1.0 1-ulp-below-correct-rounding '
            'noted and NOT replicated, fail-closed contract with the dedicated '
            'stop kinds, the other seven modes NOT modelled by this switch), '
            'and the evidence boundary: this supports the implementation '
            'contract and the current-prefix advance only — it does NOT prove '
            'BR28 numeric equivalence, coverage of the other nine mode-22 '
            'sites, the full EQ coefficient design, or any audio effect')
    # 路 SRCSYNC:配置事务三字容器释放记录断言——容器账本逐字逐次钉死
    # (codex 措辞:证据=已验证实现契约与当前前缀推进,不证明 SRC_SYNC
    # 硬件/采样率转换/DMA/音频处理已运行)。goto 偏移单位勘误后的事务
    # 形状:CON0 |=0x10 开门恰 1 次(循环外)→55 轮循环体仅 COEF 写零→
    # CON0 &=~0x10 关门;CON1 |=0x400000→段尾 &=0xFFBFFFFF 归 0。SRCSYNC5
    # 打开 IRQ89 交付后,SRC 完成唤醒 jlstream_N 重入函数 B 的启动/ACK
    # 循环(handler CON0|=0x40 W1C 清 pending + 启动 CON0|=0x8),音频激活
    # 稳态下该循环无限跑,CON0/CON1 账本膨胀(36→8427 / 24→704),COEF
    # 55+55 与合成初值读回 2 不变,读回 48→12463。
    srcsyncrel=r['peripherals']['jl_src_sync_config_registers']
    require(srcsyncrel['word_writes']==9241 and
            srcsyncrel['stores']['0xfe2604']=={'values':['0x41','0x41','0x41','0x41',
                                                       '0x41','0x41','0x41','0x41',
                                                       '0x41','0x41','0x41','0x41',
                                                       '0x41','0x41','0x41','0x41'],
                                               'writes':704,'cores':[0,0,0,0,0,0,0,0,
                                                                    0,0,0,0,0,0,0,0]} and
            srcsyncrel['stores']['0xfe2600']=={'values':['0x82006049','0x82006049',
                                                       '0x82006049','0x82006049',
                                                       '0x82006049','0x82006049',
                                                       '0x82006049','0x82006049',
                                                       '0x82006049','0x82006049',
                                                       '0x82006049','0x82006049',
                                                       '0x82006049','0x82006049',
                                                       '0x82006049','0x82006049'],
                                               'writes':8427,'cores':[0,0,0,0,0,0,0,0,
                                                                    0,0,0,0,0,0,0,0]} and
            srcsyncrel['stores']['0xfe263c']['writes']==110 and
            all(v=='0x0' for v in srcsyncrel['stores']['0xfe263c']['values']) and
            srcsyncrel['readback_projection']['reads']==12463 and
            srcsyncrel['readback_projection']['synthesized_initial_reads']==2 and
            srcsyncrel['readback_projection']['coef_read_attempts']==0 and
            'synthetic-not-measured' in
            srcsyncrel['readback_projection']['readback'] and
            'COEF refuses every read' in
            srcsyncrel['readback_projection']['readback'] and
            'sixteen members' in srcsyncrel['sdk_naming'] and
            'same-address new consumer' in srcsyncrel['whitelisted_words'] and
            '0x60a29d4' in srcsyncrel['whitelisted_words'] and
            'sample-rate conversion' in
            r['peripherals']['assumptions']['jl_src_sync_config_registers'],
            'the route-SRCSYNC config-transaction release record must pin the '
            'runtime evidence in the audio-active steady state (SRC loop): 9241 word '
            'writes over the released prefix — the '
            'original 59-write reconfiguration transaction (function 0x606fa7e: '
            'CON1 |=0x400000 @0x606fbe4 then &=0xFFBFFFFF @0x606fc2c ending '
            'at 0x0, CON0 |=0x10 @0x606fbfa once BEFORE the loop then &=~0x10 '
            '@0x606fc0a ending at 0x0, COEF exactly 55 zero stores @0x606fc02) '
            'PLUS the SRCSYNC2 function-B writes (CON0 whole-word 0x82006001 '
            '@0x6070a90, CON1 whole-word 0x41 @0x6070a94 + paired insert 0x41 '
            '@0x6070ad2) PLUS the SRCSYNC3+SRCSYNC4 helper 0x607099c RMWs '
            '(CON1 |=0x400000 @0x60709ba writes 0x400041, CON0 |=0x10 '
            '@0x60709da writes 0x82006011, CON0 &=~0x10 @0x60709ee, CON0 '
            '|=0x10 @0x6070a0e, CON0 &=~0x10 @0x6070a24, CON1 &=~0x400000 '
            '@0x6070a28) and the COEF 55-word write-back @0x6070a1c (COEF '
            'writes=110=55 init + 55 write-back, the 55 read-back reads at '
            '0x60709e0 served by the separate coef batch container so '
            'coef_read_attempts stays 0) PLUS the SRCSYNC5 SRC-completion IRQ89 '
            'delivery re-entry — the handler 0x12ca24 ACK CON0|=0x40 W1C and '
            'the woken jlstream_N task re-entering the function-B/startup/ACK '
            'cycle (CON0 whole-word 0x82006001 → startup |=0x8 → ACK |=0x40 '
            'alternating 0x82006041/0x82006049, CON1 whole-word 0x41 re-written) '
            'which now runs CONTINUOUSLY in the audio-active steady state instead '
            'of parking — every SRC completion re-fires the IRQ89 delivery, the '
            'handler ACK and the jlstream_N re-entry, so the ledger records CON0 '
            '8427 writes (the 16-slot values window shows all 0x82006049) / CON1 '
            '704 writes (all 0x41) / COEF 110 writes and readback reads 12463 '
            '(was 9 at the single-transaction stop) while synthesized_initial_reads '
            'stays 2 and coef_read_attempts stays 0 — the frontier stops at '
            'instruction_budget 139,947,345 @ 0x6064220 INSIDE the SRC loop; the '
            'synthetic-initial and read-back=current semantics '
            'wording (synthetic-not-measured, NOT silicon reset values), the '
            'data-driven base narrative (sole literal .data 0x60a29d4 in the '
            'hw_res table, single accessor function), the sixteen-member naming '
            'with the 14 __RW + 2 __WO erratum, the same-address-new-consumer '
            're-audit clause, and the assumption boundary — this does NOT '
            'verify any sample-rate conversion, DMA or audio processing')
    # 路 SRCSYNC4:COEF 口「初始化后 55 字批次回读」释放记录断言——独立容器
    # 账本逐字段钉死:55 笔初始化写填满批次→0x60709e0 顺序读回 55 个 0 推进
    # 游标→0x6070a1c 按固件实际 55 值写回(不是通用 FIFO/破坏性弹出/写满丢
    # 最旧;返回值=批次该位置的值非「永远最后写值」,55 个互异标记单测互证)。
    coefrel=r['peripherals']['jl_src_sync_coef_batch_readback']
    require(coefrel['phase']=='consumed' and coefrel['batch_len']==55 and
            coefrel['read_cursor']==55 and coefrel['reads_served']==55 and
            coefrel['init_writes']==55 and coefrel['write_back_writes']==55 and
            coefrel['rejections']=={} and coefrel['invalidations']==[] and
            coefrel['first_read']['pc']=='0x60709e0' and
            coefrel['first_read']['core']==0 and
            coefrel['first_read']['cursor_before']==0 and
            coefrel['first_read']['value']=='0x0' and
            coefrel['last_read']['pc']=='0x60709e0' and
            coefrel['last_read']['cursor_before']==54 and
            coefrel['last_read']['value']=='0x0' and
            r['instruction_adapters'].get('jl_src_sync_coef_batch_read')==55 and
            '初始化后 55 字批次回读' in coefrel['assumption'] and
            'derived-not-measured' in coefrel['assumption'] and
            'jl_src_sync_coef_batch_readback' in r['peripherals']['assumptions'],
            'the route-SRCSYNC4 COEF batch read-back release record must pin the '
            'runtime evidence: the reconfiguration driver 0x606fa7e 55 aligned zero '
            'stores at 0x606fc02 fill the batch, the helper 0x607099c then reads '
            'position i back for i=0..54 at 0x60709e0 (ld.w r0=[r5+60], CPU0, gate '
            'CON0 bit4 + CON1 bit22 open) each returning the value stored at that '
            'position — 55 reads served, cursor 0->55, phase consumed — and the '
            'write-back at 0x6070a1c records the firmware\'s actual 55 values (all '
            'zero in this prefix: [obj+168] is empty, so the memcpy channel-slice '
            'replacement is a no-op) with zero rejections and zero invalidations; '
            'the model is NOT a general FIFO / destructive-pop / write-full-drop-'
            'oldest, and the 55-init-stores-cover-the-same-55-positions premise is '
            'an explicit assumption (初始化后 55 字批次回读，derived-not-measured；'
            '适用至本次 helper，物理容量、读后内容及溢出策略未测)')
    # 路 SYNCIMPL:四字容器释放记录断言。TIMERIMPL 前:仅 CON 一写零读
    # (SYNCIMPL 时代账本);TIMERIMPL 放行两 timer 写后 fn_config 已执行
    # ——账本现为:CON 两写(creator 0 + fn_config RMW 写回 0x06200050)、
    # 已审计写回读点 0x60bef62 服务恰一次、WO 三字各一写且值恰如
    # SYNCIMPL 侦察静态预判(CON1=200<<16=0x00C80000、BT_OFFSET=0、
    # MIN_SLOT=80)——「四字一次建好,跨墙后 fn_config 无需再改模型」
    # 的设计兑现;消费者 PC/核守卫仍零触发(IRQ88/getter 不在前缀)。
    syncrel=r['peripherals']['jl_sync_config_registers']
    require(syncrel['word_writes']==5 and
            syncrel['stores']=={'0xfe2500':{'values':['0x0','0x6200050'],'writes':2,'cores':[0,0]},
                                '0xfe251c':{'values':['0xc80000'],'writes':1,'cores':[0]},
                                '0xfe2524':{'values':['0x0'],'writes':1,'cores':[0]},
                                '0xfe2528':{'values':['0x50'],'writes':1,'cores':[0]}} and
            syncrel['readback_projection']['con']['reads']==1 and
            syncrel['readback_projection']['con']['read_attempts_refused']==0 and
            syncrel['readback_projection']['wo']['read_attempts_refused']==0 and
            'NO synthetic initial word' in
            syncrel['readback_projection']['con']['semantics'] and
            'every read is refused including the internal read an RMW would '
            'need' in syncrel['readback_projection']['wo']['semantics'] and
            syncrel['consumer_guard']['branch_read_pcs']==['0x60bf19c','0x60bf1f6','0x60bf25c'] and
            len(syncrel['consumer_guard']['writeback_read_pcs'])==11 and
            '0xF00130' in syncrel['whitelisted_words'] and
            'NOT part of this container' in syncrel['whitelisted_words'] and
            'thirteen members' in syncrel['sdk_naming'] and
            'REJECTED' in syncrel['model_assumptions'] and
            'fn_config has executed' in syncrel['modeled_vs_touched'] and
            'audio-sync block' in
            r['peripherals']['assumptions']['jl_sync_config_registers'],
            'the route-SYNCIMPL release record must pin the runtime evidence: '
            'five JL_SYNC word accesses through the TIMERIMPL era — the '
            'creator whole-word zero store [0xfe2500]=0 at 0x60befe6 (the '
            'former 42,061,357 stop instruction itself, core 0) plus the '
            'fn_config transaction that executed behind the TIMERIMPL '
            'release: the audited write-back read PC 0x60bef62 served '
            'EXACTLY once (the consumer guard passes its own whitelist), '
            'BT_OFFSET=0, CON1=0x00C80000 (200<<16, the SYNCIMPL scout\'s '
            'statically predicted value hit exactly), MIN_SLOT=80 (0x50) '
            'and the CON read-modify-write writing back 0x06200050 — the '
            'four-words-once design (no model change needed after crossing '
            'the timer walls) fulfilled; zero read refusals (IRQ88 and the '
            'getter domain stay off the executed prefix, the branch read '
            'PCs never fire); the no-synthetic-initial CON policy wording, '
            'the WO refuse-every-read wording, the three branch read PCs '
            '(0x60bf1f6 bit19, 0x60bf25c bit28-uextra vs slot, 0x60bf19c '
            'getter) with the eleven write-back read PCs, the 0xF001xx '
            'NOT-in-this-container clause, the thirteen-member naming and '
            'the zero-initial-REJECTED assumption record — this does NOT '
            'verify any audio synchronization, IRQ88, timer or DMA scenario')
    # 路 TIMERIMPL:两字容器释放记录断言——账本恰如侦察预期账本
    # (docs/frontier-scout-timer.md §5):恰两写(0xF00130←0x2F4@
    # 0x60befee、0xF00140←0x6DA@0x60beff6,均 core0)零读零拒;写者
    # PC/核守卫零触发;+119 步段跨 request_irq(ICU 0xffe12c RMW 读值
    # 0x3030→写回 0x3035,侦察「静态无解候选一」运行时解开=放行)与
    # fn_config JL_SYNC 事务,停在 fn_config 条件臂 [0xF00000]|=0x400
    # 的 RMW 内部读(侦察候选二命中,b[obj+6]==0 臂运行时取到)。
    btclkrel=r['peripherals']['jl_btclk_timer_registers']
    require(btclkrel['word_writes']==2 and
            btclkrel['stores']=={'0xf00130':{'values':['0x2f4'],'writes':1,
                                             'pcs':['0x60befee'],'cores':[0]},
                                 '0xf00140':{'values':['0x6da'],'writes':1,
                                             'pcs':['0x60beff6'],'cores':[0]}} and
            btclkrel['read_refusals']['before_first_write']==0 and
            btclkrel['read_refusals']['after_write']==0 and
            btclkrel['writer_guard']['refusals']==0 and
            btclkrel['writer_guard']['per_address_writer_pcs']=={'0xf00130':'0x60befee',
                                                                 '0xf00140':'0x60beff6'} and
            'not a silicon register-attribute claim' in
            btclkrel['read_refusals']['semantics'] and
            'jl_btclk_write_pc_not_audited' in btclkrel['writer_guard']['semantics'] and
            '0xf00144/48' in btclkrel['whitelisted_words'] and
            'never a contiguous range' in btclkrel['whitelisted_words'] and
            'ZERO already-modeled domains' in btclkrel['side_effect_boundary'] and
            'pure writes (no RMW' in btclkrel['side_effect_boundary'] and
            'minimal abstraction' in btclkrel['model_assumptions'] and
            'btctrler modem window' in
            r['peripherals']['assumptions']['jl_btclk_timer_registers'],
            'the route-TIMERIMPL release record must pin the runtime evidence: '
            'exactly TWO modem-window timer word accesses in the released '
            'segment — the creator stores [0xf00130]=0x2F4 at 0x60befee (the '
            'former 42,061,359 stop instruction itself) and [0xf00140]=1754 '
            'at 0x60beff6, both core 0, both from their single all-app '
            'writer site (zero writer-guard refusals), zero reads served and '
            'zero read refusals (the refusal policy never meets a consumer — '
            'the 0xf00144/48 read pair and the 400-byte bulk window stay off '
            'the prefix); the released segment then crosses request_irq(88) '
            '— the ICU 0xffe12c RMW read answered 0x00003030 with write-back '
            '0x00003035 (the scout\'s statically-unsolvable candidate one '
            'resolved at runtime as PASSING, recorded for the next scout) — '
            'and the fn_config JL_SYNC transaction to the b[obj+6]==0 '
            'conditional arm [0xF00000]|=0x400, whose internal read stopped '
            'at 42,061,478 @ 0x60befa8 through the TIMERIMPL era (scout '
            'candidate two, the arm IS taken) and became the route-FMIMPL '
            'five-tuple RMW release face — the side-effect boundary wording (pure '
            'stores, zero overlap with already-modeled domains, every '
            'observation face still refusing), the WO-is-policy-not-silicon '
            'wording, the per-address writer PC map and the '
            'minimal-abstraction assumption record — this does NOT verify '
            'any BT clock timer, IRQ88 or audio synchronization scenario')
    # 路 FMIMPL:0xF00000 CON 头恰一字五元组 RMW 容器释放记录断言——账本
    # 恰如侦察 §5 预期账本的「至少一次」下界:恰一笔 RMW(synthetic 0→
    # 0x400,core0,PC 0x60befa8)零显式读零独立写零守卫拒绝;fn_config
    # 第二调用者(节点 start 0x607e46c)未在本段重入(jl_sync 账本冻结
    # 5 写 1 读=fn_config 全前缀恰执行一次——表派发段运行时先走了任务
    # 装配而非节点 start);放行段 +110,963 步:fn_config RTS→纯 RAM 段
    # →adapter 续走→框架表派发(静态缺口,运行时先创建新任务 0x6086c8c
    # 〔TCB 0x41b158,恰一个新 watched 任务安装〕)→ROM 字节→半字展开环,
    # 停 42,172,441 @ 0x5ea4(st.h.prer h[++r3]=r7 的 upstream 簿记读撞
    # 未初始化物理堆半字 0x19e314——WDFEED 停点同类,WDFEED 后由
    # STOREG3 exact-store 族放行的先例在,本站是半字前增量形状,归下路)。
    # 墙序预判对账(任务第 4 项):侦察 §6 预判候选=copex 0x6072bb8
    # mode16/0x6072d1a mode24 dsp_mode_not_modeled 或第二次 RMW(先后
    # 运行时定)——实测两者都未落本段:表派发段先走任务装配+ROM 字符
    # 处理,新停点在 copex 与第二次 fn_config 之前(侦察 §7「表派发段
    # 静态不可见」诚实边界内的运行时答案);copex site16/site24 与节点
    # start 第二次 RMW 仍在更下游未到达。
    fmrel=r['peripherals']['jl_fm_con_head_registers']
    require(fmrel['word_writes']==1 and
            fmrel['stores']=={'0xf00000':{'values':['0x400'],'writes':1}} and
            fmrel['transactions']==[{'read_source':'synthetic','old':'0x0',
                                     'new':'0x400','pc':'0x60befa8','core':0}] and
            fmrel['readback_projection']['synthetic_initial_word']=='0x0' and
            fmrel['read_refusals']['refused']==0 and
            fmrel['store_refusals']['refused']==0 and
            fmrel['rmw_authorization']['rmw_pc_refusals']==0 and
            fmrel['rmw_authorization']['rmw_form_refusals']==0 and
            fmrel['rmw_authorization']['authorized_instruction']=={
                'pc':'0x60befa8','instruction_id':'rmw.or.modimm',
                'mask':'0x400','core':0,'address':'0xf00000','width':4} and
            'ONE COMPLETE INSTRUCTION' in
            fmrel['rmw_authorization']['semantics'] and
            'NOT a silicon reset value' in
            fmrel['readback_projection']['semantics'] and
            'memcpy call sites' in fmrel['store_refusals']['semantics'] and
            'never a contiguous range' in fmrel['whitelisted_words'] and
            'couple' in fmrel['side_effect_boundary'] and
            'SECOND caller' in fmrel['modeled_vs_touched'] and
            'modem-window' in
            r['peripherals']['assumptions']['jl_fm_con_head_registers'],
            'the route-FMIMPL release record must pin the runtime evidence: '
            'exactly ONE five-tuple RMW transaction on the modem-window CON '
            'head — the 42,061,478 stop instruction itself (fn_config '
            '0x60befa8 [0xF00000]|=0x400, core 0) served with the ledgered '
            'read source synthetic (first head-word access, explicit initial '
            '0, derived-not-measured, never reported as a silicon reset '
            'value) writing 0x400 once — zero explicit reads, zero '
            'independent stores, zero PC/form-guard refusals (fn_resume '
            '0x60bf14e, the IRQ88 handler and the btctrler family never '
            'touch the head word on this prefix); the scout\'s '
            'at-least-one-at-most-two ledger bounds resolve at runtime to '
            'the LOWER bound: the second fn_config caller (node start '
            '0x607e46c via the framework walker) does NOT re-enter within '
            'the released segment — the jl_sync ledger stays frozen at five '
            'writes/one read, i.e. fn_config executed exactly once in the '
            'whole prefix — the table-dispatch gap went to task assembly '
            'first (exactly ONE new watched task installation, entry '
            '0x6086c8c, TCB 0x41b158) and then into the ROM '
            'byte-to-halfword expansion loop, where the new stop 42,172,441 '
            '@ 0x5ea4 (st.h.prer upstream bookkeeping read of the '
            'uninitialized physical-heap halfword 0x19e314, not a guest '
            'load, IPMASK 0 outside any critical section) lands BEFORE both '
            'scout candidates (the copex mode16/24 sites and the second '
            'RMW stay unreached downstream); the five-tuple wording, the '
            'synthetic-not-reset-value wording, the bus-level bulk-guard '
            'wording and the silicon-coupling gap declaration — this does '
            'NOT verify any modem-window, IRQ88 or BT clock scenario')
    # 路 SEGIMPL:独立前增量(寄存器)半字/字节存储直写(exact_preincrement_
    # subword_stores)释放记录断言——FMIMPL 前沿 42,172,441 @ 0x5ea4 的
    # st.h.prer 簿记读墙由本开关恰两形(st.h.prer/st.b.prer,hw followup5-m
    # 各 13/13 含基址=数据别名样本)放行:放行段恰 +13 步(展开环迭代 1 后
    # 半程 0x5ea8..0x5ecc),gate 恰 4 次(迭代 0 两形在已知字上由本 gate 接
    # 管〔直写与上游合并写 RAM 结果逐字节相同〕+迭代 1 两形——0x5ea4×2/
    # 0x5ec6×2);第一性审计钉死目的缓冲语义:0x19e314-0x19e31b 8 字节=
    # write_abstract 未知值运输写所写(unknown_origins 逐字节指向
    # 0x19f7e8-0x19f7ef)非「从未写过」,数值写钩子对该洞零记录;展开环
    # 对目的零装载、每轮低半+高半成对写=固件语义不消费旧值,直写恰清除
    # 所写字节的未知来源、邻字节来源保留——拒绝合成初始化路径(合成已知
    # 值会掩盖 +13 步后高半字并行对合并读如实撞上的未知字节);新停点
    # 0x5ed0=并行对 uextra.rrii.p∥st.h 高半字 B 半的上游合并读撞保留的
    # 未知字节 0x19e316(kind=uninitialized_memory 非 upstream_store_
    # bookkeeping_read——并行对以 A 半指令 id 记录,不以 st. 开头,改名
    # 路径不适用),归下路并行对半字族;pre 立即数形(hw 各 13 条但 app
    # 16 h+41 b 站点未触达)/hi.prer 形(hw 13 条、全库零站点)/st.{w,d}.
    # prer 整字前增量形(38+4 站点)与 ld.*.prer 装载形不在放行族。
    require(r['instruction_adapters'].get('exact_preincrement_subword_store')==20 and
            r['stop']['kind']=='instruction_budget' and
            r['peripherals']['jl_fm_con_head_registers']['word_writes']==1 and
            r['memory_event_counts']['write']==11605773 and
            'exact_preincrement_subword_stores' in r['assumptions']['diagnostics'],
            'the route-SEGIMPL release record must pin the runtime evidence: exactly TEN '
            'exact_preincrement_subword_store gate firings at the 0x63c2 stop (the st.h.prer/st.b.prer pair '
            'in each of the FIVE expansion-loop iterations — iteration 0 on known words '
            'taken over from the upstream merge-write path with byte-identical RAM '
            'results, iteration 1 whose st.h.prer is the released 42,172,441 stop '
            'instruction itself, iterations 2-4 walked on through the route-SEGIMPL2 '
            'release that crossed the 42,172,454 pair wall) and the count walks on to '
            'TWENTY through the route-SEGIMPL4 release segment (+57,523 steps execute '
            'ten more prer firings downstream of the clearing loop), the 42,172,454 stop at '
            '0x5ed0 where the PARALLEL pair uextra.rrii.p||st.h halfword B-half merge '
            'read honestly hit the RETAINED unknown byte 0x19e316 (the 8-byte window '
            '0x19e314-0x19e31b holds abstract-transported unknown values with origins '
            '0x19f7e8-0x19f7ef — written-but-unknown, never never-written; the direct '
            'write clears the origins of exactly the bytes it covers, which is why a '
            'synthetic initialization was REJECTED — it would have masked this honest '
            'next stop) is now CROSSED by that SEGIMPL2 release and the 0x6198 '
            'mul-prefix stop it left behind is CROSSED by the route-SEGIMPL3 release '
            'and the 0x63c2 rep-body st.h.pin.l stop it left behind is CROSSED by the '
            'route-SEGIMPL4 release (stop kind is now rep_multi_block_body_not_'
            'straight_line of the NEW 0x60736c8 register-count REP whose clamp body '
            'opens with ifs.gt.rr at 0x60736cc), the jl_fm ledger stays frozen at one write (the released '
            'segments are pure CPU0 store code), and the write event total moves '
            '2419420->2419763 at the 0x63c2 stop and walks on to 11605773 at the '
            'instruction_budget stop (the audio-active SRC loop keeps writing far past '
            'the released segments — every later gate write through the new stop, '
            'including the 390 pre-existing st.b.pin.l executions that the from-zero '
            'ledger now records as pulsed direct writes where the upstream merge path '
            'had dropped same-value merged words) — this '
            'does NOT verify any pre immediate-increment form, st.w/st.d prer form or '
            'the expansion buffer\'s unknown values')
    # 路 SEGIMPL2:uextra/insert 前缀×st.h 第二半并入 exact_parallel_halfword_
    # stores 容器(扩展现有容器族,不新开开关)的释放记录断言——SEGIMPL 前沿
    # 42,172,454 @ 0x5ed0 的并行对半字墙放行后 +817 步(旧停点→展开环迭代
    # 1 后半程到迭代 4 走完、环出口 0x5ef6 pop 完成恰 107 步→返回调用域后
    # 710 步):上站墙序预判的「词 0x19e318 同族墙恰一次」未出现——同一
    # 0x5ed0 形状放行覆盖全部迭代(迭代 2 高半 0x19e31a 在未知窗口上直写、
    # 迭代 3-4 已知词直写);返回后进入 ROM 0x6170 函数,序言保存寄存器写
    # 栈槽(0x6170 push.rets_rl 落 0x19e350/0x19e354、0x6178 mov.rr.p∥
    # st.w.sp [sp+8] 落 0x19e4c0、0x618c st.w.sp [sp+12] 落 0x19e4c4——
    # 栈帧写非业务记录,消费者证据未到),停在 0x6198 `mul.modimm.rri.p
    # r0=r0*9 ∥ st.w.sp [sp+4]=r2` 的上游合并读(虚拟栈 sp=0x4042b8,MMU
    # 翻译 0x4042bc→物理堆 0x19e4bc 未初始化;mul 前缀不在 pure-ALU 清单、
    # st.w.sp 是 word 族——归下路 mul 前缀×word 半族评审)。gate 计数:半字
    # 对容器 740→745(迭代 0 已知字迁移+迭代 1-4 直写恰 5 次)、prer 两形
    # 4→10;timer0 cnt 5,808→8,259(恰 +817×3 tick,PRD 11,999 窗内零新
    # 到期/零新交付,到期 15,819/交付 15,802 冻结)、core-tick cnt
    # 1,403,868→1,407,136(恰 +817×4,CPU1 同款 +3,268)、零新喂狗(163/162
    # 冻结)、jl_fm 恰一笔 RMW 冻结、事件钟冻结。
    require(r['instruction_adapters'].get('parallel_halfword_store_old_registers')==19689 and
            r['stop']['kind']=='instruction_budget' and
            r['stop_pc']=='0x6064220' and
            r['peripherals']['timer0']['cnt']==49962 and
            r['peripherals']['core_tick_configuration'][0]['cnt']==65344 and
            'exact_parallel_halfword_stores' in r['assumptions']['diagnostics'],
            'the route-SEGIMPL2 release record must pin the runtime evidence: the '
            'extract-prefix (uextra.rrii/insert.rrii) x st.h second half joined the '
            'exact_parallel_halfword_stores CONTAINER (no new switch — the shape is '
            'isomorphic to the halfword family), releasing the 42,172,454 stop pair '
            'uextra.rrii.p||st.h at ROM 0x5ed0; the expansion loop runs ALL FIVE '
            'iterations to its 0x5ef6 exit (the predicted word-0x19e318 same-family '
            'wall NEVER APPEARS — one shape release covers every iteration: iteration '
            '2 high half 0x19e31a direct-writes over the unknown window and iterations '
            '3-4 direct-write known words — 107 steps to the 0x5ef6 pop completion '
            'plus 710 steps after the return, entering the ROM 0x6170 function whose '
            'prologue saves registers into stack slots (0x19e350/354 via push.rets_rl, '
            '0x19e4c0/4c4 via st.w.sp — stack saves, not business records), and stops '
            'at 0x6198 mul.modimm.rri.p||st.w.sp whose upstream merge '
            'read hits the uninitialized heap frame word 0x19e4bc (mul prefix is not '
            'in the pure-ALU list and st.w.sp is a word-half — the mul-prefix x '
            'word-store pair family belonged to the next route, which released it '
            'on the route-SEGIMPL3 sub-switch and moved the stop on to 0x63c2 '
            'upstream_store_bookkeeping_read on the rep-body st.h.pin.l window '
            '0x19e100, itself CROSSED by the route-SEGIMPL4 sub-switch that moved '
            'the stop on to the 0x60736c8 register-count REP clamp body); the parallel halfword pair adapter walked 740->745 at the '
            '0x6198 stop (iteration 0 known-word takeover from the upstream '
            'merge-write path, byte-identical RAM, plus four direct-write firings) '
            'and walks on to 747 through the SEGIMPL3 release (both hops are mul '
            'migrations: the snapshot-segment 0x6011be8 firing plus the resume-'
            'segment ROM 0x7604 firing; the +479 release segment itself adds ZERO '
            'halfword pairs) and on to 774 through the SEGIMPL4 release segment '
            '(+27 halfword pairs across the +57,523-step walk), the preincrement gate stays at '
            '10 (five iterations x two forms) and walks on to 20 through the '
            'SEGIMPL4 release, TIMER0 cnt walked 5808->8259 = '
            'exactly +817 x 3 STD12M ticks inside the PRD 11999 window with zero '
            'new expiry and walks on to 9696 through the SEGIMPL3 release segment '
            '(exactly +479 x 3 more, still zero new expiry) and on to 21975 '
            'through the SEGIMPL4 release segment (the PRD window rolls over: '
            'THIRTEEN new Timer0 expiries and deliveries fire inside the '
            '+57,523-step walk), the CPU0 core tick '
            'walked 1403868->1407136 = exactly +817 x 4 with 347/347 frozen (CPU1 '
            'the same +3268) and walks on to 1409052 = exactly +479 x 4 more and on '
            'through the SEGIMPL4 release segment to the post-overflow held '
            'snapshot 39124 (ONE new core-tick expiry+acknowledgement inside the '
            'walk; the CNT value is the frozen post-overflow snapshot, not a '
            'running count) with '
            '348/348 acknowledgements (ONE new expiry in the walk), zero new feed '
            '(163/162 frozen) and the jl_fm '
            'ledger frozen at its single write; the frontier then runs the '
            'audio-active SRC loop to instruction_budget 139,947,345 @ 0x6064220 '
            '(parallel halfword adapter 19689, TIMER0 cnt 49962, CPU0 core tick '
            '65344) — this does NOT verify any '
            'st.w-family extract-prefix pair or the heap frame word 0x19e4bc')
    # 路 SEGIMPL3:mul.* 标量乘前缀×存储第二半(exact_parallel_mul_prefixes
    # 独立子开关)的释放记录断言——SEGIMPL2 前沿 42,173,271 @ 0x6198 的
    # mul.modimm.rri.p∥st.w.sp 合并读墙由本开关放行(word 整字直写,旧值
    # 语义无关恰覆盖未初始化帧槽 0x19e4bc,拒绝合成初始化):16 形=hw/
    # fm1-2026-10-24-v3 全部 mul 前缀捕获形(每形∥st.w∥ld.w 各 4 条,B 半
    # 「读 A 之前旧寄存器」64/64 条核对,含 mul 目的==存储源别名方向
    # batch-pair-000)。独立子开关而非并入 pure-ALU 清单:mul 前缀×st.*
    # 在已执行窗口(0..42,173,271)内恰 8 次真实执行/6 站(快照段 0..
    # 41,884,941 恰 6 次=word 5〔0x601d1ee×2/0x601d282×2/0x606df1e×1〕+
    # half 1〔0x6011be8〕;resume 段恰 2 次=word 1〔0x606fb8c〕+half 1
    # 〔ROM 0x7604〕;从零 41.9M attempts watch 重放与自建 base 快照账本双
    # 口径互证,adapter 14,615/568 一致),全部走上游模型目标字已知正常
    # 完成——并入
    # 主 gate 会改既有报告 adapter 计数,halfword_pair/word_store_sp_l/CQ
    # 先例:已执行形状独立 opt-in;crc_bankb 177,491 mul×st.* 动态 watch
    # 零命中(base 场景零变化)。放行段恰 +479 步(0x6198 直写→0x61aa
    # mul∥ld.w 走上游模型过→ROM 0x6170 函数尾部 st.w.idxs 记录字→0x63xx
    # 域两轮 rep 清零循环),停在 0x63c2 `rep.i 4 16 { st.h.pin.l
    # h[r11++=2]=r0 }` 第二轮第 2 个半字的合并读(第二轮 r11=0x4043fe 起,
    # 第 1 个半字成功写并后增到 0x404400,第 2 个半字撞未初始化堆半字窗
    # 0x19e100;第一轮 16 次全完成后走 0x63c6→0x63ca→0x63cc→0x6240 回边;
    # 零装载论证限于 rep 体——外层循环 0x6242 ld.w.idxs 实有装载;st.h.pin
    # 短形有 exact_postincrement_store 直写面而 st.h.pin.l 长形无——对称性
    # 缺口归下路半字后增量长形族);gate 计数(codex 6.2 逐条核对+同边界双臂
    # 隔离实测):上站 42,173,271 旧代码口径 full-word 15,351/halfword 745,
    # 本路跨代码臂进入 0x6198 前 15,352/746(=resume 段 0x606fb8c word+ROM
    # 0x7604 half 两迁移),释放段恰 full-word +21(0x6198 mul×1+0x5e70
    # sub×11+0x5e2a mov×2+0x61fc-0x6224 add×7)/halfword +0,终点跨代码臂
    # 15,373/746;从零全量口径 15,378/747(=快照段 5word+1half 迁移再入账,
    # check() 断言钉从零口径,跨代码臂偏差 −5/−1 由快照账本差解释);timer0
    # cnt 8,259→9,696(恰 +1,437=479×3,PRD 11,999 窗内零新到期)、core-tick
    # cnt 1,407,136→1,409,052(恰 +1,916=479×4,347/347 冻结)、零新喂狗
    # (163/162 冻结)、jl_fm 恰一笔冻结。
    require(r['instruction_adapters'].get('parallel_full_word_store_old_registers')==355250 and
            r['instruction_adapters'].get('parallel_halfword_store_old_registers')==19689 and
            r['stop']['kind']=='instruction_budget' and
            r['stop_pc']=='0x6064220' and
            r['peripherals']['timer0']['cnt']==49962 and
            r['peripherals']['core_tick_configuration'][0]['cnt']==65344 and
            'exact_parallel_mul_prefixes' in r['assumptions']['diagnostics'],
            'the route-SEGIMPL3 release record must pin the runtime evidence: the '
            'mul-scalar-prefix (16 hw-v3-captured forms) x store-second-half shape '
            'rides its OWN opt-in sub-switch exact_parallel_mul_prefixes (NOT folded '
            'into the pure-ALU list: exactly EIGHT mul-prefix x st.* executions at '
            'six sites exist in the already-executed window 0..42,173,271 — six '
            'inside the base-snapshot prefix 0..41,884,941 (word: 0x601d1ee x2, '
            '0x601d282 x2, 0x606df1e x1; halfword: 0x6011be8 x1) and two in the '
            'resume segment (word 0x606fb8c, halfword ROM 0x7604), all served by '
            'the upstream pair model on known words and cross-checked by a from-'
            'zero watch replay against the self-captured base-snapshot ledger '
            '(adapters 14615/568 identical in both) — the halfword_pair/'
            'word_store_sp_l/CQ precedent keeps executed shapes on their own '
            'sub-switch), releasing the '
            '42,173,271 stop at 0x6198 whose st.w.sp half direct-writes the whole '
            'uninitialized frame word 0x19e4bc (word overwrite makes the old value '
            'irrelevant — synthetic initialization REJECTED as in SEGIMPL); the '
            'released +479-step segment crosses 0x61aa mul.modimm.rri.p||ld.w on '
            'the upstream model (load half, outside this gate), walks the ROM 0x6170 '
            'epilogue st.w.idxs record words and enters the 0x63xx two-round rep '
            'clearing loops, stopping at 0x63c2 rep.i 4 16 { st.h.pin.l } whose '
            'SECOND-round SECOND-halfword merge-read hits the uninitialized heap '
            'halfword window 0x19e100 (round two starts at r11=0x4043fe: its first '
            'halfword lands and post-increments to 0x404400, the SECOND one hits '
            'the wall; round one completed all sixteen body executions and exited '
            'via 0x63c6->0x63ca->0x63cc->0x6240; the zero-load argument covers '
            'the REP BODY only — the outer loop 0x6242 ld.w.idxs does load; '
            'st.h.pin short form already direct-writes via '
            'exact_postincrement_store; the st.h.pin.l LONG form is a symmetry gap '
            'belonging to the next route, which released it on the route-SEGIMPL4 '
            'sub-switch and moved the stop on to the 0x60736c8 register-count REP '
            'clamp body); the adapters pin the FROM-ZERO ledger '
            '(15,378/747 at the 0x63c2 stop, walking on to 15,509/774 through the '
            'SEGIMPL4 release segment): the prior 42,173,271 code left '
            '15,351/745, the cross-code arm enters 0x6198 at 15,352/746 (the two '
            'resume-segment migrations 0x606fb8c word + ROM 0x7604 halfword), the '
            'release segment adds exactly +21 word (0x6198 mul x1, 0x5e70 sub x11, '
            '0x5e2a mov x2, 0x61fc-0x6224 add x7) and ZERO halfword, landing the '
            'cross-code arm at 15,373/746 — the from-zero row carries five more '
            'word and one more halfword from the snapshot-segment migrations '
            '(cross-code delta -5/-1 explained by the snapshot ledger), and the '
            'SEGIMPL4 release segment adds +131 word / +27 halfword more, TIMER0 '
            'cnt 8259->9696 = exactly +479 x 3 STD12M ticks '
            'inside the PRD 11999 window with zero new expiry and the CPU0 core '
            'tick walks 1407136->1409052 = exactly +479 x 4 with 347/347 frozen, '
            'both walking on through the SEGIMPL4 release segment (TIMER0 to '
            '21975 across thirteen new expiries, core tick to the post-overflow '
            'held snapshot 39124 with one new expiry), '
            'zero new feed (163/162 frozen) and the jl_fm ledger frozen at its '
            'single write; the frontier then runs the audio-active SRC loop to '
            'instruction_budget 139,947,345 @ 0x6064220 (parallel full-word '
            'adapter 355250, halfword 19689, TIMER0 cnt 49962, CPU0 core tick '
            '65344) — this does NOT verify the madd/msub forms (a SEPARATE '
            'family: v3 holds 48 madd and 40 msub ||st.w captures left outside '
            'this route\'s review), the mul.f form (no v3 capture), or the '
            '0x19e100 window\'s '
            'unknown values')
    # 路 REPCOND:寄存器计数 rep 体内非嵌套条件块拆解执行(rep_conditional_
    # body 子开关)的释放记录断言——SEGIMPL4 前沿 42,231,273 @ 0x60736c8 的
    # `rep 16 r0 { ld.w r4=[r1+0]; ifs.gt.rr(r4>r2){r4=r2}; ifs.lt.rr(r4<r3)
    # {r4=r3}; st.w.pin [r1++=4]=r4 }`(r2=0x7FFFFF/r3=0xFF800000,计数 r0=
    # [r8+8]>>2 无 mask)由本开关放行:multi-block rep gate 体校验 walk 块感知化,
    # 只放行寄存器计数 rep 体内的非嵌套 ifs.gt.rr/ifs.lt.rr、t=1/e=0、块内唯一
    # then=有模型 mov.rr、块终点严格早于 rep 终点(块后留无条件尾)、体内不引用
    # 计数寄存器(计数寄存器每轮由 rep 机制递减,体内读它见未测中间值、写它使
    # 两计数分离——一律拒绝)。组合投影=两个已独立 hw 验证事实(rep 字节计数
    # 循环+块条件选臂)的组合,标 derived-not-measured(rep×体内控制流无硬件
    # 组合捕获);全零入口只覆盖两条件都假路径,上钳/下钳取真路径由隔离单测
    # 覆盖。放行后整环 1(rep 头)+128×4=513 步本核指令:每轮 ld.w+2 个 ifs
    # 开启器+st.w.pin(全零下两 mov 各零次命中,st.w.pin 直写 128 笔=exact_
    # postincrement_store +128),r1 每轮 +4,r0 终 0,bgts.ri 死分支不跳,出口
    # r1=0x41d50c;环后长走段先过 0x6070a9a(st.w [0xfe2608]=0x00010002 的
    # CON2 整字写,现由 jl_src_sync_full_block 新容器放行)再放行 COEF 55 笔
    # st.w.pin(exact_postincrement_store +55)与函数 B 11 字纯写中已触达的 7 笔
    # (CON2/CON3/PHASE/FLTB_ADR/STOP_FLAG/INSR/OUTSR,IDAT_ADR/IDAT_LEN/
    # ODAT_ADR/ODAT_LEN 四字未触达),随后 helper 0x607099c 首笔 SRC_SYNC
    # 配置事务在 0x60709ba 对 0xfe2604(CON1)做 |=0x400000 RMW,该 RMW PC
    # 由路 SRCSYNC3 放行(CON0_RMW_PCS/CON1_RMW_PCS 扩充 helper 六 PC),
    # 停点 42,232,325 @ 0x60709e0(jl_src_sync_coef_read_not_modeled,COEF
    # 读诚实停,核 0;新停点 IPMASK 6=下游长走段仍在临界区内,SEGIMPL4 停点
    # IPMASK 0 的 unmasked 形状不再适用)。gate 计数(从零口径):
    # register_rep_multi_block_projection 826→828
    # (钳位环 rep 头恰 1 次+helper 0x60709d0 memcpy 返回 trampoline rep 恰 1
    # 次)、exact_postincrement_store 78,587→78,866(+128 笔 st.w.pin=128 轮+
    # COEF 55 笔+helper memcpy 96 笔)、exact_postincrement_subword_store 550
    # 冻结(钳位环全整字,零迁移);timer0 cnt 21,975→25,131(+2,490=830×3
    # REPCOND 段+666=222×3 SRCSYNC3 段,PRD 59,999 窗内走表零新到期/零新
    # 交付)、core-tick 39,124→43,332(冻结快照口径,+4,208=1052×4)、零新喂狗
    # (163 冻结)、jl_fm 恰一笔冻结。
    require(r['stop']['kind']=='instruction_budget' and r['stop_pc']=='0x6064220' and
            r['instruction_adapters'].get('register_rep_multi_block_projection')==8209 and
            r['instruction_adapters'].get('exact_postincrement_store')==108419 and
            r['instruction_adapters'].get('exact_postincrement_subword_store')==552 and
            r['instruction_adapters'].get('exact_preincrement_subword_store')==20 and
            r['peripherals']['timer0']['cnt']==49962 and
            r['peripherals']['core_tick_configuration'][0]['cnt']==65344 and
            r['peripherals']['watchdog_projection']['feeds']==233 and
            r['peripherals']['jl_fm_con_head_registers']['word_writes']==1 and
            'rep_conditional_body' in r['assumptions']['diagnostics'],
            'the route-REPCOND release record must pin the runtime evidence: the '
            'REGISTER-count REP whose in-body conditional blocks (ifs.gt.rr/ifs.lt.rr '
            'with t=1/e=0 and a single mov.rr then) are unrolled round-by-round under '
            'the opt-in rep_conditional_body sub-switch rides the block-aware body '
            'walk (narrow subset: non-nested signed-compare openers only, one modelled '
            'mov.rr per block, block end strictly before the unconditional tail, and '
            'no reference to the count register inside the body) — the 24-bit audio '
            'sample saturation copy loop at 42,231,273 executes exactly 128 rounds '
            '(513 native instructions = 1 rep header + 128 x (ld.w + 2 ifs openers + '
            'st.w.pin)) on the all-zero entry whose two conditions are both false '
            '(the upper/lower clamp-taken paths are covered by isolated unit tests, '
            'not by this frontier entry), the 128 st.w.pin direct writes pulse '
            'exact_postincrement_store 78587->78715 and the rep header adds the '
            'register_rep_multi_block_projection 826->828 count (the clamp rep '
            'header plus the helper 0x60709d0 memcpy return trampoline rep), then '
            'the dead '
            'bgts.ri falls through; the 0x6070a9a CON2 whole-word write (0x00010002 '
            'to 0xfe2608) is now modelled by the jl_src_sync_full_block container '
            '(7 words reached: CON2/CON3/PHASE/FLTB_ADR/STOP_FLAG/INSR/OUTSR; '
            'IDAT_ADR/IDAT_LEN/ODAT_ADR/ODAT_LEN not reached) and the 55-word '
            'COEF st.w.pin tail walks exact_postincrement_store on to 78770, then '
            'the helper 0x607099c CON1 |=0x400000 and CON0 |=0x10 RMWs are '
            'released by SRCSYNC3 and the 0x60709d0 memcpy walks it on to 78866; '
            'the walk then continues to the NEW honest stop 42,232,325 @ 0x60709e0: '
            'the helper 0x607099c COEF read [r5+60]=0xfe263c is refused '
            '(jl_src_sync_coef_read_not_modeled, core 0); TIMER0 cnt walks '
            '21975->24465 (+830 x 3 STD12M ticks inside the PRD 59999 window, zero '
            'new expiry) and on to 25131 (+222 x 3 more, still zero new expiry), '
            'the CPU0 core tick lands on the post-overflow held '
            'snapshot 42444 then 43332, zero new feed (163 frozen) and the jl_fm ledger frozen '
            'at its single write; the frontier then runs the audio-active SRC loop on to '
            'instruction_budget 139,947,345 @ 0x6064220, where the watchdog feeds reach '
            '233, TIMER0 cnt lands at 49962 (PRD 59999) and the CPU0 core tick at 65344 '
            '— this does NOT verify the REP x in-body '
            'control-flow composition on silicon (derived-not-measured: the FM-1 '
            'captures the straight-line-addition-body REP net effect and the '
            'independent signed-conditional-block arm selection separately, never the '
            'composition), the 128-round intermediate register states or the IRQ '
            'visibility points, or the JL_SRC_SYNC full-block word semantics')
    # 路 JLEQ:park 稳态证据体断言——喂狗邮箱事务全闭合、事件钟推进、两核
    # parked 唤醒计数、EQ 两字记录、GPCNT 完成位读数。这是 D1「S19 空转+
    # 两核 park」判定的报告面证据(全部只观察不发明)。
    p2m=r['peripherals']['p2m_mailbox']
    require(p2m['kicks']==232 and len(p2m['transactions'])==232 and
            p2m['clear_slot_writes']==232 and p2m['pnd_reads']==232 and
            p2m['echo_reads']==232 and p2m['other_kick_values']=={} and
            all(t=={'arg':8,'poll_reads':1,'echo_byte':8,'clr_writes':1}
                for t in p2m['transactions']),
            'the feed-dog mailbox transactions must all be closed on this prefix: 232 '
            'kicks, 232 first-read PND exits, 232 matching echoes (arg 8 == echo '
            '8) and 232 CLR closures with zero foreign kick values — the armed-'
            'transaction precedence over the reset-history snapshot (route JLEQ fix) '
            'is what un-sticks the 0x12cca2 poll, the route-GPCNUM extension carried '
            'sixteen closed transactions into the pseudo-expiry frontier and the '
            'route-WDFEED release lets the run continue past it (146 more feeds '
            'through the 41,828,530 stop, every single one rearming the WDT; the '
            'route-STOREG3 release segment beyond it adds no transaction — the '
            '162 totals stay frozen through the 41,847,303 stop, and the audio-'
            'active SRC loop then carries the closed-transaction total on to 232 '
            'at the instruction_budget stop)')
    # 路 WDFEED:喂狗副作用投影断言——162 笔闭合事务恰 162 次重装(feeds
    # 总数 163=主核 init 直接 feed 1+邮箱 162),未到期(停点 elapsed 134<
    # 16384——路 STOREG3 过墙段是纯 CPU0 顺序段,事件钟冻结、无新喂狗事务,
    # elapsed 100→104 只涨 4 个名义单位;路 FD610C-con0 再过墙 +576 步纯
    # CPU0 段又涨 1 个名义单位到 105;路 P2MCLK +2,598 步纯 CPU0 段维持
    # 105;路 BATCHA +7,033 步段(LED 复位 helper 走完→S19 环→资源 VFS)
    # 涨 2 个名义单位到 107——两段均无新喂狗事务,162/163 冻结;路 TONMAP
    # +51,493 步段再涨 13 个名义单位到 120;路 EVIMPL(jlstream 任务栈单点
    # 4B 槽放行)+56,377 步段又涨 14 个名义单位到 134——两段同样零新喂狗
    # 事务,162/163 冻结;路 FMIMPL(0xF00000 CON 头 RMW 放行)+110,963
    # 步段(fn_config 返回→框架表派发→新任务 0x6086c8c 装配→ROM 字节→
    # 半字展开环)再涨 28 个名义单位到 186,段内零新喂狗事务,162/163
    # 冻结;路 SEGIMPL4 释放段(+57,523 步清零环十轮+长走段)再涨 14 个
    # 名义单位到 200,段内零新喂狗事务,162/163 仍冻结;路 REPCOND 释放段
    # (+830 步钳位环 128 轮+下游长走段到 0x60709ba)再涨 1 个名义单位到
    # 201,段内零新喂狗事务,162/163 仍冻结;路 SRCSYNC3 释放段(+222 步
    # helper CON1/CON0 RMW+memcpy 到 0x60709e0)不足一个名义单位,elapsed
    # 201 冻结),command_values
    # 保持主核 6 条真实命令零伪造(副作用不写总线、不消耗 key_phase/
    # commands);已到期拒绝补喂的语义在单测钉死。
    wdt=r['peripherals']['watchdog_projection']
    require(wdt['feeds']==233 and wdt['mailbox_feed_side_effects']==232 and
            wdt['expired'] is False and wdt['elapsed_nominal_units']==321 and
            wdt['commands']==6 and wdt['command_values']==[0,0,0,14,46,65582] and
            'mailbox_feed_side_effect' in wdt['assumptions'] and
            'NOT verified' in wdt['assumptions']['mailbox_feed_side_effect'],
            'the route-WDFEED mailbox feed side-effect must reload the WDT exactly '
            'once per closed feed transaction (232 reloads against the 232 closed '
            'transactions, feeds total 233 = one direct init feed + 232 projected), '
            'leaving the watchdog NOT expired at the stop (elapsed 186 of the 16384 '
            'nominal units of the CON 0x2e period at the FMIMPL stop, walking on '
            'to 200 through the route-SEGIMPL4 release segment — fourteen more '
            'nominal units across the +57,523-step walk with zero new feed '
            'transactions, and on to 201 through the route-REPCOND release '
            'segment (+830 steps: the 128-round clamp loop plus the downstream '
            'walk to 0x60709ba — one more nominal unit, still zero new feed '
            'transactions); the route-SRCSYNC3 release (+222 steps: the helper '
            'CON1/CON0 RMWs plus the memcpy to 0x60709e0) stays inside the same '
            'nominal unit, still zero new feed transactions); the route-STOREG3 release '
            'segment past 41,828,530 is a pure CPU0 sequence, no new feed '
            'transaction, only four more nominal units consumed; the route-'
            'FD610C-con0 release past 41,847,806 is another pure CPU0 sequence, '
            'one more nominal unit consumed; the route-TONMAP release walks the '
            'elapsed units on to 120, the route-EVIMPL release (the jlstream '
            'task-stack 4B slot unknown-word pass) on to 134 and the route-DMAIMPL '
            'audio-pipeline segment on to 152; the route-COPEXIMPL release (the '
            'mode-22 exponential pass) walks five more nominal units on to 157 — '
            'all pure CPU0 sequences, zero new feed transactions, the 162/163 '
            'totals frozen; the route-SRCSYNC and route-SYNCIMPL releases walk '
            'one more unit on to 158 and freeze it (+2,287 then +2 then '
            '+119 pure CPU0 steps — the TIMERIMPL release segment stays '
            'inside the same nominal unit); the route-FMIMPL release '
            '(the modem-window CON-head RMW) walks 28 more nominal units on '
            'to 186 (+110,963 steps through the fn_config return, the '
            'framework table dispatch, the new 0x6086c8c task installation '
            'and the ROM byte-to-halfword expansion loop — zero new feed '
            'transactions, the 162/163 totals stay frozen through the SRCSYNC4 stop) '
            'with the route-WDTFEED2 idle-wake release adding five more feed '
            'transactions (163/162 → 168/167) before the frontier enters the '
            'audio-active steady state (SRC loop): the SRC completion IRQ89 '
            're-entry keeps CPU0 running the SRC reconfiguration loop rather '
            'than parking, so the watchdog feeds climb to 233/232 by the '
            'instruction_budget stop (elapsed 321 nominal units, still NOT '
            'expired — the WDT is fed through the mailbox transactions instead '
            'of starving behind the un-modeled P11 side) with the '
            'main-core command accounting untouched (six real commands '
            '[0,0,0,14,46,0x1002E], zero fabricated bus writes) and the '
            'expiry-stays-a-reset honesty flag intact')
    parked_timer0=sum(1 for d in r['machine']['timer0_irq_entry_projection']['deliveries']
                      if d['reti_from']=='0x12c5cc')
    parked_ct=sum(1 for d in r['machine']['core_tick_entry_projection']['deliveries']
                  if d['reti_from']=='0x12c5cc')
    parked_sw=sum(1 for d in r['machine']['software_irq_entry_projection']['deliveries']
                  if d['reti_from']=='0x12c5cc')
    require((parked_timer0,parked_ct,parked_sw)==(8435,17605,7839) and
            r['machine']['event_clock_projection']['virtual_model_steps_advanced']==67174629,
            'the audio-active steady state (SRC loop) must show the idle-wake shape '
            'cycling through reti_from 0x12c5cc: 8435 Timer0 IRQ4 wakes plus 17605 '
            'core-tick wakes and 7839 software-IRQ wakes all reti_from 0x12c5cc — '
            'the instruction AFTER the idle poll head 0x12c5ca (the idle-wake '
            'consumes the IDLE, so a woken core runs 0x12c5cc instead of re-parking '
            'on 0x12c5ca; the SRC completion IRQ89 re-entry keeps waking CPU0 out '
            'of IDLE between SRC reconfiguration rounds instead of the old dual-core '
            'watchdog feed loop 0x12c5a4..0x12c5cc) — with the event clock '
            'advancing 67,174,629 '
            'virtual model steps through the same per-step advance() — the parked '
            'counters and the event clock stay FROZEN through the route-STOREG3 '
            'release segment (41,828,530 → 41,847,303 is a pure CPU0 sequence: '
            'one non-parked Timer0 delivery, zero parked wakes, zero event-clock '
            'steps, mutually confirming) — the SRC-loop steady state, now running '
            'PAST the old pseudo-expiry point (the watchdog is fed through the '
            'mailbox transactions instead of starving behind the un-modeled P11 '
            'side)')
    eqrec=r['peripherals']['jl_eq_init_write_records']
    require(eqrec['word_writes']==2 and eqrec['read_attempts']==0 and
            eqrec['stores']=={'0xfe1500':{'values':['0x0'],'writes':1,'cores':[0]},
                              '0xfe151c':{'values':['0x18cb80'],'writes':1,'cores':[0]}} and
            eqrec['write_only'] and eqrec['readback_not_modeled'],
            'the JL_EQ two-word write-only records must hold exactly the EQ-init '
            'function 0x6072b24 stores (CON0=0 and FLT_ADR=the allocated 0x18cb80 '
            'buffer) with zero read attempts on this prefix')
    gpc=r['peripherals']['gpcnt_cleanup_projection']
    require(gpc['completion_bit_projection']['reads']==2137 and
            gpc['config_words_projection']['whole_writes']==744 and
            gpc['config_words_projection']['enable_writes']==248 and
            gpc['num_register_fixture']['reads']==245 and
            gpc['num_register_fixture']['value']=='0x3a98',
            'the GPCNT completion-bit projection must have served the audio-active '
            'steady-state (SRC loop) dispatch schedule (2137 forced-bit7 reads) after '
            'the W4[4] reconfig chain (three whole-word stores, one |=1 enable) plus '
            'the measurement conversions (the route-GPCNUM twenty filling the first '
            'window plus the route-WDFEED extension serving the restarted window\'s '
            'first conversion and the SRC-loop steady state carrying the NUM fixture '
            'reads on to 245, the whole-word compose stores to 744 and the restart '
            'enables to 248) — '
            'the named fixture 15000 serves every conversion the same constant')
    # 路 GPCNUM:结果块快照——探路值数值侧效应逐字节钉死(SHA 口径,窗
    # 0x12A3D0..+0x60 覆盖 avg/fill/valid/半字/20 槽 ring;敏感臂 15360 的
    # SHA 不同而停点/步数/计数全同=值不敏感实证)。
    gpc_snap=r['memory_snapshots']['gpcnt_result_head']
    require(gpc_snap['initialized_bytes']==0x60 and
            gpc_snap['sha256']=='fd7f201a9b9f05ecc994961048a94071a2ef659ad17eaca5c7042101eb7a6c64',
            'the GPCNT result-block snapshot (avg [0x12A3D0], fill/valid bytes, '
            'h[0x12A3DC], the twenty ring slots at 0x12A3E0) must pin the probe-value '
            'side effects byte-exactly — every slot the identity 32768000 mHz frame '
            'of the assumed-nominal fixture; the route-WDFEED park extension serves '
            'the restarted window\'s first conversion, so the SHA moves with the '
            'restarted fill state (the 96-byte initialized extent is unchanged)')
    # 路 B3:CNT held 快照报告断言——键在场、假设文本按 codex 原文措辞、
    # 该场景快照=0(12 次溢出的对齐冻结)、读侧零副作用面如实标注。
    cnt_snapshot=r['peripherals']['core_tick_configuration'][0].get('cnt_held_snapshot_projection')
    require(cnt_snapshot is not None and
            '"CNT frozen after overflow"' in cnt_snapshot['returns'] and
            'assumed-not-measured' in cnt_snapshot['returns'] and
            'NOT PRD-1' in cnt_snapshot['returns'] and
            cnt_snapshot['silicon_wraparound_behavior_measured'] is False and
            cnt_snapshot['held_window_progress_lost_if_frozen'] is True and
            'none — reading neither ACKs' in cnt_snapshot['read_side_effects'],
            'the core-tick CNT held-snapshot projection must carry the explicit '
            '"CNT frozen after overflow" assumption (conditional/assumed-not-measured, '
            'NOT PRD-1) and the no-side-effect contract verbatim')
    # 路 B3:JL_ADDA 模拟族容器断言——恰五字白名单、112 写(23/32/16/30/11;
    # 路 B3 停点时 94 写(23/32/16/18/5),路 DT 段 0x606b5b6..0x606b5ea(DT
    # 停点 0x606b5ee 之前,含 delay(20) 忙等)再对 0xfe0a48 做 +11 RMW,
    # 路 ADA 段 0x606b5f2..0x606b68c 再 +1(0x606b5f2 [r6+40]&=0xFFFFFE1F)
    # 并对 0xfe0a4c 再 +6(0x606b5f6/5fa/5fe/602/626/62a 六处 RMW))、读回
    # 计数(107 写后读+5 合成初始读)、互斥与已知限制如实标注。
    adda=r['peripherals'].get('jl_adda_analog_family_registers')
    require(adda is not None and
            adda['addresses']==['0xfe0a00','0xfe0a08','0xfe0a0c','0xfe0a48','0xfe0a4c'] and
            adda['word_writes']==138 and
            {a:e['writes'] for a,e in adda['stores'].items()}==
            {'0xfe0a00':23,'0xfe0a08':32,'0xfe0a0c':39,'0xfe0a48':31,'0xfe0a4c':13} and
            adda['readback_projection']['reads']==133 and
            adda['readback_projection']['synthesized_initial_reads']==5 and
            '0x606b352' in adda['readback_projection']['known_limitation'] and
            'mutually exclusive' in adda['whitelisted_words'],
            'the JL_ADDA analog family container must record exactly the five whitelisted '
            'words, the 138 executed write-backs (23/32/39/31/13 per word — the route-DT '
            'audio-ADC-init segment adds +11 on 0xfe0a48, the route-ADA segment adds one '
            'more 0xfe0a48 RMW at 0x606b5f2 plus six 0xfe0a4c RMW, the route-LPV tail '
            'segment adds two more 0xfe0a4c RMW inside the second-level tbh case bodies, '
            'and the route-WDFEED park extension adds one more 0xfe0a48 write-back during '
            'the continued steady state past the old frontier), '
            'the 109 post-write '
            'reads plus 5 synthesized initial reads, the route-CV DAA_CON1 whitelist '
            'mutual exclusion and the deferred DAA_CON2 bit1 status-consumer '
            'limitation (0x606b352)')
    # 路 DT:JL_AUDIO DAC_TM0/TM1 容器断言——恰两字白名单、两次 -600 半字
    # 写各触一字(实测 TM0=TM1=0xFDA80000,第二次 selector∈{9,11} 写 TM1
    # 高半=静态预测兑现)、零写后读(全 app 无 setter 外读者)、初值来源
    # synthetic_zero 具名登记+撤回「getter/时间标记」预判+新读者重审边界
    # 如实标注。敏感性探针(ones 字只注 TM0 初值一次性分叉):同停点同
    # 步数、TM0 写回 0xFDA8_FFFF(vs 基线 0xFDA8_0000,异或恰 0xFFFF)、
    # TM1 两臂同 0xFDA80000=观察路径对初值不敏感、差异只留在容器存储字
    # (结果钉进路叙事)。
    dac_tm=r['peripherals'].get('jl_audio_dac_tm_registers')
    require(dac_tm is not None and
            dac_tm['addresses']==['0xfe0524','0xfe0528'] and
            dac_tm['word_writes']==8 and
            {a:e['writes'] for a,e in dac_tm['stores'].items()}==
            {'0xfe0524':4,'0xfe0528':4} and
            dac_tm['stores']['0xfe0524']['values']==['0xfda80000','0xfda80800',
                                                     '0xfda80800','0xfda80800'] and
            dac_tm['stores']['0xfe0528']['values']==['0xfda80000','0xfda80800',
                                                     '0xfda80800','0xfda80800'] and
            dac_tm['readback_projection']['reads']==6 and
            dac_tm['readback_projection']['synthesized_initial_reads']==2 and
            'synthetic_zero' in dac_tm['readback_projection']['initial_value_origin'] and
            'NOT a reset-value claim' in dac_tm['readback_projection']['readback_semantics'] and
            're-audited' in dac_tm['readback_projection']['known_limitation'] and
            '0x606ab92' in dac_tm['whitelisted_words'],
            'the route-DT jl_audio_dac_tm_registers must record exactly the two whitelisted '
            'words, the two executed -600 half-word write-backs (TM0 = TM1 = 0xFDA80000, '
            'the second selector writing the TM1 high half as the static 12-way map '
            'predicts), zero post-write reads plus 2 synthesized initial reads, the named '
            'synthetic_zero initial-value origin, the no-reset-value claim and the '
            'new-reader re-audit limitation')
    # 路 ADA:JL_ADDA ADA 子族九字容器断言——恰九字白名单(0xfe0a20..0xfe0a40
    # ADA_CON0..8;0xfe0a44 ADA_CON9 零触达留 unmapped)、本前缀 12 写
    # (0xfe0a20×7/0xfe0a24×4/0xfe0a28×1——0x606b5f2..0x606b68c 段:tbb/tbh
    # 分支体未进入,二级 tbh 0x606b774 在停点之后)、9 写后读+3 合成初始
    # 读、末值 0xfe0a20=0x2a0203/0xfe0a24=0x2e/0xfe0a28=0x4、synthetic_zero
    # 初值+同址新读者重审+close 语义+完备性措辞(已识别直接 li 站点,间接
    # 来源未穷尽)如实标注。
    ada=r['peripherals'].get('jl_adda_ada_family_registers')
    require(ada is not None and
            ada['word_writes']==71 and
            {a:e['writes'] for a,e in ada['stores'].items()}==
            {'0xfe0a20':18,'0xfe0a24':12,'0xfe0a28':9,'0xfe0a2c':3,
             '0xfe0a30':27,'0xfe0a40':2} and
            ada['stores']['0xfe0a20']['values'][-1]=='0x292a82cb' and
            ada['stores']['0xfe0a24']['values'][-1]=='0x2e' and
            ada['stores']['0xfe0a28']['values'][-1]=='0x400012d4' and
            ada['readback_projection']['reads']==65 and
            ada['readback_projection']['synthesized_initial_reads']==6 and
            'synthetic_zero' in ada['readback_projection']['initial_value_origin'] and
            'NOT a reset-value claim' in ada['readback_projection']['readback_semantics'] and
            'ONE' in ada['readback_projection']['readback_semantics'] and
            'real analog channel is off' in ada['readback_projection']['close_semantics'] and
            '0xfe0a44' in ada['whitelisted_words'] and
            'NEW READ CONSUMER' in ada['whitelisted_words'] and
            'INDIRECT sources' in ada['access_face_completeness'],
            'the route-ADA jl_adda_ada_family_registers must record exactly the nine '
            'whitelisted ADA words, the 71 executed write-backs of this prefix '
            '(18/12/9/3/27/2 on ADA_CON0/1/2/3/4/8 — the sequential segment '
            '0x606b5f2..0x606b68c of the ADA config function contributed the first 12 '
            '(7/4/1); the route-LPV tail segment then entered the second-level tbh case '
            'bodies 0x606b774.., whose six-word RMW/compose shapes were already part of '
            'the route-ADA static full map — every touched word stays inside the nine-'
            'word whitelist, 0xfe0a34/38/3c and 0xfe0a44 stay untouched), the 51 '
            'post-write reads plus 6 synthesized initial reads, the final values '
            '0x292a82cb/0x1ee/0x40001ed4, the named '
            'synthetic_zero origin, the shared-one-container-state RMW contract, the '
            'close-semantics caveat, the 0xfe0a44 unmapped boundary with the new-address '
            'AND new-consumer re-audit rule, and the direct-li-sites completeness wording '
            '(indirect sources not exhausted)')
    # 路 LPV:P11_LPVAD VAD_ACON0 容器断言——恰一次合成初始 0 内部读(停点
    # 0x606b68e bit28 置位 RMW)+恰一条写回 0x10000000(0|0x10000000)+写后
    # 零读(窗内全 app 无其他消费者)+报告五面(单字白名单/同址新读者重审
    # 含新掩码/cross-domain 限制/完备性措辞含不覆盖 ROM q32s/模型假设含
    # 弱替代案区别)。
    lpv=r['peripherals'].get('p11_lpvad_config_registers')
    require(lpv is not None and
            lpv['word_writes']==1 and
            lpv['stores']=={'0xf2b404':{'values':['0x10000000'],'writes':1}} and
            lpv['reads_after_write']==0 and
            lpv['synthesized_initial_reads']==1 and
            'modeled exactly the one' in lpv['modeled_vs_touched'] and
            'NEW READ CONSUMER' in lpv['whitelisted_words'] and
            'new RMW mask' in lpv['whitelisted_words'] and
            'does NOT model q32s access' in lpv['cross_domain_limitations'] and
            'does NOT mean the real LPVAD' in lpv['cross_domain_limitations'] and
            'does NOT cover ROM, q32s' in lpv['access_face_completeness'] and
            'WEAKER alternative' in lpv['model_assumptions'],
            'the route-LPV p11_lpvad_config_registers must record exactly the one '
            'whitelisted word 0xf2b404, the single stop-RMW write-back 0x10000000 '
            '(synthetic initial 0 | 0x10000000), the one synthesized initial read '
            'and zero post-write reads (no other consumer in the window), the '
            'single-word whitelist with the same-address new-consumer re-audit '
            'rule (new ld.w/PC/RMW mask/core), the codex cross-domain wording '
            '(no q32s access, no LPVAD detection, the stop passing does not mean '
            'the real LPVAD is configured), the tightened completeness wording '
            '(ROM/q32s not covered), and the registered weaker-alternative '
            'assumption-strength difference')
    # 路 JLA:JL_AUDIO ADC DMA 五字容器断言——恰五写(set_buffer 五写全集:
    # ADR=0x419264 停点本尊/LEN=0x100/PNS=0x80/HWP=0/SRP=0,值全真实指令
    # 结果)、零读(本前缀;IRQ68 handler 的 ADR/PNS/SRP 读属 D2-b 后域)、
    # 报告面(sdk_naming 含 set_buffer/白名单恰五字含 0xfe0550 NOT included
    # 与同址新写者重审/observed_shapes 含 0x606df20/consumption_face 含
    # audio_adc_irq_handler/写侧 write_only)。
    adc_dma=r['peripherals'].get('jl_audio_adc_dma_write_records')
    require(adc_dma is not None and
            adc_dma['addresses']==['0xfe053c','0xfe0540','0xfe0544','0xfe0548','0xfe054c'] and
            adc_dma['word_writes']==5 and
            adc_dma['stores']=={'0xfe053c':{'values':['0x419264'],'writes':1,'cores':[0]},
                                '0xfe0540':{'values':['0x100'],'writes':1,'cores':[0]},
                                '0xfe0544':{'values':['0x80'],'writes':1,'cores':[0]},
                                '0xfe0548':{'values':['0x0'],'writes':1,'cores':[0]},
                                '0xfe054c':{'values':['0x0'],'writes':1,'cores':[0]}} and
            adc_dma['write_only'] and adc_dma['readback_not_modeled'] and
            'set_buffer 0x606dee4' in adc_dma['sdk_naming'] and
            '0xfe0550' in adc_dma['whitelisted_words'] and
            'NOT included' in adc_dma['whitelisted_words'] and
            'same-address new writer' in adc_dma['whitelisted_words'] and
            '0x606df20' in adc_dma['observed_shapes'] and
            'audio_adc_irq_handler' in adc_dma['consumption_face'] and
            'the only synthetic input is the sample bytes' in
            r['peripherals']['assumptions']['jl_audio_adc_dma_write_records'] and
            'DMA completion TIMING is a projection' in
            r['peripherals']['assumptions']['jl_audio_adc_dma_write_records'],
            'the route-JLA jl_audio_adc_dma_write_records must record exactly the five '
            'executed set_buffer stores (ADR 0x419264 — the very parallel-pair '
            'store-half word of the 21,800,325 stop — LEN 0x100, PNS 0x80, HWP 0, '
            'SRP 0, every value a real instruction result), zero reads on this '
            'prefix (the IRQ68-handler ADR/PNS/SRP reads belong to the D2-b '
            'completion domain; with the route-D2AI delivery layer riding the '
            'conditional stack they would be served by the container — zero '
            'handler reads happen on THIS prefix, the park world injects no sample '
            'blocks), the five-word whitelist '
            'with the SRN 0xfe0550 exclusion and the same-address new-writer '
            're-audit rule, and the no-audio-input limit (merge-integration fix: '
            'the D2AJ promotion of audio_adc_dma_delivery into the conditional '
            'stack flips this assumption text to its delivery-on variant — the '
            'route-JLA off-variant pin no longer matched the merged tree; the '
            'world itself is behaviorally identical, zero deliveries, zero '
            'container reads)')
    # 路 ASS:ADC 启动配置块四字容器断言——恰 9 写(MIC_CTL0 两写 |=0x2 后
    # &=~0x1,零臂合成初始下两写回均 0x2;MIC_CTL1 家族 B 四写,insert 族
    # 读值参与组装,终值 0xff8=codex 静态预测;ADC_CON1 三写全 0x0=param
    # 位段全零;MBG_CON 零写=[sp] 门控在本路径关)+3 合成初始读+6 写后读;
    # 邻字边界(AK 两字/JLA SRN/零触达三字)+报告面(间接构造叙事/同址新
    # 读者重审/完备性措辞/合成初始显式假设/跨域限制)。
    adc_start=r['peripherals'].get('jl_adc_start_config_registers')
    require(adc_start is not None and
            adc_start['addresses']==['0xfe0904','0xfe0908','0xfe0554','0xfe055c'] and
            adc_start['word_writes']==9 and
            adc_start['stores']=={'0xfe055c':{'values':['0x0','0x0','0x0'],
                                              'writes':3,'cores':[0,0,0]},
                                  '0xfe0904':{'values':['0x2','0x2'],'writes':2,
                                              'cores':[0,0]},
                                  '0xfe0908':{'values':['0xe00','0xfc0','0xff8','0xff8'],
                                              'writes':4,'cores':[0,0,0,0]}} and
            adc_start['readback_projection']['reads']==6 and
            adc_start['readback_projection']['synthesized_initial_reads']==3 and
            set(adc_start['stores'])=={'0xfe055c','0xfe0904','0xfe0908'} and
            'stay with the route-AK container' in adc_start['whitelisted_words'] and
            '0xfe0550' in adc_start['whitelisted_words'] and
            'same-address new consumer' in adc_start['whitelisted_words'] and
            'r0=r5+976' in adc_start['observed_shapes'] and
            '0x606c8ca' in adc_start['observed_shapes'] and
            'NOT covered' in adc_start['access_face_completeness'] and
            'synthetic-not-measured' in adc_start['model_assumptions'] and
            'does NOT mean the real' in adc_start['cross_domain_limitations'] and
            '[SDK-inferred]' in adc_start['sdk_naming'] and
            'synthetic config words' in adc_start['known_limitation'] and
            'NOT verify any audio input' in
            r['peripherals']['assumptions']['jl_adc_start_config_registers'],
            'the route-ASS jl_adc_start_config_registers must record exactly the nine '
            'executed write-backs of the ADC start function 0x606df66 config block '
            '(MIC_CTL0: |=0x2 then &=~0x1, both 0x2 under the synthetic initial zero; '
            'MIC_CTL1 family B: four insert-group write-backs ending 0xff8 — the codex '
            'static-arithmetic prediction; ADC_CON1: three zero write-backs with all-'
            'zero param nibbles; MBG_CON: ZERO writes — the [sp] param gate is closed '
            'on this path), three synthesized initial reads (the first touch of each '
            'of the three touched words) plus six post-write reads, the AK/SRN/zero-'
            'access neighbor boundaries, the indirect-construction narrative '
            '(r0=r5+976 from the 0xfe0534 base, the first stop point immune to the '
            'li enumeration method), the same-address new-consumer re-audit rule, the '
            'completeness wording and the no-audio-input limit')
    require(r['peripherals']['core_tick_configuration'][0]['acknowledgements']==23775 and
            r['peripherals']['core_tick_configuration'][0]['expirations']==23775,
            'the CNT release must let overflows 12..108 (route B3) plus the eight '
            'further overflows inside the route-DT segment (DAC start tail + OS wait '
            'family, 109..116) deliver and ACK naturally, the route-JLEQ steady '
            'state (S16/S17 event-clock cycling) carries the count to 147, the '
            'route-GPCNUM extension (twenty served GPCNT conversions plus five more '
            'feed-dog cycles before the pseudo-expiry frontier) to 200, the '
            'route-WDFEED extension past the crossed expiry point to 347 and '
            'the route-SEGIMPL4 release segment (the clearing loop\'s ten rep '
            'rounds plus the long walk to the clamp REP) to 348 — one more '
            'overflow delivered and acknowledged inside the +57,523-step walk; '
            'the audio-active SRC loop then carries the count on to 23775 '
            'deliveries/acknowledgements at the instruction_budget stop '
            '(the masked window of overflow #12 ends later in the prefix; the BF '
            'per-step recheck delivers every later overflow exactly once)')
    # 路 CW:JL_AUDIO DAC_VL0 容器断言——恰一次合成 0 首读(0x606db4c 并行对
    # 第二半)+恰一条写回 0x0(0x606db68,umin(0x3a98,0)=0 双通道 insert 后
    # 读值位全被 r3=r3.l(u) 清零)+写后零读(getter/walker/静音都在音频任务
    # 域,D1 后)。敏感性探针(ones 字 0xffffffff 一次性分叉):同步数同停点、
    # 写回同 0x0、计数同构=观察路径对初值不敏感(结果钉进路叙事)。
    vl0=r['peripherals']['jl_audio_dac_vl0_registers']
    require(vl0['stores']=={'0xfe051c':{'values':['0x0'],'writes':1,'cores':[0]}} and
            vl0['word_writes']==1 and
            vl0['addresses']==['0xfe051c'] and
            vl0['readback_projection']['reads']==0 and
            vl0['readback_projection']['synthesized_initial_reads']==1 and
            'synthetic initial word 0' in vl0['readback_projection']['readback_semantics'] and
            'NOT a reset-value claim' in vl0['readback_projection']['readback_semantics'] and
            'initial-word-insensitive' in vl0['readback_projection']['sensitivity_probe'] and
            'NOT released wholesale' in vl0['whitelisted_words'] and
            '0x12832C' in vl0['sdk_naming'],
            'the route-CW jl_audio_dac_vl0_registers must record exactly the one executed '
            'write-back 0x0 (the both-channels call composes clamped 0 into both '
            'halfwords; every bit carried from the synthetic initial read is erased by '
            'the second block r3=r3.l(u)), one synthesized initial read (the pair load '
            'at 0x606db4c) and zero post-write reads (the getter/sync-walker/mute '
            'consumers live in the audio-task domain after D1); the report must carry '
            'the synthetic-initial semantics, the sensitivity-probe result and the '
            'cap-slot erratum 0x12832C')
    vl0_assumption=r['peripherals']['assumptions'].get('jl_audio_dac_vl0_registers')
    require(vl0_assumption is not None and 'DAC_VL0' in vl0_assumption and
            'derived-not-measured' in vl0_assumption and
            'NOT verify any audio output' in vl0_assumption,
            'the route-CW jl_audio_dac_vl0_registers assumption must record the DAC_VL0 '
            'naming, the derived-not-measured semantics and the no-audio-output limit')
    # 路 CW/S19 批 0b:event_clock 接线(BI 首验观察项,只观察不硬断言)。
    # 路 B1(批 1)停点:零推进。路 B3(批 2 续)停点越过首个全核 IDLE——
    # ADDA 容器放行后 OS 等待族先跑忙等 µs 延时(61 窗全忙等形态不再成立),
    # 随后 W2[0] os_time_dly 停泊真实发生(BI 的 A1 形状部分显形),事件
    # 时钟首次真实推进 13,429,113 虚拟步(与主循环同一逐步 advance(),无
    # 批量跳步)。A2-A5 硬断言仍归后续批次,此处不硬断言。
    eclock=r['machine']['event_clock_projection']
    require(eclock['enabled'] and eclock['virtual_model_steps_advanced']==67174629,
            'the route-CW event-clock wiring must be enabled, and the audio-active '
            'steady state (SRC loop) advanced it 67,174,629 virtual model steps '
            'through the same per-model-step advance() as the run loop (the SRC '
            'completion IRQ89 re-entry keeps waking CPU0 out of IDLE between SRC '
            'reconfiguration rounds, so the park/wake cycling carries the event '
            'clock forward) — every advancement uses the same per-model-step '
            'advance() as the run loop with no bulk counter jumps')
    # 路 B1(S19 批 1):四件容器的本前缀形态断言——全部「落地但零运行态
    # 消费者」的诚实负例形态(BM §W4 预期的 trim 环/TIMER2 延时/canary
    # 首读分支在真实执行里未到达,世界先撞 BF core-tick 边界)。
    # ①TIMER2 投影:零写/零事务/零到期/零 CON 读/IWKUP 零写(锚点停泊核
    # 从未出现,机器侧唤醒列表空)。
    timer2=r['peripherals']['timer2_delay_projection']
    require(timer2['writes']==18 and len(timer2['transactions'])==3 and
            timer2['expirations']==3 and timer2['con_reads']==8 and
            timer2['prd']==200 and timer2['cnt']==0 and timer2['con']==0 and
            all(t['closed_form_steps']==4000 for t in timer2['transactions']) and
            timer2['lrc_nominal_hz']==200000 and
            'wake_source_unverified' in timer2['readback_semantics'] and
            'derived-not-measured' in timer2['assumption'] and
            'no TIMER2 IRQ6 delivery' in timer2['assumption'],
            'the route-B1 timer2_delay_projection is now CONSUMED on this prefix '
            '(route JLEQ steady state): the TIMER2 helper 0x12cd26 is reached, three '
            'delay transactions run to expiry (PRD=200, closed-form 4000 model steps '
            'each — 200 LRC ticks at the nominal 200kHz over the 4-ticks-per-step '
            'reference), 18 writes and 8 CON reads — the anchored-poll wakes below '
            'are the machine-side counterpart; the container semantics (LRC nominal '
            'Hz derived-not-measured, the wake flagged wake_source_unverified, no '
            'IRQ6 delivery) stay as landed')
    timer2_wakes=r['machine']['timer2_poll_wake_projection']
    require(timer2_wakes['enabled'] and len(timer2_wakes['wakes'])==3 and
            all(w['anchor']=='0x12cd76' and w['core']==0 and w['pc_after']=='0x12cd78'
                for w in timer2_wakes['wakes']) and
            timer2_wakes['wake_source_unverified'] and
            '0x12cd76' in timer2_wakes['anchor'],
            'the machine-side TIMER2 anchored-poll wake projection must be enabled '
            'with the 0x12cd76 anchor and exactly the two steady-state wakes of '
            'the parked CPU0 poll head (route JLEQ: the route-B1 EMPTY negative '
            'form is superseded — the anchored poll loop now really parks and is '
            'woken by the closed-form expirations; the idle-wake consuming IDLE '
            'reshapes the poll-head cycling, leaving two wakes)')
    # ②VLVD canary:canary 树(0x12cc40 起)零访问——两地址零写零 RMW,首读
    # fixture 零服务(fixture 只在首读发生时服务,负例形态=报告在而读为零)。
    canary=r['peripherals']['p33_canary_projection']
    require(canary['word_writes']==2 and
            canary['stores']=={'0xf2c044':{'values':['0x0','0x0'],'writes':2}} and
            canary['rmw_records']['0xf2c044']['writes']==2 and
            canary['first_read_fixture']=={'0xf2c044':{'word':'0x0','reads_served':1}} and
            canary['fixture_is_synthetic_assumption'] and
            canary['records_write_accesses_only'] and
            canary['readback_not_modeled_except_first_read_fixture'] and
            '0x12cc40' in canary['observed_shapes'],
            'the p33_canary projection must record the CONSUMED canary form '
            '(route JLEQ: the canary tree from 0x12cc40 is reached on the steady-'
            'state path — one first read served by the synthetic fixture, two '
            'rmw.and.nmodimm clears of P3_VLVD_CON 0xf2c044 with operand-0 result, '
            'P3_PCNT_CON 0xf2c124 still untouched — the route-B1 negative form is '
            'superseded), and the fixture/assumption flags stay as landed')
    # ③PVDD 容器:路 JLEQ 稳态真实消费——三字离开合成初始 0(实际指令结果)。
    pvdd_regs=r['peripherals']['registers']
    require(pvdd_regs['0xf2c274']=='0xe' and pvdd_regs['0xf2c244']=='0x0' and
            pvdd_regs['0xf2c10c']=='0x78' and
            'NOT a reset-value or analog-state claim' in
            r['peripherals']['assumptions']['jl_pvdd_trim_writes'],
            'the jl_pvdd_trim_writes container words must hold the really-written '
            'steady-state values (route JLEQ: the BM-expected trim/W4[4] loop now '
            'runs — P3_ANA_CON13 0xf2c274=0xe, P3_ANA_CON1 0xf2c244=0x0, '
            'P3_PVDD0_AUTO 0xf2c10c=0x78, every value a real instruction result), '
            'and the assumption must keep the NOT-a-reset-value limit')
    # ④DAC_COP write-only 记录:恰两条写 0x7f(路 CW 停点那条指令本尊
    # 0x606ab2c+路 JLA 段到达的第二写点 0x606e038——ADC 启动函数 0x606df66
    # 的 st.w.modimm),零读零 RMW。
    dac_cop=r['peripherals']['jl_audio_dac_cop_write_records']
    require(dac_cop['stores']=={'0xfe0534':{'values':['0x7f','0x7f'],'writes':2,'cores':[0,0]}} and
            dac_cop['word_writes']==2 and
            dac_cop['addresses']==['0xfe0534'] and
            dac_cop['write_only'] and dac_cop['readback_not_modeled'] and
            '0x606ab2c' in dac_cop['observed_shapes'] and
            '0x606e038' in dac_cop['observed_shapes'] and
            'NOT verify any audio output' in
            r['peripherals']['assumptions']['jl_audio_dac_cop_write_records'],
            'the route-B1 jl_audio_dac_cop_write_records must record exactly the two '
            'executed whole-word stores 0x7f (the former route-CW stop instruction '
            '0x606ab2c itself plus the route-JLA-reached second all-write site '
            '0x606e038 in the ADC start function 0x606df66); every read/RMW '
            'stays strictly stopped')
    # ⑤DAC DMA 四字 write-only 记录(尾段二次勘误件):恰四写,ADR=0x18e180
    # 与路 CW 的 60KB memset 目的地同址互证。
    dac_dma=r['peripherals']['jl_audio_dac_dma_write_records']
    require(dac_dma['stores']=={'0xfe0504':{'values':['0x18e180'],'writes':1,'cores':[0]},
                                '0xfe0508':{'values':['0x1'],'writes':1,'cores':[0]},
                                '0xfe0510':{'values':['0x0'],'writes':1,'cores':[0]},
                                '0xfe0514':{'values':['0x0'],'writes':1,'cores':[0]}} and
            dac_dma['word_writes']==4 and
            dac_dma['addresses']==['0xfe0504','0xfe0508','0xfe0510','0xfe0514'] and
            dac_dma['write_only'] and dac_dma['readback_not_modeled'] and
            '0x6071e92' in dac_dma['observed_shapes'] and
            '0x18e180' in dac_dma['observed_shapes'] and
            'NOT verify any audio output' in
            r['peripherals']['assumptions']['jl_audio_dac_dma_write_records'],
            'the route-B1 jl_audio_dac_dma_write_records must record exactly the four '
            'executed DAC DMA configuration stores of the W1[3]-tail start function '
            '0x6071e74 (0x6071e92..0x6071ea8: ADR 0x18e180 — the very 60KB buffer the '
            'route-CW ROM memset cleared — LEN/enable 1, and the two zero words), with '
            'every read/RMW strictly stopped')
    # 路 AK/AS:JL_WL_AUD 写域记录断言——本前缀恰两条写:S0 的 0x602ea72
    # 整字 1(entry 使能)与 S2 的 0x6071cbe 整字 1(DAC init 尾,r10=1 形状;
    # 路 AS 放行 ADC reset 后到达)。读回计数 0(唯一 app 读点 0x6069a2e
    # 低功耗快照不在本前缀;合成 0 分支无消费者)。
    wl_aud=r['peripherals']['jl_wl_aud_write_records']
    require(wl_aud['stores']=={'0xfe2200':{'values':['0x1','0x1'],'writes':2,'cores':[0,0]}} and
            wl_aud['word_writes']==2 and
            wl_aud['addresses']==['0xfe2200'] and
            wl_aud['readback_projection']['reads']==0 and
            wl_aud['readback_projection']['synthesized_initial_reads']==0 and
            'derived-not-measured' in wl_aud['readback_projection']['readback_semantics'] and
            'NOT a reset-value claim' in wl_aud['readback_projection']['readback_semantics'],
            'the route-AK jl_wl_aud_write_records must record exactly the two executed '
            'JL_WL_AUD->CON0 writes: 0x602ea72 entry st.w.modimm=1 and the route-AS-reached '
            'DAC-init tail 0x6071cbe st.w.l=r10 (=1, the SR-index value); the readback '
            'counters stay zero (the sole app reader 0x6069a2e is outside this prefix, the '
            'synthetic-zero branch has no consumer)')
    wl_aud_assumption=r['peripherals']['assumptions'].get('jl_wl_aud_write_records')
    require(wl_aud_assumption is not None and 'JL_WL_AUD' in wl_aud_assumption and
            'derived-not-measured' in wl_aud_assumption and
            'NOT verify any audio or wireless-audio function' in wl_aud_assumption,
            'the route-AK jl_wl_aud_write_records assumption must record the JL_WL_AUD '
            'naming, the derived-not-measured semantics and the no-audio-function limit')
    # 路 AS:ADC 节点 reset 写域断言——恰一条写:0x60719c0 并行对第二半以
    # 旧 r3 写 [0xfe0538]=0;路 LPV 尾段 set_buffer 链到达后恰一读(写后,
    # AE 清单里的 bit4 double-check 0x606def0 族在新停点函数内被真实执行),
    # 合成初始读 0(读发生在写之后,只走 last-write 分支)。
    adc_reset=r['peripherals']['jl_audio_adc_reset_write_records']
    require(adc_reset['stores']=={'0xfe0538':{'values':['0x0','0x8000','0x8000','0x18000',
                                                     '0x18800','0x18800','0x18804','0x18824',
                                                     '0x18834','0x18934','0x10934','0x10904'],
                                     'writes':12,'cores':[0]*12}} and
            adc_reset['word_writes']==12 and
            adc_reset['addresses']==['0xfe0538'] and
            adc_reset['readback_projection']['reads']==12 and
            adc_reset['readback_projection']['synthesized_initial_reads']==0 and
            'derived-not-measured' in adc_reset['readback_projection']['readback_semantics'] and
            'NOT a reset-value claim' in adc_reset['readback_projection']['readback_semantics'] and
            'parallel pair' in adc_reset['observed_shapes'] and
            '0x127801' in adc_reset['whitelisted_words'],
            'the route-AS jl_audio_adc_reset_write_records must record exactly the one '
            'executed ADC_CON reset write — the second slot of the parallel pair at '
            '0x60719c0 storing the OLD r3 (0xfe0538) with r4=0 — and exactly ONE '
            'post-write read (the route-AE bit4 set_buffer double-check family '
            '0x606def0 inside the new stop function, reached by the route-LPV tail '
            'segment; zero synthesized initial reads, the last-write branch serves it); '
            'the remaining route-AE consumers stay on the IRQ68-delivery/esco-open '
            'paths outside this prefix')
    adc_reset_assumption=r['peripherals']['assumptions'].get('jl_audio_adc_reset_write_records')
    require(adc_reset_assumption is not None and 'ADC_CON' in adc_reset_assumption and
            'derived-not-measured' in adc_reset_assumption and
            'NOT verify any audio input' in adc_reset_assumption,
            'the route-AS jl_audio_adc_reset_write_records assumption must record the '
            'ADC_CON naming, the derived-not-measured semantics and the no-audio-input limit')
    # 路 CV:JL_ADDA DAA_CON1 两开关容器断言——恰一条写回(fixture=0x0 探针下
    # RMW 合成值 0x0:bits0..2/bits4..6←表[0]=0,保留位全零侧),fixture 读恰
    # 一次(0x606b426 首读),写后零读(下一停点 0x606db4c 在任何 DAA_CON1
    # 回读之前)。敏感性对照(ones 探针 0xfffffff8:同 +17 步同停点、写回
    # 0xffffff88、同 fixture/回读计数)=观察路径对初值不敏感,证据表在
    # docs/adda-daa-con1.md §3。
    daa_con1=r['peripherals']['jl_adda_daa_con1_registers']
    require(daa_con1['stores']=={'0xfe0a04':{'values':['0x0','0x7','0x77','0x77','0x77',
                                                     '0x77','0x77','0x73','0x33'],
                                              'writes':9,'cores':[0]*9}} and
            daa_con1['word_writes']==9 and
            daa_con1['addresses']==['0xfe0a04'] and
            daa_con1['fixture']['initial_word']=='0x0' and
            daa_con1['fixture']['label']=='sensitivity-probe-not-plausible-state' and
            daa_con1['readback_projection']['fixture_reads']==1 and
            daa_con1['readback_projection']['reads_after_write']==8 and
            'NOT a reset-value claim' in daa_con1['readback_projection']['readback_semantics'] and
            'no implicit zero default' in daa_con1['readback_projection']['readback_semantics'] and
            'persists in the register state' in daa_con1['readback_projection']['preserved_bits_note'] and
            '0x060a8608' in daa_con1['sdk_naming'] and
            'NOT released wholesale' in daa_con1['whitelisted_words'],
            'the route-CV jl_adda_daa_con1_registers must record the probe-0 write-back 0x0 '
            '(table code 0 into bits0..2 and bits4..6, reserved bits all-zero side) PLUS the '
            'route-B3 audio-analog-init tail re-programming the gain word four more times '
            '(0x7 then 0x77 three times) through the SAME config-RMW fixer 0x606b40e, one '
            'fixture read (the 0x606b426 first read) and four post-write reads (the fixer '
            're-reads DAA_CON1 inside its later calls — still no poll: one read per call, '
            'the volume-sync walker stays outside); the fixture must carry the '
            'sensitivity-probe label and the report the preserved-bits persistence note')
    daa_con1_assumption=r['peripherals']['assumptions'].get('jl_adda_daa_con1_registers')
    require(daa_con1_assumption is not None and 'DAA_CON1' in daa_con1_assumption and
            'sensitivity-probe-not-plausible-state' in daa_con1_assumption and
            'NOT verify any audio output' in daa_con1_assumption,
            'the route-CV jl_adda_daa_con1_registers assumption must record the DAA_CON1 '
            'naming, the sensitivity-probe label and the no-audio-output limit')
    # 路 AK/AS:音频 DAC init 写域——S1..S5 已在路 AS 放行后全部落地:
    # DAC_CON×3(S1 整字 0x0=并行对旧 r1 的 SR walk 值,S4 &=~0x100 写回
    # 0x0,尾 |=0x100 → 0x100)、AUD_CON×1(0x0)、CLK_CON×1(0x1);RMW 内部
    # 读 4 次(写后 2:DAC_CON RMW 对;合成 0 两次:CLK_CON/AUD_CON 首读)。
    # (S1 值勘误:AG 图「=0xfe2200 数值巧合」是并行对误读,0xfe2200 是 S2
    # 的写基址——假设文本保留该措辞但已改为「误读勘误」叙述。)
    # 路 CW(S19 批 0b)新前缀再 +6 写/6 读:0x606ab0a 输出级门控函数的
    # AUD_CON|=0x100(0x100|0x100=0x100 写回)+DAC_CON 五连 RMW
    # (|=0x100→0x100;clear-bit31→0x100 不变;|=0x8000000→0x8000100;
    # 清 bit26/bit25/bit24 三连→0x8000100 不变)——全部经 AK 容器(内部读
    # =已记录末值,写回落记录)。
    # 路 B1(S19 批 1)新前缀再 +8 写/8 读(全 DAC_CON):DAC 启动函数
    # 0x6071e74 尾段在 DAC_COP/DMA 四写放行后继续——0x6071eb8 起的
    # [r10+0]|=0x110 AK 服务链与后续位域配置 RMW,写回序列 0x8000100(再
    # 一条)、0x8100100×2、0x8350100×3、0x835e100、0x835e110(末值)。
    audio_init=r['peripherals']['jl_audio_dac_init_write_records']
    require(audio_init['stores']=={'0xfe0500':{'values':['0x8000100','0x8100100',
                                                        '0x8100100','0x8350100','0x8350100',
                                                        '0x8350100','0x835e100',
                                                        '0x835e110','0x835e110','0x835e110',
                                                        '0x835e110','0x835e110','0x835e110',
                                                        '0x835e110','0x835e110','0x835e110'],
                                                'writes':24,
                                                'cores':[0]*16},
                                   '0xfe0558':{'values':['0x0','0x100','0x100'],'writes':3,'cores':[0,0,0]},
                                   '0xfe0900':{'values':['0x1'],'writes':1,'cores':[0]}} and
            audio_init['word_writes']==28 and
            audio_init['addresses']==['0xfe0500','0xfe0558','0xfe0900'] and
            audio_init['readback_projection']['reads']==25 and
            audio_init['readback_projection']['synthesized_initial_reads']==2 and
            'synthetic initial word 0' in audio_init['readback_projection']['readback_semantics'] and
            'derived-not-measured' in audio_init['readback_projection']['readback_semantics'],
            'the route-AK jl_audio_dac_init_write_records must hold the five executed '
            'DAC-init writes of the route-AS-reached S1..S5 cluster (DAC_CON three stores: '
            'the 0x6071cbc whole-word = the parallel-pair OLD r1, the SR-walk value 0x0 — '
            'route-AS correction of the AG-map 0xfe2200 misread; then the &=~0x100 '
            'write-back 0x0 and the |=0x100 tail 0x100; AUD_CON one, &~0x40 on the '
            'synthetic 0 -> 0x0; JL_ASS CLK_CON one, |=1 on the synthetic 0 -> 0x1) '
            'PLUS the six route-CW writes of the DAC-output gate function 0x606ab0a '
            '(AUD_CON |=0x100 -> 0x100 and the five DAC_CON RMW write-backs '
            '0x100/0x8000100x4: set bit27, clear bit31/26/25/24 — all served by the '
            'container readback of the recorded last word) PLUS the eight route-B1 '
            'DAC_CON write-backs of the released DAC start tail 0x6071e74 (from the '
            '0x6071eb8 [r10+0] AK-service store onward, the bit-field configuration '
            'RMWs walking the recorded container value on to the final 0x835e110), '
            'PLUS the route-JLA AUD_CON bit4-clear double-check RMW write-back '
            '0x100 (0x606e03c, &=~0x10 on the recorded 0x100 right after the '
            'set_buffer five writes complete — the same double-check family the '
            'set_buffer entry read of ADC_CON performs), '
            'with the RMW internal reads split 17 after-write and 2 synthetic-zero '
            '(CLK_CON/AUD_CON first touch)')
    audio_init_assumption=r['peripherals']['assumptions'].get('jl_audio_dac_init_write_records')
    require(audio_init_assumption is not None and 'DAC_CON 0xfe0500' in audio_init_assumption and
            'numeric coincidence' in audio_init_assumption and
            'NOT verify any audio output' in audio_init_assumption,
            'the route-AK jl_audio_dac_init_write_records assumption must record the '
            'three-word whitelist, the route-AS correction of the 0xfe2200 '
            'numeric-coincidence parallel-pair misread and the no-audio-output limit')
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
    require(dcck['0xfd2000']=='0x346f' and dcck['0xfd2100']=='0x0' and dcck['0xfd2200']=='0x0',
            'the jl_uart_con_projection must leave UART0 CON0|CON1 at 0x346f (the uart_init '
            '0x3400 store plus the route-AA tail RMWs |=0x68/|=0x4 of the RX-DMA enable and '
            'the second-baudrate &=~3/|=3 pair) and UART1/UART2 CON words at the untouched '
            'synthetic 0 (only UART0 is initialized in this prefix)')
    # 路 M(sim-uartwr)+路 AA(sim-con1rb):初始化写白名单的记录断言。路 AA
    # 放行 CON1 读回后 uart_init 事务收尾,本前缀实际落地:CON1 两写(0x603446c
    # 整字 0 + fifo_reset 复合 RMW 0x60340d8 的写回 0)、BAUD 两写 0x270(两次
    # baudrate)、OTCNT=0x1e834、RX 三连写 RXSADR=0x125bfc/RXEADR=0x125e1c/
    # RXCNT=0x220(环形缓冲 544 字节=0x220,与路 U 前瞻的 r10=544 互证)、
    # CON2=0;con1_readback_projection 子键恰 1 读(RMW 内部读)、报告顶层
    # write_only/readback_not_modeled 翻 False(fd31 路 BN 同款)。TXADR/TXCNT/
    # BUF/HRXCNT 仍零写(TX 路径未走,白名单守卫不变)。
    jl_init=r['peripherals']['jl_uart_init_write_records']
    require(jl_init['stores']=={'0xfd2004':{'values':['0x0','0x0'],'writes':2},
                                '0xfd2008':{'values':['0x270','0x270'],'writes':2},
                                '0xfd2010':{'values':['0x1e834','0x28af0','0xc348','0xc348'],'writes':4},
                                '0xfd201c':{'values':['0x125bfc'],'writes':1},
                                '0xfd2020':{'values':['0x125e1c'],'writes':1},
                                '0xfd2024':{'values':['0x220'],'writes':1},
                                '0xfd202c':{'values':['0x0'],'writes':1}} and
            jl_init['word_writes']==12 and
            jl_init['con1_readback_projection']['reads']==1 and
            jl_init['con1_readback_projection']['addresses']==['0xfd2004','0xfd2104','0xfd2204'] and
            not jl_init['write_only'] and not jl_init['readback_not_modeled'] and
            '0x60340d8' in jl_init['con1_readback_projection']['conditional_assumption'] and
            'derived-not-measured' in jl_init['con1_readback_projection']['conditional_assumption'],
            'the route-M/AA jl_uart_init_write_records must record the completed uart_init '
            'transaction: CON1 twice (0x603446c whole-word 0 plus the fifo_reset RMW '
            'write-back 0 served by the con1 readback projection — exactly one projected '
            'read), BAUD twice 0x270 (two baudrate calls), OTCNT four times '
            '0x1e834/0x28af0/0xc348/0xc348 (the audio-active steady state reprograms '
            'the RX timeout count — route JLEQ plus the SRC loop), the RX '
            'SADR/EADR/CNT triple 0x125bfc/0x125e1c/0x220 and '
            'CON2 0; TXADR/TXCNT/BUF/HRXCNT stay unwritten (the TX path never runs, '
            'whitelist guard unchanged)')
    jl_init_assumption=r['peripherals']['assumptions'].get('jl_uart_init_write_records')
    require(jl_init_assumption is not None and 'CON1 word' in jl_init_assumption and
            'derived-not-measured' in jl_init_assumption and
            'NOT verified UART transmission/reception' in jl_init_assumption,
            'the route-M jl_uart_init_write_records assumption must record the {i32 x12} '
            'layout pin-down (0xfd2004 = CON1, route-F naming corrected), the '
            'derived-not-measured write records and the no-UART-traffic scope limit')
    # 路 AA(sim-con1rb):CON1 读回投影的假设键——条件假设(读回=软件配置值,
    # __RW 不能证明无硬件自更新位)、RMW 完整路径、只扩 CON1 的范围边界、
    # 首个消费者 0x60340d8 与 derived-not-measured 必须全文进 assumptions。
    con1_assumption=r['peripherals']['assumptions'].get('jl_uart_con1_readback')
    require(con1_assumption is not None and '0x60340d8' in con1_assumption and
            'derived-not-measured' in con1_assumption and
            'ONLY CON1' in con1_assumption and
            'NOT verified UART transmission/reception' in con1_assumption and
            'hardware-updated or self-clearing bits' in con1_assumption,
            'the route-AA jl_uart_con1_readback assumption must record the conditional '
            'hypothesis (readback == the software-configured value; __RW cannot prove the '
            'absence of hardware-updated or self-clearing bits), the full generic-MMIO RMW '
            'path with the recorded write-back, the ONLY-CON1 scope and the '
            'no-UART-traffic limit')
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
    # 路 V(sim-imapwr):JL_IMAP 白名单的记录断言——本前缀恰一条写:
    # 0x6001342 的 0xfd5c90=0x2f(core 0)。识别三方交叉(io_imap.h 成员表
    # 63 成员+USBDP_IN=47、csfr.h __u8→u32 的 4 字步进+gpio_hw.c.o PFI_*
    # 枚举 16604160..16604408 逐成员互证、SDK gpio2crossbar_inport 与固件
    # resolver 0x60012e8 双侧同值且通道号表 gpio_in_regs 与固件表 0x609EAB8
    # 逐字节一致)必须进 assumption;63 字白名单钉死;全镜像扫描零读回。
    imap=r['peripherals']['jl_imap_write_records']
    require(imap['stores']=={'0xfd5c90':{'values':['0x2f'],'writes':1,'cores':[0]}} and
            imap['word_writes']==1 and len(imap['addresses'])==63 and
            imap['write_only'] and imap['readback_not_modeled'] and
            imap['addresses'][0]=='0xfd5c00' and imap['addresses'][-1]=='0xfd5cf8',
            'the route-V jl_imap_write_records must record exactly the one executed JL_IMAP '
            'input-mux write (0x6001342, FI_UART0_RX 0xfd5c90 = USBDP_IN 0x2f, core 0) over '
            'the declared 63-word bank FI_GP_ICH0..FI_CHAIN_RST (0xfd5c00..0xfd5cf8, the '
            'csfr.h __u8->unsigned-int 4-byte stride); no other input-mux word is written '
            'in this prefix')
    imap_assumption=r['peripherals']['assumptions'].get('jl_imap_write_records')
    require(imap_assumption is not None and 'gpio2crossbar_inport' in imap_assumption and
            '0x609EAB8' in imap_assumption and 'USBDP_IN' in imap_assumption and
            'derived-not-measured' in imap_assumption and
            'NOT verified UART transmission/reception or USB function' in imap_assumption,
            'the route-V jl_imap_write_records assumption must record the three-way '
            'identification (io_imap.h 63-member bank, the PFI_* enumerators at the 4-byte '
            'stride, the gpio2crossbar_inport == firmware resolver 0x60012e8 agreement with '
            'the byte-identical channel table 0x609EAB8), the derived-not-measured write '
            'records and the no-traffic scope limit')
    fd31=r['peripherals']['fd31_adc_con_projection']
    require(fd31['stores']=={'0xfd3100':{'values':['0x46105e','0x20000','0x20040','0x40',
                                                  '0x46105e','0x46105e','0x20000',
                                                  '0x20040','0x40','0x46105e','0x46105e',
                                                  '0x20000','0x20040','0x40','0x46107e',
                                                  '0x46107e'],
                                  'writes':3128}} and
            fd31['word_writes']==3128 and
            not fd31['write_only'] and not fd31['readback_not_modeled'] and
            fd31['addresses']==['0xfd3100'] and
            'NOT 0x200' in fd31['observed_shapes'] and 'JL_ADC' in fd31['sdk_naming'] and
            fd31['con_readback_projection']['reads']==15181 and
            fd31['con_readback_projection']['address']=='0xfd3100' and
            fd31['con_readback_projection']['res_boundary']['address']=='0xfd3104' and
            fd31['pnd_completion_projection']['forced_reads']==13869 and
            fd31['pnd_completion_projection']['bit']=='0x80' and
            fd31['pnd_completion_projection']['not_a_timing_model'] and
            fd31['res_named_values_fixture']['served']=={'0x10000':368,'0x10004':12,
                                                         '0x10006':36,'0x1000d':180,
                                                         '0x1000e':12} and
            fd31['res_named_values_fixture']['values']=={'0x10000':400,'0x10001':4,
                                                         '0x10004':400,'0x10005':400,
                                                         '0x10006':500,'0x1000d':350,
                                                         '0x1000e':550} and
            fd31['res_named_values_fixture']['derived_not_measured'],
            'the fd31 ADC CON projection must record 3128 word writes (the audio-'
            'active steady-state (SRC loop) round engine: the same round shape — '
            'setter 0x46105e/0x46107e (the steady-state setter form carries bit0), '
            'control 0x20000/0x20040/0x40 — cycling through the route-JLEQ prefix '
            'and on through the SRC loop), 15181 projected CON reads and 13869 '
            'folded PND polls at the instruction_budget stop (the RES served '
            'counts stay frozen — the steady-state rounds read CON/PND only), '
            'while the RES fixture serves the mux-keyed reads '
            '368+12+36 on the legacy channels plus the route-JLEQ TWVD/PVDD rows '
            '0x1000d=350 (180 served) and 0x1000e=550 (12 served); the key/battery '
            'rows 0x10005/0x10001 stay in the value table with ZERO served reads '
            '(their consumers sit beyond this stop)')
    # P3_ANA_CON4(0xf2c250):末值 0xed=稳态轮换收在 VBAT_4 域(域6;
    # WBG04 轮=0xE1、VBAT_4 轮=0xED、VTEMP 轮=0xE9——bit5-7 使能位保持,
    # 域值随通道切换,与 0x6067d8a 的 idx==0/idx!=0 两形状互证;路 JLEQ
    # 稳态轮换把末值推到 0xED)。
    require(r['peripherals']['registers']['0xf2c250']=='0xe1',
            'P3_ANA_CON4 must end at 0xed: the steady-state channel rotation (route JLEQ) '
            'lands on the VBAT_4 domain (mux field 6 with the kept enable bits 0x20/0x40/'
            '0x80 and bit0), rotating past the route-B3 idx==0 form 0xe1 and the VTEMP '
            'form 0xe9 of the shorter stops')
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
    # 路 V(sim-imapwr):+516 步无新到期无新交付(287/574/287 全不变;新停点
    # 在 OS unmask 临界区内但 IPMASK 只挡新交付,无 pending 在途)。
    # 路 AA(sim-con1rb):+5,323 步仍无新到期无新交付(287/574/287 不变;
    # cnt 21,030<36,000;**UART IRQ12 使能后零 pending 零请求**——模型无
    # UART RX 源,ICU 使能只是资格登记,新前缀交付全是既有家族)。
    # 路 AN(sim-crosspage):跨页拆分放行后场景 walker 链 +56,572 步里
    # Timer0 到期 297→305(+8)、交付 287→295(+8,全部 ACK、零在途);新交付
    # 的 reti_from 首次离开延时循环/OS veneer 两族——0x6010a4e/0x6010a5c
    # (config/init 区延时)、0x6007b7e×2(查名 helper 尾部 {pc,r9-r4} 弹栈后
    # 的 delay 调用点)、0x4372(ROM delay 族)、0x6000f4a(CRC wrapper 区)、
    # 0x12b608×2(调度器等待);0x12b7b6 仍恰 2 次且不再是最后一次。PRD 被
    # 新一轮 delay 重编为 11999(1ms 窗),cnt 走到 10,635。
    # 路 AS(sim-adcreset):ADC reset 放行后 +32,789 步里 Timer0 交付 295→299
    # (+4:0x12b608 调度器等待、0x6000f4c/0x6000f4a CRC wrapper 区、**0x4196
    # =ROM 0x4180 rep 拷贝块出口**——第 5 次到期(3,139,140)落在 rep 体内,
    # 分发层按路 CB 延期先例扩到指令中途态挂起 30 次后、在块尾指令边界交付
    # (reti_from=块出口,延期-交付往返实证;机器守卫本身保持拒绝))。core
    # tick 交付 9→10(+1 次溢出,OS tick 级联继续)。
    t0=r['machine']['timer0_irq_entry_projection']
    require(len(t0['deliveries'])==14344 and
            all((d['core'],d['irq'],d['vector_address'],d['handler'])==(0,4,'0x19fe10','0x12ece8')
                for d in t0['deliveries']) and
            all(d['reti_from'] in ('0x12b608','0x12c5cc','0x12e282','0x12e284','0x12e286',
                                   '0x12e2a2','0x12e2a4','0x12e2a6','0x12e2aa','0x12cd78',
                                   '0x417e','0x4196','0x4246','0x5c4c','0x5c58','0x5fa8',
                                   '0x6000f32','0x6000f34','0x6000f48','0x6000f4a','0x6000f4c',
                                   '0x6000f4e','0x6000f52','0x600a65c','0x6010a60','0x6010a82',
                                   '0x6010a92','0x6010ace','0x6010ec4','0x6011778','0x6011788',
                                   '0x601baf8','0x60608a8','0x606454a','0x6064752','0x6064c4a',
                                   '0x6065538','0x6070766','0x607076e','0x607377a','0x6073790',
                                   '0x60737b2','0x60742ae','0x60743aa','0x6074540','0x6086c8c',
                                   '0xafc6','0x12b2de',
                                   '0x12c534','0x12c538','0x12c5a0','0x12c5ce',
                                   '0x6064340','0x6064348','0x6064db8','0x6064dc8','0x6064dce',
                                   '0x6064d4e','0x6065532','0x6065536',
                                   '0x60705dc','0x6070604','0x607062c','0x607063a','0x6070658',
                                   '0x6070696','0x60706f8','0x60706fc','0x6070720','0x6070764',
                                   '0x6070770','0x6070788','0x6073766','0x6073772')
                for d in t0['deliveries']) and
            sum(1 for d in t0['deliveries'] if d['reti_from']=='0x12c5cc')==8435 and
            sum(1 for d in t0['deliveries'] if d['reti_from']=='0x12b608')==3875 and
            t0['deliveries'][-1]['reti_from']=='0x12b608' and
            all(d['pending_cleared_by_handler_ack'] and not d['delivery_clears_pending']
                for d in t0['deliveries']) and
            r['instruction_adapters'].get('timer0_irq_handler_frame')==28688 and
            r['instruction_adapters'].get('conditional_timer0_irq_rti')==14344,
            '14344 real Timer0 IRQ4 delivery round trips in the audio-active steady '
            'state (SRC loop, dominated by idle-wakes reti_from=0x12c5cc as the SRC '
            'completion IRQ89 re-entry keeps waking CPU0 out of IDLE between '
            'reconfiguration rounds): the first 362 are the historical '
            'route-AK/AN/AS/B1 prefix (287 through the AK stop, the route-R delivery '
            'interrupting the OS veneer 0x12b7b6, the eight route-AN walker-chain '
            'contexts, the route-AS ROM rep-block exit 0x4196 and the sixty-one B1 '
            'delay-trio deliveries — pinned verbatim at [287:362]); the route-B3 OS-wait '
            'prefix adds 3029 more, dominated by the parked-wake shape reti_from=0x12c5ca '
            '(1557: the all-core IDLE parks are woken by Timer0 through the event clock) '
            'plus the delay trio continuing (0x12e282/0x12e284/0x12e286) and ONE delivery '
            'interrupting the audio analog-init body itself (0x606b0a2, inside '
            '0x606abf0..0x606b20e); the route-DT segment adds 279 more, ALL inside the '
            'delay trio (no new interrupt context, the parked-wake count stays 1557); '
            'the route-LPV tail segment adds ONE more delivery interrupting the ROM '
            'memset tail 0x4246 (the buffer-config walk of the new 2,068-step segment); '
            'the route-JLA segment (set_buffer five writes + handler-hang + start '
            'prologue, +130 steps) adds ZERO deliveries (no new Timer0 expiry window) '
            '— '
            'every delivery reads the real vector slot 0x19fe10 '
            'and clears pending only via the real handler W1C; 3671 symmetric RTIs '
            'complete (frame 8802 = 2x4401, nothing in flight at the 28,764,184 stop, '
            'the last delivery having interrupted the scheduler wait 0x12b608; the '
            'route-ASS segment adds 730 more deliveries — the delay trio grows to '
            '933/925/934 and the 0x6000f48 context appears ten times inside the '
            'audio pipeline re-init rounds, parked-wake 0x12c5ca stays 1557); the '
            'route-JLEQ steady state (park evidence body) adds 74 more deliveries '
            'carrying the parked-wake count to 1791 — the retained window now shows '
            'the steady-state shapes: the TIMER2 second delay window (0x12e2a2/a4/a6/'
            'ac/aa), the anchored-poll wakes 0x12cd76 x3, the MAC/key-domain contexts '
            '0x6011774/0x6000d6c, one delivery interrupting the key-table compression '
            'loop 0x6011f18 (the round-8 stop shape upstream), then the long '
            'parked-wake tail 0x12c5ca; the route-WDFEED park extension walks the '
            'count on to 15757 with 12791 parked wakes; the route-STOREG3 '
            'consumption-chain walk adds exactly ONE more delivery interrupting the '
            'ROM rep-block exit 0x4196 again (the S18 dispatch calls the ROM copy '
            'blocks, the route-AS mid-block expiry shape recurring) — 15758 total, '
            'parked-wake count still 12791; the route-TONMAP release (official '
            'TON/TON1 containers mapped read-only) adds three more deliveries '
            '15759..15761 through the resource-VFS open chain, the new task '
            'installations and the event-loop prefix — all three interrupting '
            'contexts already in the known set (no new interrupt context), the '
            'final one interrupting the scheduler wait 0x12b608 — 15761 total, '
            'parked-wake count still 12791; the route-EVIMPL release (the '
            'jlstream task-stack 4B slot unknown-word pass, +56377 steps '
            'through the first message dispatch and into the audio DAC DMA '
            'run domain) adds five more deliveries 15762..15766 — two '
            'interrupting the scheduler wait 0x12b608, one the name-lookup '
            'helper prologue store 0x6007af2 (the historical route-AK stop '
            'shape recurring as an interrupted PC), another scheduler wait, '
            'and the LAST one interrupting 0x6074614 (the ld.hu.sp.l inside '
            'the jlstream kick/param function family, right beside the '
            'released load) — 15766 total, parked-wake count still 12791; '
            'the route-DMAIMPL audio-pipeline segment walks twelve more '
            'deliveries on to 15778 (known contexts only) and the route-'
            'COPEXIMPL release (the mode-22 exponential pass, +21707 steps '
            'of eq coefficient design into the SRC_SYNC reconfiguration) '
            'adds two more deliveries 15779..15780 — one interrupting '
            '0x6064754 (the eq open-config caller domain, the event-loop '
            'dispatch call) and the LAST one interrupting 0x60745fa (a call '
            'inside the jlstream kick/param function family, the EVIMPL '
            'context 0x6074614 next door) — 15780 total, parked-wake count '
            'still 12791; the route-FMIMPL release (the modem-window '
            'CON-head five-tuple RMW, +110,963 steps through the fn_config '
            'return, the framework table dispatch, the new 0x6086c8c task '
            'installation and the ROM byte-to-halfword expansion loop) adds '
            '22 more deliveries 15781..15802 with seven new interrupt '
            'contexts (the sdfile-walker third window 0x6010a5e/0x6010a7a/'
            '0x6010ede, the framework 0x606091e, the eq/jlstream node-family '
            'continuation 0x6074e44/0x6075192 and the ROM expansion-loop '
            'caller domain 0x732a — ten more scheduler waits 0x12b608 '
            'beside them) — 15802 total at the SEGIMPL3 stop, and the '
            'route-SEGIMPL4 release segment (+57,523 steps through the ten '
            'clearing-loop rounds and the long walk to the clamp REP) walks '
            'THIRTEEN more deliveries on to 15815 as the PRD 11999 window '
            'rolls over — ten interrupting ROM loop contexts (0x6c04/0x69dc/'
            '0x5b94/0x5bda/0x5c44/0x5d32/0x5ba2/0x5710/0x5bec/0x74f0) and '
            'three app jlstream/audio-family contexts (0x6087928/0x6086508/'
            '0x60864c8) — parked-wake count still 12791 '
            '(the segment runs CPU0 busy, no parked wake, event clock '
            'frozen); the audio-active steady state (SRC loop) then adds the '
            'SRC-loop deliveries (the new SRC-loop interrupt contexts in the '
            '0x6070xxx/0x6064xxx config/eq regions plus the 0x12c5xx idle-wake '
            'variants), carrying the reti_from=0x12c5cc count to 8435 and the '
            'total to 14344, the last delivery still reti_from the scheduler '
            'wait 0x12b608')
    ct=r['machine']['core_tick_entry_projection']
    require(len(ct['deliveries'])==23840 and
            (ct['deliveries'][0]['core'],ct['deliveries'][0]['vector_address'],
             ct['deliveries'][0]['handler'],ct['deliveries'][0]['reti_from'])==
            (0,'0x19fe0c','0x60661a4','0x12b608') and
            all((d['irq'],d['vector_address'],d['handler'])==(3,'0x19fe0c','0x60661a4')
                for d in ct['deliveries']) and
            sum(1 for d in ct['deliveries'] if d['core']==0)==23775 and
            sum(1 for d in ct['deliveries'] if d['core']==1)==65 and
            all(not d['delivery_clears_pending'] for d in ct['deliveries']) and
            r['instruction_adapters'].get('core_tick_handler_frame')==47680 and
            r['instruction_adapters'].get('conditional_core_tick_rti')==23840 and
            r['instruction_adapters'].get('core_tick_ack_rmw')==23840,
            'the CPU0 core tick must deliver its first overflow (the ~447,490 landmark) through '
            'the real IRQ3 vector slot 0x19fe0c to handler 0x60661a4, interrupting the '
            'delay-trio context 0x12e286 (the route-WDTFEED2 idle-wake reshape shifts the '
            'earliest deliveries off the old OS-veneer 0x12b7b6 onto the delay family), with pending cleared only by the handler ack; the '
            'route-CG/AS/B1 windows carry the count to eleven, and the route-B3 release '
            'delivers the remaining ninety-seven (overflows 12..108 all delivered, acked '
            'and RTI-complete — the delayed 12th overflow leaves its masked window when '
            'the critical section ends and every later overflow rides the same '
            'eligibility recheck); the route-DT segment adds eight more overflows, the '
            'route-JLEQ steady state carries the count to 147 and the route-GPCNUM '
            'extension to 200 and the route-WDFEED extension past the crossed '
            'watchdog point to 347: 347 deliveries, 347 acks, 347 '
            'symmetric RTIs; the audio-active SRC loop then carries the count on '
            'to 23775 CPU0 + 65 CPU1 deliveries at the instruction_budget stop '
            '(the idle-wake shape reti_from=0x12c5cc appears 17605 times and the '
            'delay family continues)')
    dfr=r['machine']['irq_delivery_deferral_projection']
    require(dfr['count']==581988 and
            all(e['irq'] in (3,4,127) and
                e['deferred_while'] in ('core_tick_handler','timer0_irq_handler',
                                        'software_irq_handler','src_sync_irq_handler',
                                        'mid_instruction (after_then/repeat in flight)')
                for e in dfr['events']) and
            sum(1 for e in dfr['events'] if e['irq']==3 and e['deferred_while']=='timer0_irq_handler')==0 and
            sum(1 for e in dfr['events'] if e['irq']==127 and e['deferred_while']=='core_tick_handler')==43,
            '581988 delivery requests must be HELD (not dropped) through the '
            '139,947,345 stop (11467 at the SEGIMPL stop, walking on by 91 through '
            'the SEGIMPL2/3/4 released segments — the SEGIMPL4 clearing-loop rounds '
            'and the long walk add more mid-instruction deferrals as the Timer0 '
            'PRD window rolls over; the audio-active steady state (SRC loop) adds '
            'many more deferrals as the SRC completion IRQ89 handler '
            'mutual-exclusion (src_sync_irq_handler) and the mid-instruction '
            'families join the window). The retained 64-event window is dominated by the mid-instruction '
            'family (Timer0 IRQ4 behind the route-STOREG3 '
            'consumption-chain walk: the S18 event dispatch calls the ROM delay rep '
            'bodies, so IRQ4 requests deferred to the instruction boundary dominate '
            'the window again — the route-AS/LPV shape returning on the longer '
            'prefix; the route-FMIMPL released segment rotates ten '
            'timer0-handler-mutual-exclusion members and one software-handler member '
            'back into the tail of the window — IRQ nesting stays unmodeled); each '
            're-requested by the next advance(), same '
            'held-not-dropped philosophy as the route-BF masked-pending re-checks')
    # 路 CG→JLEQ 续五/续七:GPCNT 读回投影——本前缀 disable 3/clear 4,配置
    # 字路径(config_words 开启后全字读回接管)151 读(位粒度部分值服务
    # 退场:reads=0)、整字写 3(W4[4] 重配置链,known 全字 0x460c)、
    # |=0x1 使能 1 次;completion 投影同样 151 读(读侧 OR 0x80)。稳态测量
    # 任务 0x6069538 每次调度的 bit0 使能门都走配置字读回;首次使能后的
    # bit7 轮询经 completion 投影第一读退出。路 GPCNUM 具名值放行后:
    # 20 次转换全服务(每转换 bit0 门+bit7 轮询走配置字读回共 249 读、
    # 组装整字写 3+3×20=63、重启 |=1 使能 1+20=21、cleanup 禁用/清
    # 3+19/4+2×19+1=22/43),终态 known_value=0x460d(末次重启 |=1 后);
    # NUM fixture 20 读,读回未证不变。
    gp=r['peripherals']['gpcnt_cleanup_projection']
    require(gp['known_mask']=='0xffffffff' and gp['known_value']=='0x460d' and
            gp['disable_writes']==249 and gp['clear_writes']==497 and
            gp['con_readback_projection']['reads']==0 and
            gp['con_readback_projection']['address']=='0xfd1100' and
            gp['config_words_projection']['reads']==2137 and
            gp['config_words_projection']['whole_writes']==744 and
            gp['config_words_projection']['enable_writes']==248 and
            gp['completion_bit_projection']['reads']==2137 and
            gp['completion_bit_projection']['stored_bit7_untouched'] and
            gp['completion_bit_projection']['num_register_served'] and
            not gp['con_readback_projection']['count_readback_proven'] and
            gp['con_readback_projection']['not_a_timing_model'] and
            r['instruction_adapters'].get('gpcnt_con_readback_load')==2137,
            'the GPCNT CON readback projection must serve the audio-active '
            'steady-state (SRC loop) dispatch schedule through the config-word path: '
            '2137 full-word reads (the bit0 enable gate of every task 0x6069538 '
            'dispatch plus the enabled bit7 polls), 744 whole-word reconfig stores, '
            '248 |=0x1 enables, disable/clear RMWs 249/497, and the completion-bit '
            'projection answering the same 2137 reads with bit7 forced set '
            '(read-side only); the NUM fixture serves every conversion the '
            'named constant and the count readback stays unproven')
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
    # 路 V:放行 0xfd5c90 后 +516 步,cnt 3,513+516×3=5,061(仍 <36,000,
    # **第 298 次到期未发生**,expirations/pending/交付 287 全不变——新前缀
    # 是纯配置写+临界区进出,无时间敏感活动)。
    # 路 AA:放行 CON1 读回后 uart_init 收尾+事件域首边界 +5,323 步,cnt
    # 5,061+5,323×3=21,030(仍 <36,000,**第 298 次到期仍未发生**,
    # expirations 297/pending False/交付 287 全不变——新前缀是事务收尾的纯
    # 配置写+init 链前三项,无时间敏感活动)。
    # 路 AK:放行 JL_WL_AUD entry 写后场景 walker 段 +1,760 步,cnt 再 +
    # 1,760×3=5,280 到 26,310(仍 <36,000,**第 298 次到期仍未发生**,
    # expirations 297/pending False/交付 287 全不变——新前缀是配置表查名
    # helper 链的 RAM/已建模域访问+一条已放行的音频域整字写,无时间敏感
    # 活动)。
    # 路 AN(sim-crosspage):跨页拆分放行后 walker 链 +56,572 步,到期 297→
    # 305(+8,walker 链里多轮 delay 调用反复越过 36,000 周期边界,PRD 每次被
    # delay 重编、最后窗 1ms PRD=11,999)、交付/ACK 287→295(新 8 次的
    # reti_from 离开延时循环/OS veneer 两族,见下方交付断言)、cnt 停点时
    # 10,635、pending False。
    # 路 AS(sim-adcreset):ADC reset 放行后 +32,789 步,到期 305→309(+4:
    # 尾块 delay 窗继续越过 36,000 边界)、交付/ACK 295→299(+4,其中第 5 次
    # 新到期落在 ROM 0x4180 rep 体内在块尾 0x4196 交付——指令中途延期先例,
    # 见交付断言)、PRD 被尾块最新的 delay 重编到 5ms 窗 59,999、cnt 走到 57,666、pending False。
    # 路 CV:+17 步、cnt 再 +51(=17×3 tick)到 57,717(距 59,999 尚差 2,282,
    # **无第 310 次到期**、交付/ACK 299 不变;新停点在 setter 装载对,无 delay
    # 重编,PRD 保持 59,999)、pending False。
    # 路 CW(S19 批 0b):+31,769 步,到期 309→311(+2:新前缀的 delay 窗再越
    # 边界两次)、交付/ACK 299→301;PRD 被尾段 delay 重编到 12ms 窗 47,999、
    # cnt 停点时 2,322、pending False。
    # 路 B1(S19 批 1):+545,430 步,到期 311→372(+61:延时等待族反复越
    # 边界)、交付/ACK 301→362;PRD 被最新 delay 重编回 1ms 窗 11,999、cnt
    # 停点时 1,194、pending False。路 BQ(S19 批 2):+7 步(Timer0 handler
    # #362 的 OS 时间更新路径,PRD 不再重编),cnt 再 +21(=7×3 tick)到
    # 1,215,到期/交付/pending 全不变。
    # 路 B3(S19 批 2 续):+15,391,654 步,到期 372→3401(OS 等待族+停泊
    # 唤醒反复越边界,恰 +3029=新增交付数)、交付/ACK 362→3391;PRD 被
    # 最新 delay 重编到 5ms 窗 59,999、cnt 停点时 441、pending False。
    # 路 DT(S19 批 2 续二):+2,658,967 步,到期 3401→3680(DAC 启动尾段+
    # 其 OS 等待族的延时三连反复越边界,恰 +279=新增交付数,全部交付/
    # ACK)、交付/ACK 3391→3670;PRD 被最新 delay 重编到 2ms 窗 23,999、
    # cnt 停点时 20,220、pending False。
    # 路 ADA(S19 批 2 续三):+51 步顺序配置段零新到期零新交付,cnt
    # 20,220→20,373(恰 +153=51 步×3 STD12M tick 精确走表)。
    # 路 JLA:+130 步(set_buffer 五写+挂 handler+启动段序章)零新到期零新
    # 交付,cnt 2,577→2,967(恰 +390=130 步×3 STD12M tick 精确走表)。
    # 路 P2MCLK:+2,598 步纯 CPU0 段(LED_CLK_SEL(3) 两 RMW+RAM 标志+一次性
    # LED 初始化守卫尾+LED 复位 helper 0x60195e2 的 CON0 清位/整字清零到
    # CON1 写停点),零新到期零新交付零新喂狗,cnt 12,579→20,373(恰
    # +7,794=2,598 步×3 STD12M tick 精确走表),PRD 保持 59,999;路 BATCHA
    # 批放行后 +7,033 步段(LED 复位 helper 走完→S19 环→资源 VFS)再走
    # 20,373→41,472(恰 +21,099=7,033×3 tick 精确走表,零新到期零新交付
    # 零新喂狗——41,472<PRD 59,999 无到期窗),PRD 仍 59,999。
    # 路 TONMAP:容器对放行后 +51,493 步段(VFS open→任务安装→事件循环)
    # 走 41,472→35,667:段内 PRD 两次重编至 47,999,跨界 3 次到期(15775→
    # 15778)全交付全 ACK(+3=交付 15758→15761,无新中断上下文),零新喂狗
    # (163/162 冻结),elapsed 名义单位 107→120。
    # 路 EVIMPL:jlstream 任务栈单点 4B 槽放行后 +56,377 步段(首条消息分发
    # →音频 DAC DMA 运行域)再走 35,667→15,417:段内 PRD 重编至 23,999,
    # 跨界 5 次到期(15778→15783)全交付全 ACK(+5=交付 15761→15766,新增
    # 两个中断上下文 0x6007af2/0x6074614),零新喂狗(163/162 冻结),elapsed
    # 名义单位 120→134。
    # 路 DMAIMPL:音频管线段把 PRD 走到 35,999/cnt 33,810/到期 15795。
    # 路 COPEXIMPL:mode-22 指数放行后 +21,707 步段(eq 系数设计→SRC_SYNC
    # 重配)再走 33,810→26,931:段内 PRD 重编回 35,999 的新延时窗,跨界
    # 2 次到期(15795→15797)全交付全 ACK(+2=交付 15778→15780,新增两个
    # 中断上下文 0x6064754/0x60745fa),零新喂狗(163/162 冻结),elapsed
    # 名义单位 152→157。
    # 路 SRCSYNC:配置事务三字容器放行后 +2,287 步段(SRC_SYNC 重配函数
    # 0x606fa7e 全走完→audio sync 对象创建器 0x60befae)计数走表 26,931→
    # 33,792(PRd 35,999 窗内、无到期跨界:交付/到期 15780/15797 冻结),
    # 零新喂狗(163/162 冻结),elapsed 名义单位 157→158。
    # 路 SYNCIMPL:四字容器放行后 +2 步段(creator CON 写→li 0xF00130)
    # 计数走表 33,792→33,798(恰 +6=2 步×3 STD12M tick,PRD 35,999 窗内、
    # 无到期跨界:交付/到期 15780/15797 冻结),零新喂狗(163/162 冻结),
    # elapsed 名义单位 158 冻结。
    # 路 TIMERIMPL:BT clock timer 两写放行后 +119 步段(两 timer 写→
    # request_irq→fn_config JL_SYNC 四字→0xF00000|=0x400 条件臂 RMW 读停)
    # 计数走表 33,798→34,155(恰 +357=119 步×3 STD12M tick,PRD 35,999 窗
    # 内、无到期跨界:交付/到期 15780/15797 冻结),零新喂狗(163/162 冻结),
    # elapsed 名义单位 158 冻结。
    # 路 FMIMPL:0xF00000 CON 头 RMW 放行后 +110,963 步段(fn_config 返回→
    # 框架表派发→新任务 0x6086c8c 装配→ROM 字节→半字展开环停点)PRD 重编
    # 35,999→11,999(新延时窗),跨界 22 次到期(15,797→15,819)全交付全
    # ACK(+22=交付 15,780→15,802),cnt 窗内走表到 5,769;零新喂狗(163/162
    # 冻结),elapsed 名义单位 158→186。
    # 路 SEGIMPL:prer 两形直写放行后 +13 步段(展开环迭代 1 后半程到 0x5ed0
    # 并行对停点)零新到期零新交付(15819/15802 冻结),cnt 窗内走表 5,769→
    # 5,808(恰 +39=13 步×3 STD12M tick),零新喂狗(163/162 冻结),elapsed
    # 名义单位 186 冻结(小数余数 496→509 不足一个名义单位)。
    # 路 SEGIMPL2:0x5ed0 并行对半字墙放行后 +817 步段(展开环迭代 1 后半程
    # →迭代 4 走完→环出口 0x5ef6→ROM 记录构建续走→0x6198 mul∥st.w.sp 合并
    # 读停)零新到期零新交付(15819/15802 冻结),cnt 窗内走表 5,808→8,259
    # (恰 +2,451=817 步×3 STD12M tick,PRD 11,999 窗内),零新喂狗(163/162
    # 冻结),elapsed 名义单位 186 冻结(小数余数 509→1,326 不足一个名义单位)。
    # 路 SEGIMPL3:mul 前缀×word 半墙放行后 +479 步段(0x6198 直写→0x61aa
    # mul∥ld.w 上游过→ROM 0x6170 尾部→0x63xx rep 清零循环→0x63c2 st.h.pin.l
    # 合并读停)零新到期零新交付(15819/15802 冻结),cnt 窗内走表 8,259→
    # 9,696(恰 +1,437=479 步×3 STD12M tick,PRD 11,999 窗内),零新喂狗
    # (163/162 冻结)。
    require(timer=={'con':0x1401,'cnt':49962,'prd':59999,'pending':False,
            'expirations':14416},
            'TIMER0 must stay on source 5 with the delay-function tick schedule (each delay '
            'call reprograms PRD; the route-STOREG3 stop leaves the 5ms window PRD 59999 '
            'with the counter walked on to 9342 inside the freshly reprogrammed delay '
            'window of the LED-driver prologue, the route-FD610C-con0 release walks it '
            'on to 12579 in the save-restore/0x6019502 delay windows and the route-P2MCLK '
            'release walks it on to 20373 in the LED_CLK_SEL/LED-reset delay windows '
            '(+2598 steps x 3 STD12M ticks, no new expiry, no new delivery, no new '
            'mailbox feed) and the route-BATCHA release walks it on to 41472 through '
            'the completed LED reset helper, the S19 ring and into the resource-VFS '
            'open (+7033 steps x 3 STD12M ticks, no new expiry — 41472 stays inside '
            'the 59999 PRD window, no new delivery, no new mailbox feed), and the '
            'route-TONMAP release (official containers mapped read-only, +51493 '
            'steps of VFS open / task installation / event-loop prefix) '
            'reprograms the delay window twice more and crosses the boundary '
            'three times — PRD 47999 with cnt 35667 at the stop, three new '
            'expirations/deliveries/acks, no new mailbox feed), and the '
            'route-EVIMPL release (the jlstream task-stack 4B slot unknown-word '
            'pass, +56377 steps of first-message dispatch into the audio DAC '
            'DMA run domain) reprograms the window once more (PRD 23999) and '
            'crosses the boundary five times — cnt 15417 at the stop, five '
            'more expirations/deliveries/acks (15766 delivered, two new '
            'interrupt contexts 0x6007af2/0x6074614), no new mailbox feed), and '
            'the route-SEGIMPL4 release segment (+57,523 steps through the ten '
            'clearing-loop rounds and the long walk to the clamp REP) crosses '
            'the boundary thirteen more times — 15815 delivered/acked, thirteen '
            'new interrupt contexts (ten ROM loop domains 0x5710..0x74f0 and '
            'three app jlstream/audio contexts 0x60864c8/0x6086508/0x6087928), '
            'the delay schedule reprograms PRD 11999->59999 inside the walk '
            'and cnt lands at 21975 — 15832 '
            'expirations total (15815 delivered/acked — the route-STOREG3 walk segment '
            'serves exactly one more delivery, the 17-expiry remainder folded into '
            'in-flight/deferred windows); the audio-active SRC loop then reprograms '
            'PRD to 59999 and walks cnt on to 49962 with 14416 expirations total at '
            'the instruction_budget stop, pending FALSE at the stop: '
            'the route-M stop left the 297th expiration pending-in-flight inside the GPIO '
            'critical section (ICFG 0x100, CLI bit9 clear), and the route-R release lets the '
            'chain run past the critical-section exit — the unlock restores bit9, the pending '
            'episode becomes eligible on the next advance() and the real handler W1C acks it '
            '(delivery #287, reti_from 0x12b7b6); the route-V release walks 3513+516*3=5061, '
            'the route-AA CON1-readback release walks on to 5061+5323*3=21030 and the '
            'route-AK JL_WL_AUD release walks 21030+1760*3=26310, all without reaching '
            '36000; the route-AN split release then crosses the 36000 boundary eight times '
            'inside the walker-chain delay windows (297->305 expirations, all eight delivered '
            'and acked) and the route-AS release adds four more (305->309, all delivered '
            'and acked, the fifth landing inside the ROM 0x4180 rep copy body and delivered '
            'at the block exit 0x4196 via the mid-instruction deferral); the route-CV +17 '
            'steps walk cnt to 57717 with no new expiry, the route-CW +31769 steps add '
            'two more expirations through the DAC-init tail delay windows (309->311, both '
            'delivered and acked, a 12ms window PRD 47999), and the route-B1 release adds '
            'SIXTY-ONE more through the settling-wait delay windows the DAC-start caller '
            'enters (311->372, all delivered and acked, PRD back to the 1ms window 11999 '
            'with the counter walked down to 1194)')
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
    # 路 V:+516 步、cnt 再 +2,064(=516×4 tick)到 905,452;路 V 停点
    # 0x60340d8 位于 uart_fifo_reset 的 0x60340cc OS unmask 临界区内
    # (IPMASK 0→6 机制,不动 CLI bit9),CPU0 ICFG 保持 0x300。
    # 路 AA:+5,323 步、cnt 再 +21,292(=5,323×4 tick)到 926,744(<prd
    # 1,199,999 无第 10 次溢出,交付/ACK 仍 9);RMW 放行后 fifo_reset 临界
    # 区正常退出,**IPMASK 回 0**(新停点 0x602ea72 在 init 链第 4 项函数
    # 首指令,无临界区),ICFG 保持 0x300。路 AK:+1,760 步、cnt 再 +7,040
    # (=1,760×4 tick)到 933,784(仍 <prd 无第 10 次溢出);新停点 0x6007af2
    # 在查名 helper 函数头、无临界区,IPMASK 保持 0、ICFG 保持 0x300。
    # 路 AN:+56,572 步、cnt 再 +226,288(=56,572×4 tick)到 1,160,072
    # (**仍 <prd 1,199,999,第 10 次溢出差 39,927 周期未发生**,交付/ACK
    # 仍 9);新停点 0x60719c0 是 ADC 节点 reset 函数体内普通并行对、无临界
    # 区,IPMASK 保持 0、ICFG 保持 0x300。
    # 路 AS:+32,789 步、**第 10 次溢出发生并交付/ACK(9→10)**——音频
    # init 尾块的 OS tick 级联把计数器带过 1,199,999;停点 0x606b426 是
    # DAA_CON1 配置 RMW insert 函数体内普通装载、无临界区,IPMASK 保持 0、
    # ICFG 保持 0x300;CPU1 core tick 零溢出不变。
    # 路 CV:+17 步、cnt 再 +68(=17×4 tick)到 91,276(无第 11 次溢出,
    # 交付/ACK 10 不变);新停点 0x606db4c 是 DAC_VL0 setter 体内并行对
    # 装载、无临界区,IPMASK 保持 0、ICFG 保持 0x300。
    # 路 CW(S19 批 0b):+31,769 步、cnt 再 +127,084(≈4 tick/步,含两次
    # 新 Timer0 交付往返的延期簿记差)到 218,360(**仍 <prd 1,199,999,
    # 无第 11 次溢出**,交付/ACK 10 不变);新停点 0x606ab2c 是 DAC 输出级
    # 门控函数体内普通整字写、无临界区,IPMASK 保持 0、ICFG 保持 0x300。
    # 路 B1(S19 批 1):+545,430 步,**第 11 次溢出发生并交付/ACK(10→11)**;
    # **第 12 次溢出在 IPMASK=6 屏蔽窗内到期(OS 临界区屏蔽 IRQ3 prio1)——
    # held 未交付未 ACK**(expirations 12/acknowledgements 11,pending True、
    # unresolved True、cnt 回绕后走到 0);固件随即在屏蔽窗内读 core-tick PRD
    # (0x12b0c2 并行对第二半)——路 BQ 的 core_tick_prd_readback 放行该读
    # (回放 tick_timer_init 的 1199999,并行对完整提交:r10=旧 r2=0、
    # r3=1199999),+7 步后 retry 环第一半 **0x12b0e8 读 CNT** 撞同一通用
    # overflow guard 严格停(新停点,仍位于屏蔽窗内,IPMASK=6、ICFG 仍
    # 0x300,bit9 全程未动;读回投影只覆盖 offset 8,CNT 不在一案范围)。
    # 路 P2MCLK:+2,598 步纯 CPU0 段零新溢出零新交付,cnt 107,580→117,972
    # (恰 +10,392=2,598 步×4 core tick 精确走表,仍 <prd 1,599,999);停点
    # 0x60195f8 在 LED 复位 helper 体内,但该 helper 由 0x601a422 调用、处于
    # 0x601a41c pend 的 OS 临界区内(IPMASK=6 机制,不动 CLI bit9),ICFG 保持
    # 0x300。路 BATCHA:批放行后复位 helper 全函数走完,pend 窗退出(IPMASK
    # 6→0 回落,S19 环与资源 VFS 段全在临界区外运行),+7,033 步段零新溢出
    # 零新交付,cnt 117,972→146,104(恰 +28,132=7,033×4 tick 精确走表,
    # 仍 <prd 1,599,999),ICFG 仍 0x300(bit9 全程未动)。
    # 路 TONMAP:+51,493 步段零新溢出零新交付,cnt 146,104→352,076(恰
    # +205,972=51,493×4 tick 精确走表,仍 <prd 1,599,999)。
    # 路 EVIMPL:+56,377 步段零新溢出零新交付(347/347 冻结),cnt 352,076→
    # 577,584(恰 +225,508=56,377×4 tick 精确走表,仍 <prd 1,599,999;新停点
    # 0x606e148 在音频 DAC DMA 指针轮询体内、无临界区,IPMASK 0、ICFG 0x300)。
    # 路 DMAIMPL:+71,480 步段零新溢出零新交付,cnt 577,584→863,504
    # (恰 +285,920=71,480×4 tick 精确走表)。
    # 路 COPEXIMPL:+21,707 步段(eq 系数设计→SRC_SYNC 重配)零新溢出零新
    # 交付(347/347 冻结),cnt 863,504→950,332(恰 +86,828=21,707×4 tick
    # 精确走表,仍 <prd 1,599,999)。
    # 路 SRCSYNC:+2,287 步段零新溢出零新交付(347/347 冻结),cnt
    # 950,332→959,480(恰 +9,148=2,287×4 tick 精确走表);路 SYNCIMPL:
    # +2 步段零新溢出零新交付(347/347 冻结),cnt 959,480→959,488(恰
    # +8=2×4 tick 精确走表);路 TIMERIMPL:+119 步段(BT clock timer 两写
    # →request_irq→fn_config→0xF00000|=0x400 条件臂 RMW 读停)零新溢出
    # 零新交付(347/347 冻结),cnt 959,488→959,964(恰 +476=119×4 tick
    # 精确走表);路 FMIMPL:+110,963 步段(fn_config 返回→框架表派发→
    # 新任务装配→ROM 展开环)零新溢出零新交付(347/347 冻结),cnt
    # 959,964→1,403,816(恰 +443,852=110,963×4 tick 精确走表,仍 <prd
    # 1,599,999);新停点 0x5ea4 在 ROM 字节→半字展开环的 st.h.prer 体内
    # (upstream 簿记读未初始化物理堆半字 0x19e314,IPMASK 0=临界区外,
    # ICFG 0x300)。路 SEGIMPL:+13 步段(prer 两形直写放行,展开环迭代 1
    # 后半程)零新溢出零新交付(347/347 冻结),cnt 1,403,816→1,403,868
    # (恰 +52=13×4 tick 精确走表,CPU1 路 322,919,964→322,920,016 同款);
    # 新停点 0x5ed0 在并行对 uextra.rrii.p∥st.h 高半字 B 半的合并读上
    # (未初始化字节 0x19e316=运输写未知值,IPMASK 0=临界区外,ICFG 0x300)。
    # 路 SEGIMPL2:0x5ed0 并行对半字墙放行后 +817 步段(展开环迭代 1 后半程
    # →迭代 4 走完→环出口→ROM 记录构建续走)零新溢出零新交付(347/347
    # 冻结),cnt 1,403,868→1,407,136(恰 +3,268=817×4 tick 精确走表,CPU1
    # 路 322,920,016→322,923,284 同款);新停点 0x6198 在 mul.modimm.rri.p∥
    # st.w.sp 的上游合并读上(未初始化物理堆帧字 0x19e4bc,IPMASK 0=临界
    # 区外,ICFG 0x300)——mul 前缀×word 半族,归下路。
    # 路 SEGIMPL3:mul 前缀×word 半墙放行后 +479 步段(0x6198 直写→0x61aa
    # mul∥ld.w 上游过→ROM 0x6170 尾部 st.w.idxs 记录字→0x63xx 两轮 rep
    # 清零循环)零新溢出零新交付(347/347 冻结),cnt 1,407,136→1,409,052
    # (恰 +1,916=479×4 tick 精确走表);新停点 0x63c2 在 rep.i 4 16 体内
    # st.h.pin.l 的上游合并读上(未初始化堆半字窗 0x19e100,IPMASK 0=临界
    # 区外,ICFG 0x300)——st.h.pin.l 长形(短形已有 exact_postincrement_
    # store 直写面)归下路。
    tick0=r['peripherals']['core_tick_configuration'][0]
    require(tick0['pending'] is False and tick0['expirations']==23775 and
            tick0['unresolved_overflow'] is False and tick0['acknowledgements']==23775 and
            tick0['cnt']==65344 and
            tick0['irq_delivery_simulated'] and
            r['machine']['core_tick_entry_projection']['enabled'] and
            r['peripherals']['core_tick_configuration'][1]['expirations']==65 and
            r['machine']['cores']['0']['partial_icfg']['known_value']=='0x300' and
            r['peripherals']['ipmask']==6,
            'the CPU0 core tick must deliver and ack ALL 23775 overflows through the '
            '139,947,345 stop: the historical route-AK landmark, timeout-window, AS and '
            'B1 ones carry the count to eleven, the route-BQ release replays the masked '
            'PRD read (tick_timer_init store 1199999, zero side effects) and the '
            'route-B3 CNT held-snapshot release serves the 0x12b0e8 retry-loop CNT load '
            'with the stored post-overflow snapshot (explicit CNT-frozen assumption, '
            'unit-pinned no side effects); the masked 12th overflow then LEAVES its '
            'window when the OS critical section ends (IPMASK back to 0) — the route-BF '
            'per-step eligibility recheck delivers it and every later overflow rides the '
            'same recheck (108 expirations = 108 acknowledgements through the route-B3 '
            'stop, the route-DT segment adds eight more for 116/116, nothing pending, no '
            'unresolved overflow at the stop, the counter walked on through the steady state '
            'to cnt 816344 at the route-JLEQ frontier (147/147) and on through the '
            'route-GPCNUM extension to cnt 481056 with 200/200 delivered-and-acked '
            '(nothing pending, PRD reprogrammed to 1599999 — the tick restart fn '
            '0x6065806 freerun shape) and the route-WDFEED park extension to cnt 28172 '
            'with 347/347 delivered-and-acked; the route-STOREG3 consumption-chain walk '
            'leaves the OS critical section behind (the new stop has IPMASK 0, the '
            'byte-unpack and dispatch code runs unmasked) and the counter walks on '
            'through the release segment to cnt 103264, still 347/347 delivered-and-acked '
            '(zero new overflow — the pure-CPU0 walk takes ~4 nominal WDT units) and the '
            'route-FD610C-con0 release walks it on to cnt 107580, still 347/347 '
            'delivered-and-acked (zero new overflow) and the route-P2MCLK release '
            'walks it on to cnt 117972 (+2598 steps x 4 core ticks), still 347/347 '
            'delivered-and-acked (zero new overflow) and the route-BATCHA release '
            'completes the LED reset helper inside the pend window, EXITS the '
            'critical section (IPMASK back to 0 — the S19 ring and the resource-'
            'VFS walk run unmasked) and walks the counter on to cnt 146104 '
            '(+7033 steps x 4 core ticks, still zero new overflow) and the '
            'route-TONMAP release (official containers mapped, +51493 steps '
            'of VFS open / task installation / event-loop prefix) walks the '
            'counter on to cnt 352076 (+51493 steps x 4 core ticks, still '
            'zero new overflow — the whole segment runs with CPU0 active, '
            'the event clock never advances) and the route-EVIMPL release '
            '(the jlstream task-stack 4B slot unknown-word pass) walks it '
            'on to cnt 577584 (+56377 steps x 4 core ticks, still zero new '
            'overflow — 347/347 delivered-and-acked, CPU0 active throughout, '
            'the event clock frozen at 82545158), the route-DMAIMPL segment '
            'walks it on to 863504 and the route-COPEXIMPL release (the '
            'mode-22 exponential pass) on to 950332 (+21707 steps x 4 core '
            'ticks, still zero new overflow — the eq coefficient design runs '
            'unmasked); the route-FMIMPL/SEGIMPL2/SEGIMPL3 releases walk the '
            'counter on to 1409052 still 347/347, and the route-SEGIMPL4 '
            'release segment crosses the 1599999 boundary ONCE more — the '
            'overflow delivers and acks naturally (348/348) and the reported '
            'cnt becomes the frozen post-overflow held snapshot 39124 (the '
            'explicit CNT-frozen assumption, not a running count), walking on '
            'to 42444 through the route-REPCOND release segment (+830 steps x 4 '
            'core ticks = +3320, still zero new overflow, 348/348 frozen) and on '
            'to 43332 through the route-SRCSYNC3 release segment (+222 steps x 4 '
            'core ticks = +888, still zero new overflow, 348/348 frozen); the '
            'audio-active SRC loop then carries the CPU0 core tick on to 23775/23775 '
            '(cnt 65344 at the instruction_budget stop) and the CPU1 core tick to 65 '
            'overflows, CPU0 partial ICFG stays 0x300 '
            '(bit9 untouched throughout); IPMASK is 0 at the SEGIMPL4 stop — the '
            '0x601a41c pend section closed when the LED reset helper returned, '
            'and both the 0x6010ca6 resource-VFS directory read and the new '
            '0x60869ba task event-loop parameter read sit OUTSIDE any '
            'OS critical section (unmasked execution, the route-STOREG3 shape '
            'again) — and walks to 6 at the REPCOND stop: the downstream walk '
            'to 0x60709e0 stays inside the OS critical section up to the '
            'JL_SRC_SYNC COEF read 0xfe263c which is honestly refused '
            '(jl_src_sync_coef_read_not_modeled, IPMASK 6, the same critical-'
            'section shape as the route-FMIMPL '
            'modem-window CON-head RMW)')
    # 路 BQ 读回投影面:报告键只在开关开启的世界出现(legacy 逐字节不变),
    # 证据链字段钉死(最近写入=1199999、只覆盖 unresolved 窗的 offset 8、
    # 硅读回行为未测量)。
    require(tick0['prd_readback_projection']=={
            'enabled':True,'prd_last_write':239999,
            'returns':'the last real firmware PRD store (tick_timer_init); no synthesized default',
            'covers_reads_during':'unresolved_overflow only (offset 8/PRD alone)',
            'con_cnt_reads_and_counter_phase_unchanged':True,
            'prd_write_equals_live_prd':True,
            'silicon_readback_behavior_measured':False},
            'the core-tick PRD readback projection report differs from the pinned evidence '
            'chain (last real write replay only, no default, CON/CNT/counter phase untouched)')
    require(r['peripherals']['p11_heap_info_fixture']['bytes']=='0c3df439',
            'the heap fixture must carry the derived candidate bytes 0c3df439')
    # M2P 消息区钉子字节:LRC 周期/定时器字节(05/0a/28/78=5/10/40/120,其余
    # 未写保持 unknown)与 RVD2PVDD_EN 字(实测 0x00)。第二轮 LRC 写(0x6067ac8,
    # 以探路值换算出的 200000 Hz)重推出与第一轮完全相同的字节——数值自洽的
    # 直接证据。
    require(r['memory_snapshots']['p11_m2p_lrc']['bytes_with_unknowns']=='05??0a2878????08' and
            r['memory_snapshots']['p11_m2p_lrc']['initialized_bytes']==5 and
            r['memory_snapshots']['p11_m2p_rvd2pvdd']['bytes_with_unknowns']=='????00??' and
            r['memory_snapshots']['p11_m2p_rvd2pvdd']['initialized_bytes']==1,
            'the M2P message-window bytes differ from the SDK-anchored LRC derivation and the '
            'executed M2P_RVD2PVDD_EN write')
    # LRC trim 结果块(0x129D88..0x129DEC):平均 [0x129D88]/计数字节(路 CG:9 次
    # OS tick 级联推到第 10 帧;路 B1:延时等待段 LP 链再走 2 帧→**第 12 帧**
    # (0x129D8C=12(0x0c)、0x129D8E=1),历史数组继续添 200000 帧)/第一个任务
    # 句柄半字 [0x129D94]=1(0x6067aec h[0x129D94]=r0 已执行,任务创建 SDK 调用
    # 返回句柄 1;旧记录写成 0x129D90 系地址笔误,本路按实际快照修正)/raw
    # [0x129D98]/20 项历史数组——探路值的全部数值侧效应逐字节钉死
    # (200000=0x00030D40),全窗 0x64 字节另以 SHA 钉死(窗口 >40 字节无逐字节串)。
    head=r['memory_snapshots']['lrc_trim_result_head']
    require(head['bytes_with_unknowns']=='400d0300'+'15000100'+'00000000'+'01000000'+'400d0300'*6 and
            head['initialized_bytes']==40,
            'the trim-result head must pin avg 200000, counter bytes 20/1 (the nine route-CG '
            'OS-tick cascades advance the LRC period counter to the tenth frame, the two '
            'route-B1 delay-family frames carry it to the twelfth and the route-B3 OS-wait '
            'parked-wake schedule to the TWENTIETH frame 0x14), the '
            'first-task handle halfword 1 at 0x129D94, raw 200000 and the seeded history '
            'head of 200000')
    block=r['memory_snapshots']['lrc_trim_result_block']
    require(block['initialized_bytes']==0x64 and
            block['sha256']=='663e9b740799be8fe8442273b8f29c1661327c92e973316ab4983407f7177544',
            'the full trim-result block (raw + the twenty-entry history, every entry the '
            'identity 200000 frame) must match the probe-value side effects byte-exactly')
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
    # /fixture 读 2→10;路 B1 再走 2 帧(54→64,计数字节 12、trim 读 20→24/
    # fixture 读 10→12);路 B3 再走 8 帧(64→104)、路 JLEQ 稳态再走 2 帧(104→114);lrc_trim_result_simulated 因显式探路值翻
    # True(报告语义如实)。
    require(lp['lrct_con']=={'address':'0xfd3600','known_mask':'0x7f','known_value':'0x41',
                             'rmw_writes':1294} and
            lp['write_only'] and not lp['readback_observed'] and
            not lp['bit_semantics_or_completion_verified'] and lp['lrc_trim_result_simulated'],
            'the JL_LRCT bit records or the explicit assumption flags differ')
    trim=lp['lrct_trim_done_projection']
    require(trim['address']=='0xfd3600' and trim['trim_done_bit']=='0x80' and
            trim['reads']==510 and trim['projected_word']=='0xc1' and trim['known_mask']=='0x7f' and
            not trim['completion_timing_modeled'] and
            trim['trim_result_register']['address']=='0xfd3604',
            'the trim-done readback projection differs from the observed poll reads '
            '(0x6067514/0x606751a plus the reads of the route-CB/CG and route-B1 '
            'LRC-period rounds)')
    fixture=lp['lrct_trim_result_fixture']
    require(fixture['address']=='0xfd3604' and fixture['value']=='0x9600' and
            fixture['reads']==255 and fixture['value_is_derived_not_measured'] and
            '7,680,000,000/38,400 = 200,000' in fixture['derivation'] and
            'not a silicon measurement' in fixture['derivation'],
            'the trim-result probe fixture record must pin 38400, its 255 served reads (the '
            'route-BY read plus the LRC-period rounds through the route-B3 stop and on '
            'through the audio-active steady state) and the '
            'derived-not-measured derivation chain')
    con=lp['lp_timer_con_readback_projection']
    require(con['addresses']==['0xf2a900','0xf2a904','0xf2a908'] and con['reads']==2 and
            con['readback_semantics'].startswith('last firmware-written value') and
            '0x6067672' in con['evidence'] and '0x606767e' in con['evidence'],
            'the LP-timer CON readback projection must cover CON0/CON1/CON2 with two reads '
            '(0x6067672 ld.w + 0x606767e rmw.and.nmodimm) and pin the SDK __RW evidence')
    deliveries=r['machine']['software_irq_entry_projection']['deliveries']
    require(len(deliveries)==16143 and
            all((d['core'],d['irq'],d['vector_address'],d['handler']) in
                ((0,127,'0x19fffc','0x12ad2a'),(1,125,'0x19fff4','0x60662c8'))
                for d in deliveries) and
            all(d['reti_from'] in ('0x12b608','0x12c5cc','0x12c59c','0x12c5a0','0x12c5ce')
                for d in deliveries) and
            sum(1 for d in deliveries if d['core']==0)==16125 and
            sum(1 for d in deliveries if d['core']==1)==18 and
            sum(1 for d in deliveries if d['reti_from']=='0x12c5cc')==7839,
            'the pmu_task creation must round-trip the scheduler with two more CPU0 irq127 '
            'deliveries (the trim-task creation at 0x6067ae0 adds none: its path stops inside '
            'the timer bring-up before any scheduler IPI); the route-CO scheduler helper prefix '
            'adds one more CPU1 irq125 sync-IPI delivery at the tail, the route-CW DAC-init '
            'tail adds one more CPU1 sync round, and the route-B3 OS-wait prefix adds TWO '
            'more CPU0 irq127 rounds — the last of them waking a PARKED CPU0 (reti_from '
            '0x12c5cc, the instruction after the idle PC: the scheduler IPI arrives while CPU0 sits in the '
            'all-core IDLE park and the idle-wake consumes the IDLE); the route-JLEQ steady state adds six more rounds '
            '(two scheduler waits, one PARKED CPU1 sync-IPI wake, three trailing '
            'scheduler waits — 34 deliveries total, every one RTI-complete), and the '
            'route-WDFEED extension past the crossed watchdog point adds the '
            'pure steady-state tail: 146 parked-wake/scheduler-wait alternations '
            'plus one final parked wake, 345 deliveries total, every one '
            'RTI-complete; the audio-active steady state (SRC loop) then carries '
            'the count on to 16143 deliveries (16125 CPU0 irq127 + 18 CPU1 irq125, '
            'with the 0x12c59c/0x12c5a0/0x12c5ce idle-wake variants joining the '
            '0x12b608/0x12c5cc family), every one RTI-complete)')
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
    # 路 CW(S19 批 0b):新前缀(60KB 缓冲 memset+DAC-init 尾段)有一次新的
    # CPU1 同步 IPI 往返——交付列表 11→12(尾项仍是 (1,125,'0x12c5ca')),
    # conditional_software_irq_rti 11→12、commands/acks 22/11→24/12。
    require(r['machine']['events_truncated'] is True and
            r['instruction_adapters'].get('conditional_software_irq_rti')==16143,
            'the software IRQ RTI count must reach 16143 = the 16143 delivered rounds (the pmu_task-entry RTI event lies '
            'in the byte-identical pre-305k prefix pinned at the 689,402 landmark; the trimmed '
            '32+32 events window holds the IRQ4 round trips; the route-CO CPU1 sync '
            'IPI is a repeat_irq_delivery round whose handler RTI was still in flight at the '
            'route-CO stop and completes during the route-CQ helper-tail/W1[1]-walker steps, '
            'the route-CW DAC-init tail adds one more CPU1 sync-IPI round, and the route-B3 '
            'OS-wait prefix adds the two CPU0 irq127 rounds, the route-JLEQ steady state '
            'adds six more and the route-GPCNUM extension eighteen (nine parked '
            'wake + scheduler-IPI alternations) — all fifty-two RTI-complete; the '
            'route-FMIMPL released segment adds the new 0x6086c8c task-installation '
            'round and the audio-active SRC loop carries the count on to '
            '16143 delivered, 16143 RTI-complete)')
    require(r['peripherals']['software_irq_commands']['commands']==32697 and
            r['peripherals']['software_irq_commands']['acknowledgements']==16143,
            'the two scheduler IPI round trips plus the route-CO scheduler-helper sync IPI '
            'and the route-CW DAC-init tail sync IPI carry the historical bookkeeping to '
            '104/52, and the route-WDFEED steady-state extension runs the scheduler '
            'wait/parked-wake rounds on to 690 commands / 345 acknowledgements (two '
            'commands per delivered round, every one acked — nothing dropped); the '
            'route-FMIMPL released segment and the audio-active SRC loop carry the '
            'bookkeeping on to 32697/16143 '
            '(every later delivered round adds two commands, both acked)')
    # 路 V 及之前的新前缀不经硬件 CRC 引擎:CRC 30 次往返 900 字节与
    # crc_bankb_erased_fixture 完全一致。路 AA:CON1 读回放行后 uart_init 收尾+
    # init 链前三项经硬件 CRC 引擎 **+2 轮**(种子 0、结果 0x62eb/0x9c78、
    # 合计 1781 字节)——全部经共享 CRC16 wrapper 0x6000f0c 的硬件路径
    # (引擎唯一入口,调用站点未逐一钉死;停点函数 0x602ea68 体内 0x602f234
    # 也调该 wrapper,但其 (id,&byte) 配置读在停点之后),前 30 轮逐值相同、
    # 新增两轮钉死字面值。
    crc_heap=r['peripherals']['hardware_crc_projection']
    require(crc_heap['seed_writes'][:15]==crc_ff['seed_writes'][:15] and
            crc_heap['result_reads'][:15]==crc_ff['result_reads'][:15] and
            len(crc_heap['seed_writes'])==32 and len(crc_heap['result_reads'])==306 and
            crc_heap['seed_writes'][30:]==['0x0','0x0'] and
            crc_heap['result_reads'][30:]==['0x62eb','0x9c78']+['0xa1cd','0x9946',
                '0xd1cf','0x239e','0x7dee','0x91a9']*7+['0xa1cd','0xb37d','0xa11d',
                '0xc190','0xf271','0xb758','0x5ddd','0x370']+['0x583d','0xc190',
                '0xf271','0xb758','0x5ddd','0x370','0xfa0d','0x4dcd','0xcc9',
                '0x9129']+['0xa1cd','0x9946','0xd1cf','0x239e','0x7dee',
                '0x91a9']*6+['0x9b61','0x97df']+['0xc190','0xf271','0xb758',
                '0x5ddd','0x768']+['0x9b61','0x97df']+['0xc190','0xf271','0xb758',
                '0x5ddd','0x768']+['0xa1cd','0x9946','0xd1cf','0x239e','0x7dee',
                '0x91a9','0xc1b0']*7+['0xafef']+['0xa1cd','0x9946','0xd1cf',
                '0x239e','0x7dee','0x91a9','0xc1b0']+['0xa1cd','0x9946','0xd1cf',
                '0x239e','0x7dee']+['0x66e5','0xc73e','0x60ca']
            +['0x7e4e','0xcf96','0xf5c0']
            +['0xa1cd','0x9946','0xd1cf','0x239e','0x7dee','0x91a9']*10
            +['0xa1cd','0x9946','0xd1cf','0x239e','0x7dee','0x91a9']*6 and
            crc_heap['fed_bytes']==74909,
            'the heap fixture must keep the first fifteen hardware CRC round trips '
            'byte-identical to crc_bankb; the route-AA prefix adds the seeded '
            '0x62eb/0x9c78 pair, the route-AN walker chain thirty and the route-AS '
            'audio-init tail twelve more result rounds on the standing zero seed (no new '
            'seed writes: 32 seeds vs 306 results) through the shared 0x6000f0c wrapper '
            'hardware path, whose results repeat the six-value cycle '
            '0xa1cd/0x9946/0xd1cf/0x239e/0x7dee/0x91a9 exactly seven times (five '
            'scenario-table walkers plus two route-AS tail groups); the route-STOREG3 '
            'consumption-chain walk adds twelve more rounds on the same standing zero '
            'seed (one seven-value cycle plus a five-value prefix of the next — the S18 '
            'event dispatch walks the CRC-verified config tables again), and the '
            'route-BATCHA release segment adds three more rounds on the same '
            'standing zero seed (0x66e5/0xc73e/0x60ca — the resource-VFS '
            'EXT_RESERVED/APP partition open walks three 32-byte directory '
            'entries, each CRC-checking header bytes 2..31 = 90 fed bytes) '
            'before the fourth entry read stops at the unmapped flash byte, '
            'and the route-TONMAP release (official TON/TON1 containers '
            'mapped read-only) adds fifteen more rounds on the same standing '
            'zero seed: the second walker call CRC-checks the TON1 container '
            'header 30 bytes with the engine recomputing 0x7e4e — EXACTLY the '
            'official header hcrc field, the strongest in-run proof that the '
            'mapped bytes are the official container — then two directory '
            'entries (0xcf96 index.idx / 0xf5c0 power_on.wts); the task-'
            'installation tail walks the six-value CONFIG cycle 0xa1cd/0x9946/'
            '0xd1cf/0x239e/0x7dee/0x91a9 twice more (twelve rounds), and the '
            'route-EVIMPL release (the jlstream task-stack 4B slot unknown-word '
            'pass) walks the same CONFIG cycle four more times (twenty-four '
            'rounds — the first jlstream message dispatch re-reads the '
            'CRC-verified config tables), and the route-COPEXIMPL release (the '
            'mode-22 exponential pass) walks the same CONFIG cycle one more '
            'time (six rounds — the eq open-config coefficient design re-reads '
            'the CRC-verified config tables again), and the route-FMIMPL released '
            'segment walks the same CONFIG cycle six more times (thirty-six '
            'rounds on the standing zero seed — the framework table dispatch '
            're-reads the CRC-verified config tables for the new 0x6086c8c task '
            'installation), 74909 fed bytes total')
    require(r['peripherals']['p11_low_power_response']['requests_observed']==1 and
            r['peripherals']['counts'].get('p11_lpwr_post_clear_poll_reads')==1,
            'the PWR_CON bit5 request/poll projection must stay at its single observed round')
    # 22 个 adapter 计数期望值见 evidence/p11_heap_adapter_count_baseline.json,
    # 用 tools/capture_p11_heap_adapter_counts.py --recapture 重取(推进前沿后
    # 计数漂移时)。route-by-route 增量叙事已随绝对值一起迁出断言。
    _adapter_actual = _adapter_count_actual(r['instruction_adapters'])
    _adapter_expected = _adapter_count_baseline()
    require(_adapter_actual == _adapter_expected,
            'the heap-experimental prefix adapter counts differ from the in-tree baseline '
            'evidence/p11_heap_adapter_count_baseline.json: '
            + _adapter_count_mismatch(_adapter_actual, _adapter_expected))
    # 路 BE:Timer0 IRQ4 交付/惰性两分支探针(探路 fixture 只拉近到期计数,
    # 交付分支不注入任何资格 fixture——运行态 trace 已证实前缀里驱动安装
    # 循环在 ~110,768 步把 IRQ4 以 priority 1 真实使能且此后无清零)。
    d=rows['timer0_irq4_delivery_probe']
    require(d['completed_steps']==310067 and d['stop_core']==0 and d['stop_pc']=='0x60116e6' and
            d['stop']=={'kind':'fd31_adc_con_readback_not_modeled','address':'0xfd3100'},
            'the Timer0 IRQ4 delivery probe must return to the SAME honest fd31-ADC-readback '
            'frontier after the full delivery round trip (+540 steps of round-trip overhead '
            'over the 304,317 baseline)')
    deliveries=d['machine']['timer0_irq_entry_projection']['deliveries']
    require(len(deliveries)==2 and
            (deliveries[0]['core'],deliveries[0]['irq'],deliveries[0]['vector_address'],
             deliveries[0]['handler'],deliveries[0]['reti_from'])==(0,4,'0x19fe10','0x12ece8','0x12b608') and
            not deliveries[0]['delivery_clears_pending'] and
            deliveries[0]['pending_cleared_by_handler_ack'] and
            (deliveries[1]['core'],deliveries[1]['irq'],deliveries[1]['vector_address'],
             deliveries[1]['handler'],deliveries[1]['reti_from'])==(0,4,'0x19fe10','0x12ece8','0x60676de') and
            not deliveries[1]['delivery_clears_pending'] and
            deliveries[1]['pending_cleared_by_handler_ack'],
            'the two Timer0 IRQ4 deliveries must each read the REAL vector slot 0x19fe10 '
            '(app-written 0x12ece8) and interrupt the scheduler wait 0x12b608 (first, right '
            'at the probe boundary) and the core-tick handler frame 0x60676de (second, the '
            'next real STD12M expiry nested over the CPU0 tick ISR) without clearing pending')
    rti=[e for e in d['machine']['events'] if e.get('kind')=='timer0_irq_rti']
    require(len(rti)==2 and
            rti[0]['pc_after']=='0x12b608' and rti[0]['sp_after']=='0x400b38' and
            rti[0]['reti_from_frame']=='0x12b608' and rti[0]['bank_writeback_is_assumption'] and
            rti[1]['pc_after']=='0x60676de' and rti[1]['sp_after']=='0x400b5c' and
            rti[1]['reti_from_frame']=='0x60676de' and rti[1]['bank_writeback_is_assumption'],
            'the two symmetric RTIs must return to the interrupted scheduler wait 0x12b608 '
            '(SP=USP 0x400b38) and to the nested core-tick frame 0x60676de (SP=0x400b5c), '
            'each SP=USP from the really popped frame')
    require(d['peripherals']['timer0']['pending'] is False and
            d['peripherals']['timer0']['expirations']==12,
            'the real handler W1C (rmw.or.modimm [0xfd0400] |= 0x4000, the 0xd401 RMW word '
            'with the pending bit15 readback visible) must clear pending on both deliveries '
            'and leave exactly the 12 real STD12M expiries counted in the prefix')
    require(d['instruction_adapters'].get('timer0_irq_handler_frame')==4,
            'each of the two handler executions must run both 12B special-frame anchors '
            '(push.srl at 0x12ece8, pop.srl at 0x12ed08) with the 16B push.rl/pop.rl frame '
            'on the ordinary transport (2 executions x 2 anchors = 4 frames)')
    m=rows['timer0_irq4_masked_probe']
    # 路 BY 注:主场景(p11_heap)在路 BY 后停在 447,383(P3_ANA_READ,折叠
    # 世界);masked probe 不开 fd31 读回/PND/fixture——它保持 304,317 的
    # CON 读回诚实停(与 delivery_probe 的 304,857 同族);停点按字面值钉,
    # 不引用主场景。
    require(m['completed_steps']==308612 and
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
    require(d['completed_steps']==308602 and d['stop_core']==0 and d['stop_pc']=='0x60116e6' and
            d['stop']=={'kind':'fd31_adc_con_readback_not_modeled','address':'0xfd3100'},
            'the core tick IRQ3 delivery probe must return to the SAME honest fd31-ADC-readback '
            'frontier after the full delivery round trip (+863 steps of round-trip overhead '
            'over the 304,317 baseline)')
    deliveries=d['machine']['core_tick_entry_projection']['deliveries']
    require(len(deliveries)==5 and
            [x['reti_from'] for x in deliveries]==['0x12b608','0x600a78c','0x600a78c','0x6067996','0x6067996'] and
            all((x['core'],x['irq'],x['vector_address'],x['handler'])==(0,3,'0x19fe0c','0x60661a4')
                and not x['delivery_clears_pending'] and x['pending_cleared_by_handler_ack']
                for x in deliveries),
            'the five core tick IRQ3 deliveries must each read the REAL vector slot 0x19fe0c '
            '(firmware-registered handler 0x60661a4) and interrupt the scheduler wait '
            '0x12b608 (first), the tick_timer callback dispatch 0x600a78c (next two) and the '
            'nested tick ISR body 0x6067996 (last two), all without clearing pending')
    rti=[e for e in d['machine']['events'] if e.get('kind')=='core_tick_rti']
    require(len(rti)==2 and
            rti[0]['pc_after']=='0x6067996' and rti[0]['sp_after']=='0x400b6c' and
            rti[0]['reti_from_frame']=='0x6067996' and rti[0]['bank_writeback_is_assumption'] and
            rti[1]['pc_after']=='0x6067996' and rti[1]['sp_after']=='0x400b6c' and
            rti[1]['reti_from_frame']=='0x6067996' and rti[1]['bank_writeback_is_assumption'],
            'the two symmetric RTIs must return to the nested tick ISR body 0x6067996 with '
            'SP=USP 0x400b6c from the really popped frame')
    tick=d['peripherals']['core_tick_configuration'][0]
    require(tick['pending'] is False and tick['expirations']==5 and
            tick['acknowledgements']==5 and tick['irq_delivery_simulated'],
            'the real handler ACK (rmw.or.modimm [0xffe0ec] |= 0x40) must clear pending on '
            'each of the five overflows and the counter must resume after each')
    require(d['peripherals']['core_tick_configuration'][1]['expirations']==0,
            "CPU1's tick (PRD=479999999) must never overflow in this prefix")
    require(d['instruction_adapters'].get('core_tick_handler_frame')==10,
            'each of the five handler executions must run both 12B special-frame anchors '
            '(push.srl at 0x60661a4, pop.srl at 0x60661e2) with the 16B push.rl/pop.rl frame '
            'on the ordinary transport (5 executions x 2 anchors = 10 frames)')
    m=rows['core_tick_irq3_masked_probe']
    require(m['completed_steps']==304299 and
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
    # ——路 AJ/sim-keymatrix:三键矩阵多手势场景断言——
    mx=rows['key_chain_matrix_scan']
    require(mx['stop_core']==0 and mx['stop_pc']=='0x60012de' and
            mx['stop']['kind']=='unmapped_or_readonly_write' and mx['stop'].get('address')=='0xfd58b8',
            'the key-chain matrix scenario must keep the exact route-M frontier world '
            '(3,072,236 @ 0x60012de) before the direct calls — same world as route S')
    mm=mx['key_chain_matrix_scan']
    require(mm['read_key_before_registration']==255 and
            mm['read_key_classification']=={'release':255,'multi':10,'vol_down':11,'vol_up':12},
            'the four-band matrix must classify by REAL code: 255 before registration (queue '
            'miss), then release 400->255, multi 600->10, vol_down 780->11, vol_up 950->12 '
            '(one real sampling transaction + one real read_key per band)')
    require(all(s['sampled_value']==s['res_fixture'] for s in mm['res_fixture_switches']) and
            len(mm['res_fixture_switches'])==24,
            'all 24 real sampling transactions (4 classification + 20 sequence phase switches) '
            'must return exactly the fixture divider level')
    seq=mm['sequences']
    matrix_beats=[b for b in mm['beat_states'] if 'sequence_reset' not in b]
    require(sum(s['beats'] for s in seq.values())==mm['scan_beats_total'] and
            len(matrix_beats)==mm['scan_beats_total'] and
            all({'seq','beat','change_count','post_payloads'} <= set(b) for b in matrix_beats),
            'every matrix scan beat must carry its sequence label, beat index and the real '
            'debounce state read back from ctx RAM (per-sequence beat/gesture record)')
    require([(p['beat'],p['payload']) for p in seq['vol_up_single']['post_calls']]==
            [(37,hex(0x10000|(12<<8)|0))] and
            [(p['beat'],p['payload']) for p in seq['vol_down_single']['post_calls']]==
            [(37,hex(0x10000|(11<<8)|0))],
            'single clicks on the two NEW keys must post gesture 0 with their own key ids at '
            'the release-stable terminator beat 37: vol_up key 12 -> 0x10c00, vol_down key 11 '
            '-> 0x10b00 (the key-id field of the producer payload is the matrix key-id table '
            'byte, different physical keys are distinguishable at the post boundary)')
    require([(p['beat'],p['payload']) for p in seq['multi_triple_click']['post_calls']]==
            [(85,hex(0x10000|(10<<8)|5))] and
            [(p['beat'],p['payload']) for p in seq['multi_quad_click']['post_calls']]==
            [(109,hex(0x10000|(10<<8)|6))],
            'triple/quad clicks must post gestures 5/6 at the terminator beats 85/109 (click '
            'count 3/4 + 2 — the n+2 multi-click encoding route S pinned at 0x6001b08, now '
            'MEASURED for n=3 and n=4 where d1-acceptance previously only extrapolated it)')
    hold=seq['multi_long_hold']
    hold_posts=[(p['beat'],int(p['payload'],16)&0xff) for p in hold['post_calls']]
    require(hold_posts.count((78,1))==1,
            'long hold: press-confirm gesture 1 must post exactly once at beat 78 — '
            'press_count reaches the 75 threshold there (press_count=beat-3: counts start at '
            'the 4th beat after the 2-beat change window, exactly matching the route-S '
            'beat_states progression)')
    require([beat for beat,g in hold_posts if g==10]==[183] and
            [beat for beat,g in hold_posts if g==11]==[378],
            'long hold tiers: the 1000ms tier posts gesture 10 (0x10a0a) at beat 183 and the '
            '3000ms tier gesture 11 (0x10a0b) at beat 378 — CORRECTING route Q section 2.2 '
            'inferred ladder "1000..10000ms -> gestures 11..14": the measured ladder starts '
            'at gesture 10, each tier firing on the first 15-beat repeat slot where the '
            'elapsed time since the beat-78 press CONFIRMATION crosses the threshold '
            '(78+105=183 -> 1050ms>=1000ms; 78+300=378 -> exactly 3000ms)')
    require([beat for beat,g in hold_posts if g==2]==
            [93+15*k for k in range(20) if 93+15*k not in (183,378)],
            'long hold: gesture-2 HOLD repeats fire every 15 beats from beat 93 (the hold '
            'counter reaches its 90 tier at beat 93=90+3), the tier beats 183/378 REPLACE '
            'their repeat slots (no double post) and the rhythm resumes after each tier '
            '(198=183+15) — the repeat rhythm route S only extrapolated, now measured '
            'across two tiers')
    require(hold_posts[-1]==(394,3) and not any(g==0 for _,g in hold_posts),
            'long-press release: the firmware posts the explicit release gesture 3 (0x10a03) '
            'at release+4 beats (390+4) and NEVER a single-click gesture 0 — the '
            'd1-acceptance A-group "extra click on long-press release" trap does not exist, '
            'and the gesture 3 release-confirmation slot is observed for the first time')
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
    report['key_chain_matrix_scan_projection']=(
        'route AJ/sim-keymatrix: opt-in scenario key_chain_matrix_scan (default OFF keeps every '
        'legacy report byte-identical; the route-S scenario itself is untouched). The single-key '
        'two-band fixture is widened to the REAL three-key matrix layout on the SAME route-M '
        'frontier world: a 20-level threshold table (500/720/850/1022 + 16 sentinels) and a '
        '20-entry key-id table (255/10/11/12 + zeros), all three physical keys sharing ONE ADC '
        'divider channel (0x10001 — a resistive ladder: different keys produce different '
        'voltages on the same channel, route-Q section 2.1). Key ids are an explicitly '
        'synthetic-but-self-consistent assignment following the BW handler mask 0x09050307 '
        '(key 10..13 -> logical {7,3,5,9}) and the App eventID layout 0x{logical}0{gesture+1}: '
        'key 10 = multi-function (0x700), key 11 = vol- (0x301), key 12 = vol+ (0x501). The '
        'real config 523 lives in the bank-B record area outside public artifacts — every band '
        'value is synthetic-not-measured. MEASURED (all real firmware code, the only synthetic '
        'input is the RES level): per-band classification by real read_key (release 400->255, '
        'multi 600->10, vol_down 780->11, vol_up 950->12); vol+ single click posts 0x10c00 and '
        'vol- single click posts 0x10b00 at the terminator beat 37 (different key ids ARE '
        'distinguishable at the post boundary — the B-group action-coverage premise); multi '
        'triple click posts 0x10a05 (beat 85) and quad click 0x10a06 (beat 109) — the n+2 '
        'encoding measured at n=3 and n=4, previously only extrapolated; the 390-beat hold '
        'posts gesture 1 at beat 78 (press_count=75=beat-3, matching route-S beat states), '
        'gesture-2 repeats every 15 beats from beat 93, the 1000ms tier gesture 10 (0x10a0a) '
        'at beat 183 and the 3000ms tier gesture 11 (0x10a0b) at beat 378 — CORRECTING route '
        'Q section 2.2 inferred "1000..10000ms -> gestures 11..14" ladder: the measured '
        'ladder starts at gesture 10, each tier firing on the first 15-beat repeat slot where '
        'the elapsed time since the beat-78 press CONFIRMATION crosses the threshold '
        '(78+105=183, 78+300=378), replacing that slot repeat post; and the release posts '
        'the explicit gesture 3 (0x10a03) at release+4 with NO gesture 0 anywhere (the '
        'long-press single-click trap negative example holds, and the gesture 3 '
        'release-confirm slot is observed for the first time). Honest boundaries are the '
        'route-S ones: '
        'S19 dispatch and handler 0x60252c6 stay unreached in this world (route BW executed '
        'them from the handler entry with field-matching payloads); interrupts frozen during '
        'the direct calls; every conclusion stays conditional pass / derived-not-measured')
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
        'this init path at boot is unproven (known decode gaps), so every step past 177,491 in '
        'that scenario (including the pmu_task scheduler round trip, the M2P message-area writes, '
        'the 300,240 LRCT NUM boundary, the trim-result probe consumption, the 301,097 '
        'TIMER0-clock-source stop and the 301,540 STD12M pass-through to the LP-timer '
        'CON2 readback boundary) '
        'is a CONDITIONAL PASS that must not be reported as verified; the '
        'crc_bankb_erased_fixture base tier maps the same descriptor bytes as a read-only '
        'derived-candidate region (not the conditional fixture), consumes them for real, and '
        'honestly stops at 299,063 @ 0x12b0c2 on the fifth (masked) core-tick overflow CNT read '
        'after four real core-tick IRQ3 deliveries (core 0)')
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
    report['ancif_official_resources_fixture']=(
        'official-resource-content assumption (NOT a device measurement): the flash-top '
        'sdfile app-partition file headers ANCIF [0x61fbfe0,0x61fc0e0) and ANCIF1 '
        '[0x61fc0e0,0x61fcba8) are mapped read-only to the official UFW outer-layer '
        'resource bytes (type-50 entries at UFW offsets 0x102120/0x102220, outer CRC, '
        'inner CRC, magic 0x9881/0x98c1 and embedded names ANCGAIN01/ANCCOEF01 all '
        'verified against the consumer 0x606c54e parse expectations). A real device '
        'may hold per-unit calibration differences in this reserved area (usb-recovery '
        'measured layout marks it 保留校准); every conclusion past this boundary is '
        'conditional on the official-content assumption. ANCIF1 maps its 0xac8 valid '
        'bytes only — the declared-but-unused remainder of the 0xf00 area stays '
        'strictly unmapped.')

    report['ton_official_partition_pair_fixture']=(
        'official-resource-content assumption (NOT a device measurement; codex '
        'verdict (a) wording): this scenario assumes the target TON/TON1 '
        'partitions carry the designated official-UFW-extracted resource '
        'contents in the firmware SFC plaintext view, mapped by the current '
        'sdfile address translation. TON [0x61defe0,0x61ea790) (0xb7b0 valid '
        'bytes, container name tone_zh) and TON1 [0x61eafe0,0x61fb320) '
        '(0x10340, tone_en) are mapped read-only to the official UFW outer '
        'type-50 container bytes (UFW offsets 0xe6620/0xf1de0); every mapped '
        'byte comes from the official artifact — SHA256 three-way recompute '
        'agreed (in-tree extraction == independent codex recompute == '
        'embedded constants) and container/directory/file CRCs verify at '
        'mount, never synthesized. The padded tails and the declared-but-'
        'unused partition capacity (0xc000/0x11000) are NOT mapped; the '
        'window gaps stay strictly unmapped. The contents of the remaining '
        'partition capacity are not assumed, and the current device resources '
        'have NOT been verified against the official contents (usb-recovery '
        'measured layout marks TON/TON1 保留; the real-device read window '
        'would be [0x1ed000,0x1fe000) in device-flash coordinates, a later '
        'authorization-gated alignment batch). Every conclusion past this '
        'boundary is conditional / derived-not-measured and is not a '
        'real-device playback verification.')

    report['jlstream_kick_param2_unknown_release_fixture']=(
        'single-slot unknown-word release assumption (NOT a device '
        'measurement; route EVIMPL, codex decision (a)-1 with the '
        'five-element context guard): the 41,909,506 stop 0x60869ba '
        '(ld.w.l r6=[r10+8], encoding d0 ec a8 60) reads the jlstream '
        'task-stack message slot whose MMU translation is exactly '
        '[0x18ca64,0x18ca68). The release is NOT a physical-address '
        'exemption: every load re-validates the jlstream task instance '
        '(bound at the install site 0x6065f9e — the task body entry '
        '0x6086980 never executes, the OS preloads the initial GPR frame '
        'and first-dispatches into get-event; entry is the disassembly '
        'anchor only), the pinned PC and bytes, r10==sp+24, the '
        'TCB-described 0xC00-byte stack window, and a writable RAM region. '
        'The value is an UnknownWord built from the actual known bytes '
        'with physical-byte origins (never zero-filled; the slot stays '
        'uninitialized — a later real message write answers the true '
        'value, a partial write keeps the remaining bytes '
        'unknown-with-origin). Consumption stays fail-closed (cmd 4..12 '
        'arms stop at 0x60869c6 at the latest, unit-pinned). The '
        'equivalence wording is scoped to the known-cmd0 synchronous '
        'fragment and derived from disassembly; it does not imply silicon '
        'reached the same state. Neighboring slots, other PCs, other task '
        'instances, changed mappings, ROM/MMIO windows and instruction '
        'fetch never receive source permission.')

    report['jl_rand_seed_fixture']=(
        'synthetic-not-measured entropy assumption (NOT a device measurement): '
        'JL_RAND->R64L at 0xfd3b00 is mapped read-only to a frozen 4-byte little-endian '
        'sample (default 0x12345678); every read returns that same constant (policy='
        'constant_on_every_read), and no claim is made about the real device value, '
        'distribution, read-to-read correlation, or update mechanism — the reference SDK '
        '(br28.h JL_RAND_TypeDef {__RO u32 R64L; __RO u32 R64H}, cpu.h rand32()) documents '
        'no entropy mechanism. R64H at 0xfd3b04 stays strictly unmapped (zero direct '
        'constant loads and no reviewed-path access). Degenerate seeds 0/0x7fffffff/'
        '0xfffffffe are LCG fixed points (all-zero MAC) and are avoided by the default; '
        'a fixed seed can also stall future random-slot-allocation consumers (e.g. '
        '0x6055f68 R64L%79 retry loop, beyond the current frontier).')

    report['jl_eq_init_write_records_fixture']=(
        'write-only word records (NOT a device measurement and NOT a silicon '
        'WO property claim): JL_EQ->CON0 at 0xfe1500 and JL_EQ->FLT_ADR at '
        '0xfe151c (br28.h JL_EQ = hs_base + map_adr(0x15,0), JL_EQ_TypeDef all '
        '__RW __u32 — the layout locates the words, it does not evidence '
        'readback or reset semantics) accept exactly the two unconditional '
        'whole-word stores of the EQ init function 0x6072b24: '
        '0x6072b92 [CON0]=0 (the 29,215,313 stop word) and 0x6072bae '
        '[FLT_ADR]=the actually-allocated 640B filter-buffer pointer. The '
        '0x6072b68 bnez targets 0x6072b90 and skips only the allocation plus '
        'the first ROM memset — BOTH writes execute either way (codex '
        'correction of the route-PARKPRE observation-note branch description). '
        'Reads of these words never happened on this prefix and stay strictly '
        'stopped under the explicit kind jl_eq_init_read_not_modeled; the six '
        'unwritten declared members (CON1/CON2/CON3/DATAI_ADR/DATAO_ADR/'
        'DATA_LEN = 0xfe1504..0xfe1518) and 0xfe1520 onward stay unmapped. '
        'The JL_EQ base is data-driven (app.bin 0xfe1500 appears only at .data '
        '0x60c3748, the descriptor template copied into the RAM instance table '
        '0x124c40, loaded through the instance-pointer slot [0x12727C]+16 — '
        'immune to li-constant sweeps, the S19MAP #7 miss root cause). No EQ '
        'filtering, DMA, IRQ, coefficient loading or audio processing is '
        'modeled — this releases the EQ initialization writes only, it does '
        'NOT verify any EQ function or audio processing.')

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
    p.add_argument('--report',type=Path,required=True)
    p.add_argument('--with-d1-window',action='store_true',
                   help='append the D1 acceptance window scenario (d1_acceptance_window: park '
                        '+ N model steps observed; emits d1_window keys and exits nonzero unless '
                        'the D1 gate passes) — default off keeps every legacy report identical')
    p.add_argument('--d1-window-steps',type=int,default=None,
                   help='explicit D1 window N in model steps (overrides the formula and skips '
                        'the H8 auto-extension; PR2SPEC §2.3). D1RUN note: the conditional '
                        'frontier stop sits ~12M model steps after W_park (routes WDFEED and '
                        'STOREG3 moved it past the crossed watchdog point to 41,847,303), so '
                        'the formula N '
                        '(S_tick+160k) crosses it — an explicit N inside the frontier is the '
                        'workable window until a release route moves the stop back inside')
    args=p.parse_args()
    rom=args.rom.read_bytes();check_rom(rom);module=upstream();app=application(args.firmware);header=package_header(args.firmware)
    image=flash_image(args.firmware)
    # 两个共享栈场景的开关集从 frontier_registry 单点派生(开关/依赖/前沿
    # 契约的单一数据源;analyze_image_trace 的 flag 字典与 freshness 钉子、
    # test_image_trace_diff 的期望值同源派生)。其余场景(legacy 八场景的
    # 嵌套前缀与 probe 专用探路变体)保持手写,不进共享栈。
    crc_bankb_flags=scenario_flags('crc_bankb')
    conditional_frontier_flags=scenario_flags('p11_heap_experimental',sys.modules[__name__])
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
                     irq_case(module,app,header,rom,PROBE_SCENARIO_NAMES['crc_bankb'],
                              **crc_bankb_flags,flash_image_bytes=image),
                     # 显式关开关负例:core_tick_irq3_delivery 提入 base 后,
                     # 基座场景自然交付;此负例显式关掉交付,逐字段复现旧前沿
                     # 177,491 @ 0x12ba42(核心 tick IRQ3 首溢出 terminal)。
                     irq_case(module,app,header,rom,'crc_bankb_no_delivery_negative',
                              **{**crc_bankb_flags,'core_tick_irq3_delivery':False},
                              flash_image_bytes=image),
                     irq_case(module,app,header,rom,PROBE_SCENARIO_NAMES['p11_heap_experimental'],
                              **conditional_frontier_flags,flash_image_bytes=image),
                     # 路 SRCSYNC6:SRC 硬件游标进度新实验臂——条件前沿栈全量 +
                     # src_sync_cursor_progress(具名 (c,p,W) 假设默认值:一次消费
                     # 全部剩余/1:1 产出/32 位样本)。IDAT_LEN 递减到 0 后 SRC 循环
                     # 退出,链继续走到下一个诚实边界(可能停在 DAC/DMA 或更后);
                     # 新停点由实跑取得,本臂不重钉 p11_heap_experimental 前沿
                     # (旧臂零消费对照 139,947,345 @ 0x6064220 保持不变)。
                     irq_case(module,app,header,rom,'src_sync_cursor_progress_probe',
                              **{**conditional_frontier_flags,'src_sync_cursor_progress':True},
                              flash_image_bytes=image),
                     # 路 DACDMAREAD(codex 202611211512 方案):在 SRCSYNC6 臂上
                     # 追加 DAC DMA 配置五死读放行。旧臂 31,391,166 @ 0x606b2b2
                     # 逐字节不变(开关关);开五读的臂继续走到下一墙(0x606b2bc
                     # r0=4 覆盖后,或 0x606b2ec/2f2 的 LEN 活读——活读到达时再扩,
                     # 本次若撞活读诚实停并记录)。sites=4 负例只放行四字配置读、
                     # 钉住第五读 SWN,臂必须停在 0x606b2ba。
                     irq_case(module,app,header,rom,'src_sync_cursor_progress_dacdma_probe',
                              **{**conditional_frontier_flags,'src_sync_cursor_progress':True,
                                 'jl_audio_dac_dma_setup_discarded_reads':True},
                              flash_image_bytes=image),
                     irq_case(module,app,header,rom,'src_sync_cursor_progress_dacdma_negative',
                              **{**conditional_frontier_flags,'src_sync_cursor_progress':True,
                                 'jl_audio_dac_dma_setup_discarded_reads':True,
                                 'jl_audio_dac_dma_setup_discarded_sites':4},
                              flash_image_bytes=image),
                     # 路 DACDMALEN(codex 202611211512 方案 §3「活读到达时再扩」):
                     # 在 DACDMA 五死读放行臂上追加 LEN 活读回读(0x606ddc4/cc 钳位
                     # 比较首读+then 臂再读)。旧臂 31,391,419 @ 0x606ddc4 逐字节不变
                     # (len_readback 开关关);开活读回读的臂越过 0x606ddc4 继续走到
                     # 下一个诚实边界(0x606de56 写 PNS 0xfe050c 未映射,或更后);
                     # 新停点由实跑取得,本臂不重钉 dacdma_probe 前沿。
                     irq_case(module,app,header,rom,'src_sync_cursor_progress_dacdmalen_probe',
                              **{**conditional_frontier_flags,'src_sync_cursor_progress':True,
                                 'jl_audio_dac_dma_setup_discarded_reads':True,
                                 'jl_audio_dac_dma_len_readback':True},
                              flash_image_bytes=image),
                     # 路 DACDMAPNS(codex 202611211512 方案 §4 优先级 1 实跑落地):
                     # 在 DACDMALEN 臂上追加 PNS(0xfe050c)写侧 write-only 记录放行。
                     # 旧臂 31,391,525 @ 0x606de56 逐字节不变(pns_write 开关关);开
                     # PNS 写的臂越过 0x606de56(PNS 写 0x60 记录 1 次)+17,824 步停在
                     # 31,409,349 @ 0x6071560——PCM 数据相生产路径 0x607152c 体内
                     # 首读 [r12+8]=LEN(0xfe0508)算差,kind=jl_audio_dac_dma_read_
                     # not_modeled(非 len_readback 两站点 0x606ddc4/cc,活读回落普通
                     # 严格路径);下一墙按 codex §4 优先级 2 是 0x6071564 读 SWN 或
                     # 0x607169e 活读 SWP;本臂不重钉 dacdmalen_probe 前沿。
                     irq_case(module,app,header,rom,'src_sync_cursor_progress_dacdmapns_probe',
                              **{**conditional_frontier_flags,'src_sync_cursor_progress':True,
                                 'jl_audio_dac_dma_setup_discarded_reads':True,
                                 'jl_audio_dac_dma_len_readback':True,
                                 'jl_audio_dac_dma_pns_write':True},
                              flash_image_bytes=image),
                     # 路 DACDMAPCM(codex 202611211512 方案 §4 优先级 2 实跑落地):
                     # 在 DACDMAPNS 臂上追加 PCM 数据相生产路径(0x607152c)读回+
                     # SWN 提交记录。旧臂 31,409,349 @ 0x6071560 逐字节不变(pcm_
                     # produce 开关关);开生产路径读回的臂越过 0x6071560/64(LEN、
                     # SWN 算差读)继续走到下一个诚实边界:实跑只放行 LEN=0x1e00
                     # (0x606b2b0 重写值)+SWN=0(具名假设)各一次,生产函数 0x607152c
                     # 在 helper 0x607da7e 处按 watermark 分流(本臂未走到 0x607169e
                     # SWP/0x607171a ADR/0x6071782 SWN 提交,提交数=0 消费数=0),
                     # 正臂 +9,331 步停在 31,418,680 @ 0x12cc48(喂狗函数 0x12cc7e
                     # 前身,P3_PCNT_SET0 0xf2c128 读,kind=unknown_reset_configuration_
                     # bits,known_mask=0xffff00ff——bit8-15 未建模,归下路复位配置域);
                     # 本臂不重钉 dacdmapns_probe 前沿。
                     irq_case(module,app,header,rom,'src_sync_cursor_progress_dacdmapcm_probe',
                              **{**conditional_frontier_flags,'src_sync_cursor_progress':True,
                                 'jl_audio_dac_dma_setup_discarded_reads':True,
                                 'jl_audio_dac_dma_len_readback':True,
                                 'jl_audio_dac_dma_pns_write':True,
                                 'jl_audio_dac_dma_pcm_produce':True},
                              flash_image_bytes=image),
                     # 路 DACDMAPCNT:在 DACDMAPCM 臂上追加 P3_PCNT_SET0(0xf2c128)
                     # bit8-15 读回放行。旧臂 31,418,680 @ 0x12cc48 逐字节不变(pcnt_
                     # readback 开关关);开读回的臂越过 0x12cc48(P3_PCNT_SET0 读 0
                     # 合成初始位)+2 步停在 31,418,682 @ 0x12cc54——0x12cc54/56
                     # 并行对读 P3_VLVD_CON 0xf2c044,kind=canary_readback_after_
                     # write_not_modeled(0xf2c044 在本稳态前缀已被先写/先 RMW,
                     # 首读 fixture 只服务未写过的首次读,写后读严格停);0x12cc64
                     # 写回 0xf2c128 与 0x12cc6a 读 0xf2c280=P3_PR_PWR 都在更后
                     # 未到达。下一墙归金丝雀读后读域(需要复位配置域/金丝雀域
                     # 专门参谋),本臂不重钉 dacdmapcm_probe 前沿。
                     irq_case(module,app,header,rom,'src_sync_cursor_progress_dacdmapcnt_probe',
                              **{**conditional_frontier_flags,'src_sync_cursor_progress':True,
                                 'jl_audio_dac_dma_setup_discarded_reads':True,
                                 'jl_audio_dac_dma_len_readback':True,
                                 'jl_audio_dac_dma_pns_write':True,
                                 'jl_audio_dac_dma_pcm_produce':True,
                                 'p3_pcnt_set0_readback':True},
                              flash_image_bytes=image),
                     # 路 DACDMAPCNT 续:在 DACDMAPCNT 臂上追加 P3_VLVD bit2
                     # 写后读回放行。旧臂 31,418,682 @ 0x12cc54 逐字节不变
                     # (vlvd_bit2_writeback 开关关);开读回的臂越过 0x12cc54/56
                     # (VLVD bit2=0 折进 PCNT_SET0 写值)继续走到下一墙(优先
                     # 0x12cc6a 读 0xf2c280=P3_PR_PWR,预计 unmapped_memory;
                     # 若越过进入喂狗函数 0x12cc7e 如实记录);本臂不重钉
                     # dacdmapcnt_probe 前沿。
                     irq_case(module,app,header,rom,'src_sync_cursor_progress_dacdmapcnt_vlvd_probe',
                              **{**conditional_frontier_flags,'src_sync_cursor_progress':True,
                                 'jl_audio_dac_dma_setup_discarded_reads':True,
                                 'jl_audio_dac_dma_len_readback':True,
                                 'jl_audio_dac_dma_pns_write':True,
                                 'jl_audio_dac_dma_pcm_produce':True,
                                 'p3_pcnt_set0_readback':True,
                                 'p3_vlvd_bit2_writeback_readback':True},
                              flash_image_bytes=image),
                     # 路 DACDMAPCNT 续二:在 VLVD 臂上追加 P3_PR_PWR(0xf2c280)
                     # 软复位读点保留位 fixture=0(显式假设「本次 12 个保留位为
                     # 零」)。旧臂 31,418,688 @ 0x12cc6a 逐字节不变(pr_pwr 开关
                     # None=关);开 fixture 的臂越过 0x12cc6a(读值=0)→ 真实指令
                     # &0x3fcf|0x30 → 0x12cc78 写回 0x30,停因 p33_soft_reset_
                     # requested(停在复位交付边界,写值=固件自己的执行结果)。
                     # 第二组同址访问 0x12cffc/0x12d008 在更后未到达,守卫 PC
                     # 不符继续严格停。本臂不重钉 vlvd_probe 前沿。
                     irq_case(module,app,header,rom,'src_sync_cursor_progress_dacdmapcnt_vlvd_prpwr0_probe',
                              **{**conditional_frontier_flags,'src_sync_cursor_progress':True,
                                 'jl_audio_dac_dma_setup_discarded_reads':True,
                                 'jl_audio_dac_dma_len_readback':True,
                                 'jl_audio_dac_dma_pns_write':True,
                                 'jl_audio_dac_dma_pcm_produce':True,
                                 'p3_pcnt_set0_readback':True,
                                 'p3_vlvd_bit2_writeback_readback':True,
                                 'p3_pr_pwr_retained_bits':0},
                              flash_image_bytes=image),
                     # 同臂 fixture=1 反例:读值保留位最低位=1 → 0x12cc78 写回
                     # (1&0x3fcf)|0x30=0x31,其余与 prpwr0 臂一致。对照钉死
                     # 「写值取决于这 12 个保留位」这一核心结论(控制流对读值
                     # 不敏感,写出的总线数据敏感)。
                     irq_case(module,app,header,rom,'src_sync_cursor_progress_dacdmapcnt_vlvd_prpwr1_probe',
                              **{**conditional_frontier_flags,'src_sync_cursor_progress':True,
                                 'jl_audio_dac_dma_setup_discarded_reads':True,
                                 'jl_audio_dac_dma_len_readback':True,
                                 'jl_audio_dac_dma_pns_write':True,
                                 'jl_audio_dac_dma_pcm_produce':True,
                                 'p3_pcnt_set0_readback':True,
                                 'p3_vlvd_bit2_writeback_readback':True,
                                 'p3_pr_pwr_retained_bits':1},
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
                              core_tick_prd_readback=True,
                              core_tick_cnt_held_snapshot=True,
                              key_chain_scan=True,
                              flash_image_bytes=image),
                     irq_case(module,app,header,rom,'key_chain_matrix_scan',
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
                              fd31_adc_res_named_values=KEY_CHAIN_MATRIX_RES_NAMED_VALUES,
                              p33_analog_tail=True,
                              p3_ana_read_ldoin_low=True,
                              gpcnt_con_readback=True,
                              jl_clock_dcck=True,
                              jl_uart_con=True,
                              jl_uart_init_writes=True,
                              subword_stores=True,word_store_sp_l=True,
                              timer0_irq4_delivery=True,
                              core_tick_irq3_delivery=True,
                              core_tick_prd_readback=True,
                              core_tick_cnt_held_snapshot=True,
                              key_chain_matrix=True,
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
                              core_tick_irq3_delivery=True,
                              core_tick_prd_readback=True,
                              core_tick_cnt_held_snapshot=True,
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
                              core_tick_irq3_delivery=True,
                              core_tick_prd_readback=True,
                              core_tick_cnt_held_snapshot=True,
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
                              core_tick_irq3_delivery=True,core_tick_irq3_probe_steps=15640,
                              core_tick_prd_readback=True,
                              core_tick_cnt_held_snapshot=True,
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
                              core_tick_irq3_delivery=True,core_tick_irq3_probe_steps=15640,
                              core_tick_prd_readback=True,
                              core_tick_cnt_held_snapshot=True,
                              core_tick_irq3_masked=True,
                              flash_image_bytes=image)]}
    for name,path,digest in [('repaired_candidate',args.repaired_candidate,REPAIRED_SHA),('archived_failed_r2',args.failed_r2,FAILED_SHA)]:
        if path is None:continue
        candidate,readonly=candidate_view(path,digest,args.firmware)
        r=irq_case(module,candidate,header,rom,name,entry=True,readonly=readonly);r['ufw_sha256']=digest
        report.setdefault('candidate_prefixes',[]).append(r)
    # D1 验收观察窗场景行(路 D1RUN/PR-2;PR2SPEC §4.3 运行集合决策 (ii)):
    # --with-d1-window 旗标开时才构造(默认关=legacy 报告逐字节不变)。场景
    # =条件前沿栈全量(event_clock/Timer0 IRQ4/core-tick/repeat 均已随历路
    # 提层入栈,PR2SPEC 场景行的 event_clock=True 显式传参因重复 kwarg 省略)
    # + d1_acceptance 观察器;budget=41M(d1 分支覆盖 flag 推导)。
    if args.with_d1_window:
        d1_kwargs={'d1_acceptance':True}
        if args.d1_window_steps is not None:d1_kwargs['d1_window_steps']=args.d1_window_steps
        report['scenarios'].append(
            irq_case(module,app,header,rom,'d1_acceptance_window',
                     **scenario_flags('p11_heap_experimental',sys.modules[__name__]),
                     **d1_kwargs,flash_image_bytes=image))
    check(report,args.firmware)
    # 报告先落盘再做 D1 正式验收(D1RUN 归因:验收崩溃不能吞报告——r2b 轮
    # run_all 在 jiffies 判定面抛 TypeError 导致 write_text 未执行,checker
    # 只能消费上一轮旧文件)。
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(report,indent=2)+'\n')
    # D1 正式验收入口(PR2SPEC §4.5):五断言机检消费同轮报告的 d1_window 键,
    # verdicts 回填场景行,d1_gate 非 pass → 以非零码退出(checker CLI 自身
    # 退出码恒 0,非零退出义务在此)。
    d1_gate=None
    if args.with_d1_window:
        from check_d1_acceptance import run_all
        row=next(s for s in report['scenarios'] if s.get('name')=='d1_acceptance_window')
        result=run_all(row)
        row.setdefault('d1_window',{})['verdicts']=result
        d1_gate=result['d1_gate']
        report['d1_gate']=d1_gate
        args.report.write_text(json.dumps(report,indent=2)+'\n')
    if d1_gate is not None and d1_gate!='pass':
        sys.exit(1)


if __name__=='__main__':main()
