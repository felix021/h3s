#!/usr/bin/env python3
"""tools/sim_flash_fixture.py 的单元测试（路 SIMB2A / M2：fixture 构成与地址映射）。

合成容器测解析+映射逻辑（UFW 目录格式按 image.py 的 '<HHHHIII44s16s' + encode() 构造——
测的是本工具的代码，不是臆造设备字节）；真实资产档（firmware/ 的官方 UFW 在场时）做
端到端：2MiB 长度、flash.bin 逐字节落 0、bank A 三重 CRC 自洽（image.bank 复用真机不变量）、
0xe6000 起全 0xff、报告逐区来源标注。资产不在场则 skip（units 档免网；AVD 验收时实跑）。
"""
import hashlib
import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / 'usb-recovery'))
import image
import sim_flash_fixture as sff

ROOT = Path(__file__).resolve().parents[1]


def make_ufw(flash_bin: bytes) -> bytes:
    """按 image.py 的容器格式造一个最小 UFW（目录项 80B @64..144，flash.bin 随后）。"""
    directory = struct.pack('<HHHHIII44s16s', 0, 0, 0, 0, 144, len(flash_bin), len(flash_bin),
                            b'flash.bin'.ljust(44, b'\x00'), b'')
    return (b'\x00' * 64) + image.encode(directory) + flash_bin


class AssembleTest(unittest.TestCase):

    def test_layout_mapping_and_report(self):
        flash_bin = bytes((i * 3 + 7) & 0xff for i in range(0x3000))
        fixture, report = sff.assemble(flash_bin)
        self.assertEqual(len(fixture), image.SIZE)
        self.assertEqual(fixture[:0x3000], flash_bin)                 # 逐字节落 0
        self.assertEqual(fixture[0x3000:], b'\xff' * (image.SIZE - 0x3000))   # 其余擦除态
        self.assertEqual(report['sha256'], hashlib.sha256(fixture).hexdigest())
        self.assertTrue(report['read_only'])
        sources = {r['start']: r['source'] for r in report['regions']}
        self.assertIn('flash.bin', sources[0])
        self.assertIn('0xff', sources[0x3000])
        named = {r['name']: r for r in report['named_regions']}
        self.assertEqual(named['bank A 应用密文（929875B）']['end_exclusive'], 0x2020 + 929875)
        self.assertEqual(named['bank B 应用密文（930245B，实机故障 r2——无公开工件）']['content'],
                         'synthetic-erased-ff')
        self.assertEqual(named['bank A boot-info']['content'], 'ufw-flash.bin')

    def test_assemble_rejects_oversize(self):
        with self.assertRaises(ValueError):
            sff.assemble(b'\x00' * (image.SIZE + 1))

    def test_build_requires_pinned_ufw(self):
        """非受检资产（随机字节）在 good_flash 的 SHA/结构门处报错——不臆造 fixture。"""
        with self.assertRaises(ValueError):
            sff.build(make_ufw(b'\xab' * 0x100))


class RealAssetTest(unittest.TestCase):
    """官方 UFW 在场时的端到端档（units 档免网：资产缺省 skip，AVD 验收实跑）。"""

    def test_real_ufw_fixture(self):
        manifest = __import__('json').loads((ROOT / 'firmware/manifest.json').read_text(encoding='utf-8'))
        ufw_path = ROOT / 'firmware' / manifest['assets']['current']['filename']
        if not ufw_path.exists():
            self.skipTest('官方 UFW 未在场（fetch_firmware --variant current 后重跑；AVD 验收前实跑）')
        fixture, report = sff.build(ufw_path.read_bytes())
        flash_bin = image.good_flash(ufw_path.read_bytes())
        self.assertEqual(len(fixture), image.SIZE)
        self.assertEqual(fixture[:len(flash_bin)], flash_bin)         # UFW flash.bin 逐字节落 0
        self.assertEqual(fixture[len(flash_bin):], b'\xff' * (image.SIZE - len(flash_bin)))
        header, app = image.bank(fixture, 0x2000)                     # 真机不变量：头/密文/明文三重 CRC
        self.assertEqual(len(app), 929875)
        self.assertEqual(report['sha256'], hashlib.sha256(fixture).hexdigest())


if __name__ == '__main__':
    unittest.main(verbosity=2)
