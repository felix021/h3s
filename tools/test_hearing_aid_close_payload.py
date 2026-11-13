#!/usr/bin/env python3
"""Offline verification of the mic_effect (DHA) close payload (route X).

The payload in tools/hearing-aid/mic_effect_close.S re-implements the
linker-GC'd hearing_aid_player_close() (SDK mic_effect.c:
mic_effect_player_delete) using only jlstream framework entry points that
exist in the official app.bin. This test never runs the payload and never
touches a device; it proves, offline:

1. round trip: every assembled instruction re-encodes byte-identically
   (encode(decode(x)) == x, the "assembler is trustworthy" closure);
2. anchor audit: every API address and every argument constant assembled
   into the bytes matches the static evidence table exactly;
3. call pairing: every register-indirect call is immediately preceded by
   loading the API address it must call;
4. slot parametrization: changing MIC_EFX_SLOT changes only the two slot
   immediate words (the payload is otherwise base-address independent).

Close-chain evidence (docs/hearing-aid-l1-payload.md §10, route X): the
firmware closes players with the three-part idiom jlstream_stop(stream, 0)
-> jlstream_release(stream) -> jlstream_event_notify(11, name); the CLOSE
PLAYER dispatch case exists at 0x060057e6 (same entry as the activation
chain's get_pipeline_uuid) and its "mic_effect" branch frees the 24 MHz
clock. UNVALIDATED ON DEVICE: passing here is not "hearing aid closes".
"""
import re
import unittest
from pathlib import Path

import pi32v2_asm as W

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'tools/hearing-aid/mic_effect_close.S'

# --- static evidence table (docs/hearing-aid-l1-payload.md §10) -------------------
API_ADDRS = {
    0x060741ee: 'jlstream_stop (h[stream+46]=fade, post cmd4; 7 firmware callers)',
    0x06074204: 'jlstream_release (post cmd9 sync teardown; route-L anchor)',
    0x060057e6: 'jlstream_event_notify (tbh switch on event-11; '
                 'get_pipeline_uuid entry, route-L anchor)',
}
CONSTS = {
    11: 'STREAM_EVENT_CLOSE_PLAYER (SDK enum; 3 firmware callers pass r0=11)',
    0x060a1de2: '"mic_effect" string (CLOSE_PLAYER strcmp chain match)',
}
SLOT_DEFAULT = 0x060e3200
SLOT_ALT = 0x060e4000
EXPECTED_CALLS = ['jlstream_stop (h[stream+46]=fade, post cmd4; 7 firmware callers)',
                  'jlstream_release (post cmd9 sync teardown; route-L anchor)',
                  'jlstream_event_notify (tbh switch on event-11; '
                  'get_pipeline_uuid entry, route-L anchor)']


def build(slot=None):
    """Assemble the close payload (optionally with a patched MIC_EFX_SLOT).

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


class MicEffectClosePayloadTest(unittest.TestCase):

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
        self.assertGreaterEqual(count, 14, 'close payload unexpectedly small')
        self.assertLessEqual(count, 30, 'close payload larger than estimated shape')

    def test_02_api_anchor_addresses(self):
        """Every firmware API address appears exactly once, as an r6 load."""
        loads = [int(m.group(1)) for off, iid, text, raw in self.insns
                 if iid == 'li.u32' and text.startswith('r6 = ')
                 for m in [re.match(r'r6 = (\d+)$', text)] if m]
        for addr, name in API_ADDRS.items():
            self.assertEqual(loads.count(addr), 1,
                             f'{name} ({addr:#x}) must be loaded exactly once')
        self.assertEqual(len(loads), len(API_ADDRS), 'unexpected extra r6 API loads')

    def test_03_argument_constants(self):
        """Every argument constant from the evidence table is present."""
        words = set()
        for off, iid, text, raw in self.insns:
            for m in re.finditer(r'= (-?\d+)$', text):
                words.add(int(m.group(1)))
        for value, meaning in CONSTS.items():
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
        """Changing MIC_EFX_SLOT changes only the two slot load words."""
        self.assertNotEqual(self.data, self.alt_data)
        diffs = [(i, a, b) for i, (a, b) in enumerate(zip(self.data, self.alt_data)) if a != b]
        self.assertTrue(diffs)
        extents = [(off, off + len(raw), iid, text)
                   for off, iid, text, raw in self.insns]
        changed_ids = set()
        for i, _, _ in diffs:
            for start, end, iid, text in extents:
                if start <= i < end:
                    changed_ids.add((iid, text))
        for iid, text in changed_ids:
            self.assertEqual(iid, 'li.u32', f'non-slot instruction changed: {text}')
            value = int(text.split('= ')[1])
            self.assertTrue(value in (SLOT_DEFAULT, SLOT_ALT),
                            f'unexpected changed immediate: {value:#x}')

    def test_06_close_order_matches_firmware_idiom(self):
        """Calls follow the firmware three-part close: stop -> release -> notify.

        The guard must read the slot before any call, and the slot must be
        cleared between release and notify (SDK order: player=NULL then
        event_notify(CLOSE_PLAYER)). stop's fade argument is 0 exactly as the
        firmware site 0x06016c12 does.
        """
        addr_to_name = {a: n for a, n in API_ADDRS.items()}
        seen = []
        slot_loads = slot_clears = 0
        for off, iid, text, raw in self.insns:
            m = re.match(r'r6 = (\d+)$', text)
            if m and int(m.group(1)) in addr_to_name:
                seen.append(addr_to_name[int(m.group(1))])
            if iid == 'li.u32' and text == f'r0 = {SLOT_DEFAULT}':
                slot_loads += 1
            if iid == 'st.w' and text == '[r0+0] = r5':
                slot_clears += 1
        self.assertEqual(seen, EXPECTED_CALLS, 'close order mismatch')
        self.assertEqual(slot_loads, 2, 'slot must be loaded exactly twice (guard + clear)')
        self.assertEqual(slot_clears, 1, 'slot must be cleared exactly once')
        # fade argument: the instruction right before the stop load sets r1 = 0
        seq = [t for _, _, t, _ in self.insns]
        i_stop = seq.index(f'r6 = {list(API_ADDRS)[0]}')
        self.assertEqual(seq[i_stop - 1], 'r1 = 0', 'stop fade must be 0 (firmware shape)')
        self.assertEqual(seq[i_stop - 2], 'r0 = r4', 'stop must receive the slot stream')
        # the guard branch tests the slot value before the first call
        i_guard = next(i for i, t in enumerate(seq) if t.startswith('if (r4 == 0)'))
        self.assertLess(i_guard, i_stop, 'reentry guard must precede the stop call')


if __name__ == '__main__':
    unittest.main(verbosity=2)
