"""Offline two-core instruction tests with a synthetic ROM and shared RAM.

No private ROM or physical headset. Board boot probes separately pin real ROM.
"""
import struct
import unittest

from br28_machine import DualCoreMachine, CoreStall
from br28_model import BR28Model, CPU1_CONTROL, IRQ_CONFIG, IPMASK, IcuInitializationProjection, CORE_PMU_CON1
from probe_boot import ENTRY, STACK_TOP, explore, Blocked
from probe_pi32v2 import upstream

DATA=0x124f00
LOOP=0x01c60000


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
