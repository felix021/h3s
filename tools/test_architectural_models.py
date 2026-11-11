"""Runner scratch/RETS effects are not architecture; real stores must survive."""
import struct
import unittest
from unittest.mock import patch

from br28_arch_models import architectural_k2
from probe_boot import ENTRY, STACK_TOP, explore
from probe_pi32v2 import upstream

SP=STACK_TOP-128


class ArchitecturalModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.module=upstream();cls.table=cls.module.table.load()

    def enc(self,name,**fields):return self.module.codec.encode(self.table.by_id(name),fields)

    def run_code(self,code,*,regs=None,seeds=(),extra=None,budget=1):
        h={'regions':[],'readonly':[],'seeds':list(seeds),
           'snapshots':[('below_sp',SP-4,4),('at_sp',SP,4)],
           'description':{'phase':'isolated architectural effect test; no board execution'}}
        return explore(self.module,code+bytes(8),name='architectural_effects',handoff=h,
            diagnostics={'initial_sp':SP,'initial_gprs':regs or [0]*16,**(extra or {})},budget=budget)

    def test_raw_csync_has_runner_scratch_but_architectural_model_has_none(self):
        c=self.module.CPU();c.pc=ENTRY;c.sp=SP;c.rets=0xffffffff
        insn=self.table.by_id('csync')
        raw,_,_=c.effect(insn,{},ENTRY,2,c.R)
        self.assertEqual(raw['mem'],{SP-4:ENTRY})
        self.assertEqual(raw['rets'],0x07fffffe)
        c.models,names=architectural_k2(c.models)
        result,_,_=c.effect(insn,{},ENTRY,2,c.R)
        self.assertIn('csync',names)
        self.assertNotIn('mem',result);self.assertNotIn('rets',result)
        self.assertEqual(c.rets,0xffffffff)

    def test_csync_leaves_unwritten_stack_unknown(self):
        r=self.run_code(self.enc('csync'))
        self.assertEqual(r['completed_steps'],1)
        self.assertEqual(r['memory_event_counts'].get('write',0),0)
        self.assertEqual(r['memory_snapshots']['below_sp']['initialized_bytes'],0)

    def test_stack_adjust_does_not_initialize_the_new_local_word(self):
        r=self.run_code(self.enc('add.sp_i',n=255))  # actual QCY -4 form
        self.assertEqual(r['completed_steps'],1);self.assertEqual(r['last_registers']['sp'],hex(SP-4))
        self.assertEqual(r['memory_snapshots']['below_sp']['bytes_with_unknowns'],'????????')
        self.assertEqual(r['memory_event_counts'].get('write',0),0)

    def test_stack_address_calculation_does_not_write_a_scratch_word(self):
        code=self.enc('add.r_sp_i',r=1,n=4)
        insn,raw,_=self.module.codec.decode(self.table,code)
        self.assertEqual(self.module.codec.format(insn,raw),'r1 = sp + 4')
        r=self.run_code(code)
        self.assertEqual(r['last_registers']['r'][1],hex(SP+4))
        self.assertEqual(r['last_registers']['sp'],hex(SP))
        self.assertEqual(r['memory_snapshots']['below_sp']['initialized_bytes'],0)

    def test_legitimate_push_equal_to_pc_is_not_filtered(self):
        regs=[0]*16;regs[3]=ENTRY
        r=self.run_code(self.enc('push.rl',l=3),regs=regs)
        self.assertEqual(r['completed_steps'],1)
        self.assertEqual(r['memory_snapshots']['below_sp']['bytes_with_unknowns'],struct.pack('<I',ENTRY).hex())
        self.assertEqual(r['last_registers']['sp'],hex(SP-4))

    def test_pop_does_not_write_the_runner_stub_below_the_frame(self):
        r=self.run_code(self.enc('pop.rl',l=3),seeds=[(SP,struct.pack('<I',0x12345678))])
        self.assertEqual(r['completed_steps'],1);self.assertEqual(r['last_registers']['r'][3],'0x12345678')
        self.assertEqual(r['last_registers']['sp'],hex(SP+4))
        self.assertEqual(r['memory_snapshots']['below_sp']['initialized_bytes'],0)
        self.assertEqual(r['memory_event_counts'].get('write',0),0)

    def test_one_byte_local_store_preserves_unknown_neighbours(self):
        regs=[0]*16;regs[0]=0x12
        r=self.run_code(self.enc('st.b.sp.l',r=0,n=3),regs=regs,extra={'exact_subword_stores':True})
        self.assertEqual(r['completed_steps'],1)
        self.assertEqual(r['memory_snapshots']['at_sp']['bytes_with_unknowns'],'??????12')
        self.assertEqual(r['memory_snapshots']['at_sp']['initialized_bytes'],1)

    def test_parallel_division_stores_old_source_without_reading_destination(self):
        # Exact QCY clock_init pair at06000542: R0=R1/R3 # [SP+16]=oldR0.
        code=self.enc('div.u.rrr.p',d=0,a=1,b=3)+self.enc('st.w.sp',r=0,n=4)
        regs=[0]*16;regs[0]=120000000;regs[1]=480000000;regs[3]=5
        h={'regions':[],'readonly':[],'seeds':[],'snapshots':[('store',SP+16,4)],
           'description':{'phase':'isolated actual clock parallel forms'}}
        r=explore(self.module,code+bytes(8),name='parallel_division_store',handoff=h,
            diagnostics={'initial_sp':SP,'initial_gprs':regs,'exact_parallel_stores':True},budget=1)
        self.assertEqual(r['completed_steps'],1)
        self.assertEqual(r['last_registers']['r'][0],hex(96000000))
        self.assertEqual(r['memory_snapshots']['store']['bytes_with_unknowns'],struct.pack('<I',120000000).hex())

    def test_unknown_wrapper_shape_fails_without_modifying_input_models(self):
        def malformed(*args):return {}
        malformed.__module__='semantics.k2'
        models={'csync':malformed}
        with self.assertRaisesRegex(ValueError,'wrapper structure'):architectural_k2(models)
        self.assertIs(models['csync'],malformed)

    def test_wrong_source_digest_refuses_to_unwrap(self):
        with patch('br28_arch_models.K2_SHA','0'*64):
            with self.assertRaisesRegex(ValueError,'source changed'):architectural_k2(self.module.CPU().models)


if __name__=='__main__':unittest.main()
