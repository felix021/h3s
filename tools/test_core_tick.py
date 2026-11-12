#!/usr/bin/env python3
"""Unit tests for the opt-in per-core system tick IRQ3 delivery projection."""
import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parent))
from br28_machine import DualCoreMachine
from br28_model import BR28Model, CoreTickStopped, CORE_TICK_BASES, IRQ_CONFIG, IPMASK
from probe_boot import Blocked, IrqDeliveryRequest

HANDLER=0x60661a4
SLOT=0x19fe0c


class FakeCpu:
    def __init__(self,mem,pc=0x6012028,core_id=0):
        self.mem=mem;self.pc=pc;self.reti=0;self.steps=7;self.core_id=core_id;self.flags=0
        self.sp_known=True;self.sp=0x4013f0;self.usp=None;self.ssp=0x123840
        self.last_explicit_ssp=0x123840
        self.bootstrap_context=None;self.after_then=None;self.repeat=None
        self.active_context=None


class VectorMem:
    def read(self,address,length):
        assert (address,length)==(SLOT,4)
        return struct.pack('<I',HANDLER)


class UnwrittenMem:
    def read(self,address,length):raise Blocked('uninitialized_memory',address=hex(address))


def machine(entry=True,banks=False,rti=False):
    return DualCoreMachine(core_tick_entry=entry,core_tick_entry_banks=banks,core_tick_rti=rti)


class CoreTickModelTests(unittest.TestCase):
    def test_ack_rmw_shape_is_exact(self):
        t=CoreTickStopped(0,1);t.cnt=0;t.prd=1;t.pending=True;t.unresolved_overflow=True
        for kind,operand in (('and',0x40),('or',0x41),('or',0x40|0x100000000)):
            with self.assertRaisesRegex(Blocked,'core_tick_rmw_not_modeled'):t.ack_rmw(kind,operand)
        t.ack_rmw('or',0x40)
        self.assertEqual((t.pending,t.unresolved_overflow,t.acknowledgements),(False,False,1))

    def test_ack_requires_a_delivered_overflow(self):
        t=CoreTickStopped(0,1);t.cnt=0;t.prd=1;t.pending=False;t.unresolved_overflow=False
        with self.assertRaisesRegex(Blocked,'ack_without_pending'):t.ack_rmw('or',0x40)

    def test_overflow_hold_then_resume_and_reenter(self):
        # Opt-in: after the overflow the counter holds (no re-raise) until the
        # handler ACK; then it resumes and can overflow again (PRD=1 re-entry).
        h=BR28Model(core_tick_configuration=True,initial_core_tick_controls=(0,0),
                    core_tick_progress=(1,1),core_tick_delivery_requests=True,
                    initial_irq_config=[0x3000]+[0]*31,initial_ipmask=0)
        base=CORE_TICK_BASES[0]
        h.write(base+4,0);h.write(base+8,1);h.write(base,1)
        h.advance(0)
        with self.assertRaises(IrqDeliveryRequest) as caught:h.advance(0)
        self.assertEqual((caught.exception.details['core'],caught.exception.details['irq']),(0,3))
        self.assertTrue(h.core_ticks[0].unresolved_overflow)
        h.advance(0)  # held, no re-raise before the ACK
        h.core_tick_rmw(base,'or',0x40)
        self.assertFalse(h.core_ticks[0].unresolved_overflow)
        h.advance(0)
        with self.assertRaises(IrqDeliveryRequest):h.advance(0)

    def test_strict_overflow_stays_terminal(self):
        h=BR28Model(core_tick_configuration=True,initial_core_tick_controls=(0,0),
                    core_tick_progress=(1,1))
        base=CORE_TICK_BASES[0]
        h.write(base+4,0);h.write(base+8,1);h.write(base,1)
        h.advance(0)
        with self.assertRaisesRegex(Blocked,'overflow_delivery_unresolved'):h.advance(0)
        with self.assertRaisesRegex(Blocked,'requires_resolution'):h.advance(0)

    def test_rmw_ack_roundtrip_and_offset_guard(self):
        h=BR28Model(core_tick_configuration=True,initial_core_tick_controls=(0,0),
                    core_tick_progress=(1,1))
        base=CORE_TICK_BASES[0]
        h.write(base+4,0);h.write(base+8,1);h.write(base,1)
        h.advance(0)
        with self.assertRaisesRegex(Blocked,'overflow_delivery_unresolved'):h.advance(0)
        h.core_tick_rmw(base,'or',0x40)
        self.assertEqual(h.core_ticks[0].acknowledgements,1)
        with self.assertRaisesRegex(Blocked,'core_tick_rmw_not_modeled'):h.core_tick_rmw(base,'or',0x41)
        with self.assertRaisesRegex(Blocked,'core_tick_rmw_not_modeled'):h.core_tick_rmw(base+4,'or',0x40)
        h2=BR28Model(core_tick_configuration=True,initial_core_tick_controls=(0,0),core_tick_progress=(1,1))
        h2.write(base+4,0);h2.write(base+8,1);h2.write(base,1)
        with self.assertRaisesRegex(Blocked,'ack_without_pending'):h2.core_tick_rmw(base,'or',0x40)

    def test_pending_rechecks_eligibility_after_critical_section(self):
        # 路 BF:溢出瞬间在临界区内(IPMASK=6>priority1)不是终判——pending
        # 期间每步重检资格(Timer0 同款,cli/sti 只屏蔽不丢弃),退出临界区
        # 即抛请求;在途不重复抛,ACK 解除并恢复计数。
        h=BR28Model(core_tick_configuration=True,initial_core_tick_controls=(0,0),
                    core_tick_progress=(4,4),core_tick_delivery_requests=True,
                    initial_irq_config=[0x3000]+[0]*31,initial_ipmask=0,
                    core_tick_irq3_expiry_probe_steps=2)
        base=CORE_TICK_BASES[0]
        h.write(base,0);h.write(base+4,0);h.write(base+8,1199999);h.write(base,1)
        h.write(IPMASK,6)
        h.advance(0x300)  # cnt 1199996: no overflow yet
        h.advance(0x300)  # overflow inside the critical section: pending, silent
        timer=h.core_ticks[0]
        self.assertTrue(timer.pending and timer.unresolved_overflow and not timer.delivery_in_flight)
        h.advance(0x300)  # still masked: stays pending, nothing requested
        self.assertTrue(timer.pending and not timer.delivery_in_flight)
        h.write(IPMASK,0)  # the real handler/idle-loop write path leaves the section
        with self.assertRaises(IrqDeliveryRequest) as caught:h.advance(0x300)
        self.assertEqual((caught.exception.details['core'],caught.exception.details['irq']),(0,3))
        self.assertTrue(timer.delivery_in_flight)
        h.advance(0x300)  # in flight: no duplicate request while the handler runs
        self.assertTrue(timer.delivery_in_flight and timer.pending)
        h.core_tick_rmw(base,'or',0x40)  # the real handler ACK shape
        self.assertFalse(timer.pending or timer.unresolved_overflow or timer.delivery_in_flight)
        h.advance(0x300)  # counter resumed from the held value
        self.assertEqual(timer.cnt,4)

    def test_first_overflow_eligible_sets_in_flight_once(self):
        # 首次溢出即合格的老路径也守同一在途纪律:抛请求置 in-flight,
        # ACK 前的重检静默,ACK 后可再次溢出重入(与 Timer0 周期重检对齐)。
        h=BR28Model(core_tick_configuration=True,initial_core_tick_controls=(0,0),
                    core_tick_progress=(1,1),core_tick_delivery_requests=True,
                    initial_irq_config=[0x3000]+[0]*31,initial_ipmask=0)
        base=CORE_TICK_BASES[0]
        h.write(base+4,0);h.write(base+8,1);h.write(base,1)
        h.advance(0)
        with self.assertRaises(IrqDeliveryRequest):h.advance(0)
        self.assertTrue(h.core_ticks[0].delivery_in_flight)
        h.advance(0)  # held, in flight: no re-raise before the ACK
        self.assertTrue(h.core_ticks[0].delivery_in_flight)
        h.core_tick_rmw(base,'or',0x40)
        self.assertFalse(h.core_ticks[0].delivery_in_flight)
        h.advance(0)
        with self.assertRaises(IrqDeliveryRequest):h.advance(0)

    def test_irq3_expiry_probe_seeds_counter_only(self):
        # 路 BF 探路 fixture:tick_timer_init 序列(CON=0 停表→request_irq
        # 已置 nibble→CNT=0→PRD→CON=1 启动)的启动写那一刻 seed
        # cnt=prd+1-4N;资格链(向量槽/nibble)不注入任何 fixture。
        h=BR28Model(core_tick_configuration=True,initial_core_tick_controls=(0,0),
                    core_tick_progress=(4,4),core_tick_delivery_requests=True,
                    initial_irq_config=[0]*32,initial_ipmask=0,
                    core_tick_irq3_expiry_probe_steps=5)
        h.write(IRQ_CONFIG,0x33b55)  # the real runtime word (IRQ3 nibble bits12-15=3)
        base=CORE_TICK_BASES[0]
        h.write(base,0);h.write(base+4,0);h.write(base+8,1199999);h.write(base,1)
        self.assertTrue(h.core_tick_irq3_probe_armed)
        self.assertEqual(h.core_ticks[0].cnt,1199999+1-4*5)
        self.assertEqual(h.irq.config[0],0x33b55)  # no eligibility injected
        for _ in range(4):h.advance(0x300)
        self.assertFalse(h.core_ticks[0].pending)
        with self.assertRaises(IrqDeliveryRequest):h.advance(0x300)
        report=h.report()['assumptions']['core_tick_irq3_probe']
        self.assertEqual((report['counter_seed_at_start_write'],report['irq3_nibble_cleared_counterfactual'],
                          report['is_synthetic_probe_fixture_not_silicon_state']),(5,False,True))

    def test_masked_probe_clears_the_nibble_counterfactually(self):
        # 反事实对照:清 IRQ3 nibble(bits12-15)的「未安装」世界——溢出只置
        # pending,不请求交付(IPMASK=0 下也惰性),无在途。
        h=BR28Model(core_tick_configuration=True,initial_core_tick_controls=(0,0),
                    core_tick_progress=(4,4),core_tick_delivery_requests=True,
                    initial_irq_config=[0]*32,initial_ipmask=0,
                    core_tick_irq3_expiry_probe_steps=5,core_tick_irq3_masked_probe=True)
        h.write(IRQ_CONFIG,0x33b55)
        base=CORE_TICK_BASES[0]
        h.write(base,0);h.write(base+4,0);h.write(base+8,1199999);h.write(base,1)
        self.assertEqual(h.irq.config[0],0x33b55&~0xf000)
        for _ in range(10):h.advance(0x300)
        timer=h.core_ticks[0]
        self.assertTrue(timer.pending and timer.unresolved_overflow and not timer.delivery_in_flight)

    def test_expiry_probe_ignores_cpu1_and_repeat_starts(self):
        # CPU1 的 tick(0xffe8ec)与重复启停(CON 1→0→1)不触发 seed:探针只
        # 盯 CPU0 首个 0→1 启动写;二次启动写(was_running 后的再启动)同样
        # 不再改计数。
        h=BR28Model(core_tick_configuration=True,initial_core_tick_controls=(0,0),
                    core_tick_progress=(4,4),core_tick_delivery_requests=True,
                    initial_irq_config=[0x3000]+[0]*31,initial_ipmask=0,
                    cpu1_held_projection=True,initial_cpu1_control=0,
                    core_tick_count_while_held=True,
                    core_tick_irq3_expiry_probe_steps=5)
        base1=CORE_TICK_BASES[1]
        h.write(base1,0);h.write(base1+4,0);h.write(base1+8,479999999);h.write(base1,1)
        self.assertFalse(h.core_tick_irq3_probe_armed)  # CPU1 start never arms the probe
        base=CORE_TICK_BASES[0]
        h.write(base,0);h.write(base+4,0);h.write(base+8,1199999);h.write(base,1)
        self.assertTrue(h.core_tick_irq3_probe_armed)
        h.write(base,0)  # stop
        h.write(base+4,0)
        h.write(base,1)  # second start while already armed: the once guard keeps the seed
        self.assertEqual(h.core_ticks[0].cnt,0)


class CoreTickMachineTests(unittest.TestCase):
    def test_entry_is_opt_in(self):
        m=machine(entry=False);m.cores={0:FakeCpu(VectorMem())}
        with self.assertRaises(ValueError):m.deliver_core_tick(0)

    def test_delivery_targets_the_overflowed_core(self):
        m=machine(entry=True,banks=True)
        cpu0=FakeCpu(VectorMem(),pc=0x6012028,core_id=0)
        cpu1=FakeCpu(VectorMem(),pc=0x12c5ca,core_id=1)
        m.cores={0:cpu0,1:cpu1}
        m.deliver_core_tick(1)
        self.assertEqual((cpu0.pc,cpu0.reti,cpu0.steps),(0x6012028,0,7),'delivery mutated the wrong core')
        self.assertEqual((cpu1.pc,cpu1.reti,cpu1.sp),(HANDLER,0x12c5ca,0x123840))
        event=m.core_tick_deliveries[0]
        self.assertEqual((event['core'],event['irq'],event['vector_address'],event['handler']),
                         (1,3,'0x19fe0c',hex(HANDLER)))
        self.assertTrue(event['pending_cleared_by_handler_ack'])
        self.assertFalse(event['delivery_clears_pending'])
        self.assertFalse(event['entry_sp_bank_or_icfg_effects_certified'])

    def test_unwritten_vector_slot_blocks_atomically(self):
        m=machine(entry=True);cpu=FakeCpu(UnwrittenMem());m.cores={0:cpu}
        with self.assertRaises(Blocked) as caught:m.deliver_core_tick(0)
        self.assertEqual(caught.exception.details['kind'],'vector_slot_uninitialized')
        self.assertEqual(caught.exception.details['address'],'0x19fe0c')
        self.assertEqual((cpu.pc,cpu.reti,cpu.steps),(0x6012028,0,7),'failed entry mutated CPU state')
        self.assertEqual(m.core_tick_deliveries,[],'failed entry recorded a delivery')

    def test_rti_returns_to_the_interrupted_task(self):
        m=machine(entry=True,banks=True,rti=True)
        cpu=FakeCpu(VectorMem(),pc=0x6012028,core_id=0);m.cores={0:cpu}
        m.deliver_core_tick(0)
        cpu.pc=0x60661e6;cpu.active_context='core_tick_handler';cpu.usp=0x6012028;cpu.reti=0x6012028
        m.return_core_tick(cpu)
        self.assertEqual((cpu.pc,cpu.sp,cpu.active_context,cpu.steps),(0x6012028,0x6012028,None,9))

    def test_rti_requires_the_exact_handler_tail(self):
        m=machine(entry=True,rti=True);cpu=FakeCpu(VectorMem());m.cores={0:cpu}
        m.deliver_core_tick(0)
        with self.assertRaises(ValueError):m.return_core_tick(cpu)


if __name__=='__main__':
    unittest.main()
