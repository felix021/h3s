#!/usr/bin/env python3
"""SIMBRIDGE bridge server（路 SIMB1A 骨架 + 路 SIMB1C 直调世界，docs/sim-bridge-design.md §4.1/§4.2）。

AVD 内 app（SimulationGattLink）经 adb reverse 连本进程：监听 127.0.0.1:7788（避开 18080
等常见占用，端口 CLI 可换），socket 命令循环跑 JSON-lines 协议（一行一条 JSON、UTF-8、
\\n 结尾、二进制 base64）。

两种状态源二选一（SIMB1C §3.5）：

  --static（默认）  静态 fixture（synthetic-not-firmware）：收到 app 真实会发的只读查询帧
    （`FE DC BA C0 FF 00 04 | SN FE 01 cmd | EF`）返回预置值。线程模型/行为与 SIMB1A
    逐字节一致（set_fixture 控制面保留，回归兼容）。
  --world <dir>     直调世界（SIMB1C）：起 explore 停点世界（p11_heap_experimental 前沿，
    快照首轮 capture ~27min、之后 restore ~8s），serve_loop 挂 post_stop——app 的 BLE 命令
    在停点世界里直调真实固件函数（读=分发器 0x600f85e 读路径+0x600b66e 回执拦截；写=T1
    setter 直调+状态旁路镜像断言，写静默不合成回执）。world_dead=可见断线+外层循环快照
    重建复役。世界模式单线程纪律：accept+handle 全部跑在 post_stop 线程内（machine 非
    线程安全），并发客户端回 busy。

协议（与 SimulationGattLink.java / docs/sim-bridge-design.md §4.2 对齐，proto=1，两模式共用）：
  app→server {"t":"hello","proto":1,"client":"sim-gatt"}
  server→app {"t":"hello_ok","world":{...}}          # static:{"mode":"static-fixture"} / world:{"mode":"direct-world",...}
  app→server {"t":"ble_write","data":b64}            # 一个 GATT 片（片不带 SN）
  server→app {"t":"chunk_ack"}                        # 片写回执（收到即 ACK，再进 handler）
  server→app {"t":"ble_notify","data":b64}            # 设备→app 完整 RCSP 帧（app 侧解帧器兼容任意分片）
  server→app {"t":"hello_err"|"busy"|"error",...}     # 拒绝/占线/链路错误（不臆造回执）
  控制面（宿主测试客户端用；app 不发，SimulationGattLink 对未知消息忽略）：
  client→server {"t":"set_fixture","reads":{...}}                    # 仅 static
  client→server {"t":"status"}                                        # → hello_ok（世界横幅刷新）
  client→server {"t":"inspect","addr":"0x…","len":N}                 # → inspect_ok{"bytes":[…]}（白名单）
  client→server {"t":"world_diff"}                                    # → world_diff_ok{"regions":{…}}
  client→server {"t":"last_write"}                                    # → last_write_ok{"result":{…}}
  client→server {"t":"resume","steps":N,"watch":["0x…",…]}           # M1-c 解冻 → resume_ok
  client→server {"t":"direct","pc":"0x…","args":[…],"watch":[…]}     # 直调裸探针 → direct_ok
  client→server {"t":"quit"}                                          # 优雅退出（serve_loop 返回摘要）

RCSP 语义（设计 §3 钉死的形状）：
  - 读 `FE 01 cmd` → 状态源 read → 命中回 `FE DC BA 00 FF <len> 00 SN cmd n payload EF`
    （protocol.md:40 实测响应形，status=00、SN 回显请求）；未命中 n<1 → 静默不回（固件同款）。
  - 写 `cmd len data` → 状态源 apply_write（世界模式=T1 setter 直调）但**不回写回执**——
    13 个 setter 恒返 0=写静默（SIMPRE §1.2），server 不合成写回执（设计 §3 纪律）。
  - 标准 opcode（03/D4/C1/E1/E2）不走本层，M1 记日志不回（未实现桩，如实记录）。

资源上限（设计 §4.2 执行契约①）：单帧 4KB / 解帧缓冲 64KB / 单行 64KB，超限断开报错。
"""
from __future__ import annotations

import argparse
import base64
import json
import select
import socket
import sys
import threading
import time

from sim_world_driver import DriverError, SimWorldDriver, WorldDead

PROTO = 1
DEFAULT_LISTEN = ('127.0.0.1', 7788)
FRAME_LIMIT = 4 * 1024          # 单个 RCSP 帧上限
BUFFER_LIMIT = 64 * 1024        # 解帧累积缓冲上限
LINE_LIMIT = 64 * 1024          # 单行 JSON 上限

RCSP_HEAD = b'\xfe\xdc\xba'
RCSP_TAIL = 0xef

# 直调世界模式里「致命=可见断线」的错误族（driver.dead 即致命；其余 DriverError 回帧不断线）
FATAL_WORLD_CODES = frozenset((
    'world_dead', 'world_already_dead', 'direct_call_blocked', 'direct_call_budget',
    'direct_call_sp_unbalanced', 'direct_call_idle', 'direct_call_simulator_failure',
    'resume_stopped', 'read_shape', 'smoke_read_mismatch', 'scratch_guard_stray_writes',
    'scratch_stack_overlap', 'mem_read_blocked', 'mem_write_blocked'))


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


def make_listener(host: str, port: int) -> socket.socket:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind((host, port))
    listener.listen(4)
    return listener


def _parse_int(value, name):
    """控制面数值解析：接受 int 或 '0x…'/'…' 字符串。"""
    if isinstance(value, bool):
        raise ValueError('%s 不合法' % name)
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return int(value, 16) if value.lower().startswith('0x') else int(value, 10)
    raise ValueError('%s 不合法' % name)


class StaticFixtureState:
    """静态状态源：cmdid → 回执数据段。线程锁护 set_fixture 与写路径更新。"""

    def __init__(self, reads: dict[int, list[int]]) -> None:
        self.lock = threading.Lock()
        self.reads = {int(k): bytes(v) for k, v in reads.items()}

    def read(self, cmdid: int, sn: int | None = None) -> bytes | None:
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
    """单客户端会话：JSON-lines 命令循环。

    静态模式：serve() 阻塞循环跑在会话线程内（SIMB1A 线程模型不变）。
    世界模式：由世界服务循环（post_stop 线程）select 唤醒后调 pump() 喂字节——
    单线程纪律（machine 非线程安全），本类不再开线程。
    """

    def __init__(self, conn: socket.socket, addr, state, world: SimWorldDriver | None = None,
                 on_session_event=None) -> None:
        self.conn = conn
        self.addr = addr
        self.state = state
        self.world = world                    # 世界模式=SimWorldDriver；None=静态
        self.on_session_event = on_session_event  # 世界模式信号回调：'world_dead'/'quit'
        self.deframer = RcspDeframer()
        self.last_write_result = None         # 世界模式写命令断言面（宿主客户端取用）
        self.pending = bytearray()
        self.closed = False

    def send(self, obj: dict) -> None:
        self.conn.sendall((json.dumps(obj, separators=(',', ':')) + '\n').encode('utf-8'))

    def serve(self) -> None:
        """静态模式入口：阻塞收字节并泵给 pump()。"""
        log('客户端接入 %s:%d' % self.addr)
        try:
            while True:
                chunk = self.conn.recv(4096)
                if not self.pump(chunk):
                    return
        finally:
            self.close()

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            try:
                self.conn.close()
            except OSError:
                pass

    def pump(self, chunk: bytes) -> bool:
        """喂一段 socket 字节，处理其中全部完整行；返回 False=会话应结束。"""
        if not chunk:
            log('客户端断开 %s:%d' % self.addr)
            return False
        self.pending.extend(chunk)
        if len(self.pending) > LINE_LIMIT * 2:
            self.send({'t': 'error', 'code': 'line_overflow', 'detail': '行缓冲超上限'})
            return False
        while b'\n' in self.pending:
            line, _, _ = self.pending.partition(b'\n')
            del self.pending[:len(line) + 1]
            if len(line) > LINE_LIMIT:
                self.send({'t': 'error', 'code': 'line_overflow', 'detail': '单行超 %dB' % LINE_LIMIT})
                return False
            if not line.strip():
                continue
            try:
                message = json.loads(line.decode('utf-8'))
                if not isinstance(message, dict):
                    raise ValueError('消息不是 JSON 对象')
            except (ValueError, UnicodeDecodeError) as exc:
                log('忽略非法行: %s' % exc)
                continue
            hello_done = getattr(self, 'hello_done', False)
            try:
                keep = self.handle(message, hello_done)
            except (DriverError, WorldDead) as exc:
                keep = self._world_error(exc)
            except SystemExit:
                return False                  # 解帧超限：会话结束（静态模式同款）
            if keep is False:
                return False
            if message.get('t') == 'hello':
                self.hello_done = True
        return True

    def _world_error(self, exc: DriverError) -> bool:
        """直调错误传播（§3.4 ④）：回 error 帧；致命族=可见断线+世界死亡信号。"""
        detail = dict(getattr(exc, 'detail', {}) or {})
        detail.setdefault('kind', getattr(exc, 'code', 'world_dead'))
        self.send({'t': 'error', 'code': getattr(exc, 'code', 'world_dead'), 'detail': detail})
        log('直调错误 %s: %r' % (getattr(exc, 'code', '?'), detail))
        fatal = isinstance(exc, WorldDead) or getattr(exc, 'code', '') in FATAL_WORLD_CODES \
            or (self.world is not None and self.world.dead)
        if fatal and self.on_session_event is not None:
            self.on_session_event('world_dead', detail)
        return not fatal

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
            if self.world is not None:
                self.send({'t': 'error', 'code': 'not_applicable',
                           'detail': 'set_fixture 仅静态模式；世界模式用 inspect/world_diff'})
                return True
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
        if self.world is not None:
            return self.handle_world_message(kind, message)
        log('忽略未知消息类型: %r' % kind)
        return True

    # ------------------------------------------------------------------ 世界模式控制面（§3.4 ⑤）
    def handle_world_message(self, kind: str, message: dict) -> bool:
        world = self.world
        if kind == 'status':
            self.send({'t': 'hello_ok', 'world': world.describe()})
            return True
        if kind == 'inspect':
            try:
                address = _parse_int(message.get('addr'), 'addr')
                size = _parse_int(message.get('len'), 'len')
                data = world.world_inspect(address, size)
            except (ValueError, TypeError) as exc:
                self.send({'t': 'error', 'code': 'bad_args', 'detail': str(exc)})
                return True
            except DriverError as exc:
                return self._world_error(exc)
            self.send({'t': 'inspect_ok', 'addr': hex(address), 'bytes': list(data)})
            return True
        if kind == 'world_diff':
            try:
                diff = world.world_diff()
            except DriverError as exc:
                return self._world_error(exc)
            self.send({'t': 'world_diff_ok', 'diff': diff})
            return True
        if kind == 'last_write':
            self.send({'t': 'last_write_ok', 'result': self.last_write_result})
            return True
        if kind == 'resume':
            try:
                steps = _parse_int(message.get('steps'), 'steps')
                if steps < 0 or steps > 200_000_000:
                    raise ValueError('steps 超界')
                watch = tuple(_parse_int(w, 'watch') for w in message.get('watch', ()))
                result = world.resume_steps(steps, watch)
            except (ValueError, TypeError) as exc:
                self.send({'t': 'error', 'code': 'bad_args', 'detail': str(exc)})
                return True
            except DriverError as exc:
                return self._world_error(exc)
            self.send({'t': 'resume_ok', **result})
            return True
        if kind == 'direct':
            try:
                pc = _parse_int(message.get('pc'), 'pc')
                args = [_parse_int(a, 'args') for a in message.get('args', ())]
                watch = tuple(_parse_int(w, 'watch') for w in message.get('watch', ()))
                max_steps = _parse_int(message.get('max_steps'), 'max_steps') \
                    if message.get('max_steps') is not None else 1_000_000
                r0, steps, hits = world.direct_call(pc, args, max_steps=max_steps, watch=watch)
            except (ValueError, TypeError) as exc:
                self.send({'t': 'error', 'code': 'bad_args', 'detail': str(exc)})
                return True
            except DriverError as exc:
                return self._world_error(exc)
            self.send({'t': 'direct_ok', 'r0': r0, 'steps': steps, 'watch_hits': hits})
            return True
        if kind == 'quit':
            log('quit：清理会话')
            self.send({'t': 'bye'})
            self.close()
            if self.on_session_event is not None:
                self.on_session_event('quit', {})
            return False
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
                payload = self.state.read(cmdid, sn)
                if payload is None:
                    log('SN=%d 读 0x%02x：未命中 → 静默（n<1 语义）' % (sn, cmdid))
                    return
                response = build_response(sn, payload)
                self.send({'t': 'ble_notify', 'data': base64.b64encode(response).decode('ascii')})
                log('SN=%d 读 0x%02x → %dB 回执 %s' % (sn, cmdid, len(response), response.hex()))
                return
            if len(body) >= 3:
                cmdid, data_len = body[1], body[2]
                data = body[3:3 + data_len]
                result = self.state.apply_write(cmdid, data)
                self.last_write_result = result
                note = result if isinstance(result, str) else _write_note(result)
                log('SN=%d 写 0x%02x（%dB）：%s' % (sn, cmdid, len(data), note))
                return
            log('SN=%d 自定义命令体过短（%dB），忽略' % (sn, len(body)))
            return
        log('SN=%d opcode 0x%02x 非自定义命令：标准 opcode 桩未实现，静默（如实记录）' % (sn, opcode))


def _write_note(result: dict) -> str:
    """世界模式写命令日志摘要。"""
    if result.get('setter') == 0:
        return result.get('note', '官方槽空/表未命中，静默')
    return 'setter=%s n=%r steps=%s watch=%d（写静默）' % (
        result.get('setter'), result.get('n'), result.get('steps'), len(result.get('watch_hits', ())))


# ---------------------------------------------------------------------------
# 世界模式（SIMB1C §3.2/§3.3）：post_stop 内的世界服务循环 + 外层重建循环
# ---------------------------------------------------------------------------

def make_world_serve_loop(listener: socket.socket):
    """构造 post_stop 回调：世界就绪自检 → baseline → accept/handle 单线程循环。

    返回（serve_loop(machine, hardware, stopped_cpu) -> dict）：正常退出带
    {'world_served': 摘要}；世界死亡带 {'world_dead': {...}}（外层循环重建信号）；
    就绪自检不过带 {'sim_bridge_world_not_ready': {...}}（不猜，如实退出）。
    """
    def serve_loop(machine, hardware, cpu):
        from sim_world_driver import DriverError, SimWorldDriver, WorldDead
        if machine is None or hardware is None or cpu is None:
            return {'sim_bridge_world_not_ready': {'reason': 'requires the dualcore world'}}
        readiness = {'stop_pc': hex(cpu.pc), 'core': cpu.core_id,
                     'sp_known': bool(getattr(cpu, 'sp_known', False))}
        try:
            if cpu.core_id != 0 or not getattr(cpu, 'sp_known', False):
                return {'sim_bridge_world_not_ready': readiness}
            driver = SimWorldDriver(machine, hardware, cpu)
            guard = driver.run_scratch_guard()          # 空直调守卫（§2.5，深栈探针按需）
            readiness['scratch_guard'] = guard
            smoke = driver.smoke_read_anc()             # 烟雾读：0x17 getter 恒返 3
            readiness['smoke_read_anc'] = smoke
            driver.take_baseline()                      # world_diff 基线=首条命令前
        except (DriverError, WorldDead) as exc:
            readiness['guard_failure'] = getattr(exc, 'detail', repr(exc))
            return {'sim_bridge_world_not_ready': readiness}
        banner = driver.describe()
        log('世界就绪：%r' % banner)

        listener.setblocking(False)
        session = None
        signals = {}

        def on_session_event(name, payload):
            signals.setdefault(name, payload)

        served = {'commands': 0, 'sessions': 0, 'banner': banner,
                  'smoke_read_anc': smoke, 'scratch_guard': guard}
        try:
            while True:
                sockets = [listener] + ([] if session is None else [session.conn])
                rlist, _, _ = select.select(sockets, [], [])
                for sock in rlist:
                    if sock is listener:
                        conn, addr = listener.accept()
                        if session is None:
                            session = SimBridgeSession(conn, addr, state=driver, world=driver,
                                                       on_session_event=on_session_event)
                            session.hello_done = False
                            served['sessions'] += 1
                            log('客户端接入 %s:%d（世界模式，单线程）' % addr)
                        else:
                            try:
                                conn.sendall((json.dumps({'t': 'busy', 'detail': '已有活动客户端'}) + '\n')
                                             .encode('utf-8'))
                            finally:
                                conn.close()
                            log('拒绝并发客户端 %s:%d（busy）' % addr)
                        continue
                    try:
                        chunk = session.conn.recv(4096)
                    except (BlockingIOError, ConnectionResetError):
                        chunk = b''
                    keep = session.pump(chunk)
                    if not keep:
                        session.close()
                        session = None
                served['commands'] = driver.command_count
                if 'world_dead' in signals:
                    log('世界死亡：%r' % signals['world_dead'])
                    return {'world_dead': signals['world_dead'], 'world_served': served}
                if 'quit' in signals:
                    log('quit：serve_loop 返回摘要')
                    return {'world_served': served}
        finally:
            if session is not None:
                session.close()
            listener.setblocking(True)
            served['commands'] = driver.command_count
            served['watch_log_tail'] = driver.watch_log[-16:]
            served['final_diff'] = None
            try:
                served['final_diff'] = driver.world_diff()
            except DriverError:
                pass

    return serve_loop


def serve_worlds(host: str, port: int, *, rom_path, firmware_path, snapshot_dir,
                 scenario: str = 'p11_heap_experimental') -> int:
    """世界模式外层循环：起世界 → serve → world_dead 则快照重建复役（§3.3）。"""
    from sim_world_boot import build_world, snapshot_path

    listener = make_listener(host, port)
    target = snapshot_path(snapshot_dir, scenario)
    log('bridge server（直调世界）监听 %s:%d，proto=%d，快照=%s' % (host, port, PROTO, target))
    rebuilds = 0
    while True:
        capture = not target.exists()
        serve_loop = make_world_serve_loop(listener)
        started = time.time()
        log('构造世界（%s，%s）…' % (scenario, '首轮全量重放+捕获（~27min 量级）' if capture else '快照 restore'))
        try:
            result = build_world(serve_loop, rom_path=rom_path, firmware_path=firmware_path,
                                 snapshot_dir=snapshot_dir, scenario=scenario, capture=capture)
        except KeyboardInterrupt:
            log('SIGINT：退出世界服务')
            return 0
        elapsed = time.time() - started
        if 'world_dead' in result:
            rebuilds += 1
            log('世界死亡（%.1fs 后第 %d 次快照重建）：%r' % (elapsed, rebuilds, result['world_dead']))
            continue
        if 'sim_bridge_world_not_ready' in result:
            log('世界就绪自检不过（不猜，如实退出）：%r' % result['sim_bridge_world_not_ready'])
            return 2
        stop = result.get('stop', {})
        log('serve_loop 结束（%.1fs）：steps=%s stop=%s %s' % (
            elapsed, result.get('completed_steps'), stop.get('kind'),
            {k: v for k, v in result.items() if k in ('world_served', 'snapshot_capture',
                                                      'snapshot_restore')}))
        return 0


# ---------------------------------------------------------------------------
# 静态模式（SIMB1A 行为逐字节保留）
# ---------------------------------------------------------------------------

def serve(host: str, port: int, state: StaticFixtureState) -> None:
    listener = make_listener(host, port)
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
                        help='（静态模式）fixture JSON 文件 {"reads":{"47":[79,3,80,129,100],...}}（缺省用内置合成值）')
    parser.add_argument('--world', type=str, default=None, metavar='SNAPSHOT_DIR',
                        help='直调世界模式：快照目录（首轮自动 capture，之后 restore 复役）')
    parser.add_argument('--rom', type=str, default=None,
                        help='（世界模式）私有 128KiB ROM 窗口文件路径')
    parser.add_argument('--firmware', type=str, default=None,
                        help='（世界模式）官方 UFW 固件路径（缺省仓内 firmware/ 官方镜像）')
    parser.add_argument('--scenario', type=str, default='p11_heap_experimental',
                        help='（世界模式）共享栈场景（默认 p11_heap_experimental 前沿）')
    args = parser.parse_args(argv)
    try:
        host, port_str = args.listen.rsplit(':', 1)
        port = int(port_str)
    except ValueError:
        parser.error('--listen 形如 127.0.0.1:7788')
    if args.world is None:
        state = DEFAULT_FIXTURE
        if args.fixture:
            with open(args.fixture, 'r', encoding='utf-8') as fh:
                doc = json.load(fh)
            state = StaticFixtureState(doc.get('reads', {}))
            log('fixture 已从 %s 载入: %s' % (args.fixture, sorted(state.reads)))
        log('状态源=静态 fixture（synthetic-not-firmware）：读命中回预置值，写更新状态但写静默')
        serve(host, port, state)
        return 0
    if not args.rom:
        parser.error('世界模式需要 --rom <128KiB ROM 窗口文件>')
    from pathlib import Path
    firmware = args.firmware
    if firmware is None:
        from probe_pi32v2 import ROOT
        firmware = str(ROOT / 'firmware/QCY_H3S_7018F_1T2_release_note_V1.1.8_011.ufw')
    return serve_worlds(host, port, rom_path=args.rom, firmware_path=firmware,
                        snapshot_dir=args.world, scenario=args.scenario)


if __name__ == '__main__':
    sys.exit(main())
