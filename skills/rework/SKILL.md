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

```
orchestrator rework <change-id>
```
