#!/usr/bin/env python3
"""tools/sim_bridge_server.py 的单元测试（路 SIMB1A / M1-a 验收第 2 条）。

本地 socket 客户端发 app 真实会发的查询帧（H3SBridge.buildReadStatusFrame 同形：
`FE DC BA C0 FF 00 04 | SN FE 01 cmd | EF`），断言 fixture 响应**字节级**正确
（protocol.md:40 实测响应形 `FE DC BA 00 FF len | 00 SN cmd n payload EF`）。
覆盖：hello 握手/拒绝、读往返（电量/ANC）、跨片重组、set_fixture 换配置（M1-a「两个
不同配置的读往返」）、写静默（0x17 写不合成回执、状态更新后读可见）、未知 cmdid 静默
（n<1 语义）、busy 单活动客户端、chunk_ack 先于 handler 回执。全 localhost。"""
import base64
import json
import socket
import sys
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sim_bridge_server as sbs


def free_port() -> int:
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


class ServerHandle:
    """后台跑 serve() 的测试句柄（静态 fixture 世界，独立于进程级 DEFAULT_FIXTURE）。"""

    def __init__(self, reads=None):
        self.port = free_port()
        state = sbs.StaticFixtureState(reads if reads is not None else {
            0x2f: [0x2f, 0x03, 80, 81 | 0x80, 100],
            0x17: [0x17, 0x03, 0x01, 0x01, 0x02],
        })
        threading.Thread(target=sbs.serve, args=('127.0.0.1', self.port, state),
                         daemon=True).start()


class Client:
    """最小 JSON-lines 客户端。"""

    def __init__(self, port):
        self.sock = socket.create_connection(('127.0.0.1', port), timeout=2.0)
        self.sock.settimeout(2.0)
        self.pending = b''

    def send(self, obj):
        self.sock.sendall((json.dumps(obj) + '\n').encode('utf-8'))

    def recv(self):
        """读一条消息（dict）；EOF 返回 None。"""
        while b'\n' not in self.pending:
            data = self.sock.recv(4096)
            if not data:
                return None
            self.pending += data
        line, _, self.pending = self.pending.partition(b'\n')
        return json.loads(line.decode('utf-8'))

    def drain_until(self, kind, allow=()):
        """收消息直到类型为 kind（允许中途出现 allow 里的类型）；超时/EOF 抛 AssertionError。"""
        while True:
            message = self.recv()
            if message is None:
                raise AssertionError('等待 %s 时连接关闭' % kind)
            if message.get('t') == kind:
                return message
            if message.get('t') not in allow:
                raise AssertionError('等待 %s 时收到意外消息: %r' % (kind, message))

    def close(self):
        self.sock.close()


def read_status_frame(sn: int, cmd: int) -> bytes:
    """app 只读查询帧（H3SBridge.buildReadStatusFrame 同形）。"""
    return bytes([0xfe, 0xdc, 0xba, 0xc0, 0xff, 0x00, 0x04, sn, 0xfe, 0x01, cmd, 0xef])


def write_frame(sn: int, cmd: int, data: bytes) -> bytes:
    """app 自定义写命令帧（SN cmdid data_len data）。"""
    body = bytes([sn, cmd, len(data)]) + data
    return bytes([0xfe, 0xdc, 0xba, 0xc0, 0xff]) + len(body).to_bytes(2, 'big') + body + b'\xef'


class SimBridgeServerTest(unittest.TestCase):

    def setUp(self):
        # 每方法独立 server：set_fixture/写路径会改状态，避免测试间泄漏（不依赖执行顺序）。
        self.server = ServerHandle()

    def hello(self, port):
        client = Client(port)
        client.send({'t': 'hello', 'proto': sbs.PROTO, 'client': 'sim-gatt'})
        ok = client.recv()
        self.assertEqual(ok.get('t'), 'hello_ok')
        self.assertEqual(ok['world']['mode'], 'static-fixture')
        return client

    def test_anc_read_roundtrip_byte_exact(self):
        """ANC 查询（SN=0x6f）→ chunk_ack + 字节级精确的 protocol.md:40 响应形。"""
        client = self.hello(self.server.port)
        try:
            client.send({'t': 'ble_write', 'data': base64.b64encode(read_status_frame(0x6f, 0x17)).decode()})
            ack = client.recv()
            self.assertEqual(ack.get('t'), 'chunk_ack')     # 收到即 ACK
            notify = client.drain_until('ble_notify')
            expect = bytes([0xfe, 0xdc, 0xba, 0x00, 0xff, 0x00, 0x07,
                            0x00, 0x6f, 0x17, 0x03, 0x01, 0x01, 0x02, 0xef])
            self.assertEqual(base64.b64decode(notify['data']), expect)
        finally:
            client.close()

    def test_battery_read_roundtrip(self):
        """电量查询 → `2f 03 50 d1 64`（80% / 81% 充电 / 100%）。"""
        client = self.hello(self.server.port)
        try:
            client.send({'t': 'ble_write', 'data': base64.b64encode(read_status_frame(9, 0x2f)).decode()})
            client.drain_until('chunk_ack')
            notify = client.drain_until('ble_notify')
            expect = bytes([0xfe, 0xdc, 0xba, 0x00, 0xff, 0x00, 0x07, 0x00, 9, 0x2f, 0x03, 80, 81 | 0x80, 100, 0xef])
            self.assertEqual(base64.b64decode(notify['data']), expect)
        finally:
            client.close()

    def test_cross_chunk_reframe(self):
        """帧拆两个 ble_write 片（头跨片）→ 仍重组应答（RcspDeframer 语义）。"""
        client = self.hello(self.server.port)
        try:
            frame = read_status_frame(0x42, 0x17)
            client.send({'t': 'ble_write', 'data': base64.b64encode(frame[:6]).decode()})
            client.drain_until('chunk_ack')
            client.send({'t': 'ble_write', 'data': base64.b64encode(frame[6:]).decode()})
            client.drain_until('chunk_ack')
            notify = client.drain_until('ble_notify')
            expect = sbs.build_response(0x42, bytes([0x17, 0x03, 0x01, 0x01, 0x02]))
            self.assertEqual(base64.b64decode(notify['data']), expect)
        finally:
            client.close()

    def test_set_fixture_second_config(self):
        """set_fixture 换 ANC 三态（通透 3,1,4）→ 读到不同值（M1-a 双配置防假阳性）。"""
        client = self.hello(self.server.port)
        try:
            client.send({'t': 'set_fixture', 'reads': {'23': [0x17, 0x03, 0x03, 0x01, 0x04]}})
            client.drain_until('hello_ok')
            client.send({'t': 'ble_write', 'data': base64.b64encode(read_status_frame(3, 0x17)).decode()})
            client.drain_until('chunk_ack')
            notify = client.drain_until('ble_notify')
            self.assertEqual(base64.b64decode(notify['data']),
                             bytes([0xfe, 0xdc, 0xba, 0x00, 0xff, 0x00, 0x07, 0x00, 3,
                                    0x17, 0x03, 0x03, 0x01, 0x04, 0xef]))
        finally:
            client.close()

    def test_write_silent_then_read_reflects(self):
        """0x17 写：不合成写回执（真机 setter 恒返 0=写静默），状态更新后读可见。"""
        client = self.hello(self.server.port)
        try:
            client.send({'t': 'ble_write', 'data': base64.b64encode(
                write_frame(7, 0x17, bytes([0x02, 0x00, 0x00]))).decode()})
            self.assertEqual(client.recv().get('t'), 'chunk_ack')
            client.sock.settimeout(0.3)
            with self.assertRaises(socket.timeout, msg='写静默：不应有写回执'):
                client.recv()
            client.sock.settimeout(2.0)
            client.send({'t': 'ble_write', 'data': base64.b64encode(read_status_frame(8, 0x17)).decode()})
            client.drain_until('chunk_ack')
            notify = client.drain_until('ble_notify')
            self.assertEqual(base64.b64decode(notify['data']),
                             bytes([0xfe, 0xdc, 0xba, 0x00, 0xff, 0x00, 0x07, 0x00, 8,
                                    0x17, 0x03, 0x02, 0x00, 0x00, 0xef]))
        finally:
            client.close()

    def test_unknown_read_silent(self):
        """fixture 未命中的读 cmdid（0x10 睡眠）→ 静默不回（n<1 语义）。"""
        client = self.hello(self.server.port)
        try:
            client.send({'t': 'ble_write', 'data': base64.b64encode(read_status_frame(4, 0x10)).decode()})
            self.assertEqual(client.recv().get('t'), 'chunk_ack')
            client.sock.settimeout(0.3)
            with self.assertRaises(socket.timeout, msg='未知 cmdid 不应有回包'):
                client.recv()
        finally:
            client.close()

    def test_hello_wrong_proto_rejected(self):
        client = Client(self.server.port)
        try:
            client.send({'t': 'hello', 'proto': 99, 'client': 'sim-gatt'})
            self.assertEqual(client.recv().get('t'), 'hello_err')
            self.assertIsNone(client.recv(), '拒绝后应断开')
        finally:
            client.close()

    def test_busy_single_active_client(self):
        first = self.hello(self.server.port)
        try:
            second = Client(self.server.port)
            try:
                self.assertEqual(second.recv().get('t'), 'busy')
            finally:
                second.close()
        finally:
            first.close()


if __name__ == '__main__':
    unittest.main(verbosity=2)
