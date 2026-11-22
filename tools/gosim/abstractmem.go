package gosim

// Abstract memory word transport, the Go twin of probe_boot.Memory's
// abstract_word / abstract_write_target / preflight_abstract_word /
// write_abstract_word / write_abstract_byte. These are the source-memory
// adapters the partial-ICFG spill/reload and the abstract task-stack transport
// run on: known RAM bytes + unknown_origins provenance + icfg_spills overlay.
//
// MMU: the Go strict Memory has no page-table model. Python translates virtual
// [0x400000, 0x800000) through hardware.mmu and everything else identically.
// The dual loop's stack/spill addresses all live below 0x400000, so this port
// treats the MMU as identity and refuses (honestly) any address inside the
// virtual window rather than fabricating a translation.

// spillToTag converts a resident IcfgSpill into the register-side IcfgValue
// tag (abstract_word's icfg_spills hit).
func spillToTag(sp *IcfgSpill) *RegTag {
	return &RegTag{
		Kind:     "icfg_value",
		Value:    sp.Value,
		Known:    sp.Known,
		Identity: sp.Identity,
		Origin:   sp.Origin,
	}
}

func tagToSpill(tag *RegTag) *IcfgSpill {
	return &IcfgSpill{
		Value:    tag.Value,
		Known:    tag.Known,
		Identity: tag.Identity,
		Origin:   tag.Origin,
	}
}

// abstractWord mirrors Memory.abstract_word(address, task_frame=...). Returns
// either a concrete int (tag == nil) or an abstract value tag; a non-nil error
// is a strict stop the caller hands back to Python.
func (m *Memory) abstractWord(addr uint32, taskFrame bool) (uint32, *RegTag, *MemError) {
	if 0x400000 <= addr && addr < 0x800000 {
		return 0, nil, &MemError{Kind: "abstract_mmu_translate_unmodeled", Access: "read", Address: addr, Size: 4}
	}
	physical := addr
	ua := uint64(physical)
	// Fully inside ROM / app / readonly mirror, or an MMIO word: a plain
	// concrete read (Python returns int.from_bytes(self.read(address,4))).
	if (m.rom != nil && ua+4 <= uint64(len(m.rom))) ||
		(m.entry <= physical && ua+4 <= uint64(m.entry)+uint64(len(m.app))) ||
		m.inReadonly(physical, 4) ||
		(m.bus != nil && m.bus.Handles(physical, 4)) {
		b, err := m.Read(addr, 4)
		if err != nil {
			return 0, nil, err
		}
		return leUint32(b), nil, nil
	}
	// Any partial overlap with a readonly mirror is a hard stop.
	if (m.rom != nil && uint64(len(m.rom)) > ua) ||
		(ua < uint64(m.entry)+uint64(len(m.app)) && ua+4 > uint64(m.entry)) ||
		m.crossesReadonly(physical, 4) {
		return 0, nil, &MemError{Kind: "abstract_read_crosses_readonly_boundary", Access: "read", Address: physical, Size: 4}
	}
	// Not RAM: a plain concrete read.
	if m.region(physical, 4) == "" {
		b, err := m.Read(addr, 4)
		if err != nil {
			return 0, nil, err
		}
		return leUint32(b), nil, nil
	}
	// A resident spill wins.
	if sp, ok := m.icfgSpills[physical]; ok {
		return 0, spillToTag(sp), nil
	}
	values := make([]*uint8, 4)
	allKnown := true
	for i := 0; i < 4; i++ {
		if b, ok := m.ram[physical+uint32(i)]; ok {
			v := b
			values[i] = &v
		} else {
			allKnown = false
		}
	}
	if allKnown {
		var v uint32
		for i := 0; i < 4; i++ {
			v |= uint32(*values[i]) << (8 * i)
		}
		return v, nil, nil
	}
	// A byte that is still unknown must have provenance unless this is a
	// task-frame load; otherwise preserve the ordinary strict failure.
	for i := 0; i < 4; i++ {
		if values[i] == nil && !taskFrame {
			if _, ok := m.unknownOrigins[physical+uint32(i)]; !ok {
				_, err := m.Read(addr, 4)
				if err != nil {
					return 0, nil, err
				}
			}
		}
	}
	tag := &RegTag{Kind: "unknown_word", Bytes: make([]*uint8, 4), Origins: make([]*uint32, 4)}
	for i := 0; i < 4; i++ {
		if values[i] != nil {
			tag.Bytes[i] = values[i]
			tag.Origins[i] = nil
		} else {
			tag.Bytes[i] = nil
			var o uint32
			if v, ok := m.unknownOrigins[physical+uint32(i)]; ok {
				o = v
			} else {
				o = physical + uint32(i)
			}
			tag.Origins[i] = &o
		}
	}
	return 0, tag, nil
}

// abstractWriteTarget mirrors Memory.abstract_write_target(address, size).
func (m *Memory) abstractWriteTarget(addr uint32, size int) (uint32, *MemError) {
	if 0x400000 <= addr && addr < 0x800000 {
		return 0, &MemError{Kind: "abstract_mmu_translate_unmodeled", Access: "write", Address: addr, Size: size}
	}
	physical := addr
	ua := uint64(physical)
	un := uint64(size)
	protected := (m.rom != nil && uint64(len(m.rom)) > ua && ua+un > 0) ||
		(ua < uint64(m.entry)+uint64(len(m.app)) && ua+un > uint64(m.entry)) ||
		m.crossesReadonly(physical, size) ||
		(m.bus != nil && m.bus.Handles(physical, size))
	if protected || m.region(physical, size) == "" {
		return 0, &MemError{Kind: "abstract_store_requires_ram", Access: "write", Address: physical, Size: size}
	}
	return physical, nil
}

// preflightAbstractWord mirrors Memory.preflight_abstract_word: validate the
// complete destination before any byte changes (the PUSH atomicity gate).
// value is int (tag == nil) or an abstract tag.
func (m *Memory) preflightAbstractWord(addr uint32, tag *RegTag) (uint32, *MemError) {
	physical, err := m.abstractWriteTarget(addr, 4)
	if err != nil {
		return 0, err
	}
	if tag != nil && tag.Kind == "icfg_value" && physical%4 != 0 {
		return 0, &MemError{Kind: "partial_icfg_spill_unaligned", Access: "write", Address: physical, Size: 4}
	}
	if tag != nil && tag.Kind == "unknown_word" {
		for base := range m.icfgSpills {
			if physical < base+4 && physical+4 > base && physical != base {
				return 0, &MemError{Kind: "mixed_abstract_spill_unaligned", Access: "write", Address: physical, Size: 4}
			}
		}
	}
	return physical, nil
}

// writeAbstractWord mirrors Memory.write_abstract_word(address, value). value
// is a concrete int (tag == nil) or an abstract tag. Concrete ints take the
// ordinary write path; abstract values overlay ram / unknown_origins /
// icfg_spills. It does not emit the event here — the caller (Python replay)
// emits the identical event so the report stays byte-identical.
func (m *Memory) writeAbstractWord(addr uint32, valInt uint32, tag *RegTag) *MemError {
	if tag == nil {
		return m.Write(addr, wordBytes(valInt))
	}
	physical, err := m.preflightAbstractWord(addr, tag)
	if err != nil {
		return err
	}
	if tag.Kind == "icfg_value" {
		m.icfgSpills[physical] = tagToSpill(tag)
		for i := 0; i < 4; i++ {
			a := physical + uint32(i)
			mask := uint32(0xff) << (8 * i)
			if tag.Known&mask == mask {
				m.ram[a] = byte(tag.Value >> (8 * i))
			} else {
				delete(m.ram, a)
			}
			delete(m.unknownOrigins, a)
		}
		return nil
	}
	// unknown_word
	delete(m.icfgSpills, physical)
	for i := 0; i < 4; i++ {
		a := physical + uint32(i)
		b := tag.Bytes[i]
		if b == nil {
			delete(m.ram, a)
			origin := uint32(0)
			if tag.Origins[i] != nil {
				origin = *tag.Origins[i]
			}
			m.unknownOrigins[a] = origin
		} else {
			m.ram[a] = *b
			delete(m.unknownOrigins, a)
		}
	}
	return nil
}

// inReadonly reports full containment of [addr, addr+size) in a readonly mirror.
func (m *Memory) inReadonly(addr uint32, size int) bool {
	ua, un := uint64(addr), uint64(size)
	for _, r := range m.readonly {
		if uint64(r.Base) <= ua && ua+un <= uint64(r.Base)+uint64(len(r.Data)) {
			return true
		}
	}
	return false
}

// crossesReadonly reports any partial overlap with a readonly mirror.
func (m *Memory) crossesReadonly(addr uint32, size int) bool {
	ua, un := uint64(addr), uint64(size)
	for _, r := range m.readonly {
		if ua < uint64(r.Base)+uint64(len(r.Data)) && ua+un > uint64(r.Base) {
			return true
		}
	}
	return false
}
