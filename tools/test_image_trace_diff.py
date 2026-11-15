#!/usr/bin/env python3
"""Unit tests for the trace-array reconvergence/parity logic in
analyze_image_trace (synthetic traces only; no simulator, no ROM, no images).

Row shape mirrors TraceMachine: (core, before_pc, next_pc, flags,
16-tuple of register signatures, tuple of (address, data-hex) writes).
"""
import inspect
import io
import itertools
import pickle

import frontier_registry
import probe_irq_entry
from analyze_image_trace import (BASE_SCENARIO_FLAGS, CONDITIONAL_EXTRA_FLAGS,
                                 SCENARIOS, TraceSpool, _divergent_spans,
                                 compare_pair, find_reconvergence,
                                 pc_run_length_summary, stop_parity)


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


def _spool_rows(count=57, chunk=8):
    """Synthetic rows covering the real signature shapes: int/None flags,
    repeated register strings, empty and multi-entry writes, unknown-word
    style signatures; loaded into a small-chunk spool so reads cross many
    chunk boundaries with a partial tail chunk."""
    rows = []
    for i in range(count):
        regs = ['00000000'] * 16
        regs[i % 16] = 'u??01|-,16-' if i % 5 == 0 else f'{i * 7:08x}'
        flags = '00' if i % 3 else 'None'
        writes = () if i % 4 else ((0x125bfc + i, 'aabbccdd'), (0x400afc, 'ff'))
        rows.append((i % 2, 0x6010000 + i * 2, 0x6010002 + i * 2, flags,
                     tuple(regs), writes))
    spool = TraceSpool(chunk=chunk)
    for r in rows:
        spool.append(r)
    spool.flush()  # partial final chunk (57 % 8 != 0)
    return rows, spool


def test_trace_spool_round_trip_is_field_exact():
    # 路 O6 验收件 1:落盘读回逐字段相等(含顺序、类型、负索引、zip、迭代),
    # 空轨迹与 close 后访问也钉死。
    rows, spool = _spool_rows()
    assert len(spool) == len(rows)
    assert list(spool) == rows
    for i in (0, 1, 7, 8, 9, 56):
        got = spool[i]
        assert got == rows[i] and type(got) is tuple and type(got[4]) is tuple \
            and type(got[5]) is tuple, f'row {i} lost shape'
    assert spool[-1] == rows[-1] and spool[-57] == rows[0]
    assert [r for r in spool] == rows  # iteration order is append order
    assert list(zip(spool, rows)) == list(zip(rows, rows))
    try:
        spool[len(rows)]
        raise AssertionError('expected IndexError past the end')
    except IndexError:
        pass
    empty = TraceSpool()
    assert len(empty) == 0 and list(empty) == []
    # append interleaved with reads (read moves the file position; the next
    # flush must still land at the tail, never at the read position)
    interleave = TraceSpool(chunk=4)
    for i in range(6):
        interleave.append(rows[i])
    assert interleave[0] == rows[0]
    for i in range(6, 12):
        interleave.append(rows[i])
    interleave.flush()
    assert [r for r in interleave] == rows[:12]
    interleave.close()
    spool.close()
    for op in (lambda: len(spool), lambda: spool[0], lambda: list(spool)):
        try:
            op()
            raise AssertionError('closed spool must be unreadable')
        except ValueError:
            pass


def test_trace_spool_views_match_list_slices():
    # 惰性视图与真 list 切片逐一等价:普通/负/步进切片、切片的切片、
    # len/索引/迭代(比较器用到的全部形态)。
    rows, spool = _spool_rows()
    for a, b, c in [(0, 10, 1), (5, -3, 1), (-6, None, 1), (None, None, 1),
                    (3, 40, 7), (-20, -2, 3), (10, 2, -2), (0, 0, 1)]:
        view = spool[a:b:c]
        expect = rows[a:b:c]
        assert len(view) == len(expect), (a, b, c)
        assert list(view) == expect, (a, b, c)
        if expect:
            assert view[0] == expect[0] and view[-1] == expect[-1]
    # slice-of-slice (compare_pair 的 prefix[-6:] 与 step:step+6 形态)
    prefix = spool[:20]
    assert list(prefix[-6:]) == rows[:20][-6:]
    assert list(prefix[3:9]) == rows[3:9]
    stepped = spool[::4]
    assert list(stepped[:5]) == rows[::4][:5]
    assert list(stepped[::-1]) == rows[::4][::-1]


def test_trace_spool_truncated_file_raises_not_eof():
    # 路 O6 验收边界(codex 定调):文件截断必须报错,不得解释成 EOF/短读。
    backing = io.BytesIO()
    spool = TraceSpool(file=backing, chunk=4)
    for i in range(10):
        spool.append(row(0x1000 + i * 2, 0x1002 + i * 2))
    spool.flush()
    assert spool[0] == spool[0]  # cache stability across repeated reads
    mid_chunk3 = backing.seek(0, io.SEEK_END)  # truncate inside the last chunk
    backing.truncate(mid_chunk3 - 3)
    spool._cache.clear()
    try:
        spool[8]
        raise AssertionError('truncated spill must raise, not read short')
    except (pickle.UnpicklingError, EOFError):
        pass


def test_reconvergence_and_spans_over_spools_equal_list_results():
    # 比较器全链路(list 与 spool 同输入产出完全相同的 detail):覆盖
    # find_reconvergence 的后向扫描/切片视图/物化消除点与 _divergent_spans。
    a = common(10) + tail(20, 0x1000)
    b = common(10) + tail(3, 0x2000) + tail(20, 0x1000)
    sa = TraceSpool(chunk=8)
    sb = TraceSpool(chunk=8)
    for r in a:
        sa.append(r)
    for r in b:
        sb.append(r)
    sa.flush(); sb.flush()
    assert find_reconvergence(sa, sb, 10) == find_reconvergence(a, b, 10)

    a2 = common(4) + tail(12, 0x1000, markers={i: '00000001' for i in range(5)},
                         writes={2: ((0x2000, 'ff'),)})
    b2 = common(4) + tail(2, 0x2000) + tail(12, 0x1000, markers={i: '00000002' for i in range(5)})
    sa2, sb2 = TraceSpool(chunk=5), TraceSpool(chunk=5)
    for r in a2:
        sa2.append(r)
    for r in b2:
        sb2.append(r)
    sa2.flush(); sb2.flush()
    assert find_reconvergence(sa2, sb2, 4) == find_reconvergence(a2, b2, 4)
    assert _divergent_spans(sa2, sb2) == _divergent_spans(a2, b2)
    assert find_reconvergence(sa, TraceSpool(), 10) == find_reconvergence(a, [], 10)


def test_pc_run_length_summary_streaming_matches_materialized():
    # 路 O6:流式聚合与原「先物化再裁剪」逐字段恒等——参考实现按原算法
    # 内联重建,覆盖 run 数 < head、恰等于 head+tail、超出后尾窗滚动、
    # 重复 PC 延长当前 run、跨核切换开新 run、spool 视图输入等形态。
    def materialized(trace, head=12, tail=12):
        runs = []
        for core, before_pc, _next, _flags, _regs, _writes in trace:
            if runs and runs[-1][0] == before_pc and runs[-1][2] == core:
                runs[-1][1] += 1
            else:
                runs.append([before_pc, 1, core])
        keep_head = runs[:head]
        keep_tail = runs[-tail:] if len(runs) > head + tail else []
        return {'run_count': len(runs), 'head': [[hex(pc), c, k] for pc, c, k in keep_head],
                'tail': [[hex(pc), c, k] for pc, c, k in keep_tail],
                'omitted_middle_runs': max(0, len(runs) - len(keep_head) - len(keep_tail))}

    def from_pcs(pcs, cores=None):
        cores = cores or [0] * len(pcs)
        return [(core, pc, 0, '00', ('00000000',) * 16, ()) for pc, core in zip(pcs, cores)]

    cases = [
        [],                                             # empty
        from_pcs([i * 2 for i in range(5)]),            # fewer runs than head
        from_pcs([i * 2 for i in range(24)]),           # exactly head+tail runs
        from_pcs([i * 2 for i in range(25)]),           # one past the boundary
        from_pcs([i * 2 for i in range(80)]),           # tail window slides
        from_pcs([1, 1, 1, 2, 3, 3, 4] * 4),            # repeats extend runs
        from_pcs([i % 3 for i in range(40)],            # core switch opens runs
                 cores=[i % 2 for i in range(40)]),
    ]
    for trace in cases:
        assert pc_run_length_summary(trace) == materialized(trace), \
            f'streaming summary diverged for a {len(trace)}-row trace'
    # same input through a spool view (head/tail windows across chunk seams)
    rows, spool = _spool_rows()
    assert pc_run_length_summary(spool) == materialized(rows)
    assert pc_run_length_summary(spool[10:-3]) == materialized(rows[10:-3])


def test_trace_spool_second_consumer_pass_is_identical():
    # 路 REUSE 核心不变量:同一 spool 被多个消费者先后整遍消费(六臂→三臂后,
    # 同一臂的轨迹喂它参与的所有 pair),每一遍的行序列、随机索引、惰性视图
    # 都与第一遍完全一致——spool 是纯数据源,消费不留状态。
    rows, spool = _spool_rows()
    first = list(spool)
    # a consumer doing random access in between two full passes
    probes = [spool[i] for i in (3, 30, 30, 11, 0)]
    assert probes == [rows[i] for i in (3, 30, 30, 11, 0)]
    second = list(spool)
    assert first == second == rows
    assert list(spool[7:20]) == rows[7:20]      # views read the same after reuse
    assert spool[-1] == rows[-1] and len(spool) == len(rows)
    spool.close()


def test_run_pool_executes_each_label_once_and_shares_runs():
    # 路 REUSE:同 (镜像, 场景, 预算) 只执行一次——freshness 与每个 pair 都取
    # 同一个 (result, trace) 对象;三镜像全组合(6 pair 臂)+freshness 臂的
    # 消费矩阵下执行次数=不同标签数(六臂→三臂的池化形态),名字/预算/场景
    # 按臂原样传递;close 后所有 spool 不可读且不再持有 run。
    import analyze_image_trace as ait

    calls = []

    def fake_frontier_run(module, app, header, rom, name, *, flash_image_bytes,
                          readonly=(), budget=None, scenario='crc_bankb', snapshot=None):
        assert snapshot is None, 'plain (non-snapshot-round) pool runs pass no snapshot'
        calls.append({'name': name, 'app': app, 'plain': flash_image_bytes,
                      'budget': budget, 'scenario': scenario})
        spool = TraceSpool()
        for i in range(3):
            spool.append(row(0x100 + i * 2, 0x102 + i * 2))
        result = {'name': name, 'completed_steps': 3, 'stop_pc': '0x104',
                  'stop': {'kind': 'unmapped_memory'}, 'machine': {'cores': {'0': {'steps': 3}}},
                  'app': app}
        return result, spool

    labels = ('eq_fence_repair', 'official', 'tested_b79')  # sorted, as main() walks them
    loaded = {label: (label, (f'app-{label}'.encode(), f'plain-{label}'.encode(), (), f'sha-{label}'))
              for label in labels}
    original = ait.frontier_run
    ait.frontier_run = fake_frontier_run
    try:
        pool = ait.RunPool(object(), b'rom', 'header', 300000, 'crc_bankb', loaded)
        # the full three-image consumption matrix: freshness(official) + all pairs
        seen = {'official': pool.run('official')}
        for a, b in itertools.combinations(labels, 2):
            seen[a] = pool.run(a)
            seen[b] = pool.run(b)
        assert len(calls) == 3, 'each distinct image must execute exactly once'
        assert sorted(c['name'] for c in calls) == sorted(f'trace_{label}' for label in labels)
        for call in calls:
            label = call['name'][len('trace_'):]
            assert call['app'] == f'app-{label}'.encode()
            assert call['plain'] == f'plain-{label}'.encode()
            assert call['budget'] == 300000 and call['scenario'] == 'crc_bankb'
        for label in labels:
            # every consumer of the same arm gets the SAME run objects back
            assert seen[label] is pool.run(label)
        runs = dict(pool.runs)
        pool.close()
        assert not pool.runs
        for _result, trace in runs.values():
            try:
                len(trace)
                raise AssertionError('closed pool spool must be unreadable')
            except ValueError:
                pass
    finally:
        ait.frontier_run = original


def _pair_runs(shape):
    """Two synthetic arms (results + traces) for compare_pair under reuse:
    shape 'signature' = identical control flow, register/write divergences only;
    shape 'insertion' = b executes 3 extra steps once (control divergence with
    immediate realignment, lag 3). Traces come back as REAL spools (the reuse
    data source) and as plain lists (a fresh non-reused execution), so pair
    reports can be compared across both."""
    if shape == 'signature':
        a = common(6) + tail(12, 0x1000, markers={i: '00000001' for i in range(4)},
                            writes={1: (('0x2000', 'ff'),)})
        b = common(6) + tail(12, 0x1000, markers={i: '00000002' for i in range(4)},
                            writes={1: (('0x2000', 'ee'),)})
        steps_a = steps_b = len(a)
    else:
        a = common(6) + tail(12, 0x1000)
        b = common(6) + tail(3, 0x2000) + tail(12, 0x1000)
        steps_a, steps_b = len(a), len(b)
    def result(steps, stop_address):
        return {'name': 'x', 'completed_steps': steps, 'stop_pc': '0x1000',
                'stop': {'kind': 'unmapped_memory', 'access': 'read',
                         'address': stop_address, 'size': 1},
                'machine': {'cores': {'0': {'steps': steps}}}}
    results = (result(steps_a, '0xf000'), result(steps_b, '0xf004'))
    spools, lists = [], []
    for rows in (a, b):
        spool = TraceSpool(chunk=5)
        for r in rows:
            spool.append(r)
        spool.flush()
        spools.append(spool)
        lists.append(list(rows))
    image = lambda label, plain: (label, (b'app', plain, (), 'sha'))  # noqa: E731
    return image, results, spools, lists


def test_compare_pair_reuse_reports_identical_across_consumers():
    # 路 REUSE 等价钉子:同一对 (result, spool) 连续喂两次 compare_pair(第二个
    # pair 重读同一 spool),两份 pair 报告完全相等;compare_pair 是纯消费者,
    # 消费完 spool 仍可读(释放权在 RunPool);spool 版报告与「不复用」的
    # 全新 list 执行版也逐字段相等。
    for shape in ('signature', 'insertion'):
        image, results, spools, lists = _pair_runs(shape)
        image_a, image_b = image('arm_a', b'plain-a'), image('arm_b', b'plain-b')
        run_spools = ((results[0], spools[0]), (results[1], spools[1]))
        first = compare_pair(image_a, run_spools[0], image_b, run_spools[1], 300000)
        second = compare_pair(image_a, run_spools[0], image_b, run_spools[1], 300000)
        assert first == second, f'reused second consumer diverged ({shape})'
        fresh = compare_pair(image_a, (results[0], lists[0]),
                             image_b, (results[1], lists[1]), 300000)
        assert first == fresh, f'spool reuse report must equal the fresh-execution report ({shape})'
        # the comparison paths were actually exercised, not trivially equal
        if shape == 'signature':
            assert first['first_divergence'] and first['divergent_signature_spans']['count'] >= 1
        else:
            assert first['first_control_divergence'] and first['reconvergence']['realigned']
        for spool in spools:  # pure consumer: still readable, then released by hand
            assert len(spool) > 0
            spool.close()


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
    transports = {'flash_image_bytes', 'readonly', 'snapshot'}  # per-run transports, not switches (snapshot = capture/restore config, sim-snapwire)
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

def test_snapshot_round_entry_naming_and_resume_budget_math():
    """Route SNAPX resume-round entry: per-(scenario, label) snapshot naming,
    the all-or-none resume budget conversion max(1, effective - captured) and
    the label-pinning expected_context (an official snapshot may not serve a
    tested_b79 arm)."""
    import tempfile
    from types import SimpleNamespace
    from collections import Counter
    from pathlib import Path
    from world_snapshot import capture_world
    from analyze_image_trace import resume_snapshot_config, snapshot_path

    directory = Path(tempfile.mkdtemp(prefix='h3s-snapx-entry-'))
    label_path = snapshot_path(directory, 'p11_heap_experimental', 'official')
    assert label_path.name == 'p11_heap_experimental-official.bin'
    assert snapshot_path(directory, 'crc_bankb', 'tested_b79').name == \
        'crc_bankb-tested_b79.bin'

    def write_snapshot(label, completed):
        ledger = SimpleNamespace(events=[], adapters=Counter(), rom_steps=Counter(),
                                 event_counts=Counter(), milestones=[], trace=[],
                                 completed=completed, stop={'kind': 'instruction_budget'})
        snapshot = capture_world(ledger=ledger,
                                 context={'scenario': f'trace_{label}',
                                          'completed_steps': completed})
        snapshot.save(snapshot_path(directory, 'p11_heap_experimental', label))

    # Frontier-stop shape: the cap is huge but never binds (the restored world
    # re-attempts its blocked instruction on the first resumed step).
    write_snapshot('official', 21800455)
    config, cap = resume_snapshot_config(directory, 'p11_heap_experimental',
                                         'official', 40000000)
    assert config['restore'] == snapshot_path(directory, 'p11_heap_experimental', 'official')
    assert config['expected_context'] == {'scenario': 'trace_official'}
    assert cap == 40000000 - 21800455
    # Budget-stop shape: the cap lands the resumed run on the same endpoint a
    # full effective-budget replay reaches; a captured 0-step world keeps a cap.
    write_snapshot('tested_b79', 21700455)
    _config, cap = resume_snapshot_config(directory, 'p11_heap_experimental',
                                          'tested_b79', 21710455)
    assert cap == 10000
    _config, cap = resume_snapshot_config(directory, 'p11_heap_experimental',
                                          'tested_b79', 21700455)
    assert cap == 1
    # A snapshot header without a step count refuses (not silently 0).
    ledger = SimpleNamespace(events=[], adapters=Counter(), rom_steps=Counter(),
                             event_counts=Counter(), milestones=[], trace=[],
                             completed=0, stop=None)
    capture_world(ledger=ledger, context={'scenario': 'trace_l1_slotram'}) \
        .save(snapshot_path(directory, 'p11_heap_experimental', 'l1_slotram'))
    try:
        resume_snapshot_config(directory, 'p11_heap_experimental', 'l1_slotram', 40000000)
    except ValueError as error:
        assert 'completed_steps' in str(error)
    else:
        raise AssertionError('resume config accepted a snapshot without completed_steps')


def test_report_for_diff_strips_transport_keys_only():
    """Route SNAPX: the final-report comparison strips the run name and the
    snapshot-round transport keys (per-run provenance, both arms carry them in
    a round) but keeps every world observation."""
    from analyze_image_trace import _report_for_diff
    report = {'name': 'trace_official', 'completed_steps': 21800455,
              'stop': {'kind': 'unmapped_memory'},
              'snapshot_capture': {'sha256': 'a', 'bytes': 1},
              'snapshot_restore': {'digest': 'b', 'completed_steps': 21800455}}
    assert _report_for_diff(report) == {'completed_steps': 21800455,
                                        'stop': {'kind': 'unmapped_memory'}}


def test_parallel_arms_match_serial_values_via_fork_handoff():
    """Route PAR3: run_parallel forks one worker per label, each running the
    SAME frontier_run and handing (result, spool) to the parent through a
    scratch directory; the reattached read-only spool must read back the exact
    rows the arm produced, and the pool registration must equal the serial
    path's (same fake arms, same values). Simulator-free: frontier_run is
    stubbed per-arm; the fork/handoff/reattach machinery is the real one."""
    import analyze_image_trace as ait

    def fake_frontier_run(module, app, header, rom, name, *, flash_image_bytes,
                          readonly=(), budget=None, scenario='crc_bankb', snapshot=None):
        label = name[len('trace_'):]
        spool = TraceSpool()
        for i in range(5):
            spool.append(row(0x100 + i * 2, 0x102 + i * 2,
                             marker=(15, f'{label}-{i}')))
        result = {'name': name, 'label': label, 'budget': budget,
                  'snapshot': snapshot, 'completed_steps': 5, 'stop_pc': '0x10a',
                  'stop': {'kind': 'unmapped_memory'}, 'machine': {'cores': {'0': {'steps': 5}}}}
        return result, spool

    labels = ('official', 'tested_b79', 'eq_fence_repair')
    loaded = {label: (label, (f'app-{label}'.encode(), f'plain-{label}'.encode(), (), f'sha-{label}'))
              for label in labels}
    original = ait.frontier_run
    ait.frontier_run = fake_frontier_run
    try:
        serial_pool = ait.RunPool(object(), b'rom', 'header', 300000, 'crc_bankb', loaded)
        serial_runs = {label: serial_pool.run(label) for label in labels}
        parallel_pool = ait.RunPool(object(), b'rom', 'header', 300000, 'crc_bankb', loaded)
        parallel_pool.run_parallel(list(labels), jobs=3)
        assert sorted(parallel_pool.runs) == sorted(labels)
        for label in labels:
            serial_result, serial_trace = serial_runs[label]
            result, trace = parallel_pool.runs[label]
            assert result == serial_result, f'{label}: parallel result drifted'
            assert len(trace) == len(serial_trace) == 5
            assert list(trace) == list(serial_trace), f'{label}: spool rows drifted'
            assert trace[2] == serial_trace[2]  # mid-chunk random access
        # read-only handoff: mutating operations on a reattached spool fail
        # loudly instead of silently editing a compared artifact
        spool = parallel_pool.runs['official'][1]
        try:
            spool.append(row(0, 2))
        except (io.UnsupportedOperation, OSError, ValueError):
            pass
        else:
            raise AssertionError('reattached spool accepted an append')
        serial_pool.close(); parallel_pool.close()
    finally:
        ait.frontier_run = original


if __name__ == '__main__':
    for name, case in sorted(globals().items()):
        if name.startswith('test_'):
            case()
            print('passed', name)
