"""Read next-day temperature from Home Assistant Open-Meteo weather forecasts.

Uses only the documented weather.get_forecasts *query* action. No HA entity
state is written and no power/control action is called.

Daily high/low midpoint is clearly tagged as an approximation if the
hourly forecast cannot cover most of tomorrow.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from math import isfinite, ceil
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


@dataclass(frozen=True)
class TomorrowTemperature:
    temperature_c: float
    method: str
    samples: int
    expected_hours: int
    forecast_date: str


def _zone(timezone_name: str):
    try:
        return ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError, KeyError) as error:
        raise ValueError("Invalid or unavailable forecast timezone") from error


def _tomorrow(now: datetime, zone):
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise ValueError("Timezone-aware now required")
    return now.astimezone(zone).date() + timedelta(days=1)


def _time(raw):
    if not isinstance(raw, str):
        raise ValueError("Forecast time absent")
    try:
        stamp = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("Forecast time malformed") from error
    if stamp.tzinfo is None:
        raise ValueError("Forecast must have timezone")
    return stamp


def _temperature(raw):
    try:
        temp = float(raw)
    except (TypeError, ValueError) as error:
        raise ValueError("Forecast temperature not numeric") from error
    if not isfinite(temp) or not -35 <= temp <= 50:
        raise ValueError("Forecast temperature outside plausible Celsius limits")
    return temp


def _expected_hours(day, zone):
    start = datetime(day.year, day.month, day.day, tzinfo=zone)
    following = day + timedelta(days=1)
    end = datetime(following.year, following.month, following.day, tzinfo=zone)
    return int((end.astimezone(timezone.utc) -
                start.astimezone(timezone.utc)).total_seconds() / 3600)


def hourly_tomorrow(rows, now: datetime,
                    timezone_name: str = "Europe/Zurich") -> TomorrowTemperature:
    zone = _zone(timezone_name)
    day = _tomorrow(now, zone)
    if not isinstance(rows, list) or len(rows) > 200:
        raise ValueError("Invalid hourly weather forecast")
    unique = {}
    for item in rows:
        if not isinstance(item, dict):
            raise ValueError("Malformed hourly weather point")
        instant = _time(item.get("datetime"))
        if instant.astimezone(zone).date() != day:
            continue
        temp = _temperature(item.get("temperature"))
        key = instant.astimezone(timezone.utc).isoformat()
        if key in unique:
            raise ValueError("Duplicate forecast hour")
        unique[key] = temp
    expected = _expected_hours(day, zone)  # 23/24/25 hours with DST
    minimum = ceil(0.80 * expected)
    if len(unique) < minimum:
        raise ValueError(
            f"Tomorrow's hourly forecast is incomplete: "
            f"{len(unique)}/{expected} h, minimum {minimum}")
    return TomorrowTemperature(
        temperature_c=round(sum(unique.values()) / len(unique), 1),
        method="HOURLY_MEAN",
        samples=len(unique),
        expected_hours=expected,
        forecast_date=day.isoformat(),
    )


def daily_tomorrow(rows, now: datetime,
                   timezone_name: str = "Europe/Zurich") -> TomorrowTemperature:
    zone = _zone(timezone_name)
    day = _tomorrow(now, zone)
    if not isinstance(rows, list) or len(rows) > 30:
        raise ValueError("Invalid daily weather forecast")
    for item in rows:
        if not isinstance(item, dict):
            raise ValueError("Malformed daily weather point")
        if _time(item.get("datetime")).astimezone(zone).date() != day:
            continue
        high = _temperature(item.get("temperature"))
        low = _temperature(item.get("templow"))
        if low > high:
            raise ValueError("Daily weather high below low")
        return TomorrowTemperature(
            temperature_c=round((high + low) / 2.0, 1),
            method="DAILY_MIDPOINT_APPROX",
            samples=2,
            expected_hours=_expected_hours(day, zone),
            forecast_date=day.isoformat(),
        )
    raise ValueError("Open-Meteo daily forecast does not cover tomorrow")


def read_tomorrow_from_ha(client, entity_id: str, now: datetime,
                          timezone_name: str = "Europe/Zurich") -> TomorrowTemperature:
    if not entity_id.startswith("weather.") or not all(
            character.isalnum() or character in "._" for character in entity_id):
        raise ValueError("Expected configured weather.* entity")
    # The forecast action uses HA's configured display temperature units.
    current = client.get_state(entity_id)
    if current.get("state") in (None, "unavailable", "unknown"):
        raise ValueError("Open-Meteo weather entity unavailable")
    unit = current.get("attributes", {}).get("temperature_unit")
    if unit != "°C":
        raise ValueError(f"Open-Meteo weather temperature must use °C, got {unit!r}")
    hourly_error = None
    try:
        points = client.get_weather_forecast(entity_id, "hourly")
        return hourly_tomorrow(points, now, timezone_name)
    except (OSError, TimeoutError, ValueError, KeyError, TypeError) as error:
        hourly_error = str(error)
    try:
        points = client.get_weather_forecast(entity_id, "daily")
        return daily_tomorrow(points, now, timezone_name)
    except (OSError, TimeoutError, ValueError, KeyError, TypeError) as error:
        raise ValueError(
            "Open-Meteo tomorrow unavailable; hourly: " +
            str(hourly_error)[:115] + "; daily: " + str(error)[:115]
        ) from error
