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

// benchM4 builds the crc_bankb-like counting advance chain (core tick +
// watchdog + Timer0 + Timer2, no software IRQ model, no delivery requests).
func benchM4() *PeriphState {
	ct, _ := NewCoreTickStopped(u32p(0), i64p(4), false, false, false)
	ct.Write(8, 119999)
	ct.Write(4, 0)
	ct.Write(0, 0x1)
	wd, _ := NewWatchdogProjection([]int64{4, 1})
	wd.Write(WDTKey, WdtKeyFirst)
	wd.Write(WDTKey, WdtKeySecond)
	wd.Write(WDTCon, 0x10031) // feed + enable + time_code 17 (period 131072)
	t2 := NewTimer2Delay()
	t2.Write(Timer2+8, 200)
	t2.Write(Timer2+4, 1)
	t2.Write(Timer2, 0xc01)
	return &PeriphState{
		Timer:           benchTimer(true),
		IRQ:             NewIRQController(),
		CoreTicks:       [2]*CoreTickStopped{ct, nil},
		Watchdog:        wd,
		Timer2:          t2,
		SourceTicksPerStep: 4,
	}
}

// BenchmarkM4AdvanceCounting measures one full advance over the M4 counting
// models (the "counting but no delivery" case the whole-loop closure now pays).
func BenchmarkM4AdvanceCounting(b *testing.B) {
	p := benchM4()
	b.ReportAllocs()
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		_ = p.Advance(0x300)
	}
}
