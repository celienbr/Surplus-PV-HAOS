"""gPlugK power balance: both entities are confirmed to report kW.

Source: user-verified units (2026-10-23). HA attributes currently omit unit.
This code is READ-ONLY and is not connected to power hardware.
"""
from datetime import datetime, timezone
from math import isfinite

from .measurements import Measurement


def _read_kw_state(state: dict, entity_id: str) -> Measurement:
    if state.get("entity_id") not in (None, entity_id):
        raise ValueError(f"Unexpected HA entity ID for {entity_id}")
    raw = state.get("state")
    if raw in (None, "", "unknown", "unavailable"):
        raise ValueError(f"{entity_id}: unavailable")
    try:
        kw = float(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{entity_id}: invalid reading") from exc
    if not isfinite(kw) or not 0 <= kw <= 100:
        raise ValueError(f"{entity_id}: invalid or negative magnitude")
    # User has independently confirmed kW for these two specific entities.
    # If HA later declares an incompatible unit, refuse silent conversion.
    unit = state.get("attributes", {}).get("unit_of_measurement")
    if unit not in (None, "kW"):
        raise ValueError(f"{entity_id}: unexpected unit {unit!r}; expected kW")

    # last_updated does not advance for unchanged zero readings.
    # last_reported DOES advance when the HA integration republishes.
    # If last_reported is missing, fall back conservatively; a stale value
    # must NOT be treated as a fresh meter reading.
    raw_ts = state.get("last_reported") or state.get("last_updated")
    if not raw_ts:
        raise ValueError(f"{entity_id}: missing report timestamp")
    try:
        ts = datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError) as exc:
        raise ValueError(f"{entity_id}: malformed report timestamp") from exc
    if ts.tzinfo is None:
        raise ValueError(f"{entity_id}: timezone missing")
    return Measurement(kw, ts.astimezone(timezone.utc))


def net_grid_power_w(pi_state: dict, po_state: dict) -> Measurement:
    """Pgrid = 1000*(Pi-Po); import positive, export negative.

    Timestamp uses the OLDER of the two reports: both must be fresh.
    """
    pi = _read_kw_state(pi_state, "sensor.gplugk_z_pi")
    po = _read_kw_state(po_state, "sensor.gplugk_z_po")
    return Measurement(1000 * (pi.value - po.value), min(pi.measured_at, po.measured_at))



def assess_grid_reading(pi_state: dict, po_state: dict, now: datetime,
                        max_age_seconds: float) -> dict:
    """Separate communication health (Pi OR Po fresh) from power confidence.

    The user's gPlugK reports exclusive directional magnitudes. An inactive
    channel can remain at exactly zero with an old HA timestamp. A recent
    report on either channel is enough for communication health *within HA*.
    This cannot prove freshness of the underlying device's source packet.

    This diagnostic application has no physical outputs or hardware watchdog.
    """
    pi = _read_kw_state(pi_state, "sensor.gplugk_z_pi")
    po = _read_kw_state(po_state, "sensor.gplugk_z_po")
    pi_fresh = pi.valid(now, max_age_seconds)
    po_fresh = po.valid(now, max_age_seconds)

    if not (pi_fresh or po_fresh):
        raise ValueError("gPlugK communication stale: neither Pi nor Po has a recent HA report")

    if pi_fresh and po_fresh:
        if pi.value > 0 and po.value > 0:
            raise ValueError("Pi and Po simultaneously nonzero: incoherent directional measurement")
        return {
            "status": "OK",
            "communication_healthy": True,
            "grid_w": 1000 * (pi.value - po.value),
            "reason": "Communication OK: recent Pi and Po reports",
            "coverage": "TWO_RECENT",
            "eligible_for_hardware": False,
        }

    if pi_fresh and po.value == 0:
        return {
            "status": "OK",
            "communication_healthy": True,
            "grid_w": 1000 * pi.value,
            "reason": "Communication OK via Pi; Po=0 unchanged (old report)",
            "coverage": "ONE_RECENT",
            "eligible_for_hardware": False,
        }

    if po_fresh and pi.value == 0:
        return {
            "status": "OK",
            "communication_healthy": True,
            "grid_w": -1000 * po.value,
            "reason": "Communication OK via Po; Pi=0 unchanged (old report)",
            "coverage": "ONE_RECENT",
            "eligible_for_hardware": False,
        }

    # One recent report proves HA is receiving sensor data, but an old
    # nonzero opposite channel makes the *power* ambiguous during transitions.
    # Keep health GREEN while refusing to calculate a misleading net power.
    return {
        "status": "POWER_UNKNOWN",
        "communication_healthy": True,
        "grid_w": None,
        "reason": "Communication OK (one recent report), but other direction retains old nonzero value",
        "coverage": "ONE_RECENT",
        "eligible_for_hardware": False,
    }
