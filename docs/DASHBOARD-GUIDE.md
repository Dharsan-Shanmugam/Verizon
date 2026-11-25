# Dashboard notes: a plain-English walkthrough

## What this dashboard does

Think of a stadium mobile network as a set of roads. People uploading photos,
videos and messages create traffic. Some roads get busy while others have room.
This dashboard predicts the busy spots, shows warnings, and lets you explore
ways to spread the traffic or add capacity.

**Important:** this is a proof-of-concept using synthetic data. The replay is a
recording, not a real Verizon live feed. Approvals are demo records, and suggested
network changes do not control real equipment. Historical savings and action
records describe the synthetic examples, not verified real-world results.

## Quick page map

| Page | The simple question it answers |
|---|---|
| Executive overview | Where should we pay attention? |
| Event forecast | Which upcoming events might overload the network? |
| Synthetic streaming replay / command center | What is happening at this moment in the recording, and what should we do? |
| Routing & backhaul | Can traffic be moved safely, and what happened in the example history? |
| What-if simulator | Would a different plan improve things? |
| Unplanned gatherings | Can we spot an unexpected crowd before problems begin? |
| Model health | How well are the predictions working? |
| Data catalog | What information is behind the dashboard? |

## A few words you will see

| Dashboard word | Everyday meaning |
|---|---|
| Cell | One part of the mobile network serving an area. A tower/site can have several cells. |
| Uplink / UL | Data sent from phones, such as uploading a video. |
| Downlink / DL | Data sent to phones, such as watching a video. |
| UL PRB % | How much of the available uplink radio resource is busy. Think of it as road occupancy, not a phone's speed. |
| Congestion | Network demand is high enough that users may experience poor service. The demo rule considers both load and slow-user speed over repeated readings. |
| Hot cell | A cell whose load is high enough to need attention or planning. |
| P10 speed | A measure of slower users' experience: roughly 10% of readings are at or below this speed. Higher is better. |
| Backhaul | The connection carrying traffic from a mobile site into the wider network. A spare radio cell is not enough if this connection is full. |
| Aggregation link | A shared connection collecting traffic from several site links. |
| Steering / routing | Encouraging some users or traffic to use another suitable cell. It is not physically moving people. |
| Headroom | Spare capacity available before reaching a safety limit. |
| Forecast band / interval | A range showing uncertainty around a prediction, not a guarantee. |
| Rollback | Undoing a proposed or simulated change when its checks fail. |
| KPI | A headline measurement, such as speed, errors or cost. |

The demo commonly uses **70% radio load as a watch level** and **85% as a
high-load planning level**. These are demo policy settings, not universal
network rules. A high-load forecast is a warning, not proof of an outage.

## 1. Executive overview

**Purpose:** the first page to open when you want the big picture.

### What you see

- **Six headline tiles:**
  - **Uplink P10 uplift:** improvement in slower users' upload speed in the
    example no-action versus with-action outcomes.
  - **Drop-rate reduction:** how much fewer interrupted sessions the examples show.
  - **Congestion minutes avoided:** reduction in overloaded cell-time.
  - **Temporary capacity cost avoidable:** estimated spending that might have
    been unnecessary in the examples.
  - **Engineer hours saved:** estimated reduction in manual effort.
  - **Forecast accuracy:** a simplified summary based on peak-load percentage
    error. It is not the probability that every forecast is correct.
- **Three things to know:** short summaries of the main findings.
- **Synthetic replay card:** whole-recording alert and decision counts, plus a
  button to open the command center. These are not current-cursor counts.
- **Uplink demand per event:** how upload demand changes across the years.
- **What fans upload:** the share of uploads belonging to different app groups.
- **Venue risk board:** upcoming activity at each venue, high-risk event counts,
  hot cells, the busiest predicted load, and event dots for the next 30 days.

### What you can do

Filter the board by venue and risk, change its sort order, or reset the filters.
Click a venue row to explore its forecasts. Click an event dot to open that event.

**How to read it:** use the board to choose where to investigate first.
The filters affect the board only; headline tiles and trend charts remain global.

## 2. Event forecast

**Purpose:** prepare before an event starts.

### What you see

- **Upcoming-events table:** event date, venue, crowd type, expected attendance,
  uncertainty band, predicted peak load, hot-cell count and risk.
- **Selected-event details:** the model's crowd estimate alongside the
  organiser's estimate and the venue capacity.
- **Maximum peak UL PRB:** how busy the busiest cell is expected to become.
- **Cells needing a plan:** how many cells may need help, plus a watch-level count.
- **Cell-by-hour heatmap:** rows are cells; columns are hours from six hours
  before to six hours after the start. Stronger/hotter colours mean busier cells.
- **Context signals:** available clues used for the attendance prediction,
  such as demand, event and weather information.
- **Peak forecast per cell:** a prediction, uncertainty range and estimated
  chance of exceeding the high-load threshold.
- **Most similar past events:** earlier examples that help explain the forecast.

### What you can do

Filter by venue or risk, click an event row, and hover over the heatmap for detail.
Use **What-if** to open that event in the simulator.

**Example reading:** "The crowd may be large, and two cells could get very busy
around the start. We should test a relief plan."

The forecast range expresses uncertainty. A displayed 90% band is designed to
cover many comparable outcomes; it does not promise that this event will fall inside it.

## 3. Synthetic streaming replay / command center

**Purpose:** watch a recorded event unfold and inspect decisions step by step.

### What you see

- **Clock and event phase:** where you are in the recording, relative to kick-off.
- **Cell tiles:** current recorded load, slower-user upload speed and upcoming
  congestion probabilities. Clicking a tile selects that cell.
- **Five current-value tiles:**
  - **Observed now:** the selected cell's recorded load.
  - **Historical forecast now:** what the event-based model expected at this time.
  - **Current forecast error:** observed load minus historical forecast.
    If the forecast is 70% and the observation is 80%, the error is **+10 percentage points**.
  - **Next 60 s forecast:** an updated estimate using observations available so far.
  - **5-min adaptive MAE:** the average size of recent next-minute prediction
    mistakes, ignoring whether they were too high or too low. Lower is better.
- **Routing required right now?:** a fresh recommendation for every cell,
  including a possible target, approval path and whether projected relief is sufficient.
- **Current decision trace:** the checks explaining the selected recommendation.
- **No-action versus proposed-action comparison:** a hypothetical source/target
  load comparison for one recommendation, not a real action or a combined plan.
- **Forecast versus actual chart:**
  - Grey: historical-model curve.
  - Black: observations available up to the replay cursor.
  - Green: 60-second predictions whose target time has now arrived.
  - Red: the next-minute estimate issued at the cursor.
- **Congestion-risk chart and alerts:** warnings about possible trouble.
- **Recommended action and decision log:** recorded timeline proposals and their
  simulated workflow, including approval, verification or rollback.
- **Backhaul now:** how busy the connecting links are at the cursor.
- **Approval audit trail:** locally saved demo approval/rejection records.

### What you can do

Play or pause, change playback speed, drag the time slider, jump to kick-off,
the next alert, congestion, or an engineer-approval event. Select cells, alerts
or decisions to inspect their details. Approve or reject a pending demo proposal.

### What the approval tiers mean

| Tier | Plain-English interpretation |
|---|---|
| 0: Monitor | No intervention recommended; keep watching. |
| 1: Automatic | The policy considers the move eligible for automatic handling in the simulation. |
| 2: Engineer approves | The proposed move needs a person's approval before simulated execution. |
| 3: Fallback | No safe steering option was found; consider backup measures and operations escalation. |

**Important distinctions:**

- A current recommendation is not proof that an action has happened.
- The current routing table rechecks conditions now; the decision log records
  earlier proposals. They can differ without contradicting each other.
- Separate safe-looking proposals are not necessarily safe to apply all at once.
- Actions do not change the recorded black observation curve.
- Adaptive error is unavailable at the beginning: a 60-second prediction needs
  60 seconds of replay time before it can be checked.
- Top-of-page replay totals describe the whole recording, while current-value
  tiles and snapshot errors describe the selected cursor.

## 4. Routing & backhaul

**Purpose:** explain why moving traffic is sometimes safe and sometimes blocked.

### What you see

- **Decision funnel:** how many example recommendations passed safety checks,
  reached execution and passed verification.
- **Approval-tier breakdown:** the mix of monitoring, automatic, engineer-approved
  and fallback decisions.
- **Why moves were blocked:** reasons a suggested move was considered unsafe.
- **Backhaul trends:** how busy site and shared links became across the years.
  The median is a typical reading; the 90th percentile describes the busier end,
  not the absolute worst reading.
- **Target-venue backhaul chart:** recorded link load through the example event.
- **Network settings changed:** counts of parameter changes in the synthetic history.
- **Steering-actions table:** recent example changes, source and target cells,
  old/new settings, approval tier, ticket and reversal information.

### What you can do

Search the action table to find a cell, parameter, ticket or other matching text.

**Example reading:** "The nearby cell has spare radio capacity, but its connection
to the wider network is already busy. Moving traffic there may just relocate the problem."

The demo checks target-site backhaul against an 85% limit and aggregation
against a 90% limit. This page is largely historical and full-recording analysis;
it is not synchronized to the command-center cursor.

## 5. What-if simulator

**Purpose:** try a plan without touching a real network.

### What you can change

- **Event:** choose the event to investigate.
- **Crowd type:** scheduled, home, away or neutral.
- **Attendance versus forecast:** 100% uses the forecast crowd; 120% tests a
  crowd 20% larger. The slider allows 30-130%.
- **Hot-cell traffic steered:** test sharing up to 50% of hot-cell traffic.
  The requested amount is subject to the simulator's neighbour/headroom limits.
- **Temporary upload capacity:** test up to 1000 Mbps of extra capacity.
- **Presets:** No mitigation, Crowd surge, Balanced relief, Extra capacity and Away crowd.

### What you see

- **Before/after tiles:** peak load, hot-cell count, congestion cell-minutes,
  session drops, slower-user speed, accessibility and backhaul load.
- **Peak load per cell:** which cells benefit and which receive extra traffic.
- **Event-day profile:** how the busiest cell's load changes through the event.
- **Backhaul comparison:** whether the connecting links improve or become busier.
- **Moves applied:** the traffic-sharing moves used in the hypothetical calculation.
- **Plan comparison:** a pinned plan alongside the current result.
- **Saved scenarios:** named plans stored in this browser, with load, compare and delete controls.

### A simple exercise

1. Select an event and choose **No mitigation**.
2. Click **Pin current result**.
3. Choose **Crowd surge** to see how extra demand changes the result.
4. Increase steering or temporary capacity and compare the two plans.
5. Check backhaul and slower-user speed, not just the busiest cell.
6. Save a useful result with a descriptive name.

Up to eight plans can be saved. They survive a reload but are not shared across
browsers or computers. **Reset** resets the scenario inputs; **Clear comparison**
removes the pinned comparison.

**Important:** this is a simplified planning model, not a guarantee. A
cell-minute counts one cell congested for one minute: two cells congested for
five minutes each means ten cell-minutes, not ten minutes of event duration.

## 6. Unplanned gatherings

**Purpose:** investigate crowds that were not in the scheduled-event plan.

### What you see

- **Summary tiles:** gathering counts, how often social activity gave a warning,
  network-only warning time, and proposed traffic-sharing counts.
- **Gatherings by type:** which kinds of unexpected crowds appear in the examples.
- **Detection-lead table:** how early social clues or network readings warned
  about congestion. Social lead is shown in minutes; network lead in seconds.
- **Target-venue list:** example gatherings, crowd sizes, traffic surges and
  whether social warning was available.
- **Replay chart:** busiest-cell and average venue load.
- **Time markers:** when a social warning appeared, the crowd formed,
  the network warning appeared and congestion started, where available.

### What you can do

Choose a gathering from the dropdown or list, then replay or stop its animation.

**Example reading:** "There was no scheduled event, but rising network demand
still provided a warning. A social clue may provide extra notice when one exists."

Social clues and gathering records come from the supplied synthetic dataset;
the application is not monitoring real social-media services.

## 7. Model health

**Purpose:** check the quality of the predictions, rather than assuming they are right.

### What you see

- **Summary tiles:** peak-load percentage error, forecast-band coverage,
  attendance error and the next-minute warning model's quality.
- **Error over time:** whether predictions improved or worsened across the example history.
- **Bias and coverage charts:** whether estimates lean too high/low, and how
  often outcomes fall inside the uncertainty range.
- **Drift charts and alerts:** whether newer data looks different from the data
  the model learned from.
- **Hold-out backtest:** results on events kept separate from model fitting.
- **Feature-drift table:** which input measurements changed in distribution.
- **60/120/180-second warning table:** warning quality at each look-ahead time.
- **Model versions:** historical example versions alongside the current PoC model.
- **Per-cell drift table:** supplied event-monitoring summaries and alert counts.
- **Training runtimes:** how long model fitting took.

### How to read the main terms

| Term | Simple interpretation |
|---|---|
| MAE | Average size of a mistake in the original unit. A 5-point load MAE means an average error of five percentage points. Lower is better. |
| MAPE | Average size of a mistake relative to the actual value, expressed as a percentage. Lower is better; it is not the same as percentage-point error. |
| Bias | Whether errors tend to lean in one direction. Near zero is desirable, but opposite mistakes can cancel out. |
| Coverage | How often actual results fall inside the predicted range. Compare it with the intended 90% level. |
| Precision | Of the congestion warnings raised, how many were correct? |
| Recall | Of the congestion cases that happened, how many were caught? |
| AUC | How well the model separates risky from safe cases across possible warning thresholds. Higher is better; 1 is ideal and 0.5 is roughly chance. It is not "percent accurate." |
| PSI / drift | How different newer input patterns are from the reference patterns. The displayed watch/alert levels flag a need to investigate, not automatic proof the model is broken. |

**Example reading:** "The average error is rising and the crowd pattern has
changed. We should investigate whether the model needs updating."

This page mainly presents reports, not a model-training control panel. Historical
version charts and supplied event drift summaries are separate from the
command center's cursor-bounded adaptive error.

## 8. Data catalog

**Purpose:** show where the dashboard's information comes from.

### What you see

- **Summary tiles:** source-file count, row count, documented columns and
  columns marked as excluded from model inputs.
- **Files table:** each dataset's contents, coverage, size and grain.
  "Grain" means what one row represents, such as one event or one cell reading.
- **Column dictionary:** column names, data types and explanations.
- **Exclusion notes:** fields the model must not use, such as simulator
  answer-only information that would unfairly reveal the outcome.

### What you can do

Search files and column descriptions. Click a file row to focus the search on
that file. Clear the search to return to the broader catalog.

**Example reading:** "I want to understand the uplink-load number: which
dataset contains it, what does one row mean, and how is the field defined?"

This is a catalog and dictionary, not a full spreadsheet editor or data-upload page.

## Claude analyst: available below each page

The analyst explains selected dashboard facts in ordinary language. The local
forecast models calculate the numbers; Claude does not replace them or execute actions.

1. Configure a valid Anthropic API key on the server.
2. Load available models and select an exact model ID, or use the configured model.
3. Ask a question, then ask a follow-up if needed.
4. Use **Clear conversation** to discard the conversation.

Useful questions:

- Overview: "Which venue should I investigate first, and why?"
- Forecast: "Why does this event need a plan?"
- Replay: "Why does this cell need engineer approval?"
- Routing: "Explain why this move was blocked."
- Simulator: "Which plan helps slower users without overloading backhaul?"
- Gatherings: "How early was this crowd detected?"
- Model health: "Explain these error numbers without technical language."
- Catalog: "Which datasets describe event demand?"

Changing the selected context clears old advice. Asking about a replay pauses
playback so the explanation refers to a stable moment.

**Cost and limits:** simulator auto-analysis is enabled by default and waits two
seconds after scenario changes before requesting advice once a model is selected.
You can turn it off. Provider failures pause auto-analysis. Replay ticks do not
automatically call Claude. Requests send selected synthetic facts and your question
to Anthropic and may incur charges; do not enter confidential information.
A rejected or missing key prevents AI answers, but the normal dashboard still works.

## Suggested order for a demonstration

1. **Overview:** explain the overall problem and choose a risky venue.
2. **Forecast:** show when and where one event may become busy.
3. **Simulator:** compare no mitigation with a relief plan.
4. **Replay:** show warnings, current recommendations and approval steps.
5. **Routing & backhaul:** explain why safety checks matter.
6. **Unplanned gatherings:** show how unexpected crowds differ.
7. **Model health:** explain how prediction quality is checked.
8. **Data catalog:** show the documented data behind the demo.

For the overall flow diagram, see [How the dashboard works](FORECAST-FLOW.md)
or its [image version](FORECAST-FLOW.png).
