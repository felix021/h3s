package gosim

import "testing"

// M5 Go-side tests. The byte-for-byte differential test test_m5_consistency.py
// drives the Go JLSrcSyncDelivery leaf model (via ctypes) against the real
// Python br28_model.JLSrcSyncDelivery; these Go tests pin the strict-stop JSON
// and IrqReq outcomes of the SRC-SYNC / ADC-DMA delivery scans, whose exact
// strings are the same literals the Python test re-derives from BR28Model.advance.

func m5irq(cfgWords map[int]uint32, ipmask *uint32) *IRQController {
	ic := NewIRQController()
	for i, v := range cfgWords {
		ic.Config[i] = u32p(v)
	}
	ic.IPMask = ipmask
	return ic
}

// IRQ68 eligible: config[8] nibble 4 = 0b0101 (enabled, priority 2), ipmask 2.
// IRQ89 eligible: config[11] nibble 1 = 0b0101 (enabled, priority 2).
func m5irq68and89() *IRQController {
	return m5irq(map[int]uint32{
		8:  0x5 << (4 * 4),
		11: 0x5 << (1 * 4),
	}, u32p(2))
}

func TestJLSrcSyncDeliveryCompletion(t *testing.T) {
	d, err := NewJLSrcSyncDelivery(40000)
	if err != nil {
		t.Fatalf("new: %v", err)
	}
	if _, err := NewJLSrcSyncDelivery(0); err == nil {
		t.Fatal("completion steps 0 should be rejected")
	}
	d.NoteStart(0)
	if !d.Started || d.StartStep == nil || *d.StartStep != 0 || d.Starts != 1 {
		t.Fatalf("note_start state wrong: %+v", d)
	}
	for i := int64(1); i < 40000; i++ {
		d.Advance(i)
		if d.Pending {
			t.Fatalf("pending set too early at step %d", i)
		}
	}
	d.Advance(40000)
	if !d.Pending || d.Started || d.Completions != 1 {
		t.Fatalf("should complete at step 40000: %+v", d)
	}
	// one pending slice at a time: advance again is a no-op while pending
	d.Advance(40001)
	if d.Completions != 1 {
		t.Fatalf("pending slice should not re-complete: completions=%d", d.Completions)
	}
	// W1C clear (bit 0x40) releases pending + delivery_in_flight
	d.DeliveryInFlight = true
	d.NoteConWrite(0x40)
	if d.Pending || d.DeliveryInFlight || d.Acks != 1 {
		t.Fatalf("note_con_write(0x40) should clear: %+v", d)
	}
	// a write without the ACK bit does nothing
	d.NoteConWrite(0x8)
	if d.Acks != 1 {
		t.Fatalf("note_con_write(0x8) should not ack: acks=%d", d.Acks)
	}
	// reentry authorization requires a full start->complete->ack cycle
	d2, _ := NewJLSrcSyncDelivery(40000)
	if d2.ReentryAuthorized() {
		t.Fatal("reentry should be unauthorized before any completion")
	}
	if !d.ReentryAuthorized() {
		t.Fatal("reentry should be authorized after a completion")
	}
}

func TestAdvanceSrcSyncDeliveryScan(t *testing.T) {
	// pending + eligible + requests on -> IrqReq(89)
	p := &PeriphState{IRQ: m5irq68and89(), SrcSync: &JLSrcSyncDelivery{Pending: true}, SrcSyncDeliveryRequests: true}
	res := p.Advance(0x300)
	if res.Err != nil || res.Irq == nil || res.Irq.Core != 0 || res.Irq.Irq != 89 {
		t.Fatalf("want IrqReq(core=0,irq=89), got irq=%+v err=%v", res.Irq, res.Err)
	}
	if !p.SrcSync.DeliveryInFlight {
		t.Fatal("delivery_in_flight should be set before handing back")
	}
	// a second advance while in-flight must not re-throw
	res = p.Advance(0x300)
	if res.Err != nil || res.Irq != nil {
		t.Fatalf("in-flight pending slice must not re-throw: irq=%+v err=%v", res.Irq, res.Err)
	}

	// pending + eligible + requests off -> strict stop
	p2 := &PeriphState{IRQ: m5irq68and89(), SrcSync: &JLSrcSyncDelivery{Pending: true}}
	res = p2.Advance(0x300)
	if res.Irq != nil || res.Err == nil {
		t.Fatalf("want strict stop, got irq=%+v err=%v", res.Irq, res.Err)
	}
	want := `{"core":0,"irq":89,"kind":"interrupt_delivery_not_implemented","note":"SRC_SYNC completion pending and eligible; CPU entry not bypassed — enable the SRC_SYNC delivery requests"}`
	if res.Err.JSON() != want {
		t.Fatalf("src_sync strict stop JSON mismatch:\n got %s\nwant %s", res.Err.JSON(), want)
	}

	// eligible raises Blocked (unknown ipmask) -> wrapped src_sync_irq_delivery_blocked
	p3 := &PeriphState{IRQ: m5irq68and89(), SrcSync: &JLSrcSyncDelivery{Pending: true}, SrcSyncDeliveryRequests: true}
	p3.IRQ.IPMask = nil
	res = p3.Advance(0x300)
	if res.Err == nil || res.Err.JSON() != `{"core":0,"irq":89,"kind":"src_sync_irq_delivery_blocked","reason":"uninitialized_ipmask"}` {
		t.Fatalf("src_sync blocked JSON mismatch: %v", res.Err)
	}

	// gates off (icfg 0x100) -> not eligible -> no delivery, no stop
	p4 := &PeriphState{IRQ: m5irq68and89(), SrcSync: &JLSrcSyncDelivery{Pending: true}, SrcSyncDeliveryRequests: true}
	res = p4.Advance(0x100)
	if res.Irq != nil || res.Err != nil {
		t.Fatalf("gates-off pending should not deliver: irq=%+v err=%v", res.Irq, res.Err)
	}
}

func TestAdvanceAdcDmaDeliveryScan(t *testing.T) {
	// pending + delivery + eligible + requests on -> IrqReq(68)
	p := &PeriphState{
		IRQ:                         m5irq68and89(),
		AudioAdcDma:                 &AudioAdcDmaDelivery{Delivery: true, Pending: true},
		AudioAdcDmaDeliveryRequests: true,
	}
	res := p.Advance(0x300)
	if res.Err != nil || res.Irq == nil || res.Irq.Core != 0 || res.Irq.Irq != 68 {
		t.Fatalf("want IrqReq(core=0,irq=68), got irq=%+v err=%v", res.Irq, res.Err)
	}
	if !p.AudioAdcDma.DeliveryInFlight {
		t.Fatal("delivery_in_flight should be set before handing back")
	}
	res = p.Advance(0x300)
	if res.Err != nil || res.Irq != nil {
		t.Fatalf("in-flight pending slice must not re-throw: irq=%+v err=%v", res.Irq, res.Err)
	}

	// requests off -> strict stop
	p2 := &PeriphState{IRQ: m5irq68and89(), AudioAdcDma: &AudioAdcDmaDelivery{Delivery: true, Pending: true}}
	res = p2.Advance(0x300)
	if res.Irq != nil || res.Err == nil {
		t.Fatalf("want strict stop, got irq=%+v err=%v", res.Irq, res.Err)
	}
	want := `{"core":0,"irq":68,"kind":"interrupt_delivery_not_implemented","note":"audio ADC DMA pending and eligible; CPU entry not bypassed — enable the audio ADC DMA delivery requests"}`
	if res.Err.JSON() != want {
		t.Fatalf("adc_dma strict stop JSON mismatch:\n got %s\nwant %s", res.Err.JSON(), want)
	}

	// eligible raises Blocked -> wrapped audio_dma_irq_delivery_blocked
	p3 := &PeriphState{
		IRQ:                         m5irq68and89(),
		AudioAdcDma:                 &AudioAdcDmaDelivery{Delivery: true, Pending: true},
		AudioAdcDmaDeliveryRequests: true,
	}
	p3.IRQ.IPMask = nil
	res = p3.Advance(0x300)
	if res.Err == nil || res.Err.JSON() != `{"core":0,"irq":68,"kind":"audio_dma_irq_delivery_blocked","reason":"uninitialized_ipmask"}` {
		t.Fatalf("adc_dma blocked JSON mismatch: %v", res.Err)
	}

	// delivery off (write-only mode) -> no scan
	p4 := &PeriphState{IRQ: m5irq68and89(), AudioAdcDma: &AudioAdcDmaDelivery{Delivery: false, Pending: true}}
	res = p4.Advance(0x300)
	if res.Irq != nil || res.Err != nil {
		t.Fatalf("write-only ADC-DMA pending should not deliver: irq=%+v err=%v", res.Irq, res.Err)
	}
}

func TestAdvanceSrcSyncCompletionEndToEnd(t *testing.T) {
	// start at step 0, run 40000 advance calls: pending flips on the completing
	// step and the same advance throws the IrqReq.
	src, _ := NewJLSrcSyncDelivery(40000)
	src.NoteStart(0)
	p := &PeriphState{IRQ: m5irq68and89(), SrcSync: src, SrcSyncDeliveryRequests: true}
	var irq *IrqReq
	for i := 0; i < 40000; i++ {
		res := p.Advance(0x300)
		if res.Err != nil {
			t.Fatalf("advance %d strict stop: %s", i+1, res.Err.JSON())
		}
		if res.Irq != nil {
			irq = res.Irq
			break
		}
	}
	if irq == nil || irq.Irq != 89 || irq.Core != 0 {
		t.Fatalf("want IrqReq(89) on the completing step, got %+v", irq)
	}
	if src.Completions != 1 || src.Pending != true {
		t.Fatalf("completion state wrong: %+v", src)
	}
}
