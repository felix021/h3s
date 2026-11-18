"""Acceptance-plan USB profiles (H5-PRE-01), candidate-parameterized.

Pure offline planning on 2MiB images. Explicitly named, digest-pinned
profiles per docs/hearing-aid-acceptance-plan.md section 1a. One candidate
app identity per flash/rollback direction pair (--candidate, default 'hal5'):

  - hal5 (original, behavior pinned by regression tests):
      accept-hal5-flash-v1   : banks A/B both b79 -> bank B = HAL5 app
      accept-b79-rollback-v1 : bank B = HAL5 (bank A still b79) -> bank B = b79
  - f1a / f1b (hearing-aid fix candidates on the HAL5 base, F1-PREP):
      accept-f1x-flash-v1   : bank B = HAL5 -> bank B = F1x app
      accept-f1x-rollback-v1 : bank B = F1x -> bank B = HAL5 (same-layer
        retreat; a further retreat to b79 is the unchanged hal5 rollback
        profile applied afterwards, i.e. F1x -> HAL5 -> b79, never a direct
        F1x -> b79 profile)

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
  - rollback accepts only a complete, CRC-valid, digest-pinned from-state
    bank B (HAL5 for the hal5 candidate, F1x for f1a/f1b), or reports a
    zero-write ALREADY_* report when bank B already carries the rollback
    target app (ALREADY_B79 when both banks are b79; ALREADY_HAL5 when bank B
    is HAL5 for f1a/f1b); unknown or mixed states are refused (interruption
    handling lives in accept_usb.analyze_interrupted, never here).

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
# F1a/F1b = hearing-aid lfix fix candidates on the HAL5 base (route LFIX-IMPL,
# UNTESTED, private files; docs/hearing-aid-lfix-impl.md). App pins below were
# extracted from the built artifacts with this module's parse_package logic
# (UFW sha == the pinned values; cross-checked against the build reports'
# hearing_aid_l1 app lengths: F1a 930968 / F1b 930854 == HAL5 930782 + 186/+72).
F1A_UFW_SHA = '73b45e659d127452697b5580b2474ef481ce9b8118f3f5dfa8b9c731bb4fef48'
F1A_APP_LEN = 930968
F1A_APP_SHA = 'd2499f8f1d8adcb9ae1fc17c49e14531ae07569b0b00806de86b067d81b0a535'
F1B_UFW_SHA = 'f16576687dae42a583149aa9f8a2f1b89dc777228245a27c289f0faeec00f2c1'
F1B_APP_LEN = 930854
F1B_APP_SHA = 'a700de5da57c83f9300a30d602591ea96583d52da7d3f845154f3861c2f6418d'

CANDIDATES = ('hal5', 'f1a', 'f1b')

FLASH_PROFILE = 'accept-hal5-flash-v1'
ROLLBACK_PROFILE = 'accept-b79-rollback-v1'
F1A_FLASH_PROFILE = 'accept-f1a-flash-v1'
F1A_ROLLBACK_PROFILE = 'accept-f1a-rollback-v1'
F1B_FLASH_PROFILE = 'accept-f1b-flash-v1'
F1B_ROLLBACK_PROFILE = 'accept-f1b-rollback-v1'

# ---- named transitions: candidate x direction -> (profile, bank-B from app,
# installed package app). The hal5 row is the original b79<->HAL5 pair and its
# behavior is regression-pinned; f1a/f1b flash from the HAL5 base and roll
# back to it (same layer). ----
_PROFILE_NAMES = {'hal5': {'flash': FLASH_PROFILE, 'rollback': ROLLBACK_PROFILE},
                  'f1a': {'flash': F1A_FLASH_PROFILE, 'rollback': F1A_ROLLBACK_PROFILE},
                  'f1b': {'flash': F1B_FLASH_PROFILE, 'rollback': F1B_ROLLBACK_PROFILE}}
_FROM_APP = {'hal5': {'flash': 'b79', 'rollback': 'hal5'},
             'f1a': {'flash': 'hal5', 'rollback': 'f1a'},
             'f1b': {'flash': 'hal5', 'rollback': 'f1b'}}
_TO_PACKAGE = {'hal5': {'flash': 'hal5', 'rollback': 'b79'},
               'f1a': {'flash': 'f1a', 'rollback': 'hal5'},
               'f1b': {'flash': 'f1b', 'rollback': 'hal5'}}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def require_candidate(candidate):
    require(candidate in CANDIDATES,
            'unknown candidate: %r (expected one of %s)' % (candidate, '/'.join(CANDIDATES)))
    return candidate


def _transition(direction, candidate, table):
    require_candidate(candidate)
    require(direction in ('flash', 'rollback'), 'unknown direction: ' + str(direction))
    return table[candidate][direction]


def _app_pins(name):
    """(app len, app sha) for a named app identity, resolved through the
    module globals at call time (tests may patch the shipped pins)."""
    return {'b79': (B79_APP_LEN, B79_APP_SHA), 'hal5': (HAL5_APP_LEN, HAL5_APP_SHA),
            'f1a': (F1A_APP_LEN, F1A_APP_SHA), 'f1b': (F1B_APP_LEN, F1B_APP_SHA)}[name]


def _ufw_sha(name):
    return {'b79': B79_PROFILE_SHA, 'hal5': HAL5_UFW_SHA,
            'f1a': F1A_UFW_SHA, 'f1b': F1B_UFW_SHA}[name]


def profile_for(direction, candidate='hal5'):
    return _transition(direction, candidate, _PROFILE_NAMES)


def package_id(direction, candidate='hal5'):
    """(pinned ufw sha, pinned app len, pinned app sha) for the package the
    direction+candidate installs."""
    name = _transition(direction, candidate, _TO_PACKAGE)
    return (_ufw_sha(name),) + _app_pins(name)


def from_state_id(direction, candidate='hal5'):
    """(app len, app sha) the device bank B must carry before this
    direction+candidate transition (b79 for hal5-flash / HAL5 for hal5-rollback
    and f1x-flash / F1x for f1x-rollback)."""
    return _app_pins(_transition(direction, candidate, _FROM_APP))


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
    expect_* (the transition's from-state; see from_state_id), bank boot
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


def make_plan(direction, before, ufw, nonce, candidate='hal5'):
    """Validate the from-state, build the target, derive the write set and
    return (target, plan). direction='flash' expects the candidate's base app
    in bank B (b79 for hal5, HAL5 for f1a/f1b); direction='rollback' expects
    bank B == the candidate app and reports a zero-write ALREADY_* report when
    bank B already carries the rollback target (both banks b79 for hal5, bank B
    HAL5 for f1a/f1b)."""
    require_candidate(candidate)
    pinned_ufw, pinned_len, pinned_sha = package_id(direction, candidate)
    package_app = parse_package(ufw, pinned_ufw, pinned_len, pinned_sha)
    from_len, from_sha = from_state_id(direction, candidate)
    if direction == 'flash':
        state = device_state(before, from_len, from_sha)
    else:
        base = _transition('rollback', candidate, _TO_PACKAGE)   # rollback target app
        already = {'b79': 'ALREADY_B79', 'hal5': 'ALREADY_HAL5'}[base]
        header_b, app_b = decode_bank(before, BANK_B)
        base_len, base_sha = _app_pins(base)
        if (len(app_b) == base_len and sha(app_b) == base_sha
                and struct.unpack_from('<H', header_b, 6)[0] == SEQ_B):
            device_state(before, base_len, base_sha)   # full base check (A included)
            return None, {'profile': profile_for(direction, candidate), 'status': already,
                          'before_sha256': sha(before), 'zero_write': True,
                          'note': ('both banks already b79; nothing to do' if base == 'b79'
                                   else 'bank B already carries the HAL5 base; '
                                        'nothing to roll back')}
        state = device_state(before, from_len, from_sha)
    target, geometry = build_target(before, package_app)
    sectors = changed_sectors(before, target)
    require(sectors, 'target equals the base map; nothing to write')
    require(sectors <= set(range(BANK_B, BANK_B_END, SECTOR)),
            'write set escapes bank B [0xee000, 0x1da000)')
    order = write_order(sectors)
    require(order[-1] == BANK_B, 'boot-info sector must be committed last')
    plan = {
        'profile': profile_for(direction, candidate), 'profile_version': 1,
        'direction': direction, 'candidate': candidate,
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
