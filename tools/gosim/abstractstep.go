package gosim

// abstractstep.go: the partial-ICFG instruction handlers and the abstract
// task-stack transport, the Go twins of probe_boot.step_body's
// "machine.partial_icfg" branch and the "abstract_task_stack_transport"
// adapter. They run on the Core twin directly (mutating it in place) and hand
// the step back to Python the moment any numeric input is an abstract value
// the evidence does not pin (Python re-runs and raises the identical Blocked).

import "strings"

// setReg writes a concrete value into a register, clearing its tag.
func setReg(core *Core, r int, v uint32) {
	core.R[r] = v & wordMask
	core.RT[r] = nil
}

// setRegTag installs an abstract value tag (placeholder concrete value 0).
func setRegTag(core *Core, r int, tag *RegTag) {
	core.R[r] = 0
	core.RT[r] = tag
}

func regsConcrete(core *Core, regs ...int) bool {
	for _, r := range regs {
		if core.RT[r] != nil {
			return false
		}
	}
	return true
}

// isFullWordStore reports whether id is one of the standalone full-word store
// forms the partial-ICFG spill and the exact_full_word_store adapters cover.
func isFullWordStore(id string) bool {
	switch id {
	case "st.w", "st.w.l", "st.w.modimm", "st.w.idxs", "st.w.sp", "st.w.idx":
		return true
	}
	return false
}

// isFullWordLoad reports whether id is one of the full-word load forms the
// partial-ICFG reload adapter covers.
func isFullWordLoad(id string) bool {
	switch id {
	case "ld.w", "ld.w.l", "ld.w.sp", "ld.w.idxs", "ld.w.idx":
		return true
	}
	return false
}

// loadAddrDest mirrors the partial_icfg_word_reload address/dest computation.
func loadAddrDest(core *Core, op *Operands, id string) (uint32, int) {
	switch id {
	case "ld.w":
		return (core.R[op.Num("b")] + uint32(op.Num("n"))) & wordMask, int(op.Num("d"))
	case "ld.w.l":
		return (core.R[op.Num("s")] + uint32(op.Num("n"))) & wordMask, int(op.Num("r"))
	case "ld.w.sp":
		return (core.SP + uint32(op.Num("n"))) & wordMask, int(op.Num("r"))
	case "ld.w.idxs":
		return (core.R[op.Num("s")] + (core.R[op.Num("t")] << 2)) & wordMask, int(op.Num("r"))
	case "ld.w.idx":
		return (core.R[op.Num("s")] + core.R[op.Num("t")]) & wordMask, int(op.Num("r"))
	}
	return 0, 0
}

// storeValueReg returns the register index that supplies the stored value, or
// -1 for the immediate form (st.w.modimm).
func storeValueReg(op *Operands, id string) int {
	switch id {
	case "st.w":
		return int(op.Num("d"))
	case "st.w.l":
		return int(op.Num("s"))
	case "st.w.idxs", "st.w.idx":
		return int(op.Num("t"))
	case "st.w.sp":
		return int(op.Num("r"))
	default:
		return -1 // st.w.modimm (immediate, never abstract)
	}
}

// storeAddrRegs returns the address-operand register indices a full-word store
// consumes (all must be concrete when the value is abstract).
func storeAddrRegs(op *Operands, id string) []int {
	switch id {
	case "st.w":
		return []int{int(op.Num("b"))}
	case "st.w.l":
		return []int{int(op.Num("r"))}
	case "st.w.idxs", "st.w.idx":
		return []int{int(op.Num("r")), int(op.Num("s"))}
	case "st.w.sp":
		return nil // SP tracked separately
	default:
		return []int{int(op.Num("r"))} // st.w.modimm (never abstract anyway)
	}
}

// loadAddrRegs returns the address-operand register indices a full-word load
// consumes (all must be concrete for the abstract reload).
func loadAddrRegs(op *Operands, id string) []int {
	switch id {
	case "ld.w":
		return []int{int(op.Num("b"))}
	case "ld.w.l":
		return []int{int(op.Num("s"))}
	case "ld.w.sp":
		return nil
	case "ld.w.idxs", "ld.w.idx":
		return []int{int(op.Num("s")), int(op.Num("t"))}
	}
	return nil
}

// runIcfgStep executes one partial-ICFG instruction form. ok is true when the
// instruction is a partial-ICFG handler (id + operand shape); when ok and
// code == BatchOK the core has already been committed (Direct). When ok and
// code != BatchOK the step must be handed back to Python (which re-derives the
// identical Blocked), without committing.
func (c *CPU) runIcfgStep(core *Core, im *insnMeta, raw map[string]uint32, pc uint32, size uint32, insnBytes []byte) (SegmentStep, int, bool) {
	id := im.id
	op := newOperands(im, raw)

	// 1. SR read/write of the ICFG (s == 11).
	if id == "mov.r_sr" || id == "mov.sr_r" || id == "li.sr_u32" {
		if raw["s"] != 11 {
			return SegmentStep{}, 0, false
		}
		if id == "mov.r_sr" {
			setRegTag(core, int(op.Num("r")), icfgRead(core))
		} else if id == "li.sr_u32" {
			av, ak, ok := icfgWriteIntAfter(core, uint32(op.Num("n")))
			if !ok {
				return SegmentStep{}, BatchAbstract, true
			}
			if icfgGateDemandChanged(core.ICFG, core.ICFGKnownMask, av, ak) {
				return SegmentStep{}, BatchAbstract, true // gate demand change must land at a fill boundary
			}
			icfgWriteInt(core, uint32(op.Num("n")))
		} else { // mov.sr_r
			r := int(op.Num("r"))
			tag := core.RT[r]
			var av, ak uint32
			var ok bool
			if tag == nil {
				av, ak, ok = icfgWriteIntAfter(core, core.R[r])
			} else if tag.Kind == "icfg_value" {
				av, ak, ok = icfgWriteTagAfter(core, tag)
			} else {
				return SegmentStep{}, BatchAbstract, true // UnknownWord/other: consumed
			}
			if !ok {
				return SegmentStep{}, BatchAbstract, true
			}
			if icfgGateDemandChanged(core.ICFG, core.ICFGKnownMask, av, ak) {
				return SegmentStep{}, BatchAbstract, true // gate demand change must land at a fill boundary
			}
			if tag == nil {
				icfgWriteInt(core, core.R[r])
			} else {
				icfgWriteTag(core, tag)
			}
		}
		core.PC = pc + size
		core.Steps++
		ev := icfgWriteEvent(core)
		if id == "mov.r_sr" {
			ev = icfgReadEvent(core)
		}
		return SegmentStep{
			PC: pc, NextPC: core.PC, Flags: uint8(core.Flags & 0xF), R: core.R, Insn: insnBytes,
			Adapter: "partial_icfg_read_or_gate_write",
			Events:  []StepEvent{{Access: ev.Access, KnownMask: ev.KnownMask, KnownValue: ev.KnownValue}},
		}, BatchOK, true
	}

	// 2. CLI/STI bit-9 updates.
	if id == "cli" || id == "cli.r" || id == "sti" || id == "sti.r" {
		saved := icfgRead(core)
		var value uint32
		var known bool
		if id == "sti.r" {
			r := int(op.Num("x"))
			tag := core.RT[r]
			if tag == nil {
				value, known = core.R[r], true
			} else if tag.Kind == "icfg_value" {
				value = tag.Value
				known = tag.Known&0x200 != 0
			} else {
				return SegmentStep{}, BatchAbstract, true // consumed
			}
		} else if strings.HasPrefix(id, "cli") {
			value, known = 0, true
		} else {
			value, known = 0x200, true
		}
		av, ak := icfgBit9After(core, value, known)
		if icfgGateDemandChanged(core.ICFG, core.ICFGKnownMask, av, ak) {
			return SegmentStep{}, BatchAbstract, true // gate demand change must land at a fill boundary
		}
		if id == "cli.r" {
			setRegTag(core, int(op.Num("x")), saved)
		}
		icfgBit9(core, value, known)
		core.PC = pc + size
		core.Steps++
		ev := icfgWriteEvent(core)
		return SegmentStep{
			PC: pc, NextPC: core.PC, Flags: uint8(core.Flags & 0xF), R: core.R, Insn: insnBytes,
			Adapter: "partial_icfg_bit9_only",
			Events:  []StepEvent{{Access: ev.Access, KnownMask: ev.KnownMask, KnownValue: ev.KnownValue}},
		}, BatchOK, true
	}

	// 3. Immediate AND/OR mask of an IcfgValue source.
	if id == "and.nmodimm.rri" || id == "and.modimm.rri" || id == "or.modimm.rri" {
		s := int(op.Num("s"))
		tag := core.RT[s]
		if tag == nil || tag.Kind != "icfg_value" {
			return SegmentStep{}, 0, false
		}
		kind := "and"
		if strings.HasPrefix(id, "or.") {
			kind = "or"
		}
		value, known, identity := icfgMask(tag.Value, tag.Known, tag.Identity, kind, uint32(op.Num("n")))
		r := int(op.Num("r"))
		if folded, ok := icfgFoldToInt(value, known, identity); ok {
			setReg(core, r, folded)
		} else {
			setRegTag(core, r, &RegTag{Kind: "icfg_value", Value: value, Known: known, Identity: identity, Origin: tag.Origin})
		}
		core.PC = pc + size
		core.Steps++
		return SegmentStep{PC: pc, NextPC: core.PC, Flags: uint8(core.Flags & 0xF), R: core.R, Insn: insnBytes, Adapter: "partial_icfg_pure_bit_mask"}, BatchOK, true
	}

	// 4. Low-byte extract of an IcfgValue source.
	if id == "ext.b0.u" {
		s := int(op.Num("s"))
		tag := core.RT[s]
		if tag == nil || tag.Kind != "icfg_value" {
			return SegmentStep{}, 0, false
		}
		value, known, _ := icfgMask(tag.Value, tag.Known, tag.Identity, "and", 255)
		if known != wordMask {
			return SegmentStep{}, BatchAbstract, true // partial low byte
		}
		setReg(core, int(op.Num("d")), value)
		core.PC = pc + size
		core.Steps++
		return SegmentStep{PC: pc, NextPC: core.PC, Flags: uint8(core.Flags & 0xF), R: core.R, Insn: insnBytes, Adapter: "partial_icfg_known_lowbyte"}, BatchOK, true
	}

	// 5. Masked compare-and-branch on an IcfgValue source.
	if id == "bne.and.modimm.ri.l" || id == "beq.and.modimm.ri.l" {
		r := int(op.Num("r"))
		tag := core.RT[r]
		if tag == nil || tag.Kind != "icfg_value" {
			return SegmentStep{}, 0, false
		}
		masked, known, _ := icfgMask(tag.Value, tag.Known, tag.Identity, "and", uint32(op.Num("i")))
		if known != wordMask {
			return SegmentStep{}, BatchAbstract, true // partial
		}
		taken := masked != 0
		if strings.HasPrefix(id, "beq") {
			taken = masked == 0
		}
		nextPC := pc + size
		if taken {
			nextPC += uint32(int64(op.Num("n")))
		}
		core.PC = nextPC
		core.Steps++
		return SegmentStep{PC: pc, NextPC: core.PC, Flags: uint8(core.Flags & 0xF), R: core.R, Insn: insnBytes, Adapter: "partial_icfg_masked_branch"}, BatchOK, true
	}

	// 6. Full-word spill of an IcfgValue.
	if isFullWordStore(id) {
		valReg := storeValueReg(op, id)
		if valReg < 0 {
			return SegmentStep{}, 0, false // immediate value is never abstract
		}
		tag := core.RT[valReg]
		if tag == nil || tag.Kind != "icfg_value" {
			return SegmentStep{}, 0, false
		}
		if !regsConcrete(core, storeAddrRegs(op, id)...) {
			return SegmentStep{}, BatchAbstract, true
		}
		if id == "st.w.sp" && !core.SPKnown {
			return SegmentStep{}, BatchAbstract, true
		}
		addr, _ := exactStoreAddrValue(c, op, id)
		if c.dmem == nil {
			return SegmentStep{}, BatchUnsafe, true
		}
		if err := c.dmem.writeAbstractWord(addr, 0, tag); err != nil {
			return SegmentStep{}, BatchAbstract, true
		}
		core.PC = pc + size
		core.Steps++
		return SegmentStep{
			PC: pc, NextPC: core.PC, Flags: uint8(core.Flags & 0xF), R: core.R, Insn: insnBytes,
			Adapter: "partial_icfg_word_spill",
			Mem:     []MemWrite{{Addr: addr, Abs: tag}},
		}, BatchOK, true
	}

	// 7. Full-word reload while any spill is resident (abstract_word).
	if isFullWordLoad(id) && c.dmem != nil && c.dmem.HasIcfgSpills() {
		if !regsConcrete(core, loadAddrRegs(op, id)...) {
			return SegmentStep{}, BatchAbstract, true
		}
		if id == "ld.w.sp" && !core.SPKnown {
			return SegmentStep{}, BatchAbstract, true
		}
		addr, dest := loadAddrDest(core, op, id)
		intVal, tag, err := c.dmem.abstractWord(addr, false)
		if err != nil {
			return SegmentStep{}, BatchAbstract, true
		}
		if tag == nil {
			setReg(core, dest, intVal)
		} else {
			setRegTag(core, dest, tag)
		}
		core.PC = pc + size
		core.Steps++
		return SegmentStep{PC: pc, NextPC: core.PC, Flags: uint8(core.Flags & 0xF), R: core.R, Insn: insnBytes, Adapter: "partial_icfg_word_reload"}, BatchOK, true
	}

	return SegmentStep{}, 0, false
}

// runStackStep executes the abstract task-stack transport (PUSH/POP with the
// register side table). ok is true when the instruction is one of the ten
// transport ids (committed or handed back).
func (c *CPU) runStackStep(core *Core, im *insnMeta, raw map[string]uint32, pc uint32, size uint32, insnBytes []byte) (SegmentStep, int, bool) {
	id := im.id
	if !isTransportID(id) {
		return SegmentStep{}, 0, false
	}
	if c.dmem == nil {
		return SegmentStep{}, BatchUnsafe, true
	}
	if !core.SPKnown {
		return SegmentStep{}, BatchAbstract, true // unknown_core_stack_pointer
	}
	isPush := strings.HasPrefix(id, "push")

	var regs []int
	if strings.HasSuffix(id, "_rl") || id == "push.rl" || id == "pop.rl" {
		last := int(raw["l"])
		if last < 4 {
			for r := last; r < 4; r++ {
				regs = append(regs, r)
			}
		} else {
			for r := 4; r <= last; r++ {
				regs = append(regs, r)
			}
		}
	} else {
		mask, ok := raw["m"]
		if !ok {
			mask = raw["n"]
		}
		for r := 0; r < 16; r++ {
			if mask>>r&1 != 0 {
				regs = append(regs, r)
			}
		}
	}
	special := ""
	if strings.Contains(id, ".rets_") {
		special = "rets"
	} else if strings.Contains(id, ".pc_") {
		special = "pc"
	}
	count := len(regs)
	if special != "" {
		count++
	}

	// The scheduler's own task restore (a pop of all 16 GPRs at 0x12ada2 in the
	// software-IRQ handler) records a machine event; that bookkeeping stays in
	// Python, so hand this exact site back rather than fabricate the event.
	if !isPush && pc == 0x12ada2 && core.ActiveContext == "software_irq_handler" &&
		len(regs) == 16 && regs[0] == 0 && regs[15] == 15 {
		return SegmentStep{}, BatchAbstract, true
	}

	if isPush {
		type pv struct {
			intVal uint32
			tag    *RegTag
		}
		values := make([]pv, count)
		for i, r := range regs {
			if core.RT[r] != nil {
				values[i].tag = core.RT[r]
			} else {
				values[i].intVal = core.R[r]
			}
		}
		if special == "rets" {
			values[count-1].intVal = core.RETS
		}
		base := core.SP - 4*uint32(count)
		// Validate every destination before changing any byte or SP.
		for i := range values {
			addr := base + 4*uint32(i)
			if values[i].tag == nil {
				if _, err := c.dmem.abstractWriteTarget(addr, 4); err != nil {
					return SegmentStep{}, BatchAbstract, true
				}
			} else {
				if _, err := c.dmem.preflightAbstractWord(addr, values[i].tag); err != nil {
					return SegmentStep{}, BatchAbstract, true
				}
			}
		}
		var mem []MemWrite
		for i := range values {
			addr := base + 4*uint32(i)
			if values[i].tag == nil {
				if err := c.dmem.Write(addr, wordBytes(values[i].intVal)); err != nil {
					return SegmentStep{}, BatchAbstract, true
				}
				mem = append(mem, MemWrite{Addr: addr, Value: values[i].intVal})
			} else {
				if err := c.dmem.writeAbstractWord(addr, 0, values[i].tag); err != nil {
					return SegmentStep{}, BatchAbstract, true
				}
				mem = append(mem, MemWrite{Addr: addr, Abs: values[i].tag})
			}
		}
		beforeSP := core.SP
		core.SP = base
		core.PC = pc + size
		core.Steps++
		return SegmentStep{PC: pc, NextPC: core.PC, Flags: uint8(core.Flags & 0xF), R: core.R, BeforeSP: beforeSP, SP: core.SP, Insn: insnBytes, Adapter: "abstract_task_stack_transport", Mem: mem}, BatchOK, true
	}

	// POP: read every word first, then commit the registers and SP.
	type rv struct {
		intVal uint32
		tag    *RegTag
	}
	values := make([]rv, count)
	for i := 0; i < count; i++ {
		intVal, tag, err := c.dmem.abstractWord(core.SP+4*uint32(i), false)
		if err != nil {
			return SegmentStep{}, BatchAbstract, true
		}
		values[i].intVal, values[i].tag = intVal, tag
	}
	var target uint32
	if special != "" {
		last := values[count-1]
		if last.tag != nil {
			if last.tag.Kind == "unknown_word" {
				return SegmentStep{}, BatchAbstract, true // consumed
			}
			if last.tag.Known != wordMask {
				return SegmentStep{}, BatchAbstract, true // partial concrete()
			}
			target = last.tag.Value
		} else {
			target = last.intVal
		}
	}
	for i, r := range regs {
		if values[i].tag == nil {
			setReg(core, r, values[i].intVal)
		} else {
			setRegTag(core, r, values[i].tag)
		}
	}
	beforeSP := core.SP
	core.SP += 4 * uint32(count)
	if special == "rets" {
		core.RETS = target & 0x07fffffe
	}
	if special == "pc" {
		core.PC = target & 0xfffffffe
	} else {
		core.PC = pc + size
	}
	core.Steps++
	return SegmentStep{PC: pc, NextPC: core.PC, Flags: uint8(core.Flags & 0xF), R: core.R, BeforeSP: beforeSP, SP: core.SP, Insn: insnBytes, Adapter: "abstract_task_stack_transport"}, BatchOK, true
}

func isTransportID(id string) bool {
	switch id {
	case "push.rl", "push.rets_rl", "push.rmask", "push.rets_rmask",
		"pop.rl", "pop.rets_rl", "pop.pc_rl", "pop.rmask", "pop.rets_rmask", "pop.pc_rmask":
		return true
	}
	return false
}
