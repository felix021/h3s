#!/usr/bin/env python3
"""Bounded static QCY P11 excerpts with a pinned incomplete external decoder.

No p-code execution, instruction recovery, hardware access or reset-PC claim.
Install pypcode3.3.3 in a separate local venv; sources go to ignored cache.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import struct
import xml.etree.ElementTree as ET

from analyze_p11 import analyze
from artifacts import retrieve
from probe_pi32v2 import ROOT, require
from verify_eq_candidate import load_image


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--fetch',action='store_true',help='download digest-pinned decoder sources')
    p.add_argument('--firmware',type=Path,default=ROOT/'firmware/QCY_H3S_7018F_1T2_release_note_V1.1.8_011.ufw')
    p.add_argument('--report',type=Path,required=True);args=p.parse_args()
    import pypcode
    dependency=json.loads((ROOT/'data/dependencies.json').read_text())['q32s_decoder_reference']
    require(pypcode.__version__==dependency['pypcode_version'],'requires pinned pypcode version')
    cache=ROOT/'tools/reference-cache/q32s-static';cache.mkdir(parents=True,exist_ok=True)
    for name,item in dependency['files'].items():
        target=cache/name
        if args.fetch:
            url=dependency['url'].replace('github.com','raw.githubusercontent.com')+'/'+dependency['commit']+'/data/languages/'+name
            retrieve(url,target,item['sha256'],item['bytes'])
        require(target.exists() and hashlib.sha256(target.read_bytes()).hexdigest()==item['sha256'],
                'missing/modified decoder source; use --fetch')
    compiler=Path(pypcode.__file__).parent/'bin/sleigh'
    subprocess.run([str(compiler),str(cache/'q32s.slaspec'),str(cache/'q32s.sla')],check=True)
    metadata=analyze(args.firmware)
    _,_,plain=load_image(args.firmware)
    resource=int(metadata['jlfs_header_offset'],16)+32
    raw=plain[resource:resource+metadata['resource_bytes']]
    main=metadata['bankcb_entries'][0];load=int(main['address_field'],16)
    data=raw[main['file_offset']:main['file_offset']+main['bytes']]
    language=next(x for x in ET.parse(cache/'JieLi.ldefs').findall('language') if x.attrib['id']=='q32s:LE:32:default')
    context=pypcode.Context(pypcode.ArchLanguage(str(cache),language))

    def region(start,end,reason):
        pc=start;rows=[]
        result={'start':hex(start),'end_exclusive':hex(end),'anchor':reason,'instructions':rows}
        while pc<end:
            require(load<=pc<load+len(data),'decode outside actual common segment')
            try:
                instruction=context.disassemble(data[pc-load:],base_address=pc,max_instructions=1).instructions[0]
            except pypcode.BadDataError:
                result['stop']={'pc':hex(pc),'reason':'undecodable; not skipped'};break
            require(instruction.length>0 and pc+instruction.length<=load+len(data),'truncated decoded instruction')
            rows.append({'pc':hex(pc),'length':instruction.length,'mnemonic':instruction.mnem,'operands':instruction.body})
            pc+=instruction.length
            require(len(rows)<=32,'static excerpt budget exceeded')
        return result

    regions=[region(0x80,0xa4,'resource common-section start; reset PC unproven'),
             region(0xa4,0xc6,'decoded immediate RETI target; RTI not executed'),
             region(0xa70,0xa88,'independent window, no claim of reachable predecessor'),
             region(0xac0,0xb06,'independent MOV immediate0xa400 anchor through bit5 clear'),
             region(0xc14,0xc2c,'independent direct-call candidate block')]
    rows={row['pc']:row for reg in regions for row in reg['instructions']}
    require(rows['0xac0']['mnemonic']=='mov' and rows['0xac0']['operands']=='r0, #0xa400','PWR_CON anchor changed')
    require(rows['0xafa']['mnemonic']=='bitclr' and rows['0xafa']['operands']=='r2, 0x5','PWR_CON bit clear changed')
    for pc,target in [('0xc14','0x8000'),('0xc1a','0x800a'),('0xc20','0x801a'),('0xc26','0x802a')]:
        require(rows[pc]['mnemonic']=='call' and rows[pc]['operands']==target,'external-call candidate changed')
        address=int(pc,16);first,second=struct.unpack_from('<HH',data,address-load)
        require(first>>13==7 and (first>>6)&127==0x4e,'direct call encoding changed')
        signed=first&63
        if signed&32:signed-=64
        require(address+4+((signed<<17)|(second<<1))==int(target,16),
                'independent relative-call target calculation disagrees')
    dispatch=bytes(data[0xc0e-load:0xc13-load])
    require([0xc0e+entry*2 for entry in dispatch]==[0xc14,0xc1a,0xc20,0xc26,0xc2c],
            'decoded branch-table entries no longer corroborate call-block boundaries')
    report={'mode':'static excerpts only; not instruction execution','resource_sha256':metadata['resource_sha256'],
            'common_sha256':main['data_sha256'],'decoder_commit':dependency['commit'],
            'pypcode_version':pypcode.__version__,'compiled_sla_sha256':hashlib.sha256((cache/'q32s.sla').read_bytes()).hexdigest(),
            'device_accessed':False,'q32s_code_executed':False,'reset_pc_proven':False,'p11_rom_acquired':False,
            'relative_call_fields_and_dispatch_table_crosschecked':True,
            'whole_h3s_boot_simulated':False,'regions':regions,
            'limitations':['incomplete external decoder; successful decode is not ISA validation',
                '0x86 special-register decode is suspicious; backend JGE p-code conflicts with mnemonic',
                'independent windows do not bridge unknown instructions or prove control-flow reachability',
                'calls outside resource suggest a P11 ROM dependency; memory type and CPU0 alias unproven']}
    args.report.parent.mkdir(parents=True,exist_ok=True);args.report.write_text(json.dumps(report,indent=2)+'\n')
    print('Bounded q32s static excerpts checked; no q32s execution or ROM-mapping proof.')


if __name__=='__main__':main()
