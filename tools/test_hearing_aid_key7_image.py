#!/usr/bin/env python3
"""Offline verification of the hearing-aid L1 double-click toggle image (route L1KEY).

Exercises the builder-side segment assembly for the cmd 7 (double-click) HA
toggle variant (hearing_aid_l1_image.py, also used by build_quiet_candidate.py
--hearing-aid-l1-key7, which implies --hearing-aid-l1-slotram) WITHOUT any
firmware input (docs/hearing-aid-l1-key7.md). The byte-level implant/diff
assertions run at build time (verify_l1_image); this test pins:

1. the key7 segment geometry (open 156B + close 64B + toggle stub 38B = 258B)
   and the toggle stub closing the segment;
2. the slot is the pinned boot-zeroed BSS word (same as the slotram variant);
3. the toggle stub shape and every firmware target: the slot guard, the
   db3c(1,1) ANC OFF request (li.u32 0x0600db3c + call.r), the open/close
   internal calls reaching the payload entries, and the final r4=1 ->
   goto 0x0602530c (handler "handled" return);
4. key7 requires close_hooks + slot_ram (a slotram superset);
5. the open/close payload prefixes still equal their standalone assemblies
   (the fixed open payload, route L1KEY).

UNVALIDATED ON DEVICE: nothing here executes any payload; the candidate is
OFFLINE_UNTESTED and must not be flashed.
"""
import unittest

import pi32v2_asm as W

import hearing_aid_l1_image as IMG

PAYLOAD_ADDRESS = IMG.PAYLOAD_ADDRESS
RAM_SLOT = IMG.RAM_SLOT
ANC_DISPATCH = IMG.ANC_DISPATCH
HANDLER_RET = IMG.HANDLER_RET


class Key7ToggleSegmentTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.slotram_report, cls.slotram_code, _ = \
            IMG.assemble_segment(close_hooks=True, slot_ram=True)
        cls.key7_report, cls.key7_code, cls.key7_slot = \
            IMG.assemble_segment(close_hooks=True, slot_ram=True, key7_toggle=True)

    def test_01_geometry_and_slot(self):
        """open 156 + close 64 + toggle 38 = 258B; slot stays the BSS word."""
        self.assertEqual(len(self.key7_code), 258)
        self.assertEqual(self.key7_slot, RAM_SLOT)
        self.assertEqual(self.key7_report['variant'], 'open+close+slotram+key7toggle')
        self.assertEqual(self.key7_report['slot_location'], 'ram-bss')
        self.assertEqual(self.key7_report['segment_end_runtime'], '0x060e3176')

    def test_02_payload_prefixes_match_standalone(self):
        """Open (fixed) and close payload bytes equal their standalone builds."""
        self.assertTrue(self.key7_report['payload_prefix_matches_route_L'])
        self.assertTrue(self.key7_report['close_payload_matches_route_X'])
        self.assertTrue(self.key7_report['toggle_stub_shape_matches'])

    def test_03_toggle_shape_and_targets(self):
        """Toggle stub: slot guard -> db3c(1,1) -> open, else close -> handled."""
        listing = self.key7_report['listing']
        toggle = [i for i in listing
                  if int(i['address'], 16) >= int(self.key7_report['toggle_address'], 16)]
        ids = [i['insn'] for i in toggle]
        self.assertEqual(ids, IMG.TOGGLE_INSN_IDS, 'toggle stub shape mismatch')
        texts = [i['text'] for i in toggle]
        # the ANC OFF request loads db3c and calls it right after
        i_db3c = texts.index(f'r6 = {ANC_DISPATCH}')
        self.assertEqual(texts[i_db3c + 1], 'call r6', 'db3c load must be called')
        self.assertEqual(texts[i_db3c - 1], 'r1 = 1', 'db3c flag must be 1')
        self.assertEqual(texts[i_db3c - 2], 'r0 = 1', 'db3c mode must be 1 (OFF)')
        # the internal calls reach open/close payload entries
        calls = [(i, t) for i, t in enumerate(texts) if t.startswith('call ')]
        self.assertEqual(len(calls), 3, 'three calls: db3c, open, close')
        # final tail: r4 = 1 -> r6 = HANDLER_RET -> goto r6
        self.assertEqual(texts[-3], 'r4 = 1')
        self.assertEqual(texts[-2], f'r6 = {HANDLER_RET}')
        self.assertEqual(texts[-1], 'goto r6')

    def test_04_slot_guard_before_any_call(self):
        """The slot is read (and branched on) before the db3c/open/close calls."""
        texts = [i['text'] for i in self.key7_report['listing']
                 if int(i['address'], 16) >= int(self.key7_report['toggle_address'], 16)]
        i_guard = next(i for i, t in enumerate(texts) if t == f'r0 = {RAM_SLOT}')
        i_first_call = next(i for i, t in enumerate(texts) if t.startswith('call '))
        self.assertLess(i_guard, i_first_call, 'slot guard must precede the first call')

    def test_05_key7_requires_slotram(self):
        """The switch is a slotram superset; open-only + key7 is an error."""
        with self.assertRaises(ValueError):
            IMG.apply_l1_patch(bytearray(0), close_hooks=True, slot_ram=False,
                               key7_toggle=True)
        with self.assertRaises(ValueError):
            IMG.assemble_segment(close_hooks=False, slot_ram=True, key7_toggle=True)

    def test_06_open_payload_is_the_fixed_route_l1key(self):
        """The composed open prefix is the fixed 156B payload (r0 reloads)."""
        open_prefix = self.key7_code[:156]
        from pi32v2_asm import disassemble
        seq = [t for _a, _i, t, _r in disassemble(open_prefix, PAYLOAD_ADDRESS)]
        # 3 cross-call reloads (set_scene/set_notify/start) + 1 error-path stream
        # reload in open_err_release = 4 `r0 = r4` total (route L1KEY fix).
        self.assertEqual(seq.count('r0 = r4'), 4,
                         'open payload must reload stream (3x cross-call + 1x release)')


if __name__ == '__main__':
    unittest.main(verbosity=2)
