package gosim

import "testing"

// The dual-core advance scan (periph.go Advance) must walk both cores for the
// IRQ3 core tick, IRQ4 Timer0 and IRQ124-127 software scans, using the per-core
// IRQ controller (IRQ/IRQ1) and the per-core ICFG gate (CoreIcfg), with the
// CPU1 held-projection gate for core 1. These tests pin the exact Blocked JSON
// shapes against the Python BR28Model.advance raise sites.

// dualPeriph returns a multicore PeriphState with both controllers and both
// per-core gates known (0x300), CPU1 released (control 1).
func dualPeriph() *PeriphState {
	p := &PeriphState{
		MulticoreEnabled:   true,
		IRQ:                NewIRQController(),
		IRQ1:               NewIRQController(),
		CoreIcfg:           [2]*uint32{u32p(0x300), u32p(0x300)},
		Cpu1Control:        u32p(1),
		Cpu1HeldProjection: true,
	}
	return p
}

// A core-tick overflow on core 1 (released) throws IRQ3 targeting core 1.
func TestDualCoreAdvanceCore1TickOverflow(t *testing.T) {
	p := dualPeriph()
	p.CoreTickDeliveryRequests = true
	ct, _ := NewCoreTickStopped(u32p(0), i64p(4), false, false, false)
	ct.Write(8, 3) // prd 3 -> period 4
	ct.Write(4, 3) // cnt 3 -> next advance overflows
	ct.Write(0, 0x1)
	p.CoreTicks[1] = ct
	p.IRQ1.Config[0] = u32p(0x5000) // irq 3 nibble: enabled, priority 2
	p.IRQ1.IPMask = u32p(2)
	res := p.Advance(0x300)
	if res.Irq == nil || res.Irq.Core != 1 || res.Irq.Irq != 3 {
		t.Fatalf("expected IRQ3 on core 1, got %+v", res)
	}
	if res.Err != nil {
		t.Fatalf("unexpected strict stop %v", res.Err)
	}
}

// A core-1 tick that counts without the held projection is a strict stop
// mirroring BR28Model.advance's core1_execution_state_unknown.
func TestDualCoreAdvanceCore1HeldUnknown(t *testing.T) {
	p := dualPeriph()
	p.Cpu1HeldProjection = false
	p.Cpu1Control = nil
	ct, _ := NewCoreTickStopped(u32p(0), i64p(4), false, false, false)
	ct.Write(8, 3)
	ct.Write(4, 0)
	ct.Write(0, 0x1)
	p.CoreTicks[1] = ct
	res := p.Advance(0x300)
	if res.Err == nil || res.Err.JSON() != `{"kind":"core1_execution_state_unknown"}` {
		t.Fatalf("expected core1_execution_state_unknown, got %+v", res)
	}
}

// Timer0 pending under multicore delivers to the first eligible core (core 0),
// then the shared delivery_in_flight blocks core 1 until the handler ACKs.
func TestDualCoreAdvanceTimer0FirstEligibleCore(t *testing.T) {
	p := dualPeriph()
	p.Timer0IRQDeliveryRequests = true
	p.Timer = &Timer0{Pending: true, DeliveryInFlight: false}
	p.IRQ.Config[0] = u32p(0x50000) // irq 4 nibble: enabled, priority 2
	p.IRQ.IPMask = u32p(2)
	p.IRQ1.Config[0] = u32p(0x50000) // same on core 1
	p.IRQ1.IPMask = u32p(2)
	res := p.Advance(0x300)
	if res.Irq == nil || res.Irq.Core != 0 || res.Irq.Irq != 4 {
		t.Fatalf("expected IRQ4 on core 0 first, got %+v", res)
	}
	if !p.Timer.DeliveryInFlight {
		t.Fatal("timer delivery_in_flight should be set")
	}
	// A second advance with the same pending slice must not re-raise.
	res2 := p.Advance(0x300)
	if res2.Irq != nil || res2.Err != nil {
		t.Fatalf("in-flight pending slice must not re-raise, got %+v", res2)
	}
}

// Timer0 pending with an unknown core-0 gate is pending_irq_with_unknown_core_icfg.
func TestDualCoreAdvanceTimer0UnknownGate(t *testing.T) {
	p := dualPeriph()
	p.Timer = &Timer0{Pending: true}
	p.CoreIcfg[0] = nil
	p.IRQ.Config[0] = u32p(0x50000)
	p.IRQ.IPMask = u32p(2)
	res := p.Advance(0x300)
	want := `{"core":0,"irq":4,"kind":"pending_irq_with_unknown_core_icfg"}`
	if res.Err == nil || res.Err.JSON() != want {
		t.Fatalf("expected %s, got %+v", want, res)
	}
}

// Software IRQ pending on core 1 (released) is scanned and, when eligible,
// throws a core-1 delivery request.
func TestDualCoreAdvanceSoftwareIrqCore1(t *testing.T) {
	p := dualPeriph()
	p.SoftwareIRQDeliveryRequests = true
	s, _ := NewSoftwareIrqCommands([]*uint32{u32p(0), u32p(1)}, nil)
	p.SoftwareIRQ = s
	// core 0 has no pending; core 1 pending bit0 = irq 124+0 = 124.
	p.IRQ1.Config[15] = u32p(0x50000) // irq 124 nibble 4: enabled, priority 2
	p.IRQ1.IPMask = u32p(2)
	res := p.Advance(0x300)
	if res.Irq == nil || res.Irq.Core != 1 || res.Irq.Irq != 124 {
		t.Fatalf("expected software IRQ124 on core 1, got %+v", res)
	}
}

// Software IRQ with an unknown per-core gate is software_irq_with_unknown_icfg.
func TestDualCoreAdvanceSoftwareIrqUnknownGate(t *testing.T) {
	p := dualPeriph()
	p.SoftwareIRQDeliveryRequests = true
	s, _ := NewSoftwareIrqCommands([]*uint32{u32p(0), u32p(1)}, nil)
	p.SoftwareIRQ = s
	p.CoreIcfg[1] = nil
	p.IRQ1.Config[15] = u32p(0x50000)
	p.IRQ1.IPMask = u32p(2)
	res := p.Advance(0x300)
	want := `{"core":1,"irq":124,"kind":"software_irq_with_unknown_icfg"}`
	if res.Err == nil || res.Err.JSON() != want {
		t.Fatalf("expected %s, got %+v", want, res)
	}
}

// Software IRQ whose eligibility raises unmodeled_unmaskable_irq is re-wrapped
// with the per-core detail (source/pending_known/icfg_gate_demand) under
// multicore, matching BR28Model.advance's special case.
func TestDualCoreAdvanceSoftwareIrqUnmaskableRwrap(t *testing.T) {
	p := dualPeriph()
	s, _ := NewSoftwareIrqCommands([]*uint32{u32p(0), u32p(1)}, nil)
	p.SoftwareIRQ = s
	p.CoreIcfg[1] = u32p(0x300)
	// irq 124 nibble: enabled, priority 7 (unmaskable, projection off).
	p.IRQ1.Config[15] = u32p(0xf0000)
	p.IRQ1.IPMask = u32p(1)
	res := p.Advance(0x300)
	want := `{"core":1,"icfg_gate_demand":"0x300","irq":124,"kind":"unmodeled_unmaskable_irq","pending_known":true,"priority":7,"source":"software_irq"}`
	if res.Err == nil || res.Err.JSON() != want {
		t.Fatalf("expected %s, got %+v", want, res)
	}
}
