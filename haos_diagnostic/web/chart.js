"use strict";
/* One timeline. Timestamps are instants; the display timezone is always Swiss.
   Null is a gap, never zero. This module cannot change regulator state. */
(function(){
 const HOUR=3600000, good=n=>typeof n==='number'&&Number.isFinite(n);
 const stamp=p=>Date.parse(p.at);
 const definitions=[
  {id:'grid',key:'grid_w',label:'Réseau gPlugK',kind:'mesure réelle',unit:'kW',factor:.001,axis:'power',color:'network',dash:[],source:'history'},
  {id:'virtual',key:'simulated_boiler_w',label:'Chauffe virtuelle',kind:'simulation enregistrée',unit:'kW',factor:.001,axis:'power',color:'heater',dash:[2,3],source:'history'},
  {id:'pv',key:'pv_w',label:'Production PV',kind:'prévision',unit:'kW',factor:.001,axis:'power',color:'solar',dash:[8,4],source:'forecast'},
  {id:'temperature',key:'temperature_c',label:'Température extérieure',kind:'prévision',unit:'°C',factor:1,axis:'temp',color:'temperature',dash:[7,4],source:'forecast'},
  {id:'pac',key:'pac_w',label:'Consommation PAC',kind:'estimation',unit:'kW',factor:.001,axis:'power',color:'pac',dash:[3,4],source:'forecast'},
  {id:'surplus',key:'surplus_w',label:'Surplus disponible',kind:'estimation',unit:'kW',factor:.001,axis:'power',color:'surplus',dash:[3,4],source:'forecast'},
  {id:'budget',key:'heater_budget_w',label:'Budget de chauffe',kind:'simulation prévisionnelle',unit:'kW',factor:.001,axis:'power',color:'heater',dash:[10,3,2,3],source:'forecast'},
  {id:'planned',key:'planned_boiler_w',label:'Chauffe planifiée',kind:'simulation thermique',unit:'kW',factor:.001,axis:'power',color:'heater',dash:[6,2],source:'forecast'},
  {id:'backup',key:'planned_grid_backup_w',label:'Appoint réseau planifié',kind:'simulation autorisée',unit:'kW',factor:.001,axis:'power',color:'network',dash:[2,5],source:'forecast'},
  {id:'water',key:'projected_water_c',label:'Température eau projetée',kind:'simulation thermique',unit:'°C',factor:1,axis:'target',color:'surplus',dash:[2,3],source:'forecast'},
  {id:'target',key:'target_c',label:'Consigne boiler',kind:'consigne calculée en simulation',unit:'°C',factor:1,axis:'target',color:'target',dash:[5,3],source:'forecast'}
 ];
 function series(history,profile,now=Date.now()){
  return definitions.map(d=>({...d,points:(d.source==='history'?history:profile?.points||[])
   .filter(p=>Number.isFinite(stamp(p))&&(d.source!=='history'||stamp(p)<=now))
   .map(p=>({at:stamp(p),value:good(p[d.key])?p[d.key]*d.factor:null,gap:p.gap_before===true}))
   .sort((a,b)=>a.at-b.at)}));
 }
 function swissDayEnd(value){
  if(!/^\d{4}-\d{2}-\d{2}$/.test(value))throw Error('Date invalide');
  const [y,m,d]=value.split('-').map(Number),base=new Date(Date.UTC(y,m-1,d));
  if(base.toISOString().slice(0,10)!==value)throw Error('Date invalide');
  const utc=Date.UTC(y,m-1,d+1),fmt=new Intl.DateTimeFormat('en-GB',{timeZone:'Europe/Zurich',timeZoneName:'shortOffset'});
  let instant=utc;
  for(let i=0;i<3;i++){const zone=fmt.formatToParts(new Date(instant)).find(x=>x.type==='timeZoneName').value;
   const match=zone.match(/GMT([+-])(\d+)(?::(\d+))?/);const offset=match?(match[1]==='+'?1:-1)*(Number(match[2])*60+Number(match[3]||0))*60000:0;instant=utc-offset;}
  return instant;
 }
 function energySeries(data){
  const day=data.period==='day',unit=day?'kW':'kWh';
  const specs=day?[
   ['grid','grid_w','Réseau (+ import / − export)','network'],
   ['pv_actual','pv_actual_w','Production PV réelle','solar'],
   ['self_consumed','self_consumed_w','Autoconsommation PV','surplus']
  ]:[
   ['import','import_kwh','Import réseau','network'],
   ['export','export_kwh','Export réseau (négatif)','heater'],
   ['pv_actual','pv_actual_kwh','Production PV réelle','solar'],
   ['self_consumed','self_consumed_kwh','Autoconsommation PV','surplus']
  ];
  return specs.map(([id,key,label,color])=>{
   const rows=day?(id==='pv_actual'?data.pv_history?.power_points||data.points:
     id==='self_consumed'?data.pv_history?.self_consumed_points||data.points:data.points):data.buckets;
   const source=id==='pv_actual'?(day?'Sofar via HA · puissance AC mesurée':'Sofar via HA · production et méthode indiquées'):
     id==='self_consumed'?'Calcul PV AC − export · sous conditions validées':data.source+(day?' · puissance mesurée':' · énergie estimée');
   return {id,label,color,unit,source,
    points:rows.map(p=>({at:stamp(p),value:good(p[key])?p[key]*(day?.001:id==='export'?-1:1):null,
      gap:p.gap_before===true,partial:id==='pv_actual'?p.pv_partial===true:id==='self_consumed'?p.self_partial===true:p.partial===true,
      entity:p.entity_id||null,method:id==='pv_actual'?p.pv_method||null:null})).filter(p=>Number.isFinite(p.at))};
  });
 }
 const formatTime=t=>new Intl.DateTimeFormat('fr-CH',{timeZone:'Europe/Zurich',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',timeZoneName:'shortOffset'}).format(new Date(t));
 if(typeof module!=='undefined')module.exports={series,swissDayEnd,formatTime,energySeries};
 if(typeof document==='undefined')return;
 window.SurplusTime={swissDayEnd,formatTime};
 window.SurplusEnergySeries=energySeries;
 const $=id=>document.getElementById(id),canvas=$('chart-power'),viewport=$('viewport-power');if(!canvas)return;
 let status=null,history=[],liveProfile={},archived=null,savedAt=null,anchor=Date.now(),isLive=true,zoom=1,selected=null,range='all';
 const visible=new Set(definitions.map(s=>s.id));let drawn=[],start=0,end=0,width=0,selectionTimes=[],plotHeight=320;
 let legendSignature='',renderQueued=false,pendingCenter=true;
 function schedule(){if(renderQueued)return;renderQueued=true;requestAnimationFrame(()=>{renderQueued=false;draw();});}
 function context(can,w,h){const dpr=Math.min(devicePixelRatio||1,2);can.width=Math.round(w*dpr);can.height=Math.round(h*dpr);can.style.width=w+'px';can.style.height=h+'px';const ctx=can.getContext('2d');ctx.setTransform(dpr,0,0,dpr,0,0);return ctx;}
 function palette(){return window.SurplusTheme.colors();}
 function extent(values,fallback,axis){if(!values.length)return fallback;let lo=Math.min(...values),hi=Math.max(...values);if(axis==='power'){lo=Math.min(0,lo);hi=Math.max(1,hi);lo=Math.floor(lo);hi=Math.ceil(hi);}else{lo=Math.floor((lo-2)/5)*5;hi=Math.ceil((hi+2)/5)*5;}return hi===lo?[lo,lo+1]:[lo,hi];}
 function availableSeries(){const all=series(history,archived||liveProfile);return all.filter(s=>!['target','planned','backup','water'].includes(s.id)||s.points.some(p=>good(p.value)));}
 function draw(){
  const now=Date.now();if(isLive)anchor=now;
  start=range==='future'?anchor:anchor-48*HOUR;end=range==='past'?anchor:anchor+48*HOUR;
  const all=availableSeries();drawn=all.filter(s=>visible.has(s.id)).map(s=>({...s,points:s.points.filter(p=>p.at>=start&&p.at<=end)}));
  $('axis-target').hidden=!drawn.some(s=>s.axis==='target');
  const base=Math.max(viewport.clientWidth,Math.min(1100,(end-start)/HOUR*14));width=Math.min(8000,Math.max(viewport.clientWidth,base*zoom));
  const ctx=context(canvas,width,plotHeight),p=palette(),top=32,bottom=48,usable=plotHeight-top-bottom;
  const ext={};for(const axis of ['power','temp','target']){ext[axis]=extent(drawn.filter(s=>s.axis===axis).flatMap(s=>s.points.map(p=>p.value).filter(good)),axis==='power'?[-1,6]:axis==='temp'?[-5,25]:[20,80],axis);}
  const x=at=>12+(at-start)/(end-start)*(width-24),y=(value,axis)=>top+(ext[axis][1]-value)/(ext[axis][1]-ext[axis][0])*usable;
  ctx.fillStyle=p.background;ctx.fillRect(0,0,width,plotHeight);
  for(const axis of ['power','temp','target']){
   if(axis==='target'&&$('axis-target').hidden)continue;
   const ax=context($('axis-'+axis),44,plotHeight);ax.fillStyle=p.background;ax.fillRect(0,0,44,plotHeight);ax.fillStyle=p.ticks;ax.font='bold 10px system-ui';ax.fillText(axis==='power'?'kW':axis==='temp'?'Ext. °C':'Eau °C',3,14);
   for(let i=0;i<=4;i++){const value=ext[axis][0]+i*(ext[axis][1]-ext[axis][0])/4,Y=y(value,axis);ax.fillText(value.toLocaleString('fr-CH',{maximumFractionDigits:1}),3,Y+4);
    if(axis==='power'){ctx.strokeStyle=p.grid;ctx.lineWidth=1;ctx.setLineDash([]);ctx.beginPath();ctx.moveTo(0,Y);ctx.lineTo(width,Y);ctx.stroke();}}
   if(axis==='power'){ax.fillText('0',3,y(0,axis)+4);ctx.strokeStyle=p.ticks;ctx.lineWidth=1.6;ctx.beginPath();ctx.moveTo(0,y(0,axis));ctx.lineTo(width,y(0,axis));ctx.stroke();}
  }
  const tickHours=Math.max(1,Math.ceil((end-start)/HOUR/(width/95)));
  for(let time=Math.ceil(start/(tickHours*HOUR))*tickHours*HOUR;time<=end;time+=tickHours*HOUR){const X=x(time);ctx.strokeStyle=p.grid;ctx.lineWidth=1;ctx.beginPath();ctx.moveTo(X,top);ctx.lineTo(X,plotHeight-bottom);ctx.stroke();ctx.fillStyle=p.ticks;ctx.font='10px system-ui';ctx.textAlign='center';const d=new Date(time);ctx.fillText(d.toLocaleString('fr-CH',{timeZone:'Europe/Zurich',day:'2-digit',month:'2-digit'}),X,plotHeight-26);ctx.fillText(d.toLocaleString('fr-CH',{timeZone:'Europe/Zurich',hour:'2-digit',minute:'2-digit',timeZoneName:'shortOffset'}),X,plotHeight-11);}
  ctx.textAlign='left';
  for(const s of drawn){ctx.strokeStyle=p[s.color];ctx.fillStyle=p[s.color];ctx.lineWidth=s.id==='grid'?2:2.4;ctx.setLineDash(s.dash);ctx.beginPath();let previous=null;
   for(const point of s.points){if(!good(point.value)){previous=null;continue;}const X=x(point.at),Y=y(point.value,s.axis);
    if(!previous||point.gap||(s.source==='forecast'&&point.at-previous.at>1.6*HOUR)){ctx.moveTo(X,Y);if(s.source==='history')ctx.fillRect(X-1.3,Y-1.3,2.6,2.6);}else{ctx.lineTo(X,Y);}previous=point;}
   ctx.stroke();
   if(s.source==='forecast'){ctx.setLineDash([]);for(const point of s.points)if(good(point.value)){ctx.beginPath();ctx.arc(x(point.at),y(point.value,s.axis),2,0,Math.PI*2);ctx.fill();}}
  }
  function marker(at,label,color){if(at<start||at>end)return;const X=x(at);ctx.setLineDash([5,4]);ctx.strokeStyle=color;ctx.lineWidth=1.5;ctx.beginPath();ctx.moveTo(X,top-9);ctx.lineTo(X,plotHeight-bottom);ctx.stroke();ctx.setLineDash([]);ctx.fillStyle=p.background;const labelWidth=ctx.measureText(label).width+10;const left=Math.max(0,Math.min(width-labelWidth,X+5));ctx.fillRect(left,3,labelWidth,20);ctx.fillStyle=color;ctx.font='bold 11px system-ui';ctx.fillText(label,left+3,18);}
  marker(now,'Maintenant',p.ticks);if(!isLive)marker(anchor,'Repère historique',p.temperature);
  if(selected!==null)marker(selected,'Sélection',p.heater);
  if(!drawn.some(s=>s.points.some(p=>good(p.value)))){ctx.fillStyle=p.ticks;ctx.font='13px system-ui';ctx.fillText('Aucune donnée disponible dans cette vue.',20,65);}
  selectionTimes=[...new Set(drawn.flatMap(s=>s.points.filter(p=>good(p.value)).map(p=>p.at)))].sort((a,b)=>a-b);
  $('history-scale').textContent=Math.round(zoom*100)+' %';$('history-window').textContent=formatTime(start)+' → '+formatTime(end);
  const prof=archived||liveProfile;
  $('source-note').textContent=(isLive?'Prévision actuelle':'Prévision archivée')+' · PV horaire : '+(prof.solar_status||'—')+' · météo : '+(prof.meteo_status||'—')+' · PAC : '+(prof.pac_status||'—')+'. '+(prof.solar_error||'')+' '+(prof.meteo_error||'');
  renderLegend(all);renderTooltip();renderTable();
  if(pendingCenter){viewport.scrollLeft=range==='all'?Math.max(0,x(anchor)-viewport.clientWidth/2):0;pendingCenter=false;}
 }
 function renderLegend(all){const signature=all.map(s=>s.id).join();if(signature===legendSignature)return;legendSignature=signature;$('chart-legend').replaceChildren();for(const s of all){const button=document.createElement('button');button.type='button';button.dataset.series=s.id;button.setAttribute('aria-pressed',String(visible.has(s.id)));const sample=document.createElement('i');sample.style.setProperty('--series-color','var(--'+(s.color==='temperature'?'temp':s.color==='network'?'network':s.color)+'-chart)');sample.style.borderTopStyle=s.dash.length?'dashed':'solid';button.append(sample,document.createTextNode(s.label+' · '+s.kind+' ('+s.unit+')'));button.onclick=()=>{visible.has(s.id)?visible.delete(s.id):visible.add(s.id);button.setAttribute('aria-pressed',String(visible.has(s.id)));selected=null;schedule();};$('chart-legend').append(button);}}
 function renderTooltip(){const box=$('chart-tooltip');if(selected===null){box.textContent='Touchez une courbe pour consulter ses valeurs exactes. Au clavier : flèches gauche / droite dans le graphique.';return;}const title=document.createElement('strong');title.textContent=formatTime(selected);const list=document.createElement('ul');for(const s of drawn){const pt=s.points.find(p=>p.at===selected);if(!pt||!good(pt.value))continue;const li=document.createElement('li');li.textContent=s.label+' : '+pt.value.toLocaleString('fr-CH',{maximumFractionDigits:3})+' '+s.unit+' · '+s.kind+(s.source==='forecast'&&savedAt?' archivée, publiée '+formatTime(Date.parse(savedAt)):'');list.append(li);}box.replaceChildren(title,list);}
 function renderTable(){const table=$('chart-table');if(!table.closest('details').open)return;const frag=document.createDocumentFragment();const rows=drawn.flatMap(s=>s.points.map(p=>({s,p}))).sort((a,b)=>a.p.at-b.p.at);for(const {s,p} of rows){const tr=document.createElement('tr');for(const txt of [formatTime(p.at),s.label+' · '+s.kind,good(p.value)?p.value.toLocaleString('fr-CH',{maximumFractionDigits:3})+' '+s.unit:'Indisponible']){const td=document.createElement('td');td.textContent=txt;tr.append(td);}frag.append(tr);}table.tBodies[0].replaceChildren(frag);}
 $('chart-table').closest('details').addEventListener('toggle',renderTable);
 function setZoom(next,screenX=viewport.clientWidth/2){const fraction=(viewport.scrollLeft+screenX)/Math.max(1,width);zoom=Math.max(.5,Math.min(6,next));draw();viewport.scrollLeft=fraction*width-screenX;}
 $('history-in').onclick=()=>setZoom(zoom*1.4);$('history-out').onclick=()=>setZoom(zoom/1.4);$('history-reset').onclick=()=>{zoom=1;selected=null;draw();viewport.scrollLeft=isLive&&range==='all'?Math.max(0,width/2-viewport.clientWidth/2):0;};
 $('chart-range').onchange=e=>{range=e.target.value;selected=null;zoom=1;draw();viewport.scrollLeft=0;};
 let gesture=null,tap=null;
 const distance=t=>Math.hypot(t[0].clientX-t[1].clientX,t[0].clientY-t[1].clientY);
 viewport.addEventListener('touchstart',e=>{if(e.touches.length===2){const center=(e.touches[0].clientX+e.touches[1].clientX)/2-viewport.getBoundingClientRect().left;gesture={distance:distance(e.touches),zoom,center,fraction:(viewport.scrollLeft+center)/width};tap=null;}else{gesture=null;}},{passive:true});
 viewport.addEventListener('touchmove',e=>{if(!gesture||e.touches.length!==2)return;e.preventDefault();zoom=Math.max(.5,Math.min(6,gesture.zoom*distance(e.touches)/Math.max(10,gesture.distance)));draw();viewport.scrollLeft=gesture.fraction*width-gesture.center;},{passive:false});
 viewport.addEventListener('touchend',e=>{if(e.touches.length<2)gesture=null;},{passive:true});
 canvas.addEventListener('pointerdown',e=>{tap={x:e.clientX,y:e.clientY,scroll:viewport.scrollLeft};});
 canvas.addEventListener('pointerup',e=>{if(!tap||gesture||Math.hypot(e.clientX-tap.x,e.clientY-tap.y)>8||Math.abs(tap.scroll-viewport.scrollLeft)>8)return;tap=null;const x=e.clientX-canvas.getBoundingClientRect().left;const at=start+(x-12)/(width-24)*(end-start);if(selectionTimes.length){selected=selectionTimes.reduce((a,b)=>Math.abs(a-at)<Math.abs(b-at)?a:b);schedule();}});
 viewport.addEventListener('keydown',e=>{if(!['ArrowLeft','ArrowRight'].includes(e.key)||!selectionTimes.length)return;e.preventDefault();const i=selected===null?-1:selectionTimes.indexOf(selected);selected=selectionTimes[Math.max(0,Math.min(selectionTimes.length-1,i+(e.key==='ArrowRight'?1:-1)))];schedule();const x=12+(selected-start)/(end-start)*(width-24);if(x<viewport.scrollLeft||x>viewport.scrollLeft+viewport.clientWidth)viewport.scrollLeft=x-viewport.clientWidth/2;});
 window.SurplusChart={
  setStatus(p){status=p;liveProfile=p.profile||{};schedule();},
  setHistory(rows,at,live,archive){if(isLive!==live||(!live&&anchor!==at))pendingCenter=true;history=rows||[];anchor=at;isLive=live;archived=live?null:archive?.profile||{points:[]};savedAt=live?null:archive?.saved_at||null;selected=null;schedule();},
  // Narrow observability for UI tests; data are already visible in the same UI.
  inspect(){return {start,end,zoom,visible:[...visible],series:drawn,anchor,isLive};}
 };
 new ResizeObserver(schedule).observe(viewport);addEventListener('surplus-theme-change',schedule);schedule();
})();
