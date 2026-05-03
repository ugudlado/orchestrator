---
id: ORC-2
title: Surface "Nx median" delta line in cost-summary.md
status: To Do
assignee: []
created_date: '2026-05-03 10:55'
updated_date: '2026-05-03 11:00'
labels:
  - slug-cost-delta-baseline
  - feature
  - score-9.1
  - recurrence-1
dependencies:
  - ORC-1
references:
  - 'Ideation session 2026-05-03 with the user — idea #3 from a 5-idea ranking.'
priority: high
ordinal: 33000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
<!-- migrated from spec/changes/backlog.md slug: cost-delta-baseline -->

**Original score:** 9.1 | **Recurrence:** 1

## Idea

Once `cost-summary.md` exists per feature, add a single line comparing this feature against rolling baseline: "this feature was 1.4× median cost, 2.1× median tokens, 0.9× median wall-clock vs the prior 30 days."

Mechanism:

1. New DuckDB view `feature_baseline` computing 30-day rolling median (per `repo_root`) of `cost_usd`, `total_tokens`, `duration_ms` from `feature_report`. Use `PERCENTILE_CONT(0.5)` over a window.
2. New named query `cost-delta <change_id>` returning the ratios.
3. Update the `cost-summary.md` template (from `cost-summary-on-archive`) to include a "Delta vs baseline" section near the top.

This is the cheap precursor to the existing `metrics-regression-detection` backlog item — gives the *signal* (one line per feature) without the alerting / autopilot-breaker complexity. If a delta line consistently shouts "3× median" across 3 features, that's the trigger to invest in the full regression detector.

## Why Now

- Depends on `cost-summary-on-archive` — only meaningful once the template exists. Ships immediately after.
- Median-based deltas are immediately readable; humans don't need to query DuckDB to know "this one was expensive."
- Cheaper than the full regression-detection feature (no `metrics_anomalies` table, no autopilot breaker), but captures 80% of the user-visible value: "is this feature an outlier?"

## Scope

1. New migration adding `feature_baseline` view (window-based median, partitioned by `repo_root`).
2. New named query `cost-delta` in `metrics-query.sh` returning ratios for a given `change_id`.
3. Update `cost-summary.md` template to render delta section.
4. Test: median calculation against a fixture with 3+ features per repo.

## Out of scope

- Anomaly storage (`metrics_anomalies` table) — that's the regression-detection feature.
- Autopilot stop-on-regression breaker.
- Per-step deltas (just feature-level for now).
- Cross-repo deltas (per-`repo_root` only).

## Dependencies

- Hard: `cost-summary-on-archive` (template must exist).

## Priority

- User value: 7/10
- Strategic fit: 8/10
- Technical leverage: 8/10 (~30 lines SQL + small template edit)
- Effort: small
- **Score: 7.5**

## Source

- Ideation session 2026-05-03 with the user — idea #3 from a 5-idea ranking.

---
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 New migration adding `feature_baseline` view (window-based median, partitioned by `repo_root`).
- [ ] #2 New named query `cost-delta` in `metrics-query.sh` returning ratios for a given `change_id`.
- [ ] #3 Update `cost-summary.md` template to render delta section.
- [ ] #4 Test: median calculation against a fixture with 3+ features per repo.
<!-- AC:END -->
