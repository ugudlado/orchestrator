---
name: doctor
description: 'Run the unified orchestrator health check. Use when user says "doctor", "/doctor", "health check", "check orchestrator health", or before debugging dispatch/contract errors.'
user-invocable: true
---

## Invocation

Run the same health report as `make doctor` — structural checks, workflow graph validation, and symlink/ORCHESTRATOR_HOME diagnostics.

## Variables

```
REPO_ROOT=${REPO_ROOT:-$(git rev-parse --show-toplevel)}
ORCHESTRATOR_HOME=${ORCHESTRATOR_HOME:-$HOME/.config/orchestrator}
```

## Execution

From `$REPO_ROOT`, run:

```bash
make doctor
```

Equivalent direct entry (same output):

```bash
ORCHESTRATOR_HOME="$ORCHESTRATOR_HOME" orchestrator doctor
```

Echo the full stdout table to the user. Exit codes:

- `0` — all checks passed, or warnings only
- `2` — at least one `[FAIL]` (fix before running workflows)
- `3` — `ORCHESTRATOR_HOME` unset

When dispatch fails with `ContractDispatchError` and `Run /doctor`, run this skill and summarize any `[FAIL]` or relevant `[WARN]` rows.
