# Executive dashboard: recommendations and live service

The interface has two pages. All data and actions are synthetic simulations.
The midnight-navy and teal palette separates navigation and selections from
service alerts: rose indicates elevated exposure, amber indicates attention,
and green indicates passing checks. Live charts use navy solid observations
and indigo dashed forecasts; color does not replace status labels or line styles.

## 1. Forecast & recommendations

**Question:** What could cause latency or packet loss, and what should we prepare?

- Select an event or filter the portfolio by venue.
- The planning-posture band summarizes scenario exposure and recommended focus
  areas. It is an advisory assessment, not a measured service status.
- Four clickable KPI cards show expected turnout, peak total scenario uplink
  throughput, highest cell PRB utilization and the estimated connected-device count.
  PRB and uplink throughput switch the single graph to that metric with all-cell
  lines. Connected devices shows the selected event's device-mix estimate as a
  count chart, not a live reading or an invented per-cell/time-varying forecast.
  User-throughput scenarios remain available in the cell forecast table.
  Turnout shows the event-level lower,
  expected and upper estimates, not invented per-cell attendance curves.
  The selected metric persists when changing events.
- RTT means round-trip delay: how long it takes a packet to go and return.
  Packet loss is the percentage that does not arrive.
- Compact planning-priority cards show the issue, recommended preparation,
  priority and responsible team. **View recommendation** opens the complete
  evidence, service impact, remedy and scenario assumptions.
- Uplink PRB utilization pressure: prepare bounded traffic steering or temporary
  capacity to protect uplink and user throughput.
- Backhaul capacity pressure: check queues, alternate-path capacity and bandwidth needs.
- TDD uplink slot allocation readiness: check whether enough time slots are assigned to upload
  traffic; stage a new profile only after checking neighboring cells and downloads.
- The visible **Venue-serving cell forecast** combines every cell in one graph,
  with distinct colored lines and a cell legend. It spans six hours before to
  six hours after the event, with an event-start marker. The 85% planning line
  appears only for PRB utilization, not for throughput.
  Changing events redraws all cell forecasts together. This combined view shows
  point forecasts, not overlapping uncertainty bands.
- The five-minute display is interpolated from the trained hourly shape.
  **Cell forecasts & assumptions** shows cell/site/sector/band, peak load and band,
  in a collapsible table with horizontal scrolling on smaller screens, alongside
  nominal traffic scenarios, P50/P05 scenarios, peak exceedance risk and
  approximate threshold start/peak/recovery. These are not all independently
  evaluated forecasting outputs. Social signals support, not determine, turnout.
- A preparedness catalogue covers capacity, steering, TDD, mobility, transport,
  physical event capacity, commissioned resources and operating guards.
  The engineering readiness table is recomputed for the selected event and venue:
  priority, review, target gap, baseline or not triggered, with forecast evidence
  and timing. Neighbor headroom is a planning screen, not a live safety clearance.
  Busy n77 cells prompt TDD review, not a claimed slot shortage. Baseline guards
  remain visible even when no pressure is predicted.
- **Confidence & evidence** shows the tested 30-minute load/risk models, not an
  accuracy claim about the illustrative latency/loss response.

**There is no approval on page 1.** Recommendations do not execute or unlock
anything. **Observe live service** opens page 2 for the recorded target event.

The RTT/loss figures are scenario estimates derived from predicted load and
modeled transport pressure. They are not measured SLAs or independently
backtested end-to-end service forecasts.

## 2. Live operations

**Question:** Is service suffering now, what is the likely issue, and which remedy is safe?

- The replay starts near the first pressure window. Default 15x replay speed
  advances one 15-second sample per wall-clock second.
- Tiles show synthetic RTT, packet loss, pending decisions and predicted radio
  load 30 minutes ahead.
- The operating-posture band prioritizes specialist attention, engineer
  decisions, service exposure, validation and forecast watches. Counts track
  decisions and verified simulations, not real network outcomes.
- There is **one combined radio-load graph**: live observations on the left,
  the replay cursor at "Now", and trained 5/10/15/20/25/30-minute forecasts on the right.
  Latency and packet loss stay in the KPI cards. The shaded band is calibrated
  for radio load, not for end-to-end service quality.
- The graph leads the operating picture; remedy progress follows it.
  **Decision priorities** retains live investigations and approval controls.
- Select a cell for its service history and safety envelope.
- Investigations explain whether service degradation coincides with radio
  pressure, transport pressure, too few uplink TDD slots, or an RF impairment.
  Unmatched degradation escalates rather than receiving a guessed automatic fix.
- Each issue has a short label, such as **Radio surge**, **TDD imbalance** or
  **Backhaul congestion**, plus several evidence-backed suspected causes.
  "Supported" means consistent with the samples, not a confirmed diagnosis.
- Six future-bin chips show learned congestion-rule probabilities for each
  five-minute interval. The visible assurance layer links to measured synthetic
  forecast and operational scorecards; unavailable real benefits are not zeros.
- Elevated forecast risk can create a **Forecast watch** and read-only RCA before
  a current threshold breach. A prediction alone never authorizes a change.
  If it clears, the case records that it cleared without action.

### Authority exists only here

| Tier | Handling |
|---|---|
| 1 | Bounded, pre-approved automatic steering when all live checks pass. No page-1 approval is needed. |
| 2 | Engineer reviews and approves the current ranked remedy. TDD profile changes are always in this tier or higher. |
| 3 | No safe executable remedy: escalate to operations / radio / transport specialists. |

### Investigation and validation

- **Investigate** opens a live side panel. Streaming continues unless paused.
- A live **Situation brief** immediately below the four-part RCA heading names the
  issue, explains the evidence-backed likely cause and why policy requires
  automatic handling, engineer review, escalation or monitoring. During validation
  it distinguishes an authorized simulation from a verified result; held and
  rolled-back actions remain clearly identified.
  A concise **Likely cause / Evidence** list shows peak forecast load, active-user
  change, SINR, BLER, an actual qualifying neighbor and transport utilization,
  with numeric backing. Relevant TDD, transport, compute or availability evidence
  is added for those issues. Missing evidence is explicit; values within policy
  ranges are not claimed to prove a stable trend or authorize an action.
  Four collapsible supporting groups expose demand/queues/users, SINR/BLER/HARQ,
  neighbor headroom/quality/handovers, and transport/availability/compute.
  Their open state is preserved during updates. Poor SINR and retransmissions
  route to RF/coverage diagnosis rather than an automatic capacity-only fix.
- Evidence, ranking and available controls update together. Changed evidence
  invalidates a stale approval request; the engineer must review it again.
- Engineer approval is now two steps: **Review rank N approval** pauses replay,
  waits for any in-flight update and fetches current evidence. The paused review
  shows exact parameters and projected effects. **Confirm rank N approval** then
  submits that reviewed snapshot. Changing options, moving/resuming the replay
  or receiving newer server evidence clears the confirmation. No automatic retry
  applies a new recommendation without consent.
- Several remedies can be considered for one issue: limited or standard steering
  profiles, different TDD uplink slot shares, and specialist remediation.
  Unsafe or insufficient profiles are shown as blocked. Available profiles are
  ranked by safety, approval tier, disruption and remaining headroom. Engineers
  can select another eligible Tier 2 option rather than silently applying it.
- Ranking shows actual comparable synthetic outcome counts and disclosed context
  gaps. The action detail is a vendor-neutral profile intent with scope, duration
  and guardrails. Carrier activation, scheduler, beam, power and prepared transport
  actions stay non-executable without commissioned resources and approved adapters.
- Running, completed and held actions cannot be approved again.
- Steering checks target headroom, site/shared transport and projected target
  RTT/loss. A TDD change checks neighbor alignment and downlink utilization.
- Source and target cells are reserved to prevent overlapping changes.
- Validation runs on **every 15-second replay sample** after application, for up
  to 120 replay seconds. It checks projected service recovery, target impact,
  radio and transport safety. Eight safe checks complete validation; any unsafe
  check triggers early rollback/escalation. Missing evidence blocks validation.
- A failure rolls back the simulation and blocks automatic retries for the run.
- Recorded RTT, loss and load are not rewritten to pretend a real change occurred.
- Rejecting or escalating holds the cell until a new replay run.

### Investigation history

The single history table records the current run's cases and timestamps. Filter
by **Agent resolved**, **Engineer approved** or **Escalated**, search by issue/cell,
or restrict the window to the last 5/15 replay minutes.

**View trail** shows:

- Original detection evidence and latest suspected causes.
- The chosen remedy and agent/engineer authorization.
- Exact simulation parameters before/after: steering share/target, or TDD slot
  share and its modeled radio/downlink factors.
- RTT/loss effects compared at the same replay timestamp with the no-action
  baseline. These are counterfactual comparisons, not real recovered measurements.
- Each validation sample and the final pass/rollback outcome.
- Detection, ranking, authorization, parameter-change and validation events.

Cases and actions persist in the local audit state. Older actions are retained
as legacy rows; missing historical details are not reconstructed as facts.

### Replay controls and alerts

- Pause/resume, change speed, advance the slider or start a new replay.
- An active run cannot rewind; a new run preserves previous action history.
- One investigation alert per cell/run updates in place. At most two popups are
  visible; other messages remain in the notification center.
- At the final sample, **New replay run** replaces Resume. Completion is not
  repeatedly announced and no future readings are invented.

## Data and limits

- The added service CSV contains a populated 15-second series for every cell
  in the recorded target event.
- Original file 25 already contains synthetic transport latency and loss.
  The new CSV combines those fields with an explicit radio-queue model and
  clearly synthetic TDD / RF test cases. See [provenance](../data/SERVICE-QUALITY.md).
- Simulation policy budgets are 50 ms RTT and 1% packet loss, sustained for three samples.
  Real service-specific budgets require network-owner approval.
- TDD effects and capacity gains are simplified simulation assumptions, not
  validated hardware behavior. Real slot-pattern changes need authorized APIs,
  synchronization, licensing and operational review.
- The dedicated radio-load outlook was trained on 19 events, calibrated on 6
  and tested on 8 later events. Its results are not validation of the QoS model.
- Forecasting uses ML; issue/remedy/tier/validation logic is rule-based.
- No real network control or external social listening is connected. Claude is
  not required to run either page.
