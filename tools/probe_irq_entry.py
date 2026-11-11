#!/usr/bin/env python3
"""Minimal IRQ127 software-interrupt entry on the real startup prefix.

The scheduler IPI (pending 0x8 set by the actual QCY code at 0x60644a6)
reaches an opt-in CPU entry: RETI loads the interrupted PC and execution
continues at the vector slot written by the firmware. Entry banks, RETI
load and pending acknowledgement are explicit assumptions; generic IRQ
entry, IDLE wake and hardware frame writes stay unmodeled. No flash approval.
"""
import argparse
import json
from pathlib import Path

from probe_dualcore import dual_case
from probe_task_returns import EMPTY_PRIORITIES
from probe_arch_startup import candidate_view, REPAIRED_SHA, FAILED_SHA
from probe_boot import application, check_rom, ROM_SHA, OFFICIAL_SHA, APP_SHA
from probe_boot_handoff import package_header
from probe_pi32v2 import ROOT, PIN, upstream, require

HANDLER_PSR_FRAMES={0x12ad30:'psr_push',0x12ad60:'psr_pop',0x12adae:'psr_push',0x12adde:'psr_pop',
                    0x12ad6e:'frame12_push',0x12ada0:'frame12_pop',
                    0x60662c8:'frame12_push'}


def irq_case(module,app,header,rom,name,*,entry=False,banks=False,idle_wait=False,rti=False,
             unmaskable=False,readonly=()):
    machine_options={**EMPTY_PRIORITIES,'first_task_return_hypothesis':'open_gate8',
                     'defer_core1_first_idle':True}
    if entry:machine_options['software_irq_entry']=True
    if banks:machine_options['software_irq_entry_banks']=True
    if idle_wait:machine_options['idle_wait_projection']=True
    if rti:machine_options['software_irq_rti']=True
    options={'initial_software_irq_pending':(0,0)}
    if entry:options['software_irq_delivery_requests']=True
    if unmaskable:options['unmaskable_irq_projection']=True
    return dual_case(module,app,header,rom,name,abstract=True,readonly=readonly,
                     machine_options=machine_options,options=options,budget=250000,
                     extra_diagnostics={'software_irq_frames':dict(HANDLER_PSR_FRAMES)} if entry else None)


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
    require(r['stop_core']==0 and r['stop_pc']=='0x12b8fe' and
            r['stop']['kind']=='uninitialized_gpio_bits' and r['stop']['address']=='0xfd5308',
            'after the sync handshake and SFC suspend the stop must be the unknown GPIO bits')
    require(r['completed_steps']>153430,'the CPU0 busy-wait did not resume after the CPU1 ack')
    report['irq127_scheduler_loop_entered']=True
    report['app_core_entered_after_irq_rti']=True
    report['dual_core_sync_handshake_executed']=True
    for r in report.get('candidate_prefixes',[]):
        d=r['machine']['software_irq_entry_projection']['deliveries']
        require(len(d)==1 and d[0]['vector_address']=='0x19fffc','candidate delivery differs')
        require(r['stop']['kind']=='unknown_core_special_register' and r['stop'].get('register')=='usp',
                'candidate handler left the modeled region unexpectedly')
    report['irq127_entry_checks_passed']=True
    report['generic_irq_entry_implemented']=False


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--rom',type=Path,required=True)
    p.add_argument('--firmware',type=Path,default=ROOT/'firmware/QCY_H3S_7018F_1T2_release_note_V1.1.8_011.ufw')
    p.add_argument('--repaired-candidate',type=Path);p.add_argument('--failed-r2',type=Path)
    p.add_argument('--report',type=Path,required=True);args=p.parse_args()
    rom=args.rom.read_bytes();check_rom(rom);module=upstream();app=application(args.firmware);header=package_header(args.firmware)
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
                              entry=True,banks=True,idle_wait=True,rti=True,unmaskable=True)]}
    for name,path,digest in [('repaired_candidate',args.repaired_candidate,REPAIRED_SHA),('archived_failed_r2',args.failed_r2,FAILED_SHA)]:
        if path is None:continue
        candidate,readonly=candidate_view(path,digest,args.firmware)
        r=irq_case(module,candidate,header,rom,name,entry=True,readonly=readonly);r['ufw_sha256']=digest
        report.setdefault('candidate_prefixes',[]).append(r)
    check(report);args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(report,indent=2)+'\n')


if __name__=='__main__':main()
