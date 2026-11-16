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
"""
import base64
import binascii
import hashlib
import struct
import sys
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sim_bridge_server as sbs
from test_sim_bridge_server import Client, free_port

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
        self.port = free_port()
        ready = threading.Event()                     # 防 bind/listen 竞态：就绪后再放行连接
        threading.Thread(target=sbs.serve, args=('127.0.0.1', self.port, sbs.DEFAULT_FIXTURE, self.usb),
                         kwargs={'ready': ready}, daemon=True).start()
        if not ready.wait(5.0):
            raise AssertionError('server 未在 5s 内就绪')

    def hello(self):
        client = Client(self.port)
        client.send({'t': 'hello', 'proto': sbs.PROTO, 'client': 'sim-usb'})
        ok = client.recv()
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


if __name__ == '__main__':
    unittest.main(verbosity=2)
