#!/usr/bin/env python3
"""Four-fold pre-load assertion suite for the candidate image worlds (route BU).

docs/candidate-image-cotest.md §1.2 gates every candidate-image loader
(l1_slotram / r4_keyhook) on four assertions BEFORE any simulation runs:

  1. ufw_sha      — candidate bytes hash to the documented build-artifact
                    sha256 and the official side hashes to the pinned
                    official 0.1.1.8 sha256 (firmware/manifest.json);
  2. integrity    — the independent CRC audit (verify_ufw_integrity.verify,
                    imported, not reimplemented) passes on the candidate:
                    UFW header/directory CRC, flash entry CRC, dual-bank
                    plain/cipher/header CRCs and the JLFS file chain;
  3. shared_boot  — candidate flash[:0x2000] is byte-identical to official
                    (the shared bootloader/header handoff assumption);
  4. byte_account — plain byte-diff runs vs official classify EXACTLY into
                    the documented whitelist regions (six 8B hook windows,
                    the fence upper-limit immediate, the cmdid 0x30 setter
                    slot (r4 only), the config.dat header CRC + last-file
                    index byte, the appended mic_effect.bin segment, the two
                    app-area JLFS CRC pairs), the whole-UFW-file diff count
                    equals the builder byte account (472B / 554B), and —
                    when the builder's build-report.json is supplied — its
                    output_sha256 / diff_count / diff_positions reconcile
                    with the independently computed diff (forged accounts
                    fail).

The whitelist geometry below is pinned INDEPENDENTLY from the merged docs
(hearing-aid-l1-payload.md §12.3, r4-command-hook.md §3.1/§3.3,
candidate-image-cotest.md §1.3) and re-measured builder artifacts. The
builder module (hearing_aid_l1_image.py) is deliberately NOT imported, so a
builder-side regression cannot silently bless its own output; the CRC engine
is the existing independent implementation and the image loader is the
shared one (verify_eq_candidate.load_image, same as analyze_image_trace).

Plain coordinates: plain[i] corresponds to runtime address 0x06000000+i and
flash offset 0x2020+i; runs are [lo, hi) byte ranges. Plain LENGTHS are the
decrypted app-region lengths (boot-info app_size = flash end - 0x2020):
0xE31E0 / 0xE322E — NOT the flash-end values 0xE5200/0xE524E quoted in the
r4/slotram docs' loose convention; the fence upper limit equals the plain
end's runtime address (0x060E31E0 / 0x060E322E), which cross-checks the two.

Offline assertions only. Nothing here runs a simulation or touches a device;
both candidates stay UNTESTED and must not be flashed.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from verify_eq_candidate import load_image          # shared loader (no builder import)
from verify_ufw_integrity import verify             # independent CRC engine

OFFICIAL_SHA = '65cafc7f9a7545c5c30701d141c77db334e37065589e42c51306132c94c879af'
OFFICIAL_DEFAULT = ROOT / 'firmware/QCY_H3S_7018F_1T2_release_note_V1.1.8_011.ufw'

# Whitelist regions in plain coordinates (runtime - 0x06000000). Sources:
# candidate-image-cotest.md §1.3 semantics table; window/fence/registry
# runtime addresses from r4-command-hook.md §3.1 and hearing-aid-l1-payload
# .md §12.3; config.dat spans = last-file header 0xE2ED3 (flash 0xE4EF3 -
# 0x2020): CRC at +0 (2B), index byte at +14.
APP_AREA_HEAD_CRC = ('app_area_head_crc', 0x0, 0x4)      # 0x2020/0x2022 CRC pair
APP_JLFS_CRC = ('app_jlfs_crc', 0x20, 0x24)              # 0x2040/0x2042 CRC pair
FENCE_IMMEDIATE = ('fence_immediate', 0x34E9A, 0x34E9E)  # LIM_PC0 top imm @0x06034e98+2
HOOK_WINDOWS = [('hook_window:' + tag, addr - 0x06000000, addr - 0x06000000 + 8)
                for tag, addr in [('ON-1', 0x0600db9a), ('ON-2', 0x0600dbc4),
                                  ('OFF-1', 0x0600dc74), ('TRANS-A', 0x0600dc7e),
                                  ('OFF-2', 0x0600dc9a), ('TRANS-B', 0x0600dca4)]]
REGISTRY_SLOT = ('registry_setter_slot', 0xA9C7C, 0xA9C80)  # cmdid 0x30 entry+8
CONFIG_DAT_SPANS = [('config_dat_header_crc', 0xE2ED3, 0xE2ED5),
                    ('config_dat_index', 0xE2EE1, 0xE2EE2)]
APPEND_BASE = 0xE3053                                    # mic_effect.bin header start

# Windows are whole-8B replacements: each window span must be fully covered.
FULL_COVERAGE_REGIONS = {name for name, _lo, _hi in HOOK_WINDOWS}

CANDIDATES = {
    'l1_slotram': {
        'sha256': '906184b2e3a5f36669ba80e97d6716206a0b9b78923a34eba1f1df29036b5914',
        'plain_length': 0xE31E0,          # flash end 0xE5200 - 0x2020 (measured)
        'whole_file_diff_bytes': 472,     # hearing-aid-l1-payload §12.3 byte account
        'registry_slot': False,
    },
    'r4_keyhook': {
        'sha256': '8e9a47d0ec89982bd6f35a616d0e1d310562c1509d761746822e787bf5dcbf5d',
        'plain_length': 0xE322E,          # flash end 0xE524E - 0x2020 (measured)
        'whole_file_diff_bytes': 554,     # r4-command-hook §3.3 byte account
        'registry_slot': True,
    },
}


def byte_diff_runs(a, b):
    """Non-overlapping [lo,hi) runs where the two byte strings differ, plus a
    trailing run for the length extension. Same semantics as the trace-diff
    tool's plain-run computation (reimplemented locally to stay import-light:
    this module must not pull the simulator stack)."""
    n = min(len(a), len(b))
    runs = []
    i = 0
    while i < n:
        if a[i] != b[i]:
            j = i
            while j < n and a[j] != b[j]:
                j += 1
            runs.append((i, j))
            i = j
        else:
            i += 1
    if len(a) != len(b):
        runs.append((n, max(len(a), len(b))))
    return runs


def _regions(spec):
    """The whitelist region list for one candidate: fixed spans + the appended
    segment (open at APPEND_BASE, closed at the pinned plain length)."""
    spans = [APP_AREA_HEAD_CRC, APP_JLFS_CRC, FENCE_IMMEDIATE, *HOOK_WINDOWS,
             *CONFIG_DAT_SPANS]
    if spec['registry_slot']:
        spans.append(REGISTRY_SLOT)
    spans.append(('appended_segment', APPEND_BASE, spec['plain_length']))
    return sorted(spans, key=lambda s: s[1])


def classify_runs(runs, spec):
    """Map every run to the unique whitelist region containing it; report
    unexplained runs and required regions with no hit. Windows additionally
    require FULL span coverage (whole-8B replacement semantics)."""
    spans = _regions(spec)
    classified = []
    unexplained = []
    coverage = {name: set() for name, _lo, _hi in spans}
    for lo, hi in runs:
        hit = None
        for name, slo, shi in spans:
            if slo <= lo and hi <= shi:
                hit = name
                break
        if hit is None:
            unexplained.append({'range': [lo, hi], 'bytes': hi - lo})
        else:
            coverage[hit].add((lo, hi))
        classified.append({'range': [lo, hi], 'bytes': hi - lo,
                           'region': hit})
    missing = []
    for name, lo, hi in spans:
        if not coverage[name]:
            missing.append(name)
        elif name in FULL_COVERAGE_REGIONS:
            union = set()
            for rlo, rhi in coverage[name]:
                union.update(range(rlo, rhi))
            if union != set(range(lo, hi)):
                missing.append(name + ' (partial window coverage)')
    return classified, unexplained, missing


def _attempt(assertion, official, candidate, func):
    """Run one check; check functions return {passed, **detail} so failures
    keep their diagnostics. Unexpected exceptions degrade to an error entry."""
    try:
        detail = func(official, candidate)
        return {'assertion': assertion, **detail}
    except Exception as exc:                    # noqa: BLE001 - report, don't crash
        return {'assertion': assertion, 'passed': False,
                'error': f'{type(exc).__name__}: {exc}'}


def _check_ufw_sha(official, candidate):
    actual = hashlib.sha256(candidate['raw']).hexdigest()
    official_actual = hashlib.sha256(official['raw']).hexdigest()
    problems = []
    if actual != candidate['spec']['sha256']:
        problems.append(f'candidate sha256 {actual} != pinned {candidate["spec"]["sha256"]}')
    if official_actual != OFFICIAL_SHA:
        problems.append(f'official sha256 {official_actual} != pinned {OFFICIAL_SHA}')
    return {'passed': not problems, 'problems': problems,
            'candidate_sha256': actual, 'official_sha256': official_actual}


def _check_integrity(_official, candidate):
    report = verify(candidate['path'])
    problems = [] if report['passed'] else list(report['errors'])
    return {'passed': report['passed'], 'problems': problems,
            'crc_errors': report['errors'], 'app_bytes': report['app_bytes'],
            'jlfs_files': len(report['jlfs'])}


def _check_shared_boot(official, candidate):
    a, b = official['flash'][:0x2000], candidate['flash'][:0x2000]
    first = next((i for i in range(0x2000) if a[i] != b[i]), None)
    problems = [] if first is None else [
        f'bootloader/header bytes differ from official at flash offset {first:#x} '
        '(shared handoff assumption broken)']
    return {'passed': first is None, 'problems': problems,
            'bytes_compared': 0x2000, 'first_diff': first}


def _check_byte_account(official, candidate):
    spec = candidate['spec']
    detail = {'passed': True, 'problems': [],
              'plain_length_official': len(official['plain']),
              'plain_length_candidate': len(candidate['plain']),
              'plain_length_expected': spec['plain_length']}
    problems = detail['problems']
    if len(candidate['plain']) != spec['plain_length']:
        problems.append(f'candidate plain length {len(candidate["plain"]):#x} != pinned '
                        f'{spec["plain_length"]:#x} (flash end - 0x2020)')
    runs = byte_diff_runs(official['plain'], candidate['plain'])
    classified, unexplained, missing = classify_runs(runs, spec)
    detail['runs'] = [{'range': [f'0x{lo:x}', f'0x{hi:x}'], 'bytes': hi - lo,
                       'region': region}
                      for (lo, hi), region in zip(runs, (c['region'] for c in classified))]
    detail['unexplained_runs'] = unexplained
    detail['missing_regions'] = missing
    # Whole-UFW-file diff against the builder byte account (472B / 554B).
    positions = None
    if len(official['raw']) != len(candidate['raw']):
        problems.append(f'candidate file length {len(candidate["raw"])} != official '
                        f'{len(official["raw"])} (fixed UFW length expected)')
    else:
        positions = [i for i in range(len(official['raw']))
                     if official['raw'][i] != candidate['raw'][i]]
        expected = spec['whole_file_diff_bytes']
        detail['whole_file_diff'] = {
            'count': len(positions), 'expected': expected,
            'positions_sha256': hashlib.sha256(
                json.dumps(positions).encode()).hexdigest()}
        if len(positions) != expected:
            problems.append(f'whole-file diff {len(positions)}B != builder byte account '
                            f'{expected}B')
    if unexplained:
        problems.append(f'{len(unexplained)} run(s) outside the whitelist: '
                        f'{[u["range"] for u in unexplained[:8]]}')
    if missing:
        problems.append(f'missing/partial required regions: {missing}')
    detail['passed'] = not problems
    return detail


def _reconcile_build_report(detail, report_path, candidate):
    """The builder's own account must describe THIS artifact and agree with
    the independently computed diff. Any field mismatch = red."""
    data = json.loads(Path(report_path).read_text())
    problems = []
    sha = hashlib.sha256(candidate['raw']).hexdigest()
    if data.get('output_sha256') != sha:
        problems.append(f"build-report output_sha256 does not describe this artifact "
                        f'({data.get("output_sha256")} != {sha})')
    l1 = (data.get('hearing_aid_l1') or {}).get('byte_diff') or {}
    count = l1.get('diff_count')
    whole = detail.get('whole_file_diff')
    if whole is None:
        problems.append('cannot reconcile: whole-file diff was not computable')
    elif count != whole['count']:
        problems.append(f'build-report diff_count {count} != independent count '
                        f'{whole["count"]}')
    positions = l1.get('diff_positions')
    positions_match = None
    if positions is not None and whole is not None:
        digest = hashlib.sha256(json.dumps(list(positions)).encode()).hexdigest()
        positions_match = digest == whole['positions_sha256']
        if not positions_match:
            problems.append('build-report diff_positions != independently computed '
                            'diff positions')
    return {'passed': not problems, 'problems': problems,
            'path': str(report_path), 'diff_count': count,
            'positions_match': positions_match}


def run_checks(name, candidate_path, official_path, build_report=None):
    """Run all four assertions, collecting every result even after a failure.
    Returns the JSON-ready report dict; report['passed'] is the overall gate."""
    if name not in CANDIDATES:
        raise ValueError(f'unknown candidate {name!r}; expected one of {sorted(CANDIDATES)}')
    candidate_path, official_path = Path(candidate_path), Path(official_path)
    spec = CANDIDATES[name]
    official = {'path': official_path, 'spec': None,
                'raw': official_path.read_bytes()}
    candidate = {'path': candidate_path, 'spec': spec,
                 'raw': candidate_path.read_bytes()}
    # Loader/decode failures surface inside the assertions that need the
    # decoded views (boot compare / byte account); sha + CRC only need bytes.
    for side in (official, candidate):
        try:
            side['raw'], side['flash'], side['plain'] = load_image(side['path'])
        except Exception as exc:                    # noqa: BLE001 - recorded below
            side['load_error'] = f'{type(exc).__name__}: {exc}'
    report = {'tool': 'candidate_world_checks', 'candidate': name,
              'candidate_path': str(candidate_path), 'official_path': str(official_path),
              'assertions': []}

    def add(entry):
        report['assertions'].append(entry)
        return entry['passed']

    add(_attempt('ufw_sha', official, candidate, _check_ufw_sha))
    add(_attempt('integrity_crc', official, candidate, _check_integrity))
    load_error = (candidate.get('load_error') or official.get('load_error'))
    if load_error:
        add({'assertion': 'shared_boot', 'passed': False,
             'error': 'image decode failed: ' + str(load_error)})
        add({'assertion': 'byte_account', 'passed': False,
             'error': 'image decode failed: ' + str(load_error)})
        report['passed'] = False
        return report
    add(_attempt('shared_boot', official, candidate, _check_shared_boot))
    account = _attempt('byte_account', official, candidate, _check_byte_account)
    if account['passed'] and build_report:
        reconciled = _attempt('byte_account', official, candidate,
                              lambda o, c: _reconcile_build_report(
                                  account, build_report, c))
        reconciled['assertion'] = 'build_report_reconcile'
        account['build_report'] = reconciled
        account['passed'] = reconciled['passed']
    add(account)
    report['passed'] = all(a['passed'] for a in report['assertions'])
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate', required=True, choices=sorted(CANDIDATES))
    parser.add_argument('--candidate-path', type=Path, required=True,
                        help='the candidate .ufw built by build_quiet_candidate.py')
    parser.add_argument('--official', type=Path, default=OFFICIAL_DEFAULT,
                        help='official 0.1.1.8 .ufw (default: %(default)s)')
    parser.add_argument('--build-report', type=Path,
                        help='optional build-report.json from the candidate build; '
                             'its byte account is reconciled against the independent diff')
    parser.add_argument('--output', type=Path, help='write the JSON report here '
                        '(default: stdout)')
    args = parser.parse_args()
    report = run_checks(args.candidate, args.candidate_path, args.official,
                        build_report=args.build_report)
    text = json.dumps(report, ensure_ascii=False, indent=2) + '\n'
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text)
    else:
        print(text, end='')
    if not report['passed']:
        failed = [a['assertion'] for a in report['assertions'] if not a['passed']]
        print(f'candidate world checks FAILED: {failed}', file=sys.stderr)
        raise SystemExit(1)


if __name__ == '__main__':
    main()
