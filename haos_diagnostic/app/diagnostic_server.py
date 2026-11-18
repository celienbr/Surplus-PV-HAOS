"""HAOS measurements, comfort planning and guarded GP8600 control."""
from __future__ import annotations

from collections import deque
from datetime import datetime, timezone, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock, Thread, Event
from queue import Queue, Full, Empty
import json
import logging
import os
import secrets
import sqlite3
import time

from .controller import Parameters, SurplusRegulator, Decision
from .user_settings import read_settings, write_b3_settings, B3_FIELDS
from .auto_target import resolve_auto_target
from .comfort_planner import plan_comfort
from .ha_client import HomeAssistantClient
from .gplugk import _read_kw_state, net_grid_power_w, assess_grid_reading
from .forecast_adapter import build_weather_preview, outdoor_forecast_c
from .open_meteo_weather import read_tomorrow_from_ha
from .profile_forecast import make_hourly_profile, read_current_temperature_c
from .predictive_target import predictive_target
from .history_store import HistoryStore, parse_time
from .energy_dashboard import EnergyDashboard, live_grid
from .pv_actual import PVAcquisition
from .gp8600 import make_dac, number
from .output_control import OutputControl
from .dac_calibration import CalibrationStore
from .measurements import Measurement
from .boiler_temperature import valid_boiler_temperature
from .heartbeat import HeartbeatService
from .alarms import Alarms
from .power_curve import PowerCurve
from .gpio_v2 import GPIOOutput
from .async_ha import AsyncControlInputs

LOG = logging.getLogger("surplus_pv_diagnostic")
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
        "boiler_temperature_max_age_seconds": int(source.get("boiler_temperature_max_age_seconds", 60)),
        "boiler_temperature_refresh_enabled": source.get("boiler_temperature_refresh_enabled", False),
        "boiler_temperature_refresh_seconds": int(source.get("boiler_temperature_refresh_seconds", 15)),
        "boiler_max_temp_c": float(source.get("boiler_max_temp_c") if source.get("boiler_max_temp_c") is not None else source.get("demo_max_c", 70)),
        "target_export_w": float(source.get("target_export_w", 100)),
        "max_power_w": float(source.get("max_power_w", 6000)),
        "weather_enabled": source.get("weather_enabled", False),
        "predictive_target_enabled": source.get("predictive_target_enabled", False),
        "auto_target_c": source.get("auto_target_c"),
        "manual_target_c": source.get("manual_target_c"),
        "manual_percent": source.get("manual_percent", 0),
        "comfort_time": source.get("comfort_time", "06:00"),
        "grid_backup_enabled": source.get("grid_backup_enabled", False),
        "use_addon_settings": source.get("use_addon_settings", False),
        "legacy_settings_active": source.get("legacy_settings_active", False),
        "boiler_temperature_entity": source.get("boiler_temperature_entity", ""),
        "comfort_margin_minutes": float(source.get("comfort_margin_minutes", 20)),
        "comfort_grace_minutes": float(source.get("comfort_grace_minutes", 60)),
        "forecast_today_entity": source.get("forecast_today_entity", "sensor.energy_production_today_remaining"),
        "forecast_tomorrow_entity": source.get("forecast_tomorrow_entity", "sensor.energy_production_tomorrow"),
        "forecast_solar_entry_id": source.get("forecast_solar_entry_id", ""),
        "weather_min_c": float(source.get("weather_min_c", 50)),
        "weather_buffer_c": float(source.get("weather_buffer_c", 5)),
        "weather_volume_l": float(source.get("weather_volume_l", 300)),
        "weather_heat_demand_kwh": float(source.get("weather_heat_demand_kwh", 5)),
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
    # The Supervisor may have stripped the old option during an upgrade. A
    # numeric fallback is used only for display/calculation until the original
    # or a new ceiling is known; real output remains inhibited in that case.
    options['boiler_ceiling_configured'] = (source.get('boiler_max_temp_c') is not None
                                             or source.get('demo_max_c') is not None)
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
    if not 10 <= options["boiler_temperature_max_age_seconds"] <= 300:
        raise ValueError("Invalid boiler temperature max age")
    if not isinstance(options["boiler_temperature_refresh_enabled"], bool):
        raise ValueError("Boiler active refresh must be a boolean")
    if not 10 <= options["boiler_temperature_refresh_seconds"] <= 120:
        raise ValueError("Invalid boiler active refresh period")
    if not 40 <= options["boiler_max_temp_c"] <= 80:
        raise ValueError("Invalid boiler maximum")
    if not 0 <= options["target_export_w"] <= 2000:
        raise ValueError("Invalid target export")
    if not 100 <= options["max_power_w"] <= 6000:
        raise ValueError("Invalid boiler power")
    # Params validates values including NaN/Infinity.
    Parameters(
        max_power_w=options["max_power_w"],
        target_export_w=options["target_export_w"],
        temp_limit_c=options["boiler_max_temp_c"],
    )
    if not isinstance(options["pac_enabled"], bool):
        raise ValueError("pac_enabled must be a boolean")
    if not isinstance(options["weather_enabled"], bool):
        raise ValueError("weather_enabled must be a boolean")
    if not isinstance(options["predictive_target_enabled"], bool):
        raise ValueError("predictive_target_enabled must be a boolean")
    if not isinstance(options["grid_backup_enabled"], bool):
        raise ValueError("grid_backup_enabled must be a boolean")
    if type(options['use_addon_settings']) is not bool or type(options['legacy_settings_active']) is not bool:
        raise ValueError('Invalid settings migration choice')
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
    if not 30 <= options["weather_min_c"] <= options["boiler_max_temp_c"]:
        raise ValueError("Minimum must not exceed maximum")
    for key in ('auto_target_c', 'manual_target_c'):
        if options[key] is not None:
            options[key] = number(options[key], 30, 80)
            # A Supervisor ceiling lowered after saving always wins.
            options[key] = min(options[key], options['boiler_max_temp_c'])
    options['manual_percent'] = number(options['manual_percent'], 0, 100)
    if options['auto_target_c'] is not None:
        # A new Supervisor minimum can supersede an older stored preference.
        options['auto_target_c'] = max(options['auto_target_c'], options['weather_min_c'])
    limits = {
        "comfort_margin_minutes": (0, 120),
        "comfort_grace_minutes": (0, 120),
        "weather_buffer_c": (0, 20),
        "weather_volume_l": (1, 2000),
        "weather_heat_demand_kwh": (0, 30),
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
    options['dac_i2c_address'] = source.get('dac_i2c_address', 88)
    if type(options['dac_i2c_address']) is not int or not 88 <= options['dac_i2c_address'] <= 95:
        raise ValueError('GP8600 I2C address must be 88..95')
    options['power_curve_points'] = source.get('power_curve_points', '')
    options['power_curve_confirmed'] = source.get('power_curve_confirmed', False)
    if not isinstance(options['power_curve_points'], str) or type(options['power_curve_confirmed']) is not bool:
        raise ValueError('Invalid power curve settings')
    options['heartbeat_task_timeout_s'] = int(source.get('heartbeat_task_timeout_s', 30))
    options['heartbeat_autostart'] = source.get('heartbeat_autostart', False)
    options['heartbeat_period_s'] = source.get('heartbeat_period_s', 0)
    options['heartbeat_width_s'] = source.get('heartbeat_width_s', 0)
    if type(options['heartbeat_autostart']) is not bool:
        raise ValueError('Invalid watchdog autostart option')
    for name in ('heartbeat_period_s', 'heartbeat_width_s'):
        number(options[name], 0, 3600)
    if not 1 <= options['heartbeat_task_timeout_s'] <= 300:
        raise ValueError('Invalid watchdog timing')
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
                 user_settings_path=None, dac=None, calibration_store=None):
        self.user_settings_path = (Path(user_settings_path) if
                                   user_settings_path is not None else None)
        self.migrated_forecast_solar_entry_id = ""
        self.settings_error = None
        configured_options = dict(options)
        if self.user_settings_path is not None:
            try:
                options, self.migrated_forecast_solar_entry_id = read_settings(
                    self.user_settings_path, options)
                parse_options(options)
            except (OSError, ValueError, TypeError) as exc:
                self.settings_error = str(exc)[:160]
                options = configured_options
                LOG.error('Préférences invalides ou indisponibles ; démarrage en arrêt : %s', exc)
        self.requested_mode = None if self.settings_error else options.get('requested_mode')
        self.dac_fault_latched = options.get('dac_fault_latched', False) if not self.settings_error else False
        self.options = parse_options(options)
        self.restore_status = ('Aucun état mémorisé valide ; démarrage en ARRÊT' if self.requested_mode is None else
                               'Mode restauré : ARRÊT' if self.requested_mode == 'ARRET' else
                               'Mode mémorisé : ' + self.requested_mode + ' — reprise en attente')
        if self.settings_error:
            self.restore_status += ' : ' + self.settings_error
        self.client = client
        self.output = OutputControl(dac or make_dac(self.options), 'boiler',
                                    auto_timeout=self.options['max_age_seconds'], max_power=self.options['max_power_w'])
        if self.options['power_curve_confirmed']:
            try:
                points = json.loads(self.options['power_curve_points'])
                curve = PowerCurve(points, source='measurement', validated=True)
                if curve.points[0][0] != 0 or curve.points[-1][0] < self.options['max_power_w']:
                    raise ValueError('La courbe doit couvrir 0 et la puissance maximale configurée')
                self.output.power_curve = curve
            except (TypeError, ValueError) as exc:
                raise ValueError('Courbe puissance/courant invalide : ' + str(exc)) from exc
        self.heartbeat = HeartbeatService()
        self.fault_lock = Lock()
        self.alarms = Alarms()
        if self.dac_fault_latched:
            self.alarms.observe('A002', 'DEFAUT')
        self.startup_lock = Lock()
        self.dac_startup_self_test = {'result': 'NOT_RUN', 'at': None}
        self.startup_auto_pending = False
        self.startup_reason = None
        self.startup_watchdog_started = False
        self.startup_zeroed = False
        self.startup_next_attempt = 0.0
        self.calibration = calibration_store or CalibrationStore()
        self._control_generation = self.output.revision
        self._next_boiler_refresh_monotonic = 0.0
        self._boiler_refresh_result = "NOT_ATTEMPTED"
        self._boiler_refresh_error = None
        self._boiler_refresh_at = None
        self.boiler_temperature_diagnostic = {
            "entity": self.options['boiler_temperature_entity'],
            "status": "STARTING", "reason": "Initial acquisition pending",
            "value_c": None, "report_at": None, "age_s": None,
            "refresh_result": self._boiler_refresh_result,
            "refresh_at": None,
        }
        self._last_thermal_log_status = None
        self.refresh_control_temperature_once()
        self._last_meter_report = None
        self._last_meter_reports = None
        self._last_poll_at = None
        self.control_timing = None
        self.control_sample_count = 0
        self.control_last_sample_ns = None
        self.comfort = {"status": "WAITING", "reason": "En attente de mesures valides.",
                        "backup_power_w": 0}
        self.comfort_estimate = None
        self.water_temperature_c = None
        self.temperature_source = ("HOME_ASSISTANT" if self.options["boiler_temperature_entity"]
                                   else "UNCONFIGURED")
        self.client = client
        self.history_store = history_store
        self.record_queue = None
        self.record_worker = None
        self.record_stop = Event()
        self.record_dropped = 0
        self.energy_dashboard = EnergyDashboard(history_store) if history_store else None
        self.pv_actual = PVAcquisition(self.options, self.client, history_store)
        self._last_logged_status = None
        self._last_comfort_label = None
        self._last_heartbeat = None
        self._last_alarm_states = {}
        self._last_forecast_archived_at = None
        self.regulator = SurplusRegulator(Parameters(
            max_power_w=self.options["max_power_w"],
            target_export_w=self.options["target_export_w"],
            gain=0.5,
            max_step_w=300,
            temp_limit_c=self.options["boiler_max_temp_c"],
        ))
        self.lock = Lock()
        self.preferences_lock = Lock()
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
        self.predictive = {"status": "WAITING", "target_c": None, "fallback": True,
                           "reason": "En attente de prévisions et de température valides."}
        self.auto_applied = None
        self.sensor_diagnostics = {"pi": None, "po": None}
        self.latest = {
            "status": "STARTING",
            "error": None,
            "data_quality": None,
            "communication_healthy": False,
            "measurement_coverage": "NONE",
            "updated_at": None,
            "meter_w": None,
            "requested_boiler_w": 0,
            "meter_age_s": None,
            "control_state": "NOT_STARTED",
        }

    def poll_once(self, now: datetime | None = None) -> dict:
        injected_clock = now is not None
        now = now or datetime.now(timezone.utc)
        generation = self.output.snapshot()['revision']
        meter_observation = None
        new_sample = False
        timing = None
        grid_generation = None
        temperature_invalid = False
        failure_stage = 'meter'
        calculation_failed = False
        with self.lock:
            self.auto_applied = None
            if generation != self._control_generation:
                self.regulator.invalid_data('Output mode changed; discard old automatic command')
                if not self.output.snapshot()['auto_suspended']:
                    self._last_meter_report = None
                    self._last_meter_reports = None
                self._control_generation = generation
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
                    if isinstance(self.client, AsyncControlInputs):
                        grid_generation = self.client.last_grid_read_generation
                    if not injected_clock:
                        now = datetime.now(timezone.utc)
                    self.sensor_diagnostics = {
                        "pi": describe_sensor(raw_pi, self.options["pi_entity"],
                                              now, self.options["max_age_seconds"]),
                        "po": describe_sensor(raw_po, self.options["po_entity"],
                                              now, self.options["max_age_seconds"]),
                    }
                    fresh_sensors = [s for s in self.sensor_diagnostics.values() if s['health'] == 'FRESH']
                    if fresh_sensors:
                        meter_observation = {
                            'meter_w': None, 'communication_healthy': True,
                            'coverage': 'TWO_RECENT' if len(fresh_sensors) == 2 else 'ONE_RECENT',
                            'reason': 'Communication gPlugK récente ; bilan à vérifier',
                            'freshness_report': min(datetime.fromisoformat(s['reported_at']) for s in fresh_sensors),
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
                    meter_reports = tuple(_read_kw_state(raw, entity).measured_at for raw, entity in (
                        (raw_pi, self.options['pi_entity']), (raw_po, self.options['po_entity'])))
                    meter_fresh = tuple(self.sensor_diagnostics[name]['health'] == 'FRESH'
                                        for name in ('pi', 'po'))
                else:
                    # Backward-compatible pure unit-test client.
                    self.sensor_diagnostics = {"pi": None, "po": None}
                    measured = self.client.read_gplugk_grid_w(
                        self.options["pi_entity"], self.options["po_entity"])
                    if isinstance(self.client, AsyncControlInputs):
                        grid_generation = self.client.last_grid_read_generation
                    if not injected_clock:
                        now = datetime.now(timezone.utc)
                    age = (now - measured.measured_at).total_seconds()
                    if not measured.valid(now, self.options["max_age_seconds"]):
                        raise ValueError(f"gPlugK data too old or future-dated ({age:.0f} s)")
                    meter_w = measured.value
                    meter_report = measured.measured_at
                    freshness_report = measured.measured_at
                    meter_reports = (meter_report,)
                    meter_fresh = (True,)

                # Preserve the meter diagnosis independently of later thermal
                # or planner failures; it must still be recent when displayed.
                meter_observation = {
                    'meter_w': meter_w, 'communication_healthy': communication_healthy,
                    'coverage': measurement_coverage, 'reason': measurement_reason,
                    'freshness_report': freshness_report,
                }
                self.alarms.observe('A004', 'OK' if communication_healthy and meter_w is not None else
                                    'NON_VERIFIABLE' if communication_healthy else 'DEFAUT', now)

                temperature_invalid = True
                failure_stage = 'thermal'
                entity = self.options["boiler_temperature_entity"]
                if not entity:
                    raise ValueError('Sonde réelle du boiler non configurée')
                if entity:
                    if injected_clock:
                        # Preserve deterministic historical simulations using
                        # their explicitly supplied timeline.
                        temperature = self.client.read_temperature_c(entity)
                    else:
                        # One source of truth: use the temperature already
                        # validated by the independent thermal task. A second
                        # HA GET in the regulator can disagree with the guard
                        # and generate intermittent false diagnostic outcomes.
                        guarded = self.output.snapshot()
                        stamp = guarded.get('temperature_at')
                        if stamp is None or guarded.get('temperature_c') is None:
                            reason = self.boiler_temperature_diagnostic.get('reason', 'unavailable')
                            raise ValueError('Boiler temperature invalid: ' + str(reason)[:160])
                        temperature = Measurement(guarded['temperature_c'],
                                                  datetime.fromisoformat(stamp))
                        now = datetime.now(timezone.utc)
                    age_s = (now - temperature.measured_at).total_seconds()
                    if not valid_boiler_temperature(temperature, now, self.options["boiler_temperature_max_age_seconds"]):
                        raise ValueError(f"Boiler temperature invalid or stale (age={age_s:.1f} s; value={temperature.value} °C)")
                    water_c = temperature.value
                temperature_invalid = False
                self.alarms.observe('A003', 'OK', now)
                failure_stage = 'calculation'
                # Recheck meter freshness after any slow temperature request.
                if freshness_report is None or not -2 <= (now-freshness_report).total_seconds() <= self.options["max_age_seconds"]:
                    raise ValueError("Meter report stale after acquisition")
                if self._last_poll_at is not None and now < self._last_poll_at:
                    raise ValueError("Clock moved backwards; awaiting a new valid cycle")
                self._last_poll_at = now
                if self._last_meter_reports is not None and any(
                        current < previous for current, previous in zip(meter_reports, self._last_meter_reports)):
                    raise ValueError("Meter report moved backwards")
                new_sample = (self._last_meter_reports is None or any(
                    fresh and current > previous for current, previous, fresh
                    in zip(meter_reports, self._last_meter_reports, meter_fresh)))
                if new_sample:
                    receipt = getattr(self.client, 'last_grid_read_stamp', None)
                    validated = time.monotonic_ns()
                    interval_ms = ((validated - self.control_last_sample_ns) / 1e6
                                   if self.control_last_sample_ns is not None else None)
                    self.control_last_sample_ns = validated
                    self.control_sample_count += 1
                    timing = {'ha_report_at': meter_report.isoformat(),
                              'ha_report_ats': [stamp.isoformat() for stamp in meter_reports],
                              'cache_received_ns': int(receipt * 1e9) if receipt is not None else None,
                              'validated_ns': validated, 'validated_sample_count': self.control_sample_count,
                              'interval_since_previous_ms': interval_ms}
                    timing['calculation_start_ns'] = time.monotonic_ns()
                self.water_temperature_c = water_c
                self.comfort_estimate = None
                self.comfort = plan_comfort(self.options, now, water_c, self.profile)
                self.comfort["temperature_source"] = self.temperature_source
                # Regulate only when real AUTO is selected. The meter already
                # includes the current boiler load; never add an invented load.
                automatic = self.output.snapshot()['mode'] == 'AUTO_REEL'
                if not automatic:
                    self.regulator.invalid_data('Output not in AUTO')
                    decision = Decision(0, 'STOPPED' if self.output.snapshot()['mode'] == 'AUTO' else 'MANUAL',
                                        'No automatic output requested')
                elif measurement_status == 'POWER_UNKNOWN':
                    decision = self.regulator.invalid_data('Net power ambiguous')
                elif measurement_coverage == 'ONE_RECENT' and meter_w > 0:
                    decision = self.regulator.invalid_data('Incomplete import measurement: heating inhibited')
                else:
                    old_command = self.regulator.command_w
                    target = resolve_auto_target(self.options, self.weather, self.predictive, now)
                    limit = target['target_c']
                    if limit is None:
                        raise ValueError('Consigne calculée invalide ; AUTO inhibé')
                    reserve = self.options['target_export_w']
                    if self.weather_enabled:
                        reserve += self.options['weather_reserve_w']
                    decision = self.regulator.step(meter_w, water_c, temp_limit_c=limit,
                                                   target_export_w=reserve)
                    self.auto_applied = {**target, 'at': now.isoformat()}
                    if decision.state == 'AUTO_READY':
                        surplus_command = decision.command_w if new_sample else old_command
                        available = old_command - meter_w - reserve
                        surplus_command = min(surplus_command, max(0, available))
                        backup = self.comfort['backup_power_w']
                        backup_ok = (self.options['grid_backup_enabled'] and backup > 0
                                     and water_c < self.options['weather_min_c'])
                        total = surplus_command
                        if backup_ok:
                            ramped = (min(backup, old_command+300) if new_sample
                                      else min(backup, old_command))
                            gap_w = ((self.options['weather_min_c']-water_c)
                                     * self.options['weather_volume_l'] * 1.163
                                     * 3600 / self.options['sample_seconds'])
                            total = max(total, min(ramped, gap_w))
                        self.regulator.command_w = min(self.options['max_power_w'], total)
                        decision = Decision(self.regulator.command_w,
                                            'COMFORT_BACKUP' if backup_ok else decision.state,
                                            'Automatic demand calculated from valid measurements')
                self._last_meter_report = meter_report
                self._last_meter_reports = meter_reports
                self.comfort["gate_state"] = decision.state
                self.alarms.observe('A001', 'OK', now)
                self.comfort["applied_boiler_w"] = round(decision.command_w)
                self.latest = {
                    "status": measurement_status,
                    "error": None,
                    "data_quality": measurement_reason,
                    "communication_healthy": communication_healthy,
                    "measurement_coverage": measurement_coverage,
                    "updated_at": now.isoformat(),
                    "meter_w": round(meter_w) if meter_w is not None else None,
                    "requested_boiler_w": 0,
                    "meter_age_s": round(age, 1) if age is not None else None,
                    "control_state": decision.state,
                }
                if meter_w is not None:
                    self.history.append({
                        "time": now.strftime("%H:%M:%S"),
                        "meter_w": round(meter_w),
                    })
            except Exception as exc:
                if failure_stage == 'meter':
                    self.alarms.observe('A004', 'DEFAUT', now)
                    self.alarms.observe('A001', 'NON_VERIFIABLE', now)
                elif failure_stage == 'thermal':
                    self.alarms.observe('A003', 'DEFAUT', now)
                    self.alarms.observe('A001', 'NON_VERIFIABLE', now)
                elif failure_stage == 'calculation':
                    LOG.exception('Échec du calcul de régulation ; repli borné ou inhibition')
                    self.alarms.observe('A001', 'DEFAUT', now)
                    calculation_failed = True
                if not injected_clock:
                    now = datetime.now(timezone.utc)
                self.regulator.invalid_data(str(exc))
                self.water_temperature_c = None
                self.comfort_estimate = None
                if temperature_invalid or self.boiler_temperature_diagnostic.get('status') != 'FRESH':
                    try:
                        estimate = plan_comfort(self.options, now, 35.0, self.profile)
                        self.comfort_estimate = {
                            'assumed_temperature_c': 35.0,
                            'temperature_source': 'HYPOTHESIS_NOT_A_MEASUREMENT',
                            'reason': 'Estimation seule : sonde indisponible ; chauffe non autorisée par cette hypothèse.',
                            'plan': estimate,
                        }
                    except (ValueError, TypeError, KeyError, RuntimeError, ArithmeticError):
                        LOG.warning('Estimation de confort indisponible')
                report = meter_observation['freshness_report'] if meter_observation else None
                meter_age = (now-report).total_seconds() if report is not None else None
                meter_fresh = (meter_age is not None and -2 <= meter_age <= self.options['max_age_seconds'])
                meter_ok = bool(meter_observation and meter_fresh and meter_observation['communication_healthy'])
                retained_w = meter_observation['meter_w'] if meter_ok else None
                self.comfort = {"status": "FAULT", "reason": str(exc)[:260],
                                "backup_power_w": 0, "applied_boiler_w": 0,
                                "gate_state": "FAULT"}
                self.latest = {
                    "status": "FAULT",
                    "error": str(exc)[:260],
                    "data_quality": meter_observation['reason'] if meter_ok else None,
                    "communication_healthy": meter_ok,
                    "measurement_coverage": meter_observation['coverage'] if meter_ok else "NONE",
                    "updated_at": now.isoformat(),
                    "meter_w": round(retained_w) if retained_w is not None else None,
                    "requested_boiler_w": 0,
                    "meter_age_s": round(meter_age, 1) if meter_ok else None,
                    "control_state": "FAULT",
                }
            try:
                observation_i2c = None
                if timing is not None:
                    timing['setpoint_ready_ns'] = time.monotonic_ns()
                if self.output.inhibit_unready_dac():
                    self._report_auto_output_failure('main', 'AUTO_REEL')
                    self.latest['control_state'] = 'FAULT'
                    self.comfort['gate_state'] = 'FAULT'
                auto_healthy = self.latest['control_state'] in ('AUTO_READY', 'COMFORT_BACKUP', 'THERMAL_LIMIT')
                if self.output.snapshot()['mode'] == 'AUTO_REEL':
                    hb = self.heartbeat.snapshot()
                    auto_healthy = auto_healthy and hb['state'] == 'NORMAL' and hb['driver'] == 'GPIO_REEL'
                if isinstance(self.client, AsyncControlInputs) and grid_generation is not None:
                    # Publishing a newer cache entry and committing a command
                    # are serialized. A superseded calculation fails closed.
                    with self.client.lock:
                        if grid_generation != self.client.grid_generation:
                            self.regulator.invalid_data('Grid observation superseded')
                            self.latest['control_state'] = 'FAULT'
                            self.latest['status'] = 'FAULT'
                            self.latest['error'] = 'Grid observation superseded'
                            self.comfort['gate_state'] = 'FAULT'
                            auto_healthy = False
                        observation_i2c = self.output.observe_auto(
                            decision.command_w if self.latest['control_state'] != 'FAULT' else 0,
                            generation, auto_healthy, new_sample=new_sample, now=now)
                else:
                    observation_i2c = self.output.observe_auto(
                        decision.command_w if self.latest['control_state'] != 'FAULT' else 0,
                        generation, auto_healthy, new_sample=new_sample, now=now)
            except Exception as exc:
                observation_i2c = getattr(exc, 'i2c_timing', None)
                LOG.error('Défaut de commande AUTO du DAC ; état physique inconnu')
                self.latest['control_state'] = 'FAULT'
                self.comfort['gate_state'] = 'FAULT'
                # A failed automatic output cannot support a controlled heat
                # command. Latch the watchdog fault even during a pulse; a
                # later successful sensor poll must not silently rearm it.
                if isinstance(exc, OSError):
                    self._report_auto_output_failure('main', self.output.snapshot()['mode'])
                elif self.output.snapshot()['mode'] == 'AUTO_REEL':
                    LOG.exception('Commande AUTO invalide ; heartbeat bloqué')
                    self.alarms.observe('A001', 'DEFAUT', now)
                    self._heartbeat_capability('main', 'AUTO_REEL', blocked=True)
            if calculation_failed and self.output.snapshot()['mode'] == 'AUTO_REEL':
                try:
                    hb = self.heartbeat.snapshot()
                    if hb['state'] != 'NORMAL' or hb['driver'] != 'GPIO_REEL':
                        raise ValueError('Heartbeat non confirmé')
                    self.output.confirm_fallback_zero(now)
                    self.comfort['fallback'] = 'ZERO_CONFIRMED_NO_COMFORT_GUARANTEE'
                except OSError:
                    self._report_auto_output_failure('main', 'AUTO_REEL')
                except Exception:
                    LOG.exception('Repli zéro impossible ; heartbeat bloqué')
                    self._heartbeat_capability('main', 'AUTO_REEL', blocked=True)
            output = self.output.snapshot()
            if output['error'] or output['dac'].get('communication') == 'FAULT':
                self._latch_dac_fault()
            elif self.dac_fault_latched:
                # A previous ACK or a repeated poll cannot clear a latched fault.
                self.alarms.observe('A002', 'DEFAUT', now)
            elif output['mode'] in ('AUTO_REEL', 'MANUEL_REEL') and output['dac']['ready']:
                i2c = output.get('last_i2c') or {}
                ack_age = (time.monotonic_ns() - i2c.get('end_ns', 0)) / 1e9
                self.alarms.observe('A002', 'OK' if i2c.get('result') == 'ACK' and
                                    0 <= ack_age <= self.options['max_age_seconds'] else 'NON_VERIFIABLE', now,
                                    evidence_monotonic=i2c.get('end_ns', 0) / 1e9,
                                    evidence_ttl=self.options['max_age_seconds'])
            elif output['mode'] not in ('AUTO_REEL', 'MANUEL_REEL') and not output['error']:
                i2c = output.get('last_i2c') or {}
                test = self.dac_startup_self_test
                detail = 'DAC non sollicité ; communication non vérifiée en continu.'
                if test['result'] == 'OK':
                    detail += ' Dernier autotest à zéro réussi : ' + test['at'] + '.'
                self.alarms.observe('A002', 'NON_SOLLICITE', now, detail=detail,
                                    evidence_monotonic=(i2c['end_ns'] / 1e9 if i2c.get('result') == 'ACK' else None),
                                    evidence_ttl=self.options['max_age_seconds'])
            if timing is not None:
                timing['i2c'] = observation_i2c
                timing['outcome'] = ('ERROR' if self.latest['control_state'] == 'FAULT' else
                                     'WRITE' if timing['i2c'] else 'NO_WRITE')
                self.control_timing = timing
            if output['mode'] == 'AUTO_REEL' and output['auto_suspended'] and self.latest['control_state'] != 'FAULT':
                self.latest['control_state'] = 'INHIBITED'
                self.comfort['gate_state'] = 'INHIBITED'
            requested = (output['auto_requested_power_w'] if output['mode'] == 'AUTO_REEL'
                         else 0 if output['mode'] == 'AUTO' else None)
            self.latest['requested_boiler_w'] = round(requested) if requested is not None else None
            self.comfort['applied_boiler_w'] = self.latest['requested_boiler_w']
            # Progress is a liveness report, not an aggregate sensor-health
            # flag. An invalid AUTO calculation has already been inhibited by
            # observe_auto; MANUEL does not depend on the grid meter at all.
            # The thermal and guard tasks report progress independently; a
            # blocked poll cannot refresh this timestamp.
            self._heartbeat_capability('main', self.output.snapshot()['mode'])
            result = self.snapshot()
        if self.history_store is not None and self.record_queue is not None:
            try:
                self.record_queue.put_nowait((now, result))
            except Full:
                self.record_dropped += 1
                # Storage overload must never block the output controller.
        elif self.history_store is not None:
            self._record_telemetry(now, result)
        return result

    def _record_telemetry(self, now, result):
        if self.history_store is not None:
            try:
                if result['status'] in ('OK', 'POWER_UNKNOWN'):
                    thermal = result.get('boiler_temperature_diagnostic', {})
                    LOG.info('gPlugK meter=%s W | status %s | requested boiler %s W; boiler=%s C source=%s thermal_age=%s s',
                             result['meter_w'], result['status'], result['requested_boiler_w'],
                             result.get('water_temperature_c'), result.get('temperature_source'), thermal.get('age_s'))
                else:
                    LOG.warning('Mesure ou régulation indisponible : %s', result['error'])
                self.history_store.record(now, result)
                for alarm in result.get('application_health', {}).get('alarms', []):
                    if self._last_alarm_states.get(alarm['id']) != (alarm['state'], alarm['at']):
                        self.history_store.log(now, 'WARNING' if alarm['state'] == 'DEFAUT' else 'INFO',
                                               'ALARM', f"{alarm['id']} {alarm['state']}: {alarm['description']}")
                        self._last_alarm_states[alarm['id']] = (alarm['state'], alarm['at'])
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
                        f"requested_boiler={result.get('requested_boiler_w')} W; "
                        f"coverage={result.get('measurement_coverage')}")
                    self._last_logged_status = label
                    self._last_heartbeat = now
            except (OSError, ValueError, sqlite3.Error) as error:
                LOG.error("Noncritical telemetry storage failure: %s", error)

    def start_record_worker(self, capacity=128):
        if self.history_store is None or self.record_worker is not None:
            return
        if type(capacity) is not int or capacity < 1:
            raise ValueError('Telemetry queue capacity must be positive')
        self.record_queue = Queue(maxsize=capacity)
        def run():
            reported_dropped = 0
            while not self.record_stop.is_set() or not self.record_queue.empty():
                try:
                    now, result = self.record_queue.get(timeout=.2)
                except Empty:
                    continue
                try:
                    if self.record_dropped != reported_dropped:
                        LOG.error('Telemetry queue full: %d records lost', self.record_dropped)
                        reported_dropped = self.record_dropped
                    self._record_telemetry(now, result)
                except Exception:
                    LOG.exception('Telemetry worker failed to record an observation')
                finally:
                    self.record_queue.task_done()
        self.record_worker = Thread(target=run, name='telemetry-store', daemon=True)
        self.record_worker.start()

    def stop_record_worker(self, timeout=5):
        if self.record_worker is None:
            return True
        self.record_stop.set()
        self.record_worker.join(timeout)
        return not self.record_worker.is_alive()

    def _heartbeat_capability(self, task, mode, blocked=False):
        """Report task progress or latch a blocking AUTO control failure.

        Sensor faults are not passed as blocked; the arbiter retains its
        thermal, metering, permission and zero-output gates. Independently,
        HeartbeatService detects missed critical-task progress and GPIO faults.
        """
        self.heartbeat.progress(task, healthy=not (mode == 'AUTO_REEL' and blocked))

    def _report_auto_output_failure(self, task, mode):
        """A failed automatic DAC write latches the shared watchdog fault."""
        if mode == 'AUTO_REEL':
            self._heartbeat_capability(task, mode, blocked=True)
            self._latch_dac_fault()

    def _latch_dac_fault(self):
        self.alarms.observe('A002', 'DEFAUT')
        with self.fault_lock:
            if self.dac_fault_latched:
                return
            self.dac_fault_latched = True
            if self.user_settings_path is not None:
                try:
                    write_b3_settings(self.user_settings_path, {'dac_fault_latched': True})
                except (OSError, ValueError):
                    LOG.exception('Impossible de mémoriser le défaut DAC ; reprise non garantie après redémarrage')

    def refresh_control_temperature_once(self, now=None):
        injected = now is not None
        now = now or datetime.now(timezone.utc)
        measurement = None
        entity = self.options['boiler_temperature_entity']
        reason = None
        try:
            if entity and self.client is not None:
                # Only the configured boiler sensor may be refreshed.
                # Throttle before the call: failed requests must not retry-storm.
                if self.options['boiler_temperature_refresh_enabled']:
                    tick = time.monotonic()
                    if tick >= self._next_boiler_refresh_monotonic:
                        self._next_boiler_refresh_monotonic = (
                            tick + self.options['boiler_temperature_refresh_seconds'])
                        self._boiler_refresh_at = datetime.now(timezone.utc).isoformat()
                        try:
                            self.client.request_boiler_temperature_refresh(entity)
                            self._boiler_refresh_result = 'HA_REQUEST_OK_NOT_PHYSICAL_PROOF'
                            self._boiler_refresh_error = None
                        except (OSError, ValueError, RuntimeError, TypeError,
                                AttributeError) as error:
                            self._boiler_refresh_result = 'HA_REQUEST_FAILED'
                            self._boiler_refresh_error = str(error)[:160]
                            LOG.warning('Boiler HA update_entity request failed: %s', error)
                measurement = self.client.read_temperature_c(entity)
            else:
                reason = 'Boiler sensor not configured or HA unavailable'
        except (OSError, ValueError, TypeError, KeyError, RuntimeError) as error:
            reason = str(error)[:160]
        if not injected:
            now = datetime.now(timezone.utc)
        age = ((now - measurement.measured_at).total_seconds()
               if measurement is not None else None)
        if measurement is None:
            status = 'UNAVAILABLE'
            reason = reason or 'No readable HA temperature'
        elif age < -2:
            status = 'FUTURE'
            reason = f'HA boiler report is future-dated by {-age:.1f} s'
        elif age > self.options['boiler_temperature_max_age_seconds']:
            status = 'STALE'
            reason = f'HA boiler report aged {age:.1f} s (limit '
            reason += str(self.options['boiler_temperature_max_age_seconds']) + ' s)'
        elif not valid_boiler_temperature(measurement, now, self.options['boiler_temperature_max_age_seconds']):
            status = 'OUT_OF_RANGE'
            reason = 'Température du boiler anormale : plage valide 0 à 83 °C'
        else:
            status = 'FRESH'
            reason = 'Recent HA report; physical conversion not proven'
        relevant = bool(entity) or self.output.snapshot()['mode'] in ('AUTO_REEL', 'MANUEL_REEL')
        self.alarms.observe('A003', 'DESACTIVE' if not relevant else 'OK' if status == 'FRESH' else 'DEFAUT', now)
        self.boiler_temperature_diagnostic = {
            'entity': entity or None, 'status': status, 'reason': reason,
            'source': ('HA_TEMPLATE_LIVE_LAST_REPORTED' if entity and self.client is not None
                       else None),
            'value_c': measurement.value if measurement is not None else None,
            'report_at': (measurement.measured_at.isoformat()
                          if measurement is not None else None),
            'age_s': round(age, 1) if age is not None else None,
            'refresh_result': self._boiler_refresh_result,
            'refresh_error': self._boiler_refresh_error,
            'refresh_at': self._boiler_refresh_at,
            'max_age_s': self.options['boiler_temperature_max_age_seconds'],
        }
        if status != self._last_thermal_log_status:
            # Warn on transitions, not every 1-second poll; timestamps and age
            # make a stale report distinguishable from timezone or HTTP errors.
            log = LOG.info if status == 'FRESH' else LOG.warning
            log('Boiler thermal status=%s entity=%s value=%s C age=%s s '
                'report=%s refresh=%s detail=%s', status, entity or '<not configured>',
                self.boiler_temperature_diagnostic['value_c'],
                self.boiler_temperature_diagnostic['age_s'],
                self.boiler_temperature_diagnostic['report_at'],
                self._boiler_refresh_result, reason)
            self._last_thermal_log_status = status
        output_mode = self.output.snapshot()['mode']
        try:
            self.output.observe_temperature(measurement, self.options['boiler_max_temp_c'],
                                            self.options['boiler_temperature_max_age_seconds'], now)
        except OSError:
            LOG.error('DAC thermal stop failed; physical output unknown')
            self._report_auto_output_failure('thermal', output_mode)
        self._heartbeat_capability('thermal', self.output.snapshot()['mode'])

    def set_output(self, values):
        # Serialize operator selection with the startup arming sequence. An
        # explicit stop or mode selection always cancels pending startup.
        with self.startup_lock:
            previous_mode = self.output.snapshot()['mode']
            action = values.get('action') if isinstance(values, dict) else None
            # Stop must take effect even when the settings disk has failed.
            if action == 'stop' and set(values) == {'action'}:
                try:
                    if self.user_settings_path is not None:
                        write_b3_settings(self.user_settings_path, {'requested_mode': 'ARRET'})
                except (OSError, ValueError):
                    self.output.command(values)
                    self.startup_auto_pending = False
                    raise
                self.requested_mode = 'ARRET'
            try:
                self._set_output(values)
            except OSError:
                requested_auto = isinstance(values, dict) and values.get('mode') == 'AUTO_REEL'
                if requested_auto or previous_mode == 'AUTO_REEL':
                    self._heartbeat_capability('main', 'AUTO_REEL', blocked=True)
                self._latch_dac_fault()
                raise
            changes = {}
            if action == 'mode':
                requested = {'AUTO': 'ARRET', 'AUTO_REEL': 'AUTO', 'MANUEL_REEL': 'MANUEL'}.get(values.get('mode'))
                if requested:
                    changes['requested_mode'] = requested
                    if requested == 'MANUEL':
                        changes['manual_target_c'] = number(values.get('target_c', self.options['boiler_max_temp_c']), 30, self.options['boiler_max_temp_c'])
            elif action == 'manual':
                changes['manual_percent'] = self.output.snapshot()['manual_percent']
            elif action == 'recover':
                with self.fault_lock:
                    if self.user_settings_path is not None:
                        write_b3_settings(self.user_settings_path, {'dac_fault_latched': False})
                    self.dac_fault_latched = False
            if changes:
                try:
                    if self.user_settings_path is not None:
                        write_b3_settings(self.user_settings_path, changes)
                except (OSError, ValueError):
                    try:
                        self.output.command({'action': 'stop'})
                    finally:
                        self._heartbeat_capability('main', 'AUTO_REEL', blocked=True)
                    raise
                self.requested_mode = changes.get('requested_mode', self.requested_mode)
                self.options.update({k: v for k, v in changes.items() if k not in ('requested_mode', 'dac_fault_latched')})
            if action in ('stop', 'mode'):
                self.restore_status = None
            if values.get('action') in ('stop', 'mode', 'recover'):
                self.startup_auto_pending = False
                self.startup_reason = None

    def set_user_preferences(self, changes):
        """Validate and persist preferences without issuing an output command."""
        if not isinstance(changes, dict) or not changes or set(changes) - B3_FIELDS:
            raise ValueError('Champs de préférences interdits')
        if self.user_settings_path is None:
            raise ValueError('Stockage des préférences indisponible')
        with self.preferences_lock:
            with self.lock:
                if not self.options['boiler_ceiling_configured']:
                    raise ValueError('Plafond du boiler inconnu')
                if self.options['use_addon_settings'] and set(changes) & {'weather_min_c', 'comfort_time'}:
                    raise ValueError('Confort contrôlé par les options Supervisor')
                ceiling = self.options['boiler_max_temp_c']
                for key in ('auto_target_c', 'manual_target_c'):
                    if key in changes:
                        number(changes[key], 30, ceiling)
                candidate = {**self.options, **changes}
                if (candidate.get('auto_target_c') is not None and
                        number(candidate['auto_target_c'], 30, ceiling) < number(candidate['weather_min_c'], 30, ceiling)):
                    raise ValueError('La consigne AUTO doit atteindre le minimum de confort')
                validated = parse_options(candidate)
                saved = {key: validated[key] for key in changes}
            # Storage can be slow: never hold the fast control lock over fsync.
            write_b3_settings(self.user_settings_path, saved)
            with self.lock:
                self.options.update(saved)
                if set(changes) & {'auto_target_c', 'predictive_target_enabled', 'weather_min_c', 'comfort_time'}:
                    self.auto_applied = None
                    self.predictive = {'status': 'WAITING', 'target_c': None, 'fallback': True,
                                       'reason': 'Réglages modifiés ; nouveau calcul en attente.'}
                    self.profile_wake.set()

    def _set_output(self, values):
        if not isinstance(values, dict):
            raise ValueError('Expected command object')
        if values.get('action') == 'mode' and values.get('mode') in ('AUTO_REEL', 'MANUEL_REEL'):
            if self.dac_fault_latched:
                raise ValueError('Défaut DAC mémorisé : récupération explicite requise')
            if not self.options['boiler_temperature_entity']:
                raise ValueError('Sonde réelle du boiler requise')
            if not self.options['boiler_ceiling_configured']:
                raise ValueError('Plafond du boiler inconnu : renseigner boiler_max_temp_c')
            if not self.heartbeat.lock.acquire(blocking=False):
                raise ValueError('Watchdog occupé ; réessayer')
            try:
                hb = self.heartbeat.snapshot()
            finally:
                self.heartbeat.lock.release()
            if hb['state'] != 'NORMAL' or hb['driver'] != 'GPIO_REEL':
                raise ValueError('Heartbeat GPIO réel sain requis')
            # Do not hold the GPIO scheduler lock across an I²C transaction.
            # The guard independently inhibits output if the heartbeat fails.
            self.output.command(values)
            return
        if values.get('action') == 'stop':
            if set(values) != {'action'}:
                raise ValueError('Unexpected stop fields')
            self.output.command(values)
            return
        self.output.command(values)

    def begin_auto_start(self):
        """Request real AUTO at process startup; never restore an old command."""
        with self.startup_lock:
            self.startup_auto_pending = True
            self.startup_reason = 'AUTO demandé : initialisation en cours'
            self.startup_watchdog_started = False
            self.startup_zeroed = False
            self.startup_next_attempt = 0.0

    def restore_requested_mode(self):
        """Restore only persisted operator intent, never a previous output value."""
        if self.requested_mode == 'AUTO' and self.dac_fault_latched:
            self.startup_reason = 'Défaut DAC mémorisé : récupération explicite requise'
        elif self.requested_mode == 'AUTO':
            self.begin_auto_start()
        elif self.requested_mode == 'MANUEL':
            self.restore_status = 'Mode mémorisé : MANUEL — reprise en attente ; sélectionner MANUEL et confirmer la commande'

    def startup_dac_self_test(self):
        """One safe-zero initialization before workers and restoration start.

        A latched fault is never cleared by a diagnostic. No command, GPIO or
        watchdog operation is performed here; the output lock serializes I²C.
        """
        with self.startup_lock, self.output.lock:
            if self.dac_startup_self_test['result'] != 'NOT_RUN':
                return self.dac_startup_self_test
            if (self.dac_fault_latched or self.startup_auto_pending or
                    self.output.mode != 'AUTO' or self.output.enabled or self.output.dac.ready):
                self.dac_startup_self_test = {'result': 'SKIPPED', 'at': None}
                return self.dac_startup_self_test
            self.output._initialize()
            stamp = datetime.now(timezone.utc).isoformat()
            if self.output.error:
                self.dac_startup_self_test = {'result': 'ERROR', 'at': stamp}
                self._latch_dac_fault()
            else:
                self.output.dac.close()
                self.dac_startup_self_test = {'result': 'OK', 'at': stamp}
            if self.history_store is not None:
                try:
                    self.history_store.log(datetime.fromisoformat(stamp),
                        'INFO' if self.dac_startup_self_test['result'] == 'OK' else 'WARNING',
                        'DAC_SELF_TEST', 'Initialisation à zéro : ' + self.dac_startup_self_test['result'])
                except (OSError, sqlite3.Error):
                    LOG.exception('Impossible de journaliser l’autotest DAC')
            return self.dac_startup_self_test

    def startup_once(self):
        """Advance startup with safe zero, configured watchdog and live inputs."""
        with self.startup_lock:
            if not self.startup_auto_pending:
                return
            if self.dac_fault_latched:
                self.startup_reason = 'Défaut DAC mémorisé : récupération explicite requise'
                self.startup_auto_pending = False
                return
            if self.output.dac.kind != 'gp8600' or self.output.connection != 'boiler':
                self.startup_reason = 'AUTO inhibé : configurer le GP8600 et le raccordement boiler'
                return
            if not self.startup_zeroed:
                if time.monotonic() < self.startup_next_attempt:
                    return
                self.startup_next_attempt = time.monotonic() + 5
                try:
                    self.output.prepare_heartbeat()
                except OSError as exc:
                    self._latch_dac_fault()
                    self.startup_reason = 'AUTO inhibé : initialisation DAC impossible : ' + str(exc)[:120]
                    self.startup_auto_pending = False
                    return
                self.alarms.observe('A002', 'OK', evidence_monotonic=self.output.last_i2c['end_ns'] / 1e9,
                                    evidence_ttl=self.options['max_age_seconds'])
                self.startup_zeroed = True
                self.startup_next_attempt = 0.0
            hb = self.heartbeat.snapshot()
            if not self.startup_watchdog_started:
                if not self.options['heartbeat_autostart']:
                    self.startup_reason = 'AUTO inhibé : démarrage du watchdog non configuré'
                    return
                if time.monotonic() < self.startup_next_attempt:
                    return
                self.startup_next_attempt = time.monotonic() + 5
                # A GPIO open can stall independently of the DAC. Do not hold
                # the operator-command lock over it; recheck intent afterwards.
                self.startup_lock.release()
                try:
                    self.autostart_heartbeat()
                except (ValueError, OSError) as exc:
                    error = str(exc)[:120]
                else:
                    error = None
                finally:
                    self.startup_lock.acquire()
                if not self.startup_auto_pending:
                    return
                if error:
                    self.startup_reason = 'AUTO inhibé : watchdog ou initialisation DAC : ' + error
                    return
                self.startup_watchdog_started = True
                self.startup_next_attempt = 0.0
                hb = self.heartbeat.snapshot()
            if hb['state'] != 'NORMAL' or hb['driver'] != 'GPIO_REEL':
                self.startup_reason = 'AUTO inhibé : watchdog ' + str(hb['state'])
                return
            if not self.options['boiler_temperature_entity']:
                self.startup_reason = 'AUTO inhibé : sonde réelle du boiler non configurée'
                return
            if self.output.power_curve is None or not self.output.power_curve.validated:
                self.startup_reason = 'AUTO inhibé : calibration puissance/courant non confirmée'
                return
            if not self.options['boiler_ceiling_configured']:
                self.startup_reason = 'AUTO inhibé : plafond du boiler inconnu, renseigner boiler_max_temp_c'
                return
            if time.monotonic() < self.startup_next_attempt:
                return
            try:
                with self.output.lock:
                    self.output.command({'action': 'mode', 'mode': 'AUTO_REEL', 'confirmed': True})
                    # Atomically require a new meter report before any write.
                    self.output.revision += 1
                    self.output.auto_suspended = True
                    self.output.reason = 'AUTO prêt : attente d’une nouvelle mesure réseau'
            except (ValueError, OSError) as exc:
                if isinstance(exc, OSError):
                    self.startup_next_attempt = time.monotonic() + 5
                self.startup_reason = 'AUTO inhibé : ' + str(exc)[:120]
                return
            self.startup_auto_pending = False
            self.startup_reason = None

    def output_status(self):
        status = self.output.snapshot()
        if self.startup_auto_pending:
            status.update(auto_suspended=True, enabled=False,
                          heat_requested=False, reason=self.startup_reason,
                          auto_physical_enabled=False, startup_pending=True)
        else:
            status['startup_pending'] = False
            if self.startup_reason:
                status['reason'] = self.startup_reason
        status['requested_mode'] = self.requested_mode
        status['startup_self_test'] = dict(self.dac_startup_self_test)
        if self.restore_status and self.requested_mode == 'AUTO':
            if (status['mode'] == 'AUTO_REEL' and not status['auto_suspended'] and not status['error']
                    and self.heartbeat.snapshot()['state'] == 'NORMAL'):
                self.restore_status = 'Mode restauré : AUTO'
            else:
                self.restore_status = 'Mode mémorisé : AUTO — reprise en attente' + ((' : ' + self.startup_reason) if self.startup_reason else '')
        status['restore_status'] = self.restore_status
        status['saved_manual_target_c'] = self.options['manual_target_c']
        status['saved_manual_percent'] = self.options['manual_percent']
        return status

    def set_heartbeat(self, command):
        self._set_heartbeat(command)

    def autostart_heartbeat(self):
        """Start only an explicitly configured production profile, at DAC zero."""
        if not self.options['heartbeat_autostart']:
            return False
        if self.output.dac.kind != 'gp8600' or self.output.connection != 'boiler':
            raise ValueError('Démarrage watchdog : configurer le DAC réel et le câblage boiler')
        self.set_heartbeat({'action': 'start', 'driver': 'gpio17', 'confirm': True,
                            'period_s': self.options['heartbeat_period_s'],
                            'width_s': self.options['heartbeat_width_s']})
        return True

    def autostart_idle_heartbeat(self):
        """Arm the configured watchdog even without a saved AUTO request.

        The AUTO restoration path owns its own safe-zero/watchdog sequence.
        This path prepares the DAC at zero but never selects a heating mode.
        A failed initialization stays stopped and must not retry on its own.
        """
        if not self.options['heartbeat_autostart'] or self.startup_auto_pending:
            return False
        if self.dac_fault_latched:
            LOG.error('Démarrage heartbeat refusé : défaut DAC mémorisé')
            return False
        try:
            started = self.autostart_heartbeat()
        except (OSError, ValueError) as exc:
            LOG.error('Démarrage heartbeat refusé : %s', exc)
            return False
        if started:
            LOG.info('Heartbeat armé hors reprise AUTO ; aucune commande de chauffe rétablie')
        return started

    def _set_heartbeat(self, command):
        if not isinstance(command, dict):
            raise ValueError('Expected heartbeat command')
        if command == {'action': 'stop'}:
            self.output.command({'action': 'stop'})
            self.heartbeat.stop()
            return
        if (set(command) != {'action', 'driver', 'confirm', 'period_s', 'width_s'}
                or command['action'] != 'start' or command['confirm'] is not True):
            raise ValueError('Explicit heartbeat confirmation required')
        if self.dac_fault_latched:
            raise ValueError('Défaut DAC mémorisé : récupération explicite requise')
        period = number(command['period_s'], .1, 3600)
        width = number(command['width_s'], .001, period)
        if width >= period or width > self.options['max_age_seconds'] or self.heartbeat.snapshot()['state'] != 'ARRET':
            raise ValueError('Arrêter le heartbeat et vérifier sa temporisation')
        if command['driver'] == 'gpio17':
            timeout = self.options['heartbeat_task_timeout_s']
            # H3DK-M1 catalogue: input >= 50 ms, reset >= 100 ms.
            if width < .05 or period - width < .1:
                raise ValueError('Impulsion GPIO >= 0,05 s et repos >= 0,1 s requis')
            if timeout < 2 * self.options['sample_seconds']:
                raise ValueError('Le délai watchdog doit couvrir deux périodes de régulation')
            try:
                self.output.prepare_heartbeat()
            except OSError:
                self._latch_dac_fault()
                raise
            self.alarms.observe('A002', 'OK', evidence_monotonic=self.output.last_i2c['end_ns'] / 1e9,
                                evidence_ttl=self.options['max_age_seconds'])
            output = GPIOOutput('/dev/gpiochip0', 17, active_high=True)
        else:
            raise ValueError('Unknown heartbeat output')
        # A heartbeat cannot outlive a stalled acquisition/regulation loop.
        self.heartbeat.start(output, ['main', 'thermal', 'guard'],
                             period=period, width=width,
                             timeout=self.options['heartbeat_task_timeout_s'])

    def guard_output_once(self):
        output_mode = self.output.snapshot()['mode']
        try:
            self.output.check_freshness()
        except OSError:
            self._report_auto_output_failure('guard', output_mode)
            raise
        if self.output.inhibit_unready_dac():
            self._report_auto_output_failure('guard', 'AUTO_REEL')
        self._heartbeat_capability('guard', self.output.snapshot()['mode'])
        with self.startup_lock:
            state = self.output.snapshot()
            if state['mode'] == 'AUTO_REEL' and state['error']:
                self._report_auto_output_failure('guard', state['mode'])
            if (not self.startup_auto_pending and state['mode'] == 'AUTO_REEL'
                    and state['error'] and not state['dac']['ready']
                    and self.heartbeat.snapshot()['state'] != 'DEFAUT'):
                self.startup_auto_pending = True
                self.startup_reason = 'AUTO inhibé : réinitialisation du DAC à zéro en attente'
                self.startup_zeroed = False
                self.startup_watchdog_started = True
                self.startup_next_attempt = time.monotonic() + 5
        self.startup_once()
        if (self.output.snapshot()['mode'] in ('AUTO_REEL', 'MANUEL_REEL')
                and self.heartbeat.snapshot()['state'] != 'NORMAL'):
            self.output.command({'action': 'stop'})

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
            temperature = self.water_temperature_c
            previous = dict(self.predictive)
        self.refresh_weather_once(now)
        try:
            profile = make_hourly_profile(options, now, self.client)
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as error:
            profile = {
                "status": "UNAVAILABLE", "points": [],
                "solar_error": "Erreur profil : " + str(error)[:180],
                "meteo_error": None,
            }
        try:
            predictive = predictive_target(profile, temperature, options['weather_min_c'],
                options['boiler_max_temp_c'], options['weather_volume_l'], now,
                options['predictive_target_enabled'], previous)
            predictive['source_updated_at'] = profile.get('daily_pv_updated_at')
        except (ValueError, TypeError):
            predictive = {'status': 'UNAVAILABLE', 'target_c': None, 'fallback': True,
                          'reason': 'Température du boiler indisponible ou invalide : repli Alpha.17.'}
        with self.lock:
            if options != self.options:
                return self.profile  # discard an in-flight response for old settings
        with self.lock:
            if options != self.options:
                return self.profile
            self.profile = profile
            self.predictive = predictive
        weather_needed = options['weather_enabled'] or options['pac_enabled']
        solar_needed = options['weather_enabled'] or options['predictive_target_enabled']
        meteo = profile.get('meteo_status')
        solar = profile.get('solar_status')
        daily = profile.get('daily_pv_status')
        solar_valid = solar in ('OK', 'PARTIAL') or daily == 'OK'
        if options['predictive_target_enabled']:
            # Test the forecast independently of a missing real probe. This
            # calculation diagnoses coverage only and cannot command heat.
            try:
                coverage = predictive_target(profile, 35.0, options['weather_min_c'],
                    options['boiler_max_temp_c'], options['weather_volume_l'], now, True)
                solar_valid = coverage.get('status') == 'OK'
            except (ValueError, TypeError, KeyError):
                solar_valid = False
        current = profile.get('current_temperature_c')
        current_error = profile.get('current_temperature_error')
        # PAC needs today's integrated hourly model and its independently
        # validated tomorrow source. The comfort planner itself consumes a
        # heater budget (and falls back conservatively when unavailable), not
        # a full 48-hour outdoor-temperature series.
        pac_reason = None
        if options['pac_enabled']:
            try:
                if options['pac_temperature_source'] == 'open_meteo':
                    read_tomorrow_from_ha(self.client, options['pac_weather_entity'], now,
                                          options['pac_timezone'])
                else:
                    outdoor_forecast_c(self.client.get_state(options['pac_forecast_temp_entity']), now)
            except (ValueError, OSError, TypeError, KeyError, AttributeError, TimeoutError) as exc:
                pac_reason = 'prévision PAC de demain insuffisante : ' + str(exc)[:140]
            if profile.get('pac_today_remaining_kwh') is None:
                pac_reason = (pac_reason + ' ; ' if pac_reason else '') + 'prévision PAC du reste du jour incomplète'
        weather_state = ('DESACTIVE' if not weather_needed else
                         'DEFAUT' if current is None and (current_error or meteo == 'UNAVAILABLE') else
                         'NON_VERIFIABLE' if current is None else
                         ('DEFAUT' if meteo == 'UNAVAILABLE' else 'NON_VERIFIABLE') if pac_reason else
                         'OK')
        detail = ('Température actuelle disponible ; ' if current is not None else
                  'Température actuelle indisponible ; ' if current_error else 'Température actuelle non vérifiée ; ')
        detail += {'OK': 'prévisions horaires complètes.', 'PARTIAL': 'prévisions horaires partielles.',
                   'UNAVAILABLE': 'prévisions horaires indisponibles.'}.get(meteo, 'prévisions horaires non vérifiées.')
        if pac_reason:
            detail += ' ' + pac_reason + '.'
        # A slow forecast request must not publish an obsolete configuration.
        with self.lock:
            if options != self.options:
                return self.profile
            self.alarms.observe('A005', weather_state, now, detail=detail if weather_needed else None)
            self.alarms.observe('A006', 'DESACTIVE' if not solar_needed else
                                'OK' if solar_valid else
                                'NON_VERIFIABLE' if solar in (None, 'WAITING') else 'DEFAUT', now)
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
            previous_error = self.profile.get('current_temperature_error')
            self.profile["current_temperature_c"] = temperature
            self.profile["current_temperature_error"] = error
            weather_needed = self.options['weather_enabled'] or self.options['pac_enabled']
        if weather_needed and error:
            self.alarms.observe('A005', 'DEFAUT', detail='Température actuelle indisponible : ' + error)
        if weather_needed and bool(previous_error) != bool(error):
            self.profile_wake.set()

    def profile_loop(self, stop: Event) -> None:
        """Refresh forecasts slowly and live weather without blocking gPlugK."""
        next_forecast = None
        while not stop.is_set():
            now = datetime.now(timezone.utc)
            if next_forecast is None or now >= next_forecast:
                result = self.refresh_profile_once(now)
                next_forecast = now + timedelta(seconds=1800 if result["status"] == "OK"
                    and (not self.options['predictive_target_enabled'] or self.predictive.get('status') == 'OK') else 300)
            else:
                self.refresh_current_weather_once()
            self.profile_wake.wait(120)
            if self.profile_wake.is_set():
                next_forecast = None
                self.profile_wake.clear()

    def snapshot(self) -> dict:
        # Only invoke this under the lock, or from HTTP with lock held.
        output = self.output_status()
        heartbeat = self.heartbeat.snapshot()
        health = self.alarms.snapshot()
        dac_alarm = next(row for row in health['alarms'] if row['id'] == 'A002')
        physical_mode = output['mode'] in ('AUTO_REEL', 'MANUEL_REEL')
        if self.dac_fault_latched or output['error']:
            dac_alarm.update(state='DEFAUT', description='Défaut DAC mémorisé ou erreur I²C ; récupération explicite requise.')
        elif physical_mode and dac_alarm['state'] != 'DEFAUT':
            age = output.get('last_ack_age_s')
            if age is None or not 0 <= age <= self.options['max_age_seconds']:
                dac_alarm.update(state='NON_VERIFIABLE', description='Commande physique active : ACK I²C récent absent ; courant physique non mesuré.')
            elif dac_alarm['state'] == 'NON_SOLLICITE':
                dac_alarm.update(state='OK', description='ACK I²C récent ; courant physique non mesuré.')
        states = {row['state'] for row in health['alarms']}
        reasons = []
        if heartbeat['state'] == 'DEFAUT':
            reasons.append('Heartbeat en défaut')
        elif physical_mode and (heartbeat['state'] != 'NORMAL' or heartbeat.get('driver') != 'GPIO_REEL'):
            reasons.append('Heartbeat requis non confirmé')
        health['state'] = ('ALARME' if 'DEFAUT' in states or heartbeat['state'] == 'DEFAUT' else
                           'NON_VERIFIABLE' if 'NON_VERIFIABLE' in states or reasons else 'OK')
        health['reasons'] = reasons
        return {
            "version": "1.0.0-alpha.25",
            "weather_toggle_token": self.weather_toggle_token,
            "read_only": False,
            "temperature_source": self.temperature_source,
            "water_temperature_c": self.water_temperature_c,
            "boiler_temperature_diagnostic": dict(self.boiler_temperature_diagnostic),
            "comfort": dict(self.comfort),
            "comfort_estimate": self.comfort_estimate,
            "predictive": dict(self.predictive),
            "auto_target": (resolve_auto_target(self.options, self.weather, self.predictive, datetime.now(timezone.utc))
                            if self.options['boiler_ceiling_configured'] else None),
            "auto_applied": dict(self.auto_applied) if self.auto_applied else None,
            "forecast_configuration_migrated": bool(self.migrated_forecast_solar_entry_id),
            "physical_outputs": "GP8600_COMMANDE_NON_VERIFIEE",
            "output_control": output,
            "heartbeat": heartbeat,
            "application_health": health,
            "options": dict(self.options),
            **self.latest,
            "history": list(self.history),
            "telemetry_queue_dropped": self.record_dropped,
            "control_timing": self.control_timing,
            "sensors": dict(self.sensor_diagnostics),
            "weather": dict(self.weather),
            "profile": dict(self.profile),
        }

    def loop(self, stop: Event) -> None:
        while not stop.is_set():
            wake = getattr(self.client, 'grid_wake', None)
            if wake is not None:
                wake.clear()
            record = self.poll_once()
            if wake is None:
                stop.wait(self.options["sample_seconds"])
            else:
                # Periodic fallback retains freshness and watchdog checks;
                # changed HA cache entries trigger the next cycle sooner.
                wake.wait(self.options["sample_seconds"])


def handler_factory(diag: Diagnostic):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path == '/api/heartbeat-events':
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream; charset=utf-8')
                self.send_header('Cache-Control', 'no-store')
                self.send_header('X-Accel-Buffering', 'no')
                self.end_headers()
                cursor = diag.heartbeat.pulse_cursor()
                try:
                    while True:
                        events, missed = diag.heartbeat.wait_for_pulses(cursor, timeout=1)
                        snapshot = diag.heartbeat.snapshot()
                        for event in events:
                            payload = json.dumps({'pulse': event, 'missed': missed,
                                                  'state': snapshot['state'],
                                                  'period_s': snapshot.get('period_s'),
                                                  'width_s': snapshot.get('width_s'),
                                                  'pulse_age_s': snapshot.get('last_pulse_age_s')
                                                  if event['at'] == snapshot.get('last_pulse_at') else None}, allow_nan=False)
                            self.wfile.write(('event: pulse\ndata: ' + payload + '\n\n').encode())
                            cursor = event['sequence']
                        payload = json.dumps(snapshot, ensure_ascii=False, allow_nan=False)
                        self.wfile.write(('event: state\ndata: ' + payload + '\n\n').encode())
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass
                return
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
            elif path == "/api/output":
                body = json.dumps({**diag.output_status(),
                                   "boiler_temperature_diagnostic": dict(diag.boiler_temperature_diagnostic),
                                   "token": diag.weather_toggle_token},
                                  ensure_ascii=False, allow_nan=False).encode('utf-8')
                content_type = 'application/json; charset=utf-8'
            elif path == "/api/heartbeat":
                body = json.dumps({**diag.heartbeat.snapshot(), "token": diag.weather_toggle_token},
                                  ensure_ascii=False, allow_nan=False).encode('utf-8')
                content_type = 'application/json; charset=utf-8'
            elif path == "/api/dac-measurements":
                state = diag.output.snapshot()['dac']
                body = json.dumps(diag.calibration.snapshot(state['driver'], state.get('address')),
                                  ensure_ascii=False, allow_nan=False).encode('utf-8')
                content_type = 'application/json; charset=utf-8'
            elif path == "/theme.css":
                body = THEME_CSS.read_bytes()
                content_type = "text/css; charset=utf-8"
            elif path == "/theme.js":
                body = THEME_JS.read_bytes()
                content_type = "application/javascript; charset=utf-8"
            elif path in ("/ui.js", "/dashboard.js", "/chart.js", "/output.js", "/power-gauge.js", "/heartbeat_status.js"):
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
                        "requested_boiler_w", "control_state", "comfort")}
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
            # Authenticated Ingress session. Only OutputControl can write the DAC.
            path = self.path.split("?", 1)[0]
            if path not in ("/api/output", "/api/heartbeat", "/api/preferences"):
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
                if set(content) != {'command', 'token'}:
                    raise ValueError('Expected command and token fields')
                if path == '/api/output':
                    diag.set_output(content['command'])
                elif path == '/api/preferences':
                    diag.set_user_preferences(content['command'])
                else:
                    diag.set_heartbeat(content['command'])
            except (OSError, sqlite3.Error):
                self.send_error(503, "Operation failed; inspect output state")
                return
            except (ValueError, TypeError, UnicodeError) as exc:
                # Browser clients need the actual validation reason. JSON
                # encoding keeps it data, never markup.
                message = str(exc)[:240] or 'Requête invalide'
                body = json.dumps({'error': message}, ensure_ascii=False).encode('utf-8')
                self.send_response(400)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.send_header('Cache-Control', 'no-store')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
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
    LOG.info("Démarrage Surplus PV : restauration des modes sous conditions")
    config_file = Path(os.environ.get("SPV_OPTIONS_FILE", "/data/options.json"))
    options = json.loads(config_file.read_text(encoding="utf-8"))
    token = os.environ.get("SUPERVISOR_TOKEN") or os.environ.get("HASSIO_TOKEN") or ""
    client = (AsyncControlInputs(HomeAssistantClient(token),
                                 max_cache_age=max(15, 2 * int(options.get('sample_seconds', 10))),
                                 process_isolation=True)
              if token else None)
    store = None
    try:
        store = HistoryStore(os.environ.get("SPV_HISTORY_DB",
                                           "/data/surplus_pv_history.sqlite3"))
        store.log(datetime.now(timezone.utc), "INFO", "START",
                  "Surplus PV diagnostic started, persistent history active")
    except (OSError, sqlite3.Error) as error:
        LOG.error("Persistent storage unavailable: %s", error)
    calibration = CalibrationStore(os.environ.get('SPV_DAC_DB', '/data/surplus_pv_dac.sqlite3'))
    diag = Diagnostic(options, client, history_store=store, calibration_store=calibration,
                      user_settings_path=os.environ.get("SPV_USER_SETTINGS_FILE",
                                                      "/data/surplus_pv_user_settings.json"))
    diag.start_record_worker()
    stop = Event()
    diag.startup_dac_self_test()
    diag.restore_requested_mode()
    def thermal_loop():
        while not stop.is_set():
            diag.refresh_control_temperature_once()
            stop.wait(1)
    def guard_loop():
        while not stop.is_set():
            try:
                diag.guard_output_once()
            except OSError:
                LOG.error('DAC stop failure; physical output unknown')
                diag.heartbeat.progress('guard')
            stop.wait(0.25)
    thermal_worker = Thread(target=thermal_loop, daemon=True)
    guard_worker = Thread(target=guard_loop, daemon=True)
    heartbeat_worker = Thread(target=diag.heartbeat.run, args=(stop,), daemon=True)
    thermal_worker.start()
    guard_worker.start()
    heartbeat_worker.start()
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
    # The watchdog autostart setting also applies to ARRET / no saved mode.
    # The AUTO restore path starts it separately after its safe-zero checks.
    diag.autostart_idle_heartbeat()
    server = ThreadingHTTPServer(("0.0.0.0", 8099), handler_factory(diag))
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        stop.set()
        if isinstance(client, AsyncControlInputs):
            client.close()
        server.server_close()
        try:
            diag.heartbeat.stop()
        except OSError:
            LOG.error('Heartbeat GPIO stop failed; physical level unknown')
        try:
            diag.output.close()
        except OSError:
            LOG.error('Shutdown DAC zero failed; independent isolation required')
        thermal_worker.join(timeout=2)
        guard_worker.join(timeout=2)
        heartbeat_worker.join(timeout=2)
        calibration.close()
        worker.join(timeout=5)
        profile_worker.join(timeout=5)
        pv_worker.join(timeout=15)
        if store is not None and not worker.is_alive() and diag.stop_record_worker():
            store.close()
        elif store is not None:
            LOG.error('Control or telemetry worker still blocked at shutdown; database left open')


if __name__ == "__main__":
    main()

