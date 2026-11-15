"""Offline unit tests for the execution-layer memoization (br28_exec_memo).

No private ROM, no physical headset. Pins the behavior contract of route
OPT1: the wrappers are exact (same inputs return the object the original
call would have returned), the memo keys separate every distinct field-value
combination (self-modified code decodes to different values and misses), and
errors are never cached.
"""
import unittest

from br28_exec_memo import install, uninstall, _sim_namespace
from probe_pi32v2 import upstream


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def _cpu():
    return upstream().CPU()


class TakesStateTests(unittest.TestCase):
    def setUp(self):
        self.cpu = _cpu()
        require(install(self.cpu), 'installation failed')
        self.check = _sim_namespace(self.cpu)['check']
        self.original = self.check.takes_state.__wrapped__

    def tearDown(self):
        uninstall()

    def test_first_call_cache_matches_original(self):
        def three_params(a, b, c):
            return None

        def four_params(a, b, c, d):
            return None

        def four_with_default(a, b, c, d=1):
            return None

        for fn in (three_params, four_params, four_with_default):
            self.assertIs(self.check.takes_state(fn), self.check.takes_state(fn))
            self.assertEqual(self.check.takes_state(fn), self.original(fn))
        # 上游判定=必选参数(无默认值)≥4:four_with_default 只有 3 个必选。
        self.assertFalse(self.check.takes_state(three_params))
        self.assertTrue(self.check.takes_state(four_params))
        self.assertFalse(self.check.takes_state(four_with_default))

    def test_signature_error_is_not_cached_and_repeats(self):
        with self.assertRaises(ValueError):
            self.check.takes_state(type)  # no signature found for builtin type
        with self.assertRaises(ValueError):
            self.check.takes_state(type)


class OperandFormatTests(unittest.TestCase):
    def setUp(self):
        self.cpu = _cpu()
        require(install(self.cpu), 'installation failed')
        namespace = _sim_namespace(self.cpu)
        self.check = namespace['check']
        self.codec = namespace['codec']
        self.operands_original = self.check.operands.__wrapped__
        self.format_original = self.codec.format.__wrapped__
        # 取一批真实带字段的指令形态;enum 值表为空的跳过(无处取合法索引)。
        self.insns = [i for i in self.cpu.tab.insns
                      if i.fields and all(f.values or f.kind != 'enum'
                                          for f in i.fields.values())][:24]

    def tearDown(self):
        uninstall()

    def _raw(self, insn, seed):
        raw = {}
        for k, (name, f) in enumerate(insn.fields.items()):
            value = (seed + k) % (1 << 12)
            if f.kind == 'enum':
                value %= len(f.values)
            elif f.kind == 'sr':
                value %= 16
            raw[name] = value
        return raw

    def test_operands_exact_and_shared_on_repeat(self):
        for seed in (0, 5):
            for insn in self.insns:
                raw = self._raw(insn, seed)
                got = self.check.operands(insn, raw)
                self.assertEqual(got, self.operands_original(insn, raw))
                self.assertIs(got, self.check.operands(insn, raw))
                self.assertEqual(got['raw'], raw)  # original copies raw into op['raw']

    def test_operands_distinct_values_miss_the_memo(self):
        for insn in self.insns:
            first = self.check.operands(insn, self._raw(insn, 1))
            changed = self.check.operands(insn, self._raw(insn, 2))
            self.assertEqual(changed, self.operands_original(insn, self._raw(insn, 2)))
            self.assertIsNot(first, changed)

    def test_operands_do_not_depend_on_the_raw_dict_object(self):
        insn = self.insns[0]
        one = self.check.operands(insn, dict(self._raw(insn, 3)))
        two = self.check.operands(insn, dict(self._raw(insn, 3)))  # 等值的新 dict:同一条目
        self.assertIs(one, two)

    def test_format_exact_and_shared_on_repeat(self):
        for seed in (0, 5):
            for insn in self.insns:
                raw = self._raw(insn, seed)
                got = self.codec.format(insn, raw)
                self.assertEqual(got, self.format_original(insn, raw))
                self.assertIs(got, self.codec.format(insn, raw))

    def test_format_explicit_syntax_stays_exact(self):
        for insn in self.insns:
            raw = self._raw(insn, 4)
            self.assertEqual(self.codec.format(insn, raw, insn.syntax),
                             self.format_original(insn, raw, insn.syntax))


class InstallTests(unittest.TestCase):
    def setUp(self):
        uninstall()

    def tearDown(self):
        uninstall()

    def test_sim_namespace_finds_the_upstream_base_class(self):
        cpu = _cpu()
        namespace = _sim_namespace(cpu)
        require(namespace is not None, 'namespace not found')
        self.assertIn('check', namespace)
        self.assertIn('codec', namespace)
        # 探针层子类(effect 定义在别的命名空间)也能定位上游基类。
        probe_like = type('ProbeLike', (type(cpu),), {})
        self.assertIs(_sim_namespace(probe_like()), namespace)

    def test_install_is_idempotent_and_uninstall_restores(self):
        cpu = _cpu()
        namespace = _sim_namespace(cpu)
        check, codec = namespace['check'], namespace['codec']
        before = (check.takes_state, check.operands, codec.format)
        self.assertTrue(install(cpu))
        wrapped = (check.takes_state, check.operands, codec.format)
        self.assertIsNot(check.takes_state, before[0])
        self.assertTrue(install(cpu))  # 第二次装配不再替换
        self.assertIs(check.takes_state, wrapped[0])
        self.assertIs(check.operands, wrapped[1])
        self.assertIs(codec.format, wrapped[2])
        uninstall()
        self.assertEqual((check.takes_state, check.operands, codec.format), before)

    def test_no_namespace_leaves_everything_untouched(self):
        class NotASim:
            def effect(self):
                return None

        self.assertIsNone(_sim_namespace(NotASim()))
        self.assertFalse(install(NotASim()))


if __name__ == '__main__':
    unittest.main()
