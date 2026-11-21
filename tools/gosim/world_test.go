package gosim

import "testing"

// runnableWorld builds a World with every loop-level precondition satisfied and
// a single-core execute engine with one safe ALU instruction (add.r0, r0, the
// 16-bit encoding 0x1800) loaded at pc 0x100.
func runnableWorld() *World {
	cpu := NewCPU()
	cpu.MemWrite(0x100, []byte{0x00, 0x18, 0x00, 0x00, 0x00, 0x00})
	cpu.SetState(0, 0, 0, 0x100)
	cpu.SetICFG(0x300)
	return &World{
		Cores:   [2]*Core{{ID: 0}},
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

// A counting Timer0 yields with the advance-not-empty reason.
func TestWorldRunLoopYieldsCountingTimer(t *testing.T) {
	w := runnableWorld()
	tm := NewTimer0()
	tm.Write(8, 9)
	tm.Write(4, 0)
	tm.Write(0, 0x1001)
	w.Periph.Timer = tm
	res := w.RunLoop(1)
	if res.Exit != YieldBeforeAttempt || len(res.Steps) != 0 {
		t.Fatalf("counting timer must yield empty, got %+v", res)
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
