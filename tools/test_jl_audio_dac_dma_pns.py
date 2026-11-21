"""JL_AUDIO DAC DMA PNS(0xfe050c)写侧 write-only 整字记录的定向测试(路 DACDMAPNS)。

两层:
- 模型级单测(BR28Model 直构):依赖缺失配置错误、开关关世界槽 None 且 PNS
  仍 unmapped、开启时槽存在且读侧显式严格停。
- 写记录单测:handles 只认 PNS、整字写记录值/核/次数、读回显式拒绝。
- 真件字节钉子:写点 0x606de56 的形状表与官方镜像字节/解码互证(codec 反查,
  防站点表笔误),有效地址=0xfe0508+4=0xfe050c=PNS。

不覆盖(如实边界):真实 explore 全链(0x606de56 写 PNS→下一墙)由
probe_irq_entry 新场景臂 src_sync_cursor_progress_dacdmapns_probe 的运行时
证据覆盖;PNS 的衰减语义/运行时状态推进不建模。
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from br28_model import BR28Model, Blocked, JLAudioDacDmaPnsWrite
from probe_pi32v2 import ROOT

FIRMWARE = ROOT / 'firmware/QCY_H3S_7018F_1T2_release_note_V1.1.8_011.ufw'
PNS = JLAudioDacDmaPnsWrite.PNS      # 0xfe050c


class PnsWriteModelTests(unittest.TestCase):
    def test_dependency_missing_is_configuration_error(self):
        # pns_write 依赖 len_readback(同一函数链,LEN 活读放行是到达
        # 0x606de56 的前置)。
        with self.assertRaisesRegex(ValueError, 'requires'):
            BR28Model(jl_audio_dac_dma_pns_write=True)

    def test_off_world_keeps_slot_none(self):
        plain = BR28Model(jl_audio_dac_dma_writes=True,
                          jl_audio_dac_dma_setup_discarded_reads=True,
                          jl_audio_dac_dma_len_readback=True)
        self.assertIsNone(plain.jl_audio_dac_dma_pns)
        on = BR28Model(jl_audio_dac_dma_writes=True,
                       jl_audio_dac_dma_setup_discarded_reads=True,
                       jl_audio_dac_dma_len_readback=True,
                       jl_audio_dac_dma_pns_write=True)
        self.assertIsInstance(on.jl_audio_dac_dma_pns, JLAudioDacDmaPnsWrite)
        # 开关关世界:PNS 仍 unmapped(不是全地址通用放行)。
        self.assertFalse(plain.handles(PNS, 4))
        with self.assertRaisesRegex(Blocked, 'unsupported_peripheral_write'):
            plain.write(PNS, 0x60)

    def test_write_records_whole_word_value_and_core(self):
        on = BR28Model(jl_audio_dac_dma_writes=True,
                       jl_audio_dac_dma_setup_discarded_reads=True,
                       jl_audio_dac_dma_len_readback=True,
                       jl_audio_dac_dma_pns_write=True)
        self.assertTrue(on.handles(PNS, 4))
        on.write(PNS, 0x60)
        entry = on.jl_audio_dac_dma_pns.stores[PNS]
        self.assertEqual(entry['values'], [0x60])
        self.assertEqual(entry['writes'], 1)
        self.assertEqual(entry['cores'], [0])
        self.assertEqual(on.jl_audio_dac_dma_pns.word_writes, 1)
        # 第二次写(阈值函数 0x6072496 的 st.w)同记录,旧值无关。
        on.write(PNS, 0x21)
        self.assertEqual(on.jl_audio_dac_dma_pns.word_writes, 2)
        self.assertEqual(on.jl_audio_dac_dma_pns.stores[PNS]['values'], [0x60, 0x21])

    def test_read_is_explicitly_strict_stopped(self):
        on = BR28Model(jl_audio_dac_dma_writes=True,
                       jl_audio_dac_dma_setup_discarded_reads=True,
                       jl_audio_dac_dma_len_readback=True,
                       jl_audio_dac_dma_pns_write=True)
        # 读回显式严格停(kind 优先于泛 unmapped,全 app 零静态读者)。
        with self.assertRaisesRegex(Blocked, 'jl_audio_dac_dma_pns_read_not_modeled'):
            on.read(PNS)

    def test_handles_only_pns_word(self):
        on = BR28Model(jl_audio_dac_dma_writes=True,
                       jl_audio_dac_dma_setup_discarded_reads=True,
                       jl_audio_dac_dma_len_readback=True,
                       jl_audio_dac_dma_pns_write=True)
        self.assertTrue(on.handles(PNS, 4))
        # 邻字 LEN/SWP/SWN 不在本容器(仍由四字容器/严格停处理)。
        for other in (0xfe0508, 0xfe0514, 0xfe0518):
            self.assertFalse(on.jl_audio_dac_dma_pns.handles(other))


class PnsWriteShapePins(unittest.TestCase):
    def test_write_site_matches_firmware_decoding(self):
        from probe_pi32v2 import upstream
        from probe_boot import application, ENTRY
        module = upstream()
        table = module.table.load()
        app = application(FIRMWARE)
        pc = 0x606de56
        offset = pc - ENTRY
        insn, fields, size = module.codec.decode(table, app[offset:offset + 8])
        self.assertEqual(insn.id, 'st.w.l', hex(pc))
        # 原始字节字段:n 是字索引(×4)→ 有效地址 0xfe0508+4=0xfe050c=PNS。
        self.assertEqual(dict(fields), {'r': 8, 's': 2, 'n': 1}, hex(pc))
        self.assertEqual(size, 4, hex(pc))
        self.assertEqual(app[offset:offset + 4].hex(), 'd0ec8520', hex(pc))
        # 基址装载点 0x606dda2 li.u32 r8=0xfe0508(6 字节)。
        insn2, fields2, size2 = module.codec.decode(table, app[0x606dda2 - ENTRY:][:8])
        self.assertEqual(insn2.id, 'li.u32')
        self.assertEqual(fields2['n'], 0xfe0508)
        self.assertEqual(0x606dda2 + size2, 0x606dda8)
        # 有效地址=0xfe0508 + n*4 == PNS。
        self.assertEqual(0xfe0508 + fields['n'] * 4, PNS, hex(pc))

    def test_second_pns_write_site_shape(self):
        # 全 app 第二处 PNS 写(阈值函数 0x6072470 体内):st.w [r5+12]=r1,
        # r5=0xfe0500,+12=0xfe050c=PNS。字节 d1 63 钉死(静态扫描零读回)。
        from probe_pi32v2 import upstream
        from probe_boot import application, ENTRY
        module = upstream()
        table = module.table.load()
        app = application(FIRMWARE)
        pc = 0x6072496
        offset = pc - ENTRY
        insn, fields, size = module.codec.decode(table, app[offset:offset + 8])
        self.assertEqual(insn.id, 'st.w', hex(pc))
        self.assertEqual(dict(fields), {'d': 1, 'b': 5, 'n': 3}, hex(pc))
        self.assertEqual(app[offset:offset + size].hex(), 'd163', hex(pc))


if __name__ == '__main__':
    unittest.main()
