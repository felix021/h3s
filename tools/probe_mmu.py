#!/usr/bin/env python3
"""Bounded real MMU initialization replay; P11 handshake remains a strict stop."""
import argparse
import hashlib
import json
import struct
from pathlib import Path

from br28_model import BR28Model, CLOCK0, PLL0, TIMER0
from probe_boot import APP_SHA, OFFICIAL_SHA, ROM_SHA, STACK_TOP, application, check_rom, explore
from probe_boot_boundaries import UPDATE, update_block
from probe_boot_handoff import HANDOFF, SENTINEL_A, package_header, profile
from probe_pi32v2 import PIN, ROOT, require, upstream


def run(module,app,header,rom,*,p11_status_cases=False):
    results=[]
    cases=[('mmu_unknown_control',False,None,True),
                               ('mmu_unmapped_table',False,0,False),
                               ('warm_mmu_initialized',False,0,True),
                               ('cold_mmu_initialized',True,0,True)]
    if p11_status_cases:cases=[(name,False,0,True) for name in ('p11_status_unknown','p11_status_zero','p11_status_one')]
    for name,cold,con,table in cases:
        h=profile(header,SENTINEL_A)
        h['description']['phase']='continuous application entry with functional MMU configuration'
        if p11_status_cases:h['description']['phase']='conditional startup with fixed external PMU status hypothesis; no P11 response model'
        h['regions'] += [(UPDATE,UPDATE+128,'retained_update_parameters'),(0x19fd68,0x19fda4,'rom_mask_context')]
        h['seeds'].append((UPDATE+8,update_block('empty')[0]))
        if table:h['regions'].append((0x120000,0x122800,'mmu_page_table'))
        h['snapshots'].append(('mmu_page_table',0x120000,0x2800))
        # Actual pmalloc descriptor and V_MEM object: values written by guest,
        # never used as initial seeds to make initialization appear successful.
        h['snapshots'] += [('physical_heap_descriptor',0x12953c,24),('virtual_heap_descriptor',0x126f14,24)]
        hardware=BR28Model(initial_registers={CLOCK0:0,CLOCK0+4:0,CLOCK0+8:0,PLL0:0 if cold else 1},
            initial_irq_config=[0]*32,initial_ipmask=0,reset_inputs_inactive=True,
            bus_lock_model=True,initial_lock_owner=None,gpio_model=True,mmu_model=True,initial_mmu_con=con,
            p11_access_probe=p11_status_cases,p11_status={'p11_status_zero':0,'p11_status_one':1}.get(name))
        diag={'zero_rep':'skip_hypothesis','adapt_icfg':True,'icfg_initial':0x300,'irq_repeat_return':True,
              'update_repeat':True,'rom_exact_stores':True,'rom_memset_rep':True,'rom_zero_rep_skip':True,
              'visible_cli':True,'opaque_pin_read':True,'exact_parallel_stores':True,
              'watch_pcs':[0x060350dc,0x06035116]}
        r=explore(module,app,name=name,adapt_stack=True,adapt_store=True,r0=HANDOFF,rom=rom,handoff=h,
                  diagnostics=diag,hardware=hardware,startup_loops=True,budget=85000 if p11_status_cases else 120000)
        results.append(r)
    h=profile(header,SENTINEL_A)
    regs=[0]*16;regs[0]=37;regs[1]=TIMER0
    hardware=BR28Model(source_ticks_per_step=0)
    if not p11_status_cases:
        results.append(explore(module,app,name='parallel_store_reads_old_r0',rom=rom,handoff=h,hardware=hardware,
            diagnostics={'entry_pc':0xa30c,'initial_gprs':regs,'initial_sp':STACK_TOP,
                         'icfg_initial':0,'exact_parallel_stores':True},budget=1))
    for r in results:
        print(r['name'],r['completed_steps'],r['stop_pc'],r['stop'],flush=True)
        r['last_instructions']=r['last_instructions'][-8:]
        for row in r['last_instructions']:row.pop('bytes',None)
        if len(r['memory_events'])>8:r['memory_events']=r['memory_events'][:4]+r['memory_events'][-4:]
        r['memory_events_truncated']=sum(r['memory_event_counts'].values())>len(r['memory_events'])
    return results


def check(report):
    rows={r['name']:r for r in report['scenarios']}
    require(rows['mmu_unknown_control']['stop']['kind']=='uninitialized_mmu_register','unknown MMU control fabricated')
    require(rows['mmu_unmapped_table']['stop']['address']=='0x120000','unmapped page table was writable')
    for name,steps in [('warm_mmu_initialized',83050),('cold_mmu_initialized',84088)]:
        r=rows[name]
        require(r['completed_steps']==steps and r['stop_pc']=='0x60688f8' and
                r['stop']['address']=='0xf2af10','P11 boundary changed')
        require(r['peripherals']['mmu']['con']==1 and r['peripherals']['mmu']['table_begin']==0x120000 and
                r['peripherals']['mmu']['table_end_inclusive']==0x1227ff,'MMU configuration differs')
        snap=r['memory_snapshots']['mmu_page_table']
        require(snap['initialized_bytes']==10240 and snap['sha256']==hashlib.sha256(bytes(10240)).hexdigest(),
                'real ROM did not initialize entire page table')
        require(r['peripherals']['mmu']['walks']==0,'unexpected virtual access during initialization')
        descriptor=bytes.fromhex(r['memory_snapshots']['physical_heap_descriptor']['bytes_with_unknowns'])
        start,end,size,first,last=struct.unpack_from('<IIIHH',descriptor)
        require((start,end,size)==(0x134500,0x19fb80,0x6b680) and
                first==((start-0x100000)>>7) and last==((end-0x100000)>>7),
                'actual QCY allocator no longer corroborates physical tag origin')
    r=rows['parallel_store_reads_old_r0']
    require(r['completed_steps']==1 and r['last_registers']['r'][0]=='0x1011' and
            r['peripherals']['timer0']['prd']==37,'parallel store read the new R0')
    report['mmu_initialization_regression_passed']=True


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--rom',type=Path,required=True)
    p.add_argument('--firmware',type=Path,default=ROOT/'firmware/QCY_H3S_7018F_1T2_release_note_V1.1.8_011.ufw')
    p.add_argument('--report',type=Path,required=True);args=p.parse_args()
    rom=args.rom.read_bytes();check_rom(rom)
    report={'format_version':1,'simulator_commit':PIN,'firmware_sha256':OFFICIAL_SHA,'application_sha256':APP_SHA,
            'rom_sha256':ROM_SHA,'device_accessed':False,'whole_h3s_boot_simulated':False,
            'real_guest_virtual_memory_allocation_validated':False,'suitable_for_flash_approval_alone':False,
            'scenarios':run(upstream(),application(args.firmware),package_header(args.firmware),rom)}
    check(report);args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(report,indent=2)+'\n')


if __name__=='__main__':main()
