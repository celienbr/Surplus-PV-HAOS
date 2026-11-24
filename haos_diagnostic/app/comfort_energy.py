"""Conservative daily estimate from logged boiler power requests, never a meter.

Only intervals with two valid requests from the same process and no gap over
180 seconds are integrated. A complete estimate requires coverage from local
midnight to a recent recorded point; legacy rows have no restart identity.
"""
from datetime import datetime, timezone
from math import isfinite
from zoneinfo import ZoneInfo
import sqlite3

from .history_store import parse_time

ZONE = ZoneInfo('Europe/Zurich')
MAX_GAP = 180


def estimate_requests(rows, start, now):
    total = 0.0
    cursor = start
    previous = None
    for stamp, power, session in rows:
        at = parse_time(stamp)
        if at > now:
            break
        valid = (isinstance(power, (int, float)) and not isinstance(power, bool)
                 and isfinite(power) and 0 <= power <= 6000 and bool(session))
        if previous:
            old_at, old_power, old_session, old_valid = previous
            dt = (at-old_at).total_seconds()
            left = max(old_at, start)
            if at > left:
                if (not old_valid or not valid or session != old_session or
                        not 0 < dt <= MAX_GAP or left > cursor):
                    return None, None
                # Linear interpolation is an estimate of requested, not measured power.
                vleft = old_power + (power-old_power)*(left-old_at).total_seconds()/dt
                total += (vleft+power)/2 * (at-left).total_seconds()/3600000
                cursor = at
        previous = (at, power, session, valid)
    if cursor <= start or (now-cursor).total_seconds() > MAX_GAP:
        return None, None
    return total, cursor


def daily_requested_estimate(store, now):
    local = now.astimezone(ZONE)
    start = local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    db = sqlite3.connect('file:'+store.path.absolute().as_posix()+'?mode=ro', uri=True, timeout=5)
    try:
        rows = db.execute('''SELECT timestamp,requested_w,session FROM comfort_energy_samples
            WHERE timestamp >= ? AND timestamp <= ? ORDER BY timestamp''',
            (datetime.fromtimestamp(start.timestamp()-MAX_GAP, timezone.utc).isoformat(), now.isoformat())).fetchall()
    finally:
        db.close()
    total, through = estimate_requests(rows, start, now)
    return {'provided_kwh': total, 'through': through.isoformat() if through else None,
            'period_start': start.isoformat(), 'timezone': 'Europe/Zurich',
            'pv_kwh': None, 'grid_kwh': None,
            'method': 'Estimation de la puissance demandée, non mesurée ; répartition indisponible'}
