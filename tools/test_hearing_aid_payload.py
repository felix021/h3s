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
   immediate words (the payload is otherwise base-address independent);
5. cross-call r0 restore (route L1KEY fix): the stream handle held in r4 is
   reloaded into r0 before set_scene/set_notify/start, matching the pc_mic
   template's `r0=[r4+0]` reload;
6. failure clock recovery: the error path after clock_alloc emits
   event_notify(11, "mic_effect") to free the 24 MHz clock (route L1KEY).

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
# 0x060057e6 is the same dispatch entry as get_pipeline_uuid; the route-L1KEY
# error path loads it a second time (event 11 = CLOSE_PLAYER -> clock_free),
# so its expected load count is 2, every other anchor exactly 1.
EVENT_NOTIFY_ADDR = 0x060057e6
CONSTS = {
    0x060a1de2: '"mic_effect" string in firmware',
    0xD06D: 'NODE_UUID_ADC (SDK fallback node)',
    0x1100: 'root pipeline thread id (built by pipeline_parse)',
    0x2000D: 'root thread flags (pc_mic shape)',
    0x100: 'root thread extra (pc_mic shape)',
    16: 'STREAM_SCENE_HEARING_AID (SDK enum; QCY value unverified)',
    10: 'set_notify value (pc_mic same value; semantics unpinned)',
    15: 'STREAM_EVENT_GET_PIPELINE_UUID',
    11: 'STREAM_EVENT_CLOSE_PLAYER (failure clock recovery)',
}
ERRORS = {-22: '-EINVAL'}
SLOT_DEFAULT = 0x060e3200
SLOT_ALT = 0x060e4000
MAIN_CHAIN = ['get_pipeline_uuid', 'jlstream_pipeline_parse',
              'jlstream_add_thread', 'jlstream_set_scene',
              'set_notify (stream[28] + 0x20006 dispatch)', 'jlstream_start']


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
        """Each API address is loaded as r6 the pinned number of times."""
        loads = [int(m.group(1)) for off, iid, text, raw in self.insns
                 if iid == 'li.u32' and text.startswith('r6 = ')
                 for m in [re.match(r'r6 = (\d+)$', text)] if m]
        for addr, name in API_ADDRS.items():
            expected = 2 if addr == EVENT_NOTIFY_ADDR else 1
            self.assertEqual(loads.count(addr), expected,
                             f'{name} ({addr:#x}) must be loaded {expected}x')
        self.assertEqual(len(loads), len(API_ADDRS) + 1,
                         'unexpected r6 API load count (7 anchors + 1 notify)')

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
        self.assertEqual(len(calls), len(API_ADDRS) + 1,
                         '7 API calls + 1 failure-clock notify expected')
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

    def test_06_call_order_matches_activation_chain(self):
        """APIs appear in the activation order pinned from pc_mic/tone players,
        with release and the failure-clock notify only on the error path."""
        addr_to_name = {a: n for a, n in API_ADDRS.items()}
        seen = []
        for off, iid, text, raw in self.insns:
            m = re.match(r'r6 = (\d+)$', text)
            if m and int(m.group(1)) in addr_to_name:
                seen.append(addr_to_name[int(m.group(1))])
        # the first six API loads form the activation chain, in order
        self.assertEqual(seen[:6], MAIN_CHAIN, 'activation order mismatch')
        # release (index 6) then the second get_pipeline_uuid load (event 11)
        self.assertEqual(seen[6], 'jlstream_release',
                         'release must precede the failure-clock notify')
        self.assertEqual(seen[7], 'get_pipeline_uuid',
                         'failure path must re-load 0x060057e6 for event 11')

    def test_07_cross_call_r0_restore(self):
        """The stream in r4 is reloaded into r0 before set_scene/set_notify/start."""
        seq = [t for _, _, t, _ in self.insns]
        # set_scene has two extra args (r1 scene, r2 callback), set_notify one
        # (r1 notify val), start none — the r0=r4 reload sits before them all.
        cases = [('jlstream_set_scene', 3),
                 ('set_notify (stream[28] + 0x20006 dispatch)', 2),
                 ('jlstream_start', 1)]
        for api, gap in cases:
            addr = [a for a, n in API_ADDRS.items() if n == api][0]
            i = seq.index(f'r6 = {addr}')
            self.assertEqual(seq[i - gap], 'r0 = r4',
                             f'{api} must receive the reloaded stream (r0 = r4)')

    def test_08_failure_clock_recovery(self):
        """After clock_alloc, every failure exit runs event_notify(11, name)."""
        seq = [t for _, _, t, _ in self.insns]
        # the clock-recovery block: r0 = 11; r1 = <name>; r6 = 0x060057e6; call r6
        i11 = seq.index('r0 = 11')
        self.assertEqual(seq[i11 + 1], f'r1 = {0x060a1de2}')
        self.assertEqual(seq[i11 + 2], f'r6 = {EVENT_NOTIFY_ADDR}')
        self.assertEqual(seq[i11 + 3], 'call r6')
        # the notify load must sit after release (failure path), so the notify
        # is the last r6 API load in the listing
        loads = [t for t in seq if t.startswith('r6 = ') and int(t.split('= ')[1])
                 in API_ADDRS]
        self.assertEqual(loads[-1], f'r6 = {EVENT_NOTIFY_ADDR}',
                         'clock-recovery notify must be the error-path tail')


if __name__ == '__main__':
    unittest.main(verbosity=2)
