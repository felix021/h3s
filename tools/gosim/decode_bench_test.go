package gosim

import "testing"

// encodeBase returns the nwords*2 bytes of an instruction's base encoding (its
// `value` with operand/don't-care bits at zero), little-endian.
func encodeBase(im *insnMeta) []byte {
	// decodeOne reads a fixed 6-byte window, so pad short instructions with
	// zeros exactly as the Python reference (_read6) does. The table's
	// mask/value store word 0 (the first word in memory) in the most
	// significant chunk, so word w of the memory encoding is
	// value >> (16*(nwords-1-w)), each word emitted little-endian.
	nw := im.nwords
	out := make([]byte, 6)
	for w := 0; w < nw; w++ {
		word := uint16(im.value >> (16 * (nw - 1 - w)))
		out[2*w] = byte(word)
		out[2*w+1] = byte(word >> 8)
	}
	return out
}

// BenchmarkDecodeOneCold measures the cold-decode path (no per-pc decode cache)
// over every instruction's base encoding. It is the direct cost of decodeOne's
// mask-matching loop (linear scan before the opcode-word index, bucket lookup
// after), with zero cache reuse between iterations.
func BenchmarkDecodeOneCold(b *testing.B) {
	encs := make([][]byte, len(decodeTable))
	for i := range decodeTable {
		encs[i] = encodeBase(&decodeTable[i])
	}
	b.ReportAllocs()
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		if _, _, _, ok := decodeOne(decodeTable, encs[i%len(encs)]); !ok {
			b.Fatalf("base encoding %d failed to decode", i%len(encs))
		}
	}
}

// BenchmarkDecodeOneColdMiss measures decodeOne on bytes that match no
// instruction at all — the worst case for the linear scan, which must then
// exhaust the whole table.
func BenchmarkDecodeOneColdMiss(b *testing.B) {
	// 0x000000000000 is a real instruction (nop); 0xFFFFFFFFFFFF matches none.
	miss := []byte{0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF}
	b.ReportAllocs()
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		if _, _, _, ok := decodeOne(decodeTable, miss); ok {
			b.Fatal("miss bytes unexpectedly decoded")
		}
	}
}
