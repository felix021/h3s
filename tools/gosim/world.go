package gosim

// Whole-loop executor skeleton + the two-exit hand-back protocol. M3 narrows the
// M1 "always yield" predicate: with the Timer0 + IRQController models ported
// (periph.go), the loop-level advance phase can now be statically proven empty
// for the "no timer counting" configuration, so RunLoop genuinely closes the
// execute phase (reusing the stage-1/2 segment contract) + empty advance inside
// Go. Every configuration where that proof does not hold still yields before the
// first attempt — nothing is fabricated.
//
// See docs/gosim-wholeloop-design.md for the full design, the execution-loop
// semantics, and the YIELD_BEFORE_ATTEMPT vs STOP exit protocol.

import "fmt"

// LoopExit distinguishes the two honest hand-back exits of a whole-loop run.
//
//	YieldBeforeAttempt: the current attempt was never started — the loop-level
//	  precondition (single core, concrete integer state, no observer, no
//	  repeat/if-block, immutable fetch window, no after_step hook, no peripheral
//	  advance to run) could not be proven, so Go hands the whole attempt back to
//	  Python with the world untouched. The committed prefix is empty.
//	Stop: Go executed a prefix of the loop and then hit a hazard it cannot model
//	  (a step outside the segment contract, fetch outside the window, a missing
//	  model). The committed prefix is returned with its per-step observations;
//	  the hazard pc is reported and Python resumes there.
type LoopExit int

const (
	YieldBeforeAttempt LoopExit = iota
	Stop
)

// Core is one pi32v2 CPU core's architectural state. It mirrors the Python
// CPU plus the machine-side per-core bookkeeping fields that after_step and the
// delivery projections read (see br28_machine.py launch/select).
type Core struct {
	ID           int
	R            [16]uint32
	Flags        uint32
	SP           uint32
	SPKnown      bool
	RETS         uint32
	PC           uint32
	ICFG         uint32
	ICFGKnown    bool
	Steps        uint64
	RepeatActive bool // repeat / after_then pending (yield before attempt)
}

// IrqReq is a pending interrupt delivery request (mirrors Python's
// IrqDeliveryRequest). Unused until the delivery-detection milestones (M5/M6);
// defined now for the contract.
type IrqReq struct {
	Core int
	Irq  uint32
}

// Ledger is the run-level bookkeeping the loop appends to (trace rows, memory
// events, adapters, milestones, step counts). It is the Go twin of the Python
// explore-ledger namespaces.
type Ledger struct {
	CompletedSteps uint64
	Trace          []LoopStep
	Events         []LoopEvent
	Milestones     []LoopEvent
}

// LoopStep is one committed attempt's observation: pc before/after, flags and
// the 16 registers after, and any memory writes the step produced (always empty
// under the side-effect-free stage-1/2 contract).
type LoopStep struct {
	PC     uint32
	NextPC uint32
	Flags  uint8
	R      [16]uint32
	Mem    []MemWrite
}

// LoopEvent is a generic ledger event (memory event, milestone, adapter hit).
type LoopEvent struct {
	Kind string
	PC   uint32
	Core int
}

// World is the complete world state: both cores, the execute engine, the strict
// memory, the peripheral model, and the ledger. Go holds this; Python
// constructs it, drives it, and reads back the ledger.
type World struct {
	Cores  [2]*Core
	Exec   *CPU    // decode+execute engine (nil = cannot run)
	Mem    *Memory // strict whole-loop memory (M2)
	Periph *PeriphState
	Ledger Ledger

	// FetchLo/FetchHi is the immutable fetch window the execute phase must stay
	// inside (the ROM or flash app window, caller-supplied).
	FetchLo, FetchHi uint32

	// Loop-level preconditions the caller (Python) asserts. Single-core is
	// derived structurally (Cores[1] == nil); the rest are explicit, never
	// guessed.
	Concrete       bool // pc / R[16] / icfg all concrete integers
	ObserverActive bool // a Python-side observer hook is armed
	AfterStepNoop  bool // after_step provably has no hook at this pc (M4+)
}

// LoopResult is the outcome of one RunLoop call.
type LoopResult struct {
	Exit   LoopExit
	StopPC uint32
	Irq    *IrqReq
	Reason string // why the loop stopped or yielded (honest, never a guess)
	Steps  []LoopStep
}

// yieldBeforeAttempt builds an empty-prefix result for a failed precondition.
func yieldBeforeAttempt(reason string) LoopResult {
	return LoopResult{Exit: YieldBeforeAttempt, Reason: reason}
}

// RunLoop executes up to n whole-loop attempts (select → fetch guard → decode →
// execute → after_step → advance → trace) inside Go.
//
// M3 scope: it only ran when the advance phase was statically empty (no timer
// counting). M4 extends the advance judgment across the ported counting models
// (core tick / watchdog / ICU / Timer2 / software IRQ): when a counting model
// is active but no delivery will be thrown, the loop now interleaves one
// execute step with one advance step inside Go instead of yielding; when the
// advance detects a pending-and-eligible IRQ it returns Stop with the IrqReq so
// Python executes the delivery projection; when the advance hits a strict stop
// (e.g. watchdog expiry, unmodeled delivery) it returns Stop with the reason.
// The after_step phase is still asserted no-op (its hooks stay in Python until
// the M4+ anchor proof lands).
func (w *World) RunLoop(n int) LoopResult {
	if w == nil {
		return yieldBeforeAttempt("nil world")
	}
	if w.Cores[0] == nil {
		return yieldBeforeAttempt("no core 0 present")
	}
	if w.Cores[1] != nil {
		return yieldBeforeAttempt("dual-core select not ported (M6)")
	}
	if !w.Concrete {
		return yieldBeforeAttempt("non-concrete (abstract) cpu state")
	}
	if w.ObserverActive {
		return yieldBeforeAttempt("observer hook active")
	}
	if w.Cores[0].RepeatActive {
		return yieldBeforeAttempt("repeat/after_then active")
	}
	if !w.AfterStepNoop {
		return yieldBeforeAttempt("after_step hook not provably no-op (M4+)")
	}
	if w.Exec == nil {
		return yieldBeforeAttempt("no execute engine bound")
	}
	if w.Periph == nil {
		return yieldBeforeAttempt("no peripheral model bound")
	}
	if w.Periph.OtherAdvanceActive {
		return yieldBeforeAttempt("unported advance-chain model active (SRC-SYNC/ADC-DMA, M5)")
	}

	// Fast path: the advance phase is provably empty (no counting/pending
	// model). Run the execute segment in one batch and skip the advance.
	if !w.Periph.HasActiveAdvance() {
		return w.runEmptyAdvance(n)
	}

	// Counting/pending models are active: interleave one execute step with one
	// advance step, stopping at the first delivery request or strict stop.
	res := LoopResult{Exit: Stop}
	icfg := w.Cores[0].ICFG
	for i := 0; i < n; i++ {
		seg := w.Exec.RunSegment(1, w.FetchLo, w.FetchHi)
		if len(seg.Steps) > 0 {
			s := seg.Steps[0]
			res.Steps = append(res.Steps, LoopStep{
				PC:     s.PC,
				NextPC: s.NextPC,
				Flags:  s.Flags,
				R:      s.R,
				Mem:    nil,
			})
		}
		if seg.StopCode != BatchOK {
			res.StopPC = seg.StopPC
			res.Reason = segmentStopReason(seg.StopCode)
			return res
		}
		adv := w.Periph.Advance(icfg)
		if adv.Irq != nil {
			res.Irq = adv.Irq
			res.StopPC = currentPC(res)
			res.Reason = fmt.Sprintf("pending eligible irq %d on core %d (delivery handed back to Python)", adv.Irq.Irq, adv.Irq.Core)
			return res
		}
		if adv.Err != nil {
			res.StopPC = currentPC(res)
			res.Reason = "advance strict stop: " + adv.Err.JSON()
			return res
		}
	}
	res.StopPC = 0
	res.Reason = fmt.Sprintf("ran %d attempts with interleaved advance (no delivery)", len(res.Steps))
	return res
}

// runEmptyAdvance is the M3 fast path: execute a segment batch and skip the
// provably-empty advance.
func (w *World) runEmptyAdvance(n int) LoopResult {
	seg := w.Exec.RunSegment(n, w.FetchLo, w.FetchHi)
	res := LoopResult{Exit: Stop, StopPC: seg.StopPC}
	for _, s := range seg.Steps {
		res.Steps = append(res.Steps, LoopStep{
			PC:     s.PC,
			NextPC: s.NextPC,
			Flags:  s.Flags,
			R:      s.R,
			Mem:    nil, // structurally impossible under the segment contract
		})
	}
	switch seg.StopCode {
	case BatchOK:
		res.StopPC = 0
		res.Reason = fmt.Sprintf("ran %d attempts; advance and after_step provably empty", len(res.Steps))
	default:
		res.Reason = segmentStopReason(seg.StopCode)
	}
	return res
}

func segmentStopReason(code int) string {
	switch code {
	case BatchFetch:
		return "fetch pc outside immutable window"
	case BatchUnsafe:
		return "instruction outside the stage-1/2 ALU contract"
	case BatchNoInstr:
		return "no instruction decodes at fetch pc"
	case BatchNoModel:
		return "decoded instruction has no model"
	default:
		return "unexpected error while stepping"
	}
}

func currentPC(res LoopResult) uint32 {
	if len(res.Steps) == 0 {
		return 0
	}
	return res.Steps[len(res.Steps)-1].NextPC
}
