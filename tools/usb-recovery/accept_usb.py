#!/usr/bin/env python3
"""Acceptance-plan USB flow (H5-PRE-01): candidate flash to bank B / rollback.

Named, digest-pinned, bidirectional profiles on top of accept_profile.py
(offline planning) and accept_verify.py (independent verification). One
candidate per flash/rollback pair, selected with --candidate (default hal5:
b79->HAL5 flash, HAL5->b79 rollback; f1a/f1b: HAL5->F1x flash, F1x->HAL5
same-layer rollback — retreat to b79 chains the unchanged hal5 rollback
afterwards). The historical r2->b79 profile in h3s_usb.py/image.py is not
modified and keeps all of its gates; this tool never writes without --apply
plus a matching one-shot nonce, and never uses the 942080B UFW flash.bin as
a 2MiB image.

Live sequence (flash/rollback, mirrors the verified 2026-11-10 restore):
  reviewed plan (plan.json + target.bin in a workdir fed by `h3s_usb.py
  backup`) -> --apply --confirm-nonce -> nonce occupied (O_EXCL, FIXED state
  directory: copying the workdir elsewhere cannot reuse it) -> journal
  restore.jsonl (O_EXCL, per-line fsync) -> fresh ROM session + pinned loader
  -> fresh double full read -> live target rebind (only the VM region may
  have drifted; the rebind can never widen the audited write set) -> per
  sector: pre-erase readback == fresh, intent journal, erase, all-FF verify,
  per-block intent/program, whole-sector readback == target -> boot-info
  sector 0xee000 last -> final full 2MiB readback == live target. Any
  failure closes the write gate and stops all further writes; journals are
  never deleted.

Interruption handling: `analyze` replays the journal against a NEW fresh
double read (`h3s_usb.py backup` into a new workdir) and accepts only states
explainable as an execution PREFIX of the journaled plan — per-block intents
make partial programming explainable, including the "device applied the
block but the response/done-row was lost" window (both byte states are
legal). Journal-confirmed and content-observed progress are reported
separately; the output is a limited recovery plan (remaining sectors only,
fresh VM, new nonce) or a refusal. `recover --apply` re-runs the same
analysis and executes it through the same executor.
"""
import argparse
import json
import os
from pathlib import Path
import time

from accept_profile import (BANK_B, CANDIDATES, SECTOR, SIZE, VM_BEGIN, VM_END,
                            _plan_digest, changed_sectors, crc, make_plan,
                            new_nonce, package_id, rebind_live_target, require)
from accept_verify import verify_plan
from h3s_usb import load_ram, read
from image import sha
from usbfs import LinuxTransport, enter_rom, open_target

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_NONCE_DIR = ROOT / 'analysis' / 'usb-recovery-nonces'


# ---- persistence helpers (fsync before the first erase, codex risk item) ----

def save_persistent(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    dir_fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


class NoncePool:
    """One-shot nonce occupation in a FIXED state directory (not the workdir):
    copying a plan directory elsewhere cannot reuse an attempted nonce. The
    first execution attempt consumes it; there is no release path — after a
    failure you generate a NEW plan (fresh nonce)."""

    def __init__(self, root):
        self.root = Path(root)

    def consume(self, nonce, record):
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(self.root / nonce, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as f:
            f.write(json.dumps(record, sort_keys=True) + '\n')
            f.flush()
            os.fsync(f.fileno())

    def occupied(self, nonce):
        return (self.root / nonce).exists()


class Journal:
    """Append-only JSONL: destructive commands are preceded by an intent row
    and followed by result rows, so an interruption may leave the last intent
    without its result — never a result without its intent."""

    def __init__(self, path, sync=True):
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        self.f = os.fdopen(fd, 'w')
        self.sync = sync

    def event(self, **row):
        line = json.dumps(row, sort_keys=True)
        self.f.write(line + '\n')
        self.f.flush()
        if self.sync:
            os.fsync(self.f.fileno())
        print(line, flush=True)

    def close(self):
        self.f.close()


# ---- restricted transport (parallel to RestoreTransport; image.ORDER untouched) ----

class AcceptRestoreTransport(LinuxTransport):
    """Write gate bound to one verified plan execution: FB01/FB04 only for the
    current cursor sector, FB04 only after the executor confirmed the erase
    readback, 512B programs must equal the live target (length+CRC checked),
    and any violation closes the gate permanently for this transport. Live
    instances come from accept_session(); tests subclass and override
    exchange() without opening USB."""

    def __init__(self):
        self.writing = False
        self.live_target = None
        self.order = ()
        self.cursor = None            # current sector offset
        self.erase_verified = False   # erase readback confirmed for cursor

    def bind(self, live_target, order):
        self.live_target = live_target
        self.order = tuple(order)

    def begin_sector(self, sector):
        require(self.writing and sector in self.order, 'sector outside plan order')
        require(self.cursor is None, 'previous sector not confirmed complete')
        self.cursor = sector
        self.erase_verified = False

    def mark_erase_verified(self):
        require(self.cursor is not None and not self.erase_verified,
                'erase verification out of sequence')
        self.erase_verified = True

    def end_sector(self):
        require(self.cursor is not None and self.erase_verified, 'sector not fully verified')
        self.cursor = None
        self.erase_verified = False

    def close_gate(self):
        self.writing = False

    def execute(self, cdb, size=0, data=None):
        opcode = int.from_bytes(cdb[:2], 'big')
        if opcode in (0xFB01, 0xFB04):
            try:
                require(self.writing and self.live_target is not None, 'write gate closed')
                require(self.cursor is not None, 'no active planned sector')
                address = int.from_bytes(cdb[2:6], 'big')
                sector = address // SECTOR * SECTOR
                require(sector == self.cursor, 'erase/program outside the active planned sector')
                if opcode == 0xFB01:
                    require(data is None and address == sector and not self.erase_verified,
                            'erase precondition failed')
                else:
                    require(self.erase_verified, 'program before a verified erase')
                    require(address % 512 == 0, 'program must be 512B aligned')
                    require(data is not None and len(data) == 512, 'program must be 512B')
                    require(data == self.live_target[address:address + 512],
                            'program data differs from the live target')
                    require(int.from_bytes(cdb[6:8], 'big') == 512
                            and int.from_bytes(cdb[9:11], 'little') == crc(data),
                            'program length/CRC mismatch')
                return self.exchange(cdb, size, data)
            except BaseException:
                self.close_gate()
                raise
        return super().execute(cdb, size, data)


def accept_session(loader_path):
    """Fresh normal QCY USB -> one mode switch -> BR28 ROM identity -> pinned
    RAM loader. Same entry sequence as the verified h3s_usb.session(); only
    the transport class differs. The loader file digest is checked by load_ram."""
    enter_rom()
    for _ in range(100):
        found = open_target(0x4c4a, 0x3442)
        if found:
            os.close(found[0])
            break
        time.sleep(.05)
    else:
        raise RuntimeError('BR28 ROM did not enumerate')
    t = AcceptRestoreTransport()
    LinuxTransport.__init__(t)
    try:
        inquiry = t.inquiry()
        require(inquiry[8:16].strip() == b'BR28' and inquiry[16:32].strip() == b'UBOOT1.00',
                'unexpected ROM chip/model')
        load_ram(t, loader_path)
        return t
    except BaseException:
        t.close()
        raise


# ---- executor (shared by flash / rollback / recovery; testable on fakes) ----

def execute_plan(t, live_target, fresh, order, journal):
    """Sectors in `order` (boot-info last): pre-erase readback == fresh, intent
    journal, erase, all-FF verify, per-block intent/program, whole-sector
    readback == live target, then a final full-chip readback == live target."""
    for sector in order:
        require(read(t, sector, SECTOR) == fresh[sector:sector + SECTOR],
                'sector changed before erase')
        t.begin_sector(sector)
        journal.event(stage='intent_erase', offset=sector)
        t.cmd(0xFB01, sector.to_bytes(4, 'big'))
        journal.event(stage='erase_done', offset=sector)
        require(read(t, sector, SECTOR) == b'\xff' * SECTOR, 'erase verification failed')
        journal.event(stage='erase_verified', offset=sector)
        t.mark_erase_verified()
        for p in range(sector, sector + SECTOR, 512):
            block = live_target[p:p + 512]
            journal.event(stage='intent_program', offset=p)
            cargs = p.to_bytes(4, 'big') + (512).to_bytes(2, 'big') + b'\0' \
                + crc(block).to_bytes(2, 'little')
            t.cmd(0xFB04, cargs, data=block)
            journal.event(stage='program_done', offset=p)
        require(read(t, sector, SECTOR) == live_target[sector:sector + SECTOR],
                'sector program verification failed')
        journal.event(stage='sector_verified', offset=sector)
        t.end_sector()
    final = read(t)
    require(final == live_target, 'final full-chip readback mismatch')
    journal.event(stage='FLASH_ACCEPT_VERIFIED', bytes=SIZE, sha256=sha(final),
                  runtime_behavior_requires_separate_confirmation=True)
    return final


# ---- interruption analysis (H5-PRE-01 offline discrimination) ----

def _sector_legal_states(sector, planned_before, planned_target, erase_intent,
                         erase_confirmed, block_intents, block_dones, verified):
    """Every on-device byte state an honest execution prefix allows for one
    sector. Sequential executor semantics: block j+1's intent is only written
    after block j's done row, so applied blocks = block_dones, plus possibly
    the one open intent (applied-but-response-lost window) = block_intents."""
    end = sector + SECTOR
    if verified:
        return [planned_target[sector:end]]
    states = []
    if not erase_intent:
        return [planned_before[sector:end]]
    erased = b'\xff' * SECTOR
    if erase_confirmed:
        states.append(erased)
    else:
        states.append(erased)
        states.append(planned_before[sector:end])   # intent sent, erase maybe not applied
    prefix = bytearray(erased)
    for k in (block_dones, block_intents):
        if k:
            prefix = bytearray(erased)
            prefix[0:k * 512] = planned_target[sector:sector + k * 512]
            states.append(bytes(prefix))
    return states


def analyze_interrupted(plan, planned_before, planned_target, journal_rows,
                        fresh, fresh2, nonce=None):
    """Accept only states explainable as an execution PREFIX of the journaled
    plan; emit a limited recovery plan (remaining sectors, fresh VM, new
    nonce) or raise. Journal-confirmed and content-observed progress are
    reported separately. A tampered state that happens to equal a legal
    intermediate state is byte-indistinguishable from real progress (honest
    boundary — recovery rewrites the remaining sectors to the same pinned
    target either way)."""
    require(len(fresh) == SIZE and fresh2 == fresh, 'fresh independent full reads differ')
    require(fresh[:BANK_B] == planned_before[:BANK_B] and fresh[VM_END:] == planned_before[VM_END:],
            'non-bank-B bytes changed against the plan; refusing recovery analysis')
    order = plan['write_order']
    facts = {p: {'erase_intent': False, 'erase_confirmed': False,
                 'block_intents': 0, 'block_dones': 0, 'verified': False} for p in order}
    for row in journal_rows:
        stage, offset = row.get('stage'), row.get('offset', 0)
        sector = offset // SECTOR * SECTOR
        require(sector in facts, 'journal references a sector outside the plan order')
        if stage == 'intent_erase':
            facts[sector]['erase_intent'] = True
        elif stage in ('erase_done', 'erase_verified'):
            facts[sector]['erase_confirmed'] = True
            facts[sector]['erase_intent'] = True
        elif stage == 'intent_program':
            facts[sector]['erase_confirmed'] = True
            facts[sector]['block_intents'] = max(facts[sector]['block_intents'],
                                                 (offset - sector) // 512 + 1)
        elif stage == 'program_done':
            facts[sector]['block_dones'] = max(facts[sector]['block_dones'],
                                               (offset - sector) // 512 + 1)
        elif stage == 'sector_verified':
            facts[sector]['verified'] = True
    remaining, journal_confirmed, content_complete, resume_from = [], [], [], None
    for p in order:
        fact = facts[p]
        legal = _sector_legal_states(p, planned_before, planned_target, **fact)
        actual = fresh[p:p + SECTOR]
        target_sector = planned_target[p:p + SECTOR]
        if actual == target_sector:
            content_complete.append(p)
            if not fact['verified'] and actual not in legal:
                raise ValueError('sector content equals the target without a '
                                 'journal explanation: ' + hex(p))
        elif fact['verified']:
            raise ValueError('journal says sector verified but content differs: ' + hex(p))
        elif actual in legal:
            remaining.append(p)
            if resume_from is None:
                resume_from = p
        else:
            raise ValueError('sector state not explainable as an execution prefix: ' + hex(p))
        if fact['verified']:
            journal_confirmed.append(p)
    if resume_from is None and remaining:
        raise ValueError('inconsistent remaining set')
    if resume_from is None:
        return {'status': 'INTERRUPTED_STATE_COMPLETE_CONTENT_VERIFICATION_ONLY',
                'journal_confirmed_complete': journal_confirmed,
                'content_equals_target': content_complete, 'resume_from': None,
                'recovery_plan': None,
                'boundary': 'all sectors already equal the target; re-verify with a '
                            'fresh full read instead of rewriting'}
    live_target = rebind_recovery_target(planned_before, planned_target, fresh, remaining)
    recovery = {
        'profile': plan['profile'] + '+recovery', 'profile_version': 1,
        'direction': plan['direction'], 'recovers_plan_digest': plan['plan_digest'],
        'before_sha256': sha(fresh), 'target_sha256': sha(live_target),
        'write_order': [p for p in order if p in remaining],
        'write_set_sorted': sorted(remaining), 'original_write_order': order,
        'nonce': nonce if nonce is not None else new_nonce(),
    }
    recovery['plan_digest'] = _plan_digest(recovery)
    return {'status': 'INTERRUPTED_STATE_EXPLAINED',
            'journal_confirmed_complete': journal_confirmed,
            'content_equals_target': content_complete, 'resume_from': resume_from,
            'recovery_plan': recovery,
            'boundary': 'a tampered state equal to a legal execution prefix is '
                        'byte-indistinguishable from real progress; recovery rewrites '
                        'the remaining sectors to the same pinned target either way'}


def rebind_recovery_target(planned_before, planned_target, fresh, remaining):
    """Recovery live target: fresh base map, remaining sectors from the pinned
    target, VM kept fresh. Never widens beyond the original audited set."""
    original = changed_sectors(planned_before, planned_target)
    require(set(remaining) <= original and remaining,
            'remaining set escapes the audited write set')
    live = bytearray(fresh)
    for p in remaining:
        live[p:p + SECTOR] = planned_target[p:p + SECTOR]
    live = bytes(live)
    require(changed_sectors(fresh, live) == set(remaining),
            'recovery rebinding touched unexpected sectors')
    require(live[:BANK_B] == planned_target[:BANK_B],
            'recovery target would modify [0, 0xee000)')
    require(live[VM_END:] == planned_target[VM_END:],
            'recovery target would modify [0x1de000, 0x200000)')
    require(live[VM_BEGIN:VM_END] == fresh[VM_BEGIN:VM_END], 'recovery must keep the freshest VM')
    return live


# ---- CLI steps ----

def _load_backups(work):
    first = (work / 'before-pass1.bin').read_bytes()
    require(first == (work / 'before-pass2.bin').read_bytes(), 'independent backups differ')
    return first


def _package_app(direction, candidate, ufw):
    from accept_profile import parse_package
    pinned_ufw, pinned_len, pinned_sha = package_id(direction, candidate)
    return parse_package(ufw, pinned_ufw, pinned_len, pinned_sha)


def cmd_plan(direction, args):
    candidate = getattr(args, 'candidate', 'hal5')
    work = Path(args.workdir)
    ufw = Path(args.package).read_bytes()
    before = _load_backups(work)
    target, plan = make_plan(direction, before, ufw, new_nonce(), candidate)
    if target is None:                      # zero-write ALREADY_* report
        save_persistent(work / 'plan.json',
                        (json.dumps(plan, indent=2, sort_keys=True) + '\n').encode())
        print(json.dumps(plan, indent=2, sort_keys=True))
        return
    verify_plan(plan, before, target, _package_app(direction, candidate, ufw))
    save_persistent(work / 'target.bin', target)
    save_persistent(work / 'plan.json',
                    (json.dumps(plan, indent=2, sort_keys=True) + '\n').encode())
    print(json.dumps(plan, indent=2, sort_keys=True))


def apply_direction(direction, args, transport_factory=None):
    candidate = getattr(args, 'candidate', 'hal5')
    require(args.apply, '%s requires --apply after reviewing plan.json' % direction)
    require(args.confirm_nonce, '--confirm-nonce must echo the plan nonce')
    work = Path(args.workdir)
    plan = json.loads((work / 'plan.json').read_text())
    require(plan.get('direction') == direction, 'plan direction mismatch')
    require(plan.get('candidate', 'hal5') == candidate, 'plan candidate mismatch')
    require('target_sha256' in plan,
            'plan is a zero-write ALREADY_* report; there is nothing to execute')
    require(plan.get('nonce') == args.confirm_nonce, 'nonce does not match plan.json')
    ufw = Path(args.package).read_bytes()
    before = _load_backups(work)
    target, regenerated = make_plan(direction, before, ufw, plan['nonce'], candidate)
    require(target == (work / 'target.bin').read_bytes(),
            'target file differs from the regenerated plan')
    require(regenerated == plan, 'plan.json differs from the regenerated plan')
    verify_plan(plan, before, target, _package_app(direction, candidate, ufw))

    pool = NoncePool(args.nonce_dir)
    require(not pool.occupied(plan['nonce']),
            'nonce already consumed by an earlier attempt; generate a NEW plan')
    pool.consume(plan['nonce'], {'direction': direction, 'candidate': candidate,
                                 'plan_digest': plan['plan_digest'],
                                 'workdir': str(work), 'nonce': plan['nonce']})
    require(not (work / 'restore.jsonl').exists(),
            'session already attempted; review its journal before any new action')
    journal = Journal(work / 'restore.jsonl')

    factory = transport_factory or (lambda: accept_session(Path(args.loader)))
    t = None
    try:
        t = factory()
        fresh = read(t)
        second = read(t)
        live_target = rebind_live_target(before, target, fresh, second)
        save_persistent(work / 'live-before.bin', fresh)
        save_persistent(work / 'live-before-pass2.bin', second)
        save_persistent(work / 'live-target.bin', live_target)
        journal.event(stage='fresh_double_read_verified', before_sha256=sha(fresh),
                      target_sha256=sha(live_target), vm_preserved=True,
                      candidate=candidate,
                      nonce=plan['nonce'], plan_digest=plan['plan_digest'])
        t.bind(live_target, plan['write_order'])
        t.writing = True
        execute_plan(t, live_target, fresh, plan['write_order'], journal)
    finally:
        if t is not None:
            t.close_gate()
            t.close()
        journal.close()
    print('Unplug USB and manually restart the headset once (documented closeout step).', flush=True)


def cmd_analyze(args):
    work = Path(args.workdir)
    plan = json.loads((work / 'plan.json').read_text())
    planned_before = _load_backups(work)
    planned_target = (work / 'target.bin').read_bytes()
    journal_rows = [json.loads(line) for line in
                    (work / 'restore.jsonl').read_text().splitlines() if line]
    freshwork = Path(args.freshworkdir)
    report = analyze_interrupted(plan, planned_before, planned_target, journal_rows,
                                 _load_backups(freshwork),
                                 (freshwork / 'before-pass2.bin').read_bytes())
    if report['recovery_plan'] is not None:
        dest = work / 'recovery-plan.json'
        require(not dest.exists(),
                'recovery-plan.json already exists (from an earlier analysis); move it '
                'away after review — this tool never overwrites recovery artifacts')
        save_persistent(dest,
                        (json.dumps(report['recovery_plan'], indent=2, sort_keys=True) + '\n').encode())
    save_persistent(Path(args.freshworkdir) / 'interruption-report.json',
                    (json.dumps(report, indent=2, sort_keys=True) + '\n').encode())
    print(json.dumps(report, indent=2, sort_keys=True))


def apply_recovery(args, transport_factory=None):
    require(args.apply, 'recover requires --apply after reviewing recovery-plan.json')
    require(args.confirm_nonce, '--confirm-nonce must echo the recovery plan nonce')
    work = Path(args.workdir)
    recovery = json.loads((work / 'recovery-plan.json').read_text())
    require(recovery['nonce'] == args.confirm_nonce, 'nonce does not match recovery-plan.json')
    original = json.loads((work / 'plan.json').read_text())
    planned_before = _load_backups(work)
    planned_target = (work / 'target.bin').read_bytes()
    journal_rows = [json.loads(line) for line in
                    (work / 'restore.jsonl').read_text().splitlines() if line]
    freshwork = Path(args.freshworkdir)
    fresh = _load_backups(freshwork)
    report = analyze_interrupted(original, planned_before, planned_target, journal_rows,
                                 fresh, (freshwork / 'before-pass2.bin').read_bytes(),
                                 nonce=recovery['nonce'])
    require(report['recovery_plan'] == recovery,
            'recovery-plan.json no longer matches the current interruption analysis')
    remaining = recovery['write_order']
    live_target = rebind_recovery_target(planned_before, planned_target, fresh, remaining)

    pool = NoncePool(args.nonce_dir)
    require(not pool.occupied(recovery['nonce']), 'recovery nonce already consumed')
    pool.consume(recovery['nonce'], {'direction': 'recover',
                                     'plan_digest': recovery['plan_digest'],
                                     'recovers_plan_digest': recovery['recovers_plan_digest'],
                                     'workdir': str(work), 'nonce': recovery['nonce']})
    journal = Journal(work / ('recovery-%s.jsonl' % recovery['nonce'][:8]))
    factory = transport_factory or (lambda: accept_session(Path(args.loader)))
    t = None
    try:
        t = factory()
        second = read(t)
        require(second == read(t), 'fresh independent full reads differ')
        require(second == fresh, 'device changed between the analysis backup and recovery')
        journal.event(stage='recovery_fresh_verified', target_sha256=sha(live_target),
                      remaining_sectors=remaining, nonce=recovery['nonce'])
        t.bind(live_target, remaining)
        t.writing = True
        execute_plan(t, live_target, fresh, remaining, journal)
    finally:
        if t is not None:
            t.close_gate()
            t.close()
        journal.close()
    print('Unplug USB and manually restart the headset once (documented closeout step).', flush=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='operation', required=True)
    q = sub.add_parser('plan-flash',
                       help='offline: bank-B flash plan (hal5: b79->HAL5; f1a/f1b: HAL5->F1x) '
                            'from a backup workdir + candidate UFW')
    q.add_argument('--workdir', required=True)
    q.add_argument('--package', required=True,
                   help='candidate UFW to install (digest-pinned per --candidate)')
    q.add_argument('--candidate', choices=list(CANDIDATES), default='hal5',
                   help='app identity to flash (default hal5 = the original b79->HAL5 pair)')
    q = sub.add_parser('plan-rollback',
                       help='offline: rollback plan (hal5: HAL5->b79, or ALREADY_B79; '
                            'f1a/f1b: F1x->HAL5 same-layer, or ALREADY_HAL5)')
    q.add_argument('--workdir', required=True)
    q.add_argument('--package', required=True,
                   help='rollback target UFW (b79 for hal5, HAL5 for f1a/f1b; digest-pinned)')
    q.add_argument('--candidate', choices=list(CANDIDATES), default='hal5',
                   help='candidate being rolled back (default hal5 = the original HAL5->b79)')
    for name in ('flash', 'rollback'):
        q = sub.add_parser(name, help='live: execute the reviewed plan (needs --apply + nonce)')
        q.add_argument('--workdir', required=True)
        q.add_argument('--package', required=True)
        q.add_argument('--candidate', choices=list(CANDIDATES), default='hal5',
                       help='app identity this plan is for (must match plan.json)')
        q.add_argument('--loader', required=True)
        q.add_argument('--apply', action='store_true')
        q.add_argument('--confirm-nonce', dest='confirm_nonce', default=None)
        q.add_argument('--nonce-dir', dest='nonce_dir', type=Path, default=DEFAULT_NONCE_DIR)
    q = sub.add_parser('analyze',
                       help='offline: explain an interrupted session from a NEW fresh double read')
    q.add_argument('--workdir', required=True)
    q.add_argument('--freshworkdir', required=True,
                   help='backup workdir of the post-interruption double read (h3s_usb.py backup)')
    q = sub.add_parser('recover', help='live: execute a reviewed recovery plan (needs --apply + nonce)')
    q.add_argument('--workdir', required=True)
    q.add_argument('--freshworkdir', required=True)
    q.add_argument('--loader', required=True)
    q.add_argument('--apply', action='store_true')
    q.add_argument('--confirm-nonce', dest='confirm_nonce', default=None)
    q.add_argument('--nonce-dir', dest='nonce_dir', type=Path, default=DEFAULT_NONCE_DIR)
    args = p.parse_args()
    if args.operation == 'plan-flash':
        cmd_plan('flash', args)
    elif args.operation == 'plan-rollback':
        cmd_plan('rollback', args)
    elif args.operation in ('flash', 'rollback'):
        apply_direction(args.operation, args)
    elif args.operation == 'analyze':
        cmd_analyze(args)
    elif args.operation == 'recover':
        apply_recovery(args)


if __name__ == '__main__':
    main()
