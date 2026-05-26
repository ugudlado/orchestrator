---
name: orchestrate
description: "Workflow router — detects intent and loads the right schema. This skill should be used when the user says 'orchestrate', 'start a feature', 'fix a bug', or describes development work that maps to a workflow type (feature, bugfix, autopilot)."
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
WORKFLOW_STATE_DIR=${WORKFLOW_STATE_DIR:-$REPO_ROOT/spec/changes}
WORKTREE_ARTIFACT_DIR="${WORKTREE_ARTIFACT_DIR:-${WORKTREE_ROOT:-$REPO_ROOT}/spec/changes}"
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

### 1. Select workflow

Run the `select-workflow` step contract — `$ORCHESTRATOR_HOME/config/steps/select-workflow.yaml`. This is a pre-init step (state.yaml does not exist yet); follow its `instruction:` block in this conversation, treating its `outputs:` as the result.

The contract owns the matching logic — trigger keywords, CLI-flag binding, resume detection, semantic fallback, halt-on-ambiguity. Do not duplicate it here.

After the step emits `{schema, reason, confidence, considered}`:

1. Read the schema YAML: `$ORCHESTRATOR_HOME/config/workflows/<schema>.yaml`. Workflow files declare `steps:` (and rarely `defaults:` overrides). They do NOT declare their own flags — flag definitions live in `$ORCHESTRATOR_HOME/config/flags.yaml`.
2. Resolve flags by merging in this order:
   - `flags.yaml.gates.<flag>.default` and `flags.yaml.behavioral.<flag>.default` — global defaults.
   - Workflow's `defaults:` block (if present) — overrides for this schema.
   - User-supplied CLI flags resolved via `flags.yaml.cli.<--name>.sets` — final override.
3. Tell the user the schema, the reason it was selected, the confidence tier, and the resolved flags.

### 2. Resume entry point

If the select-workflow step emitted `confidence: resume`, it has already pointed at an active state.yaml. Read its `next_step` (phase + step_id), read its persisted `flags`, and enter the dispatch loop at that point. Tell the user: "Resuming <change_id> at <phase>/<step_id>."

Otherwise this is a new workflow — proceed to sub-step 2.1 to initialize state before entering the dispatch loop. This applies equally to full workflow runs and phase-constrained wrapper calls such as `/specify` (`--phase specify`); artifact-producing steps must never run before init has created the worktree/artifact directory.

#### 2.1 Initialize new workflows

Call the init script:

```
bash skills/orchestrate/scripts/seed-state.sh <slug> <schema> [flag=value ...]
```

Arguments:
- `<slug>` is the change_id / feature slug for this workflow (derived from the request or Linear ticket).
- `<schema>` is the schema name emitted by the select-workflow step (e.g. `bugfix`, `feature`).
- `[flag=value ...]` are any resolved CLI flag overrides (e.g. `auto=true agents=true tdd_required=false`).

After the script exits 0, assert that state.yaml exists with a promoted
workflow plan before proceeding:
- `$WORKFLOW_STATE_DIR/<slug>/state.yaml` exists, and its
  `workflow_plan.main.nodes` is a non-empty list.

`generate_plan` promotes the seeded workflow plan into the `nodes` shape in
place inside state.yaml — there is no separate plan file (ORC-63).

If state.yaml is absent or its workflow plan is unpromoted, the seeder printed an error to stderr — surface it to the user and halt. Do NOT proceed to the dispatch loop with a missing state.yaml (that is the exact bug this step was added to prevent).

The script is the executable init contract. Do not duplicate its workflow-plan, worktree, artifact-dir, or state-stamping logic in this prompt or in wrapper skills. It is idempotent: re-running it when state.yaml already exists exits 0 without overwriting.

### 3. Dispatch loop — HL-287 M5: use the `orchestrator` CLI

The dispatch loop is now a thin wrapper around `orchestrator next` and
`orchestrator done`. Pre/post stamping (started_at / completed_at /
status / usage / evidence) is applied uniformly by the CLI — do NOT
write per-step stamping prose.

**Context-passthrough contract (single-file workflow state, ORC-63).**
`orchestrator next` returns an action JSON that already contains the agent's
full operational contract: `instruction`, `rules` (merged per rule-merge.md),
`step_context` (the `workflow_plan` node block with
goal/inputs/outputs/agent/status/depends_on),
`inputs`, `expected_outputs`, `resolved_allowed_tools`, `env`, plus (for
resume_step) `is_resume` and `started_at`. The driver MUST pass this payload
verbatim into the agent spawn prompt. It MUST NOT re-derive goal, rules, or
outputs from memory — those already live in `step_context` and `rules`.

What the driver SHOULD add to a spawn prompt (beyond the passthrough):
- Feature-specific paths the agent can't get from step_context: state_dir,
  worktree_path, backlog-entry anchor, prior-phase archives (if relevant).
- For developer spawns: pass full tasks.md queue; agent completes all unchecked items before COMPLETION.
- For reviewer spawns: the artifacts + any driver-level findings to verify.

What the driver MUST NOT duplicate in the spawn prompt:
- The step's goal (in step_context.goal).
- The merged rules (in `rules` and step_context.rules — same list).
- Expected_outputs (in `expected_outputs`).
- Verify criteria (in step_context.verify when applicable).
- Step-contract instruction (in `instruction`).

This keeps the `workflow_plan` nodes in state.yaml as the single source of
step contract; duplication invites drift when step contracts update.

```
LOOP:
  # Driver-local: set manual_phase_advance_flag = true when patching phase or
  # next_step outside orchestrator done (e.g. operator forces advance). Reset
  # to false at the start of each loop iteration unless set again that iteration.
  manual_phase_advance_flag = false

  # ORC-45 two-path dispatch protocol:
  #   exit 0 + JSON with `agent` key  → spawn agent
  #   exit 0 + no JSON                → inline script ran and recorded; loop again
  #   exit 1                          → workflow complete
  #   exit 2                          → step blocked
  #   exit 3                          → ContractDispatchError (missing agent: and run:)
  exit_code, stdout = orchestrator next $WORKFLOW_STATE_DIR/$CHANGE_ID/state.yaml

  IF exit_code == 1:
      # Workflow complete — no JSON on stdout.
      # Full cost report + one-line tail summary.
      # Run both; include both in the final message to the user.
      # If cost-report.sh exits non-zero, include script stderr verbatim — do not skip.
      cost_report = run `scripts/cost-report.sh --change-id $CHANGE_ID`
      cost_tail   = run `scripts/cost-report.sh --change-id $CHANGE_ID --tail`
      STOP (workflow done) — include cost_tail as the headline, then cost_report stdout below it

  IF exit_code == 2:
      # Step blocked — no JSON on stdout. Read state.yaml for reason/escalation.
      STOP (read state.yaml step_history[-1] for escalation details, escalate or fix)

  IF exit_code == 3:
      # ContractDispatchError — step has neither agent: nor run:.
      STOP (surface stderr to user, add agent: or run: to the step contract)

  IF exit_code == 0 AND stdout is empty:
      # Inline script ran synchronously and was recorded by CLI — nothing for driver to do.
      # Loop continues to call `orchestrator next` for the next step.
      continue

  IF exit_code == 0 AND "agent" in action:
      action = parse JSON from stdout
      # Show running cost after every step (AC-3, ORC-42). cost_so_far is always
      # present in the action dict (0.0 when DB unavailable). Skip if 0.
      IF action.cost_so_far > 0:
          print(f"  [cost so far: ${action.cost_so_far:.2f}]")

      IF action.get("is_resume"):
          # Resume: always log to stderr — even under flags.auto == true — so operators see resume events.
          print(f"RESUMING step {action.step_id} (attempt {action.attempt})", file=sys.stderr)

      # Agent spawn. Load agent .md from $ORCHESTRATOR_HOME/agents/<action.agent>.md.
      # Spawn with run_in_background: true as the default.
      # Exceptions: ideator and reviewer spawns are short-running and may be foreground.
      spawn agent(action.agent) with prompt=action.instruction, rules=action.rules,
            step_context=action.step_context, inputs=action.inputs,
            expecting=action.expected_outputs,
            resolved_allowed_tools=action.resolved_allowed_tools, env=action.env,
            run_in_background: true  # default; omit for ideator/reviewer
      # Pass action.step_context into the prompt verbatim (goal, merged rules,
      # inputs, outputs, agent, status, depends_on, repeat_until when present).
      # Do NOT re-derive these from memory — the workflow_plan node in
      # state.yaml is the single source.

      # 1. Collect agent result (wait for background task to complete).
      # 2. Parse COMPLETION block from agent result (contracts/done-payload.md).
      #    Map fields verbatim — do NOT extract review_score, verdict, or artifact
      #    content from report prose. Agents write artifact files themselves;
      #    COMPLETION carries machine-readable fields only.
      #    Merge step_id, phase, agent from dispatch context; pass the raw Task tool
      #    result text as agent_task_result (record.py extracts agentId and loads
      #    billing-truth usage from subagent JSONL — do not parse usage or agentId).
      # 3. Compute workflow_issues — driver assembly point (orc-89). Schema and
      #    retro layout: config/steps/contracts/workflow-issues.md. Start with [].
      #    Four driver-detected categories (append one issue object each when true):
      #    (a) Retry-then-success: read state.yaml step_history[-1]; when attempt > 1
      #        and status == completed, append {category: driver-bug, severity:
      #        workaround-applied, surfaced_at: "<phase>/<step_id>",
      #        detail: "retry-then-success on <phase>/<step_id>",
      #        dedup_key: "retry-success:<phase>:<step_id>"}.
      #    (b) Empty usage: when agent_task_result includes an agentId line but the
      #        usage block is empty or reports zero tokens, append {category: telemetry,
      #        severity: workaround-applied, dedup_key: "empty-usage:<phase>:<step_id>"}.
      #    (c) Manual phase advance: when manual_phase_advance_flag is true, append
      #        {category: driver-bug, severity: workaround-applied,
      #        dedup_key: "manual-phase-advance:<phase>"}; clear the flag after emit.
      #    (d) Sentinel drain: for each line in
      #        $WORKFLOW_STATE_DIR/$CHANGE_ID/.pending-issues.jsonl (if present),
      #        parse JSON and append; log malformed lines to stderr and skip.
      #    (e) Agent-supplied: concatenate COMPLETION.workflow_issues verbatim when set.
      # 4. Pipe payload to orchestrator done (driver does not verify tasks/tests).
      #    Attach workflow_issues only when the merged list is non-empty.

      done_payload = {step_id, phase, status, agent, agent_task_result, outputs, evidence}
      IF workflow_issues is non-empty:
          done_payload.workflow_issues = workflow_issues
      done_exit = orchestrator done state.yaml <<< done_payload
      # Full contract: config/steps/contracts/done-payload.md
      IF done_exit == 0:
          rm -f $WORKFLOW_STATE_DIR/$CHANGE_ID/.pending-issues.jsonl
      # On done exit 3 (ContractDispatchError) or any other non-zero done exit, do NOT
      # rm .pending-issues.jsonl — preserve drained sentinel lines for retry.
```

### Workflow issues (orc-89)

The dispatch driver is the single assembly point for `workflow_issues` on each
agent step. Inline scripts surface non-fatal anomalies via
`config/scripts/inline/record-issue.sh` (append to
`.pending-issues.jsonl`); the driver drains that file each loop iteration before
`orchestrator done`. Agents may also list `workflow_issues:` in COMPLETION; the
driver passes them through unchanged. Full payload schema, dedup_key conventions,
category/severity values, and retro.md layout:
`config/steps/contracts/workflow-issues.md`.

Escalation (agent returns STATUS: escalate_to_architect): record a
step_history entry with `status: escalate_to_architect` — `orchestrator
next` on the following call exits 2 (blocked), which the loop surfaces.
The architect escalation contract (steps/contracts/architect-escalation.md)
defines how to spawn the architect and re-dispatch.

### 4. Phase transitions

All schemas (feature, bugfix, autopilot) have a single `main` phase — no advancement needed; `complete_workflow` fires when the last step completes.

### Key rules

- **Always read the step contract YAML before executing** — never execute from memory
- **Always read the agent .md file before spawning** — the agent needs its full prompt
- **State.yaml is the source of truth** — read it before each step to confirm position
- **One step at a time** — complete and record each step before starting the next
- **Follow Error Recovery Contract** (contracts/error-recovery.md) for all failures
- **Follow Resume Token Format Contract** (contracts/resume-token.md) for next_step writes
