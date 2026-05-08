# Design: HL-303 — Workflow artifacts to the worktree

## Context

`/autopilot` skipped all 29 tasks in `ORC-37` because the writer/reader
disagreement on `spec/changes/<id>/` location combined with a fail-open
predicate to make `_check_all_tasks_completed` return `True` immediately.

Two structural seams cause this:

1. Writers default `WORKFLOW_STATE_DIR=$REPO_ROOT/spec/changes`
   (`seed-state.sh:49`, `workflow-init.yaml:50`, every step contract using
   `$WORKFLOW_STATE_DIR/$CHANGE_ID/`).
2. Reader `_resolve_tasks_md` (`record.py:906`) prefers `worktree_path`
   then falls back to `repo_root`.

When the two roots diverge (i.e., `flags.worktree=true`), the reader probes
a directory the writers never populated. The fail-open at
`record.py:931` then masks the missing file as "all done."

A second seam: `_resolve_feature_metrics_tasks_path` (`record.py:807`) is
repo_root-only — already diverged from `_resolve_tasks_md` since ORC-36.

## Goals / Non-Goals

### Goals

- Eliminate the writer/reader path mismatch when `flags.worktree=true`.
- Move tracked artifacts (`spec/design/tasks/diagnose/ux-*`) onto the
  feature branch where they belong.
- Tighten the fail-open in `_check_all_tasks_completed` so a missing
  expected file does not silently advance the workflow.
- Unify the two diverged resolvers so future drift cannot recur.

### Non-Goals

- Renaming `WORKFLOW_STATE_DIR` globally (semantics clarified, name kept).
- Migrating archived runs (out of scope; archive layout unchanged).
- Touching unrelated worktree lifecycle code.

## Approaches Considered

### Approach A — Writers move to worktree (split layout)

`state.yaml` + `plan.yaml` stay at `$REPO_ROOT/spec/changes/<id>/`
(gitignored). Tracked artifacts (`spec/design/tasks/diagnose`, ux files)
write to `$WORKTREE_ROOT/spec/changes/<id>/`. Reader resolves tracked
artifacts from the worktree.

- **Complexity**: M
- **Pros**:
  - Feature branch carries the spec/design/tasks/diagnose changes natively;
    archive becomes a `git mv` after merge instead of a copy across roots.
  - Matches `.gitignore:20-21` semantics (only state/plan are ignored —
    everything else was meant to be tracked).
  - Reader's existing worktree-first resolver becomes correct (after the
    fail-open fix).
- **Cons**:
  - Two roots in play (state at repo_root, artifacts at worktree). Mental
    overhead until documented.
  - Archive script must source from both roots.
  - 8–10 writer call-sites need plumbing to receive a `WORKTREE_ROOT`
    value distinct from `WORKFLOW_STATE_DIR`.
- **AC coverage**: Covers all 10 ACs. Required: T-2 fail-open fix, T-3
  resolver unification.

### Approach B — Reader moves back to repo_root

Drop `worktree_path` preference in `_resolve_tasks_md`. Writers stay where
they are (everything at repo_root). Tracked artifacts never reach the
feature branch unless explicitly committed by hand.

- **Complexity**: S
- **Pros**:
  - Smallest patch (one resolver change + remove the worktree candidate).
  - One-root model — simpler mental layout.
- **Cons**:
  - Defeats HL-303's stated intent (artifacts should live in the worktree).
  - Tracked artifacts remain off the feature branch — a structural
    inconsistency we'd inherit.
  - `flags.worktree=true` runs gain no artifact-on-branch benefit.
  - Doesn't fix the fail-open bug — still latent.
- **AC coverage**: Fails AC-5..AC-10 as written (those require artifacts
  in the worktree). Would need AC rewrite, breaking ticket alignment.

### Approach C — Hybrid with reader fallback

Writers move to worktree (like A); reader prefers worktree but falls back
to repo_root for "transitional" runs.

- **Complexity**: L
- **Pros**:
  - Tolerates in-flight runs from before the fix without breakage.
- **Cons**:
  - Only one in-flight run exists (`hl-303`, this very run, verified via
    `ls /Users/spidey/code/orchestrator/spec/changes/*/state.yaml`). The
    fallback exists for a single run that we control.
  - Adds permanent ambiguity to the resolver — when does the fallback
    expire? Without a removal trigger, it's permanent tech debt.
  - More logic in the resolver for zero benefit at steady state.
- **AC coverage**: Same as A, plus extra fallback paths.

### Selection (auto-heuristic)

Map XS=1..XL=5 → A=3, B=2, C=4. Lowest is **B**, but B fails AC coverage
(AC-5..AC-10 require tracked artifacts in worktree per HL-303 ticket).
Reject B for criterion-coverage failure. Tie-break between A and C is
moot — A is strictly lower complexity and there is no in-flight run
needing the C-fallback (verified empirically). **Select Approach A.**

Selection rationale:
1. Honors HL-303 ticket intent (artifacts in worktree).
2. Lowest complexity that satisfies all ACs.
3. No live runs need a fallback (`spec/changes/*/state.yaml` lists only
   `hl-303`, which we are migrating in-line as part of this change).
4. Pairs naturally with the required fail-open fix and resolver
   unification — both of which any layout-fixing approach demands.

## High-Level Design

### Architecture Overview

```
Repo root  $REPO_ROOT/spec/changes/<id>/
   ├── state.yaml         (ephemeral, gitignored)
   └── plan.yaml          (ephemeral, gitignored)

Worktree   $WORKTREE_ROOT/spec/changes/<id>/
   ├── spec.md            (tracked, on feature branch)
   ├── design.md          (tracked)
   ├── tasks.md           (tracked)
   ├── diagnose.md        (tracked, bugfix only)
   ├── ux-prototype.html  (tracked, when ux_design=true)
   └── ux-artifacts.yaml  (tracked, when ux_design=true)
```

When `flags.worktree=false`, both classes collapse onto repo_root
(unchanged behavior).

### Key Abstractions

- **`WORKFLOW_STATE_DIR`** — semantically narrowed to "directory holding
  state.yaml/plan.yaml". Always `$REPO_ROOT/spec/changes`. Documented in
  CONVENTIONS.md.
- **`WORKTREE_ARTIFACT_DIR`** (new) — directory holding tracked artifacts.
  Equals `$WORKTREE_ROOT/spec/changes` when `flags.worktree=true`,
  otherwise equals `$WORKFLOW_STATE_DIR`. Exported by step-dispatch
  alongside `ORCHESTRATOR_WORKFLOW_DIR`.
- **`_resolve_workflow_artifact_path(state_raw, filename)`** (new, in
  record.py) — single helper used by both `_resolve_tasks_md` and
  `_resolve_feature_metrics_tasks_path`. Returns the worktree path when
  worktree exists; repo_root path otherwise.

## Low-Level Design

### Components — change list mapped to writers/readers

| Component | File | Change |
|---|---|---|
| Writer (state seed) | `skills/orchestrate/scripts/seed-state.sh:49` | Keep `WORKFLOW_STATE_DIR=$REPO_ROOT/spec/changes`. Add a comment clarifying state lives here permanently. |
| Writer (artifacts) | `config/steps/design-and-draft-artifacts.yaml:72` | Change instruction target from `$WORKFLOW_STATE_DIR/$CHANGE_ID/` to `$WORKTREE_ARTIFACT_DIR/$CHANGE_ID/`. |
| Writer (diagnose) | `config/steps/diagnose.yaml` | Same target swap to `$WORKTREE_ARTIFACT_DIR`. |
| Writer (ux) | `config/steps/ux-design.yaml:30-31` | Same target swap. |
| Writer (init) | `config/steps/workflow-init.yaml:50-51` | Verify state.yaml at `$WORKFLOW_STATE_DIR`; verify worktree artifact dir was created (`mkdir -p $WORKTREE_ROOT/spec/changes/<slug>` after `git worktree add`). |
| Writer (bootstrap) | `config/steps/write-bootstrap-state.yaml:46` | Unchanged target (state.yaml — repo_root). |
| Reader | `config/scripts/orchestrator_next/record.py:807-819` | Replace `_resolve_feature_metrics_tasks_path` body with a call into the unified resolver. |
| Reader | `config/scripts/orchestrator_next/record.py:889-917` | `_resolve_tasks_md` calls the unified resolver. Logic: if `flags.worktree=true` AND worktree dir exists → worktree path; else repo_root path. |
| Reader (fail-open) | `config/scripts/orchestrator_next/record.py:920-932` | `_check_all_tasks_completed`: if `_resolve_tasks_md` returns a path and that path does not exist, return `False` (fail-closed). Only `path is None` returns `True`. |
| Dispatcher (second seam) | `config/scripts/orchestrator_next/dispatch.py:314-319` | Dispatch's history-walk treats any step with a `completed` history entry as advanced. For steps whose contract declares `repeat_until`, this is wrong — the step must be re-emitted while the predicate returns False. Fix: in the history-walk loop, when a step has a completed entry AND its contract has `repeat_until`, evaluate the predicate; if False, select that step as `next_step_id` (re-emit). Reuses `_REPEAT_PREDICATES` from `record.py` (lift to a shared module or import). |
| Env propagation | `config/scripts/orchestrator_next/parser.py:180` | Keep `workflow_dir = worktree_path`. Add new line propagating worktree-or-repo-root as `WORKTREE_ARTIFACT_DIR` env. |
| Env propagation | `config/steps/contracts/step-dispatch.md:160` | Add `ORCHESTRATOR_WORKTREE_ARTIFACT_DIR` to the env table. |
| Archive | `scripts/inline/archive-completed-change.sh:24` | Source split: `cp -r $WORKTREE_ROOT/spec/changes/$CHANGE_ID/* $DST/` then `cp $REPO_ROOT/spec/changes/$CHANGE_ID/{state,plan}.yaml $DST/`. Then `rm -rf` both source dirs. |
| Metrics | `scripts/inline/compute-swe-metrics.sh` | Reads via `_resolve_feature_metrics` which now points at the unified resolver — no script-level change required. |
| Skill headers | `skills/orchestrate/SKILL.md:20`, `skills/learn/SKILL.md:16-32`, `skills/telemetry/SKILL.md:29` | Document the split; `WORKFLOW_STATE_DIR` for state, `WORKTREE_ARTIFACT_DIR` for artifacts. |
| Conventions | `config/steps/CONVENTIONS.md:297` | Update the rule to point artifacts at `$WORKTREE_ARTIFACT_DIR/$CHANGE_ID/`, state at `$WORKFLOW_STATE_DIR/$CHANGE_ID/`. |
| Repo CLAUDE.md | `CLAUDE.md` Paths table | Add row for "Active feature artifacts → worktree". |

### Data Flow

1. `seed-state.sh` writes `state.yaml` at `$REPO_ROOT/spec/changes/<slug>/`.
2. `workflow-init` runs `git worktree add ...` → creates worktree → runs
   `mkdir -p $WORKTREE_ROOT/spec/changes/<slug>/` to ensure artifact dir exists.
3. `parser.py:load_state` reads state.yaml from repo_root, exports
   `ORCHESTRATOR_WORKFLOW_DIR = worktree_path` and
   `ORCHESTRATOR_WORKTREE_ARTIFACT_DIR = (worktree_path or repo_root)`.
4. Step contracts using `$WORKTREE_ARTIFACT_DIR/$CHANGE_ID/` as the
   tracked-artifact target write into the worktree.
5. `_check_all_tasks_completed` resolves to the worktree path, finds
   `tasks.md`, parses checkboxes correctly.
6. `archive-completed-change.sh` merges from both roots into the archive
   destination, then `rm -rf` both source directories.

### State Management

- `state.yaml`: lives at `$REPO_ROOT/spec/changes/<id>/state.yaml`
  throughout the run. Append-only via `orchestrator done`.
- `plan.yaml`: same root.
- Tracked artifacts: live at `$WORKTREE_ROOT/spec/changes/<id>/...`. Edited
  by agents, committed to the feature branch.

### Error Handling

- `flags.worktree=true` but worktree directory missing →
  `_resolve_workflow_artifact_path` falls through to repo_root and emits
  a stderr warning. (Belt-and-suspenders; should not occur.)
- Tracked artifact missing while expected → `_check_all_tasks_completed`
  returns `False` (loop iterates, exposes the missing artifact at next step).
- Archive source missing in either root → archive script logs a warning
  and skips that source class but does not fail the archive (existing
  policy).

### Two fail-open seams, not one

The original analysis identified `record.py:_check_all_tasks_completed` as the
fail-open. Investigation during T-1 revealed a **second, structurally identical
seam** in `dispatch.py:dispatch()` (lines 314-319). When the orchestrator CLI
runs `orchestrator next`, it goes through `dispatch.dispatch()` — NOT through
`record._compute_next_step`. Dispatch independently walks `step_history` to
find "the first step in `active` without a completed entry," and ignores both:

1. `state.next_step` (which `record._compute_next_step` correctly sets to the
   repeat_until step when the predicate is False).
2. `contract.repeat_until` itself.

Net effect without fixing dispatch: `record.py`'s correct decision to re-emit
`execute-next-task` is silently overridden when the next CLI tick goes through
dispatch, because dispatch sees the completed entry and advances to
`run-phase-review`. This recreates the original ORC-37 manual-recovery
scenario inside hl-303's own run.

**Resolution:** Both fail-opens must be closed. `record.py` honors
`repeat_until` correctly already (line 961-970); `dispatch.py` must be
taught the same rule by consulting `_REPEAT_PREDICATES` for any completed
step in `active` before treating it as advanced. The shared predicate map
should be moved to a module both can import (or imported from `record` into
`dispatch`) — no duplication.

## Resolved Open Questions

- **Q1 — Which side moves?** Writers move to the worktree for tracked
  artifacts. State/plan stay at repo_root. Justification: HL-303 ticket
  intent ("artifacts should live in the worktree"), `.gitignore` already
  treats state.yaml/plan.yaml as ephemeral, and feature-branch content
  belongs on the feature branch.
- **Q2 — Worktree-removal mid-run safety?** Archive runs before
  `remove-worktree` (per `state.yaml workflow_plan` ordering for hl-303
  and the standard schema). Tracked artifacts move into the archive at
  step 11 (`archive-completed-change`); worktree removal at step 12
  (`remove-worktree`) only deletes the now-empty worktree dir. If a crash
  occurs before archive, artifacts persist in the worktree (recoverable
  via `git worktree list` + manual recovery). State.yaml at repo_root
  remains untouched and recovery state is intact.
- **Q3 — Backward compatibility for in-flight runs?** Verified via
  `ls /Users/spidey/code/orchestrator/spec/changes/*/state.yaml` — only
  `hl-303` is active. This very run migrates in-line: spec/design/tasks
  written to the worktree from the design phase forward. State.yaml
  remains where seed-state placed it (repo_root). No transitional
  fallback layer needed; no migration script needed.
- **Q4 — Resolver unification?** Required, not optional. Once writers
  move to worktree, the repo_root-only
  `_resolve_feature_metrics_tasks_path` is wrong by construction —
  `compute-swe-metrics` would read tasks.md from an empty repo_root path.
  Unify into `_resolve_workflow_artifact_path(state_raw, filename)`;
  retain the two named entry points as thin wrappers for caller clarity.

## Migration Plan

Single-run migration (this very `hl-303` run is the test case):

1. Architect (this spawn) writes `spec.md`, `design.md`, `tasks.md` to
   `$WORKTREE_ROOT/spec/changes/hl-303/` directly (already done as part of
   this artifact write).
2. Subsequent steps (capture-test-baseline, execute-next-task,
   run-phase-review) read tasks.md from the worktree. Today's reader
   already prefers worktree first — this resolves correctly *before* T-2
   lands in code, because the bug is fail-open + writers, not the
   candidate order.
3. T-1 lands the regression test (failing on main).
4. T-2 lands the fail-open fix and the writer redirects (so future runs
   work).
5. Archive at end of run merges both roots correctly.

No migration script. No data move. The only "migration" artifact is this
file you are reading.

## Constraints

- Cannot rename `WORKFLOW_STATE_DIR` globally (too many call-sites; not
  worth the churn). Documented narrowing instead.
- Cannot move state.yaml into the worktree (gitignore conflict + ephemeral
  semantics).
- Must not introduce a permanent fallback layer (NFR-1).

## Trade-offs

- Two roots in play during a run is mildly more cognitive load than a
  single root. Accepted because:
  - The two roots have **different lifetimes and different gitignore
    treatment**. Forcing them onto one root would either commit ephemeral
    state (worktree) or strand tracked artifacts off-branch (repo_root).
- Archive script becomes slightly more complex (two sources). Accepted
  because the alternative — forcing artifacts to one root — has worse
  consequences upstream.

## Decisions

- `state.yaml` stays at repo_root → aligned with `.gitignore` → state
  remains ephemeral and per-machine.
- Tracked artifacts move to worktree → carried by feature branch → archive
  becomes natural git history of the merged branch.
- Fail-open tightened to fail-closed for "expected file missing" → root-cause
  fix per CLAUDE.md "Root-Cause Debugging" rule → prevents future drift
  from silently skipping tasks.
- Resolvers unified → one source of truth for "where does tasks.md live" →
  prevents the recurrence of the ORC-36 divergence flagged a cycle ago.

## Open Questions

- None remaining; Q1–Q4 resolved above.

<!-- Format contract: contracts/artifact-formats.md § Design Format Contract -->
