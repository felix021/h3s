#!/usr/bin/env python3
"""M2 USB 只读通道的 2MiB raw flash fixture 构造器（docs/sim-bridge-design.md §4.4「flash 后端输入」）。

UFW 容器 ≠ 2MiB 物理镜像（codex 修正）：本工具把公开工件按 usb-recovery.md §实际物理布局
**显式列地址映射**拼成 2MiB raw 镜像，缺公开工件的区填擦除态 0xff 并在报告里逐区标注——
不臆造字节：

  [0x000000, 0x0e6000)  官方 UFW 的 flash.bin 逐字节（942080B=0xe6000，SHA 钉死
                        image.GOOD_FLASH_SHA；image.good_flash() 同一解析+校验）——含引导区
                        0..0x1fff、bank A boot-info 0x2000..0x201f、bank A 应用密文 0x2020 起
                        929875B（至 0x0e5073）
  [0x0e6000, 0x200000)  擦除态 0xff（**显式合成标注**）——bank B boot-info/应用密文（0x0ee000..）、
                        VM（0x1da000..0x1ddfff）、BTIF、TON/TON1（0x1e1000/0x1ed000）、ANCIF/ANCIF1
                        （0x1fe000/0x1fe100）均无公开工件，真实设备这些区有内容；M2 只读通道
                        语义不受影响，对拍真机/恢复流不在范围

FD07 内存窗后端 = 公开 CPU ROM dump（rom-read.md 发布件，SHA 钉死；--fetch-rom 另取）。

CLI：
  python3 tools/sim_flash_fixture.py --output analysis/sim-usb/flash-2mib.bin \
      [--fetch-rom analysis/sim-usb/rom-window.bin]
输入 UFW 按 firmware/manifest.json 的 current 资产口径经 artifacts.retrieve 拉取
（SHA/长度双校验，已在 firmware/ 时直接复用，不重复下载）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / 'usb-recovery'))
import artifacts
import image

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / 'analysis/sim-usb/flash-2mib.bin'
DEFAULT_ROM_OUTPUT = ROOT / 'analysis/sim-usb/rom-window.bin'
ROM_URL = ('https://github.com/felix021/h3s/releases/download/firmware/'
           'QCY_H3S_BR28_ROM_000000-020000.bin')
ROM_SHA256 = '7cae0a02e3ab3755c1e392f03c89222dd701065ec9bd3ec8ac109a7b3f40f793'
ROM_BYTES = 0x20000

# usb-recovery.md §实际物理布局 的命名区（报告里逐区标注来源；地址/长度钉自该表）。
NAMED_REGIONS = (
    (0x000000, 0x002000, '引导区（保留）'),
    (0x002000, 0x002020, 'bank A boot-info'),
    (0x002020, 0x002020 + 929875, 'bank A 应用密文（929875B）'),
    (0x0ee000, 0x0ee020, 'bank B boot-info'),
    (0x0ee020, 0x0ee020 + 930245, 'bank B 应用密文（930245B，实机故障 r2——无公开工件）'),
    (0x1da000, 0x1de000, 'VM 设备运行配置'),
    (0x1e1000, 0x1ed000, 'TON 保留区'),
    (0x1ed000, 0x1fe000, 'TON1 及 BTIF 等保留区'),
    (0x1fe000, 0x1fe100, 'ANCIF 校准'),
    (0x1fe100, 0x200000, 'ANCIF1 校准'),
)


def fetch_ufw(root: Path = ROOT) -> Path:
    """按 manifest 的 current 资产拉取官方 UFW（SHA/长度双校验；已存在则复用）。"""
    manifest = json.loads((root / 'firmware/manifest.json').read_text(encoding='utf-8'))
    item = manifest['assets']['current']
    url = 'https://github.com/felix021/h3s/releases/download/%s/%s' % (
        manifest['release_tag'], item['filename'])
    return artifacts.retrieve(url, root / 'firmware' / item['filename'],
                              item['sha256'], item['bytes'])


def fetch_rom(output: Path = DEFAULT_ROM_OUTPUT) -> Path:
    """拉取公开 CPU ROM dump（rom-read.md 发布件；FD07 后端输入）。"""
    return artifacts.retrieve(ROM_URL, output, ROM_SHA256, ROM_BYTES)


def assemble(flash_bin: bytes) -> tuple[bytes, dict]:
    """flash.bin（UFW 内 942080B）→ 2MiB fixture + 来源报告。纯函数（单测用）。"""
    if len(flash_bin) > image.SIZE:
        raise ValueError('flash.bin 超出 2MiB 物理镜像: %dB' % len(flash_bin))
    fixture = bytearray(b'\xff' * image.SIZE)
    fixture[:len(flash_bin)] = flash_bin
    report = {
        'profile': 'h3s-sim-usb-flash-fixture-v1',
        'bytes': image.SIZE,
        'sha256': hashlib.sha256(fixture).hexdigest(),
        'regions': [
            {'start': 0, 'end_exclusive': len(flash_bin),
             'source': 'firmware/H3S_0.1.1.8_ANC_ON_TRANSPARENT.ufw 内 flash.bin（逐字节，'
                       'SHA256=' + image.GOOD_FLASH_SHA + '）'},
            {'start': len(flash_bin), 'end_exclusive': image.SIZE,
             'source': '擦除态 0xff（合成填充——bank B/VM/BTIF/TON/ANCIF 无公开工件，显式标注）'},
        ],
        'named_regions': [
            {'start': s, 'end_exclusive': e, 'name': n,
             'content': 'ufw-flash.bin' if e <= len(flash_bin) else 'synthetic-erased-ff'}
            for s, e, n in NAMED_REGIONS
        ],
        'read_only': True,
    }
    return bytes(fixture), report


def build(ufw: bytes) -> tuple[bytes, dict]:
    """UFW 容器字节 → 2MiB fixture + 报告（good_flash 做 SHA/结构校验，非受检资产报错）。"""
    flash_bin = image.good_flash(ufw)
    return assemble(flash_bin)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--output', type=Path, default=DEFAULT_OUTPUT,
                        help='2MiB fixture 输出路径（默认 analysis/sim-usb/flash-2mib.bin）')
    parser.add_argument('--report', type=Path, default=None,
                        help='来源报告 JSON 输出路径（默认 <output>.report.json）')
    parser.add_argument('--fetch-rom', nargs='?', type=Path, const=DEFAULT_ROM_OUTPUT, default=None,
                        help='同时拉取 FD07 后端的公开 ROM dump（可带路径，默认 analysis/sim-usb/rom-window.bin）')
    args = parser.parse_args(argv)
    ufw_path = fetch_ufw()
    print('UFW: %s' % ufw_path)
    fixture, report = build(ufw_path.read_bytes())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(fixture)
    report_path = args.report if args.report else args.output.with_suffix('.output.report.json')
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    print('fixture: %s (%dB sha256=%s)' % (args.output, len(fixture), report['sha256']))
    print('report: %s' % report_path)
    if args.fetch_rom is not None:
        rom_path = fetch_rom(args.fetch_rom)
        print('rom: %s (sha256=%s)' % (rom_path, ROM_SHA256))
    return 0


if __name__ == '__main__':
    sys.exit(main())
