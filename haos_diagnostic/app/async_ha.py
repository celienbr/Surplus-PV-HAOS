"""Nonblocking acquisition of control inputs from Home Assistant.

One daemon worker per input may be outstanding. A blocked request cannot
accumulate workers, and never holds the controller or watchdog lock. Results
retain their original Home Assistant timestamps; cache age is an additional
limit, not a replacement for measurement freshness checks.
"""
from threading import Event, Lock, Thread
from time import monotonic
import multiprocessing


def _invoke(pipe, method, args):
    try:
        pipe.send((True, method(*args)))
    except Exception as exc:
        pipe.send((False, str(exc)[:160]))
    finally:
        pipe.close()


def isolated_call(method, args, timeout):
    """Run a request in a disposable process with a total time limit."""
    context = multiprocessing.get_context('spawn')
    parent, child = context.Pipe(duplex=False)
    process = context.Process(target=_invoke, args=(child, method, args), daemon=True)
    try:
        process.start()
        child.close()
        if not parent.poll(timeout):
            raise TimeoutError('Délai total de requête Home Assistant dépassé')
        ok, result = parent.recv()
        if not ok:
            raise RuntimeError(result)
        return result
    finally:
        parent.close()
        child.close()
        if process.pid is not None:
            if process.is_alive():
                process.terminate()
            process.join(timeout=1)
            if process.is_alive():
                process.kill()
                process.join(timeout=1)


class AsyncControlInputs:
    CONTROL_METHODS = frozenset(('read_gplugk_states', 'read_gplugk_grid_w',
                                 'read_temperature_c', 'request_boiler_temperature_refresh'))

    def __init__(self, client, max_cache_age=15, clock=monotonic,
                 process_isolation=False, request_timeout=12, refresh_interval=5):
        self.client = client
        self.max_cache_age = max_cache_age
        self.clock = clock
        self.process_isolation = process_isolation
        self.request_timeout = request_timeout
        self.refresh_interval = refresh_interval
        self.stop = Event()
        self.lock = Lock()
        self.entries = {}
        self.grid_wake = Event()
        self.last_grid_read_stamp = None
        self.grid_generation = 0
        self.last_grid_read_generation = None

    def __getattr__(self, name):
        method = getattr(self.client, name)
        if name not in self.CONTROL_METHODS:
            return method
        def read(*args):
            key = (name, args)
            with self.lock:
                entry = self.entries.setdefault(key, {'pending': False, 'value': None,
                                                       'stamp': None, 'error': None})
                if not entry['pending']:
                    entry['pending'] = True
                    Thread(target=self._fetch, args=(key, method, args), daemon=True).start()
                stamp, value, error = entry['stamp'], entry['value'], entry['error']
                if name in ('read_gplugk_states', 'read_gplugk_grid_w'):
                    self.last_grid_read_stamp = stamp
                    self.last_grid_read_generation = self.grid_generation
            if stamp is not None and 0 <= self.clock() - stamp <= self.max_cache_age:
                return value
            raise RuntimeError(error or 'Acquisition Home Assistant en attente')
        return read

    def _fetch(self, key, method, args):
        once = key[0] == 'request_boiler_temperature_refresh'
        grid = key[0] in ('read_gplugk_states', 'read_gplugk_grid_w')
        while not self.stop.is_set():
            try:
                value = (isolated_call(method, args, self.request_timeout)
                         if self.process_isolation else method(*args))
            except Exception as exc:
                with self.lock:
                    changed = self.entries[key]['stamp'] is not None or self.entries[key]['error'] is None
                    self.entries[key].update(stamp=None, error=str(exc)[:160])
                    if grid and changed:
                        self.grid_generation += 1
            else:
                with self.lock:
                    changed = self.entries[key]['stamp'] is None or self.entries[key]['value'] != value
                    self.entries[key].update(value=value, stamp=self.clock(), error=None)
                    if grid and changed:
                        self.grid_generation += 1
            # Wake after publishing the cache. Repeated identical HA reports
            # do not cause extra regulation cycles or manufacture samples.
            if grid and changed:
                self.grid_wake.set()
            if once or self.stop.wait(self.refresh_interval):
                break
        with self.lock:
            self.entries[key]['pending'] = False

    def close(self):
        self.stop.set()
        self.grid_wake.set()
