---
feature-id: orc-74
linear-ticket: N/A
---

# Design: Split record.py god module

## Context

`orchestrator_next/record.py` is 1403 lines spanning five concerns: payload
validation / output supplementation, statechart routing + boundary detection,
state.yaml mutation + next-step computation, feature-metrics aggregation (task
counts, git churn, review scores, wall-clock), and a thin CLI entry point. The
cost-computation concern was already extracted to `pricing.py` (ORC-71), which
established the repo's re-export precedent: moved symbols are re-imported into
`record.py` at module level (record.py:488) so existing callers and test imports
keep binding the same live objects.

Two of the remaining concerns are mechanically separable with no behavior change:
**feature-metrics computation** (`compute_task_counts`, `run_git_churn`, etc., ≈390
lines) and **payload-supplement helpers** (`_supplement_legacy_outputs`, etc., ≈155
lines). Both are pure functions with no back-edges into `record()`'s state-mutation
transaction. The other three concerns (routing, boundary detection, next-step) read
and mutate `state_raw` in place inside `record()` and cannot be lifted without
threading mutable state through new function boundaries — producing more code, not
less.

The change is sequenced after the DAG epic (ORC-65 landed; dispatch/state-mutation
paths are stable), satisfying the ticket's coordination constraint (AC #4).

## Goals / Non-Goals

### Goals

- Extract feature-metrics functions to `orchestrator_next/metrics.py` (ticket AC #2).
- Extract payload-supplement helpers to `orchestrator_next/payload.py`.
- Re-export every moved name from `record.py` so no test file or production caller
  changes its import path (ticket AC #5).
- Reduce `record.py` from 1403 to ≈860 LOC by removing ≈545 lines of extracted code.

### Non-Goals

- **record.py is NOT reduced to ~500 LOC.** Ticket AC #1 ("no module exceeds ~500
  LOC") and AC #3 ("record() a thin ~400-LOC orchestrator") are **not literally met
  for record.py** under this scope. After extracting the two separable concerns,
  record.py lands at ≈860 LOC. Reaching 500 would require extracting
  routing/boundary/next-step, which are cohesive with `record()`'s in-place
  `state_raw` mutation — splitting them threads mutable state through new boundaries
  and adds indirection for no LOC saving. This is the deliberate descope flagged in
  the discovery brief. The two **new** modules (metrics.py ≈400, payload.py ≈200)
  each stay under 500 and own one concern, satisfying the spirit of AC #1.
- Not extracting routing (`_resolve_routing`, `_find_workflow_node`).
- Not extracting boundary detection (`BoundaryKind`, `_detect_boundary`,
  `_phase_node_ids`) — small (≈50 lines) and `_detect_boundary` / `BoundaryKind`
  are imported directly by tests.
- Not extracting `_compute_next_step` / `_repeat_until_pending` — these coordinate
  with `readiness.py` and `REPEAT_PREDICATES`.
- Not moving `REPEAT_PREDICATES` — `readiness.py` lazy-imports it from `record.py`
  (readiness.py:58, :131); moving it would break that cycle workaround.
- No behavior change, no state.yaml schema change, no test expectation change.

## Approaches Considered

### Approach 1: Two-module extraction with re-exports (metrics.py + payload.py)

Move the two self-contained clusters to new modules; re-export all moved names from
`record.py` via module-level `from orchestrator_next.<mod> import ...`, mirroring the
ORC-71 pricing.py precedent.

- **Pros:** Zero import-path churn (tests untouched); dependency graph flows one way
  (record → metrics, record → payload); mirrors an established, tested pattern;
  small, reviewable diff (pure move + re-export).
- **Cons:** record.py still imports the moved names back, so it remains a hub — but
  that is exactly what the pricing.py precedent does and is invisible to callers.
- **Complexity:** S (module reuse: 2 existing modules' patterns reused — pricing
  re-export idiom, readiness lazy-import idiom).

### Approach 2: Single combined `record_helpers.py` module

Move both clusters into one helper module rather than two.

- **Pros:** One new file instead of two.
- **Cons:** Conflates two unrelated concerns (metrics aggregation vs. payload
  validation) in one module — defeats AC #2's intent ("metrics extraction moved to
  its **own** module") and produces a ≈545-line grab-bag that itself approaches the
  500-LOC bar. Lower discoverability: a contributor hunting `run_git_churn` would not
  look in a file named for payloads.
- **Complexity:** S.

### Approach 3: Aggressive extraction including routing/boundary/next-step

Additionally lift routing, boundary detection, and next-step computation to hit the
literal ≤500-LOC target for record.py.

- **Pros:** Satisfies ticket AC #1 / AC #3 literally.
- **Cons:** Routing/boundary/next-step mutate `state_raw` in place inside `record()`;
  extracting them requires passing mutable state across boundaries or returning patch
  objects, adding indirection and **increasing** total LOC. High regression risk on
  the exact paths the DAG epic just stabilized. Larger diff, more review surface.
- **Complexity:** L.

### Selected Approach

**Approach 1 — Two-module extraction with re-exports.** Auto-selection heuristic:
map complexity (S=2, S=2, L=4); lowest numeric complexity ties between Approach 1 and
Approach 2 at S=2; tiebreak on module reuse count — Approach 1 reuses two established
idioms (pricing re-export + readiness lazy-import), Approach 2 reuses one (re-export
only); Approach 1 wins. Approach 3 (L=4) is ruled out by the constraint that
routing/boundary/next-step are cohesive with the in-place state-mutation transaction —
extracting them adds code rather than removing it and risks the freshly-stabilized DAG
paths.

## High-Level Design

### Architecture Overview

```
record.py  (orchestrator, ≈860 LOC)
  ├── imports & re-exports → metrics.py   (feature-metrics computation, ≈400 LOC)
  ├── imports & re-exports → payload.py   (payload supplement helpers, ≈200 LOC)
  ├── imports & re-exports → pricing.py   (cost computation — ORC-71, unchanged)
  └── still owns: routing, boundary detection, state mutation, next-step, CLI main
readiness.py
  └── lazy-imports REPEAT_PREDICATES from record.py  (UNCHANGED)
```

Dependency direction is strictly one-way: `record → {metrics, payload, pricing}`. No
new module imports `record`, so no new cycle is introduced (UC-E1).

### Key Abstractions

No new abstractions. The re-export idiom is the only pattern introduced, and it
already exists for pricing.py — moved names remain importable from
`orchestrator_next.record` because Python binds the re-imported object to the same
identity the production path uses.

## Low-Level Design

### Components

**`orchestrator_next/metrics.py`** (new) — feature-metrics computation. Moves:
`compute_task_counts`, `compute_retries`, `compute_resolution`, `run_git_churn`,
`extract_review_scores`, `wall_clock_minutes`, `_phase_review_verdict`,
`_resolve_workflow_artifact_path`, `_resolve_feature_metrics_tasks_path`,
`_resolve_feature_metrics`, plus the `_PHASE_REVIEW_VERDICTS` constant. These are pure
computation: each takes a state/path and returns a dict; none mutate `record()`'s
locals. `_resolve_feature_metrics` is export-only (never called inside `record()` —
verified: its sole self-reference is an error string at record.py:861).

**`orchestrator_next/payload.py`** (new) — payload supplement/coerce surface. Moves:
`_coerce_payload_outputs`, `_artifact_basenames_from_outputs`,
`_supplement_legacy_outputs`, `_supplement_learn_result`,
`_supplement_backlog_tickets_synced`, `_merge_evidence_block`, and the
`_OUTPUTS_ALLOW_EMPTY_LIST` constant. `StepContract` is referenced only as a
type annotation (PEP 563 string annotation under `from __future__ import
annotations`) — payload.py guards it under `TYPE_CHECKING` so no runtime import is
added.

**`orchestrator_next/record.py`** (modified) — drops the moved bodies, adds two
module-level re-export blocks. Crucially, `_check_declared_outputs` (stays in
record.py, line ≈959) consumes `_OUTPUTS_ALLOW_EMPTY_LIST`; since that constant moves
to payload.py, record.py must **import it back for internal use** (not merely
re-export it). The re-export `from orchestrator_next.payload import
_OUTPUTS_ALLOW_EMPTY_LIST` covers both needs — the name is bound in record's namespace
and `_check_declared_outputs` resolves it as before.

### Data Flow

Unchanged. `record()` reads stdin JSON → calls `_coerce_payload_outputs` /
`_supplement_*` (now resolved from payload.py via re-export) → mutates `state_raw` →
writes step_history. The metrics path (`_resolve_feature_metrics` →
`compute_task_counts` etc.) is invoked only by tests and complete-phase step scripts,
which import from `orchestrator_next.record` and continue to resolve the moved names
via re-export.

### State Management

No state schema change. `state_raw` mutation stays entirely inside `record()` in
record.py — this is precisely why routing/boundary/next-step are not extracted.

### Error Handling

Unchanged. Moved functions keep their existing try/except behavior (e.g.
`compute_task_counts` returns null-dict on YAMLError/OSError; `run_git_churn` catches
subprocess failures). No new failure modes; the only new risk is a circular import,
mitigated by the one-way dependency direction (UC-E1).

## Constraints

- Python 3.14 (project runtime). No new third-party dependencies; all moves are pure
  Python using stdlib + existing project imports.
- `from __future__ import annotations` is active in record.py and must be in both new
  modules so `StepContract`/`Path` forward annotations stay string-only.

## Trade-offs

record.py remains a ≈860-LOC import hub rather than a ~400-LOC thin orchestrator. This
is accepted because the residual size is dominated by the cohesive state-mutation
transaction (routing + boundary + next-step + `record()`), which cannot be split
without adding indirection and risking the DAG paths. The win is concrete: two
single-concern modules under 500 LOC each, a clear audit surface for "what can the
record boundary tolerate" (payload.py) and "how are feature metrics computed"
(metrics.py), at the cost of leaving record.py above the literal 500 line target.

## Acceptance Criteria

- AC-1: After the split, `pytest` on the 14 record-touching test files passes with no
  modification to any test file or import path. [traces: UC-1]
  Verify: `pytest orchestrator_next/tests/test_record_*.py orchestrator_next/tests/test_boundary_detection.py orchestrator_next/tests/test_repeat_until.py orchestrator_next/tests/test_resolve_artifact_fallback.py orchestrator_next/tests/test_resolve_tasks_md.py orchestrator_next/tests/test_orc36_path_consolidation.py orchestrator_next/tests/test_endtoend_migrated_workflow.py orchestrator_next/tests/test_t13_compute_resolution_from_step_history.py -q`
- AC-2: `compute_task_counts`, `run_git_churn`, `extract_review_scores`,
  `wall_clock_minutes`, `compute_retries`, `compute_resolution`,
  `_resolve_feature_metrics`, `_resolve_feature_metrics_tasks_path`,
  `_resolve_workflow_artifact_path` are defined in `orchestrator_next/metrics.py`
  (not in record.py). [traces: UC-2]
  Verify: `python -c "import orchestrator_next.metrics as m; assert all(hasattr(m, n) for n in ['compute_task_counts','run_git_churn','extract_review_scores','wall_clock_minutes','_resolve_feature_metrics'])"`
- AC-3: The payload supplement helpers (`_coerce_payload_outputs`,
  `_artifact_basenames_from_outputs`, `_supplement_legacy_outputs`,
  `_supplement_learn_result`, `_supplement_backlog_tickets_synced`,
  `_merge_evidence_block`) and `_OUTPUTS_ALLOW_EMPTY_LIST` are defined in
  `orchestrator_next/payload.py`. [traces: UC-3]
  Verify: `python -c "import orchestrator_next.payload as p; assert hasattr(p,'_supplement_legacy_outputs') and hasattr(p,'_OUTPUTS_ALLOW_EMPTY_LIST')"`
- AC-4: All moved names remain importable from `orchestrator_next.record` (re-export
  preserves existing import paths). [traces: UC-2, UC-E2]
  Verify: `python -c "from orchestrator_next.record import compute_task_counts, run_git_churn, _resolve_feature_metrics, _resolve_feature_metrics_tasks_path, _coerce_payload_outputs, _supplement_legacy_outputs, _OUTPUTS_ALLOW_EMPTY_LIST"`
- AC-5: No new circular import — `orchestrator_next.metrics` and
  `orchestrator_next.payload` import cleanly with `record` not yet imported, and
  `readiness`'s lazy `REPEAT_PREDICATES` import path is unchanged. [traces: UC-E1, UC-E3]
  Verify: `python -c "import orchestrator_next.metrics; import orchestrator_next.payload; import orchestrator_next.readiness; import orchestrator_next.record"` and `grep -q "from orchestrator_next.record import REPEAT_PREDICATES" orchestrator_next/readiness.py`

## Decisions

- Re-export moved names from record.py (mirror ORC-71 pricing.py at record.py:488) →
  preserves 14 test files' import paths → no flag-day test edits (satisfies AC #5).
- Two separate modules, not one combined helper → each owns a single concern and
  stays under 500 LOC → satisfies AC #2's "own module" intent and AC #1's spirit.
- `_OUTPUTS_ALLOW_EMPTY_LIST` moves to payload.py and record.py re-imports it →
  `_check_declared_outputs` (staying in record.py) still resolves it → one-way
  dependency preserved.
- Leave routing/boundary/next-step in record.py → they mutate `state_raw` in place;
  extraction adds code and risks DAG-stabilized paths → record.py stays ≈860 LOC by
  design (AC #1 literal target consciously not met).
- `StepContract` guarded under `TYPE_CHECKING` in payload.py → it is a string
  annotation only; no runtime import, no new dependency edge.

## Open Questions

- (Resolved) OQ-1 from discovery: use explicit `from orchestrator_next.<mod> import
  ...` re-exports (grep-friendly, matches pricing.py precedent), not `__all__`.
- (Resolved) OQ-2 from discovery: keep `_resolve_feature_metrics_tasks_path` as its
  own exported function — two tests import it; collapsing it would touch test files
  and violate the no-test-change constraint (AC #5).
