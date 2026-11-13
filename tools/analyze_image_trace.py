#!/usr/bin/env python3
"""Three-image startup-trajectory diff under the shared frontier projection stack.

Loads two or three named UFW images (official / tested_b79 / eq_fence_repair,
each SHA-pinned), runs the real startup prefix for each under the IDENTICAL
projection stack — imported straight from probe_irq_entry.irq_case — and
compares the two executions STEP BY STEP: per executed instruction the compared
signature is (core, before_pc, next_pc, flags, all 16 GPRs in canonical form
including UnknownWord byte provenance and IcfgValue masks, and every guest RAM
write of that step). The report states the first divergence (step, PC,
classification, root cause against the byte-diff runs of the two images) or
that no divergence was observed up to the shared stop point. No device access,
no flash approval.

Two shared-stack scenarios (--scenario): the default crc_bankb runs the pinned
crc_bankb_erased_fixture frontier flag set; p11_heap_experimental layers the
p11_heap_experimental_config conditional opt-ins (derived-not-measured heap
fixture, Timer0 STD12M, LP timer, P33 family, P3_LS, reset-projection mask
sets, subword stores) on the same stack up to the current conditional frontier.
Every conclusion under the conditional scenario is conditional, not a verified
boot fact; the default scenario's behavior and pins stay byte-identical.

Honest comparison basis (what a "no divergence" result does and does not mean):
- per-step comparison covers scheduling, fetch PC, control flow, flags, the
  full register file and guest RAM writes;
- peripheral-register writes are NOT in the per-step signature (no hook): a
  divergence there only becomes visible through a later read-back into a GPR,
  a stop-point difference, or the final report comparison;
- when both step traces are identical the two final reports (stop, registers,
  adapter counters, peripheral records, memory snapshots, machine events) are
  compared as a whole;
- after a CONTROL-flow divergence the tool searches for a step lag under which
  the two control sequences realign for the whole remaining suffix up to both
  stops (reconvergence): the realignment point, the zero-divergence lengths,
  and every remaining signature-divergent span under that lag (register state
  and guest RAM writes) are reported; if no lag realigns the traces that fact
  is reported honestly;
- every pair also reports stop-point parity (steps, per-core steps, full stop
  records): reaching the same honest boundary under the shared stack;
- identical or reconverging trajectories on this prefix only mean the prefix
  did not CONSUME the image differences into any compared observable (or
  consumed them into values that died without changing control flow); it is
  NOT an equivalence proof for the images (unconsumed hook sites stay
  unconsumed evidence).
"""
import argparse
import hashlib
import itertools
import json
from pathlib import Path

import probe_dualcore
import probe_irq_entry
from br28_machine import DualCoreMachine
from br28_icfg import IcfgValue
from br28_unknown import UnknownWord
from probe_arch_startup import REPAIRED_SHA, candidate_view
from probe_boot import APP_SHA, OFFICIAL_SHA, application, check_rom, flash_image
from probe_boot_handoff import package_header
from probe_pi32v2 import PIN, ROOT, require, upstream
from verify_eq_candidate import load_image
from verify_ufw_integrity import verify

B79_SHA = 'b79cb3a6f8702eb9e1d12eaf181a968f6b5848a66c051b2c792c39aa8b42fa1b'
B79_APP_SHA = '8f594d5908f6d4eac9b63e5e4538d3c0dbf0ed1a1e285547fcf2d9c6b0914517'
EQ_APP_SHA = '3788e3a1e25ed4e14ac3e8167674279e2d9e48e1904684c4aeaa06bc11d579cb'
PLAIN_LENGTHS = {'official': 0xE3053, 'tested_b79': 0xE3053, 'eq_fence_repair': 0xE31C5}
OFFICIAL_DEFAULT = ROOT / 'firmware/QCY_H3S_7018F_1T2_release_note_V1.1.8_011.ufw'

# Shared-stack scenarios. The crc_bankb flag set is the pinned shared-frontier
# stack (main scenario; default). The p11_heap_experimental flag set layers the
# conditional opt-ins of probe_irq_entry's p11_heap_experimental_config scenario
# (derived-not-measured heap fixture, Timer0 STD12M, LP timer, P33 family,
# P3_LS, reset-projection mask sets, subword stores...) on top of the same
# stack. The scenario construction (probe_irq_entry.irq_case) is imported, and
# the two data constants below are imported values, never duplicated here; the
# boolean switch lists themselves are kept side by side with the upstream
# scenario calls in probe_irq_entry.main() — tools/test_image_trace_diff.py
# pins the two lists against that call site by AST so upstream flag drift
# fails the unit tests instead of silently changing what this tool runs.
BASE_SCENARIO_FLAGS = dict(entry=True, banks=True, idle_wait=True, rti=True,
                           unmaskable=True, sfc_suspend=True, gpio_out_readback=True,
                           kick_wake=True, hardware_crc=True, rep_block=True,
                           rep_unknown_transport=True, repeat=True, icu_region=True,
                           dcu_cmo=True, zero_rep_skip=True, erased_bankb=True,
                           insert_load_pair=True, halfword_pair=True, p11_power_init=True)
CONDITIONAL_EXTRA_FLAGS = dict(
    heap_info_bytes=probe_irq_entry.P11_HEAP_INFO_DERIVED,
    byte_pair=True, lp_timer_writes=True, lrct_trim_done=True,
    lrct_trim_result=probe_irq_entry.LRCT_TRIM_RESULT_PROBE,
    timer0_std12m=True, lp_timer_con_readback=True,
    p33_analog_config=True, power_init_rstcon_masks=True,
    power_init_wkup_dly=True,
    mem_pwr_con=True, p33_window=True, p33_port_sel=True,
    p33_awkup=True, p3_ls=True, p11_syscon1_masks=True, fd31_adc_con=True,
    fd31_adc_con_readback=True,
    fd31_adc_pnd_completion=True,
    fd31_adc_res_named_values=probe_irq_entry.FD31_ADC_RES_NAMED_VALUES,
    p33_analog_tail=True, p3_ana_read_ldoin_low=True, gpcnt_con_readback=True,
    jl_clock_dcck=True,
    subword_stores=True, timer0_irq4_delivery=True, core_tick_irq3_delivery=True)

SCENARIOS = {
    'crc_bankb': {
        'irq_case_flags': BASE_SCENARIO_FLAGS,
        'stack_description': ('probe_irq_entry.irq_case with the crc_bankb_erased_fixture '
                              'frontier flag set (imported)'),
        'default_budget': 300000,
        # 配置新鲜度钉子:official 必须复现主前沿停点,否则本工具的 flag 集已与
        # 主前沿场景漂移(或上游场景变更),结果不可信。上游前沿推进后按新前沿重钉。
        'freshness': {'completed_steps': 289849, 'stop_pc': '0x60679ba',
                      'stop_fields': {'kind': 'unmapped_memory', 'address': '0xf27f4b'}},
    },
    'p11_heap_experimental': {
        'irq_case_flags': {**BASE_SCENARIO_FLAGS, **CONDITIONAL_EXTRA_FLAGS},
        'stack_description': ('probe_irq_entry.irq_case with the p11_heap_experimental_config '
                              'conditional flag set (crc_bankb stack plus the derived-not-measured '
                              'opt-ins: every conclusion under this scenario is conditional, not '
                              'a verified boot fact)'),
        'default_budget': 3200000,
        # 条件前沿新鲜度钉子:official 在条件 flag 集下必须复现当前条件前沿停点
        # (probe_irq_entry 的 p11_heap_experimental_config 场景前沿)。条件前沿随
        # 上游合并前移后按新前沿重钉(前沿前移后可重跑跟进)。路 BY:PND 完成
        # 投影+RES 具名 fixture 闭合采样事务,前沿 447,383 @ P3_ANA_READ 首读。
        # 路 CB:p3_ana_read_ldoin_low 具名常量 fixture(SDK charge_hw.h bit1=
        # LVCMP_DET/bit2=LDO5V_DET+charge.c 阈值+无外部供电场景锚→两比较器读 0)
        # 放行首读;L5V/CHG_CON2 计算寻址面并入 tail 容器;~447,490 core tick
        # 首溢出真实交付,其 handler 在途期间 Timer0 请求被延期机制挂起(14 次,
        # 挂起不丢弃);OS tick 级联把世界带进 0x6069538 的 GPCNT_CON 读,路 CB
        # 停在 601,880 @ unknown_gpcnt_configuration_bits。
        # 路 CG:gpcnt_con_readback 位粒度读回投影(br28.h CON __RW+全 app+ROM
        # 唯一写者=固件自己的 disable+clear 位 RMW 对)放行该读——bit0=0(app
        # 自己关断)→ 真实跳过分支,任务 0x6069538 九次调度全空手返回;W1[0]
        # 20 轮超时路径全预算执行(41 次 fixture 读、不创建充电任务);core tick
        # 溢出交付合计 9 次/延期 138 次/Timer0 交付 286 次;超时后进 mode 切换
        # 函数 0x6068152(r0==1 分支)——3,070,626 @ 0x606817a 读
        # [0xfd0008]=JL_CLOCK->DCCK_CON0 停。
        # 路 CL:jl_clock_dcck 投影(路 AM 容器同款「显式合成初始字 0 + 读回=
        # 当前寄存器值」;br28.h __RW + SDK power_clock.c save/restore 双证据)
        # 放行该读——mode 切换函数体真实执行(DCCK_CON0 0x46000e0c/DCCK_CON1
        # 0xff000100/DCCK_CON2 0x10000 + WLA_CON8/P3_ANA_CON0/7/8/9/10 配置 +
        # 软件延时),W1[0] 返回,W1[1] walker call scheduler 中断上下文 helper
        # 0x6065c3a,其 icfg 读(known_mask 0x3ff/value 0x300)被 0x6065c4c
        # `if ((r0&0xFF)!=0)` 分支消费——部分 ICFG 模型的 IcfgValue.__and__
        # 要求整字全知、此形状无处理器,诚实停 partial_icfg_value_consumed。
        # **新前沿 3,070,786 @ 0x6065c4c**(active-priority 低字节测试,归下一路)。
        'freshness': {'completed_steps': 3070786, 'stop_pc': '0x6065c4c',
                      'stop_fields': {'kind': 'partial_icfg_value_consumed',
                                      'known_mask': '0x3ff', 'known_value': '0x300'}},
    },
}

COMPARISON_BASIS = (
    'per-step comparison covers scheduling, fetch PC, control flow, flags, the full 16-GPR register file '
    '(including UnknownWord byte provenance and IcfgValue masks) and every guest RAM write of the step; '
    'peripheral-register writes are NOT in the per-step signature (no hook), so a divergence there only '
    'becomes visible through a later read-back into a GPR, a stop-point difference or the final report '
    'comparison; when both step traces are identical the two final reports are compared as a whole; '
    'after a control-flow divergence a step-lag search reports reconvergence (the realignment point, '
    'zero-divergence lengths and every remaining signature-divergent span under the lag) or the honest '
    'no-realignment fact; every pair reports stop-point parity; identical or reconverging trajectories '
    'on this prefix only mean the prefix did not consume the image differences into any compared '
    'observable (or consumed them into values that died without changing control flow), it is NOT an '
    'image-equivalence proof')

# 展示用已知差异语义对照(来源:verify_eq_candidate.audit 站点 + HANDOFF 镜像
# 资源节)。只用于把首分歧 PC/差异游程标注成人类可读的语义,不参与判定。
KNOWN_SITE_LABELS = (
    (0x34E92, 0x34E98, 'debug_init lower PC limit load 0x06000100 (shared)'),
    (0x34E98, 0x34E9E, 'debug_init fence upper PC limit immediate (b79 0x060c3338, eq candidate 0x060e31c5)'),
    (0x59CE, 0x59D4, 'eq hook site eq_source_read_hook'),
    (0x25030, 0x25036, 'eq hook site eq_record_preset_source'),
    (0x2510E, 0x25114, 'eq hook site eq_record_custom_source'),
    (0x25364, 0x2536A, 'eq hook site eq_key_hook'),
    (0x78846, 0x7884C, 'eq hook site eq_open_hook (inside power_init)'),
    (0x9E510, 0x9E511, 'official-vs-b79 ANC key-cycle table byte at app.bin 0x9e410'),
    (0x0, 0x4, 'resource table header CRC word'),
    (0x20, 0x24, 'resource table nested CRC word'),
    (0xE0A2C, 0xE0A30, 'stream.bin entry header CRC'),
    (0xE2344, 0xE23A2, 'stream.bin volume parameter region'),
    (0xE2ED3, 0xE2ED5, 'config.dat JLFS header CRC (recomputed over the flipped index field)'),
    (0xE2EE1, 0xE2EE2, 'config.dat JLFS index field (b79 terminator 1, eq candidate 0: enumeration continues to the appended entry)'),
    (0xE3053, 0xE31C5, 'appended eq_toggle.bin (eq candidate only)'),
)


def site_label(offset):
    for low, high, label in KNOWN_SITE_LABELS:
        if low <= offset < high:
            return label
    return None


def _register_signature(value):
    if isinstance(value, bool):
        return repr(value)
    if isinstance(value, int):
        return f'{value:08x}'
    if isinstance(value, UnknownWord):
        data = ''.join(f'{b:02x}' if b is not None else '??' for b in value.bytes)
        origins = ','.join(hex(o) if o is not None else '-' for o in value.origins)
        return f'u{data}|{origins}'
    if isinstance(value, IcfgValue):
        return f'i{value.known:08x}.{value.value:08x}'
    return '?'


class TraceMachine(DualCoreMachine):
    """Observation-only DualCoreMachine: records one compact signature per
    executed instruction. Inherited scheduling, projections and honesty stops
    are untouched; the two recording overrides only delegate to super()."""

    instances = []

    def __init__(self, *args, **kwargs):
        self.trace = []
        self.pending_writes = []
        TraceMachine.instances.append(self)
        super().__init__(*args, **kwargs)

    def note_memory_write(self, address, data):
        self.pending_writes.append((address, data.hex()))
        super().note_memory_write(address, data)

    def after_step(self, cpu=None, before_pc=None):
        super().after_step(cpu, before_pc)
        if cpu is not None:
            flags = getattr(cpu, 'flags', None)
            self.trace.append((cpu.core_id, before_pc, cpu.pc,
                               f'{flags:02x}' if isinstance(flags, int) else repr(flags),
                               tuple(_register_signature(v) for v in cpu.R),
                               tuple(self.pending_writes)))
            self.pending_writes = []


def frontier_run(module, app, header, rom, name, *, flash_image_bytes, readonly=(),
                 budget=None, scenario='crc_bankb'):
    """One run of a shared-stack scenario via probe_irq_entry.irq_case (the
    scenario parameter construction is imported, never duplicated: the flag
    set comes from SCENARIOS and is pinned against probe_irq_entry.main()'s
    scenario calls by unit tests). scenario 'crc_bankb' is the pinned shared
    frontier (default); 'p11_heap_experimental' layers the conditional
    derived-not-measured opt-ins on the same stack. budget overrides
    irq_case's own budget through a dual_case wrapper. Returns
    (result, machine_trace)."""
    require(scenario in SCENARIOS, f'unknown scenario {scenario!r}')
    TraceMachine.instances = []
    original_machine = probe_dualcore.DualCoreMachine
    original_dual = probe_irq_entry.dual_case
    probe_dualcore.DualCoreMachine = TraceMachine
    if budget is not None:
        def shim(*args, **kwargs):
            kwargs['budget'] = budget
            return original_dual(*args, **kwargs)
        probe_irq_entry.dual_case = shim
    try:
        result = probe_irq_entry.irq_case(
            module, app, header, rom, name,
            **SCENARIOS[scenario]['irq_case_flags'],
            flash_image_bytes=flash_image_bytes, readonly=readonly)
    finally:
        probe_dualcore.DualCoreMachine = original_machine
        probe_irq_entry.dual_case = original_dual
    require(len(TraceMachine.instances) == 1, 'expected exactly one machine per run')
    return result, TraceMachine.instances[0].trace


def load_official(path, _official=None):
    app = application(path)
    image = flash_image(path)
    return app, image, (), OFFICIAL_SHA


def load_b79(path, official_path):
    raw, flash, plain = load_image(path)
    require(hashlib.sha256(raw).hexdigest() == B79_SHA, 'tested_b79 label requires the exact tested ANC/tone image')
    require(verify(path)['passed'], 'tested_b79 CRC failure')
    official_flash = load_image(official_path)[1]
    require(flash[:0x2000] == official_flash[:0x2000], 'tested_b79 bootloader/header differs from official (shared handoff assumption broken)')
    require(len(plain) == PLAIN_LENGTHS['tested_b79'], 'tested_b79 plaintext length changed')
    app = plain[0x100:0x100 + 842654]
    require(hashlib.sha256(app).hexdigest() == B79_APP_SHA, 'tested_b79 application bytes changed')
    return app, plain, (), B79_SHA


def load_eq_candidate(path, official_path):
    app, readonly = candidate_view(path, REPAIRED_SHA, official_path)
    plain = load_image(path)[2]
    require(len(plain) == PLAIN_LENGTHS['eq_fence_repair'], 'eq_fence_repair plaintext length changed')
    require(hashlib.sha256(app).hexdigest() == EQ_APP_SHA, 'eq_fence_repair application bytes changed')
    return app, plain, readonly, REPAIRED_SHA


def byte_diff_runs(a, b):
    """Non-overlapping [lo,hi) runs where the two plaintexts differ, plus a
    trailing run for the length extension (appended eq_toggle.bin)."""
    n = min(len(a), len(b))
    runs = []
    i = 0
    while i < n:
        if a[i] != b[i]:
            j = i
            while j < n and a[j] != b[j]:
                j += 1
            runs.append((i, j))
            i = j
        else:
            i += 1
    if len(a) != len(b):
        runs.append((n, max(len(a), len(b))))
    return runs


def pc_run_length_summary(trace, head=12, tail=12):
    runs = []
    for core, before_pc, _next_pc, _flags, _regs, _writes in trace:
        if runs and runs[-1][0] == before_pc and runs[-1][2] == core:
            runs[-1][1] += 1
        else:
            runs.append([before_pc, 1, core])
    keep_head = runs[:head]
    keep_tail = runs[-tail:] if len(runs) > head + tail else []
    return {'run_count': len(runs), 'head': [[hex(pc), count, core] for pc, count, core in keep_head],
            'tail': [[hex(pc), count, core] for pc, count, core in keep_tail],
            'omitted_middle_runs': max(0, len(runs) - len(keep_head) - len(keep_tail))}


def classify_divergence(step, row_a, row_b, diff_runs, plain_a, plain_b):
    (core_a, pc_a, next_a, flags_a, regs_a, writes_a) = row_a
    (core_b, pc_b, next_b, flags_b, regs_b, writes_b) = row_b
    detail = {'step': step, 'core_a': core_a, 'core_b': core_b,
              'pc_a': hex(pc_a), 'pc_b': hex(pc_b),
              'next_pc_a': hex(next_a), 'next_pc_b': hex(next_b)}
    if core_a != core_b:
        detail['classification'] = 'core_scheduling_divergence'
    elif pc_a != pc_b:
        detail['classification'] = 'control_flow_divergence'
        detail['note'] = 'identical previous step, different next fetch PC: the divergence entered between the two steps (delivery/idle-wake bookkeeping differs)'
    elif next_a != next_b:
        detail['classification'] = 'branch_target_divergence'
        detail['note'] = 'same instruction, different next PC: a data input selected a different branch'
    elif regs_a != regs_b:
        changed = [i for i in range(16) if regs_a[i] != regs_b[i]]
        detail['classification'] = 'register_state_divergence'
        detail['registers'] = {str(i): {'a': regs_a[i], 'b': regs_b[i]} for i in changed}
    elif writes_a != writes_b:
        detail['classification'] = 'memory_write_divergence'
        detail['writes_a'] = [[hex(address), data] for address, data in writes_a]
        detail['writes_b'] = [[hex(address), data] for address, data in writes_b]
    else:
        detail['classification'] = 'flags_divergence'
        detail['flags_a'] = flags_a
        detail['flags_b'] = flags_b
    # root cause against the actual image byte differences: an instruction whose
    # fetched extent [pc, next_pc) overlaps a differing run consumes the image
    # difference directly (e.g. a patched immediate inside a shared opcode).
    flash_base = 0x06000000
    lo_pc = min(pc_a, pc_b) - flash_base
    hi_pc = max(next_a, next_b) - flash_base
    extent_hits = [(lo, hi) for lo, hi in diff_runs if lo < hi_pc and hi > lo_pc]
    if extent_hits:
        lo, hi = extent_hits[0]
        detail['instruction_bytes_differ_within_instruction'] = True
        detail['image_diff_run'] = {'plain_offset': [hex(lo), hex(hi)],
                                    'cpu_range': [hex(flash_base + lo), hex(flash_base + hi)],
                                    'known_site': site_label(lo)}
        detail['instruction_bytes_a'] = plain_a[max(lo_pc, 0):hi_pc].hex() if lo_pc >= 0 else ''
        detail['instruction_bytes_b'] = plain_b[max(lo_pc, 0):hi_pc].hex() if lo_pc >= 0 else ''
        if detail['classification'] == 'register_state_divergence':
            detail['note'] = 'the executed instruction encoding differs between the images inside its immediate operand; the register divergence consumes the patched bytes directly'
    else:
        detail['instruction_bytes_differ_within_instruction'] = False
        detail['note'] = detail.get('note') or (
            'instruction bytes are identical at this PC; the differing input is data loaded earlier '
            'from image-differing memory (flash/app/resource bytes) whose value entered this register '
            'or an earlier one')
    return detail


def json_diff(a, b, path='$', out=None, limit=24):
    if out is None:
        out = []
    if len(out) >= limit:
        return out
    if type(a) is not type(b):
        out.append({'path': path, 'a': _snippet(a), 'b': _snippet(b), 'kind': 'type'})
        return out
    if isinstance(a, dict):
        for key in sorted(set(a) | set(b)):
            if key not in a or key not in b:
                out.append({'path': f'{path}.{key}', 'kind': 'missing',
                            'a': _snippet(a.get(key)), 'b': _snippet(b.get(key))})
            else:
                json_diff(a[key], b[key], f'{path}.{key}', out, limit)
            if len(out) >= limit:
                return out
    elif isinstance(a, list):
        if len(a) != len(b):
            out.append({'path': path, 'kind': 'length', 'a': len(a), 'b': len(b)})
        for i, (x, y) in enumerate(zip(a, b)):
            json_diff(x, y, f'{path}[{i}]', out, limit)
            if len(out) >= limit:
                return out
    elif a != b:
        out.append({'path': path, 'kind': 'value', 'a': _snippet(a), 'b': _snippet(b)})
    return out


def _snippet(value, width=120):
    text = json.dumps(value, sort_keys=True, default=str)
    return text if len(text) <= width else text[:width] + '…'


def _control(row):
    return (row[0], row[1], row[2])


def compare_pair(module, rom, header, name_a, image_a, name_b, image_b, budget, scenario='crc_bankb'):
    label_a, (app_a, plain_a, readonly_a, sha_a) = image_a
    label_b, (app_b, plain_b, readonly_b, sha_b) = image_b
    result_a, trace_a = frontier_run(module, app_a, header, rom, name_a,
                                     flash_image_bytes=plain_a, readonly=readonly_a,
                                     budget=budget, scenario=scenario)
    result_b, trace_b = frontier_run(module, app_b, header, rom, name_b,
                                     flash_image_bytes=plain_b, readonly=readonly_b,
                                     budget=budget, scenario=scenario)
    for label, result, plain in ((label_a, result_a, plain_a), (label_b, result_b, plain_b)):
        print(label, result['completed_steps'], result['stop_pc'], result['stop']['kind'], flush=True)
    diff_runs = byte_diff_runs(plain_a, plain_b)
    pair = {'images': {label_a: {'ufw_sha256': sha_a, 'flash_plain_sha256': hashlib.sha256(plain_a).hexdigest(),
                                 'flash_plain_bytes': len(plain_a)},
                       label_b: {'ufw_sha256': sha_b, 'flash_plain_sha256': hashlib.sha256(plain_b).hexdigest(),
                                 'flash_plain_bytes': len(plain_b)}},
            'image_byte_diff_runs': [{'plain_offset': [hex(lo), hex(hi)], 'known_site': site_label(lo)}
                                      for lo, hi in diff_runs],
            'shared_stack': SCENARIOS[scenario]['stack_description'],
            'budget': budget,
            'runs': {label_a: _run_summary(result_a), label_b: _run_summary(result_b)},
            'final_report_diffs': json_diff({k: v for k, v in result_a.items() if k != 'name'},
                                            {k: v for k, v in result_b.items() if k != 'name'}, limit=64)}
    first = None
    first_control = None
    for step, (row_a, row_b) in enumerate(zip(trace_a, trace_b)):
        if first is None and row_a != row_b:
            first = classify_divergence(step, row_a, row_b, diff_runs, plain_a, plain_b)
        if _control(row_a) != _control(row_b):
            first_control = step
            break
    pair['trace_lengths'] = {label_a: len(trace_a), label_b: len(trace_b)}
    pair['parity'] = stop_parity(label_a, result_a, label_b, result_b)
    if first_control is not None:
        row_a, row_b = trace_a[first_control], trace_b[first_control]
        pair['first_control_divergence'] = {
            'step': first_control, 'core_a': row_a[0], 'core_b': row_b[0],
            'pc_a': hex(row_a[1]), 'next_pc_a': hex(row_a[2]),
            'pc_b': hex(row_b[1]), 'next_pc_b': hex(row_b[2]),
            'registers': {str(j): {'a': row_a[4][j], 'b': row_b[4][j]}
                          for j in range(16) if row_a[4][j] != row_b[4][j]},
            'note': 'identical previous step, different next fetch PC: the diverged control flow started here'}
        pair['reconvergence'] = find_reconvergence(trace_a, trace_b, first_control)
        prefix = trace_a[:first_control]
        pair['common_control_prefix'] = {'steps': first_control, 'summary': pc_run_length_summary(prefix)}
    else:
        pair['control_flow_identical_over'] = {'steps': min(len(trace_a), len(trace_b)),
                                               'summary': pc_run_length_summary(
                                                   trace_a[:min(len(trace_a), len(trace_b))])}
    if first is not None:
        prefix = trace_a[:first['step']]
        first['context_before'] = [[row[0], hex(row[1])] for row in prefix[-6:]]
        first['context_after_a'] = [[row[0], hex(row[1])] for row in trace_a[first['step']:first['step'] + 6]]
        first['context_after_b'] = [[row[0], hex(row[1])] for row in trace_b[first['step']:first['step'] + 6]]
        pair['first_divergence'] = first
        pair['common_prefix'] = {'steps': first['step'], 'summary': pc_run_length_summary(prefix)}
        if first_control is None:
            # 无控制流错位:分歧段(连续不同签名的区间)统计有效
            spans = _divergent_spans(trace_a, trace_b)
            pair['divergent_signature_spans'] = {
                'count': len(spans),
                'first': [{'steps': [s, e], 'pc': hex(trace_a[s][1]),
                           'registers': {str(j): [trace_a[s][4][j], trace_b[s][4][j]]
                                         for j in range(16) if trace_a[s][4][j] != trace_b[s][4][j]},
                           'writes_differ': trace_a[s][5] != trace_b[s][5]}
                          for s, e in spans[:8]]}
            pair['note'] = ('register/memory-signature divergences exist but the CONTROL FLOW never diverged '
                            'and both runs reached the same stop; see divergent_signature_spans for every '
                            'point where an image difference became visible in a compared observable')
    else:
        pair['first_divergence'] = None
        pair['no_divergence_within'] = min(len(trace_a), len(trace_b))
        pair['common_prefix'] = {'steps': min(len(trace_a), len(trace_b)),
                                 'summary': pc_run_length_summary(trace_a[:min(len(trace_a), len(trace_b))])}
        if not pair['final_report_diffs']:
            pair['note'] = ('identical trajectories AND identical final reports up to the shared stop point; '
                            'this only shows the prefix did not consume the image differences into any compared '
                            'observable, it is NOT an image-equivalence proof')
        else:
            pair['note'] = ('identical per-step signatures; the two final reports still differ (stop point, '
                            'peripheral records, counters or snapshots) — a divergence invisible to the '
                            'per-step comparison surfaced only in the report')
    return pair


def _divergent_spans(trace_a, trace_b, limit=100000):
    spans = []
    start = None
    for i, (row_a, row_b) in enumerate(zip(trace_a, trace_b)):
        differ = row_a != row_b
        if differ and start is None:
            start = i
        elif not differ and start is not None:
            spans.append((start, i))
            start = None
        if len(spans) > limit:
            break
    if start is not None:
        spans.append((start, min(len(trace_a), len(trace_b))))
    return spans


def _suffix_control_realign_start(trace_a, trace_b, lag, lo):
    """Smallest s >= lo such that _control(trace_a[i]) == _control(trace_b[i+lag])
    for EVERY i in [s, len(trace_a)); None when the tails never match or the
    lag is out of range."""
    if lo + lag < 0 or len(trace_a) + lag > len(trace_b):
        return None
    s = len(trace_a)
    for i in range(len(trace_a) - 1, lo - 1, -1):
        if _control(trace_a[i]) == _control(trace_b[i + lag]):
            s = i
        else:
            break
    return s if s < len(trace_a) else None


def find_reconvergence(trace_a, trace_b, first_control):
    """Reconvergence analysis after a control-flow divergence.

    Tests the exact trace-length lag (len(b) - len(a)) — the only lag under
    which the comparison covers BOTH traces all the way to their stops — and
    asks whether the two CONTROL sequences (core, fetch PC, next PC) are
    identical for the whole remaining suffix from some realignment step on:
    i.e. the divergence inserted (or removed) a bounded number of steps ONCE
    and the control flows never diverged again. On success the remaining
    signature divergences (register state / guest RAM writes) are reported as
    spans under that lag; on failure (permanent divergence, or more than one
    insertion, or trailing unmatched steps) the honest no-realignment fact is
    reported. Pure trace-array logic, no simulator state."""
    lag = len(trace_b) - len(trace_a)
    s = _suffix_control_realign_start(trace_a, trace_b, lag, first_control)
    if s is not None:
        if lag < 0:
            # a is the longer run: the earliest suffix match can slide into a
            # PERIODIC coincidence (e.g. every table-walk round runs the same
            # code with the same step count, so a's diverged round control-
            # matches b's previous round). The semantic realignment localizes
            # the insertion: a's steps from first_control+|lag| on correspond
            # to b's steps from first_control on. The scan result guarantees
            # that candidate also suffix-matches whenever it is >= s.
            semantic = first_control - lag
            if semantic >= s:
                s = semantic
        detail = {'realigned': True, 'lag': lag,
                  'realign_step_a': s, 'realign_step_b': s + lag,
                  'immediate': s == first_control,
                  'control_zero_divergence_steps': len(trace_a) - s,
                  'aligned_to_common_stop': True,
                  'method': ('the two PC sequences match step-for-step under the lag from the realignment '
                             'point to both stops; the diverged run executed the extra steps as one bounded '
                             'insertion and the control flows never diverged again')}
        if lag > 0:
            window = {'run': 'b', 'count': lag, 'steps': [first_control, s + lag]}
            window['exact_position'] = s == first_control
        else:
            window = {'run': 'a', 'count': -lag, 'steps': [first_control, s]}
            window['exact_position'] = s - first_control == -lag
        if not window['exact_position']:
            window['note'] = ('the extra step count is exact but its position inside the bracket is not '
                              'localized (realignment was not at the divergence step)')
        detail['extra_window'] = window
        spans = _divergent_spans(trace_a[s:], [trace_b[i + lag] for i in range(s, len(trace_a))])
        spans = [(s + lo, s + hi) for lo, hi in spans]
        signature_realign = spans[-1][1] if spans else s
        detail['signature_after_realignment'] = {
            'divergent_span_count': len(spans),
            'divergent_steps': sum(hi - lo for lo, hi in spans),
            'spans_with_differing_writes': sum(
                1 for lo, hi in spans if any(trace_a[i][5] != trace_b[i + lag][5] for i in range(lo, hi))),
            'first': [{'steps': [lo, hi], 'pc_a': hex(trace_a[lo][1]),
                       'registers': {str(j): [trace_a[lo][4][j], trace_b[lo + lag][4][j]]
                                     for j in range(16) if trace_a[lo][4][j] != trace_b[lo + lag][4][j]},
                       'writes_differ': any(trace_a[i][5] != trace_b[i + lag][5] for i in range(lo, hi))}
                      for lo, hi in spans[:8]],
            'full_signature_realign_step_a': signature_realign,
            'full_signature_realign_step_b': signature_realign + lag,
            'full_signature_zero_divergence_steps': len(trace_a) - signature_realign}
        return detail
    return {'realigned': False,
            'note': ('no step lag realigns the two control sequences onto each other up to the stops; '
                     'the traces stay control-diverged to the end and per-step comparison beyond the '
                     'first control divergence only reports that the traces differ, not where')}


def stop_parity(label_a, result_a, label_b, result_b):
    """Explicit stop-point parity between the two runs of a pair: step counts,
    per-core steps and the full stop records (kind/PC/address). Same-stop parity
    under the shared stack is the 'both images reach the same honest boundary'
    fact; it is NOT a behavioral-equivalence claim."""
    stop_a, stop_b = result_a['stop'], result_b['stop']
    cores_a = {k: v['steps'] for k, v in result_a['machine']['cores'].items()}
    cores_b = {k: v['steps'] for k, v in result_b['machine']['cores'].items()}
    return {'steps': {label_a: result_a['completed_steps'], label_b: result_b['completed_steps'],
                      'delta': result_b['completed_steps'] - result_a['completed_steps']},
            'per_core_steps': {label_a: cores_a, label_b: cores_b,
                               'delta': {k: cores_b.get(k, 0) - cores_a.get(k, 0)
                                         for k in sorted(set(cores_a) | set(cores_b))}},
            'stop_pc': {'a': result_a['stop_pc'], 'b': result_b['stop_pc'],
                        'same': result_a['stop_pc'] == result_b['stop_pc']},
            'stop_record': {'a': stop_a, 'b': stop_b, 'identical': stop_a == stop_b},
            'same_stop': stop_a == stop_b and result_a['stop_pc'] == result_b['stop_pc']}


def _run_summary(result):
    return {'completed_steps': result['completed_steps'], 'stop_pc': result['stop_pc'],
            'stop': result['stop'],
            'per_core_steps': {k: v['steps'] for k, v in result['machine']['cores'].items()}}


LOADERS = {'official': load_official,
           'tested_b79': lambda path, official: load_b79(path, official),
           'eq_fence_repair': lambda path, official: load_eq_candidate(path, official)}


def check_self(report):
    """钉死 2026-11-12 首轮 official vs tested_b79 结论(全量到前沿停点):

    - 双方 289,849 步、同停点 0x60679ba(power_init 内堆信息 0xf27f4b unmapped 读)、
      per-core 步数一致;stop parity 字段(步数/每核/完整停点记录)全同;
    - 控制流(核调度/PC/next_pc)289,849 步零分歧,无 reconvergence 字段;
    - 首个签名分歧 step 155,390 @ 0x6010b16(rep 4 32 资源表头拷贝体首装载
      r2=b[r1++]),r2=0x28 vs 0x88——两镜像资源表头 CRC 字节差异(plain 0x0
      首字节)被运输进寄存器;共 30 个分歧段,每段都是表头 CRC 字节经 rep 拷贝
      /硬件 CRC 引擎计算显形(CRC 读回结果 4 处不同),全部不改变控制流;
    - ANC 键表字节(0x609e510/app.bin 0x9e410)与 stream.bin 音量参数区
      (0x60e2344..)在前缀内零读取(任何装载都会显形为 GPR/写分歧);
    - final report 差异恰好 5 处(4 处 CRC result_reads + 1 处 watch r0)。
    上游前沿推进后按新结论重钉。
    """
    pairs = {p['name']: p for p in report['pairs']}
    require(set(pairs) == {'official_vs_tested_b79'}, 'self-check must run exactly the repository pair')
    p = pairs['official_vs_tested_b79']
    for label in ('official', 'tested_b79'):
        run = p['runs'][label]
        require(run['completed_steps'] == 289849 and run['stop_pc'] == '0x60679ba' and
                run['stop']['kind'] == 'unmapped_memory' and run['per_core_steps'] == {'0': 278683, '1': 11181},
                f'{label} run changed: the shared-stack prefix is no longer the pinned frontier stop')
    parity = p['parity']
    require(parity['steps'] == {'official': 289849, 'tested_b79': 289849, 'delta': 0} and
            parity['stop_pc'] == {'a': '0x60679ba', 'b': '0x60679ba', 'same': True} and
            parity['stop_record']['identical'] and parity['same_stop'] and
            parity['stop_record']['a']['address'] == '0xf27f4b' and
            parity['per_core_steps']['delta'] == {'0': 0, '1': 0},
            'the official-vs-b79 stop parity changed: both images must reach the identical frontier stop')
    require('first_control_divergence' not in p and 'reconvergence' not in p,
            'official vs tested_b79 control flow diverged')
    first = p['first_divergence']
    require(first and first['step'] == 155390 and first['pc_a'] == '0x6010b16' and
            first['classification'] == 'register_state_divergence' and
            first['registers'] == {'2': {'a': '00000028', 'b': '00000088'}} and
            not first['instruction_bytes_differ_within_instruction'],
            'the first official-vs-b79 signature divergence is no longer the resource-table header '
            'CRC byte transported by the rep 4 32 copy at 0x6010b16')
    spans = p['divergent_signature_spans']
    require(spans['count'] == 30 and all(not s['writes_differ'] for s in spans['first']),
            'the official-vs-b79 divergence-span shape changed (image bytes visible only as '
            'register values, never as differing stored bytes)')
    diffs = sorted(d['path'] for d in p['final_report_diffs'])
    require(diffs == sorted(['$.milestones[23].r0',
                             '$.peripherals.hardware_crc_projection.result_reads[1]',
                             '$.peripherals.hardware_crc_projection.result_reads[8]',
                             '$.peripherals.hardware_crc_projection.result_reads[15]',
                             '$.peripherals.hardware_crc_projection.result_reads[22]']),
            'the official-vs-b79 final-report differences changed: ' + str(diffs))


def check_self_conditional(report):
    """钉死 2026-11-13 路 CL 条件场景结论(official vs tested_b79,条件 flag 集
    到当前条件前沿 3,070,786;一切结论条件通过、derived-not-measured;历史
    世界 689,402(路 BS)/447,383(路 BY)/601,880(路 CB)/3,070,626(路 CG)
    已被后续投影替换,见 HANDOFF 对应「步含义」段):

    - 双方 3,070,786 步、同停点 0x6065c4c(partial_icfg_value_consumed,
      known_mask 0x3ff/value 0x300,当前条件前沿——路 CL 的 jl_clock_dcck
      投影放行 mode 切换函数 0x6068152 的 DCCK_CON0/1/2 读侧后,函数体真实
      执行,W1[1] walker call scheduler 中断上下文 helper 0x6065c3a 的 icfg
      读被 `if((r0&0xFF)!=0)` 分支消费)、per-core {0:3059917,1:11181}
      一致;stop parity 全同——official↔b79 parity 延长到条件前沿
      3,070,786,控制流零分歧、无 reconvergence;
    - 首个签名分歧仍 step 155,390 @ 0x6010b16(资源表头 CRC 字节 r2=0x28 vs
      0x88,与 crc_bankb 前沿完全一致);条件前缀新增步(601,880..3,070,786,
      超时窗口+OS tick 级联+mode 切换体)没有消费任何新的镜像差异;
    - final report 差异仍恰好 5 处(4 处 CRC result_reads + 1 处 watch r0)。
    条件前沿推进后按新前沿重钉(前沿前移后可重跑跟进)。
    """
    pairs = {p['name']: p for p in report['pairs']}
    require(set(pairs) == {'official_vs_tested_b79'},
            'conditional self-check must run exactly the repository pair')
    p = pairs['official_vs_tested_b79']
    for label in ('official', 'tested_b79'):
        run = p['runs'][label]
        require(run['completed_steps'] == 3070786 and run['stop_pc'] == '0x6065c4c' and
                run['stop']['kind'] == 'partial_icfg_value_consumed' and
                run['stop']['known_mask'] == '0x3ff' and
                run['stop']['known_value'] == '0x300' and
                run['per_core_steps'] == {'0': 3059917, '1': 11181},
                f'{label} run changed: the conditional flag set no longer reaches the pinned '
                'conditional frontier stop (3070786 @ 0x6065c4c, the partial-ICFG consumption '
                'inside the scheduler interrupt-context helper 0x6065c3a after the route-CL '
                'jl_clock_dcck projection released the JL clock family and the mode-switch body '
                'ran)')
    parity = p['parity']
    require(parity['steps'] == {'official': 3070786, 'tested_b79': 3070786, 'delta': 0} and
            parity['stop_pc'] == {'a': '0x6065c4c', 'b': '0x6065c4c', 'same': True} and
            parity['stop_record']['identical'] and parity['same_stop'] and
            parity['stop_record']['a']['kind'] == 'partial_icfg_value_consumed' and
            parity['per_core_steps']['delta'] == {'0': 0, '1': 0},
            'the official-vs-b79 conditional stop parity changed: both images must reach the '
            'identical conditional frontier stop')
    require('first_control_divergence' not in p and 'reconvergence' not in p,
            'official vs tested_b79 control flow diverged under the conditional flag set')
    first = p['first_divergence']
    require(first and first['step'] == 155390 and first['pc_a'] == '0x6010b16' and
            first['classification'] == 'register_state_divergence' and
            first['registers'] == {'2': {'a': '00000028', 'b': '00000088'}} and
            not first['instruction_bytes_differ_within_instruction'],
            'the first official-vs-b79 conditional signature divergence is no longer the '
            'resource-table header CRC byte at 0x6010b16 (it must stay identical to the '
            'crc_bankb-scenario divergence)')
    spans = p['divergent_signature_spans']
    require(spans['count'] == 30 and all(not s['writes_differ'] for s in spans['first']),
            'the official-vs-b79 conditional divergence-span shape changed (image bytes must '
            'stay visible only as register values, never as differing stored bytes)')
    diffs = sorted(d['path'] for d in p['final_report_diffs'])
    require(diffs == sorted(['$.milestones[23].r0',
                             '$.peripherals.hardware_crc_projection.result_reads[1]',
                             '$.peripherals.hardware_crc_projection.result_reads[8]',
                             '$.peripherals.hardware_crc_projection.result_reads[15]',
                             '$.peripherals.hardware_crc_projection.result_reads[22]']),
            'the official-vs-b79 conditional final-report differences changed: ' + str(diffs))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rom', type=Path, required=True)
    parser.add_argument('--image', action='append',
                        help='label=path with label in {official,tested_b79,eq_fence_repair}; repeat 2-3 times')
    parser.add_argument('--pair', action='append',
                        help='labelA:labelB to compare (default: every combination)')
    parser.add_argument('--scenario', choices=sorted(SCENARIOS), default='crc_bankb',
                        help='shared-stack scenario: crc_bankb pins the main frontier (default, '
                             'unchanged behavior); p11_heap_experimental runs the conditional '
                             'derived-not-measured flag set up to the current conditional frontier '
                             '(every conclusion under it is conditional, not a verified boot fact)')
    parser.add_argument('--budget', type=int, default=None,
                        help='per-run step budget (default: scenario-specific; crc_bankb 300000, '
                             'p11_heap_experimental 720000)')
    parser.add_argument('--self-check', action='store_true',
                        help='pin the official-vs-tested_b79 conclusion of the selected scenario with '
                             'assertions (repository images only, no private artifacts)')
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    scenario = args.scenario
    budget = args.budget if args.budget is not None else SCENARIOS[scenario]['default_budget']

    if args.self_check:
        # 自检固定跑仓库内两镜像(official vs tested_b79)到所选场景的前沿停点;
        # 断言钉死已记录结论(crc_bankb=2026-11-12 首轮/路 AJ;p11_heap_experimental=
        # 路 BK 条件前沿)。上游前沿推进(probe_irq_entry 场景变化)后这些步数/PC
        # 断言会失败,按新前沿重新钉。
        require(not args.image and not args.pair, '--self-check runs the two repository images')
        args.image = [f'official={OFFICIAL_DEFAULT}',
                      f"tested_b79={ROOT / 'firmware/H3S_0.1.1.8_ANC_ON_TRANSPARENT.ufw'}"]
        args.pair = ['official:tested_b79']

    images = {}
    for item in args.image:
        label, _, raw_path = item.partition('=')
        require(label in LOADERS, f'unknown image label {label!r}; supported: {sorted(LOADERS)}')
        require(label not in images, f'duplicate image label {label!r}')
        images[label] = (Path(raw_path), label)
    require(len(images) >= 2, 'need at least two images')
    official_path = images['official'][0] if 'official' in images else None
    require(official_path is not None or {'tested_b79', 'eq_fence_repair'} <= set(images),
            'without the official image, only the tested_b79:eq_fence_repair pair is defined')

    rom = args.rom.read_bytes()
    check_rom(rom)
    module = upstream()
    if official_path is None:
        official_path = OFFICIAL_DEFAULT
    header = package_header(official_path)
    loaded = {}
    for label, (path, _) in images.items():
        loaded[label] = (label, LOADERS[label](path, official_path))

    pairs = [(item.split(':')[0], item.split(':')[1]) for item in (args.pair or [])]
    if not pairs:
        pairs = list(itertools.combinations(sorted(images), 2))
    for a, b in pairs:
        require(a in loaded and b in loaded, f'pair {a}:{b} references an unloaded image')

    # 配置新鲜度自检:official 必须复现所选场景的前沿停点,否则本工具的
    # flag 集已与主前沿场景漂移(或上游场景变更),结果不可信。
    if 'official' in loaded:
        fresh = SCENARIOS[scenario]['freshness']
        check = frontier_run(module, loaded['official'][1][0], header, rom, 'frontier_freshness_check',
                             flash_image_bytes=loaded['official'][1][1], budget=budget,
                             scenario=scenario)
        require(check[0]['completed_steps'] == fresh['completed_steps'] and
                check[0]['stop_pc'] == fresh['stop_pc'] and
                all(check[0]['stop'].get(k) == v for k, v in fresh['stop_fields'].items()),
                f'official frontier stop changed for scenario {scenario!r}: the imported flag set no '
                f'longer reproduces the pinned frontier stop ({fresh["completed_steps"]} @ '
                f'{fresh["stop_pc"]}); update this pin after the frontier advances')

    report = {'simulator_commit': PIN, 'device_accessed': False, 'whole_h3s_boot_simulated': False,
              'candidate_whole_boot_validated': False, 'suitable_for_flash_approval_alone': False,
              'comparison_basis': COMPARISON_BASIS,
              'pairs': []}
    if scenario != 'crc_bankb':
        # 默认场景报告保持逐字节不变;非默认场景才标注场景键。
        report['scenario'] = scenario
    for a, b in pairs:
        print(f'== diff {a} vs {b} (scenario {scenario}) ==', flush=True)
        report['pairs'].append({'name': f'{a}_vs_{b}',
                                **compare_pair(module, rom, header, f'trace_{a}', loaded[a],
                                               f'trace_{b}', loaded[b], budget, scenario)})
    if args.self_check:
        if scenario == 'crc_bankb':
            check_self(report)
        else:
            check_self_conditional(report)
        report['image_trace_self_check_passed'] = True
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + '\n')
    print('wrote', args.report, flush=True)


if __name__ == '__main__':
    main()
