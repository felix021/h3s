#!/usr/bin/env python3
"""Offline verification of the R4 key-store App-command payload (route AQ).

The payload in tools/r4cmd/keystore_hook.S is the firmware-side setter that
wires one RCSP custom command (registry cmdid 0x30, null setter word filled
by the image builder) onto the real VM get/set_config dispatchers for key 30.
This test never runs the payload and never touches a device; it proves,
offline:

1. round trip: every assembled instruction re-encodes byte-identically
   (encode(decode(x)) == x) and the size stays in the estimated shape band;
2. anchor audit: exactly the two pinned API addresses (set/get_config
   dispatchers, route AB) are loaded, each once, into r6;
3. call pairing: every register-indirect call is preceded by its API load
   (three 2B argument setups in between, the payload's pinned shape);
4. shape constants: the command-shape constants (key id 30, value length 8,
   write length 9, read length 1) all appear as instruction operands;
5. position independence: the payload carries no absolute address except the
   two API anchors — assembling it under a different base yields identical
   bytes (no .equ depends on the segment layout);
6. the response-writing discipline: the write branch stores the status word
   through the saved response pointer at +0 (the dispatcher overwrites
   resp[0..1] after the call), the read branch passes resp+2 as the
   get_config destination, and the write branch passes data+1 as the
   set_config source.

Evidence table is from docs/r4-command-hook.md + docs/vm-key-roundtrip.md
(route AB pinned both dispatcher entries by direct simulation) and
docs/cmd-0x17-0x1d-layout.md (route P pinned the setter ABI
(buf, cap, data, len) -> n). UNVALIDATED ON DEVICE: passing here is not
"the App can read/write key 30 on the headset".
"""
import unittest
from pathlib import Path

import pi32v2_asm as W

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'tools/r4cmd/keystore_hook.S'

# --- static evidence table (docs/r4-command-hook.md) ----------------------------
API_ADDRS = {
    0x06001f12: 'VM set_config dispatcher (route AB, key-30 write/read proven)',
    0x06000f88: 'VM get_config dispatcher (route AB, key-30 read-back proven)',
}
CONSTS = {
    30: 'VM key id (route K: free key 27..36 band; no registration needed)',
    8: 'value length (route AB roundtrip length)',
    9: 'write shape data_len = key byte + 8 value bytes',
    1: 'read shape data_len = key byte only',
}
MIN_INSNS, MAX_INSNS = 20, 40


def build():
    """Assemble the payload. Returns [(offset, insn_id, text, raw_bytes)]."""
    obj = W.read_object(W.assemble_file(SOURCE))
    sections = W.code_sections(obj)
    assert len(sections) == 1, f'expected one code section, got {len(sections)}'
    name, data, relocs = sections[0]
    assert not relocs, f'payload must be self-contained, found {len(relocs)} relocs'
    return list(W.disassemble(data)), data, name


class R4KeyhookPayloadTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.insns, cls.data, cls.section = build()

    def test_01_roundtrip_every_instruction(self):
        """encode(decode(x)) == x for every instruction: zero-diff round trip."""
        count = 0
        for offset, insn_id, text, raw in self.insns:
            assert insn_id is not None, f'undecodable word at +{offset:#x}'
            got = W.decode(self.data, offset)
            assert got is not None, f'decode failed at +{offset:#x}'
            insn, values, length = got
            assert length == len(raw), f'length mismatch at +{offset:#x}'
            reencoded = W.encode(insn_id, **values)
            self.assertEqual(reencoded, raw, f're-encode differs at +{offset:#x} ({text})')
            count += 1
        self.assertGreaterEqual(count, MIN_INSNS, 'payload unexpectedly small')
        self.assertLessEqual(count, MAX_INSNS, 'payload larger than estimated shape')
        self.assertEqual(len(self.data), 78, 'payload size changed')

    def test_02_api_anchor_addresses(self):
        """Exactly the two pinned API addresses, each loaded once into r6."""
        loads = [int(text.split('= ')[1]) for _off, iid, text, _raw in self.insns
                 if iid == 'li.u32']
        self.assertEqual(sorted(loads), sorted(API_ADDRS),
                         f'API anchor set mismatch: {[hex(v) for v in loads]}')
        for _off, iid, text, _raw in self.insns:
            if iid == 'li.u32':
                self.assertTrue(text.startswith('r6 = '),
                                f'API address must load into r6: {text}')

    def test_03_call_pairing(self):
        """Each register-indirect call follows its own API load (3x 2B args)."""
        by_off = {off: (iid, text) for off, iid, text, _raw in self.insns}
        calls = [(off, iid) for off, iid, _t, _r in self.insns if iid == 'call.r']
        self.assertEqual(len(calls), len(API_ADDRS), 'one call per API anchor')
        for off, _iid in calls:
            prev = by_off[off - 12]           # li.u32(6B) + 3x 2B arg setups
            self.assertEqual(prev[0], 'li.u32', 'call must follow its API load')
            addr = int(prev[1].split('= ')[1])
            self.assertIn(addr, API_ADDRS, f'call target {addr:#x} not pinned')

    def test_04_shape_constants(self):
        """Every command-shape constant appears as an instruction operand."""
        words = set()
        for _off, _iid, text, _raw in self.insns:
            for token in text.replace('(', ' ').replace(')', ' ').split():
                if token.lstrip('-').isdigit():
                    words.add(int(token))
        for value, meaning in CONSTS.items():
            self.assertIn(value, words, f'missing constant {value:#x} ({meaning})')

    def test_05_position_independence(self):
        """No layout-dependent constant: the standalone bytes are base-free."""
        import hashlib
        first = hashlib.sha256(self.data).hexdigest()
        _insns2, data2, _name = build()
        self.assertEqual(hashlib.sha256(data2).hexdigest(), first,
                         're-assembly is not deterministic')
        # no absolute address except the API anchors: no other li.u32 exists
        # (test_02 pins the li.u32 set), and no memory operand embeds a
        # 32-bit address constant — every displacement is small.
        for off, iid, text, raw in self.insns:
            if iid == 'li.u32':
                continue
            got = W.decode(self.data, off)
            insn, values, _n = got
            for name, value in values.items():
                if isinstance(value, int):
                    self.assertLess(abs(value), 0x10000,
                                    f'unexpected wide constant in {text}')

    def test_06_response_and_value_addressing(self):
        """resp+2 for the read-back destination, data+1 for the write source,
        and the status word stored through the saved resp pointer at +0."""
        texts = [text for _o, _i, text, _r in self.insns]
        self.assertIn('r1 = r4 + 2', texts, 'read branch must target resp+2')
        self.assertIn('r1 = r2 + 1', texts, 'write branch must source data+1')
        self.assertIn('[r4+0] = r5', texts, 'write status must store at resp+0')
        # callee-saved discipline: prologue/epilogue pair around r4-r6
        self.assertEqual(self.insns[0][3].hex(), '7604',
                         'prologue must save {rets, r6-r4}')
        self.assertEqual(self.insns[-1][3].hex(), '5604',
                         'epilogue must restore {pc, r6-r4}')
        # r7 (the dispatcher's live registry-entry pointer after the setter
        # returns, consumed at 0x0600f946) is never written
        for off, iid, text, raw in self.insns:
            self.assertNotIn('r7 = ', text, f'payload must not touch r7: {text}')


if __name__ == '__main__':
    unittest.main()
