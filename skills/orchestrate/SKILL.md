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

### 4. Dispatch loop

For each phase, for each step in the filtered list:

```
READ step contract:  $ORCHESTRATOR_HOME/config/steps/<step_id>.yaml
READ agent definition: $ORCHESTRATOR_HOME/agents/<step.agent>.md  (if step has agent: field)

COLLECT rules for this step:
  - step contract's own rules:
  - schema-level rules_when: (evaluate flags, append matching rules)
  - schema-level extra_rules: (append unconditionally)

RECORD started_at = current ISO 8601 timestamp

IF step contract has pre_execute.approach_required: true:
  REQUIRE the agent (or inline executor) to emit an APPROACH block before any
  other action, per CONVENTIONS.md § Pre-Execute Approach Statement:
    APPROACH:
      files: <paths>
      approach: <one sentence>
      not_doing: <scope exclusion>
  CAPTURE the block into state.yaml step_history[-1].approach verbatim.
  Under --auto, proceed immediately after emitting. Under interactive mode,
  wait for user confirmation unless the parent step has already been approved.

IF step has agent: field:
  RESOLVE host subagent type:
    - Default: subagent_type = step.agent
    - If the current host rejects or does not expose repo-defined agent names,
      use the Codex compatibility map below and inject the full agent definition
      into the prompt:
        architect         -> worker
        developer         -> worker
        workflow-improver -> worker
        debugger          -> worker
        reviewer          -> explorer
        discoverer        -> explorer
        ux-reviewer       -> explorer
        ideator           -> explorer
        humanizer         -> worker
        sonnet-agent      -> worker
        haiku-agent       -> default
    - If no mapping exists, use worker and record the fallback in step_history.
  IF this is a re-spawn after failure (step_history[-1].retry_context exists
     and retries.<step_id> > 0):
    READ step_history[-1].retry_context and build the RETRY_CONTEXT block
    per contracts/error-recovery.md § Retry Context Contract. This block
    will be appended to the prompt below.
  SPAWN sub-agent via the host's agent/subagent tool:
    - subagent_type: resolved host subagent type
    - prompt:
        1. "You are executing orchestrator agent `<step.agent>`."
        2. Full contents of `$ORCHESTRATOR_HOME/agents/<step.agent>.md`
           when using a compatibility fallback; native hosts may rely on the
           registered agent definition.
        3. step.instruction + collected rules
        4. RETRY_CONTEXT block (if this is a re-spawn), appended verbatim
           after a blank line.
    - Include: phase context, state.yaml path, relevant contract files from CONVENTIONS.md § Contract Files
  WAIT for agent result

ELSE (inline step — no agent: field):
  EXECUTE step.instruction directly in this context
  COUNT tool invocations made during execution (Read, Bash, Edit, etc.) — this is tool_uses for the inline step.

RECORD completed_at = current ISO 8601 timestamp

IF agent returns STATUS: escalate_to_architect:
  READ $ORCHESTRATOR_HOME/config/steps/contracts/architect-escalation.md
  READ $ORCHESTRATOR_HOME/agents/architect.md
  SPAWN architect agent (Mode 3: Implementation Consultation) with:
    - spec.md (from $WORKFLOW_STATE_DIR/$CHANGE_ID/spec.md)
    - design.md (from $WORKFLOW_STATE_DIR/$CHANGE_ID/design.md)
    - escalation block (type, task_id, context, question, attempted from agent result)
    - tasks.md with current completion status
  WAIT for architect response
  IF architect response contains DESIGN_AMENDMENT (not "none"):
    WRITE updated design.md to $WORKFLOW_STATE_DIR/$CHANGE_ID/design.md
  IF architect response contains TASK_CHANGES (not "none"):
    UPDATE $WORKFLOW_STATE_DIR/$CHANGE_ID/tasks.md per architect instructions
  RECORD in state.yaml escalation_events (per contracts/architect-escalation.md § State Recording):
    - task_id, type, question, decision, design_amended, tasks_changed, timestamp
  RE-SPAWN developer agent with original step prompt plus architect decision appended:
    "Architect Decision (escalation resolved): <DECISION field>"
  CONTINUE same step (do NOT advance next_step; do NOT increment retries for the task)
  SKIP the AFTER step completes block below for this iteration

AFTER step completes:
  APPEND to state.yaml step_history: {step_id, phase, status, agent, started_at, completed_at}
  If a compatibility fallback was used, preserve the configured agent name in
  `agent:` and add `runtime_agent: <resolved host subagent type>`.

  ### Inline-step usage schema

  IF step had NO agent: field (inline step):
    Write a usage: block with `agent: inline` on the step_history entry:
    ```yaml
    agent: inline
    usage:
      tool_uses: <count of tool invocations made during inline execution>
      duration_ms: <completed_at_epoch_ms - started_at_epoch_ms>
    ```
    - `duration_ms` = milliseconds between `started_at` and `completed_at` timestamps.
    - `tool_uses` = count of tool calls made while executing step.instruction.
    - Token fields (input_tokens, output_tokens, total_tokens) are OMITTED for inline steps.
      Consumers treat absent token fields as 0.
    - `agent: inline` is the canonical marker; the per-agent awk pass in compute-swe-metrics
      aggregates inline steps under the "inline" agent bucket.

  IF step had agent: field, extract usage data from the agent result and add a usage: block.
  Two sources — check both:
    a) **Proxy agents (llm_submit)**: look for a `---llm_usage---` / `---end_usage---` block
       in the result text. Copy the fields directly: input_tokens, output_tokens, total_tokens.
    b) **Native agents (Agent tool)**: the result summary includes token counts
       (e.g. "tokens: 12345 input, 3456 output"). Extract input_tokens, output_tokens,
       and compute total_tokens = input_tokens + output_tokens.
  Also count tool invocations by type name (Read, Bash, Edit, Grep, Write, Glob,
  WebSearch, WebFetch, SendMessage, etc.) from the agent result, and write as:
    tools: {ToolName: count, ...}
  Sum all tool counts as tool_uses.
  Write the complete block:
    ```yaml
    usage:
      input_tokens: <N>
      output_tokens: <N>
      total_tokens: <N>
      cost_usd: <N>
      tool_uses: <N>
      tools:
        Read: <N>
        Bash: <N>
        ...
    ```
  cost_usd comes from the `---llm_usage---` block (proxy agents) or can be omitted (native agents).
  If token data is unavailable, still write what you have (even just tool counts).
  If no tool calls were made, omit tools: or write tools: {}.
  IF step's verify block has evidence_required: true:
    Check step_history[-1].evidence exists and is non-empty (at least one of
    commands/file_checks/counts has content). If missing, treat the step as
    STATUS: blocked per CONVENTIONS.md § Evidence-Required Verification and
    follow Agent Blocked Protocol. Do NOT advance to the next step.
  WRITE next_step to state.yaml pointing to the next step
  IF step has repeat_until: check condition — if not met, re-execute this step
  IF step has verify: check assertions — if failed, follow Error Recovery Contract
```

### 5. Phase transitions

After all steps in a phase complete:
- Verify phase-level `verify:` block (commands, assertions, metrics)
- If phase has `requires:` — this was already validated at phase start
- Advance `phase` field in state.yaml to the next phase
- Continue the dispatch loop with the next phase's steps

### Key rules

- **Always read the step contract YAML before executing** — never execute from memory
- **Always read the agent .md file before spawning** — the agent needs its full prompt
- **State.yaml is the source of truth** — read it before each step to confirm position
- **One step at a time** — complete and record each step before starting the next
- **Follow Error Recovery Contract** (contracts/error-recovery.md) for all failures
- **Follow Resume Token Format Contract** (contracts/resume-token.md) for next_step writes
