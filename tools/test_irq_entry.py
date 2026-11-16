#!/usr/bin/env python3
"""Unit tests for the opt-in minimal IRQ127 CPU entry projection."""
import struct
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0,str(Path(__file__).resolve().parent))
from br28_machine import DualCoreMachine
from br28_model import SoftwareIrqCommands
from probe_boot import Blocked


class FakeCpu:
    def __init__(self,mem,pc=0x12b608):
        self.mem=mem;self.pc=pc;self.reti=0;self.steps=7;self.core_id=0;self.flags=0
        self.sp_known=True;self.sp=0x4013f0;self.usp=None;self.ssp=0x123840
        self.last_explicit_ssp=0x123840
        self.bootstrap_context=None;self.after_then=None;self.repeat=None
        self.active_context=None


class UnwrittenMem:
    def read(self,address,length):raise Blocked('uninitialized_memory',address=hex(address))


class VectorMem:
    def read(self,address,length):
        assert (address,length)==(0x19fffc,4)
        return struct.pack('<I',0x12AD2A)


def machine(entry=True):
    m=DualCoreMachine(software_irq_entry=entry)
    m.hardware=SimpleNamespace(software_irq=SoftwareIrqCommands((8,0)))
    return m


class IrqEntryTests(unittest.TestCase):
    def test_entry_is_opt_in(self):
        m=machine(entry=False)
        with self.assertRaises(ValueError):m.deliver_software_irq(FakeCpu(VectorMem()),127)

    def test_only_the_scheduler_ipi_is_covered(self):
        m=machine()
        for irq in (124,125,126,3):
            with self.assertRaises(ValueError):m.deliver_software_irq(FakeCpu(VectorMem()),irq)

    def test_unwritten_vector_slot_blocks_atomically(self):
        m=machine();cpu=FakeCpu(UnwrittenMem())
        with self.assertRaises(Blocked) as caught:m.deliver_software_irq(cpu,127)
        self.assertEqual(caught.exception.details['kind'],'vector_slot_uninitialized')
        self.assertEqual(caught.exception.details['address'],'0x19fffc')
        self.assertEqual((cpu.pc,cpu.reti,cpu.steps),(0x12b608,0,7),'failed entry mutated CPU state')
        self.assertEqual(m.hardware.software_irq.pending[0].value&8,8,'failed entry cleared pending')
        self.assertEqual(m.software_irq_deliveries,[],'failed entry recorded a delivery')

    def test_delivery_loads_vector_and_acknowledges_once(self):
        m=machine();cpu=FakeCpu(VectorMem())
        m.deliver_software_irq(cpu,127)
        self.assertEqual((cpu.pc,cpu.reti,cpu.steps),(0x12AD2A,0x12b608,8))
        self.assertEqual(m.hardware.software_irq.pending[0].value&8,0)
        self.assertEqual(m.hardware.software_irq.acknowledgements,1)
        event=m.software_irq_deliveries[0]
        self.assertEqual((event['vector_address'],event['handler'],event['reti_from']),
                         ('0x19fffc','0x12ad2a','0x12b608'))
        self.assertFalse(event['entry_sp_bank_or_icfg_effects_certified'])
        with self.assertRaises(ValueError):m.deliver_software_irq(FakeCpu(VectorMem()),127)


class KeyChainFixtureTests(unittest.TestCase):
    """路 S/sim-keychain:键链 fixture 布局钉死(防退化,反汇编依据见常量注释)。"""

    def test_sensor_object_layout(self):
        import probe_irq_entry as pie
        obj=pie.key_chain_sensor_object_bytes()
        self.assertEqual(len(obj),70)
        # read_key 0x6000E94 消费面:+0 enable / +4 通道字 / +10 阈值表 / +50 键 id 表
        self.assertEqual(obj[0],1,'enable/键数门(b[obj+0]==0 时 read_key 直接返 255)')
        self.assertEqual(struct.unpack_from('<I',obj,4)[0],pie.KEY_CHAIN_CHANNEL_WORD)
        thresholds=struct.unpack_from('<20H',obj,10)
        self.assertEqual(thresholds[:2],(500,1022),'两档分界+ADC 满量程界(>1022 为哨兵)')
        self.assertEqual([t for t in thresholds[2:]], [1023]*18,'剩余槽全哨兵')
        ids=list(obj[50:70])
        self.assertEqual(ids[:2],[255,10],'无键 255(<=500)/按下键 10(501..1022)')
        self.assertEqual(pie.KEY_CHAIN_RES_NAMED_VALUES[pie.KEY_CHAIN_CHANNEL_WORD],
                         pie.KEY_CHAIN_RES_PRESSED,'RES fixture 键通道按下档缺省就位')

    def test_res_fixture_extends_without_mutating_by_dict(self):
        import probe_irq_entry as pie
        # BY 场景自己的 dict 不受键链扩展影响(legacy 报告不变的前提)
        self.assertEqual(pie.FD31_ADC_RES_NAMED_VALUES,{0x10000:400,0x10006:500,0x10004:400})
        self.assertEqual(pie.KEY_CHAIN_RES_NAMED_VALUES,
                         {0x10000:400,0x10006:500,0x10004:400,0x10001:700})


class KeyChainMatrixFixtureTests(unittest.TestCase):
    """路 AJ/sim-keymatrix:三键矩阵 fixture 布局自洽性(防退化)。

    布局约束=S 路钉死的 read_key 消费面(+0/+4/+10/+50);键 id 分配依据
    BW handler 掩码 0x09050307(key10..13→逻辑键{7,3,5,9})与 App eventID
    0x{logical}0{gesture+1} 的对应(推导,全部 synthetic-not-measured)。
    """

    def test_matrix_sensor_object_layout(self):
        import probe_irq_entry as pie
        obj=pie.key_chain_matrix_sensor_object_bytes()
        self.assertEqual(len(obj),70)
        self.assertEqual(obj[0],3,'enable/键数=三键矩阵自洽值(非零即过 read_key 旁路门)')
        self.assertEqual(struct.unpack_from('<I',obj,4)[0],pie.KEY_CHAIN_CHANNEL_WORD,
                         '三键共用同一 ADC 分压通道(S 路通道字不变)')
        thresholds=struct.unpack_from('<20H',obj,10)
        self.assertEqual(thresholds[:4],(500,720,850,1022),
                         '释放+三键按下共四档分界(升序)')
        self.assertEqual(thresholds[4:],(1023,)*16,'剩余槽全哨兵(>1022=10 位 ADC 满量程界)')
        self.assertEqual(list(thresholds),sorted(thresholds),'阈值表必须升序(线性定档前提)')
        ids=list(obj[50:70])
        self.assertEqual(ids[:4],[255,10,11,12],
                         '释放 255/多功能 10/音量- 11/音量+ 12(BW 掩码+App eventID 推导)')
        self.assertEqual(ids[4:],[0]*16)

    def test_matrix_bands_are_disjoint_and_levels_inside(self):
        import probe_irq_entry as pie
        thresholds=[500,720,850,1022]
        bands=[(0,500,255),(501,720,10),(721,850,11),(851,1022,12)]
        for (lo,hi,key_id),(name,level) in zip(bands,
                (('release',pie.KEY_CHAIN_MATRIX_RES_RELEASE),
                 *pie.KEY_CHAIN_MATRIX_RES_LEVELS.items())):
            self.assertLessEqual(lo,level)
            self.assertLessEqual(level,hi,
                                 f'{name} 代表读数 {level} 必须落在自己的阈值区间 {lo}..{hi}')
            # 与上下界的距离≫EMA 瞬态幅度(S 路稳态假设同款论证)
            self.assertGreaterEqual(min(level-lo,hi-level),50,name)
        self.assertEqual(pie.KEY_CHAIN_MATRIX_RES_RELEASE,pie.KEY_CHAIN_RES_RELEASE,
                         '释放态与 S 路同一档(三键全松→同一分压)')

    def test_matrix_res_fixture_extends_without_mutating_other_dicts(self):
        import probe_irq_entry as pie
        self.assertEqual(pie.FD31_ADC_RES_NAMED_VALUES,{0x10000:400,0x10006:500,0x10004:400})
        self.assertEqual(pie.KEY_CHAIN_MATRIX_RES_NAMED_VALUES,
                         {0x10000:400,0x10006:500,0x10004:400,
                          pie.KEY_CHAIN_CHANNEL_WORD:pie.KEY_CHAIN_MATRIX_RES_LEVELS['multi']})
        self.assertNotEqual(pie.KEY_CHAIN_MATRIX_RES_NAMED_VALUES[pie.KEY_CHAIN_CHANNEL_WORD],
                            pie.KEY_CHAIN_RES_NAMED_VALUES[pie.KEY_CHAIN_CHANNEL_WORD],
                            '矩阵场景键通道缺省档(600)须区别于 S 路(700)——两 dict 独立')


class AncifOfficialResourcesTests(unittest.TestCase):
    """路 FLS:flash 顶 sdfile ANCIF/ANCIF1 官方资源内容 fixture 的静态钉子。

    嵌入字节与官方 UFW(在场时)外层重提取互证;UFW 缺席时跳过重提取侧,
    自校验(长度/魔数/内层 CRC/头内名)不跳过。
    """

    def test_shape_and_self_check(self):
        import binascii
        import probe_irq_entry
        ancif, ancif1 = probe_irq_entry.ANCIF_OFFICIAL_RESOURCES
        self.assertEqual(len(ancif), 0x100)
        self.assertEqual(len(ancif1), 0xac8)
        for blob, magic, name, payload_len in (
                (ancif, 0x9881, b'ANCGAIN01', 0xec),
                (ancif1, 0x98c1, b'ANCCOEF01', 0xab4)):
            w0, stored, h6, h8 = struct.unpack_from('<IHHH', blob)
            self.assertEqual(h6, magic)
            self.assertEqual(6 + (w0 - 2), len(blob))
            self.assertEqual(h8, 10 + payload_len)
            self.assertEqual(blob[10:10 + len(name)], name)
            self.assertEqual(binascii.crc_hqx(blob[6:], 0), stored)

    def test_matches_official_ufw_outer_layer(self):
        import subprocess
        import tempfile
        import probe_irq_entry as pe
        root = Path(__file__).resolve().parents[1]
        firmware = root / 'firmware/QCY_H3S_7018F_1T2_release_note_V1.1.8_011.ufw'
        misctools = root / 'tools/jl-misctools/firmware'
        if not firmware.exists():
            self.skipTest('official UFW not fetched')
        if not (misctools / 'fwunpack_newfw.py').exists():
            self.skipTest('jl-misctools submodule not initialized')
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            subprocess.run([sys.executable, str(misctools / 'fwunpack_newfw.py'),
                            '--dirname', str(work / 'unpacked'), str(firmware)],
                           check=True, capture_output=True)
            subprocess.run([sys.executable, str(root / 'tools/extract_ufw_resources.py'),
                            str(firmware), '--unpacked', str(work / 'unpacked'),
                            '--output', str(work / 'extracted')],
                           check=True, capture_output=True)
            ancif, ancif1 = pe.ANCIF_OFFICIAL_RESOURCES
            for name, embedded in (('ANCIF', ancif), ('ANCIF1', ancif1)):
                extracted = (work / 'extracted/outer' / name).read_bytes()
                self.assertEqual(len(extracted), len(embedded))
                self.assertEqual(extracted, embedded,
                                 f'{name} embedded bytes diverge from the official UFW outer layer')


class JlRandSeedTests(unittest.TestCase):
    """路 RAND:JL_RAND->R64L(0xfd3b00)熵源种子 fixture 的静态钉子。

    主臂常量=4 字节小端 0x12345678(非退化:Lehmer MINSTD 首步 state=
    0x2a18e99e,6 字节 MAC 输出 31 54 23 93 fe eb);退化种子 0/0x7FFFFFFF/
    0xFFFFFFFE 是 LCG 不动点(全零输出)不得作主臂。
    """

    def test_seed_constant_is_non_degenerate(self):
        import probe_irq_entry
        seed = int.from_bytes(probe_irq_entry.JL_RAND_SEED_BYTES, 'little')
        self.assertEqual(len(probe_irq_entry.JL_RAND_SEED_BYTES), 4)
        self.assertEqual(seed, 0x12345678)
        self.assertNotIn(seed, (0, 0x7fffffff, 0xfffffffe),
                         '主臂种子不得是 LCG 不动点(全零 MAC)')

    def test_seed_first_lcg_step_shape(self):
        import probe_irq_entry
        seed = int.from_bytes(probe_irq_entry.JL_RAND_SEED_BYTES, 'little')
        # rand_bytes:seed 读入 state 后,循环每步 state=(state*16807) mod
        # 0x7fffffff、输出 2 字节=state>>15。首步 state=0x2a18e99e,6 字节
        # MAC=3 步输出(内存序 31 54 23 93 fe eb),非全零。
        state = seed
        out = []
        for _ in range(3):
            state = (state * 16807) % 0x7fffffff
            out.append(state >> 15)
        self.assertEqual(out[0], 0x5431)
        self.assertEqual(state, 0x75ff4bfa)
        mac = b''.join(x.to_bytes(2, 'little') for x in out)
        self.assertEqual(mac, bytes.fromhex('31542393feeb'))
        self.assertTrue(any(mac))

    def test_registry_feature_wires_to_constant(self):
        import probe_irq_entry
        import frontier_registry
        f = frontier_registry._BY_SWITCH.get('jl_rand_seed')
        self.assertIsNotNone(f, 'jl_rand_seed 须登记在 frontier_registry')
        self.assertEqual(f.stack, 'conditional')
        self.assertEqual(f.value_attr, 'JL_RAND_SEED_BYTES')
        self.assertIn('ancif_official_resources', f.requires)
        self.assertIs(getattr(probe_irq_entry, f.value_attr),
                      probe_irq_entry.JL_RAND_SEED_BYTES)


class JlEqInitWritesTests(unittest.TestCase):
    """路 JLEQ:EQ 初始化两字(CON0 0xfe1500/FLT_ADR 0xfe151c)write-only
    记录的静态钉子(codex gpt-6-astra 定调 (c):恰两字、零读回、零合成
    初值;六字 0xfe1504..0xfe1518 零触达留 unmapped)。

    敏感性定调:两写都是纯 st.w 整字覆盖写、无 RMW 无读→初值零消费,
    不设 ones 敏感性探针(CV/ADA 的 ones 臂是 RMW 保留位验证,形状不同
    不适用);验证=主臂 read_attempts 断言 0 + 负例关停停原边界。
    """

    def test_whitelist_is_exactly_two_words(self):
        from br28_model import JL_EQ_INIT_WORDS
        self.assertEqual(tuple(JL_EQ_INIT_WORDS), (0xfe1500, 0xfe151c))
        # 六个声明成员与 0xfe1520 起不进白名单(零访问证据不凭 __RW 推)。
        for unmodeled in (0xfe1504, 0xfe1508, 0xfe150c,
                          0xfe1510, 0xfe1514, 0xfe1518, 0xfe1520):
            self.assertNotIn(unmodeled, JL_EQ_INIT_WORDS)

    def test_registry_feature_shape(self):
        import frontier_registry
        f = frontier_registry._BY_SWITCH.get('jl_eq_init_writes')
        self.assertIsNotNone(f, 'jl_eq_init_writes 须登记在 frontier_registry')
        self.assertEqual(f.stack, 'conditional')
        self.assertFalse(f.value_switch)
        self.assertIsNone(f.value_attr)
        # 依赖边=场景推进顺序(RAND 放行后链才到达停点),非硬件依赖。
        self.assertEqual(f.requires, ('jl_rand_seed',))

    def test_stop_word_and_companion_addresses(self):
        # 停点字=CON0(0x6072b92 写 0xfe1500=值 0);同函数伴写=FLT_ADR
        # (0x6072bae 写 0xfe151c=分配缓冲指针)。br28.h JL_EQ 基址钉死:
        # hs_base(0xfe0000)+map_adr(0x15,0)=(0xfe0000+(64*0x15+0)*4)。
        self.assertEqual(0xfe0000 + (64 * 0x15 + 0) * 4, 0xfe1500)
        self.assertEqual(0xfe1500 + 0x1c, 0xfe151c)


if __name__=='__main__':
    unittest.main()
