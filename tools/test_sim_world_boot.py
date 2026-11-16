#!/usr/bin/env python3
"""tools/sim_world_boot.py 的单元测试（路 SIMB1C→SIMB1F）：构造链直连的零世界验证。

不跑仿真：monkeypatch 装载链（upstream/application/flash_image/check_rom/
package_header）与 probe_irq_entry.irq_case，断言 build_world——
  ① post_stop 直传 irq_case（SIMB1F 起 probe 禁区解禁、irq_case 有透传参，
     SIMB1C 的 dual_case monkeypatch 缝合线已退役）；
  ② 开关集单源来自 frontier_registry.scenario_flags（零手抄）；
  ③ 快照 cfg 形状：缺失→capture；存在→restore+budget=1+expected_context 钉子。
另测 irq_case 透传参自身的互斥守卫（显式 post_stop 与键链场景不同时给）。
"""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import frontier_registry
import probe_irq_entry
import sim_world_boot as swb


from contextlib import ExitStack


def _patch_loaders():
    """把重装载链换成轻桩（真实校验留给真实世界路），返回单个 context manager。"""
    stack = ExitStack()
    for module, name, stub in (
            ('probe_pi32v2', 'upstream', lambda: object()),
            ('probe_boot', 'application', lambda path: b'app'),
            ('probe_boot', 'flash_image', lambda path: b'image'),
            ('probe_boot', 'check_rom', lambda rom: None),
            ('probe_boot_handoff', 'package_header', lambda path: b'header')):
        stack.enter_context(mock.patch.object(sys.modules[module], name, stub))
    return stack


class BuildWorldTest(unittest.TestCase):

    def _build(self, serve_loop, tmpdir, capture=False):
        captured = {}

        def fake_irq_case(module, app, header, rom, name, **kwargs):
            captured['irq_case'] = {'name': name, **kwargs}
            return {'name': name}

        with _patch_loaders(), \
                mock.patch.object(Path, 'read_bytes', lambda self: b'rom'), \
                mock.patch.object(probe_irq_entry, 'irq_case', fake_irq_case):
            result = swb.build_world(serve_loop, rom_path=Path('/rom.bin'),
                                     firmware_path=Path('/fw.ufw'), snapshot_dir=tmpdir,
                                     capture=capture)
        return result, captured

    def test_post_stop_passed_directly_to_irq_case(self):
        serve_loop = lambda machine, hardware, cpu: None
        result, captured = self._build(serve_loop, '/nonexistent-snapdir')
        self.assertIs(captured['irq_case']['post_stop'], serve_loop)   # 直传（无缝合线）
        self.assertEqual(captured['irq_case']['name'], swb.WORLD_NAME)
        self.assertEqual(captured['irq_case']['snapshot']['capture'],
                         Path('/nonexistent-snapdir') /
                         'p11_heap_experimental-sim_bridge_world.bin')

    def test_flags_single_sourced_from_registry(self):
        serve_loop = lambda machine, hardware, cpu: None
        _result, captured = self._build(serve_loop, '/nonexistent-snapdir')
        expected = frontier_registry.scenario_flags('p11_heap_experimental', probe_irq_entry)
        self.assertEqual(captured['irq_case']['flash_image_bytes'], b'image')
        for switch, value in expected.items():
            self.assertEqual(captured['irq_case'][switch], value, switch)
        self.assertGreater(len(expected), 50)                          # 条件栈全量（零手抄）

    def test_restore_config_when_snapshot_exists(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            target = swb.snapshot_path(tmpdir)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b'snapshot')
            _result, captured = self._build(lambda *a: None, tmpdir)
            snapshot = captured['irq_case']['snapshot']
            self.assertEqual(snapshot['restore'], target)
            self.assertEqual(snapshot['budget'], 1)                    # 首步重撞停点
            self.assertEqual(snapshot['expected_context'], {'scenario': swb.WORLD_NAME})


class IrqCasePostStopGuardTest(unittest.TestCase):
    """irq_case 透传参的互斥守卫（SIMB1F 最小改的行为面）。"""

    def test_explicit_post_stop_rejects_key_chain_scenarios(self):
        with self.assertRaises(ValueError) as ctx:
            probe_irq_entry.irq_case(object(), b'app', b'header', b'x' * 0x20000, 'guard_case',
                                     key_chain_scan={'bands': {}},
                                     post_stop=lambda *a: None)
        self.assertIn('exclusive', str(ctx.exception))

    def test_post_stop_reaches_dual_case(self):
        """正例：显式 post_stop 一路透传到 dual_case（不与键链同给）。"""
        seen = {}

        def fake_dual_case(*args, **kwargs):
            seen.update(kwargs)
            return {'name': kwargs.get('name')}

        callback = lambda machine, hardware, cpu: None
        with mock.patch.object(probe_irq_entry, 'dual_case', fake_dual_case):
            probe_irq_entry.irq_case(object(), b'app', b'header', b'x' * 0x20000, 'direct_case',
                                     post_stop=callback)
        self.assertIs(seen['post_stop'], callback)


if __name__ == '__main__':
    unittest.main(verbosity=2)
