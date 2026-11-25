(function () {
  'use strict';
  let live = null, generation = 0;
  const situationEvidence = (issue, outlook) => {
    const groups = issue.diagnostic_groups;
    const demand = groups.find(g => g.id === 'demand')?.metrics;
    const neighbors = groups.find(g => g.id === 'mobility')?.neighbors || [];
    const q = issue.quality;
    const format = (value, unit, assess) => Number.isFinite(value)
      ? `${assess(value)} (${value.toFixed(1)}${unit})` : 'Unavailable';
    const predictions = outlook.filter(p => p.minutes > 0).map(p => p.prediction);
    const peak = predictions.length && predictions.every(Number.isFinite) ? Math.max(...predictions) : NaN;
    const change = demand?.['Active user change (up to 2 min) %'];
    const safeNeighbors = neighbors.filter(n => n.eligible && n.available === true &&
      Number.isFinite(n.headroom) && n.headroom >= 15 && Number.isFinite(n.sinr_db) &&
      n.sinr_db >= 5 && Number.isFinite(n.handover_success_pct) && n.handover_success_pct >= 95);
    const target = safeNeighbors.find(n => n.cell_id === issue.remedy?.target) || safeNeighbors[0];
    const rows = [
      ['UL PRB forecast / next 30 min peak', format(peak, '%', v => v >= 85 ? 'High' : v >= 70 ? 'Elevated' : 'Within range')],
      ['Active UL users', format(change, '% over up to 2 min', v => v > 0 ? 'Increasing' : v < 0 ? 'Decreasing' : 'Unchanged')],
      ['PUSCH SINR', format(q.pusch_sinr_db, ' dB', v => v < 5 ? 'Low / degraded' : 'Within policy range')],
      ['UL BLER', format(q.ul_bler_pct, '%', v => v > 10 ? 'Elevated' : 'Within policy range')],
      [target ? `Neighbor ${target.cell_id} headroom` : 'Neighbor headroom',
        target ? `Available (${target.headroom.toFixed(1)}%); quality and mobility gates pass`
          : issue.rule === 'DATA_QUALITY' ? 'Insufficient evidence' : 'No configured neighbor passes the headroom, quality and mobility gates'],
      ['Transport utilization', Number.isFinite(issue.site_util) && Number.isFinite(issue.agg_util)
        ? `${issue.site_util > 85 || issue.agg_util > 90 ? 'Elevated' : 'Within policy range'} (site ${issue.site_util.toFixed(1)}% / shared ${issue.agg_util.toFixed(1)}%)`
        : 'Unavailable']
    ];
    if (issue.rule === 'DATA_QUALITY') rows.forEach(row => { row[1] = 'Insufficient evidence / telemetry gap'; });
    if (issue.rule === 'TDD_UPLINK') rows.push(['TDD uplink slots',
      Number.isFinite(q.tdd_ul_demand_pct) && Number.isFinite(q.tdd_ul_share_pct)
        ? `Demand ${q.tdd_ul_demand_pct.toFixed(0)}% / allocated ${q.tdd_ul_share_pct.toFixed(0)}%`
        : 'Unavailable']);
    if (['TRANSPORT_CAPACITY', 'TRANSPORT_QUALITY', 'SERVICE_DEGRADATION'].includes(issue.rule)) {
      rows.push(['Transport delay', format(q.transport_latency_ms, ' ms', v => v > 30 ? 'Elevated' : 'Within policy range')],
        ['Transport packet loss', format(q.transport_packet_loss_pct, '%', v => v > 1 ? 'Elevated' : 'Within policy range')]);
    }
    if (issue.rule === 'COMPUTE_PRESSURE') rows.push(['Compute utilization', format(q.compute_util_pct, '%', v => v >= 90 ? 'High' : 'Within policy range')]);
    if (issue.rule === 'AVAILABILITY') rows.push(['Cell availability', q.cell_available === true ? 'Available' : q.cell_available === false ? 'Unavailable' : 'Evidence unavailable']);
    return rows;
  };
  const situationBrief = (issue, workflow, action, remedy) => {
    const recorded = action && ['verifying', 'verified', 'rolled_back', 'validation_blocked', 'rejected', 'escalated'].includes(workflow.state);
    const title = recorded ? action.issue_title : issue.title;
    const evidence = recorded ? action.reason : issue.reason;
    const causes = {
      RADIO_PRESSURE: 'Demand-driven radio capacity pressure',
      TDD_UPLINK: 'Uplink demand exceeds allocated TDD time slots',
      TRANSPORT_CAPACITY: 'Backhaul capacity pressure',
      TRANSPORT_QUALITY: 'Transport path or queue impairment',
      RADIO_ERRORS: 'Radio interference or coverage impairment',
      COVERAGE: 'Coverage-related radio impairment',
      COMPUTE_PRESSURE: 'Processing capacity pressure',
      AVAILABILITY: 'Cell availability fault',
      DATA_QUALITY: 'Missing or invalid telemetry',
      SERVICE_DEGRADATION: 'Service degradation / cause not yet established',
      FORECAST_WATCH: 'Predicted congestion risk / cause not yet established'
    };
    let cause = recorded ? evidence : causes[issue.rule] || 'No persistent rule breach is currently detected.';
    let handling;
    switch (workflow.state) {
      case 'verifying':
        handling = `${action.automatic ? 'Agent-authorized' : 'Engineer-approved'} simulation is being checked every 15 replay seconds for service recovery and safety. It is not yet verified.`;
        break;
      case 'verified':
        handling = `${action.automatic ? 'Agent-authorized' : 'Engineer-approved'} simulation passed its validation checks. Recorded live measurements remain unchanged.`;
        break;
      case 'rolled_back':
        handling = 'Validation failed, so the simulation was rolled back and automatic retries are blocked. Specialist review is needed.';
        break;
      case 'validation_blocked':
        handling = 'Evidence needed for validation is unavailable. Success cannot be claimed; execution is held for specialist review.';
        break;
      case 'rejected':
        handling = 'The proposed action was rejected. Execution is held for this run; it will not automatically retry.';
        break;
      case 'escalated':
        handling = `Specialist ownership is recorded: ${evidence}. Specialists must review the evidence before selecting a remedy.`;
        break;
      case 'escalation_required':
        handling = `Specialist ownership is required: ${issue.reason}. Next: ${issue.recommended_next_step}`;
        break;
      case 'reserved':
        handling = 'Another action reserves this cell or its neighbor. Overlapping changes are blocked during the safety cooldown.';
        break;
      case 'recording_ending':
        handling = 'Too little recorded time remains to complete the 120-second validation window. No new remedy can safely start.';
        break;
      case 'engineer_review':
        handling = remedy?.id === 'tdd_ul_profile'
          ? 'Changing uplink slots can affect neighboring cells and download capacity, so an engineer must review the alignment and downlink checks before approval.'
          : 'The proposed steering is outside the automatic scope: high source load or a move between sites requires engineer review of the projected impact.';
        break;
      case 'automatic_ready':
        handling = 'A bounded steering profile is within the pre-approved scope and passes radio, transport and service gates. The agent can simulate it, then validate; it cannot claim success in advance.';
        break;
      case 'forecast_watch':
        handling = 'Read-only investigation only. A prediction alone cannot authorize an automatic change or engineer approval.';
        break;
      default:
        handling = 'Continue monitoring. No remedy or approval is currently required.';
    }
    return { title: title === 'Healthy' ? 'No active rule breach' : title, cause, handling };
  };
  App.views.live = {
    investigate(cell, sessionId) {
      if (live?.openInvestigation && (!sessionId || live.session.id === sessionId)) live.openInvestigation(cell).catch(Exec.error);
      else {
        if (sessionId) sessionStorage.setItem('vdi-executive-session', sessionId);
        sessionStorage.setItem('vdi-investigation-cell', cell);
        if (App.current === 'live') App.route();
        else location.hash = '#/live';
      }
    },
    async render(el) {
      const version = ++generation;
      const recording = await App.api('/api/live');
      if (!el.isConnected || generation !== version) return;
      let session;
      const stored = sessionStorage.getItem('vdi-executive-session');
      if (stored) {
        try {
          const snap = await App.api('/api/operations/live?seconds=' + recording.times[0] + '&session_id=' + encodeURIComponent(stored));
          session = snap.session;
        } catch (error) {
          if (!error.message.includes('Start a valid replay session')) throw error;
          Exec.alert('New replay session', 'The previous server session is unavailable.');
        }
      }
      if (!session) {
        session = await App.post('/api/operations/session', {});
        sessionStorage.setItem('vdi-executive-session', session.id);
      }
      if (!el.isConnected || generation !== version) return;
      const startIndex = Math.max(0, recording.times.indexOf(session.seconds));
      const busiest = recording.cells.reduce((a, b) => recording.series[a].prb[startIndex] >= recording.series[b].prb[startIndex] ? a : b);
      live = { el, recording, session, cell: busiest, seconds: session.seconds,
        playing: true, busy: false, speed: 15, accumulator: 0, caseFilter: 'all', caseMinutes: 0, incidentCells: new Set(), snapshot: null };
      const state = live;
      el.innerHTML = `<div class="page-title"><div><div class="eyebrow">02 / OPERATING PICTURE</div><h1>Stay ahead. Respond with control.</h1>
        <p class="subline">A 30-minute outlook and a clear route to resolution.</p></div>
        <div class="toolbar"><button class="btn" id="live-evidence">Confidence & evidence &nearr;</button><button class="btn primary" id="stream-play">Pause stream</button></div></div>
        <section class="executive-brief" id="live-brief" aria-live="polite"></section>
        <div class="stream-end-banner" id="recording-status" role="status" hidden><div><b>Recording complete</b><span>Start a new replay to continue. No future readings are invented.</span></div><button class="btn small primary" id="restart-complete-run">Start new replay</button></div>
        <section class="metrics" id="live-metrics"></section>
        <div class="assurance-strip"><b>CONTROLLED AUTOMATION</b><span>Engineer gates</span><span>Validation & rollback</span><span>Full audit trail</span>${Exec.pill('SIMULATION ONLY')}</div>
        <div class="live-layout"><section class="panel"><div class="panel-head"><div><h2 id="live-chart-title">Live radio load + 30-minute forecast</h2><p class="subline" id="live-chart-meta"></p></div>
          <span class="status-line"><i class="live-dot"></i><span id="stream-clock"></span></span></div>
          <div class="live-chart" id="operations-chart"></div>
          <div class="chart-caption" id="live-chart-caption"></div><div class="risk-bin-strip" id="risk-bins" aria-label="Congestion-rule probability by future five-minute interval"></div>
          <div id="remedy-lifecycle" aria-live="polite"></div>
          <div class="cell-strip" id="operation-cells"></div><div class="scrubber"><span>Event replay</span><input id="stream-position" aria-label="Advance event replay" type="range" min="0" max="${recording.times.length - 1}" step="1"><span id="stream-time"></span>
          <select id="stream-speed" aria-label="Replay speed"><option value="1">1x</option><option value="15" selected>15x replay</option><option value="60">60x replay</option></select></div></section>
          <section class="panel"><div class="panel-head"><h2>Decision priorities</h2><span id="issue-count"></span></div><div class="incident-list" id="issue-list"></div>
          <div class="approval-bar"><div>${Exec.pill('LIVE POLICY ACTIVE', 'good')}<p>Tier 1 auto / Tier 2 engineer / Tier 3 escalate</p></div><a class="btn small" href="#/forecast/EVT-LIVE-20261028">Recommendations</a></div></section></div>
        <div class="live-bottom"><section class="panel"><div class="panel-head"><h2>Safety envelope</h2><button class="btn link" id="policy-details">Policy &nearr;</button></div><div class="panel-body" id="safety-envelope"></div></section>
          <section class="panel"><div class="panel-head"><h2>Resolution overview</h2><button class="btn link" id="reset-session">New replay run</button></div><div class="panel-body" id="resolution-summary"></div></section></div>
        <section class="panel case-history"><div class="panel-head"><div><h2>Investigation history</h2><p class="subline">This run / issue, action, parameters, effect and validation</p></div><span id="case-count"></span></div>
          <div class="case-filters" role="group" aria-label="Investigation filters"><button class="btn small selected" data-case-filter="all">All</button><button class="btn small" data-case-filter="agent_resolved">Agent resolved</button>
            <button class="btn small" data-case-filter="engineer_approved">Engineer approved</button><button class="btn small" data-case-filter="escalated">Escalated</button>
            <select id="case-time" aria-label="History time window"><option value="0">Entire run</option><option value="15">Last 15 replay minutes</option><option value="5">Last 5 replay minutes</option></select>
            <input id="case-search" type="search" aria-label="Search cases" placeholder="Issue or cell"></div><div class="case-table-wrap" id="operations-log"></div></section>`;
      const active = () => live === state && el.isConnected && generation === version;
      const parameterTable = parameters => parameters?.length
        ? `<table class="table"><thead><tr><th>Parameter</th><th>Before</th><th>After</th></tr></thead><tbody>${parameters.map(p => `<tr><td>${App.esc(p.parameter)}</td><td>${App.esc(p.old)} ${App.esc(p.unit)}</td><td><b>${App.esc(p.new)} ${App.esc(p.unit)}</b></td></tr>`).join('')}</tbody></table>`
        : '<p class="muted">Parameter details were not recorded for this legacy action.</p>';
      const setPlaying = value => {
        if (value && state.ended) { el.querySelector('#reset-session').click(); return; }
        if (value && state.approvalReview) {
          state.approvalReview = null;
          state.approvalMessage = 'Replay resumed. Start a new approval review before confirming.';
        }
        state.playing = value;
        el.querySelector('#stream-play').textContent = state.ended ? 'New replay run' : value ? 'Pause stream' : 'Resume stream';
        const resume = el.querySelector('#continue-validation');
        if (resume) resume.hidden = value;
        if (state.snapshot) renderInvestigation();
      };
      const issueDialog = async cell => {
        if (!active()) return;
        state.investigationCell = cell; state.cell = cell;
        state.investigationError = ''; state.investigationRanking = null; state.investigationChanged = false; state.investigationChoice = null;
        state.approvalReview = null; state.approvalMessage = '';
        Exec.openInvestigation();
        renderInvestigation();
        await refresh(false);
      };
      state.openInvestigation = issueDialog;
      const renderInvestigation = () => {
        const panel = document.getElementById('investigation-panel');
        if (!active() || panel.hidden || !state.snapshot || !state.investigationCell) return;
        const snap = state.snapshot, cell = state.investigationCell;
        const issue = snap.issues.find(row => row.cell_id === cell);
        if (!issue) return;
        const workflow = issue.workflow;
        const action = snap.session.actions.find(a => a.id === workflow.action_id);
        const selectedRemedy = issue.candidates.find(c => c.choice_id === state.investigationChoice);
        const remedy = action?.remedy || selectedRemedy || issue.remedy;
        if (state.approvalReview && (state.approvalReview.cell !== cell ||
            state.approvalReview.token !== issue.review_token ||
            state.approvalReview.choice !== remedy?.choice_id || !workflow.can_approve)) {
          state.approvalReview = null;
          state.approvalMessage = 'Evidence or ranking changed. Review the latest option before confirming.';
        }
        const reviewReady = !!state.approvalReview;
        const rank = issue.rule + ':' + issue.tier + ':' + (issue.remedy?.choice_id || '');
        const changed = state.investigationRanking && state.investigationRanking !== rank;
        state.investigationRanking = rank;
        if (changed) state.investigationChanged = true;
        const focused = panel.contains(document.activeElement) ? document.activeElement.id : null;
        const detailsOpen = panel.querySelector('details[data-remedies]')?.open;
        const intentOpen = panel.querySelector('details.intent-detail')?.open;
        const expandedGroups = new Set([...panel.querySelectorAll('details[data-diagnostic]')].filter(d => d.open).map(d => d.dataset.diagnostic));
        const scrollTop = document.getElementById('investigation-content').scrollTop;
        const number = (value, decimals = 1) => Number.isFinite(value) ? value.toFixed(decimals) : 'Unavailable';
        const route = workflow.state === 'verifying' ? 'Tier ' + action.tier + ' / already authorized'
          : workflow.state === 'verified' ? 'Verified / monitoring'
          : ['rejected', 'escalated', 'rolled_back', 'validation_blocked'].includes(workflow.state) ? 'Execution held / specialist ownership'
          : issue.tier === 1 ? 'Automatic / pre-approved limits'
          : issue.tier === 2 ? 'Engineer approval required' : issue.tier === 3 ? 'Specialist escalation' : 'Monitor only';
        const brief = situationBrief(issue, workflow, action, remedy);
        const evidenceRows = situationEvidence(issue, snap.outlook);
        const disabled = state.investigationSaving ? 'disabled' : '';
        document.getElementById('investigation-title').textContent = cell + ' / ' + issue.title;
        document.getElementById('investigation-content').innerHTML = `<p class="subline">Serving cell / ${App.esc(issue.network_object.site_id)} / sector ${App.esc(issue.network_object.sector)} / ${App.esc(issue.network_object.band)}</p><div class="investigation-toolbar">
          ${Exec.pill(workflow.label, workflow.tone)}<span>${Exec.clock(snap.seconds, recording.event_start)} replay</span>
          <button class="btn small" id="investigation-play">${state.ended ? 'New replay run' : state.playing ? 'Pause to review' : 'Resume stream'}</button></div>
          <div class="investigation-ranking ${workflow.tone}"><span class="eyebrow">CURRENT AUTHORITY</span><h3>${App.esc(route)}</h3>
          <p>Controls below follow current evidence, safety and ranking.</p></div>
          ${state.investigationChanged ? '<p class="ranking-change" role="status">Ranking changed. Latest evidence and controls are shown.</p>' : ''}
          <div class="investigation-signals"><div><span>Latency / RTT</span><b>${number(issue.quality.latency_ms)}<small> ms</small></b></div>
            <div><span>Packet loss</span><b>${number(issue.quality.packet_loss_pct, 2)}<small>%</small></b></div>
            <div><span>Radio load</span><b>${number(issue.observed)}%</b></div></div>
          <div class="investigation-step"><span class="investigation-step-icon">1</span><div><h3>Rule evidence</h3><p>${App.esc(issue.reason)}</p></div></div>
          <div class="diagnostic-groups"><h3>Four-part quick RCA / synthetic counters</h3>
            <section class="rca-situation-brief" aria-label="Situation brief" aria-live="polite">
              <div><span class="eyebrow">ISSUE</span><b>${App.esc(brief.title)}</b></div>
              <div><span class="eyebrow">LIKELY CAUSE</span><p>${App.esc(brief.cause)}</p></div>
              <div><span class="eyebrow">EVIDENCE / CURRENT REPLAY</span>
                <dl class="rca-evidence-list">${evidenceRows.map(([label, value]) => `<div><dt>${App.esc(label)}</dt><dd>${App.esc(value)}</dd></div>`).join('')}</dl></div>
              <div><span class="eyebrow">WHY THIS HANDLING</span>${Exec.pill(workflow.label, workflow.tone)}<p>${App.esc(brief.handling)}</p></div>
              <small>Current synthetic counters support, not confirm, the cause. Within range does not prove a stable trend. Neighbor checks do not authorize steering.</small>
            </section>${issue.diagnostic_groups.map(g => `<details data-diagnostic="${g.id}" ${expandedGroups.has(g.id) ? 'open' : ''}><summary><b>${App.esc(g.title)}</b><span>${App.esc(g.assessment)}</span></summary>
            <p class="simulation-note">${App.esc(g.definition)}</p>
            <div class="diagnostic-metrics">${Object.entries(g.metrics).map(([key, value]) => `<div><span>${App.esc(key)}</span><b>${typeof value === 'number' ? number(value) : App.esc(value)}</b></div>`).join('')}</div>
            ${g.neighbors ? `<table class="table"><thead><tr><th>Neighbor</th><th>Headroom</th><th>SINR</th><th>HO success</th></tr></thead><tbody>${g.neighbors.map(n => `<tr><td>${App.esc(n.cell_id)}</td><td>${number(n.headroom)}%</td><td>${number(n.sinr_db)} dB</td><td>${number(n.handover_success_pct)}%</td></tr>`).join('')}</tbody></table>` : ''}
            </details>`).join('')}</div>
          <div class="investigation-step"><span class="investigation-step-icon">2</span><div><h3>${remedy ? 'Rank ' + remedy.rank + ' / ' + App.esc(remedy.title) : issue.forecast_watch ? 'Read-only predictive RCA' : issue.tier === 0 ? 'No remedy required' : 'No executable safe remedy'}</h3>
            <p>${remedy ? (remedy.id === 'tdd_ul_profile' ? 'TDD uplink slots ' + remedy.old_ul_share + '% &rarr; ' + remedy.new_ul_share + '%'
              : App.esc(cell) + ' &rarr; ' + App.esc(remedy.target)) + ' / projected radio relief ' + remedy.relief + ' pts'
              : issue.forecast_watch ? 'Inspect the serving cell and neighbors before choosing any intervention. A forecast is not a diagnosis.'
              : issue.tier === 0 ? 'Signals are within limits. Continue monitoring.' : App.esc(issue.recommended_next_step)}</p>
            ${remedy ? `<div class="investigation-checks">${Exec.pill(remedy.headroom_after + '% headroom after', 'good')}${Exec.pill(remedy.site_after + '% site after', 'good')}${Exec.pill(remedy.agg_after + '% shared', 'good')}</div>` : ''}
          </div></div>
          ${issue.remedies.length ? `<details data-remedies ${detailsOpen ? 'open' : ''}><summary>All possible remedies / safety-ranked</summary>
            <table class="table"><thead><tr><th>Rank</th><th>Remedy</th><th>Route</th><th>Gate</th></tr></thead><tbody>${issue.remedies.map(c => `<tr class="${c.choice_id === remedy?.choice_id ? 'recommended' : ''}"><td>${c.rank}</td><td>${App.esc(c.title)}<small>${App.esc(c.target || c.complexity)}</small></td><td>${c.tier === 0 ? 'Observe' : c.tier === 1 ? 'Agent' : c.tier === 2 ? 'Engineer' : 'Escalate'}</td><td>${!c.eligible ? 'Blocked: ' + App.esc(c.reason) : c.executable ? 'Safe profile' : c.tier === 0 ? 'Read-only' : 'Specialist only'}</td></tr>`).join('')}</tbody></table></details>` : ''}
          ${workflow.can_approve && issue.candidates.length > 1 ? `<label class="remedy-choice">Engineer option<select id="investigation-remedy" ${disabled}>${issue.candidates.filter(c => c.tier === 2).map(c => `<option value="${App.esc(c.choice_id)}" ${c.choice_id === remedy?.choice_id ? 'selected' : ''}>Rank ${c.rank}: ${App.esc(c.title)} / ${App.esc(c.target || 'UL ' + c.new_ul_share + '%')}</option>`).join('')}</select></label>` : ''}
          ${remedy?.historical_evidence ? `<div class="historical-grounding"><b>Historical grounding / ${remedy.historical_evidence.confidence} confidence</b>
            <p>${remedy.historical_evidence.matches ? remedy.historical_evidence.successes + ' / ' + remedy.historical_evidence.matches + ' comparable synthetic outcomes successful' : 'No comparable completed outcomes; this is not a proven remedy.'}</p>
            <span>${App.esc(remedy.historical_evidence.source)}. ${App.esc(remedy.historical_evidence.limitations)}</span></div>` : ''}
          ${remedy?.intent ? `<details class="intent-detail" ${intentOpen ? 'open' : ''}><summary>Vendor-neutral action intent / not raw RAN commands</summary>
            <div class="check-row"><span><b>${App.esc(remedy.intent.action)}</b><small>${App.esc(remedy.intent.profile_id)}</small></span></div>
            <p class="muted">Scope: ${App.esc(remedy.intent.scope.join(', '))} / expires after ${remedy.intent.duration_seconds / 60} minutes.</p>
            <p class="muted">${App.esc(remedy.intent.adapter)}. ${App.esc(remedy.intent.coordination)}</p></details>` : ''}
          ${reviewReady ? `<section class="approval-review" role="status"><b>Approval review paused at ${Exec.clock(state.approvalReview.seconds, recording.event_start)}</b>
            <p>Review these exact simulation changes, then confirm.</p>${parameterTable(remedy.parameters)}
            <p>Projected RTT ${number(remedy.service_after?.latency_ms)} ms / loss ${number(remedy.service_after?.packet_loss_pct, 2)}%.</p></section>`
            : state.approvalMessage ? `<p class="ranking-change" role="status">${App.esc(state.approvalMessage)}</p>` : ''}
          ${action?.parameters?.length ? `<h3>Simulation inputs changed / not vendor commands</h3>${parameterTable(action.parameters)}` : ''}
          <div class="investigation-step"><span class="investigation-step-icon">3</span><div><h3>${App.esc(workflow.label)}</h3>
            ${workflow.state === 'verifying' ? `<p>${workflow.remaining_s}s replay time until validation</p><div class="validation-track" role="progressbar" aria-label="Investigation validation progress" aria-valuenow="${workflow.progress_pct}" aria-valuemin="0" aria-valuemax="100"><i style="width:${workflow.progress_pct}%"></i></div>` : ''}
            ${action?.validation ? `<p>${App.esc(action.validation.result)} / source projection ${number(action.validation.source_after)}%</p>
              ${action.validation.service_after ? `<p>Service projection ${number(action.validation.service_after.latency_ms)} ms / ${number(action.validation.service_after.packet_loss_pct, 2)}% loss</p>` : ''}` : ''}
            ${action?.validation_samples?.length ? `<p>${action.validation_samples.length} live replay checks / latest ${Exec.clock(action.validation_samples.at(-1).at, recording.event_start)}: ${action.validation_samples.at(-1).passed ? 'check passed' : 'failed; execution held'}</p>` : ''}
            ${workflow.state === 'automatic_ready' ? '<p>Automatic execution on the next stream tick; no engineer approval needed.</p>' : ''}
          </div></div>
          ${state.investigationError ? `<p class="inline-error" role="alert">${App.esc(state.investigationError)}</p>` : ''}
          <div class="investigation-actions">
            ${workflow.can_approve && remedy ? `<button class="btn primary" id="investigation-approve" ${disabled}>${reviewReady ? 'Confirm rank ' + remedy.rank + ' approval' : 'Review rank ' + remedy.rank + ' approval'}</button>` : ''}
            ${reviewReady ? `<button class="btn" id="cancel-approval-review" ${disabled}>Cancel review</button>` : ''}
            ${workflow.can_respond ? `<button class="btn" id="investigation-respond" ${disabled}>${issue.tier === 3 ? 'Record escalation' : 'Reject / hold'}</button>` : ''}
            ${state.investigationSaving ? `<span role="status">${state.reviewPreparing ? 'Waiting for current evidence...' : 'Saving decision...'}</span>` : ''}
          </div><p class="simulation-note">RTT / loss are populated synthetic scenarios. All actions and validation are simulated; recorded telemetry stays unchanged.</p>`;
        document.getElementById('investigation-play').onclick = () => setPlaying(!state.playing);
        const choice = panel.querySelector('#investigation-remedy');
        if (choice) choice.onchange = event => {
          state.investigationChoice = event.target.value; state.approvalReview = null;
          state.approvalMessage = 'Option changed. Review its parameters before confirming.';
          renderInvestigation();
        };
        const prepareApproval = async () => {
          if (state.investigationSaving) return;
          const requestedChoice = remedy?.choice_id;
          state.investigationSaving = true; state.reviewPreparing = true; state.investigationError = '';
          state.approvalReview = null; state.approvalMessage = 'Pausing and fetching current review evidence...';
          setPlaying(false);
          try {
            await refresh(false);
            if (!active() || state.investigationCell !== cell) return;
            const fresh = state.snapshot.issues.find(row => row.cell_id === cell);
            const current = fresh.candidates.find(c => c.choice_id === requestedChoice) || fresh.remedy;
            if (!fresh.workflow.can_approve || !current || current.tier !== 2) {
              state.approvalMessage = 'Current evidence no longer permits engineer approval. Nothing was applied.';
              return;
            }
            state.investigationChoice = current.choice_id;
            state.approvalReview = { cell, token: fresh.review_token, choice: current.choice_id, seconds: state.snapshot.seconds };
            state.approvalMessage = '';
          } catch (error) {
            state.investigationError = error.message;
          } finally {
            state.investigationSaving = false;
            state.reviewPreparing = false;
            if (active()) renderInvestigation();
          }
        };
        const respond = async type => {
          if (state.investigationSaving) return;
          const review = state.approvalReview;
          if (type === 'approve' && (!review || review.token !== issue.review_token || review.choice !== remedy?.choice_id)) {
            await prepareApproval(); return;
          }
          state.investigationSaving = true; state.investigationError = ''; renderInvestigation();
          try {
            await App.post('/api/operations/respond', { session_id: state.session.id, cell_id: cell,
              action: type, review_token: type === 'approve' ? review.token : issue.review_token,
              choice_id: type === 'approve' ? review.choice : remedy?.choice_id });
            state.approvalReview = null; state.approvalMessage = '';
            state.investigationChanged = false;
            await refresh(false);
          } catch (error) {
            state.approvalReview = null;
            state.investigationError = error.message.includes('Investigation changed')
              ? 'Live evidence changed. Nothing was applied. Review the refreshed recommendation and confirm again.'
              : error.message;
            try { await refresh(false); } catch (refreshError) { /* refresh reports connection errors */ }
          } finally {
            state.investigationSaving = false;
            if (active()) renderInvestigation();
          }
        };
        const approve = panel.querySelector('#investigation-approve'), response = panel.querySelector('#investigation-respond');
        if (approve) approve.onclick = () => reviewReady ? respond('approve') : prepareApproval();
        if (response) response.onclick = () => respond(issue.tier === 3 ? 'escalate' : 'reject');
        const cancel = panel.querySelector('#cancel-approval-review');
        if (cancel) cancel.onclick = () => {
          state.approvalReview = null; state.approvalMessage = 'Approval review cancelled. No action was applied.';
          renderInvestigation();
        };
        if (focused && !panel.querySelector('#' + focused)) document.getElementById('investigation-close').focus({ preventScroll: true });
        else if (focused) panel.querySelector('#' + focused).focus({ preventScroll: true });
        document.getElementById('investigation-content').scrollTop = scrollTop;
      };
      const draw = snap => {
        state.responseSignature = JSON.stringify(snap);
        state.snapshot = snap; state.session = snap.session; state.seconds = snap.session.seconds;
        state.ended = snap.index === recording.times.length - 1;
        el.querySelector('#recording-status').hidden = !state.ended;
        if (state.ended) {
          setPlaying(false);
          if (!state.completionNotified) {
            state.completionNotified = true;
            Exec.alert('Recording complete', 'Start a new replay run. No future measurements are invented.');
          }
        }
        const selected = snap.issues.find(row => row.cell_id === state.cell);
        const upcoming = snap.outlook[snap.outlook.length - 1];
        const activeIssues = snap.issues.filter(row => row.tier > 0 || row.forecast_watch || [
          'verifying', 'verified', 'rejected', 'escalated', 'rolled_back', 'validation_blocked'].includes(row.workflow.state))
          .sort((a, b) => Number(b.workflow.needs_review) - Number(a.workflow.needs_review) || b.tier - a.tier || b.observed - a.observed);
        const decisions = snap.issues.filter(row => row.workflow.needs_review).length;
        const validating = snap.session.actions.filter(action => action.status === 'verifying').length;
        const verified = snap.session.actions.filter(action => action.status === 'verified').length;
        const escalations = activeIssues.filter(issue => issue.tier === 3).length;
        const exposed = snap.quality.latency > snap.service_policy.latency_limit_ms || snap.quality.packet_loss > snap.service_policy.packet_loss_limit_pct;
        const posture = escalations ? 'Specialist attention required.' : decisions ? 'Engineer decisions pending.'
          : exposed ? 'Service exposure remains elevated.' : validating ? 'Validation in progress.'
          : activeIssues.some(issue => issue.forecast_watch) ? 'Watch the emerging demand.' : 'Within the configured operating envelope.';
        el.querySelector('#live-brief').innerHTML = `<div class="brief-copy"><span class="eyebrow">OPERATING POSTURE</span><h2>${posture}</h2>
          <p>${decisions} awaiting decisions / ${validating} validating / ${verified} verified simulations</p></div>
          <div class="brief-status">${Exec.pill(escalations ? 'ESCALATION ACTIVE' : decisions || exposed ? 'ATTENTION' : validating ? 'VALIDATING' : 'MONITORING', escalations ? 'danger' : decisions || exposed ? 'amber' : 'good')}
          <span>Recorded synthetic stream. No real actuation.</span></div>`;
        el.querySelector('#live-metrics').innerHTML = [
          Exec.metric('Latency now', snap.quality.latency.toFixed(1), 'ms RTT', 'Synthetic / budget ' + snap.service_policy.latency_limit_ms + ' ms', snap.quality.latency > snap.service_policy.latency_limit_ms ? 'danger' : 'good'),
          Exec.metric('Packet loss now', snap.quality.packet_loss.toFixed(2), '%', 'Synthetic / budget ' + snap.service_policy.packet_loss_limit_pct + '%', snap.quality.packet_loss > snap.service_policy.packet_loss_limit_pct ? 'danger' : 'good'),
          Exec.metric('Needs a decision', decisions, '', `${validating} validating / ${verified} verified simulations`, decisions ? 'danger' : 'good'),
          Exec.metric('Radio in 30 minutes', upcoming.prediction.toFixed(1), '%', upcoming.low + ' - ' + upcoming.high + '% load range', upcoming.prediction >= 85 ? 'danger' : '')
        ].join('');
        const workflow = selected.workflow;
        const action = snap.session.actions.find(a => a.id === workflow.action_id);
        const stepLabels = ['Detected', action?.automatic ? 'Policy approved' : 'Approved', 'Simulated', 'Validated'];
        const completed = workflow.state === 'verified' ? 4 : action?.remedy ? 3 : 1;
        const lifecycle = el.querySelector('#remedy-lifecycle');
        lifecycle.innerHTML = `<div class="remedy-state ${workflow.tone}" data-state="${workflow.state}">
          <div class="remedy-state-head"><div><span class="eyebrow">${App.esc(state.cell)} / DECISION STATE</span><h3>${App.esc(workflow.label)}</h3></div>
          ${Exec.pill(workflow.state === 'verifying' ? workflow.remaining_s + 's TO CHECK' : workflow.state === 'verified' ? 'SIMULATION PASSED' : 'RULE-BASED', workflow.tone)}</div>
          ${action?.remedy ? `<div class="remedy-route"><b>${App.esc(action.cell_id)}</b><span>&rarr;</span><b>${App.esc(action.target || 'TDD UL profile')}</b>
            <span class="impact-chip">Projected source ${(action.remedy.source_after + action.remedy.relief).toFixed(1)}% &rarr; ${action.remedy.source_after}%</span></div>
            <div class="remedy-steps">${stepLabels.map((label, index) => `<span class="${index < completed ? 'complete' : ''}">${index < completed ? '&#10003;' : index + 1} ${label}</span>`).join('')}</div>
            ${workflow.state === 'verifying' ? `<div class="validation-track" role="progressbar" aria-label="Simulation validation progress" aria-valuenow="${workflow.progress_pct}" aria-valuemin="0" aria-valuemax="100"><i style="width:${workflow.progress_pct}%"></i></div>` : ''}
            <span class="simulation-note">Hypothetical effect. Recorded observations are unchanged.</span>` : ''}
          ${['verifying', 'automatic_ready'].includes(workflow.state) ? `<button class="btn small primary" id="continue-validation" ${state.playing ? 'hidden' : ''}>${workflow.state === 'verifying' ? 'Resume & validate' : 'Resume automatic run'} &rarr;</button>` : ''}
          </div>`;
        const continueButton = el.querySelector('#continue-validation');
        if (continueButton) continueButton.onclick = () => setPlaying(true);
        el.querySelector('#live-chart-meta').textContent = state.cell + ' / cell-level forecast / five-minute steps through 30 minutes';
        el.querySelector('#stream-clock').textContent = Exec.clock(snap.seconds, recording.event_start);
        el.querySelector('#stream-time').textContent = Math.round(snap.seconds / 60) + 'm from start';
        const slider = el.querySelector('#stream-position');
        slider.min = snap.index; slider.value = snap.index;
        const history = snap.history.filter(point => point.seconds < snap.seconds).map(point => ({ x: (point.seconds - snap.seconds) / 60, value: point.value }));
        const x = [...history.map(p => p.x), 0, ...snap.outlook.slice(1).map(p => p.minutes)];
        const pad = history.map(() => null);
          el.querySelector('#live-chart-title').textContent = '30-minute cell-level demand & risk outlook';
          el.querySelector('#live-chart-caption').textContent = 'Navy solid: observed radio / indigo dashed: trained load forecasts / shading: 90% calibrated load range';
          Charts.line('#operations-chart', {
          x, xTicks: [-20, -10, 0, 5, 10, 15, 20, 25, 30], height: 310, yMin: 0, yMax: 100, legend: false, yFormat: v => v + '%',
          xFormat: value => value === 0 ? 'Now' : (value > 0 ? '+' : '') + Math.round(value) + 'm',
          series: [
            { name: 'Observed', values: [...history.map(p => p.value), snap.observed, ...snap.outlook.slice(1).map(() => null)], color: '#142f46', width: 2.8 },
            { name: 'Outlook', values: [...pad, ...snap.outlook.map(p => p.prediction)], color: '#5265c7', width: 2.8, dash: '6 4' }
          ],
          bands: [{ low: [...pad, ...snap.outlook.map(p => p.low)], high: [...pad, ...snap.outlook.map(p => p.high)], color: '#dfe6ff', opacity: .75 }],
          thresholds: [{ y: 85, label: '85% policy guardrail', color: '#b43755' }],
          markers: [{ x: 0, label: '', color: '#91a5b8' }]
          });
        el.querySelector('#risk-bins').innerHTML = snap.outlook.slice(1).map(p => `<div class="${p.congestion_probability >= p.risk_threshold ? 'high' : ''}">
          <span>+${p.minutes - 5}-${p.minutes} min</span><b title="Probability of the supplied synthetic congestion rule">${Math.round(p.congestion_probability * 100)}%</b></div>`).join('');
        el.querySelector('#operation-cells').innerHTML = snap.cells.map(c => {
          const route = action?.remedy && ['verifying', 'verified'].includes(workflow.state);
          const source = route && c.cell_id === action.cell_id, target = route && c.cell_id === action.target;
          return `<button class="cell-card ${c.cell_id === state.cell ? 'selected' : ''} ${c.prb >= 85 ? 'hot' : ''} ${source ? 'route-source' : target ? 'route-target' : ''}" data-cell="${c.cell_id}" aria-label="Select ${c.cell_id}">${c.cell_id.replace('V01-', '')}<b>${Math.round(c.prb)}%</b><div class="cell-bar"><i style="width:${c.prb}%"></i></div>${source || target ? `<span class="route-label">${source ? 'SOURCE' : 'RECEIVER'}</span>` : ''}</button>`;
        }).join('');
        el.querySelectorAll('[data-cell]').forEach(button => button.onclick = () => { state.cell = button.dataset.cell; refresh(false).catch(Exec.error); });
        el.querySelector('#issue-count').innerHTML = Exec.pill(decisions + ' TO REVIEW', decisions ? 'amber' : 'good');
        el.querySelector('#issue-list').innerHTML = activeIssues.length ? activeIssues.map(issue => {
          const current = issue.workflow;
          return `<button class="incident-card state-${current.state}" data-issue="${issue.cell_id}"><div class="meta">${Exec.pill(current.label, current.tone)}<span class="muted">${issue.cell_id}</span></div>
            <b>${App.esc(issue.title)}</b><p>${current.state === 'verifying' ? current.remaining_s + 's until validation' : 'Tier ' + issue.tier + ' / ' + Math.round(issue.observed) + '% recorded radio'}</p></button>`;
        }).join('') : '<div class="empty"><span class="live-dot"></span><h3>Operating within limits</h3>No persistent rule breaches.</div>';
        el.querySelectorAll('[data-issue]').forEach(button => button.onclick = () => issueDialog(button.dataset.issue).catch(Exec.error));
        el.querySelector('#safety-envelope').innerHTML = [
          ['Radio use', selected.observed, 85], ['Site link', selected.site_util, 85], ['Shared link', selected.agg_util, 90]
        ].map(([label, value, limit]) => `<div class="link-row"><span>${label}</span><div class="progress-track"><i class="${value > limit ? 'hot' : ''}" style="width:${Math.min(value, 100)}%"></i></div><b>${Math.round(value)}%</b></div>`).join('')
          + `<div class="status-line">${Exec.pill('SERVICE RULES ACTIVE', 'good')}RTT ${snap.service_policy.latency_limit_ms} ms / loss ${snap.service_policy.packet_loss_limit_pct}%</div><p class="subline">15% target headroom / 85% site / 90% shared link</p>`;
        renderHistory(snap);
        snap.issues.forEach(issue => {
          const current = issue.workflow;
          if (!issue.tier && current.state === 'monitoring' && !state.incidentCells.has(issue.cell_id)) return;
          state.incidentCells.add(issue.cell_id);
          Exec.incident(state.session.id + ':' + issue.cell_id, {
            title: issue.cell_id + ' / ' + issue.title,
            message: (issue.forecast_watch ? 'Predictive watch / no execution' : 'Tier ' + issue.tier) + ' / ' + issue.quality.latency_ms + ' ms / ' + issue.quality.packet_loss_pct + '% loss / run ' + state.session.id.slice(0, 6),
            state: current.state, tier: issue.tier, target: issue.remedy?.target || '',
            label: current.label, tone: current.tone,
            attention: current.needs_review || ['forecast_watch', 'verifying', 'verified', 'rolled_back', 'validation_blocked'].includes(current.state),
            action: () => App.views.live.investigate(issue.cell_id, state.session.id)
          });
        });
        renderInvestigation();
        el.querySelector('#live-evidence').onclick = () => { setPlaying(false); Exec.evidence(snap.model); };
        el.querySelector('#policy-details').onclick = () => {
          setPlaying(false);
          const score = snap.scorecard, value = n => n === null ? 'Not yet available' : n + '%';
          Exec.dialog('Assurance & operational scorecard', `<h3>One automatic action family</h3><p>Only configured temporary steering profiles may run automatically within scope and guardrails. TDD simulation needs engineering review. Raw handover, power, beam and transport changes are not automatically executed.</p>
            <table class="table"><tbody><tr><td>Completed simulation success</td><td>${value(score.success_rate_pct)}</td></tr><tr><td>Rollback rate</td><td>${value(score.rollback_rate_pct)}</td></tr>
              <tr><td>Engineer acceptance</td><td>${value(score.engineer_acceptance_pct)}</td></tr><tr><td>Mean successful validation</td><td>${score.mean_validation_seconds === null ? 'Not yet available' : score.mean_validation_seconds + ' replay seconds'}</td></tr></tbody></table>
            <h3 class="case-section-title">Guardrails</h3><p>Configured cell scope, target availability / SINR / handover checks, 15% post-move headroom, 85% site / 90% shared transport, service budgets, conflict reservations, 10-minute intent validity, live checks and rollback.</p>
            <p class="muted">All thresholds are simulation policy values requiring network calibration. Historical examples are synthetic and incomplete vendor context is flagged. ${App.esc(score.source)}</p>
            <p class="muted">Not measured: ${App.esc(score.unavailable.join(', '))}.</p>`);
        };
      };
      const caseRows = snap => {
        const rows = Object.values(snap.session.cases || {}).map(c => ({ ...c,
          actions: snap.session.actions.filter(a => a.case_id === c.id) }));
        snap.session.actions.filter(a => !a.case_id).forEach(a => rows.push({
          id: 'legacy-' + a.id, cell_id: a.cell_id, title: a.issue_title || 'Legacy action',
          first_seconds: a.seconds, last_seconds: a.validation?.at || a.seconds, status: a.status,
          tier: a.tier, hypotheses: a.hypotheses || [], timeline: [], actions: [a]
        }));
        return rows.map(c => {
          const last = c.actions.at(-1);
          const escalated = c.tier === 3 || ['escalation_required', 'escalated', 'rolled_back', 'validation_blocked'].includes(c.status);
          const category = escalated ? 'escalated' : last?.automatic && last.status === 'verified' ? 'agent_resolved'
            : last?.remedy && last.automatic === false ? 'engineer_approved' : 'pending';
          const activity = ['verified', 'rejected', 'escalated', 'rolled_back', 'validation_blocked'].includes(c.status)
            ? last?.validation?.at ?? last?.seconds ?? c.last_seconds : c.last_seconds;
          return { ...c, category, last, activity_seconds: activity };
        }).sort((a, b) => b.activity_seconds - a.activity_seconds || b.first_seconds - a.first_seconds);
      };
      const showCase = id => {
        const entry = caseRows(state.snapshot).find(c => c.id === id);
        if (!entry) return;
        setPlaying(false);
        const number = value => Number.isFinite(value) ? value.toFixed(2) : 'Unavailable';
        Exec.dialog(entry.title + ' / ' + entry.cell_id, `<div class="toolbar">${Exec.pill(entry.status.replaceAll('_', ' '), entry.category === 'agent_resolved' ? 'good' : entry.category === 'escalated' ? 'danger' : 'amber')}
          <span class="muted">Detected ${Exec.clock(entry.first_seconds, recording.event_start)} / replay</span></div>
          ${entry.detected_evidence ? `<h3 class="case-section-title">At detection</h3><p>${App.esc(entry.detected_evidence.reason)}</p><p class="muted">${number(entry.detected_evidence.quality?.latency_ms)} ms RTT / ${number(entry.detected_evidence.quality?.packet_loss_pct)}% loss / ${number(entry.detected_evidence.radio)}% radio</p>` : ''}
          <h3 class="case-section-title">Suspected causes / latest evidence</h3>${entry.hypotheses.map(h => `<div class="check-row"><span><b>${App.esc(h.cause)}</b><small>${App.esc(h.evidence)}</small></span>${['Supported', 'Not supported'].includes(h.assessment) ? '' : Exec.pill(h.assessment)}</div>`).join('') || '<p class="muted">Not recorded for this legacy action.</p>'}
          ${entry.actions.map(a => {
            const latest = a.validation || a.validation_samples?.at(-1);
            const before = latest?.service_before || a.service_before, after = latest?.service_after || a.remedy?.service_after;
            return `<div class="case-action-block"><h3>${a.automatic ? 'Agent action' : a.remedy ? 'Engineer-approved action' : 'Human response'} / ${App.esc(a.remedy?.title || a.status)}</h3>
              <p class="muted">${Exec.clock(a.seconds, recording.event_start)} / ${App.esc(a.status.replaceAll('_', ' '))}</p>
              ${a.remedy ? parameterTable(a.parameters) : ''}
              ${after ? `<h3 class="case-section-title">Effect / counterfactual at ${Exec.clock(latest?.at ?? a.seconds, recording.event_start)}</h3>
                <table class="table"><thead><tr><th>Metric</th><th>No-action baseline</th><th>Projected with remedy</th></tr></thead><tbody>
                  <tr><td>RTT</td><td>${number(before?.latency_ms)} ms</td><td>${number(after.latency_ms)} ms</td></tr>
                  <tr><td>Packet loss</td><td>${number(before?.packet_loss_pct)}%</td><td>${number(after.packet_loss_pct)}%</td></tr></tbody></table>` : ''}
              ${a.validation_samples?.length ? `<details><summary>${a.validation_samples.length} live validation checks</summary><table class="table"><thead><tr><th>Replay time</th><th>RTT</th><th>Loss</th><th>Check</th></tr></thead><tbody>
                ${a.validation_samples.map(s => `<tr><td>${Exec.clock(s.at, recording.event_start)}</td><td>${number(s.service_after?.latency_ms)} ms</td><td>${number(s.service_after?.packet_loss_pct)}%</td><td>${s.passed ? 'Pass' : s.blocked ? 'Data blocked' : 'Rollback'}</td></tr>`).join('')}</tbody></table></details>` : ''}
              ${a.validation ? `<p><b>${App.esc(a.validation.result)}</b></p>` : a.remedy ? '<p class="muted">Validation is still pending; no resolution claimed.</p>' : ''}
            </div>`;
          }).join('')}
          <h3 class="case-section-title">Time-stamped case trail</h3><ol class="case-timeline">${entry.timeline.map(t => `<li><time>${Exec.clock(t.seconds, recording.event_start)}</time><div><b>${App.esc(t.kind.replaceAll('_', ' '))}</b><p>${App.esc(t.summary)}</p></div></li>`).join('') || '<li>Detailed trail unavailable for this legacy action.</li>'}</ol>
          <p class="simulation-note">Recorded observations are not changed. Effects and validation are live counterfactual checks on synthetic data, not proof of real network recovery.</p>`,
        [{ label: 'Open live investigation', primary: true, run: () => issueDialog(entry.cell_id) }]);
      };
      const renderHistory = snap => {
        const all = caseRows(snap), query = el.querySelector('#case-search').value.trim().toLowerCase();
        const rows = all.filter(c => (state.caseFilter === 'all' || c.category === state.caseFilter)
          && (!state.caseMinutes || snap.seconds - c.activity_seconds <= state.caseMinutes * 60)
          && (!query || (c.title + ' ' + c.cell_id).toLowerCase().includes(query)));
        el.querySelector('#case-count').innerHTML = Exec.pill(rows.length + ' CASES');
        el.querySelector('#resolution-summary').innerHTML = ['agent_resolved', 'engineer_approved', 'escalated'].map(category =>
          `<button class="resolution-counter" data-summary-filter="${category}"><b>${all.filter(c => c.category === category).length}</b><span>${category.replaceAll('_', ' ')}</span></button>`).join('');
        el.querySelector('#operations-log').innerHTML = rows.length ? `<table class="table case-table"><thead><tr><th>Detected</th><th>Issue / cell</th><th>Route</th><th>Remedy</th><th>Outcome</th><th></th></tr></thead><tbody>
          ${rows.map(c => `<tr><td>${Exec.clock(c.first_seconds, recording.event_start)}</td><td><b>${App.esc(c.title)}</b><small>${App.esc(c.cell_id)}</small></td>
            <td>${App.esc(c.category.replaceAll('_', ' '))}</td><td>${App.esc(c.last?.remedy?.title || (c.tier === 3 ? 'Specialist remediation' : 'Awaiting decision'))}</td>
            <td>${Exec.pill(c.status.replaceAll('_', ' '), c.status === 'verified' ? 'good' : c.category === 'escalated' ? 'danger' : 'amber')}</td><td><button class="btn small" data-case="${c.id}">View trail</button></td></tr>`).join('')}</tbody></table>`
          : '<div class="empty">No cases match these filters.</div>';
        el.querySelectorAll('[data-case]').forEach(button => button.onclick = () => showCase(button.dataset.case));
        el.querySelectorAll('[data-summary-filter]').forEach(button => button.onclick = () => setCaseFilter(button.dataset.summaryFilter));
      };
      const setCaseFilter = value => {
        state.caseFilter = value;
        el.querySelectorAll('[data-case-filter]').forEach(button => button.classList.toggle('selected', button.dataset.caseFilter === value));
        if (state.snapshot) renderHistory(state.snapshot);
      };
      el.querySelectorAll('[data-case-filter]').forEach(button => button.onclick = () => setCaseFilter(button.dataset.caseFilter));
      el.querySelector('#case-time').onchange = event => { state.caseMinutes = +event.target.value; if (state.snapshot) renderHistory(state.snapshot); };
      el.querySelector('#case-search').oninput = () => { if (state.snapshot) renderHistory(state.snapshot); };
      const refresh = async advance => {
        if (!active()) return;
        if (state.busy) { state.pending = true; return state.refreshPromise; }
        const run = async () => {
          state.busy = true;
          const requestCell = state.cell;
          try {
            const response = advance
              ? await App.post('/api/operations/tick', { session_id: state.session.id, seconds: state.seconds, cell_id: requestCell })
              : await App.api('/api/operations/live?' + new URLSearchParams({ seconds: state.seconds, cell_id: requestCell, session_id: state.session.id }));
            if (active()) {
              if (requestCell === state.cell) {
                if (JSON.stringify(response) !== state.responseSignature) draw(response);
              }
              else {
                state.session = response.session; state.seconds = response.session.seconds; state.pending = true;
              }
            }
          } catch (error) {
            if (active()) { setPlaying(false); Exec.error(error); }
            throw error;
          } finally {
            state.busy = false;
            if (active() && state.pendingSeek !== undefined) {
              const index = state.pendingSeek;
              delete state.pendingSeek;
              state.pending = false;
              if (!state.snapshot) Exec.error(new Error('Wait for a valid stream snapshot before advancing.'));
              else if (index < state.snapshot.index) Exec.error(new Error('Cannot rewind an active run. Start a new replay run.'));
              else { state.seconds = recording.times[index]; await refresh(true); }
            } else if (state.pending && active()) { state.pending = false; await refresh(false); }
          }
        };
        state.refreshPromise = run();
        return state.refreshPromise;
      };
      el.querySelector('#stream-play').onclick = () => setPlaying(!state.playing);
      el.querySelector('#restart-complete-run').onclick = () => el.querySelector('#reset-session').click();
      el.querySelector('#stream-speed').onchange = event => { state.speed = +event.target.value; };
      el.querySelector('#stream-position').onchange = async event => {
        state.approvalReview = null;
        state.approvalMessage = 'Replay position changed. Review current evidence before confirming.';
        setPlaying(false);
        if (state.busy) { state.pendingSeek = +event.target.value; return; }
        state.seconds = recording.times[+event.target.value];
        try { await refresh(true); } catch (error) { /* refresh displays the error */ }
      };
      el.querySelector('#reset-session').onclick = () => {
        setPlaying(false);
        Exec.dialog('Start a new replay run?', '<p class="muted">Restarts near the first pressure window. Previous actions remain persisted in their original session. Planning recommendations do not gate live actions.</p>', [
          { label: 'Start new run', primary: true, run: async () => {
            const fresh = await App.post('/api/operations/session', {});
            sessionStorage.setItem('vdi-executive-session', fresh.id);
            Exec.clearPopups();
            await App.route();
          }}
        ]);
      };
      await refresh(true);
      const pendingInvestigation = sessionStorage.getItem('vdi-investigation-cell');
      if (pendingInvestigation && recording.cells.includes(pendingInvestigation)) {
        sessionStorage.removeItem('vdi-investigation-cell'); await issueDialog(pendingInvestigation);
      }
      App.every(4000, async () => {
        if (!active() || state.playing || state.busy) return;
        try { await refresh(false); } catch (error) { /* refresh displays the error */ }
      });
      App.every(1000, async () => {
        if (!active() || !state.playing || state.busy || state.investigationSaving) return;
        state.accumulator += state.speed;
        if (state.accumulator < 15) return;
        const steps = Math.floor(state.accumulator / 15);
        state.accumulator %= 15;
        const index = Math.min(recording.times.length - 1, state.snapshot.index + steps);
        state.seconds = recording.times[index];
        try { await refresh(true); } catch (error) { /* refresh pauses and displays the error */ }
      });
    },
    destroy() { generation++; live = null; Exec.closeInvestigation(); }
  };
})();
