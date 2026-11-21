package gosim

import "testing"

func newMCore(id int, pc uint32, icfg uint32, known bool) *MCore {
	return &MCore{ID: id, PC: pc, ICFG: icfg, ICFGKnown: known, InsnID: "idle", InsnSize: 2}
}

// Fresh machine with core 0 only selects core 0.
func TestMachineSelectSingleCore(t *testing.T) {
	m := NewMachine()
	m.Cores[0] = newMCore(0, 0x100, 0x300, true)
	for i := 0; i < 4; i++ {
		got, err := m.Select()
		if err != nil {
			t.Fatal(err)
		}
		if got != 0 {
			t.Fatalf("single core must select 0, got %d", got)
		}
	}
}

// Dual core with neither idle round-robins 0,1,0,1 (last_core starts 1).
func TestMachineSelectRoundRobin(t *testing.T) {
	m := NewMachine()
	m.Cores[0] = newMCore(0, 0x100, 0x300, true)
	m.Cores[1] = newMCore(1, 0x200, 0x300, true)
	want := []int{0, 1, 0, 1, 0, 1}
	for i, w := range want {
		got, err := m.Select()
		if err != nil {
			t.Fatal(err)
		}
		if got != w {
			t.Fatalf("step %d: want core %d, got %d", i, w, got)
		}
	}
}

// A parked core is skipped in favor of the other runnable core.
func TestMachineSelectIdleWaitExclusion(t *testing.T) {
	m := NewMachine()
	m.Cores[0] = newMCore(0, 0x100, 0x300, true)
	m.Cores[1] = newMCore(1, 0x200, 0x300, true)
	m.IdleWait[1] = true // core 1 parked
	// last_core=1 -> core=0 first, not parked -> 0
	if got, _ := m.Select(); got != 0 {
		t.Fatalf("want 0, got %d", got)
	}
	// now last_core=0 -> core=1, parked -> other=0 runnable -> 0
	if got, _ := m.Select(); got != 0 {
		t.Fatalf("parked core 1 must be skipped, want 0, got %d", got)
	}
	// core 0 parked, core 1 runnable: last_core=0 -> core=1 -> 1
	m.IdleWait[0] = true
	m.IdleWait[1] = false
	if got, _ := m.Select(); got != 1 {
		t.Fatalf("parked core 0 must be skipped, want 1, got %d", got)
	}
}

// Both parked: round-robin target stays (no runnable peer to fall back to).
func TestMachineSelectBothParked(t *testing.T) {
	m := NewMachine()
	m.Cores[0] = newMCore(0, 0x100, 0x300, true)
	m.Cores[1] = newMCore(1, 0x200, 0x300, true)
	m.IdleWait[0] = true
	m.IdleWait[1] = true
	// last_core=1 -> core=0, parked; other=1 also parked -> stays 0
	if got, _ := m.Select(); got != 0 {
		t.Fatalf("both parked must keep the round-robin target 0, got %d", got)
	}
}

// defer_core1_first_idle bounces the core-1 pick back to core 0 (bounded).
func TestMachineSelectDeferCore1(t *testing.T) {
	m := NewMachine()
	m.Cores[0] = newMCore(0, 0x100, 0x300, true)
	m.Cores[1] = newMCore(1, Core1IdlePC, 0x300, true)
	m.DeferCore1FirstIdle = true
	m.FirstTaskReturns[1] = true
	m.LastCore = 0 // next round-robin pick is core 1
	got, _ := m.Select()
	if got != 0 {
		t.Fatalf("defer_core1 must bounce to core 0, got %d", got)
	}
	if m.DeferredCore1Turns != 1 {
		t.Fatalf("deferred turns must increment, got %d", m.DeferredCore1Turns)
	}
}

// At the issue-delay limit the defer stops and core 1 runs.
func TestMachineSelectDeferCore1AtLimit(t *testing.T) {
	m := NewMachine()
	m.Cores[0] = newMCore(0, 0x100, 0x300, true)
	m.Cores[1] = newMCore(1, Core1IdlePC, 0x300, true)
	m.DeferCore1FirstIdle = true
	m.FirstTaskReturns[1] = true
	m.DeferredCore1Turns = Core1IdleDelayLimit
	m.LastCore = 0
	got, _ := m.Select()
	if got != 1 {
		t.Fatalf("at the delay limit core 1 must run, got %d", got)
	}
	if m.DeferredCore1Turns != Core1IdleDelayLimit {
		t.Fatalf("turns must not change at the limit, got %d", m.DeferredCore1Turns)
	}
}

// The CPU1-control kick wake fires before the round-robin choice and consumes
// the parked idle (pc += size, steps += 1, event recorded).
func TestMachineSelectCpu1KickWake(t *testing.T) {
	m := NewMachine()
	m.Cores[0] = newMCore(0, 0x100, 0x300, true)
	m.Cores[1] = newMCore(1, 0x6066318, 0x300, true)
	m.Cores[1].Steps = 5
	m.IdleWait[1] = true
	m.Cpu1ControlKickWake = true
	kicks := int64(1)
	m.HardwareKicks = &kicks
	m.SeenCpu1ControlKicks = 0

	if got, err := m.Select(); err != nil || got != 0 {
		t.Fatalf("kick select: want (0, nil), got (%d, %v)", got, err)
	}
	if m.IdleWait[1] {
		t.Fatal("core 1 must leave idle_wait after the kick")
	}
	if m.Cores[1].PC != 0x606631a {
		t.Fatalf("core 1 pc must advance by idle size, got %#x", m.Cores[1].PC)
	}
	if m.Cores[1].Steps != 6 {
		t.Fatalf("core 1 steps must increment, got %d", m.Cores[1].Steps)
	}
	if m.SeenCpu1ControlKicks != 1 {
		t.Fatalf("seen kicks must advance, got %d", m.SeenCpu1ControlKicks)
	}
	if len(m.Events) != 1 || m.Events[0]["kind"] != "core_woken_by_cpu1_control_kick" {
		t.Fatalf("kick wake event missing, events=%v", m.Events)
	}
}

// The TIMER2 poll wake fires when pending and an anchored core is parked, and
// consumes the anchored idle (pc += 2, steps += 1).
func TestMachineSelectTimer2Wake(t *testing.T) {
	m := NewMachine()
	m.Cores[0] = newMCore(0, Timer2PollAnchor, 0x300, true)
	m.Cores[1] = newMCore(1, 0x200, 0x300, true)
	m.IdleWait[0] = true
	m.Timer2DelayProjection = true
	m.Timer2 = NewTimer2Delay()
	m.Timer2.Pending = true

	if got, err := m.Select(); err != nil || got != 0 {
		t.Fatalf("timer2 select: want (0, nil), got (%d, %v)", got, err)
	}
	if m.IdleWait[0] {
		t.Fatal("anchored core 0 must leave idle_wait after the TIMER2 wake")
	}
	if m.Cores[0].PC != Timer2PollAnchor+2 {
		t.Fatalf("anchored pc must consume idle, got %#x", m.Cores[0].PC)
	}
	if len(m.Timer2PollWakes) != 1 {
		t.Fatalf("timer2 poll wake must be recorded, got %v", m.Timer2PollWakes)
	}
	if len(m.Events) != 1 || m.Events[0]["kind"] != "core_woken_by_timer2_completion" {
		t.Fatalf("timer2 wake event missing, events=%v", m.Events)
	}
}

// The kick wake refuses a non-idle instruction at the parked core's PC.
func TestMachineSelectKickRequiresIdle(t *testing.T) {
	m := NewMachine()
	m.Cores[0] = newMCore(0, 0x100, 0x300, true)
	m.Cores[1] = newMCore(1, 0x6066318, 0x300, true)
	m.Cores[1].InsnID = "mov.rr"
	m.IdleWait[1] = true
	m.Cpu1ControlKickWake = true
	kicks := int64(1)
	m.HardwareKicks = &kicks
	if _, err := m.Select(); err == nil {
		t.Fatal("kick wake must refuse a non-idle parked instruction")
	}
}
