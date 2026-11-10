#!/usr/bin/env python3
"""Locate QCY's own six Timer ISR pointers and decode them at RAM addresses."""
import argparse
import json
from pathlib import Path
import struct

from probe_boot import APP_SHA, ENTRY, application
from probe_boot_handoff import RAM_CODE
from probe_pi32v2 import PIN, ROOT, require, upstream


def source_address(runtime):
    if RAM_CODE[0]<=runtime<RAM_CODE[0]+RAM_CODE[2]:
        return RAM_CODE[1]+runtime-RAM_CODE[0]
    return runtime


def analyze(module,app):
    table=module.table.load();starts={};offset=app.find(bytes.fromhex('e904'))
    for pc,expected in ((0x06065d8a,0x19fe00),(0x060c84ae,0x126f6c)):
        insn,fields,_=module.codec.decode(table,app[pc-ENTRY:pc-ENTRY+6])
        require(insn.id=='li.u32' and fields['n']==expected,'QCY vector/callback pointer changed')
    while offset!=-1:
        got=module.codec.decode(table,app[offset+2:offset+8])
        if offset%2==0 and got and got[0].id in ('push.rl','push.rmask'):
            source=ENTRY+offset
            runtime=source-RAM_CODE[1]+RAM_CODE[0] if RAM_CODE[1]<=source<RAM_CODE[1]+RAM_CODE[2] else source
            starts[runtime]=source
        offset=app.find(bytes.fromhex('e904'),offset+1)
    candidates=[]
    for offset in range(0,len(app)-24,4):
        entries=struct.unpack_from('<6I',app,offset)
        if len(set(entries))==6 and all(address in starts for address in entries):
            candidates.append((ENTRY+offset,entries))
    require(len(candidates)==1,'expected one six-Timer IRQ pointer table')
    address,entries=candidates[0];handlers=[]
    for irq,runtime in enumerate(entries,4):
        pc=runtime;instructions=[];calls=[];save_words=3
        while pc<runtime+128:
            source=source_address(pc);got=module.codec.decode(table,app[source-ENTRY:source-ENTRY+6])
            require(got is not None,'Timer handler decode failed')
            insn,raw,size=got
            if pc==runtime+2:
                require(insn.id=='push.rl' and raw=={'l':0},'expected QCY r0..r3 save')
                save_words+=4
            field=module.branch_field(insn)
            if insn.id.startswith('call.') and field:
                calls.append({'site':hex(pc),'target':hex(pc+size+module.check.operands(insn,raw)[field])})
            instructions.append({'pc':hex(pc),'instruction_id':insn.id,'text':module.codec.format(insn,raw)})
            pc+=size
            if insn.id=='rti':break
        require(instructions[-1]['instruction_id']=='rti' and pc-runtime==38,'Timer C IRQ shape changed')
        handlers.append({'irq':irq,'runtime':hex(runtime),'source':hex(starts[runtime]),'bytes':pc-runtime,
                         'software_save_bytes':save_words*4,'calls':calls,'instructions':instructions})
    return {'application_sha256':APP_SHA,'simulator_commit':PIN,'pointer_table':hex(address),
            'irq_vector_base':'0x19fe00','vector_registration_routine':'0x6065d86',
            'callback_table':'0x126f6c','handlers':handlers,'device_accessed':False,
            'architectural_interrupt_entry_or_rti_proven':False,
            'meaning':'actual QCY C ISR bytes and software-frame shape; not the SDK76B assembly veneer'}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--firmware',type=Path,default=ROOT/'firmware/QCY_H3S_7018F_1T2_release_note_V1.1.8_011.ufw')
    p.add_argument('--report',type=Path,required=True);args=p.parse_args()
    report=analyze(upstream(),application(args.firmware))
    args.report.parent.mkdir(parents=True,exist_ok=True);args.report.write_text(json.dumps(report,indent=2)+'\n')
    print('QCY Timer ISR table:',report['pointer_table'],'; six actual handlers,28B software frames')


if __name__=='__main__':main()
