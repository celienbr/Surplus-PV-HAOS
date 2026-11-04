/* All URLs relative for HA Ingress. One request in flight, coalesced sliders. */
"use strict";
(()=>{
 const el=id=>document.getElementById(id);
 let state=null,token=null,busy=false,queue=[],editing=false;
 const fmt=n=>typeof n==='number'&&Number.isFinite(n)?n.toFixed(4):'—';
 function render(s){
  state=s;token=s.token||token;
  el('manual-target').max=s.thermal_ceiling_c;
  if(s.mode==='MANUEL')el('manual-target').value=s.target_c;
  else if(Number(el('manual-target').value)>s.thermal_ceiling_c)el('manual-target').value=s.thermal_ceiling_c;
  el('output-driver').textContent=`Pilote ${s.dac.driver} · I²C ${s.dac.communication} · câblage ${s.connection} · dernier ACK ${fmt(s.dac.last_acknowledged_ma)} mA (consigne, pas mesure)`;
  el('output-mode').value=s.mode;
  el('manual-controls').hidden=s.mode!=='MANUEL';
  el('bench-controls').hidden=s.mode!=='BANC_DAC';
  el('manual-target').disabled=s.mode==='MANUEL';
  el('bench-enable').checked=s.mode==='BANC_DAC'&&s.enabled;
  el('bench-requested').textContent=fmt(s.requested_ma)+' mA';
  el('output-state').textContent=s.reason+' · température surveillée '+fmt(s.temperature_c)+' °C'+(s.heat_requested?' · chauffe demandée':' · aucune chauffe demandée');
  if(!editing&&!busy){
   el('manual-power').value=s.manual_percent;
   el('manual-percent').textContent=s.manual_percent+' %';
   el('bench-slider').value=el('bench-current').value=s.requested_ma;
  }
  if(s.error)el('output-error').textContent=s.error+' ; la sortie physique peut être inconnue.';
  preview();
 }
 async function refresh(){
  if(busy)return;
  try{const r=await fetch('./api/output',{cache:'no-store'});if(!r.ok)throw Error('HTTP '+r.status);const s=await r.json();if(!busy&&(!state||s.revision>=state.revision))render(s);}
  catch(e){el('output-error').textContent='Communication perdue : '+e;el('output-state').textContent='État de sortie inconnu';}
 }
 async function post(url,command){
  const r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token,command})});
  if(!r.ok)throw Error('Commande refusée (HTTP '+r.status+'). Vérifier mode, température, câblage et état I²C.');
 }
 async function send(command){
  // Coalesce only sliders of the same type; mode barriers await the current write.
  const last=queue[queue.length-1];
  if(last&&last.action===command.action&&['manual','bench'].includes(command.action))queue[queue.length-1]=command;
  else queue.push(command);
  if(busy)return;
  busy=true;el('output-error').textContent='';
  try{while(queue.length){const next=queue.shift();await post('./api/output',next);}}
  catch(e){queue=[];el('output-error').textContent=String(e);}
  finally{busy=false;editing=false;await refresh();}
 }
 el('output-mode').onchange=()=>send({action:'mode',mode:el('output-mode').value,
  target_c:Number(el('manual-target').value),isolated:el('bench-isolated').checked});
 el('manual-power').oninput=()=>{editing=true;el('manual-percent').textContent=el('manual-power').value+' %';send({action:'manual',percent:Number(el('manual-power').value)});};
 function bench(){editing=true;send({action:'bench',enabled:el('bench-enable').checked,current_ma:Number(el('bench-current').value)});}
 el('bench-slider').oninput=()=>{el('bench-current').value=el('bench-slider').value;bench();};
 el('bench-current').oninput=()=>{el('bench-slider').value=el('bench-current').value;if(el('bench-current').value!==''&&el('bench-current').checkValidity())bench();};
 el('bench-enable').onchange=bench;
 el('output-recover').onclick=()=>send({action:'recover'});
 function preview(){
  const raw=el('bench-voltage').value,v=Number(raw),r=Number(el('bench-resistance').value);
  const valid=raw!==''&&Number.isFinite(v)&&r>0;
  const ma=valid?v/r*1000:null;
  el('bench-measured').textContent=fmt(ma)+' mA';
  el('bench-error').textContent=fmt(ma===null?null:ma-(state?.requested_ma||0))+' mA';
 }
 el('bench-voltage').oninput=el('bench-resistance').oninput=preview;
 async function records(){
  const r=await fetch('./api/dac-measurements',{cache:'no-store'});if(!r.ok)throw Error('Relevés indisponibles');
  const p=await r.json();el('bench-points').replaceChildren();
  for(const point of p.points){const tr=document.createElement('tr');for(const value of [fmt(point.requested_ma),fmt(point.measured_ma),fmt(point.error_ma),point.driver==='simulated'?'SIMULATION / saisie utilisateur':'Multimètre / saisie utilisateur']){const td=document.createElement('td');td.textContent=value;tr.append(td);}el('bench-points').append(tr);}
  el('bench-fit').textContent=p.fit?`Calibration proposée : gain ${fmt(p.fit.gain)}, décalage ${fmt(p.fit.offset_ma)} mA ; résidu max ${fmt(p.fit.max_residual_ma)} mA. Commande = (cible − décalage) / gain. Non appliquée ; revue des mesures et limites requise.`:'Calibration : au moins deux relevés distincts espacés de 4 mA sont requis.';
 }
 el('bench-record').onclick=async()=>{
  try{
   if(busy||queue.length)throw Error('Attendre la fin de transmission de la consigne');
   if(el('bench-voltage').value===''||!el('bench-voltage').checkValidity()||!el('bench-resistance').checkValidity())throw Error('Saisir un relevé multimètre valide');
   await post('./api/dac-measurements',{voltage_v:Number(el('bench-voltage').value),resistance_ohm:Number(el('bench-resistance').value)});
   await records();el('bench-voltage').value='';preview();
  }catch(e){el('output-error').textContent=String(e);}
 };
 refresh();records().catch(e=>{el('output-error').textContent=String(e);});setInterval(refresh,1000);
})();
