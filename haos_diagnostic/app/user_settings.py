"""Versioned UI preferences; installation settings never enter the UI write API."""
import json
import os
import tempfile
import fcntl
from pathlib import Path

USER_FIELDS = frozenset({"boiler_max_temp_c", "weather_min_c", "comfort_time",
                         "weather_enabled", "pac_enabled", "grid_backup_enabled"})
LEGACY_V2_FIELDS = (USER_FIELDS - {"boiler_max_temp_c"}) | {"demo_max_c"}
LEGACY_FIELDS = (LEGACY_V2_FIELDS - {"grid_backup_enabled"}) | {"forecast_solar_entry_id"}
B3_FIELDS = frozenset({'auto_target_c', 'predictive_target_enabled', 'weather_min_c',
                       'comfort_time', 'manual_target_c', 'manual_percent'})
STORED_FIELDS = B3_FIELDS | {'requested_mode', 'dac_fault_latched'}


def stop_marker(path: Path) -> Path:
    return path.with_name(path.name + '.stop')


def remember_stop(path: Path):
    """Keep ARRÊT durable even if replacement of the preferences fails."""
    marker = stop_marker(path)
    try:
        with marker.open('xb') as stream:
            os.fchmod(stream.fileno(), 0o600)
            os.fsync(stream.fileno())
    except FileExistsError:
        pass
    sync_directory(path.parent)


def sync_directory(path: Path):
    directory = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def read_settings(path: Path, options: dict) -> tuple[dict, str]:
    if not path.exists():
        return options, ""
    saved = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(saved, dict):
        raise ValueError("Invalid saved user settings")
    b3 = {}
    if saved.get('schema_version') == 3:
        if set(saved) != {'schema_version', 'legacy', 'b3'} or not isinstance(saved['b3'], dict) or set(saved['b3']) - STORED_FIELDS:
            raise ValueError('Invalid B3 settings schema')
        if 'requested_mode' in saved['b3'] and saved['b3']['requested_mode'] not in ('ARRET', 'AUTO', 'MANUEL'):
            raise ValueError('Invalid saved mode')
        if 'dac_fault_latched' in saved['b3'] and type(saved['b3']['dac_fault_latched']) is not bool:
            raise ValueError('Invalid saved DAC fault')
        b3, saved = saved['b3'], saved['legacy']
        if saved is None:
            return _merge_b3(options, b3), ''
        if not isinstance(saved, dict):
            raise ValueError('Invalid legacy settings')
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
    return _merge_b3(merged, b3), legacy_id


def _merge_b3(options, b3):
    merged = dict(options)
    # Supervisor remains authoritative for the legacy comfort fields when
    # use_addon_settings is active. B3 never changes the physical ceiling.
    for name, value in b3.items():
        if name in ('weather_min_c', 'comfort_time') and options.get('use_addon_settings', False):
            continue
        merged[name] = value
    return merged


def write_b3_settings(path: Path, values: dict):
    """Atomic replacement, preserving the complete old schema for rollback.

    Caller must serialize and validate values before invoking this function.
    """
    if not isinstance(values, dict) or set(values) - STORED_FIELDS:
        raise ValueError('Champs de préférences interdits')
    if 'requested_mode' in values and values['requested_mode'] not in ('ARRET', 'AUTO', 'MANUEL'):
        raise ValueError('Mode mémorisé invalide')
    if 'dac_fault_latched' in values and type(values['dac_fault_latched']) is not bool:
        raise ValueError('Défaut DAC mémorisé invalide')
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(path.name + '.lock')
    with lock_path.open('a+b') as lock:
        os.chmod(lock_path, 0o600)
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        _write_b3_settings_locked(path, values)
        # A new mode supersedes the stop marker only after the mode is durable.
        if 'requested_mode' in values and stop_marker(path).exists():
            stop_marker(path).unlink()
            sync_directory(path.parent)


def _write_b3_settings_locked(path: Path, values: dict):
    old = json.loads(path.read_text(encoding='utf-8')) if path.exists() else None
    if old is not None and not isinstance(old, dict):
        raise ValueError('Invalid saved user settings')
    if old is not None and old.get('schema_version') == 3:
        if set(old) != {'schema_version', 'legacy', 'b3'} or not isinstance(old['b3'], dict) or set(old['b3']) - STORED_FIELDS:
            raise ValueError('Invalid B3 settings schema')
        legacy, previous = old['legacy'], old['b3']
    else:
        if old is not None and not (set(old) == LEGACY_FIELDS or
                (set(old) == {'schema_version', 'settings', 'migrated_forecast_solar_entry_id'}
                 and old.get('schema_version') == 2 and isinstance(old.get('settings'), dict)
                 and set(old['settings']) in (USER_FIELDS, LEGACY_V2_FIELDS))):
            raise ValueError('Invalid saved user settings schema')
        legacy, previous = old, {}
    payload = {'schema_version': 3, 'legacy': legacy, 'b3': {**previous, **values}}
    if old is not None and old.get('schema_version') != 3:
        backup = path.with_name(path.name + '.pre-b3.backup')
        try:
            with backup.open('xb') as f:
                os.chmod(backup, 0o600)
                f.write(path.read_bytes()); f.flush(); os.fsync(f.fileno())
        except FileExistsError:
            pass
    fd, temporary = tempfile.mkstemp(prefix='.spv-settings-', dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(payload, f, ensure_ascii=False, allow_nan=False)
            f.flush(); os.fsync(f.fileno())
        os.replace(temporary, path)
        sync_directory(path.parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
