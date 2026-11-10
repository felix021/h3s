#!/usr/bin/env python3
"""Execute the exact private H3S ROM window offline, with strict MMIO stops.

Requires --rom; never downloads or publishes the ROM. No device access.
Isolated functions and REP hypotheses are distinct from contiguous boot.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct

from probe_boot import (APP_SHA, ENTRY, OFFICIAL_SHA, ROM_RETURN, ROM_SHA, SDK_PIN,
                        STACK_TOP, application, check_rom, explore)
from probe_boot_boundaries import UPDATE, update_block
from probe_boot_handoff import HANDOFF, SENTINEL_A, package_header, profile
from probe_pi32v2 import PIN, ROOT, require, upstream

CONTEXT=0x19fd68
BUFFER=0x124000
CALLBACK=0x06000b10  # actual QCY log_putbyte address; not called by mask_init
CANARY=bytes.fromhex('5ac33ca5')
GPRS=[0x13570000+i for i in range(16)]


def function_harness(entry,args):
    regs=list(GPRS);regs[:len(args)]=args
    return {'entry_pc':entry,'initial_sp':STACK_TOP,'initial_gprs':regs,
            'stop_on_rom_return':True,'rom_exact_stores':True}


def describe_rom(module,rom):
    """Small control-flow summaries, never an entire ROM disassembly."""
    result=[];table=module.table.load()
    for name,start,end in (('mask_init',0x4590,0x45bc),('_pll_init',0xc2ce,0xc368),
                           ('timer_delay_helper',0xa2fa,0xa320),('cpu_delay_helper',0xc2b8,0xc2ce)):
        pc=start;count=0;edges=[]
        while pc<end:
            got=module.codec.decode(table,rom[pc:pc+6])
            require(got is not None,'unknown instruction in bounded ROM function')
            insn,raw,size=got;field=module.branch_field(insn)
            require(pc+size<=end,'instruction crosses known function end')
            if field:
                edges.append({'site':hex(pc),'instruction_id':insn.id,
                              'target':hex(pc+size+module.check.operands(insn,raw)[field])})
            pc+=size;count+=1
        result.append({'name':name,'start':hex(start),'end_exclusive':hex(end),
                       'decoded_instructions':count,'static_edges':edges})
    return result


def run(module,app,header,rom):
    results=[]

    def execute(name,diag,h,**kwargs):
        h={**h,'description':{k:v for k,v in h['description'].items()
                            if k not in ('maskrom_executed','pll0_bit0_set_is_hypothesis')}}
        h['description']['rom_mapping']='exact private ROM, no API stubs; see executed-ROM step count'
        result=explore(module,app,name=name,handoff=h,rom=rom,diagnostics=diag,**kwargs)
        result['phase']='isolated ROM function' if diag.get('stop_on_rom_return') else 'application-entry prefix'
        # Public evidence contains bounded state, no raw ROM or bulk RAM mirror.
        result['last_instructions']=result['last_instructions'][-8:]
        for row in result['last_instructions']:
            row.pop('bytes',None)
        if len(result['memory_events'])>8:
            result['memory_events']=result['memory_events'][:4]+result['memory_events'][-4:]
        result['memory_events_truncated']=sum(result['memory_event_counts'].values())>len(result['memory_events'])
        results.append(result)
        print(name,result['completed_steps'],'steps;',result['stop_pc'],result['stop'],flush=True)
        return result

    h=profile(header,SENTINEL_A)
    execute('cold_pll_contiguous',{'rom_exact_stores':True},h,
            adapt_stack=True,adapt_store=True,pll=0)
    execute('pll_isolated_strict',function_harness(0x1fe14,[]),h)

    h=profile(header,SENTINEL_A)
    h['description']['isolated_phase']='mask_init only; not a continuation past unknown application control writes'
    h['regions'].append((CONTEXT-4,CONTEXT+64,'rom_mask_context_with_guards'))
    h['seeds'] += [(CONTEXT-4,CANARY),(CONTEXT+60,CANARY)]
    h['snapshots'] += [('mask_context',CONTEXT,60),('saved_callback',CONTEXT+20,8),
                       ('left_guard',CONTEXT-4,4),('right_guard',CONTEXT+60,4)]
    for name,options in (('mask_strict',{}),('mask_nonzero_rep',{'rom_memset_rep':True}),
                         ('mask_zero_skip_hypothesis',{'rom_memset_rep':True,'rom_zero_rep_skip':True})):
        execute(name,{**function_harness(0x1fde8,[0,CALLBACK]),**options},h)

    # These aligned sizes have nonzero word AND byte tails; no zero-REP skip.
    for length in (61,63):
        h=profile(header,SENTINEL_A)
        h['description']['isolated_phase']='aligned ROM memset; explicit nonzero REP countdown, no zero skip'
        h['regions'].append((BUFFER-4,BUFFER+68,'isolated_memset_buffer'))
        h['seeds'] += [(BUFFER-4,CANARY),(BUFFER+64,CANARY)]
        h['snapshots'] += [('requested',BUFFER,length),('unwritten_tail',BUFFER+length,64-length),
                           ('left_guard',BUFFER-4,4),('right_guard',BUFFER+64,4)]
        execute('memset_aligned_'+str(length),{**function_harness(0x1fd80,[BUFFER,0xa5,length]),
                                             'rom_memset_rep':True},h)

    execute('memset_rom_destination_rejected',
            {**function_harness(0x1fd80,[0,0xa5,61]),'rom_memset_rep':True},profile(header,SENTINEL_A))

    h=profile(header,SENTINEL_A)
    h['regions'].append((UPDATE,UPDATE+128,'retained_update_parameters'))
    block,_=update_block('empty');h['seeds'].append((UPDATE+8,block))
    diag={'zero_rep':'skip_hypothesis','adapt_icfg':True,'icfg_initial':0x300,
          'irq_repeat_return':True,'update_repeat':True,'rom_exact_stores':True}
    execute('warm_pll_control_still_strict',diag,h,adapt_stack=True,pll=1,r0=HANDOFF,startup_loops=True)
    return results


def check(report):
    rows={r['name']:r for r in report['scenarios']}
    for name,steps in (('cold_pll_contiguous',16),('pll_isolated_strict',3)):
        r=rows[name]
        require((r['completed_steps'],r['stop_pc'],r['stop']['kind'])==
                (steps,'0xc2d6','unmapped_or_readonly_write'),'wrong real PLL boundary')
        require(r['stop']['address']=='0xfd4610' and r['stop']['value_bytes']=='00000000',
                'PLL CON4 write must remain blocked')
        require(r['completed_model_steps_in_rom']==3,'PLL ROM path did not execute')
    strict=rows['mask_strict'];nonzero=rows['mask_nonzero_rep'];complete=rows['mask_zero_skip_hypothesis']
    require((strict['completed_steps'],strict['stop_pc'],strict['stop']['kind'])==
            (15,'0x4242','unsupported_runtime_instruction'),'strict ROM REP must stop')
    require((nonzero['completed_steps'],nonzero['stop_pc'],nonzero['stop']['kind'])==
            (33,'0x424c','unverified_zero_count_rep'),'zero-tail REP must stop')
    require(nonzero['memory_snapshots']['mask_context'].get('sha256')==hashlib.sha256(bytes(60)).hexdigest(),
            'ROM memset word clear mismatch')
    expected=bytearray(60);struct.pack_into('<II',expected,20,CALLBACK,0)
    require(complete['memory_snapshots']['mask_context'].get('sha256')==hashlib.sha256(expected).hexdigest(),
            'mask_init output differs from independent expected context')
    require(complete['memory_snapshots']['saved_callback']['bytes_with_unknowns']==struct.pack('<II',CALLBACK,0).hex(),
            'mask_init argument slots differ')
    require(complete['completed_steps']==40 and complete['instruction_adapters']['rom_byte_exact_store']==17,
            'mask_init model path changed')
    for r in (strict,nonzero,complete,rows['memset_aligned_61'],rows['memset_aligned_63']):
        for label in ('left_guard','right_guard'):
            require(r['memory_snapshots'][label]['bytes_with_unknowns']==CANARY.hex(),'ROM wrote outside buffer')
    for r in (complete,rows['memset_aligned_61'],rows['memset_aligned_63']):
        require(r['stop']['kind']=='isolated_function_return' and r['stop_pc']==hex(ROM_RETURN),
                'isolated ROM function did not return')
        require(r['last_registers']['sp']==hex(STACK_TOP),'ROM function stack not restored')
        for reg in range(4,16):
            require(int(r['last_registers']['r'][reg],16)==GPRS[reg],'ROM callee-saved register changed')
    for length in (61,63):
        r=rows['memset_aligned_'+str(length)]
        require(r['memory_snapshots']['requested'].get('sha256')==hashlib.sha256(bytes([0xa5])*length).hexdigest(),
                'actual ROM memset differs from host fill')
        require(r['memory_snapshots']['unwritten_tail']['initialized_bytes']==0,'memset initialized unrelated tail')
        require(int(r['last_registers']['r'][0],16)==BUFFER,'memset return pointer changed')
        require('rom_zero_rep_skip_hypothesis' not in r['instruction_adapters'],'nonzero case used zero skip')
    warm=rows['warm_pll_control_still_strict']
    require(warm['completed_steps']==17248 and warm['stop_pc']=='0x60664c4' and
            warm['stop']['address']=='0xf2c11c','mapped ROM must not bypass the warm-path control write')
    require(warm['completed_model_steps_in_rom']==0,'warm contiguous profile must not silently call ROM')
    rejected=rows['memset_rom_destination_rejected']
    require(rejected['stop_pc']=='0x4244' and rejected['stop']['kind']=='unmapped_or_readonly_write' and
            rejected['stop']['address']=='0x0','ROM destination must remain read-only')
    require(report['rom_tail_fetch_check']=={'entry':'0x1fffc','next_pc':'0x10fb8',
            'completed_steps':1,'stop_kind':'instruction_budget'},'last ROM export fetch changed')
    report['rom_execution_regression_passed']=True
    report['isolated_rom_results_match_host_expectations']=True


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--rom',type=Path,required=True,help='private captured ROM window, exact digest required')
    p.add_argument('--firmware',type=Path,default=ROOT/'firmware/QCY_H3S_7018F_1T2_release_note_V1.1.8_011.ufw')
    p.add_argument('--fetch-simulator',action='store_true')
    p.add_argument('--report',type=Path,required=True)
    args=p.parse_args()
    rom=args.rom.read_bytes();check_rom(rom)
    app=application(args.firmware);header=package_header(args.firmware);module=upstream(args.fetch_simulator)
    report={'format_version':1,'simulator_commit':PIN,'reference_sdk_commit':SDK_PIN,
            'firmware_sha256':OFFICIAL_SHA,'application_sha256':APP_SHA,'rom_sha256':ROM_SHA,
            'device_accessed':False,'firmware_created':False,'raw_rom_published':False,
            'whole_h3s_boot_simulated':False,'suitable_for_flash_approval_alone':False,
            'unknown_control_writes_applied':False,'rom_functions_stubbed':False,
            'rom_functions':describe_rom(module,rom),'scenarios':run(module,app,header,rom)}
    tail=explore(module,app,name='last_rom_export_one_step',rom=rom,budget=1,
                 diagnostics={'entry_pc':0x1fffc})
    report['rom_tail_fetch_check']={'entry':tail['entry'],'next_pc':tail['stop_pc'],
                                  'completed_steps':tail['completed_steps'],'stop_kind':tail['stop']['kind']}
    check(report)
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(report,indent=2)+'\n')


if __name__=='__main__':
    main()
