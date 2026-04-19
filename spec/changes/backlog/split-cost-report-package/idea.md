# Split cost_report.py (898 LOC) into a package

## Idea

`config/scripts/orchestrator_next/cost_report.py` has grown to 898 LOC with 7 distinct public entry points plus an anomaly-detection subsystem in a single file. The two test files already split along natural seams (`test_cost_report.py` 299 LOC for aggregation/render; `test_cost_report_anomaly.py` 245 LOC for anomalies) — the source hasn't caught up.

Restructure into a package, preserving the public API exactly via `__init__.py`:

```
cost_report/
  __init__.py      # re-exports public API unchanged
  aggregate.py     # aggregate_feature / aggregate_by_scope / aggregate_repo + SQL
  render.py        # 4 render_* functions (markdown/text/json)
  anomaly.py       # anomaly detection + helpers
  formatters.py    # _fmt_cost / _fmt_tokens / _fmt_duration / _fmt_percent
```

## Why Now

1. `otel_map.py` was just deleted and `cost_report.py` was just edited — module is actively in motion; a split here won't collide with parallel work.
2. Existing test files already mirror the proposed seams, so verification is cheap.
3. Sets the precedent for package-ification used by downstream refactors (agent preamble extraction, script tree consolidation).
4. Risk is minimal: pure refactor, strong existing coverage, no behavior change.

## Acceptance

- `cost_report.py` removed; `cost_report/` package created.
- All existing imports continue to work.
- `orchestrator cost ...` CLI output byte-identical on a realistic run (diff snapshot).
- Both test files pass unchanged.
- Each new module ≤ ~250 LOC.
- No new public API; no behavior change.

## Out of Scope

- New report types or CLI flags.
- Refactoring SQL queries.
- Touching `upsert.py` or schema.

## Priority

- User value: 5/10 (maintainer quality-of-life, no end-user change)
- Strategic fit: 8/10 (unblocks adjacent structural work)
- Technical leverage: 8/10 (low-risk, high-clarity)
- Effort: small
- **Score: 7.0**

## Size

Small. Target: 3 tasks — extract aggregate + render, extract anomaly + formatters, rewire `__init__` + verify CLI parity.

## Labels

orchestrator, improvement

## Notes

Linear ticket creation blocked by workspace free-tier limit on 2026-04-19; file this in Linear when the workspace is upgraded.
