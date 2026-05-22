# Architect Escalation Contract

Defines the protocol for developer agents to escalate design questions to the architect
mid-implementation. Ensures design contradictions, gaps, and ambiguities are resolved
authoritatively rather than silently guessed at or worked around.

## Trigger Conditions

Escalate when the developer encounters ANY of these during task implementation:

1. **Design contradiction** — the task instruction conflicts with design.md
   (e.g., "use X, not Y" in design.md but the task requires Y)
2. **Missing design coverage** — the task requires a decision design.md does not address
   (e.g., a new data flow, error path, or component interaction not covered)
3. **Scope ambiguity** — it is genuinely unclear whether a behavior falls inside or outside
   the current task's scope, and the wrong choice would cascade into other tasks
4. **Architectural dependency** — implementing the task requires a structural decision that
   will affect other tasks or future phases (e.g., shared interface shape, state layout,
   cross-cutting concern)

## What NOT to Escalate

The developer resolves these independently — do not escalate:

- **Implementation details** — which loop structure, variable names, internal helper design
- **Test strategy** — how to structure tests, what to mock, test helper organization
- **Library usage** — which method to call, how to use an API within an already-chosen library
- **Minor uncertainty** — anything answerable by re-reading design.md carefully
- **Retry failures** — test failures, build errors, verification failures follow the Error
  Recovery Contract (contracts/error-recovery.md), not this protocol

## Escalation Format

When escalating, the developer returns this structured status block:

```
STATUS: escalate_to_architect
type: <contradiction|missing_coverage|scope_ambiguity|architectural_dependency>
task_id: T-<N>
context: |
  <2-4 sentences: what the task requires, what design.md says, why they conflict>
question: |
  <single, concrete question the architect must answer to unblock implementation>
attempted: |
  <what the developer already tried or considered — prevents the architect re-deriving
  what the developer already knows>
```

### Example

```
STATUS: escalate_to_architect
type: contradiction
task_id: T-7
context: |
  T-7 requires writing the result to a shared cache keyed by user_id. design.md (§ Data
  Flow) says all cache writes go through the CacheManager abstraction. But the task file
  path list includes cache.ts directly and the task description says "write directly".
  These two cannot both be correct.
question: |
  Should T-7 use CacheManager.set() (design.md path) or write to cache.ts directly
  (task description path)?
attempted: |
  Re-read design.md § Data Flow and tasks.yaml T-7. The discrepancy is in design.md line 42
  vs tasks.yaml T-7 files section. Both are explicit. This is not a misreading.
```

## Orchestrator Handling

When the developer returns `STATUS: escalate_to_architect`, the orchestrator:

1. **READ** `agents/architect.md` (Mode 3: Implementation Consultation)
2. **READ** `contracts/architect-escalation.md` (this file)
3. **SPAWN** architect agent with the following context bundle:
   - `design.md` — full design (includes Acceptance Criteria)
   - The escalation block (type, task_id, context, question, attempted)
   - `tasks.yaml` with current task list (use step_history for completion status)
4. **WAIT** for architect response
5. **IF** architect provides `DESIGN_AMENDMENT`: write updated design.md to disk
6. **IF** architect provides `TASK_CHANGES`: update tasks.yaml accordingly
7. **RECORD** escalation event in state.yaml `escalation_events` (see § State Recording)
8. **RE-SPAWN** developer agent with the original task prompt plus architect decision
   appended as "Architect Decision (escalation resolved):"
9. **CONTINUE** the same execute-one-task step — do NOT advance, do NOT increment retries

## Architect Response Format

The architect responds with this structured block:

```
DECISION: <concrete answer to the developer's question — one unambiguous directive>
RATIONALE: |
  <why this decision — grounded in design.md or simplicity principle>
DESIGN_AMENDMENT: |
  <diff or prose update to design.md that captures this decision for future reference>
  — OR —
  none
TASK_CHANGES: |
  <any changes to tasks.yaml: amended task descriptions, new tasks, removed tasks>
  — OR —
  none
```

### Amendment Principle

The architect's answer should make implementation simpler, not harder. If the question
exposes a real gap in design.md, amend design.md to close it — the amendment becomes
the authoritative record so other tasks don't hit the same gap.

## State Recording

Every escalation is recorded in `state.yaml` under `escalation_events`:

```yaml
escalation_events:
  - task_id: "T-7"
    type: contradiction
    question: "Should T-7 use CacheManager.set() or write to cache.ts directly?"
    decision: "Use CacheManager.set() — task description was stale, design.md is authoritative"
    design_amended: true
    tasks_changed: false
    timestamp: "2026-04-11T10:00:00Z"
```

### escalation_events Field Schema

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `task_id` | string | Yes | Task that triggered the escalation (e.g., `T-7`) |
| `type` | enum | Yes | One of: `contradiction`, `missing_coverage`, `scope_ambiguity`, `architectural_dependency` |
| `question` | string | Yes | The concrete question that was escalated |
| `decision` | string | Yes | The architect's decision (DECISION field verbatim) |
| `design_amended` | boolean | Yes | Whether design.md was updated |
| `tasks_changed` | boolean | Yes | Whether tasks.yaml was updated |
| `timestamp` | string | Yes | ISO 8601 timestamp when escalation was resolved |

## Retry Interaction

Escalation does NOT count as a retry. The developer resumes the same task (T-N) with
the architect's decision appended as additional context. The `retries.T-N` counter in
state.yaml is not incremented. The task is not marked failed.

This ensures an escalation cycle — developer raises concern, architect resolves it,
developer implements — costs no retry budget. Only verification failures consume retries.
