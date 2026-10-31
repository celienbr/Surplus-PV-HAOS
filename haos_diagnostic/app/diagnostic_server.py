"""Safe read-only Home Assistant OS diagnostic with ingress monitoring.

This program CANNOT access GPIO, I2C or Home Assistant write APIs. All boiler
power figures are hypothetical, calculated by the genuine project regulator
against live gPlugK readings + an entirely virtual boiler load.
"""
from __future__ import annotations

from collections import deque
from datetime import datetime, timezone, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock, Thread, Event
import json
import logging
import os
import secrets
import sqlite3
import time

from .controller import Parameters, SurplusRegulator, Decision
from .user_settings import USER_FIELDS, read_settings, save_settings
from .comfort_planner import plan_comfort, project_comfort_profile
from .ha_client import HomeAssistantClient
from .gplugk import _read_kw_state, net_grid_power_w, assess_grid_reading
from .forecast_adapter import build_weather_preview
from .profile_forecast import make_hourly_profile, read_current_temperature_c
from .history_store import HistoryStore, parse_time
from .energy_dashboard import EnergyDashboard, live_grid
from .pv_actual import PVAcquisition

LOG = logging.getLogger("surplus_pv_diagnostic")
INDEX = Path(__file__).resolve().parent.parent / "web" / "index.html"
MOBILE_INPUTS = Path(__file__).resolve().parent.parent / "web" / "inputs.html"
PROFILE_VIEW = Path(__file__).resolve().parent.parent / "web" / "profile.html"
BOILER_VIEW = Path(__file__).resolve().parent.parent / "web" / "boiler.html"
THEME_CSS = Path(__file__).resolve().parent.parent / "web" / "theme.css"
THEME_JS = Path(__file__).resolve().parent.parent / "web" / "theme.js"
HISTORY_JS = Path(__file__).resolve().parent.parent / "web" / "history.js"


def parse_options(source: dict) -> dict:
    # Only the two entities verified with the user are accepted.
    if source.get("pi_entity", "sensor.gplugk_z_pi") != "sensor.gplugk_z_pi":
        raise ValueError("Import entity must be sensor.gplugk_z_pi")
    if source.get("po_entity", "sensor.gplugk_z_po") != "sensor.gplugk_z_po":
        raise ValueError("Export entity must be sensor.gplugk_z_po")
    options = {
        "pi_entity": "sensor.gplugk_z_pi",
        "po_entity": "sensor.gplugk_z_po",
        "sample_seconds": int(source.get("sample_seconds", 10)),
        "max_age_seconds": int(source.get("max_age_seconds", 60)),
        "demo_water_c": float(source.get("demo_water_c", 45)),
        "demo_max_c": float(source.get("demo_max_c", 70)),
        "target_export_w": float(source.get("target_export_w", 100)),
        "max_power_w": float(source.get("max_power_w", 6000)),
        "weather_enabled": source.get("weather_enabled", False),
        "comfort_time": source.get("comfort_time", "06:00"),
        "grid_backup_enabled": source.get("grid_backup_enabled", False),
        "boiler_temperature_entity": source.get("boiler_temperature_entity", ""),
        "comfort_efficiency": float(source.get("comfort_efficiency", 0.95)),
        "comfort_loss_w": float(source.get("comfort_loss_w", 100)),
        "comfort_margin_minutes": float(source.get("comfort_margin_minutes", 20)),
        "comfort_grace_minutes": float(source.get("comfort_grace_minutes", 60)),
        "forecast_today_entity": source.get("forecast_today_entity", "sensor.energy_production_today_remaining"),
        "forecast_tomorrow_entity": source.get("forecast_tomorrow_entity", "sensor.energy_production_tomorrow"),
        "forecast_solar_entry_id": source.get("forecast_solar_entry_id", ""),
        "weather_min_c": float(source.get("weather_min_c", 50)),
        "weather_buffer_c": float(source.get("weather_buffer_c", 5)),
        "weather_volume_l": float(source.get("weather_volume_l", 300)),
        "weather_heat_demand_kwh": float(source.get("weather_heat_demand_kwh", 5)),
        "house_today_kwh": float(source.get("house_today_kwh", 2)),
        "house_tomorrow_kwh": float(source.get("house_tomorrow_kwh", 5)),
        "battery_today_kwh": float(source.get("battery_today_kwh", 0)),
        "battery_tomorrow_kwh": float(source.get("battery_tomorrow_kwh", 0)),
        "weather_reserve_w": float(source.get("weather_reserve_w", 0)),
        "pac_enabled": source.get("pac_enabled", False),
        "pac_temperature_source": source.get("pac_temperature_source", "open_meteo"),
        "pac_weather_entity": source.get("pac_weather_entity", "weather.maison"),
        "pac_timezone": source.get("pac_timezone", "Europe/Zurich"),
        "pac_forecast_temp_entity": source.get(
            "pac_forecast_temp_entity", "sensor.surplus_pv_temperature_demain"),
        "pac_reference_electric_kwh": float(source.get("pac_reference_electric_kwh", 8)),
        "pac_reference_temp_c": float(source.get("pac_reference_temp_c", 7)),
        "pac_balance_temp_c": float(source.get("pac_balance_temp_c", 16)),
        "pac_reference_cop": float(source.get("pac_reference_cop", 3.5)),
        "pac_cop_change_per_c": float(source.get("pac_cop_change_per_c", 0.1)),
    }
    # Automatic compatibility for this installation's obsolete HA options:
    # previous packages saved weather.home; the user confirmed weather.maison.
    options["weather_entity_migrated"] = options["pac_weather_entity"] == "weather.home"
    if options["weather_entity_migrated"]:
        options["pac_weather_entity"] = "weather.maison"
    entry = options["forecast_solar_entry_id"]
    if not isinstance(entry, str) or (entry and (
            not 8 <= len(entry) <= 64 or
            not all(ch.isalnum() or ch in "_-" for ch in entry))):
        raise ValueError("Invalid Forecast.Solar configuration-entry ID")
    if not 5 <= options["sample_seconds"] <= 60:
        raise ValueError("Invalid sample period")
    if not 10 <= options["max_age_seconds"] <= 300:
        raise ValueError("Invalid max age")
    if not 0 <= options["demo_water_c"] <= 90:
        raise ValueError("Invalid demonstration temperature")
    if not 40 <= options["demo_max_c"] <= 80:
        raise ValueError("Invalid demonstration maximum")
    if not 0 <= options["target_export_w"] <= 2000:
        raise ValueError("Invalid target export")
    if not 100 <= options["max_power_w"] <= 6000:
        raise ValueError("Invalid boiler power")
    # Params validates values including NaN/Infinity.
    Parameters(
        max_power_w=options["max_power_w"],
        target_export_w=options["target_export_w"],
        temp_limit_c=options["demo_max_c"],
    )
    if options["demo_water_c"] < 0:
        raise ValueError("Invalid demonstration temperature")
    if not isinstance(options["pac_enabled"], bool):
        raise ValueError("pac_enabled must be a boolean")
    if not isinstance(options["weather_enabled"], bool):
        raise ValueError("weather_enabled must be a boolean")
    if not isinstance(options["grid_backup_enabled"], bool):
        raise ValueError("grid_backup_enabled must be a boolean")
    entity = options["boiler_temperature_entity"]
    if not isinstance(entity, str) or (entity and (not entity.startswith("sensor.")
            or not all(c.isalnum() or c in "._" for c in entity))):
        raise ValueError("Invalid boiler temperature sensor ID")
    comfort = options["comfort_time"]
    if not isinstance(comfort, str) or len(comfort) != 5 or comfort[2] != ":" or not (
            comfort[:2].isdigit() and comfort[3:].isdigit() and
            0 <= int(comfort[:2]) < 24 and 0 <= int(comfort[3:]) < 60):
        raise ValueError("Comfort hour must be HH:MM (Europe/Zurich)")
    for k in ("forecast_today_entity", "forecast_tomorrow_entity",
              "pac_forecast_temp_entity"):
        entity = options[k]
        if not isinstance(entity, str) or not entity.startswith("sensor.") or not all(
                char.isalnum() or char in "._" for char in entity):
            raise ValueError("Invalid weather sensor ID: " + k)
    if options["pac_temperature_source"] not in ("open_meteo", "sensor"):
        raise ValueError("PAC temperature source must be open_meteo or sensor")
    if not isinstance(options["pac_weather_entity"], str) or not (
            options["pac_weather_entity"].startswith("weather.") and
            all(ch.isalnum() or ch in "._" for ch in options["pac_weather_entity"])):
        raise ValueError("PAC weather entity must be weather.*")
    if not isinstance(options["pac_timezone"], str) or len(options["pac_timezone"]) > 80:
        raise ValueError("Invalid PAC timezone name")
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
    try:
        ZoneInfo(options["pac_timezone"])
    except (ZoneInfoNotFoundError, ValueError, KeyError) as error:
        raise ValueError("PAC timezone not supported") from error
    if not 30 <= options["weather_min_c"] <= options["demo_max_c"]:
        raise ValueError("Minimum must not exceed maximum")
    limits = {
        "comfort_efficiency": (0.5, 1),
        "comfort_loss_w": (0, 1000),
        "comfort_margin_minutes": (0, 120),
        "comfort_grace_minutes": (0, 120),
        "weather_buffer_c": (0, 20),
        "weather_volume_l": (1, 2000),
        "weather_heat_demand_kwh": (0, 30),
        "house_today_kwh": (0, 50),
        "house_tomorrow_kwh": (0, 50),
        "battery_today_kwh": (0, 50),
        "battery_tomorrow_kwh": (0, 50),
        "weather_reserve_w": (0, 3000),
        "pac_reference_electric_kwh": (0, 70),
        "pac_reference_temp_c": (-15, 15),
        "pac_balance_temp_c": (0, 25),
        "pac_reference_cop": (1.1, 8),
        "pac_cop_change_per_c": (0, 0.3),
    }
    from math import isfinite
    for key, (min_value, max_value) in limits.items():
        value = options[key]
        if not isfinite(value) or not min_value <= value <= max_value:
            raise ValueError("Invalid weather parameter " + key)
    if options["pac_reference_temp_c"] >= options["pac_balance_temp_c"]:
        raise ValueError("PAC reference temperature must be below heating balance")
    if options["weather_reserve_w"] + options["target_export_w"] > 6000:
        raise ValueError("Total export reserve too large")
    if options["comfort_loss_w"] >= options["max_power_w"] * options["comfort_efficiency"]:
        raise ValueError("Thermal losses must be below effective boiler power")
    for key in ('pv_actual_power_entity', 'pv_actual_energy_today_entity', 'pv_actual_energy_total_entity'):
        entity = source.get(key, '')
        if not isinstance(entity, str) or (entity and (
                not entity.startswith('sensor.') or len(entity) > 255 or
                not all(c.isalnum() or c in '._' for c in entity))):
            raise ValueError('Invalid PV sensor ID: ' + key)
        options[key] = entity
    options['pv_actual_max_age_seconds']=int(source.get('pv_actual_max_age_seconds',120))
    options['pv_actual_sample_seconds']=int(source.get('pv_actual_sample_seconds',60))
    options['pv_self_consumption_topology_verified']=source.get('pv_self_consumption_topology_verified',False)
    if not 10<=options['pv_actual_max_age_seconds']<=900:
        raise ValueError('Invalid PV freshness period')
    if not 10<=options['pv_actual_sample_seconds']<=120:
        raise ValueError('Invalid PV sample period')
    if not isinstance(options['pv_self_consumption_topology_verified'],bool):
        raise ValueError('PV topology confirmation must be boolean')
    return options


def describe_sensor(state: dict, entity_id: str, now: datetime, max_age_s: float) -> dict:
    """Describe a single HA sensor without confusing unchanged with fresh.

    Diagnostic values are informational ONLY. The regulator still requires
    both readings to be fresh; a stale zero cannot authorize heating.
    """
    result = {
        "entity_id": entity_id, "kw": None, "age_s": None,
        "reported_at": None, "last_updated": state.get("last_updated"),
        "timestamp_source": None, "health": "INVALID",
    }
    reported = state.get("last_reported")
    result["timestamp_source"] = "last_reported" if reported else "last_updated"
    try:
        measurement = _read_kw_state(state, entity_id)
        age = (now - measurement.measured_at).total_seconds()
        result.update({
            "kw": measurement.value,
            "age_s": round(age, 1),
            "reported_at": measurement.measured_at.isoformat(),
            "health": "FRESH" if measurement.valid(now, max_age_s) else "STALE",
        })
    except (ValueError, TypeError, AttributeError) as exc:
        result["health"] = "INVALID"
        result["reason"] = str(exc)[:160]
    return result


class Diagnostic:
    def __init__(self, options: dict, client=None, history_store=None,
                 user_settings_path=None):
        self.user_settings_path = (Path(user_settings_path) if
                                   user_settings_path is not None else None)
        self.migrated_forecast_solar_entry_id = ""
        if self.user_settings_path is not None:
            options, self.migrated_forecast_solar_entry_id = read_settings(
                self.user_settings_path, options)
        self.options = parse_options(options)
        if self.user_settings_path is not None and self.user_settings_path.exists():
            save_settings(self.user_settings_path, self.options,
                          self.migrated_forecast_solar_entry_id)
        self._last_meter_report = None
        self._last_poll_at = None
        self.comfort = {"status": "WAITING", "reason": "En attente de mesures valides.",
                        "backup_power_w": 0, "physical_outputs": False}
        self.water_temperature_c = (None if self.options["boiler_temperature_entity"]
                                    else self.options["demo_water_c"])
        self.temperature_source = ("HOME_ASSISTANT" if self.options["boiler_temperature_entity"]
                                   else "SIMULATED_NOT_CONNECTED")
        self.client = client
        self.history_store = history_store
        self.energy_dashboard = EnergyDashboard(history_store) if history_store else None
        self.pv_actual = PVAcquisition(self.options, self.client, history_store)
        self._last_logged_status = None
        self._last_comfort_label = None
        self._last_heartbeat = None
        self._last_forecast_archived_at = None
        self.regulator = SurplusRegulator(Parameters(
            max_power_w=self.options["max_power_w"],
            target_export_w=self.options["target_export_w"],
            gain=0.5,
            max_step_w=300,
            temp_limit_c=self.options["demo_max_c"],
        ))
        self.lock = Lock()
        self.history = deque(maxlen=180)
        self.weather_toggle_token = secrets.token_urlsafe(32)
        self.profile_wake = Event()
        self.weather_enabled = self.options["weather_enabled"]
        self.weather = build_weather_preview(
            self.options, datetime.now(timezone.utc), None, self.weather_enabled)
        self.next_weather_refresh = None
        self.next_profile_refresh = None
        self.profile = {
            "status": "WAITING", "points": [],
            "solar_status": "WAITING", "meteo_status": "WAITING",
            "pac_status": "WAITING"
        }
        self.sensor_diagnostics = {"pi": None, "po": None}
        self.latest = {
            "status": "STARTING",
            "error": None,
            "data_quality": None,
            "communication_healthy": False,
            "measurement_coverage": "NONE",
            "updated_at": None,
            "meter_w": None,
            "simulated_boiler_w": 0,
            "simulated_grid_after_w": None,
            "meter_age_s": None,
            "simulation_state": "NOT_STARTED",
        }

    def poll_once(self, now: datetime | None = None) -> dict:
        injected_clock = now is not None
        now = now or datetime.now(timezone.utc)
        with self.lock:
            # Forecast HTTP work belongs to the profile worker. No forecast
            # dependency may postpone the fast measurement / thermal gate.
            try:
                if self.client is None:
                    raise RuntimeError("Home Assistant API unavailable: no Supervisor token")
                measurement_status = "OK"
                measurement_reason = "Current HA reports"
                communication_healthy = True
                measurement_coverage = "TWO_RECENT"
                if hasattr(self.client, "read_gplugk_states"):
                    raw_pi, raw_po = self.client.read_gplugk_states(
                        self.options["pi_entity"], self.options["po_entity"])
                    if not injected_clock:
                        now = datetime.now(timezone.utc)
                    self.sensor_diagnostics = {
                        "pi": describe_sensor(raw_pi, self.options["pi_entity"],
                                              now, self.options["max_age_seconds"]),
                        "po": describe_sensor(raw_po, self.options["po_entity"],
                                              now, self.options["max_age_seconds"]),
                    }
                    assessment = assess_grid_reading(
                        raw_pi, raw_po, now, self.options["max_age_seconds"])
                    meter_w = assessment["grid_w"]
                    measurement_status = assessment["status"]
                    measurement_reason = assessment["reason"]
                    communication_healthy = assessment["communication_healthy"]
                    measurement_coverage = assessment["coverage"]
                    ages = [sensor["age_s"] for sensor in self.sensor_diagnostics.values()
                            if sensor and sensor["health"] == "FRESH"]
                    age = max(ages) if ages else None
                    reports = [datetime.fromisoformat(s["reported_at"])
                               for s in self.sensor_diagnostics.values()
                               if s and s["health"] == "FRESH"]
                    meter_report = max(reports) if reports else None
                    freshness_report = min(reports) if reports else None
                else:
                    # Backward-compatible pure unit-test client.
                    self.sensor_diagnostics = {"pi": None, "po": None}
                    measured = self.client.read_gplugk_grid_w(
                        self.options["pi_entity"], self.options["po_entity"])
                    if not injected_clock:
                        now = datetime.now(timezone.utc)
                    age = (now - measured.measured_at).total_seconds()
                    if not measured.valid(now, self.options["max_age_seconds"]):
                        raise ValueError(f"gPlugK data too old or future-dated ({age:.0f} s)")
                    meter_w = measured.value
                    meter_report = measured.measured_at
                    freshness_report = measured.measured_at

                entity = self.options["boiler_temperature_entity"]
                water_c = self.options["demo_water_c"]
                if entity:
                    temperature = self.client.read_temperature_c(entity)
                    if not injected_clock:
                        now = datetime.now(timezone.utc)
                    if not temperature.valid(now, self.options["max_age_seconds"]):
                        raise ValueError("Boiler temperature stale or future-dated")
                    water_c = temperature.value
                # Recheck meter freshness after any slow temperature request.
                if freshness_report is None or not -2 <= (now-freshness_report).total_seconds() <= self.options["max_age_seconds"]:
                    raise ValueError("Meter report stale after acquisition")
                if self._last_poll_at is not None and now < self._last_poll_at:
                    raise ValueError("Clock moved backwards; awaiting a new valid cycle")
                self._last_poll_at = now
                if self._last_meter_report is not None and meter_report < self._last_meter_report:
                    raise ValueError("Meter report moved backwards")
                new_sample = meter_report != self._last_meter_report
                self.water_temperature_c = water_c
                self.comfort = plan_comfort(self.options, now, water_c, self.profile)
                self.comfort["temperature_source"] = self.temperature_source
                self.comfort["physical_outputs"] = False
                # Communication and power confidence are different notions.
                if measurement_status == "POWER_UNKNOWN":
                    decision = self.regulator.invalid_data("Net power ambiguous")
                    simulated_after = None
                elif measurement_coverage == "ONE_RECENT" and meter_w > 0:
                    decision = self.regulator.invalid_data(
                        "Incomplete import measurement: virtual heater inhibited")
                    simulated_after = None
                else:
                    old_command = self.regulator.command_w
                    limit = self.options["demo_max_c"]
                    reserve = self.options["target_export_w"]
                    weather_fresh = False
                    if self.weather_enabled and self.weather.get("forecast_timestamp"):
                        stamp = datetime.fromisoformat(self.weather["forecast_timestamp"])
                        weather_fresh = -300 <= (now-stamp).total_seconds() <= 10800
                    if self.weather_enabled:
                        reserve += self.options["weather_reserve_w"]
                    if self.weather_enabled and weather_fresh and self.weather["status"] == "ADAPTIVE":
                        limit = self.weather["target_c"]
                    decision = self.regulator.step(meter_w + old_command, water_c,
                                                   temp_limit_c=limit,
                                                   target_export_w=reserve)
                    if decision.state == "AUTO_READY_SIMULATION":
                        # No repeated correction on the same HA report.
                        surplus_command = decision.command_w if new_sample else old_command
                        surplus_command = min(surplus_command, max(0, -meter_w-reserve))
                        backup = self.comfort["backup_power_w"]
                        backup_ok = (self.options["grid_backup_enabled"] and backup > 0
                                     and water_c < self.options["weather_min_c"])
                        total = surplus_command
                        if backup_ok:
                            ramped = (min(backup, old_command+300) if new_sample
                                      else min(backup, old_command))
                            # Limit interval energy to the current measured gap.
                            gap_w = ((self.options["weather_min_c"]-water_c)
                                     * self.options["weather_volume_l"] * 1.163
                                     * 3600 / self.options["sample_seconds"]
                                     / self.options["comfort_efficiency"])
                            total = max(total, min(ramped, gap_w))
                        self.regulator.command_w = min(self.options["max_power_w"], total)
                        decision = Decision(self.regulator.command_w,
                            "COMFORT_BACKUP_SIMULATION" if backup_ok else decision.state,
                            "Logical calculation only; no actuator")
                    simulated_after = meter_w + decision.command_w
                self._last_meter_report = meter_report
                self.comfort["gate_state"] = decision.state
                self.comfort["applied_boiler_w"] = round(decision.command_w)
                self.latest = {
                    "status": measurement_status,
                    "error": None,
                    "data_quality": measurement_reason,
                    "communication_healthy": communication_healthy,
                    "measurement_coverage": measurement_coverage,
                    "updated_at": now.isoformat(),
                    "meter_w": round(meter_w) if meter_w is not None else None,
                    "simulated_boiler_w": round(decision.command_w),
                    "simulated_grid_after_w": (
                        round(simulated_after) if simulated_after is not None else None
                    ),
                    "meter_age_s": round(age, 1) if age is not None else None,
                    "simulation_state": decision.state,
                }
                if meter_w is not None:
                    self.history.append({
                        "time": now.strftime("%H:%M:%S"),
                        "meter_w": round(meter_w),
                        "simulated_boiler_w": round(decision.command_w),
                    })
            except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
                self.regulator.invalid_data(str(exc))
                self.water_temperature_c = None
                self.comfort = {"status": "FAULT", "reason": str(exc)[:260],
                                "backup_power_w": 0, "applied_boiler_w": 0,
                                "gate_state": "FAULT", "physical_outputs": False}
                self.latest = {
                    "status": "FAULT",
                    "error": str(exc)[:260],
                    "data_quality": None,
                    "communication_healthy": False,
                    "measurement_coverage": "NONE",
                    "updated_at": now.isoformat(),
                    "meter_w": None,
                    "simulated_boiler_w": 0,
                    "simulated_grid_after_w": None,
                    "meter_age_s": None,
                    "simulation_state": "FAULT",
                }
            result = self.snapshot()
        # Persistent recording is outside the regulator lock and never
        # participates in electrical decisions or causes a faulty command.
        if self.history_store is not None:
            try:
                self.history_store.record(now, result)
                comfort_label = (result["comfort"].get("status"),
                                 result["comfort"].get("deadline"))
                if comfort_label != self._last_comfort_label:
                    self.history_store.log(now, "INFO", "COMFORT_PLAN",
                        str(comfort_label) + ": " + result["comfort"].get("reason", ""))
                    self._last_comfort_label = comfort_label
                label = str(result["status"])
                if (label != self._last_logged_status or
                    self._last_heartbeat is None or
                    (now-self._last_heartbeat).total_seconds() >= 300):
                    self.history_store.log(now,
                        "WARNING" if label not in ("OK","POWER_UNKNOWN") else "INFO",
                        "GRID_STATUS",
                        f"{label}: network={result.get('meter_w')} W; "
                        f"virtual_boiler={result.get('simulated_boiler_w')} W; "
                        f"coverage={result.get('measurement_coverage')}")
                    self._last_logged_status = label
                    self._last_heartbeat = now
            except (OSError, ValueError, sqlite3.Error) as error:
                LOG.error("Noncritical telemetry storage failure: %s", error)
        return result

    def refresh_weather_once(self, now: datetime | None = None) -> dict:
        now = now or datetime.now(timezone.utc)
        with self.lock:
            options, enabled = dict(self.options), self.weather_enabled
        weather = build_weather_preview(options, now, self.client, enabled)
        with self.lock:
            if options == self.options and enabled == self.weather_enabled:
                self.weather = weather
        return weather

    def refresh_profile_once(self, now: datetime | None = None) -> dict:
        """Refresh the longer-running forecast OUTSIDE the gPlugK lock."""
        now = now or datetime.now(timezone.utc)
        with self.lock:
            options = dict(self.options)
        self.refresh_weather_once(now)
        try:
            profile = make_hourly_profile(options, now, self.client)
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as error:
            profile = {
                "status": "UNAVAILABLE", "points": [],
                "solar_error": "Erreur profil : " + str(error)[:180],
                "meteo_error": None,
            }
        with self.lock:
            if options != self.options:
                return self.profile  # discard an in-flight response for old settings
            water_c = self.water_temperature_c
            ceiling = (self.weather["target_c"] if self.weather_enabled
                       and self.weather["status"] == "ADAPTIVE" else options["demo_max_c"])
        # CPU-only forward model outside the measurement lock. Its generation
        # timestamp remains explicit; it is a scenario, never measured heat.
        if water_c is not None:
            profile = project_comfort_profile(options, now, water_c, profile, ceiling_c=ceiling)
        else:
            profile["comfort_projection"] = {"status": "UNAVAILABLE",
                "reason": "Température valide non disponible ; trajectoire non calculée."}
        with self.lock:
            if options != self.options:
                return self.profile
            self.profile = profile
        if self.history_store is not None:
            try:
                # Archive at most one as-issued forecast per hour.
                if (self._last_forecast_archived_at is None or
                    (now-self._last_forecast_archived_at).total_seconds() >= 3600):
                    self.history_store.record_forecast(now, profile)
                    self._last_forecast_archived_at = now
                if profile.get("status") not in ("OK", "PARTIAL"):
                    self.history_store.log(now, "WARNING", "FORECAST",
                        (profile.get("solar_error") or profile.get("meteo_error")
                         or profile.get("status") or "Unavailable")[:400])
            except (OSError, ValueError, sqlite3.Error) as error:
                LOG.error("Forecast archival failure (noncritical): %s", error)
        return profile

    def refresh_current_weather_once(self) -> None:
        """Refresh only the live temperature (2 min), not 48 h forecasts."""
        try:
            temperature = read_current_temperature_c(
                self.client, self.options["pac_weather_entity"])
            error = None
        except (OSError, ValueError, TypeError, KeyError, AttributeError, TimeoutError) as exc:
            temperature, error = None, str(exc)[:180]
        with self.lock:
            self.profile["current_temperature_c"] = temperature
            self.profile["current_temperature_error"] = error

    def profile_loop(self, stop: Event) -> None:
        """Refresh forecasts slowly and live weather without blocking gPlugK."""
        next_forecast = None
        while not stop.is_set():
            now = datetime.now(timezone.utc)
            if next_forecast is None or now >= next_forecast:
                result = self.refresh_profile_once(now)
                next_forecast = now + timedelta(
                    seconds=1800 if result["status"] == "OK" else 300)
            else:
                self.refresh_current_weather_once()
            self.profile_wake.wait(120)
            if self.profile_wake.is_set():
                next_forecast = None
                self.profile_wake.clear()

    def set_user_settings(self, values: dict) -> None:
        """Save validated UI preferences only; never call HA control services."""
        allowed = USER_FIELDS
        if not isinstance(values, dict) or set(values) != allowed:
            raise ValueError("Unexpected user settings fields")
        if (isinstance(values["demo_max_c"], bool) or
                isinstance(values["weather_min_c"], bool) or
                not isinstance(values["demo_max_c"], (int, float)) or
                not isinstance(values["weather_min_c"], (int, float))):
            raise ValueError("Temperature settings must be numeric")
        if not isinstance(values["weather_enabled"], bool) or not isinstance(values["pac_enabled"], bool):
            raise ValueError("Strategy switches must be boolean")
        with self.lock:
            proposed = parse_options({**self.options, **values})
            if self.user_settings_path is not None:
                save_settings(self.user_settings_path, proposed,
                              self.migrated_forecast_solar_entry_id)
            self.options = proposed
            self._last_meter_report = None
            self.comfort = {"status": "WAITING", "reason": "Réglages modifiés ; nouveau calcul attendu.",
                            "backup_power_w": 0, "applied_boiler_w": 0, "physical_outputs": False}
            self.latest["simulated_boiler_w"] = 0
            self.latest["simulated_grid_after_w"] = self.latest.get("meter_w")
            self.latest["simulation_state"] = "SETTINGS_CHANGED"
            self.profile = {"status": "WAITING", "points": []}
            self.weather_enabled = proposed["weather_enabled"]
            self.regulator = SurplusRegulator(Parameters(
                max_power_w=proposed["max_power_w"],
                target_export_w=proposed["target_export_w"],
                gain=0.5, max_step_w=300,
                temp_limit_c=proposed["demo_max_c"],
            ))
            self.next_weather_refresh = None
            self.weather = build_weather_preview(
                self.options, datetime.now(timezone.utc), None, self.weather_enabled)
            self.profile_wake.set()

    def set_weather_enabled(self, enabled: bool) -> None:
        """Change adviser only, in process memory. No HA or device writes."""
        if not isinstance(enabled, bool):
            raise ValueError("Boolean weather toggle required")
        with self.lock:
            self.weather_enabled = enabled
            self.options["weather_enabled"] = enabled
            self.profile_wake.set()
            self.next_weather_refresh = None
            self.weather = build_weather_preview(
                self.options, datetime.now(timezone.utc), None, self.weather_enabled)
            if enabled:
                self.weather.update({
                    "enabled": True,
                    "status": "UPDATING",
                    "decision": "Lecture des prévisions Home Assistant au prochain cycle.",
                })

    def snapshot(self) -> dict:
        # Only invoke this under the lock, or from HTTP with lock held.
        return {
            "version": "1.0.0-alpha.3",
            "weather_toggle_token": self.weather_toggle_token,
            "read_only": True,
            "temperature_source": self.temperature_source,
            "water_temperature_c": self.water_temperature_c,
            "comfort": dict(self.comfort),
            "forecast_configuration_migrated": bool(self.migrated_forecast_solar_entry_id),
            "physical_outputs": "NOT_IMPLEMENTED",
            "options": dict(self.options),
            **self.latest,
            "history": list(self.history),
            "sensors": dict(self.sensor_diagnostics),
            "weather": dict(self.weather),
            "profile": dict(self.profile),
        }

    def loop(self, stop: Event) -> None:
        while not stop.is_set():
            record = self.poll_once()
            if record["status"] in ("OK", "POWER_UNKNOWN"):
                LOG.info("gPlugK meter=%s W | status %s | virtual boiler %d W | virtual grid %s; temperature demo %.1f C",
                         str(record["meter_w"]), record["status"], record["simulated_boiler_w"],
                         str(record["simulated_grid_after_w"]), self.options["demo_water_c"])
            else:
                LOG.warning("Diagnostic read-only: %s", record["error"])
            stop.wait(self.options["sample_seconds"])


def handler_factory(diag: Diagnostic):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path == "/api/history.csv":
                if diag.history_store is None:
                    self.send_error(503, "Persistent history not configured")
                    return
                from urllib.parse import parse_qs
                args = parse_qs(self.path.partition("?")[2], keep_blank_values=True)
                try:
                    if set(args) - {"start", "end", "format", "type"}:
                        raise ValueError("Unsupported CSV parameter")
                    if any(len(values) != 1 for values in args.values()):
                        raise ValueError("Repeated CSV parameter")
                    start = args["start"][0]
                    end = args["end"][0]
                    fmt = args.get("format", ["excel"])[0]
                    if fmt not in ("excel", "standard"):
                        raise ValueError("CSV format must be excel or standard")
                    dataset = args.get("type", ["network"])[0]
                    if dataset not in ("network", "forecasts", "logs"):
                        raise ValueError("CSV dataset must be network, forecasts or logs")
                    if dataset == "network":
                        chunks = diag.history_store.iter_export_history_csv(
                            start, end, excel=(fmt == "excel"))
                    else:
                        chunks = diag.history_store.iter_export_auxiliary_csv(
                            start, end, excel=(fmt == "excel"), dataset=dataset)
                    # Consume first chunk BEFORE sending HTTP 200, so errors
                    # from initialization cannot become a corrupt CSV download.
                    first_chunk = next(chunks)
                except (ValueError, KeyError, StopIteration) as error:
                    self.send_error(400, str(error)[:150])
                    return
                # Large exports stream in bounded batches instead of loading
                # entire SQLite history into Raspberry Pi memory.
                self.send_response(200)
                self.send_header("Content-Type", "text/csv; charset=utf-8")
                self.send_header("Content-Disposition",
                    'attachment; filename="surplus_pv_historique_' +
                    dataset + '_' + start[:10] + '_' + end[:10] +
                    ('_excel' if fmt == "excel" else '_standard') + '.csv"')
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Content-Security-Policy", "default-src 'none'")
                self.send_header("Connection", "close")
                self.end_headers()
                self.close_connection = True
                try:
                    self.wfile.write(first_chunk)
                    for chunk in chunks:
                        self.wfile.write(chunk)
                except (OSError, sqlite3.Error, ValueError) as error:
                    LOG.warning("CSV export connection ended: %s", error)
                return
            if path in ("/", "/index.html", "/entrees"):
                body = MOBILE_INPUTS.read_bytes()
                content_type = "text/html; charset=utf-8"
            elif path in ("/profil", "/profil.html"):
                body = MOBILE_INPUTS.read_bytes()
                content_type = "text/html; charset=utf-8"
            elif path in ("/boiler", "/boiler.html"):
                body = BOILER_VIEW.read_bytes()
                content_type = "text/html; charset=utf-8"
            elif path in ('/energie', '/energy.html'):
                body = (MOBILE_INPUTS.parent / 'energy.html').read_bytes()
                content_type = 'text/html; charset=utf-8'
            elif path in ("/diagnostic", "/diagnostic.html"):
                body = INDEX.read_bytes()
                content_type = "text/html; charset=utf-8"
            elif path == "/theme.css":
                body = THEME_CSS.read_bytes()
                content_type = "text/css; charset=utf-8"
            elif path == "/theme.js":
                body = THEME_JS.read_bytes()
                content_type = "application/javascript; charset=utf-8"
            elif path in ("/ui.js", "/dashboard.js", "/chart.js", "/energy.js"):
                body = (MOBILE_INPUTS.parent / path[1:]).read_bytes()
                content_type = "application/javascript; charset=utf-8"
            elif path == "/history.js":
                body = HISTORY_JS.read_bytes()
                content_type = "application/javascript; charset=utf-8"
            elif path == '/api/energy':
                if diag.energy_dashboard is None:
                    self.send_error(503, 'Persistent history not configured')
                    return
                from urllib.parse import parse_qs
                from zoneinfo import ZoneInfo
                args = parse_qs(self.path.partition('?')[2], keep_blank_values=True)
                try:
                    if set(args)-{'period','date'} or any(len(v) != 1 for v in args.values()):
                        raise ValueError('Unsupported or repeated energy parameter')
                    period = args.get('period', ['day'])[0]
                    selected = args.get('date', [datetime.now(ZoneInfo('Europe/Zurich')).date().isoformat()])[0]
                    data = dict(diag.energy_dashboard.query(period, selected))
                    with diag.lock:
                        state = diag.snapshot()
                    data['live'] = live_grid(state, diag.options['max_age_seconds'])
                    if diag.pv_actual.history:
                        try:
                            data = diag.pv_actual.history.enrich(data)
                        except (sqlite3.Error, ValueError, OSError):
                            data['pv_history_error'] = 'Historique PV temporairement indisponible'
                    pv = diag.pv_actual.snapshot()
                    power = pv['power']
                    calculated = pv['self']
                    data['pv_actual'] = {
                        'available': power['status']=='OK', 'source':power['source'],
                        'reason': ('Production PV réelle : disponible' if power['status']=='OK' else
                                   'Production PV réelle : non disponible — '+power['reason']),
                        'power':power, 'energy_today':pv['today'], 'energy_total':pv['total'],
                        'storage_error':pv['storage_error'], 'development':'SW-004.2'}
                    data['self_consumption'] = {
                        'available':calculated['status']=='OK',
                        'reason':calculated['reason'], 'power':calculated,
                        'topology_verified':diag.options['pv_self_consumption_topology_verified'],
                        'sync_tolerance_seconds':15}
                    data['forecast_comparison'] = {
                        'mode':'Measured AC kept separate from raw Forecast.Solar predictions',
                        'forecast_api':'./api/forecast-at', 'forecast_view':'./#profil',
                        'correction_applied':False}

                    data['pv_configuration'] = {k:diag.options[k] for k in (
                        'pv_actual_power_entity','pv_actual_energy_today_entity','pv_actual_energy_total_entity')}
                    body = json.dumps(data, ensure_ascii=False, allow_nan=False).encode('utf-8')
                    content_type = 'application/json; charset=utf-8'
                except (ValueError, OverflowError) as exc:
                    self.send_error(400, str(exc)[:140])
                    return
                except sqlite3.Error:
                    self.send_error(503, 'Energy history temporarily unavailable')
                    return
            elif path in ("/api/history", "/api/archives", "/api/logs",
                              "/api/forecast-at", "/api/archive.csv"):
                if diag.history_store is None:
                    self.send_error(503, "Persistent history not configured")
                    return
                from urllib.parse import parse_qs
                from datetime import timedelta
                args = parse_qs(self.path.partition("?")[2], keep_blank_values=False)
                def param(name, fallback=None):
                    values = args.get(name)
                    if values and len(values) > 1:
                        raise ValueError("Parameter repeated: " + name)
                    return values[0] if values else fallback
                try:
                    if path == "/api/history":
                        end = parse_time(param("end", datetime.now(timezone.utc).isoformat()))
                        start = parse_time(param("start", (end-timedelta(hours=48)).isoformat()))
                        data = diag.history_store.query(start,end)
                        body = json.dumps(data,ensure_ascii=False,allow_nan=False).encode("utf-8")
                        content_type = "application/json; charset=utf-8"
                    elif path == "/api/archives":
                        body = json.dumps(diag.history_store.archives(),
                                          ensure_ascii=False).encode("utf-8")
                        content_type = "application/json; charset=utf-8"
                    elif path == "/api/logs":
                        count = int(param("limit", "300"))
                        body = json.dumps(diag.history_store.logs(limit=count),
                                          ensure_ascii=False).encode("utf-8")
                        content_type = "application/json; charset=utf-8"
                    elif path == "/api/forecast-at":
                        at = parse_time(param("at", datetime.now(timezone.utc).isoformat()))
                        result = diag.history_store.forecast_at(at)
                        body = json.dumps(
                            result if result is not None else
                            {"saved_at":None,"profile":None}, ensure_ascii=False
                        ).encode("utf-8")
                        content_type = "application/json; charset=utf-8"
                    else:
                        day = param("day", "")
                        body = diag.history_store.export_archive_csv(day)
                        content_type = "text/csv; charset=utf-8"
                except (ValueError, OverflowError) as exc:
                    self.send_error(400, str(exc)[:140])
                    return
            elif path in ("/api/status", "/status", "/api/telemetry"):
                with diag.lock:
                    state = diag.snapshot()
                if path == "/api/telemetry":
                    # Native HA REST sensors need no settings/session token.
                    state = {key: state[key] for key in (
                        "version", "read_only", "physical_outputs", "temperature_source",
                        "water_temperature_c", "updated_at", "status", "meter_w",
                        "simulated_boiler_w", "simulation_state", "comfort")}
                body = json.dumps(state, ensure_ascii=False, allow_nan=False).encode("utf-8")
                content_type = "application/json; charset=utf-8"
            else:
                self.send_error(404, "Not found")
                return
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            if path == "/api/archive.csv":
                from urllib.parse import parse_qs
                day = parse_qs(self.path.partition("?")[2]).get("day", ["archive"])[0]
                self.send_header("Content-Disposition",
                                 'attachment; filename="surplus_pv_' + day + '.csv"')
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'self'")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            # Local diagnostic settings only; never touch Home Assistant or power hardware.
            path = self.path.split("?", 1)[0]
            if path not in ("/api/weather-toggle", "/api/user-settings"):
                self.send_error(405, "No hardware/HA write operations")
                return
            # HA ingress can remove or rewrite Sec-Fetch-Site and Host,
            # so neither header is reliable proof of same-origin access.
            # The GET /api/status API provides an unpredictable, per-process
            # CSRF token. A browser on a foreign origin cannot read it due to
            # HA ingress authentication and no Access-Control-Allow-Origin.
            if self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
                self.send_error(415, "Expected application/json")
                return
            try:
                content_length = int(self.headers.get("Content-Length", "0"))
                if content_length < 2 or content_length > 1300:
                    self.send_error(413, "Invalid request size")
                    return
                content = json.loads(self.rfile.read(content_length).decode("utf-8"))
                if not isinstance(content, dict) or not isinstance(content.get("token"), str):
                    self.send_error(400, "Missing session token")
                    return
                if not secrets.compare_digest(content["token"], diag.weather_toggle_token):
                    self.send_error(403, "Invalid or expired session token")
                    return
                if path == "/api/weather-toggle":
                    if set(content) != {"enabled", "token"} or not isinstance(content["enabled"], bool):
                        self.send_error(400, "Expected enabled and token fields")
                        return
                    diag.set_weather_enabled(content["enabled"])
                else:
                    if set(content) != {"settings", "token"}:
                        self.send_error(400, "Expected settings and token fields")
                        return
                    diag.set_user_settings(content["settings"])
            except OSError:
                self.send_error(503, "Cannot save diagnostic settings")
                return
            except (ValueError, TypeError, UnicodeError, json.JSONDecodeError):
                self.send_error(400, "Invalid JSON")
                return
            body = b'{"ok":true}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt, *args):
            LOG.debug("web: " + fmt, *args)

    return Handler


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    LOG.warning("DIAGNOSTIC ONLY. No GPIO, I2C, DAC, watchdog, or HA writes.")
    config_file = Path(os.environ.get("SPV_OPTIONS_FILE", "/data/options.json"))
    options = json.loads(config_file.read_text(encoding="utf-8"))
    token = os.environ.get("SUPERVISOR_TOKEN") or os.environ.get("HASSIO_TOKEN") or ""
    client = HomeAssistantClient(token) if token else None
    store = None
    try:
        store = HistoryStore(os.environ.get("SPV_HISTORY_DB",
                                           "/data/surplus_pv_history.sqlite3"))
        store.log(datetime.now(timezone.utc), "INFO", "START",
                  "Surplus PV diagnostic started, persistent history active")
    except (OSError, sqlite3.Error) as error:
        LOG.error("Persistent storage unavailable: %s", error)
    diag = Diagnostic(options, client, history_store=store,
                      user_settings_path=os.environ.get("SPV_USER_SETTINGS_FILE",
                                                      "/data/surplus_pv_user_settings.json"))
    stop = Event()
    worker = Thread(target=diag.loop, args=(stop,), daemon=True)
    worker.start()
    profile_worker = Thread(target=diag.profile_loop, args=(stop,), daemon=True)
    profile_worker.start()
    def pv_loop():
        while not stop.is_set():
            if diag.pv_actual.enabled:
                with diag.lock:
                    grid_snapshot = diag.snapshot()
                diag.pv_actual.poll(grid_snapshot)
            stop.wait(diag.options['pv_actual_sample_seconds'])
    pv_worker = Thread(target=pv_loop, daemon=True)
    pv_worker.start()
    server = ThreadingHTTPServer(("0.0.0.0", 8099), handler_factory(diag))
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        stop.set()
        server.server_close()
        worker.join(timeout=5)
        profile_worker.join(timeout=5)
        pv_worker.join(timeout=15)
        if store is not None:
            store.close()


if __name__ == "__main__":
    main()

