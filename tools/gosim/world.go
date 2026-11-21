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

	// M6C special registers + execution context (br28_machine deliver_*
	// projections). A nil pointer is the Python None (unknown); an empty
	// ActiveContext string is the Python None active_context.
	USP              *uint32               // user stack pointer (nil = unknown)
	SSP              *uint32               // kernel stack pointer (nil = unknown)
	RETI             *uint32               // return-from-interrupt pc (nil = unknown)
	IRQPSR           *uint32               // entry PSR snapshot (nil = unknown)
	ActiveContext    string                // "" = None (thread mode)
	BootstrapContext bool                  // cpu.bootstrap_context
	LastExplicitSSP  *uint32               // cpu.last_explicit_ssp (nil = unknown)
	AudioIrqOuter    *AudioIrqOuterContext // audio IRQ68 nested outer context (nil = None)
}

// AudioIrqOuterContext mirrors the cpu.audio_irq_outer_context dict saved when
// IRQ68 nests into the Timer0 handler (active_context / reti / sp). RETI is a
// pointer so it can round-trip the Python None (unknown) reti.
type AudioIrqOuterContext struct {
	ActiveContext string
	RETI          *uint32
	SP            uint32
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
	Concrete bool // pc / R[16] / icfg all concrete integers
	// AfterStep is the after_step hook configuration plus the first-task
	// tracking state. nil = no hooks configured, so after_step is provably
	// empty for every pc; a non-nil config is judged per-pc by CanProveNoop
	// (M6B) instead of a blanket flag.
	AfterStep *AfterStep
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
// counting). M4/M5 extends the advance judgment across the full ported
// advance-chain model set (core tick / watchdog / ICU / Timer2 / software IRQ /
// SRC-SYNC / ADC-DMA): when a counting model is active but no delivery will be
// thrown, the loop now interleaves one execute step with one advance step
// inside Go instead of yielding; when the advance detects a pending-and-eligible
// IRQ it returns Stop with the IrqReq so Python executes the delivery
// projection; when the advance hits a strict stop (e.g. watchdog expiry,
// unmodeled delivery) it returns Stop with the reason. The after_step phase is
// still asserted no-op (its hooks stay in Python until the anchor proof lands),
// and the dual-core select stays in Python (M6).
func (w *World) RunLoop(n int) LoopResult {
	if w == nil {
		return yieldBeforeAttempt("nil world")
	}
	if w.Cores[0] == nil {
		return yieldBeforeAttempt("no core 0 present")
	}
	if w.Cores[1] != nil {
		// Stage A ported the select phase (machine.go Machine.Select), Stage B
		// the after_step no-op judgment (after_step.go), Stage C the deliver_*
		// projections (deliver.go, differential-tested). The dual-core loop
		// still cannot close because these three pieces are not yet wired into
		// one RunLoop (the select/deliver state lives in the standalone Machine
		// / DeliverMachine, not World), and the dual-core advance scan is
		// single-core only. That wiring is the remaining integration step.
		return yieldBeforeAttempt("dual-core loop not closed: select (M6A), after_step (M6B) and deliver projections (M6C) are ported and differential-tested, but the whole-loop wiring is not done")
	}
	if !w.Concrete {
		return yieldBeforeAttempt("non-concrete (abstract) cpu state")
	}
	if w.AfterStep != nil && w.AfterStep.ObserverActive {
		return yieldBeforeAttempt("observer hook active")
	}
	if w.Cores[0].RepeatActive {
		return yieldBeforeAttempt("repeat/after_then active")
	}
	if w.Exec == nil {
		return yieldBeforeAttempt("no execute engine bound")
	}
	if w.Periph == nil {
		return yieldBeforeAttempt("no peripheral model bound")
	}

	// after_step: an armed hook means the loop must judge each committed step's
	// before/next pc (CanProveNoop) instead of trusting a blanket flag. An
	// empty hook set + empty advance keeps the M3-M5 batch fast path.
	afterStepArmed := w.AfterStep != nil && w.AfterStep.HasArmedHook()

	// Fast path: advance empty and no after_step hook armed. Run the execute
	// segment in one batch and skip both the advance and the per-step after_step
	// judgment (both provably empty for every pc).
	if !w.Periph.HasActiveAdvance() && !afterStepArmed {
		return w.runEmptyAdvance(n)
	}

	// Interleaved: one execute step, then the after_step judgment, then the
	// advance (when active), stopping at the first after_step hook, delivery
	// request, or strict stop.
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
		if afterStepArmed {
			s := res.Steps[len(res.Steps)-1]
			if ok, why := w.AfterStep.CanProveNoop(0, s.PC, s.NextPC); !ok {
				res.StopPC = s.NextPC
				res.Reason = "after_step hook fires: " + why + " (handed back to Python)"
				return res
			}
		}
		if !w.Periph.HasActiveAdvance() {
			continue
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
	res.Reason = fmt.Sprintf("ran %d attempts with interleaved after_step/advance judgment (no hazard)", len(res.Steps))
	return res
}

// runEmptyAdvance is the M3 fast path: execute a segment batch and skip the
// provably-empty advance and after_step (both are provably no-op for every pc
// in this configuration).
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
