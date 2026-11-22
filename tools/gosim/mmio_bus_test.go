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
