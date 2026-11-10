#!/usr/bin/env python3
"""Symbolic GPIO startup and actual QCY Timer0 ISR software-frame replay."""
import argparse
import json
from pathlib import Path
import struct

from br28_model import BR28Model, CLOCK0, PLL0, TIMER0
from probe_boot import APP_SHA, ENTRY, OFFICIAL_SHA, ROM_SHA, STACK_TOP, application, check_rom, explore
from probe_boot_boundaries import UPDATE, update_block
from probe_boot_handoff import BSS, DATA, HANDOFF, RAM_CODE, SENTINEL_A, package_header, profile
from probe_pi32v2 import PIN, ROOT, require, upstream
from probe_rom import GPRS

IRQ_ENTRY=0x12ece8
IRQ_POP=0x12ed08
IRQ_RTI=0x12ed0c
CALLBACKS=0x126f6c
NOOP=0x06000b10
IRQ_CONTEXT={'reti':0x060abcde,'rets':0x06001234,'psr':0xa500000a}


def run(module,app,header,rom):
    results=[]
    cases=[('gpio_strict',False,False,False,100000),('gpio_partial',False,True,False,100000),
           ('opaque_value_visible',False,True,True,18196),('opaque_after_return',False,True,True,18197),
           ('opaque_overwritten',False,True,True,18198),('warm_to_mmu',False,True,True,100000),
           ('cold_to_mmu',True,True,True,100000)]
    for name,cold,gpio,opaque,budget in cases:
        h=profile(header,SENTINEL_A)
        h['description']['phase']='continuous application entry with exact bitwise GPIO projection'
        h['description'].pop('maskrom_executed',None);h['description'].pop('pll0_bit0_set_is_hypothesis',None)
        h['regions'] += [(UPDATE,UPDATE+128,'retained_update_parameters'),(0x19fd68,0x19fda4,'rom_mask_context')]
        h['seeds'].append((UPDATE+8,update_block('empty')[0]))
        hardware=BR28Model(initial_registers={CLOCK0:0,CLOCK0+4:0,CLOCK0+8:0,PLL0:0 if cold else 1},
            initial_irq_config=[0]*32,initial_ipmask=0,reset_inputs_inactive=True,
            bus_lock_model=True,initial_lock_owner=None,gpio_model=gpio)
        diag={'zero_rep':'skip_hypothesis','adapt_icfg':True,'icfg_initial':0x300,'irq_repeat_return':True,
              'update_repeat':True,'rom_exact_stores':True,'rom_memset_rep':True,'rom_zero_rep_skip':True,
              'visible_cli':True,'opaque_pin_read':opaque}
        r=explore(module,app,name=name,adapt_stack=True,adapt_store=True,r0=HANDOFF,rom=rom,handoff=h,
                  diagnostics=diag,hardware=hardware,startup_loops=True,budget=budget)
        results.append(r);print(name,r['completed_steps'],r['stop_pc'],r['stop'],flush=True)
    for name,context,callback in (('isr_strict',False,0),('isr_null_callback',True,0),
                                  ('isr_actual_rts_callback',True,NOOP),('isr_invalid_callback',True,0x13ff00)):
        h=profile(header,SENTINEL_A)
        h['description']['phase']='isolated post-entry Timer0 ISR; not a continuation of startup'
        h['description'].pop('maskrom_executed',None)
        h['seeds'] += [(BSS[0],bytes(BSS[1]))]+[(dst,app[src-ENTRY:src-ENTRY+n]) for dst,src,n in (DATA,RAM_CODE)]
        h['seeds'].append((CALLBACKS,struct.pack('<I',callback)))
        hardware=BR28Model(source_ticks_per_step=0,initial_irq_config=[0]*32,initial_ipmask=6,
                           bus_lock_model=True,initial_lock_owner=None)
        for address,value in ((TIMER0,0x4000),(TIMER0+4,0),(TIMER0+8,2),(TIMER0,0x1011)):
            hardware.write(address,value)
        hardware.timer.advance(12)  # pending established before entering the software handler
        diag={'entry_pc':IRQ_ENTRY,'initial_sp':STACK_TOP,'initial_gprs':GPRS,
              'icfg_initial':0,'adapt_icfg':True,'irq_repeat_return':True,'visible_cli':True,'watch_pcs':[NOOP]}
        if context:diag.update(irq_context=IRQ_CONTEXT,irq_pop_pc=IRQ_POP)
        r=explore(module,app,name=name,rom=rom,handoff=h,diagnostics=diag,hardware=hardware)
        results.append(r);print(name,r['completed_steps'],r['stop_pc'],r['stop'],flush=True)
    h=profile(header,SENTINEL_A)
    h['description']['phase']='isolated corrupted saved IRQ frame; not architectural IRQ entry'
    dst,src,n=RAM_CODE;h['seeds'].append((dst,app[src-ENTRY:src-ENTRY+n]))
    h['seeds'].append((STACK_TOP-12,struct.pack('<III',0x12345679,0x12345679,IRQ_CONTEXT['psr'])))
    r=explore(module,app,name='isr_saved_return_masks',rom=rom,handoff=h,
              diagnostics={'entry_pc':IRQ_POP,'initial_sp':STACK_TOP-12,'initial_gprs':GPRS,
                           'irq_context':IRQ_CONTEXT,'irq_pop_pc':IRQ_POP,'icfg_initial':0})
    results.append(r);print(r['name'],r['completed_steps'],r['stop_pc'],r['stop'],flush=True)
    for result in results:
        result['last_instructions']=result['last_instructions'][-8:]
        for row in result['last_instructions']:row.pop('bytes',None)
        if len(result['memory_events'])>8:result['memory_events']=result['memory_events'][:4]+result['memory_events'][-4:]
        result['memory_events_truncated']=sum(result['memory_event_counts'].values())>len(result['memory_events'])
    return results


def check(report):
    rows={r['name']:r for r in report['scenarios']}
    require(rows['gpio_strict']['stop']['address']=='0xfd5108','strict GPIO boundary changed')
    require(rows['gpio_partial']['stop']['address']=='0xf2c300','partial GPIO must not invent PORT_SEL0')
    for name,pc in (('opaque_value_visible','0x6067462'),('opaque_after_return','0x603502c')):
        r=rows[name];require(r['stop_pc']==pc and r['last_registers']['r'][0]=='unknown','dead read was concretized')
    require(rows['opaque_overwritten']['last_registers']['r'][0]=='0x350','caller did not kill opaque R0')
    for name in ('warm_to_mmu','cold_to_mmu'):
        r=rows[name]
        require(r['stop']['address']=='0xff0404' and r['stop_pc']=='0x60350e8','new MMU boundary changed')
        for address in ('0xfd5108','0xfd510c','0xfd5110','0xfd5114','0xfd5120'):
            require(r['peripherals']['gpio'][address]=={'known_value':'0x2','known_mask':'0x2'},
                    'PB1 configuration differs or unknown neighbours were fabricated')
        require(r['instruction_adapters']['proven_dead_p33_read']==1,'wrong opaque read count')
    for name,steps in (('isr_null_callback',62),('isr_actual_rts_callback',64)):
        r=rows[name]
        require(r['completed_steps']==steps and r['stop_pc']==hex(IRQ_RTI) and
                r['stop']['instruction_id']=='rti','ISR must stop before architectural RTI')
        require(r['last_registers']['r']==[hex(x) for x in GPRS] and r['last_registers']['sp']==hex(STACK_TOP),
                'ISR did not preserve all GPRs and stack')
        require(r['last_registers']['reti']==hex(IRQ_CONTEXT['reti']) and
                r['last_registers']['rets']==hex(IRQ_CONTEXT['rets']) and
                r['last_registers']['psr_projection']==hex(IRQ_CONTEXT['psr']),'ISR special frame mismatch')
        require(not r['peripherals']['timer0']['pending'],'actual ISR did not acknowledge Timer0')
        hits=[m for m in r['milestones'] if m['kind']=='watched_pc_executed']
        require(len(hits)==(1 if name=='isr_actual_rts_callback' else 0),'callback path mismatch')
    require(rows['isr_invalid_callback']['stop_pc']=='0x13ff00','invalid ISR callback escaped the fetch fence')
    r=rows['isr_saved_return_masks']
    require(r['stop_pc']==hex(IRQ_RTI) and r['completed_steps']==2 and
            r['last_registers']['reti']=='0x2345678' and r['last_registers']['rets']=='0x2345678' and
            r['last_registers']['sp']==hex(STACK_TOP),'saved RETI/RETS were not masked to bits26..1')
    report['gpio_irq_regression_passed']=True


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--rom',type=Path,required=True)
    p.add_argument('--firmware',type=Path,default=ROOT/'firmware/QCY_H3S_7018F_1T2_release_note_V1.1.8_011.ufw')
    p.add_argument('--report',type=Path,required=True);args=p.parse_args()
    rom=args.rom.read_bytes();check_rom(rom);app=application(args.firmware)
    report={'format_version':1,'simulator_commit':PIN,'firmware_sha256':OFFICIAL_SHA,'application_sha256':APP_SHA,
            'rom_sha256':ROM_SHA,'device_accessed':False,'firmware_created':False,'whole_h3s_boot_simulated':False,
            'architectural_irq_entry_or_rti_simulated':False,'suitable_for_flash_approval_alone':False,
            'scenarios':run(upstream(),app,package_header(args.firmware),rom)}
    check(report);args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(report,indent=2)+'\n')


if __name__=='__main__':main()
