package gosim

import "testing"

// TestCanProveAfterStepNoop pins the four after_step hooks' hit/miss branches.
// Each case mirrors one branch of br28_machine.DualCoreMachine.after_step.
func TestCanProveAfterStepNoop(t *testing.T) {
	cases := []struct {
		name   string
		a      *AfterStep
		core   int
		before uint32
		next   uint32
		wantOK bool
	}{
		{"nil config", nil, 0, 0x100, 0x102, true},
		{"empty config any pc", &AfterStep{}, 0, 0x100, 0x102, true},

		// Hook 1: cfg523 fixture install (next pc == parser entry, core 0,
		// armed and not yet installed).
		{"cfg523 fires at entry", &AfterStep{Cfg523Enabled: true}, 0, 0x60010b2, Cfg523ParserEntry, false},
		{"cfg523 not at entry", &AfterStep{Cfg523Enabled: true}, 0, 0x100, 0x102, true},
		{"cfg523 already installed", &AfterStep{Cfg523Enabled: true, Cfg523Installed: true}, 0, 0x60010b2, Cfg523ParserEntry, true},
		{"cfg523 core-1 gate", &AfterStep{Cfg523Enabled: true}, 1, 0x60010b2, Cfg523ParserEntry, true},

		// Hook 2: core-1 boot anchors (before pc recorder).
		{"boot anchor fires", &AfterStep{}, 1, 0xc5fe, 0xc600, false},
		{"boot anchor core-0 gate", &AfterStep{}, 0, 0xc5fe, 0xc600, true},
		{"boot anchor not an anchor", &AfterStep{}, 1, 0x1234, 0x1236, true},

		// Hook 3: first-task hypothesis.
		{"first-task entry fires", &AfterStep{FirstTaskHypothesis: true}, 0, 0x6065364, 0x6066106, false},
		{"first-task setup trace advances", &AfterStep{FirstTaskHypothesis: true, FirstTaskSetupIndex: [2]int{0, -1}}, 0, 0x6066106, 0x6066108, false},
		{"first-task setup trace later pc", &AfterStep{FirstTaskHypothesis: true, FirstTaskSetupIndex: [2]int{5, -1}}, 0, 0x6066116, 0x606611a, false},
		{"first-task no in-flight setup", &AfterStep{FirstTaskHypothesis: true, FirstTaskSetupIndex: [2]int{-1, -1}}, 0, 0x6066106, 0x6066108, true},
		{"first-task frame tail fires", &AfterStep{FirstTaskHypothesis: true, FirstTaskReturnFrame: [2]bool{true, false}}, 0, 0x606612c, 0x606612e, false},
		{"first-task frame tail not armed", &AfterStep{FirstTaskHypothesis: true}, 0, 0x606612c, 0x606612e, true},
		{"first-task hypothesis off", &AfterStep{}, 0, 0x6065364, 0x6066106, true},

		// Hook 4: D1 observer feeds every step.
		{"observer active", &AfterStep{ObserverActive: true}, 0, 0x100, 0x102, false},
	}
	for _, tc := range cases {
		ok, why := tc.a.CanProveNoop(tc.core, tc.before, tc.next)
		if ok != tc.wantOK {
			t.Fatalf("%s: got %v (%s), want %v", tc.name, ok, why, tc.wantOK)
		}
	}
}

// TestAfterStepHasArmedHook pins the config-level fast-path gate.
func TestAfterStepHasArmedHook(t *testing.T) {
	cases := []struct {
		name string
		a    *AfterStep
		want bool
	}{
		{"nil", nil, false},
		{"empty", &AfterStep{}, false},
		{"observer", &AfterStep{ObserverActive: true}, true},
		{"first-task", &AfterStep{FirstTaskHypothesis: true}, true},
		{"cfg523 armed", &AfterStep{Cfg523Enabled: true}, true},
		{"cfg523 installed", &AfterStep{Cfg523Enabled: true, Cfg523Installed: true}, false},
	}
	for _, tc := range cases {
		if got := tc.a.HasArmedHook(); got != tc.want {
			t.Fatalf("%s: got %v, want %v", tc.name, got, tc.want)
		}
	}
}

// A cfg523-armed world whose first step lands on the parser entry stops at that
// step and hands the after_step hook back to Python (instead of fabricating the
// install).
func TestWorldRunLoopAfterStepHookFires(t *testing.T) {
	cpu := NewCPU()
	cpu.MemWrite(0x60010b2, []byte{0x00, 0x18, 0x00, 0x00}) // add.r0 r0, 2 bytes
	cpu.SetState(0, 0, 0, 0x60010b2)
	cpu.SetICFG(0x300)
	w := &World{
		Cores:     [2]*Core{{ID: 0, ICFG: 0x300, ICFGKnown: true}},
		Exec:      cpu,
		Mem:       NewMemory(),
		Periph:    &PeriphState{Timer: NewTimer0()},
		FetchLo:   0x6001000,
		FetchHi:   0x6002000,
		Concrete:  true,
		AfterStep: &AfterStep{Cfg523Enabled: true},
	}
	res := w.RunLoop(1)
	if res.Exit != Stop {
		t.Fatalf("expected Stop, got exit %v (%s)", res.Exit, res.Reason)
	}
	if len(res.Steps) != 1 {
		t.Fatalf("the firing step is committed, want 1 step, got %d (%s)", len(res.Steps), res.Reason)
	}
	if res.StopPC != Cfg523ParserEntry {
		t.Fatalf("stop pc must be the parser entry, got %#x", res.StopPC)
	}
	if res.Irq != nil {
		t.Fatalf("after_step hook is not an IRQ, got %+v", res.Irq)
	}
}

// The same world with the cfg523 fixture already installed closes the loop in
// Go (the idempotent gate consumed, so after_step is provably empty).
func TestWorldRunLoopAfterStepHookInstalledCloses(t *testing.T) {
	cpu := NewCPU()
	cpu.MemWrite(0x60010b2, []byte{0x00, 0x18, 0x00, 0x00})
	cpu.SetState(0, 0, 0, 0x60010b2)
	cpu.SetICFG(0x300)
	w := &World{
		Cores:     [2]*Core{{ID: 0, ICFG: 0x300, ICFGKnown: true}},
		Exec:      cpu,
		Mem:       NewMemory(),
		Periph:    &PeriphState{Timer: NewTimer0()},
		FetchLo:   0x6001000,
		FetchHi:   0x6002000,
		Concrete:  true,
		AfterStep: &AfterStep{Cfg523Enabled: true, Cfg523Installed: true},
	}
	res := w.RunLoop(1)
	if res.Exit != Stop || len(res.Steps) != 1 {
		t.Fatalf("installed cfg523 must close in Go, got %+v", res)
	}
	if res.StopPC != 0 {
		t.Fatalf("clean close must not report a hazard pc, got %#x (%s)", res.StopPC, res.Reason)
	}
}
