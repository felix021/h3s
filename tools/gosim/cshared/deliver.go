// cgo export wrappers for the M6 Stage C delivery projections
// (gosim/deliver.go), callable from Python via ctypes for the differential
// test test_m6c_deliver_consistency.py. See gosim_m6c.py for the Python side.
package main

/*
#include <stdint.h>
#include <stdlib.h>
*/
import "C"

import (
	"runtime/cgo"
	"unsafe"

	"h3s/gosim"
)

// deliver operation codes (the Python side maps them).
const (
	opDeliverSoftwareIrq = 0
	opDeliverCoreTick    = 1
	opDeliverTimer0Irq   = 2
	opDeliverAudioDmaIrq = 3
	opDeliverSrcSyncIrq  = 4
	opReturnSoftwareIrq  = 5
	opReturnCoreTick     = 6
	opReturnTimer0Irq    = 7
	opReturnAudioDmaIrq  = 8
	opReturnSrcSyncIrq   = 9
)

// deliver error codes (negative).
const (
	deliverErrLoad    = -20 // LoadJSON parse/apply failure
	deliverErrValue   = -21 // Python ValueError (require guard)
	deliverErrBlocked = -22 // Python Blocked (strict stop); errBuf carries JSON
)

func deliver(h C.uintptr_t) *gosim.DeliverMachine {
	return cgo.Handle(h).Value().(*gosim.DeliverMachine)
}

//export gosim_deliver_new
func gosim_deliver_new() C.uintptr_t {
	return C.uintptr_t(cgo.NewHandle(gosim.NewDeliverMachine()))
}

//export gosim_deliver_free
func gosim_deliver_free(h C.uintptr_t) { cgo.Handle(h).Delete() }

//export gosim_deliver_load
func gosim_deliver_load(h C.uintptr_t, data *C.uint8_t, n C.int) C.int {
	if data == nil || n <= 0 {
		return deliverErrLoad
	}
	src := unsafe.Slice((*byte)(unsafe.Pointer(data)), int(n))
	if err := deliver(h).LoadJSON(src); err != nil {
		return deliverErrLoad
	}
	return 0
}

//export gosim_deliver_call
func gosim_deliver_call(h C.uintptr_t, op C.int, coreID C.int, irq C.uint32_t, errBuf *C.uint8_t, errCap C.int) C.int {
	m := deliver(h)
	var err error
	switch int(op) {
	case opDeliverSoftwareIrq:
		err = m.DeliverSoftwareIrq(int(coreID), uint32(irq))
	case opDeliverCoreTick:
		err = m.DeliverCoreTick(int(coreID))
	case opDeliverTimer0Irq:
		err = m.DeliverTimer0Irq(int(coreID))
	case opDeliverAudioDmaIrq:
		err = m.DeliverAudioDmaIrq(int(coreID))
	case opDeliverSrcSyncIrq:
		err = m.DeliverSrcSyncIrq(int(coreID))
	case opReturnSoftwareIrq:
		err = m.ReturnSoftwareIrq(int(coreID))
	case opReturnCoreTick:
		err = m.ReturnCoreTick(int(coreID))
	case opReturnTimer0Irq:
		err = m.ReturnTimer0Irq(int(coreID))
	case opReturnAudioDmaIrq:
		err = m.ReturnAudioDmaIrq(int(coreID))
	case opReturnSrcSyncIrq:
		err = m.ReturnSrcSyncIrq(int(coreID))
	default:
		return deliverErrValue
	}
	if err == nil {
		return 0
	}
	switch e := err.(type) {
	case *gosim.DeliverValueError:
		if errBuf != nil && errCap > 0 {
			dst := unsafe.Slice((*byte)(unsafe.Pointer(errBuf)), int(errCap))
			n := copy(dst, e.Msg)
			if n < len(dst) {
				dst[n] = 0
			}
		}
		return deliverErrValue
	case *gosim.PeriphError:
		if errBuf != nil && errCap > 0 {
			dst := unsafe.Slice((*byte)(unsafe.Pointer(errBuf)), int(errCap))
			n := copy(dst, e.JSON())
			if n < len(dst) {
				dst[n] = 0
			}
		}
		return deliverErrBlocked
	}
	return deliverErrValue
}

//export gosim_deliver_state
func gosim_deliver_state(h C.uintptr_t, out *C.uint8_t, outCap C.int) C.int {
	if out == nil || outCap <= 0 {
		return 0
	}
	b := deliver(h).StateJSON()
	dst := unsafe.Slice((*byte)(unsafe.Pointer(out)), int(outCap))
	n := copy(dst, b)
	if n < len(dst) {
		dst[n] = 0
	}
	return C.int(n)
}
