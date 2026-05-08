---
feature-id: HL-303
linear-ticket: HL-303
schema: bugfix
---

# Specification: Workflow artifacts should live in the worktree, not repo_root

## Symptom

During `/autopilot ORC-37`, the dispatcher advanced past `execute-next-task` on
the first iteration — all 29 tasks were skipped. The user had to manually
symlink `worktree/spec/changes/orc-37/tasks.md → repo_root/spec/changes/orc-37/tasks.md`
mid-run and force a `run-phase-review` failure to recover.

The dispatcher's `repeat_until: all_tasks_completed` predicate returned `True`
immediately because `_check_all_tasks_completed` could not find `tasks.md` at
the path it probed.

## Root Cause

Writers and the reader resolve `spec/changes/<id>/` to two different
filesystem roots when `flags.worktree=true`.

- **Writers** default `WORKFLOW_STATE_DIR=$REPO_ROOT/spec/changes` and write
  every artifact (spec/design/tasks/diagnose, ux files) under repo_root.
  Cited:
    - `skills/orchestrate/scripts/seed-state.sh:49`
    - `config/steps/design-and-draft-artifacts.yaml:72`
    - `config/steps/diagnose.yaml` (via `$WORKFLOW_STATE_DIR/$CHANGE_ID/`)
    - `config/steps/ux-design.yaml:30-31`
    - `skills/orchestrate/SKILL.md:20`, `skills/learn/SKILL.md:16`,
      `skills/telemetry/SKILL.md:29`
- **Reader** `_resolve_tasks_md` at `config/scripts/orchestrator_next/record.py:906`
  prefers `worktree_path` over `repo_root` when constructing candidate paths.
- When the candidate worktree path does not exist, `_check_all_tasks_completed`
  catches `FileNotFoundError` at `record.py:931` and returns `True`
  (fail-open), triggering premature advancement.
- Sibling resolver `_resolve_feature_metrics_tasks_path` at
  `record.py:807-819` is repo_root-only — diverged from `_resolve_tasks_md`
  (flagged in ORC-36 archive).

## What Changes

Move the **canonical artifact location** for tracked workflow files
(`spec.md`, `design.md`, `tasks.md`, `diagnose.md`, `ux-prototype.html`,
`ux-artifacts.yaml`) into the worktree at
`$WORKTREE_ROOT/spec/changes/<change_id>/` whenever `flags.worktree=true`.

`state.yaml` and `plan.yaml` remain at `$REPO_ROOT/spec/changes/<change_id>/`
because they are gitignored (`.gitignore:20-21`) and must not be carried on
the feature branch. They are ephemeral run records, not feature deliverables.

The reader is corrected to read tracked artifacts from the worktree (matching
where writers put them), and `_check_all_tasks_completed` is tightened so that
"file expected but missing" no longer fail-opens — it fail-closes (returns
`False`, keep iterating tasks). Only "no candidate path constructible at all"
remains fail-open.

Both resolver functions converge on a single helper.

## Requirements

### Functional

1. **FR-1**: When `flags.worktree=true`, all tracked workflow artifacts
   (`spec.md`, `design.md`, `tasks.md`, `diagnose.md`,
   `ux-prototype.html`, `ux-artifacts.yaml`) are written to and read from
   `$WORKTREE_ROOT/spec/changes/<change_id>/`.
2. **FR-2**: When `flags.worktree=false`, all tracked workflow artifacts are
   written to and read from `$REPO_ROOT/spec/changes/<change_id>/` (unchanged
   behavior for the no-worktree path).
3. **FR-3**: `state.yaml` and `plan.yaml` continue to live at
   `$REPO_ROOT/spec/changes/<change_id>/` regardless of `flags.worktree`.
4. **FR-4**: `_resolve_tasks_md` returns the worktree path when
   `flags.worktree=true` and the worktree directory exists; returns the
   repo_root path otherwise.
5. **FR-5**: `_check_all_tasks_completed` returns `False` (fail-closed) when
   a candidate path was constructible but the file does not exist on disk.
   Only "no candidate constructible" returns `True`.
6. **FR-6**: `_resolve_feature_metrics_tasks_path` and `_resolve_tasks_md`
   share a single resolver implementation (no behavioral divergence).
7. **FR-7**: `archive-completed-change.sh` sources tracked artifacts from
   the worktree and `state.yaml`/`plan.yaml` from repo_root, merging them
   into the single archive directory under
   `spec/changes/archive/YYYY-MM-DD-<id>/`.
8. **FR-8**: A new env var `ORCHESTRATOR_WORKTREE_ROOT` (or reuse of
   `ORCHESTRATOR_WORKFLOW_DIR`) is set by the dispatcher so writers can target
   the worktree without re-deriving it.

### Non-Functional

1. **NFR-1**: No new fallback logic that masks misconfiguration. If the
   worktree directory is missing while `flags.worktree=true`, fail loud.
2. **NFR-2**: Backward-compat: zero impact on archived runs (they remain
   untouched in `spec/changes/archive/`).
3. **NFR-3**: Backward-compat for in-flight runs: only `hl-303` is currently
   active (verified via `ls spec/changes/*/state.yaml`); migration handled
   in-line by writing this run's artifacts to the worktree.

## In Scope

- `skills/orchestrate/scripts/seed-state.sh` — keep state.yaml/plan.yaml at repo_root
  but emit a separate `WORKTREE_ARTIFACT_DIR` value for downstream writers.
- `config/steps/{design-and-draft-artifacts,diagnose,ux-design,workflow-init}.yaml`
  — write tracked artifacts to the worktree path.
- `config/steps/CONVENTIONS.md` — document the split (state at repo_root,
  tracked artifacts at worktree).
- `config/scripts/orchestrator_next/record.py:807-932` — unify resolvers
  and tighten fail-open.
- `config/scripts/orchestrator_next/parser.py:180` — keep `workflow_dir =
  worktree_path` (already correct), confirm semantics.
- `scripts/inline/archive-completed-change.sh` — pull tracked artifacts from
  the worktree before mv-ing into archive.
- `scripts/inline/compute-swe-metrics.sh` — point at the unified resolver.
- `.gitignore` — confirm `spec/changes/*/state.yaml` and `plan.yaml` patterns
  cover the repo_root location (no change needed unless verified missing).
- Regression test: `config/scripts/orchestrator_next/tests/test_resolve_tasks_md.py`
  + new test reproducing the reader/writer mismatch.
- `CLAUDE.md` (repo) — update Repo Wiring § Paths table to reflect split.
- `skills/orchestrate/SKILL.md`, `skills/learn/SKILL.md`,
  `skills/telemetry/SKILL.md` — update header path resolution to set
  both vars or use the worktree-aware var.

## Out of Scope

- Changes to `config/scripts` or `config/templates` symlink layout (HL-303
  ticket explicitly excludes).
- Refactor of any unrelated worktree behavior (worktree creation, cleanup).
- Migration of archived `spec/changes/archive/*` runs (untouched).
- Renaming `WORKFLOW_STATE_DIR` env var globally (kept for compatibility;
  semantics clarified in CONVENTIONS.md).

## Acceptance Criteria

- **AC-1**: Regression test `test_check_all_tasks_completed_fail_closed_when_path_missing`
  exists in `config/scripts/orchestrator_next/tests/`. Verify:
  `cd config/scripts/orchestrator_next && python -m pytest tests/test_resolve_tasks_md.py -k fail_closed -v`
  → asserts `_check_all_tasks_completed` returns `False` when the candidate
  path is constructible but the file does not exist. Test FAILS on `main`
  (current fail-open) and PASSES after T-2.
- **AC-2**: `_resolve_tasks_md` returns `$WORKTREE_ROOT/spec/changes/<id>/tasks.md`
  when `state_raw` contains `worktree_path` AND that directory exists. Verify:
  pytest case `test_resolve_uses_worktree_when_present` (already exists, semantics
  preserved) passes; new case `test_resolve_falls_back_to_repo_root_when_worktree_missing`
  passes.
- **AC-3**: `_resolve_feature_metrics_tasks_path` is replaced by a call into the same
  unified resolver as `_resolve_tasks_md`. Verify:
  `grep -n 'def _resolve_feature_metrics_tasks_path\|def _resolve_tasks_md' config/scripts/orchestrator_next/record.py`
  → only one resolver definition remains; the other is a thin wrapper or removed.
- **AC-4**: `_check_all_tasks_completed` returns `False` (not `True`) when
  `_resolve_tasks_md` returns a non-None path that does not exist on disk.
  Verify: `python -m pytest tests/test_resolve_tasks_md.py -v` shows
  `test_check_all_tasks_completed_fail_closed_when_path_missing` PASS.
- **AC-5**: `design-and-draft-artifacts.yaml` writes to
  `$WORKTREE_ROOT/spec/changes/$CHANGE_ID/` when `flags.worktree=true`.
  Verify: `grep -n 'WORKTREE\|worktree_path' config/steps/design-and-draft-artifacts.yaml`
  shows the worktree-aware target; running this current `hl-303` workflow
  produces `spec.md`, `design.md`, `tasks.md` under
  `/Users/spidey/code/feature_worktrees/hl-303/spec/changes/hl-303/`.
- **AC-6**: `diagnose.yaml` writes `diagnose.md` to the worktree when
  `flags.worktree=true`. Verify: `grep -n 'WORKTREE\|worktree' config/steps/diagnose.yaml`.
- **AC-7**: `ux-design.yaml` writes `ux-prototype.html` and `ux-artifacts.yaml`
  to the worktree when `flags.worktree=true`. Verify: `grep -n 'WORKTREE\|worktree' config/steps/ux-design.yaml`.
- **AC-8**: `workflow-init.yaml` verify-block accepts state.yaml at repo_root
  AND requires the worktree artifact directory to exist when
  `flags.worktree=true`. Verify: `grep -A2 'verify:' config/steps/workflow-init.yaml`
  shows `state.yaml at $REPO_ROOT/spec/changes/...` and a new check for
  `$WORKTREE_ROOT/spec/changes/<slug>/` directory creation.
- **AC-9**: `archive-completed-change.sh` collects tracked artifacts from
  `$WORKTREE_ROOT/spec/changes/$CHANGE_ID/` and `state.yaml`/`plan.yaml`
  from `$REPO_ROOT/spec/changes/$CHANGE_ID/`, merging both into the archive
  destination. Verify: shell-test
  `config/tests/test-archive-merges-worktree-artifacts.sh` passes;
  `cat scripts/inline/archive-completed-change.sh | grep -E 'WORKTREE|worktree'`
  shows the worktree source.
- **AC-10**: This very `hl-303` run produces a green archive end-to-end —
  artifacts at the worktree, state at repo_root, archive at
  `spec/changes/archive/YYYY-MM-DD-hl-303/` containing all of
  `spec.md`, `design.md`, `tasks.md`, `diagnose.md`, `state.yaml`,
  `plan.yaml`. Verify: post-merge `ls spec/changes/archive/2026-05-04-hl-303/`
  lists all six files.

## Test Strategy

### Test File Paths

- `config/scripts/orchestrator_next/tests/test_resolve_tasks_md.py` (extend)
  — unit tests for resolver and fail-closed semantics.
- `config/tests/test-archive-merges-worktree-artifacts.sh` (new) — shell
  test asserting archive sources from both roots.
- `spec/changes/hl-303/repro.sh` (existing) — must transition from "Bug
  confirmed" to "OK: predicate correctly detected unchecked tasks".

### Coverage Targets

- New/modified resolver code: 100% branch coverage on the worktree-vs-repo_root
  resolution paths.
- No relaxation of existing test coverage.

### Key Test Scenarios

- Fail-open removed: missing tasks.md when path constructible → returns False.
- Fail-open preserved: no path constructible (no change_id, no roots) → returns True.
- Worktree present + writers there: resolver finds tasks.md at worktree.
- `flags.worktree=false`: resolver returns repo_root path; existing tests pass.
- Archive correctly merges from both roots.

## Impact

Breaking changes for in-flight runs: only `hl-303` is active (verified). This
run's artifacts are written to the worktree from `design-and-draft-artifacts`
forward — no migration script needed. Archived runs are untouched.

## Decisions

- **state.yaml stays at repo_root**: It's gitignored. Carrying it on the
  feature branch would conflate ephemeral run records with tracked deliverables.
- **Tracked artifacts move to worktree**: They are feature-branch content
  (spec/design/tasks/diagnose belong with the code change).
- **No fallback layer**: Only one in-flight run exists. Fallback would be
  permanent tech debt for a single migration moment.
- **Resolver unification is required, not optional**: Once writers move,
  `_resolve_feature_metrics_tasks_path` (repo_root-only) is wrong by
  construction. Unifying is correctness, not cleanup.
- **Tighten fail-open**: The original bug-amplifier — root-cause fix per
  CLAUDE.md "Root-Cause Debugging" rule.

<!-- Format contract: contracts/artifact-formats.md § Specification Format Contract -->
