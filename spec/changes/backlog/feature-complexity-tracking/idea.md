# Feature Complexity Tracking + Cost-Per-Complexity Reporting (HL-291)

## Idea

Follow-up to HL-290 (cost-report-generator). Every feature has an implicit complexity estimate (XS/S/M/L/XL) but it's not captured as structured data. After 10-20 features we should be able to answer: what does an M-sized feature typically cost? Are we consistently under/over-estimating?

## Scope

1. **Declare complexity in state.yaml** — written at design phase; closed set XS/S/M/L/XL
2. **Architect responsibility** — during `design-and-draft-artifacts`, emit a `complexity:` top-level field in state.yaml. Step contract `outputs:` declares `complexity` so typed-I/O validation picks it up
3. **DuckDB schema addition** — new `features` table with one row per `(repo_root, change_id)` holding complexity + feature-level metadata (cleaner than denormalizing to step_events)
4. **Extend `orchestrator cost`** — new `--by complexity` flag for `--repo` scope. Produces complexity-bucket summary: median/p90 cost per bucket, feature count per bucket
5. **Schema validation** — complexity value must be from closed set {XS, S, M, L, XL}

## Open Questions for Architect
- Column-vs-table: adding `complexity` to every step_events row is denormalized. New `features` table is cleaner.
- Backfill: existing archived features have no complexity captured. Leave as NULL/"unknown" or add manual backfill?
- Estimate accuracy tracking: flag features labeled S that cost like an L.

## Acceptance Criteria
- `orchestrator cost --repo . --by complexity` produces complexity-bucket cost summary
- `design-and-draft-artifacts` step contract declares `complexity` in outputs
- Invalid complexity values rejected at upsert time with clear error
- New `features` table in DuckDB with at least: change_id, repo_root, complexity, started_at, completed_at

## Dependencies
- HL-287, HL-290 — landed
- HL-295 per-step tools (can run independently, but DuckDB changes should coordinate)

## Priority
- User value: 7/10
- Strategic fit: 8/10
- Size: S-M (4-5 tasks)
- Linear: HL-291
