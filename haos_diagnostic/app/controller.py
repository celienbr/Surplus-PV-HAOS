"""Pure, hardware-free PV surplus regulation.

Conventions: grid_w > 0 is import; grid_w < 0 is export.
The grid meter ALREADY includes the boiler's consumption.
"""
from dataclasses import dataclass
from math import isfinite


@dataclass(frozen=True)
class Parameters:
    max_power_w: float = 6000.0
    target_export_w: float = 100.0
    gain: float = 0.5
    max_step_w: float = 300.0
    temp_limit_c: float = 60.0  # EXAMPLE only: validate boiler limits before production
    temp_hysteresis_c: float = 3.0

    def __post_init__(self) -> None:
        numeric = (self.max_power_w, self.target_export_w, self.gain,
                   self.max_step_w, self.temp_limit_c, self.temp_hysteresis_c)
        if not all(isfinite(float(x)) for x in numeric):
            raise ValueError("Controller parameters must be finite")
        if not 0 < self.max_power_w <= 6000:
            raise ValueError("Invalid boiler power")
        if not 0 < self.gain <= 1:
            raise ValueError("Gain must be within (0, 1]")
        if self.max_step_w <= 0:
            raise ValueError("Maximum step must be positive")
        if not 0 <= self.target_export_w <= 2000:
            raise ValueError("Export margin outside prototype envelope")
        if not 30 <= self.temp_limit_c <= 80:
            raise ValueError("Temperature limit outside prototype envelope")
        if not 0 < self.temp_hysteresis_c <= 20:
            raise ValueError("Invalid temperature hysteresis")


@dataclass(frozen=True)
class Decision:
    command_w: float
    state: str
    explanation: str


class SurplusRegulator:
    def __init__(self, params: Parameters):
        self.params = params
        self.command_w = 0.0
        self.temperature_allows_heating = True
        self._last_temp_limit_c = None

    def invalid_data(self, reason: str) -> Decision:
        """Stop logical AUTO power; a real watchdog would cease toggling.

        Note: when hardware is eventually present, the *physical* relay reverts
        to the S2 manual selection, which may still heat from the grid.
        """
        self.command_w = 0.0
        return Decision(0.0, "FAULT", reason)

    def step(self, grid_w: float, water_temp_c: float, *, temp_limit_c: float | None = None,
             target_export_w: float | None = None) -> Decision:
        if not (isfinite(grid_w) and -30000 <= grid_w <= 30000):
            return self.invalid_data("Invalid grid power")
        if not (isfinite(water_temp_c) and -20 <= water_temp_c <= 110):
            return self.invalid_data("Invalid water temperature")

        p = self.params
        # An adaptive target is a *ceiling*, never a way to bypass the manual
        # temperature maximum. The value comes from the forecast adviser.
        limit = p.temp_limit_c if temp_limit_c is None else temp_limit_c
        if not isfinite(limit) or not 30 <= limit <= p.temp_limit_c:
            return self.invalid_data("Invalid adaptive temperature ceiling")
        # If the forecast raises the target after a previous thermal shutdown,
        # permit heating again when we are safely below the new limit.
        if (self._last_temp_limit_c is not None
                and limit > self._last_temp_limit_c
                and water_temp_c <= limit - p.temp_hysteresis_c):
            self.temperature_allows_heating = True
        self._last_temp_limit_c = limit
        if water_temp_c >= limit:
            self.temperature_allows_heating = False
        elif water_temp_c <= limit - p.temp_hysteresis_c:
            self.temperature_allows_heating = True

        if not self.temperature_allows_heating:
            # No ramp-down delay on thermal regulation cutoff.
            self.command_w = 0.0
            return Decision(0.0, "THERMAL_LIMIT", "Temperature target reached")

        # A closed-loop correction; do NOT interpret remaining export as an
        # absolute load demand. Nonlinear CELDUC mapping is a separate problem.
        desired_export = p.target_export_w if target_export_w is None else target_export_w
        if not isfinite(desired_export) or not 0 <= desired_export <= 6000:
            return self.invalid_data("Invalid priority export margin")
        target_grid_w = -desired_export
        correction_w = p.gain * (target_grid_w - grid_w)
        desired_w = max(0.0, min(p.max_power_w, self.command_w + correction_w))
        delta_w = max(-p.max_step_w, min(p.max_step_w, desired_w - self.command_w))
        self.command_w = max(0.0, min(p.max_power_w, self.command_w + delta_w))
        return Decision(self.command_w, "AUTO_READY_SIMULATION", "Valid data; logical command only")
