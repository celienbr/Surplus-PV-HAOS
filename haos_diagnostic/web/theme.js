"use strict";
/* Local appearance preference only. Old preferences migrate without being displayed. */
(function(){
 const KEY='surplus-pv-theme-v1',valid=new Set(['auto','light','dark']);
 let selected='auto';try{const saved=localStorage.getItem(KEY);selected=({forest:'dark',lake:'light'})[saved]||saved||'auto';}catch(_){}
 if(!valid.has(selected))selected='auto';
 const media=matchMedia('(prefers-color-scheme: dark)');
 const resolved=()=>selected==='auto'?(media.matches?'dark':'light'):selected;
 function colors(){const s=getComputedStyle(document.documentElement),get=k=>s.getPropertyValue('--'+k).trim();return{background:get('chart-bg'),grid:get('chart-grid'),ticks:get('chart-tick'),solar:get('solar-chart'),solarActual:get('solar-actual-chart'),pac:get('pac-chart'),surplus:get('surplus-chart'),heater:get('heater-chart'),temperature:get('temp-chart'),target:get('target-chart'),network:get('network-chart')};}
 function refresh(){document.documentElement.dataset.theme=resolved();document.documentElement.style.colorScheme=resolved();document.querySelectorAll('[data-theme-picker]').forEach(e=>e.value=selected);window.dispatchEvent(new CustomEvent('surplus-theme-change'));}
 function update(value){if(!valid.has(value))return;selected=value;try{localStorage.setItem(KEY,value);}catch(_){}refresh();}
 refresh();
 function setup(){const bar=document.createElement('div');bar.className='theme-choice';const label=document.createElement('label');label.htmlFor='theme-picker';label.textContent='Apparence';const picker=document.createElement('select');picker.id='theme-picker';picker.dataset.themePicker='';picker.setAttribute('aria-label','Choisir le thème visuel');for(const [value,title] of [['light','Clair'],['dark','Sombre'],['auto','Automatique']]){const opt=document.createElement('option');opt.value=value;opt.textContent=title;picker.append(opt);}picker.value=selected;picker.onchange=()=>update(picker.value);bar.append(label,picker);const slot=document.getElementById('theme-slot');if(slot)slot.append(bar);else document.querySelector('header')?.after(bar);}
 if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',setup);else setup();
 media.addEventListener('change',()=>{if(selected==='auto')refresh();});
 addEventListener('storage',e=>{if(e.key===KEY){selected=valid.has(e.newValue)?e.newValue:'auto';refresh();}});
 window.SurplusTheme={colors,resolve:resolved,set:update,selected:()=>selected};
})();

/* Presentation only: per-tab navigation state, reset on reload or new tab. */
(function(){
 const KEY='surplus-pv-view-session';
 const embedded=document.documentElement.classList.contains('embedded');
 let mode='normal';
 try {
  if(embedded && parent!==window && parent.SurplusViewMode) mode=parent.SurplusViewMode.selected();
  else if(!embedded){
   const navigation=performance.getEntriesByType('navigation')[0];
   const referrer=document.referrer ? new URL(document.referrer) : null;
   const appRoot=new URL('.',location.href).pathname;
   const fromApp=referrer && referrer.origin===location.origin && referrer.pathname.startsWith(appRoot);
   if((navigation && navigation.type==='reload') || !fromApp || window.opener) sessionStorage.removeItem(KEY);
   mode=sessionStorage.getItem(KEY)==='diagnostic'?'diagnostic':'normal';
  }
 } catch(_){}
 function apply(value, persist=true){
  mode=value==='diagnostic'?'diagnostic':'normal';
  document.documentElement.dataset.viewMode=mode;
  document.querySelectorAll('[data-view-picker]').forEach(el=>el.value=mode);
  if(persist&&!embedded)try{sessionStorage.setItem(KEY,mode);}catch(_){}
  window.dispatchEvent(new CustomEvent('surplus-view-change',{detail:mode}));
 }
 window.SurplusViewMode={selected:()=>mode,set:apply};
 apply(mode,false);
 if(embedded){
  try {parent.addEventListener('surplus-view-change',event=>apply(event.detail,false));}catch(_){}
  return;
 }
 function setup(){
  const slot=document.getElementById('theme-slot');if(!slot)return;
  const bar=document.createElement('div');bar.className='theme-choice view-choice';
  const label=document.createElement('label');label.htmlFor='view-picker';label.textContent='Affichage';
  const picker=document.createElement('select');picker.id='view-picker';picker.dataset.viewPicker='';
  picker.setAttribute('aria-label','Mode d’affichage');
  for(const [value,title] of [['normal','Normal'],['diagnostic','Diagnostic']]){
   const option=document.createElement('option');option.value=value;option.textContent=title;picker.append(option);
  }
  picker.value=mode;picker.onchange=()=>apply(picker.value);bar.append(label,picker);slot.append(bar);
 }
 if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',setup);else setup();
})();
