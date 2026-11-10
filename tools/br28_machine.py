"""Deterministic shared-memory CPU scheduler; explicit CPU1 boot conditions.

CPU1 runs actual ROM/vector code. No ready flag or task selection injection.
"""
from probe_boot import Blocked
from probe_pi32v2 import require
from br28_unknown import UnknownWord, word


class CoreStall(Exception):
    def __init__(self,reason,**details):self.details={'kind':reason,**details}


class DualCoreMachine:
    def __init__(self,*,cpu1_reset_pc=None,cpu1_icfg=None,irq1_config=None,ipmask1=None,
                 bootstrap_rti=False,unknown_task_gprs=False):
        if cpu1_reset_pc is not None:require(cpu1_reset_pc==0,'only explicit ROM reset-vector0 supported')
        if cpu1_icfg is not None:require(isinstance(cpu1_icfg,int) and 0<=cpu1_icfg<=0xffffffff,'invalid CPU1 ICFG fixture')
        self.cpu1_reset_pc=cpu1_reset_pc;self.cpu1_icfg=cpu1_icfg
        self.irq1_config=irq1_config;self.ipmask1=ipmask1;self.bootstrap_rti=bootstrap_rti
        self.unknown_task_gprs=unknown_task_gprs
        self.cores={};self.last_core=1;self.stalls=0;self.launches=0;self.events=[];self.event_count=0
        self.boot_anchors=[];self.boot_anchor_count=0

    def record_event(self,event):
        self.event_count+=1;self.events.append(event)
        if len(self.events)>32:self.events.pop(16)

    def attach(self,cpu0,factory,hardware):
        require(not self.cores,'machine cannot be reused across execution sessions')
        self.cores[0]=cpu0;self.factory=factory;self.hardware=hardware
        hardware.enable_multicore(self.irq1_config,self.ipmask1,self.launch)

    def launch(self,value):
        if value!=1 or 1 in self.cores:raise Blocked('unsupported_cpu1_release_transition',value=hex(value))
        if self.cpu1_reset_pc is None:raise Blocked('cpu1_reset_vector_unknown')
        cpu=self.factory();cpu.core_id=1;cpu.pc=self.cpu1_reset_pc
        cpu.R=[0]*16  # placeholders: actual ROM clears all before use, not measured reset values
        cpu.sp=0;cpu.sp_known=False;cpu.ssp=cpu.usp=cpu.reti=None;cpu.rets=0
        cpu.icfg=self.cpu1_icfg;cpu.icfg_known=self.cpu1_icfg is not None
        cpu.bootstrap_context=True;cpu.opaque_source=None;cpu.mutable_decode_bytes=None
        self.cores[1]=cpu;self.launches+=1
        self.record_event({'kind':'cpu1_released','entry':hex(cpu.pc),'ready_flag_written':False})

    def select(self):
        core=1-self.last_core
        if core not in self.cores:core=0
        self.last_core=core;self.hardware.current_core=core
        self.hardware.core_icfg={k:c.icfg if c.icfg_known else None for k,c in self.cores.items()}
        return self.cores[core]

    def after_step(self,cpu=None,before_pc=None):
        if cpu is not None and cpu.core_id==1 and before_pc in (0,0xa144,0xc5fe,0xc602,0x12caf4,0x12cafa,0x12cb00):
            self.boot_anchor_count+=1
            if len(self.boot_anchors)<16:
                self.boot_anchors.append({'pc':hex(before_pc),'next_pc':hex(cpu.pc),
                    'r0':word(cpu.R[0]),'rets':hex(cpu.rets)})
        self.hardware.core_icfg={k:c.icfg if c.icfg_known else None for k,c in self.cores.items()}

    def note_stall(self,cpu,details):
        self.stalls+=1
        self.record_event({'core':cpu.core_id,**details})

    def note_bank_write(self,cpu,name,value):
        setattr(cpu,'last_explicit_'+name,value)
        if cpu.sp_known and cpu.sp!=value:cpu.sp_known=False

    def note_sp_write(self,cpu):
        # Alias/writeback mode is unresolved. Equal values are unambiguous;
        # different bank values become unknown until an explicit bank write.
        for name in ('usp','ssp'):
            value=getattr(cpu,name)
            if value is not None and value!=cpu.sp:setattr(cpu,name,None)

    def note_memory_write(self,address,data):
        for target,size in ((0x1265f5,1),(0x19ff54,4)):
            if address<=target and target+size<=address+len(data):
                value=int.from_bytes(data[target-address:target-address+size],'little')
                self.record_event({'kind':'guest_notification_write','core':self.hardware.current_core,
                    'pc':hex(self.cores[self.hardware.current_core].pc),'address':hex(target),'value':hex(value)})

    def return_bootstrap(self,cpu):
        if not self.bootstrap_rti:raise Blocked('cpu1_bootstrap_rti_contract_unknown')
        require(cpu.core_id==1 and cpu.pc==0x12cb06 and cpu.bootstrap_context,
                'bootstrap contract cannot be reused for task/IRQ RTI')
        require([a['pc'] for a in self.boot_anchors]==[hex(p) for p in (0,0xa144,0xc5fe,0xc602,0x12caf4,0x12cafa,0x12cb00)],
                'conditional RTI requires actual ROM vector and exact trampoline path')
        require(self.boot_anchors[1]['r0']=='0x1' and self.boot_anchors[2]['r0']=='0x12caf4' and
                self.boot_anchors[3]['next_pc']=='0x12caf4' and self.boot_anchors[3]['rets']=='0xc604',
                'CPU1 identity/vector/CALL provenance differs')
        require(cpu.reti==0x12cad4 and cpu.sp==cpu.usp==0x124840,'actual QCY bootstrap registers differ')
        if not cpu.icfg_known:raise Blocked('cpu1_bootstrap_icfg_fixture_unknown')
        # Conditional bootstrap return: switch to the established USP, consume
        # the bootstrap token. SSP writeback and IRQ gates remain unresolved.
        cpu.unresolved_ssp_candidates=(getattr(cpu,'last_explicit_ssp',cpu.ssp),cpu.sp)
        cpu.sp=cpu.usp;cpu.ssp=None;cpu.icfg_unknown_mask=0x300;cpu.icfg_known=False
        cpu.bootstrap_context=False;cpu.active_context='bootstrap_return_thread_projection'
        cpu.pc=cpu.reti;cpu.steps+=1
        self.record_event({'kind':'conditional_bootstrap_rti','core':1,'pc_after':hex(cpu.pc),
                            'sp_after':hex(cpu.sp),'ssp_after':'unknown','icfg_gate_bits':'unknown'})

    def report(self):
        return {'schedule':'round-robin one instruction per runnable core; no cycle accuracy',
                'cpu1_reset_vector_fixture':self.cpu1_reset_pc,'cpu1_initial_icfg_fixture':self.cpu1_icfg,
                'reset_vector_release_causality_measured':False,'bootstrap_rti_projection':self.bootstrap_rti,
                'bootstrap_mode_or_ssp_writeback_certified':False,'launches':self.launches,'stalls':self.stalls,
                'unknown_task_gprs_preserved':self.unknown_task_gprs,
                'bootstrap_rti_icfg_policy':'other visible bits preserve explicit initial fixture; gates8/9 unknown until guest mask',
                'bootstrap_mode_transition_assumption':'startup exception to thread at the exact QCY trampoline only',
                'events':self.events,'events_truncated':self.event_count>len(self.events),
                'cpu1_boot_anchors':self.boot_anchors,'boot_anchors_truncated':self.boot_anchor_count>len(self.boot_anchors),
                'ready_injected':False,
                'cores':{str(k):{'pc':word(c.pc),'steps':c.steps,'r':[word(v) for v in c.R],
                    **({'unknown_gpr_sources':{str(i):[word(a) for a in v.origins] for i,v in enumerate(c.R)
                                              if isinstance(v,UnknownWord)}} if self.unknown_task_gprs else {}),
                    'sp':word(c.sp) if c.sp_known else 'unknown','ssp':word(c.ssp),'usp':word(c.usp),'reti':word(c.reti),
                    'last_explicit_ssp':word(getattr(c,'last_explicit_ssp',None)),
                    'last_explicit_usp':word(getattr(c,'last_explicit_usp',None)),
                    'rets':word(c.rets),'icfg':word(c.icfg) if c.icfg_known else 'unknown',
                    'icfg_value_projection':word(c.icfg),
                    'ssp_after_bootstrap_candidates':[word(v) for v in getattr(c,'unresolved_ssp_candidates',())],
                    'bootstrap_context':c.bootstrap_context} for k,c in self.cores.items()}}
