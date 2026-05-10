---
id: ORC-6
title: Metrics Regression Detection + Regression-Breaker Exit Code
status: To Do
assignee: []
created_date: '2026-05-03 10:55'
updated_date: '2026-05-10 10:54'
labels:
  - slug-metrics-regression-detection
  - feature
  - score-7.8
  - recurrence-1
dependencies: []
priority: medium
ordinal: 5000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
<!-- migrated from spec/changes/backlog.md slug: metrics-regression-detection -->

**Original score:** 7.8 | **Recurrence:** 1

## Idea

Turn the metrics stack from a passive ledger into an active guardrail. Detect feature-level and step-level regressions against rolling baselines, surface them in `/telemetry`, and let an external loop runner halt on a `regression_breaker` exit code when the last 3 features each regressed.

## Why Now

Prerequisite: fix-cost-usd-and-widen-token-split (baselines on zeros are meaningless) and backfill-step-history-jsonl (needs full history). This caps the observability arc — once it lands, future regressions self-report.

## Evidence

- `execute-next-task` averaged ~19 min across 2 samples — no rolling baseline exists to flag drift.
- The `/learn` skill references "steps taking >2× average" but nothing produces that signal.
- Prior commit 62166d6 fixed a PyYAML indent bug that had silently corrupted step_history parsing — a drift alarm would have caught it days earlier.
- The cost_usd=0 regression that motivated the observability batch also went undetected for multiple features.

## Fix

1. New step `compute-metrics-regressions.yaml`, run after `compute-swe-metrics`.
2. New table `metrics_anomalies`:
   ```
   change_id, anomaly_type, metric, observed, baseline_median, ratio, detected_at
   ```
3. Flag:
   - feature `cost_usd` > 1.5× 30-day median (same schema)
   - step `duration_ms` > 2× median for same `step_id`
   - single-agent token spike > 2× median
4. Surface top anomalies in `/telemetry`.
5. Regression breaker: `/orchestrate` complete-phase exits with non-zero code (e.g. 23, `EXIT_REGRESSION_BREAKER`) when the just-shipped feature appears in `metrics_anomalies` AND the prior 2 features also did. External loop runners (autopilot wrapper scripts, remote-agent harnesses) can then stop calling `/autopilot` on regression. Writing the breaker as an exit code, not an in-process check, makes it composable with any runner — including the single-iteration thin-wrapper autopilot.

## Priority

- User value: 8/10 (passive surfacing + composable breaker, no per-runner integration)
- Strategic fit: 8/10
- Technical leverage: 8/10
- Effort: medium
- **Score: 7.8**

## Source

Reframed 2026-05-03 after autopilot collapse (commit fa6112d): the original "autopilot-iterate writes stop_reason to _checkpoint.json" mechanism is dead. Replaced with a CLI exit-code that any external runner can act on.

---
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 New compute-metrics-regressions.yaml step; new metrics_anomalies table (change_id, anomaly_type, metric, observed, baseline_median, ratio, detected_at)
- [ ] #2 Flag: feature cost_usd > 1.5x 30-day median; step duration_ms > 2x median; single-agent token spike > 2x median
- [ ] #3 Surface top anomalies in /telemetry
- [ ] #4 Regression breaker: complete-phase exits with EXIT_REGRESSION_BREAKER (e.g. 23) when just-shipped feature + prior 2 features all appear in metrics_anomalies
<!-- AC:END -->



## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Absorbed ORC-32 (read-sub-state-metrics.sh outdated paths ISSUE-26) — path fix is a prerequisite for regression detection baselines to have valid data. Fix must land first within this task's implementation.
<!-- SECTION:NOTES:END -->
