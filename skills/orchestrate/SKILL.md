---
name: orchestrate
description: "Workflow router — detects intent and loads the right schema. Use for any development work: features, bugfixes, chores, spikes, bootstrap, autopilot."
user-invocable: true
args:
  - name: request
    description: >
      What to work on — a description, Linear ticket ID (e.g. HL-170), or a feature ID to resume.
      Schema-specific flags (--no-tdd, --auto, --agents, --focus, etc.) are passed through to the schema.
    required: false
---

## Variables

```
REPO_NAME=$(basename "$(git rev-parse --show-toplevel)")
REPO_ROOT=$(git rev-parse --show-toplevel)
ORCHESTRATOR_HOME=${ORCHESTRATOR_HOME:-$HOME/.config/spec}
SPEC_CHANGES_DIR=$ORCHESTRATOR_HOME/changes/$REPO_NAME
```

## Execution

### 1. Resume Check

Scan `$SPEC_CHANGES_DIR/*/state.yaml` for `status: active` matching the request.
If found → load that schema and jump directly to the recorded phase and step.

### 2. Detect Schema

Read `$ORCHESTRATOR_HOME/config/guidelines.yaml` and classify the request by semantic intent.
Pass any flags from `$ARGUMENTS` through to the schema — flags not declared in the schema are ignored.

### 3. Load and Walk Schema

Load `$ORCHESTRATOR_HOME/config/workflows/$SCHEMA.yaml`.
Execute its phases and steps per the schema's own rules and step contracts.
