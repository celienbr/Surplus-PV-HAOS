"use strict";
/* Persistent archive navigation and original CSV export. No chart or regulator writes. */
(function(){
 const $=id=>document.getElementById(id);if(!$('chart-power'))return;
 const HOUR=3600000,DAY=24*HOUR;let rangeEnd=new Date(),live=true,archiveDates=[],request=0;
 const label=(id,value)=>{if($(id))$(id).textContent=value};
 const displayDate=d=>d.toLocaleString('fr-CH',{timeZone:'Europe/Zurich',dateStyle:'short',timeStyle:'short'});
 async function get(url){const r=await fetch(url,{cache:'no-store',signal:AbortSignal.timeout(15000)});if(!r.ok)throw Error('HTTP '+r.status);return r.json();}
 async function load(){
  const ticket=++request;if(live)rangeEnd=new Date();
  const end=new Date(rangeEnd),begin=new Date(end.getTime()-48*HOUR);
  label('history-status','Chargement des mesures enregistrées…');
  const [historyResult,forecastResult]=await Promise.allSettled([
   get('./api/history?start='+encodeURIComponent(begin.toISOString())+'&end='+encodeURIComponent(end.toISOString())),
   live?Promise.resolve(null):get('./api/forecast-at?at='+encodeURIComponent(end.toISOString()))]);
  if(ticket!==request)return;
  const hist=historyResult.status==='fulfilled'?historyResult.value:{points:[]};
  const forecast=forecastResult.status==='fulfilled'?forecastResult.value:null;
  window.SurplusChart.setHistory(hist.points,end.getTime(),live,forecast);
  label('history-status',historyResult.status==='fulfilled'?'Mesures originales conservées ; graphique réduit avec extrema et lacunes.':'Historique indisponible · '+String(historyResult.reason));
  label('history-count',(hist.points?.length||0)+' points historiques affichés · + achat / − injection');
  label('forecast-snapshot',live?'Prévision actuelle · données estimées, aucune puissance réseau future inventée.':forecast?.saved_at?'Prévision archivée publiée le '+displayDate(new Date(forecast.saved_at))+' · valeurs prévues à cette époque, pas des mesures.':forecastResult.status==='rejected'?'Archive de prévision indisponible.':'Aucune prévision archivée à cette date.');
  $('history-next').disabled=live;
 }
 function navigate(days){rangeEnd=new Date(Math.min(Date.now(),rangeEnd.getTime()+days*DAY));live=rangeEnd.getTime()>=Date.now()-1000;load();}
 $('history-prev').onclick=()=>navigate(-1);$('history-next').onclick=()=>navigate(1);
 function now(){live=true;rangeEnd=new Date();$('history-day').value='';load();}
 $('history-now').onclick=now;$('forecast-view-live').onclick=now;
 // Swiss calendar day end, not browser local time or noon UTC; DST tested.
 $('history-day').addEventListener('change',e=>{if(!e.target.value)return;rangeEnd=new Date(Math.min(Date.now(),window.SurplusTime.swissDayEnd(e.target.value)));live=rangeEnd.getTime()>=Date.now()-1000;load();});
 async function archives(){
  try{
   const r=await fetch("./api/archives",{cache:"no-store"});
   if(!r.ok)throw Error("HTTP "+r.status);
   const data=await r.json();
   archiveDates=(data.days||[]).map(d=>d.date);
   const select=$("archive-day");
   for(const day of archiveDates){
    const opt=document.createElement("option");opt.value=day;opt.textContent=day;
    select.appendChild(opt);
   }
   label("archive-status",archiveDates.length?
     archiveDates.length+" jour(s) archivés dans /data · conservation longue durée.":
     "Aucune archive pour le moment : l'archivage commence après 48 h.");
  }catch(error){label("archive-status",String(error));}
 }
 $("archive-open").addEventListener("click",()=>{
  const date=$("archive-day").value;if(!date)return;
  $("history-day").value=date;
  $("history-day").dispatchEvent(new Event("change"));
 });
 $("archive-download").addEventListener("click",()=>{
  const day=$("archive-day").value;if(!day)return;
  downloadCsv(day,day,"network");
 });
 $("history-logs").addEventListener("click",async()=>{
  const info=$("logs-view");
  if(!info.hidden){info.hidden=true;return;}
  try{
   const r=await fetch("./api/logs?limit=300",{cache:"no-store"});
   if(!r.ok)throw Error("HTTP "+r.status);
   const payload=await r.json();
   info.textContent=(payload.logs||[]).map(l=>
    l.at+" | "+l.level+" | "+l.event+" | "+l.message).join("\n")||
    "Aucun événement enregistré.";
   info.hidden=false;
  }catch(e){info.textContent=String(e);info.hidden=false;}
 });
 // Export exact raw measurements, not the min/max-decimated chart points.
 function swissDay(date){
  const parts=new Intl.DateTimeFormat("en-GB",{
   year:"numeric",month:"2-digit",day:"2-digit",
   timeZone:"Europe/Zurich"}).formatToParts(date);
  const val=type=>parts.find(x=>x.type===type)?.value||"";
  return val("year")+"-"+val("month")+"-"+val("day");
 }
 const downloadCsv=(start,end,forceDataset=null)=>{
  const fmt=$("csv-format").value;
  const dataset=forceDataset||$("csv-dataset").value;
  if(!["excel","standard"].includes(fmt)||
     !["network","forecasts","logs"].includes(dataset))return;
  const url="./api/history.csv?start="+encodeURIComponent(start)+
    "&end="+encodeURIComponent(end)+"&format="+encodeURIComponent(fmt)+
    "&type="+encodeURIComponent(dataset);
  label("csv-export-info","Téléchargement en cours — fichier CSV "+fmt+
    " contenant les "+(dataset==="network"?"mesures réseau":
      dataset==="forecasts"?"prévisions archivées":"logs techniques")+
    " sans réduction de points.");
  // Browser handles attachment; request remains restricted to HA ingress.
  location.href=url;
 };
 $("csv-from").value=swissDay(new Date(Date.now()-DAY));
 $("csv-to").value=swissDay(new Date());
 $("csv-48h").addEventListener("click",()=>{
  const now=new Date();
  downloadCsv(new Date(now.getTime()-48*HOUR).toISOString(),now.toISOString());
 });
 $("csv-7d").addEventListener("click",()=>{
  const now=new Date();
  downloadCsv(new Date(now.getTime()-7*DAY).toISOString(),now.toISOString());
 });
 $("csv-custom").addEventListener("click",()=>{
  const from=$("csv-from").value,to=$("csv-to").value;
  const fromDate=new Date(from+"T00:00:00Z"),toDate=new Date(to+"T00:00:00Z");
  if(!/^\d{4}-\d{2}-\d{2}$/.test(from)||!/^\d{4}-\d{2}-\d{2}$/.test(to)||
     !Number.isFinite(fromDate.getTime())||!Number.isFinite(toDate.getTime())||
     (toDate-fromDate)/DAY<0||(toDate-fromDate)/DAY>30){
    label("csv-export-info","Choisis une période valide de 1 à 31 jours calendaires inclus.");
    return;
  }
  downloadCsv(from,to);
 });
 load();archives();setInterval(()=>{if(live)load();},30000);
})();

