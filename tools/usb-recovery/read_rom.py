#!/usr/bin/env python3
"""Read the H3S CPU ROM window twice via a pinned RAM loader, never write Flash.

Requires a fresh, normal QCY USB connection on rooted 64-bit Linux/Android.
Raw images may contain vendor material: keep the output private/ignored.
"""
import argparse
import errno
import hashlib
import json
import os
from pathlib import Path
import time

from h3s_usb import LOADER_SHA, crc, load_ram, require, save_json
from usbfs import LinuxTransport, enter_rom, open_target

ROM_SIZE=0x20000
READ_BLOCK=128


class ROMReadTransport(LinuxTransport):
    def __init__(self,loader):
        require(len(loader)==23552 and hashlib.sha256(loader).hexdigest()==LOADER_SHA,
                'pinned BR28 RAM loader required')
        self.loader=loader
        self.loaded=0
        self.phase='fresh-rom'
        super().__init__()

    def execute(self,cdb,size=0,data=None):
        require(1<=len(cdb)<=16,'invalid CDB')
        opcode=int.from_bytes(cdb[:2],'big')
        if cdb[0]!=0x12:
            require(len(cdb)==16,'vendor CDB must be 16 bytes')
        if cdb[0]==0x12:
            require(cdb==bytes([0x12,0,0,0,36,0]) and data is None and size==36,'invalid INQUIRY')
        elif opcode==0xfb06:
            address=int.from_bytes(cdb[2:6],'big');length=int.from_bytes(cdb[6:8],'big')
            require(self.phase=='fresh-rom' and data is not None,'RAM upload phase mismatch')
            require(address==0x120000+self.loaded and length==len(data)==512,'RAM upload sequence mismatch')
            require(data==self.loader[self.loaded:self.loaded+512],'RAM upload differs from pinned loader')
            require(cdb[8]==0 and int.from_bytes(cdb[9:11],'little')==crc(data),'RAM upload CRC mismatch')
            require(cdb[11:]==b'\xff'*5,'unexpected RAM upload fields')
        elif opcode==0xfb08:
            require(self.phase=='fresh-rom' and self.loaded==len(self.loader),'loader incomplete')
            require(cdb[2:8]==bytes.fromhex('001200000001') and data is None and size==16,
                    'only the pinned loader entry/argument may be called')
            require(cdb[8:]==b'\xff'*8,'unexpected loader jump fields')
        elif opcode==0xfc0a:
            require(self.phase=='loader-started' and data is None and size==16,'online query phase mismatch')
            require(cdb[2:]==b'\xff'*14,'unexpected online query fields')
        elif opcode==0xfd07:
            address=int.from_bytes(cdb[2:6],'big');length=int.from_bytes(cdb[6:8],'big')
            require(self.phase=='loader-ready' and data is None,'memory read before loader verification')
            require(length==size and 1<=size<=READ_BLOCK and 0<=address<address+size<=ROM_SIZE,
                    'read outside fixed ROM window or tested block size')
            require(cdb[8:]==b'\xff'*8,'unexpected memory read fields')
        else:
            raise ValueError('opcode forbidden in ROM reader: '+hex(opcode))
        response=self.exchange(cdb,size,data)
        if opcode==0xfb06:
            self.loaded+=len(data)
        elif opcode==0xfb08:
            self.phase='loader-started'
        elif opcode==0xfc0a:
            require(response[:3]==bytes.fromhex('fc0a03'),'RAM loader must report SPI NOR')
            self.phase='loader-ready'
        return response


def memory_read(t,address,size):
    return t.cmd(0xfd07,address.to_bytes(4,'big')+size.to_bytes(2,'big'),size=size)


def read_two_passes(t,work):
    digests=[]
    for pass_id in (1,2):
        path=work/f'rom-000000-020000.pass{pass_id}.bin'
        fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        digest=hashlib.sha256()
        with os.fdopen(fd,'wb') as out:
            for address in range(0,ROM_SIZE,READ_BLOCK):
                block=memory_read(t,address,READ_BLOCK)
                require(len(block)==READ_BLOCK,'short ROM block')
                out.write(block);out.flush();digest.update(block)
                if address%0x4000==0:
                    print('ROM read',pass_id,hex(address),flush=True)
            os.fsync(out.fileno())
        digests.append(digest.hexdigest())
    require(digests[0]==digests[1],'independent ROM reads differ')
    require((work/'rom-000000-020000.pass1.bin').read_bytes()==
            (work/'rom-000000-020000.pass2.bin').read_bytes(),'ROM byte comparison failed')
    return {'status':'ROM_WINDOW_DOUBLE_READ_MATCH','start':0,'end_exclusive':ROM_SIZE,
            'bytes':ROM_SIZE,'sha256':digests[0],'memory_read_block':READ_BLOCK,
            'flash_erase_or_program_commands':0,'otp_write_commands':0,
            'silicon_rom_extent_not_established':True,'raw_files_must_remain_private':True}


def capture(loader_path,work):
    loader=loader_path.read_bytes()
    require(len(loader)==23552 and hashlib.sha256(loader).hexdigest()==LOADER_SHA,'pinned BR28 RAM loader required')
    require(not work.exists(),'use a NEW private output directory')
    existing_rom=open_target(0x4c4a,0x3442)
    if existing_rom:
        os.close(existing_rom[0])
        raise ValueError('fresh normal USB required; existing ROM/loader identity is ambiguous')
    normal=open_target(0x3654,0x4a55)
    require(normal is not None,'fresh normal QCY USB connection required; do not reuse a running loader')
    os.close(normal[0])
    work.mkdir(parents=True,mode=0o700)
    enter_rom()
    for _ in range(100):
        found=open_target(0x4c4a,0x3442)
        if found:
            os.close(found[0]);break
        time.sleep(.05)
    else:
        raise RuntimeError('BR28 ROM did not enumerate')
    t=ROMReadTransport(loader)
    try:
        info=t.inquiry()
        require(info[8:16].strip()==b'BR28' and info[16:32].strip()==b'UBOOT1.00','unexpected ROM identity')
        load_ram(t,loader_path)
        probe=memory_read(t,0x1fdc0,128)
        require(probe==memory_read(t,0x1fdc0,128) and len(set(probe))>=8,'ROM export probe unstable/empty')
        result=read_two_passes(t,work)
        require((work/'rom-000000-020000.pass1.bin').read_bytes()[0x1fdc0:0x1fe40]==probe,
                'ROM probe differs from full read')
        result['ram_loader_sha256']=LOADER_SHA
        save_json(work/'report.json',result)
        print(json.dumps(result),flush=True)
    finally:
        try:t.close()
        except OSError as e:
            if e.errno not in (errno.ENODEV,errno.ESHUTDOWN):
                raise
    print('Read complete. Unplug USB and manually restart the headset; check playback and ANC key.',flush=True)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--loader',type=Path,required=True)
    p.add_argument('--workdir',type=Path,required=True)
    args=p.parse_args()
    capture(args.loader,args.workdir)


if __name__=='__main__':
    main()
