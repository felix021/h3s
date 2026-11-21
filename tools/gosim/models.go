package gosim

// Flag/arithmetic helpers (mirror semantics._p_util and semantics._r_util),
// plus the basic, basic2 and p.py instruction models.

import (
	"math/big"
	"math/bits"
)

const (
	N_, Z_, C_, V_ uint32 = 8, 4, 2, 1
)

// s32/s16/s64 sign-extend.
func s32(x int64) int64  { return int64(int32(x)) }
func s16(x int64) int64  { return int64(int16(x)) }
func s64(x uint64) int64 { return int64(x) }

func nz(res int64) uint32 {
	res &= 0xFFFFFFFF
	var f uint32
	if res>>31 != 0 {
		f |= N_
	}
	if res == 0 {
		f |= Z_
	}
	return f
}

func arithFlags(r int64, c, v int) uint32 {
	f := nz(r)
	if c != 0 {
		f |= C_
	}
	if v != 0 {
		f |= V_
	}
	return f
}

func addFull(a, b, c int64) (r uint32, carry uint32, overflow int) {
	s := (a & 0xFFFFFFFF) + (b & 0xFFFFFFFF) + c
	r = uint32(s & 0xFFFFFFFF)
	carry = uint32(s >> 32)
	if s32(a)+s32(b)+c != s32(int64(r)) {
		overflow = 1
	}
	return
}

func subFull(a, b, c int64) (r uint32, carry uint32, overflow int) {
	r, carry, _ = addFull(a, ^b&0xFFFFFFFF, c)
	if s32(a)-s32(b)-(1-c) != s32(int64(r)) {
		overflow = 1
	}
	return
}

func rev32(x uint32) uint32 { return bits.Reverse32(x) }

func shift32(kind string, x uint32, c int, cin uint32) (uint32, uint32) {
	if c == 0 {
		return x, cin
	}
	if c > 32 {
		c = 32
	}
	switch kind {
	case "lsl":
		return (x << c) & 0xFFFFFFFF, x >> (32 - c) & 1
	case "lsr":
		return x >> c, x >> (c - 1) & 1
	case "asr":
		return uint32(s32(int64(x)) >> c) & 0xFFFFFFFF, x >> (c - 1) & 1
	case "shl3":
		v := s32(int64(x)) << c
		if v > 0x7FFFFFFF {
			v = 0x7FFFFFFF
		} else if v < -0x80000000 {
			v = -0x80000000
		}
		return uint32(v) & 0xFFFFFFFF, x >> (32 - c) & 1
	}
	panic("bad shift kind " + kind)
}

func shift64(kind string, x uint64, c int, cin uint64) (uint64, uint64) {
	if kind == "ror" {
		c %= 64
		if c == 0 {
			return x, cin
		}
		return (x>>c | x<<(64-c)) & 0xFFFFFFFFFFFFFFFF, cin
	}
	if c == 0 {
		return x, cin
	}
	if c > 64 {
		c = 64
	}
	switch kind {
	case "rorc":
		return ror65(x, cin, c)
	case "lsl":
		return (x << c) & 0xFFFFFFFFFFFFFFFF, x >> (64 - c) & 1
	case "lsr":
		return x >> c, x >> (c - 1) & 1
	case "asr":
		return uint64(s64(x) >> c), x >> (c - 1) & 1
	case "shl3":
		return shl3sat64(x, c), x >> (64 - c) & 1
	}
	panic("bad shift kind " + kind)
}

// shl3sat64 saturating signed shift-left of a 64-bit value by c (0..64).
func shl3sat64(x uint64, c int) uint64 {
	v := s64(x)
	if c >= 64 {
		if v == 0 {
			return 0
		}
		if v < 0 {
			return 0x8000000000000000
		}
		return 0x7FFFFFFFFFFFFFFF
	}
	if v == 0 {
		return 0
	}
	// saturate without overflowing: threshold = 2^(63-c) (positive) / -2^(63-c).
	if v > 0 {
		if v >= int64(1)<<(63-c) {
			return 0x7FFFFFFFFFFFFFFF
		}
		return uint64(v << c)
	}
	if v <= -(int64(1) << (63 - c)) {
		return 0x8000000000000000
	}
	return uint64(v << c)
}

// ror65 rotates the 65-bit value (cin as bit 64, x as low 64) right by c
// (1..64): returns (low 64, carry out).
func ror65(x uint64, cin uint64, c int) (uint64, uint64) {
	k := 65 - c // rotate left by k in 65 bits (k in 1..64)
	var outX uint64
	for j := 0; j < 64; j++ {
		m := j - k
		if m < 0 {
			m += 65
		}
		var b uint64
		if m == 64 {
			b = cin
		} else {
			b = x >> m & 1
		}
		outX |= b << j
	}
	m := 64 - k
	if m < 0 {
		m += 65
	}
	var outCin uint64
	if m == 64 {
		outCin = cin
	} else {
		outCin = x >> m & 1
	}
	return outX, outCin
}

// ---- semantics._r_util -----------------------------------------------------

func half(v uint32, high bool) uint32 {
	if high {
		return v >> 16
	}
	return v & 0xFFFF
}

func sethalf(old uint32, high bool, h int64) uint32 {
	h &= 0xFFFF
	if high {
		return (old & 0x0000FFFF) | uint32(h)<<16
	}
	return (old & 0xFFFF0000) | uint32(h)
}

func usat(v int64, bits int) int64 {
	hi := (int64(1) << bits) - 1
	if v < 0 {
		return 0
	}
	if v > hi {
		return hi
	}
	return v
}

func ssat(v int64, bits int) int64 {
	lo := -(int64(1) << (bits - 1))
	hi := (int64(1) << (bits - 1)) - 1
	if v < lo {
		return lo
	}
	if v > hi {
		return hi
	}
	return v
}

func sext(v int64, bits int) int64 {
	v &= (int64(1) << bits) - 1
	if v>>(bits-1) != 0 {
		return v - (int64(1) << bits)
	}
	return v
}

func lane(a, b int64, sign int64, mode string, bits int) uint32 {
	parts := splitComma(trimParens(mode))
	rnd := false
	kind := ""
	x2 := false
	for _, p := range parts {
		switch p {
		case "rnd":
			rnd = true
		case "x2":
			x2 = true
		default:
			kind = p
		}
	}
	signed := kind == "ssat" || kind == "savg"
	if signed {
		a, b = sext(a, bits), sext(b, bits)
	}
	t := a + sign*b
	if kind == "usat" || kind == "ssat" {
		if x2 {
			t *= 2
			if sign < 0 && !signed && t >= int64(1)<<bits {
				t = 0
			}
		}
		if signed {
			t = ssat(t, bits)
		} else {
			t = usat(t, bits)
		}
	} else {
		if rnd {
			t++
		}
		t >>= 1
	}
	return uint32(t & ((int64(1) << bits) - 1))
}

func trimParens(s string) string {
	s = trimPrefix(s, "(")
	s = trimSuffix(s, ")")
	return s
}

func splitComma(s string) []string {
	var out []string
	start := 0
	for i := 0; i <= len(s); i++ {
		if i == len(s) || s[i] == ',' {
			out = append(out, s[start:i])
			start = i + 1
		}
	}
	return out
}

func shiftLane(kind string, v uint32, n int64, bits int) uint32 {
	m := uint32((1 << bits) - 1)
	switch kind {
	case "lsl":
		if n >= int64(bits) {
			return 0
		}
		return (v << uint(n)) & m
	case "lsr":
		if n >= int64(bits) {
			return 0
		}
		return v >> uint(n)
	case "asr":
		nn := n
		if nn > int64(bits) {
			nn = int64(bits)
		}
		return uint32(sext(int64(v), bits)>>nn) & m
	case "shl3":
		nn := n
		if nn >= int64(bits)-1 {
			nn = int64(bits) - 1
		}
		if nn < 0 {
			nn = 0
		}
		return uint32(ssat(sext(int64(v), bits)<<nn, bits)) & m
	}
	panic("bad lane kind " + kind)
}

// ---- basic.py --------------------------------------------------------------

func modelNop(c *CPU, op *Operands, st *State) (*Effect, error) {
	return ef().fl(c.flags), nil
}

func modelMovRR(c *CPU, op *Operands, st *State) (*Effect, error) {
	return ef().reg(int(op.Num("x")), c.R[op.Num("y")]).fl(c.flags), nil
}

func modelLiU8(c *CPU, op *Operands, st *State) (*Effect, error) {
	return ef().reg(int(op.Num("r")), uint32(op.Num("n"))).fl(c.flags), nil
}

func modelLslRRI(c *CPU, op *Operands, st *State) (*Effect, error) {
	e := ef()
	if op.Num("n") < 32 {
		e.reg(int(op.Num("d")), c.R[op.Num("s")]<<uint(op.Num("n")))
	} else {
		e.reg(int(op.Num("d")), 0)
	}
	return e.flNone(), nil
}

// add.rrr / sub.rrr (16-bit forms run as their 32-bit forms).
func modelAddRRR(c *CPU, op *Operands, st *State) (*Effect, error) {
	return addModel("d", "a", "b")(c, op, st)
}

func modelSubRRR(c *CPU, op *Operands, st *State) (*Effect, error) {
	return subModel("d", "a", "b")(c, op, st)
}

// ---- basic2.py -------------------------------------------------------------

func modelLdW(c *CPU, op *Operands, st *State) (*Effect, error) {
	return ef().reg(int(op.Num("d")), st.load(c.R[op.Num("b")]+uint32(op.Num("n")), 4)).fl(c.flags), nil
}

func modelStW(c *CPU, op *Operands, st *State) (*Effect, error) {
	return ef().memw(c.R[op.Num("b")]+uint32(op.Num("n")), c.R[op.Num("d")]).fl(c.flags), nil
}

func modelBeqz(c *CPU, op *Operands, st *State) (*Effect, error) {
	t := 0
	if c.R[op.Num("r")] == 0 {
		t = 1
	}
	return ef().exitv(t).fl(c.flags), nil
}

func modelRts(c *CPU, op *Operands, st *State) (*Effect, error) {
	return ef().exitv(1).fl(c.flags), nil
}

// ---- p.py ------------------------------------------------------------------

func val(c *CPU, op *Operands, k string) int64 {
	if k == "n" || k == "m" {
		return op.Num(k)
	}
	return int64(c.R[op.Num(k)])
}

func addModel(dst, a, b string) modelFunc {
	return func(c *CPU, op *Operands, st *State) (*Effect, error) {
		r, cy, ov := addFull(val(c, op, a), val(c, op, b), 0)
		return ef().reg(int(op.Num(dst)), r).fl(arithFlags(int64(r), int(cy), ov)), nil
	}
}

func subModel(dst, a, b string) modelFunc {
	return func(c *CPU, op *Operands, st *State) (*Effect, error) {
		r, cy, ov := subFull(val(c, op, a), val(c, op, b), 1)
		return ef().reg(int(op.Num(dst)), r).fl(arithFlags(int64(r), int(cy), ov)), nil
	}
}

func modelAddc(c *CPU, op *Operands, st *State) (*Effect, error) {
	r, cy, ov := addFull(int64(c.R[op.Num("s")]), int64(c.R[op.Num("t")]), int64(c.flags>>1&1))
	return ef().reg(int(op.Num("r")), r).fl(arithFlags(int64(r), int(cy), ov)), nil
}

func modelSubc(c *CPU, op *Operands, st *State) (*Effect, error) {
	r, cy, ov := subFull(int64(c.R[op.Num("s")]), int64(c.R[op.Num("t")]), int64(c.flags>>1&1))
	return ef().reg(int(op.Num("r")), r).fl(arithFlags(int64(r), int(cy), ov)), nil
}

func modelAddBrev(c *CPU, op *Operands, st *State) (*Effect, error) {
	s, cy, ov := addFull(int64(rev32(c.R[op.Num("r")])), int64(rev32(c.R[op.Num("s")])), 0)
	r := rev32(s)
	return ef().reg(int(op.Num("r")), r).fl(arithFlags(int64(r), int(cy), ov)), nil
}

// _nz: N and Z from the 32-bit result, C and V kept.
func nzModel(dst string, fn func(c *CPU, op *Operands) uint32) modelFunc {
	return func(c *CPU, op *Operands, st *State) (*Effect, error) {
		r := fn(c, op) & 0xFFFFFFFF
		return ef().reg(int(op.Num(dst)), r).fl((c.flags & (C_ | V_)) | nz(int64(r))), nil
	}
}

func modelClz(c *CPU, op *Operands, st *State) (*Effect, error) {
	x := c.R[op.Num("s")]
	r := uint32(32 - bits.Len32(x))
	return ef().reg(int(op.Num("r")), r).fl((c.flags & (C_ | V_)) | nz(int64(r))), nil
}

func modelCls(c *CPU, op *Operands, st *State) (*Effect, error) {
	x := c.R[op.Num("s")]
	var y uint32
	if x>>31 != 0 {
		y = x ^ 0xFFFFFFFF
	} else {
		y = x
	}
	r := uint32(32 - bits.Len32(y))
	return ef().reg(int(op.Num("r")), r).fl((c.flags & (C_ | V_)) | nz(int64(r))), nil
}

func halfIns(old uint32, newv int64, high bool) uint32 {
	if high {
		return (old & 0x0000FFFF) | uint32(newv)<<16
	}
	return (old & 0xFFFF0000) | uint32(newv)
}

func modelLiByte(c *CPU, op *Operands, st *State) (*Effect, error) {
	sh := 8 * op.Raw("n")
	mask := uint32(0xFF) << sh
	return ef().reg(int(op.Num("r")), (c.R[op.Num("r")] & ^mask) | (uint32(op.Num("m"))<<sh)).fl(c.flags), nil
}

func modelLiHalf(c *CPU, op *Operands, st *State) (*Effect, error) {
	high := op.Raw("n") != 0
	return ef().reg(int(op.Num("r")), halfIns(c.R[op.Num("r")], op.Num("m"), high)).fl(c.flags), nil
}

func modelMovHalf(c *CPU, op *Operands, st *State) (*Effect, error) {
	src := int64(half(c.R[op.Num("s")], op.Raw("m") != 0))
	high := op.Raw("n") != 0
	return ef().reg(int(op.Num("r")), halfIns(c.R[op.Num("r")], src, high)).fl(c.flags), nil
}

func fieldw(p, l int64) int {
	w := int(l)
	if w > 32-int(p) {
		w = 32 - int(p)
	}
	if w < 0 {
		w = 0
	}
	return w
}

func modelUextra(c *CPU, op *Operands, st *State) (*Effect, error) {
	w := fieldw(op.Num("n"), op.Num("m"))
	r := (c.R[op.Num("s")] >> uint(op.Num("n"))) & ((uint32(1) << w) - 1)
	return ef().reg(int(op.Num("r")), r).fl((c.flags & (C_ | V_)) | nz(int64(r))), nil
}

func modelSextra(c *CPU, op *Operands, st *State) (*Effect, error) {
	l := op.Num("m")
	r := int64((c.R[op.Num("s")] >> uint(op.Num("n"))) & ((uint32(1)<<uint(l))-1))
	if l != 0 {
		r = (r ^ (int64(1) << (l - 1))) - (int64(1) << (l - 1))
	}
	r &= 0xFFFFFFFF
	return ef().reg(int(op.Num("r")), uint32(r)).fl((c.flags & (C_ | V_)) | nz(r)), nil
}

func modelInsert(c *CPU, op *Operands, st *State) (*Effect, error) {
	w := fieldw(op.Num("n"), op.Num("m"))
	mask := ((uint32(1) << w) - 1) << uint(op.Num("n"))
	r := (c.R[op.Num("r")] & ^mask) | ((c.R[op.Num("s")] << uint(op.Num("n"))) & mask)
	return ef().reg(int(op.Num("r")), r).fl((c.flags & (C_ | V_)) | nz(int64(r))), nil
}

func shiftModel(kind, dst, src, cnt string) modelFunc {
	return func(c *CPU, op *Operands, st *State) (*Effect, error) {
		var cc int64
		if cnt == "n" {
			cc = op.Num("n")
		} else {
			cc = int64(c.R[op.Num(cnt)]) & 0xFF
		}
		x := c.R[op.Num(src)]
		r, cy := shift32(kind, x, int(cc), c.flags>>1&1)
		return ef().reg(int(op.Num(dst)), r).fl(nz(int64(r)) | (cy << 1) | (c.flags & V_)), nil
	}
}

func ror32(x uint32, c int64) uint32 {
	c &= 31
	if c == 0 {
		return x
	}
	return (x >> uint(c)) | (x << uint(32-c))
}

func modelRorcC(src, cnt, dst string) modelFunc {
	return func(c *CPU, op *Operands, st *State) (*Effect, error) {
		var cc int64
		if cnt == "n" {
			cc = op.Num("n")
		} else {
			cc = int64(c.R[op.Num(cnt)]) & 0xFF
			if cc > 32 {
				cc = 32
			}
		}
		r, cy := rorc32(c.R[op.Num(src)], int(cc), c.flags>>1&1)
		return ef().reg(int(op.Num(dst)), r).fl(nz(int64(r)) | (cy << 1) | (c.flags & V_)), nil
	}
}

func rorc32(x uint32, c int, cin uint32) (uint32, uint32) {
	// 33-bit rotate right: value (cin, x), c in 0..32.
	if c == 0 {
		return x, cin
	}
	k := (33 - c) % 33 // rotate left by k in 33 bits
	var out uint32
	for j := 0; j < 32; j++ {
		m := j - k
		if m < 0 {
			m += 33
		}
		var b uint32
		if m == 32 {
			b = cin
		} else {
			b = x >> m & 1
		}
		out |= b << j
	}
	m := 32 - k
	if m < 0 {
		m += 33
	}
	var outCin uint32
	if m == 32 {
		outCin = cin
	} else {
		outCin = x >> m & 1
	}
	return out, outCin
}

func modelXchgRRR(c *CPU, op *Operands, st *State) (*Effect, error) {
	x := c.R[op.Num("s")]
	cnt := int64(c.R[op.Num("t")]) & 63
	if cnt >= 32 {
		cnt -= 64
	}
	cy := c.flags >> 1 & 1
	var r uint32
	if cnt > 0 {
		r = (x << uint(cnt)) & 0xFFFFFFFF
		cy = x >> (32 - uint(cnt)) & 1
	} else if cnt < 0 {
		r = x >> uint(-cnt)
		cy = x >> (uint(-cnt) - 1) & 1
	} else {
		r = x
	}
	return ef().reg(int(op.Num("r")), r).fl(nz(int64(r)) | (cy << 1) | (c.flags & V_)), nil
}

func pairShiftModel(kind string, imm bool) modelFunc {
	return func(c *CPU, op *Operands, st *State) (*Effect, error) {
		p := int(op.Num("r"))
		var cc int64
		if imm {
			cc = op.Num("n")
		} else {
			cc = int64(c.R[op.Num("s")]) & 0xFF
		}
		x := uint64(c.R[p]) | uint64(c.R[p+1])<<32
		r, cy := shift64(kind, x, int(cc), uint64(c.flags>>1&1))
		e := ef().reg(p, uint32(r)).reg(p+1, uint32(r>>32))
		return e.fl((c.flags &^ C_) & 15 | (uint32(cy) << 1)), nil
	}
}

func modelCcSet(c *CPU, op *Operands, st *State) (*Effect, error) {
	e := ef()
	if op.Num("v") != 0 {
		e.fl((c.flags &^ C_) & 15 | C_)
	} else {
		e.fl((c.flags &^ C_) & 15)
	}
	return e, nil
}

func shiftrModel(imm bool) modelFunc {
	return func(c *CPU, op *Operands, st *State) (*Effect, error) {
		s := int(op.Num("s"))
		x := new(big.Int).SetUint64(uint64(c.R[s]) | uint64(c.R[s+1])<<32)
		var cc int64
		var modeRaw uint32
		var arithRaw uint32
		if imm {
			cc = op.Num("m")
			modeRaw = op.Raw("p")
			arithRaw = op.Raw("n")
		} else {
			cc = int64(c.R[op.Num("t")]) & 0xFF
			modeRaw = op.Raw("m")
			arithRaw = op.Raw("n")
		}
		if cc > 64 {
			cc = 64
		}
		arith := arithRaw != 0
		xv := new(big.Int)
		if arith {
			// sign-extend the 64-bit value to signed.
			if x.Bit(63) != 0 {
				xv.Sub(x, two64)
			} else {
				xv.Set(x)
			}
		} else {
			xv.Set(x)
		}
		// q = floor(xv / 2^c): arithmetic right shift for negative big.Int.
		q := new(big.Int).Rsh(xv, uint(cc))
		// r = xv - q*2^c; rem2 = 2*r.
		shifted := new(big.Int).Lsh(new(big.Int).Set(q), uint(cc))
		r := new(big.Int).Sub(xv, shifted)
		rem2 := new(big.Int).Lsh(r, 1)
		pow2c := new(big.Int).Lsh(bigOne, uint(cc))
		tie := cc > 0 && rem2.Cmp(pow2c) == 0
		roundUp := false
		if cc > 0 && rem2.Cmp(pow2c) > 0 {
			roundUp = true
		} else if tie {
			var adj int64
			switch modeRaw {
			case 0: // up
				adj = 1
			case 1: // even
				adj = int64(q.Bit(0))
			case 2: // round
				if xv.Sign() >= 0 {
					adj = 1
				}
			case 3: // zero
				if xv.Sign() < 0 {
					adj = 1
				}
			}
			roundUp = adj != 0
		}
		if roundUp {
			q.Add(q, bigOne)
		}
		// saturate to 32 bits.
		var res int64
		if arith {
			res = clampBigToInt32(q)
		} else {
			if q.Sign() < 0 {
				res = 0
			} else if q.Cmp(u32Max) > 0 {
				res = 0xFFFFFFFF
			} else {
				res = q.Int64()
			}
		}
		res &= 0xFFFFFFFF
		var cy uint32
		if cc != 0 {
			cy = uint32(x.Rsh(x, uint(cc-1)).Bit(0))
		}
		return ef().reg(int(op.Num("r")), uint32(res)).fl(nz(res) | (cy << 1) | (c.flags & V_)), nil
	}
}

var (
	bigOne  = big.NewInt(1)
	two64   = new(big.Int).Lsh(bigOne, 64)
	u32Max  = new(big.Int).SetUint64(0xFFFFFFFF)
)

func clampBigToInt32(q *big.Int) int64 {
	if q.Cmp(bigMin32) < 0 {
		return -0x80000000
	}
	if q.Cmp(bigMax32) > 0 {
		return 0x7FFFFFFF
	}
	return q.Int64()
}

var (
	bigMin32 = big.NewInt(-0x80000000)
	bigMax32 = big.NewInt(0x7FFFFFFF)
)

func modelLsrVB(c *CPU, op *Operands, st *State) (*Effect, error) {
	x := c.R[op.Num("s")]
	n := op.Num("n")
	var r uint32
	for i := 0; i < 4; i++ {
		r |= ((x >> (8 * i) & 0xFF) >> uint(n)) << (8 * i)
	}
	return ef().reg(int(op.Num("r")), r).flNone(), nil
}

func modelAsrVH(c *CPU, op *Operands, st *State) (*Effect, error) {
	n := op.Num("n")
	e := ef()
	for k := 0; k < 2; k++ {
		x := c.R[op.Num("s")+int64(k)]
		var r uint32
		for i := 0; i < 2; i++ {
			h := s32(int64((x >> (16 * i) & 0xFFFF)) << 16) >> 16
			if n == 0 {
				r |= uint32(h&0xFFFF) << (16 * i)
			} else {
				r |= uint32(((h+(int64(1)<<uint(n-1)))>>uint(n))&0xFFFF) << (16 * i)
			}
		}
		e.reg(int(op.Num("r"))+k, r)
	}
	return e.flNone(), nil
}

func modelPopcntRR(c *CPU, op *Operands, st *State) (*Effect, error) {
	return ef().fl(c.flags), nil
}

func modelPopcntRPair(c *CPU, op *Operands, st *State) (*Effect, error) {
	r := halfIns(c.R[op.Num("r")], int64(c.R[op.Num("s")]&0xFFFF), false)
	return ef().reg(int(op.Num("r")), r).fl(c.flags), nil
}

func trimPrefix(s, p string) string {
	if len(s) >= len(p) && s[:len(p)] == p {
		return s[len(p):]
	}
	return s
}

func trimSuffix(s, p string) string {
	if len(s) >= len(p) && s[len(s)-len(p):] == p {
		return s[:len(s)-len(p)]
	}
	return s
}
