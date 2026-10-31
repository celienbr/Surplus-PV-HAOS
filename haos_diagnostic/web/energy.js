"use strict";
/* Consultation only. Same Swiss time, theme palette and gaps as chart.js. */
(function(){
 const $=id=>document.getElementById(id),good=n=>typeof n==='number'&&Number.isFinite(n);
 const fmt=n=>good(n)?n.toLocaleString('fr-CH',{maximumFractionDigits:3}):'Non disponible';
 const swissToday=()=>new Intl.DateTimeFormat('sv-SE',{timeZone:'Europe/Zurich',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date());
 const time=t=>window.SurplusTime.formatTime(t);
 const canvas=$('energy-chart'),viewport=$('energy-viewport');if(!canvas)return;
 let data=null,series=[],selected=null,geometry=null,request=null,revision=0;
 const hidden=new Set();
 function resetDate(){const day=swissToday(),period=$('energy-period').value,input=$('energy-date');
  input.type=period==='year'?'number':period==='month'?'month':'date';
  input.min=period==='year'?'2000':period==='month'?'2000-01':'2000-01-01';input.max=period==='year'?'9998':period==='month'?'9998-12':'9998-12-31';
  input.value=period==='year'?day.slice(0,4):period==='month'?day.slice(0,7):day;
 }
 function move(direction){const period=$('energy-period').value,input=$('energy-date'),v=input.value;
  if(!input.checkValidity()||!v)return;
  let next;
  if(period==='year')next=String(Number(v)+direction);
  else {const d=new Date(v+(period==='month'?'-01':'')+'T12:00:00Z');
   if(period==='day')d.setUTCDate(d.getUTCDate()+direction);else d.setUTCMonth(d.getUTCMonth()+direction);
   next=d.toISOString().slice(0,period==='day'?10:7);}
  input.value=next;if(!input.checkValidity()){input.value=v;return;}load();
 }
 async function load(){
  const date=$('energy-date');if(!date.value||!date.checkValidity())return;
  request?.abort();request=new AbortController();const current=++revision;
  try{
   const response=await fetch('./api/energy?'+new URLSearchParams({period:$('energy-period').value,date:date.value}),{cache:'no-store',signal:request.signal});
   if(!response.ok)throw Error('Historique non disponible (HTTP '+response.status+')');
   const result=await response.json();if(current!==revision)return;
   data=result;series=window.SurplusEnergySeries(data);selected=null;
   const live=data.live,watts=live.grid_w;
   $('energy-live').textContent=good(watts)?fmt(Math.abs(watts)/1000)+' kW · '+(watts>0?'Importation':watts<0?'Exportation':'Équilibre'):'Non disponible';
   $('energy-freshness').textContent=(good(live.age_seconds)?'Âge rapport HA : '+Math.round(live.age_seconds)+' s. ':'')+live.freshness_basis;
   $('energy-status').textContent=live.status==='OK'?'Mesures réseau disponibles · '+(live.quality||''):'Mesures réseau indisponibles ou périmées · '+(live.quality||'');
   for(const key of ['import','export'])$('energy-'+key).textContent=fmt(data.totals[key+'_kwh'])+(good(data.totals[key+'_kwh'])?' kWh · estimés':'');
   const pv=data.pv_actual,own=data.self_consumption,ph=data.pv_history;
   $('energy-pv').textContent=pv.available?'Production PV réelle AC : '+fmt(pv.power.value/1000)+' kW · '+pv.power.entity_id+' · âge HA '+fmt(pv.power.age_seconds)+' s':pv.reason;
   $('energy-self').textContent=own.available?'Autoconsommation PV : '+fmt(own.power.value/1000)+' kW · calcul sur mesures synchronisées':own.reason;
   const daily=pv.energy_today,total=pv.energy_total;
   $('energy-pv-counters').textContent='Production du jour (index Sofar) : '+fmt(daily?.value)+(good(daily?.value)?' kWh':'')+' · Index total Sofar : '+fmt(total?.value)+(good(total?.value)?' kWh':'');
   $('energy-house').textContent='Consommation maison calculée : '+(own.available&&good(own.power.house_w)?fmt(own.power.house_w/1000)+' kW':'Non disponible');
   $('energy-pv-history').textContent='Production PV sur la période couverte : '+fmt(ph?.energy_kwh)+(good(ph?.energy_kwh)?' kWh':'')+' · couverture '+fmt((ph?.covered_seconds||0)/3600)+' h / '+fmt(data.period_seconds/3600)+' h'+(ph?.partial?' · historique partiel':'')+' · méthodes : '+(ph?.methods?.map(m=>({total:'index total Sofar',today:'index du jour Sofar',power:'intégration AC estimée'})[m]||m).join(', ')||'aucune')+' · anomalies : '+(ph?.anomaly_count||0)+'. Autoconsommation estimée sur la période couverte : '+fmt(ph?.self_energy_kwh)+(good(ph?.self_energy_kwh)?' kWh':'')+' · couverture '+fmt((ph?.self_covered_seconds||0)/3600)+' h.'+(data.pv_history_error||pv.storage_error?' Historique PV temporairement indisponible.':'');
   $('energy-window').textContent=time(Date.parse(data.start))+' → '+time(Date.parse(data.end))+' · '+(data.period==='day'?'Puissance (kW)':'Énergie (kWh)');
   $('energy-coverage').textContent=(data.partial?'Couverture historique partielle':'Période entièrement couverte')+' · '+fmt(data.covered_seconds/3600)+' h / '+fmt(data.period_seconds/3600)+' h ; '+(good(data.coverage_ratio)?(100*data.coverage_ratio).toFixed(1)+' % du temps écoulé':'aucune couverture')+'. Anomalies : '+data.anomaly_count+'.'+(data.buckets.some(b=>b.one_recent_seconds>0)?' Comprend des intervalles avec un seul canal HA récent, canal opposé nul inchangé.':'');
   $('energy-method').textContent='Réseau : '+data.method+'. Lacune maximale intégrée : '+data.max_gap_seconds+' s. Bilan calculé à '+time(Date.parse(data.generated_at))+' (cache 60 s).';
   legend();draw();table();
  }catch(error){if(error.name==='AbortError')return;if(current!==revision)return;data=null;series=[];selected=null;
   for(const key of ['live','import','export'])$('energy-'+key).textContent='Non disponible';
   $('energy-status').textContent=error.message;$('energy-coverage').textContent='Historique non disponible';$('energy-window').textContent='';$('energy-method').textContent='';$('energy-freshness').textContent='';for(const id of ['energy-pv','energy-self','energy-pv-counters','energy-pv-history','energy-house'])$(id).textContent='Non disponible';legend();draw();table();}finally{if(current===revision)request=null;}
 }
 function legend(){const box=$('energy-legend');box.replaceChildren();for(const s of series){const button=document.createElement('button');button.type='button';button.setAttribute('aria-pressed',String(!hidden.has(s.id)));const icon=document.createElement('i');icon.style.setProperty('--series-color',window.SurplusTheme.colors()[s.color]);button.append(icon,document.createTextNode(s.label+' · '+s.unit+' · '+s.source));button.onclick=()=>{hidden.has(s.id)?hidden.delete(s.id):hidden.add(s.id);button.setAttribute('aria-pressed',String(!hidden.has(s.id)));draw();};box.append(button);}}
 function draw(){const width=Math.max(300,viewport.clientWidth),height=320,dpr=Math.min(devicePixelRatio||1,2);canvas.width=width*dpr;canvas.height=height*dpr;canvas.style.width=width+'px';canvas.style.height=height+'px';const ctx=canvas.getContext('2d');ctx.setTransform(dpr,0,0,dpr,0,0);const p=window.SurplusTheme.colors();ctx.fillStyle=p.background;ctx.fillRect(0,0,width,height);
  const visible=series.filter(s=>!hidden.has(s.id)),values=visible.flatMap(s=>s.points.map(p=>p.value).filter(good));
  let low=Math.min(0,...values),high=Math.max(1,...values);const margin=(high-low)*.08;low-=margin;high+=margin;
  const points=[...new Set(series.flatMap(s=>s.points.map(p=>p.at)))].sort((a,b)=>a-b).map(at=>({at})),start=Date.parse(data?.start)||0,end=Date.parse(data?.end)||1;
  const x=at=>52+(at-start)/(end-start)*(width-68),y=v=>25+(high-v)/(high-low)*245;
  geometry={x,start,end,width,points};ctx.font='11px system-ui';ctx.fillStyle=p.ticks;ctx.fillText(series[0]?.unit||'',4,14);
  for(let i=0;i<=4;i++){const v=low+(high-low)*i/4,Y=y(v);ctx.strokeStyle=p.grid;ctx.beginPath();ctx.moveTo(48,Y);ctx.lineTo(width-8,Y);ctx.stroke();ctx.fillStyle=p.ticks;ctx.fillText(v.toLocaleString('fr-CH',{maximumFractionDigits:1}),2,Y+4);}
  ctx.strokeStyle=p.ticks;ctx.beginPath();ctx.moveTo(48,y(0));ctx.lineTo(width-8,y(0));ctx.stroke();
  for(const s of visible){ctx.strokeStyle=p[s.color];ctx.fillStyle=p[s.color];ctx.lineWidth=2;ctx.beginPath();let previous=null;
   for(const pt of s.points){if(!good(pt.value)){previous=null;continue;}const X=x(pt.at),Y=y(pt.value);if(!previous||pt.gap)ctx.moveTo(X,Y);else ctx.lineTo(X,Y);ctx.fillRect(X-2,Y-2,4,4);previous=pt;}ctx.stroke();}
  ctx.fillStyle=p.ticks;ctx.textAlign='center';const ticks=Math.min(4,points.length);
  for(let i=0;i<ticks;i++){const pt=points[Math.floor(i*(points.length-1)/Math.max(1,ticks-1))];const label=new Intl.DateTimeFormat('fr-CH',{timeZone:'Europe/Zurich',...(data.period==='day'?{hour:'2-digit',minute:'2-digit',timeZoneName:'shortOffset'}:data.period==='month'?{day:'2-digit',month:'2-digit'}:{month:'short'})}).format(new Date(pt.at));ctx.fillText(label,Math.max(65,Math.min(width-48,x(pt.at))),300);}
  ctx.textAlign='left';if(!values.length){ctx.fillStyle=p.ticks;ctx.fillText('Aucune mesure disponible pour cette période',58,50);}
  if(selected!==null&&points[selected]){ctx.strokeStyle=p.ticks;ctx.setLineDash([4,3]);ctx.beginPath();ctx.moveTo(x(points[selected].at),20);ctx.lineTo(x(points[selected].at),275);ctx.stroke();ctx.setLineDash([]);}tooltip();
 }
 function tooltip(){const box=$('energy-tooltip');if(selected===null){box.textContent='Touchez ou cliquez sur le graphique. Au clavier : flèches gauche / droite. Les lacunes restent sans valeur.';return;}const at=geometry?.points[selected]?.at;if(!good(at))return;const title=document.createElement('strong');title.textContent=time(at);const list=document.createElement('ul');for(const s of series){const nearest=s.points.reduce((best,p)=>!best||Math.abs(p.at-at)<Math.abs(best.at-at)?p:best,null),pt=nearest&&Math.abs(nearest.at-at)<=15000?nearest:null,li=document.createElement('li');li.textContent=s.label+' : '+fmt(pt?.value)+(good(pt?.value)?' '+s.unit:'')+(pt?.partial?' · couverture partielle':'')+' · '+s.source+(pt?.at!==undefined&&pt.at!==at?' · mesure à '+time(pt.at):'')+(pt?.entity?' · '+pt.entity:'');list.append(li);}box.replaceChildren(title,list);}
 function table(){if(!$('energy-table-details').open)return;const fragment=document.createDocumentFragment();for(const s of series)for(const p of s.points){const tr=document.createElement('tr');for(const value of [time(p.at),s.label,fmt(p.value)+(good(p.value)?' '+s.unit:''),s.source+(p.entity?' · '+p.entity:'')+(p.method?' · '+p.method:'')+(p.partial?' · couverture partielle':'')]){const td=document.createElement('td');td.textContent=value;tr.append(td);}fragment.append(tr);}$('energy-table').tBodies[0].replaceChildren(fragment);}
 let tap=null;canvas.addEventListener('pointerdown',e=>{tap={x:e.clientX,y:e.clientY};});canvas.addEventListener('pointerup',e=>{if(!tap||Math.hypot(e.clientX-tap.x,e.clientY-tap.y)>8||!geometry?.points.length)return;tap=null;const at=geometry.start+(e.clientX-canvas.getBoundingClientRect().left-52)/(geometry.width-68)*(geometry.end-geometry.start);selected=geometry.points.reduce((best,p,i)=>Math.abs(p.at-at)<Math.abs(geometry.points[best].at-at)?i:best,0);draw();});
 viewport.addEventListener('keydown',e=>{if(!['ArrowLeft','ArrowRight'].includes(e.key)||!geometry?.points.length)return;e.preventDefault();selected=Math.max(0,Math.min(geometry.points.length-1,(selected??-1)+(e.key==='ArrowRight'?1:-1)));draw();});
 $('energy-table-details').addEventListener('toggle',table);$('energy-period').onchange=()=>{resetDate();load();};$('energy-date').onchange=load;$('energy-prev').onclick=()=>move(-1);$('energy-next').onclick=()=>move(1);$('energy-today').onclick=()=>{resetDate();load();};
 new ResizeObserver(draw).observe(viewport);addEventListener('surplus-theme-change',()=>{legend();draw();});
 resetDate();load();setInterval(()=>{if(!request)load();},10000);
})();
