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

This is the `complete` workflow subcommand (`config/workflows/complete.yaml`), routed like any other workflow via `orchestrator-run.sh --schema complete` (not a separate driver).

On success: archive on the feature branch (complete phase), then merge to the default branch (unconditional — invoking `orchestrator complete` IS the deliberate merge signal), then remove the worktree. If merge fails, the worktree is kept.
