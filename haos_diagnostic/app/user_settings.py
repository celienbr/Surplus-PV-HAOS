"""Versioned UI preferences; installation settings never enter the UI write API."""
import json
import os
from pathlib import Path

USER_FIELDS = frozenset({"boiler_max_temp_c", "weather_min_c", "comfort_time",
                         "weather_enabled", "pac_enabled", "grid_backup_enabled"})
LEGACY_V2_FIELDS = (USER_FIELDS - {"boiler_max_temp_c"}) | {"demo_max_c"}
LEGACY_FIELDS = (LEGACY_V2_FIELDS - {"grid_backup_enabled"}) | {"forecast_solar_entry_id"}


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
          and set(saved["settings"]) in (USER_FIELDS, LEGACY_V2_FIELDS)):
        legacy_id = saved["migrated_forecast_solar_entry_id"]
        saved = saved["settings"]
    else:
        raise ValueError("Invalid saved user settings schema")
    if not isinstance(legacy_id, str):
        raise ValueError("Invalid migrated Forecast.Solar ID")
    if 'demo_max_c' in saved:
        # Preserve the old v2 file before rewriting the schema for rollback.
        backup = path.with_name(path.name + '.v2.backup')
        if not backup.exists():
            with backup.open('xb') as stream:
                stream.write(path.read_bytes())
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(backup, 0o600)
        saved['boiler_max_temp_c'] = saved.pop('demo_max_c')
    # Explicit non-empty application configuration wins. Retain the old value
    # internally until the owner transfers it to Supervisor configuration.
    use_addon_settings = options.get('use_addon_settings', False)
    if type(use_addon_settings) is not bool:
        raise ValueError('Invalid addon settings migration choice')
    merged = dict(options) if use_addon_settings else {**options, **saved}
    merged['legacy_settings_active'] = not use_addon_settings
    if merged.get('boiler_max_temp_c') is None:
        merged['boiler_max_temp_c'] = saved['boiler_max_temp_c']
    if options.get('boiler_max_temp_c') is not None:
        merged['boiler_max_temp_c'] = options['boiler_max_temp_c']
    merged["forecast_solar_entry_id"] = options.get("forecast_solar_entry_id") or legacy_id
    return merged, legacy_id
