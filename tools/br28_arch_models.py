"""Use architectural k2 effects without its hardware-runner instrumentation.

The pinned upstream wraps every k2 model with a scratch-PC store and a runner
RETS normalization. Calling the exact inner model preserves real stores even
when their value coincides with PC; filtering the output by value would not.
"""
import hashlib

from probe_pi32v2 import CACHE, require

K2_SHA='0bf0b78a61898ee76ce9f74e0058354517a075d350d0ccf255d12253426a21d7'


def architectural_k2(models):
    source=CACHE/'tools/hwtest/semantics/k2.py'
    require(hashlib.sha256(source.read_bytes()).hexdigest()==K2_SHA,'k2 architectural adapter source changed')
    result=dict(models);names=[]
    for name,model in models.items():
        if getattr(model,'__module__',None)!='semantics.k2':continue
        require(model.__qualname__=='_with_stub.<locals>.f' and model.__code__.co_freevars==('fn',) and
                model.__closure__ is not None and len(model.__closure__)==1,'unknown k2 wrapper structure')
        inner=model.__closure__[0].cell_contents
        require(callable(inner) and inner.__module__=='semantics.k2' and
                inner.__qualname__!='_with_stub.<locals>.f','unexpected nested k2 effect')
        result[name]=inner;names.append(name)
    require(bool(names),'no pinned k2 wrappers found')
    return result,tuple(sorted(names))
