"""Heartbeat state machine. The caller schedules tick() with a monotonic clock.

All required task progress timestamps must be refreshed independently by the
tasks they represent. A stalled caller emits no further edges; hardware must
also handle a stuck active level and power loss.
"""
from time import monotonic
from threading import Condition, RLock
from datetime import datetime, timezone
from collections import deque

from .gp8600 import number


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
        self.last_pulse_at = None
        self.pulse_count = 0
        self.completed_pulse_count = 0
        self.armed_at = None
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
        self.armed_at = self.next_pulse
        self.pulse_end = None
        self.active = False

    def tick(self):
        if self.state in ('ARRET', 'DEFAUT'):
            return
        now = self.clock()
        if any(stamp is None for stamp in self.tasks.values()):
            if not 0 <= now - self.armed_at <= self.timeout:
                self.fault('Initialisation des tâches critiques périmée')
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
                self.completed_pulse_count += 1
            elif not self.active and now >= self.next_pulse:
                self.output.set_active(True)
                self.active = True
                self.last_pulse = now
                self.last_pulse_at = datetime.now(timezone.utc).isoformat()
                self.pulse_count += 1
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
        self.pulse_changed = Condition(self.lock)
        self.pulse_sequence = 0
        self.state_sequence = 0
        self.pulse_events = deque(maxlen=256)
        self.heartbeat = None
        self.pending_fault = None
        self.pending_stop = False

    def start(self, output, required, **timing):
        with self.lock:
            if self.heartbeat is not None and self.heartbeat.state != 'ARRET':
                raise ValueError('Arrêter le heartbeat avant réarmement')
            heartbeat = Heartbeat(output, required, **timing)
            heartbeat.arm()
            self.heartbeat = heartbeat
            self.state_sequence += 1
            self.pulse_changed.notify_all()
            self.pending_fault = None
            self.pending_stop = False

    def progress(self, task, healthy=True):
        # A blocked GPIO ioctl must not stall the temperature or control loop.
        if not healthy:
            # Latch first; the scheduler owns GPIO close, which may block.
            # Readers see pending_fault as DEFAUT before that close completes.
            self.pending_fault = 'Défaut tâche critique : ' + task
            if self.lock.acquire(blocking=False):
                try:
                    self.state_sequence += 1
                    self.pulse_changed.notify_all()
                finally:
                    self.lock.release()
            return
        if not self.lock.acquire(blocking=False):
            return
        try:
            hb = self.heartbeat
            if hb is None or hb.state in ('ARRET', 'DEFAUT'):
                return
            if task in hb.tasks:
                hb.progress(task)
        finally:
            self.lock.release()

    def tick(self):
        with self.lock:
            if self.heartbeat is not None:
                previous_state = self.heartbeat.state
                if self.pending_fault is not None:
                    reason, self.pending_fault = self.pending_fault, None
                    if self.heartbeat.state not in ('ARRET', 'DEFAUT'):
                        self.heartbeat.fault(reason)
                elif self.pending_stop:
                    self.pending_stop = False
                    if self.heartbeat.state != 'DEFAUT':
                        try:
                            self.heartbeat.stop()
                        except OSError:
                            pass  # stop() recorded the uncertain GPIO state
                else:
                    previous_count = self.heartbeat.pulse_count
                    self.heartbeat.tick()
                    if self.heartbeat.pulse_count != previous_count:
                        self.pulse_sequence += 1
                        self.pulse_events.append({'sequence': self.pulse_sequence,
                                                  'at': self.heartbeat.last_pulse_at})
                        self.pulse_changed.notify_all()
                if self.heartbeat.state != previous_state:
                    self.state_sequence += 1
                    self.pulse_changed.notify_all()

    def wait_for_pulses(self, after, timeout=1):
        """Return generated pulses after a cursor; never fabricate old events."""
        with self.pulse_changed:
            state_sequence = self.state_sequence
            self.pulse_changed.wait_for(lambda: self.pulse_sequence > after or self.state_sequence != state_sequence, timeout=timeout)
            events = [event.copy() for event in self.pulse_events if event['sequence'] > after]
            missed = bool(events and events[0]['sequence'] > after + 1)
            return events, missed

    def pulse_cursor(self):
        with self.lock:
            return self.pulse_sequence

    def stop(self):
        with self.lock:
            if self.heartbeat is not None:
                self.heartbeat.stop()
                self.pending_fault = None
                self.pending_stop = False
                self.state_sequence += 1
                self.pulse_changed.notify_all()

    def stop_for_mode_change(self):
        # Neither a held lock nor a blocking GPIO close may delay MANUEL.
        # The heartbeat worker performs the stop on its next tick.
        self.pending_stop = True

    def snapshot(self):
        with self.lock:
            hb = self.heartbeat
            if hb is None:
                return {'state': 'ARRET', 'active': False, 'reason': None,
                        'last_pulse_monotonic': None, 'last_pulse_at': None,
                        'last_pulse_age_s': None, 'pulse_count': 0, 'driver': None}
            age = None if hb.last_pulse is None else hb.clock() - hb.last_pulse
            overdue = (hb.state == 'NORMAL' and
                       (age is None or age < 0 or age > hb.period + max(1, hb.width * 2)))
            # Report a missed pulse immediately; the scheduler performs the
            # potentially blocking GPIO close on its next tick.
            if overdue and self.pending_fault is None:
                self.pending_fault = 'Impulsion logicielle périmée'
            state = 'DEFAUT' if overdue or self.pending_fault is not None else hb.state
            if self.pending_stop and state != 'DEFAUT':
                state = 'ARRET'
            reason = self.pending_fault if state == 'DEFAUT' and self.pending_fault else hb.reason
            return {'state': state, 'active': hb.active and state == 'NORMAL', 'reason': reason,
                    'last_pulse_monotonic': hb.last_pulse, 'period_s': hb.period,
                    'last_pulse_at': hb.last_pulse_at, 'last_pulse_age_s': age,
                    'pulse_count': hb.pulse_count,
                    'completed_pulse_count': hb.completed_pulse_count,
                    'task_timeout_s': hb.timeout,
                    'task_progress_monotonic': dict(hb.tasks),
                    'task_age_s': {name: (None if stamp is None else max(0, hb.clock() - stamp))
                                   for name, stamp in hb.tasks.items()},
                    'width_s': hb.width,
                    'driver': getattr(hb.output, 'driver_kind', 'GPIO_REEL'),
                    'gpio': getattr(hb.output, 'offset', None)}

    def run(self, stop):
        try:
            while not stop.is_set():
                self.tick()
                stop.wait(.02)
        finally:
            self.stop()
