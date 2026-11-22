package gosim

// Partial-ICFG abstract algebra, the Go twin of tools/br28_icfg.py's
// IcfgState/IcfgValue plus probe_boot's partial_icfg instruction handlers
// (step_body "partial_icfg" branch). It only models the subset the evidence
// pins: SR read/write of the ICFG, bit-9 CLI/STI updates, immediate AND/OR
// masks, the low-byte extract, and the two masked compare-and-branch forms.
// Everything else (an IcfgValue consumed as a full numeric operand, a literal
// write outside 0/0x200/0x300, a cross-core / stale-generation write) is
// handed back to Python, which re-derives the identical Blocked.
//
// The per-core state lives on Core (ICFG / ICFGKnown / ICFGKnownMask are the
// value / fully-known flag / full 32-bit known mask of br28_icfg.IcfgState;
// ICFGStatusGen / ICFGWrites / ICFGBit9Updates are the generation and the two
// counters the report reads back). A register carrying an IcfgValue rides the
// RegTag side table (kind "icfg_value"), exactly like br28_icfg.IcfgValue.

const icfgGates uint32 = 0x300

// icfgRead mirrors IcfgState.read(): the IcfgValue snapshot (origin =
// (core, status_generation), identity = full).
func icfgRead(c *Core) *RegTag {
	return &RegTag{
		Kind:     "icfg_value",
		Value:    c.ICFG,
		Known:    c.ICFGKnownMask,
		Origin:   &[2]int{c.ID, c.ICFGStatusGen},
		Identity: wordMask,
	}
}

// icfgWriteInt applies IcfgState.write(source int). Only 0/0x200/0x300 are
// pinned literals; anything else is blocked (handed back to Python).
func icfgWriteInt(c *Core, source uint32) bool {
	if source != 0 && source != 0x200 && source != 0x300 {
		return false
	}
	value, known := source, icfgGates
	c.ICFG = (c.ICFG &^ icfgGates) | (value & known & icfgGates)
	c.ICFGKnownMask = (c.ICFGKnownMask &^ icfgGates) | known
	c.ICFGKnown = c.ICFGKnownMask == wordMask
	c.ICFGWrites++
	return true
}

// icfgWriteTag applies IcfgState.write(source IcfgValue). It requires the same
// core + status_generation origin and identity preserved outside the gate bits
// (0x300); a cross-core or stale-generation tag is blocked.
func icfgWriteTag(c *Core, tag *RegTag) bool {
	if tag.Origin == nil {
		return false
	}
	origin := *tag.Origin
	if origin != [2]int{c.ID, c.ICFGStatusGen} {
		return false
	}
	if tag.Identity&^icfgGates != ^icfgGates {
		return false
	}
	known := tag.Known & icfgGates
	c.ICFG = (c.ICFG &^ icfgGates) | (tag.Value & known & icfgGates)
	c.ICFGKnownMask = (c.ICFGKnownMask &^ icfgGates) | known
	c.ICFGKnown = c.ICFGKnownMask == wordMask
	c.ICFGWrites++
	return true
}

// icfgBit9 mirrors IcfgState.bit9(value, known): set bit 9 to value (masked to
// bit 9) with the given knownness.
func icfgBit9(c *Core, value uint32, known bool) {
	if known {
		c.ICFG = (c.ICFG &^ 0x200) | (value & 0x200)
		c.ICFGKnownMask |= 0x200
	} else {
		c.ICFG &^= 0x200
		c.ICFGKnownMask &^= 0x200
	}
	c.ICFGKnown = c.ICFGKnownMask == wordMask
	c.ICFGBit9Updates++
}

// icfgGateDemandChanged reports whether an ICFG write to (value, known) changes
// the select-side gate demand. The dual loop's periph advance uses the periph
// state (ipmask, pending, irq config) that is snapshotted once at fill start,
// while a committed cli/sti/mov.sr_r changes the gate mid-fill. A mid-fill gate
// change would evaluate delivery eligibility with an inconsistent (gate,
// ipmask) pair that Python's step-by-step loop never observes (Python re-runs
// select, and thus core_icfg, after every instruction). Handing the write back
// keeps the gate change at a fill boundary, exactly matching Python.
func icfgGateDemandChanged(v0, k0, v1, k1 uint32) bool {
	a, b := gateDemand(v0, k0), gateDemand(v1, k1)
	if a == nil || b == nil {
		return a != b
	}
	return *a != *b
}

// icfgWriteIntAfter returns the post-write (value, known) of icfgWriteInt
// without mutating the core. ok is false when the literal is unpinned (handed
// back to Python).
func icfgWriteIntAfter(c *Core, source uint32) (uint32, uint32, bool) {
	if source != 0 && source != 0x200 && source != 0x300 {
		return 0, 0, false
	}
	value, known := source, icfgGates
	return (c.ICFG &^ icfgGates) | (value & known & icfgGates), (c.ICFGKnownMask &^ icfgGates) | known, true
}

// icfgWriteTagAfter is icfgWriteTag's post-write (value, known) without the
// mutation; ok is false when the write is blocked (handed back).
func icfgWriteTagAfter(c *Core, tag *RegTag) (uint32, uint32, bool) {
	if tag.Origin == nil || *tag.Origin != [2]int{c.ID, c.ICFGStatusGen} {
		return 0, 0, false
	}
	if tag.Identity&^icfgGates != ^icfgGates {
		return 0, 0, false
	}
	known := tag.Known & icfgGates
	return (c.ICFG &^ icfgGates) | (tag.Value & known & icfgGates), (c.ICFGKnownMask &^ icfgGates) | known, true
}

// icfgBit9After is icfgBit9's post-write (value, known) without the mutation.
func icfgBit9After(c *Core, value uint32, known bool) (uint32, uint32) {
	if known {
		return (c.ICFG &^ 0x200) | (value & 0x200), c.ICFGKnownMask | 0x200
	}
	return c.ICFG &^ 0x200, c.ICFGKnownMask &^ 0x200
}

// icfgMask mirrors IcfgValue.mask: a pure bit-selection that keeps unknown
// bits unknown and re-derives the identity mask.
func icfgMask(value, known, identity uint32, op string, operand uint32) (uint32, uint32, uint32) {
	operand &= wordMask
	if op == "and" {
		return value & operand, known | (^operand), identity & operand
	}
	// "or"
	return value | operand, known | operand, identity &^ operand
}

// icfgFoldToInt mirrors the partial_icfg_pure_bit_mask fold: a fully-known mask
// result whose identity outside the gate bits is lost is a plain numeric
// extraction (never writable back to the ICFG), so it folds to an int.
func icfgFoldToInt(value, known, identity uint32) (uint32, bool) {
	if known == wordMask && identity&^icfgGates != ^icfgGates {
		return value, true
	}
	return 0, false
}

// icfgEvent renders the special-read/special-write memory event an ICFG step
// emits (probe_boot's event({'access':..., 'register':'icfg', ...})).
type icfgEvent struct {
	Access     string `json:"access"`
	KnownMask  string `json:"known_mask"`
	KnownValue string `json:"known_value"`
}

func icfgReadEvent(c *Core) icfgEvent {
	return icfgEvent{Access: "special-read", KnownMask: hexWord(c.ICFGKnownMask), KnownValue: hexWord(c.ICFG)}
}

func icfgWriteEvent(c *Core) icfgEvent {
	return icfgEvent{Access: "special-write", KnownMask: hexWord(c.ICFGKnownMask), KnownValue: hexWord(c.ICFG)}
}
