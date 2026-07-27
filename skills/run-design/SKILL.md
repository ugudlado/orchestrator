---
name: run-design
description: "Run the design workflow schema (explore → design → design-review). Prefer /orchestrate --schema design. The design skill-step lives under config/steps/design."
user-invocable: true
args:
  - name: feature-id
    description: Feature ID to design (e.g., ORC-122). Auto-detected from worktree/branch if omitted.
    required: false
---

## Execution

Route to the orchestrate skill with the design schema.

```
orchestrate $ARGUMENTS --schema design
```
