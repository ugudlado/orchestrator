---
name: specify
description: "Create feature specification. Alias for /develop that runs only the specify (or diagnose) phase. Use when the user says 'specify', 'create spec', 'write specification'."
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
  - name: --no-linear
    description: Skip Linear ticket creation
    type: flag
---

## Execution

Route to `/orchestrate` with a `--phase specify` constraint. The schema owns all execution logic.

```
/orchestrate $ARGUMENTS --phase specify
```
