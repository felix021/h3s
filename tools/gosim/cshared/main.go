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

//export gosim_cpu_set_icfg
func gosim_cpu_set_icfg(h C.uintptr_t, icfg C.uint32_t) {
	cpu(h).SetICFG(uint32(icfg))
}

//export gosim_clear_cache
func gosim_clear_cache(h C.uintptr_t) {
	cpu(h).ClearCache()
}

// gosim_invalidate_ram drops the decode-cache entries overlapping a flat
// [u32 little-endian] list of changed RAM byte addresses (the incremental-sync
// decode-cache invalidation; see CPU.InvalidateRAMBytes).
//
//export gosim_invalidate_ram
func gosim_invalidate_ram(h C.uintptr_t, addrs *C.uint8_t, n C.int) {
	if addrs == nil || n <= 0 {
		return
	}
	data := unsafe.Slice((*byte)(unsafe.Pointer(addrs)), int(n))
	out := make([]uint32, 0, len(data)/4)
	for i := 0; i+4 <= len(data); i += 4 {
		out = append(out, uint32(data[i])|uint32(data[i+1])<<8|uint32(data[i+2])<<16|uint32(data[i+3])<<24)
	}
	cpu(h).InvalidateRAMBytes(out)
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

// gosim_cpu_set_dmem binds a strict Memory (gosim_smem_*) to a CPU as its data
// memory, so the execute engine's load/store paths route through it (reads of
// unwritten/unmapped bytes raise instead of returning zero). smem == 0 unbinds
// (reverts to the flat memory the raw core / segment contract use).
//
//export gosim_cpu_set_dmem
func gosim_cpu_set_dmem(h C.uintptr_t, smem C.uintptr_t) {
	if smem == 0 {
		cpu(h).SetDataMem(nil)
		return
	}
	cpu(h).SetDataMem(mem(smem))
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

// gosim_smem_replace_ram installs a whole known-byte map (the Python mem.ram
// snapshot): entries is a flat [u32 little-endian addr][u8 value] sequence.
// It replaces the Go ram map wholesale (no events, no guest-write side effect),
// so a byte Python marks unknown is simply absent afterwards.
//
//export gosim_smem_replace_ram
func gosim_smem_replace_ram(h C.uintptr_t, entries *C.uint8_t, n C.int) {
	if entries == nil || n <= 0 {
		mem(h).ReplaceRAM(map[uint32]byte{})
		return
	}
	data := unsafe.Slice((*byte)(unsafe.Pointer(entries)), int(n))
	mem(h).ReplaceRAM(parseRAMEntries(data))
}

// parseRAMEntries decodes a flat [u32 little-endian addr][u8 value] sequence
// into a known-byte map (shared by replace/import).
func parseRAMEntries(data []byte) map[uint32]byte {
	m := make(map[uint32]byte, len(data)/5)
	for i := 0; i+5 <= len(data); i += 5 {
		addr := uint32(data[i]) | uint32(data[i+1])<<8 | uint32(data[i+2])<<16 | uint32(data[i+3])<<24
		m[addr] = data[i+4]
	}
	return m
}

// gosim_smem_import_ram adds known bytes to the Go ram map (the incremental
// delta sync): entries has the same [u32 addr][u8 value] layout. A byte present
// in the map is overwritten; a byte absent stays absent. No events, no
// guest-write side effect (mirrors ImportRAMBytes).
//
//export gosim_smem_import_ram
func gosim_smem_import_ram(h C.uintptr_t, entries *C.uint8_t, n C.int) {
	if entries == nil || n <= 0 {
		return
	}
	data := unsafe.Slice((*byte)(unsafe.Pointer(entries)), int(n))
	mem(h).ImportRAMBytes(parseRAMEntries(data))
}

// gosim_smem_remove_ram marks bytes unknown (Python ram.pop): addrs is a flat
// [u32 little-endian] array. Removing an already-absent byte is a no-op.
// No events, no guest-write side effect (mirrors RemoveRAMBytes).
//
//export gosim_smem_remove_ram
func gosim_smem_remove_ram(h C.uintptr_t, addrs *C.uint8_t, n C.int) {
	if addrs == nil || n <= 0 {
		return
	}
	data := unsafe.Slice((*byte)(unsafe.Pointer(addrs)), int(n))
	out := make([]uint32, 0, len(data)/4)
	for i := 0; i+4 <= len(data); i += 4 {
		out = append(out, uint32(data[i])|uint32(data[i+1])<<8|uint32(data[i+2])<<16|uint32(data[i+3])<<24)
	}
	mem(h).RemoveRAMBytes(out)
}

// gosim_smem_replace_unknown_origins installs the whole unknown_origins map:
// entries is a flat [u32 little-endian addr][u32 little-endian origin] sequence.
// It replaces the Go map wholesale (no events, no guest-write side effect).
//
//export gosim_smem_replace_unknown_origins
func gosim_smem_replace_unknown_origins(h C.uintptr_t, entries *C.uint8_t, n C.int) {
	m := map[uint32]uint32{}
	if entries != nil && n > 0 {
		data := unsafe.Slice((*byte)(unsafe.Pointer(entries)), int(n))
		for i := 0; i+8 <= len(data); i += 8 {
			addr := uint32(data[i]) | uint32(data[i+1])<<8 | uint32(data[i+2])<<16 | uint32(data[i+3])<<24
			origin := uint32(data[i+4]) | uint32(data[i+5])<<8 | uint32(data[i+6])<<16 | uint32(data[i+7])<<24
			m[addr] = origin
		}
	}
	mem(h).ReplaceUnknownOrigins(m)
}

// gosim_smem_replace_icfg_spills installs the whole icfg_spills map: entries is
// a flat [u32 addr][u32 value][u32 known][u32 identity][i32 origin_core][i32
// origin_gen] sequence. It replaces the Go map wholesale.
//
//export gosim_smem_replace_icfg_spills
func gosim_smem_replace_icfg_spills(h C.uintptr_t, entries *C.uint8_t, n C.int) {
	m := map[uint32]*gosim.IcfgSpill{}
	if entries != nil && n > 0 {
		data := unsafe.Slice((*byte)(unsafe.Pointer(entries)), int(n))
		for i := 0; i+24 <= len(data); i += 24 {
			addr := u32le(data[i:])
			value := u32le(data[i+4:])
			known := u32le(data[i+8:])
			identity := u32le(data[i+12:])
			core := int(int32(u32le(data[i+16:])))
			gen := int(int32(u32le(data[i+20:])))
			m[addr] = &gosim.IcfgSpill{Value: value, Known: known, Identity: identity, Origin: &[2]int{core, gen}}
		}
	}
	mem(h).ReplaceIcfgSpills(m)
}

func u32le(b []byte) uint32 {
	return uint32(b[0]) | uint32(b[1])<<8 | uint32(b[2])<<16 | uint32(b[3])<<24
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
