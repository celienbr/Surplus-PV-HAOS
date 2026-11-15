"""Single writer for the real GP8600; mode changes clear old setpoints."""
from __future__ import annotations

from datetime import datetime, timezone
from threading import RLock
from time import monotonic, monotonic_ns

from .gp8600 import number
from .boiler_temperature import valid_boiler_temperature


class OutputControl:
    def __init__(self, dac, connection="boiler", auto_timeout=60, max_power=6000):
        if connection != "boiler":
            raise ValueError("Only the boiler output is supported")
        self.dac, self.connection = dac, connection
        self.auto_timeout, self.max_power = auto_timeout, max_power
        self.power_curve = None
        self.lock = RLock()
        self.mode = "AUTO"
        self.revision = 0
        self.enabled = False
        self.requested_ma = self.manual_percent = 0.
        self.target_c = None
        self.auto_deadline = None
        self.auto_suspended = False
        self.reason = "Démarrage sans commande ; état physique inconnu"
        self.auto_power_w = 0.
        self.temperature = None
        self.ceiling = 70.
        self.max_age = 60.
        self.error = None
        self.last_i2c = None
        self.i2c_writes = 0
        self.i2c_avoided = 0

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
        self.auto_suspended = False
        self.reason = reason

    def _write(self, ma):
        start = monotonic_ns()
        self.i2c_writes += 1
        try:
            self.dac.set_current(ma)
        except OSError as exc:
            self.last_i2c = {'start_ns': start, 'end_ns': monotonic_ns(), 'result': 'ERROR'}
            if self.mode == 'AUTO_REEL':
                # Retain operator intent while no output is trusted. Recovery
                # must reinitialize at zero and wait for a new meter report.
                self.revision += 1
                self.enabled = False
                self.auto_suspended = True
                self.auto_power_w = self.requested_ma = 0.
                self.auto_deadline = None
                self.reason = 'Erreur DAC ; AUTO inhibé, sortie physique inconnue'
            else:
                self._clear("Erreur DAC ; reprise explicite à zéro nécessaire")
            self.error = str(exc)[:160]
            raise
        self.last_i2c = {'start_ns': start, 'end_ns': monotonic_ns(), 'result': 'ACK'}

    def _temperature_ok(self, now):
        t = self.temperature
        return valid_boiler_temperature(t, now, self.max_age)

    def observe_temperature(self, measurement, ceiling, max_age, now=None):
        now = now or datetime.now(timezone.utc)
        with self.lock:
            valid = valid_boiler_temperature(measurement, now, max_age)
            self.temperature, self.ceiling, self.max_age = measurement if valid else None, ceiling, max_age
            if self.mode in ("MANUEL_REEL", "AUTO_REEL"):
                if self.mode == "AUTO_REEL" and (not self._temperature_ok(now) or self.temperature.value >= ceiling):
                    self._suspend_auto("Chauffe suspendue : température indisponible ou plafond atteint")
                elif not self._temperature_ok(now):
                    self._stop("Température invalide ou périmée ; retour ARRÊT")
                elif self.mode != "AUTO_REEL" and self.temperature.value >= min(self.target_c, ceiling):
                    self._stop("Consigne thermique atteinte ; retour ARRÊT")

    def check_freshness(self, now=None):
        with self.lock:
            if self.mode == "AUTO_REEL" and not self._temperature_ok(now or datetime.now(timezone.utc)):
                self._suspend_auto("Chauffe suspendue : température périmée")
            elif self.mode == "MANUEL_REEL" and not self._temperature_ok(now or datetime.now(timezone.utc)):
                self._stop("Température périmée ; retour ARRÊT")
            elif self.mode == "AUTO_REEL" and self.auto_deadline is not None and monotonic() > self.auto_deadline:
                self._suspend_auto("Chauffe suspendue : calcul AUTO périmé")

    def _suspend_auto(self, reason):
        if not self.auto_suspended:
            self.revision += 1
            self.auto_suspended = True
            self.enabled = False
            self.auto_power_w = self.requested_ma = 0.
            self.auto_deadline = None
            self.reason = reason
            if self.dac.ready:
                self._write(0)

    def _stop(self, reason):
        self._clear(reason)
        if self.dac.ready:
            try:
                self._write(0)
            finally:
                self.dac.close()

    def prepare_heartbeat(self):
        """Explicit GPIO activation must first clear any retained DAC command.

        An I2C acknowledgement does not prove current or electrical inhibition.
        """
        with self.lock:
            self._stop("Initialisation avant battement ; état physique à vérifier")
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
                if set(data) - {"action", "mode", "target_c", "confirmed"}:
                    raise ValueError("Unexpected mode fields")
                mode = data.get("mode")
                if mode not in ("AUTO", "AUTO_REEL", "MANUEL_REEL"):
                    raise ValueError("Unknown output mode")
                if mode in ("AUTO_REEL", "MANUEL_REEL") and data.get("confirmed") is not True:
                    raise ValueError("Confirmer explicitement la commande physique sur installation")
                # Validate before changing state, but no transition can retain heat.
                target = None
                if mode == "MANUEL_REEL":
                    target = number(data.get("target_c", self.ceiling), 30, self.ceiling)
                    if not self._temperature_ok(now):
                        raise ValueError("Une température valide est requise")
                    if self.temperature.value >= target:
                        self._stop("Consigne déjà atteinte ; ARRÊT")
                        return
                if mode == "AUTO_REEL" and (not self._temperature_ok(now) or self.temperature.value >= self.ceiling):
                    raise ValueError("Température réelle valide sous le plafond requise")
                if mode == "AUTO_REEL" and (self.power_curve is None or not self.power_curve.validated):
                    raise ValueError("Courbe puissance/courant mesurée et validée requise")
                self._stop("Changement de mode ; anciennes consignes supprimées")
                if mode in ("AUTO_REEL", "MANUEL_REEL"):
                    self._initialize()
                if mode != "AUTO" and not self.dac.ready:
                    raise OSError("Récupération I²C à zéro requise")
                self.mode, self.target_c = mode, target
                if mode == "AUTO_REEL":
                    self.auto_deadline = monotonic() + self.auto_timeout
                self.reason = "Mode " + mode
                # MANUEL starts with no heat request. The slider is a separate command.
            elif action == "manual":
                if set(data) != {"action", "percent"} or self.mode != "MANUEL_REEL":
                    raise ValueError("MANUEL requis")
                percent = number(data["percent"], 0, 100)
                if not self._temperature_ok(now) or self.temperature.value >= min(self.target_c, self.ceiling):
                    self._stop("Protection thermique ; retour ARRÊT")
                    raise ValueError("Température invalide ou cible atteinte")
                ma = 0 if percent == 0 else 4 + 16 * percent / 100
                self._write(ma)
                self.manual_percent, self.requested_ma = percent, ma
                self.enabled = True
                self.revision += 1
            elif action == "stop":
                if set(data) != {"action"}:
                    raise ValueError("Unexpected stop fields")
                self._stop("Arrêt demandé ; état physique à vérifier")
            elif action == "recover":
                if set(data) - {"action", "confirmed"}:
                    raise ValueError("Unexpected recovery fields")
                if data.get("confirmed") is not True:
                    raise ValueError("Confirmer la récupération à zéro")
                self._clear("Récupération explicite à zéro ; ARRÊT")
                self._initialize()
                if self.error:
                    raise OSError(self.error)
                self.dac.close()
            else:
                raise ValueError("Unknown control action")

    def observe_auto(self, power_w, revision, healthy=True, new_sample=True, now=None):
        with self.lock:
            if self.mode == "AUTO_REEL" and self.revision == revision:
                now = now or datetime.now(timezone.utc)
                invalid_thermal = not self._temperature_ok(now) or self.temperature.value >= self.ceiling
                if not healthy or invalid_thermal:
                    self._suspend_auto("Chauffe suspendue : données invalides")
                    return
                if self.auto_suspended and not new_sample:
                    return
                if not self.dac.ready:
                    return
                try:
                    power_w = number(power_w, 0, self.max_power)
                except ValueError:
                    self._stop("Consigne AUTO invalide ; réarmement explicite nécessaire")
                    raise
                ma = 0 if power_w == 0 else self.power_curve.current_for_power(power_w)
                # An unchanged, previously acknowledged command requires no
                # additional I²C transaction. Recovery always initializes at
                # zero and stale/thermal faults still use the stop path.
                sent = ma != self.requested_ma
                if sent:
                    try:
                        self._write(ma)
                    except OSError as exc:
                        exc.i2c_timing = dict(self.last_i2c)
                        raise
                else:
                    self.i2c_avoided += 1
                self.auto_power_w, self.requested_ma = power_w, ma
                if self.auto_suspended:
                    self.reason = 'AUTO actif : mesures valides'
                self.auto_suspended = False
                self.enabled = power_w > 0
                self.auto_deadline = monotonic() + self.auto_timeout
                return dict(self.last_i2c) if sent else None

    def snapshot(self):
        with self.lock:
            return {"mode": self.mode, "revision": self.revision,
                    "enabled": self.enabled, "requested_ma": self.requested_ma, "max_power_w": self.max_power,
                    "manual_percent": self.manual_percent, "target_c": self.target_c,
                    "heat_requested": ((self.mode == "MANUEL_REEL" and self.manual_percent > 0)
                                       or (self.mode == "AUTO_REEL" and self.auto_power_w > 0)) and self.enabled,
                    "auto_requested_power_w": self.auto_power_w, "auto_physical_enabled": self.mode == "AUTO_REEL",
                    "auto_suspended": self.auto_suspended,
                    "last_i2c": self.last_i2c, "i2c_writes": self.i2c_writes,
                    "i2c_avoided": self.i2c_avoided,
                    "power_mapping": "MESURES_VALIDEES" if self.power_curve and self.power_curve.validated else "NON_CONFIGUREE",
                    "connection": self.connection, "reason": self.reason, "error": self.error,
                    "k2_commanded": False, "power_calibrated": bool(self.power_curve and self.power_curve.validated),
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
