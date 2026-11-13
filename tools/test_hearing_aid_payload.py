#!/usr/bin/env python3
"""Offline verification of the mic_effect (DHA) activation payload (route L).

The payload in tools/hearing-aid/mic_effect_open.S re-implements the
linker-GC'd hearing_aid_player_open() using only jlstream framework entry
points that exist in the official app.bin. This test never runs the payload
and never touches a device; it proves, offline:

1. round trip: every assembled instruction re-encodes byte-identically
   (encode(decode(x)) == x, the "assembler is trustworthy" closure);
2. anchor audit: every API address and every argument constant assembled
   into the bytes matches the static evidence table exactly;
3. call pairing: every register-indirect call is immediately preceded by
   loading the API address it must call;
4. slot parametrization: changing MIC_EFX_SLOT changes only the two slot
   immediate words (the payload is otherwise base-address independent).

Evidence table is from docs/hearing-aid-l1-payload.md (route L disassembly
of tone player 0x0600d9be, pc_mic player 0x060339a4 and the jlstream
framework entries). UNVALIDATED ON DEVICE: passing here is not "hearing aid
works".
"""
import re
import unittest
from pathlib import Path

import pi32v2_asm as W

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'tools/hearing-aid/mic_effect_open.S'

# --- static evidence table (docs/hearing-aid-l1-payload.md §2/§4) -----------------
API_ADDRS = {
    0x060057e6: 'get_pipeline_uuid',
    0x06074938: 'jlstream_pipeline_parse',
    0x06074ec6: 'jlstream_add_thread',
    0x06074a5e: 'jlstream_set_scene',
    0x06074e64: 'set_notify (stream[28] + 0x20006 dispatch)',
    0x06074ffe: 'jlstream_start',
    0x06074204: 'jlstream_release',
}
CONSTS = {
    0x060a1de2: '"mic_effect" string in firmware',
    0xD06D: 'NODE_UUID_ADC (SDK fallback node)',
    0x1100: 'root pipeline thread id (built by pipeline_parse)',
    0x2000D: 'root thread flags (pc_mic shape)',
    0x100: 'root thread extra (pc_mic shape)',
    16: 'STREAM_SCENE_HEARING_AID (SDK enum; QCY value unverified)',
    10: 'set_notify value (pc_mic same value; semantics unpinned)',
    15: 'STREAM_EVENT_GET_PIPELINE_UUID',
}
ERRORS = {-22: '-EINVAL'}
SLOT_DEFAULT = 0x060e3200
SLOT_ALT = 0x060e4000


def build(slot=None):
    """Assemble the payload (optionally with a patched MIC_EFX_SLOT).

    Returns [(offset, insn_id, text, raw_bytes)] over the single code section.
    """
    text = SOURCE.read_text()
    if slot is not None:
        patched = re.sub(r'\.equ MIC_EFX_SLOT, 0x[0-9a-fA-F]+',
                         f'.equ MIC_EFX_SLOT, 0x{slot:x}', text, count=1)
        assert patched != text, 'MIC_EFX_SLOT equ not found for patching'
        text = patched
    obj = W.read_object(W.assemble_text(text, str(SOURCE)))
    sections = W.code_sections(obj)
    assert len(sections) == 1, f'expected one code section, got {len(sections)}'
    name, data, relocs = sections[0]
    assert not relocs, f'payload must be self-contained, found {len(relocs)} relocs'
    return list(W.disassemble(data)), data, name


class MicEffectPayloadTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.insns, cls.data, cls.section = build()
        cls.alt_insns, cls.alt_data, _ = build(SLOT_ALT)

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
        self.assertGreaterEqual(count, 30, 'activation payload unexpectedly small')
        self.assertLessEqual(count, 60, 'activation payload larger than estimated shape')

    def test_02_api_anchor_addresses(self):
        """Every firmware API address appears exactly once, as an r6 load."""
        loads = [(off, m.group(1)) for off, iid, text, raw in self.insns
                 if iid == 'li.u32' and text.startswith('r6 = ')
                 for m in [re.match(r'r6 = (\d+)$', text)] if m]
        loaded = [int(v) for _, v in loads]
        for addr, name in API_ADDRS.items():
            self.assertEqual(loaded.count(addr), 1,
                             f'{name} ({addr:#x}) must be loaded exactly once')
        self.assertEqual(len(loaded), len(API_ADDRS), 'unexpected extra r6 API loads')

    def test_03_argument_constants(self):
        """Every argument constant from the evidence table is present."""
        words = set()
        for off, iid, text, raw in self.insns:
            for m in re.finditer(r'= (-?\d+)$', text):
                words.add(int(m.group(1)))
        for value, meaning in {**CONSTS, **ERRORS}.items():
            self.assertIn(value, words, f'missing constant {value:#x} ({meaning})')

    def test_04_call_pairing(self):
        """Each register-indirect call is immediately preceded by its API load."""
        seq = [(off, iid, text) for off, iid, text, raw in self.insns]
        calls = [i for i, (_, iid, _) in enumerate(seq) if iid == 'call.r']
        self.assertEqual(len(calls), len(API_ADDRS),
                         'one call per API anchor expected')
        for i in calls:
            prev = seq[i - 1]
            self.assertEqual(prev[1], 'li.u32', 'call must follow an API address load')
            m = re.match(r'r6 = (\d+)$', prev[2])
            assert m, f'unexpected load before call: {prev[2]}'
            addr = int(m.group(1))
            self.assertIn(addr, API_ADDRS, f'call target {addr:#x} is not a pinned API')

    def test_05_slot_parametrization(self):
        """Changing MIC_EFX_SLOT changes only the two slot load/store words."""
        self.assertNotEqual(self.data, self.alt_data)
        # diff byte runs must all be inside li.u32 slot loads only
        diffs = [(i, a, b) for i, (a, b) in enumerate(zip(self.data, self.alt_data)) if a != b]
        self.assertTrue(diffs)
        # collect instruction extents containing any diff
        changed_ids = set()
        offset = 0
        idx = 0
        extents = []
        for off, iid, text, raw in self.insns:
            extents.append((off, off + len(raw), iid, text))
        for i, _, _ in diffs:
            for start, end, iid, text in extents:
                if start <= i < end:
                    changed_ids.add((iid, text))
        for iid, text in changed_ids:
            self.assertEqual(iid, 'li.u32', f'non-slot instruction changed: {text}')
            value = int(text.split('= ')[1])
            self.assertTrue(value in (SLOT_DEFAULT, SLOT_ALT),
                            f'unexpected changed immediate: {value:#x}')

    def test_06_call_order_matches_activation_chain(self):
        """APIs appear in the activation order pinned from pc_mic/tone players."""
        order = ['get_pipeline_uuid', 'jlstream_pipeline_parse',
                 'jlstream_add_thread', 'jlstream_set_scene',
                 'set_notify (stream[28] + 0x20006 dispatch)',
                 'jlstream_start', 'jlstream_release']
        addr_to_name = {a: n for a, n in API_ADDRS.items()}
        seen = []
        for off, iid, text, raw in self.insns:
            m = re.match(r'r6 = (\d+)$', text)
            if m and int(m.group(1)) in addr_to_name:
                seen.append(addr_to_name[int(m.group(1))])
        main_chain = [n for n in seen if n != 'jlstream_release']
        self.assertEqual(main_chain, order[:6], 'activation order mismatch')
        self.assertEqual(seen[-1], 'jlstream_release', 'release must be the error path last call')


if __name__ == '__main__':
    unittest.main(verbosity=2)
