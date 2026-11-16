#!/usr/bin/env python3
"""SIMBRIDGE bridge server（路 SIMB1A M1-a 通道半边 + SIMB2A M2 USB 只读通道，
docs/sim-bridge-design.md §4.1/§4.2/§4.4）。

AVD 内 app（SimulationGattLink / SimulationUsbLink）经 adb reverse 连本进程：监听
127.0.0.1:7788（避开 18080 等常见占用，端口 CLI 可换），socket 命令循环跑 JSON-lines 协议
（一行一条 JSON、UTF-8、\\n 结尾、二进制 base64）。

**BLE 状态源 = 静态 fixture（synthetic-not-firmware）**：收到 app 真实会发的只读查询帧
（`FE DC BA C0 FF 00 04 | SN FE 01 cmd | EF`，cmd∈{0x2f 电量,0x17 ANC,…}）返回预置值，
让 app 的读环与 UI 真实更新、通道全链路打通。「直调半边」（停点世界 getter/setter 直调，
JLEQ park 判定+RCBACK 回执预研）对接后替换 `StaticFixtureState` 即可，socket 契约不变。

**USB 面 = MSC BOT 设备端（M2 只读，协议级仿真，设计 §4.6 诚实边界：不执行固件代码）**：
`MscBotDevice` 按 tools/usb-recovery 的钉死字节形状应答 CBW→数据→CSW（UsbScsi.java 的
host 端镜像），flash 后端 = 2MiB raw fixture（tools/sim_flash_fixture.py 从公开 UFW 构造），
FD07 内存窗后端 = 公开 CPU ROM dump（rom-read.md 发布件）。设备身份与 loader 相位是
**进程级状态**（跨 socket 重连保留——「进 ROM 后断开再扫描」仍见 ROM 身份；server 退出才
复位，设计 §4.4）。**红线内置：USB 通道无 Flash 写路径**——FB01/FB04/WRITE(10) 等白名单外
opcode 一律 CSW status=1 明确拒绝并记录；flash 后端 bytes 只读挂载（无写方法）。

协议（与 SimulationGattLink.java / SimulationUsbLink.java / 设计 §4.2 对齐，proto=1）：
  app→server {"t":"hello","proto":1,"client":"sim-gatt"|"sim-usb"}
  server→app {"t":"hello_ok","world":{...}}          # sim-gatt: static-fixture；sim-usb: sim-usb-msc
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
  控制面（宿主测试客户端用；app 不会发）：
  client→server {"t":"set_fixture","reads":{"47":[79,3,80,129,100],...}}
                      # 键=cmdid 十进制串，值=回执数据段（cmdid|n|payload）的整数列表

RCSP 语义（设计 §3 钉死的形状）：
  - 读 `FE 01 cmd` → 查 fixture → 命中则回 `FE DC BA 00 FF <len> 00 SN cmd n payload EF`
    （protocol.md:40 实测响应形，status=00、SN 回显请求）；未命中 n<1 → 静默不回（固件同款）。
  - 写 `cmd len data` → 更新 fixture 状态（后续读可见）但**不回写回执**——真机 0x17/0x1d
    setter 恒返 0=写静默（qcy-command17.asm:59），静态骨架不同步合成写回执（设计 §3 纪律）。
  - 标准 opcode（03/D4/C1/E1/E2）不走本层，M1 记日志不回（未实现桩，如实记录）。

线程模型：accept 循环允许多路接入，但同一时刻只允许一个活动连接（其余立刻回 busy 断开，
设计 §4.1 单活动客户端纪律——BLE 与 USB 共用此约束：数据会话活跃时 app 侧 scanJson 复用
数据会话 socket，不开新连接）。资源上限（设计 §4.2 执行契约①）：单帧 4KB / 解帧缓冲 64KB /
单行 64KB，超限断开报错。
"""
from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import socket
import struct
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


class _Reject(Exception):
    """设备端拒绝该命令：CSW status=1 + 记录（app 侧 exchange 对 CSW≠0/短读抛错=可见失败）。"""


class MscBotDevice:
    """MSC Bulk-Only Transport 设备端（M2 只读命令集，进程级状态）。

    - BOT 流解析：usb_bulk_out 消息只是 socket 分片，设备按字节流重组 CBW/OUT 数据相位
      （「USBC」签名重同步），应答以 usb_bulk_in 消息逐 USB IN transfer 发出——数据相位
      与 13B CSW 各自独立一条、长度精确（设计 §4.4 codex 契约；UsbScsi.java:127-136 拒短读）。
    - 相位机照 read_rom.py：fresh-rom →(FB06×46)→ FB08 → loader-started → FC0A →
      loader-ready；FD05/FD07 只在 loader-ready 后应答（app 的 loaderReady 门同源）。
    - 身份机：normal →(EP0 usb_ctl len0)→ rom；跨连接保留、server 退出才复位（设计 §4.4）。
    - 只读红线：flash 后端 bytes 无写方法（只读挂载语义）；FB01/FB04/FC0C/FC12/WRITE(10)
      等白名单外 opcode 一律 _Reject 并记录 self.rejected——**M2 不存在任何 Flash 写路径**。
    """

    def __init__(self, flash=None, rom=None, identity='normal'):
        if flash is not None and len(flash) != USB_FLASH_SIZE:
            raise ValueError('flash fixture 须恰为 2MiB raw（got %dB）' % len(flash))
        if rom is not None and len(rom) != USB_ROM_WINDOW:
            raise ValueError('ROM 后端须恰为 128KiB（got %dB）' % len(rom))
        if identity not in ('normal', 'rom'):
            raise ValueError('identity 须 normal|rom')
        self.flash = flash                                   # bytes：只读（无写路径）
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
        # BOT 字节流状态（feed_out 专用）
        self._rx = bytearray()
        self._waiting = None                                 # None=等 CBW；dict=命令在途

    # ---------------- 外观（world 横幅 / 枚举 / 打开） ----------------

    def describe(self):
        with self.lock:
            return {
                'mode': 'sim-usb-msc',
                'identity': self.identity,
                'loader_phase': self.phase,
                'loader_bytes': self.loaded,
                'flash_bytes': len(self.flash) if self.flash is not None else 0,
                'flash_sha256': self.flash_sha256,
                'rom_sha256': self.rom_sha256,
                'read_only': True,                           # M2 红线：无 Flash 写路径
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
        opcode = 0x12 if cdb[0] == 0x12 else ((cdb[0] << 8) | cdb[1])
        try:
            if w['lun'] != 0:
                raise _Reject('lun=%d 非 0' % w['lun'])
            if cdb[0] == 0x12:
                return self._inquiry(w)
            handler = {0xfb06: self._ram_write, 0xfb08: self._loader_start,
                       0xfc0a: self._online_query, 0xfd05: self._flash_read,
                       0xfd07: self._mem_read}.get(opcode)
            if handler is None:
                raise _Reject('opcode 0x%04x 不在 M2 只读白名单（USB 通道无写路径）' % opcode)
            return handler(w)
        except _Reject as exc:
            self.rejected.append({'opcode': '0x%04x' % opcode if opcode is not None else None,
                                  'reason': str(exc)})
            self.counts['rejected'] = self.counts.get('rejected', 0) + 1
            log('MSC 拒绝 opcode 0x%04x: %s' % (opcode, exc))
            return [self._csw(w['tag'], 1)]
        finally:
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
        if self.flash is None:
            raise _Reject('无 flash 后端（--flash-image 未提供）')
        return [self.flash[address:address + length], self._csw(w['tag'])]

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


class SimBridgeSession:
    """单客户端会话：JSON-lines 命令循环（阻塞，跑在会话线程内）。
    BLE（sim-gatt）与 USB（sim-usb）消息共存一个 handler——设备对象进程级共享。"""

    def __init__(self, conn: socket.socket, addr, state: StaticFixtureState,
                 usb: MscBotDevice | None = None) -> None:
        self.conn = conn
        self.addr = addr
        self.state = state
        self.usb = usb if usb is not None else MscBotDevice()
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


def serve(host: str, port: int, state: StaticFixtureState, usb: MscBotDevice | None = None,
          ready: threading.Event | None = None) -> None:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind((host, port))
    listener.listen(4)
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
                        help='fixture JSON 文件 {"reads":{"47":[79,3,80,129,100],...}}（缺省用内置合成值）')
    parser.add_argument('--flash-image', type=str, default=None,
                        help='2MiB raw flash fixture（tools/sim_flash_fixture.py 构造；缺省=无 FD05 后端）')
    parser.add_argument('--rom', type=str, default=None,
                        help='128KiB CPU ROM dump（rom-read.md 发布件；缺省=无 FD07 后端）')
    parser.add_argument('--identity', choices=['normal', 'rom'], default='normal',
                        help='USB 设备初始身份（默认 normal；进程级状态，跨连接保留）')
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
    flash = open(args.flash_image, 'rb').read() if args.flash_image else None
    rom = open(args.rom, 'rb').read() if args.rom else None
    usb = MscBotDevice(flash=flash, rom=rom, identity=args.identity)
    log('状态源=静态 fixture（synthetic-not-firmware）：读命中回预置值，写更新状态但写静默')
    log('USB MSC 设备端：identity=%s flash=%s rom=%s（M2 只读：无 Flash 写路径，白名单外 opcode 拒绝+记录）'
        % (usb.identity,
           ('%dB sha256=%s' % (len(flash), usb.flash_sha256[:16] + '…')) if flash is not None else '无',
           ('%dB sha256=%s' % (len(rom), usb.rom_sha256[:16] + '…')) if rom is not None else '无'))
    serve(host, port, state, usb)
    return 0


if __name__ == '__main__':
    sys.exit(main())
