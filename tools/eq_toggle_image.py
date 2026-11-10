"""Insert the bounded, linked EQ experiment into the known 0.1.1.8 layout.

Used only by the builder. The independent verifier must not import this module.
"""
import hashlib
from pathlib import Path
import struct

from jltech.crc import jl_crc16

APP_BASE = 0x06000100
APP_DATA = 0x2120
APP_SIZE = 842654
PAYLOAD_ADDRESS = 0x060E3074
OLD_APP_END = 0xE5073
SITES = {
    '.site.source_read': (0x060059CE, bytes.fromhex('50ee6a124020')),
    '.site.preset': (0x06025030, bytes.fromhex('412252ee0a12')),
    '.site.custom': (0x0602510E, bytes.fromhex('402152eeea02')),
    '.site.key': (0x06025364, bytes.fromhex('0d40bfeaf792')),
    '.site.open': (0x06078846, bytes.fromhex('6061f8436061')),
}


def read_sections(path):
    data = Path(path).read_bytes()
    if data[:7] != b'\x7fELF\x01\x01\x01':
        raise ValueError('Expected little-endian ELF32')
    header = struct.unpack_from('<16sHHIIIIIHHHHHH', data)
    if header[1:4] != (2, 0xF1, 1) or header[11] != 40:
        raise ValueError('Expected linked ELF32-pi32v2')
    start, count, names_index = header[6], header[12], header[13]
    table = [struct.unpack_from('<10I', data, start + i * 40) for i in range(count)]
    names_header = table[names_index]
    names = data[names_header[4]:names_header[4] + names_header[5]]
    result = {}
    for name, kind, flags, address, offset, size, *_ in table:
        label = names[name:].split(b'\0')[0].decode('ascii')
        if flags & 2 and size:
            if kind != 1 or offset + size > len(data) or label in result:
                raise ValueError('Unexpected allocated ELF section')
            result[label] = (address, data[offset:offset + size])
    if set(result) != set(SITES) | {'.payload'}:
        raise ValueError('Unexpected allocated sections or new RAM')
    return data, result


def apply_eq_patch(flash, elf):
    raw_elf, sections = read_sections(elf)
    app = bytes(flash[APP_DATA:APP_DATA + APP_SIZE])
    if hashlib.sha256(app).hexdigest() != '8f594d5908f6d4eac9b63e5e4538d3c0dbf0ed1a1e285547fcf2d9c6b0914517':
        raise ValueError('EQ candidate must build on the exact ANC-patched app')
    sites = []
    for label, (address, before) in SITES.items():
        linked_address, code = sections[label]
        position = APP_DATA + address - APP_BASE
        if linked_address != address or len(code) != 6 or flash[position:position + 6] != before:
            raise ValueError('Hook mismatch: ' + label)
        flash[position:position + 6] = code
        sites.append({'name': label, 'runtime_address': hex(address),
                      'app_offset': hex(address - APP_BASE),
                      'before': before.hex(), 'after': code.hex()})
    address, code = sections['.payload']
    if address != PAYLOAD_ADDRESS or not 1 <= len(code) <= 0xF6C:
        raise ValueError('Payload outside fixed flash padding')
    # Hardware trial exposed a missing runtime invariant. debug_init loads
    # 0x06000100..0x060c3338 into LIM_PC0_L/H and enables the PC guard. File/CRC
    # coverage does NOT make a resource's address executable. Keep the faulty
    # design blocked, even though its historical offline CRC audit passed.
    if not 0x06000100 <= address < address + len(code) <= 0x060C3338:
        raise ValueError('REJECTED: EQ payload exceeds the firmware Flash PC execution range; redesign required')
    # Existing entries already include unaligned headers (config.dat starts
    # flash+0xe4ef3). No old file is moved or extended. Append a normal JLFS file.
    last_header = 0xE4EF3
    if flash[last_header + 14:last_header + 16] != b'\x01\x00':
        raise ValueError('Expected final config.dat entry')
    if flash[last_header + 16:last_header + 32].split(b'\0')[0] != b'config.dat':
        raise ValueError('Unexpected final file')
    if jl_crc16(flash[last_header + 2:last_header + 32]) != struct.unpack_from('<H', flash, last_header)[0]:
        raise ValueError('Invalid config.dat header CRC')
    # Beyond OLD_APP_END, the unpacker's decrypted.bin still contains RAW 0xff
    # padding. The caller must encrypt the whole extended SFC area, not XOR a
    # purported plaintext delta into these raw padding bytes.
    payload_flash = 0x2020 + PAYLOAD_ADDRESS - 0x06000000
    if payload_flash != OLD_APP_END + 33:
        raise ValueError('Payload/file alignment mismatch')
    end = payload_flash + len(code)
    if end > len(flash) or any(value != 255 for value in flash[OLD_APP_END:end]):
        raise ValueError('Non-padding bytes at extension location')
    struct.pack_into('<H', flash, last_header + 14, 0)
    struct.pack_into('<H', flash, last_header, jl_crc16(flash[last_header + 2:last_header + 32]))
    content = b'\xff' + code
    block_size = 32 + len(content)
    header = bytearray(struct.pack('<HHIIBBH16s', 0, jl_crc16(content), 32,
                                   block_size, 0x82, 0, 1, b'eq_toggle.bin'))
    struct.pack_into('<H', header, 0, jl_crc16(header[2:]))
    flash[OLD_APP_END:end] = header + content
    # Existing nested app.bin and parent app-area checksums, after ALL code edits.
    struct.pack_into('<H', flash, 0x2042, jl_crc16(flash[APP_DATA:APP_DATA + APP_SIZE]))
    struct.pack_into('<H', flash, 0x2040, jl_crc16(flash[0x2042:0x2060]))
    struct.pack_into('<H', flash, 0x2022, jl_crc16(flash[0x2040:0xCFCC0]))
    struct.pack_into('<H', flash, 0x2020, jl_crc16(flash[0x2022:0x2040]))
    return {'status': 'EXPERIMENTAL_NOT_FLASHED', 'patch_elf_sha256': hashlib.sha256(raw_elf).hexdigest(),
            'payload_sha256': hashlib.sha256(code).hexdigest(), 'payload_bytes': len(code),
            'payload_runtime': hex(address), 'payload_flash_offset': hex(payload_flash),
            'extension_file': 'eq_toggle.bin', 'old_app_end': OLD_APP_END, 'new_app_end': end,
            'hook_sites': sites, 'state': 'volatile EQ source byte bit 7; enabled after boot',
            'spatial_eq_type_10': 'double click ignored', 'device_verified': False}
