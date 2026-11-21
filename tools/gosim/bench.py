#!/usr/bin/env python3
"""Throughput benchmark: Go gosim core vs the pinned pi32v2-isa simulator.

Builds a straight-line program of random decodable instructions (no branches,
no context/unmodeled forms), steps it through the Python reference CPU and the
Go core (single ctypes call per instruction, and one batched call), and reports
instructions/second and speedup.

Usage:
    python3 tools/gosim/bench.py --instructions 20000
"""
import argparse
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tools" / "gosim"))
sys.path.insert(0, str(ROOT / "tools" / "reference-cache" / "pi32v2-isa"))

import gosim as gosim_glue  # noqa: E402
import probe_pi32v2  # noqa: E402
from br28_arch_models import architectural_k2  # noqa: E402

module = probe_pi32v2.upstream()
tab = module.table.load()
codec = module.codec
models = module.check.load_models()


def arch_cpu():
    c = module.CPU()
    c.models, _ = architectural_k2(c.models)
    return c

BASE = 0x00100000
SP = 0x00200000


def straight_line_pool(rng):
    """Instruction ids that fall through (single step, no forward/branch decode)
    and run standalone (no context/unmodeled forms)."""
    probe = arch_cpu()
    pool = []
    for insn in tab.insns:
        if insn.id not in models or insn.block or insn.id.endswith(".p"):
            continue
        if insn.id in ("tbb", "tbh", "rts", "call.r", "goto.r"):
            continue
        if module.branch_field(insn) is not None:
            continue
        if insn.id.startswith(("rep", "pop.pc")):
            continue
        if insn.id in ("push.srl", "pop.srl", "push.srmask", "pop.srmask"):
            continue
        # must actually run standalone
        enc = codec.encode(insn, {name: 0 for name in insn.fields})
        probe.mem = module.Memory()
        probe.mem.write(BASE, enc)
        probe.R = [BASE + 0x100] * 16
        probe.flags = 0
        probe.sp = SP
        probe.rets = 0
        probe.pc = BASE
        probe.steps = 0
        probe.cache.clear()
        probe.after_then = None
        probe.seq = 0
        probe.icfg = 0x02050500
        try:
            probe.step()
        except RuntimeError:
            continue
        pool.append(insn)
    return pool


def build_program(n, rng, pool):
    """Bytes of n straight-line instructions laid out sequentially."""
    out = bytearray()
    for _ in range(n):
        insn = rng.choice(pool)
        vals = {name: rng.randrange(1 << len(insn.field_bits(name)))
                for name in insn.fields}
        out += codec.encode(insn, vals)
    return bytes(out)


def timeit(fn, n):
    t0 = time.perf_counter()
    fn()
    dt = time.perf_counter() - t0
    return n / dt, dt


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--instructions", type=int, default=20000)
    args = ap.parse_args()
    n = args.instructions
    rng = random.Random(7)
    pool = straight_line_pool(rng)
    prog = build_program(n, rng, pool)
    print(f"program: {n} instructions from a pool of {len(pool)} fall-through forms, "
          f"{len(prog)} bytes at {BASE:#x}")

    # ---- Python reference ----
    py = arch_cpu()
    py.mem.write(BASE, prog)
    py.mem.write(SP - 0x1000, bytes(0x2000))
    py.R = [BASE + 0x100 + 4 * i for i in range(16)]
    py.flags = 0
    py.sp = SP
    py.pc = BASE
    ips_py, _ = timeit(lambda: [py.step() for _ in range(n)], n)

    # ---- Go, one ctypes call per instruction ----
    go = gosim_glue.Gosim(arch=True)
    go.write(BASE, prog)
    go.write(SP - 0x1000, bytes(0x2000))
    go.set_regs([BASE + 0x100 + 4 * i for i in range(16)])
    go.set_state(0, SP, 0, BASE)
    ips_go1, _ = timeit(lambda: [go.step() for _ in range(n)], n)

    # ---- Go, batched (one ctypes call) ----
    go2 = gosim_glue.Gosim(arch=True)
    go2.write(BASE, prog)
    go2.write(SP - 0x1000, bytes(0x2000))
    go2.set_regs([BASE + 0x100 + 4 * i for i in range(16)])
    go2.set_state(0, SP, 0, BASE)
    ips_gob, _ = timeit(lambda: go2.batch_step(n), n)

    print(f"\nPython reference      : {ips_py:>12,.0f} inst/s  ({1e6/ips_py:>7.3f} us/step)")
    print(f"Go, 1 ctypes call/step: {ips_go1:>12,.0f} inst/s  ({1e6/ips_go1:>7.3f} us/step)  {ips_go1/ips_py:>6.1f}x")
    print(f"Go, batched           : {ips_gob:>12,.0f} inst/s  ({1e6/ips_gob:>7.3f} us/step)  {ips_gob/ips_py:>6.1f}x")

    # consistency of the benchmark program (both engines end at the same state)
    assert py.pc == go.get_state()[3], f"pc diverged: py {py.pc:#x} go {go.get_state()[3]:#x}"
    assert py.R == go.get_regs(), "registers diverged"
    print("\n(benchmark program end state matches between Python and Go)")

    # batch call overhead (empty loop cost) for context
    nop = codec.encode(tab.by_id("nop"), {})
    g3 = gosim_glue.Gosim(arch=True)
    g3.write(BASE, nop * 1000)
    g3.set_state(0, SP, 0, BASE)
    t0 = time.perf_counter()
    for _ in range(10000):
        g3.batch_step(1)
    per_call = (time.perf_counter() - t0) / 10000
    print(f"empty gosim_batch_step(1) ctypes round-trip: {per_call*1e6:.2f} us/call")


if __name__ == "__main__":
    main()
