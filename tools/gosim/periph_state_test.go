package gosim

import "testing"

// The advance state transfer (periph_state.go) must round-trip a PeriphState
// byte-for-byte: StateJSON -> LoadStateJSON into a fresh state -> StateJSON
// produces the identical bytes. This is the Go-side witness of the transfer the
// Python differential test (test_advance_consistency.py) exercises end to end.
func TestPeriphStateJSONRoundTrip(t *testing.T) {
	p := &PeriphState{
		SourceTicksPerStep:         4,
		TimerLsbTicksPerStep:       i64p(4),
		MulticoreEnabled:           false,
		CoreIcfg:                   [2]*uint32{nil, nil},
		CoreTickDeliveryRequests:   true,
		Timer0IRQDeliveryRequests:  true,
		CompletedModelSteps:        7,
	}
	p.Timer = NewTimer0(4, 5)
	p.Timer.Write(8, 9)
	p.Timer.Write(4, 0)
	p.Timer.Write(0, 0x1401) // std12m source, div 0, mode 1
	p.Timer.Advance(4)
	p.Timer.Pending = true
	p.Timer.DeliveryInFlight = true

	p.IRQ = NewIRQController()
	cfg := u32p(0x55000)
	p.IRQ.Config[0] = cfg
	p.IRQ.IPMask = u32p(2)

	ct, err := NewCoreTickStopped(u32p(0), i64p(4), true, true, true)
	if err != nil {
		t.Fatal(err)
	}
	ct.Write(8, 9)
	ct.Write(4, 3)
	ct.Write(0, 0x1)
	ct.Advance()
	p.CoreTicks[0] = ct

	first, err := p.StateJSON()
	if err != nil {
		t.Fatal(err)
	}

	fresh := &PeriphState{}
	if err := fresh.LoadStateJSON(first); err != nil {
		t.Fatal(err)
	}
	second, err := fresh.StateJSON()
	if err != nil {
		t.Fatal(err)
	}
	if string(first) != string(second) {
		t.Fatalf("state transfer round-trip changed bytes:\n  first =%s\n  second=%s", first, second)
	}

	// And an advance step from the transferred state must agree with one from the
	// original state (the orchestrator reads only the carried fields here).
	want, _ := p.AdvanceJSON(0x300)
	got, _ := fresh.AdvanceJSON(0x300)
	if string(want) != string(got) {
		t.Fatalf("advance from transferred state diverged:\n  want=%s\n  got =%s", want, got)
	}
}
