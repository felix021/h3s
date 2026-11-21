"""ctypes glue for the Go Timer0 / IRQController models (tools/gosim/periph.go).

The differential test test_timer0_irq_consistency.py drives the Go models
through this module and compares them byte-for-byte against the real Python
br28_model.Timer0 / br28_model.IRQController. Build the shared library first:

    cd tools/gosim && go build -buildmode=c-shared -o libgosim.so ./cshared
"""
import ctypes
import json
import os
from ctypes import c_int, c_uint32, c_uint64, c_uint8, POINTER

_HERE = os.path.dirname(os.path.abspath(__file__))
_LIB = os.path.join(_HERE, "libgosim.so")

# Error codes (mirror cshared/main.go).
PERIPH_OK = 0
PERIPH_ERR_BLOCKED = -6
PERIPH_ERR_VALUE = -7

_HANDLE = c_uint64
_ERR_CAP = 512


class _Lib:
    def __init__(self):
        lib = ctypes.CDLL(_LIB)
        # Timer0
        lib.gosim_timer_new.argtypes = [c_uint32]
        lib.gosim_timer_new.restype = _HANDLE
        lib.gosim_timer_free.argtypes = [_HANDLE]
        lib.gosim_timer_write.argtypes = [_HANDLE, c_int, c_uint32, POINTER(c_uint8), c_int]
        lib.gosim_timer_write.restype = c_int
        lib.gosim_timer_read.argtypes = [_HANDLE, c_int, POINTER(c_uint32), POINTER(c_uint8), c_int]
        lib.gosim_timer_read.restype = c_int
        lib.gosim_timer_advance.argtypes = [_HANDLE, ctypes.c_longlong]
        lib.gosim_timer_advance.restype = c_int
        lib.gosim_timer_steps_to_next.argtypes = [_HANDLE, ctypes.c_longlong, POINTER(c_int)]
        lib.gosim_timer_steps_to_next.restype = ctypes.c_longlong
        lib.gosim_timer_state.argtypes = [
            _HANDLE,
            POINTER(c_uint32), POINTER(c_uint32), POINTER(c_uint32),
            POINTER(c_int), POINTER(c_int),
            POINTER(ctypes.c_longlong), POINTER(ctypes.c_longlong), POINTER(ctypes.c_longlong),
        ]
        lib.gosim_timer_state.restype = c_int
        # IRQController
        lib.gosim_irq_new.argtypes = [c_int]
        lib.gosim_irq_new.restype = _HANDLE
        lib.gosim_irq_free.argtypes = [_HANDLE]
        lib.gosim_irq_set_config_word.argtypes = [_HANDLE, c_int, c_int, c_uint32]
        lib.gosim_irq_set_ipmask.argtypes = [_HANDLE, c_int, c_uint32]
        lib.gosim_irq_eligible.argtypes = [_HANDLE, c_int, c_uint32, POINTER(c_uint8), c_int]
        lib.gosim_irq_eligible.restype = c_int
        self.lib = lib


_LIB_SINGLETON = _Lib()


def _err(errbuf):
    s = bytes(errbuf).split(b"\x00", 1)[0]
    return json.loads(s.decode()) if s else None


class GosimTimer:
    """One Go Timer0 handle."""

    def __init__(self, allowed_sources=(4,)):
        mask = 0
        for s in allowed_sources:
            mask |= 1 << s
        self.handle = _LIB_SINGLETON.lib.gosim_timer_new(mask)

    def write(self, offset, value):
        """Returns ("ok", None) or ("error", details_dict)."""
        errbuf = (c_uint8 * _ERR_CAP)()
        code = _LIB_SINGLETON.lib.gosim_timer_write(self.handle, offset, value, errbuf, _ERR_CAP)
        if code == PERIPH_OK:
            return ("ok", None)
        return ("error", _err(errbuf))

    def read(self, offset):
        """Returns ("ok", value) or ("error", details_dict)."""
        out = c_uint32()
        errbuf = (c_uint8 * _ERR_CAP)()
        code = _LIB_SINGLETON.lib.gosim_timer_read(self.handle, offset, ctypes.byref(out), errbuf, _ERR_CAP)
        if code == PERIPH_OK:
            return ("ok", out.value)
        return ("error", _err(errbuf))

    def advance(self, ticks):
        """Returns ("ok", None) or ("valueerror", None)."""
        code = _LIB_SINGLETON.lib.gosim_timer_advance(self.handle, ticks)
        if code == PERIPH_OK:
            return ("ok", None)
        return ("valueerror", None)

    def steps_to_next(self, rate):
        """Returns ("ok", n) or ("none", None)."""
        ok = c_int()
        n = _LIB_SINGLETON.lib.gosim_timer_steps_to_next(self.handle, rate, ctypes.byref(ok))
        if ok.value == 0:
            return ("none", None)
        return ("ok", n)

    def state(self):
        """Returns the full Timer0 state as a tuple mirroring the Python attrs."""
        con, cnt, prd = c_uint32(), c_uint32(), c_uint32()
        pending, inflight = c_int(), c_int()
        rem, srem, exp = (ctypes.c_longlong(), ctypes.c_longlong(), ctypes.c_longlong())
        has = _LIB_SINGLETON.lib.gosim_timer_state(
            self.handle,
            ctypes.byref(con), ctypes.byref(cnt), ctypes.byref(prd),
            ctypes.byref(pending), ctypes.byref(inflight),
            ctypes.byref(rem), ctypes.byref(srem), ctypes.byref(exp))
        return (
            con.value if has & 1 else None,
            cnt.value if has & 2 else None,
            prd.value if has & 4 else None,
            bool(pending.value),
            rem.value,
            srem.value,
            exp.value,
            bool(inflight.value),
        )


class GosimIRQ:
    """One Go IRQController handle."""

    def __init__(self, unmaskable_projection=False):
        self.handle = _LIB_SINGLETON.lib.gosim_irq_new(1 if unmaskable_projection else 0)

    def set_config_word(self, index, value):
        if value is None:
            _LIB_SINGLETON.lib.gosim_irq_set_config_word(self.handle, index, 0, 0)
        else:
            _LIB_SINGLETON.lib.gosim_irq_set_config_word(self.handle, index, 1, value)

    def set_ipmask(self, value):
        if value is None:
            _LIB_SINGLETON.lib.gosim_irq_set_ipmask(self.handle, 0, 0)
        else:
            _LIB_SINGLETON.lib.gosim_irq_set_ipmask(self.handle, 1, value)

    def eligible(self, index, icfg):
        """Returns ("ok", bool) or ("error", details_dict)."""
        errbuf = (c_uint8 * _ERR_CAP)()
        code = _LIB_SINGLETON.lib.gosim_irq_eligible(self.handle, index, icfg, errbuf, _ERR_CAP)
        if code == 1:
            return ("ok", True)
        if code == 0:
            return ("ok", False)
        return ("error", _err(errbuf))
