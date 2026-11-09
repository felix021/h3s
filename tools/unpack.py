#!/usr/bin/env python3
"""Reproduce H3S official firmware extraction from a hash-pinned source image."""
import argparse
import hashlib
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--source', type=Path, default=ROOT / 'firmware/QCY_H3S_7018F_1T2_release_note_V1.1.8_011.ufw')
p.add_argument('--output-dir', type=Path, default=ROOT / 'analysis/v1.1.8')
a = p.parse_args()
source = a.source.resolve()
if hashlib.sha256(source.read_bytes()).hexdigest() != '65cafc7f9a7545c5c30701d141c77db334e37065589e42c51306132c94c879af':
    raise SystemExit('Only the exact official H3S 0.1.1.8 image is supported')
upstream = ROOT / 'tools/jl-misctools/firmware/fwunpack_newfw.py'
if not upstream.is_file():
    raise SystemExit('Run git submodule update --init --recursive first')
out = a.output_dir.resolve()
out.mkdir(parents=True, exist_ok=False)
subprocess.run([sys.executable, str(upstream), '--dirname', str(out / 'unpacked'), str(source)], cwd=ROOT, check=True)
subprocess.run([sys.executable, str(ROOT / 'tools/extract_ufw_resources.py'), str(source), '--unpacked', str(out / 'unpacked'), '--output', str(out / 'resources')], cwd=ROOT, check=True)
for name in ['outer', 'tones']:
    shutil.move(str(out / 'resources' / name), str(out / name))
(out / 'resources').rmdir()
print('Extracted and verified:', out)
