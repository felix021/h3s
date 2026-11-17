"""Assemble and implant the hearing-aid L1 activation payload + ANC-key hooks.

Used only by the builder. The independent verifier must not import this module.

Route T / sim-l1image (2026-11-13). Two inputs assembled at build time into one
self-contained appended segment (docs/hearing-aid-l1-payload.md §2/§3/§9):

  - tools/hearing-aid/mic_effect_open.S  — the route-L activation payload
    (41 insns / 134B, zero relocations; MIC_EFX_SLOT .equ is patched here to
    the real appended-slot address, the only layout-dependent constant);
  - tools/hearing-aid/mic_effect_hook.S  — the two TRANS-branch replay stubs.

Route Y / sim-l1closeimg (2026-11-14) adds the close direction
(--hearing-aid-l1-close, implies --hearing-aid-l1-patch;
docs/hearing-aid-l1-payload.md §10/§11):

  - tools/hearing-aid/mic_effect_close.S — the route-X close payload
    (20 insns / 64B, idempotent: slot==0 -> early return 0 -> stop(0) ->
    release -> clear slot -> event_notify(11, "mic_effect"));
  - tools/hearing-aid/mic_effect_close_hook.S — the four ON/OFF leave stubs.

Route AQ / sim-r4hook (2026-11-14) adds the R4 App-command hook
(--r4-keyhook, implies --hearing-aid-l1-slotram; docs/r4-command-hook.md):
a minimal RCSP custom-command setter payload (tools/r4cmd/keystore_hook.S,
27 insns / 78B, zero relocations) appended after the L1 stubs, wired into
the command registry by filling the cmdid 0x30 entry's null setter word —
the only edit inside the stock data area beyond the L1 fence/window edits.

Route CT / sim-slotram (2026-11-14) moves the data slot out of the flash
appended segment into boot-zeroed BSS RAM (--hearing-aid-l1-slotram, implies
--hearing-aid-l1-close; docs §10.4/§12). The close chain functionally depends
on the slot holding the stream handle written by open (§10.4 方案 B ruled out
every framework-side lookup), and a plain st.w cannot program the SFC-mapped
flash region, so the route-Y flash-tail slot leaves close permanently
early-exited on real hardware — the RAM slot is what makes the close half of
the candidate actually functional, not just hardening. The appended segment
drops the trailing 4B slot entirely (364B code, file 397B, fence top
0x060E31E0). Slot address evidence (five classes, machine-rechecked by
tools/analyze_hearing_aid_slot_address.py): inside the really-executed boot
BSS zero loop [0x125320, 0x12a62c) (docs/boot-handoff.md digests); zero
li.u32 anchors / provenance-traced accesses / dynamic-index bases within
+-0x400 / aligned-or-unaligned flash pointer-words into the hole, across
app.bin + RAM-code source + ROM; below the QCY giant struct base 0x1265C0
(fields proven to reach +0x2CE6). Residual classes that no static scan can
close (runtime-computed bulk destinations, ROM-resident paths beyond the
captured window) stay documented in §12.

Implant edits, all byte-asserted against the official 0.1.1.8 layout:
  1. append a JLFS file at the same geometry as the EQ payload precedent
     (header at flash 0xE5073, 0xff marker, code at runtime 0x060E3074,
     4-byte zero data slot at the segment tail);
  2. replace the two 8B TRANS windows (0x0600dc7e / 0x0600dca4, each exactly
     `call 0x0600c2aa; r0=[r0+116]`, the only branch landing there is the
     pinned TRANS branch) with `r6=<stub>; goto r6`;
  3. close variant: also replace the four 8B ON/OFF windows (ON-1 0x0600db9a /
     OFF-1 0x0600dc74 / ON-2 0x0600dbc4 / OFF-2 0x0600dc9a, each exactly
     `call 0x0600c2aa` + the ON(+112)/OFF(+120) apply-slot load) the same
     way — leaving transparency (either chain) first closes the hearing
     aid, then replays the original slot load and returns at window+8;
  4. extend the debug_init LIM_PC0 upper-limit immediate at 0x06034e98 from
     0x060c3338 to the appended-file end (route O proved the stock fence
     rejects payload fetches);
  5. the caller then re-runs the JLFS/app-area/boot-info/UFW CRC chain.

UNTESTED offline candidate. Never flashed; the ANC-key loop where the hooks
live has not been reached by any simulation frontier.
"""
import hashlib
from pathlib import Path
import re
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools/jl-misctools/firmware'))
sys.path.insert(0, str(ROOT / 'tools'))
from jltech.crc import jl_crc16
from jltech.cipher import jl_sfc_cipher, jl_enc_cipher
import pi32v2_asm as ASM

APP_BASE = 0x06000100          # runtime base of app.bin
APP_DATA = 0x2120              # flash offset of app.bin payload
APP_SIZE = 842654
OLD_APP_END = 0xE5073          # first free append offset (EQ precedent)
LAST_HEADER = 0xE4EF3          # config.dat JLFS header (current last file)
PAYLOAD_ADDRESS = 0x060E3074   # OLD_APP_END + 33 (header 32 + 0xff marker), 4-aligned
FENCE_SITE = 0x06034e98        # debug_init `li.u32 r?, LIM_PC0 upper limit`
FENCE_ORIGINAL = bytes.fromhex('c1ff38330c06')   # loads 0x060c3338
HOOK_FILE = 'mic_effect.bin'
# (runtime window start, original 8B bytes, chain epilogue). Original windows
# disassemble as `call.l 0x0600c2aa` + `ld.w.l r0=[r0+116]` (TRANS apply slot);
# route L pinned the only branch landing at each start (0x0600db92/0x0600dbbc)
# and a full-app scan found no branch into either interior.
WINDOWS = [
    ('A', 0x0600dc7e, bytes.fromhex('bfea14f3d0ec0407'), 0x0600dc8c),
    ('B', 0x0600dca4, bytes.fromhex('bfea01f3d0ec0407'), 0x0600dcd2),
]
ANC_CTX_GET = 0x0600c2aa        # pure-read leaf: writes r0-r2 only
# Route-Y leave windows (§10.5): (tag, start, original 8B, mode-slot offset,
# stub label). Entry is the pinned mode branch (OFF) or plain fall-through
# (ON); the full-app branch-landing scan found no landing inside any window
# and the pop at 0x0600dc98 (4 live early-exit landings) stays outside.
CLOSE_WINDOWS = [
    ('ON-1', 0x0600db9a, bytes.fromhex('bfea86f3d0ec0007'), 112, 'leave_on1'),
    ('OFF-1', 0x0600dc74, bytes.fromhex('bfea19f3d0ec0807'), 120, 'leave_off1'),
    ('ON-2', 0x0600dbc4, bytes.fromhex('bfea71f3d0ec0057'), 112, 'leave_on2'),
    ('OFF-2', 0x0600dc9a, bytes.fromhex('bfea06f3d0ec0807'), 120, 'leave_off2'),
]
STUB_INSN_IDS = ['li.u32', 'call.r', 'ld.w.l', 'mov.rr', 'li.u32',
                 'call.r', 'call.s', 'li.u32', 'goto.r']
# The fixed (156B) open payload pushes trans_branch_2's `call mic_effect_open`
# beyond call.s range, so the assembler emits call.l there (route L1KEY).
STUB_B_INSN_IDS = ['li.u32', 'call.r', 'ld.w.l', 'mov.rr', 'li.u32',
                   'call.r', 'call.l', 'li.u32', 'goto.r']
LEAVE_INSN_IDS = ['li.u32', 'call.r', 'ld.w.l', 'call.s', 'mov.rr',
                  'li.u32', 'goto.r']
LEAVE_ON2_INSN_IDS = ['li.u32', 'call.r', 'ld.w.l', 'call.s', 'li.u32',
                      'call.r', 'li.u32', 'goto.r']
# Route L1KEY (docs/hearing-aid-l1-key7.md): the cmd7_toggle stub shape —
# slot guard -> ANC OFF request (db3c(1,1)) -> open, else close -> r4=1 ->
# goto the handler's "handled" return. Exactly the disassembled 13-insn stub.
TOGGLE_INSN_IDS = ['li.u32', 'ld.w', 'bnez', 'li.u8', 'li.u8', 'li.u32',
                   'call.r', 'call.s', 'goto.s', 'call.s', 'li.u8', 'li.u32',
                   'goto.r']
# Route HAL3 (docs/hearing-aid-l3-mpo.md): the cmd7_toggle_mpo stub — the
# L1KEY stub plus two instructions on the open path (open != 0 -> done;
# call apply_mpo). In the composed segment the open entry sits >240B away, so
# its call (and close's) take the wide call.l form (same reason as
# STUB_B_INSN_IDS); pinned by the L3 tests.
MPO_TOGGLE_INSN_IDS = ['li.u32', 'ld.w', 'bnez', 'li.u8', 'li.u8', 'li.u32',
                       'call.r', 'call.l', 'bnez', 'call.s', 'goto.s',
                       'call.l', 'li.u8', 'li.u32', 'goto.r']
# apply_mpo code shape (43 insns / 128B): prologue -> get_config(30,sp,8) ->
# magic/ver/rsv byte guards -> M bit-range guards -> 76B template copy loop ->
# three knee stores -> jlstream_set_node_param(0xdefe, name, buf, 76) ->
# return passthrough.
MPO_INSN_IDS = ['push.rets_rl', 'add.sp_i', 'mov.r_sr', 'li.u8', 'mov.rr',
                'li.u8', 'li.u32', 'call.r', 'bne.ri', 'ld.bu', 'bne.ri',
                'ld.bu', 'bne.ri', 'ld.bu', 'bne.ri', 'ld.bu', 'bnez', 'ld.w',
                'li.u32', 'ble.rr', 'li.u32', 'bge.rr', 'add.rri', 'li.u32',
                'li.u8', 'ld.w.pin', 'st.w.pin', 'add.ri.neg7', 'bnez',
                'add.rri', 'st.w', 'st.w', 'st.w', 'li.u32', 'li.u32',
                'mov.rr', 'li.u8', 'li.u32', 'call.r', 'goto.s', 'li.u8',
                'add.sp_i', 'pop.pc_rl']
MPO_CODE_BYTES = 128
# The official Drc1Eff 76B tool_set (stream.bin sha 19c9deeb §5 @0x1038) with
# exactly one edit: th[2].in (+24) 110.3f -> 120.0f. Cross-checked against the
# official image by test_hearing_aid_l3_mpo_payload.py.
MPO_TEMPLATE_EXPECTED = bytes.fromhex(
    '00000000'                      # +0  is_bypass = 0
    '6400c800'                      # +4  attack=100 / release=200
    '9a99993e' '9a99993e'           # +8  th[0] = (0.3f, 0.3f)
    '9a99b442' '9a99b442'           # +16 th[1] = (90.3f, 90.3f) [runtime M]
    '0000f042' '9a99dc42'           # +24 th[2] = (120.0f raised, 110.3f) [out = M]
    '9a99b442' '9a99b442'           # +32 th[3] = (90.3f, 90.3f) official residue
    '9a99b442' '9a99b442'           # +40 th[4]
    '9a99b442' '9a99b442'           # +48 th[5]
    '00000000' '00000000'           # +56/+60 inputgain/outputgain = 0
    '0000c841'                      # +64 rms_time = 25.0f
    '03000001' '00000100'           # +68 tnum=3/rms/PEAK/TWOPOINT; +72 holdtime/resv
)
MPO_NAME_EXPECTED = b'Drc1Eff\x00' + b'\x00' * 8   # 16B name field
MPO_API_ANCHORS = [0x06000f88, 0x0607538a]         # get_config, set_node_param
# Route HAL4 (docs/hearing-aid-l4-eq.md): the EQ ten-band consumption payload
# (reads VM key 31, gates the whole 88B packet, sends ten EQ_SEG 44B packets to
# "Eq1Eff" via jlstream_set_node_param). Pinned by the L4 tests.
EQ_SOURCE = ROOT / 'tools/hearing-aid/apply_eq.S'
EQ_HOOK_SOURCE = ROOT / 'tools/hearing-aid/mic_effect_toggle_l4_hook.S'
# apply_eq code shape (62 insns / 180B): prologue -> get_config(31,sp,88) ->
# two header-word gates -> per-seg {freq 1..21599, |gain|<=40.0f} gate loop ->
# ten-segment send loop (template copy -> idx/freq/gain patch ->
# set_node_param(0x737b,"Eq1Eff",pkt,44) -> r0==1 check) -> 0/1/2 return.
EQ_INSN_IDS = ['push.rets_rl', 'add.sp_i', 'mov.r_sr', 'li.u8', 'mov.rr',
               'li.u8', 'li.u32', 'call.r', 'bne.ri', 'ld.w', 'li.u32',
               'bne.rr', 'ld.w', 'li.u8', 'bne.rr', 'li.u32', 'li.s16',
               'add.rri', 'li.u8', 'ld.hu', 'beqz', 'bgt.rr', 'ld.w',
               'and.nmodimm.rri', 'bgt.rr', 'add.rri', 'add.ri.neg7', 'bnez',
               'li.u32', 'li.u32', 'li.zero_hi', 'add.s14.rri', 'li.u32',
               'li.u8', 'ld.w.pin', 'st.w.pin', 'add.ri.neg7', 'bnez',
               'add.s14.rri', 'st.h.l', 'lsl.rri.l', 'add.rrr', 'add.rri',
               'ld.hu', 'st.w', 'ld.w', 'st.w', 'li.s16', 'mov.rr', 'mov.rr',
               'li.u8', 'call.r', 'bne.ri', 'add.s14.rri', 'bne.ri', 'li.u8',
               'goto.s', 'li.u8', 'goto.s', 'li.u8', 'add.sp_i', 'pop.pc_rl']
EQ_CODE_BYTES = 180
# The EQ_SEG 44B packet template: u32 subcmd 7; 16B seg {index(0), iir=2,
# freq(0), gain(0), q=0.7f}; 24B fade {10, 100, 0, 0.1f, zeros}. Segment
# constants (iir/q) mirror the official Eq1Eff blob (stream.bin §5 @0xeac);
# fade constants mirror both firmware producer loops (0x06025096/0x06024f68).
EQ_PKT_EXPECTED = bytes.fromhex(
    '07000000'                      # +0  subcmd 7 = EQ_SEG
    '0000' '0200'                   # +4  index(0) / iir_type=2 band_pass
    '00000000'                      # +8  freq (runtime)
    '00000000'                      # +12 gain (runtime)
    '3333333f'                      # +16 q = 0.7f
    '0a' '64' '0000'                # +20 fade {10, 100, 0}
    'cdcccc3d'                      # +24 fade step 0.1f
    '00000000' '00000000' '00000000' '00000000'   # +28..44
)
EQ_NAME_EXPECTED = b'Eq1Eff\x00' + b'\x00' * 9    # 16B name field
EQ_API_ANCHORS = [0x06000f88, 0x0607538a]         # get_config, set_node_param
EQ_HDR_WORD0 = 0x00024148          # {'H','A', ver 2, rsv 0}
EQ_GAIN_MAX_BITS = 0x42200000      # 40.0f bit pattern (magnitude bound)
EQ_NODE_UUID = 0x737b
EQ_IOCTL_OK = 1                    # eq ioctl 0x2000F routine return
# The cmd7_toggle_l4 stub: the HAL3 mpo stub plus one instruction on the open
# path (call apply_eq after apply_mpo). Same wide call.l forms (entries sit
# >240B away).
L4_TOGGLE_INSN_IDS = ['li.u32', 'ld.w', 'bnez', 'li.u8', 'li.u8', 'li.u32',
                      'call.r', 'call.l', 'bnez', 'call.l', 'call.l',
                      'goto.s', 'call.l', 'li.u8', 'li.u32', 'goto.r']
# Route L1KEY cmd 7 graft window: the double-click action-branch entry at
# 0x06025404 (jump table 0x06025382 byte 0x40). Original 8B = the publish-name
# setup `add.s14.rri r1=r8+1873; li.u8 r0=0; li.u8 r4=0`. A full-app pc-relative
# branch-landing scan (route L1KEY, 51,710 sites) found zero landings inside the
# window; the only incoming branch is the jump-table tbb byte itself (not a
# pc-relative branch, so it does not appear in the scan — the known entry).
KEY7_WINDOW = ('KEY7', 0x06025404, bytes.fromhex('01e1518740204420'))
HANDLER_RET = 0x0602530c        # handler "handled" return: r0=r4; pop.pc_rl l=9
ANC_DISPATCH = 0x0600db3c       # internal-mode dispatch: r0=1(OFF), r1=flag
PAYLOAD_SOURCE = ROOT / 'tools/hearing-aid/mic_effect_open.S'
HOOK_SOURCE = ROOT / 'tools/hearing-aid/mic_effect_hook.S'
CLOSE_SOURCE = ROOT / 'tools/hearing-aid/mic_effect_close.S'
CLOSE_HOOK_SOURCE = ROOT / 'tools/hearing-aid/mic_effect_close_hook.S'
TOGGLE_HOOK_SOURCE = ROOT / 'tools/hearing-aid/mic_effect_toggle_hook.S'
# Route HAL3 (docs/hearing-aid-l3-mpo.md): the MPO consumption payload (reads
# VM key 30, builds the wdrc 76B packet, calls jlstream_set_node_param) and the
# key7-toggle stub variant that invokes it after a successful open (codex a2:
# "saved now, applied on next hearing-aid start").
MPO_SOURCE = ROOT / 'tools/hearing-aid/apply_mpo.S'
MPO_HOOK_SOURCE = ROOT / 'tools/hearing-aid/mic_effect_toggle_mpo_hook.S'
# Route AQ (docs/r4-command-hook.md): the R4 key-store command payload + the
# registry slot it is wired into. The RCSP custom-command dispatch table
# [0x060a9b78, 0x060a9d1c) has 15 entries x 28B {+0 init, +4 getter, +8 setter,
# +20 restore, +24 cmdid} and NO cmdid-free entry; the dispatcher write path
# (0x0600f8b4/0x0600f932, route P) null-checks the setter word and silently
# exits when it is 0. cmdid 0x30's entry at 0x060a9c74 is a live read-only
# command (getter 0x06026060, not in the control panel or protocol notes)
# whose setter word 0x060a9c7c is exactly zero — filling it is strictly
# additive (a write to 0x30 is a no-op today). No static reference targets
# the entry (li.u32 + raw LE32 scans over app.bin, route AQ); the only
# consumers are the table-base walkers (dispatch 0x0600f85e / restore
# 0x0600f836 / boot-init 0x06024df4), which read +4/+20/+0 respectively and
# leave +8 to the write path alone.
R4_KEYHOOK_SOURCE = ROOT / 'tools/r4cmd/keystore_hook.S'
R4_REG_ENTRY = 0x060a9c74             # cmdid 0x30 entry base
R4_REG_SLOT = 0x060a9c7c              # entry+8, the null setter word
R4_REG_SLOT_ORIGINAL = b'\x00\x00\x00\x00'
R4_CMDID = 0x30
# Route CT RAM slot (docs §12): centre word of the statically dead 593B BSS
# hole [0x12544e, 0x12569f) — 294B/295B margins to the nearest referenced
# byte on either side. Must stay inside the boot BSS zero loop
# [0x125320, 0x12a62c) (a non-zeroed byte would make close stop() a garbage
# pointer on first use) and below the QCY struct base 0x1265C0.
# tools/analyze_hearing_aid_slot_address.py re-checks every evidence class.
RAM_SLOT = 0x00125574
BSS_ZERO_RANGE = (0x125320, 0x12a62c)     # really-executed boot zero loop
QCY_BASE = 0x1265c0                       # giant device struct base (→ +0x2CE6)


def _patch_slot(text, slot):
    patched = re.sub(r'\.equ MIC_EFX_SLOT, 0x[0-9a-fA-F]+',
                     f'.equ MIC_EFX_SLOT, 0x{slot:x}', text, count=1)
    if patched == text:
        raise ValueError('MIC_EFX_SLOT equ not found for patching')
    return patched


def _strip_close_section(text):
    """Drop the close payload's own .section line so it joins the open segment.

    Route X's source stays byte-identical (its standalone test assembles it
    with its own section); the composed segment must stay ONE section.
    """
    m = re.search(r'^[ \t]*\.section[ \t]+\.text\.mic_effect_close.*$\n?',
                  text, re.M)
    if m is None:
        raise ValueError('close payload section directive not found')
    stripped = text[:m.start()] + text[m.end():]
    if '.section' in stripped:
        raise ValueError('unexpected extra .section in close payload')
    return stripped


def _strip_r4_section(text):
    """Drop the R4 payload's own .section line so it joins the L1 segment.

    Route AQ's source stays byte-identical (its standalone test assembles it
    with its own section); the composed segment must stay ONE section.
    """
    m = re.search(r'^[ \t]*\.section[ \t]+\.text\.r4_keystore_hook.*$\n?',
                  text, re.M)
    if m is None:
        raise ValueError('R4 payload section directive not found')
    stripped = text[:m.start()] + text[m.end():]
    if '.section' in stripped:
        raise ValueError('unexpected extra .section in R4 payload')
    return stripped


def _patch_mpo_addrs(text, template, name):
    """Bake the segment-local rodata runtime addresses into the L3 payload.

    Same two-pass pattern as _patch_slot: the source carries placeholder
    .equ values; both the placeholder and every real address encode as the
    same wide li.u32, so the section layout is stable across passes.
    """
    for equ, value in (('MPO_TEMPLATE_ADDR', template), ('MPO_NAME_ADDR', name)):
        patched = re.sub(r'\.equ ' + equ + r', 0x[0-9a-fA-F]+',
                         f'.equ {equ}, 0x{value:x}', text, count=1)
        if patched == text:
            raise ValueError(f'{equ} equ not found for patching')
        text = patched
    return text


def _strip_mpo_sections(text):
    """Drop the L3 payload's .section lines (code + both rodata blobs).

    Route HAL3's source stays byte-identical for its standalone test; in the
    composed segment the template/name rodata join the single code section
    after apply_mpo's code. The code refers to them via MPO_*_ADDR .equ
    values (forward labels would relocations — the segment must stay at zero).
    """
    stripped = re.sub(r'^[ \t]*\.section[ \t]+\.(?:text\.apply_mpo'
                      r'|rodata\.mpo_template|rodata\.mpo_name).*$\n?',
                      '', text, flags=re.M)
    if '.section' in stripped:
        raise ValueError('unexpected extra .section in L3 payload')
    if len(stripped) == len(text):
        raise ValueError('no L3 section directive found to strip')
    return stripped


def _patch_eq_addrs(text, pkt, name):
    """Bake the segment-local EQ rodata runtime addresses into the L4 payload
    (same two-pass contract as _patch_mpo_addrs)."""
    for equ, value in (('EQ_PKT_ADDR', pkt), ('EQ_NAME_ADDR', name)):
        patched = re.sub(r'\.equ ' + equ + r',\s*0x[0-9a-fA-F]+',
                         f'.equ {equ}, 0x{value:x}', text, count=1)
        if patched == text:
            raise ValueError(f'{equ} equ not found for patching')
        text = patched
    return text


def _strip_eq_sections(text):
    """Drop the L4 payload's .section lines (code + both rodata blobs).

    Same contract as _strip_mpo_sections: the source stays byte-identical for
    its standalone test; the composed segment is ONE section and refers to
    the rodata via EQ_*_ADDR .equ values.
    """
    stripped = re.sub(r'^[ \t]*\.section[ \t]+\.(?:text\.apply_eq'
                      r'|rodata\.eq_pkt|rodata\.eq_name).*$', '', text, flags=re.M)
    if '.section' in stripped:
        raise ValueError('unexpected extra .section in L4 payload')
    if len(stripped) == len(text):
        raise ValueError('no L4 section directive found to strip')
    return stripped


def _compose(slot, close_hooks=False, r4_keyhook=False, key7_toggle=False,
             l3_mpo=False, mpo_addrs=None, l4_eq=False, eq_addrs=None):
    """Concatenate payload source (slot-patched) + hook stubs into one text.

    key7_toggle=True (route L1KEY) composes open + close + the cmd7_toggle
    stub and OMITS the ANC-key trans/leave stubs — the double-click toggle
    drives its own ANC OFF via db3c(1,1), and the ANC key keeps its stock
    OFF/ON/TRANS windows (no TRANS=HA, no ON/OFF close hooks).

    l3_mpo=True (route HAL3) composes open + close + the apply_mpo payload +
    the cmd7_toggle_mpo stub (open path additionally applies the latest VM
    key 30 MPO after a successful open). Same KEY7 graft window as L1KEY;
    mutually exclusive with key7_toggle (identical graft site).

    l4_eq=True (route HAL4) additionally inserts the apply_eq payload after
    the apply_mpo rodata and uses the cmd7_toggle_l4 stub (open path applies
    the latest VM key 31 ten-band EQ to Eq1Eff after apply_mpo). Implies
    l3_mpo; same KEY7 graft window.
    """
    if l3_mpo and key7_toggle:
        raise ValueError('l3_mpo and key7_toggle share the KEY7 graft window')
    if l4_eq and not l3_mpo:
        raise ValueError('l4_eq requires l3_mpo (route HAL4 is an L3 superset)')
    mpo_addrs = mpo_addrs or (PAYLOAD_ADDRESS, PAYLOAD_ADDRESS)  # pass-1 placeholder
    eq_addrs = eq_addrs or (PAYLOAD_ADDRESS, PAYLOAD_ADDRESS)    # pass-1 placeholder
    text = _patch_slot(PAYLOAD_SOURCE.read_text(), slot)
    if close_hooks:
        text += _strip_close_section(_patch_slot(CLOSE_SOURCE.read_text(), slot))
    if l3_mpo:
        text += _patch_mpo_addrs(_strip_mpo_sections(MPO_SOURCE.read_text()),
                                 *mpo_addrs)
    if l4_eq:
        text += _patch_eq_addrs(_strip_eq_sections(EQ_SOURCE.read_text()),
                                *eq_addrs)
    if not key7_toggle and not l3_mpo:
        text += HOOK_SOURCE.read_text() + \
            (CLOSE_HOOK_SOURCE.read_text() if close_hooks else '')
    if key7_toggle:
        text += _patch_slot(TOGGLE_HOOK_SOURCE.read_text(), slot)
    if l3_mpo:
        text += _patch_slot(
            (EQ_HOOK_SOURCE if l4_eq else MPO_HOOK_SOURCE).read_text(), slot)
    if r4_keyhook:
        text += _strip_r4_section(R4_KEYHOOK_SOURCE.read_text())
    return text


def _slot_checks(slot):
    """Invariants of the RAM slot address (docs §12; fails loudly if moved
    outside the boot-zeroed, statically dead hole)."""
    start, end = BSS_ZERO_RANGE
    if not (start <= slot < end - 3) or slot % 4:
        raise ValueError(f'RAM slot {slot:#x} outside aligned BSS zero range')
    if slot >= QCY_BASE:
        raise ValueError('RAM slot must stay below the QCY struct base')


def _assemble(text):
    obj = ASM.read_object(ASM.assemble_text(text, '<hearing-aid-l1>'))
    sections = ASM.code_sections(obj)
    if len(sections) != 1 or sections[0][2]:
        raise ValueError('L1 segment must be one code section with zero relocations')
    return obj, sections[0][1]


def assemble_segment(close_hooks=False, slot_ram=False, r4_keyhook=False,
                     key7_toggle=False, l3_mpo=False, l4_eq=False):
    """Assemble payload+stubs at PAYLOAD_ADDRESS with the real slot address.

    Two passes: the section length is layout-stable (test_hearing_aid_payload /
    test_hearing_aid_close_payload prove only the slot li.u32 immediates depend
    on the slot value), so pass 1 measures the length and pass 2 bakes
    slot = base + length. slot_ram=True (route CT) instead pins the slot to
    the boot-zeroed BSS address RAM_SLOT — layout-independent, so the two-pass
    byte-diff assertion does not apply — and the segment carries no trailing
    slot bytes. r4_keyhook=True (route AQ) additionally appends the R4
    key-store command payload after the stubs. key7_toggle=True (route L1KEY)
    composes open + close + the cmd7_toggle stub and drops the ANC-key
    trans/leave stubs (requires close_hooks + slot_ram; docs/hearing-aid-l1-key7.md).
    l3_mpo=True (route HAL3) additionally inserts the apply_mpo payload and
    uses the cmd7_toggle_mpo stub (requires close_hooks + slot_ram, mutually
    exclusive with key7_toggle; docs/hearing-aid-l3-mpo.md).
    l4_eq=True (route HAL4) additionally inserts the apply_eq payload and
    uses the cmd7_toggle_l4 stub (requires l3_mpo; docs/hearing-aid-l4-eq.md).
    Returns a report dict with bytes, entry offsets and the disassembly.
    """
    if r4_keyhook and not (close_hooks and slot_ram):
        raise ValueError('r4_keyhook requires close_hooks+slot_ram '
                         '(route AQ is a slotram superset)')
    if key7_toggle and not (close_hooks and slot_ram):
        raise ValueError('key7_toggle requires close_hooks+slot_ram '
                         '(route L1KEY is a slotram superset)')
    if l3_mpo and not (close_hooks and slot_ram):
        raise ValueError('l3_mpo requires close_hooks+slot_ram '
                         '(route HAL3 is a slotram superset)')
    if l4_eq and not l3_mpo:
        raise ValueError('l4_eq requires l3_mpo (route HAL4 is an L3 superset)')
    if slot_ram:
        if not close_hooks:
            raise ValueError('slot_ram requires close_hooks (route CT is a close superset)')
        _slot_checks(RAM_SLOT)
        slot = RAM_SLOT
    _, first = _assemble(_compose(PAYLOAD_ADDRESS, close_hooks, r4_keyhook,
                                  key7_toggle, l3_mpo, l4_eq=l4_eq))
    mpo_addrs = None
    eq_addrs = None
    if l3_mpo:
        # measure the rodata offsets from pass 1, bake real runtime addresses
        # into pass 2 (placeholder and real values are both wide li.u32 —
        # layout-stable across passes, same contract as the slot patch)
        probe = ASM.read_object(ASM.assemble_text(
            _compose(slot, close_hooks, r4_keyhook, key7_toggle, l3_mpo,
                     l4_eq=l4_eq),
            '<mpo-probe>'))
        want = ('mpo_template', 'mpo_name') + \
            (('eq_pkt_template', 'eq_name') if l4_eq else ())
        syms = {s.name: s.value for s in probe.symbols if s.name in want}
        if set(syms) != set(want):
            raise ValueError(f'missing rodata symbols: {set(want) - set(syms)}')
        mpo_addrs = (PAYLOAD_ADDRESS + syms['mpo_template'],
                     PAYLOAD_ADDRESS + syms['mpo_name'])
        if l4_eq:
            eq_addrs = (PAYLOAD_ADDRESS + syms['eq_pkt_template'],
                        PAYLOAD_ADDRESS + syms['eq_name'])
    if not slot_ram:
        slot = PAYLOAD_ADDRESS + len(first)
    obj, code = _assemble(_compose(slot, close_hooks, r4_keyhook, key7_toggle,
                                   l3_mpo, mpo_addrs, l4_eq, eq_addrs))
    if len(code) != len(first):
        raise ValueError('slot immediate changed the section layout')
    if slot_ram:
        diffs = []                   # slot independent of layout: single pass
    else:
        diffs = [i for i, (a, b) in enumerate(zip(first, code)) if a != b]
        if any(code[i] == first[i] for i in diffs) or not diffs:
            raise ValueError('expected slot-only byte differences between passes')
    if key7_toggle or l3_mpo:
        names = ['mic_effect_open', 'mic_effect_close']
        if l4_eq:
            names += ['apply_mpo', 'mpo_template', 'mpo_name',
                      'apply_eq', 'eq_pkt_template', 'eq_name', 'cmd7_toggle_l4']
        elif l3_mpo:
            names += ['apply_mpo', 'mpo_template', 'mpo_name', 'cmd7_toggle_mpo']
        else:
            names += ['cmd7_toggle']
        if r4_keyhook:
            names += ['r4_keystore_hook']
    else:
        names = ['mic_effect_open', 'trans_branch_1', 'trans_branch_2']
        if close_hooks:
            names += ['mic_effect_close'] + [w[4] for w in CLOSE_WINDOWS]
        if r4_keyhook:
            names += ['r4_keystore_hook']
    symbols = {s.name: s.value for s in obj.symbols if s.name in names}
    if len(symbols) != len(names):
        raise ValueError(f'missing section symbols: {set(names) - set(symbols)}')
    if key7_toggle or l3_mpo:
        open_len = len(_assemble(_patch_slot(PAYLOAD_SOURCE.read_text(), slot))[1])
        stub_offsets = [open_len]   # payload_bytes boundary for the report
    else:
        stub_offsets = [symbols[n] for n in ('trans_branch_1', 'trans_branch_2')]
    for name, off in symbols.items():
        if name != 'mic_effect_open' and not 0 < off < len(code):
            raise ValueError(f'symbol {name} out of segment')
    if symbols['mic_effect_open'] != 0:
        raise ValueError('mic_effect_open must open the segment')
    listing = []
    # L3/L4 variants: only the CODE parts are instruction-decoded; the rodata
    # blobs interleaved between them are appended as data entries (they are
    # not instructions — the encode round-trip does not apply). L3 carries one
    # data span (template+name at the tail); L4 carries two (apply_mpo's
    # template/name between the payload codes, apply_eq's pkt/name before the
    # stub).
    data_spans = []
    if l4_eq:
        # mpo rodata (template+name) ends where the apply_eq code begins;
        # eq rodata (pkt+name) ends where the toggle stub begins
        data_spans = [(symbols['mpo_template'], symbols['apply_eq'],
                       'mpo_template 76B + mpo_name 16B'),
                      (symbols['eq_pkt_template'], symbols['cmd7_toggle_l4'],
                       'eq_pkt 44B + eq_name 16B')]
    elif l3_mpo:
        data_spans = [(symbols['mpo_template'], len(code),
                       'mpo_template 76B + mpo_name 16B')]
    decode_ranges = []
    cursor = 0
    for lo, hi, _label in sorted(data_spans):
        if lo < cursor or hi <= lo:
            raise ValueError('data spans overlap or misordered')
        decode_ranges.append((cursor, lo))
        cursor = hi
    decode_ranges.append((cursor, len(code)))
    for (lo, hi), span in zip(decode_ranges,
                              data_spans + [None]):
        for addr, insn_id, text, raw in ASM.disassemble(code[lo:hi],
                                                        PAYLOAD_ADDRESS + lo):
            listing.append({'address': f'0x{addr:08x}', 'bytes': raw.hex(),
                            'insn': insn_id, 'text': text})
            if insn_id is None:
                raise ValueError(f'undecodable word at {addr:#x}')
            insn, values, length = ASM.decode(code, addr - PAYLOAD_ADDRESS)
            if ASM.encode(insn_id, **values) != raw:
                raise ValueError(f're-encode differs at {addr:#x} ({text})')
        if span is not None:
            slo, shi, label = span
            listing.append({'address': f'0x{PAYLOAD_ADDRESS + slo:08x}',
                            'bytes': code[slo:shi].hex(), 'insn': None,
                            'text': f'.data {shi - slo}B ({label})'})
    slot_tail = 0 if slot_ram else 4
    variant = ('open+close+slotram+l4eq' if l4_eq else
               'open+close+slotram+l3mpo' if l3_mpo else
               'open+close+slotram+key7toggle' if key7_toggle else
               'open+close+slotram+r4keyhook' if r4_keyhook else
               'open+close+slotram' if slot_ram else
               'open+close' if close_hooks else 'open')
    report = {'variant': variant,
              'payload_bytes': stub_offsets[0], 'stub_bytes': len(code) - stub_offsets[0],
              'code_bytes': len(code), 'slot_address': f'0x{slot:08x}',
              'slot_location': 'ram-bss' if slot_ram else 'flash-tail',
              'slot_boot_value': ('boot BSS zero loop [0x125320,0x12a62c) '
                                  '(docs/boot-handoff.md)' if slot_ram
                                  else 'image bytes 00 00 00 00'),
              'segment_start': f'0x{PAYLOAD_ADDRESS:08x}',
              'segment_end_runtime': f'0x{PAYLOAD_ADDRESS + len(code) + slot_tail:08x}',
              'code_sha256': hashlib.sha256(code).hexdigest(),
              'slot_diff_offsets': diffs, 'listing': listing}
    if l4_eq:
        report['toggle_address'] = f'0x{PAYLOAD_ADDRESS + symbols["cmd7_toggle_l4"]:08x}'
        report['close_address'] = f'0x{PAYLOAD_ADDRESS + symbols["mic_effect_close"]:08x}'
        report['apply_mpo_address'] = f'0x{PAYLOAD_ADDRESS + symbols["apply_mpo"]:08x}'
        report['apply_eq_address'] = f'0x{PAYLOAD_ADDRESS + symbols["apply_eq"]:08x}'
        report['mpo_template_offset'] = symbols['mpo_template']
        report['mpo_name_offset'] = symbols['mpo_name']
        report['eq_pkt_offset'] = symbols['eq_pkt_template']
        report['eq_name_offset'] = symbols['eq_name']
    elif l3_mpo:
        report['toggle_address'] = f'0x{PAYLOAD_ADDRESS + symbols["cmd7_toggle_mpo"]:08x}'
        report['close_address'] = f'0x{PAYLOAD_ADDRESS + symbols["mic_effect_close"]:08x}'
        report['apply_mpo_address'] = f'0x{PAYLOAD_ADDRESS + symbols["apply_mpo"]:08x}'
        report['mpo_template_offset'] = symbols['mpo_template']
        report['mpo_name_offset'] = symbols['mpo_name']
    elif key7_toggle:
        report['toggle_address'] = f'0x{PAYLOAD_ADDRESS + symbols["cmd7_toggle"]:08x}'
        report['close_address'] = f'0x{PAYLOAD_ADDRESS + symbols["mic_effect_close"]:08x}'
    else:
        report['stub_1_address'] = f'0x{PAYLOAD_ADDRESS + stub_offsets[0]:08x}'
        report['stub_2_address'] = f'0x{PAYLOAD_ADDRESS + stub_offsets[1]:08x}'
    if close_hooks and not key7_toggle and not l3_mpo:
        report['close_address'] = f'0x{PAYLOAD_ADDRESS + symbols["mic_effect_close"]:08x}'
        report['leave_stub_addresses'] = {
            tag: f'0x{PAYLOAD_ADDRESS + symbols[label]:08x}'
            for tag, _a, _b, _s, label in CLOSE_WINDOWS}
    if r4_keyhook:
        report['r4_keyhook_address'] = f'0x{PAYLOAD_ADDRESS + symbols["r4_keystore_hook"]:08x}'
    _verify_semantics(code, stub_offsets, slot, report, close_hooks,
                      r4_keyhook=r4_keyhook, key7_toggle=key7_toggle,
                      l3_mpo=l3_mpo, l4_eq=l4_eq)
    return report, code, slot


def _verify_semantics(code, stub_offsets, slot, report, close_hooks=False,
                      r4_keyhook=False, key7_toggle=False, l3_mpo=False,
                      l4_eq=False):
    """Decode-level assertions: stub shape, every pinned firmware target."""
    insns = list(ASM.disassemble(code, PAYLOAD_ADDRESS))
    by_off = {a - PAYLOAD_ADDRESS: (iid, t, raw) for a, iid, t, raw in insns}
    # payload part: first bytes == the route-L payload assembled alone, then
    # (close variant) the route-X close payload assembled alone.
    standalone_open = _assemble(_patch_slot(PAYLOAD_SOURCE.read_text(), slot))[1]
    if code[:len(standalone_open)] != standalone_open:
        raise ValueError('composed segment prefix != standalone route-L payload')
    report['payload_prefix_matches_route_L'] = True
    if close_hooks:
        standalone_close = _assemble(
            _patch_slot(CLOSE_SOURCE.read_text(), slot))[1]
        close_off = len(standalone_open)
        if code[close_off:close_off + len(standalone_close)] != standalone_close:
            raise ValueError('composed close payload != standalone route-X payload')
        report['close_payload_matches_route_X'] = True
    if l4_eq:
        _verify_mpo(code, by_off, report)
        _verify_eq(code, by_off, report)
        _verify_l4_toggle_stub(code, by_off, report)
        if r4_keyhook:
            _verify_r4_keyhook(code, by_off, report)
    elif l3_mpo:
        _verify_mpo(code, by_off, report)
        _verify_mpo_toggle_stub(code, by_off, report)
        if r4_keyhook:
            _verify_r4_keyhook(code, by_off, report)
    elif key7_toggle:
        _verify_toggle_stub(code, by_off, report)
    else:
        # stubs: exact instruction-shape + absolute firmware targets
        epi = {'A': WINDOWS[0][3], 'B': WINDOWS[1][3]}
        stub_b_end = len(code)
        if close_hooks:
            stub_b_end = int(report['leave_stub_addresses']['ON-1'], 16) - PAYLOAD_ADDRESS
        for tag, stub_off, other in (('A', stub_offsets[0], stub_offsets[1]),
                                     ('B', stub_offsets[1], stub_b_end)):
            end = other
            shape = [by_off[o][0] for o in range(stub_off, end) if o in by_off]
            expected_shape = STUB_B_INSN_IDS if (tag == 'B' and close_hooks) \
                else STUB_INSN_IDS
            if shape != expected_shape:
                raise ValueError(f'stub {tag} instruction shape mismatch: {shape}')
            loads = [int(by_off[o][1].split('= ')[1])
                     for o in range(stub_off, end) if o in by_off and by_off[o][0] == 'li.u32']
            call_off = stub_off + 6 + 2 + 4 + 2 + 6 + 2   # `call mic_effect_open`
            iid, t, raw = by_off[call_off]
            m = re.search(r'call (-?\d+)$', t)
            if m is None:
                raise ValueError(f'stub {tag} internal call does not decode: {t}')
            target = PAYLOAD_ADDRESS + call_off + len(raw) + int(m.group(1))
            if (loads[:2] != [ANC_CTX_GET, 0x0600dae4] or loads[2] != epi[tag]
                    or iid not in ('call.s', 'call.l') or target != PAYLOAD_ADDRESS):
                raise ValueError(f'stub {tag} targets mismatch: loads={loads} call={t} target={target:#x}')
        # the last leave stub must end where the R4 payload begins (when present),
        # so its shape check cannot bleed into the appended keyhook
        leave_end = len(code)
        if r4_keyhook:
            leave_end = int(report['r4_keyhook_address'], 16) - PAYLOAD_ADDRESS
        if close_hooks:
            _verify_leave_stubs(code, by_off, report, seg_end=leave_end)
        if r4_keyhook:
            _verify_r4_keyhook(code, by_off, report)
    # slot loads in the payload+stub part resolve to the real slot address
    # (open: guard + record; close: guard + clear; toggle: guard — all
    # load r0 = slot). key7/L3 adds the toggle guard (apply_mpo reads none).
    expected_loads = [slot] * ((4 if close_hooks else 2)
                               + (1 if (key7_toggle or l3_mpo) else 0))
    slot_loads = [int(t.split('= ')[1]) for o, (iid, t, raw) in sorted(by_off.items())
                  if iid == 'li.u32' and t.startswith('r0 = ')
                  and int(t.split('= ')[1]) == slot]
    if len(slot_loads) != len(expected_loads):
        raise ValueError(f'slot immediates mismatch: {slot_loads}')


def _verify_mpo(code, by_off, report):
    """The apply_mpo payload (route HAL3): exact code shape, template/name
    bytes, and every firmware/segment target.

    The code part (between the close payload and the template) must match
    MPO_INSN_IDS; the li.u32 loads must cover both API anchors, the two
    float bit-pattern bounds, and the segment-local template/name addresses;
    the copy loop and the three knee stores keep their pinned displacements.
    """
    mpo_off = int(report['apply_mpo_address'], 16) - PAYLOAD_ADDRESS
    tpl_off = report['mpo_template_offset']
    name_off = report['mpo_name_offset']
    if mpo_off + MPO_CODE_BYTES != tpl_off:
        raise ValueError('mpo_template must directly follow the apply_mpo code')
    if tpl_off + len(MPO_TEMPLATE_EXPECTED) != name_off:
        raise ValueError('mpo_name must directly follow the template')
    shape = [by_off[o][0] for o in range(mpo_off, tpl_off) if o in by_off]
    if shape != MPO_INSN_IDS:
        raise ValueError(f'apply_mpo shape mismatch: {shape}')
    loads = [int(by_off[o][1].split('= ')[1])
             for o in range(mpo_off, tpl_off) if o in by_off and by_off[o][0] == 'li.u32']
    seg = PAYLOAD_ADDRESS
    expected_loads = [MPO_API_ANCHORS[0],          # get_config
                      0x3E99999A, 0x42F00000,      # 0.3f / 120.0f bit patterns
                      seg + tpl_off,               # mpo_template
                      0xdefe,                      # NODE_UUID_WDRC
                      seg + name_off,              # mpo_name
                      MPO_API_ANCHORS[1]]          # jlstream_set_node_param
    if loads != expected_loads:
        raise ValueError(f'apply_mpo loads mismatch: {[hex(x) for x in loads]}')
    # knee stores: th[1].in/+16, th[1].out/+20, th[2].out/+28 of the stack packet
    stores = [t for o in range(mpo_off, tpl_off) if o in by_off
              for t in [by_off[o][1]] if t.startswith('[r3+')]
    if stores != ['[r3+16] = r5', '[r3+20] = r5', '[r3+28] = r5']:
        raise ValueError(f'apply_mpo knee stores mismatch: {stores}')
    if code[tpl_off:tpl_off + 76] != MPO_TEMPLATE_EXPECTED:
        raise ValueError('mpo template bytes differ from the official Drc1Eff '
                         '76B (single th[2].in raise)')
    if code[name_off:name_off + 16] != MPO_NAME_EXPECTED:
        raise ValueError('mpo name field mismatch')
    report['apply_mpo_shape_matches'] = True
    report['mpo_template_matches_official'] = True


def _verify_mpo_toggle_stub(code, by_off, report):
    """The cmd7_toggle_mpo stub: exact shape + every firmware target.

    Shape = slot guard -> (open) r0=1/r1=1 -> db3c(1,1) -> mic_effect_open ->
    open!=0 shortcut -> apply_mpo, else mic_effect_close -> r4=1 ->
    goto HANDLER_RET. Internal calls must reach open/apply_mpo/close.
    """
    toggle_off = int(report['toggle_address'], 16) - PAYLOAD_ADDRESS
    close_off = int(report['close_address'], 16) - PAYLOAD_ADDRESS
    mpo_off = int(report['apply_mpo_address'], 16) - PAYLOAD_ADDRESS
    stub_end = len(code)
    if 'r4_keyhook_address' in report:
        stub_end = int(report['r4_keyhook_address'], 16) - PAYLOAD_ADDRESS
    shape = [by_off[o][0] for o in range(toggle_off, stub_end) if o in by_off]
    stub_bytes = sum(len(by_off[o][2]) for o in range(toggle_off, stub_end) if o in by_off)
    if toggle_off + stub_bytes != stub_end:
        raise ValueError('cmd7_toggle_mpo stub must close (or border the keyhook)')
    if shape != MPO_TOGGLE_INSN_IDS:
        raise ValueError(f'mpo toggle stub shape mismatch: {shape}')
    loads = [int(by_off[o][1].split('= ')[1])
             for o in range(toggle_off, stub_end) if o in by_off and by_off[o][0] == 'li.u32']
    if loads != [int(report['slot_address'], 16), ANC_DISPATCH, HANDLER_RET]:
        raise ValueError(f'mpo toggle stub loads mismatch: {loads}')
    seq = [(o, by_off[o][0], by_off[o][1]) for o in range(toggle_off, stub_end)
           if o in by_off]
    for i, (o, iid, t) in enumerate(seq):
        if iid == 'li.u32' and t == f'r6 = {ANC_DISPATCH}':
            if seq[i + 1][1] != 'call.r':
                raise ValueError('db3c load not immediately followed by its call')
        if iid in ('call.s', 'call.l'):
            m = re.search(r'call (-?\d+)$', t)
            if m is None:
                raise ValueError(f'mpo toggle call does not decode: {t}')
            raw = by_off[o][2]
            target = PAYLOAD_ADDRESS + o + len(raw) + int(m.group(1))
            if target not in (PAYLOAD_ADDRESS,                      # open
                              PAYLOAD_ADDRESS + mpo_off,            # apply_mpo
                              PAYLOAD_ADDRESS + close_off):         # close
                raise ValueError(f'mpo toggle call reaches {target:#x}')
    report['mpo_toggle_stub_shape_matches'] = True


def _verify_eq(code, by_off, report):
    """The apply_eq payload (route HAL4): exact code shape, pkt/name bytes,
    and every firmware/segment target.

    The code part (between the mpo rodata and the eq pkt blob) must match
    EQ_INSN_IDS; the li.u32 loads must cover both API anchors, the header
    word0, the gain-magnitude bound, and the segment-local pkt/name
    addresses; the per-segment patch stores keep their pinned displacements
    (idx halfword @+4, freq word @+8, gain word @+12 of the stack packet).
    """
    eq_off = int(report['apply_eq_address'], 16) - PAYLOAD_ADDRESS
    pkt_off = report['eq_pkt_offset']
    name_off = report['eq_name_offset']
    if eq_off + EQ_CODE_BYTES != pkt_off:
        raise ValueError('eq_pkt must directly follow the apply_eq code')
    if pkt_off + len(EQ_PKT_EXPECTED) != name_off:
        raise ValueError('eq_name must directly follow the pkt template')
    shape = [by_off[o][0] for o in range(eq_off, pkt_off) if o in by_off]
    if shape != EQ_INSN_IDS:
        raise ValueError(f'apply_eq shape mismatch: {shape}')
    loads = [int(by_off[o][1].split('= ')[1])
             for o in range(eq_off, pkt_off) if o in by_off and by_off[o][0] == 'li.u32']
    seg = PAYLOAD_ADDRESS
    expected_loads = [EQ_API_ANCHORS[0],          # get_config
                      EQ_HDR_WORD0,               # {'H','A',ver2,rsv0}
                      EQ_GAIN_MAX_BITS,           # 40.0f magnitude bound
                      EQ_API_ANCHORS[1],          # jlstream_set_node_param
                      seg + name_off,             # eq_name
                      seg + pkt_off]              # eq_pkt_template
    if loads != expected_loads:
        raise ValueError(f'apply_eq loads mismatch: {[hex(x) for x in loads]}')
    # per-segment patches: index halfword @+4, freq word @+8, gain word @+12
    # of the stack packet (r3 = packet base)
    stores = [t for o in range(eq_off, pkt_off) if o in by_off
              for t in [by_off[o][1]]
              if t.startswith('h[r3+') or t.startswith('[r3+')]
    if stores != ['h[r3+4] = r8', '[r3+8] = r1', '[r3+12] = r1']:
        raise ValueError(f'apply_eq patch stores mismatch: {stores}')
    # the send argument setup feeds set_node_param(uuid, name, packet, 44)
    texts = [by_off[o][1] for o in sorted(by_off) if eq_off <= o < pkt_off]
    for needed in (f'r0 = {EQ_NODE_UUID}', 'r1 = r5', 'r2 = r3', 'r3 = 44'):
        if needed not in texts:
            raise ValueError(f'apply_eq send setup missing: {needed}')
    if not any(t.startswith(f'if (r0 != {EQ_IOCTL_OK})') for t in texts):
        raise ValueError('apply_eq missing the r0==1 continue gate')
    if code[pkt_off:pkt_off + 44] != EQ_PKT_EXPECTED:
        raise ValueError('eq pkt template bytes differ from the pinned '
                         'EQ_SEG 44B constants')
    if code[name_off:name_off + 16] != EQ_NAME_EXPECTED:
        raise ValueError('eq name field mismatch')
    report['apply_eq_shape_matches'] = True
    report['eq_pkt_template_matches'] = True


def _verify_l4_toggle_stub(code, by_off, report):
    """The cmd7_toggle_l4 stub: exact shape + every firmware target.

    Shape = slot guard -> (open) r0=1/r1=1 -> db3c(1,1) -> mic_effect_open ->
    open!=0 shortcut -> apply_mpo -> apply_eq, else mic_effect_close ->
    r4=1 -> goto HANDLER_RET. Internal calls must reach open/apply_mpo/
    apply_eq/close.
    """
    toggle_off = int(report['toggle_address'], 16) - PAYLOAD_ADDRESS
    close_off = int(report['close_address'], 16) - PAYLOAD_ADDRESS
    mpo_off = int(report['apply_mpo_address'], 16) - PAYLOAD_ADDRESS
    eq_off = int(report['apply_eq_address'], 16) - PAYLOAD_ADDRESS
    stub_end = len(code)
    if 'r4_keyhook_address' in report:
        stub_end = int(report['r4_keyhook_address'], 16) - PAYLOAD_ADDRESS
    shape = [by_off[o][0] for o in range(toggle_off, stub_end) if o in by_off]
    stub_bytes = sum(len(by_off[o][2]) for o in range(toggle_off, stub_end) if o in by_off)
    if toggle_off + stub_bytes != stub_end:
        raise ValueError('cmd7_toggle_l4 stub must close (or border the keyhook)')
    if shape != L4_TOGGLE_INSN_IDS:
        raise ValueError(f'l4 toggle stub shape mismatch: {shape}')
    loads = [int(by_off[o][1].split('= ')[1])
             for o in range(toggle_off, stub_end) if o in by_off and by_off[o][0] == 'li.u32']
    if loads != [int(report['slot_address'], 16), ANC_DISPATCH, HANDLER_RET]:
        raise ValueError(f'l4 toggle stub loads mismatch: {loads}')
    seq = [(o, by_off[o][0], by_off[o][1]) for o in range(toggle_off, stub_end)
           if o in by_off]
    for i, (o, iid, t) in enumerate(seq):
        if iid == 'li.u32' and t == f'r6 = {ANC_DISPATCH}':
            if seq[i + 1][1] != 'call.r':
                raise ValueError('db3c load not immediately followed by its call')
        if iid in ('call.s', 'call.l'):
            m = re.search(r'call (-?\d+)$', t)
            if m is None:
                raise ValueError(f'l4 toggle call does not decode: {t}')
            raw = by_off[o][2]
            target = PAYLOAD_ADDRESS + o + len(raw) + int(m.group(1))
            if target not in (PAYLOAD_ADDRESS,                      # open
                              PAYLOAD_ADDRESS + mpo_off,            # apply_mpo
                              PAYLOAD_ADDRESS + eq_off,             # apply_eq
                              PAYLOAD_ADDRESS + close_off):         # close
                raise ValueError(f'l4 toggle call reaches {target:#x}')
    report['l4_toggle_stub_shape_matches'] = True


def _verify_toggle_stub(code, by_off, report):
    """The cmd7_toggle stub (route L1KEY): exact shape + every firmware target.

    Shape = slot guard -> (open) r0=1/r1=1 -> db3c(1,1) -> mic_effect_open,
    else mic_effect_close -> r4=1 -> goto HANDLER_RET. The two internal calls
    must reach the open/close payload entries, and the ANC_DISPATCH load must
    sit immediately before its call.
    """
    toggle_off = int(report['toggle_address'], 16) - PAYLOAD_ADDRESS
    close_off = int(report['close_address'], 16) - PAYLOAD_ADDRESS
    if toggle_off + sum(6 if i == 'li.u32' else 2 for i in TOGGLE_INSN_IDS) != len(code):
        raise ValueError('cmd7_toggle stub must close the segment')
    shape = [by_off[o][0] for o in range(toggle_off, len(code)) if o in by_off]
    if shape != TOGGLE_INSN_IDS:
        raise ValueError(f'toggle stub shape mismatch: {shape}')
    # li.u32 loads: [slot, ANC_DISPATCH, HANDLER_RET]
    loads = [int(by_off[o][1].split('= ')[1])
             for o in range(toggle_off, len(code)) if o in by_off and by_off[o][0] == 'li.u32']
    if loads != [int(report['slot_address'], 16), ANC_DISPATCH, HANDLER_RET]:
        raise ValueError(f'toggle stub loads mismatch: {loads}')
    # the db3c call must immediately follow the ANC_DISPATCH li.u32 load
    seq = [(o, by_off[o][0], by_off[o][1]) for o in range(toggle_off, len(code))
           if o in by_off]
    for i, (o, iid, t) in enumerate(seq):
        if iid == 'li.u32' and t == f'r6 = {ANC_DISPATCH}':
            if seq[i + 1][1] != 'call.r':
                raise ValueError('db3c load not immediately followed by its call')
        if iid == 'call.s':
            m = re.search(r'call (-?\d+)$', t)
            if m is None:
                raise ValueError(f'toggle internal call does not decode: {t}')
            raw = by_off[o][2]
            target = PAYLOAD_ADDRESS + o + len(raw) + int(m.group(1))
            if target not in (PAYLOAD_ADDRESS, PAYLOAD_ADDRESS + close_off):
                raise ValueError(f'toggle call reaches {target:#x}, not open/close')
    report['toggle_stub_shape_matches'] = True


def _verify_leave_stubs(code, by_off, report, seg_end=None):
    """The four ON/OFF leave stubs (§10.5): shape + every firmware target."""
    stubs = sorted(((tag, int(report['leave_stub_addresses'][tag], 16)
                     - PAYLOAD_ADDRESS, address, mode_slot)
                    for tag, address, _orig, mode_slot, _label in CLOSE_WINDOWS),
                   key=lambda t: t[1])
    close_off = int(report['close_address'], 16) - PAYLOAD_ADDRESS
    for index, (tag, stub_off, address, mode_slot) in enumerate(stubs):
        end = stubs[index + 1][1] if index + 1 < len(stubs) else \
            (seg_end if seg_end is not None else len(code))
        shape = [by_off[o][0] for o in range(stub_off, end) if o in by_off]
        expected_shape = LEAVE_ON2_INSN_IDS if tag == 'ON-2' else LEAVE_INSN_IDS
        if shape != expected_shape:
            raise ValueError(f'leave stub {tag} shape mismatch: {shape}')
        loads = [int(by_off[o][1].split('= ')[1])
                 for o in range(stub_off, end) if o in by_off and by_off[o][0] == 'li.u32']
        ret = address + 8        # original 2B merge goto / the 0x0600bbba call
        expected_loads = ([ANC_CTX_GET, ANC_CTX_GET, ret] if tag == 'ON-2'
                          else [ANC_CTX_GET, ret])
        if loads != expected_loads:
            raise ValueError(f'leave stub {tag} loads mismatch: {loads}')
        # replayed mode-slot load keeps the original displacement
        ld_text = by_off[stub_off + 6 + 2][1]
        m = re.search(r'r5 = \[r0\+(\d+)\]', ld_text)
        if m is None or int(m.group(1)) != mode_slot:
            raise ValueError(f'leave stub {tag} slot load mismatch: {ld_text}')
        # internal call reaches the close payload entry
        call_off = stub_off + 6 + 2 + 4
        iid, t, raw = by_off[call_off]
        m = re.search(r'call (-?\d+)$', t)
        if m is None or iid != 'call.s':
            raise ValueError(f'leave stub {tag} internal call does not decode: {t}')
        target = PAYLOAD_ADDRESS + call_off + len(raw) + int(m.group(1))
        if target != PAYLOAD_ADDRESS + close_off:
            raise ValueError(f'leave stub {tag} calls {target:#x}, not the close payload')


# Route AQ (+HAL4 key-31 extension): the R4 keyhook payload's exact
# instruction shape, from the assembled keystore_hook.S (prologue, len
# dispatch over {9, 89, 1}, key-30/key-31 write branches with the set_config
# arg setup + status byte store, read branch dispatching key 30/31 to their
# get_config lengths, silent/epilogue).
R4_INSN_IDS = ['push.rets_rl', 'mov.rr', 'beq.ri', 'beq.ri', 'beq.ri',
               'goto.s', 'ld.bu', 'bne.ri', 'li.u32', 'li.u8', 'add.rri',
               'li.u8', 'call.r', 'li.u8', 'beqz', 'li.u8', 'st.b', 'li.u8',
               'goto.s', 'ld.bu', 'bne.ri', 'li.u32', 'li.u8', 'add.rri',
               'li.u8', 'call.r', 'li.u8', 'beqz', 'li.u8', 'st.b', 'li.u8',
               'goto.s', 'ld.bu', 'beq.ri', 'beq.ri', 'goto.s', 'li.u32',
               'li.u8', 'add.rri', 'li.u8', 'call.r', 'bne.ri', 'li.u8',
               'goto.s', 'li.u32', 'li.u8', 'add.rri', 'li.u8', 'call.r',
               'bne.ri', 'li.u8', 'goto.s', 'li.u8', 'pop.pc_rl']
R4_SET_CONFIG = 0x06001f12        # VM set_config dispatcher (route AB)
R4_GET_CONFIG = 0x06000f88        # VM get_config dispatcher (route AB)
R4_VALUE_LEN = 8


def _verify_r4_keyhook(code, by_off, report):
    """The R4 keyhook tail: standalone-equal bytes, exact shape, both API
    anchors loaded into r6 exactly once and each immediately called, the
    write branch's status word store, and the read branch's resp+2 buffer."""
    standalone = _assemble(R4_KEYHOOK_SOURCE.read_text())[1]
    hook_off = int(report['r4_keyhook_address'], 16) - PAYLOAD_ADDRESS
    if code[hook_off:hook_off + len(standalone)] != standalone:
        raise ValueError('composed R4 keyhook != standalone route-AQ payload')
    if hook_off + len(standalone) != len(code):
        raise ValueError('R4 keyhook must close the segment')
    report['r4_payload_matches_route_AQ'] = True
    shape = [by_off[o][0] for o in range(hook_off, len(code)) if o in by_off]
    if shape != R4_INSN_IDS:
        raise ValueError(f'R4 keyhook instruction shape mismatch: {shape}')
    loads = [int(by_off[o][1].split('= ')[1]) for o in range(hook_off, len(code))
             if o in by_off and by_off[o][0] == 'li.u32']
    # HAL4 extension: two write branches (set_config) + two read branches
    # (get_config) — each anchor loaded exactly twice
    if sorted(loads) != sorted([R4_SET_CONFIG, R4_SET_CONFIG,
                                R4_GET_CONFIG, R4_GET_CONFIG]):
        raise ValueError(f'R4 keyhook API anchors mismatch: {loads}')
    # every call r6 is preceded (three 2B arg setups earlier) by its API load
    offsets = sorted(o for o in by_off if o >= hook_off)
    for o in offsets:
        if by_off[o][0] == 'call.r':
            prev = by_off.get(o - 12)
            if prev is None or prev[0] != 'li.u32' or not prev[1].startswith('r6 = '):
                raise ValueError(f'R4 call at +{o} not preceded by its API load')
    # write branch stores the status BYTE through the saved resp pointer r4
    # at +2 (the dispatcher overwrites resp[0..1]; a word store at +0 would
    # leave resp[2]==0 for both success and failure — route HAL3 erratum);
    # read branch builds the destination as r4+2 before get_config
    texts = [by_off[o][1] for o in offsets]
    if 'b[r4+2] = r5' not in texts or '[r4+0] = r5' in texts \
            or 'r1 = r4 + 2' not in texts or 'r1 = r2 + 1' not in texts:
        raise ValueError('R4 keyhook resp/value addressing mismatch')
    report['r4_keyhook_bytes'] = len(standalone)


def apply_l1_patch(flash, close_hooks=False, slot_ram=False, r4_keyhook=False,
                   key7_toggle=False, l3_mpo=False, l4_eq=False):
    """Apply the implant edits to the decrypted flash image in place.

    close_hooks=True builds the open+close segment (route Y) and additionally
    replaces the four ON/OFF leave windows. slot_ram=True (route CT) pins the
    data slot to boot-zeroed BSS instead of the flash segment tail (implies
    the close variant). r4_keyhook=True (route AQ) additionally appends the
    R4 key-store command payload and wires it into the cmdid 0x30 registry
    entry's null setter word (implies the slotram variant). key7_toggle=True
    (route L1KEY) instead composes open + close + the cmd7_toggle stub and
    grafts ONLY the double-click cmd 7 entry (0x06025404) — the ANC key keeps
    its stock OFF/ON/TRANS windows. l3_mpo=True (route HAL3) is the L1KEY
    shape plus the apply_mpo payload: open success feeds the latest VM key 30
    MPO into Drc1Eff via jlstream_set_node_param (same KEY7 graft window,
    mutually exclusive with key7_toggle). l4_eq=True (route HAL4) additionally
    inserts the apply_eq payload and uses the cmd7_toggle_l4 stub: open
    success then applies the latest VM key 31 ten-band EQ to Eq1Eff the same
    way (docs/hearing-aid-l4-eq.md). Returns the report dict (incl.
    new_app_end for the caller's SFC/boot-info pass).
    Per-site byte assertions keep this valid with or without the ANC
    two-state table change, which edits one unrelated app.bin byte first.
    """
    if r4_keyhook and not (close_hooks and slot_ram):
        raise ValueError('r4_keyhook requires close_hooks+slot_ram '
                         '(route AQ is a slotram superset)')
    if key7_toggle and not (close_hooks and slot_ram):
        raise ValueError('key7_toggle requires close_hooks+slot_ram '
                         '(route L1KEY is a slotram superset)')
    if l3_mpo and not (close_hooks and slot_ram):
        raise ValueError('l3_mpo requires close_hooks+slot_ram '
                         '(route HAL3 is a slotram superset)')
    if l4_eq and not l3_mpo:
        raise ValueError('l4_eq requires l3_mpo (route HAL4 is an L3 superset)')
    if slot_ram and not close_hooks:
        raise ValueError('slot_ram requires close_hooks (route CT is a close superset)')
    segment, code, slot = assemble_segment(close_hooks, slot_ram=slot_ram,
                                           r4_keyhook=r4_keyhook,
                                           key7_toggle=key7_toggle,
                                           l3_mpo=l3_mpo, l4_eq=l4_eq)
    slot_tail = 0 if slot_ram else 4
    end = OLD_APP_END + 32 + 1 + len(code) + slot_tail   # header + 0xff + code (+ slot)
    if end > len(flash) or any(v != 255 for v in flash[OLD_APP_END:end]):
        raise ValueError('non-padding bytes at the L1 append location')
    hooks = []
    # one uniform window spec: (tag, address, original 8B, stub address text,
    # replaced-instruction description, only-incoming-branch note)
    if key7_toggle or l3_mpo:
        specs = [
            ('KEY7', KEY7_WINDOW[1], KEY7_WINDOW[2], segment['toggle_address'],
             'add.s14.rri r1=r8+1873; li.u8 r0=0; li.u8 r4=0 '
             '(audio-mode-cycle publish-name setup)',
             'jump table 0x06025382 byte 0x40 (double-click cmd 7)'),
        ]
    else:
        specs = [
            ('A', WINDOWS[0][1], WINDOWS[0][2], segment['stub_1_address'],
             'call 0x0600c2aa; r0=[r0+116] (TRANS apply fetch)',
             '0x0600db92 (TRANS)'),
            ('B', WINDOWS[1][1], WINDOWS[1][2], segment['stub_2_address'],
             'call 0x0600c2aa; r0=[r0+116] (TRANS apply fetch)',
             '0x0600dbbc (TRANS)'),
        ]
        if close_hooks:
            incoming = {'ON-1': 'none (plain fall-through)',
                        'ON-2': 'none (plain fall-through)',
                        'OFF-1': '0x0600db8e (mode==1)', 'OFF-2': '0x0600dbb8 (mode==1)'}
            for tag, address, original, mode_slot, _label in CLOSE_WINDOWS:
                reg = 'r5' if tag == 'ON-2' else 'r0'    # original load target
                specs.append((tag, address, original,
                              segment['leave_stub_addresses'][tag],
                              f'call 0x0600c2aa; {reg}=[r0+{mode_slot}] '
                              f'({"ON" if mode_slot == 112 else "OFF"} apply fetch)',
                              incoming[tag]))
    for tag, address, original, stub_text, replaced, incoming_note in specs:
        position = APP_DATA + address - APP_BASE
        if flash[position:position + 8] != original:
            raise ValueError(f'hook window {tag} bytes changed')
        stub = int(stub_text, 16)
        window = ASM.encode('li.u32', r=6, n=stub) + ASM.encode('goto.r', x=6)
        got = list(ASM.disassemble(window, address))
        if [g[1] for g in got] != ['li.u32', 'goto.r'] or len(window) != 8:
            raise ValueError(f'hook window {tag} replacement does not decode')
        flash[position:position + 8] = window
        hooks.append({'window': tag, 'runtime_address': f'0x{address:08x}',
                      'stub_address': stub_text,
                      'before': original.hex(), 'after': window.hex(),
                      'replaced_instructions': replaced,
                      'replacement': f'r6={stub_text}; goto r6',
                      'only_incoming_branch': incoming_note})
    fence_position = APP_DATA + FENCE_SITE - APP_BASE
    if flash[fence_position:fence_position + 6] != FENCE_ORIGINAL:
        raise ValueError('LIM_PC0 upper-limit instruction changed')
    upper = PAYLOAD_ADDRESS + len(code) + slot_tail  # end of the appended file
    flash[fence_position + 2:fence_position + 6] = struct.pack('<I', upper)
    fence = {'site': f'0x{FENCE_SITE:08x}', 'old_upper_limit': '0x60c3338',
             'new_upper_limit': f'0x{upper:08x}',
             'lower_bound_and_enable_preserved': True,
             'evidence': 'route O negative test: stock fence rejects payload fetch'}
    r4_registry = None
    if r4_keyhook:
        # data window, not an instruction window: the cmdid 0x30 entry's null
        # setter word (verified zero + reference-free against official bytes)
        # becomes a pointer to the appended R4 payload
        reg_position = APP_DATA + R4_REG_SLOT - APP_BASE
        if flash[reg_position:reg_position + 4] != R4_REG_SLOT_ORIGINAL:
            raise ValueError('R4 registry setter word is not the expected null')
        hook_address = int(segment['r4_keyhook_address'], 16)
        struct.pack_into('<I', flash, reg_position, hook_address)
        r4_registry = {
            'kind': 'data-pointer (registry setter slot, not code)',
            'registry_entry': f'0x{R4_REG_ENTRY:08x}',
            'slot_address': f'0x{R4_REG_SLOT:08x}',
            'cmdid': R4_CMDID,
            'before': R4_REG_SLOT_ORIGINAL.hex(),
            'after': struct.pack('<I', hook_address).hex(),
            'handler': segment['r4_keyhook_address'],
            'write_path_evidence': 'dispatcher 0x0600f8b4 null-checks [entry+8], '
                                   '0x0600f932-0x0600f93e calls setter(buf,128,data,len); '
                                   'async trampoline 0x060256aa same ABI',
            'read_path_unchanged': 'getter 0x06026060 (entry+4) untouched',
            'reference_scan': 'no li.u32 / raw LE32 reference targets the entry '
                              '(full app.bin scan, route AQ)'}
        segment['r4_registry_window'] = r4_registry
    if flash[LAST_HEADER + 14:LAST_HEADER + 16] != b'\x01\x00':
        raise ValueError('expected final config.dat entry')
    if flash[LAST_HEADER + 16:LAST_HEADER + 32].split(b'\0')[0] != b'config.dat':
        raise ValueError('unexpected final file')
    if jl_crc16(flash[LAST_HEADER + 2:LAST_HEADER + 32]) != \
            struct.unpack_from('<H', flash, LAST_HEADER)[0]:
        raise ValueError('invalid config.dat header CRC')
    struct.pack_into('<H', flash, LAST_HEADER + 14, 0)
    struct.pack_into('<H', flash, LAST_HEADER,
                     jl_crc16(flash[LAST_HEADER + 2:LAST_HEADER + 32]))
    content = b'\xff' + code + (b'' if slot_ram else b'\x00\x00\x00\x00')
    block_size = 32 + len(content)
    header = bytearray(struct.pack('<HHIIBBH16s', 0, jl_crc16(content), 32,
                                   block_size, 0x82, 0, 1, HOOK_FILE.encode()))
    struct.pack_into('<H', header, 0, jl_crc16(header[2:]))
    flash[OLD_APP_END:end] = header + content
    # Existing nested app.bin and parent app-area checksums, after ALL code edits.
    struct.pack_into('<H', flash, 0x2042, jl_crc16(flash[APP_DATA:APP_DATA + APP_SIZE]))
    struct.pack_into('<H', flash, 0x2040, jl_crc16(flash[0x2042:0x2060]))
    struct.pack_into('<H', flash, 0x2022, jl_crc16(flash[0x2040:0xCFCC0]))
    struct.pack_into('<H', flash, 0x2020, jl_crc16(flash[0x2022:0x2040]))
    payload_flash = OLD_APP_END + 33
    if payload_flash != 0x2020 + PAYLOAD_ADDRESS - 0x06000000:
        raise ValueError('payload/file alignment mismatch')
    segment.update({'status': 'OFFLINE_UNTESTED', 'hook_windows': hooks,
                    'fence_repair': fence, 'extension_file': HOOK_FILE,
                    'old_app_end': OLD_APP_END, 'new_app_end': end,
                    'payload_flash_offset': hex(payload_flash),
                    'slot_initial_bytes': segment['slot_boot_value'],
                    'device_verified': False})
    return segment


def verify_l1_image(result, source, flash_pos, original_plain, plain, l1):
    """Byte-exact verification of the finished L1 candidate against the source.

    Independent re-checks (not just diff bookkeeping): the SFC ciphertext of
    the whole app region, the boot-info fields recomputed from both sides, the
    hook windows and appended segment decoded back out of the final plaintext,
    and a whole-file diff asserted equal to exactly the expected positions:
    plaintext edits below the old JLFS end + the re-encrypted append region +
    the 32B boot-info + the two UFW CRC fields. Works for any combination of
    attenuation / ANC two-state / hearing-aid-demo (their plaintext edits are
    covered by the same plain-diff term); EQ toggle is rejected upstream.
    Returns (diff_positions, expected_positions) for the build report.
    """
    if len(result) != len(source):
        raise ValueError('candidate length changed')
    new_app_end = l1['new_app_end']
    # 1. SFC ciphertext of [0x2020, new_app_end) reproduces from plaintext.
    cipher = bytearray(plain[0x2020:new_app_end])
    jl_sfc_cipher(cipher, 0, len(cipher), 0, 0x3496)
    if bytes(result[flash_pos + 0x2020:flash_pos + new_app_end]) != bytes(cipher):
        raise ValueError('SFC 应用区密文与明文重算不一致')
    # 2. boot-info fields recomputed independently from result bytes.
    boot = bytearray(result[flash_pos + 0x2000:flash_pos + 0x2020])
    jl_enc_cipher(boot, 0, len(boot), 0xffff)
    boot_hcrc, plain_crc, cipher_crc, reserved, boot_size = struct.unpack_from('<HHHHI', boot)
    if (reserved != 0 or boot_size != new_app_end - 0x2020
            or plain_crc != jl_crc16(plain[0x2020:new_app_end])
            or cipher_crc != jl_crc16(result[flash_pos + 0x2020:flash_pos + new_app_end])
            or boot_hcrc != jl_crc16(boot[2:])):
        raise ValueError('boot-info 字段与独立重算不一致')
    # 3. hook windows decode back to r6=<stub>; goto r6 out of the final plaintext.
    for hook in l1['hook_windows']:
        address = int(hook['runtime_address'], 16)
        position = APP_DATA + address - APP_BASE
        window = plain[position:position + 8]
        decoded = list(ASM.disassemble(window, address))
        stub_addr = int(hook['stub_address'], 16)
        if ([d[1] for d in decoded] != ['li.u32', 'goto.r']
                or decoded[0][2] != f'r6 = {stub_addr}'
                or decoded[1][2] != 'goto r6'):
            raise ValueError(f'接驳窗口 {hook["window"]} 最终字节反解码不符')
    # 3b. the R4 registry setter word decodes back to the handler pointer.
    if 'r4_registry_window' in l1:
        reg = l1['r4_registry_window']
        reg_position = APP_DATA + int(reg['slot_address'], 16) - APP_BASE
        if struct.unpack_from('<I', plain, reg_position)[0] != \
                int(reg['handler'], 16):
            raise ValueError('R4 注册表 setter 字与 handler 地址不一致')
        if struct.unpack_from('<I', plain, APP_DATA + R4_REG_ENTRY - APP_BASE
                              + 24)[0] & 0xff != R4_CMDID:
            raise ValueError('R4 注册表项 cmdid 不是 0x30')
    # 4. appended segment: slot bytes (flash-tail variant) or exact file end
    #    (ram-slot variant carries no slot in the image), full listing
    #    round-trip.
    code_start = l1['payload_flash_offset'] if isinstance(l1['payload_flash_offset'], int) \
        else int(l1['payload_flash_offset'], 16)
    code_len = l1['code_bytes']
    if l1.get('slot_location') == 'ram-bss':
        if new_app_end != code_start + code_len:
            raise ValueError('ram-slot 变体的追加文件必须在代码段末尾结束（无镜像内槽）')
    elif plain[code_start + code_len:code_start + code_len + 4] != b'\x00\x00\x00\x00':
        raise ValueError('数据槽初始字节不是 0')
    if plain[code_start:code_start + code_len] != _segment_bytes(l1):
        raise ValueError('追加代码段与汇编产物不一致')
    # 5. whole-file exact diff.
    expected = {flash_pos + i for i in range(0x2020, OLD_APP_END)
                if original_plain[i] != plain[i]}
    expected |= {flash_pos + i for i in range(OLD_APP_END, new_app_end)
                 if result[flash_pos + i] != source[flash_pos + i]}
    expected |= {flash_pos + 0x2000 + i for i in range(32)
                 if result[flash_pos + 0x2000 + i] != source[flash_pos + 0x2000 + i]}
    expected |= {i for i in (0, 1, 2, 3, 68, 69) if result[i] != source[i]}
    diff = {i for i in range(len(source)) if result[i] != source[i]}
    if diff != expected:
        missing = sorted(expected - diff)[:24]
        unexpected = sorted(diff - expected)[:24]
        raise ValueError(f'字节差异与预期不符：missing={missing} unexpected={unexpected}')
    return sorted(diff), sorted(expected)


def _segment_bytes(l1):
    """The assembled code bytes recovered from the build-time listing."""
    out = bytearray()
    base = int(l1['segment_start'], 16)
    for item in l1['listing']:
        out += bytes.fromhex(item['bytes']) if item.get('bytes') else b''
    if len(out) != l1['code_bytes'] or int(l1['listing'][0]['address'], 16) != base:
        raise ValueError('listing 拼接长度/基址不一致')
    return bytes(out)
