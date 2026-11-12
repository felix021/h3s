"""Offline peripheral contract tests; never opens a device or needs private ROM."""
import unittest

from br28_model import BR28Model, CLOCK0, IPMASK, IRQ_CONFIG, IRQController, PINR1, TIMER0, Timer0, PartialWord, MMU, MMU_BASE, P11AccessProbe, P11_UART_CON2, PMU_STA, PWR_CON, P11_HEAP_INFO
from probe_boot import Blocked
from br28_model import WatchdogProjection, WDT_CON, WDT_KEY
from br28_model import EfuseReadProjection, EFUSE_CON0, EFUSE_CON1, EFUSE_RDAT
from br28_model import GPCNT_CON, CORE_TICK_BASES, CPU1_CONTROL, SYS_DIV
from br28_model import HardwareCrcEngine, CRC_ENGINE
from br28_model import SfcClockGateProjection, SFC_CLOCK_GATE
from br28_model import SFCReadBus, SFC_BASE, SFC_SECONDARY_BASE
from br28_model import SDTAP_CON, PSRAM_CON0, IOMAP_CON0, USB_IO_CON0, USB_DISABLE_REGISTERS
from br28_model import DebugExecutionProjection, EMU_CON, ResetHistoryProjection, RESET_CONFIG_MASKS
from br28_model import MPUConfigurationProjection, GpioOutValue
from br28_model import IcuInitializationProjection, ICU_BASES
from br28_model import DcuCmoProjection, DCU_CMO_FLUSH, DCU_CMO_FLUSHINV
from br28_model import LpTimerInitRegisters, P11_LPTMR0_CON0, P11_LPTMR0_CON1, P11_LPTMR0_CON2, POWER_INIT_PMU_CFG0, POWER_INIT_PMU_CFG1, LRCT_CON


class ModelTests(unittest.TestCase):
    def test_mpu_gate_toggle_is_explicit_and_unwritten_slots_stay_unknown(self):
        with self.assertRaisesRegex(Blocked,'unknown_initial_mpu_write_gate'):MPUConfigurationProjection().read(0xff03fc)
        m=MPUConfigurationProjection(0)
        with self.assertRaisesRegex(Blocked,'requires_known_open'):m.write(0xff0300,0)
        m.write(0xff03fc,0xe7);self.assertEqual(m.read(0xff03fc),1)
        m.write(0xff0300,0x60607);self.assertEqual(m.read(0xff0300),0x60607)
        with self.assertRaisesRegex(Blocked,'unknown_mpu_slot'):m.read(0xff0304)
        m.write(0xff03fc,0xe7);self.assertEqual(m.read(0xff03fc),0)
        with self.assertRaisesRegex(Blocked,'command_not_modeled'):m.write(0xff03fc,1)

    def debug(self):
        d=DebugExecutionProjection((0,None))
        for i,(low,high) in enumerate(((0x6000100,0x60e31c5),(0x12a640,0x1344c6),(4,0x20000))):
            d.write(0xffe218+8*i,high);d.write(0xffe21c+8*i,low)
        d.write(EMU_CON,0xe000001f)
        return d

    def test_pc_guard_uses_three_ranges_and_independent_cpu_banks(self):
        d=self.debug()
        for pc in (4,0x12a640,0x6000100,0x60e3092):d.check_fetch(pc,6)
        with self.assertRaisesRegex(Blocked,'unknown_initial_pc_guard'):d.check_fetch(0x6000100,2,core=1)
        with self.assertRaisesRegex(Blocked,'unknown_debug_configuration'):d.read(EMU_CON+0x800)
        with self.assertRaisesRegex(Blocked,'virtual_comparison_unverified'):d.check_fetch(0x400000,2)

    def test_pc_guard_unknown_control_ranges_and_terminal_violation(self):
        with self.assertRaisesRegex(Blocked,'unknown_initial_pc_guard'):DebugExecutionProjection().check_fetch(4,2)
        d=DebugExecutionProjection((0,None));d.write(EMU_CON,0xe000001f)
        with self.assertRaisesRegex(Blocked,'range_unknown'):d.check_fetch(4,2)
        d=self.debug()
        with self.assertRaisesRegex(Blocked,'projected_pc_range_violation'):d.check_fetch(0x30000,2)
        for action in (lambda:d.check_fetch(4,2),lambda:d.write(EMU_CON,0),lambda:d.read(EMU_CON)):
            with self.assertRaisesRegex(Blocked,'requires_delivery'):action()

    def test_pc_guard_checks_full_extent_without_truncating_odd_upper_limit(self):
        d=self.debug();d.check_fetch(0x60e31c2,3)
        with self.assertRaisesRegex(Blocked,'projected_pc_range_violation'):d.check_fetch(0x60e31c4,2)
        self.assertEqual(d.registers[0xffe218],0x60e31c5)

    def test_reset_clear_preserves_reason_and_rejects_guessed_flag_readback(self):
        h=ResetHistoryProjection({0xfd0004:0x80,0xfd0000:2})
        h.write(0xfd0004,0xc0)
        self.assertEqual(h.read(0xfd0000),2)
        with self.assertRaisesRegex(Blocked,'post_clear_state_unknown'):h.read(0xfd0004)
        with self.assertRaisesRegex(Blocked,'unknown_reset_or_wake_history'):h.read(0xf2a400)

    def test_reset_configuration_masks_preserve_unknown_neighbours(self):
        h=ResetHistoryProjection()
        for address,mask in RESET_CONFIG_MASKS.items():
            h.rmw(address,'and',mask)
            self.assertEqual(h.configuration[address].known,(~mask)&0xffffffff)
            self.assertEqual(h.configuration[address].value,0)
            with self.assertRaisesRegex(Blocked,'unknown_reset_configuration_bits'):h.read(address)
        with self.assertRaisesRegex(Blocked,'operation_not_modeled'):h.rmw(0xf2a628,'or',16)

    def test_wake_clear_is_a_command_and_retains_unselected_pending_bits(self):
        h=ResetHistoryProjection({0xf2c158:0x123})
        h.write(0xf2c160,1)
        self.assertEqual(h.read(0xf2c158),0x122)
        with self.assertRaisesRegex(Blocked,'readback_not_modeled'):h.read(0xf2c160)
        with self.assertRaisesRegex(Blocked,'mask_not_modeled'):h.write(0xf2c160,0x100)

    def test_rtc_clear_targets_pending_not_the_separate_source_history(self):
        h=ResetHistoryProjection({0xf2d2a0:0x11})
        h.write(0xf2d248,0)  # no-op leaves unknown pending unknown
        with self.assertRaisesRegex(Blocked,'nonzero_mapping_unverified'):h.write(0xf2d248,1)
        h=ResetHistoryProjection({0xf2d2a0:0x11,0xf2d24c:3})
        with self.assertRaisesRegex(Blocked,'nonzero_mapping_unverified'):h.write(0xf2d248,1)
        self.assertEqual(h.read(0xf2d24c),3)
        self.assertEqual(h.read(0xf2d2a0),0x11)

    def test_port_state_inputs_are_explicit_and_snapshot_writes_rejected(self):
        self.assertFalse(BR28Model().handles(SDTAP_CON,4))
        h=BR28Model(port_initialization_projection=True)
        for address in (SDTAP_CON,PSRAM_CON0):
            with self.assertRaisesRegex(Blocked,'unknown_port_input'):h.read(address)
            with self.assertRaisesRegex(Blocked,'snapshot_write_not_modeled'):h.write(address,0)
        with self.assertRaisesRegex(Blocked,'uninitialized_peripheral'):h.read(IOMAP_CON0)
        h=BR28Model(port_initialization_projection=True,
                    port_input_words={SDTAP_CON:0xa5a5a5a4,PSRAM_CON0:2},initial_registers={IOMAP_CON0:0x20000})
        self.assertEqual(h.read(SDTAP_CON),0xa5a5a5a4)
        self.assertEqual(h.read(PSRAM_CON0),2)
        self.assertEqual(h.read(IOMAP_CON0),0x20000)
        with self.assertRaises(ValueError):BR28Model(port_initialization_projection=True,port_input_words={0xfd6018:0})

    def test_port_usb_disable_does_not_supply_controller_status_or_enable(self):
        h=BR28Model(port_initialization_projection=True)
        for address in USB_DISABLE_REGISTERS:
            h.write(address,0)
            with self.assertRaisesRegex(Blocked,'status_not_modeled'):h.read(address)
            with self.assertRaisesRegex(Blocked,'enable_or_configuration_not_modeled'):h.write(address,1)
        self.assertEqual(h.usb_disable_writes,list(USB_DISABLE_REGISTERS))

    def test_usb_io_preserves_unknown_bits_unless_initial_fixture_provided(self):
        h=BR28Model(port_initialization_projection=True,gpio_model=True)
        h.gpio_rmw(USB_IO_CON0,'or',0x5000)
        self.assertEqual(h.gpio[USB_IO_CON0].known,0x5000)
        with self.assertRaisesRegex(Blocked,'uninitialized_gpio_bits'):h.read(USB_IO_CON0)
        h=BR28Model(port_initialization_projection=True,gpio_model=True,initial_usb_io_word=0x40000008)
        h.gpio_rmw(USB_IO_CON0,'or',0x5000)
        self.assertEqual(h.read(USB_IO_CON0),0x40005008)
        with self.assertRaises(ValueError):BR28Model(initial_usb_io_word=0)

    def test_gpio_out_readback_keeps_unknown_neighbours_until_masked_or_consumed(self):
        h=BR28Model(gpio_model=True)
        with self.assertRaisesRegex(Blocked,'uninitialized_gpio_bits'):h.gpio_read(0xfd5300)
        h.gpio_rmw(0xfd5300,'or',0x8)
        word=h.gpio_read(0xfd5300)
        self.assertIsInstance(word,GpioOutValue)
        self.assertEqual((word.value,word.known),(0x8,0x8))
        cleared=word.mask('and',0xfffffff7)
        self.assertEqual((cleared.value,cleared.known),(0x0,0x8))
        set_again=cleared.mask('or',0x8)
        self.assertEqual((set_again.value,set_again.known),(0x8,0x8))
        with self.assertRaisesRegex(Blocked,'gpio_out_readback_consumed'):cleared.concrete()
        h.gpio_write_partial(0xfd5300,cleared)
        self.assertEqual((h.gpio[0xfd5300].value,h.gpio[0xfd5300].known),(0x0,0x8))
        h.write(0xfd5300,0x8)
        self.assertEqual(h.gpio_read(0xfd5300),0x8)

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

    def test_cpu1_control_rmw_records_kick_bit_and_keeps_release_strict(self):
        h=BR28Model(cpu1_held_projection=True,initial_cpu1_control=0)
        h.cpu1_release_handler=lambda value:None
        h.cpu1_control_rmw(CPU1_CONTROL,'or',1)  # 实际释放指令就是 |=1(0x60660da)
        self.assertEqual(h.read(CPU1_CONTROL),1)
        with self.assertRaisesRegex(Blocked,'unsupported_cpu1_release_transition'):
            h.cpu1_control_rmw(CPU1_CONTROL,'or',9)  # 释放后含 bit0 的 RMW 仍停
        h.cpu1_control_rmw(CPU1_CONTROL,'or',8)  # kick bit3(0x12b732 轮询循环)
        self.assertEqual(h.read(CPU1_CONTROL),9)
        with self.assertRaisesRegex(Blocked,'cpu1_control_rmw_form_not_modeled'):
            h.cpu1_control_rmw(CPU1_CONTROL,'xor',1)
        report=h.report()
        self.assertEqual((report['cpu1_held_projection']['current_control'],
                          report['cpu1_held_projection']['kick_rmw_writes']),(9,1))
        self.assertIn('cpu1_control_kick_bit3',report['assumptions'])

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

    def test_p11_ready_and_watchdog_projections_stack_on_one_model(self):
        # p11_ready_watchdog 场景的模型层形状:两个独立 opt-in 叠加,互不制造对方的效应。
        # 双 KEY 对里的第一个 KEY 正是 83,094 严格停点写下的字(0x048c159d,wdt.c.o 钉死)。
        def ready_watchdog_model(progress):
            return BR28Model(p11_access_probe=True,p11_status=0,p11_pmu_ready_projection=True,
                             watchdog_projection=True,watchdog_progress=progress)
        strict=ready_watchdog_model(None)
        for key in WatchdogProjection.keys:strict.write(WDT_KEY,key)
        with self.assertRaisesRegex(Blocked,'clock_unknown'):strict.write(WDT_CON,0x2e)  # 漏 progress 仍严格
        h=ready_watchdog_model((0,1))
        for value in P11AccessProbe.sequence:h.write(P11_UART_CON2,value)
        self.assertEqual(h.read(PMU_STA),1)  # 基线0 + 条件 bit0=1,与全字 status=1 场景同读数
        for key in (0x048c159d,0x26ae37bf):h.write(WDT_KEY,key)  # app 0x600a57c/0x600a582 观测形状
        h.write(WDT_CON,0x2e)
        for _ in range(100):h.advance(0)
        self.assertEqual((h.read(WDT_CON),h.watchdog.elapsed),(0x2e,0))
        self.assertEqual(h.p11.sequences,1)  # watchdog 命令不干扰 P11 序列状态

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

    def test_pmu_sta_ready_projection_is_separate_from_the_fixed_fixture(self):
        # 独立 opt-in:需要访问探针和显式基线字;不制造其他位
        with self.assertRaises(ValueError):P11AccessProbe(None,ready_response=True)
        with self.assertRaises(ValueError):BR28Model(p11_pmu_ready_projection=True)
        with self.assertRaises(ValueError):BR28Model(p11_access_probe=True,p11_status=None,
                                                     p11_pmu_ready_projection=True)
        probe=P11AccessProbe(0,ready_response=True)
        with self.assertRaises(Blocked):probe.read()  # 序列未完成前没有响应
        for value in P11AccessProbe.sequence:probe.write(value)
        self.assertEqual(probe.read(),1)  # 基线0 + 条件 bit0=1
        legacy=P11AccessProbe(0)
        for value in P11AccessProbe.sequence:legacy.write(value)
        self.assertEqual(legacy.read(),0)  # 投影关闭:读回仍是不变的 fixture
        self.assertIn('pmu_ready_response_projection',probe.report())
        self.assertNotIn('pmu_ready_response_projection',P11AccessProbe(1).report())
        labels=probe.report()['pmu_ready_response_projection']
        self.assertFalse(labels['bit0_set_mechanism_proven'])
        self.assertFalse(labels['causal_link_to_pwr_con_bit5_assumed'])

    def test_pwr_con_bit5_response_requires_the_real_request_and_keeps_neighbour_bits(self):
        # 未启用投影:|=0x20 的写回仍走严格复位标志路径
        strict=BR28Model(reset_history_projection=True,reset_history_words={PWR_CON:0x80})
        with self.assertRaisesRegex(Blocked,'reset_flag_command_not_modeled'):strict.write(PWR_CON,0xa0)
        self.assertEqual(strict.read(PWR_CON),0x80)
        with self.assertRaises(ValueError):BR28Model(p11_lpwr_response_projection=True)  # 需要复位快照
        h=BR28Model(reset_history_projection=True,reset_history_words={PWR_CON:0x80},
                    p11_lpwr_response_projection=True)
        # 请求之前读回不臆造:POR bit7 保留,bit5 维持快照值
        self.assertEqual(h.read(PWR_CON),0x80)
        # 只有已证实的 |=0x20 RMW 形式被接受;夹带其他位或 and 形式仍严格拒绝
        with self.assertRaisesRegex(Blocked,'p11_lpwr_request_form_not_modeled'):
            h.p11_lpwr_rmw(PWR_CON,'rmw.or.modimm',0x60)
        with self.assertRaisesRegex(Blocked,'p11_lpwr_request_form_not_modeled'):
            h.p11_lpwr_rmw(PWR_CON,'rmw.and.nmodimm',0x20)
        with self.assertRaisesRegex(Blocked,'p11_lpwr_request_form_not_modeled'):
            h.p11_lpwr_rmw(PWR_CON,'rmw.or.modimm',None)
        self.assertEqual(h.read(PWR_CON),0x80)
        # 真实请求之后:bit5 读回投影为 0(显式假设 P11 已处理),bit7 不变
        h.p11_lpwr_rmw(PWR_CON,'rmw.or.modimm',0x20)
        self.assertEqual(h.read(PWR_CON),0x80)
        self.assertEqual(h.p11_lp.requests,1)
        # 快照本身未被改写:快照若含 bit5,只有请求之后才被投影清零
        preset=BR28Model(reset_history_projection=True,reset_history_words={PWR_CON:0xa0},
                         p11_lpwr_response_projection=True)
        self.assertEqual(preset.read(PWR_CON),0xa0)
        preset.p11_lpwr_rmw(PWR_CON,'rmw.or.modimm',0x20)
        self.assertEqual(preset.read(PWR_CON),0x80)
        # 投影不给 bit6 清标志命令开后门:清标志之后、无请求时读回仍是未知
        # (真实前缀里主 init 清标志先于 power_init 请求,两个条件都满足才走
        # post_flag_clear_read 的轮询读回——见 test_pwr_con_post_clear_poll_readback)
        preset.write(PWR_CON,0xe0)  # 快照0xa0的 bit6 清标志命令
        preset.p11_lp.requests=0    # 反事实:清标志后从未观察到请求
        with self.assertRaisesRegex(Blocked,'reset_flag_post_clear_state_unknown'):preset.read(PWR_CON)
        self.assertEqual(h.report()['p11_low_power_response']['requests_observed'],1)

    def test_pwr_con_post_clear_poll_readback_needs_request_and_only_clears_flag_bits(self):
        # 真实前缀顺序(反汇编钉死):主 init 复位原因处理 0x6068ce4..e8 读
        # PWR_CON bit7→0x6068cf2 |=0x40 清标志;app_core power_init 0x60679a0
        # |=0x20 请求后 0x60679ae 死等 bit5。lpwr 投影开启且已观察到请求时,
        # 清标志后的轮询读回=快照&~bit7(清标志配对推定)&~bit5(P11 响应);
        # 其余读者/寄存器保持严格停。
        h=BR28Model(reset_history_projection=True,reset_history_words={PWR_CON:0x80},
                    p11_lpwr_response_projection=True)
        h.write(PWR_CON,0xc0)  # 快照0x80的 bit6 清标志命令(主 init 0x6068cf2)
        with self.assertRaisesRegex(Blocked,'reset_flag_post_clear_state_unknown'):h.read(PWR_CON)
        h.p11_lpwr_rmw(PWR_CON,'rmw.or.modimm',0x20)
        self.assertEqual(h.read(PWR_CON),0x00)   # bit7 配对清零、bit5 响应清零
        self.assertEqual(h.counts['p11_lpwr_post_clear_poll_reads'],1)
        # 快照含 bit5 时同样投影清零;其他 RESET_FLAGS 成员不受影响
        preset=BR28Model(reset_history_projection=True,reset_history_words={PWR_CON:0xa0,0xfd0004:0x80},
                         p11_lpwr_response_projection=True)
        preset.write(PWR_CON,0xe0);preset.write(0xfd0004,0xc0)
        preset.p11_lpwr_rmw(PWR_CON,'rmw.or.modimm',0x20)
        self.assertEqual(preset.read(PWR_CON),0x00)
        with self.assertRaisesRegex(Blocked,'reset_flag_post_clear_state_unknown'):preset.read(0xfd0004)
        labels=preset.report()['p11_low_power_response']
        self.assertIn('post_flag_clear_readback',labels)

    def test_reset_extra_configuration_masks_are_opt_in(self):
        # 0xf2c05c(power_init 0x60678f4 唯一引用)按 opt-in 进配置表;默认关,
        # legacy 报告与默认三地址表不变。
        self.assertNotIn(0xf2c05c,ResetHistoryProjection().configuration)
        with self.assertRaises(ValueError):
            ResetHistoryProjection(extra_configuration_masks={0xf2c054:1})  # 与默认表重叠
        with self.assertRaises(ValueError):
            ResetHistoryProjection(extra_configuration_masks={0xf2c05c:1<<32})  # 非法掩码
        with self.assertRaises(ValueError):
            BR28Model(power_init_config_rmw=True)  # 需要 reset 投影
        base=BR28Model(reset_history_projection=True,reset_history_words={PWR_CON:0x80})
        self.assertNotIn(0xf2c05c,base.reset.configuration)
        h=BR28Model(reset_history_projection=True,reset_history_words={PWR_CON:0x80},
                    power_init_config_rmw=True)
        h.reset.rmw(0xf2c05c,'and',0x3fd)
        word=h.reset.configuration[0xf2c05c]
        self.assertEqual(word.known,0xfffffc02)  # 清 bit1 与 bits10+ → 已知 0
        self.assertEqual(word.value,0)
        with self.assertRaisesRegex(Blocked,'unknown_reset_configuration_bits'):h.reset.read(0xf2c05c)
        with self.assertRaisesRegex(Blocked,'operation_not_modeled'):h.reset.rmw(0xf2c05c,'and',0x3fd-1)
        self.assertNotIn('0xf2c05c',base.reset.report()['configuration'])
        self.assertEqual(h.reset.report()['configuration']['0xf2c05c'],
                         {'known_mask':'0xfffffc02','known_value':'0x0'})

    def test_p11_window_registers_are_write_only_and_shape_checked(self):
        # q32s PMU_CON 0xf2a0c0:唯一观测形状 |=1(0x60679a8),write-only 位记录;
        # SYS_DIV/CLK_CON0 整字写记录;全部读回严格拒绝;默认关时不 handles。
        h=BR28Model(p11_window_projection=True)
        self.assertTrue(h.handles(0xf2a0c0,4) and h.handles(0xf2a410,4) and h.handles(0xf2a414,4))
        h.p11_window_rmw(0xf2a0c0,'or',1)
        self.assertEqual(h.p11_win.control.known,1)  # 只 bit0 已知
        with self.assertRaisesRegex(Blocked,'form_not_modeled'):h.p11_window_rmw(0xf2a0c0,'or',2)
        with self.assertRaisesRegex(Blocked,'form_not_modeled'):h.p11_window_rmw(0xf2a0c0,'and',1)
        h.write(0xf2a410,0);h.write(0xf2a414,1)
        with self.assertRaisesRegex(Blocked,'readback_not_modeled'):h.read(0xf2a410)
        with self.assertRaisesRegex(Blocked,'readback_not_modeled'):h.read(0xf2a0c0)
        with self.assertRaisesRegex(Blocked,'full_store_not_modeled'):h.write(0xf2a0c0,1)
        report=h.report()['p11_window_projection']
        self.assertEqual(report['q32s_pm_con']['rmw_writes'],1)
        self.assertEqual(report['stores'],{'0xf2a410':{'values':['0x0'],'writes':1},
                                           '0xf2a414':{'values':['0x1'],'writes':1}})
        self.assertTrue(report['write_only'] and not report['readback_observed'])
        self.assertFalse(report['causal_link_to_pwr_con_bit5_response_assumed'])
        self.assertFalse(BR28Model().handles(0xf2a0c0,4))  # 默认关

    def test_lp_timer_init_registers_are_write_only_bit_records(self):
        # power_init 尾部低功耗定时器链(独立开关 lp_timer_init_projection,默认关):
        # P11_LPTMR0 CON0/1/2 与 PMU 配置族 0xf2c04c/0xf2c050 整字写记值+次数;
        # JL_LRCT CON 0xfd3600 只按位记录 RMW(and 清位/或置位不经读回,PartialWord
        # 与 gpio RMW/q32s PMU_CON 同语义);全部读回严格拒绝(0x6067514 的 LRC
        # trim 使能/busy 轮询是诚实停止点);0xfd3600 整字 store 无观测形状拒绝。
        h=BR28Model(lp_timer_init_projection=True)
        for address in (P11_LPTMR0_CON0,P11_LPTMR0_CON1,P11_LPTMR0_CON2,
                        POWER_INIT_PMU_CFG0,POWER_INIT_PMU_CFG1,LRCT_CON):
            self.assertTrue(h.handles(address,4))
        self.assertFalse(BR28Model().handles(P11_LPTMR0_CON0,4))  # 默认关
        self.assertFalse(BR28Model().handles(LRCT_CON,4))
        h.write(P11_LPTMR0_CON0,0x5c);h.write(P11_LPTMR0_CON1,0x53);h.write(P11_LPTMR0_CON2,0)
        h.write(POWER_INIT_PMU_CFG0,0x21);h.write(POWER_INIT_PMU_CFG1,0x41)
        h.lp_timer_rmw(LRCT_CON,'and',0xffffffc1)  # 清 bits0-5 → 已知 0
        h.lp_timer_rmw(LRCT_CON,'or',0x40)         # 置 bit6
        h.lp_timer_rmw(LRCT_CON,'or',0x1)          # 置 bit0
        word=h.lp_timer.lrct
        self.assertEqual((word.known,word.value),(0x7f,0x41))
        with self.assertRaisesRegex(Blocked,'readback_not_modeled'):h.read(P11_LPTMR0_CON0)
        with self.assertRaisesRegex(Blocked,'readback_not_modeled'):h.read(LRCT_CON)
        with self.assertRaisesRegex(Blocked,'full_store_not_modeled'):h.write(LRCT_CON,1)
        report=h.report()['lp_timer_init_projection']
        self.assertEqual(report['stores'],{'0xf2a900':{'values':['0x5c'],'writes':1},
                                           '0xf2a904':{'values':['0x53'],'writes':1},
                                           '0xf2a908':{'values':['0x0'],'writes':1},
                                           '0xf2c04c':{'values':['0x21'],'writes':1},
                                           '0xf2c050':{'values':['0x41'],'writes':1}})
        self.assertEqual(report['lrct_con']['rmw_writes'],3)
        self.assertTrue(report['write_only'] and not report['readback_observed'])
        self.assertFalse(report['bit_semantics_or_completion_verified'])
        self.assertFalse(report['lrc_trim_result_simulated'])
        self.assertNotIn('lp_timer_init_projection',BR28Model().report())
        # 未观测的 RMW 形式(xor 等)与未建模地址拒绝
        with self.assertRaisesRegex(Blocked,'form_not_modeled'):LpTimerInitRegisters().rmw(LRCT_CON,'xor',1)
        with self.assertRaises(ValueError):LpTimerInitRegisters().rmw(0x1234,'and',1)
        with self.assertRaises(ValueError):LpTimerInitRegisters().write(0x1234,0)

    def test_p11_heap_info_fixture_is_explicit_or_strictly_unknown(self):
        with self.assertRaises(ValueError):BR28Model(p11_heap_info_bytes=b'\x01\x02')
        self.assertEqual(BR28Model().read_bytes(P11_HEAP_INFO,1),None)  # 无 fixture:严格 unknown
        self.assertEqual(BR28Model().read_bytes(P11_HEAP_INFO,4),None)
        h=BR28Model(p11_heap_info_bytes=bytes.fromhex('01020304'))
        self.assertEqual(h.read_bytes(P11_HEAP_INFO,1),b'\x01')
        self.assertEqual(h.read_bytes(P11_HEAP_INFO+1,2),b'\x02\x03')
        self.assertEqual(h.read_bytes(P11_HEAP_INFO,4),bytes.fromhex('01020304'))
        for outside in (P11_HEAP_INFO-1,P11_HEAP_INFO+4):
            self.assertEqual(h.read_bytes(outside,1),None)  # 范围之外不臆造
        self.assertEqual(h.report()['p11_heap_info_fixture']['bytes'],'01020304')

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

    def test_hardware_crc_engine_matches_the_app_software_table(self):
        # app 0x060a860c 的 16 项半字节表(直接从 app 字节提取,entry[i]=i*0x1021):
        # 多项式 0x1021、MSB-first 左移、初值=种子、无输出异或。ROM 0xaa1e 与
        # app 0x6000f2c 的访问形状一致:写+4 种子、写+0 馈字节、csync 后读+4。
        APP_TABLE=(0x0000,0x1021,0x2042,0x3063,0x4084,0x50a5,0x60c6,0x70e7,
                   0x8108,0x9129,0xa14a,0xb16b,0xc18c,0xd1ad,0xe1ce,0xf1ef)
        def software_table_crc(seed,data):
            crc=seed
            for byte in data:
                for nibble in (byte>>4,byte&15):
                    crc=((crc<<4)&0xffff)^APP_TABLE[(crc>>12)^nibble]
            return crc
        def engine_crc(seed,data):
            engine=HardwareCrcEngine()
            engine.write(CRC_ENGINE+4,seed)
            for byte in data:engine.write(CRC_ENGINE,byte)
            return engine.read(CRC_ENGINE+4)
        self.assertEqual(engine_crc(0,b'123456789'),0x31c3)  # CRC-16/XMODEM check value
        rom_shape=b'\xff\xff'+bytes.fromhex('c3cfc0e8ced2b0aec4e3a3acd3f1c1')
        for seed,data in ((0,b'123456789'),(0xffff,rom_shape),(0xb00b,b'\x00'),
                          (0x1234,bytes(range(64))),(0xd97f,b'')):
            with self.subTest(seed=hex(seed),length=len(data)):
                self.assertEqual(engine_crc(seed,data),software_table_crc(seed,data))

    def test_hardware_crc_keeps_reset_unknown_and_observed_shapes_only(self):
        engine=HardwareCrcEngine()
        with self.assertRaisesRegex(Blocked,'unknown_crc_engine_reset_state'):engine.read(CRC_ENGINE+4)
        with self.assertRaisesRegex(Blocked,'crc_feed_before_seed'):engine.write(CRC_ENGINE,0)
        engine.write(CRC_ENGINE+4,0xffff)
        with self.assertRaisesRegex(Blocked,'crc_seed_width'):engine.write(CRC_ENGINE+4,0x10000)
        with self.assertRaisesRegex(Blocked,'crc_feed_width'):engine.write(CRC_ENGINE,0x100)
        self.assertEqual(engine.read(CRC_ENGINE+4),0xffff)  # seed reads back before any feed
        with self.assertRaisesRegex(Blocked,'crc_data_port_read'):engine.read(CRC_ENGINE)
        engine.write(CRC_ENGINE,0)
        with self.assertRaisesRegex(Blocked,'crc_register_not_modeled'):engine.write(CRC_ENGINE+8,0)

    def test_hardware_crc_is_opt_in_and_routed(self):
        with self.assertRaisesRegex(Blocked,'unsupported_peripheral_write'):BR28Model().write(CRC_ENGINE+4,0)
        self.assertFalse(BR28Model().handles(CRC_ENGINE+4,4))
        model=BR28Model(hardware_crc_projection=True)
        model.write(CRC_ENGINE+4,0);model.write(CRC_ENGINE,0x31)
        self.assertEqual(model.read(CRC_ENGINE+4),0x2672)
        report=model.report()['hardware_crc_projection']
        self.assertEqual(report['seed_writes'],['0x0'])
        self.assertEqual(report['fed_bytes'],1)
        self.assertEqual(report['result_reads'],['0x2672'])
        self.assertFalse(report['reset_value_measured'])
        self.assertFalse(report['hardware_bit_or_byte_order_silicon_verified'])

    def test_sfc_clock_select_is_write_only_and_never_fabricates_readback(self):
        # 0xfd1c04(+4) 两侧证据都是整字写:app 0x6010b88 `st.w [r0+4]=3`
        # (资源表扫描后一次性重配)、ROM 0xc198 `st.w [r5+4]=uextra(cfg,8,8)`
        # (flash 配置字 bits[8:16])。固件从不读回——未写不臆造复位值,
        # 写过之后读回同样没有证据,一律诚实停。
        gate=SfcClockGateProjection()
        with self.assertRaisesRegex(Blocked,'sfc_clock_select_readback_not_observed'):gate.read(SFC_CLOCK_GATE+4)
        gate.write_select(3)
        with self.assertRaisesRegex(Blocked,'sfc_clock_select_readback_not_observed'):gate.read(SFC_CLOCK_GATE+4)
        report=gate.report()
        self.assertEqual(report['select'],'0x3')
        self.assertEqual(report['select_writes'],1)
        self.assertTrue(report['select_write_only'])
        self.assertFalse(report['select_readback_observed'])
        self.assertFalse(report['select_bit_semantics_verified'])

    def test_sfc_clock_select_is_opt_in_and_routed(self):
        # 默认关:0xfd1c04 整字写仍是不支持的外设写;打开既有的
        # sfc_clock_gate_projection(0xfd1c00 块)后,写走只写记录、读继续停。
        model=BR28Model()
        self.assertFalse(model.handles(SFC_CLOCK_GATE+4,4))
        with self.assertRaisesRegex(Blocked,'unsupported_peripheral_write'):model.write(SFC_CLOCK_GATE+4,3)
        model=BR28Model(sfc_clock_gate_projection=True)
        model.write(SFC_CLOCK_GATE+4,3)
        with self.assertRaisesRegex(Blocked,'sfc_clock_select_readback_not_observed'):model.read(SFC_CLOCK_GATE+4)
        report=model.report()['sfc_clock_gate_projection']
        self.assertEqual((report['select'],report['select_writes']),('0x3',1))
        self.assertEqual(report['select_address'],'0xfd1c04')

    def test_sfc_window_registers_take_only_the_observed_shapes(self):
        # VM flash 窗口组(+0x18/+0x20/+0x24)挂在 sfc_read_bus 下。全部证据只有
        # app 两个引用点的写:0x600a36e..0x600a37e(r1=0xfe0218:[+12]=0x061d7fe0、
        # [+8]=0x7fffffff、[0]|=2)与 SFC 上下文恢复尾 0x60c5bfa..0x60c5c0a(r1=
        # 0xfe0220:[+4]=call 结果、[0]=0x7fffffff,不重写控制位);ROM 零引用,SDK
        # 头文件无名。因此:+0x20/+0x24 整字写只记录,+0x18 只接受 or 置位(不经
        # 读回、按位记录),读回与形状外的写一律诚实停。
        bus=SFCReadBus(1)
        bus.write(SFC_BASE+0x24,0x061d7fe0)
        bus.write(SFC_BASE+0x20,0x7ffffff)
        bus.window_rmw(2)
        for address in (SFC_BASE+0x18,SFC_BASE+0x20,SFC_BASE+0x24):
            with self.assertRaisesRegex(Blocked,'sfc_window_readback_not_observed'):bus.read(address)
        with self.assertRaisesRegex(Blocked,'sfc_window_control_full_store_not_modeled'):
            bus.write(SFC_BASE+0x18,3)
        window=bus.report()['window_registers']
        self.assertEqual((window['base'],window['base_writes']),('0x61d7fe0',1))
        self.assertEqual((window['limit'],window['limit_writes']),('0x7ffffff',1))
        self.assertEqual(window['control_operations'],[{'kind':'or','operand':'0x2'}])
        self.assertEqual((window['control_known_value'],window['control_known_mask']),('0x2','0x2'))
        self.assertTrue(window['write_only'] and not window['readback_observed'])
        self.assertFalse(window['bit_semantics_verified'])
        self.assertIn('flash_access_gate_unchanged',window)

    def test_sfc_window_group_routes_through_both_buses_and_stays_off_by_default(self):
        # 默认(BR28Model() 不开 sfc_read_bus)窗口组仍是不支持的外设写;打开后
        # 主/副 SFC 共用同一形状门,副 SFC 独立记录(实际固件从不写它)。
        model=BR28Model()
        self.assertFalse(model.handles(SFC_BASE+0x24,4))
        with self.assertRaisesRegex(Blocked,'unsupported_peripheral_write'):model.write(SFC_BASE+0x24,1)
        model=BR28Model(sfc_read_bus=True,initial_sfc_control=1,
                        secondary_sfc_read_bus=True,initial_secondary_sfc_control=0)
        model.write(SFC_BASE+0x24,0x061d7fe0);model.write(SFC_BASE+0x20,0x7ffffff)
        model.sfc_window_rmw(SFC_BASE+0x18,2)
        model.write(SFC_SECONDARY_BASE+0x24,0x061d7fe0)
        with self.assertRaisesRegex(Blocked,'sfc_window_readback_not_observed'):
            model.read(SFC_BASE+0x20)
        primary=model.report()['sfc_read_bus']['window_registers']
        secondary=model.report()['sfc_read_bus_secondary']['window_registers']
        self.assertEqual((primary['base'],primary['limit']),('0x61d7fe0','0x7ffffff'))
        self.assertEqual(secondary['base'],'0x61d7fe0')
        self.assertEqual(model.report()['sfc_read_bus']['writes'],2)  # 两次整字写;RMW 不计入
        # 窗口 RMW 不经过读回:已知位只含置位过的 bit1,整字消费仍严格停
        with self.assertRaisesRegex(Blocked,'sfc_window_readback_not_observed'):
            model.read(SFC_BASE+0x18)

    def test_icu_region_registers_take_only_the_observed_shapes(self):
        # 两核 ICU region 组(0xffe400/0xffec00 的 +0x14/+0x18/+0x10)。证据形状:
        # ROM export sfc_drop_cache(0x1fdcc→0xc726,SDK cache.h (beg,len) 签名族)
        # 每核 cli→testset 0x19fd78 抢锁→[+0x14]=begin、[+0x18]=end-1(实测
        # 0x061d7fe0/0x061d7fff,32 字节窗口)→[+0x10]=0xca000040(0xc72c 按
        # len>=0x1000 在 0xc730/0xc736 两条谓词 li 里二选一,实测 len=0x20 选
        # 0x40)→call 0xbac0 轮询 CON bit30(与 0xca000010 使能路径 0xbae0 同一
        # 条 ld.w.idxs@0xbaca)→call 0xbad4 释放锁。数据寄存器固件只写不读
        # (该序列与 app 全量扫描均无读回)——write-only 记录,读回一律停。
        proj=IcuInitializationProjection({0xffe400:0x80000000,0xffec00:0x80000000},4,region_projection=True)
        proj.write(0xffe414,0x061d7fe0);proj.write(0xffe418,0x061d7fff)
        proj.write(0xffe410,0xca000040)
        self.assertEqual(proj.read(0xffe400)&0x40000000,0x40000000)  # CON bit30 busy
        for _ in range(4):proj.advance()
        self.assertEqual(proj.read(0xffe400),0x80000000)             # 完成后回到 fixture
        with self.assertRaisesRegex(Blocked,'icu_region_readback_not_modeled'):proj.read(0xffe414)
        with self.assertRaisesRegex(Blocked,'icu_region_readback_not_modeled'):proj.read(0xffec18)
        with self.assertRaisesRegex(Blocked,'icu_command_not_modeled'):proj.write(0xffe410,0xca000030)
        with self.assertRaisesRegex(Blocked,'icu_command_not_modeled'):proj.write(0xffe410,0xca000010|0xf0)
        report=proj.report()['region_projection']
        self.assertEqual(report['registers']['0xffe400'],
                         {'0x14':{'value':'0x61d7fe0','writes':1},'0x18':{'value':'0x61d7fff','writes':1}})
        self.assertEqual(report['registers']['0xffec00'],
                         {'0x14':{'value':None,'writes':0},'0x18':{'value':None,'writes':0}})
        self.assertTrue(report['write_only'] and not report['readback_observed'])
        self.assertFalse(report['command_low_byte_semantics_verified'])
        self.assertTrue(report['same_busy_machinery_as_command4'])
        self.assertEqual(proj.report()['commands'],[{'address':'0xffe410','value':'0xca000040'}])

    def test_icu_region_projection_is_opt_in_and_routed(self):
        # 默认关:不开 icu_initialization_projection 时 region 写是不支持的外设写;
        # 只开 ICU 初始化投影时 +0x14/+0x18 仍不接管、region 命令字仍拒绝;
        # 两个开关都开才走 write-only 记录 + 共享完成投影(必须挂初始化投影,
        # 单独开 region 直接报参数错误)。
        model=BR28Model()
        self.assertFalse(model.handles(0xffe414,4))
        with self.assertRaisesRegex(Blocked,'unsupported_peripheral_write'):model.write(0xffe414,1)
        model=BR28Model(icu_initialization_projection=True,icu_command_steps=4)
        self.assertFalse(model.handles(0xffe414,4))
        with self.assertRaisesRegex(Blocked,'unsupported_peripheral_write'):model.write(0xffe414,1)
        with self.assertRaisesRegex(Blocked,'icu_command_not_modeled'):model.write(0xffe410,0xca000040)
        with self.assertRaises(ValueError):BR28Model(icu_region_projection=True)
        model=BR28Model(cache_status_probe=True,
                        cache_status_words={base:0x80000000 for base in ICU_BASES},
                        icu_initialization_projection=True,icu_command_steps=4,icu_region_projection=True)
        for base in ICU_BASES:  # 两核独立路由:CPU0/CPU1 的 ICU 各自记录
            model.write(base+0x14,0x061d7fe0);model.write(base+0x18,0x061d7fff)
            model.write(base+0x10,0xca000040)
            with self.assertRaisesRegex(Blocked,'icu_region_readback_not_modeled'):model.read(base+0x18)
        icu=model.report()['icu_initialization_projection']
        self.assertEqual([c['address'] for c in icu['commands']],['0xffe410','0xffec10'])
        self.assertEqual(icu['completed_commands'],0)  # 完成仍由显式 advance 步数驱动
        self.assertEqual(icu['pending_cores'],[0,1])

    def test_dcu_cmo_ports_take_only_write_only_line_records(self):
        # SDK csfr.h 把 JL_L1P(0xff0500,共享 L1P cache)类型化为
        # JL_TypeDef_q32DSP_DCU,CMO[32](+0x80..+0xff)标注 __WO;0xff05c8/
        # 0xff05cc=CMO[18]/CMO[19],逐 index 操作语义 SDK 未命名,由 ROM export
        # wrapper 区分(flush_dcache 0x1fdac→0xc780 用 r2=0xff05c8、
        # flushinv_dcache 0x1fdb0→0xc78c→0xc71a 用 r2=0xff05cc,都调同一循环
        # 0xc6f4:寄存器计数 rep,体=add.rrr.p(r0+=32)+st.w [r2]=旧 r0 并行对,
        # 每行写一次口,存储半按硬件 run3 证据读 A 之前的旧寄存器)。实测
        # flushinv_dcache(beg=0x061d7fe0,len=0x20)一行,首写=行地址 0x061d7fe0。
        # 固件全量字节扫描:两个口各只有 wrapper 一处 li.u32 引用,无任何读回
        # ——write-only 序列记录,读回一律停;长序列按首 32+末 32 截断。
        proj=DcuCmoProjection()
        self.assertTrue(proj.handles(DCU_CMO_FLUSHINV) and proj.handles(DCU_CMO_FLUSH))
        self.assertFalse(proj.handles(0xff05d0))
        proj.write(DCU_CMO_FLUSHINV,0x061d7fe0)
        proj.write(DCU_CMO_FLUSH,0x061d7fe0);proj.write(DCU_CMO_FLUSH,0x061d8000)
        with self.assertRaisesRegex(Blocked,'dcu_cmo_readback_not_modeled'):proj.read(DCU_CMO_FLUSHINV)
        with self.assertRaisesRegex(Blocked,'dcu_cmo_readback_not_modeled'):proj.read(DCU_CMO_FLUSH)
        for _ in range(70):proj.write(DCU_CMO_FLUSHINV,0x061d8000)
        report=proj.report()
        self.assertEqual(report['ports']['0xff05c8'],{'values':['0x61d7fe0','0x61d8000'],'writes':2})
        self.assertEqual(len(report['ports']['0xff05cc']['values']),64)  # 首 1+末 63…截断保序
        self.assertEqual(report['ports']['0xff05cc']['values'][0],'0x61d7fe0')
        self.assertEqual(report['ports']['0xff05cc']['writes'],71)
        self.assertTrue(report['values_truncated'])
        self.assertTrue(report['write_only'] and not report['readback_observed'])
        self.assertFalse(report['port_index_semantics_verified'])
        self.assertFalse(report['cache_contents_or_coherence_simulated'])

    def test_dcu_cmo_projection_is_opt_in_and_routed(self):
        # 默认关:不开 dcu_cmo_projection 时 0xff05cc/0xff05c8 写是不支持的外设写;
        # 打开后经 BR28Model 路由 write-only 记录(独立开关,不依赖 ICU/CON 机制)。
        model=BR28Model()
        self.assertFalse(model.handles(0xff05cc,4) or model.handles(0xff05c8,4))
        with self.assertRaisesRegex(Blocked,'unsupported_peripheral_write'):model.write(0xff05cc,1)
        with self.assertRaisesRegex(Blocked,'unsupported_peripheral_write'):model.write(0xff05c8,1)
        model=BR28Model(dcu_cmo_projection=True)
        self.assertTrue(model.handles(0xff05cc,4) and model.handles(0xff05c8,4))
        model.write(0xff05cc,0x061d7fe0)
        with self.assertRaisesRegex(Blocked,'dcu_cmo_readback_not_modeled'):model.read(0xff05cc)
        dcu=model.report()['dcu_cmo_projection']
        self.assertEqual(dcu['ports']['0xff05cc'],{'values':['0x61d7fe0'],'writes':1})
        self.assertEqual(dcu['ports']['0xff05c8'],{'values':[],'writes':0})

    def test_cpu1_release_bit_survives_kick_bit_record(self):
        # kick 位(bit3,只写记录)把 cpu1_control 记到 9;CPU1 的交付资格判定必须
        # 看 bit0 释放位,否则 kick 会产生「负调度效应」——第二次 pend(窗口组建模
        # 后 162,646 前沿真实发生:CPU0 再 pend irq125 到 CPU1)被静默吞掉,CPU1 永不
        # 醒、CPU0 在 0x12b66a 死等。bit0 语义是既有 kick 投影声明的一部分。
        config=[0]*32;config[15]=0xd00000  # irq125:word15 bit20,enable+priority6(unmaskable 类)
        h=BR28Model(cpu1_held_projection=True,initial_cpu1_control=0,
                    software_irq_command_projection=True,initial_software_irq_pending=(0,0))
        h.cpu1_release_handler=lambda value:None
        h.enable_multicore(config,0,lambda value:None)
        h.cpu1_control_rmw(CPU1_CONTROL,'or',1)   # 释放(0x60660da 形状)
        h.cpu1_control_rmw(CPU1_CONTROL,'or',8)   # kick(0x12b732 形状)→ control=9
        h.software_irq.command(0xffe9a0,2)        # CPU0 第二次 pend irq125 到 CPU1
        with self.assertRaisesRegex(Blocked,'unmodeled_unmaskable_irq'):
            h.advance(0x300)  # CPU1 的 pending 检查必须仍然发生
        with self.assertRaisesRegex(Blocked,'cpu1_hold_after_release_not_implemented'):
            h.write(CPU1_CONTROL,0)  # kick 后的 hold 也不能漏过已释放判定


if __name__=='__main__':unittest.main()
