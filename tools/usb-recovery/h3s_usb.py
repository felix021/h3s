#!/usr/bin/env python3
"""Linux/rooted-Android recovery for the explicitly documented H3S r2 failure.

Default operations read only or load RAM. Flash writes require `restore --apply`
and a previously prepared, independently validated fixed six-sector plan.
"""
import argparse
import json
import os
from pathlib import Path
import time
import urllib.request

from image import ORDER, SIZE, SECTOR, GOOD_UFW_SHA, crc, prepare, preserve_live_vm, require, sha
from usbfs import LinuxTransport, enter_rom, open_target

LOADER_COMMIT = 'adb3f18889e88ac512ce0a3c4d8cc3d3cb30696a'
LOADER_SHA = 'ab1b77eb73385aaa78ab769253b2af7ef63d0ade46f391bd7424cc598a40267c'
LOADER_URL = ('https://raw.githubusercontent.com/kagaimiq/jl-uboot-tool/' +
              LOADER_COMMIT + '/data/loaderblobs/usb/br28loader.bin')


def save(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    fd = os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'wb') as f:
        f.write(data)


def save_json(path, report):
    save(path,(json.dumps(report,indent=2)+'\n').encode())


def read(t, address=0, length=SIZE):
    require(address % 512 == length % 512 == 0 and 0 <= address < address+length <= SIZE,
            'read outside chip')
    return b''.join(t.cmd(0xFD05,p.to_bytes(4,'big')+(512).to_bytes(2,'big'),size=512)
                    for p in range(address,address+length,512))


def load_ram(t, path):
    blob = Path(path).read_bytes()
    require(len(blob) == 23552 and sha(blob) == LOADER_SHA, 'BR28 loader digest mismatch')
    for p in range(0,len(blob),512):
        data = blob[p:p+512]
        args = (0x120000+p).to_bytes(4,'big')+len(data).to_bytes(2,'big')
        args += b'\0'+crc(data).to_bytes(2,'little')
        t.cmd(0xFB06,args,data=data)
    t.cmd(0xFB08,(0x120000).to_bytes(4,'big')+(1).to_bytes(2,'big'))
    require(t.cmd(0xFC0A)[2] == 3,'RAM loader did not report SPI NOR')
    print('Pinned BR28 RAM loader ready; no Flash write performed',flush=True)


def session(loader):
    # A freshly entered ROM is required. An already-running RAM loader has the
    # same INQUIRY name; see the documented reconnect procedure between runs.
    blob = Path(loader).read_bytes()
    require(len(blob) == 23552 and sha(blob) == LOADER_SHA,'BR28 loader digest mismatch')
    enter_rom()  # bounded: capability reads, then at most one mode-switch
    for _ in range(100):
        found = open_target(0x4c4a,0x3442)
        if found:
            os.close(found[0])
            break
        time.sleep(.05)
    else:
        raise RuntimeError('BR28 ROM did not enumerate')
    t = RestoreTransport()
    try:
        inquiry = t.inquiry()
        require(inquiry[8:16].strip() == b'BR28' and inquiry[16:32].strip() == b'UBOOT1.00',
                'unexpected ROM chip/model')
        load_ram(t,loader)
        return t
    except BaseException:
        t.close()
        raise


class RestoreTransport(LinuxTransport):
    writing = False
    target = None
    active_sector = None

    def execute(self, cdb, size=0, data=None):
        opcode = int.from_bytes(cdb[:2],'big')
        if opcode in (0xFB01,0xFB04):
            require(self.writing and self.target is not None,'Flash write gate closed')
            address = int.from_bytes(cdb[2:6],'big')
            require(self.active_sector in ORDER,'no planned active sector')
            if opcode == 0xFB01:
                require(address == self.active_sector and data is None,'erase outside active planned sector')
            else:
                require(address//SECTOR*SECTOR == self.active_sector and address%512 == 0,
                        'program outside active planned sector')
                require(data is not None and len(data) == 512,'program must be 512B')
                require(data == self.target[address:address+512],'program data differs from target')
                require(int.from_bytes(cdb[6:8],'big') == 512 and
                        int.from_bytes(cdb[9:11],'little') == crc(data),'program length/CRC mismatch')
            return self.exchange(cdb,size,data)
        return super().execute(cdb,size,data)


def backup(args):
    work = Path(args.workdir)
    require(not work.exists(),'use a NEW work directory; do not overwrite backups')
    work.mkdir(parents=True,mode=0o700)
    t = session(args.loader)
    try:
        first = read(t)
        save(work/'before-pass1.bin',first)
        second = read(t)
        save(work/'before-pass2.bin',second)
        require(first == second,'independent full Flash reads differ')
        report = {'status':'DOUBLE_BACKUP_VERIFIED','bytes':SIZE,'sha256':sha(first)}
        save_json(work/'backup.json',report)
        print(json.dumps(report),flush=True)
    finally:
        t.close()


def plan(args):
    work = Path(args.workdir)
    first, second = (work/'before-pass1.bin').read_bytes(), (work/'before-pass2.bin').read_bytes()
    require(first == second,'independent backups differ')
    target, report = prepare(first,Path(args.good_ufw).read_bytes())
    save(work/'target.bin',target)
    save_json(work/'plan.json',report)
    print(json.dumps(report,indent=2))


def restore(args):
    require(args.apply,'restore requires --apply after reviewing plan.json')
    work = Path(args.workdir)
    first = (work/'before-pass1.bin').read_bytes()
    require(first == (work/'before-pass2.bin').read_bytes(),'independent backups differ')
    target, expected = prepare(first,Path(args.good_ufw).read_bytes())
    require(target == (work/'target.bin').read_bytes(),'target file differs from regenerated plan')
    require(expected == json.loads((work/'plan.json').read_text()),'plan differs from regenerated checks')
    require(not (work/'restore.jsonl').exists(),'session already attempted; review its journal before any new action')
    print('Restoring tested ANC UFW SHA256 '+GOOD_UFW_SHA,flush=True)
    print('6 sectors; header last; bank A, VM and calibration retained',flush=True)
    fd = os.open(work/'restore.jsonl',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w') as log:
        def event(**row):
            line=json.dumps(row)
            log.write(line+'\n');log.flush();os.fsync(log.fileno())
            print(line,flush=True)
        t = session(args.loader)
        try:
            fresh = read(t)
            second = read(t)
            live_target = preserve_live_vm(first,target,fresh,second)
            save(work/'live-before.bin',fresh)
            save(work/'live-before-pass2.bin',second)
            save(work/'live-target.bin',live_target)
            event(stage='fresh_double_read_verified',before_sha256=sha(fresh),
                  target_sha256=sha(live_target),vm_preserved=True)
            t.target = live_target
            t.writing = True
            for address in ORDER:
                require(read(t,address,SECTOR) == fresh[address:address+SECTOR],
                        'sector changed before erase')
                t.active_sector = address
                event(stage='erase_sector',offset=address)
                t.cmd(0xFB01,address.to_bytes(4,'big'))
                require(read(t,address,SECTOR) == b'\xff'*SECTOR,'erase verification failed')
                for p in range(address,address+SECTOR,512):
                    data = live_target[p:p+512]
                    cargs = p.to_bytes(4,'big')+(512).to_bytes(2,'big')+b'\0'+crc(data).to_bytes(2,'little')
                    t.cmd(0xFB04,cargs,data=data)
                require(read(t,address,SECTOR) == live_target[address:address+SECTOR],
                        'sector program verification failed')
                event(stage='sector_verified',offset=address)
            t.writing = False
            final = read(t)
            save(work/'after.bin',final)
            require(final == live_target,'final full-chip readback mismatch')
            event(stage='FLASH_RESTORE_VERIFIED',bytes=SIZE,sha256=sha(final),
                  runtime_behavior_requires_separate_confirmation=True)
        finally:
            t.writing = False
            t.close()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='operation',required=True)
    sub.add_parser('probe',help='standard SCSI INQUIRY only; requires ROM USB identity')
    sub.add_parser('enter-rom',help='capability read then one USB recovery-mode request; no Flash write')
    q = sub.add_parser('fetch-loader',help='download the hash-pinned BR28 RAM loader')
    q.add_argument('--output',required=True)
    for name in ('backup','plan','restore'):
        q = sub.add_parser(name)
        q.add_argument('--workdir',required=True)
        if name != 'plan':
            q.add_argument('--loader',required=True)
        if name != 'backup':
            q.add_argument('--good-ufw',required=True)
        if name == 'restore':
            q.add_argument('--apply',action='store_true')
    args = p.parse_args()
    if args.operation == 'fetch-loader':
        with urllib.request.urlopen(LOADER_URL,timeout=30) as r:
            blob=r.read(23553)
        require(len(blob) == 23552 and sha(blob) == LOADER_SHA,'downloaded loader digest mismatch')
        save(args.output,blob)
        print('loader verified: '+LOADER_SHA)
    elif args.operation == 'enter-rom':
        enter_rom()
    elif args.operation == 'probe':
        t=LinuxTransport()
        try:
            t.inquiry()
        finally:
            t.close()
    else:
        {'backup':backup,'plan':plan,'restore':restore}[args.operation](args)


if __name__ == '__main__':
    main()
