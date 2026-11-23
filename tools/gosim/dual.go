package gosim

// Dual-core whole-loop execution contract (sixth wiring): the scalar ld/st
// subset the Go loop can execute byte-for-byte against Python's general path /
// exact_full_word_store adapter, plus the strict-memory fetch/execute step that
// replaces the immutable-window RunSegment for the dual loop.

import "strings"

// dualAllowed is the whitelist of scalar ld/st the dual loop executes. Every
// other instruction falls back to the stage-1 batchUnsafe deny set (still the
// conservative contract: blockThen/body and .p pairs are rejected structurally
// in safeDual). Loads are the plain scalar forms (concrete operands, no
// adapter); stores are exactly the standalone full-word forms Python routes
// through its exact_full_word_store adapter (direct write, no merge-read).
var dualAllowed = map[string]bool{
	"ld.w": true, "ld.bu": true, "ld.hu": true,
	"ld.w.l": true, "ld.w.sp": true, "ld.w.idx": true, "ld.w.idxs": true,
	"ld.bu.l": true, "ld.hu.l": true, "ld.bu.sp.l": true, "ld.hu.sp.l": true,
	"ld.bu.idx": true, "ld.hu.idx": true, "ld.hu.idxs": true, "ld.bu.pi": true, "ld.hu.pi": true,
	"ld.bu.pin": true, "ld.hu.pin": true, "ld.bu.pre": true, "ld.hu.pre": true,
	"ld.bu.prer": true, "ld.hu.prer": true,
	"ld.w.pin": true,

	"st.w": true, "st.w.l": true, "st.w.modimm": true,
	"st.w.idxs": true, "st.w.sp": true, "st.w.idx": true,
}

// dualSubwordStoreAllowed is the whitelist of standalone byte/halfword offset
// stores the dual loop executes byte-for-byte against Python's
// exact_subword_stores adapter (probe_boot step_body). Each writes exactly its
// width bytes (1 for st.b, 2 for st.h) with no merge-read of the containing
// word; a tagged address/value operand is handed back (BatchAbstract) and an
// odd halfword address is handed back (BatchMemFault) so Python re-derives the
// identical Blocked.
var dualSubwordStoreAllowed = map[string]bool{
	"st.b": true, "st.h": true,
	"st.b.l": true, "st.h.l": true,
	"st.b.sp.l": true, "st.h.sp.l": true,
	"st.h.idxs": true,
}

// dualRmwAllowed is the whitelist of read-modify-write forms the dual loop
// executes byte-for-byte against Python's upstream m2 rmw model (makeRmw).
// Only a plain-RAM target is executed: an MMIO target is handed back
// (BatchUnsafe) so Python's peripheral model owns the read/write side effects,
// and a strict-memory fault (uninitialized read / readonly write) hands back
// through the ordinary BatchMemFault path.
var dualRmwAllowed = map[string]bool{
	"rmw.add.modimm": true, "rmw.add.r": true, "rmw.add.s12": true,
	"rmw.and.bitr": true, "rmw.and.invbitr": true, "rmw.and.invr": true,
	"rmw.and.modimm": true, "rmw.and.nmodimm": true, "rmw.and.r": true,
	"rmw.asr.i": true, "rmw.asr.r": true,
	"rmw.lsl.i": true, "rmw.lsl.r": true,
	"rmw.lsr.i": true, "rmw.lsr.r": true,
	"rmw.or.bitr": true, "rmw.or.modimm": true, "rmw.or.r": true,
	"rmw.shl3.i": true, "rmw.shl3.r": true,
	"rmw.sub.r": true,
	"rmw.xor.bitr": true, "rmw.xor.modimm": true, "rmw.xor.r": true,
}

// dualStoreAdapter maps the store ids the commit must count as Python's
// exact_full_word_store adapter ("" = no adapter to count).
var dualStoreAdapter = map[string]bool{
	"st.w": true, "st.w.l": true, "st.w.modimm": true,
	"st.w.idxs": true, "st.w.sp": true, "st.w.idx": true,
}

// dualDirectFlowAllowed is the whitelist of plain direct call/return and the
// flag-preserving csync the dual loop executes byte-for-byte against Python.
// call.s/l/xl write RETS to the return address (pc+size) and jump to the
// branch target (modelCall's retsv + exitv, applied by step1); rts jumps to
// the concrete RETS register; csync only carries the flags forward (upstream
// k2 model: {"flags": fl}). The task-stack forms (pop.pc*/push.rets*) and the
// indirect call.r / conditional rtss stay rejected via batchUnsafe — those are
// the abstract-value / timing-sensitive boundaries this cut does not touch.
var dualDirectFlowAllowed = map[string]bool{
	"call.s": true, "call.l": true, "call.xl": true,
	"rts":   true,
	"csync": true,
}

// dualIfBlockAllowed is the whitelist of register/immediate-compare if-block
// openers the dual loop executes. Each is a pure condition (eq/ne/ge/lt/gt/le
// over concrete GPRs, or a GPR-vs-immediate mask in the and.rr/and.rm forms)
// with no memory access, no flags change and no abstract output beyond the
// (internal) block sentinel: the model evaluates the compare and the core's
// Step machinery opens the conditional block (afterThen) exactly like Python's
// cpu.after_then. Floating (iff.*) and the guessed and_block (if.unknown.rr.*)
// forms stay rejected.
var dualIfBlockAllowed = map[string]bool{
	"if.eq.rr": true, "if.eq.ri.modimm": true, "if.eq.ri.s12": true,
	"if.ne.rr": true, "if.ne.ri.modimm": true, "if.ne.ri.s12": true,
	"if.ge.rr": true, "if.ge.ri.modimm": true, "if.ge.ri.s12": true,
	"if.lt.rr": true, "if.lt.ri.modimm": true, "if.lt.ri.s12": true,
	"if.gt.rr": true, "if.gt.ri.modimm": true, "if.gt.ri.s12": true,
	"if.le.rr": true, "if.le.ri.modimm": true, "if.le.ri.s12": true,
	"if.eq.and.rr": true, "if.ne.and.rr": true,
	"if.eq.and.rm": true, "if.ne.and.rm": true,
	"ifs.ge.rr": true, "ifs.ge.ri.modimm": true, "ifs.ge.ri.s12": true,
	"ifs.lt.rr": true, "ifs.lt.ri.modimm": true, "ifs.lt.ri.s12": true,
	"ifs.gt.rr": true, "ifs.gt.ri.modimm": true, "ifs.gt.ri.s12": true,
	"ifs.le.rr": true, "ifs.le.ri.modimm": true, "ifs.le.ri.s12": true,
}

// safeDual reports whether the dual loop can execute the decoded instruction
// exactly. It is the stage-1 safeALU contract relaxed with dualAllowed and the
// register-compare if-block openers (dualIfBlockAllowed). Without a strict
// memory bound (dmem == nil) the loop degrades to the pure segment contract
// (safeALU), which the differential test test_dualcore_consistency.py pins on
// the zero-handle construction.
func (c *CPU) safeDual(im *insnMeta) bool {
	if c.dmem == nil {
		return c.safeALU(im)
	}
	if im.body != "" {
		return false
	}
	if im.blockThen != "" {
		return dualIfBlockAllowed[im.id]
	}
	if strings.HasSuffix(im.id, ".p") {
		return false
	}
	if dualAllowed[im.id] || dualDirectFlowAllowed[im.id] || dualRmwAllowed[im.id] {
		return true
	}
	if batchUnsafe[im.id] {
		return false
	}
	return true
}

func isMemError(err error) bool {
	_, ok := err.(*MemError)
	return ok
}

// DualTagInfo is the per-step abstract-value bookkeeping the dual loop needs to
// maintain the register side table after a committed step.
type DualTagInfo struct {
	// Writes is the GPR indices the step wrote (apply's effect.regs keys); the
	// caller clears their tags because they are now concrete.
	Writes []int
	// MovTag is true for a mov.rr whose source carried a tag: the destination
	// inherits the source tag (a pure transport, not a numeric consumption), so
	// the caller sets RT[MovDst] = RT[MovSrc] after clearing Writes.
	MovTag bool
	MovSrc int
	MovDst int
	// Direct is true when RunDualStepCore committed the step itself (an ICFG
	// instruction or the abstract task-stack transport): the core twin already
	// carries the post-step state, so the caller must not re-sync from the CPU
	// engine (and must not re-apply Writes clearing).
	Direct bool
}

// RunDualStepCore executes one whole-loop step on c (strict fetch + dual
// contract) with abstract-value awareness, and returns the observation plus a
// stop code, stop pc, and the underlying error detail (nil on success). On
// BatchOK the SegmentStep carries the executed instruction bytes, this step's
// ordered writes, and the adapter the Python commit must count. A strict-memory
// fault (unmapped/uninitialized/unaligned) is reported as BatchMemFault without
// committing the step; Python re-runs the instruction and re-derives the
// identical Blocked (or MMIO side effect). A tagged (abstract) value that the
// instruction would consume numerically is reported as BatchAbstract without
// committing the step; Python re-runs the instruction and raises the identical
// consumed() Blocked.
func (c *CPU) RunDualStepCore(core *Core, partialIcfg, unknownTaskGprs bool) (SegmentStep, DualTagInfo, int, uint32, error) {
	pc := c.pc
	im, raw, size, err := c.decode(pc)
	if err != nil {
		if isMemError(err) {
			return SegmentStep{}, DualTagInfo{}, BatchMemFault, pc, err
		}
		return SegmentStep{}, DualTagInfo{}, BatchNoInstr, pc, err
	}
	// Abstract-value instruction handlers run before the structural contract:
	// they own the ICFG instruction forms and the task-stack transport, which
	// the stage-1/2 deny set otherwise rejects.
	insnBytes, err := c.InsnBytes(pc)
	if err != nil {
		if isMemError(err) {
			return SegmentStep{}, DualTagInfo{}, BatchMemFault, pc, err
		}
		return SegmentStep{}, DualTagInfo{}, BatchNoInstr, pc, err
	}
	if partialIcfg {
		if s, code, ok := c.runIcfgStep(core, im, raw, pc, uint32(size), insnBytes); ok {
			if code == BatchOK {
				// ICFG handlers never move SP; carry the (unchanged) value.
				s.BeforeSP, s.SP = core.SP, core.SP
			}
			return s, DualTagInfo{Direct: code == BatchOK}, code, pc, nil
		}
	}
	if unknownTaskGprs {
		if s, code, ok := c.runStackStep(core, im, raw, pc, uint32(size), insnBytes); ok {
			return s, DualTagInfo{Direct: code == BatchOK}, code, pc, nil
		}
	}
	if s, code, err, ok := c.runRepRegisterStore(core, im, raw, pc, uint32(size), insnBytes); ok {
		return s, DualTagInfo{Direct: code == BatchOK}, code, pc, err
	}
	if s, code, err, ok := c.runRepImmediateSingleStore(core, im, raw, pc, uint32(size), insnBytes); ok {
		return s, DualTagInfo{Direct: code == BatchOK}, code, pc, err
	}
	if s, code, err, ok := c.runPinByteStore(core, im, raw, pc, uint32(size), insnBytes); ok {
		return s, DualTagInfo{Direct: code == BatchOK}, code, pc, err
	}
	if s, code, err, ok := c.runPinWordStore(core, im, raw, pc, uint32(size), insnBytes); ok {
		return s, DualTagInfo{Direct: code == BatchOK}, code, pc, err
	}
	if s, code, err, ok := c.runExactSubwordStore(core, im, raw, pc, uint32(size), insnBytes); ok {
		return s, DualTagInfo{Direct: code == BatchOK}, code, pc, err
	}
	// rmw MMIO guard: an atomic read-modify-write whose target is a modeled MMIO
	// word is handed back before the bus would absorb the read/write (Python's
	// peripheral model owns those side effects). A non-MMIO target falls through
	// to safeDual + makeRmw; the strict-memory read/write faults still hand back
	// through BatchMemFault.
	if dualRmwAllowed[im.id] {
		if c.dmem == nil {
			return SegmentStep{}, DualTagInfo{}, BatchUnsafe, pc, nil
		}
		op := newOperands(im, raw)
		if core.RT[int(op.Num("r"))] == nil {
			addr := (c.R[int(op.Num("r"))] + uint32(op.Num("n"))) & wordMask
			if c.dmem.HandlesMMIO(addr, 4) {
				return SegmentStep{}, DualTagInfo{}, BatchUnsafe, pc, nil
			}
		}
	}
	if !c.safeDual(im) {
		return SegmentStep{}, DualTagInfo{}, BatchUnsafe, pc, nil
	}
	if c.models[im.id] == nil {
		return SegmentStep{}, DualTagInfo{}, BatchNoModel, pc, nil
	}
	// abstract-value gate: every consumed register must be concrete. mov.rr is
	// the one pure transport — a tagged source is moved to the destination
	// instead of consumed.
	tag := DualTagInfo{}
	if im.id == "mov.rr" {
		tag.MovSrc = int(raw["y"])
		tag.MovDst = int(raw["x"])
		if core.RT[tag.MovSrc] != nil {
			tag.MovTag = true
		}
	} else {
		for r := range consumedRegs(im, raw) {
			if core.RT[r] != nil {
				return SegmentStep{}, DualTagInfo{}, BatchAbstract, pc, nil
			}
		}
	}
	if consumesSP(im) && !core.SPKnown {
		return SegmentStep{}, DualTagInfo{}, BatchAbstract, pc, nil
	}
	// Drain any stale read-side effect counters before the step, then collect
	// this step's MMU walks / MMIO reads for the Python replay. (The raw-core
	// differential tests bind no strict memory, so dmem is nil there.)
	if c.dmem != nil {
		c.dmem.TakeWalks()
		c.dmem.TakeMMIOReads()
	}
	if err := c.Step(); err != nil {
		if isMemError(err) {
			return SegmentStep{}, DualTagInfo{}, BatchMemFault, pc, err
		}
		return SegmentStep{}, DualTagInfo{}, BatchOther, pc, err
	}
	var walks []WalkInfo
	var mmioReads int
	if c.dmem != nil {
		walks = c.dmem.TakeWalks()
		mmioReads = c.dmem.TakeMMIOReads()
	}
	adapter := ""
	if dualStoreAdapter[im.id] {
		adapter = "exact_full_word_store"
	} else if im.id == "mov.rr" && tag.MovTag {
		if core.RT[tag.MovSrc].Kind == "icfg_value" {
			adapter = "partial_icfg_register_copy"
		} else {
			adapter = "unknown_gpr_copy"
		}
	}
	tag.Writes = c.TakeRegWrites()
	return SegmentStep{
		PC:        pc,
		NextPC:    c.pc,
		Flags:     uint8(c.flags & 0xF),
		R:         c.R,
		Mem:       c.TakeWrites(),
		BeforeSP:  c.sp,
		SP:        c.sp,
		Insn:      insnBytes,
		Adapter:   adapter,
		Walks:     walks,
		MMIOReads: mmioReads,
	}, tag, BatchOK, 0, nil
}

// ---- exact full-word store models -------------------------------------------

// exactStoreAddrValue mirrors Python word_store for the standalone full-word
// store forms (probe_boot.exact_full_word_store adapter): address and value are
// masked to 32 bits; alignment is checked by the caller.
func exactStoreAddrValue(c *CPU, op *Operands, id string) (addr, val uint32) {
	switch id {
	case "st.w":
		addr = c.R[op.Num("b")] + uint32(op.Num("n"))
		val = c.R[op.Num("d")]
	case "st.w.modimm":
		addr = c.R[op.Num("r")] + uint32(op.Num("n"))
		val = uint32(op.Num("m"))
	case "st.w.idxs":
		addr = c.R[op.Num("r")] + (c.R[op.Num("s")] << 2)
		val = c.R[op.Num("t")]
	case "st.w.idx":
		addr = c.R[op.Num("r")] + c.R[op.Num("s")]
		val = c.R[op.Num("t")]
	case "st.w.l":
		addr = c.R[op.Num("r")] + uint32(op.Num("n"))
		val = c.R[op.Num("s")]
	case "st.w.sp":
		addr = c.sp + uint32(op.Num("n"))
		val = c.R[op.Num("r")]
	}
	return addr & 0xFFFFFFFF, val & 0xFFFFFFFF
}

// modelExactFullWordStore is the direct full-word store (no merge-read, no
// same-value skip), matching probe_boot's exact_full_word_store adapter. On the
// strict-memory (dual) path an unaligned target is reported as a fault so the
// step is handed back to Python, which raises the identical Blocked. The raw
// core (dmem == nil) keeps the upstream flat-memory semantics (write any
// address), which the stage-1/raw differential test test_consistency.py pins.
func modelExactFullWordStore(id string) modelFunc {
	return func(c *CPU, op *Operands, st *State) (*Effect, error) {
		addr, val := exactStoreAddrValue(c, op, id)
		if c.dmem != nil && addr%4 != 0 {
			return nil, &MemError{Kind: "unaligned_exact_word_store", Access: "write", Address: addr, Size: 4}
		}
		return ef().memw(addr, val).fl(c.flags), nil
	}
}

// runPinByteStore executes the standalone short-form byte postincrement store
// st.b.pin (syntax b[{b}++={n}] = {d}) the dual loop runs byte-for-byte against
// Python's exact_postincrement_store adapter (probe_boot step_body's
// immediate_rep_projection + exact_subword_stores short-form gate). It writes
// the source register's low byte to the old base address, then writes the base
// register back to base + n (n = -1 or +1), leaving flags untouched — the
// exact semantics the adapter pins. ok is true when the instruction is
// st.b.pin (committed or handed back); every other id returns ok == false so
// the caller falls through to the shared safeDual/model path.
//
// A tagged base or source register is handed back (BatchAbstract) so Python
// re-derives the identical consumed() Blocked; a strict-memory fault on the
// byte write is handed back (BatchMemFault) so Python re-derives the identical
// unmapped/readonly/uninitialized Blocked. The raw core (dmem == nil) keeps the
// upstream deny-set semantics (BatchUnsafe), which the differential tests pin.
func (c *CPU) runPinByteStore(core *Core, im *insnMeta, raw map[string]uint32, pc uint32, size uint32, insnBytes []byte) (SegmentStep, int, error, bool) {
	if im.id != "st.b.pin" {
		return SegmentStep{}, 0, nil, false
	}
	if c.dmem == nil {
		return SegmentStep{}, BatchUnsafe, nil, true
	}
	op := newOperands(im, raw)
	b := int(op.Num("b"))
	d := int(op.Num("d"))
	if core.RT[b] != nil || core.RT[d] != nil {
		return SegmentStep{}, BatchAbstract, nil, true
	}
	addr := core.R[b]
	n := op.Num("n") // uimm, scale -2 add 1: +1 or -1
	byteVal := byte(core.R[d])
	if err := c.dmem.Write(addr, []byte{byteVal}); err != nil {
		return SegmentStep{}, BatchMemFault, err, true
	}
	core.R[b] = uint32(int64(addr)+n) & wordMask
	core.PC = pc + size
	core.Steps++
	return SegmentStep{
		PC:       pc,
		NextPC:   core.PC,
		Flags:    uint8(core.Flags & 0xF),
		R:        core.R,
		BeforeSP: core.SP,
		SP:       core.SP,
		Insn:     insnBytes,
		Adapter:  "exact_postincrement_store",
		Mem:      []MemWrite{{Addr: addr, Bytes: []byte{byteVal}}},
	}, BatchOK, nil, true
}

// runPinWordStore executes the standalone short-form word postincrement store
// st.w.pin (syntax [{b}++={n}] = {d}) the dual loop runs byte-for-byte against
// Python's immediate_rep_projection short-form gate (exact_postincrement_store
// adapter, probe_boot step_body): it writes the source register's full word to
// the old base address, then writes the base register back to base + n (n = +4
// or -4, the signed increment from the n field), leaving flags untouched. ok is
// true when the instruction is st.w.pin (committed or handed back); every other
// id returns ok == false so the caller falls through to the shared
// safeDual/model path.
//
// A tagged base or source register is handed back (BatchAbstract) so Python
// re-derives the identical consumed() Blocked; an unaligned base or a
// strict-memory fault on the word write is handed back (BatchMemFault) so
// Python re-derives the identical unaligned/unmapped/readonly Blocked. The raw
// core (dmem == nil) keeps the upstream deny-set semantics (BatchUnsafe), which
// the differential tests pin.
func (c *CPU) runPinWordStore(core *Core, im *insnMeta, raw map[string]uint32, pc uint32, size uint32, insnBytes []byte) (SegmentStep, int, error, bool) {
	if im.id != "st.w.pin" {
		return SegmentStep{}, 0, nil, false
	}
	if c.dmem == nil {
		return SegmentStep{}, BatchUnsafe, nil, true
	}
	op := newOperands(im, raw)
	b := int(op.Num("b"))
	d := int(op.Num("d"))
	if core.RT[b] != nil || core.RT[d] != nil {
		return SegmentStep{}, BatchAbstract, nil, true
	}
	addr := core.R[b]
	if addr%4 != 0 {
		return SegmentStep{}, BatchMemFault, &MemError{Kind: "unaligned_exact_postincrement_store", Access: "write", Address: addr, Size: 4}, true
	}
	n := op.Num("n") // uimm scale -8 add 4: +4 or -4
	word := core.R[d]
	b4 := []byte{byte(word), byte(word >> 8), byte(word >> 16), byte(word >> 24)}
	if err := c.dmem.Write(addr, b4); err != nil {
		return SegmentStep{}, BatchMemFault, err, true
	}
	core.R[b] = uint32(int64(addr)+n) & wordMask
	core.PC = pc + size
	core.Steps++
	return SegmentStep{
		PC:       pc,
		NextPC:   core.PC,
		Flags:    uint8(core.Flags & 0xF),
		R:        core.R,
		BeforeSP: core.SP,
		SP:       core.SP,
		Insn:     insnBytes,
		Adapter:  "exact_postincrement_store",
		Mem:      []MemWrite{{Addr: addr, Bytes: b4}},
	}, BatchOK, nil, true
}

// runPinHalfStore executes the standalone short-form halfword postincrement
// store st.h.pin (syntax h[{b}++={n}] = {d}) the dual loop runs byte-for-byte
// against Python's exact_postincrement_store adapter (probe_boot step_body's
// immediate_rep_projection + exact_subword_stores short-form gate). It writes
// the source register's low halfword to the old base address, then writes the
// base register back to base + n (n = +2 or -2), leaving flags untouched. ok is
// true when the instruction is st.h.pin; every other id returns ok == false.
//
// A tagged base or source register is handed back (BatchAbstract); an odd base
// or a strict-memory fault is handed back (BatchMemFault). The raw core
// (dmem == nil) keeps the upstream deny-set semantics (BatchUnsafe).
func (c *CPU) runPinHalfStore(core *Core, im *insnMeta, raw map[string]uint32, pc uint32, size uint32, insnBytes []byte) (SegmentStep, int, error, bool) {
	if im.id != "st.h.pin" {
		return SegmentStep{}, 0, nil, false
	}
	if c.dmem == nil {
		return SegmentStep{}, BatchUnsafe, nil, true
	}
	op := newOperands(im, raw)
	b := int(op.Num("b"))
	d := int(op.Num("d"))
	if core.RT[b] != nil || core.RT[d] != nil {
		return SegmentStep{}, BatchAbstract, nil, true
	}
	addr := core.R[b]
	if addr%2 != 0 {
		return SegmentStep{}, BatchMemFault, &MemError{Kind: "unaligned_exact_postincrement_store", Access: "write", Address: addr, Size: 2}, true
	}
	n := op.Num("n") // uimm scale -4 add 2: +2 or -2
	half := uint16(core.R[d])
	b2 := []byte{byte(half), byte(half >> 8)}
	if err := c.dmem.Write(addr, b2); err != nil {
		return SegmentStep{}, BatchMemFault, err, true
	}
	core.R[b] = uint32(int64(addr)+n) & wordMask
	core.PC = pc + size
	core.Steps++
	return SegmentStep{
		PC:       pc,
		NextPC:   core.PC,
		Flags:    uint8(core.Flags & 0xF),
		R:        core.R,
		BeforeSP: core.SP,
		SP:       core.SP,
		Insn:     insnBytes,
		Adapter:  "exact_postincrement_store",
		Mem:      []MemWrite{{Addr: addr, Bytes: b2}},
	}, BatchOK, nil, true
}

// runRepRegisterStore executes the register-count rep gates the dual loop owns
// (the startup bounded copy/clear loops and the ROM memset), plus their single-
// or two-instruction pin-store bodies with the register countdown + pc
// roll-back. The Python slow path's _post_step does the identical bookkeeping
// (decrement the count register AND Repeat.Remaining, roll pc back to
// Repeat.Start while remaining > 0, clear Repeat on the last iteration); Go
// owns it here so consecutive body stores run inside one fill.
//
// The gate set is exactly the probe_boot step_body gates this cut migrates:
//   - ROM memset 0x4242 (register 4) / 0x424c (register 2), raw n==0, body is
//     one st.w.pin (0x4244) / one st.b.pin (0x424e) — rom_nonzero_rep_countdown
//     or rom_zero_rep_skip (count==0).
//   - Startup bounded rep 0x06000130 (body_size 2, single st.w.pin) and
//     0x0600014a / 0x06000166 / 0x06000182 (body_size 4, a ld.w.pin + st.w.pin
//     copy pair) — bounded_startup_rep_controller or zero_rep_skip (count==0,
//     0x06000182 only).
//
// ok is true when the instruction is one of these gates or a body instruction
// (committed or handed back). The first body instruction of the copy-loop form
// (ld.w.pin) returns ok == false so it falls through to the shared safeDual /
// model path (a plain postincrement load with no countdown); every other id
// returns ok == false so the caller falls through. A register-count rep at any
// other pc, or a body shape this cut does not own, is handed back (BatchUnsafe)
// so Python re-derives the identical gate. The raw core (dmem == nil) keeps the
// deny-set semantics (BatchUnsafe).
func (c *CPU) runRepRegisterStore(core *Core, im *insnMeta, raw map[string]uint32, pc uint32, size uint32, insnBytes []byte) (SegmentStep, int, error, bool) {
	// Mid-repeat with a register count: the current instruction is a body
	// instruction of a repeat this handler owns.
	if core.Repeat != nil && core.Repeat.Register >= 0 {
		if c.dmem == nil {
			return SegmentStep{}, BatchUnsafe, nil, true
		}
		switch im.id {
		case "st.w.pin", "st.b.pin":
			// Last body instruction: the store, then the register countdown.
			var s SegmentStep
			var code int
			var err error
			var ok bool
			if im.id == "st.w.pin" {
				s, code, err, ok = c.runPinWordStore(core, im, raw, pc, size, insnBytes)
			} else {
				s, code, err, ok = c.runPinByteStore(core, im, raw, pc, size, insnBytes)
			}
			if !ok {
				return SegmentStep{}, BatchUnsafe, nil, true
			}
			if code != BatchOK {
				return s, code, err, true
			}
			// Register countdown + pc roll-back (mirrors Python _post_step).
			reg := core.Repeat.Register
			core.R[reg] = core.R[reg] - 1
			core.Repeat.Remaining--
			if core.Repeat.Remaining > 0 {
				core.PC = core.Repeat.Start
				s.NextPC = core.PC
			} else {
				core.Repeat = nil
			}
			s.R = core.R
			return s, BatchOK, nil, true
		case "ld.w.pin":
			// First body instruction of the startup copy-loop form: a plain
			// postincrement load executed by the shared safeDual/model path
			// (no countdown — the countdown fires when the store reaches End).
			return SegmentStep{}, 0, nil, false
		default:
			return SegmentStep{}, BatchUnsafe, nil, true
		}
	}

	// The register-count rep gate (only when no repeat is in flight).
	if im.id != "rep" {
		return SegmentStep{}, 0, nil, false
	}
	if c.dmem == nil {
		return SegmentStep{}, BatchUnsafe, nil, true
	}
	if core.Repeat != nil {
		// A nested rep (or a rep while an immediate rep.i is in flight) stays
		// Python — Python's gate requires cpu.repeat is None.
		return SegmentStep{}, BatchUnsafe, nil, true
	}
	op := newOperands(im, raw)
	r := int(op.Num("r"))

	// ROM memset: 0x4242 (register 4) / 0x424c (register 2), single-store body.
	if pc == 0x4242 || pc == 0x424c {
		reg := 4
		if pc == 0x424c {
			reg = 2
		}
		if raw["n"] != 0 || r != reg {
			return SegmentStep{}, BatchUnsafe, nil, true
		}
		if core.RT[reg] != nil {
			return SegmentStep{}, BatchAbstract, nil, true
		}
		count := core.R[reg]
		if count == 0 {
			// rom_zero_rep_skip: the 2-byte body never executes.
			core.PC = pc + size + 2
			core.Steps++
			return SegmentStep{
				PC:       pc,
				NextPC:   core.PC,
				Flags:    uint8(core.Flags & 0xF),
				R:        core.R,
				BeforeSP: core.SP,
				SP:       core.SP,
				Insn:     insnBytes,
				Adapter:  "rom_zero_rep_skip_hypothesis",
			}, BatchOK, nil, true
		}
		if count > 100000 {
			return SegmentStep{}, BatchUnsafe, nil, true
		}
		core.Repeat = &RepeatState{Start: pc + size, End: pc + size + 2, Register: reg, Remaining: count}
		core.PC = pc + size
		core.Steps++
		return SegmentStep{
			PC:       pc,
			NextPC:   core.PC,
			Flags:    uint8(core.Flags & 0xF),
			R:        core.R,
			BeforeSP: core.SP,
			SP:       core.SP,
			Insn:     insnBytes,
			Adapter:  "rom_nonzero_rep_countdown_hypothesis",
		}, BatchOK, nil, true
	}

	// Startup bounded rep.
	if pc == 0x06000130 || pc == 0x0600014a || pc == 0x06000166 || pc == 0x06000182 {
		bodySize := uint32(4)
		if pc == 0x06000130 {
			bodySize = 2
		}
		if r != 2 || op.Num("n") != int64(bodySize) {
			return SegmentStep{}, BatchUnsafe, nil, true
		}
		if core.RT[2] != nil {
			return SegmentStep{}, BatchAbstract, nil, true
		}
		count := core.R[2]
		if count == 0 {
			if pc != 0x06000182 {
				// Zero count at a non-skip site: Python raises Blocked.
				return SegmentStep{}, BatchUnsafe, nil, true
			}
			// zero_rep_skip: the empty PSRAM copy body never executes.
			core.PC = pc + size + bodySize
			core.Steps++
			return SegmentStep{
				PC:       pc,
				NextPC:   core.PC,
				Flags:    uint8(core.Flags & 0xF),
				R:        core.R,
				BeforeSP: core.SP,
				SP:       core.SP,
				Insn:     insnBytes,
				Adapter:  "zero_rep_skip_hypothesis",
			}, BatchOK, nil, true
		}
		if count > 10000 {
			return SegmentStep{}, BatchUnsafe, nil, true
		}
		core.Repeat = &RepeatState{Start: pc + size, End: pc + size + bodySize, Register: 2, Remaining: count}
		core.PC = pc + size
		core.Steps++
		return SegmentStep{
			PC:       pc,
			NextPC:   core.PC,
			Flags:    uint8(core.Flags & 0xF),
			R:        core.R,
			BeforeSP: core.SP,
			SP:       core.SP,
			Insn:     insnBytes,
			Adapter:  "bounded_startup_rep_controller",
		}, BatchOK, nil, true
	}

	// A register-count rep at any other pc stays Python.
	return SegmentStep{}, BatchUnsafe, nil, true
}

// runRepImmediateSingleStore executes the immediate_rep_single_store_projection
// gate (probe_boot step_body's immediate_rep_projection short-form gate) inside
// the dual loop: a rep.i whose body is a single short-form postincrement store
// (st.w.pin / st.h.pin / st.b.pin). The rep.i itself sets core.Repeat and
// advances pc to the body; each body-store step executes the store and then does
// the repeat countdown + pc roll-back (register is -1 = immediate rep.i, so the
// countdown decrements Repeat.Remaining and rolls pc back to Repeat.Start while
// remaining > 0, clearing Repeat on the last iteration). The Python slow path's
// _post_step does the identical bookkeeping; Go owns it here so consecutive body
// stores run inside one fill.
//
// ok is true when the instruction is rep.i (gate) or the repeat body store
// (committed or handed back); every other id returns ok == false so the caller
// falls through to the shared safeDual/model path. A rep.i whose body is not one
// of the three supported single stores is handed back (BatchUnsafe) so Python
// re-derives the identical gate (a multi-block / unknown-body rep.i must not be
// silently re-shaped). The raw core (dmem == nil) keeps the deny-set semantics.
func (c *CPU) runRepImmediateSingleStore(core *Core, im *insnMeta, raw map[string]uint32, pc uint32, size uint32, insnBytes []byte) (SegmentStep, int, error, bool) {
	// Mid-repeat: the current instruction must be the repeat body store.
	if core.Repeat != nil {
		if c.dmem == nil {
			return SegmentStep{}, BatchUnsafe, nil, true
		}
		if core.Repeat.Register >= 0 {
			// A register-count repeat is handled by runRepRegisterStore, which
			// runs before this handler. Its first body instruction (ld.w.pin of
			// the startup copy-loop form) falls through here to the shared
			// safeDual/model path with no countdown.
			return SegmentStep{}, 0, nil, false
		}
		// An MMIO target is handed back before the bus absorbs the side effect
		// (Python's peripheral model owns MMIO writes), mirroring the standalone
		// postincrement-store head check the dual loop relies on.
		if im.id == "st.w.pin" || im.id == "st.h.pin" || im.id == "st.b.pin" {
			op := newOperands(im, raw)
			b := int(op.Num("b"))
			if core.RT[b] == nil {
				addr := core.R[b]
				switch im.id {
				case "st.b.pin":
					if c.dmem.HandlesByteWrite(addr) {
						return SegmentStep{}, BatchUnsafe, nil, true
					}
				case "st.h.pin":
					if c.dmem.HandlesMMIO(addr, 2) {
						return SegmentStep{}, BatchUnsafe, nil, true
					}
				default: // st.w.pin
					if c.dmem.HandlesMMIO(addr, 4) {
						return SegmentStep{}, BatchUnsafe, nil, true
					}
				}
			}
		}
		var s SegmentStep
		var code int
		var err error
		var ok bool
		switch im.id {
		case "st.w.pin":
			s, code, err, ok = c.runPinWordStore(core, im, raw, pc, size, insnBytes)
		case "st.h.pin":
			s, code, err, ok = c.runPinHalfStore(core, im, raw, pc, size, insnBytes)
		case "st.b.pin":
			s, code, err, ok = c.runPinByteStore(core, im, raw, pc, size, insnBytes)
		default:
			return SegmentStep{}, BatchUnsafe, nil, true
		}
		if !ok {
			return SegmentStep{}, BatchUnsafe, nil, true
		}
		if code != BatchOK {
			return s, code, err, true
		}
		// Store committed at body end (s.NextPC == core.PC == body end); now
		// the repeat countdown + roll-back.
		core.Repeat.Remaining--
		if core.Repeat.Remaining > 0 {
			core.PC = core.Repeat.Start
			s.NextPC = core.PC
		} else {
			core.Repeat = nil
		}
		return s, BatchOK, nil, true
	}

	// The rep.i gate.
	if im.id != "rep.i" {
		return SegmentStep{}, 0, nil, false
	}
	if c.dmem == nil {
		return SegmentStep{}, BatchUnsafe, nil, true
	}
	if core.AfterThen != nil {
		return SegmentStep{}, BatchUnsafe, nil, true
	}
	op := newOperands(im, raw)
	n := op.Num("n")   // count, 1..32
	bodyBytes := op.Num("b")
	if n < 1 || n > 32 {
		return SegmentStep{}, BatchUnsafe, nil, true
	}
	bIm, _, bSize, err := c.decode(pc + uint32(size))
	if err != nil {
		if isMemError(err) {
			return SegmentStep{}, BatchMemFault, err, true
		}
		return SegmentStep{}, BatchNoInstr, err, true
	}
	if int64(bSize) != bodyBytes {
		return SegmentStep{}, BatchUnsafe, nil, true
	}
	switch bIm.id {
	case "st.w.pin", "st.h.pin", "st.b.pin":
	default:
		return SegmentStep{}, BatchUnsafe, nil, true
	}
	core.Repeat = &RepeatState{
		Start:     pc + uint32(size),
		End:       pc + uint32(size) + uint32(bodyBytes),
		Register:  -1,
		Remaining: uint32(n),
	}
	core.PC = pc + uint32(size)
	core.Steps++
	return SegmentStep{
		PC:       pc,
		NextPC:   core.PC,
		Flags:    uint8(core.Flags & 0xF),
		R:        core.R,
		BeforeSP: core.SP,
		SP:       core.SP,
		Insn:     insnBytes,
		Adapter:  "immediate_rep_single_store_projection",
	}, BatchOK, nil, true
}

// runExactSubwordStore executes the standalone byte/halfword offset stores the
// dual loop runs byte-for-byte against Python's exact_subword_stores adapter
// (probe_boot step_body's exact_subword_stores gate): address and value are
// masked to 32 bits, the store writes exactly width bytes (1/2) with no
// merge-read, and flags stay untouched. ok is true when the instruction is one
// of the subword-store ids (committed or handed back); every other id returns
// ok == false so the caller falls through to safeDual/model.
//
// A tagged address/value/index operand is handed back (BatchAbstract) so Python
// re-derives the identical consumed() Blocked; an odd halfword address is
// handed back (BatchMemFault) so Python re-derives the identical "unaligned
// exact subword store" Blocked. The raw core (dmem == nil) keeps the upstream
// deny-set semantics (BatchUnsafe), which the differential tests pin.
func (c *CPU) runExactSubwordStore(core *Core, im *insnMeta, raw map[string]uint32, pc uint32, size uint32, insnBytes []byte) (SegmentStep, int, error, bool) {
	id := im.id
	if !dualSubwordStoreAllowed[id] {
		return SegmentStep{}, 0, nil, false
	}
	if c.dmem == nil {
		return SegmentStep{}, BatchUnsafe, nil, true
	}
	op := newOperands(im, raw)
	width := 1
	if strings.HasPrefix(id, "st.h") {
		width = 2
	}
	var addr uint32
	baseReg, idxReg, valReg := -1, -1, -1
	switch id {
	case "st.h.idxs":
		baseReg, idxReg, valReg = int(op.Num("r")), int(op.Num("s")), int(op.Num("t"))
		addr = core.R[baseReg] + (core.R[idxReg] << 1)
	case "st.b.sp.l", "st.h.sp.l":
		if !core.SPKnown {
			return SegmentStep{}, BatchAbstract, nil, true
		}
		valReg = int(op.Num("r"))
		addr = core.SP + uint32(op.Num("n"))
	case "st.b.l", "st.h.l":
		baseReg, valReg = int(op.Num("r")), int(op.Num("s"))
		addr = core.R[baseReg] + uint32(op.Num("n"))
	default: // st.b, st.h
		baseReg, valReg = int(op.Num("b")), int(op.Num("d"))
		addr = core.R[baseReg] + uint32(op.Num("n"))
	}
	addr &= wordMask
	if baseReg >= 0 && core.RT[baseReg] != nil {
		return SegmentStep{}, BatchAbstract, nil, true
	}
	if idxReg >= 0 && core.RT[idxReg] != nil {
		return SegmentStep{}, BatchAbstract, nil, true
	}
	if valReg >= 0 && core.RT[valReg] != nil {
		return SegmentStep{}, BatchAbstract, nil, true
	}
	if addr%uint32(width) != 0 {
		return SegmentStep{}, BatchMemFault, &MemError{Kind: "unaligned_exact_subword_store", Access: "write", Address: addr, Size: width}, true
	}
	b := make([]byte, width)
	for i := 0; i < width; i++ {
		b[i] = byte(core.R[valReg] >> (8 * i))
	}
	if err := c.dmem.Write(addr, b); err != nil {
		return SegmentStep{}, BatchMemFault, err, true
	}
	core.PC = pc + size
	core.Steps++
	return SegmentStep{
		PC:       pc,
		NextPC:   core.PC,
		Flags:    uint8(core.Flags & 0xF),
		R:        core.R,
		BeforeSP: core.SP,
		SP:       core.SP,
		Insn:     insnBytes,
		Adapter:  "exact_subword_store",
		Mem:      []MemWrite{{Addr: addr, Bytes: b}},
	}, BatchOK, nil, true
}
