#!/usr/bin/env python3
"""Check bounded CPU1 bootstrap/cache anchors in the exact private CPU0 ROM.

Static analysis only: no device access, CPU execution, ROM download or raw-byte
output. The report does not contain the input path or a full disassembly.
"""
import argparse
import hashlib
import json
from pathlib import Path

from probe_boot import ROM_SHA, SDK_PIN, check_rom
from probe_pi32v2 import PIN, require, upstream


REGIONS = (
    (0x0, 0x4, 'bd0fd76c0a4f06376ea3b0c7f96b9e7469b91f61a4a5782ab1432b13b78564f5'),
    (0xa120, 0xa15c, '31adb62dd67b530c0aee4725cc6b01be1fb835773aea848c8027f750a6978efb'),
    (0xc5f0, 0xc606, 'f3ad82f53a9116d49bf0687a71a2887109d5bd49820380763a89b620e6744e04'),
    (0xbac0, 0xbb20, '8a58916477de2e621ed89936a6eb75290c64a200505abaa6294ea7476ffe5e75'),
    (0xbb20, 0xbb30, 'eb6ebda4f4949503554ac7f266c31f42727b50bc0bfd9b973370d12ac602cfec'),
    (0xbb30, 0xbb70, 'a548204ef2794b73d30eb9379f66198e29bc9095635938c6f70982e33dc40349'),
    (0xc792, 0xc7b0, 'e33f61812d910490043a38b19348b4d9a770f04cf0a5a3e9d991c02dca9a3a41'),
    (0x1fdb4, 0x1fdc4, '96f6d567fe2aa27393ad6c4f35f80fe894cb5b2417ed137fb8fbd5117e25eb24'),
)


def analyze(module, rom):
    check_rom(rom)
    regions = []
    for start, end, expected in REGIONS:
        digest = hashlib.sha256(rom[start:end]).hexdigest()
        require(digest == expected, f'CPU1 ROM window digest changed at {start:#x}')
        regions.append({'start': hex(start), 'end_exclusive': hex(end), 'sha256': digest})
    table = module.table.load()
    anchors = []

    def anchor(pc, identity, operands, target=None):
        got = module.codec.decode(table, rom[pc:pc + 6])
        require(got is not None, f'unknown CPU1 ROM anchor at {pc:#x}')
        insn, raw, size = got
        actual = {k: v for k, v in module.check.operands(insn, raw).items() if k != 'raw'}
        require(insn.id == identity and actual == operands,
                f'CPU1 ROM instruction contract changed at {pc:#x}')
        require(any(start <= pc and pc + size <= end for start, end, _ in REGIONS),
                'CPU1 ROM anchor outside pinned windows')
        row = {'pc': hex(pc), 'instruction_id': insn.id, 'size': size}
        field = module.branch_field(insn)
        if target is not None:
            require(field is not None and pc + size + actual[field] == target,
                    f'CPU1 ROM control flow changed at {pc:#x}')
            row['target'] = hex(target)
        else:
            require(field is None, f'unchecked CPU1 ROM branch at {pc:#x}')
        anchors.append(row)

    anchor(0, 'goto.l', {'n': 41244}, 0xa120)
    for index in range(8):
        anchor(0xa120 + index * 2, 'li.zero_pair', {'p': index * 2})
    for pc, special in ((0xa130, 5), (0xa134, 1), (0xa138, 2), (0xa13c, 3), (0xa140, 0)):
        anchor(pc, 'mov.sr_r', {'r': 0, 's': special})
    anchor(0xa144, 'mov.r_sr', {'r': 0, 's': 6})
    anchor(0xa148, 'beqz', {'r': 0, 'n': 18}, 0xa15c)
    anchor(0xa14a, 'li.sr_u32', {'s': 13, 'n': 0x19fd94})
    anchor(0xa150, 'mov.sp_ssp', {})
    anchor(0xa152, 'li.sr_u32', {'s': 12, 'n': 0x19fda4})
    anchor(0xa158, 'goto.l', {'n': 9364}, 0xc5f0)
    anchor(0xc5f0, 'mov.r_sr', {'r': 0, 's': 6})
    anchor(0xc5f4, 'rsub.modimm.rri', {'r': 0, 'n': 63, 's': 0})
    anchor(0xc5f8, 'li.u32', {'r': 1, 'n': 0x19fe00})
    anchor(0xc5fe, 'ld.w.idxs', {'r': 0, 's': 1, 't': 0})
    anchor(0xc602, 'call.r', {'x': 0})
    anchor(0xc604, 'goto.s', {'n': -2}, 0xc604)
    bootstrap_count = len(anchors)
    require(bootstrap_count == 26, 'CPU1 bootstrap anchor coverage changed')

    # These SDK-named exports are checked against the acquired ROM itself.
    for pc, offset, target in ((0x1fdb4, -79398, 0xc792), (0x1fdb8, -82588, 0xbb20),
                               (0x1fdbc, -82656, 0xbae0), (0x1fdc0, -82580, 0xbb30)):
        anchor(pc, 'goto.l', {'n': offset}, target)
    anchor(0xbae2, 'mov.r_sr', {'r': 0, 's': 6})
    anchor(0xbae6, 'cli.r', {'x': 3})
    anchor(0xbae8, 'li.u32', {'r': 1, 'n': 0x19fd78})
    anchor(0xbaee, 'testset.b', {'x': 1})
    anchor(0xbafc, 'li.u32', {'r': 4, 'n': 0xca000010})
    anchor(0xbb02, 'st.w.idxs', {'r': 1, 's': 2, 't': 4})
    anchor(0xbaca, 'ld.w.idxs', {'r': 2, 's': 0, 't': 1})
    anchor(0xbace, 'bne.and.ri', {'r': 2, 'n': 1 << 30, 'm': -8}, 0xbaca)
    anchor(0xbadc, 'st.b', {'d': 1, 'b': 0, 'n': 0})
    anchor(0xbb0a, 'sti.r', {'x': 3})
    anchor(0xbb1a, 'rmw.or.modimm', {'r': 0, 'n': 0, 'm': 1})
    anchor(0xbb32, 'mov.r_sr', {'r': 0, 's': 6})
    anchor(0xbb36, 'bnez', {'r': 0, 'n': 54}, 0xbb6e)
    anchor(0xbb6e, 'pop.pc', {})
    anchor(0xbb28, 'ld.w', {'d': 1, 'b': 0, 'n': 0})
    anchor(0xc7a0, 'ld.w.idxs', {'r': 3, 's': 2, 't': 1})

    return {
        'format_version': 1, 'rom_sha256': ROM_SHA, 'rom_bytes': len(rom),
        'decoder_commit': PIN, 'reference_sdk_commit': SDK_PIN,
        'static_only': True, 'device_accessed': False, 'cpu_executed': False,
        'scope': 'Bounded ROM bootstrap and cache anchors; not a hardware release or timing model',
        'regions': regions, 'bootstrap_anchors': anchors[:bootstrap_count],
        'cache_anchors': anchors[bootstrap_count:],
        'cpu1_path': {
            'conditional_start_pc': '0x0', 'explicit_cnum_input': 1,
            'entry_pointer_formula': '0x19fe00 + (0x3f - CNUM) * 4',
            'entry_pointer_address': hex(0x19fe00 + (0x3f - 1) * 4),
            'indirect_call_pc': '0xc602', 'return_pc': '0xc604',
            'instructions_through_indirect_call': 25,
            'ssp_written': '0x19fd94', 'sp_written': 'SSP', 'usp_written': '0x19fda4',
            'cleared_gprs': 'R0..R15 before CNUM selection',
            'cleared_special': ['PSR', 'RETE', 'RETX', 'RETS', 'RETI'],
            'icfg': 'not read or written on this bootstrap path; initial value unknown',
        },
        'cpu1_cache_path': {
            'icu_export': '0x1fdbc', 'icu_implementation': '0xbae0',
            'shared_ram_lock': '0x19fd78', 'icu_command_address': '0xffec10',
            'icu_command': '0xca000010', 'icu_con_address': '0xffec00',
            'command_wait': 'poll bit30 until clear; separate from idle bit31',
            'enable': 'OR bit0 after command wait and software unlock',
            'dcu_export': '0x1fdc0', 'dcu_implementation': '0xbb30',
            'dcu_nonzero_cnum_branch': '0xbb36 -> 0xbb6e; push/pop RETS only',
            'idle_helpers': {'icu': '0x1fdb4 -> 0xc792; both core CON bit31',
                             'dcu': '0x1fdb8 -> 0xbb20; shared L1P CON 0xff0500 bit31'},
        },
        'unproved': [
            'CPU1 release starts at ROM address0',
            'Initial ICFG, privilege mode and stack bank selection',
            'Architectural RTI behavior at the application trampoline',
            'Cache command latency, clock, coherence and hardware status causality',
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rom', type=Path, required=True, help='exact private 128KiB ROM window')
    parser.add_argument('--report', type=Path, required=True, help='sanitized JSON output')
    args = parser.parse_args()
    require(args.rom.resolve() != args.report.resolve() and
            not (args.report.exists() and args.rom.samefile(args.report)),
            'report must not overwrite ROM input')
    rom = args.rom.read_bytes()
    check_rom(rom)
    report = analyze(upstream(), rom)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + '\n')
    print('CPU1 ROM bootstrap and cache anchors verified; sanitized static report written.')


if __name__ == '__main__':
    main()
