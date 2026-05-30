---
feature-id: orc-108
linear-ticket: ORC-108
---

# Design: Replace task injection with explicit execute-tasks step in feature/bugfix workflows

## Context

The orchestrator DAG walker requires all nodes to be visible at dispatch time. Two runtime surgery patterns violate this:

1. `expand_plan.py` injects task-T-* nodes by searching for `run-phase-review` by name and inserting before it — a positional coupling that breaks if schema topology changes.
2. `complete_phase.py` injects complete-phase nodes (from `complete.yaml`) into the live feature DAG because `orchestrator complete` is `RESUME_ONLY` and the feature schema does not declare those steps.

Both patterns hide schema topology from readers and create fragile runtime coupling to specific step names.

**Critical constraint discovered during design:** Complete-phase steps (`compute-prediction-accuracy`, `run-learn-cycle`) read the feature's `state.yaml` at the active artifact directory for `step_history`, `tasks.yaml`, and `design.md`. A fresh empty seed for `orchestrator complete` would produce an empty `step_history` and break both metrics steps. Complete must operate on the feature's existing `state.yaml`.

## Goals / Non-Goals

### Goals

- Add an explicit `execute-tasks` anchor step to `feature.yaml` and `bugfix.yaml` so task-T-* nodes have a named home in the schema.
- Refactor `expand_plan.py` to inject task nodes under the `execute-tasks` anchor rather than searching for `run-phase-review` positionally.
- Declare complete-phase steps directly in `feature.yaml` and `bugfix.yaml` so the seeded DAG includes them from the start.
- Delete `complete_phase.py` and its direct tests once the feature DAG carries complete-phase steps statically.
- Update `orchestrator-run.sh` to skip the `complete_phase.py` call; replace it with a lightweight implement-completeness guard.
- Update `complete.yaml` comment to reflect the new model.

### Non-Goals

- Fresh-state seeding for `orchestrator complete` — complete must operate on the feature's existing `state.yaml` (step_history dependency).
- Changes to `execute-one-task` contract or `developer` agent behavior.
- Changes to `generate_plan.py` node-building or topo-sort logic.
- Metrics/DuckDB schema changes.
- Changes to `run-phase-review`, `run-ux-critique`, or other feature steps beyond depend_on wiring.

## Approaches Considered

### Approach 1: Schema-static complete steps (add to feature.yaml/bugfix.yaml tails)

Add complete-phase step ids directly to `feature.yaml` and `bugfix.yaml` step lists, after `run-phase-review`. `generate_plan.py` includes them at seed time. `orchestrator complete` marks implement-phase nodes as completed and advances `next_step` — `complete_phase.py`'s injection logic is no longer needed.

**Pros:** No expand-plan changes for complete steps. Schema is fully readable. Single source of truth. Complete-phase steps are in the DAG from day one of the feature workflow.

**Cons:** Features carry complete-phase nodes as `pending` throughout implement — they're visible but unreachable (correct — dispatcher won't pick them up until their `depends_on` chain is satisfied). Requires the completeness guard to remain in `orchestrator-run.sh` to block premature `complete` invocation.

**Complexity:** S

### Approach 2: expand-plan injects both task nodes and complete-phase nodes

`expand_plan.py` handles two injection passes: task-T-* nodes under `execute-tasks` anchor, and complete-phase nodes from `complete.yaml` after the DAG tail.

**Pros:** One script owns all dynamic injection. Clean anchor-based targeting.

**Cons:** `expand-plan` is now responsible for two unrelated concerns. Complete-phase nodes are only visible after `expand-plan` runs — the schema still doesn't declare them. Increases complexity of `expand_plan.py` without eliminating runtime surgery (just relocating it).

**Complexity:** M

### Approach 3: Hybrid — anchor for tasks, schema-static for complete steps

Add `execute-tasks` anchor to schemas for task injection. Add complete-phase steps statically to schema tails. Both concerns are addressed cleanly in their natural location.

**Pros:** Cleanest separation of concerns. Task injection stays in `expand_plan.py` (its natural domain). Complete-phase presence is schema-declared. Both are verifiable by reading the YAML files.

**Cons:** Same as Approach 1 for complete-phase (pending nodes visible during implement). More schema lines than Approach 2.

**Complexity:** S

### Selected Approach

**Approach 3 (Hybrid)** — complexity S, highest module reuse.

Approach 1 and 3 differ only in where `execute-tasks` anchor lives; Approach 3 makes both changes explicit in schemas. Approach 2 is complexity M with no advantage over static schema declaration for complete-phase steps.

Auto-selection: Approach 1 and 3 both have complexity S. Tie-break by module reuse: Approach 3 reuses `expand_plan.py`'s existing anchor-search pattern for `execute-tasks` (just changing the target anchor name from `run-phase-review` to `execute-tasks`). Approach 1 has no `expand_plan.py` changes. Approach 3 provides the cleaner dependency story (tasks explicitly anchor-wired, complete statically declared), so selected by the "schema readability" criterion from UC-3.

## High-Level Design

### Architecture Overview

```
feature.yaml / bugfix.yaml (schemas)
  │
  ├── steps: [..., expand-plan, execute-tasks, run-phase-review, ..., complete-phase-steps...]
  │                                ↑                      ↑
  │                    anchor for task injection    complete steps static
  │
  ↓ generate_plan.py (seed time)
  state.yaml workflow_plan[main].nodes
    ├── ... (pre-implement steps)
    ├── execute-tasks  (anchor, auto-completed inline)
    ├── run-phase-review
    ├── ... (ticket-review, compute-prediction-accuracy, run-learn-cycle, ...)
    └── archive-completed-change
         ← all visible from day 1 of the feature workflow

  ↓ expand_plan.py (after design-and-draft-artifacts)
  Injects task-T-* nodes under execute-tasks anchor
  Wires: task-T-N depends_on → task-T-(N-1); execute-tasks depends_on → [task-T-last]
  (no longer touches run-phase-review directly)

  ↓ orchestrator complete (CLI)
  pre-flight check: are all implement-phase + task nodes completed?
  → mark remaining implement nodes as completed, advance next_step
  → dispatch loop runs compute-prediction-accuracy through archive-completed-change
  (complete_phase.py deleted; guard becomes a simpler inline check in orchestrator-run.sh)
```

### Key Abstractions

- **`execute-tasks` anchor**: A named schema step that serves as the structural home for dynamically injected task-T-* nodes. It is an inline step (no agent) that auto-completes after all task-T-* children finish. Its `depends_on` is rewired by `expand_plan.py` to `[task-T-last]` after injection.
- **Schema-static complete-phase steps**: Steps from `complete.yaml` declared in `feature.yaml`/`bugfix.yaml` tails. Present in the DAG from seed time; unreachable until implement-phase chain completes.
- **Implement-completeness guard**: The check that `orchestrator complete` performs before marking nodes done. Moved from `complete_phase.py` into `orchestrator-run.sh` as a lightweight Python inline or dedicated script.

## Low-Level Design

### Components

**`config/workflows/feature.yaml`**
- Add `execute-tasks` between `expand-plan` and `run-phase-review`.
- Add complete-phase steps from `complete.yaml` after `ticket-qa` (or `run-learn-cycle` if ticket-qa is not present in all schemas): `mark-change-completed`, `compute-swe-metrics`, `gather-learn-metrics`, `cost-report`, `archive-completed-change`, `ticket-done`.

**`config/workflows/bugfix.yaml`**
- Same additions as `feature.yaml`.

**`orchestrator_next/expand_plan.py`**
- Change injection anchor from `run-phase-review` to `execute-tasks`.
- After injecting task nodes, rewire `execute-tasks.depends_on` to `[task-T-last]` instead of rewiring `run-phase-review.depends_on`.
- `run-phase-review.depends_on` is now declared in the schema (depends on `execute-tasks`) — no dynamic rewiring needed.
- Update module docstring (lines 8–10) to reference `execute-tasks` anchor.

**`config/steps/expand-plan/expand_plan.py`** (symlinked copy)
- Same changes as `orchestrator_next/expand_plan.py`.

**`orchestrator_next/scripts/orchestrator-run.sh`**
- Remove `python3 -m orchestrator_next.complete_phase` call (lines 289–294).
- Replace with lightweight implement-completeness guard: inline Python that reads `state.yaml` and confirms all non-complete-phase nodes are `completed` or `skipped` before marking them done and advancing `next_step`.

**`orchestrator_next/complete_phase.py`** (deleted after guard is moved)
**`orchestrator_next/tests/test_complete_phase.py`** (deleted with complete_phase.py)

**`config/workflows/complete.yaml`**
- Update comment on line 9 to reflect that complete-phase steps are declared in feature/bugfix schemas and seeded at feature init time, not injected at complete-time.

### Data Flow

1. `orchestrator feature orc-NNN` seeds `state.yaml` via `generate_plan.py`. DAG nodes include all schema steps including complete-phase tail. Status: all `pending`.
2. `expand-plan` step runs. `expand_plan.py` reads `tasks.yaml`, injects `task-T-*` nodes under `execute-tasks` anchor, rewires `execute-tasks.depends_on = [task-T-last]`.
3. Dispatcher walks DAG normally through implement steps → execute-tasks (anchor, inline) → run-phase-review → ticket-review → ticket-qa.
4. User runs `orchestrator complete orc-NNN`. `orchestrator-run.sh` calls implement-completeness guard script.
5. Guard reads `state.yaml`: confirms task-T-* and implement-phase nodes are all `completed`/`skipped`. Marks any remaining non-complete-phase pending nodes as `completed`. Advances `next_step` to first incomplete complete-phase node.
6. Dispatcher runs `compute-prediction-accuracy` → `run-learn-cycle` → `mark-change-completed` → ... → `archive-completed-change` → `ticket-done`.

### State Management

- `state.yaml` is written once at seed time with full DAG (including complete-phase tail).
- `expand_plan.py` writes `state.yaml` once after task injection (atomic write, idempotent).
- `orchestrator-run.sh` guard writes `state.yaml` once when `complete` is invoked (mark non-complete nodes as completed, set `next_step`).
- No other runtime mutations to node structure.

### Error Handling

- **UC-E1**: `tasks.yaml` not found — `expand_plan.py` raises `FileNotFoundError` as today; error message updated to reference `execute-tasks` anchor.
- **UC-E2**: `complete` invoked before implement finishes — guard detects pending task-T-* or implement-phase nodes and exits with error (same semantics as `complete_phase.py` today, simpler code path).
- **UC-E3**: Double-complete guard — `archive_completion probe` in `orchestrator-run.sh` remains unchanged; unaffected by this refactor.
- **UC-E4**: `expand-plan` idempotency — unchanged; anchor-based injection skips already-present nodes.

## Constraints

- Complete-phase steps cannot use a truly fresh empty seed — `run-learn-cycle` and `compute-prediction-accuracy` read `step_history`, `tasks.yaml`, and `design.md` from the feature's existing `state.yaml` artifact directory.
- `expand_plan.py` changes must remain backward-compatible with the schema step list shape (list of strings or dict entries).
- The `execute-tasks` anchor node needs a contract that auto-completes inline (no agent spawn needed).

## Trade-offs

- **Pending complete-phase nodes visible during implement**: Feature workflows will show complete-phase nodes as `pending` from day 1. These nodes are topologically unreachable (their `depends_on` chain isn't satisfied until implement completes), so the dispatcher will not pick them up prematurely. Trade-off: slightly noisier DAG graph; benefit: full schema transparency.
- **Implement-completeness guard complexity reduced**: Moving the guard from `complete_phase.py` (148 lines with injection logic) to a focused ~30-line inline or script eliminates the injection code while preserving the guard behavior.

## Acceptance Criteria

- AC-1: Given a feature workflow in `feature.yaml`, when a maintainer reads the YAML, then they see `execute-tasks` between `expand-plan` and `run-phase-review` AND complete-phase steps (`compute-prediction-accuracy` through `ticket-done`) after `ticket-qa`. [traces: UC-3]
- AC-2: Given `tasks.yaml` exists, when `expand-plan` runs, then task-T-* nodes are injected as children of the `execute-tasks` anchor (not positionally before `run-phase-review`), and `execute-tasks.depends_on` is rewired to `[task-T-last]`. [traces: UC-1, UC-E4]
- AC-3: Given a seeded feature workflow, when `orchestrator complete <slug>` is invoked after implement finishes, then complete-phase steps run using the feature's existing `state.yaml` (preserving `step_history` and artifact paths), without calling `complete_phase.py`. [traces: UC-2]
- AC-4: Given a feature with pending task-T-* nodes, when `orchestrator complete <slug>` is invoked, then the implement-completeness guard exits with a blocking error before advancing `next_step`. [traces: UC-E2]
- AC-5: Given `complete_phase.py` is deleted, when `pytest orchestrator_next/tests/` runs, then only `test_complete_phase.py` (also deleted) fails — all other test files pass green. [traces: UC-2, UC-3]
- AC-6: Given the refactored system, when `complete.yaml`'s comment is read, then it accurately describes the new model (complete steps declared in feature/bugfix schemas, seeded at feature init). [traces: UC-3]

## Decisions

- `execute-tasks` anchor is an inline step (no agent) that auto-completes when its `depends_on` chain is satisfied — mirror `capture-test-baseline` shape. → No new step contract needed; the dispatcher handles inline steps with `run:` or `main:`. → An empty/no-op script is the simplest implementation.
- Complete-phase steps declared statically in feature/bugfix schemas, not injected by expand-plan. → Keeps expand-plan's single responsibility (task injection). → Eliminates complete_phase.py's injection path.
- Implement-completeness guard moves to `orchestrator-run.sh` as a dedicated script or inline Python. → Keeps shell entry point self-contained. → Guard logic is ~30 lines vs. 148 in complete_phase.py.
- `run-phase-review.depends_on` is declared in schema as `[execute-tasks]` (or via implicit chaining if the engine supports it). → Removes the dynamic `run-phase-review` rewiring from expand_plan.py. → Verified: generate_plan.py supports explicit `depends_on` on schema step entries (dict form).

## Open Questions

- OQ-1 RESOLVED: `execute-tasks` anchor shape is an inline no-op step (mirror capture-test-baseline pattern). No agent dispatch.
- OQ-2 RESOLVED: Complete inherits the feature's existing `state.yaml` — not a fresh seed. Guard marks implement nodes as completed and advances `next_step`.
- OQ-3 RESOLVED: Implement-completeness guard moves to `orchestrator-run.sh` as a focused script (~30 lines), not to a seed validation step.
- OQ-4: Are there callers of `complete_step_ids_for_schema()` outside `complete_phase.py` and its tests? → Grep confirms: no. Safe to delete.
- OQ-5: Do `test_complete_workflow_e2e.py` and `test_complete_workflow.py` import `complete_phase.py` directly? → Grep confirms: no. They test the CLI subprocess path, which will survive the delete once `orchestrator-run.sh` is rewired. Only `test_complete_phase.py` imports it directly.
