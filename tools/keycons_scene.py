#!/usr/bin/env python3
"""KEYCONS 场景 runner：park 停点世界上的按键事件消费面直调定位验证。

docs/peripheral-input-simulation.md §7.3 第 6/7 层诚实边界（S19 分发/handler 消费
「等 S19」）的 CALLIMPL 式直调定位法收口：唯一合成输入=按键事件载荷字
0x10a00（key10 单击——S 路真固件 post 序列实测过的生产者侧输出，AJ §8.2 同形
互证），post API/入队/出队/过滤/泵分发/handler/动作发布全部真实固件执行。

消费链（本路反汇编钉死，见报告）：
  post 面   0x6001b5e 调 0x6000cba(载荷字,0)→0x6064978('app_core',0x10000C,2,&载荷)
            →0x606487c 环形入队（头字 (0x10000C<<8)|3=0x10000C03+{0x10a00,0}）
  消费面    S19 循环 0x60123e6→get_event(sp,16)=0x6064c8c：当前任务=[0x126C5C+core*4]
            →队列=任务+8（+0 写游标/+2 读游标/+4 容量/+6 计数/+8 缓冲）→
            0x6064b98(队列,16,block) 出队解包→0x6064c0e(16,block) 过滤：
            block[0]&0xF00000==0x300000→内联回调返 1 续取；否则 get_event 返 13
  泵分发    0x60123f4 读 [sp]=id→bit0x100000 门→id==r12(0x10000C)→特路 0x60108fe
            （子码=载荷[0]，0x10a00 非 20/21→公共出口 0x6010a38 返 0）→表2 walk
            0x601240a（6 记录@0x60A8C68）：[0] 0x10000C→0x600434a（载荷≠22 返 0
            续 walk）、[5] 0x10000C→0x60252c6（0x601241e 间接 call，r0=sp+4）
  handler   0x60252c6：bit16 门→tbb 手势→b[QCY+38] 吞键门→0x60c0fd8 门→
            0x6008170 模式门（==5）→键掩码 0x09050307→15×3B 映射表@QCY+3404
            （默认 (7,1)→动作 1）→cmd1 0x6025396：0x6018bf4 发布 'music_pp'
            （[QCY+416] 环追加）→提示音 0x60075c6(r9,66)→返 1→0x6012422 事件终结

分段（照 CALLIMPL 两段法，接缝如实分档）：
  步 1 post 档     直调 0x6000cba(0x10a00,0)——手势机 0x6001b5e 调用点的同 ABI 真码。
  步 2/3 存量档    park 队列既有 0x300004 回调事件（CALLIMPL 记录的冻结存量）：
                   真出队+真过滤（内联回调 0x60226c2=裸 rts 执行）——FIFO 决定
                   先于按键事件出队，顺带取「0x300000 族消费」运行时证据。
  步 4/5 出队档    按键事件真出队（block={0x10000C,0x10a00,0}）+真过滤（返 0=
                   get_event 的 13 交付语义）。接缝=13 返泵的打包点（get_event
                   上下文门 icfg.b0>0 拒绝任务态直调，如实分档不硬凑）。
  步 6 泵分发档    0x60123f4 续行（get_event 返 13 后的第一指令）：寄存器面=
                   0x60123c0..0x60123e0 prologue 常量直载（r4=sp+4/r7,r11=表2界/
                   r8=QCY/r9=611/r10=表1界/r12=0x10000C/r13=表2尾），block 写在
                   停点 sp 下方的死帧窗（[sp] 面=步 4 真出队产物，不手编事件字），
                   停点集={0x60123e6 循环头,0x6012422 事件终结}。

断言面=事件出队（队列计数/游标/block 字段）/分发命中（特路+表2[0]+表2[5]
PC 链）/handler 执行（入口→cmd1 分支 PC 链）/动作可观测（'music_pp' 环游标
推进+名字字节落环）/事件终结（停点=0x6012422 且 r0=handler 返 1）。
PC watch 全部为反汇编钉死的真实指令地址（本文件不发明地址/状态）。

用法：python3 tools/keycons_scene.py [--payload 0x10a00] [--out .../scene.json]
  默认 restore analysis/snapshots 的 park 快照（gpcnt_num 关——CALLIMPL 同款
  flag_overrides 配套侧）；证据 JSON 落 analysis/local-checks/keycons/scene.json
  （git 忽略）。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from sim_world_driver import SimWorldDriver                        # noqa: E402
from sim_world_driver import POST_WRAPPER                          # noqa: E402
from probe_boot import Blocked                                     # noqa: E402

# ---- 固件地址（本路反汇编钉死+既有路互证，不发明） ----
QCY = 0x1265C0
# post 面
POST_API = 0x6000cba              # 事件 post API（手势机 0x6001b5e 的调用目标）
POST_NAME_ADDR = 0x60a1d11        # 'app_core'（0x6000cc2 li 立即数）
POST_EVENT_ID = 0x10000C          # ★硬编码事件 id（0x6000cc8 li）
POST_INNER = 0x606487c            # os_taskq_post 内层（CALLIMPL 同名锚）
POST_MAIN = 0x60648a2             # 内层主路径（b[QCY+52]==0 才达）
POST_TASK_FOUND = 0x60648b0       # 任务查找命中后的队列几何路径
POST_PAIR_HEAD = 0x60648c0        # 入队字写并行对头（watch 锚纪律：对头）
POST_RESULT_OBS = 0x606498a       # 该 PC 处 r0=内层 post 返回值
# 消费面
CUR_TASK_TABLE = 0x126C5C         # 每核当前任务表（get_event/dequeue 的 cnum 寻址；
                                   # 0x60652e8 idle 创建时预填=idle 对象 QCY+cnum*0x58+6136）
TASK_LOOKUP = 0x60647fa           # 按名查任务对象（0x6064856 strcmp 链；纯查询零入队）
DEQUEUE = 0x6064b98               # 取记录（get_event 内部委调；ABI r0=任务+8,r1=size,r2=block）
UNPACK = 0x60649b0                # 解包（dequeue 内部委调）
FILTER = 0x6064c0e                # 0x300000 族内联消费/普通事件放行（ABI r0=size,r1=block）
FILTER_CB_N1 = 0x6064c48          # n==1 臂的 call r1（回调执行点）
FILTER_RETSLOT = 0x6064c72        # 回写返回值槽（bit8 臂）
# 泵分发档
PUMP_CONT = 0x60123f4             # get_event 返 13 后续行首指令（r0=[sp]）
SPECIAL_PATH = 0x60108fe          # 0x10000C 特路（子码分派）
SPECIAL_EXIT = 0x6010a38          # 特路公共出口（r0=r5 返）
WALK_CMP = 0x6012416              # 表2 walk 的 event 比较分支部
CALL_SITE = 0x601241e             # 间接 call r1（并行对头 0x601241a 的执行点）
TABLE2_R0 = 0x600434a             # 表2[0]（0x10000C 同族先行 handler）
KEY_HANDLER = 0x60252c6           # 表2[5]（按键 handler；BW/AO 互证）
# handler 内部链（AO 文档+本路复核）
H_BIT16_GATE = 0x60252cc          # bit16 键事件族门（过门=不跳走）
H_TBB = 0x60252d8                 # 手势子类型 tbb
H_SWALLOW_GATE = 0x60252fa        # b[QCY+38]==1 吞键门（不得命中跳走）
H_SECOND_GATE = 0x6025302         # 0x60c0fd8()≠0 吞键门（park 预期返 0 过门）
H_MODE_QUERY = 0x602531c          # 0x6008170 模式查询调用点
H_MAP_HIT = 0x6025364             # 映射命中（r5=动作号）
CMD1_BRANCH = 0x6025396           # cmd 1 分支体（发布 'music_pp'+提示音 66）
PUBLISHER = 0x6018bf4             # 动作名发布器（[QCY+416] 环追加）
TONE_THUNK = 0x60075c6            # 提示音（0x6003b02 thunk）
H_RET_HANDLED = 0x602530a         # handler 返 1 出口（r4=1）
# 停点集（分段边界=真实控制流点）
PUMP_LOOP_HEAD = 0x60123e6        # 循环头（walk 穷尽/下一事件）
PUMP_EVENT_DONE = 0x6012422       # 事件终结（handler 返非 0）
# 动作面
RING_PTR_SLOT = QCY + 416         # [QCY+416]=动作名环对象指针（0x6018bfc）
MAP_TABLE = QCY + 3404            # 15×3B {key,gesture,action}（0x602533e）
MODE_FILTER_SLOT = QCY + 611 * 4  # [0x126F4C] 过滤对象（特路+表1 门）
SWALLOW_BYTE = QCY + 38           # b[QCY+38] 吞键门字节
MODE_QUERY = 0x6008170              # 0x6008170()：工作模式查询（handler 模式门）
DEFAULT_MAP_FLASH = 0x609e250       # flash 默认映射表 45B（0x60254ce 装载源；AO §3.2）
CMD1_NAME_ADDR = 0x60a1cc0          # 'music_pp'（cmd1 r8+1024 名串,flash 直读互证）
R4_OFF = 4                        # r4 = sp+4（载荷指针）
R7_TABLE1_END = 0x60A8C68
R8_QCY = QCY
R9_FILTER_IDX = 611
R10_TABLE1_BEGIN = 0x60A89E0
R11_TABLE2_BEGIN = 0x60A8C68
R12_EVENT_ID = POST_EVENT_ID
R13_TABLE2_END = 0x60A8CB0

PAYLOAD_DEFAULT = 0x10a00         # key10 单击（S 路实测 post 载荷字）

# 泵段 live-in 寄存器面（r4 的值随 seg_sp 定,运行时填;其余=prologue 常量）。
PUMP_REG_FACE_DEFAULT = {7: R7_TABLE1_END, 8: R8_QCY, 9: R9_FILTER_IDX,
                         10: R10_TABLE1_BEGIN, 11: R11_TABLE2_BEGIN,
                         12: R12_EVENT_ID, 13: R13_TABLE2_END}

POST_WATCH = (POST_API, POST_WRAPPER, POST_INNER, POST_MAIN, POST_TASK_FOUND,
              POST_PAIR_HEAD, POST_RESULT_OBS)
DEQ_WATCH = (DEQUEUE, UNPACK)
FILTER_KEY_WATCH = (FILTER, FILTER_CB_N1)     # 按键事件走普通臂：回调点=负证据
PUMP_WATCH = (PUMP_CONT, SPECIAL_PATH, SPECIAL_EXIT, WALK_CMP, CALL_SITE,
              TABLE2_R0, KEY_HANDLER, H_BIT16_GATE, H_TBB, H_SWALLOW_GATE,
              H_SECOND_GATE, H_MODE_QUERY, H_MAP_HIT, CMD1_BRANCH, PUBLISHER,
              TONE_THUNK, H_RET_HANDLED, PUMP_EVENT_DONE)

# 队列几何（任务对象坐标：+8 写游标/+10 读游标/+12 容量/+14 计数/+16 缓冲；
# 出队函数坐标=任务+8 起：+2 读游标/+4 容量/+6 计数/+8 缓冲——两套坐标同一物理）
TASK_WCUR, TASK_RCUR, TASK_CAP, TASK_CNT, TASK_BUF = 8, 10, 12, 14, 16

# park 队列存量（CALLIMPL 记录的冻结世界未消费存量，运行时复核）
STOCK_HEAD_WORD = 0x30000405      # (0x300004<<8)|5：回调族，4 字跟载荷
STOCK_CALLBACK = 0x60226c2        # 裸 rts（本路反汇编钉死）
STOCK_FLAGS = 0x101               # n=1 参数字+bit8 回写返回值
STOCK_PARAM = 0x0
STOCK_RETSLOT = 0x126270
STOCK_WORDS = 5
FILTER_STOCK_WATCH = (FILTER, FILTER_CB_N1, STOCK_CALLBACK, FILTER_RETSLOT)


def read_bytes(cpu, address, size):
    out = bytearray()
    while size:
        chunk = size if (address & 127) + size <= 128 else 128 - (address & 127)
        try:
            out += cpu.mem.read(address, chunk)
        except Blocked:
            return None
        address += chunk
        size -= chunk
    return bytes(out)


def read_u32(cpu, address):
    data = read_bytes(cpu, address, 4)
    return None if data is None else int.from_bytes(data, 'little')


def read_u16(cpu, address):
    data = read_bytes(cpu, address, 2)
    return None if data is None else int.from_bytes(data, 'little')


def read_u8(cpu, address):
    data = read_bytes(cpu, address, 1)
    return None if data is None else data[0]


def hexed(value):
    return None if value is None else hex(value)


def task_queue_face(cpu, task):
    """app_core 任务队列观察面（任务坐标；未初始化读=拒读如实记）。"""
    if task is None:
        return None
    wcur = read_u16(cpu, task + TASK_WCUR)
    rcur = read_u16(cpu, task + TASK_RCUR)
    cap = read_u16(cpu, task + TASK_CAP)
    cnt = read_u16(cpu, task + TASK_CNT)
    buf = read_u32(cpu, task + TASK_BUF)
    words = []
    if buf is not None and cap is not None:
        for i in range(cap if cap <= 16 else 16):
            words.append(hexed(read_u32(cpu, buf + 4 * i)))
    return {'task': hexed(task), 'count': cnt, 'write_cursor': wcur,
            'read_cursor': rcur, 'capacity': cap, 'buffer': hexed(buf),
            'buffer_words': words}


def ring_face(cpu):
    """动作名环观察面：[QCY+416] 指针+b[ring+0] 游标+前 40B。"""
    ring = read_u32(cpu, RING_PTR_SLOT)
    if not ring:
        return {'ring_ptr': hexed(ring)}
    return {'ring_ptr': hexed(ring), 'cursor_b0': read_u8(cpu, ring),
            'bytes_40': list(read_bytes(cpu, ring, 40) or [])}


def covered_pcs(hits):
    return {h['pc'] for h in hits}


def hit_at(hits, pc):
    want = hex(pc)
    return next((h for h in hits if h['pc'] == want), None)


def segment_call(cpu, machine, entry, reg_face, sp_block_words, stop_pcs,
                 watch=(), max_steps=5_000_000, label=None):
    """泵分发档受控步进（driver.direct_call 的分段变体，场景文件本地实现）。

    与 SimWorldDriver.direct_call 的差异（接缝如实）：
      - sp 显式下移到停点死帧窗（泵代码读 [sp]/[sp+4] 面——活栈不可写），
        block 字=步 4 真出队产物（调用方写入，本函数不编事件字）；
      - 停点集 stop_pcs（真实控制流点：循环头/事件终结）命中即停；
      - **不抛墙**：Blocked/预算/idle 撞墙时返回 dict 携带 watch_hits（墙前
        证据不丢——CALLIMPL「铁证不随世界死亡丢面」同款纪律），上下文寄存器
        损坏才抛（世界半写不可继续，调用方如实终止场景）。
    纪律与 direct_call 对齐：哨兵返回、SP 平衡校验、finally 只恢复寄存器。"""
    saved = {'pc': cpu.pc, 'sp': cpu.sp, 'rets': cpu.rets, 'flags': cpu.flags,
             'R': list(cpu.R)}
    context_before = (getattr(cpu, 'icfg', None), getattr(cpu, 'icfg_known', None),
                      getattr(cpu, 'reti', None), getattr(cpu, 'irq_psr', None),
                      getattr(cpu, 'active_context', None))
    seg_sp = sp_block_words['sp']
    for index, word in enumerate(sp_block_words['block']):
        cpu.mem.write(seg_sp + 4 * index, word.to_bytes(4, 'little'))
    cpu.pc = entry
    cpu.sp = seg_sp
    for index, value in reg_face.items():
        cpu.R[index] = value
    cpu.rets = 0x00100000            # DIRECT_SENTINEL 同值（driver 同款未映射空洞）
    watch_set = set(watch)
    stop_set = set(stop_pcs)
    steps = 0
    watch_hits = []
    stop_pc = None
    r_face = None
    wall = None
    try:
        while cpu.pc != 0x00100000:
            if watch_set and cpu.pc in watch_set:
                watch_hits.append({'pc': hex(cpu.pc),
                                   'r': [hex(v) if isinstance(v, int) else str(v)
                                         for v in cpu.R[:8]], 'step': steps,
                                   'label': label})
            if cpu.pc in stop_set:
                stop_pc = cpu.pc
                r_face = [hex(v) if isinstance(v, int) else str(v) for v in cpu.R[:8]]
                break
            if steps >= max_steps:
                wall = {'type': 'segment_call_budget', 'stop_pc': hex(cpu.pc),
                        'steps': steps, 'label': label}
                break
            insn, _raw, _size = cpu.decode(cpu.pc)
            if insn.id == 'idle':
                wall = {'type': 'segment_call_idle', 'stop_pc': hex(cpu.pc),
                        'steps': steps, 'label': label}
                break
            before = cpu.pc
            cpu.step()
            steps += 1
            machine.after_step(cpu, before)
        if wall is None and stop_pc is None:
            # 哨兵返回：SP 必须平衡回 seg_sp（callee 帧对称）。
            if cpu.sp != seg_sp:
                wall = {'type': 'segment_call_sp_unbalanced', 'entry': hex(entry),
                        'seg_sp': hex(seg_sp), 'exit_sp': hex(cpu.sp)}
            else:
                r_face = [hex(v) if isinstance(v, int) else str(v) for v in cpu.R[:8]]
    except Blocked as exc:                       # 模型墙：墙前 watch 流水随行
        wall = {'type': 'segment_call_blocked', 'stop_pc': hex(cpu.pc),
                'stop': exc.details, 'steps': steps, 'label': label}
    except Exception as exc:                     # 上游未预期异常：如实记墙
        wall = {'type': 'segment_call_simulator_failure',
                'error_type': type(exc).__name__, 'message': str(exc)[:300],
                'stop_pc': hex(cpu.pc), 'steps': steps, 'label': label}
    finally:
        cpu.pc = saved['pc']
        cpu.sp = saved['sp']
        cpu.rets = saved['rets']
        cpu.flags = saved['flags']
        cpu.R = saved['R']
    context_after = (getattr(cpu, 'icfg', None), getattr(cpu, 'icfg_known', None),
                     getattr(cpu, 'reti', None), getattr(cpu, 'irq_psr', None),
                     getattr(cpu, 'active_context', None))
    if context_after != context_before:
        raise RuntimeError('segment_call_context_corrupted entry=%#x wall=%r'
                           % (entry, wall))
    return {'stop_pc': stop_pc, 'r_face': r_face, 'steps': steps,
            'watch_hits': watch_hits, 'wall': wall}


def run_scene(machine, hardware, cpu, payload, max_steps_seg):
    driver = SimWorldDriver(machine, hardware, cpu)
    observations = {'banner': {
        'stop_pc': hex(cpu.pc), 'core': cpu.core_id,
        'active_context': getattr(cpu, 'active_context', None),
        'payload_word': hex(payload),
        'synthetic_inputs': [
            'payload word %#x (key10 单击;S 路真固件 post 序列的实测输出,AJ §8.2 逐字段互证;'
            'post API 以上(载荷字本身)为合成,post/入队/出队/过滤/泵分发/handler/动作全真码)'
            % payload,
            '泵分发档接缝:get_event 的 icfg.b0 上下文门+任务态选择不在停点世界直调可达段'
            '(IRQ 上下文返 3)——出队/过滤由其内部委调 0x6064b98/0x6064c0e 真跑,'
            '13 返泵打包点为分段边界;泵段寄存器面=prologue 常量直载(CALLIMPL post-tx 同法)',
            's6b fixture:RAM 映射表 @0x12730C 装入 flash 默认表 45B@0x609e250'
            '(0x60254ce 装载源,真固件数据;擦除态世界 config 48 缺档致原表全零——'
            'synthetic-load-of-real-bytes,S 路 config 523 fixture 同款先例,'
            's6 无 fixture 档先跑并保留世界真值结果)'],
        'park_anchor': ('snapshot frontier 29,601,125@0x606955c(gpcnt_num 关配套侧,'
                        'CALLIMPL 同款 flag_overrides)'),
    }}

    # ---------------- 步 0:world_prep ----------------
    # park 停点 CPU0 当前任务=idle 对象（0x60652e8 预填值 0x127DB8——app_core 不在
    # 跑,P3「冻结世界只入队不消费」的运行时佐证）——app_core 任务对象改由真码
    # 按名查找（0x60647fa,poster 同款查找,纯查询零入队）。
    cur_task = read_u32(cpu, CUR_TASK_TABLE)
    app_core, lookup_steps, lookup_hits = driver.direct_call(
        TASK_LOOKUP, [POST_NAME_ADDR], max_steps=64_000,
        label='s0:lookup_app_core')
    mode_probe, mode_steps, _mode_hits = driver.direct_call(
        MODE_QUERY, [], max_steps=64_000, label='s0:mode_probe')
    stock_retslot_val = read_u32(cpu, STOCK_RETSLOT)
    face0 = {
        'cur_task_core0': hexed(cur_task),
        'cur_task_note': ('[0x126C5C]=idle 任务对象（0x60652c8 创建链 0x60652e8 预填 '
                          'QCY+cnum*0x58+6136=0x127DB8）——park 停点 CPU0=idle 在跑,'
                          'app_core 未运行:get_event 真消费需调度器先切回 app_core'),
        'app_core_task_lookup': {'entry': hex(TASK_LOOKUP), 'returned': hexed(app_core),
                                 'steps': lookup_steps,
                                 'name_addr': hex(POST_NAME_ADDR)},
        'mode_query_probe': {'entry': hex(MODE_QUERY), 'returned': mode_probe,
                             'steps': mode_steps},
        'queue': task_queue_face(cpu, app_core),
        'swallow_byte_QCY38': read_u8(cpu, SWALLOW_BYTE),
        'mode_filter_slot_126F4C': hexed(read_u32(cpu, MODE_FILTER_SLOT)),
        'tone_handle_QCY924': hexed(read_u32(cpu, QCY + 924)),
        'ring': ring_face(cpu),
        'map_table_15x3': [list(read_bytes(cpu, MAP_TABLE + 3 * i, 3) or [])
                           for i in range(15)],
        'stock_retslot_126270_pre': hexed(stock_retslot_val),
        'post_gate_b_QCY52': read_u8(cpu, QCY + 52),
    }
    q0 = face0['queue'] or {}
    face0['checks'] = {
        # 预期面（CALLIMPL 运行时证据+本路队列几何推导）：存量 0x300004 记录
        # 5 字、读写游标 5/0、容量 128。模式门/吞键门/环指针/映射表=运行时
        # 观测项（BW 世界以 mode 5 过门,park 具体值未钉——只记录,断言在步 6 结果里）。
        'app_core_task_found': isinstance(app_core, int) and app_core != 0,
        'queue_count_is_stock5': q0.get('count') == STOCK_WORDS,
        'queue_cursors_5_0': q0.get('write_cursor') == 5 and q0.get('read_cursor') == 0,
        'stock_head_word_present': q0.get('buffer_words') and
                                   q0['buffer_words'][0] == hex(STOCK_HEAD_WORD),
        'stock_callback_word': q0.get('buffer_words') and
                               q0['buffer_words'][1] == hex(STOCK_CALLBACK),
    }
    observations['world_prep'] = face0
    if not all(face0['checks'].values()):
        observations['wall'] = {'type': 'world_prep_stock_mismatch',
                                'failed': [k for k, v in face0['checks'].items() if not v]}
        return observations
    queue = app_core

    # ---------------- 步 1:post 档（真 post API 入队） ----------------
    r0_1, steps1, hits1 = driver.direct_call(
        POST_API, [payload, 0], max_steps=200_000, watch=POST_WATCH,
        label='s1:post_%#x' % payload)
    q1 = task_queue_face(cpu, queue)
    post_result = hit_at(hits1, POST_RESULT_OBS)
    cov1 = covered_pcs(hits1)
    s1 = {
        'entry': hex(POST_API), 'abi': 'r0=载荷字,r1=0（0x6001b5c 真码同形）',
        'returned': r0_1, 'steps': steps1,
        'post_path': {'wrapper': hex(POST_WRAPPER) in cov1,
                      'inner': hex(POST_INNER) in cov1,
                      'main_b52_gate_passed': hex(POST_MAIN) in cov1,
                      'task_found': hex(POST_TASK_FOUND) in cov1,
                      'queue_word_written': hex(POST_PAIR_HEAD) in cov1,
                      'post_return_r0': post_result['r'][0] if post_result else None},
        'queue_after': q1, 'watch_hit_pcs': sorted(cov1),
    }
    s1['checks'] = {
        # 断言 P①:post 链 PC 覆盖（API→wrapper→inner→主路径→任务命中→入队字写）。
        'poster_chain_covered': all(hex(p) in cov1 for p in
                                    (POST_WRAPPER, POST_INNER, POST_MAIN,
                                     POST_TASK_FOUND, POST_PAIR_HEAD)),
        # 断言 P②:队列落盘（计数 5→8、写游标 5→8、头字 0x10000C03+载荷字）。
        'queue_count_5_to_8': q1.get('count') == STOCK_WORDS + 3,
        'queue_write_cursor_5_to_8': q1.get('write_cursor') == STOCK_WORDS + 3,
        'key_head_word_landed': q1.get('buffer_words') and
                                q1['buffer_words'][5] == hex(POST_EVENT_ID << 8 | 3),
        'key_payload_words_landed': q1.get('buffer_words') and
                                    q1['buffer_words'][6] == hex(payload)
                                    and q1['buffer_words'][7] == '0x0',
        'post_returned_zero': r0_1 == 0,
    }
    observations['s1_post'] = s1
    if not all(s1['checks'].values()):
        observations['wall'] = {'type': 's1_post_assertion_failed',
                                'failed': [k for k, v in s1['checks'].items() if not v]}
        return observations

    # ---------------- 步 2/3:存量档（0x300004 回调事件真消费） ----------------
    block = driver.scratch_body           # 16B 出队块（driver scratch,栈不冲突）
    r0_2, steps2, hits2 = driver.direct_call(
        DEQUEUE, [queue + 8, 16, block], max_steps=100_000, watch=DEQ_WATCH,
        label='s2:dequeue_stock')
    q2 = task_queue_face(cpu, queue)
    blk2 = [hexed(read_u32(cpu, block + 4 * i)) for i in range(5)]
    s2 = {
        'entry': hex(DEQUEUE),
        'abi': 'r0=任务+8(队列对象),r1=16,r2=block（0x6064cbe/cc0/cc2 真码同形）',
        'returned': r0_2, 'steps': steps2,
        'block_words': blk2, 'queue_after': q2,
        'watch_hit_pcs': sorted(covered_pcs(hits2)),
    }
    s2['checks'] = {
        # 断言 C①:存量出队（block={id,回调,flags,参数,返回槽}+计数 8→3+读游标 0→5）。
        'stock_block_shape': blk2 == [hex(STOCK_HEAD_WORD >> 8), hex(STOCK_CALLBACK),
                                      hex(STOCK_FLAGS), hex(STOCK_PARAM),
                                      hex(STOCK_RETSLOT)],
        'queue_count_8_to_3': q2.get('count') == 3,
        'queue_read_cursor_0_to_5': q2.get('read_cursor') == STOCK_WORDS,
    }
    observations['s2_dequeue_stock'] = s2
    if not all(s2['checks'].values()):
        observations['wall'] = {'type': 's2_stock_dequeue_failed',
                                'failed': [k for k, v in s2['checks'].items() if not v]}
        return observations

    r0_3, steps3, hits3 = driver.direct_call(
        FILTER, [16, block], max_steps=100_000, watch=FILTER_STOCK_WATCH,
        label='s3:filter_stock')
    retslot_after = read_u32(cpu, STOCK_RETSLOT)
    cb_hit = hit_at(hits3, STOCK_CALLBACK)
    cb_call_hit = hit_at(hits3, FILTER_CB_N1)
    s3 = {
        'entry': hex(FILTER), 'abi': 'r0=16,r1=block（0x6064cce/d0 真码同形）',
        'returned': r0_3, 'steps': steps3,
        'callback_call_site_r1': cb_call_hit['r'][1] if cb_call_hit else None,
        'callback_r0_at_entry': cb_hit['r'][0] if cb_hit else None,
        'retslot_126270_after': hexed(retslot_after),
        'watch_hit_pcs': sorted(covered_pcs(hits3)),
    }
    s3['checks'] = {
        # 断言 C②:内联回调执行（call site r1=出队回调字+裸 rts 的真回调 PC 命中）
        # +返回值回写（bit8 臂 0x6064c72 命中；回调返 r0=参数 0→*0x126270=0）
        # +过滤返 1=已消费。
        'stock_callback_call_target_is_dequeued_word': bool(
            cb_call_hit and cb_call_hit['r'][1] == hex(STOCK_CALLBACK)),
        'stock_callback_ran': cb_hit is not None,
        'stock_retslot_write_covered': hex(FILTER_RETSLOT) in covered_pcs(hits3),
        'stock_retslot_value_is_param': retslot_after == STOCK_PARAM,
        'stock_filter_consumed_return_1': r0_3 == 1,
    }
    observations['s3_filter_stock'] = s3
    if not all(s3['checks'].values()):
        observations['wall'] = {'type': 's3_stock_filter_failed',
                                'failed': [k for k, v in s3['checks'].items() if not v]}
        return observations

    # ---------------- 步 4/5:出队档（按键事件出队+过滤放行） ----------------
    r0_4, steps4, hits4 = driver.direct_call(
        DEQUEUE, [queue + 8, 16, block], max_steps=100_000, watch=DEQ_WATCH,
        label='s4:dequeue_key')
    q4 = task_queue_face(cpu, queue)
    blk4 = [hexed(read_u32(cpu, block + 4 * i)) for i in range(3)]
    s4 = {
        'entry': hex(DEQUEUE), 'returned': r0_4, 'steps': steps4,
        'block_words': blk4, 'queue_after': q4,
        'watch_hit_pcs': sorted(covered_pcs(hits4)),
    }
    s4['checks'] = {
        # 断言 K①:按键事件出队（block={0x10000C,载荷字,0}+计数 3→0+读游标 5→8）。
        'key_block_shape': blk4 == [hex(POST_EVENT_ID), hex(payload), '0x0'],
        'queue_count_3_to_0': q4.get('count') == 0,
        'queue_read_cursor_5_to_8': q4.get('read_cursor') == 8,
    }
    observations['s4_dequeue_key'] = s4
    if not all(s4['checks'].values()):
        observations['wall'] = {'type': 's4_key_dequeue_failed',
                                'failed': [k for k, v in s4['checks'].items() if not v]}
        return observations

    r0_5, steps5, hits5 = driver.direct_call(
        FILTER, [16, block], max_steps=100_000, watch=FILTER_KEY_WATCH,
        label='s5:filter_key')
    blk5 = [hexed(read_u32(cpu, block + 4 * i)) for i in range(3)]
    s5 = {
        'entry': hex(FILTER), 'returned': r0_5, 'steps': steps5,
        'block_words_after': blk5,
        'watch_hit_pcs': sorted(covered_pcs(hits5)),
    }
    s5['checks'] = {
        # 断言 K②:普通事件放行（返 0=get_event 的 13 交付语义；block 原样；
        # 回调执行点=负证据——0x10000C 不是 0x300000 族）。
        'key_filter_returned_zero': r0_5 == 0,
        'key_block_intact': blk5 == blk4,
        'no_callback_executed': hex(FILTER_CB_N1) not in covered_pcs(hits5),
    }
    observations['s5_filter_key'] = s5
    if not all(s5['checks'].values()):
        observations['wall'] = {'type': 's5_key_filter_failed',
                                'failed': [k for k, v in s5['checks'].items() if not v]}
        return observations

    # ---------------- 步 6:泵分发档（0x60123f4 续行→handler→动作） ----------------
    # block=步 4 真出队落在 scratch 的字原样搬运（不再从常量重造），写在停点
    # sp 下方死帧窗（泵读 [sp]/[sp+4]；活栈不可写——SIMB1F 死帧窗口径同款区域）。
    seg_sp = (cpu.sp if isinstance(cpu.sp, int) else 0x1237d8) - 0x20
    reg_face = {4: seg_sp + R4_OFF, **PUMP_REG_FACE_DEFAULT}
    dequeued_block = [read_u32(cpu, block + 4 * i) for i in range(3)]
    ring_before = ring_face(cpu)
    ram_before = dict(cpu.mem.ram)
    seg = segment_call(
        cpu, machine, PUMP_CONT, reg_face,
        {'sp': seg_sp, 'block': dequeued_block},
        stop_pcs=(PUMP_LOOP_HEAD, PUMP_EVENT_DONE), watch=PUMP_WATCH,
        max_steps=max_steps_seg, label='s6:pump_dispatch_%#x' % payload)
    stop_pc, r_face, steps6 = seg['stop_pc'], seg['r_face'], seg['steps']
    hits6, seg_wall = seg['watch_hits'], seg['wall']
    cov6 = covered_pcs(hits6)
    ring_after = ring_face(cpu)
    heap_touched = sum(1 for a, v in cpu.mem.ram.items()
                       if ram_before.get(a) != v and 0x134500 <= a < 0x19FB80)
    q6 = task_queue_face(cpu, queue)
    handler_hit = hit_at(hits6, KEY_HANDLER)
    cmd1_hit = hit_at(hits6, CMD1_BRANCH)
    special_exit_hit = hit_at(hits6, SPECIAL_EXIT)
    t2r0_hit = hit_at(hits6, TABLE2_R0)
    s6 = {
        'entry': hex(PUMP_CONT),
        'register_face': ('r4=sp+4,r7=0x60A8C68,r8=QCY,r9=611,r10=0x60A89E0,'
                          'r11=0x60A8C68,r12=0x10000C,r13=0x60A8CB0'
                          '（0x60123c0..0x60123e0 prologue 常量）;block@%#x'
                          '=步4真出队产物' % seg_sp),
        'stop_pc': hexed(stop_pc), 'stop_r_face': r_face, 'steps': steps6,
        'wall': seg_wall,
        'special_path': {'entered': hex(SPECIAL_PATH) in cov6,
                         'exit_r5': special_exit_hit['r'][5] if special_exit_hit else None},
        'table2_walk': {'record0_handler_ran': t2r0_hit is not None,
                        'key_handler_ran': handler_hit is not None,
                        'key_handler_r0_payload_ptr': handler_hit['r'][0] if handler_hit else None},
        'handler_chain': {
            'bit16_gate_passed': hex(H_BIT16_GATE) in cov6,
            'tbb_reached': hex(H_TBB) in cov6,
            'swallow_gate_not_taken': hex(H_SWALLOW_GATE) in cov6,
            'second_gate_query_ran': hex(H_SECOND_GATE) in cov6,
            'mode_query_ran': hex(H_MODE_QUERY) in cov6,
            'map_hit_action_loaded': hex(H_MAP_HIT) in cov6,
            'cmd1_branch_taken': hex(CMD1_BRANCH) in cov6,
            'ret_handled_path': hex(H_RET_HANDLED) in cov6,
        },
        'action_ring': {'before': ring_before, 'after': ring_after},
        'tone_thunk_covered': hex(TONE_THUNK) in cov6,
        'queue_after': q6, 'heap_bytes_touched': heap_touched,
        'watch_hit_pcs': sorted(cov6),
    }
    rb, ra = ring_before, ring_after
    cursor_advanced = (rb.get('ring_ptr') and rb['ring_ptr'] == ra.get('ring_ptr')
                       and isinstance(rb.get('cursor_b0'), int)
                       and isinstance(ra.get('cursor_b0'), int)
                       and ra['cursor_b0'] == rb['cursor_b0'] + len(b'music_pp') + 1)
    name_in_ring = False
    if ra.get('bytes_40'):
        flat = bytes(ra['bytes_40'])
        name_in_ring = b'music_pp' in flat
    s6['checks'] = {
        # 断言 D①:分发命中（特路进入+子码放行 exit r5==0+表2[0] 先行 handler
        # 执行后续 walk+按键 handler 以 r0=载荷指针被调）。
        'special_path_entered_and_released': (s6['special_path']['entered']
                                              and s6['special_path']['exit_r5'] == '0x0'),
        'table2_record0_ran': t2r0_hit is not None,
        'key_handler_called_with_payload': bool(
            handler_hit and int(handler_hit['r'][0], 16) == seg_sp + R4_OFF),
        # 断言 D②:handler 执行（门链 PC+cmd1 分支）——模式门/吞键门过与否由
        # 真码读真全局决定,不过门=诚实结果(断言面记录实际走向)。
        'handler_internal_chain': (s6['handler_chain']['bit16_gate_passed']
                                   and s6['handler_chain']['tbb_reached']),
        'cmd1_branch_reached': cmd1_hit is not None,
        # 断言 D③:动作可观测（环游标 +9、'music_pp' 字节落环）。
        'ring_cursor_advanced_by_namelen': cursor_advanced,
        'music_pp_bytes_in_ring': name_in_ring,
        # 断言 D④:事件终结（停点=0x6012422 事件终结支,handler 返 1）。
        'stopped_at_event_done': stop_pc == PUMP_EVENT_DONE,
        'handler_returned_one': bool(r_face and r_face[0] == '0x1'),
        # 提示音沿真码到达（播放成败不进必要条件——SEC-01 两档裁定口径）。
        'tone_path_reached': hex(TONE_THUNK) in cov6,
    }
    observations['s6_pump_dispatch'] = s6

    # ---------------- 步 6b:默认表 fixture 档（动作分支可达性） ----------------
    # s6 世界真值=映射表全零（config 48 装载在擦除态世界被跳过——config 523 旁路
    # 同族,S 路 §7.1 fixture 先例）：查表落空→handler 返 0。本档把 flash 默认表
    # 45B（0x609e250——0x60254ce 的装载源,真固件数据非发明）装入 RAM 映射表后
    # 重跑 post→出队→过滤→泵分发,取动作分支（cmd1→发布器→提示音）执行证据。
    default_map = read_bytes(cpu, DEFAULT_MAP_FLASH, 45)
    driver.mem_write(MAP_TABLE, default_map)
    r0_1b, steps1b, hits1b = driver.direct_call(
        POST_API, [payload, 0], max_steps=200_000, watch=POST_WATCH,
        label='s6b:post_%#x' % payload)
    r0_4b, _steps4b, _hits4b = driver.direct_call(
        DEQUEUE, [queue + 8, 16, block], max_steps=100_000, watch=DEQ_WATCH,
        label='s6b:dequeue_key')
    blk4b = [read_u32(cpu, block + 4 * i) for i in range(3)]
    r0_5b, _steps5b, _hits5b = driver.direct_call(
        FILTER, [16, block], max_steps=100_000, watch=FILTER_KEY_WATCH,
        label='s6b:filter_key')
    seg_sp_b = (cpu.sp if isinstance(cpu.sp, int) else 0x1237d8) - 0x20
    reg_face_b = {4: seg_sp_b + R4_OFF, **PUMP_REG_FACE_DEFAULT}
    ring_before_b = ring_face(cpu)
    seg_b = segment_call(
        cpu, machine, PUMP_CONT, reg_face_b,
        {'sp': seg_sp_b, 'block': blk4b},
        stop_pcs=(PUMP_LOOP_HEAD, PUMP_EVENT_DONE), watch=PUMP_WATCH,
        max_steps=max_steps_seg, label='s6b:pump_dispatch_%#x' % payload)
    cov6b = covered_pcs(seg_b['watch_hits'])
    ring_after_b = ring_face(cpu)
    publisher_hit = hit_at(seg_b['watch_hits'], PUBLISHER)
    handler_hit_b = hit_at(seg_b['watch_hits'], KEY_HANDLER)
    s6b = {
        'note': ('fixture 档:RAM 映射表 @%#x 装入 flash 默认表 45B@%#x'
                 '（0x60254ce 装载源;擦除态世界 config 48 缺档致原表全零——'
                 'synthetic-load-of-real-bytes,同 S 路 config 523 fixture 先例）'
                 % (MAP_TABLE, DEFAULT_MAP_FLASH)),
        'default_map_bytes': list(default_map),
        'map_table_after': [list(read_bytes(cpu, MAP_TABLE + 3 * i, 3) or [])
                            for i in range(15)],
        'post_returned': r0_1b, 'dequeue_block': [hexed(w) for w in blk4b],
        'filter_returned': r0_5b,
        'stop_pc': hexed(seg_b['stop_pc']), 'stop_r_face': seg_b['r_face'],
        'steps': seg_b['steps'], 'wall': seg_b['wall'],
        'handler_r0_payload_ptr': handler_hit_b['r'][0] if handler_hit_b else None,
        'publisher': {'entered': publisher_hit is not None,
                      'r0': publisher_hit['r'][0] if publisher_hit else None,
                      'r1_name_ptr': publisher_hit['r'][1] if publisher_hit else None},
        'action_ring': {'before': ring_before_b, 'after': ring_after_b},
        'tone_thunk_covered': hex(TONE_THUNK) in cov6b,
        'watch_hit_pcs': sorted(cov6b),
    }
    s6b['checks'] = {
        # 断言 F①:fixture 后查表命中→cmd1 分支（映射机制+动作分发动真码）。
        'map_hit_action_loaded': hex(H_MAP_HIT) in cov6b,
        'cmd1_branch_taken': hex(CMD1_BRANCH) in cov6b,
        # 断言 F②:发布器以真名串指针被调（r1=0x60a1cc0 'music_pp'——flash 直读互证）；
        # park 无环对象（[QCY+416]=0 世界真值）→发布器 r7==0 门早退=诚实边界,
        # 环写入本身归有环世界（D1 窗口/后段前沿）。
        'publisher_called_with_music_pp': bool(
            publisher_hit and publisher_hit['r'][1] == hex(CMD1_NAME_ADDR)),
        'ring_absent_in_park': ring_after_b.get('ring_ptr') == '0x0',
        # 断言 F③:提示音沿真码到达（播放成败不进必要条件——SEC-01 两档裁定）。
        'tone_path_reached': hex(TONE_THUNK) in cov6b,
        # 断言 F④:事件终结（停点=0x6012422,handler 返 1）。
        'stopped_at_event_done': seg_b['stop_pc'] == PUMP_EVENT_DONE,
        'handler_returned_one': bool(seg_b['r_face'] and seg_b['r_face'][0] == '0x1'),
    }
    observations['s6b_pump_dispatch_fixture'] = s6b
    return observations


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--payload', type=lambda s: int(s, 0), default=PAYLOAD_DEFAULT,
                        help='按键事件载荷字（默认 0x10a00=key10 单击,S 路实测值）')
    parser.add_argument('--max-seg-steps', type=int, default=10_000_000,
                        help='步 6 泵分发段步数预算')
    parser.add_argument('--capture-park', action='store_true',
                        help='重捕 park 前沿快照(gpcnt_num_value=None,停回 29,601,125@'
                             '0x606955c;快照开关集一致性——捕获与还原共同前提,'
                             'CALLIMPL 同款惯例)')
    parser.add_argument('--out', default='analysis/local-checks/keycons/scene.json')
    args = parser.parse_args()

    from sim_world_boot import build_world
    t0 = time.time()

    def serve_loop(machine, hardware, cpu, stop=None):
        return run_scene(machine, hardware, cpu, args.payload, args.max_seg_steps)

    result = build_world(serve_loop,
                         capture=args.capture_park,
                         flag_overrides={'gpcnt_num_value': None},
                         rom_path='/home/felix021/tmp/QCY_H3S_BR28_ROM_000000-020000.bin',
                         firmware_path='firmware/QCY_H3S_7018F_1T2_release_note_V1.1.8_011.ufw',
                         snapshot_dir='analysis/snapshots')
    payload = {'elapsed_s': round(time.time() - t0, 1),
               'stop': result.get('stop'),
               'completed_steps': result.get('completed_steps'),
               'scene': {k: v for k, v in result.items()
                         if k in ('banner', 'world_prep', 'wall', 's1_post',
                                  's2_dequeue_stock', 's3_filter_stock',
                                  's4_dequeue_key', 's5_filter_key',
                                  's6_pump_dispatch', 's6b_pump_dispatch_fixture')}}
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=1, default=str), encoding='utf-8')

    f0 = payload['scene'].get('world_prep', {})
    s1 = payload['scene'].get('s1_post', {})
    s2 = payload['scene'].get('s2_dequeue_stock', {})
    s3 = payload['scene'].get('s3_filter_stock', {})
    s4 = payload['scene'].get('s4_dequeue_key', {})
    s5 = payload['scene'].get('s5_filter_key', {})
    s6 = payload['scene'].get('s6_pump_dispatch', {})
    s6b = payload['scene'].get('s6b_pump_dispatch_fixture', {})
    print(json.dumps({'elapsed_s': payload['elapsed_s'],
                      'wall': payload['scene'].get('wall'),
                      'prep_checks': f0.get('checks'),
                      'mode_probe': f0.get('mode_query_probe'),
                      'tone_handle': f0.get('tone_handle_QCY924'),
                      'queue0': f0.get('queue'),
                      's1_checks': s1.get('checks'),
                      's2_checks': s2.get('checks'),
                      's3_checks': s3.get('checks'),
                      's4_checks': s4.get('checks'),
                      's5_checks': s5.get('checks'),
                      's6_checks': s6.get('checks'),
                      's6_stop': s6.get('stop_pc'), 's6_special': s6.get('special_path'),
                      's6_walk': s6.get('table2_walk'),
                      's6_ring_after': (s6.get('action_ring') or {}).get('after'),
                      's6_wall': s6.get('wall'),
                      's6b_checks': s6b.get('checks'),
                      's6b_stop': s6b.get('stop_pc'),
                      's6b_publisher': s6b.get('publisher'),
                      's6b_wall': s6b.get('wall'),
                      'evidence': str(out)}, ensure_ascii=False, indent=1))
    return 0


if __name__ == '__main__':
    sys.exit(main())
