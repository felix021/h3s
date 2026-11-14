#!/usr/bin/env python3
"""Unit tests for tools/acceptance_record.py(路 AX/sim-accrecord,d1-acceptance §8 工具落地)。

覆盖:往返(dataclass↔JSON↔磁盘)/校验拒绝路径(九栏非空、四值枚举、显式 null、
证据等级附件、PASS+条件性交叉校验、BLOCKED 依赖说明)/BLOCKED 语义(与 PASS 分开计数,
§6.4「BLOCKED 也是没过」)/docs/acceptance-samples 两条真实示例合法/CLI 三入口。
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import acceptance_record as ar

TOOL = Path(__file__).resolve().parent / 'acceptance_record.py'
SAMPLES = Path(__file__).resolve().parents[1] / 'docs' / 'acceptance-samples'
CONDITION = '合成 fixture A(synthetic-not-measured)'


def sample_record(**overrides):
    """合法基线记录(PASS+条件性,条件与假设一致)。"""
    record = ar.Record(
        case_id='D2-a-REC-01',
        firmware_identity='official UFW(SHA256 65cafc…,§3.1 身份钉死)',
        initial_state='可追溯快照:前沿停点世界(3,072,236 @ 0x60012de)',
        inputs_schedule='fixture 注入@模型时间轴 t0..t1(不用指令步数冒充毫秒)',
        observation_boundary='窗口 [t0,t1];观察面=post 边界载荷字',
        assertions=['d1-acceptance §6.1 A 组:手势在 post 边界可区分'],
        timeout_budget='10,000 模型步;超时=FAIL,不是重跑的理由',
        model_assumptions=[CONDITION, '前沿投影栈 opt-in'],
        result='PASS',
        uncovered_items=None,
        evidence_level='conditional',
        evidence_conditions=[CONDITION],
        recorded_at='2026-11-14T08:00:00')
    for key, value in overrides.items():
        setattr(record, key, value)
    return record


class RoundTripTests(unittest.TestCase):
    def test_json_roundtrip_preserves_all_fields(self):
        record = sample_record()
        clone, errors = ar.record_from_json(record.to_json())
        self.assertEqual(errors, [])
        self.assertEqual(clone.to_json(), record.to_json())

    def test_disk_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            record = sample_record(uncovered_items=['模拟前端', 'DMA 总线仲裁'])
            path = ar.save_record(record, directory)
            self.assertEqual(path.name, 'D2-a-REC-01.json')  # 每用例一文件
            loaded, problems = ar.load_records(directory)
            self.assertEqual(problems, {})
            self.assertEqual(loaded['D2-a-REC-01'].to_json(), record.to_json())

    def test_single_string_list_fields_are_promoted(self):
        clone, errors = ar.record_from_json(
            {**sample_record().to_json(), 'assertions': '§6.1 A 组'})
        self.assertEqual(errors, [])
        self.assertEqual(clone.assertions, ['§6.1 A 组'])

    def test_summary_contains_rows_and_stats(self):
        records = {'D2-a-REC-01': sample_record(),
                   'R4-VM-01': sample_record(case_id='R4-VM-01', result='BLOCKED',
                                             evidence_level='none',
                                             evidence_conditions=[],
                                             blocked_missing='SFC 擦写未建模')}
        markdown = ar.summarize(records)
        for needle in ('| D2-a-REC-01 | PASS |', '| R4-VM-01 | BLOCKED | none |',
                       'PASS=1', 'BLOCKED=1', '共 2 条'):
            self.assertIn(needle, markdown)

    def test_summary_escapes_pipes_and_multiline(self):
        record = sample_record(assertions=['a|b\nc'])
        self.assertIn('a\\|b c', ar.summarize({'D2-a-REC-01': record}))


class RejectTests(unittest.TestCase):
    def test_valid_baseline_has_no_errors(self):
        self.assertEqual(ar.validate_record(sample_record()), [])

    def test_each_of_the_nine_columns_required(self):
        for name in ('firmware_identity', 'initial_state', 'inputs_schedule',
                     'observation_boundary', 'timeout_budget'):
            errors = ar.validate_record(sample_record(**{name: ''}))
            self.assertTrue(any(name in error for error in errors), name)
        for name in ('assertions', 'model_assumptions'):
            errors = ar.validate_record(sample_record(**{name: []}))
            self.assertTrue(any(name in error for error in errors), name)

    def test_list_column_empty_element_rejected(self):
        errors = ar.validate_record(sample_record(assertions=['', 'x']))
        self.assertTrue(any('assertions' in error for error in errors))

    def test_result_four_value_enum(self):
        for bad in ('pass', 'PARTIAL', 'SKIP', ''):
            errors = ar.validate_record(sample_record(result=bad))
            self.assertTrue(any('result' in error for error in errors), bad)
        for good in ('PASS', 'FAIL', 'BLOCKED', 'NOT_RUN'):
            record = sample_record(result=good, evidence_level='none',
                                   evidence_conditions=[])
            if good == 'BLOCKED':
                record.blocked_missing = '模型缺件'
            self.assertFalse([e for e in ar.validate_record(record) if 'result' in e], good)

    def test_uncovered_items_explicit_none_semantics(self):
        # §8:未覆盖项不许留空——空必须显式 null;[] 是「想留空没留干净」
        self.assertEqual(ar.validate_record(sample_record(uncovered_items=None)), [])
        self.assertEqual(ar.validate_record(
            sample_record(uncovered_items=['模拟前端'])), [])
        errors = ar.validate_record(sample_record(uncovered_items=[]))
        self.assertTrue(any('显式 null' in error for error in errors))
        errors = ar.validate_record(sample_record(uncovered_items='模拟前端'))
        self.assertTrue(any('uncovered_items' in error for error in errors))

    def test_conditional_requires_conditions(self):
        errors = ar.validate_record(sample_record(evidence_conditions=[]))
        self.assertTrue(any('evidence_conditions' in error for error in errors))

    def test_device_supported_requires_refs(self):
        record = sample_record(evidence_level='device_supported',
                               evidence_conditions=[], evidence_refs=[])
        errors = ar.validate_record(record)
        self.assertTrue(any('evidence_refs' in error for error in errors))
        record.evidence_refs = ['evidence/rom-read.json(2026-11-10 会话)']
        self.assertEqual(ar.validate_record(record), [])

    def test_pass_conditional_cross_checks_model_assumptions(self):
        # PASS+条件性:条件必须逐条出现在「模型假设」栏(两套口径不允许)
        record = sample_record(evidence_conditions=['DMA 完成行为投影'])
        errors = ar.validate_record(record)
        self.assertTrue(any('交叉校验' in error for error in errors), errors)
        record.evidence_conditions = [CONDITION, 'DMA 完成行为投影']
        errors = ar.validate_record(record)
        self.assertTrue(any('交叉校验' in error for error in errors), errors)
        record.evidence_conditions = [CONDITION]
        self.assertEqual(ar.validate_record(record), [])

    def test_non_pass_conditional_records_condition_without_cross_check(self):
        # 交叉校验只约束 PASS(任务口径);NOT_RUN+条件性不触发,由断言栏自证
        record = sample_record(result='NOT_RUN',
                               evidence_conditions=['DMA 完成行为投影'])
        self.assertEqual(ar.validate_record(record), [])

    def test_case_id_format(self):
        for good in ('D2-a-REC-01', 'D2-b-SCO-01', 'D1-TC-01', 'D2-c-OS-01',
                     'D3-BEH-01', 'R4-VM-01'):
            self.assertFalse([e for e in ar.validate_record(sample_record(case_id=good))
                              if '用例 id' in e], good)
        for bad in ('d2-a-rec-1', 'D2-a-REC-1', 'D2-REC-001', 'D2a-REC-01', 'REC-01', ''):
            self.assertTrue(any('用例 id' in error for error in
                                ar.validate_record(sample_record(case_id=bad))), bad)

    def test_timestamp_format(self):
        errors = ar.validate_record(sample_record(recorded_at='2026-11-14'))
        self.assertTrue(any('recorded_at' in error or '时间戳' in error
                            for error in errors))

    def test_from_json_rejects_unknown_fields(self):
        data = {**sample_record().to_json(), 'uncovered_item': []}  # 手滑少个 s
        _, errors = ar.record_from_json(data)
        self.assertTrue(any('未知字段' in error for error in errors))

    def test_from_json_rejects_non_object(self):
        _, errors = ar.record_from_json(['not', 'a', 'record'])
        self.assertTrue(errors)


class BlockedTests(unittest.TestCase):
    """§6.4:BLOCKED 与 FAIL 都是「没过」,区别只是归因;必带缺失依赖说明。"""

    def blocked(self, **overrides):
        return sample_record(result='BLOCKED', evidence_level='none',
                             evidence_conditions=[], **overrides)

    def test_blocked_requires_missing_dependency_note(self):
        errors = ar.validate_record(self.blocked())
        self.assertTrue(any('blocked_missing' in error for error in errors))

    def test_blocked_with_string_or_list_note_accepted(self):
        self.assertEqual(ar.validate_record(
            self.blocked(blocked_missing='USB 设备控制器/主机枚举未建模')), [])
        self.assertEqual(ar.validate_record(
            self.blocked(blocked_missing=['USB 设备控制器未建模', '主机枚举未建模'])), [])

    def test_blocked_rejects_empty_note(self):
        for bad in ('', [], ['']):
            errors = ar.validate_record(self.blocked(blocked_missing=bad))
            self.assertTrue(any('blocked_missing' in error for error in errors), bad)

    def test_non_blocked_may_not_carry_blocked_missing(self):
        errors = ar.validate_record(sample_record(blocked_missing='SFC 未建模'))
        self.assertTrue(any('仅允许 BLOCKED' in error for error in errors))

    def test_blocked_never_counts_as_pass_in_stats(self):
        records = {'D2-a-WIR-01': self.blocked(blocked_missing='USB 控制器未建模')}
        markdown = ar.summarize(records)
        self.assertIn('PASS=0', markdown)
        self.assertIn('BLOCKED=1', markdown)

    def test_load_records_reports_invalid_files_without_loading_them(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            ar.save_record(sample_record(), directory)
            (directory / 'D1-BAD-01.json').write_text('{"case_id": "D1-BAD-01"}',
                                                      encoding='utf-8')
            loaded, problems = ar.load_records(directory)
            self.assertIn(str(directory / 'D1-BAD-01.json'), problems)
            self.assertEqual(len(loaded), 1)  # 合法记录照常加载


class SampleTests(unittest.TestCase):
    """docs/acceptance-samples 两条真实示例(路 AJ/路 AB 取材)必须合法且口径诚实。"""

    def test_samples_validate(self):
        records, problems = ar.load_records(SAMPLES)
        self.assertEqual(problems, {})
        self.assertEqual(set(records), {'D2-a-REC-01', 'R4-VM-01'})

    def test_d2a_rec01_shape(self):
        records, _ = ar.load_records(SAMPLES)
        record = records['D2-a-REC-01']
        self.assertEqual(record.result, 'PASS')
        self.assertEqual(record.evidence_level, 'conditional')
        # 条件性通过的条件逐条来自「模型假设」栏
        self.assertTrue(set(record.evidence_conditions) <= set(record.model_assumptions))
        self.assertTrue(record.uncovered_items)  # 未覆盖项如实列出

    def test_r4_vm01_shape(self):
        records, _ = ar.load_records(SAMPLES)
        record = records['R4-VM-01']
        self.assertEqual(record.result, 'PASS')
        self.assertEqual(record.evidence_level, 'conditional')
        self.assertTrue(set(record.evidence_conditions) <= set(record.model_assumptions))
        # 世界 C 的诚实停点必须出现在观察边界或未覆盖项里(不冒充通过)
        text = json.dumps(record.to_json(), ensure_ascii=False)
        self.assertIn('0x12cc48', text)


class CliTests(unittest.TestCase):
    def run_tool(self, *arguments):
        return subprocess.run([sys.executable, str(TOOL), *arguments],
                               capture_output=True, text=True)

    def test_add_validate_summary_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / 'records'
            source = Path(tmp) / 'new.json'
            new = sample_record(case_id='D2-b-SCO-01', result='BLOCKED',
                                evidence_level='none', evidence_conditions=[],
                                blocked_missing='IRQ68 真实分发链未接线')
            source.write_text(json.dumps(new.to_json(), ensure_ascii=False),
                              encoding='utf-8')
            added = self.run_tool('--add', str(source), '--dir', str(directory))
            self.assertEqual(added.returncode, 0, added.stderr)
            self.assertTrue((directory / 'D2-b-SCO-01.json').exists())
            checked = self.run_tool('--validate', str(directory))
            self.assertEqual(checked.returncode, 0, checked.stderr)
            self.assertIn('全部合法', checked.stdout)
            summary = self.run_tool('--summary', '--dir', str(directory))
            self.assertEqual(summary.returncode, 0)
            self.assertIn('| D2-b-SCO-01 | BLOCKED |', summary.stdout)

    def test_add_rejects_invalid_record_without_writing(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / 'records'
            source = Path(tmp) / 'bad.json'
            source.write_text(json.dumps(
                {**sample_record(result='PARTIAL').to_json()}, ensure_ascii=False),
                encoding='utf-8')
            added = self.run_tool('--add', str(source), '--dir', str(directory))
            self.assertEqual(added.returncode, 1)
            self.assertIn('拒绝写入', added.stderr)
            self.assertFalse((directory / 'D2-a-REC-01.json').exists())

    def test_validate_default_dir_missing_is_zero_records(self):
        # 目录不存在=零记录通过(验收目录由 --add 按需创建)
        with tempfile.TemporaryDirectory() as tmp:
            checked = self.run_tool('--validate', str(Path(tmp) / 'absent'))
            self.assertEqual(checked.returncode, 0)
            self.assertIn('校验 0 条', checked.stdout)

    def test_samples_pass_cli_validate(self):
        checked = self.run_tool('--validate', str(SAMPLES))
        self.assertEqual(checked.returncode, 0, checked.stderr)

    def test_summary_output_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / 'summary.md'
            done = self.run_tool('--summary', '--dir', str(SAMPLES),
                                 '--output', str(output))
            self.assertEqual(done.returncode, 0, done.stderr)
            markdown = output.read_text(encoding='utf-8')
            self.assertIn('| D2-a-REC-01 | PASS |', markdown)
            self.assertIn('| R4-VM-01 | PASS |', markdown)


if __name__ == '__main__':
    unittest.main()
