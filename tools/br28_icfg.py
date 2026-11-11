"""Opt-in, evidence-scoped ICFG gate writes with partial hardware status.

The AC791N measurements constrain selected operations, not a complete BR28
writable mask or RTI mode. Unsupported status writes/unknown consumers stop.
"""
M=0xffffffff
GATES=0x300
import operator


def blocked(kind,**details):
    from probe_boot import Blocked
    raise Blocked(kind,**details)


class IcfgValue:
    """Register expression retaining identity of unmodified bits."""
    __slots__=('value','known','origin','identity')

    def __init__(self,value,known,origin,identity=M):
        self.known=known&M;self.value=value&self.known;self.origin=origin;self.identity=identity&M

    def consumed(self,*args,**kwargs):
        blocked('partial_icfg_value_consumed',known_mask=hex(self.known),known_value=hex(self.value))

    def concrete(self):
        if self.known!=M:self.consumed()
        return self.value

    def mask(self,operation,operand):
        operand&=M
        if operation=='and':
            value=self.value&operand;known=self.known|(~operand&M);identity=self.identity&operand
        elif operation=='or':
            value=self.value|operand;known=self.known|operand;identity=self.identity&~operand
        else:blocked('partial_icfg_bit_operation_unmodeled',operation=operation)
        return IcfgValue(value,known,self.origin,identity)

    def overlay_bytes(self,offset,data):
        value,known,identity=self.value,self.known,self.identity
        for i,byte in enumerate(data):
            shift=8*(offset+i);mask=255<<shift
            value=(value&~mask)|(byte<<shift);known|=mask;identity&=~mask
        return IcfgValue(value,known,self.origin,identity)

    def report(self):
        return {'known_mask':hex(self.known),'known_value':hex(self.value),
                'source_core':self.origin[0],'status_generation':self.origin[1],
                'identity_mask':hex(self.identity)}

    __int__=__index__=concrete
    def __float__(self):return float(self.concrete())
    def __bool__(self):return bool(self.concrete())
    def __hash__(self):return hash(self.concrete())
    def __neg__(self):return -self.concrete()
    def __pos__(self):return self.concrete()
    def __invert__(self):return ~self.concrete()

    def __repr__(self):return 'IcfgValue'


# Numeric operations require a fully known word. Partial bit-mask operations
# are handled explicitly by the CPU, without evaluating unrelated unknown bits.
def _binary(operation,reverse=False):
    def apply(self,other):
        lhs=self.concrete();rhs=other.concrete() if isinstance(other,IcfgValue) else other
        return operation(rhs,lhs) if reverse else operation(lhs,rhs)
    return apply


for _name,_operation in (('eq',operator.eq),('ne',operator.ne),('lt',operator.lt),('le',operator.le),
        ('gt',operator.gt),('ge',operator.ge),('and',operator.and_),('or',operator.or_),('xor',operator.xor),
        ('add',operator.add),('sub',operator.sub),('mul',operator.mul),('lshift',operator.lshift),
        ('rshift',operator.rshift),('truediv',operator.truediv),('floordiv',operator.floordiv),
        ('mod',operator.mod),('pow',operator.pow)):
    setattr(IcfgValue,'__'+_name+'__',_binary(_operation))
    if _name not in ('eq','ne','lt','le','gt','ge'):
        setattr(IcfgValue,'__r'+_name+'__',_binary(_operation,True))


class IcfgState:
    def __init__(self,core,value,known):
        self.core=core;self.known=known&M;self.value=value&self.known;self.status_generation=0
        self.writes=0;self.bit9_updates=0
        self.task_return_projections=0

    def read(self):
        return IcfgValue(self.value,self.known,(self.core,self.status_generation))

    def write(self,source):
        if isinstance(source,int):
            # Only literal patterns directly constrained by controls/pending
            # experiments. Other status-bit writes are not generalized.
            if source not in (0,0x200,0x300):
                blocked('icfg_literal_write_unmodeled',value=hex(source))
            value,known=source,GATES
        elif isinstance(source,IcfgValue):
            if source.origin!=(self.core,self.status_generation) or source.identity&~GATES&M!=(~GATES&M):
                blocked('icfg_status_write_lacks_identity',source=source.report(),core=self.core,
                        status_generation=self.status_generation)
            value,known=source.value,source.known&GATES
        else:
            if hasattr(source,'consumed'):source.consumed()
            blocked('icfg_write_source_unmodeled',source_type=type(source).__name__)
        self.value=(self.value&~GATES)|(value&known&GATES)
        self.known=(self.known&~GATES)|known;self.writes+=1

    def bit9(self,value,known=True):
        self.value=(self.value&~0x200)|(value&0x200 if known else 0)
        self.known=(self.known&~0x200)|(0x200 if known else 0);self.bit9_updates+=1

    def invalidate_after_bootstrap(self,preserve_status_mask=0):
        self.known&=preserve_status_mask&~GATES&M;self.value&=self.known
        self.status_generation+=1

    def gate_demand(self):
        # Any known closed gate proves delivery impossible even if unrelated
        # hardware status and the other gate are unknown.
        if self.known&~self.value&GATES:return 0
        return GATES if self.known&GATES==GATES else None

    def project_first_task_return(self,gate8):
        # Common assumptions of the comparison, not a silicon return rule:
        # retain gate9 and the explicitly empty active-priority byte. Drop
        # all unrelated status knowledge; evaluate alternative gate8 outcomes.
        self.known&=0x2ff;self.value&=self.known
        if gate8 is not None:
            self.known|=0x100
            if gate8:self.value|=0x100
        self.status_generation+=1;self.task_return_projections+=1

    def report(self):
        return {**self.read().report(),'gate_demand':self.gate_demand(),'writes':self.writes,
                'bit9_updates':self.bit9_updates,'complete_br28_writable_mask_certified':False,
                'status_or_task_rti_transition_simulated':bool(self.task_return_projections)}
