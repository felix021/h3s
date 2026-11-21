#!/usr/bin/env python3
"""Differential test: the minimal single-core World.RunLoop cgo exit vs the
Python step-by-step reference, byte-for-byte.

RunLoop here runs the configuration the second wiring targets: core 0 + strict
memory + an EMPTY Periph + an EMPTY after_step, so the advance and after_step
phases are provably no-ops and RunLoop reduces to the stage-1/2 segment batch.
The per-step observations (pc / next_pc / flags / 16 registers) must be
byte-identical to the Python reference on real firmware stretches.

This is the "wrap RunSegment in RunLoop" proof: test_batch_consistency.py
already proves segment.go == the Python step-by-step reference, so here we prove
World.RunLoop == that same reference (transitively RunLoop == run_segment).

Usage:
    cd tools/gosim && go build -buildmode=c-shared -o libgosim.so ./cshared
    python3 tools/gosim/test_world_runloop_consistency.py
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
import test_batch_consistency as tbc  # noqa: E402
from gosim_world import GosimWorld  # noqa: E402


def bench(segs, repeats, seed):
    """Cold-cache wall-clock: Python step-by-step vs one World.RunLoop call,
    both from a cold decode cache (fresh CPU per rep)."""
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

    # Python step-by-step, cold decode cache every rep.
    t0 = time.perf_counter()
    for _ in range(repeats):
        tbc.run_reference(py_cpu, mem, start_pc, R, flags, sp, rets, N, clear_cache=True)
    py_sec = time.perf_counter() - t0

    # Go World.RunLoop, one reused CPU handle with a cold decode cache every rep
    # (the same cold-cache discipline as the Python side; the one-time app load
    # is outside the timed loop, as in the real frontier where the engine is
    # reused across the whole explore run).
    go = gosim_glue.Gosim(arch=True)
    go.write(tbc.ENTRY, tbc.app)
    t0 = time.perf_counter()
    for _ in range(repeats):
        go.clear_cache()
        go.set_regs(R)
        go.set_state(flags, sp, rets, start_pc)
        w = GosimWorld(go.handle, 0, lo, hi)
        w.run_loop(N)
        w.free()
    go_sec = time.perf_counter() - t0

    print(f"bench segment @{start_pc:#010x} len={N} repeats={repeats} (cold cache per rep)")
    print(f"  python step-by-step : {py_sec * 1e6 / (repeats * N):.3f} us/step "
          f"({py_sec * 1e6 / repeats:.2f} us/segment)")
    print(f"  go World.RunLoop    : {go_sec * 1e6 / (repeats * N):.3f} us/step "
          f"({go_sec * 1e6 / repeats:.2f} us/segment)")
    print(f"  speedup             : {py_sec / go_sec:.1f}x")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--bench", action="store_true")
    ap.add_argument("--bench-repeats", type=int, default=5000)
    args = ap.parse_args()

    app = tbc.application(tbc.OFFICIAL)
    tbc.app = app
    segs = tbc.find_segments(app, min_len=2, cap=150)
    if not segs:
        print("RESULT: FAIL — no safe segments found")
        return 1

    if args.bench:
        bench(segs, args.bench_repeats, 0x0A11)
        return 0

    rng = random.Random(0x0A11)
    py_cpu = tbc.make_py_cpu()
    mem = tbc.RecordingMemory()
    mem.write(tbc.ENTRY, app)
    py_cpu.mem = mem

    go = gosim_glue.Gosim(arch=True)
    go.write(tbc.ENTRY, app)

    tested = 0
    steps = 0
    for seg in segs:
        R = [rng.getrandbits(32) for _ in range(16)]
        flags = rng.randrange(16)
        sp = rng.getrandbits(32)
        rets = rng.getrandbits(32) & tbc.RETS_MASK
        start_pc = seg[0][0]
        cap = len(seg) + 4

        obs = tbc.run_reference(py_cpu, mem, start_pc, R, flags, sp, rets, cap)
        L = len(obs)

        go.set_regs(R)
        go.set_state(flags, sp, rets, start_pc)
        world = GosimWorld(go.handle, 0, tbc.ENTRY, tbc.ENTRY + len(app))
        res = world.run_loop(cap)
        world.free()

        if res["exit"] != 1:  # Stop
            print(f"\nMISMATCH segment@{start_pc:#010x}: expected Stop exit, got {res}")
            return 1
        gsteps = [(s["pc"], s["next_pc"], s["flags"], tuple(s["r"])) for s in res["steps"]]
        want = [(pc, npc, fl, tuple(Rs)) for pc, npc, fl, Rs, _ in obs]
        if gsteps != want:
            print(f"\nMISMATCH segment@{start_pc:#010x}: ref_steps={L} go_steps={len(gsteps)}")
            n = min(len(want), len(gsteps))
            i = next((i for i in range(n) if want[i] != gsteps[i]), n)
            print(f"  first step diff at {i}:")
            print(f"    want {want[i] if i < len(want) else None}")
            print(f"    got  {gsteps[i] if i < len(gsteps) else None}")
            return 1
        if len(gsteps) != L:
            print(f"\nMISMATCH segment@{start_pc:#010x}: step count ref={L} go={len(gsteps)}")
            return 1
        tested += 1
        steps += L

    print(f"RESULT: PASS — {tested} segments, {steps} steps, World.RunLoop byte-for-byte "
          f"identical to the Python step-by-step reference (empty advance/after_step)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
