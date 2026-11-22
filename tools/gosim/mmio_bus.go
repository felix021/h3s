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
)

func irqWordIndex(addr uint32) int { return int((addr - irqConfigBase) / 4) }

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

// Handles is the Bus.Handles method: aligned 4-byte words only, mirroring
// hardware.handles()'s size==4 and address%4==0 gate.
func (p *PeriphState) Handles(addr uint32, size int) bool {
	return size == 4 && addr%4 == 0 && p.handlesIRQWord(addr)
}

// Read is the Bus.Read method: the IRQ config / IPMASK readback. A nil
// register raises the same strict stop Python does (core-1 uses the distinct
// cpu1 kinds).
func (p *PeriphState) Read(addr uint32) (uint32, *MemError) {
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

// Write is the Bus.Write method: the IRQ config / IPMASK store (including the
// 0..7 priority-mask bound).
func (p *PeriphState) Write(addr uint32, value uint32) *MemError {
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
