"""Durable read-only telemetry history, forecast snapshots, archives and 7-day logs.

SQLite is stored under HA add-on's persistent /data directory, NOT in the
Docker image. Measurements are always original signed network W; unknown
power stays NULL. Archive rows are retained permanently in a second table
and downloadable via API. Writes never alter regulator inputs/outputs.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta, date, time
from math import isfinite
from pathlib import Path
from zoneinfo import ZoneInfo
import csv
import io
import itertools
from threading import RLock
import json
import logging
import sqlite3

LOG = logging.getLogger(__name__)
RETENTION = timedelta(hours=48)
LOG_RETENTION = timedelta(days=7)
UTC = timezone.utc


def aware(dt: datetime) -> datetime:
    if not isinstance(dt, datetime) or dt.tzinfo is None:
        raise ValueError("History time must be timezone-aware")
    return dt.astimezone(UTC)


def parse_time(s: str) -> datetime:
    if not isinstance(s, str) or len(s) > 45:
        raise ValueError("Invalid timestamp")
    try:
        return aware(datetime.fromisoformat(s.replace("Z", "+00:00")))
    except (TypeError, AttributeError, ValueError) as error:
        raise ValueError("Use an ISO 8601 date/time with timezone") from error


def valid_power(value):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
        raise ValueError("Invalid network power value")
    if abs(value) > 100000:
        raise ValueError("Network power outside diagnostic limits")
    return round(value)


class HistoryStore:
    def __init__(self, db_path: str | Path):
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = RLock()
        self.db = sqlite3.connect(str(self.path), check_same_thread=False, timeout=5)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA busy_timeout=5000")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS measurements(
                timestamp TEXT PRIMARY KEY,
                grid_w INTEGER, simulated_heater_w INTEGER,
                status TEXT NOT NULL, coverage TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS archived_measurements(
                timestamp TEXT PRIMARY KEY,
                grid_w INTEGER, simulated_heater_w INTEGER,
                status TEXT NOT NULL, coverage TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS forecasts(
                timestamp TEXT PRIMARY KEY, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS archived_forecasts(
                timestamp TEXT PRIMARY KEY, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS technical_logs(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL, level TEXT NOT NULL,
                event TEXT NOT NULL, message TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS history_log_time ON technical_logs(timestamp);
        """)
        self.db.commit()
        self._last_maintenance = None

    def close(self):
        with self.lock:
            self.db.close()

    def record(self, when: datetime, state: dict) -> None:
        """Record all attempts; a fault/unknown meter is saved as NULL, not zero."""
        stamp = aware(when).isoformat()
        grid = valid_power(state.get("meter_w")) if state.get("status") == "OK" else None
        heater = valid_power(state.get("simulated_boiler_w")) if grid is not None else None
        status = str(state.get("status") or "UNKNOWN")[:40]
        coverage = str(state.get("measurement_coverage") or "NONE")[:40]
        with self.lock:
            with self.db:
                self.db.execute(
                    "INSERT OR REPLACE INTO measurements VALUES (?,?,?,?,?)",
                    (stamp, grid, heater, status, coverage))
            self._maintenance_if_due(aware(when))

    def record_forecast(self, when: datetime, profile: dict) -> None:
        at = aware(when)
        payload = json.dumps(profile, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        if len(payload) > 250000:
            raise ValueError("Forecast snapshot too large")
        with self.lock:
            with self.db:
                self.db.execute(
                    "INSERT OR REPLACE INTO forecasts VALUES (?,?)",
                    (at.isoformat(), payload))
            self._maintenance_if_due(at)

    def log(self, when: datetime, level: str, event: str, message: str) -> None:
        stamp = aware(when).isoformat()
        if level not in ("INFO", "WARNING", "ERROR"):
            raise ValueError("Unsupported log level")
        with self.lock:
            with self.db:
                self.db.execute(
                    "INSERT INTO technical_logs(timestamp,level,event,message) VALUES(?,?,?,?)",
                    (stamp, level, event[:40], str(message)[:400]))
            self._maintenance_if_due(aware(when))

    def _maintenance_if_due(self, now: datetime) -> None:
        if self._last_maintenance is not None and now - self._last_maintenance < timedelta(minutes=15):
            return
        cutoff = (now - RETENTION).isoformat()
        log_cutoff = (now - LOG_RETENTION).isoformat()
        # Atomic archiving within one database; no measurement is discarded
        # unless its archive copy has been committed in the SAME transaction.
        with self.db:
            self.db.execute("""
                INSERT OR IGNORE INTO archived_measurements
                SELECT * FROM measurements WHERE timestamp < ?""", (cutoff,))
            self.db.execute("DELETE FROM measurements WHERE timestamp < ?", (cutoff,))
            self.db.execute("""
                INSERT OR IGNORE INTO archived_forecasts
                SELECT * FROM forecasts WHERE timestamp < ?""", (cutoff,))
            self.db.execute("DELETE FROM forecasts WHERE timestamp < ?", (cutoff,))
            self.db.execute("DELETE FROM technical_logs WHERE timestamp < ?", (log_cutoff,))
        self._last_maintenance = now

    def query(self, start: datetime, end: datetime, *, max_points=1200) -> dict:
        start, end = aware(start), aware(end)
        if end <= start or end - start > timedelta(days=8):
            raise ValueError("History window must be 0-8 days")
        if not 60 <= max_points <= 4000:
            raise ValueError("History result size must be 60-4000")
        with self.lock:
            rows = self.db.execute("""
              SELECT timestamp,grid_w,simulated_heater_w,status,coverage
                FROM archived_measurements WHERE timestamp>=? AND timestamp<=?
              UNION ALL
              SELECT timestamp,grid_w,simulated_heater_w,status,coverage
                FROM measurements WHERE timestamp>=? AND timestamp<=?
              ORDER BY timestamp
            """, (start.isoformat(), end.isoformat(), start.isoformat(), end.isoformat())).fetchall()
        # Group points by time bucket; preserve min/max of signed grid for
        # zero crossing and spikes, and keep explicit NULL at failures.
        # Never average away negative injection with positive purchases.
        real_gaps = set()
        previous = None
        for item in rows:
            current = parse_time(item[0])
            if previous is not None and (current - previous).total_seconds() > 180:
                real_gaps.add(item[0])
            previous = current
        if len(rows) > max_points:
            stride = max(1, (len(rows) + max(1,max_points//6) - 1)//max(1,max_points//6))
            sampled = []
            for k in range(0, len(rows), stride):
                group = rows[k:k+stride]
                valid = [r for r in group if r[1] is not None]
                picks = [group[0], group[-1]]
                if valid:
                    picks += [min(valid, key=lambda r:r[1]), max(valid, key=lambda r:r[1])]
                invalid = next((r for r in group if r[1] is None), None)
                if invalid is not None:
                    picks.append(invalid)
                sampled.extend({r[0]:r for r in picks}.values())
            rows = sorted({r[0]:r for r in sampled}.values())[:max_points]
        return {
            "start":start.isoformat(), "end":end.isoformat(),
            "count":len(rows),
            "points":[{"at":t,"grid_w":grid,"simulated_boiler_w":heater,
                       "status":status,"coverage":coverage,"gap_before":t in real_gaps}
                      for t,grid,heater,status,coverage in rows],
            "signed_convention":"positive=achat / negative=injection",
            "sampling":"min/max preserved by bucket; gaps are not interpolated",
        }

    def forecast_at(self, at: datetime) -> dict | None:
        stamp = aware(at).isoformat()
        with self.lock:
            result = self.db.execute("""
                SELECT timestamp,payload FROM
                (SELECT timestamp,payload FROM archived_forecasts WHERE timestamp<=?
                 UNION ALL
                 SELECT timestamp,payload FROM forecasts WHERE timestamp<=?)
                ORDER BY timestamp DESC LIMIT 1
            """, (stamp, stamp)).fetchone()
        if result is None:
            return None
        return {"saved_at":result[0], "profile":json.loads(result[1])}

    def archives(self) -> dict:
        with self.lock:
            rows = self.db.execute("""
                SELECT substr(timestamp,1,10) AS day,COUNT(*)
                  FROM archived_measurements GROUP BY day ORDER BY day DESC LIMIT 3650
            """).fetchall()
            f = self.db.execute("""
                SELECT substr(timestamp,1,10) AS day,COUNT(*)
                  FROM archived_forecasts GROUP BY day ORDER BY day DESC LIMIT 3650
            """).fetchall()
        return {"storage":"persistent /data SQLite", "retention":"48h active, archive permanent",
                "days":[{"date":day,"measurements":n} for day,n in rows],
                "forecast_days":[{"date":day,"snapshots":n} for day,n in f]}

    def logs(self, *, limit=300) -> dict:
        if not 1 <= limit <= 1000:
            raise ValueError("Invalid log limit")
        with self.lock:
            rows = self.db.execute("""
                SELECT timestamp,level,event,message FROM technical_logs
                ORDER BY timestamp DESC,id DESC LIMIT ?""", (limit,)).fetchall()
        return {"retention_days":7, "logs":[
            {"at":stamp,"level":level,"event":event,"message":message}
            for stamp,level,event,message in rows]}

    @staticmethod
    def _safe_csv_text(value: str) -> str:
        """Protect Excel users if externally supplied status ever resembles a formula."""
        value = str(value)
        if value[:1] in ("=", "+", "-", "@", "\t", "\r", "\n"):
            return "'" + value
        return value

    @staticmethod
    def _fr_number(value: int | None, *, as_kw: bool = False,
                   excel: bool = True) -> str:
        if value is None:
            return ""
        if as_kw:
            result = f"{value / 1000:.3f}"
            return result.replace(".", ",") if excel else result
        return str(value)

    @staticmethod
    def _export_bounds(start_date: str, end_date: str) -> tuple[str, str]:
        tz = ZoneInfo("Europe/Zurich")
        if "T" in start_date or "T" in end_date:
            # Precise instant export for the true rolling last 48 hours.
            if "T" not in start_date or "T" not in end_date:
                raise ValueError("Start and end must both use datetimes")
            begin = parse_time(start_date)
            finish = parse_time(end_date)
        else:
            try:
                a = date.fromisoformat(start_date)
                b = date.fromisoformat(end_date)
                if a.isoformat() != start_date or b.isoformat() != end_date:
                    raise ValueError("Noncanonical date")
            except (TypeError, ValueError) as error:
                raise ValueError("Dates must be YYYY-MM-DD") from error
            if not 0 <= (b - a).days <= 30:
                raise ValueError("Choose between 1 and 31 inclusive calendar days")
            begin = datetime.combine(a, time.min, tzinfo=tz).astimezone(UTC)
            finish = datetime.combine(b + timedelta(days=1), time.min, tzinfo=tz).astimezone(UTC)
        if not timedelta(0) < finish - begin <= timedelta(days=32):
            raise ValueError("Export duration must be 1 second to 31 calendar days")
        if begin > datetime.now(UTC) + timedelta(days=1):
            raise ValueError("Cannot export a future history period")
        start = begin.isoformat()
        end = finish.isoformat()
        return start, end

    def iter_export_history_csv(self, start_date: str, end_date: str,
                                *, excel: bool = True):
        """Export RAW measurements from active + archived tables, no downsampling.

        Dates are Swiss calendar days, start/end INCLUSIVE, with DST handled
        through timezone-aware midnight boundaries. The result is a chunk
        iterator backed by a separate read-only SQLite connection so lengthy
        exports do not hold the data-collection mutex. Max 31 days per request
        avoids Excel's 1,048,576-row limit even at the 5s sampling minimum.
        """
        start, end = self._export_bounds(start_date, end_date)
        tz = ZoneInfo("Europe/Zurich")
        delimiter = ";" if excel else ","
        # UTF-8 with BOM and a delimiter hint make a double-click in Excel CH
        # open columns correctly; alternative RFC-style CSV for other tools.
        def lines():
            if excel:
                yield b"\xef\xbb\xbfsep=;\r\n"
            headers = [
                "Date locale CH", "Heure locale CH", "Date-heure UTC ISO8601",
                "Fuseau UTC", "Puissance reseau signee (W)",
                "Achat reseau (W)", "Injection reseau (W)",
                "Puissance reseau signee (kW)", "Chauffe virtuelle (W)",
                "Etat mesure", "Couverture compteur", "Nature donnees"
            ]
            buf = io.StringIO(newline="")
            writer = csv.writer(buf, delimiter=delimiter, lineterminator="\r\n")
            writer.writerow(headers)
            yield buf.getvalue().encode("utf-8")
            buf.seek(0)
            buf.truncate(0)
            db = sqlite3.connect(
                "file:" + self.path.absolute().as_posix() + "?mode=ro",
                uri=True, timeout=30
            )
            try:
                cur = db.execute("""
                    SELECT timestamp,grid_w,simulated_heater_w,status,coverage
                      FROM archived_measurements
                     WHERE timestamp>=? AND timestamp<?
                    UNION ALL
                    SELECT timestamp,grid_w,simulated_heater_w,status,coverage
                      FROM measurements
                     WHERE timestamp>=? AND timestamp<?
                    ORDER BY timestamp
                """, (start, end, start, end))
                count = 0
                while True:
                    batch = cur.fetchmany(1000)
                    if not batch:
                        break
                    for ts, watts, virtual, status, coverage in batch:
                        instant = parse_time(ts)
                        local = instant.astimezone(tz)
                        delta = local.strftime("%z")
                        offset = delta[:3] + ":" + delta[3:]
                        writer.writerow([
                            local.strftime("%d.%m.%Y"),
                            local.strftime("%H:%M:%S"),
                            instant.strftime("%Y-%m-%dT%H:%M:%SZ"),
                            offset,
                            self._fr_number(watts, excel=excel),
                            self._fr_number(max(0, watts) if watts is not None else None, excel=excel),
                            self._fr_number(max(0, -watts) if watts is not None else None, excel=excel),
                            self._fr_number(watts, as_kw=True, excel=excel),
                            self._fr_number(virtual, excel=excel),
                            self._safe_csv_text(status),
                            self._safe_csv_text(coverage),
                            "Mesure reseau reelle ; chauffe virtuelle simulee",
                        ])
                    count += len(batch)
                    # Raw source records are not decimated or synthesized.
                    # Defensive bound below Excel's maximum row count.
                    if count > 1000000:
                        raise ValueError("CSV exceeds Excel's supported row count")
                    yield buf.getvalue().encode("utf-8")
                    buf.seek(0)
                    buf.truncate(0)
            finally:
                db.close()

        return lines()

    def iter_export_auxiliary_csv(self, start_date: str, end_date: str,
                                      *, excel: bool = True, dataset: str = "forecasts"):
        """Export archived + active forecast points or retained technical logs."""
        if dataset not in ("forecasts", "logs"):
            raise ValueError("Unknown historical dataset")
        start, end = self._export_bounds(start_date, end_date)
        delimiter = ";" if excel else ","
        zone = ZoneInfo("Europe/Zurich")
        def numeric(number, places=0):
            if number is None or isinstance(number, bool) or not isinstance(number,(int,float)) or not isfinite(number):
                return ""
            value = f"{number:.{places}f}"
            return value.replace(".", ",") if excel else value
        def run():
            if excel:
                yield b"\xef\xbb\xbfsep=;\r\n"
            buf=io.StringIO(newline="")
            writer=csv.writer(buf,delimiter=delimiter,lineterminator="\r\n")
            if dataset=="forecasts":
                writer.writerow([
                    "Prevision publiee CH", "Prevision publiee UTC",
                    "Heure visee CH", "Heure visee UTC", "Puissance PV prevue (W)",
                    "Temperature exterieure prevue (C)",
                    "PAC estimee (W)", "Maison estimee (W)",
                    "Surplus estime (W)", "Chauffe possible estimee (W)",
                    "Statut prevision", "PV statut", "Meteo statut", "PAC statut",
                    "Nature donnees"
                ])
            else:
                writer.writerow(["Date CH","Heure CH","Date-heure UTC ISO8601",
                                 "Niveau","Evenement","Message technique"])
            yield buf.getvalue().encode("utf-8")
            buf.seek(0);buf.truncate(0)
            db=sqlite3.connect("file:"+self.path.absolute().as_posix()+"?mode=ro",uri=True,timeout=30)
            try:
                if dataset=="forecasts":
                    cur=db.execute("""
                        SELECT timestamp,payload FROM archived_forecasts
                         WHERE timestamp>=? AND timestamp<?
                        UNION ALL
                        SELECT timestamp,payload FROM forecasts
                         WHERE timestamp>=? AND timestamp<?
                        ORDER BY timestamp
                    """,(start,end,start,end))
                else:
                    cur=db.execute("""
                        SELECT timestamp,level,event,message FROM technical_logs
                         WHERE timestamp>=? AND timestamp<? ORDER BY timestamp,id
                    """,(start,end))
                count=0
                while True:
                    rows=cur.fetchmany(100)
                    if not rows:break
                    for row in rows:
                        if dataset=="logs":
                            at,level,event,message=row
                            instant=parse_time(at)
                            local=instant.astimezone(zone)
                            writer.writerow([local.strftime("%d.%m.%Y"),
                                             local.strftime("%H:%M:%S"),
                                             instant.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                             self._safe_csv_text(level),
                                             self._safe_csv_text(event),
                                             self._safe_csv_text(message)])
                            count+=1
                        else:
                            at,payload=row
                            issued=parse_time(at)
                            document=json.loads(payload)
                            points=document.get("points") or [{}]
                            if not isinstance(points,list):
                                points=[{}]
                            if len(points)>240:
                                raise ValueError("Too many points in one forecast")
                            for point in points:
                                if not isinstance(point,dict):continue
                                when=point.get("at")
                                try:target=parse_time(when)
                                except (TypeError,ValueError):target=None
                                writer.writerow([
                                    issued.astimezone(zone).strftime("%d.%m.%Y %H:%M:%S"),
                                    issued.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                    target.astimezone(zone).strftime("%d.%m.%Y %H:%M:%S") if target else "",
                                    target.strftime("%Y-%m-%dT%H:%M:%SZ") if target else "",
                                    numeric(point.get("pv_w")),
                                    numeric(point.get("temperature_c"),1),
                                    numeric(point.get("pac_w")),
                                    numeric(point.get("house_w")),
                                    numeric(point.get("surplus_w")),
                                    numeric(point.get("heater_budget_w")),
                                    self._safe_csv_text(document.get("status","")),
                                    self._safe_csv_text(document.get("solar_status","")),
                                    self._safe_csv_text(document.get("meteo_status","")),
                                    self._safe_csv_text(document.get("pac_status","")),
                                    "PREVISION ENREGISTREE ; PAS UNE MESURE REELLE",
                                ])
                                count+=1
                    if count>1000000:
                        raise ValueError("CSV exceeds Excel's row limit")
                    yield buf.getvalue().encode("utf-8")
                    buf.seek(0);buf.truncate(0)
            finally:
                db.close()
        return run()

    def export_archive_csv(self, date: str) -> bytes:
        """CSV for a single archived UTC day, no path manipulation."""
        if not isinstance(date, str) or len(date)!=10:
            raise ValueError("Expected YYYY-MM-DD")
        try:
            checked = datetime.strptime(date,"%Y-%m-%d")
        except ValueError as exc:
            raise ValueError("Invalid archive date") from exc
        if checked.strftime("%Y-%m-%d") != date:
            raise ValueError("Noncanonical archive date")
        with self.lock:
            rows = self.db.execute("""
                SELECT * FROM archived_measurements
                WHERE timestamp>=? AND timestamp<? ORDER BY timestamp""",
                (date, (checked+timedelta(days=1)).date().isoformat())).fetchall()
        import csv
        import io
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["timestamp_utc","reseau_signe_W","chauffe_virtuelle_W","statut","couverture"])
        writer.writerows(rows)
        return output.getvalue().encode("utf-8")
