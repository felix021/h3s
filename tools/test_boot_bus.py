"""Isolated instruction tests for the SFC gate, stack stores, opaque PWM read and P11 responses.

No private ROM, device access, vendor compiler or firmware output required.
"""
from pathlib import Path
import struct
import unittest

from br28_model import BR28Model, SFC_BASE, MMU_BASE, EMU_CON, PWR_CON, P11_HEAP_INFO
from br28_machine import DualCoreMachine
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

    def test_power_init_lpwr_request_rmw_projects_only_with_the_response(self):
        # 实际 power_init 指令 0x60679a0(rmw.or.modimm [r4] |=0x20,r4=PWR_CON)。
        regs=[0]*16;regs[4]=PWR_CON
        strict=BR28Model(reset_history_projection=True,reset_history_words={PWR_CON:0x80})
        result=explore(self.module,self.app,name='lpwr_request_strict',handoff=self.handoff(),
            hardware=strict,diagnostics={'entry_pc':0x60679a0,'initial_sp':STACK_TOP,'initial_gprs':regs},budget=4)
        self.assertEqual(result['completed_steps'],0)
        self.assertEqual(result['stop_pc'],'0x60679a0')
        self.assertEqual(result['stop']['kind'],'reset_flag_command_not_modeled')
        projected=BR28Model(reset_history_projection=True,reset_history_words={PWR_CON:0x80},
                            p11_lpwr_response_projection=True)
        result=explore(self.module,self.app,name='lpwr_request_projected',handoff=self.handoff(),
            hardware=projected,diagnostics={'entry_pc':0x60679a0,'initial_sp':STACK_TOP,'initial_gprs':regs},budget=4)
        # 请求 RMW 完成;停在下一个尚未建模的 q32s PMU_CON(0xf2a0c0)释放读。
        self.assertEqual(result['completed_steps'],2)
        self.assertEqual(result['stop']['kind'],'unmapped_memory')
        self.assertEqual(result['stop']['address'],'0xf2a0c0')
        self.assertEqual(projected.p11_lp.requests,1)

    def test_power_init_bit5_poll_exits_only_after_the_observed_request(self):
        # 实际轮询 0x60679ae(ld.w [r4])/0x60679b0(bne.and.ri r0,bit5)。快照显式含
        # bit5 的反事实下:无请求时死循环;记录真实请求后同一路径退出循环。
        regs=[0]*16;regs[4]=PWR_CON
        waiting=BR28Model(reset_history_projection=True,reset_history_words={PWR_CON:0xa0},
                          p11_lpwr_response_projection=True)
        result=explore(self.module,self.app,name='lpwr_poll_waiting',handoff=self.handoff(),
            hardware=waiting,diagnostics={'entry_pc':0x60679ae,'initial_sp':STACK_TOP,'initial_gprs':regs},budget=6)
        self.assertEqual(result['completed_steps'],6)
        self.assertEqual(result['stop']['kind'],'instruction_budget')
        requested=BR28Model(reset_history_projection=True,reset_history_words={PWR_CON:0xa0},
                            p11_lpwr_response_projection=True)
        requested.p11_lpwr_rmw(PWR_CON,'rmw.or.modimm',0x20)
        result=explore(self.module,self.app,name='lpwr_poll_released',handoff=self.handoff(),
            hardware=requested,diagnostics={'entry_pc':0x60679ae,'initial_sp':STACK_TOP,'initial_gprs':regs},budget=2)
        self.assertEqual(result['completed_steps'],2)
        self.assertEqual(result['stop_pc'],'0x60679b4')  # bne 未跳,走到堆信息装载
        self.assertEqual(result['last_registers']['r'][0],'0x80')  # bit5=0,bit7 保留

    def test_power_init_heap_info_byte_loads_need_the_explicit_fixture(self):
        # 实际堆信息装载 0x60679ba(ld.bu.l [r4+1])/0x60679be(ld.bu [r4])。
        regs=[0]*16;regs[4]=P11_HEAP_INFO
        result=explore(self.module,self.app,name='heap_info_strict',handoff=self.handoff(),
            hardware=BR28Model(),diagnostics={'entry_pc':0x60679ba,'initial_sp':STACK_TOP,'initial_gprs':regs},budget=1)
        self.assertEqual(result['completed_steps'],0)
        self.assertEqual(result['stop']['kind'],'unmapped_memory')
        self.assertEqual(result['stop']['address'],hex(P11_HEAP_INFO+1))
        fixed=BR28Model(p11_heap_info_bytes=bytes.fromhex('78563412'))
        result=explore(self.module,self.app,name='heap_info_fixture',handoff=self.handoff(),
            hardware=fixed,diagnostics={'entry_pc':0x60679ba,'initial_sp':STACK_TOP,'initial_gprs':regs},budget=2)
        self.assertEqual(result['completed_steps'],2)
        self.assertEqual(result['last_registers']['r'][8],'0x56')
        self.assertEqual(result['last_registers']['r'][5],'0x78')

    def test_multi_instruction_rep_block_copies_bytes_like_semantic_expansion(self):
        # 实际资源表拷贝指令 0x6010b14 rep 4 32(4 字节体 ld.bu.pin+st.b.pin,
        # 重复 32 次)。开 immediate_rep_multi_block 后逐条真实执行:内存效果
        # 必须等于语义展开(把源 32 字节逐一搬到目的,两个基址寄存器各 +32),
        # 1 步 rep.i + 32×2 步体指令 = 65 步,PC 停在体后的 0x6010b1a。
        h=self.handoff();src,dst=0x124f80,0x124f00
        payload=bytes(range(0x21,0x41))
        h['seeds'].append((src,payload))
        h['snapshots'] += [('rep_multi_src',src,32),('rep_multi_dst',dst,32)]
        regs=[0]*16;regs[0]=dst;regs[1]=src
        result=explore(self.module,self.app,name='rep_multi_block',handoff=h,
            diagnostics={'entry_pc':0x06010b14,'initial_sp':STACK_TOP,'initial_gprs':regs,
                         'immediate_rep_projection':True,'immediate_rep_multi_block':True,
                         'exact_subword_stores':True},budget=65)
        self.assertEqual(result['completed_steps'],65)
        self.assertEqual(result['stop_pc'],'0x6010b1a')
        self.assertEqual(result['last_registers']['r'][0],hex(dst+32))
        self.assertEqual(result['last_registers']['r'][1],hex(src+32))
        self.assertEqual(result['memory_snapshots']['rep_multi_dst']['bytes_with_unknowns'],payload.hex())
        self.assertEqual(result['memory_snapshots']['rep_multi_src']['bytes_with_unknowns'],payload.hex())
        self.assertEqual(result['instruction_adapters'].get('immediate_rep_multi_block_projection'),1)

    def test_multi_instruction_rep_block_stays_opt_in(self):
        # 不开 immediate_rep_multi_block 时同一指令必须诚实停在
        # unsupported_runtime_instruction(rep.i),legacy 行为不变。
        h=self.handoff();src,dst=0x124f80,0x124f00
        h['seeds'].append((src,bytes(32)))
        regs=[0]*16;regs[0]=dst;regs[1]=src
        result=explore(self.module,self.app,name='rep_multi_block_off',handoff=h,
            diagnostics={'entry_pc':0x06010b14,'initial_sp':STACK_TOP,'initial_gprs':regs,
                         'immediate_rep_projection':True,'exact_subword_stores':True},budget=65)
        self.assertEqual(result['completed_steps'],0)
        self.assertEqual(result['stop_pc'],'0x6010b14')
        self.assertEqual(result['stop']['kind'],'unsupported_runtime_instruction')
        self.assertEqual(result['stop']['instruction_id'],'rep.i')

    def test_multi_instruction_rep_block_rejects_control_flow_body(self):
        # 实际 IRQ-disabled helper 块 0x12cb12 rep 24 2:24 字节体内含条件块
        # 开启器 if.eq.ri.modimm(0x12cb22)。多指令体投影必须诚实拒绝——REP
        # 与体内控制流的交互没有硬件捕获证据(该块由 irq_repeat_return 特例
        # 单独处理,此处不启用该特例)。
        regs=[0]*16
        result=explore(self.module,self.app,name='rep_multi_block_ctlflow',handoff=self.handoff(),
            diagnostics={'entry_pc':0x12cb12,'initial_sp':STACK_TOP,'initial_gprs':regs,
                         'immediate_rep_projection':True,'immediate_rep_multi_block':True},budget=1)
        self.assertEqual(result['completed_steps'],0)
        self.assertEqual(result['stop_pc'],'0x12cb12')
        self.assertEqual(result['stop']['kind'],'rep_multi_block_body_not_straight_line')
        self.assertEqual(result['stop']['instruction_id'],'if.eq.ri.modimm')
        self.assertEqual(result['stop']['address'],'0x12cb22')

    # ROM 0xc70c-0xc718 的精确编码(flushinv_dcache 0xc71a→0xc6f4 的 DCU CMO
    # 失效循环):寄存器计数 rep r1 体 4 字节 = add.rrr.p(r0+=r3)+st.w [r2]=旧 r0
    # 并行对,体后 bnez r1 回卷到 rep,尾 csync+ssync(解码窗口读 6 字节,
    # 附带尾部保证只读区覆盖)。按字节钉在只读区,不依赖 ROM 文件。
    DCU_REP_LOOP=bytes.fromhex('110388dca060f15c20002000')

    def dcu_rep_hardware(self):
        # 0xff05cc=CMO[19](flushinv_dcache 口);0xff05c8 一并建模但本循环不写。
        return BR28Model(dcu_cmo_projection=True)

    def test_register_count_rep_pair_body_runs_the_dcu_invalidate_loop(self):
        # 寄存器计数 rep 推广(immediate_rep_multi_block 扩展):计数来自真实
        # 寄存器值,计数寄存器按 FM-1 run3 硬件证据递减到 0(体后 bnez 自然
        # 不跳);并行对体按 exact_parallel_stores 的旧寄存器语义执行——存储半
        # 读 A 之前的旧 r0,所以写入序列是行地址 0x061d7fe0/0x061d8000/
        # 0x061d8020(步长 32),r0 末值 0x061d8040,r1 归 0。1 步 rep + 3 步
        # 并行对 + 1 步 bnez = 5 步。
        pc=0x01c60000
        h=self.handoff();h['readonly'].append((pc,self.DCU_REP_LOOP))
        regs=[0]*16;regs[0]=0x061d7fe0;regs[1]=3;regs[2]=0xff05cc;regs[3]=0x20
        result=explore(self.module,self.app,name='dcu_rep_loop',handoff=h,
            hardware=self.dcu_rep_hardware(),
            diagnostics={'entry_pc':pc,'initial_sp':STACK_TOP,'initial_gprs':regs,
                         'immediate_rep_multi_block':True,'exact_parallel_stores':True},budget=5)
        self.assertEqual(result['completed_steps'],5)
        self.assertEqual(result['stop_pc'],hex(pc+8))
        self.assertEqual(result['stop']['kind'],'instruction_budget')
        self.assertEqual(result['last_registers']['r'][0],hex(0x061d8040))
        self.assertEqual(result['last_registers']['r'][1],'0x0')
        dcu=result['peripherals']['dcu_cmo_projection']
        self.assertEqual(dcu['ports']['0xff05cc'],
                         {'values':['0x61d7fe0','0x61d8000','0x61d8020'],'writes':3})
        self.assertEqual(dcu['ports']['0xff05c8'],{'values':[],'writes':0})
        self.assertEqual(result['instruction_adapters'].get('register_rep_multi_block_projection'),1)
        self.assertEqual(result['instruction_adapters'].get('parallel_full_word_store_old_registers'),3)

    def test_register_count_rep_stays_opt_in_and_rejects_risky_counts(self):
        # 不开 immediate_rep_multi_block:同一指令诚实停在 unsupported_runtime_
        # instruction(rep),legacy 行为不变。开了 gate 但并行对执行路径
        # (exact_parallel_stores)未开:体形状拒绝。计数寄存器为 0:BR28 未测量
        # (GAPS.md「Not tried: rep with a count of 0」),诚实停。
        pc=0x01c60000
        h=self.handoff();h['readonly'].append((pc,self.DCU_REP_LOOP))
        regs=[0]*16;regs[0]=0x061d7fe0;regs[1]=3;regs[2]=0xff05cc;regs[3]=0x20
        result=explore(self.module,self.app,name='dcu_rep_off',handoff=h,
            hardware=self.dcu_rep_hardware(),
            diagnostics={'entry_pc':pc,'initial_sp':STACK_TOP,'initial_gprs':regs,
                         'exact_parallel_stores':True},budget=5)
        self.assertEqual(result['completed_steps'],0)
        self.assertEqual(result['stop']['kind'],'unsupported_runtime_instruction')
        self.assertEqual(result['stop']['instruction_id'],'rep')
        result=explore(self.module,self.app,name='dcu_rep_no_pair_gate',handoff=h,
            hardware=self.dcu_rep_hardware(),
            diagnostics={'entry_pc':pc,'initial_sp':STACK_TOP,'initial_gprs':regs,
                         'immediate_rep_multi_block':True},budget=5)
        self.assertEqual(result['completed_steps'],0)
        self.assertEqual(result['stop']['kind'],'rep_multi_block_body_parallel_pair_unsupported')
        self.assertEqual(result['stop']['instruction_id'],'add.rrr.p')
        self.assertEqual(result['stop']['pair_id'],'st.w')
        regs[1]=0
        result=explore(self.module,self.app,name='dcu_rep_zero',handoff=h,
            hardware=self.dcu_rep_hardware(),
            diagnostics={'entry_pc':pc,'initial_sp':STACK_TOP,'initial_gprs':regs,
                         'immediate_rep_multi_block':True,'exact_parallel_stores':True},budget=5)
        self.assertEqual(result['completed_steps'],0)
        self.assertEqual(result['stop']['kind'],'unverified_zero_count_rep')

    def test_zero_count_rep_skip_generalizes_the_memset_precedent(self):
        # 零计数跳过假设(opt-in zero_count_rep_skip):memset 先例
        # rom_zero_rep_skip(0x4242/0x424c)推广到 multi-block gate 的寄存器
        # 计数形式——count=0 时体一次也不执行、PC 前进到体后、计数寄存器保持
        # 0(FM-1 run3「递减到 0」在 0 次执行下的自然延伸,count=0 本身 BR28
        # 未测量,zero_count_skip_is_assumption 显式标注不声称硅验证)。默认
        # 关闭时的诚实停由 test_register_count_rep_stays_opt_in_and_rejects_
        # risky_counts 的 dcu_rep_zero 子例钉死。体形状仍须先通过与非零计数
        # 完全相同的直线体校验(此处:并行对要求 exact_parallel_stores)。
        pc=0x01c60000
        h=self.handoff();h['readonly'].append((pc,self.DCU_REP_LOOP))
        regs=[0]*16;regs[0]=0x061d7fe0;regs[1]=0;regs[2]=0xff05cc;regs[3]=0x20
        result=explore(self.module,self.app,name='dcu_rep_zero_skip',handoff=h,
            hardware=self.dcu_rep_hardware(),
            diagnostics={'entry_pc':pc,'initial_sp':STACK_TOP,'initial_gprs':regs,
                         'immediate_rep_multi_block':True,'exact_parallel_stores':True,
                         'zero_count_rep_skip':True},budget=2)
        # 1 步跳过(rep→体后 bnez)+ 1 步 bnez(r1=0 不跳)= 2 步,体未执行。
        self.assertEqual(result['completed_steps'],2)
        self.assertEqual(result['stop_pc'],hex(pc+8))
        self.assertEqual(result['last_registers']['r'][1],'0x0')
        self.assertEqual(result['last_registers']['r'][0],hex(0x061d7fe0))
        self.assertEqual(result['instruction_adapters'].get('zero_count_skip_is_assumption'),1)
        self.assertNotIn('register_rep_multi_block_projection',result['instruction_adapters'])
        dcu=result['peripherals']['dcu_cmo_projection']
        self.assertEqual(dcu['ports']['0xff05cc'],{'values':[],'writes':0},
                         'the skipped REP body must not touch memory')

    def test_ssync_executes_as_the_run4_no_visible_effect_model(self):
        # flushinv_dcache 尾部 0xc714 csync + 0xc716 ssync(ROM 实际编码)。ssync
        # 上游只有 run4 的 runner 上下文 trace 模型;按 FM-1 run4 捕获「flags
        # 0/15 下无任何可见寄存器/标志/sp/rets 变化」以无效果模型执行,顺序/
        # 屏障语义显式不建模(报告键只在真实执行到的前缀出现)。
        pc=0x01c60010
        h=self.handoff();h['readonly'].append((pc,bytes.fromhex('2000220020002000')))
        regs=[0]*16;regs[0]=0x1234
        result=explore(self.module,self.app,name='ssync_noop',handoff=h,
            diagnostics={'entry_pc':pc,'initial_sp':STACK_TOP,'initial_gprs':regs},budget=2)
        self.assertEqual(result['completed_steps'],2)
        self.assertEqual(result['stop_pc'],hex(pc+4))
        self.assertEqual(result['last_registers']['r'][0],'0x1234')
        self.assertEqual(result['instruction_adapters'].get('ssync_noop'),1)
        self.assertIn('ssync_noop_model',result['instruction_model_corrections'])

    def test_boot_info_rep_copy_transports_padding_unknowns(self):
        # 实际 boot_info 头拷贝 0x600a352 rep 4 4(r3=[r2++=4]/[r1++=4]=r3,
        # 重复 4 次)。源 0x122800 按真机状态部分初始化:byte0=0x10(vm.align,
        # 启动拷贝例程 0x60664ee 写入)、word1=0x1da000(vm.vm_saddr)与
        # word3=0x4000(vm.vm_size)(VM init 0x6011102/08 写入),bytes 1-3
        # (vm_info 的 C 对齐 padding)与 8-11(vm_res 保留字段)无任何写入者。
        # 开 rep_copy_unknown_transport 后:已知字节照搬、未知字节按来源运输
        # (不臆造数值),1 步 rep.i + 4×2 步体 = 9 步,r3 末值为已知的
        # vm_size,r1/r2 各 +16。
        h=self.handoff();src,dst=0x122800,0x124f00
        h['seeds'] += [(src,b'\x10'),(src+4,struct.pack('<I',0x1da000)),
                       (src+12,struct.pack('<I',0x4000))]
        h['snapshots'].append(('boot_info_copy_dst',dst,16))
        regs=[0]*16;regs[1]=dst;regs[2]=src
        result=explore(self.module,self.app,name='rep_copy_transport',handoff=h,
            diagnostics={'entry_pc':0x0600a352,'initial_sp':STACK_TOP,'initial_gprs':regs,
                         'immediate_rep_projection':True,'immediate_rep_multi_block':True,
                         'rep_copy_unknown_transport':True},budget=9)
        self.assertEqual(result['completed_steps'],9)
        self.assertEqual(result['stop_pc'],'0x600a358')
        self.assertEqual(result['last_registers']['r'][1],hex(dst+16))
        self.assertEqual(result['last_registers']['r'][2],hex(src+16))
        self.assertEqual(result['last_registers']['r'][3],hex(0x4000))
        self.assertEqual(result['memory_snapshots']['boot_info_copy_dst']['bytes_with_unknowns'],
                         '10??????'+'00a01d00'+'????????'+'00400000')
        self.assertEqual(result['instruction_adapters'].get('rep_copy_unknown_transport_load'),2)
        self.assertEqual(result['instruction_adapters'].get('rep_copy_unknown_transport_store'),2)

    def test_boot_info_rep_copy_transport_stays_opt_in(self):
        # 同一指令不开 rep_copy_unknown_transport 时必须诚实停在体首装载的
        # 未初始化读上(旧前沿 162,615 的精确形状),legacy 行为不变。
        h=self.handoff();src,dst=0x122800,0x124f00
        h['seeds'].append((src,b'\x10'))
        regs=[0]*16;regs[1]=dst;regs[2]=src
        result=explore(self.module,self.app,name='rep_copy_transport_off',handoff=h,
            diagnostics={'entry_pc':0x0600a352,'initial_sp':STACK_TOP,'initial_gprs':regs,
                         'immediate_rep_projection':True,'immediate_rep_multi_block':True},budget=9)
        self.assertEqual(result['completed_steps'],1)
        self.assertEqual(result['stop_pc'],'0x600a354')
        self.assertEqual(result['stop'],{'kind':'uninitialized_memory','access':'read',
            'address':'0x122800','size':4,'region':'qcy_boot_info_destination',
            'first_missing':'0x122801'})

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

    def test_parallel_extension_stores_to_uninitialized_stack_without_merge_read(self):
        h=self.handoff();sp=STACK_TOP-128
        h['snapshots'].append(('parallel_stack_word',sp,4))
        regs=[0]*16;regs[0]=0xfe123456;regs[4]=0xdeadbeef
        result=explore(self.module,self.app,name='extension_parallel_store',handoff=h,
            diagnostics={'entry_pc':0x06011530,'initial_sp':sp,'initial_gprs':regs,
                         'exact_parallel_stores':True,'exact_parallel_extensions':True},budget=1)
        self.assertEqual(result['completed_steps'],1)
        self.assertEqual(result['last_registers']['r'][1],'0x56')
        self.assertEqual(result['memory_snapshots']['parallel_stack_word']['bytes_with_unknowns'],'efbeadde')

    def test_first_task_partial_restore_does_not_zero_or_half_restore_unknown_gprs(self):
        h=self.handoff();tcb=0x124f00;frame=0x124f40
        h['seeds'] += [(tcb,struct.pack('<I',frame)),(frame,struct.pack('<IIII',ENTRY,0,0,0))]
        regs=[0]*16;regs[0]=0x12345678;regs[4]=tcb
        def run(budget):
            return explore(self.module,self.app,name='selected_task_frame',handoff=h,
                hardware=BR28Model(cpu1_held_projection=True,initial_cpu1_control=0),machine=DualCoreMachine(),
                diagnostics={'entry_pc':0x06066120,'initial_sp':STACK_TOP,'initial_gprs':regs,'icfg_initial':0},budget=budget)
        r=run(3)
        self.assertEqual(r['completed_steps'],3)
        self.assertEqual(r['last_registers']['sp'],hex(frame+12))
        self.assertEqual(r['machine']['cores']['0']['reti'],hex(ENTRY))
        r=run(4)
        self.assertEqual(r['completed_steps'],3)
        self.assertEqual(r['stop_pc'],'0x6066128')
        self.assertEqual(r['stop']['kind'],'uninitialized_memory')
        self.assertEqual(r['last_registers']['r'][0],'0x12345678')
        self.assertEqual(r['last_registers']['sp'],hex(frame+12))

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

    # ---- bank B 头 0xFF fixture(opt-in erased_bankb,机制层钉子)----

    def erased_bankb_case(self,ptr,*,fixture=True,store=False):
        h=self.handoff()
        h['description']['phase']='erased bank-B fixture mechanism test'
        if fixture:h['readonly'].append((0x60fdfe0,b'\xff'*32))
        # 真实 app 指令:0x6010fc8 `r2=[r0++=4]`(rep 4 8 体内的字装载)、0x6010fca
        # `[r1++=4]=r2`(配套字存储)——find_partition 拷贝路径用的同一指令形状。
        regs=[0]*16;regs[0]=ptr;regs[1]=ptr;regs[2]=0x12345678
        return explore(self.module,self.app,name='erased_bankb_fixture',handoff=h,
            diagnostics={'entry_pc':0x6010fca if store else 0x6010fc8,'initial_sp':STACK_TOP,
                         'initial_rets':ENTRY,'initial_gprs':regs},budget=1)

    def test_erased_bankb_fixture_reads_back_0xff_words(self):
        # 区内(含末字 0x60fe000-4)读出 0xffffffff,post-increment 正常递增。
        for ptr in (0x60fdfe0,0x60fe000-4):
            result=self.erased_bankb_case(ptr)
            self.assertEqual(result['completed_steps'],1)
            self.assertEqual(result['stop']['kind'],'instruction_budget')
            self.assertEqual(result['last_registers']['r'][2],'0xffffffff')
            self.assertEqual(result['last_registers']['r'][0],hex(ptr+4))

    def test_erased_bankb_fixture_bounds_and_default_stay_strict(self):
        # fixture 只覆盖 32 字节头读取窗:紧邻区外(下界 0x60fdfdc/上界 0x60fe000)
        # 与默认(不挂 fixture)都继续严格停 unmapped——bank B 其余字节不臆造。
        for ptr in (0x60fdfdc,0x60fe000):
            result=self.erased_bankb_case(ptr)
            self.assertEqual(result['completed_steps'],0)
            self.assertEqual(result['stop'],{'kind':'unmapped_memory','access':'read',
                'address':hex(ptr),'size':4,'reference_sdk_register':None})
        result=self.erased_bankb_case(0x60fdfe0,fixture=False)
        self.assertEqual(result['stop']['kind'],'unmapped_memory')

    def test_erased_bankb_fixture_is_read_only(self):
        result=self.erased_bankb_case(0x60fdfe0,store=True)
        self.assertEqual(result['completed_steps'],0)
        self.assertEqual(result['stop'],{'kind':'unmapped_or_readonly_write','access':'write',
            'address':'0x60fdfe0','size':4,'value_bytes':'78563412'})

    def test_erased_bankb_header_crc_can_never_match(self):
        # 黄金算术钉子:消费者(find_partition 0x6010f32)在 0x6010f80 比较
        # crc16(头 bytes 2..31) 与头前 2 字节期望值;0xFF 擦除态下期望=0xffff,
        # 而按 app 软件 CRC-16 参数(多项式 0x1021、MSB-first、初值=种子 0、
        # 无输出异或)计算 30 个 0xff 的结果=0x75f8——既非 0(0x6010f78 的
        # 「CRC==0 视为无效」分支)也非 0xffff,必然走不匹配→良性跳过分支。
        crc=0
        for byte in b'\xff'*30:
            crc^=byte<<8
            for _ in range(8):
                crc=((crc<<1)^0x1021)&0xffff if crc&0x8000 else (crc<<1)&0xffff
        self.assertEqual(crc,0x75f8)
        self.assertNotIn(crc,(0,0xffff))


if __name__=='__main__':unittest.main()
