/* 05 What-if simulator (digital twin) */
(function () {
  'use strict';
  const C = Charts.colors;
  const DEF = { attendance_pct: 100, traffic_moved_pct: 0, temp_capacity_mbps: 0, home_away: '' };
  let q = null, seq = 0, comparison = null, lastResult = null, saved = [], compareSeq = 0;
  const STORE = 'vdi-scenarios-v1';
  const PRESETS = [
    ['No mitigation', { attendance_pct: 100, traffic_moved_pct: 0, temp_capacity_mbps: 0, home_away: '' }],
    ['Crowd surge', { attendance_pct: 125, traffic_moved_pct: 0, temp_capacity_mbps: 0, home_away: 'home' }],
    ['Balanced relief', { attendance_pct: 110, traffic_moved_pct: 20, temp_capacity_mbps: 200, home_away: '' }],
    ['Extra capacity', { attendance_pct: 120, traffic_moved_pct: 30, temp_capacity_mbps: 600, home_away: '' }],
    ['Away crowd', { attendance_pct: 100, traffic_moved_pct: 0, temp_capacity_mbps: 0, home_away: 'away' }]
  ];

  function readSaved() {
    try {
      const rows = JSON.parse(localStorage.getItem(STORE) || '[]');
      if (!Array.isArray(rows) || rows.length > 8 || rows.some(row =>
        !row || typeof row.name !== 'string' || row.name.length > 60 || !row.inputs ||
        typeof row.inputs.event_id !== 'string' || !['', 'home', 'away', 'neutral'].includes(row.inputs.home_away) ||
        ![['attendance_pct', 30, 130], ['traffic_moved_pct', 0, 50], ['temp_capacity_mbps', 0, 1000]]
          .every(([key, min, max]) => Number.isFinite(row.inputs[key]) && row.inputs[key] >= min && row.inputs[key] <= max))) {
        throw new Error('Saved scenario data is invalid.');
      }
      return rows;
    } catch (e) { App.toast('Could not load saved scenarios: ' + e.message); return []; }
  }

  function writeSaved(rows) {
    try { localStorage.setItem(STORE, JSON.stringify(rows)); saved = rows; return true; }
    catch (e) { App.toast('Could not save scenarios: ' + e.message); return false; }
  }

  function updateAI() {
    if (!lastResult) return;
    const selection = Object.assign({ event_id: lastResult.event.event_id }, lastResult.inputs);
    if (comparison) selection.comparison = Object.assign({ event_id: comparison.event.event_id }, comparison.inputs);
    App.setAISelection(selection);
  }

  function renderComparison(el) {
    const box = el.querySelector('#sm-comparison');
    if (!comparison || !lastResult) {
      box.innerHTML = '<div class="empty">Pin the current result or compare a saved scenario to see two plans side by side.</div>';
      return;
    }
    const a = comparison.kpis.after, b = lastResult.kpis.after;
    box.innerHTML = `<p class="small">${App.esc(comparison.event.event_name)} vs ${App.esc(lastResult.event.event_name)}
      ${comparison.event.event_id !== lastResult.event.event_id ? ' (different events)' : ''}</p>
      <div class="scroll"><table class="tbl"><thead><tr><th>Metric</th><th class="num">Pinned plan</th><th class="num">Current plan</th><th class="num">Change</th></tr></thead><tbody>
      ${METRICS.map(([key, label, fmt, good]) => {
        const delta = b[key] - a[key];
        return `<tr><td>${label}</td><td class="num">${fmt(a[key])}</td><td class="num">${fmt(b[key])}</td>
          <td class="num ${Math.abs(delta) < 1e-9 ? '' : delta * good > 0 ? 'delta-good' : 'delta-bad'}">${delta > 0 ? '+' : ''}${fmt(delta)}</td></tr>`;
      }).join('')}</tbody></table></div>`;
  }

  function scenario(inputs) {
    const params = new URLSearchParams(Object.entries(inputs).filter(([, value]) => value !== '' && value !== null));
    return App.api('/api/simulate?' + params);
  }

  const METRICS = [
    ['max_prb', 'Peak UL PRB · %', v => v.toFixed(0), -1],
    ['hot_cells', 'Cells ≥85% PRB', v => v.toFixed(0), -1],
    ['congestion_min', 'Congestion · cell-min', v => App.num(v), -1],
    ['drop_rate', 'Session drops · %', v => v.toFixed(2), -1],
    ['p10_avg', 'Uplink P10 · Mbps', v => v.toFixed(2), 1],
    ['accessibility', 'Accessibility · %', v => v.toFixed(1), 1],
    ['site_bh_max', 'Busiest site link · %', v => v.toFixed(0), -1],
    ['agg_bh', 'Aggregation link · %', v => v.toFixed(0), -1],
  ];

  function compareCard(m, b, a) {
    const [k, label, f, good] = m;
    const dv = a[k] - b[k];
    const better = dv * good > 0, same = Math.abs(dv) < 1e-9;
    return `<div class="kpi compact"><div class="kpi-label">${label}</div>
      <div class="compare" style="margin-top:8px"><span class="b">${f(b[k])}</span><span class="arrow">→</span><span class="a" style="color:${same ? C.black : better ? C.green : C.red}">${f(a[k])}</span></div>
      <div class="${same ? 'muted small' : better ? 'delta-good' : 'delta-bad'}">${same ? 'no change' : (dv > 0 ? '+' : '−') + f(Math.abs(dv))}</div></div>`;
  }

  async function run(el) {
    if (!el.isConnected || App.current !== 'simulator') return;
    const my = ++seq;
    const r = await scenario(q);
    if (my !== seq || !el.isConnected || !el.querySelector('#sm-take') || App.current !== 'simulator') return;
    lastResult = r;
    updateAI();
    const b = r.kpis.before, a = r.kpis.after;
    el.querySelector('#sm-take').textContent = r.takeaway;
    el.querySelector('#sm-method').textContent = 'Digital twin: ' + r.method;
    el.querySelector('#sm-att').innerHTML = `<b>${App.num(r.attendance.scenario)}</b> fans <span class="muted">(forecast ${App.num(r.attendance.forecast)}, capacity ${App.num(r.attendance.capacity)}, ${App.esc(r.inputs.home_away)} crowd)</span>`;
    el.querySelector('#sm-cmp').innerHTML = METRICS.map(m => compareCard(m, b, a)).join('');
    Charts.bars('#sm-cells', {
      labels: r.cells.map(c => c.cell_id.replace(/^V\d+-/, '')), height: 280, max: 105, format: v => v.toFixed(0) + '%',
      series: [{ name: 'Before mitigation', values: r.cells.map(c => c.before), color: C.g3 }, { name: 'After mitigation', values: r.cells.map(c => c.after), color: C.red }],
      thresholds: [{ v: 85, label: '85%', color: C.black }]
    });
    Charts.line('#sm-hourly', {
      x: r.hourly.map(h => h.hour_rel), height: 280, yMin: 0, yMax: 100, yFormat: v => v + '%', xFormat: v => (v > 0 ? '+' : '') + v + 'h',
      series: [{ name: 'Busiest cell - before', values: r.hourly.map(h => h.before), color: C.g4, width: 2, dash: '5 4' },
               { name: 'Busiest cell - after', values: r.hourly.map(h => h.after), color: C.red, width: 2.5, area: true }],
      thresholds: [{ y: 85, label: 'congestion threshold', color: C.black }], markers: [{ x: 0, label: 'start', color: C.g4 }]
    });
    Charts.bars('#sm-bh', {
      labels: r.backhaul.map(l => l.link_id.replace(/^V\d+-/, '')), height: 240, max: 105, format: v => v.toFixed(0) + '%',
      series: [{ name: 'Before', values: r.backhaul.map(l => l.before), color: C.g3 }, { name: 'After', values: r.backhaul.map(l => l.after), color: C.black }],
      thresholds: [{ v: 90, label: '90% agg', color: C.red }, { v: 85, label: '85% site', color: C.amber }]
    });
    el.querySelector('#sm-moves').innerHTML = r.moves.length ? r.moves.map(m => `<div class="small">${App.esc(m.from)} → ${App.esc(m.to)}: ${m.prb_pts} PRB pts</div>`).join('')
      : '<div class="small muted">No traffic moved - raise “traffic moved” to steer hot-cell users to neighbours with headroom.</div>';
    renderComparison(el);
  }

  App.views.simulator = {
    async render(el, params) {
      const evs = (await App.api('/api/simulate/events')).events;
      const def = await App.api('/api/simulate');
      if (!el.isConnected || App.current !== 'simulator') return;
      saved = readSaved();
      lastResult = null;
      comparison = null;
      q = Object.assign({}, DEF, { event_id: params[0] || (q && q.event_id) || def.event.event_id });
      el.innerHTML = `
        ${App.head('What-if simulator', 'Test the plan before match day', '')}
        <div class="card playground-presets"><div class="card-head"><h2>Try a scenario</h2><span class="pill info">Synthetic digital twin</span></div>
          <div class="toolbar">${PRESETS.map(([label], index) => `<button class="btn ghost" data-preset="${index}">${label}</button>`).join('')}</div></div>
        <div class="grid g-1-2">
          <div class="card">
            <div class="card-head"><h2>Scenario</h2><button class="btn ghost" id="sm-reset">Reset</button></div>
            <div class="field" style="margin-bottom:16px"><label>Event</label>
              <select id="sm-event">${evs.map(e => `<option value="${e.event_id}" ${e.event_id === q.event_id ? 'selected' : ''}>${App.esc(e.label)}</option>`).join('')}</select></div>
            <div class="field" style="margin-bottom:16px"><label>Crowd type</label>
              <div class="seg" id="sm-ha">${[['', 'Scheduled'], ['home', 'Home'], ['away', 'Away'], ['neutral', 'Neutral']].map(([v, l]) => `<button data-v="${v}" class="${q.home_away === v ? 'on' : ''}">${l}</button>`).join('')}</div></div>
            <div class="field" style="margin-bottom:16px"><label for="sm-att-r">Attendance vs forecast <b id="v-att"></b></label><input type="range" id="sm-att-r" min="30" max="130" step="5"></div>
            <div class="field" style="margin-bottom:16px"><label for="sm-mov-r">Hot-cell traffic steered to neighbours <b id="v-mov"></b></label><input type="range" id="sm-mov-r" min="0" max="50" step="5"></div>
            <div class="field" style="margin-bottom:12px"><label for="sm-cap-r">Temporary UL capacity added <b id="v-cap"></b></label><input type="range" id="sm-cap-r" min="0" max="1000" step="50"></div>
            <div class="foot-note" id="sm-att"></div>
            <div style="margin-top:14px"><h3>Moves applied</h3><div id="sm-moves"></div></div>
          </div>
          <div class="grid g2">
            <div class="card"><div class="card-head"><h2>Compare plans</h2><div class="toolbar"><button class="btn" id="sm-pin">Pin current result</button><button class="btn ghost" id="sm-unpin">Clear comparison</button></div></div>
              <div id="sm-comparison"></div></div>
            <div class="card"><div class="card-head"><div><h2>Saved scenarios</h2><div class="card-sub">Stored in this browser only, maximum 8 plans. No API key is stored here.</div></div></div>
              <div class="toolbar"><input id="sm-name" type="text" maxlength="60" placeholder="Plan name" aria-label="Scenario name"><button class="btn" id="sm-save">Save current result</button></div>
              <div id="sm-saved"></div></div>
          </div>
          <div>
            <p class="takeaway" id="sm-take" style="margin-top:0"></p>
            <div class="kpis" id="sm-cmp" style="grid-template-columns:repeat(4,minmax(0,1fr))"></div>
            <div class="card"><div class="card-head"><div><h2>Peak uplink per cell</h2><div class="card-sub">Before vs after mitigation (forecast peak UL PRB)</div></div></div><div id="sm-cells" class="chart-box"></div></div>
          </div>
        </div>
        <div class="grid g2">
          <div class="card"><div class="card-head"><div><h2>Event-day profile</h2><div class="card-sub">Busiest cell, hourly peak UL PRB (category profile × cell forecast)</div></div></div><div id="sm-hourly" class="chart-box"></div></div>
          <div class="card"><div class="card-head"><div><h2>Backhaul utilisation</h2><div class="card-sub">Busiest-15-min utilisation per link, before vs after</div></div></div><div id="sm-bh" class="chart-box"></div></div>
        </div>
        <div class="foot-note" id="sm-method"></div>`;
      const sync = () => {
        el.querySelector('#sm-event').value = q.event_id;
        el.querySelector('#sm-att-r').value = q.attendance_pct; el.querySelector('#v-att').textContent = q.attendance_pct + '%';
        el.querySelector('#sm-mov-r').value = q.traffic_moved_pct; el.querySelector('#v-mov').textContent = q.traffic_moved_pct + '%';
        el.querySelector('#sm-cap-r').value = q.temp_capacity_mbps; el.querySelector('#v-cap').textContent = q.temp_capacity_mbps + ' Mbps';
        el.querySelectorAll('#sm-ha button').forEach(b => b.classList.toggle('on', b.dataset.v === q.home_away));
      };
      const go = App.debounce(() => run(el).catch(e => App.toast(e.message)), 120);
      const drawSaved = () => {
        el.querySelector('#sm-saved').innerHTML = saved.length ? saved.map((row, index) => `<div class="saved-plan">
          <span><b>${App.esc(row.name)}</b><span class="small muted"> ${row.inputs.attendance_pct}% crowd / ${row.inputs.traffic_moved_pct}% steer / ${row.inputs.temp_capacity_mbps} Mbps</span></span>
          <div class="toolbar"><button class="btn ghost" data-load="${index}">Load</button><button class="btn ghost" data-compare="${index}">Compare</button><button class="btn ghost" data-delete="${index}">Delete</button></div></div>`).join('')
          : '<div class="empty">Save a plan to revisit or compare it later.</div>';
        el.querySelectorAll('[data-load]').forEach(button => button.addEventListener('click', () => {
          const inputs = saved[+button.dataset.load].inputs;
          if (!evs.some(event => event.event_id === inputs.event_id)) { App.toast('Saved event is no longer available.'); return; }
          q = Object.assign({}, inputs); sync(); go();
        }));
        el.querySelectorAll('[data-compare]').forEach(button => button.addEventListener('click', async () => {
          const request = ++compareSeq;
          try {
            const result = await scenario(saved[+button.dataset.compare].inputs);
            if (request !== compareSeq || !el.isConnected || App.current !== 'simulator') return;
            comparison = result; renderComparison(el); updateAI();
          } catch (e) { App.toast('Could not compare scenario: ' + e.message); }
        }));
        el.querySelectorAll('[data-delete]').forEach(button => button.addEventListener('click', () => {
          if (writeSaved(saved.filter((_, index) => index !== +button.dataset.delete))) drawSaved();
        }));
      };
      el.querySelector('#sm-save').addEventListener('click', () => {
        const name = el.querySelector('#sm-name').value.trim();
        if (!name || !lastResult) { App.toast('Enter a plan name and wait for the result to finish.'); return; }
        if (saved.length >= 8) { App.toast('Delete a saved plan before adding another (limit 8).'); return; }
        const row = { name, inputs: Object.assign({ event_id: lastResult.event.event_id }, lastResult.inputs) };
        if (writeSaved(saved.concat(row))) { drawSaved(); App.toast('Scenario saved in this browser.'); }
      });
      el.querySelector('#sm-pin').addEventListener('click', () => {
        if (!lastResult) return;
        compareSeq++; comparison = lastResult; renderComparison(el); updateAI();
      });
      el.querySelector('#sm-unpin').addEventListener('click', () => {
        compareSeq++; comparison = null; renderComparison(el); updateAI();
      });
      el.querySelectorAll('[data-preset]').forEach(button => button.addEventListener('click', () => {
        q = Object.assign({ event_id: q.event_id }, PRESETS[+button.dataset.preset][1]); sync(); go();
      }));
      drawSaved();
      const bind = (id, key) => el.querySelector(id).addEventListener('input', e => { q[key] = +e.target.value; sync(); go(); });
      bind('#sm-att-r', 'attendance_pct'); bind('#sm-mov-r', 'traffic_moved_pct'); bind('#sm-cap-r', 'temp_capacity_mbps');
      el.querySelectorAll('#sm-ha button').forEach(b => b.addEventListener('click', () => { q.home_away = b.dataset.v; sync(); go(); }));
      el.querySelector('#sm-event').addEventListener('change', e => { q.event_id = e.target.value; go(); });
      el.querySelector('#sm-reset').addEventListener('click', () => { q = Object.assign({}, DEF, { event_id: q.event_id }); sync(); go(); });
      sync();
      if (q.event_id === def.event.event_id && q.attendance_pct === 100 && !q.traffic_moved_pct && !q.temp_capacity_mbps && !q.home_away) {
        // show a meaningful default mitigation scenario
        q.traffic_moved_pct = 20; q.temp_capacity_mbps = 200; sync();
      }
      await run(el);
    },
    destroy() { seq++; compareSeq++; }
  };
})();
