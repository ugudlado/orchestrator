# Real Telemetry Dashboard Implementation

## Idea
The `/telemetry` skill reads archived state.yaml files (`spec/changes/archive/*/state.yaml`) for metrics data. Build it out to compute trends across features and render a structured dashboard showing: (1) cost trend (USD per feature over time), (2) pass@1 and pass@2 rates, (3) rework rate trend, (4) retry hotspots by step contract, (5) resolution rate by schema type, (6) review score distribution. This directly consumes the SWE metrics that `compute-swe-metrics` writes to state.yaml.

## Why Now
The metrics pipeline is complete -- `compute-swe-metrics` writes to state.yaml, archived state.yaml files contain full metrics blocks, `/learn` reads them for quality bar adjustment, and `compute-prediction-accuracy` adds prediction data. The only missing piece is a human-readable dashboard. Without it, the metrics data accumulates but nobody sees the trends.

## Prototype
```
/telemetry

Workflow Health Dashboard (last 10 features)
=============================================

Cost Trend:
  avg: $2.34/feature   trend: -12% (improving)
  cost/task: $0.47      benchmark: SWE-bench median $3.20

Resolution:
  pass@1: 78%  pass@2: 94%  resolve_rate: 96%
  regression_rate: 0.0%

Quality:
  avg review score: 9.2/10  green_base: 9.0
  rework_rate: 0.15  (target: <0.20)

Retry Hotspots:
  1. execute-next-task: 2.3 retries/feature (type-check failures)
  2. run-phase-review: 1.1 retries/feature (scope findings)

Prediction Accuracy:
  task count: 87% accurate  file overlap: 72%
```

## Priority
- User value: 7/10
- Strategic fit: 6/10
- Technical leverage: 4/10
- Effort: medium
- **Score: 2.8**
