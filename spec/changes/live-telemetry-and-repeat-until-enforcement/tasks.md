# Tasks: Live cost telemetry + repeat_until enforcement

<!-- TDD required: every fix task is preceded by a failing regression test task. -->
<!-- Order: ISSUE-16 first (dispatch correctness is the blocker for autopilot), then ISSUE-17 (telemetry). -->

## ISSUE-16 — Enforce repeat_until in record.py

- [x] T-1 Write regression test: `_compute_next_step` re-emits `execute-next-task` while tasks.md has unchecked items (RED)
  - **Files**: `config/scripts/orchestrator_next/tests/test_repeat_until.py` (new)
  - **Approach**: One pytest module with three cases.
    1. `test_repeats_when_unchecked_tasks_present`: seed a temp dir with a `state.yaml` (`phase: implement`, `workflow_plan.implement.active: [execute-next-task, run-phase-review]`, `tasks_path: <tmp>/tasks.md`, `worktree_path: <tmp>`, `change_id: repro-16`) and a `tasks.md` with two `- [ ]` lines; drop a stub `execute-next-task.yaml` under an `ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE` dir containing `id:`, `agent:`, `inputs: []`, `outputs: [task_execution_result]`, and `repeat_until: all_tasks_completed`; call `record(state_path, payload)` with `status=completed`; assert `result["next_step"]["step_id"] == "execute-next-task"`.
    2. `test_advances_when_all_tasks_checked`: same fixture but `tasks.md` contains only `- [x]` lines; assert `next_step.step_id == "run-phase-review"`.
    3. `test_no_repeat_when_contract_lacks_repeat_until`: step contract without the `repeat_until:` key; assert advance (baseline behaviour preserved).
  - **Why**: ISSUE-16 — proves `_compute_next_step` honours the contract's `repeat_until` field.
  - **Verify**: `pytest config/scripts/orchestrator_next/tests/test_repeat_until.py -q` — all three FAIL (red). Record stderr in the task log so the failure mode is captured.

- [x] T-2 Implement: parse `repeat_until` + enforce `all_tasks_completed` in `_compute_next_step` (GREEN) — depends on T-1
  - **Files**:
    - `config/scripts/orchestrator_next/parser.py` (extend `StepContract` and `_load_contract`)
    - `config/scripts/orchestrator_next/record.py` (add `_resolve_tasks_md`, `_check_all_tasks_completed`, `_REPEAT_PREDICATES`, and new branch in `_compute_next_step`)
    - `config/steps/execute-next-task.yaml` (add top-level `repeat_until: all_tasks_completed`)
  - **Approach**:
    1. In `parser.StepContract` add `repeat_until: str | None = None`; in `_load_contract` pass `repeat_until=data.get("repeat_until")` to the constructor.
    2. In `record.py` add `_resolve_tasks_md(state_raw)` returning `Path(state_raw["tasks_path"])` if present, else `Path(state_raw["worktree_path"]) / "spec" / "changes" / state_raw["change_id"] / "tasks.md"` (expand `~`); return `None` if path construction is impossible.
    3. Add `_check_all_tasks_completed(state_raw)`: resolve tasks.md; if missing or unreadable, return True; else read text and return `re.search(r"^\s*-\s*\[\s*\]", text, re.MULTILINE) is None`.
    4. Add module-level `_REPEAT_PREDICATES = {"all_tasks_completed": _check_all_tasks_completed}`.
    5. In `_compute_next_step`, BEFORE `completed.add((phase, just_completed_step_id))`, load the contract for `just_completed_step_id` via `load_contract_for_step(just_completed_step_id, state_yaml_path_from_state)`; if the contract has `repeat_until` in `_REPEAT_PREDICATES` and the predicate returns False, return `{"phase": phase, "step_id": just_completed_step_id}` immediately. Pass `state_yaml_path` down from `record()` (one extra arg).
    6. Append `repeat_until: all_tasks_completed` as a top-level key to `config/steps/execute-next-task.yaml` (near `agent:` / `inline:`).
    7. Unknown predicate names: `sys.stderr.write` one-line warning, treat as absent.
    8. Contract load failure: swallow `FileNotFoundError` / `ContractError`, treat as no `repeat_until`.
  - **Why**: ISSUE-16 root cause — `_compute_next_step` never consulted `repeat_until`.
  - **Verify**: T-1's three tests pass. `python3 .tmp/repro-issue-16.py` exits 0. `pytest config/scripts/orchestrator_next/tests -q` green (no other regressions).

- [ ] T-3 Verify ISSUE-16 end-to-end and confirm no collateral damage — depends on T-2
  - **Files**: none modified
  - **Approach**:
    1. Run `python3 .tmp/repro-issue-16.py` — must print `PASS`, exit 0.
    2. Run the full `config/scripts/orchestrator_next/tests/` suite — full green.
    3. Run `config/tests/` shell tests that touch workflow/dispatch behaviour (grep for `execute-next-task` in `config/tests/*.sh` and exercise those) to confirm no regressions.
    4. Record in the task log: repro output, pytest summary, and a one-sentence confirmation that `run-phase-review` is no longer prematurely emitted.
  - **Why**: Bugfix rule — reproduction from diagnosis.md must now produce expected output; zero other tests broken.
  - **Verify**: Two commands both exit 0; pytest summary line pasted into task log.

## ISSUE-17 — Compute cost_usd in record.py when agent usage lacks model/cost

- [ ] T-4 Write regression test: `record()` populates `usage.model` and `usage.cost_usd` when the payload omits them (RED)
  - **Files**: `config/scripts/orchestrator_next/tests/test_record_cost_compute.py` (new)
  - **Approach**: Four pytest cases.
    1. `test_computes_cost_for_native_sonnet_agent`: payload with `agent=developer`, `usage={input_tokens: 22000, output_tokens: 5000}`, no model, no cost_usd; assert the written `state_history[-1].usage.cost_usd == pytest.approx(22000*3.0/1e6 + 5000*15.0/1e6, rel=1e-6)` and `usage.model == "claude-sonnet-4-6"`.
    2. `test_computes_cost_for_native_opus_agent`: `agent=architect`; assert model resolves to `claude-opus-4-7`, cost uses opus rates.
    3. `test_includes_cache_read_tokens_when_present`: usage also has `cache_read_input_tokens: 10000`; assert cost adds `10000 * 0.30/1e6`.
    4. `test_preserves_existing_cost_usd`: payload already contains `cost_usd: 0.42`; assert the stored value is exactly 0.42 and `model` is not overwritten.
    5. `test_skips_when_agent_unresolvable`: `agent=inline` (not in routes.yaml); assert `usage.cost_usd` remains unset/None and no exception.
  - **Why**: ISSUE-17 — proves `record()` computes cost for native agents and does not clobber pre-computed values.
  - **Verify**: `pytest config/scripts/orchestrator_next/tests/test_record_cost_compute.py -q` — cases 1–3 FAIL (red), 4–5 may accidentally pass; document which fail in the task log.

- [ ] T-5 Implement: compute cost_usd + resolve model in `record()` (GREEN) — depends on T-4
  - **Files**:
    - `scripts/routes.yaml` (add `backends:` block)
    - `config/scripts/orchestrator_next/record.py` (add `_load_routes`, `_load_pricing`, `_compute_cost_usd`; call from `record()`)
  - **Approach**:
    1. In `scripts/routes.yaml` add a sibling top-level block:
       ```yaml
       backends:
         native_opus:   claude-opus-4-7
         native_sonnet: claude-sonnet-4-6
       ```
    2. In `record.py` add `_orchestrator_home()` helper returning `Path(os.environ["ORCHESTRATOR_HOME"])` (or repo root fallback), then `@functools.lru_cache(maxsize=1)` `_load_routes()` and `_load_pricing()` loaders.
    3. Add `_compute_cost_usd(agent: str, usage: dict) -> tuple[str | None, float | None]` returning `(model_id, cost_usd)`. Resolution order: agent → `routes.agents[agent]` → if value is in `routes.backends`, use that model_id; else if value matches a key in `routes.models`, use `routes.models[<backend>].model`; else return `(None, None)` and log one-line stderr warning. Look up model_id in `pricing.models`; on miss, fall back to `pricing.default`. Sum: `input_tokens * input/1e6 + output_tokens * output/1e6 + cache_read_input_tokens * cache_read/1e6` (each token field defaults to 0 if absent).
    4. In `record()`, just before building `entry`, if `payload.get("agent")` truthy and `not payload.get("usage", {}).get("cost_usd")`, call `_compute_cost_usd`; if it returns a non-None cost, mutate a *local copy* of usage setting `model` and `cost_usd`, and use that copy in the entry.
  - **Why**: ISSUE-17 root cause — nothing in `orchestrator_next/` computed cost; the fix writes it at the record boundary so both state.yaml and step_events get live data.
  - **Verify**: All five T-4 tests pass. Existing `config/scripts/orchestrator_next/tests/` suite stays green. `python3 .tmp/repro-issue-17.py` exits 0 with `cost_usd ≈ 0.141`.

- [ ] T-6 Verify ISSUE-17 end-to-end: repro passes and `orchestrator cost` shows live numbers — depends on T-5
  - **Files**: none modified
  - **Approach**:
    1. Run `python3 .tmp/repro-issue-17.py` — must print `PASS`, exit 0.
    2. Run an integration-style check: call `record()` twice in a throwaway script with `agent=developer` + token counts, then invoke the existing `orchestrator cost --change-id <tmp>` path (or its test double `test_cost_report.py`'s helper) and confirm the summed `cost_usd` is > 0 and matches the hand-computed expectation within 1e-6.
    3. Run the full `config/scripts/orchestrator_next/tests/` suite — full green.
    4. Record in the task log: repro output, the hand-vs-computed cost pair, and the full pytest summary line.
  - **Why**: Bugfix rule — reproduction produces expected output; cost report demonstrably emerges from the fix (not from pre-existing cached DB rows).
  - **Verify**: Repro exits 0; cost assertion holds; pytest green.
