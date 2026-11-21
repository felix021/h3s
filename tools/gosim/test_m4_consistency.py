#!/usr/bin/env python3
"""Differential test: Go M4 peripheral models (periph_m4.go) vs the real Python
br28_model.CoreTickStopped / WatchdogProjection / IcuInitializationProjection /
Timer2Delay / SoftwareIrqCommands, byte-for-byte.

Follows test_timer0_irq_consistency.py: the five classes are directly
importable, so this drives the REAL Python models and the Go models side by
side over targeted and randomized operation sequences and compares every
outcome (successful value, the exact Blocked details, or the ValueError class)
plus the full advance-relevant post-op state.

Usage:
    cd tools/gosim && go build -buildmode=c-shared -o libgosim.so ./cshared
    python3 tools/gosim/test_m4_consistency.py
"""
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tools" / "gosim"))

from probe_boot import Blocked  # noqa: E402
from br28_model import (  # noqa: E402
    CoreTickStopped, WatchdogProjection, IcuInitializationProjection,
    Timer2Delay, SoftwareIrqCommands,
)
from gosim_m4 import GosimCoreTick, GosimWatchdog, GosimICU, GosimTimer2, GosimSwIrq  # noqa: E402

CORE_TICK_BASES = (0xffe0ec, 0xffe8ec)
WDT_CON, WDT_KEY = 0xf2a800, 0xf2a804
ICU_BASES = (0xffe400, 0xffec00)
TIMER2 = 0xfd0600
SWIRQ_BASES = (0xffe1a0, 0xffe9a0)
SWIRQ_SRC = (0xffe200, 0xffea00)


def _check(label, i, op, go, py):
    if go != py:
        raise AssertionError(
            f"[{label}] op#{i} {op!r} outcome mismatch:\n  go={go}\n  py={py}")


# ---- CoreTickStopped ---------------------------------------------------------

def py_coretick_outcome(py, op):
    try:
        if op[0] == "read":
            return ("ok", py.read(op[1]))
        if op[0] == "write":
            py.write(op[1], op[2])
            return ("ok", None)
        if op[0] == "advance":
            py.advance()
            return ("ok", None)
        if op[0] == "ack":
            py.ack_rmw(op[1], op[2])
            return ("ok", None)
        raise AssertionError(op)
    except Blocked as e:
        return ("error", e.details)
    except ValueError:
        return ("valueerror", None)


def py_coretick_state(py):
    return {
        "con": py.con, "cnt": py.cnt, "prd": py.prd, "pending": py.pending,
        "writes": py.writes, "progress": py.progress,
        "expirations": py.expirations,
        "unresolved_overflow": py.unresolved_overflow,
        "acknowledgements": py.acknowledgements,
        "delivery_in_flight": py.delivery_in_flight,
        "prd_write": py.prd_write,
    }


def run_coretick_pair(py, go, ops, label):
    for i, op in enumerate(ops):
        p = py_coretick_outcome(py, op)
        g = (go.read(op[1]) if op[0] == "read"
             else go.write(op[1], op[2]) if op[0] == "write"
             else go.advance() if op[0] == "advance"
             else go.ack_rmw(op[1], op[2]))
        _check(label, i, op, g, p)
        ps = py_coretick_state(py)
        gs = go.state()
        if gs != ps:
            raise AssertionError(
                f"[{label}] op#{i} {op!r} state mismatch:\n  go={gs}\n  py={ps}")


def targeted_coretick_ops():
    ops = [
        ("write", 8, 9), ("write", 4, 0), ("write", 0, 0x1),
        ("advance",), ("advance",), ("advance",),
        ("read", 8),          # overflow window: prd_readback off -> blocked
        ("ack", "or", 0x40),  # clear pending + unresolved
        ("advance",),
        ("write", 0, 0x40), ("write", 4, 5), ("write", 8, 99),
        ("write", 0, 0x1), ("advance",), ("read", 0), ("read", 4),
        ("ack", "and", 0x40),  # wrong rmw shape -> blocked
    ]
    return ops


def random_coretick_ops(rng, n):
    offsets = [0, 4, 8, 1, 5, 9, 12]
    con_values = [0, 0x1, 0x40, 0x41, 0x2, 0x80000000]
    cnt_values = [0, 1, 5, 9, 99, 100, 0xffffffff]
    prd_values = [1, 9, 99, 0, 0xffffffff]
    ops = []
    for _ in range(n):
        k = rng.random()
        if k < 0.3:
            ops.append(("write", rng.choice(offsets), rng.getrandbits(32)))
        elif k < 0.45:
            ops.append(("write", rng.choice([0, 4, 8]), rng.choice(con_values + cnt_values + prd_values)))
        elif k < 0.55:
            ops.append(("read", rng.choice([0, 4, 8])))
        elif k < 0.85:
            ops.append(("advance",))
        else:
            ops.append(("ack", rng.choice(["or", "and"]), rng.choice([0x40, 0x0, 0x41, 0xffffffff])))
    return ops


def test_coretick():
    configs = [
        dict(label="unknown", initial_con=None, progress=None),
        dict(label="disabled", initial_con=0, progress=None),
        dict(label="running", initial_con=0, progress=4),
        dict(label="prd-readback", initial_con=0, progress=4, prd_readback=True),
        dict(label="cnt-held", initial_con=0, progress=4, cnt_held_snapshot=True),
        dict(label="freerun", initial_con=0, progress=4, freerun_edge_period=True),
    ]
    for cfg in configs:
        label = "coretick-" + cfg["label"]
        kwargs = {k: v for k, v in cfg.items() if k != "label"}
        py = CoreTickStopped(**kwargs)
        go = GosimCoreTick(**kwargs)
        run_coretick_pair(py, go, targeted_coretick_ops(), label + "-targeted")
        rng = random.Random(0xC0A1 ^ sum(ord(c) for c in label))
        run_coretick_pair(py, go, random_coretick_ops(rng, 4000), label + "-random")
    print("CoreTickStopped: PASS (Go matches br28_model.CoreTickStopped byte-for-byte)")


# ---- WatchdogProjection -------------------------------------------------------

def py_wdt_outcome(py, op):
    try:
        if op[0] == "read":
            return ("ok", py.read(op[1]))
        if op[0] == "write":
            py.write(op[1], op[2])
            return ("ok", None)
        if op[0] == "advance":
            py.advance()
            return ("ok", None)
        if op[0] == "mailbox_feed":
            py.mailbox_feed()
            return ("ok", None)
        raise AssertionError(op)
    except Blocked as e:
        return ("error", e.details)
    except ValueError:
        return ("valueerror", None)


def py_wdt_state(py):
    return {
        "con": py.con, "key_phase": py.key_phase, "elapsed": py.elapsed,
        "remainder": py.remainder, "commands": py.commands, "feeds": py.feeds,
        "expired": py.expired, "mailbox_feeds": py.mailbox_feeds,
    }


def run_wdt_pair(py, go, ops, label):
    for i, op in enumerate(ops):
        p = py_wdt_outcome(py, op)
        g = (go.read(op[1]) if op[0] == "read"
             else go.write(op[1], op[2]) if op[0] == "write"
             else go.advance() if op[0] == "advance"
             else go.mailbox_feed())
        _check(label, i, op, g, p)
        if py_wdt_state(py) != go.state():
            raise AssertionError(
                f"[{label}] op#{i} {op!r} state mismatch:\n  go={go.state()}\n  py={py_wdt_state(py)}")


def targeted_wdt_ops():
    # key pair -> CON enable with a timeout clock -> count to expiry.
    ops = [
        ("write", WDT_KEY, 0x048c159d),
        ("write", WDT_KEY, 0x26ae37bf),
        ("write", WDT_CON, 0x1002e),  # bit16 feed + bit5 enable + time_code 14
        ("advance",), ("advance",),
        ("read", WDT_CON),
        ("mailbox_feed",),
        ("advance",),
    ]
    return ops


def random_wdt_ops(rng, n):
    keys = [0x048c159d, 0x26ae37bf, 0, 1, 0xffffffff]
    cons = [0x1002e, 0x2e, 0x10000, 0x10020, 0x1002f, 0x2f, 0x31, 0, 0xffffffff]
    addrs = [WDT_CON, WDT_KEY, 0xf2a7fc, 0xdeadbeef]
    ops = []
    for _ in range(n):
        k = rng.random()
        if k < 0.35:
            ops.append(("write", rng.choice(addrs), rng.getrandbits(32)))
        elif k < 0.5:
            ops.append(("write", rng.choice([WDT_CON, WDT_KEY]), rng.choice(keys + cons)))
        elif k < 0.6:
            ops.append(("read", rng.choice(addrs)))
        elif k < 0.9:
            ops.append(("advance",))
        else:
            ops.append(("mailbox_feed",))
    return ops


def test_watchdog():
    configs = [dict(label="no-clock", progress=None),
               dict(label="clock", progress=(4, 1)),
               dict(label="frac-clock", progress=(1, 7))]
    for cfg in configs:
        label = "wdt-" + cfg["label"]
        py = WatchdogProjection(cfg["progress"])
        go = GosimWatchdog(cfg["progress"])
        run_wdt_pair(py, go, targeted_wdt_ops(), label + "-targeted")
        rng = random.Random(0x0D ^ sum(ord(c) for c in label))
        run_wdt_pair(py, go, random_wdt_ops(rng, 4000), label + "-random")
    print("WatchdogProjection: PASS (Go matches br28_model.WatchdogProjection byte-for-byte)")


# ---- IcuInitializationProjection ----------------------------------------------

def py_icu_outcome(py, op):
    try:
        if op[0] == "read":
            return ("ok", py.read(op[1]))
        if op[0] == "write":
            py.write(op[1], op[2])
            return ("ok", None)
        if op[0] == "advance":
            py.advance()
            return ("ok", None)
        raise AssertionError(op)
    except Blocked as e:
        return ("error", e.details)
    except ValueError:
        return ("valueerror", None)


def py_icu_state(py):
    return {
        "control0": py.control[ICU_BASES[0]],
        "control1": py.control[ICU_BASES[1]],
        "command_steps": py.command_steps,
        "pending": [py.pending.get(ICU_BASES[0]), py.pending.get(ICU_BASES[1])],
        "completed": py.completed,
        "busy_reads": py.busy_reads,
    }


def run_icu_pair(py, go, ops, label):
    for i, op in enumerate(ops):
        p = py_icu_outcome(py, op)
        g = (go.read(op[1]) if op[0] == "read"
             else go.write(op[1], op[2]) if op[0] == "write"
             else go.advance())
        _check(label, i, op, g, p)
        if py_icu_state(py) != go.state():
            raise AssertionError(
                f"[{label}] op#{i} {op!r} state mismatch:\n  go={go.state()}\n  py={py_icu_state(py)}")


def targeted_icu_ops():
    base0 = ICU_BASES[0]
    ops = [
        ("write", base0 + 16, 0xca000010),   # start command (needs control set)
        ("read", base0),                      # busy bit read
        ("advance",), ("advance",),
        ("write", base0, 0x1),                # enable (needs command completed)
        ("read", base0),
    ]
    return ops


def random_icu_ops(rng, n):
    b = ICU_BASES[0]
    ops = []
    addrs = [b, b + 16, b + 4, ICU_BASES[1], ICU_BASES[1] + 16]
    values = [0xca000010, 0xca000020, 0xca000040, 0x1, 0x0, 0x2, 0xffffffff]
    for _ in range(n):
        k = rng.random()
        if k < 0.4:
            ops.append(("write", rng.choice(addrs), rng.getrandbits(32)))
        elif k < 0.6:
            ops.append(("write", rng.choice(addrs), rng.choice(values)))
        elif k < 0.8:
            ops.append(("read", rng.choice(addrs)))
        else:
            ops.append(("advance",))
    return ops


def test_icu():
    configs = [
        dict(label="unknown", initial_words={}, command_steps=None),
        dict(label="steps", initial_words={ICU_BASES[0]: 0}, command_steps=3),
        dict(label="region", initial_words={ICU_BASES[0]: 0, ICU_BASES[1]: 0},
             command_steps=3, region_projection=True),
    ]
    for cfg in configs:
        label = "icu-" + cfg["label"]
        kwargs = {k: v for k, v in cfg.items() if k != "label"}
        py = IcuInitializationProjection(**kwargs)
        go = GosimICU(**kwargs)
        run_icu_pair(py, go, targeted_icu_ops(), label + "-targeted")
        rng = random.Random(0x1C ^ sum(ord(c) for c in label))
        run_icu_pair(py, go, random_icu_ops(rng, 4000), label + "-random")
    print("IcuInitializationProjection: PASS (Go matches br28_model.IcuInitializationProjection byte-for-byte)")


# ---- Timer2Delay --------------------------------------------------------------

def py_timer2_outcome(py, op):
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


def py_timer2_state(py):
    return {
        "con": py.con, "cnt": py.cnt, "prd": py.prd, "pending": py.pending,
        "lrc_remainder": py.lrc_remainder, "remainder": py.remainder,
        "expirations": py.expirations, "writes": py.writes,
        "con_reads": py.con_reads, "advance_calls": py.advance_calls,
    }


def run_timer2_pair(py, go, ops, label):
    for i, op in enumerate(ops):
        p = py_timer2_outcome(py, op)
        g = (go.write(op[1], op[2]) if op[0] == "write"
             else go.read(op[1]) if op[0] == "read"
             else go.advance(op[1]) if op[0] == "advance"
             else go.steps_to_next(op[1]))
        _check(label, i, op, g, p)
        if py_timer2_state(py) != go.state():
            raise AssertionError(
                f"[{label}] op#{i} {op!r} state mismatch:\n  go={go.state()}\n  py={py_timer2_state(py)}")


def targeted_timer2_ops():
    # LRC delay shape: prd=200, cnt=1, con 0xc01 (mode 1, src 3, div 0).
    ops = [
        ("write", TIMER2 + 8, 200),
        ("write", TIMER2 + 4, 1),
        ("write", TIMER2, 0xc01),
        ("advance", 4), ("advance", 4), ("advance", 4),
        ("read", TIMER2), ("read", TIMER2 + 4),
        ("steps", 4),
        ("write", TIMER2, 0xc01 | 0x4000),  # W1C clear pending
        ("read", TIMER2),
        ("write", TIMER2, 0xc00),           # stop
        ("advance", 4), ("steps", 4),
        ("advance", -1),                     # ValueError
        ("write", TIMER2 + 12, 0),           # unsupported register
        ("read", TIMER2 + 12),
        ("write", TIMER2, 0x1401),           # source 5 not LRC -> blocked
        ("write", TIMER2, 0x1),              # mode 1 source 0 -> blocked
    ]
    return ops


def random_timer2_ops(rng, n):
    offs = [TIMER2, TIMER2 + 4, TIMER2 + 8, TIMER2 + 1, TIMER2 + 12, TIMER2 + 20]
    cons = [0xc00, 0xc01, 0xc01 | 0x4000, 0x0, 0x1401, 0x1, 0x3, 0xfcf3, 0xfcf4]
    ticks = [0, 1, 4, 7, 79, 80, 81, 4000, -1]
    rates = [1, 4, 80]
    ops = []
    for _ in range(n):
        k = rng.random()
        if k < 0.3:
            ops.append(("write", rng.choice(offs), rng.getrandbits(32)))
        elif k < 0.45:
            ops.append(("write", rng.choice(offs), rng.choice(cons)))
        elif k < 0.55:
            ops.append(("read", rng.choice(offs)))
        elif k < 0.85:
            ops.append(("advance", rng.choice(ticks)))
        else:
            ops.append(("steps", rng.choice(rates)))
    return ops


def test_timer2():
    py = Timer2Delay()
    go = GosimTimer2()
    run_timer2_pair(py, go, targeted_timer2_ops(), "timer2-targeted")
    rng = random.Random(0x72)
    run_timer2_pair(py, go, random_timer2_ops(rng, 6000), "timer2-random")
    print("Timer2Delay: PASS (Go matches br28_model.Timer2Delay byte-for-byte)")


# ---- SoftwareIrqCommands -------------------------------------------------------

def py_swirq_outcome(py, op):
    try:
        if op[0] == "command":
            py.command(op[1], op[2])
            return ("ok", None)
        if op[0] == "source_rmw":
            py.source_rmw(op[1], op[2], op[3])
            return ("ok", None)
        if op[0] == "ack":
            py.acknowledge(op[1], op[2])
            return ("ok", None)
        raise AssertionError(op)
    except Blocked as e:
        return ("error", e.details)
    except ValueError:
        return ("valueerror", None)


def _pw(w):
    return {"value": w.value, "known": w.known}


def py_swirq_state(py):
    return {
        "pending": [_pw(w) for w in py.pending],
        "source_enable": [_pw(w) for w in py.source_enable],
        "commands": py.commands,
        "acknowledgements": py.acknowledgements,
        "source_commands": py.source_commands,
    }


def run_swirq_pair(py, go, ops, label):
    for i, op in enumerate(ops):
        p = py_swirq_outcome(py, op)
        g = (go.command(op[1], op[2]) if op[0] == "command"
             else go.source_rmw(op[1], op[2], op[3]) if op[0] == "source_rmw"
             else go.acknowledge(op[1], op[2]))
        _check(label, i, op, g, p)
        if py_swirq_state(py) != go.state():
            raise AssertionError(
                f"[{label}] op#{i} {op!r} state mismatch:\n  go={go.state()}\n  py={py_swirq_state(py)}")


def targeted_swirq_ops():
    ops = [
        ("command", SWIRQ_BASES[0], 0x1),
        ("command", SWIRQ_BASES[0] + 4, 0x1),   # clear bit 0
        ("command", SWIRQ_BASES[1], 0x8),
        ("source_rmw", SWIRQ_SRC[0], "and", ~8 & 0xffffffff),
        ("source_rmw", SWIRQ_SRC[0], "or", 0x8),
        ("ack", 0, 0),
        ("command", SWIRQ_BASES[0], 0x10),      # mask outside low nibble -> blocked
        ("command", 0xdeadbeef, 0x1),           # unmodeled address -> blocked
        ("source_rmw", SWIRQ_SRC[0], "xor", 0x8),  # unmodeled form -> blocked
    ]
    return ops


def random_swirq_ops(rng, n):
    ops = []
    for _ in range(n):
        k = rng.random()
        core = rng.randrange(2)
        base = SWIRQ_BASES[core]
        src = SWIRQ_SRC[core]
        if k < 0.35:
            ops.append(("command", rng.choice([base, base + 4, 0xffff0000]),
                        rng.choice([0, 1, 8, 15, 16, 0xffffffff])))
        elif k < 0.6:
            ops.append(("source_rmw", rng.choice([src, 0xffff0000]),
                        rng.choice(["and", "or", "xor"]), rng.getrandbits(32)))
        else:
            ops.append(("ack", core, rng.randrange(4)))
    return ops


def test_swirq():
    configs = [
        dict(label="unknown", initial_pending=None, initial_source_enable=None),
        dict(label="explicit", initial_pending=(0, 8), initial_source_enable=(1, 2)),
    ]
    for cfg in configs:
        label = "swirq-" + cfg["label"]
        py = SoftwareIrqCommands(cfg["initial_pending"], cfg["initial_source_enable"])
        go = GosimSwIrq(cfg["initial_pending"], cfg["initial_source_enable"])
        run_swirq_pair(py, go, targeted_swirq_ops(), label + "-targeted")
        rng = random.Random(0x51A7 ^ sum(ord(c) for c in label))
        run_swirq_pair(py, go, random_swirq_ops(rng, 4000), label + "-random")
    print("SoftwareIrqCommands: PASS (Go matches br28_model.SoftwareIrqCommands byte-for-byte)")


if __name__ == "__main__":
    test_coretick()
    test_watchdog()
    test_icu()
    test_timer2()
    test_swirq()
    sys.exit(0)
