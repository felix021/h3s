package gosim

// if/iff/ifs block-openers and rep/rep.i (semantics.v3/v4/v5).

func blockModel(condition string, opts compareOpts) modelFunc {
	pred := compareModel(condition, opts)
	return func(c *CPU, op *Operands, st *State) (*Effect, error) {
		res, err := pred(c, op, st)
		if err != nil {
			return nil, err
		}
		taken := res.exit != 0
		e := ef().fl(c.flags)
		if taken {
			for k := int64(0); k < op.Num("t"); k++ {
				e.reg(8+int(k), 0x100+uint32(k))
			}
		} else {
			for k := int64(0); k < op.Num("e"); k++ {
				e.reg(12+int(k), 0x200+uint32(k))
			}
		}
		return e, nil
	}
}

// v4 and_block: taken = !(r & s), for if.unknown.rr.{1..7}.
func andBlockModel() modelFunc {
	return func(c *CPU, op *Operands, st *State) (*Effect, error) {
		yes := c.R[op.Num("r")]&c.R[op.Num("s")] == 0
		e := ef().fl(c.flags)
		if yes {
			for k := int64(0); k < op.Num("t"); k++ {
				e.reg(8+int(k), 0x100+uint32(k))
			}
		} else {
			for k := int64(0); k < op.Num("e"); k++ {
				e.reg(12+int(k), 0x200+uint32(k))
			}
		}
		return e, nil
	}
}

func repModel(regCount bool) modelFunc {
	return func(c *CPU, op *Operands, st *State) (*Effect, error) {
		var count, body int64
		if regCount {
			count = int64(c.R[op.Num("r")])
			body = op.Num("n")
		} else {
			count = op.Num("n")
			body = op.Num("b")
		}
		n := count * (body / 2)
		e := ef().reg(7, (c.R[7] + uint32(n)) & 0xFFFFFFFF)
		if regCount {
			e.reg(int(op.Num("r")), 0)
		}
		if n == 0 {
			e.fl(c.flags)
		} else {
			e.fl(0)
		}
		return e, nil
	}
}
