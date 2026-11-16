#!/usr/bin/env python3
"""tools/handoff_lint.py 单测（路 AUDITFIX/sim-auditfix，AUDIT3 §5 落地；
ARCHIVE 路补跨文件两向）。

绿向（防误伤）：
  - 当前真 HANDOFF「已实现」区+归档文件合计 225 条零发现（含无 token 旧条目、
    同路号复用、同分支复用、正文交叉引用他人路 token 四类合法形态）；
  - 合成最小 section 的四类合法形态逐一直接验证不红；
  - 归一化后不足 MIN_FUZZY_LEN 的短条目不进相似度层；
  - 两文件各一条不同条目（跨文件互异）不红。
红向（篡改必红，仓库惯例）：
  - 整行原样重复；
  - AUDIT3 抓到的历史 AO 形态复刻（仅差「本次，」3 字符，条目原文摘自
    f9e17ad~1）→ 路由身份层；
  - 改路号+改分支的近似重复（路由身份/首部均不命中）→ 正文相似度层；
  - 首部 40 字符全同、正文分叉（无路 token 旧式条目）→ 首部层；
  - 同一条目 HANDOFF 一份+归档文件一份（跨文件重复，ARCHIVE 路新增形状）
    → 路由身份层，报文含两文件标签。
"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parent))
from handoff_lint import (HANDOFF,HEAD_LEN,MIN_FUZZY_LEN,SOURCES,check,
                          find_duplicates,implemented_entries)

# AUDIT3 删掉的历史重复对（git show f9e17ad~1:HANDOFF.md L190/L193 条目前缀
# 摘录；第二份仅多「本次，」——用 replace 精确复刻该差异点）。
AO_LINE=('- **D2-a 动作覆盖半边静态钉死：按键映射 handler 0x060252c6 全图'
         '（路 AO/sim-keyaction，基线 18bbfeb；纯只读静态反汇编+文档，未改 tools/'
         ' 任何代码、未跑改报告 probe，全部 derived-not-measured）**：主交付 '
         '[docs/key-action-map.md](docs/key-action-map.md) + d1-acceptance §6.1 '
         'B 组增补。①**注册与入口**：handler=S19 第二分发表（0x60a8c68）记录[5] '
         '{filter 0xff, event_id **0x10000c**, handler 0x060252c6}')
AO_LINE_NEAR=AO_LINE.replace('全图（路 AO/','全图（本次，路 AO/')

# 合法形态一：路号复用（真 AO 两案，分支不同）。
RSTCON_LINE=('- **P3_RST_CON0 多掩码访问掩码集扩展（本次，路 AO/sim-rstcon，'
             '`power_init_rstcon_mask_sets` 模型参数）**：write-only 位记录族'
             '证据与按键映射无关，正文各自独立。')
# 合法形态二：分支复用（PAR3 顺手项挂在 SNAPX 分支上）。
PAR3_LINE=('- **三臂进程并行 `--jobs N`（路 PAR3/sim-snapx，一案一提交）**：'
           'check_local 并发聚合报错，各项独立写 report。')
SNAPX_LINE=('- **快照接入探索验证流程：四容器适配器补齐+等价性重验（本次，'
            '路 SNAPX/sim-snapx）**：canonical 后 21.8M 重验逐字节一致。')
# 合法形态三：正文交叉引用他人路 token（自有 token 在前）。
D3PREP_LINE=('- **D3 差分验收准备（本次，路 BS/sim-d3prep，基线 973f4f8）**：'
             '三档清单与观测手段盘点；core tick 主线另见 路 BS/sim-tickmain '
             '条目，本条只做准备不含交付。')
TICKMAIN_LINE=('- **core tick IRQ3 交付 opt-in 进主条件场景（本次，'
               '路 BS/sim-tickmain，基线 6746e7f）**：前沿跨过 447,490 到 '
               'RES 读。')


def lint(*entries):
    return find_duplicates([(2*i+1,text) for i,text in enumerate(entries)])


class HandoffLintGreen(unittest.TestCase):
    def test_real_handoff_is_clean(self):
        # 两文件各自解析到真实规模（HANDOFF 近段+归档早段，均非空跑），合并零发现。
        counts={label:len(implemented_entries(path.read_text(encoding='utf-8')))
                for label,path in SOURCES}
        self.assertGreaterEqual(counts['HANDOFF.md'],50)
        self.assertGreaterEqual(counts['docs/handoff-archive.md'],50)
        self.assertGreaterEqual(sum(counts.values()),100)
        self.assertEqual(check(),[])

    def test_route_letter_reuse_not_flagged(self):
        self.assertEqual(lint(AO_LINE,RSTCON_LINE),[])

    def test_shared_branch_not_flagged(self):
        self.assertEqual(lint(PAR3_LINE,SNAPX_LINE),[])

    def test_cross_reference_token_not_flagged(self):
        self.assertEqual(lint(D3PREP_LINE,TICKMAIN_LINE),[])

    def test_short_entries_skip_fuzzy_layer(self):
        # 归一化后 <MIN_FUZZY_LEN 的短条目不进相似度层（由精确层负责）。
        self.assertEqual(lint('- **甲：短条目**','- **乙：短条目**'),[])

    def test_cross_file_distinct_entries_not_flagged(self):
        # 跨文件互异：HANDOFF 一条+归档一条（不同路由），合并查重不红。
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            handoff=root/'HANDOFF.md'
            archive=root/'handoff-archive.md'
            handoff.write_text('## 已实现\n'+AO_LINE+'\n',encoding='utf-8')
            archive.write_text('# 归档\n\n## 已实现（归档区）\n'+RSTCON_LINE+'\n',
                               encoding='utf-8')
            findings=check((('HANDOFF.md',handoff),
                            ('docs/handoff-archive.md',archive)))
        self.assertEqual(findings,[])


class HandoffLintRed(unittest.TestCase):
    def test_verbatim_duplicate_caught(self):
        findings=lint(AO_LINE,RSTCON_LINE,AO_LINE)
        self.assertEqual(len(findings),1)
        self.assertIn('路由身份同为 路 AO/sim-keyaction',findings[0])

    def test_historical_ao_shape_caught(self):
        # 仅差「本次，」3 字符的历史形态（AUDIT3 实案）必红。
        findings=lint(AO_LINE,AO_LINE_NEAR)
        self.assertEqual(len(findings),1)
        self.assertIn('路由身份同为 路 AO/sim-keyaction',findings[0])

    def test_renamed_route_near_duplicate_caught_by_similarity(self):
        # 改路号+改分支后路由身份与首部都不命中，只剩正文相似度可抓。
        body='证据正文'*40+'收尾。'
        left=('- **AO：改路复刻——仅剩相似度层可抓的近似重复（路 AO/sim-keyaction，'
              '基线 18bbfeb）**：'+body)
        right=('- **ZZ：改路复刻——仅剩相似度层可抓的近似重复（路 ZZ/sim-keyaction2，'
               '基线 18bbfeb）**：'+body)
        findings=lint(left,right)
        self.assertEqual(len(findings),1)
        self.assertIn('正文相似度',findings[0])

    def test_shared_head_divergent_body_caught(self):
        # 无路 token 旧式条目：首部 HEAD_LEN 字符全同、正文分叉 → 首部层。
        common=('- **旧式无 token 条目：开头四十个字符保持完全相同之后再分道扬镳'
                '各写各的互不相关内容')
        self.assertGreaterEqual(len(common),HEAD_LEN)
        left=common+'A 面证据'*40
        right=common+'B 面证据'*40
        findings=lint(left,right)
        self.assertEqual(len(findings),1)
        self.assertIn(f'首部 {HEAD_LEN} 字符全同',findings[0])

    def test_cross_file_duplicate_caught(self):
        # ARCHIVE 路新增红向形状：同一条目 HANDOFF 一份+归档一份 → 必红，
        # 报文同时带两文件位置标签（可定位到文件）。
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            handoff=root/'HANDOFF.md'
            archive=root/'handoff-archive.md'
            handoff.write_text('## 已实现\n'+AO_LINE+'\n',encoding='utf-8')
            archive.write_text('# 归档\n\n## 已实现（归档区）\n'+AO_LINE+'\n',
                               encoding='utf-8')
            findings=check((('HANDOFF.md',handoff),
                            ('docs/handoff-archive.md',archive)))
        self.assertEqual(len(findings),1)
        self.assertIn('HANDOFF.md:L2',findings[0])
        self.assertIn('docs/handoff-archive.md:L4',findings[0])
        self.assertIn('路由身份同为 路 AO/sim-keyaction',findings[0])


if __name__=='__main__':
    unittest.main()
