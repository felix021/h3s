package gosim

import "testing"

// M3 advance-phase hot-path benchmarks (cold, no cache reuse across iterations).
// These quantify the per-attempt cost of the peripheral work the whole-loop
// closure either skips (empty advance) or pays (counting advance), plus the
// static no-op judgment that gates the closure.

func benchTimer(counting bool) *Timer0 {
	t := NewTimer0()
	t.Write(8, 119999)
	t.Write(4, 0)
	if counting {
		t.Write(0, 0x1001) // mode 1, source 4, div 0
	} else {
		t.Write(0, 0x1000) // stopped
	}
	return t
}

// BenchmarkTimer0AdvanceEmpty measures one advance() on a stopped timer — the
// "no timer counting" case the whole-loop closure skips.
func BenchmarkTimer0AdvanceEmpty(b *testing.B) {
	t := benchTimer(false)
	b.ReportAllocs()
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		_ = t.Advance(4)
	}
}

// BenchmarkTimer0AdvanceCounting measures one advance() on a running timer.
func BenchmarkTimer0AdvanceCounting(b *testing.B) {
	t := benchTimer(true)
	b.ReportAllocs()
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		_ = t.Advance(4)
	}
}

// BenchmarkCanProveAdvanceNoop measures the static advance-empty judgment — the
// new loop-level predicate M3 adds in front of the execute phase.
func BenchmarkCanProveAdvanceNoop(b *testing.B) {
	p := &PeriphState{Timer: NewTimer0()}
	b.ReportAllocs()
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		_, _ = p.CanProveAdvanceNoop()
	}
}
