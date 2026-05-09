---
id: ORC-40
title: >-
  Populate step_history/per_agent_metrics/per_step_metrics from step_events so
  metrics-query.sh works
status: Done
assignee:
  - '@claude'
created_date: '2026-05-08 12:04'
updated_date: '2026-05-09 11:03'
labels:
  - feature
  - orchestrator
dependencies: []
references:
  - >-
    https://linear.app/home-labs-experiments/issue/HL-286/duckdb-normalized-tables-for-step-history-per-agent-per-step-metrics
priority: medium
ordinal: 37000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Consolidates ORC-40 and ORC-41.

The three tables (step_history, per_agent_metrics, per_step_metrics) exist but have zero rows.
metrics-query.sh queries them for recent-features, step-cost-hotspots, agent-cost-hotspots,
agent-duration-outliers. /telemetry and /learn silently fall back to YAML globbing every time
because the tables are always empty.

The data already lives in step_events and feature_report — it needs to be synced into these tables.

Root cause: register-repo.sh (or equivalent ingest path) was never updated to populate the three
tables from step_events after the DuckDB migration.

Fix:
A. Add a migration/view or ingest script that populates step_history, per_agent_metrics,
   per_step_metrics from step_events + feature_report on demand (or as views).
B. Verify metrics-query.sh recent-features, retry-hotspots, step-cost-hotspots all return data.
C. Verify /telemetry no longer falls back to YAML globbing when DuckDB has data.
D. Verify /learn retry-hotspots query returns data.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 register-repo.sh creates step_history, per_agent_metrics, per_step_metrics tables
- [ ] #2 Ingest populates tables from state.yaml on every register-repo run
- [ ] #3 /learn and /telemetry queries run against DuckDB tables without json_extract
- [ ] #4 per_agent_metrics populated from step_events GROUP BY (repo_root, change_id, agent_name)
- [ ] #5 per_step_metrics populated from step_events GROUP BY (repo_root, change_id, step_id)
- [ ] #6 features table populated from feature_report for recent-features query
- [ ] #7 metrics-query.sh recent-features returns rows for completed features
- [ ] #8 metrics-query.sh step-cost-hotspots and agent-cost-hotspots return non-empty results
- [ ] #9 ORC-41 absorbed: /learn retry-hotspots query returns data without YAML fallback
<!-- AC:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Replaced three dead empty tables (features, per_agent_metrics, per_step_metrics) with DuckDB views over step_events / feature_report.

Root cause: register-repo.sh ingested data from .metrics.per_agent_tokens YAML blocks that no longer exist in post-DuckDB state.yaml files — all new archives have no metrics: key, leaving all three tables permanently empty.

Fix: migration 0005 drops the tables and recreates them as views:
- features → feature_report JOINed with step_events for timestamp bounds
- per_agent_metrics → step_events GROUP BY agent_name (tokens, cost, duration, steps)
- per_step_metrics → step_events GROUP BY step_id

register-repo.sh: removed all dead CREATE TABLE / upsert blocks for these tables. Only real ingest tables (step_history, per_agent_tool_uses, per_tool_uses) remain.

Verified: features=98 rows, per_agent_metrics=137, per_step_metrics=271. All metrics-query.sh commands (recent-features, agent-cost-hotspots, step-cost-hotspots, cycle-count) return live data. 365 tests still passing.
<!-- SECTION:FINAL_SUMMARY:END -->
