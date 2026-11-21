// cgo export wrappers for the M6 Stage B after_step no-op judgment
// (gosim/after_step.go), callable from Python via ctypes for the differential
// test test_m6_after_step_consistency.py. See gosim_m6b.py for the Python side.
package main

/*
#include <stdint.h>
#include <stdlib.h>
*/
import "C"

import "h3s/gosim"

// gosim_afterstep_can_prove_noop runs the static after_step judgment for one
// attempt. cfg is a JSON afterStepConfig string (see AfterStep.LoadJSON). It
// returns 1 when the after_step phase is provably a no-op, 0 when a hook fires
// at this pc, and -1 when the config does not parse.
//
//export gosim_afterstep_can_prove_noop
func gosim_afterstep_can_prove_noop(cfg *C.char, core C.int, beforePC C.uint32_t, nextPC C.uint32_t) C.int {
	if cfg == nil {
		return -1
	}
	var a gosim.AfterStep
	if err := a.LoadJSON([]byte(C.GoString(cfg))); err != nil {
		return -1
	}
	ok, _ := a.CanProveNoop(int(core), uint32(beforePC), uint32(nextPC))
	if ok {
		return 1
	}
	return 0
}
