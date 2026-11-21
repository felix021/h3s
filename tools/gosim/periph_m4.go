package gosim

// M4 peripheral port: the remaining "counting but no delivery" models —
// CoreTickStopped, WatchdogProjection, IcuInitializationProjection, Timer2Delay
// and SoftwareIrqCommands — byte-for-byte aligned with the Python
// br28_model.* classes (docs/gosim-wholeloop-design.md §4). Together with the
// M3 Timer0 / IRQController pair they give the Go side the full advance-chain
// leaf set it needs to step the counting models and detect "pending and
// eligible" deliveries (thrown as an IrqReq back to Python), without porting
// the delivery projections themselves (which stay in br28_machine.py through
// M5).
//
// Red line, as in periph.go: every strict stop (Python Blocked) is a
// *PeriphError whose JSON is byte-identical to Python json.dumps(details,
// sort_keys=True) with compact separators. Nothing is fabricated: unknown
// register words stay nil and raise exactly as the Python models do.

import (
	"fmt"
)

// Register bases and constants for the M4 models (br28_model.py top block).
const (
	CoreTickBase0 = 0xffe0ec
	CoreTickBase1 = 0xffe8ec
	WDTCon        = 0xf2a800
	WDTKey        = WDTCon + 4
	ICuBase0      = 0xffe400
	ICuBase1      = 0xffec00
	Timer2        = 0x00fd0600
	IwkupNumBase  = 0xffe1b0
	SwIrqBase0    = 0xffe1a0
	SwIrqBase1    = 0xffe9a0
	SwIrqSrcBase0 = 0xffe200
	SwIrqSrcBase1 = 0xffea00

	LrcHzNominal          = 200000
	LrcRefTicksPerLrcTick = 16_000_000 / LrcHzNominal // = 80
	Timer2LrcSource       = 3
	ICUCommand0           = 0xca000010
	ICUCommandRegion0     = 0xca000020
	ICUCommandRegion1     = 0xca000040
	WdtKeyFirst           = 0x048c159d
	WdtKeySecond          = 0x26ae37bf
)

func icuIsRegionOffset(off uint32) bool { return off == 0x14 || off == 0x18 }

// ---------------------------------------------------------------------------
// PartialWord (br28_model.PartialWord)
// ---------------------------------------------------------------------------

// PartialWord mirrors br28_model.PartialWord: exact bitwise register state;
// unknown bits are never returned as zero.
type PartialWord struct {
	Value uint32 // value & known
	Known uint32
}

// NewPartialWord mirrors PartialWord(value, known).
func NewPartialWord(value, known uint32) *PartialWord {
	known &= 0xffffffff
	return &PartialWord{Value: value & known, Known: known}
}

// Apply mirrors PartialWord.apply: 'or' sets operand bits known/1, 'and' makes
// the cleared bits known/0 and keeps the rest unknown.
func (w *PartialWord) Apply(kind string, operand uint32) error {
	operand &= 0xffffffff
	switch kind {
	case "or":
		w.Known |= operand
		w.Value |= operand
	case "and":
		w.Known |= ^operand
		w.Value &= operand
	default:
		return fmt.Errorf("unsupported partial-word operation")
	}
	return nil
}

// ---------------------------------------------------------------------------
// CoreTickStopped (br28_model.CoreTickStopped)
// ---------------------------------------------------------------------------

// CoreTickStopped mirrors the Python nominal core-tick counter. Register words
// are nil while uninitialized; Pending is nil (unknown) when the initial CON is
// unknown, otherwise a bool. Overflow sets UnresolvedOverflow and freezes the
// counter until the handler's |=0x40 ACK clears pending.
type CoreTickStopped struct {
	InitialCon         *uint32 // nil = unknown (only None/0 accepted)
	Con                *uint32 // nil = unknown
	Cnt                *uint32
	Prd                *uint32
	Pending            *bool  // nil = unknown
	Writes             int64
	Progress           *int64 // nil = no explicit per-step progress
	Expirations        int64
	UnresolvedOverflow bool
	Acknowledgements   int64
	DeliveryInFlight   bool
	PrdReadback        bool
	PrdWrite           *uint32
	CntHeldSnapshot    bool
	CntSnapshotProbe   *uint32
	FreerunEdgePeriod  bool
}

// NewCoreTickStopped mirrors CoreTickStopped(initial_con, progress, ...).
// initialCon and progress are nil when the caller passes None.
func NewCoreTickStopped(initialCon *uint32, progress *int64, prdReadback, cntHeldSnapshot, freerunEdgePeriod bool) (*CoreTickStopped, error) {
	if initialCon != nil && *initialCon != 0 {
		return nil, fmt.Errorf("only unknown or explicitly disabled core tick supported")
	}
	if progress != nil && (*progress < 0 || *progress > 256) {
		return nil, fmt.Errorf("invalid explicit core tick progress")
	}
	t := &CoreTickStopped{
		InitialCon:        initialCon,
		Con:               initialCon,
		PrdReadback:       prdReadback,
		CntHeldSnapshot:   cntHeldSnapshot,
		FreerunEdgePeriod: freerunEdgePeriod,
		Progress:          progress,
	}
	if initialCon == nil {
		t.Pending = nil
	} else {
		p := false
		t.Pending = &p
	}
	return t, nil
}

// SetCntSnapshotProbe mirrors the probe-only sensitivity value assignment.
func (t *CoreTickStopped) SetCntSnapshotProbe(v *uint32) { t.CntSnapshotProbe = v }

// Read mirrors CoreTickStopped.read(offset).
func (t *CoreTickStopped) Read(offset int) (uint32, *PeriphError) {
	if t.UnresolvedOverflow && !((t.PrdReadback && offset == 8 && t.PrdWrite != nil) ||
		(t.CntHeldSnapshot && offset == 4 && t.Cnt != nil)) {
		return 0, periphErr("core_tick_overflow_requires_resolution")
	}
	if t.UnresolvedOverflow && t.CntHeldSnapshot && offset == 4 {
		if t.CntSnapshotProbe != nil {
			return *t.CntSnapshotProbe, nil
		}
		return *t.Cnt, nil
	}
	if offset == 8 && t.UnresolvedOverflow && t.PrdReadback {
		return *t.PrdWrite, nil
	}
	var value *uint32
	switch offset {
	case 0:
		value = t.Con
	case 4:
		value = t.Cnt
	case 8:
		value = t.Prd
	}
	if value == nil || (offset == 0 && t.Pending == nil) {
		return 0, periphErr("unknown_core_tick_state")
	}
	if offset == 0 && *t.Pending {
		return 0, periphErr("core_tick_pending_readback_unverified")
	}
	return *value, nil
}

// Write mirrors CoreTickStopped.write(offset, value). Returns a *PeriphError
// (Python Blocked) or a plain error (Python ValueError, invalid offset).
func (t *CoreTickStopped) Write(offset int, value uint32) error {
	if t.UnresolvedOverflow {
		return periphErr("core_tick_overflow_requires_resolution")
	}
	if offset == 0 {
		if value&^uint32(0x41) != 0 {
			return periphErr("core_tick_control_not_modeled", "value", hexWord(value))
		}
		if value&1 != 0 {
			if t.Progress == nil {
				return periphErr("core_tick_enable_not_modeled", "value", hexWord(value))
			}
			if t.Cnt == nil || t.Prd == nil || t.Pending == nil {
				return periphErr("unknown_core_tick_state")
			}
			if *t.Prd == 0 || (*t.Prd == 0xffffffff && !t.FreerunEdgePeriod) {
				return periphErr("core_tick_period_edge_unverified", "period", hexWord(*t.Prd))
			}
			if *t.Cnt > *t.Prd {
				return periphErr("core_tick_count_exceeds_period")
			}
		}
		v := value & 1
		t.Con = &v
		if value&64 != 0 {
			p := false
			t.Pending = &p
		}
	} else if offset == 4 || offset == 8 {
		if t.Con != nil && *t.Con&1 != 0 {
			return periphErr("active_core_tick_reconfiguration_unverified")
		}
		if offset == 4 {
			t.Cnt = &value
		} else {
			t.Prd = &value
			t.PrdWrite = &value
		}
	} else {
		return fmt.Errorf("invalid core tick offset")
	}
	t.Writes++
	return nil
}

// Advance mirrors CoreTickStopped.advance.
func (t *CoreTickStopped) Advance() *PeriphError {
	if t.UnresolvedOverflow {
		return periphErr("core_tick_overflow_requires_resolution")
	}
	if t.Con == nil || *t.Con&1 == 0 {
		return nil
	}
	cnt := int64(*t.Cnt)
	prd := int64(*t.Prd)
	periods := (cnt + *t.Progress) / (prd + 1)
	newCnt := uint32((cnt + *t.Progress) % (prd + 1))
	t.Cnt = &newCnt
	if periods != 0 {
		p := true
		t.Pending = &p
		t.Expirations += periods
		t.UnresolvedOverflow = true
	}
	return nil
}

// AckRmw mirrors CoreTickStopped.ack_rmw(kind, operand).
func (t *CoreTickStopped) AckRmw(kind string, operand uint32) *PeriphError {
	if kind != "or" || operand != 0x40 {
		return periphErr("core_tick_rmw_not_modeled", "operation", kind, "operand", hexWord(operand&0xffffffff))
	}
	if t.Pending == nil {
		return periphErr("unknown_core_tick_state")
	}
	if !*t.Pending {
		return periphErr("core_tick_ack_without_pending")
	}
	p := false
	t.Pending = &p
	t.UnresolvedOverflow = false
	t.Acknowledgements++
	t.DeliveryInFlight = false
	return nil
}

// ---------------------------------------------------------------------------
// WatchdogProjection (br28_model.WatchdogProjection)
// ---------------------------------------------------------------------------

// WatchdogProjection mirrors the Python SDK-command watchdog with an optional
// explicit nominal timeout clock. Progress is nil when the caller passes None.
type WatchdogProjection struct {
	ProgressNum  *int64 // numerator (nil = no clock)
	ProgressDen  int64  // denominator
	Con          *uint32
	KeyPhase     int64
	Elapsed      int64
	Remainder    int64
	Commands     int64
	Feeds        int64
	Expired      bool
	MailboxFeeds int64
}

// NewWatchdogProjection mirrors WatchdogProjection(progress). progress is nil
// for None, else [num, den].
func NewWatchdogProjection(progress []int64) (*WatchdogProjection, error) {
	w := &WatchdogProjection{}
	if progress == nil {
		return w, nil
	}
	if len(progress) != 2 || progress[0] < 0 || progress[1] < 1 {
		return nil, fmt.Errorf("watchdog progress needs explicit nonnegative numerator/positive denominator")
	}
	w.ProgressNum = &progress[0]
	w.ProgressDen = progress[1]
	return w, nil
}

// Read mirrors WatchdogProjection.read(address).
func (w *WatchdogProjection) Read(address uint32) (uint32, *PeriphError) {
	if w.Expired {
		return 0, periphErr("watchdog_requires_reset_after_expiry")
	}
	if address != WDTCon {
		return 0, periphErr("watchdog_key_read_not_modeled")
	}
	if w.Con == nil {
		return 0, periphErr("uninitialized_watchdog_control")
	}
	return *w.Con, nil
}

// Write mirrors WatchdogProjection.write(address, value). Returns a
// *PeriphError (Blocked) or a plain error (ValueError, non-watchdog register).
func (w *WatchdogProjection) Write(address, value uint32) error {
	if w.Expired {
		return periphErr("watchdog_requires_reset_after_expiry")
	}
	if address == WDTKey {
		if w.KeyPhase >= 2 {
			return periphErr("watchdog_key_sequence_not_modeled", "value", hexWord(value), "phase", w.KeyPhase)
		}
		var expect uint32
		if w.KeyPhase == 0 {
			expect = WdtKeyFirst
		} else {
			expect = WdtKeySecond
		}
		if value != expect {
			return periphErr("watchdog_key_sequence_not_modeled", "value", hexWord(value), "phase", w.KeyPhase)
		}
		w.KeyPhase++
		return nil
	}
	if address != WDTCon {
		return fmt.Errorf("not a watchdog register")
	}
	if w.KeyPhase != 2 {
		return periphErr("watchdog_command_without_observed_key_pair")
	}
	if value&^uint32(0x100ff) != 0 || (value&31) > 17 {
		return periphErr("unsupported_watchdog_control", "value", hexWord(value))
	}
	if value&32 != 0 && value&0xc0 != 0 {
		return periphErr("watchdog_assert_irq_mode_not_modeled", "value", hexWord(value))
	}
	if value&32 != 0 && w.ProgressNum == nil {
		return periphErr("watchdog_timeout_clock_unknown", "value", hexWord(value))
	}
	if w.Con != nil && *w.Con&32 != 0 && value&32 != 0 && (value&31) != (*w.Con&31) {
		return periphErr("active_watchdog_period_change_not_modeled")
	}
	if value&0x10000 != 0 {
		w.Elapsed = 0
		w.Remainder = 0
		w.Feeds++
	}
	if value&32 == 0 || w.Con == nil || *w.Con&32 == 0 {
		w.Elapsed = 0
		w.Remainder = 0
	}
	v := value &^ 0x10000
	w.Con = &v
	w.KeyPhase = 0
	w.Commands++
	return nil
}

// Advance mirrors WatchdogProjection.advance.
func (w *WatchdogProjection) Advance() *PeriphError {
	if w.Expired {
		return periphErr("watchdog_requires_reset_after_expiry")
	}
	if w.Con == nil || *w.Con&32 == 0 {
		return nil
	}
	ticks := (w.Remainder + *w.ProgressNum) / w.ProgressDen
	w.Remainder = (w.Remainder + *w.ProgressNum) % w.ProgressDen
	w.Elapsed += ticks
	if w.Elapsed >= int64(1)<<(*w.Con&31) {
		w.Expired = true
		return periphErr("watchdog_expired_delivery_not_implemented", "control", hexWord(*w.Con),
			"elapsed_nominal_units", w.Elapsed)
	}
	return nil
}

// MailboxFeed mirrors WatchdogProjection.mailbox_feed.
func (w *WatchdogProjection) MailboxFeed() *PeriphError {
	if w.Expired {
		return periphErr("watchdog_requires_reset_after_expiry")
	}
	w.Elapsed = 0
	w.Remainder = 0
	w.Feeds++
	w.MailboxFeeds++
	return nil
}

// ---------------------------------------------------------------------------
// IcuInitializationProjection (br28_model.IcuInitializationProjection)
// ---------------------------------------------------------------------------

// IcuInitializationProjection mirrors the Python ICU command-countdown model.
// Control words are per-base (0xffe400, 0xffec00); each pending command counts
// down and completes at <= 0.
type IcuInitializationProjection struct {
	Control          [2]*uint32 // nil = unknown initial CON
	RegionProjection bool
	CommandSteps     *int64 // nil = no explicit completion clock (Python None)
	Pending          [2]int64
	HasPending       [2]bool
	Completed        int64
	BusyReads        int64
}

// NewIcuInitializationProjection mirrors IcuInitializationProjection(...).
// control0/control1 are the initial CON words per base (nil = unknown).
func NewIcuInitializationProjection(control0, control1 *uint32, commandSteps *int64, regionProjection bool) (*IcuInitializationProjection, error) {
	if commandSteps != nil && (*commandSteps < 0 || *commandSteps > 10000) {
		return nil, fmt.Errorf("invalid explicit ICU command step count")
	}
	icu := &IcuInitializationProjection{RegionProjection: regionProjection, CommandSteps: commandSteps}
	if control0 != nil && *control0&^uint32(0xc0000001) != 0 {
		return nil, fmt.Errorf("ICU initial configuration outside scoped bits")
	}
	if control1 != nil && *control1&^uint32(0xc0000001) != 0 {
		return nil, fmt.Errorf("ICU initial configuration outside scoped bits")
	}
	icu.Control[0] = control0
	icu.Control[1] = control1
	return icu, nil
}

func icuBaseIndex(address uint32) int {
	if address < ICuBase1 {
		return 0
	}
	return 1
}

// Peek mirrors IcuInitializationProjection.peek(address).
func (icu *IcuInitializationProjection) Peek(address uint32) (uint32, *PeriphError) {
	base := uint32(ICuBase0)
	if address >= ICuBase1 {
		base = ICuBase1
	}
	if icuIsRegionOffset(address - base) {
		return 0, periphErr("icu_region_readback_not_modeled", "address", hexWord(address))
	}
	if address != base {
		return 0, periphErr("icu_command_readback_not_modeled", "address", hexWord(address))
	}
	idx := icuBaseIndex(address)
	if icu.Control[idx] == nil {
		return 0, periphErr("unknown_initial_icu_control", "address", hexWord(address))
	}
	v := *icu.Control[idx]
	if icu.HasPending[idx] {
		v |= 0x40000000
	}
	return v, nil
}

// Read mirrors IcuInitializationProjection.read(address).
func (icu *IcuInitializationProjection) Read(address uint32) (uint32, *PeriphError) {
	v, err := icu.Peek(address)
	if err != nil {
		return 0, err
	}
	if v&0x40000000 != 0 {
		icu.BusyReads++
	}
	return v, nil
}

// Write mirrors IcuInitializationProjection.write(address, value).
func (icu *IcuInitializationProjection) Write(address, value uint32) *PeriphError {
	base := uint32(ICuBase0)
	if address >= ICuBase1 {
		base = ICuBase1
	}
	idx := icuBaseIndex(address)
	offset := address - base
	if icuIsRegionOffset(offset) {
		if !icu.RegionProjection {
			return periphErr("icu_region_write_not_modeled", "address", hexWord(address))
		}
		// region register write is a write-only record; no counter state to carry
		return nil
	}
	if address == base+16 {
		allowed := value == ICUCommand0
		if icu.RegionProjection && (value == ICUCommandRegion0 || value == ICUCommandRegion1) {
			allowed = true
		}
		if !allowed {
			return periphErr("icu_command_not_modeled", "value", hexWord(value))
		}
		if icu.Control[idx] == nil {
			return periphErr("unknown_initial_icu_control", "address", hexWord(base))
		}
		peeked, err := icu.Peek(base)
		if err != nil {
			return err
		}
		if peeked&0x40000000 != 0 {
			return periphErr("icu_prior_command_state_unresolved", "address", hexWord(base))
		}
		if icu.CommandSteps == nil {
			return periphErr("icu_command_progress_unknown", "address", hexWord(address))
		}
		icu.Pending[idx] = *icu.CommandSteps
		icu.HasPending[idx] = true
		return nil
	}
	old, err := icu.Peek(base)
	if err != nil {
		return err
	}
	if icu.HasPending[idx] {
		return periphErr("icu_enable_before_command_completed")
	}
	if value != (old | 1) {
		return periphErr("icu_control_write_not_modeled", "address", hexWord(address), "value", hexWord(value))
	}
	v := old | 1
	icu.Control[idx] = &v
	return nil
}

// Advance mirrors IcuInitializationProjection.advance.
func (icu *IcuInitializationProjection) Advance() {
	if icu.CommandSteps != nil && *icu.CommandSteps == 0 {
		return // explicit stopped completion clock
	}
	for i := 0; i < 2; i++ {
		if !icu.HasPending[i] {
			continue
		}
		icu.Pending[i]--
		if icu.Pending[i] <= 0 {
			icu.HasPending[i] = false
			icu.Completed++
		}
	}
}

// ---------------------------------------------------------------------------
// Timer2Delay (br28_model.Timer2Delay)
// ---------------------------------------------------------------------------

// Timer2Delay mirrors the JL_TIMER2 delay-domain projection (LRC 1/80 fraction
// accumulation, pending set only; no IRQ6 delivery).
type Timer2Delay struct {
	Con          *uint32
	Cnt          *uint32
	Prd          *uint32
	Pending      bool
	LrcRemainder int64
	Remainder    int64
	Expirations  int64
	Writes       int64
	ConReads     int64
	AdvanceCalls int64
}

// NewTimer2Delay mirrors Timer2Delay().
func NewTimer2Delay() *Timer2Delay { return &Timer2Delay{} }

// Write mirrors Timer2Delay.write(address, value).
func (t *Timer2Delay) Write(address, value uint32) *PeriphError {
	offset := int64(address) - Timer2
	t.Writes++
	switch offset {
	case 0:
		mode := value & 3
		if value&^uint32(0xfcf3) != 0 || (mode != 0 && mode != 1) {
			return periphErr("unsupported_timer_mode", "value", hexWord(value), "timer", "timer2")
		}
		if mode != 0 && (value>>10)&15 != Timer2LrcSource {
			return periphErr("unsupported_timer_clock_source", "value", hexWord(value), "timer", "timer2")
		}
		if mode != 0 && (t.Cnt == nil || t.Prd == nil) {
			return periphErr("uninitialized_timer_count_or_period", "timer", "timer2")
		}
		if value&0x4000 != 0 {
			t.Pending = false
		}
		v := value &^ 0xc000
		t.Con = &v
	case 4:
		t.Cnt = &value
		t.LrcRemainder = 0
		t.Remainder = 0
	case 8:
		t.Prd = &value
	default:
		return periphErr("unsupported_timer_register", "address", hexWord(Timer2+uint32(offset)))
	}
	return nil
}

// Read mirrors Timer2Delay.read(address).
func (t *Timer2Delay) Read(address uint32) (uint32, *PeriphError) {
	offset := int64(address) - Timer2
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
		return 0, periphErr("uninitialized_peripheral", "address", hexWord(Timer2+uint32(offset)))
	}
	if offset == 0 {
		t.ConReads++
		val := *v
		if t.Pending {
			val |= 0x8000
		}
		return val, nil
	}
	return *v, nil
}

// Advance mirrors Timer2Delay.advance(source_ticks). Returns a plain error for
// a negative tick count (Python ValueError).
func (t *Timer2Delay) Advance(sourceTicks int64) error {
	if sourceTicks < 0 {
		return fmt.Errorf("negative/noninteger timer progress")
	}
	t.AdvanceCalls++
	if t.Con == nil || *t.Con&3 == 0 {
		return nil
	}
	total := t.LrcRemainder + sourceTicks
	lrcTicks := total / LrcRefTicksPerLrcTick
	t.LrcRemainder = total % LrcRefTicksPerLrcTick
	divisor := int64(Divisors[(*t.Con>>4)&15])
	ticks := (t.Remainder + lrcTicks) / divisor
	t.Remainder = (t.Remainder + lrcTicks) % divisor
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

// StepsToNextExpiration mirrors Timer2Delay.steps_to_next_expiration. The
// second return is false when not counting/uninitialized (Python None).
func (t *Timer2Delay) StepsToNextExpiration(sourceTicksPerStep int64) (int64, bool) {
	if sourceTicksPerStep <= 0 {
		return 0, false
	}
	if t.Con == nil || *t.Con&3 == 0 || t.Cnt == nil || t.Prd == nil {
		return 0, false
	}
	needed := int64(Divisors[(*t.Con>>4)&15])*(int64(*t.Prd)+1-int64(*t.Cnt)) - t.Remainder
	x := LrcRefTicksPerLrcTick*needed - t.LrcRemainder
	return ceilDiv(x, sourceTicksPerStep), true
}

// ---------------------------------------------------------------------------
// SoftwareIrqCommands (br28_model.SoftwareIrqCommands)
// ---------------------------------------------------------------------------

// SoftwareIrqCommands mirrors the Python software-IRQ pending word model.
type SoftwareIrqCommands struct {
	Pending         [2]*PartialWord
	SourceEnable    [2]*PartialWord
	Commands        int64
	Acknowledgements int64
	SourceCommands  int64
}

// NewSoftwareIrqCommands mirrors SoftwareIrqCommands(initial_pending,
// initial_source_enable); a nil slice element means unknown (Python None).
func NewSoftwareIrqCommands(initialPending, initialSourceEnable []*uint32) (*SoftwareIrqCommands, error) {
	if initialPending != nil && len(initialPending) != 2 {
		return nil, fmt.Errorf("software IRQ initial pending requires two explicit low-nibble or unknown fixtures")
	}
	if initialSourceEnable != nil && len(initialSourceEnable) != 2 {
		return nil, fmt.Errorf("software IRQ source enable requires two explicit low-nibble or unknown fixtures")
	}
	if initialPending != nil {
		for _, v := range initialPending {
			if v != nil && *v > 15 {
				return nil, fmt.Errorf("software IRQ initial pending requires two explicit low-nibble or unknown fixtures")
			}
		}
	}
	if initialSourceEnable != nil {
		for _, v := range initialSourceEnable {
			if v != nil && *v > 15 {
				return nil, fmt.Errorf("software IRQ source enable requires two explicit low-nibble or unknown fixtures")
			}
		}
	}
	s := &SoftwareIrqCommands{}
	for i := 0; i < 2; i++ {
		var pv, sv *uint32
		if initialPending != nil {
			pv = initialPending[i]
		}
		if initialSourceEnable != nil {
			sv = initialSourceEnable[i]
		}
		if pv == nil {
			s.Pending[i] = NewPartialWord(0, 0)
		} else {
			s.Pending[i] = NewPartialWord(*pv, 15)
		}
		if sv == nil {
			s.SourceEnable[i] = NewPartialWord(0, 0)
		} else {
			s.SourceEnable[i] = NewPartialWord(*sv, 15)
		}
	}
	return s, nil
}

func swIrqHandles(address uint32) bool {
	return address == SwIrqBase0 || address == SwIrqBase0+4 || address == SwIrqBase1 || address == SwIrqBase1+4
}

func swIrqHandlesSource(address uint32) bool {
	return address == SwIrqSrcBase0 || address == SwIrqSrcBase1
}

// Command mirrors SoftwareIrqCommands.command(address, mask).
func (s *SoftwareIrqCommands) Command(address, mask uint32) *PeriphError {
	if !swIrqHandles(address) {
		return periphErr("software_irq_command_address_unmodeled", "address", hexWord(address))
	}
	if mask&^uint32(15) != 0 {
		return periphErr("software_irq_command_bits_unmodeled", "address", hexWord(address), "mask", hexWord(mask))
	}
	core := 0
	if address >= SwIrqBase1 {
		core = 1
	}
	base := uint32(SwIrqBase0)
	if core == 1 {
		base = SwIrqBase1
	}
	if address == base+4 {
		s.Pending[core].Apply("and", ^mask)
	} else {
		s.Pending[core].Apply("or", mask)
	}
	s.Commands++
	return nil
}

// SourceRmw mirrors SoftwareIrqCommands.source_rmw(address, kind, operand).
func (s *SoftwareIrqCommands) SourceRmw(address uint32, kind string, operand uint32) *PeriphError {
	if !swIrqHandlesSource(address) {
		return periphErr("software_irq_source_address_unmodeled", "address", hexWord(address))
	}
	core := 0
	if address >= SwIrqSrcBase1 {
		core = 1
	}
	if (kind != "and" && kind != "or") || operand&^uint32(0xffffffff) != 0 {
		return periphErr("software_irq_source_rmw_form_unmodeled", "address", hexWord(address))
	}
	s.SourceEnable[core].Apply(kind, operand)
	s.SourceCommands++
	return nil
}

// Acknowledge mirrors SoftwareIrqCommands.acknowledge(core, bit).
func (s *SoftwareIrqCommands) Acknowledge(core int, bit uint32) {
	s.Pending[core].Apply("and", ^(uint32(1)<<bit)&0xffffffff)
	s.Acknowledgements++
}

// ---------------------------------------------------------------------------
// State maps for the byte-for-byte differential tests (driven via ctypes).
// The Go JSON state is parsed by the Python test and compared value-for-value
// against the matching Python model attributes. The field set is the
// advance-relevant state only; report-only bookkeeping (command_values,
// region write counts, transactions, iwkup_writes) stays Python-side.
// ---------------------------------------------------------------------------

// StateMap mirrors the advance-relevant CoreTickStopped attributes.
func (t *CoreTickStopped) StateMap() map[string]any {
	m := map[string]any{
		"writes":              t.Writes,
		"expirations":         t.Expirations,
		"unresolved_overflow": t.UnresolvedOverflow,
		"acknowledgements":    t.Acknowledgements,
		"delivery_in_flight":  t.DeliveryInFlight,
	}
	m["con"] = nilU32(t.Con)
	m["cnt"] = nilU32(t.Cnt)
	m["prd"] = nilU32(t.Prd)
	m["prd_write"] = nilU32(t.PrdWrite)
	m["pending"] = nilBool(t.Pending)
	m["progress"] = nilI64(t.Progress)
	return m
}

// StateMap mirrors the advance-relevant WatchdogProjection attributes.
func (w *WatchdogProjection) StateMap() map[string]any {
	return map[string]any{
		"con":           nilU32(w.Con),
		"key_phase":     w.KeyPhase,
		"elapsed":       w.Elapsed,
		"remainder":     w.Remainder,
		"commands":      w.Commands,
		"feeds":         w.Feeds,
		"expired":       w.Expired,
		"mailbox_feeds": w.MailboxFeeds,
	}
}

// StateMap mirrors the advance-relevant IcuInitializationProjection attrs.
func (icu *IcuInitializationProjection) StateMap() map[string]any {
	pending := make([]any, 2)
	for i := 0; i < 2; i++ {
		if icu.HasPending[i] {
			pending[i] = icu.Pending[i]
		} else {
			pending[i] = nil
		}
	}
	return map[string]any{
		"control0":      nilU32(icu.Control[0]),
		"control1":      nilU32(icu.Control[1]),
		"command_steps": nilI64(icu.CommandSteps),
		"pending":       pending,
		"completed":     icu.Completed,
		"busy_reads":    icu.BusyReads,
	}
}

// StateMap mirrors the advance-relevant Timer2Delay attributes.
func (t *Timer2Delay) StateMap() map[string]any {
	return map[string]any{
		"con":            nilU32(t.Con),
		"cnt":            nilU32(t.Cnt),
		"prd":            nilU32(t.Prd),
		"pending":        t.Pending,
		"lrc_remainder":  t.LrcRemainder,
		"remainder":      t.Remainder,
		"expirations":    t.Expirations,
		"writes":         t.Writes,
		"con_reads":      t.ConReads,
		"advance_calls":  t.AdvanceCalls,
	}
}

// StateMap mirrors the advance-relevant SoftwareIrqCommands attributes.
func (s *SoftwareIrqCommands) StateMap() map[string]any {
	pw := func(w *PartialWord) map[string]any { return map[string]any{"value": w.Value, "known": w.Known} }
	return map[string]any{
		"pending":          []any{pw(s.Pending[0]), pw(s.Pending[1])},
		"source_enable":    []any{pw(s.SourceEnable[0]), pw(s.SourceEnable[1])},
		"commands":         s.Commands,
		"acknowledgements": s.Acknowledgements,
		"source_commands":  s.SourceCommands,
	}
}

func nilU32(p *uint32) any {
	if p == nil {
		return nil
	}
	return *p
}

func nilI64(p *int64) any {
	if p == nil {
		return nil
	}
	return *p
}

func nilBool(p *bool) any {
	if p == nil {
		return nil
	}
	return *p
}
