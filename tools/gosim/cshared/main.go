// cgo export wrappers for the gosim decode+execute core, callable from Python
// via ctypes (see tools/gosim/gosim.py).
//
// Build: go build -buildmode=c-shared -o libgosim.so ./cshared
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

// Step status codes (match the Go errors the Python side maps).
const (
	stepOK        = 0
	stepNoInstr   = -1
	stepNoModel   = -2
	stepNeedsCtx  = -3
	stepOther     = -4
)

func cpu(h C.uintptr_t) *gosim.CPU {
	return cgo.Handle(h).Value().(*gosim.CPU)
}

//export gosim_new
func gosim_new() C.uintptr_t {
	return C.uintptr_t(cgo.NewHandle(gosim.NewCPU()))
}

//export gosim_new_arch
func gosim_new_arch() C.uintptr_t {
	return C.uintptr_t(cgo.NewHandle(gosim.NewArchCPU()))
}

//export gosim_free
func gosim_free(h C.uintptr_t) {
	cgo.Handle(h).Delete()
}

//export gosim_reset
func gosim_reset(h C.uintptr_t) {
	cpu(h).Reset()
}

//export gosim_mem_write
func gosim_mem_write(h C.uintptr_t, addr C.uint32_t, data *C.uint8_t, n C.int) {
	slice := unsafe.Slice((*byte)(unsafe.Pointer(data)), int(n))
	cpu(h).MemWrite(uint32(addr), slice)
}

//export gosim_mem_read
func gosim_mem_read(h C.uintptr_t, addr C.uint32_t, buf *C.uint8_t, n C.int) {
	slice := unsafe.Slice((*byte)(unsafe.Pointer(buf)), int(n))
	out := cpu(h).MemRead(uint32(addr), int(n))
	copy(slice, out)
}

//export gosim_set_regs
func gosim_set_regs(h C.uintptr_t, regs *C.uint32_t) {
	slice := unsafe.Slice((*uint32)(unsafe.Pointer(regs)), 16)
	cpu(h).SetRegs(slice)
}

//export gosim_get_regs
func gosim_get_regs(h C.uintptr_t, regs *C.uint32_t) {
	slice := unsafe.Slice((*uint32)(unsafe.Pointer(regs)), 16)
	cpu(h).GetRegs(slice)
}

//export gosim_set_state
func gosim_set_state(h C.uintptr_t, flags C.uint32_t, sp C.uint32_t, rets C.uint32_t, pc C.uint32_t) {
	cpu(h).SetState(uint32(flags), uint32(sp), uint32(rets), uint32(pc))
}

//export gosim_get_state
func gosim_get_state(h C.uintptr_t, flags *C.uint32_t, sp *C.uint32_t, rets *C.uint32_t, pc *C.uint32_t) {
	f, s, r, p := cpu(h).GetState()
	*flags = C.uint32_t(f)
	*sp = C.uint32_t(s)
	*rets = C.uint32_t(r)
	*pc = C.uint32_t(p)
}

//export gosim_step
func gosim_step(h C.uintptr_t) C.int {
	return mapErr(cpu(h).Step())
}

//export gosim_batch_step
func gosim_batch_step(h C.uintptr_t, n C.int) C.int {
	c := cpu(h)
	for i := 0; i < int(n); i++ {
		if err := c.Step(); err != nil {
			return mapErr(err)
		}
	}
	return stepOK
}

//export gosim_steps
func gosim_steps(h C.uintptr_t) C.uint64_t {
	return C.uint64_t(cpu(h).Steps())
}

func mapErr(err error) C.int {
	if err == nil {
		return stepOK
	}
	switch {
	case errIs(err, gosim.ErrNoInstruction):
		return stepNoInstr
	case errIs(err, gosim.ErrNoModel):
		return stepNoModel
	case errIs(err, gosim.ErrNeedsContext):
		return stepNeedsCtx
	}
	return stepOther
}

func errIs(err, target error) bool {
	return gosim.ErrIs(err, target)
}

func main() {}
