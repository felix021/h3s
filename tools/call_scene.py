#!/usr/bin/env python3
"""CALLIMPL 场景 runner:park 停点世界上的通话态注入(预研 §6 步 0-2+TX 首跑+负例)。

docs/call-state-pre-study.md §6 实施切分的前三步执行载体(D2AJ/d2_audio_scene
模式:唯一合成输入=3 字节帧,walker/分发器/poster/handler/创建体全部真实固件执行):

  步 0 world_prep:park 面读回——b[QCY+8087/8088/8089]、h[QCY+2616]、
      b[QCY+2618/2619/2620]、[QCY+2600/2604/2608]、[QCY+620/624]。
  步 1 直调 walker 0x603573e:入口 ABI=反汇编钉死(r1=帧缓冲,r2=arg2——
      0x6035744 r4=r1/0x6035760 r5=r2.b0/0x6035766 r1_r0=r5_r4 把
      (帧,arg2.b0) 传给回调数组 {0x601be6e,0x60222b2}@0x60a89d8)。帧=
      {144,83,chmap}(chmap≠0,预研 §4)→ 分发器 case 83 写 chmap+h[2616]=50
      →仅 post 0x100009(分发器层不 relay,负证据断言)。
  步 2 直调 handler 0x60207a2(8B 参数缓冲{83,0,...}):**定位法**(预研
      §5.3(ii)——跳过队列语义/事件所有权/调度时序,不进验收),两种分段:
      handler 档全程跑(TX 段经路 UARTTX 放行后完成返回);post-tx 档从
      0x6020fc6 续行(跳过 TX relay 段,入口寄存器面静态复现:r13/r7=
      prologue 常量、r5 真码自载)取创建体/出口链/aec 门证据。
      case 83 体全链:bit0x20 清零支路
      0x601d8a6→TX relay 0x6020fbe→0x601b85c({144,83},2)→task_create
      ('enc_mpt_sel')→zalloc(32)→[QCY+624] 落槽→popcount 通道节点环→
      b[obj+0]=1→共享出口 0x60212ba 链式调 0x60200b6(chmap)→返回 0=对象
      存活。真实泵消费(队列出队进 handler)=M1-c 世界重钉后,本路如实
      BLOCKED 分档。
  步 3 TX 支路实证(handler 档内 0x6020fbe 段,路 UARTTX 已放行):0x601b85c
      门(b[QCY+2619]==0 且 h[QCY+2616]≠0)→表[+16](1) 开链 0x60343e8(已初始
      化即 no-op)→会话 vtable[+8]=帧构建器 0x6060254({0x55,0xAA,len,payload...,
      checksum} 包络)→DMA 发送 0x603413c→**TXADR 0xfd2014=会话缓冲+TXCNT
      0xfd2018=包络总长(2+4=6)两写落 JLUartTxStartWriteRecords 记录**→
      b[obj+80]==0(轮询档)→函数直接返回(0x6034266→0x6034172,零等待)→
      0x601b85c 清 b[QCY+2619]+关链→返 0——断言⑤后半(0x6020fc6 续行=TX
      relay 完成返回)过。完成语义钉死(路 UARTTX 探针):同步路径启动后不等
      任何完成位;完成消费面=中断域(IRQ12 handler 0x60354e0 TX-done 支,
      CON0 bit15+bit2 键控)与启动前等待环 CON0 bit0(经 0x12cb08 返 0 出口
      立即退出)——**不发明完成投影**(无读时投影位/无 advance 置位/无 IRQ12
      交付件),CON0 保持路 F「读回=最后软件写值」。
  负例 --chmap 0:事件照达(分发器不设门)、创建被 handler 内 r5==0 真门拦
      (PC 覆盖 0x6020fd2 分支,[QCY+624] 保持 0;出口仍调 0x60200b6(0)=未钉
      项 9,观测其返回与 [QCY+620] 副作用)。

断言面=预研 §4 验收判据草案 ①-⑧(walker 段①②③+handler 段④⑤⑥⑦⑧),
PC watch 全部为反汇编钉死的真实指令地址(本文件不发明地址/状态)。

用法:python3 tools/call_scene.py [--capture-park|--capture] [--chmap 0x05] [--s2-entry ...]
  默认 restore analysis/snapshots 的 park 快照(gpcnt_num 关——快照开关集一致性,
  见 --capture-park 注);证据 JSON 落 analysis/local-checks/callimpl/scene.json
  (git 忽略)。负例:python3 tools/call_scene.py --chmap 0 --out .../scene_chmap0.json
  TX 后分段诊断:python3 tools/call_scene.py --s2-entry post-tx --out .../scene_posttx.json
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

# ---- 固件地址/形状(预研 §1/§6 钉死+本路反汇编复核,不发明) ----
WALKER = 0x603573e              # 传输回调 walker(vtable 槽 [0x124C80+280] 等价直调)
WALKER_H2616_STORE_10 = 0x6035750    # h[QCY+2616]=10(store 前命中:r0=1308,r1=QCY,r3=10)
WALKER_ERR_TX = 0x6035770       # 双回调返 0 的 {255} TX 支路(正例不得命中)
DISPATCHER_144 = 0x601be6e      # {144,code} 分发器(gate b[buf+0]==144)
CASE83_DISPATCHER = 0x601c314   # 分发器 case 83 体入口
CHMAP_STORE = 0x601c31a         # b[QCY+8087]=chmap(chmap 唯一写点;命中时 r0=chmap)
H2616_STORE_50 = 0x601c324      # h[QCY+2616]=50(命中时 r0=1308,r1=50)
POST_TAIL = 0x601c00c           # 共享尾 call 0x601b944(r0=code,r1=0)
POSTER = 0x601b944              # 0x100009 唯一 poster
POST_INNER = 0x606487c           # os_taskq_post 内层(b[QCY+52] 早退门→任务查找→入队)
POST_MAIN = 0x60648a2            # 内层主路径(b[QCY+52]==0 才到达:临界区+任务查找)
POST_TASK_FOUND = 0x60648b0      # 任务查找命中后的队列几何路径(h[r4+12] 容量门)
POST_PAIR_HEAD = 0x60648c0       # 入队字写并行对头(`r3=r0.b0 #`∥`r1=h[r4+8]`)——
                                  # watch 只见对头;第二半 0x60648c2(SIMB1C 的
                                  # POSTER_QUEUE_READ)作为独立 PC 永不命中(PARSTOR
                                  # 并行对语义),r4=队列对象在对头同样可取
POST_RESULT_OBS = 0x606498a      # 该 PC 处 r0=内层 post 返回值(错误码诊断面)
TX_API = 0x601b85c              # UART transport 发送 API(分发器层不得命中)
SECOND_CALLBACK = 0x60222b2     # walker 第二回调(144 族返 1→自然跳过,负证据)
HANDLER = 0x60207a2             # app_core 事件 0x100009 handler(r0=参数缓冲)
TBH_0_30 = 0x602082c            # 表二 tbh(83 不经此,负证据)
TBH_129_160 = 0x60207d8         # 表三 tbh(83 不经此,负证据——D2AJ 勘误口径)
TBH_82_93 = 0x6020894           # 表一 tbh(83→0x6020f52,必经)
CASE83_HANDLER = 0x6020f52      # handler case 83 体入口
GPIO_FN = 0x601b960             # 0x601b960(1):0xfd5100 RMW 支路
UART_CON_FN = 0x601d8a6         # 0x601d8a6(1,1):bit0x20 清零支路(UART CON 写)
TX_RELAY_SITE = 0x6020fbe       # handler 侧 TX relay 调用点({144,83} 帧)
POST_TX_CONT = 0x6020fc6        # TX relay 调用后的续行首站——双语义:handler 档
                                 # 命中=TX relay 完成返回的证据;post-tx 档=分段
                                 # 诊断入口(r5 在 0x6020fca 由真码从 b[QCY+8087]
                                 # 自载;r13/r7 为 prologue 常量 0x60207b8/0x60207c6)
POST_TX_PC = POST_TX_CONT       # handler 档断言用的可读别名(同一 PC)
R5_GATE = 0x6020fd2             # r5==0 硬门(命中时 r5=chmap)
TASK_CREATE = 0x6067242         # task_create_byname(entry,name='enc_mpt_sel')
TASK_CREATE_RET_OBS = 0x6020ffa  # 该 PC 处 r0 仍是 task_create 返回值(下条才改写 r0=32)
AEC_SLOT_STORE = 0x6021002      # [QCY+624]=r0(aec 前端对象唯一写点)
ACTIVE_STORE = 0x60210f6        # b[obj+0]=1(消费者 0x6033cb4 活跃门)
SHARED_EXIT = 0x60212ba         # 共享出口(r0=r5=chmap→call 0x60200b6)
ESCO_OPEN = 0x60200b6           # esco 开链(chmap 兼作 flags;返 0=对象存活)
ESCO_RET_OBS = 0x60212c0        # 该 PC 处 r0=0x60200b6 返回值(断言⑧)
HANDLER_RETURN = 0x602125e      # handler 正常返回路径(r0=0)
TEARDOWN = 0x601ca3e            # 旧对象拆除(首注入不得命中)
R13_QCY = 0x1265C0              # handler prologue r13 常量(0x60207b8)
R7_TABLE_BASE = 0x60A18C0       # handler prologue r7 常量(0x60207c6;+1554=名串)
TASK_NAME_ADDR = 0x60a1ed2      # 'enc_mpt_sel' 名串(flash,读回互证)
QCY = 0x1265C0
HEAP_BEGIN, HEAP_END = 0x134500, 0x19FB80

CHMAP_DEFAULT = 0x05            # 预研 §4 首选(bit0+bit2,N=2,esco flags 同 b1 先例)

S1_WATCH = (WALKER_H2616_STORE_10, DISPATCHER_144, CASE83_DISPATCHER, CHMAP_STORE,
            H2616_STORE_50, POST_TAIL, POSTER, POST_WRAPPER, POST_PAIR_HEAD,
            POST_INNER, POST_MAIN, POST_TASK_FOUND, POST_RESULT_OBS,
            TX_API, SECOND_CALLBACK, WALKER_ERR_TX)
S2_WATCH = (HANDLER, TBH_0_30, TBH_129_160, TBH_82_93, CASE83_HANDLER, GPIO_FN,
            UART_CON_FN, TX_API, TX_RELAY_SITE, POST_TX_CONT, R5_GATE, TASK_CREATE,
            TASK_CREATE_RET_OBS, AEC_SLOT_STORE, ACTIVE_STORE, SHARED_EXIT,
            ESCO_OPEN, ESCO_RET_OBS, HANDLER_RETURN, TEARDOWN)


def read_bytes(cpu, address, size):
    # MMU 逐页分块读(d2_audio_scene 同款翻译面)。
    out = bytearray()
    while size:
        chunk = size if (address & 127) + size <= 128 else 128 - (address & 127)
        out += cpu.mem.read(address, chunk)
        address += chunk
        size -= chunk
    return bytes(out)


def read_u32(cpu, address):
    return int.from_bytes(read_bytes(cpu, address, 4), 'little')


def read_u16(cpu, address):
    return int.from_bytes(read_bytes(cpu, address, 2), 'little')


def read_u8(cpu, address):
    return read_bytes(cpu, address, 1)[0]


def popcount15(value):
    """chmap 低 15 位 popcount(创建体 0x6021010 uextra(p:1,l:15) 同域)。"""
    return bin(value & 0x7FFF).count('1')


def compose_frame(chmap):
    """最小通话事件帧(预研 §4):{b0=144, b1=83, b2=chmap}——唯一合成输入。"""
    if not 0 <= chmap <= 0xFF:
        raise ValueError('chmap must be one frame byte, got %#x' % chmap)
    return bytes([144, 83, chmap])


def covered_pcs(hits):
    return {h['pc'] for h in hits}


def hit_at(hits, pc):
    """pc 的首次命中记录(driver watch 的 pc 是 hex 字符串)。"""
    want = hex(pc)
    return next((h for h in hits if h['pc'] == want), None)


def queue_face_raw(cpu, queue_base):
    """app_core 队列观察面(0x60648b0 钉死几何:+8 头/+12 容量/+14 计数/+16 缓冲)。

    直读 cpu(driver 世界死亡后仍可读;与 SimWorldDriver._queue_face 同形)。
    缓冲逐字容错:未初始化字记 'uninit'(严格模型的诚实面,不发明零)。"""
    if queue_base is None:
        return None

    def tolerant_u16(address):
        try:
            return read_u16(cpu, address)
        except Exception:
            return None

    def tolerant_u32(address):
        try:
            return read_u32(cpu, address)
        except Exception:
            return None

    head = tolerant_u16(queue_base + 8)
    cap = tolerant_u16(queue_base + 12)
    count = tolerant_u16(queue_base + 14)
    buf = tolerant_u32(queue_base + 16)
    words = []
    if isinstance(head, int) and isinstance(cap, int) and isinstance(buf, int):
        for i in range(min(head + 8, cap)):
            word = tolerant_u32(buf + 4 * i)
            words.append(hex(word) if isinstance(word, int) else 'uninit')
    return {'queue': hex(queue_base), 'count': count, 'head': head, 'capacity': cap,
            'buffer': hex(buf) if isinstance(buf, int) else None,
            'buffer_words': words}


def node_ring_walk(cpu, obj):
    """obj+24 双向环形链(哨兵=&obj+24)遍历:节点原始面记录(≤8 防环)。"""
    head = obj + 24
    nodes = []
    node = read_u32(cpu, head)
    while node != head and len(nodes) < 8:
        nodes.append({'node': hex(node),
                      'next': hex(read_u32(cpu, node)),
                      'prev': hex(read_u32(cpu, node + 4)),
                      'mask_h8': hex(read_u16(cpu, node + 8)),
                      'cursor_h10': hex(read_u16(cpu, node + 10)),
                      'buf_16': hex(read_u32(cpu, node + 16)),
                      'ptr_20': hex(read_u32(cpu, node + 20))})
        node = read_u32(cpu, node)
    return nodes


def world_prep(cpu):
    """步 0:park 面读回(预研 §6 步 0 清单;预期值只钉预研已钉项,其余如实记录)。

    逐字段容错:park 从未写过的字节按严格模型拒读,记 'uninitialized_read'
    (诚实面;预研 §7-6 预期 chmap=0 即属「boot 零写」类)。"""

    def u8(address):
        try:
            return read_u8(cpu, address)
        except Exception:
            return 'uninitialized_read'

    def u16(address):
        try:
            return read_u16(cpu, address)
        except Exception:
            return 'uninitialized_read'

    def u32hex(address):
        try:
            return hex(read_u32(cpu, address))
        except Exception:
            return 'uninitialized_read'

    face = {
        'chmap_b_QCY8087': u8(QCY + 8087),
        'call_flag_b_QCY8088': u8(QCY + 8088),
        'call_flag_b_QCY8089': u8(QCY + 8089),
        'link_state_h_QCY2616': u16(QCY + 2616),
        'tx_state_b_QCY2618': u8(QCY + 2618),
        'tx_busy_b_QCY2619': u8(QCY + 2619),
        'post_gate_b_QCY52': u8(QCY + 52),
        'cfg_table_QCY2600': u32hex(QCY + 2600),
        'session_QCY2604': u32hex(QCY + 2604),
        'session_QCY2608': u32hex(QCY + 2608),
        'ready_b_QCY2620': u8(QCY + 2620),
        'esco_slot_QCY620': u32hex(QCY + 620),
        'aec_slot_QCY624': u32hex(QCY + 624),
    }
    face['checks'] = {
        # 预研 §7-6/§1.4 已钉预期:无通话事件→chmap=0、aec/esco 槽=0;
        # b[QCY+2619]==0 是 0x601b85c 发送门的前置(§1.4 表);
        # [QCY+2600] 配置表=0x609d9c4(BTMAP §8.1)。其余(h[2616]/会话/就绪位)
        # park 具体值未钉,只记录不断言。
        'chmap_slot_zero': face['chmap_b_QCY8087'] == 0,
        'aec_slot_zero': face['aec_slot_QCY624'] == '0x0',
        'esco_slot_zero': face['esco_slot_QCY620'] == '0x0',
        'tx_busy_gate_open': face['tx_busy_b_QCY2619'] == 0,
        'cfg_table_is_0x609d9c4': face['cfg_table_QCY2600'] == '0x609d9c4',
    }
    return face


def run_scene(machine, hardware, cpu, chmap, max_steps_s2, s2_entry='handler',
              max_steps_b3=400_000):
    driver = SimWorldDriver(machine, hardware, cpu)
    observations = {'banner': {
        'stop_pc': hex(cpu.pc), 'core': cpu.core_id,
        'active_context': getattr(cpu, 'active_context', None),
        'in_irq_context_banner': getattr(cpu, 'active_context', None),
        'chmap': hex(chmap), 'frame': [144, 83, chmap], 's2_entry': s2_entry,
        'synthetic_inputs': [
            'frame bytes {144,83,chmap} (synthetic-not-measured, UART 物理输入未验证)',
            's2 定位法:handler 档=param buffer {83,0,..} 跳过队列语义;'
            'post-tx 档=0x6020fc6 续行跳过 TX relay 段(TX 段由 handler 档单独实证'
            '至 TX 启动(路 UARTTX 放行 TXADR/TXCNT 写侧记录,完成零等待);入口寄存器'
            ' r13/r7=prologue 常量,r5 真码自载)——均不进验收'],
        'park_anchor': ('snapshot frontier 29,601,125@0x606955c(快照 restore 服务面);'
                        '条件前沿 32,737,356(watchdog)不在本路锚定——前沿已前移但'
                        'park 快照仍是可直接 restore 的最新可服役停点(SIMB1G 记录)')}}

    # ---------------- 步 0:world_prep ----------------
    observations['world_prep'] = world_prep(cpu)

    # ---------------- 步 1:直调 walker(帧注入) ----------------
    frame = compose_frame(chmap)
    frame_addr = driver.scratch_body
    driver.mem_write(frame_addr, frame)
    try:
        r0, steps, hits = driver.direct_call(
            WALKER, [0, frame_addr, 0], max_steps=2_000_000,
            watch=S1_WATCH, label='s1:walker_{144,83,%#x}' % chmap)
    except Exception as exc:                                  # WorldDead/DriverError 如实记墙
        observations['s1_walker'] = {'wall': {'type': type(exc).__name__,
                                              'detail': str(exc)[:1200],
                                              'detail_dict': getattr(exc, 'detail', None)}}
        return observations
    covered = covered_pcs(hits)
    # app_core 队列基址:入队字写并行对头 0x60648c0 处 r4=队列对象(0x60648b0
    # 起钉死几何:+8 头/+12 容量/+14 计数/+16 缓冲)。不走 driver._note_queue_watch
    # 的 heap 域守卫——任务对象可落在 0x41xxxx RAM 面(heap 检查会误拒)。
    pqr_hit = hit_at(hits, POST_PAIR_HEAD)
    queue_base = None
    if pqr_hit and pqr_hit['r'][4] is not None:
        try:
            queue_base = int(pqr_hit['r'][4], 16)
        except ValueError:
            queue_base = None
    face1 = queue_face_raw(cpu, queue_base)
    if face1 is not None and not (isinstance(face1['capacity'], int)
                                  and 0 < face1['capacity'] <= 4096
                                  and isinstance(face1['buffer'], str)):
        face1 = {**face1, 'note': 'queue geometry sanity failed—base kept for record only'}
        queue_base = None
    order10 = hit_at(hits, WALKER_H2616_STORE_10)
    order50 = hit_at(hits, H2616_STORE_50)
    chmap_hit = hit_at(hits, CHMAP_STORE)
    post_result_hit = hit_at(hits, POST_RESULT_OBS)
    task_found_hit = hit_at(hits, POST_TASK_FOUND)
    task_queue_geometry = None
    if task_found_hit and task_found_hit['r'][4] is not None:
        try:
            task_obj = int(task_found_hit['r'][4], 16)
            task_queue_geometry = {
                'task_obj': hex(task_obj),
                'capacity_h12': read_u16(cpu, task_obj + 12),
                'count_h14': read_u16(cpu, task_obj + 14),
                'head_h8': read_u16(cpu, task_obj + 8),
                'buf_16': hex(read_u32(cpu, task_obj + 16)),
            }
        except Exception:
            task_queue_geometry = {'note': 'queue geometry read blocked'}
    s1 = {
        'entry': hex(WALKER),
        'abi': 'r1=frame, r2=arg2(0)——0x6035744/0x6035760/0x6035766 反汇编钉死',
        'returned': r0, 'steps': steps,
        'h_QCY2616_final': read_u16(cpu, QCY + 2616),
        'chmap_b_QCY8087_final': read_u8(cpu, QCY + 8087),
        'dispatcher_b_QCY8072': read_u8(cpu, QCY + 8072),
        'dispatcher_b_QCY8076': read_u8(cpu, QCY + 8076),
        'post_path': {'inner': hex(POST_INNER) in covered,
                      'main_b52_gate_passed': hex(POST_MAIN) in covered,
                      'task_found': hex(POST_TASK_FOUND) in covered,
                      'queue_word_written': hex(POST_PAIR_HEAD) in covered,
                      'post_return_r0': post_result_hit['r'][0] if post_result_hit else None,
                      'task_queue_geometry': task_queue_geometry},
        'queue_face': face1,
        'watch_hit_pcs': sorted(covered),
    }
    s1['checks'] = {
        # 断言①:h[2616] 写序 10→50(walker 先 10、case 83 后 50),终值 50。
        'h2616_write_order_10_then_50': bool(
            order10 and order50 and order10['step'] < order50['step']
            and order10['r'][3] == hex(10) and order50['r'][1] == hex(50)),
        'h2616_final_50': s1['h_QCY2616_final'] == 50,
        # 断言②:chmap 写点覆盖(PC=0x601c31a 命中且 r0=帧字节)。
        'chmap_store_covered': bool(chmap_hit and chmap_hit['r'][0] == hex(chmap)),
        'chmap_final_equal': s1['chmap_b_QCY8087_final'] == chmap,
        # 断言③:0x100009 入队(poster 链 PC 覆盖+队列计数+缓冲字)。
        # 缓冲头字形状=(event<<8)|(nwords+1)=0x10000903(0x60648c4 insert(r8,p:8,
        # l:24),SIMB1C W4 的 0x300000 族同构);参数字=b0=83(0x601b944 只写栈缓冲
        # byte0,高字节=poster 栈真实残留——固件同形,handler 只读 b[buf+0])。
        'poster_chain_covered': all(hex(p) in covered for p in
                                    (POST_TAIL, POSTER, POST_WRAPPER, POST_PAIR_HEAD)),
        'queue_event_enqueued': bool(
            face1 and isinstance(face1['count'], int) and face1['count'] > 0
            and hex(0x100009 << 8 | 3) in face1['buffer_words']
            and any(int(w, 16) & 0xFF == 83 for w in face1['buffer_words']
                    if w != 'uninit')),
        # 负证据:分发器层不 relay(82/84 才 inline relay)、第二回调被返 1 跳过、
        # 错误应答支路不走、walker 返 0。
        'no_tx_at_dispatcher_layer': hex(TX_API) not in covered,
        'second_callback_skipped': hex(SECOND_CALLBACK) not in covered,
        'walker_error_tx_absent': hex(WALKER_ERR_TX) not in covered,
        'walker_returned_zero': r0 == 0,
        'dispatcher_144_bookkeeping': (s1['dispatcher_b_QCY8072'] == 1
                                       and s1['dispatcher_b_QCY8076'] == 0),
    }
    observations['s1_walker'] = s1
    # 断言分档:core=①②+负证据(walker/分发器真实执行面);queue=③(入队)。
    # 入队被 poster 调度门静默丢时(SIMB1E W4 对 'anc' 0x300000 post 的同款记录:
    # 「post 触达但 poster 调度门静默丢,固件 fire-and-forget 同语义」)③ 记 BLOCKED
    # 不挡 s2——步 2 定位法本就不经队列(预研 §5.3(ii)),aec 门开合不依赖 ③。
    s1_core = ('h2616_write_order_10_then_50', 'h2616_final_50', 'chmap_store_covered',
               'chmap_final_equal', 'no_tx_at_dispatcher_layer', 'second_callback_skipped',
               'walker_error_tx_absent', 'walker_returned_zero',
               'dispatcher_144_bookkeeping')
    s1_queue = ('poster_chain_covered', 'queue_event_enqueued')
    if not all(s1['checks'][key] for key in s1_core):
        s1['wall'] = {'type': 's1_core_assertion_failed',
                      'failed': [k for k in s1_core if not s1['checks'][k]]}
        return observations
    if not all(s1['checks'][key] for key in s1_queue):
        s1['queue_blocked'] = {
            'failed': [k for k in s1_queue if not s1['checks'][k]],
            'note': '入队未发生(poster 调度门/查找/容量门之一,post_path 记录实际路径;'
                    'SIMB1E W4 同款静默丢)——队列语义断言 ③ 本轮 BLOCKED,归 M1-c 世界重钉',
        }

    # ---------------- 步 2:直调 handler(定位法,两种分段) ----------------
    # 真实路径=泵出队后以参数缓冲调 0x60207a2;此处直调同形缓冲(队列语义/
    # 所有权/时序弱化——预研 §5.3(ii) 定位档,不进验收)。chmap 已由步 1 真码
    # 写入 b[QCY+8087],handler 读回的是真实全局,不手写该字节。
    #
    # s2_entry='handler'(默认):从 0x60207a2 全程跑——TX relay 段经路 UARTTX
    #   (JLUartTxStartWriteRecords,TXADR/TXCNT 写侧记录)放行:轮询档启动后
    #   零等待返回(步 3 实证),断言⑤完整。
    # s2_entry='post-tx'(分段诊断):从 0x6020fc6 续行——跳过的只有 TX relay 段
    #   (该段由 handler 档单独实证:TX 启动写落记录);入口寄存器面全部静态复现:r13/r7=
    #   prologue 常量(0x60207b8/0x60207c6),r5 由真码 0x6020fca 从 b[QCY+8087]
    #   自载,r0..r4 均先写后读。分段法跳过 TX 段的启动记录面(该证据由
    #   handler 档完整覆盖),只为 ⑥⑦⑧(创建体/出口链/aec 门)取分段对照。
    param_addr = driver.scratch_body + 16
    driver.mem_write(param_addr, bytes([83, 0, 0, 0, 0, 0, 0, 0]))
    queue_face_pre_s2 = queue_face_raw(cpu, queue_base)
    ram_before_s2 = dict(cpu.mem.ram)
    uart_stores_before = None
    if getattr(hardware, 'jl_uart_init_records', None) is not None:
        uart_stores_before = {a: e['writes']
                              for a, e in hardware.jl_uart_init_records.stores.items()}
    # 路 UARTTX:TX 启动写(TXADR/TXCNT)记录容器的 s2 前基线(轮询档 TX relay
    # 完成返回的证据面:两写落记录=TX 启动信号真实执行)。
    tx_start_before = None
    if getattr(hardware, 'jl_uart_tx_start_records', None) is not None:
        tx_start_before = {a: e['writes']
                           for a, e in hardware.jl_uart_tx_start_records.stores.items()}
    if s2_entry == 'post-tx':
        # r0..r6=0、r7=表基、r8..r12=0、r13=QCY(r5 由真码自载,见上注)
        s2_args = [0, 0, 0, 0, 0, 0, 0, R7_TABLE_BASE, 0, 0, 0, 0, 0, R13_QCY]
        s2_target = POST_TX_CONT
        s2_label = 's2b:post_tx_continuation_chmap_%#x' % chmap
    else:
        s2_target, s2_args = HANDLER, [param_addr]
        s2_label = 's2:handler_case83_chmap_%#x' % chmap
    try:
        r0_2, steps2, hits2 = driver.direct_call(
            s2_target, s2_args, max_steps=max_steps_s2,
            watch=S2_WATCH, label=s2_label)
        s2_wall = None
    except Exception as exc:
        r0_2, steps2 = None, None
        detail = getattr(exc, 'detail', None) or {}
        hits2 = detail.get('watch_hits') or []
        s2_wall = {'type': type(exc).__name__, 'detail': str(exc)[:1600],
                   'detail_dict': detail}
    covered2 = covered_pcs(hits2)
    heap_changed = sorted(a for a, v in cpu.mem.ram.items()
                          if ram_before_s2.get(a) != v and HEAP_BEGIN <= a < HEAP_END)
    aec_obj = read_u32(cpu, QCY + 624)
    obj_face = None
    node_checks = None
    if aec_obj:
        n_expect = popcount15(chmap)
        masks_expect = sorted(1 << b for b in range(15) if chmap & (1 << b))
        try:
            nodes = node_ring_walk(cpu, aec_obj)
            masks = sorted(int(n['mask_h8'], 16) for n in nodes)
            node_checks = {
                'node_count_equal_popcount': len(nodes) == n_expect,
                'node_masks_equal_chmap_bits': masks == masks_expect,
                'node_cursors_zero': all(n['cursor_h10'] == '0x0' for n in nodes),
                'node_bufs_nonzero': all(int(n['buf_16'], 16) != 0 for n in nodes),
            }
        except Exception as exc:                    # 节点字段严格读拒:如实记录,不猜
            nodes = None
            node_checks = {'ring_walk_blocked': str(exc)[:200]}
        obj_face = {
            'obj': hex(aec_obj), 'n_popcount': n_expect,
            'active_b0': read_u8(cpu, aec_obj + 0),
            'chmap_h4': hex(read_u16(cpu, aec_obj + 4)),
            'nodes_buf_8': hex(read_u32(cpu, aec_obj + 8)),
            'chan_buf_12': hex(read_u32(cpu, aec_obj + 12)),
            'buf_size_16': read_u32(cpu, aec_obj + 16),
            'limit_20': hex(read_u32(cpu, aec_obj + 20)),
            'ring_tail_28': hex(read_u32(cpu, aec_obj + 28)),
            'nodes': nodes,
        }
    task_ret_hit = hit_at(hits2, TASK_CREATE_RET_OBS)
    task_create_hit = hit_at(hits2, TASK_CREATE)
    esco_ret_hit = hit_at(hits2, ESCO_RET_OBS)
    esco_open_hit = hit_at(hits2, ESCO_OPEN)
    tx_api_hit = hit_at(hits2, TX_API)
    queue_face_post_s2 = queue_face_raw(cpu, queue_base)
    uart_delta = None
    if getattr(hardware, 'jl_uart_init_records', None) is not None and uart_stores_before is not None:
        uart_delta = {}
        for a, entry in hardware.jl_uart_init_records.stores.items():
            delta = entry['writes'] - uart_stores_before.get(a, 0)
            if delta:
                uart_delta[hex(a)] = {'writes': delta,
                                      'last': hex(entry['values'][-1]) if entry['values'] else None}
    # 路 UARTTX:TX 启动写记录 delta——TXADR(0xfd2014)末值应=会话缓冲
    # [QCY+2608](world_prep 读回),TXCNT(0xfd2018)末值=帧构建器包络总长
    # (payload 2+包络 4=6,0x6060254 反汇编钉死)。
    tx_start_delta = None
    if getattr(hardware, 'jl_uart_tx_start_records', None) is not None and tx_start_before is not None:
        tx_start_delta = {}
        for a, entry in hardware.jl_uart_tx_start_records.stores.items():
            delta = entry['writes'] - tx_start_before.get(a, 0)
            if delta:
                tx_start_delta[hex(a)] = {'writes': delta,
                                          'last': hex(entry['values'][-1]) if entry['values'] else None}
    s2 = {
        'segment': ('post-tx continuation(0x6020fc6 续行;TX relay 段由 handler 档'
                    '单独实证:TX 启动写落记录+完成返回,本段跳过该段只为分段对照)' if s2_entry == 'post-tx'
                    else 'handler 全程(0x60207a2 起,含 TX relay 段)'),
        'entry': hex(s2_target),
        'param_buffer': ('r13/r7=prologue 常量直载,r5 真码自载' if s2_entry == 'post-tx'
                         else 'scratch {83,0,...} (定位法,跳过出队)'),
        'returned': None if r0_2 is None else hex(r0_2),
        'steps': steps2, 'wall': s2_wall,
        'epilogue_note': ('续行入口无 prologue 压栈——0x602125e epilogue 从 park 停点栈'
                          '弹返回地址,落到 ROM rti(0xa176)诚实停=入口失配副作用非固件墙;'
                          '全部证据(global/heap/PC 链)在该墙之前完成' if s2_entry == 'post-tx'
                          else None),
        'queue_face_pre': queue_face_pre_s2,
        'queue_face_post': queue_face_post_s2,
        'queue_event_still_pending_note': (
            '直调不经泵出队——队列事件仍在(真实消费=M1-c 世界重钉后 resume 全环,'
            '本路如实 BLOCKED 分档)'),
        'heap_bytes_touched_by_s2': len(heap_changed),
        'task_name_flash_readback': repr(read_bytes(cpu, TASK_NAME_ADDR, 12)),
        'task_create_args_observed': ({'entry_r0': task_create_hit['r'][0],
                                       'pdata_r1': task_create_hit['r'][1],
                                       'name_r2': task_create_hit['r'][2]}
                                      if task_create_hit else None),
        'task_create_return_observed': (task_ret_hit['r'][0] if task_ret_hit else None),
        'watch_hit_pcs': sorted(covered2),
        'tx_relay': {
            'uart_con_fn_hit': hit_at(hits2, UART_CON_FN) is not None,
            'tx_api_hit': {'pc': tx_api_hit['pc'], 'r': tx_api_hit['r']} if tx_api_hit else None,
            'post_tx_pc_covered': hex(POST_TX_PC) in covered2,
            'uart_init_word_writes_delta': uart_delta,
            # 路 UARTTX:TX 启动写记录(轮询档 TX relay 完成返回的物证——
            # TXADR=会话缓冲/TXCNT=包络总长,完成语义=启动后零等待,详见
            # JLUartTxStartWriteRecords 文档串)。
            'tx_start_word_writes_delta': tx_start_delta,
        },
        'aec_object': obj_face, 'node_checks': node_checks,
        'esco_slot_QCY620_after': hex(read_u32(cpu, QCY + 620)),
        'aec_slot_QCY624_after': hex(aec_obj),
    }
    if chmap != 0:
        continuation = s2_entry == 'post-tx'
        s2['checks'] = {
            # 断言④:入口→tbh 82..93 表→case 体 PC 链(129..160/0..30 表负证据)。
            # post-tx 分段:④的入口链由 handler 档单独覆盖,本段只验续行入口命中
            # 与「不再回卷 TX 段」的负证据。
            'handler_entry_covered': (hex(HANDLER) in covered2 if not continuation else None),
            'tbh_82_93_table_used': (hex(TBH_82_93) in covered2 if not continuation else None),
            'other_tbh_tables_unused': (hex(TBH_0_30) not in covered2
                                        and hex(TBH_129_160) not in covered2),
            'case83_body_covered': (hex(CASE83_HANDLER) in covered2 if not continuation
                                    else None),
            'continuation_entry_covered': (hex(POST_TX_CONT) in covered2 if continuation
                                           else None),
            # 断言⑤:bit0x20 清零支路+TX relay 完成返回(0x6020fc6 续行=完成证据;
            # 路 UARTTX 放行后 TX 启动写落记录:TXADR=会话缓冲、TXCNT=包络总长
            # 6——两写记录+续行命中=「真实执行至 TX 启动并返回」的完整证据面;
            # 若 TX 撞墙则此处 False 且 wall 记录停点)。post-tx 分段:TX 段被
            # 显式跳过,⑤ 记 None(墙/放行证据在 handler 档)。
            'uart_con_branch_taken': (hit_at(hits2, UART_CON_FN) is not None
                                      if not continuation else None),
            'tx_relay_reached': (hex(TX_RELAY_SITE) in covered2 or tx_api_hit is not None
                                 if not continuation else None),
            'tx_relay_completed': (hex(POST_TX_PC) in covered2 if not continuation else None),
            'tx_start_pair_recorded': (
                bool(tx_start_delta
                     and isinstance(tx_start_delta.get('0xfd2014'), dict)
                     and tx_start_delta['0xfd2014']['last'] == observations['world_prep'].get(
                         'session_QCY2608')
                     and isinstance(tx_start_delta.get('0xfd2018'), dict)
                     and tx_start_delta['0xfd2018']['last'] == hex(6))
                if not continuation else None),
            # 断言⑥:task_create 被调且返回成功。实测极性(CALLIMPL 运行时钉):
            # 0x6020ffa 处 r0=0——JL OS 族 0=成功惯例;预研 §1.2「返回非零」为
            # 静态猜测,按实测修正。传入 entry 实测=0x6033e54(预研 §1.2 记
            # 0x605F12C 系转写滑笔:0x6020fea LI 常量 100875860=0x6033E54)。
            'task_create_called': hex(TASK_CREATE) in covered2,
            'task_create_returned_zero_success': bool(task_ret_hit
                                                       and task_ret_hit['r'][0] == '0x0'),
            # 断言⑦:对象面([QCY+624]/字段/节点/b[obj+0]=1)。
            'aec_slot_store_covered': hex(AEC_SLOT_STORE) in covered2,
            'aec_object_alive': aec_obj != 0,
            'obj_fields': bool(obj_face and obj_face['active_b0'] == 1
                               and obj_face['chmap_h4'] == hex(chmap)
                               and obj_face['limit_20'] == hex(0x800)
                               and obj_face['buf_size_16'] == 2060 * popcount15(chmap) + 4),
            'active_store_covered': hex(ACTIVE_STORE) in covered2,
            'nodes_valid': bool(node_checks and all(node_checks.values())),
            # 断言⑧:共享出口→0x60200b6(chmap) 返回 0、出口后对象仍活。
            'shared_exit_covered': hit_at(hits2, SHARED_EXIT) is not None,
            'esco_open_called': esco_open_hit is not None,
            'esco_open_passed_chmap': bool(esco_open_hit and esco_open_hit['r'][0] == hex(chmap)),
            'esco_open_returned_zero': bool(esco_ret_hit and esco_ret_hit['r'][0] == '0x0'),
            'object_survives_after_exit': aec_obj != 0 and hex(TEARDOWN) not in covered2,
            'handler_normal_return': hex(HANDLER_RETURN) in covered2 or r0_2 == 0,
        }
    else:
        # 负例 chmap=0:事件照达(TX relay/共享出口照走)、创建被 r5==0 真门拦,
        # [QCY+624] 保持 0、无堆分配(观测面;0x60200b6(0) 行为=未钉项 9 如实记)。
        continuation = s2_entry == 'post-tx'
        s2['checks'] = {
            'r5_gate_covered': hex(R5_GATE) in covered2,
            'no_task_create': hex(TASK_CREATE) not in covered2,
            'no_aec_slot_store': hex(AEC_SLOT_STORE) not in covered2,
            'no_active_store': hex(ACTIVE_STORE) not in covered2,
            'aec_slot_stays_zero': aec_obj == 0,
            'shared_exit_still_taken': hex(SHARED_EXIT) in covered2,
            'esco_open_passed_zero': bool(esco_open_hit and esco_open_hit['r'][0] == '0x0'),
            # 未钉项 9 实测钉死:0x60200b6(0) ≠ no-op——重建 esco 会话对象
            # ([QCY+620]→新值)+GPIO 支路+~116B 堆,返回 1;出口按非零返真调
            # 0x601ca3e 拆除(对 [QCY+624]=0 快速早出,不挂)。
            'esco_open_zero_return_is_nonzero': bool(
                esco_ret_hit and esco_ret_hit['r'][0] == '0x1'),
            'exit_teardown_called_on_nonzero_return': hex(TEARDOWN) in covered2,
            'tx_relay_still_runs': ((hex(TX_RELAY_SITE) in covered2
                                     or tx_api_hit is not None)
                                    if not continuation else None),
            # 负例同样经 TX relay({144,83} 帧不含 chmap)——TX 启动写记录
            # 照落(TXADR=会话缓冲/TXCNT=6)。
            'tx_start_pair_recorded': (
                bool(tx_start_delta
                     and isinstance(tx_start_delta.get('0xfd2014'), dict)
                     and tx_start_delta['0xfd2014']['last'] == observations['world_prep'].get(
                         'session_QCY2608')
                     and isinstance(tx_start_delta.get('0xfd2018'), dict)
                     and tx_start_delta['0xfd2018']['last'] == hex(6))
                if not continuation else None),
        }
        s2['negative_notes'] = {
            'esco_open_zero_return': (esco_ret_hit['r'][0] if esco_ret_hit else None),
            'esco_slot_QCY620_side_effect': s2['esco_slot_QCY620_after'],
            'heap_bytes_note': ('%dB=0x60200b6(0) 的 esco 会话重建面,非 aec 分配'
                                % len(heap_changed)),
            'unpinned_item_9': '0x60200b6(0) 出口调用行为=预研 §7 未钉项 9,本负例实测落档',
        }
    observations['s2_handler'] = s2

    # ---------------- 步 4:b3 消费闭环(INJ-03 步 4,路 UARTTX 复跑) ----------------
    # 前置:s2 handler 档全链完成(aec 对象存活+esco 开链已由共享出口 0x60212ba
    # →0x60200b6(chmap) 真码执行——ADC DMA 五写/handler 挂链都在,无需 b1 直调)。
    # 交付环=D2AJ b2/b3 同款(d2_audio_scene.deliver_and_collect 形状):合成
    # 交织样本块(通道高字节 01/02 可区分+块号 bits7-4+样本序 bits3-0)→
    # produce_block(当前窗口+置 pending)→IRQ68 真实交付(嵌套进入 park 停点
    # 的 Timer0 事件循环)→受控步进真实分发器链→RTI 回 park 停点。esco handler
    # 丢弃门 b[esco+0]≤7 计数早退,第 9 次调用起真路径(解交织→逐通道
    # 0x6033cb4——aec 前端门 b[obj+0]==1 由步 2 真码置位,即 INJ-03 b3 墙)。
    if (s2_entry == 'handler' and chmap != 0 and aec_obj
            and s2.get('wall') is None
            and all(bool(v) for k, v in (s2.get('checks') or {}).items()
                    if v is not None and k in ('aec_object_alive', 'active_store_covered',
                                               'nodes_valid'))):
        observations['b3_loop'] = run_b3_loop(machine, hardware, cpu, aec_obj,
                                              max_steps_b3)
    else:
        observations['b3_loop'] = {'skipped': ('s2 未完成(aec 对象未活或 wall 在)或'
                                               '分段/负例档——b3 只在 handler 档正例后跑')}
    return observations


def run_b3_loop(machine, hardware, cpu, aec_obj, max_steps_b3):
    """步 4:b3 交付环(k=1..12)——样本消费闭环+aec 簿记断言面。

    机器纪律与 d2_audio_scene.deliver_and_collect 同款:冻结世界受控步进
    (不调 hardware.advance)、Blocked 如实记墙、RTI 回 park 停点收口。"""
    from d2_audio_scene import (compose_block, AUDIO_ADC_HDL, PARK_STOP_PC,
                                DISPATCHER_PCS, HANDLER_PCS, ESCO_HANDLER,
                                ESCO_EARLY_EXIT, ESCO_REAL_PATH, ESCO_DEINTERLEAVE,
                                ESCO_CHAN_CONSUMER)
    dma = hardware.jl_audio_adc_dma
    watch = set(DISPATCHER_PCS + HANDLER_PCS + (ESCO_HANDLER, ESCO_EARLY_EXIT,
                                                ESCO_REAL_PATH, ESCO_DEINTERLEAVE,
                                                ESCO_CHAN_CONSUMER))
    esco_obj = read_u32(cpu, QCY + 620)
    channels = read_u8(cpu, AUDIO_ADC_HDL + 79)
    sample_bytes = 2 if read_u8(cpu, AUDIO_ADC_HDL + 90) == 0 else 4
    pns = dma.stores[0xfe0544]['values'][-1]
    adr = dma.stores[0xfe053c]['values'][-1]
    length = dma.stores[0xfe0540]['values'][-1]

    def node_faces():
        faces = []
        for node in node_ring_walk(cpu, aec_obj):
            buf = int(node['buf_16'], 16)
            face = {'node': node['node'], 'mask': node['mask_h8'],
                    'cursor_h10': node['cursor_h10']}
            try:
                face['buffer_head'] = read_bytes(cpu, buf, 8).hex()
            except Exception:
                face['buffer_head'] = 'uninitialized_read'
            faces.append(face)
        return faces

    base = {
        'esco_obj_QCY620': hex(esco_obj),
        'aec_obj_QCY624': hex(aec_obj),
        'esco_counter_b0': read_u8(cpu, esco_obj + 0),
        'channels': channels, 'sample_bytes': sample_bytes,
        'pns': pns, 'adr': hex(adr), 'len_samples': length,
        'aec_active_b0': read_u8(cpu, aec_obj + 0),
        'aec_nodes_before': node_faces(),
    }
    records = []
    for k in range(1, 13):
        block = compose_block(pns, channels, sample_bytes, block_index=k)
        ring_pos = ((k - 1) * pns) % length
        expected_window = adr + ring_pos * channels * sample_bytes
        produced = dma.produce_block(cpu.mem, block, channels=channels,
                                     sample_bytes=sample_bytes)
        gate = machine.icfg_demands().get(0)
        gate_used = gate if gate is not None else (
            cpu.icfg if getattr(cpu, 'icfg_known', False) else 0x300)
        eligible = hardware.irq.eligible(68, gate_used)
        rec = {'k': k, 'produced_window': produced['window'],
               'expected_window': hex(expected_window),
               'window_matches_ring': produced['window'] == hex(expected_window),
               'eligible': eligible,
               'esco_counter_before': read_u8(cpu, esco_obj + 0)}
        if not eligible:
            rec['wall'] = {'type': 'irq68_not_eligible', 'gate': hex(gate_used)}
            records.append(rec)
            break
        try:
            machine.deliver_audio_dma_irq(0)
        except (Blocked, ValueError) as exc:
            rec['wall'] = {'type': type(exc).__name__, 'detail': str(exc)[:600]}
            records.append(rec)
            break
        steps = 0
        early = real = deinter = consumer = 0
        wall = None
        while steps < max_steps_b3:
            if cpu.pc in watch:
                if cpu.pc == ESCO_EARLY_EXIT:early += 1
                elif cpu.pc == ESCO_REAL_PATH:real += 1
                elif cpu.pc == ESCO_DEINTERLEAVE:deinter += 1
                elif cpu.pc == ESCO_CHAN_CONSUMER:consumer += 1
            try:
                before = cpu.pc
                insn, _raw, _size = cpu.decode(cpu.pc)
                cpu.step()
                steps += 1
                machine.after_step(cpu, before)
            except Blocked as exc:
                wall = {'at_step': steps, 'stop_pc': hex(cpu.pc), 'stop': exc.details}
                break
            if (cpu.pc == PARK_STOP_PC
                    and getattr(cpu, 'active_context', None) == 'timer0_irq_handler'):
                break
        rec.update({'window_steps': steps, 'wall': wall,
                    'early_exit_hits': early, 'real_path_hits': real,
                    'deinterleave_hits': deinter, 'chan_consumer_hits': consumer,
                    'esco_counter_after': read_u8(cpu, esco_obj + 0),
                    'rti_ok': (cpu.pc == PARK_STOP_PC
                               and getattr(cpu, 'active_context', None) == 'timer0_irq_handler')})
        records.append(rec)
        if wall is not None:
            break
    # 消费闭环断言面:aec 节点游标推进+缓冲样本字节回读(真路径 k 起才可能)。
    nodes_after = node_faces()
    real_ks = [r['k'] for r in records if r.get('real_path_hits')]
    consumer_ks = [r['k'] for r in records if r.get('chan_consumer_hits')]
    # 真路径起点=丢弃门派生(首个 counter_before>=8 的 k),不硬编码 k 值;
    # D2AJ 先例(开链后 2 发)为 k=9,本场景开链即出口链、counter 起点
    # 由真码决定(预期 0→同 k=9,如实记录)。
    gate_k = next((r['k'] for r in records if r.get('esco_counter_before', 0) >= 8), None)
    checks = {
        'deliveries_rti_all': all(r.get('rti_ok') for r in records) and len(records) == 12,
        'early_exit_until_gate': bool(real_ks) and all(
            r.get('early_exit_hits') and not r.get('real_path_hits')
            for r in records if r['k'] < real_ks[0]),
        'real_path_starts_at_counter_gate': bool(real_ks) and real_ks[0] == gate_k
                                            and real_ks == list(range(real_ks[0], real_ks[-1] + 1)),
        'chan_consumer_called': bool(consumer_ks),
        'aec_gate_passed': bool(consumer_ks) and read_u8(cpu, aec_obj + 0) == 1,
        'node_cursor_advanced': any(int(n['cursor_h10'], 16) > 0 for n in nodes_after),
        'node_buffer_written': any(n.get('buffer_head') not in ('uninitialized_read', '')
                                   and n['buffer_head'] != '00' * 4
                                   for n in nodes_after),
    }
    return {'base': base, 'deliveries': records, 'nodes_after': nodes_after,
            'checks': checks,
            'note': ('真路径 k=9 起=b[esco+0] 丢弃门计数满 8(D2AJ b3 同形);'
                     '0x6033cb4 逐通道消费过 b[obj+0]==1 活跃门(INJ-03 b3 墙实体,'
                     '对象由本场景步 2 真码创建——TX relay 放行前该门恒假)')}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--capture', action='store_true',
                        help='全量重放重新捕获快照(~16min,停在 registry 前沿=GPCNUM watchdog)')
    parser.add_argument('--capture-park', action='store_true',
                        help='重捕 park 前沿快照(gpcnt_num_value=None,停回 29,601,125@0x606955c;'
                             ' SIMB1G「park 快照+配套代码」惯例:GPCNUM 后代码需重捕 park 面)')
    parser.add_argument('--frontier', action='store_true',
                        help='锚 registry 前沿(GPCNUM watchdog 停点)而非 park 面——'
                             '快照捕获/还原的开关集必须一致,默认 park')
    parser.add_argument('--chmap', type=lambda s: int(s, 0), default=CHMAP_DEFAULT,
                        help='通道图字节(默认 0x05;负例用 0)')
    parser.add_argument('--s2-entry', choices=('handler', 'post-tx'), default='handler',
                        help='步 2 直调分段:handler=0x60207a2 全程(TX 段放行后完成返回);'
                             'post-tx=0x6020fc6 续行分段诊断(TX 段跳过,创建体/出口链全真码)')
    parser.add_argument('--max-s2-steps', type=int, default=20_000_000,
                        help='步 2 handler 直调步数预算(含 esco 开链 ~6.4M)')
    parser.add_argument('--max-b3-steps', type=int, default=400_000,
                        help='步 4 b3 每轮交付的受控步进预算(D2AJ 同款 400k)')
    parser.add_argument('--out', default='analysis/local-checks/callimpl/scene.json')
    args = parser.parse_args()

    from sim_world_boot import build_world
    t0 = time.time()

    def serve_loop(machine, hardware, cpu, stop=None):
        return run_scene(machine, hardware, cpu, args.chmap, args.max_s2_steps,
                          args.s2_entry, max_steps_b3=args.max_b3_steps)

    # 快照开关集一致性:park 锚(gpcnt_num_value=None)是捕获与还原的共同前提
    # (M4 静态校验会拒载异开关快照);--frontier/--capture 才锚 registry 前沿
    # (--capture 捕的是前沿快照,会覆盖同名 park 快照——捕 park 用 --capture-park)。
    if args.capture:
        args.frontier = True
    park_anchor = not args.frontier
    result = build_world(serve_loop,
                         capture=args.capture or args.capture_park,
                         flag_overrides=({'gpcnt_num_value': None} if park_anchor else None),
                         rom_path='/home/felix021/tmp/QCY_H3S_BR28_ROM_000000-020000.bin',
                         firmware_path='firmware/QCY_H3S_7018F_1T2_release_note_V1.1.8_011.ufw',
                         snapshot_dir='analysis/snapshots')
    payload = {'elapsed_s': round(time.time() - t0, 1),
               'stop': result.get('stop'),
               'completed_steps': result.get('completed_steps'),
               'scene': {k: v for k, v in result.items()
                         if k in ('banner', 'world_prep', 's1_walker', 's2_handler',
                                  'b3_loop')}}
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=1, default=str), encoding='utf-8')

    wp = payload['scene'].get('world_prep', {})
    s1 = payload['scene'].get('s1_walker', {})
    s2 = payload['scene'].get('s2_handler', {})
    b3 = payload['scene'].get('b3_loop', {})
    b3face = ({'checks': b3.get('checks'),
               'series': {'k': [r.get('k') for r in b3.get('deliveries', [])],
                          'early': [r.get('early_exit_hits') for r in b3.get('deliveries', [])],
                          'real': [r.get('real_path_hits') for r in b3.get('deliveries', [])],
                          'consumer': [r.get('chan_consumer_hits') for r in b3.get('deliveries', [])],
                          'counter': [r.get('esco_counter_after') for r in b3.get('deliveries', [])],
                          'rti': [r.get('rti_ok') for r in b3.get('deliveries', [])]},
               'wall': next((r['wall'] for r in b3.get('deliveries', []) if r.get('wall')), None),
               'nodes_after': b3.get('nodes_after')}
              if isinstance(b3, dict) and 'deliveries' in b3 else b3)
    print(json.dumps({'elapsed_s': payload['elapsed_s'],
                      'world_prep': {'face': {k: v for k, v in wp.items() if k != 'checks'},
                                     'checks': wp.get('checks')},
                      's1_checks': s1.get('checks'),
                      's1_post_path': s1.get('post_path'),
                      's1_queue': s1.get('queue_face'),
                      's1_queue_blocked': s1.get('queue_blocked'),
                      's1_wall': s1.get('wall'),
                      's2_checks': s2.get('checks'),
                      's2_tx': s2.get('tx_relay'),
                      's2_obj': s2.get('aec_object'),
                      's2_nodes': s2.get('node_checks'),
                      's2_negative_notes': s2.get('negative_notes'),
                      's2_queue_post_count': ((s2.get('queue_face_post') or {}).get('count')),
                      's2_heap_bytes': s2.get('heap_bytes_touched_by_s2'),
                      's2_wall': s2.get('wall'),
                      'b3': b3face,
                      'evidence': str(out)}, ensure_ascii=False, indent=1))
    return 0


if __name__ == '__main__':
    sys.exit(main())
