#!/usr/bin/env python3
"""Unit tests for the opt-in JL_ANC 44-word container (route D2ANC, INJ-05).

Mirrors the ADA-family test shape: whitelist exactness, write record plus
last-write readback with the explicit synthetic initial 0, generic-MMIO
service through BR28Model, and the strict-stop default when the switch is
off. Design source: docs/audio-input-paths.md §2.2b (ANC stop-reconfig-start
chain) + the whole-app li-constant register-propagation sweep (22 words) +
22 wall-pinned words of the runtime-indexed table-driven filter family
(0x606cfb2 domain; each pinned by an actual unmapped stop during the
reconfig-chain direct call).
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from br28_model import BR28Model, JLAncRegisters, JLClockCon3Registers
from probe_boot import Blocked


class JLAncRegistersTests(unittest.TestCase):
    def test_whitelist_is_exactly_the_44_enumerated_words(self):
        container = JLAncRegisters()
        self.assertEqual(len(container.WORDS), 44)
        for address in container.WORDS:
            self.assertTrue(container.handles(address), hex(address))
        for address in (0xfe0414, 0xfe0418, 0xfe041c, 0xfe0450,
                        0xfe0454, 0xfe0490, 0xfe04a0, 0xfe04ac, 0xfe04bc,
                        0xfe04c8, 0xfe04ec, 0xfe04f0, 0xfe04f8,
                        0xfe0500, 0xfe0538):
            self.assertFalse(container.handles(address), hex(address))

    def test_write_record_and_last_write_readback(self):
        container = JLAncRegisters()
        container.write(0xfe040c, 0x80000000)
        container.write(0xfe040c, 0x80000001)
        self.assertEqual(container.stores[0xfe040c]['writes'], 2)
        self.assertEqual(container.read(0xfe040c), 0x80000001)
        self.assertEqual(container.reads_after_write, 1)
        self.assertEqual(container.synthesized_initial_reads, 0)

    def test_read_before_any_write_serves_the_synthetic_zero(self):
        container = JLAncRegisters()
        self.assertEqual(container.read(0xfe0400), 0)
        self.assertEqual(container.synthesized_initial_reads, 1)
        container.write(0xfe0400, 0x102)
        self.assertEqual(container.read(0xfe0400), 0x102)

    def test_out_of_range_and_foreign_addresses_are_refused(self):
        container = JLAncRegisters()
        with self.assertRaises(ValueError):
            container.write(0xfe0414, 1)
        with self.assertRaises(ValueError):
            container.write(0xfe0400, -1)
        with self.assertRaises(ValueError):
            container.write(0xfe0400, 1 << 32)
        with self.assertRaises(ValueError):
            container.read(0xfe0500)

    def test_report_carries_the_whitelist_and_projection_faces(self):
        report = JLAncRegisters().report()
        self.assertEqual(report['word_writes'], 0)
        self.assertIn('0xfe0400', report['whitelisted_words'])
        self.assertIn('synthetic', report['readback_projection']['readback_semantics'])
        self.assertIn('synthetic-initial-0', report['assumption'])
        self.assertIn('no ANC effect',
                      report['readback_projection']['known_limitation'])


class Br28ModelServiceTests(unittest.TestCase):
    def test_switch_off_keeps_the_block_strictly_unmapped(self):
        model = BR28Model()
        with self.assertRaises(Blocked):
            model.read(0xfe0400)

    def test_switch_on_serves_reads_and_writes_through_the_generic_face(self):
        model = BR28Model(jl_anc_writes=True)
        model.write(0xfe0400, 0x102)          # CON bit1 set (start)
        self.assertEqual(model.read(0xfe0400), 0x102)
        model.write(0xfe0404, 0x41aa8c)       # DMA address word (candidate-2 face)
        self.assertEqual(model.read(0xfe0404), 0x41aa8c)
        self.assertEqual(model.jl_anc.word_writes, 2)
        with self.assertRaises(Blocked):
            model.read(0xfe0414)              # zero-touch neighbour stays unmapped

    def test_report_section_present_when_on(self):
        model = BR28Model(jl_anc_writes=True)
        result = model.report()
        self.assertIn('jl_anc_registers', result)
        self.assertIn('jl_anc_registers', result['assumptions'])


class JLClockCon3Tests(unittest.TestCase):
    def test_single_word_whitelist_and_readback(self):
        container = JLClockCon3Registers()
        self.assertTrue(container.handles(0xfd0024))
        for address in (0xfd0020, 0xfd0028, 0xfd0008):
            self.assertFalse(container.handles(address), hex(address))
        self.assertEqual(container.read(0xfd0024), 0)
        self.assertEqual(container.synthesized_initial_reads, 1)
        container.write(0xfd0024, 0x20000)
        self.assertEqual(container.read(0xfd0024), 0x20000)
        with self.assertRaises(ValueError):
            container.write(0xfd0020, 1)

    def test_model_service_on_and_off(self):
        model = BR28Model(jl_clock_con3_readback=True)
        model.write(0xfd0024, 0x20000)
        self.assertEqual(model.read(0xfd0024), 0x20000)
        with self.assertRaises(Blocked):
            BR28Model().read(0xfd0024)      # switch off: strictly unmapped


if __name__ == '__main__':
    unittest.main()
