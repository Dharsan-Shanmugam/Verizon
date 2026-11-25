(function () {
  'use strict';
  const alerts = [];
  const incidentNotices = new Map();
  let investigationReturnFocus = null;
  function mountNotice(node) {
    const host = document.getElementById('notifications');
    host.appendChild(node);
    while (host.children.length > 2) host.firstElementChild.remove();
  }
  window.Exec = {
    clearPopups() {
      document.getElementById('notifications').replaceChildren();
      incidentNotices.clear();
    },
    pill(text, tone = '') { return `<span class="pill ${tone}">${App.esc(text)}</span>`; },
    metric(label, value, unit, detail, tone = '') {
      return `<article class="metric ${tone}"><div class="metric-label">${App.esc(label)}</div>
        <div class="metric-value">${App.esc(value)}<small>${App.esc(unit)}</small></div><div class="metric-detail">${App.esc(detail)}</div></article>`;
    },
    clock(seconds, start) {
      const time = new Date(start.replace(' ', 'T') + ':00');
      return new Date(time.getTime() + seconds * 1000).toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', hour12: false });
    },
    alert(title, message, action) {
      alerts.unshift({ title, message });
      if (alerts.length > 30) alerts.pop();
      document.getElementById('alerts-button').classList.add('unread');
      const node = App.h(`<aside class="notice"><div><b>${App.esc(title)}</b><p>${App.esc(message)}</p></div>
        ${action ? '<button class="btn small review">Review</button>' : ''}<button class="icon-button dismiss" aria-label="Dismiss notification">&times;</button></aside>`);
      mountNotice(node);
      node.querySelector('.dismiss').onclick = () => node.remove();
      if (action) node.querySelector('.review').onclick = () => { node.remove(); action(); };
      setTimeout(() => node.remove(), 10000);
    },
    incident(key, update) {
      let record = alerts.find(item => item.key === key);
      if (!record) {
        record = { key };
        alerts.unshift(record);
        if (alerts.length > 30) alerts.pop();
      }
      const signature = update.state + ':' + update.tier + ':' + update.target;
      const changed = record.signature !== signature;
      Object.assign(record, update, { signature });
      let notice = incidentNotices.get(key);
      if (changed && update.attention) {
        document.getElementById('alerts-button').classList.add('unread');
        if (!notice || !notice.isConnected) {
          notice = App.h('<aside class="notice incident-notice" role="status"><div class="incident-notice-copy"></div><button class="btn small review">Investigate</button><button class="icon-button dismiss" aria-label="Dismiss notification">&times;</button></aside>');
          mountNotice(notice);
          incidentNotices.set(key, notice);
          notice.querySelector('.dismiss').onclick = () => { notice.remove(); incidentNotices.delete(key); };
          setTimeout(() => { notice.remove(); if (incidentNotices.get(key) === notice) incidentNotices.delete(key); }, 12000);
        }
      }
      if (notice?.isConnected) {
        notice.querySelector('.incident-notice-copy').innerHTML = `${Exec.pill(update.label, update.tone)}<b>${App.esc(update.title)}</b><p>${App.esc(update.message)}</p>`;
        notice.querySelector('.review').onclick = () => { notice.remove(); incidentNotices.delete(key); record.action(); };
      }
      if (document.getElementById('executive-dialog').open && document.getElementById('dialog-title').textContent === 'Notifications') Exec.renderNotifications();
    },
    renderNotifications() {
      document.getElementById('dialog-body').innerHTML = alerts.length ? alerts.map((a, i) => `<div class="alert-item">
        ${a.label ? Exec.pill(a.label, a.tone) : ''}<b>${App.esc(a.title)}</b><p>${App.esc(a.message)}</p>
        ${a.key ? `<button class="btn small" data-notification="${i}">Investigate</button>` : ''}</div>`).join('')
        : '<p class="muted">You are up to date. Preparation and live issue alerts appear here.</p>';
      document.querySelectorAll('[data-notification]').forEach(button => button.onclick = () => {
        document.getElementById('executive-dialog').close();
        alerts[+button.dataset.notification].action();
      });
    },
    openInvestigation() {
      const panel = document.getElementById('investigation-panel');
      if (panel.hidden) investigationReturnFocus = document.activeElement;
      document.getElementById('executive-dialog').close();
      panel.hidden = false;
      panel.focus({ preventScroll: true });
    },
    closeInvestigation() {
      document.getElementById('investigation-panel').hidden = true;
      if (investigationReturnFocus?.isConnected) investigationReturnFocus.focus({ preventScroll: true });
      investigationReturnFocus = null;
    },
    dialog(title, html, buttons = []) {
      const dialog = document.getElementById('executive-dialog');
      document.getElementById('dialog-title').textContent = title;
      document.getElementById('dialog-body').innerHTML = html;
      const actions = document.getElementById('dialog-actions');
      actions.replaceChildren();
      buttons.forEach(({ label, primary, run }) => {
        const button = document.createElement('button');
        button.textContent = label;
        button.className = 'btn ' + (primary ? 'primary' : '');
        button.onclick = async () => {
          const label = button.textContent;
          button.textContent = 'Saving...';
          actions.querySelectorAll('button').forEach(item => { item.disabled = true; });
          const previous = document.querySelector('#dialog-body .inline-error');
          if (previous) previous.remove();
          try { await run(); dialog.close(); }
          catch (error) {
            const notice = document.createElement('p');
            notice.className = 'inline-error'; notice.setAttribute('role', 'alert');
            notice.textContent = error.message;
            document.getElementById('dialog-body').appendChild(notice);
          } finally {
            button.textContent = label;
            actions.querySelectorAll('button').forEach(item => { item.disabled = false; });
          }
        };
        actions.appendChild(button);
      });
      if (!dialog.open) dialog.showModal();
    },
    evidence(report) {
      Exec.dialog('Forecast evidence', `<p class="muted">Dedicated load models. Chronological event split; synthetic data only.</p>
        <div class="evidence-counts"><b>${report.split.train.length} training events</b><b>${report.split.calibration.length} calibration</b><b>${report.split.test.length} unseen test events</b></div>
        <table class="table"><thead><tr><th>Horizon</th><th>MAE</th><th>Hold-current MAE</th><th>90% coverage</th></tr></thead><tbody>
        ${Object.entries(report.horizons).map(([h, m]) => `<tr><td>${+h / 60} min</td><td>${m.mae} pts</td><td>${m.persistence_mae} pts</td><td>${m.coverage_pct}%</td></tr>`).join('')}</tbody></table>
        <h3 class="case-section-title">Congestion-rule forecast scorecard</h3><p class="muted">${App.esc(report.risk_definition)}</p>
        <table class="table"><thead><tr><th>Future bin</th><th>AUC</th><th>Precision</th><th>Recall</th><th>Brier</th></tr></thead><tbody>${Object.entries(report.horizons).map(([h, m]) => `<tr><td>${+h / 60 - 5}-${+h / 60} min</td><td>${m.congestion_risk.auc}</td><td>${m.congestion_risk.precision}</td><td>${m.congestion_risk.recall}</td><td>${m.congestion_risk.brier}</td></tr>`).join('')}</tbody></table>
        <p class="muted">Uncertainty comes from separate calibration events. Dashed chart segments connect direct 5-minute predictions; they are not per-second forecasts. Risk metrics use held-out synthetic events, not real-network SLA validation.</p>
        <p class="muted">${App.esc(report.limitations.join('. '))}.</p>`);
    },
    error(error) { Exec.alert('Action needs attention', error.message); }
  };
  document.getElementById('dialog-close').onclick = () => document.getElementById('executive-dialog').close();
  document.getElementById('investigation-close').onclick = () => Exec.closeInvestigation();
  document.getElementById('investigation-panel').addEventListener('keydown', event => {
    if (event.key === 'Escape') { event.preventDefault(); Exec.closeInvestigation(); }
  });
  document.getElementById('alerts-button').onclick = () => {
    document.getElementById('alerts-button').classList.remove('unread');
    Exec.dialog('Notifications', '');
    Exec.renderNotifications();
  };
})();
