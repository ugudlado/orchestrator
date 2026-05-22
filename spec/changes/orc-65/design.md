---
feature-id: ORC-65
linear-ticket: N/A
---

# Design: Task-DAG expansion — flat task-nodes via expand-plan

## Context

`execute-next-task` is a single opaque meta-step that loops via
`repeat_until: all_tasks_completed`. The developer agent internally scans
`tasks.md`, picks the next ready task, implements it, marks `[x]`, and loops
inside one spawn. The task queue is invisible to the orchestrator: `orchestrator
graph` shows one recurring step, telemetry (`step_history`, DuckDB) is keyed by
step not task, parallelism is impossible, and the rework loop on
`needs_work` reaches deep into `record.py` to reset the step to `in_progress`.

After ORC-63 the workflow plan is a flat DAG of nodes in
`state.yaml.workflow_plan[phase].nodes`. The dispatcher (`dispatch.py`) and
readiness module (`readiness.py`) already walk this DAG one node at a time. The
plumbing exists; we just need to expose tasks as nodes in the same plan.

Existing system boundaries:
- `dispatch.py` returns `action.step_context = node` (the full node dict) on
  each spawn (`_node_step_context`, dispatch.py:159-169). Anything stored on a
  node is delivered to the spawned agent — no new plumbing required.
- `readiness.next_ready_node` selects nodes in declaration order with
  topo-correctness. Cycle detection lives in `generate_plan._topo_sort`.
- `record.py` writes node status via `readiness.mark_node_status` and persists
  `step_history` entries keyed by `step_id` (which is the node id).
- `needs_work` rework branch (record.py:1391-1411) resets
  `execute-next-task` to `in_progress`. This branch must be replaced.

## Goals / Non-Goals

### Goals

- Replace the opaque `execute-next-task` meta-step with one DAG node per task,
  appended to `workflow_plan[implement].nodes` as flat top-level nodes.
- Authoring shifts from `tasks.md` (markdown checkboxes) to `tasks.yaml`
  (machine-readable with explicit `id` and `depends_on`).
- Introduce `orchestrator expand-plan <state.yaml>` — an idempotent CLI verb
  that reads `tasks.yaml` and appends task-nodes to the plan.
- One developer spawn per task. `execute-one-task` is a ~40-line contract; no
  scheduling logic in the agent.
- `needs_work` rework appends fix-tasks via `expand-plan` instead of resetting
  a step to `in_progress` — one node-injection mechanism, not two.
- Per-task entries in `step_history` and DuckDB; `orchestrator graph` shows
  one node per task.
- Resume works without changes: `orchestrator next` reads existing node
  statuses; `expand-plan` is append-if-absent and never mutates completed
  nodes.
- Staged delivery — five compounding stages, each producing a coherent
  workflow. No flag-day rewrite.

### Non-Goals

- Driver-side concurrent spawns. ORC-65 exposes the ready frontier; whether
  the driver spawns concurrently is a follow-on concern.
- Changes to the top-level phase order in workflow schemas (we add
  `expand-plan` and replace `execute-next-task`; phases themselves are
  unchanged).
- Changes to per-node `repeat_until` plumbing in `readiness.py` (other steps
  use it). Only the `all_tasks_completed` predicate and its caller-site code
  paths are removed.
- Re-architecting `run-phase-review` itself. We change one branch of its
  outcome handler (`retry` action keeps current behavior; `needs_work` switches
  from in-place reset to `expand-plan`-driven injection).
- A pre-flight CLI to validate `tasks.yaml` outside of `expand-plan`. The
  expand-plan invocation IS the validator (topo-sort raises on cycles or
  unknown ids).

## Approaches Considered

### Approach 1: Step-as-sub-DAG (the original discovery model)

Nested sub-DAG hanging off `execute-next-task.sub_dag.nodes[]`. Requires a
second walker beside `next_ready_node`, a separate readiness check for
sub-nodes, a Mermaid renderer that descends into sub-DAGs, and special-casing
in `_detect_boundary`. The parent step becomes a placeholder whose status is
synthesized from sub-node statuses.

- **Pros**: leaves `workflow_plan` schema untouched.
- **Cons**: two walkers, two readiness paths, two id schemes, and a deeply
  invasive change to `record.py`'s boundary detection. Inconsistent surface
  between "real" nodes and task-nodes. Tests double. Resume tricky because
  sub-DAG state lives in a different shape.

### Approach 2: Sidecar `task-dag.yaml` file with separate dispatcher

A second file alongside `state.yaml` carrying the task DAG. A second
`dispatch_tasks.py` walker that runs inside `execute-next-task`.

- **Pros**: keeps state.yaml structurally identical.
- **Cons**: two walkers, two state files, two retry contracts, two
  reconcile paths against DuckDB. Resume must rebuild from two files
  in lockstep. No structural benefit over Approach 1 — worse, in fact, because
  the data lives outside `state.yaml`.

### Approach 3: Flat task-nodes appended via `expand-plan` (SELECTED)

Each task is a top-level node in `workflow_plan[implement].nodes`,
indistinguishable from `run-phase-review` or any other node. A new CLI verb
`orchestrator expand-plan <state.yaml>` reads `tasks.yaml`, builds nodes, and
appends them to the plan. The existing dispatcher walks them; the existing
record path closes them; the existing graph renderer displays them.

- **Pros**: one walker, one readiness path, one node-status mutator, one
  Mermaid renderer, one resume story. The dispatcher does not know task-nodes
  are special. Topo-sort is reused. `_detect_boundary` works unchanged: the
  last task-node is the predecessor of `run-phase-review`, which remains the
  last gating node before terminal steps.
- **Cons**: `workflow_plan[implement].nodes` becomes data-driven, not schema-
  driven. The plan grows after `generate_plan` runs — but this is already true
  for the rework loop today (which mutates node status from record.py). The
  expansion path makes it explicit.

### Selected Approach

**Approach 3.** The constraints that rule out 1 and 2 are: (a) we already
have a working DAG walker, readiness checker, status mutator, and renderer
operating on one shape, and they cost real maintenance; (b) the rework loop
already mutates the plan from outside `generate_plan`, so the "plan is
frozen post-generate" invariant is already gone in practice. Approach 3
collapses task scheduling into the existing machinery. The discovery brief's
AC-6 ("don't mutate workflow_plan") was a constraint inherited from the
sub-DAG framing — once you accept that node injection is the mechanism, the
constraint is the wrong shape and Approach 3 is the simplest design.

## High-Level Design

### Architecture Overview

```
                  generate_plan
                       │
                       ▼
            workflow_plan[implement].nodes:
              [design-and-draft-artifacts,
               expand-plan,              ← new run: node
               run-phase-review, ...]
                       │
                       ▼ (dispatcher walks)
            design-and-draft-artifacts
              writes design.md, tasks.yaml, tasks.md
                       │
                       ▼
            expand-plan (CLI verb, idempotent)
              reads tasks.yaml
              appends task-T-1, task-T-2, ... to nodes
              rewires run-phase-review.depends_on = [last task-node]
                       │
                       ▼ (dispatcher walks again)
            task-T-1 → execute-one-task → developer spawn
            task-T-2 → execute-one-task → developer spawn
            ...
                       │
                       ▼
            run-phase-review
              verdict: pass    → continue
              verdict: needs_work → append fix tasks to tasks.yaml
                                   → invoke expand-plan
                                   → run-phase-review left pending
                                   → dispatcher schedules fix tasks
```

### Key Abstractions

- **`tasks.yaml`** — architect-authored task list. The source of truth for
  what the implement phase does. Schema below under State Management.
- **`orchestrator expand-plan <state.yaml>`** — pure function:
  `read(tasks.yaml) + read(state.yaml) → write(state.yaml)`. Idempotent:
  appends nodes whose id is not already present; never mutates completed
  nodes; rewrites `run-phase-review.depends_on` to the current task-node tail.
- **`execute-one-task`** — single-task step contract. The contract's
  `step_context.task` carries the full task payload. The agent implements
  ONE task and returns COMPLETION.
- **Task-node** — a flat node in `workflow_plan[implement].nodes` with
  `agent: developer`, `step_contract: execute-one-task`, and a `task:` block
  containing the full task payload.

## Low-Level Design

### Components

| Component | Responsibility | Inputs | Outputs |
|-----------|----------------|--------|---------|
| `design-and-draft-artifacts` (Stage 1) | Architect emits `tasks.yaml` and `tasks.md` | `design.md` direction | `tasks.yaml`, `tasks.md` |
| `orchestrator expand-plan` (Stage 2) | Read `tasks.yaml`, append task-nodes to plan | `tasks.yaml`, `state.yaml` | `state.yaml` (mutated) |
| `execute-one-task` (Stage 3) | One developer spawn implements one task | `step_context.task` | task verification + commit |
| `run-phase-review` `needs_work` branch (Stage 3) | Append fix tasks to `tasks.yaml`, invoke `expand-plan` | `phase-review.md`, `tasks.yaml` | `tasks.yaml` (mutated), `state.yaml` (mutated via expand-plan) |
| Per-task DuckDB telemetry (Stage 4) | `step_events` rows keyed by `task-T-N` node id | `step_history` | metrics rows |
| `tasks.md` deletion (Stage 5) | Remove the markdown artifact and all references | — | cleanup |

### Data Flow

1. **Architect pass** — `design-and-draft-artifacts` writes `design.md`,
   `tasks.yaml`, `tasks.md` (Stage 1 keeps both files; Stage 5 deletes
   `tasks.md`).
2. **Plan expansion** — `expand-plan` runs as the next node. It reads
   `tasks.yaml`, constructs a node per task with the appropriate
   `agent: developer` and `step_contract: execute-one-task`, appends them to
   `workflow_plan[implement].nodes` (skipping ids already present), runs
   topo-sort over the full plan (raises on cycles), and rewrites
   `run-phase-review.depends_on` to `[last_task_node_id]`.
3. **Task execution** — dispatcher selects each ready task-node; agent
   spawns; `execute-one-task` reads `step_context.task` and implements;
   COMPLETION records a per-task `step_history` entry.
4. **Phase review** — `run-phase-review` runs after the last task-node.
   On `needs_work`, the step appends fix tasks to `tasks.yaml` (new entries
   with `id: fix-N`, `depends_on: [previous_last_task]`), then calls
   `orchestrator expand-plan` as a subprocess. The dispatcher's next call
   picks up the new fix-task nodes. `run-phase-review` itself is left in
   `pending` (its node-status is reset from `completed` to `pending` along
   with the rewired `depends_on`).
5. **Resume** — interrupted runs: `orchestrator next` reads node statuses
   from state.yaml. `expand-plan` re-running is a no-op (all nodes present).

### State Management

#### `tasks.yaml` schema

```yaml
# tasks.yaml — written by design-and-draft-artifacts (architect)
version: 1
tasks:
  - id: T-1
    title: "Wire X to Y"
    agent: developer            # node.agent — informs spawn
    depends_on: []              # depends_on other task ids; first task may omit
    files:                      # files the task is allowed to touch
      - path/to/file.py
    verify:                     # commands the developer runs before COMPLETION
      - pytest tests/test_x.py::test_wire
    test_scenarios:             # human-readable cases the developer's tests must cover
      - "Y observes X's emission"
    # optional fields:
    why: "AC-3"                 # which design AC this task serves
    change: "edit file.py:42 to call y_emit() instead of y_set()"
  - id: T-2
    title: "Add regression test"
    depends_on: [T-1]
    files:
      - tests/test_x.py
    verify:
      - pytest tests/test_x.py
```

Validation: `expand-plan` rejects (a) duplicate ids, (b) unknown ids in
`depends_on`, (c) cycles (via reused topo-sort), (d) missing required
fields (`id`, `title`, `files`, `verify`).

#### Task-node shape (appended to `workflow_plan[implement].nodes`)

```yaml
- id: task-T-1
  status: pending
  agent: developer
  goal: "Wire X to Y"
  inputs: []
  outputs: [task_execution_result]
  rules: [...]                  # merged from execute-one-task contract
  depends_on: []                # mapped from task.depends_on (T-N → task-T-N)
  task:                         # the payload — delivered via step_context
    id: T-1
    title: "Wire X to Y"
    files: [path/to/file.py]
    verify: [pytest tests/test_x.py::test_wire]
    test_scenarios: ["..."]
    why: "AC-3"
    change: "edit file.py:42 to call y_emit() instead of y_set()"
```

Node id convention: `task-<task_id>` (e.g., `task-T-1`, `task-fix-1`). The
`task-` prefix prevents collision with any future schema-level node named
`T-1`. `_safe_id` in graph.py already handles `-` cleanly.

#### `run-phase-review` rewiring

After every `expand-plan` invocation:
```python
plan["run-phase-review"]["depends_on"] = [last_task_node_id]
```
If `last_task_node_id == "run-phase-review"` (no tasks), `expand-plan` is a
no-op for the `depends_on` edge. This is the only case where rewiring is
skipped.

### Control Flow

#### `expand-plan` algorithm

```
load state.yaml
load tasks.yaml (path resolved via state.worktree_artifact_dir/tasks.yaml)
existing_ids = { n.id for n in plan[implement].nodes }
for task in tasks.yaml:
    node_id = f"task-{task.id}"
    if node_id in existing_ids:
        continue                          # idempotent
    node = build_task_node(task)
    plan[implement].nodes.append(node)
# rewire run-phase-review edge
task_node_ids = [n.id for n in plan[implement].nodes if n.id.startswith("task-")]
if task_node_ids:
    rpr = find_node(plan[implement].nodes, "run-phase-review")
    rpr.depends_on = [task_node_ids[-1]]
# reuse generate_plan._topo_sort for cycle/unknown-id validation
_topo_sort(plan[implement].nodes, plan[implement].filtered or set())
write state.yaml (atomic via pre-write byte buffer, mirroring record.py)
```

#### `needs_work` branch (Stage 3)

Replace record.py:1411 `readiness.mark_node_status(state_raw, phase,
"execute-next-task", "in_progress")` with: the rework branch is moved OUT of
`record.py` into the `run-phase-review` agent's COMPLETION handling. The
agent, on `needs_work`, before returning COMPLETION:

1. Reads `phase-review.md` for the gap list.
2. Appends fix-tasks to `tasks.yaml` (ids `fix-1`, `fix-2`, ...; depends_on
   the previous final task-node).
3. Runs `orchestrator expand-plan $STATE_YAML` as a subprocess.
4. Returns COMPLETION with verdict `needs_work` and the fix-task ids.

`record.py` on receiving `needs_work`: reset the `run-phase-review` node
status to `pending` (so the dispatcher schedules it again after the fix
tasks), but do NOT touch any other node. The fix tasks are already present
in the plan via step (3); their `depends_on` chain ensures they run first,
and run-phase-review's rewired `depends_on` ensures it runs after them.

This eliminates the special-case reset code and unifies task injection
under one mechanism (`expand-plan`).

### Error Handling

| Failure | Detection | Response |
|---------|-----------|----------|
| Cycle in `tasks.yaml` | `_topo_sort` raises ValueError | `expand-plan` exits non-zero; node stays `in_progress`; user fixes `tasks.yaml` and reruns |
| Unknown `depends_on` id | `_topo_sort` raises ValueError | same as above |
| Missing required field in task | Schema check in `expand-plan` | `expand-plan` exits non-zero with field name and task id |
| `tasks.yaml` not found | Path resolution in `expand-plan` | exit non-zero with hint pointing to architect step |
| Task verification failure during developer spawn | Existing error-recovery path | unchanged — node enters retry per `error-recovery.md` |
| `needs_work` infinite loop | Existing global retry cap | Stage 3 inherits the existing `retries` ceiling logic; no new ceiling added |
| `expand-plan` rerun after partial completion | `existing_ids` membership check | append-if-absent; completed nodes untouched |

## Constraints

- The dispatcher API (`dispatch.dispatch`, `readiness.next_ready_node`,
  `readiness.mark_node_status`) must NOT change shape. Stages 2-3 only add
  callers and add node-shape fields (`task:`); they do not modify the
  walker contract.
- `record.py`'s `_check_all_tasks_completed` and the `all_tasks_completed`
  predicate are removed in Stage 3 (after `execute-next-task` is gone).
  Per-node `repeat_until` plumbing in `readiness.py` stays — other steps may
  still declare repeat predicates.
- Staged delivery: every stage must leave the workflow runnable. Stage 1 is
  additive only (`tasks.yaml` emitted but unread). Stage 5 is the only stage
  that deletes `tasks.md`.
- ORC-66 (one-spawn-per-task) is implicitly Stage 3; `execute-one-task`
  replaces both `execute-next-task` and the loop inside it.

## Trade-offs

- **Plan grows after `generate_plan` runs.** Today, `generate_plan` is the
  sole writer of `workflow_plan.nodes`; after ORC-65, `expand-plan` and
  `run-phase-review` (via expand-plan) also append nodes. Acceptable: the
  rework loop already mutates the plan from outside `generate_plan` (status
  resets), and the append-if-absent rule prevents drift.
- **`tasks.yaml` overlaps with `tasks.md` for two stages.** Stage 1 emits
  both. The architect is the single writer of both, so drift risk is low.
  Stage 5 deletes `tasks.md` after telemetry migration (Stage 4) is proven.
- **`run-phase-review` agent now writes to `tasks.yaml`.** The review step
  becomes a writer in addition to a reader. Acceptable: it was already a
  writer (it appended fix tasks to `tasks.md` in the old model — see
  run-phase-review.yaml:73).
- **No driver-side parallelism in this change.** The ready frontier exposes
  parallelism (multiple ready task-nodes), but the orchestrate skill spawns
  serially. Follow-on.

## Acceptance Criteria

### Stage 1 — Architect emits `tasks.yaml`

- AC-1: `design-and-draft-artifacts` writes `tasks.yaml` to
  `$WORKTREE_ARTIFACT_DIR/<change>/tasks.yaml` alongside `tasks.md` for every
  feature/bugfix workflow. [traces: UC-1]
  Verify: `python -c "import yaml; d=yaml.safe_load(open('tasks.yaml')); assert d['version']==1 and d['tasks'] and all('id' in t for t in d['tasks'])"`
- AC-2: `tasks.yaml` schema validates: each task has `id`, `title`, `files`,
  `verify`; `depends_on` references existing task ids; no duplicate ids.
  [traces: UC-1, UC-E1]
  Verify: a script `config/scripts/inline/validate-tasks-yaml.sh` exits 0 on
  the architect's output and exits non-zero on a synthetic cycle file.

### Stage 2 — `expand-plan` CLI verb (unwired)

- AC-3: `orchestrator expand-plan <state.yaml>` reads `tasks.yaml` from the
  worktree artifact dir and appends a flat task-node per task to
  `workflow_plan[implement].nodes`. [traces: UC-1, UC-3]
  Verify: integration test seeds a state.yaml with no task-nodes, places a
  `tasks.yaml`, invokes `expand-plan`, and asserts `task-T-1`, `task-T-2`
  appear in the plan with correct `depends_on` and `task:` payloads.
- AC-4: `expand-plan` is idempotent — second invocation appends nothing and
  exits 0. [traces: UC-3]
  Verify: integration test runs `expand-plan` twice, diff of state.yaml
  between runs is empty.
- AC-5: `expand-plan` rejects a `tasks.yaml` with a dependency cycle and
  leaves state.yaml unchanged. [traces: UC-E1]
  Verify: integration test with `T-1.depends_on=[T-2]; T-2.depends_on=[T-1]`;
  expand-plan exits non-zero; `git diff state.yaml` is empty.
- AC-6: `expand-plan` rejects an unknown id in `depends_on` and leaves
  state.yaml unchanged. [traces: UC-E2]
  Verify: integration test with `T-1.depends_on=[T-99]`; expand-plan exits
  non-zero; `git diff state.yaml` is empty.
- AC-7: `expand-plan` rewires `run-phase-review.depends_on` to the last
  task-node id. [traces: UC-1]
  Verify: integration test asserts after `expand-plan` that
  `workflow_plan[implement].nodes[<run-phase-review>].depends_on == ["task-T-N"]`
  where N is the highest task id.

### Stage 3 — Wire `expand-plan`, replace `execute-next-task`

- AC-8: `feature.yaml`, `bugfix.yaml`, `spike.yaml` schemas list
  `expand-plan` immediately after `design-and-draft-artifacts` and replace
  `execute-next-task` with no entry (task-nodes carry the work).
  [traces: UC-1]
  Verify: `grep -E '^\s*-\s*(execute-next-task|expand-plan)' config/workflows/feature.yaml`
  shows `expand-plan` present and `execute-next-task` absent.
- AC-9: `execute-one-task` step contract exists, declares `agent: developer`,
  has no `repeat_until`, and the instruction tells the agent to read
  `step_context.task` and implement that one task. [traces: UC-1]
  Verify: `wc -l config/steps/execute-one-task.yaml` shows < 80 lines;
  `grep -c repeat_until config/steps/execute-one-task.yaml` is 0;
  `grep step_context.task config/steps/execute-one-task.yaml` matches.
- AC-10: `execute-next-task.yaml`, `repeat_until: all_tasks_completed`,
  `_check_all_tasks_completed`, and `all_tasks_completed` in REPEAT_PREDICATES
  are all removed. [traces: UC-1]
  Verify: `grep -r all_tasks_completed config/` produces no matches.
- AC-11: One task-node per task — the dispatcher returns each task-node
  individually and `step_history` records one terminal entry per task.
  [traces: UC-2, UC-4]
  Verify: end-to-end test with a 3-task `tasks.yaml`; after run,
  `step_history` contains three entries with `step_id ∈ {task-T-1, task-T-2,
  task-T-3}`, each `status: completed`.
- AC-12: Resume after a task is interrupted picks up at the next ready
  task-node, not re-running completed task-nodes. [traces: UC-3]
  Verify: integration test marks `task-T-1` completed in state.yaml, runs
  `orchestrator next`, asserts the returned action's `step_id == task-T-2`.
- AC-13: `run-phase-review` `needs_work` branch appends fix tasks to
  `tasks.yaml`, calls `expand-plan`, and resets the review node to
  `pending`; `record.py` no longer special-cases `execute-next-task`.
  [traces: UC-1]
  Verify: integration test simulates `needs_work` COMPLETION; assert
  `tasks.yaml` has new `fix-N` entries, plan has new `task-fix-N` nodes,
  run-phase-review node status is `pending`, and `grep execute-next-task
  config/scripts/orchestrator_next/record.py` is empty.
- AC-14: `orchestrator graph` renders each task-node as a discrete node in
  Mermaid output. [traces: UC-4]
  Verify: integration test runs `orchestrator graph` on a 3-task plan;
  output contains `task_T_1`, `task_T_2`, `task_T_3` as Mermaid identifiers.

### Stage 4 — Per-task telemetry

- AC-15: DuckDB `step_events` table receives one row per task-node with
  `step_id` set to the task-node id (e.g., `task-T-1`). [traces: UC-1]
  Verify: integration test with a 3-task feature; after run,
  `duckdb metrics.duckdb "SELECT step_id FROM step_events WHERE change_id='<id>' AND step_id LIKE 'task-%'"`
  returns three rows.
- AC-16: `compute_resolution` reads `tasks_completed` and `tasks_total` from
  `step_history` (per-task entries) instead of parsing `tasks.md` checkbox
  counts. [traces: UC-1]
  Verify: unit test seeds `step_history` with two `task-T-N` completed
  entries and one `failed`; assert `compute_resolution` returns
  `tasks_completed=2, tasks_total=3`.

### Stage 5 — Delete `tasks.md`

- AC-17: `tasks.md` is no longer emitted by `design-and-draft-artifacts`;
  the Task Format Contract in `artifact-formats.md` is removed; all
  `inputs:` references to `tasks.md` across step contracts are removed.
  [traces: UC-1]
  Verify: `grep -r tasks.md config/` returns matches only in archived
  features under `spec/changes/archive/` (or zero matches if archive is
  excluded).

## Decisions

- **Task payload on the node** → delivered via `step_context.task` →
  zero new plumbing in dispatch.py; the developer reads the node it was
  spawned for.
- **`needs_work` writes `tasks.yaml`, invokes `expand-plan`** → one
  injection mechanism for all task additions (initial + rework) → simpler
  `record.py`, easier to reason about plan growth.
- **Node id `task-T-N` (dash, no slash)** → `_safe_id` already passes
  through dashes → no Mermaid escape changes, no readiness lookup changes.
- **`expand-plan` is a `run:` node, not an agent step** → idempotent
  mechanical work, no LLM cost, no nondeterminism → fits the
  `config/scripts/inline/` model already used for `seed-state.sh`,
  `append-retro.sh`.
- **Staged delivery (5 stages) rather than one big change** → each stage
  is independently revertable → reduces blast radius of any regression.

## Open Questions

(None — all OQs from discovery are resolved under the flat-nodes model. See
discovery.md § Open-question resolutions.)
