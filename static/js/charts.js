/* Minimal SVG chart library (no dependencies, works offline).
 * Charts.line / area / bars / stackedBar / donut / gauge / heatmap / sparkline / funnel
 * Every chart takes (element, options) and re-renders on container resize. */
(function () {
  'use strict';
  const NS = 'http://www.w3.org/2000/svg';
  const C = {
    red: '#EE0000', black: '#000000', g1: '#F6F6F6', g2: '#D8DADA', g3: '#A7A7A7', g4: '#6F7171',
    green: '#008331', amber: '#ED7000', darkred: '#A30000', ink: '#1B1D1F'
  };
  const PALETTE = [C.red, C.black, C.g4, C.amber, C.g3, C.darkred, '#4B4D4F', C.g2];

  function svg(tag, attrs, parent) {
    const e = document.createElementNS(NS, tag);
    for (const k in attrs || {}) if (attrs[k] !== undefined && attrs[k] !== null) e.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(e);
    return e;
  }
  function text(parent, x, y, str, attrs) {
    const t = svg('text', Object.assign({ x, y }, attrs || {}), parent);
    t.textContent = str;
    return t;
  }
  function fmt(v, f) {
    if (v === null || v === undefined || Number.isNaN(v)) return '–';
    if (typeof f === 'function') return f(v);
    const a = Math.abs(v);
    if (a >= 1e6) return (v / 1e6).toFixed(1) + 'M';
    if (a >= 1e4) return (v / 1e3).toFixed(0) + 'k';
    if (a >= 100) return v.toFixed(0);
    if (a >= 10) return v.toFixed(1).replace(/\.0$/, '');
    return v.toFixed(2).replace(/\.?0+$/, '');
  }
  function niceTicks(min, max, n) {
    if (min === max) { max = min + 1; }
    const span = max - min, step0 = span / Math.max(n, 1);
    const mag = Math.pow(10, Math.floor(Math.log10(step0)));
    const err = step0 / mag;
    const step = (err >= 7.5 ? 10 : err >= 3.5 ? 5 : err >= 1.5 ? 2 : 1) * mag;
    const out = [];
    for (let v = Math.ceil(min / step) * step; v <= max + step * 1e-9; v += step) out.push(+v.toFixed(10));
    return out;
  }

  // ---------------------------------------------------------------- tooltip
  let tip;
  function tooltip() {
    if (!tip) { tip = document.createElement('div'); tip.className = 'chart-tip'; document.body.appendChild(tip); }
    return tip;
  }
  function showTip(html, ev) {
    const t = tooltip(); t.innerHTML = html; t.style.display = 'block';
    const pad = 14, w = t.offsetWidth, h = t.offsetHeight;
    let x = ev.clientX + pad, y = ev.clientY + pad;
    if (x + w > window.innerWidth - 8) x = ev.clientX - w - pad;
    if (y + h > window.innerHeight - 8) y = ev.clientY - h - pad;
    t.style.left = x + 'px'; t.style.top = y + 'px';
  }
  function hideTip() { if (tip) tip.style.display = 'none'; }

  // ---------------------------------------------------------------- mount / resize
  const ro = ('ResizeObserver' in window) ? new ResizeObserver(entries => {
    for (const e of entries) {
      const el = e.target;
      if (el.__chart && Math.abs((el.__w || 0) - el.clientWidth) > 2) el.__chart();
    }
  }) : null;
  function mount(el, opts, draw) {
    if (typeof el === 'string') el = document.querySelector(el);
    if (!el) return null;
    const render = () => {
      el.__w = el.clientWidth;
      el.innerHTML = '';
      const W = Math.max(el.clientWidth || 300, 120);
      const H = opts.height || el.clientHeight || 220;
      const root = svg('svg', { width: W, height: H, viewBox: `0 0 ${W} ${H}`, class: 'chart', role: 'img',
        'aria-label': opts.ariaLabel || 'chart' }, el);
      draw(root, W, H);
    };
    el.__chart = render;
    if (ro && !el.__observed) { ro.observe(el); el.__observed = true; }
    render();
    return { el, update: (o) => { Object.assign(opts, o); render(); } };
  }

  function legend(root, items, x, y, W) {
    let cx = x;
    const g = svg('g', { class: 'legend' }, root);
    items.forEach(it => {
      const w = 20 + it.name.length * 6.6;
      if (cx + w > W - 4) { cx = x; y += 16; }
      if (it.dash) svg('line', { x1: cx, x2: cx + 14, y1: y - 4, y2: y - 4, stroke: it.color, 'stroke-width': 2, 'stroke-dasharray': it.dash }, g);
      else svg('rect', { x: cx, y: y - 9, width: 10, height: 10, rx: 2, fill: it.color }, g);
      text(g, cx + (it.dash ? 18 : 14), y, it.name, { class: 'legend-label' });
      cx += w + 10;
    });
    return y;
  }

  // ---------------------------------------------------------------- line / area
  function line(el, o) {
    return mount(el, o, (root, W, H) => {
      const series = (o.series || []).filter(s => s && s.values);
      const x = o.x || (series[0] ? series[0].values.map((_, i) => i) : []);
      const n = x.length;
      const m = { t: 12, r: o.marginRight || 16, b: o.xLabel ? 40 : 28, l: o.marginLeft || 44 };
      if (o.legend !== false && series.length) {
        const items = series.filter(s => !s.noLegend).map(s => ({ name: s.name, color: s.color, dash: s.dash }));
        (o.bands || []).forEach(b => b.name && items.push({ name: b.name, color: b.legendColor || b.color }));
        m.t = legend(root, items, m.l, 14, W) + 16;
      }
      const pw = W - m.l - m.r, ph = H - m.t - m.b;
      let ymin = o.yMin, ymax = o.yMax;
      if (ymin === undefined || ymax === undefined) {
        let lo = Infinity, hi = -Infinity;
        series.forEach(s => s.values.forEach(v => { if (v !== null && !Number.isNaN(v)) { lo = Math.min(lo, v); hi = Math.max(hi, v); } }));
        (o.bands || []).forEach(b => { b.low.forEach(v => v !== null && (lo = Math.min(lo, v))); b.high.forEach(v => v !== null && (hi = Math.max(hi, v))); });
        (o.thresholds || []).forEach(t => { lo = Math.min(lo, t.y); hi = Math.max(hi, t.y); });
        if (!Number.isFinite(lo)) { lo = 0; hi = 1; }
        const pad = (hi - lo) * 0.08 || 1;
        if (ymin === undefined) ymin = lo >= 0 && lo - pad < 0 ? 0 : lo - pad;
        if (ymax === undefined) ymax = hi + pad;
      }
      const xv = o.xType === 'category' ? x.map((_, i) => i) : x;
      const x0 = xv[0], x1 = xv[n - 1] === x0 ? x0 + 1 : xv[n - 1];
      const X = v => m.l + (v - x0) / (x1 - x0) * pw;
      const Y = v => m.t + ph - (v - ymin) / (ymax - ymin) * ph;
      const g = svg('g', {}, root);
      niceTicks(ymin, ymax, Math.max(2, Math.floor(ph / 45))).forEach(t => {
        svg('line', { x1: m.l, x2: m.l + pw, y1: Y(t), y2: Y(t), class: 'grid' }, g);
        text(g, m.l - 8, Y(t) + 4, fmt(t, o.yFormat), { class: 'axis-label', 'text-anchor': 'end' });
      });
      const nxt = Math.max(2, Math.floor(pw / (o.xTickWidth || 80)));
      const step = Math.max(1, Math.ceil(n / nxt));
      const tickIndices = o.xTicks ? o.xTicks.map(value => x.indexOf(value)).filter(i => i >= 0)
        : Array.from({ length: Math.ceil(n / step) }, (_, i) => i * step);
      for (const i of tickIndices) {
        text(g, X(xv[i]), m.t + ph + 18, o.xFormat ? o.xFormat(x[i], i) : String(x[i]), { class: 'axis-label', 'text-anchor': 'middle' });
      }
      if (o.xLabel) text(g, m.l + pw / 2, H - 4, o.xLabel, { class: 'axis-title', 'text-anchor': 'middle' });
      if (o.yLabel) text(g, 12, m.t + ph / 2, o.yLabel, { class: 'axis-title', 'text-anchor': 'middle', transform: `rotate(-90 12 ${m.t + ph / 2})` });
      (o.shade || []).forEach(s => {
        const a = X(s.from), b = X(s.to);
        svg('rect', { x: Math.min(a, b), y: m.t, width: Math.max(Math.abs(b - a), 1), height: ph, fill: s.color || C.red, opacity: s.opacity || 0.08 }, g);
      });
      (o.bands || []).forEach(b => {
        let d = '';
        for (let i = 0; i < n; i++) if (b.high[i] !== null) d += (d ? 'L' : 'M') + X(xv[i]) + ',' + Y(Math.min(b.high[i], ymax));
        for (let i = n - 1; i >= 0; i--) if (b.low[i] !== null) d += 'L' + X(xv[i]) + ',' + Y(Math.max(b.low[i], ymin));
        if (d) svg('path', { d: d + 'Z', fill: b.color || C.g2, opacity: b.opacity || 0.5 }, g);
      });
      const usedY = [];
      (o.thresholds || []).forEach(t => {
        if (t.y < ymin || t.y > ymax) return;
        svg('line', { x1: m.l, x2: m.l + pw, y1: Y(t.y), y2: Y(t.y), stroke: t.color || C.red, 'stroke-dasharray': '4 4', 'stroke-width': 1.2 }, g);
        let ly = Y(t.y) - 5;
        if (usedY.some(u => Math.abs(u - ly) < 12)) ly = Y(t.y) + 13;
        usedY.push(ly);
        if (t.label) text(g, m.l + pw - 4, ly, t.label, { class: 'thr-label', 'text-anchor': 'end', fill: t.color || C.red });
      });
      series.forEach(s => {
        let d = '', started = false;
        for (let i = 0; i < n; i++) {
          const v = s.values[i];
          if (v === null || v === undefined || Number.isNaN(v)) { started = false; continue; }
          d += (started ? 'L' : 'M') + X(xv[i]).toFixed(1) + ',' + Y(Math.max(Math.min(v, ymax), ymin)).toFixed(1);
          started = true;
        }
        if (s.area) {
          const first = s.values.findIndex(v => v !== null), last = s.values.length - 1 - [...s.values].reverse().findIndex(v => v !== null);
          if (first >= 0) svg('path', { d: d + `L${X(xv[last])},${Y(Math.max(ymin, 0))}L${X(xv[first])},${Y(Math.max(ymin, 0))}Z`, fill: s.color, opacity: s.areaOpacity || 0.12 }, g);
        }
        svg('path', { d, fill: 'none', stroke: s.color || C.black, 'stroke-width': s.width || 2, 'stroke-dasharray': s.dash, 'stroke-linejoin': 'round', 'stroke-linecap': 'round' }, g);
        if (s.dots) s.values.forEach((v, i) => v !== null && svg('circle', { cx: X(xv[i]), cy: Y(v), r: 3, fill: s.color }, g));
      });
      (o.markers || []).forEach(mk => {
        const xx = X(mk.x);
        if (xx < m.l - 1 || xx > m.l + pw + 1) return;
        svg('line', { x1: xx, x2: xx, y1: m.t, y2: m.t + ph, stroke: mk.color || C.black, 'stroke-width': 1.2, 'stroke-dasharray': mk.dash || '3 3' }, g);
        if (mk.label) text(g, xx + (mk.anchor === 'end' ? -4 : 4), m.t + 10 + (mk.row || 0) * 14, mk.label, { class: 'marker-label', fill: mk.color || C.black, 'text-anchor': mk.anchor || 'start' });
      });
      if (o.cursor !== undefined && o.cursor !== null) {
        const cx = X(o.cursor);
        svg('line', { x1: cx, x2: cx, y1: m.t, y2: m.t + ph, stroke: C.black, 'stroke-width': 1.5 }, g);
        svg('circle', { cx, cy: m.t, r: 3.5, fill: C.black }, g);
      }
      // hover
      const hl = svg('line', { x1: 0, x2: 0, y1: m.t, y2: m.t + ph, stroke: C.g4, 'stroke-width': 1, opacity: 0 }, g);
      const hit = svg('rect', { x: m.l, y: m.t, width: pw, height: ph, fill: 'transparent' }, g);
      hit.addEventListener('mousemove', ev => {
        const r = root.getBoundingClientRect();
        const px = ev.clientX - r.left;
        let best = 0, bd = Infinity;
        for (let i = 0; i < n; i++) { const dd = Math.abs(X(xv[i]) - px); if (dd < bd) { bd = dd; best = i; } }
        hl.setAttribute('x1', X(xv[best])); hl.setAttribute('x2', X(xv[best])); hl.setAttribute('opacity', 1);
        let html = `<b>${o.xFormat ? o.xFormat(x[best], best) : x[best]}</b>`;
        series.forEach(s => { if (!s.noTip) html += `<div><span class="sw" style="background:${s.color}"></span>${s.name}: ${fmt(s.values[best], o.tipFormat || o.yFormat)}</div>`; });
        (o.bands || []).forEach(b => b.name && (html += `<div>${b.name}: ${fmt(b.low[best], o.yFormat)} – ${fmt(b.high[best], o.yFormat)}</div>`));
        showTip(html, ev);
      });
      hit.addEventListener('mouseleave', () => { hideTip(); hl.setAttribute('opacity', 0); });
      if (o.onClick) hit.addEventListener('click', ev => {
        const r = root.getBoundingClientRect(); const px = ev.clientX - r.left;
        let best = 0, bd = Infinity;
        for (let i = 0; i < n; i++) { const dd = Math.abs(X(xv[i]) - px); if (dd < bd) { bd = dd; best = i; } }
        o.onClick(best, x[best]);
      });
    });
  }
  function area(el, o) {
    o.series = (o.series || []).map(s => Object.assign({ area: true }, s));
    return line(el, o);
  }

  // ---------------------------------------------------------------- bars (grouped / stacked / horizontal)
  function bars(el, o) {
    return mount(el, o, (root, W, H) => {
      const labels = o.labels || [];
      const series = o.series || [{ name: o.name || '', values: o.values || [], color: o.color || C.red, colors: o.colors }];
      const stacked = !!o.stacked, horiz = !!o.horizontal;
      const showLegend = o.legend !== undefined ? o.legend : series.length > 1;
      const m = { t: 10, r: o.marginRight || (horiz ? 56 : 12), b: horiz ? 10 : (o.xLabelHeight || 30), l: o.marginLeft || (horiz ? 150 : 44) };
      if (showLegend) m.t = legend(root, series.map(s => ({ name: s.name, color: s.color })), horiz ? 8 : m.l, 14, W) + 16;
      if (!horiz && o.valueLabels) m.t += 10;
      const pw = W - m.l - m.r, ph = H - m.t - m.b;
      const totals = labels.map((_, i) => stacked ? series.reduce((a, s) => a + (s.values[i] || 0), 0) : Math.max(...series.map(s => s.values[i] || 0)));
      let vmax = o.max !== undefined ? o.max : Math.max(...totals, ...(o.thresholds || []).map(t => t.v), 0) * 1.08 || 1;
      const vmin = o.min || 0;
      const g = svg('g', {}, root);
      const n = labels.length || 1;
      const band = (horiz ? ph : pw) / n;
      const inner = band * (o.barWidth || 0.68);
      const V = v => (v - vmin) / (vmax - vmin);
      if (!horiz) {
        niceTicks(vmin, vmax, Math.max(2, Math.floor(ph / 45))).forEach(t => {
          const y = m.t + ph - V(t) * ph;
          svg('line', { x1: m.l, x2: m.l + pw, y1: y, y2: y, class: 'grid' }, g);
          text(g, m.l - 8, y + 4, fmt(t, o.format), { class: 'axis-label', 'text-anchor': 'end' });
        });
      }
      const every = horiz ? 1 : Math.max(1, Math.ceil(n / Math.max(1, Math.floor(pw / (o.labelWidth || 46)))));
      labels.forEach((lab, i) => {
        const start = (horiz ? m.t : m.l) + i * band + (band - inner) / 2;
        let acc = 0;
        series.forEach((s, k) => {
          const v = s.values[i] || 0;
          const col = (s.colors && s.colors[i]) || s.color || PALETTE[k % PALETTE.length];
          let rect;
          if (horiz) {
            const bh = stacked ? inner : inner / series.length;
            const y = stacked ? start : start + k * bh;
            const x = m.l + V(stacked ? acc : 0) * pw;
            rect = svg('rect', { x, y, width: Math.max(V(v) * pw, v > 0 ? 1 : 0), height: Math.max(bh - 1, 1), fill: col, rx: 2 }, g);
            if (!stacked || k === series.length - 1) {
              const endv = stacked ? acc + v : v;
              if (o.valueLabels !== false) text(g, m.l + V(endv) * pw + 6, y + bh / 2 + 4, fmt(endv, o.format), { class: 'bar-value' });
            }
          } else {
            const bw = stacked ? inner : inner / series.length;
            const x = stacked ? start : start + k * bw;
            const top = V(stacked ? acc + v : v), bot = V(stacked ? acc : 0);
            rect = svg('rect', { x, y: m.t + ph - top * ph, width: Math.max(bw - 1, 1), height: Math.max((top - bot) * ph, v > 0 ? 1 : 0), fill: col, rx: stacked ? 0 : 2 }, g);
            if (o.valueLabels && (!stacked || k === series.length - 1)) text(g, x + bw / 2, m.t + ph - top * ph - 5, fmt(stacked ? acc + v : v, o.format), { class: 'bar-value', 'text-anchor': 'middle' });
          }
          acc += v;
          rect.addEventListener('mousemove', ev => showTip(`<b>${lab}</b><div><span class="sw" style="background:${col}"></span>${s.name ? s.name + ': ' : ''}${fmt(v, o.tipFormat || o.format)}</div>`, ev));
          rect.addEventListener('mouseleave', hideTip);
          if (o.onClick) { rect.style.cursor = 'pointer'; rect.addEventListener('click', () => o.onClick(i, lab)); }
        });
        if (horiz) {
          const t = text(g, m.l - 8, start + inner / 2 + 4, lab, { class: 'axis-label', 'text-anchor': 'end' });
          if (o.labelClass) t.setAttribute('class', o.labelClass);
        } else if (i % every === 0) {
          text(g, m.l + i * band + band / 2, m.t + ph + 18, lab, { class: 'axis-label', 'text-anchor': 'middle' });
        }
      });
      (o.thresholds || []).forEach(t => {
        if (horiz) {
          const x = m.l + V(t.v) * pw;
          svg('line', { x1: x, x2: x, y1: m.t, y2: m.t + ph, stroke: t.color || C.red, 'stroke-dasharray': '4 4' }, g);
          if (t.label) text(g, x + 4, m.t + 10, t.label, { class: 'thr-label', fill: t.color || C.red });
        } else {
          const y = m.t + ph - V(t.v) * ph;
          svg('line', { x1: m.l, x2: m.l + pw, y1: y, y2: y, stroke: t.color || C.red, 'stroke-dasharray': '4 4' }, g);
          if (t.label) text(g, m.l + pw - 2, y - 5, t.label, { class: 'thr-label', 'text-anchor': 'end', fill: t.color || C.red });
        }
      });
    });
  }
  const bar = bars;
  function stackedBar(el, o) { return bars(el, Object.assign({ stacked: true }, o)); }

  // ---------------------------------------------------------------- donut
  function donut(el, o) {
    return mount(el, o, (root, W, H) => {
      const items = (o.items || []).filter(i => i.value > 0);
      const total = items.reduce((a, b) => a + b.value, 0) || 1;
      const legendW = o.legend === false ? 0 : Math.min(240, W * 0.5);
      const R = Math.min((W - legendW) / 2, H / 2) - 8, r = R * (o.inner || 0.64);
      const cx = (W - legendW) / 2, cy = H / 2;
      let a0 = -Math.PI / 2;
      items.forEach((it, k) => {
        const a1 = a0 + it.value / total * Math.PI * 2;
        const large = a1 - a0 > Math.PI ? 1 : 0;
        const p = (rad, a) => [cx + rad * Math.cos(a), cy + rad * Math.sin(a)];
        const [x0, y0] = p(R, a0), [x1, y1] = p(R, a1 - 1e-6), [x2, y2] = p(r, a1 - 1e-6), [x3, y3] = p(r, a0);
        const col = it.color || PALETTE[k % PALETTE.length];
        const path = svg('path', { d: `M${x0},${y0}A${R},${R} 0 ${large} 1 ${x1},${y1}L${x2},${y2}A${r},${r} 0 ${large} 0 ${x3},${y3}Z`, fill: col, stroke: '#fff', 'stroke-width': 2 }, root);
        path.addEventListener('mousemove', ev => showTip(`<b>${it.label}</b><div>${fmt(it.value, o.format)} · ${(100 * it.value / total).toFixed(1)}%</div>`, ev));
        path.addEventListener('mouseleave', hideTip);
        a0 = a1;
      });
      if (o.center) {
        text(root, cx, cy + 4, o.center.value, { class: 'donut-value', 'text-anchor': 'middle' });
        if (o.center.label) text(root, cx, cy + 24, o.center.label, { class: 'donut-label', 'text-anchor': 'middle' });
      }
      if (o.legend !== false) {
        let y = Math.max(cy - items.length * 11, 14);
        items.forEach((it, k) => {
          const col = it.color || PALETTE[k % PALETTE.length];
          svg('rect', { x: W - legendW + 6, y: y - 9, width: 10, height: 10, rx: 2, fill: col }, root);
          text(root, W - legendW + 22, y, `${it.label}`, { class: 'legend-label' });
          text(root, W - 4, y, `${(100 * it.value / total).toFixed(0)}%`, { class: 'legend-label strong', 'text-anchor': 'end' });
          y += 22;
        });
      }
    });
  }

  // ---------------------------------------------------------------- gauge
  function gauge(el, o) {
    return mount(el, o, (root, W, H) => {
      const min = o.min || 0, max = o.max || 100;
      const cx = W / 2, cy = H * 0.78, R = Math.min(W / 2 - 6, H * 0.74), r = R * 0.74;
      const ang = v => Math.PI * (1 + (Math.min(Math.max(v, min), max) - min) / (max - min));
      const arc = (v0, v1, col) => {
        const a0 = ang(v0), a1 = ang(v1);
        const p = (rad, a) => [cx + rad * Math.cos(a), cy + rad * Math.sin(a)];
        const [x0, y0] = p(R, a0), [x1, y1] = p(R, a1), [x2, y2] = p(r, a1), [x3, y3] = p(r, a0);
        svg('path', { d: `M${x0},${y0}A${R},${R} 0 0 1 ${x1},${y1}L${x2},${y2}A${r},${r} 0 0 0 ${x3},${y3}Z`, fill: col }, root);
      };
      arc(min, max, C.g1);
      const th = o.thresholds || [70, 85];
      const v = o.value === null || o.value === undefined ? min : o.value;
      const col = v >= th[1] ? C.red : v >= th[0] ? C.amber : C.green;
      if (v > min) arc(min, v, col);
      th.forEach(t => {
        const a = ang(t);
        svg('line', { x1: cx + (r - 3) * Math.cos(a), y1: cy + (r - 3) * Math.sin(a), x2: cx + (R + 3) * Math.cos(a), y2: cy + (R + 3) * Math.sin(a), stroke: C.black, 'stroke-width': 1.2 }, root);
      });
      text(root, cx, cy - (o.label ? 4 : 2), fmt(v, o.format) + (o.unit || ''), { class: 'gauge-value', 'text-anchor': 'middle', fill: v >= th[1] ? C.red : C.ink });
      if (o.label) text(root, cx, cy + 16, o.label, { class: 'gauge-label', 'text-anchor': 'middle' });
    });
  }

  // ---------------------------------------------------------------- heatmap
  function heatColor(v, lo, mid, hi) {
    // white -> grey -> amber -> red
    const stops = [[lo, [246, 246, 246]], [mid * 0.82, [216, 218, 218]], [mid, [237, 112, 0]], [hi, [238, 0, 0]]];
    if (v <= stops[0][0]) return `rgb(${stops[0][1]})`;
    for (let i = 1; i < stops.length; i++) {
      if (v <= stops[i][0]) {
        const [a, ca] = stops[i - 1], [b, cb] = stops[i];
        const t = (v - a) / Math.max(b - a, 1e-9);
        return `rgb(${ca.map((c, k) => Math.round(c + (cb[k] - c) * t)).join(',')})`;
      }
    }
    return `rgb(${stops[3][1]})`;
  }
  function heatmap(el, o) {
    return mount(el, o, (root, W, H) => {
      const rows = o.rows || [], cols = o.cols || [];
      const m = { t: 8, r: 8, b: 26, l: o.marginLeft || 70 };
      const cw = (W - m.l - m.r) / Math.max(cols.length, 1), ch = (H - m.t - m.b) / Math.max(rows.length, 1);
      const lo = o.min || 0, mid = o.mid || 85, hi = o.max || 100;
      rows.forEach((rl, i) => {
        text(root, m.l - 8, m.t + i * ch + ch / 2 + 4, rl, { class: 'axis-label', 'text-anchor': 'end' });
        cols.forEach((cl, j) => {
          const v = o.values[i][j];
          const rect = svg('rect', { x: m.l + j * cw + 1, y: m.t + i * ch + 1, width: Math.max(cw - 2, 1), height: Math.max(ch - 2, 1), rx: 3, fill: heatColor(v, lo, mid, hi) }, root);
          if (o.showValues !== false && cw > 30 && ch > 16) text(root, m.l + j * cw + cw / 2, m.t + i * ch + ch / 2 + 4, fmt(v, o.format), { class: 'heat-value', 'text-anchor': 'middle', fill: v >= mid * 0.95 ? '#fff' : C.ink });
          rect.addEventListener('mousemove', ev => showTip(`<b>${rl} · ${cl}</b><div>${o.valueLabel || 'Value'}: ${fmt(v, o.format)}</div>${o.extra ? o.extra(i, j) : ''}`, ev));
          rect.addEventListener('mouseleave', hideTip);
        });
      });
      const every = Math.max(1, Math.ceil(cols.length / Math.floor((W - m.l) / 46)));
      cols.forEach((cl, j) => { if (j % every === 0) text(root, m.l + j * cw + cw / 2, H - 8, cl, { class: 'axis-label', 'text-anchor': 'middle' }); });
    });
  }

  // ---------------------------------------------------------------- sparkline
  function sparkline(el, o) {
    return mount(el, Object.assign({ height: o.height || 36 }, o), (root, W, H) => {
      const v = (o.values || []).filter(x => x !== null);
      if (!v.length) return;
      const lo = o.min !== undefined ? o.min : Math.min(...v), hi = o.max !== undefined ? o.max : Math.max(...v);
      const n = o.values.length;
      const X = i => 2 + i / Math.max(n - 1, 1) * (W - 4), Y = x => H - 3 - (x - lo) / Math.max(hi - lo, 1e-9) * (H - 6);
      let d = '';
      o.values.forEach((x, i) => { if (x !== null) d += (d ? 'L' : 'M') + X(i).toFixed(1) + ',' + Y(x).toFixed(1); });
      if (o.fill !== false) svg('path', { d: d + `L${X(n - 1)},${H}L${X(0)},${H}Z`, fill: o.color || C.red, opacity: 0.1 }, root);
      svg('path', { d, fill: 'none', stroke: o.color || C.red, 'stroke-width': 1.6 }, root);
      if (o.threshold !== undefined) svg('line', { x1: 0, x2: W, y1: Y(o.threshold), y2: Y(o.threshold), stroke: C.red, 'stroke-dasharray': '2 3', 'stroke-width': 1 }, root);
      const last = o.values[n - 1];
      if (last !== null) svg('circle', { cx: X(n - 1), cy: Y(last), r: 2.5, fill: o.color || C.red }, root);
    });
  }

  // ---------------------------------------------------------------- funnel
  function funnel(el, o) {
    return mount(el, o, (root, W, H) => {
      const st = o.stages || [];
      const max = Math.max(...st.map(s => s.n), 1);
      const lw = Math.min(190, W * 0.32), rh = (H - 8) / Math.max(st.length, 1);
      st.forEach((s, i) => {
        const w = (W - lw - 70) * s.n / max;
        const y = 4 + i * rh;
        const col = s.color || (i === st.length - 1 ? C.amber : i >= st.length - 2 ? C.green : i === 0 ? C.black : C.g4);
        text(root, lw - 10, y + rh / 2 + 4, s.stage, { class: 'axis-label strong', 'text-anchor': 'end' });
        const rect = svg('rect', { x: lw, y: y + 4, width: Math.max(w, 2), height: rh - 8, rx: 4, fill: col }, root);
        const pct = i > 0 && s.of !== undefined ? ` · ${(100 * s.n / Math.max(s.of, 1)).toFixed(0)}%` : '';
        text(root, lw + Math.max(w, 2) + 8, y + rh / 2 + 4, fmt(s.n, v => v.toLocaleString()) + pct, { class: 'bar-value strong' });
        rect.addEventListener('mousemove', ev => showTip(`<b>${s.stage}</b><div>${s.n.toLocaleString()} decisions${pct}</div>${s.note ? '<div>' + s.note + '</div>' : ''}`, ev));
        rect.addEventListener('mouseleave', hideTip);
      });
    });
  }

  window.Charts = { line, area, bar, bars, stackedBar, donut, gauge, heatmap, sparkline, funnel, colors: C, palette: PALETTE, fmt, heatColor, hideTip };
})();
