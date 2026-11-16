#!/usr/bin/env python3
"""Unit tests for the opt-in audio ADC DMA delivery projection (route D2AI).

Mirrors tools/test_timer0_irq.py: model-level pending/readback/W1C/produce_block
semantics plus machine-level minimal entry/RTI for the IRQ68 dispatcher
0x602f406 (vector slot 0x19ff10, firmware-registered at boot 0x602ec28).
Design source: docs/d2-audio-input-pre-study.md §4/§5/§7 (route D2AUD).
"""
import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parent))
from br28_machine import DualCoreMachine
from br28_model import (BR28Model, JL_AUDIO_ADC_CON, JL_AUDIO_ADC_DMA_WORDS,
                        JL_AUDIO_ADC_SRN, IRQ_CONFIG)
from probe_boot import Blocked, IrqDeliveryRequest

HANDLER=0x602f406   # IRQ68 dispatcher (vector slot content)
SLOT=0x19ff10       # 0x19fe00+68*4
TAIL=0x602f452      # dispatcher rti (0x602f44e pop.srl restores the frame first)
# The real park-world ICFG nibbles: IRQ68 nibble 5 (prio2|enable) in config[8],
# IRQ4 (Timer0) nibble 3 (prio1|enable) in config[0] — config[0]=0x33b7d has
# nibble 3 at bits16-19 (probe_irq_entry route-BE runtime word 0x33b55 family).
IRQ68_NIBBLE_WORD=(5<<16)
IRQ4_NIBBLE_WORD=(3<<16)


class FakeCpu:
    def __init__(self,mem,pc=0x606955c,core_id=0):
        self.mem=mem;self.pc=pc;self.reti=0x12caea;self.steps=7;self.core_id=core_id
        self.flags=0
        self.sp_known=True;self.sp=0x1237b8;self.usp=0x4013f0;self.ssp=0x123840
        self.last_explicit_ssp=0x123840
        self.bootstrap_context=None;self.after_then=None;self.repeat=None
        self.active_context=None


class VectorMem:
    def read(self,address,length):
        assert (address,length)==(SLOT,4)
        return struct.pack('<I',HANDLER)

    def write(self,address,data):
        raise AssertionError('the delivery entry must not write through the vector mem')


class RamMem:
    """Tiny byte-addressed RAM double for produce_block window writes."""
    def __init__(self):
        self.writes=[]

    def write(self,address,data):
        self.writes.append((address,bytes(data)))

    def read(self,address,length):
        raise AssertionError('not used in these tests')


class UnwrittenMem:
    def read(self,address,length):raise Blocked('uninitialized_memory',address=hex(address))


def armed_model(**extra):
    # Park-stack shape: wl_aud -> ADC reset records -> five-word records with
    # the delivery projection; IRQ68 nibble enabled prio2, ipmask 0.
    return BR28Model(jl_wl_aud_write_records=True,jl_audio_adc_reset_write_records=True,
                     jl_audio_adc_dma_writes=True,audio_adc_dma_delivery=True,
                     initial_irq_config=[IRQ4_NIBBLE_WORD]+[0]*7+[IRQ68_NIBBLE_WORD]+[0]*23,
                     initial_ipmask=0,**extra)


def five_writes(h):
    # The real set_buffer shape (route JLA): ADR/LEN/PNS/HWP=0/SRP=0.
    h.write(JL_AUDIO_ADC_CON,0)
    for address,value in zip(JL_AUDIO_ADC_DMA_WORDS,(0x419264,1536,256,0,0)):
        h.write(address,value)


class AudioDmaModelTests(unittest.TestCase):
    def test_delivery_requires_its_write_side_predecessors(self):
        with self.assertRaises(ValueError):
            BR28Model(audio_adc_dma_delivery=True)
        with self.assertRaises(ValueError):
            BR28Model(jl_audio_adc_dma_writes=True,audio_adc_dma_delivery=True)
        with self.assertRaises(ValueError):
            BR28Model(jl_wl_aud_write_records=True,jl_audio_adc_reset_write_records=True,
                      jl_audio_adc_dma_writes=True,audio_adc_dma_delivery_requests=True)

    def test_default_off_keeps_the_strict_read_stop(self):
        h=BR28Model(jl_wl_aud_write_records=True,jl_audio_adc_reset_write_records=True,
                    jl_audio_adc_dma_writes=True)
        h.write(0xfe053c,0x419264)
        with self.assertRaisesRegex(Blocked,'jl_audio_adc_dma_read_not_modeled'):h.read(0xfe053c)
        # SRN stays unmapped with the projection off.
        with self.assertRaisesRegex(Blocked,'uninitialized_peripheral'):h.read(JL_AUDIO_ADC_SRN)

    def test_delivery_serves_reads_and_the_srn_word(self):
        h=armed_model();five_writes(h)
        self.assertEqual(h.read(0xfe053c),0x419264)      # ADR last write
        self.assertEqual(h.read(0xfe0540),1536)          # LEN
        self.assertEqual(h.read(0xfe0544),256)           # PNS
        self.assertEqual(h.read(0xfe054c),0)             # SRP
        self.assertEqual(h.read(JL_AUDIO_ADC_SRN),0)     # SRN synthesized initial 0
        dma=h.jl_audio_adc_dma
        self.assertEqual((dma.synthesized_reads,dma.reads_served),(1,5))
        report=h.report()['jl_audio_adc_dma_write_records']
        self.assertTrue(report['delivery_projection']['enabled'])

    def test_pending_overlay_and_w1c_round_trip(self):
        h=armed_model();five_writes(h)
        h.write(JL_AUDIO_ADC_CON,0x33)                    # SR bits etc.
        self.assertEqual(h.read(JL_AUDIO_ADC_CON)&0x80,0)
        dma=h.jl_audio_adc_dma
        dma.pending=True;dma.delivery_in_flight=True
        self.assertTrue(h.read(JL_AUDIO_ADC_CON)&0x80)    # bit7 overlays when pending
        # The real dispatcher ack shape: rmw [0xfe0538] |= 0x40 — the generic
        # MMIO path reads the projected word and writes the composed value back.
        value=h.read(JL_AUDIO_ADC_CON)|0x40
        h.write(JL_AUDIO_ADC_CON,value)
        self.assertFalse(dma.pending)
        self.assertFalse(dma.delivery_in_flight)
        self.assertEqual(dma.con_w1c_acks,1)
        self.assertFalse(h.read(JL_AUDIO_ADC_CON)&0x80)
        # bit7 written by firmware never sticks (hardware-owned bit, read-side only).
        h.write(JL_AUDIO_ADC_CON,0x33|0x80)
        self.assertFalse(h.read(JL_AUDIO_ADC_CON)&0x80)

    def test_hwp_read_follows_software_reset_and_model_advance(self):
        h=armed_model();five_writes(h)
        dma=h.jl_audio_adc_dma
        self.assertEqual(h.read(0xfe0548),0)              # software reset wrote 0
        dma.produce_block(RamMem(),bytes(256*2*2),channels=2,sample_bytes=2)
        self.assertEqual(h.read(0xfe0548),256)            # advanced by PNS samples

    def test_produce_block_writes_the_current_window(self):
        h=armed_model();five_writes(h)
        mem=RamMem()
        dma=h.jl_audio_adc_dma
        block=bytes(range(256))*4                          # 1024B = PNS*ch*ssize
        info=dma.produce_block(mem,block,channels=2,sample_bytes=2)
        # MMU 逐页分块:0x419264 页内余量 28B → 28+7×128+100 共 9 片,重组后
        # 与块逐字节相等、地址连续。
        reassembled=b''.join(data for _address,data in mem.writes)
        self.assertEqual(len(mem.writes),9)
        self.assertEqual(reassembled,block)
        self.assertEqual(mem.writes[0][0],0x419264)
        for (a1,d1),(a2,_d2) in zip(mem.writes,mem.writes[1:]):
            self.assertEqual(a2,a1+len(d1),'chunks must be address-contiguous')
        self.assertEqual((info['window'],info['block_bytes'],info['pns_samples']),
                         ('0x419264',1024,256))
        self.assertTrue(dma.pending);self.assertEqual(dma.blocks_produced,1)
        # Second block lands at ADR+256*2*2 with HWP advanced.
        dma.pending=False                                   # simulate the real W1C
        mem.writes.clear()
        dma.produce_block(mem,block,channels=2,sample_bytes=2)
        self.assertEqual(mem.writes[0][0],0x419264+1024)

    def test_produce_block_shape_guards(self):
        h=armed_model();five_writes(h)
        dma=h.jl_audio_adc_dma
        with self.assertRaisesRegex(ValueError,'exactly PNS'):
            dma.produce_block(RamMem(),bytes(10),channels=2,sample_bytes=2)
        with self.assertRaisesRegex(Blocked,'jl_audio_adc_dma_word_unwritten'):
            fresh=armed_model();fresh.jl_audio_adc_dma.produce_block(RamMem(),bytes(1024),
                                                                     channels=2,sample_bytes=2)

    def test_produce_block_ring_wrap_aligned_folds_the_write_position(self):
        # 路 D2AJ(b4 绕回投影):PNS 整除 LEN 时块界与环尾对齐——第 7 块
        # 窗口折回 ADR+0,HWP 读回=环内位置(hwp%LEN)折返、单调累计继续,
        # 圈数=hwp//LEN 派生;写位置折返≠数据折返(旧窗字节被新块覆盖)。
        h=armed_model();five_writes(h)
        dma=h.jl_audio_adc_dma
        block=bytes(256*2*2)
        for _ in range(6):                               # LEN=1536: one full lap
            dma.produce_block(RamMem(),block,channels=2,sample_bytes=2)
            dma.pending=False
        self.assertEqual(h.read(0xfe0548),0)             # 1536%1536: folded back to ring start
        mem=RamMem()
        info=dma.produce_block(mem,block,channels=2,sample_bytes=2)   # block 7 wraps
        self.assertEqual(mem.writes[0][0],0x419264)      # window = ADR+0
        self.assertEqual((info['ring_pos_after'],info['ring_wraps']),(256,1))
        self.assertEqual(dma.hwp,7*256)                  # monotonic count keeps going
        self.assertEqual(h.read(0xfe0548),256)           # read-back is the ring position

    def test_produce_block_ring_straddle_stays_honestly_stopped(self):
        # PNS 不整除 LEN:跨环尾的块保持诚实停(硅上跨块搬运形状未测)。
        h=armed_model()
        h.write(JL_AUDIO_ADC_CON,0)
        for address,value in zip(JL_AUDIO_ADC_DMA_WORDS,(0x419264,300,256,0,0)):
            h.write(address,value)
        dma=h.jl_audio_adc_dma
        block=bytes(256*2*2)
        dma.produce_block(RamMem(),block,channels=2,sample_bytes=2)   # offset 0 fits
        with self.assertRaisesRegex(Blocked,'jl_audio_adc_dma_ring_straddle_not_modeled'):
            dma.produce_block(RamMem(),block,channels=2,sample_bytes=2)   # 256..512 > 300

    def test_srp_read_projection_tracks_the_freshest_completed_block(self):
        # 路 D2AJ(b3/b4):SRP=硬件域读指针,投影=最近完成块的环内起点
        # (hwp-PNS) mod LEN(读时刻 produce_block 已推进);未完成任何块=
        # 最后写值容器面(set_buffer 写 0)。app 唯一读点=audio handler
        # 0x607265a(data=ADR+SRP×ch×ssize)——b2 实测 block1 SRP=0 互证。
        h=armed_model();five_writes(h)
        dma=h.jl_audio_adc_dma
        self.assertEqual(h.read(0xfe054c),0)             # no block yet: set_buffer's 0
        block=bytes(256*2*2)
        dma.produce_block(RamMem(),block,channels=2,sample_bytes=2)
        self.assertEqual(h.read(0xfe054c),0)             # block 1 origin = ring pos 0
        dma.pending=False
        dma.produce_block(RamMem(),block,channels=2,sample_bytes=2)
        self.assertEqual(h.read(0xfe054c),256)           # block 2 origin = 256
        dma.pending=False
        for _ in range(4):                               # blocks 3..6 fill the lap
            dma.produce_block(RamMem(),block,channels=2,sample_bytes=2)
            dma.pending=False
        dma.produce_block(RamMem(),block,channels=2,sample_bytes=2)   # block 7 wrapped
        self.assertEqual(h.read(0xfe054c),0)             # block 7 origin = ring pos 0 again

    def test_advance_raises_the_delivery_request_once_per_episode(self):
        h=armed_model(audio_adc_dma_delivery_requests=True);five_writes(h)
        dma=h.jl_audio_adc_dma
        dma.pending=True
        with self.assertRaises(IrqDeliveryRequest) as caught:h.advance(0x300)
        self.assertEqual((caught.exception.details['core'],caught.exception.details['irq']),(0,68))
        self.assertTrue(dma.delivery_in_flight)
        h.advance(0x300)                                   # in flight: no re-raise
        h.write(JL_AUDIO_ADC_CON,h.read(JL_AUDIO_ADC_CON)|0x40)   # the real W1C ack
        dma.pending=True                                   # next episode
        with self.assertRaises(IrqDeliveryRequest):h.advance(0x300)

    def test_advance_strict_stop_without_the_request_switch(self):
        h=armed_model();five_writes(h)
        h.jl_audio_adc_dma.pending=True
        with self.assertRaisesRegex(Blocked,'interrupt_delivery_not_implemented'):h.advance(0x300)

    def test_advance_keeps_a_masked_or_disabled_episode_inert(self):
        # IRQ68 nibble cleared: pending stays set, nothing raises, nothing stops.
        h=armed_model(audio_adc_dma_delivery_requests=True)
        five_writes(h)
        h.write(IRQ_CONFIG+8*4,0)                          # clear the IRQ68 word
        h.jl_audio_adc_dma.pending=True
        h.advance(0x300)
        self.assertTrue(h.jl_audio_adc_dma.pending)
        self.assertFalse(h.jl_audio_adc_dma.delivery_in_flight)

    def test_advance_without_pending_never_requests(self):
        # 负例②(无 pending→零交付)的模型面:pending 未置位时扫描零行为。
        h=armed_model(audio_adc_dma_delivery_requests=True);five_writes(h)
        for _ in range(8):h.advance(0x300)
        self.assertFalse(h.jl_audio_adc_dma.pending)
        self.assertFalse(h.jl_audio_adc_dma.delivery_in_flight)


def machine(entry=True,banks=False,rti=False,repeat=False):
    return DualCoreMachine(audio_irq_entry=entry,audio_irq_entry_banks=banks,
                           audio_irq_rti=rti,repeat_irq_delivery=repeat)


class AudioIrqMachineTests(unittest.TestCase):
    def test_entry_is_opt_in(self):
        m=machine(entry=False);m.cores={0:FakeCpu(VectorMem())}
        with self.assertRaises(ValueError):m.deliver_audio_dma_irq(0)

    def test_delivery_reads_the_real_slot_and_keeps_pending(self):
        m=machine(entry=True,banks=True)
        cpu0=FakeCpu(VectorMem(),pc=0x606955c,core_id=0)
        m.cores={0:cpu0}
        m.deliver_audio_dma_irq(0)
        self.assertEqual((cpu0.pc,cpu0.reti,cpu0.steps),(HANDLER,0x606955c,8))
        self.assertEqual((cpu0.sp,cpu0.usp,cpu0.ssp),(0x123840,0x1237b8,0x123840))
        event=m.audio_irq_deliveries[0]
        self.assertEqual((event['core'],event['irq'],event['vector_address'],event['handler']),
                         (0,68,'0x19ff10','0x602f406'))
        self.assertFalse(event['delivery_clears_pending'])
        self.assertTrue(event['pending_cleared_by_handler_ack'])
        self.assertIn('0xfe0538',event['handler_ack_shape'])
        self.assertFalse(event['nested'])

    def test_unwritten_vector_slot_blocks_atomically(self):
        m=machine(entry=True);cpu=FakeCpu(UnwrittenMem());m.cores={0:cpu}
        with self.assertRaises(Blocked) as caught:m.deliver_audio_dma_irq(0)
        self.assertEqual(caught.exception.details['kind'],'vector_slot_uninitialized')
        self.assertEqual(caught.exception.details['address'],'0x19ff10')
        self.assertEqual((cpu.pc,cpu.reti,cpu.steps),(0x606955c,0x12caea,7),
                         'failed entry mutated CPU state')
        self.assertEqual(m.audio_irq_deliveries,[],'failed entry recorded a delivery')

    def test_nested_entry_on_timer0_requires_strictly_higher_priority(self):
        h=armed_model()
        m=machine(entry=True,rti=True)
        cpu=FakeCpu(VectorMem(),pc=0x606955c,core_id=0)
        cpu.active_context='timer0_irq_handler';cpu.reti=0x4013f0
        m.cores={0:cpu};m.hardware=h                       # the nesting check reads h.irq
        m.deliver_audio_dma_irq(0)
        self.assertEqual((cpu.pc,cpu.reti),(HANDLER,0x606955c))
        self.assertEqual(cpu.active_context,'audio_dma_irq_handler')
        outer=cpu.audio_irq_outer_context
        self.assertEqual((outer['active_context'],outer['reti'],outer['sp']),
                         ('timer0_irq_handler',0x4013f0,0x1237b8))
        event=m.audio_irq_deliveries[0]
        self.assertTrue(event['nested'])
        self.assertEqual(event['nesting_justification'],
                         {'in_flight_irq':4,'audio_nibble':'0x5','outer_nibble':'0x3'})

    def test_nested_entry_refused_when_priority_is_not_higher(self):
        # IRQ68 nibble downgraded to prio1 (== Timer0): the nesting must refuse.
        h=armed_model()
        h.write(IRQ_CONFIG+8*4,(1<<16))                    # prio1|enable
        m=machine(entry=True,rti=True)
        cpu=FakeCpu(VectorMem(),pc=0x606955c,core_id=0)
        cpu.active_context='timer0_irq_handler'
        m.cores={0:cpu};m.hardware=h
        with self.assertRaises(ValueError):m.deliver_audio_dma_irq(0)
        self.assertEqual(m.audio_irq_deliveries,[])

    def test_nested_entry_refused_for_other_in_flight_contexts(self):
        h=armed_model()
        for context in ('software_irq_handler','core_tick_handler'):
            m=machine(entry=True)
            cpu=FakeCpu(VectorMem(),pc=0x606955c,core_id=0)
            cpu.active_context=context
            m.cores={0:cpu};m.hardware=h
            with self.assertRaises(Blocked) as caught:m.deliver_audio_dma_irq(0)
            self.assertEqual(caught.exception.details['kind'],'audio_irq_nesting_not_modeled')
        # Self-nesting under repeat is refused by the repeat guard (ValueError).
        m=machine(entry=True,repeat=True)
        cpu=FakeCpu(VectorMem(),pc=0x606955c,core_id=0)
        cpu.active_context='audio_dma_irq_handler'
        m.cores={0:cpu};m.hardware=h
        with self.assertRaises(ValueError):m.deliver_audio_dma_irq(0)

    def test_one_shot_without_repeat_and_self_nesting_rejection(self):
        h=armed_model()
        m=machine(entry=True);cpu=FakeCpu(VectorMem());m.cores={0:cpu};m.hardware=h
        m.deliver_audio_dma_irq(0)
        with self.assertRaises(ValueError):m.deliver_audio_dma_irq(0)
        m2=machine(entry=True,repeat=True);cpu2=FakeCpu(VectorMem())
        m2.cores={0:cpu2};m2.hardware=h
        m2.deliver_audio_dma_irq(0)
        cpu2.active_context='audio_dma_irq_handler'
        with self.assertRaises(ValueError):m2.deliver_audio_dma_irq(0)

    def test_rti_returns_to_the_interrupted_task(self):
        h=armed_model()
        m=machine(entry=True,banks=True,rti=True)
        cpu=FakeCpu(VectorMem(),pc=0x6067d30,core_id=0);m.cores={0:cpu};m.hardware=h
        m.deliver_audio_dma_irq(0)
        cpu.pc=TAIL;cpu.active_context='audio_dma_irq_handler'
        cpu.usp=0x6067d30;cpu.reti=0x6067d30
        m.return_audio_dma_irq(cpu)
        self.assertEqual((cpu.pc,cpu.sp,cpu.active_context,cpu.steps),(0x6067d30,0x6067d30,None,9))

    def test_rti_restores_the_outer_timer0_context_when_nested(self):
        h=armed_model()
        m=machine(entry=True,rti=True)
        cpu=FakeCpu(VectorMem(),pc=0x606955c,core_id=0)
        cpu.active_context='timer0_irq_handler';cpu.reti=0x4013f0
        m.cores={0:cpu};m.hardware=h
        m.deliver_audio_dma_irq(0)
        # The real pop.srl at 0x602f44e restored the frame: reti=interrupted pc.
        cpu.pc=TAIL;cpu.reti=0x606955c
        m.return_audio_dma_irq(cpu)
        self.assertEqual((cpu.pc,cpu.active_context,cpu.reti),(0x606955c,'timer0_irq_handler',0x4013f0))
        self.assertFalse(hasattr(cpu,'audio_irq_outer_context'))
        self.assertEqual(cpu.sp,0x1237b8,'nested RTI keeps the kernel stack')

    def test_rti_requires_the_exact_dispatcher_tail(self):
        h=armed_model()
        m=machine(entry=True,rti=True);cpu=FakeCpu(VectorMem());m.cores={0:cpu};m.hardware=h
        m.deliver_audio_dma_irq(0)
        cpu.pc=0x602f450;cpu.active_context='audio_dma_irq_handler'
        with self.assertRaises(ValueError):m.return_audio_dma_irq(cpu)
        cpu.pc=TAIL;cpu.active_context=None
        with self.assertRaises(ValueError):m.return_audio_dma_irq(cpu)

    def test_machine_report_carries_the_audio_projection_face(self):
        m=machine(entry=True,rti=True)
        report=m.report()
        face=report['audio_irq_entry_projection']
        self.assertEqual(face['deliveries'],[])
        self.assertIn('0x19ff10',face['vector_slot_formula'])
        self.assertIn('0x602f452',face['rti_tail'])
        m2=machine(entry=False)
        self.assertNotIn('audio_irq_entry_projection',m2.report())


if __name__=='__main__':
    unittest.main()
