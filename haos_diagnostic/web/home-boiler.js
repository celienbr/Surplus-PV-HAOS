/* Surplus PV Alpha.27 — home boiler gauge, using the same API contract as the
 * complete Boiler screen. The private preview intercepts these calls locally. */
(() => {
  'use strict';
  const slider = document.getElementById('home-boiler-power');
  if (!slider) return;
  const value = document.getElementById('home-boiler-power-value');
  const label = document.getElementById('home-boiler-power-label');
  const hint = document.getElementById('home-boiler-power-hint');
  const result = document.getElementById('home-boiler-command-result');
  let token = null, canAdjust = false, editing = false, busy = false;
  const valid = n => typeof n === 'number' && Number.isFinite(n);
  const clamp = n => Math.max(0, Math.min(100, n));
  const watts = n => Math.round(n).toLocaleString('fr-CH') + ' W';

  function paint(percent, text, explanation) {
    const safe = valid(percent) ? clamp(percent) : 0;
    slider.value = String(Math.round(safe));
    slider.style.setProperty('--home-level', safe + '%');
    value.textContent = text;
    hint.textContent = explanation;
  }

  function render(output = {}, status = {}) {
    if (busy || editing) return;
    token = output.token || status.weather_toggle_token || token;
    const manual = output.mode === 'MANUEL_REEL';
    canAdjust = manual && !output.startup_pending && !output.error && !output.dac_recovery?.pending &&
      status.boiler_temperature_diagnostic?.status === 'FRESH' && !!token;
    slider.disabled = !canAdjust;
    slider.classList.toggle('is-adjustable', canAdjust);
    slider.setAttribute('aria-label', manual ? 'Régler la commande manuelle du DAC, en pourcentage' : 'Puissance demandée, lecture seule');
    label.textContent = manual ? 'Commande manuelle du DAC' : 'Puissance demandée au boiler';
    if (manual) {
      const accepted = output.enabled === true && !output.error && !output.startup_pending &&
        !output.dac_recovery?.pending && valid(output.manual_percent) ? clamp(output.manual_percent) : null;
      const note = output.error ? 'Défaut actif : réglage suspendu.' :
        output.startup_pending ? 'Reprise en attente : réglage suspendu.' :
        !canAdjust ? 'Réglage indisponible : mesure non validée.' :
        output.heat_requested ? 'Glissez le curseur puis relâchez pour appliquer la consigne DAC.' :
        'Consigne mémorisée ; aucune chauffe demandée actuellement. Glissez pour la modifier.';
      const nominal = accepted !== null && valid(output.max_power_w) && output.max_power_w > 0 ?
        ' · '+watts(accepted * output.max_power_w / 100)+' estimés' : '';
      paint(accepted, accepted === null ? '—' : Math.round(accepted) + ' % DAC'+nominal, note);
      return;
    }
    if (output.error || output.startup_pending || output.dac_recovery?.pending) {
      paint(null, '—', 'Commande non confirmée : '+(output.reason||'vérification nécessaire.'));
    } else if (output.mode === 'AUTO') {
      paint(0, '0 W', 'ARRÊT · bargraphe en lecture seule.');
    } else if (output.mode === 'AUTO_REEL') {
      const requested = output.enabled === false ? 0 : output.auto_requested_power_w;
      const percent = valid(requested) && valid(output.max_power_w) && output.max_power_w > 0 ?
        100 * requested / output.max_power_w : null;
      paint(percent, valid(requested) ? watts(requested) : '—',
        output.error ? 'Commande suspendue : défaut de commande.' : 'AUTO · bargraphe en lecture seule (commande, non mesure).');
    } else {
      paint(null, '—', 'État de la commande non confirmé.');
    }
  }

  slider.addEventListener('input', () => {
    if (!canAdjust || busy) return;
    editing = true;
    result.textContent = '';
    const percent = clamp(Number(slider.value));
    paint(percent, Math.round(percent) + ' % DAC', 'Relâchez pour appliquer cette consigne manuelle.');
  });
  slider.addEventListener('change', async () => {
    if (!canAdjust || busy) return;
    busy = true;
    editing = true;
    slider.disabled = true;
    const percent = clamp(Number(slider.value));
    result.textContent = 'Envoi de la consigne…';
    try {
      const response = await fetch('./api/output', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({token, command: {action: 'manual', percent}})
      });
      if (!response.ok) {
        const error = await response.json().catch(() => ({}));
        throw new Error(error.error || 'Commande refusée');
      }
      result.textContent = 'Commande acceptée ; état en cours de vérification.';
    } catch (error) {
      result.textContent = 'Réglage refusé ou non confirmé : ' + error.message;
    } finally {
      busy = false;
      editing = false;
      if (typeof window.surplusRefreshDashboard === 'function')
        await window.surplusRefreshDashboard();
    }
  });

  function offline() {
    canAdjust = false;
    editing = false;
    slider.disabled = true;
    slider.classList.remove('is-adjustable');
    result.textContent = 'Communication indisponible : commande non confirmée.';
    paint(null, '—', 'Communication indisponible : réglage désactivé.');
  }
  window.SurplusHomeBoiler = {render, offline};
})();

/* Read-only energy views, sharing the same solar boiler value. */
(() => {
  const el = id => document.getElementById(id);
  if (!el('ce-bar')) return;
  const valid = n => typeof n === 'number' && Number.isFinite(n) && n >= 0;
  const num = n => n.toLocaleString('fr-CH', {minimumFractionDigits:1, maximumFractionDigits:1});
  const kwh = n => valid(n) ? num(n)+' kWh' : '—';
  const matchesTotal = (total, ...parts) => [total, ...parts].every(valid) &&
    Math.abs(parts.reduce((sum, part) => sum + part, 0) - total) < 0.001;
  const when = value => new Date(value).toLocaleString('fr-CH', {timeZone:'Europe/Zurich'});
  const fits = segment => segment.classList.toggle('has-room',
    segment.clientWidth >= segment.firstChild.getBoundingClientRect().width + 8);
  const resize = new ResizeObserver(entries => entries.forEach(({target}) => fits(target)));

  function draw(bar, legend, parts, denominator) {
    for (const segment of bar.children) resize.unobserve(segment);
    bar.replaceChildren();
    legend.replaceChildren();
    for (const [kind, label, value] of parts) {
      if (denominator > 0 && value > 0) {
        const segment = document.createElement('span'), number = document.createElement('span');
        segment.className = 'ce-segment ce-' + kind;
        segment.style.width = (value / denominator * 100) + '%';
        segment.setAttribute('aria-hidden', 'true');
        number.textContent = num(value);
        segment.append(number);
        bar.append(segment);
        resize.observe(segment);
      }
      const item = document.createElement('span'), swatch = document.createElement('i');
      swatch.className = 'ce-' + kind;
      item.append(swatch, document.createTextNode(label + ' : ' + kwh(value)));
      legend.append(item);
    }
  }

  function render(data) {
    el('ce-remaining').textContent = data.comfort_met ? 'Confort atteint' :
      valid(data.remaining_kwh) ? kwh(data.remaining_kwh) : '—';
    el('ce-remaining-label').textContent = data.comfort_met ? '' :
      valid(data.remaining_kwh) ? 'à fournir' : 'Besoin restant indisponible';
    el('ce-reference').textContent = kwh(data.reference_kwh);
    el('ce-provided').textContent = kwh(data.provided_kwh);
    const split = matchesTotal(data.provided_kwh, data.pv_kwh, data.grid_kwh);
    const partial = !split &&
      matchesTotal(data.covered_provided_kwh, data.covered_pv_kwh, data.covered_grid_kwh) &&
      matchesTotal(data.covered_production_kwh, data.covered_other_pv_kwh, data.covered_pv_kwh, data.covered_injection_kwh);
    const parts = split ? [['pv','Surplus PV',data.pv_kwh],['grid','Réseau',data.grid_kwh]] :
      partial ? [['pv','Surplus PV',data.covered_pv_kwh],['grid','Réseau',data.covered_grid_kwh]] :
      [['unknown','Déjà fournis',data.provided_kwh], ['pv','Surplus PV',null], ['grid','Réseau',null]];
    if (!partial && valid(data.remaining_kwh)) parts.push(['rest','À fournir',data.remaining_kwh]);
    const denominator = partial ? data.covered_provided_kwh :
      valid(data.remaining_kwh) ? data.reference_kwh : data.provided_kwh;
    draw(el('ce-bar'), el('ce-legend'), parts, denominator);
    el('ce-status').textContent = partial ? 'Sur les périodes couvertes · ' + kwh(data.covered_provided_kwh) :
      split ? '' : 'Répartition indisponible';
    el('ce-note').textContent = [
      'Depuis minuit (Europe/Zurich).', (split || partial) ? data.balance_source : 'Répartition indisponible.',
      !split && valid(data.provided_kwh) ? 'Total journalier estimé à partir de la puissance demandée.' : null,
      data.through ? 'Dernier relevé du cumul à ' + when(data.through) + '.' : 'Cumul journalier indisponible.',
      data.balance_from ? 'Première période couverte dès le ' + when(data.balance_from) + '.' : null,
      data.balance_status,
      data.balance_through ? 'Dernier relevé du bilan à ' + when(data.balance_through) + '.' : null
    ].filter(Boolean).join(' ');

    if (!el('pv-home-bar')) return;
    const total = data.production_kwh;
    el('pv-home-total').textContent = valid(total) ? kwh(total) : '—';
    const complete = split && matchesTotal(total, data.other_pv_kwh, data.pv_kwh, data.injection_kwh);
    const showSplit = complete || partial;
    const barTotal = complete ? total : data.covered_production_kwh;
    const solar = complete ? data.pv_kwh : data.covered_pv_kwh;
    const other = complete ? data.other_pv_kwh : data.covered_other_pv_kwh;
    const injection = complete ? data.injection_kwh : data.covered_injection_kwh;
    const surplus = showSplit ? solar + injection : null;
    const bracket = el('pv-surplus-track');
    bracket.hidden = !showSplit;
    if (showSplit) {
      bracket.classList.toggle('no-surplus', surplus === 0);
      bracket.style.setProperty('--surplus-start', (surplus > 0 ? other / barTotal * 100 : 0) + '%');
      el('pv-surplus').textContent = 'Surplus ' + kwh(surplus);
    }
    draw(el('pv-home-bar'), el('pv-home-legend'), [
      ['house','Autres usages',showSplit ? other : null], ['pv','Vers le boiler',showSplit ? solar : null],
      ['export','Injecté',showSplit ? injection : null]
    ], showSplit ? barTotal : null);
    el('pv-home-status').textContent = partial ? 'Sur les périodes couvertes · ' + kwh(barTotal) :
      complete ? '' : 'Répartition indisponible';
    el('pv-home-note').textContent = [
      'Depuis minuit (Europe/Zurich).', data.balance_status || 'Répartition indisponible.',
      valid(data.balance_covered_seconds) ? 'Couverture : ' + Math.floor(data.balance_covered_seconds / 60) + ' min.' : null,
      data.balance_from ? 'Première période dès ' + when(data.balance_from) + '.' : null,
      data.production_through ? 'Compteur Solarman à ' + when(data.production_through) + '.' : null,
      data.balance_through ? 'Dernier relevé du bilan à ' + when(data.balance_through) + '.' : null
    ].filter(Boolean).join(' ');
  }
  window.SurplusComfortEnergy = {render};
  let busy=false;
  async function update(){if(busy)return;busy=true;try{
    const response=await fetch('./api/comfort-energy',{cache:'no-store',signal:AbortSignal.timeout(12000)});
    if(!response.ok)throw Error('indisponible');render(await response.json());
  }catch(_){render({});}finally{busy=false;}}
  update();setInterval(update,30000);
})();
