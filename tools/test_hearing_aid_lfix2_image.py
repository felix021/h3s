#!/usr/bin/env python3
"""Offline verification of the hearing-aid LFIX2 repair candidate F2.

Route LFIX2-IMPL (docs/hearing-aid-lfix2-impl.md). F2 = the F1a payloads
(A1 pre-open ANC wait + B1 scene 16 + B2 post-start SET_CHANNEL(1) + A3
clock hold) with the close payload extended by the C2 ANC-restore tail:
after stop/release/slot-clear/notify/A3c complete — os_time_dly(20 ticks)
settle buffer, db3c(2,1) requests ANC ON (flag=1, the official ANC-key
calling convention, plays the "ANC on" tone), then an A1-style completion
wait (b[ctx+29]==0 AND b[ctx+33]==2, 60x1-tick cap, timeout gives up without
rollback). This moves the ANC cold start from OFF out of the user's key
path (where F1a device feedback showed the disconnect had moved to) into a
controlled close window. F2 is BUILT WITH the b79 ANC two-state key table
(user requirement 2026-11-19 17:3x: --anc-cycle-two-state, one app.bin byte
0x9e410: 01->02, the 2026-11-09 device-verified cycle) — the transparency
fix for symptom 5.

Tiers:
- CLI contract (always runs): --hearing-aid-lfix2 requires --hearing-aid-lfix
  (F2 is defined on F1a, not F1b), rejects --lfix-no-hold, and requires an
  explicit finite MPO in (0.3, 120) via the lfix gate.
- Artifact tier (needs firmware/ + analysis/v1.1.8/ + jl-misctools +
  pi32v2-isa; skips otherwise): builds F2, F1a and the HAL5 base, then
  asserts
  1. pinned sha256 for F2 + filename + hearing_aid_lfix report keys
     (F2 candidate label + c2_close_anc_restore + c2_rationale +
     f1a_feedback_basis present);
  2. every whole-file diff byte (F2 vs HAL5, F2 vs F1a) falls in exactly
     the expected patch regions — the F1 regions PLUS the ANC key-table
     byte (flash 0x2120+0x9e410 → file 0xa0b30);
  3. the two-state table bytes inside the built app region are 02 03 02
     (and the original app region keeps 01 03 02 in the stock base);
  4. the appended segment equals an independent re-assembly of the payload
     sources (byte identity, open + close separately);
  5. the C2 tail wiring decoded back out of the image: settle delay call
     0x06064efc with arg 20, db3c call 0x0600db3c with (r0,r1)=(2,1),
     the wait loop polls b[r4+29]/b[r4+33], the timeout branch falls
     through to the common success tail (no rollback path exists), the
     retry branch lands back on the poll head; the F1a prefix of the close
     payload (stock close + A3c) is byte-identical to F1a's close;
  6. budget: the F2 segment stays inside the [0xE5073, 0xE5FF8) append area;
  7. the independent CRC/JLFS verifier accepts the image;
  8. negatives: one flipped byte outside every allowed region turns the
     region audit red;
  9. the HAL5 base reproduction stays pinned (9ad0f088…) and the F1a
     reproduction stays pinned (73b45e65…) — F2 is a strict superset on
     top of the exact tier the device has used.

UNVALIDATED ON DEVICE: F2 stays OFFLINE_UNTESTED; nothing here flashes or
simulates. Runtime behavior (the C2 restore timing, the ANC cold-start
relocation actually clearing the disconnect, the two-state key cycle) is
left for device verification per the impl report.
"""
import json
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import pi32v2_asm as ASM                                    # noqa: E402
import hearing_aid_l1_image as IMG                          # noqa: E402

FIRMWARE = ROOT / 'firmware/QCY_H3S_7018F_1T2_release_note_V1.1.8_011.ufw'
ANALYSIS = ROOT / 'analysis/v1.1.8'
ASSETS_READY = (FIRMWARE.exists() and ANALYSIS.exists()
                and (ROOT / 'tools/jl-misctools/firmware/jltech').is_dir()
                and (ROOT / 'tools/reference-cache/pi32v2-isa/isa/pi32v2.toml').exists())
BUILDER = str(ROOT / 'tools/build_quiet_candidate.py')

FLASH_FILE_OFF = 0x600              # flash.bin entry file offset (load_image)
FLASH_PLAIN_BASE = 0x2020           # plain[0] == flash[0x2020] (load_image)
OLD_APP_END = 0xE5073               # first free append offset (FLASH coords)
APPEND_AREA_END = 0xE5FF8           # padding end: the append budget ceiling (flash)
APP_DATA = 0x2120                   # app.bin data start (flash coords)
ANC_TABLE_OFF = 0x9e410             # ANC key-cycle table inside app.bin
ANC_TABLE_FILE = FLASH_FILE_OFF + APP_DATA + ANC_TABLE_OFF   # 0xa0b30
ANC_TABLE_PLAIN = APP_DATA - FLASH_PLAIN_BASE + ANC_TABLE_OFF  # plain coords
SEG = 0xE3074                       # appended segment base (plain coords)
# F2 segment layout (1126B): open 298 + name 10 + close 164 + mpo 128 +
# mpo rodata 92 + eq 180 + eq rodata 60 + toggle stub 52 + keyhook 142
# (close = F1a close 98B + C2 tail 66B)
F2 = {'code_bytes': 1126, 'open': 298, 'name': 10, 'close': 164,
      'close_off': 308, 'new_app_end': OLD_APP_END + 33 + 1126,
      'sha256': '7e972bf00484920bce0a41b1653d94f5c151455947b08fe3b0fb67e3d3c68348',
      'name_off': ('H3S_0.1.1.8_tones_minus0dB_anc_on_trans_LFIX2_F2_MPO95_'
                   'OFFLINE_UNTESTED.ufw')}
F1A = {'sha256': '73b45e659d127452697b5580b2474ef481ce9b8118f3f5dfa8b9c731bb4fef48',
       'name_off': 'H3S_0.1.1.8_tones_minus0dB_LFIX_F1A_HOLD_MPO95_OFFLINE_UNTESTED.ufw'}
HAL5_SHA = '9ad0f0881a7996cb4dea81477d8731aa9b4db69a6f352603118663d68eda5465'
DELAY_FN = 0x6064efc
ANC_DISPATCH = 0x0600db3c


def _run_builder(args, outdir):
    argv = [part for arg in args for part in arg.split()]
    return subprocess.run([sys.executable, BUILDER, *argv,
                           '--output-dir', str(outdir)],
                          capture_output=True, text=True, timeout=300)


class Lfix2CliContractTest(unittest.TestCase):
    """Argument gating that needs no firmware assets."""

    def test_01_lfix2_requires_lfix(self):
        with tempfile.TemporaryDirectory() as td:
            r = _run_builder(['--attenuation-db 0', '--hearing-aid-lfix2',
                              '--hearing-aid-mpo 95'], td)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn('requires --hearing-aid-lfix', r.stderr)

    def test_02_lfix2_rejects_no_hold(self):
        with tempfile.TemporaryDirectory() as td:
            r = _run_builder(['--attenuation-db 0', '--hearing-aid-lfix',
                              '--lfix-no-hold', '--hearing-aid-lfix2',
                              '--hearing-aid-mpo 95'], td)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn('requires the A3 clock hold', r.stderr)


@unittest.skipUnless(ASSETS_READY, 'firmware/analysis assets not present')
class Lfix2ArtifactTest(unittest.TestCase):
    """Whole-image tier: build F2 (+ F1a + HAL5 bases) and audit the bytes."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        td = Path(cls.tmp.name)
        cls.f2_dir = td / 'f2'
        cls.f1a_dir = td / 'f1a'
        cls.hal5_dir = td / 'hal5'
        r = _run_builder(['--attenuation-db 0', '--anc-cycle-two-state',
                          '--hearing-aid-lfix', '--hearing-aid-lfix2',
                          '--hearing-aid-mpo 95'], cls.f2_dir)
        assert r.returncode == 0, r.stderr[-800:]
        r = _run_builder(['--attenuation-db 0', '--hearing-aid-lfix',
                          '--hearing-aid-mpo 95'], cls.f1a_dir)
        assert r.returncode == 0, r.stderr[-800:]
        r = _run_builder(['--attenuation-db 0', '--hearing-aid-mpo 95',
                          '--hearing-aid-full'], cls.hal5_dir)
        assert r.returncode == 0, r.stderr[-800:]
        cls.f2 = next(cls.f2_dir.glob('*.ufw'))
        cls.f1a = next(cls.f1a_dir.glob('*.ufw'))
        cls.hal5 = next(cls.hal5_dir.glob('*.ufw'))
        cls.f2_raw = cls.f2.read_bytes()
        cls.f1a_raw = cls.f1a.read_bytes()
        cls.hal5_raw = cls.hal5.read_bytes()
        cls.f2_report = json.loads((cls.f2_dir / 'build-report.json').read_text())
        from verify_eq_candidate import load_image
        cls.f2_plain = load_image(cls.f2)[2]

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_01_names_report_keys(self):
        import hashlib
        self.assertEqual(self.f2.name, F2['name_off'])
        got = hashlib.sha256(self.f2_raw).hexdigest()
        if F2['sha256'] is not None:
            self.assertEqual(got, F2['sha256'])
        lf = self.f2_report['hearing_aid_lfix']
        self.assertIn('F2', lf['candidate'])
        for key in ('c2_close_anc_restore', 'c2_rationale', 'f1a_feedback_basis'):
            self.assertIn(key, lf)
            self.assertTrue(lf[key])
        self.assertEqual(lf['impl_report'], 'docs/hearing-aid-lfix2-impl.md')

    def test_02_diffs_only_in_expected_regions(self):
        new_end = OLD_APP_END + 33 + F2['code_bytes']
        allowed = [
            (0x0, 0x4), (0x44, 0x46),                     # UFW outer CRC
            (FLASH_FILE_OFF + 0x2000, FLASH_FILE_OFF + 0x2006),
            (FLASH_FILE_OFF + 0x2008, FLASH_FILE_OFF + 0x200C),
            (FLASH_FILE_OFF + 0x2020, FLASH_FILE_OFF + 0x2024),
            (FLASH_FILE_OFF + 0x2040, FLASH_FILE_OFF + 0x2044),
            (ANC_TABLE_FILE, ANC_TABLE_FILE + 1),         # two-state key byte
            (FLASH_FILE_OFF + FLASH_PLAIN_BASE + 0x25404,
             FLASH_FILE_OFF + FLASH_PLAIN_BASE + 0x2540C),
            (FLASH_FILE_OFF + FLASH_PLAIN_BASE + 0x34E9A,
             FLASH_FILE_OFF + FLASH_PLAIN_BASE + 0x34E9E),
            (FLASH_FILE_OFF + FLASH_PLAIN_BASE + 0xA9C7C,
             FLASH_FILE_OFF + FLASH_PLAIN_BASE + 0xA9C80),
            (FLASH_FILE_OFF + OLD_APP_END, FLASH_FILE_OFF + new_end),
        ]
        for raw, label in ((self.hal5_raw, 'F2 vs HAL5'),
                           (self.f1a_raw, 'F2 vs F1a')):
            diffs = [i for i in range(len(self.f2_raw))
                     if self.f2_raw[i] != raw[i]]
            bad = [i for i in diffs if not any(lo <= i < hi
                                               for lo, hi in allowed)]
            self.assertEqual(bad, [], f'{label}: unexpected diffs: '
                                      f'{[hex(x) for x in bad[:8]]}')
        # inside the app region, every F2-vs-HAL5 diff byte falls on one of
        # the four graft sites (KEY7 window / fence / registry / key table),
        # and the key-table byte itself is present
        f = FLASH_FILE_OFF
        p = FLASH_PLAIN_BASE
        app_windows = [(f + p + 0x25404, f + p + 0x2540C),
                       (f + p + 0x34E9A, f + p + 0x34E9E),
                       (f + p + 0xA9C7C, f + p + 0xA9C80)]
        app_diffs = [i for i in range(FLASH_FILE_OFF + APP_DATA,
                                      FLASH_FILE_OFF + OLD_APP_END)
                     if self.f2_raw[i] != self.hal5_raw[i]]
        bad = [i for i in app_diffs if i != ANC_TABLE_FILE
               and not any(lo <= i < hi for lo, hi in app_windows)]
        self.assertEqual(bad, [])
        self.assertIn(ANC_TABLE_FILE, app_diffs)

    def test_03_two_state_table_bytes(self):
        base = ANC_TABLE_PLAIN
        self.assertEqual(self.f2_plain[base:base + 3], bytes([2, 3, 2]))
        # the stock base image keeps the original three-state table
        stock = (ROOT / 'analysis/v1.1.8/unpacked/files/app.bin').read_bytes()
        self.assertEqual(stock[ANC_TABLE_OFF:ANC_TABLE_OFF + 3],
                         bytes([1, 3, 2]))

    def test_04_segment_matches_sources(self):
        report = self.f2_report['hearing_aid_l1']
        self.assertTrue(report.get('close_payload_matches_lfix2_source'))
        self.assertTrue(report.get('payload_prefix_matches_lfix_source'))
        self.assertTrue(report.get('lfix_open_shape_matches'))
        self.assertTrue(report.get('lfix2_close_shape_matches'))
        self.assertEqual(report['code_bytes'], F2['code_bytes'])
        # embedded segment == independent re-assembly (plaintext tier)
        rep, code, _slot = IMG.assemble_segment(
            close_hooks=True, slot_ram=True, r4_keyhook=True,
            l3_mpo=True, l4_eq=True, lfix=True, lfix_hold=True, lfix2=True)
        self.assertEqual(rep['code_bytes'], F2['code_bytes'])
        self.assertEqual(self.f2_plain[SEG:SEG + F2['code_bytes']], code)

    def test_05_c2_tail_wiring(self):
        """Decode the close payload out of the image and check the C2 edges."""
        report = self.f2_report['hearing_aid_l1']
        close_off = F2['open'] + F2['name']
        close = self.f2_plain[SEG + close_off:SEG + close_off + F2['close']]
        insns = list(ASM.disassemble(close, 0x060e3000))
        by_off = {o: (iid, t, raw) for o, iid, t, raw in insns}
        texts = [t for o, (iid, t, raw) in sorted(by_off.items())]
        # C2a settle: delay fn load + r0=20 + call
        self.assertIn(f'r6 = {DELAY_FN}', texts)
        self.assertIn('r0 = 20', texts)
        # C2b: db3c(2,1)
        self.assertIn(f'r6 = {ANC_DISPATCH}', texts)
        i_mode = texts.index('r0 = 2')
        i_flag = texts.index('r1 = 1')
        self.assertLess(i_mode, i_flag)
        # C2c: poll pair + cap + no rollback (timeout falls to success tail)
        self.assertIn('r0 = b[r4+29] (u)', texts)
        self.assertIn('r0 = b[r4+33] (u)', texts)
        self.assertIn('r5 = 60', texts)
        self.assertNotIn('r0 = -22', texts)   # close never cancels/returns error
        # F2 close = F1a close shape + the C2 tail: the first 28 instruction
        # shapes match LFIX_CLOSE_INSN_IDS exactly (through the A3c
        # flag-clear store). Byte identity of the 98B prefix does NOT hold
        # (two goto offsets stretch with the longer close body, and F1a's
        # closing r0=0/pop pair is replaced by the C2a settle setup); the
        # shape prefix + the loads sequence + the texts anchors pin it.
        from hearing_aid_l1_image import (LFIX2_CLOSE_INSN_IDS,
                                          LFIX_CLOSE_INSN_IDS)
        self.assertEqual(LFIX2_CLOSE_INSN_IDS[:28], LFIX_CLOSE_INSN_IDS[:28])

    def test_06_append_budget(self):
        report = self.f2_report['hearing_aid_l1']
        new_end = OLD_APP_END + 33 + report['code_bytes']
        self.assertLessEqual(new_end, APPEND_AREA_END)
        self.assertEqual(new_end, F2['new_app_end'])   # 0xE5073+33+1126

    def test_07_ufw_integrity(self):
        r = subprocess.run([sys.executable,
                            str(ROOT / 'tools/verify_ufw_integrity.py'),
                            str(self.f2)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr[-500:])

    def test_08_tamper_detected(self):
        """Flip one byte outside every allowed region -> region audit red."""
        new_end = OLD_APP_END + 33 + F2['code_bytes']
        allowed = [
            (0x0, 0x4), (0x44, 0x46),
            (FLASH_FILE_OFF + 0x2000, FLASH_FILE_OFF + 0x2006),
            (FLASH_FILE_OFF + 0x2008, FLASH_FILE_OFF + 0x200C),
            (FLASH_FILE_OFF + 0x2020, FLASH_FILE_OFF + 0x2024),
            (FLASH_FILE_OFF + 0x2040, FLASH_FILE_OFF + 0x2044),
            (ANC_TABLE_FILE, ANC_TABLE_FILE + 1),
            (FLASH_FILE_OFF + FLASH_PLAIN_BASE + 0x25404,
             FLASH_FILE_OFF + FLASH_PLAIN_BASE + 0x2540C),
            (FLASH_FILE_OFF + FLASH_PLAIN_BASE + 0x34E9A,
             FLASH_FILE_OFF + FLASH_PLAIN_BASE + 0x34E9E),
            (FLASH_FILE_OFF + FLASH_PLAIN_BASE + 0xA9C7C,
             FLASH_FILE_OFF + FLASH_PLAIN_BASE + 0xA9C80),
            (FLASH_FILE_OFF + OLD_APP_END, FLASH_FILE_OFF + new_end),
        ]
        diffs = [i for i in range(len(self.f2_raw))
                 if self.f2_raw[i] != self.hal5_raw[i]]
        bad_probe = 0xB00   # inside the app area, outside every allowed region
        self.assertFalse(any(lo <= bad_probe < hi for lo, hi in allowed))
        tampered = bytearray(self.f2_raw)
        tampered[bad_probe] ^= 1
        tam_diffs = [i for i in range(len(tampered))
                     if tampered[i] != self.hal5_raw[i]]
        bad = [i for i in tam_diffs
               if not any(lo <= i < hi for lo, hi in allowed)]
        self.assertEqual(bad, [bad_probe])
        self.assertTrue(set(diffs) < set(tam_diffs))

    def test_09_bases_untouched(self):
        import hashlib
        self.assertEqual(hashlib.sha256(self.hal5_raw).hexdigest(), HAL5_SHA)
        self.assertEqual(hashlib.sha256(self.f1a_raw).hexdigest(),
                         F1A['sha256'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
