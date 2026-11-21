#!/usr/bin/env python3
"""Differential test: the dual-core World.RunLoop cgo exit vs the Python
step-by-step reference, byte-for-byte on the clean path, plus the honest
hand-back boundaries (after_step hook fire, delivery deferral).

This is the fourth-wiring exit: gosim_world_dual_new binds both execute engines
plus the M6A select Machine and the M6C DeliverMachine, so World.RunLoop takes
the dual-core path (select -> decode+execute -> after_step judgment -> advance ->
delivery dispatch) inside Go. Three arms:

* clean dual-core run: two cores execute the same safe-ALU stretch under
  round-robin select, with a counting-but-not-delivering Timer0 advance. The
  per-step observations (core / pc / next_pc / flags / 16 registers) must be
  byte-identical to the Python reference on real firmware stretches.
* after_step YIELD: a cfg523 fixture hook armed, one core executes one safe step
  landing on the parser entry; the loop hands the attempt back instead of
  fabricating the hook body.
* delivery deferral: a pending-eligible Timer0 IRQ4 lands on a core whose Timer0
  handler is in flight; the loop hands the request back to Python (the deferral
  bookkeeping stays Python), never fabricates a delivery.

Usage:
    cd tools/gosim && go build -buildmode=c-shared -o libgosim.so ./cshared
    python3 tools/gosim/test_dualcore_consistency.py            # parity + boundaries
    python3 tools/gosim/test_dualcore_consistency.py --bench    # cold-cache speedup
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
import test_interleaved_consistency as tic  # noqa: E402
from gosim_world import GosimDualWorld, GosimPeriph  # noqa: E402

CFG523_PARSER_ENTRY = 0x60010B4
ADD_R0_R0 = b"\x00\x18\x00\x00\x00\x00"  # 0x1800 little-endian: add r0, r0 (safe ALU)


def run_dualcore_reference(cpu0, cpu1, mem, start_pc, R0, R1, flags0, flags1, sp, rets, cap):
    """Round-robin select + one safe step per selected core, mirroring the Go
    dual-core loop's clean path. Returns [(core, before_pc, next_pc, flags, R)]."""
    tbc.reset_py(cpu0, R0, flags0, sp, rets, start_pc, clear_cache=True)
    tbc.reset_py(cpu1, R1, flags1, sp, rets, start_pc, clear_cache=True)
    mem.writes.clear()
    obs = []
    last_core = 1
    for _ in range(cap):
        core = 1 - last_core
        last_core = core
        c = cpu0 if core == 0 else cpu1
        before = c.pc
        got = c.decode(c.pc)
        if got is None or not tbc.is_safe(got[0]):
            break
        c.step()
        obs.append((core, before, c.pc, c.flags & 0xF, tuple(c.R)))
    return obs


def go_dual_steps(res):
    return [(s["core"], s["pc"], s["next_pc"], s["flags"], tuple(s["r"]))
            for s in res["steps"]]


def make_go_cpu(app, lo, hi):
    go = gosim_glue.Gosim(arch=True)
    go.write(lo, app)
    return go


def setup_dual(go0, go1, periph, lo, hi, start_pc0, start_pc1, R0, R1, flags0, flags1,
               sp, rets, icfg):
    go0.clear_cache()
    go0.set_regs(R0)
    go0.set_state(flags0, sp, rets, start_pc0)
    go0.set_icfg(icfg)
    go1.clear_cache()
    go1.set_regs(R1)
    go1.set_state(flags1, sp, rets, start_pc1)
    go1.set_icfg(icfg)
    world = GosimDualWorld(go0.handle, go1.handle, 0, 0, 0, lo, hi)
    if periph is not None:
        world.set_periph(periph.handle)
    return world


def clean_parity(segs, app):
    rng = random.Random(0xD0A1)
    py_cpu0 = tbc.make_py_cpu()
    py_cpu1 = tbc.make_py_cpu()
    mem = tbc.RecordingMemory()
    mem.write(tbc.ENTRY, app)
    py_cpu0.mem = mem
    py_cpu1.mem = mem

    lo, hi = tbc.ENTRY, tbc.ENTRY + len(app)
    go0 = make_go_cpu(app, lo, hi)
    go1 = make_go_cpu(app, lo, hi)

    state, _ = tic.counting_periph_state()
    periph = GosimPeriph()
    periph.load(state)

    tested = 0
    steps = 0
    for seg in segs:
        R0 = [rng.getrandbits(32) for _ in range(16)]
        R1 = [rng.getrandbits(32) for _ in range(16)]
        flags0 = rng.randrange(16)
        flags1 = rng.randrange(16)
        sp = rng.getrandbits(32)
        rets = rng.getrandbits(32) & tbc.RETS_MASK
        start_pc = seg[0][0]
        cap = 2 * len(seg) + 8

        obs = run_dualcore_reference(py_cpu0, py_cpu1, mem, start_pc, R0, R1, flags0, flags1,
                                     sp, rets, cap)
        L = len(obs)

        world = setup_dual(go0, go1, periph, lo, hi, start_pc, start_pc, R0, R1, flags0, flags1,
                           sp, rets, 0x02050500)
        res = world.run_loop(cap)
        world.free()

        if res["exit"] != 1:  # Stop
            print(f"\nMISMATCH segment@{start_pc:#010x}: expected Stop exit, got {res}")
            return 1
        gsteps = go_dual_steps(res)
        want = obs
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
    periph.free()

    print(f"dual-core [clean run]: PASS — {tested} segments, {steps} steps, "
          f"select+execute+advance byte-for-byte identical to the Python reference")
    return 0


def after_step_yield():
    lo = CFG523_PARSER_ENTRY - 0x10
    hi = CFG523_PARSER_ENTRY + 0x10
    start_pc = CFG523_PARSER_ENTRY - 2

    go0 = gosim_glue.Gosim(arch=True)
    go0.write(start_pc, ADD_R0_R0)
    go1 = gosim_glue.Gosim(arch=True)
    go1.write(start_pc, ADD_R0_R0)

    R0 = [0] * 16
    R1 = [0] * 16
    world = setup_dual(go0, go1, None, lo, hi, start_pc, start_pc, R0, R1, 0, 0, 0, 0, 0x300)
    world.set_afterstep({
        "cfg523_enabled": True, "cfg523_installed": False,
        "first_task_hypothesis": False, "observer_active": False,
        "first_task_setup_index": [-1, -1],
        "first_task_return_frame": [False, False],
    })
    res = world.run_loop(1)
    world.free()

    if res["exit"] != 1 or res["stop_pc"] != CFG523_PARSER_ENTRY:
        print(f"\nMISMATCH after_step YIELD: expected Stop at {CFG523_PARSER_ENTRY:#x}, got {res}")
        return 1
    if len(res["steps"]) != 1:
        print(f"\nMISMATCH after_step YIELD: expected 1 committed step, got {len(res['steps'])}")
        return 1
    if "after_step hook fires" not in res["reason"]:
        print(f"\nMISMATCH after_step YIELD: reason must name the hook, got {res['reason']!r}")
        return 1
    print("dual-core [after_step YIELD]: PASS — the hook fire hands the attempt back, "
          "nothing fabricated")
    return 0


def delivery_deferral():
    lo, hi = 0x100, 0x200
    start_pc = 0x100

    go0 = gosim_glue.Gosim(arch=True)
    go0.write(start_pc, ADD_R0_R0)
    go1 = gosim_glue.Gosim(arch=True)
    go1.write(start_pc, ADD_R0_R0)

    state, _ = tic.delivery_periph_state()
    periph = GosimPeriph()
    periph.load(state)

    R0 = [0] * 16
    R1 = [0] * 16
    world = setup_dual(go0, go1, periph, lo, hi, start_pc, start_pc, R0, R1, 0, 0, 0, 0, 0x300)
    world.set_core_inflight(0, repeat_active=False, active_context="timer0_irq_handler")
    res = world.run_loop(1)
    world.free()
    periph.free()

    if res["exit"] != 1 or res["irq"] != {"core": 0, "irq": 4}:
        print(f"\nMISMATCH delivery deferral: expected Stop + Irq(0,4), got {res}")
        return 1
    if len(res["steps"]) != 1:
        print(f"\nMISMATCH delivery deferral: expected 1 committed step, got {len(res['steps'])}")
        return 1
    if "delivery deferral" not in res["reason"]:
        print(f"\nMISMATCH delivery deferral: reason must name the deferral, got {res['reason']!r}")
        return 1
    print("dual-core [delivery deferral]: PASS — the request is handed back to Python, "
          "never fabricated")
    return 0


def bench(segs, app, repeats, seed):
    seg = max(segs, key=len)
    N = 2 * len(seg)  # total round-robin steps across both cores
    rng = random.Random(seed)
    R0 = [rng.getrandbits(32) for _ in range(16)]
    R1 = [rng.getrandbits(32) for _ in range(16)]
    flags0 = rng.randrange(16)
    flags1 = rng.randrange(16)
    sp = rng.getrandbits(32)
    rets = rng.getrandbits(32) & tbc.RETS_MASK
    start_pc = seg[0][0]
    lo, hi = tbc.ENTRY, tbc.ENTRY + len(app)

    py_cpu0 = tbc.make_py_cpu()
    py_cpu1 = tbc.make_py_cpu()
    mem = tbc.RecordingMemory()
    mem.write(tbc.ENTRY, app)
    py_cpu0.mem = mem
    py_cpu1.mem = mem

    # Python round-robin step-by-step, cold decode cache every rep (no advance,
    # so the Go side — which runs select+step+advance — does strictly more work;
    # the speedup is a conservative lower bound, same discipline as the third cut).
    t0 = time.perf_counter()
    for _ in range(repeats):
        run_dualcore_reference(py_cpu0, py_cpu1, mem, start_pc, R0, R1, flags0, flags1,
                               sp, rets, N)
    py_sec = time.perf_counter() - t0

    go0 = make_go_cpu(app, lo, hi)
    go1 = make_go_cpu(app, lo, hi)
    state, _ = tic.counting_periph_state()
    periph = GosimPeriph()
    t0 = time.perf_counter()
    for _ in range(repeats):
        periph.load(state)
        world = setup_dual(go0, go1, periph, lo, hi, start_pc, start_pc, R0, R1, flags0, flags1,
                           sp, rets, 0x02050500)
        world.run_loop(N)
        world.free()
    go_sec = time.perf_counter() - t0
    periph.free()

    print(f"bench dual-core segment @{start_pc:#010x} len={len(seg)} (total {N} steps) "
          f"repeats={repeats} (cold cache per rep)")
    print(f"  python round-robin step : {py_sec * 1e6 / (repeats * N):.3f} us/step "
          f"({py_sec * 1e6 / repeats:.2f} us/segment)")
    print(f"  go dual-core RunLoop    : {go_sec * 1e6 / (repeats * N):.3f} us/step "
          f"({go_sec * 1e6 / repeats:.2f} us/segment)")
    print(f"  speedup                 : {py_sec / go_sec:.1f}x")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--bench", action="store_true")
    ap.add_argument("--bench-repeats", type=int, default=2000)
    ap.add_argument("--max-segments", type=int, default=150)
    args = ap.parse_args()

    app = tbc.application(tbc.OFFICIAL)
    tbc.app = app
    segs = tbc.find_segments(app, min_len=2, cap=args.max_segments)
    if not segs:
        print("RESULT: FAIL — no safe segments found")
        return 1

    if args.bench:
        bench(segs, app, args.bench_repeats, 0xD0A1)
        return 0

    if clean_parity(segs, app):
        return 1
    if after_step_yield():
        return 1
    if delivery_deferral():
        return 1
    print("RESULT: PASS — dual-core cgo end-to-end (clean run parity + after_step "
          "YIELD + delivery deferral hand-back)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
