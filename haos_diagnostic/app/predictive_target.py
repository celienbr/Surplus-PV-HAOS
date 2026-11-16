"""Slow, side-effect-free PV potential adviser; never a power budget.

The conservative factor is a classification margin, not an estimate of
future export. Only the live grid meter can authorize surplus heating.
"""
from datetime import datetime, timedelta, timezone
from math import isfinite
from zoneinfo import ZoneInfo

UTC = timezone.utc
ZONE = ZoneInfo("Europe/Zurich")
FACTOR = 4.0  # require four kWh of gross PV per kWh of extra storage
HYSTERESIS = 0.10


def _instant(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp without timezone")
    return value.astimezone(UTC)


def _number(value, low, high):
    if isinstance(value, bool):
        raise ValueError("boolean is not a number")
    value = float(value)
    if not isfinite(value) or not low <= value <= high:
        raise ValueError("number outside permitted range")
    return value


def predictive_target(profile, temperature_c, minimum_c, maximum_c, volume_l, now,
                      enabled, previous=None):
    """Classify gross PV through tomorrow's Zurich midnight, requiring coverage.

    Daily totals and the hourly series must both be fresh. Their minimum is a
    conservative consistency bound, not an assertion about available surplus.
    Hysteresis retains the previous level within 10% of a threshold. State is
    intentionally volatile: a restart recalculates from current forecasts.
    """
    now = _instant(now)
    minimum = _number(minimum_c, 30, 80)
    maximum = _number(maximum_c, minimum, 80)
    volume = _number(volume_l, 1, 2000)
    temperature = _number(temperature_c, 0, 83)
    result = {"status": "OFF" if not enabled else "UNAVAILABLE", "target_c": None,
              "fallback": True, "calculated_at": now.isoformat(),
              "pv_potential_kwh": None, "thermal_energy_kwh": None,
              "reason": "Fonction désactivée : comportement Alpha.17.",
              "hysteresis": "NONE", "horizon_end": None,
              "coverage_status": "UNAVAILABLE", "covered_hours": 0,
              "missing_hours": 0, "first_missing_hour": None}
    if not enabled:
        return result
    try:
        if not isinstance(profile, dict) or profile.get("daily_pv_status") != "OK":
            raise ValueError("totaux journaliers indisponibles")
        for key in ("generated_at", "daily_pv_updated_at"):
            age = (now - _instant(profile[key])).total_seconds()
            if not -300 <= age <= 10800:
                raise ValueError("prévision périmée ou future")
        today = _number(profile["daily_today_remaining_kwh"], 0, 200)
        tomorrow = _number(profile["daily_tomorrow_kwh"], 0, 200)
        end_day = now.astimezone(ZONE).date() + timedelta(days=2)
        end = datetime(end_day.year, end_day.month, end_day.day, tzinfo=ZONE).astimezone(UTC)
        cursor, hourly = now, 0.0
        missing = []
        known_by_day = {}
        points = sorted(profile["points"], key=lambda p: _instant(p["at"]))
        for point in points:
            start = _instant(point["at"])
            stop = _instant(point.get("period_end") or (start + timedelta(hours=1)))
            if not 0 < (stop-start).total_seconds() <= 3600:
                raise ValueError("intervalle horaire invalide")
            if stop <= now or start >= end:
                continue
            start, stop = max(start, now), min(stop, end)
            if start != cursor:
                raise ValueError("profil horaire incomplet ou superposé")
            day = start.astimezone(ZONE).date()
            # A missing PV hour is unknown, not a fictitious measurement of 0 W.
            # Complete reported intervals contribute to a conservative lower
            # bound; holes within a day's reported range remain blocking. A
            # missing edge interval cannot be asserted to be night: solar
            # elevation and the provider's coverage are not available here.
            if point["pv_w"] is None:
                missing.append((start, day))
            else:
                watts = _number(point["pv_w"], 0, 50000)
                hourly += watts * (stop-start).total_seconds() / 3600000
                known_by_day.setdefault(day, []).append(start)
            cursor = stop
        if cursor != end:
            raise ValueError("profil horaire incomplet")
        result["coverage_status"] = "PARTIAL" if missing else "COMPLETE"
        result["covered_hours"] = sum(len(hours) for hours in known_by_day.values())
        result["missing_hours"] = len(missing)
        result["first_missing_hour"] = missing[0][0].isoformat() if missing else None
        if not known_by_day:
            raise ValueError("profil PV horaire sans heure exploitable")
        for day, hours in known_by_day.items():
            first, last = hours[0], hours[-1]
            internal = next((stamp for stamp, gap_day in missing
                             if gap_day == day and first < stamp < last), None)
            if internal is not None:
                raise ValueError("lacune PV horaire interne le " + internal.isoformat())
        local_today = now.astimezone(ZONE).date()
        local_tomorrow = local_today + timedelta(days=1)
        if ((today > 0 and local_today not in known_by_day)
                or (tomorrow > 0 and local_tomorrow not in known_by_day)):
            raise ValueError("jour avec prévision PV positive sans aucune heure connue")
        potential = min(today + tomorrow, hourly)
        levels = [minimum]
        while levels[-1] < maximum:
            levels.append(min(maximum, levels[-1] + 5))
        thresholds = [FACTOR * volume * 1.163 * (level-minimum) / 1000 for level in levels]
        index = sum(potential >= threshold for threshold in thresholds) - 1
        old = None
        if isinstance(previous, dict) and previous.get("status") == "OK":
            old = previous.get("target_c")
        if old in levels:
            old_index = levels.index(old)
            if index > old_index and potential < thresholds[old_index+1] * (1+HYSTERESIS):
                index = old_index
                result["hysteresis"] = "HELD_UP"
            elif index < old_index and potential >= thresholds[old_index] * (1-HYSTERESIS):
                index = old_index
                result["hysteresis"] = "HELD_DOWN"
        target = levels[index]
        result.update(status="OK", fallback=False, target_c=target,
                      pv_potential_kwh=round(potential, 3), horizon_end=end.isoformat(),
                      thermal_energy_kwh=round(volume * 1.163 * max(0, target-temperature) / 1000, 3),
                      reason=("PV brut borné par les totaux et le profil ; "
                              + ("borne basse conservatrice sur les seules heures renseignées "
                                 + f"({result['covered_hours']} h connues, {result['missing_hours']} h absentes, "
                                 + "aucun zéro inventé) ; " if missing else "")
                              + "seuils à quatre fois l'énergie de stockage au-dessus du minimum. "
                              + "Le surplus futur reste inconnu."))
    except (ValueError, TypeError, KeyError, OverflowError) as error:
        result["reason"] = "Repli Alpha.17 : " + str(error)[:160]
    return result
