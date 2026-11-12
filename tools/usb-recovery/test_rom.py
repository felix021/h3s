"""Offline ROM-reader guards and double-read verification. Never opens USB."""
from contextlib import redirect_stdout
import io
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from read_rom import (P11_CODE_SHA1, P11_HEAP_PROBE_ADDRESSES, P11_HEAP_PROBE_PLAN,
                      ROMReadTransport, ROM_SIZE, crc, memory_read,
                      p11_probe_expectations, p11_probe_execute, pinned_p11_head,
                      probe_read_two, read_two_passes)


class ROMReadTests(unittest.TestCase):
    def transport(self,phase='loader-ready'):
        t=object.__new__(ROMReadTransport)
        t.phase=phase;t.loaded=0;t.loader=b'\x55'*23552
        t.probe_addresses=None
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


class P11HeapProbeTests(unittest.TestCase):
    def transport(self,phase='loader-ready'):
        t=object.__new__(ROMReadTransport)
        t.phase=phase;t.loaded=0;t.loader=b'\x55'*23552
        t.probe_addresses=P11_HEAP_PROBE_ADDRESSES
        t.exchange=lambda cdb,size=0,data=None: bytes(size)
        return t

    def test_plan_is_four_controls_then_target(self):
        self.assertEqual([role for _,_,role in P11_HEAP_PROBE_PLAN],['control']*4+['target'])
        self.assertEqual(P11_HEAP_PROBE_ADDRESSES,{0x19fef4,0x1265f4,0xf20080,0xfd3604,0xf27f40})
        self.assertEqual(P11_HEAP_PROBE_PLAN[-1][1],0xf27f40)

    def test_probe_allowlist_rejects_unplanned_addresses(self):
        for address in (0x0,0x1fdc0,0xf27f41,0xf27fc0,0xf2007f,0x20000,0xfd3600):
            with self.subTest(address=hex(address)),self.assertRaises(ValueError):
                memory_read(self.transport(),address,128)

    def test_probe_pins_128_byte_blocks(self):
        for size in (1,8,32,64,127,129,160,256,512):
            with self.subTest(size=size),self.assertRaises(ValueError):
                memory_read(self.transport(),0xf27f40,size)

    def test_probe_keeps_phase_gate_and_opcode_whitelist(self):
        with self.assertRaises(ValueError):
            memory_read(self.transport('fresh-rom'),0x19fef4,128)
        t=self.transport()
        for opcode in (0xfb00,0xfb01,0xfb04,0xfd05,0xfc0c,0xfc12):
            with self.subTest(opcode=hex(opcode)),self.assertRaises(ValueError):
                t.execute(opcode.to_bytes(2,'big').ljust(16,b'\xff'),16)

    def test_probe_planned_reads_pass_the_guard(self):
        for address in sorted(P11_HEAP_PROBE_ADDRESSES):
            with self.subTest(address=hex(address)):
                self.assertEqual(memory_read(self.transport(),address,128),bytes(128))

    def test_probe_read_two_requires_identical_passes(self):
        class Flaky:
            calls=0
            def cmd(self,opcode,args,size):
                self.calls+=1
                return bytes([self.calls%2])*size
        with self.assertRaisesRegex(ValueError,'differ'):
            probe_read_two(Flaky(),0xf27f40)
        class Stable:
            def cmd(self,opcode,args,size):
                return bytes(range(size))
        self.assertEqual(probe_read_two(Stable(),0x19fef4),bytes(range(128)))

    def test_probe_reports_expectations_without_enforcing(self):
        class Zero:
            def cmd(self,opcode,args,size):
                return bytes(size)
        with tempfile.TemporaryDirectory() as work:
            entries=p11_probe_execute(Zero(),Path(work),p11_probe_expectations(None))
        self.assertEqual(len(entries),5)
        by_name={entry['name']:entry for entry in entries}
        self.assertIs(by_name['cpu1_entry_snapshot']['expected']['match'],False)
        self.assertIs(by_name['p11_heap_descriptor']['expected']['match'],False)
        self.assertIs(by_name['cpu1_ready_snapshot']['expected']['match'],False)
        self.assertIs(by_name['lrct_num']['expected']['checked'],False)
        self.assertIs(by_name['p11_code_head']['expected']['checked'],False)
        for entry in entries:
            self.assertIs(entry['expected']['enforced'],False)
            self.assertIs(entry['two_pass_match'],True)

    def test_probe_expectations_match_pinned_bytes(self):
        head=bytes.fromhex('11'*32)
        def block_for(address):
            block=bytearray(128)
            block[4:8]=bytes.fromhex('f4ca1200')
            block[1]=1
            block[0xa:0xe]=bytes.fromhex('0c3df439')
            if address==0xf20080:
                block[:32]=head
            return bytes(block)
        class Pinned:
            def cmd(self,opcode,args,size):
                return block_for(int.from_bytes(args[:4],'big'))
        with tempfile.TemporaryDirectory() as work:
            entries=p11_probe_execute(Pinned(),Path(work),p11_probe_expectations(head))
            raw={entry['name']:entry for entry in entries}
            for entry in entries:
                path=Path(work)/f"p11-probe-{entry['address']:08x}-{entry['name']}.bin"
                self.assertEqual(path.read_bytes(),block_for(entry['address']))
                self.assertEqual(path.stat().st_mode & 0o777,0o600)
                if entry['expected']['checked']:
                    self.assertIs(entry['expected']['match'],True)
        self.assertIs(raw['lrct_num']['expected']['checked'],False)

    def test_pinned_p11_head_rejects_wrong_bytes(self):
        with tempfile.TemporaryDirectory() as work:
            wrong=Path(work)/'p11.bin'
            wrong.write_bytes(b'\x00'*12248)
            with self.assertRaisesRegex(ValueError,'pinned'):
                pinned_p11_head(wrong)

    def test_p11_code_option_requires_probe_mode(self):
        script=Path(__file__).resolve().parent/'read_rom.py'
        result=subprocess.run([sys.executable,str(script),'--loader','x','--workdir','y',
                               '--p11-code','z'],capture_output=True,text=True)
        self.assertNotEqual(result.returncode,0)
        self.assertIn('--p11-code requires --p11-heap-probe',result.stderr)


if __name__=='__main__':
    unittest.main()
