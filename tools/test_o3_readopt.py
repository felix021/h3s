#!/usr/bin/env python3
"""Semantics pinning for the O3 Memory.read access-path work (probe_boot.Memory).

docs/o3-memory-design.md §7.3: every optimization here must keep behavior
byte-identical. These tests pin the region-lookup semantics that A2's
last-hit hint / sorted-index fast path and A3a's merged traversal must
preserve, on both the non-overlapping (index) and overlapping (linear
fallback) paths. No private ROM, device access or firmware output.
"""
import json
import unittest

from probe_boot import ENTRY, STACK_TOP, application, explore
from probe_boot_handoff import RAM_CODE, package_header, profile, SENTINEL_A
from probe_pi32v2 import ROOT, upstream


class RegionLookupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module=upstream()
        firmware=ROOT/'firmware/QCY_H3S_7018F_1T2_release_note_V1.1.8_011.ufw'
        cls.app=application(firmware);cls.header=package_header(firmware)

    def handoff(self):
        h=profile(self.header,SENTINEL_A)
        h['description']['phase']='isolated O3 region lookup semantics test; not contiguous startup'
        return h

    def _fetch_case(self,h,pc,budget=4):
        return explore(self.module,self.app,name='o3_region_lookup',handoff=h,
                       diagnostics={'entry_pc':pc},budget=budget)

    def test_uninitialized_fetch_reports_first_region_byte(self):
        # Baseline (non-overlapping): an uninitialized fetch inside qcy_ram_code
        # reports the region kind, the fetch operation and the first missing
        # byte. This is also the first_missing pin for A3a's merged traversal.
        pc=RAM_CODE[0]+8
        result=self._fetch_case(self.handoff(),pc)
        self.assertEqual(result['completed_steps'],0)
        self.assertEqual(result['stop']['kind'],'uninitialized_memory')
        self.assertEqual(result['stop']['region'],'qcy_ram_code')
        self.assertEqual(result['stop']['access'],'fetch')
        self.assertEqual(result['stop']['first_missing'],hex(pc))

    def test_first_missing_skips_known_prefix_bytes(self):
        # Seeding a known prefix must move first_missing to the first hole:
        # the missing check walks bytes in order and stops at the first gap,
        # regardless of already-known bytes before it.
        h=self.handoff();pc=RAM_CODE[0]+8
        h['seeds'].append((pc,bytes.fromhex('11223344')))
        result=self._fetch_case(h,pc)
        self.assertEqual(result['completed_steps'],0)
        self.assertEqual(result['stop']['kind'],'uninitialized_memory')
        self.assertEqual(result['stop']['first_missing'],hex(pc+4))

    def test_overlapping_regions_fall_back_to_first_declared(self):
        # Overlap detection must fall back to the original linear scan, where
        # the FIRST declared region wins (a sorted index would reorder matches
        # and change observable region kinds; the fallback keeps them).
        h=self.handoff();pc=RAM_CODE[0]+8
        h['regions']=((pc-0x40,pc+0x40,'synthetic_overlap_head'),)+tuple(h['regions'])
        result=self._fetch_case(h,pc)
        self.assertEqual(result['stop']['kind'],'uninitialized_memory')
        self.assertEqual(result['stop']['region'],'synthetic_overlap_head')

    def test_disjoint_inserted_region_keeps_index_lookup(self):
        # A disjoint region inserted at the head must not disturb matches
        # elsewhere: the sorted-index path still resolves qcy_ram_code.
        h=self.handoff();pc=RAM_CODE[0]+8
        h['regions']=((0x110000,0x110100,'disjoint_inserted'),)+tuple(h['regions'])
        result=self._fetch_case(h,pc)
        self.assertEqual(result['stop']['kind'],'uninitialized_memory')
        self.assertEqual(result['stop']['region'],'qcy_ram_code')

    def test_unmapped_fetch_reports_detail_fields(self):
        # Outside every region the tail blocker must still carry the detail
        # dict (operation/address/size) built after the A1 deferral point.
        result=self._fetch_case(self.handoff(),0x00400000)
        self.assertEqual(result['completed_steps'],0)
        self.assertEqual(result['stop']['kind'],'unmapped_instruction_fetch')
        self.assertEqual(result['stop']['access'],'fetch')
        self.assertEqual(result['stop']['address'],'0x400000')
        self.assertEqual(result['stop']['size'],6)


class ReadMemoInvalidationTests(unittest.TestCase):
    """O3-A3b RAM 读结果 memo 的失效矩阵(docs/o3-memory-design.md §7.3)。

    探测方式:最小 st.w.sp 场景(一条真实指令写 bootstrap_stack)停点后,
    post_stop 直调 mem.read/write(键链场景 fixture 同款注入点);断言全部
    是「优化前后行为恒等」钉子——失效漏点会直接读回旧值/旧对象。
    """

    @classmethod
    def setUpClass(cls):
        cls.module=upstream()
        firmware=ROOT/'firmware/QCY_H3S_7018F_1T2_release_note_V1.1.8_011.ufw'
        cls.app=application(firmware);cls.header=package_header(firmware)

    def run_probe(self,probe):
        h=profile(self.header,SENTINEL_A)
        h['description']['phase']='isolated O3 read-memo invalidation test'
        h['readonly'].append((0x01c60000,bytes.fromhex('a03f00000000')))  # st.w.sp [sp+252]=r0
        regs=[0]*16;regs[0]=0x11223344
        result=explore(self.module,self.app,name='o3_memo_probe',handoff=h,
            post_stop=lambda machine,hardware,cpu,stop=None:probe(cpu.mem),
            diagnostics={'entry_pc':0x01c60000,'initial_sp':0x123700,'initial_gprs':regs,
                         'exact_parallel_stores':True},budget=1)
        self.assertEqual(result['completed_steps'],1)
        return result['o3_probe']

    def test_repeat_read_shares_object_and_write_invalidates(self):
        def probe(mem):
            a=0x123700+252  # the stored word
            r1=mem.read(a,4);r2=mem.read(a,4)
            shared=r1 is r2 and r1.hex()=='44332211'  # memo hit: same object, stored value
            mem.write(a,bytes.fromhex('99887766'))
            after=mem.read(a,4)      # a stale memo would return 44332211
            return {'o3_probe':{'shared':shared,'after_write':after.hex()}}
        p=self.run_probe(probe)
        self.assertTrue(p['shared'])
        self.assertEqual(p['after_write'],'99887766')

    def test_partial_write_keeps_neighbour_bytes(self):
        def probe(mem):
            a=0x123700+252
            mem.read(a,4)
            mem.write(a+2,bytes.fromhex('aabb'))  # overwrite the upper half
            return {'o3_probe':{'value':mem.read(a,4).hex()}}
        self.assertEqual(self.run_probe(probe)['value'],'4433aabb')

    def test_abstract_unknown_byte_invalidates_and_blocks(self):
        from br28_unknown import UnknownWord
        def probe(mem):
            a=0x123700+252
            mem.read(a,4)
            word=UnknownWord((None,0x11,0x22,0x33),(a,None,None,None))
            mem.write_abstract_byte(a,word,index=0)  # low byte becomes unknown
            blocked={}
            try:
                mem.read(a,4)
            except Exception as e:
                blocked={'region':getattr(e,'details',{}).get('region'),
                         'first_missing':getattr(e,'details',{}).get('first_missing')}
            mem.write(a,bytes.fromhex('55'))  # low byte known again
            return {'o3_probe':{'blocked':blocked,'after':mem.read(a,4).hex()}}
        p=self.run_probe(probe)
        self.assertEqual(p['blocked'],{'region':'bootstrap_stack','first_missing':hex(0x123700+252)})
        self.assertEqual(p['after'],'55332211')

    def test_uninitialized_read_is_not_cached(self):
        A=0x123700+200  # inside the stack region, never written
        def probe(mem):
            def attempt():
                try:
                    mem.read(A,4)
                    return None
                except Exception as e:
                    return getattr(e,'details',{}).get('first_missing')
            first=attempt()
            mem.write(A,bytes.fromhex('01'))  # fill only the first hole
            second=attempt()
            return {'o3_probe':{'first':first,'second':second}}
        p=self.run_probe(probe)
        self.assertEqual(p['first'],hex(A))
        self.assertEqual(p['second'],hex(A+1))  # the shifted hole is re-derived, not memoized

    def test_size_variant_keys_are_independent(self):
        def probe(mem):
            a=0x123700+252
            w4=mem.read(a,4);w2=mem.read(a,2)
            return {'o3_probe':{'w4':w4.hex(),'w2':w2.hex()}}
        p=self.run_probe(probe)
        self.assertEqual((p['w4'],p['w2']),('44332211','4433'))

    def test_capacity_cap_triggers_full_clear_and_stays_correct(self):
        def probe(mem):
            b=0x125320  # qcy_bss window (0x530c bytes)
            span=0x5300
            for offset in range(0,span,0x1000):
                chunk=bytes(k%256 for k in range(offset,min(offset+0x1000,span)))
                mem.write(b+offset,chunk)
            r_first=mem.read(b,3)  # multi-byte: single bytes are CPython-cached, `is` would be meaningless
            sum1=sum(mem.read(b+i,1)[0] for i in range(span))  # 21,248 (addr,1) keys
            expected=sum(i%256 for i in range(span))
            # size variants push the distinct-key count past the 1<<16 cap
            # (~85k total), forcing the full clear inside read.
            for size in (2,3,4):
                for i in range(span-size+1):
                    mem.read(b+i,size)
            r_after=mem.read(b,3)  # first key: cleared by the cap, then rebuilt
            return {'o3_probe':{'r_first':r_first.hex(),'sum1_ok':sum1==expected,
                                'rebuilt':r_first is not r_after and r_first==r_after}}
        p=self.run_probe(probe)
        self.assertEqual(p['r_first'],'000102')
        self.assertTrue(p['sum1_ok'])
        self.assertTrue(p['rebuilt'])


class TraceWindowTests(unittest.TestCase):
    """Route WIND: the explore diagnostic buffer is windowed, not stepwise-formatted.

    The main loop no longer calls codec.format per step (PERF measured
    1.013 calls/step; OPT1's memo only removed the recomputation, not the
    call structure). Rows now carry the (insn, raw) material and are
    materialized only at the consumption points (report assembly, snapshot
    capture), so evicted rows are never formatted. These tests pin the
    zero-change contract on the public-asset typed_memory_replay world
    (16,077 steps, no machine, no private ROM): the eager code formatted
    16,078 times here, the windowed code exactly 64, and the serialized
    last_instructions is byte-identical (sha256 pinned; old code produced
    the same digest).
    """
    @classmethod
    def setUpClass(cls):
        cls.module=upstream()
        firmware=ROOT/'firmware/QCY_H3S_7018F_1T2_release_note_V1.1.8_011.ufw'
        cls.app=application(firmware);cls.header=package_header(firmware)

    def _replay(self):
        from probe_boot_handoff import HANDOFF,SENTINEL_A,profile
        return explore(self.module,self.app,name='typed_memory_replay',adapt_stack=True,
                       pll=1,r0=HANDOFF,handoff=profile(self.header,SENTINEL_A),
                       startup_loops=True)

    def test_window_rows_are_byte_identical_and_bounded(self):
        result=self._replay()
        self.assertEqual(result['completed_steps'],16077)
        self.assertEqual(result['stop']['kind'],'unverified_zero_count_rep')
        rows=result['last_instructions']
        self.assertEqual(len(rows),64)  # budget 16,077 >> 64: eviction ran
        # Key order is the eager construction order (json.dumps has no
        # sort_keys; report bytes depend on this): 63 executed rows plus the
        # blocked stop row without next_pc.
        shapes={tuple(row.keys()) for row in rows}
        self.assertEqual(shapes,{('pc','instruction_id','text','bytes','next_pc'),
                                 ('pc','instruction_id','text','bytes')})
        self.assertEqual(rows[-1].get('next_pc',None),None)
        import hashlib
        self.assertEqual(hashlib.sha256(json.dumps(rows).encode()).hexdigest(),
                         '5e447a12502aa5421656212632a9c2b4fa318eba0ab3be3fb15d1143f2bf51ef')

    def test_loop_makes_no_stepwise_format_calls(self):
        counts={'n':0}
        codec=self.module.codec
        original=codec.format
        def counting(insn,values,syntax=None):
            counts['n']+=1
            return original(insn,values,syntax)
        codec.format=counting
        try:
            result=self._replay()
        finally:
            codec.format=original
        # No machine in this world, so no OPT1 memo layer sits above the
        # counter: every codec.format invocation of the run is counted. The
        # eager code counted 16,078 here; the windowed loop counts only the
        # 64 materialized rows (this prefix executes no parallel pairs).
        self.assertEqual(counts['n'],64)
        self.assertEqual(result['completed_steps'],16077)

    def test_materialized_text_equals_independent_formatting(self):
        # Deferred materialization must produce the eager string: re-decode
        # each row from its own step-time bytes and re-format from scratch.
        rows=self._replay()['last_instructions']
        table=self.module.table.load()
        for row in rows:
            insn,raw,_size=self.module.codec.decode(table,bytes.fromhex(row['bytes']))
            self.assertEqual(insn.id,row['instruction_id'])
            self.assertEqual(self.module.codec.format(insn,raw),row['text'])


if __name__=='__main__':
    unittest.main()
