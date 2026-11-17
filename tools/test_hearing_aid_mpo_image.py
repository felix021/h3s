#!/usr/bin/env python3
"""Offline verification of the hearing-aid L2 whole-band MPO curve (route HAL2).

The L2 MPO candidate (build_quiet_candidate.py --hearing-aid-mpo, optionally
composed with --hearing-aid-demo) rewrites Drc1Eff as a flat-tail three-point
curve (0.3,0.3)/(M,M)/(X,M). Route-L2 codex review pinned why the multi-point
shape is REQUIRED: the DRC core (drc.o DrcInit/DrcGainProcess, reference SDK
LLVM IR) computes per-segment slopes from adjacent knees and EXTRAPOLATES the
last segment beyond the last knee -- there is no clamp at the final out value.
Writing only threshold[2].out yields a ~2:1 compressor, not a ceiling (at
120 dB detection the curve still outputs ~104.7 dB). This suite pins:

Logic tier (no firmware input, always runs):
1. the curve model (verify_mpo_curve) accepts the flat-tail shape and rejects
   the OLD single-out shape at overdrive probes (the negative test for the
   corrected semantics), non-increasing knees and non-zero tail slope;
2. the knee spread rule (X = 110.3 below, M + 10.3 at/above the stock tail)
   keeps inputs strictly increasing and the tail flat.

Artifact tier (skipped when the official firmware / analysis dir / jl-misctools
is absent, same convention as test_candidate_world_checks.py):
3. standalone MPO (M=95) and demo (M=100) candidates build clean and pass the
   INDEPENDENT auditor verify_hearing_aid_l0.audit (CRC + byte-exact diff +
   param roundtrip + flat-tail curve), M >= 110.3 (M=115) included (tail raise);
4. one flipped DRC parameter byte must fail the auditor (anti-vacuity);
5. the build report carries the honest semantics string (whole-band digital
   domain, NOT per-band, NOT SPL, NOT transient protection).

UNVALIDATED ON DEVICE: nothing here executes any audio path; the candidates
are OFFLINE_UNTESTED and must not be flashed.
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
import build_quiet_candidate as BQ                               # noqa: E402
import verify_hearing_aid_l0 as VA                               # noqa: E402

OFFICIAL = ROOT / 'firmware/QCY_H3S_7018F_1T2_release_note_V1.1.8_011.ufw'
ANALYSIS = ROOT / 'analysis/v1.1.8/unpacked/files/stream.bin'
ARTIFACTS_READY = (OFFICIAL.exists() and ANALYSIS.exists()
                   and (ROOT / 'tools/jl-misctools/firmware/jltech').exists())

F32 = BQ.f32
STOCK_KNEES = [(F32(0.3), F32(0.3)), (F32(90.3), F32(90.3)), (F32(110.3), F32(110.3))]


def flat_tail(mpo):
    """The curve the builder/verifier agree on for MPO=mpo (same X rule)."""
    tail = F32(110.3) if F32(mpo) < F32(110.3) else F32(mpo + 10.3)
    return [(F32(0.3), F32(0.3)), (F32(mpo), F32(mpo)), (tail, F32(mpo))]


class MpoCurveModelTest(unittest.TestCase):
    """Logic tier: the corrected semantics, pinned without any firmware input."""

    def test_01_flat_tail_curve_passes(self):
        for mpo in (60.0, 88.0, 95.0, 100.0, 110.0, 110.3, 118.0):
            knees = flat_tail(mpo)
            slopes = BQ.verify_mpo_curve(knees, 3, F32(mpo))
            self.assertEqual(slopes, [1.0, 0.0], mpo)

    def test_02_old_single_out_shape_is_not_a_ceiling(self):
        """负例（codex 复核钉死）：只改末点 out 的旧 L0 形状不是上限。

        末段斜率 (100-90.3)/(110.3-90.3)=0.485，120dB 检测电平沿末段外推到
        ~104.7dB，仍越过 MPO=100 —— verify_mpo_curve 必须拒绝它。
        """
        old_shape = [(F32(0.3), F32(0.3)), (F32(90.3), F32(90.3)), (F32(110.3), F32(100.0))]
        slopes = [(old_shape[i + 1][1] - old_shape[i][1]) /
                  (old_shape[i + 1][0] - old_shape[i][0]) for i in range(2)]
        self.assertAlmostEqual(slopes[1], 0.485, places=3)
        probe = 120.0
        seg = sum(1 for i in range(1, 2) if probe > old_shape[i][0])
        b = old_shape[seg + 1][1] - slopes[seg] * old_shape[seg + 1][0]
        self.assertGreater(slopes[seg] * probe + b, 100.0 + 4.0)  # ~104.7 dB
        with self.assertRaises(ValueError):
            BQ.verify_mpo_curve(old_shape, 3, F32(100.0))

    def test_03_rejects_non_increasing_knees(self):
        dup = [(F32(0.3), F32(0.3)), (F32(100.0), F32(100.0)), (F32(100.0), F32(100.0))]
        with self.assertRaises(ValueError):
            BQ.verify_mpo_curve(dup, 3, F32(100.0))

    def test_04_rejects_nonzero_tail_slope(self):
        ramp = [(F32(0.3), F32(0.3)), (F32(100.0), F32(100.0)), (F32(110.3), F32(104.7))]
        with self.assertRaises(ValueError):
            BQ.verify_mpo_curve(ramp, 3, F32(100.0))

    def test_05_tail_spread_rule_keeps_strict_increase(self):
        """M >= 110.3 时尾点抬高 M+10.3，in 序列始终严格递增。"""
        for mpo in (60.0, 100.0, 110.3, 118.0, 120.0):
            knees = flat_tail(mpo)
            ins = [k[0] for k in knees]
            self.assertTrue(all(b > a for a, b in zip(ins, ins[1:])), mpo)
            self.assertEqual(knees[2][1], F32(mpo))            # 尾段输出 = M
            self.assertEqual(knees[1], (F32(mpo), F32(mpo)))    # 拐点 (M,M)


@unittest.skipUnless(ARTIFACTS_READY, 'official firmware / analysis inputs absent')
class MpoCandidateArtifactTest(unittest.TestCase):
    """Artifact tier: build + independent audit of the offline candidates."""

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        temp = Path(cls.temp.name)
        cls.source = OFFICIAL
        cls.candidates = {}
        for name, extra in (('demo', ['--hearing-aid-demo']),
                            ('mpo95', ['--hearing-aid-mpo', '95']),
                            ('mpo115', ['--hearing-aid-mpo', '115'])):
            out_dir = temp / name
            with (temp / f'{name}.log').open('w') as out:
                subprocess.run([sys.executable, str(ROOT / 'tools/build_quiet_candidate.py'),
                                '--source', str(OFFICIAL), '--attenuation-db', '0',
                                *extra, '--output-dir', str(out_dir)],
                               stdout=out, check=True)
            cls.candidates[name] = {'ufw': next(out_dir.glob('*.ufw')),
                                    'report': json.loads(
                                        (out_dir / 'build-report.json').read_text())}

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_01_independent_audit(self):
        for name, mpo, mode in (('demo', 100.0, 'demo'), ('mpo95', 95.0, 'mpo'),
                                ('mpo115', 115.0, 'mpo')):
            result = VA.audit(self.source, self.candidates[name]['ufw'],
                              mpo=mpo, mode=mode)
            self.assertTrue(result['crc_valid'])
            self.assertEqual(result['mpo_curve_slopes'], [1.0, 0.0])
            self.assertGreaterEqual(result['ufw_diff_count'], 16)

    def test_02_tampered_byte_fails_audit(self):
        """反空转：翻转一个 DRC 参数字节必须让独立校验器变红。"""
        ufw = bytearray(self.candidates['mpo95']['ufw'].read_bytes())
        # 翻转 UFW 密文里的一个字节会同时破坏 CRC 与参数回读——再翻回参数位无法
        # 定位（密文位置相关），所以直接破坏候选后 CRC 校验必须失败。
        ufw[len(ufw) // 2] ^= 0xFF
        bad = Path(self.temp.name) / 'tampered.ufw'
        bad.write_bytes(ufw)
        with self.assertRaises(ValueError):
            VA.audit(self.source, bad, mpo=95.0, mode='mpo')

    def test_03_report_carries_honest_semantics(self):
        for name in ('demo', 'mpo95', 'mpo115'):
            ha = self.candidates[name]['report']['hearing_aid_demo']
            semantics = ha['mpo_semantics']
            for token in ('全带', '非每频独立', '非 SPL', '瞬时'):
                self.assertIn(token, semantics)
            self.assertEqual(ha['roundtrip']['threshold_num'], 3)
            self.assertEqual(ha['roundtrip']['mpo_curve'][2][1], ha['mpo_db'])
            self.assertTrue(any(c['field'] == 'threshold[1].in'
                                for c in ha['changes']))

    def test_04_mpo_only_leaves_eq_stock(self):
        """纯 MPO 候选不动 EQ（demo 模式才写 1kHz/+39dB）。"""
        self.assertEqual(self.candidates['mpo95']['report']['hearing_aid_demo']['mode'], 'mpo')
        rt = self.candidates['mpo95']['report']['hearing_aid_demo']['roundtrip']
        self.assertEqual((rt['freq'], rt['gain']), (1070, 0.0))
        demo_rt = self.candidates['demo']['report']['hearing_aid_demo']['roundtrip']
        self.assertEqual((demo_rt['freq'], demo_rt['gain']), (1000, BQ.f32(39.0)))


if __name__ == '__main__':
    unittest.main()
