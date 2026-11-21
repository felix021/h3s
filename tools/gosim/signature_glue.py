"""ctypes glue for the Go register-signature formatter (tools/gosim/signature.go).

Encodes the 16 CPU registers into type-tagged descriptors, calls the Go
SignaturesBatch export once per step, and decodes the returned strings. It
replaces map(_register_signature, cpu.R) in TraceMachine.after_step when the
opt-in is enabled; the sys.intern identity semantics stay on the Python side
(route DT).

The Go formatter is byte-identical to tools/analyze_image_trace.py
_register_signature (proven by test_signature_consistency.py over the full
type x value space). The encoder is a fast path: if any value cannot be
represented in the descriptor (an int outside int64 — the masked register file
never holds one), it returns None and the caller falls back to the pure-Python
path.

This module loads libgosim.so directly (not via tools/gosim/gosim.py) so it
works whether tools/gosim is imported as a namespace package (from the tools/
directory) or as a flat module directory (as test_consistency.py does).
"""
import ctypes
import struct
import sys
from ctypes import POINTER, c_int, c_uint8
from pathlib import Path

_GOSIM = Path(__file__).resolve().parent
_TOOLS = _GOSIM.parent
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))

from br28_icfg import IcfgValue  # noqa: E402
from br28_unknown import UnknownWord  # noqa: E402

_INT_MIN = -(1 << 63)
_INT_MAX = (1 << 63) - 1

# Descriptor tags — must match signature.go.
TAG_INT = 0
TAG_BOOL = 1
TAG_WORD = 2
TAG_ICFG = 3
TAG_OTHER = 4

_STR_CAP = 64  # generous per-string capacity (UnknownWord ~53 chars worst case)


def encode_register_values(R):
    """Encode register values into type-tagged descriptors (bytes). Returns
    None if any int value is outside int64 — the caller falls back to Python."""
    buf = bytearray()
    for v in R:
        if isinstance(v, bool):  # bool before int, mirrors _register_signature
            buf.append(TAG_BOOL)
            buf.append(1 if v else 0)
        elif isinstance(v, int):
            if not _INT_MIN <= v <= _INT_MAX:
                return None
            buf.append(TAG_INT)
            buf += struct.pack('<q', v)
        elif isinstance(v, UnknownWord):
            known = 0
            for i, b in enumerate(v.bytes):
                if b is not None:
                    known |= 1 << i
            buf.append(TAG_WORD)
            buf.append(known)
            for b in v.bytes:
                buf.append(b if b is not None else 0)
            for o in v.origins:
                buf += struct.pack('<I', o if o is not None else 0)
        elif isinstance(v, IcfgValue):
            buf.append(TAG_ICFG)
            buf += struct.pack('<II', v.known, v.value)
        else:
            buf.append(TAG_OTHER)
    return bytes(buf)


def decode_signature_batch(out, n):
    """Decode n u16-length-prefixed strings into a list of sys.intern'd str."""
    result = []
    pos = 0
    for _ in range(n):
        (length,) = struct.unpack_from('<H', out, pos)
        pos += 2
        result.append(sys.intern(bytes(out[pos:pos + length]).decode('ascii')))
        pos += length
    return result


_lib = None


def _load():
    global _lib
    lib = ctypes.CDLL(str(_GOSIM / 'libgosim.so'))
    lib.gosim_signature_batch.argtypes = [POINTER(c_uint8), c_int, c_int,
                                          POINTER(c_uint8), c_int]
    lib.gosim_signature_batch.restype = c_int
    _lib = lib


def register_signatures(R):
    """Format the registers via Go, returning a list of interned strings.
    Returns None on the unencodable-value fallback."""
    enc = encode_register_values(R)
    if enc is None:
        return None
    if _lib is None:
        _load()
    n = len(R)
    inbuf = (c_uint8 * len(enc)).from_buffer_copy(enc)
    outbuf = (c_uint8 * (n * _STR_CAP))()
    written = _lib.gosim_signature_batch(inbuf, len(enc), n, outbuf, n * _STR_CAP)
    if written < 0:
        raise MemoryError('gosim_signature_batch: output buffer too small')
    return decode_signature_batch(bytes(outbuf[:written]), n)
