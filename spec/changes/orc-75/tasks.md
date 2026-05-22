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

- [x] T-3: Write regression test for skill-runner dispatch and record
  Why: AC-1, AC-2 — proves `agent: skill-runner` dispatches correctly and records cleanly without usage tokens. Must FAIL before the fix.
  Files: config/scripts/orchestrator_next/tests/test_dispatch_skill_runner.py (new)
  Change: New pytest module. (a) Dispatch: build a state.yaml whose next ready node has an `agent: skill-runner` contract; call `dispatch.dispatch()`; assert action["agent"] == "skill-runner", action carries instruction, exit 0. (b) Record: call `record()` with `status: "completed", agent: "skill-runner"`, no usage tokens; assert exit 0 (no agent_step_missing_usage). Mirror harnesses in test_dispatch.py and test_record_validation.py.
  Test scenarios:
    - dispatch of an `agent: skill-runner` step yields action with agent == "skill-runner"
    - action carries instruction, rules, inputs, env
    - record() of a skill-runner step with status completed and no usage tokens → exit 0
    - test fails on current code (record.py rejects non-inline agent with no tokens)
  depends: T-2

- [x] T-4: Fix record.py — exempt skill-runner from the usage guard
  Why: AC-2 — skill-runner steps run in driver context, produce no subagent billing.
  Files: config/scripts/orchestrator_next/record.py
  Change: At record.py:1399 and 1420, extend the inline exemption to cover skill-runner:
    - Line 1399: `contract_agent not in ("inline", "skill-runner")`
    - Line 1420: `agent not in ("inline", "skill-runner")`
  No other record.py changes needed.
  Test scenarios:
    - T-3 record regression test now PASSES
    - existing inline exemption tests unchanged
    - real agent steps (developer, reviewer, etc.) still require usage
  depends: T-3

- [x] T-5: Add skill-runner to doctor.py exemption
  Why: AC-5 — doctor.py checks that agent: values have a corresponding .md file; skill-runner is a sentinel, not a real agent file.
  Files: config/scripts/orchestrator_next/doctor.py
  Change: At doctor.py:123, extend the sentinel check: `if not name or name in ("inline", "skill-runner"):` → skip the agent-file existence check.
  Test scenarios:
    - A contract with `agent: skill-runner` does not produce a missing-agent-file error in doctor
  depends: T-4

- [x] T-6: Convert run-learn-cycle.yaml to agent: skill-runner
  Why: AC-1, AC-2 — the step contract must honestly declare driver-invoked-skill execution.
  Files: ~/.config/orchestrator/config/steps/run-learn-cycle.yaml
  Change: Line 6 `agent: workflow-improver` → `agent: skill-runner`. No instruction change — it already says "invoke /learn".
  Test scenarios:
    - run-learn-cycle.yaml parses and loads as a contract with agent == "skill-runner"
    - dispatching run-learn-cycle yields action with agent == "skill-runner"
  depends: T-5

- [x] T-7: Update orchestrate SKILL.md driver protocol for agent: skill-runner
  Why: AC-2 — the driver must handle a skill-runner action by invoking the skill inline.
  Files: skills/orchestrate/SKILL.md
  Change: In the dispatch LOOP, add a branch: `exit 0 AND "agent" in action AND action.agent == "skill-runner"` → invoke `Skill(...)` per action.instruction in the driver's own context, then call `orchestrator done` with `{step_id, phase, status, agent: "skill-runner", outputs}`. Keep the existing spawn branch for all other agents. Document that skill-runner is NOT spawned.
  depends: T-6

- [x] T-8: Run full test suite — zero new failures
  Why: AC-5 — ensure neither fix regresses dispatch, record, or workflow behavior.
  Files: (verification only)
  Change: Run `python -m pytest config/scripts/orchestrator_next/tests/ -x -q`. Confirm T-1 and T-3 regression tests pass and no prior passing test fails.
  depends: T-7

- [x] T-9: Review checkpoint (phase gate)
  Why: Phase gate — type-check + test + build must pass before signoff.
  depends: T-8

<!-- Status markers: [ ] pending, [x] done -->
<!-- depends: T-N = dependency -->
