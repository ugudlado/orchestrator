---
feature-id: orc-71
linear-ticket: N/A
---

# Design: Unify Cost Computation Between Python and Shell

## Context

The orchestrator computes LLM cost in two places with two independent
implementations of the same logic. `record.py` (lines 580-789) holds the Python
path: `_orchestrator_home`, `_load_routes`, `_lookup_price`, `_ensure_pricing_cache`,
`_compute_cost_usd`, plus `_DATED_MODEL_SUFFIX_RE`. `estimate-cost.sh`
(lines 59-170) holds a Bash reimplementation: `get_backend`, `get_model`,
`resolve_native`, `lookup_pricing`. Both parse `scripts/routes.yaml`, resolve
agent→backend→model chains, and query the same DuckDB `pricing` table — but they
diverge at seven decision points (see Decisions). Every divergence is a latent
bug: a pricing change applied in one path is silently wrong in the other.

This is a behavior-preserving refactor. The Python path is the reference
implementation (it has the dated-suffix fix from ORC-30, the `effective_from`
date filter, and the 4-column read). The work extracts that logic into a single
module, gives it a CLI entry point, points `estimate-cost.sh` at the CLI, and
makes `record.py` import the shared symbols. The one deliberate behavior change
is removing `estimate-cost.sh`'s hardcoded DB-absent fallback rates (Decision
D-2), which is the bug the user explicitly asked to fix.

Verified caller-site facts (per the learned grep-before-asserting rule):

- `_compute_cost_usd` is called at `record.py:394` (`_resolve_driver_session`),
  `record.py:557` (`_write_subagent_events`), and `record.py:1541-1545`
  (`record()`). All three receive an already-open DuckDB connection (or `None`).
  The connection originates in `record.py:main()` (lines 1844-1872).
- `_lookup_price` and `_pricing_cache` are imported *by name* from
  `orchestrator_next.record` by `test_pricing_lookup.py`,
  `test_pricing_lookup_dated.py`, and `test_record_cost_compute.py`. The test
  fixtures call `_record_mod._pricing_cache.clear()` and
  `_record_mod._load_routes.cache_clear()`. The refactor MUST keep these symbols
  importable from `record.py` and bound to the *same objects* the production
  code uses, or these tests break.
- `estimate-cost.sh` is invoked with a modern `python3` for inline JSON parsing
  (`estimate-cost.sh:168`); it has no `orchestrator_next` import today.
- `preview-route.sh` only shells out to `estimate-cost.sh` and treats a non-zero
  exit / empty stdout as `route_preview.status: estimate_unavailable` — an
  existing, well-formed degraded state.
- The `pricing` table schema (`migrations/0001_seed_pricing.sql`) has columns
  `model_id, input_usd, output_usd, cache_read_usd, cache_creation_usd,
  is_local, effective_from`; `cache_creation_usd` is nullable.

## Goals / Non-Goals

### Goals

- Extract `_orchestrator_home`, `_load_routes`, `_lookup_price`,
  `_ensure_pricing_cache`, `_compute_cost_usd`, `_billable_token_units`, and
  `_DATED_MODEL_SUFFIX_RE` into a new `orchestrator_next/pricing.py` module —
  one implementation of routes resolution and DuckDB pricing lookup.
- Add a CLI entry point to `pricing.py` that prices one or more agents in a
  single process invocation and emits a JSON array, so `estimate-cost.sh` can
  replace its Bash reimplementation with one subprocess call.
- Make `record.py` import the extracted symbols and re-export them by reference,
  so all existing `record.py` consumers and tests keep working unchanged.
- Replace the routes-parsing and pricing-lookup Bash in `estimate-cost.sh`
  (`get_backend`, `get_model`, `resolve_native`, `lookup_pricing`) with one call
  to the Python CLI, while keeping `estimate-cost.sh` a Bash 3.2-compatible
  wrapper.
- Reconcile all seven behavioral divergences (see Decisions) so the two paths
  produce identical pricing for identical inputs.

### Non-Goals

- No change to `cost-report.sh`, `compute-swe-metrics.sh`, or `bin/orchestrator`
  — none of them compute pricing; they read pre-computed columns.
- No change to `preview-route.sh` — it is a thin wrapper over `estimate-cost.sh`
  and is unaffected by how `estimate-cost.sh` computes pricing internally.
- No new pricing rows and no schema changes in `migrations/0001_seed_pricing.sql`.
- No rewrite of `estimate-cost.sh`'s estimator math (archive scan, median
  tokens-per-task, per-agent share, 90/10 input-output split) — only the routes
  resolution and pricing lookup move to Python.
- No DB-absent fallback rates anywhere (see Decision D-2 — this is the removal,
  not a non-goal of capability).
- No `--usage-model` CLI flag — JSONL billing-truth override stays an in-process
  concern (see Decision D-7).

## Approaches Considered

### Approach 1: Shared module + bulk CLI, `record.py` re-exports

Extract the pricing functions into `orchestrator_next/pricing.py`. `record.py`
imports them and re-exports `_lookup_price`, `_pricing_cache`, `_load_routes`,
etc. by reference so existing imports and test fixtures resolve unchanged.
`pricing.py` grows a `main()`/`__main__` CLI that takes `--agents a b c …`,
resolves and prices all of them against the DuckDB in one process, and prints a
JSON array. `estimate-cost.sh` calls this CLI exactly once per invocation and
parses the JSON with the `python3` it already depends on.

Pros: one implementation; one subprocess spawn per preview (no per-agent
startup tax); test contracts preserved via re-export; the bulk CLI is the only
new surface. Cons: `record.py` keeps thin re-export lines (a small, explicit
indirection).

### Approach 2: Shared module + per-agent CLI

Same extraction, but `estimate-cost.sh` calls the CLI once per agent inside its
existing `while` loop. Pros: trivially small CLI (`--agent X`, single object
out); no multi-agent output format to define. Cons: ~8 Python interpreter
starts per preview — wasteful, and the discovery brief (UC-E3) explicitly flags
this as a performance risk. Two invocation styles is also more shell surface.

### Approach 3: Generate the Bash from a Python source of truth

Keep both implementations but generate the Bash pricing functions from a Python
template at build time. Pros: no runtime subprocess. Cons: a code generator is
new machinery to build and maintain; generated Bash is hard to read and debug;
solves a problem (subprocess cost) that Approach 1 already makes negligible.
Over-engineered for an ~8-call-per-preview path.

### Selected Approach

**Approach 1.** It is the simplest design that eliminates the duplication
outright (Approach 3 keeps two implementations; only the source moves).
Complexity **S**: one new module that is mostly a verbatim lift of existing,
already-tested code, plus a small CLI and a Bash simplification. Module reuse is
maximal — the extracted code is the proven `record.py` path. Approach 2 is
rejected because UC-E3 calls out per-agent subprocess overhead as a real
concern and a single bulk call removes it for negligible extra complexity (one
JSON-array format). Approach 3 is rejected by the simplicity gate: a codegen
step is disproportionate machinery for a low-frequency path.

## High-Level Design

### Architecture Overview

```
                       scripts/routes.yaml
                              │
                              ▼
                 orchestrator_next/pricing.py
                  (routes resolution + DuckDB
                   pricing lookup + cost math)
                  ┌───────────┴───────────┐
        import (in-process)         python3 -m … (subprocess)
                  │                         │
                  ▼                         ▼
            record.py                 estimate-cost.sh
       (_compute_cost_usd via      (one CLI call → JSON array,
        re-exported symbols)        replaces get_backend /
                                    get_model / resolve_native /
                                    lookup_pricing)
                              │
                              ▼
                    DuckDB `pricing` table
```

`pricing.py` is the single owner of: loading `routes.yaml`, the
agent→backend→model resolution chain, the dated-suffix strip, the
`effective_from` date filter, the in-process pricing-row cache, and the
per-token cost arithmetic. Both consumers go through it — `record.py` by
in-process import, `estimate-cost.sh` by subprocess.

### Key Abstractions

- **`orchestrator_next.pricing` module** — the pricing library. Public-to-the-
  package functions keep their current underscored names (`_lookup_price`,
  `_compute_cost_usd`, `_load_routes`, `_ensure_pricing_cache`,
  `_billable_token_units`) and module-level state (`_pricing_cache`,
  `_DATED_MODEL_SUFFIX_RE`) so the move is name-stable.
- **Re-export bridge in `record.py`** — `record.py` does
  `from orchestrator_next.pricing import _lookup_price, _compute_cost_usd,
  _load_routes, _ensure_pricing_cache, _billable_token_units, _orchestrator_home,
  _DATED_MODEL_SUFFIX_RE, _pricing_cache`. Because Python imports bind the *same
  object*, `record.py._pricing_cache` is `pricing._pricing_cache` and
  `record.py._load_routes` is `pricing._load_routes`. Test fixtures that mutate
  `_record_mod._pricing_cache` or call `_record_mod._load_routes.cache_clear()`
  therefore act on the live objects the production code uses.
- **Pricing CLI (`python -m orchestrator_next.pricing`)** — a `main(argv)` /
  `__main__` entry point. Input: `--agents <name> [<name> …]`. It opens the
  metrics DuckDB (same path convention as `record.py.main()`:
  `$METRICS_DB`, else `$ORCHESTRATOR_HOME/metrics.duckdb`), resolves and prices
  each agent, and prints a JSON array to stdout. It honors `ORCHESTRATOR_HOME`
  and `METRICS_DB`, the same env vars the test harness sets.

## Low-Level Design

### Components

**Component 1 — `orchestrator_next/pricing.py` (new module).**
Responsibility: own all routes resolution and DuckDB pricing logic. Contents are
lifted verbatim (logic byte-equivalent) from `record.py:580-789`:
`_orchestrator_home`, `_load_routes` (with its `functools.lru_cache`),
`_LOOKUP_SQL`, `_LOAD_ALL_SQL`, `_pricing_cache`, `_ensure_pricing_cache`,
`_DATED_MODEL_SUFFIX_RE`, `_lookup_price`, `_billable_token_units`,
`_compute_cost_usd`. Imports needed: `datetime`, `functools`, `os`, `re`, `sys`,
`pathlib.Path`, `yaml`. Stderr warning prefixes stay `[record]` only if a test
asserts on them; otherwise they may read `[pricing]` — see Decision D-8.

**Component 2 — pricing CLI entry point (in `pricing.py`).**
Responsibility: price a list of agents in one process for `estimate-cost.sh`.
`main(argv)` parses `--agents`; for each agent it resolves the model via the same
chain `_compute_cost_usd` uses (agent→backend→model, native and proxy paths) and
calls `_lookup_price`. The CLI is a **pure pricer** — it does NOT discover or
enumerate agents. `--agents <name> [<name> …]` is **required and must carry a
non-empty list**: invoked with no `--agents` flag, or with `--agents` and zero
names, the CLI exits non-zero with a usage error on stderr and prints nothing on
stdout. There is no no-args "price all routed agents" mode — agent-list
assembly is the caller's responsibility (Component 4). It emits a JSON array,
one object per agent: `{"agent", "backend", "model", "input_usd", "output_usd",
"cache_read_usd", "cache_creation_usd"}` — all four pricing columns
(Decision D-5). If the metrics DB is absent it exits non-zero with a stderr
message and prints nothing on stdout (Decision D-2). A `__main__` guard makes
`python -m orchestrator_next.pricing` work. Resolution with zero usage tokens
(pre-flight has none) means the `__default__` token-backed fallback in
`_compute_cost_usd` does not apply; the CLI resolves model from routes only and
reports `model: null` / pricing-from-`__default__`-row consistently with
Decision D-1's routes coverage. An agent name not present in `routes.yaml` is
still a valid input: it is priced the same way `_compute_cost_usd` handles an
unrouted agent (model unresolved → `__default__` pricing row), so
archive-observed agents that are not in `routes.yaml` still get a priced JSON
entry.

**Component 3 — `record.py` re-export bridge.**
Responsibility: keep `record.py`'s public symbol surface stable. Delete the
function bodies at lines 580-789 and replace with the `from
orchestrator_next.pricing import …` line listed under Key Abstractions. All
existing call sites (`record.py:394, 557, 1541-1545`) keep calling
`_compute_cost_usd` with no signature change.

**Component 4 — `estimate-cost.sh` simplification.**
Responsibility: stay a Bash 3.2-compatible pre-flight wrapper; **own agent-list
assembly**; delegate pricing. `estimate-cost.sh` keeps building `ALL_AGENTS_LIST`
exactly as it does today (verified at `estimate-cost.sh:262-281`): the
deduplicated, sorted **union of (a) agents in `routes.yaml`** and **(b) agents
observed in the archive scan** (`PER_AGENT_SHARE`). This union semantics MUST be
preserved — the archive-scan data lives only in the shell, so the shell is the
sole place that knows the full agent set. The CLI never sees the archive and
must not own this list. Remove only the pricing-resolution Bash: `get_backend`,
`get_model`, `resolve_native`, `lookup_pricing`, and the `AGENT_BACKEND_MAP` /
`BACKEND_MODEL_MAP` awk parsers (lines 59-170). The `routes.yaml` agent names
needed for the union are still parsed from `routes.yaml` by `estimate-cost.sh`
(a small awk pass over the `agents:` block — distinct from the deleted
pricing-resolution awk). Call the CLI once with the full explicit list:
`PYTHONPATH="$ORCHESTRATOR_HOME/config/scripts" python3 -m orchestrator_next.pricing
--agents $ALL_AGENTS_LIST`. Parse the returned JSON array with the `python3` the
script already uses. Per agent, read `input_usd`/`output_usd`/`cache_read_usd`
into the existing `in_price`/`out_price`/`cache_price` variables — the
estimator math downstream is untouched. No `declare -A`, `mapfile`, or
`readarray` may be introduced (Bash 3.2; scenario (d) guards this).

**Component 5 — `routes.yaml` `native_haiku` entry.**
Add `native_haiku: claude-haiku-4-5` to the `backends:` block (Decision D-1).
One line; makes routes.yaml the single source of native-backend truth.

### Data Flow

In-process (UC-1): `record()` → `_compute_cost_usd(db, agent, usage)` →
`_load_routes()` resolves agent→backend→model (or `usage.model` wins, D-7) →
`_lookup_price(db, model, now)` scans the cached pricing rows with the
`effective_from <= now` filter and dated-suffix strip → returns a cost float.

Subprocess (UC-2): `preview-route.sh` → `estimate-cost.sh` builds the
routes ∪ archive agent list → one
`python3 -m orchestrator_next.pricing --agents <full explicit list>` call →
`pricing.main()` resolves+prices the supplied agents against DuckDB → JSON array
on stdout → `estimate-cost.sh` parses it → existing estimator math →
`route_preview` YAML.

### State Management

- `_pricing_cache: dict[int, dict]` — per-connection pricing-row cache keyed by
  `id(db)`, populated on first lookup. It moves to `pricing.py` and is
  re-exported by reference into `record.py`. Test fixtures clear it via
  `_record_mod._pricing_cache.clear()`; the re-export-by-reference guarantee
  (Key Abstractions) keeps that working.
- `_load_routes` `lru_cache(maxsize=1)` — process-lifetime cache of parsed
  `routes.yaml`. Moves to `pricing.py`; re-exported. Test fixtures call
  `.cache_clear()` on it via `record.py`; same-object re-export keeps that valid.
- The CLI subprocess is a fresh process per `estimate-cost.sh` run — its caches
  live and die with that process; no cross-invocation state.

### Error Handling

- **DB absent (D-2):** in-process, `_lookup_price` already returns `None` when
  `db is None` and `_compute_cost_usd` returns `(model, None)`; `record.py`
  records no `cost_usd`. The CLI, when the metrics DB file does not exist, exits
  non-zero and writes a stderr diagnostic — it does NOT print fallback rates.
  `estimate-cost.sh` propagates the non-zero exit; `preview-route.sh` already
  maps a non-zero estimator exit to `route_preview.status: estimate_unavailable`.
  No `15.00 75.00 1.50` hardcoded rates exist after this change.
- **Unresolvable model (UC-E1):** unchanged — `_compute_cost_usd` falls back to
  the `__default__` pricing row when billable tokens > 0, else returns
  `(None, None)`. The CLI, having no usage tokens, reports the model as resolved
  from routes or `null`; `_lookup_price`'s own `__default__` fallback still
  applies so a price is returned.
- **CLI subprocess failure (malformed JSON, Python error):** `estimate-cost.sh`
  treats a non-zero CLI exit or unparseable output the same as DB-absent —
  surfaced upstream as `estimate_unavailable` rather than guessed rates.
- **`routes.yaml` missing / malformed:** `_load_routes` already returns `{}`
  on `FileNotFoundError`/`OSError`/`YAMLError`; unchanged.

## Constraints

- `estimate-cost.sh` must remain Bash 3.2-compatible: no `declare -A`, no
  `mapfile`, no `readarray`, no `${var^^}`. `test_estimate_cost_sh.py`
  scenario (d) is the regression guard.
- The pricing CLI must be invokable as `python3 -m orchestrator_next.pricing`
  with `config/scripts/` on `PYTHONPATH`. `estimate-cost.sh` already has
  `$ORCHESTRATOR_HOME`; it sets
  `PYTHONPATH="$ORCHESTRATOR_HOME/config/scripts"` for the call.
- The CLI must honor `ORCHESTRATOR_HOME`, `METRICS_DB`, and `ROUTES_FILE`/routes
  resolution consistent with the env vars `test_estimate_cost_sh.py` sets.
- `record.py`'s public pricing symbols must stay importable from
  `orchestrator_next.record` and bound to the same objects `pricing.py` uses.

## Trade-offs

- **One subprocess spawn per preview** is accepted: `estimate-cost.sh` runs once
  per workflow preview (~8 agents), not in any hot loop. The bulk CLI keeps it
  to a single Python start. Performance target: `preview-route.sh` completes in
  well under 2 seconds wall-clock for an ~8-agent cold-start preview on a
  developer laptop — a real end-to-end budget, not a tight-loop microbenchmark.
- **`record.py` keeps thin re-export lines** instead of importing `pricing` and
  using a `pricing.` prefix everywhere. This is a deliberate, minimal indirection
  that preserves the test contract (`_record_mod._pricing_cache`,
  `_record_mod._load_routes`) with zero test edits. The alternative — rewriting
  every test import — is more churn for no benefit.
- **One deliberate behavior change** (D-2: DB-absent no longer yields fabricated
  rates). It is in scope and intended: the old fallback produced wrong cost
  numbers silently; `estimate_unavailable` is the honest state.

## Acceptance Criteria

- AC-1: Given a completed step, when `record()` runs with an open metrics DB,
  then `_compute_cost_usd` (now imported from `orchestrator_next.pricing`)
  resolves agent→backend→model and returns the same `cost_usd` it returned
  before the refactor; all 7 tests in `test_record_cost_compute.py` pass
  unchanged. [traces: UC-1]
- AC-2: Given `orchestrator_next/pricing.py` exists, when
  `test_pricing_lookup.py` and `test_pricing_lookup_dated.py` run with their
  current `from orchestrator_next.record import _lookup_price` imports and their
  `_record_mod._pricing_cache.clear()` / `_load_routes.cache_clear()` fixtures,
  then every test passes unchanged — proving the re-export bridge binds the same
  objects. [traces: UC-1, UC-3]
- AC-3: Given a running pre-flight preview, when `estimate-cost.sh` is invoked,
  then it calls `python3 -m orchestrator_next.pricing` exactly once (verifiable:
  no `get_backend`/`get_model`/`resolve_native`/`lookup_pricing` functions
  remain in the script; `grep -c` on those names returns 0) and produces a
  `route_preview` YAML block; `test_estimate_cost_sh.py` scenarios (a), (b), (d)
  pass unchanged. [traces: UC-2]
- AC-4: Given the metrics DB file is absent, when `estimate-cost.sh` runs, then
  the pricing CLI exits non-zero with a stderr diagnostic and no fabricated
  rates appear anywhere; `test_estimate_cost_sh.py` scenario (c) — updated per
  Decision D-2 — asserts this fail-loud behavior (CLI non-zero exit / estimator
  surfaces `estimate_unavailable`), not the old default-rate behavior.
  [traces: UC-E2]
- AC-5: Given `native_haiku` is referenced as a backend, when the pricing CLI or
  `_compute_cost_usd` resolves it, then it resolves to `claude-haiku-4-5` via
  `routes.yaml` (not a hardcoded map); `routes.yaml` `backends:` contains
  `native_haiku: claude-haiku-4-5`. [traces: UC-E1]
- AC-6: Given future-dated and dated-suffix model rows, when the pricing CLI
  prices a model, then it applies the `effective_from <= now` filter and the
  `-YYYYMMDD` suffix strip identically to the in-process path — the CLI and
  `_compute_cost_usd` return the same rate for the same model and time.
  [traces: UC-2, UC-3]
- AC-7: Given the pricing CLI is invoked with `--agents` listing N agents
  supplied by the caller, when it runs, then it spawns exactly one Python
  process and emits a JSON array of N objects, each carrying all four pricing
  columns (`input_usd`, `output_usd`, `cache_read_usd`, `cache_creation_usd`);
  an agent name not present in `routes.yaml` (e.g. an archive-observed agent)
  still produces a priced entry, and invoking the CLI with no `--agents` flag or
  an empty list exits non-zero with a usage error. [traces: UC-2, UC-E3]
- AC-8: Given `estimate-cost.sh` runs a preview where the archive scan observed
  an agent that is absent from `routes.yaml`, when the preview is produced, then
  that agent still appears in the `route_preview` agents list — the routes ∪
  archive-observed union semantics are preserved after the rewire. [traces: UC-2]

## Decisions

- D-1 (OQ-1, native_haiku) → `estimate-cost.sh:resolve_native()` hardcoded
  `native_haiku→claude-haiku-4-5` while `routes.yaml` only had `native_opus` and
  `native_sonnet`. Resolution: add `native_haiku: claude-haiku-4-5` to
  `routes.yaml` `backends:`. → Routes.yaml becomes the single source of native-
  backend truth; the unified module needs no embedded override map. Adding a
  *backend* entry is in scope (Out-of-Scope covers only *pricing-table* rows).
- D-2 (OQ-2, DB-absent fallback) → LOCKED by the user. `estimate-cost.sh`
  previously substituted hardcoded `15.00 75.00 1.50` rates when the DB was
  absent; that produced silently-wrong cost numbers. Resolution: remove the
  fallback entirely. The DB is expected to always be present; when it is not,
  the pricing CLI fails loud (non-zero exit, stderr diagnostic, no stdout) and
  in-process `_lookup_price` returns `None`. There is no `--fallback-rates`
  flag. → `estimate_cost.sh` propagates the failure; `preview-route.sh` reports
  `estimate_unavailable`. **AC #4 of the ticket is amended:** all of
  `test_record_cost_compute.py` passes unchanged, and `test_estimate_cost_sh.py`
  passes unchanged EXCEPT scenario (c), which is updated to assert the fail-loud
  behavior (task T-7). The old hardcoded fallback is the bug being removed, so
  that one test must change.
- D-3 (OQ-3, `effective_from` filter) → `record.py` filtered
  `effective_from <= now`; `estimate-cost.sh` used `ORDER BY effective_from DESC
  LIMIT 1` with no date filter, so future-dated pricing would wrongly apply
  today. Resolution: the unified module keeps the `effective_from <= now`
  filter (the correct, ORC-tested behavior). No flag. → Future-dated rows no
  longer leak into pre-flight estimates.
- D-4 (OQ-4, dated-suffix stripping) → `record.py` strips `-YYYYMMDD` suffixes
  (ORC-30); `estimate-cost.sh` did not. Resolution: the unified module always
  strips (the existing `_DATED_MODEL_SUFFIX_RE` behavior), so the CLI path gets
  the fix for free. No flag. → Dated model IDs price against their base model
  in both paths.
- D-5 (OQ-5, `cache_creation_usd`) → `record.py` reads 4 pricing columns;
  `estimate-cost.sh` read 3. Resolution: the CLI reads and emits all four
  columns in its JSON output. `estimate-cost.sh` keeps reading the three it
  needs (`input_usd`, `output_usd`, `cache_read_usd`) into its existing
  variables — its estimator math is unchanged — and simply ignores the fourth.
  → One canonical output shape; in-process callers already use all four.
- D-6 (OQ-6, CLI invocation mode) → Resolution: bulk mode only, with an
  **explicit caller-supplied `--agents` list**. The CLI takes `--agents a b c …`
  (required, non-empty) and prices exactly those agents in one process;
  `estimate-cost.sh` calls it exactly once per preview. The CLI is a pure pricer
  and does NOT discover or enumerate agents — there is no no-args "price all
  routed agents" mode. Agent-list ownership stays with `estimate-cost.sh`, which
  is the only place with archive-scan context: it builds the routes ∪
  archive-observed union (see Component 4, verified at `estimate-cost.sh:262-281`)
  and passes the full list explicitly. A per-agent CLI mode is rejected (UC-E3:
  ~8 interpreter starts per preview is wasteful; two invocation styles is extra
  surface). → One subprocess spawn per preview; one JSON-array output format;
  one source of truth for "which agents to price" (the shell) and one for "what
  an agent costs" (the CLI).
- D-7 (OQ-7, `usage.model` precedence) → `record.py` Step 0 prefers
  `usage['model']` from JSONL as billing truth. The pre-flight path has no JSONL
  context. Resolution: `usage.model` override stays an in-process concern — it
  is already expressed by `_compute_cost_usd` reading `usage.get("model")`; no
  `--usage-model` CLI flag is added. → The CLI resolves model from routes only;
  the in-process path keeps JSONL precedence. No new flag surface.
- D-8 (stderr warning prefix) → The lifted functions emit `[record]`-prefixed
  stderr warnings. `test_pricing_lookup.py` asserts only that *some* stderr text
  is emitted (`assert captured.err`), not the prefix. Resolution: the prefix may
  become `[pricing]` for accuracy, since no test pins it. → If any test is found
  to assert the literal `[record]` prefix during implementation, keep `[record]`;
  the implementing task verifies this with a grep before changing the string.

## Open Questions

- None. All seven discovery open questions (OQ-1 … OQ-7) are resolved in
  Decisions D-1 … D-7.

<!-- Format contract: contracts/artifact-formats.md § Design Format Contract -->
