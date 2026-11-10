#!/usr/bin/env python3
"""Bounded BR28 peripheral exploration with exact private ROM, offline only."""
import argparse
import hashlib
import json
from pathlib import Path
import struct

from br28_model import BR28Model, CLOCK0, IRQ_CONFIG, PINR1, PLL0
from probe_boot import APP_SHA, BOOT_ARGS, OFFICIAL_SHA, ROM_SHA, SDK_PIN, application, check_rom, explore
from probe_boot_boundaries import UPDATE, update_block
from probe_boot_handoff import HANDOFF, SENTINEL_A, package_header, profile
from probe_pi32v2 import PIN, ROOT, require, upstream
from probe_rom import CALLBACK, CONTEXT, function_harness


def run(module,app,header,rom):
    results=[]
    for name,cold,isolated,ticks,known_clocks,reset_inputs,known_irq in (
        ('pll_unknown_clock_initials',False,True,4,False,False,True),
        ('pll_ticks1',False,True,1,True,False,True),
        ('pll_ticks4',False,True,4,True,False,True),
        ('pll_ticks17',False,True,17,True,False,True),
        ('pll_stopped_clock',False,True,0,True,False,True),
        ('warm_unknown_reset_inputs',False,False,4,True,False,True),
        ('warm_known_inputs',False,False,4,True,True,True),
        ('warm_unknown_irq_initials',False,False,4,True,True,False),
        ('cold_clock_prefix',True,False,4,True,True,True),
        ('cold_contiguous',True,False,4,True,True,True),
        ('warm_bus_lock_modeled',False,False,4,True,True,True),
        ('warm_bus_lock_unknown_irq',False,False,4,True,True,False),
        ('cold_bus_lock_modeled',True,False,4,True,True,True)):
        h=profile(header,SENTINEL_A)
        h['description']={k:v for k,v in h['description'].items()
                          if k not in ('maskrom_executed','pll0_bit0_set_is_hypothesis')}
        h['description']['execution_scope']='isolated ROM PLL call' if isolated else 'contiguous application entry'
        h['regions'] += [(UPDATE,UPDATE+128,'retained_update_parameters'),
                         (CONTEXT,CONTEXT+60,'rom_mask_context')]
        h['seeds'].append((UPDATE+8,update_block('empty')[0]))
        h['snapshots'] += [('rom_mask_context',CONTEXT,60),('rom_clock_pair',BOOT_ARGS+4,8)]
        registers={PLL0:0 if cold else 1}
        if known_clocks:registers.update({CLOCK0:0,CLOCK0+4:0,CLOCK0+8:0})
        hw=BR28Model(source_ticks_per_step=ticks,initial_registers=registers,
                     initial_irq_config=[0]*32 if known_irq else None,initial_ipmask=0,
                     reset_inputs_inactive=reset_inputs,bus_lock_model='bus_lock' in name,initial_lock_owner=None)
        if isolated:
            # Make the output slot deliberately wrong so the real ROM store
            # must change it, rather than merely preserve our expected value.
            h['seeds'].append((BOOT_ARGS+4,struct.pack('<II',24000000,123)))
            h['description']['pll_boot_arg_seed']={'osc_hz':24000000,'initial_output_slot':123}
            diag={**function_harness(0x1fe14,[]),'icfg_initial':0}
        else:
            diag={'zero_rep':'skip_hypothesis','adapt_icfg':True,'icfg_initial':0x300,
                  'irq_repeat_return':True,'update_repeat':True,'rom_exact_stores':True,
                  'rom_memset_rep':True,'rom_zero_rep_skip':True,'visible_cli':True}
            if name=='cold_clock_prefix':diag['stop_before_pc']=0x06000116
        r=explore(module,app,name=name,adapt_stack=not isolated,adapt_store=not isolated,
                  r0=HANDOFF if not isolated else 0,rom=rom,handoff=h,diagnostics=diag,hardware=hw,
                  startup_loops=not isolated,budget=400 if ticks==0 else 100000)
        r['phase']=h['description']['execution_scope']
        r['last_instructions']=r['last_instructions'][-8:]
        for row in r['last_instructions']:row.pop('bytes',None)
        if len(r['memory_events'])>8:r['memory_events']=r['memory_events'][:4]+r['memory_events'][-4:]
        r['memory_events_truncated']=sum(r['memory_event_counts'].values())>len(r['memory_events'])
        results.append(r)
        print(name,r['completed_steps'],'steps;',r['stop_pc'],r['stop'],flush=True)
    return results


def check(report):
    rows={r['name']:r for r in report['scenarios']}
    for name in ('pll_ticks1','pll_ticks4','pll_ticks17'):
        r=rows[name];regs=r['peripherals']['registers']
        require(r['stop']['kind']=='isolated_function_return','PLL must return with ticking Timer0')
        require(regs[hex(PLL0)]=='0x40892d93' and regs[hex(PLL0+4)]=='0x80a' and
                regs[hex(PLL0+8)]=='0x5e' and regs[hex(PLL0+12)]=='0x31f','PLL programming result mismatch')
        require(regs[hex(CLOCK0)]=='0x30' and regs[hex(CLOCK0+4)]=='0x643' and regs[hex(CLOCK0+8)]=='0x0',
                'clock selection result mismatch')
        require(r['peripherals']['timer0']['expirations']==2 and not r['peripherals']['timer0']['pending'],
                'two genuine timer waits must expire and be acknowledged')
        require(r['memory_snapshots']['rom_clock_pair']['bytes_with_unknowns']==struct.pack('<II',24000000,48000000).hex(),
                'ROM clock globals differ')
    r=rows['pll_stopped_clock']
    require(r['stop']['kind']=='instruction_budget' and r['peripherals']['timer0']['expirations']==0,
            'no timer clock must stall, not produce fake pending')
    r=rows['pll_unknown_clock_initials']
    require(r['stop']['kind']=='uninitialized_peripheral' and r['stop']['address']==hex(CLOCK0),
            'unknown clock initial state must stop')
    r=rows['warm_unknown_reset_inputs']
    require(r['stop']['kind']=='unmodeled_pin_reset_input' and r['stop']['address']==hex(PINR1),
            'reset inputs cannot be silently assumed inactive')
    r=rows['cold_clock_prefix']
    require(r['stop']['kind']=='diagnostic_milestone_reached' and r['stop_pc']=='0x6000116',
            'actual application clock initializer must return')
    expected=bytearray(60);struct.pack_into('<II',expected,20,CALLBACK,0)
    for name in ('warm_known_inputs','warm_unknown_irq_initials','cold_contiguous'):
        r=rows[name]
        require(r['memory_snapshots']['rom_mask_context'].get('sha256')==hashlib.sha256(expected).hexdigest(),
                'continuous ROM mask_init did not produce expected state')
        writes=[e['value'] for e in r['peripherals']['writes'] if e['address']==hex(PINR1)]
        require(writes==['0x0','0x17'],'P33 initialization sequence differs')
        require(r['peripherals']['ipmask']==6,'actual IRQ priority threshold write did not execute')
    for name,steps in (('warm_bus_lock_modeled',17789),('cold_bus_lock_modeled',18827)):
        r=rows[name]
        require(r['completed_steps']==steps and r['stop_pc']=='0x6084f72' and r['stop']['address']=='0xfd5108',
                'continuous path must stop at unknown PORTB.DIR')
        p=r['peripherals']
        require(p['counts']['lock_acquire']==p['counts']['lock_release']==6 and p['shared_lock_owner'] is None,
                'six IRQ critical sections must release the shared lock')
        clears=[w for w in p['writes'] if IRQ_CONFIG<=int(w['address'],16)<IRQ_CONFIG+128]
        require([w['address'] for w in clears]==[hex(IRQ_CONFIG)]*4+[hex(IRQ_CONFIG+4)]*2 and
                all(w['value']=='0x0' for w in clears),'six timer IRQ disables must emit writes, even if unchanged')
    r=rows['warm_bus_lock_unknown_irq']
    require(r['stop']['kind']=='uninitialized_peripheral' and r['stop']['address']==hex(IRQ_CONFIG),
            'unknown IRQ initials must stop before RMW')
    report['hardware_contract_regression_passed']=True
    report['timer_tick_sensitivity_preserves_pll_programming']=True


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--rom',type=Path,required=True)
    p.add_argument('--firmware',type=Path,default=ROOT/'firmware/QCY_H3S_7018F_1T2_release_note_V1.1.8_011.ufw')
    p.add_argument('--report',type=Path,required=True)
    args=p.parse_args();rom=args.rom.read_bytes();check_rom(rom)
    app=application(args.firmware);header=package_header(args.firmware);module=upstream()
    report={'format_version':1,'simulator_commit':PIN,'reference_sdk_commit':SDK_PIN,
            'rom_sha256':ROM_SHA,'firmware_sha256':OFFICIAL_SHA,'application_sha256':APP_SHA,
            'device_accessed':False,'firmware_created':False,'whole_h3s_boot_simulated':False,
            'suitable_for_flash_approval_alone':False,'cpu_interrupt_delivery_simulated':False,
            'analog_pll_lock_simulated':False,'scenarios':run(module,app,header,rom)}
    check(report)
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(report,indent=2)+'\n')


if __name__=='__main__':main()
