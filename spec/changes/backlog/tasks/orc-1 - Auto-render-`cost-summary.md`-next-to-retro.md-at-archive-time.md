---
id: ORC-1
title: Auto-render `cost-summary.md` next to retro.md at archive time
status: To Do
assignee: []
created_date: '2026-05-03 10:55'
updated_date: '2026-05-03 11:00'
labels:
  - slug-cost-summary-on-archive
  - feature
  - score-9.2
  - recurrence-1
dependencies: []
references:
  - >-
    Ideation session 2026-05-03 with the user — bundled #1+#4 from a 5-idea
    ranking.
priority: high
ordinal: 1000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
<!-- migrated from spec/changes/backlog.md slug: cost-summary-on-archive -->

**Original score:** 9.2 | **Recurrence:** 1

## Idea

Today, after a feature lands, all the cost / token / timing data exists in DuckDB (`feature_report` view + `step_events` table) but the user has to manually run `/telemetry` or `metrics-query.sh` to see it. The data is captured; the consumption side is manual.

Add a render step inside `mark-change-completed.yaml` (or `archive-completed-change.yaml`) that queries `feature_report` for the just-completed `change_id`, joins per-step rows from `step_events`, and writes `spec/changes/archive/<date>-<slug>/cost-summary.md` with:

- **Totals**: `cost_usd`, input/output/cache tokens, total tokens, wall-clock `duration_ms`
- **Per-agent breakdown** (from `per_agent_metrics` JSON in `feature_report`)
- **Per-phase timing** (group `step_events` by phase: "specify 14m, implement 38m, complete 6m")
- **Top 3 most expensive steps** (by `cost_usd` from `step_events`)
- **Per-step timing detail** (sub-bullets under each phase: "execute-next-task 31m [hot retry]")

Folds in idea #4 from the cost-metrics ideation session — step-grain timing — into the same template so it ships with the summary in one feature instead of two.

## Why Now

- All the SQL infrastructure exists (`feature_report` view shipped via `report-views-retire-cli`, `step_events` shipped via `subprocess-per-step-observability`). The work is one template + one step-contract edit.
- Closes the loop: every feature ends with a self-contained cost report next to its `retro.md`. No more "where did the time go on that one?" guessing.
- Prerequisite for `cost-delta-baseline` — having the rendered template makes it trivial to add a "Nx median" line later.
- Cheap precursor to the autopilot cost tail and to any future regression-detection work.

## Scope

1. New SQL query (or named query in `metrics-query.sh`): `feature-summary <change_id>` returning the full row + per-step rollup.
2. New template at `config/templates/feature/cost-summary.md` with sections for totals, per-agent, per-phase, top-3 steps.
3. Edit `config/steps/archive-completed-change.yaml` (or `mark-change-completed.yaml`) to render the template into the archive directory.
4. Test: archive a small completed feature and assert `cost-summary.md` exists with non-empty sections.

## Out of scope

- Comparison vs baseline (covered by `cost-delta-baseline`).
- Real-time emit during autopilot (covered by `autopilot-cost-tail`).
- Changing `feature_report` view shape — read-only consumer.

## Priority

- User value: 9/10 (visibility every feature, zero manual queries)
- Strategic fit: 9/10 (closes the metrics consumption loop)
- Technical leverage: 8/10 (~50 lines: template + SQL + step edit)
- Effort: small
- **Score: 8.4**

## Source

- Ideation session 2026-05-03 with the user — bundled #1+#4 from a 5-idea ranking.

---
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 New SQL query (or named query in `metrics-query.sh`): `feature-summary <change_id>` returning the full row + per-step rollup.
- [ ] #2 New template at `config/templates/feature/cost-summary.md` with sections for totals, per-agent, per-phase, top-3 steps.
- [ ] #3 Edit `config/steps/archive-completed-change.yaml` (or `mark-change-completed.yaml`) to render the template into the archive directory.
- [ ] #4 Test: archive a small completed feature and assert `cost-summary.md` exists with non-empty sections.
<!-- AC:END -->
