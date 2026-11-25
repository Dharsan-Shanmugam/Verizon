/* 01 Executive overview */
(function () {
  'use strict';
  const C = Charts.colors;
  const APP_COLORS = [C.red, C.black, C.amber, C.g4, C.darkred, C.g3, '#4B4D4F', C.g2];

  App.views.overview = {
    async render(el) {
      const d = await App.api('/api/overview');
      if (!el.isConnected || App.current !== 'overview') return;
      const g = d.growth;
      el.innerHTML = `
        ${App.head('Executive overview', 'Stadium uplink: predict it, route it safely, prove the value', d.takeaway)}
        <div class="kpis six">${d.kpis.map(App.kpiTile).join('')}</div>
        <div class="grid g-3-2">
          <div class="card"><div class="card-head"><h2>Three things to know</h2></div>
            <div class="headlines">${d.headlines.map(x => `<div class="headline"><div>${App.esc(x)}</div></div>`).join('')}</div></div>
          <div class="card dark" style="display:flex;flex-direction:column;justify-content:space-between">
            <div><div class="kpi-label" style="color:#A7A7A7">Synthetic replay · ${App.esc(d.live.event_id)}</div>
              <div style="font-size:15px;margin-top:6px;color:#D8DADA">Stream recorded observations, measure forecast error, and inspect routing approval tiers</div></div>
            <div style="display:flex;gap:36px;margin-top:18px;align-items:flex-end;flex-wrap:wrap">
              <div><div style="font-size:44px;font-weight:700;line-height:1">${d.live.decisions}</div><div class="small" style="color:#A7A7A7;margin-top:6px">routing decisions</div></div>
              <div><div style="font-size:44px;font-weight:700;line-height:1;color:#EE0000">${d.live.alerts}</div><div class="small" style="color:#A7A7A7;margin-top:6px">alerts raised</div></div>
              <a class="btn primary nowrap" style="margin-left:auto;text-decoration:none" href="#/live">Open command center →</a>
            </div></div>
        </div>
        <div class="grid g2">
          <div class="card"><div class="card-head"><div><h2>Uplink demand per event, ${g[0].year}–${g[g.length - 1].year}</h2>
            <div class="card-sub">Uplink GB per event across 13 venues · download-to-upload ratio ${g[0].dl_ul_ratio}:1 → ${g[g.length - 1].dl_ul_ratio}:1</div></div></div>
            <div id="ov-growth" class="chart-box"></div></div>
          <div class="card"><div class="card-head"><div><h2>What fans upload</h2>
            <div class="card-sub">Share of venue uplink by application group, per year</div></div></div>
            <div id="ov-apps" class="chart-box"></div></div>
        </div>
        <div class="card"><div class="card-head"><div><h2>Venue risk board · next 30 days</h2>
            <div class="card-sub">Forecast peak UL PRB per cell for every scheduled event · ranked by cells needing a plan · click a venue to drill in</div></div>
            <div class="legend-inline"><span><span class="dot high"></span>high</span><span><span class="dot medium"></span>watch</span><span><span class="dot low"></span>low</span></div></div>
          <div class="toolbar board-filters">
            <label>Venue <select id="ov-venue"><option value="all">All venues</option>${d.venue_board.map(v => `<option value="${v.venue_id}">${App.esc(v.venue_name)}</option>`).join('')}</select></label>
            <label>Risk <select id="ov-risk">${['all', 'high', 'medium', 'low', 'none'].map(r => `<option value="${r}">${r}</option>`).join('')}</select></label>
            <label>Sort <select id="ov-sort"><option value="risk">Highest risk first</option><option value="events">Most events</option><option value="name">Venue name</option></select></label>
            <button class="btn ghost" id="ov-reset">Reset filters</button>
          </div><p class="small muted" id="ov-summary" role="status"></p>
          <div class="scroll"><table class="tbl" id="ov-board"></table></div>
          <div class="foot-note">Filters apply to the risk board only. KPI tiles, growth and headlines remain portfolio-wide. Click an event dot to open its forecast.</div></div>`;
      Charts.bars('#ov-growth', {
        labels: g.map(r => String(r.year)), values: g.map(r => r.ul_gb_per_event), height: 270, name: 'UL GB per event',
        colors: g.map((r, i) => i === g.length - 1 ? C.red : C.black), valueLabels: true, format: v => App.num(v)
      });
      const am = d.app_mix;
      Charts.stackedBar('#ov-apps', {
        labels: am.years.map(String), height: 270, max: 100, format: v => v.toFixed(0) + '%', legend: true,
        series: am.apps.map((a, i) => ({ name: a, values: am.share[a], color: APP_COLORS[i % APP_COLORS.length] }))
      });
      const drawBoard = () => {
      const venue = el.querySelector('#ov-venue').value, risk = el.querySelector('#ov-risk').value;
      const sort = el.querySelector('#ov-sort').value;
      const board = d.venue_board.filter(v => (venue === 'all' || v.venue_id === venue) && (risk === 'all' || v.risk === risk));
      if (sort === 'events') board.sort((a, b) => b.events_30d - a.events_30d);
      if (sort === 'name') board.sort((a, b) => a.venue_name.localeCompare(b.venue_name));
      el.querySelector('#ov-summary').textContent = `${board.length} venues / ${board.reduce((sum, v) => sum + v.events_30d, 0)} events / ${board.reduce((sum, v) => sum + v.high_risk_events, 0)} high-risk events in this selection`;
      App.setAISelection({ venue, risk });
      const maxPk = 100;
      el.querySelector('#ov-board').innerHTML = `<thead><tr><th>Venue</th><th>Next event</th><th class="num">Events</th>
          <th class="num">High-risk events</th><th class="num">Hot cells (worst event)</th><th>Max peak UL PRB</th><th>Next 30 days</th><th>Risk</th></tr></thead><tbody>
        ${board.map(v => `<tr class="clickable" data-v="${v.venue_id}">
          <td class="nowrap"><b>${App.esc(v.venue_name)}</b>${v.is_target ? ' <span class="pill home" style="margin-left:4px;font-size:9px;padding:2px 6px">target</span>' : ''}<div class="small muted">${App.esc(v.city)}, ${App.esc(v.state)} · ${App.short(v.seats)} seats</div></td>
          <td><b>${App.esc(v.next_event || '—')}</b><div class="small muted">${App.esc(v.next_start || '')}</div></td>
          <td class="num">${v.events_30d}</td><td class="num"><b>${v.high_risk_events}</b></td><td class="num"><b style="color:${v.max_hot_cells ? '#EE0000' : 'inherit'}">${v.max_hot_cells}</b><span class="muted"> / ${v.cells}</span></td>
          <td style="min-width:130px"><div style="display:flex;gap:8px;align-items:center"><div class="inline-bar" style="flex:1"><i class="${v.risk}" style="width:${(v.max_peak_prb || 0) / maxPk * 100}%"></i></div><span class="small nowrap">${v.max_peak_prb === null ? '–' : v.max_peak_prb.toFixed(0) + '%'}</span></div>
</td>
          <td><div class="timeline-dots">${v.timeline.map(t => `<a href="#/forecast/${encodeURIComponent(t.event_id)}" aria-label="${App.esc(t.name + ' ' + t.start)}" title="${App.esc(t.start + ' · ' + t.name + ' · ' + t.risk)}" style="background:${t.risk === 'high' ? C.red : t.risk === 'medium' ? C.amber : C.green}"></a>`).join('')}</div></td>
          <td>${App.riskPill(v.risk === 'medium' ? 'medium' : v.risk)}</td></tr>`).join('') || '<tr><td colspan="8" class="empty">No venues match these filters.</td></tr>'}</tbody>`;
      el.querySelectorAll('#ov-board tr.clickable').forEach(tr => tr.addEventListener('click', event => { if (!event.target.closest('a')) location.hash = '#/forecast/venue/' + tr.dataset.v; }));
      };
      ['#ov-venue', '#ov-risk', '#ov-sort'].forEach(id => el.querySelector(id).addEventListener('change', drawBoard));
      el.querySelector('#ov-reset').addEventListener('click', () => {
        el.querySelector('#ov-venue').value = el.querySelector('#ov-risk').value = 'all';
        el.querySelector('#ov-sort').value = 'risk'; drawBoard();
      });
      drawBoard();
    }
  };
})();
