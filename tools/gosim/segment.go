package gosim

// Stage-1 segment batch: run a pre-qualified stretch of instructions inside one
// Go call and return the per-step observations, instead of crossing the cgo
// boundary once per instruction. This is the skeleton of the "whole-loop-to-Go"
// engine, not yet the fast path.
//
// Segment contract (byte-for-byte parity is proven by test_batch_consistency.py
// against the Python reference, and the Python side pre-qualifies segments with
// the same predicate):
//   * single core, sequential scalar ALU only — no branch/jump/return, no
//     if-block opener, no rep, no .p parallel pair, no memory/stack/cache, no
//     special register (sr field), no sp/bank move;
//   * fetch pc must stay inside the caller-supplied [fetchLo, fetchHi) window
//     (the immutable ROM/flash region in the real frontier);
//   * every step therefore changes only pc (+size), flags and R[0..15]; memory
//     writes are structurally impossible, so the per-step mem-write list is
//     always empty here.
//
// Honest-stop property: the batch never fabricates a value or skips a hazard.
// The first step that violates the contract (unsafe instruction, decode
// failure, missing model, fetch outside the window, or an unexpected error)
// ends the batch: everything executed before it is returned as the prefix, and
// StopCode/StopPC describe exactly where and why it stopped.

import (
	"encoding/binary"
	"strings"
)

// Batch stop codes (mirrored in tools/gosim/gosim_batch.py).
const (
	BatchOK      = 0 // ran all n steps
	BatchUnsafe  = 1 // next instruction is outside the stage-1 ALU contract
	BatchNoInstr = 2 // no instruction decodes at the fetch pc
	BatchNoModel = 3 // decoded instruction has no model
	BatchOther   = 4 // unexpected error while stepping
	BatchFetch   = 5 // fetch pc outside [fetchLo, fetchHi)
	BatchMemFault = 6 // strict-memory fault while executing (hand back to Python)
)

// MemWrite is one whole-word store observed during a step.
type MemWrite struct {
	Addr  uint32
	Value uint32
}

// SegmentStep is the per-step observation: pc before the step, pc after it,
// flags after it, the 16 registers after it, and this step's memory writes
// (always empty under the stage-1 contract).
type SegmentStep struct {
	PC     uint32
	NextPC uint32
	Flags  uint8
	R      [16]uint32
	Mem    []MemWrite

	// Insn is the executed instruction's bytes (whole-loop path only; the
	// stage-1 serialization ignores it). Adapter names the Python instruction
	// adapter the commit must count ("" when the step needs none).
	Insn    []byte
	Adapter string
}

// SegmentResult is the whole batch outcome. The executed-step count is implied
// by len(Steps); the serializer derives it, so an early (honest) stop can never
// leave a stale/zero count in the header.
type SegmentResult struct {
	StopCode int
	StopPC   uint32
	Steps    []SegmentStep
}

// safeALU reports whether the decoded instruction belongs to the segment
// contract. Stage 2 relaxes the contract from sequential ALU to include
// unconditional branches (goto.s/goto.l) and flag conditional branches
// (bc.*), which the Go core already executes via the step1 control-flow
// (tgt + model exit). blockThen/body (if-blocks / rep) and .p parallel pairs
// remain structural rejects; batchUnsafe adds the memory/stack/cache/control/
// dsp/sr/sp-bank families plus the still-unrelaxed call/rts/register-compare
// branches.
func (c *CPU) safeALU(im *insnMeta) bool {
	if im.blockThen != "" || im.body != "" {
		return false
	}
	if strings.HasSuffix(im.id, ".p") {
		return false
	}
	if batchUnsafe[im.id] {
		return false
	}
	return true
}

// RunSegment executes up to n contract-compliant steps and returns the
// per-step observations plus an honest stop code for the first step that
// cannot run under the contract.
func (c *CPU) RunSegment(n int, fetchLo, fetchHi uint32) SegmentResult {
	var res SegmentResult
	for i := 0; i < n; i++ {
		pc := c.pc
		if pc < fetchLo || pc >= fetchHi {
			res.StopCode = BatchFetch
			res.StopPC = pc
			return res
		}
		im, _, _, err := c.decode(pc)
		if err != nil {
			res.StopCode = BatchNoInstr
			res.StopPC = pc
			return res
		}
		if !c.safeALU(im) {
			res.StopCode = BatchUnsafe
			res.StopPC = pc
			return res
		}
		if c.models[im.id] == nil {
			res.StopCode = BatchNoModel
			res.StopPC = pc
			return res
		}
		if err := c.Step(); err != nil {
			res.StopCode = BatchOther
			res.StopPC = pc
			return res
		}
		res.Steps = append(res.Steps, SegmentStep{
			PC:     pc,
			NextPC: c.pc,
			Flags:  uint8(c.flags & 0xF),
			R:      c.R,
			Mem:    nil,
		})
	}
	return res
}

// segmentLayout is the exact byte protocol (all little-endian):
//   header   u32 executed, u32 stop_code, u32 stop_pc
//   per step u32 pc, u32 next_pc, u8 flags, u8 n_mem, u16 reserved,
//            16 x u32 registers, then n_mem x (u32 addr, u32 value)
const (
	segHdrSize   = 12
	segStepFixed = 76 // pc(4) next_pc(4) flags(1) n_mem(1) pad(2) r[16](64)
	segMemSize   = 8
)

// WriteSegmentBuffer serializes res into buf and returns the number of bytes
// written, or -1 when buf is too small. Exported for the cgo wrapper.
func WriteSegmentBuffer(buf []byte, res SegmentResult) int {
	need := segHdrSize
	for _, s := range res.Steps {
		need += segStepFixed + segMemSize*len(s.Mem)
	}
	if need > len(buf) {
		return -1
	}
	w := 0
	put32 := func(v uint32) {
		binary.LittleEndian.PutUint32(buf[w:], v)
		w += 4
	}
	put32(uint32(len(res.Steps)))
	put32(uint32(res.StopCode))
	put32(res.StopPC)
	for _, s := range res.Steps {
		put32(s.PC)
		put32(s.NextPC)
		buf[w] = s.Flags
		w++
		buf[w] = uint8(len(s.Mem))
		w++
		binary.LittleEndian.PutUint16(buf[w:], 0) // reserved
		w += 2
		for i := 0; i < 16; i++ {
			put32(s.R[i])
		}
		for _, m := range s.Mem {
			put32(m.Addr)
			put32(m.Value)
		}
	}
	return w
}
