#!/usr/bin/env python3
"""Build/audit/replay a distinct EQ fence-repair candidate, entirely offline."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import struct
import sys
import tempfile

from probe_pi32v2 import ROOT, firmware_app, require, run_probe, upstream
from verify_eq_candidate import audit, load_image
from verify_ufw_integrity import crc, decode, sfc, verify


class MemoryImage:
    def __init__(self,raw):self.raw=bytes(raw)
    def read_bytes(self):return self.raw
    def __str__(self):return 'in-memory CRC-valid negative candidate'


def crc_valid_mutant(candidate,kind):
    raw,flash,plain=load_image(candidate);raw,flash=bytearray(raw),bytearray(flash)
    boot=bytearray(decode(flash[0x2000:0x2020]))
    if kind=='boot_reserved':boot[6]^=1
    elif kind=='payload_name':
        plain=bytearray(plain)
        require(plain[0xe31bc:0xe31c5]==b'Eq0Media\0','payload test location changed')
        plain[0xe31bc]=ord('X');start=0xe3053
        struct.pack_into('<H',plain,start+2,crc(plain[start+32:]))
        struct.pack_into('<H',plain,start,crc(plain[start+2:start+32]))
        encrypted=sfc(plain);flash[0x2020:0x2020+len(plain)]=encrypted
        struct.pack_into('<HH',boot,2,crc(plain),crc(encrypted))
    else:raise ValueError('unknown mutation')
    struct.pack_into('<H',boot,0,crc(boot[2:]));flash[0x2000:0x2020]=decode(boot)
    raw[0x600:0xe6600]=flash
    row=bytearray(decode(raw[64:144]));struct.pack_into('<H',row,4,crc(flash));raw[64:144]=decode(row)
    header=bytearray(decode(raw[:64]));count=struct.unpack_from('<H',header,8)[0]
    struct.pack_into('<H',header,2,crc(raw[64:64+count*80]));struct.pack_into('<H',header,0,crc(header[2:]))
    raw[:64]=decode(header);result=MemoryImage(raw)
    require(verify(result)['passed'],'negative sample must have valid CRCs')
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--patch-elf',type=Path,required=True)
    p.add_argument('--patch-asm',type=Path,required=True)
    p.add_argument('--rejected-r2',type=Path,help='optional exact historical failed UFW, for negative audit')
    p.add_argument('--report',type=Path,required=True);args=p.parse_args()
    baseline=ROOT/'firmware/H3S_0.1.1.8_ANC_ON_TRANSPARENT.ufw'
    command=[sys.executable,str(ROOT/'tools/build_quiet_candidate.py'),'--attenuation-db','10',
             '--anc-cycle-two-state','--eq-toggle-elf',str(args.patch_elf.resolve())]
    with tempfile.TemporaryDirectory(prefix='h3s-eq-repair-') as temporary:
        root=Path(temporary)
        rejected=subprocess.run([*command,'--output-dir',str(root/'default')],cwd=ROOT,capture_output=True,text=True)
        require(rejected.returncode!=0 and 'REJECTED: EQ payload exceeds' in rejected.stderr,
                'default builder no longer rejects unsafe r2 layout')
        require(not (root/'default').exists(),'rejected build created output')
        built=subprocess.run([*command,'--eq-code-fence-repair','--output-dir',str(root/'repair')],
                             cwd=ROOT,capture_output=True,text=True)
        require(built.returncode==0,'repair build failed: '+built.stderr[-200:])
        candidate,=list((root/'repair').glob('*.ufw'))
        audited=audit(baseline,candidate,args.patch_asm,repaired_fence=True)
        for kind,reason in [('boot_reserved','Unrelated boot-info fields changed'),
                            ('payload_name','exact archived337B compiled payload')]:
            mutant=crc_valid_mutant(candidate,kind)
            try:audit(baseline,mutant,args.patch_asm,repaired_fence=True)
            except ValueError as e:require(reason in str(e),'wrong CRC-valid mutant rejection')
            else:raise ValueError('CRC-valid unsafe mutation passed scope audit')
        try:audit(baseline,candidate,args.patch_asm)
        except ValueError as e:require('Unexpected plaintext changes' in str(e),'unexpected default audit rejection')
        else:raise ValueError('repair silently accepted under original policy')
        plain=load_image(candidate)[2];app=plain[0x100:0x100+842654]
        executed=run_probe(upstream(),firmware_app(baseline),args.patch_elf.read_bytes(),repaired_app=app,
                           repaired_payload=plain[0xe3074:])
    report={'device_accessed':False,'whole_h3s_boot_simulated':False,'suitable_for_flash_approval_alone':False,
            'default_builder_rejects_r2':True,'default_auditor_does_not_silently_accept_repair':True,
            'crc_valid_unrelated_boot_info_and_payload_mutants_rejected':True,
            'audit':audited,'execution':executed}
    if args.rejected_r2:
        require(hashlib.sha256(args.rejected_r2.read_bytes()).hexdigest()==
                'c8dae5233678102aac152f0ff94952edea83c6eeca6afaf216db43226d0b6696','not exact rejected r2')
        try:audit(baseline,args.rejected_r2,args.patch_asm,repaired_fence=True)
        except ValueError as e:require('Repaired fence does not end exactly' in str(e),'wrong r2 rejection reason')
        else:raise ValueError('historical r2 passed repaired-candidate audit')
        report['historical_r2_rejected_by_repair_policy']=True
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(report,indent=2)+'\n')
    print('Offline repair build, independent audit,5 guarded entries and15 compiled function cases passed; whole boot pending.')


if __name__=='__main__':main()
