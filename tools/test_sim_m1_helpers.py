#!/usr/bin/env python3
"""tools/test_sim_m1_verdicts.py 纯函数面的单测（路 SIMB1C；世界相关断言由执行器实跑覆盖）。

SIMB1F 增补：Verdict 四态机（codex P1——必需断言的 note 不得混进全绿）。"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_sim_m1_verdicts import Verdict, eq_curve, parse_notify, read_frame, write_frame


class FrameTest(unittest.TestCase):

    def test_read_frame_shape(self):
        frame = read_frame(0x6F, 0x17)
        self.assertEqual(frame, bytes([0xfe, 0xdc, 0xba, 0xc0, 0xff, 0x00, 0x04,
                                       0x6f, 0xfe, 0x01, 0x17, 0xef]))

    def test_write_frame_shape(self):
        frame = write_frame(7, 0x17, bytes([1, 2, 3]))
        self.assertEqual(frame, bytes([0xfe, 0xdc, 0xba, 0xc0, 0xff, 0x00, 0x06,
                                       7, 0x17, 3, 1, 2, 3, 0xef]))

    def test_parse_notify_roundtrip(self):
        payload = bytes([0x17, 3, 1, 1, 2])
        body = bytes([0x00, 0x6F]) + payload
        frame = b'\xfe\xdc\xba\x00\xff' + len(body).to_bytes(2, 'big') + body + b'\xef'
        status, sn, parsed = parse_notify(frame)
        self.assertEqual((status, sn, parsed), (0, 0x6F, payload))


class EqCurveTest(unittest.TestCase):

    def test_layout_3b_header_7b_segments(self):
        curve = eq_curve(2)
        self.assertEqual(len(curve), 3 + 7 * 2)
        self.assertEqual(curve[0], 1)                       # type=1 自定义
        self.assertEqual(curve[1] | (curve[2] << 8), (-400) & 0xffff)   # mastergain -4dB×100
        # 段 0：freq 30 LE / q 80 / gain 0 / iir 0
        self.assertEqual(curve[3:5], (30).to_bytes(2, 'little'))
        self.assertEqual(curve[5:7], (80).to_bytes(2, 'little'))
        self.assertEqual(curve[7:9], (0).to_bytes(2, 'little'))
        self.assertEqual(curve[9], 0)

    def test_len_mod7_is_3_for_all_segment_counts(self):
        for segments in range(1, 11):
            self.assertEqual(len(eq_curve(segments)) % 7, 3, segments)


class VerdictFourStateTest(unittest.TestCase):
    """codex P1（2026-11-17）：PASS/FAIL/BLOCKED/PARTIAL——必需断言被世界状态
    挡住（note_required）不得混进全绿；可选诊断（note）不影响 verdict。"""

    def _verdict(self):
        return Verdict({})

    def test_all_pass_is_pass(self):
        verdict = self._verdict()
        verdict.check('a', True)
        verdict.check('b', True)
        self.assertEqual((verdict.failures, verdict.incomplete), (0, 0))

    def test_required_note_makes_partial(self):
        verdict = self._verdict()
        verdict.check('a', True)
        verdict.note_required('w4', {'queue_face': None})
        self.assertEqual(verdict.incomplete, 1)              # 必需项未完成被计数
        self.assertEqual(verdict.failures, 0)
        self.assertIsNone(verdict.blocked)

    def test_optional_note_does_not_degrade(self):
        verdict = self._verdict()
        verdict.check('a', True)
        verdict.note('c4_optional', {})
        self.assertEqual((verdict.failures, verdict.incomplete, verdict.notes), (0, 0, 1))

    def test_first_blocked_reason_is_kept(self):
        verdict = self._verdict()
        verdict.fail_blocked('B1_world_dead', {'x': 1})
        verdict.fail_blocked('C0_resume_stopped', {'y': 2})
        self.assertEqual(verdict.blocked['reason'], 'B1_world_dead')
        self.assertEqual(verdict.blocked['subsequent'][0]['reason'], 'C0_resume_stopped')

    def test_check_rows_recorded(self):
        verdict = self._verdict()
        verdict.check('a', False, {'detail': 1})
        verdict.note_required('b', {'detail': 2})
        rows = verdict.record['checks']
        self.assertEqual([row['id'] for row in rows], ['a', 'b'])
        self.assertEqual(rows[0]['ok'], False)
        self.assertEqual(rows[1]['state'], 'incomplete')


if __name__ == '__main__':
    unittest.main(verbosity=2)
