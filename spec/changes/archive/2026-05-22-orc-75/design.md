---
feature-id: orc-75
linear-ticket: none
---

# Design: Fix run-learn-cycle step contract mismatch + abandoned re-dispatch loop

## Context

`run-learn-cycle.yaml` declares `agent: workflow-improver` but its instruction tells
the driver to invoke the `/learn` skill inline (`Skill({ skill: "learn", ... })`).
`/learn` runs in the driver's conversation context — it cannot run as a spawned
sub-agent because it itself spawns sub-agents (workflow-evaluator, workflow-improver)
and a sub-agent cannot spawn sub-agents. The `agent:` declaration is therefore a
lie about how the step executes.

This produces a three-way deadlock at `orchestrator done` time (see `diagnose.md`):
- `status: completed` → `record.py` rejects with `agent_step_missing_usage` (exit 3) —
  no subagent JSONL, no usage tokens.
- `status: blocked` → not a valid done-payload status (exit 3).
- `status: abandoned` → accepted, but the `run-learn-cycle` DAG node is left
  `in_progress`, and `readiness.is_node_ready` re-emits it forever → infinite loop.

The `abandoned`-leaves-node-`in_progress` behavior is a **separate latent bug** in
`record.py`: it affects any step that records `abandoned` while its node is
`in_progress`, not just `run-learn-cycle`.

Existing system boundaries:
- `dispatch.py` has two dispatch paths: `agent:` → spawn sub-agent; `run:` → CLI
  executes a shell script synchronously (exit 0, no JSON).
- `record.py` already treats `agent == "inline"` as exempt from the usage guard
  (lines 1399, 1420 — both gated on `!= "inline"`).
- `skills/orchestrate/SKILL.md` is the driver protocol: it handles spawn actions
  (JSON with `agent`) and inline-script completions (exit 0, no JSON).

## Goals / Non-Goals

### Goals

- `run-learn-cycle` completes cleanly through `orchestrator done` so every workflow's
  complete phase finishes without manual intervention.
- The step contract honestly declares how it executes (real spawnable agent).
- Any step recording `status: abandoned` terminates its DAG node so the workflow
  pauses (exit 2) instead of re-dispatching the same step infinitely.

### Non-Goals

- No new generalized "skill step" dispatch abstraction.
- No change to `recovered`/`completed` node-flip semantics or the run-phase-review
  rework loop.
- No retroactive repair of the orc-67 archived state.
- No changes to `dispatch.py` — the existing agent spawn path handles `workflow-learner`.

## Approaches Considered

### Approach 1: Convert to a `run:` shell-script step (diagnosis Fix 1, literal form)

Change `agent: workflow-improver` → `run: scripts/inline/run-learn-cycle.sh`.

- Pro: matches the existing inline-step pattern; `record.py` requires no usage.
- Con: **not feasible**. Every `run:` step points to an executable shell script the
  CLI runs synchronously. `/learn` is a Claude skill — it cannot be invoked from a
  shell script. A shell wrapper could not produce the driver-context skill call.

### Approach 2: Declare `run-learn-cycle` as `agent: inline`

Change `agent: workflow-improver` → `agent: inline`. Add a driver-execute dispatch
path so `agent: inline` produces an action the driver runs in its own context
(rather than spawning), then reports `agent: "inline"` to `orchestrator done`.

- Pro: `record.py` already exempts `agent == "inline"` from the usage guard, so no
  `record.py` change for this fix.
- Con: muddies the `agent:` field semantics — `agent: inline` is already used for
  shell-script steps, not driver-context skill invocations. Extending it would
  conflate two distinct execution models under one sentinel.

### Approach 3: Convert `/learn` into a real spawnable agent — `workflow-learner` (selected)

Extract the full `/learn` pipeline into a new `agents/workflow-learner.md` agent
definition. The agent carries the entire learn pipeline (find context, gather inputs,
cross-feature analysis, evaluate, route findings, report, rule effectiveness, decay,
quality bar). Change `run-learn-cycle.yaml` to declare `agent: workflow-learner`.
Thin the `skills/learn/SKILL.md` to a short wrapper that spawns `workflow-learner`.

- Pro: honest — the step becomes a real agent spawn. `record.py`'s usage guard is
  satisfied by the subagent JSONL naturally. No `dispatch.py` or `record.py` changes
  needed for Fix 1 (only the `abandoned` flip in record.py for Fix 2). The driver
  retains `/learn` as a user-invocable entry point.
- Con: the learn pipeline now lives in two places (agent + thin skill wrapper) —
  but the agent is the canonical home; the skill is just a dispatch shim.

### Selected Approach

**Approach 3.** Approaches 1 and 2 are infeasible or introduce semantic drift.
Approach 3 is the cleanest fix: `/learn` becomes a real agent, satisfying `record.py`
without any sentinel tricks or dispatch changes. The existing `agent:` spawn path
handles it naturally.

The separate `abandoned`-loop bug is fixed independently in `record.py` (see
Decision below) — it is required regardless of Approach 3.

## High-Level Design

### Architecture Overview

Two independent changes:

1. **Convert `/learn` to a spawnable agent** — create `agents/workflow-learner.md`
   carrying the full learn pipeline. Change `run-learn-cycle.yaml` to declare
   `agent: workflow-learner`. Thin `skills/learn/SKILL.md` to a 3-step wrapper
   that resolves the feature-id and spawns `workflow-learner`. The dispatch path
   is unchanged — `workflow-learner` is a regular agent spawn.

2. **`abandoned` node-status flip** — `record.py` lines 1597–1617 currently flip
   the DAG node only for `completed`/`recovered`. Extend the flip so `abandoned`
   also marks the node `completed`. The node is then terminal; `state.status` stays
   `blocked` (already set at line 1580), and the next `orchestrator next` exits 2.

### Key Abstractions

- **`workflow-learner` agent** — a real spawnable agent carrying the full `/learn`
  pipeline. Satisfies `record.py`'s usage guard via subagent JSONL. User-invocable
  `/learn` skill becomes a thin wrapper that spawns it.
- **Terminal `abandoned` node** — an `abandoned` record marks its node `completed`
  (DAG-terminal) while setting `state.status = blocked` (workflow-paused). The two
  together mean "this step is done for this run; the workflow halts for a human".

## Low-Level Design

### Components

**`agents/workflow-learner.md`** (new)
- Standard agent frontmatter: `name: workflow-learner`, `model: claude-sonnet-4-6`,
  `color: green`, `tools: [Read, Write, Edit, Grep, Glob, Bash, WebSearch]`.
- Body: full learn pipeline from `skills/learn/SKILL.md` §0–§5c verbatim — find
  context, gather inputs, cross-feature analysis, evaluate, route findings, report,
  rule effectiveness update, decay, quality bar.

**`skills/learn/SKILL.md`**
- Replace full pipeline body with a 3-step wrapper: resolve feature-id from
  `$ARGUMENTS`, spawn `workflow-learner` agent with feature-id and `--scope` args.
- Frontmatter preserved intact (`user-invocable: true`, `args`).

**`config/steps/run-learn-cycle.yaml`** (global copy under `~/.config/orchestrator/`)
- Change `agent: workflow-improver` → `agent: workflow-learner`.
- Instruction simplified: spawn `workflow-learner` for the current change-id.

**`config/scripts/orchestrator_next/record.py`**
- Lines 1597–1617: change the node-flip gate from
  `if status in ("completed", "recovered"):` to also cover `abandoned`. The
  `abandoned` case takes the plain `else` branch → `mark_node_status(..., "completed")`.
  The run-phase-review rework logic and `_repeat_until_pending` only apply to
  `completed`/`recovered` and must not run for `abandoned` — keep them guarded so
  `abandoned` always falls through to the `completed` mark. `state.status` is
  already set to `blocked` at line 1580; that is untouched.

### Data Flow

Fix 1 (`run-learn-cycle` happy path):
```
orchestrator next → dispatch.py: contract.agent == "workflow-learner"
  → spawn action {agent: "workflow-learner", instruction, ...}
driver: spawns workflow-learner agent via Task tool
  → agent runs full learn pipeline, returns COMPLETION
  → orchestrator done {status: completed, agent: "workflow-learner", outputs}
record.py: subagent JSONL present, usage guard satisfied
  → node flipped to completed → next_step advances to compute-swe-metrics
```

Fix 2 (`abandoned` for any step):
```
orchestrator done {status: abandoned}
record.py line 1580: state.status = "blocked"
record.py lines 1597–1617: status == "abandoned" → node marked completed
_compute_next_step: abandoned node not re-emitted (is_node_ready excludes completed)
next orchestrator next: no ready node OR state blocked → exit 2 → driver halts
```

### State Management

- `state.yaml` `workflow_plan.<phase>.nodes[].status` — the `abandoned` node moves
  `in_progress` → `completed`.
- `state.yaml` `status` — set to `blocked` by the existing line 1580 for
  `abandoned`; unchanged by this design.
- No new state fields.

### Error Handling

- `run-learn-cycle` instruction makes learning best-effort. A `workflow-learner`
  failure records `learn_skipped` and the step completes `completed`.
- If a step genuinely must abandon, the workflow now pauses cleanly (exit 2) for a
  human, instead of looping. This is the correct failure mode.

## Constraints

- The change to `run-learn-cycle.yaml` is to the global step contract under
  `$ORCHESTRATOR_HOME/config/steps/`. It is a whole-file edit of one line.
- `record.py` changes must not alter behavior of `completed`/`recovered` steps or
  the run-phase-review rework loop.
- The `abandoned` flip must not trigger the run-phase-review rework loop or the
  `repeat_until` re-dispatch — those stay gated on `completed`/`recovered`.

## Trade-offs

- Splitting the learn pipeline between an agent and a thin skill wrapper means two
  files to keep in sync. Accepted: the agent is the canonical home; the skill is
  a one-time dispatch shim that rarely changes.
- Marking an `abandoned` node `completed` slightly overloads the word "completed"
  (the step did not succeed). Accepted: `node.status` is a DAG-walk control flag,
  and `state.status = blocked` carries the real outcome. The alternative (a new
  `abandoned` node status threaded through `readiness.py`) is more code for no
  behavioral gain.

## Acceptance Criteria

- AC-1: Given `run-learn-cycle` is the current step, when `orchestrator next` is
  run, then the dispatched action carries `agent: "workflow-learner"` and is a
  standard agent spawn action. [traces: UC-1]
- AC-2: Given the driver spawned `workflow-learner` for `run-learn-cycle`, when it
  calls `orchestrator done` with `status: completed, agent: "workflow-learner"`,
  then `record.py` accepts it (exit 0) — subagent JSONL satisfies usage guard.
  [traces: UC-1]
- AC-3: Given an `orchestrator done` payload with `status: abandoned` for a node
  currently `in_progress`, when the record is written, then that node's status in
  `workflow_plan` becomes `completed` and `state.status` becomes `blocked`.
  [traces: UC-2]
- AC-4: Given an `abandoned` record was written for a step, when `orchestrator
  next` runs, then the same step is NOT re-dispatched (no infinite loop); the
  command exits 2 (blocked) or advances past the abandoned node. [traces: UC-2]
- AC-5: Given existing `completed`/`recovered`/`agent:`-spawn/`run:`-script steps,
  when they are dispatched and recorded, then their behavior is unchanged (no
  regression in the existing test suite). [traces: UC-3]

## Decisions

- Fix 1 uses `workflow-learner` agent (real spawn), not `agent: inline` sentinel →
  the step becomes an honest agent spawn; `record.py` usage guard is satisfied
  naturally via subagent JSONL; no dispatch.py changes needed.
- `abandoned` node flipped to `completed` (not a new node status) → `node.status`
  is a DAG control flag; `state.status = blocked` already carries the real outcome
  → avoids threading a new status through `readiness.py`.
- Two independent regression tests (one per fix) → the `abandoned` loop is a
  latent bug independent of `run-learn-cycle`; `tdd_required=true` requires a test
  preceding each fix.

## Open Questions

- None.
