---
name: run-implement
description: "Run the implement workflow schema. Prefer /orchestrate --schema implement. The implement skill-step lives under config/steps/implement."
user-invocable: true
args:
  - name: feature-id
    description: Feature ID (e.g., ORC-121). Auto-detected from worktree/branch if omitted.
    required: false
---

## Execution

Run the implement workflow schema. Design artifacts must already exist (design.md, tasks.md).
Includes the automated review gate: ticket-review → review (loops back to
implement on failure) → ticket-qa → learn cycle.

```
orchestrator run $FEATURE_ID --schema implement
```

If no feature-id is provided, detect from current branch or active state.yaml.
