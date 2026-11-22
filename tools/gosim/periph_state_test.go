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

// The remaining M4/M5 leaves (watchdog / ICU / Timer2 / software IRQ / SRC-SYNC /
// ADC-DMA) round-trip through the same StateJSON/LoadStateJSON entry point with
// the same byte-identity and advance-parity guarantees.
func TestPeriphStateJSONRoundTripFullModels(t *testing.T) {
	p := &PeriphState{
		SourceTicksPerStep:          4,
		CompletedModelSteps:         40,
		CoreTickDeliveryRequests:    true,
		Timer0IRQDeliveryRequests:   true,
		SoftwareIRQDeliveryRequests: true,
		SrcSyncDeliveryRequests:     true,
		AudioAdcDmaDeliveryRequests: true,
	}

	// Watchdog: counting (bit 5), period 4 -> 16 units, no expiry yet.
	p.Watchdog, _ = NewWatchdogProjection([]int64{4, 1})
	p.Watchdog.Con = u32p(0x24)
	p.Watchdog.KeyPhase = 0
	p.Watchdog.Elapsed = 3
	p.Watchdog.Remainder = 2
	p.Watchdog.Commands = 2
	p.Watchdog.Feeds = 1

	// ICU: one pending command countdown plus busy reads/completed.
	p.ICU, _ = NewIcuInitializationProjection(u32p(0x1), nil, i64p(64), false)
	p.ICU.HasPending[0] = true
	p.ICU.Pending[0] = 3
	p.ICU.Completed = 1
	p.ICU.BusyReads = 14

	// Timer2: counting with fractional LRC/post-divisor remainders.
	p.Timer2 = NewTimer2Delay()
	p.Timer2.Con = u32p(0x1001)
	p.Timer2.Cnt = u32p(0)
	p.Timer2.Prd = u32p(9)
	p.Timer2.LrcRemainder = 7
	p.Timer2.Remainder = 3
	p.Timer2.Expirations = 2
	p.Timer2.Writes = 4
	p.Timer2.ConReads = 5
	p.Timer2.AdvanceCalls = 6

	// Software IRQ: known pending nibbles + source-enable bookkeeping.
	p.SoftwareIRQ, _ = NewSoftwareIrqCommands([]*uint32{u32p(8), u32p(1)}, []*uint32{u32p(0), u32p(2)})
	p.SoftwareIRQ.Commands = 3
	p.SoftwareIRQ.Acknowledgements = 2
	p.SoftwareIRQ.SourceCommands = 1

	// SRC-SYNC: started, 40-5=35 < 40000, so still started (not pending).
	p.SrcSync, _ = NewJLSrcSyncDelivery(40000)
	p.SrcSync.NoteStart(5)
	p.SrcSync.Advance(40)

	// ADC-DMA: delivery mode, pending.
	p.AudioAdcDma = NewAudioAdcDmaDelivery(true)
	p.AudioAdcDma.Pending = true

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
		t.Fatalf("full-model state transfer round-trip changed bytes:\n  first =%s\n  second=%s", first, second)
	}
	want, _ := p.AdvanceJSON(0x300)
	got, _ := fresh.AdvanceJSON(0x300)
	if string(want) != string(got) {
		t.Fatalf("full-model advance from transferred state diverged:\n  want=%s\n  got =%s", want, got)
	}
}

// TestPeriphStateJSONRoundTripCRC pins the CrcEngine round-trip through the same
// StateJSON/LoadStateJSON entry point (seeded value and unseeded nil crc) with
// byte-identity.
func TestPeriphStateJSONRoundTripCRC(t *testing.T) {
	p := &PeriphState{SourceTicksPerStep: 4, MulticoreEnabled: true}
	p.Crc = &CrcEngine{Crc: u32p(0x31c3)}
	first, err := p.StateJSON()
	if err != nil {
		t.Fatal(err)
	}
	fresh := &PeriphState{}
	if err := fresh.LoadStateJSON(first); err != nil {
		t.Fatal(err)
	}
	if fresh.Crc == nil || *fresh.Crc.Crc != 0x31c3 {
		t.Fatalf("crc round-trip mismatch: %+v", fresh.Crc)
	}
	second, err := fresh.StateJSON()
	if err != nil {
		t.Fatal(err)
	}
	if string(first) != string(second) {
		t.Fatalf("crc state transfer round-trip changed bytes:\n  first =%s\n  second=%s", first, second)
	}

	// Unseeded engine (Crc != nil with nil crc value) must round-trip as null,
	// not vanish.
	u := &PeriphState{SourceTicksPerStep: 4}
	u.Crc = &CrcEngine{}
	uFirst, err := u.StateJSON()
	if err != nil {
		t.Fatal(err)
	}
	uFresh := &PeriphState{}
	if err := uFresh.LoadStateJSON(uFirst); err != nil {
		t.Fatal(err)
	}
	if uFresh.Crc == nil || uFresh.Crc.Crc != nil {
		t.Fatalf("unseeded crc round-trip mismatch: %+v", uFresh.Crc)
	}
}
