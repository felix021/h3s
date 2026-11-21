#!/usr/bin/env python3
"""Unit tests for the opt-in SRC_SYNC completion + IRQ89 delivery projection (route SRCSYNC5).

Mirrors tools/test_audio_dma_delivery.py: model-level completion/pending/W1C/readback
semantics plus machine-level minimal entry/RTI for the IRQ89 handler 0x12ca24
(vector slot 0x19ff64 = 0x19fe00+89*4; the firmware hw_res-table ram2 RAM copy,
flash source 0x60c61fc).
Design source: codex 202611201614 final.md §2 (SRC_SYNC 建到哪里).
"""
import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from br28_machine import DualCoreMachine
from br28_model import (BR28Model, JLSrcSyncDelivery,
                        JL_SRC_SYNC_CONFIG_WORDS, JL_SRC_SYNC_FULL_BLOCK_WORDS)
from probe_boot import Blocked, IrqDeliveryRequest

HANDLER = 0x12ca24     # IRQ89 handler (vector slot content, hw_res ram2 RAM copy)
SLOT = 0x19ff64        # 0x19fe00 + 89*4
TAIL = 0x12cad2        # RAM copy rti (flash source 0x60c62aa)
ACK_PC = 0x12ca42      # RAM copy of the CON0 |=0x40 W1C RMW (flash 0x60c621a)

# IRQ89 nibble: config[11] nibble 1 (bits 4..7) = prio1|enable (0b0011<<4).
IRQ89_NIBBLE_WORD = (3 << 4)


class FakeCpu:
    def __init__(self, mem, pc=0x606955c, core_id=0):
        self.mem = mem
        self.pc = pc
        self.reti = 0x12caea
        self.steps = 7
        self.core_id = core_id
        self.flags = 0
        self.sp_known = True
        self.sp = 0x1237b8
        self.usp = 0x4013f0
        self.ssp = 0x123840
        self.last_explicit_ssp = 0x123840
        self.bootstrap_context = None
        self.after_then = None
        self.repeat = None
        self.active_context = None


class VectorMem:
    def read(self, address, length):
        assert (address, length) == (SLOT, 4)
        return struct.pack('<I', HANDLER)

    def write(self, address, data):
        raise AssertionError('the delivery entry must not write through the vector mem')


class UnwrittenMem:
    def read(self, address, length):
        raise Blocked('uninitialized_memory', address=hex(address))


def armed_model(**extra):
    # SRC_SYNC write-side predecessors + the delivery projection; IRQ89 nibble
    # enabled prio1, ipmask 0. 测试固定 completion_steps=1(快速触发完成事件),
    # 显式传 jl_src_sync_completion_steps 可覆盖;真实场景用 40000(1 OS tick)。
    extra.setdefault('jl_src_sync_completion_steps', 1)
    return BR28Model(jl_src_sync_config_words=True, jl_src_sync_full_block=True,
                     jl_src_sync_delivery=True, jl_src_sync_delivery_requests=True,
                     initial_irq_config=[0] * 11 + [IRQ89_NIBBLE_WORD] + [0] * 20,
                     initial_ipmask=0, **extra)


def function_b_writes(h):
    # The real function-B whole-word stores (route SRCSYNC2): CON2/CON3/IDAT_ADR/
    # IDAT_LEN/ODAT_ADR/ODAT_LEN/FLTB_ADR/STOP_FLAG/INSR/OUTSR/PHASE, with
    # distinct values so the positional read-back is unambiguous. CON0 whole-word
    # overwrite rides the config container (the real 0x6070a90 store).
    h.jl_src_sync.write(0xfe2600, 0x82006001, 0)          # CON0 whole-word overwrite
    values = {0xfe2608: 0x00010002, 0xfe260c: 0x00010002,   # CON2/CON3
              0xfe2610: 0x41d30c, 0xfe2614: 0x40,           # IDAT_ADR/LEN
              0xfe2618: 0x41d9ac, 0xfe261c: 0x3a2,          # ODAT_ADR/LEN
              0xfe2620: 0x41e000, 0xfe262c: 0,              # FLTB_ADR/STOP_FLAG
              0xfe2630: 0, 0xfe2634: 0x41d9ae,              # INSR/OUTSR
              0xfe2638: 0x41f000}                            # PHASE
    for address in JL_SRC_SYNC_FULL_BLOCK_WORDS:
        h.jl_src_sync_full_block.write(address, values[address], 0)


class SrcSyncModelTests(unittest.TestCase):
    def test_delivery_requires_its_write_side_predecessors(self):
        with self.assertRaises(ValueError):
            BR28Model(jl_src_sync_delivery=True)
        with self.assertRaises(ValueError):
            BR28Model(jl_src_sync_config_words=True, jl_src_sync_delivery=True)
        with self.assertRaises(ValueError):
            BR28Model(jl_src_sync_config_words=True, jl_src_sync_full_block=True,
                      jl_src_sync_delivery_requests=True)

    def test_default_off_keeps_the_strict_read_stop(self):
        h = BR28Model(jl_src_sync_config_words=True, jl_src_sync_full_block=True)
        function_b_writes(h)
        with self.assertRaisesRegex(Blocked, 'jl_src_sync_full_block_read_not_modeled'):
            h.read(0xfe2610)   # IDAT_ADR stays refused with the delivery half off

    def test_delivery_serves_the_handler_progress_readback(self):
        h = armed_model()
        function_b_writes(h)
        self.assertEqual(h.read(0xfe2610), 0x41d30c)      # IDAT_ADR last write
        self.assertEqual(h.read(0xfe2614), 0x40)          # IDAT_LEN
        self.assertEqual(h.read(0xfe2618), 0x41d9ac)      # ODAT_ADR
        self.assertEqual(h.read(0xfe261c), 0x3a2)         # ODAT_LEN
        self.assertEqual(h.read(0xfe2620), 0x41e000)      # FLTB_ADR
        self.assertEqual(h.read(0xfe262c), 0)             # STOP_FLAG
        self.assertEqual(h.read(0xfe2634), 0x41d9ae)      # OUTSR
        self.assertEqual(h.jl_src_sync_full_block.reads_served, 7)
        self.assertTrue(h.jl_src_sync_full_block.report()['readback_projection']['enabled'])

    def test_unwritten_full_block_word_stays_stopped(self):
        h = armed_model()
        with self.assertRaisesRegex(Blocked, 'jl_src_sync_full_block_word_unwritten'):
            h.read(0xfe2610)

    def test_completion_event_raises_the_delivery_request_once_per_episode(self):
        h = armed_model()
        function_b_writes(h)
        h.jl_src_sync_delivery.note_start(h)             # the real rmw_start hook
        self.assertFalse(h.jl_src_sync_delivery.pending)
        with self.assertRaises(IrqDeliveryRequest) as caught:
            h.advance(0x300)                             # completion_steps=1 -> pending + request
        self.assertEqual((caught.exception.details['core'], caught.exception.details['irq']), (0, 89))
        self.assertTrue(h.jl_src_sync_delivery.pending)
        self.assertTrue(h.jl_src_sync_delivery.delivery_in_flight)
        self.assertEqual(h.jl_src_sync_delivery.completions, 1)
        # In flight: no second request for the same episode.
        h.advance(0x300)

    def test_completion_fires_after_the_explicit_delay(self):
        h = armed_model(jl_src_sync_completion_steps=3)
        function_b_writes(h)
        h.jl_src_sync_delivery.note_start(h)
        h.advance(0x300)                                 # step 1 of 3
        self.assertFalse(h.jl_src_sync_delivery.pending)
        h.advance(0x300)                                 # step 2 of 3
        self.assertFalse(h.jl_src_sync_delivery.pending)
        with self.assertRaises(IrqDeliveryRequest) as caught:
            h.advance(0x300)                             # step 3: complete + deliver
        self.assertEqual((caught.exception.details['core'], caught.exception.details['irq']), (0, 89))

    def test_handler_ack_w1c_clears_pending(self):
        h = armed_model()
        function_b_writes(h)
        h.jl_src_sync_delivery.note_start(h)
        with self.assertRaises(IrqDeliveryRequest):
            h.advance(0x300)                             # pending set, delivery in flight
        self.assertTrue(h.jl_src_sync_delivery.pending)
        value = h.jl_src_sync_delivery.rmw_ack(ACK_PC, 'rmw.or.modimm', 0x40, 0, h)
        self.assertEqual(value, 0x82006001 | 0x40)        # CON0 |= 0x40 composed on the old word
        self.assertFalse(h.jl_src_sync_delivery.pending)
        self.assertFalse(h.jl_src_sync_delivery.delivery_in_flight)
        self.assertEqual(h.jl_src_sync_delivery.acks, 1)
        # The ACK write-back is recorded in the original CON0 ledger.
        self.assertEqual(h.jl_src_sync.read(0xfe2600), 0x82006041)

    def test_ack_rmw_guards(self):
        h = armed_model()
        function_b_writes(h)
        with self.assertRaisesRegex(Blocked, 'jl_src_sync_ack_rmw_pc_not_audited'):
            h.jl_src_sync_delivery.rmw_ack(0xdead, 'rmw.or.modimm', 0x40, 0, h)
        with self.assertRaisesRegex(Blocked, 'jl_src_sync_ack_rmw_form_not_audited'):
            h.jl_src_sync_delivery.rmw_ack(ACK_PC, 'rmw.and.nmodimm', 0x40, 0, h)
        with self.assertRaisesRegex(Blocked, 'jl_src_sync_ack_rmw_form_not_audited'):
            h.jl_src_sync_delivery.rmw_ack(ACK_PC, 'rmw.or.modimm', 0x8, 0, h)

    def test_advance_strict_stop_without_the_request_switch(self):
        h = BR28Model(jl_src_sync_config_words=True, jl_src_sync_full_block=True,
                      jl_src_sync_delivery=True,
                      jl_src_sync_completion_steps=1,
                      initial_irq_config=[0] * 11 + [IRQ89_NIBBLE_WORD] + [0] * 20,
                      initial_ipmask=0)
        function_b_writes(h)
        h.jl_src_sync_delivery.note_start(h)
        with self.assertRaisesRegex(Blocked, 'interrupt_delivery_not_implemented'):
            h.advance(0x300)

    def test_advance_keeps_a_masked_episode_inert(self):
        # IRQ89 nibble cleared: pending stays set, nothing raises, nothing stops.
        h = armed_model()
        function_b_writes(h)
        h.irq.config[11] = 0                              # clear the IRQ89 word
        h.jl_src_sync_delivery.note_start(h)
        h.advance(0x300)                                 # nibble cleared: inert
        self.assertTrue(h.jl_src_sync_delivery.pending)
        self.assertFalse(h.jl_src_sync_delivery.delivery_in_flight)


def cursor_model(**extra):
    # 路 SRCSYNC6:在 delivery 之上叠 SRC 硬件游标进度。fixture 值同 function_b_writes:
    # C=CON2 低字节=2 通道、W=4 字节样本、IDAT_ADR=0x41d30c/IDAT_LEN=64、
    # ODAT_ADR=0x41d9ac/ODAT_LEN=930。默认 consume=exhaust(一次吃光 64)、
    # produce=1:1(产 64)。
    extra.setdefault('jl_src_sync_completion_steps', 1)
    return BR28Model(jl_src_sync_config_words=True, jl_src_sync_full_block=True,
                     jl_src_sync_delivery=True, jl_src_sync_delivery_requests=True,
                     jl_src_sync_cursor_progress=True,
                     initial_irq_config=[0] * 11 + [IRQ89_NIBBLE_WORD] + [0] * 20,
                     initial_ipmask=0, **extra)


class SrcSyncCursorProgressTests(unittest.TestCase):
    def test_cursor_progress_requires_delivery(self):
        with self.assertRaises(ValueError):
            BR28Model(jl_src_sync_config_words=True, jl_src_sync_full_block=True,
                      jl_src_sync_cursor_progress=True)

    def test_completion_commits_idat_and_odat_progress(self):
        h = cursor_model()
        function_b_writes(h)
        self.assertEqual(h.read(0xfe2614), 0x40)          # before start: last-written
        h.jl_src_sync_delivery.note_start(h)
        with self.assertRaises(IrqDeliveryRequest):
            h.advance(0x300)                             # commit + pending + request
        self.assertTrue(h.jl_src_sync_delivery.pending)
        # 逐块守恒:IDAT_LEN 64->0,IDAT_ADR +c*C*W=+64*2*4=+512。
        self.assertEqual(h.read(0xfe2614), 0x0)           # input exhausted -> loop exits
        self.assertEqual(h.read(0xfe2610), 0x41d30c + 512)
        # 输出端也推进:ODAT_ADR +p*C*W=+512,ODAT_LEN 930->866。
        self.assertEqual(h.read(0xfe2618), 0x41d9ac + 512)
        self.assertEqual(h.read(0xfe261c), 930 - 64)
        # 其余字仍最后写值(不受游标影响)。
        self.assertEqual(h.read(0xfe2638), 0x41f000)      # PHASE
        self.assertEqual(h.read(0xfe262c), 0)             # STOP_FLAG

    def test_committed_snapshot_survives_the_ack(self):
        h = cursor_model()
        function_b_writes(h)
        h.jl_src_sync_delivery.note_start(h)
        with self.assertRaises(IrqDeliveryRequest):
            h.advance(0x300)
        # handler 先清 IRQ(ACK),再依次读 IDAT/ODAT——ACK 后快照仍保留。
        h.jl_src_sync_delivery.rmw_ack(ACK_PC, 'rmw.or.modimm', 0x40, 0, h)
        self.assertFalse(h.jl_src_sync_delivery.pending)
        self.assertEqual(h.read(0xfe2614), 0x0)
        self.assertEqual(h.read(0xfe2610), 0x41d30c + 512)
        self.assertEqual(h.read(0xfe2618), 0x41d9ac + 512)

    def test_conservation_is_per_commit(self):
        h = cursor_model()
        function_b_writes(h)
        h.jl_src_sync_delivery.note_start(h)
        with self.assertRaises(IrqDeliveryRequest):
            h.advance(0x300)
        cursor = h.jl_src_sync_cursor
        self.assertEqual(cursor.commits, 1)
        rec = cursor.history[-1]
        self.assertEqual((rec['channels'], rec['sample_bytes']), (2, 4))
        self.assertEqual(rec['consumed'], 64)
        self.assertEqual(rec['produced'], 64)
        self.assertEqual(rec['idat_len_after'], rec['idat_len_before'] - rec['consumed'])
        self.assertEqual(rec['idat_after'] - rec['idat_before'],
                         rec['consumed'] * rec['channels'] * rec['sample_bytes'])
        self.assertEqual(rec['odat_after'] - rec['odat_before'],
                         rec['produced'] * rec['channels'] * rec['sample_bytes'])

    def test_out_of_range_consumption_fails_closed(self):
        # consume 显式给 65 > 剩余 64:验证在前、提交前即停,不置 pending。
        h = cursor_model(jl_src_sync_cursor_consume=65)
        function_b_writes(h)
        h.jl_src_sync_delivery.note_start(h)
        with self.assertRaisesRegex(Blocked, 'jl_src_sync_cursor_consume_out_of_range'):
            h.advance(0x300)
        self.assertFalse(h.jl_src_sync_delivery.pending)
        self.assertEqual(h.jl_src_sync_cursor.commits, 0)

    def test_idat_only_without_odat_is_the_tone_done_negative(self):
        # 只动 IDAT、ODAT 原地(produce=0):输入耗尽但产出字节数为零。
        # 固件 obj+76=obj+44-obj+72 算出的产出=0——「输入耗尽+零产出」不是
        # tone 播完,这是反例,须过不了 tone 播完验收。
        h = cursor_model(jl_src_sync_cursor_produce=0)
        function_b_writes(h)
        h.jl_src_sync_delivery.note_start(h)
        with self.assertRaises(IrqDeliveryRequest):
            h.advance(0x300)
        self.assertEqual(h.read(0xfe2614), 0x0)           # IDAT 耗尽(循环会退出)
        # ODAT_ADR 原地 -> 固件 obj+76=obj+44-obj+72 算出的产出字节数为 0
        # (obj+44 读回仍=输出起始字),「输入耗尽+零产出」=假 tone 播完。
        self.assertEqual(h.read(0xfe2618), 0x41d9ac)      # ODAT_ADR 原地

    def test_unchanged_arm_still_serves_last_written_value(self):
        # 旧臂(delivery 无游标)四游标字读回仍=最后写值——139,947,345 零消费对照。
        h = armed_model()
        function_b_writes(h)
        h.jl_src_sync_delivery.note_start(h)
        with self.assertRaises(IrqDeliveryRequest):
            h.advance(0x300)
        self.assertEqual(h.read(0xfe2614), 0x40)          # IDAT_LEN 不减
        self.assertEqual(h.read(0xfe2610), 0x41d30c)      # IDAT_ADR 不动
        self.assertEqual(h.read(0xfe2618), 0x41d9ac)      # ODAT_ADR 不动


def machine(entry=True, banks=False, rti=False, repeat=False):
    return DualCoreMachine(src_sync_irq_entry=entry, src_sync_irq_entry_banks=banks,
                           src_sync_irq_rti=rti, repeat_irq_delivery=repeat)


class SrcSyncIrqMachineTests(unittest.TestCase):
    def test_entry_is_opt_in(self):
        m = machine(entry=False)
        m.cores = {0: FakeCpu(VectorMem())}
        with self.assertRaises(ValueError):
            m.deliver_src_sync_irq(0)

    def test_delivery_reads_the_real_slot_and_keeps_pending(self):
        m = machine(entry=True, banks=True)
        cpu0 = FakeCpu(VectorMem(), pc=0x606955c, core_id=0)
        m.cores = {0: cpu0}
        m.deliver_src_sync_irq(0)
        self.assertEqual((cpu0.pc, cpu0.reti, cpu0.steps), (HANDLER, 0x606955c, 8))
        self.assertEqual((cpu0.sp, cpu0.usp, cpu0.ssp), (0x123840, 0x1237b8, 0x123840))
        self.assertEqual(cpu0.active_context, 'src_sync_irq_handler')
        event = m.src_sync_irq_deliveries[0]
        self.assertEqual((event['core'], event['irq'], event['vector_address'], event['handler']),
                         (0, 89, '0x19ff64', '0x12ca24'))
        self.assertFalse(event['delivery_clears_pending'])
        self.assertTrue(event['pending_cleared_by_handler_ack'])

    def test_handler_sp_pushes_preserve_entry_bank_fixtures(self):
        # 路 SRCSYNC5:handler 体内的 push/pop 是平衡帧对而非银行重指派——
        # note_sp_write 必须保护 src_sync_irq_handler 上下文,否则入口银行
        # fixture(usp=任务栈/ssp=内核栈)在首个 push 时就被清成 unknown,
        # 一次性 RTI 拿不到 restored frame/task stack(实跑 0x12cad2 停点根因)。
        m = machine(entry=True, banks=True)
        cpu = FakeCpu(VectorMem(), core_id=0)
        m.cores = {0: cpu}
        m.deliver_src_sync_irq(0)
        self.assertEqual((cpu.usp, cpu.ssp), (0x1237b8, 0x123840))
        cpu.sp -= 12                               # handler frame12_push
        m.note_sp_write(cpu)
        self.assertEqual((cpu.usp, cpu.ssp), (0x1237b8, 0x123840),
                         'handler SP pushes must not clear the entry bank fixtures')
        cpu.sp += 12                               # handler frame12_pop restores SP
        m.note_sp_write(cpu)
        self.assertEqual((cpu.usp, cpu.ssp), (0x1237b8, 0x123840))

    def test_unwritten_vector_slot_blocks_atomically(self):
        m = machine(entry=True)
        cpu = FakeCpu(UnwrittenMem())
        m.cores = {0: cpu}
        with self.assertRaises(Blocked) as caught:
            m.deliver_src_sync_irq(0)
        self.assertEqual(caught.exception.details['kind'], 'vector_slot_uninitialized')
        self.assertEqual(caught.exception.details['address'], '0x19ff64')
        self.assertEqual((cpu.pc, cpu.reti, cpu.steps), (0x606955c, 0x12caea, 7),
                         'failed entry mutated CPU state')
        self.assertEqual(m.src_sync_irq_deliveries, [], 'failed entry recorded a delivery')

    def test_one_shot_without_repeat_and_self_nesting_rejection(self):
        m = machine(entry=True)
        cpu = FakeCpu(VectorMem())
        m.cores = {0: cpu}
        m.deliver_src_sync_irq(0)
        with self.assertRaises(ValueError):
            m.deliver_src_sync_irq(0)

    def test_rti_returns_to_the_interrupted_task(self):
        m = machine(entry=True, banks=True, rti=True)
        cpu = FakeCpu(VectorMem(), pc=0x6067d30, core_id=0)
        m.cores = {0: cpu}
        m.deliver_src_sync_irq(0)
        cpu.pc = TAIL
        cpu.usp = 0x6067d30
        cpu.reti = 0x6067d30
        m.return_src_sync_irq(cpu)
        self.assertEqual((cpu.pc, cpu.sp, cpu.active_context, cpu.steps),
                         (0x6067d30, 0x6067d30, None, 9))

    def test_rti_requires_the_exact_handler_tail(self):
        m = machine(entry=True, rti=True)
        cpu = FakeCpu(VectorMem())
        m.cores = {0: cpu}
        m.deliver_src_sync_irq(0)
        cpu.pc = TAIL + 2
        with self.assertRaises(ValueError):
            m.return_src_sync_irq(cpu)
        cpu.pc = TAIL
        cpu.active_context = None
        with self.assertRaises(ValueError):
            m.return_src_sync_irq(cpu)

    def test_machine_report_carries_the_src_sync_projection_face(self):
        m = machine(entry=True, rti=True)
        report = m.report()
        face = report['src_sync_irq_entry_projection']
        self.assertEqual(face['deliveries'], [])
        self.assertIn('0x19ff64', face['vector_slot_formula'])
        self.assertIn('0x12cad2', face['rti_tail'])
        m2 = machine(entry=False)
        self.assertNotIn('src_sync_irq_entry_projection', m2.report())


if __name__ == '__main__':
    unittest.main()
