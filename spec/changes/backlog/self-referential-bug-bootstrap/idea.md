# Self-referential bugfix bootstrap (ISSUE-19)

## Idea
When a bugfix's change_description references files under `config/scripts/orchestrator_next/` or other dispatcher-critical paths, the dispatcher can't rely on its own current behavior during the fix. Two options:

1. **Schema hint**: bugfix schema detects self-referential changes (grep change_description for paths under `config/scripts/` or `bin/orchestrator`). fix-plan.md gains a "Bootstrap Constraint" section the driver reads, warning which workarounds to apply mid-run.

2. **Step marker**: allow `repeat_until` (or a new `bootstrap_before`) on a step to apply the fix from the working tree before running the step — i.e., re-import record.py after T-2 lands but before T-3 runs. Risky; prefer (1).

## Why Now
`live-telemetry-and-repeat-until-enforcement` fixed ISSUE-16 (dispatcher ignoring repeat_until) but had to work around ISSUE-16 during its own execution. The driver manually re-pointed `next_step.step_id = execute-next-task` between each of T-2..T-6, then demoted T-1's status from completed to in_progress so the dispatcher didn't treat it as done. Six manual state edits that should be automated or avoided.

## Prototype
```
### Bootstrap Constraint
This feature modifies `config/scripts/orchestrator_next/record.py`.
During implement phase, the dispatcher runs the PRE-fix version of record.py
until the workflow run is complete. Driver workarounds:
  - After each execute-next-task, re-point next_step.step_id = execute-next-task
    until tasks.md has no `- [ ]` lines.
  - Group dependency chains into single spawns to minimize re-point operations.
  - Do not call `orchestrator record` between tasks within a chain.
```

## Priority
- User value: 6/10 (rare but painful when hit)
- Strategic fit: 7/10 (infra self-improvement)
- Technical leverage: 6/10
- Effort: small
- **Score: 6.3**

## Source
spec/changes/archive/2026-04-19-live-telemetry-and-repeat-until-enforcement/retro.md §ISSUE-19
