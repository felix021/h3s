"""Isolated instruction tests for the SFC gate, stack stores and opaque PWM read.

No private ROM, device access, vendor compiler or firmware output required.
"""
from pathlib import Path
import struct
import unittest

from br28_model import BR28Model, SFC_BASE, MMU_BASE, EMU_CON
from probe_boot import ENTRY, STACK_TOP, application, explore
from probe_boot_handoff import RAM_CODE, SENTINEL_A, package_header, profile
from probe_pi32v2 import ROOT, upstream


class BusInstructionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module=upstream()
        firmware=ROOT/'firmware/QCY_H3S_7018F_1T2_release_note_V1.1.8_011.ufw'
        cls.app=application(firmware);cls.header=package_header(firmware)

    def handoff(self):
        h=profile(self.header,SENTINEL_A)
        dst,src,length=RAM_CODE
        h['seeds'].append((dst,self.app[src-ENTRY:src-ENTRY+length]))
        h['description']['phase']='isolated real-instruction bus test; not contiguous startup'
        return h

    def test_cached_decoder_entry_still_obeys_flash_disable(self):
        regs=[0]*16;regs[1]=0;regs[2]=SFC_BASE
        result=explore(self.module,self.app,name='cached_flash_fetch_gate',handoff=self.handoff(),
            hardware=BR28Model(sfc_read_bus=True,initial_sfc_control=1),
            diagnostics={'entry_pc':0x12e41e,'initial_sp':STACK_TOP,'initial_rets':ENTRY,
                         'initial_gprs':regs,'exact_parallel_stores':True,'predecode_pcs':[ENTRY]},budget=4)
        self.assertEqual(result['completed_steps'],2)
        self.assertEqual(result['stop_pc'],hex(ENTRY))
        self.assertEqual(result['stop']['kind'],'sfc_disabled_flash_access')

    def test_ram_data_load_cannot_read_disabled_flash(self):
        regs=[0]*16;regs[0]=ENTRY
        result=explore(self.module,self.app,name='flash_data_gate',handoff=self.handoff(),
            hardware=BR28Model(sfc_read_bus=True,initial_sfc_control=0),
            diagnostics={'entry_pc':0x12e414,'initial_sp':STACK_TOP,'initial_gprs':regs},budget=1)
        self.assertEqual(result['completed_steps'],0)
        self.assertEqual(result['stop']['kind'],'sfc_disabled_flash_access')

    def test_real_ram_flash_reconfiguration_sequence(self):
        regs=[0]*16;regs[0]=SFC_BASE;regs[4]=3;regs[5]=2
        result=explore(self.module,self.app,name='real_sfc_reconfiguration',handoff=self.handoff(),
            hardware=BR28Model(sfc_read_bus=True,initial_sfc_control=1),
            diagnostics={'entry_pc':0x12e1e8,'initial_sp':STACK_TOP,'initial_gprs':regs,
                         'exact_parallel_stores':True},budget=7)
        self.assertEqual(result['completed_steps'],7)
        bus=result['peripherals']['sfc_read_bus']
        self.assertEqual((bus['con'],bus['baud'],bus['writes']),(0x200001,3,4))

    def test_exact_stack_word_store_uses_scaled_offset(self):
        h=self.handoff();pc=0x01c60000;sp=0x123700
        h['readonly'].append((pc,bytes.fromhex('a03f00000000')))  # confirmed st.w.sp [sp+252]=r0
        h['snapshots'].append(('stored_word',sp+252,4))
        regs=[0]*16;regs[0]=0x11223344
        result=explore(self.module,self.app,name='stack_word',handoff=h,
            diagnostics={'entry_pc':pc,'initial_sp':sp,'initial_gprs':regs,'exact_parallel_stores':True},budget=1)
        self.assertEqual(result['completed_steps'],1)
        self.assertEqual(result['memory_snapshots']['stored_word']['bytes_with_unknowns'],'44332211')

    def test_indexed_word_store_does_not_read_uninitialized_destination(self):
        h=self.handoff();h['snapshots'].append(('indexed_word',0x124f14,4))
        regs=[0]*16;regs[4]=0x124f00;regs[13]=20;regs[0]=0xdeadbeef
        result=explore(self.module,self.app,name='indexed_word',handoff=h,
            diagnostics={'entry_pc':0x0606577a,'initial_sp':STACK_TOP,'initial_gprs':regs,
                         'exact_parallel_stores':True},budget=1)
        self.assertEqual(result['completed_steps'],1)
        self.assertEqual(result['memory_snapshots']['indexed_word']['bytes_with_unknowns'],'efbeadde')

    def test_halfword_store_leaves_unwritten_neighbours_unknown(self):
        h=self.handoff();h['snapshots'].append(('halfword_and_neighbours',0x124f0c,4))
        regs=[0]*16;regs[4]=0x124f00;regs[0]=0xabcd
        result=explore(self.module,self.app,name='exact_halfword',handoff=h,
            diagnostics={'entry_pc':0x06066cb4,'initial_sp':STACK_TOP,'initial_gprs':regs,
                         'exact_subword_stores':True},budget=1)
        self.assertEqual(result['completed_steps'],1)
        self.assertEqual(result['memory_snapshots']['halfword_and_neighbours']['bytes_with_unknowns'],'cdab????')

    def test_actual_immediate_repeat_executes_seventeen_word_stores(self):
        h=self.handoff();base=0x124f00
        h['snapshots'] += [('repeat_extent',base,72),('repeat_head',base,8),('repeat_tail',base+64,8)]
        regs=[0]*16;regs[0]=base;regs[1]=0x11223344;regs[2]=base
        result=explore(self.module,self.app,name='immediate_repeat',handoff=h,
            diagnostics={'entry_pc':0x06066cf4,'initial_sp':STACK_TOP,'initial_gprs':regs,
                         'immediate_rep_projection':True},budget=18)
        self.assertEqual(result['completed_steps'],18)
        self.assertEqual(result['stop_pc'],'0x6066cf8')
        self.assertEqual(result['last_registers']['r'][2],hex(base+68))
        self.assertEqual(result['memory_snapshots']['repeat_extent']['initialized_bytes'],68)
        self.assertEqual(result['memory_snapshots']['repeat_head']['bytes_with_unknowns'],'4433221144332211')
        self.assertEqual(result['memory_snapshots']['repeat_tail']['bytes_with_unknowns'],'44332211????????')

    def test_actual_halfword_repeat_preserves_stack_neighbours(self):
        h=self.handoff();base=0x123702
        h['snapshots'] += [('halfword_repeat',base-2,18)]
        regs=[0]*16;regs[0]=base;regs[6]=0xffffffff
        result=explore(self.module,self.app,name='halfword_repeat',handoff=h,
            diagnostics={'entry_pc':0x0606891c,'initial_sp':STACK_TOP,'initial_gprs':regs,
                         'immediate_rep_projection':True,'exact_subword_stores':True},budget=8)
        self.assertEqual(result['completed_steps'],8)
        self.assertEqual(result['stop_pc'],'0x6068920')
        self.assertEqual(result['last_registers']['r'][0],hex(base+14))
        self.assertEqual(result['memory_snapshots']['halfword_repeat']['bytes_with_unknowns'],
                         '????'+'ffff'*7+'????')

    def test_virtual_remap_cannot_execute_stale_decoded_instruction(self):
        m=self.module;table=m.table.load()
        def encode(name,**fields):return m.codec.encode(table.by_id(name),fields)
        program=encode('st.h',d=0,b=1,n=0)+encode('goto.r',x=2)+bytes(8)
        old=encode('li.u8',r=3,n=1)+bytes(6)
        new=encode('li.u8',r=3,n=2)+bytes(6)
        h={'regions':[(0x120000,0x120002,'table'),(0x130000,0x130008,'old_code'),
                      (0x130080,0x130088,'new_code')], 'readonly':[],
           'seeds':[(0x120000,struct.pack('<H',0xa600)),(0x130000,old),(0x130080,new)],
           'snapshots':[('pte',0x120000,2)],
           'description':{'phase':'isolated synthetic MMU decoder cache test'}}
        regs=[0]*16;regs[0]=0xa601;regs[1]=0x120000;regs[2]=0x400000
        hardware=BR28Model(mmu_model=True,initial_mmu_con=0)
        hardware.write(MMU_BASE+4,0x120000);hardware.write(MMU_BASE+8,0x120001);hardware.write(MMU_BASE,1)
        result=explore(m,program,name='virtual_redecode',hardware=hardware,handoff=h,
            diagnostics={'initial_gprs':regs,'predecode_pcs':[0x400000],'exact_subword_stores':True},budget=3)
        self.assertEqual(result['completed_steps'],3)
        self.assertEqual(result['last_registers']['r'][3],'0x2')
        self.assertEqual(result['last_instructions'][-1]['text'],'r3 = 2')
        self.assertEqual(result['last_instructions'][-1]['bytes'],new[:2].hex())

    def guarded_hardware(self,flash_low,flash_high,enabled=True):
        hardware=BR28Model(debug_execution_projection=True,initial_debug_controls=(0,None))
        for i,(low,high) in enumerate(((flash_low,flash_high),(0x12a640,0x12ef14),(4,0x20000))):
            hardware.write(0xffe218+8*i,high);hardware.write(0xffe21c+8*i,low)
        if enabled:hardware.write(EMU_CON,0xe000001f)
        return hardware

    def test_cached_instruction_must_obey_newly_enabled_pc_guard(self):
        regs=[0]*16;regs[1]=0xe000001f;regs[2]=EMU_CON
        result=explore(self.module,self.app,name='cached_pc_guard',handoff=self.handoff(),
            hardware=self.guarded_hardware(ENTRY+2,ENTRY+128,enabled=False),
            diagnostics={'entry_pc':0x12e41e,'initial_sp':STACK_TOP,'initial_rets':ENTRY,
                         'initial_gprs':regs,'exact_parallel_stores':True,'predecode_pcs':[ENTRY]},budget=4)
        self.assertEqual(result['completed_steps'],2)
        self.assertEqual(result['stop_pc'],hex(ENTRY))
        self.assertEqual(result['stop']['kind'],'projected_pc_range_violation')

    def test_data_read_is_not_an_executing_pc_range_check(self):
        regs=[0]*16;regs[0]=ENTRY
        result=explore(self.module,self.app,name='unguarded_data_read',handoff=self.handoff(),
            hardware=self.guarded_hardware(ENTRY+8,ENTRY+128),
            diagnostics={'entry_pc':0x12e414,'initial_sp':STACK_TOP,'initial_gprs':regs},budget=1)
        self.assertEqual(result['completed_steps'],1)
        self.assertEqual(result['last_registers']['r'][1],hex(self.app[0]))

    def test_pc_guard_checks_entire_parallel_instruction_before_effects(self):
        regs=[0]*16;regs[0]=0xffff;regs[5]=0xfd6000
        pc=0x060689d2
        result=explore(self.module,self.app,name='parallel_range_crossing',handoff=self.handoff(),
            hardware=self.guarded_hardware(pc,pc+4),
            diagnostics={'entry_pc':pc,'initial_sp':STACK_TOP,'initial_gprs':regs,'exact_parallel_stores':True},budget=1)
        self.assertEqual(result['completed_steps'],0)
        self.assertEqual(result['stop']['kind'],'projected_pc_range_violation')
        self.assertEqual(result['stop']['size'],6)

    def test_actual_task_frame_doubleword_store_preserves_unknown_gpr_slots(self):
        h=self.handoff();base=0x124f00
        h['snapshots'].append(('frame_head',base,24))
        regs=[0]*16;regs[0]=0x06012028;regs[1]=0;regs[2]=base
        result=explore(self.module,self.app,name='frame_doubleword',handoff=h,
            diagnostics={'entry_pc':0x06065f9e,'initial_sp':STACK_TOP,'initial_gprs':regs,
                         'exact_doubleword_stores':True},budget=1)
        self.assertEqual(result['completed_steps'],1)
        self.assertEqual(result['memory_snapshots']['frame_head']['bytes_with_unknowns'],
                         '2820010600000000'+'??'*16)

    def test_second_p33_return_stays_opaque_through_call_and_configuration(self):
        def run(budget,mutation=None):
            h=self.handoff();sp=STACK_TOP-64
            h['seeds'].append((sp,struct.pack('<6I',0,0,0,0,0,0x06035306)))
            regs=[0]*16;regs[8]=0xf2c120;app=self.app
            if mutation is not None:
                app=bytearray(app);app[mutation-ENTRY:mutation-ENTRY+2]=bytes.fromhex('0416')
            return explore(self.module,bytes(app),name='second_p33_return',handoff=h,
                hardware=BR28Model(reset_history_projection=True),
                diagnostics={'entry_pc':0x0606745e,'initial_sp':sp,'initial_gprs':regs,'opaque_pin_read':True},budget=budget)
        for budget in (1,2,5):self.assertEqual(run(budget)['last_registers']['r'][0],'unknown')
        self.assertEqual(run(7)['last_registers']['r'][0],'0x3fdf')
        for pc,steps in ((0x06035306,2),(0x06068c88,5),(0x06068c8c,6)):
            result=run(10,mutation=pc)
            self.assertEqual(result['completed_steps'],steps)
            self.assertIn('opaque pin0 return would be consumed',result['stop']['message'])

    def pwm_case(self,budget,caller=0x060006e2,consume=False):
        h=self.handoff();sp=STACK_TOP-64
        h['seeds'] += [(sp,struct.pack('<III',0,0x124ff4,caller)),
                       (0x125000,struct.pack('<I',0x06034a8c))]
        regs=[0]*16;regs[4]=3;regs[5]=0xfd2994;regs[6]=0x125004
        app=self.app
        if consume:
            # Same length, now consumes opaque R0: the guard must reject it.
            app=bytearray(app);app[0x06034a84-ENTRY:0x06034a86-ENTRY]=bytes.fromhex('0416')
        return explore(self.module,bytes(app),name='isolated_pwm_dead_return',handoff=h,
            hardware=BR28Model(),diagnostics={'entry_pc':0x06034a82,'initial_sp':sp,'initial_gprs':regs,
                                             'opaque_pwm_read':True},budget=budget)

    def test_pwm_value_stays_unknown_until_actual_overwrite(self):
        for budget in (1,4,6):
            result=self.pwm_case(budget)
            self.assertEqual(result['last_registers']['r'][0],'unknown')
        self.assertEqual(self.pwm_case(7)['last_registers']['r'][0],'0x6034a8c')

    def test_pwm_opaque_consumer_or_unproved_caller_is_rejected(self):
        result=self.pwm_case(3,consume=True)
        self.assertEqual(result['completed_steps'],1)
        self.assertIn('opaque PWM value would be consumed',result['stop']['message'])
        result=self.pwm_case(1,caller=0x060006e8)
        self.assertEqual(result['completed_steps'],0)
        self.assertIn('PWM return might be consumed',result['stop']['message'])


if __name__=='__main__':unittest.main()
