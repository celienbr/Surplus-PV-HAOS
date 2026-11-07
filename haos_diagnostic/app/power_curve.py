"""Bounded interpolation of independently measured power/current pairs.

This module does not operate a device or assert that zero current inhibits heat.
"""
from __future__ import annotations

from .gp8600 import number


class PowerCurve:
    def __init__(self, points, *, source, validated=False):
        if source not in ("simulation", "measurement"):
            raise ValueError("Unknown curve provenance")
        if type(validated) is not bool or (validated and source != "measurement"):
            raise ValueError("Only measured curves can be validated")
        if not isinstance(points, (list, tuple)) or len(points) < 2:
            raise ValueError("At least two calibration points are required")
        result = []
        for point in points:
            if not isinstance(point, (list, tuple)) or len(point) != 2:
                raise ValueError("Each point must contain power and current")
            power = number(point[0], 0, 6000)
            current = number(point[1], 4, 20)
            if result and (power <= result[-1][0] or current <= result[-1][1]):
                raise ValueError("Calibration power and current must increase strictly")
            result.append((power, current))
        self.points = tuple(result)
        self.source = source
        self.validated = validated

    def current_for_power(self, power_w):
        power = number(power_w, self.points[0][0], self.points[-1][0])
        for (p0, i0), (p1, i1) in zip(self.points, self.points[1:]):
            if power <= p1:
                return i0 + (i1-i0) * (power-p0) / (p1-p0)
        return self.points[-1][1]
