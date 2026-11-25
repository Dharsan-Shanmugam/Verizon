/* App shell: hash router, API client, formatting + DOM helpers. */
(function () {
  'use strict';
  const cache = new Map();
  const App = {
    views: {},
    current: null,
    timers: [],
    aiSelection: {},
    aiModel: '',
    aiCleanup: null,
    aiContextChanged: null,
    routeEpoch: 0,

    setAISelection(selection) {
      if (JSON.stringify(App.aiSelection) === JSON.stringify(selection)) return;
      App.aiSelection = selection;
      if (App.aiContextChanged) App.aiContextChanged();
    },

    mountAI(el, view) {
      const panel = App.h(`<section class="card ai-panel" aria-label="Claude advisory analyst">
        <div class="card-head"><h2>Claude analyst</h2><span class="pill info">Advisory only</span></div>
        <p class="small muted">Sends this view's synthetic facts and your question to Anthropic.
          Does not change forecasts, approvals, or network settings. API usage may incur charges.
          Do not enter confidential information.</p>
        <div class="toolbar"><button class="btn ghost ai-models">Load available models</button>
          <label>Model <select class="ai-model" aria-label="Claude model"><option value="">Select a model</option></select></label>
          ${view === 'simulator' ? '<label><input type="checkbox" class="ai-auto" checked> Auto-analyze scenario changes (2 s debounce; billed)</label>' : ''}</div>
        <label class="field">Question
          <textarea class="ai-question" rows="2" maxlength="2000">Explain the key facts, risks, and safe next steps for this view.</textarea>
        </label>
        <div class="toolbar"><button class="btn primary ai-run" disabled>Ask Claude</button>
          <button class="btn ghost ai-clear">Clear conversation</button></div>
        <p class="small ai-status" role="status">Checking server configuration...</p>
        <div class="ai-conversation" aria-live="polite"></div>
      </section>`);
      el.appendChild(panel);
      const select = panel.querySelector('.ai-model');
      const state = panel.querySelector('.ai-status');
      const run = panel.querySelector('.ai-run');
      const load = panel.querySelector('.ai-models');
      const auto = panel.querySelector('.ai-auto');
      let history = [], timer = null, busy = false, disposed = false, revision = 0, pending = false;
      const clearAnswer = () => {
        history = [];
        panel.querySelector('.ai-conversation').replaceChildren();
      };
      const schedule = () => {
        clearTimeout(timer);
        if (!auto || !auto.checked || !select.value || run.disabled && !busy) return;
        if (busy) { pending = true; return; }
        state.textContent = 'Scenario changed. Claude analysis scheduled in 2 seconds...';
        timer = setTimeout(() => ask(true), 2000);
      };
      const ask = async (automatic = false) => {
        clearTimeout(timer);
        if (!select.value) { state.textContent = 'Select a model first.'; return; }
        if (busy) return;
        const question = panel.querySelector('.ai-question').value.trim();
        if (!question) { state.textContent = 'Enter a question.'; return; }
        if (App.views[view].beforeAI) App.views[view].beforeAI();
        const requestRevision = revision;
        busy = true;
        run.disabled = true;
        state.textContent = automatic ? 'Auto-analyzing the rendered scenario...' : 'Requesting Claude analysis...';
        try {
          const result = await App.post('/api/ai/explain', {
            view, model: select.value, question, history,
            selection: JSON.parse(JSON.stringify(App.aiSelection))
          });
          if (disposed || requestRevision !== revision) return;
          const turn = App.h(`<article class="ai-turn"><b>${App.esc(question)}</b><pre class="ai-answer"></pre></article>`);
          turn.querySelector('pre').textContent = result.text;
          panel.querySelector('.ai-conversation').appendChild(turn);
          history.push({ role: 'user', content: question }, { role: 'assistant', content: result.text.slice(0, 6000) });
          history = history.slice(-4);
          state.textContent = `Model: ${result.model}. Advisory only; verify before acting.`
            + (result.truncated ? ' Response reached its token limit.' : '');
        } catch (e) {
          if (disposed) return;
          if (auto) auto.checked = false;
          pending = false;
          state.textContent = e.message + ' Automatic analysis paused; fix the issue before retrying.';
        } finally {
          busy = false;
          if (!disposed) {
            run.disabled = false;
            if (pending) { pending = false; schedule(); }
          }
        }
      };
      App.aiContextChanged = () => {
        revision++;
        clearAnswer();
        state.textContent = 'Context changed. Previous advice cleared; ask about the current selection.';
        schedule();
      };
      App.aiCleanup = () => { disposed = true; clearTimeout(timer); App.aiContextChanged = null; };
      App.api('/api/ai/status?current=1').then(s => {
        if (disposed) return;
        const model = App.aiModel || s.model;
        if (model) select.add(new Option(model, model, true, true));
        state.textContent = s.configured ? 'Ready. Select a model, then request an explanation.'
          : 'Set ANTHROPIC_API_KEY in the root .env file and restart. Existing dashboard features work without it.';
        run.disabled = load.disabled = !s.configured;
      }).catch(e => { if (!disposed) { state.textContent = e.message; run.disabled = load.disabled = true; } });
      load.addEventListener('click', async () => {
        load.disabled = true;
        state.textContent = 'Loading model IDs from Anthropic...';
        try {
          const result = await App.post('/api/ai/models', {});
          if (disposed) return;
          const previous = select.value;
          select.replaceChildren(new Option('Select a model', ''));
          result.models.forEach(m => select.add(new Option(`${m.name} (${m.id})`, m.id)));
          if (result.models.some(m => m.id === previous)) select.value = previous;
          state.textContent = 'Select your model. No model is selected automatically.';
        } catch (e) { state.textContent = e.message; }
        finally { load.disabled = false; }
      });
      select.addEventListener('change', () => {
        App.aiModel = select.value; revision++; clearAnswer(); schedule();
      });
      if (auto) auto.addEventListener('change', () => {
        if (auto.checked) schedule();
        else { clearTimeout(timer); pending = false; state.textContent = 'Automatic analysis paused. Ask Claude manually when needed.'; }
      });
      panel.querySelector('.ai-clear').addEventListener('click', () => {
        revision++; clearTimeout(timer); pending = false; clearAnswer(); state.textContent = 'Conversation cleared.';
      });
      run.addEventListener('click', () => ask(false));
    },

    async api(path, opts) {
      const useCache = !opts && !path.includes('?') && !path.startsWith('/api/live') && !path.startsWith('/api/audit');
      if (useCache && cache.has(path)) return cache.get(path);
      const res = await fetch(path, opts ? Object.assign({ headers: { 'Content-Type': 'application/json' } }, opts) : undefined);
      if (!res.ok) {
        let msg = res.statusText;
        try { msg = (await res.json()).error || msg; } catch (e) { /* ignore */ }
        throw new Error(`${res.status} ${msg}`);
      }
      const data = await res.json();
      if (useCache) cache.set(path, data);
      return data;
    },
    post(path, body) { return App.api(path, { method: 'POST', body: JSON.stringify(body || {}) }); },

    // ----- helpers
    h(html) { const t = document.createElement('template'); t.innerHTML = html.trim(); return t.content.firstElementChild; },
    esc(s) { return String(s === null || s === undefined ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); },
    num(v, d) {
      if (v === null || v === undefined || Number.isNaN(+v)) return '–';
      return (+v).toLocaleString('en-US', { minimumFractionDigits: d || 0, maximumFractionDigits: d || 0 });
    },
    money(v) {
      if (v === null || v === undefined) return '–';
      if (Math.abs(v) >= 1e6) return '$' + (v / 1e6).toFixed(1) + 'M';
      if (Math.abs(v) >= 1e3) return '$' + (v / 1e3).toFixed(0) + 'k';
      return '$' + Math.round(v);
    },
    pct(v, d) { return v === null || v === undefined ? '–' : (+v).toFixed(d === undefined ? 0 : d) + '%'; },
    short(v) {
      const a = Math.abs(v);
      if (a >= 1e6) return (v / 1e6).toFixed(1) + 'M';
      if (a >= 1e4) return (v / 1e3).toFixed(1) + 'k';
      return App.num(v);
    },
    tierClass(t) { return t ? 'tier' + (String(t).match(/tier (\d)/) || [0, 0])[1] : 'tier0'; },
    tierShort(t) { return t ? String(t).replace(' - ', ' · ') : ''; },
    riskPill(r) { return `<span class="pill ${App.esc(r)}">${App.esc(r)}</span>`; },
    haPill(h) { return `<span class="pill ${h === 'home' ? 'home' : h === 'away' ? 'away' : 'neutral'}">${App.esc(h)}</span>`; },
    kpiTile(k) {
      let v = k.value, unit = k.unit || '', disp;
      if (k.display !== undefined) disp = k.display;
      else if (unit === 'USD') { disp = App.money(v); unit = ''; }
      else if (unit === 'min' || unit === 'h') disp = App.short(v);
      else disp = (k.prefix || '') + App.num(v, Math.abs(v) < 10 && v % 1 ? (Math.abs(v) < 1 ? 2 : 1) : (v % 1 ? 1 : 0));
      return `<div class="kpi"><div class="kpi-label">${App.esc(k.label)}</div>
        <div class="kpi-value ${k.red === false ? '' : 'red'}">${disp}<span class="u">${App.esc(unit === 'min' ? 'min' : unit)}</span></div>
        <div class="kpi-sub">${App.esc(k.sub || '')}</div></div>`;
    },
    head(eyebrow, title, takeaway, right) {
      return `<div class="page-head"><div><div class="eyebrow">${App.esc(eyebrow)}</div><h1>${App.esc(title)}</h1></div>
        ${right ? `<div>${right}</div>` : ''}</div>
        <p class="takeaway">${takeaway ? App.esc(takeaway) : ''}</p>`;
    },
    toast(msg) {
      const t = App.h(`<div class="toast">${App.esc(msg)}</div>`);
      document.body.appendChild(t);
      setTimeout(() => t.remove(), 3200);
    },
    every(ms, fn) { const id = setInterval(fn, ms); App.timers.push(id); return id; },
    debounce(fn, ms) { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; },

    // ----- router
    async route() {
      const epoch = ++App.routeEpoch;
      const hash = location.hash.replace(/^#\/?/, '') || 'forecast';
      const [name, ...rest] = hash.split('/');
      const view = App.views[name] ? name : 'forecast';
      if (App.aiCleanup) { App.aiCleanup(); App.aiCleanup = null; }
      App.timers.forEach(clearInterval); App.timers = [];
      if (App.current && App.views[App.current] && App.views[App.current].destroy) App.views[App.current].destroy();
      Charts.hideTip();
      const dialog = document.getElementById('executive-dialog');
      if (dialog && dialog.open) dialog.close();
      App.current = view;
      App.aiSelection = {};
      document.querySelectorAll('#nav a').forEach(a => a.classList.toggle('active', a.dataset.view === view));
      const host = document.getElementById('view');
      const el = App.h('<div class="view-content" tabindex="-1"><div class="loading">Loading…</div></div>');
      host.replaceChildren(el);
      const t0 = performance.now();
      try {
        await App.views[view].render(el, rest.map(decodeURIComponent));
        if (epoch !== App.routeEpoch || !el.isConnected) return;
        el.dataset.renderMs = Math.round(performance.now() - t0);
      } catch (e) {
        if (epoch !== App.routeEpoch || !el.isConnected) return;
        console.error(e);
        el.innerHTML = `<div class="error-box"><b>Could not load this view.</b><br>${App.esc(e.message)}<br>
          <span class="small muted">Is the server running (python -m vdi.server) and has the pipeline been trained?</span></div>`;
      }
      el.focus({ preventScroll: true });
      window.scrollTo(0, 0);
    },
    async start() {
      window.addEventListener('hashchange', App.route);
      try {
        const h = await App.api('/api/health');
        const d = new Date(h.as_of + 'T12:00:00');
        document.getElementById('asof').textContent = `Model ${h.model_version} · as of ${d.toLocaleDateString('en-US', { day: 'numeric', month: 'short', year: 'numeric' })}`;
      } catch (e) { /* shown by view */ }
      App.route();
    },
  };
  window.App = App;
})();
