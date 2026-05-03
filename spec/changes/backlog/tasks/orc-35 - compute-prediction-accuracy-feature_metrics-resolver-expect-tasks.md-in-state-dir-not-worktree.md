---
id: ORC-35
title: >-
  compute-prediction-accuracy + feature_metrics resolver expect tasks.md in
  state dir, not worktree
status: Done
assignee: []
created_date: '2026-05-03 12:04'
updated_date: '2026-05-03 12:34'
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
Two scripts expect tasks.md at \`<state_dir>/tasks.md\` but the actual location during a workflow run is \`<worktree>/spec/changes/<slug>/tasks.md\`:

1. \`scripts/inline/compute-prediction-accuracy.py\` — silently reports 0 predicted/actual tasks, yielding bogus 100% accuracy and rework_rate=0.
2. \`config/scripts/orchestrator_next/_resolve_feature_metrics\` (record.py) — hard-fails 'tasks.md not found at <state_dir>/tasks.md' on bugfix mark-change-completed. Worked around in ORC-27 run by symlinking the worktree tasks.md into the state dir.

Fix options:
- A: Have these scripts read state.yaml for \`worktree_path\` + \`change_id\` and look at \`<worktree>/spec/changes/<change_id>/tasks.md\` first, falling back to \`<state_dir>/tasks.md\`.
- B: Have the workflow always symlink tasks.md into the state dir at design-and-draft-artifacts time (one extra ln -s in architect agent).
- C: Move artifacts under state dir directly (bigger refactor — out of scope).

Lean toward B for minimal blast radius.

## Why Now

Hit during ORC-27 autopilot. Currently silently degrades metrics for every bugfix run; future runs will fail mark-change-completed without manual intervention.

## Note vs ORC-32

This is distinct from ORC-32 (which targets read-sub-state-metrics.sh path drift, an autopilot-telemetry concern). Same drift family, different scripts.

## Score
- User value: 7/10 (blocks autopilot completion silently)
- Strategic fit: 7/10
- Effort: S
- Score: 7.0
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 compute-prediction-accuracy.py finds tasks.md at <worktree>/spec/changes/<change_id>/tasks.md when state-dir copy is missing
- [ ] #2 _resolve_feature_metrics in record.py finds tasks.md at the same fallback path
- [ ] #3 Bugfix workflow runs end-to-end without the manual tasks.md symlink workaround
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Superseded by ORC-36 (consolidate active state under spec/changes/<slug>/). ORC-36 ACs 3 and 4 cover both ORC-35 failure points (compute-prediction-accuracy.py + _resolve_feature_metrics).
<!-- SECTION:NOTES:END -->
