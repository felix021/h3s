#!/usr/bin/env python3
"""F2 候选 L1 双击 open/close 闭环 runner(路 F2L1)。

把 F2 候选(UFW 7e972bf0…)装载进 D1 验收观察窗世界(S19 park 稳态,window_complete
停点),接管 post_stop 槽跑两轮双击(每轮只入队一个 0x10a04):round1=open(槽
0x125574 0→stream)、round2=close(槽 stream→0)。与 D2-a 同机制、独立实现。

关键工程门槛(任务书):D2-b runner 的 build_world() 内部 application() 硬校验
sha256(raw)==OFFICIAL_SHA,不能直接把固件路径换成 F2——本 runner 复用
analyze_image_trace.load_f2() 返回的 (app, plain, readonly, sha),按 trace runner
方式传 flash_image_bytes=plain、readonly=readonly,为 F2 捕获自己的快照。

快照身份:场景名 f2_l1_acceptance_window 写入 capture_context['scenario'](restore
侧 expected_context 必须同名);候选 SHA/全明文摘要由 load_f2 的前置断言
(candidate_world_checks + CANDIDATE_PLAIN_SHAS)钉死,不进 git。

用法:
  python3 tools/sim_f2_l1.py --capture   # 全量重放(约 17min)并捕获 F2 快照
  python3 tools/sim_f2_l1.py             # 从 analysis/snapshots/f2_l1-*.bin 恢复(~30s)

诚实边界:F2 保持 UNTESTED;本 runner 不构成刷机或行为验证声明。D1 观察器是模块级
全局(不进快照 M5),restore 轮 d1 verdicts 是 fresh 观察器面(与 capture 轮不一致),
L1 闭环自身的 post_stop 观察面(分发/槽/apply 门)不受影响——如实标注。restore 用
budget=0(非 build_world 的 budget=1):d1_acceptance 的停点是观察器 window_complete
而非自然 Blocked,budget=1 会让首步 select 落到 CPU1 上、post_stop 拿不到 CPU0;
budget=0 保留恢复的 window_complete 停点且 c=CPU0,post_stop 才能做键链注入。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from probe_boot import check_rom                                            # noqa: E402
from probe_boot_handoff import package_header                               # noqa: E402
from probe_pi32v2 import upstream                                           # noqa: E402
from analyze_image_trace import load_f2, F2_SHA                             # noqa: E402
import frontier_registry                                                    # noqa: E402
import probe_irq_entry                                                      # noqa: E402

WORLD_NAME = 'f2_l1_acceptance_window'
ROM_PATH = '/home/felix021/tmp/QCY_H3S_BR28_ROM_000000-020000.bin'
OFFICIAL_PATH = ('/home/felix021/code/headsets/h3s/firmware/'
                 'QCY_H3S_7018F_1T2_release_note_V1.1.8_011.ufw')
F2_PATH = '/home/felix021/tmp/lfix2/lfiximpl2-f2.ufw'


def snapshot_path(snapshot_dir) -> Path:
    return Path(snapshot_dir) / f'f2_l1-{WORLD_NAME}.bin'


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--capture', action='store_true',
                   help='全量重放并重新捕获 F2 快照(~分钟级);默认从快照恢复(~秒级)')
    p.add_argument('--rom', type=Path, default=Path(ROM_PATH))
    p.add_argument('--official', type=Path, default=Path(OFFICIAL_PATH))
    p.add_argument('--f2', type=Path, default=Path(F2_PATH))
    p.add_argument('--snapshot-dir', default='analysis/snapshots')
    p.add_argument('--out', default='analysis/local-checks/f2l1/scene.json')
    args = p.parse_args()

    t0 = time.time()
    module = upstream()
    app, plain, readonly, sha = load_f2(args.f2, args.official)
    header = package_header(args.official)
    rom = args.rom.read_bytes()
    check_rom(rom)
    flags = frontier_registry.scenario_flags('p11_heap_experimental', probe_irq_entry)

    target = snapshot_path(args.snapshot_dir)
    Path(args.snapshot_dir).mkdir(parents=True, exist_ok=True)
    if args.capture or not target.exists():
        snapshot_cfg = {'capture': target}
    else:
        snapshot_cfg = {'restore': target,
                        'expected_context': {'scenario': WORLD_NAME},
                        'budget': 0}

    result = probe_irq_entry.irq_case(module, app, header, rom, WORLD_NAME,
                                      **flags, d1_acceptance=True, f2_l1_loop=True,
                                      flash_image_bytes=plain, readonly=readonly,
                                      snapshot=snapshot_cfg)
    payload = {'elapsed_s': round(time.time() - t0, 1),
               'candidate_ufw_sha256': sha,
               'candidate_ufw_sha256_matches_f2': sha == F2_SHA,
               'candidate_plain_sha256': hashlib.sha256(plain).hexdigest(),
               'candidate_plain_length': hex(len(plain)),
               'append_segment_runtime': hex(0x060E3053),
               'fence_upper_limit': '0x060e34da',
               'key7_window_bytes': app[0x06025404 - 0x06000100:0x06025404 - 0x06000100 + 8].hex(),
               'slot_address': '0x125574',
               'slot_boot_value': 'boot BSS zero loop [0x125320,0x12a62c)',
               'world_name': WORLD_NAME,
               'stop': result.get('stop'),
               'completed_steps': result.get('completed_steps'),
               'f2_l1_loop': result.get('f2_l1_loop'),
               'snapshot_capture': result.get('snapshot_capture'),
               'snapshot_restore': result.get('snapshot_restore')}
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=1, default=str), encoding='utf-8')

    loop = result.get('f2_l1_loop') or {}
    summary = loop.get('summary') or {}
    print(json.dumps({'elapsed_s': payload['elapsed_s'],
                      'candidate_ufw_sha256': payload['candidate_ufw_sha256'],
                      'completed_steps': payload['completed_steps'],
                      'stop': payload['stop'],
                      'f2_l1_summary': summary,
                      'rounds': [{'round': r.get('round'), 'label': r.get('label'),
                                  'pass': r.get('pass'),
                                  'end_slot': r.get('end_slot'),
                                  'checks': {k: v for k, v in (r.get('checks') or {}).items()
                                             if not v or k in (
                                                 'slot_after_open_nonzero',
                                                 'slot_after_close_zero',
                                                 'open_returned_zero',
                                                 'open_failed_arm_apply_skipped',
                                                 'open_slot_write_hit',
                                                 'close_slot_clear_hit',
                                                 'apply_mpo_called')}}
                                 for r in loop.get('rounds', [])],
                      'out': str(out)}, indent=1))


if __name__ == '__main__':
    main()
