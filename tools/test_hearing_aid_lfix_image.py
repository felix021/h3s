#!/usr/bin/env python3
"""Offline verification of the hearing-aid LFIX repair candidates F1a/F1b.

Route LFIX-IMPL (docs/hearing-aid-lfix-impl.md; plan docs/hearing-aid-lfix-plan.md
§4). F1 = the HAL5 full chain with the open/close payloads replaced by the
repair variants: A1 pre-open ANC-exit wait (timeout cancels this open), B1
set_scene(stream,10) removed, B2 explicit SET_CHANNEL(1) AFTER start returns.
F1a additionally carries the A3 clock hold (clock_alloc/free "lfix_hold",
128 MHz, diagnostic tier); F1b (--lfix-no-hold) differs from F1a by A3 alone —
the plan §4-C two-artifact adjudication (a runtime switch would need a key-32
param channel through the keystore hook + app, ruled not worth the surface).

Tiers:
- CLI contract (always runs): lfix requires an explicit finite MPO in
  (0.3, 120) — same contract as full — and rejects --hearing-aid-demo,
  --hearing-aid-l1-key7; --lfix-no-hold without --hearing-aid-lfix is an error.
- Artifact tier (needs firmware/ + analysis/v1.1.8/ + jl-misctools +
  pi32v2-isa; skips otherwise): builds F1a, F1b and the HAL5 base, then
  asserts
  1. pinned sha256 for both candidates + filenames + hearing_aid_lfix
     report keys (A1/B1/B2/A3 semantics + honest boundary present);
  2. every whole-file diff byte (F1a vs HAL5, F1b vs HAL5, F1a vs F1b)
     falls in exactly the expected patch regions — UFW outer CRC, boot-info
     window, the four nested app CRC pairs, the KEY7 window, the fence
     immediate, the R4 registry setter word, and the appended-file region;
  3. the appended segment embedded in each .ufw equals an independent
     re-assembly of the lfix payload sources (byte identity);
  4. F1b's close payload is byte-identical to the HAL5 base close payload
     (F1b changes the open payload only); F1b carries no clock anchors and
     no "lfix_hold" name bytes; F1a carries exactly the A3 anchor set
     (alloc + 2× name + free + 128 MHz) — the "differs by A3 only" contract;
  5. the A1 loop wiring decoded back out of the image: poll reads
     b[r4+29]/b[r4+33], timeout branch lands on the cancel path, retry
     branch lands on the poll head, delay call target 0x06064efc;
  6. budget: both candidates stay inside the [0xE5073, 0xE5FF8) append area;
  7. the independent CRC/JLFS verifier accepts both images;
  8. negatives: one flipped byte outside every allowed region turns the
     region audit red (the audit has discriminative power);
  9. the HAL5 base reproduction is untouched by this route (pinned
     9ad0f088… — F1 is defined on top of the exact candidate that is
     already on the device).

UNVALIDATED ON DEVICE: both candidates stay OFFLINE_UNTESTED; nothing here
flashes or simulates. Runtime behavior (ANC wait passing, channel override,
clock-hold effect) is left for device verification per the impl report.
"""
import json
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import pi32v2_asm as ASM                                    # noqa: E402
import hearing_aid_l1_image as IMG                          # noqa: E402

FIRMWARE = ROOT / 'firmware/QCY_H3S_7018F_1T2_release_note_V1.1.8_011.ufw'
ANALYSIS = ROOT / 'analysis/v1.1.8'
ASSETS_READY = (FIRMWARE.exists() and ANALYSIS.exists()
                and (ROOT / 'tools/jl-misctools/firmware/jltech').is_dir()
                and (ROOT / 'tools/reference-cache/pi32v2-isa/isa/pi32v2.toml').exists())
BUILDER = str(ROOT / 'tools/build_quiet_candidate.py')

FLASH_FILE_OFF = 0x600              # flash.bin entry file offset (load_image)
FLASH_PLAIN_BASE = 0x2020           # plain[0] == flash[0x2020] (load_image)
OLD_APP_END = 0xE5073               # first free append offset (FLASH coords)
APPEND_AREA_END = 0xE5FF8           # padding end: the append budget ceiling (flash)
SEG = 0xE3074                       # appended segment base (plain coords)
KEY7_WINDOW = (0x25404, 0x2540C)    # cmd7 branch body entry (plain coords)
FENCE_IMM = (0x34E9A, 0x34E9E)      # LIM_PC0 upper-limit immediate (plain coords)
REGISTRY_WORD = (0xA9C7C, 0xA9C80)  # cmdid 0x30 setter word (plain coords)
# F1a segment layout (1060B): open code 298 + name 10 + close 98 + mpo 128 +
# mpo rodata 92 + eq 180 + eq rodata 60 + toggle stub 52 + keyhook 142
F1A = {'code_bytes': 1060, 'open': 298, 'name': 10, 'close': 98,
       'close_off': 308, 'new_app_end': OLD_APP_END + 33 + 1060,
       'sha256': '73b45e659d127452697b5580b2474ef481ce9b8118f3f5dfa8b9c731bb4fef48',
       'name_off': 'H3S_0.1.1.8_tones_minus0dB_LFIX_F1A_HOLD_MPO95_OFFLINE_UNTESTED.ufw'}
# F1b segment layout (946B): open 228 + close 64 + mpo 128 + mpo rodata 92 +
# eq 180 + eq rodata 60 + toggle stub 52 + keyhook 142 (close == stock HAL5)
F1B = {'code_bytes': 946, 'open': 228, 'close': 64,
       'close_off': 228, 'new_app_end': OLD_APP_END + 33 + 946,
       'sha256': 'f16576687dae42a583149aa9f8a2f1b89dc777228245a27c289f0faeec00f2c1',
       'name_off': 'H3S_0.1.1.8_tones_minus0dB_LFIX_F1B_NOHOLD_MPO95_OFFLINE_UNTESTED.ufw'}
LFIX_HOLD_FLAG = 0x125578          # A3 ownership byte (BSS, slot+4)
HAL5_SHA = '9ad0f0881a7996cb4dea81477d8731aa9b4db69a6f352603118663d68eda5465'
CLOCK_ALLOC, CLOCK_FREE = 0x06000978, 0x0600572c
DELAY_FN = 0x6064efc


def _run_builder(args, outdir):
    return subprocess.run([sys.executable, BUILDER, *args,
                           '--output-dir', str(outdir)],
                          capture_output=True, text=True, timeout=300)


def _allowed_regions(new_app_end):
    """Expected patch regions as RAW-FILE offsets for a whole-file diff.

    Plain-coord constants shift by 0x2020 to reach flash coords (load_image
    slices plain from flash 0x2020); OLD_APP_END/new_app_end are already
    flash coords; every flash coord shifts by the 0x600 flash.bin file entry.
    Narrowed per the codex review: boot-info allows only the CRC [0,6) and
    app-length [8,12) fields (the rest of the 32B window must be identical);
    the nested app CRCs are the four 2B words at 0x2020/0x2022/0x2040/0x2042
    (the words in between must be identical); the append region is our own
    file (content pinned byte-exactly by test_03 + header fields in test_03b).
    """
    f = FLASH_FILE_OFF
    p = FLASH_PLAIN_BASE
    regions = [
        (0x0, 0x4), (0x44, 0x46),                       # UFW outer CRC (6B)
        (f + 0x2000, f + 0x2006),                       # boot-info CRC field
        (f + 0x2008, f + 0x200C),                       # boot-info length field
        (f + 0x2020, f + 0x2024),                       # nested app CRC pair 1
        (f + 0x2040, f + 0x2044),                       # nested app CRC pair 2
        (f + p + KEY7_WINDOW[0], f + p + KEY7_WINDOW[1]),      # cmd7 graft
        (f + p + FENCE_IMM[0], f + p + FENCE_IMM[1]),   # fence upper-limit imm
        (f + p + REGISTRY_WORD[0], f + p + REGISTRY_WORD[1]),  # R4 setter word
        (f + OLD_APP_END, f + new_app_end),             # appended mic_effect.bin
    ]
    return [(lo, hi) for lo, hi in regions]


def _assert_diffs_in_regions(t, raw_a, raw_b, new_end, label):
    diffs = [i for i in range(len(raw_a)) if raw_a[i] != raw_b[i]]
    allowed = _allowed_regions(new_end)
    bad = [i for i in diffs if not any(lo <= i < hi for lo, hi in allowed)]
    t.assertEqual(bad, [], f'{label}: diffs outside expected regions: '
                           f'{[hex(x) for x in bad[:8]]}')
    return diffs


class LfixCliContractTest(unittest.TestCase):
    """lfix 的开关契约：M 必填/范围门/demo·key7 互斥/no-hold 依赖（路 §CLI）。"""

    def _reject(self, args, fragment):
        with tempfile.TemporaryDirectory() as tmp:
            proc = _run_builder(['--attenuation-db', '0', *args], tmp)
        self.assertNotEqual(proc.returncode, 0, f'builder accepted {args}')
        self.assertIn(fragment, proc.stderr)

    def test_01_requires_explicit_mpo(self):
        self._reject(['--hearing-aid-lfix'],
                     'requires an explicit --hearing-aid-mpo')

    def test_02_mpo_range_gate(self):
        for bad in ('0.3', '0.2', '120', '121', 'nan', 'inf', '-5',
                    '0.300000001', '1e100'):
            self._reject(['--hearing-aid-lfix', '--hearing-aid-mpo', bad],
                         '0.3 < --hearing-aid-mpo < 120')

    def test_03_demo_mutually_exclusive(self):
        self._reject(['--hearing-aid-lfix', '--hearing-aid-mpo', '95',
                      '--hearing-aid-demo'], 'mutually exclusive')

    def test_04_key7_window_mutually_exclusive(self):
        self._reject(['--hearing-aid-lfix', '--hearing-aid-mpo', '95',
                      '--hearing-aid-l1-key7'], 'share the KEY7 graft window')

    def test_05_no_hold_requires_lfix(self):
        self._reject(['--lfix-no-hold', '--hearing-aid-mpo', '95'],
                     '--lfix-no-hold requires --hearing-aid-lfix')


@unittest.skipUnless(ASSETS_READY, 'official firmware assets not materialized')
class LfixArtifactTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = FIRMWARE.read_bytes()
        cls.tmp = tempfile.TemporaryDirectory()
        base = Path(cls.tmp.name)
        cls.builds = {}
        for tag, args in (('f1a', ['--hearing-aid-lfix', '--hearing-aid-mpo', '95']),
                          ('f1b', ['--hearing-aid-lfix', '--lfix-no-hold',
                                   '--hearing-aid-mpo', '95']),
                          ('hal5', ['--hearing-aid-full', '--hearing-aid-mpo', '95'])):
            out = base / tag
            proc = _run_builder(['--attenuation-db', '0', *args], out)
            assert proc.returncode == 0, f'{tag} build failed: {proc.stderr[-400:]}'
            cls.builds[tag] = {'ufw': next(out.glob('*.ufw')),
                               'report': json.loads(
                                   (out / 'build-report.json').read_text())}
        from verify_eq_candidate import load_image
        cls.images = {tag: load_image(cls.builds[tag]['ufw'])
                      for tag in ('f1a', 'f1b', 'hal5')}

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    # —— 1. 命名 + sha + 报告键 ——
    def test_01_names_shas_report(self):
        import hashlib
        for tag, spec in (('f1a', F1A), ('f1b', F1B)):
            path = self.builds[tag]['ufw']
            self.assertEqual(path.name, spec['name_off'])
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            self.assertEqual(digest, spec['sha256'])
            rep = self.builds[tag]['report']['hearing_aid_lfix']
            self.assertIn('F1', rep['candidate'])
            for key in ('a1_anc_wait', 'b1_scene_value_change',
                        'b2_set_channel_after_start', 'a3_switch',
                        'honest_boundary'):
                self.assertIn(key, rep)
        self.assertIn('A3 clock hold', self.builds['f1a']['report']['hearing_aid_lfix']['candidate'])
        self.assertIn('no clock hold', self.builds['f1b']['report']['hearing_aid_lfix']['candidate'])
        self.assertIsNotNone(self.builds['f1a']['report']['hearing_aid_lfix']['a3_clock_hold'])
        self.assertIsNone(self.builds['f1b']['report']['hearing_aid_lfix']['a3_clock_hold'])

    # —— 2. 整文件 diff 全落预期补丁区（三方）——
    def test_02_diffs_only_in_expected_regions(self):
        raw = {t: self.builds[t]['ufw'].read_bytes() for t in self.builds}
        for label, x, y, end in (('F1a vs HAL5', 'f1a', 'hal5', F1A['new_app_end']),
                                 ('F1b vs HAL5', 'f1b', 'hal5', F1B['new_app_end']),
                                 ('F1a vs F1b', 'f1a', 'f1b', F1A['new_app_end'])):
            diffs = _assert_diffs_in_regions(self, raw[x], raw[y], end, label)
            self.assertTrue(diffs, f'{label}: expected nonzero diffs')

    # —— 3. 镜像内追加段 == 载荷源独立重汇编；追加头字段钉死 ——
    def test_03_segment_matches_sources(self):
        for tag, spec, hold in (('f1a', F1A, True), ('f1b', F1B, False)):
            _, _, plain = self.images[tag]
            rep, code, _slot = IMG.assemble_segment(
                close_hooks=True, slot_ram=True, r4_keyhook=True,
                l3_mpo=True, l4_eq=True, lfix=True, lfix_hold=hold)
            self.assertEqual(rep['code_bytes'], spec['code_bytes'])
            self.assertEqual(plain[SEG:SEG + spec['code_bytes']], code,
                             f'{tag}: embedded segment != re-assembled sources')
            # appended-file header fields: JLFS name + 0xff marker (the CRC
            # and size fields are recomputed and covered by the diff regions;
            # name/kind/flags/marker must be exactly the HAL5 precedent shape)
            hdr = plain[OLD_APP_END - FLASH_PLAIN_BASE: SEG]
            self.assertEqual(hdr[16:30], b'mic_effect.bin', 'append filename')
            self.assertEqual(hdr[12], 0x82, 'append file kind')
            self.assertEqual(hdr[13:16], b'\x00\x01\x00', 'append flags/index')
            self.assertEqual(plain[SEG - 1], 0xFF, 'append 0xff marker')

    # —— 4. 「仅差 A3」契约 ——
    def test_04_delta_is_a3_only(self):
        _, _, plain_a = self.images['f1a']
        _, _, plain_b = self.images['f1b']
        _, _, plain_h = self.images['hal5']
        seg_a = plain_a[SEG:SEG + F1A['code_bytes']]
        seg_b = plain_b[SEG:SEG + F1B['code_bytes']]
        seg_h = plain_h[SEG:SEG + 874]
        # F1b close payload == HAL5 close payload (byte-identical, no shift:
        # both assemble standalone with the same slot + firmware anchors)
        self.assertEqual(seg_b[F1B['close_off']:F1B['close_off'] + 64],
                         seg_h[156:220], 'F1b close != HAL5 stock close')
        # F1b carries no A3 anchors, no flag byte reference and no name bytes
        append_plain = (OLD_APP_END - FLASH_PLAIN_BASE,
                        F1B['new_app_end'] - FLASH_PLAIN_BASE)
        self.assertNotIn(b'lfix_hold', plain_b[append_plain[0]:append_plain[1]])
        for anchor in (CLOCK_ALLOC, CLOCK_FREE, LFIX_HOLD_FLAG):
            self.assertNotIn(struct.pack('<I', anchor), seg_b[:F1B['open']])
        # F1a carries exactly the A3 anchor set inside its open code
        open_a = seg_a[:F1A['open']]
        name_addr = 0x06000000 + SEG + F1A['close_off'] - F1A['name']
        for anchor, count in ((CLOCK_ALLOC, 1), (CLOCK_FREE, 1),
                              (name_addr, 2), (128000000, 1),
                              (LFIX_HOLD_FLAG, 3)):
            got = open_a.count(struct.pack('<I', anchor))
            self.assertEqual(got, count,
                             f'F1a open anchor {anchor:#x}: {got} != {count}')
        self.assertEqual(seg_a[F1A['close_off'] - F1A['name']:F1A['close_off']],
                         b'lfix_hold\x00')
        # F1a close carries the gated free (flag x2 + name addr + clock_free)
        close_a = seg_a[F1A['close_off']:F1A['close_off'] + F1A['close']]
        for anchor, count in ((CLOCK_FREE, 1), (name_addr, 1),
                              (LFIX_HOLD_FLAG, 2)):
            got = close_a.count(struct.pack('<I', anchor))
            self.assertEqual(got, count,
                             f'F1a close anchor {anchor:#x}: {got} != {count}')
        # normalized common-instruction stream: strip the 19 A3 instructions
        # from F1a's open and normalize branch targets — the remaining
        # instruction sequence must equal F1b's open EXACTLY (codex review:
        # anchor counting alone cannot catch a corrupted shared instruction)
        self._assert_normalized_open_equal(seg_a[:F1A['open']],
                                           seg_b[:F1B['open']])

    def _assert_normalized_open_equal(self, open_a, open_b):
        """A3-stripped normalized comparison of the two open payloads.

        Align the two NORMALIZED text streams (branch displacements replaced
        by T) with difflib — the instruction texts disambiguate repeated ids
        (e.g. `r4 = r0` vs `r0 = r4`); every insert opcode must be one of the
        19 A3 instructions and after removing them the streams must be
        IDENTICAL. This is the codex-review contract: anchor counting cannot
        catch a corrupted shared instruction, this can.
        """
        import difflib
        import re

        def norm(code):
            return [re.sub(r'goto -?\d+$', 'goto T', t)
                    for _a, _iid, t, _raw in ASM.disassemble(code, 0x060E3074)]

        a = norm(open_a)
        b = norm(open_b)
        name_addr = 0x06000000 + SEG + F1A['close_off'] - F1A['name']
        flag = LFIX_HOLD_FLAG
        a3_texts = [f'r0 = {name_addr}', 'r1 = 128000000',
                    f'r6 = {CLOCK_ALLOC}', 'call r6', 'r5 = r0',
                    f'r0 = {flag}', 'if (r5 != 0) goto T', 'r5 = 1',
                    'b[r0+0] = r5', 'r0 = r4',
                    f'r0 = {flag}', 'r5 = b[r0+0] (u)', 'if (r5 == 0) goto T',
                    f'r0 = {name_addr}', f'r6 = {CLOCK_FREE}', 'call r6',
                    f'r0 = {flag}', 'r5 = 0', 'b[r0+0] = r5']
        removed = []
        for op, _a1, a2, b1, b2 in difflib.SequenceMatcher(
                None, b, a, autojunk=False).get_opcodes():
            if op == 'insert':
                removed.extend(a[b1:b2])
            elif op != 'equal':
                raise AssertionError(f'open streams are not insert-only: {op}')
        # insert-only alignment + the inserted multiset being exactly the 19
        # A3 instructions == "F1a open is F1b open plus the A3 blocks" over
        # normalized texts (branch targets abstracted)
        self.assertEqual(sorted(removed), sorted(a3_texts),
                         'A3 delta is not exactly the pinned A3 instruction set')

    # —— 5. A1 循环接线（双产物都解码，五条边全覆盖，独立于构建器）——
    def test_05_a1_loop_wiring(self):
        for tag, spec in (('f1b', F1B), ('f1a', F1A)):
            _, _, plain = self.images[tag]
            seg = plain[SEG:SEG + spec['code_bytes']]
            open_code = seg[:spec['open']]
            insns = list(ASM.disassemble(open_code, 0x060E3074))
            by_addr = {a: (iid, t, raw) for a, iid, t, raw in insns}
            texts = [t for _a, _i, t, _r in insns]
            for needed in ('r4 = [r6+500]', 'r0 = b[r4+29] (u)',
                           'r0 = b[r4+33] (u)', 'r5 += -1', 'r0 = 1'):
                self.assertIn(needed, texts, f'{tag}: {needed}')
            # delay fn loaded once; the poll tail is r0=1; call r6; goto poll
            delay_loads = [i for i, t in enumerate(texts)
                           if t.startswith(f'r6 = {DELAY_FN}')]
            self.assertEqual(len(delay_loads), 1, tag)
            i1 = texts.index('r0 = 1')
            self.assertEqual(texts[i1 + 1], 'call r6', tag)
            at = [(i[0], i[2]) for i in insns]
            addr_cancel = next(a for a, t in at if t == 'r0 = -22')
            addr_poll = next(a for a, t in at if t == 'r0 = b[r4+29] (u)')
            addr_uuid = next(a for a, t in at if t == 'r0 = 15')
            addr_count = next(a for a, t in at if t == 'r5 += -1')
            # edge 1: ctx == 0 -> straight to the uuid setup (skip the wait)
            ctx0 = next((a, t, raw) for a, (iid, t, raw) in by_addr.items()
                        if iid == 'beqz' and t.startswith('if (r4 == 0)'))
            self.assertEqual(_branch_target(ctx0[0], ctx0[2], ctx0[1]),
                             addr_uuid, f'{tag}: ctx==0 skip does not reach uuid')
            # edge 2: transition busy (+29 != 0) -> the countdown, NOT open
            busy = next((a, t, raw) for a, (iid, t, raw) in by_addr.items()
                        if iid == 'bnez' and t.startswith('if (r0 != 0)')
                        and by_addr[a - 4][1].startswith('r0 = b[r4+29]'))
            self.assertEqual(_branch_target(busy[0], busy[2], busy[1]),
                             addr_count,
                             f'{tag}: busy branch does not reach the countdown')
            # edge 3: settled (+33 == 1, +29 == 0) -> uuid setup
            settled = next((a, t, raw) for a, (iid, t, raw) in by_addr.items()
                           if iid == 'beq.ri' and t.startswith('if (r0 == 1)'))
            self.assertEqual(_branch_target(settled[0], settled[2], settled[1]),
                             addr_uuid, f'{tag}: settled branch misses uuid')
            # edge 4: countdown exhausted -> the cancel path (r0 = -22)
            timeout = next((a, t, raw) for a, (iid, t, raw) in by_addr.items()
                           if iid == 'beqz' and t.startswith('if (r5 == 0)'))
            self.assertEqual(_branch_target(timeout[0], timeout[2], timeout[1]),
                             addr_cancel, f'{tag}: timeout misses the cancel path')
            # edge 5: retry goto returns exactly to the poll head
            retry = [a for a, (iid, t, raw) in by_addr.items()
                     if iid == 'goto.s' and _branch_target(a, raw, t) == addr_poll]
            self.assertEqual(len(retry), 1,
                             f'{tag}: retry goto does not return to the poll head')

    # —— 6. 预算 ——
    def test_06_append_budget(self):
        for tag, spec in (('f1a', F1A), ('f1b', F1B)):
            end = self.builds[tag]['report']['hearing_aid_l1']['new_app_end']
            self.assertEqual(end, spec['new_app_end'])
            self.assertLessEqual(end, APPEND_AREA_END,
                                 f'{tag} exceeds the append area budget')

    # —— 7. 独立 CRC/JLFS 校验器 ——
    def test_07_ufw_integrity(self):
        for tag in ('f1a', 'f1b'):
            proc = subprocess.run(
                [sys.executable, str(ROOT / 'tools/verify_ufw_integrity.py'),
                 str(self.builds[tag]['ufw'])],
                capture_output=True, text=True, timeout=120)
            self.assertEqual(proc.returncode, 0,
                             f'{tag}: integrity verifier rejected the image')

    # —— 8. 反例：预期区外翻 1 字节必须红（codex 复核：对照须用 HAL5——
    #     F1b 对官方的合法差异含 stream.bin L2 烘焙区，不在本测试区域集内）——
    def test_08_tamper_detected(self):
        hal5 = self.builds['hal5']['ufw'].read_bytes()
        clean = self.builds['f1b']['ufw'].read_bytes()
        # sanity: the untampered F1b vs HAL5 diff passes (all in regions)
        _assert_diffs_in_regions(self, clean, hal5, F1B['new_app_end'], 'clean')
        tampered = bytearray(clean)
        tampered[FLASH_FILE_OFF + 0x500] ^= 0xFF       # unrelated code byte
        with self.assertRaises(AssertionError) as cm:
            _assert_diffs_in_regions(self, bytes(tampered), hal5,
                                     F1B['new_app_end'], 'tampered')
        self.assertIn('0xb00', str(cm.exception))

    # —— 9. 基座未受扰动：HAL5 复现逐字节 ——
    def test_09_hal5_base_untouched(self):
        import hashlib
        digest = hashlib.sha256(self.builds['hal5']['ufw'].read_bytes()).hexdigest()
        self.assertEqual(digest, HAL5_SHA)


def _branch_target(addr, raw, text):
    """Branch/goto target from the disassembler's byte displacement."""
    import re
    m = re.search(r'goto (-?\d+)$', text)
    if m is None:
        raise AssertionError(f'not a branch: {text}')
    return addr + len(raw) + int(m.group(1))


if __name__ == '__main__':
    unittest.main(verbosity=2)
