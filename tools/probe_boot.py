#!/usr/bin/env python3
"""Bounded H3S application-entry exploration. No device access or firmware output.

Runs five separate scenarios, never continuing a failed CPU. Unknown reads and
fetches stop; two diagnostic PLL values and one R0 pointer are hypotheses only.
This is application handoff exploration, not architectural reset or boot proof.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import struct

from probe_pi32v2 import ROOT, PIN, require, upstream
from verify_ufw_integrity import decode, sfc
from br28_unknown import UnknownWord, word
from br28_icfg import IcfgValue, M as WORD_MASK
from br28_arch_models import architectural_k2, K2_SHA

OFFICIAL_SHA='65cafc7f9a7545c5c30701d141c77db334e37065589e42c51306132c94c879af'
APP_SHA='18f3d573a5b1a15f488a6803ce4f828634e602015c85e92d205bbe62d7060041'
ENTRY=0x06000100
PLL=0x00fd4600
BOOT_ARGS=0x0019fc28
STACK_TOP=0x00123840
SDK_PIN='67328f4741c69993c88e504c67377b196c724f97'
ROM_SHA='7cae0a02e3ab3755c1e392f03c89222dd701065ec9bd3ec8ac109a7b3f40f793'
ROM_RETURN=0x01c70000  # simulator sentinel, never fetched as real code


def check_rom(rom):
    require(len(rom)==0x20000 and hashlib.sha256(rom).hexdigest()==ROM_SHA,
            'requires exact privately acquired 128KiB ROM window')


class Blocked(RuntimeError):
    def __init__(self,kind,**details):
        self.details={'kind':kind,**details}
        super().__init__(json.dumps(self.details,sort_keys=True))


class IrqDeliveryRequest(RuntimeError):
    """Eligible software IRQ asking the machine for the (opt-in) CPU entry."""
    def __init__(self,**details):
        self.details=details
        super().__init__(json.dumps(self.details,sort_keys=True))


def application(path):
    raw=path.read_bytes()
    require(hashlib.sha256(raw).hexdigest()==OFFICIAL_SHA,'requires exact official 0.1.1.8 UFW')
    row=decode(raw[64:144])
    offset,length=struct.unpack_from('<II',row,8)
    require((offset,length)==(0x600,0xe6000),'unexpected flash entry')
    flash=raw[offset:offset+length]
    app_length=struct.unpack_from('<I',decode(flash[0x2000:0x2020]),8)[0]
    plain=sfc(flash[0x2020:0x2020+app_length])
    app=plain[0x100:0x100+842654]
    require(hashlib.sha256(app).hexdigest()==APP_SHA,'unexpected application bytes')
    return app


def flash_image(path):
    """0x06000000 起的完整 sfc 明文(默认只映射 ENTRY=0x06000100 起的 app 切片)。

    头 0x100 字节是 flash 资源分区表(app_area_head 等),app 切片之后是资源区
    (ANCIF 等)。固件启动中段的资源表扫描(如 0x6010b14 rep 4 32 从 0x06000000
    拷 32 字节)会读这两段:都是官方 UFW 的已知真实字节,按需作为 handoff 只读
    区传入,不改变默认映射。
    """
    raw=path.read_bytes()
    require(hashlib.sha256(raw).hexdigest()==OFFICIAL_SHA,'requires exact official 0.1.1.8 UFW')
    row=decode(raw[64:144])
    offset,length=struct.unpack_from('<II',row,8)
    require((offset,length)==(0x600,0xe6000),'unexpected flash entry')
    flash=raw[offset:offset+length]
    app_length=struct.unpack_from('<I',decode(flash[0x2000:0x2020]),8)[0]
    return sfc(flash[0x2020:0x2020+app_length])


def explore(module,app,*,name,adapt_stack=False,adapt_store=False,pll=None,r0=0,budget=100_000,
            handoff=None,startup_loops=False,diagnostics=None,rom=None,hardware=None,machine=None):
    if rom is not None:
        check_rom(rom)
    events=[]
    adapters=Counter()
    rom_steps=Counter()
    event_counts=Counter()
    milestones=[]
    use_architectural_models=bool(adapt_stack or adapt_store or diagnostics or machine is not None)
    architectural_model_names=set()
    if machine is not None:
        from br28_machine import CoreStall
        from br28_model import GpioOutValue
        require(hardware is not None,'machine requires a peripheral model; board frontend separately pins ROM')
    else:
        CoreStall=()
        GpioOutValue=()

    def event(row):
        if machine is not None:row={'core':hardware.current_core,**row}
        events.append(row)
        event_counts[row['access']]+=1
        if handoff and len(events)>64:
            events.pop(32)  # first32 + last32; never publish a RAM/code mirror

    class Memory:
        # Only the observed bootstrap stack and 24B handoff object are writable.
        # Allocated RAM starts UNINITIALIZED, not as a successful zero read.
        regions=((0x00123000,STACK_TOP,'bootstrap_stack'),
                 (BOOT_ARGS,BOOT_ARGS+24,'hypothesized_boot_argument_object'))+tuple(handoff['regions'] if handoff else ())
        operation='read'

        def __init__(self):
            self.ram={}
            self.unknown_origins={}
            self.icfg_spills={}

        def region(self,address,size):
            for low,high,kind in self.regions:
                if low<=address and address+size<=high:
                    return kind
            return None

        def read(self,address,size):
            require(size>0 and 0<=address<=0xffffffff and address+size<=0x100000000,
                    'invalid memory request')
            if hardware is not None and hardware.mmu is not None:
                address=hardware.mmu.translate(address,size,self.read)
            if hardware is not None and hardware.sfc is not None:hardware.sfc.check_flash_access(address)
            if ENTRY<=address and address+size<=ENTRY+len(app):
                return app[address-ENTRY:address-ENTRY+size]
            if rom is not None and 0<=address and address+size<=len(rom):
                return rom[address:address+size]
            for base,data in handoff['readonly'] if handoff else ():
                if base<=address and address+size<=base+len(data):
                    return data[address-base:address-base+size]
            detail={'access':self.operation,'address':hex(address),'size':size}
            if hardware is not None and self.operation!='fetch' and hardware.handles(address,size):
                return struct.pack('<I',hardware.read(address))
            if hardware is not None and self.operation!='fetch':
                # Byte-granular explicit fixtures (P2M heap info); without a
                # fixture this returns None and the ordinary strict path applies.
                data=hardware.read_bytes(address,size)
                if data is not None:return data
            if self.operation!='fetch' and address==PLL and size==4 and pll is not None:
                event({**detail,'hypothesis':'PLL0_CON0','value':hex(pll)})
                return struct.pack('<I',pll)
            region=self.region(address,size)
            if region:
                missing=next((a for a in range(address,address+size) if a not in self.ram),None)
                if missing is not None:
                    raise Blocked('uninitialized_memory',**detail,region=region,first_missing=hex(missing))
                return bytes(self.ram[a] for a in range(address,address+size))
            if self.operation=='fetch':
                raise Blocked('unmapped_instruction_fetch',**detail,
                              reference_sdk_symbol='_pll_init' if address==0x1fe14 else None)
            raise Blocked('unmapped_memory',**detail,
                          reference_sdk_register='JL_PLL0.CON0' if address==PLL else None)

        def write(self,address,data):
            if hardware is not None and hardware.mmu is not None:
                address=hardware.mmu.translate(address,len(data),self.read)
            if rom is not None and address<len(rom) and address+len(data)>0:
                raise Blocked('unmapped_or_readonly_write',access='write',address=hex(address),size=len(data))
            if hardware is not None and hardware.handles(address,len(data)):
                hardware.write(address,int.from_bytes(data,'little'))
                return
            region=self.region(address,len(data))
            if not region:
                details={'value_bytes':data.hex()} if diagnostics else {}
                raise Blocked('unmapped_or_readonly_write',access='write',address=hex(address),size=len(data),**details)
            for base,value in list(self.icfg_spills.items()):
                low,high=max(base,address),min(base+4,address+len(data))
                if low<high:
                    updated=value.overlay_bytes(low-base,data[low-address:high-address])
                    if updated.known==WORD_MASK:del self.icfg_spills[base]
                    else:self.icfg_spills[base]=updated
            self.ram.update((address+i,byte) for i,byte in enumerate(data))
            for a in range(address,address+len(data)):self.unknown_origins.pop(a,None)
            if machine is not None:machine.note_memory_write(address,data)
            event({'access':'write','address':hex(address),'bytes':data.hex(),'region':region})

        def abstract_word(self,address,*,task_frame=False):
            # Only task-frame loads may introduce new unknowns. A later stack
            # reload must have provenance from an explicit abstract store.
            physical=hardware.mmu.translate(address,4,self.read) if hardware is not None and hardware.mmu is not None else address
            if ((rom is not None and 0<=physical and physical+4<=len(rom)) or
                    ENTRY<=physical and physical+4<=ENTRY+len(app) or
                    any(base<=physical and physical+4<=base+len(data) for base,data in (handoff['readonly'] if handoff else ())) or
                    hardware is not None and hardware.handles(physical,4)):
                return int.from_bytes(self.read(address,4),'little')
            if ((rom is not None and physical<len(rom) and physical+4>0) or
                    physical<ENTRY+len(app) and physical+4>ENTRY or
                    any(physical<base+len(data) and physical+4>base for base,data in (handoff['readonly'] if handoff else ()))):
                raise Blocked('abstract_read_crosses_readonly_boundary',address=hex(physical),size=4)
            if not self.region(physical,4):return int.from_bytes(self.read(address,4),'little')
            if physical in self.icfg_spills:return self.icfg_spills[physical]
            values=[self.ram.get(physical+i) for i in range(4)]
            if all(v is not None for v in values):return int.from_bytes(bytes(values),'little')
            for i,v in enumerate(values):
                if v is None and not task_frame and physical+i not in self.unknown_origins:
                    self.read(address,4)  # preserve the ordinary strict failure
            origins=[self.unknown_origins.get(physical+i,physical+i) if v is None else None for i,v in enumerate(values)]
            return UnknownWord(values,origins)

        def write_abstract_word(self,address,value):
            if isinstance(value,int):self.write(address,struct.pack('<I',value&0xffffffff));return
            require(isinstance(value,(UnknownWord,IcfgValue)),'unsupported abstract memory value')
            physical=self.preflight_abstract_word(address,value);region=self.region(physical,4)
            if isinstance(value,IcfgValue):
                self.icfg_spills[physical]=value
                for i in range(4):
                    a=physical+i;mask=255<<(8*i)
                    if value.known&mask==mask:self.ram[a]=(value.value>>(8*i))&255
                    else:self.ram.pop(a,None)
                    self.unknown_origins.pop(a,None)
                event({'access':'abstract_write','address':hex(physical),'size':4,'region':region,
                       'known_mask':hex(value.known),'known_value':hex(value.value)})
                return
            self.icfg_spills.pop(physical,None)
            for i,v in enumerate(value.bytes):
                a=physical+i
                if v is None:self.ram.pop(a,None);self.unknown_origins[a]=value.origins[i]
                else:self.ram[a]=v;self.unknown_origins.pop(a,None)
            event({'access':'abstract_write','address':hex(physical),'size':4,'region':region,
                   'bytes_with_unknowns':''.join('??' if v is None else f'{v:02x}' for v in value.bytes)})

        def preflight_abstract_word(self,address,value):
            require(isinstance(value,(int,UnknownWord,IcfgValue)),'unsupported abstract stack source')
            physical=self.abstract_write_target(address)
            if isinstance(value,IcfgValue) and physical%4:
                raise Blocked('partial_icfg_spill_unaligned',address=hex(physical))
            if isinstance(value,UnknownWord) and any(physical<base+4 and physical+4>base and physical!=base
                                                    for base in self.icfg_spills):
                raise Blocked('mixed_abstract_spill_unaligned',address=hex(physical))
            return physical

        def abstract_write_target(self,address):
            physical=hardware.mmu.translate(address,4,self.read) if hardware is not None and hardware.mmu is not None else address
            protected=((rom is not None and physical<len(rom) and physical+4>0) or
                physical<ENTRY+len(app) and physical+4>ENTRY or
                any(physical<base+len(data) and physical+4>base for base,data in (handoff['readonly'] if handoff else ())) or
                hardware is not None and hardware.handles(physical,4))
            if protected:raise Blocked('abstract_store_requires_ram',address=hex(physical),size=4)
            if not self.region(physical,4):raise Blocked('abstract_store_requires_ram',address=hex(physical),size=4)
            return physical

    class CPU(module.CPU):
        core_id=0
        def __init__(self):
            super().__init__()
            if use_architectural_models:
                self.models,names=architectural_k2(self.models)
                architectural_model_names.update(names)
        usp=None
        reti=None
        sp_known=True
        bootstrap_context=False
        bootstrap_icfg_pending=False
        task_frame_load=None
        task_frame_restore=None
        task_gpr_frame=None
        first_task_return_frame=None
        ssp=0
        repeat=None
        opaque_source=None
        mutable_decode_bytes=None

        def check_opaque(self,insn,raw):
            if not any(x is None for x in self.R):return
            require(self.R[0] is None and all(x is not None for x in self.R[1:]),'unsupported opaque register')
            if self.opaque_source=='pwm_dead_read':
                allowed={0x06034a84:('add.ri.u5',{'r':4,'n':1}),
                         0x06034a86:('bne.ri',{'r':4,'i':4,'n':503}),
                         0x06034a78:('mov.rr',{'x':0,'y':4}),
                         0x06034a8a:('pop.pc_rl',{'l':5}),
                         0x060006e2:('add.ri.u5',{'r':5,'n':8}),
                         0x060006e4:('blt.rr',{'r':5,'s':6,'n':507}),
                         0x060006de:('ld.w',{'d':0,'b':5,'n':1})}
                require(diagnostics and diagnostics.get('opaque_pwm_read') and
                        allowed.get(self.pc)==(insn.id,raw),'opaque PWM value would be consumed')
                if self.pc==0x060006e4:require(self.R[5]<self.R[6],'opaque PWM return lacks proven next callback')
            elif self.opaque_source=='cpu1_bootstrap_icfg':
                require(machine is not None and self.core_id==1 and self.pc==0x12cad8 and
                        insn.id=='and.nmodimm.rri' and raw=={'r':0,'s':0,'n':3904},
                        'opaque bootstrap ICFG consumed outside exact gate mask')
            elif self.opaque_source=='p33_pin0_return':
                allowed={0x06067462:('pop.pc_rl',{'l':8}),
                         0x06035306:('call.xl',{'n':105658}),
                         0x06068c80:('push.rets_rl',{'l':7}),
                         0x06068c82:('li.u32',{'r':2,'n':15902216}),
                         0x06068c88:('rmw.and.nmodimm',{'r':2,'n':8,'m':16}),
                         0x06068c8c:('li.s16',{'r':0,'n':16351})}
                require(diagnostics and diagnostics.get('opaque_pin_read') and
                        allowed.get(self.pc)==(insn.id,raw),'opaque pin0 return would be consumed')
            else:
                require(diagnostics and diagnostics.get('opaque_pin_read') and
                        ((self.pc==0x06067462 and insn.id=='pop.pc_rl' and raw=={'l':8}) or
                         (self.pc==0x0603502c and insn.id=='li.s16' and raw=={'r':0,'n':848})),
                        'opaque register would be consumed outside verified dead-value path')

        def word_store(self,insn,raw,abstract=False):
            op=module.check.operands(insn,raw)
            if insn.id=='st.w.modimm':address=self.R[op['r']]+op['n'];value=op['m']
            elif insn.id=='st.w.idxs':address=self.R[op['r']]+(self.R[op['s']]<<2);value=self.R[op['t']]
            elif insn.id=='st.w.idx':address=self.R[op['r']]+self.R[op['s']];value=self.R[op['t']]
            elif insn.id=='st.w.l':address=self.R[op['r']]+op['n'];value=self.R[op['s']]
            elif insn.id=='st.w':address=self.R[op['b']]+op['n'];value=self.R[op['d']]
            elif insn.id=='st.w.sp':address=self.sp+op['n'];value=self.R[op['r']]
            else:raise ValueError('unsupported exact word store')
            return address&0xffffffff,value if abstract and isinstance(value,IcfgValue) else value&0xffffffff

        def finish_exact(self,end):
            self.seq=end;self.pc=end;self.steps+=1
            if self.after_then is not None:
                self.after_then[0]-=1;self.check_then()

        def effect(self,insn,raw,pc,size,R):
            model=self.models.get(insn.id)
            if use_architectural_models and getattr(model,'__module__',None)=='semantics.v4':
                raise Blocked('unsupported_runner_context_model',instruction_id=insn.id)
            if use_architectural_models and insn.id=='mov.r_sr':
                # Direct architectural reads are handled in step_body. A
                # parallel half must not fall back to measured ROM constants.
                raise Blocked('special_register_effect_requires_explicit_state',register=raw['s'])
            result=super().effect(insn,raw,pc,size,R)
            # Models can transport a source by returning it unchanged. Reject
            # unsupported abstract outputs before upstream apply mutates any
            # register/memory; explicit copy/stack adapters handle valid moves.
            values=result[0]
            for group in ('regs','mem'):
                for key,value in list(values.get(group,{}).items()):
                    if isinstance(value,IcfgValue):values[group][key]=value.concrete()
            for key in ('flags','sp','rets','exit'):
                if isinstance(values.get(key),IcfgValue):values[key]=values[key].concrete()
            for value in (*values.get('regs',{}).values(),*values.get('mem',{}).values(),
                          *(values.get(k) for k in ('flags','sp','rets','exit'))):
                if isinstance(value,(UnknownWord,IcfgValue)):value.consumed()
            return result

        def decode(self,pc):
            if pc%2:
                raise Blocked('unaligned_instruction_fetch',address=hex(pc))
            if hardware is not None and hardware.sfc is not None:hardware.sfc.check_flash_access(pc)
            old=self.mem.operation
            self.mem.operation='fetch'
            try:
                # RAM and virtual mappings may change. Reuse a decode only
                # after checking its actual bytes through the current mapping.
                if not ((rom is not None and 0<=pc<len(rom)) or 0x06000000<=pc<0x08000000):
                    if self.mutable_decode_bytes is None:self.mutable_decode_bytes={}
                    if pc in self.cache:
                        actual=self.mem.read(pc,self.cache[pc][2])
                        if self.mutable_decode_bytes.get(pc)==actual:return self.cache[pc]
                        self.cache.pop(pc)
                    result=super().decode(pc)
                    self.mutable_decode_bytes[pc]=self.mem.read(pc,result[2])
                    return result
                if rom is not None and len(rom)-6<pc<len(rom):
                    # Upstream asks for6B even for a2B/4B instruction. Decode
                    # available tail bytes without reading/padding beyond ROM.
                    if pc not in self.cache:
                        got=module.codec.decode(self.tab,self.mem.read(pc,len(rom)-pc))
                        if got is None:
                            raise Blocked('truncated_rom_instruction_fetch',address=hex(pc))
                        self.cache[pc]=got
                    return self.cache[pc]
                return super().decode(pc)
            finally:
                self.mem.operation=old

        def step(self):
            before_pc=self.pc
            before_sp=self.sp
            helper_return=bool(self.repeat and self.repeat.get('allow_helper_return') and self.pc==0x0012cb2a)
            self.step_body()
            if isinstance(self.pc,IcfgValue):self.pc=self.pc.concrete()
            if machine is not None and self.sp!=before_sp:machine.note_sp_write(self)
            if machine is not None and before_pc==0x06066120:
                self.task_frame_load=(self.R[4],self.R[1])
            if rom is not None and 0<=before_pc<len(rom):
                rom_steps['completed_model_steps_in_rom']+=1
            if diagnostics and before_pc in (0x06000192,0x06000f08,0x060108bc):
                kind={0x06000192:'startup_icfg_mask_applied',0x06000f08:'software_crc_result',
                      0x060108bc:'update_result_get_return'}[before_pc]
                milestones.append({**({'core':self.core_id} if machine is not None else {}),'kind':kind,'pc':hex(before_pc),
                                   'icfg':hex(self.icfg) if machine is None or not machine.partial_icfg or self.icfg_known else 'unknown',
                                   **({'icfg_known_mask':hex(self.icfg_state.known)} if machine is not None and machine.partial_icfg else {}),
                                   'r0':word(self.R[0]),'sp':hex(self.sp)})
            if diagnostics and before_pc in diagnostics.get('watch_pcs',()):
                milestone={'kind':'watched_pc_executed','pc':hex(before_pc),'r0':word(self.R[0])}
                if diagnostics.get('watch_registers'):
                    milestone['registers']={f'r{i}':word(self.R[i])
                                             for i in diagnostics['watch_registers']}
                if machine is not None:milestone['core']=self.core_id
                milestones.append(milestone)
            if diagnostics and before_pc in diagnostics.get('watch_ram_after_pc',{}):
                spans=diagnostics['watch_ram_after_pc'][before_pc]
                require(len(spans)<=4,'too many watched RAM spans')
                for label,address,length in spans:
                    require(1<=length<=32 and self.mem.region(address,length),'watched span must be bounded physical harness RAM')
                    values=[self.mem.ram.get(address+i) for i in range(length)]
                    milestones.append({**({'core':self.core_id} if machine is not None else {}),'kind':'watched_ram_after_pc','pc':hex(before_pc),'label':label,
                        'address':hex(address),'size':length,'initialized_bytes':sum(v is not None for v in values),
                        'bytes_with_unknowns':''.join('??' if v is None else f'{v:02x}' for v in values)})
            if helper_return:
                self.repeat=None
                adapters['irq_repeat_return_hypothesis']+=1
                return
            if self.repeat and self.pc==self.repeat['end']:
                register=self.repeat['register']
                if register is not None:
                    self.R[register]-=1
                self.repeat['remaining']-=1
                if self.repeat['remaining']:
                    self.pc=self.repeat['start']
                else:
                    self.repeat=None

        def step_body(self):
            insn,raw,size=self.decode(self.pc)
            self.check_opaque(insn,raw)
            if machine is not None:
                if machine.first_task_return_hypothesis is not None:
                    machine.first_task_tail(self,insn,raw)
                    if insn.id=='rti' and self.pc==0x6066130:
                        machine.project_first_task_return(self)
                        adapters['scoped_first_task_return_hypothesis']+=1
                        return
                if self.pc==0x06066120:
                    require(insn.id=='ld.w' and raw=={'d':1,'b':4,'n':0} and self.after_then is None,
                            'first-task TCB stack load changed')
                if not self.sp_known and (insn.id.startswith(('push','pop','add.sp','add.r_sp')) or
                        ('.sp' in insn.id and insn.id!='mov.sp_ssp')):
                    raise Blocked('unknown_core_stack_pointer',core=self.core_id)
                if insn.id in ('goto.r','call.r'):
                    target=self.R[raw['x']]
                    if isinstance(target,UnknownWord):target.consumed()
                    if isinstance(target,IcfgValue):target.concrete()
                if machine.gpio_out_readback:
                    kind=insn.id;op=module.check.operands(insn,raw)
                    if kind in ('ld.w','ld.w.l','ld.w.sp','ld.w.idxs','ld.w.idx'):
                        if kind=='ld.w':address=self.R[op['b']]+op['n'];dest=op['d']
                        elif kind=='ld.w.l':address=self.R[op['s']]+op['n'];dest=op['r']
                        elif kind=='ld.w.sp':address=self.sp+op['n'];dest=op['r']
                        elif kind=='ld.w.idxs':address=self.R[op['s']]+(self.R[op['t']]<<2);dest=op['r']
                        else:address=self.R[op['s']]+self.R[op['t']];dest=op['r']
                        address&=0xffffffff
                        if hardware.handles_gpio(address) and address%256!=4:
                            self.R[dest]=hardware.gpio_read(address)
                            self.finish_exact(self.pc+size);adapters['gpio_out_readback_load']+=1
                            return
                    if kind in ('and.nmodimm.rri','and.modimm.rri','or.modimm.rri') and isinstance(self.R[op['s']],GpioOutValue):
                        self.R[op['r']]=self.R[op['s']].mask('and' if kind.startswith('and.') else 'or',op['n'])
                        self.finish_exact(self.pc+size);adapters['gpio_out_readback_bit_mask']+=1
                        return
                    if kind in ('or.bit','and.invbit') and isinstance(self.R[op['r']],GpioOutValue):
                        self.R[op['r']]=self.R[op['r']].mask('or' if kind=='or.bit' else 'and',op['b'])
                        self.finish_exact(self.pc+size);adapters['gpio_out_readback_bit_mask']+=1
                        return
                    if kind=='mov.rr' and isinstance(self.R[op['y']],GpioOutValue):
                        self.R[op['x']]=self.R[op['y']]
                        self.finish_exact(self.pc+size);adapters['gpio_out_readback_register_copy']+=1
                        return
                    if kind in ('st.w','st.w.l','st.w.modimm','st.w.idxs','st.w.idx','st.w.sp'):
                        if kind=='st.w.modimm':address=self.R[op['r']]+op['n'];value=op['m']
                        elif kind=='st.w.idxs':address=self.R[op['r']]+(self.R[op['s']]<<2);value=self.R[op['t']]
                        elif kind=='st.w.idx':address=self.R[op['r']]+self.R[op['s']];value=self.R[op['t']]
                        elif kind=='st.w.l':address=self.R[op['r']]+op['n'];value=self.R[op['s']]
                        elif kind=='st.w':address=self.R[op['b']]+op['n'];value=self.R[op['d']]
                        else:address=self.sp+op['n'];value=self.R[op['r']]
                        address&=0xffffffff
                        if isinstance(value,GpioOutValue):
                            if not (hardware.handles_gpio(address) and address%256!=4):value.consumed()
                            hardware.gpio_write_partial(address,value)
                            self.finish_exact(self.pc+size);adapters['gpio_out_readback_writeback']+=1
                            return
                if machine.partial_icfg:
                    state=self.icfg_state;kind=insn.id
                    if kind in ('mov.r_sr','mov.sr_r','li.sr_u32') and raw.get('s')==11:
                        if kind=='mov.r_sr':self.R[raw['r']]=state.read()
                        else:state.write(raw['n'] if kind=='li.sr_u32' else self.R[raw['r']])
                        machine.sync_icfg(self)
                        event({'access':'special-read' if kind=='mov.r_sr' else 'special-write',
                               'register':'icfg','known_mask':hex(state.known),'known_value':hex(state.value)})
                        self.finish_exact(self.pc+size);adapters['partial_icfg_read_or_gate_write']+=1
                        return
                    if kind in ('cli','cli.r','sti','sti.r'):
                        saved=state.read()
                        if kind=='sti.r':
                            source=self.R[raw['x']]
                            if isinstance(source,IcfgValue):value,known=source.value,bool(source.known&0x200)
                            elif isinstance(source,int):value,known=source,True
                            else:source.consumed()
                        else:value,known=(0 if kind.startswith('cli') else 0x200),True
                        if kind=='cli.r':self.R[raw['x']]=saved
                        state.bit9(value,known);machine.sync_icfg(self)
                        event({'access':'special-write','register':'icfg','known_mask':hex(state.known),'known_value':hex(state.value)})
                        self.finish_exact(self.pc+size);adapters['partial_icfg_bit9_only']+=1
                        return
                    if kind=='mov.rr' and isinstance(self.R[raw['y']],IcfgValue):
                        self.R[raw['x']]=self.R[raw['y']]
                        self.finish_exact(self.pc+size);adapters['partial_icfg_register_copy']+=1
                        return
                    if kind in ('and.nmodimm.rri','and.modimm.rri','or.modimm.rri') and isinstance(self.R[raw['s']],IcfgValue):
                        op=module.check.operands(insn,raw)
                        value=self.R[op['s']].mask('and' if kind.startswith('and.') else 'or',op['n'])
                        # Fully known extracts can be consumed numerically;
                        # preserve source identity for actual ICFG restoration.
                        self.R[op['r']]=(value.value if value.known==WORD_MASK and value.identity&~0x300&WORD_MASK!=
                                        (~0x300&WORD_MASK) else value)
                        self.finish_exact(self.pc+size);adapters['partial_icfg_pure_bit_mask']+=1
                        return
                    if kind=='ext.b0.u' and isinstance(self.R[raw['s']],IcfgValue):
                        source=self.R[raw['s']]
                        self.R[raw['d']]=source.mask('and',255).concrete()
                        self.finish_exact(self.pc+size);adapters['partial_icfg_known_lowbyte']+=1
                        return
                    if kind in ('st.w','st.w.l','st.w.modimm','st.w.idxs','st.w.sp','st.w.idx'):
                        address,value=self.word_store(insn,raw,abstract=True)
                        if isinstance(value,IcfgValue):
                            self.mem.write_abstract_word(address,value)
                            self.finish_exact(self.pc+size);adapters['partial_icfg_word_spill']+=1
                            return
                    if self.mem.icfg_spills and kind in ('ld.w','ld.w.l','ld.w.sp','ld.w.idxs','ld.w.idx'):
                        op=module.check.operands(insn,raw)
                        if kind=='ld.w':address=self.R[op['b']]+op['n'];dest=op['d']
                        elif kind=='ld.w.l':address=self.R[op['s']]+op['n'];dest=op['r']
                        elif kind=='ld.w.sp':address=self.sp+op['n'];dest=op['r']
                        else:
                            address=self.R[op['s']]+(self.R[op['t']]<<(2 if kind=='ld.w.idxs' else 0));dest=op['r']
                        self.R[dest]=self.mem.abstract_word(address&WORD_MASK)
                        self.finish_exact(self.pc+size);adapters['partial_icfg_word_reload']+=1
                        return
                if self.core_id==1 and self.pc==0x12cad8 and self.opaque_source=='cpu1_bootstrap_icfg':
                    require(self.bootstrap_icfg_pending and self.R[0] is None and insn.id=='and.nmodimm.rri' and
                            raw=={'r':0,'s':0,'n':3904},'bootstrap ICFG mask requires its one-use unknown read')
                    self.R[0]=self.icfg&~0x300
                    self.bootstrap_icfg_pending=False;self.opaque_source=None
                    self.finish_exact(self.pc+size);adapters['bootstrap_icfg_actual_gate_mask']+=1
                    return
                if insn.id=='li.sr_u32' and raw['s'] in (0,1,2,3,5,12,13,14):
                    sr,value=raw['s'],raw['n']
                    if sr in (1,2,5) and value!=0:raise Blocked('special_register_nonzero_semantics_unmodeled',register=sr)
                    if sr in (0,3):value&=0x07fffffe
                    name={0:'reti',1:'retx',2:'rete',3:'rets',5:'irq_psr',12:'usp',13:'ssp',14:'sp'}[sr]
                    setattr(self,name,value)
                    if sr in (12,13):machine.note_bank_write(self,name,value)
                    if sr==5:self.flags=0
                    if sr==14:self.sp_known=True;machine.note_sp_write(self)
                    self.finish_exact(self.pc+size);adapters['multicore_visible_special_write']+=1
                    return
                if insn.id in ('mov.r_sr','mov.sr_r') and raw['s'] in (0,1,2,3,5,12,13,14):
                    sr=raw['s'];name={0:'reti',1:'retx',2:'rete',3:'rets',5:'irq_psr',12:'usp',13:'ssp',14:'sp'}[sr]
                    if insn.id=='mov.r_sr':
                        value=getattr(self,name,None)
                        if value is None or (sr==14 and not self.sp_known):raise Blocked('unknown_core_special_register',core=self.core_id,register=name)
                        if sr==5:value=(value&~15)|(self.flags&15)
                        self.R[raw['r']]=value
                    else:
                        value=self.R[raw['r']]
                        if isinstance(value,UnknownWord):value.consumed()
                        if isinstance(value,IcfgValue):value=value.concrete()
                        if sr in (1,2,5) and value!=0:raise Blocked('special_register_nonzero_semantics_unmodeled',register=sr)
                        if sr in (0,3):value&=0x07fffffe
                        if self.pc==0x06066122:
                            require(sr==14 and raw['r']==1 and self.task_frame_load==(self.R[4],value),
                                    'first-task SP switch lacks its actual TCB load')
                        setattr(self,name,value)
                        if sr in (12,13):machine.note_bank_write(self,name,value)
                        if sr==5:self.flags=0
                        if sr==14:self.sp_known=True;machine.note_sp_write(self)
                        if self.pc==0x06066122:
                            self.task_frame_restore={'tcb':self.R[4],'base':value}
                            self.task_frame_load=None
                    self.finish_exact(self.pc+size);adapters['multicore_visible_special_move']+=1
                    return
                if insn.id=='mov.sp_ssp':
                    if self.ssp is None:raise Blocked('unknown_core_special_register',core=self.core_id,register='ssp')
                    self.sp=self.ssp;self.sp_known=True
                    machine.note_bank_switch(self,'ssp')
                    self.finish_exact(self.pc+size);adapters['multicore_visible_sp_from_ssp']+=1
                    return
                if insn.id=='mov.sp_usp':
                    if self.usp is None:raise Blocked('unknown_core_special_register',core=self.core_id,register='usp')
                    self.sp=self.usp;self.sp_known=True
                    machine.note_bank_switch(self,'usp')
                    self.finish_exact(self.pc+size);adapters['multicore_visible_sp_from_usp']+=1
                    return
                if insn.id=='mov.usp_sp':
                    self.usp=self.sp
                    machine.note_bank_write(self,'usp',self.sp)
                    self.finish_exact(self.pc+size);adapters['multicore_visible_usp_from_sp']+=1
                    return
                if insn.id.startswith(('mov.sp_','mov.ssp_sp','mov.uspn_sp','mov.sspn_sp')):
                    raise Blocked('multicore_stack_bank_transfer_unmodeled',instruction_id=insn.id)
                if insn.id=='rti' and self.core_id==1 and self.pc==0x12cb06:
                    machine.return_bootstrap(self);adapters['conditional_cpu1_bootstrap_rti']+=1
                    return
                if (insn.id=='rti' and machine.software_irq_rti and
                        getattr(self,'active_context',None)=='software_irq_handler' and
                        self.pc in machine.SOFTWARE_IRQ_RTI_TAILS):
                    # Symmetric to the entry assumption: PC=RETI, SP=USP. The
                    # restored RETI/PSR come from the real popped frame; the
                    # bank writeback and ICFG effects stay uncertified.
                    machine.return_software_irq(self);adapters['conditional_software_irq_rti']+=1
                    return
                if (insn.id=='rti' and machine.core_tick_rti and
                        getattr(self,'active_context',None)=='core_tick_handler' and self.pc==0x60661e6):
                    machine.return_core_tick(self);adapters['conditional_core_tick_rti']+=1
                    return
                if self.pc==0x06066126:
                    require(insn.id=='pop.srl' and raw.get('m',raw.get('n'))==0x29 and self.task_frame_restore and
                            self.sp==self.task_frame_restore['base'],'first-task special frame lacks its selected stack')
                    reti,rets,psr=struct.unpack('<III',self.mem.read(self.sp,12))
                    self.reti=reti&0x07fffffe;self.rets=rets&0x07fffffe;self.irq_psr=psr;self.flags=psr&15;self.sp+=12
                    machine.record_event({'kind':'first_task_special_frame_restored','core':self.core_id,
                        'tcb':hex(self.task_frame_restore['tcb']),'frame':hex(self.task_frame_restore['base']),
                        'reti':hex(self.reti),'rets':hex(self.rets),'psr':hex(psr),'sp_after':hex(self.sp)})
                    self.task_gpr_frame=dict(self.task_frame_restore)
                    self.task_frame_restore=None
                    self.finish_exact(self.pc+size);adapters['actual_first_task_special_frame']+=1
                    return
                if machine.unknown_task_gprs and self.pc==0x06066128:
                    require(insn.id=='pop.rmask' and raw=={'n':0xffff} and self.task_gpr_frame and
                            self.sp==self.task_gpr_frame['base']+12 and self.after_then is None,
                            'abstract first-task GPR load requires the actual selected frame')
                    values=[self.mem.abstract_word(self.sp+4*i,task_frame=True) for i in range(16)]
                    if machine.first_task_return_hypothesis is not None:
                        machine.arm_first_task_return(self,self.task_gpr_frame,values)
                    self.R=values;self.sp+=64
                    machine.record_event({'kind':'first_task_gprs_restored','core':self.core_id,
                        'frame':hex(self.task_gpr_frame['base']),'sp_after':hex(self.sp),
                        'unknown_registers':[i for i,v in enumerate(values) if isinstance(v,UnknownWord)]})
                    self.task_gpr_frame=None
                    self.finish_exact(self.pc+size);adapters['actual_first_task_abstract_gprs']+=1
                    return
                if machine.unknown_task_gprs and insn.id=='mov.rr' and isinstance(self.R[raw['y']],UnknownWord):
                    self.R[raw['x']]=self.R[raw['y']]
                    self.finish_exact(self.pc+size);adapters['unknown_gpr_copy']+=1
                    return
                if machine.unknown_task_gprs and insn.id in ('push.rl','push.rets_rl','push.rmask','push.rets_rmask',
                        'pop.rl','pop.rets_rl','pop.pc_rl','pop.rmask','pop.rets_rmask','pop.pc_rmask'):
                    if insn.id.endswith('_rl') or insn.id in ('push.rl','pop.rl'):
                        last=raw['l'];regs=list(range(last,4)) if last<4 else list(range(4,last+1))
                    else:
                        mask=raw.get('m',raw.get('n'));regs=[i for i in range(16) if mask>>i&1]
                    special='rets' if '.rets_' in insn.id else 'pc' if '.pc_' in insn.id else None
                    count=len(regs)+(special is not None)
                    # The scheduler's own task restore (handler tail after the
                    # reloaded TCB frame pointer) loads task-frame GPR slots;
                    # uninitialised slots become UnknownWord like the first task.
                    task_frame_load=(not insn.id.startswith('push') and self.pc==0x12ada2 and
                                     getattr(self,'active_context',None)=='software_irq_handler' and
                                     regs==list(range(16)))
                    if insn.id.startswith('push'):
                        values=[self.R[i] for i in regs]+([self.rets] if special else [])
                        base=self.sp-4*count
                        # Validate the complete destination before changing any byte or SP.
                        for i,v in enumerate(values):self.mem.preflight_abstract_word(base+4*i,v)
                        for i,v in enumerate(values):self.mem.write_abstract_word(base+4*i,v)
                        self.sp=base
                    else:
                        values=[self.mem.abstract_word(self.sp+4*i,task_frame=task_frame_load) for i in range(count)]
                        if task_frame_load:
                            machine.record_event({'kind':'scheduler_task_gprs_restored','core':self.core_id,
                                'frame':hex(self.sp),'unknown_registers':[i for i,v in enumerate(values) if isinstance(v,UnknownWord)]})
                        target=values[-1] if special else None
                        if isinstance(target,UnknownWord):target.consumed()
                        if isinstance(target,IcfgValue):target=target.concrete()
                        for i,v in zip(regs,values):self.R[i]=v
                        self.sp+=4*count
                        if special=='rets':self.rets=target&0x07fffffe
                    end=self.pc+size
                    self.seq=end;self.pc=target&0xfffffffe if special=='pc' else end;self.steps+=1
                    if self.after_then is not None:
                        self.after_then[0]-=1;self.check_then()
                    adapters['abstract_task_stack_transport']+=1
                    return
                if insn.id in ('li.sr_u32','mov.r_sr','mov.sr_r') and not (
                        (insn.id=='mov.r_sr' and raw['s']==6) or
                        (insn.id in ('mov.r_sr','mov.sr_r') and raw['s']==11)):
                    raise Blocked('multicore_special_register_semantics_unmodeled',register=raw['s'],instruction_id=insn.id)
                frames=dict((diagnostics or {}).get('software_irq_frames') or {})
                core_tick_frames=(diagnostics or {}).get('core_tick_frames') or {}
                frames.update(core_tick_frames)
                if insn.id in ('push.srl','pop.srl','push.srmask','pop.srmask') and self.pc in frames:
                    shape=frames[self.pc];push=shape.endswith('push')
                    require(shape in ('psr_push','psr_pop','frame12_push','frame12_pop') and
                            insn.id==('push.srl' if push else 'pop.srl') and
                            raw.get('m',raw.get('n'))==(32 if shape.startswith('psr') else 41) and
                            self.after_then is None,'IRQ handler special-frame shape changed')
                    if shape=='psr_push':
                        self.sp-=4
                        psr=(getattr(self,'irq_psr',0)&~15)|(self.flags&15)
                        self.mem.write(self.sp,struct.pack('<I',psr))
                        self.flags=0  # report5-k: pushing PSR saves then clears low flags
                    elif shape=='psr_pop':
                        psr=struct.unpack('<I',self.mem.read(self.sp,4))[0]
                        self.irq_psr=psr;self.flags=psr&15;self.sp+=4
                    elif shape=='frame12_push':
                        self.sp-=12
                        psr=(getattr(self,'irq_psr',0)&~15)|(self.flags&15)
                        self.mem.write(self.sp,struct.pack('<III',self.reti,self.rets,psr))
                        self.flags=0
                    else:
                        self.reti,self.rets,psr=struct.unpack('<III',self.mem.read(self.sp,12))
                        self.reti&=0x07fffffe;self.rets&=0x07fffffe
                        self.irq_psr=psr;self.flags=psr&15;self.sp+=12
                    self.pc+=size;self.steps+=1
                    if self.pc in core_tick_frames:adapters['core_tick_handler_frame']+=1
                    else:adapters['software_irq_handler_psr_frame' if shape.startswith('psr') else
                                 'software_irq_handler_context_frame']+=1
                    return
                if insn.id in ('push.srl','pop.srl','push.srmask','pop.srmask') and not (diagnostics or {}).get('irq_context'):
                    raise Blocked('multicore_special_frame_unmodeled',instruction_id=insn.id)
            if diagnostics and diagnostics.get('exact_doubleword_stores') and insn.id in ('st.d.l','st.d.sp.l'):
                # A doubleword store overwrites all eight bytes, so the old
                # words cannot affect its result. Writing both halves directly
                # skips the upstream store model's merge bookkeeping, which
                # reads the old words first (a model-internal read, not a
                # guest load) and would stop on uninitialized RAM. Neither
                # form updates its base register, so both halves are the
                # current registers (upstream reads the odd half after a base
                # update only on the pi/prer forms).
                op=module.check.operands(insn,raw)
                if insn.id=='st.d.l':
                    address=(self.R[op['r']]+op['n'])&0xffffffff;source=op['s']
                else:
                    address=(self.sp+op['n'])&0xffffffff;source=op['r']
                require(address%4==0 and source%2==0 and 0<=source<=14,
                        'unsupported unaligned or odd-register doubleword store')
                self.mem.write(address,struct.pack('<II',self.R[source]&0xffffffff,self.R[source+1]&0xffffffff))
                self.finish_exact(self.pc+size);adapters['exact_doubleword_store']+=1
                return
            if diagnostics and diagnostics.get('immediate_rep_projection') and insn.id=='rep.i':
                op=module.check.operands(insn,raw)
                body,fields,body_size=self.decode(self.pc+size)
                allowed_stores=('st.w.pin','st.h.pin','st.b.pin') if diagnostics.get('exact_subword_stores') else ('st.w.pin',)
                if body_size==op['b'] and body.id in allowed_stores:
                    require(self.repeat is None and self.after_then is None and 1<=op['n']<=32,
                            'immediate REP projection has an unsupported context')
                    self.repeat={'start':self.pc+size,'end':self.pc+size+op['b'],'register':None,'remaining':op['n']}
                    self.pc+=size;self.steps+=1;adapters['immediate_rep_single_store_projection']+=1
                    return
            if diagnostics and diagnostics.get('immediate_rep_projection') and (insn.id=='st.w.pin' or
                    (diagnostics.get('exact_subword_stores') and insn.id in ('st.h.pin','st.b.pin'))):
                op=module.check.operands(insn,raw);address=self.R[op['b']]
                width={'st.w.pin':4,'st.h.pin':2,'st.b.pin':1}[insn.id]
                require(address%width==0,'unaligned postincrement store')
                source=self.R[op['d']]
                if (isinstance(source,UnknownWord) and diagnostics.get('rep_copy_unknown_transport') and
                        self.repeat is not None and self.repeat.get('multi_block')):
                    # rep 拷贝体的未知字节回存:已知字节落盘、未知字节带来源保持
                    # 未知(与装载侧 rep_copy_unknown_transport 配对,不臆造数值)。
                    self.mem.write_abstract_word(address&0xffffffff,source)
                    self.R[op['b']]=(address+op['n'])&0xffffffff
                    self.finish_exact(self.pc+size);adapters['rep_copy_unknown_transport_store']+=1
                    return
                value=source&((1<<(width*8))-1)
                self.mem.write(address&0xffffffff,value.to_bytes(width,'little'))
                self.R[op['b']]=(address+op['n'])&0xffffffff
                self.finish_exact(self.pc+size);adapters['exact_postincrement_store']+=1
                return
            if (diagnostics and diagnostics.get('rep_copy_unknown_transport') and insn.id=='ld.w.pin' and
                    self.repeat is not None and self.repeat.get('multi_block')):
                # rep 拷贝体装载(证据形状:0x600a352 rep 4 4 体 r3=[r2++=4]/
                # [r1++=4]=r3,源是 qcy_boot_info 0x122800)。boot_info 的 bytes
                # 1-3(vm_info 内 u8 align 后的 C 对齐 padding)与 8-11(vm_res,
                # boot.h 注释 reverse_bytes 保留字段)在 app/ROM 里没有任何写
                # 入者——真机上是残留 RAM 垃圾。模型不臆造数值:跨未写 RAM 字节
                # 的字装载构造 UnknownWord(逐字节来源=源地址),交给存储侧按来
                # 源运输;任何后续消费者仍严格停。
                op=module.check.operands(insn,raw);address=self.R[op['b']]&0xffffffff
                physical=(hardware.mmu.translate(address,4,self.mem.read)
                          if hardware is not None and hardware.mmu is not None else address)
                region=self.mem.region(physical,4)
                values=[self.mem.ram.get(physical+i) for i in range(4)] if region else None
                if (region and address%4==0 and physical not in self.mem.icfg_spills and
                        any(v is None for v in values)):
                    origins=[physical+i if v is None else None for i,v in enumerate(values)]
                    self.R[op['d']]=UnknownWord(values,origins)
                    self.R[op['b']]=(address+op['n'])&0xffffffff
                    event({'access':'abstract-read','address':hex(physical),'size':4,'region':region,
                           'bytes_with_unknowns':''.join('??' if v is None else f'{v:02x}' for v in values)})
                    self.finish_exact(self.pc+size);adapters['rep_copy_unknown_transport_load']+=1
                    return
            if diagnostics and diagnostics.get('exact_subword_stores') and insn.id in (
                    'st.h','st.b','st.h.l','st.b.l','st.h.sp','st.b.sp','st.h.sp.l','st.b.sp.l'):
                op=module.check.operands(insn,raw)
                if '.sp' in insn.id:address=self.sp+op['n'];value=self.R[op['r']]
                elif insn.id.endswith('.l'):address=self.R[op['r']]+op['n'];value=self.R[op['s']]
                else:address=self.R[op['b']]+op['n'];value=self.R[op['d']]
                width=2 if insn.id.startswith('st.h') else 1
                require(address%width==0,'unaligned exact subword store')
                self.mem.write(address&0xffffffff,(value&((1<<(width*8))-1)).to_bytes(width,'little'))
                self.finish_exact(self.pc+size);adapters['exact_subword_store']+=1
                return
            if diagnostics and diagnostics.get('exact_parallel_stores') and insn.id.endswith('.p'):
                pair,pair_raw,pair_size=self.decode(self.pc+size)
                base=self.tab.by_id(insn.id[:-2])
                pure_alu=base.id.startswith(('li.','mov.','add.','sub.','and.','or.','xor.','div.')) or (
                    diagnostics.get('exact_parallel_extensions') and base.id=='ext.b0.u')
                if pure_alu and pair.id in ('st.w','st.w.l','st.w.modimm','st.w.idxs','st.w.sp','st.w.idx'):
                    params=module.table.placeholders(insn.syntax);plain=module.table.placeholders(base.syntax)
                    base_raw=dict(zip(plain,(raw[k] for k in params)))
                    base_raw.update({k:raw[k] for k in base.fields if k not in base_raw and k in raw})
                    # Evaluate only pure ALU half on old registers. The store also
                    # uses old registers, without a host scratch-word merge read.
                    effect,_,_=self.effect(base,base_raw,self.pc,size,self.R)
                    require(set(effect)<= {'regs','flags'},'parallel ALU produced non-ALU effects')
                    address,value=self.word_store(pair,pair_raw,abstract=machine is not None and machine.partial_icfg)
                    require(address%4==0,'unaligned exact parallel word store')
                    if isinstance(value,IcfgValue):self.mem.write_abstract_word(address,value)
                    else:self.mem.write(address,struct.pack('<I',value))
                    self.apply(effect);self.finish_exact(self.pc+size+pair_size)
                    adapters['parallel_full_word_store_old_registers']+=1
                    return
            if diagnostics and diagnostics.get('exact_parallel_stores') and insn.id in ('st.w','st.w.l','st.w.modimm','st.w.idxs','st.w.sp','st.w.idx'):
                address,value=self.word_store(insn,raw)
                require(address%4==0,'unaligned exact word store')
                self.mem.write(address,struct.pack('<I',value));self.finish_exact(self.pc+size)
                adapters['exact_full_word_store']+=1
                return
            if hardware is not None and diagnostics and diagnostics.get('opaque_pwm_read') and self.pc==0x06034a82:
                require(insn.id=='ld.w' and raw=={'d':0,'b':5,'n':0} and self.after_then is None and
                        0<=self.R[4]<4 and self.R[5]==0xfd2964+(self.R[4]<<4),'discarded PWM read shape changed')
                require(int.from_bytes(self.mem.read(self.sp+8,4),'little')==0x060006e2 and
                        int.from_bytes(self.mem.read(self.sp+4,4),'little')+8<self.R[6],
                        'PWM return might be consumed by caller')
                hardware.observe_dead_pwm_read(self.R[5])
                self.R[0]=None;self.opaque_source='pwm_dead_read';self.pc+=size;self.steps+=1
                adapters['proven_dead_pwm_configuration_read']+=1
                return
            if hardware is not None and diagnostics and diagnostics.get('opaque_pin_read') and self.pc==0x0606745e:
                require(insn.id=='ld.w.l' and raw=={'r':0,'s':8,'n':120} and self.R[8]+480==0xf2c300
                        and self.after_then is None,'P33 discarded read shape changed')
                caller=int.from_bytes(self.mem.read(self.sp+20,4),'little')
                require(caller in (0x0603502c,0x06035306),'P33 read has a caller that might consume R0')
                if caller==0x0603502c:
                    ret_insn,ret_fields,_=self.decode(caller)
                    require(ret_insn.id=='li.s16' and ret_fields=={'r':0,'n':848},'caller no longer kills R0')
                hardware.observe_dead_port_selection_read(0xf2c300)
                self.opaque_source='p33_pin0_return' if caller==0x06035306 else 'p33_dead_return'
                self.R[0]=None  # opaque, never a fake register value
                self.pc+=size;self.steps+=1
                adapters['proven_dead_p33_read']+=1
                return
            if diagnostics and diagnostics.get('irq_context') and self.pc in (diagnostics['entry_pc'],diagnostics['irq_pop_pc']):
                push=self.pc!=diagnostics['irq_pop_pc']
                require(insn.id==('push.srl' if push else 'pop.srl') and raw.get('m',raw.get('n'))==0x29
                        and self.after_then is None,'QCY IRQ special-frame shape changed')
                if push:
                    self.sp-=12
                    psr=(self.irq_psr&~15)|(self.flags&15)
                    self.mem.write(self.sp,struct.pack('<III',self.reti,self.rets,psr))
                    self.flags=0  # pinned hardware report5-k: pushing PSR saves then clears low flags
                else:
                    self.reti,self.rets,self.irq_psr=struct.unpack('<III',self.mem.read(self.sp,12))
                    self.reti&=0x07fffffe;self.rets&=0x07fffffe;self.flags=self.irq_psr&15;self.sp+=12
                self.pc+=size;self.steps+=1
                adapters['qcy_irq_special_frame']+=1
                return
            if hardware is not None and hardware.bus_lock_model and insn.id in ('lockset','lockclr'):
                require(self.after_then is None,'shared lock adapter inside conditional block is not implemented')
                hardware.lock(insn.id=='lockset')
                self.pc+=size;self.steps+=1
                adapters['cpu0_shared_lock_ownership']+=1
                return
            if hardware is not None and hardware.reset is not None and insn.id in ('rmw.and.nmodimm','rmw.and.modimm','rmw.and.r'):
                op=module.check.operands(insn,raw);address=(self.R[op['r']]+op['n'])&0xffffffff
                if address in hardware.reset.configuration:
                    operand=self.R[op['s']] if insn.id=='rmw.and.r' else op['m']
                    hardware.reset.rmw(address,'and',operand)
                    word=hardware.reset.configuration[address]
                    hardware.counts['reads']+=1
                    hardware.record_write({'address':hex(address),'known_mask':hex(word.known),'known_value':hex(word.value)})
                    self.finish_exact(self.pc+size);adapters['reset_unknown_preserving_configuration_rmw']+=1
                    return
            if hardware is not None and insn.id in ('rmw.or.modimm','rmw.and.nmodimm','rmw.and.modimm','rmw.and.invbitr','rmw.or.bitr','rmw.or.r','rmw.and.invr','rmw.and.r'):
                op=module.check.operands(insn,raw)
                address=(self.R[op['r']]+op['n'])&0xffffffff
                if 0xf00000<=address<0x1000000:
                    if hardware.software_irq is not None and hardware.software_irq.handles(address):
                        if insn.id=='rmw.or.bitr':mask=1<<(self.R[op['s']]&31)
                        elif insn.id=='rmw.or.modimm':mask=op['m']
                        else:raise Blocked('software_irq_rmw_form_unmodeled',instruction_id=insn.id)
                        hardware.software_irq_command(address,mask)
                        self.finish_exact(self.pc+size);adapters['software_irq_selected_bit_command']+=1
                        return
                    if hardware.software_irq is not None and hardware.software_irq.handles_source(address):
                        if insn.id=='rmw.and.nmodimm':source_kind='and';operand=op['m']
                        elif insn.id=='rmw.or.modimm':source_kind='or';operand=op['m']
                        else:raise Blocked('software_irq_source_rmw_form_unmodeled',instruction_id=insn.id)
                        hardware.software_irq_source_rmw(address,source_kind,operand)
                        self.finish_exact(self.pc+size);adapters['software_irq_source_enable_rmw']+=1
                        return
                    if machine is not None and address in hardware.core_power:
                        if insn.id!='rmw.and.nmodimm':raise Blocked('core_power_rmw_form_not_modeled')
                        hardware.core_power_rmw(address,'and',op['m'])
                        self.finish_exact(self.pc+size);adapters['core_power_unknown_preserving_rmw']+=1
                        return
                    if hardware.handles_gpcnt(address):
                        if insn.id=='rmw.and.nmodimm':kind='and';operand=op['m']
                        elif insn.id=='rmw.or.modimm':kind='or';operand=op['m']
                        else:raise Blocked('gpcnt_rmw_form_not_modeled',instruction_id=insn.id)
                        hardware.gpcnt_rmw(address,kind,operand)
                        self.finish_exact(self.pc+size);adapters['gpcnt_unknown_preserving_cleanup_rmw']+=1
                        return
                    if hardware.handles_sfc_clock(address):
                        if insn.id=='rmw.or.modimm':kind='or';operand=op['m']
                        elif insn.id=='rmw.and.nmodimm':kind='and';operand=op['m']
                        else:raise Blocked('sfc_clock_rmw_form_not_modeled',instruction_id=insn.id)
                        hardware.sfc_clock_rmw(address,kind,operand)
                        self.finish_exact(self.pc+size);adapters['sfc_clock_gate_rmw']+=1
                        return
                    if hardware.handles_sfc_window_rmw(address):
                        # 0x600a37e `rmw.or.modimm [0xfe0218]|=2`:VM flash 窗口
                        # 使能位置位,唯一观测形状。窗口组只写不读,RMW 不经过
                        # 读回,按位记录;其他形式(and/整字写)无证据,保持拒绝。
                        if insn.id!='rmw.or.modimm':
                            raise Blocked('sfc_window_control_rmw_form_not_modeled',instruction_id=insn.id)
                        hardware.sfc_window_rmw(address,op['m'])
                        self.finish_exact(self.pc+size);adapters['sfc_window_enable_rmw']+=1
                        return
                    if hardware.handles_core_tick(address):
                        # Handler ACK |=0x40 (bit6 W1C clear-pending) on CON.
                        # Only the RMW form is modeled; a full-word CON store
                        # still goes through the strict write path.
                        if insn.id=='rmw.or.modimm':kind='or';operand=op['m']
                        else:raise Blocked('core_tick_rmw_form_not_modeled',instruction_id=insn.id)
                        hardware.core_tick_rmw(address,kind,operand)
                        self.finish_exact(self.pc+size);adapters['core_tick_ack_rmw']+=1
                        return
                    if hardware.handles_cpu1_control_rmw(address):
                        # CPU1 PMU_CON0 RMWs: the actual release itself is the guarded
                        # |=1 at 0x60660da; after release CPU0 re-asserts the kick bit3
                        # while polling a peer-core flag (0x12b732 in the 0x12b73c loop).
                        # bit0-involved forms keep the strict write-path semantics.
                        if insn.id=='rmw.or.modimm':kind='or';operand=op['m']
                        elif insn.id in ('rmw.and.nmodimm','rmw.and.modimm'):kind='and';operand=op['m']
                        else:raise Blocked('cpu1_control_rmw_form_not_modeled',instruction_id=insn.id)
                        hardware.cpu1_control_rmw(address,kind,operand)
                        self.finish_exact(self.pc+size);adapters['cpu1_control_rmw']+=1
                        return
                    if hardware.handles_p11_lpwr_rmw(address):
                        # power_init 0x60679a0 请求:PWR_CON |=0x20(LP_PWR_INIT_FLAG)。
                        # opt-in P11 响应投影只记录该请求;合并写回不落在严格复位
                        # 标志路径(该路径只认 bit6 清标志命令)。其他形式保持严格拒绝。
                        hardware.p11_lpwr_rmw(address,insn.id,op.get('m'))
                        self.finish_exact(self.pc+size);adapters['p11_lpwr_request_rmw']+=1
                        return
                    if hardware.handles_gpio(address):
                        if insn.id=='rmw.or.modimm':kind='or';operand=op['m']
                        elif insn.id=='rmw.and.nmodimm':kind='and';operand=op['m']
                        elif insn.id=='rmw.and.modimm':kind='and';operand=op['m']
                        elif insn.id=='rmw.and.invbitr':kind='and';operand=~(1<<(self.R[op['s']]&31))
                        elif insn.id=='rmw.and.invr':kind='and';operand=~self.R[op['s']]
                        elif insn.id=='rmw.or.bitr':kind='or';operand=1<<(self.R[op['s']]&31)
                        elif insn.id=='rmw.and.r':kind='and';operand=self.R[op['s']]
                        else:kind='or';operand=self.R[op['s']]
                        hardware.gpio_rmw(address,kind,operand)
                        self.pc+=size;self.steps+=1
                        if self.after_then is not None:
                            self.after_then[0]-=1
                            self.check_then()  # same then-count / else-skip policy as upstream
                            adapters['gpio_conditional_rmw']+=1
                        adapters['gpio_symbolic_rmw']+=1
                        return
                    old=int.from_bytes(self.mem.read(address,4),'little')
                    if insn.id=='rmw.or.modimm':value=old|op['m']
                    elif insn.id=='rmw.and.nmodimm':value=old&op['m']
                    elif insn.id=='rmw.and.modimm':value=old&op['m']
                    elif insn.id=='rmw.and.invbitr':value=old&~(1<<(self.R[op['s']]&31))
                    elif insn.id=='rmw.and.invr':value=old&~self.R[op['s']]
                    elif insn.id=='rmw.or.bitr':value=old|(1<<(self.R[op['s']]&31))
                    elif insn.id=='rmw.and.r':value=old&self.R[op['s']]
                    else:value=old|self.R[op['s']]
                    # Always emit the bus write, even when the value is equal.
                    # Upstream scratch-memory models can drop unchanged writes.
                    self.mem.write(address,struct.pack('<I',value&0xffffffff))
                    if self.after_then is not None:adapters['mmio_conditional_rmw']+=1
                    self.finish_exact(self.pc+size)
                    adapters['mmio_rmw_always_writes']+=1
                    return
            if hardware is not None and insn.id in ('st.w','st.w.l','st.w.modimm','st.w.idxs'):
                op=module.check.operands(insn,raw)
                if insn.id=='st.w.modimm':address=(self.R[op['r']]+op['n'])&0xffffffff;value=op['m']
                elif insn.id=='st.w.idxs':address=(self.R[op['r']]+(self.R[op['s']]<<2))&0xffffffff;value=self.R[op['t']]
                elif insn.id=='st.w.l':address=(self.R[op['r']]+op['n'])&0xffffffff;value=self.R[op['s']]
                else:address=(self.R[op['b']]+op['n'])&0xffffffff;value=self.R[op['d']]
                if 0xf00000<=address<0x1000000:
                    require(self.after_then is None,'MMIO exact store inside conditional block is not adapted')
                    self.mem.write(address,struct.pack('<I',value&0xffffffff))
                    self.pc+=size;self.steps+=1
                    adapters['mmio_full_width_store']+=1
                    return
            if hardware is not None and rom is not None and self.pc==0xa30c:
                pair,pair_raw,pair_size=self.decode(self.pc+size)
                require(insn.id=='li.s16.p' and raw=={'d':0,'n':4113} and
                        pair.id=='st.w' and pair_raw=={'d':0,'b':1,'n':2} and self.after_then is None,
                        'ROM timer parallel parameter store changed')
                self.mem.write((self.R[1]+8)&0xffffffff,struct.pack('<I',self.R[0]))
                self.R[0]=4113;self.pc+=size+pair_size;self.steps+=1
                adapters['rom_timer_parallel_store_old_register']+=1
                return
            if rom is not None and diagnostics and diagnostics.get('rom_exact_stores') and self.pc==0xc2d6:
                require(insn.id=='st.w.modimm' and raw=={'r':3,'n':4,'m':0},'ROM PLL first store changed')
                # Reject the actual MMIO write, rather than an upstream
                # read/merge of the old destination that is not a guest load.
                self.mem.write((self.R[3]+16)&0xffffffff,bytes(4))
                raise AssertionError('unknown PLL control write must never be accepted')
            if rom is not None and diagnostics and diagnostics.get('rom_memset_rep') and self.pc in (0x4242,0x424c):
                register=4 if self.pc==0x4242 else 2
                require(insn.id=='rep' and raw=={'n':0,'r':register} and self.repeat is None,
                        'ROM memset REP form changed')
                count=self.R[register]
                require(0<=count<=10000,'unexpected ROM memset count')
                if count==0:
                    if not diagnostics.get('rom_zero_rep_skip'):
                        raise Blocked('unverified_zero_count_rep',instruction_id=insn.id,
                                      note='ROM memset tail; BR28 count=0 semantics unmeasured')
                    self.pc+=size+2
                    adapters['rom_zero_rep_skip_hypothesis']+=1
                else:
                    self.repeat={'start':self.pc+size,'end':self.pc+size+2,
                                 'register':register,'remaining':count}
                    self.pc+=size
                    adapters['rom_nonzero_rep_countdown_hypothesis']+=1
                self.steps+=1
                return
            if rom is not None and diagnostics and diagnostics.get('rom_exact_stores') and self.pc in (0x4244,0x424e,0x45b0,0x45b8):
                # Actual full-width stores do not architecturally load old RAM.
                # Restrict the upstream bookkeeping workaround to verified sites.
                if self.pc in (0x45b0,0x45b8):
                    value_reg=4 if self.pc==0x45b0 else 5
                    require(insn.id=='st.w' and raw=={'b':0,'n':0,'d':value_reg},'ROM pointer store changed')
                    address=self.R[0];value=self.R[value_reg];width=4;increment=False
                else:
                    width=4 if self.pc==0x4244 else 1
                    require(insn.id==('st.w.pin' if width==4 else 'st.b.pin') and
                            raw=={'b':3,'d':1,'n':0},'ROM memset store changed')
                    address=self.R[3];value=self.R[1];increment=True
                require(self.after_then is None,'ROM store adapter cannot skip a conditional block')
                self.mem.write(address,(value&((1<<(width*8))-1)).to_bytes(width,'little'))
                if increment:self.R[3]=(address+width)&0xffffffff
                self.pc+=size
                self.steps+=1
                adapters['rom_byte_exact_store']+=1
                return
            if diagnostics and diagnostics.get('visible_cli') and insn.id in ('cli','cli.r','sti','sti.r'):
                if not self.icfg_known:
                    raise Blocked('uninitialized_special_register',register='icfg')
                module.CPU.step(self)  # upstream's visible bit9 effect, not IRQ delivery
                adapters['cli_sti_visible_bit9_only']+=1
                event({'access':'special-write','register':'icfg','value':hex(self.icfg)})
                return
            if use_architectural_models and insn.id=='mov.r_sr' and raw.get('s')==6:
                self.R[raw['r']]=self.core_id
                self.pc+=size
                self.steps+=1
                adapters['per_core_visible_cnum' if machine is not None else 'core0_visible_cnum']+=1
                return
            if use_architectural_models and machine is None and insn.id=='mov.r_sr' and raw.get('s') in (3,14,15):
                self.R[raw['r']]={3:self.rets,14:self.sp,15:self.pc}[raw['s']]
                self.finish_exact(self.pc+size);adapters['explicit_visible_core_register_read']+=1
                return
            if (machine is not None or diagnostics) and insn.id in ('mov.r_sr','mov.sr_r') and raw.get('s')==11:
                if machine is None and not diagnostics.get('adapt_icfg'):
                    raise Blocked('unsupported_special_register',register='icfg',instruction_id=insn.id)
                if insn.id=='mov.r_sr':
                    if not self.icfg_known:
                        if (machine is not None and self.core_id==1 and self.pc==0x12cad4 and
                                getattr(self,'icfg_unknown_mask',None)==0x300 and raw['r']==0):
                            self.R[0]=None;self.opaque_source='cpu1_bootstrap_icfg';self.bootstrap_icfg_pending=True
                            event({'access':'special-read','register':'icfg','value':'unknown gates8/9; other bits explicit fixture'})
                        else:raise Blocked('uninitialized_special_register',register='icfg')
                    else:
                        self.R[raw['r']]=self.icfg
                        event({'access':'special-read','register':'icfg','value':hex(self.icfg)})
                else:
                    value=self.R[raw['r']]
                    if isinstance(value,UnknownWord):value.consumed()
                    self.icfg=value
                    self.icfg_known=True
                    if machine is not None:self.icfg_unknown_mask=0
                    event({'access':'special-write','register':'icfg','value':hex(self.icfg)})
                self.pc+=size
                self.steps+=1
                adapters['icfg_visible_value_only']+=1
                return
            if adapt_stack and self.pc in (ENTRY,ENTRY+6):
                expected=14 if self.pc==ENTRY else 13
                require(insn.id=='li.sr_u32' and raw=={'s':expected,'n':STACK_TOP},
                        'stack-entry adapter assumptions changed')
                require(self.after_then is None,'stack adapter cannot run in a conditional block')
                if expected==14:
                    self.sp=raw['n']
                else:
                    self.ssp=raw['n']
                # Visible SP/SSP storage only; no mode switching or IRQ banking.
                self.pc+=size
                self.steps+=1
                adapters['entry_li_sp_ssp']+=1
                return
            if adapt_store and self.pc==0x06035636:
                # Upstream Writes merges/drops words by reading old memory,
                # even for a complete 8B store. That bookkeeping read is not
                # a guest architectural read and must not demand initialized RAM.
                require(insn.id=='st.d.l' and raw=={'r':0,'n':1,'s':1},
                        'clock-pair store adapter assumptions changed')
                require(self.after_then is None,'store adapter cannot run in a conditional block')
                self.mem.write((self.R[0]+4)&0xffffffff,struct.pack('<II',self.R[2],self.R[3]))
                self.pc+=size
                self.steps+=1
                adapters['bootstrap_full_width_clock_store']+=1
                return
            if handoff and self.pc in (0x060664e2,0x060664ea,0x060664ee,
                                      0x060664f2,0x060664f6,0x060664fc):
                forms={0x060664e2:('st.d.l',16,8,2),0x060664ea:('st.w',24,4,3),
                       0x060664ee:('st.b',0,1,2),0x060664f2:('st.h',28,2,2),
                       0x060664f6:('st.h',30,2,2),0x060664fc:('st.d.l',32,8,2)}
                expected,offset,width,reg=forms[self.pc]
                require(insn.id==expected and self.after_then is None,'handoff store form changed')
                value=self.R[reg] | (self.R[reg+1]<<32 if width==8 else 0)
                self.mem.write(self.R[1]+offset,(value&((1<<(width*8))-1)).to_bytes(width,'little'))
                self.pc+=size
                self.steps+=1
                adapters['typed_handoff_byte_exact_store']+=1
                return
            if startup_loops and self.pc in (0x06000130,0x0600014a,0x06000166,0x06000182):
                require(insn.id=='rep' and self.repeat is None,'unexpected startup repeat form')
                op=module.check.operands(insn,raw)
                body_size=2 if self.pc==0x06000130 else 4
                require(op['r']==2 and op['n']==body_size,'startup repeat operands changed')
                if self.R[2]==0:
                    if diagnostics and diagnostics.get('zero_rep')=='skip_hypothesis':
                        require(self.pc==0x06000182,'zero REP hypothesis scoped to empty PSRAM copy')
                        self.pc+=size+body_size
                        self.steps+=1
                        adapters['zero_rep_skip_hypothesis']+=1
                        return
                    raise Blocked('unverified_zero_count_rep',instruction_id=insn.id,
                                  note='upstream hardware evidence does not establish count=0 behaviour')
                require(self.R[2]<=10000,'unexpected startup repeat count')
                count=self.R[2]
                if diagnostics and diagnostics.get('rep_chunk'):
                    require(1<=diagnostics['rep_chunk']<=10000,'invalid REP chunk hypothesis')
                    count=min(count,diagnostics['rep_chunk'])
                self.repeat={'start':self.pc+size,'end':self.pc+size+body_size,'register':2,'remaining':count}
                self.pc+=size
                self.steps+=1
                adapters['bounded_startup_rep_controller']+=1
                return
            if startup_loops and self.pc in (0x06000132,0x0600014e,0x0600016a):
                expected={'d':1,'b':3,'n':4} if self.pc==0x06000132 else {'d':3,'b':4,'n':4}
                operands=module.check.operands(insn,raw)
                require(insn.id=='st.w.pin' and all(operands.get(k)==v for k,v in expected.items()),
                        'startup word store changed')
                base,reg=expected['b'],expected['d']
                self.mem.write(self.R[base],struct.pack('<I',self.R[reg]))
                self.R[base]=(self.R[base]+4)&0xffffffff
                self.pc+=size
                self.steps+=1
                adapters['startup_word_store']+=1
                return
            if diagnostics and diagnostics.get('update_repeat') and self.pc==0x060108b8:
                require(insn.id=='rep.i' and module.check.operands(insn,raw)['n']==20,
                        'retained update-clear repeat changed')
                require(self.repeat is None,'nested update clear loop')
                self.repeat={'start':self.pc+size,'end':self.pc+size+2,'register':None,'remaining':20}
                self.pc+=size
                self.steps+=1
                adapters['retained_update_clear_rep_i']+=1
                return
            if diagnostics and diagnostics.get('irq_repeat_return') and self.pc==0x0012cb12:
                op=module.check.operands(insn,raw)
                require(insn.id=='rep.i' and op['n']==2 and op['b']==24 and self.repeat is None,
                        'IRQ-disabled helper repeat changed')
                self.repeat={'start':self.pc+size,'end':self.pc+size+24,'register':None,
                             'remaining':2,'allow_helper_return':True}
                self.pc+=size
                self.steps+=1
                adapters['irq_repeat_return_hypothesis']+=1
                return
            if diagnostics and diagnostics.get('update_repeat') and self.pc==0x060108ba:
                op=module.check.operands(insn,raw)
                require(insn.id=='st.w.pin' and all(op.get(k)==v for k,v in {'d':2,'b':1,'n':4}.items()),
                        'retained update-clear store changed')
                self.mem.write(self.R[1],struct.pack('<I',self.R[2]))
                self.R[1]=(self.R[1]+4)&0xffffffff
                self.pc+=size
                self.steps+=1
                adapters['retained_update_clear_word_store']+=1
                return
            if diagnostics and self.pc==0x060664c4:
                require(insn.id=='st.w','maskrom-init control store changed')
                self.mem.write(self.R[1],struct.pack('<I',self.R[0]))
                self.pc+=size
                self.steps+=1
                adapters['maskrom_init_control_store']+=1
                return
            if diagnostics and self.pc==0x0012b56c:
                require(insn.id=='st.w.idxs','IPMASK control store changed')
                self.mem.write((self.R[0]+(self.R[2]<<2))&0xffffffff,struct.pack('<I',self.R[3]))
                self.pc+=size
                self.steps+=1
                adapters['ipmask_control_store']+=1
                return
            if use_architectural_models and insn.id=='ssync':
                # ssync:上游只有 run4 的 runner 上下文 trace 模型(effect() 按
                # unsupported_runner_context_model 拒绝)。FM-1 run4 硬件捕获:
                # 「flags 0/15 下无任何可见寄存器/标志/sp/rets/scratch 变化,
                # 不证明顺序/排他/事件交付」。按与 csync(k2 模型,仅保 flags)
                # 同形状的无效果模型执行;只有真实执行到 ssync 的前缀才受影响
                # 并在报告里计数(启动前缀首个出现点 0xc716,flushinv_dcache 尾)。
                self.finish_exact(self.pc+size);adapters['ssync_noop']+=1
                return
            if diagnostics and diagnostics.get('immediate_rep_multi_block') and insn.id in ('rep.i','rep'):
                # 通用多指令 rep 体,放在所有针对性 rep 特例之后兜底(实测形状:
                # 0x6010b14 rep.i 4 32 = 4 字节体 ld.bu.pin+st.b.pin 重复 32 次,
                # 字节 memcpy;编码 n=次数、b=体字节数,上游 v3 硬件模型按「次数
                # ×整个体」计总效果)。主循环的 repeat 消费按「PC 到达体末尾即
                # 完成一轮」回卷,与单 store 体同一机制,因此只接受直线体:逐条
                # 解码必须恰好覆盖体字节,每条都是有模型的非控制流指令;体指令
                # 随后走常规解码执行,内存效果=真实执行。分支/调用/条件块开启器/
                # 嵌套 rep 一律诚实拒绝:REP 与体内控制流的交互没有硬件捕获证据,
                # 不臆造。
                # 寄存器计数 rep(ROM 0xc70c `rep r1,4`,flushinv_dcache 的 DCU
                # CMO 失效循环):上游 FM-1 run3 硬件证据「体执行次数=计数寄存
                # 器值,寄存器递减到 0」(v3.py rep 模型),计数必须来自真实寄存
                # 器值——未知/抽象计数值诚实停;计数 0(GAPS.md 明确未测)默认
                # 诚实停,opt-in zero_count_rep_skip 时按 memset 零跳过先例推广
                # (跳过体,见下方)。
                # ROM 编译形状在体后跟 `bnez r计数控,回 rep`:按递减到 0 的模型
                # 执行完后 bnez 自然不跳(两种硬件解释的可观测结果一致)。
                # 并行对体(add.rrr.p+st.w):按一条指令计字节,第二半必须是
                # exact_parallel_stores 已支持的整字存储形状,执行期由该 gate 按
                # 「B 读 A 之前的旧寄存器」硬件语义精确执行(run3 证据)。
                op=module.check.operands(insn,raw)
                if insn.id=='rep.i':
                    body_bytes,count,register=op['b'],op['n'],None
                    require(1<=count<=32,'immediate REP projection has an unsupported context')
                else:
                    body_bytes,register=op['n'],op['r']
                    count=self.R[register]
                    if not isinstance(count,int):
                        raise Blocked('rep_register_count_unknown',register=register,
                                      instruction_id=insn.id)
                    if count==0 and not diagnostics.get('zero_count_rep_skip'):
                        raise Blocked('unverified_zero_count_rep',instruction_id=insn.id,
                                      note='register-count REP; BR28 count=0 semantics unmeasured')
                    require(count<=10000,'register-count REP outside the bounded model')
                require(self.repeat is None and self.after_then is None,
                        'immediate REP projection has an unsupported context')
                walk=self.pc+size;covered=0
                while covered<body_bytes:
                    inner,_,inner_size=self.decode(walk)
                    if inner.id.endswith('.p'):
                        pair,_,pair_size=self.decode(walk+inner_size)
                        base=self.tab.by_id(inner.id[:-2])
                        pure_alu=base.id.startswith(('li.','mov.','add.','sub.','and.','or.','xor.','div.')) or (
                            diagnostics.get('exact_parallel_extensions') and base.id=='ext.b0.u')
                        if not (diagnostics.get('exact_parallel_stores') and pure_alu and
                                pair.id in ('st.w','st.w.l','st.w.modimm','st.w.idxs','st.w.sp','st.w.idx')):
                            raise Blocked('rep_multi_block_body_parallel_pair_unsupported',
                                          instruction_id=inner.id,pair_id=pair.id,address=hex(walk))
                        covered+=inner_size+pair_size;walk+=inner_size+pair_size;continue
                    if not (not inner.block and
                            module.branch_field(inner) is None and
                            not inner.id.startswith(('rep','goto','gotoss','call','callns',
                                                     'pop.pc','rts','lock','idle','wfe','sev',
                                                     'swi','syscall','btbclr','bkpt','hbkpt',
                                                     'cli','sti','rti','rtx','rte')) and
                            inner.id not in ('tbb','tbh') and
                            self.models.get(inner.id) is not None):
                        raise Blocked('rep_multi_block_body_not_straight_line',
                                      instruction_id=inner.id,address=hex(walk))
                    covered+=inner_size;walk+=inner_size
                if covered!=body_bytes:
                    raise Blocked('rep_multi_block_body_unaligned',body_bytes=body_bytes,covered=covered)
                if count==0:
                    # 零计数跳过假设(opt-in zero_count_rep_skip,把 memset 先例
                    # rom_zero_rep_skip/0x4242、0x424c 的零跳过推广到本 gate):
                    # count=0 时体一次也不执行,PC 前进到体后,计数寄存器保持 0
                    # (FM-1 run3「递减到 0」在 0 次执行下的自然延伸)。count=0
                    # 本身 BR28 未测量(GAPS.md「Not tried: rep with a count of
                    # 0」),zero_count_skip_is_assumption 显式标注、不声称硅验证;
                    # 开关关闭仍诚实停(unverified_zero_count_rep)。体形状仍须
                    # 先通过上方与非零计数完全相同的直线体校验。
                    self.pc+=size+body_bytes;self.steps+=1
                    adapters['zero_count_skip_is_assumption']+=1
                    return
                self.repeat={'start':self.pc+size,'end':self.pc+size+body_bytes,'register':register,
                             'remaining':count,'multi_block':True}
                self.pc+=size;self.steps+=1
                adapters['immediate_rep_multi_block_projection' if insn.id=='rep.i'
                         else 'register_rep_multi_block_projection']+=1
                return
            # Context-specific control models must never pretend to be hardware.
            if insn.id.startswith(('rep','lock','idle','wfe','sev','swi','syscall',
                                   'btbclr','bkpt','hbkpt','cli','sti','rti','rtx','rte')):
                raise Blocked('unsupported_runtime_instruction',instruction_id=insn.id)
            return super().step()

    c=CPU()
    c.mem=Memory()
    if handoff:
        for address,data in handoff['seeds']:
            require(c.mem.region(address,len(data)) is not None,'seed outside declared RAM')
            c.mem.ram.update((address+i,value) for i,value in enumerate(data))
    c.pc=diagnostics.get('entry_pc',ENTRY) if diagnostics else ENTRY
    c.R=[0]*16
    c.R[0]=r0
    c.sp=c.rets=c.icfg=0
    if diagnostics:
        if 'initial_rets' in diagnostics:
            value=diagnostics['initial_rets']
            require(isinstance(value,int) and value&~0x07fffffe==0,'initial RETS must fit writable bits26..1')
            c.rets=value
        if 'initial_gprs' in diagnostics:
            values=diagnostics['initial_gprs']
            require(len(values)==16 and all(isinstance(v,int) and 0<=v<=0xffffffff for v in values),
                    'diagnostic GPRs must contain16 unsigned words')
            c.R=list(values)
        if diagnostics.get('stop_on_rom_return'):
            require(rom is not None,'ROM return sentinel requires an explicit ROM mapping')
            c.rets=ROM_RETURN
        if diagnostics.get('irq_context'):
            ctx=diagnostics['irq_context']
            require(set(ctx)=={'reti','rets','psr'} and all(0<=v<=0xffffffff for v in ctx.values()),
                    'explicit synthetic RETI/RETS/PSR required')
            c.reti=ctx['reti'];c.rets=ctx['rets'];c.irq_psr=ctx['psr'];c.flags=ctx['psr']&15
        c.sp=diagnostics.get('initial_sp',0)
        c.icfg_known=diagnostics.get('icfg_initial') is not None
        if c.icfg_known:
            c.icfg=diagnostics['icfg_initial']
        for pc in diagnostics.get('predecode_pcs',()):c.decode(pc)
    trace=[]
    completed=0
    stop=None
    if machine is not None:
        cpu0=c
        def factory():
            cpu=CPU();cpu.mem=cpu0.mem
            return cpu
        machine.attach(c,factory,hardware)
    for _ in range(budget):
        if machine is not None:c=machine.select()
        if hardware is not None and diagnostics and c.pc==diagnostics.get('stop_before_pc'):
            stop={'kind':'diagnostic_milestone_reached','address':hex(c.pc)}
            break
        if diagnostics and diagnostics.get('stop_on_rom_return') and c.pc==ROM_RETURN:
            stop={'kind':'isolated_function_return','sentinel':hex(ROM_RETURN)}
            break
        row={'pc':hex(c.pc)}
        if machine is not None:row['core']=c.core_id
        try:
            if hardware is not None and hardware.debug is not None:hardware.debug.check_fetch(c.pc,1,c.core_id)
            insn,raw,size=c.decode(c.pc)
            if hardware is not None and hardware.debug is not None:
                extent=size
                if insn.id.endswith('.p'):extent+=c.decode(c.pc+size)[2]
                hardware.debug.check_fetch(c.pc,extent,c.core_id)
            row.update(instruction_id=insn.id,text=module.codec.format(insn,raw),
                       bytes=c.mem.read(c.pc,size).hex())
            if machine is not None and insn.id=='idle' and machine.idle_wait_projection:
                # IDLE semantics unmeasured: the core stops issuing until a
                # real delivery wakes it. No wake injection, no held-bit edit.
                machine.enter_idle_wait(c)
                raise CoreStall('core_idle_waiting',core=c.core_id)
            c.step()
            completed+=1
            row['next_pc']=hex(c.pc)
            if machine is not None:
                if c.core_id==1:hardware.secondary_cpu_executed=True
                machine.after_step(c,int(row['pc'],16))
            if hardware is not None:hardware.advance(c.icfg)
        except CoreStall as e:
            machine.note_stall(c,e.details);row['stall']=e.details
            machine.after_step()
            try:hardware.advance(c.icfg)
            except Blocked as pending:stop=pending.details
        except Blocked as e:
            stop=e.details
            if row.get('instruction_id','').startswith('st.') and stop['kind']=='uninitialized_memory':
                stop={**stop,'kind':'upstream_store_bookkeeping_read',
                      'note':'upstream store model reads previous words to merge/drop writes; not a guest load'}
        except IrqDeliveryRequest as request:
            if machine is None:
                raise ValueError('delivery request without the opt-in machine entry projection')
            # The requesting advance may fire for the peer core; deliver to
            # the target core, whose current PC is the interrupted context.
            if request.details['irq']==3:
                if not machine.core_tick_entry:
                    raise ValueError('core tick delivery request without the opt-in machine entry projection')
                machine.deliver_core_tick(request.details['core'])
                row['core_tick_entry']=request.details
            else:
                if not machine.software_irq_entry:
                    raise ValueError('delivery request without the opt-in machine entry projection')
                target=machine.cores.get(request.details['core'],c)
                machine.deliver_software_irq(target,request.details['irq'])
                row['software_irq_entry']=request.details
        except (RuntimeError,ValueError,KeyError,IndexError) as e:
            stop={'kind':'upstream_simulator_failure','error_type':type(e).__name__,'message':str(e)}
        trace.append(row)
        if len(trace)>64:
            trace.pop(0)
        if stop is not None:
            break
    if stop is None:
        stop={'kind':'instruction_budget'}
    result={'name':name,'entry':hex(diagnostics.get('entry_pc',ENTRY) if diagnostics else ENTRY),'completed_steps':completed,'stop_pc':hex(c.pc),
        'stop':stop,'assumptions':{
            'cpu0_single_thread_no_interrupts':True,
            'unobserved_initial_gprs_flags_sp_rets_icfg':'zero placeholders, not a measured boot state',
            'initial_r0':hex(diagnostics['initial_gprs'][0] if diagnostics and 'initial_gprs' in diagnostics else r0),'r0_boot_args_pointer_is_hypothesis':r0==BOOT_ARGS,
            'pll0_con0_read_hypothesis':hex(pll) if pll is not None else None,
            'writable_ram_windows_are_scoped_harness_regions_not_a_full_chip_map':True,
            'uninitialized_memory_reads_stop':True},
        'instruction_adapters':dict(adapters),'memory_events':events,
        'last_instructions':trace,'last_registers':{'r':[word(x) for x in c.R],
            'sp':hex(c.sp) if c.sp_known else 'unknown','ssp':hex(c.ssp) if c.ssp is not None else 'unknown','rets':hex(c.rets),
            'icfg':hex(c.icfg) if c.icfg is not None and (machine is None or c.icfg_known) else 'unknown'}}
    if machine is not None:
        result['stop_core']=c.core_id;result['machine']=machine.report()
        result['assumptions']['cpu0_single_thread_no_interrupts']=False
        result['assumptions']['deterministic_dual_core_interleaving_without_irq_entry']=True
    if use_architectural_models:
        result['instruction_model_corrections']={'k2_source_sha256':K2_SHA,
            'runner_scratch_pc_store_removed':True,'runner_rets_normalization_removed':True,
            'architectural_inner_models':sorted(architectural_model_names),
            'legitimate_stores_are_not_filtered_by_value':True,
            'other_unsupported_contexts_remain_strict':True}
        if adapters.get('ssync_noop'):
            # 只有真实执行到 ssync 的前缀才出现该键(legacy 前缀不经过 ssync,
            # 报告不变)。
            result['instruction_model_corrections']['ssync_noop_model']=(
                'fm1 run-4 capture: no visible register/flag/sp/rets change (flags 0 and 15); '
                'ordering/barrier semantics not modeled; executions: %d'%adapters['ssync_noop'])
    if handoff:
        result['assumptions']['typed_handoff']=handoff['description']
        if startup_loops:
            result['assumptions']['nonzero_rep_counts']='full countdown model; upstream hardware tested counts1..7 on AC791N; large counts/BR28 unverified'
            if diagnostics and diagnostics.get('rep_chunk'):
                result['assumptions']['nonzero_rep_counts']='counterfactual REP chunks of '+str(diagnostics['rep_chunk'])+'; not verified BR28 behaviour'
        result['memory_event_counts']=dict(event_counts)
        result['memory_events_truncated']=sum(event_counts.values())>len(events)
        result['memory_snapshots']={}
        for label,address,length in handoff['snapshots']:
            values=[c.mem.ram.get(a) for a in range(address,address+length)]
            snap={'address':hex(address),'size':length,'initialized_bytes':sum(v is not None for v in values)}
            if all(v is not None for v in values):
                snap['sha256']=hashlib.sha256(bytes(values)).hexdigest()
            if length<=40:
                snap['bytes_with_unknowns']=''.join('??' if v is None else f'{v:02x}' for v in values)
            result['memory_snapshots'][label]=snap
    if diagnostics:
        result['assumptions']['diagnostics']=dict(diagnostics)
        result['assumptions']['unobserved_initial_gprs_flags_sp_rets_icfg']='zero placeholders except explicit diagnostic overrides; not a measured boot state'
        result['milestones']=milestones
    if rom is not None:
        result['rom_mapping']={'start':0,'end_exclusive':len(rom),'sha256':ROM_SHA,
                               'readonly':True,'silicon_extent_not_established':True}
        result['completed_model_steps_in_rom']=rom_steps['completed_model_steps_in_rom']
        result['assumptions']['rom_rep_model']='strict unless explicitly selected nonzero countdown / zero skip hypothesis'
    if hardware is not None:
        result['peripherals']=hardware.report()
    if diagnostics and diagnostics.get('irq_context'):
        result['last_registers']['reti']=hex(c.reti)
        result['last_registers']['psr_projection']=hex((c.irq_psr&~15)|(c.flags&15))
        result['assumptions']['irq_execution']='isolated post-entry software frame; architectural IRQ entry/RTI, bank selection and nesting not implemented'
    return result


def check_known_blockers(report):
    expected=[('unmodified_upstream',0,ENTRY,'upstream_simulator_failure',None),
        ('stack_adapter_only',6,0x0603561e,'unmapped_memory',PLL),
        ('pll_bit0_clear_store_context',11,0x06035636,'upstream_store_bookkeeping_read',BOOT_ARGS+4),
        ('pll_bit0_clear_store_adapter',13,0x0001fe14,'unmapped_instruction_fetch',0x0001fe14),
        ('pll_bit0_set_r0_hypothesis',11,0x060664d8,'uninitialized_memory',BOOT_ARGS+4)]
    require(len(report['scenarios'])==len(expected),'scenario count changed')
    for result,(name,steps,pc,kind,address) in zip(report['scenarios'],expected):
        require((result['name'],result['completed_steps'],result['stop_pc'],result['stop']['kind'])
                ==(name,steps,hex(pc),kind),'known boot-prefix result changed: '+name)
        if address is not None:
            require(result['stop']['address']==hex(address),'wrong blocker address: '+name)
    strict=report['scenarios'][1]
    require(strict['last_registers']['sp']==hex(STACK_TOP-16),'bootstrap stack depth mismatch')
    clear=report['scenarios'][3]
    require(clear['instruction_adapters']=={'entry_li_sp_ssp':2,'bootstrap_full_width_clock_store':1},
            'unexpected instruction adaptation')
    writes=[e for e in clear['memory_events'] if e.get('address')==hex(BOOT_ARGS+4) and e['access']=='write']
    require(len(writes)==1 and writes[0]['bytes']==struct.pack('<II',24_000_000,48_000_000).hex(),
            'clock-pair output mismatch')
    set_=report['scenarios'][4]
    require(set_['last_registers']['r'][0]==hex(BOOT_ARGS) and set_['last_registers']['sp']==hex(STACK_TOP),
            'boot argument register/stack not restored')
    report['known_blocker_regression_passed']=True


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fetch-simulator',action='store_true')
    parser.add_argument('--firmware',type=Path,default=ROOT/'firmware/QCY_H3S_7018F_1T2_release_note_V1.1.8_011.ufw')
    parser.add_argument('--max-steps',type=int,default=100_000)
    parser.add_argument('--check-known-blockers',action='store_true',help='assert exact archived boot-prefix results')
    parser.add_argument('--report',type=Path)
    args=parser.parse_args()
    require(1<=args.max_steps<=1_000_000,'instruction budget must be 1..1000000')
    app=application(args.firmware)
    module=upstream(args.fetch_simulator)
    scenarios=[explore(module,app,name='unmodified_upstream',budget=args.max_steps),
        explore(module,app,name='stack_adapter_only',adapt_stack=True,budget=args.max_steps),
        explore(module,app,name='pll_bit0_clear_store_context',adapt_stack=True,pll=0,budget=args.max_steps),
        explore(module,app,name='pll_bit0_clear_store_adapter',adapt_stack=True,adapt_store=True,pll=0,budget=args.max_steps),
        explore(module,app,name='pll_bit0_set_r0_hypothesis',adapt_stack=True,pll=1,r0=BOOT_ARGS,budget=args.max_steps)]
    report={'format_version':1,'simulator_commit':PIN,'reference_sdk_commit':SDK_PIN,
        'firmware_sha256':OFFICIAL_SHA,'application_sha256':APP_SHA,
        'device_accessed':False,'firmware_created':False,'instruction_budget_per_scenario':args.max_steps,
        'architectural_reset_simulated':False,'whole_h3s_boot_simulated':False,
        'suitable_for_flash_approval_alone':False,'scenarios':scenarios}
    if args.check_known_blockers:
        check_known_blockers(report)
    text=json.dumps(report,indent=2)+'\n'
    if args.report:
        args.report.parent.mkdir(parents=True,exist_ok=True)
        args.report.write_text(text)
    # Exit zero means exploration finished, NOT successful headset boot.
    for result in scenarios:
        print(result['name'],result['completed_steps'],'steps;',result['stop_pc'],result['stop'])
    if not args.report:
        print(text,end='')


if __name__=='__main__':
    main()
