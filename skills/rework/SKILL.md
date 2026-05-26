---
name: rework
description: "QA failed — move ticket back to In Progress. Branch is retained; developer resumes work on it. Use when QA finds issues that need fixes before re-review."
user-invocable: true
args:
  - name: change-id
    description: Change ID or path to state.yaml (e.g. orc-86). Auto-detected from current branch if omitted.
    required: false
---

## Execution

1. Resolve the change ID from `$ARGUMENTS` or the current git branch name.
2. Run `scripts/qa-rework.sh <change-id>` from `$REPO_ROOT`.
3. Report: ticket moved to In Progress, branch name for developer to resume on.

```bash
REPO_ROOT=$(git rev-parse --show-toplevel)
CHANGE_ID="${ARGUMENTS:-$(git branch --show-current | sed 's|.*/||')}"
bash "$REPO_ROOT/scripts/qa-rework.sh" "$CHANGE_ID" "$REPO_ROOT"
```
