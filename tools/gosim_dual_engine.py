"""Dual-core whole-loop engine: run select -> execute -> after_step-judgment ->
advance inside Go for the safe-ALU stretches of a dual-core crc_bankb run, and
hand every boundary back to Python.

This is the "fifth wiring": the Go World.RunLoop (tools/gosim/world.go) becomes
the actual executor for the dual-core phase, not just the single-core segment
batch. The engine snapshots the live Python machine / hardware / CPU state into
the Go world, runs one RunLoop call, restores the mutated state back, and hands
the committed step observations to the explore loop for trace materialization.

Boundaries that still hand back to Python (honest, never fabricated):
  * idle instruction   -> enter_idle_wait / event clock (br28_machine)
  * unsafe instruction (memory / special-reg / parallel / repeat) -> c.step()
  * fetch outside the immutable flash window (RAM code / ROM) -> c.step()
  * after_step hook fire -> machine.after_step (hook bodies stay Python)
  * pending-eligible IRQ -> dispatch_delivery (delivery projections stay Python)
  * advance strict stop (Blocked) -> the explore stop channel

Byte-parity is the differential witness (test_dualcore_consistency.py and the
gosim_measure --self-check-batch harness); nothing here invents a value.
"""
import json
import os
import struct
import sys
import time
from pathlib import Path

# 分项计时(H3S_DUAL_TIMING=1 时开启):把一次 fill 的各个环节拆开累计 µs。
# 键形如 'pack_ram'/'go_map'/...,'n:<key>' 记对应环节的执行次数,便于换算
# µs/fill。关闭时 _t 是空操作,零开销(不改变行为与字节产物)。
TIMING = None
if os.environ.get('H3S_DUAL_TIMING'):
    TIMING = {}


def _t(key, dt):
    if TIMING is not None:
        TIMING[key] = TIMING.get(key, 0.0) + dt
        TIMING['n:' + key] = TIMING.get('n:' + key, 0) + 1


def timing_report():
    """分项计时汇总:{环节: (总 µs, 次数, µs/次)}。TIMING=None 时返回 {}。"""
    if TIMING is None:
        return {}
    out = {}
    for k, v in TIMING.items():
        if k.startswith('n:'):
            continue
        if k in ('dirty_sum', 'dirty_max'):
            out[k] = (v, TIMING.get('n:dirty', 0), 0.0)
            continue
        n = TIMING.get('n:' + k, 0)
        out[k] = (v * 1e6, n, v * 1e6 / n if n else 0.0)
    return out


_GOSIM = Path(__file__).resolve().parent / 'gosim'
if str(_GOSIM) not in sys.path:
    sys.path.insert(0, str(_GOSIM))

import gosim as _glue            # noqa: E402
import gosim_mem as _smem        # noqa: E402
import gosim_world as _world     # noqa: E402
from gosim_world import GosimDualWorld, GosimPeriph  # noqa: E402
import gosim_m6 as _m6           # noqa: E402

from br28_unknown import UnknownWord  # noqa: E402
from br28_icfg import IcfgValue       # noqa: E402

# ICU register bases mirrored from test_advance_consistency.py (the differential
# witness for the whole advance-chain state round-trip).
ICU_BASES = (0xFFE400, 0xFFEC00)


def _opt_int(v):
    """None for anything that is not a concrete int (mirrors the Go nil u32)."""
    return v if type(v) is int else None


def cores_concrete(c0, c1):
    """Both cores have strictly-concrete pc / icfg / R (no UnknownWord/IcfgValue/
    bool). This is the Python-side twin of the Go World.Concrete precondition:
    the dual engine only runs when every register round-trips as an exact int."""
    def ok(c):
        return (c is not None
                and type(c.pc) is int
                and type(getattr(c, 'icfg', 0)) is int
                and all(type(v) is int for v in c.R))
    # c1 None = 单核阶段(CPU1 未 release):只要求 CPU0 具体。
    if c1 is None:
        return ok(c0)
    return ok(c0) and ok(c1)


def cores_head_ok(c0, c1):
    """Both cores can enter the Go dual loop with abstract register tags: pc and
    icfg must be concrete (select needs a decodable pc), every R slot must be
    either a concrete int or an UnknownWord/IcfgValue the bridge round-trips
    losslessly, and any in-flight repeat must be one the Go loop owns: the
    immediate rep.i single-store projection (register None, exactly the four
    start/end/register/remaining keys) or the register-count startup / ROM
    memset gates (the bounded copy/clear loops and the memset single-store
    bodies, register 2/4 at their pinned start/end addresses). Every other
    repeat form (multi-block / allow_helper_return / other register-count
    shapes) stays Python. An open if-block (after_then) is now carried by the
    Go loop (the if.* family is migrated), so it no longer refuses the entry.
    Anything else (GpioOutValue, bool, ...) refuses loudly. Go is still the
    execution authority — it hands a step back the moment an actual numeric
    input is tagged, so the per-step gate lives in RunDualStepCore."""
    def _owned_register_repeat(rp):
        if set(rp.keys()) != {'start', 'end', 'register', 'remaining'}:
            return False
        reg = rp.get('register')
        start = rp.get('start')
        end = rp.get('end')
        if reg is None:
            return False
        # Startup bounded rep: register 2, single-store (0x06000132) or
        # ld.w.pin + st.w.pin copy body (0x0600014c / 0x06000168 / 0x06000184).
        if reg == 2 and start in (0x06000132, 0x0600014c, 0x06000168, 0x06000184):
            return end in (start + 2, start + 4)
        # ROM memset: register 4 (0x4242, st.w.pin body) / register 2
        # (0x424c, st.b.pin body), both single-store 2-byte bodies.
        if reg == 4 and start == 0x4244 and end == 0x4246:
            return True
        if reg == 2 and start == 0x424e and end == 0x4250:
            return True
        return False

    def ok(c):
        rp = getattr(c, 'repeat', None)
        repeat_ok = (rp is None or
                     (set(rp.keys()) == {'start', 'end', 'register', 'remaining'} and
                      (rp.get('register') is None or _owned_register_repeat(rp))))
        return (c is not None
                and type(c.pc) is int
                and type(getattr(c, 'icfg', 0)) is int
                and repeat_ok
                and all(type(v) is int or isinstance(v, (UnknownWord, IcfgValue))
                        for v in c.R))
    # c1 None = 单核阶段(CPU1 未 release):只要求 CPU0 可进 Go 整圈。
    if c1 is None:
        return ok(c0)
    return ok(c0) and ok(c1)


def encode_reg_tag(v):
    """One GPR slot -> the Go RegTag shape, or None for a concrete int.

    Abstract values round-trip losslessly through the side table: UnknownWord
    carries its 4 bytes + 4 origins, IcfgValue its value/known/origin/identity
    (the world_snapshot._ABSTRACT_FIELDS shapes). Anything else refuses loudly
    (the bridge never fabricates a value)."""
    if type(v) is int:
        return None
    if isinstance(v, UnknownWord):
        return {'kind': 'unknown_word',
                'bytes': list(v.bytes), 'origins': list(v.origins)}
    if isinstance(v, IcfgValue):
        return {'kind': 'icfg_value',
                'value': v.value, 'known': v.known,
                'origin': list(v.origin), 'identity': v.identity}
    raise ValueError('dual engine snapshots only concrete or UnknownWord/IcfgValue '
                     'GPRs (never fabricate a value)')


def decode_reg_tag(tag):
    """Go RegTag shape -> the live Python abstract value (None = concrete int)."""
    if tag is None:
        return None
    kind = tag['kind']
    if kind == 'unknown_word':
        return UnknownWord(tuple(tag['bytes']), tuple(tag['origins']))
    if kind == 'icfg_value':
        return IcfgValue(tag['value'], tag['known'], tuple(tag['origin']),
                         tag.get('identity', 0xffffffff))
    raise ValueError('unknown register tag kind: ' + repr(kind))


def _icfg_bits(c):
    """(value, known_mask, fully_known) of one CPU's ICFG, partial-icfg aware."""
    st = getattr(c, 'icfg_state', None)
    if st is not None:
        return st.value, st.known, st.known == 0xffffffff
    return (c.icfg if c.icfg_known else 0), (0xffffffff if c.icfg_known else 0), \
        c.icfg_known


def _icfg_extra(c):
    """(status_generation, writes, bit9_updates) of one CPU's ICFG state."""
    st = getattr(c, 'icfg_state', None)
    if st is not None:
        return st.status_generation, st.writes, st.bit9_updates
    return 0, 0, 0


# The three select-side literal anchors (mirror gosim/machine.go).
_TIMER2_POLL_ANCHOR = 0x12CD76
_CORE1_IDLE_PC = 0x12C5CA
_CORE1_IDLE_DELAY_LIMIT = 4096


def predict_select(machine, hardware):
    """Side-effect-free mirror of DualCoreMachine.select(): the core the real
    select would pick, its CPU (or None), and that core's effective pc AFTER any
    wake that applies to it (CPU1-control kick / TIMER2 anchored poll).

    The real select commits side effects (idle_wait removal, pc += idle size,
    steps += 1, event records, last_core / deferred turns, core_icfg demand)
    exactly once per attempt inside Go. This predictor only REPLIES which core
    and pc the head check should observe, so a call here never double-commits a
    wake and a wrong prediction (this is a pure optimization) can only waste or
    skip a fill, never change the selected core's behavior."""
    kicks = getattr(hardware, 'cpu1_control_kick_writes', None)
    idle = set(machine.idle_wait_cores)
    cores = machine.cores
    pc = {cid: c.pc for cid, c in cores.items()}
    decoded = {}
    for cid, c in cores.items():
        try:
            insn, _, size = c.decode(c.pc)
            decoded[cid] = (insn.id, size)
        except Exception:
            decoded[cid] = (None, 0)

    if machine.cpu1_control_kick_wake and kicks is not None and kicks != machine.seen_cpu1_control_kicks:
        if 1 in cores and 1 in idle and decoded.get(1, (None, 0))[0] == 'idle':
            idle.discard(1)
            pc[1] += decoded[1][1]

    timer2 = getattr(hardware, 'timer2', None)
    if machine.timer2_delay_projection and timer2 is not None and timer2.pending:
        for core in sorted(cid for cid in idle
                           if cid in cores and pc[cid] == _TIMER2_POLL_ANCHOR):
            if decoded.get(core, (None, 0))[0] == 'idle' and pc[core] == _TIMER2_POLL_ANCHOR:
                idle.discard(core)
                pc[core] += decoded[core][1]

    core = 1 - machine.last_core
    if core not in cores:
        core = 0
    if core in idle:
        other = 1 - core
        if other in cores and other not in idle:
            core = other
    if (core == 1 and machine.defer_core1_first_idle and 1 in machine.first_task_returns
            and pc.get(1) == _CORE1_IDLE_PC
            and machine.deferred_core1_turns < _CORE1_IDLE_DELAY_LIMIT):
        core = 0
    return core, cores.get(core), pc.get(core)


# ---- snapshot: live Python objects -> Go JSON shapes ---------------------------

def core_state(c):
    """One live Python CPU -> the coreStateJSON shape (cshared/world.go)."""
    def regs():
        out = []
        rt = []
        for v in c.R:
            tag = encode_reg_tag(v)
            if tag is None:
                out.append(v)
            else:
                out.append(0)  # placeholder; the side table is authoritative
            rt.append(tag)
        return out, rt

    def outer():
        o = getattr(c, 'audio_irq_outer_context', None)
        if not o:
            return None
        return {'active_context': o.get('active_context') or '',
                'reti': _opt_int(o.get('reti')), 'sp': o.get('sp', 0)}

    r, rt = regs()
    icfg, icfg_mask, icfg_known = _icfg_bits(c)
    icfg_gen, icfg_writes, icfg_bit9 = _icfg_extra(c)
    _repeat = getattr(c, 'repeat', None)
    return {
        'id': c.core_id,
        'r': r,
        'rt': rt,
        'flags': c.flags,
        'sp': c.sp if c.sp_known else 0,
        'sp_known': c.sp_known,
        'rets': c.rets,
        'pc': c.pc,
        'icfg': icfg,
        'icfg_known': icfg_known,
        'icfg_known_mask': icfg_mask,
        'icfg_status_gen': icfg_gen,
        'icfg_writes': icfg_writes,
        'icfg_bit9_updates': icfg_bit9,
        'steps': c.steps,
        'repeat_active': (_repeat is not None
                          or getattr(c, 'after_then', None) is not None),
        # repeat is the structured in-flight repeat state the Go loop owns
        # (immediate rep.i single-store projection; register None -> -1).
        # after_then is the in-flight if-block state the Go loop persists and
        # round-trips (mirrors cpu.after_then == [left, elseN]).
        'repeat': (None if _repeat is None else
                   {'start': _repeat['start'], 'end': _repeat['end'],
                    'register': -1 if _repeat.get('register') is None else _repeat['register'],
                    'remaining': _repeat['remaining']}),
        'after_then': (None if getattr(c, 'after_then', None) is None else
                       {'left': c.after_then[0], 'else': c.after_then[1]}),
        'usp': _opt_int(getattr(c, 'usp', None)),
        'ssp': _opt_int(getattr(c, 'ssp', None)),
        'reti': _opt_int(getattr(c, 'reti', None)),
        'irq_psr': _opt_int(getattr(c, 'irq_psr', None)),
        'active_context': getattr(c, 'active_context', None) or '',
        'bootstrap_context': getattr(c, 'bootstrap_context', False),
        'last_explicit_ssp': _opt_int(getattr(c, 'last_explicit_ssp', None)),
        'audio_irq_outer_context': outer(),
    }


def machine_state(m):
    """Live DualCoreMachine -> the machineInput shape (machine.go)."""
    def core_dict(cid):
        if cid not in m.cores:
            return None
        c = m.cores[cid]
        # A core can sit at a pc Python cannot decode right now (e.g. flash
        # fetch while SFC is disabled); the select twin only needs insn_id/
        # insn_size for the two idle-wake helpers, so an undecodable pc is
        # reported as ''/0 exactly like predict_select and the Go syncMachineCores
        # fallback. The selected core's execution is still guarded by
        # _dual_head_ok, which refuses an undecodable head.
        try:
            insn, _, size = c.decode(c.pc)
            insn_id, insn_size = insn.id, size
        except Exception:
            insn_id, insn_size = '', 0
        icfg, icfg_mask, icfg_known = _icfg_bits(c)
        return {
            'id': c.core_id, 'pc': c.pc, 'steps': c.steps,
            'icfg': icfg, 'icfg_known': icfg_known,
            'icfg_known_mask': icfg_mask,
            'insn_id': insn_id, 'insn_size': insn_size,
        }

    hw = m.hardware
    kicks = getattr(hw, 'cpu1_control_kick_writes', None)
    timer2 = getattr(hw, 'timer2', None)
    core_icfg = hw.core_icfg if isinstance(hw.core_icfg, dict) else {}
    return {
        'cores': [core_dict(0), core_dict(1)],
        'idle_wait': [0 in m.idle_wait_cores, 1 in m.idle_wait_cores],
        'last_core': m.last_core,
        'defer_core1_first_idle': m.defer_core1_first_idle,
        'first_task_returns': [0 in m.first_task_returns, 1 in m.first_task_returns],
        'deferred_core1_turns': m.deferred_core1_turns,
        'cpu1_control_kick_wake': m.cpu1_control_kick_wake,
        'seen_cpu1_control_kicks': m.seen_cpu1_control_kicks,
        'hardware_kicks': kicks,
        'timer2_delay_projection': m.timer2_delay_projection,
        'timer2_pending': bool(timer2 is not None and timer2.pending),
        'idle_wait_projection': m.idle_wait_projection,
        # gosim29 第一刀:events 只回传本批新增项。Python 持有完整历史与累计数,
        # Go 选核只在唤醒时追加事件(调度判断不读旧历史),故每批传空账本、
        # 构造快照时不复制历史——砍掉 events 的双向整份往返。恢复侧由
        # restore_machine 把 Go 返回的新增事件按序喂回 m.record_event。
        'events': [],
        'event_count': 0,
        'timer2_poll_wakes': [dict(w) for w in m.timer2_poll_wakes],
        'current_core': getattr(hw, 'current_core', 0),
        'core_icfg': [_opt_int(core_icfg.get(0)), _opt_int(core_icfg.get(1))],
        'partial_icfg': m.partial_icfg,
        'unknown_task_gprs': m.unknown_task_gprs,
    }


def after_step_state(m):
    """Live machine -> the afterStepConfig shape (after_step.go)."""
    def setup_index(cid):
        setup = m.first_task_setup.get(cid)
        return setup['index'] if setup is not None else -1

    return {
        'cfg523_enabled': m.cfg523_object_bytes is not None,
        'cfg523_installed': m.cfg523_installed_at is not None,
        'first_task_hypothesis': m.first_task_return_hypothesis is not None,
        'observer_active': False,  # d1_window_observer stays Python in this cut
        'first_task_setup_index': [setup_index(0), setup_index(1)],
        'first_task_return_frame': [
            (0 in m.cores and m.cores[0].first_task_return_frame is not None),
            (1 in m.cores and m.cores[1].first_task_return_frame is not None),
        ],
    }


# Per-peripheral state extraction (mirrors test_advance_consistency.py, the
# differential witness for the whole advance-chain round-trip).

def _timer_state(t):
    return {
        'allowed_sources': list(t.allowed_sources),
        'con': t.con, 'cnt': t.cnt, 'prd': t.prd,
        'pending': t.pending, 'remainder': t.remainder,
        'source_remainder': t.source_remainder, 'expirations': t.expirations,
        'delivery_in_flight': t.delivery_in_flight,
    }


def _irq_state(ic):
    return {'config': list(ic.config), 'ipmask': ic.ipmask,
            'unmaskable_irq_projection': ic.unmaskable_irq_projection}


def _coretick_state(t):
    return {
        'con': t.con, 'cnt': t.cnt, 'prd': t.prd, 'pending': t.pending,
        'writes': t.writes, 'progress': t.progress, 'expirations': t.expirations,
        'unresolved_overflow': t.unresolved_overflow,
        'acknowledgements': t.acknowledgements,
        'delivery_in_flight': t.delivery_in_flight,
        'prd_write': t.prd_write, 'prd_readback': t.prd_readback,
        'cnt_held_snapshot': t.cnt_held_snapshot,
        'freerun_edge_period': t.freerun_edge_period,
        'cnt_snapshot_probe_value': t.cnt_snapshot_probe_value,
    }


def _watchdog_state(w):
    return {
        'progress_num': w.progress[0] if w.progress is not None else None,
        'progress_den': w.progress[1] if w.progress is not None else 0,
        'con': w.con, 'key_phase': w.key_phase, 'elapsed': w.elapsed,
        'remainder': w.remainder, 'commands': w.commands, 'feeds': w.feeds,
        'expired': w.expired, 'mailbox_feeds': w.mailbox_feeds,
    }


def _icu_state(icu):
    return {
        'control0': icu.control[ICU_BASES[0]],
        'control1': icu.control[ICU_BASES[1]],
        'command_steps': icu.command_steps,
        'pending': [icu.pending.get(b) for b in ICU_BASES],
        'completed': icu.completed, 'busy_reads': icu.busy_reads,
    }


def _timer2_state(t):
    return {
        'con': t.con, 'cnt': t.cnt, 'prd': t.prd, 'pending': t.pending,
        'lrc_remainder': t.lrc_remainder, 'remainder': t.remainder,
        'expirations': t.expirations, 'writes': t.writes,
        'con_reads': t.con_reads, 'advance_calls': t.advance_calls,
    }


def _partial_word_state(w):
    return {'value': w.value, 'known': w.known}


def _software_irq_state(s):
    return {
        'pending': [_partial_word_state(s.pending[0]), _partial_word_state(s.pending[1])],
        'source_enable': [_partial_word_state(s.source_enable[0]),
                          _partial_word_state(s.source_enable[1])],
        'commands': s.commands, 'acknowledgements': s.acknowledgements,
        'source_commands': s.source_commands,
    }


def _src_sync_state(d):
    return {
        'completion_steps': d.completion_steps, 'started': d.started,
        'start_step': d.start_step, 'pending': d.pending,
        'delivery_in_flight': d.delivery_in_flight,
        'starts': d.starts, 'completions': d.completions, 'acks': d.acks,
    }


def _audio_adc_dma_state(a):
    return {
        'delivery': a.delivery, 'pending': a.pending,
        'delivery_in_flight': a.delivery_in_flight,
    }


def _crc_state(e):
    # The Go side only needs the accumulator value (to keep feeding inside one
    # segment). seed_writes / fed_bytes / result_reads and the general write
    # ledger stay Python-side: the replay of each committed feed/seed through
    # c.mem.write re-derives them exactly once, so a feed is applied once per
    # side (never twice to one accumulator) and the write ledger stays intact.
    return {'crc': e.crc}


def periph_state(hw):
    core_icfg = [hw.core_icfg.get(0), hw.core_icfg.get(1)]
    core_ticks = ([_coretick_state(t) for t in hw.core_ticks]
                  if hw.core_ticks else [None, None])
    return {
        'source_ticks_per_step': hw.source_ticks_per_step,
        'timer_lsb_ticks_per_step': hw.timer_lsb_ticks_per_step,
        'multicore_enabled': hw.multicore_enabled,
        'core_icfg': core_icfg,
        'cpu1_control': hw.cpu1_control,
        'cpu1_held_projection': hw.cpu1_held_projection,
        'core_tick_count_while_held': hw.core_tick_count_while_held,
        'core_tick_delivery_requests': hw.core_tick_delivery_requests,
        'timer0_irq_delivery_requests': hw.timer0_irq_delivery_requests,
        'software_irq_delivery_requests': hw.software_irq_delivery_requests,
        'src_sync_delivery_requests': hw.jl_src_sync_delivery_requests,
        'audio_adc_dma_delivery_requests': hw.audio_adc_dma_delivery_requests,
        'completed_model_steps': hw.counts['completed_model_steps'],
        'timer': _timer_state(hw.timer),
        'irq': _irq_state(hw.irq),
        'irq1': _irq_state(hw.irq1) if hw.irq1 is not None else None,
        'core_ticks': core_ticks,
        'watchdog': _watchdog_state(hw.watchdog) if hw.watchdog is not None else None,
        'icu': _icu_state(hw.icu) if hw.icu is not None else None,
        'timer2': _timer2_state(hw.timer2) if hw.timer2 is not None else None,
        'software_irq': _software_irq_state(hw.software_irq) if hw.software_irq is not None else None,
        'src_sync': _src_sync_state(hw.jl_src_sync_delivery) if hw.jl_src_sync_delivery is not None else None,
        'audio_adc_dma': _audio_adc_dma_state(hw.jl_audio_adc_dma) if hw.jl_audio_adc_dma is not None else None,
        'crc': _crc_state(hw.crc_engine) if hw.crc_engine is not None else None,
    }


# ---- restore: Go JSON shapes -> live Python objects ---------------------------

def restore_core(c, gc):
    """Write one Go coreStateJSON back into the live Python CPU.

    R / flags / pc / steps are always written back. sp / rets / icfg are also
    written back now: the ICFG instruction handlers and the abstract task-stack
    transport the Go loop executes mutate them, so the restore must carry their
    post-loop values. The typed register side table ('rt') reconstructs any
    abstract GPR losslessly; when it is absent (legacy concrete shape) each R
    slot stays a concrete int."""
    if gc is None:
        return
    rt = gc.get('rt')
    if rt is None:
        c.R = [v if type(v) is int else 0 for v in gc['r']]
    else:
        r = gc['r']
        c.R = [v if tag is None else decode_reg_tag(tag)
               for v, tag in zip(r, rt)]
    c.flags = gc['flags'] & 0xFFFFFFFF
    c.pc = gc['pc'] & 0xFFFFFFFF
    c.steps = gc['steps']
    c.sp = gc['sp'] & 0xFFFFFFFF
    c.sp_known = bool(gc.get('sp_known', True))
    c.rets = gc['rets'] & 0xFFFFFFFF
    rp = gc.get('repeat')
    if rp is None:
        c.repeat = None
    else:
        c.repeat = {'start': rp['start'] & 0xFFFFFFFF, 'end': rp['end'] & 0xFFFFFFFF,
                    'register': None if rp.get('register', -1) < 0 else rp['register'],
                    'remaining': rp['remaining']}
    at = gc.get('after_then')
    c.after_then = None if at is None else [at['left'], at['else']]
    icfg = gc.get('icfg', 0) & 0xFFFFFFFF
    st = getattr(c, 'icfg_state', None)
    if st is not None:
        st.value = icfg
        st.known = gc.get('icfg_known_mask', 0xffffffff) & 0xFFFFFFFF
        st.status_generation = gc.get('icfg_status_gen', 0)
        st.writes = gc.get('icfg_writes', 0)
        st.bit9_updates = gc.get('icfg_bit9_updates', 0)
    else:
        c.icfg = icfg
        c.icfg_known = bool(gc.get('icfg_known', False))


def restore_machine(m, gs):
    """Write one Go machine StateJSON back into the live DualCoreMachine."""
    m.idle_wait_cores = {i for i, b in enumerate(gs['idle_wait']) if b}
    m.last_core = gs['last_core']
    m.deferred_core1_turns = gs['deferred_core1_turns']
    m.seen_cpu1_control_kicks = gs['seen_cpu1_control_kicks']
    m.timer2_poll_wakes = [dict(w) for w in gs['timer2_poll_wakes']]
    # gosim29 第一刀:Go 每批只回传新增的 select 唤醒事件(输入账本为空)。按序
    # 喂回 m.record_event,Python 沿用自身的「前16+后16」有界窗口并继续累积
    # event_count。返回列表被当作增量使用,必须完整无丢项:现有唤醒结构下每次
    # 唤醒都会把该核移出 idle_wait、重新停泊发生在 Python 侧,故单个 fill 最多
    # 新增 kick+至多两条锚定轮询唤醒(远小于 32),Go 的 recordEvent 不会触发
    # pop。用断言钉住这个不变量,而不是猜。
    new_events = [dict(e) for e in gs['events']]
    if gs['event_count'] != len(new_events):
        raise ValueError(
            'dual-engine select events must round-trip whole (bounded list used '
            'as a per-batch increment): event_count=%d len=%d'
            % (gs['event_count'], len(new_events)))
    for e in new_events:
        m.record_event(e)
    m.hardware.current_core = gs['current_core']
    m.hardware.core_icfg = {i: gs['core_icfg'][i]
                            for i in (0, 1) if gs['core_icfg'][i] is not None}
    # first_task_returns / boot_anchors / deliveries / deferrals are Python-side
    # (after_step hook bodies and delivery projections stay Python in this cut),
    # so they are not restored from Go.


def restore_periph(hw, gs):
    """Write one Go periph StateJSON back into the live BR28Model."""
    hw.source_ticks_per_step = gs['source_ticks_per_step']
    hw.timer_lsb_ticks_per_step = gs['timer_lsb_ticks_per_step']
    hw.multicore_enabled = gs['multicore_enabled']
    hw.cpu1_control = gs['cpu1_control']
    hw.cpu1_held_projection = gs['cpu1_held_projection']
    hw.core_tick_count_while_held = gs['core_tick_count_while_held']
    hw.core_tick_delivery_requests = gs['core_tick_delivery_requests']
    hw.timer0_irq_delivery_requests = gs['timer0_irq_delivery_requests']
    hw.software_irq_delivery_requests = gs['software_irq_delivery_requests']
    hw.jl_src_sync_delivery_requests = gs['src_sync_delivery_requests']
    hw.audio_adc_dma_delivery_requests = gs['audio_adc_dma_delivery_requests']
    hw.counts['completed_model_steps'] = gs['completed_model_steps']
    # core_icfg is restored from the machine state (authoritative select twin).
    _restore_timer(hw.timer, gs['timer'])
    _restore_irq(hw.irq, gs['irq'])
    if hw.irq1 is not None and gs['irq1'] is not None:
        _restore_irq(hw.irq1, gs['irq1'])
    if hw.core_ticks:
        for i, ct in enumerate(hw.core_ticks):
            _restore_coretick(ct, gs['core_ticks'][i])
    if hw.watchdog is not None and gs['watchdog'] is not None:
        _restore_watchdog(hw.watchdog, gs['watchdog'])
    if hw.icu is not None and gs['icu'] is not None:
        _restore_icu(hw.icu, gs['icu'])
    if hw.timer2 is not None and gs['timer2'] is not None:
        _restore_timer2(hw.timer2, gs['timer2'])
    if hw.software_irq is not None and gs['software_irq'] is not None:
        _restore_software_irq(hw.software_irq, gs['software_irq'])
    if hw.jl_src_sync_delivery is not None and gs['src_sync'] is not None:
        _restore_src_sync(hw.jl_src_sync_delivery, gs['src_sync'])
    if hw.jl_audio_adc_dma is not None and gs['audio_adc_dma'] is not None:
        _restore_audio_adc_dma(hw.jl_audio_adc_dma, gs['audio_adc_dma'])
    # crc is intentionally NOT restored back into hw.crc_engine: the committed
    # feed/seed steps are replayed through c.mem.write, which updates the live
    # crc_engine (accumulator + bookkeeping + write ledger) exactly once.
    # Restoring the Go accumulator here would double-apply the feeds.


def _restore_timer(t, s):
    t.allowed_sources = list(s['allowed_sources'])
    t.con = s['con']; t.cnt = s['cnt']; t.prd = s['prd']
    t.pending = s['pending']; t.remainder = s['remainder']
    t.source_remainder = s['source_remainder']; t.expirations = s['expirations']
    t.delivery_in_flight = s['delivery_in_flight']


def _restore_irq(ic, s):
    ic.config = list(s['config']); ic.ipmask = s['ipmask']
    ic.unmaskable_irq_projection = s['unmaskable_irq_projection']


def _restore_coretick(t, s):
    if s is None:
        return
    t.con = s['con']; t.cnt = s['cnt']; t.prd = s['prd']
    t.pending = s['pending']; t.writes = s['writes']; t.progress = s['progress']
    t.expirations = s['expirations']; t.unresolved_overflow = s['unresolved_overflow']
    t.acknowledgements = s['acknowledgements']; t.delivery_in_flight = s['delivery_in_flight']
    t.prd_write = s['prd_write']; t.prd_readback = s['prd_readback']
    t.cnt_held_snapshot = s['cnt_held_snapshot']
    t.freerun_edge_period = s['freerun_edge_period']
    t.cnt_snapshot_probe_value = s['cnt_snapshot_probe_value']


def _restore_watchdog(w, s):
    if s['progress_num'] is None:
        w.progress = None
    else:
        w.progress = (s['progress_num'], s['progress_den'])
    w.con = s['con']; w.key_phase = s['key_phase']; w.elapsed = s['elapsed']
    w.remainder = s['remainder']; w.commands = s['commands']; w.feeds = s['feeds']
    w.expired = s['expired']; w.mailbox_feeds = s['mailbox_feeds']


def _restore_icu(icu, s):
    icu.control[ICU_BASES[0]] = s['control0']
    icu.control[ICU_BASES[1]] = s['control1']
    icu.command_steps = s['command_steps']
    # pending is a dict of only the in-progress bases; a nil slot must remove the
    # key (an explicit None value would crash ICU.advance's `pending[base]-=1`).
    icu.pending.clear()
    for b, v in zip(ICU_BASES, s['pending']):
        if v is not None:
            icu.pending[b] = v
    icu.completed = s['completed']; icu.busy_reads = s['busy_reads']


def _restore_timer2(t, s):
    t.con = s['con']; t.cnt = s['cnt']; t.prd = s['prd']
    t.pending = s['pending']; t.lrc_remainder = s['lrc_remainder']
    t.remainder = s['remainder']; t.expirations = s['expirations']
    t.writes = s['writes']; t.con_reads = s['con_reads']; t.advance_calls = s['advance_calls']


def _restore_software_irq(s, st):
    s.pending[0].value, s.pending[0].known = st['pending'][0]['value'], st['pending'][0]['known']
    s.pending[1].value, s.pending[1].known = st['pending'][1]['value'], st['pending'][1]['known']
    s.source_enable[0].value, s.source_enable[0].known = st['source_enable'][0]['value'], st['source_enable'][0]['known']
    s.source_enable[1].value, s.source_enable[1].known = st['source_enable'][1]['value'], st['source_enable'][1]['known']
    s.commands = st['commands']; s.acknowledgements = st['acknowledgements']
    s.source_commands = st['source_commands']


def _restore_src_sync(d, s):
    d.completion_steps = s['completion_steps']; d.started = s['started']
    d.start_step = s['start_step']; d.pending = s['pending']
    d.delivery_in_flight = s['delivery_in_flight']
    d.starts = s['starts']; d.completions = s['completions']; d.acks = s['acks']


def _restore_audio_adc_dma(a, s):
    a.delivery = s['delivery']; a.pending = s['pending']
    a.delivery_in_flight = s['delivery_in_flight']


# ---- the engine ---------------------------------------------------------------

class DualCoreEngine:
    """One Go dual-core world reused across a single explore run.

    The two execute engines and the World share one strict Memory handle: fetch
    (ROM/app/readonly + physical RAM code), scalar loads and the standalone
    full-word stores run against it. The machine / periph handles are rebound in
    place each run so the Go world stays in sync with the live Python objects.
    """

    def __init__(self, rom, app, entry, regions=(), readonly=(), mem=None,
                 single_start=False):
        self.go0 = _glue.Gosim(arch=True)
        self.go1 = _glue.Gosim(arch=True)
        self.rom = rom
        self.app = app
        self.entry = entry
        self.mem = mem  # the live probe_boot Memory (RAM re-sync source)
        if self.mem is not None and self.mem._gosim_dirty is None:
            self.mem._gosim_dirty = set()  # enable Python-side dirty tracking

        # One shared strict memory: regions / ROM / app / readonly imported in
        # Python's own order, plus the current known RAM bytes. The initial
        # import is the only full bulk ram map; every later re-sync is a delta
        # (import/remove the bytes Python changed). Never a guest Write, so no
        # spurious write record is fabricated.
        self.smem = _smem.StrictMem()
        if rom is not None:
            self.smem.set_rom(rom)
        self.smem.set_app(entry, app)
        for lo, hi, kind in regions:
            self.smem.add_region(lo, hi, kind)
        for base, data in readonly:
            self.smem.add_readonly(base, data)
        self._ram_synced = False
        self._ram_dict = None
        self._sync_ram()

        # Both execute engines and the World bind the one strict memory.
        self.go0.set_data_mem(self.smem.handle)
        self.go1.set_data_mem(self.smem.handle)

        self.machine = _m6.GosimMachine()
        self.periph = GosimPeriph()

        # Fetch window = the whole physical address space; the strict decode is
        # the honest guard (RAM code / ROM now fetchable, unmapped/uninitialized
        # faults hand back to Python which re-derives the identical Blocked).
        self.world = GosimDualWorld(self.go0.handle, self.go1.handle, self.smem.handle,
                                    self.machine.handle, 0, 0, 0xFFFFFFFF,
                                    single_start=single_start)
        self.world.set_periph(self.periph.handle)

        # 第四刀:CPU/machine/periph 跨 fill 常驻。Go World 在 run_loop 之间
        # 保持其 Cores/Machine/PeriphState/AfterStep 不动,Python 侧也不再每批
        # 全量重装:
        #   * _resident=True 表示 Go 已持有与 Python 一致的状态(上一个 clean
        #     批刚跑完、Python 的 dual_buf 消费只是把 Go 的末态镜像回 Python),
        #     下一个相邻 clean 批直接续跑,整段跳过 json_state+load_state。
        #   * _cs/_ms/_ps/_aps 缓存「上一次实际推给 Go 的分片」(diff 基准);
        #     _resident=False 的真实再入按分片对比,只推 Python 慢步真正改过的
        #     核/machine/periph/after_step,不变的分片不再 cgo 往返。
        self._resident = False
        self._cs = [None, None]
        self._ms = None
        self._ps = None
        self._aps = None
        self._digest_debug = False
        self._digest_on = False

    def _sync_mmu(self, hardware):
        """MMU page-table projection (mirrors the snapshot body). Stable during
        the dual phase, but the re-sync is a cheap cgo call and keeps the two
        sides honest if a slow-path store ever touches the table."""
        mmu = getattr(hardware, 'mmu', None)
        if mmu is None:
            self.smem.set_mmu()
        else:
            self.smem.set_mmu(_opt_int(mmu.con), _opt_int(mmu.begin), _opt_int(mmu.end))

    def _pack_ram(self):
        out = bytearray()
        for addr, byte in self.mem.ram.items():
            out += struct.pack('<IB', addr & 0xffffffff, byte & 0xff)
        return bytes(out)

    def _pack_unknown_origins(self):
        out = bytearray()
        for addr, origin in self.mem.unknown_origins.items():
            out += struct.pack('<II', addr & 0xffffffff, origin & 0xffffffff)
        return bytes(out)

    def _pack_icfg_spills(self):
        out = bytearray()
        for addr, v in self.mem.icfg_spills.items():
            origin = getattr(v, 'origin', None)
            core = origin[0] if origin is not None else 0
            gen = origin[1] if origin is not None else 0
            out += struct.pack('<IIIIii', addr & 0xffffffff, v.value & 0xffffffff,
                               v.known & 0xffffffff, v.identity & 0xffffffff, core, gen)
        return bytes(out)

    def _sync_abstract_mem(self):
        """Full-replace the Go unknown_origins / icfg_spills maps from the live
        Python memory (both are tiny; a wholesale replace is always correct and
        cheaper than a delta for these provenance overlays)."""
        self.smem.replace_unknown_origins(self._pack_unknown_origins())
        self.smem.replace_icfg_spills(self._pack_icfg_spills())

    def _sync_ram(self):
        """Reconcile the Go ram map with the live Python mem.ram.

        The first call (and any wholesale dict replacement, e.g. a snapshot
        restore) does a full import and returns None (decode caches must be
        fully cleared). Afterwards only the bytes Python changed since the last
        sync are pushed: a byte still known is imported with its final value, a
        byte now unknown is removed. Returns the changed-address list for
        targeted decode-cache invalidation (empty = nothing changed)."""
        if self.mem is None:
            return None
        if not self._ram_synced or self.mem.ram is not self._ram_dict:
            t0 = time.perf_counter()
            packed = self._pack_ram()
            _t('pack_ram', time.perf_counter() - t0)
            t0 = time.perf_counter()
            self.smem.replace_ram(packed)
            _t('go_map', time.perf_counter() - t0)
            self._ram_dict = self.mem.ram
            self._ram_synced = True
            if self.mem._gosim_dirty is not None:
                self.mem._gosim_dirty.clear()
            return None
        dirty = self.mem._gosim_dirty
        if dirty is None or not dirty:
            return []
        changed = list(dirty)
        if TIMING is not None:
            TIMING['dirty_sum'] = TIMING.get('dirty_sum', 0) + len(changed)
            TIMING['dirty_max'] = max(TIMING.get('dirty_max', 0), len(changed))
            TIMING['n:dirty'] = TIMING.get('n:dirty', 0) + 1
        ram = self.mem.ram
        t0 = time.perf_counter()
        writes = bytearray()
        removes = bytearray()
        for a in changed:
            b = ram.get(a)
            if b is None:
                removes += struct.pack('<I', a & 0xffffffff)
            else:
                writes += struct.pack('<IB', a & 0xffffffff, b & 0xff)
        _t('pack_ram', time.perf_counter() - t0)
        t0 = time.perf_counter()
        if writes:
            self.smem.import_ram(bytes(writes))
        if removes:
            self.smem.remove_ram(bytes(removes))
        _t('go_map', time.perf_counter() - t0)
        dirty.clear()
        return changed

    @staticmethod
    def cores_concrete(c0, c1):
        """Both cores strictly concrete (pc/icfg/R all exact ints)."""
        return cores_concrete(c0, c1)

    @staticmethod
    def cores_head_ok(c0, c1):
        """Both cores can enter the Go dual loop (pc/icfg concrete; R concrete or
        UnknownWord/IcfgValue tags)."""
        return cores_head_ok(c0, c1)

    def note_slow_path(self):
        """Python 慢路径(select/c.step/advance/交付投影)会改写 live 状态,使
        Go 常驻状态不再等于 Python;下一次进入必须重新快照。probe_boot 在慢
        路径入口调用,清掉 clean 续跑的 _resident 标志(缓存仍可用于分片 diff,
        故不清)。"""
        self._resident = False

    def _is_clean(self, res):
        """True when the fill ran to its step cap with no hazard (the Go 'ran N
        dual-core attempts ... no hazard' reason) and committed at least one
        step. Only such a fill leaves Python == Go after the dual_buf drain, so
        only it can make the next fill a direct continue."""
        return (res.get('exit') == 1
                and res.get('reason', '').startswith('ran ')
                and len(res.get('steps') or []) > 0)

    def snapshot(self, machine, hardware, c0, c1):
        """Snapshot the live state into the Go world (cores / select / periph /
        after_step config), re-syncing the RAM bytes Python wrote since the last
        Go run (slow-path stores / abstract writes).

        第四刀:真实再入只推分片。cs/ms/ps/aps 与上次推入的缓存对比,不变的
        分片不再 cgo 往返(核按 per-core set_core 只推改过的那个;after_step
        配置变化稀少、通常整段跳过)。"""
        changed = self._sync_ram()
        self._sync_abstract_mem()
        self._sync_mmu(hardware)

        t0 = time.perf_counter()
        cs0 = core_state(c0)
        # c1 None = 单核阶段:核 1 尚未 launch,Go 世界保持 Cores[1]=nil;核 1
        # 只会在 Python 慢路径真正 release 后出现,届时下一次快照把它推进原世界。
        cs1 = core_state(c1) if c1 is not None else None
        ms = machine_state(machine)
        ps = periph_state(hardware)
        aps = after_step_state(machine)
        _t('json_state', time.perf_counter() - t0)

        # Invalidate only the decode-cache windows whose executed bytes changed
        # (a full clear was the per-fill cost; the delta keeps the cache warm
        # across fills). A full import (first sync / wholesale replacement)
        # still clears both caches.
        t0 = time.perf_counter()
        if changed is None:
            self.go0.clear_cache()
            self.go1.clear_cache()
        elif changed:
            self.go0.invalidate_ram(changed)
            self.go1.invalidate_ram(changed)
        _t('clear_cache', time.perf_counter() - t0)

        t0 = time.perf_counter()
        if self._cs[0] != cs0:
            self.world.set_core(0, cs0)
            self._cs[0] = cs0
        if self._cs[1] != cs1:
            self.world.set_core(1, cs1)
            self._cs[1] = cs1
        if self._ms != ms:
            self.machine.load(ms)
            self._ms = ms
        if self._ps != ps:
            self.periph.load(ps)
            self._ps = ps
        if self._aps != aps:
            self.world.set_afterstep(aps)
            self._aps = aps
        _t('load_state', time.perf_counter() - t0)

    def run_loop(self, machine, hardware, c0, c1, n):
        """Snapshot -> run one Go RunLoop call -> restore. Returns the raw
        LoopResult dict (exit/stop_pc/stop_core/reason/irq/steps/cores).

        第四刀:上一个 fill 是 clean 时(_resident)相邻批直接续跑——Go 已持有
        正确状态,跳过 json_state+load_state 的整段重建,只保留 RAM/抽象内存/
        MMU 的廉价重同步(抽象写与慢路径存需要在再入前追平)。"""
        if self._resident:
            changed = self._sync_ram()
            self._sync_abstract_mem()
            self._sync_mmu(hardware)
            if changed is None:
                self.go0.clear_cache()
                self.go1.clear_cache()
            elif changed:
                self.go0.invalidate_ram(changed)
                self.go1.invalidate_ram(changed)
        else:
            self.snapshot(machine, hardware, c0, c1)
        t0 = time.perf_counter()
        res = self.world.run_loop(n)
        _t('run_loop', time.perf_counter() - t0)
        # 下一个 fill 能否直接续跑,取决于本 fill 是否 clean。非 clean(边界
        # 交回)会让 Python 慢步改写状态,必须在再入时重新快照。
        self._resident = self._is_clean(res)
        self.restore(machine, hardware, c0, c1, res)
        return res

    def restore(self, machine, hardware, c0, c1, res):
        """Write one Go LoopResult back into the live Python objects. The
        post-run select / advance state arrives folded into the run_loop result
        (machine / periph keys), so there are no separate cgo round-trips for
        machine.state() / periph.state() (第四刀:restore 侧 3 次 cgo -> 1 次)."""
        t0 = time.perf_counter()
        cores = res.get('cores') or [None, None]
        restore_core(c0, cores[0])
        if c1 is not None:
            restore_core(c1, cores[1])
        restore_machine(machine, res.get('machine'))
        restore_periph(hardware, res.get('periph'))
        _t('restore', time.perf_counter() - t0)

    def free(self):
        self.world.free()
        self.periph.free()
        self.smem.free()

    # ---- whole-run trace digest (第四刀第②步) ------------------------------
    # Go hashes its committed steps inside run_loop; Python feeds the slow /
    # boundary steps' reference bytes (repr(row)) through digest_feed so the one
    # hash state stays in global completion order. debug=True captures every
    # hashed row's bytes for a line-by-line comparison.

    def digest_enable(self, debug=False):
        self.world.digest_enable(debug=debug)
        self._digest_debug = debug
        self._digest_on = True

    def digest_reset(self):
        self.world.digest_reset()

    def digest_feed(self, data):
        self.world.digest_feed(data)

    def digest_count(self):
        return self.world.digest_count()

    def digest_hex(self):
        return self.world.digest_hex()

    def digest_step_len(self, index):
        return self.world.digest_step_len(index)

    def digest_step_get(self, index):
        return self.world.digest_step_get(index)
