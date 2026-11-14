#!/usr/bin/env python3
"""Fast-tier unit tests for the world snapshot framework (S1 module half).

Small-world round trip (reflexivity check #0: restore, compare zero steps,
re-capture byte-identical), the AZ-method counterexamples (a deleted state
field and an injected deviation must both turn the comparison red — proving
the check has discriminative power, not vacuous green), the drift double gate
(code fingerprint + manifest tamper both refuse loading) and the RAM
run-length codec boundaries (hole semantics: absence stays absence).
"""
import json
import struct
import sys
import unittest
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))

import world_snapshot as ws
from world_snapshot import (SnapshotError, WorldSnapshot, assert_equal, capture_world,
                            decode_ram_runs, decode_value, encode_ram_runs, encode_value,
                            pairs_of, restore_world)
from br28_icfg import IcfgValue
from br28_machine import DualCoreMachine
from br28_model import BR28Model, IRQController, PartialWord
from br28_unknown import UnknownWord
from br28_model import GpcntConValue, GpioOutValue


def attach_irq1(hardware):
    """Structural poke both worlds share: give CPU1 an IRQ controller without
    the full enable_multicore path (which would add M4-family state)."""
    hardware.irq1 = IRQController(initial_config=[None] * 32, initial_ipmask=None)
    hardware.multicore_enabled = True


def build_hardware():
    hardware = BR28Model(core_tick_configuration=True, core_tick_progress=(3, 7),
                         software_irq_command_projection=True, mmu_model=True)
    # Stop-point-shaped state pokes (the shapes the M3 inventory lists).
    timer = hardware.timer
    timer.con, timer.cnt, timer.prd = 0x1400, 9660, 119999
    timer.pending, timer.remainder, timer.source_remainder = True, 2, 1
    timer.expirations, timer.delivery_in_flight = 5, True
    tick0 = hardware.core_ticks[0]
    tick0.con, tick0.cnt, tick0.prd, tick0.writes = 1, 42, 99, 3
    tick0.pending = tick0.unresolved_overflow = tick0.delivery_in_flight = True
    tick0.expirations, tick0.acknowledgements = 1, 0
    hardware.core_ticks[1].writes = 1
    hardware.irq.config[5] = 0x33b55
    hardware.irq.ipmask = 1
    attach_irq1(hardware)
    hardware.irq1.config[3] = 2
    hardware.irq1.ipmask = 2
    commands = hardware.software_irq
    commands.pending[0].apply('or', 0xb)
    commands.source_enable[0].apply('or', 0x8)
    commands.commands, commands.acknowledgements, commands.source_commands = 4, 1, 2
    hardware.registers[0xffe00c] = 0x300001
    hardware.gpio[0xfd0504] = PartialWord()
    hardware.gpio[0xfd0504].apply('or', 5)
    hardware.lock_owner = 0
    hardware.current_core = 1
    hardware.core_icfg = {0: 0x300, 1: None}
    hardware.secondary_cpu_executed = True
    hardware.cpu1_control = 1
    hardware.cpu1_control_kick_writes = 2
    hardware.usb_disable_writes = ['0xffe8c0']
    hardware.p3_ana_read_served = 3
    hardware.counts.update({'reads': 11, 'writes': 7, 'completed_model_steps': 289849,
                            'opaque_pwm_configuration_reads': 1})
    hardware.writes = [{'address': '0xfd0400', 'value': '0x1'},
                       {'address': '0xffe1a0', 'value': '0x8'}]
    hardware.timer0_irq4_probe_armed = True
    hardware.mmu.con, hardware.mmu.begin, hardware.mmu.end = 1, 0x120000, 0x1227fe
    hardware.mmu.walks = 67
    hardware.mmu.last_walk = {'virtual': '0x49faf4', 'physical': '0x19faf4',
                              'entry_address': '0x1200d2', 'tag': '0x8000'}
    return hardware


def build_machine():
    machine = DualCoreMachine(software_irq_entry=True, core_tick_entry=True,
                              core_tick_rti=True, timer0_irq_entry=True, timer0_irq_rti=True,
                              idle_wait_projection=True, software_irq_rti=True,
                              event_clock_projection=True)
    machine.last_core, machine.stalls, machine.launches = 0, 5, 1
    machine.idle_wait_cores = {0}
    machine.deferred_core1_turns, machine.seen_cpu1_control_kicks = 7, 2
    machine.software_irq_deliveries = [{'kind': 'software_irq_entry', 'core': 0, 'irq': 127,
                                        'handler': '0x12ad2a', 'reti_from': '0x6066128'}]
    machine.core_tick_deliveries = [{'kind': 'core_tick_entry', 'core': 0, 'irq': 3}]
    machine.timer0_irq_deliveries = [{'kind': 'timer0_irq_entry', 'core': 1, 'irq': 4}]
    machine.irq_delivery_deferrals = 1
    machine.irq_delivery_deferral_events = [
        {'kind': 'irq_delivery_deferred', 'core': 0, 'irq': 4,
         'deferred_while': 'timer0_irq_handler'}]
    for index in range(40):
        machine.record_event({'kind': 'test_event', 'i': index})  # bounded 32 + true count
    machine.boot_anchors = [{'pc': '0xa144', 'next_pc': '0xc5fe', 'r0': '0x1', 'rets': '0xc604'}]
    machine.boot_anchor_count = 6
    machine.event_clock_advances = 12
    return machine


def build_cpu():
    registers = [0,
                 UnknownWord((None, 5, None, 255), (0x19f640, None, 0x19f641, None)),
                 IcfgValue(0x300, 0x3ff, (0, 2), 0x300),
                 GpioOutValue(0xfd0504, 0x5, 0xf),
                 GpcntConValue(0, 1)]
    registers += list(range(100, 111))
    return SimpleNamespace(core_id=0, pc=0x60679ba, steps=289849, seq=0x60679bc,
                           R=registers, flags=0, sp=0x123456, sp_known=True, usp=0x124000,
                           ssp=None, reti=0x12c57e, rets=0, irq_psr=None, icfg=0x300,
                           icfg_known=False, icfg_unknown_mask=0x300)


def build_mem():
    mem = SimpleNamespace(ram={}, unknown_origins={}, icfg_spills={})
    for offset in range(16):
        mem.ram[0x123000 + offset] = offset          # contiguous run
    mem.ram[0x123020] = 0xff                          # isolated byte after a hole
    for address in range(0x124840, 0x124850):
        mem.ram[address] = 0                          # known zeros (not unknown!)
    for offset in range(8):
        mem.ram[0x19fe00 + offset] = 0x10 + offset    # vector slots
    mem.ram[0x1a0000] = 0x5a                          # far sparse byte
    mem.unknown_origins = {0x123840 + 60: 0x19f640, 0x123843: 0x19f643}
    mem.icfg_spills = {0x124f00: IcfgValue(0x200, 0x3ff, (1, 3), 0x200)}
    return mem


def build_ledger():
    return SimpleNamespace(adapters=Counter({'parallel_byte_store_old_registers': 104,
                                             'gpcnt_con_readback_load': 1}),
                           rom_steps=Counter({'completed_model_steps_in_rom': 9}),
                           event_counts=Counter({'write': 50, 'read': 30}),
                           milestones=[{'kind': 'watched_pc', 'pc': '0x60679ba', 'core': 0}],
                           trace=[{'pc': '0x6067a00', 'core': 1}],
                           completed=289849,
                           stop={'kind': 'unmapped_memory', 'access': 'read',
                                 'address': '0xf27f4b', 'size': 4})


def small_world():
    return dict(cpus={'cpu0': build_cpu()}, mem=build_mem(), hardware=build_hardware(),
                machine=build_machine(), ledger=build_ledger())


def fresh_world():
    hardware = BR28Model(core_tick_configuration=True, core_tick_progress=(3, 7),
                         software_irq_command_projection=True, mmu_model=True)
    attach_irq1(hardware)
    return dict(cpus={'cpu0': SimpleNamespace(**{name: None for name in ws.M1_CPU_FIELDS})},
                mem=SimpleNamespace(ram={}, unknown_origins={}, icfg_spills={}),
                hardware=hardware,
                machine=DualCoreMachine(software_irq_entry=True, core_tick_entry=True,
                                        core_tick_rti=True, timer0_irq_entry=True,
                                        timer0_irq_rti=True, idle_wait_projection=True,
                                        software_irq_rti=True, event_clock_projection=True),
                ledger=SimpleNamespace(adapters=Counter(), rom_steps=Counter(),
                                       event_counts=Counter(), milestones=[], trace=[],
                                       completed=0, stop=None))


def captured(world, **extra):
    return capture_world(cpus=world['cpus'], mem=world['mem'], hardware=world['hardware'],
                         machine=world['machine'], ledger=world['ledger'],
                         context={'scenario': 'small_world_test', 'completed_steps': 289849},
                         **extra)


def reframed(data, section, mutate):
    """Re-frame snapshot bytes with one JSON section mutated (tamper helper)."""
    offset = 12  # magic + version
    blobs = []
    for _ in range(3):
        (length,) = struct.unpack_from('<I', data, offset)
        offset += 4
        blobs.append(json.loads(data[offset:offset + length]))
        offset += length
    index = {'header': 0, 'manifest': 1, 'body': 2}[section]
    mutate(blobs[index])
    out = bytearray(ws.MAGIC) + struct.pack('<I', ws.FORMAT_VERSION)
    for blob in blobs:
        encoded = json.dumps(blob, sort_keys=True, separators=(',', ':')).encode()
        out += struct.pack('<I', len(encoded)) + encoded
    return bytes(out) + data[offset:]


class SmallWorldRoundTrip(unittest.TestCase):
    def test_round_trip_zero_step_field_equality_and_byte_identity(self):
        world = small_world()
        snapshot = captured(world)
        data = snapshot.encode()
        loaded = WorldSnapshot.from_bytes(data)
        self.assertEqual(loaded.captured_modules, ['M1', 'M2', 'M3', 'M5', 'M6', 'M7'])
        fresh = fresh_world()
        result = restore_world(loaded, cpus=fresh['cpus'], mem=fresh['mem'],
                               hardware=fresh['hardware'], machine=fresh['machine'],
                               ledger=fresh['ledger'], expected_context={'scenario': 'small_world_test'})
        # Reflexivity check #0: restored state equals the original field by field.
        for key, original in world['cpus'].items():
            for field in ws.M1_CPU_FIELDS:
                assert_equal(getattr(fresh['cpus'][key], field), getattr(original, field),
                             f'M1.{key}.{field}')
        assert_equal(fresh['mem'].ram, world['mem'].ram, 'M2.ram')
        assert_equal(fresh['mem'].unknown_origins, world['mem'].unknown_origins, 'M2.unknown_origins')
        assert_equal(fresh['mem'].icfg_spills, world['mem'].icfg_spills, 'M2.icfg_spills')
        for field in ws.M3_TIMER_FIELDS:
            assert_equal(getattr(fresh['hardware'].timer, field),
                         getattr(world['hardware'].timer, field), f'M3.timer.{field}')
        for index, tick in enumerate(world['hardware'].core_ticks):
            for field in ws.M3_CORE_TICK_FIELDS:
                assert_equal(getattr(fresh['hardware'].core_ticks[index], field),
                             getattr(tick, field), f'M3.core_ticks[{index}].{field}')
        for name in ('irq', 'irq1'):
            for field in ws.M3_IRQ_FIELDS:
                assert_equal(getattr(getattr(fresh['hardware'], name), field),
                             getattr(getattr(world['hardware'], name), field), f'M3.{name}.{field}')
        for field in ws.M3_SOFTWARE_IRQ_FIELDS:
            assert_equal(getattr(fresh['hardware'].software_irq, field),
                         getattr(world['hardware'].software_irq, field), f'M3.software_irq.{field}')
        for name in ws.M3_STATE:
            assert_equal(getattr(fresh['hardware'], name), getattr(world['hardware'], name),
                         f'M3.{name}')
        for field in ws.M6_MMU_FIELDS:
            assert_equal(getattr(fresh['hardware'].mmu, field),
                         getattr(world['hardware'].mmu, field), f'M6.mmu.{field}')
        for name in ws.M5_STATE:
            if name == 'core_ids':
                continue
            assert_equal(getattr(fresh['machine'], name), getattr(world['machine'], name),
                         f'M5.{name}')
        self.assertEqual(list(fresh['machine'].cores), [], 'M5.core_ids (no cores launched)')
        for name in ws.M7_STATE:
            assert_equal(getattr(fresh['ledger'], name), getattr(world['ledger'], name),
                         f'M7.{name}')
        # Bounded-list + true-count pair survives exactly (M5 two-piece rule).
        self.assertEqual((len(fresh['machine'].events), fresh['machine'].event_count), (32, 40))
        # Collection tags keep restored counters/sets type-exact (not plain dicts).
        self.assertIsInstance(fresh['hardware'].counts, Counter)
        self.assertIsInstance(fresh['ledger'].adapters, Counter)
        self.assertIsInstance(fresh['machine'].idle_wait_cores, set)
        # Strong reflexivity: capturing the restored world reproduces the bytes.
        self.assertEqual(captured(fresh).encode(), data)
        self.assertEqual(result.digest, snapshot.digest())


class Counterexamples(unittest.TestCase):
    """AZ methodology: the comparison must be red when state is deleted or
    biased — a green-only comparison proves nothing. (Deleting a field inside
    the snapshot file itself is separately caught by the restore-side manifest
    gate; see DriftGates.test_deleted_field_in_body_refuses_restore.)"""

    def test_unapplied_state_field_must_turn_comparison_red(self):
        world = small_world()
        loaded = WorldSnapshot.from_bytes(captured(world).encode())
        fresh = fresh_world()
        restore_world(loaded, cpus=fresh['cpus'], mem=fresh['mem'],
                      hardware=fresh['hardware'], machine=fresh['machine'],
                      ledger=fresh['ledger'])
        # Simulate a restore adapter that skipped one field (Timer0's second-level
        # fractional remainder): reset it to the pre-restore value.
        fresh['hardware'].timer.source_remainder = 0
        with self.assertRaises(AssertionError) as caught:
            for field in ws.M3_TIMER_FIELDS:
                assert_equal(getattr(fresh['hardware'].timer, field),
                             getattr(world['hardware'].timer, field), f'M3.timer.{field}')
        self.assertIn('source_remainder', str(caught.exception))

    def test_injected_ram_deviation_must_turn_comparison_red(self):
        world = small_world()
        loaded = WorldSnapshot.from_bytes(captured(world).encode())
        fresh = fresh_world()
        restore_world(loaded, cpus=fresh['cpus'], mem=fresh['mem'],
                      hardware=fresh['hardware'], machine=fresh['machine'],
                      ledger=fresh['ledger'])
        fresh['mem'].ram[0x123000] ^= 1  # one biased RAM byte
        with self.assertRaises(AssertionError):
            assert_equal(fresh['mem'].ram, world['mem'].ram, 'M2.ram')


class DriftGates(unittest.TestCase):
    def test_fingerprint_mismatch_refuses_load(self):
        data = captured(small_world()).encode()
        tampered = reframed(data, 'header',
                            lambda header: header.update(code_fingerprint='0' * 64))
        with self.assertRaisesRegex(SnapshotError, 'fingerprint'):
            WorldSnapshot.from_bytes(tampered)

    def test_fingerprint_tracks_source_and_adapter_shape(self):
        import tempfile
        base = ws.code_fingerprint()
        with tempfile.NamedTemporaryFile() as handle:
            handle.write(b'extra source')
            handle.flush()
            self.assertNotEqual(ws.code_fingerprint(extra_sources=(('probe', b'extra source'),)),
                                base)
            self.assertEqual(ws.code_fingerprint(extra_sources=(('probe', b'extra source'),)),
                             ws.code_fingerprint(extra_sources=(('probe', b'extra source'),)))

    def test_tampered_manifest_section_refuses_load(self):
        data = captured(small_world()).encode()

        def mutate(manifest):
            manifest['M3']['objects']['timer'][1].append('bogus_field')
        with self.assertRaisesRegex(SnapshotError, 'manifest'):
            WorldSnapshot.from_bytes(reframed(data, 'manifest', mutate))

    def test_restore_side_manifest_drift_names_the_class(self):
        loaded = WorldSnapshot.from_bytes(captured(small_world()).encode())
        loaded.manifest['M3']['objects']['timer'][1].append('bogus_field')
        fresh = fresh_world()
        with self.assertRaisesRegex(SnapshotError, 'Timer0'):
            restore_world(loaded, cpus=fresh['cpus'], mem=fresh['mem'],
                          hardware=fresh['hardware'], machine=fresh['machine'],
                          ledger=fresh['ledger'])

    def test_deleted_field_in_body_refuses_restore(self):
        """A snapshot whose body lost a field (even with a consistent manifest)
        is refused by the restore-side manifest gate — deleting state cannot
        pass silently."""
        loaded = WorldSnapshot.from_bytes(captured(small_world()).encode())
        del loaded.body['M3']['objects']['timer']['fields']['source_remainder']
        reloaded = WorldSnapshot.from_bytes(loaded.encode())  # manifest re-derives: load ok
        fresh = fresh_world()
        with self.assertRaisesRegex(SnapshotError, 'Timer0'):
            restore_world(reloaded, cpus=fresh['cpus'], mem=fresh['mem'],
                          hardware=fresh['hardware'], machine=fresh['machine'],
                          ledger=fresh['ledger'])

    def test_truncated_and_foreign_files_refuse(self):
        data = captured(small_world()).encode()
        for broken in (data[:-3], data + b'x', b'not a snapshot' + data[12:]):
            with self.assertRaises(SnapshotError):
                WorldSnapshot.from_bytes(broken)

    def test_static_switch_mismatch_refuses_restore(self):
        loaded = WorldSnapshot.from_bytes(captured(small_world()).encode())
        fresh = fresh_world()
        wrong = BR28Model(core_tick_configuration=True, core_tick_progress=(3, 7),
                          software_irq_command_projection=True, mmu_model=True, gpio_model=True)
        attach_irq1(wrong)
        with self.assertRaisesRegex(SnapshotError, 'static configuration mismatch'):
            restore_world(loaded, cpus=fresh['cpus'], mem=fresh['mem'], hardware=wrong,
                          machine=fresh['machine'], ledger=fresh['ledger'])

    def test_capture_context_gate(self):
        loaded = WorldSnapshot.from_bytes(captured(small_world()).encode())
        fresh = fresh_world()
        kwargs = dict(cpus=fresh['cpus'], mem=fresh['mem'], hardware=fresh['hardware'],
                      machine=fresh['machine'], ledger=fresh['ledger'])
        restore_world(loaded, expected_context={'scenario': 'small_world_test'}, **kwargs)
        with self.assertRaisesRegex(SnapshotError, 'capture context mismatch'):
            restore_world(loaded, expected_context={'scenario': 'other_world'}, **kwargs)

    def test_save_and_load_via_file(self):
        import tempfile
        loaded = WorldSnapshot.from_bytes(captured(small_world()).encode())
        with tempfile.NamedTemporaryFile(suffix='.snapshot') as handle:
            path = Path(handle.name)
            loaded.save(path)
            again = WorldSnapshot.load(path)
        self.assertEqual(again.encode(), loaded.encode())
        self.assertEqual(again.digest(), loaded.digest())


class UncapturedStateRefuses(unittest.TestCase):
    def test_m4_projection_family_refuses_capture(self):
        hardware = BR28Model(watchdog_projection=True)
        with self.assertRaisesRegex(SnapshotError, 'watchdog'):
            capture_world(hardware=hardware)

    def test_full_world_cpu_refuses_minimal_m1(self):
        cpu = build_cpu()
        cpu.cache = {}  # decode memo: excluded by design, but marks a full CPU
        with self.assertRaisesRegex(SnapshotError, 'capture-wiring'):
            capture_world(cpus={'cpu0': cpu})

    def test_extra_memory_attribute_refuses_capture(self):
        mem = build_mem()
        mem.regions = ()
        with self.assertRaisesRegex(SnapshotError, 'unexpected attributes'):
            capture_world(mem=mem)


class ValueCodec(unittest.TestCase):
    def test_abstract_values_round_trip(self):
        values = [UnknownWord((None, 5, None, 255), (0x19f640, None, 0x19f641, None)),
                  IcfgValue(0x300, 0x3ff, (0, 2), 0x300),
                  GpioOutValue(0xfd0504, 0x5, 0xf),
                  GpcntConValue(0, 1),
                  PartialWord(0x5, 0xf)]
        for value in values:
            assert_equal(decode_value(encode_value(value)), value)

    def test_abstract_value_mismatch_is_red(self):
        with self.assertRaises(AssertionError):
            assert_equal(PartialWord(0x5, 0xf), GpcntConValue(0, 1))
        with self.assertRaises(AssertionError):
            assert_equal(PartialWord(0x5, 0xf), PartialWord(0x5, 0xe))

    def test_collection_tags_round_trip_type_exact(self):
        assert_equal(decode_value(encode_value({1, 3, 2})), {1, 2, 3})
        self.assertIsInstance(decode_value(encode_value({1, 2})), set)
        counter = Counter({'reads': 11, 'writes': 7})
        assert_equal(decode_value(encode_value(counter)), counter)
        self.assertIsInstance(decode_value(encode_value(counter)), Counter)
        assert_equal(decode_value(pairs_of({0x123: 5, 0x122: None})), {0x123: 5, 0x122: None})
        assert_equal(decode_value(encode_value((4, 5))), (4, 5))
        assert_equal(decode_value(encode_value(b'\x01\x02')), b'\x01\x02')

    def test_whitelist_violations_refuse(self):
        for bad in (1.5, object(), {1: 'a'}):
            with self.assertRaises(SnapshotError):
                encode_value(bad)
        # Mixed int/str sets still encode deterministically (type-first sort key).
        self.assertEqual(encode_value({2, 'b', 1}), {'$set': [1, 2, 'b']})

    def test_unknown_tag_refuses(self):
        with self.assertRaisesRegex(SnapshotError, 'unknown value tag'):
            decode_value({'$new_state_kind': 1})


class RamRunLength(unittest.TestCase):
    def round_trip(self, ram):
        decoded = decode_ram_runs(encode_ram_runs(ram))
        assert_equal(decoded, ram)
        return decoded

    def test_boundaries(self):
        self.assertEqual(encode_ram_runs({}), [])
        self.round_trip({})
        self.assertEqual(encode_ram_runs({0x100: 1}), [(0x100, 1, b'\x01')])
        self.round_trip({0x100: 1})
        # Adjacent addresses merge into one run; holes stay absent.
        runs = encode_ram_runs({0x100: 1, 0x101: 2, 0x103: 4})
        self.assertEqual([(start, length) for start, length, _ in runs],
                         [(0x100, 2), (0x103, 1)])
        decoded = self.round_trip({0x100: 1, 0x101: 2, 0x103: 4})
        self.assertNotIn(0x102, decoded)  # hole semantics: absence stays absence
        # Known zeros are state, distinct from unknown holes.
        decoded = self.round_trip({0x200: 0})
        self.assertEqual(decoded, {0x200: 0})
        # Full-range and high addresses.
        ram = {address: address & 0xff for address in range(0x123000, 0x124000)}
        self.assertEqual(len(encode_ram_runs(ram)), 1)
        self.round_trip(ram)
        self.round_trip({0xffffffff: 0xaa})
        # Unsorted input dict still encodes sorted.
        assert_equal(decode_ram_runs(encode_ram_runs({0x300: 3, 0x100: 1, 0x200: 2})),
                     {0x100: 1, 0x200: 2, 0x300: 3})

    def test_invalid_inputs_refuse(self):
        with self.assertRaises(SnapshotError):
            encode_ram_runs({0x100: 256})   # byte out of range
        with self.assertRaises(SnapshotError):
            encode_ram_runs({0x100: 'x'})
        with self.assertRaises(SnapshotError):
            decode_ram_runs([[0x100, 2, b'\x01'], [0x101, 1, b'\x02']])  # overlap
        with self.assertRaises(SnapshotError):
            decode_ram_runs([[0x200, 1, b'\x01'], [0x100, 1, b'\x02']])  # unsorted
        with self.assertRaises(SnapshotError):
            decode_ram_runs([[0x100, 2, b'\x01']])                        # length mismatch
        with self.assertRaises(SnapshotError):
            decode_ram_runs([[0x100, 0, b'']])                            # empty run
        with self.assertRaises(SnapshotError):
            decode_ram_runs(['not a segment'])


if __name__ == '__main__':
    unittest.main()
