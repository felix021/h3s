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


if __name__=='__main__':
    unittest.main()
