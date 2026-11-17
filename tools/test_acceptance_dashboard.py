#!/usr/bin/env python3
"""Unit tests for tools/acceptance_dashboard.py(路 PANEL/sim-panel,全局面板生成器)。

覆盖:合成目录的聚合逻辑(分布表/条件性列/分组表/FAIL·BLOCKED 归因/索引链接/
映射过期拒绝/非法记录拒绝)+真实库集成(各分目录+上级散置全部合法、库内面板与记录
字节级一致——即「记录改动而面板未再生成」的过期守卫)。
"""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import acceptance_record as ar
import acceptance_dashboard as ad

TOOL = Path(__file__).resolve().parent / 'acceptance_dashboard.py'
DASHBOARD = Path(__file__).resolve().parents[1] / 'docs' / 'acceptance-dashboard.md'
REGEN = 'python3 tools/acceptance_dashboard.py'
CONDITION = '合成 fixture A(synthetic-not-measured)'


def record(case_id, result='PASS', **overrides):
    """合法基线记录(PASS+条件性);BLOCKED 自动补 blocked_missing。"""
    fields = dict(
        case_id=case_id,
        firmware_identity='official UFW(SHA256 65cafc…,§3.1 身份钉死)',
        initial_state='可追溯快照:前沿停点世界(3,072,236 @ 0x60012de)',
        inputs_schedule='fixture 注入@模型时间轴 t0..t1(不用指令步数冒充毫秒)',
        observation_boundary='窗口 [t0,t1];观察面=post 边界载荷字',
        assertions=['d1-acceptance §6.1 A 组:手势在 post 边界可区分'],
        timeout_budget='10,000 模型步;超时=FAIL,不是重跑的理由',
        model_assumptions=[CONDITION, '前沿投影栈 opt-in'],
        result=result,
        uncovered_items=None,
        evidence_level='conditional' if result == 'PASS' else 'none',
        evidence_conditions=[CONDITION] if result == 'PASS' else [],
        recorded_at='2026-11-15T08:00:00')
    fields.update(overrides)
    if fields['result'] == 'BLOCKED' and fields.get('blocked_missing') is None:
        fields['blocked_missing'] = 'S19 生产分发未接线(归 D1 事件循环)'
    if fields['result'] != 'BLOCKED':
        fields.setdefault('blocked_missing', None)
    return ar.Record(**fields)


SYN_PANELS = (('p1', '面板一(合成)'),)
SYN_ROOT = ('(上级散置)', '上级目录示例(合成)')
SYN_GROUPS = (
    ('刷机授权', '合成性质', ('A1-HEA-01', 'A1-HEA-02'), '合成 README 第 1 行'),
    ('前沿推进(非授权类,模型件缺失)', '合成性质', ('A1-ACT-09',), '合成 README 第 2 行'),
)


def build_synthetic(base: Path):
    """合成样本树:p1 目录 4 条+上级散置 1 条,覆盖四种结果与条件性列。"""
    samples = base / 'samples'
    for case_id in ('A1-HEA-01', 'A1-HEA-02'):
        ar.save_record(record(case_id), samples / 'p1')
    ar.save_record(record('A1-ACT-09', result='BLOCKED',
                          evidence_level='none', evidence_conditions=[]),
                   samples / 'p1')
    ar.save_record(record('A1-PAR-01', result='FAIL', evidence_level='conditional',
                          evidence_conditions=[CONDITION],
                          uncovered_items=['22 步首交付延迟的指令级仲裁轨迹——下一路接手']),
                   samples / 'p1')
    ar.save_record(record('A1-REC-01', result='NOT_RUN', evidence_level='none',
                          evidence_conditions=[]), samples)
    return samples


class SyntheticTests(unittest.TestCase):
    def generate(self, base: Path):
        return ad.generate(base / 'samples', panels=SYN_PANELS, root_panel=SYN_ROOT,
                           groups=SYN_GROUPS)

    def test_distribution_row_and_totals(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            samples = build_synthetic(base)
            markdown, stats = self.generate(base)
            self.assertIn('| [p1](acceptance-samples/p1/README.md) '
                          '| 面板一(合成) | 2 | 2 | 1 | 1 | 0 | 4 |', markdown)
            # 合计含上级散置 1 条 NOT_RUN:PASS 2(条件性 2)/FAIL 1/BLOCKED 1/NOT_RUN 1
            self.assertIn('| **合计** |  | **2** | **2** | **1** | **1** | **1** | **5** |',
                          markdown)
            self.assertEqual(stats['total'], 5)
            self.assertEqual(stats['totals']['PASS_conditional'], 2)

    def test_pass_without_conditional_evidence_counts_separately(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            samples = build_synthetic(base)
            ar.save_record(record('A1-DRY-01', result='PASS', evidence_level='none',
                                  evidence_conditions=[]), samples / 'p1')
            markdown, _ = self.generate(base)
            # p1: PASS 3(条件性 2——D3-PRB-02 同款 dry-run PASS 不计入条件性)
            self.assertIn('| 面板一(合成) | 3 | 2 | 1 | 1 | 0 | 5 |', markdown)

    def test_groups_list_results_read_from_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            build_synthetic(base)
            markdown, _ = self.generate(base)
            self.assertIn('### 2.1 刷机授权(2 条挂账)', markdown)
            # 链接=相对输出文件的路径:合成树挂在 <tmp>/samples 下,前缀即 samples/
            self.assertIn('[A1-HEA-02](samples/p1/A1-HEA-02.json) | p1 | PASS '
                          '| conditional(1)', markdown)
            self.assertIn('### 2.2 前沿推进(非授权类,模型件缺失)(1 条挂账)', markdown)
            # NOT_RUN 未入组 → 「未入组等待项」清单
            self.assertIn('未入组等待项 1 条', markdown)
            self.assertIn('[A1-REC-01]', markdown)

    def test_fail_and_blocked_tables_carry_record_attribution(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            build_synthetic(base)
            markdown, _ = self.generate(base)
            self.assertIn('### FAIL(1 条)', markdown)
            self.assertIn('22 步首交付延迟的指令级仲裁轨迹', markdown)
            self.assertIn('### BLOCKED(1 条', markdown)
            self.assertIn('S19 生产分发未接线(归 D1 事件循环)', markdown)

    def test_index_links_every_record_to_its_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            build_synthetic(base)
            markdown, _ = self.generate(base)
            for case_id, rel in (('A1-HEA-01', 'samples/p1/A1-HEA-01.json'),
                                 ('A1-REC-01', 'samples/A1-REC-01.json')):
                self.assertIn(f'[{case_id}]({rel})', markdown)

    def test_stale_group_mapping_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            build_synthetic(base)
            groups = (('刷机授权', 'x', ('A1-HEA-01', 'GONE-01'), 'y'),)
            with self.assertRaisesRegex(ValueError, 'GONE-01'):
                ad.generate(base / 'samples', panels=SYN_PANELS, root_panel=SYN_ROOT,
                            groups=groups)

    def test_invalid_record_refuses_to_generate(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            samples = build_synthetic(base)
            (samples / 'p1' / 'A1-BAD-01.json').write_text('{"case_id": "A1-BAD-01"}',
                                                           encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'A1-BAD-01'):
                ad.generate(samples, panels=SYN_PANELS, root_panel=SYN_ROOT,
                            groups=SYN_GROUPS)

    def test_duplicate_case_id_across_directories_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            samples = build_synthetic(base)
            ar.save_record(record('A1-HEA-01'), samples)  # 与 p1/ 内重复
            with self.assertRaisesRegex(ValueError, '重复'):
                ad.generate(samples, panels=SYN_PANELS, root_panel=SYN_ROOT,
                            groups=SYN_GROUPS)

    def test_output_is_deterministic(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            build_synthetic(base)
            first, _ = self.generate(base)
            second, _ = self.generate(base)
            self.assertEqual(first, second)  # 无时钟:字节级可再生(过期校验的前提)


class RealRepoTests(unittest.TestCase):
    """真实库:各分目录+上级散置全部合法、面板与记录一致(过期守卫)。"""

    def test_all_real_records_valid(self):
        _, _, _, problems = ad.scan_panels(ad.SAMPLES)
        self.assertEqual(problems, {})

    def test_committed_dashboard_matches_records(self):
        markdown, stats = ad.generate()
        self.assertTrue(DASHBOARD.exists(),
                        f'缺少 docs/acceptance-dashboard.md——请先 {REGEN}')
        self.assertEqual(DASHBOARD.read_text(encoding='utf-8'), markdown,
                         f'docs/acceptance-dashboard.md 与记录不一致——请再生成({REGEN})'
                         '并连同记录改动一起提交')

    def test_key_records_visible_on_dashboard(self):
        markdown, _ = ad.generate()
        for needle in ('[A1-PAR-01]', 'FAIL', '[D2-a-ACT-10]', '### BLOCKED(',
                       '刷机授权', '真机 BLE 联调授权', '只读观测会话授权'):
            self.assertIn(needle, markdown)
        # 唯一 FAIL 与全部 BLOCKED 都在关键表里有归因行
        self.assertEqual(markdown.count('| [A1-PAR-01]('), 2)  # FAIL 表+索引
        for case_id in ('D2-a-ACT-10', 'R2-HEA-06', 'R3-APP-05', 'R4-VM-05'):
            self.assertIn(f'| [{case_id}](', markdown)


class CliTests(unittest.TestCase):
    def run_tool(self, *arguments):
        return subprocess.run([sys.executable, str(TOOL), *arguments],
                              capture_output=True, text=True,
                              cwd=str(Path(__file__).resolve().parents[1]))

    def test_check_passes_when_fresh(self):
        done = self.run_tool('--check')
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn('未过期', done.stdout)

    def test_stdout_equals_committed_file(self):
        done = self.run_tool('--stdout')
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout,
                         DASHBOARD.read_text(encoding='utf-8'))

    def test_output_flag_writes_given_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / 'panel.md'
            done = self.run_tool('--output', str(output))
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertEqual(output.read_text(encoding='utf-8'),
                             DASHBOARD.read_text(encoding='utf-8'))

    def test_check_fails_when_dashboard_stale(self):
        # 用 --output 生成一份旧面板到库内路径,再 --check 必须红(记录「改动」=面板落后)
        original = DASHBOARD.read_text(encoding='utf-8')
        try:
            # 陈旧=文件内容≠generate() 输出,与任何计数无关(截尾一字符即可)
            DASHBOARD.write_text(original[:-1], encoding='utf-8')
            done = self.run_tool('--check')
            self.assertEqual(done.returncode, 1)
            self.assertIn('再生成', done.stderr)
        finally:
            DASHBOARD.write_text(original, encoding='utf-8')


if __name__ == '__main__':
    unittest.main()
