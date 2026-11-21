package gosim

import "testing"

// The byte-for-byte differential test test_m4_consistency.py drives the Go
// models against the real Python br28_model classes over targeted + randomized
// sequences. These Go tests pin the strict-stop JSON (matching Python Blocked
// details) and a handful of semantics that don't need the ctypes round-trip.

func m4ptr32(v uint32) *uint32 { return &v }
func m4ptr64(v int64) *int64   { return &v }

func TestM4PeriphErrorJSON(t *testing.T) {
	cases := []struct {
		name string
		act  func() *PeriphError
		want string
	}{
		{
			"coretick read uninitialized",
			func() *PeriphError {
				ct, _ := NewCoreTickStopped(nil, nil, false, false, false)
				_, e := ct.Read(0)
				return e
			},
			`{"kind":"unknown_core_tick_state"}`,
		},
		{
			"coretick control not modeled",
			func() *PeriphError {
				ct, _ := NewCoreTickStopped(nil, nil, false, false, false)
				return peFromErr(ct.Write(0, 0x2))
			},
			`{"kind":"core_tick_control_not_modeled","value":"0x2"}`,
		},
		{
			"coretick enable not modeled",
			func() *PeriphError {
				ct, _ := NewCoreTickStopped(m4ptr32(0), nil, false, false, false)
				return peFromErr(ct.Write(0, 0x1))
			},
			`{"kind":"core_tick_enable_not_modeled","value":"0x1"}`,
		},
		{
			"coretick rmw not modeled",
			func() *PeriphError {
				ct, _ := NewCoreTickStopped(nil, nil, false, false, false)
				return ct.AckRmw("and", 0x40)
			},
			`{"kind":"core_tick_rmw_not_modeled","operand":"0x40","operation":"and"}`,
		},
		{
			"watchdog uninitialized control",
			func() *PeriphError {
				w, _ := NewWatchdogProjection(nil)
				_, e := w.Read(WDTCon)
				return e
			},
			`{"kind":"uninitialized_watchdog_control"}`,
		},
		{
			"watchdog key sequence",
			func() *PeriphError {
				w, _ := NewWatchdogProjection(nil)
				return peFromErr(w.Write(WDTKey, 0))
			},
			`{"kind":"watchdog_key_sequence_not_modeled","phase":0,"value":"0x0"}`,
		},
		{
			"watchdog command without keys",
			func() *PeriphError {
				w, _ := NewWatchdogProjection(nil)
				return peFromErr(w.Write(WDTCon, 0))
			},
			`{"kind":"watchdog_command_without_observed_key_pair"}`,
		},
		{
			"icu unknown initial control",
			func() *PeriphError {
				icu, _ := NewIcuInitializationProjection(nil, nil, m4ptr64(3), false)
				_, e := icu.Read(ICuBase0)
				return e
			},
			`{"address":"0xffe400","kind":"unknown_initial_icu_control"}`,
		},
		{
			"timer2 uninitialized peripheral",
			func() *PeriphError {
				_, e := NewTimer2Delay().Read(Timer2)
				return e
			},
			`{"address":"0xfd0600","kind":"uninitialized_peripheral"}`,
		},
		{
			"timer2 unsupported clock source",
			func() *PeriphError {
				return NewTimer2Delay().Write(Timer2, 0x1401)
			},
			`{"kind":"unsupported_timer_clock_source","timer":"timer2","value":"0x1401"}`,
		},
		{
			"swirq unmodeled address",
			func() *PeriphError {
				s, _ := NewSoftwareIrqCommands(nil, nil)
				return s.Command(0xdeadbeef, 1)
			},
			`{"address":"0xdeadbeef","kind":"software_irq_command_address_unmodeled"}`,
		},
		{
			"swirq bits unmodeled",
			func() *PeriphError {
				s, _ := NewSoftwareIrqCommands(nil, nil)
				return s.Command(SwIrqBase0, 0x10)
			},
			`{"address":"0xffe1a0","kind":"software_irq_command_bits_unmodeled","mask":"0x10"}`,
		},
	}
	for _, tc := range cases {
		err := tc.act()
		if err == nil {
			t.Fatalf("%s: expected an error", tc.name)
		}
		if err.JSON() != tc.want {
			t.Fatalf("%s JSON mismatch:\n got %s\nwant %s", tc.name, err.JSON(), tc.want)
		}
	}
}

func peFromErr(err error) *PeriphError {
	if pe, ok := err.(*PeriphError); ok {
		return pe
	}
	return nil
}

func TestCoreTickAdvanceOverflow(t *testing.T) {
	ct, _ := NewCoreTickStopped(m4ptr32(0), m4ptr64(4), false, false, false)
	if err := ct.Write(8, 9); err != nil {
		t.Fatalf("prd: %v", err)
	}
	if err := ct.Write(4, 0); err != nil {
		t.Fatalf("cnt: %v", err)
	}
	if err := ct.Write(0, 0x1); err != nil {
		t.Fatalf("enable: %v", err)
	}
	for i := 0; i < 2; i++ {
		if err := ct.Advance(); err != nil {
			t.Fatalf("advance: %v", err)
		}
	}
	if ct.UnresolvedOverflow {
		t.Fatal("no overflow expected at cnt=8")
	}
	if err := ct.Advance(); err != nil {
		t.Fatalf("advance overflow: %v", err)
	}
	if !ct.UnresolvedOverflow || *ct.Cnt != 2 || ct.Expirations != 1 {
		t.Fatalf("overflow state wrong: cnt=%d exp=%d unresolved=%v", *ct.Cnt, ct.Expirations, ct.UnresolvedOverflow)
	}
	// advance again is a strict stop while unresolved
	if err := ct.Advance(); err == nil {
		t.Fatal("advance while unresolved should stop")
	}
	// ACK clears and re-enables counting
	if err := ct.AckRmw("or", 0x40); err != nil {
		t.Fatalf("ack: %v", err)
	}
	if ct.UnresolvedOverflow || *ct.Pending {
		t.Fatal("ack should clear unresolved + pending")
	}
}

func TestWatchdogExpiry(t *testing.T) {
	w, _ := NewWatchdogProjection([]int64{4, 1})
	// key pair -> enable with time_code 1 (period 2)
	if err := w.Write(WDTKey, WdtKeyFirst); err != nil {
		t.Fatalf("key1: %v", err)
	}
	if err := w.Write(WDTKey, WdtKeySecond); err != nil {
		t.Fatalf("key2: %v", err)
	}
	if err := w.Write(WDTCon, 0x10021); err != nil { // feed + enable + time_code 1
		t.Fatalf("con: %v", err)
	}
	err := w.Advance() // elapsed 4 >= 1<<1 -> expires with a strict stop
	if err == nil {
		t.Fatal("watchdog should have expired at elapsed=4 >= 1<<1")
	}
	if err.JSON() != `{"control":"0x21","elapsed_nominal_units":4,"kind":"watchdog_expired_delivery_not_implemented"}` {
		t.Fatalf("expiry JSON mismatch: %s", err.JSON())
	}
	if !w.Expired {
		t.Fatal("watchdog should be marked expired")
	}
	if err := w.Advance(); err == nil {
		t.Fatal("advance after expiry should stop")
	}
}

func TestICUCommandCountdown(t *testing.T) {
	icu, _ := NewIcuInitializationProjection(m4ptr32(0), nil, m4ptr64(3), false)
	if err := icu.Write(ICuBase0+16, ICUCommand0); err != nil {
		t.Fatalf("command: %v", err)
	}
	if v, err := icu.Read(ICuBase0); err != nil || v&0x40000000 == 0 {
		t.Fatalf("busy bit should be set during command: v=%#x err=%v", v, err)
	}
	icu.Advance() // 3 -> 2
	icu.Advance() // 2 -> 1
	icu.Advance() // 1 -> 0 -> completed
	if icu.Completed != 1 || icu.HasPending[0] {
		t.Fatalf("command should complete after 3 steps: completed=%d", icu.Completed)
	}
	if v, err := icu.Read(ICuBase0); err != nil || v&0x40000000 != 0 {
		t.Fatalf("busy bit cleared after completion: v=%#x", v)
	}
}

func TestTimer2LrcFraction(t *testing.T) {
	t2 := NewTimer2Delay()
	if err := t2.Write(Timer2+8, 200); err != nil {
		t.Fatalf("prd: %v", err)
	}
	if err := t2.Write(Timer2+4, 1); err != nil {
		t.Fatalf("cnt: %v", err)
	}
	if err := t2.Write(Timer2, 0xc01); err != nil { // mode 1, src 3 (LRC), div 0
		t.Fatalf("con: %v", err)
	}
	// LRC 1/80: 4 ref ticks per advance -> lrc_ticks accumulate; PRD 200 -> 4000 steps.
	if s, ok := t2.StepsToNextExpiration(4); !ok || s != 4000 {
		t.Fatalf("steps_to_next = %d,%v want 4000,true", s, ok)
	}
	for i := 0; i < 3999; i++ {
		if err := t2.Advance(4); err != nil {
			t.Fatalf("advance %d: %v", i, err)
		}
	}
	if t2.Pending {
		t.Fatal("should not be pending at step 3999")
	}
	if err := t2.Advance(4); err != nil {
		t.Fatalf("advance 4000: %v", err)
	}
	if !t2.Pending || t2.Expirations != 1 {
		t.Fatalf("should be pending at step 4000: pending=%v exp=%d", t2.Pending, t2.Expirations)
	}
}

func TestSoftwareIrqCommand(t *testing.T) {
	s, _ := NewSoftwareIrqCommands(nil, nil)
	if err := s.Command(SwIrqBase0, 0x5); err != nil {
		t.Fatalf("set: %v", err)
	}
	if s.Pending[0].Value != 0x5 || s.Pending[0].Known != 0x5 {
		t.Fatalf("pending should be value=5 known=5, got %+v", s.Pending[0])
	}
	if err := s.Command(SwIrqBase0+4, 0x1); err != nil { // clear bit 0
		t.Fatalf("clear: %v", err)
	}
	if s.Pending[0].Value != 0x4 || s.Pending[0].Known != 0x5 {
		t.Fatalf("after clear pending should be value=4 known=5, got %+v", s.Pending[0])
	}
	s.Acknowledge(0, 2) // clear bit 2
	if s.Pending[0].Value != 0x0 || s.Pending[0].Known != 0x5 {
		t.Fatalf("after ack pending should be value=0 known=5, got %+v", s.Pending[0])
	}
}
