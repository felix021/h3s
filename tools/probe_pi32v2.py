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
        # Firmware replay uses the real k2 effects; the runner's stack-PC
        # store and RETS masking are test-frame artifacts only.
        from br28_arch_models import architectural_k2
        c.models,_=architectural_k2(c.models)
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


# ---------------------------------------------------------------------------
# Route BW (sim-eqctrl): opt-in EQ control-plane event-injection scenarios.
#
# Explicit opt-in via --event-injection: it additionally requires the archived
# patch ELF, the offline fence-repair candidate and the official image. The
# legacy scenarios above stay byte-identical without this flag. These runs
# start REAL firmware functions (the D1-dispatch-reachable hook-site hosts of
# docs/eq-payload-semantics.md §2) from their entries with SYNTHETIC event /
# table / node inputs named in EVENT_INJECTION_FIXTURES below; RAM contents
# come from the image's own startup blobs (regions pinned by
# probe_boot_handoff), never invented. Every stubbed callee is listed in
# EVENT_INJECTION_STUBS. Nothing here is a device measurement, a flash
# approval, or a claim that the post/RTOS queue semantics are modeled.

OFFICIAL_UFW_SHA='65cafc7f9a7545c5c30701d141c77db334e37065589e42c51306132c94c879af'
STARTUP_DATA_REGION=(0x00124840,0x060c3338,0xae0)      # pinned by probe_boot_handoff
STARTUP_RAM_CODE_REGION=(0x0012a640,0x060c3e18,0x48d4)  # pinned by probe_boot_handoff
EVENT_INJECTION_FIXTURES={
    'version':'bw1',
    'classification':'synthetic-not-measured',
    'synthetic_event_word':0x10000|(10<<8)|4,   # key field 10 -> match key 7; subtype 4 -> gesture class 2
    'synthetic_keymap_entry':(7,2,9),           # (key,gesture,action) row in the BSS-resident key map
    'synthetic_mode_query_return':5,            # fn 0x6008170: 5 keeps the gesture class unflagged
    'synthetic_initial_source_byte':0x01,       # QCY[42]: custom curve already selected
    'synthetic_preset_index':7,                 # QCY[43] for the source-read matrix
    'synthetic_node_type':18,                   # fn 0x60057e6 branch guarding site 0x60059ce
    'synthetic_node_name':b'MusicEq\x00',       # image constant at 0x60a1bb8 (strncmp, 7)
    'synthetic_heap_block':0x00131400,          # one synthetic 1068B block for fn 0x607538a packing
}
# Explicit SYNTHETIC stubs (real callees depend on RTOS/ROM/file state the
# function-level harness does not model; the packed-message bytes themselves
# are produced by real instructions between the stubs).
EVENT_INJECTION_STUBS={
    0x6008170:'mode query -> synthetic 5 (from the if (r0!=5) branch shape)',
    0x6074192:'queue post boundary -> records args+message, synthetic success 0',
    0x12e16e:'RAM allocator -> one synthetic block (no OS heap)',
    0x12e190:'RAM free -> no-op (no OS heap)',
    0x6063610:'strncpy copy -> SYNTHETIC (upstream model lacks ld.w.pin word path); byte path cross-checked below',
    0x1fd74:'ROM memcpy -> pure byte copy',
    0x1fd84:'ROM strcmp variant -> byte compare to NUL',
    0x1fd8c:'ROM strncmp variant -> byte compare with r2 limit',
    0x1fd90:'ROM strncmp variant -> byte compare with r2 limit',
    0x6017958:'original key context API -> synthetic 1 (handled)',
    0x60140fc:'post-key API -> synthetic 1',
}


def official_app(path):
    raw=path.read_bytes()
    require(sha(raw)==OFFICIAL_UFW_SHA,'official image must be the exact published original UFW')
    row=decode(raw[64:144])
    offset,length=struct.unpack_from('<II',row,8)
    flash=raw[offset:offset+length]
    app_length=struct.unpack_from('<I',decode(flash[0x2000:0x2020]),8)[0]
    plain=sfc(flash[0x2020:0x2020+app_length])
    return plain[0x100:0x100+842654]


class _EventMemoryMixin:
    def _regions(self):
        raise NotImplementedError

    def valid(self,address,length,write=False):
        require(any(lo <= address and address+length <= hi and (not write or rw)
                    for lo,hi,rw in self._regions()),
                f'unsupported memory {"write" if write else "read"}: {address:#x}+{length}')


def run_event_injection(module,app,official,repaired_app,sections):
    """Three-image control-plane replay of the four D1-reachable EQ hook sites.

    Observes exactly what docs/eq-payload-semantics.md §4 lists as reachable
    without D2: per-site execution coverage, the actual post boundary of the
    toggle SENDs, the synchronous RESTORE call chain, the state-byte round
    trip, the &0x7F mask semantics and the type-10 guard.
    """
    fixtures=dict(EVENT_INJECTION_FIXTURES)
    Q42,Q43=QCY+42,QCY+43
    SITE_KEY,SITE_READ,SITE_PRESET,SITE_CUSTOM=0x6025364,0x60059ce,0x6025030,0x602510e
    HANDLER_KEY,FN_SOURCE,FN_PRESET_LOADER=0x60252c6,0x60057e6,0x6024fe6
    SENTINEL=0x00133ff0
    payload_at={s['addr']:s['data'] for s in sections}

    class Memory(_EventMemoryMixin,module.Memory):
        def __init__(self):
            super().__init__()
            self.regions=[]
        def map(self,address,data,writable=False):
            self.regions.append((address,address+len(data),writable))
            super().write(address,data)
        def _regions(self):
            return self.regions
        def read(self,address,length):
            self.valid(address,length)
            return super().read(address,length)
        def write(self,address,data):
            self.valid(address,len(data),write=True)
            super().write(address,data)

    class CPU(module.CPU):
        # The Flash execution fence (LIM_PC0 pair) governs flash fetches; RAM
        # code and ROM regions are constrained by their memory mappings only.
        execution_bounds=None
        def decode(self,pc):
            if self.execution_bounds and pc>=0x06000000:
                lo,hi=self.execution_bounds
                if not lo<=pc<hi:
                    raise PermissionError(f'Flash execution fence: {pc:#010x} outside {lo:#010x}..{hi:#010x}')
                insn,raw,size=super().decode(pc)
                require(pc+size<=hi,'instruction crosses execution boundary')
                return insn,raw,size
            return super().decode(pc)

    def machine(image,*,bounds):
        c=CPU()
        c.mem=Memory()
        c.mem.map(BASE,image)
        c.mem.map(0x00124000,bytes(0x11000),writable=True)
        # real startup contents: .data blob and RAM code copied verbatim from
        # the b79 application image (identical bytes in all three images here)
        data_src,code_src=STARTUP_DATA_REGION[1],STARTUP_RAM_CODE_REGION[1]
        c.mem.map(STARTUP_DATA_REGION[0],app[data_src-BASE:data_src-BASE+STARTUP_DATA_REGION[2]],writable=True)
        c.mem.map(STARTUP_RAM_CODE_REGION[0],app[code_src-BASE:code_src-BASE+STARTUP_RAM_CODE_REGION[2]],writable=True)
        if image is repaired_app:
            # hook-site rewrites and payload come from the audited candidate ELF
            for address,data in payload_at.items():
                c.mem.map(address,data)
        c.execution_bounds=bounds
        c.sp=0x00134000
        from br28_arch_models import architectural_k2
        c.models,_=architectural_k2(c.models)
        return c

    repaired_bounds=(BASE,0x060e3074+337)
    plain_bounds=(BASE,0x060c3338)

    def record_post(c,posts):
        msg=c.R[1]
        blob=c.mem.read(msg,88)
        posts.append({'post_args':[c.R[0],msg,c.R[2],c.R[3]],
                      'uuid16':blob[2] | blob[3]<<8,
                      'name':blob[4:20].split(b'\0')[0].decode('ascii','replace'),
                      'param':list(struct.unpack('<11I',blob[44:88]))})

    def stub_step(c,posts):
        """Apply one synthetic stub; returns True when pc was a stub address."""
        pc=c.pc
        if pc==0x6008170:
            c.R[0]=fixtures['synthetic_mode_query_return'];c.pc=c.rets;return True
        if pc==0x6074192:
            record_post(c,posts);c.R[0]=0;c.pc=c.rets;return True
        if pc==0x12e16e:
            c.R[0]=fixtures['synthetic_heap_block'] if c.R[0]==1068 else 0;c.pc=c.rets;return True
        if pc==0x12e190:
            c.pc=c.rets;return True
        if pc==0x1fd74:
            c.mem.write(c.R[0],c.mem.read(c.R[1],c.R[2]));c.R[0]=c.R[0];c.pc=c.rets;return True
        if pc in (0x1fd84,0x1fd8c,0x1fd90):
            limit=c.R[2] if pc!=0x1fd84 else 1<<16
            def text(addr):
                out=b'';k=0
                while k<limit:
                    byte=c.mem.read(addr+k,1)[0]
                    if not byte:break
                    out+=bytes([byte]);k+=1
                return out
            c.R[0]=0 if text(c.R[0])==text(c.R[1]) else 1;c.pc=c.rets;return True
        if pc==0x6063610:
            dst,src,n=c.R[0],c.R[1],c.R[2]
            data=bytearray(n);k=0
            while k<n:
                byte=c.mem.read(src+k,1)[0]
                if not byte:break
                data[k]=byte;k+=1
            c.mem.write(dst,bytes(data));c.R[0]=dst;c.pc=c.rets;return True
        if pc in (0x6017958,0x60140fc):
            c.R[0]=1;c.pc=c.rets;return True
        return False

    def execute(c,entry,args,*,budget,watch=()):
        """Run real instructions from entry until the return sentinel, a watch
        address records one event, or an honest boundary stops the run."""
        events=[];posts=[]
        c.R[:len(args)]=[a&0xFFFFFFFF for a in args]
        c.pc=entry;c.rets=SENTINEL
        seen=set()
        try:
            for _ in range(budget):
                if c.pc==SENTINEL:
                    break
                if c.pc in watch and c.pc not in seen:
                    seen.add(c.pc)
                    events.append({'pc':hex(c.pc),'kind':'watch',
                                   'registers':[c.R[i] for i in range(4)]})
                if not stub_step(c,posts):
                    c.step()
        except (PermissionError,RuntimeError,ValueError) as e:
            events.append({'kind':'stop','pc':hex(c.pc),'error':f'{type(e).__name__}: {e}'})
        return events,posts

    report={'fixtures':{k:(v.decode('ascii') if isinstance(v,bytes) else v) for k,v in fixtures.items()},
            'stubs':{hex(k):v for k,v in sorted(EVENT_INJECTION_STUBS.items())},
            'device_accessed':False,'firmware_created':False,'suitable_for_flash_approval_alone':False}
    scenarios={}

    # -- scenario 1: key handler entry -> site 0x6025364 -> toggle round trip --
    def double_click(c):
        c.mem.write(0x130000,struct.pack('<I',fixtures['synthetic_event_word']))
        c.mem.write(QCY+3404,bytes(fixtures['synthetic_keymap_entry']))
        return execute(c,HANDLER_KEY,[0x130000],budget=800,
                       watch=(SITE_KEY,0x60e3074,0x60e3128,0x607538a,0x6017958,0x60140fc,
                              0x602619a,0x6025118,0x602503a,SITE_PRESET,SITE_CUSTOM))

    images={'b79':(app,plain_bounds),'official':(official,plain_bounds),
            'repaired':(repaired_app,repaired_bounds)}
    baseline={}
    for name,(image,bounds) in images.items():
        c=machine(image,bounds=bounds)
        c.mem.write(Q42,bytes([fixtures['synthetic_initial_source_byte']]))
        events,posts=double_click(c)
        baseline[name]={'events':events,'posts':posts,
                        'state_byte':c.mem.read(Q42,1)[0],'handler_return':c.R[0],
                        'steps':c.steps}
    b79_events=[(e.get('pc'),e['registers'] if e['kind']=='watch' else e) for e in baseline['b79']['events']]
    official_events=[(e.get('pc'),e['registers'] if e['kind']=='watch' else e) for e in baseline['official']['events']]
    parity=b79_events==official_events and baseline['b79']['handler_return']==baseline['official']['handler_return']
    require(parity,'official and b79 key-handler replays diverged (sites are byte-identical)')
    for name in ('b79','official'):
        run=baseline[name]
        site_hit=[e for e in run['events'] if e.get('pc')==hex(SITE_KEY)]
        require(site_hit,'baseline image did not execute original site 0x6025364')
        require(not run['posts'] and run['state_byte']==fixtures['synthetic_initial_source_byte'],
                'baseline images must not touch the EQ state byte or post messages')
        api_seen=[e['pc'] for e in run['events'] if e.get('pc') in (hex(0x6017958),hex(0x60140fc))]
        require(api_seen==[hex(0x6017958),hex(0x60140fc)],
                f'{name}: original key-context API sequence changed: {api_seen}')
        run['api_sequence']=api_seen
    first=baseline['repaired']
    require(any(e.get('pc')==hex(SITE_KEY) for e in first['events']),'repaired image did not reach site 0x6025364')
    require(any(e.get('pc')==hex(0x60e3128) for e in first['events']),'repaired double-click did not enter eq_toggle')
    # BT three-state table row 1: 0x01 -> SEND(2,1)x2, no RESTORE, 0x81, return 1
    require(len(first['posts'])==2 and first['state_byte']==0x81,
            'toggle-on-first-click must post exactly two messages and set 0x81')
    for p in first['posts']:
        require(p['post_args']==[11,fixtures['synthetic_heap_block'],0x2000f,fixtures['synthetic_heap_block']+24]
                and p['uuid16']==0x737b and p['name'] in ('MusicEqBt','Eq0Media')
                and p['param']==[2,1]+[0]*9,
                f'toggle-on post boundary mismatch: {p}')
    require([p['name'] for p in first['posts']]==['MusicEqBt','Eq0Media'],'toggle-on node order changed')
    require(not any(e.get('pc')==hex(0x602619a) for e in first['events']),'RESTORE must not run when turning EQ off')
    require(first['handler_return']==1,'key handler must report handled (1) after toggle')

    # second double-click on the same machine state: 0x81 -> 0x01 round trip
    c=machine(repaired_app,bounds=repaired_bounds)
    c.mem.write(Q42,b'\x81')
    events,posts=double_click(c)
    restore_hit=[e for e in events if e.get('pc')==hex(0x602619a)]
    require(restore_hit and restore_hit[0]['registers'][:2]==[0,10],
            'second double-click must synchronously call RESTORE(0,10)')
    require(any(e.get('pc')==hex(0x6025118) for e in events),'RESTORE chain must reach fn 0x6025118')
    require(any(e.get('pc')==hex(0x602503a) for e in events),'RESTORE chain must reach fn 0x602503a')
    require(any(e.get('pc')==hex(SITE_CUSTOM) for e in events),'RESTORE chain must execute site 0x602510e')
    toggle_posts=[p for p in posts if p['param'][0]==2]
    require(len(toggle_posts)==2 and all(p['param'][1]==0 for p in toggle_posts),
            'toggle-off must post exactly two (2,0) messages')
    require(c.mem.read(Q42,1)[0]==0x01,'state byte must round-trip back to 0x01')
    scenarios['key_double_click_round_trip']={
        'three_image_parity_official_vs_b79':True,
        'baseline':{'site_executed':True,'posts':0,'state_byte_unchanged':True,
                    'api_sequence':[hex(0x6017958),hex(0x60140fc)]},
        'repaired':{'first_click':{'posts':first['posts'],'state_byte':'0x81','restore_called':False,'return':1},
                    'second_click':{'restore_args':[0,10],'chain':[hex(0x602619a),hex(0x6025118),hex(0x602503a),hex(SITE_CUSTOM)],
                                    'posts':toggle_posts,'final_state_byte':'0x01'}}}

    # -- scenario 2: type-10 spatial guard (BT table row 3) --
    c=machine(repaired_app,bounds=repaired_bounds)
    c.mem.write(Q42,b'\x01');c.mem.write(QCY+5356,b'\x0a')
    events,posts=double_click(c)
    require(any(e.get('pc')==hex(0x60e3128) for e in events),'toggle must be entered for the guard check')
    require(not posts and c.mem.read(Q42,1)[0]==0x01,
            'type-10 must write nothing and post nothing')
    scenarios['key_type10_guard']={'entered':True,'posts':0,'state_byte':'0x01','writes':0}

    # -- scenario 3: source-read site mask matrix (site 0x60059ce) --
    matrix={}
    for name,(image,bounds) in images.items():
        matrix[name]={}
        for initial in (0x01,0x02,0x81,0x82):
            c=machine(image,bounds=bounds)
            node=0x130800
            c.mem.write(node,b'\x00'*0x100)
            c.mem.write(node,fixtures['synthetic_node_name'])
            c.mem.write(Q42,bytes([initial]));c.mem.write(Q43,bytes([fixtures['synthetic_preset_index']]))
            events,_=execute(c,FN_SOURCE,[fixtures['synthetic_node_type'],node],budget=2000,
                             watch=(SITE_READ,0x60059d4,0x60059da,0x60059de,0x60059e6))
            site=[e for e in events if e.get('pc')==hex(SITE_READ)]
            require(site,'source-read site not reached')
            after=[e for e in events if e.get('pc')==hex(0x60059d4)]
            require(after,'source-read consumer branch not reached')
            observed=after[0]['registers'][1]
            stops=[e for e in events if e['kind']=='stop']
            # 0x60059d4 is a t=1/e=1 block: then r2=0, else r2=QCY[43]; the
            # store b[r8+1]=r2 at 0x60059de is unconditional, so the index is
            # really loaded only when the ELSE arm (0x60059da) executes.
            path='custom' if any(e.get('pc')==hex(0x60059e6) for e in events) else \
                 ('preset' if any(e.get('pc')==hex(0x60059da) for e in events) else 'default')
            index_loaded=any(e.get('pc')==hex(0x60059da) for e in events)
            matrix[name][f'{initial:#04x}']={'r1_after_site':observed,'path':path,
                                             'qcy43_loaded_to_node':index_loaded,
                                             'returned':not stops,'steps':c.steps}
    for initial in (0x01,0x02):
        base_state=f'{initial:#04x}'
        masked_state=f'{initial|0x80:#04x}'
        require(matrix['repaired'][masked_state]['r1_after_site']==initial
                and matrix['repaired'][masked_state]['path']==matrix['b79'][base_state]['path']
                and matrix['repaired'][masked_state]['qcy43_loaded_to_node']==matrix['b79'][base_state]['qcy43_loaded_to_node'],
                f'mask equivalence failed for {masked_state}')
        require(matrix['b79'][masked_state]['path']=='default' and not matrix['b79'][masked_state]['qcy43_loaded_to_node'],
                'baseline with bit7 set must fall to the default source path')
    require(matrix['b79']['0x81']['path']=='default','0x81 on b79 must take the default path')
    require(matrix['official']==matrix['b79'],'official source-read matrix diverged from b79')
    scenarios['source_read_mask_matrix']=matrix

    # -- scenario 4: preset site tail window (site 0x6025030 write semantics) --
    # fn 0x6024fe6's deep chain (config subsystem -> file reads) is NOT modeled;
    # the site tail 0x6025026..0x6025035 replays on real image bytes with a
    # synthetic stack frame, checking the b79 write vs the hook's bit7-keeping
    # record stub on the repaired image.
    tail={}
    for name,(image,bounds) in images.items():
        c=machine(image,bounds=bounds)
        initial=0x82 if name=='repaired' else 0x02
        c.mem.write(Q42,bytes([initial]))
        c.mem.write(Q43,b'\x00')
        c.sp=0x133f00
        # the tail executes 'sp += 16' before '{pc, r5, r4} = [sp++]': the
        # synthetic return frame (r4, r5, pc) sits at sp+16..sp+28
        for k,word in enumerate((0,0,SENTINEL)):
            c.mem.write(c.sp+16+4*k,struct.pack('<I',word))
        c.R[4]=fixtures['synthetic_preset_index']   # fn arg: the preset index
        events,_=execute(c,0x6025026,[QCY],budget=40,watch=(SITE_PRESET,))
        require(any(e.get('pc')==hex(SITE_PRESET) for e in events),
                f'preset site tail did not execute on {name}')
        require(c.mem.read(Q43,1)[0]==fixtures['synthetic_preset_index'],
                f'{name}: preset index write lost')
        result_byte=c.mem.read(Q42,1)[0]
        expected=0x02 if name!='repaired' else (initial&0x80)|0x02
        require(result_byte==expected,
                f'{name}: source byte after preset record = {result_byte:#04x}, expected {expected:#04x}')
        tail[name]={'initial':f'{initial:#04x}','result':f'{result_byte:#04x}',
                    'bit7_kept':bool(result_byte&0x80) if name=='repaired' else None}
    require(tail['repaired']['result']=='0x82','repaired preset record must keep bit7 (0x82)')
    require(tail['b79']['result']=='0x02' and tail['official']['result']=='0x02',
            'baseline preset record writes plain source=2')
    scenarios['preset_site_tail_window']={**tail,
        'note':'deep chain into the config subsystem is not modeled; tail window only'}

    # -- scenario 5: strncpy stub cross-check on the real unaligned path --
    c=machine(repaired_app,bounds=repaired_bounds)
    real=bytes(0x40)
    c.mem.write(0x130f00,real)
    c.R[:3]=[0x130f00,0x60e31b2,16]   # dst, music_name (unaligned -> byte path), 16
    c.pc=0x6063610;c.rets=SENTINEL
    for _ in range(200):
        if c.pc==SENTINEL:break
        c.step()
    require(c.pc==SENTINEL,'real strncpy execution did not return')
    real_result=c.mem.read(0x130f00,16)
    stub_result=bytearray(16)
    k=0
    while k<16:
        byte=c.mem.read(0x60e31b2+k,1)[0]
        if not byte:break
        stub_result[k]=byte;k+=1
    require(bytes(stub_result)==real_result,'strncpy stub disagrees with real unaligned execution')
    scenarios['strncpy_stub_crosscheck']={'real_equals_stub':True,
        'note':'real byte path (unaligned source) validates the SYNTHETIC stub; the word path model gap is documented'}

    report['scenarios']=scenarios
    report['honest_boundaries']=[
        'queue post return value is a synthetic success; real queue state and consumer wake-up are RTOS behavior (D2 observation)',
        'fn 0x6024fe6 deep chain (0x60750aa config subsystem) not modeled; preset site covered by its tail window',
        'fn 0x60057e6 entry scenario uses the r0=18 call form (the payload==53 producer of handler 0x6017e0c stays unpinned)',
        'site 0x6078846 is D2-only (effect-framework domain) and intentionally absent',
        'RAM startup contents are image .data/RAM-code blobs; all other RAM is synthetic-zero, not a measured boot state',
    ]
    report['effect_node_state_comparison']='deferred to the D2 first-audio scenario (docs/eq-payload-semantics.md §4)'
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--fetch-simulator',action='store_true')
    p.add_argument('--firmware',type=Path,default=ROOT/'firmware/H3S_0.1.1.8_ANC_ON_TRANSPARENT.ufw')
    p.add_argument('--patch-elf',type=Path,help='optional archived r2 ELF; never produces a flashable image')
    p.add_argument('--repaired-candidate',type=Path,help='distinct offline fence-repair UFW; independent audit required')
    p.add_argument('--patch-asm',type=Path,help='vendor patch disassembly for the independent repaired-candidate audit')
    p.add_argument('--official-ufw',type=Path,help='exact published official UFW for three-image event-injection scenarios')
    p.add_argument('--event-injection',action='store_true',
                   help='opt-in EQ control-plane event-injection scenarios; requires --patch-elf, --repaired-candidate, --patch-asm and --official-ufw')
    p.add_argument('--report',type=Path)
    args=p.parse_args()
    if args.repaired_candidate and (not args.patch_elf or not args.patch_asm):
        p.error('--repaired-candidate requires --patch-elf and --patch-asm')
    if args.event_injection and not (args.patch_elf and args.repaired_candidate and args.patch_asm and args.official_ufw):
        p.error('--event-injection requires --patch-elf, --repaired-candidate, --patch-asm and --official-ufw')
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
    if args.event_injection:
        exe=module.read_exec(args.patch_elf.read_bytes())
        sections=[s for s in exe['sections'] if s['flags']&2 and s.get('data')]
        result['event_injection']=run_event_injection(module,firmware_app(args.firmware),
            official_app(args.official_ufw),repaired_app,sections)
    text=json.dumps(result,indent=2)+'\n'
    if args.report:
        args.report.parent.mkdir(parents=True,exist_ok=True)
        args.report.write_text(text)
    print(text,end='')


if __name__ == '__main__':
    main()
