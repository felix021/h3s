#!/usr/bin/env python3
"""Re-check the hearing-aid L1 RAM data-slot address against official bytes.

Route CT / sim-slotram (2026-11-14). The --hearing-aid-l1-slotram candidate
moves the mic_effect stream-handle slot from the flash appended segment into
BSS RAM (docs/hearing-aid-l1-payload.md §10.4/§12). The close chain reads this
slot to find the stream written by open, so the address must be (a) zero at
boot and (b) never touched by any firmware code other than the payloads.
This tool machine-rechecks both properties, statically, every time it runs:

  1. boot-zero: the address lies inside the really-executed boot BSS zero
     loop [0x125320, 0x12a62c) (docs/boot-handoff.md, executed digests), is
     4-aligned, and sits below the QCY giant-struct base 0x1265C0 (fields are
     proven to reach +0x2CE6 = 0x1292A6, so the whole 0x1265C0..0x1299D0
     stretch is one structure's reach);
  2. no absolute anchor: no li.u32 in app.bin, the RAM-code source region or
     the (optional) public ROM asset materializes any address within +-16B
     of the slot;
  3. no traced access: a linear register-provenance pass (immediates, mov,
     constant add/sub, register-compose add, mul-by-const, post-increment
     walkers, static/indexed/post-inc bracket accesses) marks no byte within
     +-16B — compilers fold base+const field addresses into single li.u32
     anchors, so classes (2)+(3) together cover every statically identifiable
     access shape;
  4. no dynamic-array base: no register-composed or register-indexed access
     base within +-0x400 (unknown-index walkers get a conservative slab);
  5. no flash pointer-word: no little-endian u32 inside [slot-8, slot+12)
     anywhere in app.bin / RAM-code source / ROM at ANY byte alignment —
     this kills the static-pointer-table class (field pointers can be
     unaligned, e.g. 0x1253ba), which anchors alone never see.

A calibration table cross-checks known-live doc-pinned BSS bytes against the
same scan: most are covered; the two invisible ones are listed with their
explanations (the 0x1292A6 class is exactly why the slot must stay below
QCY_BASE). Residual classes no static scan can close — runtime-computed bulk
destinations (the VM-init rep-copy pattern), pointer values built at runtime
from data, DMA — stay documented in §12; nothing here is a runtime proof.

Static evidence only: no device access, no simulation, derived-not-measured.
"""
import argparse
import bisect
import json
import re
import struct
from pathlib import Path

from probe_boot import APP_SHA, ENTRY, application
from probe_pi32v2 import PIN, ROOT, require, upstream
from verify_ufw_integrity import decode, sfc

APP_BASE = ENTRY                    # 0x06000100
APP_DATA = 0x2120                   # decrypted-flash offset of app.bin (builder)
RAM_LO, RAM_HI = 0x120000, 0x1a0000
BSS_ZERO = (0x125320, 0x12a62c)     # really-executed boot zero loop
QCY_BASE = 0x1265c0                 # giant device struct (fields reach +0x2CE6)
QCY_TICK_FIELD = 0x1292a6           # = QCY_BASE + 0x2CE6, b[dev+11494] (HANDOFF)
RAM_SLOT = 0x125574                 # hearing_aid_l1_image.RAM_SLOT (re-checked)
HOLE = (0x12544e, 0x12569f)         # the statically dead 593B BSS hole
ANCHOR_MARGIN = 16                  # no anchor/covered byte within +-16B
DYN_RADIUS = 0x400                  # no dynamic base within +-1KiB
RAMCODE_RUNTIME, RAMCODE_LEN = 0x12a640, 18644   # docs/boot-handoff.md
RAMCODE_FLASH = 0x2020 + 0x060c3e18 - 0x06000000  # decrypted-flash offset
# the SFC keystream is keyed by offset relative to the app-area base 0x2020
# (check_reproduction.py decodes the app chunk with sfc() origin 0)
SFC_APP_BASE = 0x2020
# doc-pinned live BSS bytes used to calibrate the scan's blind spots
CALIBRATION = {
    0x1265c0: 'QCY struct base',
    0x126584: 'mic_effect scene-config byte target (route I)',
    0x1265EB: 'App 0x17 mode byte',
    0x126D70: 'clock mutex (li.u32 inside clock_free 0x06005730)',
    0x1276a4: 'VM config block (runtime rep-copy destination)',
    0x1292A6: 'QCY+0x2CE6 sfc bit (b[dev+11494], register-index class)',
    0x126540: 'per-core-tick struct base (fields fold to other anchors)',
    0x129D88: 'RAM-code IRQ data',
}

BR = re.compile(r'\[(r\d+)([^\]]*)\]')


def parse_bracket(suffix):
    s = suffix.strip()
    if s == '':
        return ('none', None)
    m = re.match(r'^\+(-?\d+)$', s)
    if m:
        return ('const', int(m.group(1)))
    m = re.match(r'^\+r(\d+)(?:<<(\d+))?$', s)
    if m:
        return ('index', (int(m.group(1)), int(m.group(2) or 0)))
    m = re.match(r'^\+\+=\s*(-?\d+)$', s)
    if m:
        return ('postinc', int(m.group(1)))
    m = re.match(r'^\+\+=\s*r(\d+)', s)
    if m:
        return ('postreg', int(m.group(1)))
    return ('bad', s)


def width_of(iid):
    if re.search(r'\.b($|\.)', iid):
        return 1
    if re.search(r'\.h($|\.)', iid):
        return 2
    return 4


def linear(module, data, base, isa):
    out, off, address = [], 0, base
    codec = module.codec
    while off < len(data):
        got = codec.decode(isa, data[off:off + 8])
        if got is None:
            off += 2
            address += 2
            continue
        insn, raw, size = got
        out.append((address, insn.id, raw, codec.format(insn, raw), size))
        off += size
        address += size
    return out


def coverage(streams, anchors_sorted):
    """Mark every statically identifiable RAM byte the streams can touch."""
    covered = []
    dyn_bases = set()
    anchors = set()

    def next_anchor(x):
        i = bisect.bisect_right(anchors_sorted, x)
        return anchors_sorted[i] if i < len(anchors_sorted) else x + 0x400

    for stream in streams:
        regs = {}
        for a, iid, raw, t, sz in stream:
            if iid.startswith('call'):
                for r in range(4):
                    regs[r] = None
                continue
            if iid.startswith(('goto', 'b', 'ret', 'rts', 'ijmp', 'nop',
                               'rep', 'loop', 'push', 'pop')):
                continue
            w = width_of(iid)
            brackets = [(int(m.group(1)[1:]), parse_bracket(m.group(2)))
                        for m in BR.finditer(t)]
            for b, (kind, val) in brackets:
                P = regs.get(b)
                if P is None:
                    continue
                if P[0] == 'abs':
                    v = P[1]
                    if not RAM_LO <= v < RAM_HI:
                        continue
                    if kind == 'none':
                        covered.append((v, v + w))
                    elif kind == 'const':
                        covered.append((v + val, v + val + w))
                    elif kind == 'postinc':
                        covered.append((v, v + w))
                        covered.append((v + val, v + val + w))
                    elif kind == 'postreg':
                        covered.append((v, v + w))
                    else:
                        dyn_bases.add(v)
                        covered.append((v, v + 0x100))
                elif P[0] == 'rel':
                    base = P[1]
                    if RAM_LO <= base < RAM_HI:
                        dyn_bases.add(base)
                        covered.append((base, next_anchor(base)))
            m = re.match(r'^r(\d+) (=|\+=|-=|\|=|&=)', t)
            if m:
                d, op = int(m.group(1)), m.group(2)
                if iid == 'li.u32' and op == '=':
                    n = raw.get('n')
                    regs[d] = ('abs', n) if isinstance(n, int) else None
                    if isinstance(n, int) and RAM_LO <= n < RAM_HI:
                        anchors.add(n)
                elif op == '=' and iid.startswith('mov.rr'):
                    mm = re.match(r'^r(\d+) = r(\d+)$', t)
                    regs[d] = regs.get(int(mm.group(2))) if mm else None
                elif iid == 'add.rr':
                    mm = re.match(r'^r(\d+) = r(\d+) \+ r(\d+)$', t)
                    if mm:
                        px, py = regs.get(int(mm.group(2))), regs.get(int(mm.group(3)))
                        if px and px[0] == 'abs' and py and py[0] == 'abs':
                            regs[d] = ('abs', px[1] + py[1])
                        elif py and py[0] == 'abs' and (px is None or px[0] == 'rel'):
                            regs[d] = ('rel', py[1])
                        elif px and px[0] == 'abs':
                            regs[d] = ('rel', px[1])
                        elif px and px[0] == 'rel':
                            regs[d] = ('rel', px[1])
                        else:
                            regs[d] = None
                    else:
                        regs[d] = None
                else:
                    mm = re.match(r'^r(\d+) \+= r(\d+)$', t)
                    if mm and regs.get(d) and regs[d][0] == 'abs' and \
                            regs.get(int(mm.group(2))) and \
                            regs[int(mm.group(2))][0] == 'abs':
                        regs[d] = ('abs', regs[d][1] + regs[int(mm.group(2))][1])
                    elif mm and regs.get(int(mm.group(2))) and \
                            regs[int(mm.group(2))][0] == 'abs':
                        regs[d] = ('rel', regs[int(mm.group(2))][1])
                    elif mm:
                        regs[d] = None
                    else:
                        mc = re.match(r'^r(\d+) = r(\d+) \+ (-?\d+)$', t)
                        if mc and regs.get(int(mc.group(2))) and \
                                regs[int(mc.group(2))][0] == 'abs':
                            regs[d] = ('abs',
                                       regs[int(mc.group(2))][1] + int(mc.group(3)))
                        elif op in ('+=', '-=') and regs.get(d) and regs[d][0] == 'abs':
                            ms = re.match(r'^r(\d+) ([+-])= (-?\d+)$', t)
                            if ms:
                                regs[d] = ('abs', regs[d][1] + int(ms.group(3))
                                           * (1 if ms.group(2) == '+' else -1))
                            else:
                                regs[d] = None
                        elif 'mul' in iid:
                            mmul = re.match(r'^r(\d+) = r(\d+) \* (-?\d+)$', t)
                            if mmul and regs.get(int(mmul.group(2))) and \
                                    regs[int(mmul.group(2))][0] == 'abs':
                                regs[d] = ('abs', regs[int(mmul.group(2))][1]
                                           * int(mmul.group(3)))
                            else:
                                regs[d] = None
                        else:
                            regs[d] = None
            for b, (kind, val) in brackets:
                if kind == 'postinc' and regs.get(b) and regs[b][0] == 'abs':
                    regs[b] = ('abs', regs[b][1] + val)
    for v in anchors:
        covered.append((v, v + 4))
    covered = [(max(s, RAM_LO), min(e, RAM_HI)) for s, e in covered
               if s < RAM_HI and e > RAM_LO]
    covered.sort()
    merged = []
    for s, e in covered:
        if merged and s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    return merged, dyn_bases, anchors


def pointer_words(data, lo, hi):
    """Little-endian u32 in [lo, hi) at ANY byte alignment (field pointers
    like 0x1253ba are unaligned)."""
    hits = []
    for off in range(0, len(data) - 3):
        v = struct.unpack_from('<I', data, off)[0]
        if lo <= v < hi:
            hits.append((off, v))
    return hits


def analyze(firmware, rom_path):
    module = upstream()
    from hearing_aid_l1_image import RAM_SLOT as BUILDER_SLOT
    require(BUILDER_SLOT == RAM_SLOT,
            'builder RAM_SLOT drifted from the pinned evidence address')
    app = application(firmware)
    require(sha_bytes(app) == APP_SHA, 'unexpected application bytes')
    ufw = firmware.read_bytes()
    flash_pos = None
    count = struct.unpack_from('<H', decode(ufw[0:64]), 8)[0]
    for i in range(count):
        row = decode(ufw[64 + i * 80:144 + i * 80])
        kind, _, _, _, pos, _size, _res = struct.unpack_from('<HHHHIII', row, 0)
        if kind == 0:
            flash_pos = pos
    require(flash_pos is not None, 'no flash.bin entry')
    # keystream sanity: the decrypted-flash offset 0x2120 must hold app.bin[0]
    require(sfc(ufw[flash_pos + APP_DATA:flash_pos + APP_DATA + 8],
                origin=APP_DATA - SFC_APP_BASE) == app[0:8],
            'SFC keystream base mismatch (app.bin[0] does not decrypt)')

    def sfc_at(flash_off, size):
        """Decrypt [flash_off, flash_off+size) with correct 32B block phase.

        The keystream key advances per 32B block of the app area (base
        0x2020): jl_sfc_cipher uses key ^ ((offset - base) >> 2) with
        blocksize 32, so a chunk not aligned to that grid must be decrypted
        from its aligned start and sliced afterwards.
        """
        rel = flash_off - SFC_APP_BASE
        aligned = rel & ~31
        span = (size + rel - aligned + 31) & ~31
        plain = sfc(ufw[flash_pos + SFC_APP_BASE + aligned:
                        flash_pos + SFC_APP_BASE + aligned + span],
                    origin=aligned)
        return plain[rel - aligned:rel - aligned + size]

    ramcode_plain = sfc_at(RAMCODE_FLASH, RAMCODE_LEN)
    isa = module.table.load()
    streams = [('app', linear(module, app, APP_BASE, isa)),
               ('ramcode', linear(module, ramcode_plain, RAMCODE_RUNTIME, isa))]
    rom = None
    if rom_path is not None:
        rom = rom_path.read_bytes()
        streams.append(('rom', linear(module, rom, 0, isa)))
    flat = [s for _, s in streams]
    anchors_sorted = sorted({raw.get('n') for st in flat
                             for _, iid, raw, _t, _s in st if iid == 'li.u32'
                             if isinstance(raw.get('n'), int)
                             and RAM_LO <= raw['n'] < RAM_HI})
    merged, dyn_bases, anchors = coverage(flat, anchors_sorted)

    def covered(addr):
        for s, e in merged:
            if s <= addr < e:
                return True
        return False

    report = {'application_sha256': APP_SHA, 'simulator_commit': PIN,
              'slot_address': f'0x{RAM_SLOT:08x}',
              'bss_zero_range': ['0x125320', '0x12a62c'],
              'scanned': {name: len(s) for name, s in streams}}
    # 1. boot-zero + geometry invariants
    require(BSS_ZERO[0] <= RAM_SLOT < BSS_ZERO[1] - 3 and RAM_SLOT % 4 == 0,
            'slot outside aligned boot-zero range')
    require(RAM_SLOT < QCY_BASE, 'slot inside QCY struct reach')
    require(RAM_SLOT - HOLE[0] >= 0x100 and HOLE[1] - (RAM_SLOT + 4) >= 0x100,
            'slot margins inside the dead hole shrank')
    report['slot_invariants'] = {
        'boot_zeroed': True, 'aligned': True, 'below_qcy': True,
        'hole': ['0x12544e', '0x12569f'],
        'margins_bytes': [RAM_SLOT - HOLE[0], HOLE[1] - RAM_SLOT - 4]}
    # 2/3. no anchor and no traced access within +-16B
    near = [hex(v) for v in anchors_sorted
            if abs(v - RAM_SLOT) <= ANCHOR_MARGIN]
    require(not near, f'absolute anchors within ±16B of the slot: {near}')
    touched = [hex(a) for a in range(RAM_SLOT - ANCHOR_MARGIN,
                                     RAM_SLOT + 4 + ANCHOR_MARGIN)
               if covered(a)]
    require(not touched, f'provenance-traced access within ±16B: {touched}')
    report['no_anchor_within_16b'] = True
    report['no_traced_access_within_16b'] = True
    # 4. no dynamic base within +-0x400
    dyn_near = sorted(hex(b) for b in dyn_bases if abs(b - RAM_SLOT) <= DYN_RADIUS)
    require(not dyn_near, f'dynamic-index bases within ±0x400: {dyn_near}')
    report['no_dynamic_base_within_0x400'] = True
    report['dynamic_base_count_total'] = len(dyn_bases)
    # 5. no pointer-word into the hole from any scanned byte blob
    ptr = {'app.bin': pointer_words(app, HOLE[0], HOLE[1]),
           'ramcode-src': pointer_words(ramcode_plain, HOLE[0], HOLE[1])}
    if rom is not None:
        ptr['rom'] = pointer_words(rom, HOLE[0], HOLE[1])
    for name, hits in ptr.items():
        require(not hits, f'{name} carries pointer-words into the hole: {hits[:4]}')
    report['flash_pointer_words_into_hole'] = {k: len(v) for k, v in ptr.items()}
    # calibration: live bytes the scan must explain (or list as the known
    # invisible classes that motivated the QCY_BASE / margins rules)
    require(QCY_BASE + 0x2CE6 == QCY_TICK_FIELD, 'QCY field arithmetic drifted')
    calib = {f'0x{a:08x}': {'note': note, 'covered_by_scan': covered(a)}
             for a, note in CALIBRATION.items()}
    uncovered = [a for a, v in calib.items() if not v['covered_by_scan']]
    require(sorted(uncovered) == ['0x00126540', '0x001292a6'],
            f'unexpected calibration misses (scan blind spots changed): {uncovered}')
    report['calibration'] = calib
    report['residual_classes'] = [
        'runtime-computed bulk destinations (VM-init rep-copy pattern)',
        'pointer values built at runtime from data',
        'DMA writes (audio buffers assumed outside BSS)',
        'p11 CPU (own address space; one-off scan found 0 main-RAM refs)',
    ]
    report['static_only'] = True
    report['device_verified'] = False
    return report


def sha_bytes(data):
    import hashlib
    return hashlib.sha256(data).hexdigest()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--firmware', type=Path,
                   default=ROOT / 'firmware/QCY_H3S_7018F_1T2_release_note_V1.1.8_011.ufw')
    p.add_argument('--rom', type=Path, default=None,
                   help='optional public ROM asset QCY_H3S_BR28_ROM_000000-020000.bin')
    p.add_argument('--report', type=Path, required=True)
    args = p.parse_args()
    report = analyze(args.firmware, args.rom)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + '\n')
    print('RAM slot 0x125574: boot-zeroed, no static access within ±16B, '
          f'no dynamic base within ±0x400, {report["dynamic_base_count_total"]} '
          'dynamic bases scanned; static evidence only')


if __name__ == '__main__':
    main()
