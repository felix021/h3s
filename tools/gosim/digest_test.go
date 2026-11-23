package gosim

import "testing"

func ptr8(v uint8) *uint8    { return &v }
func ptr32(v uint32) *uint32 { return &v }

func TestRegSigConcrete(t *testing.T) {
	if got := regSig(1, nil); got != "00000001" {
		t.Fatalf("concrete 1: got %q", got)
	}
	if got := regSig(0, nil); got != "00000000" {
		t.Fatalf("concrete 0: got %q", got)
	}
	if got := regSig(0xffffffff, nil); got != "ffffffff" {
		t.Fatalf("concrete max: got %q", got)
	}
}

func TestRegSigUnknownWord(t *testing.T) {
	tag := &RegTag{
		Kind:    "unknown_word",
		Bytes:   []*uint8{ptr8(1), ptr8(2), nil, ptr8(4)},
		Origins: []*uint32{nil, nil, ptr32(1), nil},
	}
	// bytes: 01 02 ?? 04 ; origins: - - 0x1 -
	if got := regSig(0, tag); got != "u0102??04|-,-,0x1,-" {
		t.Fatalf("unknown_word: got %q", got)
	}
}

func TestRegSigIcfgValue(t *testing.T) {
	tag := &RegTag{Kind: "icfg_value", Known: 1, Value: 2}
	if got := regSig(0, tag); got != "i00000001.00000002" {
		t.Fatalf("icfg_value: got %q", got)
	}
}

func TestStepBytesEmptyWrites(t *testing.T) {
	w := &World{}
	var R [16]uint32
	for i := 1; i <= 16; i++ {
		R[i-1] = uint32(i)
	}
	s := &LoopStep{Core: 0, PC: 4594852, NextPC: 4594856, Flags: 1, R: R}
	want := "(0, 4594852, 4594856, '01', ('00000001', '00000002', '00000003', '00000004', " +
		"'00000005', '00000006', '00000007', '00000008', '00000009', '0000000a', '0000000b', " +
		"'0000000c', '0000000d', '0000000e', '0000000f', '00000010'), ())"
	if got := string(w.stepBytes(s)); got != want {
		t.Fatalf("empty writes:\n got  %s\n want %s", got, want)
	}
}

func TestStepBytesSingleAndMultiWrites(t *testing.T) {
	w := &World{}
	var R [16]uint32
	s := &LoopStep{Core: 1, PC: 100, NextPC: 104, Flags: 0xff, R: R,
		Mem: []LoopMemWrite{{Addr: 1234, Bytes: []byte{0xaa, 0xbb}}}}
	want := "(1, 100, 104, 'ff', ('00000000', '00000000', '00000000', '00000000', " +
		"'00000000', '00000000', '00000000', '00000000', '00000000', '00000000', '00000000', " +
		"'00000000', '00000000', '00000000', '00000000', '00000000'), ((1234, 'aabb'),))"
	if got := string(w.stepBytes(s)); got != want {
		t.Fatalf("single write:\n got  %s\n want %s", got, want)
	}

	s.Mem = []LoopMemWrite{
		{Addr: 7, Bytes: []byte{0x00}},
		{Addr: 8, Bytes: []byte{0xff}},
		{Addr: 9, Bytes: []byte{0x12, 0x34}},
	}
	want = "(1, 100, 104, 'ff', ('00000000', '00000000', '00000000', '00000000', " +
		"'00000000', '00000000', '00000000', '00000000', '00000000', '00000000', '00000000', " +
		"'00000000', '00000000', '00000000', '00000000', '00000000'), " +
		"((7, '00'), (8, 'ff'), (9, '1234')))"
	if got := string(w.stepBytes(s)); got != want {
		t.Fatalf("multi write:\n got  %s\n want %s", got, want)
	}
}

func TestStepBytesAbstractWriteSkipped(t *testing.T) {
	w := &World{}
	var R [16]uint32
	abs := &RegTag{Kind: "icfg_value", Known: 0xff, Value: 0x12}
	s := &LoopStep{Core: 0, PC: 0, NextPC: 4, Flags: 0, R: R,
		Mem: []LoopMemWrite{{Addr: 0x100, Abs: abs}}}
	want := "(0, 0, 4, '00', ('00000000', '00000000', '00000000', '00000000', " +
		"'00000000', '00000000', '00000000', '00000000', '00000000', '00000000', '00000000', " +
		"'00000000', '00000000', '00000000', '00000000', '00000000'), ())"
	if got := string(w.stepBytes(s)); got != want {
		t.Fatalf("abstract write skip:\n got  %s\n want %s", got, want)
	}
}

// fakeBus is a minimal Bus that models a write at one pinned MMIO word so the
// digest can be pinned to skip MMIO-routed stores exactly as Python's
// Memory.write returns early at hardware.handles.
type digestFakeBus struct{ mmio uint32 }

func (f *digestFakeBus) Handles(addr uint32, size int) bool { return addr == f.mmio && size == 4 }
func (f *digestFakeBus) HandlesRead(addr uint32, size int) bool {
	return false
}
func (f *digestFakeBus) Read(addr uint32) (uint32, *MemError)          { return 0, nil }
func (f *digestFakeBus) ReadBytes(addr uint32, size int) []byte        { return nil }
func (f *digestFakeBus) Write(addr uint32, value uint32) *MemError     { return nil }
func (f *digestFakeBus) HandlesByteWrite(addr uint32) bool             { return false }
func (f *digestFakeBus) WriteByteAt(addr uint32, b byte) *MemError     { return nil }

func TestStepBytesMMIOWriteSkipped(t *testing.T) {
	w := &World{Mem: NewMemory()}
	w.Mem.SetBus(&digestFakeBus{mmio: 0xffe1a8})
	var R [16]uint32
	s := &LoopStep{Core: 0, PC: 0, NextPC: 4, Flags: 0, R: R,
		Mem: []LoopMemWrite{
			{Addr: 0xffe1a8, Bytes: []byte{0x06, 0x00, 0x00, 0x00}}, // MMIO word route: skipped
			{Addr: 0x100, Bytes: []byte{0xaa}},                       // RAM store: kept
		}}
	want := "(0, 0, 4, '00', ('00000000', '00000000', '00000000', '00000000', " +
		"'00000000', '00000000', '00000000', '00000000', '00000000', '00000000', '00000000', " +
		"'00000000', '00000000', '00000000', '00000000', '00000000'), ((256, 'aa'),))"
	if got := string(w.stepBytes(s)); got != want {
		t.Fatalf("mmio write skip:\n got  %s\n want %s", got, want)
	}
}
