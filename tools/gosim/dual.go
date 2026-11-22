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
	"ld.bu.idx": true, "ld.hu.idx": true, "ld.bu.pi": true, "ld.hu.pi": true,
	"ld.bu.pin": true, "ld.hu.pin": true, "ld.bu.pre": true, "ld.hu.pre": true,
	"ld.bu.prer": true, "ld.hu.prer": true,

	"st.w": true, "st.w.l": true, "st.w.modimm": true,
	"st.w.idxs": true, "st.w.sp": true, "st.w.idx": true,
}

// dualStoreAdapter maps the store ids the commit must count as Python's
// exact_full_word_store adapter ("" = no adapter to count).
var dualStoreAdapter = map[string]bool{
	"st.w": true, "st.w.l": true, "st.w.modimm": true,
	"st.w.idxs": true, "st.w.sp": true, "st.w.idx": true,
}

// safeDual reports whether the dual loop can execute the decoded instruction
// exactly. It is the stage-1 safeALU contract relaxed with dualAllowed. Without
// a strict memory bound (dmem == nil) the loop degrades to the pure segment
// contract (safeALU), which the differential test test_dualcore_consistency.py
// pins on the zero-handle construction.
func (c *CPU) safeDual(im *insnMeta) bool {
	if c.dmem == nil {
		return c.safeALU(im)
	}
	if im.blockThen != "" || im.body != "" {
		return false
	}
	if strings.HasSuffix(im.id, ".p") {
		return false
	}
	if dualAllowed[im.id] {
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

// RunDualStep executes one whole-loop step on c (strict fetch + dual contract)
// and returns the observation plus a stop code, stop pc, and the underlying
// error detail (nil on success). On BatchOK the SegmentStep carries the executed
// instruction bytes, this step's ordered writes, and the adapter the Python
// commit must count. A strict-memory fault (unmapped/uninitialized/unaligned) is
// reported as BatchMemFault without committing the step; Python re-runs the
// instruction and re-derives the identical Blocked (or MMIO side effect).
func (c *CPU) RunDualStep() (SegmentStep, int, uint32, error) {
	pc := c.pc
	im, _, _, err := c.decode(pc)
	if err != nil {
		if isMemError(err) {
			return SegmentStep{}, BatchMemFault, pc, err
		}
		return SegmentStep{}, BatchNoInstr, pc, err
	}
	if !c.safeDual(im) {
		return SegmentStep{}, BatchUnsafe, pc, nil
	}
	if c.models[im.id] == nil {
		return SegmentStep{}, BatchNoModel, pc, nil
	}
	insnBytes, err := c.InsnBytes(pc)
	if err != nil {
		if isMemError(err) {
			return SegmentStep{}, BatchMemFault, pc, err
		}
		return SegmentStep{}, BatchNoInstr, pc, err
	}
	if err := c.Step(); err != nil {
		if isMemError(err) {
			return SegmentStep{}, BatchMemFault, pc, err
		}
		return SegmentStep{}, BatchOther, pc, err
	}
	adapter := ""
	if dualStoreAdapter[im.id] {
		adapter = "exact_full_word_store"
	}
	return SegmentStep{
		PC:      pc,
		NextPC:  c.pc,
		Flags:   uint8(c.flags & 0xF),
		R:       c.R,
		Mem:     c.TakeWrites(),
		Insn:    insnBytes,
		Adapter: adapter,
	}, BatchOK, 0, nil
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
