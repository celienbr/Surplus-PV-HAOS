"""User multimeter records; never infer physical measurement from I2C ACK."""
from __future__ import annotations

import json
import sqlite3
from threading import Lock



class CalibrationStore:
    def __init__(self, path=":memory:"):
        self.lock = Lock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("""CREATE TABLE IF NOT EXISTS dac_measurements_v1 (
            id INTEGER PRIMARY KEY, at TEXT NOT NULL, payload TEXT NOT NULL)""")
        self.db.commit()

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
