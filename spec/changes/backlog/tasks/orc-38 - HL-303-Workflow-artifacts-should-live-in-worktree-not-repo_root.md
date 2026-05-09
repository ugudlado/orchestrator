---
id: ORC-38
title: 'HL-303: Workflow artifacts should live in worktree, not repo_root'
status: Done
assignee: []
created_date: '2026-05-08 12:04'
updated_date: '2026-05-08 15:27'
labels:
  - bug
  - orchestrator
dependencies: []
references:
  - >-
    https://linear.app/home-labs-experiments/issue/HL-303/workflow-artifacts-specdesigntasksstate-should-live-in-the-worktree
  - spec/changes/hl-303/state.yaml
priority: high
ordinal: 35000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Workflow artifacts under spec/changes/<id>/ (spec.md, design.md, tasks.md, diagnose.md, state.yaml, plan.yaml) currently live in $REPO_ROOT/spec/changes/<id>/. They should live in $WORKTREE_ROOT/spec/changes/<id>/ — i.e., the per-feature worktree where the branch code edits happen. This was the root cause of fail-open bugs discovered during ORC-37 autopilot run.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 State and artifact files are written to the worktree path, not repo_root
- [ ] #2 Fail-open fallback is removed — missing worktree path fails loud
- [ ] #3 Regression tests pass for worktree artifact location
<!-- AC:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
HL-303 implemented WORKTREE_ARTIFACT_DIR — all writer step contracts (diagnose, design-and-draft-artifacts, ux-design) now write to $WORKTREE_ROOT/spec/changes/<slug>/ when flags.worktree=true.

Confirmed:
- parser.py computes worktree_artifact_dir from worktree_path in state.yaml
- record.py exports ORCHESTRATOR_WORKTREE_ARTIFACT_DIR per-step
- diagnose.yaml, design-and-draft-artifacts.yaml, ux-design.yaml all use $WORKTREE_ARTIFACT_DIR
- Stale spec/changes/hl-303/ leftover from pre-fix part of run cleaned up

Archived at spec/changes/archive/2026-05-08-hl-303/
<!-- SECTION:FINAL_SUMMARY:END -->
