# How the dashboard works

Think of it as a traffic planner for a stadium's mobile network: it predicts
where the network may get crowded and suggests ways to ease the pressure.

```mermaid
flowchart TD
    A["Event details + past examples<br/>Crowds, weather and network usage"] --> B["Estimate how many people will attend"]
    B --> C["Predict which cell towers get busy<br/>and when"]
    C --> D["Watch incoming network readings<br/>Currently recorded synthetic data"]
    D --> E["Update the next-minute forecast<br/>Could congestion happen soon?"]

    E --> F{"Does the network<br/>need help?"}
    F -->|No| G["Keep watching"]
    G --> D

    F -->|Yes| H{"Can a nearby cell safely<br/>take some traffic?"}
    H -->|Yes| I{"How serious is the risk?"}
    I -->|Moderate| J["Recommend automatic traffic sharing"]
    I -->|High| K["Ask an engineer to approve"]
    H -->|No| L["Suggest backup measures<br/>and alert the operations team"]

    J --> M["Simulate the action<br/>Check whether it would help"]
    K --> M
    L --> M
    M --> D

    C --> N["What-if playground<br/>Try bigger crowds, extra capacity<br/>or different traffic-sharing plans"]
    N --> O["Compare the predicted results"]

    C -.-> P["Claude explains the results<br/>It does not calculate forecasts<br/>or control the network"]
    M -.-> P
    O -.-> P
```

Example: "A big crowd is expected. This cell may become overloaded. A nearby
cell has spare room - sharing some traffic could help."

Today, it is a simulation: it does not read a real Verizon live feed or change
actual network settings.
