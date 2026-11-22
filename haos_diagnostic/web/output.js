/* Commands use relative URLs so Home Assistant Ingress retains its prefix. */
"use strict";
(()=>{
 const el=id=>document.getElementById(id);
 const mode=el('output-mode');
 const fmt=n=>typeof n==='number'&&Number.isFinite(n)?n.toFixed(4):'—';
 let state=null,token=null,busy=false,editing=false,selecting=false,queue=[],hardwareError=false,lastDisplayMode=null;
 function selected(s){return s.startup_pending?({MANUEL:'MANUEL_REEL',AUTO:'AUTO_REEL'})[s.requested_mode]||s.mode:s.mode;}
 function paint(s,force=false){if((selecting||busy)&&!force)return;const current=selected(s);if(mode.value!==current)mode.value=current;mode.dataset.mode=current;document.querySelectorAll('[data-output-mode]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.outputMode===current)));}
 function paintSummary(s){
  const requested=selected(s),manual=requested==='MANUEL_REEL',fresh=s.boiler_temperature_diagnostic?.status==='FRESH';
  let value='—';
  if(!s.startup_pending && !s.error && !s.dac_recovery?.pending){
   if(s.mode==='AUTO')value='0 W';
   else if(manual&&s.enabled===true&&Number.isFinite(s.manual_percent))value=s.manual_percent+' % DAC';
   else if(s.mode==='AUTO_REEL'&&s.enabled===false)value='0 W';
   else if(s.mode==='AUTO_REEL'&&s.enabled===true&&Number.isFinite(s.auto_requested_power_w))value=Math.round(s.auto_requested_power_w).toLocaleString('fr-CH')+' W';
  }
  el('boiler-quick-power').textContent=value;
  el('boiler-quick-power-label').textContent=manual?'Commande DAC demandée':'Puissance demandée';
  document.body.dataset.boilerMode=requested;
  const message=s.error?'Chauffe suspendue · défaut de commande.':
   s.startup_pending?'Reprise en attente · commande non confirmée.':
   !fresh?'Chauffe suspendue · température non validée.':
   s.heat_requested?'Chauffe demandée · état physique non mesuré.':'Aucune chauffe demandée · état physique non mesuré.';
  el('boiler-brief').textContent=message;
  const alert=el('boiler-alert');
  alert.hidden=!s.error&&!s.startup_pending&&fresh;
  alert.textContent=s.error?'Défaut actif : voir les diagnostics.':
   s.startup_pending?'Reprise en attente : confirmer le mode avant une nouvelle commande.':
   !fresh?'Température du boiler non validée : commande de chauffe suspendue.':'';
 }
 function render(s){
  state=s;token=s.token||token;window.renderPowerGauge(s);paint(s);paintSummary(s);
  el('output-restore').textContent=s.restore_status||'';
  const manual=selected(s)==='MANUEL_REEL';
  if(manual&&lastDisplayMode!=='MANUEL_REEL')el('manual-settings').open=true;
  if(!manual&&lastDisplayMode==='MANUEL_REEL')el('manual-settings').open=false;
  lastDisplayMode=selected(s);
  el('manual-controls').hidden=!manual;
  el('manual-target').disabled=manual;
  el('manual-target').max=s.thermal_ceiling_c;
  if(manual)el('manual-target').value=s.target_c??s.saved_manual_target_c??s.thermal_ceiling_c;
  else if(Number(el('manual-target').value)>s.thermal_ceiling_c)el('manual-target').value=s.thermal_ceiling_c;
  if(!editing&&!busy){const percent=s.startup_pending?s.saved_manual_percent??0:s.manual_percent;el('manual-power').value=percent;el('manual-percent').textContent=percent+' %';}
  const communication=s.dac.communication==='INACTIVE'?'inactif — non initialisé':s.dac.communication==='ACK'?'dernier échange acquitté':s.dac.communication;
  const test=s.startup_self_test||{};
  const recovery=s.dac_recovery;
  const progress=recovery?.pending?` · récupération à 0 mA : ${recovery.valid_zero_tests}/${recovery.required_zero_tests} essais valides`:'';
  el('output-driver').textContent=`Pilote GP8600 · I²C ${communication} · âge du dernier ACK ${fmt(s.last_ack_age_s)} s · dernière commande ${fmt(s.dac.last_acknowledged_ma)} mA (pas une mesure) · adresse ${s.dac.address??'—'} · autotest ${test.result||'non effectué'} ${test.at||''}`+progress;
  const thermal=s.boiler_temperature_diagnostic||{};
  el('output-state').textContent=s.reason+' · température '+fmt(s.temperature_c)+' °C'+(thermal.reason?' · '+thermal.reason:'')+(s.heat_requested?' · chauffe demandée':' · aucune chauffe demandée')+` · calibration ${s.power_calibrated?'confirmée':'non confirmée : AUTO inhibé'}`;
  if(s.error)el('output-error').textContent=s.error+' ; état physique inconnu.';
  else if(hardwareError)el('output-error').textContent='';
  hardwareError=Boolean(s.error);
 }
 async function refresh(){if(busy)return;try{const r=await fetch('./api/output',{cache:'no-store'});if(!r.ok)throw Error('HTTP '+r.status);const s=await r.json();if(!busy&&(!state||s.revision>=state.revision))render(s);}catch(e){window.renderPowerGauge(null);el('output-error').textContent='Communication perdue : '+e;el('output-state').textContent='État de sortie inconnu';el('boiler-quick-power').textContent='—';el('boiler-brief').textContent='État inconnu · connexion perdue.';el('boiler-alert').hidden=false;el('boiler-alert').textContent='Communication perdue : ne pas se fier aux dernières valeurs affichées.';}}
 async function send(command){
  const last=queue[queue.length-1];if(command.action==='stop')queue=[command];else if(last&&last.action==='manual'&&command.action==='manual')queue[queue.length-1]=command;else queue.push(command);
  if(busy)return;busy=true;el('output-error').textContent='';
  try{while(queue.length){const r=await fetch('./api/output',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token,command:queue.shift()})});if(!r.ok){let detail='';try{detail=(await r.json()).error||'';}catch(_e){}throw Error('Commande refusée (HTTP '+r.status+')'+(detail?' : '+detail:''));}}}
  catch(e){queue=[];el('output-error').textContent=String(e);if(state)paint(state,true);}
  finally{busy=false;editing=false;await refresh();}
 }
 document.querySelectorAll('[data-output-mode]').forEach(b=>b.addEventListener('click',()=>{if(busy||!state||b.dataset.outputMode===selected(state))return;mode.value=b.dataset.outputMode;mode.dispatchEvent(new Event('change'));}));
 mode.addEventListener('focus',()=>{selecting=true;});
 mode.addEventListener('pointerdown',()=>{selecting=true;});
 mode.addEventListener('touchstart',()=>{selecting=true;},{passive:true});
 mode.addEventListener('blur',()=>{selecting=false;setTimeout(()=>{if(!selecting&&!busy&&state)paint(state);},0);});
 mode.onchange=()=>{
  selecting=false;
  if(mode.value==='AUTO'){
   if(!confirm('Confirmer la demande d’arrêt ?')){if(state)paint(state,true);return;}
   send({action:'stop'});
  }else send({action:'mode',mode:mode.value,target_c:Number(el('manual-target').value),confirmed:true});
 };
 el('manual-power').oninput=()=>{editing=true;el('manual-percent').textContent=el('manual-power').value+' %';send({action:'manual',percent:Number(el('manual-power').value)});};
 async function calibration(){try{const r=await fetch('./api/dac-measurements',{cache:'no-store'});if(!r.ok)throw Error('HTTP '+r.status);const data=await r.json();el('calibration-history').textContent=`${data.points.length} relevé(s) historique(s) · `+(data.fit?`gain proposé ${fmt(data.fit.gain)}, décalage ${fmt(data.fit.offset_ma)} mA (non appliqué)`:'aucune correction calculée');}catch(e){el('calibration-history').textContent='Relevés historiques indisponibles : '+e;}}
 calibration();
 refresh();setInterval(refresh,1000);
})();
