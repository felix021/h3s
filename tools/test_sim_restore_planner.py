#!/usr/bin/env python3
"""tools/sim_restore_planner.py 单测（M3 §3.4：diff→扇区计划/写完模拟/保留区间/CLI 产出形状）。

计划判据全部对照 docs/sim-m3-flash-spec.md §3.4：空计划拒绝、长度门、preserved 覆盖
计划外全域（image.validate_delta 同款保留纪律）、写完模拟精确重现 target。
"""
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sim_restore_planner as srp
import image

BASE = bytes((i * 7 + 3) & 0xff for i in range(image.SIZE))          # 合成 2MiB（全片非 ff 模式）


def retarget(base, sectors, seed):
    """构造目标镜像：只改指定 4KiB 扇区内容（seed 换模式，保证 diff 非空）。"""
    target = bytearray(base)
    for sector in sectors:
        target[sector:sector + image.SECTOR] = bytes(
            (i * seed + 11) & 0xff for i in range(image.SECTOR))
    return bytes(target)


class PlanRestoreTest(unittest.TestCase):

    def test_two_sector_plan_shape(self):
        sectors = (0x0ee000, 0x0f3000)
        plan = srp.plan_restore(BASE, retarget(BASE, sectors, seed=5))
        self.assertEqual(plan['sectors'], sorted(sectors))
        self.assertEqual(plan['base_sha256'], hashlib.sha256(BASE).hexdigest())
        self.assertEqual(plan['target_sha256'],
                         hashlib.sha256(retarget(BASE, sectors, seed=5)).hexdigest())
        self.assertEqual(plan['preserved'], [[0, 0x0ee000], [0x0f4000, image.SIZE]])

    def test_boundary_sectors_zero_length_preserved(self):
        """计划含扇区 0 与最后一扇区：preserved 两段退化为零长区间（形状保持，不发明过滤）。"""
        plan = srp.plan_restore(BASE, retarget(BASE, (0x0, 0x1ff000), seed=9))
        self.assertEqual(plan['sectors'], [0x0, 0x1ff000])
        self.assertEqual(plan['preserved'], [[0, 0], [image.SIZE, image.SIZE]])

    def test_single_byte_sector_attribution(self):
        """单字节变化归属其 4KiB 扇区：偏移 4095→扇区 0；4096→扇区 0x1000。"""
        for offset, expected_sector in ((4095, 0x0), (4096, 0x1000)):
            target = bytearray(BASE)
            target[offset] ^= 0x01
            plan = srp.plan_restore(BASE, bytes(target))
            self.assertEqual(plan['sectors'], [expected_sector], hex(offset))

    def test_empty_plan_rejected(self):
        with self.assertRaisesRegex(ValueError, '空计划'):
            srp.plan_restore(BASE, BASE)

    def test_size_mismatch_rejected(self):
        with self.assertRaisesRegex(ValueError, '2MiB'):
            srp.plan_restore(BASE, BASE[:image.SIZE - 1])
        with self.assertRaisesRegex(ValueError, '2MiB'):
            srp.plan_restore(BASE + b'\x00', BASE)

    def test_written_simulation_preserves_untouched_bytes(self):
        """写完模拟的机检面：计划外字节 target==base（构造保证），preserved 区间恰覆盖计划外全域。"""
        sectors = (0x113000, 0x1d0000, 0x1d1000)
        target = retarget(BASE, sectors, seed=13)
        plan = srp.plan_restore(BASE, target)
        for start, end in plan['preserved']:
            self.assertEqual(BASE[start:end], target[start:end], (hex(start), hex(end)))
        for sector in sectors:                                     # 计划内确实变化
            self.assertNotEqual(BASE[sector:sector + image.SECTOR],
                                target[sector:sector + image.SECTOR])

    def test_cli_writes_plan_json(self):
        sectors = (0x0ee000, 0x1d0000)
        target = retarget(BASE, sectors, seed=21)
        with tempfile.TemporaryDirectory() as tmp:
            base_path = Path(tmp) / 'base.bin'
            target_path = Path(tmp) / 'target.bin'
            plan_path = Path(tmp) / 'plan.json'
            base_path.write_bytes(BASE)
            target_path.write_bytes(target)
            self.assertEqual(srp.main(['--base', str(base_path), '--target', str(target_path),
                                       '--plan', str(plan_path)]), 0)
            doc = json.loads(plan_path.read_text(encoding='utf-8'))
            self.assertEqual(doc, srp.plan_restore(BASE, target))


if __name__ == '__main__':
    unittest.main(verbosity=2)
