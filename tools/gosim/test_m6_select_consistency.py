#!/usr/bin/env python3
"""Differential test: Go M6 Stage A dual-core select (machine.go) vs the real
Python br28_machine.DualCoreMachine.select, branch-for-branch.

The harness builds a Python DualCoreMachine with minimal fake CPUs/hardware in a
given state, mirrors that state into the Go Machine (via gosim_m6.py), runs one
select on each, and compares the selected core plus every observable side effect
(idle_wait set, last_core, deferred_core1_turns, seen_cpu1_control_kicks, event
list, event_count, timer2_poll_wakes, hardware.current_core, hardware.core_icfg,
and the woken cores' pc/steps). Targeted scenarios pin each branch; randomized
scenarios fuzz the state space.

The anchored idle sites are the two wake preconditions from the Python code:
the TIMER2 poll anchor 0x12cd76 and the CPU1 kick idle (any 'idle' instruction).
No ROM is required.

Usage:
    cd tools/gosim && go build -buildmode=c-shared -o libgosim.so ./cshared
    python3 tools/gosim/test_m6_select_consistency.py
"""
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tools" / "gosim"))

import br28_machine  # noqa: E402
from br28_machine import DualCoreMachine  # noqa: E402
from gosim_m6 import GosimMachine  # noqa: E402

TIMER2_POLL_ANCHOR = 0x12CD76
CORE1_IDLE_PC = 0x12C5CA
KICK_IDLE_PC = 0x6066318


class _FakeInsn:
    def __init__(self, ident):
        self.id = ident


class _FakeCPU:
    def __init__(self, core_id, pc, steps=0, icfg=0x300, icfg_known=True,
                 insn_id="idle", size=2):
        self.core_id = core_id
        self.pc = pc
        self.steps = steps
        self.icfg = icfg
        self.icfg_known = icfg_known
        self.insn_id = insn_id
        self.size = size

    def decode(self, pc):
        return (_FakeInsn(self.insn_id), {}, self.size)


class _FakeHW:
    def __init__(self, kicks=None, timer2=None):
        self.cpu1_control_kick_writes = kicks
        self.timer2 = timer2
        self.current_core = 0
        self.core_icfg = None


def new_machine():
    return DualCoreMachine(cpu1_reset_pc=0, cpu1_icfg=0x300)


def py_to_go_state(m):
    def core_dict(cid):
        if cid not in m.cores:
            return None
        c = m.cores[cid]
        insn, _, size = c.decode(c.pc)
        return {
            "id": c.core_id, "pc": c.pc, "steps": c.steps,
            "icfg": c.icfg if c.icfg_known else 0,
            "icfg_known": c.icfg_known,
            "insn_id": insn.id, "insn_size": size,
        }

    hw = m.hardware
    kicks = getattr(hw, "cpu1_control_kick_writes", None)
    timer2 = getattr(hw, "timer2", None)
    return {
        "cores": [core_dict(0), core_dict(1)],
        "idle_wait": [0 in m.idle_wait_cores, 1 in m.idle_wait_cores],
        "last_core": m.last_core,
        "defer_core1_first_idle": m.defer_core1_first_idle,
        "first_task_returns": [0 in m.first_task_returns, 1 in m.first_task_returns],
        "deferred_core1_turns": m.deferred_core1_turns,
        "cpu1_control_kick_wake": m.cpu1_control_kick_wake,
        "seen_cpu1_control_kicks": m.seen_cpu1_control_kicks,
        "hardware_kicks": kicks,
        "timer2_delay_projection": m.timer2_delay_projection,
        "timer2_pending": bool(timer2 is not None and timer2.pending),
    }


def canonical_py(m, selected):
    hw = m.hardware
    cores = []
    for cid in (0, 1):
        cores.append(None if cid not in m.cores
                     else {"pc": m.cores[cid].pc, "steps": m.cores[cid].steps})
    core_icfg = {cid: hw.core_icfg.get(cid) for cid in m.cores}
    return {
        "selected": selected,
        "cores": cores,
        "idle_wait": sorted(m.idle_wait_cores),
        "last_core": m.last_core,
        "deferred_core1_turns": m.deferred_core1_turns,
        "seen_cpu1_control_kicks": m.seen_cpu1_control_kicks,
        "events": list(m.events),
        "event_count": m.event_count,
        "timer2_poll_wakes": list(m.timer2_poll_wakes),
        "current_core": hw.current_core,
        "core_icfg": core_icfg,
    }


def canonical_go(gs, selected):
    cores = [None if cd is None else {"pc": cd["pc"], "steps": cd["steps"]}
             for cd in gs["cores"]]
    core_icfg = {i: gs["core_icfg"][i] for i, cd in enumerate(gs["cores"]) if cd is not None}
    return {
        "selected": selected,
        "cores": cores,
        "idle_wait": sorted(i for i, b in enumerate(gs["idle_wait"]) if b),
        "last_core": gs["last_core"],
        "deferred_core1_turns": gs["deferred_core1_turns"],
        "seen_cpu1_control_kicks": gs["seen_cpu1_control_kicks"],
        "events": gs["events"],
        "event_count": gs["event_count"],
        "timer2_poll_wakes": gs["timer2_poll_wakes"],
        "current_core": gs["current_core"],
        "core_icfg": core_icfg,
    }


def run_one(m, label):
    go = GosimMachine()
    go.load(py_to_go_state(m))
    py_err = go_err = None
    py_selected = go_selected = None
    try:
        py_selected = m.select().core_id
    except ValueError:
        py_err = "ValueError"
    except Exception as e:  # noqa: BLE001 — surface any unexpected Python error
        py_err = type(e).__name__
    try:
        go_selected = go.select()
    except ValueError:
        go_err = "ValueError"
    if py_err != go_err:
        raise AssertionError(
            f"[{label}] error mismatch: py={py_err} go={go_err}")
    if py_err is not None:
        return
    p = canonical_py(m, py_selected)
    g = canonical_go(go.state(), go_selected)
    if p != g:
        raise AssertionError(
            f"[{label}] state mismatch:\n  go={g}\n  py={p}")


def configure(m, cores, idle_wait, last_core=1, defer=False, first_task_returns=None,
              deferred_turns=0, kick_wake=False, seen_kicks=0, kicks=None,
              timer2_proj=False, timer2_pending=False):
    m.cores = {cid: cpu for cid, cpu in cores.items()}
    m.idle_wait_cores = set(idle_wait)
    m.last_core = last_core
    m.defer_core1_first_idle = defer
    m.first_task_returns = {cid: {"dummy": True} for cid in (first_task_returns or [])}
    m.deferred_core1_turns = deferred_turns
    m.cpu1_control_kick_wake = kick_wake
    m.seen_cpu1_control_kicks = seen_kicks
    timer2 = None
    if timer2_proj:
        timer2 = _FakeHW_kw_timer2(timer2_pending)
    m.timer2_delay_projection = timer2_proj
    m.hardware = _FakeHW(kicks=kicks, timer2=timer2)
    return m


class _FakeHW_kw_timer2:
    def __init__(self, pending):
        self.pending = pending


def c0(pc=0x100, steps=0, icfg=0x300, known=True, insn_id="idle", size=2):
    return _FakeCPU(0, pc, steps, icfg, known, insn_id, size)


def c1(pc=0x200, steps=0, icfg=0x300, known=True, insn_id="idle", size=2):
    return _FakeCPU(1, pc, steps, icfg, known, insn_id, size)


def targeted():
    cases = []

    # Single core only.
    cases.append(configure(new_machine(), {0: c0()}, []))

    # Round-robin: last_core 0 and 1 with neither idle.
    cases.append(configure(new_machine(), {0: c0(), 1: c1()}, [], last_core=0))
    cases.append(configure(new_machine(), {0: c0(), 1: c1()}, [], last_core=1))

    # Idle-wait exclusion: core 1 parked -> 0; core 0 parked -> 1; both parked.
    cases.append(configure(new_machine(), {0: c0(), 1: c1()}, [1], last_core=0))
    cases.append(configure(new_machine(), {0: c0(), 1: c1()}, [0], last_core=1))
    cases.append(configure(new_machine(), {0: c0(), 1: c1()}, [0, 1], last_core=0))

    # defer_core1: core-1 pick bounced back to 0 (below the limit) and at the limit.
    cases.append(configure(new_machine(), {0: c0(), 1: c1(CORE1_IDLE_PC)},
                           [], last_core=0, defer=True, first_task_returns=[1], deferred_turns=0))
    cases.append(configure(new_machine(), {0: c0(), 1: c1(CORE1_IDLE_PC)},
                           [], last_core=0, defer=True, first_task_returns=[1],
                           deferred_turns=br28_machine.CORE1_IDLE_DELAY_LIMIT))

    # CPU1 control kick wake.
    cases.append(configure(new_machine(),
                           {0: c0(), 1: c1(KICK_IDLE_PC, steps=5)}, [1], last_core=0,
                           kick_wake=True, seen_kicks=0, kicks=1))

    # TIMER2 poll wake.
    cases.append(configure(new_machine(),
                           {0: c0(TIMER2_POLL_ANCHOR), 1: c1()}, [0], last_core=1,
                           timer2_proj=True, timer2_pending=True))

    # icfg_known=False on one core yields None in the demand map.
    cases.append(configure(new_machine(),
                           {0: c0(known=False), 1: c1()}, [], last_core=0))
    return cases


def randomized(n):
    rng = random.Random(0x6D6)
    cases = []
    for _ in range(n):
        m = new_machine()
        both = rng.random() < 0.9
        cores = {0: c0(pc=rng.choice([0x100, 0x200, TIMER2_POLL_ANCHOR, KICK_IDLE_PC]),
                       steps=rng.randint(0, 100),
                       icfg=rng.choice([0x300, 0x200]), known=rng.random() < 0.9)}
        if both:
            cores[1] = c1(pc=rng.choice([0x200, CORE1_IDLE_PC, KICK_IDLE_PC]),
                          steps=rng.randint(0, 100),
                          icfg=rng.choice([0x300, 0x200]), known=rng.random() < 0.9)
        idle = [cid for cid in cores if rng.random() < 0.4]
        defer = rng.random() < 0.2
        ftr = [1] if (both and rng.random() < 0.3) else []
        kick_wake = rng.random() < 0.3
        kicks = rng.randint(0, 2) if rng.random() < 0.5 else None
        timer2_proj = rng.random() < 0.3
        timer2_pending = rng.random() < 0.5
        configure(m, cores, idle, last_core=rng.choice([0, 1]), defer=defer,
                  first_task_returns=ftr, deferred_turns=rng.randint(0, 2),
                  kick_wake=kick_wake, seen_kicks=rng.randint(0, 2), kicks=kicks,
                  timer2_proj=timer2_proj, timer2_pending=timer2_pending)
        cases.append(m)
    return cases


def main():
    cases = targeted() + randomized(3000)
    for i, m in enumerate(cases):
        run_one(m, f"case{i}")
    print(f"dual-core select: PASS (Go matches DualCoreMachine.select "
          f"branch-for-branch over {len(cases)} scenarios)")


if __name__ == "__main__":
    main()
