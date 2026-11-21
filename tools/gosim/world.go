package gosim

// M1 whole-loop first cut: the Go-side "complete world state" skeleton and the
// two-exit hand-back protocol. This file is inert scaffolding — no peripheral
// model is ported yet, so RunLoop always yields before executing. It pins down
// the shapes the M2..M6 ports will fill in, and is the contract surface the
// Python engine (tools/gosim_batch_engine.py) will grow into.
//
// See docs/gosim-wholeloop-design.md for the full design, the execution-loop
// semantics, and the YIELD_BEFORE_ATTEMPT vs STOP exit protocol.

// LoopExit distinguishes the two honest hand-back exits of a whole-loop run.
//
//	YIELD_BEFORE_ATTEMPT: the current attempt was never started — the loop-level
//	  precondition (single core, concrete integer state, no observer, no
//	  repeat/if-block, immutable fetch window, no peripheral advance to run)
//	  could not be proven, so Go hands the whole attempt back to Python with the
//	  world untouched. The committed prefix is empty.
//	STOP: Go executed a prefix of the loop and then hit a hazard it cannot model
//	  (memory access, abstract value, IRQ, dual-core handoff, observer hook,
//	  non-trivial after_step/advance). The committed prefix is returned with its
//	  per-step observations; the hazard pc is reported and Python resumes there.
type LoopExit int

const (
	YieldBeforeAttempt LoopExit = iota
	Stop
)

// Core is one pi32v2 CPU core's architectural state. It mirrors the Python
// CPU plus the machine-side per-core bookkeeping fields that after_step and the
// delivery projections read (see br28_machine.py launch/select).
type Core struct {
	ID      int
	R       [16]uint32
	Flags   uint32
	SP      uint32
	SPKnown bool
	RETS    uint32
	PC      uint32
	ICFG    uint32
	ICFGKnown bool
	Steps   uint64
	RepeatActive bool // repeat / after_then pending (yield before attempt)
}

// Periph is the peripheral-model surface the loop's advance phase drives. In M1
// it is a placeholder interface; the ported models (Timer0, core ticks,
// watchdog, ICU, Timer2, ADC-DMA / SRC-SYNC delivery, software IRQ — see the
// design doc §advance chain) implement it in later milestones.
type Periph interface {
	// Advance runs one virtual model step. It returns nil when nothing pending,
	// or an *IrqReq / *StopReason describing a delivery request or a strict
	// stop. M1: always nil because no model is ported.
	Advance(icfg uint32) (irq *IrqReq, stop *StopReason)
}

// IrqReq is a pending interrupt delivery request (mirrors Python's
// IrqDeliveryRequest). M1: unused, defined for the contract.
type IrqReq struct {
	Core int
	Irq  uint32
}

// StopReason is a strict stop (mirrors Python's Blocked details). M1: unused.
type StopReason struct {
	Kind string
}

// Ledger is the run-level bookkeeping the loop appends to (trace rows, memory
// events, adapters, milestones, step counts). It is the Go twin of the Python
// explore-ledger namespaces; M1 only declares it.
type Ledger struct {
	CompletedSteps uint64
	Trace          []LoopStep
	Events         []LoopEvent
	Milestones     []LoopEvent
}

// LoopStep is one committed attempt's observation: pc before/after, flags and
// the 16 registers after, and any memory writes the step produced (empty under
// the M1 side-effect-free contract).
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

// World is the complete world state: both cores, the strict memory, the
// peripheral model, and the ledger. Go holds this; Python constructs it, drives
// it, and reads back the ledger.
type World struct {
	Cores  [2]*Core
	Mem    *Memory
	Periph Periph
	Ledger Ledger
}

// LoopResult is the outcome of one RunLoop call.
type LoopResult struct {
	Exit     LoopExit
	StopPC   uint32
	Irq      *IrqReq
	Reason   string // why the loop stopped or yielded (honest, never a guess)
	Steps    []LoopStep
}

// RunLoop executes up to n whole-loop attempts (select → fetch guard → decode →
// execute → after_step → advance → trace). M1: every configuration is
// conservatively yielded before the first attempt, because no peripheral model
// and no strict-memory semantics are ported yet — fabricating any of them would
// break the honest-stop red line. The method shape is final; only the predicate
// narrows as M2..M6 land.
func (w *World) RunLoop(n int) LoopResult {
	_ = n
	return LoopResult{
		Exit:   YieldBeforeAttempt,
		Reason: "M1 skeleton: advance/after_step/strict-memory not yet ported; whole loop yields to Python",
	}
}
