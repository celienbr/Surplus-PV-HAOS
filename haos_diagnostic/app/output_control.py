"""Single writer for AUTO / MANUEL / BANC_DAC. No relay or HA write API.

AUTO retains alpha.3's virtual regulator. Only explicit session commands arm
the DAC in MANUEL or BANC_DAC. A mode transition clears all old setpoints.
"""
from __future__ import annotations

from datetime import datetime, timezone
from threading import RLock
from time import monotonic

from .gp8600 import number
from .power_curve import PowerCurve


class OutputControl:
    def __init__(self, dac, connection="bench", auto_timeout=60, max_power=6000):
        if connection not in ("bench", "boiler"):
            raise ValueError("DAC connection must be bench or boiler")
        self.dac, self.connection = dac, connection
        self.auto_timeout, self.max_power = auto_timeout, max_power
        self.power_curve = PowerCurve([(0, 4), (max_power, 20)], source="simulation")
        self.lock = RLock()
        self.mode = "AUTO"
        self.revision = 0
        self.enabled = False
        self.requested_ma = self.manual_percent = 0.
        self.target_c = None
        self.auto_deadline = None
        self.reason = "Démarrage en AUTO ; aucune consigne reprise"
        self.auto_power_w = 0.
        self.temperature = None
        self.ceiling = 70.
        self.max_age = 60.
        self.error = None
        # A real device is not opened until an explicitly confirmed bench mode.
        if self.dac.kind == "simulated":
            with self.lock:
                self._initialize()
        elif self.connection == "boiler":
            self.reason = "Démarrage sans commande ; état physique inconnu"

    def _initialize(self):
        try:
            self.dac.initialize()
            self.error = None
        except OSError as exc:
            self.error = str(exc)[:160]
            self.reason = "I²C indisponible ; sortie physique inconnue"

    def _clear(self, reason):
        self.revision += 1
        self.mode, self.enabled = "AUTO", False
        self.requested_ma = self.manual_percent = self.auto_power_w = 0.
        self.target_c = None
        self.auto_deadline = None
        self.reason = reason

    def _write(self, ma):
        try:
            self.dac.set_current(ma)
        except OSError as exc:
            self._clear("Erreur DAC ; reprise explicite à zéro nécessaire")
            self.error = str(exc)[:160]
            raise

    def _temperature_ok(self, now):
        t = self.temperature
        return (t is not None and t.valid(now, self.max_age)
                and 0 <= t.value <= 90)

    def observe_temperature(self, measurement, ceiling, max_age, now=None):
        now = now or datetime.now(timezone.utc)
        with self.lock:
            valid = measurement is not None and measurement.valid(now, max_age) and 0 <= measurement.value <= 90
            self.temperature, self.ceiling, self.max_age = measurement if valid else None, ceiling, max_age
            if self.mode in ("MANUEL", "MANUEL_BANC", "MANUEL_REEL", "AUTO_REEL"):
                if not self._temperature_ok(now):
                    self._stop("Température invalide ou périmée ; retour AUTO")
                elif self.mode != "AUTO_REEL" and self.temperature.value >= min(self.target_c, ceiling):
                    self._stop("Consigne thermique atteinte ; retour AUTO")
                elif self.mode == "AUTO_REEL" and self.temperature.value >= ceiling:
                    self._stop("Plafond thermique atteint ; reprise explicite nécessaire")

    def check_freshness(self, now=None):
        with self.lock:
            if self.mode in ("MANUEL", "MANUEL_BANC", "MANUEL_REEL", "AUTO_REEL") and not self._temperature_ok(now or datetime.now(timezone.utc)):
                self._stop("Température périmée ; retour AUTO")
            elif self.mode in ("AUTO_BANC", "AUTO_REEL") and self.auto_deadline is not None and monotonic() > self.auto_deadline:
                self._stop("Calcul AUTO périmé ; réarmement explicite nécessaire")

    def _stop(self, reason):
        self._clear(reason)
        if self.dac.ready:
            try:
                self._write(0)
            finally:
                if self.dac.kind == "gp8600":
                    self.dac.close()

    def prepare_heartbeat(self):
        """Explicit GPIO activation must first clear any retained DAC command.

        An I2C acknowledgement does not prove current or electrical inhibition.
        """
        with self.lock:
            if self.dac.kind == "gp8600" and self.connection == "boiler":
                self._stop("Initialisation avant heartbeat ; état physique à vérifier")
                self._initialize()
                if self.error:
                    raise OSError(self.error)
                self.dac.close()

    def command(self, data, now=None):
        """Serialized, synchronous write. Never enqueue an old slider command."""
        now = now or datetime.now(timezone.utc)
        with self.lock:
            action = data.get("action")
            if action == "mode":
                if set(data) - {"action", "mode", "target_c", "isolated", "confirmed"}:
                    raise ValueError("Unexpected mode fields")
                mode = data.get("mode")
                if mode not in ("AUTO", "MANUEL", "MANUEL_BANC", "AUTO_BANC", "BANC_DAC", "AUTO_REEL", "MANUEL_REEL"):
                    raise ValueError("Unknown output mode")
                if mode in ("AUTO_REEL", "MANUEL_REEL") and (self.dac.kind != "gp8600" or self.connection != "boiler" or data.get("confirmed") is not True):
                    raise ValueError("Confirmer explicitement la commande physique sur installation")
                # Validate before changing state, but no transition can retain heat.
                target = None
                if mode in ("MANUEL", "MANUEL_BANC", "MANUEL_REEL"):
                    target = number(data.get("target_c", self.ceiling), 30, self.ceiling)
                    if not self._temperature_ok(now):
                        raise ValueError("Une température valide est requise")
                    if self.temperature.value >= target:
                        self._stop("Consigne déjà atteinte ; AUTO")
                        return
                if mode == "AUTO_REEL" and (not self._temperature_ok(now) or self.temperature.value >= self.ceiling):
                    raise ValueError("Température réelle valide sous le plafond requise")
                if self.dac.kind == "gp8600":
                    required = "bench" if mode in ("BANC_DAC", "MANUEL_BANC", "AUTO_BANC") else "boiler"
                    if mode != "AUTO" and self.connection != required:
                        raise ValueError("Câblage configuré incompatible avec ce mode")
                if mode in ("BANC_DAC", "MANUEL_BANC", "AUTO_BANC") and data.get("isolated") is not True:
                    raise ValueError("Confirmer le banc isolé du CELDUC et du 400 V")
                self._stop("Changement de mode ; anciennes consignes supprimées")
                if mode in ("BANC_DAC", "MANUEL_BANC", "AUTO_BANC", "AUTO_REEL", "MANUEL_REEL") and self.dac.kind == "gp8600":
                    self._initialize()
                if mode != "AUTO" and not self.dac.ready:
                    raise OSError("Récupération I²C à zéro requise")
                self.mode, self.target_c = mode, target
                if mode in ("AUTO_BANC", "AUTO_REEL"):
                    self.auto_deadline = monotonic() + self.auto_timeout
                self.reason = "Mode " + mode
                # MANUEL starts with no heat request. The slider is a separate command.
            elif action == "manual":
                if set(data) != {"action", "percent"} or self.mode not in ("MANUEL", "MANUEL_BANC", "MANUEL_REEL"):
                    raise ValueError("MANUEL requis")
                percent = number(data["percent"], 0, 100)
                if not self._temperature_ok(now) or self.temperature.value >= min(self.target_c, self.ceiling):
                    self._stop("Protection thermique ; retour AUTO")
                    raise ValueError("Température invalide ou cible atteinte")
                ma = 0 if self.mode == "MANUEL_REEL" and percent == 0 else 4 + 16 * percent / 100
                self._write(ma)
                self.manual_percent, self.requested_ma = percent, ma
                self.enabled = True
                self.revision += 1
            elif action == "bench":
                if set(data) != {"action", "enabled", "current_ma"} or self.mode != "BANC_DAC":
                    raise ValueError("BANC DAC requis")
                if type(data["enabled"]) is not bool:
                    raise ValueError("Boolean enabled required")
                ma = number(data["current_ma"], 0, 20)
                self._write(ma if data["enabled"] else 0)
                self.revision += 1
                self.enabled = data["enabled"]
                self.requested_ma = ma if self.enabled else 0.
            elif action == "stop":
                if set(data) != {"action"}:
                    raise ValueError("Unexpected stop fields")
                self._stop("Arrêt demandé ; état physique à vérifier")
            elif action == "recover":
                if set(data) - {"action", "confirmed"}:
                    raise ValueError("Unexpected recovery fields")
                if self.dac.kind == "gp8600" and data.get("confirmed") is not True:
                    raise ValueError("Confirmer la récupération à zéro")
                self._clear("Récupération explicite à zéro ; AUTO")
                self._initialize()
                if self.error:
                    raise OSError(self.error)
                if self.dac.kind == "gp8600":
                    self.dac.close()
            else:
                raise ValueError("Unknown control action")

    def observe_auto(self, power_w, revision, healthy=True):
        # Alpha.3 AUTO is virtual; cannot overwrite either explicit mode.
        with self.lock:
            if self.mode == "AUTO" and self.revision == revision:
                self.auto_power_w = power_w
            elif self.mode in ("AUTO_BANC", "AUTO_REEL") and self.revision == revision:
                if not healthy or (self.mode == "AUTO_REEL" and not self._temperature_ok(datetime.now(timezone.utc))):
                    self._stop("Défaut AUTO ; réarmement explicite nécessaire")
                    return
                try:
                    power_w = number(power_w, 0, self.max_power)
                except ValueError:
                    self._stop("Consigne AUTO invalide ; réarmement explicite nécessaire")
                    raise
                # Bench-only provisional model; no claim about heater power.
                ma = 0 if power_w == 0 else self.power_curve.current_for_power(power_w)
                self._write(ma)
                self.auto_power_w, self.requested_ma = power_w, ma
                self.enabled = power_w > 0
                self.auto_deadline = monotonic() + self.auto_timeout

    def snapshot(self):
        with self.lock:
            return {"mode": self.mode, "revision": self.revision,
                    "enabled": self.enabled, "requested_ma": self.requested_ma, "max_power_w": self.max_power,
                    "manual_percent": self.manual_percent, "target_c": self.target_c,
                    "heat_requested": ((self.mode in ("MANUEL", "MANUEL_REEL") and self.manual_percent > 0)
                                       or (self.mode == "AUTO_REEL" and self.auto_power_w > 0)) and self.enabled,
                    "auto_virtual_power_w": self.auto_power_w, "auto_physical_enabled": self.mode in ("AUTO_BANC", "AUTO_REEL") and self.dac.kind == "gp8600",
                    "power_mapping": "PROVISOIRE_NON_QUALIFIE" if self.mode == "AUTO_REEL" else "PROVISOIRE_SIMULATION_BANC",
                    "connection": self.connection, "reason": self.reason, "error": self.error,
                    "k2_commanded": False, "power_calibrated": False,
                    "temperature_c": self.temperature.value if self.temperature else None,
                    "temperature_at": self.temperature.measured_at.isoformat() if self.temperature else None,
                    "thermal_ceiling_c": self.ceiling,
                    "dac": self.dac.snapshot()}

    def close(self):
        with self.lock:
            try:
                self._stop("Arrêt de l'application")
            finally:
                self.dac.close()
