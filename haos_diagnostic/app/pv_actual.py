"""Optional Sofar AC observations via the existing, read-only HA client.

This service never participates in regulator/comfort decisions. Its own worker
isolates optional HA timeouts from the existing grid acquisition.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from math import isfinite
from threading import RLock
import logging
import sqlite3

from .measurements import Measurement
from .history_store import parse_time
from .energy_dashboard import live_grid, ZONE
from .pv_history import PVHistory

UTC = timezone.utc
SOURCE = 'Sofar Solar / Solarman via Home Assistant · production AC'
SYNC_SECONDS = 15
KINDS = {'power':'pv_actual_power_entity', 'today':'pv_actual_energy_today_entity',
         'total':'pv_actual_energy_total_entity'}
LOG = logging.getLogger(__name__)


def unavailable(kind, entity, now, status, reason):
    return {'kind':kind, 'entity_id':entity, 'observed_at':now.isoformat(),
            'at':None, 'value':None, 'raw_numeric':None, 'status':status,
            'reason':reason, 'source':SOURCE, 'anchor':False}


def read_observation(kind, entity, state, now, max_age):
    item=unavailable(kind,entity,now,'INVALID','Mesure invalide')
    try:
        if not isinstance(state,dict) or state.get('entity_id')!=entity:
            raise ValueError('Identifiant HA absent ou différent')
        raw=state.get('state'); item['raw_state']=str(raw)[:80]
        if raw in (None,'','unknown','unavailable'):
            item.update(status='UNAVAILABLE',reason='Capteur HA indisponible')
            return item
        if isinstance(raw,bool):raise ValueError('Valeur booléenne interdite')
        value=float(raw)
        if not isfinite(value):raise ValueError('Valeur non finie')
        attrs=state.get('attributes') or {}
        if not isinstance(attrs,dict):raise ValueError('Attributs HA invalides')
        unit=attrs.get('unit_of_measurement')
        factors={'W':1.,'kW':1000.} if kind=='power' else {'Wh':.001,'kWh':1.}
        if unit not in factors:raise ValueError('Unité incompatible : '+str(unit))
        if attrs.get('device_class')!=('power' if kind=='power' else 'energy'):
            raise ValueError('Classe de mesure incompatible')
        expected=('measurement',) if kind=='power' else ('total_increasing',)
        if attrs.get('state_class') not in expected:
            raise ValueError('Classe statistique incompatible')
        value*=factors[unit]
        limit=100000 if kind=='power' else 2400 if kind=='today' else 1e9
        if not 0<=value<=limit:raise ValueError('Valeur négative ou hors plage')
        at=parse_time(state.get('last_reported') or state.get('last_updated'))
        item.update(at=at.isoformat(),raw_numeric=value,unit='W' if kind=='power' else 'kWh',
                    original_unit=unit, timestamp_basis='last_reported' if state.get('last_reported') else 'last_updated')
        if not Measurement(value,at).valid(now,max_age):
            item.update(status='STALE',reason='Rapport HA périmé ou futur')
            return item
        item.update(value=value,status='OK',reason='Mesure HA validée',anchor=True,
                    valid_until=(at+timedelta(seconds=max_age)).isoformat())
    except (ValueError,TypeError,OverflowError) as error:
        item['reason']=str(error)
    return item


def derive(power, grid, options, now):
    result=unavailable('self','calculated.pv_self_consumption',now,'UNAVAILABLE',
                       'Autoconsommation : non disponible')
    result['source']='Calcul : PV AC Sofar − export gPlugK'
    result['house_w']=None
    if not options['pv_self_consumption_topology_verified']:
        result['reason']='Autoconsommation : non disponible — topologie non confirmée'
        return result
    if power['status']!='OK':
        result['reason']='Autoconsommation : non disponible — production AC non valide'
        return result
    live=live_grid(grid,options['max_age_seconds'],now)
    if live['status']!='OK' or grid.get('measurement_coverage')!='TWO_RECENT':
        result['reason']='Autoconsommation : non disponible — deux canaux réseau récents requis'
        return result
    pv_at=parse_time(power['at'])
    grid_at=parse_time(grid['updated_at'])-timedelta(seconds=float(grid['meter_age_s']))
    if abs((pv_at-grid_at).total_seconds())>SYNC_SECONDS:
        result['reason']='Autoconsommation : non disponible — mesures non synchronisées (15 s)'
        return result
    export=max(0,-live['grid_w'])
    value=power['value']-export
    house=power['value']+live['grid_w']
    result.update(raw_numeric=value,pv_at=power['at'],grid_at=grid_at.isoformat(),
                  topology_verified=True,pv_ac_w=power['value'],grid_w=live['grid_w'],export_w=export,
                  sync_delta_seconds=abs((pv_at-grid_at).total_seconds()),unit='W',at=max(pv_at,grid_at).isoformat())
    if value<0 or house<0:
        result.update(status='ANOMALY',reason='Autoconsommation : non disponible — export supérieur à la production AC')
        return result
    result.update(value=value,house_w=house,status='OK',reason='Calcul sur mesures synchronisées ; topologie confirmée',
                  valid_until=min(parse_time(power['valid_until']),grid_at+timedelta(seconds=options['max_age_seconds'])).isoformat())
    return result


class PVAcquisition:
    def __init__(self, options, client, store=None):
        self.options=options;self.client=client;self.lock=RLock();self.previous={}
        self.enabled=any(options[key] for key in KINDS.values())
        self.history=None;self.storage_error=None
        if store is not None:
            try:
                self.history=PVHistory(store,create=self.enabled)
                for kind,key in KINDS.items():
                    self.previous[kind]=self.history.last_anchor(kind,options[key])
            except (sqlite3.Error,OSError,ValueError):
                self.storage_error='Historique PV temporairement indisponible'
                LOG.exception('Optional PV history unavailable')
        now=datetime.now(UTC)
        self.latest={kind:unavailable(kind,options[key],now,'UNCONFIGURED','Capteur non configuré') for kind,key in KINDS.items()}
        self.latest['self']=derive(self.latest['power'],{},options,now)

    def poll(self, grid, now=None):
        injected=now is not None
        states={}
        for kind,key in KINDS.items():
            entity=self.options[key]
            if not entity:continue
            try:
                if self.client is None:raise RuntimeError('Client HA indisponible')
                states[kind]=self.client.get_state(entity)
            except (OSError,ValueError,TypeError,RuntimeError):
                states[kind]=None
        now=now if injected else datetime.now(UTC)
        observations={}
        with self.lock:
            for kind,key in KINDS.items():
                entity=self.options[key]
                if not entity:
                    item=unavailable(kind,entity,now,'UNCONFIGURED','Capteur non configuré')
                elif states.get(kind) is None:
                    item=unavailable(kind,entity,now,'ERROR','Lecture HA temporairement impossible')
                else:
                    item=read_observation(kind,entity,states[kind],now,self.options['pv_actual_max_age_seconds'])
                previous=self.previous.get(kind)
                if item['status']=='OK' and previous:
                    at=parse_time(item['at']);before=parse_time(previous['at'])
                    if at<before:
                        item.update(status='LATE',value=None,anchor=False,reason='Rapport reçu en retard')
                    elif at==before and item['value']!=previous['raw_numeric']:
                        item.update(status='CONFLICT',value=None,anchor=False,reason='Valeurs contradictoires au même horodatage')
                    elif at==before and previous['status']=='ANOMALY':
                        item.update(status='ANOMALY',value=None,reason=previous['reason'])
                    elif kind!='power' and item['value']<previous['raw_numeric']:
                        daily_reset=(kind=='today' and at.astimezone(ZONE).date()>before.astimezone(ZONE).date())
                        if daily_reset:
                            item['reason']='Remise à zéro journalière observée'
                        else:
                            item.update(status='ANOMALY',value=None,reason='Index en diminution ; nouveau segment requis')
                    elif kind!='power' and at>before and item['value']-previous['raw_numeric']>100000*(at-before).total_seconds()/3600000+.11:
                        item.update(status='ANOMALY',value=None,reason='Discontinuité excessive de compteur')
                if item['anchor']:
                    self.previous[kind]=deepcopy(item)
                observations[kind]=item
            observations['self']=derive(observations['power'],grid,self.options,now)
            self.latest=observations
        if self.history:
            try:
                self.history.record(observations.values())
                self.storage_error=None
            except (sqlite3.Error,OSError,ValueError):
                self.storage_error='Historique PV temporairement indisponible'
                LOG.exception('Noncritical PV recording failure')
        return self.snapshot(now)

    def snapshot(self, now=None):
        now=now or datetime.now(UTC)
        with self.lock:result=deepcopy(self.latest)
        for item in result.values():
            item['age_seconds']=(now-parse_time(item['at'])).total_seconds() if item['at'] else None
            if item['status']=='OK' and not -2 <= item['age_seconds']:
                item.update(status='STALE',value=None,house_w=None,reason='Horloge antérieure à la mesure')
            if item['status']=='OK' and now>parse_time(item['valid_until']):
                item.update(status='STALE',value=None,house_w=None,reason='Mesure périmée depuis la dernière acquisition')
        result['storage_error']=self.storage_error
        return result
