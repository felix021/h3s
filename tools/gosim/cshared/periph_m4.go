// cgo export wrappers for the M4 peripheral models (gosim/periph_m4.go),
// callable from Python via ctypes for the byte-for-byte differential test
// test_m4_consistency.py. See gosim_m4.py for the Python side.
package main

/*
#include <stdint.h>
#include <stdlib.h>
*/
import "C"

import (
	"encoding/json"
	"runtime/cgo"
	"unsafe"

	"h3s/gosim"
)

// ---- helpers ----------------------------------------------------------------

func coretick(h C.uintptr_t) *gosim.CoreTickStopped {
	return cgo.Handle(h).Value().(*gosim.CoreTickStopped)
}
func wdt(h C.uintptr_t) *gosim.WatchdogProjection {
	return cgo.Handle(h).Value().(*gosim.WatchdogProjection)
}
func icu(h C.uintptr_t) *gosim.IcuInitializationProjection {
	return cgo.Handle(h).Value().(*gosim.IcuInitializationProjection)
}
func timer2(h C.uintptr_t) *gosim.Timer2Delay {
	return cgo.Handle(h).Value().(*gosim.Timer2Delay)
}
func swirq(h C.uintptr_t) *gosim.SoftwareIrqCommands {
	return cgo.Handle(h).Value().(*gosim.SoftwareIrqCommands)
}

// writeM4Err copies a Blocked JSON into errBuf (NUL-terminated) and returns the
// periphErrBlocked code.
func writeM4Err(errBuf *C.uint8_t, errCap C.int, err *gosim.PeriphError) C.int {
	if errBuf != nil && errCap > 0 {
		dst := unsafe.Slice((*byte)(unsafe.Pointer(errBuf)), int(errCap))
		s := err.JSON()
		n := copy(dst, s)
		if n < len(dst) {
			dst[n] = 0
		}
	}
	return periphErrBlocked
}

// writeM4State marshals a model state map to JSON into outBuf.
func writeM4State(out *C.uint8_t, outCap C.int, m map[string]any) C.int {
	if out == nil || outCap <= 0 {
		return 0
	}
	b, _ := json.Marshal(m)
	dst := unsafe.Slice((*byte)(unsafe.Pointer(out)), int(outCap))
	n := copy(dst, b)
	if n < len(dst) {
		dst[n] = 0
	}
	return C.int(n)
}

// ---- CoreTickStopped --------------------------------------------------------

//export gosim_coretick_new
func gosim_coretick_new(initialConHas C.int, initialCon C.uint32_t, progressHas C.int, progress C.longlong,
	prdReadback C.int, cntHeld C.int, freerun C.int) C.uintptr_t {
	var ic *uint32
	if initialConHas != 0 {
		v := uint32(initialCon)
		ic = &v
	}
	var prog *int64
	if progressHas != 0 {
		v := int64(progress)
		prog = &v
	}
	t, err := gosim.NewCoreTickStopped(ic, prog, prdReadback != 0, cntHeld != 0, freerun != 0)
	if err != nil {
		return 0
	}
	return C.uintptr_t(cgo.NewHandle(t))
}

//export gosim_coretick_free
func gosim_coretick_free(h C.uintptr_t) { cgo.Handle(h).Delete() }

//export gosim_coretick_read
func gosim_coretick_read(h C.uintptr_t, offset C.int, out *C.uint32_t, errBuf *C.uint8_t, errCap C.int) C.int {
	v, err := coretick(h).Read(int(offset))
	if err != nil {
		return writeM4Err(errBuf, errCap, err)
	}
	*out = C.uint32_t(v)
	return 0
}

//export gosim_coretick_write
func gosim_coretick_write(h C.uintptr_t, offset C.int, value C.uint32_t, errBuf *C.uint8_t, errCap C.int) C.int {
	err := coretick(h).Write(int(offset), uint32(value))
	if err == nil {
		return 0
	}
	if pe, ok := err.(*gosim.PeriphError); ok {
		return writeM4Err(errBuf, errCap, pe)
	}
	return periphErrValue
}

//export gosim_coretick_advance
func gosim_coretick_advance(h C.uintptr_t, errBuf *C.uint8_t, errCap C.int) C.int {
	if err := coretick(h).Advance(); err != nil {
		return writeM4Err(errBuf, errCap, err)
	}
	return 0
}

//export gosim_coretick_ack_rmw
func gosim_coretick_ack_rmw(h C.uintptr_t, kind *C.char, operand C.uint32_t, errBuf *C.uint8_t, errCap C.int) C.int {
	if err := coretick(h).AckRmw(C.GoString(kind), uint32(operand)); err != nil {
		return writeM4Err(errBuf, errCap, err)
	}
	return 0
}

//export gosim_coretick_set_cnt_probe
func gosim_coretick_set_cnt_probe(h C.uintptr_t, has C.int, value C.uint32_t) {
	t := coretick(h)
	if has != 0 {
		v := uint32(value)
		t.SetCntSnapshotProbe(&v)
	} else {
		t.SetCntSnapshotProbe(nil)
	}
}

//export gosim_coretick_state
func gosim_coretick_state(h C.uintptr_t, out *C.uint8_t, outCap C.int) C.int {
	return writeM4State(out, outCap, coretick(h).StateMap())
}

// ---- WatchdogProjection ------------------------------------------------------

//export gosim_wdt_new
func gosim_wdt_new(progressHas C.int, num C.longlong, den C.longlong) C.uintptr_t {
	var progress []int64
	if progressHas != 0 {
		progress = []int64{int64(num), int64(den)}
	}
	w, err := gosim.NewWatchdogProjection(progress)
	if err != nil {
		return 0
	}
	return C.uintptr_t(cgo.NewHandle(w))
}

//export gosim_wdt_free
func gosim_wdt_free(h C.uintptr_t) { cgo.Handle(h).Delete() }

//export gosim_wdt_read
func gosim_wdt_read(h C.uintptr_t, address C.uint32_t, out *C.uint32_t, errBuf *C.uint8_t, errCap C.int) C.int {
	v, err := wdt(h).Read(uint32(address))
	if err != nil {
		return writeM4Err(errBuf, errCap, err)
	}
	*out = C.uint32_t(v)
	return 0
}

//export gosim_wdt_write
func gosim_wdt_write(h C.uintptr_t, address C.uint32_t, value C.uint32_t, errBuf *C.uint8_t, errCap C.int) C.int {
	err := wdt(h).Write(uint32(address), uint32(value))
	if err == nil {
		return 0
	}
	if pe, ok := err.(*gosim.PeriphError); ok {
		return writeM4Err(errBuf, errCap, pe)
	}
	return periphErrValue
}

//export gosim_wdt_advance
func gosim_wdt_advance(h C.uintptr_t, errBuf *C.uint8_t, errCap C.int) C.int {
	if err := wdt(h).Advance(); err != nil {
		return writeM4Err(errBuf, errCap, err)
	}
	return 0
}

//export gosim_wdt_mailbox_feed
func gosim_wdt_mailbox_feed(h C.uintptr_t, errBuf *C.uint8_t, errCap C.int) C.int {
	if err := wdt(h).MailboxFeed(); err != nil {
		return writeM4Err(errBuf, errCap, err)
	}
	return 0
}

//export gosim_wdt_state
func gosim_wdt_state(h C.uintptr_t, out *C.uint8_t, outCap C.int) C.int {
	return writeM4State(out, outCap, wdt(h).StateMap())
}

// ---- IcuInitializationProjection ---------------------------------------------

//export gosim_icu_new
func gosim_icu_new(control0Has C.int, control0 C.uint32_t, control1Has C.int, control1 C.uint32_t,
	commandStepsHas C.int, commandSteps C.longlong, regionProjection C.int) C.uintptr_t {
	var c0, c1 *uint32
	if control0Has != 0 {
		v := uint32(control0)
		c0 = &v
	}
	if control1Has != 0 {
		v := uint32(control1)
		c1 = &v
	}
	var cs *int64
	if commandStepsHas != 0 {
		v := int64(commandSteps)
		cs = &v
	}
	icu, err := gosim.NewIcuInitializationProjection(c0, c1, cs, regionProjection != 0)
	if err != nil {
		return 0
	}
	return C.uintptr_t(cgo.NewHandle(icu))
}

//export gosim_icu_free
func gosim_icu_free(h C.uintptr_t) { cgo.Handle(h).Delete() }

//export gosim_icu_read
func gosim_icu_read(h C.uintptr_t, address C.uint32_t, out *C.uint32_t, errBuf *C.uint8_t, errCap C.int) C.int {
	v, err := icu(h).Read(uint32(address))
	if err != nil {
		return writeM4Err(errBuf, errCap, err)
	}
	*out = C.uint32_t(v)
	return 0
}

//export gosim_icu_write
func gosim_icu_write(h C.uintptr_t, address C.uint32_t, value C.uint32_t, errBuf *C.uint8_t, errCap C.int) C.int {
	if err := icu(h).Write(uint32(address), uint32(value)); err != nil {
		return writeM4Err(errBuf, errCap, err)
	}
	return 0
}

//export gosim_icu_advance
func gosim_icu_advance(h C.uintptr_t) { icu(h).Advance() }

//export gosim_icu_state
func gosim_icu_state(h C.uintptr_t, out *C.uint8_t, outCap C.int) C.int {
	return writeM4State(out, outCap, icu(h).StateMap())
}

// ---- Timer2Delay -------------------------------------------------------------

//export gosim_timer2_new
func gosim_timer2_new() C.uintptr_t {
	return C.uintptr_t(cgo.NewHandle(gosim.NewTimer2Delay()))
}

//export gosim_timer2_free
func gosim_timer2_free(h C.uintptr_t) { cgo.Handle(h).Delete() }

//export gosim_timer2_write
func gosim_timer2_write(h C.uintptr_t, address C.uint32_t, value C.uint32_t, errBuf *C.uint8_t, errCap C.int) C.int {
	if err := timer2(h).Write(uint32(address), uint32(value)); err != nil {
		return writeM4Err(errBuf, errCap, err)
	}
	return 0
}

//export gosim_timer2_read
func gosim_timer2_read(h C.uintptr_t, address C.uint32_t, out *C.uint32_t, errBuf *C.uint8_t, errCap C.int) C.int {
	v, err := timer2(h).Read(uint32(address))
	if err != nil {
		return writeM4Err(errBuf, errCap, err)
	}
	*out = C.uint32_t(v)
	return 0
}

//export gosim_timer2_advance
func gosim_timer2_advance(h C.uintptr_t, sourceTicks C.longlong) C.int {
	if err := timer2(h).Advance(int64(sourceTicks)); err != nil {
		return periphErrValue
	}
	return 0
}

//export gosim_timer2_steps_to_next
func gosim_timer2_steps_to_next(h C.uintptr_t, rate C.longlong, ok *C.int) C.longlong {
	s, has := timer2(h).StepsToNextExpiration(int64(rate))
	if !has {
		*ok = 0
		return 0
	}
	*ok = 1
	return C.longlong(s)
}

//export gosim_timer2_state
func gosim_timer2_state(h C.uintptr_t, out *C.uint8_t, outCap C.int) C.int {
	return writeM4State(out, outCap, timer2(h).StateMap())
}

// ---- SoftwareIrqCommands -----------------------------------------------------

//export gosim_swirq_new
func gosim_swirq_new(p0Has C.int, p0 C.uint32_t, p1Has C.int, p1 C.uint32_t,
	s0Has C.int, s0 C.uint32_t, s1Has C.int, s1 C.uint32_t) C.uintptr_t {
	pending := make([]*uint32, 2)
	src := make([]*uint32, 2)
	if p0Has != 0 {
		v := uint32(p0)
		pending[0] = &v
	}
	if p1Has != 0 {
		v := uint32(p1)
		pending[1] = &v
	}
	if s0Has != 0 {
		v := uint32(s0)
		src[0] = &v
	}
	if s1Has != 0 {
		v := uint32(s1)
		src[1] = &v
	}
	s, err := gosim.NewSoftwareIrqCommands(pending, src)
	if err != nil {
		return 0
	}
	return C.uintptr_t(cgo.NewHandle(s))
}

//export gosim_swirq_free
func gosim_swirq_free(h C.uintptr_t) { cgo.Handle(h).Delete() }

//export gosim_swirq_command
func gosim_swirq_command(h C.uintptr_t, address C.uint32_t, mask C.uint32_t, errBuf *C.uint8_t, errCap C.int) C.int {
	if err := swirq(h).Command(uint32(address), uint32(mask)); err != nil {
		return writeM4Err(errBuf, errCap, err)
	}
	return 0
}

//export gosim_swirq_source_rmw
func gosim_swirq_source_rmw(h C.uintptr_t, address C.uint32_t, kind *C.char, operand C.uint32_t, errBuf *C.uint8_t, errCap C.int) C.int {
	if err := swirq(h).SourceRmw(uint32(address), C.GoString(kind), uint32(operand)); err != nil {
		return writeM4Err(errBuf, errCap, err)
	}
	return 0
}

//export gosim_swirq_ack
func gosim_swirq_ack(h C.uintptr_t, core C.int, bit C.uint32_t) {
	swirq(h).Acknowledge(int(core), uint32(bit))
}

//export gosim_swirq_state
func gosim_swirq_state(h C.uintptr_t, out *C.uint8_t, outCap C.int) C.int {
	return writeM4State(out, outCap, swirq(h).StateMap())
}
