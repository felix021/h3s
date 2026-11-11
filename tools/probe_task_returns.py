#!/usr/bin/env python3
"""Opt-in first-task return comparisons on the real startup prefix.

Counterfactual returns are explicit hypotheses, not measured BR28 RTI
semantics; hardware_mode_certified stays false. The strict default still
stops before task RTI. No IRQ entry, ready injection or flash approval.
"""
import argparse
import json
from pathlib import Path

from probe_dualcore import dual_case
from probe_arch_startup import candidate_view, REPAIRED_SHA, FAILED_SHA
from probe_boot import application, check_rom, ROM_SHA, OFFICIAL_SHA, APP_SHA
from probe_boot_handoff import package_header
from probe_pi32v2 import ROOT, PIN, upstream, require

EMPTY_PRIORITIES={'partial_icfg':True,'initial_icfg_masks':(0x3ff,0x3ff),
                  'bootstrap_status_preserve_mask':0xff}
# (name, hypothesis, defer_cpu1_first_idle, explicit_no_initial_software_pending)
CASES=(
    ('return_stack_unknown_pending_clear','unresolved_stack',False,True),
    ('gate8_closed_round_robin','closed_gate8',False,False),
    ('gate8_unknown_deferred','unknown_gate8',True,True),
    ('gate8_open_deferred','open_gate8',True,True),
    ('gate8_closed_deferred','closed_gate8',True,True),
)


def return_case(module,app,header,rom,name,*,hypothesis,defer=False,pending_clear=False,readonly=()):
    machine_options={**EMPTY_PRIORITIES,'first_task_return_hypothesis':hypothesis}
    if defer:machine_options['defer_core1_first_idle']=True
    options={'initial_software_irq_pending':(0,0)} if pending_clear else None
    return dual_case(module,app,header,rom,name,abstract=True,readonly=readonly,
                     machine_options=machine_options,options=options,budget=250000)


def check(report):
    rows={r['name']:r for r in report['scenarios']}
    hypothesis_by_name=dict((c[0],c[1]) for c in CASES)
    for r in report['scenarios']:
        comparison=r['machine'].get('first_task_return_comparison') or {}
        returns=comparison.get('returns') or []
        require(returns and all(e['hypothesis']==hypothesis_by_name[r['name']] and
                not e['hardware_mode_certified'] for e in returns),'counterfactual return missing or certified')
        require(comparison['silicon_task_return_certified'] is False,'return comparison certified silicon')
    r=rows['return_stack_unknown_pending_clear']
    require(r['completed_steps']==147270 and r['stop_core']==1 and r['stop_pc']=='0x12c57e' and
            r['stop']=={'kind':'unknown_core_stack_pointer','core':1} and
            r['machine']['cores']['1']['sp']=='unknown',
            'unresolved stack must stay unknown at the first task-stack instruction')
    r=rows['gate8_closed_round_robin']
    require(r['stop_core']==1 and r['stop_pc']=='0x12c5ca' and r['stop']['kind']=='unsupported_runtime_instruction' and
            r['stop']['instruction_id']=='idle','closed gate8 round-robin must stop at the real CPU1 IDLE')
    r=rows['gate8_unknown_deferred']
    require(r['stop_core']==0 and r['stop_pc']=='0x12b608' and
            r['stop']['kind']=='software_irq_with_unknown_icfg' and r['stop']['irq']==127,
            'IRQ127 delivery must stay blocked on the unknown gate')
    r=rows['gate8_open_deferred']
    require(r['stop_core']==0 and r['stop_pc']=='0x12b608' and
            r['stop']['kind']=='software_irq_delivery_not_implemented' and r['stop']['irq']==127,
            'eligible IRQ127 must stop before the unimplemented entry')
    r=rows['gate8_closed_deferred']
    require(r['stop_pc']=='0x12c5ca' and r['stop']['kind']=='unsupported_runtime_instruction' and
            r['stop']['instruction_id']=='idle',
            'both cores must reach their IDLE boundaries without a deadlock proof')
    for r in report.get('candidate_prefixes',[]):
        require(r['stop']['kind'] in ('uninitialized_memory','unsupported_runtime_instruction',
                'software_irq_with_unknown_icfg','software_irq_delivery_not_implemented'),
                'candidate comparison left the compared stop set')
    report['task_return_comparisons_passed']=True
    report['all_returns_remain_counterfactual']=True


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--rom',type=Path,required=True)
    p.add_argument('--firmware',type=Path,default=ROOT/'firmware/QCY_H3S_7018F_1T2_release_note_V1.1.8_011.ufw')
    p.add_argument('--repaired-candidate',type=Path);p.add_argument('--failed-r2',type=Path)
    p.add_argument('--report',type=Path,required=True);args=p.parse_args()
    rom=args.rom.read_bytes();check_rom(rom);module=upstream();app=application(args.firmware);header=package_header(args.firmware)
    report={'firmware_sha256':OFFICIAL_SHA,'application_sha256':APP_SHA,'rom_sha256':ROM_SHA,'simulator_commit':PIN,
        'device_accessed':False,'whole_h3s_boot_simulated':False,'candidate_whole_boot_validated':False,
        'suitable_for_flash_approval_alone':False,'rti_semantics_certified_on_br28':False,
        'explicit_empty_active_priorities_fixture':True,
        'scenarios':[return_case(module,app,header,rom,name,hypothesis=hypothesis,defer=defer,pending_clear=clear)
                     for name,hypothesis,defer,clear in CASES]}
    for name,path,digest in [('repaired_candidate',args.repaired_candidate,REPAIRED_SHA),('archived_failed_r2',args.failed_r2,FAILED_SHA)]:
        if path is None:continue
        candidate,readonly=candidate_view(path,digest,args.firmware)
        r=return_case(module,candidate,header,rom,'gate8_open_deferred',hypothesis='open_gate8',defer=True,
                      pending_clear=True,readonly=readonly);r['ufw_sha256']=digest
        report.setdefault('candidate_prefixes',[]).append(r)
    check(report);args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(report,indent=2)+'\n')


if __name__=='__main__':main()
