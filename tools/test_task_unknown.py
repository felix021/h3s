"""Unknown initial task GPRs must survive copies/saves and never drive control.

Synthetic isolated fixtures plus the actual QCY context-pop instructions. No ROM
or device is needed. These checks do not establish first-task RTI semantics.
"""
import struct
import unittest

from br28_machine import DualCoreMachine
from br28_model import BR28Model, EMU_CON
from br28_unknown import UnknownWord
from probe_boot import ENTRY, STACK_TOP, Blocked, application, explore
from probe_pi32v2 import ROOT, upstream


class UnknownTaskTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module=upstream();cls.table=cls.module.table.load()
        cls.app=application(ROOT/'firmware/QCY_H3S_7018F_1T2_release_note_V1.1.8_011.ufw')

    def enc(self,name,**fields):return self.module.codec.encode(self.table.by_id(name),fields)

    def unknown(self):return UnknownWord((None,0x22,None,0x44),(0x124f10,None,0x124f12,None))

    def cpu(self,program,overlap=()):
        h={'regions':list(overlap),'readonly':[(0x1c60000,b'\x12\x34\x56\x78')],'seeds':[],'snapshots':[],
           'description':{'phase':'isolated unknown task-word regression'}}
        hardware=BR28Model(cpu1_held_projection=True,initial_cpu1_control=0,
                           debug_execution_projection=True,initial_debug_controls=(0,None))
        machine=DualCoreMachine(unknown_task_gprs=True)
        explore(self.module,program+bytes(8),name='unknown_fixture',handoff=h,hardware=hardware,machine=machine,
            diagnostics={'initial_sp':STACK_TOP-64,'icfg_initial':0,'exact_parallel_stores':True},budget=0)
        return machine.cores[0]

    def test_unused_unknowns_allow_overwrites_and_copy_preserves_partial_bytes(self):
        e=self.enc;c=self.cpu(e('li.u8',r=4,n=7)+e('mov.rr',x=3,y=1)+e('li.u8',r=1,n=2))
        value=self.unknown();c.R[1]=value;c.flags=5
        for _ in range(3):c.step()
        self.assertIs(c.R[3],value);self.assertEqual(c.R[4],7);self.assertEqual(c.R[1],2)
        self.assertEqual(c.flags,5)

    def test_unknown_indirect_targets_and_special_writes_fail_before_mutation(self):
        e=self.enc
        programs=[e('goto.r',x=1),e('call.r',x=1)]
        programs += [e('mov.sr_r',r=1,s=s) for s in (0,3,5,11,12,13,14)]
        for program in programs:
            with self.subTest(program=program.hex()):
                c=self.cpu(program);c.R[1]=self.unknown()
                before=(c.pc,c.steps,c.sp,c.ssp,c.usp,c.reti,c.rets,c.icfg,c.icfg_known,c.flags)
                with self.assertRaisesRegex(Blocked,'unknown_task_register_consumed'):c.step()
                self.assertEqual((c.pc,c.steps,c.sp,c.ssp,c.usp,c.reti,c.rets,c.icfg,c.icfg_known,c.flags),before)

    def test_unknown_arithmetic_or_branch_is_never_resolved_with_zero(self):
        for program in (self.enc('add.ri.u5',r=1,n=1),self.enc('beq.ri',r=1,i=0,n=2)):
            c=self.cpu(program);value=self.unknown();c.R[1]=value;c.flags=5
            with self.assertRaisesRegex(Blocked,'unknown_task_register_consumed'):c.step()
            self.assertIs(c.R[1],value);self.assertEqual(c.steps,0);self.assertEqual(c.flags,5)

    def test_parallel_unknown_copy_fails_before_known_half_or_store_commits(self):
        e=self.enc
        programs=[e('mov.rr.p',x=3,y=1)+e('mov.rr',x=4,y=0),
                  e('mov.rr.p',x=3,y=1)+e('st.w',d=0,b=2,n=0),
                  e('mov.rr.p',x=4,y=0)+e('mov.rr',x=3,y=1)]
        for program in programs:
            c=self.cpu(program);c.R[1]=self.unknown();c.R[0]=17;c.R[2]=c.sp
            c.mem.write(c.sp,struct.pack('<I',99));c.R[3]=88;c.R[4]=77
            with self.assertRaisesRegex(Blocked,'unknown_task_register_consumed'):c.step()
            self.assertEqual((c.R[3],c.R[4],c.pc,c.steps),(88,77,ENTRY,0))
            self.assertEqual(c.mem.read(c.sp,4),struct.pack('<I',99))

    def test_stack_save_invalidates_stale_bytes_and_reload_preserves_provenance(self):
        e=self.enc;c=self.cpu(e('push.rmask',m=2)+e('li.u8',r=1,n=0)+e('pop.rmask',n=4))
        value=self.unknown();c.R[1]=value;sp=c.sp
        c.mem.write(sp-4,b'\xaa'*4)
        c.step()
        with self.assertRaisesRegex(Blocked,'uninitialized_memory'):c.mem.read(sp-4,4)
        self.assertNotIn(sp-4,c.mem.ram);self.assertEqual(c.mem.ram[sp-3],0x22)
        self.assertNotIn(sp-2,c.mem.ram);self.assertEqual(c.mem.ram[sp-1],0x44)
        c.step();c.step()
        self.assertEqual(c.R[1],0);self.assertIsInstance(c.R[2],UnknownWord)
        self.assertEqual(c.R[2].bytes,value.bytes);self.assertEqual(c.R[2].origins,value.origins)
        self.assertEqual(c.sp,sp)

    def test_unwritten_ram_cannot_be_reloaded_as_a_saved_unknown(self):
        c=self.cpu(self.enc('pop.rmask',n=2));sp=c.sp;c.R[1]=123
        with self.assertRaisesRegex(Blocked,'uninitialized_memory'):c.step()
        self.assertEqual((c.R[1],c.sp,c.steps),(123,sp,0))

    def test_unknown_return_word_does_not_partly_pop_gprs_or_sp(self):
        c=self.cpu(self.enc('pop.pc_rl',l=0));sp=c.sp
        c.mem.write(sp,struct.pack('<IIII',11,22,33,44))
        c.mem.write_abstract_word(sp+16,self.unknown())
        before=list(c.R)
        with self.assertRaisesRegex(Blocked,'unknown_task_register_consumed'):c.step()
        self.assertEqual(c.R,before);self.assertEqual(c.sp,sp);self.assertEqual(c.pc,ENTRY)

    def test_abstract_access_cannot_bypass_flash_mmio_or_readonly_with_overlapping_ram(self):
        code=self.enc('li.u8',r=4,n=7)
        c=self.cpu(code,overlap=((ENTRY,ENTRY+32,'overlap_flash'),(EMU_CON,EMU_CON+4,'overlap_mmio'),
                                 (0x1c60000,0x1c60004,'overlap_readonly')))
        c.mem.ram.update({ENTRY+i:0xaa for i in range(4)})
        for address in (ENTRY,EMU_CON,0x1c60000):
            self.assertEqual(c.mem.abstract_word(address,task_frame=True),int.from_bytes(c.mem.read(address,4),'little'))
            with self.assertRaisesRegex(Blocked,'abstract_store_requires_ram'):
                c.mem.write_abstract_word(address,self.unknown())
        self.assertFalse(c.mem.unknown_origins)
        for address in (0x1c60000-2,0x1c60000+2):
            # Even an overlapping RAM fixture must not turn a partial readonly
            # word into four unknown RAM bytes.
            with self.assertRaisesRegex(Blocked,'abstract_read_crosses_readonly_boundary'):
                c.mem.abstract_word(address,task_frame=True)

    def frame(self,extent=76):
        tcb=0x124f00;frame=0x124f40
        h={'regions':[(tcb,tcb+4,'tcb'),(frame,frame+extent,'task_frame')],'readonly':[],
           'seeds':[(tcb,struct.pack('<I',frame)),(frame,struct.pack('<IIII',ENTRY,0,5,0x12345678))],
           'snapshots':[],'description':{'phase':'isolated real QCY task-frame restore'}}
        machine=DualCoreMachine(unknown_task_gprs=True);regs=[0xabcdef]*16;regs[4]=tcb
        r=explore(self.module,self.app,name='first_task_abstract_frame',handoff=h,
            hardware=BR28Model(cpu1_held_projection=True,initial_cpu1_control=0),machine=machine,
            diagnostics={'entry_pc':0x6066120,'initial_sp':STACK_TOP,'initial_gprs':regs,'icfg_initial':0},budget=4)
        return r,machine.cores[0],frame

    def test_actual_first_frame_load_replaces_every_gpr_and_preserves_flags(self):
        r,c,frame=self.frame()
        self.assertEqual(r['completed_steps'],4);self.assertEqual(c.sp,frame+76)
        self.assertEqual(c.flags,5);self.assertEqual(c.R[0],0x12345678)
        for i in range(1,16):
            self.assertIsInstance(c.R[i],UnknownWord)
            self.assertEqual(c.R[i].origins,tuple(range(frame+12+4*i,frame+16+4*i)))
        self.assertIsNone(c.task_gpr_frame)

    def test_unmapped_frame_tail_rejects_entire_gpr_pop(self):
        r,c,frame=self.frame(72)
        self.assertEqual(r['completed_steps'],3);self.assertEqual(c.sp,frame+12)
        self.assertEqual(r['stop']['kind'],'unmapped_memory')
        self.assertEqual(c.R[0],0xabcdef);self.assertEqual(c.R[15],0xabcdef)


if __name__=='__main__':unittest.main()
