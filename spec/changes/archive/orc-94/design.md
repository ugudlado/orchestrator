---
feature-id: orc-94
linear-ticket: ORC-94
---

# Design: Unify state.yaml path resolution across scripts (worktree-aware)

## Context

Three callers locate `state.yaml` for an already-running or already-archived
change: `scripts/qa-approve.sh`, `scripts/qa-rework.sh`, and the
`config/steps/preview-route/script.sh` dispatch step. All three currently
hardcode `$REPO_ROOT/spec/changes/{,archive/}<id>/state.yaml`-style lookups
that miss worktree-completed runs whose archives live under
`$WORKTREE_ROOT/spec/changes/archive/<id>/state.yaml` (per ORC-80). preview-route
additionally points `estimate-cost.sh` at the worktree root instead of the
per-change state dir, silently degrading every worktree run's cost estimate to
`estimate_unavailable`. The dispatcher already exports `ORCHESTRATOR_CHANGE_ID`
and `ORCHESTRATOR_WORKFLOW_DIR` to each step (see `config/scripts/orchestrator_next/dispatch.py:128`),
so a single CLI helper taking only `<change-id>` can serve all three callers
without changes to dispatch.

## Goals / Non-Goals

### Goals

- One canonical `state.yaml` lookup order — live → main archive → worktree
  archive → legacy dated archive — implemented in one file and consumed by all
  three callers (AC #9, #10).
- `qa-approve.sh <change-id>` succeeds for worktree-completed features and
  tears down both the branch and the worktree in the same run (AC #1, #4, #12).
- `qa-rework.sh <change-id>` resolves worktree-completed features symmetrically
  (AC #6).
- `preview-route` produces a real cost estimate when run inside a worktree
  workflow (AC #7) and preserves its non-worktree behaviour (AC #8).
- Bats coverage for all four behaviour changes (AC #5, #13).

### Non-Goals

- Refactoring `estimate-cost.sh`; it already accepts a state-dir or
  state-file argument.
- Generalising the resolver to other call sites (`archive-completed-change`,
  `ticket-*`) — not failing today, see CLAUDE.md "Minimal Fixes".
- Changing `WORKFLOW_STATE_DIR` semantics or the worktree base directory
  convention (`~/code/feature_worktrees/`).
- Backfilling historical `estimate_unavailable` rows in archived runs.

## Approaches Considered

### Approach 1: Sourceable bash library at `scripts/lib/resolve-state.sh`

Define `resolve_state_yaml <id>` as a function; qa-* source it the same way
they source `lib/ticket-common.sh`; preview-route either sources it (across
the `scripts/` ↔ `config/steps/` boundary) or forks.

- Pros: no subshell cost in qa-*; matches the existing `ticket-common.sh`
  pattern.
- Cons: forces preview-route to either reach into `$REPO_ROOT/scripts/lib/`
  from a dispatch step (awkward coupling) or fork anyway, in which case the
  "one source of truth" requirement (AC #9) is preserved only by convention.

### Approach 2: Standalone executable at `scripts/resolve-state-yaml.sh`

Single CLI script. All three callers invoke `bash $REPO_ROOT/scripts/resolve-state-yaml.sh <id>`
and capture stdout. Worktree base discovery uses
`$WORKTREE_ROOT` → `git worktree list` → `~/code/feature_worktrees/<id>`.

- Pros: identical invocation pattern for all three callers; preview-route stays
  decoupled from `scripts/lib/`; helper is independently testable as a bats
  unit (its stdout contract is the whole API).
- Cons: one extra fork per resolution. Worst-case ≤2 forks per QA run and 1
  per workflow start — negligible.

### Approach 3: Hybrid (sourceable + executable when run as `$0`)

Library that defines a function and runs it if invoked directly.

- Pros: maximum flexibility per caller.
- Cons: extra surface area (`if [ "${BASH_SOURCE[0]}" = "$0" ]; then ...`),
  two contracts to test, no concrete caller benefits from sourcing today.

### Selected Approach

**Approach 2.** AC #9 explicitly requires a "single source of truth" used by
all three callers; the cleanest way to satisfy that across the
`scripts/` ↔ `config/steps/` boundary is a CLI helper. Fork cost is
non-issue. Approach 1 fails the coupling test for preview-route; Approach 3
adds complexity for no win.

## High-Level Design

### Architecture Overview

```
qa-approve.sh ──┐
qa-rework.sh ───┼──> resolve-state-yaml.sh <change-id>  ─> echoes absolute path | exit 1
preview-route ──┘                │
                                 ▼
              [live]  $WORKFLOW_STATE_DIR/<id>/state.yaml
              [main archive]  $REPO_ROOT/spec/changes/archive/<id>/state.yaml
              [worktree archive]  $WTBASE/<id>/spec/changes/archive/<id>/state.yaml
              [legacy dated]  $REPO_ROOT/spec/changes/archive/*-<id>/state.yaml
```

`qa-approve.sh` additionally invokes the existing
`config/scripts/inline/remove-worktree.sh` with `STATE_YAML_PATH` set to the
resolved path, so worktree teardown reuses the workflow's own machinery.

### Key Abstractions

- **`resolve-state-yaml.sh <change-id> [repo-root]`** — pure stdout contract.
  Echoes one absolute path and exits 0, or prints the candidates it tried to
  stderr and exits 1. No side effects.
- **Worktree base discovery** — internal helper inside the script:
  `_worktree_base_for <id>` checks `$WORKTREE_ROOT`, then parses
  `git worktree list --porcelain` for a path ending in `/<id>`, then falls
  back to `~/code/feature_worktrees/<id>`.

## Low-Level Design

### Components

| File | Responsibility |
|---|---|
| `scripts/resolve-state-yaml.sh` (new) | Echo absolute `state.yaml` path for `<change-id>` or exit 1. Documents the lookup order in a header comment. |
| `scripts/qa-approve.sh` (edit) | Replace inline candidate loop (lines 24–38) with `STATE_YAML="$(bash "$SCRIPT_DIR/resolve-state-yaml.sh" "$ARG" "$REPO_ROOT")"`. After successful merge + branch delete, call `STATE_YAML_PATH=$STATE_YAML REPO_ROOT=$REPO_ROOT bash "$INLINE_DIR/remove-worktree.sh"` and log the JSON result. |
| `scripts/qa-rework.sh` (edit) | Replace inline candidate loop (lines 22–36) with the helper invocation. No teardown changes. |
| `config/steps/preview-route/script.sh` (edit) | When `ORCHESTRATOR_CHANGE_ID` is set: resolve `STATE_YAML="$(bash "$REPO_ROOT/scripts/resolve-state-yaml.sh" "$ORCHESTRATOR_CHANGE_ID" "$REPO_ROOT")"` and pass `dirname "$STATE_YAML"` (the state-dir) to `$ESTIMATOR`. Fall back to the existing `$WORKFLOW_DIR`-pass-through when `ORCHESTRATOR_CHANGE_ID` is empty (preserves AC #8). |
| `config/tests/test_resolve_state_yaml.bats` (new) | Unit-test the helper: live, main archive, worktree archive, legacy dated, missing-everywhere → exit 1. |
| `config/tests/test_qa_approve_worktree.bats` (new) | End-to-end: fake worktree-completed feature, `qa-approve.sh <id>` finds state, branch + worktree both removed, no orphans. |
| `config/tests/test_preview_route_worktree.bats` (new) | preview-route in a worktree layout emits a `route_preview` whose `status` is **not** `estimate_unavailable`, and still works in a non-worktree layout. |

### Data Flow

1. Caller has `<change-id>` (string, case-insensitive — already lowercased in
   qa-*; preview-route gets it from env).
2. Caller forks `bash scripts/resolve-state-yaml.sh <id> [<repo-root>]`.
3. Helper iterates candidate paths in documented order, returns the first
   existing file's absolute path on stdout.
4. Caller uses the path directly (qa-*) or derives `dirname` for the
   state-dir (preview-route).

### State Management

The helper is stateless. State lives in `state.yaml` files; the helper only
locates them. No caching, no env mutation.

### Error Handling

- Helper, no path found: print the four candidate paths it tried to stderr,
  exit 1. Callers `set -euo pipefail` propagates the failure (UC-E1).
- Helper, multiple candidates exist: take first in documented precedence and
  print `note: picked <path> over <other>` to stderr (UC-E2). Implementation:
  collect matches as we iterate, return first, print a note if `${#matches[@]} > 1`.
- `git worktree list` unavailable / no match: silently fall through to default
  base (UC-E3). Helper never errors on worktree discovery — only on the final
  "no candidate exists" condition.
- qa-approve teardown race (UC-E4): the script must NOT exit on
  `branch -D` failure; it must continue to `remove-worktree.sh`. Use
  `git -C ... branch -D ... || true` and always invoke remove-worktree if
  `state.yaml` carries a `worktree_path`.
- preview-route resolution failure: keep current behaviour — emit
  `estimate_unavailable` JSON with the helper's stderr as `reason` (UC-E5).
  The helper's exit is captured into a non-fatal branch.

## Constraints

- Bash 3.2 compatible (matches `estimate-cost.sh`): no `declare -A`, no
  `mapfile`, no `${var^^}`.
- No new Python dependencies; the helper is pure bash.
- Verify commands must be repo-root-relative (per learned rule, source orc-86).

## Trade-offs

- One fork per resolution call. Negligible — at most 3 forks per QA run, 1
  per workflow start; not on any hot path.
- Helper has its own error surface (stderr text format) — callers depend on
  exit code only, not on stderr text. Stderr is for humans, not parsing.
- We keep the legacy dated-archive glob even though ORC-86 removed the date
  prefix (D-3). Cost: one extra `compgen` candidate; benefit: any pre-removal
  archive still on a developer's disk continues to work.

## Acceptance Criteria

- AC-1: Given a worktree-completed feature with state at
  `~/code/feature_worktrees/<id>/spec/changes/archive/<id>/state.yaml`, when
  `bash scripts/resolve-state-yaml.sh <id>` is invoked from the main repo,
  then it echoes that absolute path on stdout and exits 0. [traces: UC-1]
- AC-2: Given a legacy main-repo archive at
  `$REPO_ROOT/spec/changes/archive/<id>/state.yaml`, when the helper is
  invoked, then it echoes that path and exits 0; existing qa-approve.sh /
  qa-rework.sh behaviour for non-worktree features is unchanged.
  [traces: UC-2, UC-3, UC-5]
- AC-3: Given no candidate exists for `<id>`, when the helper is invoked,
  then it prints the four candidate paths it tried to stderr and exits 1;
  qa-approve.sh / qa-rework.sh abort with exit 1 before any merge, ticket
  update, branch delete, or worktree removal. [traces: UC-E1]
- AC-4: Given two candidates exist for `<id>` (e.g. main archive and worktree
  archive), when the helper is invoked, then it returns the higher-priority
  one (live > main archive > worktree archive > legacy dated) and writes a
  `note: picked ... over ...` line to stderr. [traces: UC-E2]
- AC-5: Given `$WORKTREE_ROOT` is unset and `git worktree list` returns no
  match for `<id>`, when the helper computes the worktree base, then it falls
  back to `$HOME/code/feature_worktrees/<id>` without erroring.
  [traces: UC-E3]
- AC-6: Given `qa-approve.sh <change-id>` succeeds against a worktree-completed
  feature, when the script finishes, then the merge commit is on `main`, the
  ticket is `Done`, the feature branch is deleted, AND
  `git worktree list` no longer contains the feature's worktree path — all in
  the same run. [traces: UC-1, UC-E4]
- AC-7: Given a worktree-style dispatch invocation (`ORCHESTRATOR_CHANGE_ID`
  set, `ORCHESTRATOR_WORKFLOW_DIR` = worktree root), when `preview-route`
  runs against a workflow with a populated archive history, then its emitted
  `route_preview` JSON has `status` other than `estimate_unavailable` (a real
  estimator output, regardless of cold-start vs hot). [traces: UC-4]
- AC-8: Given a non-worktree dispatch invocation (`ORCHESTRATOR_WORKFLOW_DIR`
  points at the main repo and `state.yaml` lives at
  `<dir>/spec/changes/<id>/state.yaml`), when `preview-route` runs, then it
  emits the same `route_preview` JSON it did before this change. [traces: UC-5]
- AC-9: All three callers (`qa-approve.sh`, `qa-rework.sh`, `preview-route/script.sh`)
  contain no inline candidate-path lookup loop for `state.yaml`; they call
  `scripts/resolve-state-yaml.sh` exclusively. Verifiable by grep:
  `git grep -nE 'spec/changes/archive/.*state\.yaml' scripts/qa-*.sh config/steps/preview-route/script.sh`
  returns no matches.
- AC-10: `scripts/resolve-state-yaml.sh` opens with a header comment
  documenting the lookup order (live → main archive → worktree archive →
  legacy dated) and the worktree-base precedence (`$WORKTREE_ROOT` →
  `git worktree list` → default).

## Decisions

- D-1: CLI helper, not sourceable library → keeps preview-route decoupled
  from `scripts/lib/` and gives a single test surface → one fork per
  resolution, accepted as negligible.
- D-2: Worktree base precedence `$WORKTREE_ROOT` → `git worktree list` →
  default → explicit env wins so tests can override → callers must NOT set
  `$WORKTREE_ROOT` in production unless they mean it.
- D-3: Keep legacy dated-archive glob as last candidate → preserves
  backwards compatibility for any pre-ORC-86 archive on disk → one extra glob
  check per call.
- D-4: preview-route's `estimate_unavailable` semantics unchanged → AC scope
  stays bounded → cold-start estimates still surface as `estimate_unavailable`
  if estimator returns null, which is correct behaviour.
- D-5: qa-approve reuses `config/scripts/inline/remove-worktree.sh` for
  teardown → no duplication of `git worktree remove` logic → introduces a
  cross-tree dependency from `scripts/` into `config/scripts/inline/`, but the
  inline script is the canonical teardown and already reads `STATE_YAML_PATH`.

## Open Questions

- None. OQ-1 through OQ-5 from discovery.md are all resolved in Decisions.

<!-- Format contract: config/steps/design-and-draft-artifacts/prompt.md § Design Format Contract -->
