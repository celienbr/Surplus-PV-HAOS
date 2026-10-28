
"use strict";
let currentData=null;
let expanded=false;
let settingsDirty=false,settingsBusy=false;
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
 const o=p.options||{},w=p.weather||{},s=p.sensors||{},prof=p.profile||{};
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
 text("setting-max",celsius(o.demo_max_c));
 text("settings-headline","Min "+celsius(o.weather_min_c)+" · Max "+celsius(o.demo_max_c));
 text("settings-summary","Réserve "+watts(o.target_export_w)+" + "+watts(o.weather_reserve_w)+" · maison demain "+kwh(o.house_tomorrow_kwh)+" · batterie "+kwh(o.battery_tomorrow_kwh));
 text("setting-house",kwh(o.house_today_kwh)+" / "+kwh(o.house_tomorrow_kwh));text("setting-extra",watts(o.weather_reserve_w));
 text("setting-time",(o.comfort_time||"—")+" · Europe/Zurich");
 syncUserForm(o,w);
 text("setting-reserve",watts(o.target_export_w));
 text("setting-battery",kwh(o.battery_tomorrow_kwh));
 if(w.pac_model_status==="ESTIMATED")tag("pac-tag","warn","Simulation active");
 else if(!o.pac_enabled)tag("pac-tag","","Désactivée");
 else tag("pac-tag","bad","Simulation indisponible");
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

function renderCalculation(p){
 const o=p.options||{},w=p.weather||{},prof=p.profile||{};
 text('calc-pv',kwh(w.gross_tomorrow_kwh??prof.daily_tomorrow_kwh));
 text('calc-house',kwh(o.house_tomorrow_kwh));text('calc-pac',kwh(w.pac_electric_tomorrow_kwh));
 text('calc-battery',kwh(o.battery_tomorrow_kwh));
 text('calc-surplus',isnum(w.pac_electric_tomorrow_kwh)?kwh(w.usable_tomorrow_assumed_kwh):'Inconnu');
 text('calc-note','Bilan de demain, borné à zéro par le moteur. La réserve instantanée en W est traitée séparément. '+(!isnum(w.pac_electric_tomorrow_kwh)?'Estimation PAC absente : aucun surplus journalier affirmé.':''));
 const adaptive=w.status==='ADAPTIVE';
 text('calc-target',celsius(adaptive?w.target_c:o.demo_max_c));
 text('calc-target-state',adaptive?'Consigne calculée maintenant · simulation':'Plafond manuel · adaptation inactive');
 text('calc-heat',watts(p.simulated_boiler_w));text('calc-heat-state','État du régulateur : '+(p.simulation_state||'—'));
 text('calc-protection',!isnum(p.meter_w)?'Mesure inexploitable':o.grid_backup_enabled?'Appoint confort autorisé (simulation)':'Surplus uniquement');
 text('calc-reserve','Réserve appliquée : '+watts(o.target_export_w+(o.weather_enabled?o.weather_reserve_w:0)));
 text('calc-decision',w.decision||'Stratégie non évaluée');
 text('fallback-pv',w.pv_status==='UNAVAILABLE'?'Repli actif · prévision journalière absente':(prof.daily_pv_status==='OK'||w.pv_status==='OK')?'Totaux journaliers disponibles':'État non confirmé');
 text('fallback-weather',prof.meteo_status==='UNAVAILABLE'?'Météo horaire indisponible':w.pac_model_status==='UNAVAILABLE'?'Prévision PAC indisponible':!o.pac_enabled?'PAC désactivée · exclue du modèle':prof.meteo_status==='PARTIAL'?'Prévisions horaires partielles':prof.meteo_status==='OK'?'Prévisions horaires disponibles':'État non confirmé');
 text('fallback-grid',!isnum(p.meter_w)?'Repli actif · mesure inexploitable':p.meter_w>=-(adaptive?w.reserve_for_other_uses_w:o.target_export_w)?'Surplus insuffisant · '+(p.simulation_state||'—'):'Injection disponible · '+(p.simulation_state||'—'));
 renderComfort(p.comfort,o);
 window.SurplusChart?.setStatus(p);
}
function swissInstant(value){
 const stamp=typeof value==='string'?Date.parse(value):NaN;
 return Number.isFinite(stamp)?new Intl.DateTimeFormat('fr-CH',{timeZone:'Europe/Zurich',day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit',timeZoneName:'shortOffset'}).format(new Date(stamp)):'—';
}
function renderComfort(plan,options={}){
 const c=plan||{};
 const labels={MINIMUM_REACHED:'Minimum atteint',WAITING_SURPLUS:'Attente du surplus',BACKUP_DUE:'Départ prudent atteint',INSUFFICIENT_TIME:'Temps insuffisant',MISSED_DEADLINE:'Échéance manquée',MISSED_DISABLED:'Échéance manquée · appoint interdit',THERMAL_LIMIT:'Plafond thermique atteint',WAITING:'En attente',FAULT:'Calcul inhibé',INVALID:'Paramètres invalides',COMFORT_MET:'Minimum atteint',SATISFIED:'Minimum atteint',SOLAR_EXPECTED:'Solaire attendu',WAIT_SOLAR:'Attente du solaire',WAITING_SOLAR:'Attente du solaire',WAIT_BACKUP:'Appoint différé',WAITING_BACKUP:'Appoint différé',BACKUP_REQUIRED:'Appoint requis',BACKUP_ACTIVE:'Appoint simulé',BACKUP:'Appoint simulé',IMPOSSIBLE:'Échéance hors capacité',BACKUP_DISABLED:'Appoint interdit',UNAVAILABLE:'Plan indisponible',SURPLUS_ONLY:'Surplus uniquement'};
 const code=c.status||'WAITING';
 tag('comfort-status',!plan?'':c.theoretical_possible===false||['FAULT','INVALID','IMPOSSIBLE','UNAVAILABLE'].includes(code)?'bad':c.backup_power_w>0?'warn':'ok',labels[code]||code);
 text('comfort-reason',(c.reason||'Aucun plan reçu. Les valeurs manquantes ne valent pas zéro.')+(c.gate_state?' Filtre de commande : '+c.gate_state+'.':''));
 text('comfort-deadline',swissInstant(c.deadline));text('comfort-start',swissInstant(c.latest_start));
 text('comfort-need',kwh(c.energy_needed_kwh));text('comfort-solar',kwh(c.solar_before_deadline_kwh));
 text('comfort-backup-energy',kwh(c.backup_energy_kwh));text('comfort-backup-power',watts(c.backup_power_w));
 text('comfort-feasibility',c.theoretical_possible===true?'Capacité suffisante selon le modèle ; confort réel non garanti.':c.theoretical_possible===false?'Minimum non garanti : délai, puissance ou autorisation insuffisants.':'Faisabilité non évaluée.');
 text('comfort-forecast','Prévisions : '+(c.forecast_status||'non évaluées')+' · appoint réseau '+(options.grid_backup_enabled===true?'autorisé uniquement jusqu’au minimum':'interdit')+' · simulation.');
}
function syncUserForm(o,w){
 if(settingsDirty||settingsBusy)return;
 const fields={"user-max":o.demo_max_c,"user-min":o.weather_min_c,
               "user-time":o.comfort_time||"06:00"};
 for(const [id,value] of Object.entries(fields)){
   if($(id))$(id).value=value??"";
 }
 $("user-weather").checked=o.weather_enabled===true;
 $("user-pac").checked=o.pac_enabled===true;
 $("user-backup").checked=o.grid_backup_enabled===true;
}
for(const id of ["user-max","user-min","user-time","user-weather","user-pac","user-backup"]){
 $(id).addEventListener("input",()=>{settingsDirty=true;text("user-save-status","Modifications non enregistrées.");});
}
$("user-settings-form").addEventListener("submit",async event=>{
 event.preventDefault();if(settingsBusy)return;
 const settings={
   demo_max_c:Number($("user-max").value),
   weather_min_c:Number($("user-min").value),
   comfort_time:$("user-time").value,
   weather_enabled:$("user-weather").checked,
   pac_enabled:$("user-pac").checked,
   grid_backup_enabled:$("user-backup").checked
 };
 if(!Number.isFinite(settings.demo_max_c)||!Number.isFinite(settings.weather_min_c)||
    settings.weather_min_c>settings.demo_max_c){
   text("user-save-status","Erreur : température minimale supérieure au maximum, ou valeur invalide.");return;
 }
 const token=currentData?.weather_toggle_token;
 if(!token){text("user-save-status","Session non initialisée.");return;}
 settingsBusy=true;$("user-save").disabled=true;
 try{
   const response=await fetch("./api/user-settings",{
     method:"POST",headers:{"Content-Type":"application/json"},
     body:JSON.stringify({settings,token}),cache:"no-store",
     signal:AbortSignal.timeout(12000)
   });
   if(!response.ok)throw Error("HTTP "+response.status);
   settingsDirty=false;
   text("user-save-status","Paramètres enregistrés localement. Les prévisions se recalculent.");
   await update();
 }catch(error){text("user-save-status","Enregistrement impossible : "+String(error));}
 finally{settingsBusy=false;$("user-save").disabled=false;}
});
let updating=false;
async function update(){
 if(updating)return;updating=true;
 try{const response=await fetch("./api/status",{cache:"no-store",signal:AbortSignal.timeout(12000)});
 if(!response.ok)throw Error('HTTP '+response.status);render(await response.json());
 document.body.classList.remove('disconnected');
 }catch(error){document.body.classList.add('disconnected');text('poll-text','Connexion perdue · valeurs précédentes périmées');
 tag('grid-tag','bad','Données périmées');text('calc-protection','État inconnu');
 text('fallback-grid','Communication interface perdue · état non confirmé');
 tag('comfort-status','bad','Plan périmé');text('comfort-reason','Communication perdue : le plan affiché ne confirme pas l’état actuel.');
 }finally{updating=false;}
}
$('expand-all').onclick=()=>{expanded=!expanded;document.querySelectorAll('#page-inputs details').forEach(e=>e.open=expanded);text('expand-all',expanded?'Masquer les détails':'Voir les détails des entrées');};
update();setInterval(update,5000);
if(/\/profil(?:\.html)?$/.test(location.pathname)&&!location.hash)location.hash='profil';
