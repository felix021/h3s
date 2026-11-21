"""ctypes glue for the Go M6 Stage A machine (tools/gosim/machine.go).

The differential test test_m6_select_consistency.py drives the Go dual-core
select through this module and compares it branch-for-branch against the real
Python br28_machine.DualCoreMachine.select. Build the shared library first:

    cd tools/gosim && go build -buildmode=c-shared -o libgosim.so ./cshared
"""
import ctypes
import json
import os
from ctypes import c_int, c_uint64, c_uint8, POINTER

_HERE = os.path.dirname(os.path.abspath(__file__))
_LIB = os.path.join(_HERE, "libgosim.so")

_HANDLE = c_uint64
_STATE_CAP = 65536

_ERR_LOAD = -10
_ERR_WAKE = -11


class _Lib:
    def __init__(self):
        lib = ctypes.CDLL(_LIB)
        self.lib = lib
        lib.gosim_machine_new.argtypes = []
        lib.gosim_machine_new.restype = _HANDLE
        lib.gosim_machine_free.argtypes = [_HANDLE]
        lib.gosim_machine_load.argtypes = [_HANDLE, POINTER(c_uint8), c_int]
        lib.gosim_machine_load.restype = c_int
        lib.gosim_machine_select.argtypes = [_HANDLE]
        lib.gosim_machine_select.restype = c_int
        lib.gosim_machine_state.argtypes = [_HANDLE, POINTER(c_uint8), c_int]
        lib.gosim_machine_state.restype = c_int


_LIB_SINGLETON = _Lib()


class GosimMachine:
    def __init__(self):
        self.handle = _LIB_SINGLETON.lib.gosim_machine_new()

    def load(self, state):
        raw = json.dumps(state).encode("utf-8")
        buf = (c_uint8 * len(raw)).from_buffer_copy(raw)
        rc = _LIB_SINGLETON.lib.gosim_machine_load(self.handle, buf, len(raw))
        if rc == _ERR_LOAD:
            raise ValueError("gosim machine load failed")

    def select(self):
        rc = _LIB_SINGLETON.lib.gosim_machine_select(self.handle)
        if rc == _ERR_WAKE:
            raise ValueError("gosim select wake require violated")
        return rc

    def state(self):
        buf = (c_uint8 * _STATE_CAP)()
        n = _LIB_SINGLETON.lib.gosim_machine_state(self.handle, buf, _STATE_CAP)
        return json.loads(bytes(buf[:n]).decode("utf-8"))
