---
id: ORC-32
title: read-sub-state-metrics.sh uses outdated paths (ISSUE-26)
status: To Do
assignee: []
created_date: '2026-05-03 10:56'
updated_date: '2026-05-03 11:00'
labels:
  - slug-fix-read-sub-state-metrics-paths
  - bug
  - score-6.5
  - recurrence-1
dependencies: []
priority: low
ordinal: 31000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
<!-- migrated from spec/changes/backlog.md slug: fix-read-sub-state-metrics-paths -->

**Original score:** 6.5 | **Recurrence:** 1

## Idea

`config/scripts/read-sub-state-metrics.sh` looks for sub-feature state.yaml
at two paths, neither of which matches the current layout:

1. `$HOME/.workflows/<slug>/state.yaml` — this was the old location;
   active states live at `$REPO_ROOT/.state/<slug>/state.yaml` now.
2. `$REPO_ROOT/spec/changes/archive/<slug>/state.yaml` — archives are
   date-prefixed (`2026-04-19-<slug>`), so this glob misses them.

Consequence: autopilot's STEP D.5 (`Capture per-iteration metrics from
sub-feature state.yaml`) calls this script, it ERRORs, and the iteration
record in `sessions.yaml` gets zero-filled metrics. The whole point of
autopilot telemetry is undercut silently.

Fix: update the path lookup chain to:
1. `$REPO_ROOT/.state/<slug>/state.yaml` (active)
2. `$REPO_ROOT/spec/changes/archive/*-<slug>/state.yaml` (date-prefixed archive, newest wins)
3. Keep the old two paths as last-resort fallbacks for backwards compat.

## Why Now

Autopilot recently became the primary driver for capturing cross-run
metrics. Every iteration that finishes under the new layout writes
zeros to sessions.yaml. Cheap to fix; every day delayed is more
corrupt telemetry data to reconcile later.

## Source

spec/changes/archive/2026-04-19-fix-inline-scripts-tmpdir/retro.md §ISSUE-26

---
<!-- SECTION:DESCRIPTION:END -->
