"""Illustrative air-source heat-pump electrical demand vs outdoor temperature.

Not an EN 14825 model, not calibrated; no COP claim for a particular machine.
A calibrated reference/day must be supplied by the installation owner.
This model estimates NEXT-DAY consumption only, never commands a device.
"""
from dataclasses import dataclass
from math import isfinite


@dataclass(frozen=True)
class PacEstimate:
    temperature_c: float
    load_factor: float
    cop_assumed: float
    energy_kwh: float


def forecast_pac_kwh(*, outside_c: float, reference_electric_kwh: float,
                     reference_temp_c: float = 7.0,
                     balance_temp_c: float = 16.0,
                     reference_cop: float = 3.5,
                     cop_change_per_c: float = 0.1) -> PacEstimate:
    """Calculate day-ahead electric PAC consumption with two effects:

    Heating needs ∝ (balance temperature - ambient outdoor temperature).
    Electrical efficiency (COP) is estimated lower at cold temperatures.
    Day's reference electric consumption must EXCLUDE other house loads.
    """
    numeric = (outside_c, reference_electric_kwh, reference_temp_c,
               balance_temp_c, reference_cop, cop_change_per_c)
    if not all(isinstance(x, (float, int)) and
               not isinstance(x, bool) and isfinite(x) for x in numeric):
        raise ValueError("Invalid PAC parameter")
    if not -35 <= outside_c <= 50 or not 0 <= reference_electric_kwh <= 70:
        raise ValueError("PAC temperature or reference energy outside limits")
    if not -15 <= reference_temp_c < balance_temp_c <= 25:
        raise ValueError("Invalid PAC reference and balance temperatures")
    if not 1.1 <= reference_cop <= 8 or not 0 <= cop_change_per_c <= 0.3:
        raise ValueError("Invalid PAC COP assumptions")

    heating_load = max(0.0, balance_temp_c - outside_c)
    reference_load = balance_temp_c - reference_temp_c
    factor = min(5.0, heating_load / reference_load)
    cop = max(1.2, min(8.0,
                      reference_cop + cop_change_per_c *
                      (outside_c - reference_temp_c)))
    electric = min(100.0,
                   reference_electric_kwh * factor * reference_cop / cop)
    return PacEstimate(outside_c, factor, round(cop, 2),
                       round(electric, 2))
