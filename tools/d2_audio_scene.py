#!/usr/bin/env python3
"""D2AI/D2AJ 场景 runner:park 停点世界上的 esco 首场景音频输入注入(b1-b4+负例)。

docs/d2-audio-input-pre-study.md §7 步 2 的执行载体(预研 §4 方案 a+直调开链,
按键先例同深度):唯一合成输入=样本字节;开链/配置/IRQ68 分发器/handler 链
全部真实固件执行。

  b1 开链:direct_call 0x60200b6(0x5)(SimWorldDriver 冻结世界直调,S 路先例)
      ——blob 解析→zalloc→逐通道 adc_mic_open→set_buffer 五写→挂链→启动。
  b2 单发交付:produce_block(交织样本进当前窗口+置 pending)→资格判(真实
      ICFG nibble+IPMASK)→machine.deliver_audio_dma_irq(0)(嵌套进入 park
      停点的 Timer0 事件循环 handler,优先级严格更高论证在机器侧)→受控步进
      真实分发器 0x602f406→0x6072628→output-handler 链→RTI 恢复外层上下文。
      步进循环=direct_call 同款冻结纪律(不调 hardware.advance,外设时间冻结)。
  负例③门关(D2AI):CON bit5 清→分发器读 pending 清位但不进 handler。
  b3 消费闭环(D2AJ):连续块 k=3..10 交付——esco handler 0x6033D5A 首层
      丢弃门 b[esco+0]≤7 计数早退(0x6033d8e→0x6033e4e),第 9 次调用起
      真路径(0x6033d90):解交织(ch0 原地压实+ch1 写 [esco+60] 缓冲)→
      逐通道 call 0x6033cb4(aec 前端入口,第二层门 [QCY+624])。
  b4 连续性(D2AJ):块 k=11..13+全序列聚合——SRN 重装次数=handler 调用数、
      data 指针按环位置推进(SRP 投影)、块号单调无旧帧重复消费、每轮 RTI。
  负例①静音(D2AJ):全零块交付→交付/解交织照常、配置/状态面不动。

用法:python3 tools/d2_audio_scene.py [--capture] [--max-handler-steps N]
  默认 restore analysis/snapshots 的 park 快照(~秒级);--capture 全量重放
  重新捕获。证据 JSON 落 analysis/local-checks/d2aj/scene.json(git 忽略)。
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

# ---- 固件地址/形状(全部预研 §2/§4.4 钉死,本文件不发明地址) ----
ESCO_OPEN = 0x60200b6          # esco 开链(flags;入口无 pend,同步)
ESCO_FLAGS = 0x5               # slot{1,0}→组{0,1}(ch0+ch1),INJ-03 首选 fixture
SET_BUFFER = 0x606dee4         # 五写 ADR/LEN/PNS/HWP=0/SRP=0
LINK_HANDLER = 0x606df3a       # 挂链(hdl, node)——尾部插入 hdl+0/+4 环
LINK_CALL_SITE = 0x6020334     # esco 侧挂链调用点(r0=hdl, r1=obj+16)
ADC_START = 0x606df66          # 启动(CON bit5+bit4 置位族)
DISPATCHER = 0x602f406         # IRQ68 分发器(向量槽 0x19ff10 内容)
VECTOR_SLOT = 0x19ff10         # 0x19fe00+68*4
AUDIO_HANDLER = 0x6072628      # audio_adc_irq_handler
# D2AI 运行时勘误:esco aec 前端 output handler=0x6033D5A(非预研 §3 表转写的
# 0x6031D5A)。证据:开链后链表节点 node=obj+16,[node+12]=[obj+28]=0x6033d5a;
# 0x6033d5a 函数头 push {rets,r14-r4} 后首读 [QCY+620](esco 运行对象槽)——
# 0x6031d5a 无函数序言(非入口)。挂链调用点 0x6020334 处 r0=0x6033d5a 互证。
ESCO_HANDLER = 0x6033D5A
RTI_TAIL = 0x602f452           # 分发器 rti(0x602f44e pop.srl 先恢复特殊帧)
PARK_STOP_PC = 0x606955c       # park 前沿停点(GPCNT NUM 读,frontier_registry)
HDL_SLOT = 0x126A90            # [QCY+620] esco 运行对象槽(通话未开=0)
AUDIO_ADC_HDL = 0x1277B4       # QCY+4596 共享 hdl(空链自指)
QCY = 0x1265C0

# b2 分发器 ADC 侧必经 PC 集(INJ-02/预研 §5:0x602f418 DAC 清位不在内)
DISPATCHER_PCS = (0x602f406, 0x602f412, 0x602f42e, 0x602f430, 0x602f434,
                  0x602f43a, 0x602f440, 0x602f444, 0x602f44e, 0x602f452)
HANDLER_PCS = (0x6072628, 0x60726e4, 0x60726ec, 0x60726f8)
WATCH_PCS = DISPATCHER_PCS + HANDLER_PCS + (ESCO_HANDLER, LINK_CALL_SITE, SET_BUFFER)
# b3(D2AJ)esco handler 内部路径锚(反汇编钉死,不做语义预设):
#   0x6033d8e 丢弃门早退 goto(计数≤7 路径) / 0x6033d90 真路径首站
#   0x6033dc6 解交织写 h[local[ch]+n*2]=interleaved (0x6033dc2..cc 内环)
#   0x6033cb4 逐通道消费函数入口(aec 前端) / 0x6033e4e 出口序言
ESCO_EARLY_EXIT = 0x6033D8E
ESCO_REAL_PATH = 0x6033D90
ESCO_DEINTERLEAVE = 0x6033DC6
ESCO_CHAN_CONSUMER = 0x6033CB4
B3_WATCH_PCS = WATCH_PCS + (ESCO_EARLY_EXIT, ESCO_REAL_PATH, ESCO_DEINTERLEAVE,
                            ESCO_CHAN_CONSUMER, 0x6033E4E)
# ANC 三字节状态面(cmd-0x17-0x1d-layout.md;负例①「配置面不动」观察点)
ANC_STATUS_BYTES = (0x12666B, 0x12666C, 0x12666D)

BLOCK_FORMULA = 'sample(ch,n)=((ch+1)<<8)|((n_ch4&0xF)<<4)|(n&0xF) — blueprint §3.1 '\
                'data design (channel-distinguishable high byte, per-sample index low nibble)'


def read_bytes(cpu, address, size):
    # MMU 逐页分块读(大缓冲 128B 页界跨界整块读同样被拒;与 produce_block
    # 的分块写同一翻译面)。
    out = bytearray()
    while size:
        chunk = size if (address & 127) + size <= 128 else 128 - (address & 127)
        out += cpu.mem.read(address, chunk)
        address += chunk
        size -= chunk
    return bytes(out)


def read_u32(cpu, address):
    return struct.unpack('<I', read_bytes(cpu, address, 4))[0]


def read_u16(cpu, address):
    return struct.unpack('<H', read_bytes(cpu, address, 2))[0]


def read_u8(cpu, address):
    return read_bytes(cpu, address, 1)[0]


def compose_block(pns, channels, sample_bytes, block_index=0):
    """蓝图数据设计三锚:通道可区分(高字节 01/02)+随块变化(bits7-4 块号)+样本序(bits3-0)。"""
    if sample_bytes != 2:
        raise SystemExit('scene compose: only 16-bit samples are wired (b[hdl+90]=%d)' % sample_bytes)
    out = bytearray()
    for n in range(pns):
        for ch in range(channels):
            out += struct.pack('<H', ((ch + 1) << 8) | ((block_index & 0xF) << 4) | (n & 0xF))
    return bytes(out)


def chain_walk(cpu, hdl):
    """hdl+0/+4 环形链(哨兵=hdl)遍历:返回 [{node, priv, handler}](≤16 防环)。"""
    nodes = []
    node = read_u32(cpu, hdl)
    while node != hdl and len(nodes) < 16:
        nodes.append({'node': hex(node), 'priv': hex(read_u32(cpu, node + 8)),
                      'handler': hex(read_u32(cpu, node + 12))})
        node = read_u32(cpu, node)
    return nodes


def run_scene(machine, hardware, cpu, max_handler_steps):
    driver = SimWorldDriver(machine, hardware, cpu)
    dma = hardware.jl_audio_adc_dma
    observations = {'banner': {'stop_pc': hex(cpu.pc), 'core': cpu.core_id,
                               'active_context': getattr(cpu, 'active_context', None),
                               'in_irq_context_banner': driver.in_irq_context,
                               'delivery_projection': bool(dma.delivery)}}
    # ---------------- world_prep 前置三查(INJ-03 b1 前置) ----------------
    # hdl 槽 0x126A90 = [QCY+308*4](reset 函数落盘,PRE §6.1);esco 运行对象
    # 槽 [QCY+620]=0x12682C(park 稳态=0,通话未开)。
    hdl_slot_value = read_u32(cpu, HDL_SLOT)
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
        'hdl79_before': read_u8(cpu, AUDIO_ADC_HDL + 79),
        'check': {'hdl_slot_is_0x1277B4': hdl_slot_value == AUDIO_ADC_HDL,
                  'esco_slot_zero': esco_slot_value == 0,
                  'vector_is_dispatcher': vector_value == DISPATCHER,
                  'irq68_enabled_prio2': nibble == 5}}
    if vector_value != DISPATCHER:
        raise Blocked('d2ai_vector_slot_not_dispatcher', value=hex(vector_value))
    if nibble != 5:
        raise Blocked('d2ai_irq68_nibble_unexpected', nibble=hex(nibble))

    # ---------------- b1:直调开链 0x60200b6(0x5) ----------------
    try:
        # 开链预算:实测 ~6.4M 步(两通道各一次 ADA 偏置等待 delay(20)≈3.2M
        # 步/次+尾部 ~120 步;0x606b5ca 站点),10M 留裕量。
        r0, open_steps, open_hits = driver.direct_call(
            ESCO_OPEN, [ESCO_FLAGS], max_steps=10_000_000,
            watch=WATCH_PCS, label='b1:esco_open_0x60200b6(0x5)')
    except Exception as exc:  # WorldDead/DriverError:如实记录墙,继续采集 b1 已落盘面
        observations['b1_open_chain'] = {'wall': {'type': type(exc).__name__,
                                                  'detail': str(exc)[:1200]}}
        return observations
    five = {hex(a): {'writes': e['writes'], 'last': hex(e['values'][-1]) if e['values'] else None}
            for a, e in dma.stores.items() if e['writes']}
    con_word = hardware.read(0xfe0538) if hasattr(hardware, 'read') else None
    chain_after = chain_walk(cpu, AUDIO_ADC_HDL)
    b1 = {
        'entry': hex(ESCO_OPEN), 'flags': ESCO_FLAGS, 'returned': r0,
        'steps': open_steps,
        'hdl79_after': read_u8(cpu, AUDIO_ADC_HDL + 79),
        'hdl78_after': hex(read_u8(cpu, AUDIO_ADC_HDL + 78)),
        'five_word_records': five,
        'chain_after': chain_after,
        'adc_con_after': hex(con_word) if con_word is not None else 'unavailable',
        'watch_hits': [{'pc': h['pc'], 'r': h['r']} for h in open_hits],
    }
    b1['checks'] = {
        # D2AI 运行时勘误:实测返回 0(预研 §2 表「返回 1」口径不符,如实记录;
        # INJ-03 的 b1 断言面不含返回值)。
        'returned_zero': r0 == 0,
        'two_channels_opened': read_u8(cpu, AUDIO_ADC_HDL + 79) == 2,
        'set_buffer_five_writes': all(
            dma.stores[a]['writes'] >= 1 for a in (0xfe053c, 0xfe0540, 0xfe0544, 0xfe0548, 0xfe054c)),
        'hwp_srp_zero': dma.stores[0xfe0548]['values'][-1] == 0
                        and dma.stores[0xfe054c]['values'][-1] == 0,
        'handler_linked': any(n['handler'] == hex(ESCO_HANDLER) for n in chain_after),
        'gates_bit5_bit4_set': con_word is not None and con_word & 0x30 == 0x30,
    }
    observations['b1_open_chain'] = b1
    if not all(b1['checks'].values()):
        b1['wall'] = {'type': 'b1_assertion_failed', 'checks': b1['checks']}
        return observations

    # ---------------- b2:produce_block + IRQ68 真实交付 ----------------
    channels = read_u8(cpu, AUDIO_ADC_HDL + 79)
    width_bool = read_u8(cpu, AUDIO_ADC_HDL + 90)
    sample_bytes = 2 if width_bool == 0 else 4
    pns = dma.stores[0xfe0544]['values'][-1]
    adr = dma.stores[0xfe053c]['values'][-1]
    block = compose_block(pns, channels, sample_bytes, block_index=1)
    produced = dma.produce_block(cpu.mem, block, channels=channels, sample_bytes=sample_bytes)
    # 资格判(与 advance() 抛请求前同一判据:真实 ICFG nibble+IPMASK)。
    gate = machine.icfg_demands().get(0)
    gate_used = gate if gate is not None else (cpu.icfg if getattr(cpu, 'icfg_known', False) else 0x300)
    eligible = hardware.irq.eligible(68, gate_used)
    b2 = {'produce_block': produced, 'block_formula': BLOCK_FORMULA,
          'gate_used': hex(gate_used) if isinstance(gate_used, int) else gate_used,
          'eligible_at_delivery': eligible,
          'data_pointer_expected': hex(adr)}  # SRP=0 → data = ADR + 0*ch*ssize
    if not eligible:
        b2['wall'] = {'type': 'irq68_not_eligible', 'gate': hex(gate_used)}
        observations['b2_single_delivery'] = b2
        return observations
    try:
        machine.deliver_audio_dma_irq(0)   # 嵌套进入 Timer0 handler(优先级论证在机器侧)
    except (Blocked, ValueError) as exc:
        b2['wall'] = {'type': type(exc).__name__, 'detail': str(exc)[:800]}
        observations['b2_single_delivery'] = b2
        return observations
    entry_event = dict(machine.audio_irq_deliveries[-1])
    # 受控步进(冻结世界纪律:不调 hardware.advance):真实分发器→handler→链→RTI。
    steps = 0
    hits = []
    wall = None
    while steps < max_handler_steps:
        if cpu.pc in WATCH_PCS:
            hits.append({'pc': hex(cpu.pc), 'r': [hex(v) if isinstance(v, int) else str(v)
                                                  for v in cpu.R[:5]], 'step': steps})
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
            break  # RTI 投影已恢复外层上下文,回到停点
    covered = {h['pc'] for h in hits}
    rti_events = [e for e in machine.events if e.get('kind') == 'audio_dma_irq_rti']
    # 样本逐字节回读:链调用点 0x60726e4 的 r1=data 指针处(交付时刻读回)。
    sample_readback = None
    call_sites = [h for h in hits if h['pc'] == hex(0x60726e4)]
    if call_sites:
        data_ptr = int(call_sites[0]['r'][1], 16)
        readback = read_bytes(cpu, data_ptr, len(block))
        sample_readback = {'data_pointer': hex(data_ptr), 'length': len(block),
                           'byte_equal': readback == block,
                           'first16': list(block[:16]),
                           'readback_first16': list(readback[:16])}
    b2.update({
        'delivery_entry_event': entry_event,
        'handler_window_steps': steps,
        'dispatcher_pc_coverage': {'0x%08x' % pc: (hex(pc) in covered) for pc in DISPATCHER_PCS},
        'dac_clear_branch_0x602f418_absent': '0x602f418' not in covered,
        'audio_handler_entry_hits': sum(1 for h in hits if h['pc'] == hex(AUDIO_HANDLER)),
        'chain_call_sites': call_sites,
        'sample_readback': sample_readback,
        'rti_events': rti_events,
        'wall': wall,
        'post_state': {
            'pending_cleared': not dma.pending,
            'in_flight_cleared': not dma.delivery_in_flight,
            'con_w1c_acks': dma.con_w1c_acks,
            'srn_record': {'writes': dma.stores[0xfe0550]['writes'],
                           'last': hex(dma.stores[0xfe0550]['values'][-1])
                                   if dma.stores[0xfe0550]['values'] else None},
            'pns_reload_writes': dma.stores[0xfe0544]['writes'],
            'cpu_back_at_park_stop': cpu.pc == PARK_STOP_PC,
            'outer_context_restored': getattr(cpu, 'active_context', None) == 'timer0_irq_handler',
        },
    })
    b2['checks'] = {
        'nested_entry_justified': bool(entry_event.get('nested')),
        'dispatcher_adcsite_covered': all(b2['dispatcher_pc_coverage'][k] for k in b2['dispatcher_pc_coverage']),
        'audio_handler_called': b2['audio_handler_entry_hits'] >= 1,
        'samples_byte_equal': bool(sample_readback and sample_readback['byte_equal']),
        'pending_cleared_by_real_w1c': b2['post_state']['pending_cleared'],
        'srn_written_equals_pns': (b2['post_state']['srn_record']['last'] == hex(pns)),
        'rti_returned_to_park_context': (cpu.pc == PARK_STOP_PC
                                         and getattr(cpu, 'active_context', None) == 'timer0_irq_handler'),
    }
    observations['b2_single_delivery'] = b2
    if not all(b2['checks'].values()):
        b2['wall'] = {'type': 'b2_assertion_failed', 'checks': b2['checks']}
        return observations

    # ---------------- 负例③:门关(CON bit5 清)→分发器读 pending 清位但不进 handler ----
    # timer0_irq4_masked_probe 先例的音频版:真实分发器字节执行,pending 真实
    # 置位/清位,双使能门关→0x6072628 不被调、SRN 不写、handler 尾三写不发生。
    handler_hits_before = b2['audio_handler_entry_hits']
    srn_writes_before = dma.stores[0xfe0550]['writes']
    hardware.write(0xfe0538, hardware.read(0xfe0538) & ~0x20)   # 真实写形状:清 bit5
    # acks 基线在门关写之后取:门关写自身携带 bit6(CON 现值含 W1C 位),也会
    # 计一次 ack——负例窗口内的增量应恰为分发器自己的一次。
    acks_before = dma.con_w1c_acks
    block2 = compose_block(pns, channels, sample_bytes, block_index=2)
    dma.produce_block(cpu.mem, block2, channels=channels, sample_bytes=sample_bytes)
    machine.deliver_audio_dma_irq(0)
    nsteps = 0
    nhits = []
    neg_wall = None
    while nsteps < max_handler_steps:
        if cpu.pc in WATCH_PCS:
            nhits.append(hex(cpu.pc))
        try:
            before = cpu.pc
            cpu.step()
            nsteps += 1
            machine.after_step(cpu, before)
        except Blocked as exc:
            neg_wall = {'at_step': nsteps, 'stop_pc': hex(cpu.pc), 'stop': exc.details}
            break
        if (cpu.pc == PARK_STOP_PC and
                getattr(cpu, 'active_context', None) == 'timer0_irq_handler'):
            break
    covered_neg = set(nhits)
    negative = {
        'variant': 'gate_closed_bit5_cleared',
        'con_bit5_cleared_by': 'real model write 0xfe0538 (dispatcher-shaped RMW face)',
        'second_block_produced': True,
        'window_steps': nsteps,
        'wall': neg_wall,
        'dispatcher_read_and_cleared_pending': ('0x602f42e' in covered_neg and '0x602f434' in covered_neg),
        'gate_branch_0x602f43a_taken': '0x602f43a' in covered_neg,
        'call_site_0x602f444_absent': '0x602f444' not in covered_neg,
        'audio_handler_not_called': len([h for h in nhits if h == hex(AUDIO_HANDLER)]) == 0,
        'handler_entry_hits_total_unchanged': b2['audio_handler_entry_hits'] == handler_hits_before,
        'srn_writes_unchanged': dma.stores[0xfe0550]['writes'] == srn_writes_before,
        'acks_incremented_by_dispatcher_only': dma.con_w1c_acks == acks_before + 1,
        'pending_cleared': not dma.pending,
        'rti_returned': cpu.pc == PARK_STOP_PC
                        and getattr(cpu, 'active_context', None) == 'timer0_irq_handler',
    }
    observations['b2_negative_gate_closed'] = negative

    # ---------------- b3+b4(D2AJ):连续交付→丢弃门→真消费路径→两整圈 ----------------
    length = dma.stores[0xfe0540]['values'][-1]

    def deliver_and_collect(block_index, muted=False):
        """一轮交付循环:produce→(交付前窗口回读)→IRQ68→受控步进→RTI 回停点。

        返回逐块记录(窗口/指针/路径/计数/消费面),断言材料不做语义预设——
        早退/真路径由 PC 锚客观区分,块号由窗口字节客观携带。
        """
        block = bytes(pns * channels * sample_bytes) if muted \
            else compose_block(pns, channels, sample_bytes, block_index=block_index)
        ring_pos = ((block_index - 1) * pns) % length
        expected_window = adr + ring_pos * channels * sample_bytes
        produced = dma.produce_block(cpu.mem, block, channels=channels,
                                     sample_bytes=sample_bytes)
        window_pre = read_bytes(cpu, expected_window, len(block))
        counter_before = read_u8(cpu, esco_obj + 0)
        # ch1 缓冲([esco+60] 指向的 zalloc 区)不做交付前预读:boot/b1 从未
        # 写过它,严格模型按 uninitialized 拒读(真硅=零)——消费证据只在
        # 真路径写入之后读,未写即如实记。
        ch1_before = None
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
            return rec, window_pre, ch1_before, block
        machine.deliver_audio_dma_irq(0)
        steps = 0
        hits = []
        wall = None
        while steps < max_handler_steps:
            if cpu.pc in B3_WATCH_PCS:
                hits.append({'pc': cpu.pc,
                             'r': [hex(v) if isinstance(v, int) else str(v) for v in cpu.R[:5]]})
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
                                 'rets': hex(cpu.rets),
                                 'active_context': getattr(cpu, 'active_context', None)}}
                break
            if (cpu.pc == PARK_STOP_PC and
                    getattr(cpu, 'active_context', None) == 'timer0_irq_handler'):
                break
        call_sites = [h for h in hits if h['pc'] == 0x60726e4]
        data_ptr = int(call_sites[0]['r'][1], 16) if call_sites else None
        window_post = read_bytes(cpu, expected_window, len(block))
        try:
            ch1_after = read_bytes(cpu, ch1_buf, pns * sample_bytes)
        except Blocked:
            ch1_after = None    # 真路径未发生(或未写满):缓冲保持未初始化
        rec.update({
            'window_steps': steps, 'wall': wall,
            'chain_call_args': call_sites[0] if call_sites else None,
            'data_ptr': hex(data_ptr) if data_ptr is not None else None,
            'data_ptr_matches_ring': data_ptr == expected_window,
            'esco_entries': sum(1 for h in hits if h['pc'] == ESCO_HANDLER),
            'early_exit_hits': sum(1 for h in hits if h['pc'] == ESCO_EARLY_EXIT),
            'real_path_hits': sum(1 for h in hits if h['pc'] == ESCO_REAL_PATH),
            'deinterleave_hits': sum(1 for h in hits if h['pc'] == ESCO_DEINTERLEAVE),
            'chan_consumer_hits': sum(1 for h in hits if h['pc'] == ESCO_CHAN_CONSUMER),
            'counter_after': read_u8(cpu, esco_obj + 0),
            'rti_ok': (cpu.pc == PARK_STOP_PC
                       and getattr(cpu, 'active_context', None) == 'timer0_irq_handler'),
        })
        return rec, window_post, ch1_after, block

    # 负例③关掉的 bit5 门重开(真实 RMW 形状;值含 bit6 的 ack 副作用如实记账)。
    acks_before_reopen = dma.con_w1c_acks
    hardware.write(0xfe0538, hardware.read(0xfe0538) | 0x20)
    esco_obj = read_u32(cpu, QCY + 620)
    aec_slot = read_u32(cpu, QCY + 624)
    ch1_buf = read_u32(cpu, esco_obj + 60)
    b3_base = {
        'gate_reopened_bit5': bool(hardware.read(0xfe0538) & 0x20),
        'reopen_ack_side_effect': dma.con_w1c_acks - acks_before_reopen,
        'esco_obj_QCY620': hex(esco_obj), 'aec_obj_QCY624': hex(aec_slot),
        'esco_fields': {'counter_b0': read_u8(cpu, esco_obj + 0),
                        'channels_b2': read_u8(cpu, esco_obj + 2),
                        'cfg_h4': hex(read_u16(cpu, esco_obj + 4)),
                        'cfg_h6': hex(read_u16(cpu, esco_obj + 6)),
                        'flags_h12': hex(read_u16(cpu, esco_obj + 12)),
                        'ch_buf_60': hex(read_u32(cpu, esco_obj + 60)),
                        'ch_buf_64': hex(read_u32(cpu, esco_obj + 64))},
        'ring': {'ADR': hex(adr), 'LEN_samples': length, 'PNS': pns,
                 'blocks_per_lap': length // pns,
                 'span_bytes': length * channels * sample_bytes,
                 'block_bytes': pns * channels * sample_bytes},
    }
    observations['b3_world_state'] = b3_base

    delivery_records = []
    for k in range(3, 11):                       # b3:k=3..10(调 #2..#9)
        rec, window_post, ch1_after, block = deliver_and_collect(k)
        rec['ch0_packed_equal'] = None
        rec['ch1_buf_equal'] = None
        if rec.get('real_path_hits'):
            ch0_packed = b''.join(struct.pack('<H', (1 << 8) | ((k & 0xF) << 4) | (n & 0xF))
                                  for n in range(pns))
            ch1_expect = b''.join(struct.pack('<H', (2 << 8) | ((k & 0xF) << 4) | (n & 0xF))
                                  for n in range(pns))
            rec['ch0_packed_equal'] = window_post[:pns * sample_bytes] == ch0_packed
            if ch1_after is not None:
                rec['ch1_buf_equal'] = ch1_after == ch1_expect
        else:
            rec['window_post_equal'] = window_post == block
        delivery_records.append(rec)
    observations['b3_deliveries'] = delivery_records

    b4_records = []
    for k in range(11, 14):                      # b4:k=11..13(真路径续+第 4 圈)
        rec, window_post, ch1_after, block = deliver_and_collect(k)
        ch0_packed = b''.join(struct.pack('<H', (1 << 8) | ((k & 0xF) << 4) | (n & 0xF))
                              for n in range(pns))
        ch1_expect = b''.join(struct.pack('<H', (2 << 8) | ((k & 0xF) << 4) | (n & 0xF))
                              for n in range(pns))
        rec['ch0_packed_equal'] = (window_post[:pns * sample_bytes] == ch0_packed
                                   if rec.get('real_path_hits') else None)
        rec['ch1_buf_equal'] = ((ch1_after == ch1_expect)
                                if rec.get('real_path_hits') and ch1_after is not None else None)
        b4_records.append(rec)
    observations['b4_deliveries'] = b4_records

    # ---------------- 负例①(D2AJ):全零静音块→交付照常/消费面按固件真实语义 ----------------
    anc_before = [read_u8(cpu, a) for a in ANC_STATUS_BYTES]
    event_kinds_before = {e.get('kind') for e in machine.events}
    muted_rec, muted_window_post, muted_ch1_after, muted_block = deliver_and_collect(14, muted=True)
    anc_after = [read_u8(cpu, a) for a in ANC_STATUS_BYTES]
    event_kinds_after = {e.get('kind') for e in machine.events}
    muted_rec.update({
        'anc_status_bytes_before': [hex(v) for v in anc_before],
        'anc_status_bytes_after': [hex(v) for v in anc_after],
        'anc_status_unchanged': anc_before == anc_after,
        'new_event_kinds': sorted(event_kinds_after - event_kinds_before),
        'window_post_all_zero': muted_window_post == bytes(len(muted_block)),
        'ch1_buf_all_zero': (muted_ch1_after == bytes(pns * sample_bytes)
                             if muted_ch1_after is not None else None),
        'aec_slot_after': hex(read_u32(cpu, QCY + 624)),
    })
    observations['negative_mute'] = muted_rec

    # ---------------- b4 聚合(全序列 k=1..13+静音):两整圈/SRN 重装/块号单调/RTI ----------------
    consumed = [{'k': 1, 'path': 'early_exit',          # b2:block1=调用#1(计数 0→1)
                 'data_ptr': b2['chain_call_sites'][0]['r'][1] if b2['chain_call_sites'] else None,
                 'content_ok': b2['checks']['samples_byte_equal']}]
    consumed.append({'k': 2, 'path': 'gate_closed',     # 负例③:产出不消费
                     'data_ptr': None, 'content_ok': negative['srn_writes_unchanged']})
    for r in delivery_records + b4_records:
        consumed.append({'k': r['k'],
                         'path': 'real_path' if r.get('real_path_hits') else 'early_exit',
                         'data_ptr': r.get('data_ptr'),
                         'content_ok': (r.get('ch0_packed_equal') if r.get('real_path_hits')
                                        else r.get('window_post_equal', r.get('window_pre_equal')))})
    srn_writes_end = dma.stores[0xfe0550]['writes']
    handler_calls_end = srn_writes_end          # audio handler 尾每次调用写 SRN 一字
    esco_entries_end = 1 + sum(r.get('esco_entries') or 0
                               for r in delivery_records + b4_records) + \
        (muted_rec.get('esco_entries') or 0)
    b4_aggregate = {
        'deliveries_total': len(consumed) + 1,   # +1 = 静音负例块(交付同轨)
        'consumed_sequence': consumed,
        'k_monotonic': [c['k'] for c in consumed] == sorted(c['k'] for c in consumed),
        'handler_calls_vs_srn_reloads': {'srn_writes': srn_writes_end,
                                         'esco_entries': esco_entries_end,
                                         'equal': handler_calls_end == esco_entries_end},
        'ring_wraps_total': dma.hwp // length,
        'ring_laps_covered': (dma.hwp - pns) // length,   # 完整圈数(末块起点所在圈)
        'two_full_laps': dma.hwp // length >= 2,
        'data_ptrs_all_match_ring': all(r.get('data_ptr_matches_ring')
                                        for r in delivery_records + b4_records),
        'all_content_evidence_ok': all(c['content_ok'] for c in consumed if c['path'] != 'gate_closed'),
        'all_rti_ok': all(r.get('rti_ok') for r in delivery_records + b4_records),
        'hwp_samples_monotonic': dma.hwp,
        'blocks_produced': dma.blocks_produced,
        'con_w1c_acks_end': dma.con_w1c_acks,
    }
    observations['b4_aggregate'] = b4_aggregate
    return observations


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--capture', action='store_true', help='全量重放重新捕获快照(~14min)')
    parser.add_argument('--max-handler-steps', type=int, default=400_000)
    parser.add_argument('--out', default='analysis/local-checks/d2aj/scene.json')
    args = parser.parse_args()

    from sim_world_boot import build_world
    t0 = time.time()

    def serve_loop(machine, hardware, cpu, stop=None):
        return run_scene(machine, hardware, cpu, args.max_handler_steps)

    result = build_world(serve_loop, capture=args.capture,
                         rom_path='/home/felix021/tmp/QCY_H3S_BR28_ROM_000000-020000.bin',
                         firmware_path='firmware/QCY_H3S_7018F_1T2_release_note_V1.1.8_011.ufw',
                         snapshot_dir='analysis/snapshots')
    payload = {'elapsed_s': round(time.time() - t0, 1),
               'stop': result.get('stop'),
               'completed_steps': result.get('completed_steps'),
               'scene': {k: v for k, v in result.items()
                         if k in ('banner', 'world_prep', 'b1_open_chain', 'b2_single_delivery',
                                  'b2_negative_gate_closed', 'b3_world_state', 'b3_deliveries',
                                  'b4_deliveries', 'b4_aggregate', 'negative_mute')}}
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=1, default=str), encoding='utf-8')
    b1 = payload['scene'].get('b1_open_chain', {})
    b2 = payload['scene'].get('b2_single_delivery', {})
    neg = payload['scene'].get('b2_negative_gate_closed', {})
    b3 = payload['scene'].get('b3_deliveries', [])
    b4 = payload['scene'].get('b4_deliveries', [])
    mute = payload['scene'].get('negative_mute', {})

    def series(records, key):
        return [r.get(key) for r in records]

    print(json.dumps({'elapsed_s': payload['elapsed_s'],
                      'b1_checks': b1.get('checks'),
                      'b2_checks': b2.get('checks'),
                      'wall': b2.get('wall'),
                      'negative_gate_closed': {k: v for k, v in neg.items()
                                               if k in ('dispatcher_read_and_cleared_pending',
                                                        'call_site_0x602f444_absent',
                                                        'audio_handler_not_called',
                                                        'srn_writes_unchanged',
                                                        'acks_incremented_by_dispatcher_only',
                                                        'pending_cleared', 'rti_returned', 'wall')},
                      'b3_world_state': payload['scene'].get('b3_world_state'),
                      'b3_series': {'k': series(b3, 'k'),
                                    'counter_after': series(b3, 'counter_after'),
                                    'early_exit': series(b3, 'early_exit_hits'),
                                    'real_path': series(b3, 'real_path_hits'),
                                    'deinterleave': series(b3, 'deinterleave_hits'),
                                    'chan_consumer': series(b3, 'chan_consumer_hits'),
                                    'window_pre_equal': series(b3, 'window_pre_equal'),
                                    'data_ptr_matches_ring': series(b3, 'data_ptr_matches_ring'),
                                    'ch0_packed_equal': series(b3, 'ch0_packed_equal'),
                                    'ch1_buf_equal': series(b3, 'ch1_buf_equal'),
                                    'window_post_equal': series(b3, 'window_post_equal'),
                                    'rti_ok': series(b3, 'rti_ok'),
                                    'walls': [r.get('wall') for r in b3 if r.get('wall')]},
                      'b4_series': {'k': series(b4, 'k'),
                                    'real_path': series(b4, 'real_path_hits'),
                                    'ch0_packed_equal': series(b4, 'ch0_packed_equal'),
                                    'ch1_buf_equal': series(b4, 'ch1_buf_equal'),
                                    'data_ptr_matches_ring': series(b4, 'data_ptr_matches_ring'),
                                    'rti_ok': series(b4, 'rti_ok'),
                                    'walls': [r.get('wall') for r in b4 if r.get('wall')]},
                      'negative_mute': {k: mute.get(k) for k in
                                        ('k', 'esco_entries', 'real_path_hits',
                                         'deinterleave_hits', 'chan_consumer_hits',
                                         'window_post_all_zero', 'ch1_buf_all_zero',
                                         'anc_status_unchanged', 'new_event_kinds', 'wall')},
                      'b4_aggregate': payload['scene'].get('b4_aggregate'),
                      'evidence': str(out)}, ensure_ascii=False, indent=1))
    return 0


if __name__ == '__main__':
    sys.exit(main())
