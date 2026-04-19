# Tasks — Split cost_report.py into aggregate/render/anomaly/formatters package

- [x] T-1: Capture pre-split CLI baseline and import inventory — run `orchestrator cost` on a realistic run, save stdout to `.tmp/cost_report_baseline.txt`; record SHA256. Enumerate every name currently importable from `orchestrator_next.cost_report` (7 public + `_step_allowlist_anomalies`) in a fixture used by the smoke test in T-2.
  Verify: `.tmp/cost_report_baseline.txt` exists and is non-empty; `.tmp/cost_report_baseline.sha256` exists; a fixture file `config/scripts/orchestrator_next/tests/_cost_report_public_names.py` (or equivalent) lists the 8 names.

- [x] T-2: Write package-structure smoke test BEFORE implementation — add a new test (e.g. `config/scripts/orchestrator_next/tests/test_cost_report_package_structure.py`) that (a) imports `orchestrator_next.cost_report` and asserts every expected public name and `_step_allowlist_anomalies` are present as attributes, (b) asserts `wc -l` of each file in `orchestrator_next/cost_report/` is ≤ 250, (c) asserts no circular import by re-importing after `importlib.reload`.
  Verify: `pytest config/scripts/orchestrator_next/tests/test_cost_report_package_structure.py` runs and fails with a clear "module not yet a package" or "missing attribute" style error prior to T-3.
  depends: T-1

- [x] T-3: Create the `cost_report/` package — delete `config/scripts/orchestrator_next/cost_report.py`; create `cost_report/__init__.py`, `formatters.py`, `aggregate.py`, `render.py`, `anomaly.py` per design.md. `__init__.py` re-exports all 7 public names and `_step_allowlist_anomalies`. If any module exceeds 250 LOC after the move, split per the overflow plan in design.md (`aggregate_buckets.py` / `render_tables.py`). No edits to `bin/orchestrator` or existing test files.
  Verify: `pytest config/scripts/orchestrator_next/tests/test_cost_report_package_structure.py` passes; `wc -l config/scripts/orchestrator_next/cost_report/*.py` shows every file ≤ 250.
  depends: T-2

- [x] T-4: Run the existing unit test suites unchanged — `pytest config/scripts/orchestrator_next/tests/test_cost_report.py` and `pytest config/scripts/orchestrator_next/tests/test_cost_report_anomaly.py`. Do NOT edit either test file. If anything fails, the fix goes into the package (usually a missing re-export or a wrong intra-package import), not into the test.
  Verify: both test files report all-passing; `git diff -- config/scripts/orchestrator_next/tests/test_cost_report.py config/scripts/orchestrator_next/tests/test_cost_report_anomaly.py` is empty.
  depends: T-3

- [x] T-5: Run the CLI integration test and the byte-parity diff — `pytest config/scripts/tests/test_cost_report_integration.py` (uses `bin/orchestrator` via subprocess); re-run the T-1 `orchestrator cost` command and diff the new stdout against `.tmp/cost_report_baseline.txt`.
  Verify: integration test passes; `diff .tmp/cost_report_baseline.txt <(orchestrator cost ...)` is empty (byte-identical); SHA256 matches `.tmp/cost_report_baseline.sha256`.
  depends: T-3

- [x] T-6: Final structural sweep — confirm `git grep "cost_report.py"` returns no lingering references to the old file; confirm `ls config/scripts/orchestrator_next/cost_report.py` errors (file gone); confirm `ls config/scripts/orchestrator_next/cost_report/__init__.py` exists; confirm `python -c "import orchestrator_next.cost_report as m; assert all(hasattr(m, n) for n in ['aggregate_feature','aggregate_by_scope','aggregate_repo','render_markdown_feature','render_markdown_scoped','render_markdown_repo','render_json','_step_allowlist_anomalies'])"` runs clean.
  Verify: all four structural checks pass as described.
  depends: T-4, T-5
