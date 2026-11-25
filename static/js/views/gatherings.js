/* 06 Unplanned gatherings */
(function () {
  'use strict';
  const C = Charts.colors;
  let timer = null;

  async function replay(el, gid) {
    const full = await App.api('/api/gatherings/' + encodeURIComponent(gid) + '/replay');
    // zoom on the onset: 30 min before the crowd forms to 45 min after congestion starts
    const tEnd = Math.max(full.first_congestion_s === null ? 0 : full.first_congestion_s, 0) + 2700;
    const keep = full.t.map((t, i) => t <= tEnd ? i : -1).filter(i => i >= 0);
    const r = Object.assign({}, full);
    ['t', 'prb_max', 'prb_mean', 'p10_min', 'rrc', 'cong'].forEach(k => { r[k] = keep.map(i => full[k][i]); });
    const box = el.querySelector('#ug-replay-meta');
    box.innerHTML = `<b>${App.esc(r.type)}</b> · ${App.esc(r.start)} · peak crowd ${App.num(r.peak_crowd)} · PoC action: <b>${App.esc(r.action.replace(/_/g, ' '))}</b>`;
    const shade = [];
    let st = null;
    r.t.forEach((t, i) => {
      if (r.cong[i] && st === null) st = t;
      if ((!r.cong[i] || i === r.t.length - 1) && st !== null) { shade.push({ from: st, to: t, color: C.red, opacity: 0.12 }); st = null; }
    });
    const markers = [];
    const fmtT = t => { const a = Math.abs(t), m = Math.floor(a / 60), sec = Math.round(a % 60); return (t < 0 ? '−' : '+') + m + 'm' + String(sec).padStart(2, '0') + 's'; };
    const legend = [];
    if (r.social_warning_s !== null) {
      legend.push(`<span><i style="background:${C.amber}"></i>social spike ${fmtT(r.social_warning_s)}${r.social_warning_s < r.t[0] ? ' (before window)' : ''}</span>`);
      if (r.social_warning_s >= r.t[0]) markers.push({ x: r.social_warning_s, color: C.amber });
    } else legend.push('<span><i style="background:#D8DADA"></i>no social signal</span>');
    markers.push({ x: 0, color: C.black, dash: '1 0' });
    legend.push(`<span><i style="background:${C.black}"></i>crowd forms 0m00s</span>`);
    markers.push({ x: r.network_warning_s, color: C.green });
    legend.push(`<span><i style="background:${C.green}"></i>network-only warning ${fmtT(r.network_warning_s)}</span>`);
    if (r.first_congestion_s !== null) {
      markers.push({ x: r.first_congestion_s, color: C.red });
      legend.push(`<span><i style="background:${C.red}"></i>congestion starts ${fmtT(r.first_congestion_s)}</span>`);
    }
    el.querySelector('#ug-markers').innerHTML = legend.join('');
    let k = 0;
    const draw = (cursor) => Charts.line('#ug-replay', {
      x: r.t, height: 300, yMin: 0, yMax: 100, yFormat: v => v + '%', xTickWidth: 70,
      xFormat: t => { const m = Math.round(t / 60); return (m > 0 ? '+' : '') + m + 'm'; },
      series: [{ name: 'Busiest cell UL PRB', values: r.prb_max.map((v, i) => cursor === null || i <= cursor ? v : null), color: C.red, width: 2.2 },
               { name: 'Venue average UL PRB', values: r.prb_mean.map((v, i) => cursor === null || i <= cursor ? v : null), color: C.black, width: 1.6 }],
      thresholds: [{ y: 85, label: '85%', color: C.red }], shade, markers, cursor: cursor === null ? null : r.t[cursor]
    });
    draw(null);
    const btn = el.querySelector('#ug-play');
    btn.onclick = () => {
      if (timer) { clearInterval(timer); timer = null; btn.textContent = '▶ Replay'; draw(null); return; }
      k = 0; btn.textContent = '■ Stop';
      timer = setInterval(() => { k += 6; if (k >= r.t.length) { clearInterval(timer); timer = null; btn.textContent = '▶ Replay'; draw(null); return; } draw(k); }, 60);
      App.timers.push(timer);
    };
  }

  App.views.gatherings = {
    async render(el) {
      const d = await App.api('/api/gatherings');
      el.innerHTML = `
        ${App.head('Unplanned gatherings', 'Crowds nobody scheduled - caught before they congest', d.takeaway)}
        <div class="kpis">
          ${App.kpiTile({ label: 'Unplanned gatherings', value: d.count, sub: `${d.venues} venues, 2016–2026 · ${App.pct(d.congestion_pct)} caused congestion` })}
          ${App.kpiTile({ label: 'Flagged by social spike', value: d.social_detected_pct, unit: '%', sub: `median ${d.social_lead_min_median} min warning when a spike exists` })}
          ${App.kpiTile({ label: 'Network-only warning', value: d.network_lead_s_median, unit: 's', sub: `median lead from live uplink signals alone · ${App.pct(d.lead_compare.network_only_share)} had no social signal` })}
          ${App.kpiTile({ label: 'PoC would steer', value: d.actions.steer_to_neighbor || 0, unit: '', red: false, sub: `${d.actions.fallback || 0} fallbacks · ${d.actions.no_routing || 0} monitor only` })}
        </div>
        <div class="grid g2">
          <div class="card"><div class="card-head"><div><h2>Gatherings by type</h2><div class="card-sub">Count of unscheduled crowds across all venues</div></div></div><div id="ug-types" class="chart-box"></div></div>
          <div class="card"><div class="card-head"><div><h2>Detection lead by type</h2><div class="card-sub">Social spike lead (minutes, when present) vs network-only lead (seconds)</div></div></div>
            <table class="tbl"><thead><tr><th>Type</th><th class="num">Social flagged</th><th class="num">Social lead</th><th class="num">Network lead</th><th class="num">Congested</th></tr></thead><tbody>
            ${d.by_type.map(t => `<tr><td><b>${App.esc(t.type)}</b></td><td class="num">${t.social_pct}%</td><td class="num">${t.social_lead_min === null ? '–' : t.social_lead_min + ' min'}</td>
              <td class="num">${t.network_lead_s} s</td><td class="num">${t.congestion_pct}%</td></tr>`).join('')}</tbody></table></div>
        </div>
        <div class="grid g-3-2">
          <div class="card"><div class="card-head"><div><h2>15-second replay · target venue</h2><div class="card-sub" id="ug-replay-meta"></div></div>
            <div class="toolbar"><select id="ug-sel">${d.v01.map(g => `<option value="${g.gathering_id}">${App.esc(g.gathering_id)} · ${App.esc(g.type)} · ${App.esc(g.start.slice(0, 10))}</option>`).join('')}</select>
            <button class="btn dark" id="ug-play">▶ Replay</button></div></div>
            <div id="ug-replay" class="chart-box"></div><div class="legend-inline" id="ug-markers" style="margin-top:8px"></div></div>
          <div class="card"><div class="card-head"><div><h2>Target-venue gatherings</h2><div class="card-sub">${d.v01.length} with 15-s telemetry, largest uplink surge first</div></div></div>
            <div class="scroll" style="max-height:340px"><table class="tbl" id="ug-list"><thead><tr><th>Gathering</th><th class="num">Crowd</th><th class="num">Surge</th><th>Social</th></tr></thead><tbody>
            ${d.v01.map(g => `<tr class="clickable" data-g="${g.gathering_id}"><td><b>${App.esc(g.type)}</b><div class="small muted">${App.esc(g.start)} · ${App.esc(g.zone)}</div></td>
              <td class="num">${App.short(g.peak_crowd)}</td><td class="num">+${App.num(g.surge_pct)}%</td><td>${g.social ? `<span class="pill ok">${g.social_lead_min} min</span>` : '<span class="pill none">none</span>'}</td></tr>`).join('')}</tbody></table></div></div>
        </div>`;
      Charts.bars('#ug-types', { labels: d.by_type.map(t => t.type), values: d.by_type.map(t => t.n), horizontal: true, height: 300, marginLeft: 230, color: C.black, name: 'gatherings', format: v => App.num(v) });
      const sel = el.querySelector('#ug-sel');
      const pick = gid => { sel.value = gid; el.querySelectorAll('#ug-list tr').forEach(tr => tr.classList.toggle('selected', tr.dataset.g === gid)); replay(el, gid).catch(e => App.toast(e.message)); };
      sel.addEventListener('change', () => pick(sel.value));
      el.querySelectorAll('#ug-list tr.clickable').forEach(tr => tr.addEventListener('click', () => pick(tr.dataset.g)));
      if (d.v01.length) pick(d.v01[0].gathering_id);
    },
    destroy() { if (timer) { clearInterval(timer); timer = null; } }
  };
})();
