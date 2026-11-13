#!/usr/bin/env python3
"""Round-trip verification for the pinned pi32v2 assembler wrapper (路 G).

Proves the "assembler is trustworthy" closure without JieLi's external clang:
every covered instruction encodes to bytes and decodes back to the same form and
the same raw field values (encode -> decode zero diff), the text assembler's
shortest-form selection is pinned, and the real eq-toggle/hooks.S assembles to a
byte-exact 1672B object whose every instruction reassembles byte-identically.

No device or private ROM. See docs/pi32v2-asm-wrap.md.
"""
import unittest

import pi32v2_asm as W

HOOKS = W.ROOT / 'tools/eq-toggle/hooks.S'

# (insn id, raw field values, expected bytes): the encode->decode closure plus
# byte pins for the shapes the project's firmware/frontier actually uses.
CODEX_CASES = [
    ('li.u32',               dict(r=0, n=0x12345678), 'c0 ff 78 56 34 12'),
    ('st.w.sp.l',            dict(n=2, r=9),          'd4 e9 09 90'),   # 0x6065ccc frontier store
    ('st.w.sp',              dict(n=2, r=9),          '89 22'),
    ('bne.and.modimm.ri.l',  dict(r=0, i=0xFF, n=0x10),   '61 ff ff 00 10 00'),
    ('beq.and.modimm.ri.l',  dict(r=0, i=0xFF, n=0xFFFC), '60 ff ff 00 fc ff'),
    ('mov.rr.p',             dict(x=0, y=1),          '10 d6'),
]

# (assembly text, [(expected insn id, expected bytes, canonical decode text)])
# The text assembler picks the shortest form; these pins document that choice.
TEXT_CASES = [
    ('r0 = 0x12345678\n', [('li.u32', 'c0 ff 78 56 34 12', 'r0 = 305419896')]),
    ('[sp+8] = r9\n',     [('st.w.sp', '89 22', '[sp+8] = r9')]),
    ('[sp+256] = r9\n',   [('st.w.sp.l', 'd4 e9 01 91', '[sp+256] = r9')]),
    ('if ((r0 & 0xFF) != 0) goto 8\n',
     [('bne.and.modimm.ri.l', '61 ff ff 00 04 00', 'if ((r0 & 0xFF) != 0) goto 8')]),
    ('if ((r0 & 0xFF) == 0) goto -8\n',
     [('beq.and.modimm.ri.l', '60 ff ff 00 fc ff', 'if ((r0 & 0xFF) == 0) goto -8')]),
    # parallel pair: A half ends with '#' and pairs with the following 16-bit store
    ('r6 = r0 #\n[r4+0] = r5\n', [('mov.rr.p', '06 d6', 'r6 = r0  #'),
                                  ('st.w', 'c5 60', '[r4+0] = r5')]),
]


class RoundtripTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pkg = W.load()
        cls.table = W.load_table()

    def test_table_loads_clean(self):
        self.assertEqual(len(self.table.insns), 1165)
        self.assertEqual(self.pkg['table'].check(self.table), [])

    def test_encode_decode_roundtrip_zero_diff(self):
        for insn_id, values, expected in CODEX_CASES:
            with self.subTest(insn=insn_id):
                data = W.roundtrip_one(insn_id, values)
                self.assertEqual(data.hex(' '), expected)

    def test_text_assembler_roundtrip(self):
        for text, rows in TEXT_CASES:
            with self.subTest(text=text.strip()):
                obj = W.read_object(W.assemble_text(text))
                got = [(name, data, relocs) for name, data, relocs in W.code_sections(obj)]
                self.assertEqual(len(got), 1, 'single-section snippet produced multiple code sections')
                _, code, relocs = got[0]
                self.assertEqual(relocs, [], 'expected no relocations in these snippets')
                decoded = list(W.disassemble(code))
                self.assertEqual(len(decoded), len(rows))
                for (addr, insn_id, fmt, raw), (exp_id, exp_bytes, exp_text) in zip(decoded, rows):
                    self.assertEqual(insn_id, exp_id)
                    self.assertEqual(raw.hex(' '), exp_bytes)
                    self.assertEqual(fmt, exp_text)

    def test_hooks_object_size_and_layout(self):
        data = W.assemble_file(HOOKS)
        self.assertEqual(len(data), 1672, 'eq-toggle/hooks.S object size changed')
        obj = W.read_object(data)
        sections = {name: (len(bytes_), len(relocs)) for name, bytes_, relocs in W.code_sections(obj)}
        self.assertEqual(sections, {
            '.site.key': (6, 1),
            '.site.source_read': (6, 1),
            '.site.preset': (4, 1),
            '.site.custom': (4, 1),
            '.site.open': (6, 1),
            '.text.hooks': (54, 7),
        }, 'hook section map changed')
        self.assertEqual(sum(b for b, _ in sections.values()), 80)
        self.assertEqual(sum(n for _, n in sections.values()), 12)

    def test_hooks_every_instruction_reassembles_byte_identical(self):
        obj = W.read_object(W.assemble_file(HOOKS))
        seen = []
        for name, code, relocs in W.code_sections(obj):
            for addr, insn_id, fmt, raw in W.disassemble(code):
                self.assertIsNotNone(insn_id, f'{name}@{addr}: undecodable word {raw.hex()}')
                self.assertEqual(W.encode(insn_id, **W.decode(raw, 0)[1]), raw,
                                 f'{name}@{addr}: {insn_id} reassembly differs')
                seen.append((insn_id, fmt))
        ids = {iid for iid, _ in seen}
        # the real hook-body instruction shapes the project depends on
        for expected in ('goto.l', 'call.l', 'nop', 'ld.bu', 'bne.ri', 'ld.bu.l',
                         'and.modimm.rri', 'li.u8', 'mov.rr', 'ld.w'):
            self.assertIn(expected, ids, f'hooks.S no longer exercises {expected}')


if __name__ == '__main__':
    unittest.main()
