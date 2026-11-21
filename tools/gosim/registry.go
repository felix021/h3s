package gosim

import "math/bits"

// buildModels assembles the instruction-model registry, replicating the
// upstream semantics modules' registration (basic, basic2, p, b2, d2, k2, m2,
// v3/v4/v5 blocks, q, r, s).

func buildModels() map[string]modelFunc {
	m := registerBase()
	registerK2(m, true) // raw pi32v2-isa keeps the _with_stub instrumentation
	return m
}

func buildArchModels() map[string]modelFunc {
	m := registerBase()
	registerK2(m, false) // architectural: strip the hardware-runner instrumentation
	return m
}

func registerBase() map[string]modelFunc {
	m := map[string]modelFunc{}

	// ---- basic.py ------------------------------------------------------
	m["nop"] = modelNop
	m["mov.rr"] = modelMovRR
	m["li.u8"] = modelLiU8
	m["lsl.rri"] = modelLslRRI
	m["add.rrr"] = modelAddRRR
	m["sub.rrr"] = modelSubRRR

	// ---- basic2.py -----------------------------------------------------
	m["ld.w"] = modelLdW
	m["st.w"] = modelStW
	m["beqz"] = modelBeqz
	m["rts"] = modelRts

	// ---- p.py ----------------------------------------------------------
	m["add.rrr.l"] = addModel("r", "s", "t")
	m["add.rr"] = addModel("d", "d", "s")
	m["add.rri"] = addModel("d", "s", "n")
	m["add.modimm.rri"] = addModel("r", "s", "n")
	m["add.s14.rri"] = addModel("r", "s", "n")
	for _, id := range []string{"add.ri.neg7", "add.ri.u5", "add.ri.u5_hi", "add.ri.u6_hi"} {
		m[id] = addModel("r", "r", "n")
	}
	m["sub.rrr.l"] = subModel("r", "s", "t")
	m["sub.modimm.rri"] = subModel("r", "s", "n")
	m["rsub.modimm.rri"] = subModel("r", "n", "s")
	m["addc.rrr"] = modelAddc
	m["subc.rrr"] = modelSubc
	m["add.brev.rr"] = modelAddBrev

	nz2 := map[string][2]string{
		"and.rr": {"d", "and"}, "or.rr": {"d", "or"}, "xor.rr": {"d", "xor"}, "not.rr": {"d", "not"},
		"and.rrr": {"r", "andr"}, "or.rrr": {"r", "orr"}, "xor.rrr": {"r", "xorr"}, "bic.rrr": {"r", "bic"},
		"and.bit.rrr": {"r", "andbit"}, "and.invbit.rrr": {"r", "andinvbit"},
		"or.bit.rrr": {"r", "orbit"}, "xor.bit.rrr": {"r", "xorbit"},
		"and.invbit": {"r", "andinvbitc"}, "or.bit": {"r", "orbitc"}, "xor.bit": {"r", "xorbitc"},
		"and.modimm.rri": {"r", "andn"}, "and.nmodimm.rri": {"r", "andn"}, "or.modimm.rri": {"r", "orn"},
		"xor.modimm.rri": {"r", "xorn"}, "mul.rr": {"d", "mul"}, "mul.rrr": {"r", "mulr"},
		"mul.modimm.rri": {"r", "muln"},
		"ext.b0.u": {"d", "b0u"}, "ext.b0.s": {"d", "b0s"}, "ext.l.u": {"d", "lu"}, "ext.l.s": {"d", "ls"},
		"sgn.rr": {"r", "sgn"}, "rev8.rr": {"r", "rev8"}, "rev16.rr": {"r", "rev16"}, "revs.rr": {"r", "revs"},
		"ror.rri": {"r", "ror"}, "ror.rrr": {"r", "rorr"},
	}
	for id, spec := range nz2 {
		dst, op := spec[0], spec[1]
		m[id] = nzModel(dst, makeNzFn(op))
	}

	m["clz.rr"] = modelClz
	m["cls.rr"] = modelCls

	m["li.byte"] = modelLiByte
	m["li.half"] = modelLiHalf
	for _, id := range []string{"li.modimm", "li.neg5", "li.s16", "li.u32"} {
		m[id] = func(c *CPU, op *Operands, st *State) (*Effect, error) {
			return ef().reg(int(op.Num("r")), uint32(op.Num("n"))).fl(c.flags), nil
		}
	}
	m["li.zero_hi"] = func(c *CPU, op *Operands, st *State) (*Effect, error) {
		return ef().reg(int(op.Num("r")), 0).fl(c.flags), nil
	}
	m["li.zero_pair"] = func(c *CPU, op *Operands, st *State) (*Effect, error) {
		return ef().reg(int(op.Num("p")), 0).reg(int(op.Num("p"))+1, 0).fl(c.flags), nil
	}
	m["mov.pair"] = func(c *CPU, op *Operands, st *State) (*Effect, error) {
		return ef().reg(int(op.Num("d")), c.R[op.Num("s")]).reg(int(op.Num("d"))+1, c.R[op.Num("s")+1]).fl(c.flags), nil
	}
	m["mov.half"] = modelMovHalf

	m["uextra.rrii"] = modelUextra
	m["sextra.rrii"] = modelSextra
	m["insert.rrii"] = modelInsert

	for _, kind := range []string{"lsl", "lsr", "asr", "shl3"} {
		m[kind+".rr"] = shiftModel(kind, "d", "d", "s")
		m[kind+".rrr"] = shiftModel(kind, "r", "s", "t")
	}
	for _, kind := range []string{"lsl", "lsr", "asr", "shl3"} {
		m[kind+".rri.l"] = shiftModel(kind, "r", "s", "n")
	}
	for _, kind := range []string{"lsr", "asr", "shl3"} {
		m[kind+".rri"] = shiftModel(kind, "d", "s", "n")
	}

	m["cycc.rri"] = modelRorcC("s", "n", "r")
	m["cycc.rrr"] = modelRorcC("s", "t", "r")
	m["xchg.rrr"] = modelXchgRRR

	for _, kind := range []string{"lsl", "lsr", "asr", "shl3"} {
		m[kind+".pair.i"] = pairShiftModel(kind, true)
		m[kind+".pair.r"] = pairShiftModel(kind, false)
	}
	m["ror.pi"] = pairShiftModel("ror", true)
	m["ror.pr"] = pairShiftModel("ror", false)
	m["cycc.pi"] = pairShiftModel("rorc", true)
	m["cycc.pr"] = pairShiftModel("rorc", false)

	m["cc.set"] = modelCcSet
	m["shiftr.rpi.mode"] = shiftrModel(true)
	m["shiftr.rpr.mode"] = shiftrModel(false)
	m["lsr.rri.vb"] = modelLsrVB
	m["asr.ppi.rnd.vh"] = modelAsrVH
	m["popcnt.rr"] = modelPopcntRR
	m["popcnt.r_pair"] = modelPopcntRPair

	// ---- b2.py ---------------------------------------------------------
	for _, id := range []string{"call.r", "call.s", "call.l", "call.xl"} {
		m[id] = modelCall
	}
	for _, id := range []string{"goto.r", "goto.s", "goto.l"} {
		m[id] = modelJump
	}
	m["bnez"] = modelBnez
	m["dbnz.r"] = modelDbnz
	m["ifs2ns.c"] = modelIfs2nsC
	m["ifs2ns.s"] = modelIfs2nsS
	for _, cond := range []string{"eq", "ne", "cs", "cc", "hi", "ls", "ge", "lt", "gt", "le"} {
		m["bc."+cond] = flagBranchModel(cond)
	}
	for _, cond := range []string{"eq", "ne", "ge", "lt", "gt", "le"} {
		for _, shape := range []string{"rr", "rr.l", "ri", "ri.l", "ri.l.modimm"} {
			m["b"+cond+"."+shape] = compareModel(cond, compareOpts{})
		}
		if cond == "eq" || cond == "ne" {
			for _, shape := range []string{"rr", "ri", "modimm.ri.l"} {
				m["b"+cond+".and."+shape] = compareModel(cond, compareOpts{masked: true})
			}
		} else {
			for _, shape := range []string{"rr", "rr.l", "ri", "ri.l", "ri.l.modimm"} {
				m["b"+cond+"s."+shape] = compareModel(cond, compareOpts{signedCmp: true})
			}
		}
	}
	for _, cond := range []string{"eq", "une", "ge", "lt", "gt", "le", "uge", "ult", "ugt", "ule"} {
		for _, shape := range []string{"rr", "rr.l"} {
			m["bf"+cond+"."+shape] = compareModel(cond, compareOpts{floating: true})
		}
	}

	// ---- d2.py ---------------------------------------------------------
	for _, acc := range []struct {
		op string
		v  int
	}{{"mul", 0}, {"madd", 1}, {"msub", -1}} {
		for _, sign := range []bool{false, true} {
			ss := "u"
			if sign {
				ss = "s"
			}
			for _, sz := range []struct {
				name string
				w    int
			}{{"h", 16}, {"w", 32}} {
				for _, addr := range []string{"i", "r"} {
					m[acc.op+".mem1."+sz.name+"."+addr+"."+ss] = makeMultiply("mem1", [2]int{sz.w, 32}, addr, sign, acc.v)
				}
			}
			for _, sz := range []struct {
				name string
				w    [2]int
			}{{"hh", [2]int{16, 32}}, {"hw", [2]int{16, 32}}, {"ww", [2]int{32, 32}}} {
				for _, addr := range []string{"i", "rh"} {
					if sz.name == "hh" && addr == "i" {
						continue
					}
					m[acc.op+".mem2."+sz.name+"."+addr+"."+ss] = makeMultiply("mem2", sz.w, addr, sign, acc.v)
				}
			}
		}
	}
	for _, id := range []string{"flush", "flushinv", "iflush", "pfetch"} {
		m[id] = modelCache
	}

	// ---- m2.py ---------------------------------------------------------
	for _, id := range memopIDs {
		m[id] = makeMemop(id)
	}
	for _, id := range rmwIDs {
		m[id] = makeRmw(id)
	}
	m["st.w.modimm"] = modelStWModimm
	m["testset.b"] = modelTestsetB
	m["ld.w.npc"] = modelNpc
	m["ld.w.npc.r"] = modelNpc

	// ---- v3/v4/v5 block + rep -----------------------------------------
	for _, b := range blockModels {
		if b.andBlock {
			m[b.id] = andBlockModel()
		} else {
			m[b.id] = blockModel(b.condition, compareOpts{signedCmp: b.signedCmp, masked: b.masked, floating: b.floating})
		}
	}
	m["rep"] = repModel(true)
	m["rep.i"] = repModel(false)

	// ---- q.py ----------------------------------------------------------
	for _, shape := range []string{"rr", "rhrh", "v.rr"} {
		for _, kind := range []string{"abs", "negs"} {
			m[kind+"."+shape] = qUnary(kind, shape)
		}
	}
	for _, shape := range []string{"rrr", "rhrhrh", "v.rrr"} {
		for _, kind := range []string{"smin", "smax", "umin", "umax"} {
			m[kind+"."+shape] = qMinmax(kind, shape)
		}
	}
	for _, family := range []string{"all", "alls", "any", "anys", "cmp", "cmps"} {
		var conds []string
		if hasSuffixStr(family, "s") {
			conds = []string{"ge", "gt", "le", "lt"}
		} else {
			conds = []string{"eq", "ne", "ge", "gt", "le", "lt"}
		}
		for _, cond := range conds {
			for _, shape := range []string{"rrr", "ppp"} {
				m[family+"."+cond+"."+shape+".v"] = qCompare(family, cond, shape == "ppp")
			}
		}
	}
	for _, sign := range []string{"s", "u"} {
		isS := sign == "s"
		m["sat8."+sign+".rhrh"] = qSaturate(8, isS, true)
		m["sat16."+sign+".rr"] = qSaturate(16, isS, false)
		m["sat8."+sign+".rr"] = qSaturate(16, isS, false)
		mode := "(ssat)"
		if sign == "u" {
			mode = "(usat)"
		}
		m["vsat16."+sign+".rp"] = qPackBytes(mode, "p")
		m["unpackb."+sign+".pr"] = qUnpack(isS, "r")
		m["unpack4."+sign+".pr"] = qUnpack(isS, "p")
	}
	m["align8.rrr"] = modelAlign(8)
	m["align16.rrr"] = modelAlign(16)
	m["align24.rrr"] = modelAlign(24)
	m["pack.rrhrh"] = modelPack
	m["packb.rp"] = qPackBytes("truncate", "s")
	m["packb.rpe"] = qPackBytes("enum", "s")
	m["packb.rp.stou"] = qPackBytes("stou", "s")
	m["mts0.prr"] = qMts(8)
	m["mts1.prr"] = qMts(16)
	m["sad4.acc.prr"] = qSad
	m["xchg.pr"] = qShift("pr", false)
	m["xchg.pr.sat"] = qShift("pr", true)
	m["xchg.rhrhr"] = qShift("rhrhr", false)
	m["xchg.rhrhr.sat"] = qShift("rhrhr", true)
	m["xchg.rrr.sat"] = qShift("rrr", true)
	m["xchg.v.rrr"] = qShift("v", false)
	m["xchg.rrr.sat.v"] = qShift("v", true)

	// ---- r.py ----------------------------------------------------------
	m["add.rhrhi"] = rMkHh(1, true)
	m["sub.rhrhi"] = rMkHh(-1, true)
	m["add.rhrhrh"] = rMkHh(1, false)
	m["sub.rhrhrh"] = rMkHh(-1, false)
	m["add.rhrhrhe"] = rMkHhe(1)
	m["sub.rhrhrhe"] = rMkHhe(-1)
	m["add.rrre"] = rMkRrre(1)
	m["sub.rrre"] = rMkRrre(-1)
	for _, kind := range []string{"lsl", "lsr", "asr", "shl3"} {
		m[kind+".rhrhi"] = rMkHshift(kind, true)
		m[kind+".rhrhr"] = rMkHshift(kind, false)
	}
	for _, kind := range []string{"lsl", "lsr", "asr", "shl3"} {
		m[kind+".v.rri"] = rMkVshift(kind, true)
		m[kind+".v.rrr"] = rMkVshift(kind, false)
	}
	m["lsr.s.rpi"] = rMkPshr(true, true)
	m["lsr.s.rpr"] = rMkPshr(true, false)
	m["lsr.u.rpi"] = rMkPshr(false, true)
	m["lsr.u.rpr"] = rMkPshr(false, false)
	m["add4.ppr"] = rAdd4Ppr
	m["add4.prr"] = rAdd4Prr
	m["add4.rrr.rnd.uavg"] = rAdd4Avg
	signsMap := map[string][2]int64{
		"addadd": {1, 1}, "addsub": {1, -1}, "subadd": {-1, 1}, "subsub": {-1, -1},
	}
	for op, sg := range signsMap {
		for _, suffix := range []string{"rnd.savg", "rnd.uavg", "savg", "uavg", "ssat", "usat", "ssat.x2", "usat.x2"} {
			m[op+".rrhrhrhrh."+suffix] = rMkTwo(sg, rModeOf(suffix))
		}
	}
	m["addsub.rhrhi.sat"] = rAddsubImmH
	m["addsub.rhrhi.mode"] = rAddsubImm2
	m["addsub.pqq.sat"] = rAddsubPqq
	m["addsub.ppp.mode"] = rAddsubPpp
	for _, sign := range []bool{false, true} {
		ss := "u"
		if sign {
			ss = "s"
		}
		m["mul."+ss+".prr"] = rMkMulp(sign, 0)
		m["madd."+ss+".prr"] = rMkMulp(sign, 1)
		m["msub."+ss+".prr"] = rMkMulp(sign, -1)
	}
	m["mul.rhrhrhe"] = rMulH
	m["mul.rrhrhe"] = rMulW
	m["madd.rhrhrhe"] = rMkMaddH(1)
	m["msub.rhrhrhe"] = rMkMaddH(-1)
	m["madd.rrhrhe"] = rMkMaddW(1)
	m["msub.rrhrhe"] = rMkMaddW(-1)
	for _, spec := range []struct {
		id    string
		acc   int64
		sign  bool
	}{{"mul.rrhi.ssat", 0, true}, {"mul.rrhi.usat", 0, false},
		{"madd.rrhi.ssat", 1, true}, {"madd.rrhi.usat", 1, false},
		{"msub.rrhi.ssat", -1, true}, {"msub.rrhi.usat", -1, false}} {
		m[spec.id] = rMkMulimm(spec.acc, spec.sign)
	}
	for _, spec := range []struct {
		id   string
		pair bool
		imm  bool
		acc  int64
		mode string
	}{
		{"mul2.rrhrhrhrhe", false, false, 0, ""},
		{"mul2.prhrhrhrhe", true, false, 0, ""},
		{"mul2.prhrhi.ssat", true, true, 0, "(ssat)"},
		{"mul2.prhrhi.usat", true, true, 0, "(usat)"},
		{"madd.mul2.rrhrhrhrhe", false, false, 1, ""},
		{"madd.mul2.prhrhrhrhe", true, false, 1, ""},
		{"madd.mul2.prhrhi.ssat", true, true, 1, "(ssat)"},
		{"madd.mul2.prhrhi.usat", true, true, 1, "(usat)"},
		{"msub.mul2.rrhrhrhrhe", false, false, -1, ""},
		{"msub.mul2.prhrhrhrhe", true, false, -1, ""},
		{"msub.mul2.prhrhi.ssat", true, true, -1, "(ssat)"},
		{"msub.mul2.prhrhi.usat", true, true, -1, "(usat)"},
	} {
		m[spec.id] = rMkMul2(spec.pair, spec.imm, spec.acc, spec.mode)
	}
	for _, sign := range []string{"s", "u"} {
		mode := "(ssat)"
		if sign == "u" {
			mode = "(usat)"
		}
		for _, spec := range []struct {
			name string
			acc  int64
		}{{"sum", 0}, {"sum.acc", 1}, {"sum.nacc", -1}} {
			m[spec.name+"."+sign+".prhrhrhrh"] = rMkMul2(true, false, spec.acc, mode)
		}
	}
	m["mul4.u.prr"] = rMul4U
	m["mul4.m.prr"] = rMul4M
	m["mul4.m.pri"] = rMul4Mi
	m["madd.mul4.u.prr"] = rMkMadd4(false)
	m["madd.mul4.m.prr"] = rMkMadd4(true)
	for _, id := range []string{"sum.acc.usat.rrr", "sum.acc.ssat.rrr", "sum.acc.msat.rrr"} {
		m[id] = rAdd4Avg
	}

	// ---- s.py ----------------------------------------------------------
	for _, op := range []string{"add", "sub", "mul", "div", "madd", "msub"} {
		m[op+".f.rrr"] = fpOpModel(op)
	}
	m["fcmp.rr"] = func(c *CPU, op *Operands, st *State) (*Effect, error) {
		return ef().fl(fpCompare(c.R[op.Num("r")], c.R[op.Num("s")])), nil
	}
	m["fmin.rrr"] = fpMinmaxModel(false)
	m["fmax.rrr"] = fpMinmaxModel(true)
	for _, op := range []string{"ftoi", "ftou", "ftof"} {
		for _, mode := range []string{"even", "trunc", "ceil", "floor"} {
			m[op+"."+mode+".rr"] = fpConvertModel(op, mode)
		}
	}
	m["itof.rr"] = fpConvertModel("itof", "even")
	m["utof.rr"] = fpConvertModel("utof", "even")
	m["ftof.half.rr"] = fpConvertModel("half", "even")
	m["ftof.full.rr"] = fpConvertModel("full", "even")
	for _, spec := range []struct {
		id   string
		pair bool
		sign bool
	}{{"div.s.rrr", false, true}, {"div.s.ppr", true, true},
		{"div.u.rrr", false, false}, {"div.u.ppr", true, false}} {
		m[spec.id] = fpDivideModel(spec.pair, spec.sign)
	}

	return m
}

func registerK2(m map[string]modelFunc, stub bool) {
	inner := map[string]modelFunc{
		"push.rl":          k2Push(func(c *CPU, op *Operands, st *State) []uint32 { return regVals(c, k2Rlist(op.Raw("l"))) }),
		"pop.rl":           k2Pop(func(op *Operands) []int { return k2Rlist(op.Raw("l")) }, ""),
		"push.rets_rl":     k2Push(func(c *CPU, op *Operands, st *State) []uint32 { return append(regVals(c, k2Rlist(op.Raw("l"))), st.rets) }),
		"pop.rets_rl":      k2Pop(func(op *Operands) []int { return k2Rlist(op.Raw("l")) }, "rets"),
		"pop.pc_rl":        k2Pop(func(op *Operands) []int { return k2Rlist(op.Raw("l")) }, "pc"),
		"push.rets":        k2Push(func(c *CPU, op *Operands, st *State) []uint32 { return []uint32{st.rets} }),
		"pop.pc":           k2Pop(func(op *Operands) []int { return nil }, "pc"),
		"push.rmask":       k2Push(func(c *CPU, op *Operands, st *State) []uint32 { return regVals(c, k2Bits(rmaskField(op))) }),
		"push.rmask.c":     k2Push(func(c *CPU, op *Operands, st *State) []uint32 { return regVals(c, k2Bits(rmaskField(op))) }),
		"push.rets_rmask":  k2Push(func(c *CPU, op *Operands, st *State) []uint32 { return append(regVals(c, k2Bits(rmaskField(op))), st.rets) }),
		"push.rets_rmask.c": k2Push(func(c *CPU, op *Operands, st *State) []uint32 { return append(regVals(c, k2Bits(rmaskField(op))), st.rets) }),
		"pop.rmask":        k2Pop(func(op *Operands) []int { return k2Bits(op.Raw("n")) }, ""),
		"pop.rets_rmask":   k2Pop(func(op *Operands) []int { return k2Bits(op.Raw("n")) }, "rets"),
		"pop.pc_rmask":     k2Pop(func(op *Operands) []int { return k2Bits(op.Raw("n")) }, "pc"),
		"push.srl":         k2PushSR("m"),
		"push.srmask":      k2PushSR("m"),
		"push.srmask.c":    k2PushSR("n"),
		"mov.r_sr":         modelMovRSR,
		"csync":            modelCsync,
		"add.sp_i":         modelAddSpI,
		"add.sp_i.l":       modelAddSpI,
		"add.r_sp_i":       modelAddRSpI,
		"add.r_sp_i.l":     modelAddRSpI,
		"ldm.post1":        ldstModel(false, false, false),
		"stm.post1":        ldstModel(true, false, false),
		"ldm.post2":        ldstModel(false, true, false),
		"stm.post2":        ldstModel(true, true, false),
		"ldm.pre1":         ldstModel(false, false, true),
		"stm.pre1":         ldstModel(true, false, true),
		"ldm.pre2":         ldstModel(false, true, true),
		"stm.pre2":         ldstModel(true, true, true),
		"insert.rrriirii":  fieldModel(modelInsert),
		"uextra.rrriirii":  fieldModel(modelUextra),
		"sextra.rrriirii":  fieldModel(modelSextra),
	}
	for id, fn := range inner {
		if stub {
			m[id] = withStub(fn)
		} else {
			m[id] = fn
		}
	}
}

func regVals(c *CPU, regs []int) []uint32 {
	out := make([]uint32, len(regs))
	for i, r := range regs {
		out[i] = c.R[r]
	}
	return out
}

func rmaskField(op *Operands) uint32 {
	if v, ok := op.raw["m"]; ok {
		return v
	}
	return op.raw["n"]
}

func hasSuffixStr(s, p string) bool { return len(s) >= len(p) && s[len(s)-len(p):] == p }

// makeNzFn builds the _nz lambda body by op name.
func makeNzFn(op string) func(c *CPU, opd *Operands) uint32 {
	switch op {
	case "and":
		return func(c *CPU, o *Operands) uint32 { return c.R[o.Num("d")] & c.R[o.Num("s")] }
	case "or":
		return func(c *CPU, o *Operands) uint32 { return c.R[o.Num("d")] | c.R[o.Num("s")] }
	case "xor":
		return func(c *CPU, o *Operands) uint32 { return c.R[o.Num("d")] ^ c.R[o.Num("s")] }
	case "not":
		return func(c *CPU, o *Operands) uint32 { return ^c.R[o.Num("s")] }
	case "andr":
		return func(c *CPU, o *Operands) uint32 { return c.R[o.Num("s")] & c.R[o.Num("t")] }
	case "orr":
		return func(c *CPU, o *Operands) uint32 { return c.R[o.Num("s")] | c.R[o.Num("t")] }
	case "xorr":
		return func(c *CPU, o *Operands) uint32 { return c.R[o.Num("s")] ^ c.R[o.Num("t")] }
	case "bic":
		return func(c *CPU, o *Operands) uint32 { return c.R[o.Num("s")] &^ c.R[o.Num("t")] }
	case "andbit":
		return func(c *CPU, o *Operands) uint32 { return c.R[o.Num("s")] & (1 << (c.R[o.Num("t")] & 31)) }
	case "andinvbit":
		return func(c *CPU, o *Operands) uint32 { return c.R[o.Num("s")] &^ (1 << (c.R[o.Num("t")] & 31)) }
	case "orbit":
		return func(c *CPU, o *Operands) uint32 { return c.R[o.Num("s")] | (1 << (c.R[o.Num("t")] & 31)) }
	case "xorbit":
		return func(c *CPU, o *Operands) uint32 { return c.R[o.Num("s")] ^ (1 << (c.R[o.Num("t")] & 31)) }
	case "andinvbitc":
		return func(c *CPU, o *Operands) uint32 { return c.R[o.Num("r")] & uint32(o.Num("b")) }
	case "orbitc":
		return func(c *CPU, o *Operands) uint32 { return c.R[o.Num("r")] | uint32(o.Num("b")) }
	case "xorbitc":
		return func(c *CPU, o *Operands) uint32 { return c.R[o.Num("r")] ^ uint32(o.Num("b")) }
	case "andn":
		return func(c *CPU, o *Operands) uint32 { return c.R[o.Num("s")] & uint32(o.Num("n")) }
	case "orn":
		return func(c *CPU, o *Operands) uint32 { return c.R[o.Num("s")] | uint32(o.Num("n")) }
	case "xorn":
		return func(c *CPU, o *Operands) uint32 { return c.R[o.Num("s")] ^ uint32(o.Num("n")) }
	case "mul":
		return func(c *CPU, o *Operands) uint32 { return c.R[o.Num("d")] * c.R[o.Num("s")] }
	case "mulr":
		return func(c *CPU, o *Operands) uint32 { return c.R[o.Num("s")] * c.R[o.Num("t")] }
	case "muln":
		return func(c *CPU, o *Operands) uint32 { return c.R[o.Num("s")] * uint32(o.Num("n")) }
	case "b0u":
		return func(c *CPU, o *Operands) uint32 { return c.R[o.Num("s")] & 0xFF }
	case "b0s":
		return func(c *CPU, o *Operands) uint32 { return uint32(sext(int64(c.R[o.Num("s")]&0xFF), 8)) }
	case "lu":
		return func(c *CPU, o *Operands) uint32 { return c.R[o.Num("s")] & 0xFFFF }
	case "ls":
		return func(c *CPU, o *Operands) uint32 { return uint32(sext(int64(c.R[o.Num("s")]&0xFFFF), 16)) }
	case "sgn":
		return func(c *CPU, o *Operands) uint32 {
			x := c.R[o.Num("s")]
			if x == 0 {
				return 0
			}
			if x>>31 != 0 {
				return 0xFFFFFFFF
			}
			return 1
		}
	case "rev8":
		return func(c *CPU, o *Operands) uint32 {
			return bits.ReverseBytes32(c.R[o.Num("s")])
		}
	case "rev16":
		return func(c *CPU, o *Operands) uint32 {
			x := c.R[o.Num("s")]
			return (x&0x00FF00FF)<<8 | (x>>8)&0x00FF00FF
		}
	case "revs":
		return func(c *CPU, o *Operands) uint32 {
			x := c.R[o.Num("s")]
			v := int64(((x&0xFF)<<8 | (x>>8)&0xFF) ^ 0x8000)
			return uint32(v - 0x8000)
		}
	case "ror":
		return func(c *CPU, o *Operands) uint32 { return ror32(c.R[o.Num("s")], o.Num("n")) }
	case "rorr":
		return func(c *CPU, o *Operands) uint32 { return ror32(c.R[o.Num("s")], int64(c.R[o.Num("t")])) }
	}
	panic("unknown nz op " + op)
}

func fpOpModel(operation string) modelFunc {
	return func(c *CPU, op *Operands, st *State) (*Effect, error) {
		var acc *uint32
		if operation == "madd" || operation == "msub" {
			a := c.R[op.Num("r")]
			acc = &a
		}
		return ef().reg(int(op.Num("r")), fpArithmetic(c.R[op.Num("s")], c.R[op.Num("t")], operation, acc)).fl(c.flags), nil
	}
}

func fpMinmaxModel(maximum bool) modelFunc {
	return func(c *CPU, op *Operands, st *State) (*Effect, error) {
		v, flags := fpMinmax(c.R[op.Num("s")], c.R[op.Num("t")], maximum)
		return ef().reg(int(op.Num("r")), v).fl(flags), nil
	}
}
