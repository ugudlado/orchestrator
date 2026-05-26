# Retro: orc-91 test fixture — explicit backlog_entry slug

## ISSUE-6 — Stale active state.yaml hijacks orchestrate resume
- **category**: driver-bug
- **severity**: blocker
- **detail**: A leftover active state causes orchestrate to resume the wrong change.
- **fix_direction**: Add orchestrator doctor check that lists and flags stale active state files.
- **backlog_entry**: orchestrator-doctor-stale-state-detector
