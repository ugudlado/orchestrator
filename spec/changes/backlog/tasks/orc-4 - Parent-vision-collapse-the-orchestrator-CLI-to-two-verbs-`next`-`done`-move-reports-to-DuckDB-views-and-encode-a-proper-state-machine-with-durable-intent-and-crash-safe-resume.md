---
id: ORC-4
title: >-
  Parent vision: collapse the orchestrator CLI to two verbs (`next` / `done`),
  move reports to DuckDB views, and encode a proper state machine with durable
  intent and crash-safe resume
status: To Do
assignee: []
created_date: '2026-05-03 10:55'
updated_date: '2026-05-03 11:00'
labels:
  - slug-workflow-engine-as-state-machine
  - feature
  - score-8.5
  - recurrence-1
dependencies: []
references:
  - >-
    Exploration session 2026-04-20: walked from "how are metrics calculated" →
    "can DuckDB be the single source" → "what about durability on crash." Full
    transcript is the discovery seed; archive at
    `spec/changes/<phase-slug>/discovery.md` per phase.
  - 'Related completed features: `single-source-metrics-via-step-events` (Apr 19'
  - write path)
  - '`sub-agent-token-ingest` (Apr 20'
  - usage capture).
  - >-
    Related pending: `generate-plan-yaml-at-init` (orthogonal — concerns
    dispatch context
  - not metrics)
  - >-
    `metrics-regression-detection` (downstream — becomes trivial once phase 3
    ships).
priority: medium
ordinal: 3000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
<!-- migrated from spec/changes/backlog.md slug: workflow-engine-as-state-machine -->

**Original score:** 8.5 | **Recurrence:** 1

## Idea

The orchestrator currently exposes 7+ CLI subcommands (`next`, `record`, `metrics`, `cost`, `ingest-driver`, `ingest-subagents`, `doctor`). Reporting logic is split across `metrics_report.py`, `cost_report.py`, and `ingest-feature-metrics.py`. Pricing lives in `config/pricing.yaml`. The read path projects through ~300 lines of Python before consumers see canonical shapes. Between `next` returning an action and `record` writing the outcome, intent is not durable — a crash loses usage capture and leaves state.yaml in `in_progress` forever.

Restructure around two invariants:

1. **DuckDB is the single source of truth.** Pricing, step facts, phase facts, feature facts, driver facts — all live in DuckDB. Reports are SQL views. `config/pricing.yaml` disappears; `metrics_report.py` and most of `cost_report.py` disappear; `ingest-*` commands disappear (absorbed into `done`).

2. **The workflow is a durable state machine.** `next` writes a pending `step_events` row before returning — intent is durable the moment it's declared. `next` is idempotent under crash: called twice with an in-flight step, it returns the same step with `is_resume: true`. `done` handles three payload kinds — `completed` (normal), `recovered` (salvage from JSONL + git), `abandoned` (give up cleanly). Level-aware writing inside `done` detects phase/feature boundaries and writes to the right tables in a single transaction.

Endpoint surface:

```
orchestrator next   <state.yaml>   # decide + stamp in_progress + flag resumes
orchestrator done   <state.yaml>   # dispatches on payload.status; writes all DuckDB levels
orchestrator doctor                # diagnostics + backfill (optional, maintenance-only)
```

All reads are SQL views (`feature_report`, `phase_report`, `agent_report`, `repo_report`) against DuckDB. Humans query the DB directly; workflow scripts query the views.

## Why Now

- The write path was consolidated by the Apr 19 `single-source-metrics-via-step-events` feature; the read path is the remaining asymmetry. This completes that story.
- Durability gap is latent but real — crash mid-step loses tokens and leaves `in_progress` forever. No incident yet, but autopilot runs at scale will hit it.
- Every subsequent metrics feature (regression detection, per-subagent attribution, step-timing telemetry — all in backlog) gets easier once reports are SQL and writes are level-aware. Without this, each new metric requires touching `metrics_report.py`, pricing YAML parsing, and a CLI flag.
- `config/pricing.yaml` is on the critical path of every `record` call; moving it to DuckDB removes a YAML read from the hot path.

## Scope (phased — each phase ships independently)

Each phase below will get its own backlog entry + `/specify` run when the prior phase is retro'd. Context from the prior phase's retro feeds the next phase's discovery.

1. **`pricing-table-in-duckdb`** — seed a `pricing` table via `ensure_schema`; `record` looks up pricing from DuckDB instead of YAML; `config/pricing.yaml` retired. Migration-managed (Option A). Small: ~200 lines net churn.

2. **`durable-intent-and-resume`** — `next` writes pending `step_events` row + state.yaml `in_progress` entry before returning; `next` detects in-flight state on re-entry and returns same step with `is_resume: true` flag. No salvage path yet — just durable intent and idempotent dispatch. Medium: touches `next.py`, `upsert.py`, dispatch contracts.

3. **`report-views-retire-cli`** — create `feature_report`, `phase_report`, `agent_report`, `repo_report` as DuckDB views; rewrite `compute-swe-metrics.sh` to query the view directly; retire `orchestrator metrics` and `orchestrator cost` CLI. Medium: ~500 lines deleted, ~150 added as view migrations.

4. **`done-verb-level-aware-writes`** — ✅ shipped 2026-04-25 (archive: `spec/changes/archive/2026-04-25-done-verb-level-aware-writes/`). Rename `record` → `done` with `payload.status` dispatch (`completed`/`recovered`/`abandoned`); level-aware writes to `phase_events` + `driver_sessions` via migration 0003 inside atomic DuckDB transactions; absorbed `_ingest_driver_main` + `_ingest_subagents_main` (213 lines deleted). 28 commits, 54 new tests, scores: specify 9/9 (round 2), implement 9/9 (round 1). Phase 5 (`cleanup-and-delete`) remains — absorb `ingest-feature-metrics` and final cleanup.

5. **`cleanup-and-delete`** — ✅ shipped 2026-04-25 (archive: `spec/changes/archive/2026-04-25-cleanup-and-delete/`). Absorbed `ingest-feature-metrics.py` (440 lines) into `record.py`'s `mark-change-completed` write path via `_resolve_feature_metrics` + `_write_feature_metrics` helpers (mirrors Phase 4 pattern). Deleted the inline script + step contract + test + `_complete-phase.yaml` entry. Round 1 implement review caught a critical dispatcher bug (FT-20: missing try/except `FileNotFoundError` on the `run_step` branch when `workflow_plan` references a deleted contract); fix landed as 5-line patch mirroring the existing `resume_step` fallback. 15 commits, 54 new tests, scores: specify 9/9 (round 1), implement 9/9 (round 2). Earlier Phase 5 backlog scope (delete `ingest-driver`/`ingest-subagents` CLIs, `metrics_report.py`, `cost_report.py` projections) was already shipped in Phases 3 and 4. **Parent refactor `workflow-engine-as-state-machine` is complete (Phases 1–5 all shipped).**

## Out of scope

- Changing dispatch semantics (`next` still returns the same action shapes — `run_step`, `run_inline`, `verify_phase`, `retry_step`, `blocked`, `complete_workflow`).
- Changing the agent contract or spawn protocol.
- Rewriting existing `step_events` rows from archived features (pricing-in-DuckDB is forward-only; historical rows keep their frozen `cost_usd`).
- Introducing OpenTelemetry / external tracing (the state-machine spans are the same shape, but this is an internal change).
- Any non-workflow system (Linear integration, UX reporting, etc.).

## Dependencies

Phases ship in order (1 → 2 → 3 → 4 → 5). None can parallelize:
- Phase 2 needs phase 1 (pending rows include `cost_usd`, requires pricing table).
- Phase 3 needs phase 1 (views depend on pricing join being a pure SQL join, not a YAML-aware Python projection).
- Phase 4 needs phase 2 (salvage path writes completions against pending rows) and phase 3 (level-aware writes depend on views existing so readers don't break during the cutover).
- Phase 5 is cleanup after phase 4; deleting `ingest-*` commands requires their logic to live inside `done`.

## Priority

- User value: 8/10 (simpler CLI surface, durable against crashes, reports queryable from any client)
- Strategic fit: 10/10 (every pending metrics feature becomes easier)
- Technical leverage: 9/10 (~700 lines deleted, closes write/read asymmetry)
- Effort: large (5 phases staggered — roughly 2–4h per phase except phase 4 which is ~1d)
- **Score: 8.5**

## Source

- Exploration session 2026-04-20: walked from "how are metrics calculated" → "can DuckDB be the single source" → "what about durability on crash." Full transcript is the discovery seed; archive at `spec/changes/<phase-slug>/discovery.md` per phase.
- Related completed features: `single-source-metrics-via-step-events` (Apr 19, write path), `sub-agent-token-ingest` (Apr 20, usage capture).
- Related pending: `generate-plan-yaml-at-init` (orthogonal — concerns dispatch context, not metrics), `metrics-regression-detection` (downstream — becomes trivial once phase 3 ships).

---
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 **`pricing-table-in-duckdb`** — seed a `pricing` table via `ensure_schema`; `record` looks up pricing from DuckDB instead of YAML; `config/pricing.yaml` retired. Migration-managed (Option A). Small: ~200 lines net churn.
- [ ] #2 **`durable-intent-and-resume`** — `next` writes pending `step_events` row + state.yaml `in_progress` entry before returning; `next` detects in-flight state on re-entry and returns same step with `is_resume: true` flag. No salvage path yet — just durable intent and idempotent dispatch. Medium: touches `next.py`, `upsert.py`, dispatch contracts.
- [ ] #3 **`report-views-retire-cli`** — create `feature_report`, `phase_report`, `agent_report`, `repo_report` as DuckDB views; rewrite `compute-swe-metrics.sh` to query the view directly; retire `orchestrator metrics` and `orchestrator cost` CLI. Medium: ~500 lines deleted, ~150 added as view migrations.
- [ ] #4 **`done-verb-level-aware-writes`** — ✅ shipped 2026-04-25 (archive: `spec/changes/archive/2026-04-25-done-verb-level-aware-writes/`). Rename `record` → `done` with `payload.status` dispatch (`completed`/`recovered`/`abandoned`); level-aware writes to `phase_events` + `driver_sessions` via migration 0003 inside atomic DuckDB transactions; absorbed `_ingest_driver_main` + `_ingest_subagents_main` (213 lines deleted). 28 commits, 54 new tests, scores: specify 9/9 (round 2), implement 9/9 (round 1). Phase 5 (`cleanup-and-delete`) remains — absorb `ingest-feature-metrics` and final cleanup.
- [ ] #5 **`cleanup-and-delete`** — ✅ shipped 2026-04-25 (archive: `spec/changes/archive/2026-04-25-cleanup-and-delete/`). Absorbed `ingest-feature-metrics.py` (440 lines) into `record.py`'s `mark-change-completed` write path via `_resolve_feature_metrics` + `_write_feature_metrics` helpers (mirrors Phase 4 pattern). Deleted the inline script + step contract + test + `_complete-phase.yaml` entry. Round 1 implement review caught a critical dispatcher bug (FT-20: missing try/except `FileNotFoundError` on the `run_step` branch when `workflow_plan` references a deleted contract); fix landed as 5-line patch mirroring the existing `resume_step` fallback. 15 commits, 54 new tests, scores: specify 9/9 (round 1), implement 9/9 (round 2). Earlier Phase 5 backlog scope (delete `ingest-driver`/`ingest-subagents` CLIs, `metrics_report.py`, `cost_report.py` projections) was already shipped in Phases 3 and 4. **Parent refactor `workflow-engine-as-state-machine` is complete (Phases 1–5 all shipped).**
<!-- AC:END -->
