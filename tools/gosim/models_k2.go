package gosim

// Stack, special-register and register-list models (semantics.k2), inner
// (architectural) semantics. The upstream _with_stub instrumentation (scratch
// PC store + rets normalization) is provided by withStub for raw pi32v2-isa
// parity; the H3S simulator strips it via architectural_k2.

var k2Const = map[string]uint32{
	"cnum": 0, "uspn": 0, "sspn": 0, "icfgn": 0, "icfgs": 0,
	"icfg": 0x1090700, "ssp": 0x1c00e00, "rete": 0, "retx": 0, "sr4": 0,
}

func srValue(name string, flags uint32, st *State) uint32 {
	switch name {
	case "psr":
		return flags & 0xF
	case "rets":
		return st.rets & RETS_MASK
	case "sp":
		return st.sp
	case "pc":
		return st.stub
	case "reti":
		return 0x1e0168e
	case "usp":
		return 0x1c01180
	default:
		return k2Const[name]
	}
}

func k2Rlist(k uint32) []int {
	if k < 4 {
		out := make([]int, 0, 4)
		for r := int(k); r < 4; r++ {
			out = append(out, r)
		}
		return out
	}
	out := make([]int, 0, 16)
	for r := 4; r <= int(k); r++ {
		out = append(out, r)
	}
	return out
}

func k2Bits(mask uint32) []int {
	out := make([]int, 0, 16)
	for i := 0; i < 16; i++ {
		if mask>>i&1 != 0 {
			out = append(out, i)
		}
	}
	return out
}

func k2Push(items func(c *CPU, op *Operands, st *State) []uint32) modelFunc {
	return func(c *CPU, op *Operands, st *State) (*Effect, error) {
		vals := items(c, op, st)
		sp := st.sp - uint32(4*len(vals))
		e := ef().fl(c.flags)
		for k, v := range vals {
			e.memw(sp+uint32(4*k), v)
		}
		return e.spv(sp), nil
	}
}

func k2Pop(regsFn func(op *Operands) []int, special string) modelFunc {
	return func(c *CPU, op *Operands, st *State) (*Effect, error) {
		rs := regsFn(op)
		e := ef().fl(c.flags)
		a := st.sp
		for _, r := range rs {
			e.reg(r, st.load(a, 4))
			a += 4
		}
		switch special {
		case "rets":
			e.retsv(st.load(a, 4) & RETS_MASK)
			a += 4
		case "pc":
			t := 0
			if st.load(a, 4) == st.taken {
				t = 1
			}
			e.exitv(t)
			a += 4
		}
		return e.spv(a), nil
	}
}

func k2PushSR(field string) modelFunc {
	return func(c *CPU, op *Operands, st *State) (*Effect, error) {
		mask := op.Raw(field)
		var vals []uint32
		for i := 0; i < 16; i++ {
			if mask>>i&1 != 0 {
				vals = append(vals, srValue(srNames[i], c.flags, st))
			}
		}
		sp := st.sp - uint32(4*len(vals))
		e := ef()
		for k, v := range vals {
			e.memw(sp+uint32(4*k), v)
		}
		e.spv(sp)
		if mask>>5&1 != 0 {
			e.fl(0)
		} else {
			e.fl(c.flags)
		}
		return e, nil
	}
}

func modelMovRSR(c *CPU, op *Operands, st *State) (*Effect, error) {
	v := srValue(srNames[op.Raw("s")], c.flags, st)
	return ef().reg(int(op.Num("r")), v).fl(c.flags), nil
}

func modelCsync(c *CPU, op *Operands, st *State) (*Effect, error) {
	return ef().fl(c.flags), nil
}

func modelAddSpI(c *CPU, op *Operands, st *State) (*Effect, error) {
	return ef().spv((st.sp + uint32(op.Num("n"))) & 0xFFFFFFFF).fl(c.flags), nil
}

func modelAddRSpI(c *CPU, op *Operands, st *State) (*Effect, error) {
	return ef().reg(int(op.Num("r")), (st.sp+uint32(op.Num("n")))&0xFFFFFFFF).fl(c.flags), nil
}

func ldstModel(store, update, pre bool) modelFunc {
	return func(c *CPU, op *Operands, st *State) (*Effect, error) {
		rs := k2Bits(op.Raw("n"))
		base := c.R[op.Num("r")]
		var start uint32
		if pre {
			start = (base - uint32(4*len(rs))) & 0xFFFFFFFF
		} else {
			start = base
		}
		e := ef().fl(c.flags)
		if store {
			for k, r := range rs {
				e.memw((start + uint32(4*k)) & 0xFFFFFFFF, c.R[r])
			}
		} else {
			for k, r := range rs {
				e.reg(r, st.load((start+uint32(4*k))&0xFFFFFFFF, 4))
			}
		}
		if update {
			inc := int64(4)
			if pre {
				inc = -4
			}
			newbase := (base + uint32(inc*int64(len(rs)))) & 0xFFFFFFFF
			inRs := false
			for _, r := range rs {
				if int64(r) == op.Num("r") {
					inRs = true
				}
			}
			if !inRs || store {
				e.reg(int(op.Num("r")), newbase)
			}
		}
		return e, nil
	}
}

func fieldModel(inner modelFunc) modelFunc {
	return func(c *CPU, op *Operands, st *State) (*Effect, error) {
		t := c.R[op.Num("t")]
		op2 := &Operands{
			raw: op.raw,
			num: make(map[string]int64, len(op.num)+2),
			str: op.str,
			pr:  op.pr,
		}
		for k, v := range op.num {
			op2.num[k] = v
		}
		op2.num["n"] = int64(t >> 8 & 31)
		op2.num["m"] = int64(t & 31)
		res, err := inner(c, op2, st)
		if err != nil {
			return nil, err
		}
		return &Effect{regs: res.regs, flags: res.flags, flagsKey: true, flagsSet: res.flagsSet}, nil
	}
}

// withStub mirrors semantics.k2._with_stub (the hardware-runner instrumentation).
func withStub(mf modelFunc) modelFunc {
	return func(c *CPU, op *Operands, st *State) (*Effect, error) {
		res, err := mf(c, op, st)
		if err != nil {
			return nil, err
		}
		if res.mem == nil {
			res.mem = map[uint32]uint32{}
		}
		key := (st.sp - 4) & 0xFFFFFFFF
		if _, ok := res.mem[key]; !ok {
			res.mem[key] = st.stub
		}
		if !res.hasRets {
			res.hasRets = true
			res.rets = st.rets & RETS_MASK
		}
		return res, nil
	}
}
