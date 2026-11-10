#!/usr/bin/env python3
"""Offline validation of two private 128KiB ROM reads and pinned SDK exports."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import struct

from image import require

STUBS_SHA='331e973cbd71d5f9add4b77a796036f88e8dad93f940b70bb82398b7748a4547'
ROM_SIZE=0x20000


def validate(first,second,stubs):
    require(len(first)==len(second)==ROM_SIZE,'expected two 128KiB address windows')
    require(first==second,'independent ROM reads differ')
    require(hashlib.sha256(stubs).hexdigest()==STUBS_SHA,'reference SDK stubs digest mismatch')
    source=re.sub(r'/\*.*?\*/','',stubs.decode(),flags=re.S)
    symbols=re.findall(r'^\s*(\w+)\s*=\s*ABSOLUTE\((0x[0-9a-fA-F]+)\);',source,re.M)
    # The linker file also names data tables, RAM objects and size constants.
    symbols=[(name,entry) for name,entry in symbols if 0x1fd6c<=int(entry,16)<ROM_SIZE]
    require(len(symbols)==162,'unexpected named export count')
    exports=[]
    for name,entry in symbols:
        address=int(entry,16)
        require(address%2==0 and 0<=address<=ROM_SIZE-4,'export outside ROM window')
        first_word,second_word=struct.unpack_from('<HH',first,address)
        # pi32v2 goto.l: 1110101011pppppp ssssssssssssssss.
        # Signed 22-bit ps displacement, scaled by 2, relative to instruction END.
        require(first_word&0xffc0==0xeac0,'export is not goto.l: '+name)
        displacement=((first_word&0x3f)<<16)|second_word
        if displacement&(1<<21):displacement-=1<<22
        target=address+4+displacement*2
        require(target%2==0 and 0<=target<ROM_SIZE,'export target outside ROM window: '+name)
        exports.append({'name':name,'entry':hex(address),'target':hex(target)})
    return {'status':'ROM_WINDOW_VERIFIED','bytes':ROM_SIZE,
            'sha256':hashlib.sha256(first).hexdigest(),'two_full_reads_equal':True,
            'matched_export_stubs':len(exports),'exports':exports,
            'full_silicon_rom_extent_established':False}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--first',type=Path,required=True)
    p.add_argument('--second',type=Path,required=True)
    p.add_argument('--stubs',type=Path,required=True)
    args=p.parse_args()
    print(json.dumps(validate(args.first.read_bytes(),args.second.read_bytes(),
                              args.stubs.read_bytes()),indent=2))


if __name__=='__main__':
    main()
