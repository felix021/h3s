#!/usr/bin/env python3
"""M3 刷机流的宿主离线计划器（docs/sim-m3-flash-spec.md §3.4）。

目标镜像 + 当前 fixture（=server --flash-image 同源）→ 扇区计划 plan.json，供验收构造
预期与 usb_arm 输入：

  {"sectors":[4KiB 基址…（升序）], "target_sha256":…, "base_sha256":…,
   "preserved":[[0,S_min],[S_max,2MiB]]}        # 计划外全部保留（image.validate_delta 同款纪律）

规则：diff(base,target) → 变化字节所属 4KiB 扇区集合；空计划拒绝（target==base 无可刷）；
写完模拟（计划扇区置 target 切片）必须精确重现 target（diff 只落在计划扇区内，机检兜底）。
预期镜像独立性（§3.4 codex 纪律）：期望值在本层离线算出，server 只执行不自评——
target_sha256 是断言输入，不是 server 给自己打分的产物。nonce 由调用方生成（app 侧 UUID
先例），planner 不发明。

「真实 UFW 级 bank 恢复」的可选 --ufw 模式（boot-info 序号/CRC/长度更新，复用
tools/usb-recovery/image.py prepare 语义）不在本版：其输入为实测坏档（私有资产、仓内不可
构造），M3 验收用合成目标覆盖同构性（规格 §3.4 注）；待有实锚再列，不发明。

CLI：
  python3 tools/sim_restore_planner.py --base analysis/sim-usb/flash-2mib.bin \
      --target analysis/sim-usb/target-2mib.bin --plan analysis/sim-usb/plan.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / 'usb-recovery'))
import image                                              # SIZE=0x200000 / SECTOR=4096 同源钉死


def plan_restore(base: bytes, target: bytes) -> dict:
    """base/target 两张 2MiB 镜像 → 扇区计划（纯函数，单测用）。

    空 diff / 长度不符 / 计划无法精确重现 target → ValueError。
    """
    if len(base) != image.SIZE or len(target) != image.SIZE:
        raise ValueError('base/target 须各为 2MiB 全片镜像（got %dB/%dB）' % (len(base), len(target)))
    sectors = sorted({i // image.SECTOR * image.SECTOR
                      for i, (a, b) in enumerate(zip(base, target)) if a != b})
    if not sectors:
        raise ValueError('空计划：target 与 base 逐字节相同（无可刷扇区）')
    written = bytearray(base)                             # 写完模拟：计划扇区置 target 切片
    for base_addr in sectors:
        written[base_addr:base_addr + image.SECTOR] = target[base_addr:base_addr + image.SECTOR]
    if bytes(written) != target:
        raise ValueError('计划扇区集无法精确重现 target（diff 外溢）')   # 构造上恒假，机检兜底
    return {
        'sectors': sectors,
        'target_sha256': hashlib.sha256(target).hexdigest(),
        'base_sha256': hashlib.sha256(base).hexdigest(),
        'preserved': [[0, sectors[0]], [sectors[-1] + image.SECTOR, image.SIZE]],
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--base', type=Path, required=True,
                        help='2MiB 当前 fixture（server --flash-image 同源）')
    parser.add_argument('--target', type=Path, required=True, help='2MiB 目标镜像')
    parser.add_argument('--plan', type=Path, required=True, help='计划 JSON 输出路径')
    args = parser.parse_args(argv)
    plan = plan_restore(args.base.read_bytes(), args.target.read_bytes())
    args.plan.parent.mkdir(parents=True, exist_ok=True)
    args.plan.write_text(json.dumps(plan, indent=2) + '\n', encoding='utf-8')
    print('plan: %s' % args.plan)
    print('  sectors: %s' % [hex(s) for s in plan['sectors']])
    print('  base_sha256:    %s' % plan['base_sha256'])
    print('  target_sha256:  %s' % plan['target_sha256'])
    print('  preserved: %s' % ([[hex(a), hex(b)] for a, b in plan['preserved']]))
    return 0


if __name__ == '__main__':
    sys.exit(main())
