---
name: telemetry
description: "Show workflow metrics dashboard. Use when user says \"telemetry\", \"show metrics\", \"workflow health\", \"dashboard\"."
user-invocable: true
args: []
---

## Execution
1. Read feature-metrics.jsonl, state.yaml archives, and error-patterns.jsonl for telemetry data
2. Present formatted metrics dashboard with benchmark comparisons to user
