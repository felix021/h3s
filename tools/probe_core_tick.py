#!/usr/bin/env python3
"""Opt-in per-core system tick IRQ3 delivery on the real startup prefix.

The actual per-core timer init (0x60661e8) enables CPU0's tick with PRD=1199999
(~1.2M tick period). With the virtual tick rate of 4 per model step the first
CPU0 overflow lands around 450k steps. That is unreachable today: the CPU1 peer
IPI (IRQ125, priority 6) is raised as an unmaskable IRQ at 153430 steps, before
the tick overflows, and its delivery is the separate preceding task. These three
scenarios therefore all stop at that same boundary; the delivery machinery is
exercised by tools/test_core_tick.py. No flash approval.
"""
import argparse
import json
from pathlib import Path

from probe_dualcore import dual_case
from probe_task_returns import EMPTY_PRIORITIES
from probe_arch_startup import candidate_view, REPAIRED_SHA, FAILED_SHA
from probe_boot import application, check_rom, ROM_SHA, OFFICIAL_SHA, APP_SHA
from probe_boot_handoff import package_header
from probe_irq_entry import HANDLER_PSR_FRAMES
from probe_pi32v2 import ROOT, PIN, upstream, require

CORE_TICK_FRAMES={0x60661a4:'frame12_push',0x60661e2:'frame12_pop'}


def core_tick_case(module,app,header,rom,name,*,entry=False,banks=False,rti=False,readonly=()):
    # Base is the irq127_appcore_roundtrip projection (software IRQ entry +
    # banks + IDLE wait + RTI); the core tick flags are layered on top.
    machine_options={**EMPTY_PRIORITIES,'first_task_return_hypothesis':'open_gate8',
                     'defer_core1_first_idle':True,'software_irq_entry':True,
                     'software_irq_entry_banks':True,'idle_wait_projection':True,'software_irq_rti':True}
    if entry:machine_options['core_tick_entry']=True
    if banks:machine_options['core_tick_entry_banks']=True
    if rti:machine_options['core_tick_rti']=True
    options={'initial_software_irq_pending':(0,0),'software_irq_delivery_requests':True,
             'core_tick_delivery_requests':entry}
    extra_diagnostics={'software_irq_frames':dict(HANDLER_PSR_FRAMES)}
    if entry:extra_diagnostics['core_tick_frames']=dict(CORE_TICK_FRAMES)
    return dual_case(module,app,header,rom,name,abstract=True,readonly=readonly,
                     machine_options=machine_options,options=options,budget=500000,
                     extra_diagnostics=extra_diagnostics)


def check(report):
    rows={r['name']:r for r in report['scenarios']}
    for r in report['scenarios']:
        require(r['completed_steps']==153430 and r['stop_core']==0 and r['stop_pc']=='0x12b66a' and
                r['stop']['kind']=='unmodeled_unmaskable_irq' and r['stop']['core']==1 and
                r['stop']['irq']==125 and r['stop']['source']=='software_irq',
                'core tick scenario left the CPU1 unmaskable-IPI boundary: '+r['name'])
    require('core_tick_entry_projection' not in rows['core_tick_strict']['machine'],
            'strict core tick scenario enabled the entry projection')
    for name in ('core_tick_entry_minimal','core_tick_rti_roundtrip'):
        projection=rows[name]['machine'].get('core_tick_entry_projection')
        require(projection and projection['enabled'] and projection['deliveries']==[],
                'core tick delivery fired before the first overflow: '+name)
    report['core_tick_first_overflow_not_reached']=True
    report['core_tick_delivery_blocked_by']=('unmodeled_unmaskable_irq core=1 irq=125 '
            '(CPU1 peer IPI precedes the ~450k-step CPU0 tick overflow)')
    report['core_tick_closure_demonstrated_by_unit_tests_only']=True


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
        'core_tick_vector_slot_formula_evidence':'0x19fe00+3*4 (IRQ3) written by the per-core timer init 0x60661e8 via 0x6065d86',
        'scenarios':[core_tick_case(module,app,header,rom,'core_tick_strict'),
                     core_tick_case(module,app,header,rom,'core_tick_entry_minimal',entry=True),
                     core_tick_case(module,app,header,rom,'core_tick_rti_roundtrip',entry=True,banks=True,rti=True)]}
    for name,path,digest in [('repaired_candidate',args.repaired_candidate,REPAIRED_SHA),('archived_failed_r2',args.failed_r2,FAILED_SHA)]:
        if path is None:continue
        candidate,readonly=candidate_view(path,digest,args.firmware)
        r=core_tick_case(module,candidate,header,rom,name,entry=True,readonly=readonly);r['ufw_sha256']=digest
        report.setdefault('candidate_prefixes',[]).append(r)
    check(report);args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(report,indent=2)+'\n')


if __name__=='__main__':main()
