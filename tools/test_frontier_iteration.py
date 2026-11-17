#!/usr/bin/env python3
"""frontier_iteration runner 的纯函数面单测(路 CKPT):报告比较口径与值解析。

不跑仿真(ROM/固件无关):compare 的 int 键规范化(内存 dict 数值键序与文件
往返后的字符串键序不同,逐键比较才是数据等价)、snapshot_* 剥离、差异键报告,
parse_value 的 int/bool/0x/str 四形。runner 的编排面(捕获/cut/臂位)由 CON0
试点实测钉死,不进 units。"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from frontier_iteration import Runner, merge_switch_overrides, parse_value, snapshot_boundary_context


class ParseValue(unittest.TestCase):
    def test_int_bool_hex_and_raw_string(self):
        self.assertEqual(parse_value('0'), 0)
        self.assertEqual(parse_value('42'), 42)
        self.assertEqual(parse_value('0x00000001'), 1)
        self.assertIs(parse_value('true'), True)
        self.assertIs(parse_value('False'), False)
        self.assertEqual(parse_value('s18_18'), 's18_18')


class MergeSwitchOverrides(unittest.TestCase):
    def test_arm_overrides_base_on_the_same_switch(self):
        # base --set X=1 不得静默压掉 arm --off X(同名以 arm 为准)。
        off, sets = merge_switch_overrides(['X'], ['Y=1'], ['X'], [])
        self.assertEqual((off, sets), (['X'], ['Y=1']))
        off, sets = merge_switch_overrides([], ['X=1'], ['X'], [])
        self.assertEqual((off, sets), (['X'], []))
        off, sets = merge_switch_overrides(['X'], [], [], ['X=0'])
        self.assertEqual((off, sets), ([], ['X=0']))
        # 不同名叠加。
        off, sets = merge_switch_overrides(['A'], ['B=1'], ['C'], ['D=2'])
        self.assertEqual((off, sets), (['A', 'C'], ['B=1', 'D=2']))


class SnapshotBoundaryContext(unittest.TestCase):
    """runner 侧干净边界守卫(本路设计核心属性):budget 停点头过、
    Blocked 停点头拒、缺 completed_steps 拒。"""

    @classmethod
    def setUpClass(cls):
        import tempfile
        from br28_model import BR28Model
        from world_snapshot import capture_world
        cls.temp = tempfile.TemporaryDirectory(prefix='h3s-ckpt-boundary-')
        cls.addClassCleanup(cls.temp.cleanup)
        hardware = BR28Model(jl_pled_con0_initial_fixture=0)
        budget = capture_world(hardware=hardware,
                               context={'scenario': 'x', 'completed_steps': 7,
                                        'stop': {'kind': 'instruction_budget'}})
        cls.clean = Path(cls.temp.name) / 'clean.bin'
        budget.save(cls.clean)
        blocked = capture_world(hardware=hardware,
                                context={'scenario': 'x', 'completed_steps': 9,
                                         'stop': {'kind': 'unmapped_memory',
                                                  'address': '0xfd6100'}})
        cls.dirty = Path(cls.temp.name) / 'dirty.bin'
        blocked.save(cls.dirty)

    def test_budget_stop_header_passes(self):
        self.assertEqual(snapshot_boundary_context(self.clean, 'x'),
                         {'scenario': 'x', 'completed_steps': 7})

    def test_blocked_stop_header_refuses(self):
        try:
            from probe_pi32v2 import require as _require
        except Exception:
            _require = None
        try:
            snapshot_boundary_context(self.dirty, 'x')
        except Exception as error:
            self.assertIn('不是 budget 停点快照', str(error))
        else:
            self.fail('dirty (blocked-stop) snapshot must refuse')

    def test_missing_completed_steps_refuses(self):
        from world_snapshot import capture_world
        from br28_model import BR28Model
        bad = capture_world(hardware=BR28Model(),
                            context={'scenario': 'x', 'stop': {'kind': 'instruction_budget'}})
        path = Path(self.temp.name) / 'bad.bin'
        bad.save(path)
        with self.assertRaises(Exception):
            snapshot_boundary_context(path, 'x')


class CompareReports(unittest.TestCase):
    def test_int_keyed_dicts_compare_data_equal_not_key_order(self):
        # 内存行:int 键 dict(数值序);参照行:文件往返后的字符串键 dict
        # (字典序 {"10"<"2"})。数据等价必须判 equal——整串比较会伪红。
        resumed = {'peripherals': {'stores': {2: 'a', 10: 'b'}}, 'stop': {'kind': 'x'}}
        reference = {'peripherals': {'stores': {'2': 'a', '10': 'b'}}, 'stop': {'kind': 'x'}}
        result = Runner.compare(resumed, reference)
        self.assertTrue(result['reference_equal'])
        self.assertEqual(result['differing_keys'], [])

    def test_snapshot_and_timing_keys_are_stripped(self):
        resumed = {'stop': 1, 'snapshot_restore': {'post_resume_attempts': 5},
                   'snapshot_capture': {'loop_attempts': 5}, '_wall_seconds': 3.2}
        self.assertEqual(Runner.compare(resumed, {'stop': 1})['reference_equal'], True)

    def test_differing_keys_are_reported_not_swallowed(self):
        resumed = {'a': 1, 'b': {'c': 2}, 'only_resumed': 1}
        reference = {'a': 1, 'b': {'c': 3}, 'only_reference': 1}
        result = Runner.compare(resumed, reference)
        self.assertFalse(result['reference_equal'])
        self.assertEqual(sorted(result['differing_keys']), ['b', 'only_reference', 'only_resumed'])
        self.assertEqual(result['differing_count'], 3)

    def test_int_keys_with_different_values_stay_red(self):
        # 键序规范化只豁免顺序,int 键下的值差必须仍然报红。
        resumed = {'stores': {2: 'a', 10: 'b'}}
        reference = {'stores': {'2': 'a', '10': 'BEEF'}}
        result = Runner.compare(resumed, reference)
        self.assertFalse(result['reference_equal'])
        self.assertEqual(result['differing_keys'], ['stores'])

    def test_tuple_vs_list_round_trip_is_equal(self):
        self.assertTrue(Runner.compare({'deliveries': (1, 2)}, {'deliveries': [1, 2]})['reference_equal'])


if __name__ == '__main__':
    unittest.main()
