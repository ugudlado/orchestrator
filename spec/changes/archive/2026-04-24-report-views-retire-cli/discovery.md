---
feature-id: report-views-retire-cli
linear-ticket: null
---

# Discovery Brief: Report views retire CLI — Phase 3 of workflow-engine-as-state-machine

## Feature Summary

Phase 3 replaces the ~300-line Python projection layer (`metrics_report.py` + the projection sections of `cost_report.py`) with four DuckDB SQL views (`feature_report`, `phase_report`, `agent_report`, `repo_report`), delivered as migration `0002_report_views.sql` following the pattern established in Phase 1. The `orchestrator metrics` and `orchestrator cost` CLI subcommands are retired; `compute-swe-metrics.sh` is rewritten to query the `feature_report` view directly via `duckdb -json` instead of shelling out to `orchestrator metrics`. The net result is ~300 lines of deletion, a single source of truth for metric aggregations in SQL, and developer-accessible ad-hoc queries via any DuckDB client — no Python intermediary needed. This builds directly on Phase 1 (pricing table + migration runner) and Phase 2 (durable intent / `orchestrator next` state machine).

## Personas & Actors

- **Orchestrate skill driver** — invokes `orchestrator cost --change-id <cid>` at workflow-complete (SKILL.md line 122); must be updated to query `feature_report` view or a thin replacement script.
- **Autopilot driver** — calls `orchestrator metrics --format json` via `read-sub-state-metrics.sh` at step D.5 to sample in-progress metrics; must be retargeted.
- **Inline compute-swe-metrics step** — shells out to `orchestrator metrics --format json` inside `compute-swe-metrics.sh`; rewritten to use `duckdb -json` directly.
- **Developer / operator** — queries metric views ad-hoc using `duckdb $METRICS_DB` or any DuckDB client without needing Python installed.
- **CI pipeline** — runs `pytest` test suite; affected test files must be retargeted to SQL assertions.

## Use Cases

### Happy Path

UC-1: Workflow-complete cost summary — the orchestrate skill driver wants to render a per-feature cost summary at the end of a workflow so that the operator sees token spend without running a separate command. The driver queries `feature_report` view (or calls a thin wrapper) instead of `orchestrator cost`.

UC-2: Compute SWE metrics — the `compute-swe-metrics` inline step wants to emit a `metrics:` YAML block into state.yaml so that the complete phase can calculate prediction accuracy. `compute-swe-metrics.sh` queries `feature_report` via `duckdb -json` and converts via `python3 -c` (bash-3.2-compatible), producing byte-identical output shape to today's version.

UC-3: Autopilot mid-run sampling — the autopilot driver wants to read in-progress token and duration metrics at step D.5 so that sub-state decisions can be made. `read-sub-state-metrics.sh` is retargeted to query the view directly and project to the narrow shape (`metrics.tokens.total`, `metrics.duration_ms`, `metrics.churn.files_changed`).

UC-4: Developer ad-hoc query — a developer wants to inspect per-agent token spend for a completed feature so that they can diagnose cost anomalies. They run `duckdb $METRICS_DB "SELECT * FROM agent_report WHERE change_id='X'"` without any Python layer.

### Error & Edge Cases

UC-E1: NULL cost_usd from in-progress rows — when a feature has step_events rows with NULL cost_usd (Phase 2 pending rows), the views must aggregate correctly. `COALESCE(SUM(cost_usd), 0)` must be applied so that partial-run cost reads return 0 rather than NULL.

UC-E2: Missing feature_metrics row — when `feature_metrics` has no row for a given `change_id` (feature started before Phase 3 migration), the `feature_report` view must return NULLs for complexity columns rather than dropping the row; a LEFT JOIN is required.

UC-E3: Repeated run byte-equivalence — running `compute-swe-metrics.sh` twice on the same frozen database must produce identical bytes. The view's sort order must be deterministic (explicit ORDER BY).

UC-E4: Anomaly detection missing from views — `_anomalies()` and `_step_allowlist_anomalies()` in `cost_report.py` read on-disk agent `.md` frontmatter and step-contract YAMLs; they cannot be expressed as pure SQL. If the CLI is retired, these checks disappear unless a thin Python wrapper or separate script is preserved.

## Scope

### In Scope

- `0002_report_views.sql`: four views — `feature_report`, `phase_report`, `agent_report`, `repo_report`
- Retire `_metrics_main()` in `bin/orchestrator` (the `orchestrator metrics` subcommand)
- Retire `_cost_main()` in `bin/orchestrator` (the `orchestrator cost` subcommand)
- Retire `aggregate_metrics()` in `metrics_report.py` (and the full module if no other callers remain)
- Retire the Python projection helpers in `cost_report.py` that are replaced by views (`_totals()`, `_per_step_rollup()`, `_build_per_agent_tokens_str()`, etc.) — but NOT `_anomalies()` / `_step_allowlist_anomalies()` until architect decides
- Rewrite `scripts/inline/compute-swe-metrics.sh` to query `feature_report` via `duckdb -json`
- Update `skills/orchestrate/SKILL.md` to remove `orchestrator cost` invocation at workflow-complete
- Update `config/scripts/read-sub-state-metrics.sh` to remove `orchestrator metrics` invocation
- Byte-equivalence test for `compute-swe-metrics.sh` output (D-4)
- Retarget `config/scripts/tests/test_cost_cli.py` (16+ subprocess tests) to direct SQL assertions
- Retarget shell tests: `config/scripts/__tests__/compute-swe-metrics-projection.test.sh`, `config/scripts/__tests__/read-sub-state-metrics.test.sh`

### Out of Scope

- `orchestrator done` rename — Phase 4
- Salvage / rescue path for interrupted workflows — Phase 4
- `ingest-driver` and `ingest-subagents` subcommand retirement — Phase 5
- `scripts/inline/ingest-feature-metrics.py` — Phase 5
- `aggregate_repo()` variants (`--since`, `--by complexity`) — architect to decide whether `repo_report` view covers these or they are dropped; no non-CLI callers identified
- Markdown renderers (`render_metrics_md()`, `render_markdown_feature()`) — architect to decide fate; no non-CLI callers identified
- Anomaly detection logic (`_anomalies()`, `_step_allowlist_anomalies()`) — architect to decide: omit, wrap in thin Python, or move to separate script
- Changes to `upsert.py`'s table DDL or existing migration `0001_seed_pricing.sql`
- Any change to `feature_metrics`, `feature_complexity`, `step_events`, `tool_calls` table schemas

## UI Direction

N/A — no UI components.

## Key Decisions

Driver-locked resolutions for the 7 open questions below, plus new design-time decisions (DV-1..DV-9) recorded during spec/design drafting:

- **OQ-1 (anomaly detection fate)** → **Approach C: defer.** `_anomalies()` and `_step_allowlist_anomalies()` remain in a trimmed `cost_report.py` as standalone callable Python functions with no CLI entry point. Phase 5 decides whether to expose them, move them, or retire them. Scope of this phase is not expanded to design a replacement.
- **OQ-2 (markdown renderer fate)** → **Prefer inline formatter, with gate.** `scripts/cost-report.sh` emits the 8-section markdown report via an inline `python3 -c` formatter that reads `duckdb -json` output. If the formatter's output is byte-equivalent to `render_markdown_feature` against the T-9 baseline, `render_markdown_feature` is deleted; otherwise it is retained and imported by the shell script. `render_metrics_md` is deleted unconditionally (no caller).
- **OQ-3 (`aggregate_repo` variants)** → **Drop `--since` and `--by complexity`.** No non-CLI callers identified in discovery. `repo_report` view exposes `first_seen` so any future downstream filter can apply directly. No per-complexity view is provided.
- **OQ-4 (`read-sub-state-metrics.sh` unlisted caller)** → **In scope.** Rewritten alongside `compute-swe-metrics.sh` with its own byte-equivalence fixture (`baseline_read_sub_state_metrics.yaml`).
- **OQ-5 (byte-equivalence baseline)** → **Replay `2026-04-21-durable-intent-and-resume`.** No `.duckdb` snapshot exists in that archive; T-3 reconstructs the DB by replaying that state's `step_history` through `upsert_step_event`, checks in a deterministic `baseline.duckdb.sql` dump, and captures stdout from the pre-phase shells as frozen YAML fixtures.
- **OQ-6 (`per_agent_*` / `per_tool_uses` / `per_step` string encoding)** → **Stringified JSON via `json_group_object(...)::VARCHAR`.** Shell consumers re-parse with `json.loads` and re-dump with `json.dumps(sort_keys=True)` before emitting YAML, guaranteeing deterministic key order across runs regardless of DuckDB's intra-aggregate ordering.
- **OQ-7 (new `orchestrator_next.report` helper module)** → **Do NOT create.** End state is shell + SQL, no new Python module. If a future caller needs a reusable helper, inline via `python3 -c` in the consuming shell script (same pattern as `scripts/estimate-cost.sh` from Phase 1).

### New design-time decisions

- **DV-8 (`per_step` location)** → Emit as a stringified JSON column inside `feature_report` (`json_group_object(step_id, json_object(...))::VARCHAR`). Not a separate view. Matches the existing `per_agent_tokens` pattern (D-8) and keeps the view count at four per backlog scope.
- **DV-9 (`per_model` location)** → Not exposed as a view. `scripts/cost-report.sh` runs a direct `SELECT model, SUM(cost_usd), ... FROM step_events GROUP BY model` when producing the Per-Model markdown section. Reason: the per-model projection is consumed in exactly one place (this markdown section) and the driver's D-1 locked the view surface at four.
- **Selected design approach** → **Approach A** from `design.md § Approaches Considered`: four views in `0002_report_views.sql` + direct shell consumption, no Python wrapper. Auto-selection heuristic: among the three approaches (A=M, B=M, C=S-but-wrong), Approach C is eliminated on correctness grounds (violates D-1 retirement intent); among the remaining M-complexity tie, Approach A has higher module reuse (0 new Python modules vs. B's 1 new module, and reuses the Phase 1 `estimate-cost.sh` duckdb-json+python3 shell idiom verbatim). Selected.

## Open Questions

- OQ-1: Anomaly detection fate — `_anomalies()` and `_step_allowlist_anomalies()` read on-disk files and cannot be SQL views. Should they be (a) silently dropped, (b) moved to a standalone Python script, or (c) wrapped in a thin `orchestrator_next.report` Python module that the views layer calls? This is an architect decision.

- OQ-2: Markdown renderer fate — `render_markdown_feature()` (175 lines, 8-section markdown report) and `render_metrics_md()` are currently only called by the CLI subcommands being retired. If the subcommands are deleted, these renderers disappear. Is any caller expected to need markdown output post-Phase-3 (e.g., a new `orchestrator report` command in Phase 4/5)?

- OQ-3: `aggregate_repo()` variants — `_cost_main()` supports `--repo --by feature|agent|tool|complexity --since <ISO>`. No non-CLI caller found. Should `repo_report` view cover these filter variants, or are the `--repo` flags dropped entirely?

- OQ-4: `read-sub-state-metrics.sh` unlisted caller — this script calls `orchestrator metrics --format json` and was NOT listed in the backlog scope. It must be retargeted as part of this phase (same change surface as `compute-swe-metrics.sh`). Confirming architect is aware this adds one more shell script to the changeset.

- OQ-5: Byte-equivalence baseline — D-4 requires a byte-equivalence test comparing new `compute-swe-metrics.sh` output against a frozen reference. Which archived feature's DB file should serve as the baseline fixture? (Candidate: `2026-04-20-pricing-table-in-duckdb` or `2026-04-21-durable-intent-and-resume`.)

- OQ-6: `per_agent_tokens` / `per_agent_tools` / `per_tool_uses` string encoding — these are currently emitted as JSON strings (not objects) in `metrics_report.py` for `yq` compatibility in `register-repo.sh`. If `feature_report` view returns them as native DuckDB `STRUCT` or `MAP` types, will `compute-swe-metrics.sh`'s `python3 -c` conversion produce the same string encoding that downstream consumers expect?

- OQ-7: New Python module wrapper — the backlog mentions `orchestrator_next.report` as a possible thin wrapper. Is this mandatory for Phase 3, or is direct `duckdb -json` + shell conversion sufficient for all callers?
