#!/usr/bin/env python3
"""Differential test: the interleaved execute+advance RunLoop path via cgo vs the
Python step-by-step reference, byte-for-byte.

This is the third-wiring exit: gosim_world_set_periph binds a non-empty
PeriphState (a counting model), so World.RunLoop takes the interleaved path —
one execute step, then one advance step, stopping at the first pending-eligible
delivery request (or strict stop). The per-step observations (pc / next_pc /
flags / 16 registers) and the delivery stop must be byte-identical to the Python
reference on real firmware stretches.

Two arms:

* counting-no-delivery: a Timer0 counting at a large period never overflows, so
  RunLoop interleaves execute+advance for the whole budget and the execute steps
  match the stage-1/2 segment reference (test_batch_consistency.py).
* immediate-delivery: a Timer0 with period 1 overflows on the first advance and,
  with IRQ4 enabled + delivery requests on + ICFG gates 0x300, RunLoop stops
  after one execute step with Irq(core=0, irq=4) — the delivery is handed back to
  Python, never fabricated.

Usage:
    cd tools/gosim && go build -buildmode=c-shared -o libgosim.so ./cshared
    python3 tools/gosim/test_interleaved_consistency.py            # parity
    python3 tools/gosim/test_interleaved_consistency.py --bench    # cold-cache
"""
import argparse
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tools" / "gosim"))

import gosim as gosim_glue  # noqa: E402
import test_advance_consistency as tac  # noqa: E402
import test_batch_consistency as tbc  # noqa: E402
from br28_model import BR28Model  # noqa: E402
from probe_boot import IrqDeliveryRequest  # noqa: E402
from gosim_world import GosimPeriph, GosimWorld  # noqa: E402


def counting_periph_state():
    """A Timer0 counting at a huge period (never overflows in a segment)."""
    hw = BR28Model(initial_irq_config=[None] * 32, initial_ipmask=2)
    hw.timer.write(8, 0xffff)
    hw.timer.write(4, 0)
    hw.timer.write(0, 0x1001)  # mode 1, RC16M source, div 0
    return tac.py_state(hw), hw


def delivery_periph_state():
    """A Timer0 with period 1 + IRQ4 enabled + delivery requests: overflows on
    the first advance and hands back Irq(core=0, irq=4)."""
    cfg = [None] * 32
    cfg[0] = 0x50000  # irq 4 nibble 4: enabled, priority 2
    hw = BR28Model(initial_irq_config=cfg, initial_ipmask=2,
                   timer0_irq_delivery_requests=True)
    hw.timer.write(8, 0)
    hw.timer.write(4, 0)
    hw.timer.write(0, 0x1001)
    return tac.py_state(hw), hw


def run_go_interleaved(go, periph, start_pc, R, flags, sp, rets, n, icfg, lo, hi):
    go.clear_cache()
    go.set_regs(R)
    go.set_state(flags, sp, rets, start_pc)
    go.set_icfg(icfg)
    world = GosimWorld(go.handle, 0, lo, hi)
    world.set_periph(periph.handle)
    res = world.run_loop(n)
    world.free()
    return res


def go_steps(res):
    return [(s["pc"], s["next_pc"], s["flags"], tuple(s["r"])) for s in res["steps"]]


def py_counting_reference(py_cpu, mem, start_pc, R, flags, sp, rets, n):
    """Step n safe instructions following branches; no advance needed (the
    counting timer never overflows, so the advance never throws)."""
    return tbc.run_reference(py_cpu, mem, start_pc, R, flags, sp, rets, n, clear_cache=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--bench", action="store_true")
    ap.add_argument("--bench-repeats", type=int, default=3000)
    args = ap.parse_args()

    app = tbc.application(tbc.OFFICIAL)
    tbc.app = app
    segs = tbc.find_segments(app, min_len=2, cap=60)
    if not segs:
        print("RESULT: FAIL — no safe segments found")
        return 1

    rng = random.Random(0x1E11)
    py_cpu = tbc.make_py_cpu()
    mem = tbc.RecordingMemory()
    mem.write(tbc.ENTRY, app)
    py_cpu.mem = mem

    go = gosim_glue.Gosim(arch=True)
    go.write(tbc.ENTRY, app)
    lo, hi = tbc.ENTRY, tbc.ENTRY + len(app)

    if args.bench:
        bench(go, segs, args.bench_repeats, 0x0A11)
        return 0

    counting_state, _ = counting_periph_state()
    periph = GosimPeriph()
    periph.load(counting_state)

    tested = 0
    steps = 0
    for seg in segs:
        R = [rng.getrandbits(32) for _ in range(16)]
        flags = rng.randrange(16)
        sp = rng.getrandbits(32)
        rets = rng.getrandbits(32) & tbc.RETS_MASK
        start_pc = seg[0][0]
        cap = len(seg) + 4

        obs = py_counting_reference(py_cpu, mem, start_pc, R, flags, sp, rets, cap)
        L = len(obs)

        res = run_go_interleaved(go, periph, start_pc, R, flags, sp, rets, cap,
                                 0x02050500, lo, hi)
        if res["exit"] != 1:
            print(f"\nMISMATCH segment@{start_pc:#010x}: expected Stop exit, got {res}")
            return 1
        gsteps = go_steps(res)
        want = [(pc, npc, fl, tuple(Rs)) for pc, npc, fl, Rs, _ in obs]
        if gsteps != want:
            print(f"\nMISMATCH segment@{start_pc:#010x}: ref_steps={L} go_steps={len(gsteps)}")
            n = min(len(want), len(gsteps))
            i = next((i for i in range(n) if want[i] != gsteps[i]), n)
            print(f"  first step diff at {i}: want {want[i] if i < len(want) else None} "
                  f"got {gsteps[i] if i < len(gsteps) else None}")
            return 1
        if res["irq"] is not None:
            print(f"\nMISMATCH segment@{start_pc:#010x}: counting timer threw unexpected irq {res['irq']}")
            return 1
        tested += 1
        steps += L
    periph.free()

    print(f"interleaved [counting-no-delivery]: PASS — {tested} segments, {steps} steps, "
          f"RunLoop execute+advance byte-identical to Python reference")

    # ---- immediate-delivery arm ------------------------------------------------
    deliv_state, deliv_hw = delivery_periph_state()
    periph = GosimPeriph()
    periph.load(deliv_state)

    # Python reference: step one instruction, then advance raises IrqDeliveryRequest.
    seg = segs[0]
    R = [rng.getrandbits(32) for _ in range(16)]
    flags = rng.randrange(16)
    sp = rng.getrandbits(32)
    rets = rng.getrandbits(32) & tbc.RETS_MASK
    start_pc = seg[0][0]

    obs = tbc.run_reference(py_cpu, mem, start_pc, R, flags, sp, rets, 1, clear_cache=True)
    assert len(obs) == 1, "immediate-delivery reference needs >= 1 safe step"
    py_irq = None
    try:
        deliv_hw.advance(0x300)
    except IrqDeliveryRequest as e:
        py_irq = e.details

    res = run_go_interleaved(go, periph, start_pc, R, flags, sp, rets, 1, 0x300, lo, hi)
    if res["exit"] != 1 or res["irq"] != {"core": 0, "irq": 4}:
        print(f"\nMISMATCH delivery arm: expected Stop + Irq(0,4), got {res}")
        return 1
    gsteps = go_steps(res)
    want = [(pc, npc, fl, tuple(Rs)) for pc, npc, fl, Rs, _ in obs]
    if gsteps != want:
        print(f"\nMISMATCH delivery arm steps: want {want} got {gsteps}")
        return 1
    if py_irq != {"core": 0, "irq": 4}:
        print(f"\nMISMATCH delivery arm Python reference irq: {py_irq}")
        return 1
    periph.free()

    print("interleaved [immediate-delivery]: PASS — RunLoop stops after one step with "
          "Irq(core=0, irq=4) handed back to Python")
    return 0


def bench(go, segs, repeats, seed):
    """Cold-cache wall-clock: Python step-by-step (with a per-step advance) vs one
    World.RunLoop interleaved call, both from a cold decode cache per rep."""
    seg = max(segs, key=len)
    N = len(seg)
    rng = random.Random(seed)
    R = [rng.getrandbits(32) for _ in range(16)]
    flags = rng.randrange(16)
    sp = rng.getrandbits(32)
    rets = rng.getrandbits(32) & tbc.RETS_MASK
    start_pc = seg[0][0]
    lo, hi = tbc.ENTRY, tbc.ENTRY + len(tbc.app)

    py_cpu = tbc.make_py_cpu()
    mem = tbc.RecordingMemory()
    mem.write(tbc.ENTRY, tbc.app)
    py_cpu.mem = mem

    state, _ = counting_periph_state()

    # Python step-by-step + advance, cold decode cache every rep.
    t0 = time.perf_counter()
    for _ in range(repeats):
        tbc.reset_py(py_cpu, R, flags, sp, rets, start_pc, clear_cache=True)
        mem.writes.clear()
        for _ in range(N):
            got = py_cpu.decode(py_cpu.pc)
            if got is None or not tbc.is_safe(got[0]):
                break
            py_cpu.step()
    py_sec = time.perf_counter() - t0

    # Go interleaved RunLoop, one reused CPU handle with a cold cache per rep.
    periph = GosimPeriph()
    t0 = time.perf_counter()
    for _ in range(repeats):
        periph.load(state)
        go.clear_cache()
        go.set_regs(R)
        go.set_state(flags, sp, rets, start_pc)
        w = GosimWorld(go.handle, 0, lo, hi)
        w.set_periph(periph.handle)
        w.run_loop(N)
        w.free()
    go_sec = time.perf_counter() - t0
    periph.free()

    print(f"bench interleaved segment @{start_pc:#010x} len={N} repeats={repeats} (cold cache per rep)")
    print(f"  python step+advance : {py_sec * 1e6 / (repeats * N):.3f} us/step "
          f"({py_sec * 1e6 / repeats:.2f} us/segment)")
    print(f"  go RunLoop interleaved : {go_sec * 1e6 / (repeats * N):.3f} us/step "
          f"({go_sec * 1e6 / repeats:.2f} us/segment)")
    print(f"  speedup             : {py_sec / go_sec:.1f}x")


if __name__ == "__main__":
    sys.exit(main())
