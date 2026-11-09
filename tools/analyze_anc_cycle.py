#!/usr/bin/env python3
"""Audit the H3S ANC-key table and reconstruct its state transitions from known code.

The vendor disassembly is produced on the build host; analysis runs locally.
This does not send Bluetooth commands or change any firmware file.
"""
from pathlib import Path
import hashlib
import json
import re
import struct

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'analysis/anc-cycle-20261109'
BASE = 0x06000100
TABLE = 0x0609e510
app = (ROOT / 'analysis/v1.1.8/unpacked/files/app.bin').read_bytes()
assert hashlib.sha256(app).hexdigest() == '18f3d573a5b1a15f488a6803ce4f828634e602015c85e92d205bbe62d7060041'
assert app[TABLE-BASE:TABLE-BASE+3] == bytes([1, 3, 2])
assert app[0x9e17d:0x9e183] == bytes([1, 2, 2, 1, 3, 3])
assert app[0x18a86:0x18a8c] == bytes.fromhex('c4ff10e50906')

instructions = []
for line in (OUT / 'app-linear.asm').read_text().splitlines():
    m = re.match(r'\s*([0-9a-f]+):\s*((?:[0-9a-f]{2} )+)\s*(.*)', line)
    if not m:
        continue
    address = int(m[1], 16)
    raw = bytes.fromhex(m[2])
    assert app[address-BASE:address-BASE+len(raw)] == raw
    text = m[3]
    transfer = re.search(r'\b(call|goto) (-?\d+)\b', text)
    target = None
    if transfer:
        # pi32v2 branch operands are relative to the END of this instruction.
        target = address + len(raw) + int(transfer[2])
        line += ' ; target ' + hex(target)
    instructions.append((address, text, target, line))

references = []
needle = struct.pack('<I', TABLE)
pos = 0
while True:
    pos = app.find(needle, pos)
    if pos < 0:
        break
    references.append(pos)
    pos += 1
assert references == [0x18a88]
callers = [{'caller': hex(a), 'target': hex(t)} for a, text, t, _ in instructions
           if text.startswith('call ') and t == 0x06018b60]
assert any(x['caller'] == '0x6018ce8' for x in callers)

# Transcription of 0x06018b84..0x06018bc6, rather than a proposed new algorithm.
def machine_next(table, current, enabled):
    following = 0
    for i in range(3):
        if table[i] == current:
            following = i + 1
            if following >= 3:
                following = 0
            if not enabled & (1 << table[following]):
                following += 1
                if following >= 3:
                    following = 0
            break
    return table[following]

old = [1, 3, 2]
new = [2, 3, 2]
transitions = []
for mask in range(16):
    if mask & 12 != 12:  # Both ANC_ON=2 and TRANS=3 enabled.
        continue
    for start in (0, 1, 2, 3, 255):
        state = start
        trace = []
        for _ in range(100):
            state = machine_next(new, state, mask)
            trace.append(state)
        assert all(x in (2, 3) for x in trace)
        assert all(a != b for a, b in zip(trace, trace[1:]))
        transitions.append({'enabled_mask': mask, 'initial': start, 'first_6': trace[:6]})

ranges = [(0x06018b60, 0x06018be6), (0x06018cc0, 0x06018cee),
          (0x060189aa, 0x06018b60), (0x0600dcee, 0x0600de80),
          (0x06025d20, 0x06025dc0)]
for start, end in ranges:
    lines = [line for a, _, _, line in instructions if start <= a < end]
    (OUT / f'{start:08x}-{end:08x}.asm').write_text('\n'.join(lines) + '\n')

report = {
    'app_sha256': hashlib.sha256(app).hexdigest(),
    'analysis_kind': 'vendor disassembly, byte-checked, manually reconstructed control flow',
    'load_base': hex(BASE), 'anc_next': '0x06018b60', 'named_key_handler': '0x06018cc0',
    'cycle_table_address': hex(TABLE), 'cycle_table_app_offset': hex(TABLE-BASE),
    'original_table': old, 'candidate_table': new,
    'internal_modes': {'off': 1, 'anc_on': 2, 'transparent': 3},
    'qcy_wire_modes': {'off': 2, 'anc_on': 1, 'transparent': 3},
    'direct_table_address_occurrences': [hex(x) for x in references],
    'anc_next_callers': callers,
    'original_transitions_mask14': {str(i): machine_next(old, i, 14) for i in range(4)},
    'candidate_transitions_mask14': {str(i): machine_next(new, i, 14) for i in range(4)},
    'candidate_simulations': transitions,
    'instruction_bytes_modified': 0,
    'limitations': ['The published image has user-confirmed behavior; this simulation alone is not hardware verification',
                    'Direct pointer search does not prove absence of computed aliases',
                    'Reference SDK source is not the exact QCY source'],
}
(OUT / 'key-cycle-analysis.json').write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps({k: v for k, v in report.items() if k != 'candidate_simulations'}, indent=2))
