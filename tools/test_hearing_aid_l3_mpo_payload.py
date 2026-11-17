#!/usr/bin/env python3
"""Offline verification of the hearing-aid L3 MPO consumption payload (route HAL3).

apply_mpo.S is the firmware-side consumer of VM key 30: protocol-gated
{magic "HA", ver 1, rsv 0, float32 M} -> L2 horizontal-tail curve on the
official Drc1Eff 76B template -> jlstream_set_node_param(0xdefe, "Drc1Eff",
packet, 76). These tests pin the payload standalone (round-trip, anchors,
constants, template/name bytes against the OFFICIAL image, address-layout
insensitivity of the .equ pair). The composed-segment and image-layer checks
live in test_hearing_aid_l3_mpo_image.py.

Run: python3 tools/test_hearing_aid_l3_mpo_payload.py  (0.5s, no firmware
input needed for the payload tests; the template test uses the official
stream.bin from analysis/v1.1.8 when present and skips with a notice when
the asset is not materialized).
"""
import re
import struct
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import pi32v2_asm as ASM  # noqa: E402

SOURCE = ROOT / 'tools/hearing-aid/apply_mpo.S'
OFFICIAL_STREAM = ROOT / 'analysis/v1.1.8/unpacked/files/stream.bin'

API_ANCHORS = {'GET_CONFIG': 0x06000f88, 'SET_NODE_PARAM': 0x0607538a}
CONSTANTS = [30, 8, 0x48, 0x41, 1,
             0x3E99999A,      # 0.3f bit pattern (exclusive lower bound)
             0x42F00000,      # 120.0f bit pattern (exclusive upper bound)
             0xdefe, 76, 19]
OFFICIAL_TEMPLATE = bytes.fromhex(
    '00000000' '6400c800'
    '9a99993e' '9a99993e'
    '9a99b442' '9a99b442'
    '9a99dc42' '9a99dc42'
    '9a99b442' '9a99b442'
    '9a99b442' '9a99b442'
    '9a99b442' '9a99b442'
    '00000000' '00000000' '0000c841'
    '03000001' '00000100')
RAISED_TAIL = struct.pack('<f', 120.0)   # 00 00 f0 42


def _assemble_with(template=0x060e3400, name=0x060e3450):
    text = SOURCE.read_text()
    text = re.sub(r'\.equ MPO_TEMPLATE_ADDR, 0x[0-9a-fA-F]+',
                  f'.equ MPO_TEMPLATE_ADDR, 0x{template:x}', text, count=1)
    text = re.sub(r'\.equ MPO_NAME_ADDR, 0x[0-9a-fA-F]+',
                  f'.equ MPO_NAME_ADDR, 0x{name:x}', text, count=1)
    obj = ASM.read_object(ASM.assemble_text(text, str(SOURCE)))
    sections = {s.name: bytes(s.data) for s in obj.sections if s.data}
    return obj, sections


class ApplyMpoPayloadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.obj, cls.sections = _assemble_with()
        cls.code = cls.sections['.text.apply_mpo']
        cls.template = cls.sections['.rodata.mpo_template']
        cls.name = cls.sections['.rodata.mpo_name']
        cls.insns = list(ASM.disassemble(cls.code))
        texts = [t for _a, _i, t, _r in cls.insns]
        cls.texts = texts

    def test_01_roundtrip_and_size(self):
        """Every instruction re-encodes to its own bytes; shape in [30, 60]."""
        for addr, iid, text, raw in self.insns:
            self.assertIsNotNone(iid, f'undecodable at {addr:#x}')
            insn, values, _n = ASM.decode(self.code, addr)
            self.assertEqual(ASM.encode(iid, **values), raw,
                             f're-encode differs at {addr:#x} ({text})')
        self.assertTrue(30 <= len(self.insns) <= 60,
                        f'implausible instruction count {len(self.insns)}')

    def test_02_api_anchors_once_with_paired_call(self):
        """Both API anchors load exactly once, each immediately used by call r6."""
        for equ, value in API_ANCHORS.items():
            loads = [t for t in self.texts if t == f'r6 = {value}']
            self.assertEqual(loads, [f'r6 = {value}'], f'{equ} load count')
        for i, (_a, _iid, text, _raw) in enumerate(self.insns):
            if text.startswith('r6 = ') and int(text.split('= ')[1]) in API_ANCHORS.values():
                self.assertEqual(self.insns[i + 1][1], 'call.r',
                                 'API load not immediately called')

    def test_03_constants_present(self):
        joined = ' | '.join(self.texts)
        for value in CONSTANTS:
            self.assertIn(str(value), joined, f'constant {value} missing')

    def test_04_protocol_guards_before_any_store(self):
        """get_config length gate + 4 byte guards + both range guards all
        precede the first knee store (a rejected packet never touches memory
        beyond the stack frame)."""
        first_store = next(i for i, (_a, _iid, t, _r) in enumerate(self.insns)
                           if t.startswith('[r3+'))
        gate_texts = ['if (r0 != 8)', 'if (r5 != 72)', 'if (r5 != 65)',
                      'if (r5 != 1)', 'if (r5 != 0)',
                      'if (r5 <= r6)', 'if (r5 >= r6)']
        for gate in gate_texts:
            idx = next((i for i, (_a, _iid, t, _r) in enumerate(self.insns)
                        if t.startswith(gate)), None)
            self.assertIsNotNone(idx, f'gate missing: {gate}')
            self.assertLess(idx, first_store, f'gate after stores: {gate}')

    def test_05_knee_stores_and_call_shape(self):
        """Three knee stores write M to th[1].in/+16, th[1].out/+20, th[2].out/+28;
        the packet pointer feeds set_node_param(uuid, name, packet, 76)."""
        stores = [t for t in self.texts if t.startswith('[r3+')]
        self.assertEqual(stores, ['[r3+16] = r5', '[r3+20] = r5', '[r3+28] = r5'])
        send_idx = next(i for i, (_a, _iid, t, _r) in enumerate(self.insns)
                        if t == f'r6 = {API_ANCHORS["SET_NODE_PARAM"]}')
        # the argument setups precede the API load: uuid, name, buf, len
        prefix = ' | '.join(self.texts[:send_idx])
        self.assertIn('r3 = 76', prefix)
        self.assertIn('r2 = r3', prefix)
        self.assertIn('r0 = 57086', prefix)          # 0xdefe as decimal

    def test_06_template_matches_official_except_raised_tail(self):
        """Template == official Drc1Eff 76B with exactly one edit:
        th[2].in (+24) 110.3f -> 120.0f (tail above the M upper bound)."""
        expected = OFFICIAL_TEMPLATE[:24] + RAISED_TAIL + OFFICIAL_TEMPLATE[28:]
        self.assertEqual(len(self.template), 76)
        self.assertEqual(self.template, expected)
        if OFFICIAL_STREAM.exists():
            stream = OFFICIAL_STREAM.read_bytes()
            official = stream[0x18a9:0x18a9 + 76]
            self.assertEqual(official, OFFICIAL_TEMPLATE,
                             'hardcoded official template drifted')
            self.assertEqual(self.template,
                             official[:24] + RAISED_TAIL + official[28:],
                             'template vs live official stream.bin')

    def test_07_name_field(self):
        """16B name field: "Drc1Eff\\0" + zero padding (§2 instance name)."""
        self.assertEqual(self.name, b'Drc1Eff\x00' + b'\x00' * 8)

    def test_08_address_layout_insensitive(self):
        """Changing both MPO_*_ADDR .equ values moves bytes only inside the
        two li.u32 immediates (same two-pass contract as MIC_EFX_SLOT)."""
        _obj2, sections2 = _assemble_with(0x060e4000, 0x060e4100)
        code2 = sections2['.text.apply_mpo']
        self.assertEqual(len(code2), len(self.code))
        diffs = [i for i, (a, b) in enumerate(zip(self.code, code2)) if a != b]
        self.assertTrue(diffs, 'expected address-dependent bytes')
        # each li.u32 immediate is 4 payload bytes at offset+2 of a 6B insn
        li_spans = []
        offset = 0
        for _a, iid, _t, raw in self.insns:
            if iid == 'li.u32' and self.code[offset:offset + 6].hex() != \
                    code2[offset:offset + 6].hex():
                li_spans.append(range(offset + 2, offset + 6))
            offset += len(raw)
        for i in diffs:
            self.assertTrue(any(i in span for span in li_spans),
                            f'byte {i} changed outside the .equ immediates')

    def test_09_stack_frame_balanced(self):
        """FRAME_STACK (88) push/pop pair; callee-saved prologue/epilogue."""
        self.assertEqual(self.insns[0][1], 'push.rets_rl')
        self.assertIn('sp += -88', self.texts)
        self.assertIn('sp += 88', self.texts)
        self.assertEqual(self.insns[-1][1], 'pop.pc_rl')

    def test_10_frame_holds_key_and_packet(self):
        """88B frame = 8B key buffer + 76B packet (+4 alignment); the packet
        base is derived as frame+8 before the template copy."""
        self.assertIn('r4 = sp', self.texts)
        self.assertIn('r0 = r4 + 8', self.texts)


if __name__ == '__main__':
    unittest.main()
