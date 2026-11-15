"use strict";
(() => {
 const light = document.getElementById('heartbeat-light');
 const state = document.getElementById('heartbeat-state');
 const last = document.getElementById('heartbeat-last');
 if (!light || !state || !last) return;
 const labels = {NORMAL:'Actif', INITIALISATION:'En attente', ARRET:'Arrêté', DEFAUT:'Défaut'};
 let timer = null, freshness = null, lost = false;
 const extinguish = () => {clearTimeout(timer); light.classList.remove('pulse');};
 const disconnected = () => {extinguish(); state.textContent = 'Défaut · liaison de surveillance interrompue';};
 const received = () => {clearTimeout(freshness); freshness = setTimeout(disconnected, 3500);};
 const stream = new EventSource('./api/heartbeat-events');
 stream.onopen = () => {lost = false; received();};
 stream.addEventListener('state', event => {
  received();
  const data = JSON.parse(event.data);
  state.textContent = lost ? 'Défaut · événements de battement perdus' :
   (labels[data.state] || 'En attente') + (data.reason ? ' · ' + data.reason : '');
  if (data.state !== 'NORMAL') extinguish();
  if (data.last_pulse_at) {
   const date = new Date(data.last_pulse_at);
   last.textContent = 'Dernière impulsion logicielle : ' + (Number.isNaN(date.valueOf()) ? data.last_pulse_at : date.toLocaleString('fr-CH'));
  }
 });
 stream.addEventListener('pulse', event => {
  received();
  const data = JSON.parse(event.data);
  if (data.missed) lost = true;
  if (lost || data.state !== 'NORMAL') {
   extinguish();
   if (lost) state.textContent = 'Défaut · événements de battement perdus';
   return;
  }
  extinguish();
  light.classList.add('pulse');
  timer = setTimeout(() => light.classList.remove('pulse'), 100);
 });
 stream.onerror = disconnected;
 window.addEventListener('pagehide', () => {clearTimeout(freshness); extinguish(); stream.close();});
})();
