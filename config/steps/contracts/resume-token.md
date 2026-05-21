# Resume Token Format Contract

The `next_step` object in state.yaml is the resume convenience pointer.
Session-start hooks (`workflow-state.sh`, `auto-continue.sh`) and the
`/orchestrate` driver read it to know where to continue after a pause or
session boundary.

`next_step` is a **derived** field, not the source of truth. The source of
truth for "what runs next" is per-node `status` in `workflow_plan` (ORC-63).
`record.py` rewrites `next_step` from the DAG-walk (`next_ready_node`) after
every completed record; dispatch never reads `next_step` to make a decision.

## Workflow plan shape

`workflow_plan[phase]` is `{nodes, filtered, verify}` — a single file, no
separate plan file. Each node is
`{id, depends_on?, status, agent, goal, inputs, outputs, rules,
repeat_until?}`. `status` ∈ `{pending, in_progress, completed, skipped}`.
`generate_plan.py` promotes the seeded `active:[ids]` list into `nodes` at
init; after init only dispatch (→ `in_progress`) and record (→ `completed`)
mutate node status.

## Format

```yaml
next_step:
  skill: orchestrate      # which skill to invoke for resume
  phase: implement        # current or next phase name
  step_id: execute-next-task  # next step to execute
  instruction: "Execute next task from tasks.md"  # human-readable from step's intent field
```

## Field Rules

| Field | Required | Format |
|-------|----------|--------|
| `skill` | Yes | Skill name to invoke (e.g., `orchestrate`, `implement`, `complete-feature`). Hooks use this to construct the resume command (`/{skill}`). |
| `phase` | Yes | Phase name (lowercase, e.g., `specify`, `implement`, `complete`). Must match a phase in the current schema. |
| `step_id` | Yes | Step contract ID (e.g., `execute-next-task`, `run-phase-review`). Must be a valid step in the named phase. |
| `instruction` | Yes | Human-readable description from the step's `intent:` field. Displayed in hook messages. |

## Validity Rules

- `next_step` is re-derived from `next_ready_node` after every step completion
  (not just phase boundaries)
- `next_step` is cleared (removed) when `status` transitions to `completed`
- `phase` must reference a phase that exists in the schema loaded from state.yaml's `schema:` field
- `step_id` must reference a node in the phase's `workflow_plan[phase].nodes` list
- If the current step is the last node of the last phase, `next_step` should
  reference the completion step (`archive-completed-change`) or be omitted

## Consumers

- `workflow-state.sh` — reads `skill` and `instruction` for session-start context
- `auto-continue.sh` — reads `skill` and `instruction` for auto-resume messages
- `/orchestrate` skill — reads `phase` and `step_id` to jump directly to resume point
