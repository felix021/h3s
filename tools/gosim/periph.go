package gosim

// M3 peripheral port: Timer0 and IRQController, byte-for-byte aligned with the
// Python br28_model.Timer0 / br28_model.IRQController semantics
// (docs/gosim-wholeloop-design.md §4). This is the first peripheral pair the Go
// side owns, and the advance-noop judgment below is what lets the whole-loop
// execute phase close for the "no timer counting" configuration.
//
// Red line: every strict stop (Python Blocked) is returned as a *PeriphError
// whose JSON is byte-identical to Python json.dumps(details, sort_keys=True)
// with compact separators. Nothing is fabricated: uninitialized con/cnt/prd
// config words / ipmask stay nil and raise exactly as the Python models do.

import (
	"encoding/json"
	"fmt"
)

// Timer0 register base (br28_model.TIMER0) and the STD12M source constants
// (STD12M_SOURCE / STD12M_TICKS_PER_REF_TICK).
const (
	TIMER0         = 0x00fd0400
	Std12MSource   = 5
	std12MTicksNum = 3
	std12MTicksDen = 4
)

// Divisors is the GPTIMER post-divisor lookup (br28_model.DIVISORS), indexed by
// the CON div field (bits 4-7).
var Divisors = [16]uint32{1, 4, 16, 64, 2, 8, 32, 128, 256, 1024, 4096, 16384, 512, 2048, 8192, 32768}

// PeriphError is a peripheral strict stop (Python Blocked). JSON() is
// byte-identical to json.dumps(details, sort_keys=True) with compact
// separators, matching the MemError convention.
type PeriphError struct {
	Kind    string
	Details map[string]any
}

func (e *PeriphError) Error() string { return e.JSON() }

func (e *PeriphError) JSON() string {
	m := make(map[string]any, len(e.Details)+1)
	for k, v := range e.Details {
		m[k] = v
	}
	m["kind"] = e.Kind
	b, _ := json.Marshal(m)
	return string(b)
}

// periphErr builds a *PeriphError from key/value detail pairs.
func periphErr(kind string, kv ...any) *PeriphError {
	d := make(map[string]any, len(kv)/2)
	for i := 0; i+1 < len(kv); i += 2 {
		d[kv[i].(string)] = kv[i+1]
	}
	return &PeriphError{Kind: kind, Details: d}
}

func hexWord(v uint32) string { return fmt.Sprintf("0x%x", v) }

func u32p(v uint32) *uint32 { return &v }

// Timer0 mirrors br28_model.Timer0 (the 0xfd0400 general-purpose timer). The
// three register words are nil while uninitialized, matching Python's None.
type Timer0 struct {
	// AllowedSources is the set of clock-source field values (bits 10-13) the
	// model accepts when mode != 0 (Python Timer0(allowed_sources=...), default
	// (4,)). An unsupported source is a strict stop.
	AllowedSources []uint32

	Con *uint32 // CON word (value &^ 0xc000), nil = uninitialized
	Cnt *uint32 // CNT word, nil = uninitialized
	Prd *uint32 // PRD word, nil = uninitialized

	Pending          bool
	Remainder        int64 // post-divisor fractional remainder (0..divisor-1)
	SourceRemainder  int64 // STD12M 3/4 fractional remainder (0..3)
	Expirations      int64
	DeliveryInFlight bool
}

// NewTimer0 returns a Timer0 with the default allowed source set (source 4),
// matching br28_model.Timer0().
func NewTimer0(allowed ...uint32) *Timer0 {
	if len(allowed) == 0 {
		allowed = []uint32{4}
	}
	return &Timer0{AllowedSources: append([]uint32(nil), allowed...)}
}

func (t *Timer0) sourceAllowed(src uint32) bool {
	for _, a := range t.AllowedSources {
		if a == src {
			return true
		}
	}
	return false
}

// Write mirrors Timer0.write (offset 0=CON, 4=CNT, 8=PRD).
func (t *Timer0) Write(offset int, value uint32) *PeriphError {
	switch offset {
	case 0:
		mode := value & 3
		if value&^uint32(0xfcf3) != 0 || (mode != 0 && mode != 1) {
			return periphErr("unsupported_timer_mode", "value", hexWord(value))
		}
		if mode != 0 && !t.sourceAllowed((value>>10)&15) {
			return periphErr("unsupported_timer_clock_source", "value", hexWord(value))
		}
		if mode != 0 && (t.Cnt == nil || t.Prd == nil) {
			return periphErr("uninitialized_timer_count_or_period")
		}
		if value&0x4000 != 0 {
			t.Pending = false
			t.DeliveryInFlight = false
		}
		t.Con = u32p(value &^ 0xc000)
	case 4:
		t.Cnt = u32p(value)
		t.Remainder = 0
		t.SourceRemainder = 0
	case 8:
		t.Prd = u32p(value)
	default:
		return periphErr("unsupported_timer_register", "address", hexWord(TIMER0+uint32(offset)))
	}
	return nil
}

// Read mirrors Timer0.read (CON bit 15 = modeled pending projection).
func (t *Timer0) Read(offset int) (uint32, *PeriphError) {
	var v *uint32
	switch offset {
	case 0:
		v = t.Con
	case 4:
		v = t.Cnt
	case 8:
		v = t.Prd
	}
	if v == nil {
		return 0, periphErr("uninitialized_peripheral", "address", hexWord(TIMER0+uint32(offset)))
	}
	val := *v
	if t.Pending && offset == 0 {
		val |= 0x8000
	}
	return val, nil
}

// Advance mirrors Timer0.advance: prescale counting plus pending set. The
// source-ticks input must be a non-negative int (Python raises ValueError
// otherwise, which the caller surfaces as a distinct error).
func (t *Timer0) Advance(sourceTicks int64) error {
	if sourceTicks < 0 {
		return fmt.Errorf("negative/noninteger timer progress")
	}
	if t.Con == nil || *t.Con&3 == 0 {
		return nil
	}
	if (*t.Con>>10)&15 == Std12MSource {
		// source_ticks is in RC16M (16 MHz) reference ticks; STD12M counts at
		// 12/16 = 3/4, fractional remainder accumulates per step.
		total := t.SourceRemainder + sourceTicks*std12MTicksNum
		sourceTicks = total / std12MTicksDen
		t.SourceRemainder = total % std12MTicksDen
	}
	divisor := int64(Divisors[(*t.Con>>4)&15])
	ticks := (t.Remainder + sourceTicks) / divisor
	t.Remainder = (t.Remainder + sourceTicks) % divisor
	cnt := int64(*t.Cnt)
	prd := int64(*t.Prd)
	periods := (cnt + ticks) / (prd + 1)
	t.Cnt = u32p(uint32((cnt + ticks) % (prd + 1)))
	t.Expirations += periods
	if periods != 0 {
		t.Pending = true
	}
	return nil
}

// StepsToNextExpiration mirrors Timer0.steps_to_next_expiration: the exact
// closed-form model-step count until the next expiration (periods >= 1). The
// second return is false when not counting or uninitialized (Python returns
// None). rate is the source ticks per model step (always positive).
func (t *Timer0) StepsToNextExpiration(rate int64) (int64, bool) {
	if rate <= 0 {
		return 0, false
	}
	if t.Con == nil || *t.Con&3 == 0 || t.Cnt == nil || t.Prd == nil {
		return 0, false
	}
	num := rate
	den := int64(1)
	if (*t.Con>>10)&15 == Std12MSource {
		num = rate * std12MTicksNum
		den = std12MTicksDen
	}
	needed := int64(Divisors[(*t.Con>>4)&15])*(int64(*t.Prd)+1-int64(*t.Cnt)) - t.Remainder
	x := den*needed - t.SourceRemainder
	// Python returns -(-x//num), i.e. ceil(x/num). x is normally >= 1 for a
	// real counting state, but a direct CNT write can push cnt above prd and
	// make x negative, so the ceil must match Python's floor-division form
	// exactly (Go's / truncates toward zero and diverges for negative x).
	return ceilDiv(x, num), true
}

// ceilDiv computes ceil(x/num) for num > 0, matching Python's -(-x//num).
func ceilDiv(x, num int64) int64 {
	if x >= 0 {
		return (x + num - 1) / num
	}
	return -((-x) / num)
}

// IRQController mirrors br28_model.IRQController: 32 ICFG words (8 nibbles
// each) plus the IPMASK, and the priority/eligibility judgment.
type IRQController struct {
	Config                  [32]*uint32
	IPMask                  *uint32
	UnmaskableIRQProjection bool
}

// NewIRQController returns an IRQController with all 32 config words
// uninitialized and no ipmask (Python IRQController()).
func NewIRQController() *IRQController { return &IRQController{} }

// Eligible mirrors IRQController.eligible: independent gates 8/9 first, then
// the per-IRQ config nibble, then priority vs IPMASK (or the opt-in unmaskable
// projection for priorities 6/7).
func (ic *IRQController) Eligible(index int, icfg uint32) (bool, *PeriphError) {
	if icfg&0x300 != 0x300 {
		return false, nil
	}
	v := ic.Config[index/8]
	if v == nil {
		return false, periphErr("uninitialized_irq_configuration", "irq", index)
	}
	nibble := (*v >> uint((index%8)*4)) & 15
	if nibble&1 == 0 {
		return false, nil
	}
	if ic.IPMask == nil {
		return false, periphErr("uninitialized_ipmask")
	}
	priority := nibble >> 1
	if priority >= 6 {
		if ic.UnmaskableIRQProjection {
			return true, nil
		}
		return false, periphErr("unmodeled_unmaskable_irq", "irq", index, "priority", priority)
	}
	return priority >= *ic.IPMask, nil
}

// PeriphState is the peripheral world state: the ported advance-chain leaf
// models (M3 Timer0 / IRQController, M4 core ticks / watchdog / ICU / Timer2 /
// software IRQ) plus an explicit flag for the advance-chain models still not
// ported (SRC-SYNC / ADC-DMA delivery detection, M5). It is the concrete
// realization of the M1 Periph placeholder.
type PeriphState struct {
	Timer *Timer0        // nil = no Timer0 model present
	IRQ   *IRQController // nil = no core-0 IRQ controller present
	IRQ1  *IRQController // nil = no core-1 IRQ controller present (multicore)

	// Multicore delivery-scan state (br28_model.BR28Model multicore fields):
	// whether the dual-core select/advance path is active, the per-core ICFG
	// gates (hardware.core_icfg; nil = unknown), and the CPU1 held-projection
	// bookkeeping the core-tick / Timer0 / software scans gate on.
	MulticoreEnabled       bool
	CoreIcfg               [2]*uint32 // hardware.core_icfg per core (nil = unknown)
	Cpu1Control            *uint32    // hardware.cpu1_control (nil = unknown)
	Cpu1HeldProjection     bool
	CoreTickCountWhileHeld *bool // nil = unknown

	CoreTicks   [2]*CoreTickStopped          // per-core core tick; nil = absent
	Watchdog    *WatchdogProjection          // nil = absent
	ICU         *IcuInitializationProjection // nil = absent
	Timer2      *Timer2Delay                 // nil = absent
	SoftwareIRQ *SoftwareIrqCommands         // nil = absent
	SrcSync     *JLSrcSyncDelivery           // nil = no SRC-SYNC delivery model (M5)
	AudioAdcDma *AudioAdcDmaDelivery         // nil = no ADC-DMA model (M5)

	// Delivery-request flags (mirror the BR28Model kwargs of the same names).
	// False = a pending-and-eligible IRQ is a strict stop (the strict default);
	// true = it becomes an IrqReq handed back to Python for the delivery
	// projection.
	CoreTickDeliveryRequests    bool
	Timer0IRQDeliveryRequests   bool
	SoftwareIRQDeliveryRequests bool
	SrcSyncDeliveryRequests     bool
	AudioAdcDmaDeliveryRequests bool

	// SourceTicksPerStep is the RC16M reference tick rate per model step
	// (Python source_ticks_per_step, default 4).
	SourceTicksPerStep int64
	// TimerLsbTicksPerStep, when non-nil, is the Timer0 source-0 (LSB) rate
	// (Python timer_lsb_ticks_per_step).
	TimerLsbTicksPerStep *int64

	// CompletedModelSteps is the per-model-step counter (Python
	// counts['completed_model_steps']), incremented once per Advance call. The
	// SRC-SYNC completion timing reads it.
	CompletedModelSteps int64
}

// AdvanceResult is the outcome of one whole-loop advance phase over the ported
// counting models. Exactly one of the three is populated.
type AdvanceResult struct {
	Empty bool         // nothing counted and nothing pending: the phase is a no-op
	Irq   *IrqReq      // a pending-and-eligible delivery request to hand back
	Err   *PeriphError // a strict stop surfaced during the advance (hazard)
}

// irqForCore returns the per-core IRQ controller (core 0 -> p.IRQ, core 1 ->
// p.IRQ1). A nil controller surfaces as an uninitialized-configuration stop in
// irqEligible, matching the Python `irq`/`irq1` split.
func (p *PeriphState) irqForCore(core int) *IRQController {
	if core == 1 {
		return p.IRQ1
	}
	return p.IRQ
}

// coreGate returns the per-core ICFG gate under the multicore delivery scan
// (Python hardware.core_icfg.get(core)); nil = unknown. It is always nil in the
// single-core path, matching `core_icfg.get(core) if multicore_enabled else None`.
func (p *PeriphState) coreGate(core int) *uint32 {
	if !p.MulticoreEnabled {
		return nil
	}
	return p.CoreIcfg[core]
}

// gateArg returns the eligibility icfg argument for a delivery scan: multicore
// uses the per-core gate (0x300 when the gate is unknown), single-core uses the
// caller's icfg directly (Python `0x300 if gate is None else gate`).
func (p *PeriphState) gateArg(core int, icfg uint32) uint32 {
	if g := p.coreGate(core); g != nil {
		return *g
	}
	if p.MulticoreEnabled {
		return 0x300
	}
	return icfg
}

// irqEligible wraps the IRQ controller eligibility judgment for one core,
// surfacing a missing controller as an uninitialized-configuration stop (the
// same shape Python raises when the per-IRQ config word is unknown).
func (p *PeriphState) irqEligible(core, index int, icfg uint32) (bool, *PeriphError) {
	ic := p.irqForCore(core)
	if ic == nil {
		return false, periphErr("uninitialized_irq_configuration", "irq", index)
	}
	return ic.Eligible(index, icfg)
}

// Advance performs one model-step advance over the ported counting models in
// the Python BR28Model.advance order (SRC-SYNC completion stepping, ICU,
// watchdog, core ticks, Timer0, Timer2, ADC-DMA pending scan, SRC-SYNC pending
// scan, software IRQ pending scan) and detects pending-and-eligible deliveries.
//
// This is the dual-core form: the core-tick (IRQ3), Timer0 (IRQ4) and software
// IRQ (IRQ124-127) scans walk both cores using the per-core controller
// (IRQ/IRQ1) and the per-core ICFG gate (CoreIcfg) when multicore is enabled,
// with the CPU1 held-projection gate for core 1; the ADC-DMA (IRQ68) and
// SRC-SYNC (IRQ89) scans stay pinned to core 0 exactly as the Python delivery
// scans pin them. The single-core path is unchanged (core 0 only, gate = the
// caller's icfg).
//
// It does NOT execute the delivery projection — a pending eligible IRQ is
// returned as Irq to hand back to Python (docs/gosim-wholeloop-design.md §4). A
// model whose own advance raises a strict stop (e.g. watchdog expiry) is
// returned as Err, exactly as the Python model raises Blocked.
func (p *PeriphState) Advance(icfg uint32) AdvanceResult {
	if p == nil {
		return AdvanceResult{}
	}
	empty := true
	srcTicks := p.SourceTicksPerStep
	if srcTicks == 0 {
		srcTicks = 4 // Python source_ticks_per_step default
	}

	// Per-model-step counter (Python counts['completed_model_steps']), bumped
	// once per advance call before any model reads it.
	p.CompletedModelSteps++

	// SRC-SYNC completion stepping (Python advance order, before ICU): the
	// explicit completion_steps timing flips started -> pending.
	if p.SrcSync != nil {
		if p.SrcSync.Started || p.SrcSync.Pending {
			empty = false
		}
		p.SrcSync.Advance(p.CompletedModelSteps)
	}

	// ICU command countdown: no delivery, no strict stop.
	if p.ICU != nil {
		if p.ICU.HasPending[0] || p.ICU.HasPending[1] {
			empty = false
		}
		p.ICU.Advance()
	}

	// Watchdog count; expiry is a strict stop (delivery not implemented).
	if p.Watchdog != nil {
		if p.Watchdog.Con != nil && *p.Watchdog.Con&32 != 0 {
			empty = false
		}
		if err := p.Watchdog.Advance(); err != nil {
			return AdvanceResult{Err: err}
		}
	}

	// Core ticks (IRQ3) — both cores, in core order. The CPU1 held-projection
	// preflight mirrors the Python gate before the per-core loop.
	if p.CoreTicks[1] != nil && p.CoreTicks[1].Con != nil && *p.CoreTicks[1].Con&1 != 0 {
		if !p.Cpu1HeldProjection || p.Cpu1Control == nil {
			return AdvanceResult{Err: periphErr("core1_execution_state_unknown")}
		}
		if *p.Cpu1Control == 0 && p.CoreTickCountWhileHeld == nil {
			return AdvanceResult{Err: periphErr("core1_tick_held_clock_unknown")}
		}
	}
	for core := 0; core < 2; core++ {
		ct := p.CoreTicks[core]
		if ct == nil {
			continue
		}
		if core == 1 && p.Cpu1HeldProjection && p.Cpu1Control != nil && *p.Cpu1Control == 0 &&
			p.CoreTickCountWhileHeld != nil && !*p.CoreTickCountWhileHeld {
			continue
		}
		if ct.Con != nil && *ct.Con&1 != 0 {
			empty = false
		}
		if ct.UnresolvedOverflow {
			if !p.CoreTickDeliveryRequests {
				return AdvanceResult{Err: periphErr("core_tick_overflow_requires_resolution")}
			}
			if ct.DeliveryInFlight {
				continue
			}
			if eligible, err := p.irqEligible(core, 3, p.gateArg(core, 0x300)); err != nil {
				return AdvanceResult{Err: periphErr("core_tick_delivery_blocked", "core", core, "irq", 3, "reason", err.Kind)}
			} else if eligible {
				ct.DeliveryInFlight = true
				return AdvanceResult{Irq: &IrqReq{Core: core, Irq: 3}}
			}
			continue
		}
		before := ct.Expirations
		if err := ct.Advance(); err != nil {
			return AdvanceResult{Err: err}
		}
		if ct.Expirations != before {
			if !p.CoreTickDeliveryRequests {
				return AdvanceResult{Err: periphErr("core_tick_overflow_delivery_unresolved", "core", core, "irq", 3,
					"note", "internal pending set; no guessed readback bit, wake or interrupt entry")}
			}
			if eligible, err := p.irqEligible(core, 3, p.gateArg(core, 0x300)); err != nil {
				return AdvanceResult{Err: periphErr("core_tick_delivery_blocked", "core", core, "irq", 3, "reason", err.Kind)}
			} else if eligible {
				ct.DeliveryInFlight = true
				return AdvanceResult{Irq: &IrqReq{Core: core, Irq: 3}}
			}
		}
	}

	// Timer0 count + pending (IRQ4) delivery scan.
	if p.Timer != nil {
		if p.Timer.Con != nil && *p.Timer.Con&3 != 0 {
			empty = false
		}
		var rate int64
		if p.Timer.Con == nil || *p.Timer.Con&3 == 0 {
			rate = 0
		} else if (*p.Timer.Con>>10)&15 == 0 {
			if p.TimerLsbTicksPerStep != nil {
				rate = *p.TimerLsbTicksPerStep
			} else {
				rate = 0 // LSB source with no explicit rate: advance is a no-op
			}
		} else {
			rate = srcTicks
		}
		if err := p.Timer.Advance(rate); err != nil {
			return AdvanceResult{Err: periphErr("timer_advance_error")}
		}
		if p.Timer.Pending {
			empty = false
			if p.MulticoreEnabled {
				for core := 0; core < 2; core++ {
					if core == 1 && (p.Cpu1Control == nil || *p.Cpu1Control&1 == 0) {
						continue
					}
					if gate := p.coreGate(core); gate == nil {
						// Unknown gate: a 0x300-eligible Timer0 is a strict stop
						// (pending_irq_with_unknown_core_icfg), not a delivery.
						if eligible, err := p.irqEligible(core, 4, 0x300); err != nil {
							return AdvanceResult{Err: err}
						} else if eligible {
							return AdvanceResult{Err: periphErr("pending_irq_with_unknown_core_icfg", "core", core, "irq", 4)}
						}
						continue
					} else {
						eligible, err := p.irqEligible(core, 4, *gate)
						if err != nil {
							return AdvanceResult{Err: err}
						}
						if eligible {
							if !p.Timer0IRQDeliveryRequests {
								return AdvanceResult{Err: periphErr("interrupt_delivery_not_implemented", "core", core, "irq", 4)}
							}
							if !p.Timer.DeliveryInFlight {
								p.Timer.DeliveryInFlight = true
								return AdvanceResult{Irq: &IrqReq{Core: core, Irq: 4}}
							}
						}
					}
				}
			} else {
				if eligible, err := p.irqEligible(0, 4, icfg); err != nil {
					return AdvanceResult{Err: err}
				} else if eligible {
					if !p.Timer0IRQDeliveryRequests {
						return AdvanceResult{Err: periphErr("interrupt_delivery_not_implemented", "irq", 4,
							"note", "Timer0 pending and enabled after priority filtering; CPU entry not bypassed")}
					}
					if !p.Timer.DeliveryInFlight {
						p.Timer.DeliveryInFlight = true
						return AdvanceResult{Irq: &IrqReq{Core: 0, Irq: 4}}
					}
				}
			}
		}
	}

	// Timer2 count; pending is NOT an IRQ delivery but a machine-side anchored
	// poll wake, which the Go loop cannot model — hand it back as a stop.
	if p.Timer2 != nil {
		if p.Timer2.Con != nil && *p.Timer2.Con&3 != 0 {
			empty = false
		}
		if err := p.Timer2.Advance(srcTicks); err != nil {
			return AdvanceResult{Err: periphErr("timer2_advance_error")}
		}
		if p.Timer2.Pending {
			empty = false
			return AdvanceResult{Err: periphErr("timer2_completion_wake", "address", hexWord(Timer2))}
		}
	}

	// ADC-DMA pending scan (IRQ68, pinned to core 0 as in the Python delivery
	// scan).
	if dma := p.AudioAdcDma; dma != nil && dma.Delivery && dma.Pending {
		empty = false
		if eligible, err := p.irqEligible(0, AudioAdcDmaIRQ68, p.gateArg(0, icfg)); err != nil {
			return AdvanceResult{Err: periphErr("audio_dma_irq_delivery_blocked", "core", 0, "irq", AudioAdcDmaIRQ68, "reason", err.Kind)}
		} else if eligible {
			if !p.AudioAdcDmaDeliveryRequests {
				return AdvanceResult{Err: periphErr("interrupt_delivery_not_implemented", "core", 0, "irq", AudioAdcDmaIRQ68,
					"note", "audio ADC DMA pending and eligible; CPU entry not bypassed — enable the audio ADC DMA delivery requests")}
			}
			if !dma.DeliveryInFlight {
				dma.DeliveryInFlight = true
				return AdvanceResult{Irq: &IrqReq{Core: 0, Irq: AudioAdcDmaIRQ68}}
			}
		}
	}

	// SRC-SYNC pending scan (IRQ89, pinned to core 0 as in the Python delivery
	// scan).
	if src := p.SrcSync; src != nil && src.Pending {
		empty = false
		if eligible, err := p.irqEligible(0, JLSrcSyncIRQ89, p.gateArg(0, icfg)); err != nil {
			return AdvanceResult{Err: periphErr("src_sync_irq_delivery_blocked", "core", 0, "irq", JLSrcSyncIRQ89, "reason", err.Kind)}
		} else if eligible {
			if !p.SrcSyncDeliveryRequests {
				return AdvanceResult{Err: periphErr("interrupt_delivery_not_implemented", "core", 0, "irq", JLSrcSyncIRQ89,
					"note", "SRC_SYNC completion pending and eligible; CPU entry not bypassed — enable the SRC_SYNC delivery requests")}
			}
			if !src.DeliveryInFlight {
				src.DeliveryInFlight = true
				return AdvanceResult{Irq: &IrqReq{Core: 0, Irq: JLSrcSyncIRQ89}}
			}
		}
	}

	// Software IRQ pending scan (IRQ124-127), both cores.
	if p.SoftwareIRQ != nil {
		for core := 0; core < 2; core++ {
			if core == 1 && (!p.MulticoreEnabled || p.Cpu1Control == nil || *p.Cpu1Control&1 == 0) {
				continue
			}
			gate := p.coreGate(core)
			word := p.SoftwareIRQ.Pending[core]
			for bit := uint32(0); bit < 4; bit++ {
				if word.Known&(1<<bit) != 0 && word.Value&(1<<bit) == 0 {
					continue
				}
				gateICFG := uint32(0x300)
				if gate != nil {
					gateICFG = *gate
				} else if !p.MulticoreEnabled {
					gateICFG = icfg
				}
				eligible, err := p.irqEligible(core, int(124+bit), gateICFG)
				if err != nil {
					if err.Kind == "unmodeled_unmaskable_irq" && p.MulticoreEnabled {
						gateDemand := "unknown"
						if gate != nil {
							gateDemand = hexWord(*gate)
						}
						return AdvanceResult{Err: periphErr("unmodeled_unmaskable_irq", "core", core, "irq", 124+bit,
							"priority", mapInt(err.Details["priority"]), "source", "software_irq",
							"pending_known", word.Known&(1<<bit) != 0, "icfg_gate_demand", gateDemand)}
					}
					return AdvanceResult{Err: err}
				}
				if eligible {
					if gate == nil && p.MulticoreEnabled {
						return AdvanceResult{Err: periphErr("software_irq_with_unknown_icfg", "core", core, "irq", 124+bit)}
					}
					if p.SoftwareIRQDeliveryRequests {
						return AdvanceResult{Irq: &IrqReq{Core: core, Irq: 124 + bit}}
					}
					kind := "software_irq_delivery_not_implemented"
					if word.Known&(1<<bit) == 0 {
						kind = "software_irq_pending_unknown"
					}
					return AdvanceResult{Err: periphErr(kind, "core", core, "irq", 124+bit)}
				}
			}
		}
	}

	return AdvanceResult{Empty: empty}
}

// HasActiveAdvance reports whether the advance phase has anything to do (a
// counting model, a pending bit, or a pending-eligible scan that would run).
// It is the M5 form of the M3 "other model active" flag: every advance-chain
// model is now a real field (SRC-SYNC / ADC-DMA landed in M5), so nothing is
// left on an explicit unported-model flag.
func (p *PeriphState) HasActiveAdvance() bool {
	if p == nil {
		return false
	}
	if p.Timer != nil {
		if p.Timer.Pending {
			return true
		}
		if p.Timer.Con != nil && *p.Timer.Con&3 != 0 {
			return true
		}
	}
	for _, ct := range p.CoreTicks {
		if ct == nil {
			continue
		}
		if ct.UnresolvedOverflow {
			return true
		}
		if ct.Pending != nil && *ct.Pending {
			return true
		}
		if ct.Con != nil && *ct.Con&1 != 0 {
			return true
		}
	}
	if p.Watchdog != nil {
		if p.Watchdog.Expired {
			return true
		}
		if p.Watchdog.Con != nil && *p.Watchdog.Con&32 != 0 {
			return true
		}
	}
	if p.ICU != nil {
		if p.ICU.HasPending[0] || p.ICU.HasPending[1] {
			return true
		}
	}
	if p.Timer2 != nil {
		if p.Timer2.Pending {
			return true
		}
		if p.Timer2.Con != nil && *p.Timer2.Con&3 != 0 {
			return true
		}
	}
	if p.SoftwareIRQ != nil {
		return true // the pending scan runs and may consult eligibility
	}
	if p.SrcSync != nil {
		if p.SrcSync.Started || p.SrcSync.Pending {
			return true
		}
	}
	if p.AudioAdcDma != nil && p.AudioAdcDma.Delivery && p.AudioAdcDma.Pending {
		return true
	}
	return false
}

// CanProveAdvanceNoop reports whether this attempt's advance phase is
// statically provable as a no-op. It returns the verdict and, when false, an
// honest reason. The M3 "no timer counting" judgment is now extended across
// the full ported model set: advance is empty only when every ported model is
// absent, not counting, and not pending.
func (p *PeriphState) CanProveAdvanceNoop() (bool, string) {
	if p == nil {
		return false, "no peripheral model present"
	}
	if p.HasActiveAdvance() {
		return false, "advance-chain model counting or pending"
	}
	return true, "no advance-chain model active"
}
