---
feature-id: generate-plan-yaml-at-init
---

# Tasks: Generate plan.yaml at workflow-init

Light mode — no preceding test tasks required (tdd_required=false). Each task self-verifies.

## T-1: Write generator module

- **File**: `config/scripts/orchestrator_next/generate_plan.py` (new)
- **Why**: Core merger. Reads state.yaml + schema + project.yaml + step contracts, applies 5-tier merge from `rule-merge.md`, emits `plan.yaml`.
- **Do**:
  - Import `parser` and `resolver` from `orchestrator_next`.
  - `generate_plan(state_yaml_path)` — public API; returns `None`, writes file.
  - `_build_step_block(step_id, phase_def, schema, project, state)` — per-step merge.
  - `_merge_rules(step_entry, contract, phase_def, schema, project, flags)` — 5-tier precedence with named-rule dedupe.
  - `_resolve_phase(schema, phase_name)` — handles `include: _<name>` resolution.
  - `_write_yaml_stable(obj, path)` — `yaml.safe_dump` with `sort_keys=False` + explicit ordering.
  - `main()` — argparse entry point, `python -m orchestrator_next.generate_plan <state_path>`.
- **Verify**:
  - `python -m orchestrator_next.generate_plan .state/generate-plan-yaml-at-init/state.yaml` writes `.state/generate-plan-yaml-at-init/plan.yaml`.
  - The emitted plan has `phases: [specify, implement, complete]` with active-only steps.
  - `--light` flag drops `explore`, `ux-design`, `run-phase-review`, `run-ux-critique` from the step lists.

## T-2: Tests for generate_plan

- **File**: `config/scripts/orchestrator_next/tests/test_generate_plan.py` (new)
- **Why**: Coverage for merge precedence, filter behavior, byte-stability, include-resolution.
- **Do**: Test cases listed in design.md § Testing Strategy.
- **Verify**:
  - `pytest config/scripts/orchestrator_next/tests/test_generate_plan.py -q` — all 6 tests pass.

## T-3: Wire generator into workflow-init agent

- **Files**: `agents/workflow-init.md`, `config/steps/workflow-init.yaml`
- **Why**: Generator must run as the final sub-step of workflow-init so plan.yaml exists before the first `orchestrator next` call.
- **Do**:
  - `agents/workflow-init.md`: add step 6 — "Generate plan.yaml: run `python -m orchestrator_next.generate_plan $WORKFLOW_STATE_DIR/<slug>/state.yaml`. Verify plan.yaml exists next to state.yaml."
  - `config/steps/workflow-init.yaml`: append `plan_yaml_path` to `outputs`, add a `verify` assertion: `plan.yaml exists at $WORKFLOW_STATE_DIR/<slug>/plan.yaml`.
- **Verify**:
  - Re-read `agents/workflow-init.md` — step 6 present.
  - `yq '.outputs | length' config/steps/workflow-init.yaml` returns 6.
  - `yq '.verify' config/steps/workflow-init.yaml` contains the plan.yaml assertion.

## T-4: Dispatcher injects step_context

- **File**: `config/scripts/orchestrator_next/dispatch.py`
- **Why**: Close the loop — agents receive the pre-merged step block on every spawn.
- **Do**:
  - In `dispatch()`, before returning a `run_step`/`run_inline`/`retry_step` action, load `plan.yaml` from `Path(state_yaml_path).parent / "plan.yaml"`.
  - Look up the matching step block by `(phase, step_id)`.
  - If plan.yaml missing: print error to stderr, `sys.exit(3)`.
  - If step block missing inside plan.yaml: print error to stderr, `sys.exit(3)`.
  - Otherwise attach the block under `action["step_context"]`.
  - Do NOT attach for `verify_phase`, `complete_workflow`, `blocked`.
- **Verify**:
  - `orchestrator next .state/generate-plan-yaml-at-init/state.yaml` — the returned JSON contains `step_context` with merged rules matching plan.yaml.
  - Rename plan.yaml temporarily; re-run; observe exit 3 + stderr message; restore.

## T-5: Tests for dispatcher step_context

- **File**: `config/scripts/orchestrator_next/tests/test_dispatch_step_context.py` (new)
- **Why**: Coverage for both happy path and missing-plan error.
- **Do**: Test cases listed in design.md § Testing Strategy.
- **Verify**:
  - `pytest config/scripts/orchestrator_next/tests/test_dispatch_step_context.py -q` — all 5 tests pass.

## T-6: End-to-end smoke test

- **Files**: none (manual verification)
- **Why**: Confirm real workflow-init run produces plan.yaml and subsequent `orchestrator next` returns step_context.
- **Do**:
  - Run the generator against THIS chore's own state.yaml: `python -m orchestrator_next.generate_plan .state/generate-plan-yaml-at-init/state.yaml`.
  - Confirm `.state/generate-plan-yaml-at-init/plan.yaml` exists and parses.
  - Run `orchestrator next .state/generate-plan-yaml-at-init/state.yaml` and confirm `step_context` key appears in output.
- **Verify**:
  - `plan.yaml` exists.
  - `orchestrator next` stdout contains `"step_context"`.
  - Full test suite: `pytest config/scripts/orchestrator_next/tests/ -q` passes.
