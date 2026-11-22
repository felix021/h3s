package gosim

// Strict memory, byte-for-byte aligned with the Python probe_boot.Memory
// semantics (docs/gosim-wholeloop-design.md §5). This is the M2 port: region
// table + ROM/app/readonly/handoff mirrors, MMIO routing hook, uninitialized
// byte sentinel (a missing ram entry is "uninitialized", never a fabricated
// zero), unmapped vs uninitialized distinction on the read and fetch paths,
// ROM write rejection, icfg_spills overlay + unknown_origins provenance, and
// write observation events.
//
// The toy page memory the CPU decode/effect core used before lives in
// flatmem.go (type flatMem); it stays there for the stage-1 segment contract,
// which is proven to never read unwritten bytes. This Memory is the honest
// whole-loop memory the World will run on from M3.
//
// Red line: a read of an unwritten byte must raise uninitialized_memory with
// the first_missing byte, and a read outside any region must raise
// unmapped_memory / unmapped_instruction_fetch. Never return zero.

import (
	"encoding/json"
	"fmt"
)

const (
	// PLLAddr is the PLL0.CON0 fixture address Python Memory special-cases.
	PLLAddr = 0x00fd4600
	// pllInitSymbolAddr is the ROM _pll_init symbol referenced in the
	// unmapped-instruction-fetch details.
	pllInitSymbolAddr = 0x1fe14
	// wordMask is the fully-known 32-bit mask (Python WORD_MASK / br28_icfg.M).
	wordMask = 0xffffffff
)

// Bus is the MMIO routing hook. It is an interface placeholder for M2: the
// peripheral models (Timer0, ICU, ...) implement it from M3 on. A nil bus means
// no hardware is present, so every MMIO address falls through to the strict
// unmapped/uninitialized paths exactly as the Python Memory does with
// hardware=None.
type Bus interface {
	Handles(addr uint32, size int) bool
	Read(addr uint32) uint32
	// ReadBytes returns a byte-granular fixture, or nil when there is none.
	ReadBytes(addr uint32, size int) []byte
	Write(addr uint32, value uint32)
	HandlesByteWrite(addr uint32) bool
	WriteByteAt(addr uint32, b byte)
}

// Region is one writable RAM region: [Lo, Hi) with a kind name.
type Region struct {
	Lo, Hi uint32
	Kind   string
}

// ReadonlyMirror is a read-only image at [Base, Base+len(Data)).
type ReadonlyMirror struct {
	Base uint32
	Data []byte
}

// IcfgSpill is a partial-ICFG spilled word. Known and Identity are 32-bit byte
// masks in the Python IcfgValue layout: bit (8*i)..(8*i+7) belong to byte i.
// Origin is the (core, status_generation) provenance br28_icfg.IcfgValue keeps;
// it must survive a spill -> reload so a reloaded value can still be written
// back to the same core's ICFG.
type IcfgSpill struct {
	Value    uint32
	Known    uint32
	Identity uint32
	Origin   *[2]int
}

// overlayBytes writes concrete bytes at offset into the spill, marking the
// written bytes known (identity cleared), exactly like Python
// IcfgValue.overlay_bytes. It returns a new spill; the caller keeps or drops the
// old one depending on whether the word is now fully known.
func (s *IcfgSpill) overlayBytes(offset int, data []byte) *IcfgSpill {
	value, known, identity := s.Value, s.Known, s.Identity
	for i, b := range data {
		shift := uint(8 * (offset + i))
		mask := uint32(0xff) << shift
		value = (value &^ mask) | (uint32(b) << shift)
		known |= mask
		identity &^= mask
	}
	return &IcfgSpill{Value: value, Known: known, Identity: identity, Origin: s.Origin}
}

// WriteEvent is one observed write (mirror of Python
// event({'access':'write', 'address':..., 'bytes':..., 'region':...})).
type WriteEvent struct {
	Address uint32
	Bytes   []byte
	Region  string
}

// MemError is a strict-memory stop (mirror of Python Blocked).
type MemError struct {
	Kind         string
	Access       string // "read", "write", or "fetch"
	Address      uint32
	Size         int
	Region       string // uninitialized_memory only
	FirstMissing uint32 // uninitialized_memory only
}

func (e *MemError) Error() string { return e.JSON() }

// JSON serializes the details exactly like Python Blocked.__str__
// (json.dumps(self.details, sort_keys=True)): hex-string address/first_missing,
// int size, and the reference_sdk_* keys present with null when not applicable.
func (e *MemError) JSON() string {
	m := map[string]any{
		"kind":    e.Kind,
		"access":  e.Access,
		"address": fmt.Sprintf("0x%x", e.Address),
		"size":    e.Size,
	}
	switch e.Kind {
	case "uninitialized_memory":
		m["region"] = e.Region
		m["first_missing"] = fmt.Sprintf("0x%x", e.FirstMissing)
	case "unmapped_memory":
		if e.Address == PLLAddr {
			m["reference_sdk_register"] = "JL_PLL0.CON0"
		} else {
			m["reference_sdk_register"] = nil
		}
	case "unmapped_instruction_fetch":
		if e.Address == pllInitSymbolAddr {
			m["reference_sdk_symbol"] = "_pll_init"
		} else {
			m["reference_sdk_symbol"] = nil
		}
	}
	b, _ := json.Marshal(m)
	return string(b)
}

// Memory is the strict whole-loop memory.
type Memory struct {
	regions  []Region
	rom      []byte // nil = absent
	app      []byte
	entry    uint32
	readonly []ReadonlyMirror
	pll      *uint32 // nil = absent
	bus      Bus
	fetch    bool // operation == 'fetch'

	ram            map[uint32]byte
	unknownOrigins map[uint32]uint32
	icfgSpills     map[uint32]*IcfgSpill
	writes         []WriteEvent

	// OnMemoryWrite mirrors machine.note_memory_write; the M3+ ledger wires it.
	OnMemoryWrite func(addr uint32, data []byte)
}

// NewMemory returns an empty strict memory (no regions, no mirrors, no bus).
func NewMemory() *Memory {
	return &Memory{
		ram:            make(map[uint32]byte),
		unknownOrigins: make(map[uint32]uint32),
		icfgSpills:     make(map[uint32]*IcfgSpill),
	}
}

// ---- configuration (mirrors the Python explore closure inputs) --------------

func (m *Memory) SetROM(b []byte)               { m.rom = b }
func (m *Memory) SetApp(entry uint32, b []byte) { m.entry, m.app = entry, b }
func (m *Memory) AddRegion(lo, hi uint32, kind string) {
	m.regions = append(m.regions, Region{lo, hi, kind})
}
func (m *Memory) AddReadonly(base uint32, b []byte) {
	m.readonly = append(m.readonly, ReadonlyMirror{base, b})
}
func (m *Memory) SetPLL(v uint32, present bool) {
	if present {
		m.pll = &v
	} else {
		m.pll = nil
	}
}
func (m *Memory) SetBus(b Bus)         { m.bus = b }
func (m *Memory) SetFetch(f bool)      { m.fetch = f }
func (m *Memory) Writes() []WriteEvent { return m.writes }

// ReplaceRAM installs a whole known-byte map (the Python mem.ram snapshot)
// without any guest-write side effect: no events, no OnMemoryWrite, no
// icfg-spill overlay, no unknown-origin pops. It is the initial import and the
// per-fill re-sync path; a byte that Python marks unknown is simply absent from
// the new map, so the strict read falls back to uninitialized exactly as
// Python does.
func (m *Memory) ReplaceRAM(entries map[uint32]byte) {
	m.ram = entries
}

// ImportRAMBytes bulk-fills known bytes (additive; used by the delta sync when
// only newly-known bytes are pushed). A byte is never removed by this path.
func (m *Memory) ImportRAMBytes(entries map[uint32]byte) {
	for a, b := range entries {
		m.ram[a] = b
	}
}

// RemoveRAMBytes marks bytes unknown (Python ram.pop) so a later strict read
// faults uninitialized. No side effects beyond the byte removal.
func (m *Memory) RemoveRAMBytes(addrs []uint32) {
	for _, a := range addrs {
		delete(m.ram, a)
	}
}

// ReplaceUnknownOrigins installs a whole unknown_origins map (no side effects).
func (m *Memory) ReplaceUnknownOrigins(entries map[uint32]uint32) {
	m.unknownOrigins = entries
}

// ReplaceIcfgSpills installs a whole icfg_spills map (no side effects).
func (m *Memory) ReplaceIcfgSpills(entries map[uint32]*IcfgSpill) {
	m.icfgSpills = entries
}

// HasIcfgSpills reports whether any partial-ICFG spill words are resident.
// While any spill exists the dual loop must not execute scalar ld/st (Python's
// partial_icfg reload/spill adapters would observe an abstract value).
func (m *Memory) HasIcfgSpills() bool { return len(m.icfgSpills) > 0 }

// HasUnknownOrigins reports whether any abstract byte provenance is resident.
func (m *Memory) HasUnknownOrigins() bool { return len(m.unknownOrigins) > 0 }

// Fetch reads n bytes with fetch access semantics (MMIO/PLL routes disabled,
// unmapped reported as unmapped_instruction_fetch). It is the instruction-fetch
// entry the strict decode path uses; the m.fetch flag is scoped to the call.
func (m *Memory) Fetch(addr uint32, n int) ([]byte, *MemError) {
	m.fetch = true
	b, err := m.Read(addr, n)
	m.fetch = false
	return b, err
}

// ROM returns the ROM image (nil when absent), so the strict decode path can
// apply the ROM-tail short read.
func (m *Memory) ROM() []byte { return m.rom }

// region returns the kind of the first region that fully contains the request,
// or "" (Python returns None).
func (m *Memory) region(addr uint32, size int) string {
	ua, un := uint64(addr), uint64(size)
	for _, r := range m.regions {
		if r.Lo <= addr && ua+un <= uint64(r.Hi) {
			return r.Kind
		}
	}
	return ""
}

func (m *Memory) access() string {
	if m.fetch {
		return "fetch"
	}
	return "read"
}

// Read mirrors Python Memory.read, in the exact dispatch order: validate,
// app mirror, ROM mirror, readonly mirrors, MMIO word route, byte-granular
// fixture route, PLL fixture, region ram (first uninitialized byte stops), and
// finally unmapped (fetch vs read distinction).
func (m *Memory) Read(addr uint32, n int) ([]byte, *MemError) {
	if n <= 0 || uint64(addr)+uint64(n) > 0x100000000 {
		return nil, &MemError{Kind: "invalid_memory_request", Access: m.access(), Address: addr, Size: n}
	}
	ua, un := uint64(addr), uint64(n)
	// app mirror: ENTRY <= address and address+size <= ENTRY+len(app)
	if m.entry <= addr && ua+un <= uint64(m.entry)+uint64(len(m.app)) {
		s := int(addr - m.entry)
		return append([]byte(nil), m.app[s:s+n]...), nil
	}
	// ROM mirror: full containment inside [0, len(rom))
	if m.rom != nil && ua+un <= uint64(len(m.rom)) {
		return append([]byte(nil), m.rom[addr:ua+un]...), nil
	}
	// handoff readonly mirrors
	for _, r := range m.readonly {
		if uint64(r.Base) <= ua && ua+un <= uint64(r.Base)+uint64(len(r.Data)) {
			s := int(addr - r.Base)
			return append([]byte(nil), r.Data[s:s+n]...), nil
		}
	}
	// MMIO word route (data reads only, never fetch)
	if m.bus != nil && !m.fetch && m.bus.Handles(addr, n) {
		v := m.bus.Read(addr)
		return []byte{byte(v), byte(v >> 8), byte(v >> 16), byte(v >> 24)}, nil
	}
	// byte-granular fixture route
	if m.bus != nil && !m.fetch {
		if d := m.bus.ReadBytes(addr, n); d != nil {
			return append([]byte(nil), d...), nil
		}
	}
	// PLL fixture (data reads only)
	if !m.fetch && addr == PLLAddr && n == 4 && m.pll != nil {
		v := *m.pll
		return []byte{byte(v), byte(v >> 8), byte(v >> 16), byte(v >> 24)}, nil
	}
	if kind := m.region(addr, n); kind != "" {
		out := make([]byte, n)
		for i := 0; i < n; i++ {
			a := addr + uint32(i)
			b, ok := m.ram[a]
			if !ok {
				return nil, &MemError{
					Kind: "uninitialized_memory", Access: m.access(), Address: addr, Size: n,
					Region: kind, FirstMissing: a,
				}
			}
			out[i] = b
		}
		return out, nil
	}
	if m.fetch {
		return nil, &MemError{Kind: "unmapped_instruction_fetch", Access: "fetch", Address: addr, Size: n}
	}
	return nil, &MemError{Kind: "unmapped_memory", Access: "read", Address: addr, Size: n}
}

// Write mirrors Python Memory.write: ROM partial-overlap rejection, MMIO word
// route, byte-granular MMIO route, region check (else unmapped_or_readonly
// write), icfg_spills overlay, ram update, unknown_origins pop, observer hook,
// and the write observation event.
func (m *Memory) Write(addr uint32, data []byte) *MemError {
	n := len(data)
	ua, un := uint64(addr), uint64(n)
	// ROM write rejection: any overlap with the ROM window is readonly.
	if m.rom != nil && ua < uint64(len(m.rom)) && ua+un > 0 {
		return &MemError{Kind: "unmapped_or_readonly_write", Access: "write", Address: addr, Size: n}
	}
	// MMIO word route
	if m.bus != nil && m.bus.Handles(addr, n) {
		m.bus.Write(addr, leUint32(data))
		return nil
	}
	// byte-granular MMIO route (single-byte stores to modeled byte registers)
	if m.bus != nil && n == 1 && m.bus.HandlesByteWrite(addr) {
		m.bus.WriteByteAt(addr, data[0])
		return nil
	}
	kind := m.region(addr, n)
	if kind == "" {
		return &MemError{Kind: "unmapped_or_readonly_write", Access: "write", Address: addr, Size: n}
	}
	// icfg_spills overlay: a concrete write marks the overlapped spilled bytes
	// known; a spill that becomes fully known is dropped.
	for base, sp := range m.icfgSpills {
		baseU := uint64(base)
		loU := maxU64(baseU, ua)
		hiU := minU64(baseU+4, ua+un)
		if loU < hiU {
			updated := sp.overlayBytes(int(loU-baseU), data[loU-ua:hiU-ua])
			if updated.Known == wordMask {
				delete(m.icfgSpills, base)
			} else {
				m.icfgSpills[base] = updated
			}
		}
	}
	for i, b := range data {
		a := addr + uint32(i)
		m.ram[a] = b
		delete(m.unknownOrigins, a)
	}
	if m.OnMemoryWrite != nil {
		m.OnMemoryWrite(addr, data)
	}
	m.writes = append(m.writes, WriteEvent{Address: addr, Bytes: append([]byte(nil), data...), Region: kind})
	return nil
}

func leUint32(data []byte) uint32 {
	var v uint32
	for i, b := range data {
		if i >= 4 {
			break
		}
		v |= uint32(b) << (8 * i)
	}
	return v
}

func maxU64(a, b uint64) uint64 {
	if a > b {
		return a
	}
	return b
}

func minU64(a, b uint64) uint64 {
	if a < b {
		return a
	}
	return b
}
