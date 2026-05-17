# Step Dispatch Contract

Authoritative reference for the `orchestrator next` CLI interface. Consumers
(skills, adapter authors, callers integrating with the dispatcher) should use this
file as the single source of truth for the JSON response schema, exit codes, and
protocol rules.

## Invocation

```
orchestrator next <path-to-state.yaml>
```

Positional argument: path to the active `state.yaml` file. One form, one verb.
No flags, no subcommands beyond `next`.

The CLI is pure-read: it reads `state.yaml` and step contracts, writes only to
`metrics.duckdb`, and never mutates `state.yaml` or spawns subprocesses.

## Exit Codes

| Code | Meaning | Action returned |
|------|---------|-----------------|
| `0` | Action available | `run_step`, `run_inline`, `resume_step`, `verify_phase` |
| `1` | Workflow complete | `complete_workflow` |
| `2` | Blocked — caller must intervene | `blocked` |
| `3` | CLI error | No JSON on stdout; error on stderr |

Exit codes are a convenience signal. The JSON `action` field is the canonical signal.
Callers must parse JSON to get the full context (especially for `blocked`).

## JSON Response Shape by Action Type

All responses are emitted on stdout as pretty-printed, sorted-keys JSON (one object,
terminated by a newline). The canonical form uses `sort_keys=True, indent=2`.

### `run_step` — step has a `run:` field in its contract

```jsonc
{
  "action": "run_step",
  "step_id": "git-init",
  "phase": "specify",
  "attempt": 1,
  "run": "scripts/inline/git-init.sh",
  "instruction": "…",
  "rules": ["…"],
  "env": {
    "ORCHESTRATOR_CHANGE_ID":    "my-feature",
    "ORCHESTRATOR_PHASE":        "specify",
    "ORCHESTRATOR_STEP_ID":      "git-init",
    "ORCHESTRATOR_ATTEMPT":      "1",
    "ORCHESTRATOR_WORKFLOW_DIR":           "/path/to/.workflows/my-feature",
    "ORCHESTRATOR_REPO_ROOT":              "/path/to/code/orchestrator",
    "ORCHESTRATOR_WORKTREE_ARTIFACT_DIR":  "/path/to/feature_worktrees/my-feature/spec/changes"
  }
}
```

### `run_inline` — step has no `run:` field (inline execution)

```jsonc
{
  "action": "run_inline",
  "step_id": "create-or-refresh-artifacts",
  "phase": "specify",
  "attempt": 1,
  "agent": "inline",
  "instruction": "…",
  "rules": ["…"],
  "env": { /* same 7 ORCHESTRATOR_* keys */ }
}
```

Note: `run` is absent for `run_inline`. All 31 inline-only step contracts produce
this action until they migrate to the `run_step` path.

### `resume_step` — last history entry is `in_progress` with no `ended_at`

```jsonc
{
  "action": "resume_step",
  "step_id": "execute-next-task",
  "phase": "implement",
  "attempt": 1,
  "is_resume": true,
  "started_at": "2026-04-20T10:00:00+00:00",
  "agent": "developer",
  "instruction": "…",
  "rules": ["…"],
  "env": { /* same 6 ORCHESTRATOR_* keys, ORCHESTRATOR_ATTEMPT="1" */ }
}
```

`attempt` is the ORIGINAL attempt number from the in_progress entry — it is NOT
incremented. `is_resume: true` signals to the caller that this is a resume of an
interrupted step, not a fresh dispatch. `started_at` is preserved from the in_progress
entry (the original wall-clock time the step was first dispatched).

### `verify_phase` — all phase steps terminal, phase has unevaluated `verify:` block

```jsonc
{
  "action": "verify_phase",
  "phase": "implement",
  "commands": ["bash scripts/verify-spec.sh"],
  "assertions": ["design.md exists and is non-empty"]
}
```

The caller runs the commands, evaluates assertions, and reports results by appending a
`run-phase-review` step_history entry with `status: completed` (pass) or
`status: failed` (fail). On failure the CLI on the next call returns `run_step` for
`run-phase-review` (a new attempt). The CLI never runs verify commands itself (pure-read invariant).

### `complete_workflow` — all phases complete (exit 1)

```jsonc
{
  "action": "complete_workflow"
}
```

Exit code 1. Caller writes final `status: completed` to state.yaml and archives.

### `blocked` — escalation or persistent block (exit 2)

```jsonc
{
  "action": "blocked",
  "reason": "escalate_to_architect",
  "phase": "implement",
  "step_id": "execute-next-task",
  "escalation": {
    "type": "contradiction",
    "task_id": "T-7",
    "context": "…",
    "question": "…",
    "attempted": "…"
  }
}
```

`reason` is one of: `escalate_to_architect`, `blocked`. For `reason: blocked`,
the `escalation` field is absent; the `step_history` entry's own `blocker` field
provides context. For `reason: escalate_to_architect`, the `escalation` block is
copied from the step_history entry's `escalation:` sub-block.

## Environment Variable Contract

When `action ∈ {run_step, run_inline, resume_step}`, the response includes an `env`
object with exactly these seven keys:

| Variable | Value | Description |
|----------|-------|-------------|
| `ORCHESTRATOR_CHANGE_ID` | slug string | Identifier from `state.yaml change_id` |
| `ORCHESTRATOR_PHASE` | string | Current phase name |
| `ORCHESTRATOR_STEP_ID` | string | Step being dispatched |
| `ORCHESTRATOR_ATTEMPT` | string (integer) | 1-based attempt number, CLI-computed |
| `ORCHESTRATOR_WORKFLOW_DIR` | absolute path | Workflow state directory (from `state.yaml worktree_path`) |
| `ORCHESTRATOR_REPO_ROOT` | absolute path | Repository root (from `ORCHESTRATOR_REPO_ROOT` env at invocation time) |
| `ORCHESTRATOR_WORKTREE_ARTIFACT_DIR` | absolute path | Base path for tracked workflow artifacts (spec/design/tasks/diagnose); points to `$WORKTREE_ROOT/spec/changes` when `flags.worktree=true`, otherwise `$REPO_ROOT/spec/changes`. |

Callers set these variables in the environment of the adapter or inline agent they
spawn. Adapters read them to locate `state.yaml`, compute their output paths, and
write the completed `step_history` entry.

## Attempt Assignment

The `attempt` number is computed by the CLI — never by the agent. The CLI scans
`step_history` for all entries matching `(phase, step_id)`, finds the maximum
`attempt` value among them, and returns `max + 1` (defaulting to 1 when no entries
exist for that pair). Agents treat `attempt` as opaque data from the JSON response
and write the returned value verbatim into their `step_history` entry.

Scope: attempt counting is per `(phase, step_id)` pair. A completed `attempt: 1`
entry in one phase does not affect attempt counting for the same `step_id` in a
different phase.

Exception: `resume_step` preserves the in_progress entry's attempt unchanged — the
CLI does NOT call the `max + 1` formula for this action. The returned `attempt` equals
the original in_progress entry's attempt value.

## Resume Protocol

When the CLI returns `action: resume_step`:

1. Caller uses the returned `attempt`, `step_id`, `phase`, `env` to re-spawn the agent.
2. The agent re-executes the step and appends a new `step_history` entry using the
   SAME `attempt` number (no increment).
3. Caller calls `orchestrator next` again — CLI upserts the terminal entry and returns
   the next action.

The `is_resume: true` field signals the caller that this is a crash-recovery dispatch.
The `started_at` field contains the original wall-clock start time of the interrupted
step. The CLI does not resume automatically; the caller drives the resume loop.

## Escalation Protocol

When `reason: escalate_to_architect`:

1. Caller reads the `escalation` block from the JSON response.
2. Caller spawns the architect agent per `contracts/architect-escalation.md`, passing
   the escalation context (`type`, `task_id`, `context`, `question`, `attempted`).
3. Architect returns `DECISION`. Caller appends the decision to the developer's prompt.
4. Caller re-spawns the developer with the **same attempt number** (no retry charged).
5. Developer appends a new `step_history` entry (typically `status: completed`) at
   the same `attempt`.
6. Both the `escalate_to_architect` entry and the subsequent `completed` entry are
   upserted into `step_events`. The composite primary key includes `status`, so both
   rows are preserved as the escalation audit trail.

The CLI never increments the attempt counter for an escalation cycle. Retries and
escalations are distinct: retries consume retry budget; escalations do not.

## Pure-Read Guarantee

The CLI **reads**: `state.yaml`, step contracts under
`$ORCHESTRATOR_HOME/config/steps/` (or override path), `config/pricing.yaml` (when
present, for optional enrichment).

The CLI **writes**: `metrics.duckdb` (INSERT OR REPLACE into `step_events` only).

The CLI **never**: writes to `state.yaml`, spawns subprocesses, makes network calls,
or touches any file other than `metrics.duckdb`.

Verifying this invariant: `stat -f %m state.yaml` before and after `orchestrator next`
must return the same timestamp. All fixture tests assert mtime-unchanged.

## Error Handling

| Condition | Behavior |
|-----------|----------|
| `state.yaml` not found or unreadable | exit 3, diagnostic on stderr, no DuckDB write |
| Malformed YAML | exit 3, YAML parse error with file:line on stderr |
| `change_id` fails slug guard (`^[a-z0-9][a-z0-9-]*$`) | exit 3, no DuckDB write |
| Step contract not found for the pending step_id | exit 3, lists searched paths on stderr |
| DuckDB locked by concurrent writer | exit 3, message notes single-writer constraint |

Exit 3 always means "no action was determined". The caller should surface the stderr
to the user and not proceed.
