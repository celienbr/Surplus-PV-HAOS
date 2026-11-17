/* Commands use relative URLs so Home Assistant Ingress retains its prefix. */
"use strict";
(()=>{
 const el=id=>document.getElementById(id);
 const mode=el('output-mode');
 const fmt=n=>typeof n==='number'&&Number.isFinite(n)?n.toFixed(4):'—';
 let state=null,token=null,busy=false,editing=false,selecting=false,queue=[];
 function paint(s,force=false){if((selecting||busy)&&!force)return;if(mode.value!==s.mode)mode.value=s.mode;mode.dataset.mode=s.mode;document.querySelectorAll('[data-output-mode]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.outputMode===s.mode)));}
 function render(s){
  state=s;token=s.token||token;window.renderPowerGauge(s);paint(s);
  el('output-restore').textContent=s.restore_status||'';
  const manual=s.mode==='MANUEL_REEL';
  el('manual-controls').hidden=!manual;
  el('manual-target').disabled=manual;
  el('manual-target').max=s.thermal_ceiling_c;
  if(manual)el('manual-target').value=s.target_c;
  else if(Number(el('manual-target').value)>s.thermal_ceiling_c)el('manual-target').value=s.thermal_ceiling_c;
  if(!editing&&!busy){el('manual-power').value=s.manual_percent;el('manual-percent').textContent=s.manual_percent+' %';}
  const communication=s.dac.communication==='INACTIVE'?'inactif — non initialisé':s.dac.communication;
  el('output-driver').textContent=`Pilote GP8600 · I²C ${communication} · dernier ACK ${fmt(s.dac.last_acknowledged_ma)} mA (commande, pas mesure) · adresse ${s.dac.address??'—'}`;
  const thermal=s.boiler_temperature_diagnostic||{};
  el('output-state').textContent=s.reason+' · température '+fmt(s.temperature_c)+' °C'+(thermal.reason?' · '+thermal.reason:'')+(s.heat_requested?' · chauffe demandée':' · aucune chauffe demandée')+` · calibration ${s.power_calibrated?'confirmée':'non confirmée : AUTO inhibé'}`;
  if(s.error)el('output-error').textContent=s.error+' ; état physique inconnu.';
 }
 async function refresh(){if(busy)return;try{const r=await fetch('./api/output',{cache:'no-store'});if(!r.ok)throw Error('HTTP '+r.status);const s=await r.json();if(!busy&&(!state||s.revision>=state.revision))render(s);}catch(e){window.renderPowerGauge(null);el('output-error').textContent='Communication perdue : '+e;el('output-state').textContent='État de sortie inconnu';}}
 async function send(command){
  const last=queue[queue.length-1];if(command.action==='stop')queue=[command];else if(last&&last.action==='manual'&&command.action==='manual')queue[queue.length-1]=command;else queue.push(command);
  if(busy)return;busy=true;el('output-error').textContent='';
  try{while(queue.length){const r=await fetch('./api/output',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token,command:queue.shift()})});if(!r.ok){let detail='';try{detail=(await r.json()).error||'';}catch(_e){}throw Error('Commande refusée (HTTP '+r.status+')'+(detail?' : '+detail:''));}}}
  catch(e){queue=[];el('output-error').textContent=String(e);if(state)paint(state,true);}
  finally{busy=false;editing=false;await refresh();}
 }
 document.querySelectorAll('[data-output-mode]').forEach(b=>b.addEventListener('click',()=>{if(busy||!state||b.dataset.outputMode===state.mode)return;mode.value=b.dataset.outputMode;mode.dispatchEvent(new Event('change'));}));
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
 el('output-recover').onclick=()=>{if(confirm('Confirmer l’arrêt et la réinitialisation du DAC à zéro ?'))send({action:'recover',confirmed:true});};
 async function calibration(){try{const r=await fetch('./api/dac-measurements',{cache:'no-store'});if(!r.ok)throw Error('HTTP '+r.status);const data=await r.json();el('calibration-history').textContent=`${data.points.length} relevé(s) historique(s) · `+(data.fit?`gain proposé ${fmt(data.fit.gain)}, décalage ${fmt(data.fit.offset_ma)} mA (non appliqué)`:'aucune correction calculée');}catch(e){el('calibration-history').textContent='Relevés historiques indisponibles : '+e;}}
 calibration();
 refresh();setInterval(refresh,1000);
})();
