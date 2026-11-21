package gosim

// decodeIndex is a first-word lookup that eliminates the linear scan over
// decodeTable in the cold-decode path.
//
// decodeOne assembles w from the three 16-bit words of a fetch window as
//   w = w0<<32 | w1<<16 | w2   (then truncates to nwords*16 bits per entry),
// so the FIRST word in memory (w0 = b[0] | b[1]<<8) always sits in the most
// significant 16-bit chunk of the mask/value encoding. For pi32v2 that chunk is
// exactly where the opcode lives (a 1-word instruction's whole word, a 2-word
// instruction's opcode word, a 3-word instruction's opcode word).
//
// decodeIndex[w0] therefore lists every table entry whose first-word mask could
// match w0. It is built once at init by expanding the don't-care bits of each
// entry's first-word mask; a cold decode then does a single array index plus a
// full mask/value check on the (small) candidate list, instead of scanning all
// 1165 entries. Bucket slices are appended in table order, so the first match
// in a bucket is the same first match the linear scan would have returned.
var decodeIndex = buildDecodeIndex()

func buildDecodeIndex() [65536][]uint16 {
	var idx [65536][]uint16
	for i := range decodeTable {
		im := &decodeTable[i]
		sh := 16 * (im.nwords - 1)
		m := uint16(im.mask>>sh) & 0xffff
		v := uint16(im.value>>sh) & 0xffff
		var free [16]uint8
		nf := 0
		for b := 0; b < 16; b++ {
			if m>>b&1 == 0 {
				free[nf] = uint8(b)
				nf++
			}
		}
		for combo := 0; combo < 1<<nf; combo++ {
			w := v
			for j := 0; j < nf; j++ {
				if combo>>j&1 != 0 {
					w |= 1 << free[j]
				}
			}
			idx[w] = append(idx[w], uint16(i))
		}
	}
	return idx
}
