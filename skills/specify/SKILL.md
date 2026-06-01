---
name: specify
description: "Create feature specification via the orchestrate workflow. Delegates to /orchestrate for init and a full workflow run. Use when the user says 'specify', 'create spec', 'write specification'."
user-invocable: true
args:
  - name: description
    description: Feature description or feature ID to resume
    required: false
  - name: --bugfix
    description: Use bugfix schema (runs diagnose phase instead)
    type: flag
  - name: --no-tdd
    description: Skip test-first enforcement
    type: flag
---

## Execution

Route to the orchestrate skill. The orchestrate skill owns pre-dispatch init, including state/worktree/artifact-dir setup.

```
orchestrate $ARGUMENTS
```
