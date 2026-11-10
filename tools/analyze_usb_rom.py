#!/usr/bin/env python3
"""Verify bounded USB dispatch evidence in the exact private BR28 ROM.

No USB/device access, RAM payload execution, download or full disassembly output.
The two isolated FB08 instruction fixtures stop at the unexecuted payload entry.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct

from probe_boot import ROM_SHA, check_rom
from probe_pi32v2 import PIN, require, upstream

REGIONS = (
    (0x468a, 0x470c, 'receive_copy', 'aa378eacee20fed1b246dd499395bb35d71b9e2037ae2ebbe546bbc4d127248e'),
    (0x470c, 0x4728, 'argument_and_receive_wrapper', 'be0cd1bf56eb1305cdcf2154bcc3da8cb6963e17e44206f316544c99156588a1'),
    (0x4738, 0x47b2, 'send_copy', 'b8aeb501a499d14b6a18f241d40c69136f0cddb599ae7c501f8924c6b21b0c2a'),
    (0x4808, 0x48f2, 'usb_setup', 'a8a0757f9562ff2e305b8d53a8de3332d645998cb0c7d64ab459c711b1f36767'),
    (0x4938, 0x499a, 'hook_dispatch_and_fb06', 'a35e73901c1bcc6b7d5f5a9bf339786d8298a8ac7db238a68115c487a12c9ef3'),
    (0x499a, 0x49f4, 'fd07', 'c6ac77e5597ec4ae3fdd75a2c09a99bc49f4f007057e166d22223f375bec56e4'),
    (0x4a70, 0x4ad0, 'fb08', '13b23d155fc045f14dcf596a80537bbaba8f79bc9490c372e2a58108f3608922'),
    (0xaa1e, 0xaa62, 'memory_transform', '0a0118c9693fbcc1d1fd3ef2d0aa61d098c1abd7b998278e74c58f234bcd744c'),
    (0x1f619, 0x1f629, 'transform_lookup', '2f3e78fc341f7ee59c7577e9318cd3e10cd9b5b4222ad02274408cdca61f3936'),
)

ANCHORS = (
    (0x4692, 'li.u32', {'n': 0x181454, 'r': 0}, None),
    (0x4698, 'ld.w.l', {'n': 0, 'r': 8, 's': 0}, None),
    (0x46ce, 'mov.rr', {'x': 0, 'y': 7}, None),
    (0x46d0, 'mov.rr', {'x': 1, 'y': 8}, None),
    (0x46d4, 'call.xl', {'n': -1438}, 0x413c),
    (0x470c, 'li.u32', {'n': 0x1813f8, 'r': 0}, None),
    (0x4712, 'ld.bu.l', {'n': 21, 'r': 1, 's': 0}, None),
    (0x4716, 'ld.bu.l', {'n': 22, 'r': 0, 's': 0}, None),
    (0x471a, 'insert.rrii', {'m': 8, 'n': 8, 'r': 0, 's': 1}, None),
    (0x471e, 'rts', {}, None),
    (0x4724, 'call.s', {'n': -156}, 0x468a),
    (0x473a, 'li.u32', {'n': 0x181444, 'r': 2}, None),
    (0x4740, 'ld.w.l', {'n': 0, 'r': 10, 's': 2}, None),
    (0x4778, 'if.ge.ri.s12', {'e': 0, 'n': 64, 'r': 5, 't': 1}, None),
    (0x4782, 'mov.rr', {'x': 0, 'y': 10}, None),
    (0x4784, 'mov.rr', {'x': 1, 'y': 6}, None),
    (0x4788, 'call.xl', {'n': -1618}, 0x413c),
    (0x478e, 'st.w.l', {'n': 0, 'r': 9, 's': 7}, None),
    (0x4810, 'li.u8', {'n': 0, 'r': 7}, None),
    (0x4884, 'li.u32', {'n': 0x181040, 'r': 0}, None),
    (0x488a, 'st.w', {'b': 4, 'd': 0, 'n': 28}, None),
    (0x4894, 'li.u32', {'n': 0x181080, 'r': 0}, None),
    (0x489a, 'st.w', {'b': 4, 'd': 0, 'n': 32}, None),
    (0x48a4, 'add.ri.u6_hi', {'n': 64, 'r': 0}, None),
    (0x48aa, 'st.w', {'b': 4, 'd': 0, 'n': 36}, None),
    (0x48c8, 'li.u32', {'n': 0x1813f8, 'r': 4}, None),
    (0x48da, 'li.u32', {'n': 0x181428, 'r': 8}, None),
    (0x4938, 'ld.w.l', {'n': 0, 'r': 2, 's': 8}, None),
    (0x493c, 'beqz', {'n': 10, 'r': 2}, 0x4948),
    (0x4942, 'call.r', {'x': 2}, None),
    (0x4944, 'bne.ri', {'i': 0, 'n': 392, 'r': 0}, 0x4ad0),
    (0x4956, 'beq.ri', {'i': 8, 'n': 278, 'r': 0}, 0x4a70),
    (0x495a, 'bne.ri', {'i': 6, 'n': 262, 'r': 0}, 0x4a64),
    (0x4982, 'call.l', {'n': -614}, 0x4720),
    (0x498e, 'call.xl', {'n': 0x608a}, 0xaa1e),
    (0x4994, 'st.w.l', {'n': 0, 'r': 8, 's': 7}, None),
    (0x49ce, 'call.xl', {'n': 0x604a}, 0xaa1e),
    (0x49e0, 'call.l', {'n': -684}, 0x4738),
    (0x49ec, 'call.xl', {'n': 0x602c}, 0xaa1e),
    (0x4a82, 'li.u32', {'n': 0x18142c, 'r': 6}, None),
    (0x4a88, 'li.u32', {'n': 0x4738, 'r': 1}, None),
    (0x4a92, 'st.w', {'b': 6, 'd': 1, 'n': 0}, None),
    (0x4a94, 'li.u32', {'n': 0x4720, 'r': 1}, None),
    (0x4a9e, 'st.w', {'b': 6, 'd': 1, 'n': 4}, None),
    (0x4aa0, 'st.w.l', {'n': 8, 'r': 6, 's': 8}, None),
    (0x4aa8, 'st.w', {'b': 6, 'd': 0, 'n': 12}, None),
    (0x4aae, 'st.w', {'b': 6, 'd': 7, 'n': 16}, None),
    (0x4ab4, 'rev8.rr', {'r': 1, 's': 3}, None),
    (0x4ab8, 'mov.rr', {'x': 0, 'y': 6}, None),
    (0x4aba, 'call.r', {'x': 1}, None),
    (0x4abc, 'li.u8', {'n': 251, 'r': 0}, None),
    (0x4abe, 'st.b.sp.l', {'n': 32, 'r': 0}, None),
    (0x4aca, 'li.u8', {'n': 16, 'r': 1}, None),
    (0x4acc, 'call.l', {'n': -920}, 0x4738),
    (0xaa20, 'li.u32', {'n': 0xffff, 'r': 3}, None),
    (0xaa26, 'li.u32', {'n': 0xfd3500, 'r': 2}, None),
    (0xaa2c, 'st.w', {'b': 2, 'd': 3, 'n': 4}, None),
    (0xaa36, 'li.u32', {'n': 0x1f619, 'r': 3}, None),
    (0xaa48, 'ld.bu', {'b': 0, 'd': 5, 'n': 0}, None),
    (0xaa4a, 'ld.w', {'b': 2, 'd': 7, 'n': 4}, None),
    (0xaa4c, 'xor.rr', {'d': 5, 's': 7}, None),
    (0xaa4e, 'st.b.pin', {'b': 0, 'd': 5, 'n': 1}, None),
)


def verify_windows(rom):
    windows = []
    for start, end, label, expected in REGIONS:
        require(hashlib.sha256(rom[start:end]).hexdigest() == expected,
                f'USB ROM window changed: {label}')
        windows.append({'name': label, 'start': hex(start), 'end_exclusive': hex(end), 'sha256': expected})
    return windows


def verify_anchors(module, rom):
    table = module.table.load()
    anchors = []
    for pc, identity, operands, target in ANCHORS:
        decoded = module.codec.decode(table, rom[pc:pc + 6])
        require(decoded is not None, f'USB ROM anchor does not decode: {pc:#x}')
        insn, raw, size = decoded
        actual = {key: value for key, value in module.check.operands(insn, raw).items() if key != 'raw'}
        require(insn.id == identity and actual == operands, f'USB ROM anchor changed: {pc:#x}')
        require(any(start <= pc and pc + size <= end for start, end, _, _ in REGIONS),
                'USB ROM anchor leaves its pinned window')
        field = module.branch_field(insn)
        row = {'pc': hex(pc), 'instruction_id': insn.id, 'size': size}
        if target is not None:
            require(field is not None and pc + size + actual[field] == target,
                    f'USB ROM branch changed: {pc:#x}')
            row['target'] = hex(target)
        else:
            require(field is None, f'unchecked USB ROM branch: {pc:#x}')
        anchors.append(row)
    return anchors


class StrictMemory:
    """Explicit synthetic workspace; ROM immutable, other reads and writes rejected."""
    def __init__(self, rom, hook):
        self.data = dict(enumerate(rom))
        self.data.update({0x1813f8 + i: 0 for i in range(128)})
        # Selected FB08 command: target 0x120000, arbitrary 16-bit argument 0xa5a5.
        command = bytes.fromhex('0800120000a5a5')
        self.data.update({0x181408 + i: value for i, value in enumerate(command)})
        self.data.update({0x181428 + i: value for i, value in enumerate(struct.pack('<I', hook))})
        self.writes = []

    def read(self, address, size):
        require(size >= 0 and all(address + i in self.data for i in range(size)),
                f'uninitialized fixture read: {address:#x}+{size}')
        return bytes(self.data[address + i] for i in range(size))

    def write(self, address, data):
        require(len(data) == 4 and address % 4 == 0 and
                0x18142c <= address and address + 4 <= 0x181440,
                f'write outside expected arglist: {address:#x}+{len(data)}')
        self.writes.append((address, len(data)))
        self.data.update({address + i: value for i, value in enumerate(data)})


def run_fb08_tail(module, rom, hook):
    """Actual instructions AFTER dispatch; no callback, payload or USB function runs."""
    cpu = module.CPU()
    cpu.mem = StrictMemory(rom, hook)
    cpu.R = [0xc0de0000 + i for i in range(16)]
    cpu.R[4], cpu.R[7], cpu.R[8] = 0x1813f8, 0, 0x181428
    cpu.pc = 0x4a70
    visited = set()
    while cpu.pc != 0x120000:
        require(cpu.steps < 30, 'FB08 tail exceeded bounded instruction count')
        require(0x4a70 <= cpu.pc < 0x4abc or 0x470c <= cpu.pc < 0x4720,
                'execution left the isolated FB08 tail/helper')
        visited.add(cpu.pc)
        cpu.step()
    require(cpu.steps == 23 and cpu.R[0] == 0x18142c and cpu.rets == 0x4abc,
            'FB08 actual CALL boundary changed')
    slots = struct.unpack('<5I', cpu.mem.read(cpu.R[0], 20))
    require(slots == (0x4738, 0x4720, 0x181428, 0xa5a5, 0), 'FB08 argument layout changed')
    require(cpu.mem.read(0x181428, 4) == struct.pack('<I', hook), 'FB08 tail changed hook')
    require(cpu.mem.writes == [(0x18142c + 4 * i, 4) for i in range(5)],
            'FB08 tail write coverage changed')
    require(0x4aba in visited and 0x470c in visited, 'actual CALL/helper not executed')
    return {'initial_hook': hex(hook), 'final_hook': hex(hook),
            'completed_model_instructions': cpu.steps, 'stop_before_payload': hex(cpu.pc),
            'rets': hex(cpu.rets), 'arglist': hex(cpu.R[0]),
            'arglist_words': [hex(value) for value in slots],
            'write_count': len(cpu.mem.writes),
            'write_span': {'start': '0x18142c', 'end_exclusive': '0x181440'}}


def analyze(module, rom):
    check_rom(rom)
    windows = verify_windows(rom)
    anchors = verify_anchors(module, rom)
    cases = [run_fb08_tail(module, rom, hook) for hook in (0, 0x121cfe)]
    return {
        'format_version': 1, 'rom_sha256': ROM_SHA, 'rom_bytes': len(rom),
        'decoder_commit': PIN, 'offline_only': True, 'device_accessed': False,
        'hardware_cpu_executed': False, 'payload_executed': False,
        'deployable': False, 'suitable_for_payload_execution_approval': False,
        'scope': 'Pinned ROM windows and selected anchors, plus two isolated instruction-model fixtures. Not a USB session, complete ABI, ownership or timing certification.',
        'regions': windows, 'selected_instruction_anchors': anchors,
        'hook_dispatch': {
            'hook_pointer_address': '0x181428', 'load_pc': '0x4938',
            'callback_call_pc': '0x4942', 'nonzero_callback_result_branch_pc': '0x4944',
            'rule': 'If installed, the hook runs before ROM dispatch; nonzero callback result bypasses ROM command handling.',
        },
        'fb06': {
            'receive_call_pc': '0x4982', 'receive_target': '0x4720',
            'transform_call_pc': '0x498e', 'transform_target': '0xaa1e',
            'hook_clear_pc': '0x4994', 'hook_clear_address': '0x181428',
            'limit': 'This is the ROM handling path, reached only if an installed hook does not consume the command. No transfer, CRC acceptance or timeout behavior was exercised.',
        },
        'fb08': {
            'dispatch_branch_pc': '0x4956', 'tail_entry_pc': '0x4a70',
            'call_pc': '0x4aba', 'post_call_pc': '0x4abc',
            'argument_pointer_in': 'R0', 'arglist_address': '0x18142c',
            'arglist_fields': ['send_pointer=0x4738', 'receive_pointer=0x4720',
                               'hook_pointer_address=0x181428', '16-bit_command_argument', 'zero'],
            'hook_effect': 'The selected FB08 tail preserves the hook; it does not perform the FB06 hook clear.',
            'response': {'send_call_pc': '0x4acc', 'send_target': '0x4738', 'bytes': 16,
                         'r0_return_value_overwrite_pc': '0x4abc', 'r0_replacement': '0xfb',
                         'limit': 'The payload return value in R0 is overwritten, not forwarded as an application result. The response is constructed after return; this fixture stops before executing the payload or sending a response.'},
        },
        'fd07': {
            'transform_before_send_pc': '0x49ce', 'send_pc': '0x49e0',
            'transform_after_send_pc': '0x49ec', 'transform_target': '0xaa1e',
            'operation': 'Transform source RAM in place, send it, then transform it again on the normal returning path.',
            'transform_engine_base': '0xfd3500', 'transform_lookup_address': '0x1f619',
            'limit': 'The static CRC-engine/XOR path supports the MengLi protocol interpretation. Engine behavior, USB completion, interruption and guaranteed source restoration were not executed or certified. Read-only regions and MMIO do not have ordinary writable-RAM behavior.',
        },
        'usb_copy_buffers': {
            'setup_control_offsets': ['0x1c', '0x20', '0x24'],
            'setup_buffer_addresses': ['0x181040', '0x181080', '0x1810c0'],
            'receive_buffer_pointer_slot': '0x181454', 'receive_copy_call_pc': '0x46d4',
            'send_buffer_pointer_slot': '0x181444', 'send_copy_call_pc': '0x4788',
            'copy_target': '0x413c', 'send_chunk_limit_bytes': 64,
            'limit': 'These routines copy through fixed USB buffers. They do not establish that every DMA engine or CPU1 is idle, or that arbitrary RAM is available.',
        },
        'fb08_tail_fixture': {
            'initial_register_policy': 'Distinct synthetic GPR values; R4=0x1813f8, R7=0, R8=0x181428. Other simulator defaults are not measured ROM initial state.',
            'workspace': 'Explicit synthetic 128-byte region at 0x1813f8, command target 0x120000 and argument 0xa5a5; no implicit zero reads elsewhere.',
            'memory_policy': 'ROM read-only; only five aligned 4-byte arglist writes accepted; uninitialized reads and all other writes rejected.',
            'entry_assumption': 'Starts after command dispatch selected the ROM FB08 path. A nonzero installed hook is assumed to have returned zero; its callback is not executed.',
            'cases': cases,
            'limit': '46 completed model instructions total; no payload fetch, USB/MMIO function, RTI, interrupt or other core runs.',
        },
        'unproved': [
            'A USB identity alone identifies whether a RAM loader hook owns a command.',
            'The normal-application transition executes every cold-ROM initialization path or yields measured ICFG, stack, reset and cache state.',
            'RAM upload space is free: 0x120000..0x122800 is the actual application MMU-table range before transition; overlap requires separate ownership and MMU evidence.',
            'CPU1, DMA and caches cannot use the proposed RAM range, or a self-written payload can safely restore mode, interrupt configuration and stack banks.',
            'A completed USB response proves full machine-state restoration or allows any new hardware action.',
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rom', type=Path, required=True, help='exact private 128KiB ROM window')
    parser.add_argument('--report', type=Path, required=True, help='bounded sanitized JSON output')
    args = parser.parse_args()
    for source in (args.rom, Path(__file__)):
        require(args.report.resolve() != source.resolve() and
                not (args.report.exists() and source.exists() and args.report.samefile(source)),
                'report must not overwrite an input')
    rom = args.rom.read_bytes()
    check_rom(rom)
    report = analyze(upstream(), rom)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + '\n')
    print('USB ROM anchors and two 23-instruction FB08 tails verified; no device or payload executed.')


if __name__ == '__main__':
    main()
