package gosim

import (
	"bytes"
	"testing"
)

// fakeBus is a minimal Bus for the MMIO routing tests. It models one aligned
// word register at wordAddr, one byte-granular fixture register at byteAddr,
// and one byte-write register at byteWriteAddr.
type fakeBus struct {
	wordAddr      uint32
	wordValue     uint32
	byteAddr      uint32
	byteFixture   []byte
	byteWriteAddr uint32
	wroteWord     *uint32
	wroteByte     *byte
	writtenAddr   uint32
}

func (f *fakeBus) Handles(addr uint32, size int) bool {
	return addr == f.wordAddr && size == 4
}

func (f *fakeBus) Read(addr uint32) uint32 { return f.wordValue }

func (f *fakeBus) ReadBytes(addr uint32, size int) []byte {
	if addr == f.byteAddr && size <= len(f.byteFixture) {
		return append([]byte(nil), f.byteFixture[:size]...)
	}
	return nil
}

func (f *fakeBus) Write(addr uint32, value uint32) {
	f.writtenAddr = addr
	f.wroteWord = &value
}

func (f *fakeBus) HandlesByteWrite(addr uint32) bool { return addr == f.byteWriteAddr }

func (f *fakeBus) WriteByteAt(addr uint32, b byte) {
	f.writtenAddr = addr
	f.wroteByte = &b
}

func le4(v uint32) []byte {
	return []byte{byte(v), byte(v >> 8), byte(v >> 16), byte(v >> 24)}
}

// romBytes returns a 0x20000-byte ROM image with a simple per-byte pattern.
func romBytes(n int) []byte {
	out := make([]byte, n)
	for i := range out {
		out[i] = byte(i * 7)
	}
	return out
}

func TestMemROMReadFullContainment(t *testing.T) {
	m := NewMemory()
	rom := romBytes(0x20000)
	m.SetROM(rom)
	got, err := m.Read(0x100, 4)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if !bytes.Equal(got, rom[0x100:0x104]) {
		t.Fatalf("rom read mismatch: %x", got)
	}
	// boundary: [0, 0x20000) is served, [0x20000, ...) is not.
	if _, err := m.Read(0x20000, 1); err == nil || err.Kind != "unmapped_memory" {
		t.Fatalf("expected unmapped_memory at rom end, got %v", err)
	}
}

func TestMemAppRead(t *testing.T) {
	m := NewMemory()
	app := make([]byte, 0x100)
	for i := range app {
		app[i] = byte(i)
	}
	m.SetApp(0x06000100, app)
	got, err := m.Read(0x06000100, 4)
	if err != nil || !bytes.Equal(got, app[:4]) {
		t.Fatalf("app read mismatch: %x err=%v", got, err)
	}
	// crossing below the app base is not the app mirror
	if _, err := m.Read(0x060000ff, 4); err == nil || err.Kind != "unmapped_memory" {
		t.Fatalf("expected unmapped_memory below app base, got %v", err)
	}
}

func TestMemReadonlyRead(t *testing.T) {
	m := NewMemory()
	data := []byte{1, 2, 3, 4, 5, 6}
	m.AddReadonly(0x19fd40, data)
	got, err := m.Read(0x19fd40, 6)
	if err != nil || !bytes.Equal(got, data) {
		t.Fatalf("readonly read mismatch: %x err=%v", got, err)
	}
	// crossing the mirror end falls through to the strict path
	if _, err := m.Read(0x19fd44, 4); err == nil || err.Kind != "unmapped_memory" {
		t.Fatalf("expected unmapped_memory past readonly end, got %v", err)
	}
}

func TestMemUninitializedFirstMissing(t *testing.T) {
	m := NewMemory()
	m.AddRegion(0x00123000, 0x00123840, "bootstrap_stack")
	if err := m.Write(0x00123000, []byte{0xAA}); err != nil {
		t.Fatalf("write: %v", err)
	}
	if err := m.Write(0x00123002, []byte{0xCC}); err != nil {
		t.Fatalf("write: %v", err)
	}
	_, err := m.Read(0x00123000, 4)
	if err == nil {
		t.Fatal("expected uninitialized_memory")
	}
	if err.Kind != "uninitialized_memory" || err.Region != "bootstrap_stack" ||
		err.FirstMissing != 0x00123001 || err.Address != 0x00123000 || err.Size != 4 {
		t.Fatalf("bad error: %+v", err)
	}
}

func TestMemUnmappedReadAndFetch(t *testing.T) {
	m := NewMemory()
	_, err := m.Read(0xdead0000, 4)
	if err == nil || err.Kind != "unmapped_memory" || err.Access != "read" {
		t.Fatalf("expected unmapped_memory, got %v", err)
	}
	m.SetFetch(true)
	_, err = m.Read(0xdead0000, 4)
	if err == nil || err.Kind != "unmapped_instruction_fetch" || err.Access != "fetch" {
		t.Fatalf("expected unmapped_instruction_fetch, got %v", err)
	}
}

func TestMemFetchReadsRegionRAM(t *testing.T) {
	m := NewMemory()
	m.AddRegion(0x1000, 0x2000, "r")
	if err := m.Write(0x1000, []byte{9, 9, 9, 9}); err != nil {
		t.Fatalf("write: %v", err)
	}
	m.SetFetch(true)
	got, err := m.Read(0x1000, 4)
	if err != nil || !bytes.Equal(got, []byte{9, 9, 9, 9}) {
		t.Fatalf("fetch region read mismatch: %x err=%v", got, err)
	}
	// uninitialized stays uninitialized even on the fetch path
	_, err = m.Read(0x1010, 4)
	if err == nil || err.Kind != "uninitialized_memory" {
		t.Fatalf("expected uninitialized_memory on fetch, got %v", err)
	}
}

func TestMemROMWriteReject(t *testing.T) {
	m := NewMemory()
	m.SetROM(romBytes(0x20000))
	if err := m.Write(0x100, []byte{1, 2, 3, 4}); err == nil || err.Kind != "unmapped_or_readonly_write" {
		t.Fatalf("expected rom write rejection, got %v", err)
	}
	// partial overlap past the ROM end is still rejected
	if err := m.Write(0x1ffff, []byte{1, 2, 3, 4}); err == nil || err.Kind != "unmapped_or_readonly_write" {
		t.Fatalf("expected partial rom write rejection, got %v", err)
	}
	// a region right after the ROM window is writable (no ROM overlap)
	m.AddRegion(0x20000, 0x21000, "after_rom")
	if err := m.Write(0x20000, []byte{1, 2, 3, 4}); err != nil {
		t.Fatalf("write after rom should succeed, got %v", err)
	}
}

func TestMemRegionWriteReadbackAndEvents(t *testing.T) {
	m := NewMemory()
	m.AddRegion(0x1000, 0x2000, "r")
	data := []byte{1, 2, 3, 4}
	if err := m.Write(0x1000, data); err != nil {
		t.Fatalf("write: %v", err)
	}
	got, err := m.Read(0x1000, 4)
	if err != nil || !bytes.Equal(got, data) {
		t.Fatalf("readback mismatch: %x err=%v", got, err)
	}
	writes := m.Writes()
	if len(writes) != 1 || writes[0].Region != "r" || writes[0].Address != 0x1000 ||
		!bytes.Equal(writes[0].Bytes, data) {
		t.Fatalf("bad write events: %+v", writes)
	}
}

func TestMemWriteOutsideRegion(t *testing.T) {
	m := NewMemory()
	if err := m.Write(0xdead0000, []byte{1, 2, 3, 4}); err == nil || err.Kind != "unmapped_or_readonly_write" {
		t.Fatalf("expected unmapped_or_readonly_write, got %v", err)
	}
}

func TestMemReadSpanningRegionBoundary(t *testing.T) {
	m := NewMemory()
	m.AddRegion(0x1000, 0x1010, "r")
	if err := m.Write(0x1000, make([]byte, 0x10)); err != nil {
		t.Fatalf("write: %v", err)
	}
	if _, err := m.Read(0x1008, 0x10); err == nil || err.Kind != "unmapped_memory" {
		t.Fatalf("expected unmapped_memory for a request spanning the region end, got %v", err)
	}
}

func TestMemPLL(t *testing.T) {
	m := NewMemory()
	m.SetPLL(0x12345678, true)
	got, err := m.Read(PLLAddr, 4)
	if err != nil || !bytes.Equal(got, le4(0x12345678)) {
		t.Fatalf("pll read mismatch: %x err=%v", got, err)
	}
	// wrong size is not the PLL fixture
	if _, err := m.Read(PLLAddr, 2); err == nil || err.Kind != "unmapped_memory" {
		t.Fatalf("expected unmapped_memory for non-word PLL read, got %v", err)
	}
	// absent fixture falls through to unmapped with the SDK register hint
	m.SetPLL(0, false)
	if _, err := m.Read(PLLAddr, 4); err == nil || err.Kind != "unmapped_memory" {
		t.Fatalf("expected unmapped_memory for absent PLL, got %v", err)
	}
	// fetch never serves the PLL fixture
	m.SetPLL(0x12345678, true)
	m.SetFetch(true)
	if _, err := m.Read(PLLAddr, 4); err == nil || err.Kind != "unmapped_instruction_fetch" {
		t.Fatalf("expected unmapped_instruction_fetch for PLL fetch, got %v", err)
	}
}

func TestMemMMIORouting(t *testing.T) {
	m := NewMemory()
	bus := &fakeBus{wordAddr: 0xfd1000, wordValue: 0xaabbccdd, byteAddr: 0xfd2000,
		byteFixture: []byte{0x11, 0x22, 0x33, 0x44}, byteWriteAddr: 0xfd3000}
	m.SetBus(bus)

	got, err := m.Read(0xfd1000, 4)
	if err != nil || !bytes.Equal(got, le4(0xaabbccdd)) {
		t.Fatalf("mmio word read mismatch: %x err=%v", got, err)
	}
	// byte-granular fixture read
	got, err = m.Read(0xfd2000, 2)
	if err != nil || !bytes.Equal(got, []byte{0x11, 0x22}) {
		t.Fatalf("mmio byte fixture read mismatch: %x err=%v", got, err)
	}
	// word write routes to the bus, not the ram
	if err := m.Write(0xfd1000, le4(0x01020304)); err != nil {
		t.Fatalf("mmio write: %v", err)
	}
	if bus.wroteWord == nil || *bus.wroteWord != 0x01020304 || bus.writtenAddr != 0xfd1000 {
		t.Fatalf("mmio word write not routed: %+v", bus)
	}
	// single-byte write routes to the byte-write hook
	if err := m.Write(0xfd3000, []byte{0x7A}); err != nil {
		t.Fatalf("mmio byte write: %v", err)
	}
	if bus.wroteByte == nil || *bus.wroteByte != 0x7A || bus.writtenAddr != 0xfd3000 {
		t.Fatalf("mmio byte write not routed: %+v", bus)
	}
	// an unhandled MMIO address still takes the strict path
	if _, err := m.Read(0xfd4000, 4); err == nil || err.Kind != "unmapped_memory" {
		t.Fatalf("expected unmapped_memory for unhandled mmio, got %v", err)
	}
}

func TestMemICfgSpillsOverlay(t *testing.T) {
	m := NewMemory()
	m.AddRegion(0x1000, 0x2000, "r")
	m.icfgSpills[0x1000] = &IcfgSpill{Value: 0x11223344, Known: 0x0000ffff, Identity: 0xffff0000}
	// write byte offset 2 marks it known; the spill survives (bytes 3 unknown)
	if err := m.Write(0x1002, []byte{0x55}); err != nil {
		t.Fatalf("write: %v", err)
	}
	sp := m.icfgSpills[0x1000]
	if sp == nil || sp.Value != 0x11553344 || sp.Known != 0x00ffffff {
		t.Fatalf("spill overlay mismatch: %+v", sp)
	}
	// writing the remaining bytes makes the word fully known and drops the spill
	if err := m.Write(0x1000, []byte{0xAA, 0xBB}); err != nil {
		t.Fatalf("write: %v", err)
	}
	if err := m.Write(0x1003, []byte{0xCC}); err != nil {
		t.Fatalf("write: %v", err)
	}
	if _, ok := m.icfgSpills[0x1000]; ok {
		t.Fatal("fully-known spill should have been dropped")
	}
}

func TestMemUnknownOriginsPop(t *testing.T) {
	m := NewMemory()
	m.AddRegion(0x1000, 0x2000, "r")
	m.unknownOrigins[0x1000] = 0x2000
	m.unknownOrigins[0x1001] = 0x2001
	if err := m.Write(0x1000, []byte{1}); err != nil {
		t.Fatalf("write: %v", err)
	}
	if _, ok := m.unknownOrigins[0x1000]; ok {
		t.Fatal("overwritten unknown origin should be popped")
	}
	if _, ok := m.unknownOrigins[0x1001]; !ok {
		t.Fatal("unwritten unknown origin should survive")
	}
}

func TestMemErrorJSONMatchesPython(t *testing.T) {
	cases := []struct {
		setup func() *Memory
		act   func(*Memory) *MemError
		want  string
	}{
		{
			setup: func() *Memory {
				m := NewMemory()
				m.AddRegion(0x00123000, 0x00123840, "bootstrap_stack")
				return m
			},
			act:  func(m *Memory) *MemError { _, e := m.Read(0x00123000, 4); return e },
			want: `{"access":"read","address":"0x123000","first_missing":"0x123000","kind":"uninitialized_memory","region":"bootstrap_stack","size":4}`,
		},
		{
			setup: func() *Memory { return NewMemory() },
			act:   func(m *Memory) *MemError { _, e := m.Read(0xdead0000, 4); return e },
			want:  `{"access":"read","address":"0xdead0000","kind":"unmapped_memory","reference_sdk_register":null,"size":4}`,
		},
		{
			setup: func() *Memory { return NewMemory() },
			act:   func(m *Memory) *MemError { _, e := m.Read(PLLAddr, 4); return e },
			want:  `{"access":"read","address":"0xfd4600","kind":"unmapped_memory","reference_sdk_register":"JL_PLL0.CON0","size":4}`,
		},
		{
			setup: func() *Memory {
				m := NewMemory()
				m.SetFetch(true)
				return m
			},
			act:  func(m *Memory) *MemError { _, e := m.Read(0xdead0000, 4); return e },
			want: `{"access":"fetch","address":"0xdead0000","kind":"unmapped_instruction_fetch","reference_sdk_symbol":null,"size":4}`,
		},
		{
			setup: func() *Memory {
				m := NewMemory()
				m.SetFetch(true)
				return m
			},
			act:  func(m *Memory) *MemError { _, e := m.Read(pllInitSymbolAddr, 4); return e },
			want: `{"access":"fetch","address":"0x1fe14","kind":"unmapped_instruction_fetch","reference_sdk_symbol":"_pll_init","size":4}`,
		},
		{
			setup: func() *Memory { return NewMemory() },
			act:   func(m *Memory) *MemError { return m.Write(0xdead0000, []byte{1, 2, 3, 4}) },
			want:  `{"access":"write","address":"0xdead0000","kind":"unmapped_or_readonly_write","size":4}`,
		},
	}
	for i, tc := range cases {
		m := tc.setup()
		err := tc.act(m)
		if err == nil {
			t.Fatalf("case %d: expected an error", i)
		}
		if err.JSON() != tc.want {
			t.Fatalf("case %d JSON mismatch:\n got %s\nwant %s", i, err.JSON(), tc.want)
		}
	}
}
