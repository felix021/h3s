// cgo export wrappers for the minimal single-core World + RunLoop
// (gosim/world.go): the "whole-loop-to-Go" second-wiring exit. Python builds the
// CPU (flat fetch mem + strict data mem via gosim_cpu_set_dmem), then wraps it
// in a World and runs one RunLoop call; the per-step observations serialize to
// JSON and are compared byte-for-byte against the Python reference
// (test_world_runloop_consistency.py).
//
// Scope (this cut): core 0 + strict memory + EMPTY Periph + EMPTY after_step,
// i.e. the configuration where advance and after_step are provably no-ops and
// RunLoop reduces to the stage-1/2 segment batch (already byte-verified by
// test_batch_consistency.py). A non-empty Periph is bound separately through
// gosim_world_set_periph.
package main

/*
#include <stdint.h>
#include <stdlib.h>
*/
import "C"

import (
	"encoding/json"
	"runtime/cgo"

	"h3s/gosim"
)

func world(h C.uintptr_t) *gosim.World {
	return cgo.Handle(h).Value().(*gosim.World)
}

//export gosim_world_new
func gosim_world_new(cpuH C.uintptr_t, smemH C.uintptr_t, fetchLo C.uint32_t, fetchHi C.uint32_t) C.uintptr_t {
	cpu := cgo.Handle(cpuH).Value().(*gosim.CPU)
	var mem *gosim.Memory
	if smemH != 0 {
		mem = cgo.Handle(smemH).Value().(*gosim.Memory)
	} else {
		mem = gosim.NewMemory()
	}
	flags, sp, rets, pc := cpu.GetState()
	core := &gosim.Core{
		ID:        0,
		R:         cpu.R,
		Flags:     flags,
		SP:        sp,
		SPKnown:   true,
		RETS:      rets,
		PC:        pc,
		ICFG:      cpu.GetICFG(),
		ICFGKnown: true,
	}
	w := &gosim.World{
		Cores:     [2]*gosim.Core{core, nil},
		Exec:      cpu,
		Mem:       mem,
		Periph:    &gosim.PeriphState{},
		FetchLo:   uint32(fetchLo),
		FetchHi:   uint32(fetchHi),
		Concrete:  true,
		AfterStep: nil, // no hooks configured: after_step provably empty for every pc
	}
	return C.uintptr_t(cgo.NewHandle(w))
}

//export gosim_world_set_periph
func gosim_world_set_periph(wH C.uintptr_t, periphH C.uintptr_t) {
	if periphH == 0 {
		world(wH).Periph = &gosim.PeriphState{}
		return
	}
	world(wH).Periph = periph(periphH)
}

//export gosim_world_free
func gosim_world_free(h C.uintptr_t) { cgo.Handle(h).Delete() }

type loopResultJSON struct {
	Exit   int            `json:"exit"`
	StopPC uint32         `json:"stop_pc"`
	Reason string         `json:"reason"`
	Irq    *gosim.IrqReqJSON `json:"irq"`
	Steps  []loopStepJSON `json:"steps"`
}

type loopStepJSON struct {
	Core   int       `json:"core"`
	PC     uint32    `json:"pc"`
	NextPC uint32    `json:"next_pc"`
	Flags  uint8     `json:"flags"`
	R      [16]uint32 `json:"r"`
}

func loopResultToJSON(res gosim.LoopResult) ([]byte, error) {
	out := &loopResultJSON{Exit: int(res.Exit), StopPC: res.StopPC, Reason: res.Reason}
	if res.Irq != nil {
		out.Irq = &gosim.IrqReqJSON{Core: res.Irq.Core, Irq: res.Irq.Irq}
	}
	for _, s := range res.Steps {
		out.Steps = append(out.Steps, loopStepJSON{
			Core: s.Core, PC: s.PC, NextPC: s.NextPC, Flags: s.Flags, R: s.R,
		})
	}
	return json.Marshal(out)
}

//export gosim_world_run_loop
func gosim_world_run_loop(wH C.uintptr_t, n C.int, out *C.uint8_t, outCap C.int) C.int {
	res := world(wH).RunLoop(int(n))
	b, err := loopResultToJSON(res)
	if err != nil {
		return writePeriphBuf(out, outCap, []byte("null"))
	}
	return writePeriphBuf(out, outCap, b)
}
