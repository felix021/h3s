#!/usr/bin/env python3
"""Tests for the candidate-world four-fold assertion tool (route BU).

docs/candidate-image-cotest.md §1.2 requires the l1_slotram / r4_keyhook
loaders to gate on four assertions (UFW sha / independent CRC / shared
bootloader / byte-account classification). tools/candidate_world_checks.py
implements them independently of the builder module; this suite pins:

Logic tier (no firmware input, always runs):
1. the pinned spec constants match the merged docs — candidate shas, whole-
   file byte accounts (493B/574B), plain lengths (flash end - 0x2020, NOT
   the loose flash-end convention the docs quote), fence top == plain end
   runtime address, six window spans at the documented runtime addresses,
   registry slot only for r4, whitelist spans disjoint;
2. byte_diff_runs semantics (merge + trailing length-extension run);
3. classifier discrimination: the measured run shape classifies green with
   nothing missing; a dropped window / partial window / extra run at the
   ANC key table / registry run on l1 all go red (equality, not subset).

Artifact tier (skipped when the official firmware, jl-misctools or the
pi32v2-isa cache is absent from this checkout): builds BOTH candidates
fresh with build_quiet_candidate.py (as check_reproduction does) and runs
the full gate —
4. both candidates: all four assertions green, r4 carries the registry run,
   l1 does not, build-report reconciliation green;
5. NEGATIVES (the spec's anti-vacuity demand): one tampered bootloader byte
   must turn the gate red (sha + CRC + shared boot + count all catch it);
   a forged build-report byte account must turn exactly the byte account
   red; one flipped non-whitelisted plain byte (ANC key table family, zero
   expected difference for candidate pairs) must leave shared boot GREEN
   while the byte account reports the unexplained run — proving the two
   assertions are independent and the classifier has discriminating power;
6. CLI exit codes: 0 green, 1 on the tampered candidate.

UNVALIDATED ON DEVICE: the candidates stay OFFLINE_UNTESTED; nothing here
flashes or simulates anything.
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import candidate_world_checks as CWC                            # noqa: E402

OFFICIAL = ROOT / 'firmware/QCY_H3S_7018F_1T2_release_note_V1.1.8_011.ufw'
ARTIFACTS_READY = (OFFICIAL.exists()
                   and (ROOT / 'tools/jl-misctools/firmware/jltech').is_dir()
                   and (ROOT / 'tools/reference-cache/pi32v2-isa/isa/pi32v2.toml').exists())

# The measured plain-run shape of the l1 candidate (route BU re-measurement
# of the builder artifacts; region labels as the classifier emits them).
L1_RUN_SHAPE = [(0x0, 0x4), (0x20, 0x24), (0xDB9A, 0xDBA2), (0xDBC4, 0xDBCC),
                (0xDC74, 0xDC7C), (0xDC7E, 0xDC86), (0xDC9A, 0xDCA2),
                (0xDCA4, 0xDCAC), (0x34E9A, 0x34E9D), (0xE2ED3, 0xE2ED5),
                (0xE2EE1, 0xE2EE2), (0xE3053, 0xE31F8)]
R4_EXTRA_RUNS = [(0xA9C7C, 0xA9C80)]          # registry setter slot, r4 only


class SpecPinTest(unittest.TestCase):
    """The pinned constants equal the merged-doc values (docs as spec)."""

    def test_01_candidate_pins(self):
        self.assertEqual(CWC.CANDIDATES['l1_slotram']['sha256'],
                         '6f267a2ed0b62ed118047e889a64483907c727d36eb5172a5d'
                         '0f0067d37b1b93')
        # HAL3 re-pin: keyhook status-byte erratum fix (st.w -> st.b),
        # docs/hearing-aid-l3-mpo.md §5 — old bfc3f681… superseded
        self.assertEqual(CWC.CANDIDATES['r4_keyhook']['sha256'],
                         '1873027bcf82026aa1829aeeebf348c9227a8f9b3895009dbc8'
                         '1205f0b30ac65')
        self.assertEqual(CWC.CANDIDATES['l1_slotram']['whole_file_diff_bytes'], 493)
        self.assertEqual(CWC.CANDIDATES['r4_keyhook']['whole_file_diff_bytes'], 574)
        self.assertEqual(CWC.OFFICIAL_SHA,
                         '65cafc7f9a7545c5c30701d141c77db334e37065589e42c513'
                         '06132c94c879af')

    def test_02_plain_lengths_are_flash_end_minus_base(self):
        """Plain length = boot-info app_size = flash end - 0x2020 (the docs'
        0xE5200/0xE524E are flash-end values, not plain lengths); fence top
        equals the plain end's runtime address."""
        for name, flash_end, fence_top in (('l1_slotram', 0xE5218, 0x060E31F8),
                                           ('r4_keyhook', 0xE5266, 0x060E3246)):
            plain = CWC.CANDIDATES[name]['plain_length']
            self.assertEqual(plain, flash_end - 0x2020)
            self.assertEqual(0x06000000 + plain, fence_top)

    def test_03_whitelist_geometry(self):
        for name, spec in CWC.CANDIDATES.items():
            spans = CWC._regions(spec)
            bounds = [(lo, hi) for _n, lo, hi in spans]
            self.assertEqual(bounds, sorted(bounds), 'spans must be sorted')
            for (lo, hi), (next_lo, _next_hi) in zip(bounds, bounds[1:]):
                self.assertLessEqual(hi, next_lo, 'spans must be disjoint')
            names = [n for n, _lo, _hi in spans]
            self.assertEqual(len(names), len(set(names)), 'region names unique')
            self.assertEqual('registry_setter_slot' in names, spec['registry_slot'])
        windows = {name: (lo, hi) for name, lo, hi in CWC.HOOK_WINDOWS}
        self.assertEqual(set(windows),
                         {'hook_window:' + t for t in
                          ('ON-1', 'ON-2', 'OFF-1', 'TRANS-A', 'OFF-2', 'TRANS-B')})
        for name, (lo, hi) in windows.items():
            self.assertEqual(hi - lo, 8, f'{name} must be the 8B window span')
        self.assertEqual(CWC.FENCE_IMMEDIATE[1:], (0x34E9A, 0x34E9E))
        self.assertEqual(CWC.REGISTRY_SLOT[1:], (0xA9C7C, 0xA9C80))
        self.assertEqual([s[1:] for s in CWC.CONFIG_DAT_SPANS],
                         [(0xE2ED3, 0xE2ED5), (0xE2EE1, 0xE2EE2)])


class ByteDiffRunsTest(unittest.TestCase):

    def test_01_merge_and_extension(self):
        self.assertEqual(CWC.byte_diff_runs(b'abc', b'abc'), [])
        self.assertEqual(CWC.byte_diff_runs(b'abc', b'axc'), [(1, 2)])
        self.assertEqual(CWC.byte_diff_runs(b'abcd', b'axyd'), [(1, 3)])
        self.assertEqual(CWC.byte_diff_runs(b'ab', b'abZZ'), [(2, 4)])
        self.assertEqual(CWC.byte_diff_runs(b'abZZ', b'ab'), [(2, 4)])


class ClassifierTest(unittest.TestCase):
    """Classification equality: every whitelist region hit, nothing outside."""

    def test_01_measured_shape_green(self):
        base = [r for r in L1_RUN_SHAPE if r[0] != CWC.APPEND_BASE]
        for name, extra in (('l1_slotram', []),
                            ('r4_keyhook', R4_EXTRA_RUNS)):
            spec = CWC.CANDIDATES[name]
            runs = sorted(base + extra + [(CWC.APPEND_BASE, spec['plain_length'])])
            classified, unexplained, missing = CWC.classify_runs(runs, spec)
            self.assertEqual(unexplained, [])
            self.assertEqual(missing, [])
            labels = {c['region'] for c in classified}
            if spec['registry_slot']:
                self.assertIn('registry_setter_slot', labels)
            else:
                self.assertNotIn('registry_setter_slot', labels)

    def test_02_dropped_window_is_missing(self):
        runs = [r for r in L1_RUN_SHAPE if r != (0xDB9A, 0xDBA2)]
        _c, _u, missing = CWC.classify_runs(runs, CWC.CANDIDATES['l1_slotram'])
        self.assertIn('hook_window:ON-1', missing)

    def test_03_partial_window_coverage_is_missing(self):
        runs = [(0xDB9A, 0xDBA0)] + [r for r in L1_RUN_SHAPE if r != (0xDB9A, 0xDBA2)]
        _c, _u, missing = CWC.classify_runs(runs, CWC.CANDIDATES['l1_slotram'])
        self.assertIn('hook_window:ON-1 (partial window coverage)', missing)

    def test_04_run_outside_whitelist_is_unexplained(self):
        # ANC key table family (runtime 0x0609e510): documented zero-diff for
        # candidate pairs (official<->b79/EQ family instead).
        runs = L1_RUN_SHAPE + [(0x9E510, 0x9E511)]
        _c, unexplained, _m = CWC.classify_runs(runs, CWC.CANDIDATES['l1_slotram'])
        self.assertEqual(unexplained, [{'range': [0x9E510, 0x9E511], 'bytes': 1}])
        # a run straddling a window edge is just as unexplained
        runs = [r for r in L1_RUN_SHAPE if r != (0xDB9A, 0xDBA2)] + [(0xDB99, 0xDBA2)]
        _c, unexplained, _m = CWC.classify_runs(runs, CWC.CANDIDATES['l1_slotram'])
        self.assertEqual([u['range'] for u in unexplained], [[0xDB99, 0xDBA2]])

    def test_05_registry_run_unexplained_for_l1(self):
        runs = L1_RUN_SHAPE + R4_EXTRA_RUNS
        _c, unexplained, _m = CWC.classify_runs(runs, CWC.CANDIDATES['l1_slotram'])
        self.assertEqual([u['range'] for u in unexplained], [[0xA9C7C, 0xA9C80]])


@unittest.skipUnless(ARTIFACTS_READY,
                     'official firmware / jl-misctools / pi32v2-isa not '
                     'present in this checkout')
class ArtifactGateTest(unittest.TestCase):
    """Full pipeline on freshly built candidates + the spec's negatives."""

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='h3s-cotcheck-')
        cls.addClassCleanup(cls.temp.cleanup)
        temp = Path(cls.temp.name)
        extracted = temp / 'extracted'
        with (temp / 'unpack.log').open('w') as out:
            subprocess.run([sys.executable, str(ROOT / 'tools/unpack.py'),
                            '--source', str(OFFICIAL),
                            '--output-dir', str(extracted)],
                           stdout=out, check=True)
        cls.artifacts = {}
        for name, flag in (('l1_slotram', '--hearing-aid-l1-slotram'),
                           ('r4_keyhook', '--r4-keyhook')):
            out_dir = temp / name
            with (temp / f'{name}.log').open('w') as out:
                subprocess.run([sys.executable, str(ROOT / 'tools/build_quiet_candidate.py'),
                                '--source', str(OFFICIAL), '--analysis-dir', str(extracted),
                                '--attenuation-db', '0', flag,
                                '--output-dir', str(out_dir)],
                               stdout=out, check=True)
            cls.artifacts[name] = {'ufw': next(out_dir.glob('*.ufw')),
                                   'report': out_dir / 'build-report.json'}

    def _checks(self, name, ufw=None, build_report=False):
        art = self.artifacts[name]
        return CWC.run_checks(name, ufw or art['ufw'], OFFICIAL,
                              build_report=art['report'] if build_report else None)

    def _assertion(self, report, name):
        return next(a for a in report['assertions'] if a['assertion'] == name)

    def test_01_both_candidates_all_green(self):
        for name, count, registry in (('l1_slotram', 493, False),
                                      ('r4_keyhook', 574, True)):
            report = self._checks(name, build_report=True)
            self.assertTrue(report['passed'], (name, report['assertions']))
            for assertion in report['assertions']:
                self.assertTrue(assertion['passed'],
                                (name, assertion['assertion'], assertion))
            account = self._assertion(report, 'byte_account')
            self.assertEqual(account['whole_file_diff']['count'], count)
            regions = {r['region'] for r in account['runs']}
            self.assertEqual('registry_setter_slot' in regions, registry)
            self.assertEqual(len([r for r in account['runs']
                                  if r['region'].startswith('hook_window:')]), 6)
            self.assertTrue(account['build_report']['passed'])

    def test_02_boot_segment_tamper_red(self):
        """One flipped bootloader byte: all four assertions catch it."""
        art = self.artifacts['l1_slotram']
        tampered = bytearray(art['ufw'].read_bytes())
        tampered[0x600 + 0x1000] ^= 0xA5          # flash 0x1000 < 0x2000
        path = Path(self.temp.name) / 'boot-tampered.ufw'
        path.write_bytes(tampered)
        report = self._checks('l1_slotram', ufw=path)
        self.assertFalse(report['passed'])
        for assertion in ('ufw_sha', 'integrity_crc', 'shared_boot', 'byte_account'):
            self.assertFalse(self._assertion(report, assertion)['passed'], assertion)
        account = self._assertion(report, 'byte_account')
        self.assertEqual(account['whole_file_diff']['count'], 494)

    def test_03_forged_byte_account_red(self):
        """Builder account tampered in place: exactly the byte account goes
        red (image bytes untouched, sha/CRC/boot stay green)."""
        art = self.artifacts['l1_slotram']
        forged = json.loads(art['report'].read_text())
        forged['hearing_aid_l1']['byte_diff']['diff_count'] = 492
        path = Path(self.temp.name) / 'forged-report.json'
        path.write_text(json.dumps(forged))
        report = CWC.run_checks('l1_slotram', art['ufw'], OFFICIAL,
                                build_report=path)
        self.assertFalse(report['passed'])
        for assertion in ('ufw_sha', 'integrity_crc', 'shared_boot'):
            self.assertTrue(self._assertion(report, assertion)['passed'], assertion)
        account = self._assertion(report, 'byte_account')
        self.assertFalse(account['passed'])
        self.assertFalse(account['build_report']['passed'])
        # a forged position list is caught the same way
        forged['hearing_aid_l1']['byte_diff']['diff_count'] = 493
        forged['hearing_aid_l1']['byte_diff']['diff_positions'] = \
            forged['hearing_aid_l1']['byte_diff']['diff_positions'][:-1]
        path.write_text(json.dumps(forged))
        report = CWC.run_checks('l1_slotram', art['ufw'], OFFICIAL,
                                build_report=path)
        self.assertFalse(self._assertion(report, 'byte_account')['build_report']['passed'])

    def test_04_non_whitelisted_plain_tamper_red(self):
        """One flipped plain byte (ANC key table, zero-expected family):
        shared boot stays GREEN, the byte account reports the unexplained
        run — the two assertions are independent, the classifier bites."""
        art = self.artifacts['l1_slotram']
        tampered = bytearray(art['ufw'].read_bytes())
        tampered[0x600 + 0x2020 + 0x9E510] ^= 0x01   # SFC is XOR at fixed position
        path = Path(self.temp.name) / 'plain-tampered.ufw'
        path.write_bytes(tampered)
        report = self._checks('l1_slotram', ufw=path)
        self.assertFalse(report['passed'])
        self.assertTrue(self._assertion(report, 'shared_boot')['passed'])
        account = self._assertion(report, 'byte_account')
        self.assertFalse(account['passed'])
        self.assertEqual(account['unexplained_runs'],
                         [{'range': [0x9E510, 0x9E511], 'bytes': 1}])

    def test_05_cli_exit_codes(self):
        art = self.artifacts['r4_keyhook']
        green = subprocess.run([sys.executable, str(ROOT / 'tools/candidate_world_checks.py'),
                                '--candidate', 'r4_keyhook', '--candidate-path', str(art['ufw']),
                                '--official', str(OFFICIAL), '--build-report', str(art['report']),
                                '--output', str(Path(self.temp.name) / 'cli.json')],
                               capture_output=True)
        self.assertEqual(green.returncode, 0, green.stderr.decode())
        written = json.loads((Path(self.temp.name) / 'cli.json').read_text())
        self.assertTrue(written['passed'])
        bad = bytearray(art['ufw'].read_bytes())
        bad[0x600 + 0x800] ^= 0xFF
        path = Path(self.temp.name) / 'cli-bad.ufw'
        path.write_bytes(bad)
        red = subprocess.run([sys.executable, str(ROOT / 'tools/candidate_world_checks.py'),
                              '--candidate', 'r4_keyhook', '--candidate-path', str(path),
                              '--official', str(OFFICIAL)], capture_output=True)
        self.assertEqual(red.returncode, 1)
        self.assertIn('FAILED', red.stderr.decode())

    def test_06_analyze_image_trace_loaders_gate_on_the_checks(self):
        """Route-DIF loader wiring: analyze_image_trace's l1_slotram /
        r4_keyhook loaders reuse run_checks as their pre-load gate and add the
        measured app-slice sha pin. A green candidate loads through the loader;
        the same candidate with one tampered bootloader byte is REFUSED by the
        loader (the four-fold gate bites inside analyze_image_trace too)."""
        import analyze_image_trace as AIT
        for name in ('l1_slotram', 'r4_keyhook'):
            art = self.artifacts[name]
            app, plain, readonly, sha = AIT.LOADERS[name](art['ufw'], OFFICIAL)
            self.assertEqual(sha, CWC.CANDIDATES[name]['sha256'])
            self.assertEqual(len(plain), CWC.CANDIDATES[name]['plain_length'])
            self.assertEqual(readonly, ())
            self.assertEqual(AIT.PLAIN_LENGTHS[name], len(plain))
        art = self.artifacts['l1_slotram']
        tampered = bytearray(art['ufw'].read_bytes())
        tampered[0x600 + 0x1000] ^= 0xA5
        path = Path(self.temp.name) / 'loader-refused.ufw'
        path.write_bytes(tampered)
        with self.assertRaises(ValueError):
            AIT.LOADERS['l1_slotram'](path, OFFICIAL)


if __name__ == '__main__':
    unittest.main()
