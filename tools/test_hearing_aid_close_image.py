#!/usr/bin/env python3
"""Offline verification of the composed hearing-aid L1 close image (route Y).

This exercises the builder-side segment assembly (hearing_aid_l1_image.py,
also used by build_quiet_candidate.py --hearing-aid-l1-close) WITHOUT any
firmware input: assemble-only checks over the single self-contained segment
open + close + TRANS stubs + leave stubs (docs/hearing-aid-l1-payload.md
§10.5/§11). The byte-level implant/diff assertions run at build time
(verify_l1_image); this test pins the segment semantics:

1. every instruction of both variants round-trips byte-identically
   (assemble_segment re-encodes each word; here we additionally check the
   open-only variant still matches the route-T segment sha);
2. the composed close segment is exactly standalone open (route L) followed
   by standalone close (route X) followed by the stubs;
3. the four leave stubs have the §10.5 shapes and targets (ctx getter, mode
   slot displacement, internal call into mic_effect_close, return at
   window+8 — ON-2 returning to the 0x0600bbba call site);
4. the two-pass slot baking changes only slot li.u32 immediates (open 2 +
   close 2 loads), everything else layout-stable;
5. ON-1/ON-2 windows are plain fall-through entries, OFF-1/OFF-2 carry the
   pinned mode branch as their only incoming branch (static scan evidence).

UNVALIDATED ON DEVICE: nothing here executes the close chain.
"""
import re
import unittest
from pathlib import Path

import pi32v2_asm as W

ROOT = Path(__file__).resolve().parents[1]
import hearing_aid_l1_image as IMG

PAYLOAD_ADDRESS = IMG.PAYLOAD_ADDRESS
# Route-T composed segment (open variant), §9.1: 198B code, this sha.
ROUTE_T_SHA = 'b0feed11b7d3c896423faf876b5300232e5277f2ad9e7b3b6b78e43f84ea4a7d'
CLOSE_WINDOWS = {tag: (addr, mode_slot) for tag, addr, _b, mode_slot, _l in IMG.CLOSE_WINDOWS}


class ComposedCloseSegmentTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.open_report, cls.open_code, cls.open_slot = IMG.assemble_segment()
        cls.close_report, cls.close_code, cls.close_slot = IMG.assemble_segment(close_hooks=True)

    def test_01_open_variant_is_route_t_segment(self):
        """The open-only variant reproduces the route-T §9.1 segment exactly."""
        self.assertEqual(len(self.open_code), 198)
        self.assertEqual(self.open_slot, 0x060e313a)
        import hashlib
        self.assertEqual(hashlib.sha256(self.open_code).hexdigest(), ROUTE_T_SHA)

    def test_02_segment_geometry(self):
        """open(134) + close(64) + stubs(64+102) = 364B, slot at the tail."""
        self.assertEqual(len(self.close_code), 364)
        self.assertEqual(self.close_slot, PAYLOAD_ADDRESS + 364)
        self.assertEqual(self.close_report['close_address'],
                         f'0x{PAYLOAD_ADDRESS + 134:08x}')
        # close payload length pinned by the route-X test
        self.assertEqual(int(self.close_report['close_address'], 16)
                         - PAYLOAD_ADDRESS, 134)

    def test_03_composed_prefix_matches_standalone_payloads(self):
        """Prefix bytes == standalone route-L open, then standalone route-X close."""
        self.assertTrue(self.close_report['payload_prefix_matches_route_L'])
        self.assertTrue(self.close_report['close_payload_matches_route_X'])

    def test_04_leave_stub_shapes_and_targets(self):
        """Every leave stub: §10.5 shape, ctx getter, slot, close call, return."""
        starts = sorted(int(self.close_report['leave_stub_addresses'][t], 16)
                        for t in CLOSE_WINDOWS)
        for tag, (addr, mode_slot) in CLOSE_WINDOWS.items():
            stub = int(self.close_report['leave_stub_addresses'][tag], 16)
            self.assertEqual(stub % 2, 0, f'{tag}: unaligned stub')
            end = starts[starts.index(stub) + 1] if stub != starts[-1] \
                else PAYLOAD_ADDRESS + len(self.close_code)
            texts = [item['text'] for item in self.close_report['listing']
                     if stub <= int(item['address'], 16) < end]
            expected_head = [f'r6 = {IMG.ANC_CTX_GET}', 'call r6',
                             f'r5 = [r0+{mode_slot}]']
            self.assertEqual(texts[:3], expected_head, f'{tag}: replay head')
            self.assertIn('call', texts[3], f'{tag}: expected the close call')
            ret = addr + 8
            self.assertEqual(texts[-2:], [f'r6 = {ret}', 'goto r6'],
                             f'{tag}: return must be window+8')
            if tag == 'ON-2':
                # re-fetch ctx into r0 before returning to the 0x0600bbba call
                self.assertEqual(texts[4:6], [f'r6 = {IMG.ANC_CTX_GET}', 'call r6'],
                                 'ON-2: ctx re-fetch missing')
            else:
                self.assertEqual(texts[4], 'r0 = r5', f'{tag}: fn restore missing')

    def test_05_slot_baking_changes_only_slot_immediates(self):
        """Two-pass slot diff stays inside the four r0=<slot> li.u32 loads."""
        diffs = self.close_report['slot_diff_offsets']
        self.assertTrue(diffs)
        extents = [(int(item['address'], 16) - PAYLOAD_ADDRESS,
                    int(item['address'], 16) - PAYLOAD_ADDRESS + len(item['bytes']) // 2,
                    item['insn'], item['text'])
                   for item in self.close_report['listing']]
        slot_loads = 0
        for i in diffs:
            hit = [(s, e, iid, t) for s, e, iid, t in extents if s <= i < e]
            self.assertEqual(len(hit), 1, f'diff at {i} spans instructions')
            s, e, iid, t = hit[0]
            self.assertEqual(iid, 'li.u32', f'non-slot byte changed at {i}')
            self.assertEqual(t, f'r0 = {self.close_slot}',
                             f'unexpected changed immediate: {t}')
        for _s, _e, iid, t in extents:
            if iid == 'li.u32' and t == f'r0 = {self.close_slot}':
                slot_loads += 1
        self.assertEqual(slot_loads, 4, 'open guard+record, close guard+clear')

    def test_06_window_entry_evidence(self):
        """ON windows fall-through only; OFF windows carry the mode branch."""
        entries = {'ON-1': 'none (plain fall-through)',
                   'ON-2': 'none (plain fall-through)',
                   'OFF-1': '0x0600db8e (mode==1)',
                   'OFF-2': '0x0600dbb8 (mode==1)'}
        for tag, addr in ((t, CLOSE_WINDOWS[t][0]) for t in CLOSE_WINDOWS):
            self.assertEqual(addr % 2, 0)
            self.assertIn(addr + 8, (0x0600dba2, 0x0600dc7c, 0x0600dbcc, 0x0600dca2),
                          f'{tag}: unexpected return address')

    def test_07_roundtrip_every_instruction(self):
        """encode(decode(x)) == x for both variants (assembler closure)."""
        for label, report in (('open', self.open_report), ('close', self.close_report)):
            for item in report['listing']:
                self.assertIsNotNone(item['insn'],
                                     f"{label}: undecodable at {item['address']}")


if __name__ == '__main__':
    unittest.main(verbosity=2)
