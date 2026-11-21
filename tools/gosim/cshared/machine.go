// cgo export wrappers for the M6 Stage A machine (gosim/machine.go — the
// dual-core select phase), callable from Python via ctypes for the
// differential test test_m6_select_consistency.py. See gosim_m6.py for the
// Python side.
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

// machine select error codes (negative; the Python side maps them).
const (
	machineErrLoad = -10 // LoadJSON parse/apply failure
	machineErrWake = -11 // a wake require was violated during select
)

func machine(h C.uintptr_t) *gosim.Machine {
	return cgo.Handle(h).Value().(*gosim.Machine)
}

//export gosim_machine_new
func gosim_machine_new() C.uintptr_t {
	return C.uintptr_t(cgo.NewHandle(gosim.NewMachine()))
}

//export gosim_machine_free
func gosim_machine_free(h C.uintptr_t) { cgo.Handle(h).Delete() }

//export gosim_machine_load
func gosim_machine_load(h C.uintptr_t, data *C.uint8_t, n C.int) C.int {
	if data == nil || n <= 0 {
		return machineErrLoad
	}
	src := unsafe.Slice((*byte)(unsafe.Pointer(data)), int(n))
	if err := machine(h).LoadJSON(src); err != nil {
		return machineErrLoad
	}
	return 0
}

//export gosim_machine_select
func gosim_machine_select(h C.uintptr_t) C.int {
	core, err := machine(h).Select()
	if err != nil {
		return machineErrWake
	}
	return C.int(core)
}

//export gosim_machine_state
func gosim_machine_state(h C.uintptr_t, out *C.uint8_t, outCap C.int) C.int {
	if out == nil || outCap <= 0 {
		return 0
	}
	b := machine(h).StateJSON()
	dst := unsafe.Slice((*byte)(unsafe.Pointer(out)), int(outCap))
	n := copy(dst, b)
	if n < len(dst) {
		dst[n] = 0
	}
	return C.int(n)
}
