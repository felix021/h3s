#!/usr/bin/env python3
"""SIMBRIDGE bridge server 骨架（路 SIMB1A / M1-a 通道半边，docs/sim-bridge-design.md §4.1/§4.2）。

AVD 内 app（SimulationGattLink）经 adb reverse 连本进程：监听 127.0.0.1:7788（避开 18080
等常见占用，端口 CLI 可换），socket 命令循环跑 JSON-lines 协议（一行一条 JSON、UTF-8、
\\n 结尾、二进制 base64）。

**本版状态源 = 静态 fixture（synthetic-not-firmware）**：收到 app 真实会发的只读查询帧
（`FE DC BA C0 FF 00 04 | SN FE 01 cmd | EF`，cmd∈{0x2f 电量,0x17 ANC,…}）返回预置值，
让 app 的读环与 UI 真实更新、通道全链路打通。「直调半边」（停点世界 getter/setter 直调，
JLEQ park 判定+RCBACK 回执预研）对接后替换 `StaticFixtureState` 即可，socket 契约不变。

协议（与 SimulationGattLink.java / docs/sim-bridge-design.md §4.2 对齐，proto=1）：
  app→server {"t":"hello","proto":1,"client":"sim-gatt"}
  server→app {"t":"hello_ok","world":{...}}          # M1-a: {"mode":"static-fixture",...}
  app→server {"t":"ble_write","data":b64}            # 一个 GATT 片（片不带 SN）
  server→app {"t":"chunk_ack"}                        # 片写回执（收到即 ACK，再进 handler）
  server→app {"t":"ble_notify","data":b64}            # 设备→app 完整 RCSP 帧（app 侧解帧器兼容任意分片）
  server→app {"t":"hello_err"|"busy"|"error",...}     # 拒绝/占线/链路错误（不臆造回执）
  控制面（宿主测试客户端用；app 不会发）：
  client→server {"t":"set_fixture","reads":{"47":[79,3,80,129,100],...}}
                      # 键=cmdid 十进制串，值=回执数据段（cmdid|n|payload）的整数列表

RCSP 语义（设计 §3 钉死的形状）：
  - 读 `FE 01 cmd` → 查 fixture → 命中则回 `FE DC BA 00 FF <len> 00 SN cmd n payload EF`
    （protocol.md:40 实测响应形，status=00、SN 回显请求）；未命中 n<1 → 静默不回（固件同款）。
  - 写 `cmd len data` → 更新 fixture 状态（后续读可见）但**不回写回执**——真机 0x17/0x1d
    setter 恒返 0=写静默（qcy-command17.asm:59），静态骨架不同步合成写回执（设计 §3 纪律）。
  - 标准 opcode（03/D4/C1/E1/E2）不走本层，M1 记日志不回（未实现桩，如实记录）。

线程模型：accept 循环允许多路接入，但同一时刻只允许一个活动客户端（其余立刻回 busy 断开，
设计 §4.1 单线程纪律——本版无共享可变世界，纪律先就位给直调半边）。资源上限（设计 §4.2
执行契约①）：单帧 4KB / 解帧缓冲 64KB / 单行 64KB，超限断开报错。
"""
from __future__ import annotations

import argparse
import base64
import json
import socket
import sys
import threading
import time

PROTO = 1
DEFAULT_LISTEN = ('127.0.0.1', 7788)
FRAME_LIMIT = 4 * 1024          # 单个 RCSP 帧上限
BUFFER_LIMIT = 64 * 1024        # 解帧累积缓冲上限
LINE_LIMIT = 64 * 1024          # 单行 JSON 上限

RCSP_HEAD = b'\xfe\xdc\xba'
RCSP_TAIL = 0xef


def log(message: str) -> None:
    print(time.strftime('[%H:%M:%S] ') + message, file=sys.stderr, flush=True)


class RcspDeframer:
    """RCSP 帧重组器（SerialBleTransport.RcspDeframer.java:225-240 的 Python 镜像）：
    字节流累积、FE DC BA 头 + len_BE16 + EF 尾、假头后移一位重同步。"""

    def __init__(self) -> None:
        self.buffer = bytearray()

    def feed(self, data: bytes) -> list[bytes]:
        self.buffer.extend(data)
        frames: list[bytes] = []
        view = self.buffer
        pos = 0
        while True:
            head = view.find(RCSP_HEAD, pos)
            if head < 0:
                pos = len(view)
                break
            if len(view) - head < 8:
                pos = head
                break
            body_len = (view[head + 5] << 8) | view[head + 6]
            end = head + 8 + body_len
            if end > len(view):
                pos = head
                break
            if view[end - 1] != RCSP_TAIL:
                pos = head + 1     # 假头：后移重同步
                continue
            if end - head > FRAME_LIMIT:
                raise ValueError('RCSP 帧超上限 %dB' % (end - head))
            frames.append(bytes(view[head:end]))
            pos = end
        del view[:pos]
        if len(self.buffer) > BUFFER_LIMIT:
            raise ValueError('解帧缓冲超上限 %dB' % len(self.buffer))
        return frames


def build_response(sn: int, payload: bytes) -> bytes:
    """组 RCSP 响应帧：FE DC BA 00 FF <len_BE16> 00 SN <payload> EF（protocol.md:40 形）。

    payload=回执数据段 `cmdid|n|data`；len = 2(status+SN) + len(payload)。
    """
    body = bytes([0x00, sn & 0xff]) + payload
    return RCSP_HEAD + b'\x00\xff' + len(body).to_bytes(2, 'big') + body + b'\xef'


class StaticFixtureState:
    """静态状态源：cmdid → 回执数据段。线程锁护 set_fixture 与写路径更新。"""

    def __init__(self, reads: dict[int, list[int]]) -> None:
        self.lock = threading.Lock()
        self.reads = {int(k): bytes(v) for k, v in reads.items()}

    def read(self, cmdid: int) -> bytes | None:
        with self.lock:
            return self.reads.get(cmdid)

    def apply_write(self, cmdid: int, data: bytes) -> str:
        """写命令的静态语义：0x17 三字节写更新 ANC 状态（后续读可见）；其余记录不回执。"""
        with self.lock:
            if cmdid == 0x17 and len(data) >= 3:
                mode, scene, strength = data[0], data[1], data[2]
                self.reads[0x17] = bytes([0x17, 0x03, mode, scene, strength])
                return 'anc 状态已更新为 mode=%d scene=%d strength=%d（写静默：不回写回执）' % (
                    mode, scene, strength)
            return 'cmd 0x%02x 写已记录（%dB，写静默：不回写回执）' % (cmdid, len(data))

    def set_reads(self, reads: dict[str, list[int]]) -> None:
        with self.lock:
            for key, value in reads.items():
                self.reads[int(key)] = bytes(value)

    def describe(self) -> dict:
        with self.lock:
            return {'mode': 'static-fixture',
                    'reads': {str(k): list(v) for k, v in sorted(self.reads.items())}}


DEFAULT_FIXTURE = StaticFixtureState({
    # 电量 0x2f：`2f 03 s0 s1 s2`（槽位 percent|0x80 充电位，MainActivity 先例）。
    # 合成值：80% / 81% 充电中 / 100%——与「未连接」占位显示可区分。
    0x2f: [0x2f, 0x03, 80, 81 | 0x80, 100],
    # ANC 0x17：`17 03 mode scene strength`（protocol.md:40 实测响应形；mode 1=降噪）。
    0x17: [0x17, 0x03, 0x01, 0x01, 0x02],
})


class SimBridgeSession:
    """单客户端会话：JSON-lines 命令循环（阻塞，跑在会话线程内）。"""

    def __init__(self, conn: socket.socket, addr, state: StaticFixtureState) -> None:
        self.conn = conn
        self.addr = addr
        self.state = state
        self.deframer = RcspDeframer()

    def send(self, obj: dict) -> None:
        self.conn.sendall((json.dumps(obj, separators=(',', ':')) + '\n').encode('utf-8'))

    def serve(self) -> None:
        log('客户端接入 %s:%d' % self.addr)
        pending = bytearray()
        hello_done = False
        try:
            while True:
                chunk = self.conn.recv(4096)
                if not chunk:
                    log('客户端断开 %s:%d' % self.addr)
                    return
                pending.extend(chunk)
                if len(pending) > LINE_LIMIT * 2:
                    self.send({'t': 'error', 'code': 'line_overflow', 'detail': '行缓冲超上限'})
                    return
                while b'\n' in pending:
                    line, _, rest = pending.partition(b'\n')
                    del pending[:len(line) + 1]
                    if len(line) > LINE_LIMIT:
                        self.send({'t': 'error', 'code': 'line_overflow', 'detail': '单行超 %dB' % LINE_LIMIT})
                        return
                    if not line.strip():
                        continue
                    try:
                        message = json.loads(line.decode('utf-8'))
                        if not isinstance(message, dict):
                            raise ValueError('消息不是 JSON 对象')
                    except (ValueError, UnicodeDecodeError) as exc:
                        log('忽略非法行: %s' % exc)
                        continue
                    keep = self.handle(message, hello_done)
                    if keep is False:
                        return
                    if message.get('t') == 'hello':
                        hello_done = True
        finally:
            try:
                self.conn.close()
            except OSError:
                pass

    def handle(self, message: dict, hello_done: bool) -> bool:
        kind = message.get('t')
        if kind == 'hello':
            proto, client = message.get('proto'), message.get('client')
            if proto != PROTO or client != 'sim-gatt':
                self.send({'t': 'hello_err', 'detail': 'proto=%r client=%r（要 proto=%d client=sim-gatt）'
                           % (proto, client, PROTO)})
                log('hello 拒绝: proto=%r client=%r' % (proto, client))
                return False
            self.send({'t': 'hello_ok', 'world': self.state.describe()})
            log('hello 通过（proto=%d）' % proto)
            return True
        if kind == 'ble_write':
            if not hello_done:
                self.send({'t': 'error', 'code': 'no_hello', 'detail': '先发 hello'})
                return False
            try:
                data = base64.b64decode(message.get('data', ''), validate=True)
            except (ValueError, TypeError) as exc:
                self.send({'t': 'error', 'code': 'bad_b64', 'detail': str(exc)})
                return False
            self.send({'t': 'chunk_ack'})     # 收到即 ACK（设计 §4.2：不隐式延迟 ACK）
            self.feed_device_bytes(data)
            return True
        if kind == 'set_fixture':
            reads = message.get('reads')
            if not isinstance(reads, dict):
                self.send({'t': 'error', 'code': 'bad_reads', 'detail': 'reads 须为 {cmdid:[int,...]}'})
                return True
            try:
                self.state.set_reads(reads)
            except (ValueError, TypeError) as exc:
                self.send({'t': 'error', 'code': 'bad_reads', 'detail': str(exc)})
                return True
            self.send({'t': 'hello_ok', 'world': self.state.describe()})
            log('set_fixture: %s' % reads)
            return True
        log('忽略未知消息类型: %r' % kind)
        return True

    def feed_device_bytes(self, data: bytes) -> None:
        """GATT 片字节流 → RCSP 解帧 → 逐帧应答（读）或记录（写静默）。"""
        try:
            frames = self.deframer.feed(data)
        except ValueError as exc:
            self.send({'t': 'error', 'code': 'frame_overflow', 'detail': str(exc)})
            log('解帧超限断开: %s' % exc)
            raise SystemExit(1)               # 让 serve() 退出（finally 关连接）
        for frame in frames:
            self.handle_frame(frame)

    def handle_frame(self, frame: bytes) -> None:
        flags, opcode = frame[3], frame[4]
        body = frame[7:-1]
        if len(body) < 1:
            log('RCSP 帧体为空（len=0），忽略: %s' % frame.hex())
            return
        sn = body[0]
        if opcode == 0xff and flags == 0xc0:  # 自定义命令（app 命令帧形）
            if len(body) >= 4 and body[1] == 0xfe and body[2] == 0x01:
                cmdid = body[3]
                payload = self.state.read(cmdid)
                if payload is None:
                    log('SN=%d 读 0x%02x：fixture 未命中 → 静默（n<1 语义）' % (sn, cmdid))
                    return
                response = build_response(sn, payload)
                self.send({'t': 'ble_notify', 'data': base64.b64encode(response).decode('ascii')})
                log('SN=%d 读 0x%02x → %dB 回执 %s' % (sn, cmdid, len(response), response.hex()))
                return
            if len(body) >= 3:
                cmdid, data_len = body[1], body[2]
                data = body[3:3 + data_len]
                note = self.state.apply_write(cmdid, data)
                log('SN=%d 写 0x%02x（%dB）：%s' % (sn, cmdid, len(data), note))
                return
            log('SN=%d 自定义命令体过短（%dB），忽略' % (sn, len(body)))
            return
        log('SN=%d opcode 0x%02x 非自定义命令：标准 opcode 桩未实现，静默（如实记录）' % (sn, opcode))


def serve(host: str, port: int, state: StaticFixtureState) -> None:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind((host, port))
    listener.listen(4)
    log('bridge server（静态 fixture）监听 %s:%d，proto=%d' % (host, port, PROTO))
    active_lock = threading.Lock()             # 单活动客户端纪律（设计 §4.1）
    try:
        while True:
            conn, addr = listener.accept()
            if not active_lock.acquire(blocking=False):
                try:
                    conn.sendall((json.dumps({'t': 'busy', 'detail': '已有活动客户端'}) + '\n').encode('utf-8'))
                finally:
                    conn.close()
                log('拒绝并发客户端 %s:%d（busy）' % addr)
                continue
            session = SimBridgeSession(conn, addr, state)

            def run(session=session, lock=active_lock):
                try:
                    session.serve()
                except SystemExit:
                    pass
                except Exception as exc:       # 不吞未知异常（设计 R11：如实上报）
                    log('会话异常: %r' % exc)
                finally:
                    lock.release()

            threading.Thread(target=run, daemon=True, name='sim-bridge-session').start()
    finally:
        listener.close()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--listen', default='%s:%d' % DEFAULT_LISTEN,
                        help='监听地址 host:port（默认 127.0.0.1:7788；adb reverse tcp:port tcp:port）')
    parser.add_argument('--fixture', type=str, default=None,
                        help='fixture JSON 文件 {"reads":{"47":[79,3,80,129,100],...}}（缺省用内置合成值）')
    args = parser.parse_args(argv)
    try:
        host, port_str = args.listen.rsplit(':', 1)
        port = int(port_str)
    except ValueError:
        parser.error('--listen 形如 127.0.0.1:7788')
    state = DEFAULT_FIXTURE
    if args.fixture:
        with open(args.fixture, 'r', encoding='utf-8') as fh:
            doc = json.load(fh)
        state = StaticFixtureState(doc.get('reads', {}))
        log('fixture 已从 %s 载入: %s' % (args.fixture, sorted(state.reads)))
    log('状态源=静态 fixture（synthetic-not-firmware）：读命中回预置值，写更新状态但写静默')
    serve(host, port, state)
    return 0


if __name__ == '__main__':
    sys.exit(main())
