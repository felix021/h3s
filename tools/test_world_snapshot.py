#!/usr/bin/env python3
"""Fast-tier unit tests for the world snapshot framework (S1 module half).

Small-world round trip (reflexivity check #0: restore, compare zero steps,
re-capture byte-identical), the AZ-method counterexamples (a deleted state
field and an injected deviation must both turn the comparison red — proving
the check has discriminative power, not vacuous green), the drift double gate
(code fingerprint + manifest tamper both refuse loading), the RAM run-length
codec boundaries (hole semantics: absence stays absence), the M4
projection-family adapters (all slots round-trip; one-shot event bits stay
consumed; carry state resumes exactly; switch/static mismatches refuse) and
the full M1 CPU inventory (route BN: dual-core 30-field round trip, abstract
GPR/IcfgState precision, memory-ownership guards, M1+M3+M4+M7 mid world).
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
from br28_icfg import IcfgState, IcfgValue
from br28_machine import DualCoreMachine
from br28_model import (BR28Model, CRC_ENGINE, EFUSE_CON0, EFUSE_CON1, EFUSE_RDAT,
                        ICU_BASES, IRQController, P33AwkupRegisters, P33CanaryWatchRegisters,
                        PartialWord)
from br28_unknown import UnknownWord
from br28_model import GpcntConValue, GpioOutValue
from probe_boot import Blocked


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


def build_cpu(core_id=0):
    """Full M1 inventory carrier in a stop-point shape: abstract GPR slots,
    partial ICFG sub-object, in-flight execution context on both cores, bank
    and first-task frame bookkeeping (route BN design §M1 groups)."""
    registers = [0,
                 UnknownWord((None, 5, None, 255), (0x19f640, None, 0x19f641, None)),
                 IcfgValue(0x300, 0x3ff, (core_id, 2), 0x300),
                 GpioOutValue(0xfd0504, 0x5, 0xf),
                 GpcntConValue(0, 1)]
    registers += list(range(100, 111))
    icfg_state = IcfgState(core_id, 0x300, 0x3ff)
    icfg_state.status_generation, icfg_state.writes = 2, 5
    icfg_state.bit9_updates, icfg_state.task_return_projections = 1, 0
    return SimpleNamespace(
        core_id=core_id, pc=0x60679ba + 2 * core_id, steps=289849 + core_id,
        seq=0x60679bc + 2 * core_id, R=registers, flags=core_id, sp=0x123456,
        sp_known=True, usp=0x124000, ssp=None if core_id == 0 else 0x124840,
        reti=0x12c57e, rets=0, irq_psr=None if core_id == 0 else 3,
        icfg=0x300, icfg_known=False, icfg_unknown_mask=0x300,
        icfg_state=icfg_state,
        bootstrap_context=core_id == 1, bootstrap_icfg_pending=core_id == 1,
        active_context=None if core_id == 0 else 'core_tick_handler',
        opaque_source=None,
        repeat=None if core_id == 0 else
        {'start': 0x4180, 'end': 0x4182, 'register': None, 'remaining': 5,
         'multi_block': True},
        after_then=[1, 2] if core_id == 0 else None,
        last_explicit_ssp=0x124840, last_explicit_usp=None,
        unresolved_ssp_candidates=(0x124840, 0x123840) if core_id == 1 else None,
        task_frame_load=None, task_frame_restore=None,
        task_gpr_frame={'tcb': 0x127e10, 'base': 0x19f630} if core_id == 1 else None,
        first_task_return_frame=None if core_id == 1 else
        {'tcb': 0x127db8, 'base': 0x19f7b0, 'next_pc': 0x606612c,
         'setup_pcs': ['0x6066106', '0x6066108'], 'gprs': (
             0, UnknownWord((None, 5, None, 255), (0x19f640, None, 0x19f641, None)),
             IcfgValue(0x300, 0x3ff, (core_id, 2), 0x300)) + tuple(range(100, 113))})


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
        self.assertEqual(loaded.captured_modules, ['M1', 'M2', 'M3', 'M4', 'M5', 'M6', 'M7'])
        # All M4 slots exist and are None (no projection switch on in this world).
        self.assertEqual(loaded.body['M4']['objects'],
                         {slot: None for slot in ws.M4_SLOTS})
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
    def test_m4_projection_extra_state_attribute_refuses_capture(self):
        """M4 families are wired now; the missing-state guard moves one level
        down: an attribute on a projection object that is neither in the state
        field list nor a static fixture (e.g. a new runtime field the adapter
        does not know) must refuse with the attribute named."""
        hardware = BR28Model(watchdog_projection=True)
        hardware.watchdog.unmodelled_new_state = 1.5  # float: not encodable as static
        with self.assertRaisesRegex(SnapshotError, 'unmodelled_new_state'):
            capture_world(hardware=hardware)

    def test_full_cpu_captures_and_unknown_attribute_refuses(self):
        """M1 is the full inventory now; the missing-state guard moved one
        level down: the §M9 memo pair (cache/mutable_decode_bytes), the
        fingerprint-rebuilt statics (tab/models) and the M2 mem reference are
        skipped by design on a full CPU, while any attribute outside inventory
        + skip refuses, named."""
        mem = build_mem()
        cpu = build_cpu()
        cpu.cache = {}
        cpu.mutable_decode_bytes = {0x60679ba: b'\x12\x34'}
        cpu.tab, cpu.models = object(), {}
        cpu.mem = mem
        snapshot = capture_world(cpus={'cpu0': cpu}, mem=mem)
        record = snapshot.body['M1']['objects']['cpu0']
        self.assertEqual(sorted(record['fields']), sorted(ws.M1_CPU_FIELDS))
        self.assertIsInstance(record['fields']['icfg_state'], dict)  # IcfgState record
        cpu.new_irq_shadow = object()  # a future CPU state kind the adapter knows not
        with self.assertRaisesRegex(SnapshotError, 'new_irq_shadow'):
            capture_world(cpus={'cpu0': cpu}, mem=mem)

    def test_extra_memory_attribute_refuses_capture(self):
        mem = build_mem()
        mem.regions = ()
        with self.assertRaisesRegex(SnapshotError, 'unexpected attributes'):
            capture_world(mem=mem)


class M1FullCPUInventory(unittest.TestCase):
    """Route BN: the full 30-field CPU inventory on both cores — dual-core
    round trip + byte identity, Blocked-semantics abstract precision
    (UnknownWord origins, IcfgValue identity vs status_generation), the
    ownership guards (M2 mem identity, core existence stays M5) and the
    combined M1+M3+M4+M7 mid-world round trip."""

    def dual_world(self):
        return {'cpu0': build_cpu(0), 'cpu1': build_cpu(1)}

    def fresh_cpus(self):
        return {key: SimpleNamespace(**{name: None for name in ws.M1_CPU_FIELDS})
                for key in ('cpu0', 'cpu1')}

    def assert_cpu_equal(self, actual, expected, key):
        for field in ws.M1_CPU_FIELDS:
            assert_equal(getattr(actual, field), getattr(expected, field),
                         f'M1.{key}.{field}')
        if getattr(expected, 'icfg_state', None) is not None:
            for field in ws.M1_ICFG_STATE_FIELDS:
                assert_equal(getattr(actual.icfg_state, field),
                             getattr(expected.icfg_state, field),
                             f'M1.{key}.icfg_state.{field}')

    def test_dual_core_full_field_round_trip_and_byte_identity(self):
        world = self.dual_world()
        snapshot = capture_world(cpus=world, context={'scenario': 'm1_full'})
        for key in ('cpu0', 'cpu1'):
            record = snapshot.body['M1']['objects'][key]
            self.assertEqual(record['class'], type(world[key]).__name__)
            self.assertEqual(sorted(record['fields']), sorted(ws.M1_CPU_FIELDS))
        loaded = WorldSnapshot.from_bytes(snapshot.encode())
        fresh = self.fresh_cpus()
        restore_world(loaded, cpus=fresh, expected_context={'scenario': 'm1_full'})
        for key, original in world.items():
            self.assert_cpu_equal(fresh[key], original, key)
        # The restored icfg_state is a live IcfgState, not a namespace shell.
        self.assertIsInstance(fresh['cpu0'].icfg_state, IcfgState)
        self.assertEqual(fresh['cpu1'].repeat['remaining'], 5)
        self.assertEqual(fresh['cpu0'].first_task_return_frame['next_pc'], 0x606612c)
        # Strong reflexivity: re-capturing the restored dual-core world (same
        # capture context) is byte-identical.
        self.assertEqual(capture_world(cpus=fresh, context={'scenario': 'm1_full'}).encode(),
                         snapshot.encode())

    def test_abstract_precision_blocked_semantics_and_icfg_identity(self):
        world = self.dual_world()
        loaded = WorldSnapshot.from_bytes(
            capture_world(cpus=world, context={'scenario': 'm1_abstract'}).encode())
        fresh = self.fresh_cpus()
        restore_world(loaded, cpus=fresh)
        original, restored = world['cpu0'], fresh['cpu0']
        # UnknownWord GPR slot: unknown bytes AND physical origins survive exactly.
        assert_equal(restored.R[1], original.R[1], 'R[1]')
        self.assertEqual(restored.R[1].origins[2], 0x19f641)
        # IcfgValue keeps value/known/origin/identity; its Blocked __eq__ never
        # runs — the comparison is field-by-field.
        assert_equal(restored.R[2], original.R[2], 'R[2]')
        # Nested frame transport: gprs tuple carries UnknownWord+IcfgValue through
        # an ordinary dict field.
        assert_equal(restored.first_task_return_frame['gprs'],
                     original.first_task_return_frame['gprs'], 'frame.gprs')
        # status_generation is state: the restored IcfgState mints reads whose
        # identity still validates against the restored generation.
        assert_equal(restored.icfg_state.read(), original.icfg_state.read(),
                     'icfg_state.read()')
        self.assertEqual(restored.icfg_state.read().origin, (0, 2))
        # Cross-field drift is red: a restored generation off by one breaks the
        # read identity against the snapshot's IcfgValue origins.
        restored.icfg_state.status_generation += 1
        self.assertNotEqual(restored.icfg_state.read().origin, original.icfg_state.read().origin)

    def test_missing_or_wrong_field_must_turn_comparison_red(self):
        world = self.dual_world()
        loaded = WorldSnapshot.from_bytes(
            capture_world(cpus=world, context={'scenario': 'm1_red'}).encode())
        fresh = self.fresh_cpus()
        restore_world(loaded, cpus=fresh)
        fresh['cpu1'].repeat = None  # as if the REP-body context was never restored
        with self.assertRaises(AssertionError) as caught:
            self.assert_cpu_equal(fresh['cpu1'], world['cpu1'], 'cpu1')
        self.assertIn('repeat', str(caught.exception))
        fresh['cpu1'].repeat = world['cpu1'].repeat
        fresh['cpu0'].icfg_state.status_generation = 0  # sub-object field drift
        with self.assertRaises(AssertionError) as caught:
            self.assert_cpu_equal(fresh['cpu0'], world['cpu0'], 'cpu0')
        self.assertIn('status_generation', str(caught.exception))

    def test_memory_ownership_guards_capture_and_restore(self):
        # Capture: a CPU referencing memory without mem= drops RAM state — refuse.
        cpu = build_cpu()
        cpu.mem = build_mem()
        with self.assertRaisesRegex(SnapshotError, 'M2'):
            capture_world(cpus={'cpu0': cpu})
        # Capture: a per-CPU memory distinct from the M2 target — refuse.
        with self.assertRaisesRegex(SnapshotError, 'shares one M2 memory'):
            capture_world(cpus={'cpu0': cpu}, mem=build_mem())
        # Restore: the same identity guard runs on the restore targets.
        snapshot = capture_world(cpus=self.dual_world())
        loaded = WorldSnapshot.from_bytes(snapshot.encode())
        fresh = self.fresh_cpus()
        mem = build_mem()
        fresh['cpu0'].mem = build_mem()  # not the mem= restore target
        fresh['cpu1'].mem = mem
        with self.assertRaisesRegex(SnapshotError, 'shares one M2 memory'):
            restore_world(loaded, cpus=fresh, mem=mem)
        fresh['cpu0'].mem = mem
        restore_world(loaded, cpus=fresh, mem=mem)  # identical reference passes

    def test_mid_world_round_trip_with_m4(self):
        """M1+M3+M4+M7 together (design §5.4 item ④ preview at field level):
        full CPUs over an all-families M4 hardware + the loop ledger round
        trip and re-capture byte-identically in one snapshot."""
        families = M4ProjectionFamilies('test_round_trip_every_family_field_exact_and_byte_identical')
        hardware = families.poke(families.build_world())
        cpus = self.dual_world()
        ledger = build_ledger()
        snapshot = capture_world(cpus=cpus, mem=build_mem(), hardware=hardware,
                                 ledger=ledger, context={'scenario': 'm1_mid_world'})
        self.assertEqual(snapshot.captured_modules, ['M1', 'M2', 'M3', 'M4', 'M7'])
        loaded = WorldSnapshot.from_bytes(snapshot.encode())
        fresh_hardware = BR28Model(**M4ProjectionFamilies.M4_SWITCHES)
        fresh_cpus = self.fresh_cpus()
        fresh_mem = SimpleNamespace(ram={}, unknown_origins={}, icfg_spills={})
        fresh_ledger = SimpleNamespace(adapters=Counter(), rom_steps=Counter(),
                                       event_counts=Counter(), milestones=[], trace=[],
                                       completed=0, stop=None)
        restore_world(loaded, cpus=fresh_cpus, mem=fresh_mem,
                      hardware=fresh_hardware, ledger=fresh_ledger,
                      expected_context={'scenario': 'm1_mid_world'})
        for key, original in cpus.items():
            self.assert_cpu_equal(fresh_cpus[key], original, key)
        families.assert_m4_equal(fresh_hardware, hardware)
        for field in ws.M3_TIMER_FIELDS:
            assert_equal(getattr(fresh_hardware.timer, field),
                         getattr(hardware.timer, field), f'M3.timer.{field}')
        for name in ws.M3_STATE:
            assert_equal(getattr(fresh_hardware, name), getattr(hardware, name), f'M3.{name}')
        for name in ws.M7_STATE:
            assert_equal(getattr(fresh_ledger, name), getattr(ledger, name), f'M7.{name}')
        # Byte identity over the whole combined world (same capture context).
        self.assertEqual(capture_world(cpus=fresh_cpus, mem=fresh_mem,
                                       hardware=fresh_hardware, ledger=fresh_ledger,
                                       context={'scenario': 'm1_mid_world'}).encode(),
                         snapshot.encode())


class M4ProjectionFamilies(unittest.TestCase):
    """All ~30 M4 projection slots (28 stateful classes + the two stateless
    idle fixtures): full-family round trip, one-shot event-bit semantics,
    carry-state (CRC/ICU) resumption and the AZ counterexamples."""

    M4_SWITCHES = dict(
        watchdog_projection=True, watchdog_progress=(1, 4),
        efuse_read_projection=True, efuse_words=(1, 2, 3, 4),
        debug_execution_projection=True, initial_debug_controls=(0, 0),
        mpu_configuration_projection=True, initial_mpu_write_gate=1,
        icu_initialization_projection=True, icu_command_steps=64,
        icu_region_projection=True,
        dcu_cmo_projection=True, l1p_idle_projection=True, icu_idle_projection=True,
        sfc_read_bus=True, initial_sfc_control=0x100001,
        secondary_sfc_read_bus=True, sfc_clock_gate_projection=True,
        hardware_crc_projection=True,
        gpcnt_cleanup_projection=True, gpcnt_con_readback=True,
        p11_access_probe=True, p11_status=0, p11_pmu_ready_projection=True,
        reset_history_projection=True, p11_lpwr_response_projection=True,
        p11_window_projection=True,
        lp_timer_init_projection=True, lrct_trim_done_projection=True,
        lrct_trim_result_value=38400, lp_timer_con_readback_projection=True,
        mem_pwr_con_projection=True, p33_window_projection=True,
        p33_port_sel_projection=True,
        p33_awkup_projection=True,
        p33_awkup_first_read_words={P33AwkupRegisters.P_IE: 0x10},
        p3_ls_projection=True,
        fd31_adc_con_projection=True, fd31_adc_con_readback=True,
        fd31_adc_pnd_completion=True, fd31_adc_res_named_values={0x10002: 400},
        p33_canary_projection=True,
        p33_canary_first_read_words={P33CanaryWatchRegisters.VLVD_CON: 0x180},
        jl_uart_init_write_records=True, jl_uart_con1_readback=True,
        jl_omap_write_records=True, jl_imap_write_records=True,
        jl_wl_aud_write_records=True, jl_audio_dac_init_write_records=True,
        jl_audio_adc_reset_write_records=True,
    )

    def build_world(self):
        return BR28Model(**self.M4_SWITCHES)

    def poke(self, hardware):
        """Stop-point-shaped state on every family, including the one-shot
        event bits and the carry state the design calls out."""
        hw = hardware
        # Model-stub family.
        hw.debug.registers[0xffe200] = 0xe0000007
        hw.debug.checks = 42
        hw.debug.violation = {'core': 0, 'pc': '0x123', 'size': 4,
                              'ranges': [['0x0', '0x100']]}
        hw.mpu.wren = 1
        hw.mpu.registers[0xff0300] = 0x60607
        hw.mpu.key_writes = 2
        # Bit-record family (SFC read bus x2, clock gate).
        hw.sfc.con, hw.sfc.baud, hw.sfc.writes = 0xf00001, 3, 4
        hw.sfc.window_control.apply('or', 2)
        hw.sfc.window_control_operations.append(('or', 2))
        hw.sfc.window_limit, hw.sfc.window_limit_writes = 0x7ffffff, 1
        hw.sfc.window_base, hw.sfc.window_base_writes = 0x061d7fe0, 1
        hw.sfc_secondary.con, hw.sfc_secondary.baud = 0xf00001, 1
        hw.sfc_secondary.writes = 2
        hw.sfc_clock.bits.apply('or', 9)
        hw.sfc_clock.operations.append(('or', 9))
        hw.sfc_clock.operations.append(('and', 0xfffff7ff))
        hw.sfc_clock.select, hw.sfc_clock.select_writes = 3, 1
        hw.sfc_clock.divider, hw.sfc_clock.divider_writes = 4, 1
        hw.sfc_clock.reads = 5
        # Carry-state family: CRC accumulator mid-transaction, ICU countdown.
        hw.crc_engine.crc = 0x1234
        hw.crc_engine.seed_writes = [0, 0x1234]
        hw.crc_engine.feeds = 3
        hw.crc_engine.result_reads = [0x1234, 0x8ac8]
        hw.icu.control = {ICU_BASES[0]: 1, ICU_BASES[1]: None}
        hw.icu.pending = {ICU_BASES[1]: 5}          # in-flight command countdown
        hw.icu.commands = [{'address': hex(ICU_BASES[1] + 16), 'value': '0xca000010'}]
        hw.icu.completed, hw.icu.busy_reads = 1, 2
        hw.icu.region[ICU_BASES[0]][0x14] = 0x6000000   # nested int-keyed map
        hw.icu.region[ICU_BASES[0]][0x18] = 0x7ffffff
        hw.icu.region_writes[ICU_BASES[0]][0x14] = 1
        hw.dcu.write(0xff05c8, 0x19f000)
        hw.dcu.write(0xff05c8, 0x19f020)
        # Stateless idle fixtures: nothing to poke, slots must round-trip on/off.
        hw.gpcnt.rmw('and', 0xfffffffe)
        hw.gpcnt.pending = False
        hw.gpcnt.con_reads = 2
        # One-shot family: efuse transaction in progress (page/byte consumed),
        # awkup/canary first-read consumed, lpwr request observed.
        hw.efuse.write(EFUSE_CON0, 0x40)            # page=1, byte=0
        hw.efuse.write(EFUSE_CON1, 0x82)            # active=True
        hw.efuse.read(EFUSE_RDAT)                   # read_done=True, reads=1
        hw.p11.phase, hw.p11.sequences, hw.p11.polls = 2, 1, 3
        hw.p11_lp.request()
        hw.p11_lp.request()
        hw.p33_awkup.read(P33AwkupRegisters.P_IE)   # first-read fixture consumed
        hw.p33_awkup.write(P33AwkupRegisters.FLT0, 0x5c)
        hw.p33_awkup.rmw(P33AwkupRegisters.EN, 'or', 4)
        hw.p33_canary.read(P33CanaryWatchRegisters.VLVD_CON)
        # Write-record family.
        hw.p11_win.release_rmw(0xf2a0c0, 'or', 1)
        address = next(iter(hw.p11_win.stores))
        hw.p11_win.write(address, 0)
        hw.p11_win.write(address, 1)
        for slot in ('lp_timer', 'p33_port_sel', 'p3_ls', 'jl_uart_init_records'):
            getattr(hw, slot).write(next(iter(getattr(hw, slot).stores)), 0x5c)
        hw.p33_window.write_byte(next(iter(hw.p33_window.stores)), 0x5c)
        for slot in ('jl_wl_aud_records', 'jl_audio_dac_init_records',
                     'jl_audio_adc_reset_records'):
            getattr(hw, slot).write(next(iter(getattr(hw, slot).stores)), 1, 0)
        hw.lp_timer.rmw(0xfd3600, 'or', 0x41)
        hw.lp_timer.lrct_con_reads, hw.lp_timer.lrct_num_reads = 2, 1
        hw.lp_timer.con_reads = 1
        hw.mem_pwr_con.rmw('or', 3)
        hw.mem_pwr_con.observe_dead_read()
        for slot in ('jl_wl_aud_records', 'jl_audio_dac_init_records',
                     'jl_audio_adc_reset_records'):
            getattr(hw, slot).reads_after_write = 1
            getattr(hw, slot).synthesized_initial_reads = 2
        hw.jl_uart_init_records.con1_reads = 1
        # jl_omap/jl_imap cores lists ride along in their stores records.
        address = next(iter(hw.jl_omap_records.stores))
        hw.jl_omap_records.write(address, 0x9f, 0)
        address = next(iter(hw.jl_imap_records.stores))
        hw.jl_imap_records.write(address, 0x1, 1)
        # fd31: runtime-mutable named-value fixture is STATE, not static.
        hw.fd31_adc_con.res_named_values[0x10004] = 500
        hw.fd31_adc_con.res_served = {0x10002: 1}
        hw.fd31_adc_con.pnd_forced_reads = 2
        hw.fd31_adc_con.con_reads = 3
        hw.fd31_adc_con.write(0xfd3100, 0x46105d)
        # Reset-history family.
        hw.reset.words[0xf2c0c0] = 0x80
        hw.reset.flag_clear_commands.add(0xf2c0c0)
        hw.reset.clear_commands.append({'address': '0xf2d248', 'value': '0x0'})
        address = next(iter(hw.reset.config_masks))
        hw.reset.configuration[address].apply('and', hw.reset.config_masks[address])
        return hardware

    def captured_m4(self, world):
        return capture_world(hardware=world,
                             context={'scenario': 'm4_all_families'})

    def world_and_snapshot(self):
        original = self.poke(self.build_world())
        return original, self.captured_m4(original)

    def restored_m4(self, snapshot, switches=None):
        hardware = BR28Model(**(switches if switches is not None else self.M4_SWITCHES))
        restore_world(snapshot, hardware=hardware,
                      expected_context={'scenario': 'm4_all_families'})
        return hardware

    def assert_m4_equal(self, actual, expected):
        for slot, cls_name, fields in ws.M4_FAMILIES:
            original, restored = getattr(expected, slot), getattr(actual, slot)
            self.assertIsNotNone(original, slot)  # this world has every switch on
            self.assertIsNotNone(restored, slot)
            for field in fields:
                assert_equal(getattr(restored, field), getattr(original, field),
                             f'M4.{slot}.{field}')
            # Per-object static fixtures/switches survive too (already checked
            # by check_static; the equality pass re-proves it on the payload).

    def test_round_trip_every_family_field_exact_and_byte_identical(self):
        original, snapshot = self.world_and_snapshot()
        self.assertIn('M4', snapshot.captured_modules)
        self.assertEqual(len(snapshot.body['M4']['objects']), len(ws.M4_SLOTS))
        self.assertEqual([slot for slot, record in snapshot.body['M4']['objects'].items()
                          if record is not None], list(ws.M4_SLOTS))
        loaded = WorldSnapshot.from_bytes(snapshot.encode())
        restored = self.restored_m4(loaded)
        self.assert_m4_equal(restored, original)
        # Strong reflexivity: re-capturing the restored world is byte-identical.
        self.assertEqual(self.captured_m4(restored).encode(), snapshot.encode())

    def test_one_shot_event_bits_stay_consumed_after_restore(self):
        original, snapshot = self.world_and_snapshot()
        restored = self.restored_m4(WorldSnapshot.from_bytes(snapshot.encode()))
        # Awkup first-read fixture: consumed before the snapshot; the restored
        # projection must NOT re-serve it (same strict stop, same kind).
        for world in (original, restored):
            with self.assertRaisesRegex(Blocked, 'awkup_repeat_read_not_modeled'):
                world.p33_awkup.read(P33AwkupRegisters.P_IE)
            with self.assertRaises(Blocked):
                world.p33_awkup.read(P33AwkupRegisters.EN)  # never had a fixture
        # Efuse transaction mid-state: active/read_done carried, so a second
        # data read is refused exactly like the original (byte pointer kept).
        for world in (original, restored):
            with self.assertRaisesRegex(Blocked, 'efuse_read_outside_selected_byte'):
                world.efuse.read(EFUSE_RDAT)
            world.efuse.write(EFUSE_CON1, 0)  # deassert completes the byte
            self.assertEqual(world.efuse.byte, 1)
        # lpwr request-armed bit: restored world keeps bit5 cleared on readback.
        self.assertEqual(restored.p11_lp.read(0xf2a400, 0xa7),
                         original.p11_lp.read(0xf2a400, 0xa7))
        self.assertEqual(original.p11_lp.read(0xf2a400, 0xa7), 0x87)

    def test_carry_state_resumes_exactly(self):
        original, snapshot = self.world_and_snapshot()
        restored = self.restored_m4(WorldSnapshot.from_bytes(snapshot.encode()))
        # CRC accumulator: both worlds continue feeding the same bytes.
        for world in (original, restored):
            world.crc_engine.write(CRC_ENGINE + 4, 0x1d0f)  # reseed mid-transaction
        for byte in (0x55, 0xaa, 0x30):
            original.crc_engine.write(CRC_ENGINE, byte)
            restored.crc_engine.write(CRC_ENGINE, byte)
        self.assertEqual(restored.crc_engine.crc, original.crc_engine.crc)
        self.assertNotEqual(restored.crc_engine.crc, 0x1d0f)
        # ICU command countdown: advance decrements both identically.
        for _ in range(3):
            original.icu.advance()
            restored.icu.advance()
        self.assertEqual(restored.icu.pending, original.icu.pending)
        self.assertEqual(restored.icu.pending, {ICU_BASES[1]: 2})

    def test_switch_mismatch_refuses_both_directions(self):
        _, snapshot = self.world_and_snapshot()
        loaded = WorldSnapshot.from_bytes(snapshot.encode())
        without_watchdog = dict(self.M4_SWITCHES)
        del without_watchdog['watchdog_projection']
        with self.assertRaisesRegex(SnapshotError, 'M4.watchdog'):
            self.restored_m4(loaded, without_watchdog)            # snap on, target off
        _, other = self.world_and_snapshot()
        other = WorldSnapshot.from_bytes(other.encode())
        other.body['M4']['objects']['sfc'] = None                 # snap off, target on
        with self.assertRaisesRegex(SnapshotError, 'M4.sfc'):
            self.restored_m4(other)

    def test_missing_family_field_must_refuse_or_turn_red(self):
        """AZ counterexample: dropping a captured field (the ICU countdown)
        must not pass silently — the restore-side manifest gate names the
        class; a restore that skipped one field turns the comparison red."""
        original, snapshot = self.world_and_snapshot()
        loaded = WorldSnapshot.from_bytes(snapshot.encode())
        del loaded.body['M4']['objects']['icu']['fields']['pending']
        reloaded = WorldSnapshot.from_bytes(loaded.encode())  # manifest re-derives
        with self.assertRaisesRegex(SnapshotError, 'IcuInitializationProjection'):
            self.restored_m4(reloaded)
        # Simulated adapter bug: restore everything, then skip applying one
        # family's carry state — the field comparison must be red.
        restored = self.restored_m4(WorldSnapshot.from_bytes(snapshot.encode()))
        restored.crc_engine.crc = None  # as if 'crc' was never restored
        with self.assertRaises(AssertionError) as caught:
            self.assert_m4_equal(restored, original)
        self.assertIn('crc', str(caught.exception))

    def test_static_fixture_mismatch_refuses(self):
        _, snapshot = self.world_and_snapshot()
        loaded = WorldSnapshot.from_bytes(snapshot.encode())
        wrong_progress = dict(self.M4_SWITCHES, watchdog_progress=(2, 5))
        with self.assertRaisesRegex(SnapshotError, 'static configuration mismatch'):
            self.restored_m4(loaded, wrong_progress)

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
