"""JL_AUDIO DAC DMA 配置五死读放行的定向测试(路 DACDMAREAD)。

两层:
- 纯守卫单测(GuardCpu/GuardHardware 鸭子类型,不跑仿真):放行(返回
  'unknown')/拒绝(执行核不符/基址不符/有效地址不符/sites=4 SWN 站点
  不放行)/通知既有 SWN 审计。
- 模型级单测(BR28Model 直构):依赖缺失配置错误、开关关世界读侧原严格
  停、开启时槽存在。
- 真件字节钉子:五读点形状表与官方镜像字节/解码互证(codec 反查,防
  站点表笔误)。

不覆盖(如实边界):真实 explore 全链(0x606b2b2..b2ba 死读→0x606b2bc
r0=4 覆盖→下一墙)由 probe_irq_entry 新场景臂的运行时证据覆盖。
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from br28_model import (BR28Model, Blocked, JLAudioDacDmaSetupDiscardedReads,
                        JLAudioDacDmaIdleReadback)
from probe_pi32v2 import ROOT

FIRMWARE = ROOT / 'firmware/QCY_H3S_7018F_1T2_release_note_V1.1.8_011.ufw'
ADR = JLAudioDacDmaSetupDiscardedReads.ADR      # 0xfe0504
LEN = JLAudioDacDmaSetupDiscardedReads.LEN      # 0xfe0508
HRP = JLAudioDacDmaSetupDiscardedReads.HRP      # 0xfe0510
SWP = JLAudioDacDmaSetupDiscardedReads.SWP      # 0xfe0514
SWN = JLAudioDacDmaSetupDiscardedReads.SWN      # 0xfe0518


class GuardCpu:
    def __init__(self, pc=0x606b2b2, core=0, r6=0xfe0500):
        self.pc = pc
        self.core_id = core
        self.R = [0] * 16
        self.R[6] = r6


class GuardHardware:
    def __init__(self, idle=None):
        self.jl_audio_dac_dma_idle = idle


def armed_guard(sites=5):
    return JLAudioDacDmaSetupDiscardedReads(sites=sites), GuardHardware()


class DacDmaSetupGuardTests(unittest.TestCase):
    def test_all_five_sites_released_by_default(self):
        guard, hw = armed_guard()
        self.assertEqual(guard.sites, 5)
        for pc, shape in guard.READ_SITES.items():
            raw = {'d': shape['d'], 'b': shape['b'], 'n': shape['n']}
            self.assertEqual(guard.serve(GuardCpu(pc), hw, raw), 'unknown', hex(pc))
        self.assertEqual(guard.served['0x606b2b2'], 1)
        self.assertEqual(guard.served['0x606b2ba'], 1)

    def test_four_sites_negative_pins_the_swN_read(self):
        # sites=4 负例:SWN 站点不放行(serve 返回 None=回落普通严格路径,
        # 自然停在 0x606b2ba 未映射读)。
        guard, hw = armed_guard(sites=4)
        self.assertIsNone(guard.shape_for(0x606b2ba))
        self.assertIsNone(guard.serve(GuardCpu(0x606b2ba), hw, {'d': 0, 'b': 6, 'n': 6}))
        for pc in guard.FOUR_WORD_SITES:
            raw = {'d': 0, 'b': 6, 'n': guard.FOUR_WORD_SITES[pc]['n']}
            self.assertEqual(guard.serve(GuardCpu(pc), hw, raw), 'unknown', hex(pc))

    def test_core_mismatch_refuses(self):
        guard, hw = armed_guard()
        self.assertIsNone(guard.serve(GuardCpu(core=1), hw, {'d': 0, 'b': 6, 'n': 1}))
        self.assertEqual(guard.rejections.get('core_mismatch:0xfe0504'), 1)

    def test_base_register_mismatch_refuses(self):
        guard, hw = armed_guard()
        self.assertIsNone(guard.serve(GuardCpu(r6=0xfe0600), hw, {'d': 0, 'b': 6, 'n': 1}))
        self.assertEqual(guard.rejections.get('base_register_mismatch:0xfe0604'), 1)

    def test_effective_address_mismatch_refuses(self):
        # r6 正确但 n 与站点不符:有效地址偏离该站点字(防御面)。
        guard, hw = armed_guard()
        self.assertIsNone(guard.serve(GuardCpu(0x606b2b2), hw, {'d': 0, 'b': 6, 'n': 2}))
        self.assertEqual(guard.rejections.get('effective_address_mismatch:0xfe0508'), 1)

    def test_swN_serve_notifies_existing_audit(self):
        idle = JLAudioDacDmaIdleReadback()
        idle.current_pc = 0x606b2ba
        guard, hw = armed_guard()
        hw.jl_audio_dac_dma_idle = idle
        self.assertEqual(guard.serve(GuardCpu(0x606b2ba), hw, {'d': 0, 'b': 6, 'n': 6}), 'unknown')
        self.assertEqual(idle.phase, 'invalidated')
        self.assertEqual(idle.invalidations[0]['event'], 'swn_pns_touched')

    def test_report_distinguishes_four_word_from_swN(self):
        guard, hw = armed_guard()
        for pc, shape in guard.READ_SITES.items():
            guard.serve(GuardCpu(pc), hw, {'d': 0, 'b': 6, 'n': shape['n']})
        report = guard.report()
        self.assertEqual(report['four_word_config_reads']['0x606b2b2'], 1)
        self.assertEqual(report['swN_discarded_read']['0x606b2ba'], 1)
        self.assertIn('unknown', report['semantics'])


class DacDmaSetupModelTests(unittest.TestCase):
    def test_dependency_missing_is_configuration_error(self):
        with self.assertRaisesRegex(ValueError, 'requires'):
            BR28Model(jl_audio_dac_dma_setup_discarded_reads=True)

    def test_off_world_keeps_slot_none(self):
        plain = BR28Model(jl_audio_dac_dma_writes=True)
        self.assertIsNone(plain.jl_audio_dac_dma_setup)
        on = BR28Model(jl_audio_dac_dma_writes=True,
                       jl_audio_dac_dma_setup_discarded_reads=True)
        self.assertIsInstance(on.jl_audio_dac_dma_setup,
                              JLAudioDacDmaSetupDiscardedReads)
        # 开关关世界:ADR/LEN/HRP/SWP 读仍原严格停(SWN 不映射=unmapped)。
        with self.assertRaisesRegex(Blocked, 'jl_audio_dac_dma_read_not_modeled'):
            plain.read(ADR)

    def test_invalid_sites_value_rejected(self):
        with self.assertRaisesRegex(ValueError, '4 or 5'):
            JLAudioDacDmaSetupDiscardedReads(sites=3)


class DacDmaSetupShapePins(unittest.TestCase):
    def test_read_sites_match_firmware_decoding(self):
        from probe_pi32v2 import upstream
        from probe_boot import application, ENTRY
        module = upstream()
        table = module.table.load()
        app = application(FIRMWARE)
        for pc, shape in JLAudioDacDmaSetupDiscardedReads.READ_SITES.items():
            offset = pc - ENTRY
            insn, fields, size = module.codec.decode(table, app[offset:offset + 8])
            self.assertEqual(insn.id, 'ld.w', hex(pc))
            self.assertEqual(dict(fields), {'d': shape['d'], 'b': shape['b'], 'n': shape['n']},
                             hex(pc))
            self.assertEqual(size, 2, hex(pc))
            self.assertEqual(app[offset:offset + 2].hex(),
                             JLAudioDacDmaSetupDiscardedReads.SITE_BYTES[pc], hex(pc))
            # 有效地址=基址 0xfe0500 + n*4,必须等于该站点字。
            self.assertEqual(0xfe0500 + shape['n'] * 4, shape['word'], hex(pc))
        # 基址装载点 0x606b264 li.u32 r6=0xfe0500(6 字节)。
        insn, fields, size = module.codec.decode(table, app[0x606b264 - ENTRY:][:8])
        self.assertEqual(insn.id, 'li.u32')
        self.assertEqual(fields['n'], 0xfe0500)
        self.assertEqual(0x606b264 + size, 0x606b26a)
        # 下一条 0x606b2bc 必须真的是 r0=4 覆盖。
        insn, fields, size = module.codec.decode(table, app[0x606b2bc - ENTRY:][:8])
        self.assertEqual(insn.id, 'li.u8')
        self.assertEqual(dict(fields), {'r': 0, 'n': 4})


if __name__ == '__main__':
    unittest.main()
