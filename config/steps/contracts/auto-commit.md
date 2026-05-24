# Auto-Commit Convention

After each task passes verification in the `execute-one-task` step, the agent
commits the changes immediately. This ensures long implementation phases survive
session interruptions — each completed task is durably saved.

## Commit Message Format

```
<prefix>(<change-id>): T-<N> <task title>
```

## Schema-to-Prefix Mapping

| Schema / Flag | Prefix |
|---------------|--------|
| `feature` | `feat` |
| `feature --light` | `chore` |
| `bugfix` | `fix` |

When running `feature --light`, use `chore:` as the commit prefix — it matches
the conventional-commits category (housekeeping, config, renames, small
cleanup) and preserves the signal that this change is scope-light.

## Rules

- **Commit only on success**: Only commit after the task's verification passes.
  Never commit failing state.
- **Scope**: Stage only files changed by the task.
  Do not `git add -A` — this prevents accidentally committing unrelated changes.
- **Squash-friendly**: These per-task commits may be squashed during
  `archive-completed-change` or at merge time. The granularity is for resilience,
  not final history.
- **Co-author**: Include the standard Co-Authored-By trailer.
- **Skip if no changes**: If verification passes but `git status --porcelain` shows
  no modified files (e.g., the task was a verification-only task), skip the commit.

## Example

```
feat(HL-193): T-2 Add retry logic to API client

Co-Authored-By: Claude <noreply@anthropic.com>
```

## Consumers

- `execute-one-task` — produces commits per this convention
- `archive-completed-change` — may squash per-task commits at completion
- `run-phase-review` — can verify commits exist for completed tasks
