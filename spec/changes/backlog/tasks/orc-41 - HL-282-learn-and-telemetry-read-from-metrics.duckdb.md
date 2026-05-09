---
id: ORC-41
title: 'HL-282: /learn and /telemetry read from metrics.duckdb'
status: Done
assignee: []
created_date: '2026-05-08 12:04'
updated_date: '2026-05-09 11:06'
labels:
  - improvement
  - orchestrator
dependencies: []
references:
  - >-
    https://linear.app/home-labs-experiments/issue/HL-282/learn-and-telemetry-read-from-metricsduckdb
priority: medium
ordinal: 38000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Both /learn and /telemetry still glob spec/changes/archive/*/state.yaml and parse YAML inline. Now that $ORCHESTRATOR_HOME/metrics.duckdb exists, both consumers should query DuckDB via duckdb -csv for cross-repo aggregations. Closes the producer-consumer loop opened by the DuckDB metrics pipeline.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 /learn queries metrics.duckdb instead of globbing YAML archives
- [ ] #2 /telemetry queries metrics.duckdb instead of globbing YAML archives
- [ ] #3 Cross-repo aggregations work correctly via DuckDB
<!-- AC:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Fixed three stale json_extract paths in metrics-query.sh that caused /learn and /telemetry to return empty results even after ORC-40 populated the views.

Root cause: payload_json in the features view is to_json(feature_report), which uses flat column names (cost_usd, review_score_avg). The queries used old nested paths from the pre-DuckDB YAML metrics: block ($.metrics.cost_usd, $.metrics.quality_score).

Fixes:
- cost-trend: $.metrics.cost_usd → $.cost_usd
- quality-trend: $.metrics.quality_score → $.review_score_avg
- retry-hotspots: was querying embedded $.step_history in payload_json (old YAML format) → rewritten to query step_events WHERE attempt > 1

Verified: cost-trend returns real USD values, retry-hotspots returns execute-next-task with 9 retries across 4 features. 365 tests passing.
<!-- SECTION:FINAL_SUMMARY:END -->
