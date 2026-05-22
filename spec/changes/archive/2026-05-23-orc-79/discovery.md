---
feature-id: orc-79
linear-ticket: ORC-79
---

# Discovery Brief: Move worktree lifecycle into the orchestrator CLI

## Feature Summary

After the ORC-66 run exposed a structural bug — `remove-worktree` executing after `archive-completed-change` had moved `state.yaml` out of the worktree, causing dispatch to re-dispatch already-completed steps — this feature moves worktree teardown out of the workflow step list and into the `orchestrator` CLI itself. The CLI will bracket the workflow: worktree creation stays in `seed-state.sh` at init time; deletion becomes a post-completion CLI action fired after `orchestrator next` would return but the state path is gone (because archive moved it). Removing `remove-worktree` from the workflow schema eliminates an entire class of late-step invalidation hazards and the `flags.yaml` `worktree` gate no longer needs a `steps:` binding.

## Personas & Actors

- **Operator** — engineer running `/orchestrate`, `/autopilot`, or `orchestrator next` in a terminal or automation context with `worktree=true`.
- **Dispatch driver** — the orchestrate/autopilot skill's dispatch loop, which calls `orchestrator next` repeatedly and responds to its exit codes.
- **orchestrator CLI** — the `bin/orchestrator` Python script; sole owner of worktree teardown after this change.
- **seed-state.sh** — shell script responsible for worktree creation at workflow init (unchanged).

## Use Cases

### Happy Path

UC-1: Normal autopilot completion — Operator runs `/autopilot` on a feature; workflow runs through all steps including `archive-completed-change`; CLI detects completion and removes the worktree; operator sees "workflow done" without encountering a re-dispatched step or a FileNotFoundError.

UC-2: Manual orchestration with worktree — Operator runs `/orchestrate` with `worktree=true`; workflow reaches `archive-completed-change` (last workflow step); CLI fires worktree teardown after confirming the state is archived; the feature branch is preserved because it was not yet merged (unmerged branch warning logged, not an error).

UC-3: Merged branch teardown — Autopilot run includes `merge-to-main` before completion; CLI removes worktree and deletes the now-merged branch cleanly with `git branch -d`.

### Error & Edge Cases

UC-E1: Worktree path missing at teardown — what happens when `worktree_path` in state.yaml resolves to a directory that no longer exists: CLI logs a warning and exits 0 (idempotent teardown, mirrors current `remove-worktree.sh` behavior).

UC-E2: Unmerged branch — what happens when teardown fires and the feature branch is not merged into default: `git worktree remove --force` removes the worktree; `git branch -d` fails; CLI logs a warning ("branch not fully merged — skipping deletion") and exits 0; branch is preserved.

UC-E3: State path gone after archive — what happens when the driver calls `orchestrator next` after `archive-completed-change` has moved `state.yaml`: CLI receives `FileNotFoundError` on `load_state`, currently exits 3 (not 1). The new design must treat this condition (or an equivalent signal) as workflow-complete so CLI can fire teardown and exit 1 cleanly.

UC-E4: In-flight workflow with `remove-worktree` in nodes — what happens when an existing `workflow_plan.main.nodes` list includes `remove-worktree` and the step contract is deleted: dispatch falls back to an inline-only stub contract (FileNotFoundError path in dispatch.py:303-313), tries to run a missing script, exits 3. In-flight workflows must be handled — either by keeping the script/contract through a deprecation window or by manual migration of their state.

## Scope

### In Scope

- Remove `remove-worktree` from `feature.yaml` and `bugfix.yaml` step lists.
- Delete or deprecate `config/steps/remove-worktree.yaml` (repo copy and `$ORCHESTRATOR_HOME` copy).
- Reshape `config/flags.yaml` `worktree` gate: drop the `steps: [remove-worktree]` binding; keep the flag for CLI consumption.
- Add post-completion worktree teardown logic to `bin/orchestrator` (the `next` verb's exit-1 path, or the completion detection described in OQ-1).
- Update `config/steps/CONVENTIONS.md` lifecycle invariant (lines 270-273): remove reference to `remove-worktree`.
- Update `config/steps/merge-to-main.yaml` rule 2: remove "must run before remove-worktree."
- Update `skills/autopilot/SKILL.md:57` which explicitly references `remove-worktree` in the complete-phase description — replace with description of CLI-owned teardown.
- Propagate all step contract and config changes to `$ORCHESTRATOR_HOME/config/steps/` copies.
- Regression test for the full archive-then-CLI-teardown completion path (AC-6).
- Branch preservation logic: `--force` remove, `-d` branch delete (not `-D`), warning on skip.

### Out of Scope

- Changing worktree creation — seed-state.sh worktree creation at init is unchanged.
- Changing `archive-completed-change` step logic or ordering — it remains the last workflow step.
- Spike and bootstrap schemas — neither contains `remove-worktree`; no schema changes needed there.
- Changing how `merge-to-main` itself works — only the cross-reference rule in its contract changes.
- Multi-repo or multi-worktree scenarios — out of scope; single worktree per workflow is the current model.

## UI Direction

N/A — no UI components.

## Key Decisions

- **Selected direction: single terminal `complete-workflow` step (user-pinned).** The
  user worked the design space interactively and pinned the model: a single new terminal
  step `complete-workflow` replaces `archive-completed-change`, `merge-to-main`, and
  `remove-worktree` in `feature.yaml` and `bugfix.yaml`. Its script sequences merge →
  archive → cd-out → worktree-remove inside one process. This overrides the
  auto-selection heuristic — it was a directed decision, not a derived one. Rationale:
  collapsing the three late steps into one atomic step structurally dissolves the
  "later step reads state an earlier step moved" hazard class — sequencing is controlled
  inside one script, not across dispatch boundaries.
- **Helper scripts kept, wrapper composes them.** `merge-to-main.sh` and
  `remove-worktree.sh` survive as helper scripts holding git logic; `complete-workflow.sh`
  invokes them. No git logic is duplicated. The standalone *step contracts*
  `merge-to-main.yaml` and `remove-worktree.yaml` are removed (those steps no longer
  exist). Rationale: reuse existing, tested git logic; one new orchestration script.
- **Flags become script-internal gates.** `merge_to_main` and `worktree` move to the
  `behavioral:` section of `flags.yaml` and are read by `complete-workflow.sh` to gate
  its merge and cleanup phases. Archive is unconditional. Rationale: with no `steps:`
  binding left, they are behavioral flags, not step-filtering gates.
- **In-flight migration is a non-goal.** Workflows seeded before this change keep the
  three old step ids frozen in `workflow_plan.main.nodes`; they must finish on the
  pre-change engine or be manually re-seeded. No deprecation-window stub. Rationale:
  the user pinned simplicity; a stub is complexity the model explicitly rejected.

See `design.md` for the full approach analysis and acceptance criteria.

## Open Questions

- OQ-1: Post-archive completion detection. After `archive-completed-change` runs (exit 0, inline), the driver loops and calls `orchestrator next` again. At that point `state.yaml` is at the archived path, not the original; `load_state` at line 224 of `bin/orchestrator` raises `FileNotFoundError`, currently exits 3 (error), not 1 (complete). The agreed design says "fire teardown after `orchestrator next` returns exit 1" — but the current CLI exits 3, not 1, when the state file is missing post-archive. Does the design intend to: (a) change the CLI to exit 1 when the state file is absent (treat missing-file as workflow-complete, using the pre-dispatch cached `worktree_path`/`branch`/`worktree` flag values), or (b) fire teardown from within the same CLI invocation that dispatched `archive-completed-change` (detecting the state-mutating step at the dispatch site, after it exits 0, using the in-memory `State.worktree_path` and `state.raw['branch']` already captured before script execution — no re-read of disk)? Option (b) is safer because it does not repurpose a "file not found" error as a completion signal. Architect decides.

- OQ-2: Autopilot schema vs. flag-set. AC-2 enumerates "feature, bugfix, spike, bootstrap, autopilot" as the five schemas to update. No `autopilot.yaml` schema file exists — autopilot is the `--autopilot` flag-set in `flags.yaml` (`auto=true`, `agents=true`, `merge_to_main=true`), not a schema. Only `feature.yaml` and `bugfix.yaml` actually contain `remove-worktree`. Should AC-2's scope be read as "all schemas that currently include remove-worktree" (i.e., just those two), or is there a planned autopilot schema that should also be updated?

- OQ-3: In-flight workflow migration. Any workflow seeded before this change will have `remove-worktree` in its frozen `workflow_plan.main.nodes`. Deleting the step contract causes dispatch to fall back to an inline-only stub with no `run:` field, which raises `ContractDispatchError` (exit 3). Should the step contract and script be kept through a deprecation window (with `remove-worktree.sh` becoming a no-op), or should in-flight workflows be manually migrated (state.yaml nodes list pruned) before the contract is deleted?

- OQ-4: `flags.yaml` worktree gate reshape. After removal, the `worktree` entry in `flags.yaml` will have no `steps:` list — it will be a behavioral flag the CLI reads directly. Should it be moved from the `gates:` section (which implies step-filtering) to the `behavioral:` section, or kept as a gate with an empty `steps:` list to preserve existing `flags.worktree` semantics for callers that read the flag?

- OQ-5: Symptom description vs. current code path. The ticket describes the failure as "`orchestrator next` re-evaluates `execute-next-task`'s `repeat_until` predicate by reading the now-moved `tasks.md`, fails to confirm completion, and re-dispatches a completed step." The current code path (post-ORC-66 fix) is different: `archive-completed-change` is pre-recorded before its script runs (bin/orchestrator lines 375-387), the script moves state.yaml, the CLI exits 0, the driver loops, `load_state` on the moved path raises `FileNotFoundError`, and the CLI exits 3 — not a repeat_until re-dispatch. Does the ticket describe a historical code path (pre-ORC-66 fix) or an alternative failure mode that still exists? The structural fix (CLI-owned teardown) dissolves both paths regardless, but the architect should know whether the re-dispatch symptom can still occur in any currently-reachable code path.

## Constraints

### CLI/Script Surface Inventory (mandatory)

**`bin/orchestrator` subcommands:**
- `next <state.yaml>` — dispatches next step; exit 0+JSON (agent), 0+empty (inline), 1 (complete), 2 (blocked), 3 (error). **Target for teardown logic.**
- `done <state.yaml>` — records a step result (alias: `record`).
- `record <state.yaml>` — silent alias for `done` (backward compat).
- `ready <state.yaml>` — read-only; prints ready node ids.
- `graph <state.yaml>` — read-only; prints Mermaid DAG.
- `doctor` — diagnostics; no state.yaml arg.

**`config/scripts/inline/` (all entrypoints):**
- `archive-completed-change.sh` — **directly involved**: last workflow step; moves `$WORKTREE_ROOT/spec/changes/$CHANGE_ID` to `$REPO_ROOT/$ARCHIVE_PATH`, then runs `rm -rf "$SRC"`. State-mutating; pre-recorded in CLI.
- `remove-worktree.sh` — **to be deleted/deprecated**: reads `WORKTREE_PATH`, `BRANCH`, `REPO_ROOT`; does `git worktree remove --force` + `git branch -d`.
- `merge-to-main.sh` — **cross-reference rule update needed**: merges feature branch into default; currently has "must run before remove-worktree" rule.
- `mark-change-completed.sh`, `compute-swe-metrics.sh`, `bootstrap-commit.sh`, `capture-test-baseline.sh`, `check-bootstrap-state.sh`, `compute-prediction-accuracy.sh`, `detect-language.sh`, `git-init.sh`, `merge-to-main.sh`, `preview-route.sh`, `register-with-orchestrator-home.sh`, `run-quality-baseline.sh`, `setup-claude-md.sh`, `setup-claude-settings.sh`, `setup-portless.sh`, `verify-report.sh`, `write-bootstrap-state.sh`, `append-retro.sh` — not directly modified; all receive `WORKTREE_PATH` env via `_inline_script_env` in `bin/orchestrator`.
- `_read_state_env.sh` — shared sourced helper for reading state.yaml fields into bash env; used by `archive-completed-change.sh` and `merge-to-main.sh`.

**`skills/orchestrate/scripts/seed-state.sh`** — sole worktree creator; lines 138-151; **unchanged** by this feature.

**Step contracts affected:**
- `config/steps/remove-worktree.yaml` (and `$ORCHESTRATOR_HOME/config/steps/remove-worktree.yaml`) — to be deleted or emptied.
- `config/steps/merge-to-main.yaml` (and `$ORCHESTRATOR_HOME` copy) — rule 2 references `remove-worktree`; update.
- `config/steps/CONVENTIONS.md` (and `$ORCHESTRATOR_HOME` copy) — lines 270-273 reference the `archive → remove-worktree` ordering invariant; rewrite.

**`config/flags.yaml`** (and `$ORCHESTRATOR_HOME/config/flags.yaml`) — `worktree` gate at line 10; drop `steps:` binding.

### Dual-tree constraint

Step contracts and config files exist in two locations: the repo (`/Users/spidey/code/orchestrator/config/`) and `$ORCHESTRATOR_HOME/config/` (`~/.config/orchestrator/config/`). Changes must be applied to both trees. The CLI resolves repo overrides under `.orchestrator/` first, then falls back to `$ORCHESTRATOR_HOME/config/`. The schemas (`feature.yaml`, `bugfix.yaml`) live only in `$ORCHESTRATOR_HOME/config/workflows/`; step contracts exist in both trees.

### Existing test coverage

- `test_archive_step_record_crash.py` — regression test for the pre-archive `record` crash; directly adjacent to this change; must pass after.
- `test_repeat_until.py` — exercises `_check_all_tasks_completed` predicate; `tasks_path` reads from state.yaml; indirectly affected if worktree teardown changes state shape.
- `test_workflow_schemas_load.py` — loads real workflow YAMLs; will need update once `remove-worktree` is removed from schemas.
- New regression test required (AC-6): full archive-then-CLI-teardown path asserting no completed step is re-dispatched.

### Engine self-modification hazard

This change modifies files that the running orchestrator reads during dispatch (workflow schemas, step contracts, CLI code itself). Any in-flight `orc-79` workflow run uses the modified engine to build its next steps. This is the standard engine self-modification hazard documented in project memory. The worktree for `orc-79` does not use `worktree=true` (this is a flat non-worktree run), so `remove-worktree` is already filtered from its `workflow_plan.main.nodes` by the gate — the hazard is confined to other in-flight worktree-enabled workflows.
