#!/usr/bin/env python3
"""验收记录工具:d1-acceptance §8 九栏格式的机器可读落地(路 AX/sim-accrecord)。

九栏=固件身份/起始状态/输入与时间表/观察边界/断言/超时预算/模型假设/结果/未覆盖项;
证据等级(conditional=条件性通过 / device_supported=真机对照支持)另列,独立于结果。
每用例一个 JSON 文件,默认目录 analysis/local-checks/acceptance/(git 忽略,不进库);
docs/acceptance-samples/ 存两条真实内容示例(D2-a-REC-01 路 AJ / R4-VM-01 路 AB)。

用例 id:路线-分组-覆盖面-序号,如 D2-a-REC-01(识别覆盖)/D2-b-SCO-01(通话场景)/
D1-TC-01/R4-VM-01;正则 ^[A-Z]\\d{1,2}(-[a-z])?(-[A-Z][A-Z0-9]{1,5})?-\\d{2}$。

校验规则(§8 的机器可读版,validate_record):
  - 九栏非空;「未覆盖项」允许空但必须显式 null(空列表 [] 拒绝——想留空没留干净)。
  - result ∈ {PASS,FAIL,BLOCKED,NOT_RUN}。
  - BLOCKED 必须附 blocked_missing(缺失依赖说明;§6.4:BLOCKED 与 FAIL 都是「没过」,
    区别只是归因——模型缺件 vs 行为错误;非 BLOCKED 记录不许带该字段)。
  - evidence_level=conditional 必须附 evidence_conditions(显式条件列表);
    =device_supported 必须附 evidence_refs(§8:引用具体观测会话/证据文件)。
  - PASS+conditional:evidence_conditions 逐条必须出现在「模型假设」栏(交叉校验,
    防「通过条件」与「声明假设」两套口径)。

CLI:
  acceptance_record.py --add record.json      # 或 - 读 stdin;校验通过才落盘
  acceptance_record.py --summary [--dir DIR]  # markdown 汇总表(--output 可写文件)
  acceptance_record.py --validate [DIR ...]   # 校验目录内全部记录(默认验收目录)
"""
from __future__ import annotations

import argparse
import dataclasses
import datetime
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DIR = ROOT / 'analysis' / 'local-checks' / 'acceptance'
SAMPLES_DIR = ROOT / 'docs' / 'acceptance-samples'

RESULTS = ('PASS', 'FAIL', 'BLOCKED', 'NOT_RUN')
EVIDENCE_LEVELS = ('none', 'conditional', 'device_supported')
CASE_ID_RE = re.compile(r'^[A-Z]\d{1,2}(?:-[a-z])?(?:-[A-Z][A-Z0-9]{1,5})?-\d{2}$')
TIMESTAMP_FORMAT = '%Y-%m-%dT%H:%M:%S'

# 九栏(§8 顺序)+证据等级/条件/引用+BLOCKED 依赖+时间戳。中文列名用于报错可读性。
COLUMNS = ('firmware_identity', 'initial_state', 'inputs_schedule',
           'observation_boundary', 'assertions', 'timeout_budget',
           'model_assumptions', 'result', 'uncovered_items')
COLUMN_LABELS = {'firmware_identity': '固件身份', 'initial_state': '起始状态',
                 'inputs_schedule': '输入与时间表', 'observation_boundary': '观察边界',
                 'assertions': '断言', 'timeout_budget': '超时预算',
                 'model_assumptions': '模型假设', 'result': '结果',
                 'uncovered_items': '未覆盖项'}
LIST_FIELDS = ('assertions', 'model_assumptions', 'evidence_conditions', 'evidence_refs')


def now_timestamp() -> str:
    return datetime.datetime.now().strftime(TIMESTAMP_FORMAT)


@dataclasses.dataclass
class Record:
    """一条验收记录:九栏 + 证据等级(另列)+ 用例 id + 记录时间戳。"""
    case_id: str = ''
    firmware_identity: str = ''
    initial_state: str = ''
    inputs_schedule: str = ''
    observation_boundary: str = ''
    assertions: list = dataclasses.field(default_factory=list)
    timeout_budget: str = ''
    model_assumptions: list = dataclasses.field(default_factory=list)
    result: str = ''
    uncovered_items: object = None  # None 或非空 list[str]
    evidence_level: str = 'none'
    evidence_conditions: list = dataclasses.field(default_factory=list)
    evidence_refs: list = dataclasses.field(default_factory=list)
    blocked_missing: object = None  # 仅 BLOCKED:str 或非空 list[str]
    recorded_at: str = ''  # %Y-%m-%dT%H:%M:%S,--add 时缺省补当前时间

    def to_json(self) -> dict:
        return dataclasses.asdict(self)


def _nonempty_strings(value: object) -> bool:
    return (isinstance(value, list) and value
            and all(isinstance(item, str) and item.strip() for item in value))


def record_from_json(data: object) -> tuple[Record, list[str]]:
    """dict → Record。只做结构转换(单字符串列表字段升格);缺栏/未知栏/类型错报错。"""
    errors: list[str] = []
    if not isinstance(data, dict):
        return Record(), [f'记录必须是 JSON 对象,得到 {type(data).__name__}']
    known = {field.name for field in dataclasses.fields(Record)}
    for key in data:
        if key not in known:
            errors.append(f'未知字段 "{key}"(已知字段: {", ".join(sorted(known))})')
    kwargs = {}
    for field in dataclasses.fields(Record):
        value = data.get(field.name)
        if field.name in LIST_FIELDS and isinstance(value, str):
            value = [value]  # 单条字符串友好升格为列表
        kwargs[field.name] = value
    return Record(**kwargs), errors


def validate_record(record: Record) -> list[str]:
    """§8 校验规则;返回错误列表(空=合法)。"""
    errors: list[str] = []
    if not isinstance(record.case_id, str) or not CASE_ID_RE.match(record.case_id):
        errors.append(f'用例 id "{record.case_id}" 不合编号规则(如 D2-a-REC-01/D2-b-SCO-01/D1-TC-01)')
    # 九栏非空(未覆盖项单独处理:允许显式 None)
    for name in ('firmware_identity', 'initial_state', 'inputs_schedule',
                 'observation_boundary', 'timeout_budget'):
        value = getattr(record, name)
        if not isinstance(value, str) or not value.strip():
            errors.append(f'「{COLUMN_LABELS[name]}」({name}) 不能为空')
    for name in ('assertions', 'model_assumptions'):
        if not _nonempty_strings(getattr(record, name)):
            errors.append(f'「{COLUMN_LABELS[name]}」({name}) 必须是非空字符串列表')
    if record.result not in RESULTS:
        errors.append(f'「结果」(result) 必须是 {"/".join(RESULTS)},得到 {record.result!r}')
    if record.uncovered_items is None:
        pass  # 显式空:允许(§8「未覆盖项……不许留空」以 None 显式声明,汇总显示 —)
    elif (isinstance(record.uncovered_items, list) and not record.uncovered_items):
        errors.append('「未覆盖项」为空时必须显式 null,不许 []')
    elif not _nonempty_strings(record.uncovered_items):
        errors.append('「未覆盖项」(uncovered_items) 必须是 null 或非空字符串列表')
    # 证据等级(另列,独立于结果)
    if record.evidence_level not in EVIDENCE_LEVELS:
        errors.append(f'证据等级 (evidence_level) 必须是 {"/".join(EVIDENCE_LEVELS)},'
                      f'得到 {record.evidence_level!r}')
    elif record.evidence_level == 'conditional':
        if not _nonempty_strings(record.evidence_conditions):
            errors.append('证据等级=条件性通过(conditional)必须附显式条件列表 '
                          '(evidence_conditions,非空字符串列表)')
    elif record.evidence_level == 'device_supported':
        if not _nonempty_strings(record.evidence_refs):
            errors.append('证据等级=真机对照支持(device_supported)必须附观测引用列表 '
                          '(evidence_refs:具体观测会话/证据文件)')
    # PASS+条件性:条件逐条出现在「模型假设」栏(交叉校验)
    if (record.result == 'PASS' and record.evidence_level == 'conditional'
            and _nonempty_strings(record.evidence_conditions)):
        missing = [cond for cond in record.evidence_conditions
                   if cond not in (record.model_assumptions or [])]
        if missing:
            errors.append('PASS+条件性通过的条件未在「模型假设」栏声明(交叉校验失败): '
                          + '; '.join(repr(cond) for cond in missing))
    # BLOCKED 语义(§6.4:BLOCKED 与 FAIL 都是「没过」,须附缺失依赖说明)
    if record.result == 'BLOCKED':
        value = record.blocked_missing
        ok = isinstance(value, str) and value.strip()
        if not ok:
            ok = _nonempty_strings(value)
        if not ok:
            errors.append('结果=BLOCKED 必须附缺失依赖说明 (blocked_missing:非空字符串或列表,'
                          '指明缺的模型件/观测手段)')
    elif record.blocked_missing is not None:
        errors.append('blocked_missing 仅允许 BLOCKED 记录携带(非 BLOCKED 的缺口写进「未覆盖项」)')
    if record.recorded_at:
        try:
            datetime.datetime.strptime(record.recorded_at, TIMESTAMP_FORMAT)
        except ValueError:
            errors.append(f'记录时间戳 "{record.recorded_at}" 不合格式 {TIMESTAMP_FORMAT}')
    else:
        errors.append('记录时间戳 (recorded_at) 不能为空(格式 %Y-%m-%dT%H:%M:%S)')
    return errors


def save_record(record: Record, directory: Path) -> Path:
    """每用例一文件:<case_id>.json;字段顺序=dataclass 声明序(九栏在前)。"""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f'{record.case_id}.json'
    path.write_text(json.dumps(record.to_json(), ensure_ascii=False, indent=2) + '\n',
                    encoding='utf-8')
    return path


def load_records(*directories: Path) -> tuple[dict[str, Record], dict[str, list[str]]]:
    """加载并合并多个记录目录(同 case_id 后读覆盖先读)。返回 (记录表, 逐文件错误表)。"""
    records: dict[str, Record] = {}
    problems: dict[str, list[str]] = {}
    for directory in directories:
        if not directory.exists():
            continue
        for path in sorted(directory.glob('*.json')):
            try:
                data = json.loads(path.read_text(encoding='utf-8'))
            except (OSError, ValueError) as error:
                problems[str(path)] = [f'JSON 解析失败: {error}']
                continue
            record, errors = record_from_json(data)
            errors = errors + validate_record(record)
            if errors:
                problems[str(path)] = errors
                continue
            records[record.case_id] = record
    return records, problems


def _cell(text: object) -> str:
    return str(text).replace('|', '\\|').replace('\n', ' ')


def _evidence_cell(record: Record) -> str:
    if record.evidence_level == 'conditional':
        return f'conditional({len(record.evidence_conditions)})'
    if record.evidence_level == 'device_supported':
        return f'device_supported({len(record.evidence_refs)})'
    return 'none'


def summarize(records: dict[str, Record], source_note: str = '') -> str:
    """导出 markdown 汇总表(结果四值分开计数;BLOCKED 不计入通过)。"""
    lines = ['# 验收记录汇总(§8 九栏格式)',
             '', source_note or f'共 {len(records)} 条(详细九栏见各 JSON):', '',
             '| 用例 id | 结果 | 证据等级 | 断言引用 | 模型假设 | 未覆盖项 | 记录时间 |',
             '|---|---|---|---|---|---|---|']
    for case_id in sorted(records):
        record = records[case_id]
        uncovered = '—' if record.uncovered_items is None else len(record.uncovered_items)
        assertions = ';<br>'.join(_cell(item) for item in record.assertions)
        lines.append(f'| {_cell(case_id)} | {record.result} | {_evidence_cell(record)} '
                     f'| {assertions} | {len(record.model_assumptions)} '
                     f'| {uncovered} | {record.recorded_at} |')
    counts = {value: sum(1 for r in records.values() if r.result == value)
              for value in RESULTS}
    lines.append('')
    lines.append('结果统计: ' + ' '.join(f'{key}={counts[key]}' for key in RESULTS)
                 + f'(共 {len(records)} 条;BLOCKED=必选用例因模型缺失未过,不是弱化 PASS,§6.4)')
    return '\n'.join(lines) + '\n'


def _read_input(spec: str) -> object:
    if spec == '-':
        return json.loads(sys.stdin.read())
    return json.loads(Path(spec).read_text(encoding='utf-8'))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--add', metavar='JSON',
                        help='从 JSON 文件(- 为 stdin)建记录:校验通过才写入记录目录')
    parser.add_argument('--summary', action='store_true',
                        help='加载记录目录并导出 markdown 汇总表')
    parser.add_argument('--validate', nargs='*', metavar='DIR',
                        help='校验目录内全部记录(不指定目录=默认验收目录;目录不存在=零记录通过)')
    parser.add_argument('--dir', action='append', type=Path, default=[],
                        help='记录目录(可重复;--add 用最后一个,--summary 合并全部;默认 %s)'
                             % DEFAULT_DIR)
    parser.add_argument('--output', type=Path, help='--summary 写入文件(默认打印 stdout)')
    args = parser.parse_args(argv)
    if not (args.add or args.summary or args.validate is not None):
        parser.error('需要 --add / --summary / --validate 之一')
    directories = args.dir or [DEFAULT_DIR]
    if args.add is not None:
        record, errors = record_from_json(_read_input(args.add))
        if not record.recorded_at:
            record.recorded_at = now_timestamp()
        errors = errors + validate_record(record)
        if errors:
            print(f'记录无效,拒绝写入(用例 id {record.case_id!r}):', file=sys.stderr)
            for error in errors:
                print(f'  - {error}', file=sys.stderr)
            return 1
        path = save_record(record, directories[-1])
        print(f'已写入 {path}(结果 {record.result},证据等级 {_evidence_cell(record)})')
    if args.summary:
        records, problems = load_records(*directories)
        markdown = summarize(records, '目录: ' + ', '.join(str(d) for d in directories))
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(markdown, encoding='utf-8')
            print(f'已写入 {args.output}')
        else:
            sys.stdout.write(markdown)
        for path, errors in sorted(problems.items()):
            print(f'警告: {path}: {"; ".join(errors)}', file=sys.stderr)
    if args.validate is not None:
        targets = args.validate or [str(d) for d in directories]
        records, problems = load_records(*(Path(t) for t in targets))
        for path, errors in sorted(problems.items()):
            print(f'无效: {path}', file=sys.stderr)
            for error in errors:
                print(f'  - {error}', file=sys.stderr)
        print(f'校验 {len(records)} 条记录: '
              + ('全部合法' if not problems else f'{len(problems)} 个文件无效'))
        return 1 if problems else 0
    return 0


if __name__ == '__main__':
    sys.exit(main())
