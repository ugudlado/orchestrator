---
feature-id: orc-118
linear-ticket: ORC-118
---

# Discovery Brief: User-level model config (auto-load ~/.orchestrator/models.yaml + orchestrator models verb)

## Feature Summary

ORC-118 adds a well-known user home-dir path (`~/.orchestrator/models.yaml`) to the model-route resolution chain in `model_routes.py`, sitting between the existing `ORCHESTRATOR_MODELS_CONFIG` env file and the bundled config-root `models.yaml`. A new `orchestrator models` CLI verb prints the effective routing per tier with source-file attribution. The `doctor` command gains per-tier source reporting. Together these let any user switch model tiers (e.g. opus → sonnet, claude → cursor) by editing one file in their home directory — no env vars, no checkout access, no engine knowledge required.

## Personas & Actors

- **Engine user** — developer or CI operator running `orchestrator run`. Wants to swap model tiers without touching checked-in config or setting shell env vars.
- **Orchestrator engine** (`model_routes.py`, `run_loop.py`) — resolves model tier → subprocess + model_id at dispatch time. Gains an additional lookup layer.
- **CLI** (`cli.py`) — dispatches the new `models` verb.
- **Doctor** (`doctor.py`) — reports model resolution provenance.

## Use Cases

### Happy Path

UC-1: Swap model tier in home dir — engine user edits `~/.orchestrator/models.yaml` (or creates it), setting `opus.model_id: claude-opus-4-8`, then runs `orchestrator run <id>` without any env vars. The engine picks up the home-file override for the next run.

UC-2: Inspect effective routing — engine user runs `orchestrator models` and sees a table of every tier with its resolved subprocess, model_id, and source file (e.g. `~/.orchestrator/models.yaml` or `<config_root>/models.yaml`).

UC-3: Per-run env override still wins — engine user sets `ORCHESTRATOR_MODELS_CONFIG=/tmp/my.yaml` for a single invocation; the env file's values win over the home-dir file for every tier it defines.

UC-4: Config-root fallback — neither home-dir file nor env override exists for a tier; resolution falls through to `<config_root>/models.yaml` as before.

UC-5: Doctor shows resolution source — engine user runs `orchestrator doctor` and the model-resolution check lists which file each tier resolved from.

### Error & Edge Cases

UC-E1: Malformed home-dir file — `~/.orchestrator/models.yaml` exists but contains invalid YAML; `_models_map` returns `{}` (safe_load returns None/raises), resolution falls through to the next layer without crashing.

UC-E2: Home-dir file exists but missing a tier — `~/.orchestrator/models.yaml` defines `sonnet` but not `opus`; `opus` falls through to config-root `models.yaml` per the existing merge pattern.

UC-E3: `orchestrator models` with no config root — config-root resolution fails (ORCHESTRATOR_CONFIG unset); command prints a clear error and exits non-zero.

## Scope

### In Scope

- `model_routes.py`: add `~/.orchestrator/models.yaml` lookup between `ORCHESTRATOR_MODELS_CONFIG` and `routes_yaml` in `resolve_field`.
- New `orchestrator models` verb: prints tier → subprocess + model_id + source file for every tier in the effective config.
- `doctor.py`: extend `check_subprocesses_available` (or add a sibling check) to report which file each tier resolved from.
- Tests: update existing `model_routes` tests; add tests for the new home-dir lookup and `models` verb.

### Out of Scope

- Phase 2 pack manager (`pack add/remove/list`) — separate ticket per plan-config-repo-split.md.
- Phase 3 repo extraction — deferred until a second consumer exists.
- Any changes to `ORCHESTRATOR_MODEL_ROUTE_OVERRIDES` JSON env handling — already correct, no change needed.
- GUI/dashboard changes — model routing is a CLI/engine concern only.
- Merging partial tier definitions across all three files (the existing `dict.update` pattern already handles per-tier merge; no new merge logic needed beyond adding one more layer).

## UI Direction

N/A — no UI components. The `orchestrator models` verb is a terminal command printing a plain text table.

## Key Decisions

- **Home-dir path is `~/.orchestrator/models.yaml`**: matches the plan doc and provides a consistent namespace for future user-level config (e.g. Phase 2's user-level install manifest). No alternative path considered.
- **Reuse `dict.update` merge pattern**: each layer merges per-tier over the previous, so a home-dir file that only defines `sonnet` leaves `opus` resolved from config-root. Consistent with the existing 2-layer pattern.
- **`orchestrator models` as a new top-level verb** (not a subcommand of `doctor`): makes it directly discoverable and scriptable. `doctor` still reports source — but as a check row, not as interactive output.
- **Build vs. reuse**: extend `model_routes.py::resolve_field` (5-line change) and add a thin `_models_verb` function in `cli.py`. No new modules needed.
- **Selected design (2026-07-13)**: Approach 1 — minimal layer insertion in `resolve_field` + parallel `resolve_all_with_source` helper for provenance. Rationale: complexity S, mirrors existing `_models_map` + `dict.update` pattern, zero API break for existing callers. Approach 2 (ModelRoutes class) and Approach 3 (merged-map cache) rejected as unjustified abstraction. See `design.md` for full comparison.
- **Phase-gate scope (2026-07-13)**: baseline `pytest orchestrator_next/tests/` has 1 pre-existing failure (`test_step_env`) unrelated to this feature. Phase-gate task (T-6) scopes `verify` to the three new test files + `test_pricing_no_fallback.py` (closest existing consumer of `model_routes`), not the full suite.

## Open Questions

- OQ-1: Should `orchestrator models` output machine-readable JSON (e.g. with `--json`) alongside the human table, or is plain text sufficient for v1? (Current ticket says "print effective routing" — assume plain text unless ACs say otherwise.)
- OQ-2: Should the `models` verb be a registered core verb in `cli.py`'s `_core_verbs` tuple, or dynamically discovered? (Recommend: add to `_core_verbs` — it's engine-level, not workflow-level.)
