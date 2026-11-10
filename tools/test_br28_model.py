"""Offline peripheral contract tests; never opens a device or needs private ROM."""
import unittest

from br28_model import BR28Model, CLOCK0, IPMASK, IRQ_CONFIG, IRQController, PINR1, TIMER0, Timer0, PartialWord, MMU, MMU_BASE, P11AccessProbe, PMU_STA
from probe_boot import Blocked
from br28_model import WatchdogProjection, WDT_CON, WDT_KEY
from br28_model import EfuseReadProjection, EFUSE_CON0, EFUSE_CON1, EFUSE_RDAT
from br28_model import GPCNT_CON, CORE_TICK_BASES, CPU1_CONTROL, SYS_DIV


class ModelTests(unittest.TestCase):
    def test_core_tick_overflow_is_terminal_until_external_resolution(self):
        h=BR28Model(core_tick_configuration=True,initial_core_tick_controls=(0,0),core_tick_progress=(1,1))
        base=CORE_TICK_BASES[0]
        h.write(base+4,0);h.write(base+8,1);h.write(base,1)
        h.advance(0)
        with self.assertRaisesRegex(Blocked,'overflow_delivery_unresolved'):h.advance(0)
        self.assertTrue(h.core_ticks[0].pending)
        for action in (lambda:h.advance(0),lambda:h.write(base,0),lambda:h.write(base,65),lambda:h.read(base)):
            with self.assertRaisesRegex(Blocked,'requires_resolution'):action()

    def test_core1_tick_requires_known_execution_and_held_clock_policy(self):
        base=CORE_TICK_BASES[1]
        h=BR28Model(core_tick_configuration=True,initial_core_tick_controls=(0,0),core_tick_progress=(1,1),
                    cpu1_held_projection=True)
        h.write(base+4,0);h.write(base+8,10)
        with self.assertRaisesRegex(Blocked,'execution_state_unknown'):h.write(base,1)
        h.write(CPU1_CONTROL,0)
        with self.assertRaisesRegex(Blocked,'held_clock_unknown'):h.write(base,1)
        for held_clock,expected in ((False,0),(True,1)):
            h=BR28Model(core_tick_configuration=True,initial_core_tick_controls=(0,0),core_tick_progress=(1,1),
                        cpu1_held_projection=True,initial_cpu1_control=0,core_tick_count_while_held=held_clock)
            h.write(base+4,0);h.write(base+8,10);h.write(base,1);h.advance(0)
            self.assertEqual(h.read(base+4),expected)
    def test_gpcnt_cleanup_keeps_unknown_neighbours_and_command_readback(self):
        h=BR28Model(gpcnt_cleanup_projection=True)
        h.gpcnt_rmw(GPCNT_CON,'and',0xfffffffe);h.gpcnt_rmw(GPCNT_CON,'or',64)
        self.assertEqual((h.gpcnt.bits.value,h.gpcnt.bits.known,h.gpcnt.pending),(0,0x81,False))
        self.assertFalse(h.gpcnt.bits.known&64)
        with self.assertRaises(Blocked):h.read(GPCNT_CON)
        with self.assertRaises(Blocked):h.gpcnt_rmw(GPCNT_CON,'or',1)
        self.assertFalse(h.handles(GPCNT_CON+4,4))

    def test_core_tick_initial_state_and_enable_remain_explicit(self):
        h=BR28Model(core_tick_configuration=True)
        with self.assertRaises(Blocked):h.read(CORE_TICK_BASES[0])
        h.write(CORE_TICK_BASES[1],0)
        with self.assertRaises(Blocked):h.read(CORE_TICK_BASES[1])  # pending not inferred from disable
        h=BR28Model(core_tick_configuration=True,initial_core_tick_controls=(0,0))
        self.assertEqual(h.read(CORE_TICK_BASES[0]),0)
        h.write(CORE_TICK_BASES[0]+4,0);h.write(CORE_TICK_BASES[0]+8,123)
        with self.assertRaisesRegex(Blocked,'enable_not_modeled'):h.write(CORE_TICK_BASES[0],1)
        self.assertEqual(h.read(CORE_TICK_BASES[0]+8),123)

    def test_cpu1_held_fixture_does_not_turn_release_into_a_latch(self):
        h=BR28Model(cpu1_held_projection=True)
        with self.assertRaises(Blocked):h.read(CPU1_CONTROL)
        h=BR28Model(cpu1_held_projection=True,initial_cpu1_control=0)
        with self.assertRaisesRegex(Blocked,'cpu1_release_not_implemented'):h.write(CPU1_CONTROL,1)
        self.assertEqual(h.read(CPU1_CONTROL),0)
        self.assertFalse(h.report()['cpu1_held_projection']['secondary_cpu_executed'])

    def test_timer_lsb_source_requires_explicit_progress(self):
        h=BR28Model(timer_lsb_ticks_per_step=2)
        for address,value in ((TIMER0,0x40),(TIMER0+4,0),(TIMER0+8,3),(TIMER0,0x41)):
            h.write(address,value)
        for _ in range(3):h.advance(0)
        self.assertEqual(h.read(TIMER0+4),3);self.assertFalse(h.timer.pending)
        h.advance(0);self.assertTrue(h.timer.pending)
        h=BR28Model()
        for address,value in ((TIMER0,0x40),(TIMER0+4,0),(TIMER0+8,3)):h.write(address,value)
        with self.assertRaisesRegex(Blocked,'clock_source'):h.write(TIMER0,0x41)

    def test_sys_div_fixture_is_not_implicitly_zero(self):
        h=BR28Model(clock_divider_projection=True)
        with self.assertRaises(Blocked):h.read(SYS_DIV)
        h=BR28Model(clock_divider_projection=True,initial_registers={SYS_DIV:0xa5a5a5a5})
        self.assertEqual(h.read(SYS_DIV),0xa5a5a5a5)

    def test_voltage_and_pll1_projection_need_explicit_initial_words(self):
        h=BR28Model(voltage_configuration_projection=True,pll1_configuration_projection=True)
        for address in (0xf2c110,0xfd470c):
            with self.assertRaises(Blocked):h.read(address)
        h.write(0xf2c110,0xee);h.write(0xfd470c,0x1f)
        self.assertEqual(h.read(0xf2c110),0xee);self.assertEqual(h.read(0xfd470c),0x1f)
        self.assertFalse(h.handles(0xf2c104,4))  # no blanket P33 write access

    def test_efuse_fixture_bytes_and_unknown_input(self):
        words=(0x01234567,0x89abcdef,0xdeadbeef,0x13579bdf)
        e=EfuseReadProjection(words,0xffffff00)
        for page,word in enumerate(words):
            e.write(EFUSE_CON0,page<<6)
            got=[]
            for byte in range(4):
                e.write(EFUSE_CON1,0x82|(byte<<2))
                value=e.read(EFUSE_RDAT);got.append(value&255)
                self.assertEqual(value&0xffffff00,0xffffff00)
                e.write(EFUSE_CON1,0)
            self.assertEqual(bytes(got),word.to_bytes(4,'little'))
        self.assertEqual(e.reads,16)
        e=EfuseReadProjection();e.write(EFUSE_CON0,0);e.write(EFUSE_CON1,0x82)
        with self.assertRaisesRegex(Blocked,'unknown_efuse_input'):e.read(EFUSE_RDAT)

    def test_efuse_only_accepts_the_known_read_transaction(self):
        e=EfuseReadProjection((0,0,0,0))
        with self.assertRaises(Blocked):e.read(EFUSE_RDAT)
        with self.assertRaises(Blocked):e.write(EFUSE_RDAT,0)
        with self.assertRaises(Blocked):e.write(EFUSE_CON0,1)
        e.write(EFUSE_CON0,0)
        with self.assertRaises(Blocked):e.write(EFUSE_CON1,0x83)
        with self.assertRaises(Blocked):e.write(EFUSE_CON1,0)
        e.write(EFUSE_CON1,0x82);e.read(EFUSE_RDAT)
        with self.assertRaises(Blocked):e.read(EFUSE_RDAT)

    def test_oscillator_projection_needs_initial_state_and_excludes_status(self):
        h=BR28Model(oscillator_configuration_projection=True)
        with self.assertRaises(Blocked):h.read(0xfd1920)
        h.write(0xfd1940,0x40000);self.assertEqual(h.read(0xfd1940),0x40000)
        with self.assertRaisesRegex(Blocked,'unknown_oscillator_status'):h.read(0xfd1978)
        with self.assertRaisesRegex(Blocked,'readonly'):h.write(0xfd1978,16)
        h=BR28Model(oscillator_configuration_projection=True,oscillator_status_word=0)
        h.write(0xfd1920,0xffffffff)
        self.assertEqual(h.read(0xfd1978),0)  # configuration writes cannot create status

    def wdt_command(self,watchdog,value):
        for key in WatchdogProjection.keys:watchdog.write(WDT_KEY,key)
        watchdog.write(WDT_CON,value)

    def test_watchdog_unknown_control_clock_and_command_order(self):
        w=WatchdogProjection()
        with self.assertRaises(Blocked):w.read(WDT_CON)
        with self.assertRaises(Blocked):w.write(WDT_CON,0)
        with self.assertRaises(Blocked):w.write(WDT_KEY,WatchdogProjection.keys[1])
        self.wdt_command(w,0)
        with self.assertRaisesRegex(Blocked,'clock_unknown'):self.wdt_command(w,0x2e)
        self.assertEqual(w.read(WDT_CON),0)
        with self.assertRaises(Blocked):WatchdogProjection((1,1)).write(WDT_KEY,0)

    def test_watchdog_fractional_deadline_feed_and_no_read_progress(self):
        w=WatchdogProjection((1,2));self.wdt_command(w,0x22)  # four nominal units
        for _ in range(100):self.assertEqual(w.read(WDT_CON),0x22)
        for _ in range(7):w.advance()
        self.assertFalse(w.expired)
        self.wdt_command(w,0x10022)
        self.assertEqual((w.elapsed,w.remainder,w.read(WDT_CON)),(0,0,0x22))
        for _ in range(7):w.advance()
        with self.assertRaisesRegex(Blocked,'watchdog_expired'):w.advance()
        self.assertTrue(w.expired);self.assertEqual(w.feeds,1)
        with self.assertRaisesRegex(Blocked,'requires_reset'):w.advance()
        with self.assertRaisesRegex(Blocked,'requires_reset'):w.write(WDT_KEY,WatchdogProjection.keys[0])
        with self.assertRaisesRegex(Blocked,'requires_reset'):w.read(WDT_CON)

    def test_active_watchdog_period_reconfiguration_is_not_guessed(self):
        w=WatchdogProjection((1,1));self.wdt_command(w,0x22)
        w.advance()
        with self.assertRaisesRegex(Blocked,'active_watchdog_period_change'):self.wdt_command(w,0x23)
        self.assertEqual((w.con,w.elapsed),(0x22,1))

    def test_watchdog_key_scope_and_unsupported_modes(self):
        w=WatchdogProjection((1,1));self.wdt_command(w,0)
        with self.assertRaises(Blocked):w.write(WDT_CON,0)
        for key in WatchdogProjection.keys:w.write(WDT_KEY,key)
        with self.assertRaises(Blocked):w.write(WDT_KEY,WatchdogProjection.keys[0])
        with self.assertRaises(Blocked):w.write(WDT_CON,0x60)
        self.assertFalse(BR28Model().handles(WDT_CON,4))
        h=BR28Model(watchdog_projection=True)
        self.assertFalse(h.handles(WDT_CON,2));self.assertFalse(h.handles(WDT_CON+1,4))

    def test_watchdog_routing_and_stopped_clock_are_explicit(self):
        h=BR28Model(watchdog_projection=True,watchdog_progress=(0,1))
        for value in WatchdogProjection.keys:h.write(WDT_KEY,value)
        h.write(WDT_CON,0x20)
        for _ in range(100):h.advance(0)
        self.assertEqual(h.read(WDT_CON),0x20);self.assertEqual(h.watchdog.elapsed,0)
        self.assertNotIn(WDT_KEY,h.registers)
        with self.assertRaises(Blocked):h.read(WDT_KEY)
        w=WatchdogProjection((1,1))
        with self.assertRaisesRegex(Blocked,'unsupported_watchdog'):self.wdt_command(w,0x32)  # code18 undefined

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
        table=bytes.fromhex('00a601a6')  # physical0x130000,0x130080, absolute tags
        read=lambda a,n:table[a-0x120000:a-0x120000+n]
        self.assertEqual(m.translate(0x400004,4,read),0x130004)
        self.assertEqual(m.translate(0x4000fc,4,read),0x1300fc)
        self.assertEqual(m.translate(0x06000100,6,read),0x06000100)
        with self.assertRaisesRegex(Blocked,'outside_table'):m.translate(0x400100,4,read)
        with self.assertRaisesRegex(Blocked,'invalid_page'):m.translate(0x400000,4,lambda a,n:bytes(n))
        with self.assertRaisesRegex(Blocked,'cross_page'):m.translate(0x40007f,2,read)
        # Actual guest tag observed when OSInit first allocates its tail page.
        m.write(MMU_BASE+8,0x1227ff)
        self.assertEqual(m.translate(0x49fff4,4,lambda a,n:bytes.fromhex('f5b3')),0x19faf4)
        self.assertEqual(m.translate(0x400000,4,lambda a,n:bytes.fromhex('0080')),0)

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
