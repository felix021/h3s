package gosim

import "testing"

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

// A pending-eligible Timer0 IRQ4 at a clean boundary is delivered in-Go: the
// M6C DeliverTimer0Irq projection moves core 0 into the timer0 handler.
func TestDualCoreRunLoopDeliversTimer0Irq(t *testing.T) {
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
	if res.Exit != Stop || res.Irq != nil {
		t.Fatalf("expected a clean Timer0 delivery (no handback), got %+v", res)
	}
	if len(res.Steps) != 1 {
		t.Fatalf("the executed step must be committed before delivery, got %d steps", len(res.Steps))
	}
	if len(w.Deliver.Timer0IrqDeliveries) != 1 {
		t.Fatalf("expected 1 timer0 delivery, got %d", len(w.Deliver.Timer0IrqDeliveries))
	}
	if w.Cores[0].ActiveContext != "timer0_irq_handler" {
		t.Fatalf("core 0 should be in the timer0 handler, got %q", w.Cores[0].ActiveContext)
	}
	if w.Cores[0].PC != Timer0Handler {
		t.Fatalf("core 0 pc should be handler 0x%x, got 0x%x", Timer0Handler, w.Cores[0].PC)
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
