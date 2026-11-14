"""Validation commune de la sonde d'eau pour la commande et le confort."""

BOILER_MAX_VALID_C = 83.0


def valid_boiler_temperature(measurement, now, max_age_s):
    return (measurement is not None and measurement.valid(now, max_age_s)
            and 0 <= measurement.value <= BOILER_MAX_VALID_C)
