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


if __name__=='__main__':
    unittest.main()
