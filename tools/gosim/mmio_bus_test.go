package gosim

import "testing"

// TestMMIOBusIRQRouting pins the IRQ-controller MMIO routing (the strict-memory
// Bus): IPMASK / IRQ_CONFIG words for core 0, and their +0x800 views for core 1
// under multicore. The 0xffe900..0xffe97c core-1 config window is the boundary
// that must not fall into the core-0 branch (index would run past 32).
func TestMMIOBusIRQRouting(t *testing.T) {
	p := &PeriphState{MulticoreEnabled: true}
	p.IRQ = NewIRQController()
	p.IRQ1 = NewIRQController()
	ip0 := uint32(3)
	p.IRQ.IPMask = &ip0
	ip1 := uint32(5)
	p.IRQ1.IPMask = &ip1

	// core-0 IPMASK write/read
	if !p.Handles(ipmaskAddr, 4) {
		t.Fatal("core-0 IPMASK not handled")
	}
	if err := p.Write(ipmaskAddr, 7); err != nil {
		t.Fatal(err)
	}
	if v, err := p.Read(ipmaskAddr); err != nil || v != 7 {
		t.Fatalf("core-0 ipmask read = %#x, %v", v, err)
	}

	// core-1 IPMASK write/read (0xffe9a8)
	if !p.Handles(ipmaskAddr+irq1Offset, 4) {
		t.Fatal("core-1 IPMASK not handled")
	}
	if err := p.Write(ipmaskAddr+irq1Offset, 6); err != nil {
		t.Fatal(err)
	}
	if v, err := p.Read(ipmaskAddr+irq1Offset); err != nil || v != 6 {
		t.Fatalf("core-1 ipmask read = %#x, %v", v, err)
	}

	// core-1 config window edges (0xffe900..0xffe97c) — must stay in-bounds.
	for _, addr := range []uint32{irqConfigBase + irq1Offset, irqConfigBase + irq1Offset + 0x7c} {
		if !p.Handles(addr, 4) {
			t.Fatalf("core-1 config %#x not handled", addr)
		}
		if err := p.Write(addr, 0xdeadbeef); err != nil {
			t.Fatalf("core-1 config write %#x: %v", addr, err)
		}
		if v, err := p.Read(addr); err != nil || v != 0xdeadbeef {
			t.Fatalf("core-1 config read %#x = %#x, %v", addr, v, err)
		}
	}

	// core-0 config index 0 / last
	for _, addr := range []uint32{irqConfigBase, irqConfigBase + 0x7c} {
		if !p.Handles(addr, 4) {
			t.Fatalf("core-0 config %#x not handled", addr)
		}
		if err := p.Write(addr, 0x1234); err != nil {
			t.Fatalf("core-0 config write %#x: %v", addr, err)
		}
		if v, err := p.Read(addr); err != nil || v != 0x1234 {
			t.Fatalf("core-0 config read %#x = %#x, %v", addr, v, err)
		}
	}

	// out-of-range writes must fault (unsupported_ipmask), not index panic.
	if err := p.Write(ipmaskAddr, 8); err == nil || err.Kind != "unsupported_ipmask" {
		t.Fatalf("ipmask >7 write did not fault: %v", err)
	}

	// unaligned / non-word sizes are not handled.
	if p.Handles(ipmaskAddr, 1) || p.Handles(ipmaskAddr+1, 4) {
		t.Fatal("unaligned or byte-sized IRQ access wrongly handled")
	}
}

// TestMMIOBusCRCRouting pins the CRC-engine MMIO routing and functional
// projection against the br28_model.HardwareCrcEngine semantics (polynomial
// 0x1021, MSB-first left shift, initial value = written seed): the known
// CRC-16/CCITT vector and the strict stops (unseeded read / feed before seed /
// width bounds / data-port read / unmodeled register).
func TestMMIOBusCRCRouting(t *testing.T) {
	p := &PeriphState{Crc: &CrcEngine{}}
	if !p.Handles(crcEngineBase, 4) || !p.Handles(crcEngineBase+4, 4) {
		t.Fatal("CRC engine words not handled")
	}
	// unseeded read is a strict stop.
	if _, err := p.Read(crcEngineBase + 4); err == nil || err.Kind != "unknown_crc_engine_reset_state" {
		t.Fatalf("unseeded result read = %v", err)
	}
	// feed before seed is a strict stop.
	if err := p.Write(crcEngineBase, 0); err == nil || err.Kind != "crc_feed_before_seed_unknown" {
		t.Fatalf("feed before seed = %v", err)
	}
	// seed width bound.
	if err := p.Write(crcEngineBase+4, 0x10000); err == nil || err.Kind != "crc_seed_width_not_modeled" {
		t.Fatalf("seed width = %v", err)
	}
	// data port read is a strict stop.
	if _, err := p.Read(crcEngineBase); err == nil || err.Kind != "crc_data_port_read_not_modeled" {
		t.Fatalf("data port read = %v", err)
	}

	// known vector: seed 0, feed "123456789" -> 0x31c3 (CRC-16/XMODEM).
	if err := p.Write(crcEngineBase+4, 0); err != nil {
		t.Fatal(err)
	}
	for _, b := range []byte("123456789") {
		if err := p.Write(crcEngineBase, uint32(b)); err != nil {
			t.Fatal(err)
		}
	}
	v, err := p.Read(crcEngineBase + 4)
	if err != nil || v != 0x31c3 {
		t.Fatalf("123456789 CRC = %#x, %v", v, err)
	}
	// seed reads back before any feed.
	if err := p.Write(crcEngineBase+4, 0xffff); err != nil {
		t.Fatal(err)
	}
	if v, err := p.Read(crcEngineBase + 4); err != nil || v != 0xffff {
		t.Fatalf("seed readback = %#x, %v", v, err)
	}
	if err := p.Write(crcEngineBase, 0x100); err == nil || err.Kind != "crc_feed_width_not_modeled" {
		t.Fatalf("feed width = %v", err)
	}
	// a write to an unmodeled CRC word is not handled (faults back to Python).
	if p.Handles(crcEngineBase+8, 4) {
		t.Fatal("unmodeled CRC word wrongly handled")
	}

	// disabled projection (nil Crc) does not handle the words.
	d := &PeriphState{}
	if d.Handles(crcEngineBase, 4) || d.Handles(crcEngineBase+4, 4) {
		t.Fatal("disabled CRC projection wrongly handled")
	}
	// unaligned / non-word sizes are not handled.
	if p.Handles(crcEngineBase, 1) || p.Handles(crcEngineBase+1, 4) {
		t.Fatal("unaligned or byte-sized CRC access wrongly handled")
	}
}

// TestMMIOBusTimer0ReadRouting pins the Timer0 CON read-only route (fourth
// wiring): a read of 0xfd0400 returns Con with 0x8000 projected while pending,
// and is gated by HandlesRead only — Handles (the write gate) does not admit it,
// so a store/RMW still faults back to Python. Uninitialized CON reads surface
// the same strict stop as br28_model.Timer0.read.
func TestMMIOBusTimer0ReadRouting(t *testing.T) {
	p := &PeriphState{Timer: NewTimer0()}
	// write CNT/PRD/CON through the Go Timer0 model (as the Python world does;
	// a running CON write requires CNT/PRD already present).
	if err := p.Timer.Write(4, 5); err != nil {
		t.Fatal(err)
	}
	if err := p.Timer.Write(8, 119999); err != nil {
		t.Fatal(err)
	}
	if err := p.Timer.Write(0, 0x1001); err != nil {
		t.Fatal(err)
	}

	if !p.HandlesRead(TIMER0, 4) {
		t.Fatal("Timer0 CON read not handled")
	}
	v, err := p.Read(TIMER0)
	if err != nil || v != 0x1001 {
		t.Fatalf("Timer0 CON read = %#x, %v", v, err)
	}

	// pending projects bit 15, matching br28_model.Timer0.read.
	p.Timer.Pending = true
	if v, err := p.Read(TIMER0); err != nil || v != 0x9001 {
		t.Fatalf("pending Timer0 CON read = %#x, %v", v, err)
	}
	p.Timer.Pending = false

	// CNT / PRD reads are not routed (CON only).
	if p.HandlesRead(TIMER0+4, 4) || p.HandlesRead(TIMER0+8, 4) {
		t.Fatal("Timer0 CNT/PRD read wrongly handled")
	}
	// the write gate does not admit the CON word: stores/RMW stay on Python.
	if p.Handles(TIMER0, 4) {
		t.Fatal("Timer0 CON write wrongly handled")
	}
	// unaligned / byte-sized reads are not handled.
	if p.HandlesRead(TIMER0, 1) || p.HandlesRead(TIMER0+1, 4) {
		t.Fatal("unaligned or byte-sized Timer0 read wrongly handled")
	}
	// no Timer0 model -> the read word is not handled.
	d := &PeriphState{}
	if d.HandlesRead(TIMER0, 4) {
		t.Fatal("Timer0 read handled without a Timer0 model")
	}
	// uninitialized CON read surfaces the identical strict stop.
	u := &PeriphState{Timer: NewTimer0()}
	if _, err := u.Read(TIMER0); err == nil || err.Kind != "uninitialized_peripheral" {
		t.Fatalf("uninitialized Timer0 CON read = %v", err)
	}
}

// TestMMUTranslate pins the virtual-window translation against the br28_model
// MMU semantics: identity outside [0x400000, 0x800000), page-table walk inside.
func TestMMUTranslate(t *testing.T) {
	m := NewMemory()
	m.AddRegion(0x120000, 0x122800, "mmu_page_table")
	m.AddRegion(0x19fb80, 0x1a0000, "physical_heap")
	m.AddRegion(0x123000, 0x124000, "bootstrap_stack")
	// page 0 (virtual 0x400000) -> physical page 0x19fb80>>7 = 0x33f7, but the
	// tag encodes absolute page number in bits 14:0 with bit15 valid. Physical
	// base 0x19fb80 = page 0x33f7. tag = 0x8000 | 0x33f7.
	tag0 := uint32(0x8000 | ((0x19fb80 >> 7) & 0x7fff))
	m.ram[0x120000] = byte(tag0)
	m.ram[0x120001] = byte(tag0 >> 8)
	con := uint32(1)
	begin := uint32(0x120000)
	end := uint32(0x122800)
	m.SetMMU(&con, &begin, &end)

	// physical byte at 0x19fb80 (mapped from virtual 0x400000) is known.
	m.ram[0x19fb80] = 0xab

	b, err := m.Read(0x400000, 1)
	if err != nil {
		t.Fatal(err)
	}
	if b[0] != 0xab {
		t.Fatalf("translated read byte = %#x", b[0])
	}

	// identity below the window.
	m.ram[0x123000] = 0xcd
	b, err = m.Read(0x123000, 1)
	if err != nil || b[0] != 0xcd {
		t.Fatalf("identity read = %#x, %v", b, err)
	}

	// disabled MMU faults virtual access.
	off := uint32(0)
	m.SetMMU(&off, &begin, &end)
	if _, err := m.Read(0x400000, 4); err == nil || err.Kind != "virtual_access_with_mmu_disabled" {
		t.Fatalf("disabled-MMU virtual read = %v", err)
	}
}
