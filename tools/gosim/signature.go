package gosim

import (
	"encoding/binary"
	"strconv"
	"strings"
)

// Register-signature formatting, byte-identical to the Python
// _register_signature in tools/analyze_image_trace.py (route DT / W3-2). The
// Python side encodes each of the 16 register values into a type-tagged
// descriptor, calls SignaturesBatch once per step, then sys.interns each
// returned string exactly as the reference after_step does. This package only
// reproduces the exact output strings; the intern/memo identity semantics stay
// on the Python side.
//
// Descriptor layout (all integers little-endian):
//   tag 0 int        : int64 value
//   tag 1 bool       : u8 (0=false, 1=true)
//   tag 2 UnknownWord: u8 known-bitmap (bit i = byte i is a known int) +
//                      u8 x4 byte values (0 where unknown) +
//                      u32 x4 origins (read only where the byte is unknown)
//   tag 3 IcfgValue  : u32 known + u32 value
//   tag 4 other      : (no payload) -> "?"
//
// The dispatch order (bool before int) mirrors _register_signature exactly:
// a Python bool is an int subclass, and the reference checks bool first, so
// True formats as "True" not "00000001".
const (
	SigTagInt   = 0
	SigTagBool  = 1
	SigTagWord  = 2
	SigTagIcfg  = 3
	SigTagOther = 4
)

// py08x reproduces CPython's f'{v:08x}' for the full int64 range: lowercase
// hex, sign preserved, zero-padded to a minimum total width of 8 (the sign
// counts toward the width), never truncated.
func py08x(v int64) string {
	neg := v < 0
	var u uint64
	if neg {
		u = uint64(-(v + 1)) + 1 // avoid overflow at MinInt64
	} else {
		u = uint64(v)
	}
	digits := strconv.FormatUint(u, 16)
	sign := ""
	if neg {
		sign = "-"
	}
	if pad := 8 - len(sign) - len(digits); pad > 0 {
		return sign + strings.Repeat("0", pad) + digits
	}
	return sign + digits
}

// py02x reproduces CPython's f'{b:02x}' for a byte (0..255).
func py02x(b uint32) string {
	s := strconv.FormatUint(uint64(b), 16)
	if len(s) < 2 {
		s = "0" + s
	}
	return s
}

// Signature formats a single encoded value descriptor. It returns the bytes
// after the descriptor and the exact Python-side signature string.
func Signature(desc []byte) (rest []byte, s string) {
	tag := desc[0]
	switch tag {
	case SigTagInt:
		v := int64(binary.LittleEndian.Uint64(desc[1:9]))
		return desc[9:], py08x(v)
	case SigTagBool:
		if desc[1] != 0 {
			return desc[2:], "True"
		}
		return desc[2:], "False"
	case SigTagWord:
		known := desc[1]
		var b strings.Builder
		b.Grow(64)
		b.WriteByte('u')
		for i := 0; i < 4; i++ {
			if known>>i&1 != 0 {
				b.WriteString(py02x(uint32(desc[2+i])))
			} else {
				b.WriteString("??")
			}
		}
		b.WriteByte('|')
		for i := 0; i < 4; i++ {
			if i > 0 {
				b.WriteByte(',')
			}
			if known>>i&1 != 0 {
				b.WriteByte('-')
			} else {
				o := binary.LittleEndian.Uint32(desc[6+4*i:])
				b.WriteString("0x")
				b.WriteString(strconv.FormatUint(uint64(o), 16))
			}
		}
		return desc[22:], b.String()
	case SigTagIcfg:
		known := binary.LittleEndian.Uint32(desc[1:5])
		value := binary.LittleEndian.Uint32(desc[5:9])
		return desc[9:], "i" + py08x(int64(known)) + "." + py08x(int64(value))
	default:
		return desc[1:], "?"
	}
}

// SignaturesBatch formats n value descriptors from in and writes each result
// as a u16-length-prefixed UTF-8 string into out. It returns the number of
// bytes written to out, or -1 if out is too small.
func SignaturesBatch(in []byte, n int, out []byte) int {
	w := 0
	rest := in
	for i := 0; i < n; i++ {
		var s string
		rest, s = Signature(rest)
		need := 2 + len(s)
		if w+need > len(out) {
			return -1
		}
		binary.LittleEndian.PutUint16(out[w:], uint16(len(s)))
		copy(out[w+2:], s)
		w += need
	}
	return w
}
