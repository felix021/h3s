package gosim

// machine.go: M6 Stage A — the dual-core select phase ported from
// br28_machine.DualCoreMachine.select(). This is the first of the three M6
// machine-side pieces (select / after_step hooks / deliver_* projections); it
// owns the round-robin core choice and the two wake fallbacks (CPU1 control
// kick and the TIMER2 anchored poll completion), whose branching order and
// side effects are pinned branch-by-branch against the Python reference by
// test_m6_select_consistency.py (differential) and machine_test.go (unit).
//
// Red line (docs/gosim-wholeloop-design.md §7.3): select is highly entangled
// with the idle_wait_cores set and the kick/timer2 bookkeeping. Every branch is
// ported in the exact Python order and every divergent state is surfaced as an
// error to the caller — nothing is fabricated. The wake helpers only fire at
// the two pinned idle sites (0x12cd76 / the CPU1 kick idle); the caller supplies
// the decoded instruction id at the core's PC so the Go side can enforce the
// exact Python `require(insn.id=='idle')` precondition instead of guessing it.

import (
	"encoding/json"
	"fmt"
)

const (
	// Core1IdleDelayLimit mirrors CORE1_IDLE_DELAY_LIMIT: the bounded
	// CPU1-issue-delay cap before the first IDLE in the defer_core1 path.
	Core1IdleDelayLimit = 4096
	// Timer2PollAnchor mirrors TIMER2_POLL_ANCHOR (br28_machine.py): the
	// only TIMER2 busy-wait loop-head idle instruction in the app.
	Timer2PollAnchor = 0x12cd76
	// Core1IdlePC mirrors the literal 0x12c5ca the defer_core1 branch keys on.
	Core1IdlePC = 0x12c5ca
)

// MCore is the per-core state the select phase reads and mutates. It is the Go
// twin of the subset of the Python CPU (probe_boot.CPU) plus the machine-side
// per-core bookkeeping that select() touches. The full CPU (decode / effect /
// apply) is out of scope for Stage A: select only needs pc / steps / icfg and
// the decoded instruction at the anchored idle sites.
type MCore struct {
	ID        int
	PC        uint32
	Steps     uint64
	ICFG      uint32
	ICFGKnown bool
	// ICFGKnownMask is the full 32-bit known mask of the ICFG value (partial
	// ICFG only). It is unused on the non-partial path (ICFGKnown suffices).
	// 0xffffffff means fully known; a partial mask drives gate_demand.
	ICFGKnownMask uint32
	// InsnID / InsnSize are the decoded instruction at PC (caller-supplied).
	// The two wake helpers require InsnID == "idle" exactly as the Python
	// cpu.decode(pc)[0].id == 'idle' precondition does.
	InsnID   string
	InsnSize uint32
}

// Machine mirrors br28_machine.DualCoreMachine restricted to the select phase
// and its two wake fallbacks (Stage A). after_step hooks and the deliver_*
// projections are the B/C pieces and are deliberately absent.
type Machine struct {
	Cores    [2]*MCore // nil = not launched (mirrors the cores dict key membership)
	IdleWait [2]bool   // the idle_wait_cores set
	LastCore int       // round-robin cursor; Python initializes to 1 so core 0 runs first

	DeferCore1FirstIdle bool
	FirstTaskReturns    [2]bool // 1 in first_task_returns (a set in Python)
	DeferredCore1Turns  int

	Cpu1ControlKickWake  bool
	SeenCpu1ControlKicks int64
	HardwareKicks        *int64 // hardware.cpu1_control_kick_writes (nil = absent)

	Timer2DelayProjection bool
	Timer2                *Timer2Delay // hardware.timer2 (nil = absent)

	// IdleWaitProjection mirrors machine.idle_wait_projection: when armed, the
	// dual-core loop hands back at the idle instruction (enter_idle_wait stays
	// Python) instead of executing it. This is a loop-level precondition the
	// caller supplies; it never guesses it.
	IdleWaitProjection bool

	Events          []map[string]any
	EventCount      int
	Timer2PollWakes []map[string]any

	CurrentCore int        // hardware.current_core
	CoreIcfg    [2]*uint32 // hardware.core_icfg per launched core (nil = None)

	// PartialIcfg mirrors DualCoreMachine.partial_icfg: when set, icfgDemands
	// derives the per-core hardware gate demand (gate_demand) from the partial
	// known mask instead of the whole-word known/nil fallback.
	PartialIcfg bool
}

// NewMachine returns a Machine with the Python constructor's select-relevant
// defaults (last_core=1 so the first select picks core 0; empty event lists so
// the JSON state matches Python's [] rather than Go's nil).
func NewMachine() *Machine {
	return &Machine{
		LastCore:        1,
		Events:          []map[string]any{},
		Timer2PollWakes: []map[string]any{},
	}
}

// recordEvent mirrors DualCoreMachine.record_event: append then, if over 32,
// pop index 16 (the same bounded-event window as Python).
func (m *Machine) recordEvent(event map[string]any) {
	m.EventCount++
	m.Events = append(m.Events, event)
	if len(m.Events) > 32 {
		m.Events = append(m.Events[:16], m.Events[17:]...)
	}
}

// timer2AnchoredParkedCores mirrors timer2_anchored_parked_cores: the ascending
// core ids that are parked (in idle_wait_cores) and whose PC is exactly the
// TIMER2 poll anchor.
func (m *Machine) timer2AnchoredParkedCores() []int {
	if m.Timer2 == nil {
		return nil
	}
	var cores []int
	for core := 0; core < 2; core++ {
		if m.IdleWait[core] && m.Cores[core] != nil && m.Cores[core].PC == Timer2PollAnchor {
			cores = append(cores, core)
		}
	}
	return cores
}

// timer2PollDue mirrors timer2_poll_due: the TIMER2 pending flag is set and at
// least one anchored core is parked.
func (m *Machine) timer2PollDue() bool {
	return m.Timer2 != nil && m.Timer2.Pending && len(m.timer2AnchoredParkedCores()) > 0
}

// wakeCpu1ControlKick mirrors wake_cpu1_control_kick: the CPU1-control kick RMW
// wakes a parked CPU1 out of its idle. No vector / RETI / held-bit change; the
// wake source is an explicit unverified assumption.
func (m *Machine) wakeCpu1ControlKick() error {
	if m.Cores[1] == nil || !m.IdleWait[1] {
		return nil
	}
	c := m.Cores[1]
	if c.InsnID != "idle" {
		return fmt.Errorf("CPU1 control kick wake requires a parked IDLE instruction")
	}
	m.IdleWait[1] = false
	c.PC += c.InsnSize
	c.Steps++
	m.recordEvent(map[string]any{
		"kind": "core_woken_by_cpu1_control_kick", "core": 1,
		"pc_after":                      hexWord(c.PC),
		"idle_completion_is_assumption": true, "wake_source_unverified": true,
	})
	return nil
}

// wakeTimer2Poll mirrors wake_timer2_poll: a parked core at the exact TIMER2
// poll anchor consumes the idle (pc += size, steps += 1) when the pending flag
// is set. Kick precedent; no vector / RETI / ICFG change (wake_source_unverified).
func (m *Machine) wakeTimer2Poll(core int) error {
	c := m.Cores[core]
	if c.InsnID != "idle" || c.PC != Timer2PollAnchor {
		return fmt.Errorf("TIMER2 poll wake requires the anchored parked IDLE at 0x12cd76")
	}
	m.IdleWait[core] = false
	c.PC += c.InsnSize
	c.Steps++
	wake := map[string]any{
		"core": core, "pc_after": hexWord(c.PC), "anchor": hexWord(Timer2PollAnchor),
	}
	m.Timer2PollWakes = append(m.Timer2PollWakes, wake)
	if len(m.Timer2PollWakes) > 16 {
		m.Timer2PollWakes = m.Timer2PollWakes[1:]
	}
	m.recordEvent(map[string]any{
		"kind": "core_woken_by_timer2_completion",
		"core": core, "pc_after": hexWord(c.PC), "anchor": hexWord(Timer2PollAnchor),
		"idle_completion_is_assumption": true, "wake_source_unverified": true,
		"assumed_mechanism": "timer2 pending breaks idle; kick precedent; no vector/RETI/ICFG change",
	})
	return nil
}

// gateDemand mirrors br28_icfg.IcfgState.gate_demand (GATES = 0x300): any known
// closed gate proves delivery impossible (0), both gates known and open give
// 0x300, anything else is unknown (nil). value is the masked ICFG value and
// known the full 32-bit known mask (value is already value&known on the Python
// side, exactly like IcfgState keeps its invariant).
func gateDemand(value, known uint32) *uint32 {
	const gates uint32 = 0x300
	if known&^value&gates != 0 {
		v := uint32(0)
		return &v
	}
	if known&gates == gates {
		v := gates
		return &v
	}
	return nil
}

// icfgDemands mirrors icfg_demands: on the partial-icfg path it is the per-core
// gate_demand; on the concrete path it is c.icfg when known else None. This is
// the fix for the software_irq_with_unknown_icfg counterexample: a partially
// known ICFG whose two gate bits are still determined must surface that gate
// demand instead of nil (which the software-IRQ scan rejects).
func (m *Machine) icfgDemands() [2]*uint32 {
	var out [2]*uint32
	for core := 0; core < 2; core++ {
		if m.Cores[core] == nil {
			continue
		}
		if m.PartialIcfg {
			out[core] = gateDemand(m.Cores[core].ICFG, m.Cores[core].ICFGKnownMask)
			continue
		}
		if m.Cores[core].ICFGKnown {
			v := m.Cores[core].ICFG
			out[core] = &v
		}
	}
	return out
}

// Select mirrors DualCoreMachine.select() branch-for-branch: the CPU1-control
// kick wake, the TIMER2 anchored poll wake, the round-robin choice with the
// idle_wait exclusion, and the bounded defer_core1 issue delay. It returns the
// selected core id. A wake-require violation surfaces as a non-nil error (the
// Python require, which the explore loop surfaces as an upstream failure).
func (m *Machine) Select() (int, error) {
	if m.Cpu1ControlKickWake && m.HardwareKicks != nil && *m.HardwareKicks != m.SeenCpu1ControlKicks {
		m.SeenCpu1ControlKicks = *m.HardwareKicks
		if err := m.wakeCpu1ControlKick(); err != nil {
			return 0, err
		}
	}
	if m.Timer2DelayProjection && m.timer2PollDue() {
		for _, core := range m.timer2AnchoredParkedCores() {
			if err := m.wakeTimer2Poll(core); err != nil {
				return 0, err
			}
		}
	}
	core := 1 - m.LastCore
	if m.Cores[core] == nil {
		core = 0
	}
	if m.IdleWait[core] {
		other := 1 - core
		if m.Cores[other] != nil && !m.IdleWait[other] {
			core = other
		}
	}
	if core == 1 && m.DeferCore1FirstIdle && m.FirstTaskReturns[1] &&
		m.Cores[1].PC == Core1IdlePC && m.DeferredCore1Turns < Core1IdleDelayLimit {
		core = 0
		m.DeferredCore1Turns++
	}
	m.LastCore = core
	m.CurrentCore = core
	m.CoreIcfg = m.icfgDemands()
	return core, nil
}

// ---- JSON state round-trip for the differential harness ---------------------

// mcCoreState is the JSON shape of one MCore.
type mcCoreState struct {
	ID            int    `json:"id"`
	PC            uint32 `json:"pc"`
	Steps         uint64 `json:"steps"`
	ICFG          uint32 `json:"icfg"`
	ICFGKnown     bool   `json:"icfg_known"`
	ICFGKnownMask uint32 `json:"icfg_known_mask"`
	InsnID        string `json:"insn_id"`
	InsnSize      uint32 `json:"insn_size"`
}

// machineInput is the JSON input shape the harness loads before a select batch.
type machineInput struct {
	Cores                 [2]*mcCoreState `json:"cores"`
	IdleWait              []bool          `json:"idle_wait"`
	LastCore              int             `json:"last_core"`
	DeferCore1FirstIdle   bool            `json:"defer_core1_first_idle"`
	FirstTaskReturns      []bool          `json:"first_task_returns"`
	DeferredCore1Turns    int             `json:"deferred_core1_turns"`
	Cpu1ControlKickWake   bool            `json:"cpu1_control_kick_wake"`
	SeenCpu1ControlKicks  int64           `json:"seen_cpu1_control_kicks"`
	HardwareKicks         *int64          `json:"hardware_kicks"`
	Timer2DelayProjection bool            `json:"timer2_delay_projection"`
	Timer2Pending         bool            `json:"timer2_pending"`
	IdleWaitProjection    bool            `json:"idle_wait_projection"`
	// Events / EventCount / Timer2PollWakes are the accumulated ledger the
	// explore engine carries over from the live Python machine so the bounded
	// event windows stay identical across a snapshot -> run -> restore cycle.
	Events          []map[string]any `json:"events"`
	EventCount      int              `json:"event_count"`
	Timer2PollWakes []map[string]any `json:"timer2_poll_wakes"`
	// CurrentCore / CoreIcfg are the hardware.current_core / hardware.core_icfg
	// twins, carried over so a yield-before-first-select still round-trips them.
	CurrentCore int        `json:"current_core"`
	CoreIcfg    [2]*uint32 `json:"core_icfg"`
	// PartialIcfg mirrors DualCoreMachine.partial_icfg.
	PartialIcfg bool `json:"partial_icfg"`
}

// LoadJSON restores the input state from JSON and resets the accumulated
// outputs (events / wakes / current_core / core_icfg) to a fresh-machine state,
// matching a freshly constructed Python DualCoreMachine.
func (m *Machine) LoadJSON(data []byte) error {
	var in machineInput
	if err := json.Unmarshal(data, &in); err != nil {
		return err
	}
	for i := 0; i < 2; i++ {
		if in.Cores[i] == nil {
			m.Cores[i] = nil
			continue
		}
		c := in.Cores[i]
		m.Cores[i] = &MCore{
			ID:            c.ID,
			PC:            c.PC,
			Steps:         c.Steps,
			ICFG:          c.ICFG,
			ICFGKnown:     c.ICFGKnown,
			ICFGKnownMask: c.ICFGKnownMask,
			InsnID:        c.InsnID,
			InsnSize:      c.InsnSize,
		}
	}
	if len(in.IdleWait) >= 2 {
		m.IdleWait[0] = in.IdleWait[0]
		m.IdleWait[1] = in.IdleWait[1]
	}
	m.LastCore = in.LastCore
	m.DeferCore1FirstIdle = in.DeferCore1FirstIdle
	if len(in.FirstTaskReturns) >= 2 {
		m.FirstTaskReturns[0] = in.FirstTaskReturns[0]
		m.FirstTaskReturns[1] = in.FirstTaskReturns[1]
	}
	m.DeferredCore1Turns = in.DeferredCore1Turns
	m.Cpu1ControlKickWake = in.Cpu1ControlKickWake
	m.SeenCpu1ControlKicks = in.SeenCpu1ControlKicks
	m.HardwareKicks = in.HardwareKicks
	m.Timer2DelayProjection = in.Timer2DelayProjection
	if in.Timer2DelayProjection {
		m.Timer2 = NewTimer2Delay()
		m.Timer2.Pending = in.Timer2Pending
	} else {
		m.Timer2 = nil
	}
	m.IdleWaitProjection = in.IdleWaitProjection
	m.Events = in.Events
	if m.Events == nil {
		m.Events = []map[string]any{}
	}
	m.EventCount = in.EventCount
	m.Timer2PollWakes = in.Timer2PollWakes
	if m.Timer2PollWakes == nil {
		m.Timer2PollWakes = []map[string]any{}
	}
	m.CurrentCore = in.CurrentCore
	m.CoreIcfg = in.CoreIcfg
	m.PartialIcfg = in.PartialIcfg
	return nil
}

// StateJSON serializes the select-relevant machine state for the differential
// harness (the Go twin of the Python attributes the harness compares).
func (m *Machine) StateJSON() []byte {
	cores := make([]any, 2)
	for i := 0; i < 2; i++ {
		if m.Cores[i] == nil {
			cores[i] = nil
			continue
		}
		c := m.Cores[i]
		cores[i] = map[string]any{
			"id":              c.ID,
			"pc":              c.PC,
			"steps":           c.Steps,
			"icfg":            c.ICFG,
			"icfg_known":      c.ICFGKnown,
			"icfg_known_mask": c.ICFGKnownMask,
			"insn_id":         c.InsnID,
			"insn_size":       c.InsnSize,
		}
	}
	icfg := make([]any, 2)
	for i := 0; i < 2; i++ {
		if m.CoreIcfg[i] == nil {
			icfg[i] = nil
		} else {
			icfg[i] = *m.CoreIcfg[i]
		}
	}
	var hwKicks any
	if m.HardwareKicks == nil {
		hwKicks = nil
	} else {
		hwKicks = *m.HardwareKicks
	}
	out := map[string]any{
		"cores":                   cores,
		"idle_wait":               []bool{m.IdleWait[0], m.IdleWait[1]},
		"last_core":               m.LastCore,
		"defer_core1_first_idle":  m.DeferCore1FirstIdle,
		"first_task_returns":      []bool{m.FirstTaskReturns[0], m.FirstTaskReturns[1]},
		"deferred_core1_turns":    m.DeferredCore1Turns,
		"cpu1_control_kick_wake":  m.Cpu1ControlKickWake,
		"seen_cpu1_control_kicks": m.SeenCpu1ControlKicks,
		"hardware_kicks":          hwKicks,
		"timer2_delay_projection": m.Timer2DelayProjection,
		"timer2_pending":          m.Timer2 != nil && m.Timer2.Pending,
		"idle_wait_projection":    m.IdleWaitProjection,
		"events":                  m.Events,
		"event_count":             m.EventCount,
		"timer2_poll_wakes":       m.Timer2PollWakes,
		"current_core":            m.CurrentCore,
		"core_icfg":               icfg,
		"partial_icfg":            m.PartialIcfg,
	}
	b, _ := json.Marshal(out)
	return b
}
