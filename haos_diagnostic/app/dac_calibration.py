"""User multimeter records; never infer physical measurement from I2C ACK."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import sqlite3
from threading import Lock

from .gp8600 import number


class CalibrationStore:
    def __init__(self, path=":memory:"):
        self.lock = Lock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("""CREATE TABLE IF NOT EXISTS dac_measurements_v1 (
            id INTEGER PRIMARY KEY, at TEXT NOT NULL, payload TEXT NOT NULL)""")
        self.db.commit()

    def record(self, state, data):
        if set(data) != {"voltage_v", "resistance_ohm"}:
            raise ValueError("Enter measured voltage and actual load resistance")
        if state["mode"] != "BANC_DAC" or not state["enabled"] or not state["dac"]["ready"]:
            raise ValueError("An enabled, communicating DAC bench is required")
        resistance = number(data["resistance_ohm"], 247.5, 252.5)
        voltage = number(data["voltage_v"], 0, 6)
        measured = voltage / resistance * 1000
        point = {"requested_ma": state["requested_ma"],
                 "acknowledged_ma": state["dac"]["last_acknowledged_ma"],
                 "code": state["dac"]["code"], "voltage_v": voltage,
                 "resistance_ohm": resistance, "measured_ma": measured,
                 "error_ma": measured - state["requested_ma"],
                 "driver": state["dac"]["driver"],
                 "address": state["dac"].get("address"),
                 "provenance": "USER_MULTIMETER", "revision": state["revision"]}
        with self.lock:
            self.db.execute("INSERT INTO dac_measurements_v1(at,payload) VALUES (?,?)",
                            (datetime.now(timezone.utc).isoformat(), json.dumps(point, allow_nan=False)))
            self.db.commit()
        return point

    def snapshot(self, driver, address=None):
        with self.lock:
            rows = self.db.execute("SELECT id,at,payload FROM dac_measurements_v1 ORDER BY id DESC LIMIT 500").fetchall()
        points = [{"id": r["id"], "at": r["at"], **json.loads(r["payload"])} for r in rows]
        selected = [p for p in points if p["driver"] == driver and p["address"] == address]
        fit = None
        if len(selected) >= 2:
            xs = [p["acknowledged_ma"] for p in selected]
            ys = [p["measured_ma"] for p in selected]
            xm, ym = sum(xs)/len(xs), sum(ys)/len(ys)
            variance = sum((x-xm)**2 for x in xs)
            if max(xs)-min(xs) >= 4 and variance > 0:
                gain = sum((x-xm)*(y-ym) for x,y in zip(xs,ys))/variance
                offset = ym-gain*xm
                if gain > 0:
                    fit = {"gain": gain, "offset_ma": offset,
                           "max_residual_ma": max(abs(y-(gain*x+offset)) for x,y in zip(xs,ys)),
                           "range_ma": [min(xs),max(xs)], "points": len(xs),
                           "correction_formula": "commande_mA = (cible_mA - offset_mA) / gain",
                           "applied": False, "status": "PROPOSITION_DEPUIS_RELEVES_UTILISATEUR"}
        return {"points": points, "fit": fit,
                "note": "Correction proposée uniquement ; contrôle des extrémités 0–20 mA et revue physique requis."}

    def close(self):
        with self.lock:
            self.db.close()
