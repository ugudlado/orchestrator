---
name: approve-qa
description: "QA passed — merge branch to main, move ticket to Done, delete branch. Use after QA sign-off on a completed feature."
user-invocable: true
args:
  - name: change-id
    description: Change ID (e.g. orc-86). Auto-detected from current branch if omitted.
    required: false
---

## Execution

1. Resolve the change ID from `$ARGUMENTS` or the current git branch name.
2. Run `orchestrator complete <change-id>` — the `complete` workflow
   (`config/workflows/complete.yaml`): learn →
   mark-change-completed → workflow-report →
   archive-completed-change → ticket-done → merge → teardown.
3. Report: archived, merged to main, branch deleted (or any warnings).

```bash
REPO_ROOT=$(git rev-parse --show-toplevel)
CHANGE_ID="${ARGUMENTS:-$(git branch --show-current | sed 's|.*/||')}"
cd "$REPO_ROOT" && orchestrator complete "$CHANGE_ID"
```
