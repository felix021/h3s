"""Scoped task-return comparisons, not a silicon RTI conformance test.

Execute the real QCY setup/tail with a test-only RTS at task_switch_hook. The
fixture isolates provenance/atomicity; it is not a boot or firmware approval.
"""
import struct
from types import SimpleNamespace
import unittest

from br28_icfg import IcfgState
from br28_machine import DualCoreMachine, CORE1_IDLE_DELAY_LIMIT, FIRST_TASK_SETUP
from br28_model import BR28Model, SoftwareIrqCommands
from probe_boot import ENTRY, STACK_TOP, application, explore
from probe_pi32v2 import ROOT, upstream

CURRENT=0x126c5c
TCB=0x127db8
FRAME=0x124f00


class TaskReturnTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module=upstream();cls.table=cls.module.table.load()
        cls.app=application(ROOT/'firmware/QCY_H3S_7018F_1T2_release_note_V1.1.8_011.ufw')

    def enc(self,name,**fields):return self.module.codec.encode(self.table.by_id(name),fields)

    def run_frame(self,hypothesis='open_gate8',*,entry=0x6065364,tcb=TCB,reti=0x12c57e,psr=0,mutation=None,extent=76):
        app=bytearray(self.app)
        # This unit tests real context-restore instructions; the separate
        # private-ROM integration probe executes the complete mapping hook.
        app[0x6066232-ENTRY:0x6066234-ENTRY]=self.enc('rts')
        if mutation is not None:
            address,data=mutation;app[address-ENTRY:address-ENTRY+len(data)]=data
        h={'regions':[(CURRENT,CURRENT+4,'current_tcb'),(TCB,TCB+4,'idle_tcb'),(FRAME,FRAME+extent,'task_frame')],
           'readonly':[],'seeds':[(CURRENT,struct.pack('<I',tcb)),(TCB,struct.pack('<I',FRAME)),
                                 (FRAME,struct.pack('<IIII',reti,0,psr,0))],
           'snapshots':[],'description':{'phase':'isolated real QCY restore; task_switch_hook is an explicit RTS test stub'}}
        machine=DualCoreMachine(partial_icfg=True,unknown_task_gprs=True,initial_icfg_masks=(0x3ff,0x3ff),
                                first_task_return_hypothesis=hypothesis)
        hardware=BR28Model(cpu1_held_projection=True,initial_cpu1_control=0,bus_lock_model=True,
                           initial_lock_owner=None,initial_irq_config=[0]*32,initial_ipmask=6,
                           software_irq_command_projection=True,initial_software_irq_pending=(0,0))
        result=explore(self.module,bytes(app),name='isolated_first_task_comparison',handoff=h,
            hardware=hardware,machine=machine,diagnostics={'entry_pc':entry,'initial_sp':STACK_TOP-64,
                'icfg_initial':0x200,'stop_before_pc':0x12c57e},budget=32)
        return result,machine.cores[0],machine,hardware

    def test_default_strict_path_still_stops_before_task_rti(self):
        r,c,m,h=self.run_frame(None)
        self.assertEqual(r['stop_pc'],'0x6066130')
        self.assertEqual(r['stop']['kind'],'unsupported_runtime_instruction')
        self.assertFalse(m.first_task_returns);self.assertEqual(c.sp,FRAME+76)

    def test_success_uses_executed_current_tcb_and_complete_setup(self):
        r,c,m,h=self.run_frame()
        self.assertEqual(r['stop']['kind'],'diagnostic_milestone_reached')
        self.assertEqual(c.pc,0x12c57e);self.assertEqual(c.sp,FRAME+76)
        self.assertEqual(c.icfg_state.known,0x3ff);self.assertEqual(c.icfg_state.value,0x300)
        event=m.first_task_returns[0]
        self.assertEqual(event['executed_setup_pcs'],[hex(x[0]) for x in FIRST_TASK_SETUP])
        self.assertEqual(event['tcb'],hex(TCB));self.assertEqual(event['restored_frame'],hex(FRAME))
        self.assertFalse(event['hardware_mode_certified']);self.assertIsNone(c.first_task_return_frame)
        self.assertIsNone(c.ssp);self.assertIsNone(c.usp);self.assertIsNone(h.lock_owner)

    def test_uncertain_stack_or_gate_is_not_replaced_by_a_number(self):
        for mode,sp_known,mask in (('unresolved_stack',False,0x2ff),('unknown_gate8',True,0x2ff),
                                  ('closed_gate8',True,0x3ff)):
            r,c,m,h=self.run_frame(mode)
            self.assertEqual(r['stop']['kind'],'diagnostic_milestone_reached')
            self.assertEqual(c.sp_known,sp_known);self.assertEqual(c.icfg_state.known,mask)
            self.assertEqual(c.icfg_state.value,0x200)
            self.assertIsNone(c.ssp);self.assertIsNone(c.usp)

    def test_direct_entry_without_real_scheduler_call_cannot_arm_return(self):
        for entry in (0x6066120,0x6066130):
            r,c,m,h=self.run_frame(entry=entry)
            self.assertEqual(r['completed_steps'],0)
            self.assertFalse(m.first_task_returns);self.assertIsNone(c.first_task_return_frame)
            self.assertEqual(c.pc,entry);self.assertEqual(c.sp,STACK_TOP-64)

    def test_other_tcb_or_changed_selection_opcode_is_rejected(self):
        variants=({'tcb':TCB+4},{'mutation':(0x6066116,self.enc('ld.w.idxs',r=5,s=4,t=1))})
        for arguments in variants:
            r,c,m,h=self.run_frame(**arguments)
            self.assertFalse(m.first_task_returns);self.assertIsNone(c.first_task_return_frame)
            self.assertEqual(c.sp,STACK_TOP-64)

    def test_bad_frame_rejects_before_partial_gpr_restore(self):
        for kwargs in ({'reti':0x6012028},{'psr':1},{'extent':72}):
            r,c,m,h=self.run_frame(**kwargs)
            self.assertFalse(m.first_task_returns);self.assertIsNone(c.first_task_return_frame)
            self.assertEqual(c.sp,FRAME+12)
            self.assertEqual(c.R[0],TCB)

    def test_mutated_sti_or_rti_does_not_execute_its_side_effects(self):
        for address,code in ((0x606612e,self.enc('li.u8',r=0,n=7)),(0x6066130,self.enc('rts'))):
            r,c,m,h=self.run_frame(mutation=(address,code))
            self.assertFalse(m.first_task_returns);self.assertEqual(c.pc,address)
            self.assertEqual(c.R[0],0);self.assertEqual(c.sp,FRAME+76)

    def test_same_core_cannot_reuse_completed_startup_return(self):
        r,c,m,h=self.run_frame();c.pc=0x6065364
        insn,raw,size=c.decode(c.pc);before=(c.pc,c.steps,c.rets)
        with self.assertRaisesRegex(ValueError,'actual scheduler call'):c.step()
        self.assertEqual((c.pc,c.steps,c.rets),before)

    def test_explicit_pending_fixture_preserves_actual_later_set_command(self):
        raw=SoftwareIrqCommands();self.assertEqual(raw.pending[0].known,0)
        projected=SoftwareIrqCommands((0,0));projected.command(0xffe1a0,8)
        self.assertEqual((projected.pending[0].known,projected.pending[0].value),(15,8))
        self.assertEqual(projected.commands,1)
        self.assertTrue(projected.report()['pending_fixture_is_not_measured_reset_state'])
        with self.assertRaises(ValueError):SoftwareIrqCommands((0,16))

    def test_issue_delay_is_bounded_and_does_not_execute_idle(self):
        m=DualCoreMachine(partial_icfg=True,unknown_task_gprs=True,
                          first_task_return_hypothesis='open_gate8',defer_core1_first_idle=True)
        m.hardware=SimpleNamespace(current_core=0,core_icfg={},cpu1_control=1)
        m.cores={i:SimpleNamespace(pc=0x12c5ca,steps=0,icfg_state=IcfgState(i,0x200,0x3ff)) for i in (0,1)}
        m.first_task_returns[1]={'hypothesis':'synthetic unit-test marker'};m.last_core=0
        for _ in range(CORE1_IDLE_DELAY_LIMIT):self.assertIs(m.select(),m.cores[0])
        self.assertIs(m.select(),m.cores[1])
        self.assertEqual(m.deferred_core1_turns,CORE1_IDLE_DELAY_LIMIT)
        self.assertEqual(m.cores[1].pc,0x12c5ca);self.assertEqual(m.cores[1].steps,0)
        self.assertEqual(m.hardware.cpu1_control,1)


if __name__=='__main__':unittest.main()
