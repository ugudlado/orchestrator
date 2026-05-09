# Tasks: Metrics capture and implement-phase streamlining

- [x] T-1 Write tests: Phase 5 commits subagent rows + driver_session at mark-change-completed (RED)
  - **Why**: FR-1, AC-1, AC-8 — covers D1b regression
  - **Verify**: New test in `config/scripts/orchestrator_next/tests/test_phase5_subagent_write.py` runs and FAILS (red); failure is "subagent rows missing from step_events after mark-change-completed completion"

- [x] T-2 Implement: extend Phase 5 in record.py to call `_write_driver_session` and `_write_subagent_events` inside the same transaction (depends: T-1)
  - **Why**: FR-1, FR-5 — fixes D1b at the boundary
  - **Verify**: T-1 tests pass (green); `pytest config/scripts/orchestrator_next/tests/test_phase5_subagent_write.py -q` returns 0; no regression in `test_boundary_detection.py` or `test_record_validation.py`

- [x] T-3 Write tests: agent-name rewrite when payload says `inline` but contract declares non-inline agent (RED) (depends: T-2)
  - **Why**: FR-2, AC-2, AC-3 — covers D2b regression
  - **Verify**: Extended cases in `test_record_validation.py` run and FAIL (red): one for non-inline-contract → rewrite, one for inline-contract → no rewrite

- [x] T-4 Implement: `_resolve_contract_agent` helper + rewrite logic in record.py (depends: T-3)
  - **Why**: FR-2 — surfaces and corrects misattribution
  - **Verify**: T-3 tests pass (green); manual smoke: run `pytest config/scripts/orchestrator_next/tests/test_record_validation.py -q` → 0 exit; running `orchestrator done` with a developer-contract payload self-reporting `inline` writes `agent_name='developer'` and emits stderr warning

- [x] T-5 Write tests: `gates.learn=false` filters `run-learn-cycle` out of `workflow_plan.complete.active` (RED) (depends: T-4)
  - **Why**: FR-3, AC-4, AC-7 — covers D3 gate behaviour
  - **Verify**: New test in `test_generate_plan.py` runs and FAILS (red): asserts `run-learn-cycle` absent from active and present in filtered with reason `"flag learn=false"`

- [x] T-6 Implement: register `gates.learn` and `behavioral.simplify` in `config/flags.yaml` + CLI mappings `--no-learn`, `--no-simplify` (depends: T-5)
  - **Why**: FR-3, FR-4, NFR-3 — registry edit only
  - **Verify**: T-5 tests pass (green); `python -c "import yaml; d = yaml.safe_load(open('config/flags.yaml')); assert 'learn' in d['gates'] and d['gates']['learn']['default'] is True; assert 'simplify' in d['behavioral']"` returns 0

- [x] T-7 Update prose: amend `~/.config/orchestrator/config/steps/run-learn-cycle.yaml` learned-rule line 15 to enumerate `flags.learn=false` as a valid skip reason (depends: T-6)
  - **Why**: prevents the next `/learn` cycle from flagging `flags.learn=false` as a rule violation
  - **Verify**: `grep -q 'learn=false for run-learn-cycle' ~/.config/orchestrator/config/steps/run-learn-cycle.yaml` returns 0

- [x] T-8 Update prose: gate FINAL-TASK SIMPLIFY PASS in `~/.config/orchestrator/config/steps/execute-next-task.yaml` lines 146-160 with a `flags.simplify` conditional readable by the developer agent (depends: T-6)
  - **Why**: FR-4, AC-5
  - **Verify**: `grep -q 'flags.simplify is false' ~/.config/orchestrator/config/steps/execute-next-task.yaml` returns 0; the conditional appears BEFORE step 10a

- [x] T-9 Sync repo-managed copies: copy edits from `~/.config/orchestrator/...` to `/Users/spidey/code/orchestrator/config/steps/...` so the source of truth and the symlink target match (depends: T-7, T-8)
  - **Why**: project convention — both copies must be in sync per CLAUDE.md repo wiring
  - **Verify**: `diff ~/.config/orchestrator/config/steps/run-learn-cycle.yaml /Users/spidey/code/orchestrator/config/steps/run-learn-cycle.yaml` returns empty; same for execute-next-task.yaml

- [x] T-10 Review checkpoint (phase gate)
  - **Verify**: full pytest run `pytest config/scripts/orchestrator_next/tests/ -q` exits 0; coverage on changed files in record.py + generate_plan.py ≥ 90%; no new warnings in `pytest -W error::DeprecationWarning`

<!-- Status markers: [ ] pending, [→] in-progress, [x] done, [~] skipped -->
<!-- (depends: T-xxx) = dependency -->
<!-- TDD: test tasks (RED) always precede implementation tasks (GREEN) -->
<!-- Coverage target: >= 90% at each phase gate -->
