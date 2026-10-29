"""Read-only energy views over the original, permanently archived grid samples.

No cumulative meter is inferred from Pi/Po. Integrals are estimates over covered
intervals only; there is no extrapolation across faults or gaps (>180 seconds).
"""
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo
from math import isfinite
import sqlite3
from threading import RLock
from time import monotonic

from .history_store import aware, parse_time

ZONE = ZoneInfo('Europe/Zurich')
UTC = timezone.utc
MAX_GAP = 180
SOURCE = 'Kamstrup OMNIPOWER / gPlugK via Home Assistant · Pi − Po'


def period_bounds(period, selected):
    """Canonical Swiss calendar boundaries; elapsed UTC handles 23/25 h days."""
    try:
        if period == 'day':
            start = date.fromisoformat(selected)
            if start.isoformat() != selected:
                raise ValueError()
            end = start + timedelta(days=1)
            dates = [start, end]
        elif period == 'month':
            start = date.fromisoformat(selected + '-01')
            if start.strftime('%Y-%m') != selected:
                raise ValueError()
            end = date(start.year + (start.month == 12), start.month % 12 + 1, 1)
            dates = [start + timedelta(days=i) for i in range((end-start).days+1)]
        elif period == 'year':
            if len(selected) != 4 or not selected.isascii() or not selected.isdigit():
                raise ValueError()
            start, end = date(int(selected), 1, 1), date(int(selected)+1, 1, 1)
            dates = [date(start.year, m, 1) for m in range(1,13)] + [end]
        else:
            raise ValueError()
        if not 2000 <= start.year <= 9998:
            raise ValueError()
    except (ValueError, TypeError, OverflowError) as exc:
        raise ValueError('Expected day YYYY-MM-DD, month YYYY-MM or year YYYY (2000–9998)') from exc
    return [datetime.combine(d, time.min, tzinfo=ZONE).astimezone(UTC) for d in dates]


def signed_integral(a, b, seconds):
    """Exact positive/negative areas of a linear segment, in kWh."""
    if a*b < 0:
        fraction = abs(a)/(abs(a)+abs(b))
        x = abs(a)*seconds*fraction/2/3600000
        y = abs(b)*seconds*(1-fraction)/2/3600000
        return (x,y) if a > 0 else (y,x)
    area = (a+b)*seconds/2/3600000
    return max(0,area), max(0,-area)


def _valid(row):
    return (row[2] == 'OK' and row[3] in ('TWO_RECENT','ONE_RECENT')
            and isinstance(row[1], (int,float)) and isfinite(row[1])
            and abs(row[1]) <= 100000)


class EnergyDashboard:
    def __init__(self, store):
        self.store = store
        self._cache = {}
        self._lock = RLock()  # Separate from acquisition/HistoryStore mutex.

    def query(self, period, selected, now=None):
        now = aware(now or datetime.now(UTC))
        bounds = period_bounds(period, selected)
        key = (period, selected)
        with self._lock:
            cached = self._cache.get(key)
            if cached and 0 <= monotonic()-cached[0] < 60 and now >= cached[1]:
                return cached[2]
            result = self._query(period, selected, bounds, now)
            if len(self._cache) >= 6:
                self._cache.pop(next(iter(self._cache)))
            self._cache[key] = (monotonic(), now, result)
            return result

    def _query(self, period, selected, bounds, now):
        start, end = bounds[0], bounds[-1]
        finish = min(end, now)
        buckets = []
        for a,b in zip(bounds,bounds[1:]):
            buckets.append({'at':a.isoformat(), 'end':b.isoformat(),
                            'import_kwh':None, 'export_kwh':None,
                            'pv_actual_kwh':None, 'self_consumed_kwh':None,
                            'covered_seconds':0., 'one_recent_seconds':0.,
                            'period_seconds':(b-a).total_seconds(),
                            'elapsed_seconds':max(0,(min(b,now)-a).total_seconds())})
        # The extra samples permit exact clipping at calendar boundaries, but
        # only when both endpoints exist and pass quality/gap checks.
        low = (start-timedelta(seconds=MAX_GAP)).isoformat()
        high = (finish+timedelta(seconds=MAX_GAP)).isoformat()
        points = []
        # Independent read-only connection: aggregation never blocks writes
        # with the HistoryStore acquisition mutex. WAL gives a stable snapshot.
        db = sqlite3.connect('file:'+self.store.path.absolute().as_posix()+'?mode=ro', uri=True, timeout=5)
        try:
            cursor = db.execute('''
                SELECT timestamp,grid_w,status,coverage FROM archived_measurements
                 WHERE timestamp>=? AND timestamp<=?
                UNION ALL
                SELECT timestamp,grid_w,status,coverage FROM measurements
                 WHERE timestamp>=? AND timestamp<=? ORDER BY timestamp
            ''', (low,high,low,high))
            previous = None
            bucket_index = 0
            anomalies = 0
            for row in cursor:
                at = parse_time(row[0])
                if at > now:
                    break
                valid = _valid(row)
                if row[1] is not None and not valid and start <= at < finish:
                    anomalies += 1
                if previous:
                    before, old, old_valid = previous
                    delta = (at-before).total_seconds()
                    if valid and old_valid and 0 < delta <= MAX_GAP:
                        left, right = max(before,start), min(at,finish)
                        while bucket_index < len(buckets)-1 and bounds[bucket_index+1] <= left:
                            bucket_index += 1
                        idx = bucket_index
                        while left < right and idx < len(buckets):
                            stop = min(right,bounds[idx+1])
                            va = old[1]+(row[1]-old[1])*(left-before).total_seconds()/delta
                            vb = old[1]+(row[1]-old[1])*(stop-before).total_seconds()/delta
                            seconds = (stop-left).total_seconds()
                            imported,exported = signed_integral(va,vb,seconds)
                            bucket = buckets[idx]
                            bucket['import_kwh'] = (bucket['import_kwh'] or 0)+imported
                            bucket['export_kwh'] = (bucket['export_kwh'] or 0)+exported
                            bucket['covered_seconds'] += seconds
                            if 'ONE_RECENT' in (old[3],row[3]):
                                bucket['one_recent_seconds'] += seconds
                            left = stop
                            idx += 1
                previous = (at,row,valid)
        finally:
            db.close()
        if period == 'day':
            points = self.store.query(start,end,max_points=1200)['points']
            # Do not expose a future record or invalid historical magnitude.
            points = [{**p, 'grid_w':p['grid_w'] if _valid((p['at'],p['grid_w'],p['status'],p['coverage'])) else None}
                      for p in points if parse_time(p['at']) < end and parse_time(p['at']) <= now]
        covered = sum(b['covered_seconds'] for b in buckets)
        elapsed = max(0,(finish-start).total_seconds())
        for bucket in buckets:
            bucket['partial'] = bucket['covered_seconds'] < bucket['period_seconds']-0.001
        return {'schema':1, 'period':period, 'selected':selected,
                'timezone':'Europe/Zurich', 'start':start.isoformat(), 'end':end.isoformat(),
                'generated_at':now.isoformat(), 'source':SOURCE,
                'method':'Intégration linéaire de puissance mesurée ; énergie estimée, pas un index compteur',
                'max_gap_seconds':MAX_GAP, 'points':points, 'buckets':buckets,
                'totals':{key:sum(b[key] or 0 for b in buckets) if covered else None
                          for key in ('import_kwh','export_kwh')},
                'covered_seconds':covered, 'elapsed_seconds':elapsed,
                'period_seconds':(end-start).total_seconds(),
                'partial':covered < (end-start).total_seconds()-0.001,
                'coverage_ratio':min(1,covered/elapsed) if elapsed else None,
                'anomaly_count':anomalies,
                'pv_actual':{'available':False, 'reason':'Production PV réelle : non disponible — intégration Sofar SW-004.2 requise'},
                'self_consumption':{'available':False, 'reason':'Autoconsommation : non disponible — mesures et topologie non validées'}}


def live_grid(snapshot, max_age, now=None):
    """Freshness at request time, without starting a second HA acquisition."""
    now = aware(now or datetime.now(UTC))
    try:
        delay = (now-parse_time(snapshot['updated_at'])).total_seconds()
        age = float(snapshot['meter_age_s'])+delay
        valid = (snapshot.get('status') == 'OK' and -2 <= delay
                 and -2 <= age <= max_age and snapshot.get('meter_w') is not None)
    except (ValueError, TypeError, KeyError):
        age, valid = None, False
    watts = snapshot.get('meter_w') if valid else None
    return {'grid_w':watts, 'direction':('import' if watts > 0 else 'export' if watts < 0 else 'balanced') if valid else None,
            'status':'OK' if valid else 'UNAVAILABLE', 'age_seconds':age,
            'updated_at':snapshot.get('updated_at'), 'source':SOURCE,
            'coverage':snapshot.get('measurement_coverage'),
            'quality':snapshot.get('data_quality') or snapshot.get('error'),
            'freshness_basis':'Rapport Home Assistant, pas horodatage physique du compteur'}
