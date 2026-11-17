"use strict";
(() => {
 const light = document.getElementById('heartbeat-light');
 const state = document.getElementById('heartbeat-state');
 const last = document.getElementById('heartbeat-last');
 if (!light || !state || !last) return;
 const labels = {NORMAL:'Actif', INITIALISATION:'En attente', ARRET:'Arrêté', DEFAUT:'Défaut'};
 let timer = null, freshness = null, pulseTimeout = null, lost = false;
 let pulseValid = false, pulseDeadline = null, period = null, width = null;
 const extinguish = () => {clearTimeout(timer); light.classList.remove('pulse');};
 const forgetPulse = () => {clearTimeout(pulseTimeout); pulseValid = false; pulseDeadline = null; extinguish();};
 const disconnected = () => {forgetPulse(); state.textContent = 'Défaut · liaison de surveillance interrompue';};
 const received = () => {clearTimeout(freshness); freshness = setTimeout(disconnected, 3500);};
 const stream = new EventSource('./api/heartbeat-events');
 stream.onopen = () => {lost = false; forgetPulse(); received();};
 stream.addEventListener('state', event => {
  received();
  const data = JSON.parse(event.data);
  period = Number.isFinite(data.period_s) && data.period_s > 0 ? data.period_s : null;
  width = Number.isFinite(data.width_s) && data.width_s > 0 ? data.width_s : null;
  if (pulseValid && performance.now() >= pulseDeadline) forgetPulse();
  state.textContent = lost ? 'Défaut · événements de battement perdus' :
   (data.state === 'NORMAL' && !pulseValid ? 'En attente · impulsion non confirmée' :
    (labels[data.state] || 'En attente') + (data.reason ? ' · ' + data.reason : ''));
  if (data.state !== 'NORMAL') forgetPulse();
  if (data.last_pulse_at) {
   const date = new Date(data.last_pulse_at);
   last.textContent = 'Dernière impulsion logicielle : ' + (Number.isNaN(date.valueOf()) ? data.last_pulse_at : date.toLocaleString('fr-CH'));
  }
 });
 stream.addEventListener('pulse', event => {
  received();
  const data = JSON.parse(event.data);
  if (Number.isFinite(data.period_s) && data.period_s > 0) period = data.period_s;
  if (Number.isFinite(data.width_s) && data.width_s > 0) width = data.width_s;
  if (data.missed) lost = true;
  const limit = period === null || width === null ? null : period + Math.max(1, width * 2);
  const age = Object.prototype.hasOwnProperty.call(data, 'pulse_age_s') ? data.pulse_age_s : 0;
  const timestamp = data.pulse ? Date.parse(data.pulse.at) : null;
  const wallAge = timestamp === null ? 0 : (Date.now() - timestamp) / 1000;
  if (lost || data.state !== 'NORMAL' || limit === null || !Number.isFinite(age) || age < 0 || age > limit || !Number.isFinite(wallAge) || wallAge < -2 || wallAge > limit) {
   forgetPulse();
   state.textContent = lost ? 'Défaut · événements de battement perdus' :
    data.state !== 'NORMAL' ? (labels[data.state] || 'En attente') : 'En attente · impulsion non confirmée';
   return;
  }
  pulseValid = true;
  pulseDeadline = performance.now() + (limit - age) * 1000;
  clearTimeout(pulseTimeout);
  pulseTimeout = setTimeout(() => {
   forgetPulse();
   state.textContent = 'Défaut · impulsion logicielle non confirmée';
  }, (limit - age) * 1000);
  state.textContent = labels.NORMAL;
  extinguish();
  light.classList.add('pulse');
  timer = setTimeout(() => light.classList.remove('pulse'), 100);
 });
 stream.onerror = disconnected;
 document.addEventListener('visibilitychange', () => {
  forgetPulse();
  state.textContent = 'En attente · impulsion non confirmée';
 });
 window.addEventListener('pagehide', event => {clearTimeout(freshness); forgetPulse(); if (!event.persisted) stream.close();});
})();
