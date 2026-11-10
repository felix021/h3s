#!/usr/bin/env python3
"""Isolated real debug_init and hook execution using its programmed PC ranges.

Exact RAM code is preloaded. No private ROM, API stubs or whole-boot claim.
"""
import argparse
import json
from pathlib import Path

from br28_model import BR28Model
from probe_arch_startup import candidate_view, REPAIRED_SHA, FAILED_SHA
from probe_boot import application, explore, ENTRY, OFFICIAL_SHA
from probe_boot_handoff import profile, package_header, RAM_CODE
from probe_pi32v2 import ROOT, PIN, require, upstream


def replay(module,app,header,name,readonly=(),upper=0x060c3338,hook=False):
    h=profile(header);dst,src,length=RAM_CODE
    h['seeds'].append((dst,app[src-ENTRY:src-ENTRY+length]));h['readonly']+=list(readonly)
    h['snapshots']=[('debug_magic',0x124840,64)]
    h['description']={'phase':'isolated debug_init with preloaded exact RAM code; not contiguous boot'}
    hw=BR28Model(debug_execution_projection=True,initial_debug_controls=(0,None))
    result=explore(module,app,name=name+'_debug_prefix',handoff=h,hardware=hw,
        diagnostics={'entry_pc':0x06034e14,'initial_sp':0x123800,'initial_rets':0x01c70000,
            'icfg_initial':0,'exact_parallel_stores':True,'immediate_rep_projection':True,
            'stop_before_pc':0x06034f08},budget=1000)
    require(result['completed_steps']==126 and result['stop_pc']=='0x6034f08' and
            result['stop']['kind']=='diagnostic_milestone_reached','actual debug prefix changed')
    cfg=hw.debug.registers
    require(cfg[0xffe200]==0xe000001f and cfg[0xffe218]==upper and cfg[0xffe220]==0x1344c6 and
            cfg[0xffe228]==0x20000,'actual debug bounds/enable differ')
    results=[result]
    if hook:
        h['seeds'].append((0x1265ea,b'\x81'));regs=[0]*16;regs[6]=0x1265c0
        r=explore(module,app,name=name+'_hook_with_actual_debug_state',handoff=h,hardware=hw,
            diagnostics={'entry_pc':0x060059ce,'initial_sp':0x123800,'initial_gprs':regs},budget=2)
        if upper==0x060c3338:
            require(r['completed_steps']==1 and r['stop_pc']=='0x60e3092' and
                    r['stop']['kind']=='projected_pc_range_violation','failed r2 escaped its actual programmed range')
        else:
            require(r['completed_steps']==2 and r['stop_pc']=='0x60e3096' and r['stop']['kind']=='instruction_budget',
                    'repaired hook did not execute its actual first payload instruction')
        results.append(r)
    for row in results:
        row['last_instructions']=row['last_instructions'][-8:]
        if len(row['memory_events'])>8:row['memory_events']=row['memory_events'][:4]+row['memory_events'][-4:]
        row['memory_events_truncated']=sum(row['memory_event_counts'].values())>len(row['memory_events'])
        print(row['name'],row['completed_steps'],row['stop_pc'],row['stop'],flush=True)
    return results


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--firmware',type=Path,default=ROOT/'firmware/QCY_H3S_7018F_1T2_release_note_V1.1.8_011.ufw')
    p.add_argument('--repaired-candidate',type=Path);p.add_argument('--failed-r2',type=Path)
    p.add_argument('--report',type=Path,required=True);args=p.parse_args()
    module=upstream();header=package_header(args.firmware)
    results=replay(module,application(args.firmware),header,'official')
    for name,path,digest,upper in [('repaired',args.repaired_candidate,REPAIRED_SHA,0x060e31c5),
                                  ('archived_failed_r2',args.failed_r2,FAILED_SHA,0x060c3338)]:
        if path is None:continue
        app,readonly=candidate_view(path,digest,args.firmware)
        rows=replay(module,app,header,name,readonly,upper,hook=True)
        for row in rows:row['ufw_sha256']=digest
        results+=rows
    report={'firmware_sha256':OFFICIAL_SHA,'simulator_commit':PIN,'device_accessed':False,
        'scope':'isolated real debug_init plus separately entered hook; not contiguous startup',
        'whole_h3s_boot_simulated':False,'candidate_whole_boot_validated':False,
        'suitable_for_flash_approval_alone':False,'silicon_limit_endpoint_alignment_certified':False,
        'debug_configuration_regression_passed':True,'scenarios':results}
    args.report.parent.mkdir(parents=True,exist_ok=True);args.report.write_text(json.dumps(report,indent=2)+'\n')


if __name__=='__main__':main()
