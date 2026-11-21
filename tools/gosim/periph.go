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

// PeriphState is the M3 peripheral world state: the two ported models plus an
// explicit flag for the advance-chain models not yet ported. It is the
// concrete realization of the M1 Periph placeholder.
type PeriphState struct {
	Timer *Timer0        // nil = no Timer0 model present
	IRQ   *IRQController // nil = no IRQ controller present

	// OtherAdvanceActive marks any unported advance-chain model (core_ticks,
	// watchdog, ICU, Timer2, software IRQ, SRC-SYNC / ADC-DMA delivery) as
	// potentially active. When true, Go cannot prove the advance phase empty
	// and must hand the attempt back to Python.
	OtherAdvanceActive bool
}

// CanProveAdvanceNoop reports whether this attempt's advance phase is
// statically provable as a no-op. It returns the verdict and, when false, an
// honest reason. This is the M3 "no timer counting" judgment: the only way
// advance is empty is (a) no unported model is active, and (b) Timer0 is either
// absent or not counting and not pending (in which case BR28Model.advance runs
// timer.advance(0), a no-op, and the pending-delivery scan is skipped).
func (p *PeriphState) CanProveAdvanceNoop() (bool, string) {
	if p == nil {
		return false, "no peripheral model present"
	}
	if p.OtherAdvanceActive {
		return false, "unported advance-chain model active (core_ticks/watchdog/ICU/Timer2/software-IRQ/SRC-SYNC/ADC-DMA)"
	}
	if p.Timer == nil {
		return true, "no Timer0 present"
	}
	if p.Timer.Pending {
		return false, "Timer0 pending (delivery scan would run)"
	}
	if p.Timer.Con == nil || *p.Timer.Con&3 == 0 {
		return true, "Timer0 not counting"
	}
	return false, "Timer0 counting"
}
