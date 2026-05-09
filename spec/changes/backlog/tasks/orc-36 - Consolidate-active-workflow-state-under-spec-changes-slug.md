---
id: ORC-36
title: Consolidate active workflow state under spec/changes/<slug>/
status: Done
assignee: []
created_date: '2026-05-03 12:33'
updated_date: '2026-05-08 15:24'
labels:
  - bug
  - refactor
  - score-8.0
  - follow-up-orc-34
  - follow-up-orc-35
  - supersedes-orc-35
dependencies: []
priority: high
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Active workflow state currently lives in two parallel locations:
- `.state/<slug>/` — state.yaml, plan.yaml (machine-managed)
- `spec/changes/<slug>/` — spec.md, design.md, diagnose.md, tasks.md (artifacts)

This split is the structural root cause behind a family of recurring bugs:
- ORC-34 (shipped): seed-state.sh wrote canonical state.yaml in .state/ — missed started_at because the script and the metrics consumer drifted apart across the two locations.
- ORC-35 (open): metrics resolver expects tasks.md in .state/<slug>/ but tasks.md lives in spec/changes/<slug>/. Required a symlink workaround during the ORC-34 autopilot run.
- archive-completed-change.sh only copies from .state/<slug>/, silently dropping spec.md/design.md/diagnose.md. Worked around manually during ORC-34.
- compute-prediction-accuracy.py reads tasks.md from .state/<slug>/ and computed predicted_tasks=0/actual_tasks=0 during ORC-34 because the file isn't there.

Fix: collapse to one location. Active state lives under `spec/changes/<slug>/` alongside the artifacts. `.state/` directory disappears.

## Approach
- seed-state.sh writes state.yaml + plan.yaml into spec/changes/<slug>/ instead of .state/<slug>/.
- Add `spec/changes/*/state.yaml` and `spec/changes/*/plan.yaml` to .gitignore (active state stays untracked; archive copies are committed).
- archive-completed-change.sh renames spec/changes/<slug>/ → spec/changes/archive/<date>-<slug>/ (single mv instead of cross-directory copy + cleanup).
- Update metrics resolver (config/scripts/orchestrator_next/record.py _resolve_feature_metrics) to read tasks.md from spec/changes/<slug>/.
- Update compute-prediction-accuracy.py and compute-swe-metrics.sh to read tasks.md from the same location.
- Update orchestrate skill, autopilot skill, and any step contracts that reference WORKFLOW_STATE_DIR or .state/ paths.
- Migrate any in-flight workflows: one-shot script that moves .state/<slug>/* into spec/changes/<slug>/ and updates state.yaml fields.
- CLAUDE.md repo-wiring "Paths" table needs updating.

## Why Now
Three bugs from this split surfaced in the last two autopilot runs (ORC-27 → ORC-34 → projected ORC-35). Each fix-the-symptom ticket leaves the structural seam in place. Closing the seam prevents the next sibling bug.

## Score
- User value: 8/10 (kills a recurring bug class, simplifies mental model)
- Strategic fit: 9/10 (single source of truth for workflow data)
- Effort: M (touches seed-state.sh, archive script, record.py, two metrics scripts, orchestrate skill, ~6 step contracts, CLAUDE.md, plus a one-shot migration for in-flight workflows)
- Score: 8.0
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 seed-state.sh writes state.yaml and plan.yaml into spec/changes/<slug>/ (no more .state/<slug>/ creation)
- [ ] #2 archive-completed-change.sh archives by renaming spec/changes/<slug>/ → spec/changes/archive/<date>-<slug>/ in a single operation
- [ ] #3 Metrics resolver _resolve_feature_metrics reads tasks.md from spec/changes/<slug>/; ORC-35 reproduction case passes without the symlink workaround
- [ ] #4 compute-prediction-accuracy.py reads tasks.md from spec/changes/<slug>/ and reports nonzero predicted/actual counts on a real run
- [ ] #5 compute-swe-metrics.sh reads from spec/changes/<slug>/
- [ ] #6 .gitignore excludes spec/changes/*/state.yaml and spec/changes/*/plan.yaml so active state stays untracked
- [ ] #7 Orchestrate, autopilot skills and all step contracts updated to reference spec/changes/<slug>/ instead of .state/<slug>/
- [ ] #8 CLAUDE.md repo-wiring Paths table reflects the new layout
- [ ] #9 End-to-end /autopilot run completes with no manual symlinking and no missing-artifact warnings
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
## Supersedes ORC-35

ORC-35 documented two specific failure points that this consolidation closes:

1. `scripts/inline/compute-prediction-accuracy.py` — silently reports 0 predicted/actual tasks, yielding bogus 100% accuracy and rework_rate=0.
2. `config/scripts/orchestrator_next/_resolve_feature_metrics` in `record.py` — hard-fails `tasks.md not found at <state_dir>/tasks.md` on bugfix mark-change-completed. Worked around in ORC-27 and ORC-34 runs by symlinking.

ORC-35 considered three fix options: (A) dual-path lookup, (B) symlink at architect time, (C) move artifacts under state dir. ORC-36 is option D — invert C: move state under spec/changes/<slug>/. Both ORC-35's fail points are AC-3 and AC-4 of this ticket.

Note vs ORC-32: ORC-32 targets `read-sub-state-metrics.sh` path drift (autopilot-telemetry); same drift family, different scripts. ORC-32 stays open.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Consolidated active workflow state under spec/changes/<slug>/. Eliminated the .state/ split that caused 4 recurring bugs (ORC-34, ORC-35, archive dropping artifacts, prediction accuracy reporting 0/0). All 7 tasks complete and archived at spec/changes/archive/2026-05-03-orc-36/.
<!-- SECTION:FINAL_SUMMARY:END -->
