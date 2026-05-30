---
feature-id: orc-108
linear-ticket: ORC-108
---

# Discovery Brief: Replace task injection with explicit execute-tasks step in feature/bugfix workflows

## Feature Summary

Three related runtime DAG surgery problems share a root cause: workflow schemas declare incomplete DAGs and compensate at runtime. `expand_plan.py` injects task-T-* nodes by finding `run-phase-review` by name and inserting before it; `complete_phase.py` injects complete-phase nodes into the feature DAG by scanning for the `compute-prediction-accuracy` anchor. Both patterns couple runtime code to schema topology and create hidden state mutations. The fix declares the gaps explicitly in the schemas: add an `execute-tasks` anchor step to feature/bugfix so tasks have a named home, and refactor `complete` to seed its own fresh state from `complete.yaml` so it doesn't need to mutate the feature DAG at all.

## Personas & Actors

- **Orchestrator engine** — the dispatcher that walks the declared DAG; must see all nodes at DAG-build time, not discover them mid-run.
- **Feature developer (human)** — uses `orchestrator complete <slug>` after implement finishes; expects clean handoff without DAG surgery.
- **Maintainer** — reads workflow schemas and step contracts to understand the system; currently confused by invisible runtime mutations.

## Use Cases

### Happy Path

UC-1: Task node injection via schema anchor — `expand_plan` reads tasks.yaml, builds task-T-* nodes, and inserts them under the declared `execute-tasks` anchor in the DAG rather than positionally searching for `run-phase-review`.

UC-2: Complete workflow seeds fresh state — `orchestrator complete <slug>` calls `orchestrator-run.sh --schema complete`, which seeds a fresh state.yaml from `complete.yaml` (using feature context), and the dispatcher runs the complete-phase steps without touching the feature state.

UC-3: Schema readability — a maintainer reads `feature.yaml` or `bugfix.yaml` and sees the full intended step sequence including the execute-tasks anchor, with task-T-* nodes appearing as children of that anchor after `expand-plan` runs.

### Error & Edge Cases

UC-E1: No tasks.yaml at expand-plan time — `expand_plan` raises `FileNotFoundError` as today; behavior unchanged, error message references the anchor step rather than positional insertion.

UC-E2: complete invoked before implement finishes — `orchestrator complete <slug>` must error out if task-T-* or implement-phase steps are still pending; fresh-seed model must preserve this guard (currently in `complete_phase.py`, must move to seeding logic or pre-seed validation).

UC-E3: double-complete guard — running `orchestrator complete` on an already-archived change must still error with "Feature already completed" as today (handled by `archive_completion probe`; unchanged).

UC-E4: expand-plan idempotency — re-running expand-plan after tasks are already injected must remain idempotent; the anchor-based approach must not duplicate nodes.

## Scope

### In Scope

- Add explicit `execute-tasks` step to `config/workflows/feature.yaml` and `config/workflows/bugfix.yaml` between `expand-plan` and `run-phase-review` (feature) or `run-phase-review` (bugfix).
- Refactor `orchestrator_next/expand_plan.py` to find the `execute-tasks` anchor node rather than `run-phase-review` as its injection point.
- Refactor `orchestrator complete` / `orchestrator-run.sh` so `complete` schema is no longer `RESUME_ONLY` — seeds a fresh state from `complete.yaml` using feature ticket context.
- Delete `orchestrator_next/complete_phase.py` and its tests once seeding is working.
- Update `complete.yaml` comment (line 9) that references "complete-phase steps through archive-completed-change" to reflect new seeding model.
- Update `expand_plan.py` docstring (line 8–10) to reference the anchor rather than positional insertion before `run-phase-review`.

### Out of Scope

- Changes to `execute-one-task` step contract or `developer` agent behavior — task node shape is unchanged.
- Changes to `generate_plan.py` node-building or topo-sort logic — task-node construction is unchanged.
- Changes to how `run-phase-review` consumes task outputs — only the `depends_on` wiring source changes.
- Removal of `run-ux-critique` or other feature steps — only the injection anchor is being added/changed.
- Metrics, dashboard, or DuckDB schema changes — complete phase steps remain the same, only how they're seeded changes.

## UI Direction

N/A — no UI components. This is an engine-level refactor of DAG declaration and state seeding.

## Key Decisions

- **execute-tasks as named anchor vs. repeat_until loop**: The ticket specifies a named anchor step. This is consistent with how the engine handles other structural anchors (e.g. `capture-test-baseline`, `run-phase-review`). The loop-restart pattern is explicitly being deprecated by this work.
- **complete inherits feature state.yaml, not fresh seed**: `run-learn-cycle` reads `step_history` from the active state.yaml; `compute-prediction-accuracy` reads `tasks.yaml` and `design.md` from the state directory (sibling lookup). A truly empty fresh seed would break both. Design pivots: complete-phase steps are declared statically in feature/bugfix schemas, so they are present in the DAG from seed time. `orchestrator complete` marks implement nodes as done and advances `next_step` — no fresh seed.
- **Approach selected: Hybrid (anchor for tasks, static for complete steps)**: `execute-tasks` anchor in schemas for task injection. Complete-phase steps appended to feature/bugfix schema tails. `complete_phase.py` injection logic replaced by a ~30-line guard script.
- **Deletion order matters**: `complete_phase.py` cannot be deleted until (1) anchor + expand_plan refactor works, (2) complete steps are in schemas and the guard script is wired, (3) e2e tests pass through the new code path.
- **Build vs. reuse**: Implement-completeness guard reuses the existing `operator_workflow.workflow_step_ids()` to identify complete-phase steps by schema name — no new schema parsing logic needed.

## Open Questions

- OQ-1: What is the canonical shape of the `execute-tasks` anchor node in the DAG? Should it carry `agent: null`, `agent: inline`, or some other sentinel to signal it is a container anchor rather than a dispatchable step?
- OQ-2: How does complete seeding inherit feature ticket context (ticket ID, Linear refs, repo path)? Does it read from the feature's archived or active state.yaml, or from a separate context file?
- OQ-3: Does `prepare_complete_phase`'s implement-completeness guard (checking for pending task-T-* nodes) move to orchestrator-run.sh as a pre-seed check, or does it become part of a new `seed-complete.sh` validation step?
- OQ-4: Are there any callers of `complete_step_ids_for_schema()` outside of `complete_phase.py` and its tests that would break on deletion?
- OQ-5: Do the existing `test_complete_workflow_e2e.py` (348 lines) and `test_complete_workflow.py` (160 lines) tests exercise `complete_phase.py` directly, or do they test the CLI path — which would survive the delete if the CLI path is rewired to fresh seeding?
