// cgo export wrappers for the whole-loop advance state transfer
// (gosim/periph_state.go): construct a PeriphState, move the live Timer0 / IRQ /
// core-tick state in and out as JSON, and run one advance step. Callable from
// Python via ctypes (tools/gosim/gosim_world.py). See test_advance_consistency.py.
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

func periph(h C.uintptr_t) *gosim.PeriphState {
	return cgo.Handle(h).Value().(*gosim.PeriphState)
}

// writePeriphBuf copies b into out (NUL-terminated) and returns the byte count.
func writePeriphBuf(out *C.uint8_t, outCap C.int, b []byte) C.int {
	if out == nil || outCap <= 0 {
		return 0
	}
	dst := unsafe.Slice((*byte)(unsafe.Pointer(out)), int(outCap))
	n := copy(dst, b)
	if n < len(dst) {
		dst[n] = 0
	}
	return C.int(n)
}

//export gosim_periph_new
func gosim_periph_new() C.uintptr_t {
	return C.uintptr_t(cgo.NewHandle(&gosim.PeriphState{}))
}

//export gosim_periph_free
func gosim_periph_free(h C.uintptr_t) { cgo.Handle(h).Delete() }

//export gosim_periph_load
func gosim_periph_load(h C.uintptr_t, data *C.uint8_t, n C.int, errBuf *C.uint8_t, errCap C.int) C.int {
	if data == nil || n <= 0 {
		writePeriphBuf(errBuf, errCap, []byte("empty periph state"))
		return -1
	}
	in := unsafe.Slice((*byte)(unsafe.Pointer(data)), int(n))
	if err := periph(h).LoadStateJSON(in); err != nil {
		writePeriphBuf(errBuf, errCap, []byte(err.Error()))
		return -1
	}
	return 0
}

//export gosim_periph_state
func gosim_periph_state(h C.uintptr_t, out *C.uint8_t, outCap C.int) C.int {
	b, err := periph(h).StateJSON()
	if err != nil {
		writePeriphBuf(out, outCap, []byte("null"))
		return -1
	}
	return writePeriphBuf(out, outCap, b)
}

//export gosim_periph_advance
func gosim_periph_advance(h C.uintptr_t, icfg C.uint32_t, out *C.uint8_t, outCap C.int) C.int {
	b, err := periph(h).AdvanceJSON(uint32(icfg))
	if err != nil {
		writePeriphBuf(out, outCap, []byte("null"))
		return -1
	}
	return writePeriphBuf(out, outCap, b)
}
