package gosim

// Whole-loop advance state transfer: the JSON shapes the differential harness
// uses to move the live Python BR28Model peripheral state into a Go PeriphState
// (and back out) so the two advance chains step from byte-identical state and
// are compared after every step.
//
// Scope (docs/gosim-wholeloop-design.md §4 M3-M5): the full advance-chain leaf
// set — Timer0 + IRQController + CoreTickStopped + WatchdogProjection +
// IcuInitializationProjection + Timer2Delay + SoftwareIrqCommands +
// JLSrcSyncDelivery + AudioAdcDmaDelivery — plus the advance-chain scalar flags.
// The watchdog/icu/timer2/software-irq/src-sync/adc-dma leaves carry exactly
// the advance-relevant runtime fields their per-model StateMap exports
// (periph_m4.go / periph_m5.go) expose; the report-only bookkeeping (watchdog
// command_values, ICU region write records, ADC-DMA store/block records) stays
// Python-side and is never silently dropped from an advance parity view. A field
// Python holds as None round-trips as JSON null -> nil, and an absent model
// (JSON null) clears the corresponding PeriphState slot.

import (
	"encoding/json"
	"fmt"
)

// ---- Timer0 ------------------------------------------------------------------

type timer0StateJSON struct {
	AllowedSources   []uint32 `json:"allowed_sources"`
	Con              *uint32  `json:"con"`
	Cnt              *uint32  `json:"cnt"`
	Prd              *uint32  `json:"prd"`
	Pending          bool     `json:"pending"`
	Remainder        int64    `json:"remainder"`
	SourceRemainder  int64    `json:"source_remainder"`
	Expirations      int64    `json:"expirations"`
	DeliveryInFlight bool     `json:"delivery_in_flight"`
}

func timer0FromJSON(s *timer0StateJSON) (*Timer0, error) {
	allowed := s.AllowedSources
	if len(allowed) == 0 {
		allowed = []uint32{4}
	}
	t := NewTimer0(allowed...)
	t.Con = s.Con
	t.Cnt = s.Cnt
	t.Prd = s.Prd
	t.Pending = s.Pending
	t.Remainder = s.Remainder
	t.SourceRemainder = s.SourceRemainder
	t.Expirations = s.Expirations
	t.DeliveryInFlight = s.DeliveryInFlight
	return t, nil
}

func timer0ToJSON(t *Timer0) *timer0StateJSON {
	return &timer0StateJSON{
		AllowedSources:   append([]uint32(nil), t.AllowedSources...),
		Con:              t.Con,
		Cnt:              t.Cnt,
		Prd:              t.Prd,
		Pending:          t.Pending,
		Remainder:        t.Remainder,
		SourceRemainder:  t.SourceRemainder,
		Expirations:      t.Expirations,
		DeliveryInFlight: t.DeliveryInFlight,
	}
}

// ---- IRQController ------------------------------------------------------------

type irqStateJSON struct {
	Config                 [32]*uint32 `json:"config"`
	IPMask                 *uint32     `json:"ipmask"`
	UnmaskableIRQProjection bool        `json:"unmaskable_irq_projection"`
}

func irqFromJSON(s *irqStateJSON) *IRQController {
	return &IRQController{
		Config:                  s.Config,
		IPMask:                  s.IPMask,
		UnmaskableIRQProjection: s.UnmaskableIRQProjection,
	}
}

func irqToJSON(ic *IRQController) *irqStateJSON {
	return &irqStateJSON{
		Config:                  ic.Config,
		IPMask:                  ic.IPMask,
		UnmaskableIRQProjection: ic.UnmaskableIRQProjection,
	}
}

// ---- CoreTickStopped ----------------------------------------------------------

type coreTickStateJSON struct {
	Con                   *uint32 `json:"con"`
	Cnt                   *uint32 `json:"cnt"`
	Prd                   *uint32 `json:"prd"`
	Pending               *bool   `json:"pending"`
	Writes                int64   `json:"writes"`
	Progress              *int64  `json:"progress"`
	Expirations           int64   `json:"expirations"`
	UnresolvedOverflow    bool    `json:"unresolved_overflow"`
	Acknowledgements      int64   `json:"acknowledgements"`
	DeliveryInFlight      bool    `json:"delivery_in_flight"`
	PrdWrite              *uint32 `json:"prd_write"`
	PrdReadback           bool    `json:"prd_readback"`
	CntHeldSnapshot       bool    `json:"cnt_held_snapshot"`
	FreerunEdgePeriod     bool    `json:"freerun_edge_period"`
	CntSnapshotProbeValue *uint32 `json:"cnt_snapshot_probe_value"`
}

func coreTickFromJSON(s *coreTickStateJSON) (*CoreTickStopped, error) {
	// NewCoreTickStopped rejects a non-nil/nonzero initial_con, so construct
	// with the config flags only and overwrite the runtime fields directly.
	t, err := NewCoreTickStopped(nil, s.Progress, s.PrdReadback, s.CntHeldSnapshot, s.FreerunEdgePeriod)
	if err != nil {
		return nil, err
	}
	t.Con = s.Con
	t.Cnt = s.Cnt
	t.Prd = s.Prd
	t.Pending = s.Pending
	t.Writes = s.Writes
	t.Expirations = s.Expirations
	t.UnresolvedOverflow = s.UnresolvedOverflow
	t.Acknowledgements = s.Acknowledgements
	t.DeliveryInFlight = s.DeliveryInFlight
	t.PrdWrite = s.PrdWrite
	t.CntSnapshotProbe = s.CntSnapshotProbeValue
	return t, nil
}

func coreTickToJSON(t *CoreTickStopped) *coreTickStateJSON {
	return &coreTickStateJSON{
		Con:                   t.Con,
		Cnt:                   t.Cnt,
		Prd:                   t.Prd,
		Pending:               t.Pending,
		Writes:                t.Writes,
		Progress:              t.Progress,
		Expirations:           t.Expirations,
		UnresolvedOverflow:    t.UnresolvedOverflow,
		Acknowledgements:      t.Acknowledgements,
		DeliveryInFlight:      t.DeliveryInFlight,
		PrdWrite:              t.PrdWrite,
		PrdReadback:           t.PrdReadback,
		CntHeldSnapshot:       t.CntHeldSnapshot,
		FreerunEdgePeriod:     t.FreerunEdgePeriod,
		CntSnapshotProbeValue: t.CntSnapshotProbe,
	}
}

// ---- WatchdogProjection (br28_model.WatchdogProjection) -----------------------

type watchdogStateJSON struct {
	ProgressNum  *int64  `json:"progress_num"`
	ProgressDen  int64   `json:"progress_den"`
	Con          *uint32 `json:"con"`
	KeyPhase     int64   `json:"key_phase"`
	Elapsed      int64   `json:"elapsed"`
	Remainder    int64   `json:"remainder"`
	Commands     int64   `json:"commands"`
	Feeds        int64   `json:"feeds"`
	Expired      bool    `json:"expired"`
	MailboxFeeds int64   `json:"mailbox_feeds"`
}

func watchdogFromJSON(s *watchdogStateJSON) (*WatchdogProjection, error) {
	var progress []int64
	if s.ProgressNum != nil {
		progress = []int64{*s.ProgressNum, s.ProgressDen}
	}
	w, err := NewWatchdogProjection(progress)
	if err != nil {
		return nil, err
	}
	w.Con = s.Con
	w.KeyPhase = s.KeyPhase
	w.Elapsed = s.Elapsed
	w.Remainder = s.Remainder
	w.Commands = s.Commands
	w.Feeds = s.Feeds
	w.Expired = s.Expired
	w.MailboxFeeds = s.MailboxFeeds
	return w, nil
}

func watchdogToJSON(w *WatchdogProjection) *watchdogStateJSON {
	return &watchdogStateJSON{
		ProgressNum:  w.ProgressNum,
		ProgressDen:  w.ProgressDen,
		Con:          w.Con,
		KeyPhase:     w.KeyPhase,
		Elapsed:      w.Elapsed,
		Remainder:    w.Remainder,
		Commands:     w.Commands,
		Feeds:        w.Feeds,
		Expired:      w.Expired,
		MailboxFeeds: w.MailboxFeeds,
	}
}

// ---- IcuInitializationProjection ----------------------------------------------

type icuStateJSON struct {
	Control0     *uint32   `json:"control0"`
	Control1     *uint32   `json:"control1"`
	CommandSteps *int64    `json:"command_steps"`
	Pending      [2]*int64 `json:"pending"`
	Completed    int64     `json:"completed"`
	BusyReads    int64     `json:"busy_reads"`
}

func icuFromJSON(s *icuStateJSON) (*IcuInitializationProjection, error) {
	icu, err := NewIcuInitializationProjection(s.Control0, s.Control1, s.CommandSteps, false)
	if err != nil {
		return nil, err
	}
	for i := 0; i < 2; i++ {
		if s.Pending[i] == nil {
			icu.HasPending[i] = false
			icu.Pending[i] = 0
		} else {
			icu.HasPending[i] = true
			icu.Pending[i] = *s.Pending[i]
		}
	}
	icu.Completed = s.Completed
	icu.BusyReads = s.BusyReads
	return icu, nil
}

func icuToJSON(icu *IcuInitializationProjection) *icuStateJSON {
	s := &icuStateJSON{
		Control0:     icu.Control[0],
		Control1:     icu.Control[1],
		CommandSteps: icu.CommandSteps,
		Completed:    icu.Completed,
		BusyReads:    icu.BusyReads,
	}
	for i := 0; i < 2; i++ {
		if icu.HasPending[i] {
			v := icu.Pending[i]
			s.Pending[i] = &v
		}
	}
	return s
}

// ---- Timer2Delay --------------------------------------------------------------

type timer2StateJSON struct {
	Con          *uint32 `json:"con"`
	Cnt          *uint32 `json:"cnt"`
	Prd          *uint32 `json:"prd"`
	Pending      bool    `json:"pending"`
	LrcRemainder int64   `json:"lrc_remainder"`
	Remainder    int64   `json:"remainder"`
	Expirations  int64   `json:"expirations"`
	Writes       int64   `json:"writes"`
	ConReads     int64   `json:"con_reads"`
	AdvanceCalls int64   `json:"advance_calls"`
}

func timer2FromJSON(s *timer2StateJSON) *Timer2Delay {
	t := NewTimer2Delay()
	t.Con = s.Con
	t.Cnt = s.Cnt
	t.Prd = s.Prd
	t.Pending = s.Pending
	t.LrcRemainder = s.LrcRemainder
	t.Remainder = s.Remainder
	t.Expirations = s.Expirations
	t.Writes = s.Writes
	t.ConReads = s.ConReads
	t.AdvanceCalls = s.AdvanceCalls
	return t
}

func timer2ToJSON(t *Timer2Delay) *timer2StateJSON {
	return &timer2StateJSON{
		Con:          t.Con,
		Cnt:          t.Cnt,
		Prd:          t.Prd,
		Pending:      t.Pending,
		LrcRemainder: t.LrcRemainder,
		Remainder:    t.Remainder,
		Expirations:  t.Expirations,
		Writes:       t.Writes,
		ConReads:     t.ConReads,
		AdvanceCalls: t.AdvanceCalls,
	}
}

// ---- SoftwareIrqCommands ------------------------------------------------------

type partialWordJSON struct {
	Value uint32 `json:"value"`
	Known uint32 `json:"known"`
}

type softwareIrqStateJSON struct {
	Pending          [2]partialWordJSON `json:"pending"`
	SourceEnable     [2]partialWordJSON `json:"source_enable"`
	Commands         int64              `json:"commands"`
	Acknowledgements int64              `json:"acknowledgements"`
	SourceCommands   int64              `json:"source_commands"`
}

func softwareIrqFromJSON(s *softwareIrqStateJSON) *SoftwareIrqCommands {
	sw, _ := NewSoftwareIrqCommands(nil, nil)
	for i := 0; i < 2; i++ {
		sw.Pending[i] = NewPartialWord(s.Pending[i].Value, s.Pending[i].Known)
		sw.SourceEnable[i] = NewPartialWord(s.SourceEnable[i].Value, s.SourceEnable[i].Known)
	}
	sw.Commands = s.Commands
	sw.Acknowledgements = s.Acknowledgements
	sw.SourceCommands = s.SourceCommands
	return sw
}

func softwareIrqToJSON(s *SoftwareIrqCommands) *softwareIrqStateJSON {
	out := &softwareIrqStateJSON{
		Commands:         s.Commands,
		Acknowledgements: s.Acknowledgements,
		SourceCommands:   s.SourceCommands,
	}
	for i := 0; i < 2; i++ {
		out.Pending[i] = partialWordJSON{Value: s.Pending[i].Value, Known: s.Pending[i].Known}
		out.SourceEnable[i] = partialWordJSON{Value: s.SourceEnable[i].Value, Known: s.SourceEnable[i].Known}
	}
	return out
}

// ---- JLSrcSyncDelivery --------------------------------------------------------

type srcSyncStateJSON struct {
	CompletionSteps  int64  `json:"completion_steps"`
	Started          bool   `json:"started"`
	StartStep        *int64 `json:"start_step"`
	Pending          bool   `json:"pending"`
	DeliveryInFlight bool   `json:"delivery_in_flight"`
	Starts           int64  `json:"starts"`
	Completions      int64  `json:"completions"`
	Acks             int64  `json:"acks"`
}

func srcSyncFromJSON(s *srcSyncStateJSON) (*JLSrcSyncDelivery, error) {
	d, err := NewJLSrcSyncDelivery(s.CompletionSteps)
	if err != nil {
		return nil, err
	}
	d.Started = s.Started
	d.StartStep = s.StartStep
	d.Pending = s.Pending
	d.DeliveryInFlight = s.DeliveryInFlight
	d.Starts = s.Starts
	d.Completions = s.Completions
	d.Acks = s.Acks
	return d, nil
}

func srcSyncToJSON(d *JLSrcSyncDelivery) *srcSyncStateJSON {
	return &srcSyncStateJSON{
		CompletionSteps:  d.CompletionSteps,
		Started:          d.Started,
		StartStep:        d.StartStep,
		Pending:          d.Pending,
		DeliveryInFlight: d.DeliveryInFlight,
		Starts:           d.Starts,
		Completions:      d.Completions,
		Acks:             d.Acks,
	}
}

// ---- AudioAdcDmaDelivery ------------------------------------------------------

type audioAdcDmaStateJSON struct {
	Delivery         bool `json:"delivery"`
	Pending          bool `json:"pending"`
	DeliveryInFlight bool `json:"delivery_in_flight"`
}

func audioAdcDmaFromJSON(s *audioAdcDmaStateJSON) *AudioAdcDmaDelivery {
	a := NewAudioAdcDmaDelivery(s.Delivery)
	a.Pending = s.Pending
	a.DeliveryInFlight = s.DeliveryInFlight
	return a
}

func audioAdcDmaToJSON(a *AudioAdcDmaDelivery) *audioAdcDmaStateJSON {
	return &audioAdcDmaStateJSON{
		Delivery:         a.Delivery,
		Pending:          a.Pending,
		DeliveryInFlight: a.DeliveryInFlight,
	}
}

// ---- CrcEngine (br28_model.HardwareCrcEngine) --------------------------------

type crcStateJSON struct {
	Crc *uint32 `json:"crc"`
}

func crcFromJSON(s *crcStateJSON) *CrcEngine {
	if s == nil {
		return nil
	}
	return &CrcEngine{Crc: s.Crc}
}

func crcToJSON(e *CrcEngine) *crcStateJSON {
	if e == nil {
		return nil
	}
	return &crcStateJSON{Crc: e.Crc}
}

// ---- PeriphState (whole advance-chain state) ----------------------------------

type periphStateJSON struct {
	SourceTicksPerStep           int64               `json:"source_ticks_per_step"`
	TimerLsbTicksPerStep         *int64              `json:"timer_lsb_ticks_per_step"`
	MulticoreEnabled             bool                `json:"multicore_enabled"`
	CoreIcfg                     [2]*uint32          `json:"core_icfg"`
	Cpu1Control                  *uint32             `json:"cpu1_control"`
	Cpu1HeldProjection           bool                `json:"cpu1_held_projection"`
	CoreTickCountWhileHeld       *bool               `json:"core_tick_count_while_held"`
	CoreTickDeliveryRequests     bool                `json:"core_tick_delivery_requests"`
	Timer0IRQDeliveryRequests    bool                `json:"timer0_irq_delivery_requests"`
	SoftwareIRQDeliveryRequests  bool                `json:"software_irq_delivery_requests"`
	SrcSyncDeliveryRequests      bool                `json:"src_sync_delivery_requests"`
	AudioAdcDmaDeliveryRequests  bool                `json:"audio_adc_dma_delivery_requests"`
	CompletedModelSteps          int64               `json:"completed_model_steps"`
	Timer                        *timer0StateJSON    `json:"timer"`
	IRQ                          *irqStateJSON       `json:"irq"`
	IRQ1                         *irqStateJSON       `json:"irq1"`
	CoreTicks                    [2]*coreTickStateJSON `json:"core_ticks"`
	Watchdog                     *watchdogStateJSON  `json:"watchdog"`
	ICU                          *icuStateJSON       `json:"icu"`
	Timer2                       *timer2StateJSON    `json:"timer2"`
	SoftwareIRQ                  *softwareIrqStateJSON `json:"software_irq"`
	SrcSync                      *srcSyncStateJSON   `json:"src_sync"`
	AudioAdcDma                  *audioAdcDmaStateJSON `json:"audio_adc_dma"`
	Crc                          *crcStateJSON        `json:"crc"`
}

// StateJSON serializes the advance-relevant PeriphState (Timer0 / IRQ / core
// ticks + scalar flags) for the differential harness. Models outside the
// transferred scope are omitted (they stay nil on load).
func (p *PeriphState) StateJSON() ([]byte, error) {
	if p == nil {
		return nil, fmt.Errorf("nil PeriphState")
	}
	s := &periphStateJSON{
		SourceTicksPerStep:          p.SourceTicksPerStep,
		TimerLsbTicksPerStep:        p.TimerLsbTicksPerStep,
		MulticoreEnabled:            p.MulticoreEnabled,
		CoreIcfg:                    p.CoreIcfg,
		Cpu1Control:                 p.Cpu1Control,
		Cpu1HeldProjection:          p.Cpu1HeldProjection,
		CoreTickCountWhileHeld:      p.CoreTickCountWhileHeld,
		CoreTickDeliveryRequests:    p.CoreTickDeliveryRequests,
		Timer0IRQDeliveryRequests:   p.Timer0IRQDeliveryRequests,
		SoftwareIRQDeliveryRequests: p.SoftwareIRQDeliveryRequests,
		SrcSyncDeliveryRequests:     p.SrcSyncDeliveryRequests,
		AudioAdcDmaDeliveryRequests: p.AudioAdcDmaDeliveryRequests,
		CompletedModelSteps:         p.CompletedModelSteps,
	}
	if p.Timer != nil {
		s.Timer = timer0ToJSON(p.Timer)
	}
	if p.IRQ != nil {
		s.IRQ = irqToJSON(p.IRQ)
	}
	if p.IRQ1 != nil {
		s.IRQ1 = irqToJSON(p.IRQ1)
	}
	for i := 0; i < 2; i++ {
		if p.CoreTicks[i] != nil {
			s.CoreTicks[i] = coreTickToJSON(p.CoreTicks[i])
		}
	}
	if p.Watchdog != nil {
		s.Watchdog = watchdogToJSON(p.Watchdog)
	}
	if p.ICU != nil {
		s.ICU = icuToJSON(p.ICU)
	}
	if p.Timer2 != nil {
		s.Timer2 = timer2ToJSON(p.Timer2)
	}
	if p.SoftwareIRQ != nil {
		s.SoftwareIRQ = softwareIrqToJSON(p.SoftwareIRQ)
	}
	if p.SrcSync != nil {
		s.SrcSync = srcSyncToJSON(p.SrcSync)
	}
	if p.AudioAdcDma != nil {
		s.AudioAdcDma = audioAdcDmaToJSON(p.AudioAdcDma)
	}
	if p.Crc != nil {
		s.Crc = crcToJSON(p.Crc)
	}
	return json.Marshal(s)
}

// LoadStateJSON rebuilds the transferred models from a StateJSON blob. A null
// model field (Python None) clears the corresponding PeriphState slot.
func (p *PeriphState) LoadStateJSON(data []byte) error {
	if p == nil {
		return fmt.Errorf("nil PeriphState")
	}
	var s periphStateJSON
	if err := json.Unmarshal(data, &s); err != nil {
		return err
	}
	p.SourceTicksPerStep = s.SourceTicksPerStep
	p.TimerLsbTicksPerStep = s.TimerLsbTicksPerStep
	p.MulticoreEnabled = s.MulticoreEnabled
	p.CoreIcfg = s.CoreIcfg
	p.Cpu1Control = s.Cpu1Control
	p.Cpu1HeldProjection = s.Cpu1HeldProjection
	p.CoreTickCountWhileHeld = s.CoreTickCountWhileHeld
	p.CoreTickDeliveryRequests = s.CoreTickDeliveryRequests
	p.Timer0IRQDeliveryRequests = s.Timer0IRQDeliveryRequests
	p.SoftwareIRQDeliveryRequests = s.SoftwareIRQDeliveryRequests
	p.SrcSyncDeliveryRequests = s.SrcSyncDeliveryRequests
	p.AudioAdcDmaDeliveryRequests = s.AudioAdcDmaDeliveryRequests
	p.CompletedModelSteps = s.CompletedModelSteps

	if s.Timer == nil {
		p.Timer = nil
	} else {
		t, err := timer0FromJSON(s.Timer)
		if err != nil {
			return err
		}
		p.Timer = t
	}
	if s.IRQ == nil {
		p.IRQ = nil
	} else {
		p.IRQ = irqFromJSON(s.IRQ)
	}
	if s.IRQ1 == nil {
		p.IRQ1 = nil
	} else {
		p.IRQ1 = irqFromJSON(s.IRQ1)
	}
	for i := 0; i < 2; i++ {
		if s.CoreTicks[i] == nil {
			p.CoreTicks[i] = nil
			continue
		}
		ct, err := coreTickFromJSON(s.CoreTicks[i])
		if err != nil {
			return err
		}
		p.CoreTicks[i] = ct
	}
	if s.Watchdog == nil {
		p.Watchdog = nil
	} else {
		w, err := watchdogFromJSON(s.Watchdog)
		if err != nil {
			return err
		}
		p.Watchdog = w
	}
	if s.ICU == nil {
		p.ICU = nil
	} else {
		icu, err := icuFromJSON(s.ICU)
		if err != nil {
			return err
		}
		p.ICU = icu
	}
	if s.Timer2 == nil {
		p.Timer2 = nil
	} else {
		p.Timer2 = timer2FromJSON(s.Timer2)
	}
	if s.SoftwareIRQ == nil {
		p.SoftwareIRQ = nil
	} else {
		p.SoftwareIRQ = softwareIrqFromJSON(s.SoftwareIRQ)
	}
	if s.SrcSync == nil {
		p.SrcSync = nil
	} else {
		d, err := srcSyncFromJSON(s.SrcSync)
		if err != nil {
			return err
		}
		p.SrcSync = d
	}
	if s.AudioAdcDma == nil {
		p.AudioAdcDma = nil
	} else {
		p.AudioAdcDma = audioAdcDmaFromJSON(s.AudioAdcDma)
	}
	if s.Crc == nil {
		p.Crc = nil
	} else {
		p.Crc = crcFromJSON(s.Crc)
	}
	return nil
}

// AdvanceResultJSON is the JSON outcome of one Advance call: exactly one of
// Irq / Err is non-nil, and Empty describes whether anything counted.
type AdvanceResultJSON struct {
	Empty bool          `json:"empty"`
	Irq   *IrqReqJSON   `json:"irq"`
	Err   json.RawMessage `json:"err"`
}

// IrqReqJSON is the delivery-request payload (mirrors Python
// IrqDeliveryRequest.details == {"core":c,"irq":i}).
type IrqReqJSON struct {
	Core int    `json:"core"`
	Irq  uint32 `json:"irq"`
}

// AdvanceJSON runs one advance step and returns the JSON outcome.
func (p *PeriphState) AdvanceJSON(icfg uint32) ([]byte, error) {
	res := p.Advance(icfg)
	out := &AdvanceResultJSON{Empty: res.Empty}
	if res.Irq != nil {
		out.Irq = &IrqReqJSON{Core: res.Irq.Core, Irq: res.Irq.Irq}
	}
	if res.Err != nil {
		out.Err = json.RawMessage(res.Err.JSON())
	}
	return json.Marshal(out)
}
