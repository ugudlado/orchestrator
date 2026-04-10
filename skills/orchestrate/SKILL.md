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
REPO_NAME=$(basename "$(git rev-parse --show-toplevel)")
REPO_ROOT=$(git rev-parse --show-toplevel)
ORCHESTRATOR_HOME=${ORCHESTRATOR_HOME:-$HOME/.config/spec}
WORKFLOW_STATE_DIR=$ORCHESTRATOR_HOME/changes/$REPO_NAME
```

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

For each phase in the schema, build the active step list using the resolved flags:
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

IF step has agent: field:
  SPAWN sub-agent via Agent tool:
    - subagent_type: step.agent
    - prompt: step.instruction + collected rules
    - Include: phase context, state.yaml path, CONVENTIONS.md reference
  WAIT for agent result

ELSE (inline step — no agent: field):
  EXECUTE step.instruction directly in this context

AFTER step completes:
  APPEND to state.yaml step_history: {step_id, phase, status, agent}
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
- **Follow Error Recovery Contract** (CONVENTIONS.md) for all failures
- **Follow Resume Token Format Contract** (CONVENTIONS.md) for next_step writes
