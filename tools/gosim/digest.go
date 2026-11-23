package gosim

import (
	"crypto/sha256"
	"encoding/hex"
	"hash"
	"strconv"
	"strings"
)

// Digest is the whole-run trace SHA-256 accumulator — the Go twin of
// gosim_measure._digest_trace, which does h.update(repr(row).encode('utf-8'))
// over every committed trace row in global completion order with no separator
// between rows. Two producers feed the one hash state:
//
//   - Go commits ordinary completed steps and hashes their repr bytes here
//     (digestSteps), in commit order;
//   - Python feeds the slow / boundary steps' reference bytes (repr(row)) into
//     the same state via the gosim_world_digest_feed cgo export, in global
//     completion order, so the accumulated digest equals _digest_trace.
//
// A nil Digest on World disables the accumulator (zero overhead, zero behavior
// change); opt-in per-step debug capture (steps != nil) keeps every hashed
// row's bytes for a line-by-line comparison that reports the first mismatch.
type Digest struct {
	h     hash.Hash
	count int64
	steps [][]byte // nil = capture disabled; non-nil = append each row's bytes
}

// NewDigest returns a fresh accumulator; debug enables per-step byte capture.
func NewDigest(debug bool) *Digest {
	d := &Digest{h: sha256.New()}
	if debug {
		d.steps = [][]byte{}
	}
	return d
}

// Reset clears the hash state, row count, and (if enabled) the debug capture.
func (d *Digest) Reset() {
	d.h = sha256.New()
	d.count = 0
	if d.steps != nil {
		d.steps = d.steps[:0]
	}
}

// Feed appends one step's already-rendered repr bytes (the Python slow-path
// reference) to the hash state and advances the row count.
func (d *Digest) Feed(b []byte) {
	d.h.Write(b)
	d.count++
	if d.steps != nil {
		d.steps = append(d.steps, append([]byte(nil), b...))
	}
}

// Count returns the number of hashed rows.
func (d *Digest) Count() int64 { return d.count }

// Hex returns the current lowercase hex digest.
func (d *Digest) Hex() string { return hex.EncodeToString(d.h.Sum(nil)) }

// StepLen returns the byte length of the captured step i, or -1 when capture
// is disabled or i is out of range.
func (d *Digest) StepLen(i int) int {
	if d.steps == nil || i < 0 || i >= len(d.steps) {
		return -1
	}
	return len(d.steps[i])
}

// StepBytes returns the captured bytes of step i, or nil when capture is
// disabled or i is out of range.
func (d *Digest) StepBytes(i int) []byte {
	if d.steps == nil || i < 0 || i >= len(d.steps) {
		return nil
	}
	return d.steps[i]
}

// stepBytes renders one committed step's Python repr bytes — the 6-tuple
// (core, before_pc, next_pc, flags-str, 16 register-signature strs, writes
// tuple) built by TraceMachine.after_step — byte-identical to repr(row).
func (w *World) stepBytes(s *LoopStep) []byte {
	var b strings.Builder
	b.WriteByte('(')
	b.WriteString(strconv.Itoa(s.Core))
	b.WriteString(", ")
	b.WriteString(strconv.FormatUint(uint64(s.PC), 10))
	b.WriteString(", ")
	b.WriteString(strconv.FormatUint(uint64(s.NextPC), 10))
	b.WriteString(", '")
	b.WriteString(py02x(uint32(s.Flags)))
	b.WriteString("', (")
	for i := 0; i < 16; i++ {
		if i > 0 {
			b.WriteString(", ")
		}
		b.WriteByte('\'')
		b.WriteString(regSig(s.R[i], s.RT[i]))
		b.WriteByte('\'')
	}
	b.WriteString("), ")
	// Writes tuple: concrete RAM stores only. Abstract stores (Abs != nil) are
	// replayed via Memory.write_abstract_word (an event, never pending_writes),
	// and MMIO-routed stores return early inside Python Memory.write via
	// hardware.handles / handles_byte_write (no note_memory_write) — both never
	// enter the trace row's writes tuple.
	b.WriteByte('(')
	nw := 0
	for _, mw := range s.Mem {
		if mw.Abs != nil {
			continue
		}
		physical := w.writePhysicalAddr(mw)
		if w.Mem != nil && (w.Mem.HandlesMMIO(physical, len(mw.Bytes)) ||
			(len(mw.Bytes) == 1 && w.Mem.HandlesByteWrite(physical))) {
			continue
		}
		if nw > 0 {
			b.WriteString(", ")
		}
		b.WriteByte('(')
		b.WriteString(strconv.FormatUint(uint64(physical), 10))
		b.WriteString(", '")
		b.WriteString(hex.EncodeToString(mw.Bytes))
		b.WriteString("')")
		nw++
	}
	if nw == 1 {
		b.WriteByte(',') // Python single-element tuple trailing comma
	}
	b.WriteByte(')')
	b.WriteByte(')')
	return []byte(b.String())
}

// writePhysicalAddr returns the physical address Python's note_memory_write
// records for a concrete store: the MMU translation Python's Memory.write
// performs on replay (identity outside the virtual window, page-table walk
// inside it). The replay runs after the fill, so the digest is computed in the
// same post-fill world state — this matches Python's replay timing exactly.
func (w *World) writePhysicalAddr(mw LoopMemWrite) uint32 {
	if w.Mem == nil {
		return mw.Addr
	}
	p, err := w.Mem.translate(mw.Addr, len(mw.Bytes), false)
	if err != nil {
		return mw.Addr // committed store implies the replay translation succeeds
	}
	return p
}

// digestSteps hashes a fill's committed steps in commit order (the global
// completion order for the Go-committed prefix).
func (w *World) DigestSteps(steps []LoopStep) {
	if w.Digest == nil {
		return
	}
	for i := range steps {
		w.Digest.Feed(w.stepBytes(&steps[i]))
	}
}

// regSig formats one register's signature exactly like Python
// _register_signature (tools/analyze_image_trace.py): a concrete int becomes
// f'{v:08x}', an UnknownWord becomes u<bytes>|<origins>, an IcfgValue becomes
// i<known:08x>.<value:08x>, and anything else becomes "?". A nil RegTag means
// R[i] is the whole truth (concrete int); a non-nil tag rides the abstract
// value in the side table. Bools cannot arise here: R is [16]uint32.
func regSig(v uint32, tag *RegTag) string {
	if tag == nil {
		return py08x(int64(v))
	}
	switch tag.Kind {
	case "unknown_word":
		var b strings.Builder
		b.Grow(64)
		b.WriteByte('u')
		for i := 0; i < 4; i++ {
			if i < len(tag.Bytes) && tag.Bytes[i] != nil {
				b.WriteString(py02x(uint32(*tag.Bytes[i])))
			} else {
				b.WriteString("??")
			}
		}
		b.WriteByte('|')
		for i := 0; i < 4; i++ {
			if i > 0 {
				b.WriteByte(',')
			}
			if i < len(tag.Origins) && tag.Origins[i] != nil {
				b.WriteString("0x")
				b.WriteString(strconv.FormatUint(uint64(*tag.Origins[i]), 16))
			} else {
				b.WriteByte('-')
			}
		}
		return b.String()
	case "icfg_value":
		return "i" + py08x(int64(tag.Known)) + "." + py08x(int64(tag.Value))
	}
	return "?"
}
