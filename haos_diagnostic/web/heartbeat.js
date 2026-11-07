"use strict";
(()=>{
 const el=id=>document.getElementById(id);
 const fmt=n=>typeof n==='number'&&Number.isFinite(n)?n.toFixed(4):'—';
 let token=null;
 async function refresh(){
  try{
   const r=await fetch('./api/heartbeat',{cache:'no-store'});if(!r.ok)throw Error('HTTP '+r.status);
   const s=await r.json();token=s.token;
   el('hb-state').textContent=`${s.state} · ${s.driver||'désactivé'}${s.gpio==null?'':' · GPIO '+s.gpio} · dernière impulsion monotone ${fmt(s.last_pulse_monotonic)} s${s.reason?' · '+s.reason:''}`;
  }catch(e){el('hb-error').textContent='État heartbeat inconnu : '+e;}
 }
 async function send(command){
  if(!token)throw Error('Session non initialisée');
  const r=await fetch('./api/heartbeat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token,command})});
  if(!r.ok)throw Error('Commande refusée (HTTP '+r.status+')');
 }
 el('hb-start').onclick=async()=>{
  if(!confirm('Confirmer l’activation du heartbeat sélectionné et les vérifications de raccordement ?'))return;
  try{await send({action:'start',driver:el('hb-driver').value,confirm:true,period_s:Number(el('hb-period').value),width_s:Number(el('hb-width').value)});el('hb-error').textContent='';}
  catch(e){el('hb-error').textContent=String(e);}finally{await refresh();}
 };
 el('hb-stop').onclick=async()=>{try{await send({action:'stop'});el('hb-error').textContent='';}catch(e){el('hb-error').textContent=String(e);}finally{await refresh();}};
 refresh();setInterval(refresh,1000);
})();
