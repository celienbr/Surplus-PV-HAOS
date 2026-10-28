"""Read Forecast.Solar's standard Home Assistant sensors (NO writes/API actions).

Raw Forecast.Solar kWh represent gross PV production, not usable export.
Assumed house and future battery shares are SUBTRACTED explicitly before
calling the advisory temperature algorithm. All outputs are demonstrative.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from math import isfinite

from .adaptive_temperature import SurplusOutlook, adaptive_temperature
from .pac_estimate import forecast_pac_kwh
from .open_meteo_weather import read_tomorrow_from_ha


@dataclass(frozen=True)
class ForecastReading:
    value_kwh: float
    updated_at: datetime


def forecast_kwh(state: dict, now: datetime, max_age_hours: float = 3.0) -> ForecastReading:
    raw = state.get("state")
    if raw in (None, "unknown", "unavailable", ""):
        raise ValueError("Forecast.Solar sensor unavailable or not installed")
    try:
        value = float(raw)
    except (TypeError, ValueError) as error:
        raise ValueError("Forecast.Solar energy not numeric") from error
    if not isfinite(value) or not 0 <= value <= 200:
        raise ValueError("Forecast.Solar energy outside allowed range")
    unit = state.get("attributes", {}).get("unit_of_measurement")
    if unit != "kWh":
        raise ValueError(f"Forecast.Solar expected kWh, got {unit!r}")
    # Prefer source state update; last_reported may simply reflect an
    # identical re-publication. Neither proves the remote forecast age.
    raw_time = state.get("last_updated")
    if not raw_time:
        raise ValueError("Forecast.Solar sensor update time absent")
    try:
        timestamp = datetime.fromisoformat(raw_time.replace("Z", "+00:00"))
    except (TypeError, ValueError, AttributeError) as error:
        raise ValueError("Invalid forecast update timestamp") from error
    if timestamp.tzinfo is None:
        raise ValueError("Forecast.Solar update must carry timezone")
    age = (now - timestamp).total_seconds() / 3600
    if not -5/60 <= age <= max_age_hours:
        raise ValueError(f"Forecast.Solar stale ({age:.1f} h)")
    return ForecastReading(value, timestamp.astimezone(timezone.utc))



def outdoor_forecast_c(state: dict, now: datetime, max_age_hours: float = 3.0) -> float:
    """Read a forecast-derived Home Assistant sensor for tomorrow.

    This MUST be a forecast temperature, not current outside temperature.
    Values without a plausible timestamp and °C unit are rejected.
    """
    raw = state.get("state")
    if raw in (None, "", "unknown", "unavailable"):
        raise ValueError("Tomorrow outdoor forecast temperature is unavailable")
    try:
        temperature = float(raw)
    except (TypeError, ValueError) as error:
        raise ValueError("Invalid tomorrow outdoor temperature forecast") from error
    if not isfinite(temperature) or not -35 <= temperature <= 50:
        raise ValueError("Tomorrow forecast temperature outside limits")
    if state.get("attributes", {}).get("unit_of_measurement") != "°C":
        raise ValueError("Tomorrow outdoor temperature must use °C")
    raw_ts = state.get("last_reported") or state.get("last_updated")
    if not raw_ts:
        raise ValueError("Missing tomorrow outdoor temperature forecast timestamp")
    try:
        timestamp = datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
    except (ValueError, TypeError, AttributeError) as error:
        raise ValueError("Invalid tomorrow temperature timestamp") from error
    if timestamp.tzinfo is None:
        raise ValueError("Tomorrow temperature timestamp must carry timezone")
    age = (now - timestamp).total_seconds() / 3600
    if not -5/60 <= age <= max_age_hours:
        raise ValueError("Tomorrow outdoor temperature forecast is stale")
    return temperature

def build_weather_preview(options: dict, now: datetime, client,
                          enabled: bool) -> dict:
    """Separate Solar PV and PAC weather diagnostics, preserving measured PV.

    The PAC estimate can only reduce estimated future PV availability;
    if PAC weather fails, do NOT pretend there is extra usable surplus.
    """
    data = {
        "enabled": enabled,
        "source": "Home Assistant Forecast.Solar and optional Open-Meteo",
        "status": "OFF" if not enabled else "UNAVAILABLE",
        "error": None,
        "pv_status": "OFF" if not enabled else "WAITING",
        "pv_error": None,
        "pac_error": None,
        "gross_today_remaining_kwh": None,
        "gross_tomorrow_kwh": None,
        "pac_enabled": options["pac_enabled"],
        "pac_outdoor_tomorrow_c": None,
        "pac_electric_tomorrow_kwh": None,
        "pac_cop_assumed": None,
        "pac_load_factor": None,
        "pac_forecast_method": None,
        "pac_forecast_date": None,
        "pac_forecast_samples": None,
        "pac_model_status": "OFF" if not options["pac_enabled"] else "WAITING",
        "usable_today_assumed_kwh": None,
        "usable_tomorrow_assumed_kwh": None,
        "target_c": options["demo_max_c"],
        "manual_max_c": options["demo_max_c"],
        "minimum_c": options["weather_min_c"],
        "decision": "Météo désactivée : limite haute manuelle.",
        "reserve_for_other_uses_w": options["weather_reserve_w"],
        "temperature_is_real": False,
        "heat_outputs_enabled": False,
        "forecast_timestamp": None,
    }
    if not enabled:
        return data
    if client is None:
        data.update(status="UNAVAILABLE", pv_status="UNAVAILABLE",
                    pv_error="Accès Home Assistant indisponible",
                    error="Accès Home Assistant indisponible",
                    decision="Lecture météo impossible : application non connectée.")
        return data

    # Isolate PV fetch from Open-Meteo: never hide valid solar production
    # solely because a separate temperature entity is unavailable.
    try:
        try:
            today_state = client.get_state(options["forecast_today_entity"])
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise ValueError(
                "Forecast.Solar aujourd'hui [" +
                options["forecast_today_entity"] + "]: " + str(error)) from error
        try:
            tomorrow_state = client.get_state(options["forecast_tomorrow_entity"])
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise ValueError(
                "Forecast.Solar demain [" +
                options["forecast_tomorrow_entity"] + "]: " + str(error)) from error
        today = forecast_kwh(today_state, now)
        tomorrow = forecast_kwh(tomorrow_state, now)
        today_use = max(0.0, today.value_kwh - options["house_today_kwh"] -
                        options["battery_today_kwh"])
        data.update({
            "pv_status": "OK",
            "gross_today_remaining_kwh": round(today.value_kwh, 2),
            "gross_tomorrow_kwh": round(tomorrow.value_kwh, 2),
            "usable_today_assumed_kwh": round(today_use, 2),
            "forecast_timestamp": min(today.updated_at,
                                      tomorrow.updated_at).isoformat(),
        })
    except (ValueError, KeyError, OSError, TimeoutError, TypeError) as error:
        data.update({
            "status": "UNAVAILABLE",
            "pv_status": "UNAVAILABLE",
            "pv_error": str(error)[:240],
            "error": str(error)[:240],
            "pac_model_status": "NOT_EVALUATED" if options["pac_enabled"] else "OFF",
            "decision": "Prévisions Forecast.Solar indisponibles : "
                        "vérifier les entités PV. Repli sur le plafond manuel fictif.",
        })
        return data

    # An explicit PAC opt-out excludes that model; it is not a measurement
    # claiming that the physical heat pump consumes zero. Missing data while
    # the model is enabled remains an unavailable forecast below.
    data["pac_excluded_by_user"] = not options["pac_enabled"]

    pac_kwh = 0.0
    pac_estimate = None
    pac_weather = None
    if options["pac_enabled"]:
        try:
            if options["pac_temperature_source"] == "open_meteo":
                try:
                    pac_weather = read_tomorrow_from_ha(
                        client, options["pac_weather_entity"], now,
                        options["pac_timezone"])
                except (ValueError, OSError, KeyError, TypeError, TimeoutError) as error:
                    raise ValueError(
                        "Open-Meteo [" + options["pac_weather_entity"] +
                        "]: " + str(error)) from error
                tomorrow_c = pac_weather.temperature_c
            else:
                try:
                    state = client.get_state(options["pac_forecast_temp_entity"])
                except (OSError, ValueError, KeyError, TypeError) as error:
                    raise ValueError(
                        "Température PAC [" + options["pac_forecast_temp_entity"] +
                        "]: " + str(error)) from error
                tomorrow_c = outdoor_forecast_c(state, now)
            pac_estimate = forecast_pac_kwh(
                outside_c=tomorrow_c,
                reference_electric_kwh=options["pac_reference_electric_kwh"],
                reference_temp_c=options["pac_reference_temp_c"],
                balance_temp_c=options["pac_balance_temp_c"],
                reference_cop=options["pac_reference_cop"],
                cop_change_per_c=options["pac_cop_change_per_c"])
            pac_kwh = pac_estimate.energy_kwh
        except (ValueError, OSError, KeyError, TypeError, TimeoutError) as error:
            message = str(error)[:240]
            data.update({
                "status": "PAC_UNAVAILABLE",
                "pac_model_status": "UNAVAILABLE",
                "pac_error": message,
                "error": message,
                "decision": "Forecast.Solar fonctionne. Température PAC non "
                            "disponible : le surplus utile de demain est "
                            "INCONNU (non remplacé par la production brute). "
                            "Aucune optimisation PAC fictive : plafond manuel.",
            })
            return data

    tomorrow_use = max(0.0, tomorrow.value_kwh - options["house_tomorrow_kwh"] -
                       options["battery_tomorrow_kwh"] - pac_kwh)
    outlook = SurplusOutlook(
        updated_at=min(today.updated_at, tomorrow.updated_at),
        remaining_today_kwh=today_use,
        tomorrow_kwh=tomorrow_use,
    )
    advice = adaptive_temperature(
        now=now,
        water_temperature_c=options["demo_water_c"],
        minimum_c=options["weather_min_c"],
        high_limit_c=options["demo_max_c"],
        forecast_enabled=True,
        outlook=outlook,
        volume_l=options["weather_volume_l"],
        comfort_buffer_c=options["weather_buffer_c"],
        estimated_demand_tomorrow_kwh=options["weather_heat_demand_kwh"],
        reserve_other_uses_w=options["weather_reserve_w"],
        base_export_margin_w=options["target_export_w"],
    )
    if advice.state != "ADAPTIVE":
        data.update({
            "status": "UNAVAILABLE",
            "error": advice.explanation,
            "decision": advice.explanation + (
            " PAC exclue du calcul à la demande de l’utilisateur (hypothèse, pas mesure)."
            if not options["pac_enabled"] else ""),
            "pac_model_status": "ESTIMATED" if pac_estimate else "OFF",
        })
        return data
    data.update({
        "status": "ADAPTIVE",
        "gross_today_remaining_kwh": round(today.value_kwh, 2),
        "gross_tomorrow_kwh": round(tomorrow.value_kwh, 2),
        "usable_today_assumed_kwh": round(today_use, 2),
        "usable_tomorrow_assumed_kwh": round(tomorrow_use, 2),
        "target_c": advice.target_c,
        "decision": advice.explanation + (
            " PAC exclue du calcul à la demande de l’utilisateur (hypothèse, pas mesure)."
            if not options["pac_enabled"] else ""),
        "reserve_for_other_uses_w": advice.reserved_export_w,
        "forecast_timestamp": outlook.updated_at.isoformat(),
        "pac_model_status": "ESTIMATED" if pac_estimate else "OFF",
        "pac_outdoor_tomorrow_c": (pac_estimate.temperature_c
                                  if pac_estimate else None),
        "pac_electric_tomorrow_kwh": (pac_estimate.energy_kwh
                                     if pac_estimate else 0.0),
        "pac_cop_assumed": (pac_estimate.cop_assumed
                            if pac_estimate else None),
        "pac_load_factor": (round(pac_estimate.load_factor, 2)
                            if pac_estimate else None),
        "pac_forecast_method": (pac_weather.method if pac_weather else
                                "EXTERNAL_SENSOR" if pac_estimate else None),
        "pac_forecast_date": pac_weather.forecast_date if pac_weather else None,
        "pac_forecast_samples": pac_weather.samples if pac_weather else None,
    })
    return data

