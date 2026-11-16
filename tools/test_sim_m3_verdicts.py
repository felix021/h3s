#!/usr/bin/env python3
"""SIMB3V M3 机检验收执行器（docs/sim-m3-flash-spec.md §5）：P1-P7/N1-N5 socket 级全环。

对**真 server 子进程**（tools/sim_bridge_server.py --flash-image …，非 mock、非 in-process
直调）跑规格 §5 全部判据，让 M3 验收从「一次 E2E 实录」变成可重跑机检：

  P1  离线构造：fixture（真实 UFW 产档优先，缺资产回退合成全非 ff 模式）+ 独立目标镜像
      + planner 计划（sim_restore_planner.plan_restore 纯函数）——期望值全部本层 hashlib
      复算，server 只执行不自评（§3.4 codex 纪律）。
  P2  M2 回归前置：未 armed 全序列 enterRom→loader→FD05 全片双读==base（M2 零漂移）。
  P3  usb_arm：ok / describe armed+work_sha256==S0 / 重复 arm（同异 nonce）=busy 拒。
  P4  逐扇区 SHA 链：核验==基线→FB01（16B 回显 fb01）→读全 ff→FB04×8→读==target 切片；
      journal_len 逐扇区 +9、work_sha256==独立构造的渐进镜像 SHA。
  P5  计划外保留区抽查==base 切片（擦写零外溢）。
  P6  全片复读==target 逐字节 + describe.work_sha256/sim_sha256==S_target（两面互证）。
  P7  reboot+验证环：断连重枚举/rescan=normal/旧 rom key 过期/再进 ROM 复读==S_target/
      journal_len+counts 对拍 E2E 实录（fb01=3/fb04=24/journal_len=28/rejected=0）/
      磁盘 fixture SHA==S0（copy-on-armed 铁证，全程多时点复验）。
  N1  未 armed FB01/FB04 裸发：CSW status=1+armed 门；副本未建；FD05 读芯片内容。
  N2  半擦断链：断 socket（不 disarm）→ armed 保持+半态可见（独立构造半态镜像对拍）→
      FD05 状态如实 → disarm 显式回滚 → 新 nonce 重 arm 副本重置 → 完整重刷全环过。
  N3  nonce 作废三态：armed 期间 busy / disarm 后已消费 / reboot 后已消费；kill -9 重启
      回滚（armed=false、FD05==S0——§3.5-A 进程级状态）。
  N4  计划外写：FB01 对齐计划外/非对齐/越界 + FB04 计划外 → server 计划门全拒+副本不变。
  N5  形状负例：FB04 CRC 错/len≠512/未擦先写；FB01 非 16B IN/截短 CDB；两 opcode 错误
      填充 → 全拒+副本不变（独立构造预期 SHA 对拍）。

四态纪律照 test_sim_m1_verdicts（PASS/FAIL/BLOCKED/PARTIAL）：判据面里依赖 app/AVD 的
半边（UI 恢复卡、JVM 构造参数负例、journal 逐条 reason 文本——不经 socket 暴露）归
E2E 实录（evidence/sim-routes/m3-avd-e2e.json）与设备级单测（test_sim_usb_device），
本执行器如实 note 不冒充、不降级硬凑。
退出码：0=PASS；3=FAIL；4=BLOCKED（server 起不来/形状与假设不符）；5=PARTIAL。
验收记录落 JSON（--record，默认 analysis/local-checks/sim-m3-verdicts.json）。
"""
from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import socket
import struct
import subprocess
import sys
import time
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
ROOT = TOOLS.parent
sys.path.insert(0, str(TOOLS))
import sim_restore_planner as planner                       # noqa: E402（计划构造器，§3.4）

USB_FLASH_SIZE = 0x200000                                   # 2MiB 全片（image.SIZE 同源）
USB_SECTOR = 512                                            # FD05/FB04 块
USB_ERASE_SECTOR = 0x1000                                   # FB01 4KiB 擦除块
USB_LOADER_ADDRESS = 0x120000
USB_LOADER_SIZE = 23552
ROM_KEY = 'sim:usb:4c4a:3442'
NORMAL_KEY = 'sim:usb:3654:4a55'

# E2E 实录的服务端账目（evidence/sim-routes/m3-avd-e2e.json server_accounting）——
# 执行器用独立构造的 plan/目标镜像跑同形状（3 扇区）主环，对拍这四个数。
E2E_ACCOUNTING = {'fb01': 3, 'fb04': 24, 'journal_len': 28, 'rejected': 0}

# 主环计划：与 E2E 同扇区集（对拍账目形状；0x1f8000 落在 fixture 的 0xff 合成区，
# 擦除验证在该扇区为弱断言——与 E2E 同形，如实记录；program+读回仍非平凡）。
MAIN_SECTORS = (0x0, 0x1000, 0x1f8000)
# N2 六扇区：全取 UFW 数据区 [0,0xe6000) 内（真 fixture 下基线非 ff——半态断言非平凡；
# 合成回退模式全片非 ff 同样成立；运行时逐扇区断言）。
N2_SECTORS = (0x3000, 0x21000, 0x52000, 0x87000, 0xb1000, 0xe5000)
# N4/N5 两扇区计划（0x1000 已擦做 CRC 门、0x2000 未擦做「未擦先写」门）。
N45_SECTORS = (0x1000, 0x2000)

LOADER = bytes((i * 11 + 1) & 0xff for i in range(USB_LOADER_SIZE))   # server 不校验内容


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sector_payload(sector: int, salt: int) -> bytes:
    """独立构造的目标扇区内容（确定性 LCG，非全 ff；与基线切片不同的断言在 P1 做）。"""
    out = bytearray(USB_ERASE_SECTOR)
    x = (sector * 2654435761 + salt * 40503 + 0xC0FFEE) & 0xFFFFFFFF
    for i in range(USB_ERASE_SECTOR):
        x = (x * 1664525 + 1013904223) & 0xFFFFFFFF
        out[i] = (x >> 17) & 0xFF
    return bytes(out)


def make_target(base: bytes, sectors, salt: int) -> bytes:
    target = bytearray(base)
    for sector in sectors:
        target[sector:sector + USB_ERASE_SECTOR] = sector_payload(sector, salt)
    return bytes(target)


def with_sectors(base: bytes, sectors, payload=b'\xff' * USB_ERASE_SECTOR) -> bytes:
    """独立构造镜像：base 拷贝 + 指定扇区替换（半态/渐进预期，不以 server 内部状态自证）。"""
    out = bytearray(base)
    for sector in sectors:
        out[sector:sector + USB_ERASE_SECTOR] = payload
    return bytes(out)


# ---------------------------------------------------------------------------
# 断言收集器（照 test_sim_m1_verdicts.Verdict 的四态纪律）
# ---------------------------------------------------------------------------

class Verdict:

    def __init__(self, record):
        self.record = record
        self.failures = 0
        self.incomplete = 0
        self.notes = 0
        self.blocked = None

    def check(self, check_id: str, ok: bool, detail=None) -> bool:
        row = {'id': check_id, 'ok': bool(ok), 'detail': detail}
        self.record.setdefault('checks', []).append(row)
        print('[%s] %s %s' % ('PASS' if ok else 'FAIL', check_id,
                              '' if detail is None else json.dumps(detail, default=str)))
        if not ok:
            self.failures += 1
        return ok

    def note_required(self, check_id: str, detail=None):
        row = {'id': check_id, 'ok': None, 'state': 'incomplete', 'detail': detail}
        self.record.setdefault('checks', []).append(row)
        self.record.setdefault('incomplete', []).append(row)
        self.incomplete += 1
        print('[INCOMPLETE] %s %s' % (check_id, json.dumps(detail, default=str)))

    def note(self, check_id: str, detail=None):
        row = {'id': check_id, 'ok': None, 'state': 'optional', 'detail': detail}
        self.record.setdefault('checks', []).append(row)
        self.record.setdefault('notes', []).append(row)
        self.notes += 1
        print('[NOTE] %s %s' % (check_id, json.dumps(detail, default=str)))

    def fail_blocked(self, reason: str, detail=None):
        if self.blocked is None:
            self.blocked = {'reason': reason, 'detail': detail}
        else:
            self.blocked.setdefault('subsequent', []).append({'reason': reason, 'detail': detail})
        print('[BLOCKED] %s %s' % (reason, json.dumps(detail or {}, default=str)))


# ---------------------------------------------------------------------------
# socket 客户端 + BOT 交换器（SimulationUsbLink/UsbScsi.exchange 的宿主镜像）
# ---------------------------------------------------------------------------

def cbw(tag, size, data_out, cdb):
    """31B CBW（UsbScsi.cbw / protocol.py exchange 同形）。"""
    assert 1 <= len(cdb) <= 16, 'host 编码错误：CDB 须 1..16B（got %dB）' % len(cdb)
    return (b'USBC' + struct.pack('<IIBBB', tag, size, 0 if data_out else 0x80, 0, len(cdb))
            + cdb.ljust(16, b'\x00'))


def vendor_cdb(opcode, args=b''):
    """16B vendor CDB（opcode 2B BE + args + 0xff 填充）。"""
    return (opcode.to_bytes(2, 'big') + args).ljust(16, b'\xff')


def csw(tag, status=0):
    return b'USBS' + struct.pack('<I', tag) + b'\x00\x00\x00\x00' + bytes([status])


class UsbClient:
    """sim-usb 会话客户端（JSON-lines；hello/describe/ctl/arm 消息面）。"""

    def __init__(self, port, host='127.0.0.1', timeout=30.0):
        self.sock = socket.create_connection((host, port), timeout=5.0)
        self.sock.settimeout(timeout)
        try:                                                # loopback 全片读提速：关 Nagle +
            self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)   # 即时 ACK（仅 ACK 时序，
        except OSError:                                     # 协议语义零影响；非 Linux 退化仅变慢）
            pass
        self.pending = b''

    def send(self, obj):
        self.sock.sendall((json.dumps(obj) + '\n').encode('utf-8'))

    def recv(self):
        while b'\n' not in self.pending:
            data = self.sock.recv(65536)
            if not data:
                return None
            self.pending += data
            try:
                self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_QUICKACK, 1)
            except OSError:
                pass
        line, _, self.pending = self.pending.partition(b'\n')
        return json.loads(line.decode('utf-8'))

    def drain_until(self, kind, allow=()):
        message = self.recv()
        if message is None:
            raise AssertionError('等待 %s 时连接关闭' % kind)
        if message.get('t') == kind:
            return message
        if message.get('t') not in allow:
            raise AssertionError('等待 %s 时收到意外消息: %r' % (kind, message))
        return message

    def hello(self):
        """hello 往返 → world 横幅（describe）。同一连接重复 hello 也应答（拿实时 describe）。"""
        self.send({'t': 'hello', 'proto': 1, 'client': 'sim-usb'})
        return self.drain_until('hello_ok', allow=('busy', 'hello_err'))['world']

    describe = hello                                        # 活会话上重发 hello 即 describe

    def ctl(self, length):
        self.send({'t': 'usb_ctl', 'len': length})
        return self.drain_until('usb_ctl_result', allow=('error',))

    def scan(self):
        self.send({'t': 'usb_scan'})
        return self.drain_until('usb_scan_result')['devices']

    def open_key(self, key):
        self.send({'t': 'usb_open', 'key': key})
        return self.drain_until('usb_open_result')

    def arm(self, sectors, target_sha256, nonce):
        self.send({'t': 'usb_arm', 'sectors': list(sectors), 'target_sha256': target_sha256,
                   'nonce': nonce})
        return self.drain_until('usb_arm_result')

    def disarm(self):
        self.send({'t': 'usb_disarm'})
        return self.drain_until('usb_disarm_result')

    def reboot_msg(self):
        self.send({'t': 'usb_reboot'})
        return self.drain_until('usb_reboot_result')

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


class BotLink:
    """socket 级 BOT 交换器（usb_bulk_out/usb_bulk_in；绿=exchange 断言 CSW status0，
    红=expect_reject 断言恰一条 CSW status1）。"""

    def __init__(self, client: UsbClient):
        self.client = client
        self.tag = 0

    def send_raw(self, raw: bytes):
        self.client.send({'t': 'usb_bulk_out', 'data': base64.b64encode(raw).decode('ascii')})

    def in_transfer(self) -> bytes:
        return base64.b64decode(self.client.drain_until('usb_bulk_in')['data'])

    def exchange(self, cdb, size=0, data=None):
        """绿路径：返回数据相位（无则 b''）；transfer 数/长度/CSW tag+status0 逐一断言。"""
        self.tag += 1
        self.send_raw(cbw(self.tag, len(data) if data is not None else size, data is not None, cdb))
        if data is not None:
            self.send_raw(data)
        expect = (1 if (data is None and size > 0) else 0) + 1
        ins = []
        while len(ins) < expect:
            transfer = self.in_transfer()
            if len(ins) < expect - 1:
                assert len(transfer) == size, '数据相位长度 %d≠%d' % (len(transfer), size)
            ins.append(transfer)
        assert ins[-1] == csw(self.tag), 'CSW 不符: %s' % ins[-1].hex()
        return ins[0] if (data is None and size > 0) else b''

    def expect_reject(self, cdb, size=0, data=None, desc=''):
        """红路径：恰一条 IN（13B CSW status=1）——拒绝不产生数据相位。"""
        self.tag += 1
        self.send_raw(cbw(self.tag, len(data) if data is not None else size, data is not None, cdb))
        if data is not None:
            self.send_raw(data)
        transfer = self.in_transfer()
        ok = transfer == csw(self.tag, 1)
        assert ok, '%s：预期 CSW status=1，收到 %s' % (desc, transfer.hex())
        return transfer


def fb04_args(address, chunk, crc=None):
    return (address.to_bytes(4, 'big') + len(chunk).to_bytes(2, 'big') + b'\x00'
            + (binascii.crc_hqx(chunk, 0) if crc is None else crc).to_bytes(2, 'little'))


def fd05_read(bot: BotLink, address, length=USB_SECTOR):
    return bot.exchange(vendor_cdb(0xfd05, address.to_bytes(4, 'big') + length.to_bytes(2, 'big')),
                        size=length)


def fd05_sector(bot: BotLink, sector):
    return b''.join(fd05_read(bot, p) for p in range(sector, sector + USB_ERASE_SECTOR, USB_SECTOR))


def fd05_full(bot: BotLink) -> bytes:
    return b''.join(fd05_read(bot, a) for a in range(0, USB_FLASH_SIZE, USB_SECTOR))


def load_loader(bot: BotLink):
    """fresh-rom → loader-ready（FB06×46 + FB08 + FC0A，CRC16-XMODEM 小端）。"""
    for p in range(0, USB_LOADER_SIZE, USB_SECTOR):
        chunk = LOADER[p:p + USB_SECTOR]
        args = ((USB_LOADER_ADDRESS + p).to_bytes(4, 'big') + len(chunk).to_bytes(2, 'big')
                + b'\x00' + binascii.crc_hqx(chunk, 0).to_bytes(2, 'little'))
        bot.exchange(vendor_cdb(0xfb06, args), data=chunk)
    r = bot.exchange(vendor_cdb(0xfb08, bytes.fromhex('001200000001')), size=16)
    assert r[:2] == b'\xfb\x08', 'FB08 回显不符: %s' % r.hex()
    q = bot.exchange(vendor_cdb(0xfc0a), size=16)
    assert q[:3] == bytes.fromhex('fc0a03'), 'FC0A 应答不符: %s' % q.hex()


# ---------------------------------------------------------------------------
# server 子进程管理（真进程，非 mock；stderr 落文件防管道死锁）
# ---------------------------------------------------------------------------

def free_port() -> int:
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


class ServerProc:
    """一次 sim_bridge_server.py 子进程生命周期（fixture 文件只读打开——P1 红线面）。"""

    def __init__(self, fixture: Path, log_path: Path):
        self.fixture = fixture
        self.log_path = log_path
        self.port = free_port()
        self.log_file = open(log_path, 'wb')
        self.proc = subprocess.Popen(
            [sys.executable, str(TOOLS / 'sim_bridge_server.py'),
             '--listen', '127.0.0.1:%d' % self.port, '--flash-image', str(fixture)],
            cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=self.log_file)
        self.dead = False

    def connect(self, wait_seconds=20.0):
        """连接 + hello（refused=bind 前/busy=活动锁未释放——都退避重试）。"""
        deadline = time.time() + wait_seconds
        last = None
        while True:
            try:
                client = UsbClient(self.port)
                client.send({'t': 'hello', 'proto': 1, 'client': 'sim-usb'})
                reply = client.recv()
                if reply is not None and reply.get('t') == 'hello_ok':
                    return client
                last = reply
                client.close()
            except (OSError, AssertionError) as exc:
                last = repr(exc)
            if time.time() > deadline:
                raise AssertionError('连接 server 失败（port=%d）：%r' % (self.port, last))
            time.sleep(0.05)

    def kill9(self):
        self.proc.kill()                                    # SIGKILL（N3-②）
        self.proc.wait(10)
        self.dead = True

    def terminate(self):
        if not self.dead:
            self.proc.terminate()
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(10)
        self.log_file.close()

    def tail_log(self, limit=2000) -> str:
        try:
            return self.log_path.read_text(encoding='utf-8', errors='replace')[-limit:]
        except OSError:
            return ''


def enter_rom(server: ServerProc):
    """normal → ctl len0 断连重枚举 → 重连 open rom key → loader-ready。返回 (client, bot)。"""
    client = server.connect()
    client.ctl(0)
    if client.recv() is not None:
        raise AssertionError('ctl len0 后应断连（重枚举）')
    client.close()
    client = server.connect()
    devices = client.scan()
    assert devices and devices[0]['key'] == ROM_KEY, '重枚举后应见 ROM 身份: %r' % devices
    result = client.open_key(ROM_KEY)
    assert result['ok'], 'open rom key 失败: %r' % result
    bot = BotLink(client)
    load_loader(bot)
    return client, bot


# ---------------------------------------------------------------------------
# 场景
# ---------------------------------------------------------------------------

def scenario_main(server: ServerProc, verdict: Verdict, A, record):
    """P2-P7 主环（3 扇区计划，与 E2E 同形）+ N1（P7 验证环后顺势在未 armed 状态裸发）。"""
    base, target, plan = A['base'], A['main_target'], A['main_plan']
    s0, s_target = A['S0'], A['S_target']
    sectors = plan['sectors']
    rom_key = ROM_KEY

    # ---- P2：M2 回归前置（未 armed 全序列 + 全片双读==base）----
    client = server.connect()
    world = client.describe()
    ok = (world.get('identity') == 'normal' and not world.get('armed')
          and world.get('flash_sha256') == s0 and world.get('journal_len') == 0)
    verdict.check('P2_initial_banner_normal_and_asset_s0', ok, {k: world.get(k) for k in
                  ('identity', 'armed', 'flash_sha256', 'journal_len')})
    client.close()
    client, bot = enter_rom(server)
    t0 = time.time()
    read1 = fd05_full(bot)
    read2 = fd05_full(bot)
    ok = read1 == base and read2 == base and world.get('flash_sha256') == s0
    verdict.check('P2_m2_regression_dual_read_s0', ok,
                  {'bytes': len(read1), 'sha256': sha256(read1), 'dual_consistent': read1 == read2,
                   'elapsed_s': round(time.time() - t0, 2)})

    # ---- P3：usb_arm ----
    granted = client.arm(sectors, s_target, 'main-nonce-1')
    world = client.describe()
    ok = (granted.get('ok') is True and world.get('armed') is True
          and world.get('work_sha256') == s0 and world.get('plan_sectors') == list(sectors)
          and world.get('read_only') is False and world.get('flash_sha256') == s0)
    verdict.check('P3_arm_granted_copy_on_armed', ok,
                  {'result': granted, 'armed': world.get('armed'),
                   'work_sha256': world.get('work_sha256'), 'plan_sectors': world.get('plan_sectors')})
    dup_same = client.arm(sectors, s_target, 'main-nonce-1')
    dup_other = client.arm(sectors, s_target, 'main-nonce-2')
    ok = (dup_same.get('ok') is False and dup_other.get('ok') is False
          and 'busy' in (dup_same.get('detail') or '') and client.describe().get('armed') is True)
    verdict.check('P3_duplicate_arm_rejected_busy', ok,
                  {'same_nonce': dup_same, 'other_nonce': dup_other})

    # ---- P4：逐扇区 SHA 链（渐进镜像独立构造对拍）----
    for i, sector in enumerate(sectors):
        pre = fd05_sector(bot, sector)
        r = bot.exchange(vendor_cdb(0xfb01, sector.to_bytes(4, 'big')), size=16)
        erased = fd05_sector(bot, sector)
        for p in range(sector, sector + USB_ERASE_SECTOR, USB_SECTOR):
            chunk = target[p:p + USB_SECTOR]
            bot.exchange(vendor_cdb(0xfb04, fb04_args(p, chunk)), data=chunk)
        post = fd05_sector(bot, sector)
        world = client.describe()
        progressive = make_target(base, sectors[:i + 1], A['main_salt'])
        ok = (pre == base[sector:sector + USB_ERASE_SECTOR]
              and r[:2] == b'\xfb\x01' and r[2:] == b'\x00' * 14
              and erased == b'\xff' * USB_ERASE_SECTOR
              and post == target[sector:sector + USB_ERASE_SECTOR]
              and world.get('journal_len') == 9 * (i + 1)
              and world.get('work_sha256') == sha256(progressive)
              and world.get('flash_sha256') == s0)
        verdict.check('P4_sector_0x%x_chain' % sector, ok,
                      {'fb01_echo': r[:2].hex(), 'journal_len': world.get('journal_len'),
                       'work_sha256': world.get('work_sha256'),
                       'expect_progressive_sha': sha256(progressive)})

    # ---- P5：计划外保留区抽查（含计划区间夹缝与尾界）----
    preserved = [a for a in (0x2000, 0x8000, 0xe5000, 0x1f9000, 0x1ff800)
                 if a // USB_ERASE_SECTOR not in {s // USB_ERASE_SECTOR for s in sectors}]
    ok = all(fd05_read(bot, a) == base[a:a + USB_SECTOR] for a in preserved)
    verdict.check('P5_preserved_sectors_untouched', ok,
                  {'addresses': [hex(a) for a in preserved], 'expect': '==base 切片'})

    # ---- P6：全片复读==target（逐字节+SHA；describe 两面互证）----
    t0 = time.time()
    full = fd05_full(bot)
    world = client.describe()
    ok = (full == target and world.get('work_sha256') == s_target
          and world.get('sim_sha256') == s_target and world.get('flash_sha256') == s0)
    verdict.check('P6_full_readback_target', ok,
                  {'bytes': len(full), 'sha256': sha256(full), 'expect': s_target,
                   'work_sha256': world.get('work_sha256'), 'sim_sha256': world.get('sim_sha256'),
                   'elapsed_s': round(time.time() - t0, 2)})

    # ---- P7：reboot + 验证环 ----
    result = client.reboot_msg()
    closed = client.recv() is None
    verdict.check('P7a_reboot_ok_then_disconnect', result.get('ok') is True and closed,
                  {'result': result, 'closed': closed})
    client.close()
    client = server.connect()
    devices = client.scan()
    world = client.describe()
    ok = (devices and devices[0]['key'] == NORMAL_KEY and devices[0]['vid'] == 0x3654
          and world.get('identity') == 'normal' and world.get('armed') is False)
    stale = client.open_key(rom_key)
    verdict.check('P7b_rescan_normal_and_old_rom_key_dead', ok and stale.get('ok') is False,
                  {'devices': devices, 'armed': world.get('armed'), 'stale_open': stale})
    client.close()
    client, bot = enter_rom(server)
    full = fd05_full(bot)
    world = client.describe()
    counts = world.get('commands') or {}
    accounting = {'fb01': counts.get('fb01'), 'fb04': counts.get('fb04'),
                  'journal_len': world.get('journal_len'), 'rejected': world.get('rejected')}
    ok = full == target and world.get('armed') is False and world.get('flash_sha256') == s0
    verdict.check('P7c_verify_loop_full_read_target', ok,
                  {'bytes': len(full), 'sha256': sha256(full), 'armed': world.get('armed')})
    ok = (accounting == E2E_ACCOUNTING and world.get('sim_sha256') == s_target)
    verdict.check('P7d_accounting_matches_e2e_record', ok,
                  {'measured': accounting, 'e2e': E2E_ACCOUNTING,
                   'sim_sha256': world.get('sim_sha256')})
    disk_sha = sha256(A['fixture_path'].read_bytes())
    verdict.check('P7e_disk_fixture_untouched', disk_sha == s0,
                  {'disk_sha256': disk_sha, 'expect': s0})
    record['main_accounting'] = accounting

    # ---- N1：未 armed FB01/FB04 裸发（P7 后未再 arm；当前 loader-ready）----
    world_before = client.describe()
    rejected_before = world_before.get('rejected')
    bot.expect_reject(vendor_cdb(0xfb01, sectors[0].to_bytes(4, 'big')), size=16, desc='N1 FB01')
    chunk = target[sectors[0]:sectors[0] + USB_SECTOR]
    bot.expect_reject(vendor_cdb(0xfb04, fb04_args(sectors[0], chunk)), data=chunk, desc='N1 FB04')
    world = client.describe()
    spot = fd05_read(bot, sectors[0])
    ok = (world.get('rejected') == rejected_before + 2
          and world.get('work_sha256') is None and world.get('armed') is False
          and world.get('read_only') is True
          and spot == target[sectors[0]:sectors[0] + USB_SECTOR])
    verdict.check('N1_not_armed_writes_rejected', ok,
                  {'rejected': world.get('rejected'), 'work_sha256': world.get('work_sha256'),
                   'read_only': world.get('read_only')})
    client.close()


def scenario_n2(server: ServerProc, verdict: Verdict, A, record):
    """N2：半擦断链→armed 保持→半态可见（独立构造）→disarm 回滚→重 arm 重刷全环。"""
    base, target, plan = A['base'], A['n2_target'], A['n2_plan']
    s0 = A['S0']
    sectors = plan['sectors']
    done = sorted(sectors)[:3]                              # 擦 3/6（只擦不写）
    half_expected = with_sectors(base, done)                # 独立构造半态预期镜像

    client, bot = enter_rom(server)
    granted = client.arm(sectors, A['S_n2_target'], 'n2-nonce-1')
    assert granted.get('ok'), granted
    for sector in done:
        bot.exchange(vendor_cdb(0xfb01, sector.to_bytes(4, 'big')), size=16)
    world = client.describe()
    assert world.get('journal_len') == 3, world
    client.close()                                          # 断链（不 disarm）

    client = server.connect()                               # server 进程存活；重连见半态
    world = client.describe()
    ok = (world.get('armed') is True and world.get('journal_len') == 3
          and world.get('work_sha256') == sha256(half_expected)
          and world.get('flash_sha256') == s0)
    verdict.check('N2a_link_drop_keeps_armed_half_state_visible', ok,
                  {'armed': world.get('armed'), 'journal_len': world.get('journal_len'),
                   'work_sha256': world.get('work_sha256'),
                   'expect_half_sha': sha256(half_expected)})
    result = client.open_key(ROM_KEY)
    assert result['ok'], result
    bot = BotLink(client)
    erased_spot = fd05_read(bot, done[0])
    pending = sorted(set(sectors) - set(done))[0]
    pending_spot = fd05_read(bot, pending)
    outside = 0x8000 if 0x8000 // USB_ERASE_SECTOR not in {s // USB_ERASE_SECTOR for s in sectors} else 0x7000
    outside_spot = fd05_read(bot, outside)
    ok = (erased_spot == b'\xff' * USB_SECTOR
          and pending_spot == base[pending:pending + USB_SECTOR]
          and outside_spot == base[outside:outside + USB_SECTOR])
    verdict.check('N2b_fd05_state_faithful', ok,
                  {'erased_0x%x' % done[0]: erased_spot[:8].hex(),
                   'pending_0x%x' % pending: pending_spot[:8].hex(),
                   'outside_0x%x' % outside: outside_spot[:8].hex()})
    disk_sha = sha256(A['fixture_path'].read_bytes())
    verdict.check('N2c_disk_fixture_untouched', disk_sha == s0,
                  {'disk_sha256': disk_sha, 'expect': s0})

    disarmed = client.disarm()
    rolled = fd05_read(bot, done[0])
    world = client.describe()
    ok = (disarmed.get('ok') is True and rolled == base[done[0]:done[0] + USB_SECTOR]
          and world.get('armed') is False and world.get('work_sha256') is None)
    verdict.check('N2d_disarm_rolls_back_to_baseline', ok,
                  {'result': disarmed, 'rolled_back_read': rolled[:8].hex(),
                   'expect': base[done[0]:done[0] + 8].hex()})

    granted = client.arm(sectors, A['S_n2_target'], 'n2-nonce-2')
    world = client.describe()
    ok = granted.get('ok') is True and world.get('work_sha256') == s0
    verdict.check('N2e_rearm_resets_copy_to_s0', ok,
                  {'result': granted, 'work_sha256': world.get('work_sha256')})
    for sector in sectors:                                  # 完整重刷（可恢复）
        pre = fd05_sector(bot, sector)
        assert pre == base[sector:sector + USB_ERASE_SECTOR], '重刷前基线核验失败 @0x%x' % sector
        bot.exchange(vendor_cdb(0xfb01, sector.to_bytes(4, 'big')), size=16)
        assert fd05_sector(bot, sector) == b'\xff' * USB_ERASE_SECTOR
        for p in range(sector, sector + USB_ERASE_SECTOR, USB_SECTOR):
            chunk = target[p:p + USB_SECTOR]
            bot.exchange(vendor_cdb(0xfb04, fb04_args(p, chunk)), data=chunk)
        assert fd05_sector(bot, sector) == target[sector:sector + USB_ERASE_SECTOR]
    full = fd05_full(bot)
    world = client.describe()
    ok = full == target and world.get('work_sha256') == A['S_n2_target']
    verdict.check('N2f_full_reflash_after_recovery', ok,
                  {'bytes': len(full), 'sha256': sha256(full), 'expect': A['S_n2_target']})
    client.close()


def scenario_n3(server: ServerProc, verdict: Verdict, A, record, logs_dir: Path):
    """N3：nonce 作废三态（busy/已消费×2）+ kill -9 重启回滚（§3.5-A 进程级状态）。"""
    sectors = A['n2_plan']['sectors'][:1]                   # 单扇区即够（只验 nonce/回滚面）
    target_sha = A['S_n2_target']
    client, bot = enter_rom(server)
    granted = client.arm(sectors, target_sha, 'N1')
    assert granted.get('ok'), granted
    same = client.arm(sectors, target_sha, 'N1')
    other = client.arm(sectors, target_sha, 'N2')
    disarmed = client.disarm()
    reuse = client.arm(sectors, target_sha, 'N1')
    ok = (same.get('ok') is False and other.get('ok') is False
          and 'busy' in (same.get('detail') or '')
          and disarmed.get('ok') is True
          and reuse.get('ok') is False and '已消费' in (reuse.get('detail') or ''))
    verdict.check('N3a_nonce_dead_armed_and_disarm', ok,
                  {'armed_dup_same': same, 'armed_dup_other': other,
                   'disarm': disarmed, 'reuse_after_disarm': reuse})

    granted = client.arm(sectors, target_sha, 'N3')
    assert granted.get('ok'), granted
    sector = sectors[0]
    bot.exchange(vendor_cdb(0xfb01, sector.to_bytes(4, 'big')), size=16)   # 半态：擦一扇区
    result = client.reboot_msg()
    closed = client.recv() is None
    client.close()
    client = server.connect()
    reuse = client.arm(sectors, target_sha, 'N3')
    ok = result.get('ok') is True and closed and reuse.get('ok') is False \
        and '已消费' in (reuse.get('detail') or '')
    verdict.check('N3b_nonce_dead_after_reboot', ok,
                  {'reboot': result, 'closed': closed, 'reuse_after_reboot': reuse})
    client.close()

    server.kill9()                                          # kill -9 → 重启同 fixture
    server2 = ServerProc(A['fixture_path'], logs_dir / 'm3v-server-n3-restart.log')
    try:
        client = server2.connect()
        world = client.describe()
        ok = (world.get('armed') is False and world.get('read_only') is True
              and world.get('work_sha256') is None and world.get('sim_sha256') == A['S0']
              and world.get('flash_sha256') == A['S0'] and world.get('journal_len') == 0)
        verdict.check('N3c_kill9_restart_rolls_back', ok,
                      {k: world.get(k) for k in ('armed', 'read_only', 'work_sha256',
                                                 'sim_sha256', 'flash_sha256', 'journal_len')})
        client.close()
        client, bot = enter_rom(server2)
        spot = fd05_read(bot, sector)
        ok = spot == A['base'][sector:sector + USB_SECTOR]  # 擦痕不在：磁盘 fixture 从头读
        verdict.check('N3d_fd05_back_to_s0_after_restart', ok,
                      {'sector': hex(sector), 'read': spot[:8].hex(),
                       'expect': A['base'][sector:sector + 8].hex()})
        # 语义注记（§3.1）：旧 nonce 的「无效」=armed 态与消费记录皆进程级，重启即不存在；
        # 同字符串再 arm 是全新会话（副本基线=S0，拿不回重启前的半态）——构造性保证。
        fresh = client.arm(sectors, target_sha, 'N1')
        world = client.describe()
        verdict.note('N3_restart_nonce_semantics',
                     {'rearm_old_nonce_string': fresh,
                      'work_sha256_after': world.get('work_sha256'),
                      'meaning': '重启后无 armed 态/无消费记录：同 nonce 字符串再 arm=全新会话'
                                 '（基线 S0），旧授权构造性无效（spec §3.1）；随后 disarm 收兵'})
        client.disarm()
        client.close()
    finally:
        server2.terminate()


def scenario_n4_n5(server: ServerProc, verdict: Verdict, A, record):
    """N4 计划外写（server 计划门）+ N5 形状负例（含截短 CDB）——副本全程不变。"""
    base = A['base']
    sectors = list(N45_SECTORS)
    target = make_target(base, sectors, salt=5)
    s_target = sha256(target)

    client, bot = enter_rom(server)
    granted = client.arm(sectors, s_target, 'n45-nonce')
    assert granted.get('ok'), granted
    world = client.describe()
    rejected0 = world.get('rejected')

    chunk = target[0x1000:0x1000 + USB_SECTOR]
    bot.expect_reject(vendor_cdb(0xfb01, (0x9000).to_bytes(4, 'big')), size=16,
                      desc='N4 FB01 对齐但计划外')
    bot.expect_reject(vendor_cdb(0xfb01, (0x1001).to_bytes(4, 'big')), size=16,
                      desc='N4 FB01 非 4KiB 对齐')
    bot.expect_reject(vendor_cdb(0xfb01, (USB_FLASH_SIZE).to_bytes(4, 'big')), size=16,
                      desc='N4 FB01 越界')
    bot.expect_reject(vendor_cdb(0xfb04, fb04_args(0x9000, chunk)), data=chunk,
                      desc='N4 FB04 计划外扇区')
    world = client.describe()
    spot = fd05_read(bot, 0x9000)
    ok = (world.get('rejected') == rejected0 + 4 and world.get('work_sha256') == A['S0']
          and spot == base[0x9000:0x9000 + USB_SECTOR])
    verdict.check('N4_out_of_plan_writes_rejected', ok,
                  {'rejected': world.get('rejected'), 'work_sha256': world.get('work_sha256'),
                   'spot_0x9000': spot[:8].hex()})
    verdict.note('N4_app_layer_guard',
                 {'scope': '双层守卫的 app 外层（eraseSector/programSector 构造参数抛、'
                           'FakeDev 零字节）不经 socket 面——归 JVM 单测与 E2E 实录 '
                           '(m3-avd-e2e.json guard_negatives 四负例全过)'})

    # N5：先绿擦 0x1000（使 CRC/重复写门可达），再逐形状负例。
    r = bot.exchange(vendor_cdb(0xfb01, (0x1000).to_bytes(4, 'big')), size=16)
    assert r[:2] == b'\xfb\x01'
    short = target[0x1000:0x1000 + 256]
    bad_crc = bytearray(vendor_cdb(0xfb04, fb04_args(0x1000, chunk)))
    bad_crc[10] ^= 0xFF                                     # CRC 字节翻转
    len256_cdb = ((0xfb04).to_bytes(2, 'big') + (0x1000).to_bytes(4, 'big')
                  + (256).to_bytes(2, 'big') + b'\x00' + (0).to_bytes(2, 'little')).ljust(16, b'\xff')
    cases = [
        (bytes(bad_crc), chunk, 'FB04 CRC 不符'),
        (len256_cdb, short, 'FB04 len≠512'),
        (vendor_cdb(0xfb04, fb04_args(0x2000, chunk)), chunk, 'FB04 未擦先写（计划内 0x2000）'),
        (vendor_cdb(0xfb01, (0x1000).to_bytes(4, 'big')), None, 'FB01 非 16B IN（CBW size=0）'),
        (b'\xfb\x01' + (0x1000).to_bytes(4, 'big') + b'\xff' * 2, None, 'FB01 截短 CDB（8B）'),
        (vendor_cdb(0xfb04, fb04_args(0x1000, chunk))[:9], chunk, 'FB04 截短 CDB（9B 缺 CRC）'),
    ]
    bad_fb01_fill = bytearray(vendor_cdb(0xfb01, (0x1000).to_bytes(4, 'big')))
    bad_fb01_fill[8] = 0x00
    cases.append((bytes(bad_fb01_fill), None, 'FB01 尾段非 0xff 补齐'))
    bad_fb04_fill = bytearray(vendor_cdb(0xfb04, fb04_args(0x1000, chunk)))
    bad_fb04_fill[12] = 0x00
    cases.append((bytes(bad_fb04_fill), chunk, 'FB04 尾段非 0xff 补齐'))
    for cdb, data, desc in cases:
        if data is None:
            # CBW size 声明 16B IN（截短 CDB 在 CDB 长度门即拒；「非 16B IN」用 size=0 打形状门）
            bot.expect_reject(cdb, size=0 if desc.startswith('FB01 非 16B') else 16, desc=desc)
        else:
            bot.expect_reject(cdb, data=data, desc=desc)
    world = client.describe()
    expected_work = with_sectors(base, [0x1000])            # 只有那一次绿擦生效
    spot_ff = fd05_read(bot, 0x1000)
    spot_base = fd05_read(bot, 0x2000)
    ok = (world.get('rejected') == rejected0 + 4 + len(cases)
          and world.get('work_sha256') == sha256(expected_work)
          and spot_ff == b'\xff' * USB_SECTOR and spot_base == base[0x2000:0x2000 + USB_SECTOR])
    verdict.check('N5_shape_negatives_rejected_work_unchanged', ok,
                  {'rejected': world.get('rejected'), 'cases': [c[2] for c in cases],
                   'work_sha256': world.get('work_sha256'),
                   'expect_work_sha': sha256(expected_work)})
    client.close()


# ---------------------------------------------------------------------------
# P1：离线资产构造（fixture/target/plan——期望独立构造）
# ---------------------------------------------------------------------------

def build_assets(verdict: Verdict, record, fixture_path: Path):
    assets = {}
    try:
        import sim_flash_fixture as sff
        ufw = sff.fetch_ufw(ROOT)                           # manifest 钉 SHA；缺则拉取（代理）
        base, report = sff.build(ufw.read_bytes())
        provenance = ('h3s-sim-usb-flash-fixture-v1（官方 UFW flash.bin 逐字节 '
                      '[0,0xe6000) + [0xe6000,2MiB) 0xff 合成填充）')
        record['fixture_report'] = {'profile': report['profile'], 'sha256': report['sha256'],
                                    'regions': report['regions']}
    except Exception as exc:                                # 离线/缺资产：合成回退（如实记录）
        base = bytes((i * 7 + 3) & 0xff for i in range(USB_FLASH_SIZE))
        provenance = 'synthetic-fallback（真实产档不可用：%s）' % repr(exc)[:160]
    fixture_path.parent.mkdir(parents=True, exist_ok=True)
    fixture_path.write_bytes(base)
    assets['base'] = base
    assets['fixture_path'] = fixture_path
    assets['S0'] = sha256(base)
    assets['provenance'] = provenance

    assets['main_salt'] = 1
    assets['main_target'] = make_target(base, MAIN_SECTORS, assets['main_salt'])
    assets['main_plan'] = planner.plan_restore(base, assets['main_target'])
    assets['S_target'] = sha256(assets['main_target'])

    assets['n2_target'] = make_target(base, N2_SECTORS, salt=2)
    assets['n2_plan'] = planner.plan_restore(base, assets['n2_target'])
    assets['S_n2_target'] = sha256(assets['n2_target'])

    record['assets'] = {
        'provenance': provenance, 'S0': assets['S0'], 'S_target': assets['S_target'],
        'S_n2_target': assets['S_n2_target'],
        'main_plan': assets['main_plan'], 'n2_plan': assets['n2_plan'],
        'fixture': str(fixture_path)}

    # P1-a：计划形状（非空/全 4KiB 对齐/在 2MiB 内/与构造扇区集一致）
    plans_ok = True
    for name, plan, expect_sectors in (('main', assets['main_plan'], MAIN_SECTORS),
                                       ('n2', assets['n2_plan'], N2_SECTORS)):
        sectors = plan['sectors']
        ok = (bool(sectors) and all(s % USB_ERASE_SECTOR == 0 and 0 <= s < USB_FLASH_SIZE
                                    for s in sectors)
              and sorted(set(sectors)) == sectors and sectors == sorted(expect_sectors))
        plans_ok = plans_ok and ok
    verdict.check('P1a_plan_shape_and_sector_sets', plans_ok,
                  {'main': assets['main_plan']['sectors'], 'n2': assets['n2_plan']['sectors']})

    # P1-b：SHA 独立复算（planner 报告值 vs 本层 hashlib；目标切片≠基线、非全 ff）
    ok = (assets['main_plan']['base_sha256'] == assets['S0']
          and assets['main_plan']['target_sha256'] == assets['S_target']
          and assets['n2_plan']['base_sha256'] == assets['S0']
          and assets['n2_plan']['target_sha256'] == assets['S_n2_target'])
    diff_ok = True
    for sector in MAIN_SECTORS + N2_SECTORS + N45_SECTORS:
        payload = (assets['main_target'] if sector in MAIN_SECTORS
                   else assets['n2_target'] if sector in N2_SECTORS
                   else make_target(base, N45_SECTORS, salt=5))[sector:sector + USB_ERASE_SECTOR]
        diff_ok = diff_ok and payload != base[sector:sector + USB_ERASE_SECTOR] \
            and payload != b'\xff' * USB_ERASE_SECTOR
    base_nonff = all(base[s:s + USB_ERASE_SECTOR] != b'\xff' * USB_ERASE_SECTOR for s in N2_SECTORS)
    verdict.check('P1b_shas_independently_recomputed', ok and diff_ok and base_nonff,
                  {'S0': assets['S0'], 'S_target': assets['S_target'],
                   'S_n2_target': assets['S_n2_target'],
                   'n2_base_sectors_non_ff': base_nonff,
                   'note': '主环 0x1f8000 基线在 fixture 的 0xff 合成区：该扇区擦除验证为弱断言'
                           '（与 E2E 同形）；program+读回仍非平凡。N2 六扇区基线全非 ff——'
                           '半态/擦除断言非平凡。'})
    return assets


# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--record', type=Path,
                        default=ROOT / 'analysis/local-checks/sim-m3-verdicts.json')
    parser.add_argument('--workdir', type=Path,
                        default=ROOT / 'analysis/local-checks',
                        help='server 日志/fixture 等运行产物目录（gitignored）')
    args = parser.parse_args(argv)

    started = time.strftime('%Y-%m-%d %H:%M:%S')
    record = {'started': started, 'executor': str(Path(__file__).name), 'checks': []}
    verdict = Verdict(record)
    args.workdir.mkdir(parents=True, exist_ok=True)
    fixture_path = args.workdir / 'm3v-flash-2mib.bin'

    assets = build_assets(verdict, record, fixture_path)

    def new_server(name):
        return ServerProc(fixture_path, args.workdir / ('m3v-server-%s.log' % name))

    scenarios = [
        ('main_P2_P7_N1', lambda s: scenario_main(s, verdict, assets, record)),
        ('N2_interrupt', lambda s: scenario_n2(s, verdict, assets, record)),
        ('N3_nonce_restart', lambda s: scenario_n3(s, verdict, assets, record, args.workdir)),
        ('N4_N5_gates', lambda s: scenario_n4_n5(s, verdict, assets, record)),
    ]
    for name, runner in scenarios:
        server = new_server(name)
        t0 = time.time()
        try:
            runner(server)
        except (AssertionError, OSError, ValueError, KeyError) as exc:
            verdict.fail_blocked('%s_shape_mismatch' % name,
                                 {'error': str(exc)[:400], 'server_log_tail': server.tail_log(800)})
        finally:
            server.terminate()
        record.setdefault('scenario_elapsed_s', {})[name] = round(time.time() - t0, 2)

    # 收尾铁证：全部场景跑完，磁盘 fixture 仍==S0（copy-on-armed：写只落进程内副本）。
    disk_sha = sha256(fixture_path.read_bytes())
    verdict.check('F_fixture_disk_untouched_after_all', disk_sha == assets['S0'],
                  {'disk_sha256': disk_sha, 'expect': assets['S0']})

    verdict.note('app_side_scope',
                 {'ui_restore_card': 'app/AVD 侧恢复卡 UI、CDP 驱动全序=E2E 实录'
                                     '（evidence/sim-routes/m3-avd-e2e.json，verdict PASS）——'
                                     '本执行器只管 server 侧机检面，NOT_RUN 不冒充',
                  'journal_reason_text': 'rejected 逐条 reason 文本与 journal 逐条序'
                                         '（erase×1+program×8/扇区按序）不经 socket 暴露：'
                                         '设备级单测 test_sim_usb_device M3FlashDeviceTest 已断言',
                  'm2_regression_units': 'P2 的「M2 既有测试不动仍绿」由 units 层'
                                         'test_sim_usb_device 回归段承担（本执行器只验行为面）'})

    if verdict.blocked is not None:
        record['verdict'] = 'BLOCKED'
        record['blocked'] = verdict.blocked
    elif verdict.failures:
        record['verdict'] = 'FAIL'
    elif verdict.incomplete:
        record['verdict'] = 'PARTIAL'
    else:
        record['verdict'] = 'PASS'
    record['failures'] = verdict.failures
    record['incomplete'] = verdict.incomplete
    record['counts'] = {'executed': sum(1 for c in record.get('checks', []) if c.get('ok') is not None),
                        'incomplete': verdict.incomplete, 'optional_notes': verdict.notes}
    record['finished'] = time.strftime('%Y-%m-%d %H:%M:%S')
    args.record.parent.mkdir(parents=True, exist_ok=True)
    args.record.write_text(json.dumps(record, indent=2, default=str) + '\n', encoding='utf-8')
    print('verdict=%s failures=%d incomplete=%d notes=%d record=%s'
          % (record['verdict'], verdict.failures, verdict.incomplete, verdict.notes, args.record))
    return {'PASS': 0, 'FAIL': 3, 'BLOCKED': 4, 'PARTIAL': 5}[record['verdict']]


if __name__ == '__main__':
    sys.exit(main())
