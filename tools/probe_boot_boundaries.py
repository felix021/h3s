#!/usr/bin/env python3
"""Audit strict stops and explicit counterfactual startup paths, offline only.

No ROM/API stub, MMIO latch or interrupt delivery model is installed. Unknown
control writes stop. Isolated setup_arch is not a continuation of failed boot.
"""
import argparse
import binascii
import hashlib
import json
from pathlib import Path
import struct

from probe_boot import APP_SHA, ENTRY, OFFICIAL_SHA, SDK_PIN, STACK_TOP, application, explore
from probe_boot_handoff import BSS, DATA, HANDOFF, RAM_CODE, SENTINEL_A, package_header, profile
from probe_pi32v2 import PIN, ROOT, require, upstream

UPDATE=0x0019fb80
RESULT=0x00126718
QEMU_PIN='519a13b879d744f5c05bfe3c4e5d4e04d8d4ae34'


def update_block(kind):
    if kind=='unknown':
        return None,None
    body=bytearray(78)
    if kind!='empty':
        struct.pack_into('<HH',body,2,0x5a01,2)
    crc=binascii.crc_hqx(body,0)
    stored=crc^(1 if kind=='invalid' else 0)
    return struct.pack('<H',stored)+body,crc


def startup_reference(module,app,raw):
    expected=json.loads((ROOT/'data/dependencies.json').read_text())['boot_reference_objects']['startup.S.o']['sha256']
    require(hashlib.sha256(raw).hexdigest()==expected,'unexpected SDK startup object')
    elf=module.read_exec(raw)
    section=next(s for s in elf['sections'] if s['name']=='.startup.text')
    body=section['data']
    mask=bytearray([1]*len(body))
    relocs=[]
    for section_relocs in elf['sections']:
        if section_relocs['type']!=4 or section_relocs['info']!=section['index']:
            continue
        for off in range(0,len(section_relocs['data']),12):
            address,info,addend=struct.unpack_from('<IIi',section_relocs['data'],off)
            require((info&255) in (4,5) and addend==0,'unexpected startup relocation')
            insn,fields,size=module.codec.decode(module.table.load(),app[address:address+6])
            require(size==6,'startup relocation is not a 6B instruction')
            for j in range(address+2,address+6):
                mask[j]=0
            operands=module.check.operands(insn,fields)
            name=elf['symbols'][info>>8][0]
            value=ENTRY+address+size+operands[module.branch_field(insn)] if info&255==4 else operands['n']
            relocs.append({'instruction':hex(ENTRY+address),'reference_symbol':name,
                           'resolved_qcy_value':hex(value),'relocation_type':info&255})
    require(len(body)==170 and len(relocs)==18,'startup reference shape changed')
    require(all(not keep or body[i]==app[i] for i,keep in enumerate(mask)),
            'QCY startup differs outside relocated immediate fields')
    ram_section=next(s for s in elf['sections'] if s['name']=='.volatile_ram_code')
    helper=ram_section['data'][0x34:0x58]
    source=RAM_CODE[1]+0x0012cb08-RAM_CODE[0]-ENTRY
    helper_target=app[source:source+len(helper)]
    require(len(helper)==36 and all(i in range(2,6) or a==b for i,(a,b) in enumerate(zip(helper,helper_target))),
            'IRQ-disabled helper differs outside its relocated pointer')
    return {'object_sha256':expected,'startup_bytes':len(body),'relocations':relocs,
        'non_relocated_bytes_checked':sum(mask),'all_non_relocated_bytes_equal':True,
        'irq_disabled_helper':{'reference_offset':'0x34','qcy_runtime':'0x12cb08',
                               'bytes':36,'non_relocated_bytes_checked':32,'all_non_relocated_bytes_equal':True},
        'meaning':'matching startup structure and relocation identities; not proof of exact QCY SDK source'}


def static_edges(module,app):
    calls=[]
    for pc,name in ((0x06000196,'update_result_get'),(0x0600019c,'maskrom_init'),
                    (0x060664d0,'mask_init (reference ROM export)')):
        i,raw,size=module.codec.decode(module.table.load(),app[pc-ENTRY:pc-ENTRY+6])
        target=pc+size+module.check.operands(i,raw)[module.branch_field(i)]
        calls.append({'site':hex(pc),'target':hex(target),'reference_name':name,
                      'executed_in_contiguous_profile':pc!=0x060664d0})
    require([r['target'] for r in calls]==['0x6010882','0x60664ba','0x1fde8'],'static call edges changed')
    return calls


def check(report,app):
    rows={s['name']:s for s in report['scenarios']}
    expected={
        'strict_zero':(16077,'0x6000182','unverified_zero_count_rep'),
        'zero_skip_icfg_unmodeled':(16079,'0x600018a','unsupported_special_register'),
        'zero_skip_icfg_unknown':(16079,'0x600018a','uninitialized_special_register'),
        'icfg_seeded_irq_rep_strict':(16099,'0x12cb12','unsupported_runtime_instruction'),
        'retained_unknown':(16116,'0x6000ee0','uninitialized_memory'),
        'retained_empty':(17248,'0x60664c4','unmapped_or_readonly_write'),
        'retained_valid':(17251,'0x60664c4','unmapped_or_readonly_write'),
        'retained_invalid':(17250,'0x60664c4','unmapped_or_readonly_write'),
        'retained_valid_alt_icfg':(17251,'0x60664c4','unmapped_or_readonly_write'),
        'retained_valid_chunk7':(20297,'0x60664c4','unmapped_or_readonly_write'),
        'retained_valid_chunk256':(17331,'0x60664c4','unmapped_or_readonly_write'),
        'isolated_setup_cli_strict':(10,'0x12b55c','unsupported_runtime_instruction'),
        'isolated_setup_cli_visible':(15,'0x12b56c','unmapped_or_readonly_write')}
    require(set(rows)==set(expected),'boundary scenario names changed')
    for name,(steps,pc,kind) in expected.items():
        r=rows[name]
        require((r['completed_steps'],r['stop_pc'],r['stop']['kind'])==(steps,pc,kind),
                'boundary regression changed: '+name)
    require(rows['retained_unknown']['stop']['address']==hex(UPDATE+10),'wrong retained read address')
    for name in ('retained_empty','retained_valid','retained_invalid','retained_valid_alt_icfg',
                 'retained_valid_chunk7','retained_valid_chunk256'):
        r=rows[name]
        require(r['stop']['address']=='0xf2c11c' and r['stop']['value_bytes']=='00000000','control write must stop')
        variant='empty' if name=='retained_empty' else 'invalid' if name=='retained_invalid' else 'valid'
        _,crc=update_block(variant)
        actual_crc=[e for e in r['milestones'] if e['kind']=='software_crc_result']
        require(len(actual_crc)==1 and int(actual_crc[0]['r0'],16)==crc,'QCY CRC differs from stdlib CRC')
        result=0x00005a00 if variant=='empty' else 0x00025a00 if variant=='invalid' else 0x00025a01
        require(r['memory_snapshots']['update_result']['bytes_with_unknowns']==struct.pack('<I',result).hex(),
                'wrong upgrade-result interpretation')
        clear=r['memory_snapshots']['retained_update_clear']
        require(clear.get('sha256')==hashlib.sha256(bytes(80)).hexdigest() and clear['initialized_bytes']==80,
                'retained update parameter clear failed')
        require(r['memory_snapshots']['retained_window']['initialized_bytes']==80,'retained RAM outside clear range was fabricated')
        bss=bytearray(BSS[1])
        struct.pack_into('<I',bss,RESULT-BSS[0],result)
        require(r['memory_snapshots']['bss'].get('sha256')==hashlib.sha256(bss).hexdigest(),
                'unexpected BSS changes beyond update-result word')
        for label,(_,source,length) in (('data',DATA),('ram_code',RAM_CODE)):
            require(r['memory_snapshots'][label].get('sha256')==hashlib.sha256(app[source-ENTRY:source-ENTRY+length]).hexdigest(),
                    'startup copy differs from application: '+label)
        require(r['instruction_adapters']['retained_update_clear_word_store']==20,'wrong update-clear loop count')
        returned=[e for e in r['milestones'] if e['kind']=='update_result_get_return']
        require(len(returned)==1 and returned[0]['sp']==hex(STACK_TOP),'update-result stack not restored')
        initial=r['assumptions']['diagnostics']['icfg_initial']
        require(int(r['last_registers']['icfg'],16)==initial&~0x300,'ICFG mask clobbered unrelated bits')
    reference=rows['retained_valid']['memory_snapshots']
    for name in ('retained_valid_alt_icfg','retained_valid_chunk7','retained_valid_chunk256'):
        for label in ('bss','data','ram_code','update_result','retained_update_clear'):
            require(rows[name]['memory_snapshots'][label]==reference[label],
                    'hypothesis sensitivity changed final memory: '+name+'/'+label)
    irq=rows['isolated_setup_cli_visible']
    require(irq['stop']['address']=='0xffe1a8' and irq['stop']['value_bytes']=='06000000','wrong IPMASK write boundary')
    report['boundary_regression_passed']=True
    report['qcy_software_crc_matches_stdlib']=True
    report['rep_chunk_and_icfg_sensitivity_memory_equal']=True


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--fetch-simulator',action='store_true')
    p.add_argument('--firmware',type=Path,default=ROOT/'firmware/QCY_H3S_7018F_1T2_release_note_V1.1.8_011.ufw')
    p.add_argument('--startup-object',type=Path,help='optional pinned SDK object for relocation matching')
    p.add_argument('--report',type=Path)
    args=p.parse_args()
    app=application(args.firmware)
    header=package_header(args.firmware)
    module=upstream(args.fetch_simulator)
    results=[]

    def run(name,diag=None,retained='unknown',isolated=False):
        h=profile(header,SENTINEL_A)
        h['regions'].append((UPDATE,UPDATE+128,'retained_update_parameters'))
        h['snapshots'] += [('update_result',RESULT,4),('retained_update_clear',UPDATE+8,80),
                           ('retained_window',UPDATE,128)]
        block,_=update_block(retained)
        if block is not None:
            h['seeds'].append((UPDATE+8,block))
        if isolated:
            h['seeds'] += [(BSS[0],bytes(BSS[1]))]+[(dst,app[src-ENTRY:src-ENTRY+length])
                                                          for dst,src,length in (DATA,RAM_CODE)]
            h['description']['isolated_phase']='setup_arch only; memory seeded from known initializer outputs, not contiguous startup'
        r=explore(module,app,name=name,adapt_stack=True,pll=None if isolated else 1,r0=HANDOFF,
                  handoff=h,startup_loops=not isolated,diagnostics=diag)
        r['phase']='isolated setup_arch' if isolated else 'application-entry prefix'
        results.append(r)
        print(name,r['completed_steps'],'steps;',r['stop_pc'],r['stop'],flush=True)

    run('strict_zero')
    run('zero_skip_icfg_unmodeled',{'zero_rep':'skip_hypothesis'})
    unknown={'zero_rep':'skip_hypothesis','adapt_icfg':True}
    run('zero_skip_icfg_unknown',unknown)
    seeded={**unknown,'icfg_initial':0x300}
    run('icfg_seeded_irq_rep_strict',seeded)
    helpers={**seeded,'irq_repeat_return':True,'update_repeat':True}
    run('retained_unknown',helpers)
    for variant in ('empty','valid','invalid'):
        run('retained_'+variant,helpers,variant)
    run('retained_valid_alt_icfg',{**helpers,'icfg_initial':0x01090700},'valid')
    for chunk in (7,256):
        run('retained_valid_chunk'+str(chunk),{**helpers,'rep_chunk':chunk},'valid')
    setup={'entry_pc':0x06034fea,'initial_sp':STACK_TOP,'icfg_initial':0,'adapt_icfg':True,'irq_repeat_return':True}
    run('isolated_setup_cli_strict',setup,isolated=True)
    run('isolated_setup_cli_visible',{**setup,'visible_cli':True},isolated=True)
    report={'format_version':1,'simulator_commit':PIN,'reference_sdk_commit':SDK_PIN,
        'qemu_policy_reference_commit':QEMU_PIN,'firmware_sha256':OFFICIAL_SHA,'application_sha256':APP_SHA,
        'device_accessed':False,'firmware_created':False,'whole_h3s_boot_simulated':False,
        'suitable_for_flash_approval_alone':False,'rom_functions_stubbed':False,
        'unknown_control_writes_applied':False,'static_call_edges':static_edges(module,app),'scenarios':results}
    check(report,app)
    if args.startup_object:
        report['reference_startup_match']=startup_reference(module,app,args.startup_object.read_bytes())
    # Curated evidence only: never publish a bulk RAM/code write transcript.
    for r in results:
        r['last_instructions']=r['last_instructions'][-12:]
        if len(r['memory_events'])>8:
            r['memory_events']=r['memory_events'][:4]+r['memory_events'][-4:]
            r['memory_events_truncated']=True
    report['public_trace_limits']={'last_instructions':12,'memory_events':8}
    text=json.dumps(report,indent=2)+'\n'
    if args.report:
        args.report.parent.mkdir(parents=True,exist_ok=True)
        args.report.write_text(text)
    else:
        print(text,end='')


if __name__=='__main__':
    main()
