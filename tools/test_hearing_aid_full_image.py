#!/usr/bin/env python3
"""Offline verification of the hearing-aid FULL combined candidate (route HAL5).

Layer above test_hearing_aid_l4_eq_image.py: the L1+L2+L3+L4 合一 candidate
(--hearing-aid-full --hearing-aid-mpo M). The route's core contract is that
full adds ZERO new payload bytes — its firmware bytes must equal the legacy
combination (--hearing-aid-l4-eq --hearing-aid-mpo M) because the L4 toggle
stub already chains open→apply_mpo→apply_eq (the L1 stub is its instruction
prefix; the three stubs graft the same KEY7 window 0x06025404, which is why
the standalone l1-key7 switch is mutually exclusive with the l3/l4 chain).

Tiers:
- CLI contract (always runs): full requires an explicit finite MPO in
  (0.3, 120) — the same consumption range as VM key 30 — and rejects
  --hearing-aid-demo (shared Eq blob on the post-MPO output branches),
  --hearing-aid-l1-key7 (same graft window) and --eq-toggle-elf (append
  conflict).
- Artifact tier (needs firmware/ + analysis/v1.1.8/ + jl-misctools +
  pi32v2-isa; skips otherwise): builds full M=95/100/115, the legacy combo
  and the L4-only candidate, then asserts
  1. byte identity full == legacy combo (the zero-new-payload contract),
  2. the appended segment is identical to the L4-only candidate's
     (code sha, addresses, fence, registry),
  3. filename + hearing_aid_full report keys (priority semantics present),
  4. an INDEPENDENT plain-space bucket audit: the candidate is decoded from
     the finished .ufw (shared loader, no builder import) and every changed
     byte is attributed to exactly one bucket — L1 base / L3 consume / L4
     consume / shared graft / L2 param / packaging — with the deterministic
     regions re-derived from the decoded bytes (KEY7 window decode, fence
     immediate, registry pointer, knee floats, full 76B readback, rodata
     blobs, stub shape + call targets, keyhook == standalone assembly);
  5. official→full minus official→L4 equals exactly the L2 bucket;
  6. both baked-tail branches (M<110.3 keeps th[2].in at 110.3f; M≥110.3
     raises it to M+10.3) plus an independent curve-semantics check
     (zero tail slope, overdrive extrapolation ≤ M);
  7. the independent CRC/JLFS verifier accepts the image;
  8. negatives: one tampered byte outside every bucket turns the audit red,
     and the L4-only candidate (no L2 bake) fed to the full audit is also
     rejected.

UNVALIDATED ON DEVICE: the candidate stays OFFLINE_UNTESTED; this file never
flashes or simulates anything. The r4_keyhook family byte pin (2576df46…)
lives in test_candidate_world_checks.py, which rebuilds that candidate —
full changes no keystore_hook bytes, so no re-pin is required.
"""
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

# —— 合一候选的固定几何（docs/hearing-aid-l5-full.md §3；与 L4 候选相同）——
PAYLOAD_ADDRESS = 0x060E3074          # appended segment runtime base
SEG = PAYLOAD_ADDRESS - 0x06000000    # 0xE3074, plain coords
KEY7_WINDOW = 0x25404                 # plain coords (runtime 0x06025404)
FENCE_IMM = 0x34E9A                   # LIM_PC0 upper-limit immediate (+2 of site)
REGISTRY_SLOT = 0xA9C7C               # cmdid 0x30 entry+8 setter word
CONFIG_DAT_CRC = (0xE2ED3, 0xE2ED5)   # last-file header CRC span
CONFIG_DAT_INDEX = (0xE2EE1, 0xE2EE2) # last-file index byte span
APPEND_HEADER = (0xE3053, 0xE3074)    # mic_effect.bin 32B header + 0xff marker
STREAM_DATA = 0xE0A4C                 # stream.bin payload base (flash 0xe2a6c)
STREAM_ENTRY_CRC = (0xE0A2C, 0xE0A30) # stream JLFS entry CRC span (flash 0xe2a4c)
DRC_KNEES = 0x18B9                    # th[1].in offset within stream.bin
TOGGLE_ADDR = 0x060E331C
KEYHOOK_ADDR = 0x060E3350
FENCE_TOP = 0x060E33DE
# segment spans (offsets within the 874B code segment)
SPANS = {
    'open': (0, 156), 'close': (156, 220),
    'apply_mpo': (220, 348), 'mpo_rodata': (348, 440),
    'apply_eq': (440, 620), 'eq_rodata': (620, 680),
    'toggle': (680, 732), 'keyhook': (732, 874),
}
# boot-info CRC/size fields (flash coords 0x2000..0x2020) that legitimately
# change: header CRC (2B) / plain CRC (2B) / cipher CRC (2B) / app size (4B)
BOOT_FIELDS = [(0x2000, 0x2006), (0x2008, 0x200C)]


def f32(v):
    return struct.unpack('<f', struct.pack('<f', v))[0]


def _run_builder(args, outdir):
    return subprocess.run([sys.executable, BUILDER, *args,
                           '--output-dir', str(outdir)],
                          capture_output=True, text=True, timeout=300)


class FullCliContractTest(unittest.TestCase):
    """full 的开关契约：M 必填、范围门、demo/l1-key7/eq-elf 互斥（路 HAL5 §2）。"""

    def _reject(self, args, fragment):
        with tempfile.TemporaryDirectory() as tmp:
            proc = _run_builder(['--attenuation-db', '0', *args], tmp)
        self.assertNotEqual(proc.returncode, 0, f'builder accepted {args}')
        self.assertIn(fragment, proc.stderr)

    def test_01_requires_explicit_mpo(self):
        self._reject(['--hearing-aid-full'],
                     'requires an explicit --hearing-aid-mpo')

    def test_02_mpo_range_gate(self):
        for bad in ('0.3', '0.2', '120', '121', 'nan', 'inf', '-5',
                    '0.300000001',    # 舍入到 0.3f，必须在 CLI 门拒绝（不能走到 ValueError）
                    '1e100'):         # f32 转换溢出前置挡（不能 OverflowError 崩栈）
            self._reject(['--hearing-aid-full', '--hearing-aid-mpo', bad],
                         '0.3 < --hearing-aid-mpo < 120', )

    def test_03_demo_mutually_exclusive(self):
        self._reject(['--hearing-aid-full', '--hearing-aid-mpo', '95',
                      '--hearing-aid-demo'],
                     'mutually exclusive')

    def test_04_key7_window_mutually_exclusive(self):
        self._reject(['--hearing-aid-full', '--hearing-aid-mpo', '95',
                      '--hearing-aid-l1-key7'],
                     'share the KEY7 graft window')

    def test_05_eq_toggle_elf_conflict(self):
        self._reject(['--hearing-aid-full', '--hearing-aid-mpo', '95',
                      '--eq-toggle-elf', '/nonexistent.elf',
                      '--anc-cycle-two-state'],
                     'append at the same flash offset')


@unittest.skipUnless(ASSETS_READY, 'official firmware assets not materialized')
class FullArtifactTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = FIRMWARE.read_bytes()
        cls.tmp = tempfile.TemporaryDirectory()
        base = Path(cls.tmp.name)
        cls.builds = {}
        for tag, args in (('full95', ['--hearing-aid-full', '--hearing-aid-mpo', '95']),
                          ('full100', ['--hearing-aid-full', '--hearing-aid-mpo', '100']),
                          ('full115', ['--hearing-aid-full', '--hearing-aid-mpo', '115']),
                          ('combo95', ['--hearing-aid-l4-eq', '--hearing-aid-mpo', '95']),
                          ('l4only', ['--hearing-aid-l4-eq'])):
            out = base / tag
            proc = _run_builder(['--attenuation-db', '0', *args], out)
            assert proc.returncode == 0, f'{tag} build failed: {proc.stderr[-400:]}'
            cls.builds[tag] = {'ufw': next(out.glob('*.ufw')),
                               'stream': out / 'stream.bin',
                               'report': __import__('json').loads(
                                   (out / 'build-report.json').read_text())}
        from verify_eq_candidate import load_image
        cls.images = {}
        cls.images['official'] = load_image(FIRMWARE)
        for tag in ('full95', 'l4only', 'full115'):
            cls.images[tag] = load_image(cls.builds[tag]['ufw'])

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    # —— 1. 零新增载荷契约：full 字节 == 旧组合 ——
    def test_01_bytes_equal_legacy_combo(self):
        self.assertEqual(self.builds['full95']['ufw'].read_bytes(),
                         self.builds['combo95']['ufw'].read_bytes())
        self.assertEqual(self.builds['full95']['stream'].read_bytes(),
                         self.builds['combo95']['stream'].read_bytes())

    # —— 2. 追加段与 L4-only 候选逐字节一致（地址/fence/注册表不变）——
    def test_02_segment_identical_to_l4(self):
        a = self.builds['full95']['report']['hearing_aid_l1']
        b = self.builds['l4only']['report']['hearing_aid_l1']
        for key in ('code_sha256', 'code_bytes', 'toggle_address',
                    'apply_mpo_address', 'apply_eq_address',
                    'r4_keyhook_address', 'segment_end_runtime',
                    'slot_address', 'variant'):
            self.assertEqual(a[key], b[key], key)
        self.assertEqual(a['variant'], 'open+close+slotram+l4eq')
        self.assertEqual(a['code_bytes'], 874)
        self.assertEqual(a['segment_end_runtime'], '0x060e33de')

    # —— 3. 命名与合一语义报告 ——
    def test_03_filename_and_full_report(self):
        self.assertIn('HEARING_AID_FULL_MPO95_OFFLINE_UNTESTED',
                      self.builds['full95']['ufw'].name)
        rep = self.builds['full95']['report']
        full = rep['hearing_aid_full']
        self.assertEqual(full['mode'],
                         'l1_key7_toggle + l2_baked_mpo + l3_key30_mpo + l4_key31_eq')
        self.assertEqual(full['baked_mpo_db'], f32(95.0))
        self.assertEqual(full['baked_curve'],
                         [[f32(0.3), f32(0.3)], [f32(95.0), f32(95.0)],
                          [f32(110.3), f32(95.0)]])
        for key in ('runtime_priority', 'semantics_by_key30_state',
                    'semantics_note', 'non_atomic_caveats', 'tail_point_note'):
            self.assertTrue(full[key], key)
        self.assertIn('key30', full['runtime_priority'])
        self.assertEqual(full['equivalent_build'], '--hearing-aid-l4-eq --hearing-aid-mpo 95')
        self.assertEqual(full['status'], 'OFFLINE_UNTESTED')
        # 报告曲线 == 镜像实况（从最终 .ufw 解码读回），杜绝报告公式漂移
        plain = self.images['full95'][2]
        got = struct.unpack_from('<ffff', plain, STREAM_DATA + DRC_KNEES)
        curve = full['baked_curve']
        self.assertEqual(got, (curve[1][0], curve[1][1], curve[2][0], curve[2][1]))
        # L2 层仍由 hearing_aid_demo 键承载（历史命名），模式必须是纯 mpo
        self.assertEqual(rep['hearing_aid_demo']['mode'], 'mpo')

    # —— 独立分桶审计（从最终 .ufw 解码，不 import 构建器）——
    @classmethod
    def _reference_segment(cls):
        """从 .S 源重汇编 874B 追加段（类级缓存）。分桶计数/形状不能替代字节
        身份：槽地址、调用目标、立即数全部要靠这份数据钉死（codex 复核
        202611180041-hal5rev #1——改一个槽操作数重算 CRC 后计数全不变）。"""
        if '_ref_seg' not in cls.__dict__:
            _report, code, _slot = IMG.assemble_segment(
                close_hooks=True, slot_ram=True, r4_keyhook=True,
                l3_mpo=True, l4_eq=True)
            cls._ref_seg = code
        return cls._ref_seg

    def _buckets(self, mpo):
        """六个互不重叠的桶（plain 坐标）+ boot/UFW 外围账。"""
        span = lambda name: range(SEG + SPANS[name][0], SEG + SPANS[name][1])
        l2 = set()
        base = STREAM_DATA + DRC_KNEES
        l2 |= set(range(base, base + 16))        # th[1].in/out + th[2].in/out
        buckets = {
            'L1_base': set(range(KEY7_WINDOW, KEY7_WINDOW + 8))
                       | set(span('open')) | set(span('close')),
            'L3_consume': set(span('apply_mpo')) | set(span('mpo_rodata')),
            'L4_consume': set(span('apply_eq')) | set(span('eq_rodata')),
            'shared_graft': set(span('toggle')) | set(span('keyhook'))
                            | set(range(REGISTRY_SLOT, REGISTRY_SLOT + 4)),
            'L2_param': l2 | set(range(*STREAM_ENTRY_CRC)),
            'packaging': set(range(FENCE_IMM, FENCE_IMM + 4))
                         | set(range(*APPEND_HEADER))
                         | set(range(*CONFIG_DAT_CRC))
                         | set(range(*CONFIG_DAT_INDEX))
                         | set(range(0x0, 0x4)) | set(range(0x20, 0x24)),
        }
        return buckets

    def _audit(self, tag, mpo):
        """对解码后的候选做分桶审计；返回 (分桶计数, 内容账差异数)。任何越桶/
        值不符抛 AssertionError。"""
        (_oraw, _oflash, oplain), (_raw, _flash, plain) = (
            self.images['official'], self.images[tag])
        n = min(len(oplain), len(plain))
        diff = {i for i in range(n) if oplain[i] != plain[i]}
        diff |= {i for i in range(n, len(plain)) if plain[i] != 0xff}
        buckets = self._buckets(mpo)
        owner = {}
        for name, s in buckets.items():
            for i in s:
                self.assertNotIn(i, owner, f'bucket overlap at {i:#x}')
                owner[i] = name
        counts = {}
        for i in diff:
            self.assertIn(i, owner, f'changed byte {i:#x} outside every bucket')
            counts[owner[i]] = counts.get(owner[i], 0) + 1
        # —— 确定性区域的值必须从解码字节重新导出（不是只看「在桶里」）——
        # KEY7 窗口：r6=<toggle>; goto r6
        window = plain[KEY7_WINDOW:KEY7_WINDOW + 8]
        decoded = list(ASM.disassemble(window, 0x06025404))
        self.assertEqual([d[1] for d in decoded], ['li.u32', 'goto.r'])
        self.assertEqual(decoded[0][2], f'r6 = {TOGGLE_ADDR}')
        self.assertEqual(decoded[1][2], 'goto r6')
        # fence 上限立即数 / 注册表 setter 指针
        self.assertEqual(struct.unpack_from('<I', plain, FENCE_IMM)[0], FENCE_TOP)
        self.assertEqual(struct.unpack_from('<I', plain, REGISTRY_SLOT)[0], KEYHOOK_ADDR)
        # L2 曲线：四个拐点字段 + 76B 其余部分与官方逐字节一致
        base = STREAM_DATA + DRC_KNEES
        x_tail = f32(110.3) if f32(mpo) < f32(110.3) else f32(mpo + 10.3)
        got = struct.unpack_from('<ffff', plain, base)
        self.assertEqual(got, (f32(mpo), f32(mpo), x_tail, f32(mpo)))
        drc = slice(base - 8, base - 8 + 76)
        self.assertEqual(plain[drc][:8], oplain[drc][:8])        # bypass/attack/release
        self.assertEqual(plain[drc][24:], oplain[drc][24:])     # th[3..5]/gains/rms/tnum...
        # —— 追加段结构：整段 874B 与源码重汇编逐字节一致（字节身份），rodata
        #    模板 + 桩形状 + keyhook 作为可读的分层复核 ——
        self.assertEqual(plain[SEG:SEG + 874], self._reference_segment())
        self.assertEqual(plain[SEG + 348:SEG + 424], IMG.MPO_TEMPLATE_EXPECTED)
        self.assertEqual(plain[SEG + 424:SEG + 440], IMG.MPO_NAME_EXPECTED)
        self.assertEqual(plain[SEG + 620:SEG + 664], IMG.EQ_PKT_EXPECTED)
        self.assertEqual(plain[SEG + 664:SEG + 680], IMG.EQ_NAME_EXPECTED)
        stub = plain[SEG + 680:SEG + 732]
        shape = [iid for _a, iid, _t, _r in ASM.disassemble(stub, TOGGLE_ADDR)]
        self.assertEqual(shape, IMG.L4_TOGGLE_INSN_IDS)
        self.assertEqual(plain[SEG + 732:SEG + 874],
                         IMG._assemble(IMG.R4_KEYHOOK_SOURCE.read_text())[1])
        # —— 外围账：boot CRC/size 字段 + UFW 外层 CRC ——
        _r, oflash = self.images['official'][0], self.images['official'][1]
        flash = self.images[tag][1]
        boot_allowed = set()
        for lo, hi in BOOT_FIELDS:
            boot_allowed |= set(range(lo, hi))
        boot_diff = {i for i in range(0x2000, 0x2020) if oflash[i] != flash[i]}
        self.assertTrue(boot_diff <= boot_allowed,
                        f'boot-info bytes outside CRC/size fields: '
                        f'{sorted(boot_diff - boot_allowed)}')
        # —— 文件级守卫（codex 复核 #2：整文件枚举差异，不能只在六个允许位里
        #    找差异——那是恒真断言）。允许集 = UFW 外层 CRC 6B + 追加段覆盖的
        #    flash 区 [boot 32B, 新 app 末)。UFW 头/条目区、bootloader、flash
        #    尾、其他资源条目必须零差异。 ——
        raw, oraw = self.images[tag][0], self.images['official'][0]
        file_diff = {i for i in range(len(raw)) if raw[i] != oraw[i]}
        allowed = {0, 1, 2, 3, 68, 69}
        allowed |= set(range(0x600 + 0x2000, 0x600 + 0x2020 + len(plain)))
        stray = file_diff - allowed
        self.assertFalse(stray, f'file bytes changed outside the guarded regions: '
                                f'{sorted(stray)[:12]}')
        ufw_diff = {i for i in (0, 1, 2, 3, 68, 69) if raw[i] != oraw[i]}
        content = len(diff) + len(boot_diff) + len(ufw_diff)
        return counts, content

    def test_04_independent_bucket_audit_m95(self):
        counts, content = self._audit('full95', 95)
        # 钉死内容账（plain+boot+UFW 坐标；与文件坐标的 956B 是两种口径，见
        # test_05）：任何载荷/参数变化都会打破这些计数
        self.assertEqual(counts, {'L1_base': 208, 'L3_consume': 213,
                                  'L4_consume': 234, 'shared_graft': 189,
                                  'L2_param': 13, 'packaging': 45})
        self.assertEqual(content, 916)

    def test_05_builder_file_diff_count_reconciles(self):
        rep = self.builds['full95']['report']
        self.assertEqual(rep['hearing_aid_l1']['byte_diff']['diff_count'], 956)
        raw = self.builds['full95']['ufw'].read_bytes()
        file_diff = sum(1 for a, b in zip(self.source, raw) if a != b)
        self.assertEqual(file_diff, 956)

    def test_06_incremental_vs_l4_is_exactly_l2(self):
        (_r, _f, oplain), (_r2, _f2, plain), (_r3, _f3, l4) = (
            self.images['official'], self.images['full95'], self.images['l4only'])
        df = {i for i in range(len(oplain)) if oplain[i] != plain[i]}
        d4 = {i for i in range(len(oplain)) if oplain[i] != l4[i]}
        l2 = self._buckets(95)['L2_param']
        self.assertEqual(df - d4, {i for i in l2 if oplain[i] != plain[i]})
        self.assertFalse(d4 - df, 'L4 bytes must be a subset of full')

    def test_07_tail_branch_coverage(self):
        """M=100 与 M=115 覆盖 X=110.3 / X=M+10.3 两分支 + 曲线语义独立检查。"""
        for tag, m in (('full100', 100.0), ('full115', 115.0)):
            stream = self.builds[tag]['stream'].read_bytes()
            base = DRC_KNEES                       # th[1].in within stream.bin
            baked = f32(m)                         # 先 float32 化再加 10.3（与固件写入同式）
            x = f32(110.3) if baked < f32(110.3) else f32(baked + 10.3)
            self.assertEqual(struct.unpack_from('<ffff', stream, base),
                             (f32(m), f32(m), x, f32(m)))
            # 独立曲线语义（L2 §1）：末段斜率 0、截距 M、过驱动外推 ≤ M
            pts = [(f32(0.3), f32(0.3)), (f32(m), f32(m)), (x, f32(m))]
            slopes = [(pts[i+1][1]-pts[i][1])/(pts[i+1][0]-pts[i][0]) for i in range(2)]
            self.assertEqual(slopes[-1], 0.0)
            for probe in (m, x, x + 30.0, x + 300.0):
                seg = 1 if probe > pts[1][0] else 0
                b = pts[seg+1][1] - slopes[seg] * pts[seg+1][0]
                self.assertLessEqual(slopes[seg] * probe + b, m + 1e-4)
        # M=115 时 th[2].in 也变（110.3f -> 125.3f）：12 参数字节 + 2 个 CRC
        # 字节（另 2 个偶合不变——计数是钉死工件，不是公式）
        counts115, _ = self._audit('full115', 115)
        self.assertEqual(counts115['L2_param'], 14)

    def test_08_independent_verifier_green(self):
        from verify_ufw_integrity import verify
        report = verify(self.builds['full95']['ufw'])
        self.assertTrue(report['passed'], report['errors'])

    def test_09_negative_tamper_outside_buckets(self):
        """桶外翻转 1 字节（重算 CRC 无关——审计直接读明文/整文件）必须被拒绝。
        同理可推：即使篡改追加段操作数（如槽地址）并重算全部 CRC 使计数不变，
        _audit 的 874B 字节身份断言（==源码重汇编）也会拒绝——计数不能替代
        字节身份（codex 复核 202611180041-hal5rev #1/#2）。"""
        raw = bytearray(self.builds['full95']['ufw'].read_bytes())
        plain_pos = 0x25000                    # 与所有桶不相干的 app 代码区
        raw[0x600 + 0x2020 + plain_pos] ^= 0x5a
        tampered = Path(self.tmp.name) / 'tampered.ufw'
        tampered.write_bytes(raw)
        from verify_eq_candidate import load_image
        self.images['tampered'] = load_image(tampered)
        with self.assertRaises(AssertionError, msg='outside every bucket'):
            self._audit('tampered', 95)

    def test_10_negative_l4_only_fails_full_audit(self):
        """去掉 L2 烘焙层的候选喂给 full 审计：拐点值断言必须拒绝。"""
        with self.assertRaises(AssertionError):
            self._audit('l4only', 95)


if __name__ == '__main__':
    unittest.main()
