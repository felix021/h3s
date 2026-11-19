#!/usr/bin/env python3
"""重取 in-tree 22 键 adapter 计数基线(条件场景 p11_heap_experimental_config)。

probe_irq_entry.check() 的 23 个 adapter 计数断言不再钉绝对值,改为读
evidence/p11_heap_adapter_count_baseline.json。前沿推进导致计数漂移时,
用本工具重跑条件场景并显式更新基线:

    python3 tools/capture_p11_heap_adapter_counts.py --recapture \
        --rom /path/to/QCY_H3S_BR28_ROM_000000-020000.bin

不带 --recapture 只打印当前基线(只读,不启动 41.8M 步仿真)。--recapture
表示「接受本次结果为新基线」,不隐含重跑语义之外的任何校验(行为验证仍由
probe_irq_entry 完整断言 + assert_preflight 负责)。
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from probe_pi32v2 import ROOT, upstream  # noqa: E402
from probe_boot import application, flash_image, check_rom  # noqa: E402
from probe_boot_handoff import package_header  # noqa: E402
from frontier_registry import scenario_flags  # noqa: E402
import probe_irq_entry  # noqa: E402

BASELINE = ROOT / 'evidence/p11_heap_adapter_count_baseline.json'
SCENARIO = 'p11_heap_experimental_config'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--recapture', action='store_true',
                        help='re-run the 41.8M-step conditional scenario and rewrite the baseline')
    parser.add_argument('--rom', type=Path,
                        help='private BR28 ROM dump (required with --recapture)')
    parser.add_argument('--firmware', type=Path,
                        default=ROOT / 'firmware/QCY_H3S_7018F_1T2_release_note_V1.1.8_011.ufw')
    args = parser.parse_args()

    if not args.recapture:
        if BASELINE.is_file():
            print(BASELINE.read_text())
        else:
            print(f'no baseline at {BASELINE}')
        print('pass --recapture --rom <ROM> to re-run the scenario and rewrite the baseline')
        return 0

    if args.rom is None:
        parser.error('--recapture requires --rom (private ROM dump)')

    module = upstream()
    firmware = args.firmware
    app = application(firmware)
    header = package_header(firmware)
    image = flash_image(firmware)
    rom = args.rom.read_bytes()
    check_rom(rom)
    t0 = time.time()
    result = probe_irq_entry.irq_case(
        module, app, header, rom, SCENARIO,
        **scenario_flags('p11_heap_experimental', probe_irq_entry),
        flash_image_bytes=image)
    counts = {key: result['instruction_adapters'].get(key)
              for key in probe_irq_entry._ADAPTER_COUNT_KEYS}
    baseline = {'scenario': SCENARIO,
                'completed_steps': result['completed_steps'],
                'stop_pc': result['stop_pc'],
                'note': '23 个 instruction_adapters 计数基线(条件场景前缀)。推进前沿后计数漂移时用 '
                        'tools/capture_p11_heap_adapter_counts.py --recapture 重取;断言见 '
                        'tools/probe_irq_entry.py check()。',
                'counts': counts}
    BASELINE.write_text(json.dumps(baseline, indent=2) + '\n')
    print(f'elapsed {time.time() - t0:.0f}s')
    print(json.dumps(baseline, indent=2, default=str))
    return 0


if __name__ == '__main__':
    sys.exit(main())
