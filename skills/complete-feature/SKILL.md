---
name: complete-feature
description: "Complete feature — verify, signoff, archive. Alias for /develop that runs only the complete phase. Use when the user says 'complete feature', 'finish feature', 'merge to main'."
user-invocable: true
args:
  - name: feature-id
    description: Feature ID (e.g., HL-170). Auto-detected from worktree/branch if omitted.
    required: false
---

## Execution

Route to `/orchestrate` with a `--phase complete` constraint. The schema owns all verification and archive logic.

```
/orchestrate $ARGUMENTS --phase complete
```
