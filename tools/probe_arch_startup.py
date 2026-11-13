#!/usr/bin/env python3
"""Conditional architecture startup with real Flash gates and OS heap allocation.

Known image digests only. No device access, firmware creation or flash approval.
"""
import argparse
import hashlib
import json
from pathlib import Path

from br28_model import CACHE_STATUS_REGISTERS, VOLTAGE_CONFIG, PLL1_CON3
from probe_boot import ENTRY, OFFICIAL_SHA, APP_SHA, ROM_SHA, application, check_rom
from probe_boot_handoff import package_header
from probe_clock_startup import startup
from probe_pi32v2 import PIN, ROOT, require
from probe_pi32v2 import upstream
from verify_eq_candidate import load_image
from verify_ufw_integrity import verify

REPAIRED_SHA='67ba97efb83a15ec9df14de574667c6a77126d43ac6d8e5f5e645cb91126f222'
FAILED_SHA='c8dae5233678102aac152f0ff94952edea83c6eeca6afaf216db43226d0b6696'
REGIONS=((0x19fe00,0x1a0000,'irq_vector_table'),(0x134500,0x19fb80,'physical_heap'))
SNAPSHOTS=(('first_allocated_pte',0x120600,2),('tail_allocated_pte',0x1227fe,2),
           ('physical_heap_descriptor',0x12953c,24))


def architecture_case(module,app,header,rom,name,*,options=None,diagnostic_options=None,
                      regions=REGIONS,readonly=(),budget=250000,registers=None,snapshots=(),machine=None,
                      post_stop=None):
    parameters={'voltage_configuration_projection':True,'pll1_configuration_projection':True,
                'cache_status_probe':True,'cache_status_words':dict.fromkeys(CACHE_STATUS_REGISTERS,0x80000000),
                'sfc_read_bus':True,'initial_sfc_control':1,'core_tick_progress':(4,4),
                'core_tick_count_while_held':True}
    parameters.update(options or {})
    diagnostics={'opaque_pwm_read':True,'exact_subword_stores':True,'immediate_rep_projection':True,
                 'watch_pcs':[0x12af74,0x060352d8,0x060352de,0x060352e4,0x060352ea,0x06035334,
                              0x06035350,0x06035356,0x060660da], 'watch_registers':[0,1,4]}
    diagnostics.update(diagnostic_options or {})
    return startup(module,app,header,rom,name=name,options=parameters,
        registers={**dict.fromkeys(VOLTAGE_CONFIG,0),PLL1_CON3:0,**(registers or {})},budget=budget,
        diagnostic_options=diagnostics,extra_regions=regions,extra_readonly=readonly,extra_snapshots=SNAPSHOTS+tuple(snapshots),machine=machine,
        post_stop=post_stop)


def candidate_view(path,expected,official):
    raw=path.read_bytes();require(hashlib.sha256(raw).hexdigest()==expected,'requires exact named image digest')
    require(verify(path)['passed'],'candidate CRC failure')
    _,flash,plain=load_image(path)
    require(flash[:0x2000]==load_image(official)[1][:0x2000],'bootloader/header differs from synthetic handoff source')
    payload=plain[0xe3074:]
    require(len(payload)==337 and hashlib.sha256(payload).hexdigest()==
            'f4e9501e515a7f74e7114633ba9bc165d1da9b399481530a78be8663dbe0e49a','candidate payload differs')
    return plain[0x100:0x100+842654],((0x060e3074,payload),)


def run(module,app,header,rom):
    cases=[('sfc_initial_unknown',{'initial_sfc_control':None},{},250000),
           ('sfc_initial_disabled',{'initial_sfc_control':0},{},250000),
           ('cache_status_unknown',{'cache_status_words':{}},{},250000),
           ('cache_stays_busy',{'cache_status_words':{**dict.fromkeys(CACHE_STATUS_REGISTERS,0x80000000),0xff0500:0}},{},109000),
           ('pwm_value_strict',{}, {'opaque_pwm_read':False},250000),
           ('core_tick_clock_unknown',{'core_tick_progress':None},{},250000),
           ('core_tick_held_clock_unknown',{'core_tick_count_while_held':None},{},250000),
           ('architecture_virtual_inputs',{}, {},250000)]
    results=[architecture_case(module,app,header,rom,name,options=options,diagnostic_options=diagnostics,budget=budget)
             for name,options,diagnostics,budget in cases]
    results.append(architecture_case(module,app,header,rom,'heap_mapping_strict',regions=REGIONS[:1]))
    return results


def check_prefix(r):
    require(r['completed_steps']==110818 and r['stop_pc']=='0x6068926' and
            r['stop']['kind']=='unmapped_memory' and r['stop']['access']=='read' and
            r['stop']['address']=='0xff0100' and r['stop']['size']==4,
            'conditional architecture prefix changed or stopped early')
    pcs={m['pc'] for m in r['milestones'] if m['kind']=='watched_pc_executed'}
    require({hex(pc) for pc in (0x060352d8,0x060352de,0x060352e4,0x060352ea,0x12af74)}<=pcs,
            'actual OS/timer/allocator calls were not executed')
    require(not ({hex(pc) for pc in (0x06035334,0x06035350,0x06035356,0x060660da)}&pcs),
            'prefix unexpectedly reached debug_init, tasks or CPU1 release')
    allocations=[m['registers'] for m in r['milestones'] if m['pc']=='0x12af74']
    require([(m['r0'],m['r1']) for m in allocations[:2]]==[('0x13f6','0x33f6'),('0x13f5','0x33f5')],
            'allocator bitmap-to-page conversion differs')
    require(r['peripherals']['mmu']['walks']==67,'actual virtual access prefix differs')
    for label,expected in [('first_allocated_pte','f6b3'),('tail_allocated_pte','f5b3')]:
        require(r['memory_snapshots'][label]['bytes_with_unknowns']==expected,'actual guest PTE differs')
    bus=r['peripherals']['sfc_read_bus']
    require((bus['con'],bus['baud'],bus['writes'])==(0x300001,1,4),
            'real Flash reconfiguration differs')
    require(r['instruction_adapters']['proven_dead_pwm_configuration_read']==4,'unexpected opaque PWM read count')


def check(report):
    rows={r['name']:r for r in report['scenarios']}
    require(rows['sfc_initial_unknown']['completed_steps']==0 and rows['sfc_initial_unknown']['stop']['kind']=='unknown_sfc_enable_state',
            'SFC initial state was inferred from mapped Flash')
    require(rows['sfc_initial_disabled']['completed_steps']==0 and rows['sfc_initial_disabled']['stop']['kind']=='sfc_disabled_flash_access',
            'disabled SFC still fetched application code')
    require(rows['cache_status_unknown']['stop']['kind']=='unknown_cache_status_input','cache readiness invented')
    require(rows['cache_stays_busy']['stop']['kind']=='instruction_budget' and
            rows['cache_stays_busy']['completed_steps']==109000 and rows['cache_stays_busy']['stop_pc']=='0xbb2a',
            'cache poll manufactured readiness or looped elsewhere')
    require(rows['pwm_value_strict']['stop']['address']=='0xfd2964','strict PWM read silently bypassed')
    require(rows['core_tick_clock_unknown']['stop']['kind']=='core_tick_enable_not_modeled','core tick clock fabricated')
    require(rows['core_tick_held_clock_unknown']['stop']['kind']=='core1_tick_held_clock_unknown','held clock policy invented')
    require(rows['heap_mapping_strict']['stop']['address']=='0x19faf4','absolute MMU tag regression or implicit RAM mapping')
    check_prefix(rows['architecture_virtual_inputs'])
    for candidate in report.get('candidate_prefixes',[]):check_prefix(candidate)
    report['architecture_prefix_checks_passed']=True


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--rom',type=Path,required=True)
    p.add_argument('--firmware',type=Path,default=ROOT/'firmware/QCY_H3S_7018F_1T2_release_note_V1.1.8_011.ufw')
    p.add_argument('--repaired-candidate',type=Path)
    p.add_argument('--failed-r2',type=Path)
    p.add_argument('--report',type=Path,required=True);args=p.parse_args()
    rom=args.rom.read_bytes();check_rom(rom);module=upstream()
    app=application(args.firmware);header=package_header(args.firmware)
    report={'firmware_sha256':OFFICIAL_SHA,'application_sha256':APP_SHA,'rom_sha256':ROM_SHA,'simulator_commit':PIN,
            'device_accessed':False,'whole_h3s_boot_simulated':False,'runtime_flash_pc_guard_enforced':False,
            'candidate_whole_boot_validated':False,'suitable_for_flash_approval_alone':False,
            'scenarios':run(module,app,header,rom)}
    for name,path,digest in [('repaired_candidate',args.repaired_candidate,REPAIRED_SHA),('archived_failed_r2',args.failed_r2,FAILED_SHA)]:
        if path is None:continue
        candidate,readonly=candidate_view(path,digest,args.firmware)
        result=architecture_case(module,candidate,header,rom,name,readonly=readonly)
        result['ufw_sha256']=digest;result['application_sha256']=hashlib.sha256(candidate).hexdigest()
        report.setdefault('candidate_prefixes',[]).append(result)
    check(report)
    args.report.parent.mkdir(parents=True,exist_ok=True);args.report.write_text(json.dumps(report,indent=2)+'\n')


if __name__=='__main__':main()
