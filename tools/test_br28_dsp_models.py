#!/usr/bin/env python3
"""copex.ppi mode-22 指数近似模型的定向测试(路 COPEXIMPL)。

三层:
- 纯门单测(GateCpu 鸭子类型,不跑仿真):契约矩阵逐面钉死——正常执行
  (含与 SDK IR 形状对位:源上字必须 zext 0)、n!=22 他模式停、源上字非零/
  抽象停、域外(dsp_domain_boundary:上下界外侧+NaN/Inf)停、.p 孪生前半
  停、copex 作并行后半停;上字不透明结果的数值消费停/搬运接口。
- 安装面:上游模型冲突检查(坑⑤:models 含 copex.ppi 即报,不静默)。
- 真件字节钉子:停点编码 ff e1 16 22 与官方镜像/codec 解码互证(防
  模式号/操作数约定笔误),包括 isa 表例 r15_r14 对 r1_r0 的编码。

不覆盖(如实边界):explore 全链的推进/停点形状由主场景开启臂的运行时
证据覆盖;±1 ulp 敏感性臂由 frontier_iteration 的 --set 变体承载(实现
  属后续,本测试只钉近似语义的常量面)。
"""
import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from br28_dsp_models import (CopexExp22Model, CopexUpperWord, X_MAX, X_MIN,
                             assert_copex_exp22_installable, copex_exp22_lower)
from br28_unknown import UnknownWord
from probe_boot import Blocked
from probe_pi32v2 import ROOT

FIRMWARE = ROOT / 'firmware/QCY_H3S_7018F_1T2_release_note_V1.1.8_011.ufw'


def f32_bits(value):
    return struct.unpack('<I', struct.pack('<f', value))[0]


def f32_value(bits):
    return struct.unpack('<f', struct.pack('<I', bits))[0]


class Insn:
    """intercept() 的指令面(id 即上游 insn.id,其余字段不用)。"""

    def __init__(self, insn_id):
        self.id = insn_id


class GateCpu:
    """intercept() 的 cpu 面:PC/核号/寄存器组/finish_exact 记录。"""

    def __init__(self, pc=0x606A900, core=0):
        self.pc = pc
        self.core_id = core
        self.R = [0] * 16
        self.finished = None

    def finish_exact(self, end):
        self.finished = end

    def decode(self, pc):
        return _DECODE_TABLE[pc]


# 停点形状:0x606a900 copex.ppi(22),下一条 rts(不在门面内,只占位)。
_DECODE_TABLE = {}


def operands(_insn, raw):
    """check.operands 的最小替身:pair 字段×2 + uimm(与上游换算一致)。"""
    return {'r': 2 * raw['r'], 's': 2 * raw['s'], 'n': raw['n']}


def copex_raw(r=1, s=1, n=22):
    return {'r': r, 's': s, 'n': n}


class CopexExp22ContractTests(unittest.TestCase):
    """契约矩阵:放行语义+专用停点(kind 逐面钉死)。"""

    def execute(self, x_bits, n=22, src_hi=0, pc=0x606A900, insn_id='copex.ppi'):
        gate = CopexExp22Model()
        cpu = GateCpu(pc=pc)
        cpu.R[2] = x_bits          # 源 pair lo=s*2=2
        cpu.R[3] = src_hi
        done = gate.intercept(cpu, Insn(insn_id), copex_raw(n=n), 4, operands)
        return gate, cpu, done

    def test_domain_constants_are_normal_float32_edges(self):
        # 声明域边界:e^x 恰为 float32 最小正常数/最大有限数(闭区间端点)。
        self.assertEqual(copex_exp22_lower(X_MIN), 0x00800000)
        self.assertEqual(copex_exp22_lower(X_MAX), 0x7F7FFFFF)
        self.assertEqual((f32_bits(X_MIN), f32_bits(X_MAX)), (0xC2AEAC50, 0x42B17218))

    def test_execute_zero_and_unit(self):
        # e^0=1.0;e^1.0=正确舍入 0x402df854(近似语义:AC791N 捕获是 1 ulp
        # 低的 0x402df853,模型不刻意复刻,假设措辞见 br28_dsp_models)。
        gate, cpu, done = self.execute(0x0)
        self.assertTrue(done)
        self.assertEqual(cpu.R[2], 0x3F800000)
        self.assertIsInstance(cpu.R[3], CopexUpperWord)
        self.assertEqual(cpu.finished, cpu.pc + 4)
        self.assertEqual(gate.executions, 1)
        self.assertEqual(gate.modes, {22: 1})
        self.assertEqual(gate.first['result_bits'], '0x3f800000')
        _, cpu2, _ = self.execute(0x3F800000)
        self.assertEqual(cpu2.R[2], 0x402DF854)

    def test_execute_db2mag_domain(self):
        # app 输入域 x∈[-10.4,+4.5](eq db2mag)与整条 dB 换算链的形状值。
        for x_bits, expected in ((0xC135D2E2, 0x3742B78E),   # e^-10.4
                                 (0x40900000, 0x42B408C5)):  # e^+4.5
            _, cpu, done = self.execute(x_bits)
            self.assertTrue(done)
            self.assertEqual(cpu.R[2], expected)
        # db2mag 10^(dB/20)=(dB/20)*ln10 的浮点链(ln10 float32=0x40135d8e,
        # 0x606a90c 的 li.u32 常数族):换算输入在域内自然通过;期望值=对
        # 同一 float32 输入直接做 double exp 再取 float32(独立于模型函数)。
        import math
        for db in (-30, -12, 0, 6, 13, 39):
            x_bits = f32_bits((db / 20.0) * struct.unpack('<f', struct.pack('<I', 0x40135D8E))[0])
            expected = f32_bits(math.exp(f32_value(x_bits)))
            _, cpu, done = self.execute(x_bits)
            self.assertTrue(done)
            self.assertEqual(cpu.R[2], expected, 'dB=%d' % db)

    def test_dst_is_src_pair_shape(self):
        # 停点形状:dst=src=r3_r2(先读源再写目的,同 pair 正确)。
        gate = CopexExp22Model()
        cpu = GateCpu()
        cpu.R[2], cpu.R[3] = 0x3F800000, 0
        done = gate.intercept(cpu, Insn('copex.ppi'), {'r': 1, 's': 1, 'n': 22},
                              4, operands)
        self.assertTrue(done)
        self.assertEqual(cpu.R[2], 0x402DF854)
        self.assertIsInstance(cpu.R[3], CopexUpperWord)
        self.assertEqual(cpu.R[3].pc, 0x606A900)

    def test_other_mode_stops_dsp_mode_not_modeled(self):
        # 其余 7 个 app 模式(24/16/17/23/0/6/7)与任意 n!=22:专用停点,
        # 不做 n&31 推广;模式入账(报告面如实)。
        for mode in (0, 6, 7, 16, 17, 23, 24, 22 | 32, 22 | 224, 255):
            if mode == 22:
                continue
            gate = CopexExp22Model()
            cpu = GateCpu()
            cpu.R[2], cpu.R[3] = 0x3F800000, 0
            with self.assertRaises(Blocked) as caught:
                gate.intercept(cpu, Insn('copex.ppi'), copex_raw(n=mode), 4, operands)
            self.assertEqual(caught.exception.details['kind'], 'dsp_mode_not_modeled',
                             'mode %d' % mode)
            self.assertEqual(caught.exception.details['mode'], mode)
            self.assertEqual(gate.executions, 0)
            self.assertEqual(gate.modes.get(mode), 1)
        # 控制高位掩码(22|32 等)不因「硬件等价证据」放行——codex 契约。

    def test_source_upper_word_nonzero_stops(self):
        # SDK 形状=zext 上字清零;非零上字(AC791N e^1.0 条的 1.0 上字即此
        # 形状,互证有限)= 操作数形状未建模,不是域外。
        gate = CopexExp22Model()
        cpu = GateCpu()
        cpu.R[2], cpu.R[3] = 0x3F800000, 0x3F800000
        with self.assertRaises(Blocked) as caught:
            gate.intercept(cpu, Insn('copex.ppi'), copex_raw(), 4, operands)
        self.assertEqual(caught.exception.details['kind'], 'dsp_operand_shape_not_modeled')
        self.assertIn('zero-extended', caught.exception.details['reason'])
        self.assertEqual(caught.exception.details['source_register'], 3)
        self.assertEqual(gate.executions, 0)

    def test_source_abstract_values_stop_without_operator_traps(self):
        # 源是抽象值(UnknownWord/CopexUpperWord):isinstance 守卫先行,
        # 不触发抽象值的运算符陷阱,给出本门自己的形状停点。
        for slot, value in ((3, UnknownWord((None, 0, 0, 0), (0x19f640, None, None, None))),
                            (2, CopexUpperWord(0x606A900))):
            gate = CopexExp22Model()
            cpu = GateCpu()
            cpu.R[2], cpu.R[3] = 0x3F800000, 0
            cpu.R[slot] = value
            with self.assertRaises(Blocked) as caught:
                gate.intercept(cpu, Insn('copex.ppi'), copex_raw(), 4, operands)
            self.assertEqual(caught.exception.details['kind'], 'dsp_operand_shape_not_modeled',
                             'slot %d' % slot)

    def test_domain_boundary_stops(self):
        # 域外(闭区间外侧:上溢侧/次正规侧)+ NaN/Inf——单条域外样本推
        # 不出规则,不模仿硬件域外输出 1.0、不做 SDK 式自动钳位。
        for bits in (f32_bits(-87.4), f32_bits(-103.9), f32_bits(88.8),
                     f32_bits(200.0), 0x7F800000, 0xFF800000, 0x7FC00000):
            gate = CopexExp22Model()
            cpu = GateCpu()
            cpu.R[2], cpu.R[3] = bits, 0
            with self.assertRaises(Blocked) as caught:
                gate.intercept(cpu, Insn('copex.ppi'), copex_raw(), 4, operands)
            self.assertEqual(caught.exception.details['kind'], 'dsp_domain_boundary',
                             'bits %#x' % bits)
            self.assertEqual(caught.exception.details['x_bits'], hex(bits))
            self.assertEqual(gate.executions, 0)
        # 域内贴边值放行(闭区间端点对齐到 float32 网格:取 >=X_MIN / <=X_MAX
        # 的最近网格点——X_MIN 自身的 float32 舍入可能落到域外一侧)。
        import math
        lo_bits = f32_bits(X_MIN)
        while f32_value(lo_bits) < X_MIN:
            lo_bits -= 1                      # 负数:位模式减 1 = 向上一步
        hi_bits = f32_bits(X_MAX)
        while f32_value(hi_bits) > X_MAX:
            hi_bits -= 1                      # 正数:位模式减 1 = 向下一步
        for bits in (lo_bits, hi_bits, 0xC135D2E2):
            _, cpu, done = self.execute(bits)
            self.assertTrue(done, 'bits=%#x' % bits)

    def test_parallel_twin_first_half_rejected(self):
        # .p 孪生作前半:上游 pair() 会派发到基形,门在提交前拒绝(坑②)。
        gate = CopexExp22Model()
        cpu = GateCpu()
        cpu.R[2], cpu.R[3] = 0x0, 0
        with self.assertRaises(Blocked) as caught:
            gate.intercept(cpu, Insn('copex.ppi.p'), copex_raw(), 6, operands)
        self.assertEqual(caught.exception.details['kind'], 'dsp_operand_shape_not_modeled')
        self.assertEqual(caught.exception.details['position'], 'parallel_twin_first_half')
        self.assertEqual(caught.exception.details['instruction_id'], 'copex.ppi.p')
        self.assertEqual(cpu.finished, None)
        self.assertEqual(gate.modes, {22: 1})

    def test_copex_as_parallel_second_half_rejected(self):
        # copex 作任一 .p 指令的并行后半:提交前拒绝(两半副作用均未提交)。
        gate = CopexExp22Model()
        cpu = GateCpu(pc=0x6000000)
        cpu.R[2], cpu.R[3] = 0x0, 0
        _DECODE_TABLE[0x6000004] = (Insn('copex.ppi'), copex_raw(), 4)
        with self.assertRaises(Blocked) as caught:
            gate.intercept(cpu, Insn('add.rrr.p'), {}, 4, operands)
        self.assertEqual(caught.exception.details['kind'], 'dsp_operand_shape_not_modeled')
        self.assertEqual(caught.exception.details['position'], 'parallel_second_half')
        # .p 后半不是 copex:正常落回(False,不执行)。
        _DECODE_TABLE[0x6000004] = (Insn('ld.w'), {}, 4)
        self.assertFalse(gate.intercept(cpu, Insn('add.rrr.p'), {}, 4, operands))
        # .p 后半是 copex.ppi.p:同样拒绝。
        _DECODE_TABLE[0x6000004] = (Insn('copex.ppi.p'), copex_raw(), 6)
        with self.assertRaises(Blocked):
            gate.intercept(cpu, Insn('add.rrr.p'), {}, 4, operands)

    def test_non_copex_instruction_falls_through(self):
        gate = CopexExp22Model()
        cpu = GateCpu()
        self.assertFalse(gate.intercept(cpu, Insn('add.rrr'), {}, 4, operands))
        self.assertEqual(cpu.finished, None)
        self.assertEqual(gate.executions, 0)

    def test_ledger_first_last_modes(self):
        gate = CopexExp22Model()
        for i, bits in enumerate((0x0, 0x3F800000, 0x40900000)):
            cpu = GateCpu(pc=0x606A900 + 8 * i, core=i % 2)
            cpu.R[2], cpu.R[3] = bits, 0
            self.assertTrue(gate.intercept(cpu, Insn('copex.ppi'), copex_raw(), 4, operands))
        report = gate.report()
        self.assertEqual(report['executions'], 3)
        self.assertEqual(report['first']['pc'], '0x606a900')
        self.assertEqual(report['last']['pc'], '0x606a910')
        self.assertEqual(report['modes'], {'22': 3})


class CopexUpperWordTests(unittest.TestCase):
    """上字不透明结果:消费即停,搬运接口与快照编解码。"""

    def test_numeric_consumption_stops(self):
        word = CopexUpperWord(0x606A900)
        for op in (lambda: word & 0xFFFFFFFF, lambda: word + 1, lambda: word == 0,
                   lambda: int(word), lambda: bool(word), lambda: -word,
                   lambda: hash(word)):
            with self.assertRaises(Blocked) as caught:
                op()
            self.assertEqual(caught.exception.details['kind'], 'copex_dst_upper_word_consumed')
            self.assertEqual(caught.exception.details['sources'], ['0x606a900'] * 4)

    def test_transport_interface_matches_unknown_word(self):
        # 搬运面:bytes/origins 接口与 UnknownWord 同形(probe_boot
        # write_abstract_word 的字节路径直接可用),origins=copex PC。
        word = CopexUpperWord(0x606A900)
        self.assertEqual(word.bytes, (None,) * 4)
        self.assertEqual(word.origins, (0x606A900,) * 4)

    def test_snapshot_codec_roundtrip(self):
        # world_snapshot 抽象值编解码(坑⑥:寄存器面序列化)。
        import world_snapshot as ws
        word = CopexUpperWord(0x606A900)
        encoded = ws.encode_value(word)
        self.assertEqual(encoded, {'$copex_upper_word': {'pc': 0x606a900}})
        decoded = ws.decode_value(encoded)
        self.assertIsInstance(decoded, CopexUpperWord)
        self.assertEqual(decoded.pc, 0x606A900)
        self.assertEqual(repr(word), 'CopexUpperWord(pc=0x606a900)')


class InstallConflictTests(unittest.TestCase):
    """坑⑤:上游 copex 模型冲突检查——报冲突,不静默让位。"""

    def test_clean_models_pass(self):
        assert_copex_exp22_installable({'add.rrr': object()})
        assert_copex_exp22_installable({})

    def test_upstream_copex_model_conflicts(self):
        for name in ('copex.ppi', 'copex.ppi.p'):
            with self.assertRaises(RuntimeError) as caught:
                assert_copex_exp22_installable({name: object()})
            self.assertIn('re-review', str(caught.exception))
            self.assertIn(name, str(caught.exception))


class FirmwareShapePins(unittest.TestCase):
    """真件字节钉子:停点编码与 SDK IR 形状互证(需 reference cache)。"""

    def test_stop_site_encoding_decodes_as_copex22(self):
        # 停点 0x606a900 四字节 ff e1 16 22 = r3_r2 = copex(r3_r2)(22):
        # word1 0xE1FF 固定;word2 rrr=001(dst pair r3_r2)、sss=001(src
        # pair r3_r2)、n=0x16=22。isa 表例 ff e1 00 e0→r15_r14 互证。
        from probe_pi32v2 import upstream
        module = upstream()
        table = module.CPU().tab
        insn, raw, size = module.codec.decode(table, bytes.fromhex('ffe11622'))
        self.assertEqual(insn.id, 'copex.ppi')
        self.assertEqual(size, 4)
        self.assertEqual(raw, {'r': 1, 's': 1, 'n': 22})
        self.assertEqual(module.check.operands(insn, raw),
                         {'raw': {'r': 1, 's': 1, 'n': 22}, 'r': 2, 's': 2, 'n': 22})
        insn, raw, _ = module.codec.decode(table, bytes.fromhex('ffe100e0'))
        self.assertEqual((insn.id, module.check.operands(insn, raw)['r']), ('copex.ppi', 14))

    def test_stop_site_bytes_in_official_app(self):
        # 官方镜像内停点字节与叶子形状(反汇编实测):0x606a8fc exp 叶子
        # = mov.rr r2,r0 / li.u8 r3,0(SDK zext 形状的固件侧对应)→
        # 0x606a900 copex.ppi r3_r2(22)→ li.neg5 r6,-30 / st.w [r1]=r2
        # (只存下字,上字丢弃)→ rts。SDK IR 的 exp_float 逐指令同形。
        from probe_boot import application, ENTRY
        from probe_pi32v2 import upstream
        app = application(FIRMWARE)
        module = upstream()
        table = module.CPU().tab
        offset = 0x606A8FC - ENTRY
        leaf = app[offset:offset + 14]
        insn, raw, _ = module.codec.decode(table, leaf[0:2])
        self.assertEqual((insn.id, raw), ('mov.rr', {'x': 2, 'y': 0}))
        insn, raw, _ = module.codec.decode(table, leaf[2:4])
        self.assertEqual((insn.id, module.check.operands(insn, raw)),
                         ('li.u8', {'raw': {'r': 3, 'n': 0}, 'r': 3, 'n': 0}))
        self.assertEqual(leaf[4:8].hex(), 'ffe11622')
        insn, raw, _ = module.codec.decode(table, leaf[4:8])
        self.assertEqual((insn.id, module.check.operands(insn, raw)),
                         ('copex.ppi', {'raw': {'r': 1, 's': 1, 'n': 22}, 'r': 2, 's': 2, 'n': 22}))
        insn, raw, _ = module.codec.decode(table, leaf[8:10])
        self.assertEqual((insn.id, raw), ('st.w', {'d': 2, 'b': 1, 'n': 0}))
        insn, raw, _ = module.codec.decode(table, leaf[10:12])
        self.assertEqual(insn.id, 'rts')


if __name__ == '__main__':
    unittest.main()
