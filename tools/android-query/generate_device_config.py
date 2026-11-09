#!/usr/bin/env python3
"""Generate ignored target configuration; never commit real device addresses."""
import argparse
import os
from pathlib import Path
import re

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--output', type=Path, required=True)
p.add_argument('--test', action='store_true', help='Generate a synthetic host-test target')
a = p.parse_args()
address = '02:00:00:00:00:01' if a.test else os.environ.get('H3S_BLE_ADDRESS', '').upper()
if not re.fullmatch(r'(?:[0-9A-F]{2}:){5}[0-9A-F]{2}', address) or address in ('00:00:00:00:00:00', 'FF:FF:FF:FF:FF:FF'):
    raise SystemExit('Set H3S_BLE_ADDRESS explicitly to the target headset BLE address')
a.output.parent.mkdir(parents=True, exist_ok=True)
a.output.write_text('package cn.felix021.h3squery;\npublic final class DeviceConfig {\n    public static final String ADDRESS = "' + address + '";\n}\n')
