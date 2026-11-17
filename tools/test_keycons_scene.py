#!/usr/bin/env python3
"""tools/keycons_scene.py 纯函数面单测（路 KEYCONS；世界相关断言由场景 runner 实跑覆盖）。

覆盖：队列观察面（任务坐标几何）、动作名环观察面、watch 命中检索、watch PC 表
地址域纪律（flash 映射区+无重复+并行对头无第二半）、泵寄存器面常量、头字打包
算术（禁手算纪律的脚本复核面）。
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import keycons_scene as ks


class FakeMem:
    def __init__(self, store=None):
        self.store = dict(store or {})

    def read(self, address, size):
        return bytes(self.store.get(address + i, 0) for i in range(size))


class FakeCpu:
    def __init__(self, store=None):
        self.mem = FakeMem(store)


def put_u16(store, address, value):
    store.update({address + i: b for i, b in enumerate(value.to_bytes(2, 'little'))})


def put_u32(store, address, value):
    store.update({address + i: b for i, b in enumerate(value.to_bytes(4, 'little'))})


class QueueFaceTest(unittest.TestCase):

    def test_task_queue_face_geometry(self):
        # 任务坐标：+8 写/+10 读/+12 容量/+14 计数/+16 缓冲（出队函数坐标=+8 起）。
        store = {}
        base = 0x418000
        buf = 0x419000
        put_u16(store, base + 8, 5)      # 写游标
        put_u16(store, base + 10, 0)     # 读游标
        put_u16(store, base + 12, 128)   # 容量
        put_u16(store, base + 14, 5)     # 计数
        put_u32(store, base + 16, buf)
        for i, w in enumerate((0x30000405, 0x60226c2, 0x101, 0x0, 0x126270)):
            put_u32(store, buf + 4 * i, w)
        face = ks.task_queue_face(FakeCpu(store), base)
        self.assertEqual(face['count'], 5)
        self.assertEqual(face['write_cursor'], 5)
        self.assertEqual(face['read_cursor'], 0)
        self.assertEqual(face['capacity'], 128)
        self.assertEqual(face['buffer_words'][:5],
                         ['0x30000405', '0x60226c2', '0x101', '0x0', '0x126270'])

    def test_task_queue_face_none_task(self):
        self.assertIsNone(ks.task_queue_face(FakeCpu(), None))


class RingFaceTest(unittest.TestCase):

    def test_ring_face_reads_cursor_and_bytes(self):
        store = {}
        ring = 0x140000
        put_u32(store, ks.RING_PTR_SLOT, ring)
        store[ring] = 9
        store.update({ring + 1 + i: b for i, b in enumerate(b'music_pp\x00')})
        face = ks.ring_face(FakeCpu(store))
        self.assertEqual(face['ring_ptr'], hex(ring))
        self.assertEqual(face['cursor_b0'], 9)
        self.assertIn(b'music_pp', bytes(face['bytes_40']))

    def test_ring_face_absent(self):
        # 零指针（无环）只记指针面,不读游标。
        face = ks.ring_face(FakeCpu())
        self.assertEqual(face, {'ring_ptr': '0x0'})


class WatchTest(unittest.TestCase):

    def test_hit_at_first_and_missing(self):
        hits = [{'pc': hex(ks.KEY_HANDLER), 'r': ['0x1237b8'] * 8, 'step': 4},
                {'pc': hex(ks.KEY_HANDLER), 'r': ['0x0'], 'step': 9}]
        hit = ks.hit_at(hits, ks.KEY_HANDLER)
        self.assertEqual(hit['step'], 4)
        self.assertIsNone(ks.hit_at(hits, ks.CMD1_BRANCH))

    def test_watch_tables_flash_resident_and_unique(self):
        for table in (ks.POST_WATCH, ks.DEQ_WATCH, ks.FILTER_STOCK_WATCH,
                      ks.FILTER_KEY_WATCH, ks.PUMP_WATCH):
            self.assertEqual(len(table), len(set(table)), [hex(p) for p in table])
            for pc in table:
                self.assertTrue(0x06000100 <= pc < 0x07000000, hex(pc))

    def test_no_parallel_pair_second_half_in_watches(self):
        # watch 锚纪律（PARSTOR/CALLIMPL）：并行对只对头。0x60648c2=0x60648c0 对的
        # 第二半,任何 watch 表不得含。
        for table in (ks.POST_WATCH, ks.DEQ_WATCH, ks.FILTER_STOCK_WATCH,
                      ks.FILTER_KEY_WATCH, ks.PUMP_WATCH):
            self.assertNotIn(0x60648c2, table)


class ConstantFaceTest(unittest.TestCase):
    """头字打包/表界/槽位算术的脚本复核（禁手算）。"""

    def test_head_words(self):
        self.assertEqual(ks.POST_EVENT_ID << 8 | 3, 0x10000C03)
        self.assertEqual(ks.STOCK_HEAD_WORD >> 8, 0x300004)
        self.assertEqual(ks.STOCK_HEAD_WORD & 0xFF, ks.STOCK_WORDS)

    def test_table_bounds(self):
        # 表2 6×12B、表1与表2共界（0x60123ca..0x60123e0 prologue 常量）。
        self.assertEqual(ks.R13_TABLE2_END - ks.R11_TABLE2_BEGIN, 6 * 12)
        self.assertEqual(ks.R7_TABLE1_END, ks.R11_TABLE2_BEGIN)
        self.assertEqual(ks.R10_TABLE1_BEGIN, 0x60A89E0)

    def test_slot_arithmetic(self):
        self.assertEqual(ks.RING_PTR_SLOT, 0x126760)
        self.assertEqual(ks.MAP_TABLE, 0x12730C)
        self.assertEqual(ks.MODE_FILTER_SLOT, 0x126F4C)
        self.assertEqual(ks.SWALLOW_BYTE, 0x1265E6)

    def test_register_face_complete(self):
        # 泵段 0x60123f4 的 prologue 常量 live-in（r4=sp+4 随 seg_sp 运行时填,
        # 不在常量表——run_scene 里显式并入）。
        needed = {7, 8, 9, 10, 11, 12, 13}
        self.assertEqual(set(ks.PUMP_REG_FACE_DEFAULT), needed)


if __name__ == '__main__':
    unittest.main()
