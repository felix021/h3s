#!/usr/bin/env python3
"""Differential test: Go Timer0 / IRQController (periph.go) vs the real Python
br28_model.Timer0 / br28_model.IRQController, byte-for-byte.

Unlike test_mem_consistency.py (whose Python Memory is a local inside
probe_boot.explore and needs a re-transcription), the Timer0 / IRQController
classes are directly importable, so this drives the REAL Python models and the
Go models side by side over targeted and randomized operation sequences and
compares every outcome (successful value, or the exact Blocked details) plus the
full post-op state.

Usage:
    cd tools/gosim && go build -buildmode=c-shared -o libgosim.so ./cshared
    python3 tools/gosim/test_timer0_irq_consistency.py
"""
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tools" / "gosim"))

from probe_boot import Blocked  # noqa: E402
from br28_model import Timer0, IRQController  # noqa: E402
from gosim_periph import GosimTimer, GosimIRQ  # noqa: E402


# ---- Timer0 -----------------------------------------------------------------

def py_timer_outcome(py, op):
    try:
        if op[0] == "write":
            py.write(op[1], op[2])
            return ("ok", None)
        if op[0] == "read":
            return ("ok", py.read(op[1]))
        if op[0] == "advance":
            py.advance(op[1])
            return ("ok", None)
        if op[0] == "steps":
            n = py.steps_to_next_expiration(op[1])
            return ("ok", n) if n is not None else ("none", None)
        raise AssertionError(op)
    except Blocked as e:
        return ("error", e.details)
    except ValueError:
        return ("valueerror", None)


def go_timer_outcome(go, op):
    if op[0] == "write":
        return go.write(op[1], op[2])
    if op[0] == "read":
        return go.read(op[1])
    if op[0] == "advance":
        return go.advance(op[1])
    if op[0] == "steps":
        return go.steps_to_next(op[1])
    raise AssertionError(op)


def py_state(py):
    return (py.con, py.cnt, py.prd, py.pending, py.remainder,
            py.source_remainder, py.expirations, py.delivery_in_flight)


def run_timer_pair(py, go, ops, label):
    for i, op in enumerate(ops):
        p = py_timer_outcome(py, op)
        g = go_timer_outcome(go, op)
        if p != g:
            raise AssertionError(
                f"[{label}] op#{i} {op!r} outcome mismatch:\n  go={g}\n  py={p}")
        ps = py_state(py)
        gs = go.state()
        if ps != gs:
            raise AssertionError(
                f"[{label}] op#{i} {op!r} state mismatch:\n  go={gs}\n  py={ps}")


def targeted_timer_ops():
    ops = []
    # build a valid counting timer: prd=9, cnt=0, con mode 1 / source 4 / div 0
    ops += [("write", 8, 9), ("write", 4, 0), ("write", 0, 0x1001)]
    # advance through a period rollover (prd+1 = 10 ticks, ÷1, +4/step)
    ops += [("advance", 4), ("advance", 4), ("advance", 4)]
    ops += [("read", 0), ("read", 4), ("read", 8)]
    ops += [("steps", 4), ("steps", 1)]
    # W1C clear pending (still counting) and read back
    ops += [("write", 0, 0x1001 | 0x4000), ("read", 0)]
    # stop and confirm advance is a no-op
    ops += [("write", 0, 0x1000), ("advance", 4), ("steps", 4)]
    # error paths
    ops += [("write", 0, 0xfcf4), ("write", 0, 0x1), ("write", 12, 0),
            ("read", 12), ("read", 1), ("advance", -1)]
    return ops


def targeted_timer_ops_std12m():
    # STD12M (source 5): 12 MHz counted at 3/4 of RC16M ref ticks.
    ops = [("write", 8, 9), ("write", 4, 0), ("write", 0, 0x1401)]
    for _ in range(6):
        ops.append(("advance", 4))
    ops += [("read", 0), ("steps", 4)]
    return ops


def random_timer_ops(rng, n):
    offsets = [0, 4, 8, 1, 5, 9, 12, 13, -1, 3]
    con_patterns = [0, 0x1000, 0x1001, 0x1401, 0x1400, 0x5001, 0x4000,
                    0x1001 | 0x4000, 0x1401 | 0x4000, 0xfcf3, 0xfcf4, 0x1, 0x2, 0x4]
    rates = [1, 2, 4, 7, 12, 4000]
    ticks = [0, 1, 4, 7, 100, 1000000, -1]
    ops = []
    for _ in range(n):
        kind = rng.random()
        if kind < 0.35:
            ops.append(("write", rng.choice(offsets), rng.choice(con_patterns)))
        elif kind < 0.5:
            ops.append(("write", rng.choice(offsets), rng.getrandbits(32)))
        elif kind < 0.65:
            ops.append(("read", rng.choice(offsets)))
        elif kind < 0.85:
            ops.append(("advance", rng.choice(ticks)))
        else:
            ops.append(("steps", rng.choice(rates)))
    return ops


def test_timer0():
    for allowed in [(4,), (4, 5)]:
        label = "timer-src" + "".join(map(str, allowed))
        py = Timer0(allowed_sources=allowed)
        go = GosimTimer(allowed_sources=allowed)
        run_timer_pair(py, go, targeted_timer_ops(), label + "-targeted")
        if 5 in allowed:
            run_timer_pair(py, go, targeted_timer_ops_std12m(), label + "-std12m")
        rng = random.Random(0x5EED ^ len(allowed))
        run_timer_pair(py, go, random_timer_ops(rng, 6000), label + "-random")
    print("Timer0: PASS (Go matches the Python br28_model.Timer0 byte-for-byte)")


# ---- IRQController -----------------------------------------------------------

def irq_config(rng):
    config = [None if rng.random() < 0.25 else rng.getrandbits(32) for _ in range(32)]
    ipmask = None if rng.random() < 0.3 else rng.randrange(0, 16)
    unmaskable = rng.random() < 0.5
    return config, ipmask, unmaskable


def build_irq_pair(config, ipmask, unmaskable):
    py = IRQController(initial_config=config, initial_ipmask=ipmask,
                       unmaskable_irq_projection=unmaskable)
    go = GosimIRQ(unmaskable_projection=unmaskable)
    for i, w in enumerate(config):
        go.set_config_word(i, w)
    go.set_ipmask(ipmask)
    return py, go


def run_irq_pair(py, go, ops, label):
    for i, (index, icfg) in enumerate(ops):
        try:
            p = ("ok", py.eligible(index, icfg))
        except Blocked as e:
            p = ("error", e.details)
        g = go.eligible(index, icfg)
        if p != g:
            raise AssertionError(
                f"[{label}] eligible({index}, {icfg:#x}) mismatch:\n  go={g}\n  py={p}")


def test_irq_controller():
    icfg_values = [0x000, 0x100, 0x200, 0x300, 0x3ff, 0x2ff, 0x7fffffff]
    rng = random.Random(0x1C)
    for seed in range(24):
        config, ipmask, unmaskable = irq_config(rng)
        py, go = build_irq_pair(config, ipmask, unmaskable)
        ops = [(i, v) for i in range(32) for v in icfg_values]
        for _ in range(200):
            ops.append((rng.randrange(32), rng.getrandbits(32)))
        run_irq_pair(py, go, ops, f"irq-seed{seed}")
    print("IRQController: PASS (Go matches the Python br28_model.IRQController byte-for-byte)")


if __name__ == "__main__":
    test_timer0()
    test_irq_controller()
    sys.exit(0)
