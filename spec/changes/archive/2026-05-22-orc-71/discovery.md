---
feature-id: orc-71
linear-ticket: N/A
---

# Discovery Brief: Unify Cost Computation Between Python and Shell

## Feature Summary

The orchestrator has two independent implementations of the same cost-computation logic: `record.py` contains `_lookup_price`, `_ensure_pricing_cache`, and `_compute_cost_usd` (Python, ~200 LOC) while `estimate-cost.sh` contains `get_backend`, `get_model`, `resolve_native`, and `lookup_pricing` (Bash, ~180 LOC). Both parse `routes.yaml`, resolve agent→backend→model chains, and query the same DuckDB pricing table — but with divergent behavior at several decision points. The goal is to extract a single Python module with a CLI entry point, have `estimate-cost.sh` shell out to it, and have `record.py` import from it — eliminating the duplication while preserving all existing test contracts.

## Personas & Actors

- **Workflow engine (record.py)**: invokes `_compute_cost_usd` at step-record time to persist `cost_usd` into DuckDB `step_events`. Needs in-process performance (lru_cache on routes, in-process pricing cache).
- **Pre-flight estimator (estimate-cost.sh → preview-route.sh)**: called before a workflow run to show estimated cost per agent route. Called once per agent per invocation (~8 times per preview). Tolerates subprocess overhead; cannot run Python in-process.
- **Developers/maintainers**: need to update pricing rates, add new models, or change route resolution in one place without synchronizing two implementations.
- **CI/test harness**: `test_record_cost_compute.py` and `test_estimate_cost_sh.py` exercise the two paths independently; both must pass unchanged after the refactor.

## Use Cases

### Happy Path

UC-1: Step cost recorded — workflow engine wants to compute cost for a completed step so that `cost_usd` is persisted accurately in DuckDB. `record.py` calls the shared module in-process; the module resolves agent→backend→model via routes.yaml, looks up DuckDB pricing with `effective_from <= now`, applies JSONL `usage.model` if present, and returns a float.

UC-2: Pre-flight route preview — operator wants to see estimated cost per agent before starting a workflow so that they can confirm the route selection. `preview-route.sh` calls `estimate-cost.sh`; the shell script shells out to the shared Python module CLI with a bulk-lookup mode; the module returns a YAML block per agent with model and rate info.

UC-3: Pricing update — developer updates a model's price in DuckDB (via migration) so that both record-time and pre-flight estimates reflect the new rate without touching two codebases.

### Error & Edge Cases

UC-E1: Unresolvable model — agent is not in routes.yaml or backend has no model_id entry. Module falls back to `__default__` pricing row if tokens > 0, returns None otherwise. Behavior must be consistent between the Python in-process path and the CLI subprocess path.

UC-E2: DuckDB absent — `METRICS_DB` path does not exist or the pricing table is empty. record.py currently returns None (no cost recorded); estimate-cost.sh currently returns hardcoded fallback rates. The unified module must settle on one behavior; both consumers must be updated to handle it.

UC-E3: CLI subprocess performance degradation — estimate-cost.sh calls the Python module once per agent (~8 agents). If the module is invoked as 8 separate subprocesses, startup overhead may make pre-flight estimation noticeably slow. A bulk-lookup mode (all agents in one call) must be supported.

## Scope

### In Scope

- Extract `_lookup_price`, `_ensure_pricing_cache`, `_compute_cost_usd`, `_load_routes`, and `_DATED_MODEL_SUFFIX_RE` from `record.py` into a new `orchestrator_next.pricing` module
- Add a CLI entry point (`python -m orchestrator_next.pricing` or `orchestrator_next/pricing.py --agents ...`) that outputs YAML or JSON pricing for one or more agents in a single invocation
- Update `record.py` to import from `orchestrator_next.pricing` (remove duplicated functions)
- Update `estimate-cost.sh` to shell out to the Python CLI entry point instead of reimplementing routes resolution and DuckDB queries
- Reconcile the 7 behavioral divergences (see Open Questions) before or during implementation — architect decides canonical behavior per divergence
- All existing tests (`test_record_cost_compute.py`, `test_estimate_cost_sh.py`) pass unchanged

### Out of Scope

- Changes to `cost-report.sh` — it reads pre-computed `cost_usd` columns from DuckDB views, not pricing logic
- Changes to `compute-swe-metrics.sh` — reads stored `pricing_*` columns, not a pricing consumer
- Changes to `bin/orchestrator` — uses `sum_cost_usd` aggregation only
- Adding new pricing rows or model entries to `migrations/0001_seed_pricing.sql`
- Changing the DuckDB schema or adding new columns
- Replacing or rewriting the bash wrapper entirely — `estimate-cost.sh` stays bash, just calls Python for pricing logic
- UI or dashboard changes

## UI Direction

N/A — no UI components.

## Key Decisions

- Selected design direction: **Shared module + bulk CLI, `record.py` re-exports** (complexity S). Extract pricing logic into `orchestrator_next/pricing.py`; `record.py` re-exports the symbols by reference so existing imports and test fixtures resolve unchanged; a single bulk CLI (`python -m orchestrator_next.pricing --agents …`) replaces the Bash reimplementation in `estimate-cost.sh`. Chosen over a per-agent CLI (rejected: ~8 interpreter starts per preview, UC-E3) and a Bash codegen approach (rejected: disproportionate machinery for a low-frequency path). See `design.md` § Selected Approach.
- The 7 behavioral divergences (OQ-1 … OQ-7) are resolved in `design.md` § Decisions as D-1 … D-7:
  - OQ-1 → D-1: add `native_haiku: claude-haiku-4-5` to `routes.yaml` (routes is the single source of native-backend truth).
  - OQ-2 → D-2 (LOCKED by user): remove the hardcoded DB-absent fallback entirely; DB-absent fails loud. No `--fallback-rates` flag. Ticket AC #4 is amended — `test_estimate_cost_sh.py` scenario (c) is updated to assert fail-loud behavior.
  - OQ-3 → D-3: keep the `effective_from <= now` date filter (the correct, ORC-tested behavior).
  - OQ-4 → D-4: always strip the `-YYYYMMDD` dated suffix (ORC-30 behavior).
  - OQ-5 → D-5: the CLI reads and emits all four pricing columns; `estimate-cost.sh` keeps using three.
  - OQ-6 → D-6: bulk CLI mode only (one subprocess spawn per preview).
  - OQ-7 → D-7: `usage.model` JSONL precedence stays in-process only; no `--usage-model` CLI flag.

## Open Questions

- OQ-1: **Native model hardcoding vs routes.yaml**: `estimate-cost.sh:resolve_native()` hardcodes `native_haiku→claude-haiku-4-5`, but `routes.yaml` backends section only has `native_opus` and `native_sonnet`. Should `native_haiku` be added to routes.yaml as canonical truth, or should the unified module embed a native-backend override map?
- OQ-2: **DB-absent fallback behavior**: record.py returns None (no cost recorded) when DB is absent; estimate-cost.sh returns hardcoded `15.00 75.00 1.50` rates. `test_estimate_cost_sh.py` scenario (c) asserts non-zero pricing when DB is absent. Should the unified module expose a `--fallback-rates` flag, or should the DB-absent path always return None and the test be updated?
- OQ-3: **`effective_from` date filter**: record.py filters `effective_from <= now`; estimate-cost.sh uses `ORDER BY effective_from DESC LIMIT 1` with no date filter. These diverge when future-dated pricing rows exist in the DB. Which behavior is canonical?
- OQ-4: **Dated-suffix stripping**: record.py strips `-YYYYMMDD` suffixes (ORC-30) before pricing lookup; estimate-cost.sh does not. Should the unified module always strip, and should `estimate-cost.sh` tests be updated to reflect this?
- OQ-5: **`cache_creation_usd` column**: record.py reads 4 pricing columns including `cache_creation_usd`; estimate-cost.sh reads 3 columns (omits `cache_creation_usd`). The CLI output format must specify whether to include all 4 columns.
- OQ-6: **CLI invocation mode**: should `estimate-cost.sh` call the Python module once per agent (simple, 8 subprocess spawns) or once per preview with all agents as arguments (bulk, 1 spawn)? The bulk mode requires a defined multi-agent output format (JSON array or multi-doc YAML).
- OQ-7: **`usage.model` JSONL precedence**: record.py Step 0 overrides model resolution with `usage.model` from the JSONL event (billing truth). estimate-cost.sh has no JSONL context. Should the CLI entry point accept an optional `--usage-model` flag for parity, or is JSONL-override strictly an in-process-only concern?

---

## Technical Context

### CLI/Script Surface Inventory

All callable entrypoints in the affected area (mandatory per discoverer contract):

| Entrypoint | Location | Invoked by |
|---|---|---|
| `estimate-cost.sh` | `config/scripts/estimate-cost.sh` | `preview-route.sh`, tests |
| `preview-route.sh` | `config/scripts/inline/preview-route.sh` | orchestrator inline step |
| `orchestrator_next.record` (record function) | `config/scripts/orchestrator_next/record.py` | `bin/orchestrator` via `python -m` |
| `python -m orchestrator_next.pricing` (proposed) | `config/scripts/orchestrator_next/pricing.py` | `estimate-cost.sh` (after this change), `record.py` (import) |

### Relevant Files

| File | Role |
|---|---|
| `config/scripts/orchestrator_next/record.py:580-789` | Source of truth for Python pricing logic; functions to extract |
| `config/scripts/estimate-cost.sh:109-325` | Bash reimplementation to replace with Python subprocess calls |
| `config/scripts/inline/preview-route.sh` | Transitively affected; calls `estimate-cost.sh` |
| `scripts/routes.yaml` | Agent→backend→model_id map; both implementations read this |
| `config/scripts/orchestrator_next/migrations/0001_seed_pricing.sql` | Pricing table schema and seed data |
| `config/scripts/orchestrator_next/tests/test_record_cost_compute.py` | 7 tests covering record.py cost path; must pass unchanged |
| `config/scripts/orchestrator_next/tests/test_estimate_cost_sh.py` | 4 scenarios covering bash cost path; must pass unchanged |

### Library Versions / Integration Points

- Python 3.x (`orchestrator_next` package under `config/scripts/`)
- DuckDB (version in use by `orchestrator_next`; accessed via `duckdb` Python package)
- Bash 3.2 (macOS default; `estimate-cost.sh` must remain 3.2-compatible — no `declare -A`, `mapfile`, `readarray`)
- `lru_cache` on `_load_routes()` — must be preserved in extracted module for in-process callers
- In-process `_pricing_cache` dict keyed by `id(db)` — must be preserved; test fixture clears it between tests via `_record_mod._pricing_cache.clear()`
- `ORCHESTRATOR_HOME` env var — used for routes.yaml path resolution by both implementations

### Key Behavioral Facts

- `_compute_cost_usd` call sites in record.py: `record()` (line 1541-1545), `_resolve_driver_session` (line 394), `_write_subagent_events` (line 557)
- `_DATED_MODEL_SUFFIX_RE = re.compile(r"-\d{8}$")` at line 640 of record.py
- DuckDB `__default__` row is the final fallback when model is unresolvable but usage tokens > 0
- `native_*` backend pattern: `native_opus→claude-opus-4-7`, `native_sonnet→claude-sonnet-4-6` in routes.yaml; `native_haiku→claude-haiku-4-5` hardcoded only in bash (OQ-1)
- Test `test_estimate_cost_sh.py` sets `REPO_ROOT`, `ORCHESTRATOR_HOME`, `ROUTES_FILE`, `ARCHIVE_GLOB`, `METRICS_DB` in env for controlled runs — the new CLI module must honor these same env vars
