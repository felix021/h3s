#!/usr/bin/env python3
"""Stage-2 segment-batch parity + wall-clock measurement.

Proves the "whole-loop-to-Go" protocol on real firmware code, now covering the
stage-2 contract (sequential scalar ALU plus control-flow branches): find
stretches of the official application that are safe under the contract, then run
the exact same stretch (a) one instruction at a time through the Python
reference (upstream sim.CPU + architectural_k2, which is what probe_boot's CPU
reduces to for such a stretch) and (b) in one cgo call through
tools/gosim/segment.go. The two are serialized to the same byte layout
(tools/gosim/gosim_batch.py) and compared byte-for-byte.

Because branches make the executed pc sequence non-sequential, the parity is
computed on the DYNAMIC prefix: both engines start from the same pc with the
same registers/flags/sp/rets, follow the same branch decisions, and stop at the
first instruction outside the contract. The comparison is on the per-step
observations (pc/next_pc/flags/16 registers), i.e. the branch-target sequence
is compared against the step-by-step pc sequence, plus the honest-stop boundary.

Usage:
    python3 tools/gosim/test_batch_consistency.py            # parity over found segments
    python3 tools/gosim/test_batch_consistency.py --list     # just print found segments
    python3 tools/gosim/test_batch_consistency.py --bench    # wall-clock per-step comparison
"""
import argparse
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for p in (ROOT / "tools", ROOT / "tools" / "gosim",
          ROOT / "tools" / "reference-cache" / "pi32v2-isa",
          ROOT / "tools" / "reference-cache" / "pi32v2-isa" / "pi32v2isa"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import gosim as gosim_glue  # noqa: E402
import gosim_batch  # noqa: E402
import probe_pi32v2  # noqa: E402
from probe_boot import ENTRY, application  # noqa: E402
from br28_arch_models import architectural_k2  # noqa: E402
from pi32v2isa import asm  # noqa: E402

OFFICIAL = ROOT / "firmware" / "QCY_H3S_7018F_1T2_release_note_V1.1.8_011.ufw"

module = probe_pi32v2.upstream()
tab = module.table.load()
codec = module.codec
arch_models, _ = architectural_k2(module.check.load_models())

ALLOWED_GROUPS = {"arithmetic", "logic", "shifts", "bitops", "moves", "constants", "multiply", "fpu"}
SP_BANK = {"add.sp_i", "add.sp_i.l", "add.r_sp_i", "add.r_sp_i.l"}
RETS_MASK = 0x07FFFFFE

# Stage-2 relaxation: the contract now also runs control flow — direct
# unconditional branches (goto.s/goto.l/goto.r), flag conditional branches
# (bc.*), zero-test branches (beqz/bnez), decrement-and-branch (dbnz.r) and the
# register-compare branch family (beq/bne/bge/bgt/ble/blt plus signed b*es).
# Call/return (rets/sp side effects) and if-block/rep stay out.
_REG_CMP = ("beq.", "bne.", "bge.", "bgt.", "ble.", "blt.",
            "bges.", "bgts.", "bles.", "blts.")


def _allow_branch(iid):
    if iid.startswith("bc."):
        return True
    if iid in ("goto.s", "goto.l", "goto.r", "beqz", "bnez", "dbnz.r"):
        return True
    return any(iid.startswith(c) for c in _REG_CMP)


def is_safe(insn):
    """Segment contract predicate (mirror of segment.go safeALU + batchUnsafe,
    now including the stage-2 branch families)."""
    if insn.group not in ALLOWED_GROUPS:
        if not _allow_branch(insn.id):
            return False
    if any(f.kind == "sr" for f in insn.fields.values()):
        return False
    if insn.id in SP_BANK or insn.id.startswith(("mov.sp_", "mov.ssp_", "mov.usp_")):
        return False
    if insn.id.endswith(".p"):
        return False
    if insn.block or getattr(insn, "body", ""):
        return False
    if asm.branch_field(insn) is not None and not _allow_branch(insn.id):
        return False
    return insn.id in arch_models


def _read6(app, off):
    return (app[off:off + 6] + b"\x00" * 6)[:6]


def find_segments(app, min_len=2, cap=None):
    """Return runs of consecutive safe instructions as lists of (pc, insn, raw, size).

    Stops early once cap segments are found (the startup prefix is ALU-dense, so
    a modest cap scans only a small fraction of the 842654-byte app)."""
    hi = len(app)
    segs, run = [], []
    pc = 0
    while pc < hi:
        if cap is not None and len(segs) >= cap:
            break
        got = codec.decode(tab, _read6(app, pc))
        if got is None:
            if len(run) >= min_len:
                segs.append(run)
            run = []
            pc += 2
            continue
        insn, raw, size = got
        if is_safe(insn):
            run.append((ENTRY + pc, insn, raw, size))
            pc += size
        else:
            if len(run) >= min_len:
                segs.append(run)
            run = []
            pc += size
    if len(run) >= min_len:
        segs.append(run)
    return segs


class RecordingMemory(module.Memory):
    def __init__(self):
        super().__init__()
        self.writes = []

    def write(self, a, data):
        self.writes.append((a, data))
        super().write(a, data)


def make_py_cpu():
    c = module.CPU()
    c.models, _ = architectural_k2(c.models)
    return c


def reset_py(c, R, flags, sp, rets, pc, clear_cache=True):
    c.R = list(R)
    c.flags = flags & 0xF
    c.sp = sp
    c.rets = rets
    c.pc = pc
    c.steps = 0
    if clear_cache:
        c.cache.clear()
    c.seq = 0
    c.after_then = None
    c.icfg = 0x02050500


def run_reference(cpu, mem, start_pc, R, flags, sp, rets, cap, clear_cache=True):
    """Python step-by-step from start_pc, following branches dynamically and
    stopping at the first instruction outside the contract (or after cap steps).
    Returns the per-step observations (before_pc, next_pc, flags, R, mem)."""
    reset_py(cpu, R, flags, sp, rets, start_pc, clear_cache=clear_cache)
    mem.writes.clear()
    obs = []
    for _ in range(cap):
        before = cpu.pc
        got = cpu.decode(cpu.pc)
        if got is None or not is_safe(got[0]):
            break
        cpu.step()
        if mem.writes:
            raise AssertionError(
                f"safe segment wrote memory at {before:#x}: {mem.writes!r}")
        obs.append((before, cpu.pc, cpu.flags & 0xF, list(cpu.R), []))
    return obs


def ref_step_bytes(obs):
    return b"".join(gosim_batch.encode_step(pc, npc, fl, R, mem)
                    for pc, npc, fl, R, mem in obs)


def run_go(go, start_pc, R, flags, sp, rets, n=None):
    go.set_regs(R)
    go.set_state(flags, sp, rets, start_pc)
    return go.run_segment(n, ENTRY, ENTRY + len(app),
                          gosim_batch.HDR_SIZE + n * (gosim_batch.STEP_FIXED + gosim_batch.MEM_SIZE))


def go_step_bytes(steps):
    return b"".join(gosim_batch.encode_step(pc, npc, fl, R, mem)
                    for pc, npc, fl, R, mem in steps)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--bench", action="store_true")
    ap.add_argument("--max-segments", type=int, default=200)
    ap.add_argument("--min-len", type=int, default=2)
    ap.add_argument("--seed", type=int, default=12345)
    ap.add_argument("--bench-repeats", type=int, default=20000)
    args = ap.parse_args()

    global app
    app = application(OFFICIAL)
    segs = find_segments(app, args.min_len, cap=None if args.list else args.max_segments)
    lengths = [len(s) for s in segs]

    if args.list:
        for s in segs[: args.max_segments]:
            ids = [insn.id for _pc, insn, _raw, _size in s]
            print(f"{s[0][0]:#010x} len={len(s):3d}  " + " ".join(ids))
        print(f"total segments={len(segs)} length histogram="
              f"{sorted(set(lengths), key=lambda n: lengths.count(n))}")
        return 0

    if args.bench:
        bench(segs, args.bench_repeats, args.seed)
        return 0

    # ---- parity (dynamic prefix, following branches) ----
    rng = random.Random(args.seed)
    py_cpu = make_py_cpu()
    mem = RecordingMemory()
    mem.write(ENTRY, app)
    py_cpu.mem = mem
    go = gosim_glue.Gosim(arch=True)
    go.write(ENTRY, app)

    tested = 0
    steps = 0
    failures = []
    stop_failures = []
    for seg in segs[: args.max_segments]:
        R = [rng.getrandbits(32) for _ in range(16)]
        flags = rng.randrange(16)
        sp = rng.getrandbits(32)
        rets = rng.getrandbits(32) & RETS_MASK
        start_pc = seg[0][0]
        cap = len(seg) + 4  # a little headroom past the static run
        obs = run_reference(py_cpu, mem, start_pc, R, flags, sp, rets, cap)
        L = len(obs)
        got = run_go(go, start_pc, R, flags, sp, rets, cap)
        (executed, stop_code, stop_pc), gsteps = gosim_batch.parse_buffer(got)
        tested += 1
        steps += L
        want = ref_step_bytes(obs)
        have = go_step_bytes(gsteps)
        if want != have or executed != L:
            failures.append((seg, L, executed, want, have, stop_code, stop_pc))
            if len(failures) >= 5:
                break
        # Honest-stop: extending past the safe prefix must stop at the boundary
        # (the pc after the last safe step), never fabricate extra steps.
        if L < cap and not (executed == L and stop_code != gosim_batch.BATCH_OK
                            and stop_pc == obs[-1][1]):
            stop_failures.append((seg, executed, stop_code, stop_pc, obs[-1][1]))
            if len(stop_failures) >= 5:
                break

    if failures:
        for seg, L, executed, want, have, stop_code, stop_pc in failures:
            print(f"\nMISMATCH segment@{seg[0][0]:#010x} static_len={len(seg)} "
                  f"ref_steps={L} go_executed={executed} go_stop_code={stop_code} go_stop_pc={stop_pc:#x}")
            limit = min(len(want), len(have))
            i = next((i for i in range(limit) if want[i] != have[i]), limit)
            print(f"  first byte diff at {i} (want len={len(want)} got len={len(have)}):")
            print(f"    want {want[max(0, i - 16):i + 16].hex()}")
            print(f"    got  {have[max(0, i - 16):i + 16].hex()}")
        print(f"\nRESULT: FAIL ({len(failures)}/{tested} segments mismatched)")
        return 1

    if stop_failures:
        for seg, executed, stop_code, stop_pc, nxt_pc in stop_failures:
            print(f"\nHONEST-STOP MISMATCH segment@{seg[0][0]:#010x} len={len(seg)} "
                  f"executed={executed} stop_code={stop_code} stop_pc={stop_pc:#x} "
                  f"expected_stop_pc={nxt_pc:#x if nxt_pc is not None else None}")
        print(f"\nRESULT: FAIL ({len(stop_failures)} honest-stop boundary violations)")
        return 1

    print(f"RESULT: PASS — {tested} segments, {steps} steps, byte-for-byte identical; "
          f"honest-stop boundary verified (max static len={max(lengths) if lengths else 0})")
    return 0


def bench(segs, repeats, seed):
    """Honest wall-clock comparison for one call: Python N steps vs Go one batch."""
    if not segs:
        print("no segments found")
        return
    seg = max(segs, key=len)
    N = len(seg)
    rng = random.Random(seed)
    R = [rng.getrandbits(32) for _ in range(16)]
    flags = rng.randrange(16)
    sp = rng.getrandbits(32)
    rets = rng.getrandbits(32) & RETS_MASK

    py_cpu = make_py_cpu()
    mem = RecordingMemory()
    mem.write(ENTRY, app)
    py_cpu.mem = mem
    go = gosim_glue.Gosim(arch=True)
    go.write(ENTRY, app)

    # warm up decode caches (both engines keep them across the timed loop)
    run_reference(py_cpu, mem, seg[0][0], R, flags, sp, rets, N)
    run_go(go, seg[0][0], R, flags, sp, rets, N)

    t0 = time.perf_counter()
    for _ in range(repeats):
        run_reference(py_cpu, mem, seg[0][0], R, flags, sp, rets, N, clear_cache=False)
    py_sec = time.perf_counter() - t0

    t0 = time.perf_counter()
    for _ in range(repeats):
        go.set_regs(R)
        go.set_state(flags, sp, rets, seg[0][0])
        go.run_segment(N, ENTRY, ENTRY + len(app), gosim_batch.outcap(N))
    go_sec = time.perf_counter() - t0

    print(f"bench segment @{seg[0][0]:#010x} len={N} repeats={repeats}")
    print(f"  python step-by-step : {py_sec * 1e6 / (repeats * N):.3f} us/step "
          f"({py_sec * 1e6 / repeats:.2f} us/segment)")
    print(f"  go batch (one call) : {go_sec * 1e6 / (repeats * N):.3f} us/step "
          f"({go_sec * 1e6 / repeats:.2f} us/segment)")


if __name__ == "__main__":
    sys.exit(main())
