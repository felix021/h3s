package gosim

import "testing"

func i64p(v int64) *int64 { return &v }

// runnableWorld builds a World with every loop-level precondition satisfied and
// a single-core execute engine with one safe ALU instruction (add.r0, r0, the
// 16-bit encoding 0x1800) loaded at pc 0x100.
func runnableWorld() *World {
	cpu := NewCPU()
	cpu.MemWrite(0x100, []byte{0x00, 0x18, 0x00, 0x00, 0x00, 0x00})
	cpu.SetState(0, 0, 0, 0x100)
	cpu.SetICFG(0x300)
	return &World{
		Cores:   [2]*Core{{ID: 0, ICFG: 0x300, ICFGKnown: true}},
		Exec:    cpu,
		Mem:     NewMemory(),
		Periph:  &PeriphState{Timer: NewTimer0()},
		FetchLo: 0x100,
		FetchHi: 0x200,

		Concrete:       true,
		ObserverActive: false,
		AfterStepNoop:  true,
	}
}

// The default (unproven) configuration still yields before the first attempt.
func TestWorldRunLoopYields(t *testing.T) {
	w := &World{Cores: [2]*Core{{ID: 0}, {ID: 1}}, Mem: NewMemory()}
	res := w.RunLoop(8)
	if res.Exit != YieldBeforeAttempt {
		t.Fatalf("expected YieldBeforeAttempt, got exit %v", res.Exit)
	}
	if len(res.Steps) != 0 {
		t.Fatalf("yield must commit no steps, got %d", len(res.Steps))
	}
	if res.Reason == "" {
		t.Fatal("yield must carry an honest reason")
	}
}

// A dual-core world (core 1 present) yields with the select-not-ported reason.
func TestWorldRunLoopYieldsDualCore(t *testing.T) {
	w := runnableWorld()
	w.Cores[1] = &Core{ID: 1}
	res := w.RunLoop(1)
	if res.Exit != YieldBeforeAttempt || len(res.Steps) != 0 {
		t.Fatalf("dual-core world must yield empty, got %+v", res)
	}
}

// A counting Timer0 (no pending, no delivery) is now advanced in Go instead of
// yielding: the loop interleaves one execute step with one advance step.
func TestWorldRunLoopAdvancesCountingTimer(t *testing.T) {
	w := runnableWorld()
	tm := NewTimer0()
	tm.Write(8, 9)
	tm.Write(4, 0)
	tm.Write(0, 0x1001)
	w.Periph.Timer = tm
	res := w.RunLoop(1)
	if res.Exit != Stop {
		t.Fatalf("counting timer should advance in Go (Stop), got exit %v (%s)", res.Exit, res.Reason)
	}
	if len(res.Steps) != 1 {
		t.Fatalf("expected 1 interleaved step, got %d (%s)", len(res.Steps), res.Reason)
	}
	if res.Irq != nil {
		t.Fatalf("no delivery expected, got irq %+v", res.Irq)
	}
	if *tm.Cnt != 4 {
		t.Fatalf("timer should have counted 4 ticks, cnt=%d", *tm.Cnt)
	}
}

// A pending Timer0 that is eligible throws an IrqReq back to Python.
func TestWorldRunLoopTimer0Irq(t *testing.T) {
	w := runnableWorld()
	w.Periph.SourceTicksPerStep = 4
	tm := NewTimer0()
	tm.Write(8, 0) // prd 0 -> period 1: any tick overflows
	tm.Write(4, 0)
	tm.Write(0, 0x1001)
	tm.Pending = true // already pending before the loop starts
	ic := NewIRQController()
	ic.Config[0] = u32p(0x50000) // irq 4 nibble: enabled, priority 2
	ic.IPMask = u32p(2)
	w.Periph.IRQ = ic
	w.Periph.Timer0IRQDeliveryRequests = true
	w.Periph.Timer = tm
	res := w.RunLoop(1)
	if res.Exit != Stop || res.Irq == nil || res.Irq.Irq != 4 {
		t.Fatalf("expected IRQ4 delivery request, got %+v", res)
	}
}

// A counting core tick that overflows with delivery requests throws IRQ3.
func TestWorldRunLoopCoreTickOverflow(t *testing.T) {
	w := runnableWorld()
	w.Periph.SourceTicksPerStep = 4
	ct, _ := NewCoreTickStopped(u32p(0), i64p(4), false, false, false)
	ct.Write(8, 3) // prd 3 -> period 4
	ct.Write(4, 3) // cnt 3 -> next advance overflows
	ct.Write(0, 0x1)
	w.Periph.CoreTicks[0] = ct
	ic := NewIRQController()
	ic.Config[0] = u32p(0x5000) // irq 3 nibble: enabled, priority 2
	ic.IPMask = u32p(2)
	w.Periph.IRQ = ic
	w.Periph.CoreTickDeliveryRequests = true
	res := w.RunLoop(1)
	if res.Exit != Stop || res.Irq == nil || res.Irq.Irq != 3 {
		t.Fatalf("expected IRQ3 delivery request, got %+v", res)
	}
}

// An active unported advance model yields (Go cannot prove advance empty).
func TestWorldRunLoopYieldsOtherAdvance(t *testing.T) {
	w := runnableWorld()
	w.Periph.OtherAdvanceActive = true
	res := w.RunLoop(1)
	if res.Exit != YieldBeforeAttempt || len(res.Steps) != 0 {
		t.Fatalf("other advance model must yield empty, got %+v", res)
	}
}

// When every precondition holds and advance is provably empty, the loop closes:
// RunLoop executes the safe ALU stretch in Go and skips the empty advance.
func TestWorldRunLoopExecutesEmptyAdvance(t *testing.T) {
	w := runnableWorld()
	res := w.RunLoop(1)
	if res.Exit != Stop {
		t.Fatalf("expected Stop, got exit %v (%s)", res.Exit, res.Reason)
	}
	if len(res.Steps) != 1 {
		t.Fatalf("expected 1 committed step, got %d (%s)", len(res.Steps), res.Reason)
	}
	s := res.Steps[0]
	if s.PC != 0x100 || s.NextPC != 0x102 {
		t.Fatalf("bad step pc/next_pc: %#x -> %#x", s.PC, s.NextPC)
	}
	if len(s.Mem) != 0 {
		t.Fatalf("segment contract must never produce memory writes, got %v", s.Mem)
	}
	if res.StopPC != 0 {
		t.Fatalf("clean budget exhaustion must not report a hazard pc, got %#x", res.StopPC)
	}
}
