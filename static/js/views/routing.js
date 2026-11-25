/* 04 Routing & backhaul */
(function () {
  'use strict';
  const C = Charts.colors;
  const TIER_COLORS = { 'tier 0 - monitor only': C.g2, 'tier 1 - automatic': C.green, 'tier 2 - engineer approves': C.black, 'tier 3 - fallback': C.red };

  App.views.routing = {
    async render(el) {
      const d = await App.api('/api/routing');
      const f = d.funnel;
      const blockedTotal = d.blocked_reasons.reduce((a, b) => a + b.n, 0);
      el.innerHTML = `
        ${App.head('Routing & backhaul', 'Steer only where the radio and the transport both have room', d.takeaway)}
        <div class="grid g-3-2">
          <div class="card"><div class="card-head"><div><h2>Decision funnel · 2022–2026</h2>
            <div class="card-sub">Steering recommendations through radio check, backhaul-aware check, execution and verification (files 20 + 26)</div></div></div>
            <div id="rt-funnel" class="chart-box"></div></div>
          <div class="card"><div class="card-head"><div><h2>Approval tiers</h2><div class="card-sub">All ${App.num(d.tiers.reduce((a, b) => a + b.n, 0))} decisions</div></div></div>
            <div id="rt-tiers" class="chart-box"></div></div>
        </div>
        <div class="grid g2">
          <div class="card"><div class="card-head"><div><h2>Why moves were blocked</h2><div class="card-sub">${App.num(blockedTotal)} unsafe steers turned into fallbacks</div></div></div>
            <div id="rt-blocked" class="chart-box"></div></div>
          <div class="card"><div class="card-head"><div><h2>Backhaul is the new bottleneck</h2>
            <div class="card-sub">90th-percentile busiest-15-min link utilisation during events · site vs venue aggregation links</div></div></div>
            <div id="rt-bh-year" class="chart-box"></div></div>
        </div>
        <div class="grid g-3-2">
          <div class="card"><div class="card-head"><div><h2>Target venue backhaul · live event</h2><div class="card-sub">15-second utilisation per link (file 25), thresholds 85% site / 90% aggregation</div></div></div>
            <div id="rt-live" class="chart-box"></div></div>
          <div class="card"><div class="card-head"><div><h2>Network settings changed</h2><div class="card-sub">Actions by parameter · share auto-rolled back after failed verify</div></div></div>
            <div id="rt-params" class="chart-box"></div></div>
        </div>
        <div class="card"><div class="card-head"><div><h2>Steering actions detail</h2><div class="card-sub">Most recent 60 changes with revert and change ticket (file 34)</div></div>
          <input type="search" id="rt-q" placeholder="Filter actions…" style="width:240px"></div>
          <div class="scroll"><table class="tbl" id="rt-actions"></table></div></div>`;
      Charts.funnel('#rt-funnel', {
        height: 300, stages: f.map((s, i) => ({
          stage: s.stage, n: s.n, of: i === 0 ? undefined : (i >= 4 ? f[3].n : f[i - 1].n),
          color: i === 0 ? C.black : i === 5 ? C.red : i === 4 ? C.green : C.g4
        }))
      });
      Charts.donut('#rt-tiers', {
        height: 300, items: d.tiers.map(t => ({ label: t.tier, value: t.n, color: TIER_COLORS[t.tier] })),
        center: { value: App.num(d.tiers.find(t => t.tier.startsWith('tier 3')).n), label: 'fallbacks' }, format: v => App.num(v)
      });
      Charts.bars('#rt-blocked', {
        labels: d.blocked_reasons.map(r => r.reason), values: d.blocked_reasons.map(r => r.n), horizontal: true, height: 220, marginLeft: 250,
        colors: d.blocked_reasons.map(r => r.reason.includes('backhaul') || r.reason.includes('aggregation') ? C.red : C.g4), format: v => App.num(v), name: 'decisions'
      });
      const by = d.backhaul_by_year;
      Charts.line('#rt-bh-year', {
        x: by.map(r => r.year), height: 240, yMin: 0, yMax: 100, yFormat: v => v + '%', xFormat: v => String(v),
        series: [
          { name: 'Aggregation p90', values: by.map(r => r.aggregation_p90), color: C.red, width: 2.5, dots: true },
          { name: 'Site p90', values: by.map(r => r.site_p90), color: C.black, width: 2.5, dots: true },
          { name: 'Aggregation median', values: by.map(r => r.aggregation_p50), color: C.red, dash: '4 4', width: 1.5 },
          { name: 'Site median', values: by.map(r => r.site_p50), color: C.g4, dash: '4 4', width: 1.5 },
        ],
        thresholds: [{ y: 90, label: '90% aggregation limit', color: C.red }, { y: 85, label: '85% site limit', color: C.black }]
      });
      const lb = d.live_backhaul, cols = [C.red, C.black, C.g4, C.amber];
      Charts.line('#rt-live', {
        x: lb.t, height: 260, yMin: 0, yMax: 100, yFormat: v => v + '%', xTickWidth: 70,
        xFormat: t => { const m = Math.round(t / 60); return (m < 0 ? 'T' : 'T+') + m + 'm'; },
        series: Object.keys(lb.links).map((k, i) => ({ name: `${k} (${d.live_capacity[k]} Gbps)`, values: lb.links[k], color: cols[i % cols.length], width: k.endsWith('AGG') ? 2.6 : 1.8 })),
        thresholds: [{ y: 90, label: '90%', color: C.red }, { y: 85, label: '85%', color: C.black }]
      });
      Charts.bars('#rt-params', {
        labels: d.actions_by_param.map(p => p.parameter.replace(/_/g, ' ')), horizontal: true, height: 260, marginLeft: 210,
        series: [{ name: 'actions', values: d.actions_by_param.map(p => p.n), color: C.black }], format: v => App.num(v),
        tipFormat: v => App.num(v)
      });
      const tbl = el.querySelector('#rt-actions');
      const draw = (q) => {
        const rows = d.actions.filter(a => !q || JSON.stringify(a).toLowerCase().includes(q.toLowerCase()));
        tbl.innerHTML = `<thead><tr><th>Applied</th><th>Ticket</th><th>Cell</th><th>Target</th><th>Parameter</th><th class="num">Old → new</th><th>Tier</th><th>Executed by</th><th>Reverted</th></tr></thead><tbody>
          ${rows.map(a => `<tr><td class="nowrap">${App.esc(a.applied_at)}</td><td class="small">${App.esc(a.change_ticket_id)}</td><td>${App.esc(a.cell_id)}</td>
            <td>${App.esc(a.target_cell_id || '—')}</td><td>${App.esc(a.parameter)}</td><td class="num nowrap">${a.old_value} → <b>${a.new_value}</b> ${App.esc(a.unit)}</td>
            <td><span class="pill ${App.tierClass(a.approval_tier)}">${App.esc((a.approval_tier || '').split(' - ')[0])}</span></td><td class="small">${App.esc(a.executed_by)}</td>
            <td class="small">${a.revert_reason && a.revert_reason.startsWith('verify') ? '<span class="pill rollback">rollback</span>' : App.esc(a.revert_reason || '')}</td></tr>`).join('')}</tbody>`;
      };
      draw('');
      el.querySelector('#rt-q').addEventListener('input', App.debounce(e => draw(e.target.value), 150));
    }
  };
})();
