# Spec — cost-report-generator (HL-290)

## Motivation

Today the only post-run cost signal is `metrics.cost.net_usd` baked into
state.yaml by `compute-swe-metrics`. It's coarse, locked into an archived
YAML blob, and covers only the workflow-run level. The richer source of
truth — `step_events` in DuckDB — already exists but nothing surfaces it
in a human- or machine-readable form, and nothing captures the
per-tool-call grain needed to answer "which agent is burning cost on
which tool?".

We need a first-class **cost report** that:

- is CLI-discoverable for any change (in-flight or archived) via
  `orchestrator cost --change-id <cid>`, with a rich default report,
- supports scoped drill-downs (`--by step|agent|tool`) and cross-change
  roll-ups at the repo level (`orchestrator cost --repo`),
- breaks out MCP tools vs native tools, shows per-agent tool usage, and
  flags anomalies (agent used a tool not in its declared frontmatter
  `tools:` list),
- reuses DuckDB aggregations (no duplicate parsers) with one additive
  schema change: a new `tool_calls` grain-table alongside `step_events`.

The orchestrate skill prints the report at end-of-workflow as a single
addendum; there is **no** archive integration, **no** new inline step,
**no** committed `cost-report.md`.

This is also a **dogfood feature** for the subprocess-per-step telemetry
from HL-287 — we expect real rows in `tool_calls` within one run.

## What Changes

1. New DuckDB table `tool_calls` (additive; `step_events` untouched).
   `ensure_schema()` in `config/scripts/orchestrator_next/upsert.py`
   creates it; `upsert_step_event()` fans a step's `usage.tools` counts
   out into per-call rows.
2. New Python module `config/scripts/orchestrator_next/cost_report.py`
   holds every aggregation query, markdown/JSON renderers, and anomaly
   detection. Pure functions over a DuckDB connection.
3. New CLI subcommand `orchestrator cost ...` wired into
   `bin/orchestrator` with the full flag surface below.
4. One-paragraph addendum in the orchestrate skill's SKILL.md
   instructing the skill to invoke `orchestrator cost --change-id <cid>`
   at `complete_workflow` action time and include stdout in its final
   user-facing message.
5. Tests in `config/scripts/tests/test_cost_report.py` (subprocess +
   fixture-seeded integration).

**No changes to**: `dispatch.py`, `config/workflows/_complete-phase.yaml`,
existing inline steps, `archive-completed-change.yaml`, or the
`step_events` schema.

## CLI Surface

```
orchestrator cost --change-id <cid>                          # default: feature-level markdown
orchestrator cost --change-id <cid> --by step|agent|tool     # scoped breakdown
orchestrator cost --repo                                     # all changes in current repo (basename match)
orchestrator cost --repo --by feature|agent|tool             # repo roll-ups
orchestrator cost --format md|json                           # default md
orchestrator cost --since <ISO date>                         # only meaningful with --repo
```

`--change-id` and `--repo` are mutually exclusive; exactly one is required.

## Requirements

### Functional

- **FR-1**: `tool_calls` table exists with the schema in design.md;
  `ensure_schema()` creates it idempotently on first call.
- **FR-2**: `upsert_step_event()` writes one row per tool invocation
  derived from `usage.tools`. If `usage.tools == {"Bash": 5, "Read": 12}`
  it writes 17 rows with `call_seq` 1..5 and 1..12 respectively. Rows
  denormalise `agent_name` from the same event. `is_mcp` is derived
  from `tool_name.startswith("mcp__")`. Per-call token/cost/duration
  fields stay NULL (Claude Code does not expose per-call telemetry).
  Empty or missing `usage.tools` produces zero rows.
- **FR-3**: `cost_report.py` exposes aggregation entry points plus
  markdown + JSON renderers. All aggregation is `GROUP BY` over
  `step_events` and `tool_calls`; no pre-computed tables.
- **FR-4**: `orchestrator cost --change-id X` default report contains
  eight sections: Executive Summary, Per-Phase, Per-Agent, Per-Model,
  Native Tools, MCP Calls, Per-Agent Tool Use, Anomalies.
- **FR-5**: `orchestrator cost --change-id X --by step|agent|tool`
  returns a single focused table for that scope (no other sections).
- **FR-6**: `orchestrator cost --repo` aggregates across all `change_id`
  values whose `repo_root` has the same basename as `$PWD`
  (`basename(repo_root) == basename($PWD)`). Default view lists each
  feature with totals; `--by feature|agent|tool` switches the grouping.
- **FR-7**: `--format json` renders the same data as a parseable JSON
  document with keys that mirror the markdown sections.
- **FR-8**: `--since <ISO>` filters rows by `step_events.started_at >=
  <ISO>`. Ignored with a stderr warning when combined with `--change-id`.
- **FR-9**: Anomaly section: for each `(agent_name, tool_name)` pair in
  the change's `tool_calls`, read the agent file from
  `$ORCHESTRATOR_HOME/agents/<agent_name>.md` (fallback
  `~/.claude/agents/<agent_name>.md`). Parse YAML frontmatter; if the
  `tools:` key exists and the tool isn't listed, emit
  `⚠️ <agent_name> used <tool_name> (N calls) — not in declared tools list`.
  If the agent file is missing or lacks a `tools:` key, skip silently.
- **FR-10**: DuckDB path resolution mirrors `bin/orchestrator next`:
  `METRICS_DB` env wins, else `$ORCHESTRATOR_HOME/metrics.duckdb`.
  Missing DB or zero matching rows → exit 3 with stderr message.
- **FR-11**: Orchestrate skill addendum — at `complete_workflow` action,
  the skill runs `orchestrator cost --change-id <cid>` and includes the
  stdout in its final user-facing message. One paragraph in SKILL.md.
  No archive side-effect; no file is committed.

### Non-Functional

- **NFR-1**: Additive schema only. `step_events` DDL is unchanged.
- **NFR-2**: Slug guard: `change_id` input validated with
  `^[a-z0-9][a-z0-9-]*$` before any DB query. Invalid → exit 3.
- **NFR-3**: All SQL uses parameterised `duckdb.execute(sql, params)`.
- **NFR-4**: Deterministic ordering (phases by first-seen via
  `MIN(started_at)`; agents/models alphabetical; tools by call count
  desc then name asc) so repeat runs produce byte-identical output.
- **NFR-5**: `dispatch.py` is not touched.

## Acceptance Criteria

- **AC-1** [traces: FR-1, FR-2]: After `upsert_step_event()` ingests an
  event whose `usage.tools = {"Bash": 2, "mcp__pal__thinkdeep": 1}`,
  `SELECT COUNT(*) FROM tool_calls` returns 3; two rows have
  `is_mcp=false`, one has `is_mcp=true`; `call_seq` is 1,2 for Bash and
  1 for the MCP tool; `agent_name` matches the step's agent.
- **AC-2** [traces: FR-4]: `orchestrator cost --change-id X` prints
  markdown containing headings for all eight sections in the documented
  order: Executive Summary, Per-Phase, Per-Agent, Per-Model,
  Native Tools, MCP Calls, Per-Agent Tool Use, Anomalies.
- **AC-3** [traces: FR-5]: `orchestrator cost --change-id X --by step`,
  `--by agent`, `--by tool` each produce a single markdown table with
  the expected columns (see design.md) and no other sections.
- **AC-4** [traces: FR-6]: With two seeded `change_id`s sharing the same
  `basename(repo_root)`, `orchestrator cost --repo` lists both with
  aggregated totals; `--repo --by agent` aggregates agents across both.
- **AC-5** [traces: FR-7]: `orchestrator cost --change-id X --format
  json` emits parseable JSON whose top-level object has keys `totals`,
  `per_phase`, `per_agent`, `per_model`, `native_tools`, `mcp_calls`,
  `per_agent_tools`, `anomalies`.
- **AC-6** [traces: FR-2, FR-4]: Given a fixture with native (`Bash`,
  `Read`) and MCP (`mcp__pal__thinkdeep`) rows, the default report
  shows them in separate `Native Tools` and `MCP Calls` tables split by
  `is_mcp`.
- **AC-7** [traces: FR-9]: Fixture where agent `developer`'s frontmatter
  `tools: ["Read", "Edit"]` but `tool_calls` contains a `Bash` row —
  anomalies section contains exactly one row matching `⚠️ developer
  used Bash`. If the agent file is missing, no row is emitted.
- **AC-8** [traces: FR-11]: `grep -n 'orchestrator cost' path/to/SKILL.md`
  returns a line inside the `complete_workflow` prose.
- **AC-9** [traces: NFR-4]: Running the default report twice against
  the same DB produces byte-identical stdout.
- **AC-10** [traces: NFR-2]: CLI invoked with `--change-id '../evil'`
  exits 3 with slug-guard message on stderr; no DB query executed.

## Impact

| Area | Change |
|---|---|
| DB schema | Additive: new `tool_calls` table |
| `upsert.py` | `ensure_schema()` + `upsert_step_event()` extended |
| `dispatch.py` | Untouched |
| Existing inline steps | None modified |
| `_complete-phase.yaml` | Untouched |
| `archive-completed-change.yaml` | Untouched |
| `bin/orchestrator` | One new subcommand branch (`cost`) |
| Orchestrate SKILL.md | One-paragraph addendum |
| Tests | New `test_cost_report.py` |

## Phase Gate Notes

- **Discovery**: done (HL-290 + amendment set scope-locks).
- **Specify**: this document (patched for 4 amendments).
- **Design**: see `design.md`.
- **Implement**: 5 tasks in `tasks.md`; TDD not required. Verifies are
  structural post-hoc checks.
- **Complete**: dogfood signal — the skill addendum should print a
  populated report on the very next completed change.

## Alternatives Considered (brief — full in design.md)

1. **Keep cost-report.md as an archived artifact / new inline step**.
   Rejected per amendment #1: adds review churn for every archived
   change; a skill-side print is cheaper and equally discoverable.
2. **Pre-aggregate a `tool_calls_rollup` table on upsert**. Rejected:
   `GROUP BY` over `tool_calls` is fast at realistic row counts and
   keeps a single source of truth.
3. **Parse `tool_calls_json` in `step_events` on the fly** (no new
   table). Rejected: denormalising into `tool_calls` gives us
   `agent_name`, `is_mcp`, `call_seq` as columns — what every
   aggregation needs and what anomaly detection keys on.
