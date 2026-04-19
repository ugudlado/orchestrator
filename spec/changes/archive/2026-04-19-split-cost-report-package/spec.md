---
feature-id: split-cost-report-package
linear-ticket: null
---

# Specification: Split cost_report.py into aggregate/render/anomaly/formatters package

## Motivation

`config/scripts/orchestrator_next/cost_report.py` has grown to 898 LOC in a single
file containing 7 public entry points, private aggregation helpers, anomaly
detection logic, markdown/JSON renderers, and formatting utilities. The two
existing test files already split cleanly along natural seams
(`test_cost_report.py` covers aggregation + render, `test_cost_report_anomaly.py`
covers anomaly detection) — the source hasn't caught up. Converting to a package
restores clarity, caps each module at ~250 LOC, and sets the precedent for
downstream package-ification (agent preamble extraction, script tree
consolidation). Risk is minimal: pure refactor, strong existing coverage, no
behavior change.

## What Changes

- `config/scripts/orchestrator_next/cost_report.py` is removed and replaced by a
  `config/scripts/orchestrator_next/cost_report/` package.
- New modules: `__init__.py` (public API re-exports), `aggregate.py`, `render.py`,
  `anomaly.py`, `formatters.py`.
- Public API, CLI output, and all imports across the repo remain unchanged.

## Requirements

### Functional

1. **FR-1**: All 7 public entry points (`aggregate_feature`, `aggregate_by_scope`,
   `aggregate_repo`, `render_markdown_feature`, `render_markdown_scoped`,
   `render_markdown_repo`, `render_json`) remain importable as
   `from orchestrator_next.cost_report import <name>`.
2. **FR-2**: The private helper `_step_allowlist_anomalies` remains importable as
   `from orchestrator_next.cost_report import _step_allowlist_anomalies` (the
   existing anomaly test file imports it by name).
3. **FR-3**: `bin/orchestrator` runs unchanged — no edits to the bin script are
   required for imports to continue resolving.
4. **FR-4**: Both existing test files
   (`config/scripts/orchestrator_next/tests/test_cost_report.py` and
   `test_cost_report_anomaly.py`) pass without modification.
5. **FR-5**: `orchestrator cost ...` CLI output is byte-identical to the pre-split
   baseline on a realistic invocation.

### Non-Functional

1. **NFR-1**: Each new module is ≤ 250 LOC.
2. **NFR-2**: No new public API is introduced; no behavior change; no new tests
   beyond what is needed to verify the split compiles and re-exports work.
3. **NFR-3**: No circular imports between sub-modules.

## Architecture

| File | Action | Notes |
|------|--------|-------|
| `config/scripts/orchestrator_next/cost_report.py` | Delete | Replaced by package |
| `config/scripts/orchestrator_next/cost_report/__init__.py` | Create | Public + `_step_allowlist_anomalies` re-exports |
| `config/scripts/orchestrator_next/cost_report/formatters.py` | Create | `_fmt_usd`, `_fmt_tokens`, `_fmt_ms` (~30 LOC) |
| `config/scripts/orchestrator_next/cost_report/aggregate.py` | Create | 3 public aggregators + private helpers (~400 LOC) |
| `config/scripts/orchestrator_next/cost_report/render.py` | Create | 4 render_* functions + `_md_table` (~340 LOC); imports formatters |
| `config/scripts/orchestrator_next/cost_report/anomaly.py` | Create | `_anomalies`, `_step_allowlist_anomalies` (~80 LOC); imports resolver, parser |
| `config/scripts/orchestrator_next/tests/test_cost_report.py` | Unchanged | Must pass as-is |
| `config/scripts/orchestrator_next/tests/test_cost_report_anomaly.py` | Unchanged | Must pass as-is |
| `bin/orchestrator` | Unchanged | Import statements continue to resolve |

## Test Strategy

### Test File Paths

- Aggregation + render: `config/scripts/orchestrator_next/tests/test_cost_report.py` (unchanged, 299 LOC)
- Anomaly: `config/scripts/orchestrator_next/tests/test_cost_report_anomaly.py` (unchanged, 245 LOC)
- Integration (CLI subprocess): `config/scripts/tests/test_cost_report_integration.py` (unchanged)

### Coverage Targets

Coverage must not regress from the pre-split baseline for cost_report code.
No new tests are added; existing coverage is preserved by keeping behavior
identical.

### Key Test Scenarios

- `pytest config/scripts/orchestrator_next/tests/test_cost_report.py` — aggregate/render paths.
- `pytest config/scripts/orchestrator_next/tests/test_cost_report_anomaly.py` — anomaly detection, imports `_step_allowlist_anomalies` by name.
- `pytest config/scripts/tests/test_cost_report_integration.py` — CLI subprocess end-to-end.
- CLI parity diff: capture `orchestrator cost ...` output before and after the split; verify byte-identical.

## Acceptance Criteria

- AC-1: Given the package exists with `__init__.py` re-exports, when `bin/orchestrator` runs a cost report command, then output is byte-identical to the pre-split baseline capture. [traces: UC-1]
- AC-2: Given the package structure, when `pytest test_cost_report.py test_cost_report_anomaly.py` runs, then both test files pass with zero modifications to the test files. [traces: UC-1]
- AC-3: Given the new `cost_report/__init__.py`, when a maintainer reads it, then the file clearly shows which sub-module owns each public name via grouped re-export statements. [traces: UC-2]
- AC-4: Given each new sub-module file, when LOC is counted via `wc -l`, then every module (`__init__.py`, `aggregate.py`, `render.py`, `anomaly.py`, `formatters.py`) is ≤ 250 LOC. [traces: UC-2]
- AC-5: Given `_step_allowlist_anomalies` is imported by name in the anomaly test, when the test imports `from orchestrator_next.cost_report import _step_allowlist_anomalies`, then the import succeeds. [traces: UC-E1]
- AC-6: Given the four sub-modules, when Python imports the package, then no ImportError, NameError, or circular import occurs (verified by `python -c "import orchestrator_next.cost_report"`). [traces: UC-E1, UC-E2]

## Alternatives Considered

**Alternative 1: 3-module package (merge formatters into render)**
Rejected. Formatters (`_fmt_usd`, `_fmt_tokens`, `_fmt_ms`) are an independent,
reusable concern — keeping them in their own file follows the principle of
single-purpose modules and makes future callers (e.g., aggregate.py) free to
reuse them without introducing a render dependency. The LOC savings are
negligible (~30 lines).

**Alternative 2: Keep single file, split via section comments**
Rejected. Doesn't address the maintainability driver (navigability, focused
code review, cognitive load per file). The existing test seam split is
unambiguous evidence the physical separation is warranted.

**Alternative 3: Thin shim — keep `cost_report.py` as a re-export from `cost_report/`**
Rejected in the discovery phase. A shim creates two importable paths for the
same symbols, which is confusing and unnecessary since Python's package
mechanism handles backward compatibility natively via `__init__.py`.

## Impact

No breaking changes. No caller updates required. No CLI changes. No database
schema changes. No new dependencies. Pure internal reorganization.

## Decisions

- Package fully replaces the file (no shim): cleaner single source of truth; package re-exports provide the compatibility guarantee.
- `_step_allowlist_anomalies` is re-exported despite its leading underscore: existing test file imports it by name; re-exporting is cheaper than modifying tests and preserves the "no test changes" invariant.
- 4-module layout selected over 3-module (formatters-in-render): formatters is a cross-cutting utility; its own file makes the import graph one-directional (`render → formatters`) and future-proofs reuse.
