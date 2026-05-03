---
id: ORC-15
title: Worktree Cleanup on Workflow Failure
status: To Do
assignee: []
created_date: '2026-05-03 10:55'
updated_date: '2026-05-03 11:00'
labels:
  - slug-worktree-cleanup-on-failure
  - feature
  - score-6.8
  - recurrence-1
dependencies: []
priority: low
ordinal: 14000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
<!-- migrated from spec/changes/backlog.md slug: worktree-cleanup-on-failure -->

**Original score:** 6.8 | **Recurrence:** 1

## Idea

When a workflow fails mid-execution (agent crash, user abort, max retries exceeded), the git worktree at `~/code/feature_worktrees/$SLUG` and the branch `feature/$SLUG` are left behind. The `remove-worktree.yaml` step only runs in the `complete` phase, so any workflow that stops before completion leaks worktrees. Over time, `git worktree list` accumulates stale entries, and `~/code/feature_worktrees/` fills with abandoned directories. Add: (1) a `make clean-worktrees` target that lists stale worktrees (no matching active state.yaml) and offers to remove them, (2) a check in `create-worktree.yaml` (or `workflow-init`) that warns if more than 5 worktrees exist (suggesting cleanup), and (3) guidance in the `on_max_retries: escalate` handler to mention worktree cleanup.

## Why Now

Any failed `/orchestrate` run leaks one worktree. Spike runs that abort, bugfix runs that escalate, autopilot runs whose external loop stops mid-feature — all leave the same trail. The `doctor` command does not check for orphaned worktrees. This is the kind of slow resource leak that is invisible until disk space runs low. Today (2026-05-03) `git worktree list` shows two stale entries (`cost-summary-on-archive`, `runpod-model-switch`) that demonstrate the gap.

## Priority

- User value: 7/10
- Strategic fit: 6/10
- Technical leverage: 6/10
- Effort: small
- **Score: 6.8**

---
<!-- SECTION:DESCRIPTION:END -->
