#!/usr/bin/env python3
"""Differential check: Go dual-core advance scan (periph.go Advance multicore)
vs the real Python BR28Model.advance multicore delivery scans.

The Go periph_dualcore_test.go pins the exact IrqDeliveryRequest(core,irq) and
Blocked detail dicts for the dual-core core-tick (IRQ3), Timer0 (IRQ4) and
software-IRQ (IRQ124-127) scans. This test constructs a real multicore BR28Model
and asserts BR28Model.advance raises those same outcomes live, so the Go literals
are the byte-for-byte witness of the Python reference.

Usage:
    cd tools/gosim && go build -buildmode=c-shared -o libgosim.so ./cshared
    python3 tools/gosim/test_m6_advance_consistency.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

from probe_boot import Blocked, IrqDeliveryRequest  # noqa: E402
from br28_model import BR28Model  # noqa: E402


def build_multicore(irq_cfg0=None, irq_cfg1=None, ipmask0=2, ipmask1=2,
                    timer0_requests=False, swirq_pending=None, swirq_requests=False):
    hw = BR28Model(
        cpu1_held_projection=True,
        initial_cpu1_control=0,
        core_tick_configuration=True,
        initial_core_tick_controls=(0, 0),
        core_tick_progress=(4, 4),
        core_tick_delivery_requests=True,
        initial_irq_config=irq_cfg0,
        initial_ipmask=ipmask0,
        timer0_irq_delivery_requests=timer0_requests,
        software_irq_command_projection=swirq_pending is not None,
        initial_software_irq_pending=swirq_pending,
        software_irq_delivery_requests=swirq_requests,
    )
    hw.enable_multicore(irq_cfg1, ipmask1, lambda value: None)
    hw.core_icfg = {0: 0x300, 1: 0x300}
    hw.cpu1_control = 1
    return hw


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


def test_core_tick_dualcore():
    cfg0 = [None] * 32
    cfg0[0] = 0x5000  # irq 3 nibble 3: enabled, priority 2 (core 0)
    cfg1 = [None] * 32
    cfg1[0] = 0x5000  # core 1
    hw = build_multicore(cfg0, cfg1)
    hw.core_ticks[1].write(8, 3)
    hw.core_ticks[1].write(4, 3)
    hw.core_ticks[1].write(0, 0x1)
    _assert_irq(hw, 0x300, 1, 3)

    # core-1 tick counting without the held projection -> Blocked
    hw = BR28Model(
        cpu1_held_projection=False,
        initial_cpu1_control=None,
        core_tick_configuration=True,
        initial_core_tick_controls=(0, 0),
        core_tick_progress=(4, 4),
        core_tick_delivery_requests=True,
        initial_irq_config=cfg0,
        initial_ipmask=2,
    )
    hw.core_ticks[1].write(8, 3)
    hw.core_ticks[1].write(4, 0)
    hw.core_ticks[1].write(0, 0x1)
    _assert_blocked(hw, 0x300, {"kind": "core1_execution_state_unknown"})
    print("dual-core core tick (IRQ3): PASS")


def test_timer0_dualcore():
    cfg0 = [None] * 32
    cfg0[0] = 0x50000  # irq 4 nibble 4: enabled, priority 2
    cfg1 = [None] * 32
    cfg1[0] = 0x50000
    hw = build_multicore(cfg0, cfg1, timer0_requests=True)
    hw.timer.pending = True
    _assert_irq(hw, 0x300, 0, 4)

    # unknown core-0 gate -> Blocked
    hw = build_multicore(cfg0, cfg1, timer0_requests=True)
    hw.core_icfg = {0: None, 1: 0x300}
    hw.timer.pending = True
    _assert_blocked(hw, 0x300, {"kind": "pending_irq_with_unknown_core_icfg", "core": 0, "irq": 4})
    print("dual-core Timer0 (IRQ4): PASS")


def test_software_irq_dualcore():
    cfg0 = [None] * 32
    cfg0[15] = 0x50000  # irq 124 nibble 4: enabled, priority 2
    cfg1 = [None] * 32
    cfg1[15] = 0x50000
    hw = build_multicore(cfg0, cfg1, swirq_pending=[0, 1], swirq_requests=True)
    _assert_irq(hw, 0x300, 1, 124)

    # unknown core-1 gate -> Blocked
    hw = build_multicore(cfg0, cfg1, swirq_pending=[0, 1], swirq_requests=True)
    hw.core_icfg = {0: 0x300, 1: None}
    _assert_blocked(hw, 0x300, {"kind": "software_irq_with_unknown_icfg", "core": 1, "irq": 124})

    # unmaskable re-wrap
    cfg1 = [None] * 32
    cfg1[15] = 0xf0000  # irq 124 nibble 4: enabled, priority 7
    hw = build_multicore(cfg0, cfg1, ipmask1=1, swirq_pending=[0, 1], swirq_requests=True)
    _assert_blocked(hw, 0x300, {
        "kind": "unmodeled_unmaskable_irq", "core": 1, "irq": 124,
        "priority": 7, "source": "software_irq", "pending_known": True,
        "icfg_gate_demand": "0x300"})
    print("dual-core software IRQ (IRQ124-127): PASS")


if __name__ == "__main__":
    test_core_tick_dualcore()
    test_timer0_dualcore()
    test_software_irq_dualcore()
    sys.exit(0)
