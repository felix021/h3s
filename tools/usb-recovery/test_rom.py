"""Offline ROM-reader guards and double-read verification. Never opens USB."""
from contextlib import redirect_stdout
import io
from pathlib import Path
import tempfile
import unittest

from read_rom import ROMReadTransport, ROM_SIZE, crc, memory_read, read_two_passes


class ROMReadTests(unittest.TestCase):
    def transport(self,phase='loader-ready'):
        t=object.__new__(ROMReadTransport)
        t.phase=phase;t.loaded=0;t.loader=b'\x55'*23552
        t.exchange=lambda cdb,size=0,data=None: bytes(size)
        return t

    def test_invalid_loader_rejected_before_usb_open(self):
        with self.assertRaisesRegex(ValueError,'pinned'):
            ROMReadTransport(b'not the loader')

    def test_flash_otp_and_arbitrary_commands_forbidden(self):
        t=self.transport()
        for opcode in (0xfb00,0xfb01,0xfb02,0xfb04,0xfc12,0xfd05,0xfc0c):
            with self.subTest(opcode=hex(opcode)),self.assertRaises(ValueError):
                t.execute(opcode.to_bytes(2,'big').ljust(16,b'\xff'),16)

    def test_memory_window_block_and_phase(self):
        for address,size in ((0x120000,128),(ROM_SIZE-64,128),(0,256),(0,512)):
            with self.subTest(address=address,size=size),self.assertRaises(ValueError):
                memory_read(self.transport(),address,size)
        with self.assertRaises(ValueError):
            memory_read(self.transport('fresh-rom'),0,128)
        self.assertEqual(memory_read(self.transport(),0x1fdc0,128),bytes(128))
        with self.assertRaises(ValueError):
            self.transport().execute(bytes.fromhex('fd07000000000080')+bytes(8),128)

    def test_upload_and_jump_are_pinned(self):
        t=self.transport('fresh-rom');block=t.loader[:512]
        args=(0x120000).to_bytes(4,'big')+(512).to_bytes(2,'big')+b'\0'+crc(block).to_bytes(2,'little')
        with self.assertRaises(ValueError):t.cmd(0xfb06,args,data=b'\x56'*512)
        with self.assertRaises(ValueError):t.cmd(0xfb08,bytes.fromhex('001200000001'))
        t.cmd(0xfb06,args,data=block);self.assertEqual(t.loaded,512)
        with self.assertRaises(ValueError):t.cmd(0xfb06,args,data=block)
        t.loaded=len(t.loader)
        with self.assertRaises(ValueError):t.cmd(0xfb08,bytes.fromhex('001200040001'))

    def test_different_second_pass_fails(self):
        class Fake:
            calls=0
            def cmd(self,opcode,args,size):
                self.calls+=1
                return bytes([0 if self.calls<=ROM_SIZE//128 else 1])*size
        with tempfile.TemporaryDirectory() as work,redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(ValueError,'reads differ'):
                read_two_passes(Fake(),Path(work))


if __name__=='__main__':
    unittest.main()
