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

```
orchestrator complete <change-id>
```

On success: archive on the feature branch (complete phase), then merge when `flags.merge_to_main`, then remove the worktree by default. If merge fails, the worktree is kept. Pass `--no-teardown` to keep the checkout after a successful merge.
