# Tasks: orchestrator doctor health check subcommand

- [x] T-1 Write tests: `doctor.py` check functions (RED — tests must fail)
  - **Why**: Covers FR-1 through FR-7 and FR-8 (exit-code derivation). TDD guardrail: all seven `check_*` functions and `run_all`'s exit-code logic are specified by tests before any implementation.
  - **Files**: `config/scripts/orchestrator_next/tests/test_doctor.py` (new). Tests use `tmp_path` fixtures to construct fake `ORCHESTRATOR_HOME` / `~/.workflows` / `metrics.duckdb` layouts per scenario.
  - Test matrix (one PASS + at least one failure-path per check):
    - `test_check_state_valid_pass` / `test_check_state_valid_malformed_yaml_fails` (AC-5, UC-E1)
    - `test_check_active_vs_archive_pass` / `test_check_active_vs_archive_substring_match_warns` (AC-2, UC-2)
    - `test_check_contracts_pass` / `test_check_contracts_missing_id_fails` / `test_check_contracts_missing_inputs_fails` (AC-3, UC-3)
    - `test_check_inline_scripts_pass` / `test_check_inline_scripts_missing_script_fails` (AC-4, UC-4)
    - `test_check_agent_files_pass` / `test_check_agent_files_inline_sentinel_skipped` / `test_check_agent_files_missing_agent_warns` (AC-7, UC-E3)
    - `test_check_duckdb_schema_pass` / `test_check_duckdb_schema_missing_tool_calls_fails` (AC-6, UC-E2)
    - `test_check_workflow_plans_pass` / `test_check_workflow_plans_missing_contract_warns` / `test_check_workflow_plans_normalizes_dict_and_if_flag`
    - `test_run_all_exit_code_all_pass` (AC-1) / `test_run_all_exit_code_warn_only_is_1` (AC-9) / `test_run_all_exit_code_any_fail_is_2` (AC-10)
  - **Verify**: `pytest config/scripts/orchestrator_next/tests/test_doctor.py -x` runs all the above and each fails with `ImportError` / `AttributeError` / `NameError` (nothing implemented yet). No test errors from fixture setup.

- [x] T-2 Implement: `doctor.py` with 7 check functions + `run_all` + `_format_table` (GREEN) (depends: T-1)
  - **Why**: Satisfies FR-1 through FR-9 and NFR-1 through NFR-5. One module, flat functions, no new deps, ~100 lines.
  - **Files**: `config/scripts/orchestrator_next/doctor.py` (new).
  - **Approach**: `CheckResult` namedtuple, seven `check_*` functions as specified in design.md § Low-Level Design, `run_all(args)` that composes them, `_format_table` using plain string formatting, exit-code derivation `2 if any FAIL else (1 if any WARN else 0)`. Reuse `parser.load_state`, `parser._load_contract`, `upsert.ensure_schema`. Each check wraps its I/O in `try/except Exception` and converts exceptions to FAIL results so one broken check cannot mask others. DuckDB expected tables hard-coded as `EXPECTED_TABLES = ("step_events", "tool_calls")`. Workflow-plan step-id normalization inlined in check 7 (three lines, not extracted).
  - **Verify**: All T-1 tests pass green. `python -c "from orchestrator_next.doctor import run_all, CheckResult"` imports cleanly. `wc -l config/scripts/orchestrator_next/doctor.py` is close to 100 lines excluding imports/docstrings.

- [x] T-3 Write tests: CLI wiring for `orchestrator doctor` subcommand (RED) (depends: T-2)
  - **Why**: Covers FR-10 and AC-8 (ORCHESTRATOR_HOME required, hard error before any check). Also validates that `_doctor_main` is importable and returns an int exit code.
  - **Files**: append to `config/scripts/orchestrator_next/tests/test_doctor.py`.
  - **Tests**:
    - `test_doctor_main_without_orchestrator_home_errors` — unset env, call `_doctor_main([])`, assert non-zero return and stderr contains `ORCHESTRATOR_HOME`.
    - `test_doctor_main_with_valid_env_returns_int` — monkeypatch env to a valid fixture, call `_doctor_main([])`, assert return is `0`, `1`, or `2`.
    - `test_doctor_main_help_flag` — call `_doctor_main(["--help"])`, assert `SystemExit` with code 0 (argparse default).
  - **Verify**: Tests fail with `ImportError` on `_doctor_main` (not yet exported from `doctor.py`) or with dispatch not wired in `bin/orchestrator`.

- [x] T-4 Wire `_doctor_main` into `bin/orchestrator` (GREEN) (depends: T-3)
  - **Why**: Satisfies FR-10 and completes the CLI surface. One edit adds `"doctor"` to the subcommand guard tuple (line ~192), one edit adds the dispatch branch (line ~197-199) modeled on the existing `cost` branch.
  - **Files**: `bin/orchestrator` (modify — two small edits). `config/scripts/orchestrator_next/doctor.py` (export `_doctor_main` symbol if not already present from T-2).
  - **Approach**: Exact two-line changes per design.md § CLI Wiring. `_doctor_main` does argparse (no flags in this iteration; parser exists for `--help` and future extension), validates `ORCHESTRATOR_HOME`, delegates to `run_all`.
  - **Verify**: All T-3 tests pass green. `orchestrator doctor --help` prints usage. `orchestrator nonexistent` still errors on unknown subcommand.

- [x] T-5 Integration smoke test: run `orchestrator doctor` against this repo (depends: T-4)
  - **Why**: Validates the command works end-to-end on a real installation. Catches any divergence between fixture assumptions in T-1/T-3 and the actual repo layout (glob patterns, paths, archive basename substring rule).
  - **Files**: None (manual / shell-level verification). Optionally add `test_smoke_doctor_on_self` that invokes `_doctor_main([])` with the current repo as `ORCHESTRATOR_HOME`.
  - **Approach**: Set `ORCHESTRATOR_HOME=/Users/spidey/code/orchestrator`, run `orchestrator doctor`, observe the table output. Expected: mixture of PASS and possibly WARN (given five of eleven active `~/.workflows/` entries are documented to match archive basenames per discovery). No unhandled exceptions.
  - **Verify**: Command exits 0, 1, or 2 (not a Python traceback). Output is a readable three-column table. Any FAIL is an actionable row, not a crash.

- [x] T-6 Review checkpoint (phase gate) (depends: T-5)
  - **Why**: Architect signoff gate — confirm all acceptance criteria are covered, no spec drift, simplicity preserved, line budget respected.
  - **Verify**: `pytest config/scripts/orchestrator_next/tests/test_doctor.py` all green with coverage >= 90% for `doctor.py`. `wc -l config/scripts/orchestrator_next/doctor.py` within scope budget (~100 lines of logic). Diff reviewed against spec.md acceptance criteria AC-1 through AC-10, all mapped to at least one test. No new third-party deps in `requirements.txt` / `pyproject.toml`.

<!-- Status markers: [ ] pending, [→] in-progress, [x] done, [~] skipped -->
<!-- (depends: T-xxx) = dependency -->
<!-- TDD: test tasks (RED) always precede implementation tasks (GREEN) -->
<!-- Coverage target: >= 90% at each phase gate -->
