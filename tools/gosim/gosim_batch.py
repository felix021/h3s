"""Stage-1 segment-batch byte protocol + reference-side serialization.

The Go core (tools/gosim/segment.go) runs a pre-qualified stretch of
single-core sequential-ALU instructions in one cgo call and returns a single
byte buffer. This module is the Python mirror of that layout, used by
test_batch_consistency.py to (a) serialize the Python step-by-step reference
observations into the same bytes and (b) parse the Go buffer back. Byte-for-byte
parity is asserted on the raw bytes, so the two serializers must agree exactly.

Layout (all little-endian):
  header    u32 executed, u32 stop_code, u32 stop_pc
  per step  u32 pc, u32 next_pc, u8 flags, u8 n_mem, u16 reserved,
            16 x u32 registers, then n_mem x (u32 addr, u32 value)

Stop codes mirror segment.go.
"""
import struct

# Stop codes (must match segment.go).
BATCH_OK = 0
BATCH_UNSAFE = 1
BATCH_NO_INSTR = 2
BATCH_NO_MODEL = 3
BATCH_OTHER = 4
BATCH_FETCH = 5

_HDR = struct.Struct("<III")
_STEP = struct.Struct("<IIBBH16I")  # pc, next_pc, flags, n_mem, reserved, r[0..15]
_MEM = struct.Struct("<II")

HDR_SIZE = _HDR.size
STEP_FIXED = _STEP.size
MEM_SIZE = _MEM.size

# Stage-1 output capacity: header + n steps of the fixed record (no mem writes).
def outcap(n):
    return HDR_SIZE + n * STEP_FIXED


def encode_header(executed, stop_code, stop_pc):
    return _HDR.pack(executed & 0xFFFFFFFF, stop_code, stop_pc & 0xFFFFFFFF)


def encode_step(pc, next_pc, flags, R, mem_writes=()):
    """Serialize one step observation. R is the 16-register list/sequence after
    the step; mem_writes is an iterable of (addr, value) whole-word stores."""
    body = _STEP.pack(pc & 0xFFFFFFFF, next_pc & 0xFFFFFFFF,
                      flags & 0xF, len(mem_writes), 0,
                      *(v & 0xFFFFFFFF for v in R))
    for addr, value in mem_writes:
        body += _MEM.pack(addr & 0xFFFFFFFF, value & 0xFFFFFFFF)
    return body


def parse_buffer(buf):
    """Parse a Go result buffer into ((executed, stop_code, stop_pc), steps).
    Each step is (pc, next_pc, flags, R_tuple, mem_writes_list)."""
    executed, stop_code, stop_pc = _HDR.unpack_from(buf, 0)
    steps = []
    pos = HDR_SIZE
    for _ in range(executed):
        pc, next_pc, flags, n_mem, _reserved, *R = _STEP.unpack_from(buf, pos)
        pos += STEP_FIXED
        mem = []
        for _ in range(n_mem):
            addr, value = _MEM.unpack_from(buf, pos)
            pos += MEM_SIZE
            mem.append((addr, value))
        steps.append((pc, next_pc, flags, tuple(R), mem))
    return (executed, stop_code, stop_pc), steps
