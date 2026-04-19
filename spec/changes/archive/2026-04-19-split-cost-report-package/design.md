# Design: Split cost_report.py into aggregate/render/anomaly/formatters package

## Context

`config/scripts/orchestrator_next/cost_report.py` is a 898-LOC single-file module
holding 7 public functions, anomaly detection, markdown/JSON renderers, and three
formatting helpers. Both existing test files already split cleanly along these
seams. Four importers consume the module (`bin/orchestrator`,
`tests/test_cost_report.py`, `tests/test_cost_report_anomaly.py`, and — via CLI
subprocess — `tests/test_cost_report_integration.py`). The refactor must preserve
every import path, CLI byte output, and test behavior exactly while splitting the
source along the natural seams the tests already describe.

## Goals / Non-Goals

### Goals

- Replace the 898-LOC file with a package of focused ≤250-LOC sub-modules.
- Preserve the public API (7 names) and the one private name (`_step_allowlist_anomalies`) imported by tests.
- Make the ownership of each symbol obvious by reading `__init__.py` alone.
- Keep `bin/orchestrator` imports and CLI output byte-identical.
- Zero behavior change — pure mechanical split.

### Non-Goals

- No new public API, CLI flags, or report types.
- No SQL changes; no refactor of existing aggregation logic beyond moving it.
- No test-file edits; no new tests beyond what is needed to verify the split.
- No compatibility shim at the old module path (the package natively provides compatibility).

## Approaches Considered

### Approach 1: 4-module package (`aggregate.py`, `render.py`, `anomaly.py`, `formatters.py`)

Four sub-modules mirroring the test-file seam boundaries and the logical
responsibilities (data shaping, rendering, anomaly detection, value formatting).

- Pros: matches discovery seam analysis exactly; keeps formatters reusable without
  pulling in a render dependency; unidirectional import graph; all four modules
  comfortably under the 250-LOC cap.
- Cons: one extra file vs. Approach 2 (~30 LOC formatters file).

### Approach 2: 3-module package (merge `formatters.py` into `render.py`)

Collapse the three `_fmt_*` helpers into `render.py`.

- Pros: one fewer file; negligible LOC savings.
- Cons: any future caller (e.g., an aggregate-level summary row) that wants to
  reuse a formatter has to import from `render.py`, entangling data shaping with
  rendering; formatters are a cross-cutting utility and shouldn't live inside a
  renderer.

### Approach 3: Keep single file, split by section comments

Add banner comments inside `cost_report.py`.

- Pros: zero file churn.
- Cons: fails the ≤250 LOC per-module constraint; doesn't address the stated
  maintainability driver (navigability, focused review, cognitive load).

### Selected Approach

**Approach 1 (4-module package).** Auto-selection heuristic: Approach 3 is ruled
out by the LOC constraint. Approaches 1 and 2 are equivalent in complexity
class (S). Reuse tiebreaker favors Approach 1: keeping `formatters.py` as its own
module preserves a unidirectional import graph (`render → formatters`) and lets
future callers consume formatters without depending on render. The +1 file cost
is trivial next to the reuse/clarity benefit.

## High-Level Design

### Architecture Overview

```
orchestrator_next/
  cost_report/
    __init__.py       # Re-exports the public API (7 names) + _step_allowlist_anomalies
    formatters.py     # _fmt_usd, _fmt_tokens, _fmt_ms
    aggregate.py      # aggregate_feature, aggregate_by_scope, aggregate_repo + helpers
    render.py         # render_markdown_feature / _scoped / _repo, render_json, _md_table
    anomaly.py        # _anomalies, _step_allowlist_anomalies

Import graph (all edges intra-package unless labelled external):
  formatters.py         (leaf)
  render.py      → formatters.py
  aggregate.py          (leaf — no intra-package deps)
  anomaly.py     → resolver.py (external), parser.py (external)
  __init__.py    → aggregate, render, anomaly   (re-export only; no logic)
```

No circular imports; `aggregate.py` has no intra-package edges, `anomaly.py`
depends only on `resolver`/`parser`, `render.py` depends only on `formatters.py`.

### Key Abstractions

No new abstractions are introduced. The refactor moves existing symbols into the
module they already logically belong to. `__init__.py` is the only new surface
and it contains only `from .<submodule> import <name>` statements grouped by
origin sub-module for at-a-glance ownership.

## Low-Level Design

### Components

| Module | Contents | Approx LOC | Deps |
|--------|----------|-----------:|------|
| `__init__.py` | Re-export statements only, grouped by sub-module. `__all__` listing the 7 public names. | ~20 | `.aggregate`, `.render`, `.anomaly` |
| `formatters.py` | `_fmt_usd`, `_fmt_tokens`, `_fmt_ms` | ~30 | (leaf) |
| `aggregate.py` | `aggregate_feature`, `aggregate_by_scope`, `aggregate_repo`, plus private helpers: `_totals`, `_per_phase`, `_per_agent`, `_per_model`, `_native_tools`, `_mcp_calls`, `_per_agent_tools`, `_by_complexity`, `_by_step`, `_by_agent_scope`, `_by_tool`, `_BUCKET_ORDER`. SQL query strings stay inline as today. | ~400 (splits further if any single function block pushes the module over 250; see below) | (leaf) |
| `render.py` | `render_markdown_feature`, `render_markdown_scoped`, `render_markdown_repo`, `render_json`, `_md_table` | ~340 (splits further if needed) | `formatters.py` |
| `anomaly.py` | `_anomalies`, `_step_allowlist_anomalies` | ~80 | external: `resolver.load_agent_tools`, `parser._load_contract`, `parser.ContractError`, `parser.StepContract` |

**≤250 LOC per module enforcement.** Discovery's line counts for `aggregate.py`
(~400) and `render.py` (~340) exceed the NFR-1 cap of 250. Implementation MUST
keep each module ≤250 LOC. If a raw extract exceeds the cap, the module MUST
be subdivided along a natural seam before the task is considered done:

- `aggregate.py` overflow plan: if >250 LOC after moving all aggregators, extract
  the per-bucket helper cluster (`_by_complexity`, `_by_step`, `_by_agent_scope`,
  `_by_tool`, `_BUCKET_ORDER`) into `aggregate_buckets.py` (sibling module),
  imported by `aggregate.py`. Not re-exported from `__init__.py` (helpers only).
- `render.py` overflow plan: if >250 LOC, extract `_md_table` plus any
  repo-report-only helpers into `render_tables.py` (sibling module), imported by
  `render.py`. Not re-exported.

These overflow modules remain optional — they are created only if the LOC cap
is actually breached after the split. The implementer verifies via `wc -l` and
adds them only on overflow.

### `__init__.py` content (concrete)

```python
"""Cost reporting package — public API stays identical to the pre-split module."""

from .aggregate import aggregate_feature, aggregate_by_scope, aggregate_repo
from .render import (
    render_markdown_feature,
    render_markdown_scoped,
    render_markdown_repo,
    render_json,
)
from .anomaly import _step_allowlist_anomalies  # re-exported: used by anomaly tests

__all__ = [
    "aggregate_feature",
    "aggregate_by_scope",
    "aggregate_repo",
    "render_markdown_feature",
    "render_markdown_scoped",
    "render_markdown_repo",
    "render_json",
]
# Note: _step_allowlist_anomalies is intentionally importable but excluded from __all__.
```

### Data Flow

Data flow is unchanged from today. CLI (`bin/orchestrator`) calls an
`aggregate_*` function to get a dict, then passes it to a `render_*` function
to get the final string. `_anomalies` is invoked inside aggregators; the
anomaly test calls `_step_allowlist_anomalies` directly. The split does not
re-route any call — it relocates function definitions only.

### State Management

No state is introduced. All functions are pure except for DB reads performed
inside aggregators (unchanged from today).

### Error Handling

No new error paths. The only structural risk is import-time errors:

- **Missing re-export.** `__init__.py` must list every name currently importable
  from the pre-split module + `_step_allowlist_anomalies`. Mitigation: an import
  smoke test (`python -c "import orchestrator_next.cost_report as m; [getattr(m, n) for n in (...)]"`)
  in the verification task.
- **Circular import.** Disallowed by the import graph above. Mitigation: `aggregate.py`
  does not import from `render.py` or `anomaly.py`; `anomaly.py` does not import
  from `aggregate.py` or `render.py`; `render.py` imports only `formatters.py`.

## Constraints

- Each new module file ≤ 250 LOC (NFR-1).
- CLI output byte-identical on a realistic `orchestrator cost` invocation (FR-5).
- No test-file edits (FR-4).
- No edits to `bin/orchestrator` (FR-3).

## Trade-offs

- **Four files vs. three.** Accepts one extra file (`formatters.py`, ~30 LOC) in
  exchange for a unidirectional import graph and formatter reusability. Acceptable
  because the file count cost is trivial.
- **Re-export a leading-underscore name.** `_step_allowlist_anomalies` is
  private by Python convention but re-exported because the existing anomaly test
  imports it by name. Accepts the convention break to preserve the "no test
  edits" invariant. Acceptable because the symbol is already effectively part of
  the module's test-facing surface.
- **Possible further subdivision of `aggregate.py` / `render.py`.** If the raw
  LOC overflows 250, the implementer adds a sibling helper module. This is a
  mechanical fallback, not a design escalation — the public API is unaffected.

## Decisions

- Package replaces file, no shim → a single importable path per symbol → cleaner
  and aligned with Python packaging semantics.
- `_step_allowlist_anomalies` is re-exported from `__init__.py` → preserves the
  "no test edits" invariant → a convention break on the underscore name, accepted
  with a comment in `__init__.py` documenting the intent.
- `formatters.py` is its own module (not merged into `render.py`) → formatters
  become reusable without a render dependency → one extra ~30 LOC file.
- LOC cap enforced by possible split into `aggregate_buckets.py` /
  `render_tables.py` → guarantees NFR-1 without hand-tuning the seam up front →
  these overflow files are created only on demand.

## Open Questions

- None. Seam boundaries, public API, import graph, overflow strategy, and test
  invariants are all resolved in discovery and in this design.
