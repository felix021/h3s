#!/usr/bin/env python3
"""World snapshot/restore framework — S1 module half (serialization + fast tier).

Implements the format and adapter layer of docs/snapshot-design.md (route BD):
layered snapshot files (magic+version+code-fingerprint header, class-attribute
manifest, type-tagged body, RAM run-length section), the drift double gate
(code fingerprint + manifest, both refuse loading with a clear error) and the
round-trip reflexivity check used by the fast tier.

Scope of this module half (route BJ, sim-snapmod): the codec, the fingerprint
and manifest gates, and a per-module capture/restore ADAPTER REGISTRY with
minimal small-world implementations for M1 (flat CPU stub), M2 (three-dict
memory), M3 (timer/IRQ/software-IRQ/scalar bookkeeping on BR28Model), M5
(delivery bookkeeping on DualCoreMachine), M6 (MMU) and M7 (loop ledger).
The capture WIRING (probe_boot explore-loop capture point, the full M1 CPU
field inventory, the M4 projection families, and the equivalence comparison
against a full replay) is the follow-up route's work after BF merges; nothing
here edits the running loop. Snapshots from this module are valid for small
worlds only — capture refuses any world containing state kinds outside the
wired adapters instead of silently dropping them (the codex missing-state
trap).

Notable format decisions (documented deviations/extensions vs the blueprint):
- Collection tags ($pairs/$set/$counter) extend the scalar whitelist so sets,
  Counters and int-keyed maps round-trip type-exact (they are state per M5/M7).
- The code fingerprint hashes the design-§2.3 source files, the upstream
  simulator PIN, the format version and the registered adapter shapes;
  capture-time scenario flags live in header.capture_context instead, because
  a load must be able to recompute the fingerprint without capture-time
  knowledge. restore_world(..., expected_context=...) gives consumers the
  scenario gate.
"""
import hashlib
import json
import struct
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

from br28_unknown import UnknownWord
from br28_icfg import IcfgValue
from br28_model import GpcntConValue, GpioOutValue, PartialWord
from probe_pi32v2 import PIN

TOOLS = Path(__file__).resolve().parent
FORMAT_VERSION = 1
MAGIC = b'H3S-SNAP'

# Drift gate 1 inputs (design §2.3): simulation semantics sources + this file.
# The wiring route extends the hashed inputs with scenario-flag serialization
# when the capture point lands.
FINGERPRINT_FILES = ('probe_boot.py', 'br28_model.py', 'br28_machine.py', 'br28_icfg.py',
                     'br28_unknown.py', 'probe_irq_entry.py', 'probe_dualcore.py',
                     'probe_platform_startup.py', 'probe_arch_startup.py',
                     'probe_clock_startup.py', 'probe_boot_handoff.py',
                     'frontier_registry.py', 'world_snapshot.py')


class SnapshotError(RuntimeError):
    """Every refusal: format errors, drift gates, uncaptured state kinds."""


# ---------------------------------------------------------------------------
# Type-tagged value codec (body values + RAM icfg_spills)
# ---------------------------------------------------------------------------

_ABSTRACT_FIELDS = {
    UnknownWord: ('bytes', 'origins'),
    IcfgValue: ('value', 'known', 'origin', 'identity'),
    GpioOutValue: ('address', 'value', 'known'),
    GpcntConValue: ('value', 'known'),
    PartialWord: ('value', 'known'),
}
_ABSTRACT_TAGS = {'$unknown_word': UnknownWord, '$icfg_value': IcfgValue,
                  '$gpio_out_value': GpioOutValue, '$gpcnt_con_value': GpcntConValue,
                  '$partial_word': PartialWord}
_ABSTRACT_CONSTRUCTORS = {
    UnknownWord: lambda f: (f['bytes'], f['origins']),
    IcfgValue: lambda f: (f['value'], f['known'], tuple(f['origin']), f['identity']),
    GpioOutValue: lambda f: (f['address'], f['value'], f['known']),
    GpcntConValue: lambda f: (f['value'], f['known']),
    PartialWord: lambda f: (f['value'], f['known']),
}


def _sorted_items(items):
    """Deterministically sort (key, value) items; refuse mixed-type keys."""
    items = list(items)
    kinds = {type(key).__name__ for key, _ in items}
    if not kinds <= {'int', 'str', 'bool'}:
        raise SnapshotError('collection keys must be int/str/bool, got ' + ','.join(sorted(kinds)))
    if len(kinds - {'bool'}) > 1:
        raise SnapshotError('mixed str/int collection keys are not supported')
    return sorted(items, key=lambda item: (isinstance(item[0], str), item[0]))


def _sorted_elements(elements):
    elements = list(elements)
    kinds = {type(item).__name__ for item in elements}
    if not kinds <= {'int', 'str', 'bool'}:
        raise SnapshotError('set/collection elements must be int/str/bool, got ' +
                            ','.join(sorted(kinds)))
    return sorted(elements, key=lambda item: (isinstance(item, str), item))


def encode_value(value):
    """Encode one state value into JSON-safe data; whitelist violations refuse.

    Whitelist: None/bool/int/str/bytes(hex)/list/dict(str keys) + type tags for
    the five abstract value classes and the three collection tags
    ($pairs/$set/$counter; $tuple keeps tuples type-exact). Anything else
    (float, raw set/Counter, arbitrary objects, int-keyed dicts) raises so a
    new state kind fails loudly instead of being silently dropped.
    """
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, bytes):
        return {'$bytes': value.hex()}
    if isinstance(value, list):
        return [encode_value(item) for item in value]
    if isinstance(value, tuple):
        return {'$tuple': [encode_value(item) for item in value]}
    if isinstance(value, Counter):  # Counter is a dict subclass: check it before dict
        return {'$counter': [[key, encode_value(value[key])] for key, _ in
                             _sorted_items(value.items())]}
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise SnapshotError('plain dict values need str keys; capture int-keyed '
                                'maps with the $pairs tag instead')
        return {key: encode_value(item) for key, item in value.items()}
    if isinstance(value, (set, frozenset)):
        return {'$set': _sorted_elements(value)}
    for tag, cls in _ABSTRACT_TAGS.items():
        if type(value) is cls:
            return {tag: {field: encode_value(getattr(value, field))
                          for field in _ABSTRACT_FIELDS[cls]}}
    raise SnapshotError('unserializable state value of type ' + type(value).__name__)


def decode_value(encoded):
    if encoded is None or isinstance(encoded, (bool, int, str)):
        return encoded
    if isinstance(encoded, list):
        return [decode_value(item) for item in encoded]
    if not isinstance(encoded, dict):
        raise SnapshotError('malformed encoded value: ' + repr(encoded)[:80])
    tags = [key for key in encoded if key.startswith('$')]
    if not tags:
        return {key: decode_value(item) for key, item in encoded.items()}
    if len(tags) != 1:
        raise SnapshotError('malformed value with multiple tags: ' + ','.join(sorted(tags)))
    tag = tags[0]
    if tag == '$bytes':
        try:
            return bytes.fromhex(encoded[tag])
        except (TypeError, ValueError) as error:
            raise SnapshotError('malformed $bytes tag: ' + str(error)) from None
    if tag == '$tuple':
        return tuple(decode_value(item) for item in encoded[tag])
    if tag == '$set':
        return set(_sorted_elements(decode_value(item) for item in encoded[tag]))
    if tag == '$counter':
        return Counter({decode_value(key): decode_value(item) for key, item in encoded[tag]})
    if tag == '$pairs':
        return {decode_value(key): decode_value(item) for key, item in encoded[tag]}
    if tag in _ABSTRACT_TAGS:
        cls = _ABSTRACT_TAGS[tag]
        try:
            fields = encoded[tag]
            if not isinstance(fields, dict):
                raise KeyError('tag payload is not an object')
            return cls(*_ABSTRACT_CONSTRUCTORS[cls](
                {key: decode_value(item) for key, item in fields.items()}))
        except (KeyError, TypeError, ValueError) as error:
            raise SnapshotError(f'malformed {tag} tag: {error}') from None
    raise SnapshotError('unknown value tag in snapshot body: ' + tag)


def pairs_of(mapping):
    """Capture an int-keyed (or str-keyed) mapping as a $pairs tag."""
    return {'$pairs': [[key, encode_value(item)] for key, item in _sorted_items(mapping.items())]}


def encode_static_value(name, value, module):
    """Encode a static-configuration attribute (M8 rebuild guard).

    Static values are scenario switches/fixtures rebuilt by the construction
    chain; capture records them so restore can refuse a differently-switched
    target world. Anything that is neither captured state nor a static value
    refuses with the attribute name (missing-state guard).
    """
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, bytes):
        return {'$bytes': value.hex()}
    if isinstance(value, Counter):
        raise SnapshotError(f'{module}: static attribute {name} is a Counter — state, not a '
                            'static fixture; add it to the module state list')
    if isinstance(value, (set, frozenset)):
        try:
            return {'$set': _sorted_elements(value)}
        except SnapshotError:
            raise SnapshotError(f'{module}: static attribute {name} has unsupported set '
                                'elements') from None
    if isinstance(value, tuple):
        return {'$tuple': [encode_static_value(name, item, module) for item in value]}
    if isinstance(value, list):
        if not all(item is None or isinstance(item, (bool, int, str)) for item in value):
            raise SnapshotError(f'{module}: static attribute {name} has unsupported list elements')
        return list(value)
    if isinstance(value, dict):
        if value and all(isinstance(key, (int, bool)) for key in value):
            return {'$pairs': [[key, encode_static_value(name, item, module)] for key, _ in
                               _sorted_items(value.items())]}
        if all(isinstance(key, str) for key in value):
            return {key: encode_static_value(name, item, module) for key, item in value.items()}
        raise SnapshotError(f'{module}: static attribute {name} has unsupported dict keys')
    raise SnapshotError(f'{module}: attribute {name} of type {type(value).__name__} is neither '
                        'captured state nor a static fixture — extend the snapshot adapter '
                        'before capturing this world (missing-state guard)')


# ---------------------------------------------------------------------------
# Round-trip reflexivity: deep field comparison (check #0)
# ---------------------------------------------------------------------------

def assert_equal(actual, expected, path='value'):
    """Field-by-field equality for restored vs original state.

    Compares the five abstract value classes by their fields (their own __eq__
    deliberately raises Blocked), treats list/tuple as equivalent containers
    (tuples decode as lists), and names the first differing path.
    """
    for cls in _ABSTRACT_FIELDS:
        if isinstance(expected, cls) or isinstance(actual, cls):
            if type(actual) is not type(expected):
                raise AssertionError(f'{path}: type {type(actual).__name__} != '
                                     f'{type(expected).__name__}')
            for field in _ABSTRACT_FIELDS[cls]:
                assert_equal(getattr(actual, field), getattr(expected, field), f'{path}.{field}')
            return
    if isinstance(expected, (list, tuple)) or isinstance(actual, (list, tuple)):
        if not (isinstance(actual, (list, tuple)) and isinstance(expected, (list, tuple))):
            raise AssertionError(f'{path}: container type mismatch '
                                 f'({type(actual).__name__} vs {type(expected).__name__})')
        if len(actual) != len(expected):
            raise AssertionError(f'{path}: length {len(actual)} != {len(expected)}')
        for index, (a, e) in enumerate(zip(actual, expected)):
            assert_equal(a, e, f'{path}[{index}]')
        return
    if isinstance(expected, (set, frozenset, Counter)) or isinstance(actual, (set, frozenset, Counter)):
        # Before the dict branch: Counter is a dict subclass, and a dict/Counter
        # type drift must be red, not silently comparable.
        if type(actual) is not type(expected):
            raise AssertionError(f'{path}: collection type {type(actual).__name__} != '
                                 f'{type(expected).__name__}')
        if actual != expected:
            raise AssertionError(f'{path}: collection content differs: '
                                 f'{sorted(actual, key=repr)[:8]} vs {sorted(expected, key=repr)[:8]}')
        return
    if isinstance(expected, dict) or isinstance(actual, dict):
        if not (isinstance(actual, dict) and isinstance(expected, dict)):
            raise AssertionError(f'{path}: dict mismatch '
                                 f'({type(actual).__name__} vs {type(expected).__name__})')
        if set(actual) != set(expected):
            missing = sorted(set(expected) - set(actual), key=repr)
            extra = sorted(set(actual) - set(expected), key=repr)
            raise AssertionError(f'{path}: keys differ, missing={missing} extra={extra}')
        for key in expected:
            assert_equal(actual[key], expected[key], f'{path}[{key!r}]')
        return
    if actual != expected:
        raise AssertionError(f'{path}: {actual!r} != {expected!r}')


# ---------------------------------------------------------------------------
# RAM run-length codec (M2: known-byte runs; holes between runs stay unknown)
# ---------------------------------------------------------------------------

def encode_ram_runs(ram):
    """dict address->byte into merged (start, length, bytes) runs, sorted.

    Key absence means an unknown byte — never a zero read — so the runs only
    cover known bytes and the holes between runs carry the absence semantics.
    """
    runs = []
    start = previous = None
    data = None
    for address in sorted(ram):
        byte = ram[address]
        if (not isinstance(address, int) or not 0 <= address <= 0xffffffff
                or not isinstance(byte, int) or not 0 <= byte <= 255):
            raise SnapshotError('invalid RAM byte at ' + repr(address))
        if start is not None and address == previous + 1:
            data += bytes([byte])
            previous = address
            continue
        if start is not None:
            runs.append((start, previous - start + 1, data))
        start = previous = address
        data = bytes([byte])
    if start is not None:
        runs.append((start, previous - start + 1, data))
    return runs


def decode_ram_runs(segments):
    """Inverse of encode_ram_runs; refuses overlapping/unsorted/malformed runs."""
    ram = {}
    previous_end = None
    for segment in segments:
        if not isinstance(segment, (list, tuple)) or len(segment) != 3:
            raise SnapshotError('malformed RAM segment record')
        start, length, data = segment
        if (not isinstance(start, int) or not isinstance(length, int) or length < 1
                or not 0 <= start <= 0xffffffff or start + length > 0x100000000):
            raise SnapshotError('invalid RAM segment bounds')
        if not isinstance(data, (bytes, bytearray)) or len(data) != length:
            raise SnapshotError('RAM segment length does not match its byte stream')
        if previous_end is not None and start < previous_end:
            raise SnapshotError('overlapping or unsorted RAM segments')
        previous_end = start + length
        for offset, byte in enumerate(data):
            ram[start + offset] = byte
    return ram


def encode_ram_section(ram, unknown_origins, icfg_spills):
    """Full M2 RAM section: known runs + provenance map + partial ICFG spills.

    unknown_origins maps unknown-byte addresses to physical source addresses;
    icfg_spills maps word addresses to partially-known IcfgValue words. Both
    complement the byte runs (design §1 M2).
    """
    for address, origin in unknown_origins.items():
        if not isinstance(address, int) or not isinstance(origin, int) or not 0 <= origin <= 0xffffffff:
            raise SnapshotError('invalid unknown-origin record')
    for address, value in icfg_spills.items():
        if not isinstance(address, int) or not isinstance(value, IcfgValue):
            raise SnapshotError('icfg_spills values must be IcfgValue words')
    return SimpleNamespace(
        segments=encode_ram_runs(ram),
        unknown_origins=[[key, item] for key, item in _sorted_items(unknown_origins.items())],
        icfg_spills=[[address, encode_value(value)]
                     for address, value in _sorted_items(icfg_spills.items())])


def decode_ram_section(section):
    ram = decode_ram_runs(section.segments)
    unknown_origins = {}
    for address, origin in section.unknown_origins:
        if address in unknown_origins or address in ram:
            raise SnapshotError('duplicate or inconsistent unknown-origin address')
        unknown_origins[address] = origin
    icfg_spills = {}
    for address, encoded in section.icfg_spills:
        value = decode_value(encoded)
        if not isinstance(value, IcfgValue):
            raise SnapshotError('icfg_spills section contains a non-IcfgValue word')
        icfg_spills[address] = value
    return SimpleNamespace(ram=ram, unknown_origins=unknown_origins, icfg_spills=icfg_spills)


def apply_ram_section(mem, section):
    """Restore the RAM section onto a live memory object (three-dict shape)."""
    decoded = decode_ram_section(section)
    mem.ram.update(decoded.ram)
    mem.unknown_origins.update(decoded.unknown_origins)
    mem.icfg_spills.update(decoded.icfg_spills)
    return decoded


# ---------------------------------------------------------------------------
# Snapshot container: header + manifest + body + ram sections
# ---------------------------------------------------------------------------

def _record_class(record):
    if not isinstance(record, dict) or 'class' not in record or 'fields' not in record:
        raise SnapshotError('malformed object record (need class/fields)')
    return record['class']


def _record_fields(record):
    fields = record['fields']
    if not isinstance(fields, dict) or any(not isinstance(key, str) for key in fields):
        raise SnapshotError('malformed object record fields')
    return sorted(fields)


def derive_manifest(body):
    """Manifest = per module, per object key: [class, sorted field names], plus
    scalar/static attribute names. Derived from the body so the stored manifest
    section is a tamper-detecting checksum of the state shape, not extra data."""
    manifest = {}
    for module, payload in body.items():
        if not isinstance(payload, dict):
            raise SnapshotError(f'module {module} payload is not an object')
        entry = {'objects': {}, 'scalars': sorted(payload.get('scalars', {})),
                 'static': sorted(payload.get('static', {}))}
        for key, record in payload.get('objects', {}).items():
            if record is None:
                continue
            records = record if isinstance(record, list) else [record]
            shapes = {(_record_class(item), tuple(_record_fields(item))) for item in records}
            if len(shapes) != 1:
                raise SnapshotError(f'{module}.{key}: records with inconsistent class/field shape')
            name, fields = shapes.pop()
            entry['objects'][key] = [name, list(fields)]
        manifest[module] = entry
    return manifest


def _manifest_diff(stored, derived):
    if set(stored) != set(derived):
        return ('module set differs: ' +
                ','.join(sorted({str(key) for key in stored} ^ {str(key) for key in derived})))
    for module in sorted(stored, key=str):
        if stored[module] != derived[module]:
            for kind in ('objects', 'scalars', 'static'):
                a, b = stored[module].get(kind), derived[module].get(kind)
                if a != b:
                    return f'{module}.{kind} differs (manifest says {a}, body says {b})'
    return None


class WorldSnapshot:
    """In-memory snapshot: header/manifest/body (JSON) + ram (binary section)."""

    def __init__(self, header, manifest, body, ram):
        self.header = header
        self.manifest = manifest
        self.body = body
        self.ram = ram

    @property
    def captured_modules(self):
        return list(self.header['captured_modules'])

    def digest(self):
        """Stable content digest for evidence summaries (design §3.2)."""
        return hashlib.sha256(self.encode()).hexdigest()

    def encode(self):
        """Deterministic bytes: canonical JSON (sorted keys) + sorted RAM runs."""
        self.manifest = derive_manifest(self.body)
        header = dict(self.header)
        header['captured_modules'] = sorted(self.body)
        header['format_version'] = FORMAT_VERSION
        self.header = header
        parts = [MAGIC, struct.pack('<I', FORMAT_VERSION)]
        for blob in (header, self.manifest, self.body):
            encoded = json.dumps(blob, sort_keys=True, separators=(',', ':')).encode()
            parts.append(struct.pack('<I', len(encoded)))
            parts.append(encoded)
        parts.append(self._encode_ram(self.ram))
        return b''.join(parts)

    @staticmethod
    def _encode_ram(ram):
        parts = [struct.pack('<I', len(ram.segments))]
        for start, length, data in ram.segments:
            if not isinstance(data, (bytes, bytearray)):
                raise SnapshotError('RAM segment byte stream is not bytes')
            parts.append(struct.pack('<II', start, length))
            parts.append(bytes(data))
        parts.append(struct.pack('<I', len(ram.unknown_origins)))
        for address, origin in ram.unknown_origins:
            parts.append(struct.pack('<II', address, origin))
        parts.append(struct.pack('<I', len(ram.icfg_spills)))
        for address, encoded in ram.icfg_spills:
            blob = json.dumps(encoded, sort_keys=True, separators=(',', ':')).encode()
            parts.append(struct.pack('<II', address, len(blob)))
            parts.append(blob)
        return b''.join(parts)

    @classmethod
    def from_bytes(cls, data, *, expected_fingerprint=None):
        """Decode + run both drift gates; any refusal raises SnapshotError."""
        def read_u32(offset, what):
            if offset + 4 > len(data):
                raise SnapshotError(f'truncated snapshot while reading {what}')
            return struct.unpack_from('<I', data, offset)[0], offset + 4

        if data[:8] != MAGIC:
            raise SnapshotError('not an H3S world snapshot (magic mismatch)')
        offset = 8
        version, offset = read_u32(offset, 'format version')
        if version != FORMAT_VERSION:
            raise SnapshotError(f'unsupported snapshot format version {version} (supported: '
                                f'{FORMAT_VERSION}); regenerate the snapshot')
        sections = []
        for name in ('header', 'manifest', 'body'):
            length, offset = read_u32(offset, name + ' length')
            if offset + length > len(data):
                raise SnapshotError(f'truncated snapshot while reading {name}')
            try:
                sections.append(json.loads(data[offset:offset + length].decode('utf-8')))
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise SnapshotError(f'malformed {name} JSON: {error}') from None
            offset += length
        header, manifest, body = sections
        ram, offset = cls._decode_ram(data, offset)
        if offset != len(data):
            raise SnapshotError('trailing bytes after the RAM section')
        # Drift gate 1: code fingerprint.
        current = expected_fingerprint or code_fingerprint()
        if header.get('code_fingerprint') != current:
            raise SnapshotError('code fingerprint mismatch: the snapshot was captured under '
                                'different simulation sources (snapshot '
                                + str(header.get('code_fingerprint'))[:16] + '... vs current '
                                + current[:16] + '...) — regenerate the snapshot (~3 minutes '
                                'for the canonical frontier point)')
        # Drift gate 2: stored manifest must match the state shape in the body.
        derived = derive_manifest(body)
        if manifest != derived:
            raise SnapshotError('manifest mismatch: the snapshot file is inconsistent or was '
                                'tampered with — ' +
                                (_manifest_diff(manifest, derived) or 'content differs'))
        snapshot = cls(header, manifest, body, ram)
        if 'M2' in snapshot.captured_modules:
            fields = snapshot.body['M2']['objects']['memory']['fields']
            if (fields.get('ram') != sum(length for _, length, _ in ram.segments)
                    or fields.get('unknown_origins') != len(ram.unknown_origins)
                    or fields.get('icfg_spills') != len(ram.icfg_spills)):
                raise SnapshotError('RAM section does not match the M2 manifest counts')
        return snapshot

    @staticmethod
    def _decode_ram(data, offset):
        def read_u32(off, what):
            if off + 4 > len(data):
                raise SnapshotError(f'truncated snapshot while reading {what}')
            return struct.unpack_from('<I', data, off)[0], off + 4

        count, offset = read_u32(offset, 'RAM segment count')
        segments = []
        for _ in range(count):
            start, offset = read_u32(offset, 'segment start')
            length, offset = read_u32(offset, 'segment length')
            if offset + length > len(data):
                raise SnapshotError('truncated snapshot inside a RAM segment')
            segments.append([start, length, data[offset:offset + length]])
            offset += length
        count, offset = read_u32(offset, 'unknown-origin count')
        origins = []
        for _ in range(count):
            address, offset = read_u32(offset, 'origin address')
            origin, offset = read_u32(offset, 'origin value')
            origins.append([address, origin])
        count, offset = read_u32(offset, 'icfg-spill count')
        spills = []
        for _ in range(count):
            address, offset = read_u32(offset, 'spill address')
            length, offset = read_u32(offset, 'spill JSON length')
            if offset + length > len(data):
                raise SnapshotError('truncated snapshot inside an icfg spill')
            try:
                encoded = json.loads(data[offset:offset + length].decode('utf-8'))
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise SnapshotError(f'malformed icfg spill JSON: {error}') from None
            spills.append([address, encoded])
            offset += length
        section = SimpleNamespace(segments=segments, unknown_origins=origins, icfg_spills=spills)
        return section, offset

    def save(self, path):
        """Write the snapshot file. Keep snapshot files out of git (analysis/
        or local temp only; the repo red line forbids publishing RAM mirrors)."""
        data = self.encode()
        Path(path).write_bytes(data)
        return data

    @classmethod
    def load(cls, path, *, expected_fingerprint=None):
        return cls.from_bytes(Path(path).read_bytes(), expected_fingerprint=expected_fingerprint)


# ---------------------------------------------------------------------------
# Module adapter registry (M1-M9 registration points)
# ---------------------------------------------------------------------------

class ModuleAdapter:
    def __init__(self, key, component, capture, restore, fields, scalars, description):
        self.key = key
        self.component = component      # world attribute the adapter consumes
        self.capture = capture          # None = by-design absence (M8/M9) or wiring route
        self.restore = restore
        self.fields = fields            # class name -> state field tuple (manifest seed)
        self.scalars = scalars          # scalar attribute names on the module's carrier
        self.description = description

    def shape_seed(self):
        return {'component': self.component,
                'fields': {name: sorted(fields) for name, fields in self.fields.items()},
                'scalars': sorted(self.scalars)}


MODULES = {}


def register_module(key, component, capture=None, restore=None, fields=None, scalars=(),
                    description=''):
    if key in MODULES:
        raise SnapshotError(f'module {key} is already registered')
    MODULES[key] = ModuleAdapter(key, component, capture, restore, dict(fields or {}),
                                 tuple(scalars), description)
    return MODULES[key]


def adapter_shape_seed():
    return {key: MODULES[key].shape_seed() for key in sorted(MODULES)}


def code_fingerprint(extra_sources=()):
    """Drift gate 1: SHA256 over the simulation sources + upstream PIN + format
    version + registered adapter shapes. Any model/adapter change invalidates
    existing snapshots; regeneration is the only migration path (design §2.3)."""
    digest = hashlib.sha256()
    digest.update(b'h3s-world-snapshot-fingerprint-v1')
    digest.update(b'format=' + str(FORMAT_VERSION).encode())
    digest.update(b'upstream-pin=' + PIN.encode())
    for name in FINGERPRINT_FILES:
        path = TOOLS / name
        digest.update(name.encode() + b'=')
        digest.update(hashlib.sha256(path.read_bytes()).hexdigest().encode())
    for label, blob in extra_sources:
        digest.update(b'extra:' + label.encode() + b'=')
        digest.update(hashlib.sha256(blob).hexdigest().encode())
    digest.update(b'adapter-shape=' + json.dumps(adapter_shape_seed(), sort_keys=True).encode())
    return digest.hexdigest()


# --- generic object-record helpers -----------------------------------------

def capture_object(obj, fields, module, key):
    record = {'class': type(obj).__name__, 'fields': {}}
    for field in fields:
        if not hasattr(obj, field):
            raise SnapshotError(f'{module}.{key}: attribute {field!r} missing on '
                                f'{type(obj).__name__} — state shape differs from the capture '
                                'field list')
        record['fields'][field] = encode_value(getattr(obj, field))
    return record


def restore_object(record, obj, module, key):
    if record['class'] != type(obj).__name__:
        raise SnapshotError(f'{module}.{key}: snapshot holds {record["class"]} state but the '
                            f'restore target is {type(obj).__name__}')
    for field, encoded in record['fields'].items():
        setattr(obj, field, decode_value(encoded))


def capture_static(obj, module, skip=()):
    return {name: encode_static_value(name, value, module)
            for name, value in sorted(vars(obj).items()) if name not in skip}


def check_static(snapshot_static, obj, module, skip=()):
    current = capture_static(obj, module, skip)
    if snapshot_static != current:
        differing = [name for name in sorted(set(snapshot_static) | set(current))
                     if snapshot_static.get(name) != current.get(name)]
        raise SnapshotError(f'{module}: static configuration mismatch on {differing} — rebuild '
                            'the restore target with the same scenario switches as the captured '
                            'world (the snapshot header records the capture context)')


def _capture_scalar(value):
    """Typed collection capture for scalar attributes (dict/Counter/set -> tags)."""
    if isinstance(value, Counter):  # Counter is a dict subclass: check it before dict
        return {'$counter': [[key, encode_value(value[key])] for key, _ in
                             _sorted_items(value.items())]}
    if isinstance(value, dict):
        return pairs_of(value)
    if isinstance(value, set):
        return {'$set': _sorted_elements(value)}
    return encode_value(value)


# --- M1: CPU objects (minimal small-world field list) ------------------------

M1_CPU_FIELDS = ('core_id', 'pc', 'steps', 'seq', 'R', 'flags', 'sp', 'sp_known',
                 'usp', 'ssp', 'reti', 'rets', 'irq_psr', 'icfg', 'icfg_known',
                 'icfg_unknown_mask')


def _capture_m1(world):
    if world.cpus is None:
        return None
    objects = {}
    for key, cpu in world.cpus.items():
        extra = sorted(set(vars(cpu)) - set(M1_CPU_FIELDS))
        missing = sorted(set(M1_CPU_FIELDS) - set(vars(cpu)))
        if extra or missing:
            raise SnapshotError('M1: CPU objects with a non-minimal attribute set cannot be '
                                f'captured yet ({key}: extra={extra} missing={missing}); the '
                                'full M1 inventory (execution context, repeat/after_then, bank '
                                'bookkeeping, task frames, IcfgState) is the capture-wiring '
                                "route's work")
        objects[key] = capture_object(cpu, M1_CPU_FIELDS, 'M1', key)
    return {'objects': objects, 'scalars': {}, 'static': {}}


def _restore_m1(payload, world):
    if world.cpus is None:
        raise SnapshotError('M1 state present but no cpus= restore target was provided')
    if set(payload['objects']) != set(world.cpus):
        raise SnapshotError(f'M1: snapshot CPU keys {sorted(payload["objects"])} differ from '
                            f'the restore targets {sorted(world.cpus)}')
    for key, cpu in world.cpus.items():
        restore_object(payload['objects'][key], cpu, 'M1', key)


# --- M2: shared memory (three-dict shape) ------------------------------------

def _capture_m2(world):
    if world.mem is None:
        return None
    extra = sorted(set(vars(world.mem)) - {'ram', 'unknown_origins', 'icfg_spills'})
    if extra:
        raise SnapshotError(f'M2: the memory object carries unexpected attributes {extra} — '
                            'extend the memory adapter before capturing this world')
    section = encode_ram_section(world.mem.ram, world.mem.unknown_origins,
                                 world.mem.icfg_spills)
    world.ram_section = section
    return {'objects': {'memory': {'class': 'Memory', 'fields': {
        'ram': sum(length for _, length, _ in section.segments),
        'unknown_origins': len(section.unknown_origins),
        'icfg_spills': len(section.icfg_spills)}}}, 'scalars': {}, 'static': {}}


def _restore_m2(payload, world):
    if world.mem is None:
        raise SnapshotError('M2 state present but no mem= restore target was provided')
    if world.ram_section is None:
        raise SnapshotError('M2 state present but the snapshot has no RAM section')
    extra = sorted(set(vars(world.mem)) - {'ram', 'unknown_origins', 'icfg_spills'})
    if extra:
        raise SnapshotError(f'M2: the restore-target memory carries unexpected attributes {extra}')
    decoded = apply_ram_section(world.mem, world.ram_section)
    fields = payload['objects']['memory']['fields']
    if (fields.get('ram') != len(decoded.ram)
            or fields.get('unknown_origins') != len(decoded.unknown_origins)
            or fields.get('icfg_spills') != len(decoded.icfg_spills)):
        raise SnapshotError('M2: the restored RAM section does not match the manifest counts')


# --- M3: peripheral timers/IRQ/scalar bookkeeping on BR28Model ---------------

M3_TIMER_FIELDS = ('con', 'cnt', 'prd', 'pending', 'remainder', 'source_remainder',
                   'expirations', 'delivery_in_flight')
M3_CORE_TICK_FIELDS = ('con', 'cnt', 'prd', 'pending', 'writes', 'expirations',
                       'unresolved_overflow', 'acknowledgements', 'delivery_in_flight')
M3_IRQ_FIELDS = ('config', 'ipmask')
M3_SOFTWARE_IRQ_FIELDS = ('pending', 'source_enable', 'commands', 'acknowledgements',
                          'source_commands')
M3_STATE = ('lock_owner', 'current_core', 'core_icfg', 'multicore_enabled',
            'secondary_cpu_executed', 'cpu1_control', 'cpu1_control_kick_writes',
            'usb_disable_writes', 'p3_ana_read_served', 'counts', 'writes', 'registers',
            'gpio', 'timer0_irq4_probe_armed', 'core_tick_irq3_probe_armed')
M3_OBJECT_SLOTS = ('timer', 'core_ticks', 'irq', 'irq1', 'software_irq', 'mmu')
M3_SKIP = M3_OBJECT_SLOTS + M3_STATE


def _capture_m3(world):
    if world.hardware is None:
        return None
    hardware = world.hardware
    for name in M3_STATE:
        if not hasattr(hardware, name):
            raise SnapshotError(f'M3: hardware attribute {name!r} missing — the peripheral '
                                'state list no longer matches the model shape')
    objects = {
        'timer': capture_object(hardware.timer, M3_TIMER_FIELDS, 'M3', 'timer'),
        'core_ticks': None if hardware.core_ticks is None else [
            capture_object(tick, M3_CORE_TICK_FIELDS, 'M3', f'core_ticks[{index}]')
            for index, tick in enumerate(hardware.core_ticks)],
        'irq': capture_object(hardware.irq, M3_IRQ_FIELDS, 'M3', 'irq'),
        'irq1': None if hardware.irq1 is None else
        capture_object(hardware.irq1, M3_IRQ_FIELDS, 'M3', 'irq1'),
        'software_irq': None if hardware.software_irq is None else
        capture_object(hardware.software_irq, M3_SOFTWARE_IRQ_FIELDS, 'M3', 'software_irq'),
    }
    scalars = {name: _capture_scalar(getattr(hardware, name)) for name in M3_STATE}
    return {'objects': objects, 'scalars': scalars,
            'static': capture_static(hardware, 'M3', skip=M3_SKIP)}


def _restore_m3(payload, world):
    if world.hardware is None:
        raise SnapshotError('M3 state present but no hardware= restore target was provided')
    hardware = world.hardware
    check_static(payload['static'], hardware, 'M3', skip=M3_SKIP)  # switches first...
    objects = payload['objects']
    restore_object(objects['timer'], hardware.timer, 'M3', 'timer')
    restore_object(objects['irq'], hardware.irq, 'M3', 'irq')
    if objects['core_ticks'] is not None:
        if hardware.core_ticks is None or len(hardware.core_ticks) != len(objects['core_ticks']):
            raise SnapshotError('M3.core_ticks: the restore target was not built with the same '
                                'core_tick_configuration switches')
        for index, record in enumerate(objects['core_ticks']):
            restore_object(record, hardware.core_ticks[index], 'M3', f'core_ticks[{index}]')
    if objects['irq1'] is not None:
        if hardware.irq1 is None:
            raise SnapshotError('M3.irq1: the snapshot holds CPU1 IRQ controller state but the '
                                'restore target has none (multicore switches differ)')
        restore_object(objects['irq1'], hardware.irq1, 'M3', 'irq1')
    if objects['software_irq'] is not None:
        if hardware.software_irq is None:
            raise SnapshotError('M3.software_irq: the restore target lacks the software IRQ '
                                'command projection')
        restore_object(objects['software_irq'], hardware.software_irq, 'M3', 'software_irq')
    for name, encoded in payload['scalars'].items():
        setattr(hardware, name, decode_value(encoded))


# --- M6: MMU ------------------------------------------------------------------

M6_MMU_FIELDS = ('con', 'begin', 'end', 'walks', 'last_walk')


def _capture_m6(world):
    if world.hardware is None or world.hardware.mmu is None:
        return None
    return {'objects': {'mmu': capture_object(world.hardware.mmu, M6_MMU_FIELDS, 'M6', 'mmu')},
            'scalars': {}, 'static': {}}


def _restore_m6(payload, world):
    if world.hardware is None or world.hardware.mmu is None:
        raise SnapshotError('M6 state present but the restore target has no MMU '
                            '(mmu_model switch differs)')
    restore_object(payload['objects']['mmu'], world.hardware.mmu, 'M6', 'mmu')


# --- M5: DualCoreMachine delivery bookkeeping ---------------------------------

M5_STATE = ('last_core', 'idle_wait_cores', 'stalls', 'launches', 'deferred_core1_turns',
            'seen_cpu1_control_kicks', 'software_irq_deliveries', 'core_tick_deliveries',
            'timer0_irq_deliveries', 'irq_delivery_deferrals', 'irq_delivery_deferral_events',
            'events', 'event_count', 'boot_anchors', 'boot_anchor_count', 'first_task_calls',
            'first_task_returns', 'first_task_setup', 'event_clock_advances', 'core_ids')
M5_SKIP = M5_STATE + ('cores', 'factory', 'hardware')


def _capture_m5(world):
    if world.machine is None:
        return None
    machine = world.machine
    scalars = {}
    for name in M5_STATE:
        value = sorted(machine.cores) if name == 'core_ids' else getattr(machine, name)
        scalars[name] = _capture_scalar(value)
    return {'objects': {}, 'scalars': scalars,
            'static': capture_static(machine, 'M5', skip=M5_SKIP)}


def _restore_m5(payload, world):
    if world.machine is None:
        raise SnapshotError('M5 state present but no machine= restore target was provided')
    machine = world.machine
    check_static(payload['static'], machine, 'M5', skip=M5_SKIP)  # switches first...
    for name, encoded in payload['scalars'].items():
        if name == 'core_ids':
            if set(machine.cores) != set(decode_value(encoded)):
                raise SnapshotError('M5.core_ids: the launched-core set differs from the '
                                    'snapshot — the restore world must run the same '
                                    'construction chain')
            continue
        setattr(machine, name, decode_value(encoded))


# --- M7: loop ledger -----------------------------------------------------------

M7_STATE = ('adapters', 'rom_steps', 'event_counts', 'milestones', 'trace', 'completed', 'stop')


def _capture_m7(world):
    if world.ledger is None:
        return None
    scalars = {name: _capture_scalar(getattr(world.ledger, name)) for name in M7_STATE}
    return {'objects': {}, 'scalars': scalars,
            'static': capture_static(world.ledger, 'M7', skip=M7_STATE)}


def _restore_m7(payload, world):
    if world.ledger is None:
        raise SnapshotError('M7 state present but no ledger= restore target was provided')
    check_static(payload['static'], world.ledger, 'M7', skip=M7_STATE)  # extras first...
    for name, encoded in payload['scalars'].items():
        setattr(world.ledger, name, decode_value(encoded))


# --- registry setup ------------------------------------------------------------

register_module('M1', 'cpus', capture=_capture_m1, restore=_restore_m1,
                fields={'CPU': M1_CPU_FIELDS},
                description='CPU objects; minimal flat field list. The full M1 inventory '
                            '(execution context, repeat/after_then, bank bookkeeping, task '
                            'frames, IcfgState) is the capture-wiring route.')
register_module('M2', 'mem', capture=_capture_m2, restore=_restore_m2,
                fields={'Memory': ('ram', 'unknown_origins', 'icfg_spills')},
                description='Shared memory as three dicts; byte content lives in the RAM '
                            'run-length section, the body record carries cross-checked counts.')
register_module('M3', 'hardware', capture=_capture_m3, restore=_restore_m3,
                fields={'Timer0': M3_TIMER_FIELDS, 'CoreTickStopped': M3_CORE_TICK_FIELDS,
                        'IRQController': M3_IRQ_FIELDS,
                        'SoftwareIrqCommands': M3_SOFTWARE_IRQ_FIELDS},
                scalars=M3_STATE,
                description='Peripheral model top level (timers, IRQ controllers, software '
                            'IRQ, register/GPIO projections, counters). M4 projection '
                            'families are NOT captured: a world with any of them enabled is '
                            'refused by the static-kind guard until their adapters land.')
register_module('M4', None, fields={},
                description='Peripheral projection object families (~25 classes). Refused '
                            'implicitly by the M3 state-kind guard; adapters are the wiring '
                            "route's work.")
register_module('M5', 'machine', capture=_capture_m5, restore=_restore_m5,
                scalars=M5_STATE,
                description='DualCoreMachine scheduling/delivery bookkeeping; core presence '
                            'via core_ids, core content via M1.')
register_module('M6', 'hardware', capture=_capture_m6, restore=_restore_m6,
                fields={'MMU': M6_MMU_FIELDS}, description='MMU functional projection.')
register_module('M7', 'ledger', capture=_capture_m7, restore=_restore_m7,
                scalars=M7_STATE,
                description='explore loop ledger (adapter counters, milestones, bounded '
                            'trace, stop record); the capture point that fills it is the '
                            'wiring route.')
register_module('M8', None, fields={},
                description='Static scenario configuration: rebuilt by re-running the '
                            'construction chain, guarded by the code fingerprint + capture '
                            'context + restore-time static checks; never stored as state.')
register_module('M9', None, fields={},
                description='Explicit exclusions (decode cache, models/tab, '
                            'dispatch_delivery, L1P/IcuIdle...) per design §1 M9; nothing to '
                            'capture by design.')


# ---------------------------------------------------------------------------
# capture_world / restore_world
# ---------------------------------------------------------------------------

def _validate_encoded(value):
    """Whitelist-walk an assembled adapter payload (adapters pre-encode their
    values; this catches anything that slipped through raw)."""
    if value is None or isinstance(value, (bool, int, str)):
        return
    if isinstance(value, list):
        for item in value:
            _validate_encoded(item)
        return
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise SnapshotError('adapter payload contains a non-str dict key')
        for item in value.values():
            _validate_encoded(item)
        return
    raise SnapshotError('adapter payload contained an unserializable value of type '
                        + type(value).__name__)


def capture_world(*, cpus=None, mem=None, hardware=None, machine=None, ledger=None,
                  context=None, extra_fingerprint=()):
    """Capture a world into a WorldSnapshot (module-half adapters only).

    cpus: {'cpu0': cpu, ...} — M1 (minimal flat CPU objects).
    mem: memory object with ram/unknown_origins/icfg_spills dicts — M2.
    hardware: BR28Model — M3 (+M6 when its mmu is present).
    machine: DualCoreMachine — M5.
    ledger: namespace carrying the M7 loop-ledger attributes.
    context: whitelisted dict recorded in the header (scenario name, switch
        list, completed_steps, stop record...); consumers compare it at
        restore time via restore_world(expected_context=...).
    Any state kind outside the wired adapters raises SnapshotError (an honest
    refusal instead of a silently incomplete snapshot).
    """
    world = SimpleNamespace(cpus=cpus, mem=mem, hardware=hardware, machine=machine,
                            ledger=ledger, ram_section=None)
    body = {}
    for key in sorted(MODULES):
        adapter = MODULES[key]
        if adapter.capture is None:
            continue  # by-design absence (M4/M8/M9) — guarded elsewhere
        payload = adapter.capture(world)
        if payload is not None:
            _validate_encoded(payload)
            body[key] = payload
    header = {'code_fingerprint': code_fingerprint(extra_fingerprint),
              'captured_modules': sorted(body),
              'capture_context': encode_value(dict(context or {}))}
    return WorldSnapshot(header, None, body,
                         world.ram_section or encode_ram_section({}, {}, {}))


def restore_world(snapshot, *, cpus=None, mem=None, hardware=None, machine=None,
                  ledger=None, expected_context=None):
    """Apply a snapshot onto freshly constructed world objects (overlay model).

    Targets are constructed with the SAME static switches as the captured
    world (M8 rebuild); each module adapter first asserts its static view
    matches, then overwrites its state fields. Returns a summary namespace.
    Proving the restored world equivalent to a full replay is the wiring
    route's tier, not this function.
    """
    if expected_context is not None:
        actual = snapshot.header.get('capture_context') or {}
        for key, value in expected_context.items():
            if actual.get(key) != value:
                raise SnapshotError(f'capture context mismatch on {key!r}: snapshot has '
                                    f'{actual.get(key)!r}, expected {value!r}')
    world = SimpleNamespace(cpus=cpus, mem=mem, hardware=hardware, machine=machine,
                            ledger=ledger, ram_section=snapshot.ram)
    applied = {}
    for module in snapshot.captured_modules:
        adapter = MODULES.get(module)
        if adapter is None or adapter.restore is None:
            raise SnapshotError(f'snapshot module {module} has no restore adapter registered '
                                'in this build — the snapshot needs the capture-wiring route')
        # Restore-side manifest gate (defense in depth behind the fingerprint):
        # every stored object shape must still be a shape the current adapter
        # captures (fields equal a declared field list; exact class name when
        # declared — stub carriers with identical fields also pass).
        stored = snapshot.manifest[module]['objects']
        declared = {tuple(sorted(fields)) for fields in adapter.fields.values()}
        for key, shape in stored.items():
            name, fields = shape[0], tuple(shape[1])  # manifest fields are sorted
            if fields not in declared or (name in adapter.fields
                                          and tuple(sorted(adapter.fields[name])) != fields):
                raise SnapshotError(f'{module}.{key}: manifest drift on class {name} — snapshot '
                                    f'fields {list(fields)} are not captured by the current '
                                    f'adapter (declared shapes: '
                                    f'{sorted(sorted(v) for v in adapter.fields.values())}); '
                                    'regenerate the snapshot')
        adapter.restore(snapshot.body[module], world)
        payload = snapshot.body[module]
        applied[module] = sorted(payload.get('objects', {}) or payload.get('scalars', {}))
    return SimpleNamespace(applied=applied, digest=snapshot.digest(),
                           modules=snapshot.captured_modules)
