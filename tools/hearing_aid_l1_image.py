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
LEAVE_INSN_IDS = ['li.u32', 'call.r', 'ld.w.l', 'call.s', 'mov.rr',
                  'li.u32', 'goto.r']
LEAVE_ON2_INSN_IDS = ['li.u32', 'call.r', 'ld.w.l', 'call.s', 'li.u32',
                      'call.r', 'li.u32', 'goto.r']
PAYLOAD_SOURCE = ROOT / 'tools/hearing-aid/mic_effect_open.S'
HOOK_SOURCE = ROOT / 'tools/hearing-aid/mic_effect_hook.S'
CLOSE_SOURCE = ROOT / 'tools/hearing-aid/mic_effect_close.S'
CLOSE_HOOK_SOURCE = ROOT / 'tools/hearing-aid/mic_effect_close_hook.S'
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


def _compose(slot, close_hooks=False):
    """Concatenate payload source (slot-patched) + hook stubs into one text."""
    text = _patch_slot(PAYLOAD_SOURCE.read_text(), slot)
    if close_hooks:
        text += _strip_close_section(_patch_slot(CLOSE_SOURCE.read_text(), slot))
    return text + HOOK_SOURCE.read_text() + \
        (CLOSE_HOOK_SOURCE.read_text() if close_hooks else '')


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


def assemble_segment(close_hooks=False, slot_ram=False):
    """Assemble payload+stubs at PAYLOAD_ADDRESS with the real slot address.

    Two passes: the section length is layout-stable (test_hearing_aid_payload /
    test_hearing_aid_close_payload prove only the slot li.u32 immediates depend
    on the slot value), so pass 1 measures the length and pass 2 bakes
    slot = base + length. slot_ram=True (route CT) instead pins the slot to
    the boot-zeroed BSS address RAM_SLOT — layout-independent, so the two-pass
    byte-diff assertion does not apply — and the segment carries no trailing
    slot bytes. Returns a report dict with bytes, entry offsets and the
    disassembly.
    """
    if slot_ram:
        if not close_hooks:
            raise ValueError('slot_ram requires close_hooks (route CT is a close superset)')
        _slot_checks(RAM_SLOT)
        slot = RAM_SLOT
    _, first = _assemble(_compose(PAYLOAD_ADDRESS, close_hooks))
    if not slot_ram:
        slot = PAYLOAD_ADDRESS + len(first)
    obj, code = _assemble(_compose(slot, close_hooks))
    if len(code) != len(first):
        raise ValueError('slot immediate changed the section layout')
    if slot_ram:
        diffs = []                   # slot independent of layout: single pass
    else:
        diffs = [i for i, (a, b) in enumerate(zip(first, code)) if a != b]
        if any(code[i] == first[i] for i in diffs) or not diffs:
            raise ValueError('expected slot-only byte differences between passes')
    names = ['mic_effect_open', 'trans_branch_1', 'trans_branch_2']
    if close_hooks:
        names += ['mic_effect_close'] + [w[4] for w in CLOSE_WINDOWS]
    symbols = {s.name: s.value for s in obj.symbols if s.name in names}
    if len(symbols) != len(names):
        raise ValueError(f'missing section symbols: {set(names) - set(symbols)}')
    stub_offsets = [symbols[n] for n in ('trans_branch_1', 'trans_branch_2')]
    for name, off in symbols.items():
        if name != 'mic_effect_open' and not 0 < off < len(code):
            raise ValueError(f'symbol {name} out of segment')
    if symbols['mic_effect_open'] != 0:
        raise ValueError('mic_effect_open must open the segment')
    listing = []
    for addr, insn_id, text, raw in ASM.disassemble(code, PAYLOAD_ADDRESS):
        listing.append({'address': f'0x{addr:08x}', 'bytes': raw.hex(),
                        'insn': insn_id, 'text': text})
        if insn_id is None:
            raise ValueError(f'undecodable word at {addr:#x}')
        insn, values, length = ASM.decode(code, addr - PAYLOAD_ADDRESS)
        if ASM.encode(insn_id, **values) != raw:
            raise ValueError(f're-encode differs at {addr:#x} ({text})')
    slot_tail = 0 if slot_ram else 4
    report = {'variant': ('open+close+slotram' if slot_ram else
                          'open+close' if close_hooks else 'open'),
              'payload_bytes': stub_offsets[0], 'stub_bytes': len(code) - stub_offsets[0],
              'code_bytes': len(code), 'slot_address': f'0x{slot:08x}',
              'slot_location': 'ram-bss' if slot_ram else 'flash-tail',
              'slot_boot_value': ('boot BSS zero loop [0x125320,0x12a62c) '
                                  '(docs/boot-handoff.md)' if slot_ram
                                  else 'image bytes 00 00 00 00'),
              'segment_start': f'0x{PAYLOAD_ADDRESS:08x}',
              'segment_end_runtime': f'0x{PAYLOAD_ADDRESS + len(code) + slot_tail:08x}',
              'stub_1_address': f'0x{PAYLOAD_ADDRESS + stub_offsets[0]:08x}',
              'stub_2_address': f'0x{PAYLOAD_ADDRESS + stub_offsets[1]:08x}',
              'code_sha256': hashlib.sha256(code).hexdigest(),
              'slot_diff_offsets': diffs, 'listing': listing}
    if close_hooks:
        report['close_address'] = f'0x{PAYLOAD_ADDRESS + symbols["mic_effect_close"]:08x}'
        report['leave_stub_addresses'] = {
            tag: f'0x{PAYLOAD_ADDRESS + symbols[label]:08x}'
            for tag, _a, _b, _s, label in CLOSE_WINDOWS}
    _verify_semantics(code, stub_offsets, slot, report, close_hooks)
    return report, code, slot


def _verify_semantics(code, stub_offsets, slot, report, close_hooks=False):
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
    # stubs: exact instruction-shape + absolute firmware targets
    epi = {'A': WINDOWS[0][3], 'B': WINDOWS[1][3]}
    stub_b_end = len(code)
    if close_hooks:
        stub_b_end = int(report['leave_stub_addresses']['ON-1'], 16) - PAYLOAD_ADDRESS
    for tag, stub_off, other in (('A', stub_offsets[0], stub_offsets[1]),
                                 ('B', stub_offsets[1], stub_b_end)):
        end = other
        shape = [by_off[o][0] for o in range(stub_off, end) if o in by_off]
        if shape != STUB_INSN_IDS:
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
                or iid != 'call.s' or target != PAYLOAD_ADDRESS):
            raise ValueError(f'stub {tag} targets mismatch: loads={loads} call={t} target={target:#x}')
    if close_hooks:
        _verify_leave_stubs(code, by_off, report)
    # slot loads/stores in the payload part resolve to the real slot address
    # (open: guard + record; close: guard + clear — both load r0 = slot)
    expected_loads = [slot] * (4 if close_hooks else 2)
    slot_loads = [int(t.split('= ')[1]) for o, (iid, t, raw) in sorted(by_off.items())
                  if iid == 'li.u32' and o < stub_offsets[0] and t.startswith('r0 = ')]
    if slot_loads != expected_loads:
        raise ValueError(f'slot immediates mismatch: {slot_loads}')


def _verify_leave_stubs(code, by_off, report):
    """The four ON/OFF leave stubs (§10.5): shape + every firmware target."""
    stubs = sorted(((tag, int(report['leave_stub_addresses'][tag], 16)
                     - PAYLOAD_ADDRESS, address, mode_slot)
                    for tag, address, _orig, mode_slot, _label in CLOSE_WINDOWS),
                   key=lambda t: t[1])
    close_off = int(report['close_address'], 16) - PAYLOAD_ADDRESS
    for index, (tag, stub_off, address, mode_slot) in enumerate(stubs):
        end = stubs[index + 1][1] if index + 1 < len(stubs) else len(code)
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


def apply_l1_patch(flash, close_hooks=False, slot_ram=False):
    """Apply the implant edits to the decrypted flash image in place.

    close_hooks=True builds the open+close segment (route Y) and additionally
    replaces the four ON/OFF leave windows. slot_ram=True (route CT) pins the
    data slot to boot-zeroed BSS instead of the flash segment tail (implies
    the close variant). Returns the report dict (incl. new_app_end for the
    caller's SFC/boot-info pass). Per-site byte assertions keep this valid
    with or without the ANC two-state table change, which edits one unrelated
    app.bin byte first.
    """
    if slot_ram and not close_hooks:
        raise ValueError('slot_ram requires close_hooks (route CT is a close superset)')
    segment, code, slot = assemble_segment(close_hooks, slot_ram=slot_ram)
    slot_tail = 0 if slot_ram else 4
    end = OLD_APP_END + 32 + 1 + len(code) + slot_tail   # header + 0xff + code (+ slot)
    if end > len(flash) or any(v != 255 for v in flash[OLD_APP_END:end]):
        raise ValueError('non-padding bytes at the L1 append location')
    hooks = []
    # one uniform window spec: (tag, address, original 8B, stub address text,
    # replaced-instruction description, only-incoming-branch note)
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
