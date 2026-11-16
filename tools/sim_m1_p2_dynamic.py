#!/usr/bin/env python3
"""SIMB1F P2 动态试跑（docs/sim-m1-direct-spec.md §5.3 第 3 步）：SIMPRE 静态结论
在 JLEQ 后新前沿停点世界（29,601,125 @ 0x606955c 读 0xfd1104，park 稳态）上的
首跑核对。三处 [待实测]：

  P2-1 0x17 四门停点读值：gate2=[0x1265C0+2444] 模块记录、gate3=b[0x1265C0+5]
       （另直调 gate3 0x60065bc 单读叶子互证）、gate1=0x6003578 连接查询直调
       读返回值（gate4 0x6018994 零调用=静态面，无动态量）。门值只记录，判放行
       与否看 M1-b W2/W3（post 触达+直存生效=四门全过）。
  P2-3 0x600f85e 读路径首跑+直调实测耗时：读命令全固件路径往返（含 0x600b66e
       入口拦截形状——r0=0xFF/SN 回显/cmdid/n 值域由驱动器 read_shape 校验），
       宿主侧墙钟计时（R7 看门狗换算依据）+直调 ANC getter 的精确步数。

P2-2（0x22 setter 首跑+JL_EQ 域写集）在 M1-b 验收（test_sim_m1_verdicts.py
B1-B5）里做，本脚本只在新世界净面上先取基线（EQ 段数/镜像初值——park 稳态
世界与旧前沿停在 EQ 域写入处的初值可能不同，如实记录）。

用法：python3 tools/sim_m1_p2_dynamic.py [--port 7787] [--record <json>]
退出码 0=全部量到手；4=BLOCKED（世界不可用/门值读取被挡，如实回报）。
"""
from __future__ import annotations

import argparse
import base64
import json
import socket
import sys
import time
from pathlib import Path

# 固件地址（tools/sim_world_driver.py 同源；此处仅控制面用）
CONFIG_BASE = 0x1265C0
GATE2_MODULE_RECORD = CONFIG_BASE + 2444     # 0x17 gate2：模块记录判 id==5
GATE3_BYTE = CONFIG_BASE + 5                 # 0x17 gate3：return b[+5]
GATE1_QUERY = 0x6003578                      # 0x17 gate1：连接查询（ABI 无参形状）
GATE3_FUNC = 0x60065BC                       # gate3 单读叶子
ANC_GETTER = 0x6025C84                       # 0x17 getter（恒返 3）
ANC_WIRE = 0x12666B
EQ_MIRROR = CONFIG_BASE + 5356
EQ_SEGMENTS = CONFIG_BASE + 5499
SMOKE_BUF = 0x12A5B0                         # scratch_resp(0x12A540)+128-16（smoke_read 同窗）


def read_frame(sn: int, cmd: int) -> bytes:
    return bytes([0xfe, 0xdc, 0xba, 0xc0, 0xff, 0x00, 0x04, sn, 0xfe, 0x01, cmd, 0xef])


class Client:
    def __init__(self, host, port, timeout=60.0):
        self.sock = socket.create_connection((host, port), timeout=10.0)
        self.sock.settimeout(timeout)
        self.pending = b''

    def send(self, obj):
        self.sock.sendall((json.dumps(obj) + '\n').encode('utf-8'))

    def recv(self):
        while b'\n' not in self.pending:
            data = self.sock.recv(4096)
            if not data:
                return None
            self.pending += data
        line, _, self.pending = self.pending.partition(b'\n')
        return json.loads(line.decode('utf-8'))

    def drain_until(self, kind, allow=()):
        while True:
            message = self.recv()
            if message is None:
                raise AssertionError('等待 %s 时连接关闭' % kind)
            if message.get('t') == kind:
                return message
            if message.get('t') not in allow:
                raise AssertionError('等待 %s 时收到意外消息: %r' % (kind, message))

    def ble_write(self, frame: bytes):
        self.send({'t': 'ble_write', 'data': base64.b64encode(frame).decode()})
        return self.drain_until('chunk_ack')

    def ble_read(self, sn: int, cmd: int):
        """发读帧 → (notify 帧, 墙钟秒)。"""
        self.ble_write(read_frame(sn, cmd))
        started = time.perf_counter()
        message = self.recv()
        elapsed = time.perf_counter() - started
        if message is None or message.get('t') != 'ble_notify':
            raise AssertionError('读 0x%02x 预期 ble_notify，收到 %r' % (cmd, message))
        return base64.b64decode(message['data']), elapsed

    def inspect(self, addr: int, size: int) -> bytes:
        self.send({'t': 'inspect', 'addr': hex(addr), 'len': size})
        message = self.drain_until('inspect_ok', allow=('error',))
        if message['t'] == 'error':
            raise AssertionError('inspect %s+%d 被拒: %r' % (hex(addr), size, message))
        return bytes(message['bytes'])

    def direct(self, pc: int, args, watch=(), max_steps=None):
        request = {'t': 'direct', 'pc': hex(pc), 'args': [hex(a) for a in args],
                   'watch': [hex(w) for w in watch]}
        if max_steps is not None:
            request['max_steps'] = max_steps
        self.send(request)
        return self.recv()

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


def _connect(args, record):
    """连接世界 server（busy/重建中重试），返回 (client, world 横幅)。"""
    deadline = time.time() + args.wait
    while True:
        try:
            client = Client(args.host, args.port)
            client.send({'t': 'hello', 'proto': 1, 'client': 'sim-gatt'})
            banner = client.drain_until('hello_ok', allow=('busy', 'hello_err', 'error'))
            if banner['t'] == 'hello_ok':
                return client, banner['world']
            client.close()
        except (OSError, AssertionError) as exc:
            record['connect_error'] = repr(exc)
        if time.time() > deadline:
            record['verdict'] = 'BLOCKED'
            Path(args.record).parent.mkdir(parents=True, exist_ok=True)
            Path(args.record).write_text(json.dumps(record, indent=2, default=str) + '\n')
            print('[BLOCKED] 连接世界失败：%r' % record.get('connect_error'))
            raise SystemExit(4)
        time.sleep(2.0)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=7787)
    parser.add_argument('--wait', type=float, default=60.0)
    parser.add_argument('--skip-gate1', action='store_true',
                        help='跳过 gate1 连接查询直调（其返回路径若撞未映射会 world_dead）')
    parser.add_argument('--record', default='analysis/local-checks/sim-m1-p2-dynamic.json')
    args = parser.parse_args(argv)

    record = {'started': time.strftime('%Y-%m-%d %H:%M:%S'), 'host': args.host, 'port': args.port}
    client, banner = _connect(args, record)
    record['world_banner'] = banner

    # ---- 净世界基线（park 稳态初值，与旧前沿 EQ 域写入处对照） ----
    record['baseline'] = {
        'anc_wire': list(client.inspect(ANC_WIRE, 3)),
        'eq_segments': client.inspect(EQ_SEGMENTS, 1)[0],
        'eq_mirror_head': client.inspect(EQ_MIRROR, 16).hex(),
    }

    # ---- P2-3：读路径首跑+计时（r0=0xFF/SN 回显/cmdid/n 形状由驱动器
    #      read_shape 校验，异常会以 error(read_shape) 回来——这里如实分档） ----
    reads = []
    try:
        for attempt in range(5):
            frame, elapsed = client.ble_read(0x6F, 0x17)
            status, sn = frame[7], frame[8]
            payload = frame[9:-1]
            reads.append({'attempt': attempt, 'seconds': round(elapsed, 4),
                          'frame': frame.hex(), 'status': status, 'sn': sn,
                          'payload': payload.hex()})
        record['p2_read_path'] = {'ok': True, 'reads': reads,
                                  'mean_seconds': round(sum(r['seconds'] for r in reads) / len(reads), 4)}
        print('[P2-3] 读路径 5 次往返 ok，均值 %ss，首帧 %s' %
              (record['p2_read_path']['mean_seconds'], reads[0]['frame']))
    except AssertionError as exc:
        record['p2_read_path'] = {'ok': False, 'error': str(exc)[:400]}
        print('[BLOCKED] 读路径首跑：%s' % exc)

    # 直调精确步数面：gate3（1 指令量级）与 0x17 getter（读路径最小段）——
    # 读路径撞墙（world_dead→外层重建 ~1s）时重连重建世界后继续取门值。
    if not record['p2_read_path'].get('ok'):
        client.close()
        print('[P2-3] 读路径撞墙：等待外层快照重建后重连（门值测量继续）…')
        time.sleep(3.0)
        client, rebuilt_banner = _connect(args, record)
        record['rebuilt_banner'] = rebuilt_banner
    getter = client.direct(ANC_GETTER, [SMOKE_BUF], max_steps=4096)
    gate3 = client.direct(GATE3_FUNC, [], max_steps=64)
    record['p2_direct_steps'] = {
        'anc_getter': getter, 'gate3': gate3,
        'getter_buffer': client.inspect(SMOKE_BUF, 3).hex(),
    }
    print('[P2-3] 直调步数：getter r0=%r steps=%r；gate3 r0=%r steps=%r' %
          (getter.get('r0'), getter.get('steps'), gate3.get('r0'), gate3.get('steps')))

    # ---- P2-1：0x17 四门停点读值（gate4=静态零调用，无动态量） ----
    gates = {
        'gate2_module_record': client.inspect(GATE2_MODULE_RECORD, 4).hex(),
        'gate2_id_is_5': int.from_bytes(client.inspect(GATE2_MODULE_RECORD, 4), 'little') == 5,
        'gate3_byte': client.inspect(GATE3_BYTE, 1).hex(),
    }
    if record['p2_read_path'].get('ok'):
        direct3 = record.get('p2_direct_steps', {}).get('gate3', {})
        gates['gate3_direct_r0'] = direct3.get('r0')
    if not args.skip_gate1:
        # gate1 连接查询直调（放最后：返回路径若撞未映射会 world_dead，前面量已落袋）
        started = time.perf_counter()
        result = client.direct(GATE1_QUERY, [], max_steps=200_000)
        gates['gate1_query'] = result
        gates['gate1_seconds'] = round(time.perf_counter() - started, 4)
        if result.get('t') == 'error':
            print('[P2-1] gate1 直调 error：%r（如实记录；世界由外层重建复役）' % result)
            record['p2_gates_0x17'] = gates
            record['verdict'] = 'OK' if record['p2_read_path'].get('ok') else 'BLOCKED'
            record['finished'] = time.strftime('%Y-%m-%d %H:%M:%S')
            Path(args.record).parent.mkdir(parents=True, exist_ok=True)
            Path(args.record).write_text(json.dumps(record, indent=2, default=str) + '\n')
            print('record=%s verdict=%s（gate1 撞墙，连接已断——世界重建复役）'
                  % (args.record, record['verdict']))
            client.close()
            return 0 if record['verdict'] == 'OK' else 4
        print('[P2-1] gate1 连接查询 r0=%r steps=%r' % (result.get('r0'), result.get('steps')))
    record['p2_gates_0x17'] = gates
    print('[P2-1] 四门读值：%r' % gates)

    client.close()        # 不发 quit：世界留给后续验收（M1 三级）继续用

    record['verdict'] = 'OK' if record['p2_read_path'].get('ok') else 'BLOCKED'
    record['finished'] = time.strftime('%Y-%m-%d %H:%M:%S')
    Path(args.record).parent.mkdir(parents=True, exist_ok=True)
    Path(args.record).write_text(json.dumps(record, indent=2, default=str) + '\n')
    print('record=%s verdict=%s' % (args.record, record['verdict']))
    return 0 if record['verdict'] == 'OK' else 4


if __name__ == '__main__':
    sys.exit(main())
