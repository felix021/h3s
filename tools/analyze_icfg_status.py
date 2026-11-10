#!/usr/bin/env python3
"""Verify pinned ICFG status constraints, not a CPU or RTI implementation.

Reads fixed AC791N measurements and BR28 SDK headers; optional private ROM is
checked without publishing bytes, full disassembly or input paths. No devices.
"""
import argparse
import hashlib
import json
from pathlib import Path

from probe_boot import ROM_SHA, SDK_PIN, check_rom
from probe_pi32v2 import ROOT, CACHE, PIN, REPO, require, upstream

SDK_CACHE = ROOT / 'tools/reference-cache/701n-boot'
SDK_DEBUG = 'SDK/interface/utils/debug.h'
SDK_CPU = 'SDK/interface/driver/cpu/br28/asm/cpu.h'
SOURCE_PINS = {'hw/fm1-2026-10-24-app2/controls.json': {'sha256': '46d6a86d88f70c381f3df7d424e6d88b8551542fcd6b057189bdf5427ad7f9f7',
                                          'bytes': 15059},
 'hw/fm1-2026-10-24-app2/results.jsonl': {'sha256': '216b1069c522808f98928be87e24dab9ead2f244ee3cf70c7ac6eda375cfcae5',
                                          'bytes': 68512},
 'hw/fm1-2026-10-24-app2/observations.json': {'sha256': '0262e84960c4ce528830b365dbe2309dbcb0487709ce7a5e1a2010d289489bd0',
                                              'bytes': 401155},
 'hw/fm1-2026-10-24-app3/results.jsonl': {'sha256': '4b4b3690394972180bd358111c721b4853b1be7c90b84787bdef0ee4f50090f5',
                                          'bytes': 217216},
 'hw/fm1-2026-10-24-app3/observations.json': {'sha256': 'a75398cd196511e70b1cf176a33229c962d5db4908dba4fae427ffceae39327d',
                                              'bytes': 1236112},
 'hw/fm1-2026-10-24-app3/nesting.json': {'sha256': '64863ac034aae201661d952d82601caf1689430aad68dc321d3207a6827beb63',
                                         'bytes': 20234},
 'hw/fm1-2026-10-24-v4/results.jsonl': {'sha256': '1ac72426cd6fd54fcebc55151527619cbc36c47472920f299e21ca78380a9dd0',
                                        'bytes': 399716},
 'hw/fm1-2026-10-24-v5/results.jsonl': {'sha256': 'f1c704084bf475297f485bb2df5a73bd4f856bc2951039da4b08c15d725b6f81',
                                        'bytes': 515633},
 'tools/hwtest/app/advanced-platform.S': {'sha256': 'e4cdbdb247301c4c1f68c8b93f307b200dcaf33825780c2b4441f1b82bc0a4d7',
                                          'bytes': 4290},
 'tools/hwtest/app/platform.S': {'sha256': 'bf5044b67870f7a1511cd13d2d3ab8e7f91041e5b39a14ddeb8d37b4ca127eaa',
                                 'bytes': 5486},
 'tools/hwtest/app/peer.S': {'sha256': 'b5b270557c010fe30475a30871a0a1800023ceb4af888f421d5e49822707b8b2',
                             'bytes': 4696},
 'tools/hwtest/semantics/v4.py': {'sha256': '32b8835f0616bcb6a3cf4123148be57649051850598a03098fe9a9222ca660f5',
                                  'bytes': 6781},
 'hw/fm1-2026-10-24-app4/observations.json': {'sha256': '8868c98f21e1f61b5fbfcdfa7e2628c12710a9e0538b82190e60220b79d2996d',
                                              'bytes': 1132882},
 'hw/fm1-2026-10-24-app4/attempts.json': {'sha256': '690583ccb759a649d346b38a1f599be71c24691ad7cd7974ded00cfcedf8ec8a',
                                          'bytes': 2926692}}
RECORD_PINS = {'hw/fm1-2026-10-24-app2/controls.json#/5': 'a8cc305672360b39533fd7ec0ffa8ce18c7d6f972536fe51aa2374f56138830c',
 'hw/fm1-2026-10-24-app2/controls.json#/6': '4ae176bd433071b7d843ffddebc5b4beb8e1704b9af85f35763ea47fae4f40fd',
 'hw/fm1-2026-10-24-app2/results.jsonl#line:45': '5478a1361ed944dd20705079195b7578d6e78c23d7f8c5ec2eb6a28dfc9a2622',
 'hw/fm1-2026-10-24-app2/results.jsonl#line:48': '5ea2c3d0eaee3eb7fd7f647192b64785ece42f14b8b9f46ba2c89307441399a3',
 'hw/fm1-2026-10-24-app3/nesting.json#/0': 'be57be4f685598d79c95be58e56c34b55f1440b410e981a41a55827622d06dbc',
 'hw/fm1-2026-10-24-app3/observations.json#/1/batches/0': '38ac68661b677d8ded4a1a5ea1f56f99c130e1dad9b67296f733e51839ed049b',
 'hw/fm1-2026-10-24-app3/nesting.json#/1': '57c71f13c159ba601867a5275fda8c661a155e951ca84480b998fc0937307972',
 'hw/fm1-2026-10-24-app3/observations.json#/1/batches/1': 'd7148913ae253fc21a6b78637d1421b7792e8bff3e9abb790700563b6d0b7ee4',
 'hw/fm1-2026-10-24-app3/nesting.json#/2': 'be0c8687fa4e57ac32c5b8b9cd01509540763c2ae973460e6424366f2e1c273c',
 'hw/fm1-2026-10-24-app3/observations.json#/1/batches/2': 'f33ff80ac1d337a66648683a8aaf22b667cfa8986c95639300abf7839b454480',
 'hw/fm1-2026-10-24-app3/nesting.json#/3': '7c20cc27a95b100229cd55353321b81f015bf0079ad8a05bf2f8e1deec3fc997',
 'hw/fm1-2026-10-24-app3/observations.json#/1/batches/3': 'bb5fafe4b31243e971cfb0a68e09cf0fc822231dfe064a74c606ea1328d64ef1',
 'hw/fm1-2026-10-24-app3/nesting.json#/4': '2035201681115c0c04134486031d488390add6acf5995df479d40b142c3c3b48',
 'hw/fm1-2026-10-24-app3/observations.json#/1/batches/4': '0e62054e5610acd9a96ad85cbe5ca30eea80f3d83acc4dd6356177e9f17b65fb',
 'hw/fm1-2026-10-24-app3/nesting.json#/5': 'd8921f74813cc3c2076454b9f9023685f8d61533caa77e59ccec53a08a2fff9e',
 'hw/fm1-2026-10-24-app3/observations.json#/1/batches/5': 'd54e74c1736db8bfd24eb01ae900dd3c79e2363e77c3281c92fa3b62a480b16c',
 'hw/fm1-2026-10-24-app3/nesting.json#/6': '708ea1dcac15e597fc755475f99c6167a6b337ac5d256a6ac23316bd35b61793',
 'hw/fm1-2026-10-24-app3/observations.json#/1/batches/6': '9c172208c8bc726c6bc79ef88980fdf064a71eb69fc79c84a655bd3aac3ba057',
 'hw/fm1-2026-10-24-app3/nesting.json#/7': '6b686f0751eefdc92b3a576d388f0b764bf619f8e85fae2a3ee71fd0a14fd9d5',
 'hw/fm1-2026-10-24-app3/observations.json#/1/batches/7': 'd9cf626bfdafa4595639c18a34e35644b992a1ec2c7ed6ff27e65b252b86c0d6',
 'hw/fm1-2026-10-24-app3/nesting.json#/8': '6fcaab8426749666bf562e0521d5b93653d4d3d2289a0ab668ae4f4cdc64ea40',
 'hw/fm1-2026-10-24-app3/observations.json#/1/batches/8': 'f3af4b81f6522c881c2e97bca09acd441fa4d540b8f9fa286ce9169690d85704',
 'hw/fm1-2026-10-24-app3/observations.json#/0/batches/7': '9add3f097ffc3209a9c0e8904cc80b9464b2a921613cb9994658e47563df4bb0',
 'hw/fm1-2026-10-24-app3/results.jsonl#line:24': '4ff0da517ebbd393f218e2c32c75d0ea75d749f438332f09a4b04de2974bf4b3',
 'hw/fm1-2026-10-24-app3/observations.json#/0/batches/8': '1884ee3b105ba7655fff66680cbc29b44c8b4f5cda6e406774883c879e75174f',
 'hw/fm1-2026-10-24-app3/results.jsonl#line:27': 'bcb29f2be8f284080048da6acbc59e92838f1807425a36509280e36ce2d502b0',
 'hw/fm1-2026-10-24-v4/results.jsonl#line:291': '8a733292610e6fbd5c203e7ac155acf35372230648f8b58bbfcdf32edc4136db',
 'hw/fm1-2026-10-24-v5/results.jsonl#line:273': '0d8836a4c988a30f89acfa0abbc6c80c553311475974fd99cb595dfc2a2961d0',
 'hw/fm1-2026-10-24-v5/results.jsonl#line:294': '78c2160746242b7ef1b862fe113a497d282990accb0fcc4ff57e1c4752c9d8f8',
 'hw/fm1-2026-10-24-app4/observations.json#/4': '1ac7b19266d16375499d77c9103f37ba7649f5019677879345fb98c17eed36ec',
 'hw/fm1-2026-10-24-app4/attempts.json#/4': 'f9ce3c0685c31c23d0f04d4857d0b5c9b29bfaef0db5962d20cc61e6d5404756'}
SDK_PINS = {'SDK/interface/utils/debug.h': {'sha256': 'f9d03b0b251f0913fcb858dbf97f599f09d77fdda7bb36fcd81bb3c63e81041e',
                                 'bytes': 7285},
 'SDK/interface/driver/cpu/br28/asm/cpu.h': {'sha256': '17fefa223674f3fe8fefd37bf7f4d14cc35735d80dcbe435186c06b7e4ed07fc',
                                             'bytes': 4729}}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def load_sources():
    dependencies = json.loads((ROOT / 'data/dependencies.json').read_text())
    require(dependencies['reference_sdk']['commit'] == SDK_PIN, 'SDK revision changed')
    for name, expected in SDK_PINS.items():
        require(dependencies['hardware_reference_files'][name] == expected,
                'SDK dependency pin changed: ' + name)
    sources = {name: (CACHE / name).read_bytes() for name in SOURCE_PINS}
    sources.update({name: (SDK_CACHE / name).read_bytes() for name in SDK_PINS})
    return sources


def verify_sources(sources):
    pins = {**SOURCE_PINS, **SDK_PINS}
    require(set(sources) == set(pins), 'missing or unexpected source file')
    for name, expected in pins.items():
        data = sources[name]
        require(len(data) == expected['bytes'] and sha(data) == expected['sha256'],
                'source hash/length mismatch: ' + name)


def parse_documents(sources):
    result = {}
    for name, data in sources.items():
        if name.endswith('.jsonl'):
            result[name] = [json.loads(line) for line in data.decode().splitlines()]
        elif name.endswith('.json'):
            result[name] = json.loads(data)
    return result


def record_at(documents, locator):
    name, pointer = locator.split('#', 1)
    value = documents[name]
    if pointer.startswith('line:'):
        return value[int(pointer[5:]) - 1]
    for key in pointer.strip('/').split('/'):
        value = value[int(key)] if isinstance(value, list) else value[key]
    return value


def verify_records(documents):
    for locator, expected in RECORD_PINS.items():
        require(sha(canonical(record_at(documents, locator))) == expected,
                'exact observation/case record changed: ' + locator)


def constraints(documents, sources):
    """Validate raw records before deriving bounded fields; also usable in negative tests."""
    verify_records(documents)
    a2 = 'hw/fm1-2026-10-24-app2/'
    a3 = 'hw/fm1-2026-10-24-app3/'
    a4 = 'hw/fm1-2026-10-24-app4/'
    controls = documents[a2 + 'controls.json']
    r2 = documents[a2 + 'results.jsonl']
    r3 = documents[a3 + 'results.jsonl']
    observations = documents[a3 + 'observations.json']
    writes = []
    for index, line, identity in ((5, 45, 'mov.sr_r'), (6, 48, 'sti')):
        case, result = controls[index], r2[line - 1]
        require(case['id'] == result['id'] == identity and
                result['batch'] == f'batch-controls-{index:03}' and
                result['out'][8:12] == [0, 0, 3, 0x033f0600], 'ICFG write sample changed')
        require(case['in'][12] == result['in'][12] == 0x200, 'control input changed')
        sample = case['after'].index({'id': 'mov.r_sr', 'values': {'r': 11, 's': 11}})
        opening = case['after'].index({'id': 'li.s16', 'values': {'r': 0, 'n': 0x300}})
        require(sample < opening, 'ICFG sample no longer precedes interrupt reopening')
        if index == 5:
            require(case['values'] == {'s': 11, 'r': 12}, 'ICFG write source changed')
        else:
            require(case['before'][:2] == [
                {'id': 'li.u8', 'values': {'r': 0, 'n': 0}},
                {'id': 'mov.sr_r', 'values': {'r': 0, 's': 11}}], 'STI no longer follows ICFG zero write')
        writes.append({'file': a2 + 'results.jsonl', 'line': line, 'batch': result['batch'],
                       'case_file': a2 + 'controls.json', 'case_index': index,
                       'write_sequence': 'ICFG=0x200' if index == 5 else 'ICFG=0; STI',
                       'plain_whole_word_rw_prediction': '0x200', 'sampled_icfg': '0x33f0600',
                       'irq_counts_before_sample': result['out'][8:10],
                       'irq_count_after_later_reopen': result['out'][10],
                       'sample_precedes_later_icfg_300_write': True})

    configs = ((3, 7, 0), (3, 7, 1), (3, 7, 2), (3, 7, 3), (3, 3, 1),
               (7, 3, 1), (0, 7, 1), (7, 0, 1), (3, 7, 1))
    group = observations[1]
    require(group['run_id'] == 0x3002 and len(group['batches']) == len(configs),
            'nested run identity/count changed')
    nested = []
    for n, (batch, config) in enumerate(zip(group['batches'], configs)):
        outer, child, gate = config
        depth = 2 if n in (1, 6, 8) else 1
        bitmap = (1 << child) | ((1 << outer) if depth == 2 else 0)
        require(batch['status'] == 2 and batch['icfg'] & 255 == bitmap and
                batch['icfg'] >> 24 == child and (batch['icfg'] >> 16) & 255 == 121,
                'nested active-priority/cause constraint changed')
        nested.append({'pointer': f'/1/batches/{n}', 'batch': batch['batch'],
                       'outer_priority': outer, 'child_priority': child, 'gate_selection': gate,
                       'max_depth': depth, 'entry_icfg': hex(batch['icfg']),
                       'active_priority_bitmap': hex(bitmap)})

    require(observations[0]['run_id'] == 0x3001, 'return-tail run changed')
    tails = []
    for case, line in ((7, 24), (8, 27)):
        entry = observations[0]['batches'][case]
        result = r3[line - 1]
        before, after = entry['icfg'], result['out'][11]
        require(result['batch'] == entry['batch'] and result['id'] == 'swi' and
                result['out'][8:11] == [0, 0, 1] and after == (before & ~255) | 0x500,
                'return-tail observation changed')
        tails.append({'entry_file': a3 + 'observations.json', 'entry_pointer': f'/0/batches/{case}',
                      'post_file': a3 + 'results.jsonl', 'post_line': line,
                      'entry_icfg': hex(before), 'post_return_tail_icfg': hex(after),
                      'xor': hex(before ^ after),
                      'tail': 'Restore entry ICFG; restore RETI, PSR, GPRs and RETS; CSYNC; RTI',
                      'causal_limit': 'Observation covers the entire tail and later sampling, not an isolated RTI side effect.'})

    v4path = 'hw/fm1-2026-10-24-v4/results.jsonl'
    v4 = documents[v4path][290]
    require(v4['id'] == 'rti' and v4['out'][8] == v4['out'][9] == 0x01090700,
            'v4 RTI endpoint changed')
    v5path = 'hw/fm1-2026-10-24-v5/results.jsonl'
    syscall_lines = [i for i, row in enumerate(documents[v5path], 1) if row['id'] == 'syscall']
    require(syscall_lines == [273, 294], 'syscall record coverage changed')
    syscalls = []
    for line in syscall_lines:
        row = documents[v5path][line - 1]
        require(row['out'][10] == 0x01090200, 'syscall entry ICFG changed')
        syscalls.append({'file': v5path, 'line': line, 'batch': row['batch'],
                         'entry_icfg': hex(row['out'][10]), 'supplied_ssp': hex(row['in'][3]),
                         'limit': 'A zero low byte occurs at real syscall entry; it cannot alone prove thread mode.'})

    app4 = [x for x in documents[a4 + 'observations.json'] if x['run_id'] == 0x4005]
    attempts = [x for x in documents[a4 + 'attempts.json'] if x['run_id'] == 0x4005]
    require(len(app4) == len(attempts) == 1 and len(app4[0]['batches']) == 9,
            'app4 evidence identity/count changed')
    for batch in app4[0]['batches']:
        require(batch['status'] == 2 and batch['samples'][5:9] == [1, 0x1c12000, 0x1c14000, 0x1c12000],
                'app4 stack endpoint changed')
    peer = attempts[0]['profile_sources']['tools/hwtest/app/peer.S']
    after_return = peer.split('peer_main:\n', 1)[1]
    require(after_return.startswith('\tcli\n\tr0 = 0\n\ticfg = r0\n'),
            'app4 no longer clears gates before sampling')
    require(after_return.index('\ticfg = r0') < after_return.index('\tr0 = usp'),
            'app4 sample order changed')

    # These are truth-table witnesses on selected endpoints ONLY. They are not
    # full CPU models and do not prove compatibility with all silicon behavior.
    witnesses = []
    for always in (False, True):
        def gate8(old, active):
            return 1 if always or active else old
        require(gate8(1, False) == 1 and gate8(0, True) == 1, 'gate8 witness arithmetic changed')
        witnesses.append({'rule': 'open_on_every_return' if always else 'open_only_with_active_return_context',
                          'v4_already_open_endpoint': gate8(1, False),
                          'app3_active_return_tail_endpoint': gate8(0, True),
                          'app4_after_explicit_gate_clear': gate8(0, False) & 0,
                          'unobserved_no_active_context_old_gate8_zero': gate8(0, False)})

    debug = sources[SDK_DEBUG].decode()
    cpu = sources[SDK_CPU].decode()
    require('#define traceSUPER_MODE() \\\n\n/*' in debug and 'ASSERT(icfg & BIT(10)' in debug,
            'SDK mode clue changed')
    require('return flag & 0xff;' in cpu and 'ret = ((flag & 0x300) != 0x300);' in cpu,
            'SDK ICFG consumer masks changed')
    return {
        'write_constraints': writes,
        'nested_priority_constraints': {'file': a3 + 'observations.json', 'records': nested},
        'return_tail_constraints': tails,
        'v4_rti_endpoint': {'file': v4path, 'line': 291, 'icfg_before': hex(v4['out'][9]),
                            'icfg_after': hex(v4['out'][8]), 'sp_before': hex(v4['sp_in']),
                            'sp_after': hex(v4['sp_out']), 'limit': 'ROM runner context only.'},
        'syscall_low_byte_counterexamples': syscalls,
        'app4_endpoint_scope': {'run_id': '0x4005', 'checked_batches': 9,
            'source_snapshot_sha256': sha(peer.encode()),
            'sampled_usp_ssp_sp': ['0x1c12000', '0x1c14000', '0x1c12000'],
            'explicit_cli_and_icfg_zero_before_samples': True,
            'immediate_post_rti_icfg_sampled': False},
        'gate8_endpoint_witnesses': {'rules': witnesses,
            'limit': 'Illustrates non-uniqueness at the selected gate8 endpoints only. Neither rule is a complete CPU model or proven to satisfy every measurement or silicon behavior. App3 includes a whole software return tail; app4 masks both candidates before sampling.'},
        'sdk_definitions': {'commit': SDK_PIN, 'not_exact_qcy_source': True,
            'debug_header': SDK_DEBUG, 'traceSUPER_MODE': 'Empty macro; commented body tests bit10. A mode-related clue, not an executed assertion, polarity specification or stack-bank rule.',
            'cpu_header': SDK_CPU, 'cpu_in_irq_mask': '0xff',
            'cpu_irq_disabled_without_unmask_support': '(ICFG & 0x300) != 0x300'},
    }


def rom_constraints(module, rom):
    if rom is None:
        return {'checked': False, 'limit': 'Optional exact private ROM adds bounded reset-startup constraints.'}
    check_rom(rom)
    table = module.table.load()
    wanted = {0xa15c: 'ssp = 1575968', 0xa162: 'sp = ssp', 0xa164: 'usp = 1576992',
              0xa170: 'reti = 41336', 0xa176: 'rti'}
    anchors = []
    for pc, expected in wanted.items():
        decoded = module.codec.decode(table, rom[pc:pc + 6])
        require(decoded is not None, 'ROM reset anchor does not decode')
        insn, fields, size = decoded
        require(module.codec.format(insn, fields) == expected, 'ROM reset anchor changed')
        anchors.append({'pc': hex(pc), 'instruction_id': insn.id, 'size': size})
    pc = 0xc606
    while pc < 0xc636:
        decoded = module.codec.decode(table, rom[pc:pc + 6])
        require(decoded is not None, 'ROM helper does not decode')
        insn, fields, size = decoded
        require(not (insn.id in ('mov.sr_r', 'li.sr_u32') and fields.get('s') == 11),
                'ROM helper writes ICFG')
        pc += size
    require(pc == 0xc636, 'ROM helper endpoint changed')
    return {'checked': True, 'sha256': ROM_SHA, 'bytes': len(rom), 'anchors': anchors,
            'direct_icfg_writes_absent_in': {'begin': '0xc606', 'end_exclusive': '0xc636'},
            'limit': 'Static reset path contains RTI after distinct SSP/SP and USP without an ordinary IRQ software frame. This is not an observed post-RTI state and does not establish task-return gates, mode, bank writeback or permission.'}


def analyze(module, sources, rom=None):
    verify_sources(sources)
    report = constraints(parse_documents(sources), sources)
    report.update({
        'format_version': 1, 'decoder_commit': PIN, 'static_only': True,
        'measurement_repository': REPO, 'measurement_commit': PIN,
        'sdk_repository': 'https://gitlab.zh-jieli.com/ai_test/701n',
        'record_hash_encoding': 'UTF-8 json.dumps(record, sort_keys=True, separators=(comma, colon)); default ensure_ascii=True',
        'device_accessed': False, 'cpu_executed': False, 'whole_h3s_boot_validated': False,
        'task_rti_authorized': False, 'scope': 'Pinned AC791N records and BR28 SDK definitions; source constraints for review, not a CPU implementation or measured QCY initial state.',
        'source_files': {**SOURCE_PINS, **SDK_PINS}, 'exact_record_sha256': RECORD_PINS,
        'rom_reset_startup': rom_constraints(module, rom),
        'counterexamples': {
            'whole_word_rw': 'ICFG=0 followed by STI, or a direct 0x200 write, does not yield the plain-RW prediction 0x200: the sampled word is 0x033f0600 before later IRQ reopening. These cases constrain status/cause preservation, not every writable bit.',
            'writing_300_clears_active_priorities': 'Nested samples retain an outer active-priority bit across the gate-opening write.',
            'low_byte_zero_proves_thread_mode': 'The two syscall entry samples have low byte zero.',
            'bit10_alone_selects_usp': 'Not established by the commented SDK macro or the sampled return contexts.',
            'v4_identifies_universal_return_behavior': 'The app3 complete return tails change low fields, without isolating which step causes that change. Unchanged v4 endpoints do not identify a universal RTI rule.',
        },
        'partial_model_review_contract': [
            'Track ICFG value, known mask and provenance separately from active return-context knowledge; a numeric initial fixture is not a measured reset state.',
            'CLI/STI affect observed gate9. CLI.r copies partial ICFG; STI.r must inspect only the required source bit9, without manufacturing unrelated known bits.',
            'An actual same-core read/AND~0x300/write expression may justify clearing gates8/9 while preserving other bit knowledge; a guessed literal word is a different operation.',
            'A separately explicit AC791N-derived literal gate-write projection can retain status knowledge, but these samples do not establish a universal writable mask or status-one-write behavior.',
            'Query only bits needed by a consumer. A known closed gate, disabled source or insufficient priority may establish ineligibility despite unrelated unknown bits; unknown required bits still stop delivery.',
            'Do not infer a real IRQ-entry token from caller-written low bits or a guessed bit10. Partial ICFG modeling does not authorize first-task RTI.',
        ],
        'unidentified_first_task_contract': [
            'Gate8 after return with old gate8=0 and no observed ordinary IRQ-entry context.',
            'Bit10/mode and SP/USP/SSP writeback after dynamic task-frame restoration.',
            'Hidden return-permission creation or consumption by RETI writes, frame pops or RTI.',
            'The required discriminating observation is an immediate return-state sample before CLI or another ICFG write, with distinct stack banks and known entry history. No new hardware action is requested or performed.',
        ],
    })
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', type=Path, required=True, help='bounded sanitized JSON output')
    parser.add_argument('--rom', type=Path, help='optional exact private 128KiB ROM input')
    args = parser.parse_args()
    inputs = [CACHE / name for name in SOURCE_PINS] + [SDK_CACHE / name for name in SDK_PINS]
    inputs.extend((ROOT / 'data/dependencies.json', Path(__file__)))
    if args.rom is not None:
        inputs.append(args.rom)
    for source in inputs:
        require(args.report.resolve() != source.resolve() and
                not (args.report.exists() and source.exists() and args.report.samefile(source)),
                'report must not overwrite a source input')
    module = upstream()
    report = analyze(module, load_sources(), None if args.rom is None else args.rom.read_bytes())
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + '\n')
    print('ICFG sources and exact records verified; bounded constraints written. No RTI model certified.')


if __name__ == '__main__':
    main()
