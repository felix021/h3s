package gosim

// The dual loop now executes plain direct call/return (call.s/l/xl, rts) and
// the flag-preserving csync (dualDirectFlowAllowed) on the strict-memory path,
// while the raw-core contract (safeALU) still rejects them and the abstract /
// timing-sensitive boundaries (call.r, rtss, pop.pc*, push.rets*) stay rejected
// on both paths.

import "testing"

func TestSafeDualDirectFlowWhitelist(t *testing.T) {
	raw := NewCPU() // dmem == nil: the raw-core contract still rejects the family
	strict := NewCPU()
	strict.SetDataMem(NewMemory())

	for _, id := range []string{"call.s", "call.l", "call.xl", "rts", "csync"} {
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

	// The boundaries this cut deliberately does not cross stay rejected even on
	// the strict path: indirect call.r, conditional rtss, and the task-stack
	// abstract forms (pop.pc*/push.rets*).
	for _, id := range []string{"call.r", "rtss", "pop.pc", "pop.pc_rl", "push.rets", "push.rets_rl", "lockset", "lockclr"} {
		im := strict.tabByID(id)
		if im == nil {
			t.Fatalf("no insnMeta for %s", id)
		}
		if strict.safeDual(im) {
			t.Fatalf("strict dual loop must keep rejecting %s", id)
		}
	}
}
