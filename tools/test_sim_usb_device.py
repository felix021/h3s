#!/usr/bin/env python3
"""tools/sim_bridge_server.py USB 面（MscBotDevice + sim-usb 会话）的单元测试
（路 SIMB2A / M2 验收第 1 条：SCSI 命令字节级红绿双向，照 test_sim_bridge_server.py 风格）。

设备端单测（in-process）：host 侧最小 BOT 交换器（UsbScsi.java exchange 的 Python 镜像）
逐命令对拍——INQUIRY 身份字节、FB06×46 装载门（CRC/地址/相位红路径）、FB08/FC0A 相位机、
FD05/FD07 精确字节（含 2MiB 全片 SHA 相等）、白名单外 opcode（FB01/FB04/WRITE(10)/FC0C/FC12）
拒绝+记录（M2 只读红线）、normal 身份 bulk 拒绝、BOT 流分片/重同步、数据相位与 CSW
独立精确长度、tag 回显。
会话级单测（localhost socket）：hello(sim-usb)/world 横幅、usb_scan→usb_open→bulk 往返、
EP0 进入 ROM（len8 能力读→len0 翻转身份断连重枚举）、跨连接身份保持、busy 单活动连接。

M3 扩展段（SIMB3C 设备级，docs/sim-m3-flash-spec.md §5 + codex 评审修正 1-7）：FB01/FB04
字节级红绿双向（armed 门/计划门/形状门/CDB 长度+补齐门/先擦后写+重复写拒）、armed 状态机
（nonce 单次消费/copy-on-armed/disarm 显式回滚/reboot 三态——armed 撤销+刷后镜像保留+
BOT 在途清空）、断链 link_reset 半包不污染、N2 中断半态全片比较（独立构造预期镜像）、
原始资产 bytes 全程不可变（红线实证）、planner 计划直喂 arm 的闭环。
M3 会话层段（SIMB3S 第二段）：usb_arm/usb_disarm/usb_reboot 消息往返（ok=false 不断链/
reboot 成功断连重枚举）、断链清理接线（close()→link_reset——codex P1 复现：OUT 半相位
断开→新连接 INQUIRY 必须完整应答，另含 CBW 半包/写完 CSW 未收两种断链）、断链 armed
保持+FD05 读副本（socket 级）、busy 单活动不破、--arm-plan CLI 冒烟（hello 横幅即 armed）。
"""
import base64
import binascii
import hashlib
import json
import re
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sim_bridge_server as sbs
import sim_restore_planner as planner
from test_sim_bridge_server import Client

FLASH = bytes((i * 7 + 3) & 0xff for i in range(sbs.USB_FLASH_SIZE))       # 合成 2MiB（协议测试用）
ROM = bytes((i * 13 + 5) & 0xff for i in range(sbs.USB_ROM_WINDOW))        # 合成 128KiB 窗
LOADER = bytes((i * 11 + 1) & 0xff for i in range(sbs.USB_LOADER_SIZE))    # 合成 loader（server 不校验摘要）


def cbw(tag, size, data_out, cdb):
    """31B CBW（UsbScsi.cbw / protocol.py exchange 同形）。"""
    return (b'USBC' + struct.pack('<IIBBB', tag, size, 0 if data_out else 0x80, 0, len(cdb))
            + cdb.ljust(16, b'\x00'))


def vendor_cdb(opcode, args=b''):
    """16B vendor CDB（opcode 2B BE + args + 0xff 填充，UsbScsi.vendorCdb 同形）。"""
    return (opcode.to_bytes(2, 'big') + args).ljust(16, b'\xff')


def csw(tag, status=0):
    return b'USBS' + struct.pack('<I', tag) + b'\x00\x00\x00\x00' + bytes([status])


class BotHost:
    """host 侧最小 BOT 交换器：CBW→(OUT 数据)→收集 IN transfer（app 的 UsbScsi.exchange 同步形）。
    绿路径用 exchange()（断言精确形状+status 0）；红路径直接看 feed_out 返回。"""

    def __init__(self, dev):
        self.dev = dev
        self.tag = 0

    def exchange(self, cdb, size=0, data=None):
        """绿路径：返回数据相位字节（无数据相位返回 b''）；形状/CSW 任何不符直接断言失败。"""
        self.tag += 1
        ins = self.dev.feed_out(cbw(self.tag, len(data) if data is not None else size, data is not None, cdb))
        if data is not None:
            ins += self.dev.feed_out(data)
        expect_transfers = (1 if (data is None and size > 0) else 0) + 1
        assert len(ins) == expect_transfers, 'IN transfer 数 %d≠%d: %r' % (
            len(ins), expect_transfers, [t.hex() for t in ins])
        if data is None and size > 0:
            assert len(ins[0]) == size, '数据相位长度 %d≠%d' % (len(ins[0]), size)
        assert ins[-1] == csw(self.tag), 'CSW 不符: %s' % ins[-1].hex()
        return ins[0] if (data is None and size > 0) else b''


def rom_device(flash=FLASH, rom=ROM, identity='normal'):
    dev = sbs.MscBotDevice(flash=flash, rom=rom, identity=identity)
    dev.control(0)                                       # normal → rom（单测直取 rom 身份起点）
    return dev


def ready_device():
    """rom 身份 + loader 全序列已过（FB06×46+FB08+FC0A）→ loader-ready。"""
    dev = rom_device()
    host = BotHost(dev)
    for p in range(0, sbs.USB_LOADER_SIZE, sbs.USB_SECTOR):
        chunk = LOADER[p:p + sbs.USB_SECTOR]
        args = ((sbs.USB_LOADER_ADDRESS + p).to_bytes(4, 'big') + len(chunk).to_bytes(2, 'big')
                + b'\x00' + binascii.crc_hqx(chunk, 0).to_bytes(2, 'little'))
        host.exchange(vendor_cdb(0xfb06, args), data=chunk)
    host.exchange(vendor_cdb(0xfb08, bytes.fromhex('001200000001')), size=16)
    host.exchange(vendor_cdb(0xfc0a), size=16)
    assert dev.phase == 'loader-ready'
    return dev, host


class MscDeviceTest(unittest.TestCase):
    """字节级红绿双向（设备端 in-process，无 socket）。"""

    def test_inquiry_identity_bytes(self):
        dev, host = rom_device(), None
        host = BotHost(dev)
        raw = host.exchange(bytes([0x12, 0, 0, 0, 36, 0]), size=36)
        self.assertEqual(len(raw), 36)
        self.assertEqual(raw[8:16], b'BR28    ')                 # vendor（usb-recovery.md 实测）
        self.assertEqual(raw[16:32], b'UBOOT1.00' + b' ' * 7)    # product（实测）
        self.assertEqual(raw[0], 0x00)
        self.assertEqual(dev.counts.get('0012'), 1)

    def test_normal_identity_bulk_rejected(self):
        """normal 身份无 MSC 接口：INQUIRY/FB06 一律拒绝+记录（app 侧短读/CSW 校验可见失败）。"""
        dev = sbs.MscBotDevice(flash=FLASH, rom=ROM)
        host = BotHost(dev)
        ins = dev.feed_out(cbw(1, 36, False, bytes([0x12, 0, 0, 0, 36, 0])))
        self.assertEqual(ins, [csw(1, 1)])
        self.assertTrue(any('rom 身份' in r['reason'] for r in dev.rejected))

    def test_loader_sequence_gates_red_and_green(self):
        dev = sbs.MscBotDevice(flash=FLASH, rom=ROM)
        dev.control(0)
        host = BotHost(dev)
        # 红：FD05 在装载前
        ins = dev.feed_out(cbw(1, 512, False, vendor_cdb(0xfd05, (0).to_bytes(4, 'big') + (512).to_bytes(2, 'big'))))
        self.assertEqual(ins, [csw(1, 1)])
        # 红：FB06 CRC 不符（第 1 块，CRC 填 0）
        chunk = LOADER[:512]
        bad = ((sbs.USB_LOADER_ADDRESS).to_bytes(4, 'big') + (512).to_bytes(2, 'big')
               + b'\x00' + (0).to_bytes(2, 'little'))
        ins = dev.feed_out(cbw(2, 512, True, vendor_cdb(0xfb06, bad))) + dev.feed_out(chunk)
        self.assertEqual(ins, [csw(2, 1)])
        self.assertEqual(dev.loaded, 0)                          # 未存入
        # 红：FB06 地址不连续（跳块）
        args = ((sbs.USB_LOADER_ADDRESS + 512).to_bytes(4, 'big') + (512).to_bytes(2, 'big')
                + b'\x00' + binascii.crc_hqx(chunk, 0).to_bytes(2, 'little'))
        ins = dev.feed_out(cbw(3, 512, True, vendor_cdb(0xfb06, args))) + dev.feed_out(chunk)
        self.assertEqual(ins, [csw(3, 1)])
        # 红：FB08 在 loader 未完整前
        ins = dev.feed_out(cbw(4, 16, False, vendor_cdb(0xfb08, bytes.fromhex('001200000001'))))
        self.assertEqual(ins, [csw(4, 1)])
        # 绿：46×512 连续装载（CRC16-XMODEM 小端）
        for p in range(0, sbs.USB_LOADER_SIZE, sbs.USB_SECTOR):
            blk = LOADER[p:p + sbs.USB_SECTOR]
            args = ((sbs.USB_LOADER_ADDRESS + p).to_bytes(4, 'big') + len(blk).to_bytes(2, 'big')
                    + b'\x00' + binascii.crc_hqx(blk, 0).to_bytes(2, 'little'))
            host.exchange(vendor_cdb(0xfb06, args), data=blk)
        self.assertEqual(dev.loaded, sbs.USB_LOADER_SIZE)
        # 绿：FB08 固定实参 → 16B 回显 + 相位 loader-started
        r = host.exchange(vendor_cdb(0xfb08, bytes.fromhex('001200000001')), size=16)
        self.assertEqual(r[:2], b'\xfb\x08')
        self.assertEqual(len(r), 16)
        self.assertEqual(dev.phase, 'loader-started')
        # 红：FB08 实参不符（入口 0x120004）
        dev2 = sbs.MscBotDevice(flash=FLASH, rom=ROM)
        dev2.control(0)
        # （绿装载略——用 ready_device 的等价路径单独覆盖；此处只钉实参门）
        # 绿：FC0A → fc0a03 + 相位 loader-ready
        q = host.exchange(vendor_cdb(0xfc0a), size=16)
        self.assertEqual(q[:3], bytes.fromhex('fc0a03'))
        self.assertEqual(q[3:6], bytes.fromhex('cd7015'))
        self.assertEqual(int.from_bytes(q[6:10], 'big'), sbs.USB_FLASH_SIZE)
        self.assertEqual(dev.phase, 'loader-ready')

    def test_fb08_wrong_args_rejected(self):
        dev, _ = ready_device()
        dev.phase = 'fresh-rom'                                 # 回拨相位只测 FB08 实参门
        dev.loaded = sbs.USB_LOADER_SIZE
        ins = dev.feed_out(cbw(9, 16, False, vendor_cdb(0xfb08, bytes.fromhex('001200040001'))))
        self.assertEqual(ins, [csw(9, 1)])                       # read_rom.py:57 同款红路径

    def test_fd05_exact_bytes_and_full_chip_sha(self):
        dev, host = ready_device()
        for address in (0, 512, 0x1000, 0xe5e00, 0x1ffe00):
            r = host.exchange(vendor_cdb(0xfd05, address.to_bytes(4, 'big') + (512).to_bytes(2, 'big')), size=512)
            self.assertEqual(r, FLASH[address:address + 512])
        # 红：未对齐/长度≠512/越界
        for args in ((1).to_bytes(4, 'big') + (512).to_bytes(2, 'big'),
                     (0).to_bytes(4, 'big') + (256).to_bytes(2, 'big'),
                     (sbs.USB_FLASH_SIZE).to_bytes(4, 'big') + (512).to_bytes(2, 'big')):
            self.tag = getattr(self, 'tag', 100) + 1
            ins = dev.feed_out(cbw(self.tag, 512, False, vendor_cdb(0xfd05, args)))
            self.assertEqual(ins, [csw(self.tag, 1)], args.hex())
        # 全片 4096×FD05 == fixture（app 备份流的 server 半边，SHA 相等判据）
        out = bytearray()
        for address in range(0, sbs.USB_FLASH_SIZE, sbs.USB_SECTOR):
            out += host.exchange(vendor_cdb(0xfd05, address.to_bytes(4, 'big') + (512).to_bytes(2, 'big')), size=512)
        self.assertEqual(hashlib.sha256(out).hexdigest(), hashlib.sha256(FLASH).hexdigest())

    def test_fd07_rom_window(self):
        dev, host = ready_device()
        for address, length in ((0, 128), (0x1fdc0, 128), (0x1fff8, 8), (0x1234, 1)):
            r = host.exchange(vendor_cdb(0xfd07, address.to_bytes(4, 'big') + length.to_bytes(2, 'big')), size=length)
            self.assertEqual(r, ROM[address:address + length])
        # 红：>128B（512B 实测断链的同款纪律）/越窗
        for address, length in ((0, 512), (0x1fff8, 16), (sbs.USB_ROM_WINDOW, 1)):
            self.tag = getattr(self, 'tag', 200) + 1
            ins = dev.feed_out(cbw(self.tag, length, False, vendor_cdb(0xfd07, address.to_bytes(4, 'big') + length.to_bytes(2, 'big'))))
            self.assertEqual(ins, [csw(self.tag, 1)], (hex(address), length))
        # 红：无 ROM 后端
        dev2, host2 = ready_device()
        dev2.rom = None
        ins = dev2.feed_out(cbw(7, 128, False, vendor_cdb(0xfd07, (0).to_bytes(4, 'big') + (128).to_bytes(2, 'big'))))
        self.assertEqual(ins, [csw(7, 1)])

    def test_write_opcodes_rejected_no_flash_write_path(self):
        """M2 红线：FB01/FB04/FC0C/FC12/WRITE(10)/FB00 一律 CSW status=1 + 记录；fixture 不可变。"""
        dev, _ = ready_device()
        sha_before = hashlib.sha256(dev.flash).hexdigest()
        attempts = [
            (0xfb01, (0x0ee000).to_bytes(4, 'big'), 16, None),                     # 擦除扇区
            (0xfb04, (0xee000).to_bytes(4, 'big') + (512).to_bytes(2, 'big') + b'\x00' + b'\x00\x00', 512, b'\xaa' * 512),  # 写 Flash
            (0xfc0c, b'', 16, None),
            (0xfc12, b'', 16, None),
            (0xfb00, (0).to_bytes(4, 'big'), 16, None),                            # 整片擦除
            (0x2a00, b'', 512, None),                                              # WRITE(10) 标准 SCSI 写
            (0x002a, b'', 512, None),                                              # 2B BE opcode=0x002a
        ]
        for i, (opcode, args, size, data) in enumerate(attempts):
            ins = dev.feed_out(cbw(i + 1, size, data is not None, vendor_cdb(opcode, args)))
            if data is not None:
                ins += dev.feed_out(data)
            self.assertEqual(ins, [csw(i + 1, 1)], 'opcode 0x%04x 应被拒绝' % opcode)
        rejected_ops = {r['opcode'] for r in dev.rejected}
        for opcode, _, _, _ in attempts:
            self.assertIn('0x%04x' % opcode, rejected_ops)
        self.assertEqual(sha_before, hashlib.sha256(dev.flash).hexdigest())
        self.assertEqual(dev.counts.get('rejected'), len(attempts))

    def test_control_state_machine_and_identity_persistence(self):
        dev = sbs.MscBotDevice(flash=FLASH, rom=ROM)
        self.assertEqual(dev.scan()[0]['vid'], sbs.USB_VID_QCY)
        data, err = dev.control(8)
        self.assertEqual((data, err), (b'\xff' * 8, None))       # 能力读（usb-recovery.md 实测值）
        data, err = dev.control(0)
        self.assertEqual((data, err), (b'', None))               # 进入请求：翻转身份
        self.assertEqual(dev.scan()[0]['vid'], sbs.USB_VID_ROM)
        self.assertEqual(dev.phase, 'fresh-rom')
        data, err = dev.control(8)
        self.assertIsNone(data)                                  # rom 身份不再应答
        self.assertIsNotNone(err)
        data, err = dev.control(3)
        self.assertIsNone(data)                                  # 未知长度拒绝（不臆造控制行为）
        # 跨「连接」保留：新会话对象拿同一设备仍见 rom 身份（进程级状态）
        self.assertEqual(dev.open_key('sim:usb:3654:4a55')[0], False)
        self.assertEqual(dev.open_key('sim:usb:4c4a:3442')[0], True)

    def test_bot_stream_fragmentation_and_resync(self):
        """CBW/数据相位跨多条 usb_bulk_out 分片 + 垃圾前缀重同步（设计 §4.4 流状态机）。"""
        dev, _ = ready_device()
        c = vendor_cdb(0xfd05, (0x800).to_bytes(4, 'big') + (512).to_bytes(2, 'big'))
        frame = cbw(77, 512, False, c)
        ins = []
        ins += dev.feed_out(b'\xde\xad')                         # 垃圾前缀
        ins += dev.feed_out(frame[:10])                          # CBW 拆三片
        ins += dev.feed_out(frame[10:20])
        ins += dev.feed_out(frame[20:])
        self.assertEqual(ins, [FLASH[0x800:0x800 + 512], csw(77)])
        # OUT 数据相位拆两片（FB06 需 fresh-rom：重置相位）
        dev.phase, dev.loaded = 'fresh-rom', 0
        chunk = LOADER[:512]
        args = ((sbs.USB_LOADER_ADDRESS).to_bytes(4, 'big') + (512).to_bytes(2, 'big')
                + b'\x00' + binascii.crc_hqx(chunk, 0).to_bytes(2, 'little'))
        f2 = cbw(78, 512, True, vendor_cdb(0xfb06, args))
        ins = dev.feed_out(f2) + dev.feed_out(chunk[:100]) + dev.feed_out(chunk[100:])
        self.assertEqual(ins, [csw(78)])
        self.assertEqual(dev.loaded, 512)

    def test_data_and_csw_separate_exact_transfers(self):
        """FD05 的数据相位与 13B CSW 必须各自独立一条、长度精确（设计 §4.4 codex 契约）。"""
        dev, _ = ready_device()
        c = vendor_cdb(0xfd05, (0).to_bytes(4, 'big') + (512).to_bytes(2, 'big'))
        ins = dev.feed_out(cbw(5, 512, False, c))
        self.assertEqual([len(t) for t in ins], [512, 13])


class UsbSessionTest(unittest.TestCase):
    """socket 级（JSON-lines 会话）：hello(sim-usb)/scan/open/bulk/ctl 全 localhost。"""

    def setUp(self):
        self.flash = bytes((i * 5 + 1) & 0xff for i in range(sbs.USB_FLASH_SIZE))
        self.rom = bytes((i * 9 + 7) & 0xff for i in range(sbs.USB_ROM_WINDOW))
        self.usb = sbs.MscBotDevice(flash=self.flash, rom=self.rom)
        # 并发隔离（FLAKYFIX）：listener 在测试线程内同步 bind 127.0.0.1:0——内核随机端口
        # bind 即独占，根除 free_port 探测式「bind 0→读号→关→稍后再 bind」的跨进程双分配
        # 窗口；bind+listen 已同步完成，无需 ready Event 再防 bind 竞态。
        self.listener = sbs.make_listener('127.0.0.1', 0)
        self.port = self.listener.getsockname()[1]
        threading.Thread(target=sbs.serve_listener,
                         args=(self.listener, sbs.DEFAULT_FIXTURE, self.usb), daemon=True).start()

    def hello(self):
        # connect_with（FLAKYFIX）：断连后立即重 hello 时，旧会话线程的 close→link_reset→
        # lock.release 是异步三步，裸 Client 会偶发吃到 busy（并发测试实例抢 CPU 放大窗口；
        # 复现：4 进程并发同模式 600 迭代 87 次 busy，单进程 0）。busy 短退避是对异步拆链的
        # 正确客户端容忍（M3 会话层 connect_with 同理），非掩盖 server 语义的重试兜底。
        client, ok = connect_with(self.port, {'t': 'hello', 'proto': sbs.PROTO, 'client': 'sim-usb'})
        self.assertEqual(ok.get('t'), 'hello_ok')
        self.assertEqual(ok['world']['mode'], 'sim-usb-msc')
        self.assertEqual(ok['world']['read_only'], True)
        return client

    def test_scan_open_bulk_roundtrip(self):
        client = self.hello()
        try:
            client.send({'t': 'usb_scan'})
            scan = client.drain_until('usb_scan_result')
            self.assertEqual(scan['devices'][0]['vid'], sbs.USB_VID_QCY)
            key = scan['devices'][0]['key']
            client.send({'t': 'usb_open', 'key': key})
            self.assertTrue(client.drain_until('usb_open_result')['ok'])
            # 进 ROM（len0）后旧 key 过期——先做一轮 INQUIRY（rom 身份门内）
            client.send({'t': 'usb_ctl', 'len': 8})
            result = client.drain_until('usb_ctl_result')
            self.assertEqual(base64.b64decode(result['data']), b'\xff' * 8)
            client.send({'t': 'usb_ctl', 'len': 0})
            client.drain_until('usb_ctl_result')
            self.assertIsNone(client.recv(), '进入 ROM 后应断开（重枚举）')
        finally:
            client.close()
        # 重枚举后：rom 身份跨连接保持 + 旧 key 打开失败 + bulk 往返字节级
        client = self.hello()
        try:
            client.send({'t': 'usb_scan'})
            scan = client.drain_until('usb_scan_result')
            self.assertEqual(scan['devices'][0]['vid'], sbs.USB_VID_ROM)
            rom_key = scan['devices'][0]['key']
            client.send({'t': 'usb_open', 'key': 'sim:usb:3654:4a55'})
            self.assertFalse(client.drain_until('usb_open_result')['ok'], '旧 key 应过期')
            client.send({'t': 'usb_open', 'key': rom_key})
            self.assertTrue(client.drain_until('usb_open_result')['ok'])
            c = bytes([0x12, 0, 0, 0, 36, 0])
            client.send({'t': 'usb_bulk_out', 'data': base64.b64encode(cbw(1, 36, False, c)).decode()})
            data = client.drain_until('usb_bulk_in')
            self.assertEqual(len(base64.b64decode(data['data'])), 36)
            csw_msg = client.drain_until('usb_bulk_in')
            self.assertEqual(base64.b64decode(csw_msg['data']), csw(1, 0))
        finally:
            client.close()

    def test_loader_and_fd05_over_socket(self):
        """socket 全链路：装载序列 46×FB06+FB08+FC0A → FD05 扇区读（app 备份流的通道半边）。"""
        client = self.hello()
        try:
            self.usb.control(0)                             # 直取 rom 起点（控制面单测已覆盖 len0 路径）
            client.send({'t': 'usb_open', 'key': self.usb.scan()[0]['key']})
            self.assertTrue(client.drain_until('usb_open_result')['ok'])
            host_tag = [0]

            def exchange(cdb, size=0, data=None):
                host_tag[0] += 1
                client.send({'t': 'usb_bulk_out', 'data': base64.b64encode(
                    cbw(host_tag[0], len(data) if data is not None else size, data is not None, cdb)).decode()})
                if data is not None:
                    client.send({'t': 'usb_bulk_out', 'data': base64.b64encode(data).decode()})
                result = b''
                if data is None and size > 0:
                    result = base64.b64decode(client.drain_until('usb_bulk_in')['data'])
                got = base64.b64decode(client.drain_until('usb_bulk_in')['data'])
                self.assertEqual(got, csw(host_tag[0]))
                return result

            for p in range(0, sbs.USB_LOADER_SIZE, sbs.USB_SECTOR):
                chunk = LOADER[p:p + sbs.USB_SECTOR]
                args = ((sbs.USB_LOADER_ADDRESS + p).to_bytes(4, 'big') + len(chunk).to_bytes(2, 'big')
                        + b'\x00' + binascii.crc_hqx(chunk, 0).to_bytes(2, 'little'))
                exchange(vendor_cdb(0xfb06, args), data=chunk)
            exchange(vendor_cdb(0xfb08, bytes.fromhex('001200000001')), size=16)
            self.assertEqual(exchange(vendor_cdb(0xfc0a), size=16)[:3], bytes.fromhex('fc0a03'))
            for address in (0, 0x10000, 0x1ff800):
                r = exchange(vendor_cdb(0xfd05, address.to_bytes(4, 'big') + (512).to_bytes(2, 'big')), size=512)
                self.assertEqual(r, self.flash[address:address + 512])
        finally:
            client.close()

    def test_hello_wrong_proto_rejected(self):
        client = Client(self.port)
        try:
            client.send({'t': 'hello', 'proto': 99, 'client': 'sim-usb'})
            self.assertEqual(client.recv().get('t'), 'hello_err')
            self.assertIsNone(client.recv())
        finally:
            client.close()

    def test_usb_messages_require_hello(self):
        client = Client(self.port)
        try:
            client.send({'t': 'usb_scan'})
            self.assertEqual(client.recv().get('t'), 'error')
        finally:
            client.close()

    def test_busy_single_active_connection(self):
        first = self.hello()
        try:
            second = Client(self.port)
            try:
                self.assertEqual(second.recv().get('t'), 'busy')
            finally:
                second.close()
        finally:
            first.close()


# ================================ M3：armed 刷机（SIMB3C 设备级） ================================

# 六扇区形状照 image.ORDER（同地址复用——合成 FLASH 全片非 ff 模式，满足评审修正 7 的
# 「待擦扇区预置非 ff 数据」基线要求）。
M3_SECTORS = (0xf3000, 0x113000, 0x166000, 0x1d0000, 0x1d1000, 0xee000)
TARGET = bytearray(FLASH)
for _s in M3_SECTORS:
    TARGET[_s:_s + 0x1000] = bytes((i * 5 + 17) & 0xff for i in range(0x1000))
TARGET = bytes(TARGET)
S0 = hashlib.sha256(FLASH).hexdigest()
S_TARGET = hashlib.sha256(TARGET).hexdigest()


def fd05_read(host, address, length=512):
    return host.exchange(vendor_cdb(0xfd05, address.to_bytes(4, 'big') + length.to_bytes(2, 'big')),
                         size=length)


def fd05_full(host):
    out = bytearray()
    for address in range(0, sbs.USB_FLASH_SIZE, sbs.USB_SECTOR):
        out += fd05_read(host, address)
    return bytes(out)


def fb04_args(address, chunk, crc=None):
    return (address.to_bytes(4, 'big') + len(chunk).to_bytes(2, 'big') + b'\x00'
            + (binascii.crc_hqx(chunk, 0) if crc is None else crc).to_bytes(2, 'little'))


def arm_with(dev, sectors=M3_SECTORS, target=TARGET, nonce='nonce-1'):
    return dev.arm({'sectors': list(sectors), 'target_sha256': hashlib.sha256(target).hexdigest(),
                    'nonce': nonce})


def flash_sector(host, sector, target=TARGET):
    """单扇区擦写（照 h3s_usb.py:162-175 逐句：擦→读 ff→8×512 写→读比对）。绿路径断言内置。"""
    host.exchange(vendor_cdb(0xfb01, sector.to_bytes(4, 'big')), size=16)
    got = b''.join(fd05_read(host, p) for p in range(sector, sector + 0x1000, 512))
    assert got == b'\xff' * 0x1000, '擦除验证失败 @0x%x' % sector
    for p in range(sector, sector + 0x1000, 512):
        chunk = target[p:p + 512]
        host.exchange(vendor_cdb(0xfb04, fb04_args(p, chunk)), data=chunk)
    got = b''.join(fd05_read(host, p) for p in range(sector, sector + 0x1000, 512))
    assert got == target[sector:sector + 0x1000], '扇区编程验证失败 @0x%x' % sector


def load_loader(host):
    """fresh-rom → loader-ready 装载序列（reboot 后重进 ROM 复用）。"""
    for p in range(0, sbs.USB_LOADER_SIZE, sbs.USB_SECTOR):
        chunk = LOADER[p:p + sbs.USB_SECTOR]
        args = ((sbs.USB_LOADER_ADDRESS + p).to_bytes(4, 'big') + len(chunk).to_bytes(2, 'big')
                + b'\x00' + binascii.crc_hqx(chunk, 0).to_bytes(2, 'little'))
        host.exchange(vendor_cdb(0xfb06, args), data=chunk)
    host.exchange(vendor_cdb(0xfb08, bytes.fromhex('001200000001')), size=16)
    host.exchange(vendor_cdb(0xfc0a), size=16)


class M3FlashDeviceTest(unittest.TestCase):
    """M3 设备级：FB01/FB04 armed 门内字节级红绿双向 + 三态状态机 + 红线实证（in-process）。"""

    def test_not_armed_fb01_fb04_rejected_like_m2(self):
        """N1：未 armed 时 FB01/FB04=CSW status=1+记录（拒绝理由换成 armed 门，M2 形状一致）。"""
        dev, host = ready_device()
        sector = M3_SECTORS[0]
        ins = dev.feed_out(cbw(1, 16, False, vendor_cdb(0xfb01, sector.to_bytes(4, 'big'))))
        self.assertEqual(ins, [csw(1, 1)])
        chunk = TARGET[sector:sector + 512]
        ins = dev.feed_out(cbw(2, 512, True, vendor_cdb(0xfb04, fb04_args(sector, chunk)))) \
            + dev.feed_out(chunk)
        self.assertEqual(ins, [csw(2, 1)])
        reasons = [r['reason'] for r in dev.rejected]
        self.assertTrue(any('armed' in r for r in reasons), reasons)
        self.assertIsNone(dev.flash_work)                       # 副本未建立
        self.assertFalse(dev.describe()['armed'])
        self.assertIsNone(dev.describe()['work_sha256'])
        self.assertEqual(fd05_read(host, sector), FLASH[sector:sector + 512])   # FD05 仍读原始
        self.assertEqual(dev.counts.get('rejected'), 2)

    def test_arm_validation_and_duplicate_arm(self):
        """arm 校验负例（§3.1）+ armed 期间重复 arm=busy 拒。"""
        dev, _ = ready_device()
        bad_plans = [
            {'sectors': [], 'target_sha256': S_TARGET, 'nonce': 'n'},                # 空扇区
            {'sectors': [0x1001], 'target_sha256': S_TARGET, 'nonce': 'n'},          # 非 4KiB 对齐
            {'sectors': [sbs.USB_FLASH_SIZE], 'target_sha256': S_TARGET, 'nonce': 'n'},  # 越界
            {'sectors': [0x1000, 0x1000], 'target_sha256': S_TARGET, 'nonce': 'n'},  # 重复
            {'sectors': ['0x1000'], 'target_sha256': S_TARGET, 'nonce': 'n'},        # 非 int
            {'sectors': [0x1000], 'target_sha256': S_TARGET, 'nonce': ''},           # 空 nonce
            {'sectors': [0x1000], 'target_sha256': 'xyz', 'nonce': 'n'},             # 坏 target SHA
            {'sectors': [0x1000], 'nonce': 'n'},                                     # 缺 target SHA
        ]
        for plan in bad_plans:
            with self.assertRaises(ValueError, msg=repr(plan)):
                dev.arm(plan)
        self.assertIsNone(dev.plan)
        granted = arm_with(dev, nonce='n-ok')
        self.assertEqual(granted['sectors'], sorted(M3_SECTORS))
        self.assertEqual(granted['base_sha256'], S0)            # 副本基线=当前仿真 flash
        d = dev.describe()
        self.assertTrue(d['armed'])
        self.assertEqual(d['read_only'], False)                 # M2 字段语义更新：armed=可写
        self.assertEqual(d['work_sha256'], S0)                  # P3-②：副本=基线拷贝
        self.assertEqual(d['sim_sha256'], S0)
        with self.assertRaisesRegex(ValueError, 'busy'):
            arm_with(dev, nonce='n-other')                      # armed 期间重复 arm（无论 nonce）

    def test_arm_requires_flash_backend(self):
        dev = sbs.MscBotDevice(flash=None, rom=ROM)
        with self.assertRaisesRegex(ValueError, '无 flash 后端'):
            dev.arm({'sectors': [0x1000], 'target_sha256': S_TARGET, 'nonce': 'n'})

    def test_nonce_single_use_across_disarm_and_reboot(self):
        """修正 2：nonce 消费即作废——armed 期间（busy 门）/disarm 后/reboot 后同 nonce 全拒。"""
        dev, host = ready_device()
        arm_with(dev, nonce='N1')
        with self.assertRaisesRegex(ValueError, 'busy'):
            arm_with(dev, nonce='N1')                           # armed 期间重复 arm=busy（无论新旧 nonce）
        dev.disarm()
        with self.assertRaisesRegex(ValueError, '已消费'):
            arm_with(dev, nonce='N1')                           # disarm 后旧 nonce 失效
        arm_with(dev, nonce='N2')                               # 新 nonce 可用
        flash_sector(host, M3_SECTORS[0])
        self.assertIsNone(dev.reboot())
        with self.assertRaisesRegex(ValueError, '已消费'):
            arm_with(dev, nonce='N1')                           # reboot 后旧 nonce 失效
        with self.assertRaisesRegex(ValueError, '已消费'):
            arm_with(dev, nonce='N2')
        record = dev.nonces_consumed['N1']                      # 消费记录绑定（修正 2）
        self.assertIn('plan_digest', record)
        self.assertEqual(record['base_sha256'], S0)
        self.assertGreater(record['expires_at'], record['consumed_at'])

    def test_fb01_erase_green_and_readback(self):
        """P4 erase 半边：16B 回显 fb01（尾段 0 填充 U1）→ FD05×8==ff → journal/erased。"""
        dev, host = ready_device()
        arm_with(dev)
        sector = M3_SECTORS[0]
        r = host.exchange(vendor_cdb(0xfb01, sector.to_bytes(4, 'big')), size=16)
        self.assertEqual(r[:2], b'\xfb\x01')                    # protocol.py:42-44 回显校验规则
        self.assertEqual(r[2:], b'\x00' * 14)                   # U1：尾段 0 填充（协议级合成标注）
        got = b''.join(fd05_read(host, p) for p in range(sector, sector + 0x1000, 512))
        self.assertEqual(got, b'\xff' * 0x1000)                 # 擦后立读全 ff（usb-recovery.md:106）
        self.assertEqual(dev.journal, [{'op': 'erase', 'sector': sector}])
        self.assertEqual(dev.erased, {sector})
        self.assertNotEqual(dev.flash_work[sector:sector + 0x1000], FLASH[sector:sector + 0x1000])

    def test_fb04_program_green_and_journal(self):
        """P4 program 半边：FB04 无 IN 响应（OUT 后直接 CSW）→ 读回==target → journal 8 条。"""
        dev, host = ready_device()
        arm_with(dev)
        sector = M3_SECTORS[0]
        host.exchange(vendor_cdb(0xfb01, sector.to_bytes(4, 'big')), size=16)
        for p in range(sector, sector + 0x1000, 512):
            chunk = TARGET[p:p + 512]
            host.exchange(vendor_cdb(0xfb04, fb04_args(p, chunk)), data=chunk)
        got = b''.join(fd05_read(host, p) for p in range(sector, sector + 0x1000, 512))
        self.assertEqual(got, TARGET[sector:sector + 0x1000])   # 编程后读回==写入值（h3s_usb.py:173）
        programs = [e for e in dev.journal if e['op'] == 'program']
        self.assertEqual([e['addr'] for e in programs], list(range(sector, sector + 0x1000, 512)))
        for e in programs:
            self.assertEqual(e['sha256'], hashlib.sha256(TARGET[e['addr']:e['addr'] + 512]).hexdigest())

    def test_fb01_gate_and_shape_errors(self):
        """N4+N5：非对齐/越界/计划外/截短 CDB（缺地址）/错误填充 → 全拒+记录+副本不变。"""
        dev, host = ready_device()
        arm_with(dev)
        before = bytes(dev.flash_work)
        attempts = [
            (0x1001).to_bytes(4, 'big'),                        # 非 4KiB 对齐
            (sbs.USB_FLASH_SIZE).to_bytes(4, 'big'),            # 越界（2MiB 端点外）
            (0x1000).to_bytes(4, 'big'),                        # 对齐但计划外
        ]
        for i, args in enumerate(attempts):
            ins = dev.feed_out(cbw(i + 1, 16, False, vendor_cdb(0xfb01, args)))
            self.assertEqual(ins, [csw(i + 1, 1)], args.hex())
        # 截短 CDB（cdb_len=8：缺地址尾段）与错误填充（尾段非 ff）
        ins = dev.feed_out(cbw(90, 16, False, b'\xfb\x01\x00\xee\x00\x00\xff\xff'
                                    .ljust(8, b'\x00')[:8]))
        self.assertEqual(ins, [csw(90, 1)])
        bad_fill = bytearray(vendor_cdb(0xfb01, (M3_SECTORS[0]).to_bytes(4, 'big')))
        bad_fill[8] = 0x00                                      # 尾段应为 ff
        ins = dev.feed_out(cbw(91, 16, False, bytes(bad_fill)))
        self.assertEqual(ins, [csw(91, 1)])
        self.assertEqual(dev.counts.get('rejected'), 5)
        self.assertEqual(bytes(dev.flash_work), before)         # 副本不变
        # 16B IN 形状门：CBW size=0（无数据相位要求）
        ins = dev.feed_out(cbw(92, 0, False, vendor_cdb(0xfb01, (M3_SECTORS[0]).to_bytes(4, 'big'))))
        self.assertEqual(ins, [csw(92, 1)])

    def test_fb04_gate_and_shape_errors(self):
        """N4+N5：CRC 错/len≠512/非 512 对齐/未擦先写/重复写已编程块/计划外/截短/错误填充。"""
        dev, host = ready_device()
        arm_with(dev)
        sector = M3_SECTORS[0]
        other = M3_SECTORS[1]                                   # 计划内但未擦
        planned_outside = 0x1000                                # 对齐、不在计划
        chunk = TARGET[sector:sector + 512]
        host.exchange(vendor_cdb(0xfb01, sector.to_bytes(4, 'big')), size=16)   # 先擦：CRC/len 门可达
        cases = []                                              # (cdb, data, desc)
        cases.append((vendor_cdb(0xfb04, fb04_args(other, chunk)), chunk, '未擦先写'))
        cases.append((vendor_cdb(0xfb04, fb04_args(planned_outside, chunk)), chunk, '计划外扇区'))
        cases.append((vendor_cdb(0xfb04, fb04_args(sector + 1, chunk)), chunk, '非 512 对齐'))
        cases.append((vendor_cdb(0xfb04, fb04_args(sector, chunk, crc=0xdead)), chunk, 'CRC 不符'))
        short = TARGET[sector:sector + 256]
        cases.append((((0xfb04).to_bytes(2, 'big') + (sector).to_bytes(4, 'big')
                       + (256).to_bytes(2, 'big') + b'\x00' + (0).to_bytes(2, 'little')).ljust(16, b'\xff'),
                      short, 'len≠512'))
        # 截短 CDB：cdb_len=9（opcode+addr4+len2+00——缺 CRC）
        cases.append((((0xfb04).to_bytes(2, 'big') + (sector).to_bytes(4, 'big')
                       + (512).to_bytes(2, 'big') + b'\x00')[:9], chunk, '缺 CRC'))
        bad_fill = bytearray(vendor_cdb(0xfb04, fb04_args(sector, chunk)))
        bad_fill[12] = 0x00                                     # 尾段应为 ff
        cases.append((bytes(bad_fill), chunk, '错误填充'))
        for i, (cdb, data, desc) in enumerate(cases):
            before_work = bytes(dev.flash_work)
            ins = dev.feed_out(cbw(i + 1, len(data), True, cdb)) + dev.feed_out(data)
            self.assertEqual(ins, [csw(i + 1, 1)], desc)
            self.assertEqual(bytes(dev.flash_work), before_work, desc)   # 副本不变
        reasons = [r['reason'] for r in dev.rejected]
        for keyword in ('未擦除', '计划外', '512 对齐', 'CRC16', '512B OUT', '16B（got 9', '0xff 补齐'):
            self.assertTrue(any(keyword in r for r in reasons), keyword)
        # 重复写已编程块（修正 6）：完整擦写一块后，未重擦再写同块 → 拒；重擦后可再写
        flash_sector(host, sector)
        ins = dev.feed_out(cbw(50, 512, True, vendor_cdb(0xfb04, fb04_args(sector, chunk)))) \
            + dev.feed_out(chunk)
        self.assertEqual(ins, [csw(50, 1)])
        self.assertTrue(any('已编程' in r['reason'] for r in dev.rejected))
        host.exchange(vendor_cdb(0xfb01, sector.to_bytes(4, 'big')), size=16)   # 重擦
        host.exchange(vendor_cdb(0xfb04, fb04_args(sector, chunk)), data=chunk)  # 重写 OK
        self.assertEqual(fd05_read(host, sector), chunk)

    def test_full_plan_flash_preserved_and_sha_chain(self):
        """P4+P5+P6：六扇区全擦写 → 计划外保留==原始、全片复读 SHA==S_target（两面互证）。"""
        dev, host = ready_device()
        arm_with(dev)
        for sector in M3_SECTORS:
            flash_sector(host, sector)
        d = dev.describe()
        self.assertTrue(d['armed'])
        self.assertEqual(d['work_sha256'], S_TARGET)
        self.assertEqual(d['sim_sha256'], S_TARGET)
        # journal 按序：每扇区 erase×1 + program×8（P4 断言）
        ops = [(e['op'], e.get('sector', e.get('addr'))) for e in dev.journal]
        expected = []
        for sector in M3_SECTORS:                               # 执行序（h3s_usb.py:162 逐扇区序）
            expected.append(('erase', sector))
            expected += [('program', p) for p in range(sector, sector + 0x1000, 512)]
        self.assertEqual(ops, expected)
        # P5：计划外保留扇区抽查（含计划区间之间的保留区）
        for address in (0, 0x10000, 0x150000 if 0x150000 not in M3_SECTORS else 0x151000, 0x1ff800):
            self.assertEqual(fd05_read(host, address), FLASH[address:address + 512], hex(address))
        # P6：全片 4096×FD05 复读 SHA==S_target（预期由测试独立持有，不用 server 回报值）
        self.assertEqual(hashlib.sha256(fd05_full(host)).hexdigest(), S_TARGET)

    def test_planner_plan_feeds_arm_end_to_end(self):
        """planner（sim_restore_planner）产出直接喂 arm：闪完全片==TARGET 的闭环（§3.4）。"""
        plan = planner.plan_restore(FLASH, TARGET)
        self.assertEqual(plan['sectors'], sorted(M3_SECTORS))
        self.assertEqual(plan['base_sha256'], S0)
        self.assertEqual(plan['target_sha256'], S_TARGET)
        dev, host = ready_device()
        dev.arm({**plan, 'nonce': 'planner-nonce'})
        for sector in plan['sectors']:
            flash_sector(host, sector)
        self.assertEqual(fd05_full(host), TARGET)

    def test_reboot_revokes_armed_keeps_content(self):
        """修正 1+P7：reboot=armed 撤销+身份 normal+loader 丢弃；刷后镜像保留（FD05 复读
        ==S_target）；旧 rom key 过期；再 arm 副本基线=刷后内容。"""
        dev, host = ready_device()
        arm_with(dev)
        for sector in M3_SECTORS:
            flash_sector(host, sector)
        rom_key = dev.scan()[0]['key']
        self.assertIsNone(dev.reboot())
        self.assertEqual(dev.identity, 'normal')
        self.assertEqual(dev.phase, 'n/a')
        self.assertEqual(dev.loaded, 0)
        self.assertFalse(dev.describe()['armed'])               # 写授权撤销
        self.assertTrue(dev.describe()['read_only'])
        tail = dev.journal[-1]
        self.assertEqual(tail['op'], 'reboot')
        self.assertEqual(tail['work_sha256'], S_TARGET)         # 断言输入（server 只记不评）
        self.assertIs(tail['target_match'], True)
        self.assertFalse(dev.open_key(rom_key)[0])              # 旧 rom key 过期
        self.assertTrue(dev.open_key('sim:usb:3654:4a55')[0])
        # ⑪ 验证环：再进 ROM 装载 loader → FD05 全片仍==S_target（副本内容保持）
        dev.control(0)
        load_loader(BotHost(dev))
        self.assertEqual(dev.phase, 'loader-ready')
        self.assertEqual(hashlib.sha256(fd05_full(BotHost(dev))).hexdigest(), S_TARGET)
        # armed 已撤销：FB01 再拒（armed 门）；再 arm 从刷后内容起副本（修正 1）
        ins = dev.feed_out(cbw(99, 16, False, vendor_cdb(0xfb01, (M3_SECTORS[0]).to_bytes(4, 'big'))))
        self.assertEqual(ins, [csw(99, 1)])
        granted = arm_with(dev, nonce='post-reboot')
        self.assertEqual(granted['base_sha256'], S_TARGET)      # 基线=当前芯片（刷后内容）
        self.assertEqual(dev.describe()['work_sha256'], S_TARGET)

    def test_reboot_clears_bot_pipeline(self):
        """修正 5（内存复现反例）：FB04 CBW+部分 OUT 数据在途 → reboot → 新 CBW 正常应答，
        旧半包字节不被消化。"""
        dev, _ = ready_device()
        arm_with(dev)
        sector = M3_SECTORS[0]
        chunk = TARGET[sector:sector + 512]
        dev.feed_out(cbw(1, 512, True, vendor_cdb(0xfb04, fb04_args(sector, chunk))))   # CBW 已收
        dev.feed_out(chunk[:16])                                                          # OUT 只到 16B
        self.assertIsNotNone(dev._waiting)
        self.assertEqual(dev._waiting['got'], 16)
        self.assertIsNone(dev.reboot())
        self.assertIsNone(dev._waiting)                   # 在途清空
        self.assertEqual(dev._rx, b'')
        # reboot 后 normal：先回 rom 装载，新 CBW 立即得到完整应答（M2 bug 场景=零响应）
        dev.control(0)
        host = BotHost(dev)
        load_loader(host)
        r = host.exchange(vendor_cdb(0xfd05, (0).to_bytes(4, 'big') + (512).to_bytes(2, 'big')), size=512)
        self.assertEqual(r, FLASH[0:512])                 # 未再 arm：读原始（reboot 后三态回只读）

    def test_link_reset_clears_pipeline_and_keeps_armed(self):
        """修正 5：断链清 BOT 在途（CBW 半包/OUT 半包/CSW 未收三种残留）且 armed 保持。"""
        dev, _ = ready_device()
        arm_with(dev)
        sector = M3_SECTORS[0]
        # ① CBW 半包：31B CBW 只到 20B
        frame = cbw(1, 16, False, vendor_cdb(0xfb01, sector.to_bytes(4, 'big')))
        dev.feed_out(frame[:20])
        self.assertEqual(len(dev._rx), 20)
        dev.link_reset()
        self.assertEqual(dev._rx, b'')
        self.assertIsNone(dev._waiting)
        # ② OUT 半包：FB04 CBW+半数据
        chunk = TARGET[sector:sector + 512]
        dev.feed_out(cbw(2, 512, True, vendor_cdb(0xfb04, fb04_args(sector, chunk))))
        dev.feed_out(chunk[:100])
        self.assertEqual(dev._waiting['got'], 100)
        dev.link_reset()
        self.assertIsNone(dev._waiting)
        # ③ 写完成但 CSW 未被宿主读走（设备侧无在途）：完整 FB04 后 reset 不影响后续
        host = BotHost(dev)
        host.exchange(vendor_cdb(0xfb01, sector.to_bytes(4, 'big')), size=16)
        host.exchange(vendor_cdb(0xfb04, fb04_args(sector, chunk)), data=chunk)
        dev.link_reset()
        # armed 保持（断链不退出 armed）：新「会话」可继续擦写
        self.assertTrue(dev.describe()['armed'])
        next_sector = M3_SECTORS[1]
        host.exchange(vendor_cdb(0xfb01, next_sector.to_bytes(4, 'big')), size=16)
        self.assertIn(next_sector, dev.erased)

    def test_disarm_rolls_back_to_baseline_and_rearm(self):
        """修正 3：中断恢复路径=半态可见 → 显式 disarm 回滚到 arm 前基线 → 新 nonce 重刷。"""
        dev, host = ready_device()
        arm_with(dev, nonce='N1')
        for sector in sorted(M3_SECTORS)[:3]:                   # 擦写一半（3/6）
            flash_sector(host, sector)
        dev.link_reset()                                        # 断链（armed 保持）
        d = dev.describe()
        self.assertTrue(d['armed'])
        self.assertNotEqual(d['work_sha256'], S0)
        self.assertNotEqual(d['work_sha256'], S_TARGET)         # 半态可见（N2-②）
        host2 = BotHost(dev)                                    # 「重连」后的新会话
        self.assertEqual(fd05_read(host2, M3_SECTORS[1]), TARGET[M3_SECTORS[1]:M3_SECTORS[1] + 512])
        dev.disarm()                                            # 显式回滚
        self.assertFalse(dev.describe()['armed'])
        self.assertEqual(fd05_read(host2, M3_SECTORS[1]), FLASH[M3_SECTORS[1]:M3_SECTORS[1] + 512])
        with self.assertRaisesRegex(ValueError, '未 armed'):
            dev.disarm()
        granted = arm_with(dev, nonce='N2')                     # 重新 arm：副本重置为基线
        self.assertEqual(granted['base_sha256'], S0)
        self.assertEqual(dev.describe()['work_sha256'], S0)
        for sector in M3_SECTORS:                               # 完整重刷（可恢复，N2-⑤）
            flash_sector(host2, sector)
        self.assertEqual(hashlib.sha256(fd05_full(host2)).hexdigest(), S_TARGET)

    def test_interrupted_half_state_full_chip_compare(self):
        """修正 7（N2 基线）：计划扇区预置非 ff 数据，擦 3/6 后全片 FD05==独立构造的半态
        预期镜像（base 拷贝+3 扇区 ff）逐字节比较——不以内部状态自证。"""
        for sector in M3_SECTORS:                               # 基线含非 ff 数据（合成模式保证）
            self.assertNotEqual(FLASH[sector:sector + 0x1000], b'\xff' * 0x1000, hex(sector))
        dev, host = ready_device()
        arm_with(dev)
        done = sorted(M3_SECTORS)[:3]
        for sector in done:
            host.exchange(vendor_cdb(0xfb01, sector.to_bytes(4, 'big')), size=16)   # 只擦不写
        expected = bytearray(FLASH)                             # 独立构造半态预期
        for sector in done:
            expected[sector:sector + 0x1000] = b'\xff' * 0x1000
        self.assertEqual(fd05_full(host), bytes(expected))
        self.assertEqual(hashlib.sha256(bytes(expected)).hexdigest(), dev.describe()['work_sha256'])

    def test_original_flash_bytes_never_mutated(self):
        """红线实证（D2）：arm/擦/写/disarm/再 arm/再刷/reboot 全程，原始资产 self.flash
        逐字节不变且恒为 bytes（无写方法的结构保证）；flash_sha256 字段同步不变。"""
        dev, host = ready_device()
        sha_before = dev.flash_sha256
        arm_with(dev, nonce='r1')
        for sector in M3_SECTORS:
            flash_sector(host, sector)
        dev.link_reset()
        dev.disarm()
        arm_with(dev, nonce='r2')
        for sector in M3_SECTORS:
            flash_sector(host, sector)
        self.assertIsNone(dev.reboot())
        self.assertIsInstance(dev.flash, bytes)                 # bytes：无写方法（结构红线）
        self.assertEqual(dev.flash, FLASH)                      # 原始资产逐字节不变
        self.assertEqual(hashlib.sha256(dev.flash).hexdigest(), sha_before)
        self.assertEqual(dev.flash_sha256, S0)

    def test_short_cdb_rejected_without_crash(self):
        """修正 4（M2 遗留）：1B CDB 不再 cdb[1] 越界崩溃——status=1+记录（opcode=None）。"""
        dev, _ = ready_device()
        ins = dev.feed_out(cbw(1, 16, False, b'\xfd'))
        self.assertEqual(ins, [csw(1, 1)])
        self.assertEqual(dev.rejected[-1]['opcode'], None)
        self.assertIn('过短', dev.rejected[-1]['reason'])
        # 后续命令仍正常（会话不崩）
        host = BotHost(dev)
        r = host.exchange(vendor_cdb(0xfd05, (0).to_bytes(4, 'big') + (512).to_bytes(2, 'big')), size=512)
        self.assertEqual(r, FLASH[0:512])


# ================================ M3 会话层（SIMB3S 第二段） ================================

TOOLS_DIR = Path(__file__).resolve().parent


class SocketBot:
    """socket 级 BOT 交换器（usb_bulk_out/usb_bulk_in 消息面；app 的 SimulationUsbLink +
    UsbScsi.exchange 同形）。与 BotHost 同接口——load_loader/flash_sector/fd05_read 直接复用。"""

    def __init__(self, client):
        self.client = client
        self.tag = 0

    def send_raw(self, raw: bytes) -> None:
        self.client.send({'t': 'usb_bulk_out', 'data': base64.b64encode(raw).decode('ascii')})

    def exchange(self, cdb, size=0, data=None):
        self.tag += 1
        self.send_raw(cbw(self.tag, len(data) if data is not None else size, data is not None, cdb))
        if data is not None:
            self.send_raw(data)
        result = b''
        if data is None and size > 0:
            result = base64.b64decode(self.client.drain_until('usb_bulk_in')['data'])
        got = base64.b64decode(self.client.drain_until('usb_bulk_in')['data'])
        assert got == csw(self.tag), 'CSW 不符: %s' % got.hex()
        return result


def connect_with(port, message):
    """连接并发送首条消息，返回 (client, 首条应答)。旧会话线程尚未释放活动锁时会回 busy——
    短退避重试（客户端主动断链后立刻重连的竞态兜底，非 server 行为断言面）。"""
    for _ in range(200):
        client = Client(port)
        client.send(message)
        reply = client.recv()
        if reply is not None and reply.get('t') == 'busy':
            client.close()
            time.sleep(0.01)
            continue
        if reply is None:
            client.close()
            raise AssertionError('首条消息 %r 无应答即断开' % message.get('t'))
        return client, reply
    raise AssertionError('重连 2s 仍 busy（活动锁未释放）')


def hello_client(port):
    client, reply = connect_with(port, {'t': 'hello', 'proto': sbs.PROTO, 'client': 'sim-usb'})
    assert reply.get('t') == 'hello_ok', reply
    assert reply['world']['mode'] == 'sim-usb-msc', reply
    return client


def arm_msg(sectors=M3_SECTORS, target_sha256=S_TARGET, nonce='sess-nonce'):
    return {'t': 'usb_arm', 'sectors': list(sectors), 'target_sha256': target_sha256, 'nonce': nonce}


class M3FlashSessionTest(unittest.TestCase):
    """M3 会话层（socket 级）：usb_arm/usb_disarm/usb_reboot 消息往返 + 断链清理接线
    （codex P1：close()→link_reset）+ 断链 armed 保持 + busy 纪律 + CLI --arm-plan 冒烟。"""

    def setUp(self):
        self._start_server()

    def _start_server(self):
        """新设备 + 新 server 线程（断链清理子测各自起——身份/相位是进程级状态，跨子测
        残留会让 ctl len0/FB06 的门拒绝，场景本就是独立会话生命周期）。"""
        self.usb = sbs.MscBotDevice(flash=FLASH, rom=ROM)
        # 并发隔离（FLAKYFIX）：port-0 listener 同步 bind（见 UsbSessionTest.setUp 注）。
        self.listener = sbs.make_listener('127.0.0.1', 0)
        self.port = self.listener.getsockname()[1]
        threading.Thread(target=sbs.serve_listener,
                         args=(self.listener, sbs.DEFAULT_FIXTURE, self.usb), daemon=True).start()

    def hello(self):
        return hello_client(self.port)

    def ready_client(self):
        """新会话走到 loader-ready：ctl len0 断连重枚举 → 重连 open → 装载 loader 序列。"""
        client = self.hello()
        client.send({'t': 'usb_ctl', 'len': 0})
        client.drain_until('usb_ctl_result')
        self.assertIsNone(client.recv(), 'ctl len0 后应断连（重枚举）')
        client = self.hello()
        client.send({'t': 'usb_open', 'key': self.usb.scan()[0]['key']})
        self.assertTrue(client.drain_until('usb_open_result')['ok'])
        load_loader(SocketBot(client))
        self.assertEqual(self.usb.phase, 'loader-ready')
        return client

    def arm_via(self, client, nonce):
        client.send(arm_msg(nonce=nonce))
        return client.drain_until('usb_arm_result')

    def test_arm_disarm_reboot_roundtrip(self):
        client = self.ready_client()
        try:
            granted = self.arm_via(client, 'sess-1')
            self.assertTrue(granted['ok'], granted)
            self.assertTrue(self.usb.describe()['armed'])
            # armed 期间重复 arm=busy 语义 → ok=false+detail，连接保持
            dup = self.arm_via(client, 'sess-2')
            self.assertFalse(dup['ok'])
            self.assertIn('busy', dup['detail'])
            client.send({'t': 'usb_scan'})                       # 连接仍可用（拒绝不断链）
            self.assertEqual(client.drain_until('usb_scan_result')['devices'][0]['vid'], sbs.USB_VID_ROM)
            # disarm 往返：ok=true；未 armed 再 disarm → ok=false 不断链
            client.send({'t': 'usb_disarm'})
            self.assertTrue(client.drain_until('usb_disarm_result')['ok'])
            client.send({'t': 'usb_disarm'})
            r = client.drain_until('usb_disarm_result')
            self.assertFalse(r['ok'])
            self.assertIn('未 armed', r['detail'])
            # 再 arm + 留一个 FB04 OUT 半相位在途 → reboot：ok=true 后断连（重枚举先例）
            self.assertTrue(self.arm_via(client, 'sess-3')['ok'])
            bot = SocketBot(client)
            sector = M3_SECTORS[0]
            chunk = TARGET[sector:sector + 512]
            bot.send_raw(cbw(1, 512, True, vendor_cdb(0xfb04, fb04_args(sector, chunk))))
            bot.send_raw(chunk[:16])                              # OUT 数据相位半途
            client.send({'t': 'usb_reboot'})
            r = client.drain_until('usb_reboot_result')
            self.assertTrue(r['ok'], r)
            self.assertIsNone(client.recv(), 'reboot 成功后应断连（重枚举）')
        finally:
            client.close()
        self.assertFalse(self.usb.describe()['armed'])           # reboot 撤销 armed
        self.assertIsNone(self.usb._waiting)                     # 在途半相位被清（设备级 reboot）
        # 重枚举后：normal 身份；journal 尾部 reboot 条目（armed 副本在途 → 记半态 SHA）
        client = self.hello()
        try:
            client.send({'t': 'usb_scan'})
            self.assertEqual(client.drain_until('usb_scan_result')['devices'][0]['vid'], sbs.USB_VID_QCY)
        finally:
            client.close()
        tail = self.usb.journal[-1]
        self.assertEqual(tail['op'], 'reboot')
        self.assertIn('work_sha256', tail)                       # armed 会话的 reboot 记半态（§3.3）
        self.assertIs(tail['target_match'], False)               # OUT 半途=未达 target（如实）

    def test_arm_validation_failures_keep_connection(self):
        """§3.6：usb_arm 校验失败 → ok=false+detail 不断链（消息级，区别于 no_hello 断链）。"""
        client = self.ready_client()
        try:
            bad_plans = [
                {'sectors': [], 'target_sha256': S_TARGET, 'nonce': 'n'},        # 空扇区
                {'sectors': [0x1001], 'target_sha256': S_TARGET, 'nonce': 'n'},  # 非 4KiB 对齐
                {'sectors': [0x1000], 'target_sha256': 'xyz', 'nonce': 'n'},     # 坏 target SHA
                {'sectors': [0x1000], 'target_sha256': S_TARGET},                # 缺 nonce
                {'nonce': 'n'},                                                  # 缺扇区
            ]
            for plan in bad_plans:
                client.send({'t': 'usb_arm', **plan})
                r = client.drain_until('usb_arm_result')
                self.assertFalse(r['ok'], plan)
                self.assertTrue(r['detail'], plan)
            self.assertFalse(self.usb.describe()['armed'])       # 全拒：未进入 armed
            # nonce 单次消费（消息面）：arm→disarm→旧 nonce 复用 → ok=false 已消费
            self.assertTrue(self.arm_via(client, 'once')['ok'])
            client.send({'t': 'usb_disarm'})
            self.assertTrue(client.drain_until('usb_disarm_result')['ok'])
            r = self.arm_via(client, 'once')
            self.assertFalse(r['ok'])
            self.assertIn('已消费', r['detail'])
            # 全程连接未断：scan 仍应答
            client.send({'t': 'usb_scan'})
            self.assertEqual(client.drain_until('usb_scan_result')['devices'][0]['vid'], sbs.USB_VID_ROM)
        finally:
            client.close()

    def test_m3_messages_require_hello(self):
        for kind, extra in (('usb_arm', {'sectors': [0x1000], 'target_sha256': S_TARGET, 'nonce': 'n'}),
                            ('usb_disarm', {}),
                            ('usb_reboot', {})):
            with self.subTest(kind):
                client, reply = connect_with(self.port, {'t': kind, **extra})
                try:
                    self.assertEqual(reply.get('t'), 'error')
                    self.assertEqual(reply.get('code'), 'no_hello')
                    self.assertIsNone(client.recv(), 'no_hello 后应断开')
                finally:
                    client.close()

    def test_disconnect_clears_bot_half_phases(self):
        """codex P1 修复验证（socket 级）：三种断链残留后新连接 INQUIRY 必须完整应答——
        旧行为=旧 _waiting 吞掉新 CBW 字节（codex 复现：响应数=0、旧相位 got=47）。"""
        sector = M3_SECTORS[0]
        chunk = TARGET[sector:sector + 512]
        cases = [  # (描述, 断链前的字节注入, 子测 nonce)
            ('OUT 数据相位半途 got=16（codex 复现场景）',
             lambda bot: (bot.send_raw(cbw(1, 512, True, vendor_cdb(0xfb04, fb04_args(sector, chunk)))),
                          bot.send_raw(chunk[:16])), 'p1-out-half'),
            ('CBW 半包（31B 只到 20B）',
             lambda bot: (bot.send_raw(cbw(2, 16, False, vendor_cdb(0xfb01, sector.to_bytes(4, 'big')))[:20]),),
             'p1-cbw-half'),
            ('写完成但 CSW 未被宿主读走',
             lambda bot: (bot.exchange(vendor_cdb(0xfb01, sector.to_bytes(4, 'big')), size=16),
                          bot.send_raw(cbw(3, 512, True, vendor_cdb(0xfb04, fb04_args(sector, chunk)))),
                          bot.send_raw(chunk)),                    # 设备侧已完成写并发出 CSW，
             'p1-csw-unread'),                                     # 宿主未读即断链
        ]
        for desc, feed, nonce in cases:
            with self.subTest(desc):
                self._start_server()                             # 子测独立设备+server（进程级状态隔离）
                client = self.ready_client()
                self.assertTrue(self.arm_via(client, nonce)['ok'])
                feed(SocketBot(client))
                client.close()                                   # 断链（不 disarm/reboot）
                # 新连接：31B INQUIRY 必须得到完整应答（36B 数据+13B CSW——旧行为零响应）
                client = self.hello()
                try:
                    # hello 成功=活动锁已释放=close()→link_reset 已执行（run() finally 顺序）
                    self.assertTrue(self.usb.describe()['armed'], desc)     # armed 保持（§3.1）
                    self.assertIsNone(self.usb._waiting, desc)              # BOT 在途已清
                    self.assertEqual(self.usb._rx, b'', desc)
                    client.send({'t': 'usb_open', 'key': self.usb.scan()[0]['key']})
                    self.assertTrue(client.drain_until('usb_open_result')['ok'])
                    r = SocketBot(client).exchange(bytes([0x12, 0, 0, 0, 36, 0]), size=36)
                    self.assertEqual(len(r), 36)
                    self.assertEqual(r[8:16], b'BR28    ')
                finally:
                    client.close()

    def test_disconnect_keeps_armed_and_fd05_reads_work_copy(self):
        """断链 armed 保持（N2 语义）：擦一扇区→断链→重连 FD05 仍读 ff（副本=半态可见）、
        计划外扇区读==原始；disarm 消息收兵后 FD05 回原始基线。"""
        sector = M3_SECTORS[0]
        client = self.ready_client()
        self.assertTrue(self.arm_via(client, 'keep')['ok'])
        bot = SocketBot(client)
        r = bot.exchange(vendor_cdb(0xfb01, sector.to_bytes(4, 'big')), size=16)
        self.assertEqual(r[:2], b'\xfb\x01')
        self.assertEqual(bot.exchange(vendor_cdb(0xfd05, sector.to_bytes(4, 'big')
                                                + (512).to_bytes(2, 'big')), size=512), b'\xff' * 512)
        client.close()                                           # 断链（不 disarm）
        d = self.usb.describe()
        self.assertTrue(d['armed'])                              # 断链不退出 armed
        self.assertEqual(d['journal_len'], 1)
        client = self.hello()
        try:
            client.send({'t': 'usb_open', 'key': self.usb.scan()[0]['key']})
            self.assertTrue(client.drain_until('usb_open_result')['ok'])
            bot = SocketBot(client)
            got = bot.exchange(vendor_cdb(0xfd05, sector.to_bytes(4, 'big') + (512).to_bytes(2, 'big')), size=512)
            self.assertEqual(got, b'\xff' * 512)                 # 已擦扇区仍 ff（副本保持）
            got = bot.exchange(vendor_cdb(0xfd05, (0x8000).to_bytes(4, 'big') + (512).to_bytes(2, 'big')),
                               size=512)
            self.assertEqual(got, FLASH[0x8000:0x8000 + 512])    # 计划外==原始（状态如实）
            client.send({'t': 'usb_disarm'})                     # 重连会话上 disarm 收兵
            self.assertTrue(client.drain_until('usb_disarm_result')['ok'])
            got = bot.exchange(vendor_cdb(0xfd05, sector.to_bytes(4, 'big') + (512).to_bytes(2, 'big')),
                               size=512)
            self.assertEqual(got, FLASH[sector:sector + 512])    # 回滚到 arm 前基线
        finally:
            client.close()

    def test_busy_single_active_not_broken_by_arm(self):
        """usb_arm 是普通请求消息：armed 状态下并发客户端仍 busy 断开、原会话不破。"""
        client = self.ready_client()
        try:
            self.assertTrue(self.arm_via(client, 'busy-1')['ok'])
            second = Client(self.port)
            try:
                self.assertEqual(second.recv().get('t'), 'busy')
            finally:
                second.close()
            client.send({'t': 'usb_scan'})                       # 原会话仍可用
            self.assertEqual(client.drain_until('usb_scan_result')['devices'][0]['vid'], sbs.USB_VID_ROM)
        finally:
            client.close()
        third = self.hello()                                     # 会话结束后活动锁已释放
        third.close()

    def test_cli_arm_plan_and_persist_placeholder(self):
        """CLI --arm-plan 构造即 armed（子进程 hello 横幅可见）+ --persist 占位（§3.5-B
        未实施——帮助文本与启动日志如实标注，不发明落盘行为）。"""
        with tempfile.TemporaryDirectory() as tmp:
            flash_path = Path(tmp) / 'flash.bin'
            flash_path.write_bytes(FLASH)
            plan_path = Path(tmp) / 'plan.json'
            plan_path.write_text(json.dumps({'sectors': list(M3_SECTORS), 'target_sha256': S_TARGET,
                                             'nonce': 'cli-smoke'}))
            help_out = subprocess.run([sys.executable, str(TOOLS_DIR / 'sim_bridge_server.py'), '--help'],
                                      capture_output=True, text=True, timeout=60)
            self.assertEqual(help_out.returncode, 0, help_out.stderr)
            self.assertIn('--arm-plan', help_out.stdout)
            self.assertIn('--persist', help_out.stdout)
            # 并发隔离（FLAKYFIX）：--listen :0 由内核随机分配（子进程启动期上百 ms 的
            # 探测端口窗口不复存在），实际端口从 stderr「监听 127.0.0.1:<port>」行取；
            # 读者线程持续排水 stderr（防 PIPE 缓冲死锁），行累积供尾部断言。
            stderr_lines = []
            port_seen = threading.Event()

            def drain_stderr():
                for raw in proc.stderr:
                    stderr_lines.append(raw.decode('utf-8'))
                    m = re.search(r'监听 127\.0\.0\.1:(\d+)', stderr_lines[-1])
                    if m:
                        actual_port.append(int(m.group(1)))
                        port_seen.set()

            actual_port = []
            proc = subprocess.Popen(
                [sys.executable, str(TOOLS_DIR / 'sim_bridge_server.py'),
                 '--listen', '127.0.0.1:0', '--flash-image', str(flash_path),
                 '--arm-plan', str(plan_path), '--persist'],
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            try:
                reader = threading.Thread(target=drain_stderr, daemon=True)
                reader.start()
                if not port_seen.wait(10.0):
                    raise AssertionError('CLI server 10s 内未报告监听端口: %s' % ''.join(stderr_lines))
                port = actual_port[0]
                client, reply = connect_with(
                    port, {'t': 'hello', 'proto': sbs.PROTO, 'client': 'sim-usb'})
                self.assertEqual(reply.get('t'), 'hello_ok', reply)
                self.assertTrue(reply['world']['armed'], reply)          # 构造即 armed
                self.assertEqual(reply['world']['plan_sectors'], sorted(M3_SECTORS))
                self.assertEqual(reply['world']['work_sha256'], S0)      # 副本=原始拷贝
                client.close()
            finally:
                proc.terminate()
                proc.wait(10)
            reader.join(2.0)
            stderr = ''.join(stderr_lines)
            self.assertIn('启动即 armed', stderr)
            self.assertIn('占位', stderr)                          # --persist 如实标注未实施


if __name__ == '__main__':
    unittest.main(verbosity=2)
