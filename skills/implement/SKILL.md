---
name: implement
description: "Execute implementation tasks. Runs only the implement phase of the orchestrate workflow. This skill should be used when the user says 'implement', 'start building', 'continue feature'."
user-invocable: true
args:
  - name: feature-id
    description: Feature ID (e.g., HL-170). Auto-detected from worktree/branch if omitted.
    required: false
---

## Execution

Route to the orchestrate skill with a `--phase implement` constraint. The schema owns all phase-gate and execution logic.

```
orchestrate $ARGUMENTS --phase implement
```
