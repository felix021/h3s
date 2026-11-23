// cgo export wrappers for the whole-run trace SHA-256 accumulator
// (gosim.Digest, gosim/digest.go). Go hashes its committed steps inside
// gosim_world_run_loop; Python feeds the slow / boundary steps' reference
// bytes through gosim_world_digest_feed so the one hash state stays in global
// completion order. The debug capture keeps each row's bytes so the Python
// side can do a line-by-line comparison and report the first mismatch.
package main

/*
#include <stdint.h>
#include <stdlib.h>
*/
import "C"

import (
	"unsafe"

	"h3s/gosim"
)

// gosim_world_digest_enable installs a fresh accumulator on the World. debug
// != 0 additionally captures each hashed row's bytes (line-by-line compare).
//
//export gosim_world_digest_enable
func gosim_world_digest_enable(wH C.uintptr_t, debug C.int) {
	world(wH).Digest = gosim.NewDigest(debug != 0)
}

// gosim_world_digest_reset clears the accumulator (hash state, row count, and
// debug capture).
//
//export gosim_world_digest_reset
func gosim_world_digest_reset(wH C.uintptr_t) {
	if d := world(wH).Digest; d != nil {
		d.Reset()
	}
}

// gosim_world_digest_feed appends one slow-path step's reference bytes
// (repr(row)) to the accumulator.
//
//export gosim_world_digest_feed
func gosim_world_digest_feed(wH C.uintptr_t, data *C.uint8_t, n C.int) {
	if d := world(wH).Digest; d != nil && data != nil && n > 0 {
		d.Feed(unsafe.Slice((*byte)(unsafe.Pointer(data)), int(n)))
	}
}

// gosim_world_digest_count returns the number of hashed rows.
//
//export gosim_world_digest_count
func gosim_world_digest_count(wH C.uintptr_t) C.longlong {
	if d := world(wH).Digest; d != nil {
		return C.longlong(d.Count())
	}
	return 0
}

// gosim_world_digest_hex writes the lowercase hex digest into out and returns
// the number of bytes written (0 when the accumulator is absent).
//
//export gosim_world_digest_hex
func gosim_world_digest_hex(wH C.uintptr_t, out *C.uint8_t, cap C.int) C.int {
	d := world(wH).Digest
	if d == nil || out == nil || cap <= 0 {
		return 0
	}
	s := d.Hex()
	return C.int(copy(unsafe.Slice((*byte)(unsafe.Pointer(out)), int(cap)), s))
}

// gosim_world_digest_step_len returns the captured byte length of step i, or
// -1 when debug capture is off or i is out of range.
//
//export gosim_world_digest_step_len
func gosim_world_digest_step_len(wH C.uintptr_t, index C.longlong) C.int {
	if d := world(wH).Digest; d != nil {
		return C.int(d.StepLen(int(index)))
	}
	return -1
}

// gosim_world_digest_step_get copies step i's captured bytes into out and
// returns the length, or -1 when capture is off, i is out of range, or out is
// too small.
//
//export gosim_world_digest_step_get
func gosim_world_digest_step_get(wH C.uintptr_t, index C.longlong, out *C.uint8_t, cap C.int) C.int {
	d := world(wH).Digest
	if d == nil || out == nil || cap < 0 {
		return -1
	}
	b := d.StepBytes(int(index))
	if b == nil || len(b) > int(cap) {
		return -1
	}
	copy(unsafe.Slice((*byte)(unsafe.Pointer(out)), int(cap)), b)
	return C.int(len(b))
}
