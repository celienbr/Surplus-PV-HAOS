"""Minimal Linux GPIO character-device v2 output, opened only on request.

The caller must qualify chip/offset, wiring and external inactive pull. Releasing
the line does not guarantee its electrical level. There is no hardware access
on import or construction.
"""
import ctypes
import fcntl
import os


class Attribute(ctypes.Structure):
    _fields_ = [('id', ctypes.c_uint32), ('padding', ctypes.c_uint32),
                ('value', ctypes.c_uint64)]


class ConfigAttribute(ctypes.Structure):
    _fields_ = [('attr', Attribute), ('mask', ctypes.c_uint64)]


class Config(ctypes.Structure):
    _fields_ = [('flags', ctypes.c_uint64), ('num_attrs', ctypes.c_uint32),
                ('padding', ctypes.c_uint32 * 5), ('attrs', ConfigAttribute * 10)]


class Request(ctypes.Structure):
    _fields_ = [('offsets', ctypes.c_uint32 * 64), ('consumer', ctypes.c_char * 32),
                ('config', Config), ('num_lines', ctypes.c_uint32),
                ('event_buffer_size', ctypes.c_uint32), ('padding', ctypes.c_uint32 * 5),
                ('fd', ctypes.c_int32)]


class Values(ctypes.Structure):
    _fields_ = [('bits', ctypes.c_uint64), ('mask', ctypes.c_uint64)]


def _iowr(number, structure):
    return (3 << 30) | (ctypes.sizeof(structure) << 16) | (0xB4 << 8) | number


class GPIOOutput:
    def __init__(self, chip, offset, active_high=True):
        if not isinstance(chip, str) or chip not in ('/dev/gpiochip0', '/dev/gpiochip1'):
            raise ValueError('Unrecognised GPIO character device')
        if type(offset) is not int or not 0 <= offset <= 53 or type(active_high) is not bool:
            raise ValueError('Invalid GPIO offset or polarity')
        self.chip, self.offset, self.active_high = chip, offset, active_high
        self.fd = None

    def open(self):
        if self.fd is not None:
            raise RuntimeError('GPIO already requested')
        chip_fd = os.open(self.chip, os.O_RDONLY | os.O_CLOEXEC)
        try:
            req = Request()
            req.offsets[0] = self.offset
            req.consumer = b'surplus-pv-heartbeat'
            req.num_lines = 1
            req.config.flags = 1 << 3  # GPIO_V2_LINE_FLAG_OUTPUT
            req.config.num_attrs = 1
            req.config.attrs[0].attr.id = 2  # OUTPUT_VALUES, applied atomically
            req.config.attrs[0].attr.value = int(not self.active_high)
            req.config.attrs[0].mask = 1
            fcntl.ioctl(chip_fd, _iowr(7, Request), req)
            if req.fd < 0:
                raise OSError('GPIO request returned invalid descriptor')
            self.fd = req.fd
        finally:
            os.close(chip_fd)

    def set_active(self, active):
        if self.fd is None or type(active) is not bool:
            raise ValueError('GPIO not requested or invalid level')
        level = active if self.active_high else not active
        fcntl.ioctl(self.fd, _iowr(15, Values), Values(int(level), 1))

    def close(self):
        if self.fd is not None:
            try:
                self.set_active(False)
            finally:
                fd, self.fd = self.fd, None
                os.close(fd)
