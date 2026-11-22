#!/usr/bin/env python3
"""Differential test: the Go whole-loop advance chain (periph_state.go
PeriphState.Advance) vs the real Python BR28Model.advance, byte-for-byte.

This is the "advance state transfer" parity for the full M3-M5 model set
(Timer0 / IRQController / CoreTickStopped / WatchdogProjection /
IcuInitializationProjection / Timer2Delay / SoftwareIrqCommands /
JLSrcSyncDelivery / AudioAdcDmaDelivery): the live Python peripheral state is
moved into a Go PeriphState as JSON, one advance step runs on both engines, and
the outcome (Blocked details / IrqDeliveryRequest(core,irq) / clean) plus the
full resulting state are compared value-for-value. Any field left out of the
transfer drifts the byte output and is caught here.

Follows test_timer0_irq_consistency.py / test_m4_consistency.py /
test_m5_consistency.py, but exercises the advance *orchestrator*
(BR28Model.advance) rather than a single leaf model. The report-only
bookkeeping (watchdog command_values, ICU region write records, ADC-DMA
store/block records, SRC-SYNC cursor) is NOT advance-relevant and is
deliberately absent from both sides of the comparison.

Usage:
    cd tools/gosim && go build -buildmode=c-shared -o libgosim.so ./cshared
    python3 tools/gosim/test_advance_consistency.py
"""
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tools" / "gosim"))

from probe_boot import Blocked, IrqDeliveryRequest  # noqa: E402
from br28_model import BR28Model, CRC_ENGINE, ICU_BASES, TIMER2, WDT_CON, WDT_KEY  # noqa: E402
from gosim_world import GosimPeriph  # noqa: E402


# ---- state extraction (mirrors periph_state.go JSON keys) ----------------------

def timer_state(t):
    return {
        "allowed_sources": list(t.allowed_sources),
        "con": t.con, "cnt": t.cnt, "prd": t.prd,
        "pending": t.pending,
        "remainder": t.remainder,
        "source_remainder": t.source_remainder,
        "expirations": t.expirations,
        "delivery_in_flight": t.delivery_in_flight,
    }


def irq_state(ic):
    return {
        "config": list(ic.config),
        "ipmask": ic.ipmask,
        "unmaskable_irq_projection": ic.unmaskable_irq_projection,
    }


def coretick_state(t):
    return {
        "con": t.con, "cnt": t.cnt, "prd": t.prd, "pending": t.pending,
        "writes": t.writes, "progress": t.progress,
        "expirations": t.expirations,
        "unresolved_overflow": t.unresolved_overflow,
        "acknowledgements": t.acknowledgements,
        "delivery_in_flight": t.delivery_in_flight,
        "prd_write": t.prd_write,
        "prd_readback": t.prd_readback,
        "cnt_held_snapshot": t.cnt_held_snapshot,
        "freerun_edge_period": t.freerun_edge_period,
        "cnt_snapshot_probe_value": t.cnt_snapshot_probe_value,
    }


def watchdog_state(w):
    return {
        "progress_num": w.progress[0] if w.progress is not None else None,
        "progress_den": w.progress[1] if w.progress is not None else 0,
        "con": w.con, "key_phase": w.key_phase, "elapsed": w.elapsed,
        "remainder": w.remainder, "commands": w.commands, "feeds": w.feeds,
        "expired": w.expired, "mailbox_feeds": w.mailbox_feeds,
    }


def icu_state(icu):
    return {
        "control0": icu.control[ICU_BASES[0]],
        "control1": icu.control[ICU_BASES[1]],
        "command_steps": icu.command_steps,
        "pending": [icu.pending.get(b) for b in ICU_BASES],
        "completed": icu.completed,
        "busy_reads": icu.busy_reads,
    }


def timer2_state(t):
    return {
        "con": t.con, "cnt": t.cnt, "prd": t.prd, "pending": t.pending,
        "lrc_remainder": t.lrc_remainder, "remainder": t.remainder,
        "expirations": t.expirations, "writes": t.writes,
        "con_reads": t.con_reads, "advance_calls": t.advance_calls,
    }


def partial_word_state(w):
    return {"value": w.value, "known": w.known}


def software_irq_state(s):
    return {
        "pending": [partial_word_state(s.pending[0]), partial_word_state(s.pending[1])],
        "source_enable": [partial_word_state(s.source_enable[0]),
                          partial_word_state(s.source_enable[1])],
        "commands": s.commands,
        "acknowledgements": s.acknowledgements,
        "source_commands": s.source_commands,
    }


def src_sync_state(d):
    return {
        "completion_steps": d.completion_steps,
        "started": d.started, "start_step": d.start_step,
        "pending": d.pending, "delivery_in_flight": d.delivery_in_flight,
        "starts": d.starts, "completions": d.completions, "acks": d.acks,
    }


def audio_adc_dma_state(a):
    return {
        "delivery": a.delivery, "pending": a.pending,
        "delivery_in_flight": a.delivery_in_flight,
    }


def crc_state(e):
    return {"crc": e.crc}


def py_state(hw):
    core_icfg = [hw.core_icfg.get(0), hw.core_icfg.get(1)]
    core_ticks = [coretick_state(t) for t in hw.core_ticks] if hw.core_ticks else [None, None]
    return {
        "source_ticks_per_step": hw.source_ticks_per_step,
        "timer_lsb_ticks_per_step": hw.timer_lsb_ticks_per_step,
        "multicore_enabled": hw.multicore_enabled,
        "core_icfg": core_icfg,
        "cpu1_control": hw.cpu1_control,
        "cpu1_held_projection": hw.cpu1_held_projection,
        "core_tick_count_while_held": hw.core_tick_count_while_held,
        "core_tick_delivery_requests": hw.core_tick_delivery_requests,
        "timer0_irq_delivery_requests": hw.timer0_irq_delivery_requests,
        "software_irq_delivery_requests": hw.software_irq_delivery_requests,
        "src_sync_delivery_requests": hw.jl_src_sync_delivery_requests,
        "audio_adc_dma_delivery_requests": hw.audio_adc_dma_delivery_requests,
        "completed_model_steps": hw.counts["completed_model_steps"],
        "timer": timer_state(hw.timer),
        "irq": irq_state(hw.irq),
        "irq1": irq_state(hw.irq1) if hw.irq1 is not None else None,
        "core_ticks": core_ticks,
        "watchdog": watchdog_state(hw.watchdog) if hw.watchdog is not None else None,
        "icu": icu_state(hw.icu) if hw.icu is not None else None,
        "timer2": timer2_state(hw.timer2) if hw.timer2 is not None else None,
        "software_irq": software_irq_state(hw.software_irq) if hw.software_irq is not None else None,
        "src_sync": src_sync_state(hw.jl_src_sync_delivery) if hw.jl_src_sync_delivery is not None else None,
        "audio_adc_dma": audio_adc_dma_state(hw.jl_audio_adc_dma) if hw.jl_audio_adc_dma is not None else None,
        "crc": crc_state(hw.crc_engine) if hw.crc_engine is not None else None,
    }


# ---- outcome helpers ----------------------------------------------------------

def py_advance(hw, icfg):
    try:
        hw.advance(icfg)
        return ("clean", None)
    except Blocked as e:
        return ("blocked", e.details)
    except IrqDeliveryRequest as e:
        return ("irq", e.details)
    except ValueError:
        return ("valueerror", None)


def go_outcome_to_py(go_out):
    if go_out["err"] is not None:
        return ("blocked", go_out["err"])
    if go_out["irq"] is not None:
        return ("irq", {"core": go_out["irq"]["core"], "irq": go_out["irq"]["irq"]})
    return ("clean", None)


def _check(label, i, go, py, what):
    if go != py:
        raise AssertionError(
            f"[{label}] #{i} {what} mismatch:\n  go={go}\n  py={py}")


# ---- Python-only state mutation (build reachable live state) ------------------

def _drive_timer(hw, rng):
    sub = rng.random()
    try:
        if sub < 0.4:
            off = rng.choice([8, 4, 0])
            val = rng.choice([9, 0, 0x1001, 0x1401, 0x1000, 0x1001 | 0x4000])
            hw.timer.write(off, val)
        elif sub < 0.7:
            hw.timer.advance(rng.choice([4, 1, 7, 12]))
        else:
            hw.advance(rng.choice([0x300, 0x200, 0x000]))
    except (Blocked, ValueError, IrqDeliveryRequest):
        pass


def _drive_core_tick(hw, rng):
    ct = hw.core_ticks[0]
    sub = rng.random()
    try:
        if sub < 0.45:
            ct.write(rng.choice([8, 4, 0]), rng.choice([9, 0, 1, 3, 0x41]))
        elif sub < 0.8:
            ct.advance()
        else:
            ct.ack_rmw("or", 0x40)
    except (Blocked, ValueError):
        pass


def _drive_watchdog(hw, rng):
    w = hw.watchdog
    sub = rng.random()
    try:
        if sub < 0.35:
            w.write(rng.choice([WDT_KEY, WDT_KEY, WDT_CON]),
                    rng.choice([0x048c159d, 0x26ae37bf, 0x31, 0x10031, 0x0]))
        elif sub < 0.6:
            w.advance()
        else:
            w.mailbox_feed()
    except (Blocked, ValueError):
        pass


def _drive_icu(hw, rng):
    icu = hw.icu
    sub = rng.random()
    try:
        if sub < 0.5:
            icu.write(0xffe410, 0xca000010)
        elif sub < 0.7:
            icu.write(0xffe400, rng.choice([0x1, 0x2]))
        else:
            icu.read(0xffe400)
    except Blocked:
        pass


def _drive_timer2(hw, rng):
    t2 = hw.timer2
    sub = rng.random()
    try:
        if sub < 0.4:
            t2.write(rng.choice([TIMER2, TIMER2 + 4, TIMER2 + 8]),
                     rng.choice([0xc01, 0x0, 0x1, 200, 9]))
        elif sub < 0.7:
            t2.advance(4)
        else:
            t2.read(TIMER2)
    except Blocked:
        pass


def _drive_software_irq(hw, rng):
    sw = hw.software_irq
    sub = rng.random()
    try:
        if sub < 0.4:
            sw.command(0xffe1a0, rng.choice([1, 2, 4, 8]))
        elif sub < 0.6:
            sw.command(0xffe1a4, rng.choice([1, 2, 4, 8]))
        elif sub < 0.8:
            sw.source_rmw(0xffe200, rng.choice(["or", "and"]), rng.choice([8, 0xff, 0]))
        else:
            sw.acknowledge(0, rng.choice([0, 1, 2, 3]))
    except Blocked:
        pass


def _drive_src_sync(hw, rng):
    d = hw.jl_src_sync_delivery
    sub = rng.random()
    if sub < 0.4:
        d.note_start(hw)
    elif sub < 0.6:
        d.note_con_write(rng.choice([0x40, 0x8, 0x48, 0x0]))


def _drive_audio_adc_dma(hw, rng):
    a = hw.jl_audio_adc_dma
    sub = rng.random()
    if sub < 0.4:
        a.pending = True
    elif sub < 0.7:
        a.note_con_write(0x40)  # W1C clears pending + delivery_in_flight


def _drive_crc(hw, rng):
    e = hw.crc_engine
    sub = rng.random()
    if sub < 0.35:
        e.write(CRC_ENGINE + 4, rng.randrange(0x10000))
    elif sub < 0.9 and e.crc is not None:
        e.write(CRC_ENGINE, rng.randrange(0x100))
    elif e.crc is not None:
        e.read(CRC_ENGINE + 4)


def mutate_py(hw, rng):
    """Drive every present model through real ops so the live state carries
    fractional remainders / expirations / delivery_in_flight / pending, then let
    the transfer parity catch any missed field."""
    if hw.core_ticks is not None and rng.random() < 0.5:
        _drive_core_tick(hw, rng)
    else:
        _drive_timer(hw, rng)
    if hw.watchdog is not None:
        _drive_watchdog(hw, rng)
    if hw.icu is not None:
        _drive_icu(hw, rng)
    if hw.timer2 is not None:
        _drive_timer2(hw, rng)
    if hw.software_irq is not None:
        _drive_software_irq(hw, rng)
    if hw.jl_src_sync_delivery is not None:
        _drive_src_sync(hw, rng)
    if hw.jl_audio_adc_dma is not None:
        _drive_audio_adc_dma(hw, rng)
    if hw.crc_engine is not None:
        _drive_crc(hw, rng)


# ---- configs ------------------------------------------------------------------

def _cfg_word(index, nibble, prio=2):
    """One ICFG config word with nibble `nibble` = enabled, priority `prio`."""
    cfg = [None] * 32
    cfg[index] = (0x1 | (prio << 1)) << (nibble * 4)
    return cfg


def build_configs():
    # ICFG word 0: nibble 3 = irq3 enabled prio2, nibble 4 = irq4 enabled prio2.
    cfg = [None] * 32
    cfg[0] = 0x55000
    base = dict(initial_irq_config=cfg, initial_ipmask=2)
    configs = [
        dict(label="timer-counting", kwargs=dict(base)),
        dict(label="timer-delivery", kwargs=dict(base, timer0_irq_delivery_requests=True)),
        dict(label="timer-std12m", kwargs=dict(base, timer0_std12m_source=True)),
        dict(label="timer-lsb", kwargs=dict(base, timer_lsb_ticks_per_step=4)),
        dict(label="coretick", kwargs=dict(base, core_tick_configuration=True,
                                           initial_core_tick_controls=(0, 0),
                                           core_tick_progress=(4, 4))),
        dict(label="coretick-delivery", kwargs=dict(base, core_tick_configuration=True,
                                                    initial_core_tick_controls=(0, 0),
                                                    core_tick_progress=(4, 4),
                                                    core_tick_delivery_requests=True)),
        dict(label="timer+coretick", kwargs=dict(base, core_tick_configuration=True,
                                                 initial_core_tick_controls=(0, 0),
                                                 core_tick_progress=(4, 4),
                                                 timer0_irq_delivery_requests=True,
                                                 core_tick_delivery_requests=True)),
        dict(label="watchdog", kwargs=dict(base, watchdog_projection=True,
                                           watchdog_progress=(4, 1))),
        dict(label="icu", kwargs=dict(base, icu_initialization_projection=True,
                                      icu_command_steps=64,
                                      cache_status_words={0xffe400: 0x0})),
        dict(label="timer2", kwargs=dict(base, timer2_delay_projection=True)),
        dict(label="software-irq", kwargs=dict(base, software_irq_command_projection=True,
                                               initial_software_irq_pending=(1, 0),
                                               software_irq_delivery_requests=True,
                                               initial_irq_config=_cfg_word(15, 4))),
        dict(label="src-sync", kwargs=dict(base, jl_src_sync_config_words=True,
                                           jl_src_sync_full_block=True,
                                           jl_src_sync_delivery=True,
                                           jl_src_sync_delivery_requests=True,
                                           jl_src_sync_completion_steps=10,
                                           initial_irq_config=_cfg_word(11, 1))),
        dict(label="adc-dma", kwargs=dict(base, jl_wl_aud_write_records=True,
                                          jl_audio_adc_dma_writes=True,
                                          jl_audio_adc_reset_write_records=True,
                                          audio_adc_dma_delivery=True,
                                          audio_adc_dma_delivery_requests=True,
                                          initial_irq_config=_cfg_word(8, 4))),
        dict(label="crc", kwargs=dict(base, hardware_crc_projection=True)),
        dict(label="all-models", kwargs=dict(
            initial_ipmask=2,
            initial_irq_config=_all_cfg(),
            core_tick_configuration=True, initial_core_tick_controls=(0, 0),
            core_tick_progress=(4, 4), core_tick_delivery_requests=True,
            timer0_irq_delivery_requests=True,
            watchdog_projection=True, watchdog_progress=(4, 1),
            icu_initialization_projection=True, icu_command_steps=64,
            cache_status_words={0xffe400: 0x0},
            timer2_delay_projection=True,
            software_irq_command_projection=True,
            initial_software_irq_pending=(1, 0),
            software_irq_delivery_requests=True,
            jl_wl_aud_write_records=True, jl_audio_adc_dma_writes=True,
            jl_audio_adc_reset_write_records=True,
            audio_adc_dma_delivery=True, audio_adc_dma_delivery_requests=True,
            jl_src_sync_config_words=True, jl_src_sync_full_block=True,
            jl_src_sync_delivery=True, jl_src_sync_delivery_requests=True,
            jl_src_sync_completion_steps=10,
            hardware_crc_projection=True)),
    ]
    return configs


def _all_cfg():
    cfg = [None] * 32
    cfg[0] = 0x55000    # irq3 nibble 3, irq4 nibble 4
    cfg[8] = 0x5 << 16  # irq68 nibble 4
    cfg[11] = 0x5 << 4  # irq89 nibble 1
    cfg[15] = 0x5 << 16  # irq124 nibble 4
    return cfg


def seed_model(hw):
    """Reach a valid live shape for each present model before the first
    transfer, mirroring the timer seed the M3/M4 harness already does."""
    try:
        hw.timer.write(8, 9)
        hw.timer.write(4, 0)
        hw.timer.write(0, 0x1001)
    except Blocked:
        pass
    if hw.watchdog is not None:
        try:
            hw.watchdog.write(WDT_KEY, 0x048c159d)
            hw.watchdog.write(WDT_KEY, 0x26ae37bf)
            hw.watchdog.write(WDT_CON, 0x31)  # enable + period 17 (slow, no expiry)
        except (Blocked, ValueError):
            pass
    if hw.icu is not None:
        try:
            hw.icu.write(0xffe410, 0xca000010)  # command port base0 + 16
        except Blocked:
            pass
    if hw.timer2 is not None:
        try:
            hw.timer2.write(TIMER2 + 8, 200)
            hw.timer2.write(TIMER2 + 4, 1)
            hw.timer2.write(TIMER2, 0xc01)  # mode 1, src 3 (LRC), div 0
        except Blocked:
            pass
    if hw.jl_src_sync_delivery is not None:
        hw.jl_src_sync_delivery.note_start(hw)


def run_config(hw, go, label, rng):
    icfgs = [0x300, 0x300, 0x200, 0x100, 0x000, 0x3ff, 0x2ff]
    seed_model(hw)
    for rnd in range(12):
        for _ in range(rng.randrange(1, 6)):
            mutate_py(hw, rng)
        go.load(py_state(hw))
        _check(label, f"transfer-{rnd}", go.state(), py_state(hw), "state")
        for _ in range(rng.randrange(1, 9)):
            icfg = rng.choice(icfgs)
            py = py_advance(hw, icfg)
            go_out = go.advance(icfg)
            _check(label, rnd, go_outcome_to_py(go_out), py, "outcome")
            _check(label, rnd, go.state(), py_state(hw), "state")


def test_advance():
    for cfg in build_configs():
        label = cfg["label"]
        hw = BR28Model(**cfg["kwargs"])
        go = GosimPeriph()
        rng = random.Random(0xAD ^ sum(ord(c) for c in label))
        run_config(hw, go, label, rng)
        go.free()
        print(f"advance [{label}]: PASS (Go PeriphState.Advance matches BR28Model.advance byte-for-byte)")


if __name__ == "__main__":
    test_advance()
    sys.exit(0)
