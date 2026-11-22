"""ctypes glue for the Go strict memory (tools/gosim/mem.go, type Memory).

The differential test test_mem_consistency.py drives the Go memory through this
module and compares it byte-for-byte against the Python probe_boot.Memory
semantics. Build the shared library first:

    cd tools/gosim && go build -buildmode=c-shared -o libgosim.so ./cshared
"""
import ctypes
import json
import os
from ctypes import c_char_p, c_int, c_uint32, c_uint64, c_uint8, POINTER

_HERE = os.path.dirname(os.path.abspath(__file__))
_LIB = os.path.join(_HERE, "libgosim.so")

# Error codes (mirror cshared/main.go).
MEM_OK = 0
MEM_ERR_INVALID = -1
MEM_ERR_UNINITIALIZED = -2
MEM_ERR_UNMAPPED = -3
MEM_ERR_UNMAPPED_FETCH = -4
MEM_ERR_READONLY_WRITE = -5

_ERR_KIND = {
    MEM_ERR_UNINITIALIZED: "uninitialized_memory",
    MEM_ERR_UNMAPPED: "unmapped_memory",
    MEM_ERR_UNMAPPED_FETCH: "unmapped_instruction_fetch",
    MEM_ERR_READONLY_WRITE: "unmapped_or_readonly_write",
    MEM_ERR_INVALID: "invalid_memory_request",
}

_HANDLE = c_uint64
_ERR_CAP = 512


class StrictMem:
    """One Go strict-memory handle, reusable across a single differential run."""

    def __init__(self):
        self.lib = ctypes.CDLL(_LIB)
        self.lib.gosim_smem_new.restype = _HANDLE
        self.lib.gosim_smem_free.argtypes = [_HANDLE]
        self.lib.gosim_smem_set_rom.argtypes = [_HANDLE, POINTER(c_uint8), c_int]
        self.lib.gosim_smem_set_app.argtypes = [_HANDLE, c_uint32, POINTER(c_uint8), c_int]
        self.lib.gosim_smem_add_region.argtypes = [_HANDLE, c_uint32, c_uint32, c_char_p]
        self.lib.gosim_smem_add_readonly.argtypes = [_HANDLE, c_uint32, POINTER(c_uint8), c_int]
        self.lib.gosim_smem_set_pll.argtypes = [_HANDLE, c_uint32, c_int]
        self.lib.gosim_smem_set_fetch.argtypes = [_HANDLE, c_int]
        self.lib.gosim_smem_read.argtypes = [_HANDLE, c_uint32, POINTER(c_uint8), c_int,
                                             POINTER(c_uint8), c_int]
        self.lib.gosim_smem_read.restype = c_int
        self.lib.gosim_smem_write.argtypes = [_HANDLE, c_uint32, POINTER(c_uint8), c_int,
                                              POINTER(c_uint8), c_int]
        self.lib.gosim_smem_write.restype = c_int
        self.lib.gosim_smem_replace_ram.argtypes = [_HANDLE, POINTER(c_uint8), c_int]
        self.lib.gosim_smem_replace_ram.restype = None
        self.lib.gosim_smem_import_ram.argtypes = [_HANDLE, POINTER(c_uint8), c_int]
        self.lib.gosim_smem_import_ram.restype = None
        self.lib.gosim_smem_remove_ram.argtypes = [_HANDLE, POINTER(c_uint8), c_int]
        self.lib.gosim_smem_remove_ram.restype = None
        self.handle = self.lib.gosim_smem_new()

    def _buf(self, data):
        return (c_uint8 * len(data)).from_buffer_copy(data)

    def set_rom(self, data):
        if data is None:
            self.lib.gosim_smem_set_rom(self.handle, None, 0)
        else:
            buf = self._buf(data)
            self.lib.gosim_smem_set_rom(self.handle, buf, len(data))

    def set_app(self, entry, data):
        buf = self._buf(data)
        self.lib.gosim_smem_set_app(self.handle, entry, buf, len(data))

    def add_region(self, lo, hi, kind):
        self.lib.gosim_smem_add_region(self.handle, lo, hi, kind.encode())

    def add_readonly(self, base, data):
        buf = self._buf(data)
        self.lib.gosim_smem_add_readonly(self.handle, base, buf, len(data))

    def set_pll(self, v, present=True):
        self.lib.gosim_smem_set_pll(self.handle, v, 1 if present else 0)

    def set_fetch(self, fetch):
        self.lib.gosim_smem_set_fetch(self.handle, 1 if fetch else 0)

    def _err(self, errbuf):
        s = bytes(errbuf).split(b"\x00", 1)[0]
        return json.loads(s.decode()) if s else None

    def read(self, addr, n):
        """Returns ("ok", bytes) or ("error", details_dict)."""
        buf = (c_uint8 * n)()
        errbuf = (c_uint8 * _ERR_CAP)()
        code = self.lib.gosim_smem_read(self.handle, addr, buf, n, errbuf, _ERR_CAP)
        if code == MEM_OK:
            return ("ok", bytes(buf))
        return ("error", self._err(errbuf))

    def write(self, addr, data):
        """Returns ("ok", None) or ("error", details_dict)."""
        buf = self._buf(data)
        errbuf = (c_uint8 * _ERR_CAP)()
        code = self.lib.gosim_smem_write(self.handle, addr, buf, len(data), errbuf, _ERR_CAP)
        if code == MEM_OK:
            return ("ok", None)
        return ("error", self._err(errbuf))

    def replace_ram(self, entries):
        """Install a whole known-byte map. entries is a flat bytes buffer of
        [u32 little-endian addr][u8 value] per byte (Python mem.ram snapshot).
        Replaces the Go ram map wholesale (no guest-write side effects)."""
        buf = (c_uint8 * len(entries)).from_buffer_copy(entries)
        self.lib.gosim_smem_replace_ram(self.handle, buf, len(entries))

    def import_ram(self, entries):
        """Add known bytes to the Go ram map (incremental delta sync). entries
        has the same [u32 addr][u8 value] layout as replace_ram, but only the
        changed bytes. Additive; absent bytes stay absent."""
        if not entries:
            return
        buf = (c_uint8 * len(entries)).from_buffer_copy(entries)
        self.lib.gosim_smem_import_ram(self.handle, buf, len(entries))

    def remove_ram(self, addrs):
        """Mark bytes unknown (incremental delta sync). addrs is a bytes buffer
        of [u32 little-endian] addresses to drop from the Go ram map."""
        if not addrs:
            return
        buf = (c_uint8 * len(addrs)).from_buffer_copy(addrs)
        self.lib.gosim_smem_remove_ram(self.handle, buf, len(addrs))
