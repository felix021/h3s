package gosim

// DSP arithmetic and multiply models (semantics.r).

import "strings"

func rHsel(c *CPU, op *Operands, reg, which string) uint32 {
	return half(c.R[op.Num(reg)], op.Str(which) == ".h")
}

func rMkHh(sign int64, imm bool) modelFunc {
	return func(c *CPU, op *Operands, st *State) (*Effect, error) {
		a := int64(rHsel(c, op, "s", "m"))
		var b int64
		if imm {
			b = op.Num("p")
		} else {
			b = int64(rHsel(c, op, "t", "p"))
		}
		v := uint32(a+sign*b) & 0xFFFF
		return ef().reg(int(op.Num("r")), sethalf(c.R[op.Num("r")], op.Str("n") == ".h", int64(v))).flNone(), nil
	}
}

func rMkHhe(sign int64) modelFunc {
	return func(c *CPU, op *Operands, st *State) (*Effect, error) {
		a := int64(rHsel(c, op, "s", "m"))
		b := int64(rHsel(c, op, "t", "p"))
		v := lane(a, b, sign, op.Str("q"), 16)
		return ef().reg(int(op.Num("r")), sethalf(c.R[op.Num("r")], op.Str("n") == ".h", int64(v))).flNone(), nil
	}
}

func rMkRrre(sign int64) modelFunc {
	return func(c *CPU, op *Operands, st *State) (*Effect, error) {
		a, b := int64(c.R[op.Num("s")]), int64(c.R[op.Num("t")])
		signed := op.Str("n") == "(ssat)"
		if signed {
			a, b = s32(a), s32(b)
		}
		t := a + sign*b
		if signed {
			t = ssat(t, 32)
		} else {
			t = usat(t, 32)
		}
		return ef().reg(int(op.Num("r")), uint32(t)).flNone(), nil
	}
}

func rMkHshift(kind string, imm bool) modelFunc {
	return func(c *CPU, op *Operands, st *State) (*Effect, error) {
		a := rHsel(c, op, "s", "m")
		var n int64
		if imm {
			n = op.Num("p")
		} else {
			n = int64(c.R[op.Num("t")]) & 0xFF
		}
		v := shiftLane(kind, a, n, 16)
		return ef().reg(int(op.Num("r")), sethalf(c.R[op.Num("r")], op.Str("n") == ".h", int64(v))).flNone(), nil
	}
}

func rMkVshift(kind string, imm bool) modelFunc {
	return func(c *CPU, op *Operands, st *State) (*Effect, error) {
		var n int64
		if imm {
			n = op.Num("n")
		} else {
			n = int64(c.R[op.Num("t")]) & 0xFF
		}
		a := c.R[op.Num("s")]
		v := shiftLane(kind, a&0xFFFF, n, 16) | shiftLane(kind, a>>16, n, 16)<<16
		return ef().reg(int(op.Num("r")), v).flNone(), nil
	}
}

func rMkPshr(signed bool, imm bool) modelFunc {
	return func(c *CPU, op *Operands, st *State) (*Effect, error) {
		p := uint64(c.R[op.Num("s")]) | uint64(c.R[op.Num("s")+1])<<32
		var n int64
		if imm {
			n = op.Num("n")
		} else {
			n = int64(c.R[op.Num("t")]) & 0xFF
		}
		var t uint32
		if signed {
			t = uint32(ssat(s64(p)>>min64(n, 63), 32))
		} else {
			if n < 64 {
				v := p >> uint(n)
				if v > 0xFFFFFFFF {
					t = 0xFFFFFFFF
				} else {
					t = uint32(v)
				}
			} else {
				t = 0
			}
		}
		var cc uint32
		if n != 0 {
			sh := min64(n, 64) - 1
			cc = uint32(p >> uint(sh) & 1)
		}
		return ef().reg(int(op.Num("r")), t).fl((c.flags&1)|(t>>31)<<3|boolU32(t==0)<<2|cc<<1), nil
	}
}

func min64(a, b int64) int64 {
	if a < b {
		return a
	}
	return b
}

func boolU32(b bool) uint32 {
	if b {
		return 1
	}
	return 0
}

func rBytes(v uint32) []uint32 {
	return []uint32{v & 0xFF, v >> 8 & 0xFF, v >> 16 & 0xFF, v >> 24 & 0xFF}
}

func rLanes16(lo, hi uint32) []uint32 {
	return []uint32{lo & 0xFFFF, lo >> 16, hi & 0xFFFF, hi >> 16}
}

func rPack16(ls []uint32, e *Effect, dst int) {
	ls[0] &= 0xFFFF
	ls[1] &= 0xFFFF
	ls[2] &= 0xFFFF
	ls[3] &= 0xFFFF
	e.reg(dst, ls[0]|ls[1]<<16)
	e.reg(dst+1, ls[2]|ls[3]<<16)
}

func rAdd4Ppr(c *CPU, op *Operands, st *State) (*Effect, error) {
	acc := rLanes16(c.R[op.Num("s")], c.R[op.Num("s")+1])
	bs := rBytes(c.R[op.Num("t")])
	for i := range acc {
		acc[i] += bs[i]
	}
	e := ef().fl(c.flags)
	rPack16(acc, e, int(op.Num("r")))
	return e, nil
}

func rAdd4Prr(c *CPU, op *Operands, st *State) (*Effect, error) {
	sign := int64(1)
	if !strings.HasPrefix(op.Str("n"), "+") {
		sign = -1
	}
	as := rBytes(c.R[op.Num("s")])
	bs := rBytes(c.R[op.Num("t")])
	ls := make([]uint32, 4)
	for i := range as {
		ls[i] = uint32(int64(as[i]) + sign*int64(bs[i]))
	}
	e := ef().fl(c.flags)
	rPack16(ls, e, int(op.Num("r")))
	return e, nil
}

func rAdd4Avg(c *CPU, op *Operands, st *State) (*Effect, error) {
	as := rBytes(c.R[op.Num("s")])
	bs := rBytes(c.R[op.Num("t")])
	var v uint32
	for i := range as {
		v |= ((as[i] + bs[i] + 1) >> 1) << (8 * i)
	}
	return ef().reg(int(op.Num("r")), v).fl(c.flags), nil
}

func rModeOf(suffix string) string {
	parts := strings.Split(suffix, ".")
	if len(parts) > 0 && parts[len(parts)-1] == "x2" {
		return "(" + parts[len(parts)-2] + ",x2)"
	}
	if len(parts) > 0 && parts[0] == "rnd" {
		return "(rnd," + parts[1] + ")"
	}
	return "(" + parts[0] + ")"
}

func rMkTwo(signs [2]int64, mode string) modelFunc {
	return func(c *CPU, op *Operands, st *State) (*Effect, error) {
		s := c.R[op.Num("s")]
		t := c.R[op.Num("t")]
		hi := lane(int64(half(s, op.Str("n") == ".h")), int64(half(t, op.Str("p") == ".h")), signs[0], mode, 16)
		lo := lane(int64(half(s, op.Str("m") == ".h")), int64(half(t, op.Str("q") == ".h")), signs[1], mode, 16)
		return ef().reg(int(op.Num("r")), uint32(hi)<<16|uint32(lo)).fl(c.flags), nil
	}
}

func rAddsubImmH(c *CPU, op *Operands, st *State) (*Effect, error) {
	sign := int64(1)
	if op.Str("o") != "+" {
		sign = -1
	}
	v := lane(int64(half(c.R[op.Num("s")], op.Str("m") == ".h")), op.Num("p"), sign, op.Str("q"), 16)
	return ef().reg(int(op.Num("r")), sethalf(c.R[op.Num("r")], op.Str("n") == ".h", int64(v))).fl(c.flags), nil
}

func rSigns(text string) []int64 {
	parts := strings.Split(text, "|")
	out := make([]int64, len(parts))
	for i, c := range parts {
		if c == "+" {
			out[i] = 1
		} else {
			out[i] = -1
		}
	}
	return out
}

func rAddsubImm2(c *CPU, op *Operands, st *State) (*Effect, error) {
	ss := rSigns(op.Str("p"))
	hi := lane(int64(half(c.R[op.Num("s")], op.Str("n") == ".h")), op.Num("q"), ss[0], op.Str("i"), 16)
	lo := lane(int64(half(c.R[op.Num("s")], op.Str("m") == ".h")), op.Num("q"), ss[1], op.Str("i"), 16)
	return ef().reg(int(op.Num("r")), uint32(hi)<<16|uint32(lo)).fl(c.flags), nil
}

func rAddsubPqq(c *CPU, op *Operands, st *State) (*Effect, error) {
	ss := rSigns(op.Str("n"))
	s := op.Pr("s")
	m := op.Pr("m")
	hi := lane(int64(c.R[s[0]]), int64(c.R[m[0]]), ss[0], op.Str("p"), 32)
	lo := lane(int64(c.R[s[1]]), int64(c.R[m[1]]), ss[1], op.Str("p"), 32)
	return ef().reg(int(op.Num("r"))+1, uint32(hi)).reg(int(op.Num("r")), uint32(lo)).fl(c.flags), nil
}

func rAddsubPpp(c *CPU, op *Operands, st *State) (*Effect, error) {
	sg := rSigns(op.Str("n"))
	// reversed: printed from the top lane down.
	for i, j := 0, len(sg)-1; i < j; i, j = i+1, j-1 {
		sg[i], sg[j] = sg[j], sg[i]
	}
	a := rLanes16(c.R[op.Num("s")], c.R[op.Num("s")+1])
	b := rLanes16(c.R[op.Num("t")], c.R[op.Num("t")+1])
	ls := make([]uint32, 4)
	for i := range a {
		ls[i] = lane(int64(a[i]), int64(b[i]), sg[i], op.Str("m"), 16)
	}
	e := ef().fl(c.flags)
	rPack16(ls, e, int(op.Num("r")))
	return e, nil
}

func rMkMulp(signed bool, acc int) modelFunc {
	return func(c *CPU, op *Operands, st *State) (*Effect, error) {
		a := int64(c.R[op.Num("s")])
		b := int64(c.R[op.Num("t")])
		if signed {
			a, b = s32(a), s32(b)
		}
		p := uint64(a) * uint64(b)
		flags := c.flags
		if acc != 0 {
			old := uint64(c.R[op.Num("r")]) | uint64(c.R[op.Num("r")+1])<<32
			if acc > 0 {
				full := old + p
				carry := uint32(0)
				if full < old {
					carry = 1
				}
				p = full
				flags = flags&^2 | carry<<1
			} else {
				full := old - p
				carry := uint32(0)
				if old >= p {
					carry = 1
				}
				p = full
				flags = flags&^2 | carry<<1
			}
		}
		return ef().reg(int(op.Num("r")), uint32(p&0xFFFFFFFF)).reg(int(op.Num("r"))+1, uint32(p>>32)).fl(flags), nil
	}
}

func rSat(v int64, signed bool, bits int) uint32 {
	if signed {
		return uint32(ssat(v, bits)) & ((1 << bits) - 1)
	}
	return uint32(usat(v, bits)) & ((1 << bits) - 1)
}

func rModeParts(mode string) (signed, x2 bool) {
	parts := strings.Split(trimParens(mode), ",")
	for _, p := range parts {
		if p == "ssat" {
			signed = true
		}
		if p == "x2" {
			x2 = true
		}
	}
	return
}

func rMulsat(a, b int64, mode string) (int64, bool) {
	signed, x2 := rModeParts(mode)
	if signed {
		a, b = sext(a, 16), sext(b, 16)
	}
	mul := int64(1)
	if x2 {
		mul = 2
	}
	return a * b * mul, signed
}

func rMulH(c *CPU, op *Operands, st *State) (*Effect, error) {
	p, sg := rMulsat(int64(half(c.R[op.Num("s")], op.Str("m") == ".h")),
		int64(half(c.R[op.Num("t")], op.Str("p") == ".h")), op.Str("q"))
	return ef().reg(int(op.Num("r")), sethalf(c.R[op.Num("r")], op.Str("n") == ".h", int64(rSat(p, sg, 16)))).fl(c.flags), nil
}

func rMulW(c *CPU, op *Operands, st *State) (*Effect, error) {
	p, sg := rMulsat(int64(half(c.R[op.Num("s")], op.Str("n") == ".h")),
		int64(half(c.R[op.Num("t")], op.Str("m") == ".h")), op.Str("p"))
	return ef().reg(int(op.Num("r")), rSat(p, sg, 32)).fl(c.flags), nil
}

func rMkMaddH(sign int64) modelFunc {
	return func(c *CPU, op *Operands, st *State) (*Effect, error) {
		p, sg := rMulsat(int64(half(c.R[op.Num("s")], op.Str("m") == ".h")),
			int64(half(c.R[op.Num("t")], op.Str("p") == ".h")), op.Str("q"))
		a := int64(half(c.R[op.Num("r")], op.Str("n") == ".h"))
		return ef().reg(int(op.Num("r")), sethalf(c.R[op.Num("r")], op.Str("n") == ".h",
			int64(rSat(a+sign*p, sg, 16)))).fl(c.flags), nil
	}
}

func rMkMaddW(sign int64) modelFunc {
	return func(c *CPU, op *Operands, st *State) (*Effect, error) {
		p, sg := rMulsat(int64(half(c.R[op.Num("s")], op.Str("n") == ".h")),
			int64(half(c.R[op.Num("t")], op.Str("m") == ".h")), op.Str("p"))
		a := int64(c.R[op.Num("r")])
		if sg {
			a = s32(a)
		}
		return ef().reg(int(op.Num("r")), rSat(a+sign*p, sg, 32)).fl(c.flags), nil
	}
}

func rMkMulimm(acc int64, signed bool) modelFunc {
	return func(c *CPU, op *Operands, st *State) (*Effect, error) {
		a := int64(half(c.R[op.Num("s")], op.Str("n") == ".h"))
		if signed {
			a = sext(a, 16)
		}
		p := a * op.Num("m")
		if acc != 0 {
			base := int64(c.R[op.Num("r")])
			if signed {
				base = s32(base)
			}
			p = base + acc*p
		}
		return ef().reg(int(op.Num("r")), rSat(p, signed, 32)).fl(c.flags), nil
	}
}

func rProducts(c *CPU, op *Operands, mode string, imm bool) ([2]int64, bool) {
	sg, x2 := rModeParts(mode)
	selS := [2]string{"n", "m"}
	selT := [2]string{"p", "q"}
	var out [2]int64
	for i := range out {
		a := int64(half(c.R[op.Num("s")], op.Str(selS[i]) == ".h"))
		if sg {
			a = sext(a, 16)
		}
		var b int64
		if imm {
			b = op.Num("p")
		} else {
			b = int64(half(c.R[op.Num("t")], op.Str(selT[i]) == ".h"))
			if sg {
				b = sext(b, 16)
			}
		}
		mul := int64(1)
		if x2 {
			mul = 2
		}
		out[i] = a * b * mul
	}
	return out, sg
}

func rMkMul2(pair bool, imm bool, acc int64, mode string) modelFunc {
	return func(c *CPU, op *Operands, st *State) (*Effect, error) {
		m := mode
		if m == "" {
			m = op.Str("i")
		}
		prods, sg := rProducts(c, op, m, imm)
		hi, lo := prods[0], prods[1]
		d := int(op.Num("r"))
		e := ef().fl(c.flags)
		if pair {
			for _, reg := range [][2]int64{{int64(d) + 1, hi}, {int64(d), lo}} {
				base := int64(c.R[reg[0]])
				if sg {
					base = s32(base)
				}
				if acc != 0 {
					e.reg(int(reg[0]), rSat(base+acc*reg[1], sg, 32))
				} else {
					e.reg(int(reg[0]), rSat(reg[1], sg, 32))
				}
			}
		} else {
			parts := [][2]int64{{16, hi}, {0, lo}}
			var full uint32
			for i, pr := range parts {
				laneAcc := int64(c.R[d] >> pr[0] & 0xFFFF)
				var v uint32
				if acc != 0 {
					v = rSat(laneAcc+acc*pr[1], sg, 16)
				} else {
					v = rSat(pr[1], sg, 16)
				}
				full |= v << pr[0]
				_ = i
			}
			e.reg(d, full)
		}
		return e, nil
	}
}

func rMul4U(c *CPU, op *Operands, st *State) (*Effect, error) {
	as := rBytes(c.R[op.Num("s")])
	bs := rBytes(c.R[op.Num("t")])
	ls := make([]uint32, 4)
	for i := range as {
		ls[i] = as[i] * bs[i]
	}
	e := ef().fl(c.flags)
	rPack16(ls, e, int(op.Num("r")))
	return e, nil
}

func rSb(v uint32) int64 {
	if v&0x80 != 0 {
		return int64(v) - 256
	}
	return int64(v)
}

func rMul4M(c *CPU, op *Operands, st *State) (*Effect, error) {
	as := rBytes(c.R[op.Num("s")])
	bs := rBytes(c.R[op.Num("t")])
	ls := make([]uint32, 4)
	for i := range as {
		ls[i] = uint32(int64(as[i]) * rSb(bs[i]))
	}
	e := ef().fl(c.flags)
	rPack16(ls, e, int(op.Num("r")))
	return e, nil
}

func rMul4Mi(c *CPU, op *Operands, st *State) (*Effect, error) {
	as := rBytes(c.R[op.Num("s")])
	ls := make([]uint32, 4)
	for i := range as {
		ls[i] = uint32(int64(as[i]) * op.Num("n"))
	}
	e := ef().fl(c.flags)
	rPack16(ls, e, int(op.Num("r")))
	return e, nil
}

func rMkMadd4(signedT bool) modelFunc {
	return func(c *CPU, op *Operands, st *State) (*Effect, error) {
		acc := rLanes16(c.R[op.Num("r")], c.R[op.Num("r")+1])
		as := rBytes(c.R[op.Num("s")])
		bs := rBytes(c.R[op.Num("t")])
		ls := make([]uint32, 4)
		for i := range acc {
			var b int64
			if signedT {
				b = rSb(bs[i])
			} else {
				b = int64(bs[i])
			}
			ls[i] = uint32(int64(acc[i]) + int64(as[i])*b)
		}
		e := ef().fl(c.flags)
		rPack16(ls, e, int(op.Num("r")))
		return e, nil
	}
}
