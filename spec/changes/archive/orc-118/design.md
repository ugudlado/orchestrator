---
feature-id: orc-118
linear-ticket: ORC-118
---

# Design: User-level model config (~/.orchestrator/models.yaml + orchestrator models verb)

## Context

`orchestrator_next/model_routes.py::resolve_field` currently resolves a model tier
(e.g. `opus`, `sonnet`) to `{subprocess, model_id}` from two layers:

1. `<config_root>/models.yaml` (bundled, checked-in).
2. `$ORCHESTRATOR_MODELS_CONFIG` file (per-shell override).
3. `$ORCHESTRATOR_MODEL_ROUTE_OVERRIDES` JSON env (per-invocation override).

Any user who wants to swap `opus → sonnet` today must either edit the checked-in
config or set `ORCHESTRATOR_MODELS_CONFIG` in their shell. Both fail for
casual/first-time users. This feature adds a home-dir file
(`~/.orchestrator/models.yaml`) as a fourth layer between (1) and (2), plus
tooling to inspect the effective routing and its source per tier.

### Verified System Boundaries

- `_models_map(path)` at `orchestrator_next/model_routes.py:19` already tolerates
  missing/malformed files (`safe_load … or {}`; returns `{}` when path is not a
  file). Adding one more layer needs no new error handling.
- `resolve_field` at `orchestrator_next/model_routes.py:28` uses `dict.update`
  layering — per-tier merge is already correct; the new layer plugs in
  identically.
- `_core_verbs` tuple at `orchestrator_next/cli.py:281` is the registration
  point for new top-level verbs; the dispatch pattern (see `doctor`,
  `validate-workflow` at cli.py:297,322) is `if args[0] == "<verb>": …; sys.exit(...)`.
- `check_subprocesses_available` at `orchestrator_next/doctor.py:274` already
  loads `config_root/models.yaml`; adding a sibling `check_model_route_sources`
  reuses the same config_root argument threaded through `run_all`.
- No existing test file targets `model_routes.py` directly (only
  `test_pricing_no_fallback.py` imports it transitively). A new
  `orchestrator_next/tests/test_model_routes.py` is safe to create.
- Baseline `pytest orchestrator_next/tests/` has 1 pre-existing failure
  (`test_step_env.py::test_inline_script_env_sets_legacy_and_orchestrator_aliases`),
  unrelated to this feature — phase-gate task MUST scope verify to the feature
  test files, not the full suite.

## Goals / Non-Goals

### Goals

- A user can set `~/.orchestrator/models.yaml` and see it take effect on the
  next `orchestrator run` with no env vars.
- `orchestrator models` prints a table of tier → subprocess → model_id → source
  file.
- `orchestrator doctor` reports which file each tier resolved from.
- Full precedence preserved: `ORCHESTRATOR_MODEL_ROUTE_OVERRIDES` (env JSON) >
  `ORCHESTRATOR_MODELS_CONFIG` (env file) > `~/.orchestrator/models.yaml` >
  `<config_root>/models.yaml`.

### Non-Goals

- JSON/machine-readable output for `orchestrator models` (v1 is plain text; OQ-1
  deferred).
- Phase 2 pack manager (`pack add/remove/list`) — separate ticket.
- Changes to `ORCHESTRATOR_MODEL_ROUTE_OVERRIDES` JSON env handling.
- Any GUI/dashboard changes.
- New merge semantics beyond the existing per-tier `dict.update` pattern.

## Approaches Considered

### Approach 1: Minimal layer insertion + parallel source-aware helper (S)

Add `~/.orchestrator/models.yaml` between the env file and routes_yaml in
`resolve_field` (2-line change). Add a sibling `resolve_all_with_source(routes_yaml)
-> dict[tier, {subprocess, model_id, source}]` that walks the same layer chain
and records which file provided each field. `models` verb + doctor check both
consume the sibling helper.

- Pros: shortest diff, mirrors existing pattern, no API break, `resolve_field`
  stays a one-liner.
- Cons: source-attribution logic lives in a second function that mirrors the
  layering; small duplication.

### Approach 2: Refactor to layered `ModelRoutes` class (M)

Introduce a `ModelRoutes(routes_yaml)` class that holds an ordered list of
`(label, path, dict)` layers; `resolve_field` becomes a thin wrapper and
source-attribution comes for free.

- Pros: single source of truth for layering; extending to a 5th layer is trivial.
- Cons: bigger surface, more test churn, callers stay call-site compatible only
  if `resolve_field` shim is preserved. YAGNI — the layer count is stable.

### Approach 3: Merged-effective-map cache (M)

Build a merged `tier -> (entry, source_map)` on first call, cache per-process.

- Pros: cheap repeated lookups.
- Cons: cache invalidation for tests (env changes between calls); current call
  volume per run is small — no performance need.

### Selected Approach

**Approach 1.** Lowest complexity (S), highest reuse of existing `_models_map`,
zero API churn for `resolve_subprocess` / `resolve_model_id` / `resolve_field`
callers. Approaches 2 and 3 add abstraction with no concrete second consumer to
justify it (violates "no unrequested abstractions").

## High-Level Design

### Architecture Overview

```
                     ┌──────────────────────────────────────────┐
 orchestrator run    │ resolve_field(model, routes_yaml, field) │
        │            │                                          │
        └──────────► │ layers (low → high precedence):          │
                     │  1. routes_yaml                          │
                     │  2. ~/.orchestrator/models.yaml   ← NEW  │
                     │  3. $ORCHESTRATOR_MODELS_CONFIG          │
                     │  4. $ORCHESTRATOR_MODEL_ROUTE_OVERRIDES  │
                     └──────────────────────────────────────────┘

 orchestrator models ──► resolve_all_with_source(routes_yaml)
                              │
                              ▼
                     prints: tier | subprocess | model_id | source

 orchestrator doctor ──► check_model_route_sources(config_root)
                              │
                              ▼
                     row: "model route sources" PASS/WARN
```

### Key Abstractions

- **Layer chain** — ordered list `[(label, path)]` walked low → high.
  `resolve_field` iterates it silently; `resolve_all_with_source` iterates and
  records the label/path at which each tier's field was last written.
- **`user_models_path()`** — one function returning
  `Path.home() / ".orchestrator" / "models.yaml"`. Isolatable in tests by
  monkeypatching `Path.home` or by exposing the layer chain to tests.

## Low-Level Design

### Components

**`orchestrator_next/model_routes.py`** (edit)

- Add `user_models_path() -> Path`: returns `Path.home() / ".orchestrator" / "models.yaml"`.
- Add module-level constant / helper `_layer_chain(routes_yaml) -> list[tuple[str, str | None]]`
  producing `[("config_root", routes_yaml), ("user_home", str(user_models_path())), ("env_file", os.environ.get("ORCHESTRATOR_MODELS_CONFIG") or "")]`.
- Modify `resolve_field`: iterate `_layer_chain(routes_yaml)` doing per-tier
  `entry.update(_models_map(path).get(model) or {})`; then apply
  `ORCHESTRATOR_MODEL_ROUTE_OVERRIDES` as before.
- Add `resolve_all_with_source(routes_yaml) -> dict[str, dict]`: for every tier
  seen across the chain, return
  `{tier: {"subprocess": val, "subprocess_source": label_or_path,
           "model_id": val, "model_id_source": label_or_path}}`. Source strings
  are the file path (or `"$ORCHESTRATOR_MODEL_ROUTE_OVERRIDES"` for the env-JSON
  override).

**`orchestrator_next/cli.py`** (edit)

- Add `"models"` to `_core_verbs` at cli.py:281.
- Add dispatch:
  `if args[0] == "models": from orchestrator_next.models_verb import main as _m; sys.exit(_m(args[1:]))`
  after the `doctor` dispatch (line ~299).

**`orchestrator_next/models_verb.py`** (new, ~30 lines)

- `main(argv) -> int`: resolve `config_root() / "models.yaml"` (via
  `orchestrator_next.paths.config_root`). On failure, print
  `error: no config root (set ORCHESTRATOR_CONFIG)` to stderr, return 1.
- Call `resolve_all_with_source(str(routes_yaml))`; print aligned table with
  columns `TIER  SUBPROCESS  MODEL_ID  SOURCE`. Return 0.

**`orchestrator_next/doctor.py`** (edit)

- Add `check_model_route_sources(config_root)`: call
  `resolve_all_with_source(str(config_root / "models.yaml"))`; produce a
  single `CheckResult("model route sources", "PASS",
  "<n> tiers resolved: opus←user_home, sonnet←config_root, …")`.
  Never FAIL (this is provenance reporting, not integrity).
- Append to check list in `run_all` (around doctor.py:362).

**`orchestrator_next/tests/test_model_routes.py`** (new)

- Uses `tmp_path` + `monkeypatch` to redirect `Path.home` and env vars.
- Covers UC-1, UC-3, UC-4, UC-E1, UC-E2 + `resolve_all_with_source` shape.

**`orchestrator_next/tests/test_models_verb.py`** (new)

- Uses `capsys` + `monkeypatch`. Covers UC-2, UC-E3.

**`orchestrator_next/tests/test_doctor_model_sources.py`** (new)

- Runs `check_model_route_sources` against a `tmp_path` config root. Covers UC-5.

### Data Flow

`orchestrator run <id>` → `run_loop` → `resolve_subprocess(model, routes_yaml)`
→ `resolve_field(model, routes_yaml, "subprocess")` → walks 4-layer chain →
returns the highest-precedence value.

`orchestrator models` → `models_verb.main` → resolves `config_root/models.yaml`
→ `resolve_all_with_source` walks the same chain, keeping the last-writing
label per (tier, field) → prints table to stdout.

`orchestrator doctor` → `check_model_route_sources` → same helper → PASS row.

### State Management

None. Pure functions over env + files. Each call re-reads YAML (matching
existing behavior of `_models_map`).

### Error Handling

- Malformed home YAML → `_models_map` returns `{}` (existing behavior); layer
  is silently skipped. Verified in UC-E1 test.
- Missing home file → `_models_map` returns `{}`. UC-E2 test asserts fall-through.
- `orchestrator models` with no config root → catches `RuntimeError` from
  `config_root()`, prints error, exits 1. UC-E3.

## Constraints

- Must not break existing `resolve_subprocess` / `resolve_model_id` /
  `resolve_field` callers (`run_loop.py`, `test_pricing_no_fallback.py`).
- Home path must be `~/.orchestrator/models.yaml` (fixed by ticket).
- Test suite has 1 pre-existing failure — phase-gate verify commands must
  scope to feature test files only.

## Trade-offs

- Two near-parallel walkers (`resolve_field` and `resolve_all_with_source`)
  instead of one layered object. Acceptable: keeps `resolve_field`'s
  1-line-per-layer form and preserves its call site simplicity. If a 5th
  layer arrives, refactor is a ~15-line change.

## Acceptance Criteria

- AC-1: With `~/.orchestrator/models.yaml` setting `opus.subprocess: cursor`,
  no env vars set, `resolve_subprocess("opus", "<config_root>/models.yaml")`
  returns `"cursor"`. [traces: UC-1]
- AC-2: With both `~/.orchestrator/models.yaml` and
  `ORCHESTRATOR_MODELS_CONFIG` file defining `opus`, the env-file value wins;
  with only home set, home wins; with neither, config-root wins.
  [traces: UC-3, UC-4]
- AC-3: With `~/.orchestrator/models.yaml` defining only `sonnet`,
  `resolve_subprocess("opus", <config_root>)` falls through to config-root.
  [traces: UC-E2]
- AC-4: With malformed YAML (`":\n  not: [yaml"`) in
  `~/.orchestrator/models.yaml`, `resolve_field` does not raise and returns
  the config-root value (or `""`). [traces: UC-E1]
- AC-5: `orchestrator models` prints a table with columns
  `TIER`, `SUBPROCESS`, `MODEL_ID`, `SOURCE`, one row per tier defined in the
  effective config, with the SOURCE column naming the file that supplied each
  tier's values. Exit code 0. [traces: UC-2]
- AC-6: `orchestrator models` with `ORCHESTRATOR_CONFIG` unset prints an error
  to stderr and exits non-zero. [traces: UC-E3]
- AC-7: `orchestrator doctor` output includes a `model route sources` check row
  listing per-tier source labels; check status is `PASS`. [traces: UC-5]
- AC-8: `pytest orchestrator_next/tests/test_model_routes.py
  orchestrator_next/tests/test_models_verb.py
  orchestrator_next/tests/test_doctor_model_sources.py` all green.
  [traces: all]

## Decisions

- **Sibling helper vs class refactor** → Approach 1 → mirrors current pattern
  and keeps `resolve_field` at ~5 lines.
- **`orchestrator models` as a top-level verb** → not a `doctor` subcommand
  → matches ticket wording ("orchestrator models prints…") and is scriptable.
- **Layer order: `routes_yaml` then home then env file (low → high)** → matches
  ticket precedence table verbatim.
- **`--json` deferred** → v1 is plain text; OQ-1 recorded as a follow-up.

## Open Questions

- OQ-1 (deferred): `--json` output for `orchestrator models`. Not blocking.
