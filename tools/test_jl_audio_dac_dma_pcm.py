"""JL_AUDIO DAC DMA PCM 数据相生产路径读回+SWN 提交记录的定向测试(路 DACDMAPCM)。

两层:
- 纯守卫单测(GuardCpu/GuardHardware 鸭子类型,不跑仿真):ADR/LEN/SWP 读回=
  最近一次整字写值、SWN 读回=具名假设初值 0 后=提交写值、拒绝(执行核/
  有效地址/零写/写记录缺失)。
- 模型级单测(BR28Model 直构):依赖缺失配置错误、开关关世界槽 None 且 SWN
  仍 unmapped、开启时槽存在且依赖链完整、SWN 写 write-only 记录。
- 真件字节钉子:读点/写点形状表与官方镜像字节/解码互证(codec 反查,防站点
  表笔误)。

不覆盖(如实边界):真实 explore 全链(0x6071560 LEN/SWN 算差读→下一墙,可能
0x6071782 SWN 提交或更后的硬件消费等待环)由 probe_irq_entry 新场景臂的
运行时证据覆盖;SWN/HRP 真实活动期推进量/硬件消费不建模。
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from br28_model import (BR28Model, Blocked, JLAudioDacDmaPcmProduce,
                        JLAudioDacDmaWriteRecords)
from probe_pi32v2 import ROOT

FIRMWARE = ROOT / 'firmware/QCY_H3S_7018F_1T2_release_note_V1.1.8_011.ufw'
LEN = JLAudioDacDmaPcmProduce.LEN
ADR = JLAudioDacDmaPcmProduce.ADR
SWP = JLAudioDacDmaPcmProduce.SWP
SWN = JLAudioDacDmaPcmProduce.SWN


class GuardRecords:
    """JLAudioDacDmaWriteRecords 的最小鸭子类型(保留 stores 四字)。"""

    def __init__(self):
        self.stores = {a: {'values': []} for a in (ADR, LEN, 0xfe0510, SWP)}


class GuardHardware:
    def __init__(self):
        self.jl_audio_dac_dma = GuardRecords()
        self.jl_audio_dac_dma_idle = None


class GuardCpu:
    def __init__(self, pc=0x6071560, core=0, r12=0xfe0500, r0=0xfe0508):
        self.pc = pc
        self.core_id = core
        self.R = [0] * 16
        self.R[12] = r12
        self.R[0] = r0


def armed():
    return JLAudioDacDmaPcmProduce(), GuardHardware()


class DacDmaPcmGuardTests(unittest.TestCase):
    def test_len_serves_last_write(self):
        guard, hw = armed()
        hw.jl_audio_dac_dma.stores[LEN]['values'] = [1, 0x40]
        self.assertEqual(guard.serve(GuardCpu(0x6071560), hw, {'r': 0, 's': 12, 'n': 2}), 0x40)

    def test_adr_serves_last_write(self):
        guard, hw = armed()
        hw.jl_audio_dac_dma.stores[ADR]['values'] = [0x18e180]
        self.assertEqual(guard.serve(GuardCpu(0x607171a), hw, {'r': 2, 's': 12, 'n': 1}), 0x18e180)

    def test_swp_serves_last_write(self):
        guard, hw = armed()
        hw.jl_audio_dac_dma.stores[SWP]['values'] = [0]
        self.assertEqual(guard.serve(GuardCpu(0x607169e), hw, {'r': 6, 's': 12, 'n': 5}), 0)

    def test_swn_serves_initial_assumption(self):
        guard, hw = armed()
        self.assertEqual(guard.serve(GuardCpu(0x6071564), hw, {'r': 1, 's': 12, 'n': 6}), 0)

    def test_swn_serves_committed_value_after_write(self):
        guard, hw = armed()
        guard.write(SWN, 0x60, 0)
        self.assertEqual(guard.serve(GuardCpu(0x607180a), hw, {'r': 0, 's': 12, 'n': 6}), 0x60)
        self.assertEqual(guard.swN_writes, [{'value': 0x60, 'core': 0}])

    def test_unreleased_pc_refuses(self):
        guard, hw = armed()
        self.assertIsNone(guard.serve(GuardCpu(0x6071910), hw, {'r': 1, 's': 12, 'n': 0}))

    def test_core_mismatch_refuses(self):
        guard, hw = armed()
        self.assertIsNone(guard.serve(GuardCpu(core=1), hw, {'r': 0, 's': 12, 'n': 2}))
        self.assertEqual(guard.rejections.get('core_mismatch:0xfe0508'), 1)

    def test_effective_address_mismatch_refuses(self):
        guard, hw = armed()
        # r12 正确但 n 与站点不符:有效地址偏离 LEN(防御面)。
        self.assertIsNone(guard.serve(GuardCpu(), hw, {'r': 0, 's': 12, 'n': 0}))
        self.assertEqual(guard.rejections.get('effective_address_mismatch:0xfe0500'), 1)

    def test_zero_write_refuses(self):
        guard, hw = armed()
        self.assertIsNone(guard.serve(GuardCpu(0x6071560), hw, {'r': 0, 's': 12, 'n': 2}))
        self.assertEqual(guard.rejections.get('zero_write:0xfe0508'), 1)

    def test_missing_records_refuses(self):
        guard = JLAudioDacDmaPcmProduce()
        hw = GuardHardware()
        hw.jl_audio_dac_dma = None
        self.assertIsNone(guard.serve(GuardCpu(0x6071560), hw, {'r': 0, 's': 12, 'n': 2}))
        self.assertEqual(guard.rejections.get('write_records_missing:0xfe0508'), 1)

    def test_helper_len_site_serves(self):
        guard, hw = armed()
        hw.jl_audio_dac_dma.stores[LEN]['values'] = [0x40]
        # helper 0x607151a:ld.w r0=[r0+0],r0=0xfe0508。
        self.assertEqual(guard.serve(GuardCpu(0x607151a), hw, {'d': 0, 'b': 0, 'n': 0}), 0x40)


class DacDmaPcmModelTests(unittest.TestCase):
    def test_dependency_missing_is_configuration_error(self):
        with self.assertRaisesRegex(ValueError, 'requires'):
            BR28Model(jl_audio_dac_dma_pcm_produce=True)

    def test_off_world_keeps_slot_none_and_swn_unmapped(self):
        plain = BR28Model(jl_audio_dac_dma_writes=True,
                          jl_audio_dac_dma_setup_discarded_reads=True,
                          jl_audio_dac_dma_len_readback=True,
                          jl_audio_dac_dma_pns_write=True)
        self.assertIsNone(plain.jl_audio_dac_dma_pcm)
        # 开关关世界:SWN 仍 unmapped(不是全地址通用放行)。
        self.assertFalse(plain.handles(SWN, 4))

    def test_on_world_slot_exists_and_handles_swN(self):
        on = BR28Model(jl_audio_dac_dma_writes=True,
                       jl_audio_dac_dma_setup_discarded_reads=True,
                       jl_audio_dac_dma_len_readback=True,
                       jl_audio_dac_dma_pns_write=True,
                       jl_audio_dac_dma_pcm_produce=True)
        self.assertIsInstance(on.jl_audio_dac_dma_pcm, JLAudioDacDmaPcmProduce)
        self.assertTrue(on.handles(SWN, 4))
        # 邻字 ADR/LEN/SWP 不在本容器(仍由四字容器处理)。
        for other in (ADR, LEN, SWP):
            self.assertFalse(on.jl_audio_dac_dma_pcm.handles(other))

    def test_swN_write_is_write_only_record(self):
        on = BR28Model(jl_audio_dac_dma_writes=True,
                       jl_audio_dac_dma_setup_discarded_reads=True,
                       jl_audio_dac_dma_len_readback=True,
                       jl_audio_dac_dma_pns_write=True,
                       jl_audio_dac_dma_pcm_produce=True)
        on.jl_audio_dac_dma_pcm.write(SWN, 0x60, 0)
        self.assertEqual(on.jl_audio_dac_dma_pcm.swN, 0x60)
        self.assertEqual(on.jl_audio_dac_dma_pcm.swN_writes, [{'value': 0x60, 'core': 0}])
        # 未核验位置的 SWN 读显式严格停(kind 优先于泛 unmapped)。
        with self.assertRaisesRegex(Blocked, 'jl_audio_dac_dma_pcm_swN_read_not_modeled'):
            on.read(SWN)


class DacDmaPcmShapePins(unittest.TestCase):
    def test_read_and_write_sites_match_firmware_decoding(self):
        from probe_pi32v2 import upstream
        from probe_boot import application, ENTRY
        module = upstream()
        table = module.table.load()
        app = application(FIRMWARE)
        for pc, shape in JLAudioDacDmaPcmProduce.READ_SITES.items():
            offset = pc - ENTRY
            insn, fields, size = module.codec.decode(table, app[offset:offset + 8])
            self.assertEqual(insn.id, shape['insn'], hex(pc))
            self.assertEqual(dict(fields), shape['raw'], hex(pc))
            self.assertEqual(app[offset:offset + size].hex(), shape['bytes'], hex(pc))
            base_reg = shape['raw'].get('s', shape['raw'].get('b'))
            self.assertEqual(base_reg, (12 if 's' in shape['raw'] else 0), hex(pc))
        # SWN 提交写点形状。
        pc = JLAudioDacDmaPcmProduce.SWN_WRITE_PC
        offset = pc - ENTRY
        insn, fields, size = module.codec.decode(table, app[offset:offset + 8])
        self.assertEqual(insn.id, 'st.w.l', hex(pc))
        self.assertEqual(dict(fields), JLAudioDacDmaPcmProduce.SWN_WRITE_SHAPE['raw'], hex(pc))
        self.assertEqual(app[offset:offset + size].hex(), JLAudioDacDmaPcmProduce.SWN_WRITE_BYTES, hex(pc))


if __name__ == '__main__':
    unittest.main()
