# Tasks — Fix run-learn-cycle step contract mismatch + abandoned re-dispatch loop

- [x] T-1: Write regression test for the abandoned re-dispatch loop
  Why: AC-3, AC-4 — proves an `abandoned` record leaves the node `in_progress` and causes infinite re-dispatch. Must FAIL before the fix.
  Files: config/scripts/orchestrator_next/tests/test_record_abandoned_node.py (new)
  Change: New pytest module. Build a minimal state.yaml with one phase, a node in `in_progress` status and a matching `in_progress` step_history entry. Call `record()` with `status: "abandoned"`. Assert (a) the node's `workflow_plan` status becomes `completed`, (b) `state.status` becomes `blocked`. Add a second assertion: after the record, `readiness.is_node_ready` returns False for that node. Mirror the harness in test_record_validation.py.
  Test scenarios:
    - abandoned record on an in_progress node → node status flips to completed
    - abandoned record → state.status set to blocked
    - after abandoned record, the node is not re-dispatched (is_node_ready False)
    - test fails on current code (node stays in_progress)

- [x] T-2: Fix record.py — flip abandoned node to completed
  Why: AC-3, AC-4 — terminal an `abandoned` node so the DAG-walk does not re-emit it.
  Files: config/scripts/orchestrator_next/record.py
  Change: At record.py:1597, extend the node-flip gate to include `abandoned`: `if status in ("completed", "recovered", "abandoned"):`. Keep the run-phase-review rework branch and `_repeat_until_pending` branch guarded so they only apply to `completed`/`recovered` — `abandoned` must fall through to the final `else` → `readiness.mark_node_status(state_raw, phase, step_id, "completed")`. Do not touch line 1580 (`state.status = "blocked"` for abandoned).
  Test scenarios:
    - T-1 regression test now PASSES
    - completed/recovered node-flip behavior unchanged
    - run-phase-review rework loop not triggered by abandoned
    - repeat_until step abandoned → still marked completed, not left in_progress
  depends: T-1

- [x] T-3: Create agents/workflow-learner.md from skills/learn/SKILL.md
  Why: AC-1, AC-2 — convert /learn from a driver-context skill into a spawnable agent so run-learn-cycle can declare a real agent: and produce a real agentId for record.py.
  Files: agents/workflow-learner.md (new)
  Change: Create agents/workflow-learner.md with standard agent frontmatter (name: workflow-learner, model: claude-sonnet-4-6, color: green, tools: [Read, Write, Edit, Grep, Glob, Bash, WebSearch]). Body is the full learn pipeline from skills/learn/SKILL.md §1–§5c verbatim — all the same logic (find context, gather inputs, cross-feature analysis, evaluate, route findings, report, rule effectiveness update, decay, quality bar). The skill file is NOT deleted — it becomes a thin user-invocable wrapper (T-4).
  Test scenarios:
    - agents/workflow-learner.md exists with valid frontmatter
    - Body contains all required sections (Find Context, Gather Inputs, Route Findings, Report)
  depends: T-2

- [x] T-4: Convert skills/learn/SKILL.md to a thin agent-spawning wrapper
  Why: Users invoke /learn interactively — the entry point must remain. It delegates to workflow-learner rather than running inline.
  Files: skills/learn/SKILL.md
  Change: Replace the full pipeline body with a short instruction: resolve the feature-id from $ARGUMENTS (same logic as current §1), then spawn the workflow-learner agent with the feature-id and --scope args passed through. Remove all pipeline prose — that now lives in the agent.
  Test scenarios:
    - SKILL.md retains frontmatter (user-invocable: true, args)
    - Body says to spawn workflow-learner, not run inline
  depends: T-3

- [x] T-5: Convert run-learn-cycle.yaml to agent: workflow-learner
  Why: AC-1, AC-2 — the step contract must declare the real agent that executes it.
  Files: ~/.config/orchestrator/config/steps/run-learn-cycle.yaml
  Change: `agent: workflow-improver` → `agent: workflow-learner`. No instruction change needed — the agent carries its own pipeline. Verify the instruction still makes sense as the spawn prompt (trim if it now duplicates the agent body).
  Test scenarios:
    - run-learn-cycle.yaml parses with agent == "workflow-learner"
    - orchestrator next dispatches run-learn-cycle with action["agent"] == "workflow-learner"
  depends: T-4

- [x] T-6: Run full test suite — zero new failures
  Why: AC-5 — ensure the abandoned fix (T-2) and agent conversion don't regress anything.
  Files: (verification only)
  Change: Run `python -m pytest config/scripts/orchestrator_next/tests/ -x -q`. Confirm T-1 regression test passes and no prior passing test fails.
  depends: T-5

- [x] T-7: Review checkpoint (phase gate)
  Why: Phase gate — type-check + test + build must pass before signoff.
  depends: T-6

<!-- Status markers: [ ] pending, [x] done -->
<!-- depends: T-N = dependency -->
