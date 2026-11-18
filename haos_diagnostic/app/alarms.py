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

    def observe(self, key, state, at=None, detail=None, *, evidence=None,
                evidence_monotonic=None, evidence_ttl=None, confirmed=False):
        if state not in ('OK', 'DEFAUT', 'NON_VERIFIABLE', 'NON_SOLLICITE', 'DESACTIVE'):
            raise ValueError('Invalid alarm state')
        stamp = (at or datetime.now(timezone.utc)).isoformat()
        with self.lock:
            row = self.rows[key]
            if state == 'OK' and row['state'] == 'DEFAUT' and not confirmed:
                if evidence is not None and evidence == row.get('healthy_evidence'):
                    return row['state']
                row['healthy_evidence'] = evidence
                row['healthy_samples'] += 1
                if row['healthy_samples'] < 2:
                    return row['state']
            elif state in ('NON_VERIFIABLE', 'NON_SOLLICITE', 'DESACTIVE') and row['state'] == 'DEFAUT':
                row['healthy_samples'] = 0
                row['activity'] = state
                return row['state']
            else:
                row['healthy_samples'] = 0
            row['observed_monotonic'] = monotonic() if evidence_monotonic is None else evidence_monotonic
            row['evidence_monotonic'] = evidence_monotonic
            row['evidence_ttl'] = evidence_ttl
            row['detail'] = detail
            row['activity'] = None
            if state != row['state'] or row['at'] is None:
                row['state'], row['at'] = state, stamp
            if state != 'DEFAUT':
                row['healthy_samples'] = 0
            if row['healthy_samples'] == 0:
                row['healthy_evidence'] = None
            return row['state']

    def confirm_dac_recovery(self, last_ack, ttl):
        self.observe('A002', 'OK', evidence_monotonic=last_ack, evidence_ttl=ttl,
                     confirmed=True)

    def snapshot(self):
        with self.lock:
            rows = []
            now = monotonic()
            for row in self.rows.values():
                public = {k: row[k] for k in ('id', 'name', 'description', 'state', 'at')}
                ttl = row.get('evidence_ttl') or (3600 if row['id'] in ('A005', 'A006') else 120)
                age = now - row.get('observed_monotonic', 0)
                if row['state'] == 'OK' and not 0 <= age <= ttl:
                    public['state'] = 'NON_VERIFIABLE'
                if public['state'] == 'OK':
                    public['description'] = 'Aucun défaut confirmé lors des dernières vérifications.'
                elif public['state'] == 'NON_VERIFIABLE':
                    public['description'] = 'Diagnostic en attente ou données trop anciennes pour conclure.'
                elif public['state'] == 'DESACTIVE':
                    public['description'] = 'Fonction non requise dans la configuration actuelle.'
                elif public['state'] == 'NON_SOLLICITE':
                    public['description'] = 'DAC non sollicité ; communication non vérifiée en continu.'
                if row.get('detail') and public['state'] == row['state']:
                    public['description'] = row['detail']
                if row.get('activity') in ('NON_SOLLICITE', 'DESACTIVE'):
                    public['activity'] = row['activity']
                    public['description'] += ' Fonction non sollicitée actuellement. Défaut précédent à confirmer.'
                elif row.get('activity') == 'NON_VERIFIABLE':
                    public['activity'] = 'NON_VERIFIABLE'
                if row['id'] == 'A002' and public['state'] == 'NON_VERIFIABLE':
                    public['description'] = 'Communication I²C non vérifiée ; aucun courant physique mesuré.'
                elif row['id'] == 'A002' and public['state'] == 'OK':
                    public['description'] = 'Un ACK I²C récent a été reçu ; identité du DAC et courant de sortie non mesurés.'
                elif row['id'] == 'A002' and public['state'] == 'NON_SOLLICITE':
                    public['description'] = (row.get('detail') or public['description']) + ' Aucun courant physique mesuré.'
                    public.update(state='OK', activity='NON_SOLLICITE')
                rows.append(public)
        states = {row['state'] for row in rows}
        overall = 'ALARME' if 'DEFAUT' in states else ('NON_VERIFIABLE' if 'NON_VERIFIABLE' in states else 'OK')
        return {'state': overall, 'alarms': rows}
