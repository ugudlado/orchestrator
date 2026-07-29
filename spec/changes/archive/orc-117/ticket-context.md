Task ORC-117 - Make the engine workflow-agnostic — persist/display all step outputs, move step-specific rules out of engine
==================================================
Status: Ready
Priority: medium
Labels: orchestrator, must-have

Description:
--------------------------------------------------
## Problem

The engine currently hardcodes workflow-specific knowledge. It should be agnostic: a step outputs arbitrary key/value pairs, and the engine's job is to **persist them and display them** — nothing about *which* keys or *which* steps.

Coupling found in the engine today:

1. **Whitelisted persistence** — `record._build_history_entry` copies only `_OPTIONAL_STEP_HISTORY_KEYS` (a fixed list: artifacts, review_score, approach, regression, rollback, retry_context, regression_check, blocker, escalation) into `step_history[]`. Any other output key (reason, known_concerns, briefing, implementation_result, …) is validated for routing then **dropped**. (orchestrator_next/record.py:49-60, 556-558)

2. **Whitelisted key-hoist** — run_loop hoists only `("learn_result", "phase_review_report", "discovery_result")` from top-level payload into outputs. (orchestrator_next/run_loop.py:238)

3. **Report shows fixed columns** — workflow_report_step.py renders only step_id/status/attempt/tokens/cost/duration, ignoring whatever the step actually output. (config/steps/workflow-report/workflow_report_step.py:69-78)

4. **Step-specific validators live in the engine** — `_validate_phase_review_output` (`if step_id != "run-phase-review"`), `_validate_design_review_output` / `_normalize_review_payload_status` (`if step_id == "design-review"`, reaching into `design_review_result` / `phase_review_report.verdict`). The engine knows the shape of specific workflow steps. (record.py:108-186)

## Proposed change

**Agnostic persistence + display (the clean part):**
- Persist the **entire** `outputs` dict onto the `step_history[]` entry (or under `entry["outputs"]`), replacing the `_OPTIONAL_STEP_HISTORY_KEYS` whitelist. Engine stores what it's given.
- Drop the run_loop key-hoist whitelist — hoist generically, or require steps to emit under `outputs:` (they already can).
- Report iterates whatever keys are present per step and prints them (truncated), instead of a fixed column set.

**Step-specific rules (needs a decision — see below):**
- The review validators enforce a real invariant: `status: completed` must mean an actual pass, so a rubber-stamping agent can't advance the DAG. That rule shouldn't be deleted, but it shouldn't be `if step_id == "design-review"` in engine code either.
- Option A: move the rule into the step's `contract.yaml` (declarative: "status completed requires outputs.design_review_result == pass") and have the engine apply a generic contract-driven check. Keeps engine agnostic, keeps the guardrail.
- Option B: accept the engine trusts agent-reported `status` blindly and drop the validators — simpler, but removes the anti-rubber-stamp guardrail.
- Recommend A: one small generic "required output value for this status" check driven by contract, replacing three hardcoded functions.

## Relationship to ORC-116
ORC-116 (surface a step "briefing") becomes trivial once persistence is agnostic — the briefing is just another output key that shows up automatically. Do this ticket first; ORC-116 collapses to a prompt change + a report tweak.

## ponytail note
Deleting three hardcoded validators and a whitelist is deletion-over-addition — the win here is *less* engine code, not more. The only new code is one generic contract-driven status check (Option A), and only if we keep the guardrail.

Acceptance Criteria:
--------------------------------------------------
- [ ] #1 step_history[] entries persist the full outputs dict a step emits, not a hardcoded whitelist
- [ ] #2 workflow_report renders whatever output keys each step produced (truncated), with no per-step/per-key knowledge in engine code
- [ ] #3 run_loop no longer hoists a hardcoded key list; steps emit under outputs generically
- [ ] #4 The three step-specific validators (_validate_phase_review_output, _validate_design_review_output, _normalize_review_payload_status) are removed from the engine and replaced by ONE generic contract-driven check: a step's contract.yaml declares a required output value for a given terminal status, and the engine coerces/rejects generically (Option A — locked)
- [ ] #5 Existing pass/needs_work guardrail behavior is preserved: design-review and run-phase-review still reject a `status: completed` that isn't an actual pass, now enforced via their contract.yaml declaration rather than engine code
- [ ] #6 Engine contains no reference to specific step_ids or specific output keys (grep for design-review/run-phase-review/design_review_result/phase_review_report in orchestrator_next/*.py returns nothing outside tests)
