package gosim

// Sparse, page-based little-endian byte memory with the same zero-padding
// semantics as the upstream sim.Memory: pages are created lazily, reads outside
// any written page return zeros, writes extend the map. A multi-byte access
// extends linearly past the 32-bit boundary exactly as the Python reference
// does (the page index is an unbounded integer; only the start address is
// masked by the callers).

const pageShift = 12
const pageSize = 1 << pageShift

type Memory struct {
	pages map[uint64][]byte
}

func NewMemory() *Memory {
	return &Memory{pages: make(map[uint64][]byte)}
}

func (m *Memory) read(addr uint32, n int) []byte {
	out := make([]byte, n)
	for i := 0; i < n; i++ {
		a := uint64(addr) + uint64(i)
		if p := m.pages[a>>pageShift]; p != nil {
			out[i] = p[a&(pageSize-1)]
		}
	}
	return out
}

func (m *Memory) write(addr uint32, data []byte) {
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
func (m *Memory) wordLE(addr uint32) uint32 {
	b := m.read(addr, 4)
	return uint32(b[0]) | uint32(b[1])<<8 | uint32(b[2])<<16 | uint32(b[3])<<24
}

func (m *Memory) writeWordLE(addr, v uint32) {
	m.write(addr, []byte{byte(v), byte(v >> 8), byte(v >> 16), byte(v >> 24)})
}
