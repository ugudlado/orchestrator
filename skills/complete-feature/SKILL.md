---
name: complete-feature
description: "Complete feature — verify, signoff, archive. Runs only the complete phase of the orchestrate workflow. This skill should be used when the user says 'complete feature', 'finish feature', 'merge to main'."
user-invocable: true
args:
  - name: feature-id
    description: Feature ID (e.g., HL-170). Auto-detected from worktree/branch if omitted.
    required: false
---

## Execution

Route to the orchestrate skill with a `--phase complete` constraint. The schema owns all verification and archive logic.

```
orchestrate $ARGUMENTS --phase complete
```
