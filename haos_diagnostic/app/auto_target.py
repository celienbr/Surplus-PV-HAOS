"""Resolve the AUTO target without changing hardware or comfort decisions."""
from datetime import datetime
from math import isfinite


def _fresh(value, now):
    try:
        stamp = datetime.fromisoformat(value)
        return -300 <= (now - stamp).total_seconds() <= 10800
    except (ValueError, TypeError, OverflowError):
        return False


def resolve_auto_target(options, weather, predictive, now):
    """Report the target used for AUTO, with a hard ceiling and real origin.

    The pre-existing adaptive-weather fallback is kept when prediction is
    selected but unavailable. A user-selected target is otherwise authoritative.
    """
    ceiling = float(options['boiler_max_temp_c'])
    user = options.get('auto_target_c', ceiling)
    user = ceiling if user is None else float(user)
    if not isfinite(user) or not 30 <= user <= ceiling:
        raise ValueError('Consigne AUTO hors limites')
    target, source = user, 'user'
    # Existing installations without a B3 preference retain Alpha.18's
    # adaptive-weather behaviour, including when predictive mode is disabled.
    legacy = options.get('auto_target_c') is None
    if options.get('predictive_target_enabled') or legacy:
        if (options.get('predictive_target_enabled') and predictive.get('status') == 'OK'
                and _fresh(predictive.get('calculated_at'), now)
                and _fresh(predictive.get('source_updated_at'), now)):
            candidate = predictive.get('target_c')
            if not isinstance(candidate, (int, float)) or isinstance(candidate, bool) or not isfinite(candidate) or candidate < 30:
                return {'target_c': None, 'source': 'invalid'}
            target, source = candidate, 'forecast'
        elif (options.get('weather_enabled') and weather.get('status') == 'ADAPTIVE'
              and _fresh(weather.get('forecast_timestamp'), now)):
            candidate = weather.get('target_c')
            if not isinstance(candidate, (int, float)) or isinstance(candidate, bool) or not isfinite(candidate) or candidate < 30:
                return {'target_c': None, 'source': 'invalid'}
            target, source = candidate, 'weather_fallback'
    return {'target_c': min(ceiling, max(30., target)), 'source': source}
