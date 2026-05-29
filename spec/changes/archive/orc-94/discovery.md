---
feature-id: orc-94
linear-ticket: ORC-94
---

# Discovery Brief: Unify state.yaml path resolution across scripts (worktree-aware)

## Feature Summary

Multiple operational scripts hardcode the main-repo `spec/changes/` layout when looking up
`state.yaml`, which silently breaks for worktree-completed workflows whose archives live
under `$WORKTREE_ROOT/spec/changes/archive/<id>/state.yaml` rather than the main repo. Two
confirmed instances exist: `qa-approve.sh` / `qa-rework.sh` (cannot locate worktree archives,
hard-fails the QA approval flow — observed closing ORC-91) and the `preview-route` inline
step (calls `estimate-cost.sh` with the worktree root instead of the per-change state dir,
silently degrades cost estimates to `estimate_unavailable`). The fix is to introduce a single
shared resolver (`scripts/resolve-state-yaml.sh` or similar) implementing the dispatcher's
own lookup order — `WORKFLOW_STATE_DIR/<id>/state.yaml` → main archive → worktree archive —
and route all three callers through it. This subsumes ORC-92.

## Personas & Actors

- **QA approver** — runs `qa-approve.sh <change-id>` after sign-off; expects the script to
  find state regardless of whether the feature ran in a worktree.
- **QA rework operator** — runs `qa-rework.sh <change-id>` on failed QA; needs the same
  lookup symmetry as approve.
- **Orchestrator dispatcher** — invokes the `preview-route` step at the start of every run,
  passing `ORCHESTRATOR_WORKFLOW_DIR` (= worktree_path); expects a real cost estimate.
- **`estimate-cost.sh`** — downstream consumer; reads `<state_dir>/state.yaml` +
  `<state_dir>/tasks.yaml` and only works if pointed at the per-change directory.

## Use Cases

### Happy Path

UC-1: QA approval for worktree feature — QA approver runs `qa-approve.sh orc-91` from the
main repo, the script finds `~/code/feature_worktrees/orc-91/spec/changes/archive/orc-91/state.yaml`,
merges the branch, marks the ticket Done, deletes the feature branch, and removes the
worktree, all in one invocation.
UC-2: QA approval for legacy non-worktree feature — QA approver runs `qa-approve.sh orc-86`,
script resolves state from `$REPO_ROOT/spec/changes/archive/orc-86/state.yaml` (existing
path), same downstream effects; no regression vs current behaviour.
UC-3: QA rework for worktree feature — QA approver runs `qa-rework.sh orc-XX` on a
worktree-completed feature; script finds the worktree archive, ticket goes back to
In Progress, branch is left intact.
UC-4: Cost preview in worktree run — dispatcher kicks off `preview-route` with
`ORCHESTRATOR_WORKFLOW_DIR=/Users/spidey/code/feature_worktrees/orc-XX`; the step resolves
the per-change state dir (`.../spec/changes/orc-XX`), invokes `estimate-cost.sh`, and emits
a real estimate (not `status: estimate_unavailable`).
UC-5: Cost preview in legacy non-worktree run — dispatcher passes a workflow dir where
state lives at `<dir>/spec/changes/<id>/state.yaml`; step resolves it correctly via the
same helper.

### Error & Edge Cases

UC-E1: Change id has no state anywhere — helper returns non-zero with a clear message
listing the three candidate paths it tried; `qa-approve.sh` / `qa-rework.sh` propagate
exit code 1 and abort before any side effect.
UC-E2: Multiple archives match the change id (e.g. dated and undated copies coexist) —
resolver picks the first hit in documented precedence order (live → main archive →
worktree archive) and prints which one it chose to stderr; no silent ambiguity.
UC-E3: `git worktree list` is unavailable or returns nothing for the change id — resolver
falls back to `~/code/feature_worktrees/<id>` per memory `feedback_worktree_path.md`.
UC-E4: Worktree removal race in `qa-approve.sh` — after `git branch -D` the worktree
directory may still exist; the script must continue and remove the worktree in the same
run so no orphan worktree / branch combo is left behind.
UC-E5: preview-route in a freshly-seeded run with no archive history — `estimate-cost.sh`
returns a cold-start `estimate: null`; the step still wraps it as a valid `route_preview`
JSON object (not `estimate_unavailable`).

## Scope

### In Scope

- New shared helper `scripts/resolve-state-yaml.sh` (or `scripts/lib/resolve-state-yaml.sh`)
  exposing one function/CLI form: given `<change-id>` (and optional repo root), echoes the
  absolute path of the resolved `state.yaml` or exits non-zero.
- Documented lookup order in the helper itself: live `WORKFLOW_STATE_DIR/<id>/state.yaml`
  → `$REPO_ROOT/spec/changes/archive/<id>/state.yaml` → worktree archive
  `<worktree_base>/<id>/spec/changes/archive/<id>/state.yaml`.
- Worktree base discovery: prefer `git worktree list` parsing; honour explicit
  `$WORKTREE_ROOT` env if set; fall back to `~/code/feature_worktrees/<id>`.
- Refactor `qa-approve.sh` and `qa-rework.sh` to source the helper, replacing their
  inline candidate loops.
- `qa-approve.sh`: ensure worktree removal happens in the same run as merge + branch
  delete, with no ordering race; reuse existing `remove-worktree` machinery if available
  (`config/steps/.../remove-worktree*`).
- Refactor the `preview-route` step's `script.sh` to call the helper (or compute
  `<workflow_dir>/spec/changes/<change_id>`) so `estimate-cost.sh` receives the real
  per-change state dir. Preserve the non-worktree fallback.
- Bats test coverage for: (a) `qa-approve.sh` by change-id alone against a fake
  worktree-completed feature; (b) `qa-rework.sh` symmetry test; (c) cleanup leaves no
  orphan branch or worktree; (d) `preview-route` in a worktree layout produces a real
  estimate; (e) `preview-route` in a non-worktree layout still works.
- Update inline comments in `qa-approve.sh`, `qa-rework.sh`, and `preview-route/script.sh`
  to document the lookup order.

### Out of Scope

- Refactoring `estimate-cost.sh` itself — it already accepts both `<state_dir>` and
  `<state.yaml>` arg shapes; the bug is in its caller.
- Generalising the helper for other call sites (`archive-completed-change`, ticket
  scripts) — those have their own resolution paths and are not failing today. Drive-by
  consolidation risks scope creep (see CLAUDE.md "Minimal Fixes").
- Changing the `WORKFLOW_STATE_DIR` convention or the worktree base directory
  (`~/code/feature_worktrees/`).
- Backfilling cost estimates for already-archived runs that showed
  `estimate_unavailable` (orc-88 etc.) — historical data is fine as-is.
- Re-running ORC-91's QA flow or revisiting that archive — already closed.

## UI Direction

N/A — no UI components. Shell-script and dispatcher-step changes only.

## Key Decisions

- D-1 (selects Approach B over A/C): standalone CLI helper at
  `scripts/resolve-state-yaml.sh <change-id>` invoked via `$(bash ...)` from all
  three callers (qa-approve, qa-rework, preview-route). Rationale: AC #9 requires
  a single source of truth, and the dispatcher already exports
  `ORCHESTRATOR_CHANGE_ID` to preview-route so a CLI signature works uniformly
  across `scripts/` callers and `config/steps/` callers without cross-boundary
  sourcing. Consequence: one fork per resolution (cheap; called ≤2× per QA run,
  1× per workflow start), and the helper is independently testable as a Bats
  unit.
- D-2 (OQ-2 resolved): worktree base precedence is `$WORKTREE_ROOT` env →
  `git worktree list` parse → `~/code/feature_worktrees/<id>` default. Explicit
  env wins so callers can override in tests.
- D-3 (OQ-3 resolved): keep the legacy dated-archive glob
  (`spec/changes/archive/*-<id>/state.yaml`) as the *last* candidate after
  worktree archive. Cheap fallback for any pre-ORC-86 archive still present;
  no new code, just one more line in the candidate loop.
- D-4 (OQ-4 resolved): preview-route keeps its non-blocking
  `estimate_unavailable` semantics. The helper only resolves paths; whether a
  resolved-but-empty state still yields `estimate_unavailable` is the
  estimator's call, unchanged by this work.
- D-5 (OQ-5 resolved): `qa-approve.sh` calls the existing
  `config/scripts/inline/remove-worktree.sh` with `STATE_YAML_PATH=$STATE_YAML`,
  not a new script. Reuses the same teardown path the workflow uses, satisfying
  AC #4/#12 without duplicating `git worktree remove` logic.

## Open Questions

- OQ-1: Helper API shape — should it be a sourceable function (`resolve_state_yaml <id>`)
  callable from bash with no fork, or a standalone executable script invoked via
  `bash scripts/resolve-state-yaml.sh <id>`? The three call sites span sourced-library
  style (`qa-*.sh` already source `lib/ticket-common.sh`) and external-script style
  (`preview-route/script.sh` is invoked by the dispatcher and would prefer a CLI). A
  hybrid that supports both (sourceable + executable when run directly) is a common
  pattern but adds surface area.
- OQ-2: Worktree base discovery precedence — when `git worktree list` and `$WORKTREE_ROOT`
  disagree (e.g. a stale env var), which wins? Proposed: `$WORKTREE_ROOT` if explicitly
  set, else `git worktree list`, else `~/code/feature_worktrees/<id>`. Confirm during
  design.
- OQ-3: Should the helper search dated archive directories (the legacy
  `archive/*-<id>/state.yaml` glob still present in `qa-approve.sh:32`)? Per memory
  `21736` the date prefix was removed, but pre-removal archives might still exist
  locally. Cheap to keep the glob as a last-resort fallback; need to decide.
- OQ-4: `preview-route` currently treats missing state as a non-blocking
  `estimate_unavailable`. After the fix, should a *resolvable-but-empty* state dir still
  return `estimate_unavailable`, or surface the underlying estimator error? Proposed:
  keep non-blocking semantics — preview is informational, never gating.
- OQ-5: Worktree removal in `qa-approve.sh` (AC #4 / #12) — currently the script only
  deletes the branch and never touches the worktree. The `remove-worktree` step exists
  in `config/steps/` but is invoked by the workflow, not `qa-approve.sh`. Should we
  call that step's script directly, inline `git worktree remove`, or shell out to a new
  `scripts/remove-worktree.sh`? Confirm during design.

<!-- Format contract: config/steps/explore/prompt.md § Discovery Brief Format Contract -->
