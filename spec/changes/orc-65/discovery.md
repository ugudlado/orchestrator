---
feature-id: ORC-65
linear-ticket: N/A
---

# Discovery Brief: Task-DAG expansion — replace execute-next-task with per-task DAG nodes (step-as-sub-DAG model)

## Feature Summary

`execute-next-task` is currently a single opaque meta-step that loops via `repeat_until: all_tasks_completed` while the developer agent internally schedules tasks from `tasks.md`. This makes the task queue invisible to the orchestrator: `orchestrator graph` shows one recurring step, not the N tasks inside, telemetry is keyed by step-not-task, and parallelism is impossible. ORC-65 replaces this with a step-as-sub-DAG model: when `execute-next-task` is dispatched, it generates a nested task graph from `tasks.md` (one node per task, edges from the dependency graph), and the orchestrator schedules those task-nodes individually — one developer spawn per task, natural parallelism for independent tasks, per-task telemetry in `step_history`, and task-level visibility in `orchestrator graph`.

## Personas & Actors

- **Orchestrator engine** (dispatch.py, record.py, readiness.py) — selects, tracks, and schedules task-nodes from the sub-DAG.
- **Developer agent** — receives a single task per spawn and implements it, no longer owns task scheduling logic.
- **Architect agent** (design-and-draft-artifacts step) — produces `tasks.md` in the new machine-readable format with explicit `id` and `depends_on` per task.
- **Workflow operator / user** — observes per-task progress via `orchestrator graph` and `orchestrator ready`; resumes interrupted workflows mid-task-queue.

## Use Cases

### Happy Path

UC-1: Standard task execution — the orchestrator wants to schedule all tasks from a freshly generated `tasks.md` so that each task gets its own developer spawn with deterministic ordering based on declared dependencies.

UC-2: Concurrent task dispatch — the orchestrator wants to identify and spawn multiple independent task-nodes simultaneously so that wall-clock time is reduced when tasks have no shared dependencies.

UC-3: Resume mid-queue — an operator wants to resume a workflow interrupted after some tasks completed so that already-completed task-nodes are not re-dispatched and the remaining frontier is picked up correctly.

UC-4: Graph visibility — a developer wants to run `orchestrator graph` during the implement phase so that they can see which task-nodes are pending, in-progress, or completed at any point in the queue.

### Error & Edge Cases

UC-E1: Cyclic task graph — what happens when `tasks.md` declares a dependency cycle (e.g., T-1 depends: T-2, T-2 depends: T-1); sub-DAG generation must fail with a clear error before any task-node is dispatched.

UC-E2: Missing `depends_on` fields — what happens when `tasks.md` is in the old prose format without machine-readable `id`/`depends_on`; the sub-DAG generator cannot parse dependencies and must fail with a helpful error pointing to the artifact format contract.

UC-E3: Task verification failure — what happens when a developer spawn for task-node T-N fails verification; the task-node should be retried or quarantined per existing error-recovery contract without affecting completed sibling nodes.

## Scope

### In Scope

- New machine-readable `tasks.md` format: each task has explicit `id` and `depends_on` (replacing prose `depends: T-N`); `artifact-formats.md` Task Format Contract updated.
- `design-and-draft-artifacts` step and architect agent updated to emit `tasks.md` in the new format.
- Sub-DAG generation from `tasks.md` at `execute-next-task` dispatch time: one namespaced node (`execute-next-task/T-N`) per task, edges from the dependency graph.
- Cycle detection at sub-DAG generation time (reusing `_topo_sort` from `generate_plan.py`).
- Dispatcher schedules task-nodes individually; `orchestrator ready` returns all satisfied task-nodes in the current frontier.
- One developer spawn per task node (the orchestrate driver's existing loop already spawns one agent per `next` returning agent-action JSON — no driver changes may be needed).
- Per-task `step_history` entries keyed by namespaced node id; `orchestrator graph` renders task sub-DAG when present.
- `repeat_until: all_tasks_completed` removed from `execute-next-task` step contract.
- Resume: re-reading the sub-DAG from its durable storage location and continuing from the ready frontier.

### Out of Scope

- Changes to the top-level `workflow_plan` structure in `state.yaml` — AC-6 explicitly forbids mutating it at dispatch time; the sub-DAG lives separately.
- Changes to other workflow steps beyond `execute-next-task` and `design-and-draft-artifacts`.
- Driver-side parallelism implementation (concurrent spawns) — the model enables it, but whether to actually spawn concurrently is a driver concern; ORC-65 exposes the ready frontier, not the concurrency mechanism.
- Phase-review rework loop semantics — the interaction between `run-phase-review` needs_work verdict and the sub-DAG (record.py:1597-1617 resets `execute-next-task` to `in_progress`) is a follow-on concern.
- ORC-64's "two-stage generate_plan" — that framing is eliminated; the sub-DAG model dissolves the problem it was solving.

## UI Direction

N/A — no UI components. Internal engine refactor; `orchestrator graph` output will change to include task-level nodes.

## Key Decisions

- **Model**: flat task-nodes in `workflow_plan[implement].nodes` — NOT a nested sub-DAG. Task-nodes are appended directly alongside other plan nodes and are indistinguishable from any other node to the dispatcher. One unified DAG, walked by the existing `orchestrator next`. Supersedes the step-as-sub-DAG framing in this brief.
- **Task artifact**: `tasks.yaml` (architect-authored) is the source of truth — each task has `id`, `title`, `agent`, `depends_on`, `files`, `verify`. `tasks.md` continues to be emitted in Stage 1 (additive, zero behavior change) and is deleted in Stage 5.
- **`expand-plan` CLI verb**: reads `tasks.yaml`, appends flat task-nodes to `workflow_plan[implement].nodes` (idempotent, append-if-absent, never touches completed nodes). Reuses existing topo-sort for cycle detection. Wired as a `run:` node after `design-and-draft-artifacts` in the feature/bugfix/spike schemas.
- **`execute-one-task`**: single-task contract (~40 lines), one developer spawn per task-node. Replaces `execute-next-task`. The full task payload is delivered on the node itself (`node.task: {id, files, verify, ...}`) and surfaced via `action.step_context` — dispatch.py already returns the full node dict; no new plumbing.
- **`repeat_until: all_tasks_completed` deleted**: removed from `record.py` (`REPEAT_PREDICATES`, `_check_all_tasks_completed`, `_repeat_until_pending`) and `readiness.repeat_until_redispatch`. Per-node `repeat_until` plumbing in readiness.py remains (other steps still use it; only the `all_tasks_completed` predicate is removed).
- **`needs_work` → node injection**: `run-phase-review` rework branch appends fix-tasks to `tasks.yaml` and re-invokes `orchestrator expand-plan` (subprocess) instead of resetting `execute-next-task` to `in_progress`. Single code path for all task injection. `run-phase-review`'s own `in_progress` reset for the `retry` action is unchanged.
- **Node id scheme**: flat ids `task-T-1`, `task-T-2`, `task-fix-1`. No `/` separator. `_safe_id` already handles `-` cleanly.
- **`run-phase-review` placement**: depends_on the last task-node. `expand-plan` rewrites this edge on each invocation so newly-appended fix tasks gate the review on rerun.
- **Resume**: `orchestrator next` reads existing node statuses unchanged. `expand-plan` is idempotent — appends only missing nodes.

### Open-question resolutions (this model)

- OQ-1 (sub-DAG location): **resolved** — no sub-DAG. Flat nodes in `workflow_plan[implement].nodes`.
- OQ-2 (`_detect_boundary`): **in scope** — the last task-node is just another node; `run-phase-review` remains the last gating node before terminal steps. No special-casing needed.
- OQ-3 (rework loop): **in scope (Stage 3)** — replaced by `expand-plan`-driven node injection.
- OQ-4 (node id scheme): **resolved** — `task-T-N`, dash-separated, `_safe_id` already safe.
- OQ-5 (developer agent instruction): **in scope (Stage 3)** — `execute-one-task` is single-task; scheduling logic deleted.
- OQ-6 (telemetry): **Stage 4** — per-task `step_history` entries become the ground truth; `parse_tasks`/`compute_resolution` updated to count from `step_history` (not `tasks.md`).

## Open Questions

- OQ-1: Where does the sub-DAG physically live? AC-6 forbids mutating `state.yaml.workflow_plan`. The three options are: (a) nested under the `execute-next-task` node as `node.sub_dag.nodes[]` — durable in `state.yaml` without touching `workflow_plan`; (b) a separate sidecar file (e.g., `task-dag.yaml` in the artifact dir) — decoupled but another file to manage on resume; (c) in-memory re-derivation per `next` call. Option (c) is ruled out: AC-5 requires per-task `step_history` entries (stable node ids) and AC-8 requires resume to read expanded nodes. The architect must choose between (a) and (b).

- OQ-2: How does `_detect_boundary` / `_phase_node_ids` (record.py:241-284) behave when task-nodes are namespaced `execute-next-task/T-N`? Both functions compare `step_id == node_ids[-1]` to decide phase/feature boundary. If the parent `execute-next-task` is no longer a real dispatched step, does the last task-node count as the final node in the phase, or does something else signal phase completion?

- OQ-3: How does the rework loop (record.py:1605-1617) interact with the sub-DAG after ORC-65? Today it resets `execute-next-task` to `in_progress` so the DAG re-emits it for fix tasks. After ORC-65, "drain fix tasks" means adding new task-nodes to the sub-DAG (appended as new nodes at the tail of the task graph). The reset-to-in_progress mechanism must be adapted or replaced.

- OQ-4: What is the namespaced node id scheme? The ticket suggests `execute-next-task/<task-id>` (e.g., `execute-next-task/T-1`). Does `_safe_id` in `graph.py` handle the `/` separator cleanly in Mermaid output? Does `readiness.is_node_ready` / `mark_node_status` work with namespaced ids, or does the node lookup need a separator-aware variant?

- OQ-5: Does the developer agent's instruction need to change? Currently `execute-next-task.yaml` instructs the developer to scan tasks.md, pick the next ready task, implement it, and loop. After ORC-65, one spawn = one task — the agent receives a single task node, implements it, and returns COMPLETION. The step contract `instruction:` field and the `developer.md` agent file both need updating to remove task-scheduling logic.

- OQ-6: How does per-task telemetry integrate with `parse_tasks` (record.py:803-821) and `compute_resolution`? These functions count checkbox states from `tasks.md`. With per-task `step_history` entries, `compute_resolution`'s `pass_at_1` approximation can become exact per-task retry accounting. Is that an in-scope change or follow-on?

<!-- Empty section means no blockers. -->
<!-- Format contract: contracts/artifact-formats.md § Discovery Brief Format Contract -->
