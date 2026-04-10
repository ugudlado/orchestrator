# Tasks — Phase 1 Reliability Improvements

- [x] T-1: Add `task_checkpoint` and `workflow_plan` fields to CONVENTIONS.md State Field Registry
  Verify: grep for `task_checkpoint` and `workflow_plan` in CONVENTIONS.md State Field Registry section — both present with type, producer, and example columns

- [ ] T-2: Add Idempotent Re-Entry section to CONVENTIONS.md
  Verify: CONVENTIONS.md contains "## Idempotent Re-Entry" section with rules for checking partial completion on entry, skipping completed work, and avoiding duplicate step_history entries

- [ ] T-3: Add per-task checkpoint write to execute-next-task.yaml
  Verify: execute-next-task.yaml instruction includes writing `task_checkpoint` to state.yaml after task completion and committing state.yaml + tasks.md atomically

- [ ] T-4: Add idempotent re-entry check to execute-next-task.yaml
  Verify: execute-next-task.yaml instruction includes a step 0 that reads task_checkpoint from state.yaml and resumes from the correct task on re-entry
  depends: T-2

- [ ] T-5: Add workflow_plan enforcement to load-project-context.yaml
  Verify: load-project-context.yaml instruction includes writing full workflow_plan to state.yaml with all phases and their active/filtered step lists, plus validation on resume
  depends: T-1
