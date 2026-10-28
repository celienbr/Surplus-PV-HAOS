"""Versioned UI preferences; installation settings never enter the UI write API."""
import json
import os
from pathlib import Path

USER_FIELDS = frozenset({"demo_max_c", "weather_min_c", "comfort_time",
                         "weather_enabled", "pac_enabled", "grid_backup_enabled"})
LEGACY_FIELDS = (USER_FIELDS - {"grid_backup_enabled"}) | {"forecast_solar_entry_id"}


def read_settings(path: Path, options: dict) -> tuple[dict, str]:
    if not path.exists():
        return options, ""
    saved = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(saved, dict):
        raise ValueError("Invalid saved user settings")
    if set(saved) == LEGACY_FIELDS:
        backup = path.with_name(path.name + ".v1.backup")
        if not backup.exists():
            # Preserve exact legacy JSON for rollback; never overwrite it.
            with backup.open("x", encoding="utf-8") as stream:
                json.dump(saved, stream, ensure_ascii=False, allow_nan=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(backup, 0o600)
        legacy_id = saved.pop("forecast_solar_entry_id")
        saved["grid_backup_enabled"] = False  # migration cannot authorize import
    elif (set(saved) == {"schema_version", "settings", "migrated_forecast_solar_entry_id"}
          and saved["schema_version"] == 2 and isinstance(saved["settings"], dict)
          and set(saved["settings"]) == USER_FIELDS):
        legacy_id = saved["migrated_forecast_solar_entry_id"]
        saved = saved["settings"]
    else:
        raise ValueError("Invalid saved user settings schema")
    if not isinstance(legacy_id, str):
        raise ValueError("Invalid migrated Forecast.Solar ID")
    # Explicit non-empty application configuration wins. Retain the old value
    # internally until the owner transfers it to Supervisor configuration.
    merged = {**options, **saved}
    merged["forecast_solar_entry_id"] = options.get("forecast_solar_entry_id") or legacy_id
    return merged, legacy_id


def save_settings(path: Path, options: dict, migrated_id: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    data = {"schema_version": 2,
            "settings": {key: options[key] for key in sorted(USER_FIELDS)},
            "migrated_forecast_solar_entry_id": migrated_id}
    try:
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
