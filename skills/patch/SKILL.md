---
name: patch
description: "Run the patch workflow — implement a small, well-scoped change with no design phase. Use when the ticket already has a clear implementation path and design artifacts are not needed. Skips explore, design, and design-review."
user-invocable: true
args:
  - name: feature-id
    description: Feature ID to patch (e.g., ORC-121). Auto-detected from worktree/branch if omitted.
    required: false
---

## Execution

Route to the orchestrate skill with the patch schema.

```
orchestrate $ARGUMENTS --schema patch
```
