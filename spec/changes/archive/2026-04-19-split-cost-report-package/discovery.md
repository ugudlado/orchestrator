---
feature-id: split-cost-report-package
linear-ticket: null
---

# Discovery Brief: Split cost_report.py into aggregate/render/anomaly/formatters package

## Feature Summary

`config/scripts/orchestrator_next/cost_report.py` is a 898-line single-file module with 7 public entry points, private aggregation helpers, anomaly-detection logic, markdown/JSON renderers, and formatting utilities all co-located. The two existing test files already split cleanly along the natural seams (test_cost_report.py covers aggregate_repo/render_markdown_repo; test_cost_report_anomaly.py covers anomaly detection). This refactor converts the file into a package with four sub-modules — aggregate, render, anomaly, formatters — while preserving the public API unchanged via `__init__.py` re-exports. No behavior change, no new API, no caller updates required.

## Personas & Actors

- Maintainer: the engineer who reads, extends, or debugs orchestrator cost reporting logic. Benefits from shorter, focused modules.
- CLI consumer (bin/orchestrator): imports 6 public names from orchestrator_next.cost_report; must continue to work unchanged.
- Test suite: both test files import from orchestrator_next.cost_report by name; imports must continue to resolve after the split.

## Use Cases

### Happy Path

UC-1: Successful package split — maintainer runs both test suites after the refactor, all tests pass, and `orchestrator cost` output is byte-identical to pre-split baseline.

UC-2: Caller transparency — a new developer reads `cost_report/__init__.py` and immediately understands which sub-module owns each function, then navigates directly to aggregate.py or render.py to make a change.

### Error & Edge Cases

UC-E1: Import regression — what happens when `__init__.py` is missing a re-export; a test or CLI invocation raises `ImportError` immediately, caught in CI before merge.

UC-E2: Private helper cross-seam dependency — what happens when anomaly.py tries to call a helper that lives only in aggregate.py; triggers an import-time circular import or NameError; resolved by explicit import from the sibling module in anomaly.py.

## Scope

### In Scope

- Convert `cost_report.py` → `cost_report/` package with `__init__.py`, `aggregate.py`, `render.py`, `anomaly.py`, `formatters.py`
- `__init__.py` re-exports all names currently importable from `orchestrator_next.cost_report` (7 public + 1 private used in tests: `_step_allowlist_anomalies`)
- Both existing test files pass unchanged (no edits to test files)
- `bin/orchestrator` continues to work unchanged (no edits to the bin script)
- Each new module targets ≤ ~250 LOC

### Out of Scope

- New report types, CLI flags, or public API additions
- Refactoring SQL queries
- Touching `upsert.py`, `schema`, or `resolver.py`
- Adding new tests beyond verifying the split compiles and existing tests pass

## UI Direction

N/A — no UI components.

## Key Decisions

- Design selection (design-and-draft-artifacts step): Approach A — 4-module package (`aggregate.py` / `render.py` / `anomaly.py` / `formatters.py`). Auto-selection heuristic tie-break: Approach A and Approach B (3-module, formatters merged into render) are equivalent complexity (S); reuse tiebreaker favors A because keeping formatters as its own module preserves a unidirectional import graph (`render → formatters`) and leaves formatters reusable without a render dependency. Approach C (single-file section comments) is ruled out by the ≤250 LOC-per-module constraint. Complexity: S.
- Package replaces file (no thin shim): `cost_report.py` is removed entirely; `cost_report/__init__.py` handles backward compatibility. A thin shim (keeping cost_report.py that imports from cost_report/) was considered but rejected — it would create two importable paths for the same symbols, which is confusing and unnecessary since Python's package mechanism handles this natively.
- `_step_allowlist_anomalies` re-exported from `__init__.py`: Although a private helper, it is imported directly by name in test_cost_report_anomaly.py (`from orchestrator_next.cost_report import _step_allowlist_anomalies`). It must be re-exported to keep those tests passing without modification.
- Cross-seam imports go aggregate → formatters, render → formatters, anomaly → (no dependency on aggregate or render): The anomaly sub-module only calls `load_agent_tools` (from resolver) and `_load_contract` (from parser) — no dependency on aggregate.py or render.py. The render sub-module depends on formatters.py for `_fmt_usd`, `_fmt_tokens`, `_fmt_ms`. No circular dependencies.

## Open Questions

- None. The seam boundaries, public API surface, import graph, and test expectations are all clear from the existing code.

---

## Technical Context (discoverer research)

### LOC confirmation
`cost_report.py`: 898 lines (confirmed via `wc -l`).

### Public API surface (7 functions — all must be re-exported from `__init__.py`)

```
aggregate_feature(db, repo_root, change_id) -> dict       # aggregate.py
aggregate_by_scope(db, repo_root, change_id, scope) -> dict  # aggregate.py
aggregate_repo(db, repo_basename, since, scope) -> dict    # aggregate.py
render_markdown_feature(data) -> str                       # render.py
render_markdown_scoped(data, scope) -> str                 # render.py
render_markdown_repo(data, scope) -> str                   # render.py
render_json(data) -> str                                   # render.py
```

Additionally, `_step_allowlist_anomalies` is imported by name in tests and must be re-exported.

### All importers of cost_report (files that must keep working)

| File | Import pattern | Notes |
|------|---------------|-------|
| `bin/orchestrator` (line 119–127) | `from orchestrator_next.cost_report import aggregate_feature, aggregate_by_scope, aggregate_repo, render_markdown_feature, render_markdown_scoped, render_markdown_repo, render_json` | CLI entry point — no changes needed post-split |
| `config/scripts/orchestrator_next/tests/test_cost_report.py` | `from orchestrator_next.cost_report import aggregate_repo, render_markdown_repo` | No changes needed |
| `config/scripts/orchestrator_next/tests/test_cost_report_anomaly.py` | `from orchestrator_next import cost_report` + `from orchestrator_next.cost_report import aggregate_feature, render_markdown_feature, _step_allowlist_anomalies` | No changes needed |
| `config/scripts/tests/test_cost_report_integration.py` | Uses CLI subprocess (`bin/orchestrator`) — imports orchestrator_next.upsert, not cost_report directly | No changes needed |

No other files in the repo import from cost_report.

### Seam boundaries (confirmed by reading cost_report.py)

| Sub-module | Lines (approx) | Content |
|-----------|----------------|---------|
| `formatters.py` | ~30 | `_fmt_usd`, `_fmt_tokens`, `_fmt_ms` |
| `aggregate.py` | ~400 | `_totals`, `_per_phase`, `_per_agent`, `_per_model`, `_native_tools`, `_mcp_calls`, `_per_agent_tools`, `_by_complexity`, `_by_step`, `_by_agent_scope`, `_by_tool`, `_BUCKET_ORDER`; public: `aggregate_feature`, `aggregate_by_scope`, `aggregate_repo` |
| `render.py` | ~340 | `_md_table`; public: `render_markdown_feature`, `render_markdown_scoped`, `render_markdown_repo`, `render_json` — depends on formatters.py |
| `anomaly.py` | ~80 | `_anomalies`, `_step_allowlist_anomalies` — calls `load_agent_tools` (resolver), `_load_contract`/`ContractError`/`StepContract` (parser); no dependency on aggregate.py or render.py |

### Cross-seam dependency graph

```
formatters.py   ← render.py
resolver.py     ← anomaly.py
parser.py       ← anomaly.py
aggregate.py    ← (no internal cost_report deps)
__init__.py     ← re-exports from all four sub-modules
```

No circular imports. Anomaly does not depend on aggregate.

### Test import analysis

Both test files import exclusively from `orchestrator_next.cost_report` (the package path). After the split, `cost_report/__init__.py` re-exports all referenced names, so tests pass unchanged — no internal private helpers are accessed except `_step_allowlist_anomalies`, which is explicitly in the re-export list.
