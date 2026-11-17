#!/usr/bin/env python3
"""tools/call_scene.py 纯函数面单测(路 CALLIMPL;世界相关断言由场景 runner 实跑覆盖)。

覆盖:帧合成/popcount、watch 命中检索、队列观察面直读、节点环遍历、world_prep
读回面(合成 FakeCpu,零仿真依赖)、watch PC 表的地址域纪律(flash 映射区+无重复)。
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import call_scene as cs


class FakeMem:
    """dict 字节面假内存(read 接口与真实 cpu.mem 同形,不做分页)。"""

    def __init__(self, store=None):
        self.store = dict(store or {})

    def read(self, address, size):
        return bytes(self.store.get(address + i, 0) for i in range(size))


class FakeCpu:
    def __init__(self, store=None):
        self.mem = FakeMem(store)


class FrameTest(unittest.TestCase):

    def test_frame_shape_default_chmap(self):
        self.assertEqual(cs.compose_frame(cs.CHMAP_DEFAULT), bytes([144, 83, 0x05]))

    def test_frame_rejects_multibyte(self):
        with self.assertRaises(ValueError):
            cs.compose_frame(0x100)


class PopcountTest(unittest.TestCase):

    def test_0x05_two_nodes(self):
        self.assertEqual(cs.popcount15(0x05), 2)

    def test_0x0b_three_nodes(self):
        self.assertEqual(cs.popcount15(0x0B), 3)

    def test_high_bits_masked_to_15(self):
        # 创建体 uextra(p:1,l:15) 同域:bit15 及以上不参与节点数。
        self.assertEqual(cs.popcount15(0x8005), 2)


class WatchTest(unittest.TestCase):

    def test_hit_at_finds_first_and_is_hex_keyed(self):
        hits = [{'pc': hex(cs.CHMAP_STORE), 'r': ['0x5', '0x1265c0'], 'step': 9},
                {'pc': hex(cs.CHMAP_STORE), 'r': ['0x5'], 'step': 11}]
        hit = cs.hit_at(hits, cs.CHMAP_STORE)
        self.assertEqual(hit['step'], 9)
        self.assertIsNone(cs.hit_at(hits, cs.TX_API))

    def test_watch_tables_flash_resident_and_unique(self):
        for table in (cs.S1_WATCH, cs.S2_WATCH):
            self.assertEqual(len(table), len(set(table)))
            for pc in table:
                self.assertTrue(0x06000100 <= pc < 0x07000000, hex(pc))


class QueueFaceTest(unittest.TestCase):

    def test_queue_face_raw_geometry(self):
        # +8 头/+12 容量/+14 计数/+16 缓冲(0x60648b0 钉死几何)。
        store = {}
        base = 0x140000
        buf = 0x150000

        def put_words(address, words):
            for index, word in enumerate(words):
                for i, b in enumerate(word.to_bytes(4, 'little')):
                    store[address + 4 * index + i] = b

        put_words(base + 8, (4,))            # head=4(与容量同域铺字节)
        store.update({base + 12 + i: b for i, b in enumerate((16, 0))})     # cap=16
        store.update({base + 14 + i: b for i, b in enumerate((3, 0))})      # count=3
        put_words(base + 16, (buf,))                                                 # 缓冲指针
        put_words(buf, (0x100009, 83, 0))                     # 事件字+参数字
        face = cs.queue_face_raw(FakeCpu(store), base)
        self.assertEqual(face['count'], 3)
        self.assertEqual(face['head'], 4)
        self.assertTrue('0x100009' in face['buffer_words'])
        self.assertTrue(hex(83) in face['buffer_words'])

    def test_queue_face_raw_none_base(self):
        self.assertIsNone(cs.queue_face_raw(FakeCpu(), None))


class NodeRingWalkTest(unittest.TestCase):

    def test_ring_walk_two_nodes(self):
        obj = 0x200000
        head = obj + 24
        n1, n2 = obj + 0x100, obj + 0x200
        store = {}
        store.update({head + i: b for i, b in enumerate(n1.to_bytes(4, 'little'))})
        store.update({obj + 28 + i: b for i, b in enumerate(n2.to_bytes(4, 'little'))})
        store.update({n1 + i: b for i, b in enumerate(n2.to_bytes(4, 'little'))})
        store.update({n1 + 4 + i: b for i, b in enumerate(head.to_bytes(4, 'little'))})
        store.update({n2 + i: b for i, b in enumerate(head.to_bytes(4, 'little'))})
        store.update({n2 + 4 + i: b for i, b in enumerate(n1.to_bytes(4, 'little'))})
        store.update({n1 + 8: 1, n1 + 9: 0})          # mask bit0
        store.update({n2 + 8: 4, n2 + 9: 0})          # mask bit2
        nodes = cs.node_ring_walk(FakeCpu(store), obj)
        self.assertEqual(len(nodes), 2)
        self.assertEqual([n['mask_h8'] for n in nodes], ['0x1', '0x4'])
        self.assertTrue(all(n['cursor_h10'] == '0x0' for n in nodes))

    def test_ring_walk_empty_sentinel(self):
        obj = 0x200000
        head = obj + 24
        store = {head + i: b for i, b in enumerate(head.to_bytes(4, 'little'))}
        self.assertEqual(cs.node_ring_walk(FakeCpu(store), obj), [])


class WorldPrepTest(unittest.TestCase):

    def test_world_prep_park_shape(self):
        # park 面:全零通话态+配置表 0x609d9c4(其余未钉项只记录不断言)。
        store = {cs.QCY + 2600 + i: b for i, b in
                 enumerate((0xc4, 0xd9, 0x09, 0x06))}
        face = cs.world_prep(FakeCpu(store))
        self.assertTrue(all(face['checks'].values()))
        self.assertEqual(face['chmap_b_QCY8087'], 0)
        self.assertEqual(face['aec_slot_QCY624'], '0x0')
        self.assertEqual(face['cfg_table_QCY2600'], '0x609d9c4')

    def test_world_prep_flags_dirty_call_state(self):
        face = cs.world_prep(FakeCpu({cs.QCY + 8087: 5, cs.QCY + 2619: 1}))
        self.assertFalse(face['checks']['chmap_slot_zero'])
        self.assertFalse(face['checks']['tx_busy_gate_open'])


if __name__ == '__main__':
    unittest.main()
