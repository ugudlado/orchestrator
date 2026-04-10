---
feature-id: reliability-phase1
linear-ticket: none
---

# Chore: Phase 1 Reliability Improvements

## What

Add three reliability foundations to the orchestrator's step contracts and conventions:

1. **Per-task checkpoint** — `task_checkpoint` field in state.yaml written after each task completion, committed atomically with tasks.md
2. **Idempotent re-entry convention** — New section in CONVENTIONS.md requiring steps to check for partial completion on entry
3. **Workflow plan at init enforcement** — `load-project-context` writes full `workflow_plan` to state.yaml at init time (enforces existing learning `workflow-plan-upfront`)

Files modified:
- `config/steps/CONVENTIONS.md` — State Field Registry additions, new Idempotent Re-Entry section
- `config/steps/execute-next-task.yaml` — per-task checkpoint write, idempotent re-entry check
- `config/steps/load-project-context.yaml` — workflow_plan enforcement at init

## Why

The orchestrator's current state management has gaps that undermine reliability:
- If a crash occurs between task completion and state recording, there's no way to know which tasks finished
- Re-running a step after partial completion can produce duplicate effects (no idempotency)
- The `workflow-plan-upfront` learning exists in project.yaml but isn't enforced structurally

These are foundational — quality gates (Phase 2) and agent collaboration (Phase 3) depend on reliable state.

## Acceptance Criteria

- AC-1: CONVENTIONS.md State Field Registry includes `task_checkpoint` with type, producer, and example
- AC-2: CONVENTIONS.md has an "Idempotent Re-Entry" section with clear rules for step re-execution
- AC-3: `execute-next-task.yaml` instruction includes checkpoint write after task completion (step 6 modified)
- AC-4: `execute-next-task.yaml` instruction includes idempotent re-entry check as step 0
- AC-5: `load-project-context.yaml` instruction includes `workflow_plan` write/validation step
- AC-6: CONVENTIONS.md State Field Registry includes `workflow_plan` with type, producer, and example
