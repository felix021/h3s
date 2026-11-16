#!/usr/bin/env python3
"""tools/test_sim_m1_verdicts.py 纯函数面的单测（路 SIMB1C；世界相关断言由执行器实跑覆盖）。"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_sim_m1_verdicts import eq_curve, parse_notify, read_frame, write_frame


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


if __name__ == '__main__':
    unittest.main(verbosity=2)
