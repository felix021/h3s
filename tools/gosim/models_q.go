package gosim

// DSP models (semantics.q).

func qLanes(x uint32, bits int, total int) []uint32 {
	var out []uint32
	for p := 0; p < total; p += bits {
		out = append(out, (x>>p)&((1<<bits)-1))
	}
	return out
}

func qJoin(xs []uint32, bits int) uint64 {
	var y uint64
	for i, x := range xs {
		y |= uint64(x&((1<<bits)-1)) << (i * bits)
	}
	return y
}

func qClip(x int64, bits int, isSigned bool) int64 {
	if isSigned {
		lo := -(int64(1) << (bits - 1))
		hi := (int64(1) << (bits - 1)) - 1
		if x < lo {
			return lo
		}
		if x > hi {
			return hi
		}
		return x
	}
	hi := (int64(1) << bits) - 1
	if x < 0 {
		return 0
	}
	if x > hi {
		return hi
	}
	return x
}

func qNz(f uint32, x uint32, bits int) uint32 {
	x &= (1 << bits) - 1
	r := f & 3
	if x == 0 {
		r |= 4
	}
	if x>>(bits-1) != 0 {
		r |= 8
	}
	return r
}

func qHalf(r uint32, high bool) uint32 {
	if high {
		return r >> 16
	}
	return r & 0xFFFF
}

func qWriteHalf(r uint32, high bool, x uint32) uint32 {
	shift := 0
	if high {
		shift = 16
	}
	return (r & ^(uint32(0xFFFF) << shift)) | ((x & 0xFFFF) << shift)
}

func qUnary(kind, shape string) modelFunc {
	return func(c *CPU, op *Operands, st *State) (*Effect, error) {
		bits := 32
		if shape != "rr" {
			bits = 16
		}
		var x uint32
		if shape == "rhrh" {
			x = qHalf(c.R[op.Num("s")], op.Str("m") == ".h")
		} else {
			x = c.R[op.Num("s")]
		}
		xs := qLanes(x, bits, 32)
		ys := make([]uint32, len(xs))
		for i, v := range xs {
			sv := sext(int64(v), bits)
			if kind == "abs" {
				if sv < 0 {
					sv = -sv
				}
			} else {
				sv = -sv
			}
			ys[i] = uint32(qClip(sv, bits, true))
		}
		y := qJoin(ys, bits)
		e := ef().fl(c.flags)
		if shape == "rhrh" {
			e.reg(int(op.Num("r")), qWriteHalf(c.R[op.Num("r")], op.Str("n") == ".h", uint32(y)))
		} else {
			e.reg(int(op.Num("r")), uint32(y))
		}
		return e, nil
	}
}

func qMinmax(kind, shape string) modelFunc {
	return func(c *CPU, op *Operands, st *State) (*Effect, error) {
		bits := 32
		if shape != "rrr" {
			bits = 16
		}
		var a, b uint32
		if shape == "rhrhrh" {
			a = qHalf(c.R[op.Num("s")], op.Str("m") == ".h")
			b = qHalf(c.R[op.Num("t")], op.Str("p") == ".h")
		} else {
			a = c.R[op.Num("s")]
			b = c.R[op.Num("t")]
		}
		isMax := strings2HasSuffix(kind, "max")
		isSigned := kind[0] == 's'
		as := qLanes(a, bits, 32)
		bs := qLanes(b, bits, 32)
		ys := make([]uint32, len(as))
		for i := range as {
			var x, z int64
			if isSigned {
				x, z = sext(int64(as[i]), bits), sext(int64(bs[i]), bits)
			} else {
				x, z = int64(as[i]), int64(bs[i])
			}
			var chosen int64
			if isMax {
				if x > z {
					chosen = x
				} else {
					chosen = z
				}
			} else {
				if x < z {
					chosen = x
				} else {
					chosen = z
				}
			}
			ys[i] = uint32(chosen)
		}
		y := qJoin(ys, bits)
		e := ef().fl(c.flags)
		if shape == "rhrhrh" {
			e.reg(int(op.Num("r")), qWriteHalf(c.R[op.Num("r")], op.Str("n") == ".h", uint32(y)))
		} else {
			e.reg(int(op.Num("r")), uint32(y))
		}
		return e, nil
	}
}

func qCompare(family, condition string, isPair bool) modelFunc {
	return func(c *CPU, op *Operands, st *State) (*Effect, error) {
		bits, total := 8, 32
		if isPair {
			bits, total = 16, 64
		}
		var a, b uint64
		if isPair {
			a = uint64(c.R[op.Num("s")]) | uint64(c.R[op.Num("s")+1])<<32
			b = uint64(c.R[op.Num("t")]) | uint64(c.R[op.Num("t")+1])<<32
		} else {
			a = uint64(c.R[op.Num("s")])
			b = uint64(c.R[op.Num("t")])
		}
		isSigned := strings2HasSuffix(family, "s")
		as := qLanes64(a, bits, total)
		bs := qLanes64(b, bits, total)
		matches := make([]bool, len(as))
		for i := range as {
			var x, z int64
			if isSigned {
				x, z = sext(int64(as[i]), bits), sext(int64(bs[i]), bits)
			} else {
				x, z = int64(as[i]), int64(bs[i])
			}
			matches[i] = relations(condition, x, z)
		}
		allM := true
		anyM := false
		for _, m := range matches {
			allM = allM && m
			anyM = anyM || m
		}
		var y uint64
		if strings2HasPrefix(family, "all") {
			if allM {
				y = (1 << total) - 1
			}
		} else if strings2HasPrefix(family, "any") {
			if anyM {
				y = (1 << total) - 1
			}
		} else {
			ys := make([]uint32, len(matches))
			for i, m := range matches {
				if m {
					ys[i] = (1 << bits) - 1
				}
			}
			y = qJoin(ys, bits)
		}
		e := ef().fl(c.flags)
		if isPair {
			e.reg(int(op.Num("r")), uint32(y&0xFFFFFFFF))
			e.reg(int(op.Num("r"))+1, uint32(y>>32))
		} else {
			e.reg(int(op.Num("r")), uint32(y))
		}
		return e, nil
	}
}

func qLanes64(x uint64, bits int, total int) []uint32 {
	var out []uint32
	for p := 0; p < total; p += bits {
		out = append(out, uint32((x>>p)&((1<<bits)-1)))
	}
	return out
}

func qSaturate(bits int, isSigned, isHalf bool) modelFunc {
	return func(c *CPU, op *Operands, st *State) (*Effect, error) {
		var x uint32
		if isHalf {
			x = qHalf(c.R[op.Num("s")], op.Str("m") == ".h")
		} else {
			x = c.R[op.Num("s")]
		}
		var xv int64
		if isSigned {
			if isHalf {
				xv = sext(int64(x), 16)
			} else {
				xv = sext(int64(x), 32)
			}
		} else {
			xv = int64(x)
		}
		y := uint32(qClip(xv, bits, isSigned))
		e := ef()
		if isHalf {
			e.reg(int(op.Num("r")), qWriteHalf(c.R[op.Num("r")], op.Str("n") == ".h", y))
		} else {
			e.reg(int(op.Num("r")), y)
		}
		if isHalf {
			e.fl(qNz(c.flags, y, 16))
		} else {
			e.fl(qNz(c.flags, y, 32))
		}
		return e, nil
	}
}

func qPackBytes(mode, source string) modelFunc {
	return func(c *CPU, op *Operands, st *State) (*Effect, error) {
		x := uint64(c.R[op.Num(source)]) | uint64(c.R[op.Num(source)+1])<<32
		xs := qLanes64(x, 16, 64)
		actual := mode
		if mode == "enum" {
			actual = op.Str("n")
		}
		ys := make([]uint32, len(xs))
		for i, v := range xs {
			switch actual {
			case "(usat)":
				ys[i] = uint32(qClip(int64(v), 8, false))
			case "(ssat)":
				ys[i] = uint32(qClip(sext(int64(v), 16), 8, true))
			case "stou":
				ys[i] = uint32(qClip(sext(int64(v), 16), 8, false))
			default:
				ys[i] = v
			}
		}
		return ef().reg(int(op.Num("r")), uint32(qJoin(ys, 8))).fl(c.flags), nil
	}
}

func qUnpack(isSigned bool, dest string) modelFunc {
	return func(c *CPU, op *Operands, st *State) (*Effect, error) {
		xs := qLanes(c.R[op.Num("s")], 8, 32)
		ys := make([]uint32, len(xs))
		for i, v := range xs {
			if isSigned {
				ys[i] = uint32(sext(int64(v), 8))
			} else {
				ys[i] = v
			}
		}
		y := qJoin(ys, 16)
		return ef().reg(int(op.Num(dest)), uint32(y)).reg(int(op.Num(dest))+1, uint32(y>>32)).fl(c.flags), nil
	}
}

func qMts(bits int) modelFunc {
	return func(c *CPU, op *Operands, st *State) (*Effect, error) {
		as := qLanes(c.R[op.Num("s")], bits, 32)
		bs := qLanes(c.R[op.Num("t")], bits, 32)
		xs := make([]uint32, 0, len(as)*2)
		for i := range as {
			xs = append(xs, bs[i], as[i])
		}
		y := qJoin(xs, bits)
		return ef().reg(int(op.Num("r")), uint32(y)).reg(int(op.Num("r"))+1, uint32(y>>32)).fl(c.flags), nil
	}
}

func qSad(c *CPU, op *Operands, st *State) (*Effect, error) {
	acc := qLanes64(uint64(c.R[op.Num("r")])|uint64(c.R[op.Num("r")+1])<<32, 16, 64)
	as := qLanes(c.R[op.Num("s")], 8, 32)
	bs := qLanes(c.R[op.Num("t")], 8, 32)
	xs := make([]uint32, len(acc))
	for i := range acc {
		d := int64(as[i]) - int64(bs[i])
		if d < 0 {
			d = -d
		}
		s := int64(acc[i]) + d
		if s > 65535 {
			s = 65535
		}
		xs[i] = uint32(s)
	}
	y := qJoin(xs, 16)
	return ef().reg(int(op.Num("r")), uint32(y)).reg(int(op.Num("r"))+1, uint32(y>>32)).fl(c.flags), nil
}

func qShift(shape string, saturating bool) modelFunc {
	return func(c *CPU, op *Operands, st *State) (*Effect, error) {
		bits := 32
		if shape == "pr" {
			bits = 64
		} else if shape == "rhrhr" || shape == "v" {
			bits = 16
		}
		var countReg int64
		if shape == "pr" {
			countReg = op.Num("s")
		} else {
			countReg = op.Num("t")
		}
		countBits := map[int]int{16: 5, 32: 6, 64: 7}[bits]
		n := sext(int64(c.R[countReg]), countBits)

		var carry uint32
		if n != 0 {
			var sh uint64
			if n > 0 {
				sh = uint64(bits) - uint64(n)
			} else {
				sh = uint64(-n) - 1
			}
			if shape == "pr" {
				x := uint64(c.R[op.Num("r")]) | uint64(c.R[op.Num("r")+1])<<32
				carry = uint32(x >> sh & 1)
			} else {
				var x32 uint32
				if shape == "rhrhr" {
					x32 = qHalf(c.R[op.Num("s")], op.Str("m") == ".h")
				} else {
					x32 = c.R[op.Num("s")]
				}
				carry = x32 >> sh & 1
			}
		} else {
			carry = c.flags >> 1 & 1
		}
		shiftedFlags := (c.flags &^ 2) | (carry << 1)

		e := ef()
		if shape == "pr" {
			// single 64-bit lane.
			x := uint64(c.R[op.Num("r")]) | uint64(c.R[op.Num("r")+1])<<32
			var y uint64
			if saturating {
				value := int64(x)
				if n >= 0 {
					value = satShiftL(value, uint(n))
				} else {
					value >>= uint(-n)
				}
				y = uint64(qClip(value, 64, true))
			} else {
				if n >= 0 {
					y = x << uint(n)
				} else {
					y = x >> uint(-n)
				}
			}
			e.reg(int(op.Num("r")), uint32(y&0xFFFFFFFF))
			e.reg(int(op.Num("r"))+1, uint32(y>>32))
			e.fl(shiftedFlags)
			return e, nil
		}
		// 16- or 32-bit lanes.
		var x32 uint32
		if shape == "rhrhr" {
			x32 = qHalf(c.R[op.Num("s")], op.Str("m") == ".h")
		} else {
			x32 = c.R[op.Num("s")]
		}
		total := bits
		if shape == "v" {
			total = 32
		}
		xs := qLanes(x32, bits, total)
		ys := make([]uint32, len(xs))
		for i, v := range xs {
			if saturating {
				value := sext(int64(v), bits)
				if n >= 0 {
					value = satShiftL(value, uint(n))
				} else {
					value >>= uint(-n)
				}
				ys[i] = uint32(qClip(value, bits, true))
			} else {
				var uv uint64 = uint64(v)
				if n >= 0 {
					uv = uv << uint(n)
				} else {
					uv >>= uint(-n)
				}
				ys[i] = uint32(uv)
			}
		}
		y := qJoin(ys, bits)
		if shape == "rhrhr" {
			e.reg(int(op.Num("r")), qWriteHalf(c.R[op.Num("r")], op.Str("n") == ".h", uint32(y)))
			e.fl(c.flags)
		} else {
			e.reg(int(op.Num("r")), uint32(y))
			if shape == "v" {
				e.fl(c.flags)
			} else {
				e.fl(qNz(shiftedFlags, uint32(y), 32))
			}
		}
		return e, nil
	}
}

// satShiftL: saturating left shift for a signed 64-bit value, overflow-safe.
func satShiftL(value int64, n uint) int64 {
	if value == 0 {
		return 0
	}
	if n >= 63 {
		if value < 0 {
			return -0x8000000000000000
		}
		return 0x7FFFFFFFFFFFFFFF
	}
	if n == 0 {
		return value
	}
	limit := int64(1) << (63 - n) // 63-n in [1, 62]: no overflow
	if value > 0 {
		if value >= limit {
			return 0x7FFFFFFFFFFFFFFF
		}
		return value << n
	}
	if value <= -limit {
		return -0x8000000000000000
	}
	return value << n
}

func modelAlign(rshift int) modelFunc {
	return func(c *CPU, op *Operands, st *State) (*Effect, error) {
		v := uint64(c.R[op.Num("s")])<<32 | uint64(c.R[op.Num("t")])
		return ef().reg(int(op.Num("r")), uint32(v>>rshift)).fl(c.flags), nil
	}
}

func modelPack(c *CPU, op *Operands, st *State) (*Effect, error) {
	v := qHalf(c.R[op.Num("s")], op.Str("n") == ".h")<<16 | qHalf(c.R[op.Num("t")], op.Str("m") == ".h")
	return ef().reg(int(op.Num("r")), v).fl(c.flags), nil
}

func strings2HasPrefix(s, p string) bool { return len(s) >= len(p) && s[:len(p)] == p }
func strings2HasSuffix(s, p string) bool { return len(s) >= len(p) && s[len(s)-len(p):] == p }
