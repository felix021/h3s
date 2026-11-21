package gosim

// M5 peripheral port: SRC-SYNC delivery (JLSrcSyncDelivery) and the ADC-DMA
// delivery-detection state — the final two advance-chain leaves, byte-for-byte
// aligned with the Python br28_model.JLSrcSyncDelivery / the delivery half of
// br28_model.JLAudioAdcDmaWriteRecords (docs/gosim-wholeloop-design.md §4).
//
// Key simplification (design §4): the Go advance only detects "pending and
// eligible" and throws an IrqReq back to Python; the delivery projections
// themselves (deliver_src_sync_irq / deliver_audio_dma_irq in br28_machine.py,
// and the DMA-completion produce_block that sets the ADC-DMA pending bit) stay
// in Python through M5. The CPU-state rewrites and explicit assumptions inside
// those projections never enter Go.
//
// SRC-SYNC completion timing is the explicit derived-not-measured assumption:
// completion_steps model steps after the real CON0|=0x8 startup write. The
// JLSrcSyncCursorProgress half (route SRCSYNC6) is NOT ported here — the Go
// delivery model always has a nil cursor, matching Python JLSrcSyncDelivery()
// with the default cursor=None. When jl_src_sync_cursor_progress is enabled the
// cursor commit stays a Python-side concern.

import "fmt"

// SRC-SYNC register / IRQ constants (br28_model.JLSrcSyncDelivery).
const (
	JLSrcSyncCON0     = 0xfe2600
	JLSrcSyncIRQ89    = 89
	JLSrcSyncStartBit = 0x8
	JLSrcSyncAckBit   = 0x40

	AudioAdcDmaIRQ68 = 68
)

// JLSrcSyncDelivery mirrors br28_model.JLSrcSyncDelivery (route SRCSYNC5): the
// completion event that sets IRQ89 pending, plus the per-pending-slice
// delivery_in_flight bookkeeping the delivery scan reads.
type JLSrcSyncDelivery struct {
	CompletionSteps int64

	Started          bool
	StartStep        *int64 // nil = None (not yet started)
	Pending          bool
	DeliveryInFlight bool
	Starts           int64
	Completions      int64
	Acks             int64
}

// NewJLSrcSyncDelivery mirrors JLSrcSyncDelivery(completion_steps, cursor=None).
// completionSteps must be a positive model-step count (Python ValueError
// otherwise).
func NewJLSrcSyncDelivery(completionSteps int64) (*JLSrcSyncDelivery, error) {
	if completionSteps < 1 {
		return nil, fmt.Errorf("SRC_SYNC completion steps must be a positive model-step count")
	}
	return &JLSrcSyncDelivery{CompletionSteps: completionSteps}, nil
}

// ReentryAuthorized mirrors JLSrcSyncDelivery.reentry_authorized.
func (d *JLSrcSyncDelivery) ReentryAuthorized() bool { return d.Completions > 0 }

// NoteStart mirrors JLSrcSyncDelivery.note_start (the CON0|=0x8 startup RMW
// dispatch). step is the current completed_model_steps count.
func (d *JLSrcSyncDelivery) NoteStart(step int64) {
	d.Started = true
	s := step
	d.StartStep = &s
	d.Starts++
}

// Advance mirrors JLSrcSyncDelivery.advance: completion_steps model steps after
// the startup write flips started->pending (one pending slice at a time).
func (d *JLSrcSyncDelivery) Advance(step int64) {
	if d.Started && !d.Pending {
		if step-*d.StartStep >= d.CompletionSteps {
			d.Started = false
			d.Pending = true
			d.Completions++
		}
	}
}

// NoteConWrite mirrors JLSrcSyncDelivery.note_con_write: a CON0 write with the
// ACK bit (0x40) set is the IRQ89 handler's real W1C that clears pending and
// releases delivery_in_flight.
func (d *JLSrcSyncDelivery) NoteConWrite(value uint32) {
	if value&JLSrcSyncAckBit != 0 {
		d.Pending = false
		d.DeliveryInFlight = false
		d.Acks++
	}
}

// StateMap mirrors the advance-relevant JLSrcSyncDelivery attributes for the
// byte-for-byte differential test.
func (d *JLSrcSyncDelivery) StateMap() map[string]any {
	return map[string]any{
		"completion_steps":   d.CompletionSteps,
		"started":            d.Started,
		"start_step":         nilI64(d.StartStep),
		"pending":            d.Pending,
		"delivery_in_flight": d.DeliveryInFlight,
		"starts":             d.Starts,
		"completions":        d.Completions,
		"acks":               d.Acks,
	}
}

// AudioAdcDmaDelivery mirrors the delivery-relevant state of
// br28_model.JLAudioAdcDmaWriteRecords in delivery mode (route D2AI). The
// produce_block DMA-completion projection (sample-byte synthesis + pending set)
// and the |=0x40 W1C clear stay in Python; the advance chain only reads
// Delivery/Pending and writes DeliveryInFlight in the delivery scan.
type AudioAdcDmaDelivery struct {
	Delivery         bool
	Pending          bool
	DeliveryInFlight bool
}

// NewAudioAdcDmaDelivery mirrors JLAudioAdcDmaWriteRecords(delivery=...).
func NewAudioAdcDmaDelivery(delivery bool) *AudioAdcDmaDelivery {
	return &AudioAdcDmaDelivery{Delivery: delivery}
}

// StateMap mirrors the delivery-relevant JLAudioAdcDmaWriteRecords attributes.
func (a *AudioAdcDmaDelivery) StateMap() map[string]any {
	return map[string]any{
		"delivery":           a.Delivery,
		"pending":            a.Pending,
		"delivery_in_flight": a.DeliveryInFlight,
	}
}
