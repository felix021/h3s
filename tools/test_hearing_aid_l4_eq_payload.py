#!/usr/bin/env python3
"""Offline verification of the hearing-aid L4 EQ ten-band payload (route HAL4).

apply_eq.S is the firmware-side consumer of VM key 31: protocol-gated
{magic "HA", ver 2, seg_count 10, 10x{u16 freq, u16 pad, float32 gain}} ->
per-segment EQ_SEG 44B packets -> jlstream_set_node_param(0x737b, "Eq1Eff",
packet, 44), ten messages, each required to return 1. These tests pin the
payload standalone (round-trip, anchors, constants, whole-packet gates before
the first send, template bytes cross-checked against the OFFICIAL stream.bin
Eq1Eff blob, address-layout insensitivity of the .equ pair). The
composed-segment and image-layer checks live in test_hearing_aid_l4_eq_image.py.

Run: python3 tools/test_hearing_aid_l4_eq_payload.py  (0.5s, no firmware
input needed for the payload tests; the template cross-check uses the
official stream.bin from analysis/v1.1.8 when present and skips otherwise).
"""
import re
import struct
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import pi32v2_asm as ASM  # noqa: E402

SOURCE = ROOT / 'tools/hearing-aid/apply_eq.S'
OFFICIAL_STREAM = ROOT / 'analysis/v1.1.8/unpacked/files/stream.bin'

API_ANCHORS = {'GET_CONFIG': 0x06000f88, 'SET_NODE_PARAM': 0x0607538a}
CONSTANTS = [31, 88, 0x00024148, 10, 21599, 0x42200000, 0x737b, 44, 1, 132]
OFFICIAL_PKT = bytes.fromhex(
    '07000000'                      # +0  subcmd 7 = EQ_SEG
    '0000' '0200'                   # +4  index(0) / iir_type=2 band_pass
    '00000000' '00000000'           # +8  freq / gain (runtime)
    '3333333f'                      # +16 q = 0.7f
    '0a' '64' '0000'                # +20 fade {10, 100, 0}
    'cdcccc3d'                      # +24 fade step 0.1f
    '00000000' '00000000' '00000000' '00000000')


def _assemble_with(pkt=0x060e3500, name=0x060e3540):
    text = SOURCE.read_text()
    for equ, value in (('EQ_PKT_ADDR', pkt), ('EQ_NAME_ADDR', name)):
        text = re.sub(r'\.equ ' + equ + r',\s*0x[0-9a-fA-F]+',
                      f'.equ {equ}, 0x{value:x}', text, count=1)
    obj = ASM.read_object(ASM.assemble_text(text, str(SOURCE)))
    sections = {s.name: bytes(s.data) for s in obj.sections if s.data}
    return obj, sections


class ApplyEqPayloadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.obj, cls.sections = _assemble_with()
        cls.code = cls.sections['.text.apply_eq']
        cls.pkt = cls.sections['.rodata.eq_pkt']
        cls.name = cls.sections['.rodata.eq_name']
        cls.insns = list(ASM.disassemble(cls.code))
        cls.texts = [t for _a, _i, t, _r in cls.insns]

    def test_01_roundtrip_and_size(self):
        """Every instruction re-encodes to its own bytes; shape in [40, 80]."""
        for addr, iid, text, raw in self.insns:
            self.assertIsNotNone(iid, f'undecodable at {addr:#x}')
            insn, values, _n = ASM.decode(self.code, addr)
            self.assertEqual(ASM.encode(iid, **values), raw,
                             f're-encode differs at {addr:#x} ({text})')
        self.assertTrue(40 <= len(self.insns) <= 80,
                        f'implausible instruction count {len(self.insns)}')

    def test_02_api_anchors_once_with_paired_call(self):
        """Both API anchors load exactly once, each immediately used by a
        register call (get_config early, set_node_param inside the loop)."""
        for equ, value in API_ANCHORS.items():
            loads = [t for t in self.texts if t == f'r6 = {value}'
                     or t == f'r9 = {value}']
            self.assertEqual(loads, [f'r6 = {value}' if equ == 'GET_CONFIG'
                                     else f'r9 = {value}'], f'{equ} load count')
        for i, (_a, _iid, text, _raw) in enumerate(self.insns):
            hit = next((v for v in API_ANCHORS.values()
                        if text in (f'r6 = {v}', f'r9 = {v}')), None)
            if hit is not None:
                nxt = self.insns[i + 1][1]
                self.assertIn(nxt, ('call.r', 'li.u32', 'li.zero_hi', 'li.s16'),
                              'API load not in its setup block')

    def test_03_constants_present(self):
        joined = ' | '.join(self.texts)
        for value in CONSTANTS:
            self.assertIn(str(value), joined, f'constant {value} missing')

    def test_04_whole_packet_gates_before_first_send(self):
        """get_config length gate + both header-word gates + the ten
        freq/gain gates all precede the first set_node_param call (a rejected
        packet sends nothing — codex: do not discover an invalid ninth
        segment after eight have already applied)."""
        send = next(i for i, (_a, iid, _t, _raw) in enumerate(self.insns)
                    if iid == 'call.r'
                    and self.texts[i].startswith('call r9'))
        gates = ['if (r0 != 88)', 'if (r5 != r6)', 'if (r5 != r6)',
                 'if (r0 == 0)', 'if (r0 > r8)', 'if (r2 > r7)']
        for gate in gates:
            idx = next((i for i, (_a, _iid, t, _r) in enumerate(self.insns)
                        if t.startswith(gate)), None)
            self.assertIsNotNone(idx, f'gate missing: {gate}')
            self.assertLess(idx, send, f'gate after the first send: {gate}')

    def test_05_patch_stores_and_send_shape(self):
        """Per-segment patches: index halfword @+4, freq word @+8, gain word
        @+12 of the stack packet; the send feeds (0x737b, name, packet, 44)
        and continues only on r0 == 1 (eq ioctl routine return, codex Q2)."""
        stores = [t for t in self.texts if t.startswith('h[r3+') or t.startswith('[r3+')]
        self.assertEqual(stores, ['h[r3+4] = r8', '[r3+8] = r1', '[r3+12] = r1'])
        send_idx = next(i for i, (_a, _iid, t, _r) in enumerate(self.insns)
                        if _iid == 'call.r' and t == 'call r9')
        prefix = ' | '.join(self.texts[:send_idx])
        self.assertIn('r0 = 29563', prefix)          # 0x737b as decimal
        self.assertIn('r1 = r5', prefix)
        self.assertIn('r2 = r3', prefix)
        self.assertIn('r3 = 44', prefix)
        self.assertTrue(any(t.startswith('if (r0 != 1)')
                            for t in self.texts[send_idx + 1:send_idx + 3]),
                        'missing the r0==1 continue gate right after the send')

    def test_06_pkt_template_pinned_and_official_cross(self):
        """44B EQ_SEG template == pinned constants; iir/q/fade bytes are
        cross-checked against the OFFICIAL Eq1Eff blob (stream.bin §5 @0xeac:
        ten segments, all iir=2 band_pass, q=0.7f) and the fade constants
        match both firmware producer loops (10/100/0.1f at packet+20..28)."""
        self.assertEqual(len(self.pkt), 44)
        self.assertEqual(self.pkt, OFFICIAL_PKT)
        if OFFICIAL_STREAM.exists():
            stream = OFFICIAL_STREAM.read_bytes()
            blob_off = 0x871 + 0xeac              # §5 base + record offset
            blob = stream[blob_off:blob_off + 168]
            seg_num = struct.unpack_from('>H', blob, 4)[0]
            self.assertEqual(seg_num, 10)
            for i in range(10):
                idx, iir = struct.unpack_from('<HH', blob, 8 + i * 16)
                q = struct.unpack_from('<f', blob, 8 + i * 16 + 12)[0]
                self.assertEqual((idx, iir), (i, 2),
                                 f'official seg[{i}] not sequential band_pass')
                self.assertEqual(struct.pack('<f', q), b'\x33\x33\x33\x3f',
                                 f'official seg[{i}] q not 0.7f')
            # template's baked iir/q bytes equal the official per-seg fields
            self.assertEqual(self.pkt[6:8], blob[8 + 2:8 + 4])
            self.assertEqual(self.pkt[16:20], blob[8 + 12:8 + 16])

    def test_07_name_field(self):
        """16B name field: "Eq1Eff\\0" + zero padding (§2 instance subid 10)."""
        self.assertEqual(self.name, b'Eq1Eff\x00' + b'\x00' * 9)

    def test_08_address_layout_insensitive(self):
        """Changing both EQ_*_ADDR .equ values moves bytes only inside the
        two li.u32 immediates (same two-pass contract as MPO_*_ADDR)."""
        _obj2, sections2 = _assemble_with(0x060e4000, 0x060e4100)
        code2 = sections2['.text.apply_eq']
        self.assertEqual(len(code2), len(self.code))
        diffs = [i for i, (a, b) in enumerate(zip(self.code, code2)) if a != b]
        self.assertTrue(diffs, 'expected address-dependent bytes')
        li_spans = []
        offset = 0
        for _a, iid, _t, raw in self.insns:
            if iid == 'li.u32' and self.code[offset:offset + 6] != \
                    code2[offset:offset + 6]:
                li_spans.append(range(offset + 2, offset + 6))
            offset += len(raw)
        for i in diffs:
            self.assertTrue(any(i in span for span in li_spans),
                            f'byte {i} changed outside the .equ immediates')

    def test_09_stack_frame_balanced(self):
        """132B frame push/pop pair; callee-saved r4-r9 prologue/epilogue."""
        self.assertEqual(self.insns[0][1], 'push.rets_rl')
        self.assertIn('sp += -132', self.texts)
        self.assertIn('sp += 132', self.texts)
        self.assertEqual(self.insns[-1][1], 'pop.pc_rl')

    def test_10_loop_bound_and_returns(self):
        """The segment loop is bounded at exactly ten iterations and the
        three return paths are 0 (all submitted) / 1 (skip) / 2 (stopped)."""
        self.assertTrue(any(t.startswith('if (r8 != 10)') for t in self.texts),
                        'missing the ten-iteration loop bound')
        for value in (0, 1, 2):
            self.assertIn(f'r0 = {value}', self.texts)


if __name__ == '__main__':
    unittest.main()
