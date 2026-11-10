#!/usr/bin/env python3
"""Typed handoff and bounded startup-memory replay, with explicit synthetic fields.

Does not implement ROM/PLL/reset, connect to hardware or generate firmware.
The cold-PLL path and real handoff values remain unresolved.
"""
import argparse
import binascii
import hashlib
import json
from pathlib import Path
import struct

from probe_boot import ENTRY, OFFICIAL_SHA, APP_SHA, SDK_PIN, application, explore
from probe_pi32v2 import ROOT, PIN, require, upstream
from verify_ufw_integrity import decode

HANDOFF=0x0019fd00  # harness allocation, NOT an observed loader pointer
HEADER=0x0019fd40   # harness allocation of the decoded package header
BOOT_INFO=0x00122800
BSS=(0x00125320,0x530c)
DATA=(0x00124840,0x060c3338,0xae0)
RAM_CODE=(0x0012a640,0x060c3e18,0x48d4)
SENTINEL_A=bytes.fromhex('571368240200000000015a5a')
SENTINEL_B=bytes.fromhex('ab89efcd020000000002a5a5')


def package_header(path):
    raw=path.read_bytes()
    require(hashlib.sha256(raw).hexdigest()==OFFICIAL_SHA,'requires exact official UFW')
    flash=raw[0x600:0x600+0xe6000]
    header=decode(flash[:32])
    require(binascii.crc_hqx(header[2:],0)==struct.unpack_from('<H',header)[0],'flash header CRC mismatch')
    # Same format interpretation as pinned jl-misctools: VID/PID stay plain.
    header=header[:4]+flash[4:8]+header[8:16]+flash[16:32]
    require(struct.unpack_from('<I',header,8)[0]==0x1ff000 and header[13]==16,
            'package flash-header fields changed')
    return header


def profile(header,sentinel=None):
    # BOOT_DEVICE_INFO: fs_info*, sfc_base_addr, app_addr, chip_id, trim, MAC+CRC.
    # Bank-A SFC mapping is a model choice consistent with documented package
    # coordinates. The pointer allocations and device fields are not measured.
    prefix=struct.pack('<III',HEADER,0x2020,0x06000000)
    return {'regions':[(HANDOFF,HANDOFF+24,'typed_boot_device_info'),
                       (BOOT_INFO,BOOT_INFO+40,'qcy_boot_info_destination'),
                       (BSS[0],BSS[0]+BSS[1],'qcy_bss'),
                       (DATA[0],DATA[0]+DATA[2],'qcy_initialized_data'),
                       (RAM_CODE[0],RAM_CODE[0]+RAM_CODE[2],'qcy_ram_code')],
        'readonly':[(HEADER,header)],'seeds':[(HANDOFF,prefix+(sentinel or b''))],
        'snapshots':[('boot_info',BOOT_INFO,40),('bss',*BSS),
                     ('data',DATA[0],DATA[2]),('ram_code',RAM_CODE[0],RAM_CODE[2])],
        'description':{'layout':'BOOT_DEVICE_INFO, matched to reference SDK boot.h and QCY copy routine',
            'r0_and_fs_info_addresses':'synthetic harness allocations',
            'sfc_mapping':'bank-A physical0x2020 -> runtime0x06000000 model',
            'flash_header_fields':'decoded exact official package; FlashSize is header value, not a capacity measurement',
            'chip_trim_mac_crc':'uninitialized' if sentinel is None else 'synthetic test sentinel '+sentinel.hex(),
            'pll0_bit0_set_is_hypothesis':True,'maskrom_executed':False}}


def check(report,app,header):
    a,b,c,d=report['scenarios']
    require(a['stop_pc']=='0x60664f0' and a['stop']['kind']=='uninitialized_memory'
            and a['stop']['address']==hex(HANDOFF+12),'partial handoff must stop at unknown chip_id')
    for result,sentinel in ((b,SENTINEL_A),(c,SENTINEL_B),(d,SENTINEL_A)):
        boot=result['memory_snapshots']['boot_info']['bytes_with_unknowns']
        require(boot[:2]==f'{header[13]:02x}' and boot[2:32]=='??'*15,'VM fields were incorrectly initialized')
        expected=struct.pack('<III',0x2020,0x06000000,0x1ff000)+sentinel
        require(boot[32:]==expected.hex(),'typed BOOT_INFO copy mismatch')
        require(result['instruction_adapters']['typed_handoff_byte_exact_store']==6,'wrong handoff store count')
    for result in (b,c):
        require(result['stop_pc']=='0x6000130' and result['stop']['kind']=='unsupported_runtime_instruction',
                'non-loop profile must stop before the first REP')
    require([r['pc'] for r in b['last_instructions']]==[r['pc'] for r in c['last_instructions']],
            'device sentinels changed the observed prefix control flow')
    require(d['stop_pc']=='0x6000182' and d['stop']['kind']=='unverified_zero_count_rep',
            'memory replay must stop at unverified REP count=0')
    require([s['completed_steps'] for s in report['scenarios']]==[19,30,30,16077],
            'known typed-handoff step counts changed')
    expected_hashes={'bss':hashlib.sha256(bytes(BSS[1])).hexdigest()}
    for label,(_,source,length) in (('data',DATA),('ram_code',RAM_CODE)):
        expected_hashes[label]=hashlib.sha256(app[source-ENTRY:source-ENTRY+length]).hexdigest()
    for label,digest in expected_hashes.items():
        snap=d['memory_snapshots'][label]
        require(snap.get('sha256')==digest and snap['initialized_bytes']==snap['size'],
                'startup memory image mismatch: '+label)
    require(d['instruction_adapters']['bounded_startup_rep_controller']==3,'wrong REP controller count')
    require(len(d['memory_events'])<=64 and len(d['last_instructions'])<=64,'unbounded public trace')
    report['known_handoff_regression_passed']=True
    report['device_sentinel_prefix_control_flow_equal']=True
    report['startup_memory_digests_match']=expected_hashes


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--fetch-simulator',action='store_true')
    p.add_argument('--firmware',type=Path,default=ROOT/'firmware/QCY_H3S_7018F_1T2_release_note_V1.1.8_011.ufw')
    p.add_argument('--report',type=Path)
    args=p.parse_args()
    app=application(args.firmware)
    header=package_header(args.firmware)
    module=upstream(args.fetch_simulator)
    cases=[('typed_known_fields_only',None,False),('typed_sentinel_A',SENTINEL_A,False),
           ('typed_sentinel_B',SENTINEL_B,False),('typed_memory_replay',SENTINEL_A,True)]
    scenarios=[explore(module,app,name=name,adapt_stack=True,pll=1,r0=HANDOFF,
                       handoff=profile(header,sentinel),startup_loops=loops)
               for name,sentinel,loops in cases]
    report={'format_version':1,'simulator_commit':PIN,'reference_sdk_commit':SDK_PIN,
        'firmware_sha256':OFFICIAL_SHA,'application_sha256':APP_SHA,
        'device_accessed':False,'firmware_created':False,'architectural_reset_simulated':False,
        'whole_h3s_boot_simulated':False,'suitable_for_flash_approval_alone':False,
        'scenarios':scenarios}
    check(report,app,header)
    text=json.dumps(report,indent=2)+'\n'
    if args.report:
        args.report.parent.mkdir(parents=True,exist_ok=True)
        args.report.write_text(text)
    for result in scenarios:
        print(result['name'],result['completed_steps'],'steps;',result['stop_pc'],result['stop'])
    if not args.report:
        print(text,end='')


if __name__=='__main__':
    main()
