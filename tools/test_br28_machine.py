"""Offline two-core instruction tests with a synthetic ROM and shared RAM.

No private ROM or physical headset. Board boot probes separately pin real ROM.
"""
import struct
import unittest
from types import SimpleNamespace

from br28_machine import DualCoreMachine, CoreStall
from br28_model import BR28Model, CPU1_CONTROL, IRQ_CONFIG, IPMASK, IcuInitializationProjection, CORE_PMU_CON1
from br28_model import SoftwareIrqCommands, CORE_TICK_BASES
from probe_boot import ENTRY, STACK_TOP, explore, Blocked, IrqDeliveryRequest
from probe_pi32v2 import upstream

DATA=0x124f00
LOOP=0x01c60000


class RepeatIrqDeliveryCpu:
    """最小 CPU 桩:只带交付/返回投影触到的字段。"""

    def __init__(self,slots,pc=0x12b608,core_id=0):
        self.mem=SlotMem(slots);self.pc=pc;self.reti=0;self.steps=7;self.core_id=core_id;self.flags=0
        self.sp_known=True;self.sp=0x4013f0;self.usp=None;self.ssp=0x123840
        self.last_explicit_ssp=0x123840
        self.bootstrap_context=None;self.after_then=None;self.repeat=None
        self.active_context=None
        self.icfg=0x300;self.icfg_known=True  # gates8/9 开(sti 后),事件时钟探查用


class SlotMem:
    def __init__(self,slots):self.slots=dict(slots)

    def read(self,address,length):
        assert (address,length) in ((a,4) for a in self.slots)
        return struct.pack('<I',self.slots[address])


class RepeatIrqDeliveryTests(unittest.TestCase):
    """暂停点1最小闭环:handler RTI 返回后同核可再次交付,默认关闭仍一次性。"""

    def software_machine(self,**extra):
        m=DualCoreMachine(software_irq_entry=True,repeat_irq_delivery=True,**extra)
        m.hardware=SimpleNamespace(software_irq=SoftwareIrqCommands((8,0)))
        return m

    def test_software_irq_redelivers_after_handler_rti(self):
        m=self.software_machine(software_irq_entry_banks=True,software_irq_rti=True)
        cpu=RepeatIrqDeliveryCpu({0x19fffc:0x12AD2A})
        m.deliver_software_irq(cpu,127)
        self.assertEqual(m.hardware.software_irq.acknowledgements,1)
        cpu.pc=0x12ade8  # 真实 handler 尾部 RTI
        m.return_software_irq(cpu)
        self.assertIsNone(cpu.active_context)
        m.hardware.software_irq.command(0xffe1a0,8)  # 固件重新 pend(真实命令路径)
        m.deliver_software_irq(cpu,127)
        self.assertEqual([(d['core'],d['irq']) for d in m.software_irq_deliveries],[(0,127),(0,127)])
        self.assertEqual(m.hardware.software_irq.acknowledgements,2)
        self.assertEqual((cpu.pc,cpu.active_context),(0x12AD2A,'software_irq_handler'))

    def test_repeat_delivery_rejected_while_a_handler_is_in_flight(self):
        m=self.software_machine()
        cpu=RepeatIrqDeliveryCpu({0x19fffc:0x12AD2A})
        m.deliver_software_irq(cpu,127)
        with self.assertRaisesRegex(ValueError,'must not nest'):
            m.deliver_software_irq(cpu,127)  # 软件 IRQ handler 在途
        cpu.active_context='core_tick_handler'
        with self.assertRaisesRegex(ValueError,'must not nest'):
            m.deliver_software_irq(cpu,127)  # tick handler 在途同样拒绝,不嵌套
        self.assertEqual(len(m.software_irq_deliveries),1)

    def test_default_off_keeps_the_one_shot_limit_after_a_real_rti(self):
        m=DualCoreMachine(software_irq_entry=True,software_irq_entry_banks=True,software_irq_rti=True)
        m.hardware=SimpleNamespace(software_irq=SoftwareIrqCommands((8,0)))
        cpu=RepeatIrqDeliveryCpu({0x19fffc:0x12AD2A})
        m.deliver_software_irq(cpu,127)
        cpu.pc=0x12ade8
        m.return_software_irq(cpu)
        m.hardware.software_irq.command(0xffe1a0,8)
        with self.assertRaisesRegex(ValueError,'one-shot'):
            m.deliver_software_irq(cpu,127)  # 未开 repeat 投影:handler 已返回也仍一次性
        self.assertEqual(len(m.software_irq_deliveries),1)

    def test_core_tick_redelivers_after_handler_rti(self):
        m=DualCoreMachine(core_tick_entry=True,core_tick_entry_banks=True,
                          core_tick_rti=True,repeat_irq_delivery=True)
        cpu=RepeatIrqDeliveryCpu({0x19fe0c:0x60661a4},pc=0x6012028)
        m.cores={0:cpu}
        m.deliver_core_tick(0)
        cpu.pc=0x60661e6  # 真实 tick handler 尾部 RTI
        m.return_core_tick(cpu)
        m.deliver_core_tick(0)  # 第二次溢出交付
        self.assertEqual([d['core'] for d in m.core_tick_deliveries],[0,0])
        self.assertEqual((cpu.pc,cpu.active_context),(0x60661a4,'core_tick_handler'))

    def test_repeat_flag_requires_an_entry_projection(self):
        with self.assertRaisesRegex(ValueError,'entry projection'):
            DualCoreMachine(repeat_irq_delivery=True)


class EventClockTests(unittest.TestCase):
    """暂停点2事件时钟最小契约(docs/event-clock.md):推进条件/同刻顺序/屏蔽与唤醒/无事件诚实停。"""

    def machine(self,**extra):
        return DualCoreMachine(software_irq_entry=True,core_tick_entry=True,
                               idle_wait_projection=True,event_clock_projection=True,**extra)

    def hardware(self,core0_irq3_nibble=3,core1_irq3_nibble=0,progress=(1,1)):
        h=BR28Model(cpu1_held_projection=True,initial_cpu1_control=0,
                    initial_irq_config=[core0_irq3_nibble<<12]+[0]*31,initial_ipmask=0,
                    core_tick_configuration=True,initial_core_tick_controls=(0,0),
                    core_tick_progress=progress,
                    core_tick_delivery_requests=True,
                    software_irq_command_projection=True,
                    initial_software_irq_pending=(0,0),
                    software_irq_delivery_requests=True)
        h.enable_multicore([core1_irq3_nibble<<12]+[0]*31,0,lambda value:None)
        h.write(CPU1_CONTROL,1)  # 真实释放路径;irq125(word15)两核默认屏蔽
        h.core_icfg={0:0x300,1:0x300}
        return h

    def arm_tick(self,h,core,cnt,prd):
        h.write(CORE_TICK_BASES[core]+8,prd)
        h.write(CORE_TICK_BASES[core]+4,cnt)
        h.write(CORE_TICK_BASES[core],1)

    def parked_machine(self,h):
        m=self.machine()
        cpu0=RepeatIrqDeliveryCpu({0x19fe0c:0x60661a4},pc=0x6066318,core_id=0)
        cpu1=RepeatIrqDeliveryCpu({0x19fe0c:0x60661a4},pc=0x12c5ca,core_id=1)
        m.cores={0:cpu0,1:cpu1};m.hardware=h;m.idle_wait_cores={0}
        return m,cpu0,cpu1

    def test_advances_exactly_to_the_nearest_tick_overflow_and_wakes_only_the_eligible_core(self):
        h=self.hardware()  # core0 irq3 合格(nibble3=使能+优先级1),core1 irq3 屏蔽
        m,cpu0,cpu1=self.parked_machine(h)
        self.arm_tick(h,0,4,5)  # 2 步后溢出
        self.arm_tick(h,1,2,5)  # 4 步后才溢出(且被屏蔽,不唤醒)
        before=h.counts['completed_model_steps']
        with self.assertRaises(IrqDeliveryRequest) as ctx:
            m.enter_idle_wait(cpu1)  # 最后一个核 IDLE:事件时钟接管
        self.assertEqual(ctx.exception.details,{'core':0,'irq':3})
        m.deliver_core_tick(0)  # probe_boot 主循环在 IrqDeliveryRequest 后做的同一步
        self.assertEqual(h.counts['completed_model_steps']-before,2)  # 时间恰好推进到溢出点
        self.assertEqual(m.event_clock_advances,2)
        self.assertEqual((cpu0.pc,cpu0.active_context),(0x60661a4,'core_tick_handler'))
        self.assertEqual(m.idle_wait_cores,{1})  # 只有被唤醒的核离开 IDLE
        self.assertEqual(h.core_ticks[1].cnt,3)  # 被屏蔽核照常计数:2+第1步;第2步的交付请求先于它的计数抛出
        self.assertFalse(h.core_ticks[1].pending)

    def test_same_tick_overflows_wake_in_core_order(self):
        h=self.hardware(core1_irq3_nibble=3)  # 两核都合格
        m,cpu0,cpu1=self.parked_machine(h)
        self.arm_tick(h,0,4,5);self.arm_tick(h,1,4,5)  # 同刻溢出
        with self.assertRaises(IrqDeliveryRequest) as ctx:
            m.enter_idle_wait(cpu1)
        self.assertEqual(ctx.exception.details,{'core':0,'irq':3})  # 扫描序:低核号先(请求抛出时 core1 计数尚未推进)
        m.deliver_core_tick(0)
        with self.assertRaises(IrqDeliveryRequest) as second:
            h.advance(0x300)  # 下一虚拟步:core1 的溢出按同一扫描序请求交付
        self.assertEqual(second.exception.details,{'core':1,'irq':3})
        m.deliver_core_tick(1)
        self.assertEqual([d['core'] for d in m.core_tick_deliveries],[0,1])
        self.assertEqual(m.idle_wait_cores,set())
        self.assertTrue(h.core_ticks[0].pending and h.core_ticks[1].pending)

    def test_masked_pending_never_wakes_and_time_moves_to_the_next_tick_event(self):
        h=self.hardware()
        m,cpu0,cpu1=self.parked_machine(h)
        h.software_irq.command(0xffe9a0,2)  # 固件真实命令路径置位 core1 pending(irq125,两核 word15=0 屏蔽)
        self.arm_tick(h,0,3,5)  # 3 步后溢出(合格)
        with self.assertRaises(IrqDeliveryRequest) as ctx:
            m.enter_idle_wait(cpu1)
        self.assertEqual(ctx.exception.details,{'core':0,'irq':3})
        m.deliver_core_tick(0)
        self.assertEqual(m.software_irq_deliveries,[])  # 被屏蔽的 pending 从不唤醒
        self.assertEqual((h.software_irq.pending[1].known,h.software_irq.pending[1].value),(15,2))  # pending 保持置位
        self.assertEqual(m.event_clock_advances,3)

    def test_masked_pending_alone_is_not_an_event_and_stops_honestly(self):
        h=self.hardware()  # 不武装任何 tick
        m,cpu0,cpu1=self.parked_machine(h)
        h.software_irq.command(0xffe9a0,2)
        with self.assertRaises(Blocked) as ctx:
            m.enter_idle_wait(cpu1)
        self.assertEqual(ctx.exception.details['kind'],'all_cores_idle_waiting')
        self.assertIn('no modeled event',ctx.exception.details['note'])
        self.assertEqual(m.event_clock_advances,1)  # 单步探查确认 pending 被屏蔽,不是事件
        self.assertEqual(m.software_irq_deliveries,[])
        self.assertEqual(h.software_irq.pending[1].value,2)

    def test_stops_honestly_with_zero_advances_when_nothing_can_fire(self):
        h=self.hardware()  # 无 tick 在计数,pending 全 0
        m,cpu0,cpu1=self.parked_machine(h)
        with self.assertRaises(Blocked) as ctx:
            m.enter_idle_wait(cpu1)
        self.assertEqual(ctx.exception.details['kind'],'all_cores_idle_waiting')
        self.assertIn('no modeled event',ctx.exception.details['note'])
        self.assertEqual(m.event_clock_advances,0)
        self.assertEqual(m.idle_wait_cores,{0,1})

    def test_default_off_keeps_the_exact_legacy_stop_shape(self):
        m=DualCoreMachine(software_irq_entry=True,idle_wait_projection=True)  # 事件时钟默认关
        h=self.hardware()
        m.hardware=h
        cpu0=RepeatIrqDeliveryCpu({},pc=0x6066318,core_id=0)
        cpu1=RepeatIrqDeliveryCpu({},pc=0x12c5ca,core_id=1)
        m.cores={0:cpu0,1:cpu1};m.idle_wait_cores={0}
        self.arm_tick(h,0,4,5)  # 即使有未来溢出,默认也立即停(时间停死=legacy 行为)
        with self.assertRaises(Blocked) as ctx:
            m.enter_idle_wait(cpu1)
        self.assertEqual(ctx.exception.details,{'kind':'all_cores_idle_waiting','idle_cores':[0,1]})
        self.assertEqual(h.counts['completed_model_steps'],0)

    def test_event_clock_flag_requires_the_idle_wait_projection(self):
        with self.assertRaisesRegex(ValueError,'IDLE wait projection'):
            DualCoreMachine(event_clock_projection=True)

    def test_report_section_is_opt_in(self):
        self.assertIn('event_clock_projection',self.machine().report())
        self.assertNotIn('event_clock_projection',
                         DualCoreMachine(software_irq_entry=True,idle_wait_projection=True).report())


class MachineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.module=upstream();cls.table=cls.module.table.load()

    def enc(self,name,**fields):return self.module.codec.encode(self.table.by_id(name),fields)

    def programs(self,locking=False,unknown_sr=False):
        e=self.enc
        start=e('lockset') if locking else b''
        c0=start+e('li.u32',r=2,n=CPU1_CONTROL)+e('li.u8',r=0,n=1)+e('st.w',d=0,b=2,n=0)
        c0+=e('mov.r_sr',r=0,s=6)+e('li.u32',r=1,n=DATA+4)+e('st.w',d=0,b=1,n=0)+e('li.u8',r=0,n=42)
        c0+=(e('lockclr') if locking else b'')+e('li.u32',r=15,n=LOOP)+e('goto.r',x=15)+bytes(8)
        c1=start+e('li.u32',r=2,n=DATA)+e('mov.r_sr',r=0,s=4 if unknown_sr else 6)+e('st.w',d=0,b=2,n=0)
        c1+=(e('lockclr') if locking else b'')+e('li.u32',r=15,n=LOOP)+e('goto.r',x=15)+bytes(8)
        return c0,c1

    def run_program(self,locking=False,unknown_sr=False,reset_pc=0,cpu1_program=None,machine_factory=DualCoreMachine):
        app,rom=self.programs(locking,unknown_sr)
        if cpu1_program is not None:rom=cpu1_program+bytes(8)
        h={'regions':[(STACK_TOP-256,STACK_TOP,'stack'),(DATA,DATA+8,'shared_data'),(0x1265f5,0x1265f6,'ready')],
           'readonly':[(0,rom),(LOOP,self.enc('goto.r',x=15)+bytes(8))],
           'seeds':[(0x1265f5,b'\x00')], 'description':{'phase':'synthetic two-core unit test; not H3S boot'},
           'snapshots':[('cnums',DATA,8),('ready',0x1265f5,1)]}
        hardware=BR28Model(cpu1_held_projection=True,initial_cpu1_control=0,bus_lock_model=True,initial_lock_owner=None)
        machine=machine_factory(cpu1_reset_pc=reset_pc,cpu1_icfg=0,irq1_config=[0]*32,ipmask1=0)
        return explore(self.module,app,name='synthetic_dual_core',handoff=h,hardware=hardware,machine=machine,
            diagnostics={'initial_sp':STACK_TOP,'icfg_initial':0,'exact_parallel_stores':True},budget=40)

    def test_per_core_cnum_and_registers_use_shared_ram_without_ready_injection(self):
        r=self.run_program()
        self.assertEqual(r['stop']['kind'],'instruction_budget')
        self.assertEqual(r['memory_snapshots']['cnums']['bytes_with_unknowns'],'0100000000000000')
        self.assertEqual(r['memory_snapshots']['ready']['bytes_with_unknowns'],'00')
        self.assertEqual(r['machine']['cores']['0']['r'][0],'0x2a')
        self.assertEqual(r['machine']['cores']['1']['r'][0],'0x1')
        self.assertFalse(r['machine']['ready_injected'])

    def test_other_core_lock_wait_retries_then_acquires_without_stealing(self):
        r=self.run_program(locking=True)
        self.assertEqual(r['stop']['kind'],'instruction_budget')
        self.assertGreater(r['machine']['stalls'],0)
        self.assertEqual(r['memory_snapshots']['cnums']['bytes_with_unknowns'],'0100000000000000')
        self.assertEqual(r['peripherals']['counts']['lock_acquire'],2)
        self.assertEqual(r['peripherals']['counts']['lock_release'],2)
        self.assertIsNone(r['peripherals']['shared_lock_owner'])

    def test_unknown_release_vector_stops_before_control_changes_or_core_creation(self):
        r=self.run_program(reset_pc=None)
        self.assertEqual(r['stop']['kind'],'cpu1_reset_vector_unknown')
        self.assertEqual(r['peripherals']['cpu1_held_projection']['current_control'],0)
        self.assertEqual(r['machine']['launches'],0)
        self.assertNotIn('1',r['machine']['cores'])

    def test_unmodeled_special_read_never_falls_into_upstream_runner_scratch(self):
        r=self.run_program(unknown_sr=True)
        self.assertEqual(r['stop']['kind'],'multicore_special_register_semantics_unmodeled')
        self.assertEqual(r['stop']['register'],4)

    def test_core_stack_is_unknown_until_actual_visible_write(self):
        r=self.run_program(cpu1_program=self.enc('push.rl',l=0))
        self.assertEqual(r['stop']['kind'],'unknown_core_stack_pointer')
        self.assertEqual(r['machine']['cores']['1']['sp'],'unknown')
        self.assertEqual(r['machine']['cores']['1']['steps'],0)

    def test_psr_read_combines_actual_current_flags(self):
        class ExplicitFlags(DualCoreMachine):
            def launch(self,value):
                super().launch(value)
                self.cores[1].irq_psr=0;self.cores[1].flags=5
        e=self.enc
        program=e('mov.r_sr',r=0,s=5)+e('li.u32',r=2,n=DATA)+e('st.w',d=0,b=2,n=0)
        program+=e('li.u32',r=15,n=LOOP)+e('goto.r',x=15)
        r=self.run_program(cpu1_program=program,machine_factory=ExplicitFlags)
        self.assertEqual(r['stop']['kind'],'instruction_budget')
        self.assertEqual(r['memory_snapshots']['cnums']['bytes_with_unknowns'],'0500000000000000')

    def test_irq_banks_and_hold_after_release_are_independent(self):
        h=BR28Model(cpu1_held_projection=True,initial_cpu1_control=0,initial_irq_config=[0]*32,initial_ipmask=0)
        h.enable_multicore([0]*32,0,lambda value:None)
        h.write(IRQ_CONFIG+0x800,7);h.write(IPMASK+0x800,6)
        self.assertEqual(h.read(IRQ_CONFIG),0)
        self.assertEqual(h.read(IPMASK),0)
        self.assertEqual(h.read(IRQ_CONFIG+0x800),7)
        self.assertEqual(h.read(IPMASK+0x800),6)
        h.write(CPU1_CONTROL,1)
        with self.assertRaisesRegex(Blocked,'hold_after_release'):h.write(CPU1_CONTROL,0)
        self.assertEqual(h.read(CPU1_CONTROL),1)

    def test_cpu1_control_kick_wakes_only_a_parked_idle_core_once(self):
        from types import SimpleNamespace
        with self.assertRaisesRegex(ValueError,'IDLE wait'):
            DualCoreMachine(cpu1_control_kick_wake=True)
        m=DualCoreMachine(cpu1_control_kick_wake=True,idle_wait_projection=True,software_irq_entry=True)
        m.hardware=SimpleNamespace(cpu1_control_kick_writes=1,current_core=0,core_icfg={})
        stub=lambda pc:SimpleNamespace(pc=pc,steps=0,icfg=0,icfg_known=True,
                                       decode=lambda pc:(SimpleNamespace(id='idle'),{},2))
        m.cores={0:stub(0),1:stub(0x6066318)};m.idle_wait_cores={1}
        self.assertIs(m.select(),m.cores[0])  # 唤醒后按轮转仍选 core0,idle 已按完成计步
        parked=m.cores[1]
        self.assertEqual((parked.pc,parked.steps),(0x606631a,1))
        self.assertNotIn(1,m.idle_wait_cores)
        wakes=[e for e in m.events if e['kind']=='core_woken_by_cpu1_control_kick']
        self.assertEqual(len(wakes),1)  # kick 计数未再增长时不得重复唤醒
        m.select()
        self.assertEqual(len([e for e in m.events if e['kind']=='core_woken_by_cpu1_control_kick']),1)

    def test_icu_busy_completion_requires_explicit_progress_and_independent_status(self):
        base=0xffec00
        h=IcuInitializationProjection({base:0x80000000})
        with self.assertRaisesRegex(Blocked,'progress_unknown'):h.write(base+16,0xca000010)
        h=IcuInitializationProjection({base:0x80000000},3)
        h.write(base+16,0xca000010)
        self.assertEqual(h.read(base),0xc0000000)
        for _ in range(2):h.advance();self.assertTrue(h.read(base)&0x40000000)
        h.advance();self.assertEqual(h.read(base),0x80000000)
        h.write(base,0x80000001);self.assertEqual(h.read(base),0x80000001)
        h=IcuInitializationProjection({base:0},0);h.write(base+16,0xca000010)
        for _ in range(10):h.advance()
        self.assertEqual(h.read(base),0x40000000)
        with self.assertRaisesRegex(Blocked,'before_command_completed'):h.write(base,0x40000001)

    def test_bus_dispatch_uses_pending_command_over_static_cache_fixture(self):
        base=0xffec00
        h=BR28Model(cache_status_probe=True,cache_status_words={base:0x80000000},
                    icu_initialization_projection=True,icu_command_steps=3)
        h.write(base+16,0xca000010)
        self.assertEqual(h.read(base),0xc0000000)
        h.advance(0);h.advance(0)
        self.assertEqual(h.read(base),0xc0000000)
        h.advance(0);self.assertEqual(h.read(base),0x80000000)

    def test_soft_command_preserves_unknown_neighbours_and_never_supplies_wo_read(self):
        h=BR28Model(software_irq_command_projection=True)
        h.software_irq_command(0xffe1a0,8)
        h.software_irq_command(0xffe9a0,4)
        h.software_irq_command(0xffe1a4,8)
        self.assertEqual((h.software_irq.pending[0].known,h.software_irq.pending[0].value),(8,0))
        self.assertEqual((h.software_irq.pending[1].known,h.software_irq.pending[1].value),(4,4))
        with self.assertRaisesRegex(Blocked,'wo_readback_unverified'):h.read(0xffe1a0)
        with self.assertRaisesRegex(Blocked,'bits_unmodeled'):h.software_irq_command(0xffe1a0,16)
        with self.assertRaisesRegex(Blocked,'full_store_contract_unmodeled'):h.write(0xffe1a0,8)

    def test_core_power_mask_retains_unknown_state_through_bus_read(self):
        h=BR28Model(cpu1_held_projection=True,initial_cpu1_control=0)
        h.enable_multicore(None,None,lambda value:None)
        h.core_power_rmw(CORE_PMU_CON1[1],'and',0xfffffeff)
        with self.assertRaisesRegex(Blocked,'uninitialized_gpio_bits'):h.read(CORE_PMU_CON1[1])
        self.assertEqual(h.core_power[CORE_PMU_CON1[1]].known,0x100)


if __name__=='__main__':unittest.main()
