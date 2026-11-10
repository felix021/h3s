#!/usr/bin/env python3
"""Offline pi32v2 feasibility probe; NEVER opens a device or produces firmware.

Uses pi32v2-isa's instruction simulator, with an explicit H3S execution fence.
The upstream CPU targets AC791N; this is a function-level BR28 research harness,
not a complete H3S board, Bluetooth, DSP, timing or hardware exception model.
"""
import argparse
import builtins
import importlib
import importlib.util
import io
import hashlib
import json
from pathlib import Path
import struct
import subprocess
from unittest.mock import patch

from verify_ufw_integrity import decode, sfc

ROOT = Path(__file__).resolve().parents[1]
PIN = '3608ed19bc21324f8ecfe79cc0018d05e43d9563'
REPO = 'https://github.com/jvitkauskas/pi32v2-isa.git'
CACHE = ROOT / 'tools/reference-cache/pi32v2-isa'
GOOD_SHA = 'b79cb3a6f8702eb9e1d12eaf181a968f6b5848a66c051b2c792c39aa8b42fa1b'
BASE, QCY = 0x06000100, 0x001265c0
SITES = (0x060059ce,0x06025030,0x0602510e,0x06025364,0x06078846)
R2_ENTRY_BYTES = ('c6ea60eb0000','80ff7ae00b00','80ffaedf0b00',
                  'c5ea86ee0000','c3ea2b540000')
R2_PAYLOAD_SHA = 'f4e9501e515a7f74e7114633ba9bc165d1da9b399481530a78be8663dbe0e49a'


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def upstream(fetch=False):
    if not CACHE.exists():
        require(fetch,'missing simulator; run this command with --fetch-simulator')
        CACHE.parent.mkdir(parents=True,exist_ok=True)
        subprocess.run(['git','init',str(CACHE)],check=True,stdout=subprocess.DEVNULL)
        subprocess.run(['git','-C',str(CACHE),'remote','add','origin',REPO],check=True)
        subprocess.run(['git','-C',str(CACHE),'fetch','--depth','1','origin',PIN],check=True)
        subprocess.run(['git','-C',str(CACHE),'checkout','--detach','FETCH_HEAD'],check=True)
    revision=subprocess.check_output(['git','-C',str(CACHE),'rev-parse','HEAD'],text=True).strip()
    require(revision == PIN,'simulator revision mismatch')
    subprocess.run(['git','-C',str(CACHE),'diff','--exit-code',PIN,'--'],check=True,stdout=subprocess.DEVNULL)
    spec=importlib.util.spec_from_file_location('h3s_isa_sim',CACHE/'tools/llvm/sim.py')
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # The pinned upstream's m2.py refers to an absent developer-side ID file.
    # Supply only IDs actually present in its committed hardware captures,
    # excluding models provided elsewhere. Never replace instruction semantics.
    models={}
    for path in sorted((CACHE/'tools/hwtest/semantics').glob('*.py')):
        if path.stem.startswith('_') or path.stem == 'm2':
            continue
        model=importlib.import_module('semantics.'+path.stem)
        require(not models.keys() & model.MODELS.keys(),'duplicate upstream instruction model')
        models.update(model.MODELS)
    ids=set()
    for path in (CACHE/'hw/fm1-2026-10-24-v2').glob('*.jsonl'):
        for line in path.read_text().splitlines():
            record=json.loads(line)
            if record.get('id') and record['id'] not in models:
                ids.add(record['id'])
    require(bool(ids),'missing upstream hardware instruction records')
    import ast
    source=(CACHE/'tools/hwtest/semantics/m2.py').read_text()
    referenced={node.args[0].value for node in ast.walk(ast.parse(source))
                if isinstance(node,ast.Call) and isinstance(node.func,ast.Name)
                and node.func.id == 'open' and node.args
                and isinstance(node.args[0],ast.Constant) and isinstance(node.args[0].value,str)}
    require(len(referenced) == 1,'upstream missing-file adapter assumptions changed')
    real_open=builtins.open

    def hardware_ids(file,*args,**kwargs):
        if str(file) in referenced:
            return io.StringIO('\n'.join(sorted(ids)))
        return real_open(file,*args,**kwargs)

    with patch('builtins.open',hardware_ids):
        memory_models=importlib.import_module('semantics.m2').MODELS
    require(not models.keys() & memory_models.keys(),'duplicate memory instruction model')
    models.update(memory_models)
    module.check.load_models=lambda:dict(models)
    return module


def firmware_app(path):
    raw=path.read_bytes()
    require(sha(raw) == GOOD_SHA,'baseline must be the exact tested ANC UFW')
    row=decode(raw[64:144])
    offset,length=struct.unpack_from('<II',row,8)
    require((offset,length) == (0x600,0xe6000),'unexpected flash entry')
    flash=raw[offset:offset+length]
    app_length=struct.unpack_from('<I',decode(flash[0x2000:0x2020]),8)[0]
    plain=sfc(flash[0x2020:0x2020+app_length])
    return plain[0x100:0x100+842654]


def run_probe(module, app, elf=None, *, repaired_app=None,repaired_payload=None):
    class Memory(module.Memory):
        def __init__(self):
            super().__init__()
            self.regions=[]

        def map(self,address,data,writable=False):
            self.regions.append((address,address+len(data),writable))
            super().write(address,data)

        def valid(self,address,length,write=False):
            require(any(lo <= address and address+length <= hi and (not write or rw)
                        for lo,hi,rw in self.regions),
                    f'unsupported memory {"write" if write else "read"}: {address:#x}+{length}')

        def read(self,address,length):
            self.valid(address,length)
            return super().read(address,length)

        def write(self,address,data):
            self.valid(address,len(data),write=True)
            super().write(address,data)

    def cpu():
        c=CPU()
        c.mem=Memory()
        c.mem.map(BASE,app)
        c.mem.map(0x00124000,bytes(0x11000),writable=True)
        c.sp=0x00134000
        return c

    class CPU(module.CPU):
        execution_bounds=None

        def decode(self,pc):
            require(pc % 2 == 0,'unaligned instruction fetch')
            if self.execution_bounds:
                lo,hi=self.execution_bounds
                if not lo <= pc < hi:
                    raise PermissionError(f'Flash execution fence: {pc:#010x} outside {lo:#010x}..{hi:#010x}')
            insn,raw,size=super().decode(pc)
            require(not self.execution_bounds or pc+size <= self.execution_bounds[1],
                    'instruction crosses execution boundary')
            require(not insn.id.startswith(('rep','lock','idle','wfe','sev','swi','syscall',
                                           'btbclr','bkpt','hbkpt','cli','sti')),
                    'instruction needs an unimplemented H3S runtime model: '+insn.id)
            return insn,raw,size

    setup=cpu()
    setup.pc=0x06034e92
    for _ in range(2):
        setup.step()  # execute QCY's actual two limit-register argument loads
    low,high=setup.R[:2]
    require((low,high) == (0x06000100,0x060c3338),'QCY PC range loads changed')
    require(app[0x06034eac-BASE:0x06034eb0-BASE] == bytes.fromhex('00ef1000'),
            'QCY PC guard enable instruction changed')
    repaired_bounds=None
    if repaired_app is not None:
        require(elf is not None and len(repaired_app)==len(app),'repair requires archived ELF and full application')
        repaired_setup=cpu();repaired_setup.mem.map(BASE,repaired_app);repaired_setup.pc=0x06034e92
        for _ in range(2):repaired_setup.step()
        repaired_bounds=tuple(repaired_setup.R[:2])
        require(repaired_bounds==(low,0x060e3074+337),'unexpected repaired execution range')
        require(repaired_app[0x34eac-0x100:0x34eb0-0x100]==app[0x34eac-0x100:0x34eb0-0x100],
                'repaired candidate changed guard enable')

    def guarded_step(c,override=False):
        # Enforce at decode too, including the second slot of a parallel pair
        # and instruction lookahead inside upstream's control-flow machinery.
        c.execution_bounds=repaired_bounds if override and repaired_bounds else None if override else (low,high)
        c.step()

    def must_reject(action,error):
        try:
            action()
        except error:
            return
        raise RuntimeError('negative harness check unexpectedly succeeded')

    checks=cpu()
    must_reject(lambda:checks.mem.read(0xffe218,4),ValueError)
    must_reject(lambda:checks.mem.write(BASE,b'\0'),ValueError)
    must_reject(lambda:checks.decode(BASE+1),ValueError)
    checks.execution_bounds=(low,high)
    must_reject(lambda:checks.decode(high),PermissionError)
    checks.execution_bounds=(0x06034e92,0x06034e94)
    must_reject(lambda:checks.decode(0x06034e92),ValueError)

    original=cpu()
    original.mem.write(QCY+42,b'\x02')
    original.R[6]=QCY
    original.pc=SITES[0]
    for _ in range(2):
        guarded_step(original)
    require(original.pc == SITES[0]+6 and original.R[:2] == [0,2],
            'original QCY source-read instructions did not replay correctly')
    report={'simulator_commit':PIN,'baseline_ufw_sha256':GOOD_SHA,
        'device_accessed':False,'firmware_created':False,
        'pc_limits_extracted_by_executing_qcy_loads':[hex(low),hex(high)],
        'original_source_read_machine_code_passed':True,
        'harness_negative_checks_passed':5,
        'policy':'H3S-derived explicit instruction-fetch fence; not hardware exception emulation',
        'whole_h3s_boot_simulated':False,'suitable_for_flash_approval_alone':False}
    vectors=[]
    for site,raw in zip(SITES,R2_ENTRY_BYTES):
        c=cpu()
        c.mem.map(site,bytes.fromhex(raw))
        c.pc=site
        guarded_step(c)
        target=c.pc
        try:
            guarded_step(c)
        except PermissionError:
            vectors.append({'site':hex(site),'target':hex(target),'result':'BLOCKED'})
        else:
            raise RuntimeError('r2 branch regression vector was not blocked')
    report['archived_r2_branch_vectors']=vectors
    report['archived_r2_verdict']='REJECTED'
    if elf is None:
        return report
    exe=module.read_exec(elf)
    sections=[s for s in exe['sections'] if s['flags'] & 2 and s.get('data')]
    require({s['addr'] for s in sections} == set(SITES)|{0x060e3074},
            'probe only accepts the archived r2 section layout')
    require(all(not s['flags'] & 1 and s['type'] == 1 for s in sections),'unexpected writable section')
    require(all(len(s['data']) == 6 for s in sections if s['addr'] in SITES),'unexpected hook size')
    payload=next(s['data'] for s in sections if s['addr'] == 0x060e3074)
    require(len(payload) == 337 and sha(payload) == R2_PAYLOAD_SHA,'not the archived r2 payload')
    for site,raw in zip(SITES,R2_ENTRY_BYTES):
        require(next(s['data'] for s in sections if s['addr'] == site) == bytes.fromhex(raw),
                'ELF hook does not match archived r2 branch vector')
    if repaired_app is not None:
        require(repaired_payload==payload,'candidate payload differs from executed ELF')
        for section in sections:
            if section['addr'] in SITES:
                offset=section['addr']-BASE
                require(repaired_app[offset:offset+len(section['data'])]==section['data'],
                        'candidate hook differs from executed ELF')
    symbols={name:value for name,value,*_ in exe['symbols'] if name}

    def patched_cpu():
        c=cpu()
        if repaired_app is not None:c.mem.map(BASE,repaired_app)
        for s in sections:
            c.mem.map(s['addr'],s['data'])
        return c

    blocked=[]
    for site in SITES:
        c=patched_cpu()
        c.pc=site
        guarded_step(c)  # execute the actual linked 4/6B jump or call
        target=c.pc
        try:
            guarded_step(c)
        except PermissionError as e:
            blocked.append({'site':hex(site),'target':hex(target),'result':'BLOCKED','reason':str(e)})
        else:
            raise RuntimeError('archived r2 unexpectedly passed the H3S execution fence')
    # Separate counterfactual diagnostics: permissions are explicitly overridden
    # here ONLY to check simple ABI/byte-store behaviour of real compiled code.
    # These results can never override the guarded rejection above.
    cases=0
    for symbol,source in [('eq_record_preset_source',2),('eq_record_custom_source',1)]:
        for initial in (1,2,129,130):
            c=patched_cpu()
            c.R=[0x10000000+i for i in range(16)]
            saved=list(c.R[4:])
            c.mem.write(QCY+42,bytes([initial]))
            c.pc=symbols[symbol]
            c.rets=0x00133ff0
            for _ in range(100):
                if c.pc == 0x00133ff0:
                    break
                guarded_step(c,override=True)
            else:
                raise RuntimeError('function did not return within instruction budget')
            require(c.mem.read(QCY+42,1) == bytes([(initial&128)|source]),'source-byte result mismatch')
            require(c.R[4:] == saved and c.sp == 0x00134000,'callee-saved registers/stack changed')
            cases+=1
    def diagnostic_call(c,symbol,args):
        c.R[:len(args)]=args
        c.pc=symbols[symbol]
        c.rets=0x00133ff0
        saved=list(c.R[4:])
        start_sp=c.sp
        calls=[]
        for _ in range(2000):
            if c.pc == 0x00133ff0:
                break
            if c.pc in (0x0607538a,0x0602619a):
                if c.pc == 0x0607538a:
                    text=bytearray()
                    for offset in range(16):
                        char=c.mem.read(c.R[1]+offset,1)
                        if char == b'\0':
                            break
                        text.extend(char)
                    else:
                        raise ValueError('unterminated EQ node name')
                    name=text.decode('ascii')
                    require(c.R[0] == 0x737b and c.R[3] == 44,'wrong EQ API UUID/ABI length')
                    command=struct.unpack('<11I',c.mem.read(c.R[2],44))
                    calls.append((name,command))
                else:
                    require(c.R[:2] == [0,10],'wrong audio-mode restore arguments')
                    calls.append(('restore',None))
                # These are explicit API STUBS, with hostile caller-saved
                # clobbers. No DSP or RTOS implementation is being emulated.
                c.pc=c.rets
                c.R[:4]=[0,0x12345678,0xabcdef12,0x90807060]
                continue
            guarded_step(c,override=True)
        else:
            raise RuntimeError('compiled function exceeded instruction budget')
        require(c.R[4:] == saved and c.sp == start_sp,'callee-saved registers/stack changed')
        return c.R[0],calls

    toggle=patched_cpu()
    toggle.R=[0x10000000+i for i in range(16)]
    toggle.mem.write(QCY+42,b'\x01')
    toggle.mem.write(QCY+5356,b'\x00')
    for result,flag in ((1,1),(2,0)):
        value,calls=diagnostic_call(toggle,'eq_toggle',[])
        expected=[(name,(2,flag)+(0,)*9) for name in ('MusicEqBt','Eq0Media')]
        if flag == 0:
            expected.append(('restore',None))
        require(value == result and calls == expected,'compiled EQ toggle API trace mismatch')
        require(toggle.mem.read(QCY+42,1) == bytes([1|(flag<<7)]),'compiled toggle state mismatch')
    toggle.mem.write(QCY+5356,b'\x0a')
    value,calls=diagnostic_call(toggle,'eq_toggle',[])
    require(value == 0 and calls == [],'spatial exclusion failed')
    open_cases=0
    for name,source,bypass in [('MusicEqBt',129,0),('Eq0Media',129,0),
                               ('EscoDlEq',129,0),('MusicEqBt',1,1)]:
        c=patched_cpu()
        context,config=0x0012a000,0x0012a100
        c.mem.write(context,name.encode()+b'\0')
        c.mem.write(config+4,bytes([bypass]))
        c.mem.write(QCY+42,bytes([source]))
        _,calls=diagnostic_call(c,'eq_open_config',[context,config])
        expected=1 if source&128 and name in ('MusicEqBt','Eq0Media') else bypass
        require(c.mem.read(context+35,1) == bytes([expected]) and not calls,'compiled node-open behaviour mismatch')
        open_cases+=1
    report.update(patch_elf_sha256=sha(elf),guarded_patch_entries=blocked,
        archived_r2_verdict='REJECTED',permission_override_diagnostics={
            'real_compiled_source_function_cases':cases,'register_and_stack_checks_passed':True,
            'real_compiled_toggle_cases_with_api_stubs':3,'real_compiled_node_open_cases':open_cases,
            'dsp_rtos_calls_are_explicit_stubs':True,
            'permission_override_for_diagnostics_only':True,'not_a_safe_firmware_verdict':True})
    if repaired_bounds:
        diagnostic=report.pop('permission_override_diagnostics')
        diagnostic['permission_override_for_diagnostics_only']=False
        diagnostic['execution_bounds_extracted_from_candidate']=[hex(x) for x in repaired_bounds]
        diagnostic['endpoint_policy']='half-open full-instruction fetch check; exact silicon endpoint/low bits unverified'
        positive=[]
        for site in SITES:
            c=patched_cpu();c.pc=site;c.R[0]=QCY;c.R[6]=QCY
            guarded_step(c,override=True);target=c.pc;guarded_step(c,override=True)
            require(0x060e3074<=target<repaired_bounds[1],'repair hook did not enter payload')
            positive.append({'site':hex(site),'target':hex(target),'result':'EXECUTED_WITH_CANDIDATE_FENCE'})
        c=patched_cpu();c.execution_bounds=repaired_bounds
        must_reject(lambda:c.decode(repaired_bounds[1]+1),PermissionError)
        report['repaired_fence_function_diagnostics']=diagnostic
        report['repaired_candidate_hook_entries']=positive
        report['repaired_candidate_upper_bound_still_rejects']=True
        report['repaired_candidate_whole_boot_validated']=False
        report['repaired_application_sha256']=sha(repaired_app)
        report['actual_candidate_payload_and_hooks_match_executed_elf']=True
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--fetch-simulator',action='store_true')
    p.add_argument('--firmware',type=Path,default=ROOT/'firmware/H3S_0.1.1.8_ANC_ON_TRANSPARENT.ufw')
    p.add_argument('--patch-elf',type=Path,help='optional archived r2 ELF; never produces a flashable image')
    p.add_argument('--repaired-candidate',type=Path,help='distinct offline fence-repair UFW; independent audit required')
    p.add_argument('--patch-asm',type=Path,help='vendor patch disassembly for the independent repaired-candidate audit')
    p.add_argument('--report',type=Path)
    args=p.parse_args()
    if args.repaired_candidate and (not args.patch_elf or not args.patch_asm):
        p.error('--repaired-candidate requires --patch-elf and --patch-asm')
    module=upstream(args.fetch_simulator)
    repaired_app=repaired_payload=None
    if args.repaired_candidate:
        from verify_eq_candidate import audit,load_image
        audit(args.firmware,args.repaired_candidate,args.patch_asm,repaired_fence=True)
        plain=load_image(args.repaired_candidate)[2]
        repaired_app=plain[0x100:0x100+842654];repaired_payload=plain[0xe3074:]
    result=run_probe(module,firmware_app(args.firmware),args.patch_elf.read_bytes() if args.patch_elf else None,
                     repaired_app=repaired_app,repaired_payload=repaired_payload)
    if args.repaired_candidate:result['repaired_candidate_sha256']=sha(args.repaired_candidate.read_bytes())
    text=json.dumps(result,indent=2)+'\n'
    if args.report:
        args.report.parent.mkdir(parents=True,exist_ok=True)
        args.report.write_text(text)
    print(text,end='')


if __name__ == '__main__':
    main()
