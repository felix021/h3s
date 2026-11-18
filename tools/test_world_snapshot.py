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
import os
import struct
import sys
import tempfile
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
                        ICU_BASES, IRQController, JLAdcStartConfigRegisters,
                        JLSrcSyncConfigRegisters,
                        P2mMailboxAck, P33AwkupRegisters, P33CanaryWatchRegisters,
                        PartialWord)
from br28_unknown import UnknownWord
from br28_model import GpcntConValue, GpioOutValue
from probe_boot import Blocked


def attach_irq1(hardware):
    """Structural poke both worlds share: give CPU1 an IRQ controller without
    the full enable_multicore path (which would add M4-family state)."""
    hardware.irq1 = IRQController(initial_config=[None] * 32, initial_ipmask=None)
    hardware.multicore_enabled = True
    from br28_model import CORE_PMU_CON1, PartialWord as _PW
    hardware.core_power = {address: _PW() for address in CORE_PMU_CON1}


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
                              event_clock_projection=True,
                              cfg523_object_bytes=bytes(range(70)))
    machine.last_core, machine.stalls, machine.launches = 0, 5, 1
    machine.idle_wait_cores = {0}
    machine.deferred_core1_turns, machine.seen_cpu1_control_kicks = 7, 2
    # Route M3FIX: the cfg523 parser-entry install already fired before the
    # stop — the one-shot bit holds the after_step result shape (a restored
    # world must keep the fixture installed, never re-install it).
    machine.cfg523_installed_at = {'pc': '0x60010af', 'object_bytes': 70}
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
    return SimpleNamespace(events=[{'access': 'write', 'address': '0x123000',
                                    'bytes': '00', 'region': 'bootstrap_stack'}],
                           adapters=Counter({'parallel_byte_store_old_registers': 104,
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
                                        software_irq_rti=True, event_clock_projection=True,
                                        cfg523_object_bytes=bytes(range(70))),
                ledger=SimpleNamespace(events=[], adapters=Counter(), rom_steps=Counter(),
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
        # M1 fields compare by the reader-visible getattr(..., None) convention
        # (the restore preserves absence-vs-None; namespace carriers have no
        # class defaults to fall back to).
        for key, original in world['cpus'].items():
            for field in ws.M1_CPU_FIELDS:
                assert_equal(getattr(fresh['cpus'][key], field, None),
                             getattr(original, field, None),
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
            assert_equal(getattr(actual, field, None), getattr(expected, field, None),
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
            assert_equal(getattr(fresh_hardware, name, None), getattr(hardware, name, None),
                         f'M3.{name}')
        for name in ws.M7_STATE:
            assert_equal(getattr(fresh_ledger, name), getattr(ledger, name), f'M7.{name}')
        # Byte identity over the whole combined world (same capture context).
        self.assertEqual(capture_world(cpus=fresh_cpus, mem=fresh_mem,
                                       hardware=fresh_hardware, ledger=fresh_ledger,
                                       context={'scenario': 'm1_mid_world'}).encode(),
                         snapshot.encode())


class M4ProjectionFamilies(unittest.TestCase):
    """All ~34 M4 projection slots (32 stateful classes + the two stateless
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
        # 路 UARTTX(sim-uarttx):TX 启动写记录容器(条件栈全开 flag 集成员;
        # 依赖路 F CON0 投影+路 AA CON1 读回,执行序前置)。
        jl_uart_con_projection=True, jl_uart_tx_start_write_records=True,
        jl_omap_write_records=True, jl_imap_write_records=True,
        jl_wl_aud_write_records=True, jl_audio_dac_init_write_records=True,
        jl_audio_adc_reset_write_records=True,
        # Post-BL families (wiring route sim-snapwire): daa_con1/vl0/cop/dma/
        # adda_analog_family/timer2, all ON in the conditional frontier flag set.
        jl_adda_daa_con1_initial_fixture=0x0, jl_adda_daa_con1_config_readback=True,
        jl_audio_dac_vl0_readback=True,
        jl_audio_dac_cop_writes=True, jl_audio_dac_dma_writes=True,
        jl_adda_analog_family=True, timer2_delay_projection=True,
        # Post-wiring families (route SNAPX/sim-snapx): dac_tm (DT)/adda_ada
        # (ADA)/p11_lpvad (LPV)/adc_dma (JLA), all ON in the current
        # conditional frontier flag set (21.8M era); probe values stay None
        # like the canonical flag set (probe_only switches are off).
        jl_audio_dac_tm_readback=True,
        jl_adda_ada_family=True,
        p11_lpvad_config=True,
        jl_audio_adc_dma_writes=True,
        # Post-SNAPX families (route ASS + B2G): the AM four-word ADC-start
        # read-back container and the P2M mailbox request-response projection,
        # both ON in the current conditional frontier flag set.
        jl_adc_start_config=True,
        p2m_mailbox_ack=True,
        # Post-B2G families (route JLEQ): the GPCNT config/completion counters
        # and the JL_EQ init two-word write-only record, all ON in the current
        # conditional frontier flag set (park frontier).
        gpcnt_con_config=True,
        gpcnt_con_completion=True,
        jl_eq_init_writes=True,
        # Post-JLEQ family (route D2ANC): the JL_ANC 44-word container, ON in
        # the current conditional frontier flag set (zero prefix touches).
        jl_anc_writes=True,
        jl_clock_con3_readback=True,
        jl_pled_con3_readback=True,
        jl_pled_con0_initial_fixture=0x00000000,
        # Route P2MCLK: the P11_SYSTEM P2M_CLK_CON0 single-word config-RMW
        # container, ON in the current conditional frontier flag set.
        p2m_clk_con0_config_rmw=True,
        # Route BATCHB (scout2 batch B): the JL_IOMC map-table seven-word
        # named-fixture container, ON in the current conditional frontier
        # flag set (prefix zero-touch — all three batch-B trigger faces are
        # outside the LED->S19 immediate path).
        jl_iomc_map_fixture=0x00000000,
        # Route BATCHA: the JL_PLED config-word family container (13 words),
        # ON in the current conditional frontier flag set.
        jl_pled_config_words=True,
        # Route EVIMPL: the jlstream single-slot unknown-word release guard,
        # ON in the current conditional frontier flag set (41.9M-era frontier).
        jlstream_kick_param2_unknown=True,
        # Route DMAIMPL: the DAC DMA HRP/SWP idle-readback guard, ON in the
        # current conditional frontier flag set (its first served load sits at
        # the 41,965,883-era frontier stop).
        jl_audio_dac_dma_idle_readback=True,
        # Route COPEXIMPL: the copex mode-22 exponential approximation gate,
        # ON in the current conditional frontier flag set (its first intercepted
        # copex sits at the 42,037,363-era frontier stop).
        copex_exp22_model=True,
        # Route SRCSYNC: the JL_SRC_SYNC config-transaction container, ON in
        # the current conditional frontier flag set (its first served RMW sits
        # at the 42,059,070 frontier stop).
        jl_src_sync_config_words=True,
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
        # Post-BL families (sim-snapwire): one write+read each, then mid-flight
        # timer2 state (running transaction, fractional remainders, iwkup log).
        hw.jl_adda_daa_con1.write(0xfe0a04, 0xfffffff8, 0)
        hw.jl_adda_daa_con1.read(0xfe0a04)      # reads_after_write += 1
        hw.jl_audio_dac_vl0.write(0xfe051c, 0x0, 0)
        hw.jl_audio_dac_vl0.read(0xfe051c)      # synthesized initial read
        hw.jl_audio_dac_cop.write(0xfe0534, 0x7f, 0)
        hw.jl_audio_dac_dma.write(0xfe0508, 1, 0)
        hw.jl_adda_family.write(0xfe0a08, 0x12)
        hw.jl_adda_family.read(0xfe0a08)
        hw.timer2.write(0xfd0604, 100)          # CNT (fractional phase reset)
        hw.timer2.write(0xfd0608, 199)          # PRD
        hw.timer2.write(0xfd0600, 0xc01)        # start: opens a transaction
        hw.timer2.con_reads = 1
        hw.timer2.write_iwkup(0xffe1b0, 6, 0)   # bounded write-only log
        # Post-wiring families (route SNAPX): DT TM0 half-word setter write +
        # readback (reads_after_write) and an untouched-word synthetic initial
        # read (TM1); ADA nine-word config write + RMW-shaped read; LPVAD
        # single-word bit-set write + readback; JLA ADC DMA write-only record
        # with the per-write core log.
        hw.jl_audio_dac_tm.write(0xfe0524, 0xfda80000)
        self.assertEqual(hw.jl_audio_dac_tm.read(0xfe0524), 0xfda80000)
        hw.jl_audio_dac_tm.read(0xfe0528)       # synthesized initial 0
        hw.jl_adda_ada.write(0xfe0a20, 0xffffffdf)
        hw.jl_adda_ada.read(0xfe0a24)           # synthesized initial 0
        hw.p11_lpvad.write(0xf2b404, 0x10000000)
        self.assertEqual(hw.p11_lpvad.read(0xf2b404), 0x10000000)
        hw.jl_audio_adc_dma.write(0xfe053c, 0x419264, 0)
        hw.jl_audio_adc_dma.write(0xfe0540, 0x100, 0)
        # Route ASS: AM four-word ADC-start synthetic read-back — one written
        # word (read-after-write) and one untouched word (synthetic initial 0).
        hw.jl_adc_start.write(JLAdcStartConfigRegisters.WORDS[0], 0x2, 0)
        self.assertEqual(hw.jl_adc_start.read(JLAdcStartConfigRegisters.WORDS[0]), 0x2)
        hw.jl_adc_start.read(JLAdcStartConfigRegisters.WORDS[1])  # synthetic initial 0
        # Route B2G: P2M mailbox request-response — one completed transaction,
        # one non-0x400 kick (histogram), a clear-slot write and a second
        # in-flight transaction left armed.
        hw.p2m_mailbox.note_message_byte(0xFF)
        hw.p2m_mailbox.kick(P2mMailboxAck.KICK_VALUE)
        hw.p2m_mailbox.pnd_read()
        hw.p2m_mailbox.echo_read()
        hw.p2m_mailbox.clear_ack(P2mMailboxAck.CLR_VALUE)
        hw.p2m_mailbox.kick(0x800)          # non-0x400 kick -> histogram, not armed
        hw.p2m_mailbox.clear_slot(0x5c)
        hw.p2m_mailbox.note_message_byte(0x3c)
        hw.p2m_mailbox.kick(P2mMailboxAck.KICK_VALUE)  # in-flight current
        # Route JLEQ: GPCNT config-word counters mid-measurement (enable bit
        # set, config re-read after the reconfiguration write, completion-bit
        # poll consumed) and the JL_EQ init two-word write-only record with
        # both words written and one refused read attempt.
        hw.gpcnt.rmw('or', 1)
        hw.gpcnt.whole_writes = 1
        hw.gpcnt.config_reads = 2
        hw.gpcnt.completion_reads = 1
        hw.jl_eq_init.write(0xfe1500, 0, 0)
        hw.jl_eq_init.write(0xfe151c, 0x18cb80, 0)
        with self.assertRaises(Blocked):
            hw.jl_eq_init.read(0xfe1500)
        # Route D2ANC: the JL_ANC 44-word container mid-reconfig — CON start
        # word written, one filter word written, one synthesized-initial read
        # consumed by an RMW before its first write.
        hw.jl_anc.write(0xfe0400, 0x102)
        hw.jl_anc.write(0xfe040c, 0x80000000)
        self.assertEqual(hw.jl_anc.read(0xfe0420), 0)
        # Route D2ANC companion: CLK_CON3 written once, one synthesized read.
        hw.jl_clock_con3.write(0xfd0024, 0x20000)
        self.assertEqual(hw.jl_clock_con3.read(0xfd0024), 0x20000)
        # Route FD610C companion: PLED CON3 served one config RMW (synthetic
        # initial 0 read + write-back), then one whole-word store — exercising
        # both the synthesized-initial-read and the read-after-write counters.
        self.assertEqual(hw.jl_pled_con3.rmw('and', 0xffffffdf), 0)
        hw.jl_pled_con3.write(0xfd610c, 0x40)
        self.assertEqual(hw.jl_pled_con3.rmw('or', 0x20), 0x60)
        # Route FD610C-con0: PLED CON0 served one named-initial read (fixture
        # 0, not a firmware write) then one whole-word store — exercising both
        # the synthesized-initial-read and the read-after-write counters.
        self.assertEqual(hw.jl_pled_con0.read(0xfd6100), 0)
        hw.jl_pled_con0.write(0xfd6100, 0x1)
        self.assertEqual(hw.jl_pled_con0.read(0xfd6100), 0x1)
        # Route P2MCLK: P2M_CLK_CON0 served the LED_CLK_SEL(3) pair (first
        # RMW answers the synthetic initial 0, second RMW reads after write) —
        # both stop-point shapes resume from the captured store.
        self.assertEqual(hw.p2m_clk_con0.rmw('and', 0xffffff1f), 0)
        self.assertEqual(hw.p2m_clk_con0.rmw('or', 0x60), 0x60)
        # Route BATCHB: the JL_IOMC map container served one synthesized-
        # initial read (ICH_CON0 fixture 0, the slot-search branch input) and
        # one whole-word write + read-back (the save/restore shape) — both
        # counters and the per-word store are branch-world state.
        self.assertEqual(hw.jl_iomc_map.read(0xfd6024), 0)
        hw.jl_iomc_map.write(0xfd601c, 0x12345678)
        self.assertEqual(hw.jl_iomc_map.read(0xfd601c), 0x12345678)
        # Route BATCHA: the config-word family took the frontier-stop shape —
        # whole-word zero clears (CON1=0 at 0x60195f8 / CON2=0 at 0x60195fa),
        # a post-write readback on CON1, plus a BRI/DUTY write whose word
        # stays read-refused (frontier shape: PRD_DIVL write-only).
        hw.jl_pled_config_words.write(0xfd6104, 0)
        hw.jl_pled_config_words.write(0xfd6108, 0)
        self.assertEqual(hw.jl_pled_config_words.read(0xfd6104), 0)
        hw.jl_pled_config_words.write(0xfd6128, 3)
        # Route EVIMPL: the jlstream release guard took the frontier-stop
        # shape — one observed task install (TCB instance), one authorized
        # unknown-word load (first-load evidence window still open at the
        # stop), one producer candidate executed. All of it is behavior
        # state: a restore without the instance would make the guard refuse
        # (strict stop regression).
        from br28_unknown import UnknownWord as _UnknownWord
        from test_jlstream_kick_param2 import GuardCpu, GuardMem, TCB
        guard = hw.jlstream_kick_param2
        installer = GuardCpu(pc=guard.INSTALL_PC, sp=0x18ca7c, core=0,
                             mem=GuardMem())
        installer.R[0] = guard.TASK_ENTRY
        installer.R[4] = TCB
        guard.note_step(installer)
        loader = GuardCpu(pc=guard.LOAD_PC, sp=0x18ca44, core=0,
                          mem=GuardMem({0x18ca5c: b'\x00\x00\x10\x00',
                                        0x18ca60: b'\x00\x00\x00\x00'}))
        loader.R[10] = 0x18ca5c
        loader.R[0] = 0
        value = guard.authorize(loader, hw, 8)
        self.assertIsInstance(value, _UnknownWord)
        guard.producer_executions['open_kick_api_0x6074ffe'] = 1
        # Route DMAIMPL: the idle-readback guard took the frontier-stop shape —
        # the four initialization writes audited (with provenance), the DAC_CON
        # baseline anchored, and one served HRP load (the 41,965,883 stop was
        # the FIRST guard-site load). All of it is behavior state: a restore
        # without the phase/invalidations/provenance would answer a read the
        # prefix already audited differently (strict stop regression).
        from test_jl_audio_dac_dma_idle import GuardCpu, GuardHardware
        idle = hw.jl_audio_dac_dma_idle
        idle_hw = GuardHardware()
        for pc, address in sorted(idle.EXPECTED_WRITE_PCS.items()):
            idle.current_pc = pc
            idle.note_write(address,
                            {0xfe0508: 1, 0xfe0504: 0x18e180, 0xfe0514: 0, 0xfe0510: 0}[address],
                            0, idle_hw)
        idle.current_pc = 0x6071eb8
        idle.note_write(idle.DAC_CON, 0x110, 0, idle_hw)
        idle.current_pc = 0x606e148
        self.assertEqual(idle.serve(GuardCpu(0x606e148), idle_hw,
                                    idle.READ_SITES[0x606e148]), 0)
        # Route COPEXIMPL: the mode-22 ledger carries state (executions,
        # first/last samples, mode histogram) — poke a stop-point-shaped pair.
        copex = hw.copex_exp22
        copex.executions = 2
        copex.samples = [{'pc': '0x606a900', 'core': 0, 'x_bits': '0xc05d0c55',
                          'result_bits': '0x3d0186e2'},
                         {'pc': '0x606a900', 'core': 0, 'x_bits': '0x0',
                          'result_bits': '0x3f800000'}]
        copex.first = copex.samples[0]
        copex.last = copex.samples[1]
        copex.modes = {22: 2}
        # Route SRCSYNC: the config-transaction container carries state —
        # one written CON1 (read-after-write), one untouched CON0 (synthetic
        # initial 0), COEF zero-value stores.
        hw.jl_src_sync.write(JLSrcSyncConfigRegisters.WORDS[1], 0x400000, 0)
        self.assertEqual(hw.jl_src_sync.read(JLSrcSyncConfigRegisters.WORDS[1]), 0x400000)
        hw.jl_src_sync.read(JLSrcSyncConfigRegisters.WORDS[0])  # synthetic initial 0
        hw.jl_src_sync.write(JLSrcSyncConfigRegisters.WORDS[2], 0, 0)
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

    def test_snapx_probe_fixture_rides_the_static_guard(self):
        """Route SNAPX: the DT/ADA probe_only initial-word substitutions are
        per-object constructor fixtures — a restore target built with a
        different probe value must refuse (naming the fixture), never silently
        re-substitute the initial word."""
        probed = dict(self.M4_SWITCHES, jl_audio_dac_tm_probe_value=0xffffffff,
                      jl_adda_ada_probe_value=0xfffffff8)
        original = self.poke(BR28Model(**probed))
        snapshot = WorldSnapshot.from_bytes(self.captured_m4(original).encode())
        with self.assertRaisesRegex(SnapshotError, 'tm0_probe_initial'):
            self.restored_m4(snapshot)                      # target: probes off
        with self.assertRaisesRegex(SnapshotError, 'ada_probe_initial'):
            self.restored_m4(snapshot, dict(probed, jl_adda_ada_probe_value=None))
        # Same probes on both sides: round-trips and the probe value survives.
        restored = self.restored_m4(snapshot, probed)
        self.assertEqual(restored.jl_audio_dac_tm._initial_word(0xfe0524), 0xffffffff)
        self.assertEqual(restored.jl_adda_ada._initial_word(0xfe0a20), 0xfffffff8)

    def test_snapx_containers_resume_readback_and_write_only_semantics(self):
        """Route SNAPX behavioral pin: after restore the DT/ADA/LPVAD readback
        containers answer from the CAPTURED stores (last written value, and
        the untouched word keeps answering the synthetic initial 0), while the
        JLA ADC DMA record stays write-only (read refuses on both worlds)."""
        original, snapshot = self.world_and_snapshot()
        restored = self.restored_m4(WorldSnapshot.from_bytes(snapshot.encode()))
        for world in (original, restored):
            self.assertEqual(world.jl_audio_dac_tm.read(0xfe0524), 0xfda80000)
            self.assertEqual(world.jl_audio_dac_tm.read(0xfe0528), 0)
            self.assertEqual(world.jl_adda_ada.read(0xfe0a20), 0xffffffdf)
            self.assertEqual(world.jl_adda_ada.read(0xfe0a24), 0)
            self.assertEqual(world.p11_lpvad.read(0xf2b404), 0x10000000)
            with self.assertRaisesRegex(Blocked, 'jl_audio_adc_dma_read_not_modeled'):
                world.jl_audio_adc_dma.read(0xfe053c)
        # The counters (reads_after_write / synthesized_initial_reads) resumed
        # exactly, so the post-restore reads keep counting from the captured
        # values (poke did one written-read; the loop above added one more).
        self.assertEqual(restored.jl_audio_dac_tm.reads_after_write,
                         original.jl_audio_dac_tm.reads_after_write)
        self.assertEqual(original.jl_audio_dac_tm.reads_after_write, 2)
        self.assertEqual(restored.jl_audio_dac_tm.synthesized_initial_reads, 2)

    def test_ass_and_b2g_containers_resume_exact_semantics(self):
        """Route ASS + B2G behavioral pin: the AM ADC-start read-back answers
        from the captured stores (written word = last value, untouched word =
        synthetic initial 0); the P2M mailbox keeps its one-shot armed bits,
        transaction log and in-flight current record across the restore, so a
        resumed world continues the exact same request-response episode."""
        original, snapshot = self.world_and_snapshot()
        restored = self.restored_m4(WorldSnapshot.from_bytes(snapshot.encode()))
        for world in (original, restored):
            self.assertEqual(world.jl_adc_start.read(JLAdcStartConfigRegisters.WORDS[0]), 0x2)
            self.assertEqual(world.jl_adc_start.read(JLAdcStartConfigRegisters.WORDS[1]), 0)
            # P2M: pnd still armed from the in-flight kick; completing it appends
            # a second transaction that matches the first (same message byte).
            self.assertTrue(world.p2m_mailbox.pnd_armed)
            self.assertEqual(world.p2m_mailbox.pnd_read(), P2mMailboxAck.ACK_BIT)
            self.assertEqual(world.p2m_mailbox.echo_read(), 0x3c)
            world.p2m_mailbox.clear_ack(P2mMailboxAck.CLR_VALUE)
        # One completed transaction before the snapshot, one after: both worlds
        # end with two identical transactions and the same counters.
        self.assertEqual(len(restored.p2m_mailbox.transactions), 2)
        self.assertEqual(restored.p2m_mailbox.transactions,
                         original.p2m_mailbox.transactions)
        self.assertEqual(restored.p2m_mailbox.kicks, original.p2m_mailbox.kicks)
        self.assertEqual(restored.p2m_mailbox.pnd_reads, 2)
        self.assertEqual(restored.p2m_mailbox.other_kick_values,
                         {'0x800': 1})
        # The AM container counters resumed exactly, too.
        self.assertEqual(restored.jl_adc_start.reads_after_write,
                         original.jl_adc_start.reads_after_write)
        self.assertEqual(restored.jl_adc_start.synthesized_initial_reads,
                         original.jl_adc_start.synthesized_initial_reads)

    def test_fd610c_pled_con3_container_resumes_exact_semantics(self):
        """Route FD610C behavioral pin: after restore the PLED CON3 container
        keeps answering config RMWs from the CAPTURED store (last written value
        still feeds the write-back), and the synthesized-initial-read /
        read-after-write counters resume exactly."""
        original, snapshot = self.world_and_snapshot()
        restored = self.restored_m4(WorldSnapshot.from_bytes(snapshot.encode()))
        for world in (original, restored):
            self.assertEqual(world.jl_pled_con3.rmw('or', 0x80), 0xe0)
        self.assertEqual(restored.jl_pled_con3.reads_after_write,
                         original.jl_pled_con3.reads_after_write)
        self.assertEqual(restored.jl_pled_con3.reads_after_write, 2)
        self.assertEqual(restored.jl_pled_con3.synthesized_initial_reads, 1)

    def test_fd610c_pled_con0_container_resumes_exact_semantics(self):
        """Route FD610C-con0 behavioral pin: after restore the PLED CON0
        container keeps the named initial fixture (static, must match) and
        resumes read-after-write from the CAPTURED store; the counters resume
        exactly. A different initial fixture on the restore target is refused."""
        original, snapshot = self.world_and_snapshot()
        restored = self.restored_m4(WorldSnapshot.from_bytes(snapshot.encode()))
        for world in (original, restored):
            self.assertEqual(world.jl_pled_con0.read(0xfd6100), 0x1)
        self.assertEqual(restored.jl_pled_con0.reads_after_write,
                         original.jl_pled_con0.reads_after_write)
        self.assertEqual(restored.jl_pled_con0.reads_after_write, 2)
        self.assertEqual(restored.jl_pled_con0.synthesized_initial_reads, 1)
        # 不同初态 fixture 必须被静态守卫拒绝(不同初态=不同分支世界)。
        with self.assertRaises(SnapshotError):
            self.restored_m4(WorldSnapshot.from_bytes(snapshot.encode()),
                             switches={**self.M4_SWITCHES,
                                       'jl_pled_con0_initial_fixture': 0x00000001})

    def test_p2mclk_p2m_clk_con0_container_resumes_exact_semantics(self):
        """Route P2MCLK behavioral pin: after restore the P2M_CLK_CON0
        container keeps answering config RMWs from the CAPTURED store (the
        post-LED_CLK_SEL word 0x60 still feeds later write-backs), the
        counters resume exactly, and a restore target built with the switch
        off is refused (slot mismatch)."""
        original, snapshot = self.world_and_snapshot()
        restored = self.restored_m4(WorldSnapshot.from_bytes(snapshot.encode()))
        for world in (original, restored):
            # 后续写回从捕获存储续算:0x60|0x80=0xe0,再清 bits5-8→0
            # (0xFFFFFF1F 恰清时钟选择位段,保留位钉子)。
            self.assertEqual(world.p2m_clk_con0.rmw('or', 0x80), 0xe0)
            self.assertEqual(world.p2m_clk_con0.rmw('and', 0xffffff1f), 0x0)
        self.assertEqual(restored.p2m_clk_con0.reads_after_write,
                         original.p2m_clk_con0.reads_after_write)
        self.assertEqual(restored.p2m_clk_con0.reads_after_write, 3)
        self.assertEqual(restored.p2m_clk_con0.synthesized_initial_reads, 1)
        # 开关关的恢复目标必须被拒绝(快照有该槽而目标没有)。
        with self.assertRaises(SnapshotError):
            self.restored_m4(WorldSnapshot.from_bytes(snapshot.encode()),
                             switches={**self.M4_SWITCHES,
                                       'p2m_clk_con0_config_rmw': False})

    def test_batchb_jl_iomc_map_container_resumes_exact_semantics(self):
        """Route BATCHB behavioral pin: after restore the JL_IOMC map
        container keeps the named initial fixture (static, must match),
        resumes read-after-write from the CAPTURED store and keeps answering
        first-read words with the fixture (the OCH/ICH branch inputs); a
        restore target with a different fixture is refused (a different map
        table feeds the comparison branch and the nibble->JL_IMAP propagation
        — a different branch world), and one built with the switch off is
        refused (slot mismatch)."""
        original, snapshot = self.world_and_snapshot()
        restored = self.restored_m4(WorldSnapshot.from_bytes(snapshot.encode()))
        for world in (original, restored):
            # 已消费字从捕获存储续读(save/restore 写回值);未写字仍答 fixture
            # (槽搜索分支输入),且消费行为在恢复后继续记账。
            self.assertEqual(world.jl_iomc_map.read(0xfd601c), 0x12345678)
            self.assertEqual(world.jl_iomc_map.read(0xfd6018), 0)
            self.assertEqual(world.jl_iomc_map.read(0xfd6030), 0)
        self.assertEqual(restored.jl_iomc_map.reads_after_write,
                         original.jl_iomc_map.reads_after_write)
        self.assertEqual(restored.jl_iomc_map.reads_after_write, 2)
        self.assertEqual(restored.jl_iomc_map.synthesized_initial_reads,
                         original.jl_iomc_map.synthesized_initial_reads)
        # 不同 fixture 值=不同分支世界,静态守卫拒绝(CON0 先例同款)。
        with self.assertRaises(SnapshotError):
            self.restored_m4(WorldSnapshot.from_bytes(snapshot.encode()),
                             switches={**self.M4_SWITCHES,
                                       'jl_iomc_map_fixture': 0x11111111})
        # 开关关的恢复目标必须被拒绝(快照有该槽而目标没有)。
        with self.assertRaises(SnapshotError):
            self.restored_m4(WorldSnapshot.from_bytes(snapshot.encode()),
                             switches={**self.M4_SWITCHES,
                                       'jl_iomc_map_fixture': None})

    def test_p2mclk_snapshot_between_the_two_rmw_legs_resumes(self):
        """Route P2MCLK mid-helper pin (codex): capture BETWEEN the two
        LED_CLK_SEL RMW legs — after the 0x6019508 clear-bits5-7 leg, before
        the 0x601950c |=0x60 leg. The restore must answer the second leg from
        the captured store (0 -> 0x60) with counters resuming exactly; the two
        RMW legs are two separate transactions, not one atomic helper."""
        world = self.build_world()
        self.assertEqual(world.p2m_clk_con0.rmw('and', 0xffffff1f), 0)
        snapshot = self.captured_m4(world)
        twin = self.build_world()
        self.assertEqual(twin.p2m_clk_con0.rmw('and', 0xffffff1f), 0)
        restored = self.restored_m4(WorldSnapshot.from_bytes(snapshot.encode()))
        restored_second = restored.p2m_clk_con0.rmw('or', 0x60)
        twin_second = twin.p2m_clk_con0.rmw('or', 0x60)
        self.assertEqual(restored_second, twin_second)
        self.assertEqual(restored_second, 0x60)
        for field in ('stores', 'word_writes', 'reads_after_write',
                      'synthesized_initial_reads'):
            self.assertEqual(getattr(restored.p2m_clk_con0, field),
                             getattr(twin.p2m_clk_con0, field))
        self.assertEqual(restored.p2m_clk_con0.reads_after_write, 1)
        self.assertEqual(restored.p2m_clk_con0.synthesized_initial_reads, 1)


class Cfg523InstallBit(unittest.TestCase):
    """Route M3FIX: the cfg523 parser-entry install bit is M5 STATE, not a
    constructor fixture. It flips None -> {'pc','object_bytes'} at runtime
    (after_step, one-shot idempotence gate); the canonical 29,215,313 resume
    round refused while it sat in the static section — a capture-time dict vs
    fresh-build None mismatch. As state it must round-trip exactly and apply
    onto a fresh (uninstalled) machine without any static refusal."""

    def test_installed_bit_round_trips_onto_a_fresh_machine(self):
        installed = build_machine()          # cfg523_installed_at already poked
        direct = capture_world(machine=installed, context={'scenario': 'cfg523_bit'})
        loaded = WorldSnapshot.from_bytes(direct.encode())
        fresh = DualCoreMachine(software_irq_entry=True, core_tick_entry=True,
                                core_tick_rti=True, timer0_irq_entry=True,
                                timer0_irq_rti=True, idle_wait_projection=True,
                                software_irq_rti=True, event_clock_projection=True,
                                cfg523_object_bytes=bytes(range(70)))
        restore_world(loaded, machine=fresh,
                      expected_context={'scenario': 'cfg523_bit'})
        self.assertEqual(fresh.cfg523_installed_at,
                         {'pc': '0x60010af', 'object_bytes': 70})
        # Re-capturing the restored machine is byte-identical (check #0 shape).
        again = capture_world(machine=fresh, context={'scenario': 'cfg523_bit'})
        self.assertEqual(again.encode(), direct.encode())

    def test_uninstalled_bit_round_trips_none(self):
        machine = DualCoreMachine(cfg523_object_bytes=None)
        direct = capture_world(machine=machine)
        loaded = WorldSnapshot.from_bytes(direct.encode())
        fresh = DualCoreMachine()
        restore_world(loaded, machine=fresh)
        self.assertIsNone(fresh.cfg523_installed_at)


class ObservationLayerMachine(unittest.TestCase):
    """Route SNAPX: analyze_image_trace.TraceMachine layers two observation
    attributes (the route-O6 signature spool and its pending-writes buffer) on
    top of DualCoreMachine. They record what THIS comparator run executed,
    not simulated world state — capture/restore must SKIP them (a restored
    world starts a fresh spool) while any other unexpected machine attribute
    still refuses (the missing-state guard keeps its teeth)."""

    def _world(self, with_observation=True):
        world = small_world()
        if with_observation:
            world['machine'].trace = object()          # opaque spool stand-in
            world['machine'].pending_writes = [(0x123, 'ab')]
        return world

    def test_observation_attributes_are_skipped_not_restored(self):
        world = self._world()
        snapshot = WorldSnapshot.from_bytes(captured(world).encode())
        target = fresh_world()
        target['machine'].trace = object()
        target['machine'].pending_writes = []
        summary = restore_world(snapshot, **target,
                                expected_context={'scenario': 'small_world_test'})
        self.assertIn('M5', summary.modules)
        # The spool buffer is per-run observation: restore must NOT have
        # overwritten the fresh run's (empty) buffer with the captured one.
        self.assertEqual(target['machine'].pending_writes, [])
        # M5 state itself round-trips as before.
        self.assertEqual(target['machine'].stalls, world['machine'].stalls)
        self.assertEqual(target['machine'].last_core, world['machine'].last_core)
        # A world without the observation attrs restores identically too (the
        # skip is one-directional: absence on the capture side is fine).
        plain = fresh_world()
        restore_world(snapshot, **plain)

    def test_any_other_new_machine_attribute_still_refuses(self):
        world = self._world()
        world['machine'].some_future_state = object()
        with self.assertRaisesRegex(SnapshotError, 'some_future_state'):
            captured(world)


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


# ---------------------------------------------------------------------------
# Wiring-route fast tier (design §3.2 item 2, full shape): a REAL explore
# world from public assets — probe_boot_handoff's typed_memory_replay
# (16,077 model steps) — snapshot at a midpoint budget stop, restore onto a
# freshly constructed world, resume to the endpoint, compare against the full
# direct run. No diff whitelist (§3.1): any differing report key is a failure.
# ---------------------------------------------------------------------------

def _explore_assets_ready():
    from probe_pi32v2 import ROOT
    firmware = ROOT / 'firmware/QCY_H3S_7018F_1T2_release_note_V1.1.8_011.ufw'
    simulator = ROOT / 'tools/reference-cache/pi32v2-isa/tools/llvm/sim.py'
    return firmware.exists() and simulator.exists()


@unittest.skipUnless(_explore_assets_ready(),
                     'official firmware / pinned pi32v2-isa not present in this checkout')
class AdvanceTierExploreWorld(unittest.TestCase):
    MIDPOINT = 8000

    @classmethod
    def setUpClass(cls):
        from probe_pi32v2 import ROOT, upstream
        from probe_boot import application
        from probe_boot_handoff import profile, package_header
        from probe_boot_handoff import SENTINEL_A
        firmware = ROOT / 'firmware/QCY_H3S_7018F_1T2_release_note_V1.1.8_011.ufw'
        cls.module = upstream()
        cls.app = application(firmware)
        cls.handoff = profile(package_header(firmware), SENTINEL_A)  # 16,077-step world
        cls.temp = tempfile.TemporaryDirectory(prefix='h3s-snapwire-')
        cls.addClassCleanup(cls.temp.cleanup)

    def run_world(self, budget, **snapshot):
        from probe_boot import explore
        from probe_boot_handoff import HANDOFF
        config = {key: value for key, value in snapshot.items()
                  if key in ('capture', 'restore', 'expected_context')}
        return explore(self.module, self.app, name='typed_memory_replay',
                       adapt_stack=True, pll=1, r0=HANDOFF,
                       handoff=self.handoff, startup_loops=True, budget=budget,
                       snapshot=config or None)

    def test_midpoint_resume_matches_full_replay_byte_for_byte(self):
        snap = Path(self.temp.name) / 'midpoint.bin'
        full = self.run_world(budget=100_000)
        self.assertEqual(full['stop']['kind'], 'unverified_zero_count_rep')  # real Blocked
        captured = self.run_world(budget=self.MIDPOINT, capture=snap)
        self.assertEqual(captured['stop'], {'kind': 'instruction_budget'})
        self.assertEqual(captured['completed_steps'], self.MIDPOINT)
        # +1: the full replay's final Blocked instruction must be re-attempted
        # (resume budget counts steps to ATTEMPT from the restored state).
        resumed = self.run_world(budget=full['completed_steps'] - self.MIDPOINT + 1,
                                 restore=snap,
                                 expected_context={'scenario': 'typed_memory_replay',
                                                   'completed_steps': self.MIDPOINT})
        self.assertEqual(resumed['stop'], full['stop'])
        self.assertEqual(resumed['completed_steps'], full['completed_steps'])
        comparison = dict(resumed)
        comparison.pop('snapshot_restore')
        self.assertEqual(json.dumps(comparison),
                         json.dumps(full),
                         'resumed report differs from the full replay (no diff whitelist; '
                         'plain dumps keeps dict insertion order visible)')

    def test_zero_step_self_check_reproduces_the_capture_report(self):
        snap = Path(self.temp.name) / 'selfcheck.bin'
        captured = self.run_world(budget=self.MIDPOINT, capture=snap)
        zero = self.run_world(budget=0, restore=snap)
        left, right = dict(captured), dict(zero)
        left.pop('snapshot_capture')
        right.pop('snapshot_restore')
        self.assertEqual(json.dumps(left), json.dumps(right))

    def test_tampered_ram_byte_must_turn_the_report_red(self):
        """AZ counterexample on the real world: one flipped RAM byte inside a
        captured segment survives both gates (lengths unchanged) — the resumed
        report must diverge from the full replay, proving the byte-for-byte
        comparison has discriminative power."""
        snap = Path(self.temp.name) / 'tampered.bin'
        full = self.run_world(budget=100_000)
        self.run_world(budget=self.MIDPOINT, capture=snap)
        loaded = WorldSnapshot.load(snap)
        target = next((index for index, (start, length, _data) in enumerate(loaded.ram.segments)
                       if length >= 16 and 0x120000 <= start < 0x130000), None)
        self.assertIsNotNone(target, 'no RAM window segment to tamper')
        start, length, data = loaded.ram.segments[target]
        data = bytearray(data)
        data[8] ^= 0x01
        loaded.ram.segments[target] = [start, length, bytes(data)]
        loaded.save(snap)  # manifest re-derives; RAM byte counts are unchanged
        resumed = self.run_world(budget=full['completed_steps'] - self.MIDPOINT + 1,
                                 restore=snap)
        comparison = dict(resumed)
        comparison.pop('snapshot_restore')
        self.assertNotEqual(json.dumps(comparison), json.dumps(full))

    def test_capture_context_gate_refuses_a_wrong_endpoint(self):
        snap = Path(self.temp.name) / 'context.bin'
        self.run_world(budget=self.MIDPOINT, capture=snap)
        with self.assertRaisesRegex(SnapshotError, 'capture context mismatch'):
            self.run_world(budget=10, restore=snap,
                           expected_context={'scenario': 'typed_memory_replay',
                                             'completed_steps': self.MIDPOINT + 1})


# ---------------------------------------------------------------------------
# Route CKPT (frontier-iteration resume loop): cross-switch restore rules,
# the dirty-stop refusal, loop-attempt reporting and the fingerprint ack.
# ---------------------------------------------------------------------------

class CrossSwitchResume(unittest.TestCase):
    """resume_ack 两条兼容规则(同一 M4 全开世界的 OFF 变体做捕获源):
    enable_slots(None/缺键→on)与 refixture_slots(virgin 容器换 fixture)。
    红线:on→off、非 virgin 换 fixture、未承认的槽位差、ack 形状错全部拒。"""

    OFF_SWITCHES = {key: value for key, value in M4ProjectionFamilies.M4_SWITCHES.items()
                    if key != 'jl_pled_con0_initial_fixture'}

    def captured_off(self):
        return capture_world(hardware=BR28Model(**self.OFF_SWITCHES),
                             context={'scenario': 'm4_all_families'})

    def test_none_to_on_refuses_without_ack(self):
        loaded = WorldSnapshot.from_bytes(self.captured_off().encode())
        target = BR28Model(**M4ProjectionFamilies.M4_SWITCHES)
        with self.assertRaisesRegex(SnapshotError, 'projection off but the restore target'):
            restore_world(loaded, hardware=target,
                          expected_context={'scenario': 'm4_all_families'})

    def test_enable_slot_ack_keeps_container_virgin_and_restores_the_rest(self):
        # 在 OFF 世界里戳一个真实停点形状的状态(CON3 已消费)——enable 规则
        # 不得碰其它槽位的状态恢复。
        original = BR28Model(**self.OFF_SWITCHES)
        original.jl_pled_con3.rmw('and', 0xffffffdf)
        original.jl_pled_con3.write(0xfd610c, 0x40)
        snapshot = capture_world(hardware=original, context={'scenario': 'm4_all_families'})
        loaded = WorldSnapshot.from_bytes(snapshot.encode())
        target = BR28Model(**M4ProjectionFamilies.M4_SWITCHES)
        result = restore_world(loaded, hardware=target,
                               expected_context={'scenario': 'm4_all_families'},
                               resume_ack={'enable_slots': ['jl_pled_con0'],
                                           'reason': 'CON0 first access is the old stop'})
        # 恢复摘要如实记录启用面;新容器保持初态(=从 0 跑到该边界的状态)。
        self.assertEqual(result.resume_ack, {'enabled_slots': ['jl_pled_con0']})
        self.assertEqual(target.jl_pled_con0.stores, {0xfd6100: {'values': [], 'writes': 0}})
        self.assertEqual(target.jl_pled_con0.initial_word, 0)
        self.assertEqual(target.jl_pled_con0.word_writes, 0)
        # 其它槽位照常恢复(CON3 的消费状态来自 OFF 快照)。
        assert_equal(target.jl_pled_con3.stores, original.jl_pled_con3.stores, 'con3.stores')
        self.assertEqual(target.jl_pled_con3.word_writes, original.jl_pled_con3.word_writes)

    def test_missing_slot_key_is_not_none_and_needs_the_same_ack(self):
        """跨代码形状(家族在捕获后才登记):body 缺键 ≠ 槽位 None——未承认时
        拒;承认后与 None→on 同规则(新容器保持初态)。目标世界用 OFF 开关集
        (con0 两边都关,隔离出 con3 的缺键差)。"""
        snapshot = self.captured_off()
        del snapshot.body['M4']['objects']['jl_pled_con3']      # 模拟旧代码无该家族
        snapshot.manifest = ws.derive_manifest(snapshot.body)   # manifest 与 body 一致
        data = snapshot.encode()
        loaded = WorldSnapshot.from_bytes(data)
        with self.assertRaisesRegex(SnapshotError, 'missing the projection slot'):
            restore_world(loaded, hardware=BR28Model(**self.OFF_SWITCHES),
                          expected_context={'scenario': 'm4_all_families'})
        target = BR28Model(**self.OFF_SWITCHES)
        result = restore_world(loaded, hardware=target,
                               expected_context={'scenario': 'm4_all_families'},
                               resume_ack={'enable_slots': ['jl_pled_con3'],
                                           'reason': 'family registered after capture'})
        self.assertEqual(result.resume_ack, {'enabled_slots': ['jl_pled_con3']})
        self.assertEqual(target.jl_pled_con3.word_writes, 0)    # 新容器初态

    def test_refixture_virgin_allows_static_delta_consumed_refuses(self):
        # virgin ON 快照(fixture 0,CON0 零触达)→ 目标 fixture 1。
        virgin = BR28Model(**M4ProjectionFamilies.M4_SWITCHES)
        snapshot = capture_world(hardware=virgin, context={'scenario': 'm4_all_families'})
        loaded = WorldSnapshot.from_bytes(snapshot.encode())
        switches = dict(M4ProjectionFamilies.M4_SWITCHES,
                        jl_pled_con0_initial_fixture=0x00000001)
        with self.assertRaisesRegex(SnapshotError, 'static configuration mismatch'):
            restore_world(loaded, hardware=BR28Model(**switches),
                          expected_context={'scenario': 'm4_all_families'})
        target = BR28Model(**switches)
        result = restore_world(loaded, hardware=target,
                               expected_context={'scenario': 'm4_all_families'},
                               resume_ack={'refixture_slots': ['jl_pled_con0'],
                                           'reason': 'sensitivity arm, virgin container'})
        # 摘要记录实际放行的静态差(审计面:操作者能看到 waived 了什么)。
        self.assertEqual(result.resume_ack,
                         {'refixtured_slots': ['jl_pled_con0'],
                          'refixtured_static_waived': {'jl_pled_con0': {
                              'initial_word': {'snapshot': 0, 'target': 1}}}})
        self.assertEqual(target.jl_pled_con0.initial_word, 1)
        # 非 virgin(读过一次 fixture)→ 拒:已消费容器是分支世界状态。
        consumed = BR28Model(**M4ProjectionFamilies.M4_SWITCHES)
        consumed.jl_pled_con0.read(0xfd6100)      # synthesized_initial_reads += 1
        snapshot = capture_world(hardware=consumed, context={'scenario': 'm4_all_families'})
        loaded = WorldSnapshot.from_bytes(snapshot.encode())
        with self.assertRaisesRegex(SnapshotError, 'not the virgin initial state'):
            restore_world(loaded, hardware=BR28Model(**switches),
                          expected_context={'scenario': 'm4_all_families'},
                          resume_ack={'refixture_slots': ['jl_pled_con0'],
                                      'reason': 'consumed container must refuse'})

    def test_ack_shape_validation(self):
        loaded = WorldSnapshot.from_bytes(self.captured_off().encode())
        target = BR28Model(**M4ProjectionFamilies.M4_SWITCHES)
        context = {'scenario': 'm4_all_families'}
        for bad in ({'enable_slots': ['no_such_slot'], 'reason': 'x'},
                    {'enable_slots': ['jl_pled_con0']},                # 缺 reason
                    {'enable_slots': ['jl_pled_con0'],
                     'refixture_slots': ['jl_pled_con0'], 'reason': 'x'},
                    {'enable_slots': 'jl_pled_con0', 'reason': 'x'},   # 非列表
                    {'unknown_key': 1, 'reason': 'x'}):
            with self.assertRaises(SnapshotError):
                restore_world(loaded, hardware=BR28Model(**M4ProjectionFamilies.M4_SWITCHES),
                              expected_context=context, resume_ack=bad)
        # on→off 永远拒,即使 ack 点名(快照 ON、目标 OFF=真实分支世界差)。
        snapshot = capture_world(hardware=BR28Model(**M4ProjectionFamilies.M4_SWITCHES),
                                 context={'scenario': 'm4_all_families'})
        loaded_on = WorldSnapshot.from_bytes(snapshot.encode())
        with self.assertRaisesRegex(SnapshotError, 'restore target has the projection off'):
            restore_world(loaded_on, hardware=BR28Model(**self.OFF_SWITCHES),
                          expected_context=context,
                          resume_ack={'enable_slots': ['jl_pled_con0'], 'reason': 'x'})


class FingerprintAck(unittest.TestCase):
    """跨代码续跑的指纹承认:ack 必须引用快照自记指纹(证明看过来源),
    结构闸(manifest)与恢复侧守卫不因 ack 放松。"""

    def test_ack_must_quote_the_snapshot_provenance(self):
        world = BR28Model(**M4ProjectionFamilies.M4_SWITCHES)
        snapshot = capture_world(hardware=world, context={'scenario': 'm4'})
        data = snapshot.encode()
        recorded = snapshot.header['code_fingerprint']
        wrong_current = 'f' * 64
        # 无 ack:维持既有拒绝语义(换一个「当前指纹」模拟代码态漂移)。
        with self.assertRaisesRegex(SnapshotError, 'code fingerprint mismatch'):
            WorldSnapshot.from_bytes(data, expected_fingerprint=wrong_current)
        # 引用错值/缺 reason:拒。
        with self.assertRaisesRegex(SnapshotError, 'code fingerprint mismatch'):
            WorldSnapshot.from_bytes(data, expected_fingerprint=wrong_current,
                                     fingerprint_ack={'prior_fingerprint': wrong_current,
                                                      'reason': 'quoted the wrong value'})
        with self.assertRaisesRegex(SnapshotError, 'code fingerprint mismatch'):
            WorldSnapshot.from_bytes(data, expected_fingerprint=wrong_current,
                                     fingerprint_ack={'prior_fingerprint': recorded})
        # 正确引用:过闸,ack 记录 was/now。
        loaded = WorldSnapshot.from_bytes(
            data, expected_fingerprint=wrong_current,
            fingerprint_ack={'prior_fingerprint': recorded,
                             'reason': 'delta only adds an opt-in switch'})
        self.assertEqual(loaded.fingerprint_acknowledged,
                         {'was': recorded, 'now': wrong_current,
                          'reason': 'delta only adds an opt-in switch'})
        # manifest 闸与 ack 无关(encode 会按 body 重导 manifest,篡改 body 后
        # 编码仍是自洽文件——manifest 独立拒载语义由 DriftGates 钉死,此处
        # 只钉「ack 不触碰结构闸」:acknowledged 加载后 manifest 校验照常跑)。


class DirtyStopLegacyResume(unittest.TestCase):
    """脏停点(step-Blocked 自然停点)快照的 budget>0 恢复保持既有语义照跑
    (parity 验证轮 --resume-from 的契约:首次重试即重现同一停点;世界停在
    迭代中途——select 已消耗/trace 半行已追加/check_fetch 已计数,重试会
    重复这些簿记,所以逐字节等价的臂位只能用干净 budget 边界快照,那道
    契约由 frontier_iteration 的 runner 侧守卫钉死)。本类钉两件事:
    attempts 计数口径,与脏恢复的 legacy 行为不因本路改动而变。"""

    @classmethod
    def setUpClass(cls):
        from probe_pi32v2 import upstream
        from probe_boot import application
        from probe_boot_handoff import profile, package_header, SENTINEL_A
        from probe_pi32v2 import ROOT
        firmware = ROOT / 'firmware/QCY_H3S_7018F_1T2_release_note_V1.1.8_011.ufw'
        cls.module = upstream()
        cls.app = application(firmware)
        cls.handoff = profile(package_header(firmware), SENTINEL_A)
        cls.temp = tempfile.TemporaryDirectory(prefix='h3s-ckpt-dirty-')
        cls.addClassCleanup(cls.temp.cleanup)

    def run_world(self, budget, **snapshot):
        from probe_boot import explore
        from probe_boot_handoff import HANDOFF
        config = {key: value for key, value in snapshot.items()
                  if key in ('capture', 'restore', 'expected_context', 'resume_ack')}
        return explore(self.module, self.app, name='typed_memory_replay',
                       adapt_stack=True, pll=1, r0=HANDOFF,
                       handoff=self.handoff, startup_loops=True, budget=budget,
                       snapshot=config or None)

    def test_dirty_capture_reports_attempts_and_legacy_resume_reblocks(self):
        snap = Path(self.temp.name) / 'dirty.bin'
        # 自然停点捕获(真实 Blocked:未验证零计数 rep)=脏停点快照。
        captured = self.run_world(budget=100_000, capture=snap)
        self.assertNotEqual(captured['stop']['kind'], 'instruction_budget')
        self.assertIn('loop_attempts', captured['snapshot_capture'])
        attempts = captured['snapshot_capture']['loop_attempts']
        self.assertLess(attempts, 100_000)
        self.assertGreaterEqual(attempts, captured['completed_steps'])
        # budget=0 自反检查(报告重现捕获轮)。
        zero = self.run_world(budget=0, restore=snap)
        left = {k: v for k, v in captured.items() if k != 'snapshot_capture'}
        right = {k: v for k, v in zero.items() if k != 'snapshot_restore'}
        self.assertEqual(json.dumps(left), json.dumps(right))
        # budget>0 恢复照跑(legacy 契约):首次重试即重现同一停点、步数不动。
        resumed = self.run_world(budget=10, restore=snap)
        self.assertEqual(resumed['stop'], captured['stop'])
        self.assertEqual(resumed['completed_steps'], captured['completed_steps'])
        self.assertEqual(resumed['snapshot_restore']['post_restore_attempts'], 1)

    def test_clean_budget_capture_reports_attempts_equal_to_budget(self):
        snap = Path(self.temp.name) / 'clean.bin'
        captured = self.run_world(budget=3000, capture=snap)
        self.assertEqual(captured['stop'], {'kind': 'instruction_budget'})
        self.assertEqual(captured['snapshot_capture']['loop_attempts'], 3000)


# ---------------------------------------------------------------------------
# Artifact tier (design §3.2): manual, ROM-gated. The conditional-frontier
# flag stack (frontier_registry scenario_flags('p11_heap_experimental'))
# rebuilt through the full irq_case construction chain; capture at a budget
# stop, resume X in {+1, +10k, +100k}, byte-compare against the full replay.
# Snapshot files stay in analysis/ (gitignored); only the summary digest row
# is written to evidence/snapshot-artifact.json (no RAM bytes).
# Env: H3S_SNAPSHOT_ROM=<captured ROM release asset> (required),
#      H3S_SNAPSHOT_CAPTURE_N=<budget stop> (default 300000),
#      H3S_SNAPSHOT_WORK=<dir> (default analysis/snapshot),
#      H3S_SNAPSHOT_CANONICAL=1 also captures the 19M-step canonical stop.
# ---------------------------------------------------------------------------

ARTIFACT_ROM = os.environ.get('H3S_SNAPSHOT_ROM')
ARTIFACT_CAPTURE_N = int(os.environ.get('H3S_SNAPSHOT_CAPTURE_N', '300000'))
ARTIFACT_CANONICAL = bool(os.environ.get('H3S_SNAPSHOT_CANONICAL'))


@unittest.skipUnless(ARTIFACT_ROM, 'manual artifact tier: set H3S_SNAPSHOT_ROM '
                     'to the authorized captured-ROM release asset')
class ArtifactTierFrontierWorld(unittest.TestCase):
    X_TIERS = (1, 10_000, 100_000)

    @classmethod
    def setUpClass(cls):
        from probe_pi32v2 import ROOT, upstream
        from probe_boot import application
        from probe_irq_entry import package_header, flash_image
        import probe_irq_entry
        from frontier_registry import scenario_flags
        firmware = ROOT / 'firmware/QCY_H3S_7018F_1T2_release_note_V1.1.8_011.ufw'
        cls.ROOT = ROOT
        cls.rom = Path(ARTIFACT_ROM).read_bytes()
        cls.app = application(firmware)
        cls.header = package_header(firmware)
        cls.image = flash_image(firmware)
        cls.module = upstream()
        cls.flags = scenario_flags('p11_heap_experimental', probe_irq_entry)
        cls.work = Path(os.environ.get('H3S_SNAPSHOT_WORK') or (ROOT / 'analysis/snapshot'))
        cls.work.mkdir(parents=True, exist_ok=True)

    def run_frontier(self, **snapshot):
        from probe_irq_entry import irq_case
        return irq_case(self.module, self.app, self.header, self.rom,
                        'p11_heap_experimental_config', **self.flags,
                        flash_image_bytes=self.image, snapshot=snapshot or None)

    def summary_rows(self):
        path = self.ROOT / 'evidence/snapshot-artifact.json'
        return json.loads(path.read_text()) if path.exists() else []

    def write_summary(self, rows):
        (self.ROOT / 'evidence/snapshot-artifact.json').write_text(
            json.dumps(rows, indent=2) + '\n')

    def test_advance_tier_three_x_on_conditional_frontier_flags(self):
        snap = self.work / f'frontier-n{ARTIFACT_CAPTURE_N}.bin'
        captured = self.run_frontier(budget=ARTIFACT_CAPTURE_N, capture=snap)
        endpoint = captured['completed_steps']
        digest = captured['snapshot_capture']['sha256']
        zero = self.run_frontier(budget=0, restore=snap)  # self-check #0
        rows = [row for row in self.summary_rows() if row.get('snapshot') != snap.name]
        rows.append({'scenario': 'p11_heap_experimental_config',
                     'capture_kind': 'instruction_budget', 'capture_steps': endpoint,
                     'snapshot': snap.name, 'snapshot_sha256': digest,
                     'snapshot_bytes': captured['snapshot_capture']['bytes'],
                     'code_fingerprint': captured['snapshot_capture']['code_fingerprint'],
                     'self_check_zero_report_equal':
                         json.dumps({k: v for k, v in captured.items()
                                     if k != 'snapshot_capture'})
                         == json.dumps({k: v for k, v in zero.items()
                                        if k != 'snapshot_restore'})})
        for steps in self.X_TIERS:
            with self.subTest(x=steps):
                full = self.run_frontier(budget=ARTIFACT_CAPTURE_N + steps)
                resumed = self.run_frontier(budget=steps, restore=snap)
                comparison = dict(resumed)
                comparison.pop('snapshot_restore')
                equal = (json.dumps(comparison) == json.dumps(full))
                rows.append({'scenario': 'p11_heap_experimental_config',
                             'capture_kind': 'instruction_budget',
                             'capture_steps': endpoint, 'advance_steps': steps,
                             'snapshot': snap.name,
                             'full_steps': full['completed_steps'],
                             'resumed_steps': resumed['completed_steps'],
                             'report_byte_equal': equal,
                             'stop_kind': full['stop']['kind']})
                self.assertTrue(equal, f'advance tier X=+{steps} diverged from the '
                                'full replay (no diff whitelist)')
        self.write_summary(rows)

    def test_cross_switch_resume_matches_full_replay(self):
        """路 CKPT(前沿迭代循环)实尺度等价:OFF 世界(CON0 fixture 关)
        budget 停点捕获 → ON 世界从快照 resume(resume_ack enable_slots)vs
        ON 从 0 跑到同预算——前缀未触该容器,两世界逐字节同;跨开关恢复的
        机械正确性(真旗舰形状的 None→on,免 41M 步全量)。"""
        from probe_irq_entry import irq_case
        snap = self.work / f'frontier-crossswitch-n{ARTIFACT_CAPTURE_N}.bin'
        off_flags = {**self.flags, 'jl_pled_con0_initial_fixture': None}
        captured = irq_case(self.module, self.app, self.header, self.rom,
                            'p11_heap_experimental_config', **off_flags,
                            flash_image_bytes=self.image,
                            snapshot={'budget': ARTIFACT_CAPTURE_N, 'capture': str(snap)})
        self.assertEqual(captured['stop'], {'kind': 'instruction_budget'})
        advance = 2000
        full = irq_case(self.module, self.app, self.header, self.rom,
                        'p11_heap_experimental_config', **self.flags,
                        flash_image_bytes=self.image,
                        snapshot={'budget': ARTIFACT_CAPTURE_N + advance})
        resumed = irq_case(self.module, self.app, self.header, self.rom,
                           'p11_heap_experimental_config', **self.flags,
                           flash_image_bytes=self.image,
                           snapshot={'restore': str(snap), 'budget': advance,
                                     'expected_context': {
                                         'scenario': 'p11_heap_experimental_config',
                                         'completed_steps': captured['completed_steps']},
                                     'resume_ack': {
                                         'enable_slots': ['jl_pled_con0'],
                                         'reason': 'CON0 first access is at 41,847,806, '
                                                   'beyond this capture boundary'}})
        comparison = dict(resumed)
        comparison.pop('snapshot_restore')
        equal = json.dumps(comparison) == json.dumps(full)
        rows = [row for row in self.summary_rows()
                if row.get('snapshot') != snap.name]
        rows.append({'scenario': 'p11_heap_experimental_config',
                     'capture_kind': 'instruction_budget',
                     'capture_steps': captured['completed_steps'],
                     'cross_switch_enable_slots': ['jl_pled_con0'],
                     'advance_steps': advance, 'snapshot': snap.name,
                     'full_steps': full['completed_steps'],
                     'resumed_steps': resumed['completed_steps'],
                     'report_byte_equal': equal,
                     'stop_kind': full['stop']['kind']})
        self.write_summary(rows)
        self.assertTrue(equal, 'cross-switch resumed report differs from the full '
                        'replay (no diff whitelist)')

    def test_canonical_frontier_capture_and_self_check(self):
        if not ARTIFACT_CANONICAL:
            self.skipTest('canonical capture is a ~19-minute run; set H3S_SNAPSHOT_CANONICAL=1')
        snap = self.work / 'canonical-p11-heap-experimental.bin'
        captured = self.run_frontier(capture=snap)  # flag-derived 20M budget
        zero = self.run_frontier(budget=0, restore=snap)
        left = {k: v for k, v in captured.items() if k != 'snapshot_capture'}
        right = {k: v for k, v in zero.items() if k != 'snapshot_restore'}
        self.assertEqual(captured['stop']['kind'], 'unmapped_memory')
        self.assertEqual(json.dumps(left), json.dumps(right))
        rows = [row for row in self.summary_rows()
                if row.get('snapshot') != snap.name]
        rows.append({'scenario': 'p11_heap_experimental_config', 'capture_kind': 'frontier_stop',
                     'capture_steps': captured['completed_steps'],
                     'stop_pc': captured['stop_pc'], 'snapshot': snap.name,
                     'snapshot_sha256': captured['snapshot_capture']['sha256'],
                     'snapshot_bytes': captured['snapshot_capture']['bytes'],
                     'code_fingerprint': captured['snapshot_capture']['code_fingerprint'],
                     'self_check_zero_report_equal': True})
        self.write_summary(rows)


if __name__ == '__main__':
    unittest.main()
