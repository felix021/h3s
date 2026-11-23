package gosim

// MMIO word routing for the dual loop's scalar ld/st against the strict memory
// (mem.go Bus). It is the honest IRQ-controller slice of
// br28_model.BR28Model.handles/read/write: IPMASK and IRQ_CONFIG (plus their
// +0x800 core-1 views) read/write the already round-tripped IRQController
// state. Every address Python would treat as unmapped/unsupported (for example
// the banked 0xffe168 view, which hardware.handles() returns False for) stays
// unhandled so the strict memory faults back to Python and re-derives the
// identical Blocked. Nothing is fabricated and no side effect is guessed: a
// word is only routed when the Go model already carries the same register the
// Python model does.

const (
	irqConfigBase = 0xffe100
	ipmaskAddr    = 0xffe1a8
	irqConfigSpan = 128
	irq1Offset    = 0x800

	// CRC engine block (br28_model.HardwareCrcEngine, opt-in via the same
	// hardware_crc_projection flag Python gates on): 0xfd3500 feeds one byte,
	// 0xfd3504 loads the seed / returns the current CRC.
	crcEngineBase = 0xfd3500
	crcEnginePoly = 0x1021
)

func irqWordIndex(addr uint32) int { return int((addr - irqConfigBase) / 4) }

// CrcEngine is the opt-in hardware CRC engine functional-equivalence projection
// (br28_model.HardwareCrcEngine): the algorithm is the app software CRC-16 table
// parameters — polynomial 0x1021, MSB-first left shift, initial value = the
// written seed, no xor-out / reflection. Crc == nil means unseeded (the reset
// state is unknown; a feed or read before a seed is a strict stop). The Go side
// carries only the accumulator value: the report bookkeeping (seed_writes /
// fed_bytes / result_reads) and the general write ledger stay Python-side,
// re-derived by the replay of each committed feed/seed through c.mem.write, so a
// feed is applied exactly once per side (never twice to one accumulator).
type CrcEngine struct {
	Crc *uint32 // nil = unseeded (reset state unknown)
}

// handlesCRCWord reports whether addr is a CRC engine word (0xfd3500 feed or
// 0xfd3504 seed/result) with the projection enabled, matching br28_model's
// `crc_engine is not None and crc_engine.handles(address)` gate exactly.
func (p *PeriphState) handlesCRCWord(addr uint32) bool {
	return p.Crc != nil && (addr == crcEngineBase || addr == crcEngineBase+4)
}

// crcFeed mirrors HardwareCrcEngine._feed: crc ^= byte<<8 then eight rounds of
// MSB-first left shift with the 0x1021 polynomial.
func crcFeed(crc uint32, b byte) uint32 {
	crc ^= uint32(b) << 8
	for i := 0; i < 8; i++ {
		if crc&0x8000 != 0 {
			crc = ((crc << 1) ^ crcEnginePoly) & 0xffff
		} else {
			crc = (crc << 1) & 0xffff
		}
	}
	return crc
}

func (p *PeriphState) readCRC(addr uint32) (uint32, *MemError) {
	if addr == crcEngineBase {
		return 0, &MemError{Kind: "crc_data_port_read_not_modeled", Access: "read", Address: addr, Size: 4}
	}
	if p.Crc.Crc == nil {
		return 0, &MemError{Kind: "unknown_crc_engine_reset_state", Access: "read", Address: addr, Size: 4}
	}
	return *p.Crc.Crc, nil
}

func (p *PeriphState) writeCRC(addr uint32, value uint32) *MemError {
	if addr == crcEngineBase+4 {
		if value > 0xffff {
			return &MemError{Kind: "crc_seed_width_not_modeled", Access: "write", Address: addr, Size: 4}
		}
		v := value
		p.Crc.Crc = &v
		return nil
	}
	// addr == crcEngineBase (the feed port; any other CRC word never routes
	// here because handlesCRCWord only admits base/base+4).
	if value > 0xff {
		return &MemError{Kind: "crc_feed_width_not_modeled", Access: "write", Address: addr, Size: 4}
	}
	if p.Crc.Crc == nil {
		return &MemError{Kind: "crc_feed_before_seed_unknown", Access: "write", Address: addr, Size: 4}
	}
	v := crcFeed(*p.Crc.Crc, byte(value))
	p.Crc.Crc = &v
	return nil
}

// handlesIRQWord reports whether addr is one of the IRQ-controller words the Go
// periph models (core 0 always; core 1 only under multicore), matching the
// br28_model.handles() membership exactly.
func (p *PeriphState) handlesIRQWord(addr uint32) bool {
	if p.IRQ != nil {
		if irqConfigBase <= addr && addr < irqConfigBase+irqConfigSpan {
			return true
		}
		if addr == ipmaskAddr {
			return true
		}
	}
	if p.MulticoreEnabled && p.IRQ1 != nil {
		if irqConfigBase+irq1Offset <= addr && addr < irqConfigBase+irq1Offset+irqConfigSpan {
			return true
		}
		if addr == ipmaskAddr+irq1Offset {
			return true
		}
	}
	return false
}

// handlesTimer0ReadWord reports whether addr is the Timer0 CON word (0xfd0400)
// whose aligned 4-byte read the Go model now owns. The polling loop reads CON
// and checks bit 15; the read returns Con with 0x8000 projected while pending.
// The write/RMW side stays on Python (Timer0.write owns the W1C bit-14 and
// config side effects), so Handles (the write gate) deliberately excludes it.
func (p *PeriphState) handlesTimer0ReadWord(addr uint32) bool {
	return p.Timer != nil && addr == TIMER0
}

// Handles is the Bus.Handles method (the WRITE gate): aligned 4-byte words only,
// mirroring hardware.handles()'s size==4 and address%4==0 gate. It admits only
// the words whose store side effects Go owns (IRQ config / IPMASK / CRC); a
// Timer0 store/RMW is not admitted, so it faults back to Python and re-derives
// the identical Timer0.write effect there.
func (p *PeriphState) Handles(addr uint32, size int) bool {
	return size == 4 && addr%4 == 0 && (p.handlesIRQWord(addr) || p.handlesCRCWord(addr))
}

// HandlesRead is the Bus.HandlesRead method (the READ gate): the write route
// plus the read-only Timer0 CON word. Reads are gated separately so Timer0 CON
// reads stay in Go while its writes remain on the Python path.
func (p *PeriphState) HandlesRead(addr uint32, size int) bool {
	return size == 4 && addr%4 == 0 &&
		(p.handlesIRQWord(addr) || p.handlesCRCWord(addr) || p.handlesTimer0ReadWord(addr))
}

// Read is the Bus.Read method: the CRC result read, then the IRQ config / IPMASK
// readback. A nil register raises the same strict stop Python does (core-1 uses
// the distinct cpu1 kinds).
func (p *PeriphState) Read(addr uint32) (uint32, *MemError) {
	if p.handlesCRCWord(addr) {
		return p.readCRC(addr)
	}
	if p.handlesTimer0ReadWord(addr) {
		v, err := p.Timer.Read(0)
		if err != nil {
			// Timer0.Read's PeriphError (uninitialized_peripheral at 0xfd0400)
			// surfaces as a strict-memory fault; Python re-derives the identical
			// Blocked. In the observed trace CON is initialized before the poll.
			return 0, &MemError{Kind: err.Kind, Access: "read", Address: addr, Size: 4}
		}
		return v, nil
	}
	if p.MulticoreEnabled && addr >= irqConfigBase+irq1Offset {
		ic := p.IRQ1
		if addr == ipmaskAddr+irq1Offset {
			if ic.IPMask == nil {
				return 0, &MemError{Kind: "unknown_cpu1_ipmask", Access: "read", Address: addr, Size: 4}
			}
			return *ic.IPMask, nil
		}
		v := ic.Config[irqWordIndex(addr-irq1Offset)]
		if v == nil {
			return 0, &MemError{Kind: "unknown_cpu1_irq_configuration", Access: "read", Address: addr, Size: 4}
		}
		return *v, nil
	}
	ic := p.IRQ
	if addr == ipmaskAddr {
		if ic.IPMask == nil {
			return 0, &MemError{Kind: "uninitialized_peripheral", Access: "read", Address: addr, Size: 4}
		}
		return *ic.IPMask, nil
	}
	v := ic.Config[irqWordIndex(addr)]
	if v == nil {
		return 0, &MemError{Kind: "uninitialized_peripheral", Access: "read", Address: addr, Size: 4}
	}
	return *v, nil
}

// Write is the Bus.Write method: the CRC feed/seed store, then the IRQ config /
// IPMASK store (including the 0..7 priority-mask bound).
func (p *PeriphState) Write(addr uint32, value uint32) *MemError {
	if p.handlesCRCWord(addr) {
		return p.writeCRC(addr, value)
	}
	if p.MulticoreEnabled && addr >= irqConfigBase+irq1Offset {
		ic := p.IRQ1
		if addr == ipmaskAddr+irq1Offset {
			if value > 7 {
				return &MemError{Kind: "unsupported_cpu1_ipmask", Access: "write", Address: addr, Size: 4}
			}
			ic.IPMask = &value
			return nil
		}
		ic.Config[irqWordIndex(addr-irq1Offset)] = &value
		return nil
	}
	ic := p.IRQ
	if addr == ipmaskAddr {
		if value > 7 {
			return &MemError{Kind: "unsupported_ipmask", Access: "write", Address: addr, Size: 4}
		}
		ic.IPMask = &value
		return nil
	}
	ic.Config[irqWordIndex(addr)] = &value
	return nil
}

// ReadBytes returns no byte-granular fixture for the IRQ words.
func (p *PeriphState) ReadBytes(addr uint32, size int) []byte { return nil }

// HandlesByteWrite reports no modeled byte register.
func (p *PeriphState) HandlesByteWrite(addr uint32) bool { return false }

// WriteByteAt is unreachable (HandlesByteWrite always returns false).
func (p *PeriphState) WriteByteAt(addr uint32, b byte) *MemError { return nil }
