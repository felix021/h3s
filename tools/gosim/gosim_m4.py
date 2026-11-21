"""ctypes glue for the Go M4 peripheral models (tools/gosim/periph_m4.go).

The differential test test_m4_consistency.py drives the Go models through this
module and compares them byte-for-byte against the real Python
br28_model.CoreTickStopped / WatchdogProjection / IcuInitializationProjection /
Timer2Delay / SoftwareIrqCommands. Build the shared library first:

    cd tools/gosim && go build -buildmode=c-shared -o libgosim.so ./cshared
"""
import ctypes
import json
import os
from ctypes import c_int, c_uint32, c_uint64, c_uint8, c_longlong, POINTER

_HERE = os.path.dirname(os.path.abspath(__file__))
_LIB = os.path.join(_HERE, "libgosim.so")

PERIPH_OK = 0
PERIPH_ERR_BLOCKED = -6
PERIPH_ERR_VALUE = -7

_HANDLE = c_uint64
_ERR_CAP = 512
_STATE_CAP = 4096


class _Lib:
    def __init__(self):
        lib = ctypes.CDLL(_LIB)
        self.lib = lib

        # CoreTickStopped
        lib.gosim_coretick_new.argtypes = [c_int, c_uint32, c_int, c_longlong,
                                           c_int, c_int, c_int]
        lib.gosim_coretick_new.restype = _HANDLE
        lib.gosim_coretick_free.argtypes = [_HANDLE]
        lib.gosim_coretick_read.argtypes = [_HANDLE, c_int, POINTER(c_uint32),
                                            POINTER(c_uint8), c_int]
        lib.gosim_coretick_read.restype = c_int
        lib.gosim_coretick_write.argtypes = [_HANDLE, c_int, c_uint32,
                                             POINTER(c_uint8), c_int]
        lib.gosim_coretick_write.restype = c_int
        lib.gosim_coretick_advance.argtypes = [_HANDLE, POINTER(c_uint8), c_int]
        lib.gosim_coretick_advance.restype = c_int
        lib.gosim_coretick_ack_rmw.argtypes = [_HANDLE, ctypes.c_char_p, c_uint32,
                                               POINTER(c_uint8), c_int]
        lib.gosim_coretick_ack_rmw.restype = c_int
        lib.gosim_coretick_set_cnt_probe.argtypes = [_HANDLE, c_int, c_uint32]
        lib.gosim_coretick_state.argtypes = [_HANDLE, POINTER(c_uint8), c_int]
        lib.gosim_coretick_state.restype = c_int

        # WatchdogProjection
        lib.gosim_wdt_new.argtypes = [c_int, c_longlong, c_longlong]
        lib.gosim_wdt_new.restype = _HANDLE
        lib.gosim_wdt_free.argtypes = [_HANDLE]
        lib.gosim_wdt_read.argtypes = [_HANDLE, c_uint32, POINTER(c_uint32),
                                       POINTER(c_uint8), c_int]
        lib.gosim_wdt_read.restype = c_int
        lib.gosim_wdt_write.argtypes = [_HANDLE, c_uint32, c_uint32,
                                        POINTER(c_uint8), c_int]
        lib.gosim_wdt_write.restype = c_int
        lib.gosim_wdt_advance.argtypes = [_HANDLE, POINTER(c_uint8), c_int]
        lib.gosim_wdt_advance.restype = c_int
        lib.gosim_wdt_mailbox_feed.argtypes = [_HANDLE, POINTER(c_uint8), c_int]
        lib.gosim_wdt_mailbox_feed.restype = c_int
        lib.gosim_wdt_state.argtypes = [_HANDLE, POINTER(c_uint8), c_int]
        lib.gosim_wdt_state.restype = c_int

        # IcuInitializationProjection
        lib.gosim_icu_new.argtypes = [c_int, c_uint32, c_int, c_uint32, c_int,
                                      c_longlong, c_int]
        lib.gosim_icu_new.restype = _HANDLE
        lib.gosim_icu_free.argtypes = [_HANDLE]
        lib.gosim_icu_read.argtypes = [_HANDLE, c_uint32, POINTER(c_uint32),
                                       POINTER(c_uint8), c_int]
        lib.gosim_icu_read.restype = c_int
        lib.gosim_icu_write.argtypes = [_HANDLE, c_uint32, c_uint32,
                                        POINTER(c_uint8), c_int]
        lib.gosim_icu_write.restype = c_int
        lib.gosim_icu_advance.argtypes = [_HANDLE]
        lib.gosim_icu_state.argtypes = [_HANDLE, POINTER(c_uint8), c_int]
        lib.gosim_icu_state.restype = c_int

        # Timer2Delay
        lib.gosim_timer2_new.argtypes = []
        lib.gosim_timer2_new.restype = _HANDLE
        lib.gosim_timer2_free.argtypes = [_HANDLE]
        lib.gosim_timer2_write.argtypes = [_HANDLE, c_uint32, c_uint32,
                                           POINTER(c_uint8), c_int]
        lib.gosim_timer2_write.restype = c_int
        lib.gosim_timer2_read.argtypes = [_HANDLE, c_uint32, POINTER(c_uint32),
                                          POINTER(c_uint8), c_int]
        lib.gosim_timer2_read.restype = c_int
        lib.gosim_timer2_advance.argtypes = [_HANDLE, c_longlong]
        lib.gosim_timer2_advance.restype = c_int
        lib.gosim_timer2_steps_to_next.argtypes = [_HANDLE, c_longlong, POINTER(c_int)]
        lib.gosim_timer2_steps_to_next.restype = c_longlong
        lib.gosim_timer2_state.argtypes = [_HANDLE, POINTER(c_uint8), c_int]
        lib.gosim_timer2_state.restype = c_int

        # SoftwareIrqCommands
        lib.gosim_swirq_new.argtypes = [c_int, c_uint32, c_int, c_uint32,
                                        c_int, c_uint32, c_int, c_uint32]
        lib.gosim_swirq_new.restype = _HANDLE
        lib.gosim_swirq_free.argtypes = [_HANDLE]
        lib.gosim_swirq_command.argtypes = [_HANDLE, c_uint32, c_uint32,
                                            POINTER(c_uint8), c_int]
        lib.gosim_swirq_command.restype = c_int
        lib.gosim_swirq_source_rmw.argtypes = [_HANDLE, c_uint32, ctypes.c_char_p, c_uint32,
                                               POINTER(c_uint8), c_int]
        lib.gosim_swirq_source_rmw.restype = c_int
        lib.gosim_swirq_ack.argtypes = [_HANDLE, c_int, c_uint32]
        lib.gosim_swirq_state.argtypes = [_HANDLE, POINTER(c_uint8), c_int]
        lib.gosim_swirq_state.restype = c_int


_LIB_SINGLETON = _Lib()


def _err(errbuf):
    s = bytes(errbuf).split(b"\x00", 1)[0]
    return json.loads(s.decode()) if s else None


def _state(fn, handle):
    buf = (c_uint8 * _STATE_CAP)()
    fn(handle, buf, _STATE_CAP)
    return json.loads(bytes(buf).split(b"\x00", 1)[0].decode())


def _buf():
    return (c_uint8 * _ERR_CAP)()


# ---- CoreTickStopped ---------------------------------------------------------

class GosimCoreTick:
    def __init__(self, initial_con=None, progress=None, prd_readback=False,
                 cnt_held_snapshot=False, freerun_edge_period=False):
        self.handle = _LIB_SINGLETON.lib.gosim_coretick_new(
            0 if initial_con is None else 1, 0 if initial_con is None else initial_con,
            0 if progress is None else 1, 0 if progress is None else progress,
            1 if prd_readback else 0, 1 if cnt_held_snapshot else 0,
            1 if freerun_edge_period else 0)

    def read(self, offset):
        out = c_uint32()
        err = _buf()
        code = _LIB_SINGLETON.lib.gosim_coretick_read(self.handle, offset, ctypes.byref(out), err, _ERR_CAP)
        if code == PERIPH_OK:
            return ("ok", out.value)
        return ("error", _err(err))

    def write(self, offset, value):
        err = _buf()
        code = _LIB_SINGLETON.lib.gosim_coretick_write(self.handle, offset, value, err, _ERR_CAP)
        if code == PERIPH_OK:
            return ("ok", None)
        if code == PERIPH_ERR_VALUE:
            return ("valueerror", None)
        return ("error", _err(err))

    def advance(self):
        err = _buf()
        code = _LIB_SINGLETON.lib.gosim_coretick_advance(self.handle, err, _ERR_CAP)
        if code == PERIPH_OK:
            return ("ok", None)
        return ("error", _err(err))

    def ack_rmw(self, kind, operand):
        err = _buf()
        code = _LIB_SINGLETON.lib.gosim_coretick_ack_rmw(
            self.handle, kind.encode(), operand, err, _ERR_CAP)
        if code == PERIPH_OK:
            return ("ok", None)
        return ("error", _err(err))

    def set_cnt_probe(self, value):
        if value is None:
            _LIB_SINGLETON.lib.gosim_coretick_set_cnt_probe(self.handle, 0, 0)
        else:
            _LIB_SINGLETON.lib.gosim_coretick_set_cnt_probe(self.handle, 1, value)

    def state(self):
        return _state(_LIB_SINGLETON.lib.gosim_coretick_state, self.handle)


# ---- WatchdogProjection -------------------------------------------------------

class GosimWatchdog:
    def __init__(self, progress=None):
        if progress is None:
            self.handle = _LIB_SINGLETON.lib.gosim_wdt_new(0, 0, 0)
        else:
            self.handle = _LIB_SINGLETON.lib.gosim_wdt_new(1, progress[0], progress[1])

    def read(self, address):
        out = c_uint32()
        err = _buf()
        code = _LIB_SINGLETON.lib.gosim_wdt_read(self.handle, address, ctypes.byref(out), err, _ERR_CAP)
        if code == PERIPH_OK:
            return ("ok", out.value)
        return ("error", _err(err))

    def write(self, address, value):
        err = _buf()
        code = _LIB_SINGLETON.lib.gosim_wdt_write(self.handle, address, value, err, _ERR_CAP)
        if code == PERIPH_OK:
            return ("ok", None)
        if code == PERIPH_ERR_VALUE:
            return ("valueerror", None)
        return ("error", _err(err))

    def advance(self):
        err = _buf()
        code = _LIB_SINGLETON.lib.gosim_wdt_advance(self.handle, err, _ERR_CAP)
        if code == PERIPH_OK:
            return ("ok", None)
        return ("error", _err(err))

    def mailbox_feed(self):
        err = _buf()
        code = _LIB_SINGLETON.lib.gosim_wdt_mailbox_feed(self.handle, err, _ERR_CAP)
        if code == PERIPH_OK:
            return ("ok", None)
        return ("error", _err(err))

    def state(self):
        return _state(_LIB_SINGLETON.lib.gosim_wdt_state, self.handle)


# ---- IcuInitializationProjection ----------------------------------------------

class GosimICU:
    def __init__(self, initial_words=None, command_steps=None, region_projection=False):
        initial_words = initial_words or {}
        c0 = initial_words.get(0xffe400)
        c1 = initial_words.get(0xffec00)
        self.handle = _LIB_SINGLETON.lib.gosim_icu_new(
            0 if c0 is None else 1, 0 if c0 is None else c0,
            0 if c1 is None else 1, 0 if c1 is None else c1,
            0 if command_steps is None else 1, 0 if command_steps is None else command_steps,
            1 if region_projection else 0)

    def read(self, address):
        out = c_uint32()
        err = _buf()
        code = _LIB_SINGLETON.lib.gosim_icu_read(self.handle, address, ctypes.byref(out), err, _ERR_CAP)
        if code == PERIPH_OK:
            return ("ok", out.value)
        return ("error", _err(err))

    def write(self, address, value):
        err = _buf()
        code = _LIB_SINGLETON.lib.gosim_icu_write(self.handle, address, value, err, _ERR_CAP)
        if code == PERIPH_OK:
            return ("ok", None)
        return ("error", _err(err))

    def advance(self):
        _LIB_SINGLETON.lib.gosim_icu_advance(self.handle)
        return ("ok", None)

    def state(self):
        return _state(_LIB_SINGLETON.lib.gosim_icu_state, self.handle)


# ---- Timer2Delay ---------------------------------------------------------------

class GosimTimer2:
    def __init__(self):
        self.handle = _LIB_SINGLETON.lib.gosim_timer2_new()

    def write(self, address, value):
        err = _buf()
        code = _LIB_SINGLETON.lib.gosim_timer2_write(self.handle, address, value, err, _ERR_CAP)
        if code == PERIPH_OK:
            return ("ok", None)
        return ("error", _err(err))

    def read(self, address):
        out = c_uint32()
        err = _buf()
        code = _LIB_SINGLETON.lib.gosim_timer2_read(self.handle, address, ctypes.byref(out), err, _ERR_CAP)
        if code == PERIPH_OK:
            return ("ok", out.value)
        return ("error", _err(err))

    def advance(self, ticks):
        code = _LIB_SINGLETON.lib.gosim_timer2_advance(self.handle, ticks)
        if code == PERIPH_OK:
            return ("ok", None)
        return ("valueerror", None)

    def steps_to_next(self, rate):
        ok = c_int()
        n = _LIB_SINGLETON.lib.gosim_timer2_steps_to_next(self.handle, rate, ctypes.byref(ok))
        if ok.value == 0:
            return ("none", None)
        return ("ok", n)

    def state(self):
        return _state(_LIB_SINGLETON.lib.gosim_timer2_state, self.handle)


# ---- SoftwareIrqCommands -------------------------------------------------------

class GosimSwIrq:
    def __init__(self, initial_pending=None, initial_source_enable=None):
        p = (initial_pending or (None, None))
        s = (initial_source_enable or (None, None))
        self.handle = _LIB_SINGLETON.lib.gosim_swirq_new(
            0 if p[0] is None else 1, 0 if p[0] is None else p[0],
            0 if p[1] is None else 1, 0 if p[1] is None else p[1],
            0 if s[0] is None else 1, 0 if s[0] is None else s[0],
            0 if s[1] is None else 1, 0 if s[1] is None else s[1])

    def command(self, address, mask):
        err = _buf()
        code = _LIB_SINGLETON.lib.gosim_swirq_command(self.handle, address, mask, err, _ERR_CAP)
        if code == PERIPH_OK:
            return ("ok", None)
        return ("error", _err(err))

    def source_rmw(self, address, kind, operand):
        err = _buf()
        code = _LIB_SINGLETON.lib.gosim_swirq_source_rmw(
            self.handle, address, kind.encode(), operand, err, _ERR_CAP)
        if code == PERIPH_OK:
            return ("ok", None)
        return ("error", _err(err))

    def acknowledge(self, core, bit):
        _LIB_SINGLETON.lib.gosim_swirq_ack(self.handle, core, bit)
        return ("ok", None)

    def state(self):
        return _state(_LIB_SINGLETON.lib.gosim_swirq_state, self.handle)
