"""Evidence-scoped BR28 register models. No device access or analog simulation.

Sources and remaining hypotheses: docs/hardware-contracts.md. Unknown reads,
capture/PWM modes, reset stimuli and actual CPU interrupt delivery still stop.
"""
from collections import Counter

from probe_boot import Blocked, IrqDeliveryRequest

TIMER0=0xfd0400
PLL0=0xfd4600
CLOCK0=0xfd0018
SYS_DIV=0xfd0014
PINR1=0xf2c11c
PINR0=0xf2c120
IRQ_CONFIG=0xffe100
IPMASK=0xffe1a8
IRQ_IPMASK_BANKED=0xffe168  # handler/idle-loop addressing: base+cnum*0x800 (0x12b56c, 0x12ad5c)
DIVISORS=(1,4,16,64,2,8,32,128,256,1024,4096,16384,512,2048,8192,32768)
GPIO_BASES=(0xfd5000,0xfd5100,0xfd5200,0xfd5300,0xfd5400,0xfd5500,0xfd5700)
MMU_BASE=0xff0400
P11_UART_CON2=0xf2af10
PMU_STA=0xfd3e04
WDT_CON=0xf2a800
WDT_KEY=WDT_CON+4
EFUSE_CON0=0xf2c2c0
EFUSE_CON1=EFUSE_CON0+4
EFUSE_RDAT=EFUSE_CON0+8
OSC_CONFIG=(0xfd1920,0xfd1924,0xfd1928,0xfd1940,0xfd1954)  # WLA_CON8/9/10/16/21
OSC_STATUS=0xfd1978  # WLA_CON30, RO; sampled by actual QCY calibration
GPCNT_CON=0xfd1100
CORE_TICK_BASES=(0xffe0ec,0xffe8ec)
CPU1_CONTROL=0xffe8c0
VOLTAGE_CONFIG=(0xf2c110,0xf2c258,0xf2c26c,0xf2c270,0xff0000)
PLL1_CON3=0xfd470c
CACHE_STATUS_REGISTERS=(0xff0500,0xffe400,0xffec00)
ICU_BASES=(0xffe400,0xffec00)
CORE_PMU_CON1=(0xffe0c4,0xffe8c4)
SFC_BASE=0xfe0200
SDTAP_CON=0xff0100
PSRAM_CON0=0xfe0300
IOMAP_CON0=0xfd6014
USB_IO_CON0=0xfd6000
USB_DISABLE_REGISTERS=(0xfd1800,0xfd1804)
PORT_INPUT_REGISTERS=(SDTAP_CON,PSRAM_CON0)
P11_PORT_DIGITAL=(0xf2b21c,0xf2b220)  # PB_DIE / PB_DIEH, initialized by p11_io_init
DEBUG_BANK_STRIDE=0x800
EMU_CON=0xffe200
DEBUG_OFFSETS=(0x1c0,0x200,0x208,0x20c,0x210,0x214,0x218,0x21c,0x220,0x224,0x228,0x22c)


class DebugExecutionProjection:
    """Actual configuration shadows plus explicit half-open PC-range policy.

    Not silicon endpoint/alignment certification, MPU or exception delivery.
    Never manufacture status, inherit CPU0 state into CPU1, or check data reads.
    """
    def __init__(self,initial_controls=None):
        if initial_controls is not None and (len(initial_controls)!=2 or any(v not in (None,0) for v in initial_controls)):
            raise ValueError('need two unknown or explicitly disabled initial debug controls')
        self.initial_controls=tuple(initial_controls or (None,None));self.registers={}
        for core,value in enumerate(self.initial_controls):
            if value is not None:self.registers[EMU_CON+core*DEBUG_BANK_STRIDE]=value
        self.checks=0;self.violation=None

    def handles(self,address):
        return any(address==0xffe000+core*DEBUG_BANK_STRIDE+offset for core in (0,1) for offset in DEBUG_OFFSETS)

    def require_live(self):
        if self.violation is not None:raise Blocked('pc_guard_violation_requires_delivery')

    def read(self,address):
        self.require_live()
        if address not in self.registers:raise Blocked('unknown_debug_configuration',address=hex(address))
        return self.registers[address]

    def write(self,address,value):
        self.require_live()
        offset=(address-0xffe000)%DEBUG_BANK_STRIDE
        if offset==0x200 and value not in (0,0xe0000007,0xe000000f,0xe000001f):
            raise Blocked('debug_control_mode_not_modeled',value=hex(value))
        if offset==0x1c0 and value!=1:raise Blocked('etm_control_mode_not_modeled',value=hex(value))
        self.registers[address]=value

    def check_fetch(self,pc,size,core=0):
        self.require_live()
        if core not in (0,1) or size<1:raise ValueError('invalid execution guard request')
        self.checks+=1;base=0xffe000+core*DEBUG_BANK_STRIDE
        if base+0x200 not in self.registers:raise Blocked('unknown_initial_pc_guard',core=core)
        if not self.registers[base+0x200]&16:return
        if 0x400000<=pc<0x800000:raise Blocked('pc_guard_virtual_comparison_unverified',pc=hex(pc))
        ranges=[]
        for index in range(3):
            high=base+0x218+8*index;low=high+4
            if low not in self.registers or high not in self.registers:
                raise Blocked('pc_guard_range_unknown',core=core,index=index)
            begin,end=self.registers[low],self.registers[high]
            if end<begin:raise Blocked('invalid_pc_guard_range',core=core,index=index)
            ranges.append((begin,end))
        if not any(begin<=pc and pc+size<=end for begin,end in ranges):
            self.violation={'core':core,'pc':hex(pc),'size':size,
                            'ranges':[[hex(a),hex(b)] for a,b in ranges]}
            raise Blocked('projected_pc_range_violation',**self.violation)

    def report(self):
        return {'registers':{hex(a):hex(v) for a,v in sorted(self.registers.items())},
                'initial_control_fixtures':self.initial_controls,'checked_executing_fetches':self.checks,
                'violation':self.violation,'policy':'union of three half-open numeric PC ranges; full instruction extent',
                'endpoint_alignment_readback_silicon_verified':False,'virtual_pc_comparison_modeled':False,
                'stack_limit_or_mpu_enforced':False,'exception_status_entry_reset_delivery_modeled':False,
                'other_emu_etm_effects_modeled':False,'cpu_banks_independent':True}


class MPUConfigurationProjection:
    """Scoped eight-slot configuration and explicit E7 write-gate projection.

    Opposite polling helpers both write E7, supporting a toggle protocol.
    Readback upper bits/ack latency and actual access enforcement are unverified.
    """
    WREN=0xff03fc

    def __init__(self,initial_wren=None):
        if initial_wren not in (None,0,1):raise ValueError('MPU gate must be unknown or explicit0/1')
        self.initial_wren=initial_wren;self.wren=initial_wren;self.registers={};self.key_writes=0

    def handles(self,address):
        return address==self.WREN or any(base<=address<base+32 and address%4==0
                                        for base in (0xff0300,0xff0340,0xff0380,0xff03c0))

    def read(self,address):
        if address==self.WREN:
            if self.wren is None:raise Blocked('unknown_initial_mpu_write_gate')
            return self.wren
        if address not in self.registers:raise Blocked('unknown_mpu_slot_configuration',address=hex(address))
        return self.registers[address]

    def write(self,address,value):
        if address==self.WREN:
            if value!=0xe7:raise Blocked('mpu_write_gate_command_not_modeled',value=hex(value))
            if self.wren is None:raise Blocked('unknown_initial_mpu_write_gate')
            self.wren^=1;self.key_writes+=1
        else:
            if self.wren!=1:raise Blocked('mpu_configuration_requires_known_open_write_gate')
            self.registers[address]=value

    def report(self):
        return {'registers':{hex(a):hex(v) for a,v in sorted(self.registers.items())},
                'write_gate':self.wren,'initial_write_gate_fixture':self.initial_wren,'key_writes':self.key_writes,
                'gate_projection':'E7 toggles bit0; upper readback bits0; synchronous acknowledgment',
                'write_gate_latency_readback_silicon_verified':False,'cpu_dma_access_permissions_enforced':False,
                'unknown_unwritten_slots_preserved':True}


class SFCReadBus:
    """Functional decoded-Flash read bus, with real enable/configuration gates.

    Transactions are synchronous. No SPI waveform/cache coherence/timing claim.
    """
    def __init__(self,initial_con=None):
        if initial_con is not None and (not isinstance(initial_con,int) or initial_con&~0xf00001):
            raise ValueError('unsupported initial SFC mode')
        self.initial_con=initial_con;self.con=initial_con;self.baud=None;self.writes=0

    def check_flash_access(self,address):
        if not 0x06000000<=address<0x08000000:return
        if self.con is None:raise Blocked('unknown_sfc_enable_state')
        if not self.con&1:raise Blocked('sfc_disabled_flash_access',address=hex(address))

    def read(self,address):
        value=self.con if address==SFC_BASE else self.baud
        if value is None:raise Blocked('unknown_sfc_configuration',address=hex(address))
        return value  # bit31 busy=0: no queued transaction in this synchronous model

    def write(self,address,value):
        if address==SFC_BASE:
            if value&~0xf00001:raise Blocked('sfc_mode_not_modeled',value=hex(value))
            self.con=value
        elif address==SFC_BASE+4:
            if self.con is None or self.con&1:raise Blocked('sfc_baud_change_requires_observed_disable')
            if value>255:raise Blocked('unsupported_sfc_baud',value=value)
            self.baud=value
        else:raise Blocked('sfc_register_not_modeled',address=hex(address))
        self.writes+=1

    def report(self):
        return {'initial_control_fixture':self.initial_con,'con':self.con,'baud':self.baud,'writes':self.writes,
                'busy_projection':'zero outstanding transactions; synchronous decoded-Flash memory',
                'flash_fetch_and_data_access_require_enable':True,'spi_or_cache_timing_simulated':False}


class IcuInitializationProjection:
    """Only observed command4 and enable bit; explicit global-step completion.

    Bit30 is command busy. Bit31 stays an independent external status fixture.
    No cache contents, physical latency, instruction-cache coherence or DMA.
    """
    def __init__(self,initial_words=None,command_steps=None):
        if command_steps is not None and (not isinstance(command_steps,int) or not 0<=command_steps<=10000):
            raise ValueError('invalid explicit ICU command step count')
        self.initial=dict(initial_words or {});self.control={b:self.initial.get(b) for b in ICU_BASES}
        if any(v is not None and (not isinstance(v,int) or v&~0xc0000001) for v in self.control.values()):
            raise ValueError('ICU initial configuration outside scoped bits')
        self.command_steps=command_steps;self.pending={};self.commands=[];self.completed=0;self.busy_reads=0

    def handles(self,address):return any(address in (b,b+16) for b in ICU_BASES)

    def base(self,address):return ICU_BASES[0] if address<ICU_BASES[1] else ICU_BASES[1]

    def peek(self,address):
        base=self.base(address)
        if address!=base:raise Blocked('icu_command_readback_not_modeled',address=hex(address))
        if self.control[base] is None:raise Blocked('unknown_initial_icu_control',address=hex(address))
        return self.control[base]|(0x40000000 if base in self.pending else 0)

    def read(self,address):
        value=self.peek(address)
        if value&0x40000000:self.busy_reads+=1
        return value

    def write(self,address,value):
        base=self.base(address)
        if address==base+16:
            if value!=0xca000010:raise Blocked('icu_command_not_modeled',value=hex(value))
            if self.control[base] is None:raise Blocked('unknown_initial_icu_control',address=hex(base))
            if self.peek(base)&0x40000000:raise Blocked('icu_prior_command_state_unresolved',address=hex(base))
            if self.command_steps is None:raise Blocked('icu_command_progress_unknown',address=hex(address))
            self.pending[base]=self.command_steps
            self.commands.append({'address':hex(address),'value':hex(value)})
        else:
            old=self.peek(base)
            if base in self.pending:raise Blocked('icu_enable_before_command_completed')
            if value!=(old|1):raise Blocked('icu_control_write_not_modeled',address=hex(address),value=hex(value))
            self.control[base]=old|1

    def advance(self):
        if self.command_steps==0:return  # explicit stopped completion clock
        for base in tuple(self.pending):
            self.pending[base]-=1
            if self.pending[base]<=0:del self.pending[base];self.completed+=1

    def report(self):
        return {'initial_word_fixtures':{hex(a):hex(v) for a,v in self.initial.items()},
                'con':{hex(a):None if v is None else hex(v) for a,v in self.control.items()},
                'command_steps_fixture':self.command_steps,'pending_cores':[ICU_BASES.index(b) for b in self.pending],
                'commands':self.commands,'completed_commands':self.completed,'busy_reads':self.busy_reads,
                'bit31_is_independent_external_status':True,'physical_latency_or_command4_semantics_certified':False,
                'cache_contents_or_coherence_simulated':False}


class CoreTickStopped:
    """Optional nominal counter; real IRQ entry and pending readback stay strict."""
    def __init__(self,initial_con=None,progress=None):
        if initial_con not in (None,0):raise ValueError('only unknown or explicitly disabled core tick supported')
        if progress is not None and (not isinstance(progress,int) or not 0<=progress<=256):raise ValueError('invalid explicit core tick progress')
        self.initial_con=initial_con
        self.con=initial_con;self.cnt=self.prd=None;self.pending=None if initial_con is None else False;self.writes=0
        self.progress=progress;self.expirations=0
        self.unresolved_overflow=False

    def read(self,offset):
        if self.unresolved_overflow:raise Blocked('core_tick_overflow_requires_resolution')
        value={0:self.con,4:self.cnt,8:self.prd}[offset]
        if value is None or (offset==0 and self.pending is None):raise Blocked('unknown_core_tick_state')
        if offset==0 and self.pending:raise Blocked('core_tick_pending_readback_unverified')
        return value

    def write(self,offset,value):
        if self.unresolved_overflow:raise Blocked('core_tick_overflow_requires_resolution')
        if offset==0:
            if value&~0x41:raise Blocked('core_tick_control_not_modeled',value=hex(value))
            if value&1:
                if self.progress is None:raise Blocked('core_tick_enable_not_modeled',value=hex(value))
                if self.cnt is None or self.prd is None or self.pending is None:raise Blocked('unknown_core_tick_state')
                if self.prd in (0,0xffffffff):raise Blocked('core_tick_period_edge_unverified',period=hex(self.prd))
                if self.cnt>self.prd:raise Blocked('core_tick_count_exceeds_period')
            self.con=value&1
            if value&64:self.pending=False
        elif offset in (4,8):
            if self.con is not None and self.con&1:raise Blocked('active_core_tick_reconfiguration_unverified')
            if offset==4:self.cnt=value
            else:self.prd=value
        else:raise ValueError('invalid core tick offset')
        self.writes+=1

    def advance(self):
        if self.unresolved_overflow:raise Blocked('core_tick_overflow_requires_resolution')
        if self.con is None or not self.con&1:return
        periods,self.cnt=divmod(self.cnt+self.progress,self.prd+1)
        if periods:self.pending=True;self.expirations+=periods;self.unresolved_overflow=True

    def report(self):
        return {'con':self.con,'cnt':self.cnt,'prd':self.prd,'pending':self.pending,'writes':self.writes,
                'virtual_source_ticks_per_step':self.progress,'expirations':self.expirations,
                'counter_projection_enabled':self.progress is not None,'irq_delivery_simulated':False,'initial_control_fixture':self.initial_con,
                'physical_initial_state_measured':False,'pending_bit7_readback_proven':False,
                'unresolved_overflow':self.unresolved_overflow,
                'counter_projection':'PRD+1 from SDK formula; no inferred core-held clock gating or physical phase'}


class GPCNTCleanup:
    """Only the observed disable/clear path; unknown neighbouring bits stay so."""
    def __init__(self):
        self.bits=PartialWord();self.pending=None;self.disable_writes=0;self.clear_writes=0

    def rmw(self,kind,operand):
        if kind=='and' and operand&0xffffffff==0xfffffffe:
            self.bits.apply('and',operand);self.disable_writes+=1
        elif kind=='or' and operand==64:
            if not self.bits.known&1 or self.bits.value&1:raise Blocked('gpcnt_disable_state_unknown')
            self.pending=False;self.bits.apply('and',~128);self.clear_writes+=1
        else:raise Blocked('gpcnt_counting_or_configuration_not_modeled',operation=kind,operand=hex(operand&0xffffffff))

    def read(self):
        raise Blocked('unknown_gpcnt_configuration_bits',known_mask=hex(self.bits.known))

    def report(self):
        return {'known_value':hex(self.bits.value),'known_mask':hex(self.bits.known),'pending':self.pending,
                'disable_writes':self.disable_writes,'clear_writes':self.clear_writes,
                'pending_clear_is_immediate_projection':True,
                'counting_or_prior_bootloader_state_simulated':False,'command_bit6_readback_proven':False}


class EfuseReadProjection:
    """Exact CPU0 read transaction shape with explicitly supplied OTP inputs.

    No programming commands, read latency or inferred device calibration.
    Upper read bits are an explicit fixture to test the guest low-byte mask.
    """
    def __init__(self,words=None,read_upper_bits=0xa5a50000):
        if words is not None and (len(words)!=4 or any(not isinstance(w,int) or not 0<=w<=0xffffffff for w in words)):
            raise ValueError('need four explicit unsigned32 EFUSE words')
        if not 0<=read_upper_bits<=0xffffffff or read_upper_bits&255:raise ValueError('invalid EFUSE upper-bit fixture')
        self.words=None if words is None else tuple(words);self.upper=read_upper_bits
        self.page=None;self.byte=0;self.active=False;self.read_done=False;self.reads=0

    def read(self,address):
        if address!=EFUSE_RDAT:raise Blocked('efuse_control_read_not_modeled',address=hex(address))
        if not self.active or self.read_done:raise Blocked('efuse_read_outside_selected_byte')
        if self.words is None:raise Blocked('unknown_efuse_input',page=self.page,byte=self.byte)
        value=(self.words[self.page]>>(8*self.byte))&255
        self.read_done=True;self.reads+=1
        return self.upper|value

    def write(self,address,value):
        if address==EFUSE_CON0:
            if self.active or value&~0xc0:raise Blocked('efuse_page_select_not_modeled',value=hex(value))
            self.page=value>>6;self.byte=0;return
        if address!=EFUSE_CON1:raise Blocked('efuse_data_is_readonly')
        if value==0:
            if not self.active or not self.read_done:raise Blocked('efuse_deassert_before_completed_read')
            self.active=self.read_done=False;self.byte+=1;return
        if self.page is None or self.active or self.byte>=4 or value!=0x82|(self.byte<<2):
            raise Blocked('unsupported_efuse_command',value=hex(value))
        self.active=True;self.read_done=False

    def report(self):
        return {'words_fixture':None if self.words is None else [hex(w) for w in self.words],
                'read_upper_bits_fixture':hex(self.upper),'selected_page':self.page,'selected_byte':self.byte,
                'active':self.active,'completed_data_reads':self.reads,'device_otp_measured':False,
                'physical_read_latency_or_programming_modeled':False}


class WatchdogProjection:
    """SDK command projection with an optional, explicit nominal timeout clock.

    One CON command per observed KEY pair is a constrained software contract,
    not a claim about silicon unlock lifetime. Enabling requires an explicit
    clock; counting before the first CON initialization is outside this model.
    Expiry stops execution; hardware reset/exception delivery is not invented.
    """
    keys=(0x048c159d,0x26ae37bf)

    def __init__(self,progress=None):
        if progress is not None:
            if len(progress)!=2 or not all(isinstance(x,int) for x in progress) or progress[0]<0 or progress[1]<1:
                raise ValueError('watchdog progress needs explicit nonnegative numerator/positive denominator')
            progress=tuple(progress)
        self.progress=progress;self.con=None;self.key_phase=0
        self.elapsed=0;self.remainder=0;self.commands=0;self.feeds=0;self.expired=False
        self.command_values=[]

    def read(self,address):
        if self.expired:raise Blocked('watchdog_requires_reset_after_expiry')
        if address!=WDT_CON:raise Blocked('watchdog_key_read_not_modeled')
        if self.con is None:raise Blocked('uninitialized_watchdog_control')
        return self.con

    def write(self,address,value):
        if self.expired:raise Blocked('watchdog_requires_reset_after_expiry')
        if address==WDT_KEY:
            if self.key_phase>=2 or value!=self.keys[self.key_phase]:
                raise Blocked('watchdog_key_sequence_not_modeled',value=hex(value),phase=self.key_phase)
            self.key_phase+=1;return
        if address!=WDT_CON:raise ValueError('not a watchdog register')
        if self.key_phase!=2:raise Blocked('watchdog_command_without_observed_key_pair')
        if value&~0x100ff or (value&31)>17:raise Blocked('unsupported_watchdog_control',value=hex(value))
        if value&32 and value&0xc0:raise Blocked('watchdog_assert_irq_mode_not_modeled',value=hex(value))
        if value&32 and self.progress is None:
            raise Blocked('watchdog_timeout_clock_unknown',value=hex(value))
        if self.con is not None and self.con&32 and value&32 and (value&31)!=(self.con&31):
            raise Blocked('active_watchdog_period_change_not_modeled')
        if value&0x10000:self.elapsed=self.remainder=0;self.feeds+=1
        if not value&32 or self.con is None or not self.con&32:
            self.elapsed=self.remainder=0
        self.con=value&~0x10000;self.key_phase=0;self.commands+=1
        self.command_values.append(value)
        if len(self.command_values)>32:self.command_values.pop(16)

    def advance(self):
        if self.expired:raise Blocked('watchdog_requires_reset_after_expiry')
        if self.con is None or not self.con&32:return
        ticks,self.remainder=divmod(self.remainder+self.progress[0],self.progress[1])
        self.elapsed+=ticks
        if self.elapsed>=1<<(self.con&31):
            self.expired=True
            raise Blocked('watchdog_expired_delivery_not_implemented',control=hex(self.con),
                          elapsed_nominal_units=self.elapsed)

    def report(self):
        return {'con':self.con,'key_phase':self.key_phase,'commands':self.commands,'feeds':self.feeds,
                'command_values':self.command_values,'command_values_truncated':self.commands>len(self.command_values),
                'elapsed_nominal_units':self.elapsed,'fraction_remainder':self.remainder,'expired':self.expired,
                'assumptions':{'nominal_units_per_model_step':self.progress,
                    'period_units':'1 << time_code, from SDK query table; hardware calibration unverified',
                    'counter_phase':'fresh enable/disable reset elapsed; feed rearms full period; explicit projection, silicon phase unmeasured',
                    'counting_before_first_explicit_CON_initialization_modeled':False,
                    'bit16_readback':'configuration projection omits command bit; silicon readback unmeasured',
                    'key_policy':'one CON command per observed KEY pair; hardware key lifetime unverified',
                    'expiry_reset_or_irq_delivery_simulated':False}}


class P11AccessProbe:
    """Explicit external-status counterfactual, NOT a P11 response model.

    CPU0 source establishes the five writes and bit0 poll, not their causality.
    A supplied status is fixed from the beginning; writes never manufacture it.
    """
    sequence=(0xff,0xee,0xab,0x0e,0x55)

    def __init__(self,status=None):
        if status not in (None,0,1):raise ValueError('P11 status fixture must be unknown,0 or1')
        self.status=status;self.phase=0;self.sequences=0;self.polls=0

    def write(self,value):
        if value!=self.sequence[self.phase]:raise Blocked('unmodeled_p11_access_write',value=hex(value))
        self.phase+=1
        if self.phase==5:self.phase=0;self.sequences+=1

    def read(self):
        if self.phase or not self.sequences:raise Blocked('p11_status_read_outside_known_sequence')
        self.polls+=1
        if self.status is None:raise Blocked('unknown_external_pmu_status',address=hex(PMU_STA))
        return self.status

    def report(self):
        return {'fixed_external_status_hypothesis':self.status,'completed_sequences':self.sequences,
                'phase':self.phase,'status_polls':self.polls,'response_causality_or_timing_modeled':False,
                'q32s_executed':False}


class MMU:
    """Functional SDK page-table projection, no TLB/cache or bus timing model."""
    def __init__(self,initial_con=None):
        if initial_con not in (None,0):raise ValueError('only explicit disabled MMU initial state supported')
        self.con=initial_con;self.begin=self.end=None
        self.walks=0
        self.last_walk=None

    def read(self,address):
        value={MMU_BASE:self.con,MMU_BASE+4:self.begin,MMU_BASE+8:self.end}[address]
        if value is None:raise Blocked('uninitialized_mmu_register',address=hex(address))
        return value

    def write(self,address,value):
        if address==MMU_BASE:
            if value&~0x31:raise Blocked('unsupported_mmu_control',value=hex(value))
            if value&1 and (self.begin is None or self.end is None or
                            self.begin%2 or self.end<self.begin or (self.end-self.begin+1)%2):
                raise Blocked('invalid_mmu_table_bounds')
            self.con=value
        elif address==MMU_BASE+4:self.begin=value
        elif address==MMU_BASE+8:self.end=value
        else:raise ValueError('not an MMU register')

    def translate(self,address,size,read_physical):
        if not 0x400000<=address<0x800000:return address
        if self.con is None:raise Blocked('uninitialized_mmu_enable',address=hex(address))
        if not self.con&1:raise Blocked('virtual_access_with_mmu_disabled',address=hex(address))
        page=(address-0x400000)//128
        entry=self.begin+page*2
        if entry+1>self.end:raise Blocked('mmu_page_outside_table',address=hex(address))
        if (address&127)+size>128:raise Blocked('mmu_cross_page_access_not_split',address=hex(address),size=size)
        tag=int.from_bytes(read_physical(entry,2),'little')
        if not tag&0x8000:raise Blocked('mmu_invalid_page',address=hex(address),entry=hex(entry))
        self.walks+=1
        physical=((tag&0x7fff)<<7)+(address&127)
        self.last_walk={'virtual':hex(address),'physical':hex(physical),'entry_address':hex(entry),'tag':hex(tag)}
        return physical

    def report(self):
        return {'con':self.con,'table_begin':self.begin,'table_end_inclusive':self.end,'walks':self.walks,
                'translation':'128B pages;2B tags;valid bit15;absolute physical page number;virtual base0x400000',
                'initial_con_is_synthetic':True,'hardware_tlb_cache_timing_simulated':False,
                **({'last_walk':self.last_walk} if self.last_walk is not None else {})}


class PartialWord:
    """Exact bitwise register state; unknown bits are never returned as zero."""
    def __init__(self,value=0,known=0):
        self.known=known&0xffffffff;self.value=value&self.known

    def apply(self,kind,operand):
        operand&=0xffffffff
        if kind=='or':self.known|=operand;self.value|=operand
        elif kind=='and':self.known|=(~operand)&0xffffffff;self.value&=operand
        else:raise ValueError('unsupported partial-word operation')

    def read(self,address):
        if self.known!=0xffffffff:
            raise Blocked('uninitialized_gpio_bits',address=hex(address),known_mask=hex(self.known))
        return self.value


class SoftwareIrqCommands:
    """Opt-in selected-bit SET/CLR contract; WO bus readback is unverified.

    Never returns a fabricated read word or silently initializes other pending.
    BR28 mapping124+n is cross-checked against actual QCY registration/ACK.
    """
    bases=(0xffe1a0,0xffe9a0)
    source_bases=(0xffe200,0xffea00)

    def __init__(self,initial_pending=None,initial_source_enable=None):
        if initial_pending is not None and (len(initial_pending)!=2 or any(v is not None and
                (not isinstance(v,int) or not 0<=v<=15) for v in initial_pending)):
            raise ValueError('software IRQ initial pending requires two explicit low-nibble or unknown fixtures')
        if initial_source_enable is not None and (len(initial_source_enable)!=2 or any(v is not None and
                (not isinstance(v,int) or not 0<=v<=15) for v in initial_source_enable)):
            raise ValueError('software IRQ source enable requires two explicit low-nibble or unknown fixtures')
        self.initial_pending=None if initial_pending is None else tuple(initial_pending)
        self.initial_source_enable=None if initial_source_enable is None else tuple(initial_source_enable)
        self.pending=[PartialWord() if v is None else PartialWord(v,15) for v in (self.initial_pending or (None,None))]
        self.source_enable=[PartialWord() if v is None else PartialWord(v,15) for v in (self.initial_source_enable or (None,None))]
        self.commands=0;self.acknowledgements=0;self.source_commands=0

    def handles(self,address):return any(address in (b,b+4) for b in self.bases)

    def handles_source(self,address):return address in self.source_bases

    def command(self,address,mask):
        if not self.handles(address):raise Blocked('software_irq_command_address_unmodeled',address=hex(address))
        if mask&~15:raise Blocked('software_irq_command_bits_unmodeled',address=hex(address),mask=hex(mask))
        core=0 if address<self.bases[1] else 1
        clear=address==self.bases[core]+4
        self.pending[core].apply('and' if clear else 'or',~mask if clear else mask)
        self.commands+=1

    def source_rmw(self,address,kind,operand):
        # Actual handler masks the source at entry ([base]&=~8) and re-arms it
        # at exit ([base]|=8). Bit meaning and delivery interaction are
        # recorded only; eligibility is unchanged in this projection.
        if not self.handles_source(address):raise Blocked('software_irq_source_address_unmodeled',address=hex(address))
        core=0 if address<self.source_bases[1] else 1
        if kind not in ('and','or') or operand&~0xffffffff:raise Blocked('software_irq_source_rmw_form_unmodeled',address=hex(address))
        self.source_enable[core].apply(kind,operand)
        self.source_commands+=1

    def acknowledge(self,core,bit):
        # Explicit assumption of the opt-in minimal entry: taking the vector
        # clears the pending bit. Which agent clears it on silicon is unknown.
        self.pending[core].apply('and',~(1<<bit)&0xffffffff)
        self.acknowledgements+=1

    def report(self):
        return {'pending':{str(i):{'known_mask':hex(w.known&15),'known_value':hex(w.value&15)} for i,w in enumerate(self.pending)},
                'commands':self.commands,'mapping':'BR28 software IRQ124+n, n=0..3; bank selects target core',
                'selected_bit_command_is_explicit_projection':True,'wo_readback_certified':False,
                'exception_delivery_simulated':False,'initial_pending_assumed_zero':self.initial_pending is not None and 0 in self.initial_pending,
                'acknowledgements':self.acknowledgements,'acknowledged_by_delivery_is_assumption':True,
                'source_enable':{str(i):{'known_mask':hex(w.known&15),'known_value':hex(w.value&15)} for i,w in enumerate(self.source_enable)},
                'source_enable_addresses':[hex(a) for a in self.source_bases],
                'source_rmw_affects_eligibility':False,'source_enable_semantics_inferred_from_handler_bytes':True,
                'source_commands':self.source_commands,
                **({'initial_pending_fixture':[None if v is None else hex(v) for v in self.initial_pending],
                    'pending_fixture_is_not_measured_reset_state':True} if self.initial_pending is not None else {})}


RESET_FLAGS=(0xfd0004,0xf2a400,0xf2c0c0)
RESET_REASON_INPUTS=(0xfd0000,0xf2a404,0xf2c048,0xf2a60c,
                    0xf2c158,0xf2c15c,0xf2c194,0xf2c198,0xf2c2a0,0xf2d2a0,0xf2d24c)
RESET_CONFIG_MASKS={0xf2a628:0xffffffef,0xf2c054:0x3fdf,0xf2c128:0xff00}
WAKE_CLEAR_TARGETS={0xf2a608:(0xf2a60c,0xffffffff),0xf2c160:(0xf2c158,0xff),
                    0xf2c164:(0xf2c15c,0xff)}
RTC_WAKE_CLEAR=0xf2d248


class ResetHistoryProjection:
    """Explicit reset/wake history snapshots, not inferred reset defaults.

    Flag-clear commands do not erase the reason snapshot read afterwards.
    Their post-command readback is unknown. No mailbox, IRQ or wake generation.
    """
    def __init__(self,words=None):
        self.initial_words=dict(words or {});self.words=dict(self.initial_words)
        allowed=RESET_FLAGS+RESET_REASON_INPUTS
        if any(a not in allowed or not isinstance(v,int) or not 0<=v<=0xffffffff for a,v in self.words.items()):
            raise ValueError('invalid reset/wake snapshot fixture')
        self.configuration={a:PartialWord() for a in RESET_CONFIG_MASKS}
        self.flag_clear_commands=set();self.clear_commands=[]

    def handles(self,address):
        return (address in RESET_FLAGS+RESET_REASON_INPUTS or address in self.configuration or
                address in WAKE_CLEAR_TARGETS or address==RTC_WAKE_CLEAR)

    def read(self,address):
        if address in self.configuration:
            word=self.configuration[address]
            if word.known!=0xffffffff:raise Blocked('unknown_reset_configuration_bits',address=hex(address),known_mask=hex(word.known))
            return word.value
        if address in WAKE_CLEAR_TARGETS or address==RTC_WAKE_CLEAR:
            raise Blocked('wake_clear_register_readback_not_modeled',address=hex(address))
        if address in self.flag_clear_commands:raise Blocked('reset_flag_post_clear_state_unknown',address=hex(address))
        if address not in self.words:raise Blocked('unknown_reset_or_wake_history',address=hex(address))
        return self.words[address]

    def rmw(self,address,kind,operand):
        if kind!='and' or operand&0xffffffff!=RESET_CONFIG_MASKS[address]:
            raise Blocked('reset_configuration_operation_not_modeled',address=hex(address))
        self.configuration[address].apply(kind,operand)

    def write(self,address,value):
        if address in RESET_FLAGS:
            old=self.read(address)
            expected=(old&0x7fbf)|64 if address==0xf2c0c0 else old|64
            if value!=expected:raise Blocked('reset_flag_command_not_modeled',address=hex(address),value=hex(value))
            self.flag_clear_commands.add(address)
        elif address==RTC_WAKE_CLEAR:
            if value:raise Blocked('rtc_wake_clear_nonzero_mapping_unverified',value=hex(value))
            self.clear_commands.append({'address':hex(address),'value':hex(value)})
        elif address in WAKE_CLEAR_TARGETS:
            target,mask=WAKE_CLEAR_TARGETS[address]
            if value&~mask:raise Blocked('wake_clear_mask_not_modeled',address=hex(address),value=hex(value))
            if value:
                if target not in self.words:raise Blocked('unknown_wake_pending_before_clear',address=hex(target))
                self.words[target]&=~value&0xffffffff
            self.clear_commands.append({'address':hex(address),'value':hex(value)})
        else:raise Blocked('reset_history_snapshot_or_config_write_not_modeled',address=hex(address))

    def report(self):
        return {'initial_full_word_fixtures':{hex(a):hex(v) for a,v in self.initial_words.items()},
                'physical_history_measured':False,'flags_cleared':[hex(a) for a in sorted(self.flag_clear_commands)],
                'flag_post_clear_readback_modeled':False,
                'configuration':{hex(a):{'known_mask':hex(w.known),'known_value':hex(w.value)} for a,w in self.configuration.items()},
                'pending_clear_commands':self.clear_commands,
                'wake_mailbox_irq_generation_or_latency_simulated':False,
                'reason_snapshots_preserved_after_flag_clear':True}


class Timer0:
    def __init__(self,allowed_sources=(4,)):
        self.allowed_sources=tuple(allowed_sources)
        self.con=self.cnt=self.prd=None
        self.pending=False
        self.remainder=0
        self.expirations=0

    def read(self,offset):
        value={0:self.con,4:self.cnt,8:self.prd}.get(offset)
        if value is None:
            raise Blocked('uninitialized_peripheral',address=hex(TIMER0+offset))
        return value|(0x8000 if self.pending and offset==0 else 0)

    def write(self,offset,value):
        if offset==0:
            mode=value&3
            if value&~0xfcf3 or mode not in (0,1):
                raise Blocked('unsupported_timer_mode',value=hex(value))
            if mode and (value>>10)&15 not in self.allowed_sources:
                raise Blocked('unsupported_timer_clock_source',value=hex(value))
            if mode and (self.cnt is None or self.prd is None):
                raise Blocked('uninitialized_timer_count_or_period')
            if value&0x4000:self.pending=False
            self.con=value&~0xc000
        elif offset==4:
            self.cnt=value;self.remainder=0
        elif offset==8:
            self.prd=value
        else:
            raise Blocked('unsupported_timer_register',address=hex(TIMER0+offset))

    def advance(self,source_ticks):
        if not isinstance(source_ticks,int) or source_ticks<0:raise ValueError('negative/noninteger timer progress')
        if self.con is None or self.con&3==0:return
        divisor=DIVISORS[(self.con>>4)&15]
        ticks,self.remainder=divmod(self.remainder+source_ticks,divisor)
        # SDK GPTIMER_SYS_SET_PRD(x) writes x-1: period is PRD+1 ticks.
        periods,self.cnt=divmod(self.cnt+ticks,self.prd+1)
        self.expirations+=periods
        if periods:self.pending=True


class IRQController:
    def __init__(self,*,initial_config=None,initial_ipmask=None,unmaskable_irq_projection=False):
        self.config=[None]*32 if initial_config is None else list(initial_config)
        if len(self.config)!=32:raise ValueError('need32 explicit ICFG words')
        self.ipmask=initial_ipmask
        # SDK hwi: priorities 6/7 are the unmaskable/GIEMASK class, set only via
        # irq_unmask_set (+6/clamp 7), delivered independently of IPMASK. The
        # projection assumes GIEMASK enabled and the enable bit alone gates
        # delivery; GIEMASK reset value and rules are NOT documented.
        self.unmaskable_irq_projection=unmaskable_irq_projection

    def eligible(self,index,icfg):
        if icfg&0x300!=0x300:return False  # independent gates8/9 in pinned app2/app3 measurements
        value=self.config[index//8]
        if value is None:raise Blocked('uninitialized_irq_configuration',irq=index)
        nibble=(value>>((index%8)*4))&15
        if not nibble&1:return False
        if self.ipmask is None:raise Blocked('uninitialized_ipmask')
        priority=nibble>>1
        if priority>=6:
            if self.unmaskable_irq_projection:return True
            raise Blocked('unmodeled_unmaskable_irq',irq=index,priority=priority)
        return priority>=self.ipmask


class BR28Model:
    def __init__(self,*,source_ticks_per_step=4,initial_registers=None,
                 initial_irq_config=None,initial_ipmask=None,reset_inputs_inactive=False,
                 bus_lock_model=False,initial_lock_owner='unknown',gpio_model=False,
                 mmu_model=False,initial_mmu_con=None,p11_access_probe=False,p11_status=None,
                 watchdog_projection=False,watchdog_progress=None,efuse_read_projection=False,efuse_words=None,
                 oscillator_configuration_projection=False,oscillator_status_word=None,timer_lsb_ticks_per_step=None,
                 gpcnt_cleanup_projection=False,core_tick_configuration=False,
                 cpu1_held_projection=False,initial_cpu1_control=None,initial_core_tick_controls=None,
                 clock_divider_projection=False,voltage_configuration_projection=False,pll1_configuration_projection=False,
                 cache_status_probe=False,cache_status_words=None,sfc_read_bus=False,initial_sfc_control=None,
                 core_tick_progress=None,core_tick_count_while_held=None,
                 port_initialization_projection=False,port_input_words=None,initial_usb_io_word=None,
                 debug_execution_projection=False,initial_debug_controls=None,
                 reset_history_projection=False,reset_history_words=None,
                 mpu_configuration_projection=False,initial_mpu_write_gate=None,
                 icu_initialization_projection=False,icu_command_steps=None,software_irq_command_projection=False,
                 initial_software_irq_pending=None,software_irq_delivery_requests=False,
                 unmaskable_irq_projection=False):
        if not 0<=source_ticks_per_step<=256:raise ValueError('invalid virtual tick increment')
        if timer_lsb_ticks_per_step is not None and (not isinstance(timer_lsb_ticks_per_step,int) or not 0<=timer_lsb_ticks_per_step<=256):
            raise ValueError('invalid explicit LSB virtual tick rate')
        if oscillator_status_word is not None and (not isinstance(oscillator_status_word,int) or not 0<=oscillator_status_word<=0xffffffff):
            raise ValueError('oscillator status fixture must be unknown or unsigned32')
        self.timer_lsb_ticks_per_step=timer_lsb_ticks_per_step
        self.timer=Timer0((4,) if timer_lsb_ticks_per_step is None else (0,4))
        self.irq=IRQController(initial_config=initial_irq_config,initial_ipmask=initial_ipmask,
                               unmaskable_irq_projection=unmaskable_irq_projection)
        self.multicore_enabled=False;self.current_core=0;self.irq1=None;self.core_icfg={}
        self.cpu1_release_handler=None;self.secondary_cpu_executed=False
        if initial_software_irq_pending is not None and not software_irq_command_projection:
            raise ValueError('software IRQ pending fixture requires explicit command projection')
        if software_irq_delivery_requests and not software_irq_command_projection:
            raise ValueError('software IRQ delivery requests require the command projection')
        self.software_irq_delivery_requests=software_irq_delivery_requests
        self.software_irq=SoftwareIrqCommands(initial_software_irq_pending) if software_irq_command_projection else None
        self.registers={}
        self.reset_inputs_inactive=reset_inputs_inactive
        self.initial_registers=dict(initial_registers or {})
        self.initial_irq_config=None if initial_irq_config is None else list(initial_irq_config)
        self.initial_ipmask=initial_ipmask
        self.source_ticks_per_step=source_ticks_per_step
        self.bus_lock_model=bus_lock_model
        self.lock_owner=initial_lock_owner
        self.initial_lock_owner=initial_lock_owner
        self.gpio_model=gpio_model
        self.gpio={}
        self.mmu=MMU(initial_mmu_con) if mmu_model else None
        self.p11=P11AccessProbe(p11_status) if p11_access_probe else None
        self.watchdog=WatchdogProjection(watchdog_progress) if watchdog_projection else None
        self.efuse=EfuseReadProjection(efuse_words) if efuse_read_projection else None
        self.oscillator_configuration_projection=oscillator_configuration_projection
        self.oscillator_status_word=oscillator_status_word
        self.gpcnt=GPCNTCleanup() if gpcnt_cleanup_projection else None
        if initial_core_tick_controls is not None and len(initial_core_tick_controls)!=2:raise ValueError('need two explicit core tick controls')
        if core_tick_progress is not None and len(core_tick_progress)!=2:raise ValueError('need explicit progress for two core ticks')
        self.core_ticks=[CoreTickStopped(value,rate) for value,rate in zip(initial_core_tick_controls or (None,None),
                       core_tick_progress or (None,None))] if core_tick_configuration else None
        if core_tick_count_while_held not in (None,False,True):raise ValueError('invalid held-core clock hypothesis')
        self.core_tick_count_while_held=core_tick_count_while_held
        if initial_cpu1_control not in (None,0):raise ValueError('only unknown or explicitly held CPU1 fixture supported')
        self.cpu1_held_projection=cpu1_held_projection;self.initial_cpu1_control=initial_cpu1_control
        self.cpu1_control=initial_cpu1_control
        self.clock_divider_projection=clock_divider_projection
        self.voltage_configuration_projection=voltage_configuration_projection
        self.pll1_configuration_projection=pll1_configuration_projection
        self.cache_status_probe=cache_status_probe
        self.cache_status_words=dict(cache_status_words or {})
        if any(address not in CACHE_STATUS_REGISTERS or not isinstance(value,int) or not 0<=value<=0xffffffff
               for address,value in self.cache_status_words.items()):raise ValueError('invalid explicit cache status fixture')
        self.icu=IcuInitializationProjection({a:v for a,v in self.cache_status_words.items() if a in ICU_BASES},icu_command_steps) if icu_initialization_projection else None
        self.sfc=SFCReadBus(initial_sfc_control) if sfc_read_bus else None
        self.debug=DebugExecutionProjection(initial_debug_controls) if debug_execution_projection else None
        self.reset=ResetHistoryProjection(reset_history_words) if reset_history_projection else None
        self.mpu=MPUConfigurationProjection(initial_mpu_write_gate) if mpu_configuration_projection else None
        self.port_initialization_projection=port_initialization_projection
        self.port_input_words=dict(port_input_words or {})
        if any(a not in PORT_INPUT_REGISTERS or not isinstance(v,int) or not 0<=v<=0xffffffff
               for a,v in self.port_input_words.items()):raise ValueError('invalid port input fixture')
        if self.port_input_words and not port_initialization_projection:raise ValueError('port fixtures require explicit projection')
        self.usb_disable_writes=[]
        if initial_usb_io_word is not None:
            if not port_initialization_projection or not gpio_model:raise ValueError('USB IO fixture requires explicit port/GPIO projections')
            if not isinstance(initial_usb_io_word,int) or not 0<=initial_usb_io_word<=0xffffffff:raise ValueError('invalid USB IO fixture')
            self.gpio[USB_IO_CON0]=PartialWord(initial_usb_io_word,0xffffffff)
        self.initial_usb_io_word=initial_usb_io_word
        self.additional_configuration=set()
        for enabled,addresses in ((oscillator_configuration_projection,OSC_CONFIG),
                                  (clock_divider_projection,(SYS_DIV,)),
                                  (voltage_configuration_projection,VOLTAGE_CONFIG),
                                  (pll1_configuration_projection,(PLL1_CON3,)),
                                  (port_initialization_projection,(IOMAP_CON0,)+P11_PORT_DIGITAL)):
            if enabled:self.additional_configuration.update(addresses)
        self.counts=Counter();self.writes=[]
        for address,value in (initial_registers or {}).items():
            if address not in (CLOCK0,CLOCK0+4,CLOCK0+8,PLL0) and address not in self.additional_configuration:
                raise ValueError('unsupported initial register')
            if not isinstance(value,int) or not 0<=value<=0xffffffff:raise ValueError('initial register must be unsigned32')
            self.registers[address]=value

    def handles(self,address,size):
        return size==4 and address%4==0 and (self.handles_gpio(address) or
            (self.software_irq is not None and self.software_irq.handles(address)) or
            (self.icu is not None and self.icu.handles(address)) or
            (self.multicore_enabled and (address==IPMASK+0x800 or IRQ_CONFIG+0x800<=address<IRQ_CONFIG+0x880)) or
            (self.multicore_enabled and address in CORE_PMU_CON1) or
            (self.debug is not None and self.debug.handles(address)) or
            (self.reset is not None and self.reset.handles(address)) or
            (self.mpu is not None and self.mpu.handles(address)) or
            (self.cache_status_probe and address in CACHE_STATUS_REGISTERS) or
            (self.sfc is not None and address in (SFC_BASE,SFC_BASE+4)) or
            address in self.additional_configuration or
            (self.port_initialization_projection and address in PORT_INPUT_REGISTERS+USB_DISABLE_REGISTERS) or
            (self.cpu1_held_projection and address==CPU1_CONTROL) or
            self.handles_core_tick(address) or
            self.handles_gpcnt(address) or
            (self.oscillator_configuration_projection and address==OSC_STATUS) or
            (self.efuse is not None and address in (EFUSE_CON0,EFUSE_CON1,EFUSE_RDAT)) or
            (self.watchdog is not None and address in (WDT_CON,WDT_KEY)) or
            (self.p11 is not None and address in (P11_UART_CON2,PMU_STA)) or
            (self.mmu is not None and MMU_BASE<=address<=MMU_BASE+8) or address in (TIMER0,TIMER0+4,TIMER0+8,PINR1,PINR0,IPMASK,
            CLOCK0,CLOCK0+4,CLOCK0+8,PLL0,PLL0+4,PLL0+8,PLL0+12,PLL0+16) or
            IRQ_CONFIG<=address<IRQ_CONFIG+128)

    def handles_gpio(self,address):
        return self.gpio_model and address%4==0 and (any(base<=address<=base+32 for base in GPIO_BASES) or
            (self.port_initialization_projection and address==USB_IO_CON0))

    def handles_core_tick(self,address):
        return self.core_ticks is not None and any(base<=address<=base+8 for base in CORE_TICK_BASES)

    def core_tick(self,address):
        index=0 if address<CORE_TICK_BASES[1] else 1
        return self.core_ticks[index],address-CORE_TICK_BASES[index]

    def handles_gpcnt(self,address):
        return self.gpcnt is not None and address==GPCNT_CON

    def gpcnt_rmw(self,address,kind,operand):
        if not self.handles_gpcnt(address):raise ValueError('not modeled GPCNT CON')
        self.gpcnt.rmw(kind,operand);self.counts['reads']+=1
        self.record_write({'address':hex(address),'operation':kind,'operand':hex(operand&0xffffffff),
                           'known_value':hex(self.gpcnt.bits.value),'known_mask':hex(self.gpcnt.bits.known)})

    def writable_gpio(self,address):
        if not self.handles_gpio(address):raise ValueError('not a GPIO register')
        if address%256==4:raise Blocked('gpio_input_is_readonly',address=hex(address))

    def gpio_rmw(self,address,kind,operand):
        self.writable_gpio(address)
        word=self.gpio.setdefault(address,PartialWord())
        word.apply(kind,operand)
        self.counts['reads']+=1
        self.record_write({'address':hex(address),'known_value':hex(word.value),'known_mask':hex(word.known)})

    def observe_dead_port_selection_read(self,address):
        if address!=0xf2c300:raise ValueError('not P3_PORT_SEL0')
        self.counts['reads']+=1
        self.counts['opaque_port_selection_reads']+=1

    def observe_dead_pwm_read(self,address):
        if address not in (0xfd2964,0xfd2974,0xfd2984,0xfd2994):raise ValueError('not the four proven PWM config reads')
        self.counts['reads']+=1;self.counts['opaque_pwm_configuration_reads']+=1

    def read(self,address):
        self.counts['reads']+=1
        if self.software_irq is not None and self.software_irq.handles(address):
            raise Blocked('software_irq_wo_readback_unverified',address=hex(address))
        if self.icu is not None and self.icu.handles(address):return self.icu.read(address)
        if self.multicore_enabled and address in CORE_PMU_CON1:return self.core_power[address].read(address)
        if self.multicore_enabled and address==IPMASK+0x800:
            if self.irq1.ipmask is None:raise Blocked('unknown_cpu1_ipmask')
            return self.irq1.ipmask
        if self.multicore_enabled and IRQ_CONFIG+0x800<=address<IRQ_CONFIG+0x880:
            value=self.irq1.config[(address-IRQ_CONFIG-0x800)//4]
            if value is None:raise Blocked('unknown_cpu1_irq_configuration',address=hex(address))
            return value
        if self.debug is not None and self.debug.handles(address):return self.debug.read(address)
        if self.reset is not None and self.reset.handles(address):return self.reset.read(address)
        if self.mpu is not None and self.mpu.handles(address):return self.mpu.read(address)
        if self.port_initialization_projection and address in PORT_INPUT_REGISTERS:
            if address not in self.port_input_words:raise Blocked('unknown_port_input',address=hex(address))
            return self.port_input_words[address]
        if self.port_initialization_projection and address in USB_DISABLE_REGISTERS:
            raise Blocked('usb_controller_status_not_modeled',address=hex(address))
        if self.cache_status_probe and address in CACHE_STATUS_REGISTERS:
            if address not in self.cache_status_words:raise Blocked('unknown_cache_status_input',address=hex(address))
            return self.cache_status_words[address]
        if self.sfc is not None and address in (SFC_BASE,SFC_BASE+4):return self.sfc.read(address)
        if self.cpu1_held_projection and address==CPU1_CONTROL:
            if self.cpu1_control is None:raise Blocked('unknown_initial_cpu1_control')
            return self.cpu1_control
        if self.handles_core_tick(address):
            timer,offset=self.core_tick(address);return timer.read(offset)
        if self.handles_gpcnt(address):return self.gpcnt.read()
        if self.oscillator_configuration_projection and address==OSC_STATUS:
            if self.oscillator_status_word is None:raise Blocked('unknown_oscillator_status_input')
            return self.oscillator_status_word
        if self.efuse is not None and address in (EFUSE_CON0,EFUSE_CON1,EFUSE_RDAT):return self.efuse.read(address)
        if self.watchdog is not None and address in (WDT_CON,WDT_KEY):return self.watchdog.read(address)
        if self.p11 is not None and address in (P11_UART_CON2,PMU_STA):
            if address!=PMU_STA:raise Blocked('unmodeled_p11_register_read',address=hex(address))
            return self.p11.read()
        if self.mmu is not None and MMU_BASE<=address<=MMU_BASE+8:return self.mmu.read(address)
        if self.handles_gpio(address):return self.gpio.setdefault(address,PartialWord()).read(address)
        if TIMER0<=address<=TIMER0+8:return self.timer.read(address-TIMER0)
        if IRQ_CONFIG<=address<IRQ_CONFIG+128:value=self.irq.config[(address-IRQ_CONFIG)//4]
        elif address==IPMASK:value=self.irq.ipmask
        else:value=self.registers.get(address)
        if value is None:raise Blocked('uninitialized_peripheral',address=hex(address))
        return value

    def write(self,address,value):
        if not isinstance(value,int) or not 0<=value<=0xffffffff:raise ValueError('MMIO value must be unsigned32')
        if not self.handles(address,4):raise Blocked('unsupported_peripheral_write',address=hex(address))
        if self.software_irq is not None and self.software_irq.handles(address):
            raise Blocked('software_irq_full_store_contract_unmodeled',address=hex(address))
        if self.icu is not None and self.icu.handles(address):
            self.icu.write(address,value);self.record_write({'address':hex(address),'value':hex(value)})
            return
        if self.multicore_enabled and address in CORE_PMU_CON1:raise Blocked('core_power_full_write_not_modeled',address=hex(address))
        if self.debug is not None and self.debug.handles(address):
            self.debug.write(address,value);self.record_write({'address':hex(address),'value':hex(value)})
            return
        if self.reset is not None and self.reset.handles(address):
            self.reset.write(address,value);self.record_write({'address':hex(address),'value':hex(value)})
            return
        if self.mpu is not None and self.mpu.handles(address):
            self.mpu.write(address,value);self.record_write({'address':hex(address),'value':hex(value)})
            return
        if self.port_initialization_projection and address in PORT_INPUT_REGISTERS:
            raise Blocked('port_input_snapshot_write_not_modeled',address=hex(address))
        if self.port_initialization_projection and address in USB_DISABLE_REGISTERS:
            if value!=0:raise Blocked('usb_controller_enable_or_configuration_not_modeled',address=hex(address),value=hex(value))
            self.usb_disable_writes.append(address)
            self.record_write({'address':hex(address),'value':hex(value),'projection':'port initialization disable only'})
            return
        if self.cache_status_probe and address in CACHE_STATUS_REGISTERS:raise Blocked('cache_control_write_not_modeled',address=hex(address))
        if self.handles_gpcnt(address):raise Blocked('gpcnt_full_control_store_not_modeled')
        if self.oscillator_configuration_projection and address==OSC_STATUS:raise Blocked('oscillator_status_is_readonly')
        if self.sfc is not None and address in (SFC_BASE,SFC_BASE+4):self.sfc.write(address,value)
        elif self.cpu1_held_projection and address==CPU1_CONTROL:
            if value&1:
                if self.cpu1_release_handler is None:raise Blocked('cpu1_release_not_implemented',value=hex(value))
                if self.cpu1_control!=0:raise Blocked('unsupported_cpu1_release_transition')
                self.cpu1_release_handler(value);self.cpu1_control=value
            else:
                if value!=0:raise Blocked('cpu1_control_write_not_modeled',value=hex(value))
                if self.multicore_enabled and self.cpu1_control==1:raise Blocked('cpu1_hold_after_release_not_implemented')
                self.cpu1_control=0
        elif self.handles_core_tick(address):
            timer,offset=self.core_tick(address)
            if address==CORE_TICK_BASES[1] and value&1:
                if not self.cpu1_held_projection or self.cpu1_control is None:raise Blocked('core1_execution_state_unknown')
                if self.cpu1_control==0 and self.core_tick_count_while_held is None:raise Blocked('core1_tick_held_clock_unknown')
            timer.write(offset,value)
        elif self.efuse is not None and address in (EFUSE_CON0,EFUSE_CON1,EFUSE_RDAT):self.efuse.write(address,value)
        elif self.watchdog is not None and address in (WDT_CON,WDT_KEY):self.watchdog.write(address,value)
        elif self.p11 is not None and address in (P11_UART_CON2,PMU_STA):
            if address!=P11_UART_CON2:raise Blocked('pmu_status_is_not_a_configuration_latch')
            self.p11.write(value)
        elif self.mmu is not None and MMU_BASE<=address<=MMU_BASE+8:self.mmu.write(address,value)
        elif self.handles_gpio(address):
            self.writable_gpio(address);self.gpio[address]=PartialWord(value,0xffffffff)
        elif TIMER0<=address<=TIMER0+8:self.timer.write(address-TIMER0,value)
        elif address==PINR1:
            if not self.reset_inputs_inactive:
                raise Blocked('unmodeled_pin_reset_input',address=hex(address),value=hex(value))
            if value&~0x7f or value>>4>4:raise Blocked('unsupported_pin_reset_configuration',value=hex(value))
            self.registers[address]=value
        elif address==PINR0:
            if value!=0:raise Blocked('pin0_reset_enable_not_modeled',value=hex(value))
            self.registers[address]=0  # SDK gpio_longpress_pin0_reset_config(time=0)
        elif self.multicore_enabled and address==IPMASK+0x800:
            if not 0<=value<=7:raise Blocked('unsupported_cpu1_ipmask',value=value)
            self.irq1.ipmask=value
        elif self.multicore_enabled and IRQ_CONFIG+0x800<=address<IRQ_CONFIG+0x880:
            self.irq1.config[(address-IRQ_CONFIG-0x800)//4]=value
        elif address==IPMASK:
            if not 0<=value<=7:raise Blocked('unsupported_ipmask',value=value)
            self.irq.ipmask=value
        elif address==IRQ_IPMASK_BANKED:
            # Same priority mask as IPMASK: the idle loop (0x12b56c) and the
            # IRQ127 handler (0x12ad5c/0x12adda) both address 0xFFE168+cnum*0x800.
            if not 0<=value<=7:raise Blocked('unsupported_ipmask',value=value)
            self.irq.ipmask=value
        elif self.multicore_enabled and address==IRQ_IPMASK_BANKED+0x800:
            if not 0<=value<=7:raise Blocked('unsupported_cpu1_ipmask',value=value)
            self.irq1.ipmask=value
        elif IRQ_CONFIG<=address<IRQ_CONFIG+128:
            self.irq.config[(address-IRQ_CONFIG)//4]=value
        else:self.registers[address]=value
        self.record_write({'address':hex(address),'value':hex(value)})

    def record_write(self,row):
        self.counts['writes']+=1
        self.writes.append(row)
        if len(self.writes)>64:self.writes.pop(32)

    def enable_multicore(self,irq1_config,ipmask1,release_handler):
        if self.multicore_enabled or not self.cpu1_held_projection:raise ValueError('multicore requires a fresh held-CPU1 model')
        self.irq1=IRQController(initial_config=irq1_config,initial_ipmask=ipmask1,
                                unmaskable_irq_projection=self.irq.unmaskable_irq_projection)
        self.core_power={a:PartialWord() for a in CORE_PMU_CON1}
        self.cpu1_release_handler=release_handler;self.multicore_enabled=True

    def core_power_rmw(self,address,kind,operand):
        if kind!='and' or operand&0xffffffff!=0xfffffeff:raise Blocked('core_power_operation_not_modeled',address=hex(address))
        word=self.core_power[address];word.apply(kind,operand)
        self.counts['reads']+=1
        self.record_write({'address':hex(address),'known_mask':hex(word.known),'known_value':hex(word.value)})

    def software_irq_command(self,address,mask):
        self.software_irq.command(address,mask)
        self.counts['reads']+=1
        self.record_write({'address':hex(address),'selected_mask':hex(mask),'projection':'WO selected-bit command; no read value fabricated'})

    def software_irq_source_rmw(self,address,kind,operand):
        self.software_irq.source_rmw(address,kind,operand)
        self.counts['reads']+=1
        self.record_write({'address':hex(address),'operation':kind,'operand':hex(operand),
                           'projection':'source-enable bit tracking only; eligibility unchanged'})

    def lock(self,acquire):
        if self.lock_owner=='unknown':raise Blocked('uninitialized_shared_lock')
        actor=self.current_core if self.multicore_enabled else 0
        if acquire:
            if self.lock_owner is not None:
                if self.multicore_enabled and self.lock_owner!=actor:
                    from br28_machine import CoreStall
                    raise CoreStall('shared_lock_wait',owner=self.lock_owner)
                raise Blocked('shared_lock_contention_or_recursion',owner=self.lock_owner)
            self.lock_owner=actor
        else:
            if self.lock_owner!=actor:raise Blocked('shared_lock_release_by_nonowner',owner=self.lock_owner)
            self.lock_owner=None
        self.counts['lock_acquire' if acquire else 'lock_release']+=1

    def advance(self,icfg):
        self.counts['completed_model_steps']+=1
        if self.icu is not None:self.icu.advance()
        if self.watchdog is not None:self.watchdog.advance()
        if self.core_ticks is not None:
            if self.core_ticks[1].con is not None and self.core_ticks[1].con&1:
                if not self.cpu1_held_projection or self.cpu1_control is None:raise Blocked('core1_execution_state_unknown')
                if self.cpu1_control==0 and self.core_tick_count_while_held is None:raise Blocked('core1_tick_held_clock_unknown')
            for core,timer in enumerate(self.core_ticks):
                if core==1 and self.cpu1_held_projection and self.cpu1_control==0 and self.core_tick_count_while_held is False:continue
                before=timer.expirations;timer.advance()
                if timer.expirations!=before:
                    raise Blocked('core_tick_overflow_delivery_unresolved',core=core,irq=3,
                                  note='internal pending set; no guessed readback bit, wake or interrupt entry')
        if self.timer.con is None or not self.timer.con&3:self.timer.advance(0)
        elif ((self.timer.con>>10)&15)==0:self.timer.advance(self.timer_lsb_ticks_per_step)
        else:self.timer.advance(self.source_ticks_per_step)
        if self.timer.pending:
            if self.multicore_enabled:
                for core,irq in enumerate((self.irq,self.irq1)):
                    if core==1 and self.cpu1_control!=1:continue
                    gate=self.core_icfg.get(core)
                    if gate is None:
                        if irq.eligible(4,0x300):raise Blocked('pending_irq_with_unknown_core_icfg',core=core,irq=4)
                    elif irq.eligible(4,gate):raise Blocked('interrupt_delivery_not_implemented',core=core,irq=4)
            elif self.irq.eligible(4,icfg):
                raise Blocked('interrupt_delivery_not_implemented',irq=4,
                              note='Timer0 pending and enabled after priority filtering; CPU entry not bypassed')
        if self.software_irq is not None:
            for core,word in enumerate(self.software_irq.pending):
                if core==1 and (not self.multicore_enabled or self.cpu1_control!=1):continue
                irq=self.irq if core==0 else self.irq1
                gate=self.core_icfg.get(core) if self.multicore_enabled else icfg
                for bit in range(4):
                    if word.known&(1<<bit) and not word.value&(1<<bit):continue
                    try:eligible=irq.eligible(124+bit,0x300 if gate is None else gate)
                    except Blocked as e:
                        if e.details['kind']=='unmodeled_unmaskable_irq' and self.multicore_enabled:
                            raise Blocked('unmodeled_unmaskable_irq',core=core,irq=124+bit,
                                          priority=e.details['priority'],source='software_irq',
                                          pending_known=bool(word.known&(1<<bit)),
                                          icfg_gate_demand='unknown' if gate is None else hex(gate)) from None
                        raise
                    if eligible:
                        if gate is None:raise Blocked('software_irq_with_unknown_icfg',core=core,irq=124+bit)
                        if self.software_irq_delivery_requests:
                            raise IrqDeliveryRequest(core=core,irq=124+bit)
                        kind='software_irq_delivery_not_implemented' if word.known&(1<<bit) else 'software_irq_pending_unknown'
                        raise Blocked(kind,core=core,irq=124+bit)

    def report(self):
        result={'registers':{hex(a):hex(v) for a,v in sorted(self.registers.items())},
                'ipmask':self.irq.ipmask,'irq_config_known_words':sum(x is not None for x in self.irq.config),
                'timer0':{'con':self.timer.con,'cnt':self.timer.cnt,'prd':self.timer.prd,
                          'pending':self.timer.pending,'expirations':self.timer.expirations},
                'counts':dict(self.counts),'writes':self.writes,
                'writes_truncated':self.counts['writes']>len(self.writes),'shared_lock_owner':self.lock_owner,
                'assumptions':{'source_ticks_per_model_step':self.source_ticks_per_step,
                    'initial_registers':{hex(a):hex(v) for a,v in sorted(self.initial_registers.items())},
                    'initial_irq_config':self.initial_irq_config,'initial_ipmask':self.initial_ipmask,
                    'shared_lock_model':self.bus_lock_model,'initial_lock_owner':self.initial_lock_owner,
                    'timer_progress_is_virtual_not_cycle_calibrated':True,
                    'external_reset_inputs_inactive':self.reset_inputs_inactive,
                    'analog_pll_lock_simulated':False,'cpu_interrupt_delivery_simulated':False}}
        if self.gpio_model:
            result['gpio']={hex(a):{'known_value':hex(w.value),'known_mask':hex(w.known)}
                            for a,w in sorted(self.gpio.items())}
            result['assumptions']['gpio']='exact bitwise configuration projection; external levels unknown; no GPIO IRQ/electrical model'
        if self.mmu is not None:result['mmu']=self.mmu.report()
        if self.p11 is not None:result['p11_access_counterfactual']=self.p11.report()
        if self.watchdog is not None:result['watchdog_projection']=self.watchdog.report()
        if self.efuse is not None:result['efuse_read_projection']=self.efuse.report()
        if self.gpcnt is not None:result['gpcnt_cleanup_projection']=self.gpcnt.report()
        if self.core_ticks is not None:
            result['core_tick_configuration']=[timer.report() for timer in self.core_ticks]
            result['assumptions']['core_tick_count_while_cpu1_held']=self.core_tick_count_while_held
        if self.cpu1_held_projection:
            result['cpu1_held_projection']={'initial_control_fixture':self.initial_cpu1_control,
                                            'current_control':self.cpu1_control,'secondary_cpu_executed':self.secondary_cpu_executed,
                                            'release_is_a_stop_not_ready_injection':not self.multicore_enabled}
        if self.multicore_enabled:
            result['multicore']={'current_core':self.current_core,'cpu1_irq_words':self.irq1.config,
                'cpu1_ipmask':self.irq1.ipmask,'core_icfg':{str(k):v for k,v in self.core_icfg.items()},
                'shared_lock_ownership_is_per_core':True,'ready_flag_injected':False}
            result['multicore']['pmu_con1']={hex(a):{'known_mask':hex(w.known),'known_value':hex(w.value)}
                                              for a,w in self.core_power.items()}
        if self.clock_divider_projection:result['assumptions']['sys_div_configuration_projection']=True
        if self.voltage_configuration_projection:
            result['assumptions']['voltage_configuration_projection']='five scoped P33/CMNG configuration registers; analog voltage/ramp/settling and RAM wait-state timing not modeled'
        if self.pll1_configuration_projection:result['assumptions']['pll1_con3_configuration_projection']='RW config only; analog PLL1 not modeled'
        if self.cache_status_probe:
            result['assumptions']['cache_status_words_fixture']={hex(a):hex(v) for a,v in sorted(self.cache_status_words.items())}
            result['assumptions']['cache_contents_and_coherence_simulated']=False
        if self.sfc is not None:result['sfc_read_bus']=self.sfc.report()
        if self.icu is not None:result['icu_initialization_projection']=self.icu.report()
        if self.software_irq is not None:result['software_irq_commands']=self.software_irq.report()
        if self.debug is not None:result['debug_execution_projection']=self.debug.report()
        if self.reset is not None:result['reset_history_projection']=self.reset.report()
        if self.mpu is not None:result['mpu_configuration_projection']=self.mpu.report()
        if self.port_initialization_projection:
            result['port_initialization_projection']={
                'input_words_fixture':{hex(a):hex(v) for a,v in self.port_input_words.items()},
                'physical_initial_values_measured':False,
                'initial_usb_io_word_fixture':self.initial_usb_io_word,
                'usb_disable_writes':[hex(a) for a in self.usb_disable_writes],
                'usb_controller_status_transfer_or_pin_electrical_model':False,
                'iomap_configuration_only':True,'usb_io_unknown_bits_preserved':True}
        if self.counts.get('opaque_pwm_configuration_reads',0):
            result['assumptions']['pwm_configuration_reads']='R0 remains unknown through guarded loop/return until actual overwrite; no value fabricated'
        if self.oscillator_configuration_projection:
            result['assumptions']['oscillator_configuration']='five scoped WLA RW configuration registers; analog oscillator/lock/settling and clock propagation not modeled'
            result['assumptions']['oscillator_status_word_fixture']=self.oscillator_status_word
        if self.timer_lsb_ticks_per_step is not None:
            result['assumptions']['timer_lsb_ticks_per_step']=self.timer_lsb_ticks_per_step
        if self.counts['opaque_port_selection_reads']:
            result['assumptions']['port_selection_read']='opaque R0 until validated caller overwrites it; value never supplied as zero'
        return result
