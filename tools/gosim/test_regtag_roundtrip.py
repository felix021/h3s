#!/usr/bin/env python3
"""Lossless GPR side-table round-trip through the cgo bridge.

Task 1/2 witness: UnknownWord / IcfgValue GPR slots must survive
core_state -> gosim_world_dual_set_cores -> run_loop(0) -> res['cores'] byte and
field exactly, reusing world_snapshot's abstract field lists (bytes/origins and
value/known/origin/identity). Segment overwrite clears the tag on the Go side
(syncCoreFromCPU) and the concrete 'r' placeholder stays 0 for abstract slots.

Usage:
    cd tools/gosim && go build -buildmode=c-shared -o libgosim.so ./cshared
    python3 tools/gosim/test_regtag_roundtrip.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tools" / "gosim"))

from types import SimpleNamespace  # noqa: E402

import gosim as gosim_glue  # noqa: E402
from br28_icfg import IcfgValue  # noqa: E402
from br28_unknown import UnknownWord  # noqa: E402
from gosim_dual_engine import core_state, restore_core  # noqa: E402
from gosim_world import GosimDualWorld  # noqa: E402


def fake_cpu(core_id, R):
    return SimpleNamespace(
        core_id=core_id, R=R, flags=0, sp=0, sp_known=True, rets=0, pc=0x100,
        icfg=0x300, icfg_known=True, icfg_state=None, steps=0, repeat=None,
        after_then=None, usp=None, ssp=None, reti=None, irq_psr=None,
        active_context=None, bootstrap_context=False, last_explicit_ssp=None,
        audio_irq_outer_context=None,
    )


def assert_reg_equal(actual, expected, path):
    if type(actual) is not type(expected):
        raise AssertionError(f"{path}: type {type(actual).__name__} != "
                             f"{type(expected).__name__}")
    if isinstance(expected, UnknownWord):
        if actual.bytes != expected.bytes or actual.origins != expected.origins:
            raise AssertionError(
                f"{path}: UnknownWord differs bytes={actual.bytes} origins={actual.origins} "
                f"vs {expected.bytes} {expected.origins}")
        return
    if isinstance(expected, IcfgValue):
        for f in ('value', 'known', 'origin', 'identity'):
            if getattr(actual, f) != getattr(expected, f):
                raise AssertionError(
                    f"{path}.{f}: {getattr(actual, f)!r} != {getattr(expected, f)!r}")
        return
    if actual != expected:
        raise AssertionError(f"{path}: {actual!r} != {expected!r}")


def roundtrip(R0, R1):
    c0 = fake_cpu(0, R0)
    c1 = fake_cpu(1, R1)
    cs0 = core_state(c0)
    cs1 = core_state(c1)

    go0 = gosim_glue.Gosim(arch=True)
    go1 = gosim_glue.Gosim(arch=True)
    world = GosimDualWorld(go0.handle, go1.handle, 0, 0, 0, 0, 0xFFFFFFFF)
    try:
        world.set_cores([cs0, cs1])
        res = world.run_loop(0)
    finally:
        world.free()

    cores = res.get('cores') or [None, None]
    back0 = fake_cpu(0, [0] * 16)
    back1 = fake_cpu(1, [0] * 16)
    restore_core(back0, cores[0])
    restore_core(back1, cores[1])

    for i, (a, e) in enumerate(zip(back0.R, R0)):
        assert_reg_equal(a, e, f"core0.R[{i}]")
    for i, (a, e) in enumerate(zip(back1.R, R1)):
        assert_reg_equal(a, e, f"core1.R[{i}]")


def main():
    concrete = [0x12345678, 0, 0xFFFFFFFF, 0x100] + [0] * 12

    # UnknownWord: two unknown bytes with physical provenance, two known bytes.
    unknown = UnknownWord((None, 0x12, None, 0x34), (0x1000, None, 0x2000, None))
    R0 = concrete[:]
    R0[3] = unknown

    # IcfgValue: fully known value but retaining gate identity; plus a partial
    # one whose identity is 0 (exercises a non-omitempty scalar).
    icfg_full = IcfgValue(0x300, 0xFFFFFFFF, (0, 7), 0x300)
    icfg_zero_id = IcfgValue(0x0, 0x0, (1, 3), 0)
    R1 = concrete[:]
    R1[5] = icfg_full
    R1[9] = icfg_zero_id

    roundtrip(R0, R1)

    # A pure concrete core must round-trip as concrete ints (no spurious tags).
    roundtrip(concrete, concrete)

    print("reg-tag round-trip: PASS (UnknownWord/IcfgValue GPR slots survive "
          "core_state -> cgo -> res['cores'] field-exactly)")


if __name__ == "__main__":
    main()
