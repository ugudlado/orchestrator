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
SPEC_CHANGES_DIR=$ORCHESTRATOR_HOME/changes/$REPO_NAME
```

## Execution

Load `$ORCHESTRATOR_HOME/config/guidelines.yaml` to resolve the correct workflow schema for the request, then load and walk that schema. All resume logic, phase gating, and step execution is owned by the schema and its step contracts.
