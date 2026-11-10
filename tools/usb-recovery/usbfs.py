"""Fixed-target Linux usbfs transport, tested on a rooted Android phone.

Uses the kernel usbdevice_fs ABI directly; no sysfs mount or Android app grant
is required. Only the verified QCY/BR28 identities are opened.
"""
import ctypes as C
import os
from pathlib import Path
import struct
import sys
import time
from protocol import USBTransport

libc = C.CDLL(None, use_errno=True)
libc.ioctl.argtypes = [C.c_int, C.c_ulong, C.c_void_p]
libc.ioctl.restype = C.c_int


class Control(C.Structure):
    _fields_ = [('kind', C.c_uint8), ('request', C.c_uint8), ('value', C.c_uint16),
        ('index', C.c_uint16), ('length', C.c_uint16), ('timeout', C.c_uint32),
        ('data', C.c_void_p)]


class Bulk(C.Structure):
    _fields_ = [('endpoint', C.c_uint), ('length', C.c_uint), ('timeout', C.c_uint),
                ('data', C.c_void_p)]


def ioctl(fd, request, arg):
    result = libc.ioctl(fd, request, C.byref(arg))
    if result < 0:
        err = C.get_errno()
        raise OSError(err, os.strerror(err))
    return result


def open_target(vid, pid):
    if sys.platform != 'linux' or os.geteuid() != 0 or C.sizeof(C.c_void_p) != 8:
        raise RuntimeError('live USB requires 64-bit Linux/Android with root privileges')
    handles = []
    for path in Path('/dev/bus/usb').glob('*/*'):
        fd = None
        try:
            fd = os.open(path, os.O_RDWR)
            descriptor = os.read(fd, 18)
            if len(descriptor) == 18 and struct.unpack_from('<HH', descriptor, 8) == (vid, pid):
                handles.append((fd, str(path)))
                fd = None
        except OSError:
            pass
        finally:
            if fd is not None:
                os.close(fd)
    if len(handles) != 1:
        for fd, _ in handles:
            os.close(fd)
        return None
    return handles[0]


def control(fd, length):
    data = C.create_string_buffer(length)
    args = Control(0x83, 0, 0xfc, 0xfe, length, 250, C.cast(data, C.c_void_p))
    n = ioctl(fd, 0xc0185500, args)
    return data.raw[:n]


def enter_rom():
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        rom = open_target(0x4c4a, 0x3442)
        if rom:
            os.close(rom[0])
            print('Already in BR28 ROM recovery', rom[1], flush=True)
            return
        normal = open_target(0x3654, 0x4a55)
        if normal:
            fd, path = normal
            try:
                capability = control(fd, 8)
                if capability != b'\xff' * 8:
                    raise RuntimeError('unexpected recovery capability: ' + capability.hex())
                print('QCY USB recovery capability confirmed', path, flush=True)
                try:
                    control(fd, 0)
                    print('ROM entry control completed', flush=True)
                except OSError as e:
                    print('ROM entry transfer result', e, flush=True)
                return  # exactly ONE mode switch; caller confirms new identity
            except OSError:
                pass  # descriptor/control read raced the existing reset loop
            finally:
                os.close(fd)
        time.sleep(.01)
    raise RuntimeError('No stable QCY control endpoint within 30 seconds')


class LinuxTransport(USBTransport):
    def __init__(self):
        found = open_target(0x4c4a, 0x3442)
        if not found:
            raise RuntimeError('exactly one BR28 ROM device required')
        self.fd, self.path = found
        interface = C.c_uint(0)
        try:
            try:
                ioctl(self.fd, 0x8004550f, interface)
            except OSError as e:
                if e.errno != 16:
                    raise
                args = C.create_string_buffer(264)
                ioctl(self.fd, 0x8108551b, args)
        except BaseException:
            os.close(self.fd)
            raise
        self.tag = 0
        print('BR28 Linux usbfs interface opened', self.path, flush=True)

    def close(self):
        try:
            ioctl(self.fd, 0x80045510, C.c_uint(0))
        finally:
            os.close(self.fd)

    def bulk(self, endpoint, payload_or_size):
        size = payload_or_size if isinstance(payload_or_size, int) else len(payload_or_size)
        buffer = C.create_string_buffer(size)
        if not isinstance(payload_or_size, int):
            buffer.raw = payload_or_size
        args = Bulk(endpoint, size, 5000, C.cast(buffer, C.c_void_p))
        n = ioctl(self.fd, 0xc0185502, args)
        return buffer.raw[:n]
