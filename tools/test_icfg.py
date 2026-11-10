"""ICFG status preservation, demanded bits and partial-value transport.

No device or private ROM. The mode/RTI transition remains outside this model.
"""
import unittest

from br28_icfg import IcfgState, IcfgValue, GATES
from br28_machine import DualCoreMachine
from br28_model import BR28Model, EMU_CON
from br28_unknown import word
from probe_boot import Blocked, ENTRY, STACK_TOP, explore
from probe_pi32v2 import upstream


class IcfgTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.module=upstream();cls.table=cls.module.table.load()

    def enc(self,name,**fields):return self.module.codec.encode(self.table.by_id(name),fields)

    def run_code(self,program,budget,*,known=0x3ff,value=0,sp=STACK_TOP-64,regions=(),seeds=(),hardware=None):
        h={'regions':list(regions),'readonly':[(0x1c60000,b'\x11\x22\x33\x44')],
           'seeds':list(seeds),'snapshots':[],'description':{'phase':'isolated synthetic partial ICFG fixture'}}
        machine=DualCoreMachine(partial_icfg=True,unknown_task_gprs=True,initial_icfg_masks=(known,known))
        if hardware is None:
            hardware=BR28Model(cpu1_held_projection=True,initial_cpu1_control=0,
                              initial_irq_config=[0]*32,initial_ipmask=0,
                              debug_execution_projection=True,initial_debug_controls=(0,None))
        r=explore(self.module,program+bytes(8),name='partial_icfg',handoff=h,hardware=hardware,machine=machine,
            diagnostics={'initial_sp':sp,'icfg_initial':value,'exact_parallel_stores':True},budget=budget)
        return r,machine.cores[0],hardware

    def test_observed_write_zero_then_sti_preserves_hardware_status(self):
        e=self.enc
        program=e('li.u8',r=0,n=0)+e('mov.sr_r',r=0,s=11)+e('sti')+e('mov.r_sr',r=11,s=11)
        r,c,_=self.run_code(program,4,known=0xffffffff,value=0x033f0700)
        self.assertEqual(r['completed_steps'],4)
        self.assertEqual(c.icfg_state.value,0x033f0600)
        self.assertEqual(c.R[11].concrete(),0x033f0600)
        self.assertEqual(word(c.R[11]),'0x33f0600')

    def test_unknown_hardware_status_stays_unknown_across_exact_mask_write(self):
        e=self.enc
        program=e('mov.r_sr',r=2,s=11)+e('and.nmodimm.rri',r=2,s=2,n=3904)+e('mov.sr_r',r=2,s=11)
        r,c,_=self.run_code(program,3,known=0,value=0)
        self.assertEqual(r['completed_steps'],3)
        self.assertEqual((c.icfg_state.value,c.icfg_state.known),(0,GATES))
        self.assertIsInstance(c.R[2],IcfgValue)
        self.assertEqual(c.R[2].identity,0xfffffcff)
        self.assertEqual(c.icfg_state.gate_demand(),0)

    def test_cli_sti_restore_only_source_bit9(self):
        e=self.enc
        program=e('cli.r',x=3)+e('sti')+e('sti.r',x=3)
        r,c,_=self.run_code(program,3,known=0x200,value=0)
        self.assertEqual(r['completed_steps'],3)
        self.assertEqual((c.icfg_state.value,c.icfg_state.known),(0,0x200))
        self.assertEqual(c.icfg_state.gate_demand(),0)
        self.assertEqual(c.R[3].known,0x200)

    def test_unknown_saved_gate9_does_not_become_open(self):
        e=self.enc
        r,c,_=self.run_code(e('cli.r',x=3)+e('sti.r',x=3),2,known=0,value=0)
        self.assertEqual(r['completed_steps'],2)
        self.assertEqual(c.icfg_state.known,0)
        self.assertIsNone(c.icfg_state.gate_demand())

    def test_numeric_consumer_of_fully_known_snapshot_is_supported(self):
        e=self.enc
        r,c,_=self.run_code(e('mov.r_sr',r=0,s=11)+e('add.s14.rri',r=1,s=0,n=1),2,
                            known=0xffffffff,value=0x200)
        self.assertEqual(r['completed_steps'],2);self.assertEqual(c.R[1],0x201)
        self.assertEqual(word(c.R[0]),'0x200')

    def test_partial_arithmetic_branch_and_special_write_stop_before_commit(self):
        e=self.enc
        for operation in (e('add.s14.rri',r=1,s=0,n=1),e('beq.ri',r=0,i=0,n=2),e('mov.sr_r',r=0,s=14)):
            r,c,_=self.run_code(e('mov.r_sr',r=0,s=11)+operation,2)
            self.assertEqual(r['completed_steps'],1)
            self.assertEqual(r['stop']['kind'],'partial_icfg_value_consumed')
            self.assertEqual(c.sp,STACK_TOP-64);self.assertEqual(c.R[1],0)

    def test_lowbyte_is_demanded_without_consuming_unrelated_status(self):
        e=self.enc;program=e('mov.r_sr',r=0,s=11)+e('ext.b0.u',d=1,s=0)
        r,c,_=self.run_code(program,2,known=0x3ff,value=0)
        self.assertEqual(r['completed_steps'],2);self.assertEqual(c.R[1],0)
        r,c,_=self.run_code(program,2,known=0x300,value=0)
        self.assertEqual(r['completed_steps'],1)
        self.assertEqual(r['stop']['kind'],'partial_icfg_value_consumed')

    def test_unproven_status_or_stale_cross_core_writes_are_rejected_atomically(self):
        state=IcfgState(0,0x033f0700,0xffffffff)
        stale=state.read();other=IcfgState(1,0,0x3ff).read()
        for value in (0x100,0x400,other,stale.mask('and',0xfffffbff)):
            before=state.report()
            with self.assertRaises(Blocked):state.write(value)
            self.assertEqual(state.report(),before)
        state.invalidate_after_bootstrap(0xff);before=state.report()
        with self.assertRaises(Blocked):state.write(stale)
        self.assertEqual(state.report(),before)

    def test_spill_reload_and_known_byte_overlay_preserve_exact_bit_knowledge(self):
        e=self.enc
        program=e('mov.r_sr',r=3,s=11)+e('push.rmask',m=8)+e('pop.rmask',n=16)
        r,c,_=self.run_code(program,3)
        self.assertEqual(r['completed_steps'],3);self.assertEqual(c.R[4].known,0x3ff)
        self.assertEqual(c.R[4].origin,c.R[3].origin)
        self.assertEqual(c.R[4].identity,0xffffffff)
        a=c.sp-4
        with self.assertRaisesRegex(Blocked,'uninitialized_memory'):c.mem.read(a,4)
        c.mem.write(a+1,b'\xaa')
        value=c.mem.abstract_word(a)
        self.assertEqual(value.known,0xffff);self.assertEqual(value.value,0xaa00)
        self.assertEqual(value.identity,0xffff00ff)
        c.mem.write(a,b'\x01\x02\x03\x04')
        self.assertEqual(c.mem.abstract_word(a),0x04030201)
        self.assertNotIn(a,c.mem.icfg_spills)

    def test_partial_spill_and_reload_require_known_sp(self):
        e=self.enc
        for operation in (e('st.w.sp',r=0,n=0),e('ld.w.sp',r=1,n=0)):
            program=e('mov.r_sr',r=0,s=11)+e('li.sr_u32',s=12,n=STACK_TOP-128)+operation
            r,c,_=self.run_code(program,3)
            self.assertEqual(r['completed_steps'],2);self.assertEqual(r['stop']['kind'],'unknown_core_stack_pointer')
            self.assertFalse(c.mem.icfg_spills)

    def test_partial_word_cannot_write_mmio_or_readonly_through_ram_overlap(self):
        r,c,_=self.run_code(self.enc('mov.r_sr',r=0,s=11),1,
            regions=((ENTRY,ENTRY+16,'overlap_flash'),(EMU_CON,EMU_CON+4,'overlap_mmio'),
                     (0x1c60000,0x1c60004,'overlap_readonly')))
        for address in (ENTRY,EMU_CON,0x1c60000):
            with self.assertRaisesRegex(Blocked,'abstract_store_requires_ram'):c.mem.write_abstract_word(address,c.R[0])
        self.assertFalse(c.mem.icfg_spills)

    def test_irq_delivery_uses_only_required_gates_and_never_acknowledges_pending(self):
        for mask,value,expected in ((0x200,0,None),(0x100,0,None),(0x300,0x300,'software_irq_delivery_not_implemented'),
                                     (0x200,0x200,'software_irq_with_unknown_icfg')):
            config=[0]*32;config[127//8]=1<<28
            hardware=BR28Model(cpu1_held_projection=True,initial_cpu1_control=0,
                initial_irq_config=config,initial_ipmask=0,software_irq_command_projection=True)
            hardware.software_irq_command(0xffe1a0,8)
            r,c,h=self.run_code(self.enc('li.u8',r=0,n=1),1,known=mask,value=value,hardware=hardware)
            self.assertEqual(r['stop']['kind'],expected or 'instruction_budget')
            self.assertEqual(h.software_irq.pending[0].value,8)
            self.assertEqual(h.software_irq.pending[0].known,8)


if __name__=='__main__':unittest.main()
