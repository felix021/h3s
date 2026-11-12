#!/usr/bin/env python3
"""Three-image startup-trajectory diff under the shared frontier projection stack.

Loads two or three named UFW images (official / tested_b79 / eq_fence_repair,
each SHA-pinned), runs the real startup prefix for each under the IDENTICAL
projection stack — imported straight from probe_irq_entry.irq_case with the
crc_bankb_erased_fixture frontier flag set — and compares the two executions
STEP BY STEP: per executed instruction the compared signature is (core,
before_pc, next_pc, flags, all 16 GPRs in canonical form including
UnknownWord byte provenance and IcfgValue masks, and every guest RAM write of
that step). The report states the first divergence (step, PC, classification,
root cause against the byte-diff runs of the two images) or that no divergence
was observed up to the shared stop point. No device access, no flash approval.

Honest comparison basis (what a "no divergence" result does and does not mean):
- per-step comparison covers scheduling, fetch PC, control flow, flags, the
  full register file and guest RAM writes;
- peripheral-register writes are NOT in the per-step signature (no hook): a
  divergence there only becomes visible through a later read-back into a GPR,
  a stop-point difference, or the final report comparison;
- when both step traces are identical the two final reports (stop, registers,
  adapter counters, peripheral records, memory snapshots, machine events) are
  compared as a whole;
- identical trajectories on this prefix only mean the prefix did not CONSUME
  the image differences into any compared observable; it is NOT an equivalence
  proof for the images (unconsumed hook sites stay unconsumed evidence).
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

COMPARISON_BASIS = (
    'per-step comparison covers scheduling, fetch PC, control flow, flags, the full 16-GPR register file '
    '(including UnknownWord byte provenance and IcfgValue masks) and every guest RAM write of the step; '
    'peripheral-register writes are NOT in the per-step signature (no hook), so a divergence there only '
    'becomes visible through a later read-back into a GPR, a stop-point difference or the final report '
    'comparison; when both step traces are identical the two final reports are compared as a whole; '
    'identical trajectories on this prefix only mean the prefix did not consume the image differences '
    'into any compared observable, it is NOT an image-equivalence proof')

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
    (0xE2ED3, 0xE2ED6, 'config.dat index bytes'),
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


def frontier_run(module, app, header, rom, name, *, flash_image_bytes, readonly=(), budget=None):
    """One run of the crc_bankb_erased_fixture frontier scenario via
    probe_irq_entry.irq_case (the scenario parameter construction is imported,
    never duplicated). budget overrides irq_case's default 250000 through a
    dual_case wrapper. Returns (result, machine_trace)."""
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
            entry=True, banks=True, idle_wait=True, rti=True, unmaskable=True,
            sfc_suspend=True, gpio_out_readback=True, kick_wake=True,
            hardware_crc=True, rep_block=True, rep_unknown_transport=True,
            repeat=True, icu_region=True, dcu_cmo=True, zero_rep_skip=True,
            erased_bankb=True, insert_load_pair=True, halfword_pair=True,
            p11_power_init=True,
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


def compare_pair(module, rom, header, name_a, image_a, name_b, image_b, budget):
    label_a, (app_a, plain_a, readonly_a, sha_a) = image_a
    label_b, (app_b, plain_b, readonly_b, sha_b) = image_b
    result_a, trace_a = frontier_run(module, app_a, header, rom, name_a,
                                     flash_image_bytes=plain_a, readonly=readonly_a, budget=budget)
    result_b, trace_b = frontier_run(module, app_b, header, rom, name_b,
                                     flash_image_bytes=plain_b, readonly=readonly_b, budget=budget)
    for label, result, plain in ((label_a, result_a, plain_a), (label_b, result_b, plain_b)):
        print(label, result['completed_steps'], result['stop_pc'], result['stop']['kind'], flush=True)
    diff_runs = byte_diff_runs(plain_a, plain_b)
    pair = {'images': {label_a: {'ufw_sha256': sha_a, 'flash_plain_sha256': hashlib.sha256(plain_a).hexdigest(),
                                 'flash_plain_bytes': len(plain_a)},
                       label_b: {'ufw_sha256': sha_b, 'flash_plain_sha256': hashlib.sha256(plain_b).hexdigest(),
                                 'flash_plain_bytes': len(plain_b)}},
            'image_byte_diff_runs': [{'plain_offset': [hex(lo), hex(hi)], 'known_site': site_label(lo)}
                                      for lo, hi in diff_runs],
            'shared_stack': 'probe_irq_entry.irq_case with the crc_bankb_erased_fixture frontier flag set (imported)',
            'budget': budget,
            'runs': {label_a: _run_summary(result_a), label_b: _run_summary(result_b)},
            'final_report_diffs': json_diff({k: v for k, v in result_a.items() if k != 'name'},
                                            {k: v for k, v in result_b.items() if k != 'name'})}
    first = None
    first_control = None
    for step, (row_a, row_b) in enumerate(zip(trace_a, trace_b)):
        if first is None and row_a != row_b:
            first = classify_divergence(step, row_a, row_b, diff_runs, plain_a, plain_b)
        if _control(row_a) != _control(row_b):
            first_control = step
            break
    pair['trace_lengths'] = {label_a: len(trace_a), label_b: len(trace_b)}
    if first_control is not None:
        row_a, row_b = trace_a[first_control], trace_b[first_control]
        pair['first_control_divergence'] = {
            'step': first_control, 'core_a': row_a[0], 'core_b': row_b[0],
            'pc_a': hex(row_a[1]), 'next_pc_a': hex(row_a[2]),
            'pc_b': hex(row_b[1]), 'next_pc_b': hex(row_b[2]),
            'note': 'after this step the two PC sequences are misaligned; per-step comparison beyond it '
                    'only reports that the traces differ, not where'}
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
      per-core 步数一致;
    - 控制流(核调度/PC/next_pc)289,849 步零分歧;
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
    require('first_control_divergence' not in p, 'official vs tested_b79 control flow diverged')
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rom', type=Path, required=True)
    parser.add_argument('--image', action='append',
                        help='label=path with label in {official,tested_b79,eq_fence_repair}; repeat 2-3 times')
    parser.add_argument('--pair', action='append',
                        help='labelA:labelB to compare (default: every combination)')
    parser.add_argument('--budget', type=int, default=300000)
    parser.add_argument('--self-check', action='store_true',
                        help='pin the first-round official-vs-tested_b79 conclusion with assertions '
                             '(repository images only, no private artifacts)')
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()

    if args.self_check:
        # 自检固定跑仓库内两镜像(official vs tested_b79)到前沿停点;断言钉死
        # 2026-11-12 首轮结论。上游前沿推进(probe_irq_entry 场景变化)后这些
        # 步数/PC 断言会失败,按新前沿重新钉。
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

    # 配置新鲜度自检:official 必须复现 HANDOFF 前沿停点,否则本工具的
    # flag 集已与主前沿场景漂移(或上游场景变更),结果不可信。
    if 'official' in loaded:
        check = frontier_run(module, loaded['official'][1][0], header, rom, 'frontier_freshness_check',
                             flash_image_bytes=loaded['official'][1][1])
        require(check[0]['completed_steps'] == 289849 and check[0]['stop_pc'] == '0x60679ba' and
                check[0]['stop']['kind'] == 'unmapped_memory' and check[0]['stop']['address'] == '0xf27f4b',
                'official frontier stop changed: the imported flag set no longer reproduces the '
                'crc_bankb_erased_fixture frontier (289849 @ 0x60679ba); update this pin after the '
                'frontier advances')

    report = {'simulator_commit': PIN, 'device_accessed': False, 'whole_h3s_boot_simulated': False,
              'candidate_whole_boot_validated': False, 'suitable_for_flash_approval_alone': False,
              'comparison_basis': COMPARISON_BASIS,
              'pairs': []}
    for a, b in pairs:
        print(f'== diff {a} vs {b} ==', flush=True)
        report['pairs'].append({'name': f'{a}_vs_{b}',
                                **compare_pair(module, rom, header, f'trace_{a}', loaded[a],
                                               f'trace_{b}', loaded[b], args.budget)})
    if args.self_check:
        check_self(report)
        report['image_trace_self_check_passed'] = True
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + '\n')
    print('wrote', args.report, flush=True)


if __name__ == '__main__':
    main()
