#!/usr/bin/env python3
"""Replay startup with partial ICFG status; task RTI remains strict.

Exact optional failed/repaired image identities. No hardware or flash approval.
"""
import argparse
import json
from pathlib import Path

from probe_dualcore import dual_case
from probe_arch_startup import candidate_view, REPAIRED_SHA, FAILED_SHA
from probe_boot import application, check_rom, ROM_SHA, OFFICIAL_SHA, APP_SHA
from probe_boot_handoff import package_header
from probe_pi32v2 import ROOT, PIN, upstream, require


def partial_case(module,app,header,rom,name,*,empty_priorities=False,readonly=()):
    return dual_case(module,app,header,rom,name,abstract=True,readonly=readonly,
        machine_options={'partial_icfg':True,
                         'initial_icfg_masks':(0x3ff,0x3ff) if empty_priorities else (0x300,0x300),
                         'bootstrap_status_preserve_mask':0xff if empty_priorities else 0})


def check_frontier(r,upper=0x60c3338):
    require(r['completed_steps']==147268 and r['stop_core']==1 and r['stop_pc']=='0x6066130' and
            r['stop']=={'kind':'unsupported_runtime_instruction','instruction_id':'rti'},
            'partial status boot bypassed task RTI or stopped early')
    m=r['machine'];cores=m['cores'];p=r['peripherals']
    require((cores['0']['steps'],cores['1']['steps'],m['stalls'])==(136405,10863,2130),
            'per-core progress or lock behavior changed')
    for core in cores.values():
        s=core['partial_icfg']
        require(core['icfg']=='unknown' and s['known_mask']=='0x3ff' and s['known_value']=='0x200' and
                s['gate_demand']==0,'unknown hardware status fabricated or known gates not tracked')
    c=cores['1']
    require(c['sp']=='0x401bfc' and c['usp']==c['ssp']=='unknown' and c['r']==['0x0']+['unknown']*15,
            'task registers/banks no longer preserve unknowns')
    require(c['partial_icfg']['status_generation']==1 and not m['ready_injected'],
            'bootstrap status knowledge lost or ready injected')
    require(p['multicore']['core_icfg']=={'0':0,'1':0} and p['shared_lock_owner']==0 and
            p['software_irq_commands']['pending']['0']=={'known_mask':'0x8','known_value':'0x8'},
            'gate demand, real lock transfer or pending provenance changed')
    require(r['memory_snapshots']['cpu1_ready']['bytes_with_unknowns']=='01' and
            r['memory_snapshots']['first_core1_unknown_gprs']['initialized_bytes']==0,
            'actual ready absent or task frame fabricated')
    debug=p['debug_execution_projection']
    require(debug['violation'] is None and debug['checked_executing_fetches']==2*(147268+2130+1) and
            debug['registers']['0xffe218']==debug['registers']['0xffea18']==hex(upper),
            'per-core executing PC guard bypassed')
    require(r['instruction_adapters']['partial_icfg_read_or_gate_write']>0 and
            r['instruction_adapters']['partial_icfg_bit9_only']>0 and
            not r['instruction_adapters'].get('bootstrap_icfg_actual_gate_mask'),
            'old fullword bootstrap adapter used instead of partial expression')


def check(report):
    unknown,empty=report['scenarios']
    require(unknown['completed_steps']==16093 and unknown['stop_core']==0 and unknown['stop_pc']=='0x6000ed2' and
            unknown['stop']['kind']=='partial_icfg_value_consumed' and
            unknown['stop']['known_mask']=='0xffffff00' and unknown['machine']['launches']==0,
            'unknown active-priority byte was assumed zero')
    require(unknown['machine']['cores']['0']['partial_icfg']['known_mask']=='0x300',
            'unknown status bits became initialized before their consumer')
    check_frontier(empty)
    for r in report.get('candidate_prefixes',[]):
        check_frontier(r,0x60e31c5 if r['ufw_sha256']==REPAIRED_SHA else 0x60c3338)
    report['partial_icfg_checks_passed']=True


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--rom',type=Path,required=True)
    p.add_argument('--firmware',type=Path,default=ROOT/'firmware/QCY_H3S_7018F_1T2_release_note_V1.1.8_011.ufw')
    p.add_argument('--repaired-candidate',type=Path);p.add_argument('--failed-r2',type=Path)
    p.add_argument('--report',type=Path,required=True);args=p.parse_args()
    rom=args.rom.read_bytes();check_rom(rom);module=upstream();app=application(args.firmware);header=package_header(args.firmware)
    report={'firmware_sha256':OFFICIAL_SHA,'application_sha256':APP_SHA,'rom_sha256':ROM_SHA,'simulator_commit':PIN,
        'device_accessed':False,'whole_h3s_boot_simulated':False,'candidate_whole_boot_validated':False,
        'suitable_for_flash_approval_alone':False,'task_rti_delivered':False,
        'literal_gate_projection_certified_on_br28':False,'empty_active_priorities_are_explicit_fixture':True,
        'scenarios':[partial_case(module,app,header,rom,'hardware_status_unknown'),
                     partial_case(module,app,header,rom,'explicit_empty_active_priorities',empty_priorities=True)]}
    for name,path,digest in [('repaired_candidate',args.repaired_candidate,REPAIRED_SHA),('archived_failed_r2',args.failed_r2,FAILED_SHA)]:
        if path is None:continue
        candidate,readonly=candidate_view(path,digest,args.firmware)
        r=partial_case(module,candidate,header,rom,name,empty_priorities=True,readonly=readonly);r['ufw_sha256']=digest
        report.setdefault('candidate_prefixes',[]).append(r)
    check(report);args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(report,indent=2)+'\n')


if __name__=='__main__':main()
