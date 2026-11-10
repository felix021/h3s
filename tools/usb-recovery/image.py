"""Offline image checks for the one device layout actually recovered in 2026-11.

Refuses other damaged images/layouts. Full-chip inputs/outputs are PRIVATE data.
"""
import binascii
import hashlib
import struct

GOOD_UFW_SHA = 'b79cb3a6f8702eb9e1d12eaf181a968f6b5848a66c051b2c792c39aa8b42fa1b'
GOOD_FLASH_SHA = 'fe1a578dba83aae1530ba5a489eeed98f58f3cc3be68f6528909a0894db014ba'
BAD_APP_SHA = '4ba0c74b033a5bf49c86c308ef5e94ad3dd1ca5b82cc969bf7f4a7f7ecb37018'
SIZE = 0x200000
VM_BEGIN, VM_END = 0x1da000, 0x1de000
ORDER = (0xf3000, 0x113000, 0x166000, 0x1d0000, 0x1d1000, 0xee000)
SECTOR = 4096


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def crc(data):
    return binascii.crc_hqx(data, 0)


def encode(data, key=0xffff):
    out = bytearray()
    for b in data:
        out.append(b ^ (key & 255))
        key = ((key << 1) ^ (0x1021 if key & 0x8000 else 0)) & 0xffff
    return bytes(out)


def sfc(data):
    return b''.join(encode(data[p:p+32], 0x3496 ^ (p//4)) for p in range(0,len(data),32))


def good_flash(ufw):
    require(len(ufw) == 1326848 and sha(ufw) == GOOD_UFW_SHA, 'not the tested ANC UFW')
    directory = encode(ufw[64:144])
    kind, _, _, _, offset, size, padded, _, name = struct.unpack('<HHHHIII44s16s', directory)
    require(kind == 0 and name.split(b'\0')[0] == b'flash.bin', 'unexpected UFW entry')
    require(size == padded == 942080 and offset+size <= len(ufw), 'UFW flash bounds')
    flash = ufw[offset:offset+size]
    require(sha(flash) == GOOD_FLASH_SHA, 'UFW flash digest mismatch')
    return flash


def bank(data, base):
    require(len(data) == SIZE and base in (0x2000,0xee000), 'unsupported chip/bank')
    header = encode(data[base:base+32])
    require(crc(header[2:]) == struct.unpack_from('<H',header)[0], 'bank header CRC')
    length = struct.unpack_from('<I',header,8)[0]
    require(0 < length <= 0xec000-32, 'bank application bounds')
    app = data[base+32:base+32+length]
    require(crc(app) == struct.unpack_from('<H',header,4)[0], 'bank cipher CRC')
    require(crc(sfc(app)) == struct.unpack_from('<H',header,2)[0], 'bank plaintext CRC')
    return header, app


def validate_delta(before, after):
    require(len(before) == len(after) == SIZE, 'full 2MiB images required')
    sectors = {i//SECTOR*SECTOR for i,(a,b) in enumerate(zip(before,after)) if a!=b}
    require(sectors == set(ORDER), 'delta is not the fixed six-sector restoration')
    require(before[:0xee000] == after[:0xee000], 'bank A/bootloader would change')
    require(before[0x1d2000:] == after[0x1d2000:], 'VM/calibration/resources would change')


def prepare(before, ufw):
    flash = good_flash(ufw)
    good_header, old_app = bank(before,0x2000)
    bad_header, bad_app = bank(before,0xee000)
    package_header = encode(flash[0x2000:0x2020])
    good_length = struct.unpack_from('<I',package_header,8)[0]
    app = flash[0x2020:0x2020+good_length]
    require(good_length == 929875 and old_app == app, 'bank A is not the tested good image')
    require(len(bad_app) == 930245 and sha(bad_app) == BAD_APP_SHA, 'bank B is not the rejected r2 image')
    require(struct.unpack_from('<H',good_header,6)[0] == 2, 'unexpected bank A sequence')
    require(struct.unpack_from('<H',bad_header,6)[0] == 3, 'unexpected bank B sequence')
    require(good_header[12:] == bad_header[12:], 'bank boot configurations disagree')
    header = bytearray(bad_header)
    header[2:6], header[8:12] = package_header[2:6], package_header[8:12]
    struct.pack_into('<H',header,0,crc(header[2:]))
    after = bytearray(before)
    after[0xee000:0xee020] = encode(header)
    after[0xee020:0xee020+len(bad_app)] = app + b'\xff'*(len(bad_app)-len(app))
    after = bytes(after)
    validate_delta(before, after)
    restored_header, restored_app = bank(after,0xee000)
    require(restored_app == app and restored_header[6:8] == bad_header[6:8], 'restored bank mismatch')
    report = {'profile':'h3s-r2-to-tested-anc-v1','before_sha256':sha(before),
        'after_sha256':sha(after),'bytes':SIZE,'good_ufw_sha256':GOOD_UFW_SHA,
        'changed_bytes':sum(a!=b for a,b in zip(before,after)), 'write_order':list(ORDER),
        'preserved_ranges':[[0,0xee000],[0x1d2000,SIZE]],
        'sectors':[{'offset':p,'before_sha256':sha(before[p:p+SECTOR]),
                    'after_sha256':sha(after[p:p+SECTOR])} for p in ORDER]}
    return after, report


def preserve_live_vm(original, target, fresh, second_read):
    """No code/calibration variation is permitted between planning and writing."""
    validate_delta(original, target)
    require(len(fresh) == SIZE and second_read == fresh, 'fresh independent full reads differ')
    require(fresh[:VM_BEGIN] == original[:VM_BEGIN] and fresh[VM_END:] == original[VM_END:],
            'non-VM bytes changed after planning; do not write')
    live_target = bytearray(fresh)
    for p in ORDER:
        live_target[p:p+SECTOR] = target[p:p+SECTOR]
    live_target = bytes(live_target)
    validate_delta(fresh, live_target)
    return live_target
