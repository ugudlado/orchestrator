---
name: orchestrate
description: "Workflow router — detects intent and loads the right schema. This skill should be used when the user says 'orchestrate', 'start a feature', 'fix a bug', 'do a chore', 'run a spike', 'bootstrap this repo', or describes development work that maps to a workflow type (feature, bugfix, chore, spike, bootstrap, autopilot)."
user-invocable: true
args:
  - name: request
    description: >
      What to work on — a description, Linear ticket ID (e.g. HL-170), or a feature ID to resume.
      All flags are passed through as-is to the resolved schema.
    required: false
---

## Variables

```
REPO_ROOT=${REPO_ROOT:-$(git rev-parse --show-toplevel)}
REPO_NAME=${REPO_NAME:-$(basename "$REPO_ROOT")}
ORCHESTRATOR_HOME=${ORCHESTRATOR_HOME:-$HOME/.config/orchestrator}
REPO_WORKFLOW_DIR=${REPO_WORKFLOW_DIR:-$REPO_ROOT/.orchestrator}
WORKFLOW_STATE_DIR=${WORKFLOW_STATE_DIR:-$REPO_ROOT/.state}
```

## Workflow file resolution

Every read of a workflow file (schema, step contract, template, included
phase, guidelines) MUST use the resolver defined in
`config/steps/contracts/workflow-override.md`:

```
RESOLVE_WORKFLOW_FILE(relative_path):
  repo_override = $REPO_WORKFLOW_DIR/<relative_path>
  IF exists(repo_override):
    RETURN repo_override
  RETURN $ORCHESTRATOR_HOME/config/<relative_path>
```

Repo overrides **fully replace** the global file (no YAML merge). Protocol
contracts under `steps/contracts/` (error-recovery, resume-token,
rule-merge, metrics-schema, workflow-override itself) are universal and
NOT subject to override — always read from `$ORCHESTRATOR_HOME/config/`.

When reading any path written below as `$ORCHESTRATOR_HOME/config/<...>`,
apply `RESOLVE_WORKFLOW_FILE(<...>)` unless it is a universal invariant
contract listed above.

## Execution

### 1. Resolve schema and flags

1. Read `$ORCHESTRATOR_HOME/config/guidelines.yaml`.
2. Match the user's request to the best workflow schema (feature, bugfix, chore, spike, bootstrap, autopilot).
3. Read the schema YAML: `$ORCHESTRATOR_HOME/config/workflows/<schema>.yaml`.
4. Resolve flags:
   - Start with schema `defaults:` (e.g., `tdd_required: true, auto: false`)
   - Apply any user-provided flags via the schema's `flags:` mapping (e.g., `--no-tdd` sets `tdd_required: false`)
   - The resolved flag set determines which steps run and which rules activate
5. Tell the user which schema and flags were resolved.

### 2. Check for resume

Read `$WORKFLOW_STATE_DIR/*/state.yaml` for any active change (status: active) matching this repo. If found:
- Read `next_step` from state.yaml to get the resume point (phase + step_id)
- Read `flags` from state.yaml (flags were resolved at workflow start and persisted)
- Skip to that phase and step in the dispatch loop below
- Tell the user: "Resuming <change_id> at <phase>/<step_id>"

If no active state, this is a new workflow — proceed from the first phase and step.

### 3. Build filtered step list

For each phase in the schema:
- If a phase has `include: _<name>` instead of inline fields, read the phase
  definition from `$ORCHESTRATOR_HOME/config/workflows/_<name>.yaml` and use
  its fields (goal, rules, verify, steps). Schema-level overrides (e.g., a
  different `verify.metrics.review_score.min`) take precedence over the included
  definition.

Build the active step list using the resolved flags:
- `step-id if <flag>` → include only when flag is truthy
- `step-id if not <flag>` → include only when flag is falsy
- Plain `step-id` or `id: step-id` → always include
- Preserve ordering — steps execute in the order listed


### 4. Dispatch loop — HL-287 M5: use the `orchestrator` CLI

The dispatch loop is now a thin wrapper around `orchestrator next` and
`orchestrator done`. Pre/post stamping (started_at / completed_at /
status / usage / evidence) is applied uniformly by the CLI — do NOT
write per-step stamping prose.

**Context-passthrough contract (post generate-plan-yaml-at-init, 2026-04-20).**
`orchestrator next` returns an action JSON that already contains the agent's
full operational contract: `instruction`, `rules` (merged per rule-merge.md),
`step_context` (the plan.yaml step block with goal/inputs/outputs/verify/agent),
`inputs`, `expected_outputs`, `resolved_allowed_tools`, `env`, plus (for
resume_step) `is_resume` and `started_at`. The driver MUST pass this payload
verbatim into the agent spawn prompt. It MUST NOT re-derive goal, rules, or
outputs from memory — those already live in `step_context` and `rules`.

What the driver SHOULD add to a spawn prompt (beyond the passthrough):
- Feature-specific paths the agent can't get from step_context: state_dir,
  worktree_path, backlog-entry anchor, prior-phase archives (if relevant).
- For developer spawns: the exact task row from tasks.md + declared Files list.
- For reviewer spawns: the artifacts + any driver-level findings to verify.

What the driver MUST NOT duplicate in the spawn prompt:
- The step's goal (in step_context.goal).
- The merged rules (in `rules` and step_context.rules — same list).
- Expected_outputs (in `expected_outputs`).
- Verify criteria (in step_context.verify when applicable).
- Step-contract instruction (in `instruction`).

This keeps plan.yaml as the single source of step contract; duplication
invites drift when step contracts update.

```
LOOP:
  action = orchestrator next $WORKFLOW_STATE_DIR/$CHANGE_ID/state.yaml

  IF action.action == "complete_workflow":
      # Cost report: run `scripts/cost-report.sh --change-id <change_id>` and include
      # its stdout verbatim in the final message to the user. This is the canonical
      # end-of-workflow cost summary (HL-290). No file is committed; no archive
      # side-effect. If the command fails, include the error message but do not
      # block the workflow completion.
      cost_report = run `scripts/cost-report.sh --change-id $CHANGE_ID`
      STOP (workflow done) — include cost_report stdout in your final message
  IF action.action == "blocked":            STOP (escalate or fix)
  IF action.action == "verify_phase":       run action.commands + action.assertions
  IF action.action == "run_inline" AND action.agent == "inline":
      # HL-287 M3 inline-script dispatch
      run action.run with action.inputs as env vars
      collect outputs (last stdout line as JSON dict)
      orchestrator done state.yaml <<< {step_id, phase, status, outputs, usage, evidence}
  IF action.action == "run_inline" AND action.agent != "inline":
      # Legacy inline-instruction (pre-M3, being phased out)
      execute action.instruction in context with action.inputs / action.rules
      orchestrator done state.yaml <<< {step_id, phase, status: completed, outputs, usage}
  IF action.action == "run_step":
      # Agent spawn. Load agent .md from $ORCHESTRATOR_HOME/agents/<action.agent>.md
      # and run action.run (adapter path) with the agent's prompt + action.inputs.
      # Spawn with run_in_background: true as the default.
      # Exceptions: ideator and reviewer spawns are short-running and may be foreground.
      spawn agent(action.agent) with prompt=action.instruction, rules=action.rules,
            step_context=action.step_context, inputs=action.inputs,
            expecting=action.expected_outputs,
            resolved_allowed_tools=action.resolved_allowed_tools, env=action.env,
            run_in_background: true  # default; omit for ideator/reviewer
      # Pass action.step_context into the prompt verbatim (goal, merged rules,
      # inputs, outputs, verify, agent, repeat_until when present). Do NOT
      # re-derive these from memory — plan.yaml is the single source.

      # 1. Collect agent result (wait for background task to complete).
      # 2. Pass outputs to orchestrator done payload.

      # 3. MANDATORY: USAGE CAPTURE — after any agent Task completes, extract from
      #    the result <usage> block:
      #      input_tokens             — from <usage> input_tokens
      #      output_tokens            — from <usage> output_tokens
      #      cache_read_input_tokens  — from <usage> cache_read_input_tokens (if present)
      #      cost_usd                 — from <usage> cost_usd or total_cost_usd (if present)
      #      duration_ms              — from <usage> duration_ms (if present)
      #      tool_calls               — a dict of {tool_name: count} tallied from the
      #                                 agent's tool use blocks in the result (if visible)
      #    Include these under the `usage` key in the `orchestrator done` payload.
      #    If a field is absent from the result, omit it — do not pass 0 or null.
      #    Example record payload usage block:
      #      "usage": {"input_tokens": 45230, "output_tokens": 3210, "duration_ms": 87400,
      #                "tool_calls": {"Read": 12, "Edit": 5, "Bash": 8}}
      #    After recording, assert step_history[-1].usage.input_tokens is non-null/non-zero
      #    for any agent (non-inline) step — record.py enforces this (FR-11).

      orchestrator done state.yaml <<< {step_id, phase, status, outputs, usage, evidence}
  IF action.action == "resume_step":
      # Always log to stderr — even under flags.auto == true — so operators see resume events.
      print(f"RESUMING step {action.step_id} (attempt {action.attempt})", file=sys.stderr)
      # Then execute identically to run_step (action.run present) or run_inline (no action.run).
      spawn agent(action.agent) with prompt=action.instruction, rules=action.rules,
            step_context=action.step_context, inputs=action.inputs,
            expecting=action.expected_outputs,
            resolved_allowed_tools=action.resolved_allowed_tools, env=action.env,
            run_in_background: true  # default; omit for ideator/reviewer
      # Pass action.step_context into the prompt verbatim (goal, merged rules,
      # inputs, outputs, verify, agent, repeat_until when present). Do NOT
      # re-derive these from memory — plan.yaml is the single source.
      # Apply the same MANDATORY USAGE CAPTURE as run_step above.
```

Escalation (agent returns STATUS: escalate_to_architect): record a
step_history entry with `status: escalate_to_architect` — `orchestrator
next` on the following call returns `action: blocked`, which the loop
surfaces. The architect escalation contract (steps/contracts/architect-escalation.md)
defines how to spawn the architect and re-dispatch.

### 5. Phase transitions

After all steps in a phase complete:
- Verify phase-level `verify:` block (commands, assertions, metrics)
- If phase has `requires:` — this was already validated at phase start
- Advance `phase` field in state.yaml to the next phase
- Continue the dispatch loop with the next phase's steps

If `orchestrator next` returns `complete_workflow` AND stderr shows
`WARNING: phase 'X' is complete but workflow_plan has other phases (...)` —
do NOT treat as terminal. Update state.yaml `phase:` field to the next phase
per schema ordering and re-dispatch. This is the driver's responsibility; the
CLI emits a loud hint but does not auto-advance.

### Key rules

- **Always read the step contract YAML before executing** — never execute from memory
- **Always read the agent .md file before spawning** — the agent needs its full prompt
- **State.yaml is the source of truth** — read it before each step to confirm position
- **One step at a time** — complete and record each step before starting the next
- **Follow Error Recovery Contract** (contracts/error-recovery.md) for all failures
- **Follow Resume Token Format Contract** (contracts/resume-token.md) for next_step writes
