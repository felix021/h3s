"""Offline failure-path checks. Never opens USB or downloads firmware."""
import struct
import unittest

from h3s_usb import RestoreTransport
from image import ORDER, SIZE, VM_BEGIN, preserve_live_vm, validate_delta, prepare
from protocol import USBTransport


class SafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original = b'\x55'*SIZE
        target = bytearray(cls.original)
        for p in ORDER:
            target[p] = 0x54
        cls.target = bytes(target)

    def test_only_latest_vm_may_vary(self):
        fresh=bytearray(self.original)
        fresh[VM_BEGIN+50] ^= 1
        fresh=bytes(fresh)
        result=preserve_live_vm(self.original,self.target,fresh,fresh)
        self.assertEqual(result[VM_BEGIN:],fresh[VM_BEGIN:])
        for p in ORDER:
            self.assertEqual(result[p:p+4096],self.target[p:p+4096])

    def test_changed_calibration_or_app_refused(self):
        for p in (0,0xee100,0x1fe000):
            with self.subTest(offset=p):
                fresh=bytearray(self.original)
                fresh[p] ^= 1
                with self.assertRaisesRegex(ValueError,'non-VM'):
                    preserve_live_vm(self.original,self.target,bytes(fresh),bytes(fresh))

    def test_unstable_double_read_refused(self):
        fresh=bytearray(self.original)
        fresh[VM_BEGIN] ^= 1
        with self.assertRaisesRegex(ValueError,'reads differ'):
            preserve_live_vm(self.original,self.target,self.original,bytes(fresh))

    def test_target_outside_six_sectors_refused(self):
        bad=bytearray(self.target)
        bad[0x1fe000] ^= 1
        with self.assertRaises(ValueError):
            validate_delta(self.original,bytes(bad))

    def test_unknown_ufw_refused_before_layout_parsing(self):
        with self.assertRaisesRegex(ValueError,'tested ANC UFW'):
            prepare(self.original,b'not a firmware')

    def test_flash_gate_and_cross_sector_writes_refused(self):
        t=RestoreTransport.__new__(RestoreTransport)
        t.writing=False
        t.target=self.target
        t.active_sector=ORDER[0]
        cdb=(b'\xfb\x01'+ORDER[0].to_bytes(4,'big')).ljust(16,b'\xff')
        with self.assertRaisesRegex(ValueError,'gate closed'):
            t.execute(cdb,16)
        t.writing=True
        cdb=(b'\xfb\x01'+ORDER[1].to_bytes(4,'big')).ljust(16,b'\xff')
        with self.assertRaisesRegex(ValueError,'outside active'):
            t.execute(cdb,16)
        for opcode in (0xfb00,0xfb02,0xfc12):
            with self.assertRaises(ValueError):
                t.execute(opcode.to_bytes(2,'big').ljust(16,b'\xff'),16)

    def test_bad_bot_tag_or_residue_refused(self):
        for tag,residue,status in ((2,0,0),(1,1,0),(1,0,1)):
            with self.subTest(tag=tag,residue=residue,status=status):
                t=USBTransport()
                t.tag=0
                responses=iter([b'\0'*31,b'\0'*36,
                    struct.pack('<4sIIB',b'USBS',tag,residue,status)])
                t.bulk=lambda endpoint,data: next(responses)
                with self.assertRaisesRegex(RuntimeError,'bad CSW'):
                    t.inquiry()


if __name__ == '__main__':
    unittest.main()
