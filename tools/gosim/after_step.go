package gosim

// after_step.go: M6 Stage B — the static "does this pc fire an after_step
// hook" judgment for the four data-driven PC-anchored special cases that live
// in br28_machine.DualCoreMachine.after_step. This ports only the hit/miss
// predicate; the hook bodies (cfg523 install, boot-anchor recording, first-task
// hypothesis tracking, D1 observer feeding) stay in Python.
//
// It mirrors the M3-M5 CanProveAdvanceNoop precedent: the judgment is the
// honest precondition that lets World.RunLoop close the after_step phase for
// configurations where no hook fires at the current pc. A pc where a hook
// fires — or where the predicate cannot be proven — hands the attempt back to
// Python, so nothing is fabricated.
//
// See docs/gosim-wholeloop-design.md §2 step 5 and §7.4 (the "hook jungle"
// blocker this milestone starts to close).

import "encoding/json"

const (
	// Cfg523ParserEntry mirrors br28_machine.CFG523_PARSER_ENTRY: the config-523
	// parser entry. The install hook keys on the *next* pc (the instruction about
	// to execute) so the 70-byte object is in place before the first parser
	// instruction runs.
	Cfg523ParserEntry = 0x60010b4
	// Cfg523ObjectAddress mirrors br28_machine.CFG523_OBJECT_ADDRESS (the
	// qcy_bss object slot the 70-byte object is written to).
	Cfg523ObjectAddress = 0x1274F4
)

// bootAnchorPCs mirrors the seven core-1 bootstrap anchor pcs in
// DualCoreMachine.after_step (the boot_anchors recorder). It is core-1 only.
var bootAnchorPCs = map[uint32]bool{
	0x0: true, 0xa144: true, 0xc5fe: true, 0xc602: true,
	0x12caf4: true, 0x12cafa: true, 0x12cb00: true,
}

// first-task hypothesis anchors (br28_machine.FIRST_TASK_SETUP entry/tail).
const (
	// firstTaskEntryBefore is the scheduler-call pc (the before_pc of the step
	// that lands on the first-task setup entry).
	firstTaskEntryBefore = 0x6065364
	// firstTaskEntryNext is the landing pc (cpu.pc after that step) that starts
	// the FIRST_TASK_SETUP trace.
	firstTaskEntryNext = 0x6066106
	// firstTaskTailLo/Hi are the two tail pcs where the armed first-task return
	// frame's next_pc is patched.
	firstTaskTailLo = 0x606612c
	firstTaskTailHi = 0x606612e
)

// firstTaskSetupPCs mirrors FIRST_TASK_SETUP[i][0] in order — the 12
// instructions of the first-task setup trace (the task description calls it
// "11 instructions"; the actual tuple has 12 entries, pinned here verbatim).
var firstTaskSetupPCs = [12]uint32{
	0x6066106, 0x6066108, 0x606610a, 0x606610c,
	0x6066112, 0x6066116, 0x606611a, 0x606611c,
	0x6066120, 0x6066122, 0x6066126, 0x6066128,
}

// AfterStep is the after_step hook configuration plus the per-core first-task
// tracking state the static judgment needs. The hook bodies stay in Python;
// this only mirrors the "does this pc fire a hook" predicate.
type AfterStep struct {
	Cfg523Enabled       bool // cfg523_object_bytes fixture armed
	Cfg523Installed     bool // idempotent install gate already consumed
	FirstTaskHypothesis bool // first_task_return_hypothesis is not None
	ObserverActive      bool // module-level d1_window_observer is attached

	// First-task tracking (mirrors machine.first_task_setup[core] and the armed
	// cpu.first_task_return_frame); only meaningful when FirstTaskHypothesis.
	// The Go loop yields at the entry pc, so in practice these stay at their
	// zero values while Go runs — but the predicate models them so the
	// differential test can pin every branch against Python.
	FirstTaskSetupIndex  [2]int  // -1 = no in-flight setup trace for this core
	FirstTaskReturnFrame [2]bool // cpu.first_task_return_frame is armed
}

// HasArmedHook reports whether any of the four hooks could fire somewhere in a
// single-core loop. Boot anchors are core-1 only, so they do not arm the
// single-core loop (and are therefore excluded). It is the config-level gate
// for the fast path: when false, after_step is provably empty for every pc.
func (a *AfterStep) HasArmedHook() bool {
	if a == nil {
		return false
	}
	if a.ObserverActive || a.FirstTaskHypothesis {
		return true
	}
	if a.Cfg523Enabled && !a.Cfg523Installed {
		return true
	}
	return false
}

// CanProveNoop is the static per-pc judgment: given the core id and the
// before/next pc of one committed attempt, does every after_step hook provably
// not fire? It returns (true, "") when the phase is a no-op, and (false,
// reason) when a hook fires at this pc. It never guesses: an armed hook whose
// anchor cannot be excluded returns false (the caller yields back to Python).
func (a *AfterStep) CanProveNoop(core int, beforePC, nextPC uint32) (bool, string) {
	if a == nil {
		return true, "no after_step hooks configured"
	}
	// Hook 4: the D1 window observer feeds every step (not pc-anchored).
	if a.ObserverActive {
		return false, "D1 window observer active (after_step feeds every step)"
	}
	// Hook 1: cfg523 fixture installs once, keyed on the parser-entry next pc.
	if a.Cfg523Enabled && !a.Cfg523Installed && core == 0 && nextPC == Cfg523ParserEntry {
		return false, "cfg523 fixture install fires at the parser entry"
	}
	// Hook 2: core-1 bootstrap anchors (before_pc recorder).
	if core == 1 && bootAnchorPCs[beforePC] {
		return false, "core-1 boot anchor fires"
	}
	// Hook 3: first-task return hypothesis (entry / setup trace / frame tail).
	if a.FirstTaskHypothesis {
		if beforePC == firstTaskEntryBefore && nextPC == firstTaskEntryNext {
			return false, "first-task setup entry fires"
		}
		if idx := a.FirstTaskSetupIndex[core]; idx >= 0 && idx < len(firstTaskSetupPCs) && beforePC == firstTaskSetupPCs[idx] {
			return false, "first-task setup trace advances"
		}
		if a.FirstTaskReturnFrame[core] && (beforePC == firstTaskTailLo || beforePC == firstTaskTailHi) {
			return false, "first-task return frame tail fires"
		}
	}
	return true, "no after_step hook fires at this pc"
}

// ---- JSON config round-trip for the differential harness -------------------

// afterStepConfig is the JSON shape the differential harness loads. The
// tracking arrays use -1 for "no in-flight setup" (Python None maps to -1).
type afterStepConfig struct {
	Cfg523Enabled        bool    `json:"cfg523_enabled"`
	Cfg523Installed      bool    `json:"cfg523_installed"`
	FirstTaskHypothesis  bool    `json:"first_task_hypothesis"`
	ObserverActive       bool    `json:"observer_active"`
	FirstTaskSetupIndex  [2]int  `json:"first_task_setup_index"`
	FirstTaskReturnFrame [2]bool `json:"first_task_return_frame"`
}

// LoadJSON restores the hook configuration from the differential harness's JSON
// shape (see afterStepConfig). A parse failure is returned as an error.
func (a *AfterStep) LoadJSON(data []byte) error {
	var in afterStepConfig
	if err := json.Unmarshal(data, &in); err != nil {
		return err
	}
	a.Cfg523Enabled = in.Cfg523Enabled
	a.Cfg523Installed = in.Cfg523Installed
	a.FirstTaskHypothesis = in.FirstTaskHypothesis
	a.ObserverActive = in.ObserverActive
	a.FirstTaskSetupIndex = in.FirstTaskSetupIndex
	a.FirstTaskReturnFrame = in.FirstTaskReturnFrame
	return nil
}
