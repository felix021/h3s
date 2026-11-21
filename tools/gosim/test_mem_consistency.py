#!/usr/bin/env python3
"""Differential test: Go strict memory (mem.go) vs the Python probe_boot.Memory
semantics, byte-for-byte.

The Python Memory class is a local inside probe_boot.explore, so it cannot be
imported directly. This test drives the Go memory (via the gosim_mem.py cgo
glue) and a faithful, compact re-transcription of the M2 scope of
probe_boot.Memory side by side over targeted and randomized operations, and
compares the outcome (successful bytes, or the exact Blocked details) of every
read/write.

The transcription is anchored to the real Memory by the Anchor test below, which
runs a minimal probe_boot.explore scenario and asserts the real class produces
the same uninitialized_memory details (kind / region / address / first_missing).

Usage:
    cd tools/gosim && go build -buildmode=c-shared -o libgosim.so ./cshared
    python3 tools/gosim/test_mem_consistency.py
"""
import json
import random
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tools" / "gosim"))
sys.path.insert(0, str(ROOT / "tools" / "reference-cache" / "pi32v2-isa"))

from probe_boot import ENTRY, PLL, STACK_TOP, BOOT_ARGS  # noqa: E402
from gosim_mem import StrictMem  # noqa: E402

# The M2-scope transcription of probe_boot.Memory (no hardware/machine/mmu/sfc,
# which are all absent in the differential scope). Each method mirrors the
# Python dispatch order exactly; Blocked becomes PyMemError with the same
# details dict shape.
class PyMemError(RuntimeError):
    def __init__(self, details):
        self.details = details
        super().__init__(json.dumps(details, sort_keys=True))


class PyMemory:
    def __init__(self, regions=(), rom=None, entry=ENTRY, app=b"", readonly=(), pll=None):
        self.regions = list(regions)
        self.rom = rom
        self.entry = entry
        self.app = app
        self.readonly = list(readonly)
        self.pll = pll
        self.fetch = False
        self.ram = {}

    def region(self, address, size):
        for low, high, kind in self.regions:
            if low <= address and address + size <= high:
                return kind
        return None

    def read(self, address, size):
        # the invalid-request guard is a harness invariant (ValueError in the real
        # probe_boot, not a Blocked); the battery never generates such a request.
        if not (size > 0 and 0 <= address <= 0xffffffff and address + size <= 0x100000000):
            raise ValueError("invalid memory request")
        if self.entry <= address and address + size <= self.entry + len(self.app):
            return self.app[address - self.entry:address - self.entry + size]
        if self.rom is not None and 0 <= address and address + size <= len(self.rom):
            return self.rom[address:address + size]
        for base, data in self.readonly:
            if base <= address and address + size <= base + len(data):
                return data[address - base:address - base + size]
        detail = {"access": "fetch" if self.fetch else "read",
                  "address": hex(address), "size": size}
        if not self.fetch and address == PLL and size == 4 and self.pll is not None:
            return struct.pack("<I", self.pll)
        region = self.region(address, size)
        if region:
            out = []
            for a in range(address, address + size):
                if a not in self.ram:
                    raise PyMemError({"kind": "uninitialized_memory", **detail,
                                      "region": region, "first_missing": hex(a)})
                out.append(self.ram[a])
            return bytes(out)
        if self.fetch:
            raise PyMemError({"kind": "unmapped_instruction_fetch", **detail,
                              "reference_sdk_symbol": "_pll_init" if address == 0x1FE14 else None})
        raise PyMemError({"kind": "unmapped_memory", **detail,
                          "reference_sdk_register": "JL_PLL0.CON0" if address == PLL else None})

    def write(self, address, data):
        if self.rom is not None and address < len(self.rom) and address + len(data) > 0:
            raise PyMemError({"kind": "unmapped_or_readonly_write", "access": "write",
                              "address": hex(address), "size": len(data)})
        region = self.region(address, len(data))
        if not region:
            raise PyMemError({"kind": "unmapped_or_readonly_write", "access": "write",
                              "address": hex(address), "size": len(data)})
        for i, b in enumerate(data):
            self.ram[address + i] = b


# ---- config (mirrors the probe_boot default regions + a handoff-like region) --
REGIONS = [
    (0x00123000, STACK_TOP, "bootstrap_stack"),
    (BOOT_ARGS, BOOT_ARGS + 24, "hypothesized_boot_argument_object"),
    (0x0019fd00, 0x0019fd18, "typed_boot_device_info"),
    (0x00001000, 0x00002000, "extra_ram"),
]
ROM_LEN = 0x20000
APP_LEN = 0x100
READONLY_BASE = 0x0019fd40
READONLY_LEN = 8


def build_pair(seed):
    rng = random.Random(seed)
    rom = bytes(rng.randrange(256) for _ in range(ROM_LEN))
    app = bytes(rng.randrange(256) for _ in range(APP_LEN))
    readonly = bytes(rng.randrange(256) for _ in range(READONLY_LEN))
    pll = rng.getrandbits(32)

    go = StrictMem()
    go.set_rom(rom)
    go.set_app(ENTRY, app)
    go.add_readonly(READONLY_BASE, readonly)
    go.set_pll(pll, True)
    for lo, hi, kind in REGIONS:
        go.add_region(lo, hi, kind)

    pm = PyMemory(regions=REGIONS, rom=rom, entry=ENTRY, app=app,
                  readonly=[(READONLY_BASE, readonly)], pll=pll)
    return go, pm, rom, app, readonly, pll


def go_outcome(go, op):
    if op[0] == "read":
        return go.read(op[1], op[2])
    return go.write(op[1], op[2])


def py_outcome(pm, op):
    if op[0] == "read":
        try:
            return ("ok", pm.read(op[1], op[2]))
        except PyMemError as e:
            return ("error", e.details)
    try:
        pm.write(op[1], op[2])
        return ("ok", None)
    except PyMemError as e:
        return ("error", e.details)


def run_ops(go, pm, ops, label):
    for i, op in enumerate(ops):
        g = go_outcome(go, op)
        p = py_outcome(pm, op)
        if g != p:
            raise AssertionError(f"[{label}] op#{i} {op!r} mismatch:\n  go={g}\n  py={p}")


def targeted_ops(rom, app, readonly, pll):
    ops = []
    # ROM mirror
    ops += [("read", 0, 4), ("read", 0x100, 6), ("read", ROM_LEN - 4, 4),
            ("read", ROM_LEN - 1, 2), ("read", ROM_LEN, 1), ("read", ROM_LEN + 1, 1)]
    # app mirror
    ops += [("read", ENTRY, 4), ("read", ENTRY + APP_LEN - 4, 4),
            ("read", ENTRY - 1, 4), ("read", ENTRY + APP_LEN, 1)]
    # readonly mirror
    ops += [("read", READONLY_BASE, READONLY_LEN), ("read", READONLY_BASE, 2),
            ("read", READONLY_BASE + READONLY_LEN - 1, 2)]
    # PLL fixture
    ops += [("read", PLL, 4), ("read", PLL, 2), ("read", PLL, 6)]
    # region: initialized vs uninitialized vs spanning
    ops += [("read", 0x1000, 4), ("read", 0x123000, 1), ("read", 0x123840 - 1, 2),
            ("read", 0x123000, 0x1000)]  # last spans the region end -> unmapped
    # unmapped
    ops += [("read", 0xdead0000, 4), ("read", 0xdead0000, 1)]
    # writes
    ops += [("write", 0, b"\x01\x02\x03\x04"),              # rom reject
            ("write", ROM_LEN - 2, b"\x01\x02\x03\x04"),    # partial rom reject
            ("write", 0x1000, b"\x11\x22\x33\x44"),         # region ok
            ("write", 0x123000, b"\xAA"),                    # region ok
            ("write", 0xdead0000, b"\x01\x02\x03\x04"),      # unmapped reject
            ("write", 0x19fd00, b"\x00" * 0x18),             # region ok
            ("write", 0x1fff, b"\x01\x02"),                  # spans region end -> reject
            ("write", PLL, b"\x00\x00\x00\x00")]             # unmapped reject
    return ops


def random_ops(rng, n):
    addrs = []
    # region interiors and boundaries
    for lo, hi, _ in REGIONS:
        addrs += [lo, hi - 1, hi, hi + 1, lo + 1]
        for _ in range(6):
            addrs.append(rng.randrange(lo, hi))
    # rom / app / readonly / pll / unmapped
    addrs += [0, 1, ROM_LEN - 1, ROM_LEN, ROM_LEN + 1, ENTRY, ENTRY + APP_LEN - 1,
              ENTRY + APP_LEN, READONLY_BASE, READONLY_BASE + READONLY_LEN - 1,
              READONLY_BASE + READONLY_LEN, PLL, 0x1FE14, 0xdead0000]
    sizes = [1, 2, 4, 6, 8, 0x10, 0x20]
    ops = []
    for _ in range(n):
        addr = rng.choice(addrs)
        size = rng.choice(sizes)
        if rng.random() < 0.7:
            ops.append(("read", addr, size))
        else:
            ops.append(("write", addr, bytes(rng.randrange(256) for _ in range(size))))
    return ops


def test_differential():
    go, pm, rom, app, readonly, pll = build_pair(12345)
    run_ops(go, pm, targeted_ops(rom, app, readonly, pll), "targeted")

    # randomized read/write battery over both memories in lockstep
    rng = random.Random(999)
    run_ops(go, pm, random_ops(rng, 4000), "random-readwrite")

    # fetch-path battery (fetch reads of every interesting address/size)
    go.set_fetch(True)
    pm.fetch = True
    fetch_ops = [("read", a, s) for a in [0, 0x100, ROM_LEN - 1, ROM_LEN, ENTRY,
                                          ENTRY + APP_LEN - 1, ENTRY + APP_LEN,
                                          READONLY_BASE, READONLY_BASE + READONLY_LEN - 1,
                                          READONLY_BASE + READONLY_LEN, PLL, 0x1FE14,
                                          0x1000, 0x123000, 0x19fd00, 0xdead0000]
                 for s in [1, 2, 4, 6]]
    run_ops(go, pm, fetch_ops, "fetch")
    go.set_fetch(False)
    pm.fetch = False

    # PLL absent: non-word / absent reads fall to unmapped with the SDK hint
    go2, pm2, _, _, _, _ = build_pair(7)
    go2.set_pll(0, False)
    pm2.pll = None
    run_ops(go2, pm2, [("read", PLL, 4), ("read", PLL, 2)], "pll-absent")

    print("RESULT: PASS (Go strict memory matches the Python Memory semantics byte-for-byte)")
    return 0


def test_real_memory_anchor():
    """Anchor the transcription to the real probe_boot.Memory: a minimal explore
    run must stop with the same uninitialized_memory details my reference and
    the Go memory produce for an unwritten bootstrap_stack word."""
    from probe_boot import explore, application
    from probe_pi32v2 import upstream

    module = upstream()
    firmware = ROOT / "firmware/QCY_H3S_7018F_1T2_release_note_V1.1.8_011.ufw"
    if not firmware.exists():
        print("anchor skipped: official firmware not present")
        return 0
    app = application(firmware)
    regs = [0] * 16
    regs[4] = 0x123702
    regs[5] = 0xABCD
    regs[0] = 0x11223344
    result = explore(module, app, name="mem_anchor",
                     diagnostics={"entry_pc": 0x6011370, "initial_sp": STACK_TOP,
                                  "initial_gprs": regs, "exact_parallel_stores": True},
                     budget=1)
    stop = result["stop"]
    assert stop["kind"] == "uninitialized_memory", stop
    assert stop["region"] == "bootstrap_stack", stop
    assert stop["address"] == hex(0x123700), stop
    assert stop["first_missing"] == hex(0x123700), stop

    # the same word through my reference and the Go memory must agree
    go, pm, _, _, _, _ = build_pair(12345)
    assert go.read(0x123700, 4) == ("error", {
        "kind": "uninitialized_memory", "access": "read", "address": "0x123700",
        "size": 4, "region": "bootstrap_stack", "first_missing": "0x123700"})
    assert py_outcome(pm, ("read", 0x123700, 4)) == ("error", {
        "kind": "uninitialized_memory", "access": "read", "address": "0x123700",
        "size": 4, "region": "bootstrap_stack", "first_missing": "0x123700"})
    print("anchor PASS (real probe_boot.Memory agrees with the reference and Go)")


if __name__ == "__main__":
    test_differential()
    test_real_memory_anchor()
    sys.exit(0)
