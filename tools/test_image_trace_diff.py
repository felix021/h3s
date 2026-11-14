#!/usr/bin/env python3
"""Unit tests for the trace-array reconvergence/parity logic in
analyze_image_trace (synthetic traces only; no simulator, no ROM, no images).

Row shape mirrors TraceMachine: (core, before_pc, next_pc, flags,
16-tuple of register signatures, tuple of (address, data-hex) writes).
"""
import inspect

import frontier_registry
import probe_irq_entry
from analyze_image_trace import (BASE_SCENARIO_FLAGS, CONDITIONAL_EXTRA_FLAGS,
                                 SCENARIOS, find_reconvergence, stop_parity)


def row(pc, nxt, marker=(15, '00000000'), write=()):
    regs = ['00000000'] * 16
    idx, value = marker
    regs[idx] = value
    return (0, pc, nxt, '00', tuple(regs), tuple(write))


def common(count, start=0x100):
    return [row(start + i * 2, start + i * 2 + 2) for i in range(count)]


def tail(count, start=0x1000, markers=None, writes=None):
    markers = markers or {}
    writes = writes or {}
    return [row(start + i * 2, start + i * 2 + 2,
                marker=(15, markers.get(i, '00000000')), write=writes.get(i, ()))
            for i in range(count)]


def test_immediate_control_reconvergence_reports_lag_and_zero_divergence():
    a = common(10) + tail(20, 0x1000)
    b = common(10) + tail(3, 0x2000) + tail(20, 0x1000)
    detail = find_reconvergence(a, b, 10)
    assert detail['realigned'] and detail['immediate']
    assert detail['lag'] == 3
    assert (detail['realign_step_a'], detail['realign_step_b']) == (10, 13)
    assert detail['control_zero_divergence_steps'] == 20
    assert detail['extra_window'] == {'run': 'b', 'steps': [10, 13], 'count': 3, 'exact_position': True}
    assert detail['aligned_to_common_stop']
    signature = detail['signature_after_realignment']
    assert signature['divergent_span_count'] == 0 and signature['divergent_steps'] == 0
    assert signature['full_signature_realign_step_a'] == 10
    assert signature['full_signature_zero_divergence_steps'] == 20


def test_transient_signature_divergence_after_realignment_is_spanned():
    # same control flow after realignment, but a register signature differs for
    # the first five tail steps and one of them writes a different guest address
    a = common(4) + tail(12, 0x1000, markers={i: '00000001' for i in range(5)},
                        writes={2: (('0x2000', 'ff'),)})
    b = common(4) + tail(2, 0x2000) + tail(12, 0x1000, markers={i: '00000002' for i in range(5)},
                                            writes={2: (('0x3000', 'ff'),)})
    detail = find_reconvergence(a, b, 4)
    assert detail['realigned'] and detail['lag'] == 2
    signature = detail['signature_after_realignment']
    assert signature['divergent_span_count'] == 1
    assert signature['divergent_steps'] == 5
    assert signature['spans_with_differing_writes'] == 1
    assert signature['first'][0]['steps'] == [4, 9]
    assert signature['first'][0]['writes_differ']
    assert signature['first'][0]['registers'] == {'15': ['00000001', '00000002']}
    assert signature['full_signature_realign_step_a'] == 9
    assert signature['full_signature_realign_step_b'] == 11
    assert signature['full_signature_zero_divergence_steps'] == 7


def test_no_realignment_is_reported_honestly_when_tails_differ():
    a = common(6) + tail(8, 0x1000)
    b = common(6) + tail(3, 0x2000) + tail(8, 0x3000)
    detail = find_reconvergence(a, b, 6)
    assert detail['realigned'] is False
    assert 'no step lag realigns' in detail['note']


def test_no_realignment_when_only_the_final_step_differs():
    # the suffix scan must require EVERY remaining step to match: a differing
    # last step alone defeats realignment
    a = common(5) + tail(6, 0x1000)
    b = common(5) + tail(2, 0x2000) + tail(5, 0x1000) + [row(0x9990, 0x9992)]
    assert find_reconvergence(a, b, 5)['realigned'] is False


def test_negative_lag_when_the_first_run_is_longer():
    a = common(4) + tail(3, 0x2000) + tail(10, 0x1000)
    b = common(4) + tail(10, 0x1000)
    detail = find_reconvergence(a, b, 4)
    assert detail['realigned'] and detail['lag'] == -3
    assert detail['extra_window'] == {'run': 'a', 'steps': [4, 7], 'count': 3, 'exact_position': True}
    assert (detail['realign_step_a'], detail['realign_step_b']) == (7, 4)


def test_reconvergence_rejects_lag_that_leaves_trailing_steps_uncovered():
    # b both inserts two steps and appends two trailing steps: the exact length
    # delta (4) does not realign the tails, and any smaller lag would leave b's
    # trailing steps uncovered, so no realignment may be claimed
    a = common(4) + tail(8, 0x1000)
    b = common(4) + tail(2, 0x2000) + tail(8, 0x1000) + tail(2, 0x4000)
    assert find_reconvergence(a, b, 4)['realigned'] is False


def test_stop_parity_same_stop_and_step_deltas():
    def result(steps, core0, core1, pc, address='0xf27f4b'):
        return {'completed_steps': steps, 'stop_pc': pc,
                'stop': {'kind': 'unmapped_memory', 'access': 'read', 'address': address, 'size': 1},
                'machine': {'cores': {'0': {'steps': core0}, '1': {'steps': core1}}}}
    parity = stop_parity('tested_b79', result(289849, 278683, 11181, '0x60679ba'),
                         'eq_fence_repair', result(290307, 279141, 11181, '0x60679ba'))
    assert parity['steps'] == {'tested_b79': 289849, 'eq_fence_repair': 290307, 'delta': 458}
    assert parity['per_core_steps']['delta'] == {'0': 458, '1': 0}
    assert parity['stop_pc'] == {'a': '0x60679ba', 'b': '0x60679ba', 'same': True}
    assert parity['stop_record']['identical'] and parity['same_stop']
    other = stop_parity('a', result(10, 9, 1, '0x1'), 'b', result(10, 9, 1, '0x2'))
    assert other['stop_pc']['same'] is False and other['same_stop'] is False
    moved = stop_parity('a', result(10, 9, 1, '0x60679ba'), 'b', result(10, 9, 1, '0x60679ba', '0xf27f4c'))
    assert moved['stop_pc']['same'] is True and moved['same_stop'] is False


def test_flag_sets_derive_from_the_frontier_registry():
    # The imported shared stack derives from the single-source registry
    # (tools/frontier_registry.py) — the same source probe_irq_entry.main()
    # itself passes to irq_case, so cross-tool drift is impossible by
    # construction. What still needs pinning here: the registry stays
    # consistent with the real probe_irq_entry.irq_case signature (no
    # unregistered switch, no registry entry for a missing parameter) and the
    # data constants referenced by name are the real module values.
    assert frontier_registry.validate()
    signature = inspect.signature(probe_irq_entry.irq_case).parameters
    transports = {'flash_image_bytes', 'readonly'}  # per-run transports, not switches
    keyword_switches = {name for name, param in signature.items()
                        if param.kind == inspect.Parameter.KEYWORD_ONLY and name not in transports}
    assert keyword_switches == set(frontier_registry.switches(
        ('base', 'conditional', 'probe_only'))), \
        'probe_irq_entry.irq_case switches and frontier_registry entries diverged'
    assert set(BASE_SCENARIO_FLAGS) == set(frontier_registry.base_flags())
    assert set(CONDITIONAL_EXTRA_FLAGS) == set(frontier_registry.extra_flags(probe_irq_entry))
    # every base switch is a plain boolean opt-in
    assert all(value is True for value in BASE_SCENARIO_FLAGS.values())
    # the non-boolean conditional flags are exactly the five named data
    # fixtures, resolved to the real probe_irq_entry module constants (no copies)
    non_boolean = {switch for switch, value in CONDITIONAL_EXTRA_FLAGS.items() if value is not True}
    assert non_boolean == {'heap_info_bytes', 'lrct_trim_result', 'fd31_adc_res_named_values',
                           'jl_adda_daa_con1_initial_fixture', 'p33_canary_first_read_words'}
    assert CONDITIONAL_EXTRA_FLAGS['heap_info_bytes'] is probe_irq_entry.P11_HEAP_INFO_DERIVED
    assert CONDITIONAL_EXTRA_FLAGS['lrct_trim_result'] is probe_irq_entry.LRCT_TRIM_RESULT_PROBE
    # 路 BM/S19 批 1:条件场景改传扩展表(勘误 BA「trim 环采样 fd31✓」——
    # 0x10005 缺行,+0x10005:400+0x10001:4);BY/键链场景继续用旧表。
    assert CONDITIONAL_EXTRA_FLAGS['fd31_adc_res_named_values'] \
        is probe_irq_entry.FD31_ADC_RES_S19_VALUES
    assert dict(probe_irq_entry.FD31_ADC_RES_S19_VALUES) == {
        **probe_irq_entry.FD31_ADC_RES_NAMED_VALUES, 0x10005: 400, 0x10001: 4}
    assert CONDITIONAL_EXTRA_FLAGS['jl_adda_daa_con1_initial_fixture'] \
        is probe_irq_entry.JL_ADDA_DAA_CON1_INITIAL_PROBE
    assert CONDITIONAL_EXTRA_FLAGS['p33_canary_first_read_words'] \
        is probe_irq_entry.P33_CANARY_FIRST_READ_WORDS


def test_scenario_registry_shapes():
    # the conditional stack layers strictly on top of the base stack (same
    # base switches, no removals); the registry pins both freshness frontiers
    # and keeps the conditional budget above the conditional frontier.
    conditional = SCENARIOS['p11_heap_experimental']['irq_case_flags']
    assert conditional == {**BASE_SCENARIO_FLAGS, **CONDITIONAL_EXTRA_FLAGS}
    for key, value in BASE_SCENARIO_FLAGS.items():
        assert conditional[key] == value
    # freshness 停点字面值单点在 tools/frontier_registry.py(probe 的前沿
    # require、analyze 的 freshness 钉子同源);前沿推进史叙事见 HANDOFF
    # 「当前前沿」表与 registry 内注释,不在此复制。
    assert SCENARIOS['crc_bankb']['freshness'] == frontier_registry.freshness('crc_bankb')
    assert SCENARIOS['p11_heap_experimental']['freshness'] == \
        frontier_registry.freshness('p11_heap_experimental')
    assert SCENARIOS['crc_bankb']['default_budget'] == 300000
    assert SCENARIOS['p11_heap_experimental']['default_budget'] >= \
        SCENARIOS['p11_heap_experimental']['freshness']['completed_steps'] + 1000


if __name__ == '__main__':
    for name, case in sorted(globals().items()):
        if name.startswith('test_'):
            case()
            print('passed', name)
