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

OFFICIAL_SHA='65cafc7f9a7545c5c30701d141c77db334e37065589e42c51306132c94c879af'
APP_SHA='18f3d573a5b1a15f488a6803ce4f828634e602015c85e92d205bbe62d7060041'
ENTRY=0x06000100
PLL=0x00fd4600
BOOT_ARGS=0x0019fc28
STACK_TOP=0x00123840
SDK_PIN='67328f4741c69993c88e504c67377b196c724f97'


class Blocked(RuntimeError):
    def __init__(self,kind,**details):
        self.details={'kind':kind,**details}
        super().__init__(json.dumps(self.details,sort_keys=True))


def application(path):
    raw=path.read_bytes()
    require(hashlib.sha256(raw).hexdigest()==OFFICIAL_SHA,'requires exact official 0.1.1.8 UFW')
    row=decode(raw[64:144])
    offset,length=struct.unpack_from('<II',row,8)
    require((offset,length)==(0x600,0xe6000),'unexpected flash entry')
    flash=raw[offset:offset+length]
    app_length=struct.unpack_from('<I',decode(flash[0x2000:0x2020]),8)[0]
    app=sfc(flash[0x2020:0x2020+app_length])[0x100:0x100+842654]
    require(hashlib.sha256(app).hexdigest()==APP_SHA,'unexpected application bytes')
    return app


def explore(module,app,*,name,adapt_stack=False,adapt_store=False,pll=None,r0=0,budget=100_000,
            handoff=None,startup_loops=False):
    events=[]
    adapters=Counter()
    event_counts=Counter()

    def event(row):
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

        def region(self,address,size):
            for low,high,kind in self.regions:
                if low<=address and address+size<=high:
                    return kind
            return None

        def read(self,address,size):
            require(size>0 and 0<=address<=0xffffffff and address+size<=0x100000000,
                    'invalid memory request')
            if ENTRY<=address and address+size<=ENTRY+len(app):
                return app[address-ENTRY:address-ENTRY+size]
            for base,data in handoff['readonly'] if handoff else ():
                if base<=address and address+size<=base+len(data):
                    return data[address-base:address-base+size]
            detail={'access':self.operation,'address':hex(address),'size':size}
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
            region=self.region(address,len(data))
            if not region:
                raise Blocked('unmapped_or_readonly_write',access='write',address=hex(address),size=len(data))
            self.ram.update((address+i,byte) for i,byte in enumerate(data))
            event({'access':'write','address':hex(address),'bytes':data.hex(),'region':region})

    class CPU(module.CPU):
        ssp=0
        repeat=None

        def decode(self,pc):
            if pc%2:
                raise Blocked('unaligned_instruction_fetch',address=hex(pc))
            old=self.mem.operation
            self.mem.operation='fetch'
            try:
                return super().decode(pc)
            finally:
                self.mem.operation=old

        def step(self):
            self.step_body()
            if self.repeat and self.pc==self.repeat['end']:
                self.R[2]-=1
                if self.R[2]:
                    self.pc=self.repeat['start']
                else:
                    self.repeat=None

        def step_body(self):
            insn,raw,size=self.decode(self.pc)
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
                    raise Blocked('unverified_zero_count_rep',instruction_id=insn.id,
                                  note='upstream hardware evidence does not establish count=0 behaviour')
                require(self.R[2]<=10000,'unexpected startup repeat count')
                self.repeat={'start':self.pc+size,'end':self.pc+size+body_size}
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
    c.pc=ENTRY
    c.R=[0]*16
    c.R[0]=r0
    c.sp=c.rets=c.icfg=0
    trace=[]
    completed=0
    stop=None
    for _ in range(budget):
        row={'pc':hex(c.pc)}
        try:
            insn,raw,size=c.decode(c.pc)
            row.update(instruction_id=insn.id,text=module.codec.format(insn,raw),
                       bytes=c.mem.read(c.pc,size).hex())
            c.step()
            completed+=1
            row['next_pc']=hex(c.pc)
        except Blocked as e:
            stop=e.details
            if row.get('instruction_id','').startswith('st.') and stop['kind']=='uninitialized_memory':
                stop={**stop,'kind':'upstream_store_bookkeeping_read',
                      'note':'upstream store model reads previous words to merge/drop writes; not a guest load'}
        except (RuntimeError,ValueError,KeyError,IndexError) as e:
            stop={'kind':'upstream_simulator_failure','error_type':type(e).__name__,'message':str(e)}
        trace.append(row)
        if len(trace)>64:
            trace.pop(0)
        if stop is not None:
            break
    if stop is None:
        stop={'kind':'instruction_budget'}
    result={'name':name,'entry':hex(ENTRY),'completed_steps':completed,'stop_pc':hex(c.pc),
        'stop':stop,'assumptions':{
            'cpu0_single_thread_no_interrupts':True,
            'unobserved_initial_gprs_flags_sp_rets_icfg':'zero placeholders, not a measured boot state',
            'initial_r0':hex(r0),'r0_boot_args_pointer_is_hypothesis':r0==BOOT_ARGS,
            'pll0_con0_read_hypothesis':hex(pll) if pll is not None else None,
            'writable_ram_windows_are_scoped_harness_regions_not_a_full_chip_map':True,
            'uninitialized_memory_reads_stop':True},
        'instruction_adapters':dict(adapters),'memory_events':events,
        'last_instructions':trace,'last_registers':{'r':[hex(x) for x in c.R],
            'sp':hex(c.sp),'ssp':hex(c.ssp),'rets':hex(c.rets),'icfg':hex(c.icfg)}}
    if handoff:
        result['assumptions']['typed_handoff']=handoff['description']
        if startup_loops:
            result['assumptions']['nonzero_rep_counts']='full countdown model; upstream hardware tested counts1..7 on AC791N; large counts/BR28 unverified'
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
