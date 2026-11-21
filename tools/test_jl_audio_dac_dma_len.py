"""JL_AUDIO DAC DMA LEN 活读回读的定向测试(路 DACDMALEN)。

两层:
- 纯守卫单测(GuardCpu/GuardHardware 鸭子类型,不跑仿真):放行(返回最近一次
  整字写值)/拒绝(执行核不符/基址不符/有效地址不符/零写/写记录缺失)。
- 模型级单测(BR28Model 直构):依赖缺失配置错误、开关关世界槽 None 且读侧
  原严格停、开启时槽存在且依赖链完整。
- 真件字节钉子:两读点形状表与官方镜像字节/解码互证(codec 反查,防站点表
  笔误)。

不覆盖(如实边界):真实 explore 全链(0x606ddc4/cc 活读→钳位→下一墙,可能
PNS 写 0x606de56)由 probe_irq_entry 新场景臂的运行时证据覆盖。
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from br28_model import (BR28Model, Blocked, JLAudioDacDmaLenReadback,
                        JLAudioDacDmaWriteRecords)
from probe_pi32v2 import ROOT

FIRMWARE = ROOT / 'firmware/QCY_H3S_7018F_1T2_release_note_V1.1.8_011.ufw'
LEN = JLAudioDacDmaLenReadback.LEN      # 0xfe0508


class GuardRecords:
    """JLAudioDacDmaWriteRecords 的最小鸭子类型(只保留 stores[LEN])。"""

    def __init__(self, values):
        self.stores = {LEN: {'values': list(values)}}


class GuardHardware:
    def __init__(self, values=(0x21,)):
        self.jl_audio_dac_dma = GuardRecords(values)


class GuardCpu:
    def __init__(self, pc=0x606ddc4, core=0, r8=LEN):
        self.pc = pc
        self.core_id = core
        self.R = [0] * 16
        self.R[8] = r8


def armed_guard(values=(0x21,)):
    return JLAudioDacDmaLenReadback(), GuardHardware(values)


class DacDmaLenGuardTests(unittest.TestCase):
    def test_serves_last_whole_word_write(self):
        guard, hw = armed_guard((0x21, 0x40))
        self.assertEqual(guard.serve(GuardCpu(0x606ddc4), hw, {'r': 3, 's': 8, 'n': 0}), 0x40)
        self.assertEqual(guard.served['0x606ddc4'], 1)
        self.assertEqual(guard.served_values, [0x40])

    def test_then_arm_re_read_served(self):
        guard, hw = armed_guard((0x40,))
        self.assertEqual(guard.serve(GuardCpu(0x606ddcc), hw, {'r': 1, 's': 8, 'n': 0}), 0x40)
        self.assertEqual(guard.served['0x606ddcc'], 1)

    def test_unreleased_pc_refuses(self):
        guard, hw = armed_guard()
        self.assertIsNone(guard.serve(GuardCpu(0x606b2b4), hw, {'r': 0, 's': 6, 'n': 2}))

    def test_core_mismatch_refuses(self):
        guard, hw = armed_guard()
        self.assertIsNone(guard.serve(GuardCpu(core=1), hw, {'r': 3, 's': 8, 'n': 0}))
        self.assertEqual(guard.rejections.get('core_mismatch:0xfe0508'), 1)

    def test_base_register_mismatch_refuses(self):
        guard, hw = armed_guard()
        self.assertIsNone(guard.serve(GuardCpu(r8=0xfe0500), hw, {'r': 3, 's': 8, 'n': 0}))
        self.assertEqual(guard.rejections.get('base_register_mismatch:0xfe0500'), 1)

    def test_effective_address_mismatch_refuses(self):
        # r8 正确但 n 与站点不符:有效地址偏离 LEN(防御面)。
        guard, hw = armed_guard()
        self.assertIsNone(guard.serve(GuardCpu(), hw, {'r': 3, 's': 8, 'n': 1}))
        self.assertEqual(guard.rejections.get('effective_address_mismatch:0xfe050c'), 1)

    def test_zero_write_refuses_no_fabricated_value(self):
        # 负例:0xfe0508 零写→返回 None(回落普通严格路径),不补零、不臆造。
        guard, hw = armed_guard(())
        self.assertIsNone(guard.serve(GuardCpu(), hw, {'r': 3, 's': 8, 'n': 0}))
        self.assertEqual(guard.rejections.get('zero_write:0xfe0508'), 1)
        self.assertEqual(guard.served_values, [])

    def test_missing_records_refuses(self):
        guard = JLAudioDacDmaLenReadback()
        hw = GuardHardware()
        hw.jl_audio_dac_dma = None
        self.assertIsNone(guard.serve(GuardCpu(), hw, {'r': 3, 's': 8, 'n': 0}))
        self.assertEqual(guard.rejections.get('write_records_missing:0xfe0508'), 1)

    def test_report_shows_served_value(self):
        guard, hw = armed_guard((0x40,))
        guard.serve(GuardCpu(), hw, {'r': 3, 's': 8, 'n': 0})
        report = guard.report()
        self.assertEqual(report['served']['0x606ddc4'], 1)
        self.assertEqual(report['served_values'], ['0x40'])
        self.assertIn('clamp', report['semantics'])


class DacDmaLenModelTests(unittest.TestCase):
    def test_dependency_missing_is_configuration_error(self):
        # len_readback 依赖 setup_discarded_reads(同一条配置函数链)。
        with self.assertRaisesRegex(ValueError, 'requires'):
            BR28Model(jl_audio_dac_dma_len_readback=True)

    def test_off_world_keeps_slot_none(self):
        plain = BR28Model(jl_audio_dac_dma_writes=True,
                          jl_audio_dac_dma_setup_discarded_reads=True)
        self.assertIsNone(plain.jl_audio_dac_dma_len)
        on = BR28Model(jl_audio_dac_dma_writes=True,
                       jl_audio_dac_dma_setup_discarded_reads=True,
                       jl_audio_dac_dma_len_readback=True)
        self.assertIsInstance(on.jl_audio_dac_dma_len, JLAudioDacDmaLenReadback)
        # 开关关世界:LEN 读仍原严格停(不是全地址通用回读)。
        with self.assertRaisesRegex(Blocked, 'jl_audio_dac_dma_read_not_modeled'):
            plain.read(LEN)

    def test_write_records_feed_the_readback(self):
        on = BR28Model(jl_audio_dac_dma_writes=True,
                       jl_audio_dac_dma_setup_discarded_reads=True,
                       jl_audio_dac_dma_len_readback=True)
        on.jl_audio_dac_dma.write(LEN, 1, 0)
        on.jl_audio_dac_dma.write(LEN, 0x40, 0)
        self.assertEqual(on.jl_audio_dac_dma_len.serve(
            GuardCpu(), on, {'r': 3, 's': 8, 'n': 0}), 0x40)


class DacDmaLenShapePins(unittest.TestCase):
    def test_read_sites_match_firmware_decoding(self):
        from probe_pi32v2 import upstream
        from probe_boot import application, ENTRY
        module = upstream()
        table = module.table.load()
        app = application(FIRMWARE)
        for pc, shape in JLAudioDacDmaLenReadback.READ_SITES.items():
            offset = pc - ENTRY
            insn, fields, size = module.codec.decode(table, app[offset:offset + 8])
            self.assertEqual(insn.id, 'ld.w.l', hex(pc))
            self.assertEqual(dict(fields), shape, hex(pc))
            self.assertEqual(size, 4, hex(pc))
            self.assertEqual(app[offset:offset + 4].hex(),
                             JLAudioDacDmaLenReadback.SITE_BYTES[pc], hex(pc))
            # 有效地址=基址 0xfe0508 + n*4,必须等于 LEN。
            self.assertEqual(0xfe0508 + shape['n'] * 4, LEN, hex(pc))
        # 基址装载点 0x606dda2 li.u32 r8=0xfe0508(6 字节)。
        insn, fields, size = module.codec.decode(table, app[0x606dda2 - ENTRY:][:8])
        self.assertEqual(insn.id, 'li.u32')
        self.assertEqual(fields['n'], 0xfe0508)
        self.assertEqual(0x606dda2 + size, 0x606dda8)


if __name__ == '__main__':
    unittest.main()
