---
feature-id: orc-122
linear-ticket: ORC-122
---

# Design: orchestrator graph — cost/token/attempt overlay from completed run

## Context

`orchestrator graph <schema>` renders a static Mermaid DAG of a workflow schema's
step topology (`render_workflow_graph`). `orchestrator graph <schema> <slug>`
currently renders the *current phase's* live node DAG from a single state file
(`render_graph`), showing per-node status colors but no run metrics.

Completed runs record rich per-step metrics in `state.yaml` → `step_history[].usage`
(tokens, `cost_usd`, attempt count), and the `workflow-report` step already
aggregates these across multiple state files. The graph command does not surface
any of this, so it cannot answer "where did time and money go, and which steps
were retried" at a glance.

This feature overlays per-step **tokens**, **USD cost**, and **retry count** onto
the static schema DAG nodes when a slug is provided, reusing the same aggregation
shape as `workflow-report`.

### Verified system boundaries (T-0)

- `step_history[].step_id` records **schema-step IDs** (`implement-tasks`,
  `run-phase-review`, `explore`) directly — implement-tasks is a single aggregate
  entry, NOT exploded into per-task `T-N` nodes. The join key against
  `render_workflow_graph` node IDs lines up with no mapping layer.
  (Verified: `.orchestrator/orc-120`, `orc-118`, `orc-99`, `orc-74`.)
- `usage` shape: `input_tokens`, `output_tokens`, `cost_usd`, `duration_ms`.
  Token label = `input_tokens + output_tokens`.
- A retried step has **multiple `step_history` entries** (att=1, att=2…).
  Aggregation sums tokens/cost and takes `max(attempt)`. (Verified: orc-99, orc-74.)
- A completed feature/patch run produces **multiple state files** in
  `.orchestrator/<slug>/` (e.g. `*_patch_state.yaml` + `*_complete_state.yaml`);
  metrics must be merged across all of them. (Verified: orc-120.)
- `render_graph` has exactly one caller: `bin/orchestrator:211` (the
  `<schema> <slug>` path).

## Goals / Non-Goals

### Goals

- `orchestrator graph <schema> <slug>` reads all state files for the slug and
  overlays per-step tokens + cost onto the static schema DAG node labels.
- Steps with `attempts > 1` get a distinct Mermaid `style ... fill:#f90` line.
- Script-only steps (no token data) render as plain nodes (step ID only).
- `orchestrator graph <schema>` (no slug) renders the unchanged static topology.
- `--html` continues to work, carrying the overlay labels and the
  click-to-inspect sidebar.

### Non-Goals

- No wall-clock duration in node labels (sidebar already shows it; ticket excludes it).
- No changes to `workflow_report_step.py`, `metrics.py`, or any DuckDB path.
- No new CLI flags beyond the existing `<schema> <slug> [--html]` interface.
- No per-step status coloring of the schema graph (only orange-for-retry).
- No preservation of the per-phase live-DAG CLI view (see Decisions).

## Approaches Considered

### Approach 1: Overlay function in graph.py with inline aggregation (complexity: S)

Add `render_workflow_graph_with_overlay(schema_name, state_dir)` to `graph.py`.
It globs `*_state.yaml` under `state_dir`, merges `step_history`, collapses by
`step_id` (sum tokens/cost, max attempt) into a `dict[step_id → {tokens, cost,
attempts}]`, then renders the same schema topology as `render_workflow_graph`
with annotated labels and `style` lines for retried steps. A small private
`_aggregate_step_metrics(state_dir)` helper holds the glob+collapse logic
(duplicated, not imported, from `workflow_report_step`). `bin/orchestrator`
routes `<schema> <slug>` to this function.

- **Pros:** Self-contained in `graph.py` (which is "no DuckDB, read-only");
  no cross-module import from a `config/steps` script; reuses the existing
  `render_workflow_graph` node/edge code via a shared internal renderer;
  smallest diff; aggregation logic is ~15 lines mirroring a proven pattern.
- **Cons:** ~15 lines of glob+collapse duplicated from `workflow_report_step`.

### Approach 2: Extract a shared aggregation module (complexity: M)

Extract `_collect_all_states` + the collapse logic from `workflow_report_step.py`
into a new `orchestrator_next/run_metrics.py` library module, import it from both
`workflow_report_step` and `graph.py`.

- **Pros:** Single source of truth for run aggregation; future consumers reuse it.
- **Cons:** Touches `workflow_report_step` (explicitly out of scope per ticket);
  larger blast radius; `workflow_report_step` is a subprocess step script with its
  own `state_yaml` lib path bootstrapping — refactoring its imports risks the
  step at runtime. Over-engineering for two call sites. The aggregation is small
  enough that duplication is cheaper than the coupling.

### Approach 3: Param-flag on the existing render_workflow_graph (complexity: S)

Add an optional `step_metrics` param to `render_workflow_graph(schema_name,
step_metrics=None)`; when present, annotate labels and emit style lines. Do
aggregation in `bin/orchestrator`.

- **Pros:** No new public function; one renderer.
- **Cons:** Pushes aggregation (file globbing, YAML loading) into `bin/orchestrator`,
  which is a thin CLI shim — business logic leaks into the entry point and is
  untestable without invoking the CLI. Mixing concerns.

### Selected Approach

**Approach 1.** Complexity map: Approach 1 = S(2), Approach 2 = M(3),
Approach 3 = S(2). Lowest numeric complexity ties between 1 and 3. Tie-break by
module reuse: Approach 1 reuses the existing `render_workflow_graph` node/edge
rendering via a shared internal helper *and* keeps aggregation testable inside
`graph.py`; Approach 3 reuses less (aggregation lands in the untestable CLI shim).
Approach 1 wins. It keeps `graph.py` self-contained and read-only, honors the
"don't touch workflow-report" constraint, and produces the smallest correct diff.

## High-Level Design

### Architecture Overview

```
bin/orchestrator  _graph_verb(args)
   │  graph <schema> <slug>
   ▼
graph.render_workflow_graph_with_overlay(schema_name, state_dir)
   │
   ├─ _aggregate_step_metrics(state_dir)        # glob *_state.yaml, merge, collapse
   │     → dict[step_id → {tokens, cost, attempts}]
   │
   └─ _render_schema_graph(schema, metrics)     # shared node/edge renderer
         → (mermaid_src, step_data)
            • node label: "step-id\nN tok · $X.XX"  (steps with data)
            • node label: "step-id"                 (script-only steps)
            • style <safe_id> fill:#f90             (attempts > 1)

   (mermaid_src, step_data) ──► render_html(...)  when --html
```

The no-slug path (`render_workflow_graph`) and the overlay path both route through
a shared internal `_render_schema_graph(schema, metrics)` so node/edge topology is
generated once. `metrics={}` reproduces today's plain output byte-for-byte.

### Key Abstractions

- **`_aggregate_step_metrics(state_dir) -> dict[str, dict]`**: globs every
  `*_state.yaml` in `state_dir`, merges all `step_history` entries, collapses by
  `step_id` summing `input_tokens+output_tokens` and `cost_usd`, taking
  `max(attempt)`. Mirrors `workflow_report_step._collect_all_states` +
  `_render_report`'s collapse, scoped to a directory glob (no archive lookup
  needed — the CLI passes the live `.orchestrator/<slug>/` dir).
- **`_render_schema_graph(schema, metrics) -> (str, dict)`**: the topology
  renderer (extracted from today's `render_workflow_graph` body), parameterized by
  a per-step metrics map. Empty map → unchanged plain graph.
- **`render_workflow_graph_with_overlay(schema_name, state_dir) -> (str, dict)`**:
  public entry — loads schema, aggregates metrics, calls `_render_schema_graph`.

### Node label format

- Step with token data: `step-id\nN,NNN tok · $X.XX`
  (e.g. `implement-tasks["implement-tasks\n106,440 tok · $5.41"]`).
- Script-only step (no tokens): `step-id` (e.g. `check-rerun["check-rerun"]`).
- Cost formatted to 2 decimals to match the ticket example (`$1.05`, `$5.41`).
- Tokens thousands-separated.

### Retry style

For each step with `attempts > 1`, append one line:
`  style <safe_id> fill:#f90,stroke:#d29922,color:#111`
after the node declarations. (`fill:#f90` is the AC's required token; stroke/color
added for legibility on the orange fill — non-essential, AC requires only fill.)

## Low-Level Design

### Components

| Component | Responsibility | Inputs | Outputs |
|-----------|----------------|--------|---------|
| `_aggregate_step_metrics` | glob+merge+collapse step_history | `state_dir: str\|Path` | `dict[step_id → {tokens:int, cost:float, attempts:int}]` |
| `_render_schema_graph` | render topology + optional overlay | `schema: dict`, `metrics: dict` | `(mermaid_src: str, step_data: dict)` |
| `render_workflow_graph` | thin wrapper, empty metrics | `schema_name: str` | `(str, {})` — unchanged signature/behavior |
| `render_workflow_graph_with_overlay` | load schema + aggregate + render | `schema_name: str`, `state_dir: str` | `(str, dict)` |
| `_graph_verb` (bin) | route `<schema> <slug>` to overlay | argv | side effect: stdout / html |

### Data Flow

1. `bin/orchestrator` parses `graph <schema> <slug> [--html]`.
2. For slug mode it builds `state_dir = .orchestrator/<slug>/` (the parent of what
   `_resolve_slug_state` resolves today) and calls
   `render_workflow_graph_with_overlay(schema, state_dir)`.
3. `_aggregate_step_metrics` loads every `*_state.yaml`, merges `step_history`,
   collapses by `step_id`.
4. `_render_schema_graph` emits node decls (with labels), `style` lines for
   retried steps, edges, and (for HTML) click callbacks.
5. `step_data` for the sidebar = last `step_history` entry per step across all
   files (so `--html` click-to-inspect keeps working).

### State Management

Read-only. No `state.yaml` writes, no DuckDB. The aggregation map is in-memory and
discarded after rendering.

### Error Handling

- Slug with no state files → existing `_resolve_slug_state` already exits code 3;
  the overlay path keeps that guard (resolve a state file first to validate the
  slug exists, then aggregate the sibling directory). Unchanged behavior (UC-E3).
- A malformed/non-dict state file or entry is skipped defensively (mirrors
  `workflow_report_step`'s `isinstance` guards) rather than crashing the render.
- Steps present in the schema but absent from `step_history` → render plain
  (no metrics line), same as script-only steps.

## Constraints

- `graph.py` must remain read-only with no DuckDB dependency.
- No edits to `workflow_report_step.py` or `metrics.py`.
- No new CLI flags; the interface stays `graph <schema> [<slug>] [--html]`.

## Trade-offs

- **Duplicating ~15 lines of aggregation** instead of extracting a shared module:
  accepted to avoid `graph.py` importing a `config/steps` subprocess script
  (inverted dependency) and to keep the diff scoped per the ticket's
  "no workflow-report changes" constraint. The cost is one place to update if the
  `usage` schema changes; acceptable for two call sites.
- **Dropping the per-phase live-DAG from the CLI** (see Decisions): accepted
  because the ticket's example unambiguously shows cross-phase schema nodes, which
  the per-phase view cannot produce, and no new flag is permitted to keep both.

## Acceptance Criteria

- AC-1: `orchestrator graph <schema> <slug>` reads **all** `*_state.yaml` files in
  `.orchestrator/<slug>/` and overlays per-step aggregated metrics (sum tokens,
  sum cost, max attempt) onto the schema DAG nodes. [traces: UC-1, UC-E2]
- AC-2: Agent steps (steps with token data) show `N tok · $X.XX` in the node
  label; script-only steps (`usage: {}`) show the step ID only with no metrics
  line. [traces: UC-1, UC-E1]
- AC-3: Steps with aggregated `attempts > 1` emit a Mermaid
  `style <node_id> fill:#f90` line (distinct orange fill); steps with one attempt
  do not. [traces: UC-2, UC-E4]
- AC-4: `orchestrator graph <schema>` (no slug) renders byte-for-byte the same
  plain schema topology as before — `render_workflow_graph` output is unchanged.
  [traces: UC-4]
- AC-5: `orchestrator graph <schema> <slug> --html` writes an HTML page whose
  embedded Mermaid carries the overlay labels and orange retry styles, and whose
  click-to-inspect sidebar still resolves per-step data. [traces: UC-3]

## Decisions

- Overlay targets the **static schema graph** (`render_workflow_graph`), not the
  per-phase live DAG (`render_graph`) → the ticket example spans multiple phases
  which a single-phase DAG cannot render → overlay annotates schema topology.
- **`graph <schema> <slug>` is repointed** from `render_graph` to the overlay
  renderer → `render_graph` was that path's only CLI caller, so the per-phase
  live-DAG view loses its CLI entry point → `render_graph` is retained as a
  library function (still imported by tests) but is no longer reachable from the
  CLI. This is an intentional capability change, surfaced here rather than as a
  silent side effect.
- **Aggregate by sum + max-attempt**, mirroring `workflow_report_step` →
  retried steps have multiple `step_history` entries → summing tokens/cost and
  taking max(attempt) gives the same totals the workflow-report already shows.
- **Duplicate, don't import, the aggregation** → `workflow_report_step` is a
  subprocess step script, not a library module → keeps `graph.py` dependency-clean.
- **No status fill, orange-for-retry only** → ACs require only `fill:#f90` for
  retries → adding status colors is scope creep with a precedence ambiguity.
- **Cost shown to 2 decimals in labels** → matches the ticket example `$1.05` →
  the HTML sidebar keeps 4-decimal precision (`fmtCost`) unchanged.

## Open Questions

- OQ-1: Duration in node labels (`· 4.2m`) — out of scope per ticket; `duration_ms`
  is available if a follow-on wants it. (Carried from discovery; not blocking.)
