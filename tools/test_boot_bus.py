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

    def test_power_init_real_prefix_ordering_flag_clear_request_poll(self):
        # 真实前缀顺序的最小指令场景:主 init 复位原因处理先发 bit6 清标志命令
        # (0x6068cf2 rmw.or.modimm [r0]|=0x40,r0=PWR_CON,门控在 0x6068ce4..e8
        # 的「查 bit7→清」配对),app_core power_init 再 |=0x20 请求(0x60679a0)
        # 并死等 bit5(0x60679ae/b0)。lpwr 投影下:清标志命令落回严格复位标志
        # 路径(不被请求投影拦截),请求后轮询经 post_flag_clear_read 第一轮退出
        # (bit7 按清标志配对推定 0、bit5 按 P11 响应投影 0)。
        regs=[0]*16;regs[0]=PWR_CON;regs[4]=PWR_CON
        h=BR28Model(reset_history_projection=True,reset_history_words={PWR_CON:0x80},
                    p11_lpwr_response_projection=True)
        # ①主 init 清标志:rmw 读快照 0x80、写回 0xC0=期望的 bit6 命令
        result=explore(self.module,self.app,name='lpwr_flag_clear',handoff=self.handoff(),
            hardware=h,diagnostics={'entry_pc':0x6068cf2,'initial_sp':STACK_TOP,'initial_gprs':regs},budget=1)
        self.assertEqual(result['completed_steps'],1)
        self.assertIn(PWR_CON,h.reset.flag_clear_commands)
        # ②清标志后、请求前的读回仍严格停(投影不臆造)
        with self.assertRaisesRegex(Exception,'reset_flag_post_clear_state_unknown'):h.read(PWR_CON)
        # ③真实请求(rmw.or.modimm |=0x20)后轮询(0x60679ae)第一轮退出
        h.p11_lpwr_rmw(PWR_CON,'rmw.or.modimm',0x20)
        result=explore(self.module,self.app,name='lpwr_cleared_poll',handoff=self.handoff(),
            hardware=h,diagnostics={'entry_pc':0x60679ae,'initial_sp':STACK_TOP,'initial_gprs':regs},budget=2)
        self.assertEqual(result['completed_steps'],2)
        self.assertEqual(result['stop_pc'],'0x60679b4')
        self.assertEqual(result['last_registers']['r'][0],'0x0')  # bit7/bit5 都 0

    def test_q32s_release_rmw_and_p11_clock_stores_execute_through_the_projection(self):
        # 实际释放指令 0x60679a8(rmw.or.modimm [r0]|=0x1,r0=0xf2a0c0=q32s
        # PMU_CON,全 app+ROM 唯一引用)与 P11_CLOCK 整字写 0x606791c
        # ([r4+20]=r0,r0=1→CLK_CON0)/0x6067920([r4+16]=r0,r0=0→SYS_DIV)。
        regs=[0]*16;regs[0]=0xf2a0c0;regs[4]=PWR_CON
        result=explore(self.module,self.app,name='q32s_release_strict',handoff=self.handoff(),
            hardware=BR28Model(reset_history_projection=True,reset_history_words={PWR_CON:0x80}),
            diagnostics={'entry_pc':0x60679a8,'initial_sp':STACK_TOP,'initial_gprs':regs},budget=1)
        self.assertEqual(result['completed_steps'],0)
        self.assertEqual(result['stop']['kind'],'unmapped_memory')
        self.assertEqual(result['stop']['address'],'0xf2a0c0')
        projected=BR28Model(reset_history_projection=True,reset_history_words={PWR_CON:0x80},
                            p11_window_projection=True)
        result=explore(self.module,self.app,name='q32s_release_projected',handoff=self.handoff(),
            hardware=projected,diagnostics={'entry_pc':0x60679a8,'initial_sp':STACK_TOP,'initial_gprs':regs},budget=1)
        self.assertEqual(result['completed_steps'],1)
        self.assertEqual(projected.p11_win.control.known,1)
        self.assertEqual(result['instruction_adapters'].get('p11_window_release_rmw'),1)
        # CLK_CON0/SYS_DIV 整字写:真实指令 0x606791c([r4+20]=r0,r0=1)/
        # 0x6067920([r4+16]=r0,r0=0),r4=PWR_CON。
        clk_regs=[0]*16;clk_regs[0]=1;clk_regs[4]=PWR_CON
        result=explore(self.module,self.app,name='p11_clk_con0',handoff=self.handoff(),
            hardware=projected,diagnostics={'entry_pc':0x606791c,'initial_sp':STACK_TOP,'initial_gprs':clk_regs},budget=3)
        self.assertEqual(result['completed_steps'],3)  # 0x606791c/0x606791e/0x6067920
        self.assertEqual(projected.p11_win.stores[0xf2a414]['values'],[1])
        self.assertEqual(projected.p11_win.stores[0xf2a410]['values'],[0])

    def test_power_init_config_rmw_0xf2c05c_executes_only_with_extra_masks(self):
        # 实际配置指令 0x60678f4(rmw.and.r [r7+16] &= r0,r7=0xf2c04c、r0=0x3FD)
        # :默认(即使开 reset 投影)不 handles → 严格停;opt-in extra masks 后
        # 按掩码记录已知位。
        regs=[0]*16;regs[7]=0xf2c04c;regs[0]=0x3fd
        base=BR28Model(reset_history_projection=True,reset_history_words={PWR_CON:0x80})
        result=explore(self.module,self.app,name='pmu_config_strict',handoff=self.handoff(),
            hardware=base,diagnostics={'entry_pc':0x60678f4,'initial_sp':STACK_TOP,'initial_gprs':regs},budget=1)
        self.assertEqual(result['completed_steps'],0)
        self.assertEqual(result['stop']['kind'],'unmapped_memory')
        self.assertEqual(result['stop']['address'],'0xf2c05c')
        h=BR28Model(reset_history_projection=True,reset_history_words={PWR_CON:0x80},
                    power_init_config_rmw=True)
        result=explore(self.module,self.app,name='pmu_config_projected',handoff=self.handoff(),
            hardware=h,diagnostics={'entry_pc':0x60678f4,'initial_sp':STACK_TOP,'initial_gprs':regs},budget=1)
        self.assertEqual(result['completed_steps'],1)
        self.assertEqual(result['instruction_adapters'].get('reset_unknown_preserving_configuration_rmw'),1)
        self.assertEqual(h.reset.configuration[0xf2c05c].known,0xfffffc02)

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

    # ROM 0x417e-0x4197 的精确编码(memcpy「不对齐源组装」变体):寄存器计数
    # rep 22 r5 体=ld.bu.pin×2+insert.rrii.p∥ld.bu.pin 并行对×2(bits 8/16)
    # +普通 insert.rrii(bits 24)+st.w.pin,体后 bnez r5 回卷。按字节钉在只读
    # 区(尾部带到 0x41a3:体走查/解码窗口按上游惯例要读 6 字节),不依赖
    # ROM 文件。寄存器交叉:pair 的 A 半 insert 读 r4、B 半 ld.bu.pin 写
    # r4——正确 memcpy 只有在「A 读并行对执行前的旧 r4」语义下才成立(反方
    # 向会跳过/重复源字节),该方向无硬件捕获证据(FM-1 run3 只捕获过
    # exact_parallel_stores 的 B 读 A 旧值),adapter 名以 _is_assumption
    # 显式标注。
    COMPOSE_REP_LOOP=bytes.fromhex('a503' '1607' '1407' 'a6f12044' '1407'
                                   'a6f12048' '1407' 'a6e1204c' 'b605' 'f553'
                                   'f78c' '831c' '911c' '82f9' '1710' '65e1')

    def test_compose_rep_pair_body_assembles_the_misaligned_memcpy(self):
        # 2 轮 rep:每轮 4 次 ld.bu.pin 取源字节 + 2 个 insert 并行对 + 1 个
        # 普通 insert 组装 LE32 + st.w.pin 存整字。组装结果必须与纯 Python
        # 语义展开一致(word = b0 | b1<<8 | b2<<16 | b3<<24)。1 步 rep + 2×6
        # 步体 = 13 步;体后 bnez(计数寄存器已递减到 0)不在预算内。
        pc=0x01c60000;src,dst=0x124f00,0x124f40
        payload=bytes.fromhex('1122334455667788')
        h=self.handoff();h['readonly'].append((pc,self.COMPOSE_REP_LOOP))
        h['seeds'].append((src,payload));h['snapshots'].append(('compose_dst',dst,8))
        regs=[0]*16;regs[1]=src;regs[3]=dst;regs[5]=2
        result=explore(self.module,self.app,name='compose_rep',handoff=h,
            diagnostics={'entry_pc':pc,'initial_sp':STACK_TOP,'initial_gprs':regs,
                         'immediate_rep_multi_block':True,'exact_parallel_stores':True,
                         'exact_parallel_insert_load':True,'immediate_rep_projection':True,
                         'exact_subword_stores':True},budget=13)
        self.assertEqual(result['completed_steps'],13)
        self.assertEqual(result['stop_pc'],hex(pc+0x18))
        self.assertEqual(result['stop']['kind'],'instruction_budget')
        expected=''.join(f'{b:02x}' for b in payload)
        self.assertEqual(result['memory_snapshots']['compose_dst']['bytes_with_unknowns'],expected,
                         'the assembled words must equal the pure-Python little-endian expansion')
        self.assertEqual(result['last_registers']['r'][1],hex(src+8))
        self.assertEqual(result['last_registers']['r'][3],hex(dst+8))
        self.assertEqual(result['last_registers']['r'][5],'0x0')
        self.assertEqual(result['last_registers']['r'][6],hex(int.from_bytes(payload[4:8],'little')))
        adapters=result['instruction_adapters']
        self.assertEqual(adapters.get('register_rep_multi_block_projection'),1)
        self.assertEqual(adapters.get('parallel_insert_load_old_registers_is_assumption'),4)
        self.assertEqual(adapters.get('exact_postincrement_store'),2)

    def test_compose_rep_pair_stays_opt_in(self):
        # 不开 exact_parallel_insert_load:multi-block gate 对装载+组装对诚实
        # 拒绝(旧前沿 228,250 的停止形状),legacy 行为不变。仅开
        # exact_parallel_stores 也不行——该 gate 只支持纯 ALU 半+st.w 族。
        pc=0x01c60000;src,dst=0x124f00,0x124f40
        h=self.handoff();h['readonly'].append((pc,self.COMPOSE_REP_LOOP))
        h['seeds'].append((src,bytes.fromhex('11223344')))
        regs=[0]*16;regs[1]=src;regs[3]=dst;regs[5]=1
        result=explore(self.module,self.app,name='compose_rep_off',handoff=h,
            diagnostics={'entry_pc':pc,'initial_sp':STACK_TOP,'initial_gprs':regs,
                         'immediate_rep_multi_block':True,'exact_parallel_stores':True,
                         'immediate_rep_projection':True,'exact_subword_stores':True},budget=13)
        self.assertEqual(result['completed_steps'],0)
        self.assertEqual(result['stop_pc'],hex(pc))
        self.assertEqual(result['stop']['kind'],'rep_multi_block_body_parallel_pair_unsupported')
        self.assertEqual(result['stop']['instruction_id'],'insert.rrii.p')
        self.assertEqual(result['stop']['pair_id'],'ld.bu.pin')
        self.assertEqual(result['stop']['address'],hex(pc+6))

    def test_compose_rep_pair_transports_unknown_source_bytes(self):
        # 运输路径:源 byte0/byte3 已知、bytes 1-2 未写(真机=残留 RAM 垃圾)。
        # 体首 ld r4(byte1)走既有字节运输分支,pair 的 B 半(byte2)走本 gate
        # 的同语义运输;A 半按字节来源组装——r6 未知字节的物理来源必须逐字节
        # 正确传播,byte3 已知所以普通 insert(bits 24)可精确执行,st.w.pin 走
        # write_abstract_word 按来源落盘。任何数值消费者仍严格停。
        pc=0x01c60000;src,dst=0x124f00,0x124f40
        h=self.handoff();h['readonly'].append((pc,self.COMPOSE_REP_LOOP))
        h['seeds'].append((src,bytes.fromhex('11')));h['seeds'].append((src+3,bytes.fromhex('44')))
        h['snapshots'].append(('compose_dst',dst,4))
        regs=[0]*16;regs[1]=src;regs[3]=dst;regs[5]=1
        result=explore(self.module,self.app,name='compose_transport',handoff=h,
            diagnostics={'entry_pc':pc,'initial_sp':STACK_TOP,'initial_gprs':regs,
                         'immediate_rep_multi_block':True,'exact_parallel_stores':True,
                         'exact_parallel_insert_load':True,'rep_copy_unknown_transport':True,
                         'immediate_rep_projection':True,'exact_subword_stores':True},budget=7)
        self.assertEqual(result['completed_steps'],7)
        self.assertEqual(result['stop_pc'],hex(pc+0x18))
        self.assertEqual(result['memory_snapshots']['compose_dst']['bytes_with_unknowns'],'11????44',
                         'known bytes land, unknown bytes stay unknown at their positions')
        adapters=result['instruction_adapters']
        self.assertEqual(adapters.get('parallel_insert_load_old_registers_is_assumption'),2)
        self.assertEqual(adapters.get('rep_copy_unknown_transport_insert'),1)
        self.assertEqual(adapters.get('rep_copy_unknown_transport_load'),2)
        self.assertEqual(adapters.get('rep_copy_unknown_transport_store'),1)

    # 并行对半字存储(exact_parallel_stores 的独立子开关 exact_parallel_
    # halfword_stores)。真实前沿形状 app 0x6011370 `mov.rr.p r6=r0 ∥
    # st.h h[r4+0]=r5`(记录解析 helper 0x6011366 的出参清零序列,旧前沿
    # 236,415 的簿记读停点):上游 pair 存储模型写前合并读旧 word(模型内部
    # 簿记读,非 guest 装载);半字是部分写、同字邻字节真保持旧值,不能像
    # st.d 双字全 8 字节覆盖写那样断言「旧值无关」——gate 内按字节粒度
    # 直写 2 个目标字节。
    def test_parallel_halfword_pair_store_writes_only_two_target_bytes(self):
        h=self.handoff();h['snapshots'].append(('halfword_pair_and_neighbours',0x124f00,8))
        regs=[0]*16;regs[4]=0x124f02;regs[5]=0xabcd;regs[0]=0x11223344
        result=explore(self.module,self.app,name='halfword_pair',handoff=h,
            diagnostics={'entry_pc':0x6011370,'initial_sp':STACK_TOP,'initial_gprs':regs,
                         'exact_parallel_stores':True,'exact_parallel_halfword_stores':True},budget=1)
        self.assertEqual(result['completed_steps'],1)
        self.assertEqual(result['stop_pc'],hex(0x6011374))
        self.assertEqual(result['last_registers']['r'][6],hex(0x11223344),
                         'the pure-ALU half must move the old register')
        self.assertEqual(result['instruction_adapters'],{'parallel_halfword_store_old_registers':1})
        self.assertEqual(result['memory_snapshots']['halfword_pair_and_neighbours']['bytes_with_unknowns'],
                         '????cdab????????','exactly two target bytes land; same-word neighbours stay unknown')

    def test_parallel_halfword_pair_store_defaults_to_strict_bookkeeping_read_stop(self):
        # 不开子开关:同一指令落回上游通用 pair 模型,未初始化目标字的合并读
        # (模型簿记,非 guest 装载)诚实停。指令 id 是 mov.rr.p(A 半命名)、
        # 不以 st. 开头,不落入 upstream_store_bookkeeping_read 改名路径——
        # kind 保持 uninitialized_memory(legacy 行为不变,即旧前沿 236,415
        # 的真实停点形状)。
        regs=[0]*16;regs[4]=0x123702;regs[5]=0xabcd;regs[0]=0x11223344
        result=explore(self.module,self.app,name='halfword_pair_strict',
            diagnostics={'entry_pc':0x6011370,'initial_sp':STACK_TOP,'initial_gprs':regs,
                         'exact_parallel_stores':True},budget=1)
        self.assertEqual(result['completed_steps'],0)
        self.assertEqual(result['stop']['kind'],'uninitialized_memory')
        self.assertNotIn('note',result['stop'],'mov.rr.p must not hit the st. bookkeeping rename')
        self.assertEqual(result['stop']['address'],hex(0x123700))
        self.assertEqual(result['stop']['region'],'bootstrap_stack')

    # 并行对字节存储(exact_parallel_stores 的独立子开关 exact_parallel_byte_
    # stores)。真实前沿形状 app 0x6067a74 add.s14.rri.p∥st.b b[r0+0]=r4
    # (power_init 主核对 P11 M2P 消息区的首写,M2P_RVD2PVDD_EN):字节存储
    # 单字节全覆盖、旧值完全无关,gate 直写 1 个目标字节、A 半按旧寄存器
    # 计算新基址(0xf2a900)。
    def test_parallel_byte_pair_store_writes_exactly_one_target_byte(self):
        h=self.handoff();h['snapshots'].append(('byte_pair_and_neighbours',0x124f00,8))
        regs=[0]*16;regs[0]=0x124f03;regs[4]=0x5a;regs[7]=0x374c
        result=explore(self.module,self.app,name='byte_pair',handoff=h,
            diagnostics={'entry_pc':0x6067a74,'initial_sp':STACK_TOP,'initial_gprs':regs,
                         'exact_parallel_stores':True,'exact_parallel_byte_stores':True},budget=1)
        self.assertEqual(result['completed_steps'],1)
        self.assertEqual(result['stop_pc'],hex(0x6067a7a))
        self.assertEqual(result['last_registers']['r'][0],hex(0x2000),
                         'the pure-ALU half must compute the new base from the old register')
        self.assertEqual(result['instruction_adapters'],{'parallel_byte_store_old_registers':1})
        self.assertEqual(result['memory_snapshots']['byte_pair_and_neighbours']['bytes_with_unknowns'],
                         '??????5a????????','exactly one target byte lands; every neighbour stays unknown')

    def test_parallel_byte_pair_store_defaults_to_strict_bookkeeping_read_stop(self):
        # 不开字节子开关:同一指令落回上游通用 pair 模型,未初始化目标字的
        # 合并读(模型簿记,非 guest 装载)诚实停——即 300,187→300,236 前沿
        # 里 M2P 窗已映射、字节 gate 关闭时的真实停点形状(add.s14.rri.p 不以
        # st. 开头,不落入 upstream_store_bookkeeping_read 改名路径)。
        regs=[0]*16;regs[0]=0x123703;regs[4]=0x5a;regs[7]=0x374c
        result=explore(self.module,self.app,name='byte_pair_strict',handoff=self.handoff(),
            diagnostics={'entry_pc':0x6067a74,'initial_sp':STACK_TOP,'initial_gprs':regs,
                         'exact_parallel_stores':True},budget=1)
        self.assertEqual(result['completed_steps'],0)
        self.assertEqual(result['stop']['kind'],'uninitialized_memory')
        self.assertNotIn('note',result['stop'],'add.s14.rri.p must not hit the st. bookkeeping rename')
        self.assertEqual(result['stop']['address'],hex(0x123700))
        self.assertEqual(result['stop']['region'],'bootstrap_stack')

    def test_m2p_lrc_helper_writes_and_reads_back_the_period_byte(self):
        # M2P 消息区「写真实生效、读回最后值」的普通 RAM 语义由真实代码钉死:
        # 0x60674b8(power_init 尾部 call,li 0xF27F80=M2P 基址)写 LRC 周期字节
        # prd=1000000/200000=5 到 M2P+0,随后三次回读 b[+0] 推导 50/200/600us
        # 定时器字节(10/40/120)写进 +2/+3/+4。测试把 SDK 声明的 M2P 窗
        # (0xF27F80..0xF27FE0)挂为 harness region,16 条指令全真实执行。
        h=self.handoff()
        h['regions'].append((0xF27F80,0xF27FE0,'p11_m2p_message_area'))
        h['snapshots'].append(('m2p_head',0xF27F80,8))
        regs=[0]*16;regs[0]=200000
        result=explore(self.module,self.app,name='m2p_lrc_bytes',handoff=h,
            diagnostics={'entry_pc':0x60674b8,'initial_sp':STACK_TOP,'initial_gprs':regs,
                         'exact_subword_stores':True,'exact_parallel_stores':True,
                         'exact_parallel_byte_stores':True},budget=15)
        self.assertEqual(result['completed_steps'],15)
        self.assertEqual(result['stop_pc'],hex(0x60674ea))
        snap=result['memory_snapshots']['m2p_head']
        self.assertEqual(snap['bytes_with_unknowns'],'05??0a2878??????',
                         'the written period byte must read back as the derived timer bytes')
        self.assertEqual(snap['initialized_bytes'],4)

    # LRC trim 完成投影(lrct_trim_done_projection,需 lp_timer_init_projection,
    # 默认关)。真实调用链 0x6067aa0 起(power_init 尾部第一轮 LRC 配置):
    # 0x6067aa6 st.b b[0x129D8E]=1(r5)、0x6067aae &=0xFFFFFFC1、call 0x60674b8
    # (M2P LRC 周期字节写-回读-推导)、call 0x60674ec(r0=1:b[0x129D84]!=0 门
    # 下 &=~1/|=0x40/|=1 启动 trim)、call 0x606750c(trim 等待函数)。投影开:
    # 0x6067514/0x606751a 两读 CON 都回 0xC1(RMW 已知 bit0/bit6=0x41 + bit7
    # 完成投影置 1),0x6067516 bit0=1 不跳、0x606751c bit7=1 不跳——beq.and.ri
    # 的 ISA 语义是「(r&(1<<n))==0 时跳转」、目标回 0x606751a,固件等的是
    # bit7 置位(旧「busy 死等清零」记录按编码修正),轮询第一轮真实退出,
    # 停在 0x6067520 `ld.w r2=[r3+1*4]`(ld.w 的 n 字段 scale=4)读 NUM
    # (0xfd3604,SDK __RW trim 结果计数)——硅测量值无证据,严格停。
    def test_lrct_trim_done_projection_exits_the_poll_and_stops_at_num(self):
        h=self.handoff()
        h['regions'].append((0xF27F80,0xF27FE0,'p11_m2p_message_area'))
        h['regions'].append((0x129D80,0x129DB0,'lrct_config_bss'))
        h['seeds'].append((0x129D84,bytes.fromhex('01')))
        regs=[0]*16;regs[5]=1
        result=explore(self.module,self.app,name='lrct_trim_done',handoff=h,
            hardware=BR28Model(lp_timer_init_projection=True,lrct_trim_done_projection=True),
            diagnostics={'entry_pc':0x6067aa0,'initial_sp':STACK_TOP,'initial_gprs':regs,
                         'exact_subword_stores':True,'exact_parallel_stores':True,
                         'exact_parallel_byte_stores':True},budget=48)
        self.assertEqual(result['completed_steps'],41)
        self.assertEqual(result['stop_pc'],hex(0x6067520))
        self.assertEqual(result['stop']['kind'],'lrct_trim_result_not_modeled')
        self.assertEqual(result['stop']['address'],'0xfd3604')
        executed=[(row['pc'],row.get('instruction_id')) for row in result['last_instructions']]
        self.assertEqual(executed[-4:],[('0x6067516','beq.and.ri'),('0x606751a','ld.w'),
                                        ('0x606751c','beq.and.ri'),('0x6067520','ld.w')],
                         'the poll must really execute both reads and both bit tests '
                         '(the window ends on the blocked NUM load)')
        self.assertEqual(result['instruction_adapters'].get('lp_timer_bit_rmw'),4)
        lp=result['peripherals']['lp_timer_init_projection']
        self.assertEqual(lp['lrct_con'],{'address':'0xfd3600','known_mask':'0x7f',
                                         'known_value':'0x41','rmw_writes':4})
        trim=lp['lrct_trim_done_projection']
        self.assertEqual((trim['reads'],trim['projected_word']),(2,'0xc1'))

    def test_lrct_trim_done_projection_defaults_to_the_con_readback_stop(self):
        # 不开 trim-done 投影(只开 lp_timer_init_projection 写记录):同一链的
        # RMW 照常按位记录(4 次),0x6067514 读 CON 严格停——300,236 前沿的
        # 真实停点形状,投影语义是纯叠加。
        h=self.handoff()
        h['regions'].append((0xF27F80,0xF27FE0,'p11_m2p_message_area'))
        h['regions'].append((0x129D80,0x129DB0,'lrct_config_bss'))
        h['seeds'].append((0x129D84,bytes.fromhex('01')))
        regs=[0]*16;regs[5]=1
        result=explore(self.module,self.app,name='lrct_trim_strict',handoff=h,
            hardware=BR28Model(lp_timer_init_projection=True),
            diagnostics={'entry_pc':0x6067aa0,'initial_sp':STACK_TOP,'initial_gprs':regs,
                         'exact_subword_stores':True,'exact_parallel_stores':True,
                         'exact_parallel_byte_stores':True},budget=48)
        self.assertEqual(result['completed_steps'],37)
        self.assertEqual(result['stop_pc'],hex(0x6067514))
        self.assertEqual(result['stop']['kind'],'lp_timer_register_readback_not_modeled')
        self.assertEqual(result['stop']['address'],'0xfd3600')
        self.assertEqual(result['instruction_adapters'].get('lp_timer_bit_rmw'),4)
        self.assertNotIn('lrct_trim_done_projection',
                         result['peripherals']['lp_timer_init_projection'])

    # NUM 探路值 fixture(lrct_trim_result_value,需 lrct_trim_done_projection,
    # 与 p11_heap_info_bytes 同族的 opt-in 数据参数)。真实调用链 0x6067aa0 起、
    # stop_before_pc=0x6067ad4(第一个任务创建调用设置之前):trim 后半全真实执行
    # ——NUM=38400 读回(0x6067520)、0x6067524 stop-helper(CON &=~1/|=0x40)、
    # 0x6067526 非零不跳、0x6067532 读设备结构门控字节 b[0x126874](种子 1,
    # 真实前缀运行态实测已初始化=1)选 64 位分子 0x1_C9C38000=7.68e9
    # (0x606753e 立即数原始字节 00 80 c3 c9=0xC9C38000,十进制 3385032704;
    # 旧记录「0xC9C9C9C0」是十进制转写笔误)、0x6067546 64/32 无符号除法
    # 商=200000、[0x129D98]=raw、首次运行 b[0x129D8E]=1(entry[0]==0)→填充
    # 路径 20 项全部=商、平均 [0x129D88]=200000、b[0x129D8C] 0→1、
    # 0x60675e0 restart-helper(CON &=~1/|=0x40/|=1,RMW 合计 4+2+3=9)、
    # 0x6067ac8 第二轮 LRC 写以 [0x129D88]=200000 重推 prd=5——M2P 字节与
    # 第一轮完全相同(05??0a2878),探路值数值自洽的逐字节证据。
    def test_lrct_trim_result_fixture_runs_the_consumer_math(self):
        h=self.handoff()
        h['regions'].append((0xF27F80,0xF27FE0,'p11_m2p_message_area'))
        h['regions'].append((0x129D80,0x129DF0,'lrct_config_bss'))
        h['regions'].append((0x126870,0x126878,'dev_struct_gate'))
        h['seeds'].append((0x129D84,bytes.fromhex('01')))  # trim 门控(power_init 0x6067a96)
        h['seeds'].append((0x129D88,bytes(0x64)))          # bss 清零态的结果块
        h['seeds'].append((0x126874,bytes.fromhex('01')))  # 64 位分子门控字节
        h['snapshots'].append(('lrc_head',0x129D88,40))
        h['snapshots'].append(('lrc_last_entry',0x129DE8,4))
        h['snapshots'].append(('m2p_head',0xF27F80,8))
        regs=[0]*16;regs[5]=1
        result=explore(self.module,self.app,name='lrct_num_probe',handoff=h,
            hardware=BR28Model(lp_timer_init_projection=True,lrct_trim_done_projection=True,
                               lrct_trim_result_value=38400),
            diagnostics={'entry_pc':0x6067aa0,'initial_sp':STACK_TOP,'initial_gprs':regs,
                         'exact_subword_stores':True,'exact_parallel_stores':True,
                         'exact_parallel_byte_stores':True,'stop_before_pc':0x6067ad4},
            budget=400)
        self.assertEqual(result['stop'],{'kind':'diagnostic_milestone_reached','address':'0x6067ad4'})
        self.assertEqual(result['completed_steps'],255)
        snapshots=result['memory_snapshots']
        self.assertEqual(snapshots['lrc_head']['bytes_with_unknowns'],
                         '400d0300'+'01000100'+'00000000'*2+'400d0300'*6,
                         'avg 200000, counter bytes 1/1, zero handle word, raw 200000 and '
                         'the seeded history head must all land')
        self.assertEqual(snapshots['lrc_last_entry']['bytes_with_unknowns'],'400d0300',
                         'the fill path must seed the last of the twenty history entries')
        self.assertEqual(snapshots['m2p_head']['bytes_with_unknowns'],'05??0a2878??????',
                         'the second LRC write must re-derive the same period bytes from '
                         'the probe-converted 200000 Hz')
        self.assertEqual(result['instruction_adapters'].get('lp_timer_bit_rmw'),9)
        lp=result['peripherals']['lp_timer_init_projection']
        self.assertEqual(lp['lrct_con'],{'address':'0xfd3600','known_mask':'0x7f',
                                         'known_value':'0x41','rmw_writes':9})
        fixture=lp['lrct_trim_result_fixture']
        self.assertEqual((fixture['address'],fixture['value'],fixture['reads'],
                          fixture['value_is_derived_not_measured']),
                         ('0xfd3604','0x9600',1,True))

    def test_parallel_halfword_pair_store_transports_unknown_bytes_by_origin(self):
        # 运输路径:字形状 rep 拷贝体(0x600a352 的真实编码 rep 4 4:
        # ld.w.pin r3=[r2++=4]/st.w.pin [r1++=4]=r3)把含未知字节的字装进
        # r3,随后真实并行对 0x600784a `add.rr.p r2+=r7 ∥ st.h h[r1+0]=r3`
        # (字节原样搬到只读区,尾部按上游解码惯例垫 4 字节)——半字
        # abstract 写:byte0 已知落盘、byte1 未知带物理来源保持未知;
        # A 半按旧寄存器做加法(r2=循环后 src+16+旧 r7)。任何数值消费者
        # 仍严格停。
        pc=0x01c60020;src,dst=0x124f00,0x124f40
        code=bytes.fromhex('1083' '2305' '9305' '72d8' '9b60' '00000000')
        h=self.handoff();h['readonly'].append((pc,code))
        h['seeds'].append((src+12,bytes.fromhex('11')))
        h['snapshots'].append(('pair_halfword_dst',dst+16,4))
        regs=[0]*16;regs[1]=dst;regs[2]=src;regs[7]=0x1000
        result=explore(self.module,self.app,name='halfword_pair_transport',handoff=h,
            diagnostics={'entry_pc':pc,'initial_sp':STACK_TOP,'initial_gprs':regs,
                         'immediate_rep_multi_block':True,'rep_copy_unknown_transport':True,
                         'immediate_rep_projection':True,
                         'exact_parallel_stores':True,'exact_parallel_halfword_stores':True},budget=10)
        self.assertEqual(result['completed_steps'],10)
        self.assertEqual(result['stop_pc'],hex(pc+10))
        self.assertEqual(result['last_registers']['r'][2],hex(src+16+0x1000),
                         'the ALU half must add to the post-loop (old) register')
        self.assertEqual(result['memory_snapshots']['pair_halfword_dst']['bytes_with_unknowns'],'11??????')
        adapters=result['instruction_adapters']
        self.assertEqual(adapters.get('parallel_halfword_store_old_registers'),1)
        self.assertEqual(adapters.get('rep_copy_unknown_transport_load'),4)
        self.assertEqual(adapters.get('rep_copy_unknown_transport_store'),4)
        # 半字 abstract 写的两条事件:byte0 已知 '11' 落盘、byte1 '??' 按来源保持。
        writes=[e for e in result['memory_events'] if e.get('access')=='abstract_write'
                and int(e['address'],16) in (dst+16,dst+17)]
        self.assertEqual([e['bytes_with_unknowns'] for e in writes],['11','??'])

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

    def test_vm_config_byte_rep_copy_transports_unknown_bytes(self):
        # 实际 VM 配置 GET 0x600a302 rep 4 16(r1=b[r0++=1](u)/b[r6++=1]=r1,
        # 重复 16 次)。源 0x1276a4..0x1276b3(设备结构 0x1265C0+0x10E4)按
        # 0x40045601 SET 路径 0x600a352 rep 4 4 拷完后的真机状态部分初始化:
        # byte0=0x10(vm.align)、word1=0x1da000(vm_saddr)、word3=0x4000
        # (vm_size)已知,bytes 1-3(vm_info 的 C 对齐 padding)与 8-11(vm_res
        # 保留字段)无任何写入者——真机上是残留 RAM 垃圾(boot-info 路钉死;
        # 183,712 前沿 watch 实测状态 10??????00a01d00????????00400000)。
        # 开 rep_copy_unknown_transport 后:已知字节照搬,未知字节按来源字节
        # 级运输(零扩展:低字节未知带来源、高 3 字节已知为 0),1 步 rep.i +
        # 16×2 步体 = 33 步;真实消费者 0x6060f9a 只读 [sp]/[sp+4]/[sp+12]
        # 三个已知字段(align/saddr/size),未知字节拷到栈上后从不读。
        h=self.handoff();src,dst=0x1276a4,0x124f00
        h['seeds'] += [(src,b'\x10'),(src+4,struct.pack('<I',0x1da000)),
                       (src+12,struct.pack('<I',0x4000))]
        h['snapshots'].append(('vm_config_copy_dst',dst,16))
        regs=[0]*16;regs[0]=src;regs[6]=dst
        result=explore(self.module,self.app,name='rep_byte_copy_transport',handoff=h,
            diagnostics={'entry_pc':0x0600a302,'initial_sp':STACK_TOP,'initial_gprs':regs,
                         'immediate_rep_projection':True,'immediate_rep_multi_block':True,
                         'rep_copy_unknown_transport':True,'exact_subword_stores':True},budget=33)
        self.assertEqual(result['completed_steps'],33)
        self.assertEqual(result['stop_pc'],'0x600a308')
        self.assertEqual(result['last_registers']['r'][0],hex(src+16))
        self.assertEqual(result['last_registers']['r'][6],hex(dst+16))
        self.assertEqual(result['last_registers']['r'][1],'0x0')
        self.assertEqual(result['memory_snapshots']['vm_config_copy_dst']['bytes_with_unknowns'],
                         '10??????'+'00a01d00'+'????????'+'00400000')
        self.assertEqual(result['instruction_adapters'].get('rep_copy_unknown_transport_load'),7)
        self.assertEqual(result['instruction_adapters'].get('rep_copy_unknown_transport_store'),7)

    def test_vm_config_byte_rep_copy_transport_stays_opt_in(self):
        # 同一指令不开 rep_copy_unknown_transport 时必须诚实停在第二轮体首
        # 装载的未初始化字节读上(183,712 前沿的精确形状),legacy 行为不变。
        h=self.handoff();src,dst=0x1276a4,0x124f00
        h['seeds'].append((src,b'\x10'))
        regs=[0]*16;regs[0]=src;regs[6]=dst
        result=explore(self.module,self.app,name='rep_byte_copy_transport_off',handoff=h,
            diagnostics={'entry_pc':0x0600a302,'initial_sp':STACK_TOP,'initial_gprs':regs,
                         'immediate_rep_projection':True,'immediate_rep_multi_block':True,
                         'exact_subword_stores':True},budget=33)
        self.assertEqual(result['completed_steps'],3)
        self.assertEqual(result['stop_pc'],'0x600a304')
        self.assertEqual(result['stop'],{'kind':'uninitialized_memory','access':'read',
            'address':'0x1276a5','size':1,'region':'qcy_bss','first_missing':'0x1276a5'})

    def test_vm_config_byte_transport_stops_generic_consumers(self):
        # 运输后的未知字节对通用消费者严格停:自定义直线代码 = 实际 rep 4 16
        # 字节拷贝体 + 一条真实编码的通用字装载 r0=[r4+0](40 60),读目的
        # bytes 8-11(vm_res,全部未知)→ 未初始化读诚实停,不返回臆造值。
        pc=0x01c60000
        h=self.handoff();h['readonly'].append((pc,bytes.fromhex('108f0107e107406020002000')))
        src,dst=0x1276a4,0x124f00
        h['seeds'].append((src,b'\x10'))
        regs=[0]*16;regs[0]=src;regs[6]=dst;regs[4]=dst+8
        result=explore(self.module,self.app,name='rep_byte_transport_consumer',handoff=h,
            diagnostics={'entry_pc':pc,'initial_sp':STACK_TOP,'initial_gprs':regs,
                         'immediate_rep_projection':True,'immediate_rep_multi_block':True,
                         'rep_copy_unknown_transport':True,'exact_subword_stores':True},budget=34)
        self.assertEqual(result['completed_steps'],33)
        self.assertEqual(result['stop_pc'],hex(pc+6))
        self.assertEqual(result['stop'],{'kind':'uninitialized_memory','access':'read',
            'address':hex(dst+8),'size':4,'region':'qcy_initialized_data',
            'first_missing':hex(dst+8)})

    def test_vm_config_byte_transport_register_consumer_stops(self):
        # 字节装载构造的 UnknownWord 被数值消费时严格停:自定义直线体
        # rep 4 16 { r1=b[r0++=1](u); r2+=r1 }(真实编码 01 07/12 18)——
        # 第 1 轮已知字节 0x10 正常加,第 2 轮装载未知字节后 r2+=r1 触发
        # unknown_task_register_consumed,来源=装载字节的物理地址,高 3 字节
        # 的已知 0 如实报告(零扩展形状),不臆造数值。
        pc=0x01c60000
        h=self.handoff();h['readonly'].append((pc,bytes.fromhex('108f0107121820002000')))
        src=0x1276a4
        h['seeds'].append((src,b'\x10'))
        regs=[0]*16;regs[0]=src;regs[2]=5
        result=explore(self.module,self.app,name='rep_byte_transport_register',handoff=h,
            diagnostics={'entry_pc':pc,'initial_sp':STACK_TOP,'initial_gprs':regs,
                         'immediate_rep_projection':True,'immediate_rep_multi_block':True,
                         'rep_copy_unknown_transport':True},budget=5)
        self.assertEqual(result['completed_steps'],4)
        self.assertEqual(result['stop_pc'],hex(pc+4))
        self.assertEqual(result['stop']['kind'],'unknown_task_register_consumed')
        self.assertEqual(result['stop']['sources'],['0x1276a5',None,None,None])
        self.assertEqual(result['stop']['known_bytes'],'??000000')

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

    def test_sp_doubleword_store_overwrites_unknowns_without_old_word_read(self):
        # 0x6060a74 is the real `st.d.sp.l d[sp]=r1_r0` of the qcy consumer
        # (0x6060a4a). The store overwrites all eight bytes, so the old words
        # cannot matter; through the exact-doubleword gate it must write both
        # halves directly, clearing the previous unknown stack bytes instead
        # of stopping on the upstream merge bookkeeping.
        h=self.handoff();sp=0x123700
        h['snapshots'].append(('doubleword_and_neighbours',sp-4,16))
        regs=[0]*16;regs[0]=0x11223344;regs[1]=0x55667788
        result=explore(self.module,self.app,name='sp_doubleword',handoff=h,
            diagnostics={'entry_pc':0x6060a74,'initial_sp':sp,'initial_gprs':regs,
                         'exact_doubleword_stores':True},budget=1)
        self.assertEqual(result['completed_steps'],1)
        self.assertEqual(result['stop_pc'],hex(0x6060a78))
        self.assertEqual(result['last_registers']['sp'],hex(sp))
        self.assertEqual(result['instruction_adapters'],{'exact_doubleword_store':1})
        self.assertEqual(result['memory_snapshots']['doubleword_and_neighbours']['bytes_with_unknowns'],
                         '????????'+'4433221188776655'+'????????')

    def test_sp_doubleword_store_defaults_to_strict_bookkeeping_read_stop(self):
        # Without the opt-in gate the same instruction must keep stopping on
        # the upstream store model's merge read of the uninitialized old word
        # (a model-internal read, not a guest load): legacy behaviour unchanged.
        sp=0x123700
        regs=[0]*16;regs[0]=0x11223344;regs[1]=0x55667788
        result=explore(self.module,self.app,name='sp_doubleword_strict',
            diagnostics={'entry_pc':0x6060a74,'initial_sp':sp,'initial_gprs':regs},budget=1)
        self.assertEqual(result['completed_steps'],0)
        self.assertEqual(result['stop']['kind'],'upstream_store_bookkeeping_read')
        self.assertEqual(result['stop']['address'],hex(sp))
        self.assertEqual(result['stop']['region'],'bootstrap_stack')

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

    # ---- bank B 第二数据窗 0xFF fixture(opt-in erased_bankb,机制层钉子)----

    def erased_bankb_window_case(self,addr,*,fixture=True,store=False):
        h=self.handoff()
        h['description']['phase']='erased bank-B window fixture mechanism test'
        if fixture:h['readonly'].append((0x61ddfe0,b'\xff'*20))
        # 真实 app 指令:读侧 0x6060a00 `r1=b[r0+3] (u)`(qcy 消费函数 0x60609ec
        # 表 walker 头 4 字节读取的首条,r0=window+条目 offset,实测读 0x61ddfe3)
        # ——r0 预减 3 使被读字节恰为 addr;写侧 0x6010fca `[r1++=4]=r2`
        # (find_partition 拷贝路径的字存储,形状先例)。
        regs=[0]*16;regs[1]=0x12345678
        if store:
            regs[1]=addr;regs[2]=0x12345678
            entry_pc=0x6010fca
        else:
            regs[0]=addr-3
            entry_pc=0x6060a00
        return explore(self.module,self.app,name='erased_bankb_window_fixture',handoff=h,
            diagnostics={'entry_pc':entry_pc,'initial_sp':STACK_TOP,
                         'initial_rets':ENTRY,'initial_gprs':regs},budget=1)

    def test_erased_bankb_window_fixture_reads_back_0xff_bytes(self):
        # 区内(含两项头读取的实测字节 0x61ddfe3/0x61ddfed 与末字节 0x61ddff3)
        # 读出 0xff——qcy 消费函数在 0x6060a00 读到的正是这些擦除态头字节。
        for addr in (0x61ddfe0,0x61ddfe3,0x61ddfed,0x61ddfe0+19):
            result=self.erased_bankb_window_case(addr)
            self.assertEqual(result['completed_steps'],1)
            self.assertEqual(result['stop']['kind'],'instruction_budget')
            self.assertEqual(result['last_registers']['r'][1],'0xff')

    def test_erased_bankb_window_fixture_bounds_and_default_stay_strict(self):
        # fixture 只覆盖 walker 表声明的 20 字节结构范围(两项 {id=0x66,size=6}/
        # {id=0x6e,size=6},每项 4 字节头+size 数据):紧邻区外(下界 0x61ddfdf/
        # 上界 0x61ddff4)与默认(不挂 fixture)都继续严格停 unmapped——bank B
        # 其余字节(含 TON 0x1e1000 窗)不臆造。
        for addr in (0x61ddfdf,0x61ddff4):
            result=self.erased_bankb_window_case(addr)
            self.assertEqual(result['completed_steps'],0)
            self.assertEqual(result['stop'],{'kind':'unmapped_memory','access':'read',
                'address':hex(addr),'size':1,'reference_sdk_register':None})
        result=self.erased_bankb_window_case(0x61ddfe0,fixture=False)
        self.assertEqual(result['stop']['kind'],'unmapped_memory')

    def test_erased_bankb_window_fixture_is_read_only(self):
        result=self.erased_bankb_window_case(0x61ddfe0,store=True)
        self.assertEqual(result['completed_steps'],0)
        self.assertEqual(result['stop'],{'kind':'unmapped_or_readonly_write','access':'write',
            'address':'0x61ddfe0','size':4,'value_bytes':'78563412'})

    def test_erased_bankb_window_walker_table_and_erased_headers(self):
        # 黄金算术钉子:walker 表 0x60a1e8a(app flash 公开字节)是 u16 对
        # {id,size}、id==0 终止;条目 offset=Σ前项(4+size)。两项 {0x66,6}/
        # {0x6e,6} → offset 0 与 0xa,结构全长 (4+6)*2=20——fixture 范围的
        # 依据。0xFF 擦除态下两项头 LE32=0xffffffff,0x6060a14 判等成立 →
        # 0x60609ec 返回 -248,调用者 0x6060a9a 对 -248 直接跳下一项(trace
        # 钉死零副作用),永远不会走到数据区 CRC(0x6060a28)。
        table=self.app[0x60a1e8a-ENTRY:0x60a1e8a-ENTRY+12]
        pairs=struct.unpack('<HHHHHH',table)
        self.assertEqual(pairs,(0x66,6,0x6e,6,0,0))
        offsets=[];acc=0
        for i in range(0,len(pairs),2):
            id_,size=pairs[i],pairs[i+1]
            if id_==0:break
            offsets.append(acc);acc+=4+size
        self.assertEqual(offsets,[0,0xa])
        self.assertEqual(acc,20)
        self.assertEqual(0xffffffff,struct.unpack('<I',b'\xff'*4)[0])
        self.assertEqual(0xffffffff>>20,0xfff)  # 即使走到 size 检查也不等于 6
        self.assertNotEqual(0xffffffff>>20,6)

    # ---- bank B 第三数据窗 0xFF fixture(opt-in erased_bankb,机制层钉子)----

    def erased_bankb_vm_case(self,addr,*,fixture=True,store=False):
        h=self.handoff()
        h['description']['phase']='erased bank-B VM fixture mechanism test'
        if fixture:h['readonly'].append((0x61d7fe0,b'\xff'*0x2000))
        # 真实 app 指令:读侧 0x600a78c `r2=b[r0+0] (u)`(VM 空白检查
        # 0x600a78a 的逐字节装载,本前缀实测扫过 0x61d7fe0..0x61d9fdf 共
        # 8192 次);写侧 0x6010fca `[r1++=4]=r2`(find_partition 拷贝路径
        # 的字存储,形状先例)。
        regs=[0]*16
        if store:
            regs[1]=addr;regs[2]=0x12345678
            entry_pc=0x6010fca
        else:
            regs[0]=addr
            entry_pc=0x600a78c
        return explore(self.module,self.app,name='erased_bankb_vm_fixture',handoff=h,
            diagnostics={'entry_pc':entry_pc,'initial_sp':STACK_TOP,
                         'initial_rets':ENTRY,'initial_gprs':regs},budget=1)

    def test_erased_bankb_vm_fixture_reads_back_0xff_bytes(self):
        # 区内(两半 magic 基址 0x61d7fe0/0x61d8fe0、扫描末字节 0x61d9fdf)
        # 读出 0xff——空白检查在 0x600a78c 读到的正是这些擦除态字节。
        for addr in (0x61d7fe0,0x61d7fe3,0x61d8fe0,0x61d9fdf):
            result=self.erased_bankb_vm_case(addr)
            self.assertEqual(result['completed_steps'],1)
            self.assertEqual(result['stop']['kind'],'instruction_budget')
            self.assertEqual(result['last_registers']['r'][2],'0xff')

    def test_erased_bankb_vm_fixture_bounds_and_default_stay_strict(self):
        # fixture 只覆盖结构声明的 VM 区全长 0x2000(两半各 0x1000):
        # 紧邻区外(下界 0x61d7fdf/上界 0x61d9fe0=flash 0x1dc000,VM 区
        # 结束)与默认(不挂 fixture)都继续严格停 unmapped——bank B 其余
        # 字节(VM 尾与第二窗 0x1e0000 之间的 gap、TON 0x1e1000)不臆造。
        for addr in (0x61d7fdf,0x61d9fe0):
            result=self.erased_bankb_vm_case(addr)
            self.assertEqual(result['completed_steps'],0)
            self.assertEqual(result['stop'],{'kind':'unmapped_memory','access':'read',
                'address':hex(addr),'size':1,'reference_sdk_register':None})
        result=self.erased_bankb_vm_case(0x61d7fe0,fixture=False)
        self.assertEqual(result['stop']['kind'],'unmapped_memory')

    def test_erased_bankb_vm_fixture_is_read_only(self):
        result=self.erased_bankb_vm_case(0x61d7fe0,store=True)
        self.assertEqual(result['completed_steps'],0)
        self.assertEqual(result['stop'],{'kind':'unmapped_or_readonly_write','access':'write',
            'address':'0x61d7fe0','size':4,'value_bytes':'78563412'})

    def test_erased_bankb_vm_extent_is_firmware_declared(self):
        # 黄金算术钉子:VM 窗范围由固件自己的结构声明链钉死——boot_info
        # vm 字段(saddr=0x1da000、size=0x4000,VM init 0x6011102/0x6011108
        # 写入)→ GET 消费者 0x6060f9a 的 clamp(>=8192→8192)与 4K 对齐切半
        # (base=(saddr+4095)&~4095、span=对齐后长、half=span>>1、half2=
        # base+half、end=base+span)→ 空白检查 0x600a79e 每半 len=half →
        # fixture 全长=span、扫描装载次数=2*half。窗口与第二窗/TON 不重叠。
        saddr,size=0x1da000,0x4000
        span=min(size,8192)
        base=(saddr+4095)&~4095
        half=span>>1
        self.assertEqual((base,span,half),(0x1da000,0x2000,0x1000))
        self.assertEqual((base+half,base+span),(0x1db000,0x1dc000))
        self.assertEqual(0x06000000+base-0x2020,0x61d7fe0)    # 窗口基址(头修正 0x2020)
        self.assertEqual(2*half,0x2000)                       # fixture 全长=扫描字节数
        self.assertLess(base+span,0x1e0000)                   # 不与第二窗(flash 0x1e0000)重叠
        self.assertLess(0x1e0000,0x1e1000)                    # 第二窗在 TON 之前
        # 擦除态 magic 判定链:两半头字都是 0xffffffff,不在 {0x5A5A5A5A,
        # 0xFFFF5A5A,0xFFFFFF56,0xFF5A5A5A} 里→0x6061106→word2≠0xFF5A5A5A
        # →空白检查路径;全 0xFF 时 0x600a78a 的 `if (r2 == 255) goto` 循环
        # 恰好走满 len 次到 0x600a79a 的 return 0。
        erased=0xffffffff
        self.assertNotIn(erased,(0x5a5a5a5a,0xffff5a5a,0xffffff56,0xff5a5a5a))
        self.assertNotEqual(erased,0xff5a5a5a)

    # ---- bank B 第四数据窗 0xFF fixture(opt-in erased_bankb,机制层钉子)----

    def erased_bankb_record_case(self,addr,*,fixture=True,store=False):
        h=self.handoff()
        h['description']['phase']='erased bank-B record fixture mechanism test'
        if fixture:h['readonly'].append((0x61de1e0,b'\xff'*8))
        # 真实 app 指令:读侧 0x6060df4 `r1=b[r0+3] (u)`(cfg_tool/btif 存储后端
        # init 0x6060d7c 解析 window+0x200 首 8 字节记录头的首条装载,r0=
        # window+0x200,本前缀实测读 0x61de1e3)——r0 预减 3 使被读字节恰为
        # addr;写侧 0x6010fca `[r1++=4]=r2`(find_partition 拷贝路径的字存储,
        # 形状先例)。
        regs=[0]*16
        if store:
            regs[1]=addr;regs[2]=0x12345678
            entry_pc=0x6010fca
        else:
            regs[0]=addr-3
            entry_pc=0x6060df4
        return explore(self.module,self.app,name='erased_bankb_record_fixture',handoff=h,
            diagnostics={'entry_pc':entry_pc,'initial_sp':STACK_TOP,
                         'initial_rets':ENTRY,'initial_gprs':regs},budget=1)

    def test_erased_bankb_record_fixture_reads_back_0xff_bytes(self):
        # 区内(含实测首读字节 0x61de1e3 与 8 字节头末字节 0x61de1e7)读出
        # 0xff——记录头 init 在 0x6060df4 读到的正是这些擦除态头字节。
        for addr in (0x61de1e0,0x61de1e3,0x61de1e7):
            result=self.erased_bankb_record_case(addr)
            self.assertEqual(result['completed_steps'],1)
            self.assertEqual(result['stop']['kind'],'instruction_budget')
            self.assertEqual(result['last_registers']['r'][1],'0xff')

    def test_erased_bankb_record_fixture_bounds_and_default_stay_strict(self):
        # fixture 只覆盖消费者自己解析的完整 8 字节头结构(0x61de1e0..0x61de1e7
        # =flash 0x1e0200..0x1e0207):紧邻区外(下界 0x61de1df/上界 0x61de1e8
        # =记录数据区首字节)与默认(不挂 fixture)都继续严格停 unmapped——
        # bank B 其余字节(记录载荷区/后续条目/TON 0x1e1000 窗)不臆造。
        for addr in (0x61de1df,0x61de1e8):
            result=self.erased_bankb_record_case(addr)
            self.assertEqual(result['completed_steps'],0)
            self.assertEqual(result['stop'],{'kind':'unmapped_memory','access':'read',
                'address':hex(addr),'size':1,'reference_sdk_register':None})
        result=self.erased_bankb_record_case(0x61de1e0,fixture=False)
        self.assertEqual(result['stop']['kind'],'unmapped_memory')

    def test_erased_bankb_record_fixture_is_read_only(self):
        result=self.erased_bankb_record_case(0x61de1e0,store=True)
        self.assertEqual(result['completed_steps'],0)
        self.assertEqual(result['stop'],{'kind':'unmapped_or_readonly_write','access':'write',
            'address':'0x61de1e0','size':4,'value_bytes':'78563412'})

    def test_erased_bankb_record_header_and_gates_are_firmware_declared(self):
        # 黄金算术钉子:记录头窗范围与擦除态分支全部由固件自己的代码/结构
        # 声明——window 基址=0x06000000+0x1e0000-0x2020(资源描述符经填充
        # 函数 0x600ae2e 给出,动态 trace 钉死)、init 0x6060dec 自己加 512
        # (0x200)得记录区基址、窗口全长 0x1000(填充结果 [sp+4],TON
        # 0x1e1000 的 CPU 基址恰为窗口末);8 字节头窗在窗口区内、不与第二
        # 窗 20 字节表结构(0x61ddfe0..0x61ddff4)重叠。0xFF 擦除态下头 word0
        # =0xffffffff,(word0-1)=0xfffffffe 无符号 >2047 → 0x6060e0e 拒绝
        # (真实分支只读头 4 字节);条目有效位 [0x126568] 由 0x6060dd0 预清
        # 0、只在 CRC 匹配分支 0x6060e42 置 1,按 index 找条目的 walker
        # 0x60112c8 入口 0x60112d4 判其 !=1 即跳——擦除态把后续记录区读取
        # 全部挡在门外,fixture 无需也不应扩到 8 字节头之外。
        window=0x06000000+0x1e0000-0x2020
        self.assertEqual(window,0x61ddfe0)
        self.assertEqual(window+512,0x61de1e0)                  # 0x6060dec 的 +512
        self.assertEqual(window+0x1000,0x06000000+0x1e1000-0x2020)  # 窗口末=TON CPU 基址
        self.assertLess(0x61ddfe0+20,0x61de1e0)                 # 不与表结构(fixture 窗 2)重叠
        self.assertLess(0x61de1e0+8,window+0x1000)              # 8 字节头在窗口区内
        word0=struct.unpack('<I',b'\xff'*4)[0]
        self.assertEqual(word0,0xffffffff)
        self.assertGreater((word0-1)&0xffffffff,2047)           # 无符号范围检查拒绝
        self.assertEqual((0xffffffff>>20),0xfff)                # 即使走到第二检查也 >800
        self.assertGreater(0xfff,800)
        # 状态块 0x12655C 的成员偏移(0x6060d7c 用 r8+偏移访问):
        # +12 条目有效位(0x6060dd0 清 0/0x6060e42 置 1)、+16 游标
        # (0x6060df0 写 window+0x200)、+20 长度(0x6060e4a 写 word0)。
        self.assertEqual(0x126568-0x12655c,12)
        self.assertEqual(0x12656c-0x12655c,16)
        self.assertEqual(0x126570-0x12655c,20)

    def test_p11_heap_info_derived_bytes_match_the_resource_immediates(self):
        # 堆信息 4 字节的静态推导钉子(p11_heap_experimental_config 场景的
        # P11_HEAP_INFO_DERIVED):begin/size 是 p11_code.bin 公共段里的链接期
        # wide 立即数,不是运行时计算。推导链三方独立:
        # ①原始字节——公共段(P11 本地 0x80 装载)0xa12 `mov r0,#0x3d0c`
        #   (c0ff 0c3d0000)、0xa1a `mov r5,#0x7f4a`(c5ff 4a7f0000)、
        #   0xa26 `mov r0,#0x39f4`(c0ff f4390000),其后 sb/lsr/sb 序列逐字节
        #   写 0x7f4a..0x7f4d(q32s 静态解码,本仓库不执行 q32s);
        # ②SDK 消费语义——lp_ipc.h P2M 堆偏移 0x00a..0x00d、p11_app.h
        #   P11_HEAP_BEGIN=P11_RAM_BASE+(H<<8|L) / P11_HEAP_SIZE=(H<<8|L);
        # ③SDK 布局互证——heap 末 0xf27700 恰等于 VAD_DVAD_CONFIG_BEGIN。
        # 值是推导而非测量:本测试只钉「推导与资源字节一致」,不构成硅验证。
        from analyze_p11 import analyze
        from probe_irq_entry import P11_HEAP_INFO_DERIVED
        from verify_eq_candidate import load_image
        firmware=ROOT/'firmware/QCY_H3S_7018F_1T2_release_note_V1.1.8_011.ufw'
        metadata=analyze(firmware)
        _,_,plain=load_image(firmware)
        resource=int(metadata['jlfs_header_offset'],16)+32
        raw=plain[resource:resource+metadata['resource_bytes']]
        entry=metadata['bankcb_entries'][0]
        load=int(entry['address_field'],16)
        data=raw[entry['file_offset']:entry['file_offset']+entry['bytes']]
        def immediate(pc,register):
            # q32s wide 立即数 mov:2 字节操作码(低 3 位=目的寄存器)+LE32。
            self.assertEqual(data[pc-load]&0xf8,0xc0,'wide-immediate mov opcode changed')
            self.assertEqual(data[pc-load]&7,register,'wide-immediate mov destination changed')
            return struct.unpack_from('<I',data,pc-load+2)[0]
        begin=immediate(0xa12,0)
        p2m_pointer=immediate(0xa1a,5)
        size=immediate(0xa26,0)
        self.assertEqual((begin,p2m_pointer,size),(0x3d0c,0x7f4a,0x39f4),
                         'the heap begin/size link-time immediates moved in p11_code.bin')
        self.assertEqual(P11_HEAP_INFO_DERIVED,
                         bytes((begin&0xff,begin>>8&0xff,size&0xff,size>>8&0xff)),
                         'the experimental heap fixture must stay the derived candidate 0c3df439')
        # SDK 锚点算术(p11_app.h 布局链,全部常量来自固定 SDK 头):
        # P2M 消息区=0xF27F40(+0xa=0xF27F4A,与 P11_HEAP_INFO 一致);
        # heap 末=P11_RAM_BASE|begin+size=0xF27700=VAD_DVAD_CONFIG_BEGIN。
        p2m=0xF20000+0x8000-0x20-0x60-0x40
        self.assertEqual(p2m+0xa,P11_HEAP_INFO,'P2M heap offsets disagree with the pinned SDK layout')
        heap_end=(0xF20000|begin)+size
        vad_dvad_begin=0xF20000+0x8000-0x20-0x60-0x40-0x20-160*2*6-20*4-20*4
        self.assertEqual(vad_dvad_begin,0xF27700,'the pinned SDK layout arithmetic changed')
        self.assertEqual(heap_end,vad_dvad_begin,
                         'the derived size no longer abuts the SDK VAD config region')


if __name__=='__main__':unittest.main()
