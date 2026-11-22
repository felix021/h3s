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
    return ok(c0) and ok(c1)


# ---- snapshot: live Python objects -> Go JSON shapes ---------------------------

def core_state(c):
    """One live Python CPU -> the coreStateJSON shape (cshared/world.go)."""
    def regs():
        out = []
        for v in c.R:
            if type(v) is not int:
                raise ValueError('dual engine snapshots only concrete GPRs (never fabricate a value)')
            out.append(v)
        return out

    def outer():
        o = getattr(c, 'audio_irq_outer_context', None)
        if not o:
            return None
        return {'active_context': o.get('active_context') or '',
                'reti': _opt_int(o.get('reti')), 'sp': o.get('sp', 0)}

    return {
        'id': c.core_id,
        'r': regs(),
        'flags': c.flags,
        'sp': c.sp if c.sp_known else 0,
        'sp_known': c.sp_known,
        'rets': c.rets,
        'pc': c.pc,
        'icfg': c.icfg if c.icfg_known else 0,
        'icfg_known': c.icfg_known,
        'steps': c.steps,
        'repeat_active': (getattr(c, 'repeat', None) is not None
                          or getattr(c, 'after_then', None) is not None),
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
        insn, _, size = c.decode(c.pc)
        return {
            'id': c.core_id, 'pc': c.pc, 'steps': c.steps,
            'icfg': c.icfg if c.icfg_known else 0,
            'icfg_known': c.icfg_known,
            'insn_id': insn.id, 'insn_size': size,
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
        'events': [dict(e) for e in m.events],
        'event_count': m.event_count,
        'timer2_poll_wakes': [dict(w) for w in m.timer2_poll_wakes],
        'current_core': getattr(hw, 'current_core', 0),
        'core_icfg': [_opt_int(core_icfg.get(0)), _opt_int(core_icfg.get(1))],
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
    }


# ---- restore: Go JSON shapes -> live Python objects ---------------------------

def restore_core(c, gc):
    """Write one Go coreStateJSON back into the live Python CPU.

    Only R / flags / pc / steps are written back: the stage-1/2 segment contract
    never touches sp / rets / icfg / the special registers (no memory, no sp move,
    no sr move, no call/return), and delivery projections stay Python, so those
    fields are unchanged by the Go run and must not be clobbered by a fabricated
    placeholder (e.g. an unknown sp would otherwise be turned into 0)."""
    if gc is None:
        return
    c.R = [v if type(v) is int else 0 for v in gc['r']]
    c.flags = gc['flags'] & 0xFFFFFFFF
    c.pc = gc['pc'] & 0xFFFFFFFF
    c.steps = gc['steps']


def restore_machine(m, gs):
    """Write one Go machine StateJSON back into the live DualCoreMachine."""
    m.idle_wait_cores = {i for i, b in enumerate(gs['idle_wait']) if b}
    m.last_core = gs['last_core']
    m.deferred_core1_turns = gs['deferred_core1_turns']
    m.seen_cpu1_control_kicks = gs['seen_cpu1_control_kicks']
    m.timer2_poll_wakes = [dict(w) for w in gs['timer2_poll_wakes']]
    m.events = [dict(e) for e in gs['events']]
    m.event_count = gs['event_count']
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

    def __init__(self, rom, app, entry, regions=(), readonly=(), mem=None):
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
                                    self.machine.handle, 0, 0, 0xFFFFFFFF)
        self.world.set_periph(self.periph.handle)

    def _pack_ram(self):
        out = bytearray()
        for addr, byte in self.mem.ram.items():
            out += struct.pack('<IB', addr & 0xffffffff, byte & 0xff)
        return bytes(out)

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

    def snapshot(self, machine, hardware, c0, c1):
        """Snapshot the live state into the Go world (cores / select / periph /
        after_step config), re-syncing the RAM bytes Python wrote since the last
        Go run (slow-path stores / abstract writes)."""
        changed = self._sync_ram()

        t0 = time.perf_counter()
        cs0 = core_state(c0)
        cs1 = core_state(c1)
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
        self.world.set_cores([cs0, cs1])
        self.machine.load(ms)
        self.periph.load(ps)
        self.world.set_afterstep(aps)
        _t('load_state', time.perf_counter() - t0)

    def run_loop(self, machine, hardware, c0, c1, n):
        """Snapshot -> run one Go RunLoop call -> restore. Returns the raw
        LoopResult dict (exit/stop_pc/stop_core/reason/irq/steps/cores)."""
        self.snapshot(machine, hardware, c0, c1)
        t0 = time.perf_counter()
        res = self.world.run_loop(n)
        _t('run_loop', time.perf_counter() - t0)
        self.restore(machine, hardware, c0, c1, res)
        return res

    def restore(self, machine, hardware, c0, c1, res):
        t0 = time.perf_counter()
        cores = res.get('cores') or [None, None]
        restore_core(c0, cores[0])
        restore_core(c1, cores[1])
        ms = self.machine.state()
        ps = self.periph.state()
        restore_machine(machine, ms)
        restore_periph(hardware, ps)
        _t('restore', time.perf_counter() - t0)

    def free(self):
        self.world.free()
        self.periph.free()
        self.smem.free()
