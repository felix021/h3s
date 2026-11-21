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

//export gosim_signature_batch
func gosim_signature_batch(in *C.uint8_t, inLen C.int, n C.int, out *C.uint8_t, outCap C.int) C.int {
	inBytes := unsafe.Slice((*byte)(unsafe.Pointer(in)), int(inLen))
	outBytes := unsafe.Slice((*byte)(unsafe.Pointer(out)), int(outCap))
	return C.int(gosim.SignaturesBatch(inBytes, int(n), outBytes))
}

//export gosim_run_segment
func gosim_run_segment(h C.uintptr_t, n C.int, fetchLo C.uint32_t, fetchHi C.uint32_t, out *C.uint8_t, outCap C.int) C.int {
	outBytes := unsafe.Slice((*byte)(unsafe.Pointer(out)), int(outCap))
	written := gosim.WriteSegmentBuffer(outBytes, cpu(h).RunSegment(int(n), uint32(fetchLo), uint32(fetchHi)))
	return C.int(written)
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

// ---- strict memory (M2) exports -------------------------------------------
//
// A standalone strict memory (gosim.Memory) with the probe_boot.Memory
// semantics, for the byte-for-byte differential test test_mem_consistency.py.

// Strict-memory error codes (negative, mirrored in gosim_mem.py).
const (
	memErrInvalid       = -1 // invalid_memory_request
	memErrUninitialized = -2 // uninitialized_memory
	memErrUnmapped      = -3 // unmapped_memory
	memErrUnmappedFetch = -4 // unmapped_instruction_fetch
	memErrReadonlyWrite = -5 // unmapped_or_readonly_write
)

func mem(h C.uintptr_t) *gosim.Memory {
	return cgo.Handle(h).Value().(*gosim.Memory)
}

func memErrCode(kind string) C.int {
	switch kind {
	case "uninitialized_memory":
		return memErrUninitialized
	case "unmapped_memory":
		return memErrUnmapped
	case "unmapped_instruction_fetch":
		return memErrUnmappedFetch
	case "unmapped_or_readonly_write":
		return memErrReadonlyWrite
	}
	return memErrInvalid
}

// writeMemErr copies the MemError JSON (NUL-terminated) into errBuf.
func writeMemErr(errBuf *C.uint8_t, errCap C.int, err *gosim.MemError) {
	if errBuf == nil || errCap <= 0 {
		return
	}
	dst := unsafe.Slice((*byte)(unsafe.Pointer(errBuf)), int(errCap))
	s := err.JSON()
	n := copy(dst, s)
	if n < len(dst) {
		dst[n] = 0
	}
}

func copyBytes(dst *C.uint8_t, n C.int, src []byte) {
	if dst == nil || n <= 0 {
		return
	}
	copy(unsafe.Slice((*byte)(unsafe.Pointer(dst)), int(n)), src)
}

func cloneBytes(src *C.uint8_t, n C.int) []byte {
	if src == nil || n <= 0 {
		return nil
	}
	return append([]byte(nil), unsafe.Slice((*byte)(unsafe.Pointer(src)), int(n))...)
}

//export gosim_smem_new
func gosim_smem_new() C.uintptr_t {
	return C.uintptr_t(cgo.NewHandle(gosim.NewMemory()))
}

//export gosim_smem_free
func gosim_smem_free(h C.uintptr_t) {
	cgo.Handle(h).Delete()
}

//export gosim_smem_set_rom
func gosim_smem_set_rom(h C.uintptr_t, data *C.uint8_t, n C.int) {
	mem(h).SetROM(cloneBytes(data, n))
}

//export gosim_smem_set_app
func gosim_smem_set_app(h C.uintptr_t, entry C.uint32_t, data *C.uint8_t, n C.int) {
	mem(h).SetApp(uint32(entry), cloneBytes(data, n))
}

//export gosim_smem_add_region
func gosim_smem_add_region(h C.uintptr_t, lo, hi C.uint32_t, kind *C.char) {
	mem(h).AddRegion(uint32(lo), uint32(hi), C.GoString(kind))
}

//export gosim_smem_add_readonly
func gosim_smem_add_readonly(h C.uintptr_t, base C.uint32_t, data *C.uint8_t, n C.int) {
	mem(h).AddReadonly(uint32(base), cloneBytes(data, n))
}

//export gosim_smem_set_pll
func gosim_smem_set_pll(h C.uintptr_t, v C.uint32_t, present C.int) {
	mem(h).SetPLL(uint32(v), present != 0)
}

//export gosim_smem_set_fetch
func gosim_smem_set_fetch(h C.uintptr_t, fetch C.int) {
	mem(h).SetFetch(fetch != 0)
}

//export gosim_smem_read
func gosim_smem_read(h C.uintptr_t, addr C.uint32_t, buf *C.uint8_t, n C.int, errBuf *C.uint8_t, errCap C.int) C.int {
	out, err := mem(h).Read(uint32(addr), int(n))
	if err != nil {
		writeMemErr(errBuf, errCap, err)
		return memErrCode(err.Kind)
	}
	copyBytes(buf, n, out)
	return 0
}

//export gosim_smem_write
func gosim_smem_write(h C.uintptr_t, addr C.uint32_t, data *C.uint8_t, n C.int, errBuf *C.uint8_t, errCap C.int) C.int {
	if data == nil || n < 0 {
		err := mem(h).Write(uint32(addr), nil)
		if err != nil {
			writeMemErr(errBuf, errCap, err)
			return memErrCode(err.Kind)
		}
		return 0
	}
	err := mem(h).Write(uint32(addr), unsafe.Slice((*byte)(unsafe.Pointer(data)), int(n)))
	if err != nil {
		writeMemErr(errBuf, errCap, err)
		return memErrCode(err.Kind)
	}
	return 0
}

// ---- peripheral models (M3) exports ----------------------------------------
//
// Timer0 and IRQController (gosim.PeriphState / periph.go), callable from
// Python for the byte-for-byte differential test test_timer0_irq_consistency.py.

// Peripheral error codes (mirrored in gosim_periph.py).
const (
	periphErrBlocked = -6 // strict stop (Python Blocked); errBuf carries the JSON
	periphErrValue   = -7 // value error (Python ValueError)
)

func timer(h C.uintptr_t) *gosim.Timer0 {
	return cgo.Handle(h).Value().(*gosim.Timer0)
}

func irq(h C.uintptr_t) *gosim.IRQController {
	return cgo.Handle(h).Value().(*gosim.IRQController)
}

func writeErrJSON(errBuf *C.uint8_t, errCap C.int, s string) {
	if errBuf == nil || errCap <= 0 {
		return
	}
	dst := unsafe.Slice((*byte)(unsafe.Pointer(errBuf)), int(errCap))
	n := copy(dst, s)
	if n < len(dst) {
		dst[n] = 0
	}
}

//export gosim_timer_new
func gosim_timer_new(allowedMask C.uint32_t) C.uintptr_t {
	var allowed []uint32
	for i := 0; i < 16; i++ {
		if allowedMask&(1<<uint(i)) != 0 {
			allowed = append(allowed, uint32(i))
		}
	}
	if len(allowed) == 0 {
		allowed = []uint32{4}
	}
	return C.uintptr_t(cgo.NewHandle(gosim.NewTimer0(allowed...)))
}

//export gosim_timer_free
func gosim_timer_free(h C.uintptr_t) {
	cgo.Handle(h).Delete()
}

//export gosim_timer_write
func gosim_timer_write(h C.uintptr_t, offset C.int, value C.uint32_t, errBuf *C.uint8_t, errCap C.int) C.int {
	if err := timer(h).Write(int(offset), uint32(value)); err != nil {
		writeErrJSON(errBuf, errCap, err.JSON())
		return periphErrBlocked
	}
	return 0
}

//export gosim_timer_read
func gosim_timer_read(h C.uintptr_t, offset C.int, out *C.uint32_t, errBuf *C.uint8_t, errCap C.int) C.int {
	v, err := timer(h).Read(int(offset))
	if err != nil {
		writeErrJSON(errBuf, errCap, err.JSON())
		return periphErrBlocked
	}
	*out = C.uint32_t(v)
	return 0
}

//export gosim_timer_advance
func gosim_timer_advance(h C.uintptr_t, sourceTicks C.longlong) C.int {
	if err := timer(h).Advance(int64(sourceTicks)); err != nil {
		return periphErrValue
	}
	return 0
}

//export gosim_timer_steps_to_next
func gosim_timer_steps_to_next(h C.uintptr_t, rate C.longlong, ok *C.int) C.longlong {
	s, has := timer(h).StepsToNextExpiration(int64(rate))
	if !has {
		*ok = 0
		return 0
	}
	*ok = 1
	return C.longlong(s)
}

// gosim_timer_state returns the Timer0 snapshot. The return value is a 3-bit
// mask: bit0 con present, bit1 cnt present, bit2 prd present (a missing word is
// Python None). remainders/expirations are always written.
//
//export gosim_timer_state
func gosim_timer_state(h C.uintptr_t, con, cnt, prd *C.uint32_t, pending, deliveryInFlight *C.int,
	remainder, sourceRemainder, expirations *C.longlong) C.int {
	t := timer(h)
	has := C.int(0)
	if t.Con != nil {
		has |= 1
		*con = C.uint32_t(*t.Con)
	}
	if t.Cnt != nil {
		has |= 2
		*cnt = C.uint32_t(*t.Cnt)
	}
	if t.Prd != nil {
		has |= 4
		*prd = C.uint32_t(*t.Prd)
	}
	if t.Pending {
		*pending = 1
	} else {
		*pending = 0
	}
	if t.DeliveryInFlight {
		*deliveryInFlight = 1
	} else {
		*deliveryInFlight = 0
	}
	*remainder = C.longlong(t.Remainder)
	*sourceRemainder = C.longlong(t.SourceRemainder)
	*expirations = C.longlong(t.Expirations)
	return has
}

//export gosim_irq_new
func gosim_irq_new(unmaskable C.int) C.uintptr_t {
	return C.uintptr_t(cgo.NewHandle(&gosim.IRQController{UnmaskableIRQProjection: unmaskable != 0}))
}

//export gosim_irq_free
func gosim_irq_free(h C.uintptr_t) {
	cgo.Handle(h).Delete()
}

//export gosim_irq_set_config_word
func gosim_irq_set_config_word(h C.uintptr_t, index C.int, has C.int, value C.uint32_t) {
	ic := irq(h)
	if has != 0 {
		v := uint32(value)
		ic.Config[index] = &v
	} else {
		ic.Config[index] = nil
	}
}

//export gosim_irq_set_ipmask
func gosim_irq_set_ipmask(h C.uintptr_t, has C.int, value C.uint32_t) {
	ic := irq(h)
	if has != 0 {
		v := uint32(value)
		ic.IPMask = &v
	} else {
		ic.IPMask = nil
	}
}

//export gosim_irq_eligible
func gosim_irq_eligible(h C.uintptr_t, index C.int, icfg C.uint32_t, errBuf *C.uint8_t, errCap C.int) C.int {
	if index < 0 || index >= 32 {
		return periphErrValue
	}
	ok, err := irq(h).Eligible(int(index), uint32(icfg))
	if err != nil {
		writeErrJSON(errBuf, errCap, err.JSON())
		return periphErrBlocked
	}
	if ok {
		return 1
	}
	return 0
}

func main() {}
