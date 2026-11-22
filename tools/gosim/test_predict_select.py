#!/usr/bin/env python3
"""Differential test: the side-effect-free predict_select mirror (gosim_dual_engine)
vs the real DualCoreMachine.select, over the select branch space.

predict_select must report the SAME core and the SAME effective pc the real
select commits, for every branch (round-robin, idle-wait exclusion, defer_core1,
CPU1-control kick wake, TIMER2 anchored poll wake) — but without mutating the
live machine. Parked cores always sit on an 'idle' instruction (the real
precondition the wake helpers require), matching the crc_bankb state space.

Usage:
    python3 tools/gosim/test_predict_select.py
"""
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tools" / "gosim"))

import br28_machine  # noqa: E402
from br28_machine import DualCoreMachine  # noqa: E402
from gosim_dual_engine import predict_select  # noqa: E402

TIMER2_POLL_ANCHOR = 0x12CD76
CORE1_IDLE_PC = 0x12C5CA
KICK_IDLE_PC = 0x6066318


class _FakeInsn:
    def __init__(self, ident):
        self.id = ident


class _FakeCPU:
    def __init__(self, core_id, pc, steps=0, insn_id="idle", size=2,
                 icfg=0x300, icfg_known=True):
        self.core_id = core_id
        self.pc = pc
        self.steps = steps
        self.insn_id = insn_id
        self.size = size
        self.icfg = icfg
        self.icfg_known = icfg_known

    def decode(self, pc):
        return (_FakeInsn(self.insn_id), {}, self.size)


class _FakeHW:
    def __init__(self, kicks=None, timer2=None):
        self.cpu1_control_kick_writes = kicks
        self.timer2 = timer2
        self.current_core = 0
        self.core_icfg = None


class _FakeTimer2:
    def __init__(self, pending):
        self.pending = pending


def new_machine():
    return DualCoreMachine(cpu1_reset_pc=0, cpu1_icfg=0x300)


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
    timer2 = _FakeTimer2(timer2_pending) if timer2_proj else None
    m.timer2_delay_projection = timer2_proj
    m.hardware = _FakeHW(kicks=kicks, timer2=timer2)
    return m


def clone_machine(m):
    """Rebuild a fresh machine with the same select-relevant state (so the real
    select's side effects land on the clone, not the input)."""
    m2 = new_machine()
    m2.cores = {cid: _FakeCPU(c.core_id, c.pc, c.steps, c.insn_id, c.size,
                             c.icfg, c.icfg_known)
                for cid, c in m.cores.items()}
    m2.idle_wait_cores = set(m.idle_wait_cores)
    m2.last_core = m.last_core
    m2.defer_core1_first_idle = m.defer_core1_first_idle
    m2.first_task_returns = dict(m.first_task_returns)
    m2.deferred_core1_turns = m.deferred_core1_turns
    m2.cpu1_control_kick_wake = m.cpu1_control_kick_wake
    m2.seen_cpu1_control_kicks = m.seen_cpu1_control_kicks
    m2.timer2_delay_projection = m.timer2_delay_projection
    kicks = getattr(m.hardware, 'cpu1_control_kick_writes', None)
    timer2 = getattr(m.hardware, 'timer2', None)
    m2.hardware = _FakeHW(kicks=kicks,
                          timer2=_FakeTimer2(timer2.pending) if timer2 is not None else None)
    return m2


def real_result(m):
    m2 = clone_machine(m)
    try:
        selected = m2.select().core_id
    except Exception as e:  # noqa: BLE001
        return ("err", type(e).__name__)
    return ("ok", selected, m2.cores[selected].pc)


def pred_result(m):
    try:
        core, cpu, pc = predict_select(m, m.hardware)
    except Exception as e:  # noqa: BLE001 — the predictor must never crash
        return ("err", type(e).__name__)
    return ("ok", core, pc)


def run_one(m, label):
    real = real_result(m)
    pred = pred_result(m)
    if real[0] != pred[0]:
        raise AssertionError(f"[{label}] outcome mismatch: real={real} pred={pred}")
    if real[0] == "err":
        return
    if real != pred:
        raise AssertionError(f"[{label}] mismatch: real={real} pred={pred}")


def c0(pc=0x100, steps=0, insn_id="idle", size=2):
    return _FakeCPU(0, pc, steps, insn_id, size)


def c1(pc=0x200, steps=0, insn_id="idle", size=2):
    return _FakeCPU(1, pc, steps, insn_id, size)


def targeted():
    cases = []
    cases.append(configure(new_machine(), {0: c0()}, []))
    cases.append(configure(new_machine(), {0: c0(), 1: c1()}, [], last_core=0))
    cases.append(configure(new_machine(), {0: c0(), 1: c1()}, [], last_core=1))
    cases.append(configure(new_machine(), {0: c0(), 1: c1()}, [1], last_core=0))
    cases.append(configure(new_machine(), {0: c0(), 1: c1()}, [0], last_core=1))
    cases.append(configure(new_machine(), {0: c0(), 1: c1()}, [0, 1], last_core=0))
    cases.append(configure(new_machine(), {0: c0(), 1: c1(CORE1_IDLE_PC)},
                           [], last_core=0, defer=True, first_task_returns=[1], deferred_turns=0))
    cases.append(configure(new_machine(), {0: c0(), 1: c1(CORE1_IDLE_PC)},
                           [], last_core=0, defer=True, first_task_returns=[1],
                           deferred_turns=br28_machine.CORE1_IDLE_DELAY_LIMIT))
    cases.append(configure(new_machine(),
                           {0: c0(), 1: c1(KICK_IDLE_PC, steps=5)}, [1], last_core=0,
                           kick_wake=True, seen_kicks=0, kicks=1))
    cases.append(configure(new_machine(),
                           {0: c0(TIMER2_POLL_ANCHOR), 1: c1()}, [0], last_core=1,
                           timer2_proj=True, timer2_pending=True))
    return cases


def randomized(n):
    rng = random.Random(0x5E1E)
    cases = []
    for _ in range(n):
        m = new_machine()
        both = rng.random() < 0.9
        cores = {0: c0(pc=rng.choice([0x100, 0x200, TIMER2_POLL_ANCHOR, KICK_IDLE_PC]),
                       steps=rng.randint(0, 100))}
        if both:
            cores[1] = c1(pc=rng.choice([0x200, CORE1_IDLE_PC, KICK_IDLE_PC]),
                          steps=rng.randint(0, 100))
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
    print(f"predict_select: PASS (mirrors DualCoreMachine.select over {len(cases)} "
          f"scenarios, side-effect-free)")


if __name__ == "__main__":
    main()
