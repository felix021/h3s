#!/usr/bin/env python3
"""Locate and validate the actual QCY P11 resource; no device or q32s execution."""
import argparse
import hashlib
import json
from pathlib import Path
import struct

from probe_boot import OFFICIAL_SHA
from probe_pi32v2 import ROOT, require
from verify_eq_candidate import load_image
from verify_ufw_integrity import crc, verify


def analyze(firmware):
    raw,_,plain=load_image(firmware)
    require(hashlib.sha256(raw).hexdigest()==OFFICIAL_SHA and verify(firmware)['passed'],
            'requires exact CRC-valid official UFW')
    position=0;found=[]
    while position<len(plain):
        _,_,offset,size,_,_,end,name=struct.unpack_from('<HHIIBBH16s',plain,position)
        require(size>=32 and position+size<=len(plain),'JLFS resource bounds')
        if name.split(b'\0')[0]==b'p11_code.bin':
            require(offset==32,'P11 JLFS content offset')
            found.append((position,plain[position+32:position+size]))
        position+=size
        if end:break
    require(len(found)==1,'requires unique P11 resource')
    position,data=found[0]
    require(len(data)==12248 and hashlib.sha256(data).hexdigest()==
            '1fe36008118168d2178c0cb3aeec265fc78e47ce70cd652e633236beb8270882','P11 resource changed')
    count=struct.unpack_from('<H',data)[0];require(count==7,'BANKCB header count')
    entries=[];cursor=count*16
    for i in range(count):
        bank,size,address,offset,expected_data,expected_header=struct.unpack_from('<HHIIHH',data,i*16)
        require(offset==cursor and size>0 and offset+size<=len(data),'BANKCB coverage')
        payload=data[offset:offset+size]
        require(crc(data[i*16:i*16+14])==expected_header and crc(payload)==expected_data,'BANKCB CRC')
        entries.append({'index':i,'bank_field':bank,'bytes':size,'address_field':hex(address),
                        'file_offset':offset,'header_crc_ok':True,'data_crc_ok':True,
                        'data_sha256':hashlib.sha256(payload).hexdigest()})
        cursor+=size
    require(cursor==len(data) and entries[0]['address_field']=='0x80' and entries[0]['bytes']==12112,
            'P11 common-code layout')
    return {'official_ufw_sha256':OFFICIAL_SHA,'resource':'p11_code.bin','jlfs_header_offset':hex(position),
            'resource_bytes':len(data),'resource_sha256':hashlib.sha256(data).hexdigest(),
            'bankcb_entries':entries,'sdk_loader_common_code_mapping':{'p11_local':'0x80','main_cpu':'0xf20080'},
            'q32s_code_executed':False,'p11_rom_acquired':False,'early_handshake_semantics_proven':False,
            'whole_h3s_boot_simulated':False,'device_accessed':False}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--firmware',type=Path,default=ROOT/'firmware/QCY_H3S_7018F_1T2_release_note_V1.1.8_011.ufw')
    p.add_argument('--report',type=Path,required=True);args=p.parse_args()
    report=analyze(args.firmware);args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(report,indent=2)+'\n')
    print('Actual QCY P11 resource located;7 header CRCs and7 data CRCs valid. No q32s execution.')


if __name__=='__main__':main()
