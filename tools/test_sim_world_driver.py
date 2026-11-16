#!/usr/bin/env python3
"""tools/sim_world_driver.py 的单元测试（路 SIMB1C，docs/sim-m1-direct-spec.md §5.1）。

纯逻辑部分用合成 machine/cpu 形状（消费面=direct_call/读/写/守卫/diff 归因的属性集）；
世界相关断言（真实停点、真固件直调）留给验收执行器 tools/test_sim_m1_verdicts.py。
"""
import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import sim_world_driver as swd


class FakeInsn:
    def __init__(self, id='nop'):
        self.id = id


class FakeMem:
    """byte 字典 RAM（driver 只用 read/write/ram 三个面）。"""

    def __init__(self, ram=None):
        self.ram = {a: b for a, b in (ram or {}).items()}

    def read(self, address, size):
        return bytes(self.ram.get(address + i, 0) for i in range(size))

    def write(self, address, data):
        for i, value in enumerate(data):
            self.ram[address + i] = value


class FakeCpu:
    """脚本化 CPU：program={pc: [动作,…]}，动作=('ret',)/('r0',v)/('sp',delta)/('pc',v)/('nop',)。"""

    def __init__(self, program, mem=None, sp=0x130000):
        self.program = program
        self.mem = mem or FakeMem()
        self.core_id = 0
        self.sp_known = True
        self.active_context = None
        self.after_then = None
        self.repeat = None
        self.pc = 0x6072b92
        self.sp = sp
        self.rets = 0
        self.flags = 0
        self.icfg = 0x300
        self.R = [0] * 16

    def decode(self, pc):
        return FakeInsn(), {}, 2

    def step(self):
        for action in self.program.get(self.pc, [('nop',)]):
            kind = action[0]
            if kind == 'ret':
                self.pc = self.rets
                return
            if kind == 'r0':
                self.R[0] = action[1]
            elif kind == 'sp':
                self.sp += action[1]
            elif kind == 'pc':
                self.pc = action[1]
                return
            elif kind == 'watch_r4':
                self.R[4] = action[1]
            elif kind == 'icfg':
                self.icfg = action[1]
        self.pc = self.pc + 2


class FakeMachine:
    def __init__(self):
        self.after_step_calls = []
        self.cores = {}

    def after_step(self, cpu=None, before=None):
        self.after_step_calls.append((before, cpu.pc if cpu else None))

    def select(self):
        return self.current

    def note_stall(self, cpu, details):
        pass


def make_driver(program, ram=None, **kwargs):
    cpu = FakeCpu(program, FakeMem(ram), **kwargs)
    machine = FakeMachine()
    machine.current = cpu
    machine.cores = {0: cpu}
    driver = swd.SimWorldDriver(machine, FakeHardware(), cpu)
    return driver, cpu, machine


class FakeHardware:
    def __init__(self):
        self.advances = 0

    def advance(self, icfg):
        self.advances += 1


class DirectCallTest(unittest.TestCase):

    def test_sentinel_return_and_register_restore(self):
        driver, cpu, machine = make_driver({swd.GATE3: [('r0', 5), ('ret',)]},
                                           sp=0x130100)
        cpu.R[3] = 0xdeadbeef
        r0, steps, hits = driver.direct_call(swd.GATE3, [1, 2, 3, 4], watch=(swd.GATE3,))
        self.assertEqual((r0, steps), (5, 1))
        self.assertEqual([h['pc'] for h in hits], [hex(swd.GATE3)])   # 入口记一次（含 r0-r3）
        self.assertEqual(hits[0]['r'][:4], [hex(1), hex(2), hex(3), hex(4)])
        self.assertEqual(cpu.R[3], 0xdeadbeef)                        # 全量恢复
        self.assertEqual(cpu.pc, 0x6072b92)
        self.assertFalse(driver.dead)

    def test_budget_exhaustion_marks_world_dead(self):
        driver, cpu, _ = make_driver({0x600000: [('pc', 0x600000)]})  # 自旋
        with self.assertRaises(swd.WorldDead) as ctx:
            driver.direct_call(0x600000, [], max_steps=8)
        self.assertEqual(ctx.exception.code, 'direct_call_budget')
        self.assertTrue(driver.dead)
        self.assertEqual(cpu.pc, 0x6072b92)                           # finally 只恢复寄存器
        with self.assertRaises(swd.WorldDead):
            driver.direct_call(swd.GATE3, [])                         # 死世界拒绝后续直调

    def test_sp_unbalance_marks_world_dead(self):
        driver, _, _ = make_driver({0x600000: [('sp', -8), ('ret',)]})
        with self.assertRaises(swd.WorldDead) as ctx:
            driver.direct_call(0x600000, [])
        self.assertEqual(ctx.exception.code, 'direct_call_sp_unbalanced')
        self.assertTrue(driver.dead)

    def test_precondition_gate_refuses_without_touching_world(self):
        driver, cpu, _ = make_driver({swd.GATE3: [('ret',)]})
        cpu.sp_known = False
        with self.assertRaises(swd.DriverError) as ctx:
            driver.direct_call(swd.GATE3, [])
        self.assertEqual(ctx.exception.code, 'world_not_ready')
        self.assertFalse(driver.dead)                                 # 纪律②：不碰世界
        cpu.sp_known = True
        cpu.repeat = object()
        with self.assertRaises(swd.DriverError):
            driver.direct_call(swd.GATE3, [])
        cpu.repeat = None
        cpu.core_id = 1
        with self.assertRaises(swd.DriverError):
            driver.direct_call(swd.GATE3, [])

    def test_handler_context_stop_is_served_and_annotated(self):
        """SIMB1F：park 前沿停点在 Timer0 handler 链内——handler 上下文不再
        一刀切拒绝（banner in_irq_context 如实标注），callee 不碰上下文寄存器
        即可正常直调（纪律①b 校验兜底）。"""
        driver, cpu, _ = make_driver({swd.GATE3: [('ret',)]})
        cpu.active_context = 'timer0_irq_handler'
        _r0, _steps, _hits = driver.direct_call(swd.GATE3, [])
        self.assertFalse(driver.dead)
        self.assertEqual(driver.in_irq_context, 'timer0_irq_handler')
        self.assertEqual(driver.describe()['in_irq_context'], 'timer0_irq_handler')

    def test_callee_touching_context_registers_kills_world(self):
        """纪律①b：callee 触碰 icfg（保存集之外的上下文面）→ world_dead 如实暴露。"""
        driver, _cpu, _machine = make_driver({swd.GATE3: [('icfg', 0x0), ('ret',)]})
        with self.assertRaises(swd.WorldDead) as ctx:
            driver.direct_call(swd.GATE3, [])
        self.assertEqual(ctx.exception.code, 'direct_call_context_corrupted')
        self.assertTrue(driver.dead)


class MemBlockedTest(unittest.TestCase):

    def test_mem_read_blocked_kills_world(self):
        class BlockedMem(FakeMem):
            def read(self, address, size):
                raise swd.Blocked('uninitialized_memory', address=hex(address))

        cpu = FakeCpu({}, BlockedMem())
        driver = swd.SimWorldDriver(FakeMachine(), FakeHardware(), cpu)
        self.assertFalse(driver.dead)                       # 指针面缺失不杀世界
        with self.assertRaises(swd.WorldDead):
            driver.mem_read(0x99, 4)
        self.assertTrue(driver.dead)


class ReadFallbackTest(unittest.TestCase):
    """SIMB1F §2.2 降级备选：分发器路径撞栈口袋簿记读假停 → getter 直调复役。"""

    def _driver(self):
        driver, cpu, _machine = make_driver({})
        cpu.sp = 0x1237d8
        driver.entry_sp = 0x1237d8
        driver.min_sp_seen = 0x123700
        return driver, cpu

    def test_dead_frame_bookkeeping_stop_falls_back_to_getter(self):
        driver, _cpu = self._driver()
        calls = []

        def fake_direct_call(pc, args, **kwargs):
            calls.append(pc)
            if pc == swd.DISPATCHER:
                raise swd.WorldDead('direct_call_blocked', pc=hex(pc),
                                    stop={'kind': 'uninitialized_memory', 'access': 'read',
                                          'address': '0x123704', 'size': 4,
                                          'region': 'bootstrap_stack'})
            if pc == swd.ANC_GETTER:
                _cpu.mem.write(driver.scratch_resp, b'\x01\x02\x03')
                return 3, 12, []
            raise AssertionError('unexpected pc %r' % pc)

        driver.direct_call = fake_direct_call
        driver.table_a_entry = lambda cmdid: {'getter': swd.ANC_GETTER, 'setter': 0, 'cmdid': cmdid,
                                              'init': 0, 'restore': 0, 'slot32': 0, 'entry': 0}
        payload = driver.read(0x17, 0x6f)
        self.assertEqual(payload, bytes([0x17, 3, 1, 2, 3]))   # cmdid|n 头 server 组装
        self.assertEqual(driver.read_mode, 'getter-fallback')
        self.assertFalse(driver.dead)                          # 复役（寄存器/上下文未动）
        self.assertEqual(calls, [swd.DISPATCHER, swd.ANC_GETTER])
        # 粘性：后续读不再撞分发器
        payload2 = driver.read(0x17, 0x70)
        self.assertEqual(payload2, bytes([0x17, 3, 1, 2, 3]))
        self.assertEqual(calls, [swd.DISPATCHER, swd.ANC_GETTER, swd.ANC_GETTER])

    def test_non_dead_frame_stop_stays_world_dead(self):
        driver, _cpu = self._driver()

        def fake_direct_call(pc, args, **kwargs):
            driver.dead = True                                  # 镜像 _die 语义
            raise swd.WorldDead('direct_call_blocked', pc=hex(pc),
                                stop={'kind': 'uninitialized_memory', 'access': 'read',
                                      'address': '0x18d03c', 'size': 4})   # 堆上，非死帧窗

        driver.direct_call = fake_direct_call
        with self.assertRaises(swd.WorldDead):
            driver.read(0x17, 0x6f)
        self.assertTrue(driver.dead)
        self.assertEqual(driver.read_mode, 'dispatcher')       # 不降级（D4 族另归因）

    def test_context_corruption_blocks_fallback(self):
        driver, cpu = self._driver()

        def fake_direct_call(pc, args, **kwargs):
            driver.dead = True                                  # 镜像 _die 语义
            cpu.icfg = 0x123                                    # 上下文寄存器被碰
            raise swd.WorldDead('direct_call_blocked', pc=hex(pc),
                                stop={'kind': 'uninitialized_memory', 'access': 'read',
                                      'address': '0x123704', 'size': 4})

        driver.direct_call = fake_direct_call
        with self.assertRaises(swd.WorldDead):
            driver.read(0x17, 0x6f)
        self.assertTrue(driver.dead)


class ReadShapeTest(unittest.TestCase):

    def _driver_with_hit(self, hit, ram):
        driver, _, _ = make_driver({}, ram)
        driver.direct_call = lambda *a, **k: (0, 10, [hit] if hit else [])
        return driver

    def test_read_returns_payload_and_validates(self):
        resp = 0x12ffe0
        ram = {resp + i: b for i, b in enumerate(bytes([0x17, 3, 1, 1, 2]))}
        hit = {'pc': hex(swd.RETURN_EXIT), 'r': [hex(v) for v in (0xFF, 0, 0x6F, resp)],
               'step': 5, 'label': None}
        driver = self._driver_with_hit(hit, ram)
        self.assertEqual(driver.read(0x17, 0x6F), bytes([0x17, 3, 1, 1, 2]))
        # body 已写入 scratch（FE 01 cmdid）
        self.assertEqual(driver.mem_read(driver.scratch_body, 3), b'\xfe\x01\x17')

    def test_read_silent_without_hit(self):
        driver = self._driver_with_hit(None, {})
        self.assertIsNone(driver.read(0x10, 9))

    def test_read_shape_violation_kills_world(self):
        resp = 0x12ffe0
        ram = {resp + i: b for i, b in enumerate(bytes([0x17, 3, 1, 1, 2]))}
        hit = {'pc': hex(swd.RETURN_EXIT), 'r': [hex(v) for v in (0xFF, 0, 0x11, resp)],
               'step': 5, 'label': None}                              # SN 回显错误
        driver = self._driver_with_hit(hit, ram)
        with self.assertRaises(swd.WorldDead) as ctx:
            driver.read(0x17, 0x6F)
        self.assertEqual(ctx.exception.code, 'read_shape')


class TableATest(unittest.TestCase):

    def test_table_a_entry_parses_fields(self):
        ram = {}
        for index, cmdid in enumerate((0x22, 0x17, 0x2f)):
            entry = swd.TABLE_A_BEGIN + index * swd.TABLE_A_STRIDE
            fields = struct.pack('<IIIII', 0x1000 + index, 0x2000 + index, 0x3000 + index,
                                 0x4000 + index, 0)
            for offset, value in enumerate(fields):
                ram[entry + offset] = value
            ram[entry + 24] = cmdid
        driver, _, _ = make_driver({}, ram)
        row = driver.table_a_entry(0x17)
        self.assertEqual((row['getter'], row['setter'], row['cmdid']),
                         (0x2001, 0x3001, 0x17))
        self.assertIsNone(driver.table_a_entry(0x99))

    def test_apply_write_empty_slot_and_too_long(self):
        driver, _, _ = make_driver({})
        driver.table_a_entry = lambda cmdid: {'cmdid': cmdid, 'setter': 0, 'entry': 0}
        result = driver.apply_write(0x2f, b'\x01')
        self.assertEqual(result['setter'], 0)                          # 官方槽空：静默跳尾
        driver.table_a_entry = lambda cmdid: {'cmdid': cmdid, 'setter': 0x602583c, 'entry': 0}
        with self.assertRaises(swd.DriverError) as ctx:
            driver.apply_write(0x22, bytes(200))
        self.assertEqual(ctx.exception.code, 'write_data_too_long')


class WriteFlowTest(unittest.TestCase):

    def test_apply_write_direct_calls_setter_with_table_abi(self):
        captured = {}

        def fake_direct_call(pc, args, **kwargs):
            captured['pc'], captured['args'], captured['watch'] = pc, args, kwargs.get('watch')
            return 0, 42, [{'pc': hex(swd.EQ_APPLY), 'r': ['0x0'] * 7, 'step': 1, 'label': None}]

        driver, _, _ = make_driver({})
        driver.table_a_entry = lambda cmdid: {'cmdid': cmdid, 'setter': 0x602583c, 'entry': 0x060a9bd8}
        driver.direct_call = fake_direct_call
        result = driver.apply_write(0x22, bytes([1] * 10))
        self.assertEqual(captured['pc'], 0x602583c)
        self.assertEqual(captured['args'], [driver.scratch_resp, 128, driver.scratch_body, 10])
        self.assertIn(swd.EQ_APPLY, captured['watch'])
        self.assertEqual((result['n'], result['steps']), (0, 42))      # 恒返 0=常态
        self.assertFalse(result['setter_returned_nonzero'])
        self.assertEqual(driver.mem_read(driver.scratch_body, 10), bytes([1] * 10))

    def test_apply_write_notes_nonzero_setter_return(self):
        driver, _, _ = make_driver({})
        driver.table_a_entry = lambda cmdid: {'cmdid': cmdid, 'setter': 0x6025ca4, 'entry': 0}
        driver.direct_call = lambda *a, **k: (3, 5, [])
        result = driver.apply_write(0x17, b'\x01\x01\x02')
        self.assertTrue(result['setter_returned_nonzero'])
        self.assertIn('静态结论外', result['note'])

    def test_queue_derivation_from_poster_watch(self):
        queue = swd.HEAP_BEGIN + 0x100
        buf = swd.HEAP_BEGIN + 0x800
        ram = {}
        for offset, value in enumerate(struct.pack('<H', 4)):
            ram[queue + 14 + offset] = value                          # count=4
        for offset, value in enumerate(struct.pack('<H', 32)):
            ram[queue + 12 + offset] = value                          # capacity=32
        for offset, value in enumerate(struct.pack('<I', buf)):
            ram[queue + 16 + offset] = value                          # buffer ptr
        for i in range(4):
            for offset, value in enumerate(struct.pack('<I', 0x30000004 if i == 0 else
                                                       (0x6025D6A if i == 1 else (1 if i == 2 else 0)))):
                ram[buf + 4 * i + offset] = value
        hits = [{'pc': hex(swd.POSTER_QUEUE_READ), 'r': ['0x0'] * 4 + [hex(queue), '0x0', '0x0'],
                 'step': 3, 'label': None}]
        driver, _, _ = make_driver({}, ram)
        driver.direct_call = lambda *a, **k: (0, 9, hits)
        driver.table_a_entry = lambda cmdid: {'cmdid': cmdid, 'setter': 0x6025ca4, 'entry': 0}
        result = driver.apply_write(0x17, b'\x01\x01\x02')
        face = result['queue_after']
        self.assertEqual(face['count'], 4)
        self.assertEqual(face['buffer_words'][:4],
                         [hex(0x30000004), hex(0x6025D6A), hex(1), hex(0)])
        # 推导出的队列窗进入白名单
        self.assertIn(bytes([4, 0]), driver.world_inspect(queue + 14, 2))


class WorldDiffTest(unittest.TestCase):

    def test_diff_attribution_regions(self):
        driver, cpu, _ = make_driver({})
        driver.take_baseline()
        cfg = swd.CONFIG_BASE + 8
        cpu.mem.write(cfg, b'\x01')                                   # config 镜像域
        cpu.mem.write(driver.scratch_body, b'\xfe\x01\x22')           # scratch
        cpu.mem.write(swd.HEAP_BEGIN + 0x40, b'\xff')                 # heap
        cpu.mem.write(0x19fe10, b'\x01')                              # irq vector table
        diff = driver.world_diff()
        self.assertEqual(diff['changed_bytes'], 6)                    # scratch 3B + config/heap/irq 各 1B
        self.assertIn('config_mirror', diff['regions'])
        self.assertIn('scratch', diff['regions'])
        self.assertIn('heap', diff['regions'])
        self.assertIn('irq_vector_table', diff['regions'])
        self.assertEqual(diff['unattributed_count'], 0)

    def test_unattributed_changes_flagged(self):
        driver, cpu, _ = make_driver({})
        driver.take_baseline()
        cpu.mem.write(0x122900, b'\x01')                              # mmu 页表外
        diff = driver.world_diff()
        self.assertEqual(diff['unattributed_count'], 1)

    def test_no_baseline_refuses(self):
        driver, _, _ = make_driver({})
        with self.assertRaises(swd.DriverError):
            driver.world_diff()


class InspectWhitelistTest(unittest.TestCase):

    def test_whitelist_membership(self):
        driver, _, _ = make_driver({})
        self.assertEqual(driver.world_inspect(swd.ANC_WIRE, 3), b'\x00\x00\x00')
        self.assertEqual(len(driver.world_inspect(swd.TABLE_A_BEGIN, 28)), 28)
        with self.assertRaises(swd.DriverError) as ctx:
            driver.world_inspect(0x19fd00, 4)                         # handoff 区不在白名单
        self.assertEqual(ctx.exception.code, 'not_whitelisted')


class ScratchGuardTest(unittest.TestCase):

    def test_empty_guard_passes_and_deep_probe(self):
        driver, cpu, _ = make_driver({swd.GATE3: [('r0', 1), ('ret',)],
                                      swd.EQ_APPLY: [('sp', -64)],
                                      swd.EQ_APPLY + 2: [('sp', 64), ('ret',)]})
        guard = driver.run_scratch_guard(deep=True)
        self.assertEqual(guard['empty_call']['stray_count'], 0)
        self.assertEqual(guard['deep_probe']['min_sp'], hex(0x130000 - 64))
        self.assertTrue(guard['scratch_stack_disjoint'])
        self.assertFalse(driver.dead)

    def test_stray_write_fails_guard(self):
        program = {swd.GATE3: [('ret',)]}

        class SpillingCpu(FakeCpu):
            def step(self):
                self.mem.write(0x126000, b'\x01')                     # 直调段外写
                super().step()

        cpu = SpillingCpu(program, FakeMem())
        driver = swd.SimWorldDriver(FakeMachine(), FakeHardware(), cpu)
        with self.assertRaises(swd.WorldDead) as ctx:
            driver.run_scratch_guard()
        self.assertEqual(ctx.exception.code, 'scratch_guard_stray_writes')

    def test_stack_overlap_detected(self):
        driver, _, _ = make_driver({swd.GATE3: [('ret',)]}, sp=0x12A600)
        driver.entry_sp = swd.SCRATCH_BODY_DEFAULT + 4                # 入口 sp 落进 scratch
        driver.min_sp_seen = swd.SCRATCH_BODY_DEFAULT - 0x100
        with self.assertRaises(swd.WorldDead) as ctx:
            driver.scratch_stack_check()
        self.assertEqual(ctx.exception.code, 'scratch_stack_overlap')

    def test_smoke_read_anc(self):
        wire = bytes([2, 1, 4])
        ram = {swd.ANC_WIRE + i: b for i, b in enumerate(wire)}
        driver, cpu, _ = make_driver({swd.ANC_GETTER: [('ret',)]}, ram)
        cpu.program[swd.ANC_GETTER] = [('r0', 3)] + [('ret',)]
        # getter 写缓冲（模拟固件 getter 行为）
        original_step = cpu.step

        def step():
            for i, value in enumerate(wire):
                cpu.mem.write(driver.scratch_resp + 112 + i, bytes([value]))
            original_step()

        cpu.step = step
        record = driver.smoke_read_anc()
        self.assertEqual(record['r0'], 3)
        self.assertEqual(record['payload'], list(wire))
        self.assertEqual(record['wire'], list(wire))


class ResumeTest(unittest.TestCase):

    def test_resume_steps_runs_loop_with_advance_and_watch(self):
        driver, cpu, machine = make_driver({0x6072b92: [('pc', 0x600000)],
                                            0x600000: [('pc', 0x600000)]})
        hardware = driver.hardware
        result = driver.resume_steps(5, watch=(0x600000,))
        self.assertEqual(result['steps'], 5)
        self.assertEqual(hardware.advances, 5)
        # 首迭代在停点 pc，其后 4 步都在 watch pc 上
        self.assertEqual(len(result['watch_hits']), 4)
        self.assertFalse(driver.dead)

    def test_resume_world_death_on_blocked(self):
        class BlockedHardware(FakeHardware):
            def advance(self, icfg):
                raise swd.Blocked('unmapped_or_readonly_write', address='0xfe1500')

        cpu = FakeCpu({})
        machine = FakeMachine()
        machine.current = cpu
        driver = swd.SimWorldDriver(machine, BlockedHardware(), cpu)
        with self.assertRaises(swd.WorldDead) as ctx:
            driver.resume_steps(3)
        self.assertEqual(ctx.exception.code, 'resume_stopped')
        self.assertEqual(ctx.exception.detail['stop']['address'], '0xfe1500')

    def test_resume_stop_carries_pre_stop_watch_hits(self):
        """SIMB1F：撞墙前的 watch 流水并进 resume_stopped 的错误 detail——
        M1-c 的 C1 断言面（解冻窗口内 0x6064c0e 消费证据可能在墙之前已发生）。"""
        class BlockedHardware(FakeHardware):
            def __init__(self):
                super().__init__()
                self.grace = 2                       # 前两次 advance 放行（让 watch 先命中）

            def advance(self, icfg):
                if self.grace > 0:
                    self.grace -= 1
                    return
                raise swd.Blocked('unmapped_memory', address='0xfd1104')

        driver, cpu, _machine = make_driver({0x6072b92: [('pc', 0x6064c0e)],
                                             0x6064c0e: [('pc', 0x6064c0e)]})
        driver.hardware = BlockedHardware()              # 换成撞墙硬件
        with self.assertRaises(swd.WorldDead) as ctx:
            driver.resume_steps(10, watch=(0x6064c0e,))
        detail = ctx.exception.detail
        self.assertEqual(detail['stop']['address'], '0xfd1104')
        hit_pcs = [hit['pc'] for hit in detail['watch_hits']]
        self.assertIn('0x6064c0e', hit_pcs)

    def test_resume_stall_branch_dispatches_delivery_request(self):
        """codex P1（2026-11-17 对账审查）：停泊（CoreStall）分支嵌套 advance 抛出的
        IrqDeliveryRequest 必须走交付分发（probe_boot:2043-2047 等价），不得转成
        upstream_simulator_failure——否则 resume 与真实执行循环不等价，M1-c 消费
        铁证不可信。桩形：idle 停泊步触发 CoreStall；第一次 advance 抛交付请求、
        第二次抛 Blocked（无事件可推的诚实停）。"""
        from probe_boot import IrqDeliveryRequest

        class DeliveringHardware(FakeHardware):
            def __init__(self):
                super().__init__()
                self.raised = False

            def advance(self, icfg):
                if not self.raised:
                    self.raised = True
                    raise IrqDeliveryRequest(core=0, irq=3)
                raise swd.Blocked('all_cores_idle_waiting', idle_cores=[0])

        delivered = []

        class DeliveringMachine(FakeMachine):
            idle_wait_projection = True
            idle_wait_cores = set()

            def enter_idle_wait(self, cpu):      # 停泊记账被砍掉：循环持续选中停泊步
                pass

            def deliver_core_tick(self, core):
                delivered.append(core)

        cpu = FakeCpu({})
        cpu.decode = lambda pc: (FakeInsn('idle'), {}, 2)
        machine = DeliveringMachine()
        machine.current = cpu
        driver = swd.SimWorldDriver(machine, DeliveringHardware(), cpu)
        with self.assertRaises(swd.WorldDead) as ctx:
            driver.resume_steps(4)
        self.assertEqual(delivered, [0])                     # 交付先发生（不是 simulator_failure）
        self.assertEqual(ctx.exception.detail['stop']['kind'], 'all_cores_idle_waiting')
        self.assertEqual(ctx.exception.detail['steps'], '0x0')

    def test_resume_stall_branch_blocked_advances_still_stop(self):
        """对照负例：停泊分支 advance 抛 Blocked（无事件可推）仍是诚实停。"""
        class BlockedHardware(FakeHardware):
            def advance(self, icfg):
                raise swd.Blocked('all_cores_idle_waiting', idle_cores=[0])

        cpu = FakeCpu({})
        cpu.decode = lambda pc: (FakeInsn('idle'), {}, 2)
        machine = FakeMachine()
        machine.current = cpu
        machine.idle_wait_projection = False
        driver = swd.SimWorldDriver(machine, BlockedHardware(), cpu)
        with self.assertRaises(swd.WorldDead) as ctx:
            driver.resume_steps(2)
        self.assertEqual(ctx.exception.detail['stop']['kind'], 'all_cores_idle_waiting')

    def test_resume_parks_idle_core_via_idle_wait_projection(self):
        """SIMB1F §5.2 校准：park 世界两核靠 idle 停泊——idle 指令不交给模型
        step，进 machine 停泊集（explore 主循环同款），CoreStall 路径记账推进。"""
        parked = []

        class ParkingMachine(FakeMachine):
            idle_wait_projection = True
            idle_wait_cores = set()

            def enter_idle_wait(self, cpu):
                if cpu.core_id in self.idle_wait_cores:
                    # 真机形状：全核停泊且无事件钟可推 → 诚实停（explore 同款出口）
                    raise swd.Blocked('all_cores_idle_waiting',
                                      idle_cores=sorted(self.idle_wait_cores))
                self.idle_wait_cores.add(cpu.core_id)
                parked.append(cpu.core_id)

        class IdleCpu(FakeCpu):
            def decode(self, pc):
                return FakeInsn('idle'), {}, 2

        cpu = IdleCpu({})
        machine = ParkingMachine()
        machine.current = cpu
        driver = swd.SimWorldDriver(machine, FakeHardware(), cpu)
        with self.assertRaises(swd.WorldDead) as ctx:
            driver.resume_steps(3)
        self.assertEqual(parked, [0])                     # idle 进停泊集，不 step
        self.assertEqual(ctx.exception.detail['stop']['kind'], 'all_cores_idle_waiting')
        self.assertEqual(ctx.exception.detail['steps'], '0x0')   # 停泊步不推进
        self.assertIn(0, machine.idle_wait_cores)


class DescribeTest(unittest.TestCase):

    def test_banner_shape(self):
        driver, _, _ = make_driver({})
        banner = driver.describe()
        self.assertEqual(banner['mode'], 'direct-world')
        self.assertEqual(banner['stop_pc'], hex(0x6072b92))
        self.assertIn('resp', banner['scratch'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
