"""Offline acceptance-profile tests. Never opens USB or downloads firmware.

Tier 1 (always runs): synthetic pinned identities — deterministic application
bytes in the real 2MiB layout (bank B at 0xee000, VM at 0x1da000, resources
beyond) exercise the full logic: plan geometry, target byte rules, independent
verification, from-state/package pin refusals, transport gates, nonce
occupation, the apply flow on a fake flash device, and per-boundary fault
injection with the interruption analyzer (H5-PRE-01 offline discrimination).
Covers all five candidates: hal5 (b79<->HAL5, regression-pinned),
f1a/f1b (HAL5->F1 flash, F1->HAL5 same-layer rollback, ALREADY_HAL5,
wrong-artifact refusals) and f2/f1ac1 (F1a->F2/F1aC1 flash, same-layer
rollback to F1a, ALREADY_F1A, wrong-artifact refusals).

Tier 2 (skips unless the real b79/HAL5 packages are present with matching
digests): the shipped pins produce valid plans against a synthetic device
built from the real application ciphertexts — concrete extents
(0xee020+929875 / +930782), precise changed-sector narrowing, rollback fill
range, and full executor round trips at real scale. A second F1 tier (real
F1a/F1b artifacts at ~/tmp/lfiximpl/ or via H3S_F1A_UFW/H3S_F1B_UFW) checks
the F1 extents (930968/930854), the F1->HAL5 fill range and round trips.
A third F2 tier (real F2/F1aC1 artifacts at ~/tmp/lfix2/ or via
H3S_F2_UFW/H3S_F1AC1_UFW) checks the F2 extents (931034 / 930968 == F1a,
the C1 equal-length table swap), the F2->F1a 66B fill range and round trips.
"""
import json
import os
from pathlib import Path
import struct
import tempfile
import types
import unittest

import accept_profile as ap
from accept_profile import (BANK_B, BANK_B_END, SECTOR, SIZE, VM_BEGIN, VM_END,
                            changed_sectors, decode_bank, encode, make_plan,
                            new_nonce, rebind_live_target)
from accept_usb import (AcceptRestoreTransport, Journal, NoncePool,
                        analyze_interrupted, apply_direction, cmd_plan,
                        execute_plan, rebind_recovery_target, save_persistent)
from accept_verify import verify_plan
from image import crc, good_flash, sfc, sha

ROOT = Path(__file__).resolve().parents[2]


def make_header(seq, app, device_fields):
    header = bytearray(32)
    struct.pack_into('<H', header, 2, crc(sfc(app)))       # plaintext CRC
    struct.pack_into('<H', header, 4, crc(app))            # cipher CRC
    struct.pack_into('<H', header, 6, seq)
    struct.pack_into('<I', header, 8, len(app))
    header[12:32] = device_fields
    struct.pack_into('<H', header, 0, crc(header[2:]))
    return bytes(header)


def make_ufw_package(app):
    """UFW container with one flash.bin entry; the package bank-A header is a
    stock header (seq 0, device fields 0xff) exactly like the real packages."""
    flash = bytearray(b'\xff' * 942080)
    flash[0:0x2000] = bytes((i * 7 + 3) & 0xff for i in range(0x2000))  # boot filler
    flash[0x2000:0x2020] = encode(make_header(0, app, b'\xff' * 20))
    flash[0x2020:0x2020 + len(app)] = app
    ufw = bytearray(b'\x11' * 64)
    directory = struct.pack('<HHHHIII44s16s', 0, 0, 0, 0, 0x600, 942080, 942080,
                            b'', b'flash.bin')
    ufw[64:144] = encode(directory)
    ufw += b'\xff' * (0x600 - len(ufw))
    ufw += flash
    ufw += b'\xff' * (1326848 - len(ufw))
    return bytes(ufw)


DEVICE_FIELDS = bytes(range(20))


class FakeFlash(AcceptRestoreTransport):
    """Fake BR28 NOR: applies FB01/FB04/FD05/FC0A to a bytearray. Fault modes
    chosen per (opcode, 1-based call index) by `fail`: 'before' raises before
    the command is applied; 'applied_no_response' applies then raises (the
    device-changed/host-uninformed window); 'erase_stuck' leaves non-FF bits
    after an erase; 'corrupt_program' programs wrong bytes with a good
    protocol response (stuck-bit style corruption)."""

    def __init__(self, image, fail=None):
        self.image = bytearray(image)
        self.tag = 0
        self.writing = False
        self.live_target = None
        self.order = ()
        self.cursor = None
        self.erase_verified = False
        self.fb01_calls = 0
        self.fb04_calls = 0
        self.fail = fail

    def close(self):
        pass

    def exchange(self, cdb, size=0, data=None):
        opcode = int.from_bytes(cdb[:2], 'big')
        if opcode == 0x12:
            return b'BR28    UBOOT1.00        ' + b'\x00' * 4
        if opcode == 0xFC0A:
            return b'\xfc\x0a\x03' + b'\x00' * 13
        index = (self.fb01_calls + 1) if opcode == 0xFB01 else (self.fb04_calls + 1)
        mode = self.fail(opcode, index) if self.fail else None
        if mode == 'before':
            raise RuntimeError('injected fault before opcode %s call %d' % (hex(opcode), index))
        address = int.from_bytes(cdb[2:6], 'big')
        length = int.from_bytes(cdb[6:8], 'big')
        if opcode == 0xFD05:
            return bytes(self.image[address:address + length])
        if opcode == 0xFB01:
            self.fb01_calls += 1
            if mode == 'erase_stuck':
                self.image[address:address + SECTOR] = b'\xff' * (SECTOR - 1) + b'\x00'
            else:
                self.image[address:address + SECTOR] = b'\xff' * SECTOR
            response = b'\xfb\x01' + b'\x00' * 14
        elif opcode == 0xFB04:
            self.fb04_calls += 1
            block = data if mode != 'corrupt_program' else bytes(b ^ 1 for b in data)
            self.image[address:address + 512] = block
            response = b'\xfb\x04' + b'\x00' * 14
        else:
            raise ValueError('opcode not allowed in fake: ' + hex(opcode))
        if mode == 'applied_no_response':
            raise RuntimeError('injected fault after applying opcode %s call %d'
                               % (hex(opcode), index))
        return response


def journal_rows(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line]


def target_with_vm(target, fresh):
    out = bytearray(target)
    out[VM_BEGIN:VM_END] = fresh[VM_BEGIN:VM_END]
    return bytes(out)


class SyntheticWorld:
    """Deterministic device + packages with the real layout geometry. The
    device models the post-2026-11-10-restoration shape: bank B carries the
    b79 app, 0xff up to the old r2 extent, r2-era residue beyond. App lengths
    mirror the real ordering b79 < HAL5 < F1b < F1a < F2 (F1 apps are longer
    than their HAL5 base: +186/+72 bytes; F1aC1 == F1a in length — C1 is an
    equal-length ANC table byte swap; F2 = F1a + 66 — the C2 close patch)."""

    def __init__(self, b79_len=9000, hal5_len=9500, r2_len=9200,
                 f1b_len=9572, f1a_len=9686, f2_len=None, f1ac1_len=None):
        f2_len = f1a_len + 66 if f2_len is None else f2_len
        f1ac1_len = f1a_len if f1ac1_len is None else f1ac1_len
        self.b79_app = bytes((i * i + 11) % 251 for i in range(b79_len))
        self.hal5_app = bytes(((i + 7) * 3 + 5) % 251 for i in range(hal5_len))
        self.f1b_app = bytes(((i + 11) * 5 + 3) % 251 for i in range(f1b_len))
        self.f1a_app = bytes(((i + 13) * 7 + 1) % 251 for i in range(f1a_len))
        self.f2_app = bytes(((i + 17) * 9 + 5) % 251 for i in range(f2_len))
        self.f1ac1_app = bytes(((i + 19) * 11 + 7) % 251 for i in range(f1ac1_len))
        self.r2_len = r2_len
        tmp = Path(tempfile.mkdtemp())
        self.b79_ufw_path = tmp / 'b79-synth.ufw'
        self.hal5_ufw_path = tmp / 'hal5-synth.ufw'
        self.f1a_ufw_path = tmp / 'f1a-synth.ufw'
        self.f1b_ufw_path = tmp / 'f1b-synth.ufw'
        self.f2_ufw_path = tmp / 'f2-synth.ufw'
        self.f1ac1_ufw_path = tmp / 'f1ac1-synth.ufw'
        self.b79_ufw = make_ufw_package(self.b79_app)
        self.hal5_ufw = make_ufw_package(self.hal5_app)
        self.f1a_ufw = make_ufw_package(self.f1a_app)
        self.f1b_ufw = make_ufw_package(self.f1b_app)
        self.f2_ufw = make_ufw_package(self.f2_app)
        self.f1ac1_ufw = make_ufw_package(self.f1ac1_app)
        self.b79_ufw_path.write_bytes(self.b79_ufw)
        self.hal5_ufw_path.write_bytes(self.hal5_ufw)
        self.f1a_ufw_path.write_bytes(self.f1a_ufw)
        self.f1b_ufw_path.write_bytes(self.f1b_ufw)
        self.f2_ufw_path.write_bytes(self.f2_ufw)
        self.f1ac1_ufw_path.write_bytes(self.f1ac1_ufw)
        self.b79_device = self._device_image(self.b79_app)
        self.hal5_device = self._device_image(self.hal5_app)
        self.f1a_device = self._device_image(self.f1a_app)

    def _device_image(self, b_app):
        image = bytearray(b'\xff' * SIZE)
        image[0:0x2000] = bytes((i * 7 + 3) & 0xff for i in range(0x2000))
        image[0x2000:0x2020] = encode(make_header(2, self.b79_app, DEVICE_FIELDS))
        image[0x2020:0x2020 + len(self.b79_app)] = self.b79_app
        b_end = BANK_B + 32 + len(b_app)
        image[BANK_B:BANK_B + 32] = encode(make_header(3, b_app, DEVICE_FIELDS))
        image[BANK_B + 32:b_end] = b_app
        # r2-era residue only beyond the committed app: when b_app is longer
        # than the old r2 extent (HAL5/F1), the app covers the old 0xff gap
        residue_from = max(b_end, BANK_B + 32 + self.r2_len)
        image[b_end:residue_from] = b'\xff' * (residue_from - b_end)
        for off in range(residue_from, BANK_B_END):
            image[off] = 0x5a                                     # r2-era residue
        for off in range(VM_BEGIN, VM_END):
            image[off] = (off // 16) & 0xff
        for off in range(VM_END, SIZE):
            image[off] = (off // 32 + 1) & 0xff
        return bytes(image)


class PinnedIdentities(unittest.TestCase):
    """Tier 1: patch the shipped pins to the synthetic identities."""

    def setUp(self):
        self.world = SyntheticWorld()
        self._saved = {name: getattr(ap, name) for name in
                       ('B79_PROFILE_SHA', 'B79_APP_LEN', 'B79_APP_SHA',
                        'HAL5_UFW_SHA', 'HAL5_APP_LEN', 'HAL5_APP_SHA',
                        'F1A_UFW_SHA', 'F1A_APP_LEN', 'F1A_APP_SHA',
                        'F1B_UFW_SHA', 'F1B_APP_LEN', 'F1B_APP_SHA',
                        'F2_UFW_SHA', 'F2_APP_LEN', 'F2_APP_SHA',
                        'F1AC1_UFW_SHA', 'F1AC1_APP_LEN', 'F1AC1_APP_SHA')}
        ap.B79_PROFILE_SHA = sha(self.world.b79_ufw)
        ap.B79_APP_LEN = len(self.world.b79_app)
        ap.B79_APP_SHA = sha(self.world.b79_app)
        ap.HAL5_UFW_SHA = sha(self.world.hal5_ufw)
        ap.HAL5_APP_LEN = len(self.world.hal5_app)
        ap.HAL5_APP_SHA = sha(self.world.hal5_app)
        ap.F1A_UFW_SHA = sha(self.world.f1a_ufw)
        ap.F1A_APP_LEN = len(self.world.f1a_app)
        ap.F1A_APP_SHA = sha(self.world.f1a_app)
        ap.F1B_UFW_SHA = sha(self.world.f1b_ufw)
        ap.F1B_APP_LEN = len(self.world.f1b_app)
        ap.F1B_APP_SHA = sha(self.world.f1b_app)
        ap.F2_UFW_SHA = sha(self.world.f2_ufw)
        ap.F2_APP_LEN = len(self.world.f2_app)
        ap.F2_APP_SHA = sha(self.world.f2_app)
        ap.F1AC1_UFW_SHA = sha(self.world.f1ac1_ufw)
        ap.F1AC1_APP_LEN = len(self.world.f1ac1_app)
        ap.F1AC1_APP_SHA = sha(self.world.f1ac1_app)

    def tearDown(self):
        ap.__dict__.update(self._saved)


class PlanTests(PinnedIdentities):
    def test_flash_plan_geometry_and_rules(self):
        w = self.world
        target, plan = make_plan('flash', w.b79_device, w.hal5_ufw, new_nonce())
        verify_plan(plan, w.b79_device, target, w.hal5_app)
        self.assertEqual(plan['profile'], 'accept-hal5-flash-v1')
        self.assertEqual(plan['geometry']['old_extent'], BANK_B + 32 + len(w.b79_app))
        self.assertEqual(plan['geometry']['new_extent'], BANK_B + 32 + len(w.hal5_app))
        self.assertIsNone(plan['geometry']['fill_0ff_range'])   # new app is longer
        order = plan['write_order']
        self.assertEqual(order[:-1], sorted(p for p in order if p != BANK_B))
        self.assertEqual(order[-1], BANK_B)                     # boot-info last
        self.assertTrue(all(BANK_B <= p < BANK_B_END for p in order))
        self.assertEqual(set(order), changed_sectors(w.b79_device, target))
        # header: only +0..6 and +8..12 differ; seq/device fields preserved
        hb = encode(w.b79_device[BANK_B:BANK_B + 32])
        ht = encode(target[BANK_B:BANK_B + 32])
        self.assertEqual(ht[6:8], hb[6:8])
        self.assertEqual(struct.unpack_from('<H', ht, 6)[0], 3)
        self.assertEqual(ht[12:32], DEVICE_FIELDS)
        self.assertEqual(struct.unpack_from('<I', ht, 8)[0], len(w.hal5_app))
        self.assertEqual(ht[2:4], struct.pack('<H', crc(sfc(w.hal5_app))))
        self.assertEqual(ht[4:6], struct.pack('<H', crc(w.hal5_app)))
        # byte rules: app region, preserved ranges, residue beyond new end kept
        new_end = BANK_B + 32 + len(w.hal5_app)
        self.assertEqual(target[BANK_B + 32:new_end], w.hal5_app)
        self.assertEqual(target[:BANK_B], w.b79_device[:BANK_B])
        self.assertEqual(target[BANK_B_END:], w.b79_device[BANK_B_END:])
        self.assertEqual(target[new_end:BANK_B_END], w.b79_device[new_end:BANK_B_END])

    def test_verify_refuses_bit_flips_and_vm_touching_target(self):
        w = self.world
        target, plan = make_plan('flash', w.b79_device, w.hal5_ufw, new_nonce())
        app = w.hal5_app
        for name, mutate in (
                ('app byte', lambda t: t[:BANK_B + 33] + bytes([t[BANK_B + 33] ^ 1]) + t[BANK_B + 34:]),
                ('VM byte', lambda t: t[:VM_BEGIN + 5] + bytes([t[VM_BEGIN + 5] ^ 1]) + t[VM_BEGIN + 6:]),
                ('bank A byte', lambda t: t[:0x2020] + bytes([t[0x2020] ^ 1]) + t[0x2021:])):
            with self.subTest(mutated=name):
                with self.assertRaises(ValueError):
                    verify_plan(plan, w.b79_device, mutate(target), app)

    def test_rollback_plan_fill_and_already_b79(self):
        w = self.world
        hal5_target, _ = make_plan('flash', w.b79_device, w.hal5_ufw, new_nonce())
        target, plan = make_plan('rollback', hal5_target, w.b79_ufw, new_nonce())
        verify_plan(plan, hal5_target, target, w.b79_app)
        new_end = BANK_B + 32 + len(w.b79_app)
        old_end = BANK_B + 32 + len(w.hal5_app)
        self.assertEqual(plan['geometry']['fill_0ff_range'], [new_end, old_end])
        self.assertEqual(target[new_end:old_end], b'\xff' * (old_end - new_end))
        self.assertEqual(target[old_end:BANK_B_END], hal5_target[old_end:BANK_B_END])
        again, report = make_plan('rollback', w.b79_device, w.b79_ufw, new_nonce())
        self.assertIsNone(again)
        self.assertEqual(report['status'], 'ALREADY_B79')
        self.assertTrue(report['zero_write'])

    def test_wrong_from_states_refused(self):
        w = self.world
        cases = {}
        mixed = bytearray(w.b79_device)
        mixed[BANK_B + 40] ^= 1                                    # unknown bank B content
        cases['mixed bank B'] = bytes(mixed)
        bad_seq = bytearray(w.b79_device)
        header = bytearray(encode(w.b79_device[BANK_B:BANK_B + 32]))
        struct.pack_into('<H', header, 6, 2)                       # B seq 2
        struct.pack_into('<H', header, 0, crc(header[2:]))
        bad_seq[BANK_B:BANK_B + 32] = encode(bytes(header))
        cases['bank B sequence'] = bytes(bad_seq)
        bad_a = bytearray(w.b79_device)
        bad_a[0x2020 + 3] ^= 1                                     # bank A no longer b79
        cases['bank A content'] = bytes(bad_a)
        bad_crc = bytearray(w.b79_device)
        bad_crc[BANK_B + 2] ^= 1                                   # header CRC broken
        cases['header CRC'] = bytes(bad_crc)
        for name, before in cases.items():
            with self.subTest(case=name):
                with self.assertRaises(ValueError):
                    make_plan('flash', before, w.hal5_ufw, new_nonce())

    def test_package_pins_refused(self):
        w = self.world
        flipped = w.hal5_ufw[:0x600 + 0x2020] + bytes([w.hal5_ufw[0x600 + 0x2020] ^ 1]) \
            + w.hal5_ufw[0x600 + 0x2021:]
        with self.assertRaisesRegex(ValueError, 'pinned UFW'):
            make_plan('flash', w.b79_device, flipped, new_nonce())
        other_len = make_ufw_package(w.hal5_app[:-512])            # valid CRCs, wrong length pin
        with self.assertRaises(ValueError):
            make_plan('flash', w.b79_device, other_len, new_nonce())
        with self.assertRaises(ValueError):                        # wrong direction package
            make_plan('flash', w.b79_device, w.b79_ufw, new_nonce())

    def test_bad_nonce_shape_refused(self):
        with self.assertRaises(ValueError):
            make_plan('flash', self.world.b79_device, self.world.hal5_ufw, 'abc')

    def test_rebind_rules(self):
        w = self.world
        target, plan = make_plan('flash', w.b79_device, w.hal5_ufw, new_nonce())
        drifted = bytearray(w.b79_device)
        drifted[VM_BEGIN + 64] ^= 1                                # VM drift is allowed
        drifted = bytes(drifted)
        live = rebind_live_target(w.b79_device, target, drifted, drifted)
        self.assertEqual(live[VM_BEGIN:VM_END], drifted[VM_BEGIN:VM_END])
        for p in plan['write_order']:
            self.assertEqual(live[p:p + SECTOR], target[p:p + SECTOR])
        with self.assertRaisesRegex(ValueError, 'reads differ'):
            rebind_live_target(w.b79_device, target, w.b79_device, drifted)
        for name, offset in (('resource', VM_END + 8), ('bank A', 0x2020), ('boot', 0x10)):
            with self.subTest(drift=name):
                bad = bytearray(w.b79_device)
                bad[offset] ^= 1
                with self.assertRaisesRegex(ValueError, 'do not write'):
                    rebind_live_target(w.b79_device, target, bytes(bad), bytes(bad))


class F1PlanTests(PinnedIdentities):
    """F1a/F1b directions: flash from a HAL5 bank B, same-layer rollback to
    HAL5, ALREADY_HAL5 zero-write report, and explicit wrong-package refusals
    (the artifact digests must gate, not just the app layer)."""

    def test_f1_flash_from_hal5_state(self):
        w = self.world
        for cand, ufw, app in (('f1a', w.f1a_ufw, w.f1a_app),
                               ('f1b', w.f1b_ufw, w.f1b_app)):
            with self.subTest(candidate=cand):
                target, plan = make_plan('flash', w.hal5_device, ufw, new_nonce(), cand)
                verify_plan(plan, w.hal5_device, target, app)
                self.assertEqual(plan['profile'], 'accept-%s-flash-v1' % cand)
                self.assertEqual(plan['candidate'], cand)
                self.assertEqual(plan['geometry']['old_extent'], BANK_B + 32 + len(w.hal5_app))
                self.assertEqual(plan['geometry']['new_extent'], BANK_B + 32 + len(app))
                self.assertIsNone(plan['geometry']['fill_0ff_range'])  # F1 apps are longer
                order = plan['write_order']
                self.assertEqual(order[-1], BANK_B)
                self.assertEqual(set(order), changed_sectors(w.hal5_device, target))
                self.assertEqual(target[:BANK_B], w.hal5_device[:BANK_B])
                self.assertEqual(target[BANK_B_END:], w.hal5_device[BANK_B_END:])
                _, app_b = decode_bank(target, BANK_B)
                self.assertEqual(sha(app_b), sha(app))

    def test_f1_flash_from_wrong_states_refused(self):
        w = self.world
        for name, before in (('both banks b79 (F1 never preceded by HAL5 flash)',
                              w.b79_device),
                             ('bank B already F1a', make_plan(
                                 'flash', w.hal5_device, w.f1a_ufw, new_nonce(), 'f1a')[0])):
            with self.subTest(case=name):
                with self.assertRaises(ValueError):
                    make_plan('flash', before, w.f1a_ufw, new_nonce(), 'f1a')

    def test_f1_rollback_to_hal5_and_already_hal5(self):
        w = self.world
        f1a_target, _ = make_plan('flash', w.hal5_device, w.f1a_ufw, new_nonce(), 'f1a')
        target, plan = make_plan('rollback', f1a_target, w.hal5_ufw, new_nonce(), 'f1a')
        verify_plan(plan, f1a_target, target, w.hal5_app)
        self.assertEqual(plan['profile'], 'accept-f1a-rollback-v1')
        new_end = BANK_B + 32 + len(w.hal5_app)
        old_end = BANK_B + 32 + len(w.f1a_app)
        self.assertEqual(plan['geometry']['fill_0ff_range'], [new_end, old_end])
        self.assertEqual(target[new_end:old_end], b'\xff' * (old_end - new_end))
        _, app_b = decode_bank(target, BANK_B)
        self.assertEqual(sha(app_b), sha(w.hal5_app))
        # bank B already HAL5: zero-write report, never an erase
        again, report = make_plan('rollback', w.hal5_device, w.hal5_ufw, new_nonce(), 'f1a')
        self.assertIsNone(again)
        self.assertEqual(report['status'], 'ALREADY_HAL5')
        self.assertTrue(report['zero_write'])

    def test_f1_package_digest_refusals(self):
        w = self.world
        flipped = w.f1a_ufw[:0x600 + 0x2020] + bytes([w.f1a_ufw[0x600 + 0x2020] ^ 1]) \
            + w.f1a_ufw[0x600 + 0x2021:]
        for name, ufw, cand in (
                ('single flipped byte in the F1a artifact', flipped, 'f1a'),
                ('F1b artifact offered as F1a', w.f1b_ufw, 'f1a'),
                ('HAL5 artifact offered as F1a', w.hal5_ufw, 'f1a'),
                ('F1a artifact for the hal5 direction', w.f1a_ufw, 'hal5')):
            with self.subTest(case=name):
                with self.assertRaisesRegex(ValueError, 'pinned UFW'):
                    make_plan('flash', w.hal5_device, ufw, new_nonce(), cand)
        f1a_target, _ = make_plan('flash', w.hal5_device, w.f1a_ufw, new_nonce(), 'f1a')
        with self.assertRaisesRegex(ValueError, 'pinned UFW'):     # wrong rollback package
            make_plan('rollback', f1a_target, w.f1b_ufw, new_nonce(), 'f1a')
        with self.assertRaisesRegex(ValueError, 'unknown candidate'):
            make_plan('flash', w.hal5_device, w.f1a_ufw, new_nonce(), 'f1x')


class F2PlanTests(PinnedIdentities):
    """F2/F1aC1 directions: flash from an F1a bank B (the 2026-11-19 device
    state), same-layer rollback to F1a, ALREADY_F1A zero-write report, and
    explicit wrong-package refusals — including F1a vs F1aC1, which differ
    only by content at an equal length (the digest gate, not the length pin,
    must separate them)."""

    def test_f2_flash_from_f1a_state(self):
        w = self.world
        for cand, ufw, app, extra in (('f2', w.f2_ufw, w.f2_app, 66),
                                      ('f1ac1', w.f1ac1_ufw, w.f1ac1_app, 0)):
            with self.subTest(candidate=cand):
                target, plan = make_plan('flash', w.f1a_device, ufw, new_nonce(), cand)
                verify_plan(plan, w.f1a_device, target, app)
                self.assertEqual(plan['profile'], 'accept-%s-flash-v1' % cand)
                self.assertEqual(plan['candidate'], cand)
                self.assertEqual(plan['geometry']['old_extent'], BANK_B + 32 + len(w.f1a_app))
                self.assertEqual(plan['geometry']['new_extent'],
                                 BANK_B + 32 + len(w.f1a_app) + extra)
                self.assertIsNone(plan['geometry']['fill_0ff_range'])  # never shorter
                order = plan['write_order']
                self.assertEqual(order[-1], BANK_B)
                self.assertEqual(set(order), changed_sectors(w.f1a_device, target))
                self.assertEqual(target[:BANK_B], w.f1a_device[:BANK_B])
                self.assertEqual(target[BANK_B_END:], w.f1a_device[BANK_B_END:])
                _, app_b = decode_bank(target, BANK_B)
                self.assertEqual(sha(app_b), sha(app))

    def test_f2_flash_from_wrong_states_refused(self):
        w = self.world
        for name, before in (('bank B HAL5 (F2 never preceded by HAL5 rollback)',
                              w.hal5_device),
                             ('both banks b79', w.b79_device),
                             ('bank B already F2', make_plan(
                                 'flash', w.f1a_device, w.f2_ufw, new_nonce(), 'f2')[0])):
            with self.subTest(case=name):
                with self.assertRaises(ValueError):
                    make_plan('flash', before, w.f2_ufw, new_nonce(), 'f2')

    def test_f2_rollback_to_f1a_and_already_f1a(self):
        w = self.world
        f2_target, _ = make_plan('flash', w.f1a_device, w.f2_ufw, new_nonce(), 'f2')
        target, plan = make_plan('rollback', f2_target, w.f1a_ufw, new_nonce(), 'f2')
        verify_plan(plan, f2_target, target, w.f1a_app)
        self.assertEqual(plan['profile'], 'accept-f2-rollback-v1')
        new_end = BANK_B + 32 + len(w.f1a_app)
        old_end = BANK_B + 32 + len(w.f2_app)
        self.assertEqual(plan['geometry']['fill_0ff_range'], [new_end, old_end])
        self.assertEqual(target[new_end:old_end], b'\xff' * (old_end - new_end))
        _, app_b = decode_bank(target, BANK_B)
        self.assertEqual(sha(app_b), sha(w.f1a_app))
        # F1aC1 rolls back over an equal-length app: no fill range at all
        f1ac1_target, _ = make_plan('flash', w.f1a_device, w.f1ac1_ufw, new_nonce(), 'f1ac1')
        target_c, plan_c = make_plan('rollback', f1ac1_target, w.f1a_ufw, new_nonce(), 'f1ac1')
        verify_plan(plan_c, f1ac1_target, target_c, w.f1a_app)
        self.assertEqual(plan_c['profile'], 'accept-f1ac1-rollback-v1')
        self.assertIsNone(plan_c['geometry']['fill_0ff_range'])
        # bank B already F1a: zero-write report, never an erase
        for cand in ('f2', 'f1ac1'):
            with self.subTest(candidate=cand):
                again, report = make_plan('rollback', w.f1a_device, w.f1a_ufw,
                                          new_nonce(), cand)
                self.assertIsNone(again)
                self.assertEqual(report['status'], 'ALREADY_F1A')
                self.assertTrue(report['zero_write'])

    def test_f2_package_digest_refusals(self):
        w = self.world
        flipped = w.f2_ufw[:0x600 + 0x2020] + bytes([w.f2_ufw[0x600 + 0x2020] ^ 1]) \
            + w.f2_ufw[0x600 + 0x2021:]
        for name, ufw, cand in (
                ('single flipped byte in the F2 artifact', flipped, 'f2'),
                ('F1aC1 artifact offered as F2', w.f1ac1_ufw, 'f2'),
                ('F1a artifact offered as F2', w.f1a_ufw, 'f2'),
                ('F2 artifact offered as F1aC1', w.f2_ufw, 'f1ac1'),
                ('F1a artifact offered as F1aC1 (equal length, '
                 'different content)', w.f1a_ufw, 'f1ac1'),
                ('F2 artifact for the f1a direction', w.f2_ufw, 'f1a')):
            with self.subTest(case=name):
                with self.assertRaisesRegex(ValueError, 'pinned UFW'):
                    make_plan('flash', w.f1a_device, ufw, new_nonce(), cand)
        f2_target, _ = make_plan('flash', w.f1a_device, w.f2_ufw, new_nonce(), 'f2')
        with self.assertRaisesRegex(ValueError, 'pinned UFW'):     # wrong rollback package
            make_plan('rollback', f2_target, w.hal5_ufw, new_nonce(), 'f2')
        with self.assertRaisesRegex(ValueError, 'unknown candidate'):
            make_plan('flash', w.f1a_device, w.f2_ufw, new_nonce(), 'f2x')


class TransportGateTests(PinnedIdentities):
    def _fresh(self, target, plan):
        t = FakeFlash(self.world.b79_device)
        t.bind(target, plan['write_order'])
        t.writing = True
        return t

    def test_gate_closed_and_outside_sector(self):
        w = self.world
        target, plan = make_plan('flash', w.b79_device, w.hal5_ufw, new_nonce())
        first = plan['write_order'][0]
        t = FakeFlash(w.b79_device)
        t.bind(target, plan['write_order'])
        cdb = (b'\xfb\x01' + first.to_bytes(4, 'big')).ljust(16, b'\xff')
        with self.assertRaisesRegex(ValueError, 'gate closed'):
            t.execute(cdb, 16)
        # every violation permanently closes the gate for that transport
        t.writing = True
        other = (b'\xfb\x01' + (first + SECTOR).to_bytes(4, 'big')).ljust(16, b'\xff')
        with self.assertRaisesRegex(ValueError, 'no active planned sector'):
            t.execute(other, 16)
        self.assertFalse(t.writing)
        t2 = self._fresh(target, plan)
        t2.begin_sector(first)
        with self.assertRaisesRegex(ValueError, 'outside the active'):
            t2.execute(other, 16)
        self.assertFalse(t2.writing)                               # violation closed the gate
        # a sector outside the plan order is refused even as a begin
        t3 = self._fresh(target, plan)
        with self.assertRaisesRegex(ValueError, 'outside plan order'):
            t3.begin_sector(BANK_B_END)                            # outside the audited bank

    def test_program_requires_verified_erase_and_matching_block(self):
        w = self.world
        target, plan = make_plan('flash', w.b79_device, w.hal5_ufw, new_nonce())
        sector = plan['write_order'][0]
        block = target[sector:sector + 512]
        program = (b'\xfb\x04' + sector.to_bytes(4, 'big') + (512).to_bytes(2, 'big')
                   + b'\0' + crc(block).to_bytes(2, 'little')).ljust(16, b'\xff')
        erase = (b'\xfb\x01' + sector.to_bytes(4, 'big')).ljust(16, b'\xff')

        t = self._fresh(target, plan)                              # program before verified erase
        t.begin_sector(sector)
        t.execute(erase, 16)
        with self.assertRaisesRegex(ValueError, 'verified erase'):
            t.execute(program, 512, block)
        self.assertFalse(t.writing)

        t = self._fresh(target, plan)                              # wrong block data
        t.begin_sector(sector)
        t.execute(erase, 16)
        t.mark_erase_verified()
        wrong = bytes(b ^ 3 for b in block[:16]) + block[16:]
        with self.assertRaisesRegex(ValueError, 'differs from the live target'):
            t.execute(program, 512, wrong)
        self.assertFalse(t.writing)

        t = self._fresh(target, plan)                              # wrong length/CRC fields
        t.begin_sector(sector)
        t.execute(erase, 16)
        t.mark_erase_verified()
        bad_crc = (b'\xfb\x04' + sector.to_bytes(4, 'big') + (512).to_bytes(2, 'big')
                   + b'\0\x00\x00').ljust(16, b'\xff')
        with self.assertRaisesRegex(ValueError, 'CRC'):
            t.execute(bad_crc, 512, block)
        self.assertFalse(t.writing)

        t = self._fresh(target, plan)                              # matching block passes
        t.begin_sector(sector)
        t.execute(erase, 16)
        t.mark_erase_verified()
        t.execute(program, 512, block)

        t = self._fresh(target, plan)                              # misaligned program
        t.begin_sector(sector)
        t.mark_erase_verified()
        misaligned = (b'\xfb\x04' + (sector + 1).to_bytes(4, 'big') + (512).to_bytes(2, 'big')
                      + b'\0' + crc(block).to_bytes(2, 'little')).ljust(16, b'\xff')
        with self.assertRaisesRegex(ValueError, 'aligned'):
            t.execute(misaligned, 512, block)


class ApplyFlowTests(PinnedIdentities):
    def _workdir(self, device):
        work = Path(tempfile.mkdtemp())
        save_persistent(work / 'before-pass1.bin', device)
        save_persistent(work / 'before-pass2.bin', device)
        return work

    def _args(self, work, package, nonce, nonce_dir=None, candidate='hal5'):
        return types.SimpleNamespace(apply=True, confirm_nonce=nonce, workdir=str(work),
                                     package=str(package), loader='unused', candidate=candidate,
                                     nonce_dir=nonce_dir or Path(tempfile.mkdtemp()))

    def test_plan_cmd_then_full_apply_and_nonce_single_use(self):
        w = self.world
        work = self._workdir(w.b79_device)
        cmd_plan('flash', types.SimpleNamespace(workdir=str(work),
                                                package=str(w.hal5_ufw_path)))
        plan = json.loads((work / 'plan.json').read_text())
        self.assertTrue((work / 'target.bin').exists())
        nonce_dir = Path(tempfile.mkdtemp())
        fake = FakeFlash(w.b79_device)
        apply_direction('flash', self._args(work, w.hal5_ufw_path, plan['nonce'], nonce_dir),
                        transport_factory=lambda: fake)
        live_target = (work / 'live-target.bin').read_bytes()
        self.assertEqual(bytes(fake.image), live_target)
        self.assertEqual(live_target[:BANK_B], w.b79_device[:BANK_B])
        self.assertEqual(live_target[BANK_B_END:], w.b79_device[BANK_B_END:])
        self.assertTrue((work / 'restore.jsonl').exists())
        rows = journal_rows(work / 'restore.jsonl')
        self.assertEqual([r['stage'] for r in rows][-1], 'FLASH_ACCEPT_VERIFIED')
        # nonce single use: refused before any new journal is created
        with self.assertRaisesRegex(ValueError, 'nonce already consumed'):
            apply_direction('flash', self._args(work, w.hal5_ufw_path, plan['nonce'], nonce_dir),
                            transport_factory=lambda: FakeFlash(w.b79_device))

    def test_f1a_plan_cmd_then_apply_roundtrip(self):
        w = self.world
        work = self._workdir(w.hal5_device)                # device already on HAL5
        cmd_plan('flash', types.SimpleNamespace(workdir=str(work),
                                                package=str(w.f1a_ufw_path), candidate='f1a'))
        plan = json.loads((work / 'plan.json').read_text())
        self.assertEqual(plan['candidate'], 'f1a')
        self.assertEqual(plan['profile'], 'accept-f1a-flash-v1')
        self.assertTrue((work / 'target.bin').exists())
        fake = FakeFlash(w.hal5_device)
        apply_direction('flash', self._args(work, w.f1a_ufw_path, plan['nonce'],
                                            candidate='f1a'),
                        transport_factory=lambda: fake)
        live_target = (work / 'live-target.bin').read_bytes()
        self.assertEqual(bytes(fake.image), live_target)
        _, app_b = decode_bank(live_target, BANK_B)
        self.assertEqual(sha(app_b), sha(w.f1a_app))
        rows = journal_rows(work / 'restore.jsonl')
        self.assertEqual([r['stage'] for r in rows][-1], 'FLASH_ACCEPT_VERIFIED')
        # candidate mismatch between plan.json and the CLI is refused pre-erase
        work2 = self._workdir(w.hal5_device)
        cmd_plan('flash', types.SimpleNamespace(workdir=str(work2),
                                                package=str(w.f1a_ufw_path), candidate='f1a'))
        plan2 = json.loads((work2 / 'plan.json').read_text())
        fake2 = FakeFlash(w.hal5_device)
        with self.assertRaisesRegex(ValueError, 'candidate mismatch'):
            apply_direction('flash', self._args(work2, w.f1a_ufw_path, plan2['nonce']),
                            transport_factory=lambda: fake2)
        self.assertEqual(fake2.fb01_calls, 0)

    def test_f2_plan_cmd_then_apply_roundtrip(self):
        w = self.world
        work = self._workdir(w.f1a_device)                # device already on F1a
        cmd_plan('flash', types.SimpleNamespace(workdir=str(work),
                                                package=str(w.f2_ufw_path), candidate='f2'))
        plan = json.loads((work / 'plan.json').read_text())
        self.assertEqual(plan['candidate'], 'f2')
        self.assertEqual(plan['profile'], 'accept-f2-flash-v1')
        self.assertTrue((work / 'target.bin').exists())
        fake = FakeFlash(w.f1a_device)
        apply_direction('flash', self._args(work, w.f2_ufw_path, plan['nonce'],
                                            candidate='f2'),
                        transport_factory=lambda: fake)
        live_target = (work / 'live-target.bin').read_bytes()
        self.assertEqual(bytes(fake.image), live_target)
        _, app_b = decode_bank(live_target, BANK_B)
        self.assertEqual(sha(app_b), sha(w.f2_app))
        rows = journal_rows(work / 'restore.jsonl')
        self.assertEqual([r['stage'] for r in rows][-1], 'FLASH_ACCEPT_VERIFIED')
        # the control candidate is the same single step from the same state
        work_c = self._workdir(w.f1a_device)
        cmd_plan('flash', types.SimpleNamespace(workdir=str(work_c),
                                                package=str(w.f1ac1_ufw_path),
                                                candidate='f1ac1'))
        plan_c = json.loads((work_c / 'plan.json').read_text())
        self.assertEqual(plan_c['profile'], 'accept-f1ac1-flash-v1')
        fake_c = FakeFlash(w.f1a_device)
        apply_direction('flash', self._args(work_c, w.f1ac1_ufw_path, plan_c['nonce'],
                                            candidate='f1ac1'),
                        transport_factory=lambda: fake_c)
        _, app_c = decode_bank((work_c / 'live-target.bin').read_bytes(), BANK_B)
        self.assertEqual(sha(app_c), sha(w.f1ac1_app))

    def test_apply_rejects_nonvm_drift_and_old_plan_reuse(self):
        w = self.world
        work = self._workdir(w.b79_device)
        target, plan = make_plan('flash', w.b79_device, w.hal5_ufw, new_nonce())
        save_persistent(work / 'target.bin', target)
        save_persistent(work / 'plan.json',
                        (json.dumps(plan, indent=2, sort_keys=True) + '\n').encode())
        drifted = bytearray(w.b79_device)
        drifted[VM_END + 16] ^= 1
        fake = FakeFlash(bytes(drifted))
        with self.assertRaisesRegex(ValueError, 'do not write'):
            apply_direction('flash', self._args(work, w.hal5_ufw_path, plan['nonce']),
                            transport_factory=lambda: fake)
        self.assertEqual(fake.fb01_calls, 0)                       # nothing erased
        # old plan against a device that already runs HAL5: refused pre-erase
        hal5_target, _ = make_plan('flash', w.b79_device, w.hal5_ufw, plan['nonce'])
        fake2 = FakeFlash(hal5_target)
        with self.assertRaises(ValueError):
            apply_direction('flash', self._args(work, w.hal5_ufw_path, plan['nonce']),
                            transport_factory=lambda: fake2)
        self.assertEqual(fake2.fb01_calls, 0)

    def test_tampered_plan_files_refused(self):
        w = self.world
        work = self._workdir(w.b79_device)
        target, plan = make_plan('flash', w.b79_device, w.hal5_ufw, new_nonce())
        plan['write_order'] = plan['write_order'] + [plan['write_order'][0]]  # duplicate
        save_persistent(work / 'target.bin', target)
        save_persistent(work / 'plan.json',
                        (json.dumps(plan, indent=2, sort_keys=True) + '\n').encode())
        with self.assertRaises(ValueError):                        # digest no longer matches
            apply_direction('flash', self._args(work, w.hal5_ufw_path, plan['nonce']),
                            transport_factory=lambda: FakeFlash(w.b79_device))

    def test_vm_drift_apply_keeps_fresh_vm(self):
        w = self.world
        work = self._workdir(w.b79_device)
        target, plan = make_plan('flash', w.b79_device, w.hal5_ufw, new_nonce())
        save_persistent(work / 'target.bin', target)
        save_persistent(work / 'plan.json',
                        (json.dumps(plan, indent=2, sort_keys=True) + '\n').encode())
        fresh = bytearray(w.b79_device)
        fresh[VM_BEGIN + 100] ^= 1
        fresh = bytes(fresh)
        fake = FakeFlash(fresh)
        apply_direction('flash', self._args(work, w.hal5_ufw_path, plan['nonce']),
                        transport_factory=lambda: fake)
        self.assertEqual(bytes(fake.image)[VM_BEGIN:VM_END], fresh[VM_BEGIN:VM_END])
        self.assertEqual(bytes(fake.image), (work / 'live-target.bin').read_bytes())


class InterruptionTests(PinnedIdentities):
    """Fault injection at destructive boundaries + analyzer discrimination."""

    def _run(self, target, plan, fail):
        self.rows_path = Path(tempfile.mkdtemp()) / 'restore.jsonl'
        fake = FakeFlash(self.world.b79_device, fail=fail)
        journal = Journal(self.rows_path, sync=False)
        try:
            fake.bind(target, plan['write_order'])
            fake.writing = True
            execute_plan(fake, target, self.world.b79_device, plan['write_order'], journal)
            error = None
        except BaseException as e:                                  # noqa: BLE001 - injected
            error = e
        finally:
            journal.close()
        return fake, error

    def _analyze(self, target, plan, fake, vm_mutated=False):
        fresh = bytearray(bytes(fake.image))
        if vm_mutated:
            fresh[VM_BEGIN + 4] ^= 1                                # device ran between sessions
        fresh = bytes(fresh)
        report = analyze_interrupted(plan, self.world.b79_device, target,
                                     journal_rows(self.rows_path), fresh, fresh)
        return report, fresh

    def _recover_and_finish(self, target, plan, report, fresh):
        recovery = report['recovery_plan']
        live = rebind_recovery_target(self.world.b79_device, target, fresh,
                                      recovery['write_order'])
        fake = FakeFlash(fresh)
        journal = Journal(Path(tempfile.mkdtemp()) / 'recovery.jsonl', sync=False)
        fake.bind(live, recovery['write_order'])
        fake.writing = True
        try:
            execute_plan(fake, live, fresh, recovery['write_order'], journal)
        finally:
            journal.close()
        self.assertEqual(bytes(fake.image), target_with_vm(target, fresh))

    def test_fail_before_first_erase(self):
        w = self.world
        target, plan = make_plan('flash', w.b79_device, w.hal5_ufw, new_nonce())
        fake, error = self._run(target, plan,
                                fail=lambda op, n: 'before' if op == 0xFB01 and n == 1 else None)
        self.assertIsNotNone(error)
        self.assertEqual(fake.fb01_calls, 0)
        self.assertEqual(bytes(fake.image), w.b79_device)           # zero device change
        report, fresh = self._analyze(target, plan, fake)
        self.assertEqual(report['resume_from'], plan['write_order'][0])
        self.assertEqual(report['recovery_plan']['write_order'], plan['write_order'])
        self._recover_and_finish(target, plan, report, fresh)

    def test_fail_after_erase_applied_response_lost(self):
        w = self.world
        target, plan = make_plan('flash', w.b79_device, w.hal5_ufw, new_nonce())
        fake, error = self._run(target, plan,
                                fail=lambda op, n: 'applied_no_response'
                                if op == 0xFB01 and n == 1 else None)
        self.assertIsNotNone(error)
        first = plan['write_order'][0]
        self.assertEqual(bytes(fake.image)[first:first + SECTOR], b'\xff' * SECTOR)
        report, fresh = self._analyze(target, plan, fake)
        self.assertEqual(report['resume_from'], first)
        self.assertEqual(report['journal_confirmed_complete'], [])
        self._recover_and_finish(target, plan, report, fresh)

    def test_fail_mid_blocks_and_applied_block_window(self):
        for mode, applied in (('before', 2), ('applied_no_response', 3)):
            with self.subTest(mode=mode):
                w = self.world
                target, plan = make_plan('flash', w.b79_device, w.hal5_ufw, new_nonce())
                fake, error = self._run(target, plan,
                                        fail=lambda op, k: mode if op == 0xFB04 and k == 3 else None)
                self.assertIsNotNone(error)
                first = plan['write_order'][0]
                sector = bytes(fake.image)[first:first + SECTOR]
                self.assertEqual(sector[:applied * 512], target[first:first + applied * 512])
                self.assertEqual(sector[applied * 512:], b'\xff' * (SECTOR - applied * 512))
                report, fresh = self._analyze(target, plan, fake)
                self.assertEqual(report['resume_from'], first)
                self._recover_and_finish(target, plan, report, fresh)

    def test_stuck_erase_and_corrupt_program_unexplainable(self):
        for mode, opcode in (('erase_stuck', 0xFB01), ('corrupt_program', 0xFB04)):
            with self.subTest(mode=mode):
                w = self.world
                target, plan = make_plan('flash', w.b79_device, w.hal5_ufw, new_nonce())
                fake, error = self._run(target, plan,
                                        fail=lambda op, k: mode if op == opcode and k == 1 else None)
                self.assertIsNotNone(error)
                with self.assertRaises(ValueError):                 # not a legal prefix state
                    self._analyze(target, plan, fake)

    def test_unknown_tampered_state_refused(self):
        w = self.world
        target, plan = make_plan('flash', w.b79_device, w.hal5_ufw, new_nonce())
        fake, error = self._run(target, plan,
                                fail=lambda op, n: 'before' if op == 0xFB01 and n == 2 else None)
        self.assertIsNotNone(error)
        fake.image[plan['write_order'][2] + 100] ^= 1               # tampered untouched sector
        with self.assertRaises(ValueError):
            self._analyze(target, plan, fake)

    def test_content_complete_journal_incomplete(self):
        w = self.world
        target, plan = make_plan('flash', w.b79_device, w.hal5_ufw, new_nonce())
        rows_path = Path(tempfile.mkdtemp()) / 'restore.jsonl'
        fake = FakeFlash(w.b79_device)
        journal = Journal(rows_path, sync=False)
        fake.bind(target, plan['write_order'])
        fake.writing = True
        try:
            for sector in plan['write_order']:                      # crash before final readback
                fake.begin_sector(sector)
                journal.event(stage='intent_erase', offset=sector)
                fake.cmd(0xFB01, sector.to_bytes(4, 'big'))
                journal.event(stage='erase_done', offset=sector)
                journal.event(stage='erase_verified', offset=sector)
                fake.mark_erase_verified()
                for p in range(sector, sector + SECTOR, 512):
                    journal.event(stage='intent_program', offset=p)
                    fake.cmd(0xFB04, p.to_bytes(4, 'big') + (512).to_bytes(2, 'big') + b'\0'
                             + crc(target[p:p + 512]).to_bytes(2, 'little'), data=target[p:p + 512])
                    journal.event(stage='program_done', offset=p)
                journal.event(stage='sector_verified', offset=sector)
                fake.end_sector()
        finally:
            journal.close()
        fresh = bytes(fake.image)
        report = analyze_interrupted(plan, w.b79_device, target, journal_rows(rows_path),
                                     fresh, fresh)
        self.assertIsNone(report['resume_from'])
        self.assertIsNone(report['recovery_plan'])
        self.assertEqual(len(report['content_equals_target']), len(plan['write_order']))

    def test_vm_mutation_between_sessions_ok_for_analysis(self):
        w = self.world
        target, plan = make_plan('flash', w.b79_device, w.hal5_ufw, new_nonce())
        fake, error = self._run(target, plan,
                                fail=lambda op, n: 'before' if op == 0xFB01 and n == 2 else None)
        self.assertIsNotNone(error)
        report, fresh = self._analyze(target, plan, fake, vm_mutated=True)
        self.assertEqual(report['resume_from'], plan['write_order'][1])
        self._recover_and_finish(target, plan, report, fresh)


class NoncePoolTests(unittest.TestCase):
    def test_single_occupation_in_fixed_dir(self):
        pool_dir = Path(tempfile.mkdtemp())
        pool = NoncePool(pool_dir)
        nonce = new_nonce()
        pool.consume(nonce, {'direction': 'flash'})
        self.assertTrue(pool.occupied(nonce))
        self.assertTrue(NoncePool(pool_dir).occupied(nonce))        # survives "workdir copy"
        with self.assertRaises(OSError):
            pool.consume(nonce, {'direction': 'flash'})


def _candidate(paths):
    return next((p for p in paths if p.exists()), None)


def _real_b79_device(b79_app):
    """Synthetic 2MiB device with the real b79 app in both banks (the
    post-2026-11-10-restoration shape with r2-era residue beyond bank B)."""
    flash = good_flash(B79_PATH.read_bytes())
    device = bytearray(b'\xff' * SIZE)
    device[0:942080] = flash                                   # boot + bank A + package tail
    # device-style bank A header: seq 2 + consistent device fields (the
    # package header carries seq 0 / 0xff fields — codex fixture correction)
    device[0x2000:0x2020] = encode(make_header(2, b79_app, DEVICE_FIELDS))
    device[BANK_B:BANK_B + 32] = encode(make_header(3, b79_app, DEVICE_FIELDS))
    device[BANK_B + 32:BANK_B + 32 + len(b79_app)] = b79_app
    r2_end = BANK_B + 32 + 930245                              # r2-era extent
    device[BANK_B + 32 + len(b79_app):r2_end] = \
        b'\xff' * (r2_end - BANK_B - 32 - len(b79_app))
    for off in range(r2_end, BANK_B_END):
        device[off] = 0x5a                                     # r2 residue
    for off in range(VM_BEGIN, VM_END):
        device[off] = (off // 16) & 0xff
    for off in range(VM_END, SIZE):
        device[off] = (off // 32 + 1) & 0xff
    return bytes(device)


B79_PATH = _candidate(([Path(os.environ['H3S_B79_UFW'])] if os.environ.get('H3S_B79_UFW') else [])
                     + [ROOT / 'firmware/H3S_0.1.1.8_ANC_ON_TRANSPARENT.ufw',
                        Path('/home/felix021/code/headsets/h3s/firmware/'
                             'H3S_0.1.1.8_ANC_ON_TRANSPARENT.ufw')])
HAL5_PATH = _candidate(([Path(os.environ['H3S_HAL5_UFW'])] if os.environ.get('H3S_HAL5_UFW') else [])
                       + [Path.home() / 'tmp/hal5-probe/full95/'
                          'H3S_0.1.1.8_tones_minus0dB_HEARING_AID_FULL_MPO95_OFFLINE_UNTESTED.ufw'])
# F1 artifacts: LFIX-IMPL build outputs, kept out of git by precedent; the
# stable local copy is ~/tmp/lfiximpl/ (same convention as the HAL5 probe dir),
# overridable with H3S_F1A_UFW / H3S_F1B_UFW.
F1A_PATH = _candidate(([Path(os.environ['H3S_F1A_UFW'])] if os.environ.get('H3S_F1A_UFW') else [])
                      + [Path.home() / 'tmp/lfiximpl/lfiximpl-f1a.ufw'])
F1B_PATH = _candidate(([Path(os.environ['H3S_F1B_UFW'])] if os.environ.get('H3S_F1B_UFW') else [])
                      + [Path.home() / 'tmp/lfiximpl/lfiximpl-f1b.ufw'])
# F2/F1aC1 artifacts: LFIXIMPL2 build outputs, kept out of git by precedent;
# the stable local copy is ~/tmp/lfix2/ (same names as the ace-side files),
# overridable with H3S_F2_UFW / H3S_F1AC1_UFW.
F2_PATH = _candidate(([Path(os.environ['H3S_F2_UFW'])] if os.environ.get('H3S_F2_UFW') else [])
                     + [Path.home() / 'tmp/lfix2/lfiximpl2-f2.ufw'])
F1AC1_PATH = _candidate(([Path(os.environ['H3S_F1AC1_UFW'])] if os.environ.get('H3S_F1AC1_UFW') else [])
                        + [Path.home() / 'tmp/lfix2/f1a-c1.ufw'])
REAL_READY = (B79_PATH is not None and HAL5_PATH is not None
              and sha(B79_PATH.read_bytes()) == ap.B79_PROFILE_SHA
              and sha(HAL5_PATH.read_bytes()) == ap.HAL5_UFW_SHA)
REAL_F1_READY = (REAL_READY and F1A_PATH is not None and F1B_PATH is not None
                 and sha(F1A_PATH.read_bytes()) == ap.F1A_UFW_SHA
                 and sha(F1B_PATH.read_bytes()) == ap.F1B_UFW_SHA)
REAL_F2_READY = (REAL_F1_READY and F2_PATH is not None and F1AC1_PATH is not None
                 and sha(F2_PATH.read_bytes()) == ap.F2_UFW_SHA
                 and sha(F1AC1_PATH.read_bytes()) == ap.F1AC1_UFW_SHA)


@unittest.skipUnless(REAL_READY, 'real b79/HAL5 packages not materialized with pinned digests')
class RealProfileTests(unittest.TestCase):
    """The shipped pins against a synthetic device built from the real apps."""

    @classmethod
    def setUpClass(cls):
        from accept_profile import parse_package
        cls.hal5_app = parse_package(HAL5_PATH.read_bytes(), ap.HAL5_UFW_SHA,
                                     ap.HAL5_APP_LEN, ap.HAL5_APP_SHA)
        cls.b79_app = parse_package(B79_PATH.read_bytes(), ap.B79_PROFILE_SHA,
                                    ap.B79_APP_LEN, ap.B79_APP_SHA)
        cls.b79_device = _real_b79_device(cls.b79_app)

    def test_real_flash_plan_narrow_sector_set(self):
        target, plan = make_plan('flash', self.b79_device, HAL5_PATH.read_bytes(), new_nonce())
        verify_plan(plan, self.b79_device, target, self.hal5_app)
        self.assertEqual(plan['geometry']['new_extent'], 0xee020 + 930782)
        self.assertEqual(plan['geometry']['old_extent'], 0xee020 + 929875)
        self.assertIsNone(plan['geometry']['fill_0ff_range'])
        order = plan['write_order']
        self.assertEqual(order[-1], BANK_B)
        self.assertLess(len(order), 40, 'write set must be the precise diff, not the bank')
        self.assertNotEqual(order, [0xf3000, 0x113000, 0x166000, 0x1d0000, 0x1d1000, BANK_B])

    def test_real_flash_then_rollback_roundtrip(self):
        _, plan = make_plan('flash', self.b79_device, HAL5_PATH.read_bytes(), new_nonce())
        hal5_target, _ = make_plan('flash', self.b79_device, HAL5_PATH.read_bytes(),
                                   plan['nonce'])
        fake = FakeFlash(self.b79_device)
        journal = Journal(Path(tempfile.mkdtemp()) / 'restore.jsonl', sync=False)
        fake.bind(hal5_target, plan['write_order'])
        fake.writing = True
        try:
            execute_plan(fake, hal5_target, self.b79_device, plan['write_order'], journal)
        finally:
            journal.close()
        self.assertEqual(bytes(fake.image), hal5_target)
        target2, plan2 = make_plan('rollback', hal5_target, B79_PATH.read_bytes(), new_nonce())
        verify_plan(plan2, hal5_target, target2, self.b79_app)
        self.assertEqual(plan2['geometry']['fill_0ff_range'],
                         [0xee020 + 929875, 0xee020 + 930782])
        fake2 = FakeFlash(hal5_target)
        journal2 = Journal(Path(tempfile.mkdtemp()) / 'restore.jsonl', sync=False)
        fake2.bind(target2, plan2['write_order'])
        fake2.writing = True
        try:
            execute_plan(fake2, target2, hal5_target, plan2['write_order'], journal2)
        finally:
            journal2.close()
        self.assertEqual(bytes(fake2.image), target2)
        _, app_b = decode_bank(target2, BANK_B)
        self.assertEqual(sha(app_b), ap.B79_APP_SHA)
        self.assertEqual(target2[:BANK_B], self.b79_device[:BANK_B])

    def test_real_rollback_already_b79(self):
        _, report = make_plan('rollback', self.b79_device, B79_PATH.read_bytes(), new_nonce())
        self.assertEqual(report['status'], 'ALREADY_B79')


@unittest.skipUnless(REAL_F1_READY, 'real F1 packages not materialized with pinned digests')
class RealF1ProfileTests(unittest.TestCase):
    """The shipped F1 pins against synthetic devices built from the real apps:
    F1 flashes from a HAL5 bank B and rolls back to HAL5 (same layer)."""

    @classmethod
    def setUpClass(cls):
        from accept_profile import parse_package
        cls.hal5_app = parse_package(HAL5_PATH.read_bytes(), ap.HAL5_UFW_SHA,
                                     ap.HAL5_APP_LEN, ap.HAL5_APP_SHA)
        cls.f1a_app = parse_package(F1A_PATH.read_bytes(), ap.F1A_UFW_SHA,
                                    ap.F1A_APP_LEN, ap.F1A_APP_SHA)
        cls.f1b_app = parse_package(F1B_PATH.read_bytes(), ap.F1B_UFW_SHA,
                                    ap.F1B_APP_LEN, ap.F1B_APP_SHA)
        b79_app = parse_package(B79_PATH.read_bytes(), ap.B79_PROFILE_SHA,
                                ap.B79_APP_LEN, ap.B79_APP_SHA)
        cls.hal5_device, _ = make_plan('flash', _real_b79_device(b79_app),
                                       HAL5_PATH.read_bytes(), new_nonce())

    def test_real_f1_flash_narrow_sector_set(self):
        for cand, path, app, extra in (('f1a', F1A_PATH, self.f1a_app, 186),
                                       ('f1b', F1B_PATH, self.f1b_app, 72)):
            with self.subTest(candidate=cand):
                ufw = path.read_bytes()
                target, plan = make_plan('flash', self.hal5_device, ufw, new_nonce(), cand)
                verify_plan(plan, self.hal5_device, target, app)
                self.assertEqual(plan['profile'], 'accept-%s-flash-v1' % cand)
                self.assertEqual(plan['package_ufw_sha256'],
                                 {'f1a': ap.F1A_UFW_SHA, 'f1b': ap.F1B_UFW_SHA}[cand])
                self.assertEqual(plan['geometry']['old_extent'], 0xee020 + 930782)
                self.assertEqual(plan['geometry']['new_extent'], 0xee020 + 930782 + extra)
                self.assertIsNone(plan['geometry']['fill_0ff_range'])  # F1 apps are longer
                order = plan['write_order']
                self.assertEqual(order[-1], BANK_B)
                self.assertLess(len(order), 40, 'write set must be the precise diff')

    def test_real_f1_flash_then_rollback_roundtrip(self):
        ufw = F1A_PATH.read_bytes()
        _, plan = make_plan('flash', self.hal5_device, ufw, new_nonce(), 'f1a')
        f1a_target, _ = make_plan('flash', self.hal5_device, ufw, plan['nonce'], 'f1a')
        fake = FakeFlash(self.hal5_device)
        journal = Journal(Path(tempfile.mkdtemp()) / 'restore.jsonl', sync=False)
        fake.bind(f1a_target, plan['write_order'])
        fake.writing = True
        try:
            execute_plan(fake, f1a_target, self.hal5_device, plan['write_order'], journal)
        finally:
            journal.close()
        self.assertEqual(bytes(fake.image), f1a_target)
        target2, plan2 = make_plan('rollback', f1a_target, HAL5_PATH.read_bytes(),
                                   new_nonce(), 'f1a')
        verify_plan(plan2, f1a_target, target2, self.hal5_app)
        self.assertEqual(plan2['profile'], 'accept-f1a-rollback-v1')
        self.assertEqual(plan2['geometry']['fill_0ff_range'],
                         [0xee020 + 930782, 0xee020 + 930968])
        fake2 = FakeFlash(f1a_target)
        journal2 = Journal(Path(tempfile.mkdtemp()) / 'restore.jsonl', sync=False)
        fake2.bind(target2, plan2['write_order'])
        fake2.writing = True
        try:
            execute_plan(fake2, target2, f1a_target, plan2['write_order'], journal2)
        finally:
            journal2.close()
        self.assertEqual(bytes(fake2.image), target2)
        _, app_b = decode_bank(target2, BANK_B)
        self.assertEqual(sha(app_b), ap.HAL5_APP_SHA)
        self.assertEqual(target2[:BANK_B], self.hal5_device[:BANK_B])

    def test_real_f1_rollback_already_hal5(self):
        for cand in ('f1a', 'f1b'):
            with self.subTest(candidate=cand):
                _, report = make_plan('rollback', self.hal5_device,
                                      HAL5_PATH.read_bytes(), new_nonce(), cand)
                self.assertEqual(report['status'], 'ALREADY_HAL5')
                self.assertTrue(report['zero_write'])

    def test_real_f1_wrong_artifact_refused(self):
        f1a = F1A_PATH.read_bytes()
        flipped = f1a[:0x600 + 0x2020] + bytes([f1a[0x600 + 0x2020] ^ 1]) \
            + f1a[0x600 + 0x2021:]
        for name, ufw, cand, direction in (
                ('flipped byte in the real F1a artifact', flipped, 'f1a', 'flash'),
                ('real F1b artifact offered as F1a', F1B_PATH.read_bytes(), 'f1a', 'flash'),
                ('real HAL5 artifact offered as F1a', HAL5_PATH.read_bytes(), 'f1a', 'flash')):
            with self.subTest(case=name):
                with self.assertRaisesRegex(ValueError, 'pinned UFW'):
                    make_plan(direction, self.hal5_device, ufw, new_nonce(), cand)


@unittest.skipUnless(REAL_F2_READY, 'real F2/F1aC1 packages not materialized with pinned digests')
class RealF2ProfileTests(unittest.TestCase):
    """The shipped F2/F1aC1 pins against synthetic devices built from the real
    apps: both flash from an F1a bank B (the 2026-11-19 device state) and roll
    back to F1a (same layer)."""

    @classmethod
    def setUpClass(cls):
        from accept_profile import parse_package
        cls.hal5_app = parse_package(HAL5_PATH.read_bytes(), ap.HAL5_UFW_SHA,
                                     ap.HAL5_APP_LEN, ap.HAL5_APP_SHA)
        cls.f1a_app = parse_package(F1A_PATH.read_bytes(), ap.F1A_UFW_SHA,
                                    ap.F1A_APP_LEN, ap.F1A_APP_SHA)
        cls.f2_app = parse_package(F2_PATH.read_bytes(), ap.F2_UFW_SHA,
                                   ap.F2_APP_LEN, ap.F2_APP_SHA)
        cls.f1ac1_app = parse_package(F1AC1_PATH.read_bytes(), ap.F1AC1_UFW_SHA,
                                      ap.F1AC1_APP_LEN, ap.F1AC1_APP_SHA)
        b79_app = parse_package(B79_PATH.read_bytes(), ap.B79_PROFILE_SHA,
                                ap.B79_APP_LEN, ap.B79_APP_SHA)
        cls.hal5_device, _ = make_plan('flash', _real_b79_device(b79_app),
                                       HAL5_PATH.read_bytes(), new_nonce())
        cls.f1a_device, _ = make_plan('flash', cls.hal5_device,
                                      F1A_PATH.read_bytes(), new_nonce(), 'f1a')

    def test_real_f2_flash_narrow_sector_set(self):
        for cand, path, app, extra in (('f2', F2_PATH, self.f2_app, 66),
                                       ('f1ac1', F1AC1_PATH, self.f1ac1_app, 0)):
            with self.subTest(candidate=cand):
                ufw = path.read_bytes()
                target, plan = make_plan('flash', self.f1a_device, ufw, new_nonce(), cand)
                verify_plan(plan, self.f1a_device, target, app)
                self.assertEqual(plan['profile'], 'accept-%s-flash-v1' % cand)
                self.assertEqual(plan['package_ufw_sha256'],
                                 {'f2': ap.F2_UFW_SHA, 'f1ac1': ap.F1AC1_UFW_SHA}[cand])
                self.assertEqual(plan['geometry']['old_extent'], 0xee020 + 930968)
                self.assertEqual(plan['geometry']['new_extent'], 0xee020 + 930968 + extra)
                self.assertIsNone(plan['geometry']['fill_0ff_range'])  # never shorter
                order = plan['write_order']
                self.assertEqual(order[-1], BANK_B)
                self.assertLess(len(order), 40, 'write set must be the precise diff')

    def test_real_f2_flash_then_rollback_roundtrip(self):
        ufw = F2_PATH.read_bytes()
        _, plan = make_plan('flash', self.f1a_device, ufw, new_nonce(), 'f2')
        f2_target, _ = make_plan('flash', self.f1a_device, ufw, plan['nonce'], 'f2')
        fake = FakeFlash(self.f1a_device)
        journal = Journal(Path(tempfile.mkdtemp()) / 'restore.jsonl', sync=False)
        fake.bind(f2_target, plan['write_order'])
        fake.writing = True
        try:
            execute_plan(fake, f2_target, self.f1a_device, plan['write_order'], journal)
        finally:
            journal.close()
        self.assertEqual(bytes(fake.image), f2_target)
        target2, plan2 = make_plan('rollback', f2_target, F1A_PATH.read_bytes(),
                                   new_nonce(), 'f2')
        verify_plan(plan2, f2_target, target2, self.f1a_app)
        self.assertEqual(plan2['profile'], 'accept-f2-rollback-v1')
        self.assertEqual(plan2['geometry']['fill_0ff_range'],
                         [0xee020 + 930968, 0xee020 + 931034])   # 66B tail cleared
        fake2 = FakeFlash(f2_target)
        journal2 = Journal(Path(tempfile.mkdtemp()) / 'restore.jsonl', sync=False)
        fake2.bind(target2, plan2['write_order'])
        fake2.writing = True
        try:
            execute_plan(fake2, target2, f2_target, plan2['write_order'], journal2)
        finally:
            journal2.close()
        self.assertEqual(bytes(fake2.image), target2)
        _, app_b = decode_bank(target2, BANK_B)
        self.assertEqual(sha(app_b), ap.F1A_APP_SHA)
        self.assertEqual(target2[:BANK_B], self.f1a_device[:BANK_B])

    def test_real_f2_rollback_already_f1a(self):
        for cand in ('f2', 'f1ac1'):
            with self.subTest(candidate=cand):
                _, report = make_plan('rollback', self.f1a_device,
                                      F1A_PATH.read_bytes(), new_nonce(), cand)
                self.assertEqual(report['status'], 'ALREADY_F1A')
                self.assertTrue(report['zero_write'])

    def test_real_f2_wrong_artifact_refused(self):
        f2 = F2_PATH.read_bytes()
        flipped = f2[:0x600 + 0x2020] + bytes([f2[0x600 + 0x2020] ^ 1]) \
            + f2[0x600 + 0x2021:]
        for name, ufw, cand in (
                ('flipped byte in the real F2 artifact', flipped, 'f2'),
                ('real F1aC1 artifact offered as F2', F1AC1_PATH.read_bytes(), 'f2'),
                ('real F1a artifact offered as F2', F1A_PATH.read_bytes(), 'f2'),
                ('real F2 artifact offered as F1aC1', F2_PATH.read_bytes(), 'f1ac1'),
                ('real F1a artifact offered as F1aC1 (equal length, '
                 'different content)', F1A_PATH.read_bytes(), 'f1ac1')):
            with self.subTest(case=name):
                with self.assertRaisesRegex(ValueError, 'pinned UFW'):
                    make_plan('flash', self.f1a_device, ufw, new_nonce(), cand)


if __name__ == '__main__':
    unittest.main()
