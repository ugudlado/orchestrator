# design.md — live-cost-probe

## Context
`bin/orchestrator` already opens `metrics.duckdb`, calls `ensure_schema`, and upserts terminal `step_history` entries before invoking `dispatch(state, ...)`. The connection is open and the schema is guaranteed at that point. `step_events.gen_ai_usage_cost_usd` is DOUBLE, nullable.

## Selected Approach: A (dispatcher-adjacent query, injected into action dict)
Heuristic: lowest complexity wins.
- **A** adds one helper + one SELECT + one dict assignment. No schema change, no new subcommand, no state.yaml field. Chosen.
- **B** (skill maintains running sum in state.yaml) duplicates truth (DuckDB already has it) and requires schema evolution + migration. Rejected.
- **C** (new `orchestrator cost` subcommand) adds CLI surface and a second round-trip per step. Rejected.

## Component Breakdown
1. **`upsert.py`** — add `sum_cost_usd(db, context) -> float`. Parameterised query; slug-guards `change_id` (reuses `_SLUG_RE`); returns `0.0` on NULL/missing.
2. **`bin/orchestrator`** — inside the existing `if _metrics_db_path:` block, after the upsert loop and before `_db.close()`, call `sum_cost_usd(_db, _context)` and stash on a local `_cost_so_far` (default `0.0`). After `dispatch(...)` returns `action`, set `action["estimated_cost_so_far"] = _cost_so_far`. On any exception, log a warning to stderr and leave `_cost_so_far = 0.0`.
3. **`dispatch.py`** — unchanged. Keeps the pure `State → (action, exit_code)` contract intact; cost is a CLI-level concern, not a dispatch-level one.
4. **Tests** — one new file `test_estimated_cost_so_far.py` covering AC-1, AC-2, AC-3.

## SQL Sketch
```sql
SELECT COALESCE(SUM(gen_ai_usage_cost_usd), 0.0)
FROM step_events
WHERE repo_root = ? AND change_id = ?
```
Params: `[context["repo_root"], context["change_id"]]`. Result: `float(row[0])`.

## Risks / Notes
- Rounding: we return the raw DOUBLE sum; callers needing display rounding do it themselves.
- Isolation: because the sum runs on the same connection post-upsert, it reflects the just-upserted terminal entries — the "so far" the user expects.
