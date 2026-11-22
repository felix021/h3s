"""ctypes glue for the Go pi32v2 decode+execute core (tools/gosim/cshared).

Build the shared library first:
    cd tools/gosim && go build -buildmode=c-shared -o libgosim.so ./cshared
"""
import ctypes
import os
import struct
from ctypes import c_int, c_uint32, c_uint64, POINTER, c_uint8

_HERE = os.path.dirname(os.path.abspath(__file__))
_LIB = os.path.join(_HERE, "libgosim.so")

STEP_OK = 0
STEP_NO_INSTR = -1
STEP_NO_MODEL = -2
STEP_NEEDS_CTX = -3
STEP_OTHER = -4

# The C-side handle is a runtime/cgo.Handle (opaque uintptr).
_HANDLE = c_uint64


class Gosim:
    def __init__(self, arch=False):
        self.lib = ctypes.CDLL(_LIB)
        self.lib.gosim_new.restype = _HANDLE
        self.lib.gosim_new_arch.restype = _HANDLE
        self.lib.gosim_free.argtypes = [_HANDLE]
        self.lib.gosim_reset.argtypes = [_HANDLE]
        self.lib.gosim_mem_write.argtypes = [_HANDLE, c_uint32, POINTER(c_uint8), c_int]
        self.lib.gosim_mem_read.argtypes = [_HANDLE, c_uint32, POINTER(c_uint8), c_int]
        self.lib.gosim_set_regs.argtypes = [_HANDLE, POINTER(c_uint32)]
        self.lib.gosim_get_regs.argtypes = [_HANDLE, POINTER(c_uint32)]
        self.lib.gosim_set_state.argtypes = [_HANDLE, c_uint32, c_uint32, c_uint32, c_uint32]
        self.lib.gosim_cpu_set_icfg.argtypes = [_HANDLE, c_uint32]
        self.lib.gosim_get_state.argtypes = [_HANDLE, POINTER(c_uint32), POINTER(c_uint32),
                                             POINTER(c_uint32), POINTER(c_uint32)]
        self.lib.gosim_step.argtypes = [_HANDLE]
        self.lib.gosim_step.restype = c_int
        self.lib.gosim_batch_step.argtypes = [_HANDLE, c_int]
        self.lib.gosim_batch_step.restype = c_int
        self.lib.gosim_steps.argtypes = [_HANDLE]
        self.lib.gosim_steps.restype = c_uint64
        self.lib.gosim_signature_batch.argtypes = [POINTER(c_uint8), c_int, c_int,
                                                   POINTER(c_uint8), c_int]
        self.lib.gosim_signature_batch.restype = c_int
        self.lib.gosim_run_segment.argtypes = [_HANDLE, c_int, c_uint32, c_uint32,
                                               POINTER(c_uint8), c_int]
        self.lib.gosim_run_segment.restype = c_int
        self.lib.gosim_cpu_set_dmem.argtypes = [_HANDLE, _HANDLE]
        self.lib.gosim_clear_cache.argtypes = [_HANDLE]
        self.lib.gosim_invalidate_ram.argtypes = [_HANDLE, POINTER(c_uint8), c_int]
        self.handle = self.lib.gosim_new_arch() if arch else self.lib.gosim_new()

    def write(self, addr, data):
        buf = (c_uint8 * len(data)).from_buffer_copy(data)
        self.lib.gosim_mem_write(self.handle, addr, buf, len(data))

    def read(self, addr, n):
        buf = (c_uint8 * n)()
        self.lib.gosim_mem_read(self.handle, addr, buf, n)
        return bytes(buf)

    def set_regs(self, regs):
        buf = (c_uint32 * 16)(*regs)
        self.lib.gosim_set_regs(self.handle, buf)

    def get_regs(self):
        buf = (c_uint32 * 16)()
        self.lib.gosim_get_regs(self.handle, buf)
        return list(buf)

    def set_state(self, flags, sp, rets, pc):
        self.lib.gosim_set_state(self.handle, flags, sp, rets, pc)

    def set_icfg(self, icfg):
        self.lib.gosim_cpu_set_icfg(self.handle, icfg)

    def get_state(self):
        flags, sp, rets, pc = c_uint32(), c_uint32(), c_uint32(), c_uint32()
        self.lib.gosim_get_state(self.handle, flags, sp, rets, pc)
        return flags.value, sp.value, rets.value, pc.value

    def reset(self):
        self.lib.gosim_reset(self.handle)

    def clear_cache(self):
        self.lib.gosim_clear_cache(self.handle)

    def invalidate_ram(self, addrs):
        """Drop decode-cache entries overlapping a list of changed RAM byte
        addresses (incremental-sync cache invalidation)."""
        if not addrs:
            return
        raw = struct.pack('<%dI' % len(addrs), *addrs)
        buf = (c_uint8 * len(raw)).from_buffer_copy(raw)
        self.lib.gosim_invalidate_ram(self.handle, buf, len(raw))

    def set_data_mem(self, smem_handle):
        """Bind a strict Memory (gosim_mem.StrictMem.handle) as this CPU's data
        memory. smem_handle == 0 unbinds (flat-memory fallback)."""
        self.lib.gosim_cpu_set_dmem(self.handle, smem_handle)

    def step(self):
        return self.lib.gosim_step(self.handle)

    def batch_step(self, n):
        return self.lib.gosim_batch_step(self.handle, n)

    def steps(self):
        return self.lib.gosim_steps(self.handle)

    def signature_batch(self, inbuf, n, outcap):
        """Format n type-tagged register descriptors (see signature.go) into
        u16-length-prefixed strings. Returns the raw out bytes, or raises if
        the output buffer was too small (written == -1)."""
        inbuf = (c_uint8 * len(inbuf)).from_buffer_copy(inbuf)
        outbuf = (c_uint8 * outcap)()
        written = self.lib.gosim_signature_batch(inbuf, len(inbuf), n, outbuf, outcap)
        if written < 0:
            raise MemoryError('gosim_signature_batch: output buffer too small')
        return bytes(outbuf[:written])

    def run_segment(self, n, fetch_lo, fetch_hi, outcap):
        """Run up to n contract-compliant steps in one cgo call (see
        segment.go / gosim_batch.py). Returns the raw serialized result bytes,
        or raises if the output buffer was too small (written == -1)."""
        outbuf = (c_uint8 * outcap)()
        written = self.lib.gosim_run_segment(self.handle, n, fetch_lo, fetch_hi, outbuf, outcap)
        if written < 0:
            raise MemoryError('gosim_run_segment: output buffer too small')
        return bytes(outbuf[:written])
