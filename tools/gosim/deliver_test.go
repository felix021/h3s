package gosim

import (
	"errors"
	"testing"
)

func u32(v uint32) *uint32 { return &v }

// A software-IRQ entry on core 0 rewrites pc/reti/irq_psr/usp/ssp and clears
// the acknowledged pending bit, matching br28_machine.deliver_software_irq.
func TestDeliverSoftwareIrqEntry(t *testing.T) {
	m := NewDeliverMachine()
	m.SoftwareIrqEntry = true
	m.SoftwareIrqEntryBanks = true
	m.IdleWakeConsumesIdle = true
	m.VectorSlots = map[uint32]*uint32{0x19fe00 + 127*4: u32(0x12ad2a)}
	m.SoftwareIrqPending[0] = NewPartialWord(8, 15)
	m.SoftwareIrqPending[1] = NewPartialWord(0, 15)
	m.Cores[0] = &Core{ID: 0, PC: 0x100, SP: 0x1000, SPKnown: true, Flags: 3, LastExplicitSSP: u32(0x2000)}
	m.IdleWait[0] = true

	if err := m.DeliverSoftwareIrq(0, 127); err != nil {
		t.Fatalf("deliver software irq: %v", err)
	}
	c := m.Cores[0]
	if c.PC != 0x12ad2a {
		t.Fatalf("pc = %#x, want handler", c.PC)
	}
	if c.RETI == nil || *c.RETI != 0x102 {
		t.Fatalf("reti = %v, want 0x102 (idle pc+2)", c.RETI)
	}
	if c.IRQPSR == nil || *c.IRQPSR != 3 {
		t.Fatalf("irq_psr = %v, want 3", c.IRQPSR)
	}
	if c.USP == nil || *c.USP != 0x1000 {
		t.Fatalf("usp = %v, want task stack 0x1000", c.USP)
	}
	if c.SP != 0x2000 || !c.SPKnown {
		t.Fatalf("sp = %#x known=%v, want 0x2000/true", c.SP, c.SPKnown)
	}
	if c.ActiveContext != "software_irq_handler" {
		t.Fatalf("active_context = %q", c.ActiveContext)
	}
	if c.Steps != 1 {
		t.Fatalf("steps = %d, want 1", c.Steps)
	}
	if m.SoftwareIrqPending[0].Value&8 != 0 {
		t.Fatalf("pending bit3 not cleared: value=%#x", m.SoftwareIrqPending[0].Value)
	}
	if m.SoftwareIrqAck != 1 {
		t.Fatalf("acknowledgements = %d, want 1", m.SoftwareIrqAck)
	}
	if len(m.SoftwareIrqDeliveries) != 1 || m.EventCount != 2 {
		t.Fatalf("deliveries=%d events=%d, want 1/2", len(m.SoftwareIrqDeliveries), m.EventCount)
	}
}

// A missing vector-slot word is the vector_slot_uninitialized strict stop.
func TestDeliverSoftwareIrqVectorUninit(t *testing.T) {
	m := NewDeliverMachine()
	m.SoftwareIrqEntry = true
	m.SoftwareIrqPending[0] = NewPartialWord(8, 15)
	m.SoftwareIrqPending[1] = NewPartialWord(0, 15)
	m.Cores[0] = &Core{ID: 0, PC: 0x100, SP: 0x1000, SPKnown: true}

	err := m.DeliverSoftwareIrq(0, 127)
	var pe *PeriphError
	if !errors.As(err, &pe) || pe.Kind != "vector_slot_uninitialized" {
		t.Fatalf("err = %v, want vector_slot_uninitialized Blocked", err)
	}
}

// IRQ68 nests into the Timer0 handler when its real priority is strictly higher,
// and the RTI restores the outer timer0 context.
func TestDeliverAudioDmaNestedRoundTrip(t *testing.T) {
	m := NewDeliverMachine()
	m.AudioIrqEntry = true
	m.AudioIrqRti = true
	m.RepeatIrqDelivery = true
	m.VectorSlots = map[uint32]*uint32{0x19fe00 + 68*4: u32(0x602f406)}
	m.IRQConfig[0] = u32(3 << 16) // irq4  prio 1
	m.IRQConfig[8] = u32(5 << 16) // irq68 prio 2
	m.Cores[0] = &Core{ID: 0, PC: 0x100, SP: 0x2000, SPKnown: true, Flags: 2,
		RETI: u32(0x200), ActiveContext: "timer0_irq_handler"}

	if err := m.DeliverAudioDmaIrq(0); err != nil {
		t.Fatalf("nested audio entry: %v", err)
	}
	c := m.Cores[0]
	if c.ActiveContext != "audio_dma_irq_handler" {
		t.Fatalf("active_context = %q after nested entry", c.ActiveContext)
	}
	if c.AudioIrqOuter == nil || c.AudioIrqOuter.ActiveContext != "timer0_irq_handler" {
		t.Fatalf("outer context not saved: %+v", c.AudioIrqOuter)
	}
	if c.RETI == nil || *c.RETI != 0x100 {
		t.Fatalf("reti = %v, want the audio return 0x100", c.RETI)
	}

	// Return at the audio RTI tail: restore the outer timer0 handler context.
	c.PC = AudioIrqRtiTail
	if err := m.ReturnAudioDmaIrq(0); err != nil {
		t.Fatalf("nested audio return: %v", err)
	}
	if c.ActiveContext != "timer0_irq_handler" {
		t.Fatalf("active_context = %q after nested return", c.ActiveContext)
	}
	if c.RETI == nil || *c.RETI != 0x200 {
		t.Fatalf("outer reti = %v, want 0x200", c.RETI)
	}
	if c.AudioIrqOuter != nil {
		t.Fatalf("outer context not cleared: %+v", c.AudioIrqOuter)
	}
}

// A require guard violation surfaces as a DeliverValueError, not a Blocked stop.
func TestDeliverCoreTickRejectsBootstrap(t *testing.T) {
	m := NewDeliverMachine()
	m.CoreTickEntry = true
	m.Cores[0] = &Core{ID: 0, PC: 0x100, BootstrapContext: true}

	err := m.DeliverCoreTick(0)
	var ve *DeliverValueError
	if !errors.As(err, &ve) {
		t.Fatalf("err = %v, want DeliverValueError", err)
	}
	if ve.Msg != "core tick entry requires an instruction boundary on the overflowed core" {
		t.Fatalf("msg = %q", ve.Msg)
	}
}
