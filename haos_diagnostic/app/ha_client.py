"""READ-ONLY access to the local Home Assistant REST API."""
import json
import urllib.error
import urllib.parse
import urllib.request

from .measurements import parse_ha_number
from .gplugk import net_grid_power_w


class HomeAssistantClient:
    def __init__(self, token: str, base_url: str = "http://supervisor/core/api"):
        if not token:
            raise ValueError("SUPERVISOR_TOKEN is required in read_only mode")
        self._token = token
        self._base_url = base_url.rstrip("/")

    def get_state(self, entity_id: str) -> dict:
        if (not entity_id.startswith(("sensor.", "weather."))
                or not all(ch.isalnum() or ch in "._" for ch in entity_id)):
            raise ValueError("Expected a valid sensor.* or weather.* entity ID")
        url = self._base_url + "/states/" + urllib.parse.quote(entity_id, safe=".")
        request = urllib.request.Request(url, headers={
            "Authorization": "Bearer " + self._token,
            "Content-Type": "application/json",
        })
        with urllib.request.urlopen(request, timeout=4) as response:
            return json.loads(response.read(65536).decode("utf-8"))

    def get_weather_forecast(self, entity_id: str, forecast_type: str) -> list:
        """Read weather forecast ONLY via HA's data-returning weather action.

        HA exposes this read as an HTTP POST. It is allowlisted strictly to
        weather/get_forecasts; no equipment control or state write is possible.
        """
        if not (entity_id.startswith("weather.") and
                all(ch.isalnum() or ch in "._" for ch in entity_id)):
            raise ValueError("Expected a valid weather.* entity ID")
        if forecast_type not in ("hourly", "daily"):
            raise ValueError("Unsupported weather forecast type")
        url = self._base_url + "/services/weather/get_forecasts?return_response"
        body = json.dumps({"entity_id": entity_id,
                           "type": forecast_type}).encode("utf-8")
        request = urllib.request.Request(
            url, data=body, method="POST",
            headers={
                "Authorization": "Bearer " + self._token,
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(request, timeout=12) as response:
            result = json.loads(response.read(131072).decode("utf-8"))
        # Rest API wraps response_variable in service_response, not directly.
        if not isinstance(result, dict):
            raise ValueError("Invalid HA weather response")
        data = result.get("service_response")
        if not isinstance(data, dict) or entity_id not in data:
            raise ValueError("Weather response entity not found")
        entity = data[entity_id]
        if not isinstance(entity, dict) or not isinstance(entity.get("forecast"), list):
            raise ValueError("Weather forecast list unavailable")
        return entity["forecast"]

    def get_solar_forecast(self, config_entry_id: str) -> dict:
        """Query-only Forecast.Solar get_forecast action (no control service)."""
        if not isinstance(config_entry_id, str) or not (8 <= len(config_entry_id) <= 64) or not all(
                ch.isalnum() or ch in "_-" for ch in config_entry_id):
            raise ValueError("Invalid Forecast.Solar config-entry ID")
        url = self._base_url + "/services/forecast_solar/get_forecast?return_response"
        payload = json.dumps({"config_entry": config_entry_id,
                              "resolution": "hourly"}).encode("utf-8")
        request = urllib.request.Request(
            url, data=payload, method="POST",
            headers={
                "Authorization": "Bearer " + self._token,
                "Content-Type": "application/json",
            })
        with urllib.request.urlopen(request, timeout=12) as response:
            answer = json.loads(response.read(262144).decode("utf-8"))
        # Home Assistant REST API for service actions returning data.
        if not isinstance(answer, dict):
            raise ValueError("Invalid Forecast.Solar service response")
        series = answer.get("service_response")
        if not isinstance(series, dict) or not isinstance(series.get("watts"), dict):
            raise ValueError("Forecast.Solar service response lacks watts")
        return series

    def read_power_w(self, entity_id: str):
        return parse_ha_number(self.get_state(entity_id), expected_unit="W")

    def read_gplugk_states(self, import_entity_id: str, export_entity_id: str):
        """Read both raw HA states without writing or changing either entity."""
        return (self.get_state(import_entity_id), self.get_state(export_entity_id))

    def read_gplugk_grid_w(self, import_entity_id: str, export_entity_id: str):
        """Read verified gPlugK Pi/Po (kW); return net grid power in W."""
        import_state, export_state = self.read_gplugk_states(
            import_entity_id, export_entity_id
        )
        return net_grid_power_w(import_state, export_state)

    def read_temperature_c(self, entity_id: str):
        state = self.get_state(entity_id)
        unit = state.get("attributes", {}).get("unit_of_measurement")
        # HA generally reports °C; do not silently convert Fahrenheit.
        if unit != "°C":
            raise ValueError(f"Temperature unit must be °C, got {unit!r}")
        # HA last_reported can advance on an unchanged temperature; it still
        # proves only an HA report, not the physical sensor acquisition time.
        return parse_ha_number({**state, "last_updated": state.get("last_reported")
                                or state.get("last_updated")}, expected_unit="°C")
