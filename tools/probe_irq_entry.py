#!/usr/bin/env python3
"""Minimal IRQ127 software-interrupt entry on the real startup prefix.

The scheduler IPI (pending 0x8 set by the actual QCY code at 0x60644a6)
reaches an opt-in CPU entry: RETI loads the interrupted PC and execution
continues at the vector slot written by the firmware. Entry banks, RETI
load and pending acknowledgement are explicit assumptions; generic IRQ
entry, IDLE wake and hardware frame writes stay unmodeled. The trailing
irq125_repeat_inert scenario pins that the opt-in repeat_irq_delivery
projection (handler return re-enables delivery) stays inert on this prefix
until the firmware actually pends a second time. No flash approval.
"""
import argparse
import json
from pathlib import Path

from probe_dualcore import dual_case
from probe_task_returns import EMPTY_PRIORITIES
from probe_arch_startup import candidate_view, REPAIRED_SHA, FAILED_SHA
from probe_boot import application, flash_image, check_rom, ROM_SHA, OFFICIAL_SHA, APP_SHA
from probe_boot_handoff import package_header
from probe_pi32v2 import ROOT, PIN, upstream, require

HANDLER_PSR_FRAMES={0x12ad30:'psr_push',0x12ad60:'psr_pop',0x12adae:'psr_push',0x12adde:'psr_pop',
                    0x12ad6e:'frame12_push',0x12ada0:'frame12_pop',
                    0x60662c8:'frame12_push',0x606633e:'frame12_pop'}

# 0xfd1c00 在 SFC 挂起/恢复周期里按位的 RMW 记录；前三条来自挂起尾与恢复头，
# 中间的 0x4000 是 0x12ba38 轮询返回后的 PLL 状态位写入，末尾两条是恢复尾的清位。
SFC_RESUME_OPERATIONS=(
    {'kind':'or','operand':'0x1'},{'kind':'or','operand':'0x8'},
    {'kind':'and','operand':'0xffffefff'},{'kind':'or','operand':'0x4000'},
    {'kind':'and','operand':'0xfffffff7'},{'kind':'or','operand':'0x8'},
    {'kind':'and','operand':'0xffffefff'},{'kind':'or','operand':'0x4000'},
    {'kind':'and','operand':'0xfffffff7'},{'kind':'or','operand':'0x8'},
    {'kind':'and','operand':'0xffffefff'},{'kind':'or','operand':'0x4000'},
    {'kind':'and','operand':'0xfffffff7'},{'kind':'or','operand':'0x8'},
    {'kind':'and','operand':'0xffffefff'},{'kind':'or','operand':'0x4000'},
    {'kind':'and','operand':'0xfffffff7'},{'kind':'or','operand':'0x1000'},
    *([{'kind':'or','operand':'0x4000'}]*32),
    {'kind':'and','operand':'0xfffff3ff'},{'kind':'and','operand':'0xfffffffe'})


def irq_case(module,app,header,rom,name,*,entry=False,banks=False,idle_wait=False,rti=False,
             unmaskable=False,sfc_suspend=False,gpio_out_readback=False,kick_wake=False,
             repeat=False,hardware_crc=False,rep_block=False,rep_unknown_transport=False,
             icu_region=False,dcu_cmo=False,flash_image_bytes=None,readonly=()):
    machine_options={**EMPTY_PRIORITIES,'first_task_return_hypothesis':'open_gate8',
                     'defer_core1_first_idle':True}
    if entry:machine_options['software_irq_entry']=True
    if banks:machine_options['software_irq_entry_banks']=True
    if idle_wait:machine_options['idle_wait_projection']=True
    if rti:machine_options['software_irq_rti']=True
    if gpio_out_readback:machine_options['gpio_out_readback']=True
    if kick_wake:machine_options['cpu1_control_kick_wake']=True
    if repeat:machine_options['repeat_irq_delivery']=True
    options={'initial_software_irq_pending':(0,0)}
    if entry:options['software_irq_delivery_requests']=True
    if unmaskable:options['unmaskable_irq_projection']=True
    if hardware_crc:options['hardware_crc_projection']=True
    # icu_region:opt-in 的两核 ICU region 寄存器组(0xffe400/0xffec00 的
    # +0x14/+0x18/+0x10)。ROM 0xc726 把 VM flash 窗口配进两核 ICU:每核
    # [+0x14]=begin、[+0x18]=end-1(write-only 记录)、[+0x10]=命令字
    # 0xca000020/0x40(与 0xca000010 同族,复用 CON bit30 完成投影)。默认关。
    if icu_region:options['icu_region_projection']=True
    # dcu_cmo:opt-in 的 DCU CMO 逐地址 cache 维护口(0xff05c8/0xff05cc,
    # SDK csfr.h JL_L1P 的 CMO[18]/CMO[19],__WO 只写)。sfc_drop_cache 两核
    # region 命令完成后尾调 flushinv_dcache(0xc71a,r2=0xff05cc):0xc6f4 把
    # (beg,len) 折算成 32 字节行数,寄存器计数 `rep r1,4` 体=add.rrr.p(r0+=32)
    # +st.w [r2]=旧 r0 并行对,每行写一次口——write-only 记录,读回一律拒绝。
    # 默认关。
    if dcu_cmo:options['dcu_cmo_projection']=True
    # rep_block:opt-in 的多指令 rep.i 体执行(0x6010b14 rep 4 32 字节拷贝,
    # 及其 callee 0x6010a64 内的四个 rep 4 8)。默认关闭,legacy 报告不变。
    # rep_unknown_transport:opt-in 的 rep 拷贝体未知字节运输(0x600a352 rep 4 4
    # 从 qcy_boot_info 0x122800 拷 16 字节,其中 bytes 1-3/8-11 是 boot.h 里
    # vm_info 的 C 对齐 padding 与 vm_res 保留字段,app/ROM 均无写入者,真机
    # 为残留 RAM 垃圾;模型按字节来源运输未知,不臆造数值)。默认关闭。
    extra={'software_irq_frames':dict(HANDLER_PSR_FRAMES)} if entry else {}
    if rep_block:extra['immediate_rep_multi_block']=True
    if rep_unknown_transport:extra['rep_copy_unknown_transport']=True
    # 资源表扫描从 flash 基址 0x06000000 读 app 映射(ENTRY 之下、app 结束
    # 之后)之外的已知真实字节:按需挂完整 sfc 明文只读区,同样不影响默认映射。
    if flash_image_bytes is not None:readonly=((0x06000000,flash_image_bytes),)+tuple(readonly)
    if sfc_suspend:
        # The SFC suspend tail sets 0xfd1c00|=1 (write-only RMW projection) and
        # then the wrapper reads the second SFC 0xfe0100 CON. The second SFC is
        # never referenced by the ROM (byte search) and the H3S boots from a
        # single 2MiB flash on 0xfe0200, so 0xfe0100 stays at its reset state;
        # CON=0 is an explicit, unmeasured reset assumption, not a default.
        options['sfc_clock_gate_projection']=True
        options['secondary_sfc_read_bus']=True
        options['initial_secondary_sfc_control']=0
    return dual_case(module,app,header,rom,name,abstract=True,readonly=readonly,
                     machine_options=machine_options,options=options,budget=250000,
                     extra_diagnostics=extra or None)


def check_delivery(r,*,before_rti=True):
    m=r['machine'];projection=m['software_irq_entry_projection']
    require(len(projection['deliveries'])==1,'scheduler IPI was not delivered exactly once')
    d=projection['deliveries'][0]
    require(d['core']==0 and d['irq']==127 and d['vector_address']=='0x19fffc' and
            d['handler']=='0x12ad2a' and d['reti_load_is_assumption'] and
            d['entry_sp_bank_or_icfg_effects_certified'] is False,'unexpected delivery record')
    if before_rti:
        require(m['cores']['0']['reti']==d['reti_from'],'RETI did not keep the interrupted PC')
    return d


def check(report):
    rows={r['name']:r for r in report['scenarios']}
    r=rows['irq127_request_strict']
    require(r['completed_steps']==149660 and r['stop_core']==0 and r['stop_pc']=='0x12b608' and
            r['stop']=={'kind':'software_irq_delivery_not_implemented','core':0,'irq':127},
            'strict path must still stop before the unimplemented entry')
    r=rows['irq127_entry_minimal']
    d=check_delivery(r)
    pending=r['peripherals']['software_irq_commands']['pending']['0']
    require(pending=={'known_mask':'0xf','known_value':'0x0'} and
            r['peripherals']['software_irq_commands']['acknowledgements']==1,
            'pending bit was not acknowledged exactly once by the delivery assumption')
    source=r['peripherals']['software_irq_commands']['source_enable']['0']
    require(source=={'known_mask':'0x8','known_value':'0x0'} and
            r['peripherals']['software_irq_commands']['source_commands']>0,
            'handler did not mask its own software-IRQ source at entry')
    require(r['peripherals']['ipmask']==7,'handler did not raise the real IPMASK to 7')
    require(r['stop_pc']=='0x12ad68' and
            r['stop']['kind']=='unknown_core_special_register' and r['stop']['register']=='usp',
            'handler without the bank assumption must stop at the unknown USP')
    adapters=r['instruction_adapters']
    require(adapters.get('software_irq_handler_psr_frame')==2 and
            adapters.get('software_irq_source_enable_rmw',0)>0,
            'entry small-frame push/pop or source masking was skipped')
    executed=[(row['pc'],row.get('instruction_id')) for row in r['last_instructions']]
    require(('0x12ad5c','st.w.modimm') in executed and ('0x12ad66','csync') in executed,
            'handler head instructions were not really executed')
    r=rows['irq127_scheduler_entry']
    d=check_delivery(r)
    require(r['machine']['software_irq_entry_projection']['entry_bank_assumption'] and
            d['entry_banks'] and not d['entry_banks']['silicon_bank_switch_certified'],
            'scheduler entry lacks the explicit bank assumption record')
    adapters=r['instruction_adapters']
    require(adapters.get('multicore_visible_sp_from_usp')==1 and
            adapters.get('software_irq_handler_context_frame')==1 and
            adapters.get('multicore_visible_sp_from_ssp',0)>=1,
            'handler bank switch or 76-byte context frame was skipped')
    require(r['completed_steps']>150000 and r['stop_core']==1 and r['stop_pc']=='0x12c5ca' and
            r['stop']['kind']=='unsupported_runtime_instruction' and
            r['stop']['instruction_id']=='idle',
            'the real scheduler must run and the stop must be the peer-core IDLE boundary')
    require(r['completed_steps']>=151000,'scheduler body 0x12adec did not execute')
    r=rows['irq127_appcore_roundtrip']
    d=check_delivery(r,before_rti=False)
    events=r['machine']['events']
    rti=[e for e in events if e['kind']=='software_irq_rti']
    require(len(rti)==1 and rti[0]['core']==0 and rti[0]['pc_after']=='0x6012028' and
            rti[0]['reti_from_frame']=='0x6012028' and rti[0]['bank_writeback_is_assumption'],
            'the restored task did not enter the real app_core entry through RTI')
    restored=[e for e in events if e['kind']=='scheduler_task_gprs_restored']
    require(restored and restored[0]['unknown_registers']==list(range(1,16)),
            'scheduler GPR restore lost the unknown task slots')
    require(any(e['kind']=='core_idle_wait_entered' for e in events),
            'peer core never reached its IDLE wait under the projection')
    require(r['stop']['kind']=='unmodeled_unmaskable_irq' and r['stop']['core']==1 and
            r['stop']['source']=='software_irq',
            'round trip must stop at the next real boundary: the CPU1 unmaskable software IRQ')
    require(r['completed_steps']>=153000,'app_core task did not execute after RTI')
    r=rows['irq125_sync_roundtrip']
    deliveries=r['machine']['software_irq_entry_projection']['deliveries']
    require([(d['core'],d['irq'],d['handler']) for d in deliveries]==
            [(0,127,'0x12ad2a'),(1,125,'0x60662c8')],
            'the peer sync IPI was not delivered to the real CPU1 handler')
    sync=deliveries[1]
    require(sync['vector_address']=='0x19fff4' and sync['entry_banks'] and
            not sync['entry_banks']['silicon_bank_switch_certified'],'CPU1 sync delivery record differs')
    require(r['peripherals']['software_irq_commands']['pending']['1']=={'known_mask':'0xf','known_value':'0x0'},
            'CPU1 sync pending was not acknowledged by its real handler')
    gpio=r['peripherals']['gpio'].get('0xfd5308')
    require(gpio=={'known_value':'0x60','known_mask':'0x68'},
            'the register-AND must clear the known bit3 and leave the masked-out bit0 unknown')
    out=r['peripherals']['gpio'].get('0xfd5300')
    require(out=={'known_value':'0x8','known_mask':'0x8'},
            'the SFC resume must re-set the known bit3 on GPIO OUT without fabricating its neighbours')
    adapters=r['instruction_adapters']
    require(adapters.get('gpio_out_readback_load')==12 and
            adapters.get('gpio_out_readback_bit_mask')==4 and
            adapters.get('gpio_out_readback_register_copy')==1 and
            adapters.get('gpio_out_readback_writeback')==2,
            'the SFC GPIO OUT read-back path was not exercised through its exact load/mask/copy/write-back shape')
    # The CPU1 handler tail pops its real {psr,rets,reti} frame (0x606633e,
    # app disassembly: pop.rmask r4-r0 / pop.srl m=41 / csync / rti) and the
    # symmetric RTI returns the core to its interrupted idle instruction.
    require(adapters.get('conditional_software_irq_rti')==2 and
            adapters.get('software_irq_handler_context_frame')==4,
            'the CPU1 handler tail frame pop and symmetric RTI were skipped')
    rti=[e for e in r['machine']['events'] if e['kind']=='software_irq_rti']
    require(len(rti)==2 and rti[1]['core']==1 and rti[1]['pc_after']=='0x12c5ca' and
            rti[1]['reti_from_frame']=='0x12c5ca' and rti[1]['sp_after']=='0x401bf8' and
            rti[1]['bank_writeback_is_assumption'],
            'the CPU1 sync handler did not return to its interrupted idle task through RTI')
    events=r['machine']['events']
    parks=[i for i,e in enumerate(events) if e['kind']=='core_idle_wait_entered' and
           e['core']==1 and e['pc']=='0x12c5ca']
    require(len(parks)==2 and parks[1]>events.index(rti[1]),
            'the returned CPU1 idle task did not re-park after its RTI (first park + re-park)')
    require(r['stop_core']==0 and r['stop_pc']=='0x6000f44' and
            r['stop']=={'kind':'unmapped_or_readonly_write','access':'write','address':'0xfd3504',
                        'size':4,'value_bytes':'00000000'},
            'after the peer return the stop must be CPU0 reaching the CRC engine control write')
    require(r['completed_steps']==155127,'the CPU1 handler return path changed its step count')
    held=r['peripherals']['cpu1_held_projection']
    require(held['current_control']==9 and held['kick_rmw_writes']==3,
            'the CPU1 PMU_CON0 kick bit was not recorded as exactly three |=8 retries')
    require(r['instruction_adapters'].get('cpu1_control_rmw')==4,
            'the release RMW plus three kick RMWs must be the only CPU1 control RMWs')
    wakes=[e for e in r['machine']['events'] if e.get('kind')=='core_woken_by_cpu1_control_kick']
    require(len(wakes)==1 and wakes[0]['pc_after']=='0x606631a' and
            wakes[0]['idle_completion_is_assumption'] and wakes[0]['wake_source_unverified'],
            'the kick wake of the parked peer core must stay an explicit assumption')
    require('cpu1_control_kick_bit3' in r['peripherals']['assumptions'],
            'the PMU_CON0 bit3 kick assumption must be recorded explicitly')
    gate=r['peripherals']['sfc_clock_gate_projection']
    require(gate['operations']==list(SFC_RESUME_OPERATIONS),
            'the SFC suspend/resume clock-gate writes changed')
    require(gate['divider']=='0xff' and gate['divider_writes']==36 and gate['reads']==36 and
            gate['readback_modeled'] is True and gate['known_value']=='0x5000' and
            gate['known_mask']=='0x5c09',
            'the SFC +8 divider write/read-back or the PLL-lock bit15 projection differs')
    require(r['peripherals']['sfc_read_bus_secondary']['con']==0 and
            r['peripherals']['sfc_read_bus_secondary']['writes']==0 and
            r['peripherals']['sfc_read_bus_secondary']['initial_control_fixture']==0,
            'the second SFC (0xfe0100) reset-state fixture or write count differs')
    r=rows['irq125_repeat_inert']
    # 暂停点1(repeat_irq_delivery)的实证边界:新投影打开后,前沿前缀必须完全
    # 不变(该场景不启用 hardware_crc,停在 155,127 的 CRC 引擎种子写)。前缀内
    # 固件对每核只 pend 过一次(commands=4:两核各 pend+clear,CPU1 handler 入口
    # 自清),第二次交付从未被请求;投影保持惰性,直到固件真实地再次 pend。
    require(r['completed_steps']==155127 and r['stop_core']==0 and r['stop_pc']=='0x6000f44' and
            r['stop']=={'kind':'unmapped_or_readonly_write','access':'write','address':'0xfd3504',
                        'size':4,'value_bytes':'00000000'},
            'the repeat delivery projection must not move the frontier stop')
    require(r['machine']['repeat_irq_delivery_projection']['enabled'] and
            not r['machine']['repeat_irq_delivery_projection']['nesting_implemented'] and
            r['machine']['repeat_irq_delivery_projection']['pending_rearmed_only_by_real_firmware_command'],
            'the repeat delivery projection is missing from the machine report')
    deliveries=r['machine']['software_irq_entry_projection']['deliveries']
    require([(d['core'],d['irq']) for d in deliveries]==[(0,127),(1,125)],
            'enabling repeat delivery changed the delivery set before the frontier')
    require(r['peripherals']['software_irq_commands']['commands']==4 and
            r['peripherals']['software_irq_commands']['acknowledgements']==2,
            'the firmware issued unexpected extra software IRQ commands before the frontier')
    r=rows['crc_engine_roundtrip']
    deliveries=r['machine']['software_irq_entry_projection']['deliveries']
    require([(d['core'],d['irq'],d['handler']) for d in deliveries[:2]]==
            [(0,127,'0x12ad2a'),(1,125,'0x60662c8')],
            'the CRC scenario must reuse the exact scheduler/sync deliveries')
    crc=r['peripherals']['hardware_crc_projection']
    # The wrapper (app 0x6000f2c) writes seed 0, feeds the 30 bytes at 0x400b22
    # one st.w each, csyncs and reads the result once. The expected value is
    # recomputed here from the app software CRC-16 parameters (poly 0x1021,
    # MSB-first, init=seed, no xorout) so the projection is checked against the
    # golden software algorithm, not against its own implementation.
    fed=bytes.fromhex('60e0e0c183070f3e5c983142a468f0e1c3872e7cd8b163e6ecd9b367cf9f')
    reference=0
    for byte in fed:
        reference^=byte<<8
        for _ in range(8):
            reference=((reference<<1)^0x1021)&0xffff if reference&0x8000 else (reference<<1)&0xffff
    require(crc['seed_writes'][0]=='0x0' and crc['result_reads'][0]==hex(reference) and
            crc['fed_bytes']>=len(fed),
            'the hardware CRC projection did not reproduce the software-table result for the actual wrapper input')
    # sfc_drop_cache 之后 VM init 链继续:同一 CRC 引擎被再用 11 次(每次种子
    # 0、喂 30 字节、读一次结果,共 12 次往返 360 字节),首轮黄金校验不变。
    require(len(crc['seed_writes'])==12 and crc['fed_bytes']==360 and len(crc['result_reads'])==12,
            'the extended VM-init prefix changed the hardware CRC usage shape')
    # 0x6010b14 rep 4 32(4 字节体 ld.bu.pin+st.b.pin × 32,flash 资源表头部
    # 32 字节拷贝)及其后整条资源表扫描(含 callee 0x6010a64 内四个 rep 4 8)
    # 全部按多指令体真实执行:70 个 rep.i 体、155,389→162,447 步;扫描完成后
    # 的一次性 SFC 时钟重配(0x12762c 标志守卫)写 0xfd1c04=3(+4 只写记录)。
    # 之后 0x600a352 rep 4 4 从 qcy_boot_info 0x122800 拷 16 字节头:bytes
    # 1-3/8-11 无任何写入者(boot.h vm_info 的 C padding 与 vm_res),按
    # rep_copy_unknown_transport 运输未知字节;消费函数 0x600a2aa 只用
    # vm_saddr(word 4-7,VM init 0x6011102 写入的 0x1da000),call 0x12bdc6
    # 算出 VM 区窗口地址 0x061d7fe0(=0x06000000+0x1da000-0x2020)。随后
    # 0x600a36e..0x600a37e 的 SFC 窗口组写入(r1=0xfe0218:[+12]=0x061d7fe0、
    # [+8]=0x7ffffff、[0]|=2)按 write-only 记录走完(sfc_read_bus 扩展),
    # call 0x12c030 后 CPU0 真实地第二次 pend irq125 到 CPU1(repeat 投影
    # +cpu1_control bit0 释放位修正),第二次交付把 CPU1 从停泊唤醒、
    # handler 处理完回停泊。之后 VM init 链 call ROM export sfc_drop_cache
    # (0x1fdcc→0xc726,实参 beg=0x061d7fe0/len=0x20):把 [0x061d7fe0,
    # 0x061d8000) 配进两核 ICU(每核 cli→testset 0x19fd78 抢锁→[+0x14]=begin、
    # [+0x18]=end-1(write-only 记录)→[+0x10]=0xca000040(0xc72c if.ge len>=
    # 0x1000 在 0xc730/0xc736 两条谓词 li 里二选一,实测 len=0x20 选 0x40)→
    # call 0xbac0 等 CON bit30 完成(与 0xca000010 使能路径同一条轮询指令)→
    # call 0xbad4 释放锁),r3+=0x800 换核重复 2 次。region 命令完成后尾调
    # flushinv_dcache(0x1fdb0→0xc71a→0xc6f4):0xff05cc 逐地址失效循环,体是
    # 寄存器计数 `rep r1,4`(add.rrr.p+st.w 并行对)。三件套建模后走通:
    # ①register_rep_multi_block(immediate_rep_multi_block 推广到寄存器计数
    # rep,计数来自真实寄存器值,计数寄存器按 FM-1 run3 证据递减到 0,体后
    # 编译器生成的 bnez r1 自然不跳);②并行对体按 exact_parallel_stores 的
    # 旧寄存器语义执行(存储半读 A 之前的旧 r0,首写 [0xff05cc]=0x061d7fe0
    # =行地址,r0 递增 32);③dcu_cmo_projection 把 0xff05cc 写入记为
    # write-only 序列(SDK csfr.h JL_L1P 的 CMO[19],__WO;flush_dcache 用的
    # 0xff05c8=CMO[18] 一并建模,本前缀未出现)。flushinv_dcache 尾部
    # csync+ssync 走通(ssync 按 run4「无可见效果」模型),返回 sfc_drop_cache、
    # VM init 链继续 12k 步,停在 ROM 0x4176 字节拷贝尾循环 `rep r2,4`——
    # 真实 len&3==0,寄存器计数 rep 的 count=0 语义 BR28 未测量(GAPS.md
    # 「Not tried: rep with a count of 0」),诚实停(unverified_zero_count_rep)。
    require(r['stop_core']==0 and r['stop_pc']=='0x4176' and
            r['stop']=={'kind':'unverified_zero_count_rep','instruction_id':'rep',
                        'note':'register-count REP; BR28 count=0 semantics unmeasured'},
            'after the DCU invalidate loop the stop must be the genuine zero-count register REP tail')
    require(r['completed_steps']==175396,'the DCU CMO projection changed its step count')
    dcu=r['peripherals']['dcu_cmo_projection']
    require(dcu['ports']['0xff05cc']=={'values':['0x61d7fe0'],'writes':1} and
            dcu['ports']['0xff05c8']=={'values':[],'writes':0},
            'the DCU invalidate port did not record exactly the one observed line address')
    require(dcu['write_only'] and not dcu['readback_observed'] and
            not dcu['port_index_semantics_verified'] and
            not dcu['cache_contents_or_coherence_simulated'],
            'the DCU CMO projection report is missing its explicit assumption record')
    require(r['instruction_adapters'].get('register_rep_multi_block_projection')==5,
            'the register-count REP projection must cover exactly the DCU loop (0xc70c) plus the '
            'four nonzero ROM memcpy counters (0x416a/0x4176 family) in the extended prefix')
    gate=r['peripherals']['sfc_clock_gate_projection']
    require(gate['select']=='0x3' and gate['select_writes']==1 and
            gate['select_write_only'] is True and gate['select_readback_observed'] is False,
            'the SFC clock +4 select write-only record differs')
    # SFC 主块窗口组(0xfe0218/+0x20/+0x24)write-only 记录:初始配置序列
    # 各写一次,+0x18 只有一次 |=2(or 置位,不经读回)。
    window=r['peripherals']['sfc_read_bus']['window_registers']
    require(window['base']=='0x61d7fe0' and window['base_writes']==1 and
            window['limit']=='0x7ffffff' and window['limit_writes']==1 and
            window['control_operations']==[{'kind':'or','operand':'0x2'}] and
            window['control_known_mask']=='0x2' and
            window['write_only'] is True and window['readback_observed'] is False,
            'the SFC window register record differs')
    require(r['instruction_adapters'].get('sfc_window_enable_rmw')==1,
            'the SFC window control RMW was not executed exactly once')
    # 两核 ICU region 组(opt-in icu_region_projection):sfc_drop_cache 把
    # [0x061d7fe0,0x061d8000) 配进两核——+0x14/+0x18 各一写(write-only 记录),
    # +0x10 命令 0xca000040 与 0xca000010(bootstrap 使能)同走 CON bit30
    # 完成投影(64 步 fixture),两核各完成一次,busy 读 14→74。
    icu=r['peripherals']['icu_initialization_projection']
    region=icu['region_projection']
    require(icu['commands']==[{'address':'0xffec10','value':'0xca000010'},
                              {'address':'0xffe410','value':'0xca000040'},
                              {'address':'0xffec10','value':'0xca000040'}] and
            icu['completed_commands']==3 and icu['busy_reads']==74 and not icu['pending_cores'],
            'the two-core ICU region commands did not run through the shared completion projection')
    require(region['registers']['0xffe400']=={'0x14':{'value':'0x61d7fe0','writes':1},
                                              '0x18':{'value':'0x61d7fff','writes':1}} and
            region['registers']['0xffec00']=={'0x14':{'value':'0x61d7fe0','writes':1},
                                              '0x18':{'value':'0x61d7fff','writes':1}},
            'the ICU region begin/end records differ from the observed sfc_drop_cache arguments')
    require(region['write_only'] and not region['readback_observed'] and
            not region['command_low_byte_semantics_verified'] and
            region['same_busy_machinery_as_command4'],
            'the ICU region projection report is missing its explicit assumption record')
    # 窗口组放行后 CPU0 第二次 pend irq125(commands 4→6),第二次交付把
    # 停泊的 CPU1 再唤醒一次:暂停点1 的真实第二次交付在 162,898 前沿被观察
    # 到。DCU 失效循环走通后 VM init 链继续与 CPU1 同步:又 pend 4 次(合计
    # commands 14/acks 7,交付 1+6 次,CPU1 每次回同一停泊 0x12c5ca)。
    deliveries=r['machine']['software_irq_entry_projection']['deliveries']
    require([(d['core'],d['irq'],d['handler'],d['reti_from']) for d in deliveries]==
            [(0,127,'0x12ad2a','0x12b608')]+
            [(1,125,'0x60662c8','0x12c5ca')]*6,
            'the repeated cross-core deliveries to the parked CPU1 did not occur through the repeat projection')
    require(r['peripherals']['software_irq_commands']['commands']==14 and
            r['peripherals']['software_irq_commands']['acknowledgements']==7,
            'the VM-init sync requests changed the software IRQ command bookkeeping')
    # 70 个体来自资源表扫描,第 71 个是 0x600a352 rep 4 4(拷 16 字节 boot_info
    # 头);其中第 1/3 轮(word0 含 padding、word2 是 vm_res)走未知字节运输,
    # 第 2/4 轮(vm_saddr/vm_size 全已知)走常规严格读。DCU 失效循环走通后
    # VM init 链的 ROM 拷贝族再加 9 个 rep.i 体(71→80)。
    require(r['instruction_adapters'].get('immediate_rep_multi_block_projection')==80,
            'the flash resource-table scan must execute its rep.i blocks through the multi-block projection')
    require(r['instruction_adapters'].get('rep_copy_unknown_transport_load')==2 and
            r['instruction_adapters'].get('rep_copy_unknown_transport_store')==2,
            'the boot-info padding/reserved words were not transported exactly twice')
    report['irq127_scheduler_loop_entered']=True
    report['app_core_entered_after_irq_rti']=True
    report['dual_core_sync_handshake_executed']=True
    report['hardware_crc_engine_modeled']=True
    for r in report.get('candidate_prefixes',[]):
        d=r['machine']['software_irq_entry_projection']['deliveries']
        require(len(d)==1 and d[0]['vector_address']=='0x19fffc','candidate delivery differs')
        require(r['stop']['kind']=='unknown_core_special_register' and r['stop'].get('register')=='usp',
                'candidate handler left the modeled region unexpectedly')
    report['irq127_entry_checks_passed']=True
    report['generic_irq_entry_implemented']=False
    report['repeat_delivery_second_observation']=('observed at the 162898 frontier: after the SFC window '
        'writes are recorded, CPU0 really pends irq125 to CPU1 a second time (software IRQ commands '
        '4 to 6) and the repeat delivery projection wakes the parked CPU1 once more; the handler '
        'returns to the same idle park 0x12c5ca. Delivering it also required the cpu1_control '
        'release-bit (bit0) fix: the kick bit3 record (control word 9) must not revoke the released '
        'core delivery eligibility that the advance() pending checks gate on')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--rom',type=Path,required=True)
    p.add_argument('--firmware',type=Path,default=ROOT/'firmware/QCY_H3S_7018F_1T2_release_note_V1.1.8_011.ufw')
    p.add_argument('--repaired-candidate',type=Path);p.add_argument('--failed-r2',type=Path)
    p.add_argument('--report',type=Path,required=True);args=p.parse_args()
    rom=args.rom.read_bytes();check_rom(rom);module=upstream();app=application(args.firmware);header=package_header(args.firmware)
    image=flash_image(args.firmware)
    report={'firmware_sha256':OFFICIAL_SHA,'application_sha256':APP_SHA,'rom_sha256':ROM_SHA,'simulator_commit':PIN,
        'device_accessed':False,'whole_h3s_boot_simulated':False,'candidate_whole_boot_validated':False,
        'suitable_for_flash_approval_alone':False,
        'vector_slot_formula_evidence':'app bytes [r1+r0<<2]=r2 at 0x6065d90 after li 0x19FE00; caller 0x6066100 passes irq=0x7f-cnum handler=0x12AD2A',
        'scenarios':[irq_case(module,app,header,rom,'irq127_request_strict'),
                     irq_case(module,app,header,rom,'irq127_entry_minimal',entry=True),
                     irq_case(module,app,header,rom,'irq127_scheduler_entry',entry=True,banks=True),
                     irq_case(module,app,header,rom,'irq127_appcore_roundtrip',
                              entry=True,banks=True,idle_wait=True,rti=True),
                     irq_case(module,app,header,rom,'irq125_sync_roundtrip',
                              entry=True,banks=True,idle_wait=True,rti=True,unmaskable=True,
                              sfc_suspend=True,gpio_out_readback=True,kick_wake=True),
                     irq_case(module,app,header,rom,'irq125_repeat_inert',
                              entry=True,banks=True,idle_wait=True,rti=True,unmaskable=True,
                              sfc_suspend=True,gpio_out_readback=True,kick_wake=True,repeat=True),
                     irq_case(module,app,header,rom,'crc_engine_roundtrip',
                              entry=True,banks=True,idle_wait=True,rti=True,unmaskable=True,
                              sfc_suspend=True,gpio_out_readback=True,kick_wake=True,hardware_crc=True,
                              rep_block=True,rep_unknown_transport=True,repeat=True,icu_region=True,
                              dcu_cmo=True,flash_image_bytes=image)]}
    for name,path,digest in [('repaired_candidate',args.repaired_candidate,REPAIRED_SHA),('archived_failed_r2',args.failed_r2,FAILED_SHA)]:
        if path is None:continue
        candidate,readonly=candidate_view(path,digest,args.firmware)
        r=irq_case(module,candidate,header,rom,name,entry=True,readonly=readonly);r['ufw_sha256']=digest
        report.setdefault('candidate_prefixes',[]).append(r)
    check(report);args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(report,indent=2)+'\n')


if __name__=='__main__':main()
