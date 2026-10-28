"""Adaptive hot-water ceiling, hardware-free and deterministic.

All solar energy inputs are estimates of PV energy available for boiler heating
AFTER household demand, priority consumers and prospective battery allocation.
Never use raw PV production as if it were available surplus.

This adviser is NOT wired to HA, I2C, GPIO or electrical power.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from math import isfinite


@dataclass(frozen=True)
class SurplusOutlook:
    updated_at: datetime
    remaining_today_kwh: float
    tomorrow_kwh: float
    # Must encompass tomorrow's complete local forecast window.
    covers_tomorrow: bool = True


@dataclass(frozen=True)
class TemperatureStrategy:
    state: str
    target_c: float
    high_limit_c: float
    minimum_c: float
    forecast_today_kwh: float | None
    forecast_tomorrow_kwh: float | None
    estimated_shortage_tomorrow_kwh: float | None
    reserved_export_w: float
    explanation: str


def adaptive_temperature(
    *,
    now: datetime,
    water_temperature_c: float,
    minimum_c: float,
    high_limit_c: float,
    forecast_enabled: bool,
    outlook: SurplusOutlook | None,
    volume_l: float = 300,
    comfort_buffer_c: float = 5,
    estimated_demand_tomorrow_kwh: float = 5,
    solar_confidence: float = 0.65,
    reserve_other_uses_w: float = 0,
    max_forecast_age_h: float = 3,
    base_export_margin_w: float = 100,
) -> TemperatureStrategy:
    """Calculate the *ceiling* for surplus heating, not a guaranteed target.

    A forecast for a sunny tomorrow reduces the optional heat storage target.
    A cloudy tomorrow raises it to cover forecast thermal deficit.
    Heat over the minimum is permitted ONLY from measured surplus, not grid
    imports. Any guarantee of the minimum by deadline uses separate logic.
    """
    vals = (water_temperature_c, minimum_c, high_limit_c, volume_l,
            comfort_buffer_c, estimated_demand_tomorrow_kwh,
            solar_confidence, reserve_other_uses_w, max_forecast_age_h,
            base_export_margin_w)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Timezone-aware current time required")
    if not all(isfinite(float(x)) for x in vals):
        raise ValueError("Invalid numerical parameter")
    if not -10 <= water_temperature_c <= 100:
        raise ValueError("Invalid water temperature")
    if not 30 <= minimum_c <= high_limit_c <= 80:
        raise ValueError("Invalid high and minimum setpoints")
    if not 0 < volume_l <= 2000:
        raise ValueError("Invalid hot water volume")
    if not 0 <= comfort_buffer_c <= 20:
        raise ValueError("Invalid comfort buffer")
    if not 0 <= estimated_demand_tomorrow_kwh <= 30:
        raise ValueError("Invalid next-day heat demand")
    if not 0 < solar_confidence <= 1 or not 0 < max_forecast_age_h <= 24:
        raise ValueError("Invalid confidence / maximum age")
    if not 0 <= reserve_other_uses_w <= 6000 or not 0 <= base_export_margin_w <= 1000:
        raise ValueError("Invalid reserve/margin")
    if base_export_margin_w + reserve_other_uses_w > 6000:
        raise ValueError("Unrealistic total export reservation")

    reserved = base_export_margin_w + reserve_other_uses_w if forecast_enabled else base_export_margin_w

    def fallback(state: str, explanation: str) -> TemperatureStrategy:
        return TemperatureStrategy(
            state=state,
            target_c=round(high_limit_c, 1),
            high_limit_c=high_limit_c,
            minimum_c=minimum_c,
            forecast_today_kwh=None,
            forecast_tomorrow_kwh=None,
            estimated_shortage_tomorrow_kwh=None,
            reserved_export_w=reserved,
            explanation=explanation,
        )

    if not forecast_enabled:
        return fallback("OFF", "Météo désactivée : retour à la consigne haute fixe.")
    if outlook is None or not outlook.covers_tomorrow:
        return fallback("UNAVAILABLE",
                        "Prévisions absentes/incomplètes : consigne haute manuelle conservée.")
    if outlook.updated_at.tzinfo is None:
        return fallback("UNAVAILABLE", "Date de mise à jour des prévisions invalide.")
    forecast_values = (outlook.remaining_today_kwh, outlook.tomorrow_kwh)
    if not all(isfinite(x) and 0 <= x <= 100 for x in forecast_values):
        return fallback("UNAVAILABLE", "Prévision énergétique invalide.")
    age = (now.astimezone(timezone.utc) -
           outlook.updated_at.astimezone(timezone.utc)).total_seconds() / 3600
    if age < -5/60 or age > max_forecast_age_h:
        return fallback("STALE", "Prévision périmée : repli sur la consigne haute fixe.")

    # Lower bound above the guaranteed comfort minimum, but never above the
    # user-defined manual high-temperature limit.
    baseline_c = min(high_limit_c, minimum_c + comfort_buffer_c)
    available_tomorrow = outlook.tomorrow_kwh * solar_confidence
    shortage_kwh = max(0.0, estimated_demand_tomorrow_kwh - available_tomorrow)

    # 1 litre × 1 kelvin ≈ 1.163 Wh thermal storage.
    extra_c = shortage_kwh * 1000 / (volume_l * 1.163)
    target = max(minimum_c, min(high_limit_c, baseline_c + extra_c))
    energy_to_target_kwh = max(0.0, target - water_temperature_c) * volume_l * 1.163 / 1000

    if shortage_kwh < 0.05:
        explanation = ("Soleil utile prévu demain : limiter le stockage thermique "
                       "et garder du surplus pour les autres usages.")
    else:
        explanation = ("Déficit solaire prévu demain : précharger le boiler "
                       "aujourd'hui uniquement avec le surplus effectivement mesuré.")
    if outlook.remaining_today_kwh < energy_to_target_kwh - 0.2:
        explanation += " Surplus restant aujourd'hui probablement insuffisant pour atteindre cette cible."
    if reserve_other_uses_w > 0:
        explanation += (" Réserve d'injection indicative : les futurs contrôleurs "
                        "de batterie devront confirmer la priorité réelle.")

    return TemperatureStrategy(
        state="ADAPTIVE",
        target_c=round(target, 1),
        high_limit_c=high_limit_c,
        minimum_c=minimum_c,
        forecast_today_kwh=round(outlook.remaining_today_kwh, 2),
        forecast_tomorrow_kwh=round(outlook.tomorrow_kwh, 2),
        estimated_shortage_tomorrow_kwh=round(shortage_kwh, 2),
        reserved_export_w=round(reserved),
        explanation=explanation,
    )
