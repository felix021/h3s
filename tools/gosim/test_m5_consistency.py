#!/usr/bin/env python3
"""Differential test: Go M5 SRC-SYNC delivery (periph_m5.go) vs the real Python
br28_model.JLSrcSyncDelivery, byte-for-byte; plus a live Python-side check that
BR28Model.advance raises exactly the SRC-SYNC / ADC-DMA delivery outcomes the Go
periph_m5_test.go pins.

Part 1 follows test_m4_consistency.py: JLSrcSyncDelivery is directly importable,
so this drives the REAL Python model and the Go model side by side over targeted
and randomized operation sequences and compares every outcome plus the full
advance-relevant post-op state. The SRCSYNC6 cursor half (JLSrcSyncCursorProgress)
is out of M5 scope: both sides run with cursor=None (the Python default).

Part 2 constructs a minimal BR28Model with the SRC-SYNC / ADC-DMA delivery flags
on and asserts BR28Model.advance raises IrqDeliveryRequest / Blocked with the
exact details the Go periph_m5_test.go strict-stop JSON literals encode.

Usage:
    cd tools/gosim && go build -buildmode=c-shared -o libgosim.so ./cshared
    python3 tools/gosim/test_m5_consistency.py
"""
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tools" / "gosim"))

from probe_boot import Blocked, IrqDeliveryRequest  # noqa: E402
from br28_model import BR28Model, JLSrcSyncDelivery  # noqa: E402
from gosim_m5 import GosimSrcSync  # noqa: E402


class _FakeHW:
    """Minimal hardware stand-in exposing counts['completed_model_steps'], the
    only field JLSrcSyncDelivery.note_start / .advance read."""

    def __init__(self):
        self.counts = {"completed_model_steps": 0}


# ---- Part 1: JLSrcSyncDelivery leaf model -----------------------------------

def py_srcsync_outcome(py, hw, op):
    if op[0] == "note_start":
        hw.counts["completed_model_steps"] = op[1]
        py.note_start(hw)
        return ("ok", None)
    if op[0] == "advance":
        hw.counts["completed_model_steps"] = op[1]
        py.advance(hw)
        return ("ok", None)
    if op[0] == "note_con_write":
        py.note_con_write(op[1])
        return ("ok", None)
    if op[0] == "reentry":
        return ("ok", py.reentry_authorized())
    raise AssertionError(op)


def go_srcsync_outcome(go, op):
    if op[0] == "note_start":
        go.note_start(op[1])
        return ("ok", None)
    if op[0] == "advance":
        go.advance(op[1])
        return ("ok", None)
    if op[0] == "note_con_write":
        go.note_con_write(op[1])
        return ("ok", None)
    if op[0] == "reentry":
        return ("ok", go.reentry_authorized())
    raise AssertionError(op)


def py_srcsync_state(py):
    return {
        "completion_steps": py.completion_steps,
        "started": py.started,
        "start_step": py.start_step,
        "pending": py.pending,
        "delivery_in_flight": py.delivery_in_flight,
        "starts": py.starts,
        "completions": py.completions,
        "acks": py.acks,
    }


def run_srcsync_pair(py, go, ops, label):
    hw = _FakeHW()
    for i, op in enumerate(ops):
        p = py_srcsync_outcome(py, hw, op)
        g = go_srcsync_outcome(go, op)
        if p != g:
            raise AssertionError(
                f"[{label}] op#{i} {op!r} outcome mismatch:\n  go={g}\n  py={p}")
        if py_srcsync_state(py) != go.state():
            raise AssertionError(
                f"[{label}] op#{i} {op!r} state mismatch:\n  go={go.state()}\n  py={py_srcsync_state(py)}")


def targeted_srcsync_ops():
    return [
        ("advance", 0),
        ("note_start", 5),
        ("advance", 5), ("advance", 6), ("advance", 14),  # 14-5=9 < 10
        ("reentry",),
        ("advance", 15),  # 15-5=10 -> completes
        ("reentry",),
        ("note_con_write", 0x40),   # ACK clears pending
        ("note_con_write", 0x8),    # no ACK bit
        ("reentry",),
        ("note_start", 100),        # restart
        ("advance", 100), ("advance", 109),
        ("note_con_write", 0x48),   # ACK bit + start bit
        ("advance", 110),
    ]


def random_srcsync_ops(rng, n):
    steps = [0, 1, 5, 9, 10, 11, 100, 999, 40000, 40001]
    values = [0, 0x8, 0x40, 0x48, 0xffffffff, 0x100]
    ops = []
    for _ in range(n):
        k = rng.random()
        if k < 0.25:
            ops.append(("note_start", rng.choice(steps)))
        elif k < 0.75:
            ops.append(("advance", rng.choice(steps)))
        elif k < 0.9:
            ops.append(("note_con_write", rng.choice(values)))
        else:
            ops.append(("reentry",))
    return ops


def test_srcsync():
    for steps in (10, 40000):
        label = f"srcsync-{steps}"
        py = JLSrcSyncDelivery(completion_steps=steps)
        go = GosimSrcSync(steps)
        run_srcsync_pair(py, go, targeted_srcsync_ops(), label + "-targeted")
        rng = random.Random(0x5E5 ^ steps)
        run_srcsync_pair(py, go, random_srcsync_ops(rng, 6000), label + "-random")
    print("JLSrcSyncDelivery: PASS (Go matches br28_model.JLSrcSyncDelivery byte-for-byte)")


# ---- Part 2: BR28Model.advance delivery scans (Python live check) ------------

def build_advance_model(delivery_requests):
    cfg = [None] * 32
    cfg[8] = 0x5 << (4 * 4)    # IRQ68: enabled, priority 2
    cfg[11] = 0x5 << (1 * 4)   # IRQ89: enabled, priority 2
    return BR28Model(
        jl_wl_aud_write_records=True,
        jl_src_sync_config_words=True, jl_src_sync_full_block=True,
        jl_src_sync_delivery=True, jl_src_sync_delivery_requests=delivery_requests,
        jl_audio_adc_dma_writes=True, jl_audio_adc_reset_write_records=True,
        audio_adc_dma_delivery=True, audio_adc_dma_delivery_requests=delivery_requests,
        initial_irq_config=cfg, initial_ipmask=2,
    )


def _assert_irq(hw, icfg, want_core, want_irq):
    try:
        hw.advance(icfg)
    except IrqDeliveryRequest as e:
        assert e.details == {"core": want_core, "irq": want_irq}, e.details
        return
    raise AssertionError(f"expected IrqDeliveryRequest(core={want_core},irq={want_irq})")


def _assert_blocked(hw, icfg, want_details):
    try:
        hw.advance(icfg)
    except Blocked as e:
        assert e.details == want_details, e.details
        return
    raise AssertionError(f"expected Blocked {want_details}")


def test_advance_delivery_scans():
    # ADC-DMA pending + eligible -> IrqDeliveryRequest(core=0, irq=68)
    hw = build_advance_model(True)
    hw.jl_audio_adc_dma.pending = True
    _assert_irq(hw, 0x300, 0, 68)

    # ADC-DMA pending + eligible + requests off -> Blocked
    hw = build_advance_model(False)
    hw.jl_audio_adc_dma.pending = True
    _assert_blocked(hw, 0x300, {
        "kind": "interrupt_delivery_not_implemented", "core": 0, "irq": 68,
        "note": "audio ADC DMA pending and eligible; CPU entry not bypassed — "
                "enable the audio ADC DMA delivery requests"})

    # ADC-DMA eligible raises Blocked (unknown ipmask) -> wrapped
    hw = build_advance_model(True)
    hw.jl_audio_adc_dma.pending = True
    hw.irq.ipmask = None
    _assert_blocked(hw, 0x300, {
        "kind": "audio_dma_irq_delivery_blocked", "core": 0, "irq": 68,
        "reason": "uninitialized_ipmask"})

    # SRC-SYNC pending + eligible -> IrqDeliveryRequest(core=0, irq=89)
    hw = build_advance_model(True)
    hw.jl_src_sync_delivery.pending = True
    _assert_irq(hw, 0x300, 0, 89)

    # SRC-SYNC pending + eligible + requests off -> Blocked
    hw = build_advance_model(False)
    hw.jl_src_sync_delivery.pending = True
    _assert_blocked(hw, 0x300, {
        "kind": "interrupt_delivery_not_implemented", "core": 0, "irq": 89,
        "note": "SRC_SYNC completion pending and eligible; CPU entry not bypassed "
                "— enable the SRC_SYNC delivery requests"})

    # SRC-SYNC eligible raises Blocked -> wrapped
    hw = build_advance_model(True)
    hw.jl_src_sync_delivery.pending = True
    hw.irq.ipmask = None
    _assert_blocked(hw, 0x300, {
        "kind": "src_sync_irq_delivery_blocked", "core": 0, "irq": 89,
        "reason": "uninitialized_ipmask"})

    # SRC-SYNC completion end-to-end: note_start at step 0 -> delivery at step 40000
    hw = build_advance_model(True)
    hw.jl_src_sync_delivery.note_start(hw)
    got = None
    for i in range(1, 40001):
        try:
            hw.advance(0x300)
        except IrqDeliveryRequest as e:
            got = (i, e.details)
            break
    assert got == (40000, {"core": 0, "irq": 89}), got
    assert hw.jl_src_sync_delivery.pending and not hw.jl_src_sync_delivery.started
    print("BR28Model.advance SRC-SYNC/ADC-DMA delivery scans: PASS "
          "(Python live outcomes match the Go-pinned literals)")


if __name__ == "__main__":
    test_srcsync()
    test_advance_delivery_scans()
    sys.exit(0)
