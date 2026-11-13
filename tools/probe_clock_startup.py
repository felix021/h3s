#!/usr/bin/env python3
"""Conditional real EFUSE/calibration/clock startup, with explicit virtual inputs.

This is not a claim about the device's OTP, bootloader state or analog clocks.
"""
import argparse
import json
from pathlib import Path
import struct

from br28_model import BR28Model, CLOCK0, PLL0, SYS_DIV, VOLTAGE_CONFIG, PLL1_CON3
from probe_boot import APP_SHA, OFFICIAL_SHA, ROM_SHA, application, check_rom, explore
from probe_boot_boundaries import UPDATE, update_block
from probe_boot_handoff import HANDOFF, SENTINEL_A, package_header, profile
from probe_pi32v2 import PIN, ROOT, require, upstream

OTP_DEFAULT=(0xffffffff,)*4  # explicit synthetic input, not an inferred erased device
OTP_TRIMMED=(0xffffffff,0xffffffff,0xfff12fff,0xffffffff)
OTP_CACHE=(0x126860,0x126864,0x12686c,0x126868)


def startup(module,app,header,rom,*,name,options=None,registers=None,budget=250000,
            diagnostic_options=None,extra_regions=(),extra_readonly=(),extra_snapshots=(),machine=None,
            post_stop=None):
    h=profile(header,SENTINEL_A)
    h['description']['phase']='conditional startup with explicit virtual OTP, oscillator and held-CPU1 inputs'
    h['description'].pop('maskrom_executed',None)
    h['description'].pop('pll0_bit0_set_is_hypothesis',None)
    h['regions'] += [(UPDATE,UPDATE+128,'retained_update_parameters'),(0x19fd68,0x19fda4,'rom_mask_context'),
                     (0x120000,0x122800,'mmu_page_table')]
    h['regions'] += list(extra_regions);h['readonly'] += list(extra_readonly)
    h['seeds'].append((UPDATE+8,update_block('empty')[0]))
    h['snapshots'] += [(f'efuse_page{page}',address,4) for page,address in enumerate(OTP_CACHE)]
    h['snapshots'].append(('rch_reference_count',0x12a3cc,1))
    h['snapshots'] += [('clock_state_688',0x126870,4),('clock_state_704',0x126880,4)]
    h['snapshots'] += list(extra_snapshots)
    initial={CLOCK0:0,CLOCK0+4:0,CLOCK0+8:0,PLL0:1,0xfd1920:0,0xfd1954:0,SYS_DIV:0}
    for address,value in (registers or {}).items():
        if value is None:initial.pop(address,None)
        else:initial[address]=value
    parameters=dict(initial_registers=initial,initial_irq_config=[0]*32,initial_ipmask=0,
        reset_inputs_inactive=True,bus_lock_model=True,initial_lock_owner=None,gpio_model=True,
        mmu_model=True,initial_mmu_con=0,p11_access_probe=True,p11_status=1,
        watchdog_projection=True,watchdog_progress=(1,4000),efuse_read_projection=True,efuse_words=OTP_DEFAULT,
        oscillator_configuration_projection=True,oscillator_status_word=16,timer_lsb_ticks_per_step=4,
        gpcnt_cleanup_projection=True,core_tick_configuration=True,initial_core_tick_controls=(0,0),
        cpu1_held_projection=True,initial_cpu1_control=0,clock_divider_projection=True)
    parameters.update(options or {})
    diagnostics={'zero_rep':'skip_hypothesis','adapt_icfg':True,'icfg_initial':0x300,'irq_repeat_return':True,
        'update_repeat':True,'rom_exact_stores':True,'rom_memset_rep':True,'rom_zero_rep_skip':True,
        'visible_cli':True,'opaque_pin_read':True,'exact_parallel_stores':True,
        'watch_pcs':[0x0603518c,0x060351cc,0x06035200,0x0603529e,0x060352d8,0x060352de,0x06035334]}
    diagnostics.update(diagnostic_options or {})
    result=explore(module,app,name=name,adapt_stack=True,adapt_store=True,r0=HANDOFF,rom=rom,handoff=h,
                   diagnostics=diagnostics,hardware=BR28Model(**parameters),startup_loops=True,budget=budget,machine=machine,
                   post_stop=post_stop)
    result['last_instructions']=result['last_instructions'][-8:]
    for row in result['last_instructions']:row.pop('bytes',None)
    if len(result['memory_events'])>8:result['memory_events']=result['memory_events'][:4]+result['memory_events'][-4:]
    result['memory_events_truncated']=sum(result['memory_event_counts'].values())>len(result['memory_events'])
    print(name,result['completed_steps'],result['stop_pc'],result['stop'],flush=True)
    return result


def run(module,app,header,rom):
    cases=[('efuse_unknown',{'efuse_words':None},{},250000),
           ('oscillator_initial_unknown',{}, {0xfd1920:None},250000),
           ('oscillator_status_unknown',{'oscillator_status_word':None},{},250000),
           ('timer_lsb_unknown',{'timer_lsb_ticks_per_step':None},{},250000),
           ('timer_lsb_stopped',{'timer_lsb_ticks_per_step':0},{},110000),
           ('cpu1_initial_unknown',{'initial_cpu1_control':None},{},250000),
           ('core_tick_initial_unknown',{'initial_core_tick_controls':None},{},250000),
           ('sys_div_initial_unknown',{}, {SYS_DIV:None},250000),
           ('clock_default_trim',{}, {},250000),
           ('clock_custom_trim',{'efuse_words':OTP_TRIMMED},{},250000),
           ('clock_oscillator_already_on',{}, {0xfd1920:1},250000),
           ('clock_voltage_configuration',{'voltage_configuration_projection':True,'pll1_configuration_projection':True},
             {**dict.fromkeys(VOLTAGE_CONFIG,0),PLL1_CON3:0},250000)]
    return [startup(module,app,header,rom,name=name,options=options,registers=registers,budget=budget)
            for name,options,registers,budget in cases]


def check(report):
    rows={r['name']:r for r in report['scenarios']}
    require(rows['efuse_unknown']['stop']['kind']=='unknown_efuse_input','unknown OTP was supplied as zero')
    require(rows['oscillator_initial_unknown']['stop']['address']=='0xfd1920','unknown initial WLA state fabricated')
    require(rows['oscillator_status_unknown']['stop']['kind']=='unknown_oscillator_status_input','status invented by configuration writes')
    require(rows['timer_lsb_unknown']['stop']['kind']=='unsupported_timer_clock_source','LSB source silently enabled')
    require(rows['timer_lsb_stopped']['stop']['kind']=='instruction_budget' and
            rows['timer_lsb_stopped']['completed_steps']==110000 and rows['timer_lsb_stopped']['stop_pc']=='0x12e3d4',
            'stopped LSB clock fabricated pending or looped elsewhere')
    require(rows['cpu1_initial_unknown']['stop']['kind']=='unknown_initial_cpu1_control','CPU1 silently assumed held')
    require(rows['core_tick_initial_unknown']['stop']['kind']=='unknown_core_tick_state','core tick initial state invented')
    require(rows['sys_div_initial_unknown']['stop']['address']=='0xfd0014','SYS_DIV initial state invented')
    for name,words,xosc,pll in [('clock_default_trim',OTP_DEFAULT,0x40000,0x408929b3),
                                ('clock_custom_trim',OTP_TRIMMED,0x10000,0x088929b3),
                                ('clock_oscillator_already_on',OTP_DEFAULT,0x40000,0x408929b3)]:
        r=rows[name]
        require(r['stop']['address']=='0xf2c110' and r['stop_pc']=='0x60002d0','new P33 voltage boundary changed')
        require(r['peripherals']['efuse_read_projection']['completed_data_reads']==16,'real page reader did not run16 data reads')
        for page,word in enumerate(words):
            require(r['memory_snapshots'][f'efuse_page{page}']['bytes_with_unknowns']==struct.pack('<I',word).hex(),
                    'real guest reader byte assembly or page-cache mapping differs')
        require(r['peripherals']['registers']['0xfd1940']==hex(xosc) and
                r['peripherals']['registers']['0xfd4600']==hex(pll),'actual trim branches differ')
        gpcnt=r['peripherals']['gpcnt_cleanup_projection']
        require(gpcnt['known_mask']=='0x81' and gpcnt['known_value']=='0x0' and
                gpcnt['disable_writes']==gpcnt['clear_writes'] and gpcnt['clear_writes']>0,
                'GPCNT cleanup fabricated neighbours or omitted unchanged writes')
        require(not r['peripherals']['cpu1_held_projection']['secondary_cpu_executed'],'CPU1 bypassed')
    require(rows['clock_oscillator_already_on']['instruction_adapters']['mmio_conditional_rmw']>0,
            'actual conditional oscillator-disable RMW did not run')
    r=rows['clock_voltage_configuration']
    require(r['stop_pc']=='0xbb28' and r['stop']['address']=='0xff0500','new real ROM cache-wait boundary changed')
    require(r['instruction_adapters']['exact_full_word_store']>0,'actual stack-word stores did not execute')
    report['conditional_clock_regression_passed']=True


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--rom',type=Path,required=True)
    p.add_argument('--firmware',type=Path,default=ROOT/'firmware/QCY_H3S_7018F_1T2_release_note_V1.1.8_011.ufw')
    p.add_argument('--report',type=Path,required=True);args=p.parse_args()
    rom=args.rom.read_bytes();check_rom(rom)
    report={'firmware_sha256':OFFICIAL_SHA,'application_sha256':APP_SHA,'rom_sha256':ROM_SHA,'simulator_commit':PIN,
            'device_accessed':False,'whole_h3s_boot_simulated':False,'device_otp_or_analog_initial_state_measured':False,
            'suitable_for_flash_approval_alone':False,'scenarios':run(upstream(),application(args.firmware),package_header(args.firmware),rom)}
    check(report);args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(report,indent=2)+'\n')


if __name__=='__main__':main()
