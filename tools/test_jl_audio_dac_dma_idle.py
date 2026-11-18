"""JL_AUDIO DAC DMA HRP/SWP 两字读回放行的定向测试(路 DMAIMPL)。

两层:
- 纯守卫单测(GuardCpu/GuardHardware 鸭子类型,不跑仿真):放行/拒绝/
  事件历史失效矩阵逐面钉死——零写、未预期重写(同值也失效)、另一核写、
  SWN/PNS 触达、DAC_CON 观测面外控制位变化、基址不符;读回=最后写值
  (零合成字节、不构造相等)。
- 模型级单测(BR28Model 直构):依赖缺失配置错误、开关关世界读侧原严格
  停、Hardware 读写钩子(事件历史先于容器分发/未映射拒绝)、报告面措辞。
- 真件字节钉子:四读点形状表与官方镜像字节/解码互证(codec 反查,防
  站点表笔误)。

不覆盖(如实边界):真实 explore 全链(空闲守卫双读→beq→POST)由主场景
开启臂的运行时证据覆盖;flush 臂 PNS 未映射停由负例 6 的守卫断言+模型
未映射语义共同钉死。
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from br28_model import (BR28Model, Blocked, JLAudioDacDmaIdleReadback,
                        JL_AUDIO_DAC_DMA_WORDS)
from probe_pi32v2 import ROOT

FIRMWARE = ROOT / 'firmware/QCY_H3S_7018F_1T2_release_note_V1.1.8_011.ufw'
HRP = JLAudioDacDmaIdleReadback.HRP          # 0xfe0510
SWP = JLAudioDacDmaIdleReadback.SWP          # 0xfe0514
PNS = JLAudioDacDmaIdleReadback.PNS          # 0xfe050c
SWN = JLAudioDacDmaIdleReadback.SWN          # 0xfe0518
DAC_CON = JLAudioDacDmaIdleReadback.DAC_CON  # 0xfe0500


class GuardCpu:
    """serve() 的 cpu 面:PC/核号/寄存器(r5 默认 JL_AUDIO 基址)。"""

    def __init__(self, pc=0x606e148, core=0, r5=0xfe0500):
        self.pc = pc
        self.core_id = core
        self.R = [0] * 16
        self.R[5] = r5


class GuardHardware:
    """serve()/note_write() 的 hardware 面:自增模型步计数(事件先后可判)。"""

    def __init__(self, steps=3204300):
        self.counts = {'completed_model_steps': steps}

    def advance(self):
        self.counts['completed_model_steps'] += 1
        return self.counts['completed_model_steps']


def armed_guard():
    """初始化写已就位(W1[3] 尾段真实时序:AK 族 DAC_CON 轨迹→四写→尾段
    |=0x110;PC/步序=停点世界实测形状)。"""
    guard = JLAudioDacDmaIdleReadback()
    hw = GuardHardware()
    guard.current_pc = 0x606ab1c                     # AK 族初始化轨迹(锚前)
    guard.note_write(DAC_CON, 0x100, 0, hw); hw.advance()
    guard.note_write(DAC_CON, 0x8000100, 0, hw); hw.advance()
    guard.note_write(DAC_CON, 0x835e100, 0, hw); hw.advance()
    for pc, address in sorted(guard.EXPECTED_WRITE_PCS.items()):
        guard.current_pc = pc
        value = {0xfe0508: 1, 0xfe0504: 0x18e180, 0xfe0514: 0, 0xfe0510: 0}[address]
        guard.note_write(address, value, 0, hw); hw.advance()
    guard.current_pc = 0x6071eb8                     # W1[3] 尾段 |=0x110(锚后钉死站点)
    guard.note_write(DAC_CON, 0x835e110, 0, hw); hw.advance()
    guard.current_pc = 0x606e148
    return guard, hw


class DacDmaIdleGuardTests(unittest.TestCase):
    """守卫面:放行语义+事件历史失效矩阵(codex 意见书 3/6 条)。"""

    def test_serve_last_written_value(self):
        # 读回=最后写值(零合成字节,值=固件自己的写);两字各自服务;
        # 读证据(pc/core/step)与写来源(pc/step/core/value)入报告面。
        guard, hw = armed_guard()
        self.assertEqual(guard.serve(GuardCpu(0x606e148), hw, {'d': 0, 'b': 5, 'n': 4}), 0)
        self.assertEqual(guard.serve(GuardCpu(0x606e14a), hw, {'d': 1, 'b': 5, 'n': 5}), 0)
        report = guard.report()
        self.assertEqual(report['phase'], 'initialization')
        self.assertEqual(report['reads']['0xfe0510']['reads_served'], 1)
        self.assertEqual(report['reads']['0xfe0514']['reads_served'], 1)
        self.assertEqual(report['reads']['0xfe0510']['last_written_value']['pc'], '0x6071ea8')
        self.assertEqual(report['reads']['0xfe0514']['last_written_value']['pc'], '0x6071ea4')
        self.assertEqual(report['reads']['0xfe0510']['last_read']['pc'], '0x606e148')

    def test_zero_write_strict_stop(self):
        # 零写读停:不补零、不从另一指针复制——拒绝并回落原严格停。
        guard = JLAudioDacDmaIdleReadback()
        hw = GuardHardware()
        self.assertIsNone(guard.serve(GuardCpu(), hw, {'d': 0, 'b': 5, 'n': 4}))
        self.assertEqual(guard.rejections, {'zero_write:0xfe0510': 1})

    def test_unexpected_rewrite_same_value_invalidates(self):
        # 未预期重写:同值也审查也失效——「曾经运行过又回到零」不得重新
        # 获得放行资格(codex 原文)。
        guard, hw = armed_guard()
        guard.current_pc = 0x607236c                  # 空间函数(数据相)写者
        guard.note_write(HRP, 0, 0, hw)               # 同值重写
        self.assertEqual(guard.phase, 'invalidated')
        self.assertIsNone(guard.serve(GuardCpu(), hw, {'d': 0, 'b': 5, 'n': 4}))
        self.assertEqual(guard.rejections.get('phase_invalidated:0xfe0510'), 1)
        self.assertEqual(guard.invalidations[0]['event'], 'unexpected_rewrite')

    def test_expected_site_rewrite_stays_initialization(self):
        # 钉死初始化站点的重写(重跑 power_on 复位)=被期望写者,审查入档
        # 但不失效(复位语义本身是初始化)。
        guard, hw = armed_guard()
        guard.current_pc = 0x6071ea8
        guard.note_write(HRP, 0, 0, hw)
        self.assertEqual(guard.phase, 'initialization')
        self.assertEqual(guard.write_totals['0xfe0510'], 2)

    def test_other_core_write_invalidates(self):
        guard, hw = armed_guard()
        guard.current_pc = 0x6071ea8
        guard.note_write(HRP, 0, 1, hw)               # 另一核(core1)
        self.assertEqual(guard.phase, 'invalidated')
        self.assertEqual(guard.invalidations[0]['event'], 'other_core_write')

    def test_write_pc_unattributed_invalidates(self):
        # 写溯源缺失(current_pc None,fail-closed)。
        guard, hw = armed_guard()
        guard.current_pc = None
        guard.note_write(HRP, 0, 0, hw)
        self.assertEqual(guard.phase, 'invalidated')
        self.assertEqual(guard.invalidations[0]['event'], 'write_pc_unattributed')

    def test_swn_pns_touch_invalidates(self):
        # SWN/PNS 触达(读或写)即失效——空间计算/flush 臂=数据相证据。
        for touch in ('write_swn', 'write_pns', 'read_swn', 'read_pns'):
            guard, hw = armed_guard()
            if touch.startswith('write'):
                guard.note_write(SWN if touch == 'write_swn' else PNS, 1, 0, hw)
            else:
                guard.note_read(SWN if touch == 'read_swn' else PNS)
            self.assertEqual(guard.phase, 'invalidated', touch)
            self.assertEqual(guard.invalidations[0]['event'], 'swn_pns_touched', touch)
            self.assertIsNone(guard.serve(GuardCpu(), hw, {'d': 0, 'b': 5, 'n': 4}), touch)

    def test_dac_con_observed_faces_do_not_invalidate(self):
        # v3 锚定:锚前初始化轨迹(AK 族 0x100→0x835e110)与锚后尾段站点
        # 0x6071eb8 全为基线(armed_guard 已覆盖,含运行时勘误事件
        # 0x606ab1c 0x100→0x8000100);锚后他站点低 4 位采样率索引面
        # (POST RMW 两腿)不失效。
        guard, hw = armed_guard()                     # 末值 0x835e110(尾段站点)
        self.assertEqual(guard.phase, 'initialization')
        self.assertIsNotNone(guard.hrp_reset_step)
        guard.current_pc = 0x606e19e
        guard.note_write(DAC_CON, 0x835e110 & ~0xF, 0, hw)   # POST &~0xF 腿
        guard.note_write(DAC_CON, 0x835e110 | 5, 0, hw)      # POST |=idx 腿
        self.assertEqual(guard.phase, 'initialization')
        # 钉死尾段站点在锚后再写(重跑 power_on 幂等尾)同样免检。
        guard.current_pc = 0x6071eb8
        guard.note_write(DAC_CON, 0x835e110, 0, hw)
        self.assertEqual(guard.phase, 'initialization')

    def test_dac_con_flush_arm_and_unknown_bits_invalidate(self):
        # 锚后他站点:flush 臂位 0x20/0x40(stop-request 证据面)与采样率
        # 索引面外位变化都失效(「数据提交/控制变化后再读 HRP 停」,codex
        # 第 6 条)。
        guard, hw = armed_guard()
        guard.current_pc = 0x606e16e
        guard.note_write(DAC_CON, 0x835e110 | 0x20, 0, hw)
        self.assertEqual(guard.phase, 'invalidated')
        self.assertEqual(guard.invalidations[0]['event'], 'dac_con_flush_arm_bits')
        guard2, hw2 = armed_guard()
        guard2.current_pc = 0x606e172
        guard2.note_write(DAC_CON, 0x835e110 | 0x40, 0, hw2)
        self.assertEqual(guard2.phase, 'invalidated')
        guard3, hw3 = armed_guard()
        guard3.current_pc = 0x6000000                 # 索引面外位(bit12)
        guard3.note_write(DAC_CON, 0x835e110 | 0x1000, 0, hw3)
        self.assertEqual(guard3.phase, 'invalidated')
        self.assertEqual(guard3.invalidations[0]['event'], 'dac_con_control_change')

    def test_base_register_mismatch_refuses(self):
        # r5≠0xfe0500:有效地址非放行字——拒绝回落普通严格路径。
        guard, hw = armed_guard()
        self.assertIsNone(guard.serve(GuardCpu(r5=0xfe0600), hw, {'d': 0, 'b': 5, 'n': 4}))
        self.assertEqual(guard.rejections.get('base_register_mismatch:0xfe0610'), 1)

    def test_unequal_pointers_not_forced_equal(self):
        # 注入 HRP≠SWP:两字各回各的最后写值,不构造相等(消费面由真实
        # beq.rr 判不等→flush 臂→PNS 写未映射停=天然墙)。
        guard = JLAudioDacDmaIdleReadback()
        hw = GuardHardware()
        guard.current_pc = 0x6071ea4
        guard.note_write(SWP, 5, 0, hw)
        guard.current_pc = 0x6071ea8
        guard.note_write(HRP, 0, 0, hw)
        self.assertEqual(guard.serve(GuardCpu(0x606e148), hw, {'d': 0, 'b': 5, 'n': 4}), 0)
        self.assertEqual(guard.serve(GuardCpu(0x606e14a), hw, {'d': 1, 'b': 5, 'n': 5}), 5)

    def test_unverified_read_location_counted(self):
        # 他处读 HRP/SWP(达 Hardware.read)=位置未核验拒绝计数。
        guard = JLAudioDacDmaIdleReadback()
        guard.note_read(HRP)
        guard.note_read(HRP)
        guard.note_read(0xfe0534)                     # 域外地址不入拒绝面
        self.assertEqual(guard.rejections, {'unverified_read_location:0xfe0510': 2})

    def test_report_face_wording(self):
        # codex 措辞分级:显式初始化空闲假设,非硅等价声明。
        guard, _ = armed_guard()
        report = guard.report()
        self.assertIn('EXPLICIT INITIALIZATION-IDLE ASSUMPTION', report['assumption'])
        self.assertIn('NOT a silicon-equivalence claim', report['assumption'])
        self.assertIn('zero synthesized bytes', report['readback_semantics'])
        self.assertTrue(report['guard']['revalidated_per_load'])
        self.assertTrue(report['guard']['not_a_permanent_address_exemption'])
        self.assertTrue(report['guard']['same_value_rewrites_audited'])

    def test_state_field_inventory_matches_snapshot_table(self):
        # M4 族字段表与实例属性集一致(world_snapshot 槽位漂移防线)。
        from world_snapshot import M4_FAMILIES
        fields = dict((slot, fs) for slot, _, fs in M4_FAMILIES)['jl_audio_dac_dma_idle']
        guard = JLAudioDacDmaIdleReadback()
        self.assertEqual(set(vars(guard)), set(fields))


class DacDmaIdleModelTests(unittest.TestCase):
    """模型级:开关/依赖/钩子/严格停语义。"""

    def test_dependency_missing_is_configuration_error(self):
        with self.assertRaisesRegex(ValueError, 'requires'):
            BR28Model(jl_audio_dac_dma_idle_readback=True)

    def test_off_world_keeps_strict_stop(self):
        # 开关关=槽 None,读侧原严格停 kind 逐字复现(负例臂 1 的模型面)。
        plain = BR28Model(jl_audio_dac_dma_writes=True)
        self.assertIsNone(plain.jl_audio_dac_dma_idle)
        with self.assertRaisesRegex(Blocked, 'jl_audio_dac_dma_read_not_modeled'):
            plain.read(HRP)
        on = BR28Model(jl_audio_dac_dma_writes=True, jl_audio_dac_dma_idle_readback=True)
        self.assertIsInstance(on.jl_audio_dac_dma_idle, JLAudioDacDmaIdleReadback)
        # 钩子已在:未核验位置读=拒绝计数+原严格停。
        with self.assertRaisesRegex(Blocked, 'jl_audio_dac_dma_read_not_modeled'):
            on.read(HRP)
        self.assertEqual(on.jl_audio_dac_dma_idle.rejections,
                         {'unverified_read_location:0xfe0510': 1})

    def test_write_hook_records_provenance_before_dispatch(self):
        # Hardware.write 钩子:观察字写先入事件历史(先于容器分发);PNS
        # 未映射写在自然 Blocked 前已失效判定。
        h = BR28Model(jl_audio_dac_dma_writes=True, jl_audio_dac_dma_idle_readback=True,
                      jl_audio_dac_init_write_records=True,
                      jl_wl_aud_write_records=True)
        guard = h.jl_audio_dac_dma_idle
        guard.current_pc = 0x6071ea8
        h.write(HRP, 0)                                # 四字容器分发
        self.assertEqual(guard.write_totals['0xfe0510'], 1)
        self.assertEqual(guard.write_events['0xfe0510'][-1]['pc'], '0x6071ea8')
        guard.current_pc = 0x606e16a
        with self.assertRaisesRegex(Blocked, 'unsupported_peripheral_write'):
            h.write(PNS, 1)                            # flush 臂墙(未映射)
        self.assertEqual(guard.phase, 'invalidated')
        self.assertEqual(guard.invalidations[0]['event'], 'swn_pns_touched')

    def test_assumption_record_in_report(self):
        h = BR28Model(jl_audio_dac_dma_writes=True, jl_audio_dac_dma_idle_readback=True)
        report = h.report()
        self.assertIn('jl_audio_dac_dma_idle_readback', report)
        self.assertIn('INITIALIZATION-IDLE ASSUMPTION',
                      report['assumptions']['jl_audio_dac_dma_idle_readback'])


class DacDmaIdleShapePins(unittest.TestCase):
    """四读点形状表与官方镜像互证(真件字节+codec 反查,防站点表笔误)。"""

    def test_read_sites_match_firmware_decoding(self):
        from probe_pi32v2 import upstream
        from probe_boot import application, ENTRY
        module = upstream()
        table = module.table.load()
        app = application(FIRMWARE)
        for pc, shape in JLAudioDacDmaIdleReadback.READ_SITES.items():
            offset = pc - ENTRY
            insn, fields, size = module.codec.decode(table, app[offset:offset + 8])
            self.assertEqual(insn.id, 'ld.w', hex(pc))
            self.assertEqual(dict(fields), shape, hex(pc))
            self.assertEqual(size, 2, hex(pc))
            self.assertEqual(app[offset:offset + 2].hex(),
                             JLAudioDacDmaIdleReadback.SITE_BYTES[pc], hex(pc))
        # 基址装载点:0x606e142 li.u32 r5=0xfe0500(6 字节)恰邻首读点。
        insn, fields, size = module.codec.decode(table, app[0x606e142 - ENTRY:][:8])
        self.assertEqual(insn.id, 'li.u32')
        self.assertEqual(fields['n'], 0xfe0500)
        self.assertEqual(0x606e142 + size, 0x606e148)
        # 两 beq.rr 均前跳出函数(空闲守卫,非回边等待环——勘误钉子)。
        for pc, target in ((0x606e14c, 0x606e194), (0x606e154, 0x606e194)):
            insn, fields, size = module.codec.decode(table, app[pc - ENTRY:][:8])
            self.assertEqual(insn.id, 'beq.rr', hex(pc))
            self.assertEqual(pc + size + fields['n'] * 2, target, hex(pc))
        # 初始化写点 PC 与四字容器白名单对齐。
        self.assertEqual(set(JL_AUDIO_DAC_DMA_WORDS),
                         set(JLAudioDacDmaIdleReadback.EXPECTED_WRITE_PCS.values()))


if __name__ == '__main__':
    unittest.main()
