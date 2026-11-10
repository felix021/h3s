"""Offline peripheral contract tests; never opens a device or needs private ROM."""
import unittest

from br28_model import BR28Model, CLOCK0, IPMASK, IRQ_CONFIG, IRQController, PINR1, TIMER0, Timer0, PartialWord, MMU, MMU_BASE, P11AccessProbe, PMU_STA
from probe_boot import Blocked


class ModelTests(unittest.TestCase):
    def test_p11_access_writes_never_invent_an_acknowledgement(self):
        for status in (None,0,1):
            probe=P11AccessProbe(status)
            with self.assertRaises(Blocked):probe.read()
            for _ in range(3):
                for value in P11AccessProbe.sequence:probe.write(value)
                if status is None:
                    with self.assertRaisesRegex(Blocked,'unknown_external_pmu_status'):probe.read()
                else:self.assertEqual(probe.read(),status)
            self.assertEqual(probe.status,status)
        with self.assertRaises(Blocked):P11AccessProbe(1).write(0)
        with self.assertRaises(Blocked):BR28Model(p11_access_probe=True,p11_status=1).write(PMU_STA,1)

    def test_mmu_aliases_invalid_pages_and_bounds(self):
        m=MMU(0)
        m.write(MMU_BASE+4,0x120000);m.write(MMU_BASE+8,0x120003)
        m.write(MMU_BASE,1)
        table=bytes.fromhex('00860186')  # physical0x130000,0x130080
        read=lambda a,n:table[a-0x120000:a-0x120000+n]
        self.assertEqual(m.translate(0x400004,4,read),0x130004)
        self.assertEqual(m.translate(0x4000fc,4,read),0x1300fc)
        self.assertEqual(m.translate(0x06000100,6,read),0x06000100)
        with self.assertRaisesRegex(Blocked,'outside_table'):m.translate(0x400100,4,read)
        with self.assertRaisesRegex(Blocked,'invalid_page'):m.translate(0x400000,4,lambda a,n:bytes(n))
        with self.assertRaisesRegex(Blocked,'cross_page'):m.translate(0x40007f,2,read)

    def test_mmu_initial_control_and_bounds_are_not_fabricated(self):
        with self.assertRaisesRegex(Blocked,'uninitialized_mmu'):MMU().read(MMU_BASE)
        with self.assertRaisesRegex(Blocked,'disabled'):MMU(0).translate(0x400000,4,lambda a,n:bytes(n))
        with self.assertRaisesRegex(Blocked,'invalid_mmu_table'):MMU(0).write(MMU_BASE,1)
        h=BR28Model(mmu_model=True,initial_mmu_con=0)
        h.write(MMU_BASE+4,0x120000);h.write(MMU_BASE+8,0x1227ff);h.write(MMU_BASE,0x11)
        self.assertEqual(h.read(MMU_BASE),0x11)
        with self.assertRaisesRegex(Blocked,'unsupported_mmu_control'):h.write(MMU_BASE,0x80)

    def test_partial_gpio_bits_stay_unknown(self):
        h=BR28Model(gpio_model=True)
        h.gpio_rmw(0xfd5108,'or',2)
        word=h.gpio[0xfd5108]
        self.assertEqual((word.value,word.known),(2,2))
        with self.assertRaises(Blocked):h.read(0xfd5108)
        h.gpio_rmw(0xfd5108,'and',~2)
        self.assertEqual((word.value,word.known),(0,2))
        with self.assertRaises(Blocked):h.read(0xfd5108)
        h.write(0xfd5108,0xa5a55a5a);h.gpio_rmw(0xfd5108,'or',1)
        self.assertEqual(h.read(0xfd5108),0xa5a55a5b)

    def test_gpio_input_never_becomes_a_configuration_latch(self):
        h=BR28Model(gpio_model=True)
        with self.assertRaises(Blocked):h.read(0xfd5104)
        with self.assertRaises(Blocked):h.write(0xfd5104,1)
        with self.assertRaises(Blocked):h.gpio_rmw(0xfd5104,'or',1)

    def test_partial_words_represent_all_initial_completions(self):
        # Exhaustively compare symbolic narrowing with all8-bit initial values.
        w=PartialWord(0,0xffffff00)
        w.apply('or',0x12);w.apply('and',~0x28)
        results=[(value|0x12)&~0x28 for value in range(256)]
        for result in results:self.assertEqual(result&w.known,w.value)
        known_from_results=0xffffffff
        for result in results:known_from_results&=~(result^results[0])&0xffffffff
        self.assertEqual(w.known,known_from_results)

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
        self.assertFalse(i.eligible(4,0x300));i.ipmask=3
        self.assertTrue(i.eligible(4,0x300))
        for gates in (0,0x100,0x200):self.assertFalse(i.eligible(4,gates))
        with self.assertRaises(Blocked):IRQController().eligible(4,0x300)

    def test_enabled_pending_irq_stops_before_unmodeled_delivery(self):
        h=BR28Model(initial_irq_config=[0]*32,initial_ipmask=0)
        h.write(IRQ_CONFIG,(1|3<<1)<<16)
        for address,value in ((TIMER0,0x4000),(TIMER0+4,0),(TIMER0+8,0),(TIMER0,0x1011)):
            h.write(address,value)
        with self.assertRaisesRegex(Blocked,'interrupt_delivery_not_implemented'):h.advance(0x300)
        self.assertTrue(h.timer.pending)
        h.write(IPMASK,6);h.advance(0x300)

    def test_lock_owner_and_unknowns_are_enforced(self):
        with self.assertRaises(Blocked):BR28Model(bus_lock_model=True).lock(True)
        h=BR28Model(bus_lock_model=True,initial_lock_owner=None)
        h.lock(True);self.assertEqual(h.lock_owner,0)
        with self.assertRaises(Blocked):h.lock(True)
        h.lock(False);self.assertIsNone(h.lock_owner)
        with self.assertRaises(Blocked):h.lock(False)


if __name__=='__main__':unittest.main()
