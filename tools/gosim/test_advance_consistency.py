#!/usr/bin/env python3
"""Differential test: the Go whole-loop advance chain (periph_state.go
PeriphState.Advance) vs the real Python BR28Model.advance, byte-for-byte.

This is the "advance state transfer" parity for the M3-M4 models (Timer0 /
IRQController / CoreTickStopped): the live Python peripheral state is moved into
a Go PeriphState as JSON (con/cnt/prd/remainder/source_remainder/expirations/
delivery_in_flight, the 32 ICFG config words + ipmask, the core-tick
unresolved_overflow/pending/writes/acknowledgements/...), one advance step runs
on both engines, and the outcome (Blocked details / IrqDeliveryRequest(core,irq)
/ clean) plus the full resulting state are compared value-for-value. Any field
left out of the transfer drifts the byte output and is caught here.

Follows test_timer0_irq_consistency.py / test_m4_consistency.py, but exercises
the advance *orchestrator* (BR28Model.advance) rather than a single leaf model.

Usage:
    cd tools/gosim && go build -buildmode=c-shared -o libgosim.so ./cshared
    python3 tools/gosim/test_advance_consistency.py
"""
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tools" / "gosim"))

from probe_boot import Blocked, IrqDeliveryRequest  # noqa: E402
from br28_model import BR28Model  # noqa: E402
from gosim_world import GosimPeriph  # noqa: E402


# ---- state extraction (mirrors periph_state.go JSON keys) ----------------------

def timer_state(t):
    return {
        "allowed_sources": list(t.allowed_sources),
        "con": t.con, "cnt": t.cnt, "prd": t.prd,
        "pending": t.pending,
        "remainder": t.remainder,
        "source_remainder": t.source_remainder,
        "expirations": t.expirations,
        "delivery_in_flight": t.delivery_in_flight,
    }


def irq_state(ic):
    return {
        "config": list(ic.config),
        "ipmask": ic.ipmask,
        "unmaskable_irq_projection": ic.unmaskable_irq_projection,
    }


def coretick_state(t):
    return {
        "con": t.con, "cnt": t.cnt, "prd": t.prd, "pending": t.pending,
        "writes": t.writes, "progress": t.progress,
        "expirations": t.expirations,
        "unresolved_overflow": t.unresolved_overflow,
        "acknowledgements": t.acknowledgements,
        "delivery_in_flight": t.delivery_in_flight,
        "prd_write": t.prd_write,
        "prd_readback": t.prd_readback,
        "cnt_held_snapshot": t.cnt_held_snapshot,
        "freerun_edge_period": t.freerun_edge_period,
        "cnt_snapshot_probe_value": t.cnt_snapshot_probe_value,
    }


def py_state(hw):
    core_icfg = [hw.core_icfg.get(0), hw.core_icfg.get(1)]
    core_ticks = [coretick_state(t) for t in hw.core_ticks] if hw.core_ticks else [None, None]
    return {
        "source_ticks_per_step": hw.source_ticks_per_step,
        "timer_lsb_ticks_per_step": hw.timer_lsb_ticks_per_step,
        "multicore_enabled": hw.multicore_enabled,
        "core_icfg": core_icfg,
        "cpu1_control": hw.cpu1_control,
        "cpu1_held_projection": hw.cpu1_held_projection,
        "core_tick_count_while_held": hw.core_tick_count_while_held,
        "core_tick_delivery_requests": hw.core_tick_delivery_requests,
        "timer0_irq_delivery_requests": hw.timer0_irq_delivery_requests,
        "software_irq_delivery_requests": hw.software_irq_delivery_requests,
        "src_sync_delivery_requests": hw.jl_src_sync_delivery_requests,
        "audio_adc_dma_delivery_requests": hw.audio_adc_dma_delivery_requests,
        "completed_model_steps": hw.counts["completed_model_steps"],
        "timer": timer_state(hw.timer),
        "irq": irq_state(hw.irq),
        "irq1": irq_state(hw.irq1) if hw.irq1 is not None else None,
        "core_ticks": core_ticks,
    }


# ---- outcome helpers ----------------------------------------------------------

def py_advance(hw, icfg):
    try:
        hw.advance(icfg)
        return ("clean", None)
    except Blocked as e:
        return ("blocked", e.details)
    except IrqDeliveryRequest as e:
        return ("irq", e.details)
    except ValueError:
        return ("valueerror", None)


def go_outcome_to_py(go_out):
    if go_out["err"] is not None:
        return ("blocked", go_out["err"])
    if go_out["irq"] is not None:
        return ("irq", {"core": go_out["irq"]["core"], "irq": go_out["irq"]["irq"]})
    return ("clean", None)


def _check(label, i, go, py, what):
    if go != py:
        raise AssertionError(
            f"[{label}] #{i} {what} mismatch:\n  go={go}\n  py={py}")


# ---- Python-only state mutation (build reachable live state) ------------------

def mutate_py(hw, rng):
    """Drive the Python model through real write/advance/ack ops so the live
    state carries fractional remainders / expirations / delivery_in_flight /
    unresolved_overflow, then let the transfer parity catch any missed field."""
    if hw.core_ticks is not None and rng.random() < 0.5:
        ct = hw.core_ticks[0]
        sub = rng.random()
        try:
            if sub < 0.45:
                ct.write(rng.choice([8, 4, 0]), rng.choice([9, 0, 1, 3, 0x41]))
            elif sub < 0.8:
                ct.advance()
            else:
                ct.ack_rmw("or", 0x40)
        except (Blocked, ValueError):
            pass
        return
    sub = rng.random()
    try:
        if sub < 0.4:
            off = rng.choice([8, 4, 0])
            val = rng.choice([9, 0, 0x1001, 0x1401, 0x1000, 0x1001 | 0x4000])
            hw.timer.write(off, val)
        elif sub < 0.7:
            hw.timer.advance(rng.choice([4, 1, 7, 12]))
        else:
            hw.advance(rng.choice([0x300, 0x200, 0x000]))
    except (Blocked, ValueError, IrqDeliveryRequest):
        pass


# ---- configs ------------------------------------------------------------------

def build_configs():
    # ICFG word 0: nibble 3 = irq3 enabled prio2, nibble 4 = irq4 enabled prio2.
    cfg = [None] * 32
    cfg[0] = 0x55000
    base = dict(initial_irq_config=cfg, initial_ipmask=2)
    configs = [
        dict(label="timer-counting", kwargs=dict(base)),
        dict(label="timer-delivery", kwargs=dict(base, timer0_irq_delivery_requests=True)),
        dict(label="timer-std12m", kwargs=dict(base, timer0_std12m_source=True)),
        dict(label="timer-lsb", kwargs=dict(base, timer_lsb_ticks_per_step=4)),
        dict(label="coretick", kwargs=dict(base, core_tick_configuration=True,
                                           initial_core_tick_controls=(0, 0),
                                           core_tick_progress=(4, 4))),
        dict(label="coretick-delivery", kwargs=dict(base, core_tick_configuration=True,
                                                    initial_core_tick_controls=(0, 0),
                                                    core_tick_progress=(4, 4),
                                                    core_tick_delivery_requests=True)),
        dict(label="timer+coretick", kwargs=dict(base, core_tick_configuration=True,
                                                 initial_core_tick_controls=(0, 0),
                                                 core_tick_progress=(4, 4),
                                                 timer0_irq_delivery_requests=True,
                                                 core_tick_delivery_requests=True)),
    ]
    return configs


def run_config(hw, go, label, rng):
    icfgs = [0x300, 0x300, 0x200, 0x100, 0x000, 0x3ff, 0x2ff]
    # seed the timer into a valid counting shape before the first transfer
    try:
        hw.timer.write(8, 9)
        hw.timer.write(4, 0)
        hw.timer.write(0, 0x1001)
    except Blocked:
        pass
    for rnd in range(12):
        for _ in range(rng.randrange(1, 6)):
            mutate_py(hw, rng)
        go.load(py_state(hw))
        _check(label, f"transfer-{rnd}", go.state(), py_state(hw), "state")
        for _ in range(rng.randrange(1, 9)):
            icfg = rng.choice(icfgs)
            py = py_advance(hw, icfg)
            go_out = go.advance(icfg)
            _check(label, rnd, go_outcome_to_py(go_out), py, "outcome")
            _check(label, rnd, go.state(), py_state(hw), "state")


def test_advance():
    for cfg in build_configs():
        label = cfg["label"]
        hw = BR28Model(**cfg["kwargs"])
        go = GosimPeriph()
        rng = random.Random(0xAD ^ sum(ord(c) for c in label))
        run_config(hw, go, label, rng)
        go.free()
        print(f"advance [{label}]: PASS (Go PeriphState.Advance matches BR28Model.advance byte-for-byte)")


if __name__ == "__main__":
    test_advance()
    sys.exit(0)
