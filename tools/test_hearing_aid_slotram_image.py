#!/usr/bin/env python3
"""Offline verification of the hearing-aid L1 slotram image (route CT).

Exercises the builder-side segment assembly for the RAM-slot variant
(hearing_aid_l1_image.py, also used by build_quiet_candidate.py
--hearing-aid-l1-slotram, which implies --hearing-aid-l1-close) WITHOUT any
firmware input (docs/hearing-aid-l1-payload.md §12). The byte-level
implant/diff assertions run at build time (verify_l1_image); this test pins:

1. the segment keeps the route-Y geometry (364B, same stub layout) and only
   the four slot li.u32 immediates differ from the close variant — the
   open/close payload sources themselves stay byte-untouched;
2. the slot is the pinned boot-zeroed BSS address, never the segment tail;
3. the slot address invariants (inside the really-executed boot BSS zero
   loop [0x125320, 0x12a62c), 4-aligned, below the QCY struct base, and
   centred in the statically dead 593B hole with >=0x100 margins);
4. the appended-segment end (and therefore the fence upper limit) drops the
   flash tail slot: end == payload address + 364;
5. exactly four r0=<slot> li.u32 loads live in the payload part (open guard
   + record, close guard + clear).

The five-class deadness evidence for the slot address itself (absolute
anchors, provenance-traced accesses, dynamic bases, flash pointer-words,
ROM/RAM-code scans) is machine-rechecked by analyze_hearing_aid_slot_address
.py against official bytes — this test only pins the builder-side geometry.

UNVALIDATED ON DEVICE: nothing here executes any payload; the candidate is
OFFLINE_UNTESTED and must not be flashed.
"""
import unittest

import pi32v2_asm as W

import hearing_aid_l1_image as IMG

PAYLOAD_ADDRESS = IMG.PAYLOAD_ADDRESS
RAM_SLOT = IMG.RAM_SLOT
HOLE = (0x12544e, 0x12569f)          # the statically dead 593B BSS hole


class SlotramSegmentTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.close_report, cls.close_code, cls.close_slot = \
            IMG.assemble_segment(close_hooks=True)
        cls.ram_report, cls.ram_code, cls.ram_slot = \
            IMG.assemble_segment(close_hooks=True, slot_ram=True)

    def test_01_geometry_unchanged_slot_in_ram(self):
        """364B segment, route-Y layout; slot is the pinned BSS word."""
        self.assertEqual(len(self.ram_code), 364)
        self.assertEqual(self.ram_slot, RAM_SLOT)
        self.assertNotEqual(self.ram_slot, PAYLOAD_ADDRESS + len(self.ram_code))
        self.assertEqual(self.ram_report['slot_location'], 'ram-bss')
        self.assertIn('0x125320', self.ram_report['slot_boot_value'])

    def test_02_only_slot_immediates_differ_from_close_variant(self):
        """Byte diff vs the route-Y segment stays inside the four slot loads."""
        diffs = [i for i, (a, b) in enumerate(zip(self.close_code, self.ram_code))
                 if a != b]
        self.assertEqual(len(self.close_code), len(self.ram_code))
        self.assertEqual(len(diffs), 16, 'expected 4 li.u32 immediates x 4B')
        self.assertTrue(all(d < 198 for d in diffs),
                        'diffs must stay inside the payload part (open+close)')
        # every differing byte sits inside one r0=<slot> li.u32 instruction
        listing = self.ram_report['listing']
        spans = [range(int(item['address'], 16) - PAYLOAD_ADDRESS,
                       int(item['address'], 16) - PAYLOAD_ADDRESS + 6)
                 for item in listing if item['text'] == f'r0 = {RAM_SLOT}']
        self.assertEqual(len(spans), 4)
        self.assertTrue(all(any(d in s for s in spans) for d in diffs))

    def test_03_slot_address_invariants(self):
        """Boot-zeroed, aligned, below QCY, centred in the dead hole."""
        start, end = IMG.BSS_ZERO_RANGE
        self.assertGreaterEqual(RAM_SLOT, start)
        self.assertLess(RAM_SLOT, end - 3)
        self.assertEqual(RAM_SLOT % 4, 0)
        self.assertLess(RAM_SLOT, IMG.QCY_BASE)
        self.assertGreaterEqual(RAM_SLOT - HOLE[0], 0x100)
        self.assertGreaterEqual(HOLE[1] - (RAM_SLOT + 4), 0x100)

    def test_04_segment_end_drops_flash_tail_slot(self):
        """Appended file ends at code end; no image slot, fence top follows."""
        self.assertEqual(self.ram_report['segment_end_runtime'],
                         f'0x{PAYLOAD_ADDRESS + 364:08x}')
        self.assertEqual(self.ram_report['segment_end_runtime'], '0x060e31e0')
        # route-Y flash-tail slot must keep its own geometry untouched
        self.assertEqual(self.close_report['segment_end_runtime'], '0x060e31e4')

    def test_05_payload_prefixes_match_standalone_sources(self):
        """Open/close payload bytes still equal their standalone assemblies."""
        self.assertTrue(self.ram_report['payload_prefix_matches_route_L'])
        self.assertTrue(self.ram_report['close_payload_matches_route_X'])

    def test_06_slotram_requires_close_hooks(self):
        """The switch is a close superset; open-only + slotram is an error."""
        with self.assertRaises(ValueError):
            IMG.apply_l1_patch(bytearray(0), close_hooks=False, slot_ram=True)
        with self.assertRaises(ValueError):
            IMG.assemble_segment(close_hooks=False, slot_ram=True)


if __name__ == '__main__':
    unittest.main()
