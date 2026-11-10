"""Offline peripheral contract tests; never opens a device or needs private ROM."""
import unittest

from br28_model import BR28Model, CLOCK0, IPMASK, IRQ_CONFIG, IRQController, PINR1, TIMER0, Timer0
from probe_boot import Blocked


class ModelTests(unittest.TestCase):
    def timer(self):
        t=Timer0();t.write(0,0x4000);t.write(4,0);t.write(8,2);t.write(0,0x1011)
        return t

    def test_count_progress_and_w1c(self):
        t=self.timer()
        for _ in range(100):self.assertFalse(t.read(0)&0x8000)
        t.advance(11);self.assertEqual(t.read(4),2);self.assertFalse(t.pending)
        t.advance(1);self.assertTrue(t.pending);self.assertEqual(t.read(4),0)
        t.write(0,t.read(0)|0x4000)
        self.assertFalse(t.pending);self.assertEqual(t.read(0),0x1011)

    def test_pending_bit_cannot_be_forced_by_guest(self):
        t=self.timer();t.write(0,0x9011);self.assertFalse(t.pending)

    def test_stopped_timer_and_multiple_periods(self):
        t=self.timer();t.advance(36);self.assertEqual(t.expirations,3)
        t.write(0,0x4000);before=t.cnt;t.advance(500);self.assertEqual(t.cnt,before)
        self.assertFalse(t.pending)

    def test_unknown_and_unsupported_inputs_fail(self):
        with self.assertRaises(Blocked):Timer0().read(0)
        for value in (0x1012,0x1111,0x0011):
            with self.subTest(value=value),self.assertRaises(Blocked):self.timer().write(0,value)
        h=BR28Model()
        with self.assertRaises(Blocked):h.read(CLOCK0)
        self.assertFalse(h.handles(0xdeadbeef,4))
        with self.assertRaises(Blocked):h.write(0xdeadbeef,0)

    def test_pin_reset_requires_external_input_assumption(self):
        with self.assertRaises(Blocked):BR28Model().write(PINR1,23)
        h=BR28Model(reset_inputs_inactive=True);h.write(PINR1,0);h.write(PINR1,23)
        self.assertEqual(h.read(PINR1),23)

    def test_irq_mask_priority_and_unknowns(self):
        i=IRQController(initial_config=[0]*32,initial_ipmask=6)
        i.config[0]=(1|3<<1)<<16
        self.assertFalse(i.eligible(4,0x200));i.ipmask=3
        self.assertTrue(i.eligible(4,0x200));self.assertFalse(i.eligible(4,0))
        with self.assertRaises(Blocked):IRQController().eligible(4,0x200)

    def test_enabled_pending_irq_stops_before_unmodeled_delivery(self):
        h=BR28Model(initial_irq_config=[0]*32,initial_ipmask=0)
        h.write(IRQ_CONFIG,(1|3<<1)<<16)
        for address,value in ((TIMER0,0x4000),(TIMER0+4,0),(TIMER0+8,0),(TIMER0,0x1011)):
            h.write(address,value)
        with self.assertRaisesRegex(Blocked,'interrupt_delivery_not_implemented'):h.advance(0x200)
        self.assertTrue(h.timer.pending)
        h.write(IPMASK,6);h.advance(0x200)

    def test_lock_owner_and_unknowns_are_enforced(self):
        with self.assertRaises(Blocked):BR28Model(bus_lock_model=True).lock(True)
        h=BR28Model(bus_lock_model=True,initial_lock_owner=None)
        h.lock(True);self.assertEqual(h.lock_owner,0)
        with self.assertRaises(Blocked):h.lock(True)
        h.lock(False);self.assertIsNone(h.lock_owner)
        with self.assertRaises(Blocked):h.lock(False)


if __name__=='__main__':unittest.main()
