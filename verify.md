# Backfill Row Counts (AC-6)

Command run:
```
ORCHESTRATOR_HOME=/Users/spidey/code/orchestrator \
METRICS_DB=/Users/spidey/code/orchestrator/metrics.duckdb \
bash config/scripts/register-repo.sh --rebuild /Users/spidey/code/orchestrator
```

Output:
```
registry: already registered /Users/spidey/code/orchestrator
rebuild: deleted existing rows for /Users/spidey/code/orchestrator
metrics: ingested=11 skipped=3 failed=0 db=/Users/spidey/code/orchestrator/metrics.duckdb
```

Row counts after rebuild:
```
┌───────────────────┬─────┐
│ t                 │ cnt │
├───────────────────┼─────┤
│ features          │  11 │
│ step_history      │ 112 │
│ per_agent_metrics │  10 │
│ per_step_metrics  │   0 │
└───────────────────┴─────┘
```

**Note:** `per_step_metrics = 0` is expected. The `metrics.per_step` field is populated by the
`feature/metrics-capture-and-workflow-streamlining` branch which has not yet merged. Once that
branch merges, re-running `register-repo.sh --rebuild` will populate `per_step_metrics`.
`step_history > 0` (112 rows) and `per_agent_metrics > 0` (10 rows) confirm the new ingest
logic is working correctly.
