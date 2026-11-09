#!/usr/bin/env python3
"""Independent H3S UFW/JLFS/dual-bank CRC audit (stdlib only, no builder imports)."""
import argparse
import binascii
import hashlib
import json
from pathlib import Path
import struct


def crc(data):
    return binascii.crc_hqx(data, 0)


def decode(data, key=0xffff):
    out=bytearray()
    for b in data:
        out.append(b^(key&255))
        high=key&0x8000
        key=(key*2)&65535
        if high: key^=0x1021
    return bytes(out)


def sfc(data, origin=0):
    return b''.join(decode(data[p:p+32],0x3496^((origin+p)//4)) for p in range(0,len(data),32))


def verify(path):
    data=path.read_bytes()
    errors=[]
    header=decode(data[:64])
    hc,dc,size,count,_,_,chip=struct.unpack('<HHIHHI48s',header)
    if size!=len(data) or crc(header[2:])!=hc: errors.append('UFW header invalid')
    if crc(data[64:64+count*80])!=dc: errors.append('UFW directory CRC invalid')
    entries=[]
    flash=None
    for i in range(count):
        row=decode(data[64+i*80:144+i*80])
        kind,_,check,_,offset,length,padded,_,name=struct.unpack('<HHHHIII44s16s',row)
        name=name.split(b'\0')[0].decode('ascii')
        if length>padded or offset+padded>len(data): raise ValueError('outer entry bounds')
        raw=data[offset:offset+padded]
        content=sfc(raw,offset) if kind in (50,52,55,56,161,251) else raw
        valid=crc(content[:length])==check
        if not valid: errors.append('outer CRC: '+name)
        entries.append({'name':name,'crc_ok':valid})
        if name=='flash.bin': flash=content[:length]
    if flash is None: raise ValueError('missing flash.bin')
    boot=decode(flash[0x2000:0x2020])
    bh,plain_expected,encrypted_expected,reserved,length=struct.unpack_from('<HHHHI',boot)
    end=0x2020+length
    if end>len(flash): raise ValueError('dual-bank bounds')
    app=sfc(flash[0x2020:end])
    boot_checks={
        'header':{'expected':bh,'actual':crc(boot[2:])},
        'plaintext':{'expected':plain_expected,'actual':crc(app)},
        'ciphertext':{'expected':encrypted_expected,'actual':crc(flash[0x2020:end])},
    }
    for name,row in boot_checks.items():
        row['ok']=row['expected']==row['actual']
        if not row['ok']: errors.append('dual-bank '+name+' CRC invalid')
    files=[]
    stream=None
    p=0
    while p+32<=len(app):
        h,d,address,block,flags,res,index,name=struct.unpack_from('<HHIIBBH16s',app,p)
        name=name.split(b'\0')[0].decode('ascii')
        if block<32 or p+block>len(app): raise ValueError('JLFS bounds')
        head_ok=crc(app[p+2:p+32])==h
        data_ok=crc(app[p+32:p+block])==d
        if not head_ok or not data_ok: errors.append('JLFS CRC: '+name)
        files.append({'name':name,'header_crc_ok':head_ok,'data_crc_ok':data_ok})
        if name=='stream.bin': stream=app[p+32:p+block]
        if flags==0x83:
            q=p+32
            while q+32<=p+block:
                sh,sd,sa,ss,sf,_,si,sn=struct.unpack_from('<HHIIBBH16s',app,q)
                sn=sn.split(b'\0')[0].decode('ascii')
                head_valid=crc(app[q+2:q+32])==sh
                if not head_valid: errors.append('nested header CRC: '+sn)
                row={'name':name+'/'+sn,'header_crc_ok':head_valid}
                if not sf&0x10:
                    if p+sa+ss>p+block: raise ValueError('nested JLFS bounds')
                    row['data_crc_ok']=crc(app[p+sa:p+sa+ss])==sd
                    if not row['data_crc_ok']: errors.append('nested data CRC: '+sn)
                files.append(row)
                if si: break
                q+=32
        p+=block
        if index: break
    if p!=len(app): errors.append('JLFS does not cover complete dual-bank region')
    volumes={}
    if stream is not None:
        for name,offset in [('Vol_SysTone',0x18f5),('Vol_SysKTone',0x1905)]:
            volumes[name]=list(struct.unpack_from('<BHiBihH',stream,offset))
    return {'file':str(path),'sha256':hashlib.sha256(data).hexdigest(),'bytes':len(data),
            'passed':not errors,'errors':errors,'outer':entries,'dual_bank':boot_checks,
            'app_bytes':length,'jlfs':files,'volume_parameters':volumes}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('firmware',type=Path)
    a=p.parse_args()
    report=verify(a.firmware)
    print(json.dumps(report,ensure_ascii=False,indent=2))
    if not report['passed']: raise SystemExit(1)
