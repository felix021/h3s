#!/usr/bin/env python3
"""Fresh-extraction, exact-image and negative-CRC checks; never flashes."""
import argparse
import binascii
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
from verify_ufw_integrity import verify, decode, sfc

ROOT = Path(__file__).resolve().parents[1]
p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--source', type=Path, default=ROOT / 'firmware/QCY_H3S_7018F_1T2_release_note_V1.1.8_011.ufw')
p.add_argument('--output', type=Path)
a = p.parse_args()
expected = {'noop': '65cafc7f9a7545c5c30701d141c77db334e37065589e42c51306132c94c879af',
            'tones': 'd1f7a067b169285d23457f8c182a8626bc75c01d679f2846a1f7c5e84b9b752d',
            'anc': 'b79cb3a6f8702eb9e1d12eaf181a968f6b5848a66c051b2c792c39aa8b42fa1b'}
source = a.source.resolve()
assert hashlib.sha256(source.read_bytes()).hexdigest() == expected['noop']
report = {'checks': {}, 'hardware_accessed': False}
with tempfile.TemporaryDirectory(prefix='h3s-reproduction-') as temp:
    temp = Path(temp)
    extracted = temp / 'extracted'
    with (temp / 'unpack.log').open('w') as out:
        subprocess.run([sys.executable, str(ROOT / 'tools/unpack.py'), '--source', str(source), '--output-dir', str(extracted)], stdout=out, check=True)
    images = {}
    for variant, attenuation, anc in [('noop', 0, False), ('tones', 10, False), ('anc', 10, True)]:
        out = temp / variant
        cmd = [sys.executable, str(ROOT / 'tools/build_quiet_candidate.py'), '--source', str(source), '--analysis-dir', str(extracted), '--attenuation-db', str(attenuation), '--output-dir', str(out)]
        if anc:
            cmd.append('--anc-cycle-two-state')
        subprocess.run(cmd, stdout=subprocess.PIPE, text=True, check=True)
        path = next(out.glob('*.ufw'))
        data = path.read_bytes()
        sha = hashlib.sha256(data).hexdigest()
        assert sha == expected[variant], (variant, sha)
        integrity = verify(path)
        assert integrity['passed'], integrity['errors']
        images[variant] = data
        report['checks'][variant] = {'sha256': sha, 'bytes': len(data), 'integrity_passed': True}
    assert images['noop'] == source.read_bytes()
    flash_offset, flash_size = struct.unpack_from('<II', decode(images['anc'][64:144]), 8)
    plain = sfc(images['anc'][flash_offset + 0x2020:flash_offset + 0xe5073])
    app = plain[0x100:0x100 + 842654]
    original_app = (extracted / 'unpacked/files/app.bin').read_bytes()
    differences = [[i, x, y] for i, (x, y) in enumerate(zip(original_app, app)) if x != y]
    assert differences == [[0x9e410, 1, 2]], differences
    assert images['anc'][flash_offset + flash_size:] == images['tones'][flash_offset + flash_size:]
    assert sum(x != y for x, y in zip(images['anc'], images['tones'])) == 20
    report['checks']['exact_app_delta'] = differences
    # Restore stale boot-info but repair the outer checks: an outer-only checker
    # would accept this historically rejected image.
    broken = bytearray(images['tones'])
    start = flash_offset + 0x2000
    broken[start:start + 32] = images['noop'][start:start + 32]
    entry = bytearray(decode(broken[64:144]))
    struct.pack_into('<H', entry, 4, binascii.crc_hqx(broken[flash_offset:flash_offset + flash_size], 0))
    broken[64:144] = decode(entry)
    head = bytearray(decode(broken[:64]))
    count = struct.unpack_from('<H', head, 8)[0]
    struct.pack_into('<H', head, 2, binascii.crc_hqx(broken[64:64 + count * 80], 0))
    struct.pack_into('<H', head, 0, binascii.crc_hqx(head[2:], 0))
    broken[:64] = decode(head)
    bad_path = temp / 'historical-stale-crc.ufw'
    bad_path.write_bytes(broken)
    sha = hashlib.sha256(broken).hexdigest()
    assert sha == '5651bff3f91c3bd37262014d267c5a6c85fc4e81703a04bc2ce21d4b6fd5c906', sha
    result = verify(bad_path)
    assert set(result['errors']) == {'dual-bank plaintext CRC invalid', 'dual-bank ciphertext CRC invalid'}
    report['checks']['stale_boot_crc_rejected'] = {'sha256': sha, 'errors': result['errors']}
    wrong_source = temp / 'wrong-source.ufw'
    wrong_source.write_bytes(b'wrong image')
    rejected = subprocess.run([sys.executable, str(ROOT / 'tools/build_quiet_candidate.py'), '--source', str(wrong_source), '--output-dir', str(temp / 'must-not-exist')], capture_output=True)
    assert rejected.returncode != 0 and not (temp / 'must-not-exist').exists()
    report['checks']['wrong_source_rejected'] = True
report['passed'] = True
if a.output:
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps(report, indent=2))
