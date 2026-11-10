"""Unconstrained task words: preserve data, stop before evaluating it.

These are abstract values, never numeric reset defaults. Arithmetic, predicates,
addresses and serialization require known operands and fail closed. Only explicit
copy/stack adapters may transport an unknown word without evaluating it.
"""


class UnknownWord:
    __slots__=('bytes', 'origins')

    def __init__(self,values,origins):
        self.bytes=tuple(values);self.origins=tuple(origins)
        if len(self.bytes)!=4 or len(self.origins)!=4 or all(v is not None for v in self.bytes):
            raise ValueError('unknown word requires exactly four bytes and at least one unknown')
        if any(v is not None and (not isinstance(v,int) or not 0<=v<=255) for v in self.bytes):
            raise ValueError('invalid partial word bytes')
        if any((v is None and (not isinstance(a,int) or not 0<=a<=0xffffffff)) or
               (v is not None and a is not None) for v,a in zip(self.bytes,self.origins)):
            raise ValueError('unknown bytes require physical provenance; known bytes have no unknown origin')

    def consumed(self,*args,**kwargs):
        from probe_boot import Blocked
        raise Blocked('unknown_task_register_consumed',
                      sources=[hex(a) if a is not None else None for a in self.origins],
                      known_bytes=''.join('??' if v is None else f'{v:02x}' for v in self.bytes))

    __int__=__index__=__float__=__bool__=consumed
    __hash__=consumed
    __eq__=__ne__=__lt__=__le__=__gt__=__ge__=consumed
    __and__=__rand__=__or__=__ror__=__xor__=__rxor__=consumed
    __add__=__radd__=__sub__=__rsub__=__mul__=__rmul__=consumed
    __lshift__=__rlshift__=__rshift__=__rrshift__=consumed
    __truediv__=__rtruediv__=__floordiv__=__rfloordiv__=consumed
    __mod__=__rmod__=__pow__=__rpow__=__neg__=__pos__=__invert__=consumed

    def __repr__(self):return 'UnknownWord'


def word(value):
    return hex(value) if isinstance(value,int) else 'unknown'
