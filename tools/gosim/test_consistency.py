#!/usr/bin/env python3
"""Differential consistency test: Go gosim core vs the pinned pi32v2-isa simulator.

For every instruction in the pinned table, this drives the same encoded bytes
and the same initial register/flags/sp/rets/memory state through (a) the Python
reference CPU from tools/reference-cache/pi32v2-isa (both the raw k2-stub
configuration and the architectural_k2 configuration) and (b) the Go core in
tools/gosim/libgosim.so, then compares R[16], flags, sp, rets, pc and a 4 KiB
memory window byte-for-byte.

Usage:
    python3 tools/gosim/test_consistency.py            # full sweep
    python3 tools/gosim/test_consistency.py --quick    # 20 instructions
    python3 tools/gosim/test_consistency.py --seed N --cases K
"""
import argparse
import random
import sys
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

# Memory window: every register that an instruction can use as an address is
# pinned into [BASE+0x100, BASE+0xE00); sp and pc sit inside the same 4 KiB
# page, so all reads/writes land in one page we can compare cheaply.
BASES = (0x00100000, 0x40000000, 0x80000000)
WIN = 0x1000
SP_OFF, PC_OFF = 0xFC0, 0x200
RETS_MASK = 0x07FFFFFE

NOP = tab.by_id("nop")
NOP_BYTES = codec.encode(NOP, {name: 0 for name in NOP.fields})


def window_bytes(rng, base):
    return bytes(rng.randrange(256) for _ in range(WIN))


def gen_field_values(insn, rng, zero=False):
    """Raw field values that codec.encode accepts, exercising block fields safely."""
    block_fields = set(insn.block.values())
    vals = {}
    for name, f in insn.fields.items():
        width = len(insn.field_bits(name))
        if width <= 0:
            continue
        if zero or name in block_fields:
            vals[name] = 0
        else:
            vals[name] = rng.randrange(1 << width)
    return vals


def reset_py(c):
    c.R = [0] * 16
    c.flags = 0
    c.sp = 0
    c.rets = 0
    c.pc = 0
    c.steps = 0
    c.cache.clear()
    c.icfg = 0x02050500
    c.seq = 0
    c.after_then = None
    c.mem = module.Memory()


def py_step(c):
    try:
        c.step()
        return ("ok", None)
    except RuntimeError as e:
        msg = str(e)
        if "has no model" in msg:
            return ("nomodel", msg)
        if "needs the hardware run's context" in msg:
            return ("needsctx", msg)
        if "no instruction decodes" in msg:
            return ("nodecode", msg)
        return ("other", msg)
    except Exception as e:  # noqa: BLE001
        return ("exception", f"{type(e).__name__}: {e}")


def py_window(c, base):
    page = c.mem.pages.get(base >> 12)
    return bytes(page) if page is not None else bytes(WIN)


def run_case(py_cpu, go, insn, field_values, rng, base):
    enc = codec.encode(insn, field_values)
    size = 2 * len(insn.words)
    # sanity: the encoding round-trips to this instruction in the reference
    got = codec.decode(tab, enc)
    assert got is not None and got[0] is insn, f"{insn.id}: encode/decode round-trip failed"

    R = [base + 0x100 + (rng.randrange(0xF00) & ~3) for _ in range(16)]
    flags = rng.randrange(16)
    sp = base + SP_OFF
    rets = rng.randrange(1 << 32) & RETS_MASK
    pc = base + PC_OFF
    wbytes = window_bytes(rng, base)

    # ---- Python reference ----
    reset_py(py_cpu)
    py_cpu.R = list(R)
    py_cpu.flags = flags
    py_cpu.sp = sp
    py_cpu.rets = rets
    py_cpu.pc = pc
    py_cpu.mem.write(base, wbytes)
    py_cpu.mem.write(pc, enc)
    py_cpu.mem.write(pc + size, NOP_BYTES)
    pstat = py_step(py_cpu)

    # ---- Go core ----
    go.reset()
    go.write(base, wbytes)
    go.write(pc, enc)
    go.write(pc + size, NOP_BYTES)
    go.set_regs(R)
    go.set_state(flags, sp, rets, pc)
    gstat = go.step()

    return pstat, gstat, (R, flags, sp, rets, pc, size, base)


def classify(pstat, gstat):
    pkind = pstat[0]
    if pkind == "ok":
        return "ok" if gstat == gosim_glue.STEP_OK else "mismatch-run"
    # Python stopped: Go must also refuse to run it standalone.
    if pkind in ("nomodel", "needsctx", "nodecode"):
        if gstat in (gosim_glue.STEP_NO_MODEL, gosim_glue.STEP_NEEDS_CTX, gosim_glue.STEP_NO_INSTR):
            return "stop"
        return "mismatch-stop"
    # Python raised something else (or a bare exception): require Go to report other.
    if gstat == gosim_glue.STEP_OTHER:
        return "stop"
    return "mismatch-stop"


def compare_regs(py_cpu, go):
    """Compare registers/flags/sp/rets/pc only (used by the data-coverage phase)."""
    diffs = []
    gregs = go.get_regs()
    for i in range(16):
        if gregs[i] != (py_cpu.R[i] & 0xFFFFFFFF):
            diffs.append(f"r{i}: go {gregs[i]:#010x} py {py_cpu.R[i] & 0xFFFFFFFF:#010x}")
    gflags, gsp, grets, gpc = go.get_state()
    if gflags != (py_cpu.flags & 0xF):
        diffs.append(f"flags: go {gflags} py {py_cpu.flags & 0xF}")
    if gsp != (py_cpu.sp & 0xFFFFFFFF):
        diffs.append(f"sp: go {gsp:#010x} py {py_cpu.sp & 0xFFFFFFFF:#010x}")
    if grets != (py_cpu.rets & 0xFFFFFFFF):
        diffs.append(f"rets: go {grets:#010x} py {py_cpu.rets & 0xFFFFFFFF:#010x}")
    if gpc != (py_cpu.pc & 0xFFFFFFFF):
        diffs.append(f"pc: go {gpc:#010x} py {py_cpu.pc & 0xFFFFFFFF:#010x}")
    return diffs


DATA_PC = 0x00010000


def run_data_case(py_cpu, go, insn, field_values, rng):
    """One case with full-random 32-bit registers: exercises sign, overflow and
    float bit patterns. Memory is not compared (addresses are arbitrary)."""
    enc = codec.encode(insn, field_values)
    size = 2 * len(insn.words)
    R = [rng.getrandbits(32) for _ in range(16)]
    flags = rng.randrange(16)
    sp = rng.getrandbits(32)
    rets = rng.getrandbits(32) & RETS_MASK
    pc = DATA_PC

    reset_py(py_cpu)
    py_cpu.R = list(R)
    py_cpu.flags = flags
    py_cpu.sp = sp
    py_cpu.rets = rets
    py_cpu.pc = pc
    py_cpu.mem.write(pc, enc)
    py_cpu.mem.write(pc + size, NOP_BYTES)
    pstat = py_step(py_cpu)

    go.reset()
    go.write(pc, enc)
    go.write(pc + size, NOP_BYTES)
    go.set_regs(R)
    go.set_state(flags, sp, rets, pc)
    gstat = go.step()

    return pstat, gstat, (R, flags, sp, rets, pc, size, DATA_PC)


def compare_state(py_cpu, go, base):
    """Byte-compare the observable machine state after one step. Returns list of diffs."""
    diffs = []
    gregs = go.get_regs()
    for i in range(16):
        if gregs[i] != (py_cpu.R[i] & 0xFFFFFFFF):
            diffs.append(f"r{i}: go {gregs[i]:#010x} py {py_cpu.R[i] & 0xFFFFFFFF:#010x}")
    gflags, gsp, grets, gpc = go.get_state()
    if gflags != (py_cpu.flags & 0xF):
        diffs.append(f"flags: go {gflags} py {py_cpu.flags & 0xF}")
    if gsp != (py_cpu.sp & 0xFFFFFFFF):
        diffs.append(f"sp: go {gsp:#010x} py {py_cpu.sp & 0xFFFFFFFF:#010x}")
    if grets != (py_cpu.rets & 0xFFFFFFFF):
        diffs.append(f"rets: go {grets:#010x} py {py_cpu.rets & 0xFFFFFFFF:#010x}")
    if gpc != (py_cpu.pc & 0xFFFFFFFF):
        diffs.append(f"pc: go {gpc:#010x} py {py_cpu.pc & 0xFFFFFFFF:#010x}")
    gmem = go.read(base, WIN)
    if gmem != py_window(py_cpu, base):
        for off in range(WIN):
            if gmem[off] != py_window(py_cpu, base)[off]:
                diffs.append(f"mem[{base + off:#x}]: go {gmem[off]:#04x} py {py_window(py_cpu, base)[off]:#04x}")
                break
    return diffs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--seed", type=int, default=12345)
    ap.add_argument("--cases", type=int, default=6)
    ap.add_argument("--arch-only", action="store_true")
    args = ap.parse_args()

    insns = tab.insns
    if args.quick:
        insns = insns[:20]

    configs = (["raw", "arch"] if not args.arch_only else ["arch"])
    report = {
        "configs": configs,
        "instructions": len(insns),
        "cases_per_instruction": args.cases + 1,
        "mismatches": [],
        "coverage": {},
    }

    for mode in report["configs"]:
        go = gosim_glue.Gosim(arch=(mode == "arch"))
        py_cpu = module.CPU()
        if mode == "arch":
            py_cpu.models, _ = architectural_k2(py_cpu.models)
        rng = random.Random(args.seed)

        n_ok = 0
        n_stop = 0
        mismatches = []
        per_insn = {}
        data_ok = 0
        data_stop = 0
        data_mismatches = []

        # ---- Phase A: windowed memory, address-safe registers -------------
        for idx, insn in enumerate(insns):
            outcomes = []
            assignments = [gen_field_values(insn, rng, zero=True)]
            assignments += [gen_field_values(insn, rng) for _ in range(args.cases)]
            for ci, vals in enumerate(assignments):
                base = BASES[(idx * (args.cases + 1) + ci) % len(BASES)]
                pstat, gstat, state = run_case(py_cpu, go, insn, vals, rng, base)
                kind = classify(pstat, gstat)
                if kind == "ok":
                    diffs = compare_state(py_cpu, go, base)
                    if diffs:
                        kind = "mismatch"
                        mismatches.append((insn.id, vals, state, diffs, pstat, gstat))
                    else:
                        n_ok += 1
                elif kind == "stop":
                    n_stop += 1
                else:
                    mismatches.append((insn.id, vals, state, [f"{kind}: py={pstat} go={gstat}"],
                                       pstat, gstat))
                outcomes.append(kind)
            per_insn[insn.id] = outcomes

        # ---- Phase B: full-random registers (sign / overflow / float bits) --
        for insn in insns:
            for _ in range(args.cases):
                vals = gen_field_values(insn, rng)
                pstat, gstat, state = run_data_case(py_cpu, go, insn, vals, rng)
                kind = classify(pstat, gstat)
                if kind == "ok":
                    diffs = compare_regs(py_cpu, go)
                    if diffs:
                        data_mismatches.append((insn.id, vals, state, diffs, pstat, gstat))
                    else:
                        data_ok += 1
                elif kind == "stop":
                    data_stop += 1
                else:
                    data_mismatches.append((insn.id, vals, state, [f"{kind}: py={pstat} go={gstat}"],
                                            pstat, gstat))

        report["coverage"][mode] = {
            "ok_steps": n_ok,
            "stop_steps": n_stop,
            "data_ok_steps": data_ok,
            "data_stop_steps": data_stop,
            "instructions_that_ran": sum(1 for outs in per_insn.values() if "ok" in outs),
        }
        report["mismatches"].append({"mode": mode, "cases": mismatches + data_mismatches})

        print(f"[{mode}] window ok={n_ok} stop={n_stop} | data ok={data_ok} stop={data_stop} "
              f"| mismatch={len(mismatches) + len(data_mismatches)} "
              f"| ran_instructions={report['coverage'][mode]['instructions_that_ran']}/{len(insns)}")

    # summary of mismatches
    any_bad = any(m["cases"] for m in report["mismatches"])
    for m in report["mismatches"]:
        for insn_id, vals, state, diffs, pstat, gstat in m["cases"][:20]:
            print(f"\nMISMATCH [{m['mode']}] {insn_id} vals={vals}")
            print(f"  py={pstat} go={gstat}")
            for d in diffs:
                print(f"  {d}")

    if any_bad:
        print("\nRESULT: FAIL (see mismatches above)")
        return 1
    print("\nRESULT: PASS (byte-identical for every exercised step)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
