"""Independent software diagnostics; no authority to operate outputs."""
from datetime import datetime, timezone
from threading import RLock
from time import monotonic

DEFINITIONS = {
    'A001': ('Défaut de calcul de régulation', 'Le calcul de régulation est indisponible ; commande automatique inhibée.'),
    'A002': ('Défaut driver / communication DAC GP8600', 'Le pilotage du DAC a échoué ; état physique de sortie inconnu.'),
    'A003': ('Défaut sonde de température boiler', 'La température du boiler est indisponible ou invalide.'),
    'A004': ('Défaut mesures gPlugK', 'Les mesures réseau ne sont pas suffisamment disponibles ou valides.'),
    'A005': ('Défaut données météorologiques', 'Les données météorologiques nécessaires sont indisponibles.'),
    'A006': ('Défaut Forecast.Solar', 'Les prévisions photovoltaïques nécessaires sont indisponibles.'),
}

class Alarms:
    def __init__(self):
        self.lock = RLock()
        self.rows = {key: {'id': key, 'name': name, 'description': description,
                           'state': 'NON_VERIFIABLE', 'at': None, 'healthy_samples': 0}
                     for key, (name, description) in DEFINITIONS.items()}

    def observe(self, key, state, at=None):
        if state not in ('OK', 'DEFAUT', 'NON_VERIFIABLE', 'DESACTIVE'):
            raise ValueError('Invalid alarm state')
        stamp = (at or datetime.now(timezone.utc)).isoformat()
        with self.lock:
            row = self.rows[key]
            row['observed_monotonic'] = monotonic()
            if key == 'A002' and state == 'DESACTIVE' and row['state'] == 'DEFAUT':
                return  # stopping AUTO does not prove that the DAC recovered
            # Unknown is not proof of recovery; two consecutive good samples
            # are required to clear a fault. Disabling a feature is explicit.
            if state == 'OK' and row['state'] == 'DEFAUT':
                row['healthy_samples'] += 1
                if row['healthy_samples'] < 2:
                    return
            elif state == 'NON_VERIFIABLE' and row['state'] == 'DEFAUT':
                row['healthy_samples'] = 0
                return
            else:
                row['healthy_samples'] = 0
            if state != row['state'] or row['at'] is None:
                row['state'], row['at'] = state, stamp
            if state != 'DEFAUT':
                row['healthy_samples'] = 0

    def snapshot(self):
        with self.lock:
            rows = []
            now = monotonic()
            for row in self.rows.values():
                public = {k: v for k, v in row.items() if k not in ('healthy_samples', 'observed_monotonic')}
                ttl = 3600 if row['id'] in ('A005', 'A006') else 120
                if row['state'] == 'OK' and now - row.get('observed_monotonic', 0) > ttl:
                    public['state'] = 'NON_VERIFIABLE'
                if public['state'] == 'OK':
                    public['description'] = 'Aucun défaut confirmé lors des dernières vérifications.'
                elif public['state'] == 'NON_VERIFIABLE':
                    public['description'] = 'Diagnostic en attente ou données trop anciennes pour conclure.'
                elif public['state'] == 'DESACTIVE':
                    public['description'] = 'Fonction non requise dans la configuration actuelle.'
                rows.append(public)
        states = {row['state'] for row in rows}
        overall = 'ALARME' if 'DEFAUT' in states else ('NON_VERIFIABLE' if 'NON_VERIFIABLE' in states else 'OK')
        return {'state': overall, 'alarms': rows}
