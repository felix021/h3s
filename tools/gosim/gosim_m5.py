"""ctypes glue for the Go M5 peripheral models (tools/gosim/periph_m5.go).

The differential test test_m5_consistency.py drives the Go JLSrcSyncDelivery
through this module and compares it byte-for-byte against the real Python
br28_model.JLSrcSyncDelivery. Build the shared library first:

    cd tools/gosim && go build -buildmode=c-shared -o libgosim.so ./cshared
"""
import ctypes
import json
import os
from ctypes import c_int, c_uint32, c_uint64, c_longlong, c_uint8, POINTER

_HERE = os.path.dirname(os.path.abspath(__file__))
_LIB = os.path.join(_HERE, "libgosim.so")

_HANDLE = c_uint64
_STATE_CAP = 4096


class _Lib:
    def __init__(self):
        lib = ctypes.CDLL(_LIB)
        self.lib = lib

        # JLSrcSyncDelivery
        lib.gosim_srcsync_new.argtypes = [c_longlong]
        lib.gosim_srcsync_new.restype = _HANDLE
        lib.gosim_srcsync_free.argtypes = [_HANDLE]
        lib.gosim_srcsync_note_start.argtypes = [_HANDLE, c_longlong]
        lib.gosim_srcsync_advance.argtypes = [_HANDLE, c_longlong]
        lib.gosim_srcsync_note_con_write.argtypes = [_HANDLE, c_uint32]
        lib.gosim_srcsync_reentry_authorized.argtypes = [_HANDLE]
        lib.gosim_srcsync_reentry_authorized.restype = c_int
        lib.gosim_srcsync_state.argtypes = [_HANDLE, POINTER(c_uint8), c_int]
        lib.gosim_srcsync_state.restype = c_int


_LIB_SINGLETON = _Lib()


def _state(fn, handle):
    buf = (c_uint8 * _STATE_CAP)()
    fn(handle, buf, _STATE_CAP)
    return json.loads(bytes(buf).split(b"\x00", 1)[0].decode())


class GosimSrcSync:
    def __init__(self, completion_steps):
        self.handle = _LIB_SINGLETON.lib.gosim_srcsync_new(completion_steps)

    def note_start(self, step):
        _LIB_SINGLETON.lib.gosim_srcsync_note_start(self.handle, step)

    def advance(self, step):
        _LIB_SINGLETON.lib.gosim_srcsync_advance(self.handle, step)

    def note_con_write(self, value):
        _LIB_SINGLETON.lib.gosim_srcsync_note_con_write(self.handle, value)

    def reentry_authorized(self):
        return bool(_LIB_SINGLETON.lib.gosim_srcsync_reentry_authorized(self.handle))

    def state(self):
        return _state(_LIB_SINGLETON.lib.gosim_srcsync_state, self.handle)
