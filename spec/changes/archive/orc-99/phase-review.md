# Phase Review — ORC-99 (`run-phase-review`)

## Verdict

- Verdict: `needs_work`
- Phase: implement (feature workflow gate before `ticket-qa`)
- Reason: phase-boundary verification includes failing required test checks.

## Inputs Reviewed

- `task_execution_result`: from state `implement-tasks` output (`implementation_result: completed`, `tasks_completed: 3`)
- `spec/changes/orc-99/design.md`
- `spec/changes/orc-99/tasks.yaml`
- `spec/project.yaml` scoring config (`critical_cap: 5`, `important_cap: 7`, `green_base: 9.25`)

## Pre-Score Guards

- Pending tasks check: PASS (`tasks.yaml` had no `status: pending` tasks before fix task generation)
- Quarantine review (implement-only): PASS (`quarantine_events` absent in state)
- Fixture mutation guard: PASS (`git diff -- tests/fixtures/` returned empty)
- TODO/FIXME/placeholder scan in change artifacts: PASS (none found)

## Verification Evidence

### Verify Commands

- PASS: `pytest orchestrator_next/tests/test_learner_overlay_lifecycle_contract.py -v`
  - Evidence: 4 passed.
- PASS: `pytest orchestrator_next/tests/test_learner_overlay_routing.py -v`
  - Evidence: 4 passed.
- PASS: `python -m compileall orchestrator_next`
  - Evidence: package compiled without syntax/build errors.
- FAIL (critical): `pytest orchestrator_next/tests/ -q`
  - Evidence: 6 failed, 439 passed, 3 skipped, 6 xfailed.
  - Failing tests:
    - `test_graph_workflow.py::test_render_workflow_graph_produces_mermaid[telemetry]` (`config/workflows/telemetry.yaml` not found)
    - `test_operator_workflows.py::test_step_params_from_contract` (missing `TELEMETRY_SCOPE`)
    - `test_operator_workflows.py::test_merge_step_env_os_environ_overrides_contract` (missing `TELEMETRY_SCOPE`)
    - `test_step_runner.py::test_capture_test_baseline_script_uses_step_dir_env` (`config/steps/capture-test-baseline/script.sh` not found)
    - `test_workflow_schemas_load.py::test_schema_ends_at_expected_terminal[feature-ticket-qa]` (schema tail mismatch)
    - `test_workflow_schemas_load.py::test_schema_ends_at_expected_terminal[bugfix-ticket-qa]` (schema tail mismatch)

### Verify Assertions

- No explicit schema `verify.assertions` block was present in the loaded step/schema state for this node.

### Verify Metrics

- Applied threshold: review minimum score = `quality_bar.min_phase_review_score = 9`.
- Computed overall review score: `5` (below threshold).

## AC Verification (implement phase)

- AC-1 PASS: section 5b includes agent overlays in scan target set.
  - Evidence: `skills/workflow-learner/SKILL.md` explicitly includes `.orchestrator/agents/*.md` in 5b target union.
  - Corroborating test: `test_rule_effectiveness_scan_includes_agent_overlay_files`.

- AC-2 PASS: section 5b-decay applies same ineffective thresholds to overlays.
  - Evidence: 5b-decay scan includes `.orchestrator/agents/*.md` and retains thresholds:
    - `hits == 0 AND (K - cycle) > 5`
    - `misses / (hits + misses) > 0.7 ...`
  - Corroborating test: `test_rule_decay_scan_applies_same_thresholds_to_overlay_rules`.

- AC-3 PASS: only learned-stamped overlay content is mutable.
  - Evidence: 5b/5b-decay text constrains edits/removal to `<!-- learned: -->` entries and explicitly preserves manual overlay prose.
  - Corroborating test: `test_overlay_mutation_scope_is_learned_comment_only`.

- AC-4 PASS: tests cover overlay hit increment, miss increment, decay removal, and defaulting behavior.
  - Evidence:
    - lifecycle contract tests passed (4/4) including missing `hits`/`misses` default-to-zero.
    - routing tests passed (4/4), preserving overlay routing invariants.

## Findings

### Critical

1. Phase-boundary test gate is red (`pytest orchestrator_next/tests/ -q` has 6 failures), blocking phase completion.
   - Dimension impact: correctness (critical cap applied).
   - Fix direction: restore workflow/schema and step-env contract consistency so the canonical test suite is green.

## Scoring

- scoring config used:
  - critical_cap: 5
  - important_cap: 7
  - green_base: 9.25

- dimensions:
  - spec_compliance: 9.25
  - correctness: 5
  - security: 9.25
  - simplicity: 9.25
  - code_quality: 9.25

- overall (minimum dimension): 5
- first-pass +1 bonus: not eligible (critical finding present and verify suite failed)

## Baseline Comparison (non-blocking)

- Historical average (`schema=feature`, `metrics.review_score_avg` from archived states): 7.69 (7 samples).
- Current overall: 5.
- Warning: Quality regression: current score 5 is 2+ below historical average 7.69 for this schema/phase.

## Fix Tasks Added

- Added `fix-1` to `spec/changes/orc-99/tasks.yaml` with `status: pending`, depending on `T-3`, scoped only to the failing verification contracts/tests.
