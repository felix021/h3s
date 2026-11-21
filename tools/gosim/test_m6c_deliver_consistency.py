#!/usr/bin/env python3
"""Differential test: Go M6 Stage C delivery projections (deliver.go) vs the
real Python br28_machine.DualCoreMachine.deliver_* / return_* methods,
branch-for-branch.

Each scenario builds a real DualCoreMachine with minimal fake CPUs/hardware in a
given state, mirrors that state into the Go DeliverMachine (via gosim_m6c.py),
runs one delivery entry or RTI return on each side, and compares the error
(ValueError message / Blocked details) plus every observable side effect (core
delivery fields, idle_wait set, the five delivery-event lists, the event list,
the software-IRQ acknowledge words). Targeted scenarios pin each branch; the
randomized scenarios fuzz the state space.

The IRQ vector table is modeled as an explicit per-slot handler word (nil =
uninitialized), mirroring cpu.mem.read(slot, 4) -> int.from_bytes(...,'little')
with the vector_slot_uninitialized catch. No ROM is required.

Usage:
    cd tools/gosim && go build -buildmode=c-shared -o libgosim.so ./cshared
    python3 tools/gosim/test_m6c_deliver_consistency.py
"""
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tools" / "gosim"))

from br28_machine import Blocked, DualCoreMachine  # noqa: E402
from gosim_m6c import GosimDeliver  # noqa: E402

SLOT_BASE = 0x19FE00

# Anchors (br28_machine constants).
SW_IRQ_RTI_TAILS = (0x12ADE8, 0x6066342)
CORE_TICK_RTI_TAIL = 0x60661E6
TIMER0_HANDLER = 0x12ECE8
TIMER0_RTI_TAIL = 0x12ED0C
AUDIO_IRQ_RTI_TAIL = 0x602F452
SRC_SYNC_RTI_TAIL = 0x12CAD2

# Nominal handler words (arbitrary; the vector read is the only thing they feed).
SW_HANDLER = 0x12AD2A
CORE_TICK_HANDLER = 0x12B608
AUDIO_HANDLER = 0x602F406
SRC_SYNC_HANDLER = 0x12CA24

# operation -> gosim_m6c op code.
OP = {
    "software_irq": 0,
    "core_tick": 1,
    "timer0_irq": 2,
    "audio_dma_irq": 3,
    "src_sync_irq": 4,
    "return_software_irq": 5,
    "return_core_tick": 6,
    "return_timer0_irq": 7,
    "return_audio_dma_irq": 8,
    "return_src_sync_irq": 9,
}

# ops that take a cpu object in Python (the rest take a core id int).
CPU_OBJECT_OPS = {"software_irq", "return_software_irq", "return_core_tick",
                  "return_timer0_irq", "return_audio_dma_irq", "return_src_sync_irq"}

IRQ_HANDLER_CONTEXTS = ("software_irq_handler", "core_tick_handler",
                        "timer0_irq_handler", "audio_dma_irq_handler",
                        "src_sync_irq_handler")


class _FakePW:
    """Minimal twin of br28_model.PartialWord.apply('and'/'or'), verbatim from the
    real model (which the Go PartialWord.Apply is already differential-tested
    against); avoids importing the ROM-gated br28_model."""
    def __init__(self, value, known):
        self.value = value & known & 0xFFFFFFFF
        self.known = known & 0xFFFFFFFF

    def apply(self, kind, operand):
        operand &= 0xFFFFFFFF
        if kind == "or":
            self.known |= operand
            self.value |= operand
        elif kind == "and":
            self.known |= (~operand) & 0xFFFFFFFF
            self.value &= operand
        else:
            raise ValueError("unsupported partial-word operation")


class _FakeSWIrq:
    def __init__(self, pending, acknowledgements=0):
        self.pending = pending  # list of 2 _FakePW
        self.acknowledgements = acknowledgements

    def acknowledge(self, core, bit):
        self.pending[core].apply("and", ~(1 << bit) & 0xFFFFFFFF)
        self.acknowledgements += 1


class _FakeIRQ:
    def __init__(self, config):
        self.config = config  # list of 32 (None or int)


class _FakeHW:
    def __init__(self, sw, irq):
        self.software_irq = sw
        self.irq = irq


class _FakeMem:
    def __init__(self, slots):
        self.slots = slots  # {addr: int or None}

    def read(self, addr, n):
        h = self.slots.get(addr)
        if h is None:
            raise Blocked("uninitialized_memory", address=hex(addr))
        return h.to_bytes(4, "little")


class _FakeCPU:
    def __init__(self):
        self.core_id = 0
        self.pc = 0
        self.steps = 0
        self.sp = 0
        self.sp_known = True
        self.usp = None
        self.ssp = None
        self.reti = None
        self.flags = 0
        self.irq_psr = None
        self.active_context = None
        self.bootstrap_context = False
        self.after_then = None
        self.repeat = None
        self.last_explicit_ssp = None
        self.audio_irq_outer_context = None
        self.mem = None


def fc(core_id, **kw):
    c = _FakeCPU()
    c.core_id = core_id
    for k, v in kw.items():
        setattr(c, k, v)
    return c


def slot(irq):
    return SLOT_BASE + irq * 4


def nibble_word(nibble):
    """Place a nibble into the low 16 bits of a config word so that
    config[irq//8] >> ((irq%8)*4) & 15 == nibble for irqs whose index%8 == 4
    (irq 4 and irq 68 both land in nibble position 4)."""
    return (nibble & 15) << 16


def new_machine(**flags):
    return DualCoreMachine(**flags)


def configure(m, cores, idle_wait, slots=None, sw_pending=((0, 15), (0, 15)),
              sw_ack=0, irq_config=None, deliveries=None):
    slots = dict(slots or {})
    for cid, cpu in cores.items():
        cpu.mem = _FakeMem(slots)
    m.cores = {cid: cpu for cid, cpu in cores.items()}
    m.idle_wait_cores = set(idle_wait)
    m._gosim_slots = slots
    m.hardware = _FakeHW(
        _FakeSWIrq([_FakePW(v, k) for v, k in sw_pending], sw_ack),
        _FakeIRQ(list(irq_config) if irq_config is not None else [None] * 32))
    d = deliveries or {}
    for attr in ("software_irq_deliveries", "core_tick_deliveries", "timer0_irq_deliveries",
                 "audio_irq_deliveries", "src_sync_irq_deliveries"):
        setattr(m, attr, list(d.get(attr, [])))
    m.events = list(d.get("events", []))
    m.event_count = d.get("event_count", 0)
    return m


def core_dict(c):
    if c is None:
        return None
    outer = getattr(c, "audio_irq_outer_context", None)
    return {
        "id": c.core_id, "pc": c.pc, "steps": c.steps, "sp": c.sp, "sp_known": c.sp_known,
        "usp": c.usp, "ssp": c.ssp, "reti": c.reti, "flags": c.flags,
        "irq_psr": getattr(c, "irq_psr", None),
        "active_context": getattr(c, "active_context", None),
        "bootstrap_context": getattr(c, "bootstrap_context", False),
        "repeat_active": (getattr(c, "after_then", None) is not None
                          or getattr(c, "repeat", None) is not None),
        "last_explicit_ssp": getattr(c, "last_explicit_ssp", None),
        "audio_irq_outer_context": (None if outer is None else
                                    {"active_context": outer["active_context"],
                                     "reti": outer["reti"], "sp": outer["sp"]}),
    }


def py_to_go_state(m):
    hw = m.hardware
    return {
        "cores": [core_dict(m.cores.get(0)), core_dict(m.cores.get(1))],
        "idle_wait": [0 in m.idle_wait_cores, 1 in m.idle_wait_cores],
        "software_irq_entry": m.software_irq_entry,
        "software_irq_entry_banks": m.software_irq_entry_banks,
        "software_irq_rti": m.software_irq_rti,
        "core_tick_entry": m.core_tick_entry,
        "core_tick_entry_banks": m.core_tick_entry_banks,
        "core_tick_rti": m.core_tick_rti,
        "timer0_irq_entry": m.timer0_irq_entry,
        "timer0_irq_entry_banks": m.timer0_irq_entry_banks,
        "timer0_irq_rti": m.timer0_irq_rti,
        "audio_irq_entry": m.audio_irq_entry,
        "audio_irq_entry_banks": m.audio_irq_entry_banks,
        "audio_irq_rti": m.audio_irq_rti,
        "src_sync_irq_entry": m.src_sync_irq_entry,
        "src_sync_irq_entry_banks": m.src_sync_irq_entry_banks,
        "src_sync_irq_rti": m.src_sync_irq_rti,
        "repeat_irq_delivery": m.repeat_irq_delivery,
        "idle_wake_consumes_idle": m.idle_wake_consumes_idle,
        "vector_slots": dict(m._gosim_slots),
        "software_irq_pending": [
            {"value": hw.software_irq.pending[0].value, "known": hw.software_irq.pending[0].known},
            {"value": hw.software_irq.pending[1].value, "known": hw.software_irq.pending[1].known},
        ],
        "software_irq_acknowledgements": hw.software_irq.acknowledgements,
        "irq_config": list(hw.irq.config),
        "software_irq_deliveries": list(m.software_irq_deliveries),
        "core_tick_deliveries": list(m.core_tick_deliveries),
        "timer0_irq_deliveries": list(m.timer0_irq_deliveries),
        "audio_irq_deliveries": list(m.audio_irq_deliveries),
        "src_sync_irq_deliveries": list(m.src_sync_irq_deliveries),
        "events": list(m.events),
        "event_count": m.event_count,
    }


def canonical_py(m):
    return {
        "cores": [core_dict(m.cores.get(0)), core_dict(m.cores.get(1))],
        "idle_wait": sorted(m.idle_wait_cores),
        "software_irq_deliveries": list(m.software_irq_deliveries),
        "core_tick_deliveries": list(m.core_tick_deliveries),
        "timer0_irq_deliveries": list(m.timer0_irq_deliveries),
        "audio_irq_deliveries": list(m.audio_irq_deliveries),
        "src_sync_irq_deliveries": list(m.src_sync_irq_deliveries),
        "events": list(m.events),
        "event_count": m.event_count,
        "software_irq_pending": [
            {"value": m.hardware.software_irq.pending[0].value,
             "known": m.hardware.software_irq.pending[0].known},
            {"value": m.hardware.software_irq.pending[1].value,
             "known": m.hardware.software_irq.pending[1].known},
        ],
        "software_irq_acknowledgements": m.hardware.software_irq.acknowledgements,
    }


def canonical_go(gs):
    return {
        "cores": gs["cores"],
        "idle_wait": sorted(i for i, b in enumerate(gs["idle_wait"]) if b),
        "software_irq_deliveries": gs["software_irq_deliveries"],
        "core_tick_deliveries": gs["core_tick_deliveries"],
        "timer0_irq_deliveries": gs["timer0_irq_deliveries"],
        "audio_irq_deliveries": gs["audio_irq_deliveries"],
        "src_sync_irq_deliveries": gs["src_sync_irq_deliveries"],
        "events": gs["events"],
        "event_count": gs["event_count"],
        "software_irq_pending": gs["software_irq_pending"],
        "software_irq_acknowledgements": gs["software_irq_acknowledgements"],
    }


def py_call(m, op, core_id, irq):
    if op == "software_irq":
        return m.deliver_software_irq(m.cores[core_id], irq)
    if op == "core_tick":
        return m.deliver_core_tick(core_id)
    if op == "timer0_irq":
        return m.deliver_timer0_irq(core_id)
    if op == "audio_dma_irq":
        return m.deliver_audio_dma_irq(core_id)
    if op == "src_sync_irq":
        return m.deliver_src_sync_irq(core_id)
    if op == "return_software_irq":
        return m.return_software_irq(m.cores[core_id])
    if op == "return_core_tick":
        return m.return_core_tick(m.cores[core_id])
    if op == "return_timer0_irq":
        return m.return_timer0_irq(m.cores[core_id])
    if op == "return_audio_dma_irq":
        return m.return_audio_dma_irq(m.cores[core_id])
    if op == "return_src_sync_irq":
        return m.return_src_sync_irq(m.cores[core_id])
    raise AssertionError("unknown op %r" % op)


def run_py(m, op, core_id, irq):
    try:
        py_call(m, op, core_id, irq)
        return None
    except ValueError as e:
        return ("ValueError", str(e))
    except Blocked as e:
        return ("Blocked", e.details)


def run_go(go, op, core_id, irq):
    try:
        go.call(OP[op], core_id, irq)
        return None
    except Exception as e:  # noqa: BLE001
        if getattr(e, "kind", None) in ("ValueError", "Blocked"):
            return (e.kind, e.payload)
        raise


def run_one(m, label, op, core_id, irq=0):
    go = GosimDeliver()
    go.load(py_to_go_state(m))
    py_err = run_py(m, op, core_id, irq)
    go_err = run_go(go, op, core_id, irq)
    if py_err is not None or go_err is not None:
        if py_err is None or go_err is None:
            raise AssertionError(f"[{label}] error mismatch: py={py_err} go={go_err}")
        if py_err[0] != go_err[0] or py_err[1] != go_err[1]:
            raise AssertionError(f"[{label}] error mismatch:\n  py={py_err}\n  go={go_err}")
        return
    p = canonical_py(m)
    g = canonical_go(go.state())
    if p != g:
        raise AssertionError(f"[{label}] state mismatch:\n  go={g}\n  py={p}")


# --- targeted scenarios -------------------------------------------------------

def targeted():
    cases = []

    def add(label, m, op, core_id, irq=0):
        cases.append((label, m, op, core_id, irq))

    # software_irq: entry disabled.
    m = configure(new_machine(), {0: fc(0, pc=0x100)}, [], slots={slot(127): SW_HANDLER})
    add("sw-disabled", m, "software_irq", 0, 127)

    # software_irq: wrong irq for core 0.
    m = configure(new_machine(software_irq_entry=True),
                  {0: fc(0, pc=0x100)}, [], slots={slot(127): SW_HANDLER})
    add("sw-wrong-irq", m, "software_irq", 0, 126)

    # software_irq: bootstrap context.
    m = configure(new_machine(software_irq_entry=True),
                  {0: fc(0, pc=0x100, bootstrap_context=True)}, [],
                  slots={slot(127): SW_HANDLER})
    add("sw-bootstrap", m, "software_irq", 0, 127)

    # software_irq: repeat active.
    m = configure(new_machine(software_irq_entry=True),
                  {0: fc(0, pc=0x100, repeat={"remaining": 3})}, [],
                  slots={slot(127): SW_HANDLER})
    add("sw-repeat-active", m, "software_irq", 0, 127)

    # software_irq: one-shot already delivered.
    m = configure(new_machine(software_irq_entry=True),
                  {0: fc(0, pc=0x100)}, [], slots={slot(127): SW_HANDLER},
                  deliveries={"software_irq_deliveries": [{"core": 0}]})
    add("sw-one-shot", m, "software_irq", 0, 127)

    # software_irq: repeat delivery onto an in-flight handler.
    m = configure(new_machine(software_irq_entry=True, repeat_irq_delivery=True),
                  {0: fc(0, pc=0x100, active_context="core_tick_handler")}, [],
                  slots={slot(127): SW_HANDLER})
    add("sw-in-flight", m, "software_irq", 0, 127)

    # software_irq: vector slot uninitialized.
    m = configure(new_machine(software_irq_entry=True),
                  {0: fc(0, pc=0x100)}, [], slots={})
    add("sw-vector-uninit", m, "software_irq", 0, 127)

    # software_irq: success, no banks.
    m = configure(new_machine(software_irq_entry=True),
                  {0: fc(0, pc=0x100, steps=5, sp=0x1000, flags=3)},
                  [], slots={slot(127): SW_HANDLER}, sw_pending=((8, 15), (0, 15)))
    add("sw-ok", m, "software_irq", 0, 127)

    # software_irq: success with banks.
    m = configure(new_machine(software_irq_entry=True, software_irq_entry_banks=True),
                  {0: fc(0, pc=0x100, steps=5, sp=0x1000, flags=3, last_explicit_ssp=0x2000)},
                  [], slots={slot(127): SW_HANDLER}, sw_pending=((8, 15), (0, 15)))
    add("sw-ok-banks", m, "software_irq", 0, 127)

    # software_irq: banks but no explicit SSP -> ValueError.
    m = configure(new_machine(software_irq_entry=True, software_irq_entry_banks=True),
                  {0: fc(0, pc=0x100, sp=0x1000)}, [], slots={slot(127): SW_HANDLER})
    add("sw-banks-no-ssp", m, "software_irq", 0, 127)

    # software_irq: banks but sp unknown -> ValueError.
    m = configure(new_machine(software_irq_entry=True, software_irq_entry_banks=True),
                  {0: fc(0, pc=0x100, sp_known=False, last_explicit_ssp=0x2000)},
                  [], slots={slot(127): SW_HANDLER})
    add("sw-banks-sp-unknown", m, "software_irq", 0, 127)

    # software_irq: woke (idle_wait) with idle_wake_consumes_idle.
    m = configure(new_machine(software_irq_entry=True, idle_wake_consumes_idle=True),
                  {0: fc(0, pc=0x12b56c, steps=5, sp=0x1000)}, [0],
                  slots={slot(127): SW_HANDLER})
    add("sw-woke-consume", m, "software_irq", 0, 127)

    # software_irq: woke without consume (return address = pc itself).
    m = configure(new_machine(software_irq_entry=True),
                  {0: fc(0, pc=0x12b56c, steps=5, sp=0x1000)}, [0],
                  slots={slot(127): SW_HANDLER})
    add("sw-woke-noconsume", m, "software_irq", 0, 127)

    # software_irq: core 1, irq 125.
    m = configure(new_machine(software_irq_entry=True),
                  {0: fc(0, pc=0x100), 1: fc(1, pc=0x200, steps=1, sp=0x1800)},
                  [], slots={slot(125): SW_HANDLER}, sw_pending=((0, 15), (2, 15)))
    add("sw-core1-125", m, "software_irq", 1, 125)

    # core_tick: entry disabled.
    m = configure(new_machine(), {0: fc(0, pc=0x100)}, [], slots={slot(3): CORE_TICK_HANDLER})
    add("ct-disabled", m, "core_tick", 0)

    # core_tick: core absent.
    m = configure(new_machine(core_tick_entry=True), {0: fc(0, pc=0x100)}, [],
                  slots={slot(3): CORE_TICK_HANDLER})
    add("ct-core-absent", m, "core_tick", 1)

    # core_tick: success no banks.
    m = configure(new_machine(core_tick_entry=True),
                  {0: fc(0, pc=0x100, steps=5, sp=0x1000, flags=2)},
                  [], slots={slot(3): CORE_TICK_HANDLER})
    add("ct-ok", m, "core_tick", 0)

    # core_tick: success with banks + woke.
    m = configure(new_machine(core_tick_entry=True, core_tick_entry_banks=True,
                              idle_wake_consumes_idle=True),
                  {0: fc(0, pc=0x12b56c, steps=5, sp=0x1000, flags=1, last_explicit_ssp=0x2000)},
                  [0], slots={slot(3): CORE_TICK_HANDLER})
    add("ct-ok-banks-woke", m, "core_tick", 0)

    # core_tick: one-shot.
    m = configure(new_machine(core_tick_entry=True),
                  {0: fc(0, pc=0x100)}, [], slots={slot(3): CORE_TICK_HANDLER},
                  deliveries={"core_tick_deliveries": [{"core": 0}]})
    add("ct-one-shot", m, "core_tick", 0)

    # timer0_irq: success (no banks), then the identity-mismatch return.
    m = configure(new_machine(timer0_irq_entry=True, timer0_irq_rti=True),
                  {0: fc(0, pc=0x100, steps=5, sp=0x1000, flags=1)},
                  [], slots={slot(4): TIMER0_HANDLER})
    add("t0-ok", m, "timer0_irq", 0)

    m = configure(new_machine(timer0_irq_entry=True, timer0_irq_rti=True),
                  {0: fc(0, pc=TIMER0_RTI_TAIL, active_context="timer0_irq_handler",
                          usp=0x1000, reti=0x100, steps=5)},
                  [], slots={}, deliveries={"timer0_irq_deliveries": []})
    add("t0-rti-identity-mismatch", m, "return_timer0_irq", 0)

    m = configure(new_machine(timer0_irq_entry=True, timer0_irq_rti=True),
                  {0: fc(0, pc=TIMER0_RTI_TAIL, active_context="timer0_irq_handler",
                          usp=0x1000, reti=0x100, steps=5)},
                  [], slots={},
                  deliveries={"timer0_irq_deliveries": [
                      {"core": 0, "handler": hex(TIMER0_HANDLER)}]})
    add("t0-rti-ok", m, "return_timer0_irq", 0)

    # audio_dma_irq: nested into a non-nestable handler -> Blocked.
    m = configure(new_machine(audio_irq_entry=True, repeat_irq_delivery=True),
                  {0: fc(0, pc=0x100, active_context="software_irq_handler")},
                  [], slots={slot(68): AUDIO_HANDLER})
    add("audio-nest-not-modeled", m, "audio_dma_irq", 0)

    # audio_dma_irq: nested priority not strictly higher -> ValueError.
    cfg = [None] * 32
    cfg[8] = nibble_word(3)  # irq68 prio 1
    cfg[0] = nibble_word(5)  # irq4  prio 2
    m = configure(new_machine(audio_irq_entry=True, repeat_irq_delivery=True),
                  {0: fc(0, pc=0x100, active_context="timer0_irq_handler",
                         reti=0x200, sp=0x2000)},
                  [], slots={slot(68): AUDIO_HANDLER}, irq_config=cfg)
    add("audio-nest-lower-prio", m, "audio_dma_irq", 0)

    # audio_dma_irq: nested with missing config word -> Blocked.
    m = configure(new_machine(audio_irq_entry=True, repeat_irq_delivery=True),
                  {0: fc(0, pc=0x100, active_context="timer0_irq_handler",
                         reti=0x200, sp=0x2000)},
                  [], slots={slot(68): AUDIO_HANDLER}, irq_config=[None] * 32)
    add("audio-nest-unconfigured", m, "audio_dma_irq", 0)

    # audio_dma_irq: nested success (prio 2 > 1).
    cfg = [None] * 32
    cfg[8] = nibble_word(5)  # irq68 prio 2
    cfg[0] = nibble_word(3)  # irq4  prio 1
    m = configure(new_machine(audio_irq_entry=True, repeat_irq_delivery=True),
                  {0: fc(0, pc=0x100, active_context="timer0_irq_handler",
                         reti=0x200, sp=0x2000, flags=2)},
                  [], slots={slot(68): AUDIO_HANDLER}, irq_config=cfg)
    add("audio-nest-ok", m, "audio_dma_irq", 0)

    # audio_dma_irq: non-nested success with banks.
    m = configure(new_machine(audio_irq_entry=True, audio_irq_entry_banks=True),
                  {0: fc(0, pc=0x100, steps=5, sp=0x1000, flags=3, last_explicit_ssp=0x2000)},
                  [], slots={slot(68): AUDIO_HANDLER})
    add("audio-ok-banks", m, "audio_dma_irq", 0)

    # audio return: nested restore of the outer timer0 context.
    m = configure(new_machine(audio_irq_entry=True, audio_irq_rti=True),
                  {0: fc(0, pc=AUDIO_IRQ_RTI_TAIL, active_context="audio_dma_irq_handler",
                         reti=0x300, sp=0x2000, steps=5,
                         audio_irq_outer_context={"active_context": "timer0_irq_handler",
                                                  "reti": 0x200, "sp": 0x2000})},
                  [], slots={}, deliveries={"audio_irq_deliveries": [{"core": 0}]})
    add("audio-rti-nested-ok", m, "return_audio_dma_irq", 0)

    # audio return: nested unbalanced SP -> ValueError.
    m = configure(new_machine(audio_irq_entry=True, audio_irq_rti=True),
                  {0: fc(0, pc=AUDIO_IRQ_RTI_TAIL, active_context="audio_dma_irq_handler",
                         reti=0x300, sp=0x2100, steps=5,
                         audio_irq_outer_context={"active_context": "timer0_irq_handler",
                                                  "reti": 0x200, "sp": 0x2000})},
                  [], slots={}, deliveries={"audio_irq_deliveries": [{"core": 0}]})
    add("audio-rti-nested-unbalanced", m, "return_audio_dma_irq", 0)

    # audio return: non-nested success.
    m = configure(new_machine(audio_irq_entry=True, audio_irq_rti=True),
                  {0: fc(0, pc=AUDIO_IRQ_RTI_TAIL, active_context="audio_dma_irq_handler",
                         usp=0x1000, reti=0x100, steps=5)},
                  [], slots={}, deliveries={"audio_irq_deliveries": [{"core": 0}]})
    add("audio-rti-ok", m, "return_audio_dma_irq", 0)

    # src_sync_irq: success.
    m = configure(new_machine(src_sync_irq_entry=True, src_sync_irq_entry_banks=True),
                  {0: fc(0, pc=0x100, steps=5, sp=0x1000, flags=1, last_explicit_ssp=0x2000)},
                  [], slots={slot(89): SRC_SYNC_HANDLER})
    add("srcsync-ok-banks", m, "src_sync_irq", 0)

    # src_sync return: success.
    m = configure(new_machine(src_sync_irq_entry=True, src_sync_irq_rti=True),
                  {0: fc(0, pc=SRC_SYNC_RTI_TAIL, active_context="src_sync_irq_handler",
                         usp=0x1000, reti=0x100, steps=5)},
                  [], slots={}, deliveries={"src_sync_irq_deliveries": [{"core": 0}]})
    add("srcsync-rti-ok", m, "return_src_sync_irq", 0)

    # core_tick return: success.
    m = configure(new_machine(core_tick_entry=True, core_tick_rti=True),
                  {0: fc(0, pc=CORE_TICK_RTI_TAIL, active_context="core_tick_handler",
                         usp=0x1000, reti=0x100, steps=5)},
                  [], slots={})
    add("ct-rti-ok", m, "return_core_tick", 0)

    # software_irq return: success (tail B).
    m = configure(new_machine(software_irq_entry=True, software_irq_rti=True),
                  {0: fc(0, pc=SW_IRQ_RTI_TAILS[1], active_context="software_irq_handler",
                         usp=0x1000, reti=0x100, steps=5)},
                  [], slots={})
    add("sw-rti-ok", m, "return_software_irq", 0)

    # return guards: rti off / wrong pc / wrong context / missing usp / missing reti.
    m = configure(new_machine(), {0: fc(0, pc=SW_IRQ_RTI_TAILS[0],
                                        active_context="software_irq_handler",
                                        usp=0x1000, reti=0x100)}, [], slots={})
    add("sw-rti-off", m, "return_software_irq", 0)

    m = configure(new_machine(software_irq_entry=True, software_irq_rti=True),
                  {0: fc(0, pc=0x9999, active_context="software_irq_handler",
                         usp=0x1000, reti=0x100)}, [], slots={})
    add("sw-rti-wrong-pc", m, "return_software_irq", 0)

    m = configure(new_machine(software_irq_entry=True, software_irq_rti=True),
                  {0: fc(0, pc=SW_IRQ_RTI_TAILS[0], active_context=None,
                         usp=0x1000, reti=0x100)}, [], slots={})
    add("sw-rti-wrong-context", m, "return_software_irq", 0)

    m = configure(new_machine(software_irq_entry=True, software_irq_rti=True),
                  {0: fc(0, pc=SW_IRQ_RTI_TAILS[0], active_context="software_irq_handler",
                         usp=None, reti=0x100)}, [], slots={})
    add("sw-rti-no-usp", m, "return_software_irq", 0)

    m = configure(new_machine(software_irq_entry=True, software_irq_rti=True),
                  {0: fc(0, pc=SW_IRQ_RTI_TAILS[0], active_context="software_irq_handler",
                         usp=0x1000, reti=None)}, [], slots={})
    add("sw-rti-no-reti", m, "return_software_irq", 0)

    return cases


# --- randomized fuzz ----------------------------------------------------------

def random_scenario(rng):
    both = rng.random() < 0.8
    cores = {0: random_cpu(rng, 0)}
    if both:
        cores[1] = random_cpu(rng, 1)

    slots = {}
    for irq in (3, 4, 68, 89, 125, 126, 127):
        if rng.random() < 0.7:
            slots[slot(irq)] = rng.choice([SW_HANDLER, TIMER0_HANDLER, AUDIO_HANDLER,
                                           SRC_SYNC_HANDLER, CORE_TICK_HANDLER])
    idle = [cid for cid in cores if rng.random() < 0.4]

    flags = {
        "software_irq_entry": rng.random() < 0.5,
        "core_tick_entry": rng.random() < 0.5,
        "timer0_irq_entry": rng.random() < 0.5,
        "audio_irq_entry": rng.random() < 0.5,
        "src_sync_irq_entry": rng.random() < 0.5,
        "software_irq_rti": rng.random() < 0.5,
        "core_tick_rti": rng.random() < 0.5,
        "timer0_irq_rti": rng.random() < 0.5,
        "audio_irq_rti": rng.random() < 0.5,
        "src_sync_irq_rti": rng.random() < 0.5,
    }
    flags["repeat_irq_delivery"] = rng.random() < 0.4
    flags["idle_wake_consumes_idle"] = rng.random() < 0.5
    # The real constructor requires each rti projection to have its entry, and
    # repeat_irq_delivery to have at least one entry projection. Honor those
    # exact constraints so the machine can be constructed.
    flags["software_irq_entry"] = flags["software_irq_entry"] or flags["software_irq_rti"]
    flags["core_tick_entry"] = flags["core_tick_entry"] or flags["core_tick_rti"]
    flags["timer0_irq_entry"] = flags["timer0_irq_entry"] or flags["timer0_irq_rti"]
    flags["audio_irq_entry"] = flags["audio_irq_entry"] or flags["audio_irq_rti"]
    flags["src_sync_irq_entry"] = flags["src_sync_irq_entry"] or flags["src_sync_irq_rti"]
    if flags["repeat_irq_delivery"] and not (flags["software_irq_entry"] or flags["core_tick_entry"]
                                             or flags["timer0_irq_entry"] or flags["audio_irq_entry"]):
        flags["software_irq_entry"] = True
    flags["software_irq_entry_banks"] = flags["software_irq_entry"] and rng.random() < 0.5
    flags["core_tick_entry_banks"] = flags["core_tick_entry"] and rng.random() < 0.5
    flags["timer0_irq_entry_banks"] = flags["timer0_irq_entry"] and rng.random() < 0.5
    flags["audio_irq_entry_banks"] = flags["audio_irq_entry"] and rng.random() < 0.5
    flags["src_sync_irq_entry_banks"] = flags["src_sync_irq_entry"] and rng.random() < 0.5

    m = new_machine(**flags)

    cfg = [None] * 32
    if rng.random() < 0.8:
        cfg[0] = nibble_word(rng.choice([3, 5, 0, 4]))
    if rng.random() < 0.8:
        cfg[8] = nibble_word(rng.choice([3, 5, 0, 4]))

    sw_pending = ((rng.randint(0, 15), 15), (rng.randint(0, 15), 15))
    deliveries = {}
    if rng.random() < 0.3:
        deliveries["software_irq_deliveries"] = [{"core": rng.randint(0, 1)}]
    if rng.random() < 0.3:
        deliveries["core_tick_deliveries"] = [{"core": rng.randint(0, 1)}]
    if rng.random() < 0.3:
        deliveries["timer0_irq_deliveries"] = [
            {"core": rng.randint(0, 1), "handler": hex(rng.choice([TIMER0_HANDLER, 0xBAD]))}]
    if rng.random() < 0.3:
        deliveries["audio_irq_deliveries"] = [{"core": rng.randint(0, 1)}]
    if rng.random() < 0.3:
        deliveries["src_sync_irq_deliveries"] = [{"core": rng.randint(0, 1)}]

    configure(m, cores, idle, slots=slots, sw_pending=sw_pending,
              sw_ack=rng.randint(0, 3), irq_config=cfg, deliveries=deliveries)
    return m


def random_cpu(rng, core_id):
    pc = rng.choice([0x100, 0x200, 0x12b56c, SW_IRQ_RTI_TAILS[0], SW_IRQ_RTI_TAILS[1],
                     CORE_TICK_RTI_TAIL, TIMER0_RTI_TAIL, AUDIO_IRQ_RTI_TAIL, SRC_SYNC_RTI_TAIL])
    ctx = rng.choice([None] + list(IRQ_HANDLER_CONTEXTS))
    usp = rng.choice([None, 0x1000, 0x1800])
    reti = rng.choice([None, 0x100, 0x200, 0x300])
    ssp = rng.choice([None, 0x2000])
    return fc(core_id, pc=pc, steps=rng.randint(0, 50), sp=rng.choice([0x1000, 0x2000]),
              sp_known=rng.random() < 0.8, usp=usp, ssp=ssp, reti=reti,
              flags=rng.randint(0, 15), irq_psr=rng.choice([None, 0, 1, 2]),
              active_context=ctx, bootstrap_context=rng.random() < 0.2,
              after_then=None if rng.random() < 0.8 else [2, 0],
              repeat=None if rng.random() < 0.8 else {"remaining": 3},
              last_explicit_ssp=rng.choice([None, 0x2000, 0x3000]),
              audio_irq_outer_context=(None if rng.random() < 0.7 else
                                       {"active_context": "timer0_irq_handler",
                                        "reti": 0x200, "sp": 0x2000}))


def main():
    cases = targeted()
    for i, (label, m, op, core_id, irq) in enumerate(cases):
        run_one(m, f"targeted-{i}-{label}", op, core_id, irq)

    rng = random.Random(0x6D6C)
    ops = list(OP.keys())
    for i in range(3000):
        m = random_scenario(rng)
        present = sorted(m.cores.keys())
        op = rng.choice(ops)
        if op in CPU_OBJECT_OPS:
            core_id = rng.choice(present)
        else:
            core_id = rng.randint(0, 1)  # may be an absent core (require guard)
        irq = 0
        if op == "software_irq":
            irq = rng.choice([125, 126, 127])
        run_one(m, f"rand-{i}-{op}", op, core_id, irq)

    print(f"deliver projections: PASS (Go matches DualCoreMachine.deliver_*/return_* "
          f"branch-for-branch over {len(cases) + 3000} scenarios)")


if __name__ == "__main__":
    main()
