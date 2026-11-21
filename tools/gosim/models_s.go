package gosim

// Float/divide models (semantics.s): exact rational arithmetic via math/big.

import "math/big"

var bigOneInt = big.NewInt(1)

var bigMask64 = new(big.Int).Sub(new(big.Int).Lsh(bigOneInt, 64), bigOneInt)

func ratPow2(n int) *big.Rat {
	if n >= 0 {
		return new(big.Rat).SetInt(new(big.Int).Lsh(bigOneInt, uint(n)))
	}
	return new(big.Rat).SetFrac(bigOneInt, new(big.Int).Lsh(bigOneInt, uint(-n)))
}

func fpDecode(bits uint32, eb, fb int) *big.Rat {
	exponent := (bits >> fb) & ((1 << eb) - 1)
	significand := bits & ((1 << fb) - 1)
	if exponent != 0 {
		significand |= 1 << fb
	}
	shift := int(exponent)
	if exponent == 0 {
		shift = 1
	}
	shift = shift - ((1 << (eb - 1)) - 1) - fb
	var val *big.Rat
	if shift >= 0 {
		val = new(big.Rat).SetFrac(
			new(big.Int).Lsh(new(big.Int).SetUint64(uint64(significand)), uint(shift)),
			bigOneInt)
	} else {
		val = new(big.Rat).SetFrac(
			new(big.Int).SetUint64(uint64(significand)),
			new(big.Int).Lsh(bigOneInt, uint(-shift)))
	}
	if bits>>(eb+fb) != 0 {
		val.Neg(val)
	}
	return val
}

func fpKind(bits uint32, eb, fb int) string {
	if (bits>>fb)&((1<<eb)-1) != (1<<eb)-1 {
		return "finite"
	}
	if bits&((1<<fb)-1) != 0 {
		return "nan"
	}
	return "inf"
}

func fpEncode(value *big.Rat, signzero int, eb, fb int) uint32 {
	sign := 0
	if value.Sign() < 0 {
		sign = 1
	} else if value.Sign() == 0 {
		sign = signzero
	}
	abs := new(big.Rat).Abs(value)
	if abs.Sign() == 0 {
		return uint32(sign << (eb + fb))
	}
	bias := (1 << (eb - 1)) - 1
	num := abs.Num()
	den := abs.Denom()
	exponent := num.BitLen() - den.BitLen()
	if abs.Cmp(ratPow2(exponent)) < 0 {
		exponent--
	}
	shift := exponent
	if shift < 1-bias {
		shift = 1 - bias
	}
	shift -= fb
	scaled := new(big.Rat).Set(abs)
	if shift >= 0 {
		scaled.Quo(scaled, ratPow2(shift))
	} else {
		scaled.Mul(scaled, ratPow2(-shift))
	}
	n := new(big.Int)
	rem := new(big.Int)
	n.QuoRem(scaled.Num(), scaled.Denom(), rem)
	twiceRem := new(big.Int).Lsh(rem, 1)
	cmp := twiceRem.Cmp(scaled.Denom())
	if cmp > 0 || (cmp == 0 && n.Bit(0) == 1) {
		n.Add(n, bigOneInt)
	}
	if n.Cmp(new(big.Int).Lsh(bigOneInt, uint(fb+1))) >= 0 {
		n.Rsh(n, 1)
		exponent++
	}
	expbits := 0
	if n.Cmp(new(big.Int).Lsh(bigOneInt, uint(fb))) >= 0 {
		expbits = exponent + bias
		if expbits < 1 {
			expbits = 1
		}
	}
	if expbits >= (1<<eb)-1 {
		return uint32(sign<<(eb+fb)) | uint32(((1<<eb)-1)<<fb)
	}
	return uint32(sign<<(eb+fb)) | uint32(expbits<<fb) | uint32(n.Uint64()&((1<<fb)-1))
}

func fpCompare(a, b uint32) uint32 {
	if fpKind(a, 8, 23) == "nan" || fpKind(b, 8, 23) == "nan" {
		return 3
	}
	key := func(x uint32) int64 {
		if x>>31 != 0 {
			return -int64(x & 0x7FFFFFFF)
		}
		return int64(x & 0x7FFFFFFF)
	}
	ka, kb := key(a), key(b)
	if ka < kb {
		return 8
	}
	if ka > kb {
		return 2
	}
	return 6
}

func fpArithmetic(a, b uint32, operation string, accumulator *uint32) uint32 {
	inputs := []uint32{a, b}
	if accumulator != nil {
		inputs = append(inputs, *accumulator)
	}
	for _, x := range inputs {
		if fpKind(x, 8, 23) == "nan" {
			return 0x7F800001
		}
	}
	if accumulator != nil {
		product := fpArithmetic(a, b, "mul", nil)
		op := "add"
		if operation == "msub" {
			op = "sub"
		}
		return fpArithmetic(*accumulator, product, op, nil)
	}
	if operation == "sub" {
		b ^= 0x80000000
		operation = "add"
	}
	ka, kb := fpKind(a, 8, 23), fpKind(b, 8, 23)
	sign := (a ^ b) >> 31
	inf := 0x7F800000 | (sign << 31)
	switch operation {
	case "mul":
		if (ka == "inf" && b&0x7FFFFFFF == 0) || (kb == "inf" && a&0x7FFFFFFF == 0) {
			return 0x7F800001
		}
		if ka == "inf" || kb == "inf" {
			return inf
		}
		prod := new(big.Rat).Mul(fpDecode(a, 8, 23), fpDecode(b, 8, 23))
		return fpEncode(prod, int(sign), 8, 23)
	case "div":
		if (ka == "inf" && kb == "inf") || (a&0x7FFFFFFF == 0 && b&0x7FFFFFFF == 0) {
			return 0x7F800001
		}
		if ka == "inf" || b&0x7FFFFFFF == 0 {
			return inf
		}
		if kb == "inf" {
			return sign << 31
		}
		q := new(big.Rat).Quo(fpDecode(a, 8, 23), fpDecode(b, 8, 23))
		return fpEncode(q, int(sign), 8, 23)
	default: // add
		if ka == "inf" && kb == "inf" && (a^b)>>31 != 0 {
			return 0x7F800001
		}
		if ka == "inf" {
			return a
		}
		if kb == "inf" {
			return b
		}
		sum := new(big.Rat).Add(fpDecode(a, 8, 23), fpDecode(b, 8, 23))
		return fpEncode(sum, int((a&b)>>31), 8, 23)
	}
}

func fpMinmax(a, b uint32, maximum bool) (uint32, uint32) {
	flags := fpCompare(a, b)
	if fpKind(a, 8, 23) == "nan" {
		return b, flags
	}
	if fpKind(b, 8, 23) == "nan" {
		return a, flags
	}
	if a&0x7FFFFFFF == 0 && b&0x7FFFFFFF == 0 {
		if maximum {
			return a & b, flags
		}
		return a | b, flags
	}
	var wantFlags uint32 = 8
	if maximum {
		wantFlags = 2
	}
	if flags == wantFlags {
		return a, flags
	}
	return b, flags
}

func fpRound(r *big.Rat, mode string) *big.Int {
	switch mode {
	case "trunc":
		return new(big.Int).Quo(r.Num(), r.Denom())
	case "floor":
		q := new(big.Int)
		m := new(big.Int)
		q.QuoRem(r.Num(), r.Denom(), m)
		if m.Sign() < 0 {
			q.Sub(q, bigOneInt)
		}
		return q
	case "ceil":
		q := new(big.Int)
		m := new(big.Int)
		q.QuoRem(r.Num(), r.Denom(), m)
		if m.Sign() > 0 {
			q.Add(q, bigOneInt)
		}
		return q
	default: // even
		neg := r.Sign() < 0
		abs := new(big.Rat).Abs(r)
		q := new(big.Int)
		m := new(big.Int)
		q.QuoRem(abs.Num(), abs.Denom(), m)
		twice := new(big.Int).Lsh(m, 1)
		cmp := twice.Cmp(abs.Denom())
		if cmp > 0 || (cmp == 0 && q.Bit(0) == 1) {
			q.Add(q, bigOneInt)
		}
		if neg {
			q.Neg(q)
		}
		return q
	}
}

func fpConvertModel(operation, rounding string) modelFunc {
	return func(c *CPU, op *Operands, st *State) (*Effect, error) {
		bits := c.R[op.Num("s")]
		k := fpKind(bits, 8, 23)
		var result uint32
		switch operation {
		case "itof", "utof":
			var v int64
			if operation == "itof" {
				v = s32(int64(bits))
			} else {
				v = int64(bits)
			}
			result = fpEncode(new(big.Rat).SetInt64(v), 0, 8, 23)
		case "half":
			sign := bits >> 31
			if k == "finite" {
				result = fpEncode(fpDecode(bits, 8, 23), int(sign), 5, 10)
			} else if k == "inf" {
				result = (sign << 15) | 0x7C00
			} else if bits&0x400000 != 0 {
				result = (sign << 15) | 0x7E00
			} else {
				result = (sign << 15) | 0x7C01
			}
			result |= c.R[op.Num("r")] & 0xFFFF0000
		case "full":
			bits &= 0xFFFF
			k = fpKind(bits, 5, 10)
			if k == "finite" {
				result = fpEncode(fpDecode(bits, 5, 10), int(bits>>15), 8, 23)
			} else if k == "inf" {
				result = ((bits >> 15) << 31) | 0x7F800000
			} else if bits&0x200 != 0 {
				result = ((bits >> 15) << 31) | 0x7FC00000
			} else {
				result = ((bits >> 15) << 31) | 0x7F800001
			}
		case "ftof":
			if k == "nan" {
				result = (bits & 0x80000000) | 0x7FC00000
			} else if k == "inf" {
				result = bits
			} else {
				iv := fpRound(fpDecode(bits, 8, 23), rounding)
				result = fpEncode(new(big.Rat).SetInt(iv), int(bits>>31), 8, 23)
			}
		default: // ftoi / ftou
			var lo, hi int64
			if operation == "ftoi" {
				lo, hi = -(int64(1)<<31), (int64(1)<<31)-1
			} else {
				lo, hi = 0, 0xFFFFFFFF
			}
			if bits>>31 != 0 {
				result = uint32(lo)
			} else {
				result = uint32(hi)
			}
			if k == "finite" {
				iv := fpRound(fpDecode(bits, 8, 23), rounding)
				// Clamp in arbitrary precision before narrowing: the rounded
				// value can be far outside int64 (huge/negative floats).
				loB, hiB := big.NewInt(lo), big.NewInt(hi)
				var r int64
				switch {
				case iv.Cmp(loB) < 0:
					r = lo
				case iv.Cmp(hiB) > 0:
					r = hi
				default:
					r = iv.Int64()
				}
				result = uint32(r)
			}
		}
		return ef().reg(int(op.Num("r")), result).fl(c.flags), nil
	}
}

func fpDivideModel(pair bool, isSigned bool) modelFunc {
	return func(c *CPU, op *Operands, st *State) (*Effect, error) {
		num := new(big.Int).SetUint64(uint64(c.R[op.Num("s")]))
		if pair {
			num.Or(num, new(big.Int).Lsh(new(big.Int).SetUint64(uint64(c.R[op.Num("s")+1])), 32))
		}
		den := new(big.Int).SetUint64(uint64(c.R[op.Num("t")]))
		if isSigned {
			width := 32
			if pair {
				width = 64
			}
			num = bigSigned(num, width)
			den = bigSigned(den, 32)
		}
		quot := new(big.Int)
		if den.Sign() != 0 {
			quot.Quo(new(big.Int).Abs(num), new(big.Int).Abs(den))
		}
		if (num.Sign() < 0) != (den.Sign() < 0) {
			quot.Neg(quot)
		}
		e := ef()
		// The quotient may be negative: extract its two's-complement low 64
		// bits (Python's `quotient & MASK` / `quotient >> 32 & MASK`), not the
		// magnitude (big.Int.Uint64 ignores the sign).
		low64 := new(big.Int).And(quot, bigMask64)
		low := uint32(low64.Uint64() & 0xFFFFFFFF)
		if pair {
			high := uint32(low64.Uint64() >> 32)
			e.reg(int(op.Num("r")), low)
			e.reg(int(op.Num("r"))+1, high)
			e.fl(c.flags)
		} else {
			value := low
			e.reg(int(op.Num("r")), value)
			flags := (c.flags & 3) | boolU32(value == 0)<<2 | (value>>31)<<3
			e.fl(flags)
		}
		return e, nil
	}
}

// bigSigned sign-extends a non-negative width-bit integer.
func bigSigned(x *big.Int, width int) *big.Int {
	if width == 0 {
		return new(big.Int).Set(x)
	}
	if x.Bit(width-1) != 0 {
		r := new(big.Int).Set(x)
		r.Sub(r, new(big.Int).Lsh(bigOneInt, uint(width)))
		return r
	}
	return new(big.Int).Set(x)
}
