"""Acceptance-plan USB profiles (H5-PRE-01): b79->HAL5 flash and HAL5->b79 rollback.

Pure offline planning on 2MiB images. Two explicitly named, digest-pinned
profiles per docs/hearing-aid-acceptance-plan.md section 1a:

  - accept-hal5-flash-v1 : device banks A/B both b79 -> bank B = HAL5 app
  - accept-b79-rollback-v1 : device bank B = HAL5 (bank A still b79) -> bank B = b79

Rules pinned here (codex consult 2026-11-18, ~/tmp/codex/202611180202-pregap):

  - target = byte copy of the session's fresh double-read base map; replace the
    audited bank-B header fields (+0..2 header CRC, +2..4 plaintext CRC, +4..6
    cipher CRC recomputed from the package app, +8..12 length) keeping +6..8
    sequence 3 and +12..32 device fields; replace the bank-B app; when the new
    app is SHORTER than the validated old app, fill 0xff up to the old app end
    (this session's validated extent, never a historical one); every byte beyond
    keeps its fresh value and is re-programmed as-is inside partially-written
    sectors.
  - write set = sectors of ALL differing bytes between base map and target,
    independently recomputed by accept_verify.verify_plan; write order is
    ascending with the boot-info sector 0xee000 last. The historical r2 six
    sectors are NOT prefilled and impose nothing here.
  - preserved ranges [0, 0xee000) and [0x1da000, 0x200000) are byte-identical
    in target; only the live VM region may drift between plan and apply and the
    live target keeps the freshest VM (digest re-bound in the journal).
  - rollback accepts only a complete, CRC-valid, digest-pinned HAL5 bank B as
    its from-state, or reports ALREADY_B79 (zero writes) when both banks are
    b79; unknown or mixed states are refused (interruption handling lives in
    accept_usb.analyze_interrupted, never here).

The r2->b79 profile in image.py / h3s_usb.py is untouched: importing its
helpers (crc/encode/sfc/sha) does not relax any of its gates, and the 942080B
UFW flash.bin is only ever read as a container, never used as a 2MiB image.
"""
import json
import secrets
import struct

from image import GOOD_UFW_SHA, VM_BEGIN, VM_END, SIZE, crc, encode, sfc, sha

SECTOR = 4096
BANK_A = 0x2000
BANK_B = 0xee000
BANK_B_END = 0x1da000          # bank B app region ceiling; VM and resources start here
SEQ_A, SEQ_B = 2, 3
HEADER_LEN = 32

# ---- pinned identities (public assets or offline-audited candidates) ----
# b79 = tested ANC two-state image (firmware release asset, GOOD_UFW_SHA)
B79_PROFILE_SHA = GOOD_UFW_SHA
B79_APP_LEN = 929875
B79_APP_SHA = '1505f01398c3d32c0854fdf98e8b84aaae5269f7d5f278fd8a27459d7d19e6bf'
# HAL5 = hearing-aid full M=95 candidate, offline-audited, UNTESTED (private file)
HAL5_UFW_SHA = '9ad0f0881a7996cb4dea81477d8731aa9b4db69a6f352603118663d68eda5465'
HAL5_APP_LEN = 930782
HAL5_APP_SHA = 'b8f10fd5c4f88d8e642577aee35077cb1d28088ccdcc33ef20545df0b513de73'

FLASH_PROFILE = 'accept-hal5-flash-v1'
ROLLBACK_PROFILE = 'accept-b79-rollback-v1'


def require(condition, message):
    if not condition:
        raise ValueError(message)


def profile_for(direction):
    return {'flash': FLASH_PROFILE, 'rollback': ROLLBACK_PROFILE}[direction]


def package_id(direction):
    """(pinned ufw sha, pinned app len, pinned app sha) for the package the direction uses."""
    if direction == 'flash':
        return (HAL5_UFW_SHA, HAL5_APP_LEN, HAL5_APP_SHA)
    if direction == 'rollback':
        return (B79_PROFILE_SHA, B79_APP_LEN, B79_APP_SHA)
    raise ValueError('unknown direction: ' + str(direction))


def new_nonce():
    return secrets.token_hex(16)


# ---- package parsing (UFW container, read-only) ----

def parse_package(ufw, pinned_ufw_sha, pinned_app_len, pinned_app_sha):
    """Decode the UFW directory entry, extract flash.bin, decode its package
    bank-A header and return the application ciphertext. Every length, CRC and
    digest is checked against the pins; the flash.bin is never treated as a
    2MiB image."""
    require(len(ufw) == 1326848 and sha(ufw) == pinned_ufw_sha,
            'package is not the pinned UFW for this profile')
    directory = encode(ufw[64:144])
    kind, _, _, _, offset, size, padded, _, name = struct.unpack('<HHHHIII44s16s', directory)
    require(kind == 0 and name.split(b'\0')[0] == b'flash.bin', 'unexpected UFW entry')
    require(size == padded == 942080 and offset + size <= len(ufw), 'UFW flash bounds')
    flash = ufw[offset:offset + size]
    header, app = decode_bank(flash, BANK_A)
    length = struct.unpack_from('<I', header, 8)[0]
    require(length == pinned_app_len and sha(app) == pinned_app_sha,
            'package application does not match the pinned length/digest')
    require(struct.unpack_from('<H', header, 6)[0] == 0 and header[12:32] == b'\xff' * 20,
            'package bank-A header carries device fields; not a stock package header')
    return app


def decode_bank(data, base):
    """Decode one boot-info header + app ciphertext from any buffer at base.
    Checks header CRC, sane length, cipher CRC and plaintext CRC (SFC phase
    starts at 0 within each bank). Returns (header32, app)."""
    require(len(data) >= base + HEADER_LEN, 'buffer too short for bank header')
    header = encode(data[base:base + HEADER_LEN])
    require(crc(header[2:]) == struct.unpack_from('<H', header)[0], 'bank header CRC')
    length = struct.unpack_from('<I', header, 8)[0]
    require(0 < length <= BANK_B_END - base - HEADER_LEN, 'bank application bounds')
    app = data[base + HEADER_LEN:base + HEADER_LEN + length]
    require(len(app) == length, 'bank application truncated')
    require(crc(app) == struct.unpack_from('<H', header, 4)[0], 'bank cipher CRC')
    require(crc(sfc(app)) == struct.unpack_from('<H', header, 2)[0], 'bank plaintext CRC')
    return header, app


# ---- device from-state validation ----

def device_state(before, expect_b_app_len, expect_b_app_sha):
    """Validate a 2MiB device image: both bank headers CRC-valid, sequences
    A=2/B=3, bank A app == pinned b79 application, bank B app digest ==
    expect_* (b79 for the flash direction, HAL5 for rollback), bank boot
    configuration tails identical. Returns per-bank (header, app)."""
    require(len(before) == SIZE, 'full 2MiB image required')
    header_a, app_a = decode_bank(before, BANK_A)
    header_b, app_b = decode_bank(before, BANK_B)
    require(struct.unpack_from('<H', header_a, 6)[0] == SEQ_A, 'bank A sequence is not 2')
    require(struct.unpack_from('<H', header_b, 6)[0] == SEQ_B, 'bank B sequence is not 3')
    require(sha(app_a) == B79_APP_SHA and len(app_a) == B79_APP_LEN,
            'bank A is not the pinned b79 application')
    require(len(app_b) == expect_b_app_len and sha(app_b) == expect_b_app_sha,
            'bank B is not the pinned application for this direction')
    require(header_a[12:32] == header_b[12:32], 'bank boot configurations disagree')
    return {'a': (header_a, app_a), 'b': (header_b, app_b)}


# ---- target construction (section 1a rules) ----

def build_target(before, package_app):
    """target = copy(before) with the audited bank-B header fields recomputed
    for package_app, the bank-B app replaced, and 0xff fill from the new app
    end up to the OLD app end when the new app is shorter. Bytes beyond the
    old app end keep their before value (re-programmed verbatim in partially
    written sectors). Returns (target, geometry dict)."""
    header_b, old_app = decode_bank(before, BANK_B)
    old_end = BANK_B + HEADER_LEN + len(old_app)
    new_len = len(package_app)
    new_end = BANK_B + HEADER_LEN + new_len
    require(new_end <= BANK_B_END, 'package application exceeds bank B ceiling')
    fill_end = max(new_end, old_end)
    header = bytearray(header_b)
    struct.pack_into('<H', header, 2, crc(sfc(package_app)))   # plaintext CRC
    struct.pack_into('<H', header, 4, crc(package_app))        # cipher CRC
    struct.pack_into('<I', header, 8, new_len)                 # length (+8..12)
    struct.pack_into('<H', header, 0, crc(header[2:]))         # header CRC (+0..2)
    # +6..8 sequence 3 and +12..32 device fields are kept untouched above.
    target = bytearray(before)
    target[BANK_B:BANK_B + HEADER_LEN] = encode(bytes(header))
    target[BANK_B + HEADER_LEN:new_end] = package_app
    target[new_end:fill_end] = b'\xff' * (fill_end - new_end)
    return bytes(target), {
        'old_app_len': len(old_app), 'old_app_sha256': sha(old_app),
        'old_extent': old_end, 'new_app_len': new_len, 'new_app_sha256': sha(package_app),
        'new_extent': new_end, 'fill_0ff_range': [new_end, fill_end] if fill_end > new_end else None,
        'preserved_tail_range': [fill_end, BANK_B_END],
        'b_header_kept_seq': struct.unpack_from('<H', header_b, 6)[0],
        'b_header_kept_device_fields_hex': header_b[12:32].hex(),
    }


def changed_sectors(before, after):
    return {i // SECTOR * SECTOR for i, (a, b) in enumerate(zip(before, after)) if a != b}


def write_order(sectors):
    """Ascending sector order with the boot-info sector committed last."""
    order = sorted(s for s in sectors if s != BANK_B)
    if BANK_B in sectors:
        order.append(BANK_B)
    return order


# ---- plan generation ----

def _plan_digest(plan):
    body = {k: v for k, v in plan.items() if k != 'plan_digest'}
    return sha(json.dumps(body, sort_keys=True).encode())


def make_plan(direction, before, ufw, nonce):
    """Validate the from-state, build the target, derive the write set and
    return (target, plan). direction='flash' expects both banks b79;
    direction='rollback' expects bank B == HAL5 (or reports ALREADY_B79)."""
    pinned_ufw, pinned_len, pinned_sha = package_id(direction)
    package_app = parse_package(ufw, pinned_ufw, pinned_len, pinned_sha)
    if direction == 'flash':
        state = device_state(before, B79_APP_LEN, B79_APP_SHA)
    else:
        header_b, app_b = decode_bank(before, BANK_B)
        if (len(app_b) == B79_APP_LEN and sha(app_b) == B79_APP_SHA
                and struct.unpack_from('<H', header_b, 6)[0] == SEQ_B):
            device_state(before, B79_APP_LEN, B79_APP_SHA)   # full b79 check (A included)
            return None, {'profile': ROLLBACK_PROFILE, 'status': 'ALREADY_B79',
                          'before_sha256': sha(before), 'zero_write': True,
                          'note': 'both banks already b79; nothing to do'}
        state = device_state(before, HAL5_APP_LEN, HAL5_APP_SHA)
    target, geometry = build_target(before, package_app)
    sectors = changed_sectors(before, target)
    require(sectors, 'target equals the base map; nothing to write')
    require(sectors <= set(range(BANK_B, BANK_B_END, SECTOR)),
            'write set escapes bank B [0xee000, 0x1da000)')
    order = write_order(sectors)
    require(order[-1] == BANK_B, 'boot-info sector must be committed last')
    plan = {
        'profile': profile_for(direction), 'profile_version': 1, 'direction': direction,
        'package_ufw_sha256': pinned_ufw, 'package_app_len': pinned_len,
        'package_app_sha256': pinned_sha,
        'before_sha256': sha(before), 'target_sha256': sha(target),
        'changed_bytes': sum(a != b for a, b in zip(before, target)),
        'write_order': order, 'write_set_sorted': sorted(sectors),
        'preserved_ranges': [[0, BANK_B], [BANK_B_END, SIZE]],
        'vm_range': [VM_BEGIN, VM_END],
        'geometry': geometry,
        'sectors': [{'offset': p, 'before_sha256': sha(before[p:p + SECTOR]),
                     'after_sha256': sha(target[p:p + SECTOR])} for p in order],
        'nonce': require_nonce(nonce),
    }
    plan['plan_digest'] = _plan_digest(plan)
    return target, plan


def require_nonce(nonce):
    require(isinstance(nonce, str) and len(nonce) >= 32
            and all(c in '0123456789abcdef' for c in nonce),
            'nonce must be a fresh >=128-bit hex string from accept_profile.new_nonce()')
    return nonce


# ---- live rebind (apply-time freshness) ----

def rebind_live_target(planned_before, planned_target, fresh, second_read):
    """Apply-time freshness gate: fresh double read must be equal, match the
    planned base map outside the VM region, and the live target keeps the
    freshest VM. The rebound diff must land on exactly the planned sectors
    (a live recomputation can never widen the audited write set)."""
    require(len(fresh) == SIZE and second_read == fresh, 'fresh independent full reads differ')
    require(fresh[:VM_BEGIN] == planned_before[:VM_BEGIN],
            'bytes in [0, 0x1da000) changed after planning; do not write')
    require(fresh[VM_END:] == planned_before[VM_END:],
            'bytes in [0x1de000, 0x200000) changed after planning; do not write')
    sectors = changed_sectors(planned_before, planned_target)
    live = bytearray(fresh)
    for p in sectors:
        live[p:p + SECTOR] = planned_target[p:p + SECTOR]
    live = bytes(live)
    require(changed_sectors(fresh, live) == sectors,
            'live rebinding touched sectors outside the audited write set')
    require(live[:BANK_B] == planned_target[:BANK_B],
            'live target would modify [0, 0xee000)')
    require(live[VM_END:] == planned_target[VM_END:],
            'live target would modify [0x1de000, 0x200000)')
    require(live[VM_BEGIN:VM_END] == fresh[VM_BEGIN:VM_END],
            'live target must keep the freshest VM')
    return live


def plan_from_files(plan_path):
    plan = json.loads(plan_path.read_text())
    require_nonce(plan.get('nonce', ''))
    require(plan.get('plan_digest') == _plan_digest(plan), 'plan digest mismatch')
    return plan
