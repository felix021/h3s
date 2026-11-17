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


class PayloadFaceTest(unittest.TestCase):
    """载荷→(键,子类型,槽,类别)折叠复核（handler 0x60252d8/0x6025334 同式）。"""

    def test_click_payloads(self):
        # AJ §8.2 实测单击载荷族：key10/11/12 单击=子类型 0→类别 1,槽 {7,3,5}。
        self.assertEqual(ks.payload_face(0x10a00), (10, 0, 7, 1))
        self.assertEqual(ks.payload_face(0x10b00), (11, 0, 3, 1))
        self.assertEqual(ks.payload_face(0x10c00), (12, 0, 5, 1))

    def test_multi_click_and_hold(self):
        # 双击 4→2/三击 5→3/四击 6→4（n+2 规律的类别面）;长按确认 1→类别 5
        # （AO §1.2/§3.3:75 拍阈值 post 0x10a01 是类别 5 唯一来源）。
        self.assertEqual(ks.payload_face(0x10a04), (10, 4, 7, 2))
        self.assertEqual(ks.payload_face(0x10a05), (10, 5, 7, 3))
        self.assertEqual(ks.payload_face(0x10a06), (10, 6, 7, 4))
        self.assertEqual(ks.payload_face(0x10a01), (10, 1, 7, 5))
        self.assertEqual(ks.payload_face(0x10b01), (11, 1, 3, 5))
        self.assertEqual(ks.payload_face(0x10c01), (12, 1, 5, 5))

    def test_no_action_categories(self):
        # 持续 2/释放 3/档位 10..14→类别 6（无动作类——REC-08 负例的折叠面）。
        for sub in (2, 3, 10, 11, 14):
            self.assertEqual(ks.payload_face(0x10000 | (10 << 8) | sub)[3], 6)

    def test_out_of_mask_key(self):
        # 越界键（key13 槽 9 在默认表无条目;key14 越掩码→255 不命中）。
        self.assertEqual(ks.payload_face(0x10000 | (13 << 8) | 0), (13, 0, 9, 1))
        self.assertEqual(ks.payload_face(0x10000 | (14 << 8) | 0), (14, 0, 255, 1))


class DefaultMapLookupTest(unittest.TestCase):
    """flash 默认表 45B（0x609e250,§3.2 钉死字节）的首匹配查表复核。"""

    MAP45 = bytes([7, 1, 1, 7, 2, 7, 7, 3, 4, 7, 4, 0, 7, 5, 0,
                   3, 1, 6, 3, 2, 0, 3, 3, 0, 3, 4, 0, 3, 5, 2,
                   5, 1, 5, 5, 2, 0, 5, 3, 0, 5, 4, 0, 5, 5, 3])

    def test_default_bindings(self):
        # §3.2 表:多功能单击 1/双击 7/三击 4/四击 0/长按 0;音量-单击 6/长按 2;
        # 音量+单击 5/长按 3;其余槽 0。
        self.assertEqual(ks.lookup_action(self.MAP45, 7, 1), 1)
        self.assertEqual(ks.lookup_action(self.MAP45, 7, 2), 7)
        self.assertEqual(ks.lookup_action(self.MAP45, 7, 3), 4)
        self.assertEqual(ks.lookup_action(self.MAP45, 7, 4), 0)
        self.assertEqual(ks.lookup_action(self.MAP45, 7, 5), 0)
        self.assertEqual(ks.lookup_action(self.MAP45, 3, 1), 6)
        self.assertEqual(ks.lookup_action(self.MAP45, 3, 5), 2)
        self.assertEqual(ks.lookup_action(self.MAP45, 5, 1), 5)
        self.assertEqual(ks.lookup_action(self.MAP45, 5, 5), 3)
        self.assertEqual(ks.lookup_action(self.MAP45, 3, 2), 0)
        self.assertEqual(ks.lookup_action(self.MAP45, 5, 4), 0)

    def test_miss_returns_none(self):
        # key13 槽 9 默认表无条目;类别 6（持续/释放/档位）同样 miss。
        self.assertIsNone(ks.lookup_action(self.MAP45, 9, 1))
        self.assertIsNone(ks.lookup_action(self.MAP45, 7, 6))
        self.assertIsNone(ks.lookup_action(bytes(45), 7, 1))   # 世界真值全零表

    def test_payload_to_action_chain(self):
        # 载荷→折叠→查表全链（分支断言分派的输入面）。
        for payload, want in ((0x10a00, 1), (0x10a04, 7), (0x10a05, 4),
                              (0x10a06, 0), (0x10a01, 0), (0x10b00, 6),
                              (0x10c00, 5), (0x10b01, 2), (0x10c01, 3)):
            _k, _s, slot, cat = ks.payload_face(payload)
            self.assertEqual(ks.lookup_action(self.MAP45, slot, cat), want,
                             hex(payload))

    def test_unreachable_actions_have_no_entry(self):
        # cmd 8/9/10/11/127 无默认表条目（ACT-05/06(8,10)/08 不可达的根因面）。
        for slot in (7, 3, 5, 9):
            for cat in range(1, 6):
                self.assertNotIn(ks.lookup_action(self.MAP45, slot, cat),
                                 (8, 9, 10, 11, 127))


class ActionAnchorTest(unittest.TestCase):
    """分支锚点与名串偏移的算术复核（AO §1.5+本路反汇编校准）。"""

    def test_name_addresses(self):
        # r8=0x60A18C0（0x602537a li）+偏移=各分支发布器 r1 名串地址。
        self.assertEqual(ks.NAME_BASE + 1024, 0x60A1CC0)   # 'music_pp'（KEYCONS）
        self.assertEqual(ks.NAME_BASE + 1347, 0x60A1E03)   # 'music_prev'
        self.assertEqual(ks.NAME_BASE + 1358, 0x60A1E0E)   # 'music_next'
        self.assertEqual(ks.NAME_BASE + 1164, 0x60A1D4C)   # 'open_siri'
        self.assertEqual(ks.NAME_BASE + 1873, 0x60A2011)   # 'InLowLatency'

    def test_branch_pcs_cover_default_table_actions(self):
        # 默认表可达动作 0..7 每个都有分支锚点或门锚（0=门对,1..7=跳表目标）。
        for action in range(0, 8):
            if action == 0:
                anchors = (ks.CMD0_GATE, ks.CMD0_UNHANDLED)
            else:
                anchors = (ks.ACTION_BRANCH_PC[action],)
            for pc in anchors:
                self.assertTrue(0x06000100 <= pc < 0x07000000, hex(pc))

    def test_action_face_tones(self):
        # 提示音号:1=66/2=70/3=69/4=45/7 无（模式状态机不响键提示音）。
        self.assertEqual(ks.ACTION_FACE[1][1], 66)
        self.assertEqual(ks.ACTION_FACE[2][1], 70)
        self.assertEqual(ks.ACTION_FACE[3][1], 69)
        self.assertEqual(ks.ACTION_FACE[4][1], 45)
        self.assertIsNone(ks.ACTION_FACE[7][1])

    def test_cstr_at_reads_null_terminated(self):
        store = dict.fromkeys(range(0x60A1D4C, 0x60A1D4C + 16), 0x41)
        store.update({0x60A1D4C + i: b for i, b in enumerate(b'open_siri\x00')})
        self.assertEqual(ks.cstr_at(FakeCpu(store), 0x60A1D4C), 'open_siri')

    def test_vol_and_mode_slots(self):
        # QCY+4434（key 86 u16 音量级别）/0x124A04（key 22 模式字节）。
        self.assertEqual(ks.VOL_LEVEL_SLOT, 0x1265C0 + 4434)
        self.assertEqual(ks.VOL_LEVEL_SLOT, 0x127712)
        self.assertEqual(ks.MODE_BYTE, 0x124A04)


if __name__ == '__main__':
    unittest.main()
