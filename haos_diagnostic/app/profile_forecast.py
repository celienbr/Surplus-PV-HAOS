"""Forty-eight UTC hours of labelled LOCAL solar / PAC forecast (simulation).

Do not generate false solar curves from daily kWh totals. HA Forecast.Solar
hourly service is available only with its config-entry ID. A missing ID or
unavailable service produces gaps and an explicit error, never invented watts.
No output controls or Home Assistant state changes are performed.
"""
from datetime import datetime, timedelta, timezone
from math import isfinite
from zoneinfo import ZoneInfo
from .pac_estimate import forecast_pac_kwh
from .forecast_adapter import forecast_kwh

def _valid_power(value):
    if isinstance(value, bool):
        raise ValueError("Boolean is not solar power")
    try:
        n = float(value)
    except (TypeError, ValueError):
        raise ValueError("Invalid hourly power") from None
    if not isfinite(n) or n < 0 or n > 50000:
        raise ValueError("Solar forecast power out of range")
    return n

def _instant(value):
    if not isinstance(value, str):
        raise ValueError("Expected an ISO timestamp")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Hour must contain a time zone")
    return result.astimezone(timezone.utc)

def hourly_solar_energy(readings: dict, hours: list[datetime]) -> tuple[list[dict], str]:
    """Normalize HA's *hourly* response using interval energy, never time-flooring.

    The client requests ``resolution: hourly``. Each key starts a one-hour
    interval; ``wh_period`` is its energy and ``watts`` its average power.
    Prefer the explicit energy series. If it is present but incomplete, keep
    holes rather than patching it with another series. Half-hour-offset periods
    are integrated over their actual overlap; overlapping periods are rejected.
    https://www.home-assistant.io/actions/forecast_solar.get_forecast/
    """
    if not isinstance(readings, dict):
        raise ValueError("Invalid Forecast.Solar response")
    energy = readings.get("wh_period")
    if energy is not None and not isinstance(energy, dict):
        raise ValueError("Invalid Forecast.Solar wh_period mapping")
    use_energy = bool(energy)
    series = energy if use_energy else readings.get("watts")
    if not isinstance(series, dict) or not series:
        raise ValueError("Réponse Forecast.Solar sans série horaire")
    periods = sorted((_instant(stamp), _valid_power(value))
                     for stamp, value in series.items())
    for (previous, _), (following, _) in zip(periods, periods[1:]):
        if following < previous + timedelta(hours=1):
            raise ValueError("Périodes Forecast.Solar horaires superposées")
    normalized = []
    for start in hours:
        end = start + timedelta(hours=1)
        covered = 0.0
        wh = 0.0
        for at, value in periods:
            seconds = (min(end, at + timedelta(hours=1)) - max(start, at)).total_seconds()
            if seconds > 0:
                covered += seconds
                # For one hour, Wh numerically equals the average W.
                wh += value * seconds / 3600
        complete = abs(covered - 3600) < 0.001
        normalized.append({"pv_w": wh if complete else None,
                           "pv_wh": wh if complete else None,
                           "solar_coverage_seconds": round(covered, 3)})
    return normalized, "wh_period" if use_energy else "watts_hourly_average"


def _local_day_bounds(day, zone):
    following = day + timedelta(days=1)
    start = datetime(day.year, day.month, day.day, tzinfo=zone).astimezone(timezone.utc)
    end = datetime(following.year, following.month, following.day,
                   tzinfo=zone).astimezone(timezone.utc)
    return start, end


def read_current_temperature_c(client, source: str) -> float:
    """Read the current HA weather attribute, independently of forecasts."""
    if client is None:
        raise ValueError("Home Assistant indisponible")
    state = client.get_state(source)
    if not isinstance(state, dict) or state.get("state") in (None, "unavailable", "unknown"):
        raise ValueError("Entité météo actuelle indisponible")
    attrs = state.get("attributes", {})
    if attrs.get("temperature_unit") != "°C":
        raise ValueError("Unité météo actuelle différente de °C")
    current_temperature = float(attrs.get("temperature"))
    if not isfinite(current_temperature) or not -35 <= current_temperature <= 50:
        raise ValueError("Température actuelle absente ou invalide")
    return round(current_temperature, 1)


def make_hourly_profile(options: dict, now: datetime, client) -> dict:
    """Return independent 48-hour series and explicit availability metadata."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Forecast time must carry a timezone")
    now = now.astimezone(timezone.utc)
    zone = ZoneInfo(options["pac_timezone"])
    current = now.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
    hours = [current + timedelta(hours=n) for n in range(48)]
    local = [d.astimezone(zone) for d in hours]
    source = options.get("pac_weather_entity", "weather.maison")
    result = {
        "status": "UNAVAILABLE",
        "start": current.isoformat(),
        "generated_at": now.isoformat(),
        "forecast_retrieved_at": now.isoformat(),
        "forecast_source_generated_at": None,
        "forecast_freshness_basis": "HA cached response; remote generation time not supplied",
        "solar_energy_source": None,
        "load_assumptions": {
            "house_today_kwh": "remaining from retrieval to local midnight; excludes PAC and boiler",
            "battery_today_kwh": "remaining reservation, not an installed battery measurement",
            "tomorrow_budgets": "spread over actual local day; reused for horizon beyond tomorrow",
            "pac_w": "24-hour-equivalent model rate integrated over actual elapsed hours",
        },
        "timezone": options["pac_timezone"],
        "solar_status": "NOT_CONFIGURED",
        "daily_pv_status": "UNAVAILABLE",
        "daily_pv_error": None,
        "daily_today_remaining_kwh": None,
        "daily_tomorrow_kwh": None,
        "tomorrow_temperature_c": None,
        "current_temperature_c": None,
        "current_temperature_error": None,
        "pac_today_remaining_kwh": None,
        "solar_error": None,
        "meteo_status": "UNAVAILABLE",
        "meteo_error": None,
        "pac_status": "OFF" if not options["pac_enabled"] else "UNAVAILABLE",
        "pac_error": None,
        "data_quality": "Prévisions brutes et hypothèses, pas un programme de chauffe.",
        "points": [{
            "at": t.isoformat(),
            "period_end": (t + timedelta(hours=1)).isoformat(),
            "local": stamp.isoformat(),
            "label": stamp.strftime("%d/%m %Hh"),
            "pv_w": None, "pv_wh": None, "solar_coverage_seconds": 0,
            "temperature_c": None,
            "pac_w": None, "house_w": None,
            "surplus_w": None, "heater_budget_w": None,
            "target_c": None,
        } for t, stamp in zip(hours, local)],
    }
    if client is None:
        result["solar_error"] = "Home Assistant indisponible"
        result["meteo_error"] = "Home Assistant indisponible"
        return result
    # Current reading must not be confused with tomorrow's forecast.
    try:
        result["current_temperature_c"] = read_current_temperature_c(client, source)
    except (OSError, ValueError, TypeError, KeyError, AttributeError, TimeoutError) as err:
        result["current_temperature_error"] = str(err)[:180]
    # Daily PV sensors are independent of optimisation and hourly setup.
    try:
        today = forecast_kwh(client.get_state(options["forecast_today_entity"]), now)
        tomorrow = forecast_kwh(client.get_state(options["forecast_tomorrow_entity"]), now)
        result["daily_pv_status"] = "OK"
        result["daily_today_remaining_kwh"] = round(today.value_kwh, 2)
        result["daily_tomorrow_kwh"] = round(tomorrow.value_kwh, 2)
    except (OSError, ValueError, KeyError, TypeError, TimeoutError, AttributeError) as error:
        result["daily_pv_error"] = str(error)[:200]
    solar_id = options.get("forecast_solar_entry_id", "").strip()
    if solar_id:
        try:
            if not hasattr(client, "get_solar_forecast"):
                raise ValueError("Client Forecast.Solar horaire non disponible")
            readings = client.get_solar_forecast(solar_id)
            normalized, energy_source = hourly_solar_energy(readings, hours)
            result["solar_energy_source"] = energy_source
            hits = 0
            for point, values in zip(result["points"], normalized):
                point.update(values)
                if values["pv_w"] is not None:
                    hits += 1
            if not hits:
                raise ValueError("Prévision PV horaire absente des 48 h à venir")
            result["solar_status"] = "PARTIAL" if hits < 48 else "OK"
        except (OSError, ValueError, KeyError, TypeError, TimeoutError, AttributeError) as err:
            result["solar_status"] = "UNAVAILABLE"
            result["solar_error"] = str(err)[:220]
    else:
        result["solar_error"] = (
            "Courbe PV horaire non configurée : ouvrir la configuration de "
            "l’application Home Assistant et renseigner "
            "forecast_solar_entry_id. Les totaux journaliers ne suffisent "
            "pas à reconstituer une courbe de puissance fiable."
        )
    try:
        rows = client.get_weather_forecast(source, "hourly")
        if not isinstance(rows, list):
            raise ValueError("Prévisions Open-Meteo non disponibles")
        by_hour = {}
        for item in rows[:240]:
            if not isinstance(item, dict):
                continue
            try:
                dt = _instant(item.get("datetime")).replace(minute=0, second=0, microsecond=0)
                temp = float(item.get("temperature"))
                if not isfinite(temp) or not -35 <= temp <= 50:
                    raise ValueError("Température hors limite")
            except (ValueError, TypeError):
                continue
            if dt not in by_hour:
                by_hour[dt] = round(temp, 1)
        hits = 0
        for i, at in enumerate(hours):
            if at in by_hour:
                result["points"][i]["temperature_c"] = by_hour[at]
                hits += 1
        if not hits:
            raise ValueError("Aucun point Open-Meteo horaire sur l'horizon")
        result["meteo_status"] = "OK" if hits == 48 else "PARTIAL"
        tomorrow_date = (now.astimezone(zone).date() + timedelta(days=1)).isoformat()
        tomorrow_temps = [p["temperature_c"] for p in result["points"]
                          if p["local"][:10] == tomorrow_date
                          and p["temperature_c"] is not None]
        if len(tomorrow_temps) >= 16:
            result["tomorrow_temperature_c"] = round(
                sum(tomorrow_temps)/len(tomorrow_temps), 1)
    except (OSError, ValueError, KeyError, TypeError, TimeoutError, AttributeError) as err:
        result["meteo_error"] = "Open-Meteo [" + source + "] : " + str(err)[:185]

    max_power = options["max_power_w"]
    reserve = options["target_export_w"] + (
        options["weather_reserve_w"] if options["weather_enabled"] else 0)
    today_date = now.astimezone(zone).date()
    for p in result["points"]:
        t = p["temperature_c"]
        pv = p["pv_w"]
        # Today's inputs are REMAINING energy, not full-day totals. Future
        # budgets are daily estimates; use the real 23/24/25-hour local day.
        day = _instant(p["at"]).astimezone(zone).date()
        day_start, day_end = _local_day_bounds(day, zone)
        today = day == today_date
        suffix = "today_kwh" if today else "tomorrow_kwh"
        daily_house = options["house_" + suffix]
        daily_battery = options["battery_" + suffix]
        seconds = (day_end - (now if today else day_start)).total_seconds()
        home_w = (daily_house + daily_battery) * 3_600_000 / seconds
        p["house_w"] = home_w
        p["load_budget_scope"] = "remaining_today" if today else "daily_assumption"
        if not options["pac_enabled"]:
            # Explicit opt-out: exclude PAC from this planning model. This is
            # an assumption selected by the user, never a measured zero load.
            p["pac_w"] = 0.0
        if options["pac_enabled"] and t is not None:
            estimate = forecast_pac_kwh(
                outside_c=t,
                reference_electric_kwh=options["pac_reference_electric_kwh"],
                reference_temp_c=options["pac_reference_temp_c"],
                balance_temp_c=options["pac_balance_temp_c"],
                reference_cop=options["pac_reference_cop"],
                cop_change_per_c=options["pac_cop_change_per_c"])
            p["pac_w"] = estimate.energy_kwh * 1000 / 24
        # A requested PAC model with missing weather blocks surplus; an
        # explicit opt-out excludes that model, documented as an assumption.
        if pv is not None and p["pac_w"] is not None:
            usable = max(0, pv - home_w - p["pac_w"] - reserve)
            p["surplus_w"] = usable
            p["heater_budget_w"] = min(max_power, usable)
    # Energy forecast from now to next local midnight, with a prorated first
    # hour and 23/25-hour Swiss days. Never present missing data as zero.
    if options["pac_enabled"]:
        tomorrow = now.astimezone(zone).date() + timedelta(days=1)
        local_end = datetime(tomorrow.year, tomorrow.month, tomorrow.day,
                             tzinfo=zone).astimezone(timezone.utc)
        total_wh = 0.0
        covered_seconds = 0.0
        complete = True
        for point in result["points"]:
            hour_start = _instant(point["at"])
            seconds = (min(hour_start + timedelta(hours=1), local_end) -
                       max(hour_start, now)).total_seconds()
            if seconds <= 0:
                continue
            watts = point["pac_w"]
            if watts is None or not isfinite(watts):
                complete = False
                break
            covered_seconds += seconds
            total_wh += watts * seconds / 3600
        if complete and covered_seconds > 0:
            result["pac_today_remaining_kwh"] = round(total_wh / 1000, 2)
    if not options["pac_enabled"]:
        result["pac_status"] = "OFF"
    elif any(p["pac_w"] is not None for p in result["points"]):
        result["pac_status"] = "ESTIMATED"
    else:
        result["pac_error"] = "Prévisions températures insuffisantes pour le modèle PAC"
    relevant = [result["solar_status"]]
    if options["pac_enabled"]:
        relevant.append(result["meteo_status"])
    if all(status in ("OK", "PARTIAL") for status in relevant):
        result["status"] = "PARTIAL" if "PARTIAL" in relevant else "OK"
    return result
