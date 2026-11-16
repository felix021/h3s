#!/usr/bin/env python3
"""HANDOFF「## 已实现」区条目查重 lint（AUDIT3 §5 流程建议落地，路 AUDITFIX）。

背景：AUDIT2 抓到三路条目整段重复、AUDIT3 又抓到 AO/sim-keyaction 条目两份（仅差
「本次，」3 字符，md5 全等/首部前缀口径均漏网）——均为 subagent/主协调合并时与既有
条目相撞，靠人工审计发现太晚，按「流程错误靠流程解决」落成 units 常驻检查。

口径（三层，任一命中即红；每对条目只报最先命中的一层）：
  1) 路由身份：条目首个「路 XXX/sim-yyy」全 token（路号+分支拼接）相同 → 重复。
     只取首个＝条目自称的路；正文交叉引用他人路 token 不算（实测 BS/sim-d3prep
     条目后文提及 BS/sim-tickmain）。路号会复用（AO/sim-keyaction vs AO/sim-rstcon）、
     分支也会复用（PAR3 与 SNAPX 共用 sim-snapx），故键必须两者拼接、缺一不可。
  2) 首部 HEAD_LEN 字符全同：AUDIT2/AUDIT3 人工查重口径的机械化，覆盖无路 token
     的旧式条目（2026-11-16 实存 73 条）。
  3) 正文相似度：压空白后 difflib.SequenceMatcher(autojunk=False) ratio≥RATIO_THRESHOLD
     （0.90）。2026-11-16 实测 217 条两两最高比值 <0.70（模板化的 P3 记录系列也在
     其下），阈值余量充足；历史 AO 对（仅差「本次，」）实测≈0.999 必红。归一化后
     不足 MIN_FUZZY_LEN 字符的短条目不进本层（信号不足，由 1/2 层负责）。

范围：仅「## 已实现」区顶格 `- ` 条目行（缩进子弹行归属其父条目，不单算）。已知
未覆盖：顶格 `*` 形态的坏前缀条目——2026-11-16 实存 `*COEF：…` 一对逐字节重复
（L135/L137，两轮审计口径均漏网）；HANDOFF 对本路只读，登记主协调合并时修。
"""
import re
import sys
from difflib import SequenceMatcher
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
HANDOFF=ROOT/'HANDOFF.md'
ROUTE_TOKEN=re.compile(r'路\s*[A-Za-z0-9]+/sim-[a-z0-9-]+')
HEAD_LEN=40
RATIO_THRESHOLD=0.90
MIN_FUZZY_LEN=60


def implemented_entries(text):
    """「## 已实现」区的顶格 `- ` 条目 → [(行号(1 起), 行文本), ...]。"""
    lines=text.split('\n')
    start=next((i for i,line in enumerate(lines) if line.startswith('## 已实现')),None)
    if start is None:
        raise SystemExit('handoff_lint: HANDOFF 缺「## 已实现」区')
    end=next((i for i in range(start+1,len(lines)) if lines[i].startswith('## ')),
             len(lines))
    return [(i+1,lines[i]) for i in range(start+1,end)
            if lines[i].startswith('- ')]


def _normalized(text):
    return re.sub(r'\s+','',text)


def find_duplicates(entries):
    """entries=[(行号,文本)] → 重复发现（字符串列表；空=绿）。"""
    findings=[]
    normalized=[_normalized(text) for _,text in entries]
    tokens=[ROUTE_TOKEN.search(text) for _,text in entries]
    for i in range(len(entries)):
        for j in range(i+1,len(entries)):
            line_i,text_i=entries[i]
            line_j,text_j=entries[j]
            why=None
            if tokens[i] and tokens[j] and tokens[i].group(0)==tokens[j].group(0):
                why=f'路由身份同为 {tokens[i].group(0)}'
            if not why and text_i[:HEAD_LEN]==text_j[:HEAD_LEN]:
                why=f'首部 {HEAD_LEN} 字符全同'
            if not why:
                left,right=normalized[i],normalized[j]
                if min(len(left),len(right))>=MIN_FUZZY_LEN:
                    matcher=SequenceMatcher(None,left,right,autojunk=False)
                    if (matcher.real_quick_ratio()>=RATIO_THRESHOLD
                            and matcher.quick_ratio()>=RATIO_THRESHOLD):
                        ratio=matcher.ratio()
                        if ratio>=RATIO_THRESHOLD:
                            why=f'正文相似度 {ratio:.3f}'
            if why:
                findings.append(f'L{line_i} 与 L{line_j} 重复（{why}）：'
                                f'{text_i.lstrip("- *")[:50]}…')
    return findings


def check(path=HANDOFF):
    """对仓库 HANDOFF.md 跑查重 → 发现列表（空=绿）。"""
    return find_duplicates(implemented_entries(
        path.read_text(encoding='utf-8')))


def main(argv=None):
    findings=check()
    if findings:
        for finding in findings:
            print('handoff-lint: '+finding)
        print(f'handoff-lint: {len(findings)} 处「已实现」条目重复')
        return 1
    print('handoff-lint: 「已实现」区条目查重通过')
    return 0


if __name__=='__main__':
    sys.exit(main())
