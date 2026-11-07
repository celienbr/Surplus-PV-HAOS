"""Read-only HA 1-Wire telemetry. Never supplies the output controller."""
from datetime import datetime, timezone
import re
import logging
import sqlite3
from threading import Lock

from .measurements import parse_ha_number


class AirProbe:
    def __init__(self, entity, address, client, max_age, store=None):
        self.entity, self.address, self.client, self.max_age = entity, address.upper(), client, max_age
        self.lock = Lock()
        self.last = None
        self.status = 'UNAVAILABLE'
        self.reason = 'Sonde non configurée' if not entity else 'En attente de lecture'
        self.store = store
        if entity and store:
            try:
                with store.lock, store.db:
                    store.db.execute('CREATE TABLE IF NOT EXISTS air_probe_observations_v1 '
                                     '(timestamp TEXT NOT NULL, address TEXT NOT NULL, temperature_c REAL, '
                                     'PRIMARY KEY(timestamp,address))')
            except (OSError, sqlite3.Error):
                logging.getLogger(__name__).exception('Air probe history initialization failed')
                self.store = None

    def poll(self, now=None):
        injected = now is not None
        now = now or datetime.now(timezone.utc)
        try:
            if not self.entity or not self.client:
                raise ValueError('Sonde ou connexion Home Assistant indisponible')
            state = self.client.get_state(self.entity)
            if not injected:
                now = datetime.now(timezone.utc)
            # The official HA 1-Wire integration exposes its ROM in device_file.
            path = state.get('attributes', {}).get('device_file', '')
            match = re.fullmatch(r'/28\.([0-9a-fA-F]{12})/temperature(?:9|10|11|12)?', path)
            if not match or match.group(1).upper() != self.address:
                raise ValueError('Identifiant 1-Wire absent ou différent de la configuration')
            m = parse_ha_number({**state, 'last_updated': state.get('last_reported') or state.get('last_updated')}, expected_unit='°C')
            if not m.valid(now, self.max_age):
                raise ValueError('Rapport Home Assistant périmé ou futur')
            if not -55 <= m.value <= 125 or m.value == 85:
                raise ValueError('Température DS18B20 invalide ou valeur initiale ambiguë')
            with self.lock:
                self.last, self.status, self.reason = m, 'CONNECTED', ''
        except (OSError, ValueError, TypeError, KeyError, RuntimeError, AttributeError) as exc:
            with self.lock:
                self.status, self.reason = 'UNAVAILABLE', str(exc)[:180]
        result = self.snapshot(now)
        if self.entity and self.store:
            # Valid samples retain HA's report time. Faults insert a gap at
            # observation time, never a fabricated zero or repeated value.
            stamp = result['last_valid_at'] if result['status'] == 'CONNECTED' else now.isoformat()
            try:
                with self.store.lock, self.store.db:
                    self.store.db.execute('INSERT OR IGNORE INTO air_probe_observations_v1 VALUES (?, ?, ?)',
                                          (stamp, self.address, result['temperature_c']))
            except (OSError, sqlite3.Error):
                logging.getLogger(__name__).exception('Air probe history unavailable')
        return result

    def history(self, now=None):
        from datetime import timedelta
        if not self.entity:
            return []
        if not self.store:
            raise OSError('Air probe history unavailable')
        now = now or datetime.now(timezone.utc)
        start = (now-timedelta(hours=48)).isoformat()
        with self.store.lock:
            rows = self.store.db.execute('SELECT timestamp, temperature_c FROM air_probe_observations_v1 '
                'WHERE address=? AND timestamp>=? AND timestamp<=? ORDER BY timestamp DESC LIMIT 20000',
                (self.address, start, now.isoformat())).fetchall()
        return [{'at': at, 'temperature_c': value} for at, value in reversed(rows)]

    def snapshot(self, now=None):
        now = now or datetime.now(timezone.utc)
        with self.lock:
            valid = self.status == 'CONNECTED' and self.last is not None and self.last.valid(now, self.max_age)
            return {'status': 'CONNECTED' if valid else 'UNAVAILABLE',
                    'temperature_c': self.last.value if valid else None,
                    'last_valid_at': self.last.measured_at.isoformat() if self.last else None,
                    'address': '28.' + self.address if self.address else None,
                    'entity': self.entity, 'location': 'OUTSIDE_BOILER',
                    'reason': self.reason if self.status != 'CONNECTED' else ('' if valid else 'Rapport périmé'),
                    'timestamp_source': 'HOME_ASSISTANT_REPORT'}
