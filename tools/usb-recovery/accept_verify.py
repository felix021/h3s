"""Independent verifier for acceptance-plan USB profiles (H5-PRE-01).

Never imports the plan generator's construction path: verify_plan re-derives
every constraint directly from primary inputs (before image, target image,
package application ciphertext, plan dict), so a wrong-but-self-consistent
plan cannot pass just because it was produced by the same code that checks it
(codex consult D2: diff derivation is not authorization).

Checked, in order:
  1. plan identity/nonce/digest self-consistency (profile, direction, pinned
     package digests, plan_digest over the canonical body);
  2. from-state: full device_state validation of `before` for the direction
     (bank A always pinned b79; bank B pinned b79 for flash / HAL5 for
     rollback);
  3. package app: length/digest match the pins;
  4. target byte rules against `before`: [0, 0xee000) and [0x1da000, 0x200000)
     identical; bank-B header differs only at +0..6 (three CRCs) and +8..12
     (length), with +6..8 sequence and +12..32 device fields preserved; header
     CRCs recomputed from package_app; app region == package_app; 0xff exactly
     on [new_end, old_end) when the new app is shorter; everything else inside
     bank B identical;
  5. write set == all differing sectors, contained in bank B, boot-info last;
  6. every per-sector and whole-image digest the plan claims matches reality.

Any violation raises ValueError before the first erase could ever happen.
"""
import struct

import accept_profile as ap
from accept_profile import (BANK_B, BANK_B_END, SECTOR, SEQ_B, SIZE,
                            _plan_digest, changed_sectors, crc, decode_bank,
                            device_state, encode, profile_for, require, sfc,
                            sha, write_order)


def verify_plan(plan, before, target, package_app):
    require(plan.get('profile') == profile_for(plan['direction']),
            'plan profile does not match its direction')
    require(plan.get('profile_version') == 1, 'unknown profile version')
    require(plan['plan_digest'] == _plan_digest(plan), 'plan digest mismatch')
    require_nonce_shape(plan['nonce'])

    # pins resolved through the module at call time (test fixtures may pin
    # synthetic identities; the shipped constants are the real profiles)
    pinned = {'flash': (ap.B79_APP_LEN, ap.B79_APP_SHA),
              'rollback': (ap.HAL5_APP_LEN, ap.HAL5_APP_SHA)}[plan['direction']]
    device_state(before, *pinned)
    require(sha(before) == plan['before_sha256'], 'before digest mismatch')
    require(sha(target) == plan['target_sha256'], 'target digest mismatch')
    require(len(package_app) == plan['package_app_len']
            and sha(package_app) == plan['package_app_sha256'],
            'package application does not match the plan pins')

    _verify_target_bytes(before, target, package_app)

    sectors = changed_sectors(before, target)
    require(sectors == set(plan['write_set_sorted']), 'plan write set is not the full diff')
    require(sectors <= set(range(BANK_B, BANK_B_END, SECTOR)), 'write set escapes bank B')
    order = write_order(sectors)
    require(order == plan['write_order'], 'plan write order is not ascending with boot-info last')
    require(plan['sectors'] == [{'offset': p, 'before_sha256': sha(before[p:p + SECTOR]),
                                 'after_sha256': sha(target[p:p + SECTOR])} for p in order],
            'per-sector digests do not match the images')
    require(plan['changed_bytes'] == sum(a != b for a, b in zip(before, target)),
            'changed_bytes accounting mismatch')
    return True


def require_nonce_shape(nonce):
    require(isinstance(nonce, str) and len(nonce) >= 32
            and all(c in '0123456789abcdef' for c in nonce), 'nonce shape invalid')


def _verify_target_bytes(before, target, package_app):
    """The byte-level target rules, re-derived from scratch."""
    require(len(before) == len(target) == SIZE, 'full 2MiB images required')
    require(target[:BANK_B] == before[:BANK_B], 'target modifies [0, 0xee000)')
    require(target[BANK_B_END:] == before[BANK_B_END:],
            'target modifies [0x1da000, 0x200000)')

    header_before, old_app = decode_bank(before, BANK_B)
    header_target = encode(target[BANK_B:BANK_B + 32])
    require(crc(header_target[2:]) == struct.unpack_from('<H', header_target)[0],
            'target bank-B header CRC invalid')
    require(header_target[6:8] == header_before[6:8],
            'target bank-B sequence field changed')
    require(struct.unpack_from('<H', header_target, 6)[0] == SEQ_B,
            'target bank-B sequence is not 3')
    require(header_target[12:32] == header_before[12:32],
            'target bank-B device fields changed')
    require(header_target[2:4] == struct.pack('<H', crc(sfc(package_app))),
            'target plaintext CRC does not match the package app')
    require(header_target[4:6] == struct.pack('<H', crc(package_app)),
            'target cipher CRC does not match the package app')
    new_len = struct.unpack_from('<I', header_target, 8)[0]
    require(new_len == len(package_app), 'target length field does not match the package app')

    new_end = BANK_B + 32 + new_len
    old_end = BANK_B + 32 + len(old_app)
    require(target[BANK_B + 32:new_end] == package_app, 'target app region is not the package app')
    fill_end = max(new_end, old_end)
    if fill_end > new_end:
        require(target[new_end:fill_end] == b'\xff' * (fill_end - new_end),
                'old tail is not cleared to 0xff up to the old app end')
    require(target[fill_end:BANK_B_END] == before[fill_end:BANK_B_END],
            'bytes beyond the old app end are not preserved')
