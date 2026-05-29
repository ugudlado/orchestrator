---
feature-id: {change_id}
linear-ticket: {ticket}
---

# Discovery Brief: Feature already completed (rerun short-circuit)

## Feature Summary

This change was already completed and archived. The workflow driver detected a
rerun (`orchestrator run`) and stopped before redoing explore/design work.
Flagged by: **{flagged_by}**.

Prior completion: `{archive_path}` (completed_at: {completed_at}).

## Personas & Actors

N/A — no new work performed on this rerun.

## Use Cases

### Happy Path

UC-1: Operator reruns a completed ticket — system reports prior archive and exits
without re-implementing.

### Error & Edge Cases

N/A

## Scope

### In Scope

- Detect archived completion and close the workflow loop.

### Out of Scope

- Re-implementation or artifact refresh on this rerun.

## UI Direction

N/A — no UI components.

## Key Decisions

- Reuse archived state as source of truth for completion metadata.

## Open Questions

- None
