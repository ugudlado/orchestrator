# Tasks — Feature Complexity Tracking

- [x] T-1: Write tests for `State.complexity` field and `load_state()` validation (RED)
  Verify: `pytest config/scripts/orchestrator_next/tests/test_parser.py -k complexity` runs and FAILS because the field and validation do not yet exist

- [x] T-2: Add `complexity: str | None = None` to `State` dataclass in `parser.py`; validate against `{XS, S, M, L, XL}` in `load_state()` with stderr warning on unknown values (GREEN)
  Verify: `pytest config/scripts/orchestrator_next/tests/test_parser.py -k complexity` passes; full parser test suite green; `python -c "from config.scripts.orchestrator_next.parser import State; s=State.__dataclass_fields__['complexity']"` succeeds
  depends: T-1

- [x] T-3: Write tests for `feature_complexity` DDL creation and `upsert_feature_complexity()` round-trip, including NULL complexity and re-upsert replace semantics (RED)
  Verify: `pytest config/scripts/orchestrator_next/tests/test_upsert.py -k feature_complexity` runs and FAILS because DDL and function do not yet exist
  depends: T-2

- [x] T-4: Add `_DDL_FEATURE_COMPLEXITY` and `upsert_feature_complexity()` to `upsert.py`; wire DDL into `ensure_schema()` (GREEN)
  Verify: `pytest config/scripts/orchestrator_next/tests/test_upsert.py -k feature_complexity` passes; full upsert test suite green; verify table exists via `duckdb metrics.duckdb "DESCRIBE feature_complexity"`
  depends: T-3

- [x] T-5: Write tests for `aggregate_repo(scope="complexity")` and `_by_complexity()` — seeded dataset with all five buckets plus features lacking a `feature_complexity` row; assert ordered XS→S→M→L→XL→unknown output with correct counts, totals, medians, p90s (RED)
  Verify: `pytest config/scripts/orchestrator_next/tests/test_cost_report.py -k complexity` runs and FAILS because the dispatch arm is not yet implemented
  depends: T-4

- [x] T-6: Implement `_by_complexity()` in `cost_report.py` (LEFT JOIN `step_events` feature-cost subquery with `feature_complexity`, `COALESCE` NULL to `'unknown'`, order by bucket); wire `scope="complexity"` into `aggregate_repo()` dispatch; extend `render_markdown_repo()` for the new arm; add `complexity` to `--by` argparse choices in `bin/orchestrator`; call `upsert_feature_complexity()` from `mark-change-completed.sh` with `|| true` error tolerance; extend `config/steps/design-and-draft-artifacts.yaml` complexity label set to include `XL` and declare `complexity` among step outputs (GREEN)
  Verify: `pytest config/scripts/orchestrator_next/tests/test_cost_report.py -k complexity` passes; `orchestrator cost --repo --by complexity` CLI prints a markdown table with columns `complexity | features | total_cost | median_cost | p90_cost`; `bash -n scripts/inline/mark-change-completed.sh` clean; `yq '.outputs' config/steps/design-and-draft-artifacts.yaml` includes `complexity`
  depends: T-5

- [x] T-7: Review checkpoint (phase gate)
  Verify: `pytest config/scripts/orchestrator_next/tests/` all green with coverage ≥ 90% on modified modules; `bash -n` clean on modified shell scripts; `orchestrator cost --repo --by complexity` smoke run succeeds against a seeded DB; no changes to `config/scripts/register-repo.sh` or the bash-owned `features` table
  depends: T-6

<!-- Status markers: [ ] pending, [→] in-progress, [x] done, [~] skipped -->
<!-- TDD: RED tasks (T-1, T-3, T-5) must fail before their paired GREEN tasks (T-2, T-4, T-6) -->
<!-- Coverage target: >= 90% at the T-7 phase gate -->
