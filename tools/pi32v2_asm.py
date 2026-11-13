#!/usr/bin/env python3
"""Offline thin wrapper over the pinned community pi32v2 assembler/linker/codec.

Imports the pi32v2 toolchain already cached at tools/reference-cache/pi32v2-isa
(github.com/jvitkauskas/pi32v2-isa, pinned at commit 3608ed19... in
data/dependencies.json and probe_pi32v2.py) so the project can assemble pi32v2
text and round-trip bytes through its bidirectional codec without JieLi's
external clang toolchain.

This is an assembler integration, NOT a C compiler: assembling .S / inline text
is covered here, compiling C source still needs the pi32v2 LLVM backend (a
separate repository, out of scope). No device is opened and no firmware is
produced. See docs/pi32v2-asm-wrap.md.
"""
import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PIN = '3608ed19bc21324f8ecfe79cc0018d05e43d9563'
REPO = 'https://github.com/jvitkauskas/pi32v2-isa.git'
CACHE = ROOT / 'tools/reference-cache/pi32v2-isa'

_pkg = None
_table = None


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def fetch():
    """Clone the pinned pi32v2-isa into the ignored reference cache (offline after the one fetch)."""
    if CACHE.exists():
        return
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(['git', 'init', str(CACHE)], check=True, stdout=subprocess.DEVNULL)
    subprocess.run(['git', '-C', str(CACHE), 'remote', 'add', 'origin', REPO], check=True)
    subprocess.run(['git', '-C', str(CACHE), 'fetch', '--depth', '1', 'origin', PIN], check=True)
    subprocess.run(['git', '-C', str(CACHE), 'checkout', '--detach', 'FETCH_HEAD'], check=True)


def load():
    """Import pi32v2isa.{codec,table,asm,elf,ld,objdump} from the pinned cache.

    Returns a dict of modules keyed by name. Raises RuntimeError if the cache is
    missing (run ``python3 tools/pi32v2_asm.py --fetch``), not at the pinned
    revision, or dirty (we never build on a silently modified third-party table).
    """
    global _pkg
    if _pkg is not None:
        return _pkg
    if not CACHE.exists():
        raise RuntimeError('missing pi32v2-isa; run "python3 tools/pi32v2_asm.py --fetch" first')
    rev = subprocess.check_output(['git', '-C', str(CACHE), 'rev-parse', 'HEAD'], text=True).strip()
    require(rev == PIN, f'pi32v2-isa revision mismatch: {rev} != {PIN}')
    subprocess.run(['git', '-C', str(CACHE), 'diff', '--exit-code', PIN, '--'],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    require((CACHE / 'isa' / 'pi32v2.toml').exists(), 'pi32v2-isa instruction table missing')
    sys.path.insert(0, str(CACHE))
    import pi32v2isa.codec as codec
    import pi32v2isa.table as table
    import pi32v2isa.asm as asm
    import pi32v2isa.elf as elf
    import pi32v2isa.ld as ld
    import pi32v2isa.objdump as objdump
    # The table resolves isa/pi32v2.toml from its own file location; assert we
    # really imported the pinned copy and not some other checkout.
    require(str(table.ROOT.resolve()) == str(CACHE.resolve()), 'pi32v2isa imported from an unexpected location')
    _pkg = {'codec': codec, 'table': table, 'asm': asm, 'elf': elf, 'ld': ld, 'objdump': objdump}
    return _pkg


def load_table():
    """The loaded instruction table (1165 forms, single source of truth), cached."""
    global _table
    if _table is None:
        _table = load()['table'].load()
    return _table


def assemble_text(text, filename='<input>', *, include=(), features=None, chip=None, chip_errors=False):
    """Assemble preprocessed source text into an ELF relocatable object (bytes).

    Pure Python: no external cpp/toolchain. Text is expected to already be
    preprocessed (hooks.S has no cpp directives and assembles as-is).
    """
    return load()['asm'].assemble_source(text, filename, tuple(include), features=features,
                                         chip=chip, chip_errors=chip_errors)


def assemble_file(path, **kwargs):
    """Assemble a source file (read as plain text, no cpp)."""
    path = Path(path)
    return assemble_text(path.read_text(), str(path), **kwargs)


def read_object(data):
    """Parse ELF object bytes into an elf.Obj."""
    return load()['elf'].read(data)


def code_sections(obj):
    """Executable, loaded sections of an object as [(name, bytes, relocs)]."""
    elf = load()['elf']
    out = []
    for s in obj.sections:
        if s.flags & elf.SHF_EXECINSTR and s.type == elf.SHT_PROGBITS and s.data:
            out.append((s.name, bytes(s.data), list(s.relocs)))
    return out


def disassemble(data, base=0):
    """Yield (address, insn_id, formatted text, raw bytes) per instruction.

    insn_id is None when the word has no table entry (only possible for data or
    a genuinely unknown word; the pinned table covers the whole decodable ISA).
    """
    pkg = load()
    table = load_table()
    offset = 0
    while offset < len(data):
        got = pkg['codec'].decode(table, data, offset)
        if got is None:
            n = min(2, len(data) - offset)
            yield base + offset, None, '.short 0x' + data[offset:offset + n][::-1].hex(), data[offset:offset + n]
            offset += n
        else:
            insn, values, n = got
            yield base + offset, insn.id, pkg['codec'].format(insn, values), data[offset:offset + n]
            offset += n


def encode(insn_id, **raw_values):
    """Encode one instruction form by id with raw field values -> bytes."""
    return load()['codec'].encode(load_table().by_id(insn_id), raw_values)


def decode(data, offset=0):
    """Decode one instruction at data[offset:] -> (insn, raw values, length) or None."""
    return load()['codec'].decode(load_table(), data, offset)


def roundtrip_one(insn_id, raw_values):
    """encode -> decode and assert the same form and raw fields come back.

    Returns the encoded bytes. This is the "assembler is trustworthy" closure:
    the codec is bidirectional over the same table, so a semantic write equals a
    semantic read back.
    """
    data = encode(insn_id, **raw_values)
    got = decode(data, 0)
    require(got is not None, f'{insn_id}: encoded bytes did not decode')
    insn, values, length = got
    require(insn.id == insn_id, f'{insn_id}: round-trip decoded as {insn.id}')
    require(values == raw_values, f'{insn_id}: round-trip fields differ: {values} != {raw_values}')
    require(length == len(data), f'{insn_id}: round-trip length {length} != {len(data)}')
    return data


# --- command line ----------------------------------------------------------------------------------

def main(argv=None):
    p = argparse.ArgumentParser(prog='pi32v2_asm',
                                description='Offline thin wrapper over the pinned pi32v2-isa assembler/codec.')
    p.add_argument('--fetch', action='store_true', help='clone the pinned pi32v2-isa into the reference cache')
    p.add_argument('--check', action='store_true', help='load and validate the instruction table')
    sub = p.add_subparsers(dest='cmd')
    a = sub.add_parser('asm', help='assemble a pi32v2 source file (plain text, no cpp) into an ELF object')
    a.add_argument('input', help='source file, or - for stdin')
    a.add_argument('-o', '--output', help='object file to write (default: input name with .o)')
    o = sub.add_parser('objdump', help='disassemble an ELF object produced by `asm`')
    o.add_argument('input', help='object file')
    o.add_argument('--base', type=lambda v: int(v, 0), default=0, help='address of the first byte')
    args = p.parse_args(argv)

    if args.fetch:
        fetch()
        rev = subprocess.check_output(['git', '-C', str(CACHE), 'rev-parse', 'HEAD'], text=True).strip()
        print(f'pi32v2-isa ready at {rev}')
        return 0

    if args.check:
        table = load_table()
        errors = load()['table'].check(table)
        for e in errors:
            print(e, file=sys.stderr)
        print(f'{len(table.insns)} entries, {len(errors)} problems')
        return 1 if errors else 0

    if args.cmd == 'asm':
        path = args.input
        text = sys.stdin.read() if path == '-' else Path(path).read_text()
        data = assemble_text(text, '<stdin>' if path == '-' else path)
        out = args.output or (Path(path).stem + '.o' if path != '-' else 'a.o')
        Path(out).write_bytes(data)
        print(f'{out}: {len(data)} bytes')
        return 0

    if args.cmd == 'objdump':
        obj = read_object(Path(args.input).read_bytes())
        for name, data, relocs in code_sections(obj):
            print(f'  section {name}: {len(data)} bytes, {len(relocs)} relocations')
            for addr, insn_id, text, raw in disassemble(data, args.base):
                marker = '??' if insn_id is None else insn_id
                print(f'{addr:8x}: {raw.hex(" "):<12} {marker:<28} {text}')
            for off, rtype, sym, addend in relocs:
                print(f'    relocation @{off}: {sym}{addend:+d}')
        return 0

    p.print_help()
    return 2


if __name__ == '__main__':
    sys.exit(main())
