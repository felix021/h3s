#!/usr/bin/env python3
"""Collect the query app's JSONL logs without root; never transmits Bluetooth commands."""
import argparse
from pathlib import Path
import re
import subprocess

p = argparse.ArgumentParser(description=__doc__)
p.add_argument("--serial", required=True)
p.add_argument("--output", required=True, type=Path)
a = p.parse_args()
base = ["adb", "-s", a.serial, "exec-out", "run-as", "cn.felix021.h3squery"]
names = subprocess.check_output(base + ["ls", "-1", "files"], text=True).splitlines()
a.output.mkdir(parents=True, exist_ok=True)
for name in names:
    if re.fullmatch(r"(?:query|upgrade)-[0-9]+\.jsonl", name):
        data = subprocess.check_output(base + ["cat", "files/" + name])
        (a.output / name).write_bytes(data)
        print(name, len(data))
