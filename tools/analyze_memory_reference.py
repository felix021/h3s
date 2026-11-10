#!/usr/bin/env python3
"""Extract pinned memory/P11 IR functions locally; never execute vendor code."""
import argparse
import hashlib
import json
from pathlib import Path
import shlex
import subprocess

ROOT=Path(__file__).resolve().parents[1]


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output-dir',type=Path,default=ROOT/'analysis/memory-reference')
    p.add_argument('--llvm-config',default='llvm-config-16')
    p.add_argument('--cxx',default='g++')
    args=p.parse_args();args.output_dir.mkdir(parents=True,exist_ok=True)
    dependencies=json.loads((ROOT/'data/dependencies.json').read_text())
    binary=args.output_dir/'llvm_ir_function'
    flags=shlex.split(subprocess.check_output([args.llvm_config,'--cxxflags','--ldflags','--libs','bitreader','core'],text=True))
    subprocess.run([args.cxx,str(ROOT/'tools/llvm_ir_function.cpp'),*flags,'-o',str(binary)],check=True)
    report={'sdk_commit':dependencies['reference_sdk']['commit'],'not_exact_qcy_source':True,
            'device_accessed':False,'objects':{},'function_ir_sha256':{}}
    for name,item in dependencies['memory_reference_objects'].items():
        archive=ROOT/'tools/reference-cache/701n-boot/SDK/cpu/br28/liba'/item['archive']
        archive_spec=dependencies['boot_reference_files']['SDK/cpu/br28/liba/'+item['archive']]
        if hashlib.sha256(archive.read_bytes()).hexdigest()!=archive_spec['sha256']:
            raise ValueError('archive digest mismatch')
        data=subprocess.check_output(['ar','p',str(archive),name]);digest=hashlib.sha256(data).hexdigest()
        if digest!=item['sha256']:raise ValueError('object digest mismatch')
        target=args.output_dir/name;target.write_bytes(data)
        report['objects'][name]={'sha256':digest,'bytes':len(data)}
        for function in item['functions']:
            ir=subprocess.check_output([str(binary),str(target),function])
            if b'\nentry:' not in ir or len(ir)<100:raise ValueError('empty function body')
            (args.output_dir/(function+'.ll')).write_bytes(ir)
            report['function_ir_sha256'][function]=hashlib.sha256(ir).hexdigest()
    (args.output_dir/'provenance.json').write_text(json.dumps(report,indent=2)+'\n')
    print('Pinned memory/P11 functions extracted; vendor objects and IR remain in the output directory.')


if __name__=='__main__':main()
