"""Heartbeat state machine. The caller schedules tick() with a monotonic clock.

All required task progress timestamps must be refreshed independently by the
tasks they represent. A stalled caller emits no further edges; hardware must
also handle a stuck active level and power loss.
"""
from time import monotonic
from threading import RLock

from .gp8600 import number


class SimulatedGPIO:
    def __init__(self):
        self.level = False
        self.opened = False

    def open(self):
        self.opened = True
        self.level = False

    def set_active(self, active):
        if not self.opened:
            raise OSError('GPIO not open')
        self.level = active

    def close(self):
        self.level = False
        self.opened = False


class Heartbeat:
    def __init__(self, output, required, period=10, width=.2, timeout=30, clock=monotonic):
        self.period = number(period, .1, 3600)
        self.width = number(width, .001, self.period)
        if self.width >= self.period:
            raise ValueError('Pulse must be shorter than period')
        self.timeout = number(timeout, self.width, 3600)
        if not required or len(set(required)) != len(required):
            raise ValueError('Critical task names required')
        self.output, self.clock = output, clock
        self.tasks = {name: None for name in required}
        self.state = 'ARRET'
        self.reason = None
        self.last_pulse = None
        self.next_pulse = self.pulse_end = None
        self.active = False

    def progress(self, task):
        if task not in self.tasks:
            raise ValueError('Unknown critical task')
        self.tasks[task] = self.clock()

    def arm(self):
        if self.state != 'ARRET':
            raise ValueError('Stop before rearming')
        self.output.open()
        self.tasks = dict.fromkeys(self.tasks)
        self.state = 'INITIALISATION'
        self.reason = None
        self.next_pulse = self.clock()
        self.pulse_end = None
        self.active = False

    def tick(self):
        if self.state in ('ARRET', 'DEFAUT'):
            return
        now = self.clock()
        if any(stamp is None for stamp in self.tasks.values()):
            return
        if any(not 0 <= now-stamp <= self.timeout for stamp in self.tasks.values()):
            self.fault('Tâche critique périmée ou horloge invalide')
            return
        # A stalled scheduler may leave the output active. Never catch up with
        # back-to-back pulses after a long scheduling pause; latch the fault.
        if self.active and now > self.pulse_end + self.width:
            self.fault('Impulsion trop longue ; ordonnanceur retardé')
            return
        try:
            if self.active and now >= self.pulse_end:
                self.output.set_active(False)
                self.active = False
            elif not self.active and now >= self.next_pulse:
                self.output.set_active(True)
                self.active = True
                self.last_pulse = now
                self.pulse_end = now + self.width
                self.next_pulse = now + self.period
            self.state = 'NORMAL'
        except OSError as exc:
            self.fault(str(exc))

    def fault(self, reason):
        self.state, self.reason = 'DEFAUT', str(reason)[:160]
        try:
            self.output.close()
        except OSError:
            pass
        self.active = False

    def stop(self):
        try:
            self.output.close()
        except OSError as exc:
            self.active = False
            self.state, self.reason = 'DEFAUT', 'État GPIO inconnu : ' + str(exc)[:120]
            raise
        else:
            self.active = False
            self.state = 'ARRET'


class HeartbeatService:
    """Serialize health reports, operator commands and scheduler ticks.

    Construction and scheduler startup do not request a GPIO. The caller
    selects and arms an output explicitly; a fault remains latched until stop.
    """
    def __init__(self):
        self.lock = RLock()
        self.heartbeat = None
        self.pending_fault = None

    def start(self, output, required, **timing):
        with self.lock:
            if self.heartbeat is not None and self.heartbeat.state != 'ARRET':
                raise ValueError('Arrêter le heartbeat avant réarmement')
            heartbeat = Heartbeat(output, required, **timing)
            heartbeat.arm()
            self.heartbeat = heartbeat
            self.pending_fault = None

    def progress(self, task, healthy=True):
        # A blocked GPIO ioctl must not stall the temperature or control loop.
        if not self.lock.acquire(blocking=False):
            if not healthy:
                self.pending_fault = 'Défaut tâche critique : ' + task
            return
        try:
            hb = self.heartbeat
            if hb is None or hb.state in ('ARRET', 'DEFAUT'):
                return
            if not healthy:
                hb.fault('Défaut tâche critique : ' + task)
            elif task in hb.tasks:
                hb.progress(task)
        finally:
            self.lock.release()

    def tick(self):
        with self.lock:
            if self.heartbeat is not None:
                if self.pending_fault is not None:
                    reason, self.pending_fault = self.pending_fault, None
                    if self.heartbeat.state not in ('ARRET', 'DEFAUT'):
                        self.heartbeat.fault(reason)
                else:
                    self.heartbeat.tick()

    def stop(self):
        with self.lock:
            if self.heartbeat is not None:
                self.heartbeat.stop()

    def snapshot(self):
        with self.lock:
            hb = self.heartbeat
            if hb is None:
                return {'state': 'ARRET', 'active': False, 'reason': None,
                        'last_pulse_monotonic': None, 'driver': None}
            return {'state': hb.state, 'active': hb.active, 'reason': hb.reason,
                    'last_pulse_monotonic': hb.last_pulse, 'period_s': hb.period,
                    'width_s': hb.width,
                    'driver': 'SIMULATION' if isinstance(hb.output, SimulatedGPIO) else 'GPIO_REEL',
                    'gpio': getattr(hb.output, 'offset', None)}

    def run(self, stop):
        try:
            while not stop.is_set():
                self.tick()
                stop.wait(.02)
        finally:
            self.stop()
