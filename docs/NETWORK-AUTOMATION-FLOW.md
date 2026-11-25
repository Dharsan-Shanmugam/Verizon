# Proposed network automation workflow

Based on [new.md](new.md).

This diagram describes a proposed design, not the current dashboard's implemented
capabilities. Real telemetry intake, ranked band/handover/TDD control, ping-pong
evaluation, and RAN-controller execution are proposed integrations. The current
dashboard uses synthetic data and simulated actions.

```mermaid
%%{init: {"theme": "base", "themeVariables": {"fontFamily": "Arial, sans-serif", "fontSize": "16px", "lineColor": "#64748B", "primaryTextColor": "#0F172A", "clusterBkg": "#F8FAFC", "clusterBorder": "#CBD5E1"}, "flowchart": {"curve": "linear", "nodeSpacing": 45, "rankSpacing": 55, "padding": 20, "htmlLabels": true}}}%%
flowchart TB
    subgraph DATA ["1. UNDERSTAND | Data & context"]
        direction LR
        A["EVENT CONTEXT<br/>Turnout, category<br/>and home / away"]
        B["HISTORICAL DEMAND<br/>10+ years of similar<br/>venues and events"]
        C["NETWORK READINGS<br/>15-second load, users<br/>and signal quality"]
        D["CAPACITY & TOPOLOGY<br/>Bands, sectors<br/>and backhaul limits"]
        A ~~~ B ~~~ C ~~~ D
    end

    subgraph PREDICT ["2. ANTICIPATE | Predictive intelligence"]
        direction LR
        M1["MATCH SIMILAR EVENTS<br/>Align the event profile<br/>with historical patterns"]
        M2["PREDICT CONGESTION<br/>Identify cell-level uplink risk<br/>1-3 minutes ahead"]
        M1 --> M2
    end

    DATA --> PREDICT
    PREDICT --> RISK{"Congestion<br/>expected?"}
    RISK -->|"No"| MONITOR["MONITOR<br/>Maintain baseline<br/>and continue ingestion"]
    RISK -->|"Yes"| SAFE{"Safe relief path?<br/>Radio headroom >=15%<br/>Backhaul utilization <=85%"}

    subgraph OPTIONS ["3. SELECT | Least-disruptive available option"]
        SELECT["RANK AVAILABLE LEVERS"]
        L1["1 | BAND STEERING<br/>Move devices to a quieter band<br/>Intra-site: B13 to n77"]
        L2["2 | OFFSET HANDOVER<br/>Adjust CIO / A3 offsets<br/>Share load with an adjacent cell"]
        L3["3 | TDD CONFIGURATION<br/>Use an uplink-heavy slot pattern<br/>Check neighbor alignment"]
        SELECT --> L1
        SELECT --> L2
        SELECT --> L3
    end

    SAFE -->|"Safe path"| SELECT
    SAFE -->|"No safe path"| FALLBACK

    subgraph GOVERN ["4. GOVERN | Approval proportional to risk"]
        AUTO["TIER 1 | AUTOMATIC<br/>Pre-approved policy<br/>for low-risk changes"]
        HUMAN["TIER 2 | ENGINEER APPROVAL<br/>Human signoff<br/>for higher-risk changes"]
    end

    L1 --> AUTO
    L2 -->|"Small / low-risk offset"| AUTO
    L2 -->|"Significant load shift"| HUMAN
    L3 --> HUMAN

    subgraph TEST ["5. VALIDATE | Scenario simulation"]
        SIM["TEST EXPECTED IMPACT<br/>PRB load, session drops and crowd spikes<br/>Check repeated back-and-forth handovers"]
        PASS{"Simulated outcome<br/>satisfactory?"}
        SIM --> PASS
    end

    AUTO --> SIM
    HUMAN --> SIM
    PASS -->|"Pass"| EXEC["EXECUTE ON RAN CONTROLLER<br/>Log the change and its rationale<br/>Set auto-revert timers"]
    PASS -->|"Degradation predicted"| FALLBACK
    FALLBACK["TIER 3 | SAFE FALLBACK<br/>Keep a conservative baseline<br/>Alert operations and escalate"]

    EXEC --> LEARN["6. VERIFY & LEARN<br/>Compare actual outcomes with forecasts<br/>Feed findings into future event predictions"]

    classDef input fill:#F1F5F9,stroke:#CBD5E1,stroke-width:1.5px,color:#0F172A;
    classDef intelligence fill:#0F172A,stroke:#0F172A,stroke-width:1.5px,color:#FFFFFF;
    classDef decision fill:#EFF6FF,stroke:#3B82F6,stroke-width:1.5px,color:#1E3A8A;
    classDef automatic fill:#ECFDF5,stroke:#10B981,stroke-width:1.5px,color:#065F46;
    classDef approval fill:#FFFBEB,stroke:#F59E0B,stroke-width:1.5px,color:#92400E;
    classDef fallback fill:#FEF2F2,stroke:#DC2626,stroke-width:1.5px,color:#991B1B;
    classDef simulation fill:#F5F3FF,stroke:#8B5CF6,stroke-width:1.5px,color:#5B21B6;

    class A,B,C,D,MONITOR input;
    class M1,M2,SELECT,LEARN intelligence;
    class RISK,SAFE,PASS,L2 decision;
    class L1,AUTO,EXEC automatic;
    class L3,HUMAN approval;
    class FALLBACK fallback;
    class SIM simulation;

    style DATA fill:#FFFFFF,stroke:#CBD5E1
    style PREDICT fill:#F8FAFC,stroke:#CBD5E1
    style OPTIONS fill:#FFFFFF,stroke:#CBD5E1
    style GOVERN fill:#F8FAFC,stroke:#CBD5E1
    style TEST fill:#FFFFFF,stroke:#CBD5E1
```
