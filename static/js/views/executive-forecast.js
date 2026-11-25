(function () {
  'use strict';
  let generation = 0, selection = 0;
  const cellColors = ['#142f46', '#4667c9', '#b43755', '#087968', '#a86b19', '#8261b6', '#168baf', '#60738c'];
  const prioritySummaries = {
    radio_headroom: 'Prepare bounded traffic steering to protect uplink and user throughput.',
    backhaul_capacity: 'Validate backhaul headroom and alternate paths for uplink traffic.',
    tdd_slots: 'Review uplink time slots, neighbor alignment and downlink headroom.',
    baseline: 'Baseline uplink throughput, user throughput and PRB utilization.'
  };
  App.views.forecast = {
    async render(el, params) {
      const version = ++generation;
      const [catalog, report] = await Promise.all([App.api('/api/events'), App.api('/api/operations/model')]);
      if (!el.isConnected || version !== generation) return;
      const events = catalog.events;
      let chartMetric = 'prb', currentPlan = null;
      let selected = params[0] || 'EVT-LIVE-20261028';
      if (!events.some(event => event.event_id === selected)) selected = events[0].event_id;
      el.innerHTML = `<div class="page-title"><div><div class="eyebrow">01 / EVENT OUTLOOK</div>
        <h1>Protect the experience. Plan ahead.</h1><p class="subline">Demand exposure and the next best planning decisions.</p></div>
        <button class="btn" id="forecast-evidence">Confidence & evidence &nearr;</button></div>
        <section class="executive-brief" id="plan-brief" aria-live="polite"></section>
        <section class="metrics" id="forecast-metrics"></section>
        <div class="forecast-layout"><section class="panel"><div class="panel-head"><h2>Event portfolio</h2>${Exec.pill(events.length + ' EVENTS')}</div>
          <select class="filter-select" id="venue-filter" aria-label="Filter by venue"><option value="all">All venues</option>
          ${[...new Map(events.map(e => [e.venue_id, e.venue_name])).entries()].map(([id, name]) => `<option value="${App.esc(id)}">${App.esc(name)}</option>`).join('')}</select>
          <div class="event-list" id="event-list"></div></section>
          <section class="panel" id="event-focus"><div class="loading">Building issue & remedy recommendations...</div></section></div>`;
      el.querySelector('#forecast-evidence').onclick = () => Exec.evidence(report);
      const drawChart = () => {
        const plan = currentPlan, e = plan.event;
        const profiles = plan.five_minute_profile.cells;
        const totalPeak = Math.max(...plan.five_minute_profile.seconds.map((_, i) =>
          profiles.reduce((sum, row) => sum + row.aggregate_ul_mbps_scenario[i], 0)));
        const metrics = [
          { id: 'turnout', label: 'Expected turnout', value: App.short(e.attendance_pred), unit: 'fans',
            detail: `${App.short(e.attendance_low)} - ${App.short(e.attendance_high)} prediction range` },
          { id: 'uplink', label: 'Uplink throughput', value: totalPeak.toFixed(1), unit: 'Mbps',
            detail: 'Scenario / peak total across venue-serving cells' },
          { id: 'prb', label: 'Uplink PRB utilization', value: e.peak_prb_max.toFixed(1), unit: '%',
            detail: 'Highest predicted cell peak / 85% planning line' },
          { id: 'devices', label: 'Connected devices', value: plan.connected_devices_est.toLocaleString('en-US'), unit: 'devices',
            detail: 'Event-level estimate / not a live count' }
        ];
        el.querySelector('#forecast-metrics').innerHTML = metrics.map(m => `<button type="button"
          class="metric forecast-metric ${m.id === chartMetric ? 'selected' : ''}" data-chart-metric="${m.id}"
          aria-pressed="${m.id === chartMetric}" aria-controls="prework-chart">
          <span class="metric-label">${m.label}</span><span class="metric-value">${m.value}<small>${m.unit}</small></span>
          <span class="metric-detail">${m.detail}</span><span class="metric-chart-hint">${m.id === chartMetric ? 'Showing in chart' : 'View chart'} &nearr;</span></button>`).join('');
        el.querySelectorAll('[data-chart-metric]').forEach(button => button.onclick = () => {
          chartMetric = button.dataset.chartMetric; drawChart();
          el.querySelector(`[data-chart-metric="${chartMetric}"]`).focus({ preventScroll: true });
        });
        const title = el.querySelector('#forecast-chart-title'), note = el.querySelector('#forecast-chart-note');
        const legend = el.querySelector('.forecast-cell-legend');
        const eventLevel = chartMetric === 'turnout' || chartMetric === 'devices';
        legend.hidden = eventLevel;
        el.querySelector('.all-cell-forecast .panel-head > .pill').hidden = eventLevel;
        if (chartMetric === 'devices') {
          title.textContent = 'Estimated connected devices';
          el.querySelector('#forecast-chart-subtitle').textContent = 'Selected event / device count';
          note.textContent = plan.connected_devices_method;
          Charts.bars('#prework-chart', { height: 300, labels: ['Connected devices'],
            values: [plan.connected_devices_est], color: '#08777b', legend: false,
            ariaLabel: 'Estimated number of connected devices for the selected event' });
          return;
        }
        if (chartMetric === 'turnout') {
          title.textContent = 'Expected event turnout';
          el.querySelector('#forecast-chart-subtitle').textContent = 'Event-level prediction / fans';
          note.textContent = 'Turnout is an event-level forecast, not a per-cell or time-varying attendance forecast.';
          Charts.bars('#prework-chart', { height: 300, labels: ['Lower estimate', 'Expected', 'Upper estimate'],
            values: [e.attendance_low, e.attendance_pred, e.attendance_high],
            colors: ['#8192d7', '#08777b', '#5265c7'], legend: false,
            ariaLabel: 'Expected event attendance with lower and upper prediction estimates' });
          return;
        }
        const metric = chartMetric === 'uplink' ? { field: 'aggregate_ul_mbps_scenario', title: 'All-cell uplink throughput', unit: ' Mbps' }
          : { field: 'prb', title: 'Venue-serving cell forecast / uplink PRB utilization', unit: '%' };
        title.textContent = metric.title;
        el.querySelector('#forecast-chart-subtitle').textContent = 'Every venue-serving cell / five-minute display';
        note.textContent = chartMetric === 'prb'
          ? 'Five-minute display from hourly model shapes. Point forecasts; not measured service outcomes.'
          : 'Synthetic throughput scenarios, not independently evaluated throughput forecasts. ' + plan.throughput_method;
        Charts.line('#prework-chart', {
          x: plan.five_minute_profile.seconds.map(s => s / 3600), xTicks: [-6, -4, -2, 0, 2, 4, 6], height: 300,
          marginLeft: chartMetric === 'prb' ? 44 : 72,
          yMin: 0, ...(chartMetric === 'prb' ? { yMax: 100 } : {}), legend: false,
          ariaLabel: metric.title + ' for every cell in the selected event',
          xFormat: h => h === 0 ? 'Event start' : (h > 0 ? '+' : '') + h + 'h',
          yFormat: v => (chartMetric === 'prb' ? v : +v.toFixed(1)) + metric.unit,
          series: profiles.map((row, i) => ({
            name: row.cell_id, values: row[metric.field], color: cellColors[i % cellColors.length],
            width: 2, dash: i >= cellColors.length ? '5 3' : undefined
          })),
          thresholds: chartMetric === 'prb' ? [{ y: 85, color: '#b43755', label: '85% planning line' }] : [],
          markers: [{ x: 0, label: 'Event start', color: '#91a5b8' }]
        });
      };
      const drawList = () => {
        const venue = el.querySelector('#venue-filter').value;
        const rows = events.filter(e => venue === 'all' || e.venue_id === venue);
        el.querySelector('#event-list').innerHTML = rows.map(e => `<button class="event-card ${e.event_id === selected ? 'selected' : ''}" data-event="${App.esc(e.event_id)}">
          <div class="meta"><span>${App.esc(e.start.slice(0, 10))}</span><span>${App.esc(e.home_away || e.category)}</span></div>
          <b>${App.esc(e.event_name)}</b><span class="muted">${App.esc(e.venue_name)}</span>
          <div class="mini-row"><span>${App.short(e.attendance_pred)} expected</span>${Exec.pill(e.risk + ' demand', e.risk)}</div></button>`).join('')
          || '<div class="empty">No events match this venue.</div>';
        el.querySelectorAll('[data-event]').forEach(button => button.onclick = () => {
          selected = button.dataset.event; drawList(); loadPlan().catch(Exec.error);
        });
      };
      const loadPlan = async () => {
        const request = ++selection;
        const plan = await App.api('/api/operations/plan/' + encodeURIComponent(selected) + '?fresh=1');
        if (!el.isConnected || request !== selection || version !== generation) return;
        const e = plan.event, q = plan.service_forecast;
        const readinessOpen = el.querySelector('#readiness-checklist')?.open;
        const elevated = e.risk === 'high' || q.latency_ms > plan.policy.latency_limit_ms || q.packet_loss_pct > plan.policy.packet_loss_limit_pct;
        el.querySelector('#plan-brief').innerHTML = `<div class="brief-copy"><span class="eyebrow">PLANNING POSTURE</span>
          <h2>${elevated ? 'Prioritize event readiness.' : 'Maintain headroom. Keep watch.'}</h2>
          <p>${App.esc(e.venue_name)} / ${App.esc(e.start)} / ${plan.recommendations.length} recommended focus areas</p></div>
          <div class="brief-status">${Exec.pill(elevated ? 'ELEVATED SCENARIO EXPOSURE' : 'LOWER SCENARIO EXPOSURE', elevated ? 'amber' : 'good')}
          <span>Advisory only. No network changes.</span></div>`;
        el.querySelector('#event-focus').innerHTML = `<div class="event-hero"><div><div class="eyebrow">${App.esc(e.venue_name)} / ${App.esc(e.start)}</div><h2>${App.esc(e.event_name)}</h2></div>${Exec.pill('ADVISORY ONLY')}</div>
          <section class="all-cell-forecast" aria-label="Event forecast chart"><div class="panel-head"><div><h3 id="forecast-chart-title"></h3><p class="subline" id="forecast-chart-subtitle"></p></div>${Exec.pill(e.heatmap.rows.length + ' CELLS')}</div>
            <div id="prework-chart"></div><div class="forecast-cell-legend">${plan.five_minute_profile.cells.map((row, i) => `<span><i style="background:${cellColors[i % cellColors.length]}"></i>${App.esc(row.cell_id)}${i >= cellColors.length ? ' (dashed)' : ''}</span>`).join('')}</div>
            <p class="simulation-note" id="forecast-chart-note"></p></section>
          <details class="cell-forecast-brief"><summary>Cell forecasts & assumptions</summary>
            <div class="forecast-table-wrap" role="region" aria-label="Cell forecasts and assumptions table" tabindex="0">
              <table class="table cell-forecast-table"><thead><tr>
                <th scope="col">Cell / sector-carrier</th><th scope="col">Peak PRB / 90% band</th>
                <th scope="col">Scenario UL Mbps<small>Nominal / upper / severe</small></th>
                <th scope="col">Scenario user Mbps<small>P50 / P05</small></th>
                <th scope="col">P(peak PRB &gt;85%)</th><th scope="col">85% load window / peak</th>
              </tr></thead><tbody>${plan.cell_forecasts.map(c => `<tr>
                <th scope="row"><b>${App.esc(c.cell_id)}</b><small>${App.esc(c.site_id)} / ${App.esc(c.sector)} / ${App.esc(c.band)}</small></th>
                <td><b>${c.peak_prb.toFixed(1)}%</b><small>${c.peak_low.toFixed(1)} - ${c.peak_high.toFixed(1)}%</small></td>
                <td>${c.aggregate_ul_mbps} / ${c.aggregate_ul_upper_mbps} / ${c.severe_ul_mbps}</td>
                <td>${c.user_p50_mbps} / ${c.user_p05_mbps}</td>
                <td>${Math.round(c.peak_risk * 100)}%</td>
                <td>${c.risk_start_s === null ? 'Not flagged' : '~' + Exec.clock(c.risk_start_s, e.start) + ' - ' + (c.recovery_s === null ? 'beyond window' : Exec.clock(c.recovery_s, e.start))}
                  <small>Peak ~${Exec.clock(c.peak_s, e.start)}</small></td>
              </tr>`).join('')}</tbody></table></div>
            <p class="simulation-note">${App.esc(plan.throughput_method)} Severe scenario: +30% attendance, capped by venue capacity. Timings are scenario 85% mean-load crossings, not calibrated outage predictions.</p></details>
          <div class="recommendation-summary"><span>Radio headroom <b>${Math.round(100 - e.peak_prb_max)}%</b></span>
            <span>Site link <b>${Math.round(plan.transport_forecast.site_util)}%</b></span><span>Shared link <b>${Math.round(plan.transport_forecast.agg_util)}%</b></span>
            <span>${(e.similar || []).length} comparable events</span></div>
          <div class="preparedness-heading"><h3>Planning priorities</h3><span>Review the evidence before committing resources.</span></div>
          <details class="cell-forecast-brief" id="readiness-checklist" ${readinessOpen ? 'open' : ''}><summary>Engineering readiness checklist</summary>
            <p class="simulation-note">${App.esc(e.venue_name)} / ${App.esc(e.start)} / prioritized from this event's forecast. Not an execution approval.</p>
            <div class="forecast-table-wrap" role="region" aria-label="Event-specific engineering readiness" tabindex="0">
              <table class="table readiness-table"><thead><tr><th scope="col">Readiness action</th><th scope="col">Forecast assessment</th><th scope="col">When</th><th scope="col">Engineering check</th></tr></thead><tbody>
              ${plan.readiness_checks.map(c => `<tr data-readiness="${App.esc(c.id)}"><td><b>${App.esc(c.title)}</b></td>
                <td>${Exec.pill({priority: 'PRIORITY', blocked: 'TARGET GAP', review: 'REVIEW', baseline: 'BASELINE', not_triggered: 'NOT TRIGGERED'}[c.status],
                  {priority: 'danger', blocked: 'amber', review: 'amber', baseline: '', not_triggered: ''}[c.status])}<p>${App.esc(c.evidence)}</p></td>
                <td>${App.esc(c.when)}</td><td>${App.esc(c.action)}</td></tr>`).join('')}</tbody></table>
            </div></details>
          <div class="recommendation-list">${plan.recommendations.map((r, i) => `<article class="recommendation-card">
            <div class="recommendation-head"><span class="recommendation-rank">${i + 1}</span><h3>${App.esc(r.issue)}</h3>${Exec.pill('P' + r.priority, r.priority === 1 ? 'danger' : r.priority === 2 ? 'amber' : 'good')}</div>
            <p class="priority-summary">${App.esc(prioritySummaries[r.id] || r.remedy)}</p>
            <div class="recommendation-foot"><span>${App.esc(r.owner)}</span><button class="btn link" data-rationale="${i}">View recommendation &nearr;</button></div></article>`).join('')}</div>
          <div class="recommendation-footer"><p>RTT / loss are modeled synthetic scenarios, not measured SLAs or independently backtested service forecasts.</p>
            ${e.event_id === 'EVT-LIVE-20261028' ? '<a class="btn primary" href="#/live">Observe live service &rarr;</a>' : ''}</div>`;
        el.querySelectorAll('[data-rationale]').forEach(button => button.onclick = () => {
          const r = plan.recommendations[+button.dataset.rationale];
          Exec.dialog(r.issue, `<p>${App.esc(r.evidence)}</p><h3>Why it matters</h3><p>${App.esc(r.impact)}</p>
            <h3>Recommended action</h3><p>${App.esc(r.remedy)}</p>
            <p class="muted">${App.esc(plan.method)}</p><p class="muted">No action is executed from this page. Live policy evaluates each remedy against current evidence.</p>`);
        });
        currentPlan = plan; drawChart();
        drawList();
      };
      el.querySelector('#venue-filter').onchange = () => {
        const venue = el.querySelector('#venue-filter').value;
        const matching = events.filter(e => venue === 'all' || e.venue_id === venue);
        if (!matching.some(e => e.event_id === selected) && matching.length) selected = matching[0].event_id;
        drawList(); loadPlan().catch(Exec.error);
      };
      drawList(); await loadPlan();
    },
    destroy() { generation++; selection++; }
  };
})();
