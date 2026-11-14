"""Offline ROM-reader guards and double-read verification. Never opens USB."""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from read_rom import (P11_CODE_SHA1, P11_HEAP_PROBE_ADDRESSES, P11_HEAP_PROBE_PLAN,
                      ROMReadTransport, ROM_SIZE, SESSION_PACK_ADDRESSES, SESSION_PACK_PLAN,
                      crc, memory_read, p11_probe_expectations, p11_probe_execute,
                      pinned_p11_head, probe_read_two, read_two_passes,
                      session_pack_command_plan, session_pack_controls_status,
                      session_pack_dry_run, session_pack_execute,
                      session_pack_expectations, session_pack_verdict,
                      session_pack_zero_write_assertions)


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


class SessionPackTests(unittest.TestCase):
    def transport(self,phase='loader-ready'):
        t=object.__new__(ROMReadTransport)
        t.phase=phase;t.loaded=0;t.loader=b'\x55'*23552
        t.probe_addresses=SESSION_PACK_ADDRESSES
        t.exchange=lambda cdb,size=0,data=None: bytes(size)
        return t

    def test_pack_plan_is_c1_five_plus_three_targets(self):
        self.assertEqual([entry[0] for entry in SESSION_PACK_PLAN],
            ['cpu1_entry_snapshot','cpu1_ready_snapshot','p11_code_head','lrct_num',
             'p11_heap_descriptor','adc_con_res','p3_ana_read','core_tick_cnt_prd'])
        self.assertEqual([entry[3] for entry in SESSION_PACK_PLAN],
            ['C1']*5+['C2','C3','C5'])
        # The C1 five-address prefix stays byte-identical to the BM probe plan.
        self.assertEqual([(n,a,r) for n,a,r,_ in SESSION_PACK_PLAN[:5]],
            list(P11_HEAP_PROBE_PLAN))
        self.assertEqual(SESSION_PACK_ADDRESSES,
            {0x19fef4,0x1265f4,0xf20080,0xfd3604,0xf27f40,0xfd3100,0xf2c064,0xffe0f0})

    def test_pack_allowlist_rejects_unplanned_addresses_and_sizes(self):
        for address in (0x0,0x1fdc0,0xfd3104,0xf2c060,0xffe0e0,0xffe100,0xf27f41):
            with self.subTest(address=hex(address)),self.assertRaises(ValueError):
                memory_read(self.transport(),address,128)
        for size in (1,32,127,129,256):
            with self.subTest(size=size),self.assertRaises(ValueError):
                memory_read(self.transport(),0xf2c064,size)

    def test_pack_keeps_phase_gate_and_opcode_whitelist(self):
        with self.assertRaises(ValueError):
            memory_read(self.transport('fresh-rom'),0x19fef4,128)
        t=self.transport()
        for opcode in (0xfb00,0xfb01,0xfb02,0xfb04,0xfc0c,0xfc12,0xfd05):
            with self.subTest(opcode=hex(opcode)),self.assertRaises(ValueError):
                t.execute(opcode.to_bytes(2,'big').ljust(16,b'\xff'),16)

    def test_pack_command_plan_totals(self):
        plan=session_pack_command_plan(None)
        self.assertEqual(len(plan),65)
        self.assertEqual([c['step'] for c in plan],list(range(1,66)))
        counts={}
        for command in plan:
            counts[command['opcode']]=counts.get(command['opcode'],0)+1
        self.assertEqual(counts,{'INQUIRY':1,'FB06':46,'FB08':1,'FC0A':1,'FD07':16})
        fd07=[c for c in plan if c['opcode']=='FD07']
        self.assertEqual([c['pass'] for c in fd07],[1]*8+[2]*8)
        self.assertEqual([c['address'] for c in fd07[:8]],
            [entry[1] for entry in SESSION_PACK_PLAN])
        self.assertTrue(all(c['crc16'] is None for c in plan if c['opcode']=='FB06'))

    def test_pack_zero_write_assertions(self):
        assertions=session_pack_zero_write_assertions(session_pack_command_plan(None))
        self.assertTrue(assertions['post_entry_all_fd07_reads'])
        self.assertEqual(assertions['forbidden_opcode_count'],0)
        self.assertEqual(assertions['flash_erase_or_program_commands'],0)
        self.assertEqual(assertions['otp_write_commands'],0)
        self.assertEqual(assertions['fd07_read_commands'],16)
        polluted=session_pack_command_plan(None)+[
            {'step':66,'opcode':'FD05','phase':'loader-ready'},
            {'step':67,'opcode':'FB01','phase':'loader-ready'}]
        self.assertEqual(session_pack_zero_write_assertions(polluted)['forbidden_opcode_count'],2)
        self.assertFalse(session_pack_zero_write_assertions(polluted)['post_entry_all_fd07_reads'])

    def test_pack_dry_run_never_touches_device(self):
        report=session_pack_dry_run(None,None)
        self.assertEqual(report['status'],'SESSION_PACK_DRY_RUN')
        self.assertFalse(report['device_accessed'])
        self.assertTrue(report['dry_run'])
        self.assertTrue(report['session_not_authorized'])
        self.assertEqual(len(report['reads']),8)
        for read in report['reads']:
            self.assertEqual(read['passes'],2)
            for check in read['checks']:
                self.assertEqual(check['verdict'],'branch_pending_session')
                self.assertTrue(check['source'],'every check carries a derivation-chain source')
        by_probe={read['probe']:read for read in report['reads']}
        self.assertEqual(by_probe['C5']['checks'][0]['policy'],'record')
        self.assertEqual(by_probe['C2']['checks'][0]['domain'],[1,1023])
        self.assertIn('400',by_probe['C2']['checks'][0]['candidates'])

    def test_dry_run_requires_session_pack_flag(self):
        script=Path(__file__).resolve().parent/'read_rom.py'
        result=subprocess.run([sys.executable,str(script),'--dry-run'],
                              capture_output=True,text=True)
        self.assertNotEqual(result.returncode,0)
        self.assertIn('--dry-run requires --session-pack',result.stderr)
        result=subprocess.run([sys.executable,str(script),'--session-pack','--dry-run'],
                              capture_output=True,text=True)
        self.assertEqual(result.returncode,0)
        report=json.loads(result.stdout)
        self.assertEqual(report['zero_write_assertions']['command_total'],65)
        self.assertTrue(report['zero_write_assertions']['post_entry_all_fd07_reads'])

    @staticmethod
    def block_for(address,p11_head=bytes(range(32)),heap=bytes.fromhex('0c3df439'),
                  res_halfword=400,charge_bits=0x06):
        block=bytearray(128)
        if address==0x19fef4:
            block[4:8]=bytes.fromhex('f4ca1200')
        elif address==0x1265f4:
            block[1]=1
        elif address==0xf20080:
            block[:32]=p11_head
        elif address==0xf27f40:
            block[0xa:0xe]=heap
        elif address==0xfd3100:
            block[4:6]=res_halfword.to_bytes(2,'little')
        elif address==0xf2c064:
            block[0]=charge_bits
        return bytes(block)

    def run_pack(self,work,expect_p11_head=bytes(range(32)),device_p11_head=None,**kwargs):
        if device_p11_head is None:
            device_p11_head=expect_p11_head
        plan={entry[1]:self.block_for(entry[1],p11_head=device_p11_head,**kwargs)
              for entry in SESSION_PACK_PLAN}
        class Pinned:
            def cmd(self,opcode,args,size):
                return plan[int.from_bytes(args[:4],'big')]
        return session_pack_execute(Pinned(),Path(work),
            session_pack_expectations(expect_p11_head))

    def test_pack_verdicts_match(self):
        with tempfile.TemporaryDirectory() as work:
            entries,heap_status,controls=self.run_pack(work)
            self.assertEqual(heap_status,'ok')
            self.assertTrue(all(v=='match' for v in controls.values()))
            by_name={entry['name']:entry for entry in entries}
            self.assertEqual(by_name['p11_heap_descriptor']['checks'][0]['verdict'],
                'match_upgrade_measured')
            res=by_name['adc_con_res']['checks'][0]
            self.assertEqual(res['verdict'],'match_upgrade_measured')
            self.assertIn('WBG04',res['note'])
            self.assertEqual(by_name['p3_ana_read']['checks'][0]['verdict'],
                'match_upgrade_measured')
            self.assertEqual([c['verdict'] for c in by_name['core_tick_cnt_prd']['checks']],
                ['recorded_no_verdict']*2)
            for entry in entries:
                self.assertTrue(entry['two_pass_match'])
                path=Path(work)/f"session-pack-{entry['address']:08x}-{entry['name']}.bin"
                self.assertEqual(path.stat().st_mode & 0o777,0o600)
            self.assertEqual(len(entries),8)

    def test_pack_verdicts_mismatch_reported_not_enforced(self):
        with tempfile.TemporaryDirectory() as work:
            entries,_,_=self.run_pack(work,
                heap=bytes.fromhex('ffffffff'),res_halfword=0,charge_bits=0x00)
        by_name={entry['name']:entry for entry in entries}
        self.assertEqual(by_name['p11_heap_descriptor']['checks'][0]['verdict'],
            'mismatch_review_no_refit')
        self.assertEqual(by_name['adc_con_res']['checks'][0]['verdict'],
            'inconclusive_not_retained_or_out_of_domain')
        self.assertEqual(by_name['p3_ana_read']['checks'][0]['verdict'],
            'mismatch_review_no_refit')

    def test_pack_domain_value_without_candidate(self):
        with tempfile.TemporaryDirectory() as work:
            entries,_,_=self.run_pack(work,res_halfword=777)
        by_name={entry['name']:entry for entry in entries}
        self.assertEqual(by_name['adc_con_res']['checks'][0]['verdict'],
            'in_domain_no_candidate_recorded_review')
        with tempfile.TemporaryDirectory() as work:
            entries,_,_=self.run_pack(work,res_halfword=500)
        by_name={entry['name']:entry for entry in entries}
        self.assertEqual(by_name['adc_con_res']['checks'][0]['verdict'],
            'match_upgrade_measured')
        self.assertIn('VBAT_4',by_name['adc_con_res']['checks'][0]['note'])

    def test_pack_retention_control_failure_gates_heap_only(self):
        with tempfile.TemporaryDirectory() as work:
            entries,heap_status,controls=self.run_pack(work,
                expect_p11_head=bytes(range(32)),
                device_p11_head=b'\x00'*32,charge_bits=0x06)
        self.assertEqual(heap_status,'failed')
        self.assertEqual(controls['p11_code_head'],'mismatch')
        by_name={entry['name']:entry for entry in entries}
        heap=by_name['p11_heap_descriptor']['checks'][0]
        self.assertEqual(heap['verdict'],'inconclusive_control_failed')
        # C2/C3/C5 verdicts stay independent of the P11 RAM retention control.
        self.assertEqual(by_name['p3_ana_read']['checks'][0]['verdict'],'match_upgrade_measured')
        self.assertEqual(by_name['core_tick_cnt_prd']['checks'][0]['verdict'],
            'recorded_no_verdict')

    def test_pack_controls_incomplete_without_p11_code(self):
        with tempfile.TemporaryDirectory() as work:
            plan={entry[1]:self.block_for(entry[1]) for entry in SESSION_PACK_PLAN}
            class Pinned:
                def cmd(self,opcode,args,size):
                    return plan[int.from_bytes(args[:4],'big')]
            entries,heap_status,controls=session_pack_execute(
                Pinned(),Path(work),session_pack_expectations(None))
        self.assertEqual(heap_status,'incomplete')
        self.assertEqual(controls['p11_code_head'],'not_checked')
        by_name={entry['name']:entry for entry in entries}
        heap=by_name['p11_heap_descriptor']['checks'][0]
        self.assertEqual(heap['verdict'],'match_upgrade_measured')
        self.assertIn('incomplete',heap['note'])

    def test_pack_double_read_mismatch_ends_session(self):
        class Flaky:
            calls=0
            def cmd(self,opcode,args,size):
                self.calls+=1
                return bytes([self.calls%2])*size
        with tempfile.TemporaryDirectory() as work:
            with self.assertRaisesRegex(ValueError,'differ'):
                session_pack_execute(Flaky(),Path(work),session_pack_expectations(None))

    def test_verdict_policies_covered(self):
        block=bytes(128)
        record={'policy':'record'}
        self.assertEqual(session_pack_verdict(record,block),'recorded_no_verdict')
        exact={'policy':'exact','offset':0,'width':2,'expected':'0102'}
        self.assertEqual(session_pack_verdict(exact,bytes.fromhex('0102')+bytes(126)),
            'match_upgrade_measured')
        self.assertEqual(session_pack_verdict(exact,bytes.fromhex('0201')+bytes(126)),
            'mismatch_review_no_refit')
        masked={'policy':'masked','offset':0,'width':1,'expected':'06','mask':0x06}
        self.assertEqual(session_pack_verdict(masked,bytes([0x46])+bytes(127)),
            'match_upgrade_measured')
        self.assertEqual(session_pack_verdict(masked,bytes([0x44])+bytes(127)),
            'mismatch_review_no_refit')
        domain={'policy':'domain','offset':0,'width':2,'domain':(1,1023),'candidates':{400:'x'}}
        self.assertEqual(session_pack_verdict(domain,(400).to_bytes(2,'little')+bytes(126)),
            'match_upgrade_measured')
        self.assertEqual(session_pack_verdict(domain,(0).to_bytes(2,'little')+bytes(126)),
            'inconclusive_not_retained_or_out_of_domain')
        self.assertEqual(session_pack_verdict(domain,(0xffff).to_bytes(2,'little')+bytes(126)),
            'inconclusive_not_retained_or_out_of_domain')
        self.assertEqual(session_pack_verdict(domain,(777).to_bytes(2,'little')+bytes(126)),
            'in_domain_no_candidate_recorded_review')


if __name__=='__main__':
    unittest.main()
