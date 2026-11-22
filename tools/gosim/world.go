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

// LoopMemWrite is one byte-granular store observed during a committed step.
type LoopMemWrite struct {
	Addr  uint32
	Bytes []byte
}

// LoopStep is one committed attempt's observation: the core it ran on, pc
// before/after, flags and the 16 registers after, this step's ordered memory
// writes, the executed instruction bytes (so self-modifying code's old trace is
// not polluted), and the Python adapter to count ("" = none).
type LoopStep struct {
	Core    int
	PC      uint32
	NextPC  uint32
	Flags   uint8
	R       [16]uint32
	Mem     []LoopMemWrite
	Insn    []byte
	Adapter string
}

// loopMemWrites converts the core's word writes into byte-granular observations.
func loopMemWrites(in []MemWrite) []LoopMemWrite {
	if len(in) == 0 {
		return nil
	}
	out := make([]LoopMemWrite, len(in))
	for i, w := range in {
		out[i] = LoopMemWrite{Addr: w.Addr, Bytes: wordBytes(w.Value)}
	}
	return out
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
	Exec   *CPU    // core-0 decode+execute engine (nil = cannot run)
	Exec1  *CPU    // core-1 decode+execute engine (nil = core 1 cannot run)
	Mem    *Memory // strict whole-loop memory (M2)
	Periph *PeriphState
	Ledger Ledger

	// Machine is the M6A dual-core select state (machine.go); Deliver is the
	// M6C delivery projection state (deliver.go). Both are wired into the
	// dual-core RunLoop. nil = not bound, so the dual-core loop yields.
	Machine *Machine
	Deliver *DeliverMachine

	// FetchLo/FetchHi is the immutable fetch window the execute phase must stay
	// inside (the ROM or flash app window, caller-supplied).
	FetchLo, FetchHi uint32

	// Fetch-guard diagnostic stops (Python explore-level stop_before_pc /
	// stop_on_rom_return). When armed, the loop yields before the attempt
	// rather than guessing their semantics.
	StopBeforePC    *uint32
	StopOnRomReturn bool

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
	// StopCore is the core id that hit the hazard (the one select picked when
	// the loop stopped), or -1 when the stop is not core-specific (a
	// YieldBeforeAttempt / a loop-level precondition). The caller (Python)
	// uses it to resume the explore loop from the exact post-stop core.
	StopCore int
	// ErrJSON is the advance strict-stop details (a PeriphError.JSON() string,
	// byte-identical to Python Blocked.details) when the loop stopped on an
	// advance strict stop; empty otherwise. The caller surfaces it as the stop.
	ErrJSON []byte
}

// yieldBeforeAttempt builds an empty-prefix result for a failed precondition.
func yieldBeforeAttempt(reason string) LoopResult {
	return LoopResult{Exit: YieldBeforeAttempt, Reason: reason, StopCore: -1}
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
		return w.runDualCore(n)
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
		// A pending Timer2 is a machine-side anchored poll wake (select-side,
		// br28_machine.timer2_poll_due), which this single-core loop has no
		// select phase to consume — hand it back rather than silently continue.
		if w.Periph.Timer2 != nil && w.Periph.Timer2.Pending {
			res.StopPC = currentPC(res)
			res.Reason = "timer2 completion wake at " + hexWord(Timer2) + " (machine-side anchored poll, handed back to Python)"
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
	case BatchMemFault:
		return "strict memory fault (handed back to Python)"
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

// execFor returns the per-core decode+execute engine.
func (w *World) execFor(core int) *CPU {
	if core == 1 {
		return w.Exec1
	}
	return w.Exec
}

// syncCPUFromCore copies the architectural state from the Core twin into the
// CPU engine before an execute step. The CPU's internal step counter is left
// alone (it is not read by the segment contract; Core.Steps is authoritative).
func syncCPUFromCore(cpu *CPU, c *Core) {
	cpu.R = c.R
	cpu.flags = c.Flags
	cpu.sp = c.SP
	cpu.rets = c.RETS
	cpu.pc = c.PC
	cpu.icfg = c.ICFG
	cpu.afterT = nil
}

// syncCoreFromCPU copies the architectural state back from the CPU engine after
// one execute step and bumps the core step counter.
func syncCoreFromCPU(c *Core, cpu *CPU) {
	c.R = cpu.R
	c.Flags = cpu.flags
	c.SP = cpu.sp
	c.RETS = cpu.rets
	c.PC = cpu.pc
	c.ICFG = cpu.icfg
	c.Steps++
}

// syncMachineCores copies the select-relevant view (pc / steps / icfg / decoded
// instruction id+size at pc) from the World cores into the Machine cores. The
// decoded instruction is what the two select wake helpers (CPU1-control kick,
// TIMER2 anchored poll) require exactly, so a pc that does not decode leaves the
// wake precondition unverifiable and the select helper honestly refuses.
func (w *World) syncMachineCores() {
	for core := 0; core < 2; core++ {
		c := w.Cores[core]
		if c == nil {
			continue
		}
		mc := w.Machine.Cores[core]
		if mc == nil {
			mc = &MCore{ID: core}
			w.Machine.Cores[core] = mc
		}
		mc.PC = c.PC
		mc.Steps = c.Steps
		mc.ICFG = c.ICFG
		mc.ICFGKnown = c.ICFGKnown
		mc.InsnID = ""
		mc.InsnSize = 0
		if cpu := w.execFor(core); cpu != nil {
			if im, _, size, err := cpu.decode(c.PC); err == nil {
				mc.InsnID = im.id
				mc.InsnSize = uint32(size)
			}
		}
	}
}

// deliveryDeferred mirrors the dispatch_delivery deferral judgment (probe_boot
// 路 CB/AS): a delivery request whose target core is mid-instruction
// (after_then/repeat in flight) or inside an in-flight handler is deferred, not
// delivered. The per-irq narrowing matches the Python in_flight_handler sets
// (IRQ68 may nest into the Timer0 handler, so only the stricter contexts defer;
// IRQ89 never nests). The deferral bookkeeping itself (note_delivery_deferral
// event + row['deferred_delivery']) stays Python, so the Go loop hands the
// request back instead of silently continuing.
func (w *World) deliveryDeferred(req *IrqReq) (bool, string) {
	c := w.Cores[req.Core]
	if c == nil {
		return false, ""
	}
	mid := c.RepeatActive
	ctx := c.ActiveContext
	inFlight := false
	switch req.Irq {
	case AudioDmaIRQNumber:
		inFlight = ctx == "software_irq_handler" || ctx == "core_tick_handler" ||
			ctx == "audio_dma_irq_handler" || ctx == "src_sync_irq_handler"
	case SrcSyncIRQNumber:
		inFlight = ctx == "software_irq_handler" || ctx == "core_tick_handler" ||
			ctx == "timer0_irq_handler" || ctx == "src_sync_irq_handler" || ctx == "audio_dma_irq_handler"
	default:
		inFlight = ctx == "software_irq_handler" || ctx == "core_tick_handler" ||
			ctx == "timer0_irq_handler" || ctx == "audio_dma_irq_handler" || ctx == "src_sync_irq_handler"
	}
	if mid && !inFlight {
		return true, "mid-instruction (after_then/repeat in flight)"
	}
	if inFlight {
		return true, "in-flight IRQ handler " + ctx
	}
	return false, ""
}

// dispatchDelivery routes a pending-eligible delivery request to the right M6C
// delivery projection (the dispatch_delivery mapping for a clean instruction
// boundary). The delivery mutates the target Core in place (w.Deliver.Cores is
// bound to w.Cores).
func (w *World) dispatchDelivery(req *IrqReq) error {
	w.Deliver.Cores = w.Cores
	switch req.Irq {
	case 3:
		return w.Deliver.DeliverCoreTick(req.Core)
	case 4:
		return w.Deliver.DeliverTimer0Irq(req.Core)
	case AudioDmaIRQNumber:
		return w.Deliver.DeliverAudioDmaIrq(req.Core)
	case SrcSyncIRQNumber:
		return w.Deliver.DeliverSrcSyncIrq(req.Core)
	default:
		return w.Deliver.DeliverSoftwareIrq(req.Core, req.Irq)
	}
}

// deliveryStopReason renders a delivery-projection failure into an honest Stop
// reason (DeliverValueError = Python require/ValueError, PeriphError = Blocked).
func deliveryStopReason(err error) string {
	switch e := err.(type) {
	case *DeliverValueError:
		return "delivery require violated: " + e.Msg
	case *PeriphError:
		return "delivery strict stop: " + e.JSON()
	default:
		return "delivery error: " + err.Error()
	}
}

// runDualCore wires the three M6 pieces (select / after_step / deliver) into
// one execution loop over both cores: select -> fetch guard -> decode ->
// execute -> after_step -> advance -> trace. It is the honest whole-loop
// skeleton: the ported pieces (select, the after_step no-op judgment, the
// dual-core advance chain, the delivery projections at a clean boundary) run
// inside Go, and every phase still Python-only (enter_idle_wait, event clock,
// trace-row text/bytes/delivery-key materialization, strict-memory load/fetch
// semantics, abstract values, the dispatch_delivery deferral bookkeeping) hands
// back via YieldBeforeAttempt / Stop — nothing is fabricated.
func (w *World) runDualCore(n int) LoopResult {
	if w.Machine == nil {
		return yieldBeforeAttempt("dual-core select (M6A Machine) not bound")
	}
	if w.Deliver == nil {
		return yieldBeforeAttempt("dual-core deliver projections (M6C DeliverMachine) not bound")
	}
	if w.Exec == nil || w.Exec1 == nil {
		return yieldBeforeAttempt("per-core execute engines not bound")
	}
	if !w.Concrete {
		return yieldBeforeAttempt("non-concrete (abstract) cpu state")
	}
	if w.AfterStep != nil && w.AfterStep.ObserverActive {
		return yieldBeforeAttempt("observer hook active")
	}
	if w.Periph == nil {
		return yieldBeforeAttempt("no peripheral model bound")
	}
	if w.StopBeforePC != nil || w.StopOnRomReturn {
		return yieldBeforeAttempt("fetch-guard diagnostic stop armed (stays in Python)")
	}
	if w.Cores[0] == nil || w.Cores[1] == nil {
		return yieldBeforeAttempt("dual-core world needs both cores present")
	}
	if w.Cores[0].RepeatActive || w.Cores[1].RepeatActive {
		return yieldBeforeAttempt("repeat/after_then active on a core")
	}

	// Bind the delivery machine's cores to the world cores so delivery state
	// changes land on the same Core objects the select/execute phases read.
	w.Deliver.Cores = w.Cores

	// Third-wiring leftover observation: Machine.Timer2 (read by the select-side
	// timer2_poll_due check) and Periph.Timer2 (mutated by the advance-side Timer2
	// count) are two independent objects whose pending flag only aligned at
	// LoadJSON. In Python both are the single hardware.timer2 object, so bind them
	// to the same Go object — a Timer2 expiry the advance sets Pending on is then
	// visible to the select-side anchored-poll-due check on the next iteration.
	// (The poll wake itself still cannot fire here: it requires a core parked at
	// the TIMER2 anchor, and enter_idle_wait stays Python-only, so the loop hands
	// back at the idle instruction first — honest, nothing fabricated.)
	if w.Machine.Timer2DelayProjection {
		w.Machine.Timer2 = w.Periph.Timer2
	}

	res := LoopResult{Exit: Stop, StopCore: -1}
	for i := 0; i < n; i++ {
		w.syncMachineCores()

		// 1. select (dual-core round-robin + the two wake fallbacks).
		core, err := w.Machine.Select()
		if err != nil {
			res.StopCore = core
			res.StopPC = w.Cores[core].PC
			res.Reason = "select wake require violated: " + err.Error()
			return res
		}
		w.Deliver.IdleWait = w.Machine.IdleWait
		w.Periph.CoreIcfg = w.Machine.CoreIcfg

		// 1b. idle hand-back: the selected core is parked at an idle instruction
		// and the IDLE wait projection is armed — enter_idle_wait (and the
		// event-clock advance behind it) stays Python, so hand the attempt back
		// before executing the idle instead of fabricating a wake. (The idle
		// instruction is also outside the stage-1/2 contract, so this is purely
		// a cleaner reason; the instruction is never executed either way.)
		if w.Machine.IdleWaitProjection && w.Machine.Cores[core] != nil &&
			w.Machine.Cores[core].InsnID == "idle" {
			res.StopCore = core
			res.StopPC = w.Cores[core].PC
			res.Reason = "idle instruction (enter_idle_wait stays Python)"
			return res
		}

		// 2. fetch guard: the immutable-window check is per-step inside the
		// execute engine; the armed diagnostic stops yielded above.

		// 3+4. decode + execute one safe-ALU step on the selected core.
		beforePC := w.Cores[core].PC
		cpu := w.execFor(core)
		syncCPUFromCore(cpu, w.Cores[core])
		s, code, stopPC, stepErr := cpu.RunDualStep()
		if code != BatchOK {
			res.StopCore = core
			res.StopPC = stopPC
			res.Reason = segmentStopReason(code)
			if stepErr != nil {
				res.ErrJSON = []byte(stepErr.Error())
			}
			return res
		}
		syncCoreFromCPU(w.Cores[core], cpu)

		// A store invalidates both cores' decode caches (self-modifying code
		// would otherwise re-execute the stale bytes).
		if len(s.Mem) > 0 {
			w.Exec.ClearCache()
			w.Exec1.ClearCache()
		}

		// 7. trace row (the Go twin: core/pc/next_pc/flags/registers; the
		// Python text/bytes/delivery-key materialization stays Python). The
		// committed step is recorded before the after_step/advance phases, so a
		// hazard in those phases still carries the executed prefix — exactly as
		// Python appends the row even when after_step/advance raises.
		res.Steps = append(res.Steps, LoopStep{
			Core:    core,
			PC:      s.PC,
			NextPC:  s.NextPC,
			Flags:   s.Flags,
			R:       s.R,
			Mem:     loopMemWrites(s.Mem),
			Insn:    s.Insn,
			Adapter: s.Adapter,
		})

		// 5. after_step judgment (hook bodies stay Python).
		if w.AfterStep != nil {
			if ok, why := w.AfterStep.CanProveNoop(core, beforePC, s.NextPC); !ok {
				res.StopCore = core
				res.StopPC = s.NextPC
				res.Reason = "after_step hook fires: " + why + " (handed back to Python)"
				return res
			}
		}

		// 6. advance (dual-core scan). A pending-eligible IRQ — deferred or
		// clean-boundary — is handed back to Python for dispatch_delivery (the
		// delivery projections stay Python in this cut; the in-Go dispatch path
		// is not wired into explore yet). Nothing is fabricated.
		adv := w.Periph.Advance(w.Cores[core].ICFG)
		if adv.Irq != nil {
			res.Irq = adv.Irq
			res.StopCore = core
			res.StopPC = s.NextPC
			if deferred, why := w.deliveryDeferred(adv.Irq); deferred {
				res.Reason = "delivery deferral (dispatch_delivery 路 CB/AS) not ported: " + why + " (handed back to Python)"
			} else {
				res.Reason = fmt.Sprintf("pending eligible irq %d on core %d (delivery handed back to Python)", adv.Irq.Irq, adv.Irq.Core)
			}
			return res
		}
		if adv.Err != nil {
			res.StopCore = core
			res.StopPC = s.NextPC
			res.Reason = "advance strict stop: " + adv.Err.JSON()
			res.ErrJSON = []byte(adv.Err.JSON())
			return res
		}
	}
	res.StopPC = 0
	res.Reason = fmt.Sprintf("ran %d dual-core attempts with select/after_step/advance wiring (no hazard)", len(res.Steps))
	return res
}
