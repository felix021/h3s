package gosim

// Strict-memory bridge tests: the CPU execute engine's data load/store paths
// (State.load, Effect.mem -> apply, specialList, memWord) must go through the
// strict Memory (mem.go), never the toy flatMem. A read of an unwritten or
// unmapped byte raises a *MemError instead of fabricating a zero.

import "testing"

func TestStateLoadStrictUninitialized(t *testing.T) {
	c := NewCPU()
	c.SetDataMem(NewMemory()); c.dmem.AddRegion(0x1000, 0x2000, "test_ram")
	st := &State{dmem: c.dmem}
	if v := st.load(0x1500, 4); v != 0 {
		t.Fatalf("load of unwritten byte returned %#x, want 0 (fault recorded)", v)
	}
	if st.err == nil {
		t.Fatal("load of unwritten byte must record a *MemError, not a zero read")
	}
	if st.err.Kind != "uninitialized_memory" {
		t.Fatalf("kind = %q, want uninitialized_memory", st.err.Kind)
	}
	if st.err.FirstMissing != 0x1500 {
		t.Fatalf("first_missing = %#x, want 0x1500", st.err.FirstMissing)
	}
	if st.err.Region != "test_ram" {
		t.Fatalf("region = %q, want test_ram", st.err.Region)
	}
}

func TestStateLoadStrictUnmapped(t *testing.T) {
	c := NewCPU()
	c.SetDataMem(NewMemory()); c.dmem.AddRegion(0x1000, 0x2000, "test_ram")
	st := &State{dmem: c.dmem}
	if v := st.load(0xdead0000, 4); v != 0 {
		t.Fatalf("unmapped load returned %#x, want 0", v)
	}
	if st.err == nil || st.err.Kind != "unmapped_memory" {
		t.Fatalf("unmapped load must record unmapped_memory, got %+v", st.err)
	}
}

func TestStateLoadStrictFirstFaultWins(t *testing.T) {
	c := NewCPU()
	c.SetDataMem(NewMemory()); c.dmem.AddRegion(0x1000, 0x2000, "test_ram")
	st := &State{dmem: c.dmem}
	st.load(0x1500, 4) // faults (uninitialized)
	first := st.err
	if v := st.load(0xdead0000, 4); v != 0 {
		t.Fatalf("second load returned %#x, want 0", v)
	}
	if st.err != first {
		t.Fatalf("second fault overwrote the first: %+v vs %+v", st.err, first)
	}
}

func TestApplyStrictWriteAndReadback(t *testing.T) {
	c := NewCPU()
	c.SetDataMem(NewMemory()); c.dmem.AddRegion(0x1000, 0x2000, "test_ram")
	if err := c.apply(&Effect{mem: map[uint32]uint32{0x1000: 0x11223344}}); err != nil {
		t.Fatalf("apply of an in-region store failed: %v", err)
	}
	b, err := c.dmem.Read(0x1000, 4)
	if err != nil {
		t.Fatalf("readback failed: %v", err)
	}
	if got := uint32(b[0]) | uint32(b[1])<<8 | uint32(b[2])<<16 | uint32(b[3])<<24; got != 0x11223344 {
		t.Fatalf("readback = %#x, want 0x11223344", got)
	}
}

func TestApplyStrictUnmappedWrite(t *testing.T) {
	c := NewCPU()
	c.SetDataMem(NewMemory()); c.dmem.AddRegion(0x1000, 0x2000, "test_ram")
	err := c.apply(&Effect{mem: map[uint32]uint32{0xdead0000: 1}})
	if err == nil || err.Kind != "unmapped_or_readonly_write" {
		t.Fatalf("unmapped store must fail with unmapped_or_readonly_write, got %+v", err)
	}
}

func TestModelLdWGoesThroughStrictMemory(t *testing.T) {
	c := NewCPU()
	c.SetDataMem(NewMemory()); c.dmem.AddRegion(0x1000, 0x2000, "test_ram")
	c.dmem.Write(0x1010, wordBytes(0xAABBCCDD))
	// modelLdW reads [R[b]+n] as a word; R[b]=0x1000, n=0x10.
	c.R[1] = 0x1000
	st := &State{dmem: c.dmem}
	res, err := modelLdW(c, &Operands{num: map[string]int64{"d": 2, "b": 1, "n": 0x10}}, st)
	if err != nil {
		t.Fatalf("modelLdW failed: %v", err)
	}
	if st.err != nil {
		t.Fatalf("modelLdW of an initialized word must not fault: %v", st.err)
	}
	if res.regs[2] != 0xAABBCCDD {
		t.Fatalf("loaded value = %#x, want 0xAABBCCDD", res.regs[2])
	}
}

func TestModelLdWUninitializedFault(t *testing.T) {
	c := NewCPU()
	c.SetDataMem(NewMemory()); c.dmem.AddRegion(0x1000, 0x2000, "test_ram")
	c.R[1] = 0x1000
	st := &State{dmem: c.dmem}
	if _, err := modelLdW(c, &Operands{num: map[string]int64{"d": 2, "b": 1, "n": 0x10}}, st); err != nil {
		t.Fatalf("modelLdW returned a model error, want the fault on st: %v", err)
	}
	if st.err == nil || st.err.Kind != "uninitialized_memory" {
		t.Fatalf("unwritten load must fault with uninitialized_memory, got %+v", st.err)
	}
}
