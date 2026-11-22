package gosim

// The dual loop now executes the register/immediate-compare if-block openers
// (dualIfBlockAllowed). This pins the two new pieces: the if-block state
// (afterT) round-trips between the Core twin and the CPU engine, and safeDual
// allows exactly the migrated openers on the strict-memory path while the
// raw-core contract (safeALU) still rejects them.

import "testing"

func TestAfterThenRoundTrip(t *testing.T) {
	// syncCPUFromCore must restore the if-block state from the Core twin.
	c := &Core{AfterThen: &AfterThenState{Left: 2, ElseN: 1}}
	cpu := NewCPU()
	syncCPUFromCore(cpu, c)
	if cpu.afterT == nil || cpu.afterT.left != 2 || cpu.afterT.elseN != 1 {
		t.Fatalf("syncCPUFromCore did not restore afterT: %+v", cpu.afterT)
	}

	// syncCoreFromCPU must save it back (surviving a mid-block decrement).
	cpu.afterT.left--
	syncCoreFromCPU(c, cpu, nil)
	if c.AfterThen == nil || c.AfterThen.Left != 1 || c.AfterThen.ElseN != 1 {
		t.Fatalf("syncCoreFromCPU did not save afterT: %+v", c.AfterThen)
	}

	// A completed block clears the round-trip state.
	cpu.afterT = nil
	syncCoreFromCPU(c, cpu, nil)
	if c.AfterThen != nil {
		t.Fatalf("syncCoreFromCPU did not clear AfterThen: %+v", c.AfterThen)
	}
}

func TestSafeDualIfBlockWhitelist(t *testing.T) {
	raw := NewCPU() // dmem == nil: the raw-core contract still rejects if-blocks
	strict := NewCPU()
	strict.SetDataMem(NewMemory())

	for _, id := range []string{"if.le.ri.s12", "if.ne.and.rm", "if.ne.and.rr", "ifs.ge.ri.s12"} {
		im := raw.tabByID(id)
		if im == nil {
			t.Fatalf("no insnMeta for %s", id)
		}
		if raw.safeDual(im) {
			t.Fatalf("raw core must reject %s (safeALU contract)", id)
		}
		if !strict.safeDual(im) {
			t.Fatalf("strict dual loop must allow %s", id)
		}
	}

	// Floating (iff.*) and guessed and_block (if.unknown.rr.*) openers stay
	// rejected even on the strict path; so does a rep body.
	for _, id := range []string{"iff.eq.rr", "iff.uge.rr", "if.unknown.rr.1", "rep.i"} {
		im := strict.tabByID(id)
		if im == nil {
			t.Fatalf("no insnMeta for %s", id)
		}
		if strict.safeDual(im) {
			t.Fatalf("strict dual loop must keep rejecting %s", id)
		}
	}
}
