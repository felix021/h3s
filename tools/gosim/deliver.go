package gosim

// deliver.go: M6 Stage C — the five IRQ delivery-entry projections and their
// five symmetric RTI returns, ported from br28_machine.DualCoreMachine
// (deliver_software_irq / deliver_core_tick / deliver_timer0_irq /
// deliver_audio_dma_irq / deliver_src_sync_irq and return_*). This is the last
// machine-side piece: with the deliver projections in Go, Python is left with
// scene/fixture/snapshot/report/differential-only responsibilities.
//
// Red line (docs/gosim-wholeloop-design.md §4 / §7.5): the CPU-state rewrites
// and the explicit assumptions inside these projections never enter Go without
// an independent byte-for-byte differential witness. Every branch is ported in
// the exact Python order, every Python `require(cond, msg)` becomes a
// *DeliverValueError (Python ValueError), every `raise Blocked(kind, ...)`
// becomes a *PeriphError whose JSON is byte-identical to
// json.dumps(details, sort_keys=True). Nothing is fabricated: the vector-slot
// read is modeled as an explicit per-slot word (nil = uninitialized), the
// entry bank assumption reads the caller-supplied last_explicit_ssp, and the
// audio IRQ68 nesting reads the caller-supplied real ICFG nibbles.

import (
	"encoding/json"
	"fmt"
)

// Delivery projection anchors (br28_machine constants).
const (
	SoftwareIrqRtiTailA = 0x12ade8
	SoftwareIrqRtiTailB = 0x6066342
	CoreTickRtiTail     = 0x60661e6
	Timer0Handler       = 0x12ece8
	Timer0RtiTail       = 0x12ed0c
	AudioDmaIRQNumber   = 68
	AudioIrqRtiTail     = 0x602f452
	SrcSyncIRQNumber    = 89
	SrcSyncRtiTail      = 0x12cad2
)

// VectorSlotBase mirrors the IRQ vector table base (0x19fe00 + irq*4).
const VectorSlotBase = 0x19fe00

// The five in-flight IRQ handler contexts (br28_machine.IRQ_HANDLER_CONTEXTS).
var irqHandlerContexts = []string{
	"software_irq_handler", "core_tick_handler", "timer0_irq_handler",
	"audio_dma_irq_handler", "src_sync_irq_handler",
}

// audioNestableContexts mirrors AUDIO_NESTABLE_CONTEXTS: only the Timer0
// event-loop handler may be preempted by the strictly-higher-priority IRQ68.
var audioNestableContexts = map[string]bool{"timer0_irq_handler": true}

// DeliverValueError mirrors Python's require(cond, msg) -> ValueError(msg). The
// require guards in the deliver projections surface as a value error, not a
// Blocked strict stop.
type DeliverValueError struct{ Msg string }

func (e *DeliverValueError) Error() string { return e.Msg }

func deliverRequire(cond bool, msg string) error {
	if !cond {
		return &DeliverValueError{Msg: msg}
	}
	return nil
}

// DeliverMachine is the delivery-relevant slice of DualCoreMachine plus the
// hardware the projections read (software-IRQ acknowledge words, IRQ config
// nibbles) and the caller-supplied vector-slot words. It is the Go twin of the
// state the differential test test_m6c_deliver_consistency.py mirrors.
type DeliverMachine struct {
	Cores    [2]*Core
	IdleWait [2]bool

	SoftwareIrqEntry      bool
	SoftwareIrqEntryBanks bool
	SoftwareIrqRti        bool
	CoreTickEntry         bool
	CoreTickEntryBanks    bool
	CoreTickRti           bool
	Timer0IrqEntry        bool
	Timer0IrqEntryBanks   bool
	Timer0IrqRti          bool
	AudioIrqEntry         bool
	AudioIrqEntryBanks    bool
	AudioIrqRti           bool
	SrcSyncIrqEntry       bool
	SrcSyncIrqEntryBanks  bool
	SrcSyncIrqRti         bool
	RepeatIrqDelivery     bool
	IdleWakeConsumesIdle  bool

	// VectorSlots is the caller-supplied IRQ vector table (address -> little-
	// endian handler word; nil value = uninitialized, mirroring cpu.mem.read's
	// uninitialized_memory -> vector_slot_uninitialized).
	VectorSlots map[uint32]*uint32

	// Software-IRQ acknowledge state (hardware.software_irq).
	SoftwareIrqPending [2]*PartialWord
	SoftwareIrqAck     int64

	// IRQConfig mirrors hardware.irq.config (32 ICFG words; nil = unconfigured),
	// read by the audio IRQ68 nesting priority justification.
	IRQConfig [32]*uint32

	SoftwareIrqDeliveries []map[string]any
	CoreTickDeliveries    []map[string]any
	Timer0IrqDeliveries   []map[string]any
	AudioIrqDeliveries    []map[string]any
	SrcSyncIrqDeliveries  []map[string]any

	Events     []map[string]any
	EventCount int
}

// NewDeliverMachine returns a DeliverMachine with empty (non-nil) slices so the
// JSON state matches Python's [] rather than Go's nil, and with the two
// software-IRQ pending words initialized to unknown (mirroring the Python
// SoftwareIrqCommands() default).
func NewDeliverMachine() *DeliverMachine {
	return &DeliverMachine{
		SoftwareIrqPending:    [2]*PartialWord{NewPartialWord(0, 0), NewPartialWord(0, 0)},
		SoftwareIrqDeliveries: []map[string]any{},
		CoreTickDeliveries:    []map[string]any{},
		Timer0IrqDeliveries:   []map[string]any{},
		AudioIrqDeliveries:    []map[string]any{},
		SrcSyncIrqDeliveries:  []map[string]any{},
		Events:                []map[string]any{},
	}
}

// recordEvent mirrors DualCoreMachine.record_event: append then, if over 32,
// pop index 16.
func (m *DeliverMachine) recordEvent(event map[string]any) {
	m.EventCount++
	m.Events = append(m.Events, event)
	if len(m.Events) > 32 {
		m.Events = append(m.Events[:16], m.Events[17:]...)
	}
}

// idleWakeReturnAddress mirrors _idle_wake_return_address: a parked core's
// interrupt return address is the idle instruction plus its fixed 2-byte size
// when idle_wake_consumes_idle, else the idle pc itself.
func (m *DeliverMachine) idleWakeReturnAddress(c *Core) uint32 {
	if m.IdleWakeConsumesIdle {
		return c.PC + 2
	}
	return c.PC
}

// requireNoInflightIrq mirrors _require_no_inflight_irq: reject a new delivery
// onto an in-flight handler that is not in the allow set (which is non-empty
// only for the audio IRQ68 nesting exception).
func (m *DeliverMachine) requireNoInflightIrq(c *Core, what string, allow map[string]bool) error {
	if allow[c.ActiveContext] {
		return nil
	}
	for _, ctx := range irqHandlerContexts {
		if ctx == c.ActiveContext {
			return &DeliverValueError{Msg: fmt.Sprintf("repeated %s delivery must not nest on an in-flight IRQ handler", what)}
		}
	}
	return nil
}

// audioPriorityNibble mirrors _audio_priority_nibble: read the irq's ICFG
// nibble from the caller-supplied config words (a missing word is a strict
// stop).
func (m *DeliverMachine) audioPriorityNibble(irq uint32) (uint32, error) {
	if irq >= 124 || m.IRQConfig[irq/8] == nil {
		return 0, periphErr("audio_irq_priority_unconfigured", "irq", irq,
			"note", "the nesting justification reads the real ICFG nibbles")
	}
	return (*m.IRQConfig[irq/8] >> uint((irq%8)*4)) & 15, nil
}

// readVectorSlot mirrors the cpu.mem.read(slot, 4) + Blocked catch: a missing
// or uninitialized slot word is the vector_slot_uninitialized strict stop.
func (m *DeliverMachine) readVectorSlot(slot uint32, coreID int, irq uint32) (uint32, error) {
	v, ok := m.VectorSlots[slot]
	if !ok || v == nil {
		return 0, periphErr("vector_slot_uninitialized", "core", coreID, "irq", irq, "address", hexWord(slot))
	}
	return *v, nil
}

// acknowledgeSoftwareIrq mirrors SoftwareIrqCommands.acknowledge(core, bit).
func (m *DeliverMachine) acknowledgeSoftwareIrq(core int, bit uint32) {
	m.SoftwareIrqPending[core].Apply("and", ^(uint32(1)<<bit)&0xffffffff)
	m.SoftwareIrqAck++
}

// entryBanks builds the entry_banks dict under the shared bank-switch
// assumption (SSP = last_explicit_ssp, USP = the interrupted task stack). It
// returns the dict, the captured bank USP (the pre-entry task stack pointer),
// and a DeliverValueError when the explicit SSP fixture or a known task stack
// pointer is missing.
func (m *DeliverMachine) entryBanks(c *Core, what string) (map[string]any, *uint32, error) {
	if c.LastExplicitSSP == nil {
		return nil, nil, &DeliverValueError{Msg: what + "entry bank assumption lacks an explicit SSP fixture"}
	}
	if !c.SPKnown {
		return nil, nil, &DeliverValueError{Msg: "interrupted task stack pointer is unknown"}
	}
	usp := c.SP
	return map[string]any{
		"usp": hexWord(c.SP), "ssp": hexWord(*c.LastExplicitSSP),
		"entry_sp": "ssp (assumed)", "silicon_bank_switch_certified": false,
	}, &usp, nil
}

// mapInt normalizes a JSON round-tripped number (float64 from json.Unmarshal)
// or a Go int back to an int for the delivery-list "core" comparisons.
func mapInt(v any) int {
	switch t := v.(type) {
	case int:
		return t
	case int64:
		return int(t)
	case uint32:
		return int(t)
	case uint64:
		return int(t)
	case float64:
		return int(t)
	case json.Number:
		n, _ := t.Int64()
		return int(n)
	}
	return 0
}

// DeliverSoftwareIrq mirrors deliver_software_irq(cpu, irq).
func (m *DeliverMachine) DeliverSoftwareIrq(coreID int, irq uint32) error {
	c := m.Cores[coreID]
	if c == nil || !m.SoftwareIrqEntry || !(irq == uint32(127-c.ID) || (c.ID == 1 && irq == 125)) ||
		c.BootstrapContext || c.RepeatActive {
		return &DeliverValueError{Msg: "software IRQ entry requires the exact per-core scheduler/sync IPI at an instruction boundary"}
	}
	if m.RepeatIrqDelivery {
		if err := m.requireNoInflightIrq(c, "software IRQ", nil); err != nil {
			return err
		}
	} else {
		for _, d := range m.SoftwareIrqDeliveries {
			if mapInt(d["core"]) == c.ID {
				return &DeliverValueError{Msg: "software IRQ entry is one-shot per core without the repeat delivery projection"}
			}
		}
	}
	slot := uint32(VectorSlotBase) + irq*4
	handler, err := m.readVectorSlot(slot, coreID, irq)
	if err != nil {
		return err
	}
	var entryBanks map[string]any
	var bankUSP *uint32
	if m.SoftwareIrqEntryBanks {
		var err error
		entryBanks, bankUSP, err = m.entryBanks(c, "")
		if err != nil {
			return err
		}
	}
	woke := m.IdleWait[coreID]
	if woke {
		m.IdleWait[coreID] = false
		m.recordEvent(map[string]any{"kind": "core_woken_by_delivery", "core": coreID, "irq": irq})
	}
	interrupted := c.PC
	if woke {
		interrupted = m.idleWakeReturnAddress(c)
	}
	reti := interrupted
	c.RETI = &reti
	c.PC = handler
	c.Steps++
	irqPSR := c.Flags & 0xF
	c.IRQPSR = &irqPSR
	if m.SoftwareIrqEntryBanks {
		c.USP = bankUSP
		c.SSP = c.LastExplicitSSP
		c.SP = *c.LastExplicitSSP
		c.SPKnown = true
	}
	c.ActiveContext = "software_irq_handler"
	m.acknowledgeSoftwareIrq(c.ID, irq-124)
	event := map[string]any{
		"kind": "software_irq_entry", "core": coreID, "irq": irq,
		"vector_address": hexWord(slot), "handler": hexWord(handler),
		"reti_from": hexWord(interrupted), "entry_counted_as_model_step": true,
		"reti_load_is_assumption": true, "delivery_clears_pending_is_assumption": true,
		"entry_psr_high_bits_unknown_pushed_as_zero": true,
		"entry_banks": entryBanks,
		"entry_sp_bank_or_icfg_effects_certified": false, "hardware_frame_write_modeled": false,
	}
	m.SoftwareIrqDeliveries = append(m.SoftwareIrqDeliveries, event)
	m.recordEvent(event)
	return nil
}

// DeliverCoreTick mirrors deliver_core_tick(core).
func (m *DeliverMachine) DeliverCoreTick(coreID int) error {
	if !m.CoreTickEntry || m.Cores[coreID] == nil {
		return &DeliverValueError{Msg: "core tick entry requires an eligible overflow on a launched core"}
	}
	c := m.Cores[coreID]
	if m.RepeatIrqDelivery {
		if err := m.requireNoInflightIrq(c, "core tick", nil); err != nil {
			return err
		}
	} else {
		for _, d := range m.CoreTickDeliveries {
			if mapInt(d["core"]) == coreID {
				return &DeliverValueError{Msg: "core tick entry is one-shot per core without the repeat delivery projection"}
			}
		}
	}
	if c.BootstrapContext || c.RepeatActive {
		return &DeliverValueError{Msg: "core tick entry requires an instruction boundary on the overflowed core"}
	}
	slot := uint32(VectorSlotBase) + 3*4
	handler, err := m.readVectorSlot(slot, coreID, 3)
	if err != nil {
		return err
	}
	var entryBanks map[string]any
	var bankUSP *uint32
	if m.CoreTickEntryBanks {
		var err error
		entryBanks, bankUSP, err = m.entryBanks(c, "core tick ")
		if err != nil {
			return err
		}
	}
	woke := m.IdleWait[coreID]
	if woke {
		m.IdleWait[coreID] = false
		m.recordEvent(map[string]any{"kind": "core_woken_by_delivery", "core": coreID, "irq": 3})
	}
	interrupted := c.PC
	if woke {
		interrupted = m.idleWakeReturnAddress(c)
	}
	reti := interrupted
	c.RETI = &reti
	c.PC = handler
	c.Steps++
	irqPSR := c.Flags & 0xF
	c.IRQPSR = &irqPSR
	if m.CoreTickEntryBanks {
		c.USP = bankUSP
		c.SSP = c.LastExplicitSSP
		c.SP = *c.LastExplicitSSP
		c.SPKnown = true
	}
	c.ActiveContext = "core_tick_handler"
	event := map[string]any{
		"kind": "core_tick_entry", "core": coreID, "irq": 3,
		"vector_address": hexWord(slot), "handler": hexWord(handler),
		"reti_from": hexWord(interrupted), "entry_counted_as_model_step": true,
		"reti_load_is_assumption": true, "delivery_clears_pending": false,
		"pending_cleared_by_handler_ack":             true,
		"entry_psr_high_bits_unknown_pushed_as_zero": true,
		"entry_banks": entryBanks,
		"entry_sp_bank_or_icfg_effects_certified": false, "hardware_frame_write_modeled": false,
	}
	m.CoreTickDeliveries = append(m.CoreTickDeliveries, event)
	m.recordEvent(event)
	return nil
}

// DeliverTimer0Irq mirrors deliver_timer0_irq(core).
func (m *DeliverMachine) DeliverTimer0Irq(coreID int) error {
	if !m.Timer0IrqEntry || m.Cores[coreID] == nil {
		return &DeliverValueError{Msg: "Timer0 IRQ4 entry requires an eligible pending episode on a launched core"}
	}
	c := m.Cores[coreID]
	if m.RepeatIrqDelivery {
		if err := m.requireNoInflightIrq(c, "Timer0 IRQ4", nil); err != nil {
			return err
		}
	} else {
		for _, d := range m.Timer0IrqDeliveries {
			if mapInt(d["core"]) == coreID {
				return &DeliverValueError{Msg: "Timer0 IRQ4 entry is one-shot per core without the repeat delivery projection"}
			}
		}
	}
	if c.BootstrapContext || c.RepeatActive {
		return &DeliverValueError{Msg: "Timer0 IRQ4 entry requires an instruction boundary"}
	}
	slot := uint32(VectorSlotBase) + 4*4
	handler, err := m.readVectorSlot(slot, coreID, 4)
	if err != nil {
		return err
	}
	var entryBanks map[string]any
	var bankUSP *uint32
	if m.Timer0IrqEntryBanks {
		var err error
		entryBanks, bankUSP, err = m.entryBanks(c, "Timer0 IRQ4 ")
		if err != nil {
			return err
		}
	}
	woke := m.IdleWait[coreID]
	if woke {
		m.IdleWait[coreID] = false
		m.recordEvent(map[string]any{"kind": "core_woken_by_delivery", "core": coreID, "irq": 4})
	}
	interrupted := c.PC
	if woke {
		interrupted = m.idleWakeReturnAddress(c)
	}
	reti := interrupted
	c.RETI = &reti
	c.PC = handler
	c.Steps++
	irqPSR := c.Flags & 0xF
	c.IRQPSR = &irqPSR
	if m.Timer0IrqEntryBanks {
		c.USP = bankUSP
		c.SSP = c.LastExplicitSSP
		c.SP = *c.LastExplicitSSP
		c.SPKnown = true
	}
	c.ActiveContext = "timer0_irq_handler"
	event := map[string]any{
		"kind": "timer0_irq_entry", "core": coreID, "irq": 4,
		"vector_address": hexWord(slot), "handler": hexWord(handler),
		"reti_from": hexWord(interrupted), "entry_counted_as_model_step": true,
		"reti_load_is_assumption": true, "delivery_clears_pending": false,
		"pending_cleared_by_handler_ack":             true,
		"handler_ack_shape":                          "real W1C rmw.or.modimm [0xfd0400] |= 0x4000 (bit14) at 0x12ecf8; modeled by Timer0.write",
		"entry_psr_high_bits_unknown_pushed_as_zero": true,
		"entry_banks":                                entryBanks,
		"entry_sp_bank_or_icfg_effects_certified":    false, "hardware_frame_write_modeled": false,
	}
	m.Timer0IrqDeliveries = append(m.Timer0IrqDeliveries, event)
	m.recordEvent(event)
	return nil
}

// DeliverAudioDmaIrq mirrors deliver_audio_dma_irq(core).
func (m *DeliverMachine) DeliverAudioDmaIrq(coreID int) error {
	if !m.AudioIrqEntry || m.Cores[coreID] == nil {
		return &DeliverValueError{Msg: "audio IRQ68 entry requires an eligible pending episode on a launched core"}
	}
	c := m.Cores[coreID]
	if m.RepeatIrqDelivery {
		if err := m.requireNoInflightIrq(c, "audio IRQ68", audioNestableContexts); err != nil {
			return err
		}
	} else {
		for _, d := range m.AudioIrqDeliveries {
			if mapInt(d["core"]) == coreID {
				return &DeliverValueError{Msg: "audio IRQ68 entry is one-shot per core without the repeat delivery projection"}
			}
		}
	}
	if c.BootstrapContext || c.RepeatActive {
		return &DeliverValueError{Msg: "audio IRQ68 entry requires an instruction boundary"}
	}
	contextBefore := c.ActiveContext
	nested := false
	var nestingJustification map[string]any
	var outer *AudioIrqOuterContext
	if contextBefore != "" {
		if !audioNestableContexts[contextBefore] {
			return periphErr("audio_irq_nesting_not_modeled", "core", coreID, "irq", 68,
				"active_context", contextBefore,
				"note", "only a strictly-higher-priority preemption of the Timer0 event-loop handler is modeled")
		}
		audioNibble, err := m.audioPriorityNibble(AudioDmaIRQNumber)
		if err != nil {
			return err
		}
		outerIrq := uint32(4)
		outerNibble, err := m.audioPriorityNibble(outerIrq)
		if err != nil {
			return err
		}
		if !(audioNibble&1 != 0 && outerNibble&1 != 0 && (audioNibble>>1) > (outerNibble>>1)) {
			return &DeliverValueError{Msg: "audio IRQ68 nesting requires a strictly higher real priority"}
		}
		nested = true
		nestingJustification = map[string]any{
			"in_flight_irq": outerIrq, "audio_nibble": hexWord(audioNibble), "outer_nibble": hexWord(outerNibble),
		}
		var outerReti *uint32
		if c.RETI != nil {
			v := *c.RETI
			outerReti = &v
		}
		outer = &AudioIrqOuterContext{ActiveContext: contextBefore, RETI: outerReti, SP: c.SP}
	}
	slot := uint32(VectorSlotBase) + AudioDmaIRQNumber*4
	handler, err := m.readVectorSlot(slot, coreID, AudioDmaIRQNumber)
	if err != nil {
		return err
	}
	var entryBanks map[string]any
	var bankUSP *uint32
	if !nested && m.AudioIrqEntryBanks {
		var err error
		entryBanks, bankUSP, err = m.entryBanks(c, "audio IRQ68 ")
		if err != nil {
			return err
		}
	}
	woke := m.IdleWait[coreID]
	if woke {
		m.IdleWait[coreID] = false
		m.recordEvent(map[string]any{"kind": "core_woken_by_delivery", "core": coreID, "irq": 68})
	}
	interrupted := c.PC
	if woke {
		interrupted = m.idleWakeReturnAddress(c)
	}
	reti := interrupted
	c.RETI = &reti
	c.PC = handler
	c.Steps++
	irqPSR := c.Flags & 0xF
	c.IRQPSR = &irqPSR
	if !nested && m.AudioIrqEntryBanks {
		c.USP = bankUSP
		c.SSP = c.LastExplicitSSP
		c.SP = *c.LastExplicitSSP
		c.SPKnown = true
	}
	if nested {
		c.AudioIrqOuter = outer
	}
	c.ActiveContext = "audio_dma_irq_handler"
	event := map[string]any{
		"kind": "audio_dma_irq_entry", "core": coreID, "irq": 68,
		"vector_address": hexWord(slot), "handler": hexWord(handler),
		"reti_from": hexWord(interrupted), "entry_counted_as_model_step": true,
		"reti_load_is_assumption": true, "delivery_clears_pending": false,
		"pending_cleared_by_handler_ack":             true,
		"handler_ack_shape":                          "real W1C rmw [0xfe0538] |= 0x40 (bit6) at dispatcher 0x602f434 and handler tail 0x60726ec; modeled by JLAudioAdcDmaWriteRecords.note_con_write",
		"entry_psr_high_bits_unknown_pushed_as_zero": true,
		"entry_banks":                                entryBanks, "nested": nested,
		"entry_sp_bank_or_icfg_effects_certified": false, "hardware_frame_write_modeled": false,
	}
	if nested {
		event["nesting_justification"] = nestingJustification
		event["nesting_is_priority_justified_assumption"] = true
	}
	m.AudioIrqDeliveries = append(m.AudioIrqDeliveries, event)
	m.recordEvent(event)
	return nil
}

// DeliverSrcSyncIrq mirrors deliver_src_sync_irq(core).
func (m *DeliverMachine) DeliverSrcSyncIrq(coreID int) error {
	if !m.SrcSyncIrqEntry || m.Cores[coreID] == nil {
		return &DeliverValueError{Msg: "SRC_SYNC IRQ89 entry requires an eligible pending episode on a launched core"}
	}
	c := m.Cores[coreID]
	if m.RepeatIrqDelivery {
		if err := m.requireNoInflightIrq(c, "SRC_SYNC IRQ89", nil); err != nil {
			return err
		}
	} else {
		for _, d := range m.SrcSyncIrqDeliveries {
			if mapInt(d["core"]) == coreID {
				return &DeliverValueError{Msg: "SRC_SYNC IRQ89 entry is one-shot per core without the repeat delivery projection"}
			}
		}
	}
	if c.BootstrapContext || c.RepeatActive {
		return &DeliverValueError{Msg: "SRC_SYNC IRQ89 entry requires an instruction boundary"}
	}
	slot := uint32(VectorSlotBase) + SrcSyncIRQNumber*4
	handler, err := m.readVectorSlot(slot, coreID, SrcSyncIRQNumber)
	if err != nil {
		return err
	}
	var entryBanks map[string]any
	var bankUSP *uint32
	if m.SrcSyncIrqEntryBanks {
		var err error
		entryBanks, bankUSP, err = m.entryBanks(c, "SRC_SYNC IRQ89 ")
		if err != nil {
			return err
		}
	}
	woke := m.IdleWait[coreID]
	if woke {
		m.IdleWait[coreID] = false
		m.recordEvent(map[string]any{"kind": "core_woken_by_delivery", "core": coreID, "irq": SrcSyncIRQNumber})
	}
	interrupted := c.PC
	if woke {
		interrupted = m.idleWakeReturnAddress(c)
	}
	reti := interrupted
	c.RETI = &reti
	c.PC = handler
	c.Steps++
	irqPSR := c.Flags & 0xF
	c.IRQPSR = &irqPSR
	if m.SrcSyncIrqEntryBanks {
		c.USP = bankUSP
		c.SSP = c.LastExplicitSSP
		c.SP = *c.LastExplicitSSP
		c.SPKnown = true
	}
	c.ActiveContext = "src_sync_irq_handler"
	event := map[string]any{
		"kind": "src_sync_irq_entry", "core": coreID, "irq": SrcSyncIRQNumber,
		"vector_address": hexWord(slot), "handler": hexWord(handler),
		"reti_from": hexWord(interrupted), "entry_counted_as_model_step": true,
		"reti_load_is_assumption": true, "delivery_clears_pending": false,
		"pending_cleared_by_handler_ack":             true,
		"handler_ack_shape":                          "real W1C rmw [0xfe2600] |= 0x40 (bit6) at handler 0x12ca42 (RAM copy of flash 0x60c621a); modeled by JLSrcSyncDelivery.note_con_write",
		"entry_psr_high_bits_unknown_pushed_as_zero": true,
		"entry_banks":                                entryBanks, "nested": false,
		"entry_sp_bank_or_icfg_effects_certified": false, "hardware_frame_write_modeled": false,
	}
	m.SrcSyncIrqDeliveries = append(m.SrcSyncIrqDeliveries, event)
	m.recordEvent(event)
	return nil
}

// ReturnSoftwareIrq mirrors return_software_irq(cpu).
func (m *DeliverMachine) ReturnSoftwareIrq(coreID int) error {
	if !m.SoftwareIrqRti || m.Cores[coreID] == nil {
		return &DeliverValueError{Msg: "IRQ RTI projection requires the exact handler tail"}
	}
	c := m.Cores[coreID]
	if (c.PC != SoftwareIrqRtiTailA && c.PC != SoftwareIrqRtiTailB) || c.ActiveContext != "software_irq_handler" || c.RepeatActive {
		return &DeliverValueError{Msg: "IRQ RTI projection requires the exact handler tail"}
	}
	if c.USP == nil || c.RETI == nil {
		return &DeliverValueError{Msg: "IRQ RTI lacks a restored frame or task stack"}
	}
	interruptedTask := *c.RETI
	c.PC = interruptedTask
	c.SP = *c.USP
	c.SPKnown = true
	c.Steps++
	c.ActiveContext = ""
	m.recordEvent(map[string]any{
		"kind": "software_irq_rti", "core": coreID, "pc_after": hexWord(c.PC), "sp_after": hexWord(c.SP),
		"reti_from_frame": hexWord(interruptedTask), "bank_writeback_is_assumption": true, "icfg_effects_certified": false,
	})
	return nil
}

// ReturnCoreTick mirrors return_core_tick(cpu).
func (m *DeliverMachine) ReturnCoreTick(coreID int) error {
	if !m.CoreTickRti || m.Cores[coreID] == nil {
		return &DeliverValueError{Msg: "core tick RTI projection requires the exact handler tail"}
	}
	c := m.Cores[coreID]
	if c.PC != CoreTickRtiTail || c.ActiveContext != "core_tick_handler" || c.RepeatActive {
		return &DeliverValueError{Msg: "core tick RTI projection requires the exact handler tail"}
	}
	if c.USP == nil || c.RETI == nil {
		return &DeliverValueError{Msg: "core tick RTI lacks a restored frame or task stack"}
	}
	interruptedTask := *c.RETI
	c.PC = interruptedTask
	c.SP = *c.USP
	c.SPKnown = true
	c.Steps++
	c.ActiveContext = ""
	m.recordEvent(map[string]any{
		"kind": "core_tick_rti", "core": coreID, "pc_after": hexWord(c.PC), "sp_after": hexWord(c.SP),
		"reti_from_frame": hexWord(interruptedTask), "bank_writeback_is_assumption": true, "icfg_effects_certified": false,
	})
	return nil
}

// ReturnTimer0Irq mirrors return_timer0_irq(cpu).
func (m *DeliverMachine) ReturnTimer0Irq(coreID int) error {
	if !m.Timer0IrqRti || m.Cores[coreID] == nil {
		return &DeliverValueError{Msg: "Timer0 IRQ4 RTI projection requires the exact handler tail"}
	}
	c := m.Cores[coreID]
	if c.PC != Timer0RtiTail || c.ActiveContext != "timer0_irq_handler" || c.RepeatActive {
		return &DeliverValueError{Msg: "Timer0 IRQ4 RTI projection requires the exact handler tail"}
	}
	matched := false
	for _, d := range m.Timer0IrqDeliveries {
		if mapInt(d["core"]) == c.ID && d["handler"] == hexWord(Timer0Handler) {
			matched = true
			break
		}
	}
	if !matched {
		return &DeliverValueError{Msg: "Timer0 IRQ4 RTI tail does not match the delivered handler identity"}
	}
	if c.USP == nil || c.RETI == nil {
		return &DeliverValueError{Msg: "Timer0 IRQ4 RTI lacks a restored frame or task stack"}
	}
	interruptedTask := *c.RETI
	c.PC = interruptedTask
	c.SP = *c.USP
	c.SPKnown = true
	c.Steps++
	c.ActiveContext = ""
	m.recordEvent(map[string]any{
		"kind": "timer0_irq_rti", "core": coreID, "pc_after": hexWord(c.PC), "sp_after": hexWord(c.SP),
		"reti_from_frame": hexWord(interruptedTask), "bank_writeback_is_assumption": true, "icfg_effects_certified": false,
	})
	return nil
}

// ReturnAudioDmaIrq mirrors return_audio_dma_irq(cpu).
func (m *DeliverMachine) ReturnAudioDmaIrq(coreID int) error {
	if !m.AudioIrqRti || m.Cores[coreID] == nil {
		return &DeliverValueError{Msg: "audio IRQ68 RTI projection requires the exact handler tail"}
	}
	c := m.Cores[coreID]
	if c.PC != AudioIrqRtiTail || c.ActiveContext != "audio_dma_irq_handler" || c.RepeatActive {
		return &DeliverValueError{Msg: "audio IRQ68 RTI projection requires the exact handler tail"}
	}
	matched := false
	for _, d := range m.AudioIrqDeliveries {
		if mapInt(d["core"]) == c.ID {
			matched = true
			break
		}
	}
	if !matched {
		return &DeliverValueError{Msg: "audio IRQ68 RTI tail does not match a delivered entry"}
	}
	outer := c.AudioIrqOuter
	if outer != nil {
		if c.RETI == nil {
			return &DeliverValueError{Msg: "audio IRQ68 nested RTI lacks a restored frame"}
		}
		if c.SP != outer.SP {
			return &DeliverValueError{Msg: "audio IRQ68 nested RTI frame is unbalanced (SP differs from the entry)"}
		}
		interrupted := *c.RETI
		c.PC = interrupted
		c.ActiveContext = outer.ActiveContext
		c.RETI = outer.RETI
		c.Steps++
		c.AudioIrqOuter = nil
		event := map[string]any{
			"kind": "audio_dma_irq_rti", "core": coreID, "pc_after": hexWord(c.PC),
			"returns_to":           "outer timer0 handler context",
			"outer_active_context": outer.ActiveContext,
			"reti_from_frame":      hexWord(interrupted), "sp_after": hexWord(c.SP),
			"bank_writeback_is_assumption": true, "icfg_effects_certified": false,
		}
		if outer.RETI != nil {
			event["outer_reti"] = hexWord(*outer.RETI)
		}
		m.recordEvent(event)
		return nil
	}
	if c.USP == nil || c.RETI == nil {
		return &DeliverValueError{Msg: "audio IRQ68 RTI lacks a restored frame or task stack"}
	}
	interruptedTask := *c.RETI
	c.PC = interruptedTask
	c.SP = *c.USP
	c.SPKnown = true
	c.Steps++
	c.ActiveContext = ""
	m.recordEvent(map[string]any{
		"kind": "audio_dma_irq_rti", "core": coreID, "pc_after": hexWord(c.PC), "sp_after": hexWord(c.SP),
		"reti_from_frame": hexWord(interruptedTask), "bank_writeback_is_assumption": true, "icfg_effects_certified": false,
	})
	return nil
}

// ReturnSrcSyncIrq mirrors return_src_sync_irq(cpu).
func (m *DeliverMachine) ReturnSrcSyncIrq(coreID int) error {
	if !m.SrcSyncIrqRti || m.Cores[coreID] == nil {
		return &DeliverValueError{Msg: "SRC_SYNC IRQ89 RTI projection requires the exact handler tail"}
	}
	c := m.Cores[coreID]
	if c.PC != SrcSyncRtiTail || c.ActiveContext != "src_sync_irq_handler" || c.RepeatActive {
		return &DeliverValueError{Msg: "SRC_SYNC IRQ89 RTI projection requires the exact handler tail"}
	}
	matched := false
	for _, d := range m.SrcSyncIrqDeliveries {
		if mapInt(d["core"]) == c.ID {
			matched = true
			break
		}
	}
	if !matched {
		return &DeliverValueError{Msg: "SRC_SYNC IRQ89 RTI tail does not match a delivered entry"}
	}
	if c.USP == nil || c.RETI == nil {
		return &DeliverValueError{Msg: "SRC_SYNC IRQ89 RTI lacks a restored frame or task stack"}
	}
	interruptedTask := *c.RETI
	c.PC = interruptedTask
	c.SP = *c.USP
	c.SPKnown = true
	c.Steps++
	c.ActiveContext = ""
	m.recordEvent(map[string]any{
		"kind": "src_sync_irq_rti", "core": coreID, "pc_after": hexWord(c.PC), "sp_after": hexWord(c.SP),
		"reti_from_frame": hexWord(interruptedTask), "bank_writeback_is_assumption": true, "icfg_effects_certified": false,
	})
	return nil
}

// ---- JSON state round-trip for the differential harness ---------------------

// deliverCoreJSON is the JSON shape of one delivery core.
type deliverCoreJSON struct {
	ID               int               `json:"id"`
	PC               uint32            `json:"pc"`
	Steps            uint64            `json:"steps"`
	SP               uint32            `json:"sp"`
	SPKnown          bool              `json:"sp_known"`
	USP              *uint32           `json:"usp"`
	SSP              *uint32           `json:"ssp"`
	RETI             *uint32           `json:"reti"`
	Flags            uint32            `json:"flags"`
	IRQPSR           *uint32           `json:"irq_psr"`
	ActiveContext    string            `json:"active_context"`
	BootstrapContext bool              `json:"bootstrap_context"`
	RepeatActive     bool              `json:"repeat_active"`
	LastExplicitSSP  *uint32           `json:"last_explicit_ssp"`
	AudioIrqOuter    *deliverOuterJSON `json:"audio_irq_outer_context"`
}

type deliverOuterJSON struct {
	ActiveContext string  `json:"active_context"`
	RETI          *uint32 `json:"reti"`
	SP            uint32  `json:"sp"`
}

type deliverPartialWordJSON struct {
	Value uint32 `json:"value"`
	Known uint32 `json:"known"`
}

// deliverInput is the JSON shape the harness loads before one deliver/return
// call.
type deliverInput struct {
	Cores                 [2]*deliverCoreJSON        `json:"cores"`
	IdleWait              [2]bool                    `json:"idle_wait"`
	SoftwareIrqEntry      bool                       `json:"software_irq_entry"`
	SoftwareIrqEntryBanks bool                       `json:"software_irq_entry_banks"`
	SoftwareIrqRti        bool                       `json:"software_irq_rti"`
	CoreTickEntry         bool                       `json:"core_tick_entry"`
	CoreTickEntryBanks    bool                       `json:"core_tick_entry_banks"`
	CoreTickRti           bool                       `json:"core_tick_rti"`
	Timer0IrqEntry        bool                       `json:"timer0_irq_entry"`
	Timer0IrqEntryBanks   bool                       `json:"timer0_irq_entry_banks"`
	Timer0IrqRti          bool                       `json:"timer0_irq_rti"`
	AudioIrqEntry         bool                       `json:"audio_irq_entry"`
	AudioIrqEntryBanks    bool                       `json:"audio_irq_entry_banks"`
	AudioIrqRti           bool                       `json:"audio_irq_rti"`
	SrcSyncIrqEntry       bool                       `json:"src_sync_irq_entry"`
	SrcSyncIrqEntryBanks  bool                       `json:"src_sync_irq_entry_banks"`
	SrcSyncIrqRti         bool                       `json:"src_sync_irq_rti"`
	RepeatIrqDelivery     bool                       `json:"repeat_irq_delivery"`
	IdleWakeConsumesIdle  bool                       `json:"idle_wake_consumes_idle"`
	VectorSlots           map[uint32]*uint32         `json:"vector_slots"`
	SoftwareIrqPending    [2]*deliverPartialWordJSON `json:"software_irq_pending"`
	SoftwareIrqAck        int64                      `json:"software_irq_acknowledgements"`
	IRQConfig             [32]*uint32                `json:"irq_config"`
	SoftwareIrqDeliveries []map[string]any           `json:"software_irq_deliveries"`
	CoreTickDeliveries    []map[string]any           `json:"core_tick_deliveries"`
	Timer0IrqDeliveries   []map[string]any           `json:"timer0_irq_deliveries"`
	AudioIrqDeliveries    []map[string]any           `json:"audio_irq_deliveries"`
	SrcSyncIrqDeliveries  []map[string]any           `json:"src_sync_irq_deliveries"`
	Events                []map[string]any           `json:"events"`
	EventCount            int                        `json:"event_count"`
}

// LoadJSON restores the delivery machine state from the harness's JSON shape.
func (m *DeliverMachine) LoadJSON(data []byte) error {
	var in deliverInput
	if err := json.Unmarshal(data, &in); err != nil {
		return err
	}
	for i := 0; i < 2; i++ {
		if in.Cores[i] == nil {
			m.Cores[i] = nil
			continue
		}
		c := in.Cores[i]
		core := &Core{
			ID:               c.ID,
			PC:               c.PC,
			Steps:            c.Steps,
			SP:               c.SP,
			SPKnown:          c.SPKnown,
			Flags:            c.Flags,
			USP:              c.USP,
			SSP:              c.SSP,
			RETI:             c.RETI,
			IRQPSR:           c.IRQPSR,
			ActiveContext:    c.ActiveContext,
			BootstrapContext: c.BootstrapContext,
			RepeatActive:     c.RepeatActive,
			LastExplicitSSP:  c.LastExplicitSSP,
		}
		if c.AudioIrqOuter != nil {
			core.AudioIrqOuter = &AudioIrqOuterContext{
				ActiveContext: c.AudioIrqOuter.ActiveContext,
				RETI:          c.AudioIrqOuter.RETI,
				SP:            c.AudioIrqOuter.SP,
			}
		}
		m.Cores[i] = core
	}
	m.IdleWait = in.IdleWait
	m.SoftwareIrqEntry = in.SoftwareIrqEntry
	m.SoftwareIrqEntryBanks = in.SoftwareIrqEntryBanks
	m.SoftwareIrqRti = in.SoftwareIrqRti
	m.CoreTickEntry = in.CoreTickEntry
	m.CoreTickEntryBanks = in.CoreTickEntryBanks
	m.CoreTickRti = in.CoreTickRti
	m.Timer0IrqEntry = in.Timer0IrqEntry
	m.Timer0IrqEntryBanks = in.Timer0IrqEntryBanks
	m.Timer0IrqRti = in.Timer0IrqRti
	m.AudioIrqEntry = in.AudioIrqEntry
	m.AudioIrqEntryBanks = in.AudioIrqEntryBanks
	m.AudioIrqRti = in.AudioIrqRti
	m.SrcSyncIrqEntry = in.SrcSyncIrqEntry
	m.SrcSyncIrqEntryBanks = in.SrcSyncIrqEntryBanks
	m.SrcSyncIrqRti = in.SrcSyncIrqRti
	m.RepeatIrqDelivery = in.RepeatIrqDelivery
	m.IdleWakeConsumesIdle = in.IdleWakeConsumesIdle
	m.VectorSlots = in.VectorSlots
	for i := 0; i < 2; i++ {
		if in.SoftwareIrqPending[i] != nil {
			m.SoftwareIrqPending[i] = NewPartialWord(in.SoftwareIrqPending[i].Value, in.SoftwareIrqPending[i].Known)
		} else {
			m.SoftwareIrqPending[i] = NewPartialWord(0, 0)
		}
	}
	m.SoftwareIrqAck = in.SoftwareIrqAck
	m.IRQConfig = in.IRQConfig
	m.SoftwareIrqDeliveries = in.SoftwareIrqDeliveries
	m.CoreTickDeliveries = in.CoreTickDeliveries
	m.Timer0IrqDeliveries = in.Timer0IrqDeliveries
	m.AudioIrqDeliveries = in.AudioIrqDeliveries
	m.SrcSyncIrqDeliveries = in.SrcSyncIrqDeliveries
	if m.SoftwareIrqDeliveries == nil {
		m.SoftwareIrqDeliveries = []map[string]any{}
	}
	if m.CoreTickDeliveries == nil {
		m.CoreTickDeliveries = []map[string]any{}
	}
	if m.Timer0IrqDeliveries == nil {
		m.Timer0IrqDeliveries = []map[string]any{}
	}
	if m.AudioIrqDeliveries == nil {
		m.AudioIrqDeliveries = []map[string]any{}
	}
	if m.SrcSyncIrqDeliveries == nil {
		m.SrcSyncIrqDeliveries = []map[string]any{}
	}
	m.Events = in.Events
	if m.Events == nil {
		m.Events = []map[string]any{}
	}
	m.EventCount = in.EventCount
	return nil
}

// coreToJSON serializes one delivery core for the harness (nil core -> nil).
func coreToJSON(c *Core) any {
	if c == nil {
		return nil
	}
	var outer any
	if c.AudioIrqOuter != nil {
		outer = map[string]any{
			"active_context": c.AudioIrqOuter.ActiveContext,
			"reti":           nilU32(c.AudioIrqOuter.RETI),
			"sp":             c.AudioIrqOuter.SP,
		}
	}
	var ac any
	if c.ActiveContext != "" {
		ac = c.ActiveContext
	}
	return map[string]any{
		"id":                      c.ID,
		"pc":                      c.PC,
		"steps":                   c.Steps,
		"sp":                      c.SP,
		"sp_known":                c.SPKnown,
		"usp":                     nilU32(c.USP),
		"ssp":                     nilU32(c.SSP),
		"reti":                    nilU32(c.RETI),
		"flags":                   c.Flags,
		"irq_psr":                 nilU32(c.IRQPSR),
		"active_context":          ac,
		"bootstrap_context":       c.BootstrapContext,
		"repeat_active":           c.RepeatActive,
		"last_explicit_ssp":       nilU32(c.LastExplicitSSP),
		"audio_irq_outer_context": outer,
	}
}

// StateJSON serializes the delivery-relevant machine state for the harness.
func (m *DeliverMachine) StateJSON() []byte {
	pw := func(w *PartialWord) map[string]any {
		if w == nil {
			return map[string]any{"value": uint32(0), "known": uint32(0)}
		}
		return map[string]any{"value": w.Value, "known": w.Known}
	}
	out := map[string]any{
		"cores":                         []any{coreToJSON(m.Cores[0]), coreToJSON(m.Cores[1])},
		"idle_wait":                     []bool{m.IdleWait[0], m.IdleWait[1]},
		"software_irq_deliveries":       m.SoftwareIrqDeliveries,
		"core_tick_deliveries":          m.CoreTickDeliveries,
		"timer0_irq_deliveries":         m.Timer0IrqDeliveries,
		"audio_irq_deliveries":          m.AudioIrqDeliveries,
		"src_sync_irq_deliveries":       m.SrcSyncIrqDeliveries,
		"events":                        m.Events,
		"event_count":                   m.EventCount,
		"software_irq_pending":          []any{pw(m.SoftwareIrqPending[0]), pw(m.SoftwareIrqPending[1])},
		"software_irq_acknowledgements": m.SoftwareIrqAck,
	}
	b, _ := json.Marshal(out)
	return b
}
