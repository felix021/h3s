#!/usr/bin/env python3
"""Unit tests for the trace-array reconvergence/parity logic in
analyze_image_trace (synthetic traces only; no simulator, no ROM, no images).

Row shape mirrors TraceMachine: (core, before_pc, next_pc, flags,
16-tuple of register signatures, tuple of (address, data-hex) writes).
"""
import ast

import probe_irq_entry
from analyze_image_trace import (BASE_SCENARIO_FLAGS, CONDITIONAL_EXTRA_FLAGS,
                                 SCENARIOS, find_reconvergence, stop_parity)
from probe_pi32v2 import ROOT


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


def _scenario_call_flags(scenario_call_name):
    """Extract the irq_case keyword set for ONE scenario call site inside
    probe_irq_entry.main() by AST (no simulator run). Name-referenced values
    (P11_HEAP_INFO_DERIVED / LRCT_TRIM_RESULT_PROBE module constants, the
    local `image` transport) are resolved against the probe_irq_entry module
    namespace; the `image` name is a local, so callers drop the transported
    flash_image_bytes/readonly keys before comparing flag sets."""
    source = (ROOT / 'tools/probe_irq_entry.py').read_text()
    matches = []
    for node in ast.walk(ast.parse(source)):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == 'irq_case' and len(node.args) >= 5
                and isinstance(node.args[4], ast.Constant)
                and node.args[4].value == scenario_call_name):
            matches.append(node)
    assert len(matches) == 1, f'expected exactly one irq_case call for {scenario_call_name!r}'
    flags = {}
    for kw in matches[0].keywords:
        if kw.arg in ('flash_image_bytes', 'readonly'):
            continue  # per-run transports (local `image`), not scenario switches
        if isinstance(kw.value, ast.Constant):
            flags[kw.arg] = kw.value.value
        elif isinstance(kw.value, ast.Name):
            flags[kw.arg] = getattr(probe_irq_entry, kw.value.id)
        else:
            flags[kw.arg] = '<unparsed>'
    return flags


def test_flag_sets_match_probe_irq_entry_scenario_calls():
    # The imported shared stack must be exactly the upstream scenario
    # construction: if probe_irq_entry gains/renames an opt-in flag for either
    # scenario, this fails instead of silently diffing under a stale stack.
    assert _scenario_call_flags('crc_bankb_erased_fixture') == SCENARIOS['crc_bankb']['irq_case_flags']
    assert _scenario_call_flags('p11_heap_experimental_config') == \
        SCENARIOS['p11_heap_experimental']['irq_case_flags']
    # the imported data constants are the real module values, not copies
    assert SCENARIOS['p11_heap_experimental']['irq_case_flags']['heap_info_bytes'] \
        is probe_irq_entry.P11_HEAP_INFO_DERIVED
    assert SCENARIOS['p11_heap_experimental']['irq_case_flags']['lrct_trim_result'] \
        is probe_irq_entry.LRCT_TRIM_RESULT_PROBE


def test_scenario_registry_shapes():
    # the conditional stack layers strictly on top of the base stack (same
    # base switches, no removals); the registry pins both freshness frontiers
    # and keeps the conditional budget above the conditional frontier.
    conditional = SCENARIOS['p11_heap_experimental']['irq_case_flags']
    assert conditional == {**BASE_SCENARIO_FLAGS, **CONDITIONAL_EXTRA_FLAGS}
    for key, value in BASE_SCENARIO_FLAGS.items():
        assert conditional[key] == value
    assert SCENARIOS['crc_bankb']['freshness'] == {
        'completed_steps': 289849, 'stop_pc': '0x60679ba',
        'stop_fields': {'kind': 'unmapped_memory', 'address': '0xf27f4b'}}
    # 路 BY:PND 完成投影折叠 80 次轮询、RES 具名 fixture 放行采样事务,
    # 前沿 447,383 @ P3_ANA_READ 首读(BV 钉住的红线)。路 CB:p3_ana_read_
    # ldoin_low 具名常量 fixture(SDK charge_hw.h bit1=LVCMP_DET/bit2=
    # LDO5V_DET+charge.c 阈值+无外部供电锚)放行首读,L5V/CHG_CON2 计算寻址
    # 面并入 tail 容器,core tick 首溢出真实交付+14 次交付延期,OS tick 级联
    # 带进 GPCNT_CON 读,601,880 @ unknown_gpcnt_configuration_bits。路 CG:
    # gpcnt_con_readback 位粒度读回投影放行该读(bit0=0 真实跳过分支,任务 9
    # 次调度全空手返回),W1[0] 20 轮超时路径全预算执行,core tick 交付合计 9
    # 次,超时后进 mode 切换函数 0x6068152——3,070,626 @ 0x606817a 读
    # JL_CLOCK->DCCK_CON0 0xfd0008 停(路 CC 预研钉死的 JL 时钟族)。路 CL:
    # jl_clock_dcck 投影放行该读,mode 切换体真实执行,W1[1] call scheduler
    # 中断上下文 helper 0x6065c3a。路 CO:partial_icfg_masked_branch 处理器放行
    # 0x6065c4c 的 active-priority 低字节掩码测试 `if ((r0&0xFF)!=0)`(bne.and.
    # modimm.ri.l i=0xFF;empty-active-priority fixture 下低字节已知 0 → 不跳),
    # helper 经 0x12cb08(临界查询→1)/0x12b55c(重进临界区 IPMASK 0→6)后停在
    # 0x6065ccc `st.w.sp.l [sp+8]=r9` 的上游存储模型簿记合并读未初始化局部
    # 0x19e2d4。路 CQ:exact_word_store_sp_l 独立子开关放行该整字存储,helper
    # 尾段写两局部并返回 W1[1] walker,停在 0x601d1ac `ld.w r0=[r2+0]` 读
    # 0xfd2000(0x601d198 读辅助函数 r1==0 分支)。路 F:命名修正——0xfd2000/
    # 0xfd2100/0xfd2200 = JL_UART0/1/2 基址(br28.h,非「SDK 参考头未命名」),
    # 读辅助函数 = SDK uart_v1.c uart_is_idle;读侧三选一=方案 (a) 投影
    # `jl_uart_con_projection`(合成 0=空闲,derived-not-measured)放行后 uart_init
    # 真实执行 CON0=0x3400,停 0x603446a 写 0xfd2004(JL_UART.BAUD|BUF,BAUD
    # __WO)——3,071,984 @ 0x603446a unmapped_or_readonly_write 写 0xfd2004 停。
    assert SCENARIOS['p11_heap_experimental']['freshness'] == {
        'completed_steps': 3071984, 'stop_pc': '0x603446a',
        'stop_fields': {'kind': 'unmapped_or_readonly_write',
                        'address': '0xfd2004'}}
    assert SCENARIOS['crc_bankb']['default_budget'] == 300000
    assert SCENARIOS['p11_heap_experimental']['default_budget'] >= \
        SCENARIOS['p11_heap_experimental']['freshness']['completed_steps'] + 1000


if __name__ == '__main__':
    for name, case in sorted(globals().items()):
        if name.startswith('test_'):
            case()
            print('passed', name)
