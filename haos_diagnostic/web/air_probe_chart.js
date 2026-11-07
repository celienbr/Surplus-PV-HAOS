"use strict";
(() => {
 const canvas=document.getElementById('air-probe-chart');
 const info=document.getElementById('air-probe-history-status');
 if(!canvas||!info)return;
 let points=[];
 function draw(){
  const w=canvas.clientWidth,h=180,dpr=Math.min(window.devicePixelRatio||1,2);
  if(!w)return;
  canvas.width=Math.round(w*dpr);canvas.height=Math.round(h*dpr);
  const ctx=canvas.getContext('2d');ctx.setTransform(dpr,0,0,dpr,0,0);ctx.clearRect(0,0,w,h);
  const valid=points.filter(p=>Number.isFinite(p.t)&&Number.isFinite(p.v));
  if(!valid.length){info.textContent='Aucune mesure disponible sur 48 h.';return;}
  let low=Infinity,high=-Infinity;for(const p of valid){low=Math.min(low,p.v);high=Math.max(high,p.v);}
  low=Math.floor(low-1);high=Math.ceil(high+1);const end=Date.now(),start=end-48*3600000;
  const x=t=>35+(t-start)/(end-start)*(w-45),y=v=>10+(high-v)/(high-low)*(h-35);
  ctx.font='11px system-ui';ctx.fillStyle=getComputedStyle(document.body).color;
  ctx.fillText(high+' °C',0,15);ctx.fillText(low+' °C',0,h-22);
  const time=t=>new Intl.DateTimeFormat('fr-CH',{timeZone:'Europe/Zurich',day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'}).format(new Date(t));
  ctx.fillText(time(start),35,h-3);ctx.textAlign='right';ctx.fillText(time(end),w-5,h-3);ctx.textAlign='left';
  ctx.strokeStyle='#008c8c';ctx.lineWidth=1.5;ctx.beginPath();let prev=null;
  for(const p of points){if(!Number.isFinite(p.t)||!Number.isFinite(p.v)){prev=null;continue;}
   const px=x(p.t),py=y(p.v);if(!prev||p.t-prev.t>60000)ctx.moveTo(px,py);else ctx.lineTo(px,py);prev=p;}
  ctx.stroke();ctx.fillStyle=ctx.strokeStyle;
  // A single valid reading next to a gap must remain visible.
  for(let i=0;i<points.length;i++){const p=points[i];if(!Number.isFinite(p.t)||!Number.isFinite(p.v))continue;
   const before=points[i-1],after=points[i+1];
   const connected=q=>q&&Number.isFinite(q.t)&&Number.isFinite(q.v)&&Math.abs(q.t-p.t)<=60000;
   if(!connected(before)&&!connected(after)){ctx.beginPath();ctx.arc(x(p.t),y(p.v),2,0,2*Math.PI);ctx.fill();}}
  info.textContent=valid.length+' rapports HA valides sur 48 h · lacunes non reliées · sonde hors boiler.';
 }
 canvas.addEventListener('pointerup',e=>{if(!points.length)return;const rect=canvas.getBoundingClientRect(),end=Date.now(),start=end-48*3600000;
  const at=start+Math.max(0,Math.min(1,(e.clientX-rect.left-35)/Math.max(1,rect.width-45)))*(end-start);
  let nearest=null;for(const p of points)if(Number.isFinite(p.t)&&(!nearest||Math.abs(p.t-at)<Math.abs(nearest.t-at)))nearest=p;
  if(nearest){const t=new Intl.DateTimeFormat('fr-CH',{timeZone:'Europe/Zurich',dateStyle:'short',timeStyle:'medium'}).format(new Date(nearest.t));
   info.textContent=t+' · '+(Number.isFinite(nearest.v)?nearest.v.toLocaleString('fr-CH',{maximumFractionDigits:3})+' °C':'mesure indisponible')+' · rapport HA, sonde hors boiler.';}});
 async function load(){try{const r=await fetch('./api/air-probe-history',{cache:'no-store'});if(!r.ok)throw Error('HTTP '+r.status);
  const data=await r.json();points=(data.points||[]).map(p=>({t:Date.parse(p.at),v:p.temperature_c}));draw();
 }catch(e){points=[];draw();info.textContent='Historique indisponible.';}}
 new ResizeObserver(draw).observe(canvas);load();setInterval(load,30000);
})();
