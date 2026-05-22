---
feature-id: orc-79
linear-ticket: ORC-79
---

# Design: Collapse workflow teardown into one terminal `complete-workflow` step

## Context

The ORC-66 run exposed a structural bug: `remove-worktree` (the last workflow step)
ran after `archive-completed-change` had `rm -rf`'d the state directory. The driver
loops `orchestrator next`, `load_state` hits the moved `state.yaml`, raises
`FileNotFoundError`, and the CLI exits 3. The root cause is not a bad ordering rule —
it is that two independent dispatch steps share a dependency (`state.yaml` /
`tasks.md`) that the earlier step destroys. Any "later step reads state an earlier
step moved" arrangement reproduces the hazard.

Current late workflow tail (`feature.yaml`, `bugfix.yaml`):
`… → archive-completed-change → merge-to-main → remove-worktree`. Each is a separate
node in `workflow_plan.main.nodes`, dispatched across separate `orchestrator next`
invocations. `archive-completed-change` and `merge-to-main` source
`config/scripts/inline/_read_state_env.sh` to read `state.yaml`; `remove-worktree`
reads `WORKTREE_PATH`/`BRANCH`/`REPO_ROOT` the same way. The CLI special-cases
`archive-completed-change` in `_STATE_MUTATING_INLINE_STEPS` (`bin/orchestrator:372`)
to pre-record it before the script runs (the ORC-66 fix, commit faa8f6e), because
`record.py` re-opens `state.yaml` to read pre-write bytes and crashes once the file
is moved.

System boundaries this design touches:

- **Dual-tree config.** Step contracts, workflow schemas, `flags.yaml`, and inline
  scripts exist in both the repo (`/Users/spidey/code/orchestrator/config/`) and
  `$ORCHESTRATOR_HOME/config/` (`~/.config/orchestrator/config/`). Verified: both
  trees contain `config/workflows/{feature,bugfix}.yaml` — the discovery brief's
  claim that schemas are HOME-only is wrong. Every config/schema/contract/script
  edit applies to BOTH trees.
- **Single-phase model.** `workflow_plan` uses one phase, `main`. There is no
  per-step phase mapping; the workflow `steps:` list is a flat ordered sequence.
- **Flag pipeline.** `seed-state.sh` merges flag defaults from `gates:` and
  `behavioral:` sections of `flags.yaml` identically into `state.yaml.flags` (it
  iterates `("gates", "behavioral")` the same way). Only the `steps:` list under a
  `gates:` entry causes step filtering. `--autopilot` sets `merge_to_main: true` via
  `cli.--autopilot.sets` by flag name, independent of section. Verified in
  `seed-state.sh:87-108`.

## Goals / Non-Goals

### Goals

- Replace `archive-completed-change`, `merge-to-main`, and `remove-worktree` in
  `feature.yaml` and `bugfix.yaml` with one terminal step `complete-workflow`.
- Make `complete-workflow.sh` sequence — in one process — merge (in worktree) →
  archive (move state out) → `cd "$REPO_ROOT"` → worktree removal, so no dispatch
  boundary sits between a state-moving step and a state-reading step.
- Capture all `state.yaml`-derived values into bash vars at script start, before any
  filesystem mutation, so archive can move `state.yaml` without breaking later phases.
- Classify `complete-workflow` as state-mutating in `bin/orchestrator` so it is
  pre-recorded before its script runs (the ORC-66 crash-avoidance contract).
- Gate the merge phase on `merge_to_main` and the cleanup phase on `worktree`, read
  by the script from `state.yaml.flags`. Archive runs unconditionally.
- Reshape `flags.yaml`: `merge_to_main` and `worktree` move to `behavioral:` (no
  `steps:` binding remains).
- Apply every change to both config trees.
- Add a regression test for the full `complete-workflow` path proving no completed
  step is re-dispatched and no `FileNotFoundError`/exit-3 occurs.

### Non-Goals

- **In-flight workflow migration.** Workflows seeded before this change keep
  `merge-to-main`/`remove-worktree` frozen in their `workflow_plan.main.nodes`. They
  must finish on the pre-change engine, or be manually re-seeded (delete `state.yaml`,
  purge `change_id` rows from `metrics.duckdb` per the rerun-discarded-workflow rule).
  No deprecation-window stub is built — the engine-self-modification hazard makes a
  half-migrated run unreliable, and a stub is complexity the pinned model rejects.
  `archive-completed-change` is retained (see the spike Non-Goal below), so an
  in-flight workflow that still dispatches it keeps both its contract and its
  `_STATE_MUTATING_INLINE_STEPS` pre-record protection.
- **ORC-79 scope is `feature.yaml` + `bugfix.yaml` only.** Migrating spike to the
  terminal-step model is a follow-up ticket.
- **`spike.yaml` is NOT migrated and is left entirely untouched.** Verified by `grep`:
  `spike.yaml:7` contains `archive-completed-change` as its terminal step;
  `bootstrap.yaml` contains none of the three removed steps (genuinely unaffected).
  Because spike still dispatches `archive-completed-change`, its step contract
  (`archive-completed-change.yaml`), its script (`archive-completed-change.sh`), and its
  `_STATE_MUTATING_INLINE_STEPS` classification are all RETAINED. ORC-79 only removes
  `merge-to-main` and `remove-worktree` as steps and contracts.
- Changing worktree *creation* — `seed-state.sh` is unchanged.
- Changing archive, merge, or worktree-removal git logic — the helper scripts'
  behavior is preserved; only their invocation changes.
- Multi-repo / multi-worktree scenarios — single worktree per workflow stays the model.

## Approaches Considered

### Approach 1: CLI-owned post-completion teardown (the original ticket model)

Move worktree teardown into `bin/orchestrator` itself: when `orchestrator next`
detects the workflow is complete, the CLI fires merge + worktree removal. Removes
`remove-worktree`/`merge-to-main` as steps but keeps `archive-completed-change`.

- Pros: teardown is engine-owned; no late workflow step at all.
- Cons: requires repurposing a `FileNotFoundError` (state.yaml moved by archive) as a
  "workflow complete" signal — overloading an error condition as control flow. The CLI
  would need to cache `worktree_path`/`branch`/flags before dispatch and act on missing
  state afterward. It splices lifecycle logic into the dispatch hot path, and the
  teardown is no longer a recorded `step_history` entry — metrics lose a node.

### Approach 2: Reorder steps so worktree removal precedes archive

Keep three steps but run `remove-worktree` before `archive-completed-change` so the
state directory still exists when worktree teardown reads it.

- Pros: minimal schema edit.
- Cons: does not work — archive moves `state.yaml` *out of* the worktree to the repo;
  if the worktree is gone first, archive has nothing to move. The two operations are
  inherently ordered archive-then-remove. Reordering only relocates the hazard.

### Approach 3: Single terminal `complete-workflow` step (SELECTED — user-pinned)

One new terminal step `complete-workflow` replaces all three. Its script runs, in one
process: (1) merge (if `merge_to_main`), while CWD is in the worktree; (2) archive
(unconditional) — moves `state.yaml`/`tasks.md`/artifacts to the repo archive;
(3) `cd "$REPO_ROOT"`, then worktree removal (if `worktree`). The git logic for merge
and removal stays in `merge-to-main.sh` and `remove-worktree.sh`, which the wrapper
invokes; archive logic stays in `archive-completed-change.sh` (also invoked by the
wrapper). The standalone step contracts for merge and removal are deleted.

- Pros: the three operations live behind one dispatch boundary — no `orchestrator next`
  call ever sits between a state-moving operation and a state-reading one. The hazard
  class is structurally eliminated, not patched. Teardown stays a recorded step. No
  error-as-signal overloading. Git logic is reused, not duplicated.
- Cons: introduces a new step contract and one orchestration script; in-flight
  worktree-enabled workflows must be handled out-of-band (covered by the non-goal).

### Selected Approach

**Approach 3**, pinned by the user during the explore phase — this overrides the
workflow's auto-selection heuristic; it was a directed decision. It is the only
approach that *dissolves* the bug rather than relocating it (Approach 2 fails
outright) or trading it for control-flow overloading (Approach 1). It honors the
simplicity-first principle: reuse the three existing tested scripts, add one wrapper
that sequences them, delete two step contracts. The acceptance criteria below
supersede the original ORC-79 ticket wording ("CLI-owned teardown") — the design,
not the ticket, is the source of truth for ACs, and the model converged from
"CLI-owned" → "driver-owned" → the terminal-step model during the explore phase.

## High-Level Design

### Architecture Overview

```
feature.yaml / bugfix.yaml  steps:  … → compute-swe-metrics → complete-workflow   (terminal)

orchestrator next  ──dispatches──▶  complete-workflow.yaml  (run: scripts/inline/complete-workflow.sh)
                                            │
bin/orchestrator: _STATE_MUTATING_INLINE_STEPS = {archive-completed-change, complete-workflow}
   └─ pre-records `completed` BEFORE running the script (record.py crash-avoidance)
                                            │
                              complete-workflow.sh  (CWD starts inside worktree)
   ┌────────────────────────────────────────┴───────────────────────────────────────┐
   │ 0. READ all state: source _read_state_env.sh → CHANGE_ID, ARCHIVE_PATH,         │
   │    WORKTREE_ROOT/PATH, REPO_ROOT, BRANCH, + flags merge_to_main / worktree      │
   │    INTO BASH VARS — before any filesystem mutation                              │
   │ 1. MERGE   (if merge_to_main)   → invoke merge-to-main.sh git logic             │
   │ 2. ARCHIVE (unconditional)      → invoke archive-completed-change.sh logic      │
   │                                   (moves state.yaml/tasks.md/artifacts out)    │
   │ 3. cd "$REPO_ROOT"   ◀── critical: git worktree remove fails if CWD is inside  │
   │    CLEANUP (if worktree)        → invoke remove-worktree.sh git logic           │
   └────────────────────────────────────────────────────────────────────────────────┘
```

### Key Abstractions

- **`complete-workflow` step** — a single state-mutating inline step; the workflow's
  terminal node. Owns the merge → archive → cleanup sequence.
- **`complete-workflow.sh`** — the orchestration script. Reads all inputs up front,
  sequences the three phases, gates phases 1 and 3 on flags, emits a combined JSON
  record on stdout.
- **Helper scripts as composable units** — `merge-to-main.sh`, `remove-worktree.sh`,
  `archive-completed-change.sh` keep their git/filesystem logic; the wrapper composes
  them. Their separate *step contracts* (for merge and removal) are deleted.

## Low-Level Design

### Components

| Component | Responsibility | Inputs | Outputs |
|-----------|----------------|--------|---------|
| `complete-workflow.yaml` (both trees) | Step contract: `run: scripts/inline/complete-workflow.sh`, `outputs: [completion_record]` | dispatched by `orchestrator next` | step action |
| `complete-workflow.sh` | Read state up front; sequence merge → archive → cd → cleanup; emit combined JSON | `STATE_YAML_PATH`, `REPO_ROOT` env; `state.yaml` (incl. `flags`) | `{completion_record: {merge_record, archive_record, worktree_record}}` |
| `merge-to-main.sh` (kept) | Merge feature branch into default branch | `STATE_YAML_PATH`, `REPO_ROOT` | `{merge_record: …}` |
| `archive-completed-change.sh` (kept) | Move state dir → repo archive, commit | `STATE_YAML_PATH` / env | `{archive_record: …}` |
| `remove-worktree.sh` (kept) | `git worktree remove --force` + `git branch -d` | `STATE_YAML_PATH`, `REPO_ROOT` | `{removed: …}` |
| `bin/orchestrator` | Pre-record `complete-workflow` as state-mutating | step id | recorded `step_history` entry |
| `feature.yaml` / `bugfix.yaml` (both trees) | Schema: replace three tail steps with `complete-workflow` | — | workflow plan |
| `flags.yaml` (both trees) | Move `merge_to_main`/`worktree` to `behavioral:` | — | flag registry |

The wrapper invokes the three helpers as **separate `bash` subprocesses**, not via
`source`. The helpers use `set -uo pipefail` and call `exit` on their early-return
paths; `source`-ing an `exit`-using script would terminate the wrapper. Calling
`bash "$dir/merge-to-main.sh"` runs each helper in its own process — `exit` ends only
that subprocess, and the wrapper captures stdout + exit code. The user's "archive
sources it" intent is satisfied in substance (no git logic duplicated, helpers
retained and composed); the literal mechanism is `bash <script>` subprocess
invocation, which is correct here.

### Data Flow

1. Driver calls `orchestrator next <state.yaml>` → dispatch resolves
   `complete-workflow.yaml` → emits an inline action (`run:` set).
2. `bin/orchestrator` sees `_step_id == "complete-workflow"` ∈ `_STATE_MUTATING_INLINE_STEPS`
   → pre-records a `completed` `step_history` entry while `state.yaml` is still at its
   live path.
3. `bin/orchestrator` runs `bash complete-workflow.sh` with `_inline_script_env`
   (provides `STATE_YAML_PATH`, `REPO_ROOT`, `CHANGE_ID`, `BRANCH`, `WORKTREE_*`,
   `ARCHIVE_PATH`). CWD is the worktree.
4. `complete-workflow.sh` step 0: sources `_read_state_env.sh`, reads `CHANGE_ID`,
   `ARCHIVE_PATH`, `WORKTREE_ROOT`, `WORKTREE_PATH`, `REPO_ROOT`, `BRANCH`, and the two
   flags into bash vars. All reads happen here, before any mutation.
5. Step 1 (merge): if `merge_to_main` true, run `bash merge-to-main.sh`, capture
   `merge_record`. CWD still in worktree. On merge conflict (helper exits non-zero),
   the wrapper exits non-zero — archive and cleanup do not run.
6. Step 2 (archive): run `bash archive-completed-change.sh`, capture `archive_record`.
   This moves `state.yaml`/`tasks.md`/artifacts to `$REPO_ROOT/$ARCHIVE_PATH`. After
   this point `STATE_YAML_PATH` no longer exists — but every value the script needs is
   already in bash vars from step 0.
7. Step 3 (cleanup): `cd "$REPO_ROOT"` FIRST, then if `worktree` true, run
   `bash remove-worktree.sh`, capture `worktree_record`.
8. Wrapper emits `{completion_record: {merge_record, archive_record, worktree_record}}`
   on stdout, exit 0.
9. Driver calls `orchestrator next` again → all nodes `completed` → exit 1 (workflow
   complete). No re-dispatch, no `FileNotFoundError`.

### State Management

- `state.yaml` lives in the worktree at dispatch time; archive moves it to the repo
  archive. The `complete-workflow` `step_history` entry is durable *before* the move
  (pre-recorded in step 2 of Data Flow). The archive script then moves an
  already-final file.
- Flags: `complete-workflow.sh` reads `flags.merge_to_main` and `flags.worktree` from
  `state.yaml`. `_inline_script_env` does NOT forward `flags.*` to inline scripts —
  the script reads them itself. The chosen mechanism (see Decisions): extend
  `_read_state_env.sh`'s `RESOLVERS` allowlist with `MERGE_TO_MAIN` and `WORKTREE`
  resolvers reading `r.get("flags", {}).get(...)`.
- `node.status` for `complete-workflow` is flipped to `completed` by `record.py` on the
  pre-record; `next_step` re-derives to "no ready node" → exit 1.

### Error Handling

| Failure | Behavior |
|---------|----------|
| Merge conflict (`merge_to_main` true) | `merge-to-main.sh` exits non-zero; wrapper exits non-zero (exit 3); archive + cleanup skipped; worktree preserved. Driver halts — recoverable. |
| Archive `cp` fails | `archive-completed-change.sh`'s hardened `cp … || exit 1` exits non-zero *before* `rm -rf`; wrapper exits non-zero; source dir intact (with the pre-recorded entry). |
| Worktree path already gone | `remove-worktree.sh` logs a warning, exits 0 (idempotent). Wrapper exit 0. |
| Branch not merged at `git branch -d` | `remove-worktree.sh` logs "branch not fully merged — skipping deletion", exits 0; branch preserved. |
| `merge_to_main` false | Merge phase skipped; `merge_record` = `{skipped: true, reason: "merge_to_main flag false"}`. |
| `worktree` false | Cleanup phase skipped; `worktree_record` = `{skipped: true, reason: "worktree flag false"}`. |
| Pre-record vs. script failure | Same ORC-66 tradeoff: state-mutating step records `completed` before script exit code is known; a non-zero archive `cp` keeps the source dir, driver halts on exit 3. |

## Constraints

- **Dual-tree.** Every config/schema/contract/script change applies to BOTH
  `/Users/spidey/code/orchestrator/config/` and `~/.config/orchestrator/config/`.
- **cd-before-remove.** `git worktree remove` fails when CWD is inside the target
  worktree. `complete-workflow.sh` starts with CWD in the worktree; it MUST
  `cd "$REPO_ROOT"` before invoking `remove-worktree.sh`.
- **Read-before-mutate.** All `state.yaml`-derived values MUST be read into bash vars
  at script start, before archive moves `state.yaml`.
- **State-mutating classification.** `_STATE_MUTATING_INLINE_STEPS`
  (`bin/orchestrator`) MUST contain BOTH `archive-completed-change` (retained — spike
  still dispatches it as its terminal step) and `complete-workflow` (added), or
  `record.py` crashes on the post-archive path exactly as the original bug did.
- **No LLM-tool references** in schemas or step contracts.
- **Engine self-modification hazard.** This change edits files the running engine
  reads. The `orc-79` run itself is `worktree=false` (flat), so it is unaffected by
  the worktree-removal path; other in-flight worktree runs are covered by the
  in-flight non-goal.

## Trade-offs

- **One step does three things.** `complete-workflow` is less granular than three
  separate steps — a merge failure and a cleanup failure surface under one step id.
  Accepted: the three operations are inherently a single atomic lifecycle action, and
  the combined `completion_record` carries per-phase sub-records for diagnosis. The
  granularity loss is the price of dissolving the hazard class.
- **In-flight workflows are not migrated.** Pre-change worktree runs break if dispatched
  on the new engine. Accepted: the engine-self-modification hazard already makes
  mid-flight schema changes unreliable; a no-op stub is more code than the simplicity
  model allows, and the operator workaround (finish on old engine / re-seed) is
  documented.
- **Helpers invoked as subprocesses, not `source`d.** Slightly more overhead than
  sourcing, and helper stdout must be captured/parsed. Accepted: the helpers `exit`
  on early-return paths; `source`-ing them would kill the wrapper. Subprocess
  invocation is the correct, safe composition.
- **`_read_state_env.sh` gains flag resolvers.** Its allowlist, named for "state env",
  now also resolves two behavioral flags. Accepted: keeps all `state.yaml` reads in
  one audited place; the alternative (forwarding `FLAG_*` env from `_inline_script_env`)
  spreads state-reading across two locations. See Decisions.

## Acceptance Criteria

- AC-1: Given a feature or bugfix workflow, when the schema is loaded, then the
  `steps:` list ends with `complete-workflow` and contains none of
  `archive-completed-change`, `merge-to-main`, `remove-worktree` — in BOTH config
  trees. Verify: `grep` each of `config/workflows/{feature,bugfix}.yaml` in repo and
  `$ORCHESTRATOR_HOME`. [traces: UC-1, UC-2]

- AC-2: Given the step contract directory, when contracts are listed, then
  `complete-workflow.yaml` exists, `merge-to-main.yaml` / `remove-worktree.yaml` are
  absent, and `archive-completed-change.yaml` is RETAINED (spike still dispatches it).
  Verify: `ls config/steps/`. [traces: UC-1]

- AC-3: Given `complete-workflow.sh`, when its body is inspected, then a
  `cd "$REPO_ROOT"` (or `cd "$REPO_ROOT"`-equivalent) statement appears before any
  `remove-worktree.sh` invocation, and all `read_state_env` / state-read statements
  appear before the archive invocation. Verify: ordering assertion (grep line numbers)
  in the regression test. [traces: UC-1, UC-E3]

- AC-4: Given `bin/orchestrator`, when `_STATE_MUTATING_INLINE_STEPS` is inspected,
  then it contains BOTH `archive-completed-change` (retained — spike still dispatches
  it) and `complete-workflow` (added). Verify:
  `grep _STATE_MUTATING_INLINE_STEPS bin/orchestrator`. [traces: UC-1, UC-E3]

- AC-5: Given `flags.yaml`, when the registry is loaded, then `merge_to_main` and
  `worktree` appear under `behavioral:` (not `gates:`) with no `steps:` key, and
  `--autopilot` still resolves `merge_to_main: true` — in BOTH config trees. Verify:
  `test_workflow_schemas_load.py` / flag-load assertion. [traces: UC-2, UC-3]

- AC-6: Given a simulated workflow with `flags.worktree=true` and
  `flags.merge_to_main=true` at the `complete-workflow` step, when `orchestrator next`
  dispatches and runs it and the driver calls `orchestrator next` once more, then:
  (i) the archive directory exists with the moved `state.yaml`/`tasks.md`; (ii) the
  worktree directory is gone; (iii) no `FileNotFoundError` is raised and the CLI does
  not exit 3; (iv) no already-`completed` step id is re-dispatched (the final
  `orchestrator next` exits 1). Verify: new regression test exercising the full path.
  [traces: UC-1, UC-3, UC-E3, UC-E4]

- AC-7: Given `complete-workflow.sh` running with `merge_to_main=false` and a worktree
  whose path no longer exists, when the step runs, then archive completes, the merge
  and cleanup phases record `skipped`, and the script exits 0 (idempotent teardown).
  Verify: regression test case with absent worktree dir + flag false. [traces: UC-E1, UC-E2]

- AC-8: Given the existing tests, when the suite runs after the change, then
  `test_archive_step_record_crash.py` (retargeted to `complete-workflow` — it keys on
  the sole state-mutating inline step), `test_repeat_until.py`, and
  `test_workflow_schemas_load.py` (updated for the new step list) all pass. Verify:
  `pytest config/scripts/orchestrator_next/tests/ -q`. [traces: UC-1]

- AC-9: Given the docs that reference the removed steps, when inspected after the
  change, then `config/steps/CONVENTIONS.md` lifecycle invariant,
  `merge-to-main.sh`'s "must run before remove-worktree" reference, and
  `skills/autopilot/SKILL.md:57` no longer reference `remove-worktree` as a workflow
  step and instead describe the `complete-workflow` step. Verify: `grep -r remove-worktree`
  over those files returns no step-reference matches. [traces: UC-1]

- AC-10: Given `complete-workflow.sh` running with `worktree=true` and a feature branch
  that is NOT merged into the default branch, when the cleanup phase runs, then
  `git worktree remove --force` succeeds, `git branch -d` is skipped (branch
  preserved), a warning is logged ("branch not fully merged — skipping deletion"), and
  the script exits 0. Verify: regression test case with an unmerged branch fixture.
  [traces: UC-E2]

## Decisions

- Single terminal `complete-workflow` step (Approach 3) → user-pinned during explore;
  only model that dissolves rather than relocates the hazard → three dispatch nodes
  collapse to one; the "later step reads moved state" class cannot occur.

- Helper scripts (`merge-to-main.sh`, `remove-worktree.sh`, `archive-completed-change.sh`)
  kept and composed by the wrapper → reuse tested git logic, no duplication → the
  standalone *step contracts* `merge-to-main.yaml` and `remove-worktree.yaml` are
  deleted (those step ids no longer dispatch in `feature.yaml`/`bugfix.yaml`);
  `archive-completed-change.yaml` is RETAINED because `spike.yaml` still dispatches
  `archive-completed-change` as its terminal step.

- Wrapper invokes helpers via `bash <script>` subprocesses, not `source` → the helpers
  `exit` on early-return paths and would kill a sourcing parent → wrapper captures
  stdout + exit code per helper; "archive sources it" is honored in substance (logic
  reused, not duplicated), not by literal `source`.

- Flags read via `_read_state_env.sh` resolvers (`MERGE_TO_MAIN`, `WORKTREE`) rather
  than `FLAG_*` env from `_inline_script_env` → keeps all `state.yaml` reads in one
  audited allowlist; the script already sources `_read_state_env.sh` for path values,
  so flags ride the same call → one Python edit to the resolver map; `_inline_script_env`
  untouched. (Trade-off: the allowlist named "state env" now also resolves behavioral
  flags — accepted for single-source-of-truth on `state.yaml` reads.)

- `merge_to_main` / `worktree` → `behavioral:` section of `flags.yaml` → with no
  `steps:` binding they no longer filter steps; `seed-state.sh` merges `behavioral:`
  and `gates:` defaults identically, and `--autopilot` sets flags by name → the move
  is value-preserving; `worktree` stays readable by `seed-state.sh` for worktree
  creation.

- In-flight migration is a non-goal → engine-self-modification hazard + pinned
  simplicity → operator note: finish pre-change worktree runs on the old engine, or
  re-seed (delete `state.yaml`, purge `change_id` metrics rows).

- ACs supersede the original ORC-79 ticket's "CLI-owned teardown" wording → the
  pinned model converged past the ticket text during explore (CLI-owned → driver-owned
  → terminal-step); design.md is the AC source of truth → the Linear ticket is not
  edited.

## Open Questions

None — the five discovery-brief open questions (OQ-1..OQ-5) are resolved by the
pinned terminal-step model and addressed in Context, Non-Goals, and Decisions.
