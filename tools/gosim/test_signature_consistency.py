#!/usr/bin/env python3
"""Differential consistency test: Go register-signature formatter vs the
Python reference _register_signature (tools/analyze_image_trace.py).

For every register type the reference dispatch handles (bool / int /
UnknownWord / IcfgValue / other), this drives the same value through (a) the
pure-Python _register_signature and (b) the Go SignaturesBatch export via
tools/gosim/signature_glue.py, then asserts byte-identical strings. A fuzz
phase additionally compares full 16-register batches drawn from all types.

Usage:
    python3 tools/gosim/test_signature_consistency.py
    python3 tools/gosim/test_signature_consistency.py --seed N --batches K
"""
import argparse
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tools" / "gosim"))

import analyze_image_trace as AIT  # noqa: E402
from br28_icfg import IcfgValue  # noqa: E402
from br28_unknown import UnknownWord  # noqa: E402
import signature_glue as glue  # noqa: E402

_ref = AIT._register_signature


def check_batch(R, label):
    """Compare Go batch vs Python reference on one register list."""
    got = glue.register_signatures(R)
    if got is None:
        return f"{label}: encoder fell back (unexpected)"
    want = [_ref(v) for v in R]
    for i, (g, w) in enumerate(zip(got, want)):
        if g != w:
            return f"{label} reg[{i}]: go {g!r} != py {w!r}"
    return None


def make_unknown_word(rng):
    """A valid UnknownWord: None byte <-> int origin, known byte <-> None."""
    bytes_ = []
    origins = []
    n_unknown = 0
    for _ in range(4):
        if rng.randrange(3) == 0:  # ~1/3 chance unknown per byte
            bytes_.append(None)
            origins.append(rng.choice([0, 1, 0x12ab, 0xffffffff, rng.getrandbits(32)]))
            n_unknown += 1
        else:
            bytes_.append(rng.randrange(256))
            origins.append(None)
    if n_unknown == 0:  # constructor requires >= 1 unknown
        bytes_[0] = None
        origins[0] = rng.getrandbits(32)
    return UnknownWord(bytes_, origins)


INT_VECTORS = [
    0, 1, 7, 0x10, 0xff, 0x100, 0x0fffffff, 0xffffffff, 0xdeadbeef, 0x100000000,
    0x7fffffffffffffff,  # int64 max
    -1, -16, -0x12345678, -(1 << 63),  # int64 min
]


def representative_vectors():
    """One value per type the dispatch handles (plus edge ints)."""
    vecs = [True, False]
    vecs += INT_VECTORS
    vecs += [
        UnknownWord([None, None, None, None], [0, 1, 0xffffffff, 0x12ab]),
        UnknownWord([0xab, None, 0x01, 0xff], [None, 0x1234, None, None]),
        UnknownWord([0x00, None, 0x00, None], [None, 0xffffffff, None, 0]),
        UnknownWord([0xff, 0xff, None, 0xff], [None, None, 0x1, None]),
        IcfgValue(value=0x12, known=0xff, origin=(0, 0)),
        IcfgValue(value=0xdeadbeef, known=0xffffffff, origin=(0, 0)),
        IcfgValue(value=0, known=0, origin=(0, 0)),
        IcfgValue(value=0x300, known=0x300, origin=(1, 2)),
        "x", None, 1.5, object(),
    ]
    return vecs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=12345)
    ap.add_argument("--batches", type=int, default=20000)
    args = ap.parse_args()

    failures = []
    # ---- Phase 1: representative single-value + batch vectors ---------------
    vecs = representative_vectors()
    for i, v in enumerate(vecs):
        err = check_batch([v], f"repr[{i}] {type(v).__name__}")
        if err:
            failures.append(err)

    # a mixed 16-register batch resembling one real after_step
    mixed = [0xdeadbeef, True, make_unknown_word(random.Random(1)),
             IcfgValue(value=0x12, known=0xff, origin=(0, 0)), 0, -1,
             make_unknown_word(random.Random(2)), None, 0xffffffff, 3.14,
             IcfgValue(value=0x300, known=0x300, origin=(1, 2)),
             make_unknown_word(random.Random(3)), 7, False, 0x100000000, "?"]
    err = check_batch(mixed, "mixed")
    if err:
        failures.append(err)

    # ---- Phase 2: fuzz full batches across all types ------------------------
    rng = random.Random(args.seed)
    n_fail = 0
    for b in range(args.batches):
        R = []
        for _ in range(16):
            kind = rng.randrange(6)
            if kind == 0:
                R.append(rng.getrandbits(32))
            elif kind == 1:
                R.append(rng.choice([True, False]))
            elif kind == 2:
                R.append(make_unknown_word(rng))
            elif kind == 3:
                R.append(IcfgValue(value=rng.getrandbits(32),
                                   known=rng.getrandbits(32),
                                   origin=(0, 0)))
            elif kind == 4:
                R.append(rng.choice([0, -1, 0x100000000, 0x7fffffffffffffff,
                                     -(1 << 63), 0xdeadbeef]))
            else:
                R.append(rng.choice(["x", None, 1.5, object()]))
        err = check_batch(R, f"fuzz[{b}]")
        if err:
            failures.append(err)
            n_fail += 1
            if n_fail >= 10:
                break

    if failures:
        print("MISMATCHES:")
        for f in failures:
            print("  " + f)
        print(f"\nRESULT: FAIL ({len(failures)} mismatches shown)")
        return 1
    print(f"RESULT: PASS (byte-identical over {len(vecs) + 1} representative "
          f"vectors + {args.batches} fuzz batches of 16 registers)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
