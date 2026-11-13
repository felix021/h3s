#!/usr/bin/env python3
"""Offline verification of the R4 keyhook image variant (route AQ).

Exercises the builder-side segment assembly for the --r4-keyhook candidate
(hearing_aid_l1_image.py, also used by build_quiet_candidate.py
--r4-keyhook, which implies --hearing-aid-l1-slotram) WITHOUT any firmware
input (docs/r4-command-hook.md). The byte-level implant/diff assertions run
at build time (verify_l1_image); this test pins:

1. the composed segment is the slotram segment + exactly 78 keyhook bytes:
   the first 364B are byte-identical to the route-CT variant, the keyhook
   opens at offset 364 (runtime 0x060e31e0) and closes the segment;
2. the keyhook tail equals the standalone route-AQ payload assembly and
   carries both VM dispatcher anchors (set/get_config) exactly once;
3. the registry constants: cmdid 0x30 entry base, null setter word at
   entry+8, original bytes zero, entry inside the dispatch-table range and
   on its 28B stride grid;
4. the segment end (and therefore the fence upper limit and the appended
   file size) grows by exactly the keyhook bytes;
5. the switch ladder rejects r4_keyhook without close_hooks+slotram.

The full-app reference scan proving no static reference targets the cmdid
0x30 entry (li.u32 + raw LE32 over official app.bin) ran in route AQ and is
recorded in docs/r4-command-hook.md; this test only pins the builder-side
geometry and the registry-slot addresses.

UNVALIDATED ON DEVICE: nothing here executes any payload; the candidate is
OFFLINE_UNTESTED and must not be flashed.
"""
import struct
import unittest

import pi32v2_asm as W

import hearing_aid_l1_image as IMG

PAYLOAD_ADDRESS = IMG.PAYLOAD_ADDRESS
KEYHOOK_BYTES = 78
KEYHOOK_OFFSET = 364                      # open 134 + close 64 + stubs 166
R4_TABLE_START = 0x060a9b78               # RCSP custom-command dispatch table
R4_TABLE_END = 0x060a9d1c


class R4KeyhookImageTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.ram_report, cls.ram_code, _slot = \
            IMG.assemble_segment(close_hooks=True, slot_ram=True)
        cls.r4_report, cls.r4_code, _slot2 = \
            IMG.assemble_segment(close_hooks=True, slot_ram=True, r4_keyhook=True)

    def test_01_segment_is_slotram_plus_keyhook(self):
        """442B = 364B slotram segment + 78B keyhook at offset 364."""
        self.assertEqual(len(self.r4_code), KEYHOOK_OFFSET + KEYHOOK_BYTES)
        self.assertEqual(self.r4_code[:KEYHOOK_OFFSET], self.ram_code,
                         'L1 part must stay byte-identical to route CT')
        self.assertEqual(self.r4_report['r4_keyhook_address'],
                         f'0x{PAYLOAD_ADDRESS + KEYHOOK_OFFSET:08x}')
        self.assertEqual(self.r4_report['variant'],
                         'open+close+slotram+r4keyhook')
        self.assertTrue(self.r4_report['r4_payload_matches_route_AQ'])

    def test_02_keyhook_anchors(self):
        """Standalone-equal tail carrying both VM dispatcher anchors once."""
        standalone = W.code_sections(
            W.read_object(W.assemble_file(IMG.R4_KEYHOOK_SOURCE)))[0][1]
        self.assertEqual(
            self.r4_code[KEYHOOK_OFFSET:], standalone,
            'composed keyhook tail != standalone route-AQ payload')
        loads = [int(text.split('= ')[1]) for _a, iid, text, _r
                 in W.disassemble(self.r4_code[KEYHOOK_OFFSET:])
                 if iid == 'li.u32']
        self.assertEqual(sorted(loads),
                         sorted([IMG.R4_SET_CONFIG, IMG.R4_GET_CONFIG]))

    def test_03_registry_slot_geometry(self):
        """cmdid 0x30 entry: on the table grid, inside the table, null setter."""
        entry, slot = IMG.R4_REG_ENTRY, IMG.R4_REG_SLOT
        self.assertEqual(slot, entry + 8, 'setter word must be entry+8')
        self.assertGreaterEqual(entry, R4_TABLE_START)
        self.assertLess(slot + 4, R4_TABLE_END)
        self.assertEqual((entry - R4_TABLE_START) % 28, 0,
                         'entry must sit on the 28B dispatch-table grid')
        self.assertEqual(IMG.R4_REG_SLOT_ORIGINAL, b'\x00\x00\x00\x00')
        self.assertEqual(IMG.R4_CMDID, 0x30)
        # entry+24 cmdid byte and entry+4 getter stay untouched by the edit
        # (the builder only writes the 4B setter word; verify_l1_image
        # re-decodes both from the final plaintext at build time)

    def test_04_geometry_grows_by_keyhook_only(self):
        """Segment end / fence top follow the 78B growth exactly."""
        self.assertEqual(
            self.r4_report['segment_end_runtime'],
            f'0x{PAYLOAD_ADDRESS + KEYHOOK_OFFSET + KEYHOOK_BYTES:08x}')
        self.assertEqual(
            self.ram_report['segment_end_runtime'],
            f'0x{PAYLOAD_ADDRESS + KEYHOOK_OFFSET:08x}')

    def test_05_switch_ladder_requires_supersets(self):
        """r4_keyhook without close_hooks+slotram is an error."""
        with self.assertRaises(ValueError):
            IMG.assemble_segment(close_hooks=False, slot_ram=False,
                                 r4_keyhook=True)
        with self.assertRaises(ValueError):
            IMG.assemble_segment(close_hooks=True, slot_ram=False,
                                 r4_keyhook=True)
        with self.assertRaises(ValueError):
            IMG.apply_l1_patch(bytearray(0), close_hooks=True, slot_ram=False,
                               r4_keyhook=True)


if __name__ == '__main__':
    unittest.main()
