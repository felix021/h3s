#!/usr/bin/env python3
"""整段搬 Go 可行性前置测量:真实 frontier 上「满足整段搬移安全条件」的
连续尝试段长度分布、覆盖率、退出原因、CPU 时间占比。

纯只读:观察模式由 probe_boot.gosim_observer 承载(默认 None=零开销零行为变化,
H3S_GOSIM_OBSERVE 环境变量开启)。本脚本驱动单场景一次运行,输出:
  --report PATH         result 报告 JSON(sort_keys,与主流程逐字节同口径)
  --observer-out PATH   (观察开启时)gosim_observer.summary() JSON
  --trace-digest PATH   (可选)逐条 trace 行的 SHA256(repr) 摘要

字节同一性证明(同一进程内两跑):--self-check-byte 时先关观察器跑一遍、再开
观察器跑一遍,断言 report 与 trace 逐字节一致,再打印 PASS/FAIL。
"""
import argparse
import hashlib
import json
from pathlib import Path

import probe_boot
import probe_boot_handoff
from probe_pi32v2 import ROOT, require, upstream

import analyze_image_trace as ait
import gosim_observe


def _digest_trace(trace):
    h = hashlib.sha256()
    n = 0
    for row in trace:
        h.update(repr(row).encode('utf-8'))
        n += 1
    return h.hexdigest(), n


def run_scenario(module, app, plain, readonly, header, rom, scenario, budget, observer):
    """observer: None(关) or a GosimObserver(开)。返回 (result, trace)。"""
    probe_boot.gosim_observer = observer
    if observer is not None:
        observer.reset()
    result, trace = ait.frontier_run(
        module, app, header, rom, 'trace_official',
        flash_image_bytes=plain, readonly=readonly,
        budget=budget, scenario=scenario)
    return result, trace


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--rom', type=Path, required=True)
    ap.add_argument('--scenario', choices=sorted(ait.SCENARIOS), default='crc_bankb')
    ap.add_argument('--budget', type=int, default=None,
                    help='default: scenario default_budget')
    ap.add_argument('--report', type=Path, required=True)
    ap.add_argument('--observer-out', type=Path, default=None)
    ap.add_argument('--trace-digest', type=Path, default=None)
    ap.add_argument('--observe', action='store_true',
                    help='force observation on (else env H3S_GOSIM_OBSERVE)')
    ap.add_argument('--no-observe', action='store_true',
                    help='force observation off (else env H3S_GOSIM_OBSERVE)')
    ap.add_argument('--self-check-byte', action='store_true',
                    help='run off+on and assert report/trace byte-identical')
    args = ap.parse_args()

    rom = args.rom.read_bytes()
    probe_boot.check_rom(rom)
    scenario = args.scenario
    budget = args.budget if args.budget is not None else ait.SCENARIOS[scenario]['default_budget']

    observe = gosim_observe.enabled()
    if args.observe:
        observe = True
    if args.no_observe:
        observe = False

    module = upstream()
    official_path = ait.OFFICIAL_DEFAULT
    header = probe_boot_handoff.package_header(official_path)
    app, plain, readonly, _sha = ait.load_official(official_path)

    if args.self_check_byte:
        result_off, trace_off = run_scenario(module, app, plain, readonly, header, rom, scenario, budget, None)
        result_on, trace_on = run_scenario(module, app, plain, readonly, header, rom, scenario, budget,
                                           gosim_observe.GosimObserver())
        report_same = json.dumps(result_off, sort_keys=True) == json.dumps(result_on, sort_keys=True)
        dig_off, n_off = _digest_trace(trace_off)
        dig_on, n_on = _digest_trace(trace_on)
        trace_same = (n_off == n_on and dig_off == dig_on)
        print('byte-identity: report_same=%s trace_same=%s (trace_rows=%d)' %
              (report_same, trace_same, n_off), flush=True)
        print('PASS' if (report_same and trace_same) else 'FAIL', flush=True)
        if args.report:
            args.report.write_text(json.dumps(result_off, sort_keys=True))
        return 0 if (report_same and trace_same) else 1

    observer = gosim_observe.GosimObserver() if observe else None
    result, trace = run_scenario(module, app, plain, readonly, header, rom, scenario, budget, observer)

    report_text = json.dumps(result, sort_keys=True)
    args.report.write_text(report_text)

    if observer is not None and args.observer_out:
        summary = observer.summary()
        summary['scenario'] = scenario
        summary['budget'] = budget
        summary['completed_steps'] = result.get('completed_steps')
        summary['stop'] = result.get('stop')
        args.observer_out.write_text(json.dumps(summary, sort_keys=True))

    if args.trace_digest:
        dig, n = _digest_trace(trace)
        args.trace_digest.write_text(json.dumps({'sha256': dig, 'rows': n}))

    print('scenario=%s budget=%d completed_steps=%s stop=%s observe=%s' %
          (scenario, budget, result.get('completed_steps'), result.get('stop'), observe),
          flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
