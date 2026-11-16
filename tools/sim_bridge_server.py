#!/usr/bin/env python3
"""SIMBRIDGE bridge server（SIMB1A 通道 + SIMB2A USB 只读 + SIMB1C 直调世界，
docs/sim-bridge-design.md §4.1/§4.2/§4.4）。

AVD 内 app（SimulationGattLink / SimulationUsbLink）经 adb reverse 连本进程：监听
127.0.0.1:7788（避开 18080 等常见占用，端口 CLI 可换），socket 命令循环跑 JSON-lines 协议
（一行一条 JSON、UTF-8、\\n 结尾、二进制 base64）。

BLE 状态源二选一（SIMB1C §3.5）+ USB 设备端（SIMB2A，进程级共享）：

  --static（默认）  BLE 静态 fixture（synthetic-not-firmware）：收到 app 真实会发的只读查询帧
    （`FE DC BA C0 FF 00 04 | SN FE 01 cmd | EF`）返回预置值，让 app 的读环与 UI 真实更新。
    线程模型/行为与 SIMB1A 逐字节一致（set_fixture 控制面保留，回归兼容）。
  --world <dir>     BLE 直调世界（SIMB1C）：起 explore 停点世界（p11_heap_experimental 前沿，
    快照首轮 capture ~27min、之后 restore ~8s），serve_loop 挂 post_stop——app 的 BLE 命令
    在停点世界里直调真实固件函数（读=分发器 0x600f85e 读路径+0x600b66e 回执拦截；写=T1
    setter 直调+状态旁路镜像断言，写静默不合成回执）。world_dead=可见断线+外层循环快照
    重建复役。世界模式单线程纪律：accept+handle 全部跑在 post_stop 线程内（machine 非
    线程安全），并发客户端回 busy。

  USB 面 = MSC BOT 设备端（M2 只读 + M3 armed 刷机，协议级仿真，设计 §4.6 诚实边界：不执行
  固件代码）：`MscBotDevice` 按 tools/usb-recovery 的钉死字节形状应答 CBW→数据→CSW
  （UsbScsi.java 的 host 端镜像），flash 后端 = 2MiB raw fixture（tools/sim_flash_fixture.py
  从公开 UFW 构造），FD07 内存窗后端 = 公开 CPU ROM dump（rom-read.md 发布件）。设备身份与
  loader 相位是**进程级状态**（跨 socket 重连保留——「进 ROM 后断开再扫描」仍见 ROM 身份；
  server 退出才复位，设计 §4.4）。**红线内置：原始 flash 资产永不写**——FB01（擦 4KiB 扇区）
  /FB04（512B 编程）仅在 armed 门内可写且只落 copy-on-armed 进程内副本（docs/sim-m3-flash-spec.md
  §2/§3），未 armed 时行为与 M2 逐字节一致（CSW status=1 拒绝+记录）；WRITE(10)/FC0C/FC12
  等白名单外 opcode 仍一律拒绝；原始 bytes 后端无写方法。

协议（与 SimulationGattLink.java / SimulationUsbLink.java / 设计 §4.2 对齐，proto=1）：
  app→server {"t":"hello","proto":1,"client":"sim-gatt"|"sim-usb"}
  server→app {"t":"hello_ok","world":{...}}          # sim-gatt: static-fixture/direct-world；sim-usb: sim-usb-msc
  app→server {"t":"ble_write","data":b64}            # 一个 GATT 片（片不带 SN）
  server→app {"t":"chunk_ack"}                        # 片写回执（收到即 ACK，再进 handler）
  server→app {"t":"ble_notify","data":b64}            # 设备→app 完整 RCSP 帧（app 侧解帧器兼容任意分片）
  app→server {"t":"usb_scan"}                         # USB 枚举（当前身份一台设备）
  server→app {"t":"usb_scan_result","devices":[...]}
  app→server {"t":"usb_open","key":...}               # 打开数据会话（key 过期→ok:false）
  server→app {"t":"usb_open_result","ok":bool,"detail":...}
  app→server {"t":"usb_bulk_out","data":b64}          # host→device bulk（CBW/OUT 数据）
  server→app {"t":"usb_bulk_in","data":b64}           # device→host bulk：数据相位与 13B CSW
                                                      #   各自独立一条消息、长度精确（BOT 契约）
  app→server {"t":"usb_ctl","len":8|0}                # EP0 厂商恢复请求（usb-recovery.md）
  server→app {"t":"usb_ctl_result","data":b64}        # len8→8×ff；len0→进 ROM 并断开（重枚举）
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

线程模型（静态模式）：accept 循环允许多路接入，但同一时刻只允许一个活动连接（其余立刻回 busy
断开，设计 §4.1 单活动客户端纪律——BLE 与 USB 共用此约束：数据会话活跃时 app 侧 scanJson
复用数据会话 socket，不开新连接）。世界模式为单线程纪律（accept/handle 跑在 post_stop 线程
内，不开会话线程，§4.2）。
资源上限（设计 §4.2 执行契约①）：单帧 4KB / 解帧缓冲 64KB / 单行 64KB，超限断开报错。
"""
from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import select
import socket
import struct
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


# ================================ M2：USB MSC BOT 设备端 ================================
# docs/sim-bridge-design.md §4.4：协议级仿真（§4.6 诚实边界——不执行固件代码），字节形状
# 对照 tools/android-query UsbScsi.java（host 端）与 tools/usb-recovery/protocol.py+
# read_rom.py+h3s_usb.py（真机协议级先例）。

USB_VID_QCY, USB_PID_QCY = 0x3654, 0x4a55      # 普通应用身份（usbfs.py）
USB_VID_ROM, USB_PID_ROM = 0x4c4a, 0x3442      # BR28 ROM 下载身份（usbfs.py）
USB_FLASH_SIZE = 0x200000                      # 2MiB 全片（usb-recovery 固定口径）
USB_ROM_WINDOW = 0x20000                       # CPU ROM 窗 128KiB（read_rom.py 固定窗口）
USB_SECTOR = 512                               # FD05 固定 512B 扇区读
USB_LOADER_ADDRESS = 0x120000                  # RAM loader 装载地址（br28loader.bin 钉死）
USB_LOADER_SIZE = 23552                        # 46×512B
USB_MEM_READ_MAX = 128                         # FD07 单次 ≤128B（512B 实测断链，rom-read.md）
USB_ERASE_SECTOR = 0x1000                      # FB01 NOR 擦除块 4KiB（image.py SECTOR 同源）
USB_ARM_NONCE_TTL = 120.0                      # nonce 消费记录有效期（UpgradeGate 2min 先例；审计字段）


class _Reject(Exception):
    """设备端拒绝该命令：CSW status=1 + 记录（app 侧 exchange 对 CSW≠0/短读抛错=可见失败）。"""


class MscBotDevice:
    """MSC Bulk-Only Transport 设备端（M2 只读命令集 + M3 armed 刷机，进程级状态）。

    - BOT 流解析：usb_bulk_out 消息只是 socket 分片，设备按字节流重组 CBW/OUT 数据相位
      （「USBC」签名重同步），应答以 usb_bulk_in 消息逐 USB IN transfer 发出——数据相位
      与 13B CSW 各自独立一条、长度精确（设计 §4.4 codex 契约；UsbScsi.java:127-136 拒短读）。
    - 相位机照 read_rom.py：fresh-rom →(FB06×46)→ FB08 → loader-started → FC0A →
      loader-ready；FD05/FD07 只在 loader-ready 后应答（app 的 loaderReady 门同源）。
    - 身份机：normal →(EP0 usb_ctl len0)→ rom；跨连接保留、server 退出才复位（设计 §4.4）。
    - M3 三态红线（docs/sim-m3-flash-spec.md §3.1 + codex 评审修正 1）：①原始资产 self.flash
      （bytes，永不写、绑定永不变更）②当前仿真 flash self.flash_sim（设备芯片内容；reboot
      保留刷后镜像）③写授权 self.plan（armed；reboot/disarm 撤销）。FB01/FB04 仅在 armed
      门内可写且只落 copy-on-armed 进程内副本 self.flash_work；未 armed 时行为与 M2 逐字节
      一致（CSW status=1 拒绝+记录）；FC0C/FC12/WRITE(10) 等白名单外 opcode 仍一律拒绝。
    """

    def __init__(self, flash=None, rom=None, identity='normal', plan=None):
        if flash is not None and len(flash) != USB_FLASH_SIZE:
            raise ValueError('flash fixture 须恰为 2MiB raw（got %dB）' % len(flash))
        if rom is not None and len(rom) != USB_ROM_WINDOW:
            raise ValueError('ROM 后端须恰为 128KiB（got %dB）' % len(rom))
        if identity not in ('normal', 'rom'):
            raise ValueError('identity 须 normal|rom')
        self.flash = flash                                   # bytes：原始资产，永不写（M2 锚定字段）
        self.rom = rom                                       # bytes | None：FD07 后端
        self.flash_sha256 = hashlib.sha256(flash).hexdigest() if flash is not None else None
        self.rom_sha256 = hashlib.sha256(rom).hexdigest() if rom is not None else None
        self.lock = threading.RLock()
        self.identity = identity
        self.phase = 'fresh-rom' if identity == 'rom' else 'n/a'
        self.loaded = 0                                      # FB06 已连续装载字节数
        self.loader = bytearray(USB_LOADER_SIZE)
        self.counts = {}                                     # opcode → 次数（验收证据）
        self.rejected = []                                   # [{'opcode','reason'}]（红线记录）
        # —— M3 armed 刷机状态（三态模型；会话层消息接线 usb_arm/usb_disarm/usb_reboot 待第二段）——
        self.flash_sim = flash                               # bytes：当前仿真 flash（芯片内容；初始=原始资产）
        self.flash_work = None                               # bytearray | None：armed 写副本（copy-on-armed）
        self.plan = None                                     # dict | None：armed 写授权
        self.journal = []                                    # [{'op':'erase'|'program'|'reboot',…}]（验收证据）
        self.erased = set()                                  # 已擦 4KiB 扇区基址（FB04 前置门）
        self.written = set()                                 # 已写 512B 块基址（未重擦的重复写拒绝）
        self.nonces_consumed = {}                            # nonce → 消费记录（单次有效，防重放）
        # BOT 字节流状态（feed_out 专用；断链/reboot 须经 link_reset/reboot 清空——评审修正 5）
        self._rx = bytearray()
        self._waiting = None                                 # None=等 CBW；dict=命令在途
        if plan is not None:
            self.arm(plan)                                   # CLI --arm-plan 冒烟入口（第二段接线）

    # ---------------- 外观（world 横幅 / 枚举 / 打开） ----------------

    def describe(self):
        with self.lock:
            backend = self.flash_work if self.flash_work is not None else self.flash_sim
            return {
                'mode': 'sim-usb-msc',
                'identity': self.identity,
                'loader_phase': self.phase,
                'loader_bytes': self.loaded,
                'flash_bytes': len(self.flash) if self.flash is not None else 0,
                'flash_sha256': self.flash_sha256,
                'rom_sha256': self.rom_sha256,
                'read_only': self.plan is None,              # armed（写授权）之外无 Flash 写路径；未 armed=M2 语义
                'armed': self.plan is not None,              # M3 写授权态（reboot 后 False=不可再写）
                'plan_sectors': list(self.plan['sectors']) if self.plan is not None else None,
                'journal_len': len(self.journal),
                'work_sha256': hashlib.sha256(self.flash_work).hexdigest()
                              if self.flash_work is not None else None,   # armed 副本当前 SHA（半态可见性）
                'sim_sha256': hashlib.sha256(backend).hexdigest()
                             if backend is not None else None,             # FD05 后端（芯片内容）SHA
                'commands': dict(self.counts),
                'rejected': len(self.rejected),
            }

    def scan(self):
        """枚举：当前身份一台设备（key 随身份翻转——旧 key 打开=设备已不在，open 失败）。"""
        with self.lock:
            if self.identity == 'normal':
                return [{'key': 'sim:usb:3654:4a55', 'vid': USB_VID_QCY, 'pid': USB_PID_QCY,
                         'name': 'QCY H3S (sim)'}]
            return [{'key': 'sim:usb:4c4a:3442', 'vid': USB_VID_ROM, 'pid': USB_PID_ROM,
                     'name': 'BR28 UBOOT (sim)'}]

    def open_key(self, key):
        with self.lock:
            current = self.scan()[0]['key']
            return (key == current, '' if key == current else 'key 已过期（设备身份=%s）' % self.identity)

    # ---------------- EP0 厂商恢复请求（usb-recovery.md「进入 ROM 的准确控制请求」） ----------------

    def control(self, length):
        """返回 (data|None, error|None)。len8=只读能力检查（8×ff，仅 normal）；len0=进 ROM
        并重启（会话层负责断开连接=重枚举）；其余长度拒绝（不臆造控制行为）。"""
        with self.lock:
            if length == 8:
                if self.identity != 'normal':
                    return None, 'rom 身份不再应答恢复能力读'
                return b'\xff' * 8, None
            if length == 0:
                if self.identity != 'normal':
                    return None, '已是 rom 身份'
                self.identity = 'rom'
                self.phase = 'fresh-rom'
                self.loaded = 0
                return b'', None
            return None, '不支持的控制长度 %r' % (length,)

    # ---------------- BOT 数据面 ----------------

    def feed_out(self, data):
        """host→device bulk 字节 → 设备应答的 IN transfer 列表（每项一条 usb_bulk_in）。"""
        with self.lock:
            self._rx.extend(data)
            ins = []
            while True:
                if self._waiting is None:
                    cbw = self._take_cbw()
                    if cbw is None:
                        break
                    self._waiting = cbw
                    if cbw['size'] == 0 or not cbw['data_out']:
                        ins += self._execute(self._waiting)
                        self._waiting = None
                        continue
                w = self._waiting
                if w['got'] < w['size']:
                    take = min(w['size'] - w['got'], len(self._rx))
                    if take == 0:
                        break
                    w['buf'] += self._rx[:take]
                    del self._rx[:take]
                    w['got'] += take
                if w['got'] >= w['size']:
                    ins += self._execute(w)
                    self._waiting = None
                else:
                    break
            return ins

    def _take_cbw(self):
        """从字节流取一个 31B CBW（签名重同步；不够一个 CBW 时返回 None 留待后续分片）。"""
        if len(self._rx) < 4:
            return None
        if self._rx[:4] != b'USBC':
            idx = bytes(self._rx).find(b'USBC', 1)
            if idx < 0:
                del self._rx[:-3]                            # 留尾部防签名跨片拆分
                return None
            del self._rx[:idx]
            if len(self._rx) < 31:
                return None
        if len(self._rx) < 31:
            return None
        cbw = bytes(self._rx[:31])
        del self._rx[:31]
        tag, size, flags, lun, cdb_len = struct.unpack('<IIBBB', cbw[4:15])
        if cdb_len < 1 or cdb_len > 16 or size > USB_FLASH_SIZE:
            self.rejected.append({'opcode': None, 'reason': 'malformed CBW: cdb_len=%d size=%d' % (cdb_len, size)})
            return None                                     # 已消费 31B，下次调用重同步
        return {'tag': tag, 'size': size, 'lun': lun, 'cdb': cbw[15:15 + cdb_len],
                'data_out': (flags & 0x80) == 0, 'buf': b'', 'got': 0}

    def _execute(self, w):
        """执行一条完整命令（数据相位已齐/无数据相位）→ IN transfer 列表。"""
        cdb = w['cdb']
        opcode = None
        try:
            if len(cdb) < 2:
                raise _Reject('CDB 过短（%dB）：无法解析 2B opcode' % len(cdb))   # 评审修正 4（防 cdb[1] 越界）
            opcode = 0x12 if cdb[0] == 0x12 else ((cdb[0] << 8) | cdb[1])
            if w['lun'] != 0:
                raise _Reject('lun=%d 非 0' % w['lun'])
            if cdb[0] == 0x12:
                return self._inquiry(w)
            handler = {0xfb06: self._ram_write, 0xfb08: self._loader_start,
                       0xfc0a: self._online_query, 0xfd05: self._flash_read,
                       0xfd07: self._mem_read,
                       0xfb01: self._sector_erase,            # M3：armed 门在 handler 体内（§3.2 取舍注：
                       0xfb04: self._sector_program}.get(opcode)   # 白名单恒注册，未 armed 拒绝理由=armed 门）
            if handler is None:
                raise _Reject('opcode 0x%04x 不在命令白名单（FC0C/FC12/WRITE(10) 等无此命令）' % opcode)
            return handler(w)
        except _Reject as exc:
            self.rejected.append({'opcode': '0x%04x' % opcode if opcode is not None else None,
                                  'reason': str(exc)})
            self.counts['rejected'] = self.counts.get('rejected', 0) + 1
            log('MSC 拒绝 opcode %s: %s' % ('0x%04x' % opcode if opcode is not None else '?', exc))
            return [self._csw(w['tag'], 1)]
        finally:
            if opcode is not None:
                self.counts['%04x' % opcode] = self.counts.get('%04x' % opcode, 0) + 1

    @staticmethod
    def _csw(tag, status=0):
        return b'USBS' + struct.pack('<I', tag) + b'\x00\x00\x00\x00' + bytes([status])

    def _rom_gate(self, what):
        if self.identity == 'rom':
            return
        raise _Reject('%s 需要 rom 身份（当前 normal：普通 QCY 复合设备无 MSC 接口）' % what)

    # —— 各 opcode（门与形状照 read_rom.py/h3s_usb.py 的装载纪律；越界即拒） ——

    def _inquiry(self, w):
        self._rom_gate('INQUIRY')
        if w['size'] != 36 or w['data_out']:
            raise _Reject('INQUIRY 须 36B IN（got size=%d）' % w['size'])
        r = bytearray(36)
        r[0], r[1], r[2], r[3], r[4] = 0x00, 0x80, 0x02, 0x02, 32   # 标准头+附加长度（合成形状）
        r[8:16] = b'BR28    '                                       # vendor 8B（usb-recovery.md 实测）
        r[16:32] = b'UBOOT1.00' + b' ' * 7                          # product 16B（实测；loader 运行中同名）
        r[32:36] = b'1.00'                                         # revision（未实测钉死，取产品版本段）
        return [bytes(r), self._csw(w['tag'])]

    def _ram_write(self, w):
        self._rom_gate('FB06')
        if self.phase != 'fresh-rom':
            raise _Reject('FB06 仅 fresh-rom 相位（当前 %s；勿对运行中 loader 再上传）' % self.phase)
        if not w['data_out']:
            raise _Reject('FB06 须 OUT 数据相位')
        cdb, data = w['cdb'], w['buf']
        address = int.from_bytes(cdb[2:6], 'big')
        length = int.from_bytes(cdb[6:8], 'big')
        if length != len(data) or length != USB_SECTOR:
            raise _Reject('FB06 块长须 512（got %d/%d）' % (length, len(data)))
        if address != USB_LOADER_ADDRESS + self.loaded:
            raise _Reject('FB06 须从 0x%x 连续装载（got 0x%x）' % (USB_LOADER_ADDRESS + self.loaded, address))
        if address + length > USB_LOADER_ADDRESS + USB_LOADER_SIZE:
            raise _Reject('FB06 越出 loader 窗 0x%x..0x%x' % (USB_LOADER_ADDRESS, USB_LOADER_ADDRESS + USB_LOADER_SIZE))
        if cdb[8] != 0 or int.from_bytes(cdb[9:11], 'little') != binascii.crc_hqx(data, 0):
            raise _Reject('FB06 CRC16-XMODEM 不符')
        self.loader[self.loaded:self.loaded + length] = data
        self.loaded += length
        return [self._csw(w['tag'])]

    def _loader_start(self, w):
        self._rom_gate('FB08')
        if self.phase != 'fresh-rom':
            raise _Reject('FB08 仅 fresh-rom 相位（当前 %s）' % self.phase)
        if self.loaded != USB_LOADER_SIZE:
            raise _Reject('loader 未装载完整（%d/%d）' % (self.loaded, USB_LOADER_SIZE))
        if w['cdb'][2:8] != bytes.fromhex('001200000001'):
            raise _Reject('FB08 仅接受钉死实参（入口 0x120000、参数 1=SPI NOR）')
        if w['size'] != 16 or w['data_out']:
            raise _Reject('FB08 须 16B IN')
        self.phase = 'loader-started'
        r = bytearray(16)                                          # 回显 opcode（protocol.py cmd 同规则）；
        r[0], r[1] = 0xfb, 0x08                                    # 尾段未实测钉死，0 填充（协议级合成标注）
        return [bytes(r), self._csw(w['tag'])]

    def _online_query(self, w):
        self._rom_gate('FC0A')
        if self.phase != 'loader-started':
            raise _Reject('FC0A 仅 loader-started 相位（当前 %s）' % self.phase)
        if w['size'] != 16 or w['data_out']:
            raise _Reject('FC0A 须 16B IN')
        self.phase = 'loader-ready'
        r = bytearray(16)
        r[0:3] = bytes.fromhex('fc0a03')                           # type3=SPI NOR（实测钉死）
        r[3:6] = bytes.fromhex('cd7015')                           # flash ID（usb-recovery.md 实测）
        r[6:10] = struct.pack('>I', USB_FLASH_SIZE)                # 容量 2MiB（字段位置未钉，合成标注）
        return [bytes(r), self._csw(w['tag'])]

    def _flash_read(self, w):
        self._rom_gate('FD05')
        if self.phase != 'loader-ready':
            raise _Reject('FD05 仅 loader-ready 相位（当前 %s）' % self.phase)
        cdb = w['cdb']
        address = int.from_bytes(cdb[2:6], 'big')
        length = int.from_bytes(cdb[6:8], 'big')
        if w['data_out'] or length != w['size'] or length != USB_SECTOR:
            raise _Reject('FD05 须 512B IN（got %d/%d）' % (length, w['size']))
        if address % USB_SECTOR != 0 or not 0 <= address < address + length <= USB_FLASH_SIZE:
            raise _Reject('FD05 地址越界/未对齐: 0x%x' % address)
        backend = self.flash_work if self.flash_work is not None else self.flash_sim
        if backend is None:
            raise _Reject('无 flash 后端（--flash-image 未提供）')
        return [backend[address:address + length], self._csw(w['tag'])]   # armed 读副本（§1 擦写中途读回可见）

    def _mem_read(self, w):
        self._rom_gate('FD07')
        if self.phase != 'loader-ready':
            raise _Reject('FD07 仅 loader-ready 相位（当前 %s）' % self.phase)
        cdb = w['cdb']
        address = int.from_bytes(cdb[2:6], 'big')
        length = int.from_bytes(cdb[6:8], 'big')
        if w['data_out'] or length != w['size'] or not 1 <= length <= USB_MEM_READ_MAX:
            raise _Reject('FD07 须 1..128B IN（got %d/%d）' % (length, w['size']))
        if not 0 <= address < address + length <= USB_ROM_WINDOW:
            raise _Reject('FD07 越出 ROM 窗 0..0x%x: 0x%x+%d' % (USB_ROM_WINDOW, address, length))
        if self.rom is None:
            raise _Reject('无 ROM 后端（--rom 未提供）')
        return [bytes(self.rom[address:address + length]), self._csw(w['tag'])]

    # ---------------- M3：armed 刷机（FB01/FB04 + 状态机；docs/sim-m3-flash-spec.md §2/§3） ----------------

    def arm(self, plan):
        """armed 进入（§3.1 一次性原子三步：校验 → copy-on-armed → nonce 消费作废）。

        plan={'sectors':[4KiB 基址…],'target_sha256':hex64,'nonce':str}。校验失败/armed 期间
        重复 arm/已消费 nonce 复用 → ValueError（会话层翻译为 usb_arm_result ok=false，不断链）。
        副本基线=**当前仿真 flash**（reboot 保留的刷后内容也算「当前芯片」，评审修正 1）；
        原始资产 self.flash 永不作为写目标。nonce 单次有效：消费即记 nonces_consumed（含
        进程实例/计划摘要/基线 SHA/有效期，评审修正 2），disarm/reboot 后同 nonce 不得复用。
        会话层消息分发（usb_arm）待第二段（SIMB3C 只做设备级）。
        """
        with self.lock:
            if self.plan is not None:
                raise ValueError('已 armed（busy 语义：先 disarm 作废或 reboot 收尾）')
            if not isinstance(plan, dict):
                raise ValueError('plan 须为 dict {"sectors":[…],"target_sha256":…,"nonce":…}')
            sectors = plan.get('sectors')
            if not isinstance(sectors, (list, tuple)) or not sectors:
                raise ValueError('sectors 须为非空列表（4KiB 基址）')
            seen = set()
            for base in sectors:
                if isinstance(base, bool) or not isinstance(base, int):
                    raise ValueError('扇区基址须为 int: %r' % (base,))
                if base % USB_ERASE_SECTOR != 0 or not 0 <= base < USB_FLASH_SIZE:
                    raise ValueError('扇区基址须 4KiB 对齐且在 2MiB 内: 0x%x' % base)
                if base in seen:
                    raise ValueError('扇区基址重复: 0x%x' % base)
                seen.add(base)
            nonce = plan.get('nonce')
            if not isinstance(nonce, str) or not nonce:
                raise ValueError('nonce 须为非空字符串（单次确认凭证）')
            consumed = self.nonces_consumed.get(nonce)
            if consumed is not None:
                raise ValueError('nonce 已消费（%s，计划摘要=%s）：单次有效，不得复用'
                                 % (consumed['note'], consumed['plan_digest'][:12]))
            target_sha = plan.get('target_sha256')
            if not isinstance(target_sha, str) or len(target_sha) != 64 \
                    or any(c not in '0123456789abcdef' for c in target_sha):
                raise ValueError('target_sha256 须为 64 位小写 hex 字符串')
            if self.flash_sim is None:
                raise ValueError('无 flash 后端（--flash-image 未提供）：无可刷镜像')
            # —— 原子三步（§3.1）：校验全过 → 副本建立 → nonce 消费（三者间无拒绝路径）——
            baseline = self.flash_sim
            self.flash_work = bytearray(baseline)
            base_sha = hashlib.sha256(baseline).hexdigest()
            digest = hashlib.sha256(('%s|%s' % (';'.join(hex(b) for b in sorted(seen)), target_sha))
                                    .encode('ascii')).hexdigest()
            self.plan = {'sectors': sorted(seen), 'target_sha256': target_sha,
                         'nonce': nonce, 'nonce_consumed': True, 'base_sha256': base_sha}
            now = time.time()
            self.nonces_consumed[nonce] = {
                'consumed_at': now,
                'expires_at': now + USB_ARM_NONCE_TTL,       # 审计记录（UpgradeGate 2min 先例）；消费即永久作废
                'instance': id(self),                        # 进程实例绑定（重启后无 armed 态，构造性防复用）
                'plan_digest': digest,
                'base_sha256': base_sha,
                'note': 'arm@%s' % time.strftime('%H:%M:%S', time.localtime(now)),
            }
            return dict(self.plan)

    def disarm(self):
        """armed 主动作废（§3.1-2 + 评审修正 3）：丢弃副本=**显式回滚到 arm 前基线**（半擦状态
        不保留；armed 期间的写全在 flash_work 上，flash_sim 未动，丢弃即回滚）。未 armed 调用 →
        ValueError。恢复路径纪律：中断后重刷=检查半态（describe/FD05）→ disarm 回滚 → 重新 arm
        （新 nonce）重刷，不做「同计划幂等重跑」（擦前核验会失败）。会话层（usb_disarm）待第二段。"""
        with self.lock:
            if self.plan is None:
                raise ValueError('未 armed（无可作废的刷机会话）')
            self.flash_work = None
            self.plan = None
            self.erased = set()
            self.written = set()

    def reboot(self):
        """整机重启换身份（§3.3 + 评审修正 1/5）：rom→normal、loader 丢弃、BOT 在途状态清空、
        armed 写授权作废；**当前仿真 flash 内容保留**（刷后镜像固化进 flash_sim——§0/⑪/P7-③
        「再进 ROM 复读==S_target」；规格 §3.3 伪码的 flash_work=None 与验收判据相抵，按评审
        修正取三态模型）。journal 记 reboot 条目（armed 时含 work_sha256+target_match 供验收读）。
        成功返回 None；非 rom 身份返回错误字符串。会话层（usb_reboot 消息+断连重枚举）待第二段。"""
        with self.lock:
            if self.identity != 'rom':
                return '已是 normal 身份'
            entry = {'op': 'reboot'}
            if self.plan is not None and self.flash_work is not None:
                work_sha = hashlib.sha256(self.flash_work).hexdigest()
                entry['work_sha256'] = work_sha
                entry['target_match'] = (work_sha == self.plan.get('target_sha256'))
                self.flash_sim = bytes(self.flash_work)      # 刷后内容固化（reboot 不回滚物理 flash）
            self.identity = 'normal'
            self.phase = 'n/a'
            self.loaded = 0
            self.loader = bytearray(USB_LOADER_SIZE)
            self.plan = None                                  # 写授权撤销
            self.flash_work = None
            self.erased = set()
            self.written = set()
            self._rx = bytearray()                            # BOT 在途状态清空（评审修正 5）
            self._waiting = None
            self.journal.append(entry)
            return None

    def link_reset(self):
        """socket 断链时的 BOT 字节流清理（评审修正 5：CBW/OUT 数据相位半包不跨会话污染——
        M2 遗留：残留 _waiting 会吞掉新会话首条 CBW 的字节）。armed 与副本**保持**（断链不退出
        armed，§3.1——重连可见半态、可继续或 disarm）。会话层在连接关闭处调用（第二段接线）。"""
        with self.lock:
            self._rx = bytearray()
            self._waiting = None

    def _sector_erase(self, w):
        """FB01 擦 4KiB 扇区（§2.2 [实测] h3s_usb.py:167 / protocol.py:39-40：addr4 BE + 0xff
        补齐 16B CDB；无 DATA OUT、16B DATA IN 回显 opcode；尾段 14B [未实证 U1] 0 填充；
        置目标扇区为 NOR 擦除态 ff）。门链 §2.4-1..4；未 armed=M2 同形拒绝。"""
        cdb = w['cdb']
        if len(cdb) != 16:
            raise _Reject('FB01 CDB 须 16B（got %dB；缺地址/截短拒绝）' % len(cdb))   # 评审修正 4
        if cdb[6:16] != b'\xff' * 10:
            raise _Reject('FB01 CDB 尾段须 0xff 补齐（protocol.py:40 补齐纪律；错误填充拒绝）')
        self._rom_gate('FB01')
        if self.phase != 'loader-ready':
            raise _Reject('FB01 仅 loader-ready 相位（当前 %s）' % self.phase)
        if self.plan is None:
            raise _Reject('FB01 需要 armed（未确认的刷机会话不开写路径）')
        address = int.from_bytes(cdb[2:6], 'big')
        if address % USB_ERASE_SECTOR != 0 or not 0 <= address < USB_FLASH_SIZE:
            raise _Reject('FB01 地址须 4KiB 对齐且在 2MiB 内: 0x%x' % address)
        if address not in self.plan['sectors']:
            raise _Reject('FB01 计划外扇区 0x%x（计划=%s）'
                          % (address, [hex(s) for s in self.plan['sectors']]))
        if w['size'] != 16 or w['data_out']:
            raise _Reject('FB01 须 16B IN（got size=%d data_out=%s）' % (w['size'], w['data_out']))
        self.flash_work[address:address + USB_ERASE_SECTOR] = b'\xff' * USB_ERASE_SECTOR
        self.erased.add(address)
        for block in range(address, address + USB_ERASE_SECTOR, USB_SECTOR):
            self.written.discard(block)                       # 重擦后块可再写（评审修正 6 的重擦出口）
        self.journal.append({'op': 'erase', 'sector': address})
        r = bytearray(16)
        r[0], r[1] = 0xfb, 0x01                               # 回显 opcode；尾段 0 填充 [未实证 U1]
        return [bytes(r), self._csw(w['tag'])]

    def _sector_program(self, w):
        """FB04 512B 编程（§2.3 [实测] h3s_usb.py:171-172：addr4+len2 BE + 00 + crc16LE（XMODEM，
        binascii.crc_hqx）；DATA OUT 精确 512B、无 IN 响应）。门链 §2.4；先擦后写纪律（erased 门）
        + 未重擦的重复写拒绝（[保守] 仿真纪律，非真机行为证据——评审修正 6）。"""
        cdb, data = w['cdb'], w['buf']
        if len(cdb) != 16:
            raise _Reject('FB04 CDB 须 16B（got %dB；缺长度/缺 CRC/截短拒绝）' % len(cdb))   # 评审修正 4
        if cdb[11:16] != b'\xff' * 5:
            raise _Reject('FB04 CDB 尾段须 0xff 补齐（protocol.py:40 补齐纪律；错误填充拒绝）')
        self._rom_gate('FB04')
        if self.phase != 'loader-ready':
            raise _Reject('FB04 仅 loader-ready 相位（当前 %s）' % self.phase)
        if self.plan is None:
            raise _Reject('FB04 需要 armed（未确认的刷机会话不开写路径）')
        address = int.from_bytes(cdb[2:6], 'big')
        length = int.from_bytes(cdb[6:8], 'big')
        if not w['data_out'] or length != len(data) or length != USB_SECTOR:
            raise _Reject('FB04 须 512B OUT（got len=%d data=%dB）' % (length, len(data)))
        if address % USB_SECTOR != 0 or not 0 <= address < address + length <= USB_FLASH_SIZE:
            raise _Reject('FB04 地址须 512 对齐且在 2MiB 内: 0x%x' % address)
        sector = address // USB_ERASE_SECTOR * USB_ERASE_SECTOR
        if sector not in self.plan['sectors']:
            raise _Reject('FB04 计划外扇区 0x%x（计划=%s）'
                          % (sector, [hex(s) for s in self.plan['sectors']]))
        if sector not in self.erased:
            raise _Reject('FB04 目标扇区未擦除（先 FB01）: 0x%x' % sector)
        if address in self.written:
            raise _Reject('FB04 块 0x%x 已编程（未重擦的重复写拒绝——保守仿真纪律，非真机行为证据）' % address)
        if cdb[8] != 0 or int.from_bytes(cdb[9:11], 'little') != binascii.crc_hqx(data, 0):
            raise _Reject('FB04 CRC16-XMODEM 不符')
        self.flash_work[address:address + length] = data
        self.written.add(address)
        self.journal.append({'op': 'program', 'addr': address,
                             'sha256': hashlib.sha256(data).hexdigest()})
        return [self._csw(w['tag'])]                          # 无 IN 响应（§2.3：OUT 后直接 CSW）


class SimBridgeSession:
    """单客户端会话：JSON-lines 命令循环。

    静态模式：serve() 阻塞循环跑在会话线程内（SIMB1A 线程模型不变；BLE（sim-gatt）与
    USB（sim-usb）消息共存一个 handler——设备对象进程级共享）。
    世界模式：由世界服务循环（post_stop 线程）select 唤醒后调 pump() 喂字节——
    单线程纪律（machine 非线程安全），本类不再开线程。
    """

    def __init__(self, conn: socket.socket, addr, state, usb: MscBotDevice | None = None,
                 world: SimWorldDriver | None = None,
                 on_session_event=None) -> None:
        self.conn = conn
        self.addr = addr
        self.state = state
        self.usb = usb if usb is not None else MscBotDevice()
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
            if proto != PROTO or client not in ('sim-gatt', 'sim-usb'):
                self.send({'t': 'hello_err', 'detail': 'proto=%r client=%r（要 proto=%d client=sim-gatt|sim-usb）'
                           % (proto, client, PROTO)})
                log('hello 拒绝: proto=%r client=%r' % (proto, client))
                return False
            world = self.state.describe() if client == 'sim-gatt' else self.usb.describe()
            self.send({'t': 'hello_ok', 'world': world})
            log('hello 通过（proto=%d client=%s）' % (proto, client))
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
        # —— USB 面（M2，设计 §4.2/§4.4）——
        if kind == 'usb_scan':
            if not hello_done:
                self.send({'t': 'error', 'code': 'no_hello', 'detail': '先发 hello'})
                return False
            self.send({'t': 'usb_scan_result', 'devices': self.usb.scan()})
            return True
        if kind == 'usb_open':
            if not hello_done:
                self.send({'t': 'error', 'code': 'no_hello', 'detail': '先发 hello'})
                return False
            ok, detail = self.usb.open_key(message.get('key'))
            self.send({'t': 'usb_open_result', 'ok': ok, 'detail': detail})
            log('usb_open key=%r → %s' % (message.get('key'), 'ok' if ok else detail))
            return True
        if kind == 'usb_bulk_out':
            if not hello_done:
                self.send({'t': 'error', 'code': 'no_hello', 'detail': '先发 hello'})
                return False
            try:
                data = base64.b64decode(message.get('data', ''), validate=True)
            except (ValueError, TypeError) as exc:
                self.send({'t': 'error', 'code': 'bad_b64', 'detail': str(exc)})
                return False
            for transfer in self.usb.feed_out(data):   # 数据相位与 CSW 各自独立一条、长度精确
                self.send({'t': 'usb_bulk_in', 'data': base64.b64encode(transfer).decode('ascii')})
            return True
        if kind == 'usb_ctl':
            if not hello_done:
                self.send({'t': 'error', 'code': 'no_hello', 'detail': '先发 hello'})
                return False
            result, error = self.usb.control(message.get('len'))
            if error is not None:
                self.send({'t': 'error', 'code': 'usb_ctl', 'detail': error})   # 控制失败不断链（真实设备同款）
                log('usb_ctl len=%r 拒绝: %s' % (message.get('len'), error))
                return True
            self.send({'t': 'usb_ctl_result', 'data': base64.b64encode(result).decode('ascii')})
            if result == b'':                           # 进入 ROM：设备重启重枚举 → 断开会话
                log('usb_ctl len0：身份翻转 normal→rom，断开连接（重枚举）')
                return False
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

    返回（serve_loop(machine, hardware, stopped_cpu, stop=None) -> dict）：正常退出带
    {'world_served': 摘要}；世界死亡带 {'world_dead': {...}}（外层循环重建信号）；
    就绪自检不过带 {'sim_bridge_world_not_ready': {...}}（不猜，如实退出）。
    stop kwarg=explore 停点后回调的既有通道扩展(路 D1RUN,probe_boot 侧统一传)。
    """
    def serve_loop(machine, hardware, cpu, stop=None):
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

def serve(host: str, port: int, state: StaticFixtureState, usb: MscBotDevice | None = None,
          ready: threading.Event | None = None) -> None:
    listener = make_listener(host, port)
    if ready is not None:
        ready.set()                                   # 测试同步用：listen 就绪后再连（防 bind 竞态）
    log('bridge server（静态 fixture + USB MSC 设备端）监听 %s:%d，proto=%d' % (host, port, PROTO))
    active_lock = threading.Lock()             # 单活动客户端纪律（设计 §4.1：BLE/USB 共用）
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
            session = SimBridgeSession(conn, addr, state, usb)

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
    help='fixture JSON 文件 {"reads":{"47":[79,3,80,129,100],...}}（缺省用内置合成值；仅静态模式）')
    parser.add_argument('--flash-image', type=str, default=None,
                        help='2MiB raw flash fixture（tools/sim_flash_fixture.py 构造；缺省=无 FD05 后端）')
    parser.add_argument('--rom', type=str, default=None,
                        help='128KiB CPU ROM dump（双面共用：USB FD07 后端 + 世界模式 ROM 窗口；世界模式必填）')
    parser.add_argument('--identity', choices=['normal', 'rom'], default='normal',
                        help='USB 设备初始身份（默认 normal；进程级状态，跨连接保留）')
    parser.add_argument('--world', type=str, default=None, metavar='SNAPSHOT_DIR',
                        help='直调世界模式：快照目录（首轮自动 capture，之后 restore 复役）')
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
        flash = open(args.flash_image, 'rb').read() if args.flash_image else None
        rom_bytes = open(args.rom, 'rb').read() if args.rom else None
        usb = MscBotDevice(flash=flash, rom=rom_bytes, identity=args.identity)
        log('状态源=静态 fixture（synthetic-not-firmware）：读命中回预置值，写更新状态但写静默')
        log('USB MSC 设备端：identity=%s flash=%s rom=%s（M2 只读：无 Flash 写路径，白名单外 opcode 拒绝+记录）'
            % (usb.identity,
               ('%dB sha256=%s' % (len(flash), usb.flash_sha256[:16] + '…')) if flash is not None else '无',
               ('%dB sha256=%s' % (len(rom_bytes), usb.rom_sha256[:16] + '…')) if rom_bytes is not None else '无'))
        serve(host, port, state, usb)
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
