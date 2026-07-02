---
name: implement
description: "Run the implement workflow — skips explore/design, goes straight to coding. Use when design artifacts already exist and the ticket is ready to build. Triggers on 'implement', 'start building', 'build this'."
user-invocable: true
args:
  - name: feature-id
    description: Feature ID (e.g., ORC-121). Auto-detected from worktree/branch if omitted.
    required: false
---

## Execution

Run the implement workflow schema. Design artifacts must already exist (design.md, tasks.md).
Includes the automated review gate: ticket-review → run-phase-review (loops back to
implement-tasks on failure) → ticket-qa → learn cycle.

```
orchestrator run $FEATURE_ID --schema implement
```

If no feature-id is provided, detect from current branch or active state.yaml.
