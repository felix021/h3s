package gosim

// Memory DSP multiply and cache models (semantics.d2).

func makeMultiply(kind string, widths [2]int, addressing string, signed bool, accumulate int) modelFunc {
	return func(c *CPU, op *Operands, st *State) (*Effect, error) {
		a := int64(st.load(c.R[op.Num("s")], widths[0]/8))
		writes := map[int]uint32{}
		var b int64
		if kind == "mem1" {
			var inc uint32
			if addressing == "i" {
				b = int64(c.R[op.Num("t")])
				inc = uint32(op.Num("n"))
			} else {
				b = int64(c.R[op.Num("u")])
				inc = c.R[op.Num("t")]
			}
			writes[int(op.Num("s"))] = c.R[op.Num("s")] + inc
		} else {
			var second int64
			var incA, incB uint32
			if addressing == "i" {
				second = op.Num("t")
				incA = uint32(op.Num("n"))
				incB = uint32(op.Num("m"))
			} else {
				second = op.Num("u")
				incA = uint32(sext(int64(c.R[op.Num("t")]>>16), 16))
				incB = uint32(sext(int64(c.R[op.Num("t")]), 16))
			}
			b = int64(st.load(c.R[second], widths[1]/8))
			writes[int(second)] = c.R[second] + incB
			writes[int(op.Num("s"))] = c.R[op.Num("s")] + incA
		}
		if signed {
			a = sext(a, widths[0])
			b = sext(b, widths[1])
		}
		// 64-bit product (wraps mod 2^64, matching (a*b) & M64 for both
		// signed and unsigned inputs).
		product := uint64(a) * uint64(b)
		d := int(op.Num("r"))
		result := product
		flags := c.flags
		if accumulate != 0 {
			old := uint64(c.R[d]) | uint64(c.R[d+1])<<32
			if accumulate > 0 {
				result = old + product
				carry := uint32(0)
				if result < old { // wrapped: old + product overflowed 64 bits
					carry = 1
				}
				flags = flags&^2 | carry<<1
			} else {
				result = old - product
				carry := uint32(0)
				if old >= product {
					carry = 1
				}
				flags = flags&^2 | carry<<1
			}
		}
		result &= 0xFFFFFFFFFFFFFFFF
		writes[d] = uint32(result & 0xFFFFFFFF)
		writes[d+1] = uint32(result >> 32)
		e := &Effect{regs: writes}
		e.fl(flags)
		return e, nil
	}
}

func modelCache(c *CPU, op *Operands, st *State) (*Effect, error) {
	return ef().fl(c.flags), nil
}
