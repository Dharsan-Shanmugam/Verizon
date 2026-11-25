/* 07 Model health - learn & govern */
(function () {
  'use strict';
  const C = Charts.colors;

  App.views.health = {
    async render(el) {
      const d = await App.api('/api/model-health');
      const m = d.metrics, pk = m.peak_uplink.test, at = m.attendance.test, sh = m.short_horizon.test;
      const h = d.history.series;
      const verChanges = [];
      h.forEach((r, i) => { if (i === 0 || r.version !== h[i - 1].version) verChanges.push({ x: i, label: r.version, color: C.g4, row: verChanges.length % 2 }); });
      el.innerHTML = `
        ${App.head('Model health · learn & govern', 'Is the forecast still right - and is the world drifting?', d.takeaway)}
        <div class="kpis">
          ${App.kpiTile({ label: 'Peak uplink MAPE', value: pk.mape_pct, unit: '%', red: false, sub: `MAE ${pk.mae_prb_pts} PRB pts · bias ${pk.bias_prb_pts} · ${App.num(pk.n)} cell-events 2025–26` })}
          ${App.kpiTile({ label: 'Interval coverage', value: pk.interval_coverage * 100, unit: '%', red: false, sub: `nominal ${Math.round(pk.nominal_coverage * 100)}% band · hot-cell recall ${Math.round(pk.hot_cell_recall * 100)}%` })}
          ${App.kpiTile({ label: 'Attendance MAPE', value: at.mape_pct, unit: '%', red: false, sub: `organiser estimate alone ${at.baseline_expected_mape_pct}% · coverage ${Math.round(at.interval_coverage * 100)}%` })}
          ${App.kpiTile({ label: '60-s warning AUC', value: sh['60s'].auc, display: sh['60s'].auc.toFixed(3), unit: '', red: false, sub: `precision ${Math.round(sh['60s'].precision * 100)}% · recall ${Math.round(sh['60s'].recall * 100)}% on ${m.short_horizon.test_events} held-out events` })}
        </div>
        <div class="grid g2">
          <div class="card"><div class="card-head"><div><h2>Forecast error over time</h2><div class="card-sub">Rolling 30-day MAPE of the production forecast (file 18, weekly) · model versions marked</div></div></div><div id="mh-mape" class="chart-box"></div></div>
          <div class="card"><div class="card-head"><div><h2>Bias and interval coverage</h2><div class="card-sub">Share of actuals inside the forecast band (top) and rolling 30-day bias in PRB pts (bottom)</div></div></div><div id="mh-cov" class="chart-box"></div><div id="mh-bias" class="chart-box"></div></div>
        </div>
        <div class="grid g2">
          <div class="card"><div class="card-head"><div><h2>Drift (PSI)</h2><div class="card-sub">Population stability: weekly max PSI of UL PRB and attendance · 0.1 watch / 0.25 alert</div></div></div><div id="mh-psi" class="chart-box"></div></div>
          <div class="card"><div class="card-head"><div><h2>vdi-1.0 hold-out backtest</h2><div class="card-sub">Monthly MAE (PRB pts, bars) and 90% band coverage (line), model trained ≤2024</div></div></div><div id="mh-bt" class="chart-box"></div><div id="mh-btc" class="chart-box"></div></div>
        </div>
        <div class="grid g3">
          <div class="card"><div class="card-head"><h2>Drift & alerts</h2></div><div class="alert-list">
            ${d.alerts.map(a => `<div class="al"><span class="pill ${a.severity}">${a.severity}</span><span>${App.esc(a.text)}</span></div>`).join('')}</div></div>
          <div class="card"><div class="card-head"><h2>Feature drift (PSI)</h2></div>
            <table class="tbl"><thead><tr><th>Feature</th><th class="num">PSI</th><th>Status</th></tr></thead><tbody>
            ${m.monitoring.drift.map(r => `<tr><td>${App.esc(r.feature)}</td><td class="num">${r.psi === null ? '–' : r.psi.toFixed(3)}</td><td><span class="pill ${r.status}">${r.status}</span></td></tr>`).join('')}</tbody></table>
            <div class="foot-note">Training ≤${m.split.match(/\d{4}/)[0]} vs 2025–26. Capacity upgrades shift load features by design; the model re-learns them at refit.</div></div>
          <div class="card"><div class="card-head"><h2>Short-horizon congestion model</h2></div>
            <table class="tbl"><thead><tr><th>Horizon</th><th class="num">AUC</th><th class="num">Precision</th><th class="num">Recall</th><th class="num">Thr.</th></tr></thead><tbody>
            ${Object.keys(sh).map(k => `<tr><td><b>${k}</b></td><td class="num">${sh[k].auc.toFixed(3)}</td><td class="num">${Math.round(sh[k].precision * 100)}%</td><td class="num">${Math.round(sh[k].recall * 100)}%</td><td class="num">${sh[k].threshold}</td></tr>`).join('')}</tbody></table>
            <div class="foot-note">Top drivers (60 s): ${m.short_horizon.drivers_60s.slice(0, 5).map(x => App.esc(x.feature)).join(', ')}. Excluded by policy: ${m.short_horizon.excluded_features.map(App.esc).join(', ')}.</div></div>
        </div>
        <div class="grid g2">
          <div class="card"><div class="card-head"><h2>Model versions</h2></div>
            <table class="tbl"><thead><tr><th>Version</th><th>In production</th><th class="num">Events</th><th class="num">MAPE</th><th class="num">Coverage</th></tr></thead><tbody>
            ${d.history.versions.map(v => `<tr><td><b>${App.esc(v.version)}</b></td><td class="small">${App.esc(v.from)} → ${App.esc(v.to)}</td><td class="num">${App.num(v.events)}</td><td class="num">${v.mape.toFixed(1)}%</td><td class="num">${Math.round(v.coverage * 100)}%</td></tr>`).join('')}
            <tr class="selected"><td><b>${App.esc(m.model_version)}</b> (this PoC)</td><td class="small">trained ${App.esc(m.generated_at.slice(0, 10))}</td><td class="num">${App.num(m.data.events_completed)}</td><td class="num">${pk.mape_pct.toFixed(1)}%</td><td class="num">${Math.round(pk.interval_coverage * 100)}%</td></tr></tbody></table>
            <div class="foot-note">Incumbent backtest (file 17): ${d.provided.map(p => `${App.esc(p.version)} ${p.mape}%`).join(' · ')} MAPE (forecast issued 7 days ahead).</div></div>
          <div class="card"><div class="card-head"><h2>Live drift per cell · current event</h2></div>
            <table class="tbl"><thead><tr><th>Cell</th><th class="num">MAE 5 min</th><th class="num">Bias 5 min</th><th class="num">Drift score</th><th class="num">Alerts</th></tr></thead><tbody>
            ${d.live_drift.cells.map(c => `<tr><td>${App.esc(c.cell_id)}</td><td class="num">${c.mae_5min}</td><td class="num">${c.bias_5min}</td><td class="num">${c.drift_score === null ? '–' : c.drift_score}</td><td class="num">${c.alerts}</td></tr>`).join('')}</tbody></table>
            <div class="foot-note">Training runtimes: attendance ${m.timings.attendance_s}s · peak ${m.timings.peak_uplink_s}s · short-horizon ${m.timings.short_horizon_s}s · total ${m.timings.total_s}s.</div></div>
        </div>`;
      const idx = h.map((_, i) => i);
      const lbl = i => h[i] ? h[i].date.slice(0, 7) : '';
      Charts.line('#mh-mape', {
        x: idx, xFormat: (_, i) => lbl(i), height: 260, yMin: 0, yFormat: v => v + '%', xTickWidth: 70,
        series: [{ name: 'Rolling 30-day MAPE', values: h.map(r => r.mape), color: C.red, width: 2 }], markers: verChanges,
        shade: d.history.episodes.map(e => ({ from: h.findIndex(r => r.date >= e.start), to: Math.max(h.findIndex(r => r.date >= e.end), 0) || h.length - 1, color: C.amber, opacity: 0.1 }))
      });
      Charts.line('#mh-cov', {
        x: idx, xFormat: (_, i) => lbl(i), height: 170, yMin: 70, yMax: 100, xTickWidth: 70, yFormat: v => v + '%',
        series: [{ name: 'Interval coverage', values: h.map(r => r.coverage * 100), color: C.green, width: 2 }],
        thresholds: [{ y: 90, label: '90% nominal', color: C.green }]
      });
      Charts.line('#mh-bias', {
        x: idx, xFormat: (_, i) => lbl(i), height: 120, xTickWidth: 70, yFormat: v => v.toFixed(1),
        series: [{ name: 'Bias (PRB pts)', values: h.map(r => r.bias), color: C.black, width: 1.6 }],
        thresholds: [{ y: 0, label: '', color: C.g3 }]
      });
      Charts.line('#mh-psi', {
        x: idx, xFormat: (_, i) => lbl(i), height: 240, yMin: 0, xTickWidth: 70,
        series: [{ name: 'PSI UL PRB', values: h.map(r => r.psi_prb), color: C.red, width: 1.8 }, { name: 'PSI attendance', values: h.map(r => r.psi_att), color: C.black, width: 1.4 }],
        thresholds: [{ y: 0.25, label: 'alert', color: C.red }, { y: 0.1, label: 'watch', color: C.amber }]
      });
      const bt = m.monitoring.backtest_monthly;
      Charts.bars('#mh-bt', {
        labels: bt.map(r => r.month.slice(2)), height: 170, format: v => v.toFixed(1), name: 'MAE (PRB pts)', values: bt.map(r => r.mae), color: C.black
      });
      Charts.line('#mh-btc', {
        x: bt.map(r => r.month.slice(2)), xType: 'category', height: 120, yMin: 50, yMax: 100, yFormat: v => v + '%', xTickWidth: 60,
        series: [{ name: 'Band coverage', values: bt.map(r => r.coverage * 100), color: C.green, width: 2, dots: true }],
        thresholds: [{ y: 90, label: '90%', color: C.green }]
      });
    }
  };
})();
