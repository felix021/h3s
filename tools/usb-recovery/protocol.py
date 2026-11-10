"""Fixed Jieli USB MSC protocol. Based on kagaimiq/jl-uboot-tool (MIT).

Pinned upstream: adb3f18889e88ac512ce0a3c4d8cc3d3cb30696a.
See THIRD_PARTY.md and UPSTREAM_LICENSE. No chip erase or OTP command exists.
"""
import struct
import json


class USBTransport:
    def execute(self, cdb, size=0, data=None):
        opcode = int.from_bytes(cdb[:2], 'big')
        if cdb[0] != 0x12 and opcode not in {0xFB06, 0xFB08, 0xFD05, 0xFC0A}:
            raise ValueError('opcode not allowed in recovery reader')
        return self.exchange(cdb, size, data)

    def exchange(self, cdb, size=0, data=None):
        if not 1 <= len(cdb) <= 16:
            raise ValueError('CDB length')
        size = len(data) if data is not None else size
        self.tag += 1
        cbw = struct.pack('<4sIIBBB', b'USBC', self.tag, size,
                          0 if data is not None else 0x80, 0, len(cdb)) + cdb.ljust(16, b'\0')
        if len(self.bulk(0x01, cbw)) != 31:
            raise RuntimeError('short CBW')
        result = b''
        if size:
            result = self.bulk(0x01, data) if data is not None else self.bulk(0x81, size)
            if len(result) != size:
                raise RuntimeError('short data phase: %d/%d' % (len(result), size))
        csw = self.bulk(0x81, 13)
        if len(csw) != 13:
            raise RuntimeError('bad CSW length: ' + csw.hex())
        signature, tag, residue, status = struct.unpack('<4sIIB', csw)
        if (signature, tag, residue, status) != (b'USBS', self.tag, 0, 0):
            raise RuntimeError('bad CSW: ' + csw.hex())
        return result

    def cmd(self, opcode, args=b'', size=16, data=None):
        cdb = (opcode.to_bytes(2, 'big') + args).ljust(16, b'\xff')
        response = self.execute(cdb, size, data)
        if data is None and size == 16 and opcode not in (0xFD05, 0xFD07):
            if response[:2] != opcode.to_bytes(2, 'big'):
                raise RuntimeError('unexpected response ' + response.hex())
        return response

    def inquiry(self):
        raw = self.execute(bytes([0x12, 0, 0, 0, 36, 0]), 36)
        print(json.dumps({'vendor': raw[8:16].decode('ascii').strip(),
            'product': raw[16:32].decode('ascii').strip(),
            'revision': raw[32:36].decode('ascii').strip(), 'raw': raw.hex()}), flush=True)
        return raw
