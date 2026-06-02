---
feature-id: orc-74
linear-ticket: N/A
---

# Discovery Brief: Split record.py god module

## Feature Summary

`orchestrator_next/record.py` is a 1403-line module that owns five distinct concerns: payload validation and output supplementation, statechart routing and boundary detection, state.yaml mutation and next-step computation, feature metrics aggregation (task counts, git churn, review scores, wall-clock), and a thin CLI entry point. The cost-computation concern was already split to `pricing.py` (ORC-71). This feature extracts the remaining separable concern — feature metrics aggregation — into its own module (`metrics.py`) and restructures the payload-supplement helpers into a `payload.py` module, reducing `record.py` to a thin orchestrator that delegates to focused submodules. No behavioral changes; all existing tests pass unchanged.

## Personas & Actors

- **Orchestrator maintainer** — reads and modifies record.py to fix bugs or add step-history features; currently forced to navigate all concerns in one file.
- **Metrics consumer** (complete-phase step scripts, test suite) — imports `_resolve_feature_metrics`, `compute_task_counts`, `extract_review_scores`, etc. directly from `orchestrator_next.record`; import paths must remain stable or be re-exported from `record.py`.
- **Test suite** — 14+ test files import symbols directly from `orchestrator_next.record`; all import paths must remain importable after the split.

## Use Cases

### Happy Path

UC-1: Split succeeds with no import breakage — maintainer runs `pytest orchestrator_next/tests/` after the split and all existing tests pass without modification to test files or import paths.
UC-2: Feature metrics extracted — a future contributor looking for `compute_task_counts` or `run_git_churn` finds them in `orchestrator_next/metrics.py`, not mixed into the state-mutation path.
UC-3: Payload supplement helpers isolated — functions like `_supplement_legacy_outputs`, `_supplement_learn_result`, `_coerce_payload_outputs` are co-located in `orchestrator_next/payload.py`, making the validation surface visible without reading routing code.

### Error & Edge Cases

UC-E1: Circular import — `metrics.py` or `payload.py` imports from `record.py` directly, causing a circular import at module load time; must be avoided by ensuring the dependency graph flows one way (record imports metrics/payload, not vice versa).
UC-E2: Test import breakage — a test does `from orchestrator_next.record import compute_task_counts`; if the function is moved without a re-export, the test breaks; mitigation is to keep all moved names re-exported from `record.py` for one cycle.
UC-E3: `REPEAT_PREDICATES` circular reference — `readiness.py` already does a lazy import of `REPEAT_PREDICATES` from `record.py` to avoid a cycle; if `REPEAT_PREDICATES` is moved to a new module, `readiness.py`'s lazy import path must be updated.

## Scope

### In Scope

- Extract feature metrics functions (`compute_task_counts`, `compute_retries`, `compute_resolution`, `run_git_churn`, `extract_review_scores`, `wall_clock_minutes`, `_resolve_feature_metrics`, `_resolve_feature_metrics_tasks_path`, `_resolve_workflow_artifact_path`) to `orchestrator_next/metrics.py`.
- Extract payload supplement helpers (`_coerce_payload_outputs`, `_artifact_basenames_from_outputs`, `_supplement_legacy_outputs`, `_supplement_learn_result`, `_supplement_backlog_tickets_synced`, `_merge_evidence_block`, `_OUTPUTS_ALLOW_EMPTY_LIST`) to `orchestrator_next/payload.py`.
- Re-export all moved names from `record.py` to preserve existing import paths for tests and production callers.
- Ensure `REPEAT_PREDICATES` stays in `record.py` (or update `readiness.py`'s lazy import path) to avoid breaking the existing cycle workaround.
- All 79+ record-related tests pass after the split without changes to test files.

### Out of Scope

- Extracting statechart routing (`_resolve_routing`, `_find_workflow_node`, `_STATUS_TO_STATE_STATUS`) — these are tightly coupled to the `record()` function's main flow and interleave with state mutation; splitting them adds indirection with minimal LOC reduction.
- Extracting boundary detection (`BoundaryKind`, `_detect_boundary`, `_phase_node_ids`) — already small (50 lines) and `_detect_boundary` is imported directly by tests; leaving it in `record.py` avoids an extra indirection layer.
- Extracting `_compute_next_step` / `_repeat_until_pending` — these coordinate with `readiness.py` imports and the `REPEAT_PREDICATES` dict; extraction would require resolving the circular dependency graph, a larger refactor than this ticket warrants.
- Splitting `record()` itself below its current 334 lines — the function is dense but cohesive; every line participates in the single state-mutation transaction.
- Adding new behavior, changing state.yaml schema, or modifying any test expectations.
- Extracting `pricing.py` re-exports (already done in ORC-71).

## UI Direction

N/A — no UI components.

## Key Decisions

- **Re-export moved names from record.py**: The 14 test files that import from `orchestrator_next.record` must not be touched; re-exporting all moved names from `record.py` (via `from orchestrator_next.metrics import ...` at the module level) satisfies AC-5 without a flag day across tests.
- **metrics.py is a pure computation module**: `_resolve_feature_metrics` is defined in `record.py` but never called there — it is only consumed by tests and (indirectly) by complete-phase step scripts via test imports. Extracting it to `metrics.py` makes its standalone nature explicit.
- **payload.py isolates the supplement/coerce surface**: The eight supplement functions (155 lines) are not imported by any external caller today; they exist only inside `record()`. Grouping them in `payload.py` creates a single place to audit "what can the record boundary tolerate from a malformed payload."
- **Do not extract routing/boundary/next-step logic**: These three concerns each depend on state_raw being mutated in place inside `record()`; breaking them out requires threading mutable state through function boundaries, producing more code not less.
- **Build: extend existing modules, do not introduce new third-party dependencies**: All extraction moves pure Python functions; no new imports outside stdlib and existing project modules.
- **Selected design direction (design-and-draft-artifacts): "Two-module extraction with re-exports"** (complexity S). Auto-heuristic: lowest numeric complexity tied at S between this and the single-combined-module approach; tiebreak on module-reuse count — two-module reuses two established idioms (pricing.py re-export at record.py:488 + readiness lazy-import), the combined approach reuses one. Aggressive extraction (routing/boundary/next-step) was rejected at complexity L because those paths mutate `state_raw` in place and extraction adds code rather than removing it. **Consequence:** record.py drops from 1403 → ≈860 LOC; ticket AC #1 (≤500 LOC for record.py) and AC #3 (~400-LOC record()) are consciously NOT met for record.py — see design.md Non-Goals for the rationale. The two new modules (metrics.py, payload.py) each stay under 500 LOC.
- **Verified against HEAD before design:** `_resolve_feature_metrics` is export-only (no internal caller in record(); confirmed). `_OUTPUTS_ALLOW_EMPTY_LIST` is consumed by `_check_declared_outputs` (record.py:959) which stays in record.py — so the constant moves to payload.py and is re-imported back. `_PHASE_REVIEW_VERDICTS` is read by `_validate_phase_review_output` (stays) and `_phase_review_verdict` (moves) — re-imported back. The 14 record-touching test files are green at HEAD (109 passed, 1 xfailed) — phase gate scopes to exactly these, not the full suite (pre-existing unrelated failures).

## Open Questions

- OQ-1: Should the re-exports in `record.py` be explicit (`from orchestrator_next.metrics import compute_task_counts`) or use `__all__`? Explicit re-exports are more grep-friendly but more verbose; `__all__` requires callers to use `import orchestrator_next.record as record_mod; record_mod.compute_task_counts` which breaks the current test import style.
- OQ-2: After the split, should `_resolve_feature_metrics_tasks_path` (a thin wrapper over `_resolve_workflow_artifact_path`) be collapsed into the caller rather than kept as its own exported function? It is currently imported by two tests — collapsing it would require updating those tests.
