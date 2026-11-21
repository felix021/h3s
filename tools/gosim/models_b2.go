package gosim

// Branch/call models (semantics.b2).

import "strings"

type compareOpts struct {
	signedCmp bool
	masked    bool
	floating  bool
}

func relations(cond string, a, b int64) bool {
	switch cond {
	case "eq":
		return a == b
	case "ne":
		return a != b
	case "ge":
		return a >= b
	case "lt":
		return a < b
	case "gt":
		return a > b
	case "le":
		return a <= b
	}
	panic("bad relation " + cond)
}

func compareModel(condition string, o compareOpts) modelFunc {
	return func(c *CPU, op *Operands, st *State) (*Effect, error) {
		a := int64(c.R[op.Num("r")])
		var b int64
		if op.HasNum("s") {
			b = int64(c.R[op.Num("s")])
		} else if op.HasNum("i") {
			b = op.Num("i")
		} else {
			b = op.Num("n")
		}
		b &= 0xFFFFFFFF
		unordered := false
		if o.floating {
			if (uint32(a)&0x7FFFFFFF) > 0x7F800000 || (uint32(b)&0x7FFFFFFF) > 0x7F800000 {
				unordered = true
			}
			if a>>31 != 0 {
				a = -int64(uint32(a) & 0x7FFFFFFF)
			} else {
				a = int64(uint32(a) & 0x7FFFFFFF)
			}
			if b>>31 != 0 {
				b = -int64(uint32(b) & 0x7FFFFFFF)
			} else {
				b = int64(uint32(b) & 0x7FFFFFFF)
			}
		} else if o.masked {
			a, b = a&b, 0
		} else if o.signedCmp {
			a, b = s32(a), s32(b)
		}
		cond := strings.TrimPrefix(condition, "u")
		var taken bool
		if unordered {
			taken = strings.HasPrefix(condition, "u")
		} else {
			taken = relations(cond, a, b)
		}
		t := 0
		if taken {
			t = 1
		}
		return ef().exitv(t).fl(c.flags), nil
	}
}

func flagBranchModel(condition string) modelFunc {
	return func(c *CPU, op *Operands, st *State) (*Effect, error) {
		f := c.flags
		n := f&8 != 0
		z := f&4 != 0
		cc := f&2 != 0
		v := f&1 != 0
		var taken bool
		switch condition {
		case "eq":
			taken = z
		case "ne":
			taken = !z
		case "cs":
			taken = cc
		case "cc":
			taken = !cc
		case "hi":
			taken = cc && !z
		case "ls":
			taken = !cc || z
		case "ge":
			taken = n == v
		case "lt":
			taken = n != v
		case "gt":
			taken = !z && n == v
		case "le":
			taken = z || n != v
		}
		t := 0
		if taken {
			t = 1
		}
		return ef().exitv(t).fl(f), nil
	}
}

func modelCall(c *CPU, op *Operands, st *State) (*Effect, error) {
	return ef().exitv(1).retsv(st.end).fl(c.flags), nil
}

func modelJump(c *CPU, op *Operands, st *State) (*Effect, error) {
	return ef().exitv(1).fl(c.flags), nil
}

func modelBnez(c *CPU, op *Operands, st *State) (*Effect, error) {
	t := 0
	if c.R[op.Num("r")] != 0 {
		t = 1
	}
	return ef().exitv(t).fl(c.flags), nil
}

func modelDbnz(c *CPU, op *Operands, st *State) (*Effect, error) {
	value := (c.R[op.Num("r")] - 1) & 0xFFFFFFFF
	t := 0
	if value != 0 {
		t = 1
	}
	return ef().reg(int(op.Num("r")), value).exitv(t).fl(c.flags), nil
}

func modelIfs2nsC(c *CPU, op *Operands, st *State) (*Effect, error) {
	t := 0
	if c.R[0]&1 == 0 {
		t = 1
	}
	return ef().exitv(t).fl(c.flags), nil
}

func modelIfs2nsS(c *CPU, op *Operands, st *State) (*Effect, error) {
	t := int(c.R[0] & 1)
	return ef().exitv(t).fl(c.flags), nil
}
