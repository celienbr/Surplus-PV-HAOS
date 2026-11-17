
"use strict";
let currentData=null;
let expanded=false;
const $=id=>document.getElementById(id);
const text=(id,value)=>{const el=$(id);if(el)el.textContent=value};
const isnum=n=>typeof n==="number"&&Number.isFinite(n);
const kwh=n=>isnum(n)?n.toLocaleString("fr-CH",{maximumFractionDigits:2})+" kWh":"—";
const kw=n=>isnum(n)?n.toLocaleString("fr-CH",{maximumFractionDigits:3})+" kW":"—";
const watts=n=>isnum(n)?Math.round(n).toLocaleString("fr-CH")+" W":"—";
const celsius=n=>isnum(n)?n.toLocaleString("fr-CH",{maximumFractionDigits:1})+" °C":"—";
function tag(id,state,label){
 const e=$(id);e.className="tag"+(state?" "+state:"");e.textContent=label;
}
function render(p){
 currentData=p;
 renderApplicationHealth(p);
 const o=p.options||{},w=p.weather||{},s=p.sensors||{},prof=p.profile||{};
 const output=p.output_control||{};
 const measuredAt=Date.parse(output.temperature_at||'');
 const age=(Date.now()-measuredAt)/1000;
 const measured=p.temperature_source==='HOME_ASSISTANT'&&isnum(output.temperature_c)&&
  Number.isFinite(age)&&age>=-2&&age<=(o.boiler_temperature_max_age_seconds??60);
 text('summary-grid',watts(p.meter_w));
 text('summary-temp',measured?celsius(output.temperature_c):'—');
 text('summary-power',output.mode==='AUTO_REEL'&&output.enabled===true?watts(output.auto_requested_power_w):output.mode==='AUTO'?'0 W':'—');
 text('input-water',measured?celsius(output.temperature_c):'Inconnue · aucune mesure valide');
 text('boiler-headline',measured?celsius(output.temperature_c):'Température inconnue');
 const today=typeof w.gross_today_remaining_kwh==="number"?w.gross_today_remaining_kwh:prof.daily_today_remaining_kwh;
 const tomorrow=typeof w.gross_tomorrow_kwh==="number"?w.gross_tomorrow_kwh:prof.daily_tomorrow_kwh;
 const tomorrowTemp=typeof w.pac_outdoor_tomorrow_c==="number"?w.pac_outdoor_tomorrow_c:prof.tomorrow_temperature_c;
 const pi=s.pi||{},po=s.po||{};
 const gridOK=p.communication_healthy===true;
 tag("grid-tag",gridOK?"ok":p.status==="STARTING"?"":"bad",
  gridOK?"Connecté":p.status==="STARTING"?"Connexion…":"À vérifier");
 text("grid-meta",gridOK?(p.status==="POWER_UNKNOWN"?"Bilan incertain · Pi / Po":
  "Pi = achat · Po = injection"):"Communication non confirmée");
 text("g-pi",kw(pi.kw));
 text("g-po",kw(po.kw));
 text("g-grid",watts(p.meter_w));
 text("grid-headline",watts(p.meter_w));
 text("g-fresh",gridOK?
  "Âge du rapport utilisé : "+(p.meter_age_s??"—")+" s · Pi "+(pi.health||"—")+" · Po "+(po.health||"—")+
  (p.status==="POWER_UNKNOWN"?" · bilan de puissance ambigu.":""):
  "Le compteur ne communique pas de mesure récente exploitable. "+(p.error||""));
 text("weather-entity",o.pac_weather_entity||"weather.maison");
 if(prof.solar_status==="OK"||prof.solar_status==="PARTIAL")tag("pv-tag","ok","Courbe active");
 else if(w.pv_status==="OK"||prof.daily_pv_status==="OK")tag("pv-tag","warn","Totaux OK · courbe absente");
 else if(!w.enabled||w.pv_status==="OFF")tag("pv-tag","","Non évalué");
 else tag("pv-tag","bad","Indisponible");
 text("pv-today",kwh(today));
 text("pv-tomorrow",kwh(tomorrow));
 text("pv-headline","Aujourd’hui "+kwh(today)+" · demain "+kwh(tomorrow));
 text("pv-message",prof.solar_error||w.pv_error||"Prévisions horaires Forecast.Solar disponibles.");
 text("profile-today",kwh(today));
 text("profile-tomorrow",kwh(tomorrow));
 if(isnum(prof.current_temperature_c)||isnum(tomorrowTemp))tag("meteo-tag","ok","Actif");
 else tag("meteo-tag","bad","À vérifier");
 text("meteo-temp",celsius(tomorrowTemp));
 text("meteo-current",celsius(prof.current_temperature_c));
 text("weather-headline","Actuelle "+celsius(prof.current_temperature_c)+" · demain "+celsius(tomorrowTemp));
 text("weather-meta",o.weather_entity_migrated?"Ancienne option weather.home corrigée en mémoire → weather.maison":"Température prévue · "+(o.pac_weather_entity||"weather.maison"));
 const method=w.pac_forecast_method==="HOURLY_MEAN"?"Moyenne horaire":
  w.pac_forecast_method==="DAILY_MIDPOINT_APPROX"?"Min/max (approximation)":
  w.pac_forecast_method==="EXTERNAL_SENSOR"?"Capteur HA": "—";
 text("meteo-method",method);
 text("meteo-message",[prof.current_temperature_error,prof.meteo_error,w.pac_error].filter(Boolean).join(" · ")||"Température actuelle et prévision récupérées indépendamment.");
 text("setting-min",celsius(o.weather_min_c));
 text("setting-max",o.boiler_ceiling_configured?celsius(o.boiler_max_temp_c):"Non configuré");
 text("settings-headline","Min "+celsius(o.weather_min_c)+" · Max "+(o.boiler_ceiling_configured?celsius(o.boiler_max_temp_c):"inconnu"));
 text("settings-summary","Réserve "+watts(o.target_export_w)+" + "+watts(o.weather_reserve_w));
 $("card-settings").hidden = !o.legacy_settings_active;
 text("setting-extra",watts(o.weather_reserve_w));
 text("setting-time",(o.comfort_time||"—")+" · Europe/Zurich");
 text("setting-weather",o.weather_enabled?"Activée":"Désactivée");
 text("setting-pac",o.pac_enabled?"Activé":"Désactivé");
 text("setting-backup",o.grid_backup_enabled?"Autorisé":"Interdit");
 text("settings-migration",o.legacy_settings_active?"Réglages hérités actifs : recopiez-les dans les options HA, puis activez use_addon_settings et redémarrez.":"");
 text("setting-reserve",watts(o.target_export_w));
 if(w.pac_model_status==="ESTIMATED")tag("pac-tag","warn","Estimation active");
 else if(!o.pac_enabled)tag("pac-tag","","Désactivée");
 else tag("pac-tag","bad","Estimation indisponible");
 text("pac-kwh",kwh(w.pac_electric_tomorrow_kwh));
 text("pac-today",kwh(prof.pac_today_remaining_kwh));
 text("pac-headline",o.pac_enabled?"Restant aujourd’hui "+kwh(prof.pac_today_remaining_kwh)+" · demain "+kwh(w.pac_electric_tomorrow_kwh):"Estimation PAC désactivée");
 text("pac-cop",isnum(w.pac_cop_assumed)?String(w.pac_cop_assumed):"—");
 text("pac-reference",kwh(o.pac_reference_electric_kwh));
 text("pac-message",w.pac_error||(!o.pac_enabled?
   "Option pac_enabled désactivée : aucun calcul PAC.":
   "Modèle théorique. Les paramètres COP et consommation restent à calibrer."));
 $("poll-dot").className="statusdot "+(gridOK?"ok":"warn");
 renderCalculation(p);
 text("poll-text","Compteur "+(gridOK?"OK":"à vérifier")+" · PV "+
  (w.pv_status||"non évalué")+" · PAC "+(w.pac_model_status||"non évaluée")+
  " · "+p.version);
}

function renderApplicationHealth(p){
 const health=p.application_health||{}, rows=health.alarms||[], heartbeat=p.heartbeat||{};
 const labels={OK:'OK',DEFAUT:'DÉFAUT',NON_VERIFIABLE:'Non vérifiable',DESACTIVE:'Désactivé'};
 const active=rows.filter(row=>row.state==='DEFAUT').map(row=>row.id);
 text('application-health-state',health.state==='ALARME'?'ALARME'+(active.length?' — '+active.join(', '):''):health.state==='OK'?'OK':'Non vérifiable');
 const globalBadge=$('application-health-state'), healthPanel=$('application-health');
 if(globalBadge)globalBadge.dataset.state=health.state||'NON_VERIFIABLE';
 if(healthPanel)healthPanel.dataset.state=health.state||'NON_VERIFIABLE';
 text('application-health-mode',heartbeat.state==='DEFAUT' ?
  'Heartbeat en défaut · commande non confirmée.' :
  health.state==='ALARME' ? 'Diagnostics dégradés · voir l’indicateur de heartbeat ci-dessous.' :
  health.state==='OK' ? 'Diagnostics normaux · voir l’indicateur de heartbeat ci-dessous.' :
  'Voir l’indicateur de heartbeat ci-dessous pour la fraîcheur des impulsions.');
 const estimate=p.comfort_estimate;
 text('application-health-estimate',estimate?'Estimation du besoin avec hypothèse 35 °C ; ce n’est pas une mesure et elle n’autorise pas la chauffe.':'');
 const container=$('application-alarms'); if(!container)return;
 container.replaceChildren();
 for(const alarm of rows){
  const item=document.createElement('article');item.className='application-alarm';
  const identifier=document.createElement('span');identifier.className='application-alarm-id';identifier.textContent=alarm.id;
  const name=document.createElement('strong');name.textContent=alarm.name;
  const state=document.createElement('span');state.className='application-badge';state.dataset.state=alarm.state;state.textContent=labels[alarm.state]||'Non vérifiable';
  const description=document.createElement('p');description.textContent=alarm.description;
  const when=document.createElement('time');
  if(alarm.at){const stamp=new Date(alarm.at);when.textContent=Number.isNaN(stamp.valueOf())?alarm.at:stamp.toLocaleString('fr-CH');when.dateTime=alarm.at;}
  else when.textContent='Aucun diagnostic daté';
  item.append(identifier,name,state,description,when);container.append(item);
 }
}

function renderCalculation(p){
 const o=p.options||{},w=p.weather||{},prof=p.profile||{};
 text('calc-pv',kwh(w.gross_tomorrow_kwh??prof.daily_tomorrow_kwh));
 text('calc-pac',kwh(w.pac_electric_tomorrow_kwh));
 text('calc-surplus',isnum(w.pac_electric_tomorrow_kwh)?kwh(w.usable_tomorrow_assumed_kwh):'Inconnu');
 text('calc-note','Consommations maison et batterie inconnues : aucun surplus futur n’est déduit de la production PV brute. '+(!isnum(w.pac_electric_tomorrow_kwh)?'Estimation PAC absente.':''));
 const adaptive=w.status==='ADAPTIVE';
 text('calc-target',celsius(adaptive?w.target_c:o.boiler_max_temp_c));
 text('calc-target-state',adaptive?'Consigne calculée maintenant':'Plafond manuel · adaptation inactive');
 const output=p.output_control||{};
 const requested=output.mode==='AUTO_REEL'?(output.enabled?output.auto_requested_power_w:0):output.mode==='AUTO'?0:null;
 text('calc-heat',watts(requested));text('calc-heat-state',output.mode==='AUTO'?'Arrêt':output.mode==='MANUEL_REEL'?'Commande manuelle':'État : '+(output.reason||'—'));
 text('calc-protection',!isnum(p.meter_w)?'Mesure inexploitable':o.grid_backup_enabled?'Appoint confort autorisé':'Surplus uniquement');
 text('calc-reserve','Réserve appliquée : '+watts(o.target_export_w+(o.weather_enabled?o.weather_reserve_w:0)));
 text('calc-decision',w.decision||'Stratégie non évaluée');
 text('fallback-pv',w.pv_status==='UNAVAILABLE'?'Repli actif · prévision journalière absente':(prof.daily_pv_status==='OK'||w.pv_status==='OK')?'Totaux journaliers disponibles':'État non confirmé');
 text('fallback-weather',prof.meteo_status==='UNAVAILABLE'?'Météo horaire indisponible':w.pac_model_status==='UNAVAILABLE'?'Prévision PAC indisponible':!o.pac_enabled?'PAC désactivée · exclue du modèle':prof.meteo_status==='PARTIAL'?'Prévisions horaires partielles':prof.meteo_status==='OK'?'Prévisions horaires disponibles':'État non confirmé');
 text('fallback-grid',!isnum(p.meter_w)?'Repli actif · mesure inexploitable':p.meter_w>=-(adaptive?w.reserve_for_other_uses_w:o.target_export_w)?'Surplus insuffisant · '+(p.control_state||'—'):'Injection disponible · '+(p.control_state||'—'));
 renderComfort(p.comfort,o);
 window.SurplusChart?.setStatus(p);
}
function swissInstant(value){
 const stamp=typeof value==='string'?Date.parse(value):NaN;
 return Number.isFinite(stamp)?new Intl.DateTimeFormat('fr-CH',{timeZone:'Europe/Zurich',day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit',timeZoneName:'shortOffset'}).format(new Date(stamp)):'—';
}
function renderComfort(plan,options={}){
 const c=plan||{};
 const labels={MINIMUM_REACHED:'Minimum atteint',WAITING_SURPLUS:'Attente du surplus',BACKUP_DUE:'Départ prudent atteint',INSUFFICIENT_TIME:'Temps insuffisant',MISSED_DEADLINE:'Échéance manquée',MISSED_DISABLED:'Échéance manquée · appoint interdit',THERMAL_LIMIT:'Plafond thermique atteint',WAITING:'En attente',FAULT:'Calcul inhibé',INVALID:'Paramètres invalides',COMFORT_MET:'Minimum atteint',SATISFIED:'Minimum atteint',SOLAR_EXPECTED:'Solaire attendu',WAIT_SOLAR:'Attente du solaire',WAITING_SOLAR:'Attente du solaire',WAIT_BACKUP:'Appoint différé',WAITING_BACKUP:'Appoint différé',BACKUP_REQUIRED:'Appoint requis',BACKUP_ACTIVE:'Appoint demandé',BACKUP:'Appoint demandé',IMPOSSIBLE:'Échéance hors capacité',BACKUP_DISABLED:'Appoint interdit',UNAVAILABLE:'Plan indisponible',SURPLUS_ONLY:'Surplus uniquement'};
 const code=c.status||'WAITING';
 tag('comfort-status',!plan?'':c.theoretical_possible===false||['FAULT','INVALID','IMPOSSIBLE','UNAVAILABLE'].includes(code)?'bad':c.backup_power_w>0?'warn':'ok',labels[code]||code);
 text('comfort-reason',(c.reason||'Aucun plan reçu. Les valeurs manquantes ne valent pas zéro.')+(c.gate_state?' Filtre de commande : '+c.gate_state+'.':''));
 text('comfort-deadline',swissInstant(c.deadline));text('comfort-start',swissInstant(c.latest_start));
 text('comfort-need',kwh(c.energy_needed_kwh));text('comfort-solar',kwh(c.solar_before_deadline_kwh));
 text('comfort-backup-energy',kwh(c.backup_energy_kwh));text('comfort-backup-power',watts(c.backup_power_w));
 text('comfort-feasibility',c.theoretical_possible===true?'Capacité suffisante selon le modèle ; confort réel non garanti.':c.theoretical_possible===false?'Minimum non garanti : délai, puissance ou autorisation insuffisants.':'Faisabilité non évaluée.');
 text('comfort-forecast','Prévisions : '+(c.forecast_status||'non évaluées')+' · appoint réseau '+(options.grid_backup_enabled===true?'autorisé uniquement jusqu’au minimum':'interdit')+'.');
}
let updating=false;
async function update(){
 if(updating)return;updating=true;
 try{const response=await fetch("./api/status",{cache:"no-store",signal:AbortSignal.timeout(12000)});
 if(!response.ok)throw Error('HTTP '+response.status);render(await response.json());
 document.body.classList.remove('disconnected');
 }catch(error){document.body.classList.add('disconnected');text('poll-text','Connexion perdue · valeurs précédentes périmées');
 renderApplicationHealth({application_health:{state:'NON_VERIFIABLE',alarms:(currentData?.application_health?.alarms||[]).map(a=>({...a,state:'NON_VERIFIABLE'}))}});
 text('application-health-mode','Connexion perdue · état actuel non confirmé.');
 tag('grid-tag','bad','Données périmées');text('calc-protection','État inconnu');
 text('fallback-grid','Communication interface perdue · état non confirmé');
 tag('comfort-status','bad','Plan périmé');text('comfort-reason','Communication perdue : le plan affiché ne confirme pas l’état actuel.');
 for(const id of ['input-water'])text(id,'Inconnu · connexion perdue');
 text('boiler-headline','État inconnu · connexion perdue');
 }finally{updating=false;}
}
$('expand-all').onclick=()=>{expanded=!expanded;document.querySelectorAll('#page-inputs details').forEach(e=>e.open=expanded);text('expand-all',expanded?'Masquer les détails':'Voir les détails des entrées');};
update();setInterval(update,5000);
if(/\/profil(?:\.html)?$/.test(location.pathname)&&!location.hash)location.hash='profil';
const boilerFrame=$('boiler-frame');
boilerFrame.addEventListener('load',()=>{
 try{
  const doc=boilerFrame.contentDocument,main=doc.querySelector('main');
  const resize=()=>{boilerFrame.style.height=Math.ceil(main.getBoundingClientRect().height+4)+'px';};
  new ResizeObserver(resize).observe(main);resize();
 }catch(e){boilerFrame.style.height='900px';}
});

function openLinkedDetails(){
 const id=decodeURIComponent(location.hash.slice(1));
 const target=id&&document.getElementById(id);
 if(target){for(let node=target;node;node=node.parentElement){if(node.tagName==='DETAILS')node.open=true;}}
}
window.addEventListener('hashchange',openLinkedDetails);openLinkedDetails();
document.querySelector('#profil').addEventListener('toggle',()=>window.dispatchEvent(new Event('resize')));
