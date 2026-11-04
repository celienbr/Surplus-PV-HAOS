"""GP8600/DFR1229 16-bit current output, Linux i2c-dev transport.

Protocol: DFRobot_GP8XXX at 0fcd909888f48431420fa23ec56926ca389c9258,
GP8600_I2C::setDACOutRange and GP8XXX_IIC::sendData. No EEPROM store,
GPIO bit banging, bus scanning, or hardware access on import/construction.
An ACK confirms communication, not device identity or physical current.
"""
from __future__ import annotations

import math
import os


def number(value, low, high):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("Numeric value required")
    if not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"Value outside {low}..{high}")
    return float(value)


def current_code(current_ma):
    return int(number(current_ma, 0, 20) * 65535 / 20 + 0.5)


class LinuxI2C:
    def __init__(self, bus, address):
        import fcntl
        self.fd = None
        try:
            self.fd = os.open(f"/dev/i2c-{bus}", os.O_RDWR | os.O_CLOEXEC)
            fcntl.ioctl(self.fd, 0x0703, address)  # I2C_SLAVE, never FORCE
        except OSError:
            self.close()
            raise

    def write(self, payload):
        if os.write(self.fd, bytes(payload)) != len(payload):
            raise OSError("Short I2C write")

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None


class SimulatedDAC:
    kind = "simulated"

    def __init__(self):
        self.ready = False
        self.last_ma = None
        self.last_code = None
        self.error = None

    def initialize(self):
        self.ready = True
        self.error = None
        self.set_current(0)

    def set_current(self, current_ma):
        code = current_code(current_ma)
        if not self.ready:
            raise OSError("DAC requires initialization/recovery at zero")
        self.last_ma, self.last_code = float(current_ma), code

    def close(self):
        self.ready = False

    def snapshot(self):
        return {"driver": self.kind, "communication": "SIMULATED" if self.ready else "FAULT",
                "ready": self.ready, "last_acknowledged_ma": self.last_ma,
                "code": self.last_code, "error": self.error,
                "physical_current_ma": None, "physical_current_measured": False}


class GP8600(SimulatedDAC):
    kind = "gp8600"

    def __init__(self, bus=1, address=0x58, transport_factory=LinuxI2C):
        super().__init__()
        # HAOS grants only i2c-1; do not accept arbitrary paths or buses.
        if type(bus) is not int or bus != 1:
            raise ValueError("Only Raspberry Pi bus i2c-1 is supported")
        if type(address) is not int or not 0x58 <= address <= 0x5f:
            raise ValueError("GP8600 address must be 0x58..0x5f (88..95)")
        self.bus, self.address = bus, address
        self.transport_factory = transport_factory
        self.transport = None

    def _fault(self, exc):
        self.ready = False
        self.last_ma = self.last_code = None  # device output is now UNKNOWN
        self.error = str(exc)[:160]
        if self.transport is not None:
            try:
                self.transport.write([0x02, 0, 0])  # best effort, never claim success
            except OSError:
                pass
            self.transport.close()
            self.transport = None

    def initialize(self):
        self.close()
        try:
            self.transport = self.transport_factory(self.bus, self.address)
            self.transport.write([0x02, 0, 0])  # clear any old data before range change
            self.transport.write([0x01, 0x04])  # GP8600 0–20 mA current range
            self.transport.write([0x02, 0, 0])
            self.ready, self.error = True, None
            self.last_ma, self.last_code = 0., 0
        except OSError as exc:
            self._fault(exc)
            raise

    def set_current(self, current_ma):
        code = current_code(current_ma)
        if not self.ready:
            raise OSError("DAC requires explicit recovery at zero")
        try:
            self.transport.write([0x02, code & 0xff, code >> 8])
            self.last_ma, self.last_code = float(current_ma), code
        except OSError as exc:
            self._fault(exc)
            raise

    def close(self):
        if self.transport is not None:
            self.transport.close()
            self.transport = None
        self.ready = False

    def snapshot(self):
        return {**super().snapshot(), "communication": "ACK" if self.ready else "FAULT",
                "bus": self.bus, "address": self.address}


def make_dac(options):
    driver = options.get("dac_driver", "simulated")
    if driver == "simulated":
        return SimulatedDAC()
    if driver == "gp8600":
        return GP8600(1, options.get("dac_i2c_address", 0x58))
    raise ValueError("Unknown DAC driver")
