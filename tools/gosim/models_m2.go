package gosim

// Memory-form models (semantics.m2): memop (ld/st), rmw, st.w.modimm,
// testset.b, and the context-only ld.w.npc/r.

import "strings"

type writes struct {
	st    *State
	words map[uint32]uint32
}

func newWrites(st *State) *writes {
	return &writes{st: st, words: map[uint32]uint32{}}
}

func (w *writes) put(addr uint32, size int, value uint32) {
	for k := 0; k < size; k++ {
		a := (addr + uint32(k)) & 0xFFFFFFFF
		wa := a & ^uint32(3)
		sh := (a & 3) * 8
		word, ok := w.words[wa]
		if !ok {
			word = w.st.load(wa, 4)
		}
		w.words[wa] = (word & ^(uint32(0xFF)<<sh)) | ((value>>(8*k)&0xFF)<<sh)
	}
}

func (w *writes) result() map[uint32]uint32 {
	out := make(map[uint32]uint32)
	for a, v := range w.words {
		if v != w.st.load(a, 4) {
			out[a] = v
		}
	}
	return out
}

func extend32(v uint32, size int, signed bool) uint32 {
	v &= uint32(1<<(8*size)) - 1
	if signed && v>>(8*size-1) != 0 {
		v -= uint32(1 << (8 * size))
	}
	return v & 0xFFFFFFFF
}

var sixteenSet = map[string]bool{
	"ld.bu": true, "ld.hu": true, "ld.w": true,
	"st.b": true, "st.h": true, "st.w": true,
	"ld.bu.pi": true, "ld.hu.pi": true, "ld.w.pi": true,
	"st.b.pi": true, "st.h.pi": true, "st.w.pi": true,
	"ld.bu.pin": true, "ld.hu.pin": true, "ld.w.pin": true,
	"st.b.pin": true, "st.h.pin": true, "st.w.pin": true,
}

func specMemop(id string) (load bool, size int, signed bool, hi bool, mode string) {
	p := strings.Split(id, ".")
	sz := map[byte]int{'b': 1, 'h': 2, 'w': 4, 'd': 8}[p[1][0]]
	load = p[0] == "ld"
	signed = p[1] == "bs" || p[1] == "hs"
	for _, x := range p {
		if x == "hi" {
			hi = true
		}
	}
	var rest []string
	for _, x := range p[2:] {
		if x != "hi" && x != "l" {
			rest = append(rest, x)
		}
	}
	mode = "off"
	if len(rest) > 0 {
		mode = rest[0]
	}
	return load, sz, signed, hi, mode
}

func makeMemop(id string) modelFunc {
	load, size, signed, hi, mode := specMemop(id)
	shift := map[int]int{1: 0, 2: 1, 4: 2, 8: 3}[size]
	in16 := sixteenSet[id]
	return func(c *CPU, op *Operands, st *State) (*Effect, error) {
		var d, b, x int64
		if in16 {
			d = op.Num("d")
			b = op.Num("b")
			if op.HasNum("i") {
				x = op.Num("i")
			}
		} else if mode == "sp" {
			d = op.Num("r")
		} else if mode == "idx" || mode == "idxs" || mode == "pi" || mode == "prer" {
			if load {
				d, b, x = op.Num("r"), op.Num("s"), op.Num("t")
			} else {
				d, b, x = op.Num("t"), op.Num("r"), op.Num("s")
			}
		} else {
			if load {
				d, b = op.Num("r"), op.Num("s")
			} else {
				d, b = op.Num("s"), op.Num("r")
			}
		}
		var base uint32
		if mode == "sp" {
			base = st.sp
		} else {
			base = c.R[b]
		}
		n := int64(0)
		if op.HasNum("n") {
			n = op.Num("n")
		}
		var addr int64
		var newBase *uint32
		switch mode {
		case "off", "sp":
			addr = int64(base) + n
		case "idx":
			addr = int64(base) + int64(c.R[x])
		case "idxs":
			addr = int64(base) + (int64(c.R[x]) << shift)
		case "pi":
			addr = int64(base)
			nb := base + c.R[x]
			newBase = &nb
		case "pin":
			addr = int64(base)
			nb := base + uint32(n)
			newBase = &nb
		default: // pre, prer
			inc := n
			if mode == "prer" {
				inc = int64(c.R[x])
			}
			addr = int64(base) + inc
			nb := uint32(addr)
			newBase = &nb
		}
		addr &= 0xFFFFFFFF
		e := ef().fl(c.flags)
		if newBase != nil {
			e.reg(int(b), *newBase)
		}
		if load {
			if size == 8 {
				e.reg(int(d), st.load(uint32(addr), 4))
				e.reg(int(d)+1, st.load(uint32(addr)+4, 4))
			} else {
				e.reg(int(d), extend32(st.load(uint32(addr), size), size, signed))
			}
			return e, nil
		}
		w := newWrites(st)
		if size == 8 {
			w.put(uint32(addr), 4, c.R[d])
			v := c.R[d+1]
			if uv, ok := e.regs[int(d)+1]; ok {
				v = uv
			}
			w.put(uint32(addr)+4, 4, v)
		} else {
			v := c.R[d]
			if hi {
				v >>= 16
			}
			w.put(uint32(addr), size, v)
		}
		e.mem = w.result()
		return e, nil
	}
}

func makeRmw(id string) modelFunc {
	parts := strings.Split(id, ".")
	opname := parts[1]
	kind := parts[2]
	return func(c *CPU, op *Operands, st *State) (*Effect, error) {
		addr := (c.R[op.Num("r")] + uint32(op.Num("n"))) & 0xFFFFFFFF
		old := int64(st.load(addr, 4))
		var b int64
		switch kind {
		case "modimm", "nmodimm", "s12", "i":
			b = op.Num("m")
		case "invr":
			b = ^int64(c.R[op.Num("s")])
		case "bitr", "invbitr":
			k := c.R[op.Num("s")] & 31
			if kind == "bitr" {
				b = int64(uint32(1) << k)
			} else {
				b = ^(int64(uint32(1)) << k)
			}
		default:
			b = int64(c.R[op.Num("s")])
		}
		var newv int64
		switch opname {
		case "add":
			newv = old + b
		case "sub":
			newv = old - b
		case "and":
			newv = old & b
		case "or":
			newv = old | b
		case "xor":
			newv = old ^ b
		default:
			newv = rmwShift(opname, old, b&0xFF)
		}
		w := newWrites(st)
		w.put(addr, 4, uint32(newv&0xFFFFFFFF))
		return (&Effect{mem: w.result()}).fl(c.flags), nil
	}
}

func rmwShift(kind string, x, c int64) int64 {
	if c > 32 {
		c = 32
	}
	switch kind {
	case "lsl":
		return int64((uint64(uint32(x)) << uint(c)) & 0xFFFFFFFF)
	case "lsr":
		return int64(uint64(uint32(x)) >> uint(c))
	case "asr":
		return int64(uint64(s32(x)>>uint(c)) & 0xFFFFFFFF)
	default: // shl3
		return shl3sat32(x, int(c)) & 0xFFFFFFFF
	}
}

func shl3sat32(x int64, c int) int64 {
	v := s32(x)
	if c >= 32 {
		if v == 0 {
			return 0
		}
		if v < 0 {
			return -0x80000000
		}
		return 0x7FFFFFFF
	}
	if v == 0 {
		return 0
	}
	if v > 0 {
		if v >= int64(1)<<(31-c) {
			return 0x7FFFFFFF
		}
		return v << uint(c)
	}
	if v <= -(int64(1) << (31 - c)) {
		return -0x80000000
	}
	return v << uint(c)
}

func modelStWModimm(c *CPU, op *Operands, st *State) (*Effect, error) {
	w := newWrites(st)
	w.put(c.R[op.Num("r")]+uint32(op.Num("n")), 4, uint32(op.Num("m")))
	return (&Effect{mem: w.result()}).fl(c.flags), nil
}

func modelTestsetB(c *CPU, op *Operands, st *State) (*Effect, error) {
	addr := c.R[op.Num("x")]
	w := newWrites(st)
	w.put(addr, 1, 0xFF)
	return (&Effect{mem: w.result()}).fl(st.load(addr, 1) & 0xF), nil
}

// ld.w.npc / ld.w.npc.r need the hardware-run code index (not available in the
// reference step engine); they raise exactly as the upstream model does.
func modelNpc(c *CPU, op *Operands, st *State) (*Effect, error) {
	return nil, ErrNeedsContext
}
