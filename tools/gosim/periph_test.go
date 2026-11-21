package gosim

import "testing"

func TestPeriphErrorJSONMatchesPython(t *testing.T) {
	cases := []struct {
		act  func() *PeriphError
		want string
	}{
		{
			act: func() *PeriphError {
				_, e := NewTimer0().Read(4)
				return e
			},
			want: `{"address":"0xfd0404","kind":"uninitialized_peripheral"}`,
		},
		{
			act: func() *PeriphError {
				return NewTimer0().Write(0, 0xfcf4) // bit 2 set: reserved
			},
			want: `{"kind":"unsupported_timer_mode","value":"0xfcf4"}`,
		},
		{
			act: func() *PeriphError {
				return NewTimer0().Write(0, 0x1) // mode 1, source 0 not allowed
			},
			want: `{"kind":"unsupported_timer_clock_source","value":"0x1"}`,
		},
		{
			act: func() *PeriphError {
				return NewTimer0().Write(0, 0x1001) // mode 1, source 4, but cnt/prd unset
			},
			want: `{"kind":"uninitialized_timer_count_or_period"}`,
		},
		{
			act: func() *PeriphError {
				return NewTimer0().Write(12, 0)
			},
			want: `{"address":"0xfd040c","kind":"unsupported_timer_register"}`,
		},
		{
			act: func() *PeriphError {
				_, e := NewIRQController().Eligible(4, 0x300)
				return e
			},
			want: `{"irq":4,"kind":"uninitialized_irq_configuration"}`,
		},
		{
			act: func() *PeriphError {
				ic := NewIRQController()
				ic.Config[0] = u32p(0x3)
				_, e := ic.Eligible(0, 0x300)
				return e
			},
			want: `{"kind":"uninitialized_ipmask"}`,
		},
		{
			act: func() *PeriphError {
				ic := NewIRQController()
				ic.Config[0] = u32p(0xf0000000) // nibble 7 = 0xf: enabled, priority 7
				ic.IPMask = u32p(1)
				_, e := ic.Eligible(7, 0x300)
				return e
			},
			want: `{"irq":7,"kind":"unmodeled_unmaskable_irq","priority":7}`,
		},
	}
	for i, tc := range cases {
		err := tc.act()
		if err == nil {
			t.Fatalf("case %d: expected an error", i)
		}
		if err.JSON() != tc.want {
			t.Fatalf("case %d JSON mismatch:\n got %s\nwant %s", i, err.JSON(), tc.want)
		}
	}
}

func TestTimer0WriteRead(t *testing.T) {
	tm := NewTimer0()
	// PRD and CNT must be set before a mode-1 CON write.
	if err := tm.Write(8, 0x1234); err != nil {
		t.Fatalf("write prd: %v", err)
	}
	// CNT write resets the two fractional remainders.
	tm.Remainder, tm.SourceRemainder = 7, 2
	if err := tm.Write(4, 0xdeadbeef); err != nil {
		t.Fatalf("write cnt: %v", err)
	}
	if *tm.Cnt != 0xdeadbeef || tm.Remainder != 0 || tm.SourceRemainder != 0 {
		t.Fatalf("cnt write should reset remainders")
	}
	// CON write clears bits 14/15 and stores the rest; W1C bit 14 clears pending.
	tm.Pending = true
	if err := tm.Write(0, 0x1001|0x4000); err != nil {
		t.Fatalf("write con: %v", err)
	}
	if tm.Pending {
		t.Fatal("W1C bit 14 should clear pending")
	}
	if tm.Con == nil || *tm.Con != 0x1001 {
		t.Fatalf("con should be 0x1001 (bits 14/15 masked), got %v", tm.Con)
	}
	// read back, pending projection only on CON.
	tm.Pending = true
	if v, err := tm.Read(0); err != nil || v != 0x1001|0x8000 {
		t.Fatalf("con read should carry pending bit: v=%#x err=%v", v, err)
	}
	if v, err := tm.Read(4); err != nil || v != 0xdeadbeef {
		t.Fatalf("cnt read: v=%#x err=%v", v, err)
	}
}

func TestTimer0AdvancePrescale(t *testing.T) {
	tm := NewTimer0()
	if err := tm.Write(8, 9); err != nil { // prd+1 = 10 ticks per period
		t.Fatalf("prd: %v", err)
	}
	if err := tm.Write(4, 0); err != nil {
		t.Fatalf("cnt: %v", err)
	}
	if err := tm.Write(0, 0x1001); err != nil { // mode 1, source 4, div 0 (÷1)
		t.Fatalf("con: %v", err)
	}
	// not-counting no-op: con mode 0.
	stopped := NewTimer0()
	if err := stopped.Write(0, 0x1000); err != nil {
		t.Fatalf("stopped con: %v", err)
	}
	if err := stopped.Advance(4); err != nil {
		t.Fatalf("stopped advance: %v", err)
	}
	if stopped.Cnt != nil || stopped.Expirations != 0 {
		t.Fatal("stopped timer must not count")
	}

	for _, want := range []struct {
		cnt, exp int64
		pending  bool
	}{
		{4, 0, false},
		{8, 0, false},
		{2, 1, true}, // 8+4 = 12 = 1*10 + 2
	} {
		if err := tm.Advance(4); err != nil {
			t.Fatalf("advance: %v", err)
		}
		if int64(*tm.Cnt) != want.cnt || tm.Expirations != want.exp || tm.Pending != want.pending {
			t.Fatalf("after advance: cnt=%d exp=%d pending=%v, want %+v",
				*tm.Cnt, tm.Expirations, tm.Pending, want)
		}
	}
	// negative ticks is a value error (Python ValueError), not a state change.
	if err := tm.Advance(-1); err == nil {
		t.Fatal("negative ticks should error")
	}
}

func TestTimer0StepsToNextExpiration(t *testing.T) {
	tm := NewTimer0()
	if _, ok := tm.StepsToNextExpiration(4); ok {
		t.Fatal("uninitialized timer should report not-counting")
	}
	tm.Write(8, 9)      // prd+1 = 10
	tm.Write(4, 0)      // cnt = 0
	tm.Write(0, 0x1001) // div 0 (÷1), source 4
	// needed = 1*(9+1-0) - 0 = 10; num=4 → ceil(10/4) = 3
	if s, ok := tm.StepsToNextExpiration(4); !ok || s != 3 {
		t.Fatalf("steps_to_next = %d,%v want 3,true", s, ok)
	}
}

func TestIRQControllerEligible(t *testing.T) {
	ic := NewIRQController()
	// gates 8/9 must both be set, else False regardless of config.
	if v, err := ic.Eligible(0, 0x000); err != nil || v {
		t.Fatalf("gates off should be false, got %v,%v", v, err)
	}
	ic.Config[0] = u32p(0x5) // nibble 0 = 0b0101: enabled, priority 2
	ic.IPMask = u32p(2)
	// priority 2 >= ipmask 2 → true
	if v, err := ic.Eligible(0, 0x300); err != nil || !v {
		t.Fatalf("priority 2 vs mask 2 should be true, got %v,%v", v, err)
	}
	// priority 2 < ipmask 3 → false
	ic.IPMask = u32p(3)
	if v, err := ic.Eligible(0, 0x300); err != nil || v {
		t.Fatalf("priority 2 vs mask 3 should be false, got %v,%v", v, err)
	}
	// enable bit clear → false (never consults ipmask)
	ic.Config[0] = u32p(0x4) // nibble 0 = 0b0100: disabled
	if v, err := ic.Eligible(0, 0x300); err != nil || v {
		t.Fatalf("disabled irq should be false, got %v,%v", v, err)
	}
	// unmaskable projection: priority 6 returns true when enabled.
	ic2 := NewIRQController()
	ic2.UnmaskableIRQProjection = true
	ic2.Config[0] = u32p(0xd) // nibble 0 = 0b1101: enabled, priority 6
	ic2.IPMask = u32p(1)
	if v, err := ic2.Eligible(0, 0x300); err != nil || !v {
		t.Fatalf("unmaskable projection should be true, got %v,%v", v, err)
	}
}

func TestCanProveAdvanceNoop(t *testing.T) {
	cases := []struct {
		name string
		p    *PeriphState
		want bool
	}{
		{"nil", nil, false},
		{"no timer", &PeriphState{}, true},
		{"timer not counting", &PeriphState{Timer: NewTimer0()}, true},
		{"timer pending", &PeriphState{Timer: &Timer0{Pending: true}}, false},
		{"other model active", &PeriphState{OtherAdvanceActive: true}, false},
	}
	for _, tc := range cases {
		ok, why := tc.p.CanProveAdvanceNoop()
		if ok != tc.want {
			t.Fatalf("%s: got %v (%s), want %v", tc.name, ok, why, tc.want)
		}
	}
	// a counting timer is not provably empty.
	count := NewTimer0()
	count.Write(8, 9)
	count.Write(4, 0)
	count.Write(0, 0x1001)
	if ok, why := (&PeriphState{Timer: count}).CanProveAdvanceNoop(); ok {
		t.Fatalf("counting timer should not be provably empty (%s)", why)
	}
}
