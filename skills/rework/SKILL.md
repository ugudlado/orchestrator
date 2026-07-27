---
name: rework
description: "QA failed — move ticket back to In Progress. Branch is retained; developer resumes work on it. Use when QA finds issues that need fixes before re-review."
user-invocable: true
args:
  - name: change-id
    description: Change ID (e.g. orc-86).
    required: true
---

## Execution

There is no `orchestrator rework` workflow. Move the ticket back to **In Progress**
yourself and leave a comment explaining what failed QA. The feature branch and
worktree stay intact — the developer resumes on the same branch.

**Backlog.md**

```bash
backlog task edit <ticket-id> -s "In Progress"
backlog task comment <ticket-id> "QA failed: <summary of issues>"
```

**Linear** — use `/linear` to set status to In Progress and add a comment with the
QA findings.

Do not delete the branch or worktree. After fixes, the developer re-runs the
workflow from the existing branch (typically `orchestrator feature <slug>` or
`/run-implement` or `orchestrator run … --schema implement` to continue implementation).
