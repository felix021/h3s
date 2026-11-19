#!/usr/bin/env python3
"""前沿推进迭代循环 runner(路 CKPT/sim-ckpt):「放行新 SFR → 推进前沿」的
秒-分钟级快照续跑编排。

解决什么:条件前沿场景(p11_heap_experimental_config)一次从 0 全量重放
≈41.8M 模型步(纯 Python ≈20+ 分钟),而一条 route 的验证臂(负例复现/主臂
推进/fixture 敏感性/幂等)此前每臂都从 0 跑。前缀不变性(旧停点=新放行寄存
器的首次访问,之前的世界逐字节同)使「旧停点边界快照 → 开新开关 → resume」
≡ 从 0 跑——本工具把该循环编排成一次全量(捕获轮)+ 秒级臂位。

循环形状(每条 route,docs/snapshot-resume.md §续跑迭代):

  1. reference --off <开关>...        从 0 跑参照臂(负例参照/主臂参照),
      [--capture-dirty PATH]          自然停点报告+循环尝试数 K(脏停点快照
                                      只作诊断样本:世界停在迭代中途,本工具
                                      不用它续跑);
  2. base --budget N --off <开关>...  从 0 跑到 budget 停点(干净循环边界)
                                      捕获快照 P(一代码态一次,≈20 分钟);
  3. cut K                            从 P 重放 K-1-P.attempts 次循环捕获 Q
                                      ——Q 落在产生停点的迭代之前,续跑等价
                                      于从 0 跑到该边界(秒-分钟级);
  4. arm NAME [--off/--set ...]       从 Q 续跑各臂(负例复现/主臂推进/
      [--ack-enable-slot SLOT]...     fixture 敏感性),跨开关恢复按显式
      [--ack-refixture-slot SLOT]...  resume_ack 放行(见 world_snapshot);
      [--reference PATH] [--budget N] 与参照报告逐键比较(除 snapshot_* 键)。

K 的口径=循环尝试数(含停泊/交付迭代),不是 completed_steps——cut 按
attempts-1 对齐,这是 codex 参谋(gpt-6-astra,2026-11-17)的核心修正:
step-Blocked 停点的世界停在迭代中途(select 已消耗/trace 已追加半行),
直接续跑会先挑错核+重复半行,干净边界快照才是等价续跑的正确起点。

诚实边界:resume_ack 只放宽「开关/指纹相等」判定;结构闸/manifest/既有槽位
静态守卫照旧,ack 的前缀中性判断是操作者的(记录进 lineage 与报告)。跨代码
ack(prior_fingerprint)恢复的结果条件通过,合并前一次全量从 0 重放
(check_local 全量层+adapter 基线重取)是红线收口。快照文件含 RAM 镜像,
只落 analysis/(gitignored),evidence/ 只进摘要行。

用法示例(CON0 试点,jl_pled_con0_initial_fixture 关/开两世界):
  python3 tools/frontier_iteration.py --rom <ROM> --route con0-pilot \
      reference --off jl_pled_con0_initial_fixture --capture-dirty off-frontier
  python3 tools/frontier_iteration.py --rom <ROM> --route con0-pilot \
      reference --report on-reference
  python3 tools/frontier_iteration.py --rom <ROM> --route con0-pilot \
      base --budget 41000000 --off jl_pled_con0_initial_fixture
  python3 tools/frontier_iteration.py --rom <ROM> --route con0-pilot cut <K>
  python3 tools/frontier_iteration.py --rom <ROM> --route con0-pilot \
      arm off-reproduce --off jl_pled_con0_initial_fixture \
      --reference <off 参照报告> --evidence
  python3 tools/frontier_iteration.py --rom <ROM> --route con0-pilot \
      arm on-main --ack-enable-slot jl_pled_con0 \
      --reason 'CON0 首访问=旧停点,前缀零触达' \
      --reference <on 参照报告> --evidence
"""
import argparse
import json
import sys
import time
from pathlib import Path

from probe_pi32v2 import ROOT, require, upstream
from probe_boot import application
from probe_irq_entry import package_header, flash_image
import probe_irq_entry
from frontier_registry import PROBE_SCENARIO_NAMES, scenario_flags

FIRMWARE = ROOT / 'firmware/QCY_H3S_7018F_1T2_release_note_V1.1.8_011.ufw'
SCENARIO = 'p11_heap_experimental'
SCENARIO_ROW = PROBE_SCENARIO_NAMES[SCENARIO]        # p11_heap_experimental_config
WORK_DEFAULT = ROOT / 'analysis/frontier-iterations'
EVIDENCE = ROOT / 'evidence/frontier-iteration.json'


def parse_value(text):
    """--set SWITCH=VALUE 的值解析(int/true/false/0x 前缀;其余按原样字符串)。"""
    if text.lower() in ('true', 'false'):
        return text.lower() == 'true'
    try:
        return int(text, 0)
    except ValueError:
        return text


def snapshot_boundary_context(snapshot_path, scenario_name):
    """从快照头读 capture_context(不经双闸——跨代码续跑的指纹差由
    resume_ack 显式承认,不能在取 context 时就被闸拒掉)。同时钉死
    干净边界契约:源快照必须是 budget 停点(脏停点世界停在迭代中途——
    select 已消耗/trace 半行已追加/check_fetch 已计数,逐字节等价的臂位
    只能建立在干净循环边界上,见 docs/snapshot-resume.md)。"""
    import struct
    data = Path(snapshot_path).read_bytes()
    require(data[:8] == b'H3S-SNAP', 'not an H3S world snapshot')
    offset = 12  # magic + format version
    (length,) = struct.unpack_from('<I', data, offset)
    header = json.loads(data[offset + 4:offset + 4 + length])
    context = header.get('capture_context') or {}
    completed = context.get('completed_steps')
    require(isinstance(completed, int) and completed >= 0,
            f'{snapshot_path}: 快照头缺 capture_context.completed_steps')
    require((context.get('stop') or {}).get('kind') == 'instruction_budget',
            f'{snapshot_path}: 不是 budget 停点快照(脏停点=迭代中途世界,'
            '臂位续跑会重复 trace 半行/重挑选核——用 cut 在失败迭代前取干净边界)')
    return {'scenario': scenario_name, 'completed_steps': completed}


def merge_switch_overrides(base_off, base_set, arm_off, arm_set):
    """--off/--set 列表合并,同名开关 arm 覆盖 base(arm 的 --off/--set 优先,
    base 的 --set 不再静默压掉 arm 的 --off);不同名叠加。"""
    arm_off_names = set(arm_off)
    arm_set_names = {item.partition('=')[0] for item in arm_set}
    off = [name for name in base_off
           if name not in arm_off_names and name not in arm_set_names] + list(arm_off)
    sets = [item for item in base_set
            if item.partition('=')[0] not in arm_off_names
            and item.partition('=')[0] not in arm_set_names] + list(arm_set)
    return off, sets


class Runner:
    def __init__(self, args):
        self.args = args
        self.work = Path(args.work_root) / args.route
        self.work.mkdir(parents=True, exist_ok=True)
        self.lineage_path = self.work / 'lineage.json'
        self.lineage = json.loads(self.lineage_path.read_text()) \
            if self.lineage_path.exists() else []
        self.rom = Path(args.rom).read_bytes()
        self.app = application(args.firmware)
        self.header = package_header(args.firmware)
        self.image = flash_image(args.firmware)
        self.module = upstream()
        from world_snapshot import code_fingerprint
        self.fingerprint = code_fingerprint()

    # ------------------------------------------------------------------ 公共面
    def flags(self, off=(), sets=()):
        """条件栈全量 flag dict,叠加 --off(置 None/False)与 --set 覆盖。"""
        flags = scenario_flags(SCENARIO, probe_irq_entry)
        for name in off:
            require(name in flags, f'--off 开关 {name!r} 不在 {SCENARIO} 场景栈内')
            flags[name] = None if not isinstance(flags[name], bool) else False
        for item in sets:
            name, _, value = item.partition('=')
            require(name in flags, f'--set 开关 {name!r} 不在 {SCENARIO} 场景栈内')
            flags[name] = parse_value(value) if value else True
        return flags

    def run_case(self, flags, snapshot, name=SCENARIO_ROW):
        from probe_irq_entry import irq_case
        started = time.time()
        row = irq_case(self.module, self.app, self.header, self.rom, name,
                       **flags, flash_image_bytes=self.image, snapshot=snapshot)
        row['_wall_seconds'] = round(time.time() - started, 1)
        return row

    def save_report(self, row, stem):
        path = self.work / f'{stem}.json'
        path.write_text(json.dumps(row, indent=2) + '\n')
        return path

    def append_lineage(self, entry):
        """并发安全追加(并行跑四臂时各进程共享 lineage.json):独占锁内
        重读-追加-写回,后完成者不再覆盖先完成者的条目。"""
        import fcntl
        entry['at'] = time.strftime('%H:%M:%S')
        with open(self.lineage_path, 'a+') as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                handle.seek(0)
                existing = json.loads(handle.read() or '[]')
                existing.append(entry)
                handle.seek(0)
                handle.truncate()
                handle.write(json.dumps(existing, indent=2) + '\n')
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)
        self.lineage.append(entry)

    def clean_source(self, name=None):
        """lineage 里的干净边界快照(base/cut 条目)。name 给定(快照文件 stem)
        时选那一条(如从早期 Q 而非最新 cut 续跑);默认最新一条。"""
        matches = [entry for entry in self.lineage if entry.get('kind') in ('base', 'cut')]
        if not matches:
            raise SystemExit('lineage 里没有 base/cut 快照;先跑 base')
        if name is None:
            return matches[-1]
        for entry in matches:
            if Path(entry['snapshot']).stem == name or entry['kind'] == name:
                return entry
        raise SystemExit(f'lineage 里没有名为 {name!r} 的 base/cut 快照'
                         f'(现有: {[Path(e["snapshot"]).stem for e in matches]})')

    def check_freshness(self, entry, ack_allowed):
        """跨代码续跑必须显式承认:lineage 指纹≠当前指纹时要求 --ack-prior
        + --reason(P1:reason 是操作者的前缀中性判断,禁止默认值伪造)。"""
        if entry.get('code_fingerprint') == self.fingerprint:
            return None
        require(ack_allowed,
                'lineage 快照是旧代码态捕获的(指纹不符);跨代码续跑需 '
                '--ack-prior-fingerprint <快照指纹> --reason <前缀中性依据>')
        require(ack_allowed == entry['code_fingerprint'],
                '--ack-prior-fingerprint 与 lineage 记录的快照指纹不符')
        require(isinstance(self.args.reason, str) and self.args.reason.strip(),
                '跨代码续跑必须给 --reason(前缀中性判断入档,不给默认值)')
        return {'prior_fingerprint': entry['code_fingerprint'],
                'reason': self.args.reason}

    # ------------------------------------------------------------------ 命令
    def cmd_reference(self):
        """从 0 全量跑一臂:参照报告(+可选脏停点诊断快照,取 K)。"""
        flags = self.flags(self.args.off, self.args.set)
        snapshot = None
        if self.args.budget is not None:
            snapshot = {'budget': self.args.budget}
        if self.args.capture_dirty:
            snapshot = (snapshot or {}) | {'capture': str(self.work / f'{self.args.capture_dirty}.bin')}
        row = self.run_case(flags, snapshot)
        path = self.save_report(row, self.args.report or 'reference')
        entry = {'kind': 'reference', 'report': str(path),
                 'off': list(self.args.off), 'set': list(self.args.set),
                 'stop': row['stop'], 'completed_steps': row['completed_steps'],
                 'stop_pc': row['stop_pc'], 'code_fingerprint': self.fingerprint}
        if self.args.capture_dirty:
            entry['dirty_snapshot'] = snapshot['capture']
            entry['loop_attempts'] = row['snapshot_capture']['loop_attempts']
            entry['snapshot_sha256'] = row['snapshot_capture']['sha256']
        self.append_lineage(entry)
        print(json.dumps({'report': str(path), 'stop': row['stop'],
                          'completed_steps': row['completed_steps'],
                          'loop_attempts': entry.get('loop_attempts'),
                          'wall_seconds': row['_wall_seconds']}, indent=2))

    def cmd_base(self):
        """从 0 跑到 budget 停点捕获 P(干净循环边界;一代码态一次)。"""
        flags = self.flags(self.args.off, self.args.set)
        stem = self.args.name or 'base'
        snap = self.work / f'{stem}.bin'
        row = self.run_case(flags, {'budget': self.args.budget, 'capture': str(snap)})
        require(row['stop'] == {'kind': 'instruction_budget'},
                f'base 捕获必须停在 instruction_budget(实际 {row["stop"]})——'
                'budget 要小于该 flag 集的自然停点')
        path = self.save_report(row, stem)
        self.append_lineage({'kind': 'base', 'snapshot': str(snap), 'report': str(path),
                             'budget': self.args.budget,
                             'attempts': row['snapshot_capture']['loop_attempts'],
                             'off': list(self.args.off), 'set': list(self.args.set),
                             'code_fingerprint': self.fingerprint,
                             'snapshot_sha256': row['snapshot_capture']['sha256']})
        print(json.dumps({'snapshot': str(snap),
                          'attempts': row['snapshot_capture']['loop_attempts'],
                          'completed_steps': row['completed_steps'],
                          'wall_seconds': row['_wall_seconds']}, indent=2))

    def cmd_cut(self):
        """从最新 base/cut 快照重放 K-1 次循环捕获 Q(失败迭代前的干净边界)。
        默认继承源快照的 flag 集;--off/--set 覆盖(如从 OFF 界快照切 ON 界
        Q_on:开关在边界处启用,前缀零触达=同一世界)。"""
        source = self.clean_source(self.args.from_snapshot)
        fingerprint_ack = self.check_freshness(source, self.args.ack_prior_fingerprint)
        attempts_total = self.args.attempts
        require(attempts_total - 1 > source['attempts'],
                f'K={attempts_total} 必须大于源快照尝试数 {source["attempts"]}+1')
        budget = attempts_total - 1 - source['attempts']
        off, sets = merge_switch_overrides(source.get('off', ()), source.get('set', ()),
                                           self.args.off, self.args.set)
        flags = self.flags(off, sets)
        snap = self.work / (self.args.name or f'cut-{attempts_total - 1}')
        if snap.suffix != '.bin':
            snap = snap.with_suffix('.bin')
        restore = {'restore': source['snapshot'], 'budget': budget, 'capture': str(snap),
                   'expected_context': self.expected_context(source)}
        if self.args.ack_enable_slot or self.args.ack_refixture_slot or fingerprint_ack:
            require(isinstance(self.args.reason, str) and self.args.reason.strip(),
                    'ack 的 cut 必须给 --reason(前缀中性判断入档,不给默认值)')
            restore['resume_ack'] = {
                **(fingerprint_ack or {}),
                'enable_slots': list(self.args.ack_enable_slot),
                'refixture_slots': list(self.args.ack_refixture_slot),
                'reason': self.args.reason}
        row = self.run_case(flags, restore)
        require(row['stop'] == {'kind': 'instruction_budget'},
                f'cut 必须停在 instruction_budget(实际 {row["stop"]})')
        path = self.save_report(row, snap.stem)
        self.append_lineage({'kind': 'cut', 'snapshot': str(snap), 'report': str(path),
                             'from': source['snapshot'], 'attempts': attempts_total - 1,
                             'off': off, 'set': sets,
                             'code_fingerprint': self.fingerprint,
                             'snapshot_sha256': row['snapshot_capture']['sha256'],
                             **({'fingerprint_acknowledged':
                                 row['snapshot_restore'].get('fingerprint_acknowledged')}
                                if fingerprint_ack else {})})
        print(json.dumps({'snapshot': str(snap), 'attempts': attempts_total - 1,
                          'resume_budget': budget,
                          'completed_steps': row['completed_steps'],
                          'wall_seconds': row['_wall_seconds']}, indent=2))

    def expected_context(self, entry):
        return snapshot_boundary_context(entry['snapshot'], SCENARIO_ROW)

    def cmd_arm(self):
        """从最新 cut 快照续跑一臂;--reference 给定时逐键比较。"""
        source = self.clean_source(self.args.from_snapshot)
        fingerprint_ack = self.check_freshness(source, self.args.ack_prior_fingerprint)
        off, sets = merge_switch_overrides(source.get('off', ()), source.get('set', ()),
                                           self.args.off, self.args.set)
        flags = self.flags(off, sets)
        if self.args.ack_enable_slot or self.args.ack_refixture_slot:
            require(isinstance(self.args.reason, str) and self.args.reason.strip(),
                    'ack 臂必须给 --reason(前缀中性判断入档,不给默认值)')
        resume_ack = {'enable_slots': list(self.args.ack_enable_slot),
                      'refixture_slots': list(self.args.ack_refixture_slot),
                      'reason': self.args.reason}
        if fingerprint_ack:
            resume_ack.update(fingerprint_ack)
        snapshot = {'restore': source['snapshot'],
                    'expected_context': self.expected_context(source),
                    'resume_ack': resume_ack}
        if self.args.budget is not None:
            snapshot['budget'] = self.args.budget
        if self.args.capture_as:
            snapshot['capture'] = str(self.work / f'{self.args.capture_as}.bin')
        row = self.run_case(flags, snapshot)
        path = self.save_report(row, self.args.name)
        comparison = None
        if self.args.reference:
            comparison = self.compare(row, json.loads(Path(self.args.reference).read_text()))
        entry = {'kind': 'arm', 'name': self.args.name, 'report': str(path),
                 'from': source['snapshot'],
                 'off': off, 'set': sets,
                 'ack': {k: v for k, v in resume_ack.items() if v},
                 'stop': row['stop'], 'completed_steps': row['completed_steps'],
                 'stop_pc': row['stop_pc'], 'code_fingerprint': self.fingerprint,
                 'report_equal_to_reference': None if comparison is None else comparison['reference_equal'],
                 'wall_seconds': row['_wall_seconds']}
        self.append_lineage(entry)
        if self.args.evidence:
            self.write_evidence(entry, row)
        print(json.dumps({'report': str(path), 'stop': row['stop'],
                          'completed_steps': row['completed_steps'],
                          'comparison': comparison,
                          'wall_seconds': row['_wall_seconds']}, indent=2))

    # ------------------------------------------------------------------ 比较
    @staticmethod
    def strip_run_metadata(row):
        """比较口径:去掉快照轮专属键(snapshot_*/_wall_seconds),其余逐键相等。"""
        return {key: value for key, value in row.items()
                if not key.startswith('snapshot_') and key != '_wall_seconds'}

    @classmethod
    def compare(cls, resumed, reference):
        """resumed 行 vs 从 0 参照行:除快照轮元数据外必须逐键相等。先过一次
        JSON 往返规范化(int 键 dict 在内存/文件两侧的键序不同,逐键比较才
        是数据等价;整串比较会有伪差异)。不等时列差异键,不吞差异。"""
        flat_l = json.loads(json.dumps(cls.strip_run_metadata(resumed)))
        flat_r = json.loads(json.dumps(cls.strip_run_metadata(reference)))
        differing = sorted(set(flat_l) ^ set(flat_r))
        for key in sorted(set(flat_l) & set(flat_r)):
            if json.dumps(flat_l[key], sort_keys=True) != json.dumps(flat_r[key], sort_keys=True):
                differing.append(key)
        return {'reference_equal': not differing,
                'differing_keys': differing[:16], 'differing_count': len(differing)}

    def write_evidence(self, entry, row):
        rows = json.loads(EVIDENCE.read_text()) if EVIDENCE.exists() else []
        rows = [item for item in rows
                if not (item.get('route') == self.args.route and item.get('name') == entry['name'])]
        rows.append({'route': self.args.route, 'name': entry['name'],
                     'kind': entry['kind'], 'stop': entry['stop'],
                     'completed_steps': entry['completed_steps'],
                     'report_equal_to_reference': entry['report_equal_to_reference'],
                     'wall_seconds': entry['wall_seconds'],
                     'snapshot_digest': row.get('snapshot_restore', {}).get('digest')[:16],
                     'code_fingerprint': entry['code_fingerprint'][:16],
                     'ack': entry['ack']})
        EVIDENCE.write_text(json.dumps(rows, indent=2) + '\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--rom', type=Path, required=True,
                        help='captured ROM release asset (private, 不进 git)')
    parser.add_argument('--firmware', type=Path, default=FIRMWARE)
    parser.add_argument('--route', required=True, help='route 名(analysis/frontier-iterations/ 下的目录)')
    parser.add_argument('--work-root', type=Path, default=WORK_DEFAULT)
    parser.add_argument('--ack-prior-fingerprint',
                        help='跨代码续跑:承认 lineage 快照的旧指纹(值=快照指纹)')
    parser.add_argument('--reason', help='resume_ack 的前缀中性依据(记录进 lineage/报告)')
    sub = parser.add_subparsers(dest='command', required=True)

    reference = sub.add_parser('reference', help='从 0 全量跑一臂(参照/K 采集)')
    reference.add_argument('--off', action='append', default=[])
    reference.add_argument('--set', action='append', default=[])
    reference.add_argument('--budget', type=int, default=None,
                           help='可选 budget 停点(默认跑到自然停点)')
    reference.add_argument('--capture-dirty', help='自然停点脏快照 stem(诊断样本+K)')
    reference.add_argument('--report', help='报告文件 stem(默认 reference)')

    base = sub.add_parser('base', help='从 0 跑 budget 停点捕获干净快照 P')
    base.add_argument('--budget', type=int, required=True)
    base.add_argument('--off', action='append', default=[])
    base.add_argument('--set', action='append', default=[])
    base.add_argument('--name', help='快照/报告 stem(默认 base)')

    cut = sub.add_parser('cut', help='从最新干净快照重放 K-1 次循环捕获 Q')
    cut.add_argument('attempts', type=int, help='探索轮总循环尝试数 K(=脏快照 loop_attempts)')
    cut.add_argument('--name', help='快照 stem(默认 cut-<K-1>)')
    cut.add_argument('--off', action='append', default=[])
    cut.add_argument('--set', action='append', default=[])
    cut.add_argument('--ack-enable-slot', action='append', default=[])
    cut.add_argument('--ack-refixture-slot', action='append', default=[])
    cut.add_argument('--from', dest='from_snapshot', default=None,
                    help='源快照 stem(默认最新 base/cut)')

    arm = sub.add_parser('arm', help='从最新 cut 快照续跑一臂')
    arm.add_argument('name', help='臂名(报告 stem)')
    arm.add_argument('--off', action='append', default=[])
    arm.add_argument('--set', action='append', default=[])
    arm.add_argument('--ack-enable-slot', action='append', default=[],
                     help='跨开关恢复显式承认的 M4 槽位(None/缺键→on)')
    arm.add_argument('--ack-refixture-slot', action='append', default=[],
                     help='virgin 容器 fixture 差异承认的 M4 槽位')
    arm.add_argument('--budget', type=int, default=None)
    arm.add_argument('--reference', type=Path, help='从 0 参照报告(resume==from-0 比较)')
    arm.add_argument('--capture-as', help='臂停点快照 stem(迭代推进时留下一代)')
    arm.add_argument('--evidence', action='store_true', help='摘要行追加进 evidence/')
    arm.add_argument('--from', dest='from_snapshot', default=None,
                    help='源快照 stem(默认最新 base/cut)')

    args = parser.parse_args()
    runner = Runner(args)
    {'reference': runner.cmd_reference, 'base': runner.cmd_base,
     'cut': runner.cmd_cut, 'arm': runner.cmd_arm}[args.command]()


if __name__ == '__main__':
    main()
