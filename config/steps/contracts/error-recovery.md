# Error Recovery Contract

Defines deterministic state transitions for all failure scenarios in the workflow.
The orchestrator and agents follow this contract to ensure identical recovery
behavior regardless of which model executes the step.

## State Transition Table

| Trigger | Condition | state.yaml Update | Next Action |
|---------|-----------|-------------------|-------------|
| Step completed | verify: assertions all pass | `step_history[]: {status: completed}` | Advance to next step |
| Step failed | verify: assertion fails | `step_history[]: {status: failed}`, increment `retries.<step_id>` | Re-execute step (same instruction + failure context) |
| Step blocked | Agent returns `STATUS: blocked` | `step_history[]: {status: blocked, blocker: "..."}` | Re-spawn agent once with blocker context (see § Agent Blocked Protocol) |
| Step blocked (2nd) | Agent blocked after re-spawn | `step_history[]: {status: failed}`, increment `retries.<step_id>` | Treat as step failure → retry or escalate |
| Step escalated to architect | Agent returns `STATUS: escalate_to_architect` | `step_history[]: {status: escalate_to_architect, escalation: {...}}` | `orchestrator next` returns `action: blocked, reason: escalate_to_architect`; caller spawns architect per `contracts/architect-escalation.md`; developer re-spawned with DECISION appended at **same attempt** (no retry charged) |
| Step blocked (dispatcher-level) | Dispatcher sees terminal `status: blocked` in last history entry for current phase | `step_history[]: {status: blocked}` already written | `orchestrator next` returns `action: blocked, reason: blocked, exit 2`; caller applies Agent Blocked Protocol |
| Phase verification failed | Any verify.command exits non-0, assertion false, or metric below threshold | `step_history[]: {step_id: run-phase-review, status: completed, verdict: needs_work}`, increment `retries.run-phase-review` | Generate fix tasks per § Fix Task Protocol, re-run phase review |
| Retry exhausted | `retries.<key> >= max_retries` | No additional update | Execute `on_max_retries` action per § Escalation Protocol |
| Agent spawn failed | Agent tool returns error | `step_history[]: {status: failed, error: "spawn failed"}` | Retry spawn once. If still fails, treat as retry exhausted. |

### `escalate_to_architect` State Entry Schema

When a developer agent encounters a design question that requires architect input,
it writes a `step_history` entry with `status: escalate_to_architect` and an
`escalation` sub-block:

```yaml
step_history:
  - step_id: execute-next-task
    phase: implement
    status: escalate_to_architect
    agent: developer
    attempt: 1
    started_at: "2026-04-18T10:00:00Z"
    ended_at: "2026-04-18T10:12:00Z"
    escalation:
      type: contradiction          # contradiction|missing_coverage|scope_ambiguity|architectural_dependency
      task_id: T-7
      context: "…"
      question: "…"
      attempted: "…"
    usage: { ... }
```

The `orchestrator next` dispatcher surfaces this as `action: blocked` with `reason:
escalate_to_architect` and the escalation block in the JSON response (exit code 2).
After the architect responds and the developer is re-spawned, the new completion entry
shares the same `attempt` — this is the only case where two terminal `step_history`
entries may exist at the same `(phase, step_id, attempt)` (distinguished by `status`).
The `step_events` DuckDB table preserves both rows via a 6-column composite primary
key that includes `status`. See `contracts/step-dispatch.md` § Escalation Protocol.

## Fix Task Protocol

When phase verification fails and retries remain:

1. For each failing assertion or command, generate exactly one fix task:
   - **Finding**: the specific failure (command output or assertion text)
   - **Scope**: only files directly related to the failure
   - **Approach**: minimal change to make the assertion/command pass
2. Append fix tasks to tasks.md under the current phase, using Task Format Contract
3. Mark the failing step as needing re-execution
4. Do NOT generate refactoring or improvement tasks — only fix the specific failure

## Agent Blocked Protocol

When an agent returns `STATUS: blocked`:

```
HANDLE_BLOCKED(agent_result, step, attempt):
  1. If attempt == 1:
     - Append blocker context to prompt: "Previous attempt was blocked: [BLOCKER]"
     - Re-spawn agent with augmented prompt
     - Set attempt = 2
  2. If attempt == 2:
     - Do NOT re-spawn
     - Record as step failure: {status: failed, blocker: agent_result.BLOCKER}
     - Increment retries.<step_id>
     - Follow retry/escalation logic
```

Maximum agent re-spawns for blocked status: **1** (total attempts: 2).

## Escalation Protocol

When `retries.<key> >= max_retries`, execute the `on_max_retries` action:

| Action Value | Behavior | When Used |
|-------------|----------|-----------|
| `escalate` | Set `status: paused` in state.yaml. Present failure summary to user with: failing assertions, retry count, suggested fix direction. Wait for user input. | Default. Used when `auto` flag is false. |
| `ticket` | Create a Linear ticket with failure details. Set `status: paused`. Continue to next phase if possible, or stop. | Used when `auto` flag is true — autonomous mode cannot pause for user input. |
| *(absent)* | Default to `escalate` if `auto` is false, `ticket` if `auto` is true. | When schema omits `on_max_retries`. |

## Retry Context Contract

When a step is re-spawned after failure, the dispatch loop must pass the agent
enough information to avoid repeating the same mistake. The re-spawn prompt
includes a `RETRY_CONTEXT:` block built from `step_history[-1].retry_context`
(set by the failing step, per its own retry recording convention).

Block format:

```
RETRY_CONTEXT:
  attempt: <K>                  # 1-based; K=2 means this is the second attempt
  previous_failure: <category>  # regression | test_failure | verify_assertion | blocked
  detail: <string>              # failing test names + stdout tail (≤ 20 lines), or regression delta
  baseline: <N or null>         # baseline.passing at regression time, or null
```

The dispatch loop:
1. Before re-spawn, reads `step_history[-1].retry_context` from state.yaml.
2. If present, appends the block verbatim to the agent prompt *after* the
   step instruction, separated by a blank line.
3. If absent (first attempt, or a step that does not record context), no block
   is appended — behavior matches pre-retry-context dispatch.

Steps that produce retry context must write it under `retry_context:` in the
step_history entry at failure time, alongside the existing `regression:` or
`error:` block. This keeps retry context durable across resume.

## Quarantine Protocol

When a task hits `max_retries` under `auto: true` (autopilot / lenient mode),
the feature is not paused. Instead:

1. Mark the task quarantined in tasks.md by changing `- [ ]` to `- [!]` and
   appending `<!-- quarantined: attempt <K>, reason: <category> -->` to the
   task line. Subsequent `all_tasks_completed` checks treat `[!]` as terminal
   (task is no longer blocking), but not as complete.
2. Append to state.yaml `quarantine_events`:
   ```yaml
   quarantine_events:
     - task_id: "T-<N>"
       attempts: <K>
       reason: <regression | test_failure | blocked>
       last_detail: <one-line failure summary>
       quarantined_at: "<ISO 8601 timestamp>"
   ```
3. Pop the last retry's stash (`git stash pop <ref>`) so the broken state is
   in the working tree — the phase reviewer needs to see what was left behind.
4. Proceed to the next task. Do NOT advance the phase while quarantined tasks
   remain unexamined — `run-phase-review` reads `quarantine_events` and treats
   each as a critical finding that gates phase pass.

Quarantine is inactive when `auto: false`. Under interactive mode, retry
exhaustion follows § Escalation Protocol as before.

## Missing STATUS Rule

If an agent's output does not contain a `STATUS:` field (either `completed` or `blocked`),
treat the result as `STATUS: blocked` with `stop_reason: missing_status`. Follow the
Agent Blocked Protocol — re-spawn once with context explaining the missing STATUS, then
treat as step failure if the second attempt also lacks STATUS.

This prevents silent success assumptions when agents return ambiguous output (e.g., empty
output, unstructured text, or error messages without the structured result format).

## State Recording for Failures

```yaml
# Step failure example
step_history:
  - step_id: execute-next-task
    phase: implement
    status: failed
    agent: developer
    error: "Test assertion failed: expected 200, got 404"
    retry_count: 2  # optional — total attempts for this step

# Retry counter
retries:
  execute-next-task: 2
  run-phase-review: 1
```

## Structured Error Events

The `error_events` field in state.yaml records every agent failure with structured data.
This enables post-run diagnostics (autopilot reading state.yaml to understand why a run
failed) and cross-session resume (hooks reading failure context on session start).

```yaml
# Top-level field in state.yaml — backward compatible (optional)
error_events:
  - step_id: execute-next-task
    phase: implement
    agent: developer
    attempt: 1
    stop_reason: error
    detail: "Agent internal error — no output returned"
    timestamp: "2026-04-05T04:12:00Z"
  - step_id: execute-next-task
    phase: implement
    agent: developer
    attempt: 2
    stop_reason: missing_status
    detail: "Agent returned output without STATUS field (0 tool calls)"
    timestamp: "2026-04-05T04:15:00Z"
```

### error_events Field Schema

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `step_id` | string | Yes | Step contract ID where failure occurred |
| `phase` | string | Yes | Phase name |
| `agent` | string | Yes | Agent role (e.g., `developer`, `reviewer`, `discoverer`) |
| `attempt` | integer | Yes | 1-based attempt number for this step |
| `stop_reason` | enum | Yes | One of: `error`, `missing_status`, `empty_output`, `spawn_failed`, `max_iterations_exceeded` |
| `detail` | string | Yes | Human-readable failure description (agent output excerpt or error message) |
| `timestamp` | string | Yes | ISO 8601 timestamp |

### stop_reason Values

| Value | Meaning |
|-------|---------|
| `error` | Agent returned `STATUS: blocked` or subagent-gate reported `stop_reason: error` |
| `missing_status` | Agent output did not contain a `STATUS:` field |
| `empty_output` | Agent returned with zero tool calls (subagent-gate detected) |
| `spawn_failed` | Agent tool itself returned an error (spawn did not complete) |
| `max_iterations_exceeded` | `repeat_until` step hit the max_iterations ceiling |

### Backward Compatibility

`error_events` is optional. State.yaml files without this field are valid — the
orchestrator initializes it as an empty array on first failure. Existing tools
(workflow-state.sh, auto-continue.sh) that read state.yaml are not affected.
