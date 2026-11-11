"""Deterministic shared-memory CPU scheduler; explicit CPU1 boot conditions.

CPU1 runs actual ROM/vector code. No ready flag or task selection injection.
"""
from probe_boot import Blocked
from probe_pi32v2 import require
from br28_unknown import UnknownWord, word
from br28_icfg import IcfgState, IcfgValue


# Counterfactual outcomes of one exact QCY startup return. No default, no
# generic RTI, and no inference of a hardware mode from the software frame.
FIRST_TASK_RETURN_HYPOTHESES={
    'unresolved_stack':(False,None),
    'unknown_gate8':(True,None),
    'closed_gate8':(True,False),
    'open_gate8':(True,True),
}
CORE1_IDLE_DELAY_LIMIT=4096
FIRST_TASK_SETUP=(
    (0x6066106,'cli',{}),(0x6066108,'lockset',{}),(0x606610a,'csync',{}),
    (0x606610c,'li.u32',{'r':4,'n':0x126c5c}),
    (0x6066112,'mov.r_sr',{'r':1,'s':6}),
    (0x6066116,'ld.w.idxs',{'r':4,'s':4,'t':1}),
    (0x606611a,'mov.rr',{'x':0,'y':4}),(0x606611c,'call.l',{'n':137}),
    (0x6066120,'ld.w',{'d':1,'b':4,'n':0}),
    (0x6066122,'mov.sr_r',{'r':1,'s':14}),
    (0x6066126,'pop.srl',{'m':41}),(0x6066128,'pop.rmask',{'n':65535}),
)


class CoreStall(Exception):
    def __init__(self,reason,**details):self.details={'kind':reason,**details}


class DualCoreMachine:
    def __init__(self,*,cpu1_reset_pc=None,cpu1_icfg=None,irq1_config=None,ipmask1=None,
                 bootstrap_rti=False,unknown_task_gprs=False,partial_icfg=False,
                 initial_icfg_masks=(0xffffffff,0xffffffff),bootstrap_status_preserve_mask=0,
                 first_task_return_hypothesis=None,defer_core1_first_idle=False,software_irq_entry=False,
                 software_irq_entry_banks=False,idle_wait_projection=False,software_irq_rti=False,
                 core_tick_entry=False,core_tick_entry_banks=False,core_tick_rti=False,
                 gpio_out_readback=False,cpu1_control_kick_wake=False):
        if cpu1_reset_pc is not None:require(cpu1_reset_pc==0,'only explicit ROM reset-vector0 supported')
        if cpu1_icfg is not None:require(isinstance(cpu1_icfg,int) and 0<=cpu1_icfg<=0xffffffff,'invalid CPU1 ICFG fixture')
        self.cpu1_reset_pc=cpu1_reset_pc;self.cpu1_icfg=cpu1_icfg
        self.irq1_config=irq1_config;self.ipmask1=ipmask1;self.bootstrap_rti=bootstrap_rti
        self.unknown_task_gprs=unknown_task_gprs
        require(len(initial_icfg_masks)==2 and all(isinstance(v,int) and 0<=v<=0xffffffff for v in initial_icfg_masks),
                'two unsigned ICFG known-mask fixtures required')
        require(isinstance(bootstrap_status_preserve_mask,int) and 0<=bootstrap_status_preserve_mask<=0xffffffff and
                not bootstrap_status_preserve_mask&0x300,'bootstrap status preservation cannot set gate knowledge')
        self.partial_icfg=partial_icfg;self.initial_icfg_masks=initial_icfg_masks
        self.bootstrap_status_preserve_mask=bootstrap_status_preserve_mask
        require(first_task_return_hypothesis is None or (first_task_return_hypothesis in FIRST_TASK_RETURN_HYPOTHESES and
                partial_icfg and unknown_task_gprs),'first-task return comparison requires explicit partial ICFG and abstract GPRs')
        self.first_task_return_hypothesis=first_task_return_hypothesis
        self.first_task_calls=set();self.first_task_returns={}
        self.first_task_setup={}
        require(not defer_core1_first_idle or first_task_return_hypothesis is not None,
                'CPU1 issue delay requires explicit first-task comparison')
        self.defer_core1_first_idle=defer_core1_first_idle;self.deferred_core1_turns=0
        require(isinstance(software_irq_entry,bool),'software IRQ entry projection is opt-in boolean')
        self.software_irq_entry=software_irq_entry;self.software_irq_deliveries=[]
        require(isinstance(software_irq_entry_banks,bool),'software IRQ entry banks are an opt-in assumption')
        require(not software_irq_entry_banks or software_irq_entry,'entry bank assumption requires the entry projection')
        self.software_irq_entry_banks=software_irq_entry_banks
        self.idle_wait_projection=idle_wait_projection
        self.idle_wait_cores=set()
        require(not idle_wait_projection or software_irq_entry,
                'IDLE wait requires the software IRQ entry projection for wake-up')
        self.software_irq_rti=software_irq_rti
        require(not software_irq_rti or software_irq_entry,
                'IRQ RTI projection requires the software IRQ entry projection')
        require(isinstance(core_tick_entry,bool),'core tick entry projection is opt-in boolean')
        self.core_tick_entry=core_tick_entry;self.core_tick_deliveries=[]
        require(isinstance(core_tick_entry_banks,bool),'core tick entry banks are an opt-in assumption')
        require(not core_tick_entry_banks or core_tick_entry,'core tick entry bank assumption requires the entry projection')
        self.core_tick_entry_banks=core_tick_entry_banks
        self.core_tick_rti=core_tick_rti
        require(not core_tick_rti or core_tick_entry,'core tick RTI projection requires the entry projection')
        require(isinstance(gpio_out_readback,bool),'GPIO OUT read-back is an opt-in boolean')
        self.gpio_out_readback=gpio_out_readback
        require(isinstance(cpu1_control_kick_wake,bool),'CPU1 control kick wake is an opt-in boolean')
        require(not cpu1_control_kick_wake or idle_wait_projection,
                'CPU1 control kick wake requires the IDLE wait projection')
        self.cpu1_control_kick_wake=cpu1_control_kick_wake;self.seen_cpu1_control_kicks=0
        self.cores={};self.last_core=1;self.stalls=0;self.launches=0;self.events=[];self.event_count=0
        self.boot_anchors=[];self.boot_anchor_count=0

    def record_event(self,event):
        self.event_count+=1;self.events.append(event)
        if len(self.events)>32:self.events.pop(16)

    def attach(self,cpu0,factory,hardware):
        require(not self.cores,'machine cannot be reused across execution sessions')
        self.cores[0]=cpu0;self.factory=factory;self.hardware=hardware
        if self.partial_icfg:self.init_icfg(cpu0)
        hardware.enable_multicore(self.irq1_config,self.ipmask1,self.launch)

    def init_icfg(self,cpu):
        cpu.icfg_state=IcfgState(cpu.core_id,cpu.icfg or 0,
                                self.initial_icfg_masks[cpu.core_id] if cpu.icfg_known else 0)
        self.sync_icfg(cpu)

    def sync_icfg(self,cpu):
        state=cpu.icfg_state;cpu.icfg=state.value;cpu.icfg_known=state.known==0xffffffff
        cpu.icfg_unknown_mask=(~state.known)&0xffffffff

    def icfg_demands(self):
        return {k:c.icfg_state.gate_demand() if self.partial_icfg else c.icfg if c.icfg_known else None
                for k,c in self.cores.items()}

    def launch(self,value):
        if value!=1 or 1 in self.cores:raise Blocked('unsupported_cpu1_release_transition',value=hex(value))
        if self.cpu1_reset_pc is None:raise Blocked('cpu1_reset_vector_unknown')
        cpu=self.factory();cpu.core_id=1;cpu.pc=self.cpu1_reset_pc
        cpu.R=[0]*16  # placeholders: actual ROM clears all before use, not measured reset values
        cpu.sp=0;cpu.sp_known=False;cpu.ssp=cpu.usp=cpu.reti=None;cpu.rets=0
        cpu.icfg=self.cpu1_icfg;cpu.icfg_known=self.cpu1_icfg is not None
        if self.partial_icfg:self.init_icfg(cpu)
        cpu.bootstrap_context=True;cpu.opaque_source=None;cpu.mutable_decode_bytes=None
        self.cores[1]=cpu;self.launches+=1
        self.record_event({'kind':'cpu1_released','entry':hex(cpu.pc),'ready_flag_written':False})

    def select(self):
        kicks=getattr(self.hardware,'cpu1_control_kick_writes',None)
        if self.cpu1_control_kick_wake and kicks is not None and kicks!=self.seen_cpu1_control_kicks:
            self.seen_cpu1_control_kicks=kicks
            self.wake_cpu1_control_kick()
        core=1-self.last_core
        if core not in self.cores:core=0
        if core in self.idle_wait_cores:
            other=1-core
            if other in self.cores and other not in self.idle_wait_cores:core=other
        if (core==1 and self.defer_core1_first_idle and 1 in self.first_task_returns and
                self.cores[1].pc==0x12c5ca and self.deferred_core1_turns<CORE1_IDLE_DELAY_LIMIT):
            # Explore another bounded issue order. Do not execute/emulate IDLE,
            # alter the guest's held-core bit, inject a wake, or skip IRQ checks.
            core=0;self.deferred_core1_turns+=1
        self.last_core=core;self.hardware.current_core=core
        self.hardware.core_icfg=self.icfg_demands()
        return self.cores[core]

    def enter_idle_wait(self,cpu):
        # IDLE semantics are unmeasured; this projection only stops the core
        # from issuing instructions until a real delivery wakes it.
        require(self.idle_wait_projection,'IDLE wait projection is not enabled')
        if cpu.core_id in self.idle_wait_cores:return
        self.idle_wait_cores.add(cpu.core_id)
        self.record_event({'kind':'core_idle_wait_entered','core':cpu.core_id,'pc':hex(cpu.pc)})
        if set(self.cores)<=self.idle_wait_cores:
            raise Blocked('all_cores_idle_waiting',idle_cores=sorted(self.idle_wait_cores))

    def wake_cpu1_control_kick(self):
        """PMU_CON0 kick RMW 唤醒挂起的 CPU1(显式投影,cpu1_control_kick_wake)。

        证据:CPU1 在自身 irq125 handler 内的 idle(0x6066318)挂起;唤醒后 0x606631a 起
        检查 flag(0x1265f7)bit0,随后清 bit2 并退出 handler;而 CPU0 侧等待 bit2 清零
        期间唯一的外部动作就是对 0xffe8c0 的 kick RMW(0x12b732)。投影:该 RMW 让 idle
        按完成计步并前进到下一条指令,不发明中断向量、不改 held 位。「bit3 就是唤醒源」
        是从握手协议推断的显式假设,硅上未验证。
        """
        cpu=self.cores.get(1)
        if cpu is None or 1 not in self.idle_wait_cores:return
        insn,_,size=cpu.decode(cpu.pc)
        require(insn.id=='idle','CPU1 control kick wake requires a parked IDLE instruction')
        self.idle_wait_cores.discard(1)
        cpu.pc+=size;cpu.steps+=1
        self.record_event({'kind':'core_woken_by_cpu1_control_kick','core':1,'pc_after':hex(cpu.pc),
                           'idle_completion_is_assumption':True,'wake_source_unverified':True})

    def return_software_irq(self,cpu):
        # Symmetric opt-in return for the one-shot scheduler-IPI handler at
        # its exact tail: PC=RETI and SP=USP. RETI/PSR come from the real
        # popped frame; bank writeback and ICFG effects are assumptions.
        require(self.software_irq_rti and cpu.pc==0x12ade8 and
                getattr(cpu,'active_context',None)=='software_irq_handler' and
                cpu.after_then is None and cpu.repeat is None,
                'IRQ RTI projection requires the exact handler tail')
        require(cpu.usp is not None and isinstance(cpu.reti,int),
                'IRQ RTI lacks a restored frame or task stack')
        interrupted_task=cpu.reti
        cpu.pc=interrupted_task;cpu.sp=cpu.usp;cpu.sp_known=True;cpu.steps+=1
        cpu.active_context=None
        self.record_event({'kind':'software_irq_rti','core':cpu.core_id,'pc_after':hex(cpu.pc),
                           'sp_after':hex(cpu.sp),'reti_from_frame':hex(interrupted_task),
                           'bank_writeback_is_assumption':True,'icfg_effects_certified':False})

    def return_core_tick(self,cpu):
        # Symmetric opt-in return for the per-core tick handler at its exact
        # tail 0x60661e6: PC=RETI and SP=USP. RETI/PSR come from the real
        # popped frame; bank writeback and ICFG effects are assumptions.
        require(self.core_tick_rti and cpu.pc==0x60661e6 and
                getattr(cpu,'active_context',None)=='core_tick_handler' and
                cpu.after_then is None and cpu.repeat is None,
                'core tick RTI projection requires the exact handler tail')
        require(cpu.usp is not None and isinstance(cpu.reti,int),
                'core tick RTI lacks a restored frame or task stack')
        interrupted_task=cpu.reti
        cpu.pc=interrupted_task;cpu.sp=cpu.usp;cpu.sp_known=True;cpu.steps+=1
        cpu.active_context=None
        self.record_event({'kind':'core_tick_rti','core':cpu.core_id,'pc_after':hex(cpu.pc),
                           'sp_after':hex(cpu.sp),'reti_from_frame':hex(interrupted_task),
                           'bank_writeback_is_assumption':True,'icfg_effects_certified':False})

    def after_step(self,cpu=None,before_pc=None):
        if cpu is not None and cpu.core_id==1 and before_pc in (0,0xa144,0xc5fe,0xc602,0x12caf4,0x12cafa,0x12cb00):
            self.boot_anchor_count+=1
            if len(self.boot_anchors)<16:
                self.boot_anchors.append({'pc':hex(before_pc),'next_pc':hex(cpu.pc),
                    'r0':word(cpu.R[0]),'rets':hex(cpu.rets)})
        if self.first_task_return_hypothesis is not None and cpu is not None:
            if before_pc==0x6065364 and cpu.pc==0x6066106:
                self.first_task_calls.add(cpu.core_id)
                self.first_task_setup[cpu.core_id]={'index':0,'pcs':[],'tcb':None,'base':None}
            setup=self.first_task_setup.get(cpu.core_id)
            if setup is not None and setup['index']<len(FIRST_TASK_SETUP) and before_pc==FIRST_TASK_SETUP[setup['index']][0]:
                if before_pc==0x6066116:setup['tcb']=cpu.R[4]
                if before_pc==0x6066120:setup['base']=cpu.R[1]
                setup['pcs'].append(hex(before_pc));setup['index']+=1
            if cpu.first_task_return_frame is not None and before_pc in (0x606612c,0x606612e):
                cpu.first_task_return_frame['next_pc']=cpu.pc
        self.hardware.core_icfg=self.icfg_demands()

    def first_task_tail(self,cpu,insn,raw):
        if cpu.pc==0x6065364:
            require(insn.id=='call.xl' and raw=={'n':1742} and cpu.after_then is None and
                    cpu.core_id not in self.first_task_setup and cpu.core_id not in self.first_task_returns,
                    'first-task entry must be the actual scheduler call')
        if 0x6066106<=cpu.pc<=0x6066128:
            setup=self.first_task_setup.get(cpu.core_id)
            require(setup is not None and setup['index']<len(FIRST_TASK_SETUP),
                    'first-task setup lacks its actual scheduler entry')
            pc,identity,fields=FIRST_TASK_SETUP[setup['index']]
            require(cpu.pc==pc and insn.id==identity and raw==fields and cpu.after_then is None and cpu.repeat is None,
                    'first-task setup skipped or changed a required instruction')
            if cpu.pc==0x6066116:
                require(cpu.R[4]==0x126c5c and cpu.R[1]==cpu.core_id,
                        'TCB selection must read actual per-core current array')
            if cpu.pc==0x6066120:
                require(cpu.R[4]==setup['tcb']==0x127db8+88*cpu.core_id,
                        'first-task comparison must restore the actual initial idle TCB')
        frame=cpu.first_task_return_frame
        if frame is not None:
            expected={0x606612c:'lockclr',0x606612e:'sti',0x6066130:'rti'}
            require(cpu.pc==frame['next_pc'] and expected.get(cpu.pc)==insn.id and not raw and cpu.after_then is None,
                    'first-task frame must reach the exact unlock/STI/RTI tail')

    def arm_first_task_return(self,cpu,frame,values):
        setup=self.first_task_setup.get(cpu.core_id)
        require(cpu.core_id in self.first_task_calls and cpu.core_id not in self.first_task_returns and
                setup is not None and setup['index']==len(FIRST_TASK_SETUP)-1 and
                frame['tcb']==setup['tcb'] and frame['base']==setup['base'] and
                cpu.first_task_return_frame is None and not cpu.bootstrap_context and
                cpu.reti==0x12c57e and cpu.rets==0 and cpu.flags==0 and getattr(cpu,'irq_psr',None)==0 and cpu.repeat is None,
                'hypothesis needs the actual first idle-task frame after scheduler call')
        require(len(values)==16 and isinstance(values[0],int) and values[0]==0,
                'initial idle argument differs from the actual startup frame')
        cpu.first_task_return_frame={**frame,'next_pc':0x606612c,'setup_pcs':setup['pcs']+[hex(cpu.pc)],'gprs':tuple(values)}

    def project_first_task_return(self,cpu):
        mode=self.first_task_return_hypothesis;frame=cpu.first_task_return_frame
        require(mode in FIRST_TASK_RETURN_HYPOTHESES and frame is not None and
                frame['next_pc']==cpu.pc==0x6066130 and cpu.sp_known and cpu.sp==frame['base']+76 and
                cpu.reti==0x12c57e and cpu.rets==0 and cpu.flags==0 and getattr(cpu,'irq_psr',None)==0 and not cpu.bootstrap_context and
                cpu.after_then is None and cpu.repeat is None and self.hardware.lock_owner!=cpu.core_id,
                'first-task return comparison lacks actual restored and unlocked context')
        require(all(a is b if isinstance(b,(UnknownWord,IcfgValue)) else isinstance(a,int) and a==b
                    for a,b in zip(cpu.R,frame['gprs'])),
                'restored GPRs changed before the compared return')
        state=cpu.icfg_state
        require(state.known&0x3ff==0x3ff and state.value&0x3ff==0x200,
                'first-task comparison requires explicit empty active priorities and actual STI')
        keep_sp,gate8=FIRST_TASK_RETURN_HYPOTHESES[mode]
        # A valid transfer is common to these counterfactuals. SP and gates
        # vary explicitly; unknown banks/mode never become numeric defaults.
        cpu.first_task_return_frame=None;cpu.sp_known=keep_sp;cpu.ssp=cpu.usp=None
        state.project_first_task_return(gate8);self.sync_icfg(cpu)
        cpu.active_context='first_task_return_hypothesis';cpu.pc=cpu.reti;cpu.steps+=1
        event={'kind':'first_task_return_hypothesis','core':cpu.core_id,'hypothesis':mode,
               'tcb':hex(frame['tcb']),'restored_frame':hex(frame['base']),'pc_after':hex(cpu.pc),
               'executed_setup_pcs':frame['setup_pcs'],
               'sp_after':word(cpu.sp) if cpu.sp_known else 'unknown',
               'gate8_after':gate8,'icfg_after_known_mask':hex(state.known),'icfg_after_known_value':hex(state.value),
               'hardware_mode_certified':False}
        self.first_task_returns[cpu.core_id]=event;self.record_event(event)

    def deliver_software_irq(self,cpu,irq):
        # Opt-in minimal CPU entry for the actual per-core scheduler IPI
        # (IRQ 127-cnum; both handlers are the firmware-registered 0x12AD2A).
        # RETI-load and vector-slot reading are checked before any state
        # changes; entry SP bank, ICFG side effects and hardware frame writes
        # stay unknown.
        require(self.software_irq_entry and (irq==(127-cpu.core_id) or (cpu.core_id==1 and irq==125)) and
                all(d['core']!=cpu.core_id for d in self.software_irq_deliveries) and
                not cpu.bootstrap_context and cpu.after_then is None and cpu.repeat is None,
                'software IRQ entry requires the exact per-core scheduler/sync IPI at an instruction boundary')
        slot=0x19fe00+irq*4
        try:handler=int.from_bytes(cpu.mem.read(slot,4),'little')
        except Blocked:raise Blocked('vector_slot_uninitialized',core=cpu.core_id,irq=irq,
                                     address=hex(slot)) from None
        # Validate the whole entry before committing any state change.
        entry_banks=None;bank_usp=None;bank_ssp=None
        if self.software_irq_entry_banks:
            # Explicit unverified assumption: the entry switches SP to the
            # kernel stack (SSP = last explicit SSP fixture) while USP keeps
            # the interrupted task stack. Silicon bank behaviour is unknown.
            bank_ssp=getattr(cpu,'last_explicit_ssp',None)
            require(bank_ssp is not None,'entry bank assumption lacks an explicit SSP fixture')
            require(cpu.sp_known,'interrupted task stack pointer is unknown')
            bank_usp=cpu.sp
            entry_banks={'usp':hex(bank_usp),'ssp':hex(bank_ssp),
                         'entry_sp':'ssp (assumed)','silicon_bank_switch_certified':False}
        # Commit atomically: no further failure is possible past this point.
        if cpu.core_id in self.idle_wait_cores:
            self.idle_wait_cores.discard(cpu.core_id)
            self.record_event({'kind':'core_woken_by_delivery','core':cpu.core_id,'irq':irq})
        interrupted=cpu.pc
        cpu.reti=interrupted;cpu.pc=handler;cpu.steps+=1
        cpu.irq_psr=cpu.flags  # entry PSR: low flags from the interrupted context; high bits unknown
        if self.software_irq_entry_banks:
            cpu.usp=bank_usp;cpu.ssp=bank_ssp;cpu.sp=bank_ssp;cpu.sp_known=True
        cpu.active_context='software_irq_handler'
        self.hardware.software_irq.acknowledge(cpu.core_id,irq-124)
        event={'kind':'software_irq_entry','core':cpu.core_id,'irq':irq,'vector_address':hex(slot),
               'handler':hex(handler),'reti_from':hex(interrupted),'entry_counted_as_model_step':True,
               'reti_load_is_assumption':True,'delivery_clears_pending_is_assumption':True,
               'entry_psr_high_bits_unknown_pushed_as_zero':True,
               'entry_banks':entry_banks,
               'entry_sp_bank_or_icfg_effects_certified':False,'hardware_frame_write_modeled':False}
        self.software_irq_deliveries.append(event);self.record_event(event)

    def deliver_core_tick(self,core):
        # Opt-in minimal CPU entry for a per-core system tick IRQ3 overflow.
        # RETI-load and vector-slot reading are checked before any state
        # change; entry SP bank and ICFG side effects stay unknown. The tick
        # pending bit is NOT cleared here: the handler ACKs it with |=0x40.
        require(self.core_tick_entry and core in self.cores and
                all(d['core']!=core for d in self.core_tick_deliveries),
                'core tick entry requires an eligible overflow on a launched core')
        cpu=self.cores[core]
        require(not cpu.bootstrap_context and cpu.after_then is None and cpu.repeat is None,
                'core tick entry requires an instruction boundary on the overflowed core')
        slot=0x19fe00+3*4
        try:handler=int.from_bytes(cpu.mem.read(slot,4),'little')
        except Blocked:raise Blocked('vector_slot_uninitialized',core=core,irq=3,
                                     address=hex(slot)) from None
        # Validate the whole entry before committing any state change.
        entry_banks=None;bank_usp=None;bank_ssp=None
        if self.core_tick_entry_banks:
            # Explicit unverified assumption: the entry switches SP to the
            # kernel stack (SSP = last explicit SSP fixture) while USP keeps
            # the interrupted task stack. Silicon bank behaviour is unknown.
            bank_ssp=getattr(cpu,'last_explicit_ssp',None)
            require(bank_ssp is not None,'core tick entry bank assumption lacks an explicit SSP fixture')
            require(cpu.sp_known,'interrupted task stack pointer is unknown')
            bank_usp=cpu.sp
            entry_banks={'usp':hex(bank_usp),'ssp':hex(bank_ssp),
                         'entry_sp':'ssp (assumed)','silicon_bank_switch_certified':False}
        # Commit atomically: no further failure is possible past this point.
        if core in self.idle_wait_cores:
            self.idle_wait_cores.discard(core)
            self.record_event({'kind':'core_woken_by_delivery','core':core,'irq':3})
        interrupted=cpu.pc
        cpu.reti=interrupted;cpu.pc=handler;cpu.steps+=1
        cpu.irq_psr=cpu.flags  # entry PSR: low flags from the interrupted context; high bits unknown
        if self.core_tick_entry_banks:
            cpu.usp=bank_usp;cpu.ssp=bank_ssp;cpu.sp=bank_ssp;cpu.sp_known=True
        cpu.active_context='core_tick_handler'
        event={'kind':'core_tick_entry','core':core,'irq':3,'vector_address':hex(slot),
               'handler':hex(handler),'reti_from':hex(interrupted),'entry_counted_as_model_step':True,
               'reti_load_is_assumption':True,'delivery_clears_pending':False,
               'pending_cleared_by_handler_ack':True,
               'entry_psr_high_bits_unknown_pushed_as_zero':True,
               'entry_banks':entry_banks,
               'entry_sp_bank_or_icfg_effects_certified':False,'hardware_frame_write_modeled':False}
        self.core_tick_deliveries.append(event);self.record_event(event)

    def note_stall(self,cpu,details):
        self.stalls+=1
        self.record_event({'core':cpu.core_id,**details})

    def note_bank_write(self,cpu,name,value):
        setattr(cpu,'last_explicit_'+name,value)
        if cpu.sp_known and cpu.sp!=value:cpu.sp_known=False

    def note_sp_write(self,cpu):
        # Alias/writeback mode is unresolved. Equal values are unambiguous;
        # different bank values become unknown until an explicit bank write.
        # Inside the software-IRQ handler the entry bank fixtures stay: its
        # pushes/pops are balanced frame pairs, not bank reassignments.
        if getattr(cpu,'active_context',None)=='software_irq_handler':return
        for name in ('usp','ssp'):
            value=getattr(cpu,name)
            if value is not None and value!=cpu.sp:setattr(cpu,name,None)

    def note_bank_switch(self,cpu,bank):
        # A bank read into SP changes only the visible SP; the other bank
        # keeps its own value instead of being invalidated as an alias.
        cpu.sp_known=True
        self.record_event({'kind':'stack_bank_switch','core':cpu.core_id,'bank':bank,
                           'sp':word(cpu.sp) if cpu.sp_known else 'unknown'})

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
        if (not self.partial_icfg and not cpu.icfg_known) or (self.partial_icfg and self.cpu1_icfg is None):
            raise Blocked('cpu1_bootstrap_icfg_fixture_unknown')
        # Conditional bootstrap return: switch to the established USP, consume
        # the bootstrap token. SSP writeback and IRQ gates remain unresolved.
        cpu.unresolved_ssp_candidates=(getattr(cpu,'last_explicit_ssp',cpu.ssp),cpu.sp)
        cpu.sp=cpu.usp;cpu.ssp=None;cpu.icfg_unknown_mask=0x300;cpu.icfg_known=False
        if self.partial_icfg:
            cpu.icfg_state.invalidate_after_bootstrap(self.bootstrap_status_preserve_mask);self.sync_icfg(cpu)
        cpu.bootstrap_context=False;cpu.active_context='bootstrap_return_thread_projection'
        cpu.pc=cpu.reti;cpu.steps+=1
        self.record_event({'kind':'conditional_bootstrap_rti','core':1,'pc_after':hex(cpu.pc),
                            'sp_after':hex(cpu.sp),'ssp_after':'unknown','icfg_gate_bits':'unknown'})

    def report(self):
        return {'schedule':('round-robin except bounded CPU1 issue delay before first IDLE; no cycle accuracy'
                            if self.defer_core1_first_idle else 'round-robin one instruction per runnable core; no cycle accuracy'),
                'cpu1_reset_vector_fixture':self.cpu1_reset_pc,'cpu1_initial_icfg_fixture':self.cpu1_icfg,
                'reset_vector_release_causality_measured':False,'bootstrap_rti_projection':self.bootstrap_rti,
                **({'software_irq_entry_projection':{'enabled':True,'deliveries':self.software_irq_deliveries,
                    'vector_slot_formula':'0x19fe00+irq*4 pinned by app bytes at 0x6065d90',
                    'entry_bank_assumption':self.software_irq_entry_banks,
                    'bank_switch_silicon_semantics_certified':False,
                    'minimal_entry_only':True,'generic_irq_entry_implemented':False}} if self.software_irq_entry else {}),
                **({'core_tick_entry_projection':{'enabled':True,'deliveries':self.core_tick_deliveries,
                    'vector_slot_formula':'0x19fe00+3*4 (IRQ3) pinned by the actual per-core timer init 0x60661e8',
                    'entry_bank_assumption':self.core_tick_entry_banks,
                    'bank_switch_silicon_semantics_certified':False,
                    'pending_cleared_by_handler_ack_not_delivery':True,
                    'minimal_entry_only':True,'generic_irq_entry_implemented':False}} if self.core_tick_entry else {}),
                'bootstrap_mode_or_ssp_writeback_certified':False,'launches':self.launches,'stalls':self.stalls,
                'unknown_task_gprs_preserved':self.unknown_task_gprs,
                **({'first_task_return_comparison':{'hypothesis':self.first_task_return_hypothesis,
                    'returns':list(self.first_task_returns.values()),'valid_transfer_is_assumed':True,
                    'preserved_psr_rets_gprs_gate9_and_empty_priorities_are_assumptions':True,
                    'deferred_core1_turns':self.deferred_core1_turns,
                    'deferred_core1_issue_limit':CORE1_IDLE_DELAY_LIMIT,
                    'cpu1_idle_executed_or_wake_injected':False,
                    'silicon_task_return_certified':False}} if self.first_task_return_hypothesis is not None else {}),
                **({'partial_icfg_projection':{'initial_known_masks':[hex(v) for v in self.initial_icfg_masks],
                     'bootstrap_status_preserve_mask':hex(self.bootstrap_status_preserve_mask),
                     'literal_patterns':[0,0x200,0x300],'hardware_status_is_not_guest_fullword_rw':True,
                     'core_icfg_in_peripheral_report_is_gate_demand_only':True}} if self.partial_icfg else {}),
                'bootstrap_rti_icfg_policy':('only explicitly selected, already-known status bits preserve input knowledge; all other bits unknown'
                    if self.partial_icfg else 'other visible bits preserve explicit initial fixture; gates8/9 unknown until guest mask'),
                'bootstrap_mode_transition_assumption':'startup exception to thread at the exact QCY trampoline only',
                'events':self.events,'events_truncated':self.event_count>len(self.events),
                'cpu1_boot_anchors':self.boot_anchors,'boot_anchors_truncated':self.boot_anchor_count>len(self.boot_anchors),
                'ready_injected':False,
                'cores':{str(k):{'pc':word(c.pc),'steps':c.steps,'r':[word(v) for v in c.R],
                    **({'partial_icfg':c.icfg_state.report(),
                        'partial_gprs':{str(i):v.report() for i,v in enumerate(c.R) if isinstance(v,IcfgValue)}}
                       if self.partial_icfg else {}),
                    **({'unknown_gpr_sources':{str(i):[word(a) for a in v.origins] for i,v in enumerate(c.R)
                                              if isinstance(v,UnknownWord)}} if self.unknown_task_gprs else {}),
                    'sp':word(c.sp) if c.sp_known else 'unknown','ssp':word(c.ssp),'usp':word(c.usp),'reti':word(c.reti),
                    'last_explicit_ssp':word(getattr(c,'last_explicit_ssp',None)),
                    'last_explicit_usp':word(getattr(c,'last_explicit_usp',None)),
                    'rets':word(c.rets),'icfg':word(c.icfg) if c.icfg_known else 'unknown',
                    'icfg_value_projection':word(c.icfg),
                    'ssp_after_bootstrap_candidates':[word(v) for v in getattr(c,'unresolved_ssp_candidates',())],
                    'bootstrap_context':c.bootstrap_context} for k,c in self.cores.items()}}
