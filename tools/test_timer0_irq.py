#!/usr/bin/env python3
"""Unit tests for the opt-in Timer0 IRQ4 delivery projection (route BE).

Mirrors tools/test_core_tick.py: model-level request/in-flight/W1C semantics
plus machine-level minimal entry/RTI for the QCY Timer0 handler 0x12ece8
(28B software frame; the real W1C ack |=0x4000 is modeled by Timer0.write).
"""
import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parent))
from br28_machine import DualCoreMachine
from br28_model import BR28Model, Timer0, TIMER0, IRQ_CONFIG
from probe_boot import Blocked, IrqDeliveryRequest

HANDLER=0x12ece8   # QCY Timer ISR table 0x60a2508 entry 0 (RAM)
SLOT=0x19fe10      # 0x19fe00+4*4 (IRQ4)
TAIL=0x12ed0c      # rti of the 38-byte handler (six-handler decode)


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


def machine(entry=True,banks=False,rti=False,repeat=False):
    return DualCoreMachine(timer0_irq_entry=entry,timer0_irq_entry_banks=banks,
                           timer0_irq_rti=rti,repeat_irq_delivery=repeat)


def armed_model(**extra):
    # Real 301,097-prefix start shape: config word masked to zero, then the
    # STD12M start write; the expiry probe pulls the boundary to N steps.
    h=BR28Model(initial_irq_config=[0]*32,initial_ipmask=0,timer0_std12m_source=True,
                timer0_irq_delivery_requests=True,**extra)
    h.write(IRQ_CONFIG,0)
    for address,value in ((TIMER0,0x4000),(TIMER0+4,0),(TIMER0+8,119999),(TIMER0,0x1400)):
        h.write(address,value)
    return h


class Timer0ModelTests(unittest.TestCase):
    def test_default_off_keeps_the_strict_stop_shape(self):
        # 与既有套件同形状(div1/source4/prd2):默认关时 pending+合格仍是
        # interrupt_delivery_not_implemented 终结停,不产生新假设键。
        h=BR28Model(initial_irq_config=[0]*32,initial_ipmask=0)
        h.write(IRQ_CONFIG,(1|3<<1)<<16)
        for address,value in ((TIMER0,0x4000),(TIMER0+4,0),(TIMER0+8,2),(TIMER0,0x1011)):
            h.write(address,value)
        h.advance(0x300);h.advance(0x300)
        with self.assertRaisesRegex(Blocked,'interrupt_delivery_not_implemented'):h.advance(0x300)
        self.assertNotIn('timer0_irq_delivery_requests',h.report()['assumptions'])
        self.assertFalse(h.timer.delivery_in_flight)

    def test_request_fires_once_per_pending_episode(self):
        # 合格 pending 抛一次 IrqDeliveryRequest(core0,irq4);in-flight 期间
        # 不再抛(计数器继续走,周期定时器语义);W1C 解除后下个周期可再抛。
        h=armed_model(timer0_irq4_expiry_probe_steps=3)
        h.write(IRQ_CONFIG,0x30000)  # the real runtime enable nibble
        h.write(TIMER0,0x1401)
        for _ in range(2):h.advance(0x300)
        with self.assertRaises(IrqDeliveryRequest) as caught:h.advance(0x300)
        self.assertEqual((caught.exception.details['core'],caught.exception.details['irq']),(0,4))
        self.assertTrue(h.timer.delivery_in_flight)
        h.advance(0x300)  # in flight: no re-raise although still pending+eligible
        self.assertTrue(h.timer.pending)
        h.write(TIMER0,0x1401|0x4000)  # the real handler ack shape (bit14 W1C)
        self.assertFalse(h.timer.pending);self.assertFalse(h.timer.delivery_in_flight)
        # PRD+1=120000 ticks at 3 ticks/step: the next episode needs 39999 more
        # steps from the post-episode residue (cnt=3 at the W1C).
        for _ in range(39998):h.advance(0x300)
        with self.assertRaises(IrqDeliveryRequest):h.advance(0x300)

    def test_ineligible_expiry_stays_inert(self):
        # 资格链不变:config[0]=0(前缀里 mask-all 后的真实状态)时到期只置
        # pending,不请求交付也不停——惰性分支的资格判据。
        h=armed_model(timer0_irq4_expiry_probe_steps=3)
        h.write(TIMER0,0x1401)
        for _ in range(6):h.advance(0x300)
        self.assertTrue(h.timer.pending);self.assertEqual(h.timer.expirations,1)
        self.assertFalse(h.timer.delivery_in_flight)

    def test_expiry_probe_seeds_counter_and_enable_nibble(self):
        # 探路 fixture:STD12M 启动写(0→1)那一刻 seed cnt=prd+1-3N、可选
        # OR IRQ4 使能+优先级1 nibble;非启动写/已运行不重复触发。
        h=armed_model(timer0_irq4_expiry_probe_steps=5)
        h.write(IRQ_CONFIG,0x33b55)  # the real runtime word (driver installs)
        h.write(TIMER0,0x1401)
        self.assertTrue(h.timer0_irq4_probe_armed)
        self.assertEqual(h.timer.cnt,119999+1-3*5)
        self.assertEqual(h.irq.config[0],0x33b55)  # delivery probe injects no eligibility
        report=h.report()['assumptions']['timer0_irq4_probe']
        self.assertEqual((report['counter_seed_at_std12m_start'],report['irq4_nibble_cleared_counterfactual'],
                          report['is_synthetic_probe_fixture_not_silicon_state']),(5,False,True))

    def test_masked_probe_clears_the_nibble_counterfactually(self):
        # 运行态已证实前缀里 IRQ4 被真实使能(0x33b55);masked 探针清 nibble
        # 演示「未安装」世界:到期只置 pending,不请求交付。
        h=armed_model(timer0_irq4_expiry_probe_steps=5,timer0_irq4_masked_probe=True)
        h.write(IRQ_CONFIG,0x33b55)
        h.write(TIMER0,0x1401)
        self.assertEqual(h.irq.config[0],0x33b55&~0xf0000)
        for _ in range(10):h.advance(0x300)
        self.assertTrue(h.timer.pending);self.assertFalse(h.timer.delivery_in_flight)

    def test_expiry_probe_ignores_earlier_source4_starts(self):
        # 早期前缀固件以 source 4 启停 Timer0(PLL/trim,~84k 步处 0x12e2c8
        # 等站点):探针对这些启动写不生效、不报错,只等 STD12M 启动。
        h=armed_model(timer0_irq4_expiry_probe_steps=5)
        h.write(TIMER0+8,7)
        h.write(TIMER0,0x1011)  # source 4 start: passes through untouched
        self.assertFalse(h.timer0_irq4_probe_armed)
        h.write(TIMER0,0x1000)  # stop (the real prefix stops between the phases)
        h.write(TIMER0+8,119999)
        h.write(TIMER0,0x1400)  # STD12M selected while stopped
        h.write(TIMER0,0x1401)  # the STD12M 0->1 start arms the probe
        self.assertTrue(h.timer0_irq4_probe_armed)


class Timer0MachineTests(unittest.TestCase):
    def test_entry_is_opt_in(self):
        m=machine(entry=False);m.cores={0:FakeCpu(VectorMem())}
        with self.assertRaises(ValueError):m.deliver_timer0_irq(0)

    def test_delivery_reads_the_real_slot_and_keeps_pending(self):
        m=machine(entry=True,banks=True)
        cpu0=FakeCpu(VectorMem(),pc=0x6067d30,core_id=0)
        cpu1=FakeCpu(VectorMem(),pc=0x12c5ca,core_id=1)
        m.cores={0:cpu0,1:cpu1}
        m.deliver_timer0_irq(0)
        self.assertEqual((cpu0.pc,cpu0.reti,cpu0.steps),(HANDLER,0x6067d30,8))
        self.assertEqual((cpu0.sp,cpu0.usp,cpu0.ssp),(0x123840,0x4013f0,0x123840))
        self.assertEqual((cpu1.pc,cpu1.reti),(0x12c5ca,0),'delivery mutated the wrong core')
        event=m.timer0_irq_deliveries[0]
        self.assertEqual((event['core'],event['irq'],event['vector_address'],event['handler']),
                         (0,4,'0x19fe10','0x12ece8'))
        self.assertFalse(event['delivery_clears_pending'])
        self.assertTrue(event['pending_cleared_by_handler_ack'])
        self.assertIn('0x4000',event['handler_ack_shape'])
        self.assertFalse(event['entry_sp_bank_or_icfg_effects_certified'])

    def test_unwritten_vector_slot_blocks_atomically(self):
        m=machine(entry=True);cpu=FakeCpu(UnwrittenMem());m.cores={0:cpu}
        with self.assertRaises(Blocked) as caught:m.deliver_timer0_irq(0)
        self.assertEqual(caught.exception.details['kind'],'vector_slot_uninitialized')
        self.assertEqual(caught.exception.details['address'],'0x19fe10')
        self.assertEqual((cpu.pc,cpu.reti,cpu.steps),(0x6012028,0,7),'failed entry mutated CPU state')
        self.assertEqual(m.timer0_irq_deliveries,[],'failed entry recorded a delivery')

    def test_one_shot_without_repeat_and_nesting_rejection(self):
        m=machine(entry=True);cpu=FakeCpu(VectorMem());m.cores={0:cpu}
        m.deliver_timer0_irq(0)
        with self.assertRaises(ValueError):m.deliver_timer0_irq(0)
        m2=machine(entry=True,repeat=True);cpu2=FakeCpu(VectorMem());m2.cores={0:cpu2}
        m2.deliver_timer0_irq(0)
        with self.assertRaises(ValueError):m2.deliver_timer0_irq(0)  # in-flight handler

    def test_rti_returns_to_the_interrupted_task(self):
        m=machine(entry=True,banks=True,rti=True)
        cpu=FakeCpu(VectorMem(),pc=0x6067d30,core_id=0);m.cores={0:cpu}
        m.deliver_timer0_irq(0)
        cpu.pc=TAIL;cpu.active_context='timer0_irq_handler';cpu.usp=0x6067d30;cpu.reti=0x6067d30
        m.return_timer0_irq(cpu)
        self.assertEqual((cpu.pc,cpu.sp,cpu.active_context,cpu.steps),(0x6067d30,0x6067d30,None,9))

    def test_rti_requires_the_exact_handler_tail(self):
        m=machine(entry=True,rti=True);cpu=FakeCpu(VectorMem());m.cores={0:cpu}
        m.deliver_timer0_irq(0)
        cpu.pc=0x12ed0a;cpu.active_context='timer0_irq_handler'
        with self.assertRaises(ValueError):m.return_timer0_irq(cpu)
        cpu.pc=TAIL;cpu.active_context=None
        with self.assertRaises(ValueError):m.return_timer0_irq(cpu)

    def test_w1c_write_clears_pending_and_in_flight(self):
        t=Timer0((4,5));t.write(0,0x4000);t.write(4,0);t.write(8,119999);t.write(0,0x1401)
        for _ in range(40000):t.advance(4)
        self.assertTrue(t.pending)
        t.delivery_in_flight=True
        t.write(0,t.read(0)|0x4000)
        self.assertFalse(t.pending);self.assertFalse(t.delivery_in_flight)


if __name__=='__main__':
    unittest.main()
