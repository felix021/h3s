#!/usr/bin/env python3
"""Wrap the known original app.bin in ELF32-pi32v2 and run the vendor objdump."""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import subprocess

ROOT = Path(__file__).resolve().parents[1]
p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--objdump', type=Path)
p.add_argument('--elf-only', action='store_true', help='Create the wrapper locally without invoking a disassembler')
p.add_argument('--app', type=Path, default=ROOT / 'analysis/v1.1.8/unpacked/files/app.bin')
p.add_argument('--output-dir', type=Path, default=ROOT / 'analysis/anc-cycle-20261109')
a = p.parse_args()
if not a.elf_only and a.objdump is None:
    p.error('--objdump is required unless --elf-only is selected')
data = a.app.read_bytes()
sha = hashlib.sha256(data).hexdigest()
if sha != '18f3d573a5b1a15f488a6803ce4f828634e602015c85e92d205bbe62d7060041':
    raise SystemExit('Unexpected app.bin: refusing to guess load address or architecture')
base = 0x06000100
names = b'\0.text\0.shstrtab\0'
name_offset = 0x100 + len(data)
section_offset = (name_offset + len(names) + 3) & ~3
header = struct.pack('<16sHHIIIIIHHHHHH', b'\x7fELF' + bytes([1, 1, 1]) + bytes(9), 2, 0xf1, 1, base, 52, section_offset, 0, 52, 32, 1, 40, 3, 2)
program = struct.pack('<IIIIIIII', 1, 0x100, base, base, len(data), len(data), 5, 4)
elf = bytearray(header + program)
elf.extend(bytes(0x100 - len(elf)))
elf.extend(data)
elf.extend(names)
elf.extend(bytes(section_offset - len(elf)))
elf.extend(bytes(40))
elf.extend(struct.pack('<IIIIIIIIII', 1, 1, 6, base, 0x100, len(data), 0, 0, 4, 0))
elf.extend(struct.pack('<IIIIIIIIII', 7, 3, 0, 0, name_offset, len(names), 0, 0, 1, 0))
a.output_dir.mkdir(parents=True, exist_ok=True)
target = a.output_dir / 'app-linear.elf'
target.write_bytes(elf)
if not a.elf_only:
    with (a.output_dir / 'app-linear.asm').open('w') as output:
        subprocess.run([str(a.objdump.resolve()), '-d', '-arch-name=pi32v2', '-mcpu=r3', str(target.resolve())], stdout=output, check=True)
(a.output_dir / 'disassembly-provenance.json').write_text(json.dumps({'source_sha256': sha, 'elf_sha256': hashlib.sha256(elf).hexdigest(), 'load_base': hex(base), 'architecture': 'pi32v2', 'cpu': 'r3', 'disassembler_invoked': not a.elf_only, 'method': 'linear disassembly; code/data boundaries still need analysis'}, indent=2) + '\n')
print('Generated ELF wrapper' + (' only' if a.elf_only else ' and vendor disassembly') + ' in', a.output_dir)
