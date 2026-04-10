# Resume Token Format Contract

The `next_step` object in state.yaml is the critical resume mechanism. Session-start
hooks (`workflow-state.sh`, `auto-continue.sh`) and the `/develop` orchestrator read
this to know exactly where to continue after a pause or session boundary.

## Format

```yaml
next_step:
  skill: develop          # which skill to invoke for resume
  phase: implement        # current or next phase name
  step_id: execute-next-task  # next step to execute
  instruction: "Execute next task from tasks.md"  # human-readable from step's intent field
```

## Field Rules

| Field | Required | Format |
|-------|----------|--------|
| `skill` | Yes | Skill name to invoke (e.g., `develop`, `implement`, `complete-feature`). Hooks use this to construct the resume command (`/{skill}`). |
| `phase` | Yes | Phase name (lowercase, e.g., `specify`, `implement`, `complete`). Must match a phase in the current schema. |
| `step_id` | Yes | Step contract ID (e.g., `execute-next-task`, `run-phase-review`). Must be a valid step in the named phase. |
| `instruction` | Yes | Human-readable description from the step's `intent:` field. Displayed in hook messages. |

## Validity Rules

- `next_step` is written after every step completion (not just phase boundaries)
- `next_step` is cleared (removed) when `status` transitions to `completed`
- `phase` must reference a phase that exists in the schema loaded from state.yaml's `schema:` field
- `step_id` must reference a step in the phase's active step list (post-filtering)
- If the current step is the last in the last phase, `next_step` should reference
  the completion step (`archive-completed-change`) or be omitted

## Consumers

- `workflow-state.sh` — reads `skill` and `instruction` for session-start context
- `auto-continue.sh` — reads `skill` and `instruction` for auto-resume messages
- `/develop` skill — reads `phase` and `step_id` to jump directly to resume point
