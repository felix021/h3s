#!/usr/bin/env python3
"""D2-b mic_effect 辅听注入闭环场景 runner(INJ-04,唯一还没跑注入闭环的场景)。

照 esco 首场景(d2_audio_scene.py D2AI/D2AJ/D2ANC)先例,把蓝图 §3.2 场景 B 的
开链+注入+消费三段证据链落地。唯一合成输入=样本字节;开链/配置/IRQ68 分发器/
output-handler/jlstream 提交面全部真实固件执行。

  b1 开链两步(蓝图 §3.2 B1):
    ①直调 descriptor walk 0x6074938(0x9c2d, 0xD06D)(CP d2c_effect_pipeline 先例
      换 (scene,plug) 参)→建出 0x9c2d 图管道(唯一 ADC 源节点 variant 3,AR §1.8③
      图勘误);stream=[r0],源节点=[stream+40],priv=[源节点+12](jlstream_start
      0x6074ffe 的 ioctl 分发同款装载:r0=[r7+12]=priv,§4.4 反汇编钉死)。
    ②直调源节点 ioctl 0x603293e(priv, 0x40001, 0)(=jlstream_start 内部对流激活
      发的 START 命令;绕过的 jlstream_start 外围状态面——0x20002 首查+os_time_dly
      重试环(0x607503c..0x607504a)在冻结世界不可跑——显式标注):
      zalloc(48) nodecfg 挂 [priv+84]→0x6075686(0xd06d,variant,nodecfg+8,priv)
      取名 "MicEff"+40B blob(event 18 主路径 vs 0x6032b40 兜底,注册者观测 R2)
      →en_map=0x1 只开 ch0(0x606b4d0/0x606de92 档 0x0a/0x606c3aa)→SR 0x606dee0
      (模式字节三态,观测 R1)→set_buffer 0x606dee4(buf,512,2)=LEN 512/PNS 256
      (buf_num=2 与 esco 3 不同——LEN=uextra(buf_num×len,1,16)=buf_num×256,布局差异
      即断言点)→挂 [priv+76]=0x6032e80 进
      hdl+0/+4 链→0x606df66 启动→0x6072712(ch_map)。
  b2 单发交付:produce_block(1ch 样本进当前窗口+置 pending)→资格判(真实 ICFG
      nibble+IPMASK)→machine.deliver_audio_dma_irq(0)→受控步进真实分发器
      0x602f406→0x6072628→output-handler 链→RTI 回 park 停点。
  b3 消费闭环:连续交付 k=2..11——output handler 0x6032e80 前 10 帧丢弃门
      b[priv+27]++≤9(0x6032e86/0x6032e8e/0x6032e92 计数早退),第 11 次调用起
      真路径(0x6032e94):0x6074080 帧分配→(模式字节==1 才走 0x602f5fa 门+
      0x6032da6 解交织——本场景模式字节=zalloc 默认 0,两锚如实不执行,R3 维持
      条件性)→memcpy 交织帧进 frame+32(0x6032ec6)→0x6074100 提交面。
  b4 连续性:k=12..16 真路径续+两整圈+块号 k 单调(旧帧重复消费即 FAIL)+负例
      ①静音块(全零样本→交付/消费照常,样本字节=0)。
  诚实边界:辅听检测/算法结果不在验收(d1-acceptance §6.2 表);样本=显式合成
  fixture(synthetic-not-measured);DMA 完成时序=投影(b2/b4 带投影前缀)。

用法:python3 tools/d2_audio_mic_effect_scene.py [--capture] [--max-handler-steps N]
  默认 restore analysis/snapshots 的 park 快照(~秒级);--capture 全量重放重新捕获。
  证据 JSON 落 analysis/local-checks/d2mic/scene.json(git 忽略)。
  世界钉 park:gpcnt_num_value=None 覆盖(d2anc/callimpl 同款 park 停点
  29,601,125 @ 0x606955c,GPCNUM 路钉过)。
"""
from __future__ import annotations

import argparse
import json
import struct
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from probe_boot import Blocked                                             # noqa: E402
from sim_world_driver import SimWorldDriver                                # noqa: E402
from d2_audio_scene import (compose_block, read_bytes, read_u32, read_u16,  # noqa: E402
                            read_u8, chain_walk, AUDIO_ADC_HDL, QCY,
                            PARK_STOP_PC, VECTOR_SLOT, DISPATCHER,
                            DISPATCHER_PCS, HANDLER_PCS, BLOCK_FORMULA)

# ---- 固件地址/形状(全部预研/AR §1.8/§4.4 钉死+本路反汇编复核,不发明) ----
PIPELINE_PARSE = 0x6074938       # descriptor walk(scene_uuid, plug_uuid)→stream(0=失败)
SCENE_MIC_EFFECT = 0x9C2D        # mic_effect 图 uuid(stream.bin §0 @0x2d0,CFG-04)
PLUG_ADC = 0xD06D                # 源节点 plug uuid(0xd06d 三兄弟,AR §1.8②)
ADC_PLUG_IOCTL = 0x603293E       # (priv, cmd, arg)——tbh @0x6032960(0x20002..0x2000F)
START_CMD = 0x40001              # START 打开链(0x6032998 出口→0x6032ac8)
MIC_EFFECT_HANDLER = 0x6032E80   # output handler(前 10 帧门+模式 1 解交织)
# b1 开链锚(0x6032ac8..0x6032d4a,AR §1.8③+本路反汇编):
START_ENTRY = 0x6032AC8          # START 打开链入口(nodecfg zalloc 挂 [priv+84])
BLOB_FETCH = 0x6075686           # 取名 "MicEff"+blob(0x6032aee call)
ADC_MIC_OPEN = 0x606B4D0         # adc_mic_open(hdl+78|=bit/hdl+79++)
ADA_CON8_WRITE = 0x606DE92       # ADA_CON8 增益档(desc+1=0x0a)
ADA_CON47_WRITE = 0x606C3AA      # ADA_CON4..7 bit0(desc+2)
SR_WRITE = 0x606DEE0             # SR 写(模式字节三态观测)
SET_BUFFER = 0x606DEE4           # 五写 ADR/LEN/PNS/HWP=0/SRP=0(buf,512,2)
LINK_HANDLER = 0x606DF3A         # 挂链(hdl, node)——尾部插入 hdl+0/+4 环
ADC_START = 0x606DF66            # 启动(CON bit5+bit4 置位族)
CH_MAP_TAIL = 0x6072712          # ch_map 收尾(0x6074ffe/ioctl 共享)
# b3/b4 消费面锚(0x6032e80 全形状,§4.4+本路反汇编):
DROP_GATE_CHECK = 0x6032E86      # r0=b[priv+27](r7=priv+16,+11)
DROP_GATE_INC = 0x6032E8E        # 计数++(≤9 早退)
DROP_GATE_RETURN = 0x6032E92     # 丢弃门早退返回
REAL_PATH = 0x6032E94            # 真路径首站(r1=b[priv+28] 通道数)
FRAME_ALLOC_CALL = 0x6032E9A     # call 0x6074080 帧分配
MODE1_GATE_CALL = 0x6032EAC      # call 0x602f5fa 模式 1 门(仅模式字节==1)
DEINTERLEAVE_CALL = 0x6032EB4    # call 0x6032da6 解交织(仅模式 1 门==1)
MEMCPY_CALL = 0x6032EC8          # call 0x1fd74 memcpy(frame+32, data, ch×len)(0x6032ec6 是 r0=r8 前置)
SUBMIT_CALL = 0x6032EFC          # call 0x6074100 投递帧(下游提交面)

OPEN_CHAIN_PCS = (ADC_PLUG_IOCTL, START_ENTRY, BLOB_FETCH, ADC_MIC_OPEN,
                  ADA_CON8_WRITE, ADA_CON47_WRITE, SR_WRITE, SET_BUFFER,
                  LINK_HANDLER, ADC_START, CH_MAP_TAIL)
CONSUME_PCS = (MIC_EFFECT_HANDLER, DROP_GATE_CHECK, DROP_GATE_INC,
               DROP_GATE_RETURN, REAL_PATH, FRAME_ALLOC_CALL, MODE1_GATE_CALL,
               DEINTERLEAVE_CALL, MEMCPY_CALL, SUBMIT_CALL)
WATCH_PCS = DISPATCHER_PCS + HANDLER_PCS + OPEN_CHAIN_PCS + CONSUME_PCS


def run_scene(machine, hardware, cpu, max_handler_steps):
    driver = SimWorldDriver(machine, hardware, cpu)
    dma = hardware.jl_audio_adc_dma
    observations = {'banner': {'stop_pc': hex(cpu.pc), 'core': cpu.core_id,
                               'active_context': getattr(cpu, 'active_context', None),
                               'in_irq_context_banner': driver.in_irq_context,
                               'delivery_projection': bool(dma.delivery)}}

    # ---------------- world_prep 前置三查(INJ-03/蓝图 B0 同款) ----------------
    hdl_slot_value = read_u32(cpu, 0x126A90)
    esco_slot_value = read_u32(cpu, QCY + 620)
    vector_value = read_u32(cpu, VECTOR_SLOT)
    nibble_word = hardware.irq.config[68 // 8]
    nibble = (nibble_word >> ((68 % 8) * 4)) & 15
    observations['world_prep'] = {
        'hdl_slot_0x126A90': hex(hdl_slot_value),
        'esco_slot_QCY+620': hex(esco_slot_value),
        'vector_slot_0x19ff10': hex(vector_value),
        'irq68_icfg_nibble': hex(nibble),
        'chain_before': chain_walk(cpu, AUDIO_ADC_HDL),
        'hdl78_before': hex(read_u8(cpu, AUDIO_ADC_HDL + 78)),
        'hdl79_before': read_u8(cpu, AUDIO_ADC_HDL + 79),
        'check': {'hdl_slot_is_0x1277B4': hdl_slot_value == AUDIO_ADC_HDL,
                  'esco_slot_zero': esco_slot_value == 0,
                  'vector_is_dispatcher': vector_value == DISPATCHER,
                  'irq68_enabled_prio2': nibble == 5,
                  'chain_empty_before': not [n for n in chain_walk(cpu, AUDIO_ADC_HDL)],
                  'hdl79_zero_before': read_u8(cpu, AUDIO_ADC_HDL + 79) == 0}}
    if vector_value != DISPATCHER:
        raise Blocked('d2mic_vector_slot_not_dispatcher', value=hex(vector_value))
    if nibble != 5:
        raise Blocked('d2mic_irq68_nibble_unexpected', nibble=hex(nibble))

    # ---------------- b1:直调 descriptor walk 0x6074938(0x9c2d, 0xD06D) ----------------
    b1 = {}
    try:
        walk_r0, walk_steps, walk_hits = driver.direct_call(
            PIPELINE_PARSE, [SCENE_MIC_EFFECT, PLUG_ADC], max_steps=20_000_000,
            watch=(PIPELINE_PARSE,), label='b1:pipeline_parse(0x9c2d,0xD06D)')
        b1['walk'] = {'returned_stream': hex(walk_r0) if isinstance(walk_r0, int) else walk_r0,
                      'steps': walk_steps, 'watch_hits': walk_hits}
    except Exception as exc:
        b1['walk'] = {'wall': {'type': type(exc).__name__, 'detail': str(exc)[:1600]}}
        observations['b1_open_chain'] = b1
        return observations
    if not isinstance(walk_r0, int) or walk_r0 == 0:
        b1['walk']['check'] = False
        b1['wall'] = {'type': 'pipeline_parse_failed', 'stream': hex(walk_r0)}
        observations['b1_open_chain'] = b1
        return observations
    stream = walk_r0
    # 定位源节点/priv(反汇编钉死:0x6074938 建根节点 [stream+40]=根节点(0x1100);
    # 0x6076b7a 源 bind zalloc(48) 源上下文挂 [node+12],其 [ctx+4]=88B ADC plug priv
    # (create 0x6032924 返回);ioctl 0x603293e 的第一参=88B ADC priv):
    source_node = read_u32(cpu, stream + 40)
    priv_ctx = read_u32(cpu, source_node + 12)
    adc_priv = read_u32(cpu, priv_ctx + 4)
    priv = adc_priv                        # 后续 b1/b2/b3/b4 面统一用 ADC plug priv
    b1['stream_face'] = {
        'stream': hex(stream),
        'source_node': hex(source_node),
        'source_node_uuid': hex(read_u16(cpu, source_node + 2)),
        'source_node_subid': read_u8(cpu, source_node + 0),
        'source_node_adapter': hex(read_u32(cpu, source_node + 40)),
        'priv_ctx_48B': hex(priv_ctx),
        'adc_priv_88B': hex(adc_priv),
        'check': {'source_node_uuid_is_0x1100_root': read_u16(cpu, source_node + 2) == 0x1100,
                  'source_node_subid_3': read_u8(cpu, source_node + 0) == 3,
                  'priv_ctx_node_backref': read_u32(cpu, priv_ctx + 8) == source_node,
                  'adc_priv_in_vheap': 0x410000 <= adc_priv < 0x420000}}

    # ---------------- b1:直调 ioctl 0x603293e(priv, 0x40001, 0) START ----------------
    # 具名单字节/半字 fixture(唯一非样本合成输入,显式登记):h[priv+32]=缓冲样本数默认
    # 256——真固件由 jlstream_start 外围 fmt 协商(0x20006 SET_MODE/GET_FMT/SET_SR)写入,
    # 直调 ioctl 绕过该外围状态面(蓝图 §3.2 B1 已标注);开链 0x6032cb6 的 h[priv+32]==0
    # →256 默认只在 [QCY+3504] 共享大缓冲槽为空时才走,boot 已复用槽非零→跳过默认→
    # set_buffer len=0 导致 LEN/PNS 全零(运行观测根因,反汇编 0x6032c8c..0x6032cf6 钉死)。
    b1['buf_len_fixture'] = {
        'address': hex(priv + 32), 'bytes': '00 01', 'written': 256,
        'note': 'named synthetic-buf-len-fixture-halfword(非样本合成输入;真固件写点='
                'jlstream_start fmt 协商外围,直调绕过;值=开链自带默认 256,AR §1.8③)'}
    cpu.mem.write(priv + 32, struct.pack('<H', 256))
    try:
        ioctl_r0, ioctl_steps, ioctl_hits = driver.direct_call(
            ADC_PLUG_IOCTL, [priv, START_CMD, 0], max_steps=20_000_000,
            watch=OPEN_CHAIN_PCS, label='b1:ioctl_START(priv,0x40001)')
    except Exception as exc:
        b1['ioctl'] = {'wall': {'type': type(exc).__name__, 'detail': str(exc)[:1600]}}
        observations['b1_open_chain'] = b1
        return observations
    five = {hex(a): {'writes': e['writes'], 'last': hex(e['values'][-1]) if e['values'] else None}
            for a, e in dma.stores.items() if e['writes']}
    chain_after = chain_walk(cpu, AUDIO_ADC_HDL)
    con_word = hardware.read(0xfe0538) if hasattr(hardware, 'read') else None
    covered = {h['pc'] for h in ioctl_hits}
    b1['ioctl'] = {}
    b1['ioctl'].update({
        'returned': ioctl_r0, 'steps': ioctl_steps,
        'priv_face': {'mode_b24': read_u8(cpu, priv + 24),
                      'started_b26': read_u8(cpu, priv + 26),
                      'drop_counter_b27': read_u8(cpu, priv + 27),
                      'channel_count_b28': read_u8(cpu, priv + 28),
                      'sr_h30': hex(read_u16(cpu, priv + 30)),
                      'nodecfg_slot_84': hex(read_u32(cpu, priv + 84)),
                      'large_buf_slot_80': hex(read_u32(cpu, priv + 80))},
        'five_word_records': five,
        'hdl78_after': hex(read_u8(cpu, AUDIO_ADC_HDL + 78)),
        'hdl79_after': read_u8(cpu, AUDIO_ADC_HDL + 79),
        'chain_after': chain_after,
        'adc_con_after': hex(con_word) if con_word is not None else 'unavailable',
        'open_chain_pc_coverage': {'0x%08x' % pc: (hex(pc) in covered)
                                   for pc in (START_ENTRY, BLOB_FETCH, ADC_MIC_OPEN,
                                              ADA_CON8_WRITE, ADA_CON47_WRITE, SR_WRITE,
                                              SET_BUFFER, LINK_HANDLER, ADC_START)},
        'watch_hits': [{'pc': h['pc'], 'r': h['r']} for h in ioctl_hits],
    })
    b1['checks'] = {
        'set_buffer_five_writes': all(
            dma.stores[a]['writes'] >= 1 for a in (0xfe053c, 0xfe0540, 0xfe0544, 0xfe0548, 0xfe054c)),
        'hwp_srp_zero': dma.stores[0xfe0548]['values'][-1] == 0
                        and dma.stores[0xfe054c]['values'][-1] == 0,
        'len_512_pns_256_bufnum2': (dma.stores[0xfe0540]['values'][-1] == 512
                                    and dma.stores[0xfe0544]['values'][-1] == 256),
        'one_channel_opened': read_u8(cpu, AUDIO_ADC_HDL + 79) == 1,
        'hdl78_bit0': read_u8(cpu, AUDIO_ADC_HDL + 78) & 0x1 == 0x1,
        'handler_linked_0x6032e80': any(n['handler'] == hex(MIC_EFFECT_HANDLER) for n in chain_after),
        'gates_bit5_bit4_set': con_word is not None and con_word & 0x30 == 0x30,
        'open_chain_anchors_covered': all(b1['ioctl']['open_chain_pc_coverage'][k]
                                          for k in b1['ioctl']['open_chain_pc_coverage']),
    }
    observations['b1_open_chain'] = b1
    if not all(b1['checks'].values()):
        b1['wall'] = {'type': 'b1_assertion_failed', 'checks': b1['checks']}
        return observations

    # ---------------- b2:produce_block + IRQ68 真实交付(单发 k=1) ----------------
    channels = read_u8(cpu, AUDIO_ADC_HDL + 79)      # 期望 1
    width_bool = read_u8(cpu, AUDIO_ADC_HDL + 90)
    sample_bytes = 2 if width_bool == 0 else 4
    pns = dma.stores[0xfe0544]['values'][-1]
    adr = dma.stores[0xfe053c]['values'][-1]
    length = dma.stores[0xfe0540]['values'][-1]
    block1 = compose_block(pns, channels, sample_bytes, block_index=1)
    produced = dma.produce_block(cpu.mem, block1, channels=channels, sample_bytes=sample_bytes)
    gate = machine.icfg_demands().get(0)
    gate_used = gate if gate is not None else (cpu.icfg if getattr(cpu, 'icfg_known', False) else 0x300)
    eligible = hardware.irq.eligible(68, gate_used)
    b2 = {'produce_block': produced, 'block_formula': BLOCK_FORMULA,
          'ring': {'ADR': hex(adr), 'LEN_samples': length, 'PNS': pns,
                   'channels': channels, 'sample_bytes': sample_bytes,
                   'blocks_per_lap': length // pns},
          'eligible_at_delivery': eligible, 'data_pointer_expected': hex(adr)}
    if not eligible:
        b2['wall'] = {'type': 'irq68_not_eligible', 'gate': hex(gate_used)}
        observations['b2_single_delivery'] = b2
        return observations
    try:
        machine.deliver_audio_dma_irq(0)
    except (Blocked, ValueError) as exc:
        b2['wall'] = {'type': type(exc).__name__, 'detail': str(exc)[:800]}
        observations['b2_single_delivery'] = b2
        return observations
    entry_event = dict(machine.audio_irq_deliveries[-1])
    steps = 0
    hits = []
    wall = None
    while steps < max_handler_steps:
        if cpu.pc in WATCH_PCS:
            hits.append({'pc': hex(cpu.pc),
                         'r': [hex(v) if isinstance(v, int) else str(v) for v in cpu.R[:5]]})
        try:
            before = cpu.pc
            insn, _raw, _size = cpu.decode(cpu.pc)
            cpu.step()
            steps += 1
            machine.after_step(cpu, before)
        except Blocked as exc:
            wall = {'at_step': steps, 'stop_pc': hex(cpu.pc), 'stop': exc.details}
            break
        if (cpu.pc == PARK_STOP_PC and
                getattr(cpu, 'active_context', None) == 'timer0_irq_handler'):
            break
    covered = {h['pc'] for h in hits}
    call_sites = [h for h in hits if h['pc'] == hex(0x60726e4)]
    sample_readback = None
    if call_sites:
        data_ptr = int(call_sites[0]['r'][1], 16)
        readback = read_bytes(cpu, data_ptr, len(block1))
        sample_readback = {'data_pointer': hex(data_ptr), 'length': len(block1),
                           'byte_equal': readback == block1, 'first16': list(block1[:16]),
                           'readback_first16': list(readback[:16])}
    b2.update({
        'delivery_entry_event': entry_event,
        'handler_window_steps': steps,
        'dispatcher_pc_coverage': {'0x%08x' % pc: (hex(pc) in covered) for pc in DISPATCHER_PCS},
        'audio_handler_entry_hits': sum(1 for h in hits if h['pc'] == hex(0x6072628)),
        'mic_effect_handler_entries': sum(1 for h in hits if h['pc'] == hex(MIC_EFFECT_HANDLER)),
        'drop_gate_hits': {'check': hex(DROP_GATE_CHECK) in covered,
                           'inc': hex(DROP_GATE_INC) in covered,
                           'return': hex(DROP_GATE_RETURN) in covered},
        'real_path_absent_block1': hex(REAL_PATH) not in covered,
        'drop_counter_after_k1': read_u8(cpu, priv + 27),
        'chain_call_sites': call_sites,
        'sample_readback': sample_readback,
        'wall': wall,
        'post_state': {
            'pending_cleared': not dma.pending,
            'in_flight_cleared': not dma.delivery_in_flight,
            'cpu_back_at_park_stop': cpu.pc == PARK_STOP_PC,
            'outer_context_restored': getattr(cpu, 'active_context', None) == 'timer0_irq_handler',
        },
    })
    b2['checks'] = {
        'nested_entry_justified': bool(entry_event.get('nested')),
        'dispatcher_adcsite_covered': all(b2['dispatcher_pc_coverage'][k]
                                          for k in b2['dispatcher_pc_coverage']),
        'audio_handler_called': b2['audio_handler_entry_hits'] >= 1,
        'mic_effect_handler_called': b2['mic_effect_handler_entries'] >= 1,
        'first_delivery_dropped': (hex(DROP_GATE_RETURN) in covered
                                   and hex(REAL_PATH) not in covered),
        'drop_counter_advanced_to_1': read_u8(cpu, priv + 27) == 1,
        'samples_byte_equal': bool(sample_readback and sample_readback['byte_equal']),
        'pending_cleared_by_real_w1c': b2['post_state']['pending_cleared'],
        'rti_returned_to_park_context': (cpu.pc == PARK_STOP_PC
                                         and getattr(cpu, 'active_context', None) == 'timer0_irq_handler'),
    }
    observations['b2_single_delivery'] = b2
    if not all(b2['checks'].values()):
        b2['wall'] = {'type': 'b2_assertion_failed', 'checks': b2['checks']}
        return observations

    # ---------------- b3+b4:连续交付→丢弃门→真消费路径→两整圈 ----------------
    def deliver_and_collect(block_index, muted=False):
        block = bytes(pns * channels * sample_bytes) if muted \
            else compose_block(pns, channels, sample_bytes, block_index=block_index)
        ring_pos = ((block_index - 1) * pns) % length
        expected_window = adr + ring_pos * channels * sample_bytes
        produced = dma.produce_block(cpu.mem, block, channels=channels,
                                     sample_bytes=sample_bytes)
        window_pre = read_bytes(cpu, expected_window, len(block))
        counter_before = read_u8(cpu, priv + 27)
        gate = machine.icfg_demands().get(0)
        gate_used = gate if gate is not None else (cpu.icfg if getattr(cpu, 'icfg_known', False) else 0x300)
        eligible = hardware.irq.eligible(68, gate_used)
        rec = {'k': block_index, 'muted': muted,
               'ring_pos_samples': ring_pos, 'ring_lap': (block_index - 1) * pns // length,
               'produced_window': produced['window'], 'expected_window': hex(expected_window),
               'window_matches_ring': produced['window'] == hex(expected_window),
               'window_pre_equal': window_pre == block,
               'counter_before': counter_before, 'eligible': eligible}
        if not eligible:
            rec['wall'] = {'type': 'irq68_not_eligible', 'gate': hex(gate_used)}
            return rec, window_pre, block
        machine.deliver_audio_dma_irq(0)
        steps = 0
        hits = []
        wall = None
        frame_dest = None
        memcpy_len = None
        frame_bytes = None
        while steps < max_handler_steps:
            pc = cpu.pc
            if pc in WATCH_PCS:
                r = [hex(v) if isinstance(v, int) else str(v) for v in cpu.R[:7]]
                hits.append({'pc': hex(pc), 'r': r})
                if pc == MEMCPY_CALL:
                    # r0=frame+32(memcpy dest)、r2=ch×len(memcpy 字节数)
                    frame_dest = int(r[0], 16) if r[0].startswith('0x') else None
                    memcpy_len = int(r[2], 16) if r[2].startswith('0x') else None
                elif pc == SUBMIT_CALL and frame_dest is not None and memcpy_len is not None:
                    # 提交面 0x6032efc 之前 memcpy 已完成、帧对象仍存活(尚未投递):
                    try:
                        frame_bytes = read_bytes(cpu, frame_dest, memcpy_len)
                    except Blocked:
                        frame_bytes = None
            try:
                before = cpu.pc
                insn, _raw, _size = cpu.decode(cpu.pc)
                cpu.step()
                steps += 1
                machine.after_step(cpu, before)
            except Blocked as exc:
                wall = {'at_step': steps, 'stop_pc': hex(cpu.pc), 'stop': exc.details,
                        'regs': {'r': [hex(v) if isinstance(v, int) else str(v)
                                       for v in cpu.R[:16]],
                                 'sp': hex(cpu.sp) if cpu.sp_known else 'unknown',
                                 'active_context': getattr(cpu, 'active_context', None)}}
                break
            if (cpu.pc == PARK_STOP_PC and
                    getattr(cpu, 'active_context', None) == 'timer0_irq_handler'):
                break
        call_sites = [h for h in hits if h['pc'] == hex(0x60726e4)]
        data_ptr = int(call_sites[0]['r'][1], 16) if call_sites else None
        window_post = read_bytes(cpu, expected_window, len(block))
        covered = {h['pc'] for h in hits}
        rec.update({
            'window_steps': steps, 'wall': wall,
            'chain_call_args': call_sites[0] if call_sites else None,
            'data_ptr': hex(data_ptr) if data_ptr is not None else None,
            'data_ptr_matches_ring': data_ptr == expected_window,
            'mic_effect_entries': sum(1 for h in hits if h['pc'] == hex(MIC_EFFECT_HANDLER)),
            'drop_gate_check_hits': sum(1 for h in hits if h['pc'] == hex(DROP_GATE_CHECK)),
            'drop_gate_return_hits': sum(1 for h in hits if h['pc'] == hex(DROP_GATE_RETURN)),
            'real_path_hits': sum(1 for h in hits if h['pc'] == hex(REAL_PATH)),
            'frame_alloc_hits': sum(1 for h in hits if h['pc'] == hex(FRAME_ALLOC_CALL)),
            'memcpy_hits': sum(1 for h in hits if h['pc'] == hex(MEMCPY_CALL)),
            'submit_hits': sum(1 for h in hits if h['pc'] == hex(SUBMIT_CALL)),
            'mode1_gate_absent': hex(MODE1_GATE_CALL) not in covered,
            'deinterleave_absent': hex(DEINTERLEAVE_CALL) not in covered,
            'frame_dest': hex(frame_dest) if frame_dest is not None else None,
            'frame_plus_32_bytes': list(frame_bytes[:16]) if frame_bytes is not None else None,
            'frame_plus_32_equal': (frame_bytes == block) if frame_bytes is not None else None,
            'window_post_equal': window_post == block,
            'counter_after': read_u8(cpu, priv + 27),
            'rti_ok': (cpu.pc == PARK_STOP_PC
                       and getattr(cpu, 'active_context', None) == 'timer0_irq_handler'),
        })
        return rec, window_post, block

    b3_records = []
    for k in range(2, 12):                       # b3:k=2..11(调 #2..#11;计数器 1→10,k=11 真路径)
        rec, _window_post, _block = deliver_and_collect(k)
        b3_records.append(rec)
    observations['b3_deliveries'] = b3_records

    b4_records = []
    for k in range(12, 17):                      # b4:k=12..16(真路径续+第 4 圈)
        rec, _window_post, _block = deliver_and_collect(k)
        b4_records.append(rec)
    observations['b4_deliveries'] = b4_records

    # 负例①静音块(全零样本→交付/消费照常,样本字节=0):
    muted_rec, muted_window_post, muted_block = deliver_and_collect(17, muted=True)
    muted_rec.update({'window_post_all_zero': muted_window_post == bytes(len(muted_block))})
    observations['negative_mute'] = muted_rec

    # ---------------- b4 聚合:两整圈/丢弃门计数/k 单调/RTI/消费面 ----------------
    all_records = b3_records + b4_records + [muted_rec]
    counter_trace = [1] + [r['counter_after'] for r in all_records]   # b2 后=1
    real_ks = [r['k'] for r in all_records if r.get('real_path_hits')]
    consumed = [{'k': 1, 'path': 'drop_gate', 'counter': 1,
                 'content_ok': b2['checks']['samples_byte_equal']}]
    for r in all_records:
        consumed.append({'k': r['k'],
                         'path': 'real_path' if r.get('real_path_hits') else 'drop_gate',
                         'counter': r['counter_after'],
                         'content_ok': (r.get('frame_plus_32_equal') if r.get('real_path_hits')
                                        else r.get('window_pre_equal', r.get('window_post_equal')))})
    b4_aggregate = {
        'deliveries_total': 1 + len(all_records),
        'counter_trace': counter_trace,
        'counter_monotonic_0_to_10_then_flat': (counter_trace[:10] == list(range(1, 11))
                                                and all(c == 10 for c in counter_trace[10:])),
        'real_path_starts_k11': real_ks and real_ks[0] == 11,
        'k_monotonic': [c['k'] for c in consumed] == sorted(c['k'] for c in consumed),
        'all_content_evidence_ok': all(c['content_ok'] for c in consumed),
        'ring_wraps_total': dma.hwp // length,
        'two_full_laps': dma.hwp // length >= 2,
        'data_ptrs_all_match_ring': all(r.get('data_ptr_matches_ring')
                                        for r in b3_records + b4_records),
        'all_rti_ok': all(r.get('rti_ok') for r in b3_records + b4_records),
        'mode1_deinterleave_not_executed': all(r.get('mode1_gate_absent') and r.get('deinterleave_absent')
                                               for r in all_records),
        'handler_calls_total': 1 + sum(r.get('mic_effect_entries') or 0 for r in all_records),
        'hwp_samples_monotonic': dma.hwp,
        'blocks_produced': dma.blocks_produced,
        'consumed_sequence': consumed,
    }
    observations['b4_aggregate'] = b4_aggregate
    return observations


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--capture', action='store_true', help='全量重放重新捕获快照(~14min)')
    parser.add_argument('--max-handler-steps', type=int, default=400_000)
    parser.add_argument('--out', default='analysis/local-checks/d2mic/scene.json')
    args = parser.parse_args()

    from sim_world_boot import build_world
    t0 = time.time()

    def serve_loop(machine, hardware, cpu, stop=None):
        obs = run_scene(machine, hardware, cpu, args.max_handler_steps)
        # 先落盘场景观察面:build_world 返回前 probe_boot 会调 hardware.report(),
        # 该面在 LEN 末写为 0 的边角世界会抛 ZeroDivisionError 吞掉 result——
        # 场景证据不能随它丢(与 d2_audio_scene/call_scene 同构的先落盘纪律)。
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out + '.partial').write_text(json.dumps({'scene': obs}, indent=1, default=str),
                                               encoding='utf-8')
        return obs

    result = build_world(serve_loop, capture=args.capture,
                         rom_path='/home/felix021/tmp/QCY_H3S_BR28_ROM_000000-020000.bin',
                         firmware_path='firmware/QCY_H3S_7018F_1T2_release_note_V1.1.8_011.ufw',
                         snapshot_dir='analysis/snapshots',
                         flag_overrides={'gpcnt_num_value': None})
    payload = {'elapsed_s': round(time.time() - t0, 1),
               'stop': result.get('stop'),
               'completed_steps': result.get('completed_steps'),
               'scene': {k: v for k, v in result.items()
                         if k in ('banner', 'world_prep', 'b1_open_chain',
                                  'b2_single_delivery', 'b3_deliveries',
                                  'b4_deliveries', 'negative_mute', 'b4_aggregate')}}
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=1, default=str), encoding='utf-8')

    b1 = payload['scene'].get('b1_open_chain', {})
    b2 = payload['scene'].get('b2_single_delivery', {})
    b3 = payload['scene'].get('b3_deliveries', [])
    b4 = payload['scene'].get('b4_deliveries', [])
    mute = payload['scene'].get('negative_mute', {})

    def series(records, key):
        return [r.get(key) for r in records]

    print(json.dumps({'elapsed_s': payload['elapsed_s'],
                      'b1_walk': b1.get('walk'),
                      'b1_stream_face': b1.get('stream_face'),
                      'b1_ioctl_priv': (b1.get('ioctl') or {}).get('priv_face'),
                      'b1_checks': b1.get('checks'),
                      'b1_open_pc': (b1.get('ioctl') or {}).get('open_chain_pc_coverage'),
                      'b1_wall': b1.get('wall'),
                      'b2_checks': b2.get('checks'),
                      'b2_wall': b2.get('wall'),
                      'b3_series': {'k': series(b3, 'k'),
                                    'counter_before': series(b3, 'counter_before'),
                                    'counter_after': series(b3, 'counter_after'),
                                    'drop_gate_return': series(b3, 'drop_gate_return_hits'),
                                    'real_path': series(b3, 'real_path_hits'),
                                    'frame_alloc': series(b3, 'frame_alloc_hits'),
                                    'memcpy': series(b3, 'memcpy_hits'),
                                    'submit': series(b3, 'submit_hits'),
                                    'frame_plus_32_equal': series(b3, 'frame_plus_32_equal'),
                                    'data_ptr_matches_ring': series(b3, 'data_ptr_matches_ring'),
                                    'rti_ok': series(b3, 'rti_ok'),
                                    'walls': [r.get('wall') for r in b3 if r.get('wall')]},
                      'b4_series': {'k': series(b4, 'k'),
                                    'real_path': series(b4, 'real_path_hits'),
                                    'frame_plus_32_equal': series(b4, 'frame_plus_32_equal'),
                                    'data_ptr_matches_ring': series(b4, 'data_ptr_matches_ring'),
                                    'rti_ok': series(b4, 'rti_ok'),
                                    'walls': [r.get('wall') for r in b4 if r.get('wall')]},
                      'negative_mute': {k: mute.get(k) for k in
                                        ('k', 'mic_effect_entries', 'real_path_hits',
                                         'frame_plus_32_equal', 'window_post_all_zero', 'wall')},
                      'b4_aggregate': payload['scene'].get('b4_aggregate'),
                      'evidence': str(out)}, ensure_ascii=False, indent=1))
    return 0


if __name__ == '__main__':
    sys.exit(main())
