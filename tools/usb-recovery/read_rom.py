#!/usr/bin/env python3
"""Read the H3S CPU ROM window twice via a pinned RAM loader, never write Flash.

Requires a fresh, normal QCY USB connection on rooted 64-bit Linux/Android.
Raw images may contain vendor material: keep the output private/ignored.
The optional --p11-heap-probe mode replaces the ROM window with the fixed
five-address plan from docs/p11-heap-measurement.md; that page (not this tool)
governs the still-deferred device session.
The optional --session-pack mode merges the four D3 read-only probes
(C1 heap descriptor / C2 ADC RES / C3 P3_ANA_READ / C5 core-tick CNT-PRD)
into one fixed eight-address plan from docs/d3-session-pack.md; --dry-run
prints the exact command plan without touching any device.
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

# Optional --p11-heap-probe plan (docs/p11-heap-measurement.md): four positive
# controls (session-plan order, then the low-value LRCT control from the
# candidate table) and one heap-descriptor target. Every entry is exactly one
# 128B FD07 read through the pinned RAM loader, read twice, no retries.
P11_CODE_BYTES=12248
P11_CODE_SHA1='fe36008118168d2178c0cb3aeec265fc78e47ce70cd652e633236beb8270882'
P11_HEAP_PROBE_PLAN=(
    ('cpu1_entry_snapshot',0x19fef4,'control'),
    ('cpu1_ready_snapshot',0x1265f4,'control'),
    ('p11_code_head',0xf20080,'control'),
    ('lrct_num',0xfd3604,'control'),
    ('p11_heap_descriptor',0xf27f40,'target'),
)
P11_HEAP_PROBE_ADDRESSES={entry[1] for entry in P11_HEAP_PROBE_PLAN}

# Optional --session-pack plan (docs/d3-session-pack.md): the C1 five-address
# P11-heap plan unchanged (four controls + target), plus one 128B read each for
# C2 (JL_ADC CON+RES at 0xfd3100/0xfd3104), C3 (P3_ANA_READ at 0xf2c064) and
# C5 (core-tick CNT/PRD at 0xffe0f0/0xffe0f4). Every entry is exactly one 128B
# FD07 read through the pinned RAM loader, read twice, no retries.
SESSION_PACK_PLAN=(
    ('cpu1_entry_snapshot',0x19fef4,'control','C1'),
    ('cpu1_ready_snapshot',0x1265f4,'control','C1'),
    ('p11_code_head',0xf20080,'control','C1'),
    ('lrct_num',0xfd3604,'control','C1'),
    ('p11_heap_descriptor',0xf27f40,'target','C1'),
    ('adc_con_res',0xfd3100,'target','C2'),
    ('p3_ana_read',0xf2c064,'target','C3'),
    ('core_tick_cnt_prd',0xffe0f0,'target','C5'),
)
SESSION_PACK_ADDRESSES={entry[1] for entry in SESSION_PACK_PLAN}
# Retention positive controls whose match gates the C1 target verdict
# (docs/p11-heap-measurement.md decision criteria: all controls match, else
# the heap read is inconclusive - data recorded, never refitted).
SESSION_RETENTION_CONTROLS=('cpu1_entry_snapshot','cpu1_ready_snapshot','p11_code_head')
# Opcodes that would write/erase/program/jump/reset beyond the pinned entry
# sequence; a session-pack plan must contain none of them (zero-write rule).
FORBIDDEN_OPCODES=(0xfb00,0xfb01,0xfb02,0xfb04,0xfc0c,0xfc12,0xfd05)
FORBIDDEN_COMMAND_NAMES=('FB00','FB01','FB02','FB04','FC0C','FC12','FD05')


class ROMReadTransport(LinuxTransport):
    def __init__(self,loader,probe_addresses=None):
        require(len(loader)==23552 and hashlib.sha256(loader).hexdigest()==LOADER_SHA,
                'pinned BR28 RAM loader required')
        self.loader=loader
        self.loaded=0
        self.phase='fresh-rom'
        self.probe_addresses=probe_addresses
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
            if self.probe_addresses is None:
                require(length==size and 1<=size<=READ_BLOCK and 0<=address<address+size<=ROM_SIZE,
                        'read outside fixed ROM window or tested block size')
            else:
                require(length==size==READ_BLOCK and address in self.probe_addresses,
                        'read outside pinned probe allowlist or tested block size')
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


def open_session(transport_factory):
    """Fresh normal QCY USB, one mode switch, BR28/UBOOT1.00 identity, no loader yet."""
    existing_rom=open_target(0x4c4a,0x3442)
    if existing_rom:
        os.close(existing_rom[0])
        raise ValueError('fresh normal USB required; existing ROM/loader identity is ambiguous')
    normal=open_target(0x3654,0x4a55)
    require(normal is not None,'fresh normal QCY USB connection required; do not reuse a running loader')
    os.close(normal[0])
    enter_rom()
    for _ in range(100):
        found=open_target(0x4c4a,0x3442)
        if found:
            os.close(found[0]);break
        time.sleep(.05)
    else:
        raise RuntimeError('BR28 ROM did not enumerate')
    t=transport_factory()
    info=t.inquiry()
    require(info[8:16].strip()==b'BR28' and info[16:32].strip()==b'UBOOT1.00','unexpected ROM identity')
    return t


def capture(loader_path,work):
    loader=loader_path.read_bytes()
    require(len(loader)==23552 and hashlib.sha256(loader).hexdigest()==LOADER_SHA,'pinned BR28 RAM loader required')
    require(not work.exists(),'use a NEW private output directory')
    t=open_session(lambda:ROMReadTransport(loader))
    work.mkdir(parents=True,mode=0o700)
    try:
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


def probe_read_two(t,address):
    """One planned probe address: exactly two FD07 reads, byte-compared, no retry."""
    first=memory_read(t,address,READ_BLOCK)
    second=memory_read(t,address,READ_BLOCK)
    require(len(first)==len(second)==READ_BLOCK and first==second,
            'independent probe reads differ at '+hex(address))
    return first


def pinned_p11_head(path):
    blob=path.read_bytes()
    require(len(blob)==P11_CODE_BYTES and hashlib.sha1(blob).hexdigest()==P11_CODE_SHA1,
            'pinned p11_code.bin required for the P11 RAM positive control')
    return blob[:32]


def p11_probe_expectations(p11_head):
    """Offline-pinned expected bytes per planned read; None means report-only.

    Expected values are reported, never enforced: a mismatch is data recorded
    as-is, per the decision criteria in docs/p11-heap-measurement.md.
    """
    return {
        'cpu1_entry_snapshot':(4,bytes.fromhex('f4ca1200'),
            '0x19fef8 CPU1 entry word 0x12caf4 (three-way simulation evidence)'),
        'cpu1_ready_snapshot':(1,b'\x01',
            '0x1265f5 CPU1 ready flag 1 (stale post-boot value if RAM retains)'),
        'p11_code_head':None if p11_head is None else (0,p11_head,
            'p11_code.bin head 32B (pinned public UFW resource, SHA1 '+P11_CODE_SHA1+')'),
        'lrct_num':None,
        'p11_heap_descriptor':(0xa,bytes.fromhex('0c3df439'),
            'derived begin=0x3d0c size=0x39f4 (route X derivation; derived, not measured)'),
    }


def p11_probe_execute(t,work,expectations):
    """Run the fixed plan; write each raw block 0600 and build report entries."""
    reads=[]
    for name,address,role in P11_HEAP_PROBE_PLAN:
        block=probe_read_two(t,address)
        fd=os.open(work/f'p11-probe-{address:08x}-{name}.bin',
                   os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'wb') as out:
            out.write(block);out.flush();os.fsync(out.fileno())
        entry={'name':name,'address':address,'role':role,'bytes':len(block),
               'sha256':hashlib.sha256(block).hexdigest(),'two_pass_match':True}
        expected=expectations[name]
        if expected is None:
            entry['expected']={'checked':False,'enforced':False,
                'reason':'no offline-pinned expectation for this read'}
        else:
            offset,bytes_,source=expected
            actual=block[offset:offset+len(bytes_)]
            entry['expected']={'checked':True,'enforced':False,'offset':offset,
                'expected_hex':bytes_.hex(),'actual_hex':actual.hex(),
                'match':actual==bytes_,'source':source}
        reads.append(entry)
    return reads


def p11_heap_capture(loader_path,work,p11_code_path=None):
    loader=loader_path.read_bytes()
    require(len(loader)==23552 and hashlib.sha256(loader).hexdigest()==LOADER_SHA,'pinned BR28 RAM loader required')
    require(not work.exists(),'use a NEW private output directory')
    p11_head=None if p11_code_path is None else pinned_p11_head(p11_code_path)
    t=open_session(lambda:ROMReadTransport(loader,probe_addresses=P11_HEAP_PROBE_ADDRESSES))
    work.mkdir(parents=True,mode=0o700)
    try:
        load_ram(t,loader_path)
        reads=p11_probe_execute(t,work,p11_probe_expectations(p11_head))
        report={'status':'P11_HEAP_PROBE_READS_COMPLETE','plan':'--p11-heap-probe',
            'reads':reads,'memory_read_block':READ_BLOCK,
            'retry_policy':'none; first failure or two-read mismatch ends the session',
            'flash_erase_or_program_commands':0,'otp_write_commands':0,
            'comparisons_reported_not_enforced':True,'ram_loader_sha256':LOADER_SHA,
            'p11_code_reference':P11_CODE_SHA1 if p11_head is not None else 'not provided',
            'p11_ram_retention_not_established':True,
            'silicon_p11_heap_bytes_remain_derived':True,
            'raw_files_must_remain_private':True}
        save_json(work/'report.json',report)
        print(json.dumps(report),flush=True)
    finally:
        try:t.close()
        except OSError as e:
            if e.errno not in (errno.ENODEV,errno.ESHUTDOWN):
                raise
    print('Probe complete. Unplug USB and manually restart the headset; check playback and ANC key.',flush=True)


def session_pack_expectations(p11_head):
    """Offline-pinned checks per session-pack read; every expectation carries
    its derivation-chain source. Policies: 'exact' byte match, 'masked'
    single-byte mask match, 'domain' numeric range with named candidates,
    'record' = record-only read point (no derivation chain -> no verdict).

    Verdicts are reported, never enforced: a mismatch is data recorded as-is
    (three-branch decision criteria in docs/d3-session-pack.md, following
    docs/p11-heap-measurement.md).
    """
    p11_head_check=(None if p11_head is None else
        {'label':'p11_code_head_32B','offset':0,'width':32,'policy':'exact',
         'expected':p11_head.hex(),
         'source':'p11_code.bin head 32B (pinned public UFW resource, SHA1 '+P11_CODE_SHA1+')'})
    if p11_head_check is None:
        p11_head_check={'label':'p11_code_head_32B','offset':0,'width':32,'policy':'record',
            'expected':None,'source':'p11_code.bin reference not provided; '
            'P11 RAM retention control not checked (pass --p11-code to enable)'}
    return {
        'cpu1_entry_snapshot':[{'label':'cpu1_entry_word','offset':4,'width':4,'policy':'exact',
            'expected':'f4ca1200',
            'source':'0x19fef8 CPU1 entry word 0x12caf4 (three-way simulation evidence; '
                     'docs/p11-heap-measurement.md)'}],
        'cpu1_ready_snapshot':[{'label':'cpu1_ready_flag','offset':1,'width':1,'policy':'exact',
            'expected':'01',
            'source':'0x1265f5 CPU1 ready flag 1 (stale post-boot value if RAM retains)'}],
        'p11_code_head':[p11_head_check],
        'lrct_num':[{'label':'lrct_num_word','offset':0,'width':4,'policy':'record','expected':None,
            'source':'JL_LRCT NUM SFR-window observation point (app trim never ran in a loader '
                     'session, reset-state data expected; low diagnostic value, recorded only)'}],
        'p11_heap_descriptor':[{'label':'heap_begin_size','offset':0xa,'width':4,'policy':'exact',
            'expected':'0c3df439',
            'source':'derived begin=0x3d0c size=0x39f4 (route X three-way derivation; '
                     'derived, not measured)'}],
        'adc_con_res':[
            {'label':'adc_res_halfword','offset':4,'width':2,'policy':'domain','domain':(1,1023),
             'candidates':{400:'WBG04 (BU section 6 candidate)',
                           500:'VBAT_4 (BU section 6; battery gate 4*(500*K/400)>=3200mV '
                               'holds for K in [756,846])'},
             'source':'RES holds the last conversion, ~WBG04 at the boot-chain end (BU section 6 '
                      'assessment); 10-bit domain 1..1023; candidates 400/500 from the '
                      'BU section 6 table (docs/adc-res-consumer.md section 6)'},
            {'label':'adc_con_halfword','offset':0,'width':2,'policy':'record','expected':None,
             'source':'JL_ADC CON reset-state observation (the 0x46105d-family projection is a '
                      'running-world last-write value; loader session = reset world, recorded only)'}],
        'p3_ana_read':[
            {'label':'charge_detector_bits','offset':0,'width':1,'policy':'masked','mask':0x06,
             'expected':'06',
             'source':'bit1=LVCMP_DET (ldoin>vbat), bit2=LDO5V_DET (ldoin>0.6V = charging cable '
                      'present) per SDK charge_hw.h bit semantics + charge.c thresholds (route CB); '
                      'a USB session has VBUS ~5V present, so both bits are expected set - '
                      'complementary to the CB battery-only fixture 0b000'},
            {'label':'other_bits','offset':0,'width':4,'policy':'record','expected':None,
             'source':'remaining P3_ANA_READ bits incl. bit0 CHARGE_FULL_FLAG: no derivation chain, '
                      'recorded only (CB derived_mask=0b110 keeps the others explicitly non-derived)'}],
        'core_tick_cnt_prd':[
            {'label':'core_tick_cnt','offset':0,'width':4,'policy':'record','expected':None,
             'source':'core-tick CNT reset-value anchor (model starts con/cnt/prd=None -> strict '
                      'stop; no derivation chain exists for the reset word, recorded only)'},
            {'label':'core_tick_prd','offset':4,'width':4,'policy':'record','expected':None,
             'source':'core-tick PRD reset-value anchor (1199999 is the firmware-written running '
                      'value; reset and running values accounted separately - D3-SFR-02)'}],
    }


def session_pack_controls_status(blocks,expectations):
    """Retention-control gate for the C1 heap target (all-controls-match rule)."""
    statuses={}
    for name in SESSION_RETENTION_CONTROLS:
        check=expectations[name][0]
        if check['policy']=='record':
            statuses[name]='not_checked'
            continue
        block=blocks[name]
        actual=block[check['offset']:check['offset']+check['width']]
        statuses[name]='match' if actual==bytes.fromhex(check['expected']) else 'mismatch'
    if 'mismatch' in statuses.values():
        return 'failed',statuses
    if 'not_checked' in statuses.values():
        return 'incomplete',statuses
    return 'ok',statuses


def session_pack_verdict(check,block):
    """Three-branch suggestion for one check against its 128B block."""
    policy=check['policy']
    if policy=='record':
        return 'recorded_no_verdict'
    actual=block[check['offset']:check['offset']+check['width']]
    if policy=='domain':
        value=int.from_bytes(actual,'little')
        if not check['domain'][0]<=value<=check['domain'][1]:
            return 'inconclusive_not_retained_or_out_of_domain'
        if value in check.get('candidates',{}):
            return 'match_upgrade_measured'
        return 'in_domain_no_candidate_recorded_review'
    expected=bytes.fromhex(check['expected'])
    if policy=='exact':
        matched=actual==expected
    else:
        matched=(actual[0]&check['mask'])==(expected[0]&check['mask'])
    return 'match_upgrade_measured' if matched else 'mismatch_review_no_refit'


def session_pack_command_plan(loader):
    """The exact command sequence a --session-pack run would execute:
    entry sequence (INQUIRY, pinned FB06 upload x46, FB08, FC0A) then, per
    planned address, two FD07 128B reads. loader=None keeps FB06 CRCs as
    placeholders (dry-run without the private loader file)."""
    plan=[]
    def add(opcode,detail,phase):
        plan.append({'step':len(plan)+1,'opcode':opcode,'phase':phase,**detail})
    add('INQUIRY',{'cdb_hex':bytes([0x12,0,0,0,36,0]).hex(),'size':36},'fresh-rom')
    if loader is None:
        for block in range(46):
            add('FB06',{'address':0x120000+block*512,'length':512,'crc16':None,
                        'loader_block':block},'fresh-rom')
    else:
        for offset in range(0,len(loader),512):
            data=loader[offset:offset+512]
            add('FB06',{'address':0x120000+offset,'length':512,'crc16':crc(data),
                        'loader_block':offset//512},'fresh-rom')
    add('FB08',{'address':0x120000,'argument':1,'size':16},'fresh-rom')
    add('FC0A',{'size':16},'loader-started')
    for pass_id in (1,2):
        for name,address,role,probe in SESSION_PACK_PLAN:
            add('FD07',{'address':address,'length':READ_BLOCK,'size':READ_BLOCK,
                        'read':name,'role':role,'probe':probe,'pass':pass_id},'loader-ready')
    return plan


def session_pack_zero_write_assertions(plan):
    """Zero-write rule, checked against the plan itself: after the pinned
    entry sequence every command is an FD07 memory READ; no write/erase/
    program/OTP/jump/reset opcode appears anywhere."""
    totals={}
    for command in plan:
        totals[command['opcode']]=totals.get(command['opcode'],0)+1
    post_entry=[c for c in plan if c['phase']=='loader-ready']
    return {
        'command_total':len(plan),
        'opcode_counts':dict(sorted(totals.items())),
        'post_entry_all_fd07_reads':bool(post_entry) and all(c['opcode']=='FD07' for c in post_entry),
        'forbidden_opcode_count':sum(totals.get(name,0) for name in FORBIDDEN_COMMAND_NAMES),
        'flash_erase_or_program_commands':0,
        'otp_write_commands':0,
        'fd07_read_commands':totals.get('FD07',0),
    }


def session_pack_execute(t,work,expectations):
    """Run the fixed plan; write each raw block 0600 and build report entries
    with two-pass comparison and three-branch verdict suggestions."""
    blocks={}
    entries=[]
    for name,address,role,probe in SESSION_PACK_PLAN:
        block=probe_read_two(t,address)
        fd=os.open(work/f'session-pack-{address:08x}-{name}.bin',
                   os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'wb') as out:
            out.write(block);out.flush();os.fsync(out.fileno())
        blocks[name]=block
        entries.append({'name':name,'address':address,'role':role,'probe':probe,
            'bytes':len(block),'sha256':hashlib.sha256(block).hexdigest(),
            'two_pass_match':True,'checks':[]})
    heap_status,control_detail=session_pack_controls_status(blocks,expectations)
    for entry in entries:
        block=blocks[entry['name']]
        for check in expectations[entry['name']]:
            verdict=session_pack_verdict(check,block)
            actual=block[check['offset']:check['offset']+check['width']]
            note=None
            if entry['name']=='p11_heap_descriptor' and check['policy']!='record':
                if heap_status=='failed':
                    verdict,note='inconclusive_control_failed',(
                        'retention control mismatch - cannot distinguish wrong derivation '
                        'from a cleared window (docs/p11-heap-measurement.md decision criteria)')
                elif heap_status=='incomplete':
                    note=('retention controls incomplete (p11_code reference missing); '
                          'verdict is review-only')
            if check['policy']=='domain' and verdict=='match_upgrade_measured':
                note='candidate: '+check['candidates'][int.from_bytes(actual,'little')]
            entry['checks'].append({'label':check['label'],'offset':check['offset'],
                'width':check['width'],'policy':check['policy'],
                'expected':check.get('expected'),'mask':check.get('mask'),
                'domain':list(check['domain']) if check.get('domain') else None,
                'candidates':{str(k):v for k,v in check.get('candidates',{}).items()} or None,
                'source':check['source'],'actual_hex':actual.hex(),
                'verdict':verdict,'enforced':False,'note':note})
    return entries,heap_status,control_detail


def session_pack_report_base(loader_provided):
    loader_status=('pinned sha256 '+LOADER_SHA if loader_provided else
                   'not provided; FB06 crc16 are placeholders, live run requires the pinned loader')
    return {'plan':'--session-pack','memory_read_block':READ_BLOCK,
            'ram_loader_sha256':LOADER_SHA,'loader':loader_status,
            'retry_policy':'none; first failure or two-read mismatch ends the session',
            'three_branch_verdicts':'reported, never enforced (mismatch is data, not an error)',
            'session_not_authorized':True,
            'raw_files_must_remain_private':True}


def session_pack_capture(loader_path,work,p11_code_path=None):
    loader=loader_path.read_bytes()
    require(len(loader)==23552 and hashlib.sha256(loader).hexdigest()==LOADER_SHA,
            'pinned BR28 RAM loader required')
    require(not work.exists(),'use a NEW private output directory')
    p11_head=None if p11_code_path is None else pinned_p11_head(p11_code_path)
    t=open_session(lambda:ROMReadTransport(loader,probe_addresses=SESSION_PACK_ADDRESSES))
    work.mkdir(parents=True,mode=0o700)
    try:
        load_ram(t,loader_path)
        reads,heap_status,control_detail=session_pack_execute(
            t,work,session_pack_expectations(p11_head))
        report=session_pack_report_base(True)
        report.update({'status':'SESSION_PACK_READS_COMPLETE','dry_run':False,
            'device_accessed':True,'reads':reads,
            'heap_retention_controls':{'status':heap_status,'detail':control_detail},
            'command_plan':session_pack_command_plan(loader),
            'zero_write_assertions':session_pack_zero_write_assertions(
                session_pack_command_plan(loader))})
        save_json(work/'report.json',report)
        print(json.dumps(report),flush=True)
    finally:
        try:t.close()
        except OSError as e:
            if e.errno not in (errno.ENODEV,errno.ESHUTDOWN):
                raise
    print('Session pack complete. Unplug USB and manually restart the headset; '
          'check playback and ANC key.',flush=True)


def session_pack_dry_run(loader_path=None,p11_code_path=None):
    """Offline rehearsal deliverable: the exact command plan plus the pinned
    expectation table, without opening USB. No device is touched."""
    loader=None
    if loader_path is not None:
        blob=loader_path.read_bytes()
        require(len(blob)==23552 and hashlib.sha256(blob).hexdigest()==LOADER_SHA,
                'pinned BR28 RAM loader required')
        loader=blob
    p11_head=None if p11_code_path is None else pinned_p11_head(p11_code_path)
    expectations=session_pack_expectations(p11_head)
    plan=session_pack_command_plan(loader)
    reads=[]
    for name,address,role,probe in SESSION_PACK_PLAN:
        reads.append({'name':name,'address':address,'role':role,'probe':probe,
            'passes':2,'length':READ_BLOCK,
            'checks':[{**{k:c[k] for k in ('label','offset','width','policy','source')
                          if k in c},
                        'expected':c.get('expected'),
                        'mask':c.get('mask'),
                        'domain':list(c['domain']) if c.get('domain') else None,
                        'candidates':({str(k):v for k,v in c['candidates'].items()}
                                      if c.get('candidates') else None),
                        'verdict':'branch_pending_session'}
                       for c in expectations[name]]})
    report=session_pack_report_base(loader is not None)
    report.update({'status':'SESSION_PACK_DRY_RUN','dry_run':True,'device_accessed':False,
        'entry_sequence':{'inquiry':1,'fb06_blocks':46,'fb08':1,'fc0a':1},
        'reads':reads,'command_plan':plan,
        'zero_write_assertions':session_pack_zero_write_assertions(plan)})
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--loader',type=Path)
    p.add_argument('--workdir',type=Path)
    p.add_argument('--p11-heap-probe',action='store_true',
        help='read the fixed five-address P11 heap plan (docs/p11-heap-measurement.md) instead of the ROM window')
    p.add_argument('--session-pack',action='store_true',
        help='read the fixed eight-address D3 session pack (C1+C2+C3+C5, docs/d3-session-pack.md) instead of the ROM window')
    p.add_argument('--dry-run',action='store_true',
        help='with --session-pack: print the exact command plan and expectation table without touching any device')
    p.add_argument('--p11-code',type=Path,
        help='optional pinned p11_code.bin reference enabling the 0xf20080 head comparison')
    args=p.parse_args()
    require(not args.dry_run or args.session_pack,'--dry-run requires --session-pack')
    require(args.p11_heap_probe or args.session_pack or args.p11_code is None,
            '--p11-code requires --p11-heap-probe or --session-pack')
    if args.session_pack:
        if args.dry_run:
            print(json.dumps(session_pack_dry_run(args.loader,args.p11_code),indent=2))
        else:
            require(args.loader is not None and args.workdir is not None,
                    '--loader and --workdir are required (omit only with --dry-run)')
            session_pack_capture(args.loader,args.workdir,args.p11_code)
    elif args.p11_heap_probe:
        require(args.loader is not None and args.workdir is not None,
                '--loader and --workdir are required')
        p11_heap_capture(args.loader,args.workdir,args.p11_code)
    else:
        require(args.loader is not None and args.workdir is not None,
                '--loader and --workdir are required')
        capture(args.loader,args.workdir)


if __name__=='__main__':
    main()
