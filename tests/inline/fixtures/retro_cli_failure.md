# Retro: orc-91 test fixture — fail-soft on backlog CLI errors

## ISSUE-7 — First issue triggers simulated create failure

- **category**: metrics-gap
- **severity**: cosmetic
- **detail**: Stub will fail the first backlog task create call.
- **fix_direction**: Ensure helper logs ERROR and continues processing remaining issues.

## ISSUE-8 — Second issue should still create after first fails

- **category**: telemetry-drift
- **severity**: cosmetic
- **detail**: Independent dedup key; must still file when the first create fails.
- **fix_direction**: Read metrics from step_events instead of empty step_history usage blocks.
