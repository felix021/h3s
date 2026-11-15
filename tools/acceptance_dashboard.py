#!/usr/bin/env python3
"""验收记录全局面板生成器(路 PANEL/sim-panel,d1-acceptance §8 档案的全局视察面)。

扫描 docs/acceptance-samples/ 七目录+上级散置的全部记录 JSON(只读汇总,不改记录),
产出一页 markdown:docs/acceptance-dashboard.md。
  1. 总览:各目录 PASS/FAIL/BLOCKED/NOT_RUN 分布+PASS(条件性)列;
  2. 待授权/依赖项汇总:记录按类别聚组(刷机/BLE 联调/只读观测会话/前沿推进/观测手段)
     ——分组映射取自 r234 README「全局待授权项清单」与 d3 README「等待项地图」先例,
     逐组注明出处;组内每条记录的结果/证据实时取自记录文件,同一记录可挂多组(先例同此);
  3. 关键 FAIL/BLOCKED 单列(归因=记录自带 blocked_missing/未覆盖项,不手写);
  4. 记录索引:每条链到 JSON 文件路径。

解析与校验复用 acceptance_record(record_from_json/validate_record,不复制逻辑)。
输出确定性:不含生成时钟,「数据截至」=各记录 recorded_at 最大值——因此库内
docs/acceptance-dashboard.md 可字节级再生,tools/test_acceptance_dashboard.py
据此做过期校验(已接入 check_local --units):记录改动而面板未再生成即红。

CLI:
  acceptance_dashboard.py               # 生成/覆写 docs/acceptance-dashboard.md
  acceptance_dashboard.py --stdout      # 打印不落盘
  acceptance_dashboard.py --output FILE # 写到指定文件
  acceptance_dashboard.py --check       # 校验库内面板与记录一致(过期/缺失/记录非法=exit 1)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import acceptance_record as ar

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / 'docs' / 'acceptance-samples'
DASHBOARD = ROOT / 'docs' / 'acceptance-dashboard.md'

# 目录面板(顺序=总览表行序,当前七个);新增目录=在此加一行。
PANELS = (
    ('d2a', 'D2-a 按键输入响应:识别×动作覆盖(路 BB/sim-d2arecords)'),
    ('r234', 'R2 辅听/R3 独立 app/R4 命令接驳(路 CU/sim-r234records)'),
    ('d2b', 'D2-b 音频输入闭环:配置实证×注入预填(路 BI/sim-d2brecords)'),
    ('cot', '候选镜像联动用例(路 BT/sim-coimg,d1-acceptance §6.5)'),
    ('d3', 'D3 真机一致性:案头就绪底稿(路 BS/sim-d3prep)'),
    ('parity', '镜像间轨迹差分(路 A1/EQDIV,L2 系证据)'),
    ('d1', 'D1 稳定调度验收(路 PR3P 预填,等闸门②)'),
)
# 上级目录散置的两条真实示例(§8.1):逻辑上分属 d2a/r234 面板(两 README 已注明),
# 这里按物理位置单列一行,扫描口径=每个 JSON 属于它所在的目录。
ROOT_PANEL = ('(上级散置)', '上级目录示例:D2-a-REC-01(属 d2a)/R4-VM-01(属 r234),§8.1')

# 待授权/依赖分组:case_id 清单是策展映射(各组注明 README 出处),记录的结果/证据
# 实时读自记录文件;映射中的 id 在记录里不存在时生成器报错(防映射过期)。
AUTH_GROUPS = (
    ('刷机授权',
     '离线候选族(patch 6c7247a5/close 5a5f2ef7/slotram 906184b2/r4-keyhook 8e9a47d0)'
     '真机生效的共同前提;AGENTS 红线「No new device action is authorized」',
     ('R2-HEA-02', 'R2-HEA-03', 'R2-HEA-04', 'R2-HEA-05', 'R2-HEA-06',
      'R4-VM-02', 'R4-VM-03', 'R4-VM-05'),
     'r234 README「全局待授权项清单」第 1 行'),
    ('真机 BLE 联调授权',
     'R3 全链(壳→transport→GATT)与 R4 App 侧端到端;android-query 真机适配清单第 9/10 条',
     ('R2-HEA-05', 'R3-APP-01', 'R3-APP-02', 'R3-APP-03', 'R3-APP-04', 'R3-APP-05',
      'R4-VM-04', 'R4-VM-05', 'D3-EVT-01'),
     'r234 README 第 2 行+d3 README「等待项地图」BLE 联调行'),
    ('只读观测会话授权(FD07/FD05)',
     'D3 SFR/PRB 四条共同前提;工具就绪=read_rom.py --session-pack 会话打包 dry-run(路 SESS)',
     ('D3-PRB-01', 'D3-SFR-01', 'D3-SFR-02', 'D3-SFR-03'),
     'd3 README「等待项地图」第 1 行'),
    ('前沿推进(非授权类,模型件缺失)',
     '主仿真前沿/D1 事件循环/RCSP 相位与注入执行载体——模型件缺口,不是授权缺口',
     ('R2-HEA-06', 'R4-VM-05', 'D2-a-ACT-10',
      'D2-b-INJ-01', 'D2-b-INJ-02', 'D2-b-INJ-03', 'D2-b-INJ-04', 'D2-b-INJ-05',
      'D2-b-INJ-06', 'D2-b-SW-01', 'D2-b-SW-02',
      'R2-COT-02', 'R2-COT-03', 'R2-COT-04', 'R2-COT-05', 'R2-COT-06',
      'R4-COT-02', 'R4-COT-03', 'R4-COT-04', 'R4-COT-05'),
     'r234 README 第 3 行+d2a README(ACT-10 归 D1 事件循环)+d2b README「注入面共同依赖地图」'
     '前两行+cot README「其余按注入层级 L0-L3 挂前沿/D1/RCSP」'),
    ('真机观测手段(手段本身现不存在)',
     '事件日志面/音频校准手段——授权也解不了,须先造观测手段',
     ('D3-TIM-01', 'D3-AUD-01'),
     'd3 README「等待项地图」第 3/4 行'),
)

# NOT_RUN 聚合行的等待说明=指向各面板 README 的依赖地图(文字为指针非数据)。
PANEL_WAITING_NOTE = {
    'd2a': '等执行载体(§6.1 等价类预填);ACT-10 BLOCKED 归 D1 事件循环——d2a README',
    'd2b': '等 BF 前沿释放+AW 蓝图实施——d2b README「注入面共同依赖地图」',
    'cot': '按 L0-L3 前置链(COT-01 两条不依赖 D1 可立即跑)——cot README',
    'd3': '等授权或观测手段——d3 README「等待项地图」',
}


def _cell(text: object) -> str:
    return str(text).replace('|', '\\|').replace('\n', ' ')


def _short(text: str, limit: int = 100) -> str:
    flat = ' '.join(text.split())
    return flat if len(flat) <= limit else flat[:limit] + '…'


def _evidence(record: ar.Record) -> str:
    return ar._evidence_cell(record)


def scan_panels(samples: Path, panels=PANELS, root_panel=ROOT_PANEL):
    """逐目录扫描记录 JSON(解析+校验复用 acceptance_record)。

    返回 (entries, panel_of, paths, problems):
      entries={面板键: {case_id: Record}};panel_of={case_id: 面板键};
      paths={case_id: Path};problems={文件路径: 错误列表}。
    """
    entries: dict[str, dict[str, ar.Record]] = {}
    panel_of: dict[str, str] = {}
    paths: dict[str, Path] = {}
    problems: dict[str, list[str]] = {}
    for key, _label in panels + (root_panel,):
        directory = samples if key == root_panel[0] else samples / key
        bucket = entries.setdefault(key, {})
        if not directory.exists():
            continue
        for path in sorted(directory.glob('*.json')):
            try:
                data = json.loads(path.read_text(encoding='utf-8'))
            except (OSError, ValueError) as error:
                problems[str(path)] = [f'JSON 解析失败: {error}']
                continue
            record, errors = ar.record_from_json(data)
            errors = errors + ar.validate_record(record)
            if errors:
                problems[str(path)] = errors
                continue
            if record.case_id in paths:
                problems[str(path)] = [
                    f'用例 id {record.case_id} 与 {paths[record.case_id]} 重复']
                continue
            bucket[record.case_id] = record
            panel_of[record.case_id] = key
            paths[record.case_id] = path
    return entries, panel_of, paths, problems


def _distribution(records) -> dict[str, int]:
    counts = {value: 0 for value in ar.RESULTS}
    counts['PASS_conditional'] = 0
    for record in records:
        counts[record.result] += 1
        if record.result == 'PASS' and record.evidence_level == 'conditional':
            counts['PASS_conditional'] += 1
    return counts


def generate(samples: Path = SAMPLES, panels=PANELS, root_panel=ROOT_PANEL,
             groups=AUTH_GROUPS) -> tuple[str, dict]:
    """生成全局面板 markdown。记录非法/分组映射过期时抛 ValueError(不产出面板)。"""
    entries, panel_of, paths, problems = scan_panels(samples, panels, root_panel)
    if problems:
        detail = '; '.join(f'{path}: {"; ".join(errors)}' for path, errors in sorted(problems.items()))
        raise ValueError(f'存在非法记录,拒绝生成面板——{detail}')
    records = {case_id: record
               for bucket in entries.values()
               for case_id, record in bucket.items()}
    stale = sorted({case_id for _, _, ids, _ in groups for case_id in ids
                    if case_id not in records})
    if stale:
        raise ValueError(f'分组映射引用了不存在的用例 id(映射过期,请更新 AUTH_GROUPS): '
                         f'{", ".join(stale)}')

    all_records = list(records.values())
    data_as_of = max((r.recorded_at for r in all_records), default='—')
    panel_total = sum(len(entries[key]) for key, _ in panels)
    lines: list[str] = []
    lines += ['# 验收记录全局面板(七目录一页汇总,路 PANEL/sim-panel)', '',
              '数据=`docs/acceptance-samples/` 全部记录的只读汇总(不改记录;每条链接直达 JSON)。',
              '记录改动后须再生成:`python3 tools/acceptance_dashboard.py`;',
              '`check_local --units` 会校验本页与记录一致(test_acceptance_dashboard.py)。',
              f'数据截至(记录 recorded_at 最大值)={data_as_of} ｜ '
              f'共 {len(all_records)} 条(七目录 {panel_total}+上级散置 '
              f'{len(entries[root_panel[0]])})。', '',
              '## 1. 总览', '',
              '| 目录 | 内容 | PASS | 其中条件性 | FAIL | BLOCKED | NOT_RUN | 共计 |',
              '|---|---|---|---|---|---|---|---|']
    totals = {key: 0 for key in list(ar.RESULTS) + ['PASS_conditional', 'total']}
    for key, label in panels + (root_panel,):
        counts = _distribution(entries[key].values())
        row_total = sum(counts[value] for value in ar.RESULTS)
        for name in ar.RESULTS:
            totals[name] += counts[name]
        totals['PASS_conditional'] += counts['PASS_conditional']
        totals['total'] += row_total
        readme = f'acceptance-samples/{key}/README.md' if key != root_panel[0] else None
        first = f'[{key}]({readme})' if readme else key
        lines.append(f'| {first} | {_cell(label)} | {counts["PASS"]} '
                     f'| {counts["PASS_conditional"]} | {counts["FAIL"]} '
                     f'| {counts["BLOCKED"]} | {counts["NOT_RUN"]} | {row_total} |')
    lines.append(f'| **合计** |  | **{totals["PASS"]}** | **{totals["PASS_conditional"]}** '
                 f'| **{totals["FAIL"]}** | **{totals["BLOCKED"]}** '
                 f'| **{totals["NOT_RUN"]}** | **{totals["total"]}** |')
    lines += ['', 'BLOCKED=必选用例因模型缺失未过,不是弱化 PASS(§6.4);条件性=证据等级 '
              'conditional(断言在显式投影/fixture 下成立,§8)。', '',
              '## 2. 待授权/依赖项汇总', '',
              '分组映射取 r234 README「全局待授权项清单」与 d3 README「等待项地图」先例'
              '(逐组注明出处);组内结果实时读自记录——同一记录可挂多组(r234 先例同此),'
              'PASS 记录挂账=其真机半边等授权,不代表已生效。', '']
    grouped: set[str] = set()
    for index, (name, nature, ids, source) in enumerate(groups, start=1):
        grouped.update(ids)
        lines.append(f'### 2.{index} {name}({len(ids)} 条挂账)')
        lines += ['', f'性质:{nature} ｜ 映射出处:{source}', '',
                  '| 记录 | 目录 | 结果 | 证据等级 |', '|---|---|---|---|']
        for case_id in ids:
            record = records[case_id]
            rel = paths[case_id].relative_to(samples.parent).as_posix()
            lines.append(f'| [{case_id}]({rel}) | {panel_of[case_id]} | {record.result} '
                         f'| {_evidence(record)} |')
        lines.append('')
    waiting = [case_id for case_id in sorted(records)
               if records[case_id].result in ('BLOCKED', 'NOT_RUN')
               and case_id not in grouped]
    if waiting:
        lines += [f'未入组等待项 {len(waiting)} 条(依赖见各记录「未覆盖项」): '
                  + ', '.join(f'[{case_id}]'
                              f'({paths[case_id].relative_to(samples.parent).as_posix()})'
                              for case_id in waiting) + '.', '']
    lines += ['## 3. 关键 FAIL/BLOCKED(归因取自记录本身)', '']
    fails = [r for r in all_records if r.result == 'FAIL']
    lines.append(f'### FAIL({len(fails)} 条)')
    lines += ['', '| 记录 | 目录 | 归因(未覆盖项首条,截断) |', '|---|---|---|']
    for record in sorted(fails, key=lambda r: r.case_id):
        first = record.uncovered_items[0] if record.uncovered_items else '—'
        rel = paths[record.case_id].relative_to(samples.parent).as_posix()
        lines.append(f'| [{record.case_id}]({rel}) | {panel_of[record.case_id]} '
                     f'| {_cell(_short(first))} |')
    blocked = [r for r in all_records if r.result == 'BLOCKED']
    lines += ['', f'### BLOCKED({len(blocked)} 条,§6.4「没过」而非弱化 PASS)',
              '', '| 记录 | 目录 | 归因(blocked_missing,截断) |', '|---|---|---|']
    for record in sorted(blocked, key=lambda r: r.case_id):
        missing = record.blocked_missing
        text = missing[0] if isinstance(missing, list) else str(missing)
        rel = paths[record.case_id].relative_to(samples.parent).as_posix()
        lines.append(f'| [{record.case_id}]({rel}) | {panel_of[record.case_id]} '
                     f'| {_cell(_short(text))} |')
    not_run = [r for r in all_records if r.result == 'NOT_RUN']
    lines += ['', f'### NOT_RUN({len(not_run)} 条,按目录聚合;等待说明=面板 README 指针)',
              '', '| 目录 | NOT_RUN | 其中入组挂账 | 组外(依赖见记录「未覆盖项」) |', '|---|---|---|---|']
    for key, _label in panels + (root_panel,):
        panel_waiting = [r for r in entries[key].values() if r.result == 'NOT_RUN']
        if not panel_waiting:
            continue
        in_group = sum(1 for r in panel_waiting if r.case_id in grouped)
        note = _cell(PANEL_WAITING_NOTE.get(key, '见该面板 README'))
        lines.append(f'| {key} | {len(panel_waiting)} | {in_group} '
                     f'| {len(panel_waiting) - in_group} | {note} |')
    lines += ['', '## 4. 记录索引(逐条链接)', '',
              '| 用例 id | 目录 | 结果 | 证据等级 | 记录时间 |', '|---|---|---|---|---|']
    for key, _label in panels + (root_panel,):
        for case_id in sorted(entries[key]):
            record = entries[key][case_id]
            rel = paths[case_id].relative_to(samples.parent).as_posix()
            lines.append(f'| [{case_id}]({rel}) | {key} | {record.result} '
                         f'| {_evidence(record)} | {record.recorded_at} |')
    panel_links = ' '.join(f'[{key}](acceptance-samples/{key}/README.md)'
                           for key, _ in panels)
    lines += ['', '- 记录格式与校验规则:docs/d1-acceptance.md §8/§8.1;'
              f'各目录面板(含依赖地图):{panel_links}。',
              '- 本页由 tools/acceptance_dashboard.py 生成(只读汇总,不手改;数字全部来自记录文件)。']
    stats = {'total': len(all_records), 'panels': {key: len(entries[key])
                                                   for key, _ in panels + (root_panel,)},
             'totals': totals, 'grouped': len(grouped), 'unassigned_waiting': len(waiting)}
    return '\n'.join(lines) + '\n', stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--output', type=Path, default=DASHBOARD,
                        help='输出文件(默认 %s)' % DASHBOARD.relative_to(ROOT))
    parser.add_argument('--stdout', action='store_true', help='打印到 stdout,不落盘')
    parser.add_argument('--check', action='store_true',
                        help='校验库内面板与记录一致(过期/缺失/记录非法=exit 1)')
    args = parser.parse_args(argv)
    try:
        markdown, stats = generate()
    except ValueError as error:
        print(error, file=sys.stderr)
        return 1
    if args.check:
        if not DASHBOARD.exists():
            print(f'缺少 {DASHBOARD.relative_to(ROOT)}:请先运行 '
                  f'python3 tools/acceptance_dashboard.py 生成', file=sys.stderr)
            return 1
        committed = DASHBOARD.read_text(encoding='utf-8')
        if committed != markdown:
            print(f'{DASHBOARD.relative_to(ROOT)} 与记录不一致(记录已改动?):'
                  '请再生成 python3 tools/acceptance_dashboard.py 后提交', file=sys.stderr)
            return 1
        print(f'面板未过期:与 {stats["total"]} 条记录一致'
              f'(docs/acceptance-dashboard.md)')
        return 0
    if args.stdout:
        sys.stdout.write(markdown)
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(markdown, encoding='utf-8')
        print(f'已写入 {args.output}({len(markdown.splitlines())} 行)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
