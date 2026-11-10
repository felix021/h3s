#!/usr/bin/env python3
"""Audit EQ/key entry points in the original H3S firmware; never patch or send data."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import struct

ROOT = Path(__file__).resolve().parents[1]
BASE = 0x06000100
SOURCE_SHA256 = '18f3d573a5b1a15f488a6803ce4f828634e602015c85e92d205bbe62d7060041'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app', type=Path, default=ROOT / 'analysis/v1.1.8/unpacked/files/app.bin')
    parser.add_argument('--asm', dest='assembly', type=Path,
                        default=ROOT / 'analysis/anc-cycle-20261109/app-linear.asm')
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'analysis/eq-toggle')
    args = parser.parse_args()
    app = args.app.read_bytes()
    if hashlib.sha256(app).hexdigest() != SOURCE_SHA256:
        raise SystemExit('Expected the unpacked ORIGINAL 0.1.1.8 app.bin')

    rows = {}
    for line in args.assembly.read_text().splitlines():
        match = re.match(r'\s*([0-9a-f]+):\s*((?:[0-9a-f]{2} )+)\s*(.*)', line)
        if not match:
            continue
        address = int(match[1], 16)
        raw = bytes.fromhex(match[2])
        if app[address - BASE:address - BASE + len(raw)] != raw:
            raise SystemExit(f'Disassembly byte mismatch at {address:#x}')
        transfer = re.search(r'\b(call|goto) (-?\d+)\b', match[3])
        target = address + len(raw) + int(transfer[2]) if transfer else None
        rows[address] = (match[3], target)

    def require(address, text=None, target=None):
        instruction, destination = rows[address]
        if text is not None and instruction != text:
            raise SystemExit(f'Unexpected instruction at {address:#x}: {instruction}')
        if target is not None and destination != target:
            raise SystemExit(f'Unexpected transfer at {address:#x}')

    # The action is reduced by one; tbb entries are offsets in halfwords.
    require(0x06025370, 'r1 = r5 + -1')
    require(0x06025376, 'if (r1 > 10) goto 42', 0x060253A4)
    require(0x06025382, 'tbb [r1]')
    table_offset = 0x06025384 - BASE
    action_targets = [0x06025384 + 2 * value
                      for value in app[table_offset:table_offset + 11]]
    expected = [0x06025396, 0x060253AC, 0x060253BC, 0x060253CC,
                0x060253E8, 0x060253F6, 0x06025404, 0x06025392,
                0x06025390, 0x06025392, 0x0602544E]
    if action_targets != expected:
        raise SystemExit('Unexpected key-action jump table')
    require(0x060253A6, 'if (r5 == 127) goto -158', 0x0602530C)
    require(0x060254AA, target=0x060251FC)  # Existing music/game/movie mode change.
    require(0x0602548C, target=0x06018B60)  # Hidden action 11 is ANC, not EQ.
    if app[0x9E153:0x9E156] != bytes([7, 2, 7]):
        raise SystemExit('Unexpected default multifunction double-click triple')

    # QCY's seven-word registration record for EQ parameter command 0x22.
    record = struct.unpack_from('<7I', app, 0xA9A78)
    if record != (0x06025774, 0x0602580A, 0x0602583C, 0x06025882,
                  0x060258B6, 0x060258EA, 0x22):
        raise SystemExit('Unexpected EQ protocol registration')
    require(0x0602587A, target=0x06025118)
    require(0x0602514C, target=0x06024FE6)

    # Node UUID 0x737b, with its actual input/output-control handler.
    descriptor = struct.unpack_from('<5I', app, 0xA8E88)
    if descriptor != (0x737B, 0x10000, 0x0607876A, 0x0607898E, 0x06078BB0):
        raise SystemExit('Unexpected EQ node descriptor')
    require(0x06078A0A, 'r0 += -2')
    require(0x06078A18, 'tbh [r1]')
    bypass_destination = 0x06078A1A + 2 * struct.unpack_from('<H', app, 0x06078A1A - BASE)[0]
    if bypass_destination != 0x06078A2E:
        raise SystemExit('Unexpected EQ subcommand-2 branch')
    require(0x06078A2E, 'r0 = b[r5+4] (u)')
    require(0x06078A30, 'b[r9+3] = r0')
    require(0x06078A34, 'if (r0 == 0) goto 296', 0x06078B60)
    require(0x06078A3A, target=0x0607895C)
    require(0x06078B66, target=0x060787AC)

    # Startup copies initialized RAM from app+0xc3238 to 0x124840. The custom
    # EQ loop's two name buffers are at 0x124c80+456 and +472 respectively.
    require(0x06000136, 'r4 = 1198144')
    require(0x0600013C, 'r1 = 101462840')
    for name, ram in [(b'MusicEqBt', 0x124E48), (b'Eq0Media', 0x124E58)]:
        offset = 0x060C3338 - BASE + ram - 0x124840
        if app[offset:offset + 16].split(b'\0')[0] != name:
            raise SystemExit('Unexpected initialized EQ node names')
    # QCY state byte is in the BSS region cleared at boot, not guessed free RAM.
    require(0x06000120, 'r3 = 1200928')
    require(0x06000128, 'r2 = 21260')
    assert 0x125320 <= 0x1265C0 + 42 < 0x125320 + 21260

    panel = json.loads((ROOT / 'data/controlpanel_qcyh3s_16764.json').read_text())
    events = [event for layout in panel['layouts'] for key in layout.get('buttons', [])
              for event in key.get('events', []) if event['eventID'] == 0x0702]
    if len(events) != 1 or sorted(item['cmd'] for item in events[0]['list']) != list(range(8)):
        raise SystemExit('Unexpected App multifunction-double-click menu')
    report = {
        'status': 'static audit of ORIGINAL app; does not validate a built EQ candidate',
        'source_app_sha256': SOURCE_SHA256,
        'all_parsed_disassembly_bytes_match_source': True,
        'multifunction_double_click': {'event_id': '0x0702', 'default_action': events[0]['defaultFun'],
                                     'default_firmware_key_gesture_action': [7, 2, 7],
                                     'menu_actions': list(range(8))},
        'key_dispatch': '0x060252c6',
        'action_destinations': {str(i + 1): hex(address) for i, address in enumerate(action_targets)},
        'eq_parameter_command': '0x22',
        'eq_getter': '0x0602580a', 'eq_setter': '0x0602583c',
        'eq_apply': '0x06025118',
        'preset_loader_not_an_enable_flag': '0x06024fe6',
        'eq_node_uuid': '0x737b', 'node_control': '0x0607898e',
        'node_parameter_sender': '0x0607538a',
        'app_eq_node_names': ['MusicEqBt', 'Eq0Media'],
        'bypass_subcommand': 2, 'bypass_branch': hex(bypass_destination),
        'bypass_value_offset_in_control_packet': 4,
        'nonzero_bypass_calls': '0x0607895c', 'zero_bypass_may_open_via': '0x060787ac',
        'implementation_requirements': [
            'Bind the actual ordinary key/gesture mapping [7,2] rather than guessing 0x82',
            'Use bounded hooks/state storage without overwriting unrelated live code or RAM',
            'Preserve current App curve; reopening the filter can load stored file parameters',
            'Reapply bypass across audio-node recreation and define App-edit behavior',
            'Preserve the ANC patch; see docs/eq-toggle.md for current experimental build status',
        ],
        'limits': [
            'No hidden EQ action exists in this inspected key-action switch; this is not an exhaustive firmware proof',
            'The active old/new App key-configuration protocol branch was not established',
            'Static control-flow evidence does not establish runtime audio routing or audible behavior',
            'Bypassing this EQ node does not bypass all DSP, ANC, or factory speaker correction',
        ],
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / 'analysis.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
