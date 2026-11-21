package gosim

import "testing"

// The M1 skeleton must never execute: with no peripheral model and no strict
// memory ported, running the loop would fabricate values. Lock that contract so
// a future milestone that adds a model can't silently start executing without
// updating the RunLoop predicate and this test together.
func TestWorldRunLoopYields(t *testing.T) {
	w := &World{Cores: [2]*Core{{ID: 0}, {ID: 1}}, Mem: NewMemory()}
	res := w.RunLoop(8)
	if res.Exit != YieldBeforeAttempt {
		t.Fatalf("expected YieldBeforeAttempt, got exit %v", res.Exit)
	}
	if len(res.Steps) != 0 {
		t.Fatalf("yield must commit no steps, got %d", len(res.Steps))
	}
	if res.Reason == "" {
		t.Fatal("yield must carry an honest reason")
	}
}
