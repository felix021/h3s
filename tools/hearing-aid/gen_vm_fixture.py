#!/usr/bin/env python3
"""生成 key30/key31 的 VM 窗口字节 fixture(路 F2L1-params 注入用)。

复用 route AB(run_vm_key_scene)的 pi32v2 冻结直调机制:用真实固件 vm_init 格式化
空白 VM 半区、真实 set_config 写入 key30/key31 记录(软件 CRC 0x6000ed6 真算),
然后整窗 dump。产出字节与「真机 App 下发 set_config(30/31) 后 flash 里的 VM 记录」
字节同源(固件自己写的),唯一合成输入是 key 值本身。

诚实边界:这是离线生成的 VM fixture,不是设备 VM 字节;写入窗口的 SFC 写端口沿用
route AB 的 byte-copy 桩(0x12be48),不是硅上 SFC 擦写。注入到 F2 世界 plain 的
flash 0x1da000 后,由 boot vm_init 真解析(route AB world B 同款),get_config 真读回。

用法:
  python3 tools/hearing-aid/gen_vm_fixture.py --mpo 85.0 \
      --freqs 100,200,300,400,500,600,700,800,900,1000 \
      --gains 1,2,3,4,5,6,7,8,9,10 \
      --out /tmp/vm_fixture.bin --official <official.ufw>
"""
from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from probe_pi32v2 import upstream, firmware_app, BASE, QCY  # noqa: E402
from probe_boot import require  # noqa: E402
from verify_ufw_integrity import decode  # noqa: E402

FLASH_BASE = 0x06000000
VM_WIN = 0x1da000
CPU_WIN = FLASH_BASE + VM_WIN
VM_CONFIG = 0x1276a4
SENTINEL = 0x00133ff0
GET, SET, VM_INIT = 0x06000f88, 0x06001f12, 0x06060f9a
BUF = 0x131000
STARTUP_DATA_REGION = (0x00124840, 0x060c3338, 0xae0)
STARTUP_RAM_CODE_REGION = (0x0012a640, 0x060c3e18, 0x48d4)


def build_key30(mpo: float) -> bytes:
    bits = struct.unpack('<I', struct.pack('<f', mpo))[0]
    return b'HA\x01\x00' + struct.pack('<I', bits)


def build_key31(freqs, gains) -> bytes:
    assert len(freqs) == len(gains) == 10, 'need exactly 10 segs'
    out = bytearray(b'HA\x02\x00\x0a\x00\x00\x00')
    for f, g in zip(freqs, gains):
        assert 1 <= f <= 21599, f'freq {f} out of range'
        gbits = struct.unpack('<I', struct.pack('<f', float(g)))[0]
        assert (gbits & 0x7fffffff) <= 0x42200000, f'gain {g} out of range'
        out += struct.pack('<H', f) + b'\x00\x00' + struct.pack('<I', gbits)
    return bytes(out)


def generate(module, app, key30_payload, key31_payload, head_bytes):
    """复制 route AB machine()/stub_step()/call() 的最小集,产出 VM 窗口字节。"""

    class Memory(module.Memory):
        def __init__(self):
            super().__init__()
            self.regions = []

        def map(self, address, data, writable=False):
            self.regions.append((address, address + len(data), writable))
            super().write(address, data)

        def valid(self, address, length, write=False):
            require(any(lo <= address and address + length <= hi and (not write or rw)
                        for lo, hi, rw in self.regions),
                    f'unsupported memory {"write" if write else "read"}: {address:#x}+{length}')

        def read(self, address, length):
            self.valid(address, length)
            return super().read(address, length)

        def write(self, address, data):
            self.valid(address, len(data), write=True)
            super().write(address, data)

    class CPU(module.CPU):
        execution_bounds = None
        repeat = None

        def decode(self, pc):
            if self.execution_bounds and pc >= 0x06000000:
                lo, hi = self.execution_bounds
                if not lo <= pc < hi:
                    raise PermissionError(f'fence: {pc:#010x} outside {lo:#x}..{hi:#x}')
                insn, raw, size = super().decode(pc)
                require(pc + size <= hi, 'instruction crosses execution boundary')
                return insn, raw, size
            return super().decode(pc)

        def step(self):
            insn, raw, size = self.decode(self.pc)
            if insn.id == 'rep.i':
                op = module.check.operands(insn, raw)
                body_bytes, count = op['b'], op['n']
                require(1 <= count <= 64, 'REP count outside bounded model')
                require(self.repeat is None, 'nested REP')
                walk = self.pc + size
                covered = 0
                while covered < body_bytes:
                    inner, _, inner_size = self.decode(walk)
                    require(not inner.block and module.branch_field(inner) is None
                            and not inner.id.startswith(('rep', 'goto', 'gotoss', 'call', 'callns', 'pop.pc', 'rts'))
                            and inner.id not in ('tbb', 'tbh')
                            and self.models.get(inner.id) is not None,
                            'REP body not straight-line')
                    covered += inner_size
                    walk += inner_size
                require(covered == body_bytes, 'REP body unaligned')
                self.repeat = {'start': self.pc + size, 'end': self.pc + size + body_bytes, 'remaining': count}
                self.pc += size
                self.steps += 1
            else:
                super().step()
            if self.repeat and self.pc == self.repeat['end']:
                self.repeat['remaining'] -= 1
                if self.repeat['remaining']:
                    self.pc = self.repeat['start']
                else:
                    self.repeat = None

    heap = {'next': 0x00131400}

    def bump(n):
        require(0 < n <= 0x100000, 'allocator stub size')
        addr = heap['next']
        heap['next'] = addr + ((n + 15) & ~15) + 16
        return addr

    def stub_step(c):
        pc = c.pc
        if pc in (0x12e16e, 0x12e160):
            c.R[0] = bump(c.R[0]); c.pc = c.rets; return True
        if pc == 0x12e190:
            c.pc = c.rets; return True
        if pc in (0x6064538, 0x606474a):
            c.R[0] = 0; c.pc = c.rets; return True
        if pc in (0x12b55c, 0x12b5b0):
            c.pc = c.rets; return True
        if pc == 0x12cb08:
            c.R[0] = 1; c.pc = c.rets; return True
        if pc in (0x1fd74, 0x1fd78, 0x1fd7c):
            c.mem.write(c.R[0], c.mem.read(c.R[1], c.R[2])); c.pc = c.rets; return True
        if pc == 0x1fd80:
            c.mem.write(c.R[0], bytes([c.R[1] & 0xff]) * c.R[2]); c.pc = c.rets; return True
        if pc == 0x12be48:
            c.mem.write(FLASH_BASE + c.R[1], c.mem.read(c.R[0], c.R[2])); c.pc = c.rets; return True
        return False

    def machine(head_bytes):
        c = CPU()
        c.mem = Memory()
        c.mem.map(BASE, app)
        c.mem.map(0x00124000, bytes(0xdc000), writable=True)
        data_src, code_src = STARTUP_DATA_REGION[1], STARTUP_RAM_CODE_REGION[1]
        c.mem.map(STARTUP_DATA_REGION[0], app[data_src - BASE:data_src - BASE + STARTUP_DATA_REGION[2]], writable=True)
        c.mem.map(STARTUP_RAM_CODE_REGION[0], app[code_src - BASE:code_src - BASE + STARTUP_RAM_CODE_REGION[2]], writable=True)
        c.mem.map(FLASH_BASE, head_bytes)
        c.mem.map(CPU_WIN, b'\xff' * 0x2000, writable=True)
        c.mem.write(VM_CONFIG, bytes([0x10, 0, 0, 0]) + struct.pack('<I', 0x1da000) + bytes(4) + struct.pack('<I', 0x4000))
        c.mem.write(0x127694, struct.pack('<I', 0x200000))
        c.mem.write(0x127698, struct.pack('<I', 0x200000))
        c.mem.write(0x1276b4, struct.pack('<I', 0))
        c.mem.write(0x1276b8, struct.pack('<I', FLASH_BASE))
        c.execution_bounds = (BASE, 0x060c3338)
        c.sp = 0x00134000
        from br28_arch_models import architectural_k2
        c.models, _ = architectural_k2(c.models)
        return c

    def call(c, entry, args, budget=600000):
        c.sp = 0x00134000
        c.pc = entry
        c.rets = SENTINEL
        c.R[:len(args)] = [a & 0xFFFFFFFF for a in args]
        for r in range(len(args), 16):
            c.R[r] = 0
        stop = None
        try:
            for _ in range(budget):
                if c.pc == SENTINEL:
                    break
                if not stub_step(c):
                    c.step()
        except (PermissionError, RuntimeError, ValueError, KeyError) as e:
            stop = {'pc': hex(c.pc), 'error': f'{type(e).__name__}: {e}'}
        return {'returned': c.pc == SENTINEL, 'r0': c.R[0] & 0xFFFFFFFF, 'stop': stop}

    # flash-head bytes (public flash section first 4 bytes)
    def flash_head(path):
        raw = path.read_bytes()
        row = decode(raw[64:144])
        offset, length = struct.unpack_from('<II', row, 8)
        return raw[offset:offset + 4]

    c = machine(head_bytes)
    r_init = call(c, VM_INIT, [])
    require(r_init['returned'] and r_init['r0'] == 0,
            f'vm_init did not return 0 ({r_init["stop"]})')
    # key 30
    c.mem.write(BUF, key30_payload)
    r30 = call(c, SET, [30, BUF, 8])
    require(r30['returned'] and r30['r0'] == 8, f'set_config(30) failed ({r30["stop"] or r30["r0"]})')
    # key 31
    c.mem.write(BUF, key31_payload)
    r31 = call(c, SET, [31, BUF, 88])
    require(r31['returned'] and r31['r0'] == 88, f'set_config(31) failed ({r31["stop"] or r31["r0"]})')
    # read-back sanity (same world)
    r_g30 = call(c, GET, [30, BUF + 32, 8])
    require(r_g30['returned'] and r_g30['r0'] == 8 and c.mem.read(BUF + 32, 8) == key30_payload,
            'get_config(30) did not read back key30')
    r_g31 = call(c, GET, [31, BUF + 40, 88])
    require(r_g31['returned'] and r_g31['r0'] == 88 and c.mem.read(BUF + 40, 88) == key31_payload,
            'get_config(31) did not read back key31')
    return c.mem.read(CPU_WIN, 0x2000)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mpo', type=float, default=85.0)
    p.add_argument('--freqs', default='100,200,300,400,500,600,700,800,900,1000')
    p.add_argument('--gains', default='1,2,3,4,5,6,7,8,9,10')
    p.add_argument('--out', default='/tmp/vm_fixture.bin')
    p.add_argument('--official', required=True, type=Path)
    args = p.parse_args()
    freqs = [int(x) for x in args.freqs.split(',')]
    gains = [float(x) for x in args.gains.split(',')]
    k30 = build_key30(args.mpo)
    k31 = build_key31(freqs, gains)
    module = upstream()
    app = firmware_app(args.official)

    def flash_head(path):
        raw = path.read_bytes()
        row = decode(raw[64:144])
        offset, length = struct.unpack_from('<II', row, 8)
        return raw[offset:offset + 4]

    window = generate(module, app, k30, k31, flash_head(args.official))
    Path(args.out).write_bytes(window)
    print(json.dumps({
        'key30': k30.hex(), 'key31_len': len(k31),
        'key31_head': k31[:16].hex(),
        'window_len': len(window), 'out': args.out,
    }, indent=1))


if __name__ == '__main__':
    import json
    main()
