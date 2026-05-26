---
name: approve-qa
description: "QA passed — merge branch to main, move ticket to Done, delete branch. Use after QA sign-off on a completed feature."
user-invocable: true
args:
  - name: change-id
    description: Change ID or path to state.yaml (e.g. orc-86). Auto-detected from current branch if omitted.
    required: false
---

## Execution

1. Resolve the change ID from `$ARGUMENTS` or the current git branch name.
2. Run `scripts/qa-approve.sh <change-id>` from `$REPO_ROOT`.
3. Report: ticket moved to Done, branch deleted (or any warnings).

```bash
REPO_ROOT=$(git rev-parse --show-toplevel)
CHANGE_ID="${ARGUMENTS:-$(git branch --show-current | sed 's|.*/||')}"
bash "$REPO_ROOT/scripts/qa-approve.sh" "$CHANGE_ID" "$REPO_ROOT"
```
