# tasks.md — live-cost-probe

- [x] T-1: Add `sum_cost_usd(db, context) -> float` to `config/scripts/orchestrator_next/upsert.py`. Parameterised `SELECT COALESCE(SUM(gen_ai_usage_cost_usd), 0.0) FROM step_events WHERE repo_root = ? AND change_id = ?`. Reuse `_SLUG_RE` guard on `change_id`. Return `float` (coerce from DuckDB scalar).
  - Verify: unit test inserts two fake `step_events` rows with costs `0.01` and `0.02` for one `(repo_root, change_id)`, asserts `sum_cost_usd` returns `0.03`; empty-table case returns `0.0`.

- [x] T-2: Wire into `bin/orchestrator`. Inside the existing `if _metrics_db_path:` block, after the upsert loop and before `_db.close()`, compute `_cost_so_far = sum_cost_usd(_db, _context)` inside a broad `try/except` (warning to stderr on failure, default `0.0`). Initialise `_cost_so_far = 0.0` outside the block so the no-DB path is covered. After `dispatch(...)` returns `action`, set `action["estimated_cost_so_far"] = _cost_so_far` prior to `emit_json(action)`.
  - Verify: run `orchestrator next` against an existing dispatch fixture; JSON output contains `"estimated_cost_so_far"` with a float value; `emit_json` sort/indent unchanged (byte-stable).

- [x] T-3: Add `config/scripts/tests/test_estimated_cost_so_far.py` covering AC-1, AC-2, AC-3 end-to-end through `bin/orchestrator` (subprocess, like sibling tests). Include the unset-`METRICS_DB` / unset-`ORCHESTRATOR_HOME` case asserting `0.0`.
  - Verify: `pytest config/scripts/tests/test_estimated_cost_so_far.py -q` passes; full suite `pytest config/scripts/tests -q` still passes (no regression in `test_dispatch*`, `test_step_events_upsert`).
