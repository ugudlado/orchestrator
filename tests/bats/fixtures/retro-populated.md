# Retro: workflow issues surfaced during orc-fixture-populated

<!-- Appended by record.py when step payloads include workflow_issues. -->

## ISSUE-1 — Missing step contract files
- **category**: missing-contract
- **severity**: blocker
- **surfaced_at**: implement/task-T-1
- **recorded_at**: 2026-05-26T12:00:00Z
- **detail**: Schema lists a step with no contract file on disk.
- **fix_direction**: Add contract validation at workflow init.

## ISSUE-2 — Sandbox blocks mktemp
- **category**: sandbox-block
- **severity**: cosmetic
- **surfaced_at**: diagnose/preview-route
- **recorded_at**: 2026-05-26T12:01:00Z
- **detail**: Inline script cannot write to /var/folders temp path.
- **fix_direction**: Prefix mktemp with ${TMPDIR:-/tmp}.

## ISSUE-3 — Dispatch retry storm
- **category**: dispatch-bug
- **severity**: workaround-applied
- **surfaced_at**: implement/execute-one-task
- **recorded_at**: 2026-05-26T12:02:00Z
- **detail**: Driver re-pointed next_step manually between tasks.
- **fix_direction**: Bootstrap constraint for self-referential bugfixes.
