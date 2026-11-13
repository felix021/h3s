#!/usr/bin/env python3
"""Pin the App 0x17 direct-set path (the 0x0600dcee family) against official bytes.

Route AC / sim-app0x17 (2026-11-14). The route-Y quick evaluation of this family
(docs/hearing-aid-l1-payload.md §11.4 as written then) read the {rets,r7-r4}
prologue, the r0==3/==2/!=1 wire-mode branches, the scene tbb and the
query/wrapper calls, and concluded the App path applies ANC modes inside the
wrapper family WITHOUT 0x0600db3c — leaving the merged --hearing-aid-l1-close
candidate apparently unable to intercept App-driven transparency. Re-deriving
the whole family from the official app.bin corrects that premise:

  - every wire-mode branch of 0x0600dcee converges on `call 0x0600db3c`
    (0x0600dd62 for wire 3/2, 0x0600ddac for wire 1 scenes 1-4) with the
    internal-mode argument (wire 1(ON)->2, 2(OFF)->1, 3(TRANS)->3) and the
    caller's flag forwarded in r1; the App 0x17 callback chain (setter
    0x06025ca4 -> callback 0x06025d6a) always passes flag=1, which selects the
    window/asynchronous path inside 0x0600db3c — exactly where the six
    route-Y graft windows (A/B/ON-1/OFF-1/ON-2/OFF-2) live. The merged
    candidate therefore already intercepts App-driven transparency entry and
    leave; no additional graft window exists to add;
  - the wrapper family (queries 0x0600bbd8/0x0600bbee, setters
    0x0600c092/0x0600c09c/0x0600c294/0x0600dcde) is engine/scene plumbing and
    never writes the mode byte b[S+33] that both the pre-query and the
    dispatch's same-mode early exit read;
  - the paths that bypass 0x0600db3c are enumerated and none is an App-driven
    transparency transition: same-mode early returns (correct no-ops), wire-1
    scene 5 (posts via the 0x06064948 notify family, no mode apply), the
    current==ON reconfigure case (no mode change), and every 0x0600db3c(.., 0)
    "synchronous" caller (flag=0 selects the 0x0600daf6 bookkeeping path that
    never traverses the windows — config-restore contexts and some event
    handlers whose trigger contexts remain unpinned).

The tool also re-runs the full-app branch-landing scan over the six route-Y
windows (route-T method) so the coverage precondition stays machine-checked,
and inventories every static caller of 0x0600db3c with its flag value.

Static evidence only: no device access, no simulation, nothing here executes.
Register-indirect calls have no static target and stay outside the scan (same
residual limitation as routes T/Y).
"""
import argparse
import hashlib
import json
from pathlib import Path

from probe_boot import APP_SHA, ENTRY, application
from probe_pi32v2 import PIN, ROOT, require, upstream

APP_BASE = ENTRY                    # 0x06000100
DISPATCH = 0x0600db3c               # internal-mode dispatch (route L §1.4)
DCEE = 0x0600dcee                   # App 0x17 mode/scene/strength apply
SETTER = 0x06025ca4                 # RCSP 0x17 registry setter (route P)
CALLBACK = 0x06025d6a               # async apply callback registered by the setter
PERSIST = 0x06012be2                # writes config key 16 from b[0x1265EB..ED]
QCY = 0x1265c0                      # big config struct base
MODE_BYTES = QCY + 171              # b[0x1265EB..ED] = wire mode/scene/strength
QUERY_MODE = 0x0600bbd8             # b[[QCY+500]+33] — current internal mode
QUERY_ENGINE = 0x0600bbee           # b[[[QCY+480]+12]+34] — ANC engine enable
ENGINE_SET = 0x0600bef2             # 0x0600c092 inner: engine on/off (writes +34)
SCENE_SET = 0x0600c1fe              # 0x0600c294 inner: h[S+10]=scene + callbacks
LOG_GUARD = 0x0600c266              # shared conditional-log tail of the wrappers
WRAP_OFF = 0x0600dcde               # {engine_set(0); log_guard()}
WRAP_SCENE = 0x0600c294             # {scene_set(k); log_guard()}
ENGINE_CLEAR = 0x0600c09c           # b[QCY+37]=0 + ROM 0x06001f12(15,1)
ENGINE_OFF = 0x0600c092             # engine_set(x, 0)
STRENGTH_TABLE = 0x060A21AC         # 0x060A1D11 + 1179, indexed by arg r2<<2
SYNC_APPLY = 0x0600daf6             # flag=0 path callee (b[S+28] bookkeeping)

# Every static caller of 0x0600db3c with its pinned (mode, flag) pair as
# decoded from the instructions immediately before the call; mode/flag None =
# dynamic value. Sites 0x0600dd62/0x0600ddac forward 0x0600dcee's r4 (flag
# argument), so their effective flag is decided by the dcee caller table below.
DB3C_CALLS = {
    0x0600dd62: (None, 'forwarded'),   # dcee wire-3/2 tail: db3c(wire_map, r4)
    0x0600ddac: (None, 'forwarded'),   # dcee wire-1 tail: db3c(2, r4)
    0x0600de56: (2, 0),                # persist-apply: stored wire mode 1 -> ON
    0x06018a7e: (2, 1),
    0x06018c8e: (2, 1),                # key event callbacks (route L §1.4 chain)
    0x06018ca4: (1, 1),
    0x06018cba: (3, 1),
    0x06018ec0: (2, 1),
    0x0601be3a: (1, 0),
    0x06021212: (3, 0),
    0x06021e54: (3, 0),
    0x060232f8: (1, 0),
    0x060243fc: (None, 1),
    0x06025d64: (2, 0),                # 0x17 restore-default
    0x06025daa: (2, 0),                # 0x17-family init side effect
    0x0602d4f0: (None, 0),
    0x0602d5dc: (None, None),
}
# Static callers of 0x0600dcee with the flag they pass in r3.
DCEE_CALLS = {
    0x06025d84: 1,     # the App 0x17 callback (route P chain)
    0x0600de68: 0,     # persist-apply at boot/restore
    0x06018ac0: 1,
    0x06018ad8: 1,
    0x06018aec: 1,
}
# Wire mode -> internal mode as fed to 0x0600db3c (AGENTS invariant 1).
WIRE_TO_INTERNAL = {1: 2, 2: 1, 3: 3}
# Route-Y graft windows (docs §11.2): start -> pinned incoming branch sites.
WINDOWS = {
    'A': (0x0600dc7e, [0x0600db92]),
    'B': (0x0600dca4, [0x0600dbbc]),
    'ON-1': (0x0600db9a, []),
    'OFF-1': (0x0600dc74, [0x0600db8e]),
    'ON-2': (0x0600dbc4, []),
    'OFF-2': (0x0600dc9a, [0x0600dbb8]),
}
EARLY_EXIT_POP = 0x0600dc98          # live landing of the 4 pre-window early exits
EARLY_EXIT_SITES = [0x0600db4a, 0x0600db54, 0x0600db66, 0x0600db6c]


_TABLE = {}


def table(module):
    """Parse the ISA table once per process (load() re-parses the TOML)."""
    if 't' not in _TABLE:
        _TABLE['t'] = module.table.load()
    return _TABLE['t']


def decode_at(module, app, address, span=8):
    got = module.codec.decode(table(module), app[address - APP_BASE:address - APP_BASE + span])
    require(got is not None, f'undecodable instruction at {address:#x}')
    insn, raw, size = got
    return insn, raw, size, module.codec.format(insn, raw)


def branch_target(module, address, insn, raw, size):
    field = module.branch_field(insn)
    if field is None:
        return None
    return address + size + module.check.operands(insn, raw)[field]


def expect_call(module, app, address, target, note):
    insn, raw, size, text = decode_at(module, app, address)
    got = branch_target(module, address, insn, raw, size)
    require(insn.id.startswith('call.') and got == target,
            f'{note}: expected call {target:#x} at {address:#x}, got {text} -> '
            f'{got:#x}' if got is not None else f'{note}: not a call at {address:#x}')
    return {'site': f'{address:#010x}', 'instruction': text, 'target': f'{target:#010x}'}


def expect_immediate(module, app, address, register, value, note):
    insn, raw, size, text = decode_at(module, app, address)
    got = raw.get('n')
    require(insn.id == 'li.u32' and got == value and f'{register} = ' in text,
            f'{note}: expected li.u32 {register} = {value:#x} at {address:#x}, got {text}')
    return {'site': f'{address:#010x}', 'instruction': text}


def linear_stream(module, app):
    """One linear decode of the whole app: list of (address, insn, raw, size, text)."""
    stream = []
    isa = table(module)
    offset, address = 0, APP_BASE
    while offset < len(app):
        got = module.codec.decode(isa, app[offset:offset + 8])
        if got is None:
            offset += 2
            address += 2
            continue
        insn, raw, size = got
        stream.append((address, insn, raw, size))
        offset += size
        address += size
    return stream


def branch_landings(module, stream):
    """address -> sorted list of branch/call sites landing exactly there."""
    landings = {}
    for address, insn, raw, size in stream:
        if module.branch_field(insn) is None:
            continue
        target = address + size + module.check.operands(insn, raw)[module.branch_field(insn)]
        landings.setdefault(target, []).append(address)
    return landings


def classify_flag(module, stream, index):
    """Recover the (mode, flag) li/mov writes immediately before a call site."""
    mode = flag = None
    codec = module.codec
    for address, insn, raw, _size in reversed(stream[max(0, index - 7):index]):
        text = codec.format(insn, raw)      # formatted lazily: only ~20 sites need it
        if module.branch_field(insn) is not None and insn.id.startswith(('goto', 'b')):
            break                      # control flow joins here; stop looking back
        if insn.id == 'li.u8':
            if text.startswith('r0 = ') and mode is None:
                mode = raw.get('n')
            elif text.startswith('r1 = ') and flag is None:
                flag = raw.get('n')
        elif insn.id == 'mov.rr' and text == 'r1 = r4' and flag is None:
            flag = 'forwarded'
    return mode, flag


def analyze(module, app):
    require(hashlib.sha256(app).hexdigest() == APP_SHA, 'unexpected application bytes')
    report = {'application_sha256': APP_SHA, 'simulator_commit': PIN,
              'device_accessed': False, 'static_only': True,
              'derived_not_measured': True}
    calls = []
    # 1. The 0x0600dcee dispatch shape: prologue, three wire-mode branches.
    for address, iid in ((0x0600dcee, 'push.rets_rl'), (0x0600dcf0, 'add.sp_i')):
        insn, _raw, _size, text = decode_at(module, app, address)
        require(insn.id == iid, f'dcee prologue changed at {address:#x}: {text}')
    for address, compare, target in ((0x0600dcf8, '== 3', 0x0600dd40),
                                      (0x0600dcfc, '== 2', 0x0600dd56),
                                      (0x0600dd00, '!= 1', 0x0600ddb8)):
        insn, raw, size, text = decode_at(module, app, address)
        got = branch_target(module, address, insn, raw, size)
        require(got == target and compare in text and 'r0' in text,
                f'wire-mode branch at {address:#x} changed: {text} -> {got and hex(got)}')
    report['wire_mode_branches'] = {
        'wire 3 (TRANS)': '0x0600dd40', 'wire 2 (OFF)': '0x0600dd56',
        'default (r0 != 1)': '0x0600ddb8 (epilogue)'}
    # 2. Wire 3 (TRANS): pre-query, early return, wrappers, dispatch call.
    calls.append(expect_call(module, app, 0x0600dd40, QUERY_MODE, 'wire-3 pre-query'))
    insn, raw, size, text = decode_at(module, app, 0x0600dd44)
    require(branch_target(module, 0x0600dd44, insn, raw, size) == 0x0600ddb8
            and '== 3' in text, f'wire-3 early-return changed: {text}')
    calls.append(expect_call(module, app, 0x0600dd4a, WRAP_OFF, 'wire-3 wrapper (engine off)'))
    calls.append(expect_call(module, app, 0x0600dd4e, WRAP_SCENE, 'wire-3 wrapper (scene 1)'))
    calls.append(expect_call(module, app, 0x0600dd62, DISPATCH, 'wire-3/wire-2 dispatch'))
    insn, raw, size, text = decode_at(module, app, 0x0600dd60)
    require(text == 'r1 = r4', f'wire-3 flag forward changed: {text}')
    insn, _raw, _size, text = decode_at(module, app, 0x0600dd52)
    require(text == 'r0 = 3', f'wire-3 internal mode literal changed: {text}')
    # 3. Wire 2 (OFF): pre-query, early return, shared dispatch call with r0=1.
    calls.append(expect_call(module, app, 0x0600dd56, QUERY_MODE, 'wire-2 pre-query'))
    insn, raw, size, text = decode_at(module, app, 0x0600dd5a)
    require(branch_target(module, 0x0600dd5a, insn, raw, size) == 0x0600ddb8
            and '== 1' in text, f'wire-2 early-return changed: {text}')
    insn, _raw, _size, text = decode_at(module, app, 0x0600dd5e)
    require(text == 'r0 = 1', f'wire-2 internal mode literal changed: {text}')
    # 4. Wire 1 (ON): engine query, conditional engine off, scene table, tbb.
    calls.append(expect_call(module, app, 0x0600dd04, QUERY_MODE, 'wire-1 mode query'))
    calls.append(expect_call(module, app, 0x0600dd0a, QUERY_ENGINE, 'wire-1 engine query'))
    calls.append(expect_call(module, app, 0x0600dd12, ENGINE_OFF, 'wire-1 conditional engine off'))
    calls.append(expect_call(module, app, 0x0600dd16, ENGINE_CLEAR, 'wire-1 engine clear'))
    report['strength_table_base'] = expect_immediate(
        module, app, 0x0600dd22, 'r0', STRENGTH_TABLE - 1179, 'table-base load')
    expect_immediate(module, app, 0x0600dd28, 'r1', QCY, 'QCY base load')
    insn, raw, size, text = decode_at(module, app, 0x0600dd2e)
    require(insn.id == 'tbb' and text == 'tbb [r2]',
            f'scene tbb changed at 0x0600dd2e: {text}')
    table = app[0x0600dd30 - APP_BASE:0x0600dd35 - APP_BASE]
    require(table == bytes.fromhex('031c263046'), f'tbb table bytes changed: {table.hex()}')
    scene_targets = [0x0600dd30 + 2 * b for b in table]
    require(scene_targets == [0x0600dd36, 0x0600dd68, 0x0600dd7c, 0x0600dd90, 0x0600ddbc],
            f'tbb case targets changed: {[hex(t) for t in scene_targets]}')
    report['scene_cases'] = {
        '1': '0x0600dd36 (strength table -> [QCY+468])',
        '2': '0x0600dd68 (strength table -> [QCY+468])',
        '3': '0x0600dd7c (strength table -> [QCY+468])',
        '4': '0x0600dd90 (immediate 0xC2C60000 -> [QCY+468])',
        '5': '0x0600ddbc (0x06064978 post; no dispatch call)'}
    expect_immediate(module, app, 0x0600dd90, 'r0', 0xC2C60000, 'scene-4 immediate')
    for site in (0x0600dd3a, 0x0600dd6c, 0x0600dd80):
        insn, _raw, _size, text = decode_at(module, app, site)
        require(insn.id == 'ld.w.idxs' and 'r6<<2' in text,
                f'strength-indexed load changed at {site:#x}: {text}')
    calls.append(expect_call(module, app, 0x0600dda0, WRAP_SCENE, 'wire-1 scene wrapper'))
    insn, raw, size, text = decode_at(module, app, 0x0600dda4)
    require(insn.id == 'if.ne.ri.s12' and 'r5 != 2' in text,
            f'wire-1 current-mode guard changed: {text}')
    calls.append(expect_call(module, app, 0x0600ddac, DISPATCH, 'wire-1 dispatch'))
    insn, _raw, _size, text = decode_at(module, app, 0x0600ddaa)
    require(text == 'r1 = r4', f'wire-1 flag forward changed: {text}')
    insn, _raw, _size, text = decode_at(module, app, 0x0600dda8)
    require(text == 'r0 = 2', f'wire-1 internal mode literal changed: {text}')
    report['wire_to_internal_mode'] = {str(k): v for k, v in WIRE_TO_INTERNAL.items()}
    # 5. Queries read the exact fields pinned in the docs.
    insn, _raw, _size, text = decode_at(module, app, 0x0600bbde)
    require(text == 'r0 = [r0+500]', f'mode query QCY+500 read changed: {text}')
    insn, _raw, _size, text = decode_at(module, app, 0x0600bbe6)
    require('b[r0+33]' in text, f'mode query field changed: {text}')
    insn, _raw, _size, text = decode_at(module, app, 0x0600bbf4)
    require(text == 'r0 = [r0+480]', f'engine query QCY+480 read changed: {text}')
    insn, _raw, _size, text = decode_at(module, app, 0x0600bbfe)
    require('b[r0+34]' in text, f'engine query field changed: {text}')
    # 6. Wrapper composition (inner calls resolved from bytes).
    calls.append(expect_call(module, app, 0x0600c096, ENGINE_SET, '0x0600c092 inner'))
    calls.append(expect_call(module, app, 0x0600c296, SCENE_SET, '0x0600c294 inner #1'))
    calls.append(expect_call(module, app, 0x0600c29a, LOG_GUARD, '0x0600c294 inner #2'))
    calls.append(expect_call(module, app, 0x0600dce0, ENGINE_OFF, '0x0600dcde inner #1'))
    calls.append(expect_call(module, app, 0x0600dce6, LOG_GUARD, '0x0600dcde inner #2'))
    insn, _raw, _size, text = decode_at(module, app, 0x0600bf12)
    require('b[r1+34]' in text, f'engine setter field changed: {text}')
    insn, _raw, _size, text = decode_at(module, app, 0x0600c232)
    require('h[r2+10]' in text, f'scene setter field changed: {text}')
    # 7. The App 0x17 chain: setter stores, registers the callback; the
    #    callback persists then calls 0x0600dcee with flag=1.
    expect_immediate(module, app, 0x06025cd0, 'r5', QCY, 'setter QCY base')
    calls.append(expect_call(module, app, 0x06025cfc, 0x06064978, 'setter post/register'))
    expect_immediate(module, app, 0x06025ce2, 'r0', CALLBACK, 'setter callback pointer')
    expect_immediate(module, app, 0x06025cf2, 'r0', STRENGTH_TABLE - 1179, 'setter name arg')
    calls.append(expect_call(module, app, 0x06025d78, PERSIST, 'callback persist'))
    calls.append(expect_call(module, app, 0x06025d84, DCEE, 'callback apply'))
    insn, _raw, _size, text = decode_at(module, app, 0x06025d82)
    require(text == 'r3 = 1', f'App 0x17 apply flag changed: {text}')
    report['app_chain'] = (
        f'setter {SETTER:#x} stores wire mode/scene/strength at b[{MODE_BYTES:#x}..] '
        f'and posts callback {CALLBACK:#x}; the callback runs persist {PERSIST:#x} '
        f'(config key 16) then {DCEE:#x}(mode, scene, strength, flag=1)')
    # 8. Dispatch flag semantics: r1==0 diverts to the sync bookkeeping path
    #    that never traverses the windows; interactive callers all use flag=1.
    insn, raw, size, text = decode_at(module, app, 0x0600db80)
    require(branch_target(module, 0x0600db80, insn, raw, size) == 0x0600dba4
            and text == 'if (r1 == 0) goto 34', f'flag-select branch changed: {text}')
    for address, target in ((0x0600dba6, 0x0600dc94), (0x0600dc94, SYNC_APPLY)):
        insn, raw, size, text = decode_at(module, app, address)
        require(branch_target(module, address, insn, raw, size) == target,
                f'sync-path hop at {address:#x} changed: {text}')
    for address, mode in ((0x06018c8a, 2), (0x06018ca0, 1), (0x06018cb6, 3)):
        insn, _raw, _size, text = decode_at(module, app, address)
        require(text == f'r0 = {mode}', f'key callback mode literal changed: {text}')
        insn, _raw, _size, text = decode_at(module, app, address + 2)
        require(text == 'r1 = 1', f'key callback flag changed: {text}')
    # 9. Full-app caller inventory of the dispatch with flag classification.
    stream = linear_stream(module, app)
    by_address = {entry[0]: i for i, entry in enumerate(stream)}
    db3c_sites = [entry[0] for entry in stream if entry[1].id.startswith('call.')
                  and branch_target(module, entry[0], entry[1], entry[2], entry[3]) == DISPATCH]
    require(sorted(db3c_sites) == sorted(DB3C_CALLS),
            f'dispatch caller set changed: {sorted(set(db3c_sites) ^ set(DB3C_CALLS))}')
    dcee_sites = [entry[0] for entry in stream if entry[1].id.startswith('call.')
                  and branch_target(module, entry[0], entry[1], entry[2], entry[3]) == DCEE]
    require(sorted(dcee_sites) == sorted(DCEE_CALLS),
            f'dcee caller set changed: {sorted(set(dcee_sites) ^ set(DCEE_CALLS))}')
    inventory = {}
    for site in sorted(DB3C_CALLS):
        mode, flag = classify_flag(module, stream, by_address[site])
        want_mode, want_flag = DB3C_CALLS[site]
        if want_mode is not None:
            require(mode == want_mode, f'dispatch caller {site:#x} mode {mode} != {want_mode}')
        if want_flag != 'dynamic':
            require(flag == want_flag,
                    f'dispatch caller {site:#x} flag {flag!r} != {want_flag!r}')
        inventory[f'{site:#010x}'] = {'mode': mode, 'flag': flag}
    for site, flag in sorted(DCEE_CALLS.items()):
        # r3 is the flag for dcee calls; find the nearest preceding `r3 = N`.
        texts = [decode_at(module, app, a)[3] for a in (site - 2, site - 4)]
        hits = [t for t in texts if t.startswith('r3 = ')]
        require(hits and hits[0] == f'r3 = {flag}',
                f'dcee caller {site:#x} flag changed: {texts}')
        inventory[f'{site:#010x}'] = {'flag_r3': flag}
    report['dispatch_callers'] = inventory
    report['dispatch_caller_count'] = len(DB3C_CALLS)
    report['sync_flag0_direct_callers'] = [f'{s:#010x}' for s in sorted(DB3C_CALLS)
                                           if DB3C_CALLS[s][1] == 0]
    report['forwarded_via_dcee'] = {'0x0600dd62/0x0600ddac r1=r4':
                                    {f'{s:#010x}': f for s, f in sorted(DCEE_CALLS.items())}}
    # 10. Landing scan over the six route-Y windows (route-T method).
    landings = branch_landings(module, stream)
    report['unique_branch_targets'] = len(landings)
    report['branch_sites_scanned'] = sum(len(v) for v in landings.values())
    windows = {}
    for tag, (start, expected) in WINDOWS.items():
        inside = sorted(s for landing in range(start + 1, start + 8)
                        for s in landings.get(landing, []))
        at_start = sorted(landings.get(start, []))
        require(not inside, f'window {tag} interior has landings: {[hex(s) for s in inside]}')
        require(at_start == expected,
                f'window {tag} entry landings {[hex(s) for s in at_start]} != '
                f'{[hex(s) for s in expected]}')
        windows[tag] = {'start': f'{start:#010x}',
                        'entry_landings': [f'{s:#010x}' for s in at_start],
                        'interior_landings': 0}
    pop_landings = sorted(landings.get(EARLY_EXIT_POP, []))
    require(pop_landings == EARLY_EXIT_SITES,
            f'early-exit pop landings changed: {[hex(s) for s in pop_landings]}')
    report['route_y_windows_recheck'] = windows
    report['early_exit_pop'] = {'address': f'{EARLY_EXIT_POP:#010x}',
                                'landings': [f'{s:#010x}' for s in pop_landings]}
    report['calls'] = calls
    report['conclusion'] = (
        'App 0x17 direct-set mode changes reach 0x0600db3c with flag=1 through '
        '0x0600dcee (wire 1->2, 2->1, 3->3); the six route-Y graft windows are on '
        'that path, so --hearing-aid-l1-close already intercepts App-driven '
        'transparency entry (windows A/B) and leave (ON/OFF windows). No new '
        'graft window exists to add. Bypassing paths (same-mode early returns, '
        'wire-1 scene 5, current==ON reconfigure, and all flag=0 sync callers) '
        'contain no App-driven transparency transition.')
    report['honest_limits'] = [
        'static decode evidence only; nothing executed or measured (derived-not-measured)',
        'register-indirect calls/gotos have no static target and are outside the scan',
        'trigger contexts of the flag=0 sync callers (event handlers / restore paths) are not pinned',
        'the 0x06064978 post/notify-family internals are not disassembled here',
    ]
    return report


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--firmware', type=Path,
                   default=ROOT / 'firmware/QCY_H3S_7018F_1T2_release_note_V1.1.8_011.ufw')
    p.add_argument('--report', type=Path, required=True)
    args = p.parse_args()
    report = analyze(upstream(), application(args.firmware))
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + '\n')
    print('App 0x17 path: funnels into 0x0600db3c with flag=1; '
          f'{report["dispatch_caller_count"]} static dispatch callers, '
          'route-Y windows re-checked clean')


if __name__ == '__main__':
    main()
