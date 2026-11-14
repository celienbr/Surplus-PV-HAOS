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
            if stamp is not None and 0 <= self.clock() - stamp <= self.max_cache_age:
                return value
            raise RuntimeError(error or 'Acquisition Home Assistant en attente')
        return read

    def _fetch(self, key, method, args):
        once = key[0] == 'request_boiler_temperature_refresh'
        while not self.stop.is_set():
            try:
                value = (isolated_call(method, args, self.request_timeout)
                         if self.process_isolation else method(*args))
            except Exception as exc:
                with self.lock:
                    self.entries[key].update(stamp=None, error=str(exc)[:160])
            else:
                with self.lock:
                    self.entries[key].update(value=value, stamp=self.clock(), error=None)
            if once or self.stop.wait(self.refresh_interval):
                break
        with self.lock:
            self.entries[key]['pending'] = False

    def close(self):
        self.stop.set()
