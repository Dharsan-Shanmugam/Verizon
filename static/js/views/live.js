/* 03 Live command center - replay of the in-progress event */
(function () {
  'use strict';
  const C = Charts.colors;
  const SPEEDS = [1, 5, 15, 30, 60];
  let S = null, snapshotSeq = 0;

  function fmtClock(sec, start) {
    const base = new Date(start.replace(' ', 'T') + ':00');
    const d = new Date(base.getTime() + sec * 1000);
    return d.toTimeString().slice(0, 8);
  }
  function relTime(sec) {
    const s = Math.abs(sec), m = Math.floor(s / 60), r = s % 60;
    return (sec < 0 ? 'T−' : 'T+') + (m >= 60 ? Math.floor(m / 60) + 'h' + String(m % 60).padStart(2, '0') : m) + 'm' + String(r).padStart(2, '0') + 's';
  }

  function decisionState(d, T) {
    const ap = S.approvals[d.decision_id];
    const verified = T >= d.verify_t;
    const steps = [{ k: 'Detected', s: 'done' }, { k: 'Checks', s: d.recommendation === 'fallback' ? 'bad' : 'ok' }];
    let status;
    if (d.tier.startsWith('tier 0')) { steps.push({ k: 'Monitor', s: 'done' }); return { steps, status: 'monitor' }; }
    if (d.recommendation === 'fallback') {
      steps.push({ k: 'Fallback applied', s: 'bad' }, { k: 'NOC escalated', s: 'done' });
      return { steps, status: 'fallback active' };
    }
    if (d.tier.startsWith('tier 2')) {
      if (!ap) { steps.push({ k: 'Awaiting approval', s: 'wait' }, { k: 'Execute', s: '' }, { k: 'Verify', s: '' }); return { steps, status: 'awaiting approval', pending: true }; }
      if (ap.action === 'reject') { steps.push({ k: 'Rejected', s: 'bad' }, { k: 'Monitor only', s: 'done' }); return { steps, status: 'rejected by operator' }; }
      steps.push({ k: 'Approved', s: 'ok' });
    } else steps.push({ k: 'Auto-approved', s: 'ok' });
    steps.push({ k: 'Executed', s: 'done' });
    if (!verified) { steps.push({ k: 'Verifying…', s: 'wait' }); status = 'verifying'; }
    else if (d.verify.result === 'pass') { steps.push({ k: 'Verified', s: 'ok' }); status = 'verified'; }
    else { steps.push({ k: 'Rolled back', s: 'bad' }, { k: 'Fallback', s: 'bad' }); status = 'rolled back → fallback'; }
    return { steps, status };
  }

  function tileHTML(c) {
    const m = S.d.cell_meta[c];
    return `<div class="cell-tile" data-c="${c}"><div class="cid"><span>${c.replace('V01-', '')}</span><span class="band">${App.esc(m.band.split(' ')[0])}</span></div>
      <div class="gauge-box" id="g-${c}"></div>
      <div class="p10">UL P10 <b id="p10-${c}"></b></div>
      <div class="riskbars">
        <span>60 s</span><div class="riskbar" id="rb60-${c}"><i></i></div><span id="rv60-${c}"></span>
        <span>180 s</span><div class="riskbar" id="rb180-${c}"><i></i></div><span id="rv180-${c}"></span>
      </div></div>`;
  }

  function updateTiles(i) {
    const thr = S.d.thresholds;
    S.d.cells.forEach(c => {
      const s = S.d.series[c];
      const prb = s.prb[i], r60 = s.r60[i], r180 = s.r180[i];
      Charts.gauge('#g-' + c, { value: prb, min: 0, max: 100, thresholds: [70, thr.ul_prb_pct_gt], unit: '%', height: 74, format: v => v.toFixed(0) });
      const tile = S.el.querySelector(`.cell-tile[data-c="${c}"]`);
      tile.classList.toggle('hot', !!s.cong[i] || r60 >= thr.alert_probability);
      tile.classList.toggle('sel', c === S.cell);
      S.el.querySelector('#p10-' + c).textContent = (s.p10[i] === null ? '–' : s.p10[i].toFixed(2)) + ' Mbps';
      [[60, r60], [180, r180]].forEach(([h, v]) => {
        const bar = S.el.querySelector(`#rb${h}-${c}`);
        bar.className = 'riskbar ' + (v >= thr.alert_probability ? 'hot' : v >= 0.2 ? 'warm' : '');
        bar.firstElementChild.style.width = Math.round(v * 100) + '%';
        S.el.querySelector(`#rv${h}-${c}`).textContent = Math.round(v * 100) + '%';
      });
    });
  }

  function updateCharts(i) {
    const d = S.d, s = d.series[S.cell], T = d.times;
    const actual = s.prb.map((v, k) => k <= i ? v : null);
    const pk = d.peak_forecast[S.cell];
    const startIdx = 0;
    const sl = a => a.slice(startIdx);
    const x = sl(T);
    const opts = {
      x, height: 280, yMin: 0, yMax: 100, xFormat: t => fmtClock(t, d.event_start).slice(0, 5), xTickWidth: 70, yFormat: v => v.toFixed(0),
      tipFormat: v => v.toFixed(1),
      series: [
        { name: 'Historical-model event curve', values: sl(s.history_fc), color: C.g4, width: 1.6, dash: '5 4' },
        { name: 'Observed synthetic stream', values: sl(actual), color: C.black, width: 2 },
        { name: '60-s forecasts, evaluated when due', values: sl(s.adaptive60_aligned.map((v, k) => k <= i ? v : null)), color: C.green, width: 1.6 },
        { name: 'Next 60 s forecast issued now', values: sl(T.map((t, k) => k >= i && t <= T[i] + 60
          ? s.prb[i] + (s.adaptive60[i] - s.prb[i]) * (t - T[i]) / 60 : null)), color: C.red, width: 2, dash: '3 3' },
      ],
      thresholds: [{ y: d.thresholds.ul_prb_pct_gt, label: `${d.thresholds.ul_prb_pct_gt}% threshold`, color: C.red }],
      cursor: T[i] >= x[0] ? T[i] : null,
      markers: d.decisions.filter(z => z.cell_id === S.cell && z.t <= T[i] && z.t >= x[0]).map(z => ({ x: z.t, color: z.tier.startsWith('tier 3') ? C.red : C.green, dash: '2 3' })),
      shade: [],
      onClick: k => { S.idx = startIdx + k; tick(0, true); },
    };
    if (pk) opts.bands = [{ name: `Pre-event peak forecast ${pk.low.toFixed(0)}–${pk.high.toFixed(0)}%`, low: x.map(() => pk.low), high: x.map(() => pk.high), color: C.red, opacity: 0.08, legendColor: '#F8C8C8' }];
    // shade congestion intervals
    let st = null;
    for (let k = startIdx; k <= i; k++) {
      if (s.cong[k] && st === null) st = T[k];
      if ((!s.cong[k] || k === i) && st !== null) { opts.shade.push({ from: st, to: T[k], color: C.red, opacity: 0.12 }); st = null; }
    }
    Charts.line('#lv-main', opts);
    Charts.line('#lv-risk', {
      x, height: 150, yMin: 0, yMax: 1, xFormat: t => fmtClock(t, d.event_start).slice(0, 5), xTickWidth: 70, yFormat: v => Math.round(v * 100) + '%',
      series: [
        { name: 'Risk 60 s (vdi model)', values: sl(s.r60.map((v, k) => k <= i ? v : null)), color: C.red, width: 2, area: true },
        { name: 'Risk 180 s', values: sl(s.r180.map((v, k) => k <= i ? v : null)), color: C.amber, width: 1.6 },
        { name: 'Risk 60 s (feed)', values: sl(s.file_r60.map((v, k) => k <= i ? v : null)), color: C.g3, width: 1.2, dash: '3 3' },
      ],
      thresholds: [{ y: d.thresholds.alert_probability, label: 'alert', color: C.red }],
      cursor: T[i] >= x[0] ? T[i] : null,
    });
    S.el.querySelector('#lv-cell-title').textContent = `${S.cell} · ${d.cell_meta[S.cell].band} · ${d.cell_meta[S.cell].site_id}`;
  }

  async function updateSnapshot() {
    const state = S, index = state.idx, cell = state.cell, request = ++snapshotSeq;
    try {
      const snap = await App.api(`/api/live/snapshot?seconds=${state.d.times[index]}&cell_id=${encodeURIComponent(cell)}`);
      if (S !== state || request !== snapshotSeq || S.idx !== index || S.cell !== cell) return;
      const selected = snap.selected, historical = snap.errors.historical;
      const mode = row => row.tier.startsWith('tier 0') ? 'Monitor only'
        : row.automatic_eligible ? 'Automatic tier; verify after 120 s'
        : row.human_approval_required ? 'Engineer approval required' : 'Fallback + NOC escalation';
      state.el.querySelector('#lv-now-kpis').innerHTML = [
        { label: 'Observed now', value: snap.observed_prb, unit: '%', sub: cell + ' at the replay cursor', red: false },
        { label: 'Historical forecast now', value: snap.historical_forecast, unit: '%', sub: 'Attendance model + historical hourly profile', red: false },
        { label: 'Current forecast error', value: historical.current, unit: 'pts', sub: `Observed minus historical forecast; MAE so far ${historical.mae} pts`, red: false },
        { label: 'Next 60 s forecast', value: snap.forecast_next_60s, unit: '%', sub: 'Uses only observations through this cursor', red: false },
        { label: '5-min adaptive MAE', value: snap.rolling_5min.mae, unit: 'pts', sub: `${snap.rolling_5min.n} matured predictions; unavailable until 60 s elapse`, red: false },
      ].map(App.kpiTile).join('');
      state.el.querySelector('#lv-routing-status').textContent = `${cell}: ${selected.routing_required
        ? selected.recommendation === 'fallback' ? 'Intervention needed, but no safe steering route.' : 'Routing required.'
        : 'No routing required.'} ${mode(selected)}. This is a current recommendation, not an executed action.`;
      state.el.querySelector('#lv-current-routing').innerHTML = `<thead><tr><th>Cell</th><th class="num">Observed UL</th><th>Risk</th><th>Route needed?</th><th>Target</th><th>Approval path</th><th>Projected relief sufficient?</th></tr></thead><tbody>
        ${snap.routing.map(row => `<tr class="clickable ${row.cell_id === cell ? 'selected' : ''}" data-cell="${row.cell_id}">
          <td><b>${App.esc(row.cell_id)}</b></td><td class="num">${row.observed_prb.toFixed(1)}%</td><td>${App.riskPill(row.risk_level)}</td>
          <td>${row.routing_required ? row.recommendation === 'fallback' ? 'No safe route' : 'Yes' : 'No'}</td><td>${App.esc(row.target_cell_id || '—')}</td>
          <td>${mode(row)}</td><td>${row.projected_relief_sufficient === null ? 'Not applicable' : row.projected_relief_sufficient ? 'Yes, subject to verification' : '<span class="delta-bad">No: review / escalation needed</span>'}</td></tr>`).join('')}</tbody>`;
      state.el.querySelectorAll('#lv-current-routing tr[data-cell]').forEach(row => row.addEventListener('click', () => {
        S.cell = row.dataset.cell; S.decision = null; tick(0, true);
      }));
      const c = snap.comparison;
      Charts.bars('#lv-action-compare', {
        labels: c.target_cell_id ? [cell, c.target_cell_id] : [cell], height: 190, max: 110,
        format: v => v.toFixed(1) + '%', series: [
          { name: 'Observed, no simulated move', values: c.target_cell_id ? [c.source_before, c.target_before] : [c.source_before], color: C.g3 },
          { name: 'Hypothetical after one action', values: c.target_cell_id ? [c.source_after, c.target_after] : [c.source_after], color: C.red }
        ], thresholds: [{ v: 85, label: '85% UL PRB', color: C.black }]
      });
      state.el.querySelector('#lv-now-trace').innerHTML = `<summary>Current decision trace for ${App.esc(cell)}</summary>
        <p><b>${App.esc(mode(selected))}</b> · ${App.esc(selected.recommendation)}</p>
        <ul class="trace">${selected.trace.map(row => `<li><span class="ic ${row.status}">${row.status === 'pass' ? '+' : '!'}</span><b>${App.esc(row.step)}</b><span>${App.esc(row.detail)}</span></li>`).join('')}</ul>`;
      state.el.querySelector('#lv-snapshot-time').textContent = `Snapshot @ ${fmtClock(snap.seconds, state.d.event_start)} · recomputed from recorded observations at this cursor`;
    } catch (e) {
      if (S === state && request === snapshotSeq) {
        state.el.querySelector('#lv-routing-status').textContent = 'Could not refresh current routing: ' + e.message;
        state.el.querySelector('#lv-current-routing').innerHTML = '';
        state.el.querySelector('#lv-now-kpis').innerHTML = '';
      }
    }
  }

  function updateFeed(i) {
    const T = S.d.times[i];
    const items = S.d.alerts.filter(a => a.t <= T);
    if (items.length === S.feedCount) return;
    S.feedCount = items.length;
    const feed = S.el.querySelector('#lv-feed');
    feed.innerHTML = items.slice(-80).reverse().map(a => `<div class="feed-item ${a.severity} ${a.decision_id ? 'clickable' : ''}" ${a.decision_id ? `data-d="${a.decision_id}"` : ''}>
      <span class="ft">${fmtClock(a.t, S.d.event_start)}</span><span>${App.esc(a.text)}</span></div>`).join('') || '<div class="empty">No alerts yet - network nominal</div>';
    feed.querySelectorAll('.feed-item.clickable').forEach(x => x.addEventListener('click', () => {
      S.decision = x.dataset.d;
      const decision = S.d.decisions.find(item => item.decision_id === S.decision);
      if (decision) S.cell = decision.cell_id;
      tick(0, true);
    }));
    S.el.querySelector('#lv-feed-count').textContent = `${items.length} alerts`;
  }

  function currentDecision() {
    const T = S.d.times[S.idx];
    const visible = S.d.decisions.filter(d => d.t <= T);
    if (S.decision) { const d = visible.find(x => x.decision_id === S.decision); if (d) return d; }
    return visible.filter(d => d.cell_id === S.cell).pop() || null;
  }

  function renderDecision(force) {
    const T = S.d.times[S.idx];
    const d = currentDecision();
    App.setAISelection({ seconds: T, cell_id: S.cell, decision_id: d ? d.decision_id : null });
    const box = S.el.querySelector('#lv-decision');
    const key = d ? d.decision_id + '|' + JSON.stringify(decisionState(d, T)) : 'none';
    if (!force && key === S.decKey) return;
    S.decKey = key;
    if (!d) { box.innerHTML = '<div class="empty">No recorded decision for this cell at this cursor. See current routing recommendations above.</div>'; return; }
    const st = decisionState(d, T);
    const chk = (d.checks && d.checks.chosen) || null;
    box.innerHTML = `
      <div class="decision-meta"><span class="pill ${d.risk_level}">${d.risk_level} risk</span><span class="pill ${App.tierClass(d.tier)}">${App.esc(d.tier)}</span>
        <span class="small muted">${App.esc(d.decision_id)} · ${App.esc(d.time)} · ${App.esc(d.phase)}</span></div>
      <div class="big-rec">${d.recommendation === 'steer_to_neighbor' ? `Steer uplink users ${App.esc(d.cell_id)} → ${App.esc(d.target_cell_id)}` : d.recommendation === 'fallback' ? `Fallback on ${App.esc(d.cell_id)} - no safe neighbour` : 'Monitor'}</div>
      <div class="states">${st.steps.map(x => `<div class="state ${x.s}">${App.esc(x.k)}</div>`).join('')}</div>
      <div class="small muted" style="margin-bottom:10px">Status: <b style="color:#000">${App.esc(st.status)}</b>
        ${d.projected && d.projected.source_prb_after !== undefined ? ` · projected source UL PRB ${d.projected.source_prb_after}%` : ''}${d.projected && d.projected.target_prb_after !== undefined ? `, target ${d.projected.target_prb_after}%` : ''}
        ${T >= d.verify_t && !st.pending && st.status !== 'rejected by operator' && d.verify && d.verify.result !== 'n/a' ? ` · simulated verify @${fmtClock(d.verify_t, S.d.event_start)}: source ${d.verify.source_prb_after}%, target ${d.verify.target_prb_after}%${d.verify.reason ? ' (' + App.esc(d.verify.reason) + ')' : ''}` : ''}</div>
      <h3>Backhaul-aware check trace</h3>
      <ul class="trace">${d.trace.map(t => `<li><span class="ic ${t.status}">${t.status === 'pass' ? '✓' : t.status === 'fail' ? '✕' : t.status === 'warn' ? '!' : 'i'}</span>
        <span class="st">${App.esc(t.step)}</span><span>${App.esc(t.detail)}</span></li>`).join('')}</ul>
      ${d.actions && d.actions.length ? `<div class="action-spec">${d.actions.map(a => `<div>${App.esc(a.parameter)}: ${a.old} → ${a.new} ${App.esc(a.unit)}${a.target ? ' (toward ' + App.esc(a.target) + ')' : ''}${a.note ? ' · ' + App.esc(a.note) : ''}</div>`).join('')}</div>` : ''}
      ${chk ? `<div class="foot-note">Moving ≈${chk.traffic_to_move_gbps} Gbps · target site ${App.esc(chk.target_site)} ${chk.site_util_before_pct}% → ${chk.site_util_after_pct}% · aggregation ${chk.aggregation_util_pct}%</div>` : ''}
      <div class="approve-row">${st.pending ? `<button class="btn primary" id="lv-approve">Approve steer</button><button class="btn" id="lv-reject">Reject</button>
        <span class="small muted">Tier 2 - engineer approval required. Decisions are written to the audit log.</span>`
        : S.approvals[d.decision_id] ? `<span class="small muted">${App.esc(S.approvals[d.decision_id].action)}d by ${App.esc(S.approvals[d.decision_id].user)} at ${App.esc(S.approvals[d.decision_id].ts)} (audit log)</span>`
        : `<span class="small muted">${d.tier.startsWith('tier 1') ? 'Tier 1 executes automatically and auto-reverts after 30 min.' : d.tier.startsWith('tier 3') ? 'Tier 3: NOC applies fallback actions and escalates.' : ''}</span>`}</div>`;
    const act = async (action) => {
      try {
        const r = await App.post(`/api/live/decisions/${encodeURIComponent(d.decision_id)}/${action}`, { user: 'exec-demo' });
        S.approvals[d.decision_id] = { action: r.action, ts: r.ts, user: r.user };
        S.listDirty = true;
        App.toast(`${d.decision_id} ${action}d - logged to audit trail`);
        renderDecision(true); renderDecisionList(); loadAudit();
      } catch (e) { App.toast('Could not record: ' + e.message); }
    };
    const a = box.querySelector('#lv-approve'), r = box.querySelector('#lv-reject');
    if (a) a.addEventListener('click', () => act('approve'));
    if (r) r.addEventListener('click', () => act('reject'));
  }

  function renderDecisionList() {
    const T = S.d.times[S.idx];
    const vis = S.d.decisions.filter(d => d.t <= T);
    const stateKey = vis.map(d => d.decision_id + ':' + decisionState(d, T).status).join('|');
    if (stateKey === S.listKey && !S.listDirty) return;
    S.listKey = stateKey;
    S.listCount = vis.length; S.listDirty = false;
    S.el.querySelector('#lv-dlist').innerHTML = `<thead><tr><th>Time</th><th>Cell</th><th>Action</th><th>Tier</th><th>Outcome</th></tr></thead><tbody>
      ${vis.slice().reverse().map(d => {
        const st = decisionState(d, T);
        return `<tr class="clickable" data-d="${d.decision_id}"><td class="nowrap">${App.esc(d.time)}</td><td>${App.esc(d.cell_id.replace('V01-', ''))}</td>
          <td>${d.recommendation === 'steer_to_neighbor' ? '→ ' + App.esc(d.target_cell_id.replace('V01-', '')) : App.esc(d.recommendation)}</td>
          <td><span class="pill ${App.tierClass(d.tier)}">${App.esc(d.tier.split(' - ')[0])}</span></td><td class="small">${App.esc(st.status)}</td></tr>`;
      }).join('') || '<tr><td colspan="5" class="empty">No decisions yet</td></tr>'}</tbody>`;
    S.el.querySelectorAll('#lv-dlist tr.clickable').forEach(tr => tr.addEventListener('click', () => {
      S.decision = tr.dataset.d; const d = S.d.decisions.find(x => x.decision_id === S.decision);
      if (d) { S.cell = d.cell_id; updateTiles(S.idx); updateCharts(S.idx); updateSnapshot(); }
      renderDecision(true);
    }));
  }

  function updateBackhaul(i) {
    const b = S.d.backhaul;
    S.el.querySelector('#lv-bh').innerHTML = b.links.map(l => {
      const v = b.util[l][i], agg = l.endsWith('AGG'), lim = agg ? 90 : 85;
      const cls = v >= lim ? 'hot' : v >= lim - 10 ? 'warm' : 'ok';
      return `<div class="lb"><span><b>${App.esc(l.replace('V01-', ''))}</b><div class="small muted">${agg ? 'aggregation' : 'site'} · ${b.capacity_gbps[l]} Gbps</div></span>
        <div class="track"><i class="${cls}" style="width:${Math.min(v, 100)}%"></i><span class="mark" style="left:${lim}%" title="limit ${lim}%"></span></div>
        <span class="right"><b>${v === null ? '–' : v.toFixed(0)}%</b></span></div>`;
    }).join('');
  }

  async function loadAudit() {
    try {
      const a = await App.api('/api/audit');
      const box = S && S.el.querySelector('#lv-audit');
      if (!box) return;
      box.innerHTML = a.entries.length ? `<table class="tbl"><thead><tr><th>Logged (UTC)</th><th>Decision</th><th>Action</th><th>User</th><th>Cell</th><th>Tier</th></tr></thead><tbody>
        ${a.entries.slice(0, 12).map(r => `<tr><td class="nowrap">${App.esc(r.ts)}</td><td>${App.esc(r.decision_id)}</td><td><span class="pill ${r.action === 'approve' ? 'ok' : 'high'}">${App.esc(r.action)}</span></td>
          <td>${App.esc(r.user)}</td><td>${App.esc(r.cell_id)}</td><td>${App.esc(r.tier)}</td></tr>`).join('')}</tbody></table>` : '<div class="empty">No approvals recorded yet.</div>';
    } catch (e) {
      if (S) S.el.querySelector('#lv-audit').textContent = 'Could not load approval audit: ' + e.message;
    }
  }

  function tick(steps, force) {
    const n = S.d.times.length;
    S.acc += steps;
    const adv = Math.floor(S.acc); S.acc -= adv;
    if (!adv && !force) return;
    S.idx = Math.min(n - 1, S.idx + adv);
    if (S.idx >= n - 1) setPlaying(false);
    const T = S.d.times[S.idx];
    S.el.querySelector('#lv-clock').textContent = fmtClock(T, S.d.event_start);
    S.el.querySelector('#lv-rel').textContent = relTime(T) + ' from kick-off';
    S.el.querySelector('#lv-phase').textContent = S.d.phases[S.idx];
    S.el.querySelector('#lv-scrub').value = S.idx;
    updateTiles(S.idx); updateCharts(S.idx); updateFeed(S.idx); updateBackhaul(S.idx);
    renderDecision(false); renderDecisionList();
    updateSnapshot();
  }

  function setPlaying(p) {
    S.playing = p;
    const b = S.el.querySelector('#lv-play');
    if (b) b.textContent = p ? '❚❚ Pause' : '▶ Play';
  }

  App.views.live = {
    async render(el, params) {
      const [d, evs] = await Promise.all([App.api('/api/live'), App.api('/api/events')]);
      if (!el.isConnected || App.current !== 'live') return;
      const ev = evs.events.find(e => e.event_id === d.event_id) || {};
      S = { el, d, idx: 0, acc: 0, playing: false, speed: 30, cell: null, decision: null, approvals: d.approvals || {}, feedCount: -1, listCount: -1 };
      const firstWarn = {}, firstCong = {};
      d.alerts.forEach(a => {
        if (a.severity === 'warning' && firstWarn[a.cell] === undefined) firstWarn[a.cell] = a.t;
        if (a.severity === 'critical' && !a.decision_id && firstCong[a.cell] === undefined) firstCong[a.cell] = a.t;
      });
      const leads = Object.keys(firstCong).filter(c => firstWarn[c] !== undefined && firstWarn[c] <= firstCong[c]).map(c => firstCong[c] - firstWarn[c]);
      const nTier = k => d.decisions.filter(x => x.tier.startsWith(k)).length;
      const hotCell = d.cells.slice().sort((a, b) => Math.max(...d.series[b].prb) - Math.max(...d.series[a].prb))[0];
      S.cell = hotCell;
      const first = d.decisions.length ? d.times.indexOf(d.decisions[0].t) : 0;
      S.idx = Math.max(0, first - 30);
      if (params && params[0] !== undefined && params[0] !== '' && !Number.isNaN(+params[0])) {
        const k = d.times.findIndex(t => t >= +params[0]); if (k >= 0) S.idx = k;
      }
      const autoplay = !(params && params[1] === 'paused');
      const take = `Across this replay the engine makes ${d.decisions.length} routing decisions: ${nTier('tier 1')} automatic, ${nTier('tier 2')} for engineer approval, ${nTier('tier 3')} fallbacks` +
        (leads.length ? ` - early warnings arrived a median ${Math.round(leads.sort((a, b) => a - b)[Math.floor(leads.length / 2)])} s before congestion.` : '.');
      el.innerHTML = `
        ${App.head(`Synthetic streaming replay · ${d.event_id}`, `${ev.event_name || 'Live event'} · ${ev.venue_name || ''}`, take)}
        <div class="replay-notice"><b>Recorded synthetic data, not a live Verizon feed.</b> One sample represents 15 seconds.
          Forecasts use historical event shape plus observations available at the cursor. Routing and action outcomes are simulated.</div>
        <div class="live-head card" style="padding:14px 20px">
          <div style="display:flex;align-items:baseline;gap:18px"><div class="clock" id="lv-clock"></div>
            <div><div class="phase" id="lv-phase"></div><div class="small muted" id="lv-rel"></div></div></div>
          <div class="player">
            <button class="btn dark" id="lv-play">▶ Play</button>
            <div class="seg" id="lv-speed">${SPEEDS.map(s => `<button data-s="${s}" class="${s === S.speed ? 'on' : ''}">${s}×</button>`).join('')}</div>
            <input type="range" id="lv-scrub" min="0" max="${d.times.length - 1}" value="${S.idx}" aria-label="Replay position">
            <button class="btn ghost" id="lv-kick">Kick-off</button>
            <button class="btn ghost" id="lv-next">Next alert →</button>
            <button class="btn ghost" id="lv-congestion">Next congestion</button>
            <button class="btn ghost" id="lv-human">Next human approval</button>
          </div>
        </div>
        <div class="cells">${d.cells.map(tileHTML).join('')}</div>
        <div class="kpis" id="lv-now-kpis"></div>
        <div class="card live-current"><div class="card-head"><div><h2>Routing required right now?</h2><div class="card-sub" id="lv-snapshot-time"></div></div></div>
          <p class="takeaway" id="lv-routing-status" role="status">Evaluating radio and backhaul headroom...</p>
          <div class="scroll"><table class="tbl" id="lv-current-routing"></table></div>
          <div class="grid g2"><details id="lv-now-trace"></details><div><h3>Compare no action vs one proposed action</h3><div id="lv-action-compare"></div>
            <div class="foot-note">Hypothetical single-decision projection only. Does not execute a move or combine simultaneous proposals. Recorded observations are unchanged.</div></div></div></div>
        <div class="grid g-2-1">
          <div class="card"><div class="card-head"><div><h2>Forecast vs actual</h2><div class="card-sub" id="lv-cell-title"></div></div>
            <div class="small muted">Errors above use only elapsed samples. Green forecasts were issued 60 s earlier. Red is the next 60 s estimate. Click a cell tile to switch.</div></div>
            <div id="lv-main" class="chart-box"></div><div id="lv-risk" class="chart-box" style="margin-top:6px"></div></div>
          <div class="card"><div class="card-head"><h2>Alerts</h2><span class="small muted" id="lv-feed-count"></span></div><div class="feed" id="lv-feed" style="max-height:420px"></div></div>
        </div>
        <div class="grid g-3-2">
          <div class="card"><div class="card-head"><h2>Recommended action</h2><span class="small muted">click any alert or decision to inspect</span></div><div id="lv-decision"></div></div>
          <div>
            <div class="card" style="margin-bottom:20px"><div class="card-head"><div><h2>Backhaul now</h2><div class="card-sub">Site links ≤85% after move · aggregation ≤90%</div></div></div><div class="linkbars" id="lv-bh"></div></div>
            <div class="card"><div class="card-head"><h2>Decision log</h2></div><div class="scroll" style="max-height:300px"><table class="tbl" id="lv-dlist"></table></div></div>
          </div>
        </div>
        <div class="card"><div class="card-head"><div><h2>Approval audit trail</h2><div class="card-sub">Append-only log (audit/approvals.jsonl)</div></div></div><div id="lv-audit"></div></div>`;
      el.querySelectorAll('.cell-tile').forEach(t => t.addEventListener('click', () => { S.cell = t.dataset.c; S.decision = null; tick(0, true); }));
      el.querySelector('#lv-play').addEventListener('click', () => setPlaying(!S.playing));
      el.querySelectorAll('#lv-speed button').forEach(b => b.addEventListener('click', () => {
        S.speed = +b.dataset.s; el.querySelectorAll('#lv-speed button').forEach(x => x.classList.toggle('on', x === b));
      }));
      el.querySelector('#lv-scrub').addEventListener('input', e => { S.idx = +e.target.value; S.feedCount = -1; S.listCount = -1; S.decision = null; tick(0, true); });
      el.querySelector('#lv-kick').addEventListener('click', () => {
        S.idx = Math.max(0, d.times.findIndex(t => t >= 0)); S.feedCount = -1; S.listCount = -1; S.decision = null; tick(0, true);
      });
      el.querySelector('#lv-next').addEventListener('click', () => {
        const T = d.times[S.idx];
        const nx = d.alerts.find(a => a.t > T);
        if (nx) { S.idx = d.times.indexOf(nx.t); S.decision = nx.decision_id || null; if (nx.cell) S.cell = nx.cell; tick(0, true); }
      });
      const jumpTo = item => {
        if (!item) { App.toast('No further matching event in this recording.'); return; }
        setPlaying(false); S.idx = d.times.indexOf(item.t); S.decision = item.decision_id || null;
        if (item.cell || item.cell_id) S.cell = item.cell || item.cell_id;
        S.acc = 0; tick(0, true);
      };
      el.querySelector('#lv-congestion').addEventListener('click', () => {
        jumpTo(d.alerts.find(a => a.t > d.times[S.idx] && a.severity === 'critical' && !a.decision_id));
      });
      el.querySelector('#lv-human').addEventListener('click', () => {
        jumpTo(d.decisions.find(item => item.t > d.times[S.idx] && item.tier.startsWith('tier 2')));
      });
      tick(0, true);
      loadAudit();
      setPlaying(autoplay);
      App.every(250, () => { if (S && S.playing) tick(S.speed * 0.25 / 15); });
    },
    beforeAI() { if (S) setPlaying(false); },
    destroy() { snapshotSeq++; S = null; }
  };
})();
