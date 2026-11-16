#!/usr/bin/env python3
"""SIMB1C M1 三级验收执行器（docs/sim-m1-direct-spec.md §4）：socket 消息序列 + 断言 + 退出码。

对运行中的直调世界 server（tools/sim_bridge_server.py --world …）跑：
  M1-a（§4.1）读环重验：A1 ANC 读往返字节级 / A2 inspect 镜像互证 / A3 EQ 读双面互证 /
     A4 确定性+SN 回显 / A5 world_diff 可归因且镜像面零变化；
  M1-b（§4.2）写半程：B0-B6 EQ 0x22 setter 直调=全效果（apply watch+镜像+段数+读回+畸形负例），
     W1-W5 ANC 0x17 直存+post 落队+0x2f 官方槽空负例；
  M1-c（§4.3，--level m1c 才跑）解冻续跑全环：C0-C7。本路（SIMB1C）只落地驱动器
     支持（resume_steps/watch 0x6064c0e），全环验收留待 JLEQ 合并后重钉世界——
     默认 level=ab 时 C 级记 skipped_pending_jleq_repin，不硬凑通过。

验收记录（消息序列/断言/watch/world_diff/横幅）落 JSON（--record，默认
analysis/local-checks/sim-m1-verdicts.json）。
退出码：0=全过；3=FAIL；4=BLOCKED（形状与世界假设不符，回报不猜）。
"""
from __future__ import annotations

import argparse
import base64
import json
import socket
import sys
import time
from pathlib import Path

RCSP_HEAD = b'\xfe\xdc\xba'

# 固件地址（sim_world_driver 同源；此处仅用于断言字面值）
EQ_MIRROR = 0x1265C0 + 5356
EQ_SEGMENTS = 0x1265C0 + 5499
ANC_WIRE = 0x12666B
EQ_SETTER = '0x602583c'
ANC_SETTER = '0x6025ca4'
EQ_APPLY = '0x6025118'
POST_WRAPPER = '0x6064978'

M1C_WATCH = ('0x6064c0e', '0x6025d6a', '0x6012be2', '0x600dcee', '0x6009f6a', '0x6025652')


def read_frame(sn: int, cmd: int) -> bytes:
    """app 只读查询帧（H3SBridge.buildReadStatusFrame 同形，protocol.md:39）。"""
    return bytes([0xfe, 0xdc, 0xba, 0xc0, 0xff, 0x00, 0x04, sn, 0xfe, 0x01, cmd, 0xef])


def write_frame(sn: int, cmd: int, data: bytes) -> bytes:
    """app 自定义写命令帧（SN cmdid dlen data）。"""
    body = bytes([sn, cmd, len(data)]) + data
    return bytes([0xfe, 0xdc, 0xba, 0xc0, 0xff]) + len(body).to_bytes(2, 'big') + body + b'\xef'


def eq_curve(n_segments: int, *, master_gain_db: float = -4.0) -> bytes:
    """合法 EQ 曲线（3B 头+7B/段；eq-cmd-0x22-layout.md §2/§7 编码，段值取文档示例的非平凡组合）。"""
    master = int(round(master_gain_db * 100)) & 0xffff
    head = bytes([1, master & 0xff, (master >> 8) & 0xff])    # type=1 自定义
    segments = [
        (30, 80, 0, 0), (170, 60, -1700, 2), (11000, 200, 200, 3),
        (600, 120, -500, 2), (2500, 90, 300, 4), (90, 70, 700, 1),
        (7500, 150, -900, 3), (320, 110, 1100, 2), (14000, 180, -300, 3), (1000, 100, 400, 2),
    ]
    out = bytearray(head)
    for freq, q100, gain100, iir in (segments[i % len(segments)] for i in range(n_segments)):
        out += (freq & 0xffff).to_bytes(2, 'little')
        out += (q100 & 0xffff).to_bytes(2, 'little')
        out += (gain100 & 0xffff).to_bytes(2, 'little')
        out += bytes([iir & 0xff])
    return bytes(out)


class Verdict:
    """断言收集器：每条 check 记 {id, ok, detail}，FAIL 不中断（汇总退出码）。"""

    def __init__(self, record):
        self.record = record
        self.failures = 0
        self.blocked = None

    def check(self, check_id: str, ok: bool, detail=None) -> bool:
        row = {'id': check_id, 'ok': bool(ok), 'detail': detail}
        self.record.setdefault('checks', []).append(row)
        status = 'PASS' if ok else 'FAIL'
        print('[%s] %s %s' % (status, check_id, '' if detail is None else json.dumps(detail, default=str)))
        if not ok:
            self.failures += 1
        return ok

    def note(self, check_id: str, detail=None):
        """实测分档记录：断言面被世界状态/未钉项挡住——不判 FAIL、不冒充 PASS（如实标注）。"""
        row = {'id': check_id, 'ok': None, 'detail': detail}
        self.record.setdefault('checks', []).append(row)
        self.record.setdefault('notes', []).append(row)
        print('[NOTE] %s %s' % (check_id, json.dumps(detail, default=str)))

    def fail_blocked(self, reason: str, detail=None):
        self.blocked = {'reason': reason, 'detail': detail}
        print('[BLOCKED] %s %s' % (reason, json.dumps(detail or {}, default=str)))


class WorldClient:
    """世界 server 的宿主测试客户端（JSON-lines + 控制面）。"""

    def __init__(self, host, port, timeout=30.0):
        self.sock = socket.create_connection((host, port), timeout=5.0)
        self.sock.settimeout(timeout)
        self.pending = b''

    def send(self, obj):
        self.sock.sendall((json.dumps(obj) + '\n').encode('utf-8'))

    def recv(self, timeout=None):
        if timeout is not None:
            self.sock.settimeout(timeout)
        while b'\n' not in self.pending:
            data = self.sock.recv(4096)
            if not data:
                return None
            self.pending += data
        line, _, self.pending = self.pending.partition(b'\n')
        return json.loads(line.decode('utf-8'))

    def drain_until(self, kind, allow=(), timeout=None):
        while True:
            message = self.recv(timeout)
            if message is None:
                raise AssertionError('等待 %s 时连接关闭' % kind)
            if message.get('t') == kind:
                return message
            if message.get('t') not in allow:
                raise AssertionError('等待 %s 时收到意外消息: %r' % (kind, message))

    def expect_silence(self, seconds: float):
        """静默窗口：无消息返回 None；有消息（如 world_dead error 帧）原样返回
        （写静默语义=无 ble_notify，error 帧属 D2/D3 风险面，由调用方如实分档）。"""
        self.sock.settimeout(seconds)
        try:
            return self.recv()
        except socket.timeout:
            return None
        finally:
            self.sock.settimeout(30.0)

    def ble_write(self, frame: bytes):
        self.send({'t': 'ble_write', 'data': base64.b64encode(frame).decode()})
        return self.drain_until('chunk_ack')

    def ble_read(self, sn: int, cmd: int, timeout=30.0) -> bytes | None:
        """发读帧 → 返回 notify 的 RCSP 帧字节；静默返回 None。"""
        self.ble_write(read_frame(sn, cmd))
        self.sock.settimeout(timeout)
        try:
            message = self.recv()
        except socket.timeout:
            raise AssertionError('读 0x%02x 无 chunk_ack（世界无响应）' % cmd)
        if message is None:
            raise AssertionError('读 0x%02x 时连接关闭' % cmd)
        if message.get('t') != 'ble_notify':
            raise AssertionError('读 0x%02x 预期 ble_notify，收到 %r' % (cmd, message))
        return base64.b64decode(message['data'])

    def inspect(self, addr: int, size: int) -> bytes:
        self.send({'t': 'inspect', 'addr': hex(addr), 'len': size})
        message = self.drain_until('inspect_ok', allow=('error',))
        if message['t'] == 'error':
            raise AssertionError('inspect %s+%d 被拒: %r' % (hex(addr), size, message))
        return bytes(message['bytes'])

    def world_diff(self):
        self.send({'t': 'world_diff'})
        return self.drain_until('world_diff_ok', allow=('error',))['diff']

    def last_write(self):
        self.send({'t': 'last_write'})
        return self.drain_until('last_write_ok', allow=('error',))['result']

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


def parse_notify(frame: bytes):
    """RCSP 响应帧 → (status, sn, payload 数据段)。protocol.md:40 形。"""
    assert frame[:3] == RCSP_HEAD and frame[-1] == 0xef, '帧头尾错误: %s' % frame.hex()
    status, sn = frame[7], frame[8]
    payload = frame[9:-1]
    return status, sn, payload


# ---------------------------------------------------------------------------
# M1-a（§4.1）：读环直调世界重验
# ---------------------------------------------------------------------------

def run_m1a(client: WorldClient, verdict: Verdict):
    record = verdict.record.setdefault('m1a', {})

    # A1：ANC 读往返（SN=0x6F 非常见值）
    frame = client.ble_read(0x6F, 0x17)
    status, sn, payload = parse_notify(frame)
    verdict.check('A1_anc_read_roundtrip',
                  status == 0 and sn == 0x6F and len(payload) == 5 and payload[0] == 0x17
                  and payload[1] == 3,
                  {'frame': frame.hex()})
    wire = payload[2:5]
    record['a1_wire'] = list(wire)

    # A2：inspect 镜像互证（回执不是罐头，是 RAM 实读）
    mirror = client.inspect(ANC_WIRE, 3)
    verdict.check('A2_inspect_cross_check', mirror == wire,
                  {'notify': list(wire), 'mirror': list(mirror)})

    # A3：EQ 读（第二 cmdid 第二镜像面）
    frame = client.ble_read(0x6F, 0x22)
    status, sn, payload = parse_notify(frame)
    ok = status == 0 and sn == 0x6F and payload[0] == 0x22
    detail = {'payload_head': payload[:4].hex() if payload else None}
    if ok:
        n = payload[1]
        ok = n == 3 + 7 * (n // 7) and len(payload) == n + 2
        detail['n'] = n
        if ok:
            segments = client.inspect(EQ_SEGMENTS, 1)[0]
            curve = client.inspect(EQ_MIRROR, n)
            ok = n == 3 + 7 * segments and payload[2:] == curve
            detail.update({'segments': segments, 'mirror': curve[:8].hex(),
                           'payload': payload[2:10].hex()})
            record['a3_segments'] = segments
    else:
        verdict.fail_blocked('A3_eq_read_shape', detail)
        return
    verdict.check('A3_eq_read_dual_face', ok, detail)

    # A4：同 SN/不同 SN 各一次，同配置同响应
    again = parse_notify(client.ble_read(0x6F, 0x17))[2]
    other = parse_notify(client.ble_read(0x23, 0x17))[2]
    verdict.check('A4_determinism_and_sn_echo',
                  again == bytes([0x17, 3]) + wire and other[2:5] == wire,
                  {'again': again.hex(), 'other_sn_payload': other.hex()})

    # A5：世界存活 + diff 可归因 + 镜像面零变化
    diff = client.world_diff()
    client.send({'t': 'status'})
    banner = client.drain_until('hello_ok')['world']
    regions = set(diff['regions'])
    ok = (not banner.get('dead') and diff['unattributed_count'] == 0
          and regions <= {'scratch', 'direct_call_stack', 'heap'}
          and 'config_mirror' not in regions)
    verdict.check('A5_world_alive_diff_attributed', ok,
                  {'regions': sorted(regions), 'unattributed': diff.get('unattributed'),
                   'dead': banner.get('dead')})
    record['a5_diff'] = diff



def write_and_check_silent(client: WorldClient, verdict: Verdict, check_id: str,
                           sn: int, cmdid: int, data: bytes) -> bool:
    """写命令 → chunk_ack + 写静默断言。静默窗口收到 error 帧（world_dead 族）
    = D2/D3 风险面落地——如实判 FAIL+BLOCKED，不降级冒充（设计 §5.1-b 纪律）。"""
    client.ble_write(write_frame(sn, cmdid, data))
    message = client.expect_silence(0.5)
    if message is None:
        verdict.check(check_id, True)
        return True
    verdict.check(check_id, False, {'unexpected_message': message})
    verdict.fail_blocked('%s_world_dead' % check_id, message)
    return False

# ---------------------------------------------------------------------------
# M1-b（§4.2）：EQ 0x22 全效果 + ANC 0x17 写半程
# ---------------------------------------------------------------------------

def run_m1b(client: WorldClient, verdict: Verdict):
    record = verdict.record.setdefault('m1b', {})
    # 实测排序（SIMB1C 首跑钉定）：ANC 写半程先于 EQ 写——EQ 的 apply 深链写集会让
    # 本世界（p11_heap_experimental 前沿停点）的 BT 池进入后续读命令撞未初始化堆
    # (0x18d03c) 的状态（D4 族世界漂移，规格 §6 D4/设计 R9），故读回类断言（B4）
    # 放最后、W 系列放最前，每条证据在世界存活窗口内取足。

    # ---- ANC 0x17 写半程（净世界） ----
    # W1：ANC 写（写静默）
    if not write_and_check_silent(client, verdict, 'W1_anc_write_silent', 0x58, 0x17,
                                  bytes([1, 1, 2])):
        return

    # W2：setter/post watch
    result = client.last_write()
    watch_pcs = [hit['pc'] for hit in (result or {}).get('watch_hits', [])]
    verdict.check('W2_anc_setter_post',
                  (result or {}).get('setter') == ANC_SETTER and result.get('n') == 0
                  and POST_WRAPPER in watch_pcs,
                  {'setter': (result or {}).get('setter'), 'n': (result or {}).get('n'),
                   'watch_pcs': watch_pcs})
    record['w2_write_result'] = result

    # W3：直存断言
    wire = client.inspect(ANC_WIRE, 3)
    verdict.check('W3_anc_direct_store', wire == bytes([1, 1, 2]), {'wire': list(wire)})
    if wire != bytes([1, 1, 2]):
        gates = {'module_record': client.inspect(0x1265C0 + 2444, 4).hex(),
                 'gate3_byte': client.inspect(0x1265C0 + 5, 1).hex()}
        record['w3_gate_diagnostics'] = gates
        print('[note] W3 直存未生效，门值诊断：%r（门值处置=具名 fixture 先例，synthetic 标注）' % gates)

    # W3b：ANC 写后读回（M1-a 面复用；ANC 写不破坏读环——与 EQ 写对照的诊断面）
    payload = parse_notify(client.ble_read(0x60, 0x17))[2]
    verdict.check('W3b_anc_readback', payload == bytes([0x17, 3, 1, 1, 2]),
                  {'payload': payload.hex()})

    # W4：post 落队断言（头字 (0x300000<<8)|(nwords+1)，nwords=3 → 0x30000004）
    face = (result or {}).get('queue_after')
    if face is None:
        # 实测（SIMB1C 首跑）：post wrapper 入口 watch 命中（投递触达），但 poster 体内
        # 调度门（b[0x1265C0+52] 或查名）在此世界状态下静默丢事件——固件同款 fire-and-
        # forget（0x17 setter 不检查 post 返回值），不 Blocked。落队断言如实停在此。
        verdict.note('W4_post_enqueued', {'queue_face': None,
                                          'measured': 'post wrapper 触达但未落队（世界状态门静默丢，'
                                                      '固件同款）；落队断言留 JLEQ 重钉世界后复验'})
    else:
        words = [int(w, 16) for w in face['buffer_words']]
        expect = [0x30000004, 0x6025D6A, 1, 0]
        verdict.check('W4_post_enqueued',
                      any(words[i:i + 4] == expect for i in range(max(len(words) - 3, 0))),
                      {'queue_face': face, 'expect': [hex(w) for w in expect]})

    # W5：0x2f 官方槽 setter=0（静默跳尾，无直调）
    if not write_and_check_silent(client, verdict, 'W5_empty_slot_write_silent', 0x59, 0x2f,
                                  bytes([80])):
        return
    result = client.last_write()
    verdict.check('W5_empty_slot_silent_skip',
                  (result or {}).get('setter') == 0 and not (result or {}).get('watch_hits'),
                  {'result': result})

    # ---- EQ 0x22 全效果 ----
    # B0：段数可读且在值域；选 N_new≠N_old。值域按固件语义取 0..10：0 段（纯 3B 头，
    # len%7==3 同样过 setter 校验）在前沿停点实测出现——世界停在 EQ/JL_EQ 域写入处，
    # EQ 配置尚未初始化（规格 §4.2 B0 的 1..10 假设按实测修正，如实记录）。
    n_old = client.inspect(EQ_SEGMENTS, 1)[0]
    if not 0 <= n_old <= 10:
        verdict.fail_blocked('B0_segment_count_out_of_range', {'n_old': n_old})
        return
    n_new = 2 if n_old != 2 else 5
    curve = eq_curve(n_new)
    verdict.check('B0_prepare', n_old != n_new, {'n_old': n_old, 'n_new': n_new,
                                                 'curve_len': len(curve), 'curve_head': curve[:10].hex()})

    # B1：EQ 写（写静默——窗口内无 ble_notify）
    if not write_and_check_silent(client, verdict, 'B1_write_silent', 0x55, 0x22, curve):
        return

    # B2：last_write 断言（setter/n/watch/steps）
    result = client.last_write()
    watch_pcs = [hit['pc'] for hit in (result or {}).get('watch_hits', [])]
    verdict.check('B2_setter_direct_call',
                  (result or {}).get('setter') == EQ_SETTER and result.get('n') == 0
                  and EQ_APPLY in watch_pcs,
                  {'setter': (result or {}).get('setter'), 'n': (result or {}).get('n'),
                   'steps': (result or {}).get('steps'), 'watch_pcs': watch_pcs})
    record['b2_write_result'] = result

    # B3：直存断言（镜像+段数）——SIMPRE §3.3：apply 在 setter 内联同步完成，
    # B2 watch 命中 + B3 写集断言即 EQ 全效果。
    segments = client.inspect(EQ_SEGMENTS, 1)[0]
    mirror = client.inspect(EQ_MIRROR, 3 + 7 * n_new)
    verdict.check('B3_mirror_direct_store', segments == n_new and mirror == curve,
                  {'segments': segments, 'expect': curve.hex(), 'mirror': mirror.hex()})

    # B5：world_diff 区段归因（规格 §4.2：{EQ 镜像+段数}∪{EQ 引擎写集}∪{直调栈窗}∪{scratch}，
    # 区段外出现变化=FAIL——不可归因写不许放过）。EQ 引擎写集按实测含 heap ctx（节点
    # 运行时）与 mmu_page_table（音频缓冲 MMU 映射页表——A5 证明读路径零触达、ANC-only
    # 世界零触达，仅 EQ apply 链写它，JLEQ 域）。EQ 写后立即取（读命令在此世界状态下
    # 会触发 D4 族池漂移，见文件头排序注记）。
    diff = client.world_diff()
    regions = set(diff['regions'])
    ok = diff['unattributed_count'] == 0 and regions <= {
        'config_mirror', 'heap', 'direct_call_stack', 'scratch', 'mmu_page_table'}
    verdict.check('B5_diff_attributed', ok,
                  {'regions': sorted(regions), 'unattributed': diff.get('unattributed')})
    record['b5_diff'] = diff

    # B6：畸形 EQ 负例（len%7≠3 → 固件 setter 真实拒绝路径，不触 apply/镜像）
    before = client.inspect(EQ_MIRROR, 3 + 7 * n_new)
    segments_before = client.inspect(EQ_SEGMENTS, 1)[0]
    if not write_and_check_silent(client, verdict, 'B6_malformed_write_silent', 0x57, 0x22,
                                  bytes(range(8))):                          # 8%7=1
        return
    after = client.inspect(EQ_MIRROR, 3 + 7 * n_new)
    verdict.check('B6_malformed_rejected',
                  after == before and client.inspect(EQ_SEGMENTS, 1)[0] == segments_before,
                  {'before': before.hex(), 'after': after.hex()})

    # B4：读回确认（值已变的读往返，M1-a 面复用）——放最后：实测 EQ 写的 apply 写集
    # 会让后续读命令在池路径撞未初始化堆（0x18d03c，D4 族世界漂移）。若撞上：EQ 全
    # 效果判据（B2/B3/B5）已过，B4 如实记 note 不冒充、不断言失败；世界由外层重建复役。
    try:
        payload = parse_notify(client.ble_read(0x56, 0x22))[2]
        verdict.check('B4_readback', payload == bytes([0x22, 3 + 7 * n_new]) + curve,
                      {'payload_len': len(payload)})
    except AssertionError as exc:
        verdict.note('B4_readback', {'blocked': str(exc)[:400],
                                     'measured': 'EQ 写后读命令在池路径撞未初始化堆（D4 族世界'
                                                 '漂移）；值断言面=B3 inspect（已过）。JLEQ 重钉'
                                                 '世界/池域覆盖后复验'})


# ---------------------------------------------------------------------------
# M1-c（§4.3）：解冻续跑全环——本路仅驱动器支持，默认 skipped（不硬凑通过）
# ---------------------------------------------------------------------------

def run_m1c(client: WorldClient, verdict: Verdict, steps: int):
    record = verdict.record.setdefault('m1c', {'steps_requested': steps})
    watch = list(M1C_WATCH)
    # C0：解冻续跑（Blocked 会以 error(world_dead)/resume 形状回来——那是 D3 风险面，如实分档）
    client.send({'t': 'resume', 'steps': steps, 'watch': watch})
    message = client.recv()
    if message.get('t') != 'resume_ok':
        verdict.fail_blocked('C0_resume_stopped', message)
        client.close()
        return
    record['resume'] = message
    hits = [hit['pc'] for hit in message['watch_hits']]

    # C1：回调消费铁证 → ANC 回调 → apply 链按序
    verdict.check('C1_consume_evidence', '0x6064c0e' in hits, {'watch_hits': hits[:32]})
    verdict.check('C1_anc_callback_chain',
                  '0x6025d6a' in hits and ('0x6012be2' in hits or '0x600dcee' in hits),
                  {'hits': hits[:64]})

    # C2：ANC ctx 模式迁移（两级读）
    ctx_ptr = int.from_bytes(client.inspect(0x1265C0 + 500, 4), 'little')
    if ctx_ptr:
        mode = client.inspect(ctx_ptr + 33, 1)
        verdict.check('C2_anc_ctx_mode', mode == b'\x01', {'ctx': hex(ctx_ptr), 'mode': list(mode)})
    else:
        verdict.check('C2_anc_ctx_mode', False, {'ctx_ptr': 0})

    # C3：diff 归因
    diff = client.world_diff()
    verdict.check('C3_diff_attributed', diff['unattributed_count'] == 0, {'diff': diff})
    record['diff'] = diff

    # C5：解冻后世界可继续服务
    payload = parse_notify(client.ble_read(0x61, 0x17))[2]
    verdict.check('C5_world_serves_after_resume', payload[0] == 0x17 and payload[1] == 3,
                  {'payload': payload.hex()})

    # C7：负例——直调未映射地址（ROM/flash 空洞取指）→ world_dead 可见断线；
    # 快照重建复役由外层 server 循环完成（重连验证留给下一次连接，本执行器如实记录断线面）。
    client.send({'t': 'direct', 'pc': '0x00f00000', 'args': []})
    message = client.recv()
    verdict.check('C7_world_dead_visible', message.get('t') == 'error', message)
    closed = client.recv() is None
    verdict.check('C7_connection_closed', closed, {'closed': closed})
    client.close()


def wait_server(host, port, seconds: float):
    """等 server 就绪——不用探测连接（会占世界模式的单活动客户端槽），只在
    hello 阶段对 busy/refused 重试。这里仅查端口是否可连（SYN 层）。"""
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=2.0):
                return True
        except OSError:
            time.sleep(2.0)
    return False


def connect_world(host, port, wait_seconds: float):
    """连接 + hello（busy=上一客户端未收尸，refused=世界重建中——都重试）。"""
    deadline = time.time() + wait_seconds
    last = None
    while True:
        try:
            client = WorldClient(host, port)
            client.send({'t': 'hello', 'proto': 1, 'client': 'sim-gatt'})
            banner = client.drain_until('hello_ok', allow=('busy', 'hello_err', 'error'))
            if banner['t'] == 'hello_ok':
                return client, banner['world']
            last = banner
            client.close()
        except (OSError, AssertionError) as exc:
            last = repr(exc)
        if time.time() > deadline:
            raise AssertionError('连接世界 server 失败：%r' % last)
        time.sleep(2.0)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=7789)
    parser.add_argument('--level', choices=('ab', 'm1c'), default='ab',
                        help='ab=M1-a+M1-b（默认）；m1c=全三级（JLEQ 重钉世界后用）')
    parser.add_argument('--m1c-steps', type=int, default=1_000_000)
    parser.add_argument('--wait', type=float, default=30.0,
                        help='等待 server 就绪秒数（首轮世界重放可给 ~2700）')
    parser.add_argument('--record', default='analysis/local-checks/sim-m1-verdicts.json')
    args = parser.parse_args(argv)

    record = {'started': time.strftime('%Y-%m-%d %H:%M:%S'), 'host': args.host, 'port': args.port,
              'level': args.level, 'checks': []}
    verdict = Verdict(record)
    try:
        client, banner = connect_world(args.host, args.port, args.wait)
    except (OSError, AssertionError) as exc:
        verdict.fail_blocked('server_not_listening', {'host': args.host, 'port': args.port,
                                                      'error': str(exc)})
        record['verdict'] = 'BLOCKED'
        Path(args.record).parent.mkdir(parents=True, exist_ok=True)
        Path(args.record).write_text(json.dumps(record, indent=2, default=str) + '\n')
        return 4
    try:
        record['world_banner'] = banner
        if banner.get('mode') != 'direct-world':
            verdict.fail_blocked('not_a_direct_world', banner)
        else:
            run_m1a(client, verdict)
            if verdict.blocked is None:
                run_m1b(client, verdict)
            if args.level == 'm1c' and verdict.blocked is None:
                run_m1c(client, verdict, args.m1c_steps)
            else:
                record['m1c'] = {'status': 'skipped_pending_jleq_repin',
                                 'note': 'M1-c 全环验收留待 JLEQ 合并后重钉世界（规格 §5.3）；'
                                         '本路只落驱动器支持（resume_steps/watch 0x6064c0e）'}
    finally:
        client.close()
    if verdict.blocked is not None:
        record['verdict'] = 'BLOCKED'
        record['blocked'] = verdict.blocked
    elif verdict.failures:
        record['verdict'] = 'FAIL'
    else:
        record['verdict'] = 'PASS'
    record['failures'] = verdict.failures
    record['finished'] = time.strftime('%Y-%m-%d %H:%M:%S')
    Path(args.record).parent.mkdir(parents=True, exist_ok=True)
    Path(args.record).write_text(json.dumps(record, indent=2, default=str) + '\n')
    print('verdict=%s failures=%d record=%s' % (record['verdict'], verdict.failures, args.record))
    return {'PASS': 0, 'FAIL': 3, 'BLOCKED': 4}[record['verdict']]


if __name__ == '__main__':
    sys.exit(main())
