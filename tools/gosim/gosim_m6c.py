"""ctypes glue for the Go M6 Stage C delivery projections (tools/gosim/deliver.go).

The differential test test_m6c_deliver_consistency.py drives the Go deliver_*
/ return_* projections through this module and compares them against the real
br28_machine.DualCoreMachine.deliver_* / return_* branch-for-branch. Build the
shared library first:

    cd tools/gosim && go build -buildmode=c-shared -o libgosim.so ./cshared
"""
import ctypes
import json
import os
from ctypes import c_int, c_uint8, c_uint32, c_uint64, POINTER

_HERE = os.path.dirname(os.path.abspath(__file__))
_LIB = os.path.join(_HERE, "libgosim.so")

_HANDLE = c_uint64
_STATE_CAP = 65536
_ERR_CAP = 4096

_ERR_LOAD = -20
_ERR_VALUE = -21
_ERR_BLOCKED = -22

# operation codes (mirrored in cshared/deliver.go).
OP_SOFTWARE_IRQ = 0
OP_CORE_TICK = 1
OP_TIMER0_IRQ = 2
OP_AUDIO_DMA_IRQ = 3
OP_SRC_SYNC_IRQ = 4
OP_RETURN_SOFTWARE_IRQ = 5
OP_RETURN_CORE_TICK = 6
OP_RETURN_TIMER0_IRQ = 7
OP_RETURN_AUDIO_DMA_IRQ = 8
OP_RETURN_SRC_SYNC_IRQ = 9


class _Lib:
    def __init__(self):
        lib = ctypes.CDLL(_LIB)
        self.lib = lib
        lib.gosim_deliver_new.argtypes = []
        lib.gosim_deliver_new.restype = _HANDLE
        lib.gosim_deliver_free.argtypes = [_HANDLE]
        lib.gosim_deliver_load.argtypes = [_HANDLE, POINTER(c_uint8), c_int]
        lib.gosim_deliver_load.restype = c_int
        lib.gosim_deliver_call.argtypes = [_HANDLE, c_int, c_int, c_uint32, POINTER(c_uint8), c_int]
        lib.gosim_deliver_call.restype = c_int
        lib.gosim_deliver_state.argtypes = [_HANDLE, POINTER(c_uint8), c_int]
        lib.gosim_deliver_state.restype = c_int


_LIB_SINGLETON = _Lib()


class _DeliverError(Exception):
    def __init__(self, kind, payload):
        super().__init__(payload)
        self.kind = kind  # "ValueError" or "Blocked"
        self.payload = payload  # message string or details dict


class GosimDeliver:
    def __init__(self):
        self.handle = _LIB_SINGLETON.lib.gosim_deliver_new()

    def __del__(self):
        _LIB_SINGLETON.lib.gosim_deliver_free(self.handle)

    def load(self, state):
        raw = json.dumps(state).encode("utf-8")
        buf = (c_uint8 * len(raw)).from_buffer_copy(raw)
        rc = _LIB_SINGLETON.lib.gosim_deliver_load(self.handle, buf, len(raw))
        if rc == _ERR_LOAD:
            raise ValueError("gosim deliver load failed")

    def call(self, op, core_id, irq=0):
        err_buf = (c_uint8 * _ERR_CAP)()
        rc = _LIB_SINGLETON.lib.gosim_deliver_call(
            self.handle, op, core_id, irq, err_buf, _ERR_CAP)
        if rc == 0:
            return None
        text = bytes(err_buf).split(b"\x00")[0].decode("utf-8")
        if rc == _ERR_VALUE:
            raise _DeliverError("ValueError", text)
        if rc == _ERR_BLOCKED:
            raise _DeliverError("Blocked", json.loads(text))
        raise ValueError("gosim deliver call failed (rc=%d)" % rc)

    def state(self):
        buf = (c_uint8 * _STATE_CAP)()
        n = _LIB_SINGLETON.lib.gosim_deliver_state(self.handle, buf, _STATE_CAP)
        return json.loads(bytes(buf[:n]).decode("utf-8"))
