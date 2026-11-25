# Architecture

The PoC follows six pipeline stages. Each maps to concrete modules, and every UI number is produced by
these modules through the JSON API (`vdi/server.py` → `vdi/api.py`). All data is synthetic.

| # | Stage | Modules | What happens |
|---|-------|---------|--------------|
| 1 | **Sources** | `data/Verizon data/*.csv`, `vdi/config.py` | 37 synthetic CSVs (venues, cells, neighbours, events, context signals, hourly/daily/15 s KPIs, backhaul, decisions, actions, costs, effort, gatherings). Location via `config/settings.json` or `VDI_DATA_DIR`. |
| 2 | **Data layer** | `vdi/data_loader.py`, `vdi/features.py` | Typed loads with `usecols`; large files (12, 13, 14, 24, 25, 27, 31, 32) reduced to derived tables (cell × event peaks, hourly shape, response-curve samples, backhaul peaks, telemetry, app mix) and pickled in `cache/`. Cache invalidates on source mtime / cache version. Causal features only; `true_occupancy_sim_only` is excluded (asserted). |
| 3 | **Predict** | `vdi/models/attendance.py`, `vdi/models/peak_uplink.py`, `vdi/models/short_horizon.py`, `vdi/similar_events.py`, `vdi/models/core.py` | Attendance (ridge on context incl. home/away + similar-event kNN, learned blend, 90 % residual band); peak UL PRB per cell × event (log-ridge, 90 % residual-quantile interval, hourly profile → cell × hour heatmap); 60/120/180 s congestion risk (logistic regression on 15 s lag/slope features). |
| 4 | **Decide (backhaul-aware)** | `vdi/decision_engine.py` | Threshold check (rule + model risk) → neighbour selection (eligible, overlap × headroom) → radio check (headroom ≥ 15 %) → backhaul check (target site link ≤ 85 % after move, venue aggregation ≤ 90 %) → approval tier → action spec (CIO, A3 offset, UL load-balance threshold, 30-min auto-revert). Returns an explainable trace. |
| 5 | **Act** | `vdi/live.py`, `vdi/simulator.py`, `vdi/api.py` (approvals) | Tier 1 automatic (medium risk, safe), tier 2 engineer approves (high risk, safe), tier 3 fallback (no safe neighbour: shape non-critical uplink, priority boost, standby carrier, NOC escalation). Verify after 120 s → pass, or rollback → fallback. Approvals/rejections appended to `audit/approvals.jsonl`. Digital-twin what-if for planning. |
| 6 | **Learn & govern** | `vdi/monitoring.py`, `vdi/business_value.py`, `vdi/pipeline.py` | Hold-out backtest by month (MAE, bias, coverage), PSI drift train-vs-recent, history from file 18 (versions, drift episodes), live drift (file 16), alerts; business value from files 28/29/30/35. `pipeline train` refits on all history after evaluation. |

## Models

### Attendance (`models/attendance.py`)
* Target: actual attendance / reference capacity (concert capacity for concerts, seats otherwise).
* Features (43): organiser estimate ratio and band, estimate confidence/source, sell-through, price, COVID cap,
  **home/away/neutral + home-crowd multiplier**, category/subtype, social, search, resale index, win %, standings,
  transit/road advisories, competing events, holidays, rivalry/primetime, weather, year.
* Ridge (closed form) + time-respecting kNN (k = 12, only events that started earlier) in a weighted context space;
  blend weight learned on leave-one-out kNN predictions. Interval: 5th/95th residual percentiles per category × home/away.

### Peak uplink (`models/peak_uplink.py`)
* Unit: cell × event peak UL PRB % (file 12, max over the ±6 h window).
* Drivers: predicted attendance × zone share × uplink growth / cell UL capacity (file 05 in force at event date),
  cell band/technology, cell factor, cell one-hots, non-event baseline (file 13), the cell's last four comparable
  events (similar-event history, strictly earlier), category, home/away, year.
* Ridge on log(PRB) clipped to 0–100 %; 90 % interval from residual quantiles per predicted-load tercile.
* Hourly profile per category turns the event peak into the cell × hour heatmap; P(> 85 %) from the residual σ.
* **Time-based split:** train ≤ 2024, test 2025–2026.

### Short-horizon congestion risk (`models/short_horizon.py`)
* 15 s telemetry for the target venue (file 14), labels `label_congestion_next_{60,120,180}s`.
* 44 causal features: current UL/DL PRB, users, throughput (P50/P10), CQI, SINR, PHR, RACH failure ratio,
  lags (1/2/4/8), slopes (1/4/8/12 steps) of PRB, P10 and RRC, rolling mean/max, rule run-length, phase.
* One logistic regression per horizon (Adam GD, positive weighting); threshold = best F1 on training.
* Split by event in time order: first 25 events train, last 8 test.

### Results (hold-out, from `artifacts/metrics.json`)
| Model | Metric |
|-------|--------|
| Attendance (767 events 2025–26) | MAPE 4.07 % (organiser estimate alone 4.14 %), MAE 2,019 people, 90 % band coverage 88.7 % |
| Peak UL PRB (6,094 cell-events 2025–26) | MAPE 7.34 %, MAE 4.25 PRB pts, bias −1.42, 90 % interval coverage 88.3 %, hot-cell precision 94.8 % / recall 78.3 % |
| Congestion risk 60 s (8 events, 92k rows) | AUC 0.997, precision 0.87, recall 0.91 |
| Congestion risk 120 s | AUC 0.996, precision 0.88, recall 0.88 |
| Congestion risk 180 s | AUC 0.996, precision 0.86, recall 0.88 |

Numbers are regenerated by `python -m vdi.pipeline train`; the UI always reads the current `metrics.json`.

## Simulator (`simulator.py`)
Inputs: event, attendance % of forecast, crowd type (home/away/neutral multipliers 1.0/0.33/0.85 from the
data dictionary), % hot-cell traffic steered, temporary UL capacity (Mbps).
1. Re-score the peak model at the scenario attendance.
2. Temporary capacity is shared across cells above 60 % PRB in proportion to excess load (PRB × cap / (cap + added)).
3. Steering moves load from cells ≥ 70 % to eligible, non-source neighbours up to 75 % PRB (capacity-ratio aware).
4. P10, drop rate, accessibility and congestion minutes from **monotone isotonic curves** fitted on hourly KPIs
   (12 + 27) and file 35 digital-twin replays; P10 after relief from file 35 with-action outcomes.
5. Backhaul: per-link background + venue load from recent comparable events (file 24), venue load scaled by site PRB.

## Live replay (`live.py`)
Scores the 15 s live stream (file 15) with the short-horizon model, raises alerts (rule breach, risk crossing),
runs the decision engine per cell with a 10-minute cooldown using live neighbour loads and live backhaul
(file 25 `is_live`), and verifies each steer 120 s later against the observed loads. Output: `artifacts/live.json`.

At startup `LiveReplay` enriches each cell with the historical-model hourly event profile and a
60-second-ahead forecast: historical profile at the target time plus the trailing eight observed
15-second residuals, clipped to 0-100% PRB. Predictions are aligned to their target observations;
the first 60 seconds have no matured adaptive error. Snapshot MAE/bias/current error are strictly
cursor-bounded, including a trailing 20-sample (five-minute) MAE. Future recorded observations never
enter an earlier forecast or its error.

Snapshots run the existing decision engine against current radio/backhaul loads for all cells,
without changing engine policy or applying cooldown/action state. Eligibility is not an executed action.
The source/target comparison projects only one recommendation; multiple rows are not a jointly safe
routing plan. Historical timeline actions, approval logs and the current snapshot remain distinct.
The browser labels recorded synthetic data explicitly; no real telemetry or RAN API is connected.

## API

### Executive two-tab workflow

`static/index.html` loads only the executive forecast/live views, `executive.js`
and `executive.css`. Earlier views remain unreferenced compatibility assets.
`Operations` owns advisory recommendations, per-run cursors, rule outcomes, reserved
source/target cells, simulations and validation. Its state is atomically written
under the audit directory. Operations endpoints share the loopback/same-Origin
restrictions of AI endpoints. This is still an unauthenticated local PoC.

`models/outlook.py` fits direct load-change ridge models at 300/600/900/1200/1500/1800 seconds
using causal telemetry features and event-relative time. Chronological event
partitions are 19 train / 6 residual calibration / 8 test. Future labels are used
only as training/evaluation targets, requiring exact timestamp alignment. The
deployed model remains the evaluated training model, not a refit on test events.
An artifact fingerprint includes source mtime/size and implementation version.
Startup creates `outlook.json` if missing/stale; pipeline training also includes
its report. No new dependencies are required.
Logistic models predict the provided congestion flag in each corresponding
future five-minute bin. Thresholds are chosen on calibration events; AUC,
precision/recall and Brier use held-out test events. New diagnostic fault
scenarios are not claimed as separately evaluated hardware/interference models.

Radio rules require three readings >=70% and P10 <3 Mbps; >=85% or inter-site
steering is Tier 2. Transport breaches over three samples escalate. Candidate
ranking enforces post-move radio <=85%, site <=85%, aggregation <=90%, then
prefers lower tiers, more target headroom and overlap. Planning is advisory and
does not gate live actions. Both endpoints of an action enter a 600-second cooldown.
Validation at +120s uses no-action recorded observations and projected moved load,
including added inter-site transport traffic. Failed actions block automatic retries.
`service_quality.py` adds a populated synthetic service CSV using supplied
transport latency/loss and explicit radio/TDD/RF scenario formulas. Three samples
above 50 ms RTT or 1% loss trigger service diagnosis. Safe steering projections
must preserve target service budgets. The TDD playbook is Tier 2 and requires
alignment, protected downlink capacity and safe transport; failed checks escalate.
Validation checks projected RTT/loss in addition to capacity. See the data
provenance document for assumptions: these are not real measurements or an
independently evaluated QoS forecasting model.

`investigations.py` records evidence-backed hypotheses and case histories.
Each run stores `cases`, an active-cell/case map, and actions linked by `case_id`.
Case events retain both replay seconds and UTC recording timestamps. Actions
store exact simulation parameters, the chosen remedy/choice ID, initial evidence,
and per-sample validation projections. Current decisions are distinct from
historical actions; a verified action cannot hide a new critical incident.

Steering compares limited/standard fractions per eligible neighbor. TDD compares
two slot profiles with proportional downlink penalties and residual slot gaps.
Executable profiles must predict source recovery as well as target safety.
Specialist remedies remain advisory. Engineer requests may specify an eligible
`choice_id`; the current review token covers the whole safe-candidate ranking.

Validation is incremental, at each 15-second sample until the 120-second deadline.
A jump processes only elapsed validation timestamps, never future samples.
An unsafe sample triggers rollback immediately; missing evidence blocks validation.
The case ledger is returned inside the session snapshot, with backward-compatible
legacy action rows in the UI.

`assurance.py` implements four-domain RCA, comparable synthetic outcome retrieval,
vendor-neutral intents and operational scorecards. Matching uses normalized load
bands, available venue/category context and dated capacity records, while missing
exact profile/vendor/software and target-side outcomes lower confidence. Ranking
uses benefit, historical support, confidence, simplicity and risk after hard gates.
Version-2 service data populates additional synthetic radio, mobility, alarm and
compute counters; current radio quality can block a capacity-only diagnosis.

| Method | Executive endpoint | Purpose |
|---|---|---|
| GET | `/api/operations/plan/{event_id}` | advisory issue/evidence/remedy recommendations; legacy POST returns 400 |
| POST | `/api/operations/session` | create a persisted demo run |
| GET | `/api/operations/live?seconds=...&cell_id=...&session_id=...` | read-only snapshot and outlook |
| POST | `/api/operations/tick` | monotonic run advancement, automatic simulation and due validation |
| POST | `/api/operations/respond` | rechecked engineer approval, rejection or escalation |
| GET | `/api/operations/model` | held-out outlook metrics and split |

The executive UI deliberately does not mount the retained Claude panel. Core
forecasting and all operational decisions work without a provider key.

Investigations are non-modal live panels. Notifications are upserted by run/cell,
and their latest state is refreshed with each snapshot. Tier-specific controls
come from server workflow permissions. A SHA-256 `review_token` binds the sample,
session, service/radio evidence, candidate ranking and workflow; it is a
freshness check, not authentication. `POST /api/operations/respond` requires the
current token for engineer approval and rechecks authority under the workflow lock.
Run-based read-only snapshots catch up to the persisted cursor, while advancing a
run backwards is still rejected.

The engineer UI uses a two-step paused review. Snapshot refreshes are single-flight
and return the actual in-flight promise, including queued follow-up refreshes.
Review pauses replay, waits for pending updates and fetches current evidence before
pinning a token/choice for explicit confirmation. Resume, seek, option changes or
new server evidence invalidate that pin. The server still performs its original
token and policy checks; the UI never silently retries an approval on changed data.

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/health` | status, versions, as-of date |
| GET | `/api/overview` | KPI tiles, growth, app mix, venue board, headlines |
| GET | `/api/events` · `/api/events/{id}` | upcoming events · detail (heatmap, cells, similar events, signals) |
| GET | `/api/live` · `/api/live/decisions` | replay payload (+ approvals) · decisions |
| GET | `/api/live/snapshot?seconds=...&cell_id=...` | cursor-bound errors, per-cell routing, single-action projection |
| POST | `/api/live/decisions/{id}/approve` · `/reject` | record approval in the audit log |
| GET | `/api/audit` | latest audit entries |
| GET | `/api/routing` | funnel, blocked reasons, tiers, backhaul, actions |
| GET/POST | `/api/simulate` | what-if (`event_id, attendance_pct, home_away, traffic_moved_pct, temp_capacity_mbps`) |
| GET | `/api/simulate/events` | selectable events |
| GET | `/api/gatherings` · `/api/gatherings/{id}/replay` | unplanned gatherings · 15 s replay |
| GET | `/api/model-health` · `/api/metrics` | monitoring payload · raw metrics.json |
| GET | `/api/catalog` · `/api/catalog/columns?q=` | file catalog · column search |

Payloads are precomputed at server start (≈5 s warm), gzip-compressed, and long series are downsampled for the browser.

## Optional Claude advisory layer

`vdi/ai.py` calls the Anthropic Models and Messages APIs with stdlib HTTPS and the server-side
`ANTHROPIC_API_KEY`. `ANTHROPIC_MODEL` optionally supplies an exact model ID; otherwise users load
available models and explicitly select one. The browser never receives the key.
`vdi/config.py` loads the workspace-root `.env` once at process startup using `python-dotenv`,
without overriding existing environment variables or interpolating credential values.
The local `.env` is ignored by version control; `.env.example` contains only empty placeholders.

`POST /api/ai/explain` uses `AppState.ai_explain` to select bounded context from existing predictions,
aggregate view payloads, the currently rendered simulator inputs, or the visible live snapshot.
Future live verification results and audit identity/comment fields are excluded. The response is
plain-text advisory content, rendered with `textContent`. No tools or action-execution path are
exposed to the model. The numerical pipeline, approvals, decision engine and replay remain independent.

`GET /api/ai/status` is local-only configuration status; `POST /api/ai/models` retrieves available
model IDs. All AI endpoints require a loopback client and, for browser Origin headers, the same
origin as the local HTTP server. Model discovery and manual questions require clicks. Simulator
auto-analysis is enabled by default, debounced for two seconds after rendered scenario/comparison
changes, single-flight per panel, and paused on provider errors. Replay ticks never trigger AI;
manual replay questions pause playback. Revision IDs discard stale replies and navigation cancels timers.
Follow-ups send at most six alternating user/assistant messages (16000 characters total); the browser
keeps at most two prior exchanges in memory and clears them whenever context changes.
Failures produce explicit HTTP errors; no model answer is substituted. See the root README for
key setup, costs and deployment restrictions.
