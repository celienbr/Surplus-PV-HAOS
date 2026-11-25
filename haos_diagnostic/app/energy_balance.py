"""Daily Sofar / gPlugK / boiler attribution, independent of regulation."""
from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
from math import isfinite
import sqlite3

from .comfort_energy import ZONE, MAX_GAP
from .energy_dashboard import live_grid
from .history_store import parse_time
from .pv_actual import SYNC_SECONDS

# No additional watt tolerance: preserve the three balance identities exactly.
# Use the existing gPlugK and PV quality gates before comparing powers.
def allocate(pv, grid, boiler):
    if not all(isfinite(value) for value in (pv, grid, boiler)):
        return None
    other = pv + grid - boiler
    injection = max(0, -grid)
    if min(pv, boiler, other) < 0 or injection > pv:
        return None
    other_pv = min(pv, other)
    solar = min(boiler, max(0, pv - other))
    return other_pv, solar, boiler - solar, injection


def synchronized_sample(pv, grid, meter, command, options, now):
    if not options['pv_self_consumption_topology_verified'] or pv['status'] != 'OK':
        return None
    live = live_grid(grid, options['max_age_seconds'], now)
    if live['status'] != 'OK' or live['coverage'] != 'TWO_RECENT':
        return None
    sensors = grid.get('sensors', {})
    try:
        if now > parse_time(pv['valid_until']):
            return None
        reports = [parse_time(pv['at'])]
        pi, po = sensors.get('pi') or {}, sensors.get('po') or {}
        reports.extend(parse_time(channel['reported_at']) for channel in (pi, po))
        grid_w = 1000 * (pi['kw'] - po['kw'])
        if (pi['kw'] > 0 and po['kw'] > 0) or round(grid_w) != live['grid_w']:
            return None
        if not (0 <= (now - min(reports[1:])).total_seconds() <= options['max_age_seconds']
                and max(reports) <= now):
            return None
        if meter is not None:
            if meter['status'] != 'OK' or now > parse_time(meter['valid_until']):
                return None
            reports.append(parse_time(meter['at']))
            watts, source, revision = meter['value'], 'measured', None
        else:
            if command is None:
                return None
            watts, changed_at, source, revision = command
            if changed_at >= min(reports):
                return None
        if (max(reports) - min(reports)).total_seconds() > SYNC_SECONDS or max(reports) > now:
            return None
    except (ValueError, TypeError, KeyError):
        return None
    parts = allocate(pv['value'], grid_w, watts)
    if parts is None:
        return None
    return max(reports), tuple(reports), parts, source, revision, grid['balance_grid_epoch'], grid_w


class DailyEnergyBalance:
    """Store verified intervals only. A new process starts without an anchor."""

    def __init__(self, store):
        self.store = store
        self.previous = None
        with store.lock, store.db:
            store.db.execute('''CREATE TABLE IF NOT EXISTS daily_energy_balance_v1(
                end_at TEXT PRIMARY KEY, start_at TEXT NOT NULL,
                other_pv TEXT NOT NULL, solar_boiler TEXT NOT NULL,
                grid_boiler TEXT NOT NULL, injection TEXT NOT NULL, source TEXT NOT NULL)''')

    def observe(self, sample):
        previous = self.previous
        self.previous = sample
        if sample is None or previous is None:
            return
        end, reports, parts, source, revision, epoch, grid = sample
        start, old_reports, old_parts, old_source, old_revision, old_epoch, old_grid = previous
        if old_reports == reports and (old_source, old_revision, old_epoch) == (source, revision, epoch):
            return
        if (not 0 < (end - start).total_seconds() <= MAX_GAP
                or not all(after > before for after, before in zip(reports, old_reports))
                or (source, revision, epoch) != (old_source, old_revision, old_epoch)
                or grid * old_grid < 0):
            return

        # Linear interpolation also allows exact clipping at Swiss midnight.
        # Store endpoint powers together so daylight-saving days use elapsed UTC seconds.
        with self.store.lock, self.store.db:
            self.store.db.execute('''INSERT OR IGNORE INTO daily_energy_balance_v1
                VALUES(?,?,?,?,?,?,?)''', (end.isoformat(), start.isoformat(),
                    *(json.dumps([a, b]) for a, b in zip(old_parts, parts)), source))

    def daily(self, now):
        start = now.astimezone(ZONE).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
        result = {'balance_status': 'Couverture incomplète', 'balance_through': None,
                  'balance_covered_seconds': 0}
        latest = self.previous
        with closing(sqlite3.connect(self.store.path.absolute().as_uri() + '?mode=ro',
                                     uri=True, timeout=5)) as db:
            rows = db.execute('''SELECT * FROM daily_energy_balance_v1
                WHERE end_at > ? AND end_at <= ? ORDER BY end_at''',
                (start.isoformat(), now.isoformat())).fetchall()
        totals = [0.] * 4
        cursor = start
        continuous = True
        estimated = False
        for end_at, start_at, *values in rows:
            left, right = parse_time(start_at), parse_time(end_at)
            clip = max(start, left, cursor)
            if right <= clip:
                continue
            continuous &= clip == cursor
            width = (right - clip).total_seconds()
            result['balance_covered_seconds'] += width
            fraction = (clip - left) / (right - left)
            for index, pair in enumerate(values[:4]):
                a, b = json.loads(pair)
                totals[index] += (a + (b-a)*fraction + b)/2 * width/3600000
            estimated |= values[4] != 'measured'
            cursor = right
        if cursor > start:
            result['balance_through'] = cursor.isoformat()
        if (continuous and latest is not None and cursor == latest[0]
                and cursor > start and now - cursor <= timedelta(seconds=MAX_GAP)):
            other, solar, grid, injection = totals
            result.update(balance_status='Couverture complète jusqu’au dernier relevé',
                          balance_source='Commande DAC confirmée et étalonnée (estimation)' if estimated
                          else 'Mesure dédiée du boiler',
                          provided_kwh=solar + grid, pv_kwh=solar, grid_kwh=grid,
                          production_kwh=other + solar + injection, other_pv_kwh=other,
                          injection_kwh=injection, through=cursor.isoformat())
        return result
