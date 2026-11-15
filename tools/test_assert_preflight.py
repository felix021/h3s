#!/usr/bin/env python3
"""Unit tests for tools/assert_preflight.py (路 CHK 离线断言预检器).

红/绿契约：原（或构造的）报告 → 绿；故意篡改一个计数/停点字段 → 必红，
且 mismatch 叶子要给出期望值/实际值。全部零仿真：只喂报告给既有 check 面。
"""
import json
import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import assert_preflight
from assert_preflight import ORIGINAL_REQUIRE, detect_kind, preflight, _run_check
from probe_pi32v2 import require
import probe_boot
from probe_boot import BOOT_ARGS, ENTRY, PLL, STACK_TOP


def _sample_check(report):
    """机器性测试用的最小 check 面（源码在本文件内，AST 定位/叶子拆分可用）。"""
    require(report['steps'] == 5, 'steps pinned')
    require(report['pc'] == '0x1' and report['stop']['kind'] == 'k' and
            report['tags'], 'stop pinned')


class MachineryTests(unittest.TestCase):
    """拦截/恢复、continue 模式、首败即抛、aborted、叶子期望-实际值。"""

    def test_green_records_every_assertion(self):
        report = {'steps': 5, 'pc': '0x1', 'stop': {'kind': 'k'}, 'tags': ['a']}
        result = _run_check('sample', _sample_check, (report,))
        self.assertEqual(result['status'], 'pass')
        self.assertEqual([a['key'] for a in result['assertions']], ['steps pinned', 'stop pinned'])
        self.assertTrue(all(a['ok'] for a in result['assertions']))

    def test_tampered_field_is_red_with_expected_and_actual(self):
        report = {'steps': 6, 'pc': '0x1', 'stop': {'kind': 'k'}, 'tags': ['a']}
        result = _run_check('sample', _sample_check, (report,))
        self.assertEqual(result['status'], 'mismatch')
        first = result['assertions'][0]
        self.assertFalse(first['ok'])
        leaf = first['leaves'][0]
        self.assertEqual((leaf['op'], leaf['left']['value'], leaf['right']['value']),
                         ('==', 6, 5))
        self.assertFalse(leaf['ok'])
        # continue 模式：第一条红后，第二条仍被求值（这里为绿）。
        self.assertTrue(result['assertions'][1]['ok'])

    def test_first_failure_mode_matches_original_semantics(self):
        report = {'steps': 6, 'pc': '0x2', 'stop': {}, 'tags': []}
        result = _run_check('sample', _sample_check, (report,), first_failure=True)
        self.assertEqual(result['status'], 'aborted')
        self.assertIn('ValueError: steps pinned', result['error'])
        self.assertEqual(len(result['assertions']), 1)

    def test_structural_crash_is_recorded_as_aborted(self):
        report = {'steps': 6}  # 缺 pc/stop：失败 require 之后代码结构性崩
        result = _run_check('sample', _sample_check, (report,))
        self.assertEqual(result['status'], 'aborted')
        self.assertTrue(result['error'].startswith('KeyError:'))
        self.assertFalse(result['assertions'][0]['ok'])

    def test_require_bindings_restored_after_run(self):
        import probe_irq_entry
        import analyze_image_trace
        _run_check('sample', _sample_check, ({'steps': 5, 'pc': '0x1', 'stop': {'kind': 'k'},
                                             'tags': [1]},))
        for module in (probe_boot, probe_irq_entry, analyze_image_trace):
            self.assertIs(module.require, ORIGINAL_REQUIRE, module.__name__)

    def test_cli_exit_codes_and_json_output(self):
        import subprocess
        with tempfile.TemporaryDirectory() as directory:
            good = Path(directory) / 'good.json'
            bad = Path(directory) / 'bad.json'
            good_report = _boot_prefix_report()
            good.write_text(json.dumps(good_report))
            bad_report = _boot_prefix_report()
            bad_report['scenarios'][1]['completed_steps'] = 7  # 篡改计数
            bad.write_text(json.dumps(bad_report))
            for name, expected_exit in (('good', 0), ('bad', 1)):
                run = subprocess.run([sys.executable,
                                      str(Path(__file__).parent / 'assert_preflight.py'),
                                      '--json', str(Path(directory) / f'{name}.json')],
                                     capture_output=True, text=True)
                self.assertEqual(run.returncode, expected_exit, name)
                payload = json.loads(run.stdout)
                self.assertEqual(payload['exit_status'], expected_exit, name)
            run = subprocess.run([sys.executable, str(Path(__file__).parent / 'assert_preflight.py'),
                                  '--json', str(bad)], capture_output=True, text=True)
            self.assertGreaterEqual(json.loads(run.stdout)['summary']['mismatch'], 1)
            # 缺失文件：默认 error；--allow-missing 搭配在档报告不算失败，
            # 但全部缺失（空转放行）也必须红。
            missing = Path(directory) / 'nope.json'
            for extra, expected in (((), 1), (('--allow-missing',), 1)):
                run = subprocess.run([sys.executable, str(Path(__file__).parent / 'assert_preflight.py'),
                                      *extra, str(missing)], capture_output=True, text=True)
                self.assertEqual(run.returncode, expected)
            run = subprocess.run([sys.executable, str(Path(__file__).parent / 'assert_preflight.py'),
                                  '--allow-missing', str(good), str(missing)],
                                 capture_output=True, text=True)
            self.assertEqual(run.returncode, 0)


class DetectionTests(unittest.TestCase):
    def test_detects_all_three_kinds(self):
        self.assertEqual(detect_kind({'scenarios': [{'name': 'unmodified_upstream'}]}),
                         'probe_boot')
        self.assertEqual(detect_kind({'scenarios': [{'name': 'irq127_request_strict'}]}),
                         'probe_irq_entry')
        self.assertEqual(detect_kind({'pairs': []}), 'analyze_image_trace')
        self.assertIsNone(detect_kind({'scenarios': [], 'something': 1}))

    def test_kind_override_beats_detection(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'report.json'
            # 无场景名的空报告按 --kind 强制成 probe_boot：首断言（场景数）红。
            path.write_text(json.dumps({'scenarios': []}))
            result = preflight([path], kind='probe_boot')
            self.assertEqual(result['reports'][0]['kind'], 'probe_boot')
            self.assertEqual(result['exit_status'], 1)


def _boot_prefix_report():
    """最小构造但全字段满足 probe_boot.check_known_blockers 的报告（字面值与
    断言面同源：ENTRY/PLL/BOOT_ARGS/STACK_TOP 均取自 probe_boot 常量）。"""
    expected = [('unmodified_upstream', 0, ENTRY, 'upstream_simulator_failure', None),
                ('stack_adapter_only', 6, 0x0603561e, 'unmapped_memory', PLL),
                ('pll_bit0_clear_store_context', 11, 0x06035636,
                 'upstream_store_bookkeeping_read', BOOT_ARGS + 4),
                ('pll_bit0_clear_store_adapter', 13, 0x0001fe14,
                 'unmapped_instruction_fetch', 0x0001fe14),
                ('pll_bit0_set_r0_hypothesis', 11, 0x060664d8,
                 'uninitialized_memory', BOOT_ARGS + 4)]
    scenarios = []
    for name, steps, pc, kind, address in expected:
        stop = {'kind': kind}
        if address is not None:
            stop['address'] = hex(address)
        scenarios.append({'name': name, 'completed_steps': steps, 'stop_pc': hex(pc),
                          'stop': stop, 'last_registers': {'sp': hex(STACK_TOP), 'r': ['0x0']},
                          'instruction_adapters': {}, 'memory_events': []})
    scenarios[1]['last_registers']['sp'] = hex(STACK_TOP - 16)
    clear = scenarios[3]
    clear['instruction_adapters'] = {'entry_li_sp_ssp': 2, 'bootstrap_full_width_clock_store': 1}
    clear['memory_events'] = [
        {'address': hex(BOOT_ARGS), 'access': 'read', 'bytes': '00'},  # 干扰项：断言只认写
        {'address': hex(BOOT_ARGS + 4), 'access': 'write',
         'bytes': struct.pack('<II', 24_000_000, 48_000_000).hex()}]
    set_ = scenarios[4]
    set_['last_registers'] = {'sp': hex(STACK_TOP), 'r': [hex(BOOT_ARGS)]}
    return {'scenarios': scenarios}


class ProbeBootSurfaceTests(unittest.TestCase):
    """真实断言面 probe_boot.check_known_blockers：构造报告绿、篡改必红。"""

    def test_constructed_report_is_green(self):
        result = _run_check('probe_boot.check_known_blockers',
                            probe_boot.check_known_blockers, (_boot_prefix_report(),))
        self.assertEqual(result['status'], 'pass')
        self.assertEqual(len(result['assertions']), 14)

    def _mismatches(self, tamper):
        report = _boot_prefix_report()
        tamper(report)
        result = _run_check('probe_boot.check_known_blockers',
                            probe_boot.check_known_blockers, (report,))
        self.assertEqual(result['status'], 'mismatch')
        return result

    def test_tampered_step_count_is_red(self):
        result = self._mismatches(lambda r: r['scenarios'][1].__setitem__('completed_steps', 7))
        miss = [a for a in result['assertions'] if not a['ok']]
        self.assertEqual(len(miss), 1)
        leaf = miss[0]['leaves'][0]
        self.assertEqual((leaf['left']['value'][1], leaf['right']['value'][1]), (7, 6))

    def test_tampered_stop_kind_is_red(self):
        result = self._mismatches(lambda r: r['scenarios'][2]['stop'].__setitem__('kind', 'x'))
        self.assertFalse([a for a in result['assertions'] if not a['ok']][0]['ok'])
        self.assertEqual(result['status'], 'mismatch')

    def test_tampered_clock_pair_bytes_is_red(self):
        def tamper(report):
            for event in report['scenarios'][3]['memory_events']:
                if event['address'] == hex(BOOT_ARGS + 4):
                    event['bytes'] = 'deadbeef'
        result = self._mismatches(tamper)
        keys = [a['key'] for a in result['assertions'] if not a['ok']]
        self.assertIn('clock-pair output mismatch', keys)


class ProbeIrqEntrySurfaceTests(unittest.TestCase):
    """真实断言面 probe_irq_entry.check：最小报告钉首断言（不触 firmware 段）。"""

    REPORT = {'scenarios': [{'name': 'irq127_request_strict', 'completed_steps': 149660,
                             'stop_core': 0, 'stop_pc': '0x12b608',
                             'stop': {'kind': 'software_irq_delivery_not_implemented',
                                      'core': 0, 'irq': 127}}]}

    def test_first_assertion_tamper_is_red_with_step_values(self):
        import probe_irq_entry
        report = json.loads(json.dumps(self.REPORT))
        report['scenarios'][0]['completed_steps'] = 149661
        result = _run_check('probe_irq_entry.check', probe_irq_entry.check, (report, None))
        first = result['assertions'][0]
        self.assertFalse(first['ok'])
        steps = [leaf for leaf in first['leaves'] if leaf['op'] == '=='][0]
        self.assertEqual((steps['left']['value'], steps['right']['value']), (149661, 149660))
        # 后续场景缺失 → 结构性中止（诚实记录，不影响 mismatch 结论）。
        self.assertEqual(result['status'], 'aborted')

    def test_missing_firmware_is_a_clean_error_not_a_crash(self):
        result = preflight([Path('/nonexistent-report.json')], allow_missing=False)
        self.assertEqual(result['exit_status'], 1)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'irq.json'
            path.write_text(json.dumps(self.REPORT))
            result = preflight([path], firmware=Path(directory) / 'nope.ufw')
            entry = result['reports'][0]
            self.assertEqual(entry['status'], 'error')
            self.assertIn('firmware file not found', entry['error'])


class AnalyzeImageTraceSurfaceTests(unittest.TestCase):
    """真实断言面 analyze_image_trace.check_self*：最小 pairs 报告篡改必红
    （期望值来自 frontier_registry，前沿推进后本测试自动跟随）。"""

    def _pair_report(self, scenario=None):
        import frontier_registry
        key = scenario or 'crc_bankb'
        base = frontier_registry.frontier(key)
        run = {'completed_steps': base['completed_steps'] + 1,  # 篡改：+1 步必红
               'stop_pc': base['stop_pc'], 'stop': {'kind': base['stop']['kind']},
               'per_core_steps': {'0': 1, '1': 1}}
        report = {'pairs': [{'name': 'official_vs_tested_b79',
                             'runs': {'official': run, 'tested_b79': dict(run)},
                             'parity': {}, 'first_divergence': {},
                             'divergent_signature_spans': {}, 'final_report_diffs': []}]}
        if scenario:
            report['scenario'] = scenario
        return report, base

    def test_check_self_tampered_steps_is_red(self):
        import analyze_image_trace
        report, base = self._pair_report()
        result = _run_check('check_self', analyze_image_trace.check_self, (report,))
        first = result['assertions'][1]
        self.assertFalse(first['ok'])
        steps = [leaf for leaf in first['leaves'] if leaf.get('right', {}).get('value') ==
                 base['completed_steps']]
        self.assertTrue(steps, 'expected-vs-actual leaves must surface the registry step count')
        self.assertEqual(result['status'], 'aborted')  # parity 缺键 → 结构性中止

    def test_conditional_scenario_selects_check_self_conditional(self):
        import analyze_image_trace
        report, _ = self._pair_report(scenario='p11_heap_experimental')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'conditional.json'
            path.write_text(json.dumps(report))
            result = preflight([path])
            entry = result['reports'][0]
            self.assertEqual(entry['kind'], 'analyze_image_trace')
            self.assertEqual(entry['checks'][0]['name'],
                             'analyze_image_trace.check_self_conditional')
            self.assertIn(entry['checks'][0]['status'], ('mismatch', 'aborted'))
            self.assertEqual(result['exit_status'], 1)


if __name__ == '__main__':
    unittest.main()
