/* 02 Event forecast */
(function () {
  'use strict';
  const C = Charts.colors;
  let state = { venue: 'all', risk: 'all', selected: null };

  const SIG_LABELS = {
    home_crowd_multiplier: ['Home-crowd factor', v => v.toFixed(2) + '×'],
    ticket_sell_through_pct: ['Ticket sell-through', v => v.toFixed(0) + '%'],
    social_interest_index: ['Social interest', v => v.toFixed(0)],
    social_mentions_7d: ['Mentions (7 d)', v => App.short(v)],
    search_trend_index: ['Search trend', v => v.toFixed(0)],
    ticket_resale_price_index: ['Resale price index', v => v.toFixed(2)],
    anchor_team_win_pct_to_date: ['Team win %', v => (v * 100).toFixed(0) + '%'],
    standings_position: ['Standings', v => '#' + v],
    competing_events_within_10mi: ['Competing events', v => String(v)],
    transit_advisory: ['Transit advisory', v => v ? 'Yes' : 'No'],
    road_closure_nearby: ['Road closure', v => v ? 'Yes' : 'No'],
    weather_temp_f: ['Temperature', v => v.toFixed(0) + '°F'],
    weather_condition: ['Weather', v => v],
    rivalry_flag: ['Rivalry', v => v ? 'Yes' : 'No'],
    estimate_confidence: ['Estimate confidence', v => (v * 100).toFixed(0) + '%'],
    signal_confidence: ['Signal confidence', v => (v * 100).toFixed(0) + '%'],
  };

  function filtered(evs) {
    return evs.filter(e => (state.venue === 'all' || e.venue_id === state.venue) && (state.risk === 'all' || e.risk === state.risk));
  }

  function rangeBar(lo, pt, hi, max) {
    return `<div class="range-bar" title="${App.num(lo)} – ${App.num(hi)}"><div class="rng" style="left:${lo / max * 100}%;width:${Math.max((hi - lo) / max * 100, 0.5)}%"></div><div class="pt" style="left:calc(${pt / max * 100}% - 1px)"></div></div>`;
  }

  async function renderDetail(el, id) {
    const box = el.querySelector('#fc-detail');
    box.innerHTML = '<div class="loading">Loading event…</div>';
    const e = await App.api('/api/events/' + encodeURIComponent(id));
    if (!box.isConnected || App.current !== 'forecast') return;
    App.setAISelection({ event_id: e.event_id });
    const sig = e.signals || {};
    box.innerHTML = `
      <div class="card" style="margin-bottom:20px">
        <div class="card-head"><div>
          <div class="eyebrow" style="margin-bottom:4px">${App.esc(e.start)} · ${App.esc(e.venue_name)}, ${App.esc(e.city)}</div>
          <h2 style="font-size:22px">${App.esc(e.event_name)}</h2></div>
          <div class="toolbar">${App.haPill(e.home_away)} ${App.riskPill(e.risk)} <a class="btn ghost" style="text-decoration:none" href="#/simulator/${encodeURIComponent(e.event_id)}">What-if →</a></div></div>
        <div class="kpis" style="margin:8px 0 0">
          <div class="kpi compact"><div class="kpi-label">Attendance forecast</div><div class="kpi-value">${App.short(e.attendance_pred)}</div>
            <div class="kpi-sub">90% band ${App.short(e.attendance_low)} – ${App.short(e.attendance_high)} · capacity ${App.short(e.capacity)}</div></div>
          <div class="kpi compact"><div class="kpi-label">Organiser estimate</div><div class="kpi-value">${App.short(e.expected_attendance)}</div>
            <div class="kpi-sub">${App.esc(e.subtype)} ${App.esc(e.category)}</div></div>
          <div class="kpi compact"><div class="kpi-label">Max peak UL PRB</div><div class="kpi-value ${e.peak_prb_max >= 85 ? 'red' : ''}">${e.peak_prb_max.toFixed(0)}<span class="u">%</span></div>
            <div class="kpi-sub">P(any cell &gt;85%) = ${(e.p_over_85 * 100).toFixed(0)}%${e.provided_forecast_peak !== null ? ` · incumbent v4.1: ${e.provided_forecast_peak.toFixed(0)}%` : ''}</div></div>
          <div class="kpi compact"><div class="kpi-label">Cells needing a plan</div><div class="kpi-value ${e.hot_cells ? 'red' : ''}">${e.hot_cells}<span class="u">/ ${e.cells.length}</span></div>
            <div class="kpi-sub">${e.watch_cells} cells forecast ≥70% (watch)</div></div>
        </div>
      </div>
      <div class="grid g-3-2">
        <div class="card"><div class="card-head"><div><h2>Cell × hour uplink risk</h2>
          <div class="card-sub">Forecast hourly peak UL PRB % per cell, 6 h before to 6 h after start · hover for congestion probability</div></div>
          <div class="legend-inline"><span><i style="background:${Charts.heatColor(40, 0, 85, 100)}"></i>&lt;70%</span><span><i style="background:${Charts.heatColor(85, 0, 85, 100)}"></i>85%</span><span><i style="background:${C.red}"></i>100%</span></div></div>
          <div id="fc-heat" class="chart-box"></div></div>
        <div class="card"><div class="card-head"><div><h2>Context signals</h2><div class="card-sub">Pre-event inputs to the attendance model</div></div></div>
          <div class="sig-grid">${Object.keys(SIG_LABELS).filter(k => sig[k] !== null && sig[k] !== undefined).map(k =>
            `<div class="sig"><div class="k">${SIG_LABELS[k][0]}</div><div class="v">${App.esc(SIG_LABELS[k][1](sig[k]))}</div></div>`).join('')}</div></div>
      </div>
      <div class="grid g2">
        <div class="card"><div class="card-head"><div><h2>Peak forecast per cell</h2><div class="card-sub">Point forecast and 90% prediction interval (grey) · threshold 85%</div></div></div>
          <table class="tbl"><thead><tr><th>Cell</th><th class="num">Forecast</th><th style="width:45%">Interval</th><th class="num">P(&gt;85%)</th></tr></thead><tbody>
          ${e.cells.map(c => `<tr><td><b>${App.esc(c.cell_id)}</b></td><td class="num" style="color:${c.pred >= 85 ? C.red : 'inherit'};font-weight:700">${c.pred.toFixed(0)}%</td>
            <td>${rangeBar(c.low, c.pred, c.high, 100)}<div class="small muted">${c.low.toFixed(0)}–${c.high.toFixed(0)}%</div></td>
            <td class="num">${(c.p_over_85 * 100).toFixed(0)}%</td></tr>`).join('')}</tbody></table></div>
        <div class="card"><div class="card-head"><div><h2>Most similar past events</h2><div class="card-sub">kNN on context (category, home/away, demand signals) weighted by venue similarity</div></div></div>
          <table class="tbl"><thead><tr><th>Date</th><th>Venue</th><th>Crowd</th><th class="num">Attendance</th><th class="num">Peak UL</th><th class="num">Cong. min</th><th class="num">Sim.</th></tr></thead><tbody>
          ${(e.similar || []).map(s => `<tr><td class="nowrap">${App.esc(s.date)}</td><td><b>${App.esc(s.venue_name)}</b><div class="small muted">${App.esc(s.event_name)}</div></td>
            <td>${App.haPill(s.home_away)}</td><td class="num">${App.num(s.actual_attendance)}</td>
            <td class="num" style="color:${s.peak_ul_prb >= 85 ? C.red : 'inherit'}">${s.peak_ul_prb === null ? '–' : s.peak_ul_prb.toFixed(0) + '%'}</td>
            <td class="num">${s.congestion_min === null ? '–' : App.num(s.congestion_min)}</td><td class="num">${s.similarity.toFixed(2)}</td></tr>`).join('') || '<tr><td colspan="7" class="empty">No comparable completed events</td></tr>'}</tbody></table>
          ${(e.similar_file || []).length ? `<div class="foot-note">Reference list (file 11) top match: ${App.esc(e.similar_file[0].event_id)} at ${App.esc(e.similar_file[0].venue_id)} (${App.esc(e.similar_file[0].date)}, score ${e.similar_file[0].similarity})</div>` : ''}</div>
      </div>`;
    const hm = e.heatmap;
    Charts.heatmap('#fc-heat', {
      rows: hm.rows.map(r => r.cell_id), cols: hm.hours.map((h, i) => (hm.hour_rel[i] === 0 ? '▶ ' : '') + h),
      values: hm.rows.map(r => r.values), height: Math.max(40 * hm.rows.length + 34, 200), min: 0, mid: 85, max: 100,
      format: v => v.toFixed(0), valueLabel: 'Peak UL PRB %',
      extra: (i, j) => `<div>P(congestion) ≈ ${(hm.rows[i].risk[j] * 100).toFixed(0)}%</div><div>${hm.hour_rel[j] >= 0 ? '+' : ''}${hm.hour_rel[j]} h from start</div>`
    });
  }

  App.views.forecast = {
    async render(el, params) {
      const d = await App.api('/api/events');
      if (!el.isConnected || App.current !== 'forecast') return;
      if (params[0] === 'venue' && params[1]) { state.venue = params[1]; state.selected = null; }
      else if (params[0]) { state.selected = params[0]; state.venue = 'all'; state.risk = 'all'; }
      const venues = [...new Map(d.events.map(e => [e.venue_id, e.venue_name])).entries()].sort();
      el.innerHTML = `
        ${App.head('Event forecast · next 30 days', 'Which events will stress the uplink - and where', d.takeaway)}
        <div class="card" style="margin-bottom:20px">
          <div class="card-head"><div><h2>Upcoming events</h2><div class="card-sub">Attendance forecast with 90% band (ridge + similar-event kNN) · peak uplink per cell (log-ridge, interval from residuals) · click a row</div></div>
            <div class="toolbar">
              <select id="fc-venue"><option value="all">All venues</option>${venues.map(([id, n]) => `<option value="${id}" ${state.venue === id ? 'selected' : ''}>${App.esc(n)}</option>`).join('')}</select>
              <div class="seg" id="fc-risk">${['all', 'high', 'medium', 'low'].map(r => `<button data-r="${r}" class="${state.risk === r ? 'on' : ''}">${r}</button>`).join('')}</div>
            </div></div>
          <div class="scroll"><table class="tbl" id="fc-table"></table></div>
        </div>
        <div id="fc-detail"></div>`;
      const draw = () => {
        const rows = filtered(d.events);
        if (!rows.find(e => e.event_id === state.selected)) state.selected = rows.length ? (rows.find(e => e.risk === 'high') || rows[0]).event_id : null;
        const maxA = Math.max(...d.events.map(e => e.capacity));
        el.querySelector('#fc-table').innerHTML = `<thead><tr><th>Start</th><th>Venue</th><th>Event</th><th>Crowd</th><th class="num">Attendance fcst</th><th style="width:150px">90% band</th>
          <th class="num">Peak UL PRB</th><th class="num">Hot cells</th><th>Risk</th></tr></thead><tbody>
          ${rows.map(e => `<tr class="clickable ${e.event_id === state.selected ? 'selected' : ''}" data-id="${e.event_id}">
            <td class="nowrap">${App.esc(e.start)}${e.status === 'in_progress' ? ' <span class="pill high">live</span>' : ''}</td>
            <td class="nowrap"><b>${App.esc(e.venue_name)}</b></td><td>${App.esc(e.event_name)}</td><td>${App.haPill(e.home_away)}</td>
            <td class="num"><b>${App.num(e.attendance_pred)}</b></td><td>${rangeBar(e.attendance_low, e.attendance_pred, e.attendance_high, maxA)}</td>
            <td class="num" style="color:${e.peak_prb_max >= 85 ? C.red : 'inherit'};font-weight:700">${e.peak_prb_max.toFixed(0)}%</td>
            <td class="num">${e.hot_cells}</td><td>${App.riskPill(e.risk)}</td></tr>`).join('') || '<tr><td colspan="9" class="empty">No events match the filter</td></tr>'}</tbody>`;
        el.querySelectorAll('#fc-table tr.clickable').forEach(tr => tr.addEventListener('click', () => {
          state.selected = tr.dataset.id;
          el.querySelectorAll('#fc-table tr').forEach(x => x.classList.toggle('selected', x.dataset.id === state.selected));
          renderDetail(el, state.selected).catch(err => { el.querySelector('#fc-detail').innerHTML = `<div class="error-box">${App.esc(err.message)}</div>`; });
        }));
        if (state.selected) return renderDetail(el, state.selected);
        el.querySelector('#fc-detail').innerHTML = '';
      };
      el.querySelector('#fc-venue').addEventListener('change', ev => { state.venue = ev.target.value; draw(); });
      el.querySelectorAll('#fc-risk button').forEach(b => b.addEventListener('click', () => {
        state.risk = b.dataset.r; el.querySelectorAll('#fc-risk button').forEach(x => x.classList.toggle('on', x === b)); draw();
      }));
      await draw();
    }
  };
})();
