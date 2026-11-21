package gosim

// flatMem is the sparse, page-based little-endian byte memory used by the CPU
// decode/effect core and the stage-1 segment contract. It is intentionally NOT
// the strict memory (see mem.go, type Memory): reads of unwritten pages return
// zeros, which is safe only because the segment contract never reads unwritten
// bytes — it fetches from a caller-supplied immutable ROM/flash window and runs
// only scalar ALU with no memory stores. The honest whole-loop memory lives in
// mem.go.

const pageShift = 12
const pageSize = 1 << pageShift

type flatMem struct {
	pages map[uint64][]byte
}

func newFlatMem() *flatMem {
	return &flatMem{pages: make(map[uint64][]byte)}
}

func (m *flatMem) read(addr uint32, n int) []byte {
	out := make([]byte, n)
	for i := 0; i < n; i++ {
		a := uint64(addr) + uint64(i)
		if p := m.pages[a>>pageShift]; p != nil {
			out[i] = p[a&(pageSize-1)]
		}
	}
	return out
}

func (m *flatMem) write(addr uint32, data []byte) {
	for i, b := range data {
		a := uint64(addr) + uint64(i)
		key := a >> pageShift
		p := m.pages[key]
		if p == nil {
			p = make([]byte, pageSize)
			m.pages[key] = p
		}
		p[a&(pageSize-1)] = b
	}
}

// wordLE reads a 32-bit little-endian word.
func (m *flatMem) wordLE(addr uint32) uint32 {
	b := m.read(addr, 4)
	return uint32(b[0]) | uint32(b[1])<<8 | uint32(b[2])<<16 | uint32(b[3])<<24
}

func (m *flatMem) writeWordLE(addr, v uint32) {
	m.write(addr, []byte{byte(v), byte(v >> 8), byte(v >> 16), byte(v >> 24)})
}
