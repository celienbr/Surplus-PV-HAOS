"""Deterministic R5 comfort planning. No I/O, no actuator or HA write access.

The requested backup power is a *total simulated boiler demand*, not measured
grid import. The caller must gate every use with fresh valid measurements and
the regulator's thermal protection. Forecasts never postpone the prudent start
derived from the temperature actually supplied to this function.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, time, timedelta, timezone
from math import isfinite
from zoneinfo import ZoneInfo

UTC = timezone.utc
ZURICH = ZoneInfo("Europe/Zurich")
WH_PER_LITRE_K = 1.163


def _number(value, name, lower, upper):
    if isinstance(value, bool):
        raise ValueError(name + " must be numeric, not boolean")
    try:
        result = float(value)
    except (ValueError, TypeError, OverflowError):
        raise ValueError("Invalid " + name) from None
    if not isfinite(result) or not lower <= result <= upper:
        raise ValueError("Invalid " + name)
    return result


def _utc(value):
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise ValueError("Invalid timestamp") from None
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Timestamp must include a timezone")
    return value.astimezone(UTC)


def _settings(options):
    enabled = options.get("grid_backup_enabled", False)
    weather = options.get("weather_enabled", False)
    if not isinstance(enabled, bool) or not isinstance(weather, bool):
        raise ValueError("Comfort and weather permissions must be booleans")
    text = options.get("comfort_time", "06:00")
    if not isinstance(text, str) or len(text) != 5 or text[2] != ":" or not (
            text[:2].isascii() and text[:2].isdigit() and
            text[3:].isascii() and text[3:].isdigit()):
        raise ValueError("Comfort time must be HH:MM")
    try:
        hour = time(int(text[:2]), int(text[3:]))
    except ValueError:
        raise ValueError("Comfort time must be HH:MM") from None
    maximum = _number(options.get("demo_max_c", 70), "maximum", 30, 80)
    result = {
        "enabled": enabled, "weather": weather, "hour": hour,
        "maximum": maximum,
        "minimum": _number(options.get("weather_min_c", 50), "minimum", 30, maximum),
        "volume": _number(options.get("weather_volume_l", 300), "volume", 1, 2000),
        "power": _number(options.get("max_power_w", 6000), "power", 100, 6000),
        "efficiency": _number(options.get("comfort_efficiency", .95), "efficiency", .5, 1),
        "loss": _number(options.get("comfort_loss_w", 100), "heat loss", 0, 1000),
        "margin": _number(options.get("comfort_margin_minutes", 20), "margin", 0, 120),
        "grace": _number(options.get("comfort_grace_minutes", 60), "grace", 0, 120),
        "step": _number(options.get("sample_seconds", 10), "sample interval", 1, 3600),
    }
    if result["loss"] >= result["power"] * result["efficiency"]:
        raise ValueError("Thermal losses must be below effective heater power")
    return result


def local_deadline(day, hour):
    """One daily Zurich deadline: first fold; spring gap -> first valid minute.

    Round trips through UTC distinguish real wall times from nonexistent ones.
    Choosing the first autumn occurrence prevents a second comfort event at the
    same displayed hour. All elapsed durations elsewhere are calculated in UTC.
    """
    wall = datetime.combine(day, hour)
    for shift in range(181):
        candidate_wall = wall + timedelta(minutes=shift)
        candidates = []
        for fold in (0, 1):
            candidate = candidate_wall.replace(tzinfo=ZURICH, fold=fold)
            instant = candidate.astimezone(UTC)
            if instant.astimezone(ZURICH).replace(tzinfo=None) == candidate_wall:
                candidates.append(instant)
        if candidates:
            return min(candidates)
    raise ValueError("Could not resolve Zurich deadline")


def _deadline(now, settings, temperature):
    today = local_deadline(now.astimezone(ZURICH).date(), settings["hour"])
    if now <= today:
        return today, False
    missed = temperature < settings["minimum"] and (
        now - today).total_seconds() <= settings["grace"] * 60
    if missed:
        return today, True
    tomorrow = now.astimezone(ZURICH).date() + timedelta(days=1)
    return local_deadline(tomorrow, settings["hour"]), False


def _forecast_energy(profile, now, deadline, maximum, enabled):
    if not enabled:
        return None, "OFF"
    if deadline <= now:
        return 0.0, "DEADLINE_PASSED"
    if not isinstance(profile, dict) or not isinstance(profile.get("points"), list):
        return None, "UNAVAILABLE"
    try:
        issued = _utc(profile.get("generated_at"))
        age = (now - issued).total_seconds()
        if age < -300 or age > 10800:
            return None, "STALE"
        # Exact intervals, continuous coverage and no duplicates are required.
        rows = []
        seen = set()
        for point in profile["points"]:
            if not isinstance(point, dict):
                raise ValueError("Invalid profile row")
            start = _utc(point.get("at"))
            if start in seen:
                raise ValueError("Duplicate forecast interval")
            seen.add(start)
            end = _utc(point["period_end"]) if point.get("period_end") else start + timedelta(hours=1)
            if not 0 < (end-start).total_seconds() <= 3600:
                raise ValueError("Invalid forecast duration")
            if end <= now or start >= deadline:
                continue
            value = point.get("heater_budget_w")
            if value is None:
                return None, "INCOMPLETE"
            watts = _number(value, "forecast budget", 0, 50000)
            rows.append((max(start, now), min(end, deadline), min(maximum, watts)))
        cursor = now
        energy = 0.0
        for start, end, watts in sorted(rows):
            if start != cursor:
                return None, "INCOMPLETE" if start > cursor else "INVALID"
            energy += watts * (end-start).total_seconds() / 3600000
            cursor = end
        if cursor != deadline:
            return None, "INCOMPLETE"
        return energy, "OK"
    except (ValueError, TypeError, KeyError, OverflowError):
        return None, "INVALID"


def plan_comfort(options, now, water_temp_c, profile=None):
    """Return a serializable, side-effect-free decision for the next daily need.

    Losses and efficiency are explicit uncalibrated assumptions. The safety
    margin advances the start but is not additional energy to be put into water.
    Grid demand stops at Tmin, independently of the optional surplus ceiling.
    A missed deadline is recovered for a bounded grace period, then the next
    daily deadline is used; this is not permanent network temperature holding.
    """
    instant = _utc(now)
    settings = _settings(options)
    temperature = _number(water_temp_c, "water temperature", -20, 110)
    deadline, missed = _deadline(instant, settings, temperature)
    remaining_h = max(0.0, (deadline-instant).total_seconds()/3600)
    sensible = max(0.0, settings["minimum"]-temperature) * settings["volume"] * WH_PER_LITRE_K / 1000
    # Existing heat above Tmin is useful storage against future losses.
    thermal_gap = max(0.0, (settings["minimum"]-temperature) * settings["volume"] * WH_PER_LITRE_K / 1000
                      + settings["loss"] * remaining_h / 1000)
    electrical_need = thermal_gap / settings["efficiency"]
    duration_h = electrical_need * 1000 / settings["power"]
    latest = deadline - timedelta(hours=duration_h, minutes=settings["margin"])
    physical_possible = electrical_need <= settings["power"] * remaining_h / 1000 + 1e-9
    solar, forecast_status = _forecast_energy(profile, instant, deadline, settings["power"], settings["weather"])
    backup_energy = None if solar is None else max(0.0, electrical_need-solar)
    solar_possible = None if solar is None else electrical_need <= solar + 1e-9
    possible = physical_possible if settings["enabled"] else (
        False if not physical_possible else solar_possible)
    due = sensible > 0 and instant >= latest
    request = 0.0
    # Limit the last sample's energy to exactly the measured sensible gap.
    if settings["enabled"] and due and temperature < settings["maximum"]:
        request = min(settings["power"], sensible * 3600000 /
                      (settings["step"] * settings["efficiency"]))

    if temperature >= settings["maximum"]:
        status, reason = "THERMAL_LIMIT", "Plafond utilisateur atteint : appoint simulé nul."
    elif sensible == 0:
        status, reason = "MINIMUM_REACHED", "Minimum actuel atteint : aucun appoint réseau."
    elif missed:
        status = "MISSED_DEADLINE" if settings["enabled"] else "MISSED_DISABLED"
        reason = "Échéance manquée ; rattrapage limité à la période de grâce." if settings["enabled"] else "Échéance manquée et appoint réseau interdit."
    elif not physical_possible:
        status = "INSUFFICIENT_TIME" if settings["enabled"] else "BACKUP_DISABLED"
        reason = "Puissance ou temps insuffisant : minimum non garanti à l’heure prévue."
    elif due:
        status = "BACKUP_DUE" if settings["enabled"] else "BACKUP_DISABLED"
        reason = "Dernier départ prudent atteint ; appoint simulé limité au minimum." if settings["enabled"] else "Dernier départ prudent atteint mais appoint réseau interdit."
    else:
        status, reason = "WAITING_SURPLUS", "Priorité au surplus mesuré jusqu’au dernier départ prudent."
    if not settings["enabled"] and solar_possible is False and sensible > 0:
        reason += " Surplus prévu insuffisant et appoint interdit : minimum non garanti."
    if solar is None:
        reason += " Prévision inutilisable ou désactivée : calcul prudent sans crédit solaire."
    return {
        "status": status, "reason": reason,
        "deadline": deadline.astimezone(ZURICH).isoformat(),
        "deadline_utc": deadline.isoformat(),
        "latest_start": latest.astimezone(ZURICH).isoformat(),
        "energy_needed_kwh": round(electrical_need, 6),
        "sensible_deficit_kwh": round(sensible, 6),
        "solar_before_deadline_kwh": None if solar is None else round(solar, 6),
        "backup_energy_kwh": None if backup_energy is None else round(backup_energy, 6),
        "fallback_backup_energy_kwh": round(electrical_need, 6) if solar is None else None,
        "backup_power_w": round(request, 6),
        "backup_power_basis": "TOTAL_SIMULATED_HEATER_DEMAND_NOT_GRID_MEASUREMENT",
        "forecast_status": forecast_status,
        "forecast_credit_for_latest_start_kwh": 0.0,
        "theoretical_possible": possible,
        "physical_capacity_sufficient": physical_possible,
        "forecast_energy_sufficient": solar_possible,
        "minimum_guaranteed": False,
        "missed_deadline": missed,
        "grid_backup_enabled": settings["enabled"],
        "minimum_c": settings["minimum"],
        "maximum_c": settings["maximum"],
        "model": {"volume_l": settings["volume"], "efficiency": settings["efficiency"],
                  "loss_w": settings["loss"], "margin_minutes": settings["margin"],
                  "grace_minutes": settings["grace"], "draws_modelled": False,
                  "calibrated": False},
    }


def project_comfort_profile(options, now, water_temp_c, profile, *, ceiling_c=None):
    """Overlay an estimated 48 h trajectory, never silently filling PV gaps.

    Five-minute energy integration uses the same planner and no hot-water draw
    model. Missing intervals end the temperature trajectory; unknown values stay
    null instead of turning into invented zero production. Output is a copy.
    """
    instant = _utc(now)
    settings = _settings(options)
    temperature = _number(water_temp_c, "water temperature", -20, 110)
    ceiling = settings["maximum"] if ceiling_c is None else _number(
        ceiling_c, "projection ceiling", settings["minimum"], settings["maximum"])
    result = deepcopy(profile)
    result["comfort_projection"] = {
        "model": "ESTIMATE_NO_DRAWS", "physical_output": False,
        "reason": "Simulation thermique sans puisages ni stratification ; valeurs inconnues laissées vides.",
    }
    capacity = settings["volume"] * WH_PER_LITRE_K
    cursor = instant
    valid = True
    for point in result.get("points", []):
        point.update(planned_boiler_w=None, projected_water_c=None,
                     planned_grid_backup_w=None, target_c=ceiling)
        try:
            start = _utc(point.get("at"))
            end = _utc(point["period_end"]) if point.get("period_end") else start+timedelta(hours=1)
            if end <= instant:
                continue
            start = max(start, instant)
            if start != cursor or end <= start:
                valid = False
            solar = _number(point.get("heater_budget_w"), "projection surplus", 0, 50000)
        except (TypeError, ValueError, KeyError):
            valid = False
        if not valid:
            continue
        watts_seconds = 0.0
        grid_seconds = 0.0
        while cursor < end:
            step_end = min(end, cursor+timedelta(minutes=5))
            seconds = (step_end-cursor).total_seconds()
            # Forecast freshness is not artificially renewed in this forward
            # model. Only actual thermal deficit determines the prudent start.
            step_options = {**options, "sample_seconds": seconds}
            plan = plan_comfort(step_options, cursor, temperature, None)
            available_solar = min(settings["power"], solar)
            optional = min(available_solar, max(0, (ceiling-temperature)*capacity)
                           *3600/(seconds*settings["efficiency"]))
            total = max(optional, plan["backup_power_w"])
            total = min(total, max(0, (settings["maximum"]-temperature)*capacity)
                        *3600/(seconds*settings["efficiency"]))
            grid = max(0.0, total-available_solar)
            # Only the comfort part may import. This also catches integration
            # mistakes without requiring a hardware driver or network service.
            if not settings["enabled"]:
                grid = 0.0
            temperature = max(0.0, temperature + (total*settings["efficiency"]-settings["loss"]) * seconds/(3600*capacity))
            watts_seconds += total*seconds
            grid_seconds += grid*seconds
            cursor = step_end
        elapsed = (end-start).total_seconds()
        point.update(planned_boiler_w=round(watts_seconds/elapsed, 2),
                     planned_grid_backup_w=round(grid_seconds/elapsed, 2),
                     projected_water_c=round(temperature, 3))
    return result
