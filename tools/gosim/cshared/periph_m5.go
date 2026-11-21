// cgo export wrappers for the M5 peripheral models (gosim/periph_m5.go),
// callable from Python via ctypes for the byte-for-byte differential test
// test_m5_consistency.py. See gosim_m5.py for the Python side.
package main

/*
#include <stdint.h>
#include <stdlib.h>
*/
import "C"

import (
	"runtime/cgo"

	"h3s/gosim"
)

func srcsync(h C.uintptr_t) *gosim.JLSrcSyncDelivery {
	return cgo.Handle(h).Value().(*gosim.JLSrcSyncDelivery)
}

//export gosim_srcsync_new
func gosim_srcsync_new(completionSteps C.longlong) C.uintptr_t {
	d, err := gosim.NewJLSrcSyncDelivery(int64(completionSteps))
	if err != nil {
		return 0
	}
	return C.uintptr_t(cgo.NewHandle(d))
}

//export gosim_srcsync_free
func gosim_srcsync_free(h C.uintptr_t) { cgo.Handle(h).Delete() }

//export gosim_srcsync_note_start
func gosim_srcsync_note_start(h C.uintptr_t, step C.longlong) {
	srcsync(h).NoteStart(int64(step))
}

//export gosim_srcsync_advance
func gosim_srcsync_advance(h C.uintptr_t, step C.longlong) {
	srcsync(h).Advance(int64(step))
}

//export gosim_srcsync_note_con_write
func gosim_srcsync_note_con_write(h C.uintptr_t, value C.uint32_t) {
	srcsync(h).NoteConWrite(uint32(value))
}

//export gosim_srcsync_reentry_authorized
func gosim_srcsync_reentry_authorized(h C.uintptr_t) C.int {
	if srcsync(h).ReentryAuthorized() {
		return 1
	}
	return 0
}

//export gosim_srcsync_state
func gosim_srcsync_state(h C.uintptr_t, out *C.uint8_t, outCap C.int) C.int {
	return writeM4State(out, outCap, srcsync(h).StateMap())
}
