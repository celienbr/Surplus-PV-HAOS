/* Informational only: the controller snapshot is the source of accepted commands. */
(function(root){
 'use strict';
 const finite=n=>typeof n==='number'&&Number.isFinite(n);
 function powerGauge(s){
  if(!s || typeof s!=='object')return {value:null,detail:'État indisponible · état physique non vérifié'};
  const mode=s.mode, manual=['MANUEL','MANUEL_REEL','MANUEL_BANC'].includes(mode);
  const auto=['AUTO','AUTO_REEL','AUTO_BANC'].includes(mode);
  const label=mode==='AUTO'?'Calcul simulé':mode==='MANUEL'?'Simulation':mode==='MANUEL_BANC'||mode==='AUTO_BANC'?'Banc isolé':mode==='BANC_DAC'?'Banc DAC':mode==='AUTO_REEL'?'Consigne calculée':'Commande acceptée';
  if(mode==='BANC_DAC')return {value:null,detail:'Banc DAC : courant direct, pourcentage de puissance indisponible · état physique non vérifié'};
  if(!manual&&!auto)return {value:null,detail:'Mode inconnu · état physique non vérifié'};
  let n;
  if(manual){if(typeof s.enabled!=='boolean')n=null;else n=s.enabled?s.manual_percent:0;}
  else if(mode!=='AUTO'&&s.enabled===false)n=0;
  else if(mode!=='AUTO'&&s.enabled!==true)n=null;
  else n=finite(s.auto_virtual_power_w)&&finite(s.max_power_w)&&s.max_power_w>0?100*s.auto_virtual_power_w/s.max_power_w:null;
  return {value:finite(n)?Math.min(100,Math.max(0,n)):null,detail:label+' · état physique non vérifié'};
 }
 function render(s){
  const result=powerGauge(s),track=document.getElementById('power-track');
  if(!track)return;
  const value=result.value,display=value===null?'—':Number(value.toFixed(1)).toLocaleString('fr-CH')+' %';
  document.getElementById('power-label').textContent='Puissance demandée au boiler : '+display;
  document.getElementById('power-fill').style.width=(value??0)+'%';
  document.getElementById('power-detail').textContent=result.detail;
  if(value===null){track.removeAttribute('aria-valuenow');track.setAttribute('aria-valuetext','Indisponible');}
  else {track.setAttribute('aria-valuenow',String(Number(value.toFixed(1))));track.setAttribute('aria-valuetext',display+' · '+result.detail);}
 }
 if(typeof module!=='undefined'&&module.exports)module.exports=powerGauge;
 if(root)root.renderPowerGauge=render;
})(typeof window==='undefined'?null:window);
