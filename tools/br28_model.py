"""Evidence-scoped BR28 register models. No device access or analog simulation.

Sources and remaining hypotheses: docs/hardware-contracts.md. Unknown reads,
capture/PWM modes, reset stimuli and actual CPU interrupt delivery still stop.
"""
from collections import Counter

from probe_boot import Blocked

TIMER0=0xfd0400
PLL0=0xfd4600
CLOCK0=0xfd0018
PINR1=0xf2c11c
PINR0=0xf2c120
IRQ_CONFIG=0xffe100
IPMASK=0xffe1a8
DIVISORS=(1,4,16,64,2,8,32,128,256,1024,4096,16384,512,2048,8192,32768)
GPIO_BASES=(0xfd5000,0xfd5100,0xfd5200,0xfd5300,0xfd5400,0xfd5500,0xfd5700)


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


class Timer0:
    def __init__(self):
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
            if mode and (value>>10)&15!=4:
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
    def __init__(self,*,initial_config=None,initial_ipmask=None):
        self.config=[None]*32 if initial_config is None else list(initial_config)
        if len(self.config)!=32:raise ValueError('need32 explicit ICFG words')
        self.ipmask=initial_ipmask

    def eligible(self,index,icfg):
        if icfg&0x300!=0x300:return False  # independent gates8/9 in pinned app2/app3 measurements
        value=self.config[index//8]
        if value is None:raise Blocked('uninitialized_irq_configuration',irq=index)
        nibble=(value>>((index%8)*4))&15
        if not nibble&1:return False
        if self.ipmask is None:raise Blocked('uninitialized_ipmask')
        priority=nibble>>1
        if priority>=6:
            raise Blocked('unmodeled_unmaskable_irq',irq=index,priority=priority)
        return priority>=self.ipmask


class BR28Model:
    def __init__(self,*,source_ticks_per_step=4,initial_registers=None,
                 initial_irq_config=None,initial_ipmask=None,reset_inputs_inactive=False,
                 bus_lock_model=False,initial_lock_owner='unknown',gpio_model=False):
        if not 0<=source_ticks_per_step<=256:raise ValueError('invalid virtual tick increment')
        self.timer=Timer0()
        self.irq=IRQController(initial_config=initial_irq_config,initial_ipmask=initial_ipmask)
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
        self.counts=Counter();self.writes=[]
        for address,value in (initial_registers or {}).items():
            if address not in (CLOCK0,CLOCK0+4,CLOCK0+8,PLL0):raise ValueError('unsupported initial register')
            self.registers[address]=value

    def handles(self,address,size):
        return size==4 and address%4==0 and (self.handles_gpio(address) or address in (TIMER0,TIMER0+4,TIMER0+8,PINR1,PINR0,IPMASK,
            CLOCK0,CLOCK0+4,CLOCK0+8,PLL0,PLL0+4,PLL0+8,PLL0+12,PLL0+16) or
            IRQ_CONFIG<=address<IRQ_CONFIG+128)

    def handles_gpio(self,address):
        return self.gpio_model and address%4==0 and any(base<=address<=base+32 for base in GPIO_BASES)

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

    def read(self,address):
        self.counts['reads']+=1
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
        if self.handles_gpio(address):
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
        elif address==IPMASK:
            if not 0<=value<=7:raise Blocked('unsupported_ipmask',value=value)
            self.irq.ipmask=value
        elif IRQ_CONFIG<=address<IRQ_CONFIG+128:
            self.irq.config[(address-IRQ_CONFIG)//4]=value
        else:self.registers[address]=value
        self.record_write({'address':hex(address),'value':hex(value)})

    def record_write(self,row):
        self.counts['writes']+=1
        self.writes.append(row)
        if len(self.writes)>64:self.writes.pop(32)

    def lock(self,acquire):
        if self.lock_owner=='unknown':raise Blocked('uninitialized_shared_lock')
        if acquire:
            if self.lock_owner is not None:raise Blocked('shared_lock_contention_or_recursion',owner=self.lock_owner)
            self.lock_owner=0
        else:
            if self.lock_owner!=0:raise Blocked('shared_lock_release_by_nonowner',owner=self.lock_owner)
            self.lock_owner=None
        self.counts['lock_acquire' if acquire else 'lock_release']+=1

    def advance(self,icfg):
        self.counts['completed_model_steps']+=1
        self.timer.advance(self.source_ticks_per_step)
        if self.timer.pending and self.irq.eligible(4,icfg):
            raise Blocked('interrupt_delivery_not_implemented',irq=4,
                          note='Timer0 pending and enabled after priority filtering; CPU entry not bypassed')

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
        if self.counts['opaque_port_selection_reads']:
            result['assumptions']['port_selection_read']='opaque R0 until validated caller overwrites it; value never supplied as zero'
        return result
