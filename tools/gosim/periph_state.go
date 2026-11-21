package gosim

// Whole-loop advance state transfer: the JSON shapes the differential harness
// uses to move the live Python BR28Model peripheral state into a Go PeriphState
// (and back out) so the two advance chains step from byte-identical state and
// are compared after every step.
//
// Scope (docs/gosim-wholeloop-design.md §4 M3-M4): Timer0 + IRQController +
// CoreTickStopped plus the advance-chain scalar flags. The remaining M4/M5
// leaves (watchdog / ICU / Timer2 / software IRQ / SRC-SYNC / ADC-DMA) already
// have per-model StateMap exports (periph_m4.go / periph_m5.go) but are NOT
// carried here yet; a PeriphState with those models bound must still be
// transferred model-by-model through their own exports. Nothing is fabricated:
// a field that Python holds as None round-trips as JSON null -> nil.

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

	// Models outside the transferred scope are never carried, so binding them
	// here would silently drop their state. They are left untouched; the
	// harness only transfers Timer0 / IRQ / core ticks through this entry point.
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
