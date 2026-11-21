"""ctypes glue for the Go World/RunLoop and PeriphState advance-chain state
transfer (tools/gosim/cshared/world.go + periph_state.go).

Two pieces for the whole-loop second wiring:

* GosimWorld — wrap a CPU (flat fetch mem + optional strict data mem) in a
  single-core World with an empty Periph/after_step, and run one RunLoop call.
  The per-step observations come back as JSON and are compared byte-for-byte
  against the Python reference by test_world_runloop_consistency.py.

* GosimPeriph — a PeriphState whose Timer0 / IRQ / core-tick state is moved in
  from the live Python BR28Model (json), stepped via Advance, and read back as
  json. The differential test test_advance_consistency.py compares the advance
  outcome and the resulting state against hardware.advance.

Build the shared library first:
    cd tools/gosim && go build -buildmode=c-shared -o libgosim.so ./cshared
"""
import ctypes
import json
import os
from ctypes import c_int, c_uint32, c_uint64, c_uint8, POINTER

_HERE = os.path.dirname(os.path.abspath(__file__))
_LIB = os.path.join(_HERE, "libgosim.so")

_HANDLE = c_uint64
_BUF_CAP = 1 << 20  # RunLoop steps + periph state JSON are bounded and small


class _Lib:
    def __init__(self):
        lib = ctypes.CDLL(_LIB)
        self.lib = lib

        lib.gosim_world_new.argtypes = [_HANDLE, _HANDLE, c_uint32, c_uint32]
        lib.gosim_world_new.restype = _HANDLE
        lib.gosim_world_set_periph.argtypes = [_HANDLE, _HANDLE]
        lib.gosim_world_free.argtypes = [_HANDLE]
        lib.gosim_world_run_loop.argtypes = [_HANDLE, c_int, POINTER(c_uint8), c_int]
        lib.gosim_world_run_loop.restype = c_int

        lib.gosim_periph_new.argtypes = []
        lib.gosim_periph_new.restype = _HANDLE
        lib.gosim_periph_free.argtypes = [_HANDLE]
        lib.gosim_periph_load.argtypes = [_HANDLE, POINTER(c_uint8), c_int,
                                          POINTER(c_uint8), c_int]
        lib.gosim_periph_load.restype = c_int
        lib.gosim_periph_state.argtypes = [_HANDLE, POINTER(c_uint8), c_int]
        lib.gosim_periph_state.restype = c_int
        lib.gosim_periph_advance.argtypes = [_HANDLE, c_uint32, POINTER(c_uint8), c_int]
        lib.gosim_periph_advance.restype = c_int


_LIB_SINGLETON = _Lib()


def _out(fn, *args):
    buf = (c_uint8 * _BUF_CAP)()
    n = fn(*args, buf, _BUF_CAP)
    if n < 0:
        return None
    return json.loads(bytes(buf[:n]).split(b"\x00", 1)[0].decode())


def _in(payload):
    b = payload.encode() if isinstance(payload, str) else json.dumps(payload).encode()
    return (c_uint8 * len(b)).from_buffer_copy(b), len(b)


class GosimWorld:
    """One single-core World: core0 CPU + strict memory + empty Periph/after_step."""

    def __init__(self, cpu_handle, smem_handle=0, fetch_lo=0, fetch_hi=0):
        self.handle = _LIB_SINGLETON.lib.gosim_world_new(cpu_handle, smem_handle,
                                                         fetch_lo, fetch_hi)

    def set_periph(self, periph_handle):
        _LIB_SINGLETON.lib.gosim_world_set_periph(self.handle, periph_handle)

    def run_loop(self, n):
        return _out(_LIB_SINGLETON.lib.gosim_world_run_loop, self.handle, n)

    def free(self):
        _LIB_SINGLETON.lib.gosim_world_free(self.handle)


class GosimPeriph:
    """One PeriphState for the advance-chain differential test."""

    def __init__(self):
        self.handle = _LIB_SINGLETON.lib.gosim_periph_new()

    def load(self, state):
        """Move the live state (a dict of Python BR28Model attrs) into Go."""
        buf, n = _in(state)
        err = (c_uint8 * 512)()
        code = _LIB_SINGLETON.lib.gosim_periph_load(self.handle, buf, n, err, 512)
        if code != 0:
            raise ValueError(bytes(err).split(b"\x00", 1)[0].decode())

    def state(self):
        """Read the advance-relevant PeriphState back as a dict."""
        return _out(_LIB_SINGLETON.lib.gosim_periph_state, self.handle)

    def advance(self, icfg):
        """Run one Advance(icfg); returns {"empty", "irq", "err"}."""
        return _out(_LIB_SINGLETON.lib.gosim_periph_advance, self.handle, icfg)

    def free(self):
        _LIB_SINGLETON.lib.gosim_periph_free(self.handle)
