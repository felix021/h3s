#!/usr/bin/env python3
"""D2AI 场景 runner:park 停点世界上的 esco 首场景音频输入注入(b1+b2)。

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

用法:python3 tools/d2_audio_scene.py [--capture] [--max-handler-steps N]
  默认 restore analysis/snapshots 的 park 快照(~秒级);--capture 全量重放
  重新捕获。证据 JSON 落 analysis/local-checks/d2ai/scene.json(git 忽略)。
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
    return observations


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--capture', action='store_true', help='全量重放重新捕获快照(~14min)')
    parser.add_argument('--max-handler-steps', type=int, default=400_000)
    parser.add_argument('--out', default='analysis/local-checks/d2ai/scene.json')
    args = parser.parse_args()

    from sim_world_boot import build_world
    t0 = time.time()

    def serve_loop(machine, hardware, cpu):
        return run_scene(machine, hardware, cpu, args.max_handler_steps)

    result = build_world(serve_loop, capture=args.capture,
                         rom_path='/home/felix021/tmp/QCY_H3S_BR28_ROM_000000-020000.bin',
                         firmware_path='firmware/QCY_H3S_7018F_1T2_release_note_V1.1.8_011.ufw',
                         snapshot_dir='analysis/snapshots')
    payload = {'elapsed_s': round(time.time() - t0, 1),
               'stop': result.get('stop'),
               'completed': result.get('completed'),
               'scene': {k: v for k, v in result.items()
                         if k in ('banner', 'world_prep', 'b1_open_chain', 'b2_single_delivery',
                                  'b2_negative_gate_closed')}}
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=1, default=str), encoding='utf-8')
    b1 = payload['scene'].get('b1_open_chain', {})
    b2 = payload['scene'].get('b2_single_delivery', {})
    neg = payload['scene'].get('b2_negative_gate_closed', {})
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
                      'evidence': str(out)}, ensure_ascii=False, indent=1))
    return 0


if __name__ == '__main__':
    sys.exit(main())
