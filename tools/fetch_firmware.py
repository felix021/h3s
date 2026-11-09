#!/usr/bin/env python3
"""Download exact release firmware bytes; never connects to headphones."""
import argparse
import json
from pathlib import Path
from artifacts import retrieve

ROOT = Path(__file__).resolve().parents[1]
p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--variant', choices=['original', 'current', 'both'], default='original')
a = p.parse_args()
m = json.loads((ROOT / 'firmware/manifest.json').read_text())
variants = list(m['assets']) if a.variant == 'both' else [a.variant]
for variant in variants:
    item = m['assets'][variant]
    url = 'https://github.com/felix021/h3s/releases/download/' + m['release_tag'] + '/' + item['filename']
    path = retrieve(url, ROOT / 'firmware' / item['filename'], item['sha256'], item['bytes'])
    print(variant, path.relative_to(ROOT), item['sha256'])
