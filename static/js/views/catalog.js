/* 08 Data catalog */
(function () {
  'use strict';
  App.views.catalog = {
    async render(el) {
      const d = await App.api('/api/catalog');
      const totalRows = d.files.reduce((a, f) => a + f.rows, 0);
      el.innerHTML = `
        ${App.head('Data catalog', 'What the PoC runs on', d.takeaway)}
        <div class="kpis">
          ${App.kpiTile({ label: 'Files', value: d.files.length, red: false, sub: 'CSV, synthetic, documented in 00_data_dictionary' })}
          ${App.kpiTile({ label: 'Rows', value: totalRows, unit: '', red: false, sub: 'across all files' })}
          ${App.kpiTile({ label: 'Documented columns', value: d.columns.length, red: false, sub: 'type and description per column' })}
          ${App.kpiTile({ label: 'Excluded from models', value: d.columns.filter(c => /DO NOT use/i.test(c.description)).length, red: false, sub: 'simulator ground-truth columns never used as features' })}
        </div>
        <div class="card" style="margin-bottom:20px"><div class="card-head"><div><h2>Files</h2><div class="card-sub">Source folder: ${App.esc(d.data_dir)}</div></div>
          <input type="search" id="ct-q" placeholder="Search files and columns…" style="width:300px"></div>
          <div class="scroll tall"><table class="tbl" id="ct-files"></table></div></div>
        <div class="card"><div class="card-head"><div><h2>Column dictionary</h2><div class="card-sub" id="ct-count"></div></div></div>
          <div class="scroll tall"><table class="tbl" id="ct-cols"></table></div></div>`;
      const draw = (q) => {
        q = (q || '').toLowerCase();
        const files = d.files.filter(f => !q || (f.file + ' ' + f.contents + ' ' + f.grain).toLowerCase().includes(q) ||
          d.columns.some(c => c.file === f.file && (c.column + ' ' + c.description).toLowerCase().includes(q)));
        el.querySelector('#ct-files').innerHTML = `<thead><tr><th>File</th><th>Contents</th><th>Grain</th><th>Coverage</th><th class="num">Rows</th><th class="num">Cols</th></tr></thead><tbody>
          ${files.map(f => `<tr class="clickable" data-f="${App.esc(f.file)}"><td class="nowrap"><b>${App.esc(f.file)}</b></td><td>${App.esc(f.contents)}</td><td class="small">${App.esc(f.grain)}</td>
            <td class="small nowrap">${App.esc(f.coverage)}</td><td class="num">${App.num(f.rows)}</td><td class="num">${f.columns}</td></tr>`).join('')}</tbody>`;
        el.querySelectorAll('#ct-files tr.clickable').forEach(tr => tr.addEventListener('click', () => { const i = el.querySelector('#ct-q'); i.value = tr.dataset.f; draw(tr.dataset.f); }));
        const cols = d.columns.filter(c => !q || (c.file + ' ' + c.column + ' ' + c.description).toLowerCase().includes(q));
        el.querySelector('#ct-count').textContent = `${App.num(cols.length)} of ${App.num(d.columns.length)} columns`;
        el.querySelector('#ct-cols').innerHTML = `<thead><tr><th>File</th><th>Column</th><th>Type</th><th>Description</th></tr></thead><tbody>
          ${cols.slice(0, 600).map(c => `<tr><td class="small nowrap">${App.esc(c.file)}</td><td class="nowrap"><b>${App.esc(c.column)}</b></td><td class="small">${App.esc(c.type)}</td>
            <td class="${/DO NOT use/i.test(c.description) ? 'delta-bad' : ''}">${App.esc(c.description)}</td></tr>`).join('')}</tbody>`;
      };
      el.querySelector('#ct-q').addEventListener('input', App.debounce(e => draw(e.target.value), 120));
      draw('');
    }
  };
})();
