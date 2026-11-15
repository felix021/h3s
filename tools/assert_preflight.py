#!/usr/bin/env python3
"""Offline assertion preflight: replay a scenario report against the check() assertions.

固化的探路预检器（路 CHK，2026-11-15）。背景：LPV 路重钉 probe 断言时烧了
4 轮全量 verify（每轮 ~35 分钟）才对齐——根因是探路报告与 check()/check_self()
断言分离，每次都要真仿真才知道断言值过没过。本工具把「报告 → 断言」闭环离线化：

    python3 tools/assert_preflight.py analysis/local-checks/boot-prefix.json
    python3 tools/assert_preflight.py analysis/local-checks/irq-entry.json \
        --firmware firmware/QCY_H3S_7018F_1T2_release_note_V1.1.8_011.ufw
    python3 tools/assert_preflight.py --json a.json b.json c.json

- 输入＝一份或多份 --report 产物 json（probe_boot / probe_irq_entry /
  analyze_image_trace），零仿真执行：只把报告喂给对应 check 断言面
  （import 调用 probe_boot.check_known_blockers / probe_irq_entry.check /
  analyze_image_trace.check_self 系列，断言逻辑不复制）。
- 输出＝逐断言结果清单（断言键=require 消息、ok/mismatch、源码位置；mismatch
  时附条件各子比较的期望值/实际值），整体退出码全过 0 / 有 mismatch 非 0；
  --json 出机器可读结果。
- 断言拦截方式：临时把各工具模块里 `from probe_pi32v2 import require` 绑定的
  require 换成记录版（不改任何工具源码，跑完恢复）。默认 continue 模式：某条
  require 失败后继续求值后续断言以列出全部 mismatch（后续代码结构崩了记为
  aborted）；--first-failure 恢复「首败即抛」的原始语义。
- probe_irq_entry.check 需要 firmware 文件（P11 公共段快照 SHA 互证用
  analyze_p11 读真件），默认路径与 probe_irq_entry.main() 相同，--firmware 可换。
  这不是仿真，只是读档。

用法（推进类改动预检流程）：改完 check() 断言字面值 → 先跑本工具对既有报告
预检（秒级）→ 全绿再上全量 verify。check_local.py --preflight 会对本仓标准
local-checks 报告集跑一遍（--allow-missing，报告缺的不算错）。
"""
import argparse
import ast
import json
import operator
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import probe_pi32v2  # noqa: E402

ROOT = probe_pi32v2.ROOT
ORIGINAL_REQUIRE = probe_pi32v2.require
DEFAULT_FIRMWARE = ROOT / 'firmware/QCY_H3S_7018F_1T2_release_note_V1.1.8_011.ufw'
CANDIDATE_LABELS = ('l1_slotram', 'r4_keyhook')
VALUE_LIMIT = 160  # 单值输出截断长度（避免大段 dump）

_OPS = {ast.Eq: ('==', operator.eq), ast.NotEq: ('!=', operator.ne),
        ast.Lt: ('<', operator.lt), ast.LtE: ('<=', operator.le),
        ast.Gt: ('>', operator.gt), ast.GtE: ('>=', operator.ge),
        ast.In: ('in', lambda a, b: a in b), ast.NotIn: ('not in', lambda a, b: a not in b),
        ast.Is: ('is', operator.is_), ast.IsNot: ('is not', operator.is_)}


def _source_of(node):
    try:
        return ast.unparse(node)
    except Exception:
        return '<unparsed>'


def _eval_node(node, frame):
    """在断言调用点的帧上下文里求值单个子表达式（check 内都是只读 report 的
    纯表达式；求值失败只记源码，不掩盖断言结论）。返回 {'value': 原值} 或
    {'error': 原因}。"""
    try:
        code = compile(ast.Expression(node), '<assert-preflight-leaf>', 'eval')
        return {'value': eval(code, frame.f_globals, dict(frame.f_locals))}
    except Exception as error:
        return {'error': f'{type(error).__name__}: {error}', 'source': _source_of(node)}


def _side_entry(side):
    """单侧的 JSON 输出形态：小值保原类型（机器可读），大值截断成文本。"""
    if 'value' in side:
        value = side['value']
        try:
            text = json.dumps(value, ensure_ascii=False)
        except (TypeError, ValueError):
            return {'value': repr(value)[:VALUE_LIMIT]}
        if len(text) <= VALUE_LIMIT:
            return {'value': value}
        return {'value': text[:VALUE_LIMIT] + f'…(+{len(text) - VALUE_LIMIT} chars)'}
    return {'error': side['error'], 'source': side.get('source')}


def _leaf_entries(condition, frame):
    """把条件表达式拆成叶子比较，逐边求值出期望/实际（best-effort，附 leaf ok）。"""
    leaves = []

    def split(node):
        if isinstance(node, ast.BoolOp):
            for child in node.values:
                split(child)
            return
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            operand = _eval_node(node.operand, frame)
            ok = None
            if 'value' in operand:
                ok = not bool(operand['value'])
            leaves.append({'op': 'not', 'ok': ok, 'operand': _side_entry(operand)})
            return
        if isinstance(node, ast.Compare):
            left = node.left
            for op, right in zip(node.ops, node.comparators):
                symbol, function = _OPS.get(type(op), (None, None))
                lhs, rhs = _eval_node(left, frame), _eval_node(right, frame)
                ok = None
                if function is not None and 'value' in lhs and 'value' in rhs:
                    ok = bool(function(lhs['value'], rhs['value']))
                leaves.append({'op': symbol or 'cmp', 'ok': ok,
                               'left': _side_entry(lhs), 'right': _side_entry(rhs)})
                left = right
            return
        operand = _eval_node(node, frame)
        ok = None
        if 'value' in operand:
            ok = bool(operand['value'])
        leaves.append({'op': 'truthy', 'ok': ok, 'operand': _side_entry(operand)})

    split(condition)
    return leaves


class _RequireCalls:
    """require(...) 调用点的 AST 索引（按文件缓存），供断言定位/条件拆分。"""

    def __init__(self):
        self._by_file = {}

    def _calls(self, filename):
        if filename not in self._by_file:
            try:
                tree = ast.parse(Path(filename).read_text())
            except (OSError, SyntaxError):
                self._by_file[filename] = None
                return None
            calls = [node for node in ast.walk(tree)
                     if isinstance(node, ast.Call) and
                     ((isinstance(node.func, ast.Name) and node.func.id == 'require') or
                      (isinstance(node.func, ast.Attribute) and node.func.attr == 'require'))]
            calls.sort(key=lambda node: node.lineno)
            self._by_file[filename] = calls
        return self._by_file[filename]

    def match(self, filename, lineno):
        """找帧当前行所在的 require 调用（多行调用按起止行覆盖匹配）。"""
        calls = self._calls(filename)
        if not calls:
            return None
        spanning = [c for c in calls if c.lineno <= lineno <= c.end_lineno]
        if spanning:
            return spanning[-1]
        started_before = [c for c in calls if c.lineno <= lineno]
        return started_before[-1] if started_before else None


_REQUIRE_CALLS = _RequireCalls()


class Recorder:
    """一次 check 调用的断言账本（被替换版 require 回写）。"""

    def __init__(self, first_failure=False):
        self.first_failure = first_failure
        self.assertions = []

    def require(self, condition, message):
        frame = sys._getframe(1)
        ok = bool(condition)
        entry = {'key': str(message), 'ok': ok,
                 'source': f"{Path(frame.f_code.co_filename).name}:{frame.f_lineno}"}
        if not ok:
            call = _REQUIRE_CALLS.match(frame.f_code.co_filename, frame.f_lineno)
            if call is not None and call.args:
                entry['condition'] = _source_of(call.args[0])
                entry['leaves'] = _leaf_entries(call.args[0], frame)
        self.assertions.append(entry)
        if not ok and self.first_failure:
            raise ValueError(message)  # 原始语义：首败即抛

    @property
    def status(self):
        return 'mismatch' if any(not entry['ok'] for entry in self.assertions) else 'pass'


class _Patch:
    """把所有 `from probe_pi32v2 import require` 的绑定换成记录版，退出时恢复。"""

    def __init__(self, require):
        self.require = require
        self.saved = []

    def __enter__(self):
        for module in list(sys.modules.values()):
            if module is not None and getattr(module, 'require', None) is ORIGINAL_REQUIRE:
                self.saved.append((module, module.require))
                module.require = self.require
        return self

    def __exit__(self, *exception):
        for module, original in self.saved:
            module.require = original
        return False


def _run_check(name, function, arguments, first_failure=False):
    """零仿真跑一个 check 面：继续模式吞掉 require 失败，结构性异常记 aborted。"""
    recorder = Recorder(first_failure=first_failure)
    result = {'name': name, 'status': 'pass', 'assertions': recorder.assertions, 'error': None}
    with _Patch(recorder.require):
        try:
            function(*arguments)
        except ValueError as error:  # --first-failure 模式或未被替换的 require
            result['error'] = f'ValueError: {error}'
        except Exception as error:  # continue 模式下后续代码依赖失败值而崩
            result['error'] = f'{type(error).__name__}: {error}'
    if result['error'] is not None:
        result['status'] = 'aborted'
    elif recorder.status != 'pass':
        result['status'] = 'mismatch'
    return result


def detect_kind(report):
    if 'pairs' in report:
        return 'analyze_image_trace'
    names = {scenario.get('name') for scenario in report.get('scenarios', [])}
    if 'irq127_request_strict' in names:
        return 'probe_irq_entry'
    if 'unmodified_upstream' in names:
        return 'probe_boot'
    return None


def _checks_for(kind, report, firmware):
    """按报告种类派生 (check 名, 函数, 参数) 列表；只 import 需要的工具模块。"""
    if kind == 'probe_boot':
        import probe_boot
        return [('probe_boot.check_known_blockers', probe_boot.check_known_blockers, (report,))]
    if kind == 'probe_irq_entry':
        import probe_irq_entry
        return [('probe_irq_entry.check', probe_irq_entry.check, (report, firmware))]
    if kind == 'analyze_image_trace':
        import analyze_image_trace
        scenario = report.get('scenario', 'crc_bankb')
        pairs = {pair.get('name') for pair in report.get('pairs', [])}
        if pairs == {'official_vs_tested_b79'}:
            if scenario == 'crc_bankb':
                return [('analyze_image_trace.check_self',
                         analyze_image_trace.check_self, (report,))]
            return [('analyze_image_trace.check_self_conditional',
                     analyze_image_trace.check_self_conditional, (report,))]
        candidates = sorted(name.rsplit('official_vs_', 1)[-1] for name in pairs
                            if name.startswith('official_vs_'))
        if len(candidates) == 1 and candidates[0] in CANDIDATE_LABELS:
            label = candidates[0]
            return [(f'analyze_image_trace.check_self_candidate[{label},{scenario}]',
                     analyze_image_trace.check_self_candidate, (report, label, scenario))]
        return [('analyze_image_trace.check_self', None, ())]
    return []


def preflight(paths, kind=None, firmware=DEFAULT_FIRMWARE, first_failure=False,
              allow_missing=False):
    """跑全部报告，返回结果 dict（含 exit_status：全过 0 / 有 mismatch 非 0）。"""
    reports = []
    for path in paths:
        entry = {'path': str(path), 'kind': None, 'status': 'error', 'checks': [], 'error': None}
        reports.append(entry)
        if not path.is_file():
            entry['status'] = 'skipped' if allow_missing else 'error'
            entry['error'] = 'report file not found' + (' (skipped, --allow-missing)' if allow_missing else '')
            continue
        try:
            report = json.loads(path.read_text())
        except ValueError as error:
            entry['error'] = f'invalid JSON: {error}'
            continue
        entry['kind'] = kind or detect_kind(report)
        if entry['kind'] is None:
            entry['error'] = ("cannot detect report kind (need scenarios[*].name like "
                              "'irq127_request_strict'/'unmodified_upstream' or a 'pairs' key); "
                              'pass --kind explicitly')
            continue
        if entry['kind'] == 'probe_irq_entry' and not firmware.is_file():
            entry['error'] = (f'firmware file not found: {firmware} (probe_irq_entry.check '
                              'reads it for the P11 snapshot cross-check; pass --firmware)')
            continue
        for name, function, arguments in _checks_for(entry['kind'], report, firmware):
            if function is None:
                entry['checks'].append({'name': name, 'status': 'error', 'assertions': [],
                                        'error': 'unsupported pair set for the self-check surface'})
                continue
            entry['checks'].append(_run_check(name, function, arguments, first_failure))
        statuses = {check['status'] for check in entry['checks']} or {'error'}
        entry['status'] = ('mismatch' if 'mismatch' in statuses else
                           'aborted' if 'aborted' in statuses else
                           'error' if 'error' in statuses else 'pass')
    summary = {'reports': len(reports),
               'checked': sum(1 for r in reports if r['status'] != 'skipped'),
               'failed': sum(1 for r in reports if r['status'] not in ('pass', 'skipped')),
               'assertions': sum(len(c['assertions']) for r in reports for c in r['checks']),
               'mismatch': sum(1 for r in reports for c in r['checks']
                               for a in c['assertions'] if not a['ok'])}
    vacuous = summary['checked'] == 0  # --allow-missing 全缺也必须红，不能空转放行
    if vacuous:
        summary['failed'] = max(summary['failed'], 1)
    return {'tool': 'assert_preflight', 'reports': reports, 'summary': summary,
            'exit_status': 0 if summary['failed'] == 0 else 1}


def _show_side(side):
    return side.get('value', side.get('error', '?'))


def _print_human(result):
    for report in result['reports']:
        line = f"report {report['path']}"
        if report['kind']:
            line += f"  [{report['kind']}]"
        print(f'{line}: {report["status"].upper()}')
        if report['error']:
            print(f"  error: {report['error']}")
        for check in report['checks']:
            suffix = f'  ({check["error"]})' if check['error'] else ''
            print(f'  check {check["name"]}: {check["status"].upper()}{suffix}')
            for index, assertion in enumerate(check['assertions'], 1):
                mark = 'ok  ' if assertion['ok'] else 'MISS'
                print(f'    [{index:3d}] {mark}  {assertion["key"]}  ({assertion["source"]})')
                if not assertion['ok']:
                    # 只展开可疑叶子（求值失败/为假），最多 6 条——通过叶子不必刷屏。
                    suspicious = [leaf for leaf in assertion.get('leaves', [])
                                  if leaf.get('ok') is not True][:6]
                    for leaf in suspicious:
                        if 'operand' in leaf:
                            value = leaf['operand'].get('value', leaf['operand'].get('error', '?'))
                            print(f'           {leaf["op"]}: {value}' +
                                  ('' if leaf.get('ok') else '  <-- leaf mismatch'))
                            continue
                        left, right = _show_side(leaf['left']), _show_side(leaf['right'])
                        print(f'           {left!s:>60} {leaf["op"]} {right}' +
                              ('' if leaf.get('ok') is None else
                               ('' if leaf['ok'] else '  <-- leaf mismatch')))
    summary = result['summary']
    if summary['checked'] == 0:
        print('warning: no report was actually checked (all missing?) — a vacuous pass proves nothing')
    print(f"summary: {summary['reports']} reports, {summary['checked']} checked, "
          f"{summary['assertions']} assertions, {summary['mismatch']} mismatched, "
          f"{summary['failed']} failed; exit {result['exit_status']}")


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('reports', nargs='+', type=Path,
                        help='probe_boot/probe_irq_entry/analyze_image_trace 的 --report 产物 json')
    parser.add_argument('--kind', choices=['probe_boot', 'probe_irq_entry', 'analyze_image_trace'],
                        help='强制指定报告种类（默认按报告内容自动识别）')
    parser.add_argument('--firmware', type=Path, default=DEFAULT_FIRMWARE,
                        help='probe_irq_entry.check 用的官方固件（默认与 probe_irq_entry 相同）')
    parser.add_argument('--first-failure', action='store_true',
                        help='恢复原始语义：首条失败 require 即抛出（默认继续求值后续断言）')
    parser.add_argument('--allow-missing', action='store_true',
                        help='缺失的报告文件记 skipped 不算失败')
    parser.add_argument('--json', action='store_true', help='机器可读 JSON 输出')
    args = parser.parse_args()
    result = preflight(args.reports, kind=args.kind, firmware=args.firmware,
                       first_failure=args.first_failure, allow_missing=args.allow_missing)
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        _print_human(result)
    sys.exit(result['exit_status'])


if __name__ == '__main__':
    main()
