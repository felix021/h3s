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
	"unsafe"

	"h3s/gosim"
)

func world(h C.uintptr_t) *gosim.World {
	return cgo.Handle(h).Value().(*gosim.World)
}

// buildCore snapshots one execute engine's architectural state into the World's
// per-core twin (R / flags / sp / rets / pc / icfg). The special registers
// (USP/SSP/RETI/IRQPSR/active_context/...) stay nil/empty here; the delivery
// projections only populate them when a delivery is actually dispatched in-Go.
func buildCore(cpu *gosim.CPU, id int) *gosim.Core {
	flags, sp, rets, pc := cpu.GetState()
	return &gosim.Core{
		ID:        id,
		R:         cpu.R,
		Flags:     flags,
		SP:        sp,
		SPKnown:   true,
		RETS:      rets,
		PC:        pc,
		ICFG:      cpu.GetICFG(),
		ICFGKnown: true,
	}
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
	w := &gosim.World{
		Cores:     [2]*gosim.Core{buildCore(cpu, 0), nil},
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

// gosim_world_dual_new builds the dual-core World: both per-core execute engines
// (core 0 = cpu0H, core 1 = cpu1H), the shared strict memory (smemH, or a fresh
// one), and the M6A select Machine (machineH) / M6C deliver projections
// (deliverH) bound. A zero handle substitutes a fresh empty Machine/Deliver.
// World.RunLoop then takes the dual-core path (runDualCore) instead of the
// single-core segment batch. Periph and AfterStep are still bound separately via
// gosim_world_set_periph / gosim_world_set_afterstep.
//
//export gosim_world_dual_new
func gosim_world_dual_new(cpu0H, cpu1H, smemH, machineH, deliverH C.uintptr_t,
	fetchLo, fetchHi C.uint32_t) C.uintptr_t {
	cpu0 := cgo.Handle(cpu0H).Value().(*gosim.CPU)
	cpu1 := cgo.Handle(cpu1H).Value().(*gosim.CPU)
	var mem *gosim.Memory
	if smemH != 0 {
		mem = cgo.Handle(smemH).Value().(*gosim.Memory)
	} else {
		mem = gosim.NewMemory()
	}
	var machine *gosim.Machine
	if machineH != 0 {
		machine = cgo.Handle(machineH).Value().(*gosim.Machine)
	} else {
		machine = gosim.NewMachine()
	}
	var deliver *gosim.DeliverMachine
	if deliverH != 0 {
		deliver = cgo.Handle(deliverH).Value().(*gosim.DeliverMachine)
	} else {
		deliver = gosim.NewDeliverMachine()
	}
	w := &gosim.World{
		Cores:     [2]*gosim.Core{buildCore(cpu0, 0), buildCore(cpu1, 1)},
		Exec:      cpu0,
		Exec1:     cpu1,
		Mem:       mem,
		Periph:    &gosim.PeriphState{},
		FetchLo:   uint32(fetchLo),
		FetchHi:   uint32(fetchHi),
		Concrete:  true,
		Machine:   machine,
		Deliver:   deliver,
		AfterStep: nil,
	}
	return C.uintptr_t(cgo.NewHandle(w))
}

// gosim_world_set_afterstep binds the M6B after_step hook configuration (JSON,
// see gosim.AfterStep.LoadJSON / afterStepConfig). cfg == nil clears the config
// (after_step provably empty for every pc). Returns 0 on success, -1 on a parse
// failure.
//
//export gosim_world_set_afterstep
func gosim_world_set_afterstep(wH C.uintptr_t, cfg *C.char) C.int {
	if cfg == nil {
		world(wH).AfterStep = nil
		return 0
	}
	a := &gosim.AfterStep{}
	if err := a.LoadJSON([]byte(C.GoString(cfg))); err != nil {
		return -1
	}
	world(wH).AfterStep = a
	return 0
}

// gosim_world_set_core_inflight sets the delivery-deferral-relevant execution
// context of one World core: whether an instruction is mid-flight
// (after_then/repeat in flight) and the active in-flight IRQ handler context
// ("" = thread mode). This is the Python-side dispatch_delivery deferral state,
// supplied to Go so the dual-core loop can run its honest deferral judgment
// (deliveryDeferred) and hand the request back rather than fabricate a delivery.
//
//export gosim_world_set_core_inflight
func gosim_world_set_core_inflight(wH C.uintptr_t, core C.int, repeatActive C.int, activeContext *C.char) C.int {
	w := world(wH)
	if int(core) < 0 || int(core) >= 2 || w.Cores[core] == nil {
		return -1
	}
	w.Cores[core].RepeatActive = repeatActive != 0
	if activeContext == nil {
		w.Cores[core].ActiveContext = ""
	} else {
		w.Cores[core].ActiveContext = C.GoString(activeContext)
	}
	return 0
}

//export gosim_world_set_periph
func gosim_world_set_periph(wH C.uintptr_t, periphH C.uintptr_t) {
	if periphH == 0 {
		world(wH).Periph = &gosim.PeriphState{}
		return
	}
	world(wH).Periph = periph(periphH)
}

// coreStateJSON is the JSON round-trip shape of one World Core (the full
// architectural state plus the delivery-deferral context). It lets the explore
// engine snapshot the live Python CPU into Go and read the post-loop core state
// back without guessing. nil u32 pointers mirror the Python None.
type coreStateJSON struct {
	ID               int               `json:"id"`
	R                [16]uint32        `json:"r"`
	Flags            uint32            `json:"flags"`
	SP               uint32            `json:"sp"`
	SPKnown          bool              `json:"sp_known"`
	RETS             uint32            `json:"rets"`
	PC               uint32            `json:"pc"`
	ICFG             uint32            `json:"icfg"`
	ICFGKnown        bool              `json:"icfg_known"`
	Steps            uint64            `json:"steps"`
	RepeatActive     bool              `json:"repeat_active"`
	USP              *uint32           `json:"usp"`
	SSP              *uint32           `json:"ssp"`
	RETI             *uint32           `json:"reti"`
	IRQPSR           *uint32           `json:"irq_psr"`
	ActiveContext    string            `json:"active_context"`
	BootstrapContext bool              `json:"bootstrap_context"`
	LastExplicitSSP  *uint32           `json:"last_explicit_ssp"`
	AudioIrqOuter    *audioIrqOuterJSON `json:"audio_irq_outer_context"`
}

type audioIrqOuterJSON struct {
	ActiveContext string  `json:"active_context"`
	RETI          *uint32 `json:"reti"`
	SP            uint32  `json:"sp"`
}

func coreFromJSON(s *coreStateJSON) *gosim.Core {
	if s == nil {
		return nil
	}
	c := &gosim.Core{
		ID:               s.ID,
		R:                s.R,
		Flags:            s.Flags,
		SP:               s.SP,
		SPKnown:          s.SPKnown,
		RETS:             s.RETS,
		PC:               s.PC,
		ICFG:             s.ICFG,
		ICFGKnown:        s.ICFGKnown,
		Steps:            s.Steps,
		RepeatActive:     s.RepeatActive,
		USP:              s.USP,
		SSP:              s.SSP,
		RETI:             s.RETI,
		IRQPSR:           s.IRQPSR,
		ActiveContext:    s.ActiveContext,
		BootstrapContext: s.BootstrapContext,
		LastExplicitSSP:  s.LastExplicitSSP,
	}
	if s.AudioIrqOuter != nil {
		c.AudioIrqOuter = &gosim.AudioIrqOuterContext{
			ActiveContext: s.AudioIrqOuter.ActiveContext,
			RETI:          s.AudioIrqOuter.RETI,
			SP:            s.AudioIrqOuter.SP,
		}
	}
	return c
}

func coreToJSON(c *gosim.Core) *coreStateJSON {
	if c == nil {
		return nil
	}
	var outer *audioIrqOuterJSON
	if c.AudioIrqOuter != nil {
		outer = &audioIrqOuterJSON{
			ActiveContext: c.AudioIrqOuter.ActiveContext,
			RETI:          c.AudioIrqOuter.RETI,
			SP:            c.AudioIrqOuter.SP,
		}
	}
	return &coreStateJSON{
		ID:               c.ID,
		R:                c.R,
		Flags:            c.Flags,
		SP:               c.SP,
		SPKnown:          c.SPKnown,
		RETS:             c.RETS,
		PC:               c.PC,
		ICFG:             c.ICFG,
		ICFGKnown:        c.ICFGKnown,
		Steps:            c.Steps,
		RepeatActive:     c.RepeatActive,
		USP:              c.USP,
		SSP:              c.SSP,
		RETI:             c.RETI,
		IRQPSR:           c.IRQPSR,
		ActiveContext:    c.ActiveContext,
		BootstrapContext: c.BootstrapContext,
		LastExplicitSSP:  c.LastExplicitSSP,
		AudioIrqOuter:    outer,
	}
}

type coresStateJSON struct {
	Cores [2]*coreStateJSON `json:"cores"`
}

// gosim_world_dual_set_cores replaces both World cores with the JSON snapshot
// (the live Python CPU state: architectural registers + steps + the
// delivery-deferral context). It must be called before the first run_loop; the
// per-core execute engines are re-synced from these cores on the next step.
// Returns 0 on success, -1 on a parse failure.
//
//export gosim_world_dual_set_cores
func gosim_world_dual_set_cores(wH C.uintptr_t, in *C.uint8_t, inLen C.int) C.int {
	w := world(wH)
	var s coresStateJSON
	if err := json.Unmarshal(C.GoBytes(unsafe.Pointer(in), inLen), &s); err != nil {
		return -1
	}
	for i := 0; i < 2; i++ {
		w.Cores[i] = coreFromJSON(s.Cores[i])
	}
	return 0
}

//export gosim_world_free
func gosim_world_free(h C.uintptr_t) { cgo.Handle(h).Delete() }

type loopResultJSON struct {
	Exit     int               `json:"exit"`
	StopPC   uint32            `json:"stop_pc"`
	StopCore int               `json:"stop_core"`
	Reason   string            `json:"reason"`
	Irq      *gosim.IrqReqJSON `json:"irq"`
	Err      json.RawMessage   `json:"err"`
	Steps    []loopStepJSON    `json:"steps"`
	Cores    [2]*coreStateJSON `json:"cores"`
}

type loopStepJSON struct {
	Core   int        `json:"core"`
	PC     uint32     `json:"pc"`
	NextPC uint32     `json:"next_pc"`
	Flags  uint8      `json:"flags"`
	R      [16]uint32 `json:"r"`
}

func loopResultToJSON(res gosim.LoopResult, w *gosim.World) ([]byte, error) {
	out := &loopResultJSON{Exit: int(res.Exit), StopPC: res.StopPC, StopCore: res.StopCore, Reason: res.Reason}
	if res.Irq != nil {
		out.Irq = &gosim.IrqReqJSON{Core: res.Irq.Core, Irq: res.Irq.Irq}
	}
	if len(res.ErrJSON) > 0 {
		out.Err = json.RawMessage(res.ErrJSON)
	}
	for _, s := range res.Steps {
		out.Steps = append(out.Steps, loopStepJSON{
			Core: s.Core, PC: s.PC, NextPC: s.NextPC, Flags: s.Flags, R: s.R,
		})
	}
	if w != nil {
		for i := 0; i < 2; i++ {
			out.Cores[i] = coreToJSON(w.Cores[i])
		}
	}
	return json.Marshal(out)
}

//export gosim_world_run_loop
func gosim_world_run_loop(wH C.uintptr_t, n C.int, out *C.uint8_t, outCap C.int) C.int {
	res := world(wH).RunLoop(int(n))
	b, err := loopResultToJSON(res, world(wH))
	if err != nil {
		return writePeriphBuf(out, outCap, []byte("null"))
	}
	return writePeriphBuf(out, outCap, b)
}
