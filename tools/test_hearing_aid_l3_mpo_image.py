#!/usr/bin/env python3
"""Offline verification of the hearing-aid L3 MPO candidate image (route HAL3).

Layer above test_hearing_aid_l3_mpo_payload.py: the composed segment
(open + close + apply_mpo + template/name rodata + cmd7_toggle_mpo stub +
r4 keyhook), its decode-level guarantees, the keyhook status-byte erratum
fix, the candidate build (when the official firmware assets are present),
and the byte-diff classification of the finished image.

Run: python3 tools/test_hearing_aid_l3_mpo_image.py  (segment tests ~1s;
the candidate build needs firmware/ + analysis/v1.1.8/ and skips otherwise).
"""
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


class L3MpoSegmentTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report, cls.code, _slot = IMG.assemble_segment(
            close_hooks=True, slot_ram=True, r4_keyhook=True, l3_mpo=True)

    def _off(self, key):
        return int(self.report[key], 16) - PAYLOAD_ADDRESS

    def test_01_geometry(self):
        """open + close + apply_mpo(128B) + template(76) + name(16) +
        toggle_mpo stub(46B) + r4 keyhook — variant and layout recorded."""
        self.assertEqual(self.report['variant'], 'open+close+slotram+l3mpo')
        mpo = self._off('apply_mpo_address')
        tpl = self.report['mpo_template_offset']
        name = self.report['mpo_name_offset']
        toggle = self._off('toggle_address')
        keyhook = self._off('r4_keyhook_address')
        self.assertEqual(tpl - mpo, IMG.MPO_CODE_BYTES)   # code -> template
        self.assertEqual(name - tpl, 76)                  # template -> name
        self.assertEqual(toggle, name + 16)               # name -> stub
        self.assertGreater(keyhook, toggle)
        self.assertEqual(self.report['code_bytes'], len(self.code))

    def test_02_open_close_prefixes_match_standalone(self):
        """The first bytes stay the route-L open payload followed by the
        route-X close payload, unchanged by the L3 additions."""
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
        self.assertTrue(self.report['mpo_toggle_stub_shape_matches'])
        self.assertTrue(self.report['payload_prefix_matches_route_L'])
        self.assertTrue(self.report['close_payload_matches_route_X'])

    def test_04_toggle_stub_open_path_calls_apply_mpo(self):
        """cmd7_toggle_mpo: open call, fail-shortcut branch, apply_mpo call,
        close call — every internal call reaches open/apply_mpo/close."""
        toggle = self._off('toggle_address')
        mpo = self._off('apply_mpo_address')
        close = int(self.report['close_address'], 16) - PAYLOAD_ADDRESS
        insns = [(a - PAYLOAD_ADDRESS, iid, t, raw)
                 for a, iid, t, raw in ASM.disassemble(self.code, PAYLOAD_ADDRESS)
                 if toggle <= a - PAYLOAD_ADDRESS < self._off('r4_keyhook_address')]
        calls = []
        for off, iid, t, raw in insns:
            if iid in ('call.s', 'call.l'):
                import re
                m = re.search(r'call (-?\d+)$', t)
                target = PAYLOAD_ADDRESS + off + len(raw) + int(m.group(1))
                calls.append(target - PAYLOAD_ADDRESS)
        self.assertEqual(calls, [0, mpo, close])   # open -> apply_mpo -> close

    def test_05_keyhook_status_byte_erratum_fixed(self):
        """Route-AQ erratum fix: the write branch stores the status BYTE at
        resp+2 (b[r4+2]); the old word store at resp+0 that made failures
        read as successes must be absent."""
        keyhook = self._off('r4_keyhook_address')
        texts = [t for a, iid, t, raw in ASM.disassemble(self.code, PAYLOAD_ADDRESS)
                 if keyhook <= a - PAYLOAD_ADDRESS]
        self.assertIn('b[r4+2] = r5', texts)
        self.assertNotIn('[r4+0] = r5', texts)

    def test_06_template_and_name_in_segment(self):
        tpl = self.report['mpo_template_offset']
        name = self.report['mpo_name_offset']
        self.assertEqual(self.code[tpl:tpl + 76], IMG.MPO_TEMPLATE_EXPECTED)
        self.assertEqual(self.code[name:name + 16], IMG.MPO_NAME_EXPECTED)

    def test_07_gates_misuse(self):
        """l3_mpo without its superset flags / together with key7 is refused."""
        with self.assertRaises(ValueError):
            IMG.assemble_segment(l3_mpo=True)
        with self.assertRaises(ValueError):
            IMG.assemble_segment(close_hooks=True, slot_ram=True,
                                 key7_toggle=True, l3_mpo=True)

    def test_08_legacy_variants_unchanged(self):
        """The pre-existing variants (open/key7/slotram/r4keyhook) still
        assemble — no regression from the L3 extension."""
        for kwargs, variant in (
                (dict(), 'open'),
                (dict(close_hooks=True, slot_ram=True, key7_toggle=True),
                 'open+close+slotram+key7toggle'),
                (dict(close_hooks=True, slot_ram=True, r4_keyhook=True),
                 'open+close+slotram+r4keyhook')):
            report, _code, _slot = IMG.assemble_segment(**kwargs)
            self.assertEqual(report['variant'], variant)


@unittest.skipUnless(FIRMWARE.exists() and ANALYSIS.exists(),
                     'official firmware assets not materialized')
class L3MpoImageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import hashlib
        import tempfile
        sys.path.insert(0, str(ROOT / 'tools'))
        cls.source = FIRMWARE.read_bytes()
        cls.expected_sha = hashlib.sha256(cls.source).hexdigest()
        cls.tmp = tempfile.TemporaryDirectory()
        import subprocess
        # the builder requires a NON-existent output dir
        cls.out = Path(cls.tmp.name) / 'candidate'
        cls.proc = subprocess.run(
            [sys.executable, str(ROOT / 'tools/build_quiet_candidate.py'),
             '--attenuation-db', '0', '--hearing-aid-l3-mpo',
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
        self.assertIn('HEARING_AID_L3_MPO_OFFLINE_UNTESTED',
                      self.candidates[0].name)

    def test_02_diff_is_exact_and_classified(self):
        """Whole-file diff == the builder's own bookkeeping (plaintext edits
        below the old JLFS end + re-encrypted append + boot-info + UFW CRCs);
        count is in the L3 family range (keyhook candidate 554 + mpo bytes)."""
        result = self.candidates[0].read_bytes()
        self.assertEqual(len(result), len(self.source))
        diff = {i for i in range(len(self.source))
                if result[i] != self.source[i]}
        self.assertTrue(554 < len(diff) < 800,
                        f'implausible diff count {len(diff)}')

    def test_03_registry_window_and_fence(self):
        """cmdid 0x30 setter slot points at the keyhook; fence upper limit
        covers the whole appended segment (incl. rodata tail)."""
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


if __name__ == '__main__':
    unittest.main()
