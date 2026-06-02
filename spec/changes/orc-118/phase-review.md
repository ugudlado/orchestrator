---
feature-id: orc-118
phase: implement
step: run-phase-review
attempt: 1
verdict: incomplete_phase
---

# Phase Review — ORC-118: Move Agent Step Execution into bin/orchestrator
## Verdict: incomplete_phase

**Date:** 2026-06-02
**Phase:** implement
**Reviewer:** run-phase-review

---

## Pending Task Check

Before scoring, tasks.yaml was read to identify any non-completed, non-quarantined tasks.

**Pending tasks (14 of 15):**

| Task | Title | Status |
|------|-------|--------|
| T-2  | Promote parse-completion.py to importable orchestrator_next/parse_completion.py (GREEN) | pending |
| T-3  | Write tests for agent_runner.resolve_route (RED) | pending |
| T-4  | Implement agent_runner.resolve_route + config resolution (GREEN) | pending |
| T-5  | Write tests for agent_runner.build_agent_prompt + resolve_agent_cwd (RED) | pending |
| T-6  | Implement build_agent_prompt + resolve_agent_cwd (GREEN) | pending |
| T-7  | Write tests for agent_runner.invoke_agent_tool (RED) | pending |
| T-8  | Implement invoke_agent_tool + pi settings resolution (GREEN) | pending |
| T-9  | Write tests for agent_runner.run_agent_step (happy + error paths) (RED) | pending |
| T-10 | Implement run_agent_step orchestration + done-payload (GREEN) | pending |
| T-11 | Write test for bin/orchestrator agent-branch dispatch (RED) | pending |
| T-12 | Wire agent branch into bin/orchestrator main() (GREEN) | pending |
| T-13 | Write grep assertion test that run-workflow.sh is a pure loop (RED) | pending |
| T-14 | Strip agent + run_step execution from run-workflow.sh (GREEN) | pending |
| T-15 | Retire bats agent-path coverage; full pytest + e2e gate (phase gate) | pending |

**Completed:** T-1 only (1/15)

**Quarantined tasks:** none

**Quarantine accepted:** none

---

## Decision

14 pending tasks are present in tasks.yaml and are not quarantined in state.yaml. Per the incomplete_phase rule, scoring is skipped and this review returns `status: failed` to route back to implement-tasks via the `on_failure` edge.

T-1 wrote the RED-phase test skeleton for `parse_completion` with `@pytest.mark.xfail(strict=False)` markers. The remaining 14 tasks cover:
- T-2: GREEN — promote parse-completion.py to importable module
- T-3/T-4: RED/GREEN — `resolve_route` (agent→tool routing)
- T-5/T-6: RED/GREEN — `build_agent_prompt` + `resolve_agent_cwd`
- T-7/T-8: RED/GREEN — `invoke_agent_tool` + pi settings
- T-9/T-10: RED/GREEN — `run_agent_step` orchestration + done-payload
- T-11/T-12: RED/GREEN — bin/orchestrator agent-branch wiring
- T-13/T-14: RED/GREEN — run-workflow.sh pure-loop strip + bats assertions
- T-15: phase gate (xfail cleanup + full pytest + e2e)

None of the acceptance criteria (AC-1 through AC-6) can be verified until the core implementation tasks complete.

---

## Fixture Integrity Check

`git diff HEAD -- tests/fixtures/` → no mutations. No fixture restoration required.

---

## Next Step

Return to implement-tasks. Continue from T-2 (first pending task in dependency order).
verdict: incomplete_phase
---

# Phase Review: ORC-118 — Move Agent Step Execution into bin/orchestrator

## Summary

**Verdict: incomplete_phase**

The implement-tasks step was abandoned before any implementation occurred. All 15 tasks in tasks.yaml remain at `status: pending`. No review score is computed per the incomplete-phase guard rule.

## Evidence

### Task Status Check

All 15 tasks have `status: pending`:

| Task | Title | Status |
|------|-------|--------|
| T-1  | Write tests for importable parse_completion module (RED) | pending |
| T-2  | Promote parse-completion.py to importable orchestrator_next/parse_completion.py (GREEN) | pending |
| T-3  | Write tests for agent_runner.resolve_route (RED) | pending |
| T-4  | Implement agent_runner.resolve_route + config resolution (GREEN) | pending |
| T-5  | Write tests for agent_runner.build_agent_prompt + resolve_agent_cwd (RED) | pending |
| T-6  | Implement build_agent_prompt + resolve_agent_cwd (GREEN) | pending |
| T-7  | Write tests for agent_runner.invoke_agent_tool (RED) | pending |
| T-8  | Implement invoke_agent_tool + pi settings resolution (GREEN) | pending |
| T-9  | Write tests for agent_runner.run_agent_step (happy + error paths) (RED) | pending |
| T-10 | Implement run_agent_step orchestration + done-payload (GREEN) | pending |
| T-11 | Write test for bin/orchestrator agent-branch dispatch (RED) | pending |
| T-12 | Wire agent branch into bin/orchestrator main() (GREEN) | pending |
| T-13 | Write grep assertion test that run-workflow.sh is a pure loop (RED) | pending |
| T-14 | Strip agent + run_step execution from run-workflow.sh (GREEN) | pending |
| T-15 | Retire bats agent-path coverage; full pytest + e2e gate (phase gate) | pending |

### Implementation State

- `orchestrator_next/agent_runner.py`: **does not exist** (neither in worktree nor on main)
- `orchestrator_next/tests/test_agent_runner.py`: exists on main (pre-dates this feature), NOT in worktree
- `orchestrator_next/parse_completion.py`: **does not exist**
- `orchestrator_next/scripts/run-workflow.sh`: unchanged (still contains invoke_tool, build_prompt, etc.)
- `bin/orchestrator`: unchanged (still emits JSON for agent steps)

### Reason for Abandonment

The implement-tasks step (`status: abandoned`) recorded the following reason in step_history:

> "Blocked by task contract contradiction: RED tasks cannot satisfy per-task passing verify
> plus file-scope restrictions."

The developer agent identified a genuine TDD contract issue: T-1 requires its verify
command (`pytest orchestrator_next/tests/test_agent_runner.py -k parse_completion -v`) to
**fail** (RED state — the module does not exist yet), but the step contract rule requires
each task's verify commands to exit 0 before it is marked complete. These are mutually
exclusive for RED-phase tasks.

**Resolution:** RED-phase tasks must use `@pytest.mark.xfail(strict=False)` so the verify
command exits 0 while tests are in expected-failure state. tasks.yaml should note this.
T-15 (the phase gate) removes xfail markers and confirms all tests pass green.

### Fixture Check

`git diff HEAD -- tests/fixtures/` — no output. No fixture mutations.

## What Needs to Happen

The implement-tasks step must be re-run. Before the next attempt, tasks.yaml should be
updated to resolve the TDD contract contradiction by annotating RED-phase task verify
commands with xfail guidance so the developer agent knows to mark those tests with
`@pytest.mark.xfail(strict=False)`.

The `on_failure: implement-tasks` edge routes this step back to implement-tasks.
