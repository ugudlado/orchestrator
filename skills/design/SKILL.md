---
name: design
description: "Run the design workflow — produce reviewed design artifacts (design.md + tasks.yaml) ready for implementation. Stops after design-review passes, worktree intact. Use when you want to think through the approach before committing to implementation."
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
