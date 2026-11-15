#!/usr/bin/env python3
"""Semantics pinning for the O3 Memory.read access-path work (probe_boot.Memory).

docs/o3-memory-design.md §7.3: every optimization here must keep behavior
byte-identical. These tests pin the region-lookup semantics that A2's
last-hit hint / sorted-index fast path and A3a's merged traversal must
preserve, on both the non-overlapping (index) and overlapping (linear
fallback) paths. No private ROM, device access or firmware output.
"""
import unittest

from probe_boot import ENTRY, application, explore
from probe_boot_handoff import RAM_CODE, package_header, profile, SENTINEL_A
from probe_pi32v2 import ROOT, upstream


class RegionLookupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module=upstream()
        firmware=ROOT/'firmware/QCY_H3S_7018F_1T2_release_note_V1.1.8_011.ufw'
        cls.app=application(firmware);cls.header=package_header(firmware)

    def handoff(self):
        h=profile(self.header,SENTINEL_A)
        h['description']['phase']='isolated O3 region lookup semantics test; not contiguous startup'
        return h

    def _fetch_case(self,h,pc,budget=4):
        return explore(self.module,self.app,name='o3_region_lookup',handoff=h,
                       diagnostics={'entry_pc':pc},budget=budget)

    def test_uninitialized_fetch_reports_first_region_byte(self):
        # Baseline (non-overlapping): an uninitialized fetch inside qcy_ram_code
        # reports the region kind, the fetch operation and the first missing
        # byte. This is also the first_missing pin for A3a's merged traversal.
        pc=RAM_CODE[0]+8
        result=self._fetch_case(self.handoff(),pc)
        self.assertEqual(result['completed_steps'],0)
        self.assertEqual(result['stop']['kind'],'uninitialized_memory')
        self.assertEqual(result['stop']['region'],'qcy_ram_code')
        self.assertEqual(result['stop']['access'],'fetch')
        self.assertEqual(result['stop']['first_missing'],hex(pc))

    def test_first_missing_skips_known_prefix_bytes(self):
        # Seeding a known prefix must move first_missing to the first hole:
        # the missing check walks bytes in order and stops at the first gap,
        # regardless of already-known bytes before it.
        h=self.handoff();pc=RAM_CODE[0]+8
        h['seeds'].append((pc,bytes.fromhex('11223344')))
        result=self._fetch_case(h,pc)
        self.assertEqual(result['completed_steps'],0)
        self.assertEqual(result['stop']['kind'],'uninitialized_memory')
        self.assertEqual(result['stop']['first_missing'],hex(pc+4))

    def test_overlapping_regions_fall_back_to_first_declared(self):
        # Overlap detection must fall back to the original linear scan, where
        # the FIRST declared region wins (a sorted index would reorder matches
        # and change observable region kinds; the fallback keeps them).
        h=self.handoff();pc=RAM_CODE[0]+8
        h['regions']=((pc-0x40,pc+0x40,'synthetic_overlap_head'),)+tuple(h['regions'])
        result=self._fetch_case(h,pc)
        self.assertEqual(result['stop']['kind'],'uninitialized_memory')
        self.assertEqual(result['stop']['region'],'synthetic_overlap_head')

    def test_disjoint_inserted_region_keeps_index_lookup(self):
        # A disjoint region inserted at the head must not disturb matches
        # elsewhere: the sorted-index path still resolves qcy_ram_code.
        h=self.handoff();pc=RAM_CODE[0]+8
        h['regions']=((0x110000,0x110100,'disjoint_inserted'),)+tuple(h['regions'])
        result=self._fetch_case(h,pc)
        self.assertEqual(result['stop']['kind'],'uninitialized_memory')
        self.assertEqual(result['stop']['region'],'qcy_ram_code')

    def test_unmapped_fetch_reports_detail_fields(self):
        # Outside every region the tail blocker must still carry the detail
        # dict (operation/address/size) built after the A1 deferral point.
        result=self._fetch_case(self.handoff(),0x00400000)
        self.assertEqual(result['completed_steps'],0)
        self.assertEqual(result['stop']['kind'],'unmapped_instruction_fetch')
        self.assertEqual(result['stop']['access'],'fetch')
        self.assertEqual(result['stop']['address'],'0x400000')
        self.assertEqual(result['stop']['size'],6)


if __name__=='__main__':
    unittest.main()
