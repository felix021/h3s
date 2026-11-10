#!/usr/bin/env python3
"""Fetch pinned reference SDK material into ignored cache, optionally decode IR.

Reference SDK != QCY source. Does not execute the vendor library or access devices.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

from artifacts import retrieve

ROOT=Path(__file__).resolve().parents[1]
CACHE=ROOT/'tools/reference-cache/701n-boot'


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--decode-ir',action='store_true',help='extract pinned objects and run local llvm-dis')
    p.add_argument('--hardware',action='store_true',help='also fetch pinned BR28 peripheral headers, driver IR and official bootloader references')
    p.add_argument('--llvm-dis',default='llvm-dis-16')
    p.add_argument('--output-dir',type=Path,default=ROOT/'analysis/boot-reference')
    args=p.parse_args()
    dependencies=json.loads((ROOT/'data/dependencies.json').read_text())
    sdk=dependencies['reference_sdk']
    base=sdk['url']+'/-/raw/'+sdk['commit']+'/'
    files=dict(dependencies['boot_reference_files'])
    objects=dict(dependencies['boot_reference_objects'])
    if args.hardware:
        files.update(dependencies['hardware_reference_files'])
        objects.update(dependencies['hardware_reference_objects'])
    for path,item in files.items():
        retrieve(base+path,CACHE/path,item['sha256'],item['bytes'])
        print(path,item['sha256'],flush=True)
    if args.hardware:
        boot=dependencies['official_bootloader_reference']
        for path,item in boot['files'].items():
            url='https://raw.githubusercontent.com/Jieli-Tech/fw-Bootloader/'+boot['commit']+'/'+path
            retrieve(url,CACHE/'official-bootloader'/path,item['sha256'],item['bytes'])
            print('official bootloader:',path,item['sha256'],flush=True)
    if not args.decode_ir:
        return
    args.output_dir.mkdir(parents=True,exist_ok=True)
    provenance={'reference_sdk_commit':sdk['commit'],'not_exact_qcy_source':True,'objects':{}}
    for name,item in objects.items():
        archive=CACHE/'SDK/cpu/br28/liba'/item['archive']
        data=subprocess.check_output(['ar','p',str(archive),name])
        digest=hashlib.sha256(data).hexdigest()
        if digest!=item['sha256']:
            raise ValueError('unexpected archive member: '+name)
        target=args.output_dir/name
        target.write_bytes(data)
        kind='LLVM bitcode' if data.startswith(b'BC\xc0\xde') else 'ELF' if data.startswith(b'\x7fELF') else 'unknown'
        provenance['objects'][name]={'sha256':digest,'bytes':len(data),'format':kind}
        if kind=='LLVM bitcode':
            subprocess.run([args.llvm_dis,str(target),'-o',str(target.with_suffix('.ll'))],check=True)
    (args.output_dir/'provenance.json').write_text(json.dumps(provenance,indent=2)+'\n')
    print('Reference objects and IR written to',args.output_dir)


if __name__=='__main__':
    main()
