---
name: specify
description: "Create feature specification. Runs only the specify (or diagnose) phase of the orchestrate workflow. This skill should be used when the user says 'specify', 'create spec', 'write specification'."
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

Route to the orchestrate skill with a `--phase specify` constraint. The orchestrate skill owns pre-dispatch init, including state/worktree/artifact-dir setup.

```
orchestrate $ARGUMENTS --phase specify
```
