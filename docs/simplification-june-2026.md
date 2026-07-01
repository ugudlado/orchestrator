# orchestrator_next simplification — June 2026

Multi-session refactor drove the Opus simplicity score from 5/10 → 9/10.
Commit: `0a02256` on `main`.

**Why:** The package had accumulated dead machinery from earlier spec iterations
that was never exercised by any live contract. Keeping it added ~2000 lines of
false complexity and misled future readers about what the engine actually does.

## What was deleted (all verified no-ops before removal)

- **Typed I/O machinery** (`_resolve_inputs`, `_check_required_inputs`,
  `_typed_input_base_dir`, `_check_declared_outputs` path-branch,
  `optional_inputs` on `StepContract`) — 0/21 live contracts set `path:` so
  every branch was unreachable in production
- **`REPEAT_PREDICATES` / `_repeat_predicate_satisfied`** from `readiness.py`
  and `record.py` — always returned True; no contract uses `repeat_until`
- **`_load_step_contract`** from `dispatch.py` — dead wrapper never called by
  live `_handle_resume` / `_dispatch_fresh` paths
- **Legacy `active:[ids]` back-compat synthesis** in `phase_nodes()` —
  all on-disk state files use the `nodes:` shape
- **Flat-file contract loading path** from `parser.py` — all 21 contracts are
  directory-form (`contract.yaml` + `prompt.md`)
- **`legacy_input_names` / `legacy_output_names`** from `StepContract` —
  tracking artifacts that no call site consumed
- Two dead test files deleted

## What was renamed / extracted

- `_resolve_allowed_tools` → `_warn_allowed_tools` (it never returns tools;
  it only emits warnings — name now matches behavior)
- Extracted `_fallback_contract`, `_resolve_step_contract_dir`,
  `_warn_if_more_phases_remain` from inline blocks in `dispatch()`
- `ContractNotFoundError as ParserContractDispatchError` alias removed
- `_parse_io_specs`: 3-form → 2-form (path branch gone), no longer returns
  `optional_names` list alongside the spec list
- Mid-file imports in `record.py` moved to module top
- `history[:] = [...]` slice-assignment simplified to direct assignment

## What was deliberately kept

- `completed_at` alias in `_parse_history_entry` — live on-disk state files
  use `completed_at`; deleting it would silently drop history for real runs
- Two separate `_compute_attempt` implementations (dispatch vs record) —
  they have intentionally different semantics (dispatch counts `in_progress`;
  record excludes them); documented, not merged

## Current state

419 tests green, Opus score 9/10.

When navigating `orchestrator_next/` assume the package is clean. There is no
typed I/O, no `repeat_until` loop, no flat-file contracts, no legacy
back-compat layers. `readiness.py` is the single node-status mutator.
