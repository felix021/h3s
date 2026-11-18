#!/usr/bin/env python3
"""Generate ignored target configuration; never commit real device addresses.

路 BLEDISC（2026-11-18）起地址默认留空=App 扫描广播名自动发现（QCY 官方 App 先例，见
docs/android-ble-connection-diagnosis-20261118.md）。H3S_BLE_ADDRESS 仍可注入，作为
扫描时的优先地址（扫到即连）。"""
import argparse
import os
from pathlib import Path
import re

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--output', type=Path, required=True)
p.add_argument('--test', action='store_true', help='Generate a synthetic host-test target')
a = p.parse_args()
address = '' if a.test else os.environ.get('H3S_BLE_ADDRESS', '').upper()
if address and (not re.fullmatch(r'(?:[0-9A-F]{2}:){5}[0-9A-F]{2}', address) or address in ('00:00:00:00:00:00', 'FF:FF:FF:FF:FF:FF')):
    raise SystemExit('H3S_BLE_ADDRESS must be a valid BLE address (AA:BB:CC:DD:EE:FF); unset it to use scan discovery')
name = os.environ.get('H3S_BLE_NAME', 'QCY H3S')
match = os.environ.get('H3S_BLE_NAME_MATCH', 'prefix')
if match not in ('prefix', 'exact'):
    raise SystemExit('H3S_BLE_NAME_MATCH must be "prefix" or "exact"')
a.output.parent.mkdir(parents=True, exist_ok=True)
a.output.write_text(
    'package cn.felix021.h3squery;\npublic final class DeviceConfig {\n'
    '    /** 可选优先地址：空=扫描发现模式（按 NAME 匹配广播）。 */\n'
    '    public static final String ADDRESS = "' + address + '";\n'
    '    /** 目标广播名。 */\n'
    '    public static final String NAME = "' + name + '";\n'
    '    /** 名字匹配模式：prefix|exact。 */\n'
    '    public static final String NAME_MATCH = "' + match + '";\n}\n')
