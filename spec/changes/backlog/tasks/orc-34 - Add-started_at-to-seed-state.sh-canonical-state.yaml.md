---
id: ORC-34
title: Add started_at to seed-state.sh canonical state.yaml
status: Done
assignee: []
created_date: '2026-05-03 12:04'
updated_date: '2026-05-03 12:30'
labels:
  - bug
  - score-7.0
  - recurrence-1
  - follow-up-orc-27
dependencies: []
priority: high
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
seed-state.sh (shipped in ORC-27) writes `created_at` but not `started_at`. The orchestrator metrics resolver `_resolve_feature_metrics` (in config/scripts/orchestrator_next/record.py) requires `started_at` — when missing, `orchestrator done` exits with `feature_metrics_resolution_failed: state missing started_at/completed_at for schema=bugfix`.

Hit during the ORC-27 autopilot run itself: I had to manually edit state.yaml to add `started_at: '<created_at>'` so the workflow could proceed past mark-change-completed.

Fix: in skills/orchestrate/scripts/seed-state.sh, write `started_at` alongside `created_at` (same ISO timestamp) in the canonical-minimum field set. Add an AC test to test_seed_state.py asserting both keys are present in the seeded state.yaml.

## Why Now

ORC-27 shipped the seeder; this gap will bite the next /autopilot run that exercises mark-change-completed → compute-swe-metrics. Small follow-up (~5 lines + 1 test).

## Score
- User value: 7/10 (blocks completion path under autopilot)
- Strategic fit: 7/10 (closes the seed-state contract)
- Effort: XS
- Score: 7.0
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 seed-state.sh writes started_at and created_at with the same ISO timestamp
- [ ] #2 test_seed_state.py asserts both keys exist after seeding
- [ ] #3 orchestrator done on a freshly-seeded state.yaml does not error with 'missing started_at'
<!-- AC:END -->
