#!/usr/bin/env python3
"""Differential test: Go M6 Stage B after_step no-op judgment (after_step.go)
vs the real Python br28_machine.DualCoreMachine.after_step, over the four hook
anchors.

The harness builds a Python DualCoreMachine in a given hook configuration,
mirrors that configuration into the Go AfterStep judgment (via gosim_m6b.py),
runs one after_step on the Python side, and checks whether any of the four
hooks fired (cfg523 install / boot anchors / first-task hypothesis / D1
observer). That "fired" verdict must match the Go judgment's "not provably
no-op" verdict branch-for-branch. Targeted scenarios pin each hook branch;
randomized scenarios fuzz the configuration space.

No ROM is required.

Usage:
    cd tools/gosim && go build -buildmode=c-shared -o libgosim.so ./cshared
    python3 tools/gosim/test_m6_after_step_consistency.py
"""
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tools" / "gosim"))

import br28_machine  # noqa: E402
from br28_machine import (CFG523_OBJECT_ADDRESS, CFG523_PARSER_ENTRY,  # noqa: E402
                          DualCoreMachine)
from gosim_m6b import can_prove_noop  # noqa: E402

BOOT_ANCHORS = (0, 0xA144, 0xC5FE, 0xC602, 0x12CAF4, 0x12CAFA, 0x12CB00)
FIRST_TASK_ENTRY_BEFORE = 0x6065364
FIRST_TASK_ENTRY_NEXT = 0x6066106
FIRST_TASK_SETUP_PCS = [
    0x6066106, 0x6066108, 0x606610A, 0x606610C,
    0x6066112, 0x6066116, 0x606611A, 0x606611C,
    0x6066120, 0x6066122, 0x6066126, 0x6066128,
]
FIRST_TASK_TAILS = (0x606612C, 0x606612E)


class FakeMem:
    def __init__(self):
        self.writes = []

    def write(self, addr, data):
        self.writes.append((addr, bytes(data)))


class FakeCPU:
    def __init__(self, core_id, pc, R=None, rets=0, frame=None):
        self.core_id = core_id
        self.pc = pc
        self.R = list(R) if R is not None else [0] * 16
        self.rets = rets
        self.mem = FakeMem()
        self.first_task_return_frame = frame
        self.icfg = 0x300
        self.icfg_known = True


class FakeHW:
    def __init__(self):
        self.core_icfg = {}
        self.current_core = 0


class FakeObserver:
    def __init__(self):
        self.notes = []
        self.window_stop_request = None

    def note_step(self, cpu, before_pc, completed, stalled=False):
        self.notes.append((before_pc, completed, stalled))


def new_machine():
    return DualCoreMachine()


def configure(m, cpu, *, cfg523=None, installed=None, hypothesis=None,
              setup_index=None, observer=None):
    """Wire a real DualCoreMachine into the given hook configuration."""
    m.cores = {cpu.core_id: cpu}
    m.hardware = FakeHW()
    m.cfg523_object_bytes = cfg523
    m.cfg523_installed_at = ({"pc": "0x1", "object_bytes": 70} if installed else None)
    m.first_task_return_hypothesis = hypothesis
    if setup_index is not None:
        cid = cpu.core_id
        m.first_task_setup[cid] = {"index": setup_index, "pcs": [],
                                   "tcb": None, "base": None}
    else:
        m.first_task_setup = {}
    br28_machine.d1_window_observer = observer
    return m


def go_config(m):
    setup_index = [-1, -1]
    for cid, s in m.first_task_setup.items():
        setup_index[cid] = s["index"]
    frame = [False, False]
    for cid, c in m.cores.items():
        frame[cid] = c.first_task_return_frame is not None
    return {
        "cfg523_enabled": m.cfg523_object_bytes is not None,
        "cfg523_installed": m.cfg523_installed_at is not None,
        "first_task_hypothesis": m.first_task_return_hypothesis is not None,
        "observer_active": br28_machine.d1_window_observer is not None,
        "first_task_setup_index": setup_index,
        "first_task_return_frame": frame,
    }


def py_fired(m, cpu, before_pc):
    """Run one Python after_step and report which of the four hooks fired."""
    pre_installed = m.cfg523_installed_at
    pre_anchor = m.boot_anchor_count
    pre_calls = set(m.first_task_calls)
    pre_setup_keys = set(m.first_task_setup)
    pre_setup_index = {k: m.first_task_setup[k]["index"] for k in pre_setup_keys}
    frame = cpu.first_task_return_frame
    pre_frame_next = None if frame is None else frame.get("next_pc")
    pre_mem_writes = len(cpu.mem.writes)
    obs = br28_machine.d1_window_observer
    pre_obs_notes = 0 if obs is None else len(obs.notes)

    m.after_step(cpu, before_pc)

    reasons = []
    if m.cfg523_installed_at != pre_installed:
        reasons.append("cfg523")
    if m.boot_anchor_count != pre_anchor:
        reasons.append("boot_anchor")
    if set(m.first_task_calls) != pre_calls:
        reasons.append("first_task_entry")
    post_setup_keys = set(m.first_task_setup)
    if post_setup_keys != pre_setup_keys:
        reasons.append("first_task_setup")
    else:
        post_setup_index = {k: m.first_task_setup[k]["index"] for k in post_setup_keys}
        if post_setup_index != pre_setup_index:
            reasons.append("first_task_setup")
    post_frame_next = None if frame is None else frame.get("next_pc")
    if post_frame_next != pre_frame_next:
        reasons.append("first_task_frame_tail")
    if len(cpu.mem.writes) != pre_mem_writes:
        reasons.append("cfg523_write")
    if obs is not None and len(obs.notes) != pre_obs_notes:
        reasons.append("observer")
    return reasons


def run_one(label, cpu, before_pc, **cfg):
    m = configure(new_machine(), cpu, **cfg)
    go_noop = can_prove_noop(go_config(m), cpu.core_id, before_pc, cpu.pc)
    py = py_fired(m, cpu, before_pc)
    if go_noop and py:
        raise AssertionError(
            f"[{label}] Go proved no-op but Python fired: {py} "
            f"(core={cpu.core_id} before={before_pc:#x} next={cpu.pc:#x})")
    if not go_noop and not py:
        raise AssertionError(
            f"[{label}] Go said a hook fires but Python after_step did nothing "
            f"(core={cpu.core_id} before={before_pc:#x} next={cpu.pc:#x})")


def targeted():
    # Hook 1: cfg523 fixture install.
    run_one("cfg523 fires", FakeCPU(0, CFG523_PARSER_ENTRY), 0x60010B2,
            cfg523=bytes(70))
    run_one("cfg523 not at entry", FakeCPU(0, 0x102), 0x100, cfg523=bytes(70))
    run_one("cfg523 installed", FakeCPU(0, CFG523_PARSER_ENTRY), 0x60010B2,
            cfg523=bytes(70), installed=True)
    run_one("cfg523 core-1 gate", FakeCPU(1, CFG523_PARSER_ENTRY), 0x60010B2,
            cfg523=bytes(70))

    # Hook 2: core-1 boot anchors.
    for pc in BOOT_ANCHORS:
        run_one(f"boot anchor {pc:#x}", FakeCPU(1, 0xC600), pc)
    run_one("boot anchor core-0 gate", FakeCPU(0, 0xC600), 0xC5FE)
    run_one("boot anchor not anchor", FakeCPU(1, 0x1236), 0x1234)

    # Hook 3: first-task hypothesis.
    run_one("first-task entry", FakeCPU(0, FIRST_TASK_ENTRY_NEXT),
            FIRST_TASK_ENTRY_BEFORE, hypothesis="closed_gate8")
    for i, pc in enumerate(FIRST_TASK_SETUP_PCS):
        run_one(f"first-task trace index {i}", FakeCPU(0, pc + 2), pc,
                hypothesis="closed_gate8", setup_index=i)
    run_one("first-task no in-flight setup", FakeCPU(0, 0x6066108), 0x6066106,
            hypothesis="closed_gate8")
    run_one("first-task frame tail lo", FakeCPU(0, 0x606612E, frame={"next_pc": 0x6066130}),
            0x606612C, hypothesis="closed_gate8")
    run_one("first-task frame tail hi", FakeCPU(0, 0x6066130, frame={"next_pc": 0x6066132}),
            0x606612E, hypothesis="closed_gate8")
    run_one("first-task tail not armed", FakeCPU(0, 0x606612E), 0x606612C,
            hypothesis="closed_gate8")
    run_one("first-task hypothesis off", FakeCPU(0, FIRST_TASK_ENTRY_NEXT),
            FIRST_TASK_ENTRY_BEFORE)

    # Hook 4: D1 observer.
    run_one("observer active", FakeCPU(0, 0x102), 0x100, observer=FakeObserver())


def randomized(n):
    rng = random.Random(0x6D6B)
    pcs = [0x100, 0x60010B2, 0x6065364, 0x6066106, 0x6066116, 0x606612C,
           0x606612E, 0xC5FE, 0x12CB00, 0x6066128]
    for _ in range(n):
        core = rng.choice([0, 1])
        before = rng.choice(pcs)
        next_pc = rng.choice(pcs)
        cfg = {}
        if rng.random() < 0.5:
            cfg["cfg523"] = bytes(70)
            cfg["installed"] = rng.random() < 0.5
        if rng.random() < 0.5:
            cfg["hypothesis"] = "closed_gate8"
            if rng.random() < 0.5:
                cfg["setup_index"] = rng.randrange(0, len(FIRST_TASK_SETUP_PCS))
        if rng.random() < 0.3:
            cfg["observer"] = FakeObserver()
        frame = None
        if rng.random() < 0.3:
            # A sentinel distinct from any real pc so the tail-branch mutation
            # (frame['next_pc'] = cpu.pc) is always observable.
            frame = {"next_pc": 0xFFFFFFFE}
        run_one(f"rand{_}", FakeCPU(core, next_pc, frame=frame), before, **cfg)


def main():
    targeted()
    randomized(3000)
    br28_machine.d1_window_observer = None
    print("after_step judgment: PASS (Go matches DualCoreMachine.after_step "
          "branch-for-branch over the four hook anchors)")


if __name__ == "__main__":
    main()
