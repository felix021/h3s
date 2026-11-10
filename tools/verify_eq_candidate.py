#!/usr/bin/env python3
"""Independent scope/control-flow audit for the experimental EQ candidate.

Imports only the stdlib integrity verifier, never builder/ELF-patching code.
Requires the vendor disassembly of the linked patch for ISA-level glue checks.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import struct

from verify_ufw_integrity import decode, sfc, verify


def load_image(path):
    raw = path.read_bytes()
    row = decode(raw[64:144])
    offset, size = struct.unpack_from('<II', row, 8)
    if (offset, size) != (0x600, 0xE6000):
        raise ValueError('Unexpected flash location/size')
    flash = raw[offset:offset + size]
    length = struct.unpack_from('<I', decode(flash[0x2000:0x2020]), 8)[0]
    plain = sfc(flash[0x2020:0x2020 + length])
    return raw, flash, plain


def audit(baseline, candidate, assembly, *, repaired_fence=False):
    for path in (baseline, candidate):
        if not verify(path)['passed']:
            raise ValueError('Independent CRC failure: ' + str(path))
    old_raw, old_flash, old = load_image(baseline)
    new_raw, new_flash, new = load_image(candidate)
    if hashlib.sha256(old_raw).hexdigest() != 'b79cb3a6f8702eb9e1d12eaf181a968f6b5848a66c051b2c792c39aa8b42fa1b':
        raise ValueError('Baseline is not the tested ANC/tone image')
    if len(new_raw) != len(old_raw) or len(new) <= len(old):
        raise ValueError('Expected fixed UFW length and extended CRC-covered region')
    prefix_changes = {i for i, (a, b) in enumerate(zip(old_raw[:0x600], new_raw[:0x600])) if a != b}
    if not prefix_changes <= {0, 1, 2, 3, 68, 69}:
        raise ValueError('Unexpected outer header/directory changes')
    if old_flash[:0x2000] != new_flash[:0x2000]:
        raise ValueError('Bootloader changed')
    old_boot,new_boot=decode(old_flash[0x2000:0x2020]),decode(new_flash[0x2000:0x2020])
    if old_boot[6:8]!=new_boot[6:8] or old_boot[12:]!=new_boot[12:]:
        raise ValueError('Unrelated boot-info fields changed')
    end = 0x2020 + len(new)
    if old_flash[end:] != new_flash[end:]:
        raise ValueError('Flash data beyond extension changed')

    # Existing flash changes: boot metadata, app-area CRC fields, five code
    # sites, the former last file's index/header CRC, and appended JLFS file.
    sites = [0x060059CE, 0x06025030, 0x0602510E, 0x06025364, 0x06078846]
    allowed = set(range(4)) | set(range(0x20, 0x24))
    allowed |= {0xE4EF3 - 0x2020, 0xE4EF4 - 0x2020,
                0xE4F01 - 0x2020, 0xE4F02 - 0x2020}
    for address in sites:
        allowed.update(range(address - 0x06000000, address - 0x06000000 + 6))
    if repaired_fence:allowed.update(range(0x34e9a,0x34e9e))
    differences = [i for i, (a, b) in enumerate(zip(old, new)) if a != b]
    unexpected = [hex(i + 0x2020) for i in differences if i not in allowed]
    if unexpected:
        raise ValueError('Unexpected plaintext changes: ' + str(unexpected[:20]))
    if old_raw[0xE6600:] != new_raw[0xE6600:]:
        raise ValueError('Outer resources changed')
    # Both images must preserve the exact tested ANC table and stream bytes.
    if old[0x100 + 0x9E410:0x100 + 0x9E413] != b'\x02\x03\x02':
        raise ValueError('Baseline ANC table mismatch')
    if new[0x100 + 0x9E410:0x100 + 0x9E413] != b'\x02\x03\x02':
        raise ValueError('ANC table changed')

    def entries(data):
        pos = 0
        result = []
        while pos < len(data):
            _, _, addr, size, flags, _, end, name = struct.unpack_from('<HHIIBBH16s', data, pos)
            result.append((pos, size, flags, name.split(b'\0')[0].decode()))
            pos += size
            if end:
                break
        if pos != len(data):
            raise ValueError('JLFS coverage mismatch')
        return result

    old_entries, new_entries = entries(old), entries(new)
    if new_entries[:-1] != old_entries or new_entries[-1][3] != 'eq_toggle.bin':
        raise ValueError('Old resources moved/changed or unexpected extension')
    for pos, size, flags, name in old_entries[1:]:
        if old[pos + 32:pos + size] != new[pos + 32:pos + size]:
            raise ValueError('Existing resource content changed: ' + name)
    start, size, _, _ = new_entries[-1]
    payload = new[start + 33:start + size]
    if start != len(old) or len(old) != 0xE3053 or new[start + 32] != 255:
        raise ValueError('Extension alignment mismatch')
    payload_address = 0x06000000 + start + 33
    if payload_address != 0x060E3074 or not 1 <= len(payload) <= 0xF6C:
        raise ValueError('Payload outside fixed location')
    upper=0x060C3338
    if repaired_fence:
        if len(payload)!=337 or hashlib.sha256(payload).hexdigest()!= 'f4e9501e515a7f74e7114633ba9bc165d1da9b399481530a78be8663dbe0e49a':
            raise ValueError('Repair policy requires the exact archived337B compiled payload')
        for address,expected in zip(sites,('c6ea60eb0000','80ff7ae00b00','80ffaedf0b00','c5ea86ee0000','c3ea2b540000')):
            offset=address-0x06000000
            if new[offset:offset+6]!=bytes.fromhex(expected):
                raise ValueError('Repair policy requires exact archived hook instructions')
        if old[0x34e92:0x34e9e]!=bytes.fromhex('c0ff00010006c1ff38330c06'):
            raise ValueError('Baseline PC limit loads changed')
        if new[0x34e92:0x34e9a]!=old[0x34e92:0x34e9a] or new[0x34e9e:0x34eb0]!=old[0x34e9e:0x34eb0]:
            raise ValueError('PC limit lower/load/call/enable changed')
        upper=struct.unpack_from('<I',new,0x34e9a)[0]
        if upper!=payload_address+len(payload):
            raise ValueError('Repaired fence does not end exactly at payload end')
    if not 0x06000100 <= payload_address < payload_address + len(payload) <= upper:
        raise ValueError('REJECTED: payload is outside the enabled Flash PC execution range')

    labels, rows, waiting = {}, {}, None
    for line in assembly.read_text().splitlines():
        label = re.fullmatch(r'([A-Za-z_][\w.]*):', line)
        if label:
            waiting = label[1]
            continue
        match = re.match(r'\s*([0-9a-f]+):\s*((?:[0-9a-f]{2} )+)\s*(.*)', line)
        if not match:
            continue
        address = int(match[1], 16)
        raw = bytes.fromhex(match[2])
        if waiting:
            labels[waiting] = address
            waiting = None
        if new[address - 0x06000000:address - 0x06000000 + len(raw)] != raw:
            raise ValueError(f'Disassembly does not match candidate at {address:#x}')
        transfer = re.search(r'\b(call|goto) (-?\d+)', match[3])
        target = address + len(raw) + int(transfer[2]) if transfer else None
        rows[address] = (match[3].strip(), target)

    for address, symbol in zip(sites, ['eq_source_read_hook', 'eq_record_preset_source',
                                     'eq_record_custom_source', 'eq_key_hook', 'eq_open_hook']):
        if rows[address][1] != labels[symbol]:
            raise ValueError(f'Incorrect hook target at {address:#x}')
    original_destinations = {0x0602530A, 0x06017958, 0x0602536A, 0x060059D4, 0x0607884C}
    outgoing = {target for address, (_, target) in rows.items()
                if address >= payload_address and target is not None
                and not payload_address <= target < payload_address + len(payload)}
    if outgoing != original_destinations:
        raise ValueError('Unexpected original-code resume/call targets: ' + str(outgoing))
    key = labels['eq_key_hook']
    if rows[key][0] != 'r5 = b[r0+0] (u)':
        raise ValueError('Key passthrough does not replay action load')
    if not rows[key + 2][0].startswith('if (r2 != 7) goto '):
        raise ValueError('Missing multifunction-key condition')
    # Cross-check against the ACTUAL default table, not only the App eventID.
    default_keys = old[0x100 + 0x9E150:0x100 + 0x9E150 + 45]
    triples = [tuple(default_keys[i:i + 3]) for i in range(0, 45, 3)]
    if (7, 2, 7) not in triples:
        raise ValueError('Unexpected original multifunction-double-click mapping')
    if not rows[key + 6][0].startswith('if (r3 != 2) goto '):
        raise ValueError('Missing double-click condition')
    source = labels['eq_source_read_hook']
    if rows[source][0] != 'r1 = b[r6+42] (u)' or rows[source + 4][0] != 'r1 = r1 & 0x7F':
        raise ValueError('Missing EQ-source flag mask')
    opening = labels['eq_open_hook']
    opening_rows = [(a, text, target) for a, (text, target) in rows.items() if opening <= a < opening + 20]
    resume_index = next(i for i, (_, _, t) in enumerate(opening_rows) if t == 0x0607884C)
    if opening_rows[resume_index - 1][1] != 'r0 = [r6+4]':
        raise ValueError('EQ-open hook does not restore original live r0')
    return {'status': 'offline audit passed; NOT hardware validation',
            'candidate_sha256': hashlib.sha256(new_raw).hexdigest(), 'bytes': len(new_raw),
            'new_crc_covered_app_bytes': len(new), 'payload_bytes': len(payload),
            'whole_ufw_changed_bytes_vs_tested_image': sum(a != b for a, b in zip(old_raw, new_raw)),
            'payload_sha256': hashlib.sha256(payload).hexdigest(),
            'changed_plaintext_bytes_before_extension': len(differences),
            'existing_resources_identical_to_tested_image': True,
            'unrelated_boot_info_fields_preserved':True,
            'anc_two_state_table_preserved': True, 'hook_targets_and_resumes_verified': True,
            'actual_disassembly_bytes_match_image': True,
            'new_file': 'eq_toggle.bin', 'device_verified': False,
            **({'code_fence_repair':{'programmed_upper_limit':hex(upper),'lower_and_enable_preserved':True,
                                    'exact_hardware_endpoint_and_low_bits_unverified':True,
                                    'whole_boot_validated':False}} if repaired_fence else {})}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('baseline', type=Path)
    parser.add_argument('candidate', type=Path)
    parser.add_argument('--patch-asm', type=Path, required=True)
    parser.add_argument('--report', type=Path)
    parser.add_argument('--repaired-fence',action='store_true',help='audit the distinct offline PC-fence repair candidate')
    args = parser.parse_args()
    result = audit(args.baseline, args.candidate, args.patch_asm,repaired_fence=args.repaired_fence)
    text = json.dumps(result, ensure_ascii=False, indent=2) + '\n'
    if args.report:
        args.report.write_text(text)
    print(text, end='')
