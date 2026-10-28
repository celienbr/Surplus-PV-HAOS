"""Validation of measurement age and units before calculating a command."""
from dataclasses import dataclass
from datetime import datetime, timezone
from math import isfinite


@dataclass(frozen=True)
class Measurement:
    value: float
    measured_at: datetime

    def valid(self, now: datetime, max_age_s: float) -> bool:
        if self.measured_at.tzinfo is None or now.tzinfo is None:
            return False
        age_s = (now - self.measured_at).total_seconds()
        return isfinite(self.value) and -2.0 <= age_s <= max_age_s


def parse_ha_number(state: dict, *, expected_unit: str) -> Measurement:
    raw = state.get("state")
    if raw in (None, "unknown", "unavailable", ""):
        raise ValueError(f"HA measurement unavailable: {raw!r}")
    value = float(raw)
    if not isfinite(value):
        raise ValueError("Non-finite measurement")
    actual_unit = state.get("attributes", {}).get("unit_of_measurement")
    if actual_unit != expected_unit:
        raise ValueError(f"Expected unit {expected_unit!r}; got {actual_unit!r}")
    raw_time = state.get("last_updated")
    if not raw_time:
        raise ValueError("Missing Home Assistant last_updated timestamp")
    timestamp = datetime.fromisoformat(raw_time.replace("Z", "+00:00"))
    if timestamp.tzinfo is None:
        raise ValueError("Missing measurement timezone")
    return Measurement(value, timestamp.astimezone(timezone.utc))
