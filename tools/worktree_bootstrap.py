#!/usr/bin/env python3
"""一键补齐新 worktree 跑 check_local（含 ROM 面依赖）所需的 gitignored 资产。

parity4/fmimpl/segimpl 等路接连手工补过的同一组东西（各花 10-20 分钟）：
  1. tools/reference-cache/   45M 钉死参考缓存（pi32v2-isa/q32s-static/701n-boot/SDK 文件）
  2. firmware/*.ufw           official 与 b79 测试镜像（sha 见 firmware/manifest.json）
  3. tools/jl-misctools/      Git 子模块（缺它时 hearing_aid/candidate 用例 import jltech 直接红）
  4. analysis/v1.1.8/         官方镜像解包派生物（缺它 hearing_aid artifact tier 全 skip）

全部维持 gitignored/子模块现状，不进 git：1/2 在 worktree 内做软链（parity4 先例），
3 就地 submodule init（对象库与主仓共享，秒级），4 从 2+3 本地解包生成（sha 钉死）。
默认源=主仓（由 git common dir 反推，不写死路径）；--source 可指任一资产齐全的仓/目录。
幂等可重跑：已就位的项报 OK 跳过；缺源时逐项报清楚缺什么、在源仓跑什么命令补。

用法（在待补齐的 worktree 根目录下）：
  python3 tools/worktree_bootstrap.py            # 补全部四项
  python3 tools/worktree_bootstrap.py --check    # 只体检不动作
  python3 tools/worktree_bootstrap.py --skip-unpack   # 跳过 analysis/v1.1.8 解包
私有 ROM bin 不在本脚本范围：check_local --rom 仍按文档显式传路径。
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# reference-cache 关键内容探针（缺任一=缓存残缺）：各探针对应的生成命令用于缺源提示。
REFCACHE_PROBES = [
    ('pi32v2-isa/isa/pi32v2.toml', 'python3 tools/probe_pi32v2.py --fetch-simulator'),
    ('q32s-static/q32s.sla', 'python3 tools/analyze_q32s.py --fetch --report analysis/q32s.json'),
    ('701n-boot/SDK', 'python3 tools/fetch_boot_reference.py --hardware'),
    ('media.a', 'python3 tools/bootstrap.py --reference-sdk --reference-binaries'),
]


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--source', type=Path, help='资产源仓根目录（默认：git common dir 反推出的主仓）')
    p.add_argument('--check', action='store_true', help='只报告现状，不做任何改动')
    p.add_argument('--skip-unpack', action='store_true', help='不生成 analysis/v1.1.8（前三项照常）')
    args = p.parse_args()

    if args.source is None:
        out = subprocess.run(['git', 'rev-parse', '--git-common-dir'],
                             cwd=ROOT, capture_output=True, text=True)
        if out.returncode != 0:
            sys.exit('无法定位主仓（git rev-parse --git-common-dir 失败）；请用 --source 显式指定源仓')
        source = Path(out.stdout.strip() or '.git').resolve().parent
    else:
        source = args.source.resolve()
    print(f'target = {ROOT}')
    print(f'source = {source}' + (' (即本仓，无需源)' if source == ROOT else ''))

    problems = []

    def report(label, status, detail=''):
        print(f'[{status:^5}] {label}' + (f' — {detail}' if detail else ''))
        if status == 'MISS':
            problems.append(f'{label}: {detail}')

    # ---- 1. tools/reference-cache ------------------------------------------
    cache_dst = ROOT / 'tools/reference-cache'
    cache_src = source / 'tools/reference-cache'
    missing_src = [rel for rel, _ in REFCACHE_PROBES if not (cache_src / rel).exists()]
    have = [rel for rel, _ in REFCACHE_PROBES if (cache_dst / rel).exists()]
    if len(have) == len(REFCACHE_PROBES):
        report('tools/reference-cache', 'OK', '关键内容齐（%d/%d 探针）' % (len(have), len(REFCACHE_PROBES)))
    elif cache_dst.is_symlink():
        report('tools/reference-cache', 'MISS',
               '软链 %s -> %s 内容残缺，缺：%s' % (cache_dst, os.readlink(cache_dst), ', '.join(
                   rel for rel, _ in REFCACHE_PROBES if not (cache_dst / rel).exists())))
    elif args.check:
        report('tools/reference-cache', 'MISS', '缺探针：' + ', '.join(
            rel for rel, _ in REFCACHE_PROBES if rel not in have))
    elif missing_src:
        report('tools/reference-cache', 'MISS',
               '源仓 %s 也缺（%s）；先在源仓跑：%s' % (cache_src, ', '.join(missing_src),
                 '；'.join(cmd for rel, cmd in REFCACHE_PROBES if rel in missing_src)))
    elif not cache_dst.exists():
        cache_dst.symlink_to(cache_src, target_is_directory=True)
        report('tools/reference-cache', 'LINK', f'软链 -> {cache_src}')
    elif cache_dst.is_dir() and not any(cache_dst.iterdir()):
        cache_dst.rmdir()
        cache_dst.symlink_to(cache_src, target_is_directory=True)
        report('tools/reference-cache', 'LINK', f'空目录换软链 -> {cache_src}')
    else:
        report('tools/reference-cache', 'MISS',
               '已存在非空真实目录但缺 %s；确认无用后删除该目录重跑本脚本，或手动从源补齐'
               % ', '.join(rel for rel, _ in REFCACHE_PROBES if rel not in have))

    # ---- 2. firmware/*.ufw（official + b79） --------------------------------
    manifest = json.loads((ROOT / 'firmware/manifest.json').read_text())
    ufw_ok = True
    for variant in ('original', 'current'):
        item = manifest['assets'][variant]
        dst = ROOT / 'firmware' / item['filename']
        src = source / 'firmware' / item['filename']
        want = item['sha256']
        label = f'firmware/{item["filename"]} ({variant})'
        present = dst.exists() or dst.is_symlink()  # 断链软链也算「有文件」交由 sha 判
        if present and dst.exists() and sha256(dst) == want:
            report(label, 'OK', 'sha256 吻合 manifest')
        elif present:
            ufw_ok = False
            report(label, 'MISS', '文件/软链存在但读不到或 sha 不符（期望 %s…）；删除后重跑' % want[:12])
        elif args.check:
            ufw_ok = False
            report(label, 'MISS', '缺失（期望 sha %s…）；不带 --check 重跑可自动软链' % want[:12])
        elif src.exists() and sha256(src) == want:
            dst.symlink_to(src)
            report(label, 'LINK', f'软链 -> {src}')
        elif src.exists():
            ufw_ok = False
            report(label, 'MISS', f'源文件 {src} sha 与 manifest 不符，拒绝软链')
        else:
            ufw_ok = False
            report(label, 'MISS', f'源仓缺该文件；在源仓跑 python3 tools/fetch_firmware.py --variant {variant}')

    # ---- 3. tools/jl-misctools 子模块 ---------------------------------------
    misctools_marker = ROOT / 'tools/jl-misctools/firmware/jltech'
    if misctools_marker.is_dir():
        report('tools/jl-misctools 子模块', 'OK', 'firmware/jltech 已检出')
    elif args.check:
        report('tools/jl-misctools 子模块', 'MISS', '未初始化')
    else:
        out = subprocess.run(['git', 'submodule', 'update', '--init', 'tools/jl-misctools'],
                             cwd=ROOT, capture_output=True, text=True)
        if out.returncode == 0 and misctools_marker.is_dir():
            report('tools/jl-misctools 子模块', 'INIT', 'submodule update --init 完成')
        else:
            sys.stderr.write(out.stdout + out.stderr)
            report('tools/jl-misctools 子模块', 'MISS',
                   'git submodule update --init 失败（exit %d）；检查网络/主仓 .git/modules 缓存' % out.returncode)

    # ---- 4. analysis/v1.1.8（派生物：ufw + 子模块 → unpack.py） -------------
    analysis = ROOT / 'analysis/v1.1.8'
    submodule_ok = misctools_marker.is_dir()
    if (analysis / 'unpacked').is_dir():
        report('analysis/v1.1.8', 'OK', 'unpacked/ 已存在')
    elif analysis.exists():
        report('analysis/v1.1.8', 'MISS', '目录存在但缺 unpacked/（残缺半成品）；删除整个 v1.1.8 后重跑')
    elif args.check:
        report('analysis/v1.1.8', 'MISS', '缺（hearing_aid artifact tier 会 skip）')
    elif args.skip_unpack:
        report('analysis/v1.1.8', 'SKIP', '--skip-unpack 指定跳过')
    elif not (ufw_ok and submodule_ok):
        report('analysis/v1.1.8', 'SKIP', 'ufw/子模块未全就位，无法解包（先修上面 MISS 项再重跑）')
    else:
        out = subprocess.run([sys.executable, 'tools/unpack.py'], cwd=ROOT,
                             capture_output=True, text=True)
        if out.returncode == 0 and (analysis / 'unpacked').is_dir():
            report('analysis/v1.1.8', 'GEN', 'tools/unpack.py 解包完成')
        else:
            sys.stderr.write(out.stdout + out.stderr)
            report('analysis/v1.1.8', 'MISS',
                   'tools/unpack.py 失败（exit %d）；需 PyYAML+crcmod（pip install pyyaml crcmod）' % out.returncode)

    # ---- 汇总 ---------------------------------------------------------------
    print()
    if problems:
        print('未全部就位（%d 项待处理，见上 MISS 行）：' % len(problems))
        for line in problems:
            print('  - ' + line)
        sys.exit(1)
    print('全部就位。直接可跑：python3 tools/check_local.py --units（ROM 面：--rom <私有ROM路径>）')


if __name__ == '__main__':
    main()
