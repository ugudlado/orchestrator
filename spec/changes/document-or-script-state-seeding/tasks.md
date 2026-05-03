# Tasks — Document or script the initial state.yaml seed for /orchestrate

- [x] T-1: Add regression test `test_seed_state_produces_dispatch_ready_pair` plus `test_seed_state_is_idempotent` and `test_seed_state_fails_without_project_yaml` to `config/scripts/orchestrator_next/tests/test_seed_state.py`. The first test must FAIL before any fix lands (it asserts the seeder script exists and produces state.yaml + plan.yaml from a clean dir, then that `orchestrator next` accepts that pair).
  Verify: `pytest config/scripts/orchestrator_next/tests/test_seed_state.py -k test_seed_state_produces_dispatch_ready_pair` fails with a clear "seed-state.sh not found" or equivalent error message before T-2, and the failure cites the missing script — not an unrelated import error.

- [x] T-2: Implement `skills/orchestrate/scripts/seed-state.sh` per spec.md FR-1 through FR-4 and design.md Components. Include the gate-flag filter inline (matching the rule documented in `agents/workflow-init.md` lines 49-54 and `skills/orchestrate/SKILL.md` §3). The script must write canonical-minimum state.yaml and run `python -m orchestrator_next.generate_plan` to produce plan.yaml.
  Verify: from a clean tmp `WORKFLOW_STATE_DIR`, running `bash skills/orchestrate/scripts/seed-state.sh <slug> bugfix` exits 0 and creates both `state.yaml` and `plan.yaml`. `python -c 'from orchestrator_next.parser import load_state; load_state("<path>")'` succeeds.
  depends: T-1

- [ ] T-3: Update `skills/orchestrate/SKILL.md` Section 2 (lines 63-67) to add a numbered seed sub-step (e.g., "2.1 Seed state for new workflows") that calls `skills/orchestrate/scripts/seed-state.sh <slug> <schema> [flag=value ...]` and verifies state.yaml + plan.yaml exist before the Section 4 dispatch loop runs. Keep the resume branch unchanged. Per spec.md FR-5.
  Verify: `grep -n "seed-state.sh" skills/orchestrate/SKILL.md` returns at least one hit inside Section 2, and a manual read shows the numbered sub-step is placed before Section 4 dispatch loop instructions.
  depends: T-2

- [ ] T-4: Run the regression test suite from T-1 plus the existing orchestrator_next test suite. T-1's primary test must now PASS. Idempotency and fail-loud tests must PASS. No existing test in `config/scripts/orchestrator_next/tests/` may regress.
  Verify: `pytest config/scripts/orchestrator_next/tests/` exits 0 with the new tests in the green count.
  depends: T-3

- [ ] T-5: Manually exercise the fix end-to-end against this very feature's worktree as proof: from a clean tmp dir, run the seeder, then `bin/orchestrator next <state.yaml>`, confirm the action JSON's `step_id` is `workflow-init` (the first active step in the bugfix schema) and exit code is not 3.
  Verify: stdout from `orchestrator next` is valid JSON containing `"step_id": "workflow-init"` and `"action": "run_step"`; exit code is 0.
  depends: T-4

<!-- Status markers: [ ] pending, [→] in-progress, [x] done, [~] skipped -->
<!-- Bugfix + tdd_required: T-1 is the regression test, T-2 is the fix. -->
