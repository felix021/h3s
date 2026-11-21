package gosim

import (
	"strings"
	"testing"
)

// dualCoreWorld builds a runnable dual-core World: both cores bound to execute
// engines with one safe ALU instruction (add r0,r0 = 0x1800) at pc 0x100, the
// M6A select Machine and the M6C DeliverMachine bound, and an empty advance.
func dualCoreWorld() *World {
	cpu0 := NewCPU()
	cpu0.MemWrite(0x100, []byte{0x00, 0x18, 0x00, 0x00, 0x00, 0x00})
	cpu0.SetState(0, 0, 0, 0x100)
	cpu0.SetICFG(0x300)
	cpu1 := NewCPU()
	cpu1.MemWrite(0x100, []byte{0x00, 0x18, 0x00, 0x00, 0x00, 0x00})
	cpu1.SetState(0, 0, 0, 0x100)
	cpu1.SetICFG(0x300)
	return &World{
		Cores: [2]*Core{
			{ID: 0, PC: 0x100, SP: 0, SPKnown: true, ICFG: 0x300, ICFGKnown: true},
			{ID: 1, PC: 0x100, SP: 0, SPKnown: true, ICFG: 0x300, ICFGKnown: true},
		},
		Exec:      cpu0,
		Exec1:     cpu1,
		Mem:       NewMemory(),
		Periph:    &PeriphState{Timer: NewTimer0()},
		Machine:   NewMachine(),
		Deliver:   NewDeliverMachine(),
		FetchLo:   0x100,
		FetchHi:   0x200,
		Concrete:  true,
		AfterStep: &AfterStep{},
	}
}

// The three M6 pieces wired into RunLoop: select picks core 0 then core 1
// (round-robin), each executes one safe-ALU step, advance is empty, and the
// trace carries two steps.
func TestDualCoreRunLoopExecutesBothCores(t *testing.T) {
	w := dualCoreWorld()
	res := w.RunLoop(2)
	if res.Exit != Stop {
		t.Fatalf("expected Stop, got exit %v (%s)", res.Exit, res.Reason)
	}
	if len(res.Steps) != 2 {
		t.Fatalf("expected 2 committed steps, got %d (%s)", len(res.Steps), res.Reason)
	}
	if res.Steps[0].Core != 0 || res.Steps[1].Core != 1 {
		t.Fatalf("round-robin should run core 0 then core 1, got %d then %d", res.Steps[0].Core, res.Steps[1].Core)
	}
	if res.Steps[0].PC != 0x100 || res.Steps[0].NextPC != 0x102 {
		t.Fatalf("bad core-0 step %#x -> %#x", res.Steps[0].PC, res.Steps[0].NextPC)
	}
	if w.Cores[0].Steps != 1 || w.Cores[1].Steps != 1 {
		t.Fatalf("both cores should have 1 step, got %d/%d", w.Cores[0].Steps, w.Cores[1].Steps)
	}
	if res.StopPC != 0 {
		t.Fatalf("clean budget exhaustion must not report a hazard pc, got %#x", res.StopPC)
	}
}

// A missing M6 piece (no Deliver machine) yields before the first attempt.
func TestDualCoreRunLoopYieldsWithoutDeliver(t *testing.T) {
	w := dualCoreWorld()
	w.Deliver = nil
	res := w.RunLoop(1)
	if res.Exit != YieldBeforeAttempt || len(res.Steps) != 0 {
		t.Fatalf("dual-core loop without a Deliver machine must yield empty, got %+v", res)
	}
}

// A pending-eligible Timer0 IRQ4 at a clean boundary is handed back to Python
// (the delivery projections stay Python in the fifth wiring): the executed step
// is committed, the request is reported, and no delivery is fabricated in-Go.
func TestDualCoreRunLoopHandsBackTimer0Irq(t *testing.T) {
	w := dualCoreWorld()
	ic := NewIRQController()
	ic.Config[0] = u32p(0x50000) // irq 4 nibble: enabled, priority 2
	ic.IPMask = u32p(2)
	w.Periph.IRQ = ic
	w.Periph.Timer = &Timer0{Pending: true}
	w.Periph.Timer0IRQDeliveryRequests = true

	w.Deliver.Timer0IrqEntry = true
	w.Deliver.VectorSlots = map[uint32]*uint32{VectorSlotBase + 4*4: u32p(Timer0Handler)}

	res := w.RunLoop(1)
	if res.Exit != Stop || res.Irq == nil {
		t.Fatalf("expected an IRQ hand-back, got %+v", res)
	}
	if res.Irq.Core != 0 || res.Irq.Irq != 4 {
		t.Fatalf("expected Irq(0,4), got %+v", res.Irq)
	}
	if len(res.Steps) != 1 {
		t.Fatalf("the executed step must be committed before the hand-back, got %d steps", len(res.Steps))
	}
	if len(w.Deliver.Timer0IrqDeliveries) != 0 {
		t.Fatalf("delivery must stay Python (no in-Go delivery), got %d", len(w.Deliver.Timer0IrqDeliveries))
	}
	if w.Cores[0].ActiveContext != "" {
		t.Fatalf("core 0 must not enter a handler in-Go, got %q", w.Cores[0].ActiveContext)
	}
}

// An after_step hook that fires at the committed step's next pc stops the loop
// and hands back, keeping the already-executed prefix.
func TestDualCoreRunLoopAfterStepHookStops(t *testing.T) {
	w := dualCoreWorld()
	pc := uint32(Cfg523ParserEntry - 2) // one safe 2-byte step lands on the parser entry
	w.FetchLo = Cfg523ParserEntry - 0x10
	w.FetchHi = Cfg523ParserEntry + 0x10
	for _, c := range w.Cores {
		c.PC = pc
	}
	w.Exec.MemWrite(pc, []byte{0x00, 0x18, 0x00, 0x00, 0x00, 0x00})
	w.AfterStep = &AfterStep{Cfg523Enabled: true}
	res := w.RunLoop(1)
	if res.Exit != Stop {
		t.Fatalf("expected Stop, got %v", res.Exit)
	}
	if res.StopPC != Cfg523ParserEntry {
		t.Fatalf("expected stop at parser entry, got %#x (%s)", res.StopPC, res.Reason)
	}
	if len(res.Steps) != 1 {
		t.Fatalf("the executed step must be committed before the hook fires, got %d", len(res.Steps))
	}
	if res.Reason == "" {
		t.Fatal("the hook stop must carry an honest reason")
	}
}

// An instruction outside the stage-1/2 contract (here: a plain 0xffff that does
// not decode) stops honestly without fabricating an execution.
func TestDualCoreRunLoopHonestStopOnUnsafe(t *testing.T) {
	w := dualCoreWorld()
	// Load an undecodable word at core 0's pc.
	w.Exec.MemWrite(0x100, []byte{0xff, 0xff, 0xff, 0xff, 0xff, 0xff})
	res := w.RunLoop(1)
	if res.Exit != Stop || res.Reason == "" {
		t.Fatalf("expected an honest Stop, got %+v", res)
	}
	if len(res.Steps) != 0 {
		t.Fatalf("an undecodable instruction must not be committed, got %d steps", len(res.Steps))
	}
}

// The third-wiring leftover: Machine.Timer2 (select-side poll-due check) and
// Periph.Timer2 (advance-side count) must become the single hardware.timer2
// object so a Timer2 pending flag set by advance is visible to the select-side
// check. This pins the runDualCore binding fix.
func TestDualCoreRunLoopBindsMachineTimer2ToPeriph(t *testing.T) {
	w := dualCoreWorld()
	w.Machine.Timer2DelayProjection = true
	w.Machine.Timer2 = NewTimer2Delay() // a stale separate object, Pending false
	w.Periph.Timer2 = NewTimer2Delay()
	w.Periph.Timer2.Pending = true

	res := w.RunLoop(1)
	if res.Exit != Stop || len(res.Steps) != 1 {
		t.Fatalf("expected one clean step, got %+v", res)
	}
	if w.Machine.Timer2 != w.Periph.Timer2 {
		t.Fatalf("Machine.Timer2 must be bound to Periph.Timer2 (single hardware.timer2 object)")
	}
	if !w.Machine.Timer2.Pending {
		t.Fatalf("the select-side Timer2 must observe the advance-side pending flag")
	}
}

// A pending-eligible delivery landing on a core whose IRQ handler is in flight
// is deferred, not fabricated: the loop hands the request back to Python with the
// honest reason. (Mid-instruction deferral is subsumed by the RepeatActive
// precondition yield, so the reachable deferral in the loop is the in-flight-
// handler branch.)
func TestDualCoreRunLoopDefersInFlightHandlerDelivery(t *testing.T) {
	w := dualCoreWorld()
	ic := NewIRQController()
	ic.Config[0] = u32p(0x50000) // irq 4 nibble: enabled, priority 2
	ic.IPMask = u32p(2)
	w.Periph.IRQ = ic
	w.Periph.Timer = &Timer0{Pending: true}
	w.Periph.Timer0IRQDeliveryRequests = true
	w.Cores[0].ActiveContext = "timer0_irq_handler" // in-flight handler: deferral

	res := w.RunLoop(1)
	if res.Exit != Stop || res.Irq == nil {
		t.Fatalf("expected a deferred-request hand-back, got %+v", res)
	}
	if res.Irq.Core != 0 || res.Irq.Irq != 4 {
		t.Fatalf("expected Irq(0,4), got %+v", res.Irq)
	}
	if len(res.Steps) != 1 {
		t.Fatalf("the executed step must be committed before the deferral, got %d steps", len(res.Steps))
	}
	if !strings.Contains(res.Reason, "delivery deferral") {
		t.Fatalf("reason must name the deferral, got %q", res.Reason)
	}
}
