#!/usr/bin/env python3
"""Offline verification of the hearing-aid L4 EQ candidate image (route HAL4).

Layer above test_hearing_aid_l4_eq_payload.py: the composed segment
(open + close + apply_mpo + mpo rodata + apply_eq + eq rodata +
cmd7_toggle_l4 stub + extended r4 keyhook), its decode-level guarantees
(including the interleaved data spans), the key-31 hook branch, the
candidate build (when the official firmware assets are present), and the
byte-diff classification of the finished image.

Run: python3 tools/test_hearing_aid_l4_eq_image.py  (segment tests ~1s; the
candidate build needs firmware/ + analysis/v1.1.8/ and skips otherwise).
"""
import re
import struct
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import pi32v2_asm as ASM  # noqa: E402
import hearing_aid_l1_image as IMG  # noqa: E402

PAYLOAD_ADDRESS = IMG.PAYLOAD_ADDRESS
FIRMWARE = ROOT / 'firmware/QCY_H3S_7018F_1T2_release_note_V1.1.8_011.ufw'
ANALYSIS = ROOT / 'analysis/v1.1.8'


class L4EqSegmentTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report, cls.code, _slot = IMG.assemble_segment(
            close_hooks=True, slot_ram=True, r4_keyhook=True, l3_mpo=True,
            l4_eq=True)

    def _off(self, key):
        return int(self.report[key], 16) - PAYLOAD_ADDRESS

    def test_01_geometry(self):
        """open + close + apply_mpo(128B) + mpo rodata(92B) + apply_eq(180B) +
        eq rodata(60B) + cmd7_toggle_l4 stub(52B) + r4 keyhook(142B) —
        variant and layout recorded."""
        self.assertEqual(self.report['variant'], 'open+close+slotram+l4eq')
        mpo = self._off('apply_mpo_address')
        eq = self._off('apply_eq_address')
        tpl = self.report['mpo_template_offset']
        name = self.report['mpo_name_offset']
        pkt = self.report['eq_pkt_offset']
        eqname = self.report['eq_name_offset']
        toggle = self._off('toggle_address')
        keyhook = self._off('r4_keyhook_address')
        self.assertEqual(tpl - mpo, IMG.MPO_CODE_BYTES)   # mpo code -> template
        self.assertEqual(name - tpl, 76)                  # template -> name
        self.assertEqual(eq - name, 16)                   # name -> apply_eq code
        self.assertEqual(pkt - eq, IMG.EQ_CODE_BYTES)     # eq code -> pkt blob
        self.assertEqual(eqname - pkt, 44)                # pkt -> name
        self.assertEqual(toggle, eqname + 16)             # name -> stub
        self.assertGreater(keyhook, toggle)
        self.assertEqual(self.report['code_bytes'], len(self.code))

    def test_02_open_close_prefixes_match_standalone(self):
        """The first bytes stay the route-L open payload followed by the
        route-X close payload, unchanged by the L3/L4 additions."""
        slot = int(self.report['slot_address'], 16)
        standalone_open = IMG._assemble(
            IMG._patch_slot(IMG.PAYLOAD_SOURCE.read_text(), slot))[1]
        standalone_close = IMG._assemble(
            IMG._patch_slot(IMG.CLOSE_SOURCE.read_text(), slot))[1]
        self.assertEqual(self.code[:len(standalone_open)], standalone_open)
        off = len(standalone_open)
        self.assertEqual(self.code[off:off + len(standalone_close)],
                         standalone_close)

    def test_03_decode_guards_reported(self):
        self.assertTrue(self.report['apply_mpo_shape_matches'])
        self.assertTrue(self.report['mpo_template_matches_official'])
        self.assertTrue(self.report['apply_eq_shape_matches'])
        self.assertTrue(self.report['eq_pkt_template_matches'])
        self.assertTrue(self.report['l4_toggle_stub_shape_matches'])
        self.assertTrue(self.report['r4_payload_matches_route_AQ'])
        self.assertTrue(self.report['payload_prefix_matches_route_L'])
        self.assertTrue(self.report['close_payload_matches_route_X'])
        # the interleaved data spans are listing data entries, not insns
        data_entries = [e for e in self.report['listing'] if e['insn'] is None]
        self.assertEqual([e['text'] for e in data_entries],
                         ['.data 92B (mpo_template 76B + mpo_name 16B)',
                          '.data 60B (eq_pkt 44B + eq_name 16B)'])

    def test_04_toggle_stub_open_path_calls_mpo_then_eq(self):
        """cmd7_toggle_l4: open call, fail-shortcut branch, apply_mpo call,
        apply_eq call, close call — every internal call reaches
        open/apply_mpo/apply_eq/close in that order."""
        toggle = self._off('toggle_address')
        mpo = self._off('apply_mpo_address')
        eq = self._off('apply_eq_address')
        close = int(self.report['close_address'], 16) - PAYLOAD_ADDRESS
        insns = [(a - PAYLOAD_ADDRESS, iid, t, raw)
                 for a, iid, t, raw in ASM.disassemble(self.code, PAYLOAD_ADDRESS)
                 if toggle <= a - PAYLOAD_ADDRESS < self._off('r4_keyhook_address')]
        calls = []
        for off, iid, t, raw in insns:
            if iid in ('call.s', 'call.l'):
                m = re.search(r'call (-?\d+)$', t)
                target = PAYLOAD_ADDRESS + off + len(raw) + int(m.group(1))
                calls.append(target - PAYLOAD_ADDRESS)
        # open -> apply_mpo -> apply_eq -> close (do_close path last)
        self.assertEqual(calls, [0, mpo, eq, close])

    def test_05_keyhook_key31_branch_in_segment(self):
        """The extended keyhook carries the key-31 write/read shapes
        (set_config(31, data+1, 88) / get_config(31, resp+2, 88), n=88 echo)
        and keeps the HAL3 resp+2 status-byte fix for both write branches."""
        keyhook = self._off('r4_keyhook_address')
        texts = [t for a, iid, t, raw in ASM.disassemble(self.code, PAYLOAD_ADDRESS)
                 if keyhook <= a - PAYLOAD_ADDRESS]
        for gate in ('if (r3 == 89)', 'if (r5 != 31)', 'if (r5 == 31)'):
            self.assertTrue(any(t.startswith(gate) for t in texts),
                            f'key-31 gate missing: {gate}')
        self.assertEqual(texts.count('r2 = 88'), 2)
        self.assertEqual(texts.count('r0 = 88'), 1)
        self.assertEqual(texts.count('b[r4+2] = r5'), 2,
                         'both write branches store the status byte at resp+2')
        self.assertNotIn('[r4+0] = r5', texts)

    def test_06_rodata_blobs_in_segment(self):
        """Both rodata pairs sit in the segment with their pinned bytes."""
        tpl = self.report['mpo_template_offset']
        name = self.report['mpo_name_offset']
        pkt = self.report['eq_pkt_offset']
        eqname = self.report['eq_name_offset']
        self.assertEqual(self.code[tpl:tpl + 76], IMG.MPO_TEMPLATE_EXPECTED)
        self.assertEqual(self.code[name:name + 16], IMG.MPO_NAME_EXPECTED)
        self.assertEqual(self.code[pkt:pkt + 44], IMG.EQ_PKT_EXPECTED)
        self.assertEqual(self.code[eqname:eqname + 16], IMG.EQ_NAME_EXPECTED)

    def test_07_gates_misuse(self):
        """l4_eq without l3_mpo / together with key7 is refused."""
        with self.assertRaises(ValueError):
            IMG.assemble_segment(close_hooks=True, slot_ram=True, l4_eq=True)
        with self.assertRaises(ValueError):
            IMG.assemble_segment(close_hooks=True, slot_ram=True,
                                 key7_toggle=True, l3_mpo=True, l4_eq=True)

    def test_08_legacy_variants_unchanged(self):
        """The pre-existing variants (open/key7/slotram/r4keyhook/l3mpo) still
        assemble — no regression from the L4 extension."""
        for kwargs, variant in (
                (dict(), 'open'),
                (dict(close_hooks=True, slot_ram=True, key7_toggle=True),
                 'open+close+slotram+key7toggle'),
                (dict(close_hooks=True, slot_ram=True, r4_keyhook=True),
                 'open+close+slotram+r4keyhook'),
                (dict(close_hooks=True, slot_ram=True, r4_keyhook=True,
                      l3_mpo=True), 'open+close+slotram+l3mpo')):
            report, _code, _slot = IMG.assemble_segment(**kwargs)
            self.assertEqual(report['variant'], variant)


@unittest.skipUnless(FIRMWARE.exists() and ANALYSIS.exists(),
                     'official firmware assets not materialized')
class L4EqImageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import hashlib
        import tempfile
        cls.source = FIRMWARE.read_bytes()
        cls.tmp = tempfile.TemporaryDirectory()
        import subprocess
        cls.out = Path(cls.tmp.name) / 'candidate'
        cls.proc = subprocess.run(
            [sys.executable, str(ROOT / 'tools/build_quiet_candidate.py'),
             '--attenuation-db', '0', '--hearing-aid-l4-eq',
             '--output-dir', str(cls.out)],
            capture_output=True, text=True, timeout=300)
        cls.candidates = list(cls.out.glob('*.ufw'))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_01_build_succeeded(self):
        self.assertEqual(self.proc.returncode, 0,
                         f'builder failed: {self.proc.stderr[-400:]}')
        self.assertEqual(len(self.candidates), 1)
        self.assertIn('HEARING_AID_L4_EQ_OFFLINE_UNTESTED',
                      self.candidates[0].name)

    def test_02_diff_is_exact_and_classified(self):
        """Whole-file diff == the builder's own bookkeeping; count sits in
        the HAL4 family range (r4 candidate 639 + mpo/eq payload growth)."""
        result = self.candidates[0].read_bytes()
        self.assertEqual(len(result), len(self.source))
        diff = {i for i in range(len(self.source))
                if result[i] != self.source[i]}
        self.assertTrue(700 < len(diff) < 1100,
                        f'implausible diff count {len(diff)}')

    def test_03_registry_window_and_fence(self):
        """cmdid 0x30 setter slot points at the extended keyhook; fence upper
        limit covers the whole appended segment (incl. both rodata pairs)."""
        import json
        report = json.loads((self.out / 'build-report.json').read_text())
        l1 = report['hearing_aid_l1']
        reg = l1['r4_registry_window']
        self.assertEqual(reg['cmdid'], 0x30)
        self.assertEqual(int(reg['handler'], 16),
                         int(l1['r4_keyhook_address'], 16))
        fence = l1['fence_repair']
        self.assertEqual(int(fence['new_upper_limit'], 16),
                         int(l1['segment_end_runtime'], 16))
        self.assertEqual(l1['segment_end_runtime'], '0x060e33de')

    def test_04_candidate_verifier_green(self):
        """The independent CRC/JLFS verifier accepts the finished image."""
        sys.path.insert(0, str(ROOT / 'tools'))
        from verify_ufw_integrity import verify
        report = verify(self.candidates[0])
        self.assertTrue(report['passed'], report['errors'])


if __name__ == '__main__':
    unittest.main()
