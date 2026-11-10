#!/usr/bin/env python3
"""Check the pinned SDK IRQ4 veneer against exact QCY bytes, without a ROM/device."""
import argparse
import hashlib
import json
from pathlib import Path

from probe_boot import APP_SHA, ENTRY, application
from probe_pi32v2 import ROOT, require, upstream


def analyze(module,app,raw):
    dep=json.loads((ROOT/'data/dependencies.json').read_text())
    expected=dep['hardware_reference_objects']['hwi.S.o']['sha256']
    require(hashlib.sha256(raw).hexdigest()==expected,'unexpected SDK IRQ assembly object')
    obj=module.read_exec(raw)
    body=next(s['data'] for s in obj['sections'] if s['name']=='.text')
    require(len(body)==2772,'SDK IRQ veneer table shape changed')
    code=body[44:66]  # IRQ2 first,22B per veneer; IRQ4 offset44.
    decoded=[];offset=0;save_words=0;table=module.table.load()
    while offset<len(code):
        insn,fields,size=module.codec.decode(table,code[offset:offset+6])
        if insn.id in ('push.rmask','push.srl'):
            save_words+=fields.get('m',fields.get('n',0)).bit_count()
        decoded.append({'instruction_id':insn.id,'text':module.codec.format(insn,fields)})
        offset+=size
    require([i['instruction_id'] for i in decoded]==['push.rmask','push.srl','li.u8',
            'call.xl','pop.srl','pop.rmask','rti'] and save_words==19,'SDK IRQ4 save sequence changed')
    matches=[];offset=app.find(code[:10])
    while offset!=-1:
        if app[offset+14:offset+22]==code[14:22]:matches.append(hex(ENTRY+offset))
        offset=app.find(code[:10],offset+1)
    return {'reference_object_sha256':expected,'application_sha256':APP_SHA,
            'reference_irq':4,'reference_veneer_bytes':22,'nonrelocated_bytes_compared':18,
            'reference_software_save_bytes':save_words*4,'instructions':decoded,
            'qcy_matching_sites':matches,'reference_is_proven_qcy_irq_abi':False,
            'device_accessed':False,'cpu_interrupt_delivery_simulated':False}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--object',type=Path,required=True)
    p.add_argument('--firmware',type=Path,default=ROOT/'firmware/QCY_H3S_7018F_1T2_release_note_V1.1.8_011.ufw')
    p.add_argument('--report',type=Path,required=True)
    args=p.parse_args()
    report=analyze(upstream(),application(args.firmware),args.object.read_bytes())
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(report,indent=2)+'\n')
    print('Reference software saves:',report['reference_software_save_bytes'],
          'bytes; QCY matches:',len(report['qcy_matching_sites']))


if __name__=='__main__':main()
