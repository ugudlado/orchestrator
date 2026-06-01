---
name: implement
description: "Execute implementation via the orchestrate workflow. Delegates to /orchestrate for a full workflow run. Use when the user says 'implement', 'start building', 'continue feature'."
user-invocable: true
args:
  - name: feature-id
    description: Feature ID (e.g., HL-170). Auto-detected from worktree/branch if omitted.
    required: false
---

## Execution

Route to the orchestrate skill. The orchestrate skill runs the full workflow via the shell driver.

```
orchestrate $ARGUMENTS
```
