#!/usr/bin/env python3
"""Actual two-core ROM/platform execution, strict at unknown task GPR slots.

Reset/mode/cache/WO-command policies are explicit unmeasured projections.
No ready injection, IRQ delivery, firmware writes or flash approval.
"""
import argparse
import json
from pathlib import Path

from br28_machine import DualCoreMachine
from probe_platform_startup import platform_case, WATCH
from probe_arch_startup import candidate_view, REPAIRED_SHA, FAILED_SHA
from probe_boot import application, check_rom, ROM_SHA, OFFICIAL_SHA, APP_SHA
from probe_boot_handoff import package_header
from probe_pi32v2 import ROOT, PIN, upstream, require

CORE1_STACK=((0x123840,0x124840,'cpu1_stack'),)
SNAPSHOTS=(('os_running',0x1265f4,1),('cache_lock',0x19fd78,1),('current_tcbs',0x126c5c,8),
           ('first_core1_frame_head',0x19f630,16),('first_core1_unknown_gprs',0x19f640,60))


def dual_case(module,app,header,rom,name,*,reset_pc=0,icfg=0,bootstrap=True,stack=True,
              options=None,budget=250000,readonly=(),abstract=False,machine_options=None,watch_pcs=(),
              extra_diagnostics=None):
    machine=DualCoreMachine(cpu1_reset_pc=reset_pc,cpu1_icfg=icfg,irq1_config=[0]*32,ipmask1=0,
                            bootstrap_rti=bootstrap,unknown_task_gprs=abstract,**(machine_options or {}))
    params={'initial_debug_controls':(0,0),'icu_initialization_projection':True,'icu_command_steps':64,
            'software_irq_command_projection':True}
    params.update(options or {})
    diagnostics={'exact_parallel_extensions':True,'watch_pcs':WATCH+[0x606602a,0x6066034,0x6066120,0x6066126]+list(watch_pcs),
                 'watch_registers':[0,1,4]}
    diagnostics.update(extra_diagnostics or {})
    return platform_case(module,app,header,rom,name,machine=machine,options=params,
        diagnostic_options=diagnostics,extra_regions=CORE1_STACK if stack else (),extra_snapshots=SNAPSHOTS,
        readonly=readonly,budget=budget)


def run(module,app,header,rom):
    cases=[('reset_vector_unknown',{'reset_pc':None}),
        ('cpu1_guard_unknown',{'options':{'initial_debug_controls':(0,None)}}),
        ('bootstrap_rti_strict',{'bootstrap':False}),
        ('bootstrap_icfg_unknown',{'icfg':None}),
        ('cpu1_stack_unmapped',{'stack':False}),
        ('icu_progress_unknown',{'options':{'icu_command_steps':None}}),
        ('icu_clock_stopped',{'options':{'icu_command_steps':0},'budget':150000}),
        ('software_irq_wo_strict',{'options':{'software_irq_command_projection':False}}),
        ('dualcore_first_task',{}),('first_task_rti_strict',{'abstract':True})]
    return [dual_case(module,app,header,rom,name,**params) for name,params in cases]


def check_frontier(r,upper=0x060c3338):
    require(r['completed_steps']==147263 and r['stop_core']==1 and r['stop_pc']=='0x6066128' and
            r['stop']['kind']=='uninitialized_memory' and r['stop']['address']=='0x19f640',
            'actual first-task unknown-register frontier changed or stopped early')
    machine=r['machine'];cores=machine['cores'];p=r['peripherals']
    require(machine['launches']==1 and machine['stalls']==2129 and not machine['ready_injected'],
            'CPU1 launch/real lock waits changed or readiness injected')
    require((cores['0']['steps'],cores['1']['steps'])==(136403,10860),'per-core instruction progress differs')
    require(cores['1']['sp']=='0x401bbc' and cores['1']['reti']=='0x12c57e' and
            cores['1']['ssp']==cores['1']['usp']=='unknown','task SP or unresolved stack bank values fabricated')
    anchors=machine['cpu1_boot_anchors']
    require([a['pc'] for a in anchors]==[hex(p) for p in (0,0xa144,0xc5fe,0xc602,0x12caf4,0x12cafa,0x12cb00)] and
            anchors[1]['r0']=='0x1' and anchors[2]['r0']=='0x12caf4' and anchors[3]['rets']=='0xc604',
            'actual ROM CPU identity/vector/CALL path not executed')
    notes=[x for x in machine['events'] if x.get('kind')=='guest_notification_write' and x.get('core')==1]
    require(any(x['pc']=='0x606602a' and x['address']=='0x1265f5' and x['value']=='0x1' for x in notes) and
            any(x['pc']=='0x6066034' and x['address']=='0x19ff54' and x['value']=='0x1' for x in notes),
            'CPU1 did not execute its actual notification writes')
    frames=[x for x in machine['events'] if x.get('kind')=='first_task_special_frame_restored']
    require(len(frames)==1 and frames[0]['core']==1 and frames[0]['tcb']=='0x127e10' and
            frames[0]['frame']=='0x401bb0' and frames[0]['sp_after']=='0x401bbc',
            'first task bypassed actual TCB selection or frame restore')
    snap=r['memory_snapshots']
    require(snap['cpu1_ready']['bytes_with_unknowns']=='01' and snap['os_running']['bytes_with_unknowns']=='00' and
            snap['cache_lock']['bytes_with_unknowns']=='00' and snap['first_core1_unknown_gprs']['initialized_bytes']==0,
            'ready/scheduler/cache lock or unknown stack provenance differs')
    icu=p['icu_initialization_projection']
    require(icu['completed_commands']==1 and icu['busy_reads']>0 and not icu['pending_cores'] and
            icu['commands']==[{'address':'0xffec10','value':'0xca000010'}] and
            icu['con']['0xffec00']=='0x80000001','actual command busy wait or cache enable bypassed')
    require(p['software_irq_commands']['pending']['0']=={'known_mask':'0x8','known_value':'0x8'} and
            p['software_irq_commands']['commands']==1,'scheduler pending request lost or auto-acknowledged')
    require(p['shared_lock_owner']==1 and p['cpu1_held_projection']['secondary_cpu_executed'],
            'core lock or execution state fabricated')
    debug=p['debug_execution_projection']
    require(debug['violation'] is None and debug['checked_executing_fetches']==2*(r['completed_steps']+machine['stalls']+1),
            'executing PC guard bypassed')
    require(debug['registers']['0xffe218']==debug['registers']['0xffea18']==hex(upper),
            'per-core actual Flash limits do not match image')
    require(r['instruction_adapters']['conditional_cpu1_bootstrap_rti']==1 and
            r['instruction_adapters']['actual_first_task_special_frame']==1,'startup/restore adapters bypassed')


def check_task_rti_frontier(r,upper=0x060c3338):
    require(r['completed_steps']==147268 and r['stop_core']==1 and r['stop_pc']=='0x6066130' and
            r['stop']=={'kind':'unsupported_runtime_instruction','instruction_id':'rti'},
            'abstract task restore bypassed RTI or stopped early')
    m=r['machine'];c=m['cores']['1'];p=r['peripherals']
    require(m['unknown_task_gprs_preserved'] and m['stalls']==2130 and not m['ready_injected'] and
            (m['cores']['0']['steps'],c['steps'])==(136405,10863),'abstract task execution progress differs')
    require(c['sp']=='0x401bfc' and c['ssp']==c['usp']=='unknown' and c['reti']=='0x12c57e' and
            c['r']==['0x0']+['unknown']*15,'task initial GPRs or stack banks fabricated')
    require(c['unknown_gpr_sources']=={str(i):[hex(a) for a in range(0x19f63c+4*i,0x19f640+4*i)] for i in range(1,16)},
            'unknown task GPRs lost actual physical frame-byte provenance')
    require(p['shared_lock_owner']==0 and c['icfg']=='0x200','actual lockclr/STI or other-core acquisition skipped')
    require(p['software_irq_commands']['pending']['0']=={'known_mask':'0x8','known_value':'0x8'},
            'pending scheduler IRQ acknowledged to cross RTI')
    require(r['memory_snapshots']['first_core1_unknown_gprs']['initialized_bytes']==0,
            'initial task register slots were written to make them readable')
    gprs=[e for e in m['events'] if e.get('kind')=='first_task_gprs_restored']
    require(len(gprs)==1 and gprs[0]['unknown_registers']==list(range(1,16)) and gprs[0]['sp_after']=='0x401bfc',
            'actual full register pop not executed once')
    debug=p['debug_execution_projection']
    require(debug['violation'] is None and debug['checked_executing_fetches']==2*(r['completed_steps']+m['stalls']+1) and
            debug['registers']['0xffe218']==debug['registers']['0xffea18']==hex(upper),'per-core PC checks bypassed')


def check(report):
    rows={r['name']:r for r in report['scenarios']}
    r=rows['reset_vector_unknown']
    require(r['completed_steps']==127239 and r['stop']['kind']=='cpu1_reset_vector_unknown' and
            r['machine']['launches']==0 and r['peripherals']['cpu1_held_projection']['current_control']==0,
            'unknown reset vector changed release state')
    require(rows['cpu1_guard_unknown']['stop']['kind']=='unknown_initial_pc_guard' and
            rows['cpu1_guard_unknown']['machine']['cores']['1']['steps']==0,'CPU1 guard state copied from CPU0')
    for name,kind in [('bootstrap_rti_strict','cpu1_bootstrap_rti_contract_unknown'),
                      ('bootstrap_icfg_unknown','cpu1_bootstrap_icfg_fixture_unknown')]:
        r=rows[name]
        require(r['completed_steps']==127296 and r['stop_pc']=='0x12cb06' and r['stop']['kind']==kind and
                r['machine']['cores']['1']['steps']==28,'strict bootstrap path changed')
    require(rows['cpu1_stack_unmapped']['stop'].get('address')=='0x124838','CPU1 stack silently mapped/zeroed')
    require(rows['icu_progress_unknown']['stop']['kind']=='icu_command_progress_unknown','cache progress invented')
    stopped=rows['icu_clock_stopped']
    require(stopped['stop']['kind']=='instruction_budget' and stopped['completed_steps']==150000 and
            stopped['peripherals']['icu_initialization_projection']['pending_cores']==[1] and
            stopped['memory_snapshots']['cpu1_ready']['bytes_with_unknowns']=='00','stopped cache clock completed or ready injected')
    require(rows['software_irq_wo_strict']['stop'].get('address')=='0xffe1a0','WO readback implicitly fabricated')
    check_frontier(rows['dualcore_first_task'])
    check_task_rti_frontier(rows['first_task_rti_strict'])
    for r in report.get('candidate_prefixes',[]):check_frontier(r,0x060e31c5 if r['ufw_sha256']==REPAIRED_SHA else 0x060c3338)
    for r in report.get('candidate_task_prefixes',[]):check_task_rti_frontier(r,0x060e31c5 if r['ufw_sha256']==REPAIRED_SHA else 0x060c3338)
    report['dualcore_prefix_checks_passed']=True


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--rom',type=Path,required=True)
    p.add_argument('--firmware',type=Path,default=ROOT/'firmware/QCY_H3S_7018F_1T2_release_note_V1.1.8_011.ufw')
    p.add_argument('--repaired-candidate',type=Path);p.add_argument('--failed-r2',type=Path)
    p.add_argument('--report',type=Path,required=True);args=p.parse_args()
    rom=args.rom.read_bytes();check_rom(rom);module=upstream();app=application(args.firmware);header=package_header(args.firmware)
    report={'firmware_sha256':OFFICIAL_SHA,'application_sha256':APP_SHA,'rom_sha256':ROM_SHA,'simulator_commit':PIN,
        'device_accessed':False,'whole_h3s_boot_simulated':False,'candidate_whole_boot_validated':False,
        'suitable_for_flash_approval_alone':False,'cpu1_reset_and_bootstrap_mode_certified':False,
        'architectural_irq_or_task_rti_delivered':False,'scenarios':run(module,app,header,rom)}
    for name,path,digest in [('repaired_candidate',args.repaired_candidate,REPAIRED_SHA),('archived_failed_r2',args.failed_r2,FAILED_SHA)]:
        if path is None:continue
        candidate,readonly=candidate_view(path,digest,args.firmware)
        r=dual_case(module,candidate,header,rom,name,readonly=readonly);r['ufw_sha256']=digest
        report.setdefault('candidate_prefixes',[]).append(r)
        r=dual_case(module,candidate,header,rom,name+'_task_restore',readonly=readonly,abstract=True);r['ufw_sha256']=digest
        report.setdefault('candidate_task_prefixes',[]).append(r)
    check(report);args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(report,indent=2)+'\n')


if __name__=='__main__':main()
