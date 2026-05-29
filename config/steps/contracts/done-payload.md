# Done Payload Contract

Authoritative reference for the `orchestrator done` CLI interface. Consumers
(skills, dispatch drivers, adapter authors) should use this file as the single
source of truth for the JSON stdin payload, responsibility split, and validation
rules.

Pair with `contracts/step-dispatch.md` (read path via `orchestrator next`).

## Responsibility split

| Role | Owns |
|------|------|
| **Step agent** (developer, discoverer, reviewer, …) | Task work, artifact files under `$WORKTREE_ARTIFACT_DIR/$CHANGE_ID/`, self-verification evidence, a **COMPLETION** block when the step's work is finished |
| **Dispatch driver** (`/orchestrate`, `/developer` loop) | Calls `orchestrator next`, spawns agents, maps COMPLETION → JSON, pipes to `orchestrator done` — **no** artifact or verify checks |
| **Reviewer** (`run-phase-review`, `/reviewer`) | Independent verification: task-node completeness (via step_history), verify commands, AC, code quality |
| **`record.py`** | Validates payload, appends `step_history`, advances `next_step`, writes DuckDB metrics |

**Agents MUST NOT** call `Write`/`Edit` on `state.yaml`. **Agents MUST NOT** call
`orchestrator done` directly — the driver does after collecting the agent result.

## Invocation

```
orchestrator done <path-to-state.yaml>   # JSON payload on stdin
```

Exit codes: `0` success, `3` validation error (state unchanged), `4` YAML
corruption detected after write (file restored), `5` boundary DB write failed.

## Required payload fields

| Field | Type | Description |
|-------|------|-------------|
| `step_id` | string | Step contract id (from `orchestrator next` action) |
| `phase` | string | Current phase name (from state.yaml) |
| `outputs` | object | Keys must cover every name in the step contract `outputs:` list |

## Optional payload fields

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `status` | string | `completed` | One of: `completed`, `recovered`, `abandoned` |
| `agent` | string | `inline` | Agent role from `action.agent` — **required** for agent steps |
| `agent_task_result` | string | — | Raw Task tool result text; `record.py` extracts `agentId:` and loads usage from subagent JSONL. **Preferred** for Claude Code drivers. Transient — not persisted in `step_history`. |
| `agent_id` | string | — | 17-char hex subagent id (deprecated for drivers — use `agent_task_result`; still accepted when passed explicitly) |
| `attempt` | int | CLI-computed | Preserve on resume; do not increment manually |
| `started_at` | string | now | ISO 8601 from `action.started_at` on resume |
| `usage` | object | — | Token/tool stats; required when `agent_task_result` is absent. When `agent_task_result` is present, `record.py` fills usage from JSONL. |
| `evidence` | object | — | Machine-visible proof (see § Evidence) |
| `artifacts` | list | — | Filenames created or modified this step |
| `review_score` | object | — | For `run-phase-review` only |
| `approach` | object | — | From pre_execute APPROACH block |
| `regression` | object | — | Regression gate failure detail |
| `rollback` | object | — | Stash ref after failed attempt |
| `retry_context` | object | — | Passed into next spawn on retry |
| `blocker` | object | — | `{ blocked_task, reason }` when STATUS: blocked |
| `escalation` | object | — | Architect escalation context |
| `workflow_issues` | list | — | Workflow anomalies to append to `retro.md` (best-effort). Schema: `contracts/workflow-issues.md` |
| `state_patch` | object | — | Top-level state.yaml scalars/lists to merge (see § State patch) |

Unknown top-level keys are ignored (same as `task_checkpoint` — never persisted).

### `run-phase-review` output validation

When `step_id` is `run-phase-review` and `status` is `completed`, `record.py`
rejects (exit 3) invalid `outputs.phase_review_report.verdict` values. Allowed:
`pass`, `needs_work`, `incomplete_phase`. Typos such as `passed` or `PASS`
fail at the boundary instead of silently corrupting metrics.

## Evidence block (`evidence_required: true` steps)

When the step contract sets `verify.evidence_required: true`, the driver MUST
include at least one non-empty subsection:

```json
{
  "evidence": {
    "commands": [
      {"cmd": "pytest tests/ -q", "exit_code": 0, "stdout_tail": "…last 20 lines…"}
    ],
    "file_checks": [
      {"path": "spec/changes/foo/tasks.yaml", "exists": true, "sha256": "…", "lines": 42}
    ],
    "counts": {"tasks_marked": 1, "tests_passing": 47}
  }
}
```

For `execute-one-task`, include `evidence.counts.tasks_marked` with the number
of tasks marked complete this spawn (typically 1 per execute-one-task invocation).
Verification checks are the reviewer's job at `run-phase-review` — not the
driver's.

## Agent COMPLETION block

Agents write human-readable artifacts to `$WORKTREE_ARTIFACT_DIR/$CHANGE_ID/`
themselves, then return this block verbatim. The driver maps it into the
`orchestrator done` payload (adds `step_id`, `phase`, `agent`, `agent_task_result`
from the Task tool result text, and COMPLETION fields). The driver MUST NOT
parse report prose for scores, verdicts, or artifact content, and MUST NOT
extract `<usage>` blocks or `agentId:` lines — `record.py` owns that.

```
COMPLETION:
  status: completed | failed | blocked
  outputs:
    <output_name>: <value>          # keys from step contract outputs:
  artifacts: [<filenames>]
  evidence:
    commands: [...]
    counts: {tasks_marked: <N>}   # execute-one-task: tasks marked complete this spawn
  review_score: {overall: N, dimensions: {...}}   # run-phase-review only
  regression: {...}                               # execute-one-task only
  rollback: {stash_ref: "stash@{0}", attempt: K}
  retry_context: {attempt: K, previous_failure: "...", detail: "..."}
  blocker: {blocked_task: "T-N", reason: "..."}
  state_patch:
    retries: {"T-3": 2}
```

## execute-one-task — implement one task, then COMPLETION

The developer agent implements exactly **one** task per spawn (identified by
`step_context.task`):

1. Read task from `step_context.task` (id, title, files, verify, test_scenarios).
2. Implement the task, run verify commands, commit per `contracts/auto-commit.md`.
3. Return **one** COMPLETION block with `evidence.counts.tasks_marked: 1`.

The driver maps COMPLETION → `orchestrator done` and calls `orchestrator next`.
Each task-node is a separate DAG node injected by `expand-plan`. The dispatcher
walks them in dependency order — no loop inside the agent.

## Example payloads

### Inline / script step

```json
{
  "step_id": "git-init",
  "phase": "specify",
  "status": "completed",
  "outputs": {"git_init_result": "ok"}
}
```

### Agent step (discoverer)

```json
{
  "step_id": "explore",
  "phase": "specify",
  "status": "completed",
  "agent": "discoverer",
  "agent_task_result": "Async agent launched successfully.\nagentId: a6e7ca188209d1f47 (internal ID - do not mention to user)",
  "outputs": {"discovery_result": {"path": "discovery.md"}},
  "artifacts": ["discovery.md"]
}
```

(`record.py` extracts `agent_id` from `agent_task_result` and populates `usage` from the subagent JSONL.)

### execute-one-task (single task node)

```json
{
  "step_id": "task-T-2",
  "phase": "implement",
  "status": "completed",
  "agent": "developer",
  "agent_id": "b7f8ab299310e2g58",
  "outputs": {"task_execution_result": {"task_id": "T-2", "status": "completed"}},
  "artifacts": ["src/module.py"],
  "usage": {"input_tokens": 25000, "output_tokens": 5000},
  "evidence": {
    "commands": [{"cmd": "pytest -q", "exit_code": 0, "stdout_tail": "…"}],
    "counts": {"tasks_marked": 1}
  }
}
```

### run-phase-review (pass)

```json
{
  "step_id": "run-phase-review",
  "phase": "implement",
  "status": "completed",
  "agent": "reviewer",
  "outputs": {"phase_review_report": {"verdict": "pass"}},
  "artifacts": ["phase-review.md"],
  "review_score": {
    "overall": 9,
    "dimensions": {
      "spec_compliance": 9,
      "correctness": 10,
      "security": 9,
      "simplicity": 9,
      "code_quality": 9
    }
  },
  "usage": {"input_tokens": 30000, "output_tokens": 5000}
}
```

### Feature signoff (reviewer Mode 3)

```json
{
  "step_id": "final-signoff",
  "phase": "complete",
  "status": "completed",
  "agent": "reviewer",
  "outputs": {"signoff_report": {"verdict": "ready"}},
  "artifacts": ["signoff-report.md"],
  "usage": {"input_tokens": 25000, "output_tokens": 4000}
}
```

## State patch

Use `state_patch` for top-level fields the step contract requires updating
alongside `step_history` — never edit state.yaml by hand.

| Key | Merge behavior |
|-----|----------------|
| `retries` | Merge by key into existing `retries:` map — payload values are **absolute counts per task/step id** (last write wins per key, not increment) |
| `quarantine_events` | Append to list |
| `baseline` | Replace `baseline` object |
| `refresh_artifacts` | Replace boolean |
| `change_type` | Replace string |
| `flag_adaptations` | Replace list |

Example (failed task attempt):

```json
{
  "step_id": "task-T-3",
  "phase": "implement",
  "status": "completed",
  "agent": "developer",
  "outputs": {"task_execution_result": {"task_id": "T-3", "status": "retry"}},
  "retry_context": {"attempt": 2, "previous_failure": "test_failure", "detail": "AssertionError in test_foo"},
  "rollback": {"stash_ref": "stash@{0}", "attempt": 2},
  "state_patch": {"retries": {"T-3": 2}},
  "usage": {"input_tokens": 5000, "output_tokens": 1200}
}
```

## What lands in step_history

`record.py` appends one entry per call. Shape matches CONVENTIONS.md § State
Updates — the payload fields above are copied into the entry; timestamps and
`next_step` are computed by the CLI.

## Workflow plan and next_step (ORC-63)

Workflow state lives in one file, `state.yaml` — there is no separate plan
file. `workflow_plan[phase]` is `{nodes, filtered, verify}`; each node carries
`{id, depends_on?, status, agent, goal, inputs, outputs, rules,
repeat_until?}` with `status` ∈ `{pending, in_progress, completed, skipped}`.

On a `completed` record, `record.py` flips the node's `status` to `completed`
via the shared readiness helper, then re-derives `state.next_step` from the
DAG-walk. `node.status` is the source of truth for dispatch readiness;
`next_step` is a derived convenience pointer for the resume mechanism.

`record.py` also enforces declared `outputs:` at this boundary: a declared
output is satisfied only when its key is present in `evidence.outputs`, the
value is non-null and non-empty, and — for a path-named output (name contains
`/`) — the file exists on disk. A missing declared output rejects the payload
with `missing_outputs` (exit 3).

## Driver checklist (after every agent spawn)

The driver orchestrates only — it does not verify task or test outcomes.

1. Parse agent COMPLETION block (or treat failure as `status: failed`).
2. Merge dispatch context: `step_id`, `phase`, `agent`, `attempt`, `started_at`, and the raw Task tool result as `agent_task_result`.
3. Pipe JSON to `orchestrator done <state.yaml>`.
4. On exit 0: call `orchestrator next` for the following step.
5. On exit 3: read stderr reason, fix payload, retry — do not advance.

Verification (task completeness, verify commands, AC) is enforced by `run-phase-review`
and the `/reviewer` agent — not by the dispatch driver.

## Consumers

- `skills/orchestrate/SKILL.md` — dispatch loop driver
- `skills/developer/SKILL.md` — implementation queue driver
- `agents/*.md` — return COMPLETION; never edit state.yaml
- `config/steps/*.yaml` — reference this contract instead of "update state.yaml"
