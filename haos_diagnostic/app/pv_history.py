"""Additive, permanent PV observations in the existing SQLite database.

The original grid/archive/forecast/preferences tables are never changed. This
module can be removed on rollback while leaving its table and data intact.
"""
from copy import deepcopy
from datetime import datetime, timedelta, time
import hashlib
import json
from math import ceil
import sqlite3
from threading import RLock
from time import monotonic

from .history_store import parse_time, aware
from .energy_dashboard import ZONE, UTC, MAX_GAP

TABLE='pv_actual_observations_v1'


def sample(points, limit=4000):
    if len(points)<=limit:return points
    step=ceil(len(points)/max(1,limit//5));indices=[]
    for start in range(0,len(points),step):
        block=range(start,min(start+step,len(points)))
        good=[i for i in block if points[i]['value'] is not None]
        picks=[start,min(start+step,len(points))-1]
        if good:picks.extend((min(good,key=lambda i:points[i]['value']),max(good,key=lambda i:points[i]['value'])))
        missing=next((i for i in block if points[i]['value'] is None),None)
        if missing is not None:picks.append(missing)
        indices.extend(picks)
    indices=sorted(set(indices))
    if len(indices)>limit:indices=indices[:limit-1]+[len(points)-1]
    result=[];previous=-1
    for i in indices:
        p=dict(points[i]);p['gap_before']=any(x['gap_before'] or x['value'] is None for x in points[previous+1:i+1])
        result.append(p);previous=i
    return result


class PVHistory:
    def __init__(self, store, *, create=False):
        self.store=store;self.cache={};self.cache_lock=RLock()
        with store.lock:
            if create:
                with store.db:
                    store.db.execute(f'''CREATE TABLE IF NOT EXISTS {TABLE}(
                        fingerprint TEXT PRIMARY KEY, kind TEXT NOT NULL,
                        entity_id TEXT NOT NULL, observed_at TEXT NOT NULL,
                        event_at TEXT NOT NULL, reported_at TEXT, value REAL,
                        raw_numeric REAL, status TEXT NOT NULL, reason TEXT NOT NULL,
                        anchor INTEGER NOT NULL, metadata TEXT NOT NULL)''')
                    store.db.execute(f'CREATE INDEX IF NOT EXISTS pv_actual_kind_time_v1 ON {TABLE}(kind,event_at)')
            self.exists=store.db.execute('SELECT 1 FROM sqlite_master WHERE type="table" AND name=?',(TABLE,)).fetchone() is not None

    def last_anchor(self, kind, entity):
        if not self.exists or not entity:return None
        with self.store.lock:
            row=self.store.db.execute(f'''SELECT reported_at,raw_numeric,status,reason FROM {TABLE}
                WHERE kind=? AND entity_id=? AND anchor=1 ORDER BY event_at DESC LIMIT 1''',(kind,entity)).fetchone()
        return dict(zip(('at','raw_numeric','status','reason'),row)) if row else None

    def record(self, observations):
        if not self.exists:return
        with self.store.lock:
            with self.store.db:
                for item in observations:
                    if item['status']=='UNCONFIGURED':continue
                    metadata={k:v for k,v in item.items() if k not in ('observed_at','age_seconds')}
                    payload=json.dumps(metadata,ensure_ascii=False,allow_nan=False,sort_keys=True,separators=(',',':'))
                    identity={k:v for k,v in metadata.items() if k!='reason'} if item['status']=='OK' else metadata
                    fingerprint=hashlib.sha256(json.dumps(identity,sort_keys=True,allow_nan=False).encode()).hexdigest()
                    event=item['at'] if item['status']=='OK' else item['observed_at']
                    self.store.db.execute(f'INSERT OR IGNORE INTO {TABLE} VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                        (fingerprint,item['kind'],item['entity_id'],item['observed_at'],event,
                         item['at'],item['value'],item['raw_numeric'],item['status'],item['reason'],
                         int(item['anchor']),payload))

        with self.cache_lock:self.cache.clear()

    def enrich(self, original, now=None):
        now=aware(now or datetime.now(UTC))
        key=(original['period'],original['selected'],original['generated_at'])
        with self.cache_lock:
            cached=self.cache.get(key)
            if cached and 0<=monotonic()-cached[0]<60:return deepcopy(cached[1])
            result=self._enrich(original,now)
            if len(self.cache)>=6:self.cache.pop(next(iter(self.cache)))
            self.cache[key]=(monotonic(),result)
            return deepcopy(result)

    def _enrich(self, original, now=None):
        data=deepcopy(original);now=aware(now or datetime.now(UTC))
        start,end=parse_time(data['start']),parse_time(data['end'])
        # Always select the energy method per Swiss DAY before combining months:
        # the day/month/year totals therefore use the same hierarchy and never
        # add two counters or an integral for the same day's production.
        dates=[];d=start.astimezone(ZONE).date()
        while datetime.combine(d,time.min,tzinfo=ZONE).astimezone(UTC)<end:
            dates.append(datetime.combine(d,time.min,tzinfo=ZONE).astimezone(UTC));d+=timedelta(days=1)
        edges=dates+[end]
        metrics={k:[{'kwh':0.,'seconds':0.} for _ in dates] for k in ('power','today','total','self')}
        points={'power':[],'self':[]};anomalies=0
        if self.exists:
            db=sqlite3.connect('file:'+self.store.path.absolute().as_posix()+'?mode=ro',uri=True,timeout=5)
            try:
                for kind in metrics:
                    previous=None;day_index=0;last_point=None
                    cursor=db.execute(f'''SELECT event_at,entity_id,value,status,observed_at,reason FROM {TABLE}
                        WHERE kind=? AND event_at>=? AND event_at<=?
                        ORDER BY event_at,observed_at''',
                        (kind,(start-timedelta(seconds=MAX_GAP)).isoformat(),(min(now,end)+timedelta(seconds=MAX_GAP)).isoformat()))
                    for event,entity,value,status,observed,reason in cursor:
                        at=parse_time(event)
                        if at>now or parse_time(observed)>now:continue
                        valid=status=='OK' and value is not None and value>=0
                        if status in ('INVALID','ANOMALY','LATE','CONFLICT') and start<=at<end:anomalies+=1
                        if data['period']=='day' and kind in points and start<=at<end:
                            gap=last_point is not None and (at-last_point).total_seconds()>MAX_GAP
                            points[kind].append({'at':at.isoformat(),'value':value if valid else None,'gap_before':gap,'status':status,'reason':reason,'entity_id':entity})
                            last_point=at
                        if previous:
                            before,old_entity,old_value,old_valid=previous
                            seconds=(at-before).total_seconds()
                            eligible=(valid and old_valid and entity==old_entity and 0<seconds<=MAX_GAP)
                            if kind=='today' and at.astimezone(ZONE).date()!=before.astimezone(ZONE).date():eligible=False
                            delta=value-old_value if valid and old_valid else 0
                            if kind in ('today','total') and (delta<0 or delta>100000*max(0,seconds)/3600000+.11):eligible=False
                            if eligible:
                                left,right=max(start,before),min(at,end,now)
                                while day_index<len(dates)-1 and edges[day_index+1]<=left:day_index+=1
                                index=day_index
                                while left<right and index<len(dates):
                                    stop=min(right,edges[index+1]);dt=(stop-left).total_seconds()
                                    if kind in ('power','self'):
                                        a=old_value+(value-old_value)*(left-before).total_seconds()/seconds
                                        b=old_value+(value-old_value)*(stop-before).total_seconds()/seconds
                                        energy=(a+b)*dt/2/3600000
                                    else:energy=delta*dt/seconds
                                    metrics[kind][index]['kwh']+=energy;metrics[kind][index]['seconds']+=dt
                                    left=stop;index+=1
                        previous=(at,entity,value,valid)
            finally:db.close()
        daily=[]
        for i,_ in enumerate(dates):
            method=next((k for k in ('total','today','power') if metrics[k][i]['seconds']>0),None)
            chosen=metrics[method][i] if method else {'kwh':None,'seconds':0}
            daily.append({'method':method,**chosen})
        total_energy=None;total_covered=0.;total_self=None;self_covered=0.;methods=set()
        for bucket in data['buckets']:
            a,b=parse_time(bucket['at']),parse_time(bucket['end'])
            indices=[i for i,d in enumerate(dates) if a<=d<b]
            covered=sum(daily[i]['seconds'] for i in indices)
            consumed=sum(metrics['self'][i]['seconds'] for i in indices)
            power_covered=sum(metrics['power'][i]['seconds'] for i in indices)
            method_names=sorted({daily[i]['method'] for i in indices if daily[i]['method']})
            bucket.update(pv_actual_kwh=sum(daily[i]['kwh'] or 0 for i in indices) if covered else None,
                          pv_covered_seconds=covered,pv_power_covered_seconds=power_covered,
                          pv_method='+'.join(method_names) if method_names else None,
                          pv_partial=covered<bucket['period_seconds']-.001,
                          self_consumed_kwh=sum(metrics['self'][i]['kwh'] for i in indices) if consumed else None,
                          self_covered_seconds=consumed,self_partial=consumed<bucket['period_seconds']-.001)
            if covered:total_energy=(total_energy or 0)+bucket['pv_actual_kwh']
            if consumed:total_self=(total_self or 0)+bucket['self_consumed_kwh']
            total_covered+=covered;self_covered+=consumed;methods.update(method_names)
        for kind,key in (('power','pv_actual_w'),('self','self_consumed_w')):
            points[kind]=[{**p,key:p['value']} for p in sample(points[kind])]
        data['pv_history']={'generated_at':now.isoformat(),'energy_kwh':total_energy,'covered_seconds':total_covered,
            'partial':total_covered<data['period_seconds']-.001,'methods':sorted(methods),
            'anomaly_count':anomalies,'power_points':points['power'],
            'self_consumed_points':points['self'],'self_energy_kwh':total_self,
            'self_covered_seconds':self_covered,'self_method':'Intégration estimée de puissance calculée sur mesures synchronisées',
            'self_partial':self_covered<data['period_seconds']-.001,
            'source':'Sofar Solar / Home Assistant',
            'method':'Par jour : différence index total, sinon index du jour, sinon intégration AC estimée ; sans addition des méthodes',
            'boundary_allocation':'Répartition linéaire entre observations aux frontières civiles, sans extrapolation ni lacunes >180 s'}
        return data
